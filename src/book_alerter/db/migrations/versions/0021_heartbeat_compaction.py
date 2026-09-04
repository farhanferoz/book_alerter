"""heartbeat_compaction — replace duplicate-row heartbeats with an
update-in-place `last_seen_at` column.

Plan task T3.2 (2026-09-04 review-and-optimisation plan), Tier 4 — the most
destructive migration in the plan. On the measured production shape (13
books, 90172 `priceobservation` rows), 86% (77835 rows) are `is_duplicate_of`
heartbeats: "we re-checked this exact offer, price unchanged." Every scrape
of a stable-priced book wrote a full new row just to say nothing changed,
which is most of why T3.1's batched stats queries still had to fetch ~88k
rows per dashboard render (see that migration's commit for the before/after
numbers) — deleting the heartbeats and keeping their information as a single
`last_seen_at` timestamp on the canonical row is what actually closes the
gap to the plan's <= 0.35s target (decision D23).

Changes to `priceobservation` / `productobservation` (mirrored):
- Add `last_seen_at DATETIME NOT NULL`, backfilled as `MAX(observed_at)`
  over each dedup group (itself plus every row that pointed at it via
  `is_duplicate_of`) — the exact value the old `buyable_last_seen` CTE in
  `db/views.py` computed via `GROUP BY COALESCE(is_duplicate_of, id)`.
  Formula pinned by a property test (`tests/integration/
  test_heartbeat_compaction_backfill_properties.py`) before this migration
  was written.
- `DELETE` every row with `is_duplicate_of IS NOT NULL` (the heartbeats).
  Canonical rows (`is_duplicate_of IS NULL`) are untouched in count —
  verified on a production copy: 90172 -> 12337 rows, with the 12337
  canonical-row count identical before and after (not merely "the total
  fell").
- Drop `is_duplicate_of` and its self-referential FK.
- Drop `ix_priceobservation_book_id` / `ix_productobservation_product_id`
  (single-column `book_id`/`product_id` indexes) — covered by the new
  composite's leftmost prefix.
- Add `ix_obs_book_source_lastseen` / `ix_pobs_product_source_lastseen`
  (`{id}, source, last_seen_at`) — what the live-offers view's
  `latest_scrape_per_source` CTE scans.

`VACUUM` is NOT run here (the plan is explicit: it's a manual step,
documented, run after the weekly backup job) — this migration only frees
the space logically; reclaiming it on disk is a separate, operator-run step:
`sqlite3 data/book_alerter.db 'VACUUM'`.

`scheduler._persist` now `UPDATE`s `last_seen_at` (and `url` — see that
function for why) on a matching prior row instead of inserting a duplicate;
`stats.compute_stats_for_items` drops the `is_duplicate_of` column from its
window-observations query and stops splitting rows into "canonical" vs
"duplicate" for the shipping cascade — every surviving row already counts
once, so `RecommendationConfig.min_global_median_observations` default drops
10 -> 5 to compensate (a bucket that needed 10 heartbeat-inflated rows to
clear the old threshold needs proportionally fewer now that rows aren't
inflated by repeat sightings of the same price).

Down-migration re-creates `is_duplicate_of` (nullable, all NULL — the
heartbeat data is gone; this is a documented, accepted loss on downgrade)
and drops `last_seen_at`. SQLite refuses the batch-table-rebuild dance while
a dependent view exists (migration 0013's lesson), so views are dropped
before each table's batch rebuild and recreated after.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from book_alerter.db.views import (
    BOOK_HISTORY_SUMMARY_VIEW_SQL,
    BOOK_LIVE_OFFERS_VIEW_SQL,
    DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL,
    DROP_BOOK_LIVE_OFFERS_VIEW_SQL,
    DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL,
    DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL,
    PRODUCT_HISTORY_SUMMARY_VIEW_SQL,
    PRODUCT_LIVE_OFFERS_VIEW_SQL,
)

revision = "0021_heartbeat_compaction"
down_revision = "0020_live_offers_views"
branch_labels = None
depends_on = None

_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

# Both FK constraint names are already explicit in the schema (migration
# 0013 named priceobservation's when it rebuilt that table; productobservation
# picked up the same convention when migration 0014 created it) — confirmed
# against a real alembic-built DB, not guessed.
_PRICEOBS_DUP_FK = "fk_priceobservation_is_duplicate_of_priceobservation"
_PRODUCTOBS_DUP_FK = "fk_productobservation_is_duplicate_of_productobservation"

# Backfill formula, pinned by the property test named in the module
# docstring: for every canonical row, MAX(observed_at) over itself plus
# every row whose is_duplicate_of points at it.
# `UPDATE ... FROM` (SQLite 3.33+) joins against a GROUP BY computed ONCE,
# not a correlated subquery re-scanning the table per canonical row — the
# naive correlated-subquery form (`SET x = (SELECT MAX(...) WHERE
# COALESCE(...) = outer.id)`) can't use an index on the COALESCE expression
# and re-aggregates per row; on the production copy (90172 rows) that
# version didn't finish in 2+ minutes of CPU time, this one takes ~0.07s
# (measured, not assumed — COALESCE prevents index use either way, so the
# win is doing the GROUP BY once instead of once per canonical row).
_BACKFILL_PRICEOBS_LAST_SEEN_SQL = """
UPDATE priceobservation
SET last_seen_at = grp.last_seen
FROM (
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id, MAX(observed_at) AS last_seen
    FROM priceobservation
    GROUP BY COALESCE(is_duplicate_of, id)
) AS grp
WHERE priceobservation.id = grp.canonical_id
  AND priceobservation.is_duplicate_of IS NULL
