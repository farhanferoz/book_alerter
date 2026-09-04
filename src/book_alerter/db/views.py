"""Canonical DDL for SQL views. Imported by Alembic migrations and by
integration tests (SQLModel.metadata.create_all does not create views).

As of migration 0021 (plan task T3.2, heartbeat compaction — following
migration 0020's T3.1 restructure), the LIVE production views are
`book_live_offers` / `product_live_offers` (candidates: one row per live
offer, freshness-gated) and `book_history_summary` / `product_history_summary`
(observation_count / last_observed_at / days_of_history / last_polled_at).
`stats.compute_stats_for_items` selects the current-best offer from the
former in Python instead of a `current_best` SQL CTE — that's what let the
per-item `book_stats`/`product_stats` views collapse into three batched
queries total for a whole list request. See that function's docstring for
the selection algorithm.

The `_STATS_VIEW_TEMPLATE` block below (and `BOOK_STATS_VIEW_SQL` /
`PRODUCT_STATS_VIEW_SQL`) is FROZEN, byte-for-byte, at the pre-0020 shape.
It is no longer live production DDL — kept only because migrations
0016-0019 import these names (each of them drops + recreates book_stats/
product_stats as part of their own upgrade path; whatever content of
theirs the final 0019 shape overwrote is exactly reproduced by this frozen
copy, so a fresh `alembic upgrade head` still round-trips correctly) and
because migration 0020's `downgrade()` recreates book_stats/product_stats
from these same constants. Do not edit this block to "fix" it going
forward — new view logic belongs in `_LIVE_OFFERS_VIEW_TEMPLATE` /
`_HISTORY_SUMMARY_VIEW_TEMPLATE` below.

The T3.1-era (`is_duplicate_of`-based) shape of `_LIVE_OFFERS_VIEW_TEMPLATE`
/ `_HISTORY_SUMMARY_VIEW_TEMPLATE` is NOT frozen here the same way — it's
inlined directly in migration `0020_live_offers_views.py` instead (that
migration owns its own upgrade-path DDL, matching the 0017/0018/0019
convention for `book_stats`/`product_stats`). Migration `0021_...` imports
the templates below (current, `last_seen_at`-based) for its upgrade and
inlines the 0020-era shape for its downgrade.

Both eras render their book/product pair from ONE template each so the
freshness logic lives in a single place — an earlier version kept two
hand-written strings "for readability"; the last-seen rework (migration
0018) made that logic complex enough that two copies were a standing drift
hazard. `test_compute_product_stats_window_percentile_matches_book_path`
asserts the book/product sides render to equivalent behaviour.

Every template here has no literal braces, so `str.format` substitution is
safe.
"""
from __future__ import annotations

