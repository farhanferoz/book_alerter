"""Shared fixtures for API integration tests.

`api_client` builds a minimal FastAPI app wired to a sqlite engine that already
has the `book_stats` view installed (`engine_with_view`). The router(s) under
test are included directly so we don't drag in the full lifespan (scheduler,
sources, notifiers). This is the test-app pattern that Task 7.2+ should reuse:
build a router-only `FastAPI()`, set `app.state.engine` + `app.state.config`,
include the routers you're testing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from book_alerter.api import alerts, books, products, sources
from book_alerter.api import config as config_routes
from book_alerter.api import metadata as metadata_routes
from book_alerter.api import notifications as notifications_routes
from book_alerter.config import Config
from book_alerter.db import models
from book_alerter.notifications.base import NotificationResult
from book_alerter.stats import MediansCache


class _StubScheduler:
    """Minimal `Scheduler.trigger_now` stub for Task 7.4 tests.

    Production code uses a real `book_alerter.scheduler.Scheduler` attached
    during lifespan. This stub exposes only the async surface that
    `POST /api/sources/{name}/run` consumes. Tests can mutate `return_zero_for`
    to simulate the backoff gate, and inspect `calls` to assert dispatch.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.next_run_id: int = 42
        self.return_zero_for: set[str] = set()

    async def trigger_now(self, name: str) -> int:
        self.calls.append(name)
        if name in self.return_zero_for:
            return 0
        return self.next_run_id


class _StubNotifier:
    """Minimal `Notifier` stub for Task 7.7 notification-test endpoint tests.

    Mirrors the `_StubScheduler` pattern: attached unconditionally by the
    `api_client` fixture under name `"stub"` so tests can reach into
    `api_client.app.state.notifiers["stub"]` to inspect `calls` and mutate
    `next_result` to drive the `sent` / `error` branches.
    """

    name = "stub"
    bypasses_quiet_hours = False

    def __init__(self) -> None:
        self.calls: list[tuple[models.Alert, models.Book]] = []
        self.next_result: NotificationResult = {"status": "sent"}

    async def send(self, alert: models.Alert, book: models.Book) -> NotificationResult:
        self.calls.append((alert, book))
        return self.next_result


@pytest.fixture
def api_client(engine_with_view, tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg = Config.load(cfg_path)  # path doesn't exist → defaults
    app = FastAPI()
    app.state.engine = engine_with_view
    app.state.config = cfg
    app.state.config_path = cfg_path
    app.state.scheduler = _StubScheduler()
    app.state.notifiers = {"stub": _StubNotifier()}
    app.state.medians_cache = MediansCache()
    app.include_router(books.router)
    app.include_router(products.router)
    app.include_router(alerts.router)
    app.include_router(sources.router)
    app.include_router(config_routes.router)
    app.include_router(metadata_routes.router)
    app.include_router(notifications_routes.router)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def make_observation():
    """Insert a `PriceObservation` directly via SQLModel session.

    Used by Task 7.2+ tests that need price-history fixtures without going
    through the full source pipeline. `total_minor` defaults to
    `price_minor + (shipping_minor or 0)` to satisfy the existing DB check.
    `last_seen_at` defaults to `observed_at` (a fresh, never-re-confirmed
    sighting); pass it explicitly to model a repeat scrape updating the row
    in place (`scheduler._persist`, migration 0021 T3.2).
    """
    def _make(
        session: Session,
        *,
        book_id: int,
        observed_at: datetime,
        source: str = "wob",
        seller: str | None = None,
        condition: str = "used_g",
        price_minor: int = 500,
        currency: str = "GBP",
        shipping_minor: int | None = 0,
        total_minor: int | None = None,
        url: str = "https://example.com/o",
        last_seen_at: datetime | None = None,
    ) -> models.PriceObservation:
        if total_minor is None:
            total_minor = price_minor + (shipping_minor or 0)
        obs = models.PriceObservation(
            book_id=book_id,
            source=source,
            seller=seller,
            condition=condition,
            price_minor=price_minor,
            currency=currency,
            shipping_minor=shipping_minor,
            total_minor=total_minor,
            url=url,
            observed_at=observed_at,
            last_seen_at=last_seen_at if last_seen_at is not None else observed_at,
        )
        session.add(obs)
        session.commit()
        session.refresh(obs)
        return obs

    return _make


@pytest.fixture
def make_alert():
    """Insert an `Alert` directly via SQLModel session.

    Used by Task 7.3+ tests that need alert-feed fixtures without running the
    full detection/dispatch pipeline. Note: capture `alert.id` immediately
    after `make_alert` returns — the row detaches from its session when the
    `with Session(...)` block exits (same gotcha as `make_observation`).
    """
    def _make(
        session: Session,
        *,
        book_id: int,
        fired_at: datetime,
        kind: str = "target_hit",
        price_minor: int = 500,
        currency: str = "GBP",
        source: str = "wob",
        condition: str = "used_g",
        message: str = "test alert",
        dismissed_at: datetime | None = None,
        delivered_via: list[str] | None = None,
    ) -> models.Alert:
        alert = models.Alert(
            book_id=book_id,
            kind=kind,  # type: ignore[arg-type]
            price_minor=price_minor,
            currency=currency,
            source=source,
            condition=condition,
            message=message,
            fired_at=fired_at,
            dismissed_at=dismissed_at,
            delivered_via=list(delivered_via or []),
        )
        session.add(alert)
        session.commit()
        session.refresh(alert)
        return alert

    return _make


@pytest.fixture
def make_source_run():
    """Insert a `SourceRun` directly via SQLModel session.

    Sibling of `make_observation` / `make_alert` — used by Task 7.4 tests that
    need historical run rows without running the full scheduler. Same detached
    instance gotcha: capture `run.id` before the `with Session(...)` block exits.
    """
    def _make(
        session: Session,
        *,
        source: str,
        started_at: datetime,
        finished_at: datetime | None = None,
        status: str = "success",
        books_attempted: int = 0,
        books_succeeded: int = 0,
        error_message: str | None = None,
        error_traceback: str | None = None,
    ) -> models.SourceRun:
        run = models.SourceRun(
            source=source,
            started_at=started_at,
            finished_at=finished_at,
            status=status,  # type: ignore[arg-type]
            books_attempted=books_attempted,
            books_succeeded=books_succeeded,
            error_message=error_message,
            error_traceback=error_traceback,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run

    return _make
