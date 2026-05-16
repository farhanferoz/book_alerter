"""Per-book statistics helpers.

`compute_book_stats(book_id, session, window_days)` reads deterministic
fields from the `book_stats` SQL view and derives the percentile-input
distribution from non-duplicate `PriceObservation` rows in the trailing
window.

Shipping handling: rows with `shipping_minor IS NULL` (Keepa historical
rows in practice) are folded into the distribution using the per-book
median of *observed* shipping (any source, any time). If we have zero
observed shipping for the book, NULL rows are excluded — no fabricated
default kicks in.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
    # Max observed_at across ALL rows (including duplicates). Moves on every
    # scrape; `last_observed_at` only moves on a canonical price change.
    last_polled_at: datetime | None = None
    # Window used to derive the percentile distribution.
    percentile_window_days: int = 90
    # Where the (estimated) current total sits in the windowed distribution,
    # as a 0–100 rank. `None` when there is no usable distribution or the
    # current price's shipping is unknown and we have no per-book shipping
    # observations to estimate from.
    current_percentile_rank: int | None = None
    # `current_best_total_minor` adjusted by `shipping_estimate_minor` when
    # the current row had no shipping signal. Used for apples-to-apples
    # percentile comparison; `current_best_total_minor` is the raw display
    # value.
    current_effective_total_minor: int | None = None
    # Median shipping observed for this book, used to fold NULL-shipping
    # rows into the distribution. `None` means no observations to median.
    shipping_estimate_minor: int | None = None
    # Sorted, shipping-adjusted totals for percentile_at() lookups.
    sorted_totals: list[int] = field(default_factory=list)

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
    p25, p50, p75 = qs
    return int(p25), int(p50), int(p75)


def _per_book_shipping_median(session: Session, book_id: int) -> int | None:
    """Median of observed shipping_minor for the book across all sources,
    all time. `None` if no row carries a known shipping value yet.

    Includes `is_duplicate_of NOT NULL` rows — dedup is keyed on exact
    (price, shipping) match, so duplicate rows carry the same shipping
    value as their canonical parent and are real signal, not noise.
    Filtering them out drops ~85% of the shipping observations on stable
    books and skews the median toward the oldest representative.
    """
    rows = session.exec(
        text(
            """
            SELECT shipping_minor FROM priceobservation
            WHERE book_id = :bid
              AND shipping_minor IS NOT NULL
            """
        ).bindparams(bid=book_id)
    ).all()
    if not rows:
        return None
    return int(statistics.median(int(r[0]) for r in rows))


def _percentile_rank(sorted_totals: list[int], value: int) -> int:
    """Percentage of `sorted_totals` <= `value` (0..100)."""
    if not sorted_totals:
        return 0
    below = sum(1 for t in sorted_totals if t <= value)
    return int(round((below / len(sorted_totals)) * 100))


def compute_book_stats(
    book_id: int,
    session: Session,
    window_days: int = 90,
) -> BookStats:
    row = session.exec(
        text(
            """
            SELECT current_best_total_minor, current_best_price_minor,
                   current_best_shipping_minor, current_best_source,
                   current_best_condition, current_best_seller, current_best_url,
                   all_time_min_total_minor, all_time_max_total_minor,
                   observation_count, last_observed_at, days_of_history,
                   last_polled_at
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
            percentile_window_days=window_days,
        )

    shipping_estimate = _per_book_shipping_median(session, book_id)
    window_start = datetime.now(UTC) - timedelta(days=window_days)

    raw_obs = session.exec(
        text(
            """
            SELECT price_minor, shipping_minor, total_minor
            FROM priceobservation
            WHERE book_id = :bid
              AND is_duplicate_of IS NULL
              AND observed_at >= :win
            """
        ).bindparams(bid=book_id, win=window_start)
    ).all()

    windowed_totals: list[int] = []
    for price_minor, shipping_minor, total_minor in raw_obs:
        if shipping_minor is not None:
            windowed_totals.append(int(total_minor))
        elif shipping_estimate is not None:
            windowed_totals.append(int(price_minor) + shipping_estimate)
        # else: drop the row — no observed shipping to estimate from.

    sorted_totals = sorted(windowed_totals)
    p25, p50, p75 = _percentiles(windowed_totals)

    current_total = row[0]
    current_shipping = row[2]
    current_price = row[1]
    effective: int | None
    if current_total is None:
        effective = None
    elif current_shipping is not None:
        effective = int(current_total)
    elif shipping_estimate is not None and current_price is not None:
        effective = int(current_price) + shipping_estimate
    else:
        effective = None  # shipping unknown both ways → no comparable basis
    rank: int | None = None
    if sorted_totals and effective is not None:
        rank = _percentile_rank(sorted_totals, effective)

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
        last_polled_at=row[12],
        p25_total_minor=p25,
        p50_total_minor=p50,
        p75_total_minor=p75,
        percentile_window_days=window_days,
        current_percentile_rank=rank,
        current_effective_total_minor=effective,
        shipping_estimate_minor=shipping_estimate,
        sorted_totals=sorted_totals,
    )


def compute_signal(
    book: models.Book, stats: BookStats, cfg: RecommendationConfig
) -> Signal:
    if stats.days_of_history < cfg.min_days_of_history:
        return "INSUFFICIENT_DATA"
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

    # Compare the shipping-adjusted current total against the percentile cut
    # of the (windowed, shipping-merged) distribution. `current_effective_
    # total_minor` is None only when both the current row and the book's
    # history lack any shipping signal we could estimate from.
    effective = stats.current_effective_total_minor
    p_field = stats.percentile_at(threshold_pct)
    if effective is None or p_field is None:
        return "INSUFFICIENT_DATA"
    if effective <= p_field:
        return "BUY"
    watch_cut = stats.percentile_at(cfg.watch_percentile)
    if watch_cut is not None and effective <= watch_cut:
        return "WATCH"
    return "WAIT"
