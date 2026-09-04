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


# --- T6.3: periodic refresh --------------------------------------------------


def test_refresh_adds_only_new_dates_and_never_duplicates(sqlite_engine, monkeypatch):
    """The property T6.3 depends on, and which did NOT exist before it.

    The plan assumed "dedup by date already exists". It did not: the only
    guard was "skip if ANY keepa row exists", so a periodic re-run would
    either do nothing (guard intact) or duplicate the whole history every
    week (guard removed). `refresh=True` skips the coarse guard and dedups
    per (seller, condition, observed_at) instead.
    """
    book_id = _make_book(sqlite_engine)
    day1, day2 = date(2026, 9, 1), date(2026, 9, 2)

    monkeypatch.setattr(
        keepa_chart, "extract_observations",
        lambda png: [keepa_chart.ExtractedObservation(day1, "amazon", 1500)],
    )
    first = keepa_backfill.backfill_blocking(
        book_id, "9780000000001", lambda: Session(sqlite_engine),
        schema=_fake_book_schema(),
    )
    assert first == 1

    # The chart now carries the original point plus a new one.
    monkeypatch.setattr(
        keepa_chart, "extract_observations",
        lambda png: [
            keepa_chart.ExtractedObservation(day1, "amazon", 1500),
            keepa_chart.ExtractedObservation(day2, "amazon", 1400),
        ],
    )

    # Without refresh, the coarse guard still short-circuits — the existing
    # first-backfill behaviour must be untouched.
    assert keepa_backfill.backfill_blocking(
        book_id, "9780000000001", lambda: Session(sqlite_engine),
        schema=_fake_book_schema(),
    ) == 0

    added = keepa_backfill.backfill_blocking(
        book_id, "9780000000001", lambda: Session(sqlite_engine),
        schema=_fake_book_schema(), refresh=True,
    )
    assert added == 1, "only the genuinely new date is inserted"

    # Running it again adds nothing — the job is weekly, so this is the
    # normal case, not an edge case.
    assert keepa_backfill.backfill_blocking(
        book_id, "9780000000001", lambda: Session(sqlite_engine),
        schema=_fake_book_schema(), refresh=True,
    ) == 0

    with Session(sqlite_engine) as s:
        rows = s.exec(
            select(models.PriceObservation).where(
                models.PriceObservation.book_id == book_id
            )
        ).all()
    assert len(rows) == 2
    assert sorted(r.price_minor for r in rows) == [1400, 1500]


def test_keepa_refresh_tick_is_a_noop_when_disabled(sqlite_engine, monkeypatch):
    """Default-off is the shipped state, so the disabled path is the one that
    actually runs in production — it must not touch Keepa at all."""
    _make_book(sqlite_engine)

    def _boom(png):
        raise AssertionError("must not reach the chart layer when disabled")

    monkeypatch.setattr(keepa_chart, "extract_observations", _boom)
    assert keepa_backfill.keepa_refresh_tick(
        lambda: Session(sqlite_engine), enabled=False
    ) == 0


def test_keepa_refresh_tick_survives_one_item_failing(sqlite_engine, monkeypatch):
    """A single bad chart (Keepa 404, OCR failure, a rate-limit page) must not
    cost the remaining items their refresh."""
    _make_book(sqlite_engine, isbn13="9780000000001")
    _make_book(sqlite_engine, isbn13="9780000000002")

    calls: list[str] = []

    def _sometimes_boom(item_id, identifier, session_factory, *, schema, refresh=False):
        calls.append(identifier)
        if identifier == "9780000000001":
            raise RuntimeError("keepa returned a rate-limit page")
        return 3

    monkeypatch.setattr(keepa_backfill, "backfill_blocking", _sometimes_boom)
    total = keepa_backfill.keepa_refresh_tick(
        lambda: Session(sqlite_engine), enabled=True
    )

    assert len(calls) == 2, "the failure must not abandon the second item"
    assert total == 3
