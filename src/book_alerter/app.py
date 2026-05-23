from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from book_alerter.api import alerts, books, covers, health, products, sources
from book_alerter.api import config as config_routes
from book_alerter.api import metadata as metadata_routes
from book_alerter.api import notifications as notifications_routes
from book_alerter.auth import basic_auth_dep, is_basic_auth_enabled
from book_alerter.config import Config
from book_alerter.db.session import get_engine
from book_alerter.enums import ItemKind
from book_alerter.http_client import build_shared_client
from book_alerter.logging_setup import configure_logging, get_logger
from book_alerter.notifications.base import Notifier
from book_alerter.notifications.dispatcher import (
    BOOK_MODELS,
    PRODUCT_MODELS,
    AlertPipeline,
)
from book_alerter.notifications.inapp import InAppNotifier
from book_alerter.notifications.ntfy import NtfyNotifier
from book_alerter.scheduler import Scheduler
from book_alerter.sources.registry import build_sources

log = get_logger(__name__)


def _build_notifiers(cfg: Config, http: httpx.AsyncClient | None) -> list[Notifier]:
    notifiers: list[Notifier] = [InAppNotifier()]
    if cfg.notifications.channels.ntfy.enabled:
        notifiers.append(NtfyNotifier(cfg.notifications.channels.ntfy, http=http))
    return notifiers


