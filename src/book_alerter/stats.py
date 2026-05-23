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


def label_for_days(days: int) -> str | None:
    """Map a `percentile_window_days` value back to its canonical key in
    `WINDOW_DAYS` ("1m"/"3m"/"12m"), or None for a custom (non-canonical)
    window. Used by every consumer that needs `windows[label]`."""
    for label, d in WINDOW_DAYS.items():
        if d == days:
            return label
    return None


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
    # "All-time" within the bounded scan window in `compute_book_stats`
    # (`max(WINDOW_DAYS) ∨ window_days`). Long-running deploys never reach
    # the literal all-time; the `new_low` alert treats this as "lower than
    # any recently-seen total."
    all_time_min_total_minor: int | None
    all_time_max_total_minor: int | None
    observation_count: int
    days_of_history: int
    last_observed_at: datetime | None
    # Max observed_at across ALL rows (including duplicates). Moves on every
    # scrape; `last_observed_at` only moves on a canonical price change.
    last_polled_at: datetime | None = None
    # Window used to derive the percentile distribution for signal logic.
    # The matching key under `windows` (1m/3m/12m) carries the p25/p50/p75 and
    # `rank` for this window — callers use `windows[label_for(percentile_window_days)]`.
    percentile_window_days: int = 90
    # Rank for the configured window, including non-canonical custom values
    # (e.g. 60 days) that don't map to a `windows` key. Mirrors
    # `windows[label_for_days(...)].rank` for canonical windows and is the
    # only way to surface a rank readout for custom ones.
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

    @property
    def item_id(self) -> int:
        """Item-kind-agnostic alias for `book_id`. The dataclass field is
        named `book_id` for historical (books-first) reasons; both books and
        products populate it with the relevant primary key. New callers
        should prefer `item_id` so the product side reads cleanly."""
        return self.book_id


# ---------------------------------------------------------------------------
# Pure helpers (no DB access) — unit-testable in isolation.
# ---------------------------------------------------------------------------


SellerClass = Literal["amazon_fulfilled", "third_party"]


def seller_class(seller: str | None) -> SellerClass:
    """Classify a marketplace seller for shipping-cascade keying.

    `amazon_fulfilled` covers offers shipped by Amazon (Prime-eligible) —
    detected by the seller string starting with "Amazon". Everything else
    (third-party Amazon sellers, WOB, BookFinder, empty/None) is
    `third_party`. The distinction matters for the cascade because
    Amazon-fulfilled offers typically ship free while third-party offers
    add postage, and mixing them in a single source-global median lets a
    Prime-dominant aggregate falsely impute zero shipping onto a
    third-party row.
    """
    if seller and seller.startswith("Amazon"):
        return "amazon_fulfilled"
    return "third_party"


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
    return round((below / len(sorted_totals)) * 100)


def _imputed_shipping(
    source: str | None,
    seller: str | None,
    *,
    book_source_medians: dict[str, int],
    source_seller_global_medians: dict[tuple[str, SellerClass], int],
    book_median: int | None,
    default_shipping: int,
) -> int:
    """Cascade lookup for a row whose own `shipping_minor` is NULL.

    Tiers (most-specific first):
      1. `book_source_medians[source]`   — this book's typical shipping
                                            on this source.
      2. `source_seller_global_medians[(source, seller_class)]` — cross-
         book median for the same (source, fulfilment class). Splits
         Amazon-fulfilled from third-party so a Prime-dominant aggregate
         doesn't impute zero shipping onto a third-party offer.
      3. `book_median`                   — this book's typical shipping
                                            across all sources.
      4. `default_shipping`              — terminal config-driven
                                            estimate; never None.
    """
    if source is not None:
        if source in book_source_medians:
            return book_source_medians[source]
        key = (source, seller_class(seller))
        if key in source_seller_global_medians:
            return source_seller_global_medians[key]
    if book_median is not None:
        return book_median
    return default_shipping


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


@dataclass(frozen=True)
class _ItemSchema:
    """Table + column names that distinguish books from products in the
    stats engine. Allows `_compute_stats_impl` and
    `source_seller_global_shipping_medians` to share one implementation."""

    observation_table: str  # priceobservation / productobservation
    id_column: str          # book_id / product_id
    stats_view: str         # book_stats / product_stats


_BOOK_SCHEMA = _ItemSchema(
    observation_table="priceobservation",
    id_column="book_id",
    stats_view="book_stats",
)
_PRODUCT_SCHEMA = _ItemSchema(
    observation_table="productobservation",
    id_column="product_id",
    stats_view="product_stats",
)


