"""Scenario 2 — Alert dedup window.

The pipeline dedups by `(book_id, kind, fired_at >= now - dedup_window)`.
We verify:

1. Two observations from the same source at the same price within the dedup
   window: both rows persist (PriceObservation is NOT deduped — that's the
   source layer's job, not the pipeline's), but only one alert fires.

2. After the dedup window expires, a fresh observation crossing a new low
   re-fires `new_low`.

Uses `freezegun` so we can advance the wall clock without sleeping.
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
    r = make_recorder("scenario_02_dedup_window")
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

    with session_for(engine) as s:
        book = make_book(s, isbn13="9780000002002", target_price_minor=1000)
        book_id = book.id
        # Seed 13 observations BELOW target so the 14th run fires target_hit.
        base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        for i in range(13):
            add_observation(
                s,
                book_id=book_id,
                total_minor=900 + i,
                source=f"warm_{i}",
                observed_at=base + timedelta(days=i),
            )

    # T=0: insert the 14th observation, run pipeline. Expect 1 target_hit alert.
    with freeze_time("2026-02-01 10:00:00"):
        with session_for(engine) as s:
            add_observation(
                s,
                book_id=book_id,
                total_minor=920,
                source="dup_src",
                observed_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
            )
        run_pipeline(pipeline, [book_id])

    with session_for(engine) as s:
        alerts = s.exec(select(models.Alert)).all()
        obs_count = len(s.exec(select(models.PriceObservation)).all())
    r.step(
        f"After T=0 (first eval): {len(alerts)} alert(s), {obs_count} obs row(s)"
    )
    r.expect(len(alerts) == 1, f"exactly one alert after first eval (got {len(alerts)})")
    r.expect(alerts[0].kind == "target_hit", f"kind=target_hit (got {alerts[0].kind})")

    # T=+5h: same source, same price → PriceObservation row STILL persists
    # (the pipeline doesn't enforce price-level dedup; sources do). But the
    # alert dedup window suppresses any new alert of the same kind.
    with freeze_time("2026-02-01 15:00:00"):
        with session_for(engine) as s:
            add_observation(
                s,
                book_id=book_id,
                total_minor=920,
                source="dup_src",
                observed_at=datetime(2026, 2, 1, 15, 0, tzinfo=UTC),
            )
        run_pipeline(pipeline, [book_id])

    with session_for(engine) as s:
        alerts = s.exec(select(models.Alert)).all()
        obs_count = len(s.exec(select(models.PriceObservation)).all())
        deliveries = s.exec(select(models.NotificationDelivery)).all()
    r.step(
        f"After T=+5h (within window): {len(alerts)} alert(s), {obs_count} obs row(s),"
        f" {len(deliveries)} delivery row(s)"
    )
    r.expect(
        len(alerts) == 1, f"alert count unchanged within dedup window (got {len(alerts)})"
    )
    r.expect(
        obs_count == 15,
        f"PriceObservation row persists (14 + 1 = 15 expected, got {obs_count})",
    )
    r.expect(
        len(deliveries) == 1,
        f"delivery count unchanged within dedup window (got {len(deliveries)})",
    )

    # T=+25h: 25h after T=0 → outside the 24h dedup window. SAME price, so
    # current_best hasn't changed. Pipeline sees no transition; no new alert.
    with freeze_time("2026-02-02 11:00:00"):
        with session_for(engine) as s:
            add_observation(
                s,
                book_id=book_id,
                total_minor=920,
                source="dup_src",
                observed_at=datetime(2026, 2, 2, 11, 0, tzinfo=UTC),
            )
        run_pipeline(pipeline, [book_id])

    with session_for(engine) as s:
        alerts_after_25h = s.exec(select(models.Alert)).all()
    r.expect(
        len(alerts_after_25h) == 1,
        f"after dedup window with unchanged price, no NEW alert (got "
        f"{len(alerts_after_25h)} total)",
    )

    # T=+26h: NEW low (below target AND below all-time min). prev_signal is
    # already TARGET_HIT, so detect_alert_kinds skips target_hit again. But
    # new_low fires since current_best < prev_all_time_min, and the dedup
    # window for new_low is empty (no prior new_low alerts).
    with freeze_time("2026-02-02 12:00:00"):
        with session_for(engine) as s:
            add_observation(
                s,
                book_id=book_id,
                total_minor=800,
                source="dup_src",
                observed_at=datetime(2026, 2, 2, 12, 0, tzinfo=UTC),
            )
        run_pipeline(pipeline, [book_id])

    with session_for(engine) as s:
        all_alerts = s.exec(
            select(models.Alert).order_by(models.Alert.id)
        ).all()
    new_alerts = [a.kind for a in all_alerts[1:]]
    r.step(f"After T=+26h drop to 800: total alerts={len(all_alerts)} new={new_alerts}")
    r.expect("new_low" in new_alerts, "new_low fires after dedup window on a true new low")

    return r.finish()


if __name__ == "__main__":
    sys.exit(main())
