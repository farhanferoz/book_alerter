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
heartbeat ROWS are gone; that loss is accepted and documented, D15). SQLite
refuses the batch-table-rebuild dance while a dependent view exists
(migration 0013's lesson), so views are dropped before each table's batch
rebuild and recreated after.

**Round-trip fix (Tier 4 fresh-session review, finding F-A).** The first
version of this migration dropped `last_seen_at` on downgrade with no
reconstruction path. That is a DIFFERENT loss than D15 accepts: D15 gives up
the per-sighting heartbeat ROWS; this was silently also giving up
`last_seen_at` ITSELF — a value already computed and sitting on the surviving
canonical row, not something that needs the deleted rows to reconstruct.
Measured on a production copy: `upgrade head -> downgrade 0019 -> upgrade
head` dropped `book_live_offers` from 212 rows to 24 and flipped 7 books'
signals, because the live-offers view gates freshness on
`last_seen_at == MAX(last_seen_at)` and a second upgrade's backfill — run
against an all-NULL `is_duplicate_of` because the heartbeats that would have
formed real groups are gone — collapses every row's group to itself, so
`last_seen_at` regresses to `observed_at` (first sighting). Fixed by having
`downgrade()` snapshot `(id, last_seen_at)` into a side table
(`_lastseen_backup_{table}`) before dropping the column; `upgrade()` checks
for that table and restores from it exactly when present, falling back to
the original group-max formula only when it is not (i.e. a genuine
first-time upgrade, fresh install or real production data) — that formula
is UNCHANGED, still the one the property test and the Tier-4 review
verified against 12,337 production rows with 0 mismatches.

**URL fix (finding F-B).** The pre-0021 `buyable_last_seen` CTE computed TWO
things per dedup group via SQLite's documented bare-column rule (exactly one
MAX() aggregate present -> every bare column takes its value from the row
that produced the max): `MAX(observed_at)` AND `url AS current_url` (the
latest sighting's link). This migration backfilled only the first and threw
the second away when it deleted the heartbeat rows that carried it, so the
canonical row kept whatever URL its FIRST sighting happened to have —
exactly what migration 0019 exists to prevent (see the comment on
`buyable_last_seen` in `db/views.py`). Measured on a production copy: 187
canonical rows' URLs differed from their latest sighting's; the worst case
pointed at an Amazon help page instead of the offer-listing page. Fixed by
adding `url = grp.current_url` to the same backfill query, using the same
per-group anchor row `last_seen_at` already comes from — one bare-column
trick, two columns pulled from it, matching what `buyable_last_seen` did in
one query. Self-heals per offer on the next scrape either way
(`scheduler._persist` sets `prior.url = c.url`); this closes the gap for
offers that are never re-seen.

**Documented behaviour change (finding F-D).** Pre-0021, the shipping-cascade
medians (`stats.source_seller_global_shipping_medians` /
`book_source_medians`) were fed by every row INCLUDING heartbeats, weighting
each bucket by how many times an offer was RE-SEEN. Post-0021 every distinct
offer counts once, so slow-moving-but-frequently-rescraped offers lose their
outsized weight. Measured on a production copy: this is a real, sometimes
large shift in the LONG-window (12m) percentile fields feeding `WindowStats`
— book 6's 12-month `rank` moved 41 -> 1 and its p25/p50/p75 moved
+64%/+45%/+29%, driven by `bookfinder`'s known-shipping rows being 66.3%
zero when heartbeats are counted but only 22.1% when they are not (a
bimodal 0-vs-~£14.80 distribution, so the median jumps a cliff rather than
drifting). Row counts (`n`) are identical in every window and no book's
90-day-configured `Signal` changes — this is a display-only effect on the
long window's percentile summary, not a correctness defect — but it rides
along with `min_global_median_observations` dropping 10 -> 5 (which,
measured separately, is a no-op on this data: the same buckets qualify at
both thresholds either way).
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

# F-A: side tables `downgrade()` snapshots `(id, last_seen_at)` into before
# dropping the column, so a subsequent `upgrade()` can restore the exact
# prior values instead of re-deriving them from `is_duplicate_of` groups
# that no longer exist (the heartbeat rows those groups depended on were
# already deleted by the FIRST upgrade — D15's accepted loss — so a second
# derivation from the same formula silently produces a different, wrong
# answer, not the same one). Named with a leading underscore and this
# migration's revision id so an operator inspecting the schema mid-rollback
# can tell at a glance it's migration-internal scratch state, not a domain
# table.
_PRICEOBS_LASTSEEN_STASH = "_0021_lastseen_backup_priceobservation"
_PRODUCTOBS_LASTSEEN_STASH = "_0021_lastseen_backup_productobservation"


def _table_exists(bind: sa.engine.Connection, name: str) -> bool:
    row = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": name},
    ).fetchone()
    return row is not None