# Freshness is keyed off when an offer was LAST seen, not first seen. A
# duplicate observation ("we re-checked and the price was unchanged") is a fresh
# sighting of its canonical offer; `buyable_last_seen` folds dups back onto the
# canonical row (via COALESCE(is_duplicate_of, id) — dups always point at a
# non-dupe canonical) so it carries its true last-seen time. WITHOUT this, a
# stable-but-live price gets deduped on every scrape and the canonical
# observed_at freezes at the FIRST sighting — so current_best reads a weeks-stale
# price, and a since-vanished offer whose first sighting happens to be recent can
# out-rank the genuinely-live offers (verified live: a WOB £16 that stopped
# appearing on 2026-05-17 beat the live £21 / Amazon £17.59 because its first
# sighting was newer than theirs).
#
# An offer is live iff its last_seen equals its source's most recent scrape
# (every offer in one scrape shares that scrape's observed_at). A whole source
# is dropped if it lags the entity's freshest scrape by more than a day — its
# scraper is erroring/disabled and an 8-day-old "current best" is misleading.
# Relative, not wall-clock: when every source is briefly behind, the cheapest
# still shows rather than the entity going price-less. Keepa is excluded
# throughout: it's a historical archive whose rows carry NULL shipping and would
# unfairly beat live totals that include postage.
_STATS_VIEW_TEMPLATE = """
CREATE VIEW {view} AS
WITH non_dupes AS (
    SELECT * FROM {obs} WHERE is_duplicate_of IS NULL
),
buyable_last_seen AS (
    -- `url AS current_url` leans on SQLite's documented bare-column rule: with
    -- exactly one MAX() aggregate and no MIN(), every bare column takes its
    -- value from the row that produced the maximum. So current_url is the URL
    -- of the LATEST sighting in the dedup group, NOT the canonical first-
    -- sighting row — whose URL may be an obsolete link an older parser wrote
    -- (e.g. a dead `/Amazon-Warehouse-Deals/b` category page) and which dedup
    -- freezes forever. URL is not part of the dedup key (item/source/seller/
    -- condition/price/shipping), so the latest URL is always for the SAME offer.
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id,
           MAX(observed_at) AS last_seen,
           url AS current_url
    FROM {obs}
    WHERE source != 'keepa'
    GROUP BY COALESCE(is_duplicate_of, id)
),
live_offers AS (
    SELECT o.{id}, o.source, o.total_minor, o.price_minor, o.shipping_minor,
           o.condition, o.seller, ls.current_url AS url, o.id, ls.last_seen
    FROM non_dupes o
    JOIN buyable_last_seen ls ON ls.canonical_id = o.id
    WHERE o.source != 'keepa'
),
latest_scrape_per_source AS (
    SELECT {id}, source, MAX(last_seen) AS latest_seen
    FROM live_offers
    GROUP BY {id}, source
),
entity_latest AS (
    SELECT {id}, MAX(latest_seen) AS global_latest
    FROM latest_scrape_per_source
    GROUP BY {id}
),
-- One row per offer present in its source's most recent scrape, from a source
-- fresh relative to the entity. The ROW_NUMBER tiebreaker keeps the cheapest
-- when a (source, condition, seller) partition carries two live prices in one
-- scrape (e.g. WOB "Very Good £21" + "Like New £22", both used_vg).
latest_per_offer AS (
    -- Partition seller via COALESCE(seller,'') — the SAME normalisation
    -- current_best's tiebreaker uses below. Partitioning by raw `seller` would
    -- put a NULL-seller and an ''-seller offer (same source+condition) in
    -- separate partitions, both winning rn=1; current_best's
    -- `COALESCE(seller,'')` equality would then match BOTH at the same lowest
    -- total and emit duplicate rows for one entity, breaking the
    -- one-row-per-entity contract of the LEFT JOIN.
    SELECT lo.{id}, lo.source, lo.total_minor, lo.price_minor, lo.shipping_minor,
           lo.condition, lo.seller, lo.url,
           ROW_NUMBER() OVER (
               PARTITION BY lo.{id}, lo.source, lo.condition, COALESCE(lo.seller, '')
               ORDER BY lo.last_seen DESC, lo.total_minor ASC, lo.id ASC
           ) AS rn
    FROM live_offers lo
    JOIN latest_scrape_per_source l
      ON l.{id} = lo.{id} AND l.source = lo.source
    JOIN entity_latest g ON g.{id} = lo.{id}
    WHERE lo.last_seen = l.latest_seen
      AND julianday(g.global_latest) - julianday(l.latest_seen) <= 1.0
),
current_best AS (
    -- When two offers tie at the same lowest total, deterministically prefer
    -- alphabetically-first source, then condition, then seller.
    SELECT lp.{id}, lp.total_minor, lp.price_minor, lp.shipping_minor,
           lp.source, lp.condition, lp.seller, lp.url
    FROM latest_per_offer lp
    JOIN (
        SELECT {id}, MIN(total_minor) AS m
        FROM latest_per_offer
        WHERE rn = 1
        GROUP BY {id}
    ) best ON best.{id} = lp.{id} AND best.m = lp.total_minor AND lp.rn = 1
    WHERE (lp.source, lp.condition, COALESCE(lp.seller, '')) = (
        SELECT lp2.source, lp2.condition, COALESCE(lp2.seller, '')
        FROM latest_per_offer lp2
        WHERE lp2.{id} = lp.{id} AND lp2.total_minor = lp.total_minor AND lp2.rn = 1
        ORDER BY lp2.source, lp2.condition, COALESCE(lp2.seller, '')
        LIMIT 1
    )
),
-- Full canonical history (including Keepa) gates INSUFFICIENT_DATA on
-- observation_count / days_of_history. All-time bounds live in the
-- compute_*_stats helpers (stats.py) so Keepa rows participate with a fair
-- shipping estimate.
agg_history AS (
    SELECT {id},
           COUNT(*)         AS observation_count,
           MAX(observed_at) AS last_observed_at,
           CAST((julianday(MAX(observed_at)) - julianday(MIN(observed_at))) AS INTEGER) AS days_of_history
    FROM non_dupes
    GROUP BY {id}
),
-- `last_polled_at` is the max observed_at over EVERY row (including dups that
-- record "we checked, unchanged"). This is the dashboard's "Last seen";
-- `last_observed_at` only moves when the canonical price actually changes.
polled AS (
    SELECT {id}, MAX(observed_at) AS last_polled_at
    FROM {obs}
    GROUP BY {id}
)
SELECT m.id AS {id},
       m.title,
       m.{extra},
       cb.total_minor    AS current_best_total_minor,
       cb.price_minor    AS current_best_price_minor,
       cb.shipping_minor AS current_best_shipping_minor,
       cb.source         AS current_best_source,
       cb.condition      AS current_best_condition,
       cb.seller         AS current_best_seller,
       cb.url            AS current_best_url,
       ah.observation_count,
       ah.last_observed_at,
       pol.last_polled_at,
       ah.days_of_history
FROM {main} m
LEFT JOIN current_best cb ON cb.{id} = m.id
LEFT JOIN agg_history ah  ON ah.{id} = m.id
LEFT JOIN polled pol      ON pol.{id} = m.id
"""


