"""Canonical DDL for SQL views. Imported by Alembic migrations and by
integration tests (SQLModel.metadata.create_all does not create views).

`book_stats` and `product_stats` are mirror views — identical CTE logic over
`priceobservation`/`book` vs `productobservation`/`product`, differing only in
the entity table/column names (and which natural-key column is surfaced:
`isbn13` vs `asin`). They are rendered from ONE template, `_STATS_VIEW_TEMPLATE`,
so the freshness / current-best logic lives in a single place. An earlier
version kept two hand-written strings "for readability"; the last-seen rework
(migration 0018) made that logic complex enough that two copies were a standing
drift hazard — a fix applied to one view but not the other would silently skew
products from books. Parameterising the handful of entity tokens removes the
duplication; `test_compute_product_stats_window_percentile_matches_book_path`
asserts the two render to equivalent behaviour.

The template has no literal braces, so `str.format` substitution is safe.
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
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id,
           MAX(observed_at) AS last_seen
    FROM {obs}
    WHERE source != 'keepa'
    GROUP BY COALESCE(is_duplicate_of, id)
),
live_offers AS (
    SELECT o.{id}, o.source, o.total_minor, o.price_minor, o.shipping_minor,
           o.condition, o.seller, o.url, o.id, ls.last_seen
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
