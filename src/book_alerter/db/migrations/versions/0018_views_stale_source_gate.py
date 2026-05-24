"""views_stale_source_gate — base current_best freshness on when an offer was
LAST seen (including dups), and drop whole sources that haven't scraped in the
current cycle.

The 0017 gate keyed freshness off the canonical (non-dupe) row's observed_at —
i.e. when a price was FIRST seen. But a stable-but-live price is deduped on
every scrape, so its canonical observed_at freezes at the first sighting. Two
failures followed (both verified live): current_best read a weeks-stale price,
and a since-vanished offer whose first sighting happened to be recent
out-ranked the genuinely-live offers (a WOB £16 that stopped appearing on
2026-05-17 beat the live £21 / Amazon £17.59 — wrong on 6 of 9 tracked books).

This rebuilds the current_best CTEs around `buyable_last_seen`
(MAX(observed_at) per canonical offer, folding dups back onto their canonical
via COALESCE(is_duplicate_of, id)): an offer is live iff its last_seen equals
its source's latest scrape, and a source is dropped entirely if it lags the
book's freshest scrape by more than a day (relative, not wall-clock, so a
brief all-source delay still shows the cheapest rather than going price-less).

Same change applied to product_stats.
"""

from alembic import op

from book_alerter.db.views import (
    BOOK_STATS_VIEW_SQL,
    DROP_BOOK_STATS_VIEW_SQL,
    DROP_PRODUCT_STATS_VIEW_SQL,
    PRODUCT_STATS_VIEW_SQL,
)

# 0017-era DDL kept inline for the downgrade path (views.py carries the current
# SQL only). This is the pre-stale-source-gate view: per-(book, source)
# freshness only, no cross-source comparison.
_PRIOR_BOOK_STATS_VIEW_SQL = """
CREATE VIEW book_stats AS
WITH non_dupes AS (
    SELECT * FROM priceobservation WHERE is_duplicate_of IS NULL
),
buyable AS (
    SELECT * FROM non_dupes
    WHERE source != 'keepa'
),
latest_scrape_per_source AS (
    SELECT book_id, source, MAX(observed_at) AS latest_observed_at
    FROM buyable
    GROUP BY book_id, source
),
latest_per_offer AS (
    SELECT b.book_id, b.source, b.total_minor, b.price_minor, b.shipping_minor,
           b.condition, b.seller, b.url, b.observed_at,
           ROW_NUMBER() OVER (
               PARTITION BY b.book_id, b.source, b.condition, b.seller
               ORDER BY b.observed_at DESC, b.total_minor ASC, b.id ASC
           ) AS rn
    FROM buyable b
    JOIN latest_scrape_per_source l
      ON l.book_id = b.book_id AND l.source = b.source
    WHERE b.observed_at = l.latest_observed_at
),
current_best AS (
    SELECT lp.book_id, lp.total_minor, lp.price_minor, lp.shipping_minor,
           lp.source, lp.condition, lp.seller, lp.url
    FROM latest_per_offer lp
    JOIN (
        SELECT book_id, MIN(total_minor) AS m
        FROM latest_per_offer
        WHERE rn = 1
        GROUP BY book_id
    ) best ON best.book_id = lp.book_id AND best.m = lp.total_minor AND lp.rn = 1
    WHERE (lp.source, lp.condition, COALESCE(lp.seller, '')) = (
        SELECT lp2.source, lp2.condition, COALESCE(lp2.seller, '')
        FROM latest_per_offer lp2
        WHERE lp2.book_id = lp.book_id AND lp2.total_minor = lp.total_minor AND lp2.rn = 1
        ORDER BY lp2.source, lp2.condition, COALESCE(lp2.seller, '')
        LIMIT 1
    )
),
agg_history AS (
    SELECT book_id,
           COUNT(*)         AS observation_count,
           MAX(observed_at) AS last_observed_at,
           CAST((julianday(MAX(observed_at)) - julianday(MIN(observed_at))) AS INTEGER) AS days_of_history
    FROM non_dupes
    GROUP BY book_id
),
polled AS (
    SELECT book_id, MAX(observed_at) AS last_polled_at
    FROM priceobservation
    GROUP BY book_id
)
SELECT b.id AS book_id,
       b.title,
       b.isbn13,
       cb.total_minor    AS current_best_total_minor,
       cb.price_minor    AS current_best_price_minor,
       cb.shipping_minor AS current_best_shipping_minor,
       cb.source         AS current_best_source,
       cb.condition      AS current_best_condition,
       cb.seller         AS current_best_seller,
       cb.url            AS current_best_url,
       ah.observation_count,
       ah.last_observed_at,
       p.last_polled_at,
       ah.days_of_history
FROM book b
LEFT JOIN current_best cb ON cb.book_id = b.id
LEFT JOIN agg_history ah  ON ah.book_id = b.id
LEFT JOIN polled p        ON p.book_id  = b.id
"""

