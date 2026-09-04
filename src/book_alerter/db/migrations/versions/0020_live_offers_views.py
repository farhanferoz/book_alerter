"""live_offers_views — replace book_stats/product_stats with candidate-only
views; current-best selection moves from SQL into Python.

Plan task T3.1 (2026-09-04 review-and-optimisation plan): `compute_book_stats`
/ `compute_product_stats` ran the whole `book_stats`/`product_stats` view per
item — SQLite materialises every CTE over the WHOLE observation table on each
call, so a 13-book dashboard list cost 13 full-table scans. `book_live_offers`
/ `product_live_offers` keep exactly the freshness-gated candidate logic
(`latest_per_offer` filtered to `rn = 1`) that used to feed `current_best`;
`book_history_summary` / `product_history_summary` keep `agg_history` +
`polled` unchanged. `stats.compute_stats_for_items` now loads candidates +
window observations + history summaries for every requested item in three
batched queries and does the effective-total ranking (and its alphabetical
tie-break) in Python — see that function's docstring.

`title`/`isbn13`/`asin` are dropped from the new views: `compute_*_stats`
never read them from `book_stats`/`product_stats` (the API layer reads them
straight off the Book/Product row it already has), so joining against
{main} was dead weight for this call path.

The `*_live_offers` / `*_history_summary` DDL below is inlined rather than
imported from `db/views.py`, unlike this migration's original version. T3.2
(migration 0021) replaces `is_duplicate_of`-based dedup-folding with a
`last_seen_at` column and repoints `db/views.py`'s "current" constants at
that shape — so a fresh install replaying 0020 needs ITS OWN frozen copy of
the T3.1-era DDL (referencing `is_duplicate_of`, which still exists at this
point in the migration sequence), the same reason 0017/0018/0019 inline
prior-era `book_stats` DDL for their own downgrade paths.
"""

from alembic import op

from book_alerter.db.views import (
    BOOK_STATS_VIEW_SQL,
    DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL,
    DROP_BOOK_LIVE_OFFERS_VIEW_SQL,
    DROP_BOOK_STATS_VIEW_SQL,
    DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL,
    DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL,
    DROP_PRODUCT_STATS_VIEW_SQL,
    PRODUCT_STATS_VIEW_SQL,
)

revision = "0020_live_offers_views"
down_revision = "0019_views_current_best_latest_url"
branch_labels = None
depends_on = None

# T3.1-era DDL (frozen here; see module docstring for why it's inlined
# instead of imported). Identical to what `db/views.py` exported for this
# migration before T3.2 repointed the "current" constants at the
# last_seen_at-based shape.
_BOOK_LIVE_OFFERS_VIEW_SQL = """
CREATE VIEW book_live_offers AS
WITH non_dupes AS (
    SELECT * FROM priceobservation WHERE is_duplicate_of IS NULL
),
buyable_last_seen AS (
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id,
           MAX(observed_at) AS last_seen,
           url AS current_url
    FROM priceobservation
    WHERE source != 'keepa'
    GROUP BY COALESCE(is_duplicate_of, id)
),
live_offers AS (
    SELECT o.book_id, o.source, o.total_minor, o.price_minor, o.shipping_minor,
           o.condition, o.seller, ls.current_url AS url, o.id, ls.last_seen
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
)
SELECT book_id, source, total_minor, price_minor, shipping_minor, condition, seller, url
FROM latest_per_offer
WHERE rn = 1
"""

_PRODUCT_LIVE_OFFERS_VIEW_SQL = """
CREATE VIEW product_live_offers AS
WITH non_dupes AS (
    SELECT * FROM productobservation WHERE is_duplicate_of IS NULL
),
buyable_last_seen AS (
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id,
           MAX(observed_at) AS last_seen,
           url AS current_url
    FROM productobservation
    WHERE source != 'keepa'
    GROUP BY COALESCE(is_duplicate_of, id)
),
live_offers AS (
    SELECT o.product_id, o.source, o.total_minor, o.price_minor, o.shipping_minor,
           o.condition, o.seller, ls.current_url AS url, o.id, ls.last_seen
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
)
SELECT product_id, source, total_minor, price_minor, shipping_minor, condition, seller, url
FROM latest_per_offer
WHERE rn = 1
"""

_BOOK_HISTORY_SUMMARY_VIEW_SQL = """
CREATE VIEW book_history_summary AS
WITH non_dupes AS (
    SELECT * FROM priceobservation WHERE is_duplicate_of IS NULL
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
SELECT ah.book_id, ah.observation_count, ah.last_observed_at, ah.days_of_history,
       pol.last_polled_at
FROM agg_history ah
LEFT JOIN polled pol ON pol.book_id = ah.book_id
"""

_PRODUCT_HISTORY_SUMMARY_VIEW_SQL = """
CREATE VIEW product_history_summary AS
WITH non_dupes AS (
    SELECT * FROM productobservation WHERE is_duplicate_of IS NULL
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
SELECT ah.product_id, ah.observation_count, ah.last_observed_at, ah.days_of_history,
       pol.last_polled_at
FROM agg_history ah
LEFT JOIN polled pol ON pol.product_id = ah.product_id
"""


def upgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)
    op.execute(DROP_PRODUCT_STATS_VIEW_SQL)
    op.execute(_BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(_PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(_BOOK_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(_PRODUCT_HISTORY_SUMMARY_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(BOOK_STATS_VIEW_SQL)
    op.execute(PRODUCT_STATS_VIEW_SQL)
