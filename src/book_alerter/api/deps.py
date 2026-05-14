"""Shared FastAPI dependencies for API routers.

Each router pulls its `Session`, `Config`, and `Scheduler` from `request.app.state`
(populated by `app.lifespan`). The `get_session` dependency yields a SQLModel
`Session` bound to the app's engine and closes it cleanly after the request.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlmodel import Session

from book_alerter.config import Config
from book_alerter.scheduler import Scheduler


def get_session(request: Request) -> Iterator[Session]:
    """Yield a `Session` bound to `app.state.engine` and close it on exit."""
    engine = request.app.state.engine
    with Session(engine) as session:
        yield session


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_scheduler(request: Request) -> Scheduler:
    """Return the running `Scheduler` from `app.state`.

    Real app boot always sets this; tests that don't need it can simply avoid
    depending on it.
    """
    return request.app.state.scheduler
