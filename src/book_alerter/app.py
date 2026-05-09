from fastapi import FastAPI

from book_alerter.api import health
from book_alerter.logging_setup import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Book Alerter", version="0.0.1")
    app.include_router(health.router)
    return app


app = create_app()
