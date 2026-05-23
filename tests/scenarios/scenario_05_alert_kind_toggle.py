"""Scenario 5 — Alert-kind toggles.

The pipeline applies BOTH filters before persisting alerts:
- Global toggle: `cfg.notifications.alert_kinds_enabled` (list).
- Per-book toggle: `book.alert_kinds_disabled` (list).

This scenario drives a book into a state where MULTIPLE kinds would
normally fire on a single eval, but with one kind disabled. We verify the
disabled kind is suppressed while the other kinds still fire.

Approach:
- Pre-warm the book so the prior eval lands at WAIT or WATCH (with a
  prev_all_time_min set).
- Then add a new low that ALSO crosses below target → target_hit fires
  AND new_low fires.
- Disable target_hit globally → only new_low should remain.
- Reset and disable target_hit per-book → same outcome.
- Reset and disable nothing → both fire as a sanity baseline.
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

from book_alerter.config import (
    Config,
    NotificationsConfig,
    RecommendationConfig,
)
from book_alerter.db import models
from book_alerter.notifications.dispatcher import AlertPipeline
from book_alerter.notifications.inapp import InAppNotifier


def _drive_to_double_alert(engine, *, isbn: str, target_minor: int,
                           extra_book_kwargs: dict | None = None) -> int:
    """Seed a book and produce 13 obs around 900, run pipeline so the
    book has prev_signal == TARGET_HIT and prev_all_time_min == 900.
    Then add a NEW low at 700 — which would normally fire `new_low`. The
    pipeline state for target_hit suppresses target_hit re-firing (already
    in TARGET_HIT), so only new_low remains.

    To actually exercise the kind-toggle filter on target_hit, we re-seed
    fresh state where prev_signal is NOT TARGET_HIT — by ensuring the
    book starts ABOVE the target, then later drops below it AND simultaneously
    sets a new all-time min.
    """
    cfg = Config(
        recommendation=RecommendationConfig(min_observations_for_signal=14),
        notifications=NotificationsConfig(quiet_hours=None),
    )
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=session_factory_for(engine),
        notifiers=[InAppNotifier()],
    )

    with session_for(engine) as s:
        book = make_book(
            s,
            isbn13=isbn,
            target_price_minor=target_minor,
            **(extra_book_kwargs or {}),
        )
        book_id = book.id
        base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        # 14 observations ABOVE target so prev_signal = BUY/WATCH/WAIT and
        # prev_all_time_min is set.
        for i in range(14):
            add_observation(
                s,
                book_id=book_id,
                total_minor=target_minor + 100 + i * 10,
                source=f"warm_{i}",
                observed_at=base + timedelta(days=i),
            )
    run_pipeline(pipeline, [book_id])
    return book_id


def _drop_below(engine, book_id: int, total_minor: int) -> None:
    """Add a single observation that crosses below target and sets a new
    all-time min — meant to fire `target_hit` AND `new_low` together."""
    with session_for(engine) as s:
        add_observation(
            s,
            book_id=book_id,
            total_minor=total_minor,
            source="drop_src",
            observed_at=datetime(2026, 2, 1, 12, 0, tzinfo=UTC),
        )


def _alerts_for(engine, book_id: int) -> list[str]:
    with session_for(engine) as s:
        rows = s.exec(
            select(models.Alert).where(models.Alert.book_id == book_id)
        ).all()
    return [a.kind for a in rows]


def main() -> int:
    # Same time-rot guard as scenario_01: freeze "now" just past the latest
    # observation so windowed stats include the full series even when the
    # real clock has moved on. See scenario_01_signal_transitions.py for
    # the failure mode this prevents.
    with freeze_time("2026-02-02 12:00:00"):
        return _run_scenario()


def _run_scenario() -> int:
    r = make_recorder("scenario_05_alert_kind_toggle")

    # --- Case 1: baseline, everything enabled — expect target_hit + new_low.
    engine = fresh_engine()
    book_id = _drive_to_double_alert(engine, isbn="9780000005001", target_minor=1000)
    initial_alerts = _alerts_for(engine, book_id)
    r.step(f"Case 1 (baseline) after warmup: {initial_alerts}")
    _drop_below(engine, book_id, total_minor=700)
    cfg = Config(
        recommendation=RecommendationConfig(min_observations_for_signal=14),
        notifications=NotificationsConfig(quiet_hours=None),
    )
    AlertPipeline(
        cfg=cfg,
        session_factory=session_factory_for(engine),
        notifiers=[InAppNotifier()],
    )
    run_pipeline(
        AlertPipeline(
            cfg=cfg,
            session_factory=session_factory_for(engine),
            notifiers=[InAppNotifier()],
        ),
        [book_id],
    )
    new_kinds = [k for k in _alerts_for(engine, book_id) if k not in initial_alerts] \
        or _alerts_for(engine, book_id)[len(initial_alerts):]
    # We just look at the full list and subtract:
    final = _alerts_for(engine, book_id)
    new_kinds = final[len(initial_alerts):]
    r.step(f"Case 1 new alerts: {new_kinds}")
    r.expect("target_hit" in new_kinds, "Case 1: target_hit fires (baseline)")
    r.expect("new_low" in new_kinds, "Case 1: new_low fires (baseline)")

    # --- Case 2: global toggle disables target_hit only.
    engine = fresh_engine()
    cfg_no_target = Config(
        recommendation=RecommendationConfig(min_observations_for_signal=14),
        notifications=NotificationsConfig(
            alert_kinds_enabled=["new_low", "percentile_cross"],
            quiet_hours=None,
        ),
    )
    pipeline_no_target = AlertPipeline(
        cfg=cfg_no_target,
        session_factory=session_factory_for(engine),
        notifiers=[InAppNotifier()],
    )
    # Re-run warmup with this pipeline.
    with session_for(engine) as s:
        book = make_book(s, isbn13="9780000005002", target_price_minor=1000)
        book_id = book.id
        base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        for i in range(14):
            add_observation(
                s,
                book_id=book_id,
                total_minor=1100 + i * 10,
                source=f"warm_{i}",
                observed_at=base + timedelta(days=i),
            )
    run_pipeline(pipeline_no_target, [book_id])
    warm_alerts = _alerts_for(engine, book_id)
    _drop_below(engine, book_id, total_minor=700)
    run_pipeline(pipeline_no_target, [book_id])
    final = _alerts_for(engine, book_id)
    new_kinds = final[len(warm_alerts):]
    r.step(f"Case 2 (global target_hit disabled) new alerts: {new_kinds}")
    r.expect("target_hit" not in new_kinds, "Case 2: target_hit suppressed by global toggle")
    r.expect("new_low" in new_kinds, "Case 2: new_low still fires")

    # --- Case 3: per-book toggle disables target_hit only.
    engine = fresh_engine()
    cfg = Config(
        recommendation=RecommendationConfig(min_observations_for_signal=14),
        notifications=NotificationsConfig(quiet_hours=None),
    )
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=session_factory_for(engine),
        notifiers=[InAppNotifier()],
    )
    with session_for(engine) as s:
        book = make_book(
            s,
            isbn13="9780000005003",
            target_price_minor=1000,
            alert_kinds_disabled=["target_hit"],
        )
        book_id = book.id
        base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        for i in range(14):
            add_observation(
                s,
                book_id=book_id,
                total_minor=1100 + i * 10,
                source=f"warm_{i}",
                observed_at=base + timedelta(days=i),
            )
    run_pipeline(pipeline, [book_id])
    warm_alerts = _alerts_for(engine, book_id)
    _drop_below(engine, book_id, total_minor=700)
    run_pipeline(pipeline, [book_id])
    final = _alerts_for(engine, book_id)
    new_kinds = final[len(warm_alerts):]
    r.step(f"Case 3 (per-book target_hit disabled) new alerts: {new_kinds}")
    r.expect(
        "target_hit" not in new_kinds, "Case 3: target_hit suppressed by per-book toggle",
    )
    r.expect("new_low" in new_kinds, "Case 3: new_low still fires")

    return r.finish()


if __name__ == "__main__":
    sys.exit(main())
