from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlmodel import Session

from book_alerter.api import health
from book_alerter.config import Config
from book_alerter.db.session import get_engine
from book_alerter.logging_setup import configure_logging, get_logger
from book_alerter.notifications.dispatcher import AlertPipeline
from book_alerter.notifications.inapp import InAppNotifier
from book_alerter.scheduler import Scheduler
from book_alerter.sources.registry import build_sources

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    cfg_path = Path(os.environ.get("BOOK_ALERTER_CONFIG_PATH", "data/config.yaml"))
    cfg = Config.load(cfg_path)
    app.state.config = cfg
    log.info("startup", config_version=cfg.config_version, config_path=str(cfg_path))

    engine = get_engine()
    sources = build_sources(cfg)

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine),
        notifiers=[InAppNotifier()],
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

    try:
        yield
    finally:
        scheduler.shutdown()
        engine.dispose()
        log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="Book Alerter", version="0.0.1", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
