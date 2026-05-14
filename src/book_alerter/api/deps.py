"""Shared FastAPI dependencies for API routers.

Each router pulls its `Session`, `Config`, `Scheduler`, and config path from
`request.app.state` (populated by `app.lifespan`). The `get_session` dependency
yields a SQLModel `Session` bound to the app's engine and closes it cleanly
after the request.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session

from book_alerter.config import Config
from book_alerter.notifications.base import Notifier
from book_alerter.scheduler import Scheduler


def get_session(request: Request) -> Iterator[Session]:
    """Yield a `Session` bound to `app.state.engine` and close it on exit."""
    engine = request.app.state.engine
    with Session(engine) as session:
        yield session


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_config_path(request: Request) -> Path:
    """Return the on-disk path of the active config YAML.

    Set by `app.lifespan` (and the `api_client` test fixture). Required by
    PATCH handlers that need to persist config changes back to disk.
    """
    return request.app.state.config_path


def get_scheduler(request: Request) -> Scheduler:
    """Return the running `Scheduler` from `app.state`.

    Real app boot always sets this; tests that don't need it can simply avoid
    depending on it.
    """
    return request.app.state.scheduler


def get_notifiers(request: Request) -> dict[str, Notifier]:
    """Return the configured notifiers keyed by `notifier.name`.

    Populated by `app.lifespan` (real app) or the `api_client` test fixture.
    Used by `POST /api/notifications/{channel}/test` to look up a channel by
    name without re-instantiating notifiers per request.
    """
    return request.app.state.notifiers


SessionDep = Annotated[Session, Depends(get_session)]
ConfigDep = Annotated[Config, Depends(get_config)]
ConfigPathDep = Annotated[Path, Depends(get_config_path)]
SchedulerDep = Annotated[Scheduler, Depends(get_scheduler)]
NotifiersDep = Annotated[dict[str, Notifier], Depends(get_notifiers)]
