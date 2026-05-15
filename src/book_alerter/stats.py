"""Per-book statistics helpers.

`compute_book_stats(book_id, session)` reads deterministic fields from the
`book_stats` SQL view (see migration 0004) and derives percentile inputs by
querying non-duplicate `PriceObservation` rows directly.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlmodel import Session

from book_alerter.config import RecommendationConfig
from book_alerter.db import models


Signal = Literal["BUY", "WATCH", "WAIT", "TARGET_HIT", "INSUFFICIENT_DATA"]


@dataclass
class BookStats:
    book_id: int
    current_best_total_minor: int | None
    current_best_price_minor: int | None
    current_best_shipping_minor: int | None
    current_best_source: str | None
    current_best_seller: str | None
    current_best_condition: str | None
    current_best_url: str | None
    p25_total_minor: int | None
    p50_total_minor: int | None
    p75_total_minor: int | None
    all_time_min_total_minor: int | None
    all_time_max_total_minor: int | None
    observation_count: int
    days_of_history: int
    last_observed_at: datetime | None
    sorted_totals: list[int] = field(default_factory=list)  # for arbitrary-percentile queries

    def percentile_at(self, pct: int) -> int | None:
        if not self.sorted_totals or not (1 <= pct <= 99):
            return None
        n = len(self.sorted_totals)
        if n == 1:
            return self.sorted_totals[0]
        idx = pct / 100 * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        frac = idx - lo
        return int(
            self.sorted_totals[lo]
            + (self.sorted_totals[hi] - self.sorted_totals[lo]) * frac
        )


def _percentiles(values: list[int]) -> tuple[int, int, int] | tuple[None, None, None]:
    if not values:
        return None, None, None
    if len(values) == 1:
        return values[0], values[0], values[0]
    qs = statistics.quantiles(sorted(values), n=4, method="inclusive")
    p25, p50, p75 = qs  # 3 cut points for n=4
    return int(p25), int(p50), int(p75)


def compute_book_stats(book_id: int, session: Session) -> BookStats:
    # Pull deterministic fields from the view.
    row = session.exec(
        text(
            """
            SELECT current_best_total_minor, current_best_price_minor,
                   current_best_shipping_minor, current_best_source,
                   current_best_condition, current_best_seller, current_best_url,
                   all_time_min_total_minor, all_time_max_total_minor,
                   observation_count, last_observed_at, days_of_history
            FROM book_stats WHERE book_id = :bid
            """
        ).bindparams(bid=book_id)
    ).one_or_none()

    if row is None:
        return BookStats(
            book_id=book_id,
            current_best_total_minor=None,
            current_best_price_minor=None,
            current_best_shipping_minor=None,
            current_best_source=None,
            current_best_seller=None,
            current_best_condition=None,
            current_best_url=None,
            p25_total_minor=None,
            p50_total_minor=None,
            p75_total_minor=None,
            all_time_min_total_minor=None,
            all_time_max_total_minor=None,
            observation_count=0,
            days_of_history=0,
            last_observed_at=None,
        )

    # Pull totals for percentiles (exclude duplicates).
    totals = [
        r[0]
        for r in session.exec(
            text(
                "SELECT total_minor FROM priceobservation "
                "WHERE book_id = :bid AND is_duplicate_of IS NULL"
            ).bindparams(bid=book_id)
        ).all()
    ]
    p25, p50, p75 = _percentiles(totals)

    return BookStats(
        book_id=book_id,
        current_best_total_minor=row[0],
        current_best_price_minor=row[1],
        current_best_shipping_minor=row[2],
        current_best_source=row[3],
        current_best_condition=row[4],
        current_best_seller=row[5],
        current_best_url=row[6],
        all_time_min_total_minor=row[7],
        all_time_max_total_minor=row[8],
        observation_count=row[9] or 0,
        last_observed_at=row[10],
        days_of_history=row[11] or 0,
        p25_total_minor=p25,
        p50_total_minor=p50,
        p75_total_minor=p75,
        sorted_totals=sorted(totals),
    )


def compute_signal(
    book: models.Book, stats: BookStats, cfg: RecommendationConfig
) -> Signal:
    # Primary gate: calendar-day spread of observations. Without temporal
    # spread the percentile distribution is just "today's prices sorted" —
    # comparing the current best against that yields a tautology, not a
    # recommendation. See RecommendationConfig.min_days_of_history.
    if stats.days_of_history < cfg.min_days_of_history:
        return "INSUFFICIENT_DATA"
    # Secondary (legacy) gate. Default 1 so it's effectively no-op once
    # the days gate is satisfied; preserved so existing configs that set
    # a higher value still gate as the user expects.
    if stats.observation_count < cfg.min_observations_for_signal:
        return "INSUFFICIENT_DATA"
    if stats.current_best_total_minor is None:
        return "INSUFFICIENT_DATA"

    threshold_pct = book.percentile_threshold or cfg.buy_percentile

    if book.target_price_minor is not None:
        tolerance = int(book.target_price_minor * (1 + cfg.target_tolerance_pct / 100))
        if stats.current_best_total_minor <= book.target_price_minor:
            return "TARGET_HIT"
        if stats.current_best_total_minor <= tolerance:
            return "BUY"
        # fall through to percentile evaluation

    # threshold_pct is any integer percentile in 1..99; we compute it from the
    # sorted-totals list carried inside BookStats so any value works.
    p_field = stats.percentile_at(threshold_pct)
    if p_field is None:
        return "INSUFFICIENT_DATA"
    if stats.current_best_total_minor <= p_field:
        return "BUY"
    if (
        stats.p50_total_minor is not None
        and stats.current_best_total_minor <= stats.p50_total_minor
    ):
        return "WATCH"
    return "WAIT"
