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
"""

from alembic import op

from book_alerter.db.views import (
    BOOK_HISTORY_SUMMARY_VIEW_SQL,
    BOOK_LIVE_OFFERS_VIEW_SQL,
    BOOK_STATS_VIEW_SQL,
    DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL,
    DROP_BOOK_LIVE_OFFERS_VIEW_SQL,
    DROP_BOOK_STATS_VIEW_SQL,
    DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL,
    DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL,
    DROP_PRODUCT_STATS_VIEW_SQL,
    PRODUCT_HISTORY_SUMMARY_VIEW_SQL,
    PRODUCT_LIVE_OFFERS_VIEW_SQL,
    PRODUCT_STATS_VIEW_SQL,
)

revision = "0020_live_offers_views"
down_revision = "0019_views_current_best_latest_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)
    op.execute(DROP_PRODUCT_STATS_VIEW_SQL)
    op.execute(BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(BOOK_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(PRODUCT_HISTORY_SUMMARY_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(BOOK_STATS_VIEW_SQL)
    op.execute(PRODUCT_STATS_VIEW_SQL)
