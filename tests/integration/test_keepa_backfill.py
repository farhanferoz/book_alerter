"""Keepa backfill: future-dated row guard (T4.3, layer 2).

`_DateCalib.__call__` (keepa_chart.py) already clamps its own output to
today, but `backfill_blocking` independently drops anything still dated
beyond today before persisting — defence in depth so a future regression to
the calibration can't silently reintroduce future-dated rows (F9: a probe
product received a Keepa row stamped 2026-09-05T00:00Z on 2026-09-04).

These tests bypass the PNG/OCR pipeline (monkeypatch `extract_observations`)
so they exercise the guard in isolation, independent of whatever the chart
layer's own clamp does — including the case a chart-layer regression would
NOT have caught.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from freezegun import freeze_time
from sqlmodel import Session, select

from book_alerter import keepa_backfill, keepa_chart
from book_alerter.db import models
from book_alerter.keepa_backfill import KeepaBackfillSchema


def _fake_book_schema() -> KeepaBackfillSchema:
    return KeepaBackfillSchema(
        item_model=models.Book,
        observation_model=models.PriceObservation,
        fk_attr="book_id",
        fetch_png=lambda identifier: b"fake-png-bytes",
        dp_url_for=lambda identifier: f"https://amazon.co.uk/dp/{identifier}",
    )


def _make_book(sqlite_engine, isbn13: str = "9780000000001") -> int:
    now = datetime.now(UTC)
    with Session(sqlite_engine) as s:
        book = models.Book(isbn13=isbn13, title="t", author="a", created_at=now, updated_at=now)
        s.add(book)
        s.commit()
        s.refresh(book)
        assert book.id is not None
        return book.id


def test_backfill_drops_future_dated_rows(sqlite_engine, monkeypatch):
    book_id = _make_book(sqlite_engine)

    with freeze_time("2026-09-04 12:00:00"):
        today = date(2026, 9, 4)
        tomorrow = today + timedelta(days=1)
        monkeypatch.setattr(
            keepa_chart,
            "extract_observations",
            lambda png: [
                keepa_chart.ExtractedObservation(today, "amazon", 1500),
                keepa_chart.ExtractedObservation(tomorrow, "new", 1600),
            ],
        )
        inserted = keepa_backfill.backfill_blocking(
            book_id,
            "9780000000001",
            lambda: Session(sqlite_engine),
            schema=_fake_book_schema(),
        )

    # Only the today-dated row survives; the tomorrow-dated one is dropped.
    assert inserted == 1
    with Session(sqlite_engine) as s:
        rows = s.exec(
            select(models.PriceObservation).where(models.PriceObservation.book_id == book_id)
        ).all()
    assert len(rows) == 1
    assert rows[0].observed_at.date() == today
    assert rows[0].price_minor == 1500


def test_backfill_returns_zero_when_every_row_is_future_dated(sqlite_engine, monkeypatch):
    book_id = _make_book(sqlite_engine)

    with freeze_time("2026-09-04 12:00:00"):
        tomorrow = date(2026, 9, 5)
        monkeypatch.setattr(
            keepa_chart,
            "extract_observations",
            lambda png: [keepa_chart.ExtractedObservation(tomorrow, "amazon", 1500)],
        )
        inserted = keepa_backfill.backfill_blocking(
            book_id,
            "9780000000001",
            lambda: Session(sqlite_engine),
            schema=_fake_book_schema(),
        )

    assert inserted == 0
    with Session(sqlite_engine) as s:
        rows = s.exec(
            select(models.PriceObservation).where(models.PriceObservation.book_id == book_id)
        ).all()
    assert rows == []


def test_backfill_keeps_all_rows_when_none_are_future_dated(sqlite_engine, monkeypatch):
    book_id = _make_book(sqlite_engine)

    with freeze_time("2026-09-04 12:00:00"):
        today = date(2026, 9, 4)
        yesterday = today - timedelta(days=1)
        monkeypatch.setattr(
            keepa_chart,
            "extract_observations",
            lambda png: [
                keepa_chart.ExtractedObservation(yesterday, "amazon", 1400),
                keepa_chart.ExtractedObservation(today, "new", 1500),
            ],
        )
        inserted = keepa_backfill.backfill_blocking(
            book_id,
            "9780000000001",
            lambda: Session(sqlite_engine),
            schema=_fake_book_schema(),
        )

    assert inserted == 2
