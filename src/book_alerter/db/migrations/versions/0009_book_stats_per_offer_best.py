"""book_stats view: pick current_best per (source, condition, seller), not per source"""
from alembic import op

from book_alerter.db.views import BOOK_STATS_VIEW_SQL, DROP_BOOK_STATS_VIEW_SQL

revision = "0009_book_stats_per_offer_best"
down_revision = "0008_book_stats_last_polled_at"
branch_labels = None
depends_on = None


_PREV_VIEW_SQL = """
CREATE VIEW book_stats AS
WITH non_dupes AS (
    SELECT * FROM priceobservation WHERE is_duplicate_of IS NULL
),
buyable AS (
    SELECT * FROM non_dupes
    WHERE source != 'keepa'
),
latest_per_source AS (
    SELECT book_id, source, total_minor, price_minor, shipping_minor,
           condition, seller, url, observed_at,
           ROW_NUMBER() OVER (PARTITION BY book_id, source ORDER BY observed_at DESC) AS rn
    FROM buyable
),
current_best AS (
    SELECT lp.book_id, lp.total_minor, lp.price_minor, lp.shipping_minor,
           lp.source, lp.condition, lp.seller, lp.url
    FROM latest_per_source lp
    JOIN (
        SELECT book_id, MIN(total_minor) AS m
        FROM latest_per_source
        WHERE rn = 1
        GROUP BY book_id
    ) best ON best.book_id = lp.book_id AND best.m = lp.total_minor AND lp.rn = 1
    WHERE lp.source = (
        SELECT MIN(source) FROM latest_per_source lp2
        WHERE lp2.book_id = lp.book_id AND lp2.total_minor = lp.total_minor AND lp2.rn = 1
    )
),
agg AS (
    SELECT book_id,
           MIN(total_minor) AS all_time_min_total_minor,
           MAX(total_minor) AS all_time_max_total_minor,
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
       a.all_time_min_total_minor,
       a.all_time_max_total_minor,
       a.observation_count,
       a.last_observed_at,
       p.last_polled_at,
       a.days_of_history
FROM book b
LEFT JOIN current_best cb ON cb.book_id = b.id
LEFT JOIN agg a          ON a.book_id  = b.id
LEFT JOIN polled p       ON p.book_id  = b.id
"""


def upgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)
    op.execute(BOOK_STATS_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)
    op.execute(_PREV_VIEW_SQL)
