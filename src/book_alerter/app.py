from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from book_alerter.api import health
from book_alerter.config import Config
from book_alerter.logging_setup import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    cfg_path = Path(os.environ.get("BOOK_ALERTER_CONFIG_PATH", "data/config.yaml"))
    cfg = Config.load(cfg_path)
    app.state.config = cfg
    log.info("startup", config_version=cfg.config_version, config_path=str(cfg_path))
    try:
        yield
    finally:
        log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="Book Alerter", version="0.0.1", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
