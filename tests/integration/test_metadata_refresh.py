"""Scheduler tests for T4.1's `metadata_refresh` job.

Covers the three things `Scheduler._refresh_one_product_metadata` /
`_metadata_refresh_tick` are responsible for:

- A successful `fetch_amazon_uk_product_metadata` resolves a PENDING
  product to OK and fills in title/image/brand.
- A failing fetch increments `metadata_attempts`; after
  `_METADATA_REFRESH_MAX_ATTEMPTS` (6) it gives up and marks FAILED.
- `BrowserSessionBusy` (D24: the amazon_uk_product profile held by a
  concurrent run) does NOT count as an attempt.

Plus a pure unit test of `_metadata_refresh_due`'s exponential-backoff gate,
which needs no DB or monkeypatching at all.

Monkeypatches `book_alerter.scheduler.fetch_amazon_uk_product_metadata`
(the name bound in scheduler.py's own namespace) rather than
`book_alerter.metadata.fetch_amazon_uk_product_metadata` — scheduler.py
imports the function by name, so patching the origin module would not
affect the reference scheduler.py already holds.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from book_alerter.config import Config
from book_alerter.db import models
from book_alerter.enums import ItemKind, MetadataStatus
from book_alerter.metadata import ProductMetadata
from book_alerter.scheduler import Scheduler
from book_alerter.sources.browser import BrowserSessionBusy


def _make_scheduler(sqlite_engine) -> Scheduler:
    return Scheduler(
        config=Config(sources={}),
        sources={},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={ItemKind.BOOK: lambda ids: None, ItemKind.PRODUCT: lambda ids: None},
    )


def _make_pending_product(sqlite_engine, make_product, *, attempts: int = 0) -> int:
    with Session(sqlite_engine) as s:
        product = make_product(s, asin="B0PEND0001", title="Amazon product B0PEND0001")
        product.metadata_status = MetadataStatus.PENDING
        product.metadata_attempts = attempts
        s.add(product)
        s.commit()
        s.refresh(product)
        return product.id


@pytest.mark.asyncio
async def test_successful_fetch_resolves_pending_to_ok(
    sqlite_engine, make_product, monkeypatch,
) -> None:
    product_id = _make_pending_product(sqlite_engine, make_product)
    scheduler = _make_scheduler(sqlite_engine)

    async def _fake_fetch(asin: str) -> ProductMetadata:
        return ProductMetadata(
            asin=asin, title="Real Title", image_url="https://x/y.jpg", brand="Acme",
        )

    monkeypatch.setattr("book_alerter.scheduler.fetch_amazon_uk_product_metadata", _fake_fetch)

    await scheduler._refresh_one_product_metadata(product_id)

    with Session(sqlite_engine) as s:
        product = s.get(models.Product, product_id)
        assert product.metadata_status == MetadataStatus.OK
        assert product.title == "Real Title"
        assert product.image_url == "https://x/y.jpg"
        assert product.brand == "Acme"
        # A resolved attempt doesn't need to have incremented the counter --
        # only failures count towards the retry budget.
        assert product.metadata_attempts == 0


@pytest.mark.asyncio
async def test_failed_fetch_increments_attempts_without_resolving(
    sqlite_engine, make_product, monkeypatch,
) -> None:
    product_id = _make_pending_product(sqlite_engine, make_product)
    scheduler = _make_scheduler(sqlite_engine)

    async def _fake_fetch(asin: str) -> None:
        return None

    monkeypatch.setattr("book_alerter.scheduler.fetch_amazon_uk_product_metadata", _fake_fetch)

    await scheduler._refresh_one_product_metadata(product_id)

    with Session(sqlite_engine) as s:
        product = s.get(models.Product, product_id)
        assert product.metadata_status == MetadataStatus.PENDING
        assert product.metadata_attempts == 1
        assert product.metadata_last_attempt_at is not None


@pytest.mark.asyncio
async def test_exhausting_retry_budget_marks_failed(
    sqlite_engine, make_product, monkeypatch,
) -> None:
    """The 6th consecutive failed attempt (not the tick's due-check, which
    this test bypasses by calling _refresh_one_product_metadata directly)
    marks the product FAILED."""
    max_attempts = Scheduler._METADATA_REFRESH_MAX_ATTEMPTS
    product_id = _make_pending_product(sqlite_engine, make_product, attempts=max_attempts - 1)
    scheduler = _make_scheduler(sqlite_engine)

    async def _fake_fetch(asin: str) -> None:
        return None

    monkeypatch.setattr("book_alerter.scheduler.fetch_amazon_uk_product_metadata", _fake_fetch)

    await scheduler._refresh_one_product_metadata(product_id)

    with Session(sqlite_engine) as s:
        product = s.get(models.Product, product_id)
        assert product.metadata_attempts == max_attempts
        assert product.metadata_status == MetadataStatus.FAILED


@pytest.mark.asyncio
async def test_browser_session_busy_does_not_count_as_an_attempt(
    sqlite_engine, make_product, monkeypatch,
) -> None:
    prior_attempts = 2
    product_id = _make_pending_product(
        sqlite_engine, make_product, attempts=prior_attempts,
    )
    scheduler = _make_scheduler(sqlite_engine)

    async def _fake_fetch(asin: str):
        raise BrowserSessionBusy("amazon_uk_product", 10.0)

    monkeypatch.setattr("book_alerter.scheduler.fetch_amazon_uk_product_metadata", _fake_fetch)

    await scheduler._refresh_one_product_metadata(product_id)

    with Session(sqlite_engine) as s:
        product = s.get(models.Product, product_id)
        assert product.metadata_status == MetadataStatus.PENDING
        assert product.metadata_attempts == prior_attempts  # unchanged
        assert product.metadata_last_attempt_at is None  # unchanged


@pytest.mark.asyncio
async def test_tick_skips_products_still_within_their_backoff_window(
    sqlite_engine, make_product, monkeypatch,
) -> None:
    """End-to-end through _metadata_refresh_tick (not the single-product
    helper): a product that failed moments ago must not be retried again
    on this tick."""
    with Session(sqlite_engine) as s:
        product = make_product(s, asin="B0BACKOFF1")
        product.metadata_status = MetadataStatus.PENDING
        product.metadata_attempts = 1
        product.metadata_last_attempt_at = datetime.now(UTC)
        s.add(product)
        s.commit()
        s.refresh(product)
        product_id = product.id

    scheduler = _make_scheduler(sqlite_engine)
    calls: list[str] = []

    async def _fake_fetch(asin: str) -> None:
        calls.append(asin)
        return None

    monkeypatch.setattr("book_alerter.scheduler.fetch_amazon_uk_product_metadata", _fake_fetch)

    await scheduler._metadata_refresh_tick()

    assert calls == []
    with Session(sqlite_engine) as s:
        product = s.get(models.Product, product_id)
        assert product.metadata_attempts == 1  # untouched -- not due yet


def test_metadata_refresh_due_backoff_schedule(sqlite_engine, make_product) -> None:
    """Pure-function coverage of the exponential backoff gate: never
    attempted -> due immediately; otherwise due only once
    2**(attempts-1) * base minutes have elapsed since the last try."""
    scheduler = _make_scheduler(sqlite_engine)
    now = datetime.now(UTC)
    base = Scheduler._METADATA_REFRESH_BASE_MINUTES

    with Session(sqlite_engine) as s:
        never_attempted = make_product(s, asin="B0DUE00001")

    assert scheduler._metadata_refresh_due(never_attempted, now) is True

    # attempts=1 -> next due after `base` minutes.
    just_tried = models.Product(
        id=1, asin="x", title="t",
        metadata_attempts=1, metadata_last_attempt_at=now - timedelta(minutes=base - 1),
        created_at=now, updated_at=now,
    )
    assert scheduler._metadata_refresh_due(just_tried, now) is False
    overdue = models.Product(
        id=1, asin="x", title="t",
        metadata_attempts=1, metadata_last_attempt_at=now - timedelta(minutes=base + 1),
        created_at=now, updated_at=now,
    )
    assert scheduler._metadata_refresh_due(overdue, now) is True

    # attempts=3 -> backoff is base * 2**2 = 4x base minutes.
    still_backing_off = models.Product(
        id=1, asin="x", title="t",
        metadata_attempts=3, metadata_last_attempt_at=now - timedelta(minutes=base * 2),
        created_at=now, updated_at=now,
    )
    assert scheduler._metadata_refresh_due(still_backing_off, now) is False
