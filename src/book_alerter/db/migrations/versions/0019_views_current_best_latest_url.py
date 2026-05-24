"""views_current_best_latest_url — current_best.url surfaces the LATEST sighting
in the dedup group, not the canonical (first-seen) row.

Dedup folds every re-sighting of a stable offer onto a canonical first-sighting
row and the view read that row's URL. The canonical row is written once, by
whatever parser first saw the offer — so a stale/obsolete link (e.g. a dead
`/Amazon-Warehouse-Deals/b` category page from the pre-fix Amazon parser) is
frozen forever, even after every later scrape recorded the correct
`/gp/offer-listing/<asin>` link on its (deduped) row.

`buyable_last_seen` already computes MAX(observed_at) per canonical offer; this
adds `url AS current_url` to that aggregate, relying on SQLite's documented
bare-column rule (one MAX(), no MIN() → bare columns come from the max row).
URL is not part of the dedup key (item/source/seller/condition/price/shipping),
so the latest URL always describes the SAME offer — price/seller/condition are
unchanged. Same change applied to product_stats via the shared view template.
"""

from alembic import op

from book_alerter.db.views import (
    BOOK_STATS_VIEW_SQL,
    DROP_BOOK_STATS_VIEW_SQL,
    DROP_PRODUCT_STATS_VIEW_SQL,
    PRODUCT_STATS_VIEW_SQL,
)

# 0018-era DDL kept inline for the downgrade path (views.py carries the current
# SQL only). Identical to the current view except current_best reads the
# canonical row's frozen `url` instead of the latest sighting's.
_PRIOR_BOOK_STATS_VIEW_SQL = """
CREATE VIEW book_stats AS
WITH non_dupes AS (
    SELECT * FROM priceobservation WHERE is_duplicate_of IS NULL
),
buyable_last_seen AS (
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id,
           MAX(observed_at) AS last_seen
    FROM priceobservation
    WHERE source != 'keepa'
    GROUP BY COALESCE(is_duplicate_of, id)
),
live_offers AS (
    SELECT o.book_id, o.source, o.total_minor, o.price_minor, o.shipping_minor,
           o.condition, o.seller, o.url, o.id, ls.last_seen
    FROM non_dupes o
    JOIN buyable_last_seen ls ON ls.canonical_id = o.id
    WHERE o.source != 'keepa'
),
latest_scrape_per_source AS (
    SELECT book_id, source, MAX(last_seen) AS latest_seen
    FROM live_offers
    GROUP BY book_id, source
),
entity_latest AS (
    SELECT book_id, MAX(latest_seen) AS global_latest
    FROM latest_scrape_per_source
    GROUP BY book_id
),
latest_per_offer AS (
    SELECT lo.book_id, lo.source, lo.total_minor, lo.price_minor, lo.shipping_minor,
           lo.condition, lo.seller, lo.url,
           ROW_NUMBER() OVER (
               PARTITION BY lo.book_id, lo.source, lo.condition, COALESCE(lo.seller, '')
               ORDER BY lo.last_seen DESC, lo.total_minor ASC, lo.id ASC
           ) AS rn
    FROM live_offers lo
    JOIN latest_scrape_per_source l
      ON l.book_id = lo.book_id AND l.source = lo.source
    JOIN entity_latest g ON g.book_id = lo.book_id
    WHERE lo.last_seen = l.latest_seen
      AND julianday(g.global_latest) - julianday(l.latest_seen) <= 1.0
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
SELECT m.id AS book_id,
       m.title,
       m.isbn13,
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
FROM book m
LEFT JOIN current_best cb ON cb.book_id = m.id
LEFT JOIN agg_history ah  ON ah.book_id = m.id
LEFT JOIN polled pol      ON pol.book_id = m.id
"""

_PRIOR_PRODUCT_STATS_VIEW_SQL = """
CREATE VIEW product_stats AS
WITH non_dupes AS (
    SELECT * FROM productobservation WHERE is_duplicate_of IS NULL
),
buyable_last_seen AS (
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id,
           MAX(observed_at) AS last_seen
    FROM productobservation
    WHERE source != 'keepa'
    GROUP BY COALESCE(is_duplicate_of, id)
),
live_offers AS (
    SELECT o.product_id, o.source, o.total_minor, o.price_minor, o.shipping_minor,
           o.condition, o.seller, o.url, o.id, ls.last_seen
    FROM non_dupes o
    JOIN buyable_last_seen ls ON ls.canonical_id = o.id
    WHERE o.source != 'keepa'
),
latest_scrape_per_source AS (
    SELECT product_id, source, MAX(last_seen) AS latest_seen
    FROM live_offers
    GROUP BY product_id, source
),
entity_latest AS (
    SELECT product_id, MAX(latest_seen) AS global_latest
    FROM latest_scrape_per_source
    GROUP BY product_id
),
latest_per_offer AS (
    SELECT lo.product_id, lo.source, lo.total_minor, lo.price_minor, lo.shipping_minor,
           lo.condition, lo.seller, lo.url,
           ROW_NUMBER() OVER (
               PARTITION BY lo.product_id, lo.source, lo.condition, COALESCE(lo.seller, '')
               ORDER BY lo.last_seen DESC, lo.total_minor ASC, lo.id ASC
           ) AS rn
    FROM live_offers lo
    JOIN latest_scrape_per_source l
      ON l.product_id = lo.product_id AND l.source = lo.source
    JOIN entity_latest g ON g.product_id = lo.product_id
    WHERE lo.last_seen = l.latest_seen
      AND julianday(g.global_latest) - julianday(l.latest_seen) <= 1.0
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
SELECT m.id AS product_id,
       m.title,
       m.asin,
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
FROM product m
LEFT JOIN current_best cb ON cb.product_id = m.id
LEFT JOIN agg_history ah  ON ah.product_id = m.id
LEFT JOIN polled pol      ON pol.product_id = m.id
"""

revision = "0019_views_current_best_latest_url"
down_revision = "0018_views_stale_source_gate"
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
