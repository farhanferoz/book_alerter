"""Per-book statistics helpers.

`compute_stats_for_items(item_ids, session, ...)` is the engine: it loads
candidates + windowed observation history + full-history summaries for every
requested item in three batched SQL queries (regardless of how many items are
requested — see its docstring), then does current-best selection and the
distribution-shaped fields in Python after running the shipping-imputation
cascade over the canonical history. `compute_book_stats` / `compute_product_stats`
are thin single-item wrappers over it, kept for callers (the per-item detail
endpoints, the alert dispatcher) that only ever want one item's stats.

Shipping cascade (applied in `_imputed_shipping`):
  1. Row's own observed `shipping_minor`           → use as-is.
  2. Per-(book, source) median of observed shipping → `price + median`.
  3. Per-source global median across all books     → `price + median`.
  4. Per-book median across all sources            → `price + median`.
  5. None of the above                             → drop the row.

Keepa rows always fall through to step 4 because Keepa never carries
shipping; non-Keepa rows with one-off NULL shipping prefer the source-
aware estimates first.

`effective_shipping` is the single seam every consumer of a shipping figure
goes through (current-best ranking here; window/percentile totals here;
the alert message once T2.2 lands). See its docstring for the Prime seam.
"""

from __future__ import annotations

import statistics
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Literal

from sqlalchemy import bindparam, text
from sqlmodel import Session

from book_alerter.config import RecommendationConfig
from book_alerter.db import models
from book_alerter.enums import Signal

# Valid range for a percentile lookup — 0 and 100 are meaningless as a rank
# cut (there's no value strictly below the minimum or above the maximum).
_MIN_PERCENTILE = 1
_MAX_PERCENTILE = 99


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
        if not self.sorted_totals or not (_MIN_PERCENTILE <= pct <= _MAX_PERCENTILE):
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
    """Table + view names that distinguish books from products in the stats
    engine. Allows `compute_stats_for_items` and
    `source_seller_global_shipping_medians` to share one implementation."""

    observation_table: str    # priceobservation / productobservation
    id_column: str            # book_id / product_id
    live_offers_view: str     # book_live_offers / product_live_offers
    history_summary_view: str  # book_history_summary / product_history_summary


