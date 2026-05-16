"""Per-book statistics helpers.

`compute_book_stats(book_id, session, window_days)` reads the deterministic
fields from the `book_stats` SQL view, then re-derives the distribution-
shaped fields in Python after running the shipping-imputation cascade
over the canonical history. The cascade needs three median lookups
(per-(book,source), per-source-global, per-book) that are awkward in SQL,
and a single Python pass feeds every consumer (windowed percentiles,
configured-window distribution for the signal, all-time min/max for
`new_low`).

Shipping cascade (applied in `_imputed_shipping`):
  1. Row's own observed `shipping_minor`           → use as-is.
  2. Per-(book, source) median of observed shipping → `price + median`.
  3. Per-source global median across all books     → `price + median`.
  4. Per-book median across all sources            → `price + median`.
  5. None of the above                             → drop the row.

Keepa rows always fall through to step 4 because Keepa never carries
shipping; non-Keepa rows with one-off NULL shipping prefer the source-
aware estimates first.
"""

from __future__ import annotations

import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlmodel import Session

from book_alerter.config import RecommendationConfig
from book_alerter.db import models


Signal = Literal["BUY", "WATCH", "WAIT", "TARGET_HIT", "INSUFFICIENT_DATA"]


# Window labels exposed to the API and consumed by the dashboard mini-bars
# and the detail-page box-plot. Keep order: shortest → longest.
WINDOW_DAYS: dict[str, int] = {
    "1m": 30,
    "3m": 90,
    "12m": 365,
}


@dataclass
class WindowStats:
    """Distribution summary for a time window, computed over imputed totals.

    `rank` is the percentile (0..100) the current effective total occupies
    within this window's distribution; `None` if there's no current or no
    distribution to rank against.
    """
    count: int = 0
    rank: int | None = None
    p5: int | None = None
    p25: int | None = None
    p50: int | None = None
    p75: int | None = None
    p95: int | None = None


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
    # Window used to derive the percentile distribution for signal logic.
    percentile_window_days: int = 90
    # Where the (estimated) current total sits in the windowed distribution,
    # as a 0–100 rank. `None` when there is no usable distribution or the
    # current price's shipping can't be imputed by the cascade.
    current_percentile_rank: int | None = None
    # `current_best_total_minor` adjusted by the cascade-imputed shipping
    # when the current row had no shipping signal. Used for apples-to-apples
    # percentile comparison; `current_best_total_minor` is the raw display
    # value.
    current_effective_total_minor: int | None = None
    # Shipping estimate used to impute the CURRENT row (if its shipping was
    # null). This is whatever the cascade picked: per-(book,source) median,
    # source-global median, or per-book median. `None` when current shipping
    # was observed or no estimate was available. Used by the UI to caption
    # the imputation.
    shipping_estimate_minor: int | None = None
    # Sorted, shipping-adjusted totals over the configured window — drives
    # `percentile_at()` for signal threshold comparisons.
    sorted_totals: list[int] = field(default_factory=list)
    # Per-window distribution summaries (1m / 3m / 12m). Empty windows are
    # returned with count=0 and all-None percentiles.
    windows: dict[str, WindowStats] = field(default_factory=dict)

    def percentile_at(self, pct: int) -> int | None:
        if not self.sorted_totals or not (1 <= pct <= 99):
            return None
        return _percentile_at_sorted(self.sorted_totals, pct)


# ---------------------------------------------------------------------------
# Pure helpers (no DB access) — unit-testable in isolation.
# ---------------------------------------------------------------------------


def _percentile_at_sorted(sorted_totals: list[int], pct: int | float) -> int:
    """Linear-interpolation percentile lookup on a pre-sorted list. Returns
    int; caller must ensure `sorted_totals` is non-empty."""
    n = len(sorted_totals)
    if n == 1:
        return sorted_totals[0]
    idx = pct / 100 * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return int(
        sorted_totals[lo]
        + (sorted_totals[hi] - sorted_totals[lo]) * frac
    )