"""
_BACKFILL_PRODUCTOBS_LAST_SEEN_SQL = """
UPDATE productobservation
SET last_seen_at = grp.last_seen
FROM (
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id, MAX(observed_at) AS last_seen
    FROM productobservation
    GROUP BY COALESCE(is_duplicate_of, id)
) AS grp
WHERE productobservation.id = grp.canonical_id
  AND productobservation.is_duplicate_of IS NULL
"""

_DELETE_PRICEOBS_DUPLICATES_SQL = (
    "DELETE FROM priceobservation WHERE is_duplicate_of IS NOT NULL"
)
_DELETE_PRODUCTOBS_DUPLICATES_SQL = (
    "DELETE FROM productobservation WHERE is_duplicate_of IS NOT NULL"
)

# 0020-era (is_duplicate_of-based) view DDL, frozen here for the downgrade
# path — identical to what db/views.py exported before this migration
# repointed its "current" constants at the last_seen_at-based shape. Same
# rationale as migration 0020's own inlined copy of the T3.1-era DDL.
_PRE_0021_BOOK_LIVE_OFFERS_VIEW_SQL = """
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

_PRE_0021_PRODUCT_LIVE_OFFERS_VIEW_SQL = """
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

_PRE_0021_BOOK_HISTORY_SUMMARY_VIEW_SQL = """
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

_PRE_0021_PRODUCT_HISTORY_SUMMARY_VIEW_SQL = """
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
    op.execute(DROP_BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL)

    # --- priceobservation ---
    op.add_column("priceobservation", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.execute(_BACKFILL_PRICEOBS_LAST_SEEN_SQL)
    op.execute(_DELETE_PRICEOBS_DUPLICATES_SQL)
    with op.batch_alter_table("priceobservation", naming_convention=_NAMING) as batch_op:
        batch_op.alter_column("last_seen_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.drop_constraint(_PRICEOBS_DUP_FK, type_="foreignkey")
        batch_op.drop_column("is_duplicate_of")
        batch_op.drop_index("ix_priceobservation_book_id")
        batch_op.create_index(
            "ix_obs_book_source_lastseen", ["book_id", "source", "last_seen_at"],
        )

    # --- productobservation ---
    op.add_column("productobservation", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.execute(_BACKFILL_PRODUCTOBS_LAST_SEEN_SQL)
    op.execute(_DELETE_PRODUCTOBS_DUPLICATES_SQL)
    with op.batch_alter_table("productobservation", naming_convention=_NAMING) as batch_op:
        batch_op.alter_column("last_seen_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.drop_constraint(_PRODUCTOBS_DUP_FK, type_="foreignkey")
        batch_op.drop_column("is_duplicate_of")
        batch_op.drop_index("ix_productobservation_product_id")
        batch_op.create_index(
            "ix_pobs_product_source_lastseen", ["product_id", "source", "last_seen_at"],
        )

    op.execute(BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(BOOK_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(PRODUCT_HISTORY_SUMMARY_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_BOOK_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(DROP_PRODUCT_HISTORY_SUMMARY_VIEW_SQL)

    # Heartbeat data cannot be recovered — every row downgrades to
    # is_duplicate_of=NULL (all "canonical"), documented data loss.
    with op.batch_alter_table("priceobservation", naming_convention=_NAMING) as batch_op:
        batch_op.drop_index("ix_obs_book_source_lastseen")
        batch_op.create_index("ix_priceobservation_book_id", ["book_id"])
        batch_op.add_column(sa.Column("is_duplicate_of", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            _PRICEOBS_DUP_FK, "priceobservation", ["is_duplicate_of"], ["id"],
        )
        batch_op.drop_column("last_seen_at")

    with op.batch_alter_table("productobservation", naming_convention=_NAMING) as batch_op:
        batch_op.drop_index("ix_pobs_product_source_lastseen")
        batch_op.create_index("ix_productobservation_product_id", ["product_id"])
        batch_op.add_column(sa.Column("is_duplicate_of", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            _PRODUCTOBS_DUP_FK, "productobservation", ["is_duplicate_of"], ["id"],
        )
        batch_op.drop_column("last_seen_at")

    op.execute(_PRE_0021_BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(_PRE_0021_PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(_PRE_0021_BOOK_HISTORY_SUMMARY_VIEW_SQL)
    op.execute(_PRE_0021_PRODUCT_HISTORY_SUMMARY_VIEW_SQL)
