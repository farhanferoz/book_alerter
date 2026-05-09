from fastapi import FastAPI

from book_alerter.api import health


def create_app() -> FastAPI:
    app = FastAPI(title="Book Alerter", version="0.0.1")
    app.include_router(health.router)
    return app


app = create_app()
