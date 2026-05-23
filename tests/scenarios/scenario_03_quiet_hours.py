"""Scenario 3 — Quiet hours.

Implementation notes (from `notifications/dispatcher.py`):
- When inside the quiet-hours window, the Alert row IS created.
- Notifiers with `bypasses_quiet_hours=True` (only `InAppNotifier`) deliver.
- All other notifiers are SUPPRESSED (not deferred — no replay logic).
- After the quiet window ends, future runs would only re-fire alerts if the
  buy condition still holds AND the dedup window has lapsed.

So this scenario tests the ACTUAL behavior (suppression, not deferral). The
spec gap (no deferral) is recorded in REPORT.md.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from freezegun import freeze_time
from helpers import (
    add_observation,
    fresh_engine,
    make_book,
    make_recorder,
    run_pipeline,
    session_factory_for,
    session_for,
)
from sqlmodel import select

from book_alerter.config import Config, NotificationsConfig, QuietHours, RecommendationConfig
from book_alerter.db import models
from book_alerter.db.models import Alert, Book
from book_alerter.notifications.base import Notifier
from book_alerter.notifications.dispatcher import AlertPipeline
from book_alerter.notifications.inapp import InAppNotifier


class _RecordingNtfy(Notifier):
    """Stands in for the ntfy channel — does NOT bypass quiet hours, so the
    dispatcher should skip it inside the window."""

    name = "ntfy"

    def __init__(self) -> None:
        self.calls: list[tuple[int | None, int | None]] = []

    async def send(self, alert: Alert, book: Book) -> dict:
        self.calls.append((alert.id, book.id))
        return {"status": "sent"}


def main() -> int:
    r = make_recorder("scenario_03_quiet_hours")
    engine = fresh_engine()
    session_factory = session_factory_for(engine)

    # Quiet hours 22:00–08:00 Europe/London.
    cfg = Config(
        recommendation=RecommendationConfig(
            min_observations_for_signal=14,
            alert_dedup_window_hours=24,
        ),
        notifications=NotificationsConfig(
            quiet_hours=QuietHours(start="22:00", end="08:00", tz="Europe/London"),
        ),
    )
    recorder = _RecordingNtfy()
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=session_factory,
        notifiers=[InAppNotifier(), recorder],
    )

    with session_for(engine) as s:
        book = make_book(s, isbn13="9780000003003", target_price_minor=1000)
        book_id = book.id
        base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        for i in range(14):
            add_observation(
                s,
                book_id=book_id,
                total_minor=900 + i,
                source=f"warm_{i}",
                observed_at=base + timedelta(days=i),
            )

    # 23:30 BST is inside the quiet hours window (22:00–08:00 Europe/London).
    # Note: 2026-05-14 in London is BST (UTC+1), so 23:30 BST == 22:30 UTC.
    r.step("Run inside quiet hours (23:30 Europe/London)")
    with freeze_time("2026-05-14 22:30:00"):  # 22:30 UTC == 23:30 BST
        run_pipeline(pipeline, [book_id])

    with session_for(engine) as s:
        alerts = s.exec(select(models.Alert)).all()
        deliveries = s.exec(select(models.NotificationDelivery)).all()

    r.step(
        f"alerts={len(alerts)} deliveries={len(deliveries)} "
        f"channels={[d.channel for d in deliveries]} "
        f"ntfy_calls={len(recorder.calls)}"
    )
    r.expect(len(alerts) == 1, f"alert row created (got {len(alerts)})")
    r.expect(
        alerts[0].delivered_via == ["inapp"],
        f"delivered_via lists only inapp (got {alerts[0].delivered_via})",
    )
    r.expect(
        [d.channel for d in deliveries] == ["inapp"],
        f"only inapp NotificationDelivery row (got {[d.channel for d in deliveries]})",
    )
    r.expect(
        len(recorder.calls) == 0,
        f"ntfy notifier was NOT called during quiet hours (calls={len(recorder.calls)})",
    )

    # Re-run AFTER quiet hours (09:00 Europe/London = 08:00 UTC). The dedup
    # window is still active (alert.fired_at ~23:30 BST is well within 24h),
    # so no new alerts fire — this verifies the documented behavior:
    # quiet-hours-suppressed alerts are NOT replayed. The user has to wait
    # for the dedup window to lapse + the condition still holding.
    r.step("Re-run after quiet hours (09:00 Europe/London)")
    with freeze_time("2026-05-15 08:00:00"):  # 08:00 UTC == 09:00 BST, outside window
        run_pipeline(pipeline, [book_id])

    with session_for(engine) as s:
        alerts = s.exec(select(models.Alert)).all()
        deliveries = s.exec(select(models.NotificationDelivery)).all()
    r.expect(
        len(recorder.calls) == 0,
        f"ntfy STILL not called after quiet hours (no replay) — "
        f"calls={len(recorder.calls)}",
    )
    r.expect(
        len(alerts) == 1,
        f"alert count unchanged — quiet-hours-skipped alerts are not replayed "
        f"(got {len(alerts)})",
    )

    return r.finish()


if __name__ == "__main__":
    sys.exit(main())