# Backfill formula, pinned by the property test named in the module
# docstring: for every canonical row, MAX(observed_at) over itself plus
# every row whose is_duplicate_of points at it. Runs on a genuine first-time
# upgrade only (fresh install, or real production data with actual
# is_duplicate_of groups) — a re-upgrade after a downgrade restores from the
# stash tables above instead, since this formula can no longer see the
# (already-deleted) heartbeat rows it needs.
# `UPDATE ... FROM` (SQLite 3.33+) joins against a GROUP BY computed ONCE,
# not a correlated subquery re-scanning the table per canonical row — the
# naive correlated-subquery form (`SET x = (SELECT MAX(...) WHERE
# COALESCE(...) = outer.id)`) can't use an index on the COALESCE expression
# and re-aggregates per row; on the production copy (90172 rows) that
# version didn't finish in 2+ minutes of CPU time, this one takes ~0.07s
# (measured, not assumed — COALESCE prevents index use either way, so the
# win is doing the GROUP BY once instead of once per canonical row). The
# `last_seen_at` half of this formula is UNCHANGED from the version the
# property test and the Tier-4 review verified against 12,337 production
# rows with 0 mismatches — only `url` is new (F-B), pulled from the SAME
# per-group anchor row via SQLite's documented bare-column rule (exactly
# one MAX() aggregate present -> every bare column takes its value from the
# row that produced the max), exactly how the pre-0021 `buyable_last_seen`
# CTE computed `url AS current_url` alongside `MAX(observed_at)` in one
# query. Verified empirically before use (not assumed): a group of 3 rows
# with distinct urls and observed_at values backfills the group's
# max-observed_at row's url onto every row in the group.
_BACKFILL_PRICEOBS_LAST_SEEN_SQL = """
UPDATE priceobservation
SET last_seen_at = grp.last_seen,
    url = grp.current_url
FROM (
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id,
           MAX(observed_at) AS last_seen,
           url AS current_url
    FROM priceobservation
    GROUP BY COALESCE(is_duplicate_of, id)
) AS grp
WHERE priceobservation.id = grp.canonical_id
  AND priceobservation.is_duplicate_of IS NULL
"""
_BACKFILL_PRODUCTOBS_LAST_SEEN_SQL = """
UPDATE productobservation
SET last_seen_at = grp.last_seen,
    url = grp.current_url
FROM (
    SELECT COALESCE(is_duplicate_of, id) AS canonical_id,
           MAX(observed_at) AS last_seen,
           url AS current_url
    FROM productobservation
    GROUP BY COALESCE(is_duplicate_of, id)
) AS grp
WHERE productobservation.id = grp.canonical_id
  AND productobservation.is_duplicate_of IS NULL
"""

