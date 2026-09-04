"""Property test pinning migration 0021's `last_seen_at` + `url` backfill
formula (plan task T3.2, heartbeat compaction) — the safety net for a
Tier 4 migration that deletes ~78,000 production rows and drops a column.

The migration backfills, on every canonical row (`is_duplicate_of IS NULL`),
`last_seen_at` as `MAX(observed_at)` over its own dedup group (itself plus
every row whose `is_duplicate_of` points at it) AND `url` as that same
group's latest sighting's URL — the same two values the pre-0021
`buyable_last_seen` CTE in `db/views.py` computed via `GROUP BY
COALESCE(is_duplicate_of, id)` (`MAX(observed_at)` plus the bare-column
`url AS current_url`, SQLite's documented rule that a bare column takes its
value from the row that produced a lone MAX()/MIN()). This test runs the
same `UPDATE ... FROM` statement the migration executes (copied here, not
imported — Alembic revision filenames start with a digit, not importable as
a normal module) against randomised dedup groups and asserts the result
matches an independent Python reference model.

A Tier-4 fresh-session review (finding F-B) caught the original version of
this migration backfilling `last_seen_at` correctly while silently
discarding `url` — the canonical row kept its own first-sighting link
instead of the group's latest one, reverting migration 0019. This test's
url column and reference model are what closes that gap: every row in a
group gets a DISTINCT url (`f"https://x/{id}"`), so an assertion that only
checked `last_seen_at`, or that gave every row in a group the same url,
could not have caught it.

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
    last_seen_at TEXT,
    url TEXT NOT NULL
)
"""

# Same `UPDATE ... FROM` statement as migration 0021's
# `_BACKFILL_PRICEOBS_LAST_SEEN_SQL` (see that file for why: the naive
# correlated-subquery form didn't finish in 2+ minutes of CPU time against
# the 90172-row production copy; this joins against a GROUP BY computed
# once and takes ~0.07s there). `url = grp.current_url` is F-B — pulled from
# the same per-group anchor row `last_seen` already comes from, via
# SQLite's bare-column rule.
_BACKFILL_LAST_SEEN_AND_URL_SQL = """
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


@settings(max_examples=50, deadline=None)
@given(offers=st.lists(_offer, min_size=1, max_size=10))
def test_backfill_last_seen_at_and_url_match_latest_sighting_per_group(offers):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(_CREATE_TABLE_SQL)

        next_id = 1
        expected_by_canonical_id: dict[int, tuple[datetime, str]] = {}
        for hours_ago in offers:
            hours = sorted(hours_ago, reverse=True)  # oldest first = canonical
            canonical_id = next_id
            canonical_ts = _BASE - timedelta(hours=hours[0])
            canonical_url = f"https://x/{canonical_id}"
            conn.execute(
                text(
                    "INSERT INTO priceobservation (id, observed_at, is_duplicate_of, url) "
                    "VALUES (:id, :ts, NULL, :url)"
                ),
                {"id": canonical_id, "ts": canonical_ts.isoformat(), "url": canonical_url},
            )
            next_id += 1
            # (timestamp, url, id) triples — id is the tiebreak in case two
            # rows in this group ever land on the exact same timestamp
            # (`hours` is `unique=True` per offer so that shouldn't happen,
            # but pinning a deterministic winner here keeps the reference
            # model well-defined regardless).
            group_sightings = [(canonical_ts, canonical_url, canonical_id)]
            for h in hours[1:]:
                dup_id = next_id
                dup_ts = _BASE - timedelta(hours=h)
                dup_url = f"https://x/{dup_id}"
                conn.execute(
                    text(
                        "INSERT INTO priceobservation "
                        "(id, observed_at, is_duplicate_of, url) "
                        "VALUES (:id, :ts, :dup_of, :url)"
                    ),
                    {
                        "id": dup_id, "ts": dup_ts.isoformat(),
                        "dup_of": canonical_id, "url": dup_url,
                    },
                )
                next_id += 1
                group_sightings.append((dup_ts, dup_url, dup_id))
            latest_ts, latest_url, _latest_id = max(group_sightings)
            expected_by_canonical_id[canonical_id] = (latest_ts, latest_url)

        conn.execute(text(_BACKFILL_LAST_SEEN_AND_URL_SQL))
        rows = conn.execute(
            text(
                "SELECT id, last_seen_at, url FROM priceobservation "
                "WHERE is_duplicate_of IS NULL"
            )
        ).all()

    assert len(rows) == len(offers)
    for canonical_id, backfilled_last_seen, backfilled_url in rows:
        got_last_seen = datetime.fromisoformat(backfilled_last_seen).replace(tzinfo=UTC)
        expected_last_seen, expected_url = expected_by_canonical_id[canonical_id]
        assert got_last_seen == expected_last_seen, (
            f"canonical id {canonical_id}: backfill gave last_seen_at="
            f"{got_last_seen}, expected {expected_last_seen}"
        )
        assert backfilled_url == expected_url, (
            f"canonical id {canonical_id}: backfill gave url={backfilled_url!r}, "
            f"expected {expected_url!r} (the group's latest sighting's url, "
            "not necessarily the canonical row's own)"
        )
