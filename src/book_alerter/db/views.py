"""Canonical DDL for SQL views. Imported by Alembic migrations and by
integration tests (SQLModel.metadata.create_all does not create views)."""
from __future__ import annotations


BOOK_STATS_VIEW_SQL = """
CREATE VIEW book_stats AS
WITH non_dupes AS (
    SELECT * FROM priceobservation WHERE is_duplicate_of IS NULL
),
-- current_best is restricted to live offers (excludes Keepa, which is a
-- historical archive whose PNG only renders item prices — its rows have
-- NULL shipping and would unfairly beat live totals that include postage).
-- Live rows with NULL shipping still qualify so the user sees the item
-- price plus an em-dash, rather than nothing at all when no source on a
-- given book managed to extract a delivery line.
buyable AS (
    SELECT * FROM non_dupes
    WHERE source != 'keepa'
),
-- Partition by the FULL offer identity so each distinct live offer competes
-- in the `current_best` MIN() race. Partitioning by (book, source) alone
-- collapsed multi-condition / multi-seller rows from the same source: e.g.
-- WOB returning both `new £19.09` and `used_vg £17.50` in one scrape would
-- have the cheaper offer dropped if the ROW_NUMBER tie-break didn't pick it.
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
    -- When two offers tie at the same lowest total, deterministically prefer
    -- alphabetically-first source, then condition, then seller. Otherwise
    -- the view returns non-deterministic rows for ties.
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
-- `last_polled_at` is the max observed_at over EVERY row (including dupes
-- that record "we checked and the price was unchanged"). This is what the
-- dashboard's "Last seen" should display — `last_observed_at` from `agg`
-- above only moves when the canonical price actually changes.
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

DROP_BOOK_STATS_VIEW_SQL = "DROP VIEW IF EXISTS book_stats"