def _build_runtime(
    app: FastAPI,
    cfg: Config,
    engine,
    http: httpx.AsyncClient | None = None,
) -> None:
    """Construct sources, notifiers, pipeline, and scheduler from `cfg` and
    attach them to `app.state`. Used by both the lifespan startup and the
    `rebuild_runtime` hot-reload path.

    `http` is the lifespan-scoped shared client; non-None during normal
    boot, None during `rebuild_runtime` (the existing client on
    `app.state.http` is reused — we don't tear it down on config swaps)."""
    if http is None:
        http = getattr(app.state, "http", None)
    sources = build_sources(cfg, http=http)
    notifiers = _build_notifiers(cfg, http=http)
    book_pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine),
        notifiers=notifiers,
        models=BOOK_MODELS,
    )
    product_pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine),
        notifiers=notifiers,
        models=PRODUCT_MODELS,
    )
    scheduler = Scheduler(
        config=cfg,
        sources=sources,
        session_factory=lambda: Session(engine),
        alert_pipelines={
            ItemKind.BOOK: book_pipeline.run,
            ItemKind.PRODUCT: product_pipeline.run,
        },
        db_path=engine.url.database,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    app.state.engine = engine
    app.state.notifiers = {n.name: n for n in notifiers}


def rebuild_runtime(app: FastAPI) -> None:
    """Tear down the live scheduler + notifier registry and rebuild them
    from `app.state.config`. Called after the config swap on PUT /api/config
    and PATCH /api/sources so config changes take effect without a restart.

    Must be invoked from a coroutine — `AsyncIOScheduler.start()` reads
    `asyncio.get_running_loop()`, which raises in a threadpool worker. The
    callers (`put_config`, `patch_source`) are `async def` for that reason.

    The previous scheduler is shut down with `wait=False`; any in-flight
    source run aborts and the next cron tick picks up the new config.

    No-op in test contexts whose `app.state.scheduler` isn't a real
    `Scheduler` (the api_client fixture attaches a `_StubScheduler`).
    """
    engine = getattr(app.state, "engine", None)
    if engine is None:
        return
    prev = getattr(app.state, "scheduler", None)
    if not isinstance(prev, Scheduler):
        return
    # Build the new runtime BEFORE tearing down the old one. If construction
    # raises (bad cron expr, unknown source name, etc.) the previous scheduler
    # keeps serving — otherwise we'd leave the app scheduler-less and PUT
    # /api/config could brick automation until the next process restart.
    # `_build_runtime` overwrites `app.state.scheduler` on success; the prev
    # ref we captured above is the one we shut down on the happy path.
    _build_runtime(app, app.state.config, engine)
    prev.shutdown()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    cfg_path = Path(os.environ.get("BOOK_ALERTER_CONFIG_PATH", "data/config.yaml"))
    first_boot = not cfg_path.exists()
    cfg = Config.load(cfg_path)
    if first_boot:
        # Persist defaults so the user can discover the schema via
        # data/config.yaml or the Advanced editor rather than an empty file.
        try:
            cfg.save(cfg_path)
            log.info("startup.config.created", config_path=str(cfg_path))
        except OSError as e:
            # Read-only mount or permission error — in-memory defaults are
            # loaded; boot continues without a seed file on disk.
            log.warning(
                "startup.config.create_failed",
                config_path=str(cfg_path),
                error=str(e),
            )
    app.state.config = cfg
    app.state.config_path = cfg_path
    log.info("startup", config_version=cfg.config_version, config_path=str(cfg_path))

    engine = get_engine()
    http = build_shared_client()
    app.state.http = http
    try:
        _build_runtime(app, cfg, engine, http=http)
    except Exception:
        # Boot failure (bad config, source/notifier construction error, etc.).
        # The finally below only runs after yield, so we'd otherwise leak the
        # shared client + engine. Close them here and re-raise so FastAPI
        # surfaces the failure to the operator.
        await http.aclose()
        engine.dispose()
        raise

    try:
        yield
    finally:
        app.state.scheduler.shutdown()
        await http.aclose()
        engine.dispose()
        log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="Book Alerter", version="0.0.1", lifespan=lifespan)
    # Evaluated once at startup; env-var changes don't flip auth without restart.
    auth_deps = [Depends(basic_auth_dep)] if is_basic_auth_enabled() else []
    for router in (
        health.router,
        books.router,
        products.router,
        alerts.router,
        sources.router,
        config_routes.router,
        metadata_routes.router,
        notifications_routes.router,
        covers.router,
    ):
        app.include_router(router, dependencies=auth_deps)

    # Serve the built frontend (Vite SPA) when the dist directory is present.
    # In dev (`uv run uvicorn`) the dist won't exist and this block is skipped —
    # Vite's own dev server serves the SPA on a different port and proxies API
    # calls. In the Docker image the FE build is copied to /app/web/dist by
    # stage 1.
    #
    # Registered LAST so all `/api/*` and other backend routes match first.
    # Two pieces are needed for SPA hosting:
    #   1. A static mount that serves the build's assets (JS/CSS/fonts/...).
    #   2. A catch-all GET that returns `index.html` for any unmatched path —
    #      this is the SPA client-side router (e.g. `/books/123`) fallback,
    #      so a deep-link reload doesn't 404.
    web_dist = Path(os.environ.get("BOOK_ALERTER_WEB_DIST", "web/dist")).resolve()
    if web_dist.is_dir():
        index_html = web_dist / "index.html"
        assets_dir = web_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.api_route(
            "/{full_path:path}",
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        async def _spa_fallback(full_path: str) -> FileResponse:
            # Try a literal file in the dist root first (favicon.svg,
            # icons.svg, robots.txt, ...). Anything else: serve index.html
            # so the React Router can take over.
            #
            # Resolve + containment check: without it, a path like
            # `../../../etc/passwd` joins to `web_dist/../../../etc/passwd`
            # which `Path.resolve()` flattens to `/etc/passwd`. Serving any
            # file outside `web_dist` is a path-traversal vulnerability.
            if full_path:
                candidate = (web_dist / full_path).resolve()
                try:
                    candidate.relative_to(web_dist)
                except ValueError:
                    candidate = None
                if candidate is not None and candidate.is_file():
                    return FileResponse(candidate)
            if index_html.is_file():
                return FileResponse(index_html)
            raise HTTPException(status_code=404)

    return app


app = create_app()
