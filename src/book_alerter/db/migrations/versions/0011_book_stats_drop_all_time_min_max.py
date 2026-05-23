"""book_stats view: drop all_time_min/max columns; bounds now computed in Python.

`compute_book_stats` imputes shipping for Keepa rows and derives the
bounds from the imputed totals, so the view's Keepa-excluded values
would have been stale.
"""
from alembic import op

from book_alerter.db.views import BOOK_STATS_VIEW_SQL, DROP_BOOK_STATS_VIEW_SQL

revision = "0011_book_stats_drop_all_time_min_max"
down_revision = "0010_book_stats_buyable_min_max"
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
agg_buyable AS (
    SELECT book_id,
           MIN(total_minor) AS all_time_min_total_minor,
           MAX(total_minor) AS all_time_max_total_minor
    FROM buyable
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
       ab.all_time_min_total_minor,
       ab.all_time_max_total_minor,
       ah.observation_count,
       ah.last_observed_at,
       p.last_polled_at,
       ah.days_of_history
FROM book b
LEFT JOIN current_best cb ON cb.book_id = b.id
LEFT JOIN agg_history ah  ON ah.book_id = b.id
LEFT JOIN agg_buyable ab  ON ab.book_id = b.id
LEFT JOIN polled p        ON p.book_id  = b.id
"""


def upgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)
    op.execute(BOOK_STATS_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)
    op.execute(_PREV_VIEW_SQL)