# F-A restore path: an exact copy of what downgrade() stashed, keyed by id
# (not by any group formula — the whole point is that the group can no
# longer be reconstructed). Only ever runs when the stash table exists,
# i.e. only on a re-upgrade after a downgrade.
_RESTORE_PRICEOBS_LAST_SEEN_FROM_STASH_SQL = f"""
UPDATE priceobservation
SET last_seen_at = stash.last_seen_at
FROM {_PRICEOBS_LASTSEEN_STASH} AS stash
WHERE priceobservation.id = stash.id
"""
_RESTORE_PRODUCTOBS_LAST_SEEN_FROM_STASH_SQL = f"""
UPDATE productobservation
SET last_seen_at = stash.last_seen_at
FROM {_PRODUCTOBS_LASTSEEN_STASH} AS stash
WHERE productobservation.id = stash.id
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

    bind = op.get_bind()
    price_has_stash = _table_exists(bind, _PRICEOBS_LASTSEEN_STASH)
    product_has_stash = _table_exists(bind, _PRODUCTOBS_LASTSEEN_STASH)

    # --- priceobservation ---
    op.add_column("priceobservation", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    # Always runs first: correct on a genuine first-time upgrade (real
    # is_duplicate_of groups), and a harmless no-op for last_seen_at on a
    # re-upgrade (every group has collapsed to a singleton, so it just sets
    # last_seen_at = observed_at) that the stash restore below immediately
    # corrects. `url` is right either way — its own group is unaffected by
    # whether this is a first upgrade or a re-upgrade.
    op.execute(_BACKFILL_PRICEOBS_LAST_SEEN_SQL)
    if price_has_stash:
        # F-A: a re-upgrade after a downgrade. Restore the exact prior
        # last_seen_at values instead of trusting the group-max formula
        # above, which can no longer see the (already-deleted) heartbeat
        # rows those values used to be derived from.
        op.execute(_RESTORE_PRICEOBS_LAST_SEEN_FROM_STASH_SQL)
        op.execute(f"DROP TABLE {_PRICEOBS_LASTSEEN_STASH}")
    op.execute(_DELETE_PRICEOBS_DUPLICATES_SQL)
    with op.batch_alter_table("priceobservation", naming_convention=_NAMING) as batch_op:
        batch_op.alter_column("last_seen_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.drop_constraint(_PRICEOBS_DUP_FK, type_="foreignkey")
        batch_op.drop_column("is_duplicate_of")
        batch_op.drop_index("ix_priceobservation_book_id")
        batch_op.create_index(
            "ix_obs_book_source_lastseen", ["book_id", "source", "last_seen_at"],
        )

    # --- productobservation --- (mirrors priceobservation exactly)
    op.add_column("productobservation", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.execute(_BACKFILL_PRODUCTOBS_LAST_SEEN_SQL)
    if product_has_stash:
        op.execute(_RESTORE_PRODUCTOBS_LAST_SEEN_FROM_STASH_SQL)
        op.execute(f"DROP TABLE {_PRODUCTOBS_LASTSEEN_STASH}")
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

    # F-A: snapshot last_seen_at before it's dropped below, so a subsequent
    # upgrade() can restore it exactly rather than re-deriving it from
    # is_duplicate_of groups this downgrade is about to make meaningless
    # (every row goes back to is_duplicate_of=NULL, i.e. its own singleton
    # group). `DROP ... IF EXISTS` first so repeated downgrade calls without
    # an intervening upgrade stay idempotent rather than erroring on a
    # leftover stash from a previous cycle.
    op.execute(f"DROP TABLE IF EXISTS {_PRICEOBS_LASTSEEN_STASH}")
    op.execute(
        f"CREATE TABLE {_PRICEOBS_LASTSEEN_STASH} AS "
        "SELECT id, last_seen_at FROM priceobservation"
    )
    op.execute(f"DROP TABLE IF EXISTS {_PRODUCTOBS_LASTSEEN_STASH}")
    op.execute(
        f"CREATE TABLE {_PRODUCTOBS_LASTSEEN_STASH} AS "
        "SELECT id, last_seen_at FROM productobservation"
    )

    # Heartbeat ROWS cannot be recovered — every row downgrades to
    # is_duplicate_of=NULL (all "canonical"), documented data loss (D15).
    # last_seen_at itself is NOT lost, per the stash above (F-A).
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