_BOOK_SCHEMA = _ItemSchema(
    observation_table="priceobservation",
    id_column="book_id",
    live_offers_view="book_live_offers",
    history_summary_view="book_history_summary",
)
_PRODUCT_SCHEMA = _ItemSchema(
    observation_table="productobservation",
    id_column="product_id",
    live_offers_view="product_live_offers",
    history_summary_view="product_history_summary",
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
    # `session.connection().execute(...)` (Core) rather than `session.exec(...)`
    # (ORM) — this scans the observation table almost in full on a young
    # deployment (little gets filtered by `since`), and ORM Session.exec's
    # autoflush check + entity-shaping is pure overhead for a raw-tuple
    # SELECT with no pending ORM writes on this read-only path. Measured
    # ~30% faster on a production copy (90k rows). Every caller of this
    # function commits before calling it, so skipping autoflush is safe.
    rows = session.connection().execute(
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


_MEDIANS_CACHE_TTL_SECONDS = 60


@dataclass
class _MediansCacheEntry:
    medians: dict[tuple[str, SellerClass], int]
    computed_at: float  # time.monotonic()


class MediansCache:
    """Per-app-instance cache for `source_seller_global_shipping_medians`,
    keyed by schema (book vs product). T3.4 (plan 2026-09-04): without it,
    every dashboard render re-scans the whole observation table for this
    cross-item tier of the shipping cascade; a 60s TTL removes that scan
    from all but one render per minute.

    `app.py`'s `_build_runtime` attaches one instance to
    `app.state.medians_cache`, rebuilt fresh on every config reload (same
    lifecycle as the scheduler) — so a `min_observations` change (from a
    config edit) always starts a clean cache rather than serving a stale
    value computed under the old threshold; entries are keyed on schema
    alone because `min_observations` is otherwise constant for the life of
    one cache instance. `scheduler.Scheduler` invalidates it after
    persisting new observations so a fresh scrape's shipping data doesn't
    wait out the TTL.
    """

    def __init__(self, *, ttl_seconds: float = _MEDIANS_CACHE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, _MediansCacheEntry] = {}

    def get_or_compute(
        self,
        session: Session,
        *,
        schema: _ItemSchema,
        min_observations: int,
    ) -> dict[tuple[str, SellerClass], int]:
        entry = self._entries.get(schema.observation_table)
        now = time.monotonic()
        if entry is not None and (now - entry.computed_at) < self._ttl_seconds:
            return entry.medians
        medians = source_seller_global_shipping_medians(
            session, min_observations=min_observations, schema=schema,
        )
        self._entries[schema.observation_table] = _MediansCacheEntry(
            medians=medians, computed_at=now,
        )
        return medians

    def invalidate(self) -> None:
        self._entries.clear()


# `Stats` is the item-kind-agnostic alias for the dataclass — books and
# products both populate this shape. The `book_id` field stays as-is for
# back-compat; new consumers should think of it as "item id" semantically.
Stats = BookStats


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------


def effective_shipping(
    source: str | None,
    seller: str | None,
    shipping_minor: int | None,
    *,
    prime: bool = False,
    cascade: Callable[[str | None, str | None], int],
) -> tuple[int, bool]:
    """Single seam for the shipping figure used everywhere a total is
    computed: current-best ranking and window/percentile totals here, the
    alert message once T2.2's notifier change lands. `cascade(source,
    seller) -> int` runs the `_imputed_shipping` tier chain for a row whose
    own shipping is unknown (see module docstring).

    Returns `(pence, is_estimate)`.

    T2.2 (plan task, not yet implemented — read it before adding branches
    here) adds the Prime rule as the FIRST check: if `prime` and
    `seller_class(seller) == "amazon_fulfilled"` and `source` is an Amazon
    source, treat delivery as free — `(0, False)`. Until then `prime` is
    accepted (so the seam's signature is already what T2.2 needs) but has
    no effect: behaviour is unconditionally "observed shipping when known,
    else the cascade estimate", which is today's semantics.
    """
    if shipping_minor is not None:
        return int(shipping_minor), False
    return cascade(source, seller), True


def compute_stats_for_items(
    item_ids: Sequence[int],
    session: Session,
    *,
    schema: _ItemSchema,
    cfg: RecommendationConfig,
    window_days: Mapping[int, int],
    prime: bool = False,
    medians: dict[tuple[str, SellerClass], int] | None = None,
) -> dict[int, BookStats]:
    """Stats bundle for every id in `item_ids`, loaded in three queries
    total regardless of how many items are requested:

    1. Live-offer candidates (`{schema.live_offers_view}`) — one row per
       live offer, already freshness-gated in SQL exactly as the old
       `book_stats`/`product_stats` views were (see `db/views.py`).
    2. Window observations (`{schema.observation_table}`, `observed_at >=
       now - max_window`) — one row per distinct offer (migration 0021,
       T3.2, deleted the heartbeat-duplicate rows this used to have to
       split out), feeding both the imputed percentile totals and the
       per-(item, source) shipping medians equally.
    3. Full-history summaries (`{schema.history_summary_view}`) —
       observation_count / last_observed_at / days_of_history /
       last_polled_at, unbounded by window (gates INSUFFICIENT_DATA).

    Grouping, the shipping cascade, and current-best selection (cheapest
    `effective_shipping` total among an item's candidates, ties broken by
    `(source, condition, seller-or-'')` ascending — the same tie-break the
    old `current_best` CTE's correlated subquery encoded) all happen in
    Python per item via `_stats_for_one_item`.

    `medians` is the cascade's cross-item (source, seller_class) tier —
    pass the result of `source_seller_global_shipping_medians(session,
    schema=schema)` when computing stats for a whole list of items in one
    request so that full-table scan runs once, not once per item; `None`
    computes it here as a 4th query (matches the single-item wrapper's
    prior behaviour). `window_days` maps each item id to the window its
    caller wants (`Book.percentile_window_days` or the item's own
    per-book/product override) — the canonical 1m/3m/12m windows are
    always computed regardless; this only affects the `sorted_totals` /
    `current_percentile_rank` fields for a non-canonical window.
    """
    ids = list(item_ids)
    if not ids:
        return {}

    if medians is None:
        medians = source_seller_global_shipping_medians(
            session,
            min_observations=cfg.min_global_median_observations,
            schema=schema,
        )

    # Query 1: live-offer candidates for every requested item.
    # `session.connection().execute(...)` (Core), not `session.exec(...)`
    # (ORM) — see the comment in `source_seller_global_shipping_medians`;
    # same reasoning applies to all three queries below.
    candidates_by_id: dict[int, list[tuple]] = defaultdict(list)
    live_offers_stmt = (
        text(
            f"""
            SELECT {schema.id_column}, source, total_minor, price_minor,
                   shipping_minor, condition, seller, url
            FROM {schema.live_offers_view}
            WHERE {schema.id_column} IN :ids
            """
        )
        .bindparams(bindparam("ids", expanding=True))
        .bindparams(ids=ids)
    )
    for iid, source, total, price, shipping, condition, seller, url in (
        session.connection().execute(live_offers_stmt).all()
    ):
        candidates_by_id[iid].append((source, total, price, shipping, condition, seller, url))

    # Query 2: window observations for every requested item, bounded to the
    # widest window in play (any per-item override, or the widest canonical
    # WINDOW_DAYS bucket). Every row is one distinct offer (post-compaction),
    # so it feeds both the imputed percentile totals and the per-(item,
    # source) shipping medians — no more canonical/duplicate split.
    max_window_days = max(max(WINDOW_DAYS.values()), *(window_days.get(i, 0) for i in ids))
    since = datetime.now(UTC) - timedelta(days=max_window_days)
    imputed_rows_by_id: dict[int, list[tuple]] = defaultdict(list)
    shipping_rows_by_id: dict[int, list[tuple[str, int]]] = defaultdict(list)
    window_obs_stmt = (
        text(
            f"""
            SELECT {schema.id_column}, observed_at, source, seller, price_minor,
                   shipping_minor, total_minor
            FROM {schema.observation_table}
            WHERE {schema.id_column} IN :ids AND observed_at >= :since
            """
        )
        .bindparams(bindparam("ids", expanding=True))
        .bindparams(ids=ids, since=since)
    )
    for iid, observed_at, source, seller, price, shipping, total in (
        session.connection().execute(window_obs_stmt).all()
    ):
        if shipping is not None:
            shipping_rows_by_id[iid].append((source, int(shipping)))
        imputed_rows_by_id[iid].append(
            (_to_aware(observed_at), source, seller, price, shipping, total)
        )

    # Query 3: full-history summaries for every requested item.
    history_by_id: dict[int, tuple] = {}
    history_stmt = (
        text(
            f"""
            SELECT {schema.id_column}, observation_count, last_observed_at,
                   days_of_history, last_polled_at
            FROM {schema.history_summary_view}
            WHERE {schema.id_column} IN :ids
            """
        )
        .bindparams(bindparam("ids", expanding=True))
        .bindparams(ids=ids)
    )
    for row in session.connection().execute(history_stmt).all():
        history_by_id[row[0]] = row[1:]

    now = datetime.now(UTC)
    return {
        iid: _stats_for_one_item(
            iid,
            candidates=candidates_by_id.get(iid, []),
            imputed_rows=imputed_rows_by_id.get(iid, []),
            shipping_rows=shipping_rows_by_id.get(iid, []),
            history=history_by_id.get(iid),
            window_days=window_days[iid],
            prime=prime,
            default_shipping_minor=cfg.default_shipping_minor,
            source_seller_global_medians=medians,
            now=now,
        )
        for iid in ids
    }


def _stats_for_one_item(
    item_id: int,
    *,
    candidates: list[tuple],
    imputed_rows: list[tuple],
    shipping_rows: list[tuple[str, int]],
    history: tuple | None,
    window_days: int,
    prime: bool,
    default_shipping_minor: int,
    source_seller_global_medians: dict[tuple[str, SellerClass], int],
    now: datetime,
) -> BookStats:
    """Per-item cascade + current-best selection + window/percentile
    computation, given the pre-fetched rows `compute_stats_for_items`
    sliced out of its three batched queries for this one item. Never
    queries the DB itself."""
    by_book_source: dict[str, list[int]] = {}
    all_book_shipping: list[int] = []
    for source, shipping in shipping_rows:
        by_book_source.setdefault(source, []).append(shipping)
        all_book_shipping.append(shipping)
    book_source_medians: dict[str, int] = {
        s: int(statistics.median(v)) for s, v in by_book_source.items()
    }
    book_median: int | None = (
        int(statistics.median(all_book_shipping)) if all_book_shipping else None
    )
    cascade = partial(
        _imputed_shipping,
        book_source_medians=book_source_medians,
        source_seller_global_medians=source_seller_global_medians,
        book_median=book_median,
        default_shipping=default_shipping_minor,
    )

    # Current-best selection: cheapest effective total among this item's
    # live-offer candidates. `min()` over (effective_total, tie_key) tuples
    # is exactly the two-stage rule the old `current_best` CTE encoded in
    # SQL — lexicographic tuple order minimises total first, and among rows
    # tied at that minimum, the alphabetically-smallest tie_key.
    best: tuple | None = None
    best_key: tuple[int, tuple[str, str, str]] | None = None
    for source, total, price, shipping, condition, seller, url in candidates:
        eff_shipping, _is_estimate = effective_shipping(
            source, seller, shipping, prime=prime, cascade=cascade
        )
        key = (price + eff_shipping, (source, condition, seller or ""))
        if best_key is None or key < best_key:
            best_key = key
            best = (source, total, price, shipping, condition, seller, url)

    if best is None:
        current_source = current_total = current_price = current_shipping = None
        current_condition = current_seller = current_url = None
    else:
        (
            current_source, current_total, current_price, current_shipping,
            current_condition, current_seller, current_url,
        ) = best

    effective: int | None
    shipping_estimate: int | None = None
    if current_total is None:
        effective = None
    elif current_shipping is not None:
        effective = int(current_total)
    else:
        # `price_minor` is non-nullable in the model, so when a candidate
        # won (current_total is not None), current_price is guaranteed set.
        assert current_price is not None
        imp = cascade(current_source, current_seller)
        effective = int(current_price) + imp
        shipping_estimate = imp

    imputed: list[tuple[datetime, int]] = []
    for observed_at, source, seller, price, shipping, total in imputed_rows:
        if shipping is not None and total is not None:
            imputed.append((observed_at, int(total)))
            continue
        if price is None:
            continue
        imp = cascade(source, seller)
        imputed.append((observed_at, int(price) + imp))

    # Sort by ts ascending once so each window resolves to a tail slice via
    # bisect, and the all-time bounds fold in alongside.
    imputed.sort(key=lambda r: r[0])
    ts_list = [r[0] for r in imputed]

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

    observation_count, last_observed_at, days_of_history, last_polled_at = (
        history if history is not None else (0, None, 0, None)
    )

    return BookStats(
        book_id=item_id,
        current_best_total_minor=current_total,
        current_best_price_minor=current_price,
        current_best_shipping_minor=current_shipping,
        current_best_source=current_source,
        current_best_condition=current_condition,
        current_best_seller=current_seller,
        current_best_url=current_url,
        all_time_min_total_minor=all_time_min,
        all_time_max_total_minor=all_time_max,
        observation_count=observation_count or 0,
        last_observed_at=last_observed_at,
        days_of_history=days_of_history or 0,
        last_polled_at=last_polled_at,
        percentile_window_days=window_days,
        current_percentile_rank=cfg_rank,
        current_effective_total_minor=effective,
        shipping_estimate_minor=shipping_estimate,
        sorted_totals=cfg_totals,
        windows=windows,
    )


def compute_book_stats(
    book_id: int,
    session: Session,
    window_days: int = 90,
    *,
    source_seller_global_medians: dict[tuple[str, SellerClass], int] | None = None,
    default_shipping_minor: int = 280,
    min_global_median_observations: int = 10,
) -> BookStats:
    """Compute the stats bundle for a single book — a thin wrapper over
    `compute_stats_for_items([book_id], ...)`. Kept for callers (the
    per-book detail endpoint, the alert dispatcher) that only ever want one
    book's stats; signature unchanged from before the T3.1 restructure."""
    cfg = RecommendationConfig(
        default_shipping_minor=default_shipping_minor,
        min_global_median_observations=min_global_median_observations,
    )
    return compute_stats_for_items(
        [book_id],
        session,
        schema=_BOOK_SCHEMA,
        cfg=cfg,
        window_days={book_id: window_days},
        medians=source_seller_global_medians,
    )[book_id]


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
    cfg = RecommendationConfig(
        default_shipping_minor=default_shipping_minor,
        min_global_median_observations=min_global_median_observations,
    )
    return compute_stats_for_items(
        [product_id],
        session,
        schema=_PRODUCT_SCHEMA,
        cfg=cfg,
        window_days={product_id: window_days},
        medians=source_seller_global_medians,
    )[product_id]


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
        return Signal.INSUFFICIENT_DATA
    if stats.observation_count < cfg.min_observations_for_signal:
        return Signal.INSUFFICIENT_DATA
    if stats.current_best_total_minor is None:
        return Signal.INSUFFICIENT_DATA

    threshold_pct = item.percentile_threshold or cfg.buy_percentile

    if item.target_price_minor is not None:
        tolerance = int(item.target_price_minor * (1 + cfg.target_tolerance_pct / 100))
        if stats.current_best_total_minor <= item.target_price_minor:
            return Signal.TARGET_HIT
        if stats.current_best_total_minor <= tolerance:
            return Signal.BUY

    # Compare the shipping-adjusted current total against the percentile cut
    # of the (windowed, shipping-merged) distribution. `current_effective_
    # total_minor` is None only when both the current row and the book's
    # history lack any shipping signal we could estimate from.
    effective = stats.current_effective_total_minor
    p_field = stats.percentile_at(threshold_pct)
    if effective is None or p_field is None:
        return Signal.INSUFFICIENT_DATA
    if effective <= p_field:
        return Signal.BUY
    watch_cut = stats.percentile_at(cfg.watch_percentile)
    if watch_cut is not None and effective <= watch_cut:
        return Signal.WATCH
    return Signal.WAIT