def source_seller_global_shipping_medians(
    session: Session,
    min_observations: int = 10,
    *,
    schema: _ItemSchema = _BOOK_SCHEMA,
) -> dict[tuple[str, SellerClass], int]:
    """Median of observed shipping per (source, seller_class) across every
    item, bounded to the widest configured window. Used as cascade tier 2
    in `_imputed_shipping`. Exposed so callers that invoke `compute_*_stats`
    in a loop (e.g. the dashboard list endpoint) compute it once per
    request rather than per item.

    Pass `schema=_PRODUCT_SCHEMA` for the product side. Default keeps the
    book behaviour for every existing caller — no signature break.

    Buckets with fewer than `min_observations` rows are excluded so
    sparse-sample medians don't pollute the cascade — the caller's
    terminal default fires instead."""
    since = datetime.now(UTC) - timedelta(days=max(WINDOW_DAYS.values()))
    rows = session.exec(
        text(
            f"""
            SELECT source, seller, shipping_minor FROM {schema.observation_table}
            WHERE shipping_minor IS NOT NULL
              AND observed_at >= :since
            """
        ).bindparams(since=since)
    ).all()
    by_key: dict[tuple[str, SellerClass], list[int]] = {}
    for source, seller, shipping in rows:
        by_key.setdefault((source, seller_class(seller)), []).append(int(shipping))
    return {
        k: int(statistics.median(v))
        for k, v in by_key.items()
        if len(v) >= min_observations
    }


# `Stats` is the item-kind-agnostic alias for the dataclass — books and
# products both populate this shape. The `book_id` field stays as-is for
# back-compat; new consumers should think of it as "item id" semantically.
Stats = BookStats


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------


def compute_book_stats(
    book_id: int,
    session: Session,
    window_days: int = 90,
    *,
    source_seller_global_medians: dict[tuple[str, SellerClass], int] | None = None,
    default_shipping_minor: int = 280,
    min_global_median_observations: int = 10,
) -> BookStats:
    """Compute the stats bundle for a single book. See `_compute_stats_impl`
    for the full contract; this is a thin wrapper that fixes the schema to
    the book tables."""
    return _compute_stats_impl(
        book_id,
        session,
        window_days,
        schema=_BOOK_SCHEMA,
        source_seller_global_medians=source_seller_global_medians,
        default_shipping_minor=default_shipping_minor,
        min_global_median_observations=min_global_median_observations,
    )


def compute_product_stats(
    product_id: int,
    session: Session,
    window_days: int = 90,
    *,
    source_seller_global_medians: dict[tuple[str, SellerClass], int] | None = None,
    default_shipping_minor: int = 280,
    min_global_median_observations: int = 10,
) -> BookStats:
    """Compute the stats bundle for a single product. Returns `BookStats`
    (the dataclass shape is item-kind-agnostic — the field `book_id` is
    reused for the product id; see plan doc for the deliberate naming
    debt). Mirrors `compute_book_stats` exactly except for the schema."""
    return _compute_stats_impl(
        product_id,
        session,
        window_days,
        schema=_PRODUCT_SCHEMA,
        source_seller_global_medians=source_seller_global_medians,
        default_shipping_minor=default_shipping_minor,
        min_global_median_observations=min_global_median_observations,
    )


