"""Property test pinning migration 0021's `last_seen_at` backfill formula
(plan task T3.2, heartbeat compaction) — the safety net for a Tier 4
migration that deletes ~78,000 production rows and drops a column.

The migration backfills `last_seen_at` on every canonical row (`is_duplicate_of
IS NULL`) as `MAX(observed_at)` over its own dedup group (itself plus every row
whose `is_duplicate_of` points at it) — the same value the pre-0021
`buyable_last_seen` CTE in `db/views.py` computed via `GROUP BY
COALESCE(is_duplicate_of, id)`. This test runs the same `UPDATE ... FROM`
statement the migration executes (copied here, not imported — Alembic
revision filenames start with a digit, not importable as a normal module)
against randomised dedup groups and asserts the result matches an
independent Python reference model.

Deliberately independent of `book_alerter.db.models.PriceObservation` (a bare
`CREATE TABLE` with just the three columns the formula touches) rather than
`engine_with_view` — the whole point of this test is to keep proving the
formula after migration 0021 removes `is_duplicate_of`/adds `last_seen_at`
from the live schema; tying it to the evolving ORM model would make it
unrunnable the moment that happens, exactly when a Tier 4 migration's safety
net matters most.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, text

_BASE = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)

# One offer = one dedup group: a canonical row plus zero or more duplicates,
# each at a distinct whole-hour offset. Whole hours avoid sub-second
# datetime-string formatting differences between Python and SQLite's
# MAX(observed_at) text comparison.
_offer = st.lists(
    st.integers(min_value=0, max_value=8760),  # hours_ago, within a year
    min_size=1, max_size=6, unique=True,
)

_CREATE_TABLE_SQL = """
CREATE TABLE priceobservation (
    id INTEGER PRIMARY KEY,
    observed_at TEXT NOT NULL,
    is_duplicate_of INTEGER,
    last_seen_at TEXT
)
"""

# Same `UPDATE ... FROM` statement as migration 0021's
# `_BACKFILL_PRICEOBS_LAST_SEEN_SQL` (see that file for why: the naive
# correlated-subquery form didn't finish in 2+ minutes of CPU time against
# the 90172-row production copy; this joins against a GROUP BY computed
# once and takes ~0.07s there).
_BACKFILL_LAST_SEEN_SQL = """
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


@settings(max_examples=50, deadline=None)
@given(offers=st.lists(_offer, min_size=1, max_size=10))
def test_backfill_last_seen_at_matches_max_observed_at_per_group(offers):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(_CREATE_TABLE_SQL)

        next_id = 1
        expected_by_canonical_id: dict[int, datetime] = {}
        for hours_ago in offers:
            hours = sorted(hours_ago, reverse=True)  # oldest first = canonical
            canonical_id = next_id
            canonical_ts = _BASE - timedelta(hours=hours[0])
            conn.execute(
                text(
                    "INSERT INTO priceobservation (id, observed_at, is_duplicate_of) "
                    "VALUES (:id, :ts, NULL)"
                ),
                {"id": canonical_id, "ts": canonical_ts.isoformat()},
            )
            next_id += 1
            group_timestamps = [canonical_ts]
            for h in hours[1:]:
                dup_ts = _BASE - timedelta(hours=h)
                conn.execute(
                    text(
                        "INSERT INTO priceobservation (id, observed_at, is_duplicate_of) "
                        "VALUES (:id, :ts, :dup_of)"
                    ),
                    {"id": next_id, "ts": dup_ts.isoformat(), "dup_of": canonical_id},
                )
                next_id += 1
                group_timestamps.append(dup_ts)
            expected_by_canonical_id[canonical_id] = max(group_timestamps)

        conn.execute(text(_BACKFILL_LAST_SEEN_SQL))
        rows = conn.execute(
            text(
                "SELECT id, last_seen_at FROM priceobservation "
                "WHERE is_duplicate_of IS NULL"
            )
        ).all()

    assert len(rows) == len(offers)
    for canonical_id, backfilled in rows:
        got = datetime.fromisoformat(backfilled).replace(tzinfo=UTC)
        assert got == expected_by_canonical_id[canonical_id], (
            f"canonical id {canonical_id}: backfill gave {got}, "
            f"expected {expected_by_canonical_id[canonical_id]}"
        )