def _percentile_rank(sorted_totals: list[int], value: int) -> int:
    """Percentage of `sorted_totals` <= `value` (0..100)."""
    if not sorted_totals:
        return 0
    below = bisect_right(sorted_totals, value)
    return int(round((below / len(sorted_totals)) * 100))


def _imputed_shipping(
    source: str | None,
    *,
    book_source_medians: dict[str, int],
    source_global_medians: dict[str, int],
    book_median: int | None,
) -> int | None:
    """Cascade lookup for a row whose own `shipping_minor` is NULL.
    Returns the shipping value to add to `price_minor`, or `None` if the
    cascade exhausted without a usable estimate. `source=None` short-
    circuits past the source-specific tiers to the per-book median."""
    if source is not None:
        if source in book_source_medians:
            return book_source_medians[source]
        if source in source_global_medians:
            return source_global_medians[source]
    return book_median


def _window_stats_from_sorted(
    sorted_totals: list[int],
    current_effective: int | None,
) -> WindowStats:
    n = len(sorted_totals)
    if n == 0:
        return WindowStats(count=0)
    rank = (
        _percentile_rank(sorted_totals, current_effective)
        if current_effective is not None
        else None
    )
    return WindowStats(
        count=n,
        rank=rank,
        p5=_percentile_at_sorted(sorted_totals, 5),
        p25=_percentile_at_sorted(sorted_totals, 25),
        p50=_percentile_at_sorted(sorted_totals, 50),
        p75=_percentile_at_sorted(sorted_totals, 75),
        p95=_percentile_at_sorted(sorted_totals, 95),
    )


# ---------------------------------------------------------------------------
# DB helpers — small, isolated queries that feed the cascade.
# ---------------------------------------------------------------------------


def source_global_shipping_medians(session: Session) -> dict[str, int]:
    """Per-source median of observed shipping across every book. Used as
    cascade step 3. Exposed so callers that invoke `compute_book_stats` in
    a loop can compute it once per request rather than per book."""
    rows = session.exec(
        text(
            """
            SELECT source, shipping_minor FROM priceobservation
            WHERE shipping_minor IS NOT NULL
            """
        )
    ).all()
    by_source: dict[str, list[int]] = {}
    for source, shipping in rows:
        by_source.setdefault(source, []).append(int(shipping))
    return {s: int(statistics.median(v)) for s, v in by_source.items()}


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------


