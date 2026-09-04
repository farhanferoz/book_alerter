"""Scenario 8 — Amazon Prime toggle flips the signal (T2.2).

Mirrors scenario_01's structure but is deliberately much smaller: it exists
to prove `RecommendationConfig.amazon_prime` reaches the alert pipeline end
to end, not to re-walk the whole signal state machine.

One book gets exactly two observations -- a cheap non-Prime-eligible "wob"
offer that never competes, and an Amazon-fulfilled offer with UNKNOWN
shipping priced between the item's target and (target + the cascade's
terminal-fallback shipping estimate). With `amazon_prime=False` the cascade
estimate is added to the Amazon offer's price, pushing its effective total
above target; with `amazon_prime=True` the Prime rule (D10) forces the same
offer's shipping to free, dropping its effective total to the bare price and
under target. Same observations, same book, same day -- only the config
toggle differs, and it is what flips `TARGET_HIT` on or off.

Two separate engines run the SAME seed data through the SAME pipeline logic,
one per config, so neither run's persisted Alert/BookSignalState rows leak
into the other -- this scenario is about the one-shot comparison, not a
state transition over time (that's scenario_01's job).
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Allow `uv run python tests/scenarios/scenario_08_prime_toggle.py`.
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

# £10.00 Amazon-fulfilled offer, shipping never observed (forces the cascade
# for it). With the terminal default_shipping_minor=280 (£2.80) fallback --
# no prior history for this book/source to build a tighter tier-1/tier-2
# estimate from -- the non-Prime effective total is 1000 + 280 = 1280.
AMAZON_PRICE_MINOR = 1000
# A non-competitive "wob" offer so Amazon always wins current-best
# regardless of which way the toggle goes (1280 or 1000, both well under
# 5280 either way).
WOB_PRICE_MINOR = 5000
# Between the Prime effective total (1000) and the non-Prime one (1280).
TARGET_PRICE_MINOR = 1150


def main() -> int:
    # Freeze "now" so compute_book_stats's windowed slice contains the
    # seeded rows regardless of when this script runs (same reasoning as
    # scenario_01).
    with freeze_time("2026-01-18 12:00:00"):
        return _run_scenario()


def _run_scenario() -> int:
    r = make_recorder("scenario_08_prime_toggle")

    base = datetime(2026, 1, 17, 12, 0, tzinfo=UTC)

    def _run_with_prime(prime: bool) -> str | None:
        engine = fresh_engine()
        session_factory = session_factory_for(engine)
        cfg = Config(
            recommendation=RecommendationConfig(
                # Clear both signal gates with just the two seeded rows —
                # this scenario is about the Prime toggle, not the gates.
                min_days_of_history=1,
                min_observations_for_signal=1,
                amazon_prime=prime,
            ),
            notifications=NotificationsConfig(quiet_hours=None),
        )
        pipeline = AlertPipeline(
            cfg=cfg, session_factory=session_factory, notifiers=[InAppNotifier()],
        )
        with session_for(engine) as s:
            book = make_book(
                s,
                isbn13="9780099490548",
                target_price_minor=TARGET_PRICE_MINOR,
            )
            book_id = book.id
        with session_for(engine) as s:
            # Day 0: the non-competitive third-party offer.
            add_observation(
                s,
                book_id=book_id,
                total_minor=WOB_PRICE_MINOR,
                source="wob",
                observed_at=base,
            )
        with session_for(engine) as s:
            # Day 1: the Amazon-fulfilled offer, shipping unknown.
            add_observation(
                s,
                book_id=book_id,
                total_minor=AMAZON_PRICE_MINOR,
                source="amazon",
                seller="Amazon",
                observed_at=base + timedelta(days=1),
            )
        run_pipeline(pipeline, [book_id])
        with session_for(engine) as s:
            state = s.exec(
                select(models.BookSignalState).where(
                    models.BookSignalState.book_id == book_id
                )
            ).one_or_none()
        return state.last_signal if state else None

    r.step("Same observations, amazon_prime=False -- cascade estimate keeps total above target")
    signal_without_prime = _run_with_prime(False)
    r.step(f"signal (prime=False) = {signal_without_prime}")
    r.expect(
        signal_without_prime != "TARGET_HIT",
        f"non-Prime effective total (1280) is above target (1150) -> not TARGET_HIT "
        f"(got {signal_without_prime})",
    )

    r.step("Same observations, amazon_prime=True -- Prime rule forces free delivery")
    signal_with_prime = _run_with_prime(True)
    r.step(f"signal (prime=True) = {signal_with_prime}")
    r.expect(
        signal_with_prime == "TARGET_HIT",
        f"Prime effective total (1000) is at/below target (1150) -> TARGET_HIT "
        f"(got {signal_with_prime})",
    )

    r.expect(
        signal_without_prime != signal_with_prime,
        f"toggling amazon_prime must flip the signal for identical observations "
        f"(got {signal_without_prime!r} both times)"
        if signal_without_prime == signal_with_prime
        else "signal differs between the two runs, as expected",
    )

    return r.finish()


if __name__ == "__main__":
    sys.exit(main())