def _compute_stats_impl(
    item_id: int,
    session: Session,
    window_days: int,
    *,
    schema: _ItemSchema,
    source_seller_global_medians: dict[tuple[str, SellerClass], int] | None,
    default_shipping_minor: int,
    min_global_median_observations: int,
) -> BookStats:
    """Schema-parameterised stats computation. Two table groups (book vs
    product) share this implementation by passing distinct `_ItemSchema`
    instances.

    `source_seller_global_medians` is a cascade-step-2 input. Callers that
    invoke this in a loop (e.g. the dashboard list endpoint) compute it
    once via `source_seller_global_shipping_medians(session, schema=...)`
    and pass it in, so we don't scan the whole observation table per item.
    `default_shipping_minor` is the cascade's terminal fallback when no
    tier produces an estimate. `min_global_median_observations` gates the
    (source, seller_class) tier so sparse buckets don't fire.
    """
    head = session.exec(
        text(
            f"""
            SELECT current_best_total_minor, current_best_price_minor,
                   current_best_shipping_minor, current_best_source,
                   current_best_condition, current_best_seller, current_best_url,
                   observation_count, last_observed_at, days_of_history,
                   last_polled_at
            FROM {schema.stats_view} WHERE {schema.id_column} = :iid
            """
        ).bindparams(iid=item_id)
    ).one_or_none()

    if head is None:
        return BookStats(
            book_id=item_id,
            current_best_total_minor=None,
            current_best_price_minor=None,
            current_best_shipping_minor=None,
            current_best_source=None,
            current_best_seller=None,
            current_best_condition=None,
            current_best_url=None,
            all_time_min_total_minor=None,
            all_time_max_total_minor=None,
            observation_count=0,
            days_of_history=0,
            last_observed_at=None,
            percentile_window_days=window_days,
            windows={k: WindowStats() for k in WINDOW_DAYS},
        )

    # Bound the imputation/percentile scan to the widest window we'll use so
    # per-item work stays O(window) not O(history). `all_time_min/max` below
    # therefore mean "min/max within this window" — for long-running deploys
    # that's the more useful signal anyway, and the `new_low` alert reads it
    # as "lower than recently seen".
    max_window_days = max(max(WINDOW_DAYS.values()), window_days)
    since = datetime.now(UTC) - timedelta(days=max_window_days)
    raw = session.exec(
        text(
            f"""
            SELECT observed_at, source, seller, price_minor, shipping_minor, total_minor
            FROM {schema.observation_table}
            WHERE {schema.id_column} = :iid
              AND is_duplicate_of IS NULL
              AND observed_at >= :since
            """
        ).bindparams(iid=item_id, since=since)
    ).all()

    # Shipping medians window-bounded to match the global query. Dupes
    # included on purpose — dupes repeat the canonical shipping signal,
    # and on slow-moving items they're the bulk of the sample.
    shipping_rows = session.exec(
        text(
            f"""
            SELECT source, shipping_minor FROM {schema.observation_table}
            WHERE {schema.id_column} = :iid
              AND shipping_minor IS NOT NULL
              AND observed_at >= :since
            """
        ).bindparams(iid=item_id, since=since)
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
    if source_seller_global_medians is None:
        source_seller_global_medians = source_seller_global_shipping_medians(
            session,
            min_observations=min_global_median_observations,
            schema=schema,
        )

    cascade_kwargs = dict(
        book_source_medians=book_source_medians,
        source_seller_global_medians=source_seller_global_medians,
        book_median=book_median,
        default_shipping=default_shipping_minor,
    )

    imputed: list[tuple[datetime, int]] = []
    for observed_at, source, seller, price, shipping, total in raw:
        if shipping is not None and total is not None:
            imputed.append((_to_aware(observed_at), int(total)))
            continue
        if price is None:
            continue
        imp = _imputed_shipping(source, seller, **cascade_kwargs)
        imputed.append((_to_aware(observed_at), int(price) + imp))

    # Current row uses the same cascade. The view picks `current_best` from
    # live offers, so `current_shipping` may be NULL for a live row that
    # failed to extract postage — cascade fills it.
    (
        current_total, current_price, current_shipping,
        current_source, _current_condition, current_seller,
    ) = head[:6]
    effective: int | None
    shipping_estimate: int | None = None
    if current_total is None:
        effective = None
    elif current_shipping is not None:
        effective = int(current_total)
    else:
        # `price_minor` is non-nullable in the model, so when the view's
        # `current_best` row exists (current_total is not None), current_price
        # is guaranteed set. Assert the invariant so a schema regression
        # surfaces here rather than silently producing 0+imp.
        assert current_price is not None
        imp = _imputed_shipping(current_source, current_seller, **cascade_kwargs)
        effective = int(current_price) + imp
        shipping_estimate = imp

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

    cfg_label = label_for_days(window_days)
    if cfg_label is not None:
        cfg_totals = totals_by_label[cfg_label]
        cfg_rank = windows[cfg_label].rank
    else:
        # Custom window not in 1m/3m/12m. `sorted_totals` backs
        # `percentile_at()` for signal logic; we also compute a standalone
        # rank so the FE has something to render for non-canonical windows.
        cfg_totals = _slice_sorted_totals(window_days)
        cfg_rank = (
            _percentile_rank(cfg_totals, effective)
            if cfg_totals and effective is not None
            else None
        )

    return BookStats(
        book_id=item_id,
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
        percentile_window_days=window_days,
        current_percentile_rank=cfg_rank,
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
    item: models.Book | models.Product,
    stats: BookStats,
    cfg: RecommendationConfig,
) -> Signal:
    """Compute the BUY/WATCH/WAIT signal for a tracked item. Item-agnostic —
    reads only `percentile_threshold` and `target_price_minor` from the
    item, both shared between Book and Product."""
    if stats.days_of_history < cfg.min_days_of_history:
        return "INSUFFICIENT_DATA"
    if stats.observation_count < cfg.min_observations_for_signal:
        return "INSUFFICIENT_DATA"
    if stats.current_best_total_minor is None:
        return "INSUFFICIENT_DATA"

    threshold_pct = item.percentile_threshold or cfg.buy_percentile

    if item.target_price_minor is not None:
        tolerance = int(item.target_price_minor * (1 + cfg.target_tolerance_pct / 100))
        if stats.current_best_total_minor <= item.target_price_minor:
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
