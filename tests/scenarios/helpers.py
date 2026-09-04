"""Shared helpers for the end-to-end scenario scripts.

Scenarios are NOT pytest tests; they're standalone scripts (so we can run them
individually for storyline-style coverage). They write into a dedicated
SQLite file `tests/scenarios/.test.db` (gitignored) and reuse the real
live-offers/history-summary view DDL (T3.1, migration 0020), so they exercise
the production code paths.
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

from book_alerter.db import models
from book_alerter.db.session import get_engine
from book_alerter.db.views import (
    BOOK_HISTORY_SUMMARY_VIEW_SQL,
    BOOK_LIVE_OFFERS_VIEW_SQL,
    DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL,
    DROP_BOOK_LIVE_OFFERS_VIEW_SQL,
    DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL,
    DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL,
    PRODUCT_HISTORY_SUMMARY_VIEW_SQL,
    PRODUCT_LIVE_OFFERS_VIEW_SQL,
)
from book_alerter.notifications.dispatcher import AlertPipeline

SCENARIO_DIR = Path(__file__).parent
SCENARIO_DB = SCENARIO_DIR / ".test.db"


# --- console output ----------------------------------------------------------


class _Recorder:
    """ANSI-free formatter for scenario output. Each scenario logs a header,
    step lines, and a final PASS/FAIL verdict; we mirror that to stdout."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.failures: list[str] = []
        print(f"\n=== {name} ===")

    def step(self, msg: str) -> None:
        print(f"  - {msg}")

    def expect(self, ok: bool, msg: str) -> None:
        if ok:
            print(f"  [OK]   {msg}")
        else:
            print(f"  [FAIL] {msg}")
            self.failures.append(msg)

    def finish(self) -> int:
        if self.failures:
            print(f"--- {self.name}: FAIL ({len(self.failures)} failure(s)) ---")
            return 1
        print(f"--- {self.name}: PASS ---")
        return 0


def make_recorder(name: str) -> _Recorder:
    return _Recorder(name)


# --- DB setup ---------------------------------------------------------------


def fresh_engine() -> Engine:
    """Drop, recreate, and migrate the scenario sqlite file. Idempotent across
    runs — every scenario invocation gets a pristine schema + all four live
    views (book/product live_offers + book/product history_summary)."""
    if SCENARIO_DB.exists():
        SCENARIO_DB.unlink()
    engine = get_engine(f"sqlite:///{SCENARIO_DB}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(DROP_BOOK_LIVE_OFFERS_VIEW_SQL)
        conn.exec_driver_sql(BOOK_LIVE_OFFERS_VIEW_SQL)
        conn.exec_driver_sql(DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL)
        conn.exec_driver_sql(PRODUCT_LIVE_OFFERS_VIEW_SQL)
        conn.exec_driver_sql(DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL)
        conn.exec_driver_sql(BOOK_HISTORY_SUMMARY_VIEW_SQL)
        conn.exec_driver_sql(DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL)
        conn.exec_driver_sql(PRODUCT_HISTORY_SUMMARY_VIEW_SQL)
    return engine


def session_factory_for(engine: Engine):
    return lambda: Session(engine)


# --- seed helpers -----------------------------------------------------------


def make_book(
    session: Session,
    *,
    isbn13: str = "9780099490548",
    title: str = "Captain Corelli's Mandolin",
    author: str = "Louis de Bernieres",
    target_price_minor: int | None = None,
    percentile_threshold: int | None = None,
    alert_kinds_disabled: list[str] | None = None,
    muted_until: datetime | None = None,
) -> models.Book:
    now = datetime.now(UTC)
    book = models.Book(
        isbn13=isbn13,
        title=title,
        author=author,
        target_price_minor=target_price_minor,
        percentile_threshold=percentile_threshold,
        alert_kinds_disabled=alert_kinds_disabled or [],
        muted_until=muted_until,
        created_at=now,
        updated_at=now,
    )
    session.add(book)
    session.commit()
    session.refresh(book)
    return book


def add_observation(
    session: Session,
    *,
    book_id: int,
    total_minor: int,
    source: str = "wob",
    condition: str = "used_g",
    observed_at: datetime | None = None,
    seller: str | None = None,
) -> models.PriceObservation:
    when = observed_at or datetime.now(UTC)
    obs = models.PriceObservation(
        book_id=book_id,
        source=source,
        seller=seller,
        condition=condition,
        price_minor=total_minor,
        currency="GBP",
        total_minor=total_minor,
        url=f"https://example.com/{source}/{total_minor}",
        observed_at=when,
        last_seen_at=when,
        raw={},
    )
    session.add(obs)
    session.commit()
    session.refresh(obs)
    return obs


def make_product(
    session: Session,
    *,
    asin: str = "B07PRODUCT1",
    title: str = "Test Product",
    brand: str | None = "TestBrand",
    target_price_minor: int | None = None,
    track_used: bool = False,
) -> models.Product:
    now = datetime.now(UTC)
    product = models.Product(
        asin=asin,
        title=title,
        brand=brand,
        target_price_minor=target_price_minor,
        track_used=track_used,
        created_at=now,
        updated_at=now,
    )
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


def add_product_observation(
    session: Session,
    *,
    product_id: int,
    total_minor: int,
    source: str = "amazon_uk_product",
    condition: str = "new",
    observed_at: datetime | None = None,
    seller: str | None = "Amazon",
) -> models.ProductObservation:
    when = observed_at or datetime.now(UTC)
    obs = models.ProductObservation(
        product_id=product_id,
        source=source,
        seller=seller,
        condition=condition,
        price_minor=total_minor,
        currency="GBP",
        total_minor=total_minor,
        url=f"https://example.com/{source}/{total_minor}",
        observed_at=when,
        last_seen_at=when,
        raw={},
    )
    session.add(obs)
    session.commit()
    session.refresh(obs)
    return obs


# --- pipeline helper --------------------------------------------------------


def run_pipeline(pipeline: AlertPipeline, book_ids: list[int]) -> None:
    asyncio.run(pipeline.run(book_ids))


# --- assertion glue ---------------------------------------------------------


@contextmanager
def session_for(engine: Engine) -> Iterator[Session]:
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(0)
