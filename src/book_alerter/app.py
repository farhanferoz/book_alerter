from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from sqlmodel import Session

from book_alerter.api import alerts, books, health, sources
from book_alerter.api import config as config_routes
from book_alerter.api import metadata as metadata_routes
from book_alerter.api import notifications as notifications_routes
from book_alerter.auth import basic_auth_dep, is_basic_auth_enabled
from book_alerter.config import Config
from book_alerter.db.session import get_engine
from book_alerter.logging_setup import configure_logging, get_logger
from book_alerter.notifications.base import Notifier
from book_alerter.notifications.dispatcher import AlertPipeline
from book_alerter.notifications.inapp import InAppNotifier
from book_alerter.notifications.ntfy import NtfyNotifier
from book_alerter.scheduler import Scheduler
from book_alerter.sources.registry import build_sources

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    cfg_path = Path(os.environ.get("BOOK_ALERTER_CONFIG_PATH", "data/config.yaml"))
    cfg = Config.load(cfg_path)
    app.state.config = cfg
    app.state.config_path = cfg_path
    log.info("startup", config_version=cfg.config_version, config_path=str(cfg_path))

    engine = get_engine()
    sources = build_sources(cfg)

    notifiers: list[Notifier] = [InAppNotifier()]
    if cfg.notifications.channels.ntfy.enabled:
        notifiers.append(NtfyNotifier(cfg.notifications.channels.ntfy))
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine),
        notifiers=notifiers,
    )
    scheduler = Scheduler(
        config=cfg,
        sources=sources,
        session_factory=lambda: Session(engine),
        alert_pipeline=pipeline.run,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    app.state.engine = engine
    app.state.notifiers = {n.name: n for n in notifiers}

    try:
        yield
    finally:
        scheduler.shutdown()
        engine.dispose()
        log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="Book Alerter", version="0.0.1", lifespan=lifespan)
    # Evaluated once at startup; env-var changes don't flip auth without restart.
    auth_deps = [Depends(basic_auth_dep)] if is_basic_auth_enabled() else []
    app.include_router(health.router, dependencies=auth_deps)
    app.include_router(books.router, dependencies=auth_deps)
    app.include_router(alerts.router, dependencies=auth_deps)
    app.include_router(sources.router, dependencies=auth_deps)
    app.include_router(config_routes.router, dependencies=auth_deps)
    app.include_router(metadata_routes.router, dependencies=auth_deps)
    app.include_router(notifications_routes.router, dependencies=auth_deps)
    return app


app = create_app()
