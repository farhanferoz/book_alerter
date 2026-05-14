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


Signal = Literal["BUY", "WATCH", "WAIT", "TARGET_HIT", "INSUFFICIENT_DATA"]


@dataclass
class BookStats:
    book_id: int
    current_best_total_minor: int | None
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
            SELECT current_best_total_minor, current_best_source, current_best_condition,
                   current_best_seller, current_best_url,
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
        current_best_source=row[1],
        current_best_condition=row[2],
        current_best_seller=row[3],
        current_best_url=row[4],
        all_time_min_total_minor=row[5],
        all_time_max_total_minor=row[6],
        observation_count=row[7] or 0,
        last_observed_at=row[8],
        days_of_history=row[9] or 0,
        p25_total_minor=p25,
        p50_total_minor=p50,
        p75_total_minor=p75,
        sorted_totals=sorted(totals),
    )
