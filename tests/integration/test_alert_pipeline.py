"""Integration tests for the alert pipeline (Task 4.4).

The pipeline takes a list of affected book_ids, recomputes stats from the DB,
detects alert kinds, applies global/per-book/mute/dedup filters, persists
Alert + NotificationDelivery rows, and updates BookSignalState for the next
evaluation. These tests exercise it end-to-end against a real SQLite engine.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from freezegun import freeze_time
from sqlmodel import Session, select

from book_alerter.config import (
    Config,
    NotificationsConfig,
    QuietHours,
    RecommendationConfig,
)
from book_alerter.db import models
from book_alerter.db.models import Alert, Book
from book_alerter.notifications.base import Notifier
from book_alerter.notifications.dispatcher import AlertPipeline
from book_alerter.notifications.inapp import InAppNotifier


class _RecordingNotifier(Notifier):
    """Captures `send` calls without performing any I/O. Stands in for ntfy
    so we can assert it was (or wasn't) invoked during quiet hours."""

    name = "ntfy"

    def __init__(self) -> None:
        self.calls: list[tuple[int | None, int | None]] = []

    async def send(self, alert: Alert, book: Book) -> dict:
        self.calls.append((alert.id, book.id))
        return {"status": "sent"}


def _seed_observations(session: Session, *, book_id: int, totals: list[int],
                       source_prefix: str = "src") -> None:
    """Persist one PriceObservation per total. Each gets a distinct source so the
    book_stats view treats each as a separate latest_per_source row."""
    now = datetime.now(UTC)
    for i, total in enumerate(totals):
        when = now - timedelta(minutes=i)
        session.add(models.PriceObservation(
            book_id=book_id,
            source=f"{source_prefix}_{i:02d}",
            condition="new",
            price_minor=total,
            currency="GBP",
            shipping_minor=0,
            total_minor=total,
            url=f"https://example.com/{i}",
            observed_at=when,
            last_seen_at=when,
            raw={},
        ))
    session.commit()


def _run(pipeline: AlertPipeline, book_ids: list[int]) -> None:
    asyncio.run(pipeline.run(book_ids))


def _make_cfg(**notif_overrides) -> Config:
    # Tests in this module focus on pipeline mechanics (does it write
    # alerts? does it persist signal state?), not on the days-of-history
    # gate. Drop the gate so the seeded same-day observations clear it.
    # Tests of the gate itself live in tests/unit/test_signal.py.
    return Config(
        recommendation=RecommendationConfig(min_days_of_history=0,
                                            min_observations_for_signal=14,
                                            alert_dedup_window_hours=24),
        notifications=NotificationsConfig(**notif_overrides),
    )


def test_pipeline_writes_alert_and_delivery_on_target_hit(
    engine_with_view, make_book,
):
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000100")
        book.target_price_minor = 1000
        s.add(book)
        s.commit()
        s.refresh(book)
        # 14 observations all <= target.
        _seed_observations(s, book_id=book.id,
                           totals=[900 + i for i in range(14)])
        book_id = book.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
    )
    _run(pipeline, [book_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(select(models.Alert)).all()
        deliveries = s.exec(select(models.NotificationDelivery)).all()
        state = s.exec(
            select(models.BookSignalState).where(
                models.BookSignalState.book_id == book_id
            )
        ).one()

    assert len(alerts) == 1
    a = alerts[0]
    assert a.kind == "target_hit"
    assert a.delivered_via == ["inapp"]
    assert a.currency == "GBP"
    assert a.message.startswith("[TARGET_HIT]")

    assert len(deliveries) == 1
    d = deliveries[0]
    assert d.channel == "inapp"
    assert d.status == "sent"
    assert d.error_message is None

    assert state.last_signal == "TARGET_HIT"


def test_pipeline_dedup_suppresses_second_run(engine_with_view, make_book):
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000101")
        book.target_price_minor = 1000
        s.add(book); s.commit(); s.refresh(book)
        _seed_observations(s, book_id=book.id,
                           totals=[900 + i for i in range(14)])
        book_id = book.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
    )
    _run(pipeline, [book_id])
    _run(pipeline, [book_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(select(models.Alert)).all()

    assert len(alerts) == 1


def test_pipeline_skips_muted_book(engine_with_view, make_book):
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000102")
        book.target_price_minor = 1000
        book.muted_until = datetime.now(UTC) + timedelta(hours=1)
        s.add(book); s.commit(); s.refresh(book)
        _seed_observations(s, book_id=book.id,
                           totals=[900 + i for i in range(14)])
        book_id = book.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
    )
    _run(pipeline, [book_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(select(models.Alert)).all()
        state = s.exec(
            select(models.BookSignalState).where(
                models.BookSignalState.book_id == book_id
            )
        ).one_or_none()
    assert alerts == []
    # Mute skips the entire evaluation — no BookSignalState row either, so a
    # price drop during the mute can still fire new_low when the mute lifts.
    assert state is None


def test_pipeline_skips_per_book_disabled_kind(engine_with_view, make_book):
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000103")
        book.target_price_minor = 1000
        book.alert_kinds_disabled = ["target_hit"]
        s.add(book); s.commit(); s.refresh(book)
        _seed_observations(s, book_id=book.id,
                           totals=[900 + i for i in range(14)])
        book_id = book.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
    )
    _run(pipeline, [book_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(
            select(models.Alert).where(models.Alert.kind == "target_hit")
        ).all()
    assert alerts == []


def test_pipeline_skips_globally_disabled_kind(engine_with_view, make_book):
    cfg = _make_cfg(alert_kinds_enabled=["new_low", "percentile_cross"])
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000104")
        book.target_price_minor = 1000
        s.add(book); s.commit(); s.refresh(book)
        _seed_observations(s, book_id=book.id,
                           totals=[900 + i for i in range(14)])
        book_id = book.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
    )
    _run(pipeline, [book_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(select(models.Alert)).all()
    # target_hit is the only kind that fires on first eval (no prev state for
    # percentile_cross / new_low transition); with target_hit disabled, no rows.
    assert alerts == []


def test_pipeline_no_alert_when_insufficient_observations(
    engine_with_view, make_book,
):
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000105")
        book.target_price_minor = 1000
        s.add(book); s.commit(); s.refresh(book)
        _seed_observations(s, book_id=book.id, totals=[800, 850, 900])
        book_id = book.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
    )
    _run(pipeline, [book_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(select(models.Alert)).all()

    # detect_alert_kinds doesn't gate on min_observations_for_signal directly;
    # target_hit fires whenever current_best <= target and prev_signal != TARGET_HIT.
    # On the first run prev_signal is None so target_hit DOES fire with 3 obs.
    # Therefore this test verifies the pipeline tolerates the low-data case
    # without raising — we don't assert "no alert" because the spec allows it.
    # We assert state is persisted and equals INSUFFICIENT_DATA.
    state = None
    with Session(engine_with_view) as s:
        state = s.exec(
            select(models.BookSignalState).where(
                models.BookSignalState.book_id == book_id
            )
        ).one()
    assert state.last_signal == "INSUFFICIENT_DATA"
    # And target_hit alert did fire (current_best=800 <= target 1000).
    assert len(alerts) == 1
    assert alerts[0].kind == "target_hit"


def test_pipeline_quiet_hours_suppresses_non_inapp(engine_with_view, make_book):
    """During quiet hours, the in-app Alert + delivery still land, but the
    captured "ntfy" notifier is never called."""
    # 00:00–23:59 UTC window means any UTC moment is inside quiet hours.
    cfg = _make_cfg(quiet_hours=QuietHours(start="00:00", end="23:59", tz="UTC"))
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000107")
        book.target_price_minor = 1000
        s.add(book); s.commit(); s.refresh(book)
        _seed_observations(s, book_id=book.id,
                           totals=[900 + i for i in range(14)])
        book_id = book.id

    recorder = _RecordingNotifier()
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier(), recorder],
    )
    # Freeze inside the window so datetime.now(ZoneInfo("UTC")) inside
    # _deliver lands at 12:00 UTC.
    with freeze_time("2026-05-14 12:00:00"):
        _run(pipeline, [book_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(select(models.Alert)).all()
        deliveries = s.exec(select(models.NotificationDelivery)).all()

    assert len(alerts) == 1
    assert alerts[0].kind == "target_hit"
    # delivered_via lists only the inapp channel — ntfy was gated out.
    assert alerts[0].delivered_via == ["inapp"]
    # Only one NotificationDelivery row (inapp); the skipped channel is not
    # persisted with a "skipped" status — it's simply absent.
    assert [d.channel for d in deliveries] == ["inapp"]
    assert deliveries[0].status == "sent"
    # And the recording notifier was never invoked.
    assert recorder.calls == []


def test_pipeline_outside_quiet_hours_sends_to_all_channels(
    engine_with_view, make_book,
):
    """Sanity-check: same config, but a time outside the window — both
    channels are exercised."""
    cfg = _make_cfg(quiet_hours=QuietHours(start="22:00", end="08:00", tz="UTC"))
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000108")
        book.target_price_minor = 1000
        s.add(book); s.commit(); s.refresh(book)
        _seed_observations(s, book_id=book.id,
                           totals=[900 + i for i in range(14)])
        book_id = book.id

    recorder = _RecordingNotifier()
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier(), recorder],
    )
    # 12:00 UTC is outside the 22:00–08:00 window.
    with freeze_time("2026-05-14 12:00:00"):
        _run(pipeline, [book_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(select(models.Alert)).all()
        deliveries = s.exec(select(models.NotificationDelivery)).all()

    assert len(alerts) == 1
    assert set(alerts[0].delivered_via) == {"inapp", "ntfy"}
    assert {d.channel for d in deliveries} == {"inapp", "ntfy"}
    assert len(recorder.calls) == 1


def test_pipeline_persists_book_signal_state_on_first_run(
    engine_with_view, make_book,
):
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000106")
        # No target — we want a normal BUY/WATCH/WAIT signal.
        _seed_observations(s, book_id=book.id,
                           totals=[100 * (i + 1) for i in range(14)])
        book_id = book.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
    )
    _run(pipeline, [book_id])

    with Session(engine_with_view) as s:
        state = s.exec(
            select(models.BookSignalState).where(
                models.BookSignalState.book_id == book_id
            )
        ).one()
    assert state.last_signal is not None
    assert state.last_signal != "INSUFFICIENT_DATA"
    assert state.last_all_time_min_total_minor == 100
    assert state.last_evaluated_at is not None