def _stats_view_sql(
    *, view: str, id_col: str, obs_table: str, main_table: str, extra_col: str
) -> str:
    return _STATS_VIEW_TEMPLATE.format(
        view=view, id=id_col, obs=obs_table, main=main_table, extra=extra_col
    )


BOOK_STATS_VIEW_SQL = _stats_view_sql(
    view="book_stats",
    id_col="book_id",
    obs_table="priceobservation",
    main_table="book",
    extra_col="isbn13",
)
DROP_BOOK_STATS_VIEW_SQL = "DROP VIEW IF EXISTS book_stats"

PRODUCT_STATS_VIEW_SQL = _stats_view_sql(
    view="product_stats",
    id_col="product_id",
    obs_table="productobservation",
    main_table="product",
    extra_col="asin",
)
DROP_PRODUCT_STATS_VIEW_SQL = "DROP VIEW IF EXISTS product_stats"


# ---------------------------------------------------------------------------
# Live views (migration 0020 onward; last_seen_at-based shape as of migration
# 0021, T3.2 heartbeat compaction). `_LIVE_OFFERS_VIEW_TEMPLATE` is the
# candidate half of the old `current_best` CTE chain — everything through
# `latest_per_offer` — filtered to `rn = 1` and stopped there: one row per
# live offer (freshness-gated exactly as before), no ranking. Ranking by
# effective total (price + observed-or-cascade shipping) and the alphabetical
# tie-break both live in `stats.compute_stats_for_items`.
#
# Migration 0021 deleted every `is_duplicate_of`-pointing heartbeat row and
# dropped that column — every remaining row is already the one row for its
# offer, carrying its own `last_seen_at` (bumped in place by
# `scheduler._persist` on a re-confirming scrape, instead of a new duplicate
# row). That's what let `non_dupes` / `buyable_last_seen` (the dedup-fold
# that used to reconstruct "last seen" and "current url" from a GROUP BY
# over the duplicate rows) disappear — `o.last_seen_at` and `o.url` are
# already current on the row itself.
# ---------------------------------------------------------------------------

