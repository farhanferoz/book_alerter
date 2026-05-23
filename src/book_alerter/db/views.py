"""Canonical DDL for SQL views. Imported by Alembic migrations and by
integration tests (SQLModel.metadata.create_all does not create views).

`book_stats` and `product_stats` are mirror views — same CTE shape, with
the table/column names swapped. We deliberately keep them as two separate
strings (not generated) for readability: the SQL is small, and a templating
abstraction would obscure the few semantic places they intentionally
diverge (e.g., `b.isbn13` vs `p.asin`).
"""
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
-- Full canonical history (including Keepa) — gates INSUFFICIENT_DATA on
-- observation_count / days_of_history. All-time bounds live in
-- `compute_book_stats` (see stats.py) so Keepa rows can participate with
-- a fair shipping estimate.
agg_history AS (
    SELECT book_id,
           COUNT(*)         AS observation_count,
           MAX(observed_at) AS last_observed_at,
           CAST((julianday(MAX(observed_at)) - julianday(MIN(observed_at))) AS INTEGER) AS days_of_history
    FROM non_dupes
    GROUP BY book_id
),
-- `last_polled_at` is the max observed_at over EVERY row (including dupes
-- that record "we checked and the price was unchanged"). This is what the
-- dashboard's "Last seen" should display — `last_observed_at` from
-- `agg_history` above only moves when the canonical price actually changes.
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

DROP_BOOK_STATS_VIEW_SQL = "DROP VIEW IF EXISTS book_stats"


PRODUCT_STATS_VIEW_SQL = """
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

DROP_PRODUCT_STATS_VIEW_SQL = "DROP VIEW IF EXISTS product_stats"
