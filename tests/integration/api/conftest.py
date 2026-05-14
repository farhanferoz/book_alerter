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

from book_alerter.api import books
from book_alerter.config import Config
from book_alerter.db import models


@pytest.fixture
def api_client(engine_with_view, tmp_path: Path):
    cfg = Config.load(tmp_path / "config.yaml")  # path doesn't exist → defaults
    app = FastAPI()
    app.state.engine = engine_with_view
    app.state.config = cfg
    app.include_router(books.router)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def make_observation():
    """Insert a `PriceObservation` directly via SQLModel session.

    Used by Task 7.2+ tests that need price-history fixtures without going
    through the full source pipeline. `total_minor` defaults to
    `price_minor + (shipping_minor or 0)` to satisfy the existing DB check.
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
        is_duplicate_of: int | None = None,
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
            is_duplicate_of=is_duplicate_of,
        )
        session.add(obs)
        session.commit()
        session.refresh(obs)
        return obs

    return _make
