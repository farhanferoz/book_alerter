"""views_freshness_gate — restrict current_best to rows from each (book, source)'s
most recent scrape.

Without this filter, a `(book, source, condition, seller)` partition whose
parser stopped emitting it (e.g. an Amazon Resale row that the old parser
mis-classified as `condition=new` before commit f24668b changed it to
`used_vg`) sits forever as "latest-of-its-partition" and can win
MIN(total_minor) against truly-current rows. After the fix, only
observations from the same source-scrape as the book's most recent are
considered live.

Same change applied to product_stats.
"""

from alembic import op

from book_alerter.db.views import (
    BOOK_STATS_VIEW_SQL,
    DROP_BOOK_STATS_VIEW_SQL,
    DROP_PRODUCT_STATS_VIEW_SQL,
    PRODUCT_STATS_VIEW_SQL,
)

# Earlier-revision DDL kept inline as raw SQL for the downgrade path. We
# don't re-import the historical strings (the canonical views.py module
# carries the *current* SQL only); duplicating them here is the standard
# alembic pattern for view-DDL migrations.
_PRIOR_BOOK_STATS_VIEW_SQL = """
CREATE VIEW book_stats AS
WITH non_dupes AS (
    SELECT * FROM priceobservation WHERE is_duplicate_of IS NULL
),
buyable AS (
    SELECT * FROM non_dupes
    WHERE source != 'keepa'
),
latest_per_offer AS (
    SELECT book_id, source, total_minor, price_minor, shipping_minor,
           condition, seller, url, observed_at,
           ROW_NUMBER() OVER (
               PARTITION BY book_id, source, condition, seller
               ORDER BY observed_at DESC
           ) AS rn
    FROM buyable
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
latest_per_offer AS (
    SELECT product_id, source, total_minor, price_minor, shipping_minor,
           condition, seller, url, observed_at,
           ROW_NUMBER() OVER (
               PARTITION BY product_id, source, condition, seller
               ORDER BY observed_at DESC
           ) AS rn
    FROM buyable
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

revision = "0017_views_freshness_gate"
down_revision = "0016_product_stats_view"
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
