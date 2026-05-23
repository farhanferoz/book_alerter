"""Scenario 4 — Per-book mute (`muted_until`).

When a book has `muted_until > now`, the pipeline:
- Skips the entire evaluation (no stats read, no alert detection).
- Does NOT persist BookSignalState (intentional — see comment in
  `_run_one`). This means a price drop while muted is preserved as a
  pending transition: when the mute lifts, the next run sees no prev
  state and fires alerts normally based on the (possibly multiple)
  observations accumulated during the mute.

We verify the documented behavior.
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

from book_alerter.config import Config, NotificationsConfig, RecommendationConfig
from book_alerter.db import models
from book_alerter.notifications.dispatcher import AlertPipeline
from book_alerter.notifications.inapp import InAppNotifier


def main() -> int:
    r = make_recorder("scenario_04_per_book_mute")
    engine = fresh_engine()
    session_factory = session_factory_for(engine)

    cfg = Config(
        recommendation=RecommendationConfig(
            min_observations_for_signal=14,
            alert_dedup_window_hours=24,
        ),
        notifications=NotificationsConfig(quiet_hours=None),
    )
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=session_factory,
        notifiers=[InAppNotifier()],
    )

    # Seed a book that is currently MUTED for 24h.
    mute_anchor = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    with session_for(engine) as s:
        book = make_book(
            s,
            isbn13="9780000004004",
            target_price_minor=1000,
            muted_until=mute_anchor + timedelta(hours=24),
        )
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

    # T=0: mute is active. Pipeline must skip ENTIRELY.
    r.step("Run while muted — no alerts, no state row")
    with freeze_time("2026-02-01 12:00:00"):
        run_pipeline(pipeline, [book_id])
    with session_for(engine) as s:
        alerts = s.exec(select(models.Alert)).all()
        state = s.exec(
            select(models.BookSignalState).where(
                models.BookSignalState.book_id == book_id
            )
        ).one_or_none()
    r.expect(len(alerts) == 0, f"no alerts while muted (got {len(alerts)})")
    r.expect(state is None, f"no BookSignalState row while muted (got {state})")

    # T=+25h: mute has lifted. The pipeline runs normally. With no prev
    # signal state, target_hit fires (current_best <= target, prev_signal
    # is None ≠ TARGET_HIT).
    r.step("Re-run after mute lifts — alerts fire normally")
    with freeze_time("2026-02-02 13:00:00"):
        run_pipeline(pipeline, [book_id])
    with session_for(engine) as s:
        alerts = s.exec(select(models.Alert)).all()
        state = s.exec(
            select(models.BookSignalState).where(
                models.BookSignalState.book_id == book_id
            )
        ).one_or_none()
    last_signal = state.last_signal if state else None
    r.step(f"After mute: alerts={[a.kind for a in alerts]} state={last_signal}")
    r.expect(
        any(a.kind == "target_hit" for a in alerts),
        f"target_hit fires after mute lifts (got {[a.kind for a in alerts]})",
    )
    r.expect(
        state is not None and state.last_signal == "TARGET_HIT",
        f"signal=TARGET_HIT (got {state.last_signal if state else None})",
    )

    return r.finish()


if __name__ == "__main__":
    sys.exit(main())