_PRIOR_PRODUCT_STATS_VIEW_SQL = """
CREATE VIEW product_stats AS
WITH non_dupes AS (
    SELECT * FROM productobservation WHERE is_duplicate_of IS NULL
),
buyable AS (
    SELECT * FROM non_dupes
    WHERE source != 'keepa'
),
latest_scrape_per_source AS (
    SELECT product_id, source, MAX(observed_at) AS latest_observed_at
    FROM buyable
    GROUP BY product_id, source
),
latest_per_offer AS (
    SELECT b.product_id, b.source, b.total_minor, b.price_minor, b.shipping_minor,
           b.condition, b.seller, b.url, b.observed_at,
           ROW_NUMBER() OVER (
               PARTITION BY b.product_id, b.source, b.condition, b.seller
               ORDER BY b.observed_at DESC, b.total_minor ASC, b.id ASC
           ) AS rn
    FROM buyable b
    JOIN latest_scrape_per_source l
      ON l.product_id = b.product_id AND l.source = b.source
    WHERE b.observed_at = l.latest_observed_at
),
current_best AS (
    SELECT lp.product_id, lp.total_minor, lp.price_minor, lp.shipping_minor,
           lp.source, lp.condition, lp.seller, lp.url
    FROM latest_per_offer lp
    JOIN (
        SELECT product_id, MIN(total_minor) AS m
        FROM latest_per_offer
        WHERE rn = 1
        GROUP BY product_id
    ) best ON best.product_id = lp.product_id AND best.m = lp.total_minor AND lp.rn = 1
    WHERE (lp.source, lp.condition, COALESCE(lp.seller, '')) = (
        SELECT lp2.source, lp2.condition, COALESCE(lp2.seller, '')
        FROM latest_per_offer lp2
        WHERE lp2.product_id = lp.product_id AND lp2.total_minor = lp.total_minor AND lp2.rn = 1
        ORDER BY lp2.source, lp2.condition, COALESCE(lp2.seller, '')
        LIMIT 1
    )
),
agg_history AS (
    SELECT product_id,
           COUNT(*)         AS observation_count,
           MAX(observed_at) AS last_observed_at,
           CAST((julianday(MAX(observed_at)) - julianday(MIN(observed_at))) AS INTEGER) AS days_of_history
    FROM non_dupes
    GROUP BY product_id
),
polled AS (
    SELECT product_id, MAX(observed_at) AS last_polled_at
    FROM productobservation
    GROUP BY product_id
)
SELECT p.id AS product_id,
       p.title,
       p.asin,
       cb.total_minor    AS current_best_total_minor,
       cb.price_minor    AS current_best_price_minor,
       cb.shipping_minor AS current_best_shipping_minor,
       cb.source         AS current_best_source,
       cb.condition      AS current_best_condition,
       cb.seller         AS current_best_seller,
       cb.url            AS current_best_url,
       ah.observation_count,
       ah.last_observed_at,
       pl.last_polled_at,
       ah.days_of_history
FROM product p
LEFT JOIN current_best cb ON cb.product_id = p.id
LEFT JOIN agg_history ah  ON ah.product_id = p.id
LEFT JOIN polled pl       ON pl.product_id = p.id
"""

revision = "0018_views_stale_source_gate"
down_revision = "0017_views_freshness_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)
    op.execute(BOOK_STATS_VIEW_SQL)
    op.execute(DROP_PRODUCT_STATS_VIEW_SQL)
    op.execute(PRODUCT_STATS_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)
    op.execute(_PRIOR_BOOK_STATS_VIEW_SQL)
    op.execute(DROP_PRODUCT_STATS_VIEW_SQL)
    op.execute(_PRIOR_PRODUCT_STATS_VIEW_SQL)
