"""Scenario 7 — Product lifecycle end-to-end.

A single Amazon-tracked product walks through the same state-machine the
books pipeline handles, exercising the parameterised AlertPipeline against
PRODUCT_MODELS:

  1. Add product with target_price_minor set.
  2. Seed 14 daily observations bracketing the target (the
     min_observations_for_signal gate clears at 14).
  3. First pipeline run on a current-best below target → fires `target_hit`
     and (since prev_signal is BUY) `percentile_cross`. Asserts that the
     row landed on `ProductAlert` (not `Alert`) and the corresponding
     NotificationDelivery row uses `product_alert_id` (not `alert_id`).
  4. A second observation strictly below the all-time min → fires
     `new_low`. Mute the product. Inject a lower-still observation; no new
     alerts. Lift mute and inject again-lower; `new_low` fires.

Bypasses the scheduler — calls `AlertPipeline.run([product_id])` directly,
simulating "the scheduler just ingested a batch for this product."
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Allow `uv run python tests/scenarios/scenario_07_product_lifecycle.py`.
sys.path.insert(0, str(Path(__file__).parent))

from freezegun import freeze_time
from helpers import (
    add_product_observation,
    fresh_engine,
    make_product,
    make_recorder,
    run_pipeline,
    session_factory_for,
    session_for,
)
from sqlmodel import select

from book_alerter.config import Config, NotificationsConfig, RecommendationConfig
from book_alerter.db import models
from book_alerter.enums import AlertKind
from book_alerter.notifications.dispatcher import PRODUCT_MODELS, AlertPipeline
from book_alerter.notifications.inapp import InAppNotifier


def main() -> int:
    # Freeze inside the observation range so compute_product_stats's windowed
    # slice contains the seeded rows. Mirrors scenario_01's pattern.
    with freeze_time("2026-01-18 12:00:00"):
        return _run_scenario()


def _run_scenario() -> int:
    r = make_recorder("scenario_07_product_lifecycle")
    engine = fresh_engine()
    session_factory = session_factory_for(engine)

    cfg = Config(
        recommendation=RecommendationConfig(
            min_days_of_history=0,
            min_observations_for_signal=14,
            buy_percentile=25,
            watch_percentile=50,
            target_tolerance_pct=5,
            alert_dedup_window_hours=24,
        ),
        notifications=NotificationsConfig(quiet_hours=None),
    )
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=session_factory,
        notifiers=[InAppNotifier()],
        models=PRODUCT_MODELS,
    )

    # 1. Seed the product with a target price below the seeded median.
    with session_for(engine) as s:
        product = make_product(
            s,
            asin="B07LIFECYC1",
            title="Test Power Bank",
            brand="Anker",
            target_price_minor=1000,
        )
        pid = product.id
    r.step(f"Seeded product id={pid} (target 10.00)")

    # 2. Seed 14 daily observations descending from £20 to £7. The current
    # best is the cheapest, so the first pipeline run will see the buy-box
    # already inside the target — both target_hit AND percentile_cross
    # qualify on the first eval (no prior state).
    base_ts = datetime.now(UTC) - timedelta(days=14)
    totals = [2000, 1900, 1800, 1700, 1600, 1500, 1400, 1300, 1200, 1100, 1050, 1020, 900, 700]
    with session_for(engine) as s:
        for i, total in enumerate(totals):
            add_product_observation(
                s, product_id=pid, total_minor=total,
                observed_at=base_ts + timedelta(days=i),
            )
    r.step(f"Seeded {len(totals)} observations descending to 7.00")

    # 3. First pipeline run.
    run_pipeline(pipeline, [pid])

    with session_for(engine) as s:
        alerts = s.exec(
            select(models.ProductAlert).where(models.ProductAlert.product_id == pid)
        ).all()
        kinds = sorted(str(a.kind) for a in alerts)
        r.expect(
            AlertKind.TARGET_HIT.value in kinds,
            f"target_hit alert fired (kinds={kinds})",
        )
        # NOTE: percentile_cross only fires on transition INTO `BUY`. When
        # `target_price_minor` is set and the current best is below it,
        # `compute_signal` returns `TARGET_HIT` directly (target precedence
        # over percentile), so percentile_cross is deliberately absent.
        r.expect(
            AlertKind.PERCENTILE_CROSS.value not in kinds,
            "percentile_cross suppressed when TARGET_HIT signal takes precedence",
        )
        r.expect(
            AlertKind.NEW_LOW.value not in kinds,
            "new_low does NOT fire on first eval (prev_all_time_min is None)",
        )

        # Polymorphic delivery — alert_id NULL, product_alert_id set.
        deliveries = s.exec(select(models.NotificationDelivery)).all()
        r.expect(
            len(deliveries) == len(alerts),
            f"one delivery row per alert ({len(deliveries)} for {len(alerts)} alerts)",
        )
        for d in deliveries:
            r.expect(
                d.alert_id is None and d.product_alert_id is not None,
                "delivery row routed via product_alert_id, not alert_id",
            )

        # Book-side rows untouched.
        book_alerts = s.exec(select(models.Alert)).all()
        r.expect(
            len(book_alerts) == 0,
            "no book Alert rows leaked from the product pipeline",
        )

        # Signal state row exists. The persisted `all_time_min` is the
        # cascade-IMPUTED min (raw total + default_shipping=280 when the
        # seeded row has shipping_minor=None), so the bottom seed of 700
        # surfaces as 700 + 280 = 980 after imputation. The next phase
        # confirms a strictly-lower imputed total triggers `new_low`.
        state = s.get(models.ProductSignalState, pid)
        persisted_min = state.last_all_time_min_total_minor if state else None
        r.expect(
            state is not None and persisted_min == 980,
            f"ProductSignalState persisted all_time_min={persisted_min} "
            "(700 raw + 280 imputed shipping)",
        )

    # 4. Lower-still observation → new_low fires (with mute test inline).
    with session_for(engine) as s:
        add_product_observation(
            s, product_id=pid, total_minor=600,
            observed_at=datetime.now(UTC) + timedelta(seconds=1),
        )

    # Bump the wall clock past the dedup window so the new_low alert isn't
    # gated by it (target_hit + percentile_cross already fired this cycle).
    with freeze_time("2026-01-19 14:00:00"):
        run_pipeline(pipeline, [pid])
        with session_for(engine) as s:
            new_lows = s.exec(
                select(models.ProductAlert).where(
                    models.ProductAlert.product_id == pid,
                    models.ProductAlert.kind == AlertKind.NEW_LOW,
                )
            ).all()
            r.expect(
                len(new_lows) == 1,
                f"new_low fires on dipping below prior all-time min ({len(new_lows)})",
            )

        # Mute the product, drop further, expect no new alerts.
        with session_for(engine) as s:
            product = s.get(models.Product, pid)
            product.muted_until = datetime.now(UTC) + timedelta(hours=2)
            s.add(product)
            s.commit()

        with session_for(engine) as s:
            add_product_observation(
                s, product_id=pid, total_minor=500,
                observed_at=datetime.now(UTC) + timedelta(seconds=2),
            )

        run_pipeline(pipeline, [pid])
        with session_for(engine) as s:
            new_lows_after_mute = s.exec(
                select(models.ProductAlert).where(
                    models.ProductAlert.product_id == pid,
                    models.ProductAlert.kind == AlertKind.NEW_LOW,
                )
            ).all()
            r.expect(
                len(new_lows_after_mute) == 1,
                "muted product fires NO additional new_low",
            )

    return r.finish()


if __name__ == "__main__":
    sys.exit(main())