_LIVE_OFFERS_VIEW_TEMPLATE = """
CREATE VIEW {view} AS
WITH live_offers AS (
    SELECT o.{id}, o.source, o.total_minor, o.price_minor, o.shipping_minor,
           o.condition, o.seller, o.url, o.id, o.last_seen_at AS last_seen
    FROM {obs} o
    WHERE o.source != 'keepa'
),
latest_scrape_per_source AS (
    SELECT {id}, source, MAX(last_seen) AS latest_seen
    FROM live_offers
    GROUP BY {id}, source
),
entity_latest AS (
    SELECT {id}, MAX(latest_seen) AS global_latest
    FROM latest_scrape_per_source
    GROUP BY {id}
),
-- One row per offer present in its source's most recent scrape, from a source
-- fresh relative to the entity. The ROW_NUMBER tiebreaker picks a survivor
-- when a (source, condition, seller) partition carries two live prices in
-- one scrape (e.g. WOB "Very Good £21" + "Like New £22", both used_vg).
-- Migration 0024 (S4, 2026-09-04 shipping-chain review) changed how it
-- picks: raw `total_minor` (`price + (shipping or 0)`) folds unknown
-- shipping to zero, so ranking on it duplicated
-- `stats.compute_stats_for_items`'s current-best selection on the WRONG
-- metric and could survive a genuinely cheaper KNOWN-shipping offer's row
-- before Python's effective-total selection ever saw it (D14 says that
-- ranking lives in Python, in one place). `total_minor` is only wrong when
-- shipping is unknown -- when it's known, `total_minor` IS the true
-- effective total, so ranking on it is correct and this view should not
-- throw that signal away (`test_current_best_selection_matches_effective_
-- total_reference`'s independent reference model requires it, and a
-- property test caught the regression when this tiebreak was changed to
-- plain `id ASC`). So: prefer a row with OBSERVED shipping over one with
-- unknown shipping outright (`shipping_minor IS NULL` sorts known-first);
-- among rows tied on that, `total_minor ASC` is trustworthy; `id ASC` is
-- the final, purely arbitrary tiebreak for genuine remaining ties.
latest_per_offer AS (
    -- Partition seller via COALESCE(seller,'') so a NULL-seller and an
    -- ''-seller offer (same source+condition) land in the SAME partition —
    -- otherwise both could win rn=1 and be handed to the Python selection as
    -- two distinct candidates for what's really one offer.
    SELECT lo.{id}, lo.source, lo.total_minor, lo.price_minor, lo.shipping_minor,
           lo.condition, lo.seller, lo.url,
           ROW_NUMBER() OVER (
               PARTITION BY lo.{id}, lo.source, lo.condition, COALESCE(lo.seller, '')
               ORDER BY lo.last_seen DESC, (lo.shipping_minor IS NULL) ASC,
                        lo.total_minor ASC, lo.id ASC
           ) AS rn
    FROM live_offers lo
    JOIN latest_scrape_per_source l
      ON l.{id} = lo.{id} AND l.source = lo.source
    JOIN entity_latest g ON g.{id} = lo.{id}
    WHERE lo.last_seen = l.latest_seen
      AND julianday(g.global_latest) - julianday(l.latest_seen) <= 1.0
)
SELECT {id}, source, total_minor, price_minor, shipping_minor, condition, seller, url
FROM latest_per_offer
WHERE rn = 1
"""

# `_HISTORY_SUMMARY_VIEW_TEMPLATE`: `observation_count` / `last_observed_at`
# (max first-sighting time — moves only when a genuinely new price appears)
# / `days_of_history` gate INSUFFICIENT_DATA exactly as before (every row IS
# already what `non_dupes` used to filter to, so no WHERE is needed here any
# more). `last_polled_at` (the dashboard's "Last seen") is `MAX(last_seen_at)`
# rather than `MAX(observed_at)` — before 0021 that meant "max observed_at
# over every row including duplicates"; since a repeat sighting no longer
# writes a new row, `last_seen_at` is now the only column that "moves on
# every scrape" the way `last_polled_at` is documented to. One GROUP BY
# instead of two CTEs + a LEFT JOIN, now that both halves read the same rows.
_HISTORY_SUMMARY_VIEW_TEMPLATE = """
CREATE VIEW {view} AS
SELECT {id},
       COUNT(*)          AS observation_count,
       MAX(observed_at)  AS last_observed_at,
       CAST((julianday(MAX(observed_at)) - julianday(MIN(observed_at))) AS INTEGER) AS days_of_history,
       MAX(last_seen_at) AS last_polled_at
FROM {obs}
GROUP BY {id}
"""


def _live_offers_view_sql(*, view: str, id_col: str, obs_table: str) -> str:
    return _LIVE_OFFERS_VIEW_TEMPLATE.format(view=view, id=id_col, obs=obs_table)


def _history_summary_view_sql(*, view: str, id_col: str, obs_table: str) -> str:
    return _HISTORY_SUMMARY_VIEW_TEMPLATE.format(view=view, id=id_col, obs=obs_table)


BOOK_LIVE_OFFERS_VIEW_SQL = _live_offers_view_sql(
    view="book_live_offers", id_col="book_id", obs_table="priceobservation",
)
DROP_BOOK_LIVE_OFFERS_VIEW_SQL = "DROP VIEW IF EXISTS book_live_offers"

PRODUCT_LIVE_OFFERS_VIEW_SQL = _live_offers_view_sql(
    view="product_live_offers", id_col="product_id", obs_table="productobservation",
)
DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL = "DROP VIEW IF EXISTS product_live_offers"

BOOK_HISTORY_SUMMARY_VIEW_SQL = _history_summary_view_sql(
    view="book_history_summary", id_col="book_id", obs_table="priceobservation",
)
DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL = "DROP VIEW IF EXISTS book_history_summary"

PRODUCT_HISTORY_SUMMARY_VIEW_SQL = _history_summary_view_sql(
    view="product_history_summary", id_col="product_id", obs_table="productobservation",
)
DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL = "DROP VIEW IF EXISTS product_history_summary"