def compute_book_stats(
    book_id: int,
    session: Session,
    window_days: int = 90,
    *,
    source_global_medians: dict[str, int] | None = None,
) -> BookStats:
    """Compute the stats bundle for a single book.

    `source_global_medians` is a cascade-step-3 input. Callers that invoke
    this in a loop (e.g. the dashboard list endpoint) should compute it
    once via `source_global_shipping_medians(session)` and pass it in, so
    we don't scan the whole `priceobservation` table per book.
    """
    head = session.exec(
        text(
            """
            SELECT current_best_total_minor, current_best_price_minor,
                   current_best_shipping_minor, current_best_source,
                   current_best_condition, current_best_seller, current_best_url,
                   observation_count, last_observed_at, days_of_history,
                   last_polled_at
            FROM book_stats WHERE book_id = :bid
            """
        ).bindparams(bid=book_id)
    ).one_or_none()

    if head is None:
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
            windows={k: WindowStats() for k in WINDOW_DAYS},
        )

    raw = session.exec(
        text(
            """
            SELECT observed_at, source, price_minor, shipping_minor, total_minor
            FROM priceobservation
            WHERE book_id = :bid
              AND is_duplicate_of IS NULL
            """
        ).bindparams(bid=book_id)
    ).all()

    # Shipping medians include duplicate rows on purpose: dupes repeat the
    # canonical shipping signal, and on slow-moving books they're the bulk
    # of the sample. Matches `source_global_shipping_medians`.
    shipping_rows = session.exec(
        text(
            """
            SELECT source, shipping_minor FROM priceobservation
            WHERE book_id = :bid AND shipping_minor IS NOT NULL
            """
        ).bindparams(bid=book_id)
    ).all()
    by_book_source: dict[str, list[int]] = {}
    all_book_shipping: list[int] = []
    for source, shipping in shipping_rows:
        by_book_source.setdefault(source, []).append(int(shipping))
        all_book_shipping.append(int(shipping))
    book_source_medians: dict[str, int] = {
        s: int(statistics.median(v)) for s, v in by_book_source.items()
    }
    book_median: int | None = (
        int(statistics.median(all_book_shipping)) if all_book_shipping else None
    )
    if source_global_medians is None:
        source_global_medians = source_global_shipping_medians(session)

    cascade_kwargs = dict(
        book_source_medians=book_source_medians,
        source_global_medians=source_global_medians,
        book_median=book_median,
    )

    imputed: list[tuple[datetime, int]] = []
    for observed_at, source, price, shipping, total in raw:
        if shipping is not None and total is not None:
            imputed.append((_to_aware(observed_at), int(total)))
            continue
        imp = _imputed_shipping(source, **cascade_kwargs)
        if imp is None or price is None:
            continue
        imputed.append((_to_aware(observed_at), int(price) + imp))

    # Current row uses the same cascade. The view picks `current_best` from
    # live offers, so `current_shipping` may be NULL for a live row that
    # failed to extract postage — cascade fills it.
    current_total, current_price, current_shipping, current_source = head[:4]
    effective: int | None
    shipping_estimate: int | None = None
    if current_total is None:
        effective = None
    elif current_shipping is not None:
        effective = int(current_total)
    else:
        imp = _imputed_shipping(current_source, **cascade_kwargs)
        if imp is not None and current_price is not None:
            effective = int(current_price) + imp
            shipping_estimate = imp
        else:
            effective = None

    # Sort by ts ascending once so each window resolves to a tail slice
    # via bisect, and the all-time bounds fold in alongside.
    imputed.sort(key=lambda r: r[0])
    ts_list = [r[0] for r in imputed]
    now = datetime.now(UTC)

    def _slice_sorted_totals(days: int) -> list[int]:
        lo = bisect_left(ts_list, now - timedelta(days=days))
        return sorted(t for _ts, t in imputed[lo:])

    totals_by_label: dict[str, list[int]] = {
        label: _slice_sorted_totals(days) for label, days in WINDOW_DAYS.items()
    }
    windows = {
        label: _window_stats_from_sorted(totals, effective)
        for label, totals in totals_by_label.items()
    }

    if imputed:
        totals_only = [t for _ts, t in imputed]
        all_time_min = min(totals_only)
        all_time_max = max(totals_only)
    else:
        all_time_min = all_time_max = None

    cfg_label = next(
        (k for k, d in WINDOW_DAYS.items() if d == window_days), None
    )
    if cfg_label is not None:
        cfg_totals = totals_by_label[cfg_label]
        cfg_window = windows[cfg_label]
    else:
        cfg_totals = _slice_sorted_totals(window_days)
        cfg_window = _window_stats_from_sorted(cfg_totals, effective)

    return BookStats(
        book_id=book_id,
        current_best_total_minor=head[0],
        current_best_price_minor=head[1],
        current_best_shipping_minor=head[2],
        current_best_source=head[3],
        current_best_condition=head[4],
        current_best_seller=head[5],
        current_best_url=head[6],
        all_time_min_total_minor=all_time_min,
        all_time_max_total_minor=all_time_max,
        observation_count=head[7] or 0,
        last_observed_at=head[8],
        days_of_history=head[9] or 0,
        last_polled_at=head[10],
        p25_total_minor=cfg_window.p25,
        p50_total_minor=cfg_window.p50,
        p75_total_minor=cfg_window.p75,
        percentile_window_days=window_days,
        current_percentile_rank=cfg_window.rank,
        current_effective_total_minor=effective,
        shipping_estimate_minor=shipping_estimate,
        sorted_totals=cfg_totals,
        windows=windows,
    )


def _to_aware(ts: datetime | str) -> datetime:
    """Coerce SQLite TEXT or naive datetimes to UTC-aware so timedelta
    comparisons against `datetime.now(UTC)` work."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


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
