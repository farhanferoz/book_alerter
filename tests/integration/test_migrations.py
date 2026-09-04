"""Migration end-to-end: every revision applies on a fresh DB, the final
PRAGMA foreign_key_check is clean, and downgrade then upgrade brings the
schema back to a working state.

Runs against `tmp_path` SQLite DBs — never touches `data/book_alerter.db`.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
PRODUCT_HEAD = "0016_product_stats_view"
PRE_PRODUCT_HEAD = "0013_fk_cascade_on_book_delete"


@contextmanager
def _alembic_pointing_at(db_path: Path) -> Iterator[AlembicConfig]:
    """Override the alembic env.py's database URL via `BOOK_ALERTER_DATABASE_URL`
    so commands run against `db_path` instead of `data/book_alerter.db`."""
    saved = os.environ.get("BOOK_ALERTER_DATABASE_URL")
    os.environ["BOOK_ALERTER_DATABASE_URL"] = f"sqlite:///{db_path}"
    try:
        cfg = AlembicConfig(str(ALEMBIC_INI))
        cfg.set_main_option("script_location", str(REPO_ROOT / "src/book_alerter/db/migrations"))
        yield cfg
    finally:
        if saved is None:
            os.environ.pop("BOOK_ALERTER_DATABASE_URL", None)
        else:
            os.environ["BOOK_ALERTER_DATABASE_URL"] = saved


def _fk_violations(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as con:
        cur = con.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA foreign_key_check")
        return cur.fetchall()


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {r[0] for r in cur.fetchall()}


def test_upgrade_to_head_on_fresh_db(tmp_path: Path) -> None:
    """Cold install — every revision applies in order."""
    db_path = tmp_path / "fresh.db"
    with _alembic_pointing_at(db_path) as cfg:
        alembic_command.upgrade(cfg, "head")
    assert _fk_violations(db_path) == []
    tables = _table_names(db_path)
    # All product tables landed.
    product_tables = {"product", "productobservation", "productalert", "productsignalstate"}
    assert product_tables <= tables
    # Book tables also still present (no migration accidentally dropped them).
    book_tables = {
        "book", "priceobservation", "alert", "notificationdelivery", "booksignalstate",
    }
    assert book_tables <= tables


def test_full_downgrade_then_upgrade_round_trip(tmp_path: Path) -> None:
    """Apply everything; downgrade to pre-products; reapply. Round-trip clean."""
    db_path = tmp_path / "round.db"
    with _alembic_pointing_at(db_path) as cfg:
        alembic_command.upgrade(cfg, "head")
        alembic_command.downgrade(cfg, PRE_PRODUCT_HEAD)
        tables = _table_names(db_path)
        assert "product" not in tables
        assert "productobservation" not in tables
        assert "productalert" not in tables
        assert "productsignalstate" not in tables
        # NotificationDelivery should be back to NOT NULL alert_id, no
        # product_alert_id column. Check via the table SQL.
        with sqlite3.connect(db_path) as con:
            cur = con.cursor()
            cur.execute("SELECT sql FROM sqlite_master WHERE name='notificationdelivery'")
            sql = cur.fetchone()[0]
        assert "product_alert_id" not in sql
        assert "ck_notificationdelivery_alert_xor_product" not in sql

        # And upgrade brings everything back, FK-clean.
        alembic_command.upgrade(cfg, "head")
        assert _fk_violations(db_path) == []
        tables_after = _table_names(db_path)
        product_tables = {"product", "productobservation", "productalert", "productsignalstate"}
        assert product_tables <= tables_after


@pytest.mark.parametrize(
    "revision",
    [
        "0014_product_tables",
        "0015_notif_delivery_polymorphic",
        "0016_product_stats_view",
        "0021_heartbeat_compaction",
        "0024_live_offers_deterministic_tiebreak",
    ],
)
def test_each_revision_individually(tmp_path: Path, revision: str) -> None:
    """Apply up to `revision`, then back down one step, then up again — each
    individual migration must downgrade and reapply cleanly."""
    db_path = tmp_path / f"{revision}.db"
    with _alembic_pointing_at(db_path) as cfg:
        alembic_command.upgrade(cfg, revision)
        # Go one step back and forward again.
        alembic_command.downgrade(cfg, "-1")
        alembic_command.upgrade(cfg, revision)
    assert _fk_violations(db_path) == []


def _seed_book_and_observations(db_path: Path) -> None:
    """A book with two dedup groups: 3 sightings of one offer (1 canonical +
    2 heartbeats) and 1 lone offer (canonical, no heartbeats) — enough to
    exercise both the group-max backfill and the no-duplicates case.

    The 3-row group's URLs deliberately DIFFER (id 3, the newest sighting,
    carries a different link than id 1, the canonical/first-sighting row) —
    a Tier-4 review (F-B) found the pre-fix migration backfilled
    `last_seen_at` from the group's latest sighting but silently kept the
    CANONICAL row's own (oldest) URL, reverting migration 0019. Giving every
    row the same URL, as this fixture used to, cannot catch that: the
    assertion would pass whichever row's URL survived.
    """
    with sqlite3.connect(db_path) as con:
        now = "2026-09-04 12:00:00"
        con.execute(
            "INSERT INTO book (id, isbn13, title, author, format, region, "
            "currency, status, created_at, updated_at) "
            "VALUES (1, '9780000000001', 't', 'a', 'any', 'UK', 'GBP', 'active', ?, ?)",
            (now, now),
        )
        rows = [
            # id, source, price, url, observed_at, is_duplicate_of
            (1, "wob", 1000, "https://x/first-sighting", "2026-08-01 10:00:00", None),
            (2, "wob", 1000, "https://x/mid-sighting", "2026-08-02 10:00:00", 1),
            (3, "wob", 1000, "https://x/latest-sighting", "2026-08-03 10:00:00", 1),  # newest
            (4, "amazon", 2000, "https://y", "2026-08-01 09:00:00", None),  # no duplicates
        ]
        for id_, source, price, url, observed_at, dup_of in rows:
            con.execute(
                "INSERT INTO priceobservation (id, book_id, source, condition, "
                "price_minor, currency, shipping_minor, total_minor, url, "
                "observed_at, raw, is_duplicate_of) "
                "VALUES (?, 1, ?, 'new', ?, 'GBP', 0, ?, ?, ?, '{}', ?)",
                (id_, source, price, price, url, observed_at, dup_of),
            )
        con.commit()


def test_heartbeat_compaction_row_counts_and_last_seen_at(tmp_path: Path) -> None:
    """T3.2 non-negotiables: canonical rows survive 1:1 (not merely "the
    total fell"), heartbeats are gone, `last_seen_at` backfills to
    MAX(observed_at) per dedup group — the value the pre-0021
    `buyable_last_seen` CTE computed — and (F-B, Tier-4 review) the
    canonical row's `url` becomes the LATEST sighting's, not the first."""
    db_path = tmp_path / "heartbeat_compaction.db"
    with _alembic_pointing_at(db_path) as cfg:
        alembic_command.upgrade(cfg, "0020_live_offers_views")
        _seed_book_and_observations(db_path)
        with sqlite3.connect(db_path) as con:
            canonical_before = con.execute(
                "SELECT COUNT(*) FROM priceobservation WHERE is_duplicate_of IS NULL"
            ).fetchone()[0]
            total_before = con.execute("SELECT COUNT(*) FROM priceobservation").fetchone()[0]
        assert canonical_before == 2
        assert total_before == 4

        alembic_command.upgrade(cfg, "0021_heartbeat_compaction")

    assert _fk_violations(db_path) == []
    with sqlite3.connect(db_path) as con:
        total_after = con.execute("SELECT COUNT(*) FROM priceobservation").fetchone()[0]
        assert total_after == canonical_before, (
            f"canonical count must survive 1:1: had {canonical_before} canonical "
            f"rows, {total_after} rows remain after compaction"
        )
        rows = {
            r[0]: (r[1], r[2])
            for r in con.execute(
                "SELECT id, last_seen_at, url FROM priceobservation ORDER BY id"
            ).fetchall()
        }
    assert rows == {
        # MAX(observed_at) over its group (id 1, 2, 3) -> id 3's timestamp,
        # and (F-B) id 3's url, not id 1's own first-sighting url.
        1: ("2026-08-03 10:00:00", "https://x/latest-sighting"),
        4: ("2026-08-01 09:00:00", "https://y"),  # no duplicates -> its own row
    }


def test_heartbeat_compaction_round_trip_preserves_last_seen_and_url(
    tmp_path: Path,
) -> None:
    """F-A + F-B (Tier-4 review of Wave 3): a downgrade -> upgrade round
    trip must not lose information the FIRST upgrade already extracted from
    the (now-deleted) heartbeat rows.

    Pre-fix, `downgrade()` dropped `last_seen_at` with no reconstruction
    path. On the SECOND `upgrade()`, the backfill's `GROUP BY
    COALESCE(is_duplicate_of, id)` groups have collapsed to singletons
    (the heartbeats that made them real groups are gone — deleted by the
    FIRST upgrade), so `last_seen_at` silently regresses to `observed_at`
    (first sighting) instead of staying at the group's true latest sighting.
    Measured on a production copy in the Tier-4 review: `book_live_offers`
    fell from 212 rows to 24 and 7 books' signals flipped. This test
    reproduces the same MECHANISM (a multi-row dedup group whose canonical
    row's `observed_at` differs from the group's latest sighting) on a
    tiny, deterministic fixture rather than needing a production snapshot.
    """
    db_path = tmp_path / "round_trip.db"
    with _alembic_pointing_at(db_path) as cfg:
        alembic_command.upgrade(cfg, "0020_live_offers_views")
        _seed_book_and_observations(db_path)

        alembic_command.upgrade(cfg, "head")
        with sqlite3.connect(db_path) as con:
            after_first_upgrade = {
                r[0]: (r[1], r[2])
                for r in con.execute(
                    "SELECT id, last_seen_at, url FROM priceobservation ORDER BY id"
                ).fetchall()
            }

        alembic_command.downgrade(cfg, "0020_live_offers_views")
        alembic_command.upgrade(cfg, "head")
        with sqlite3.connect(db_path) as con:
            after_round_trip = {
                r[0]: (r[1], r[2])
                for r in con.execute(
                    "SELECT id, last_seen_at, url FROM priceobservation ORDER BY id"
                ).fetchall()
            }

    assert _fk_violations(db_path) == []
    assert after_round_trip == after_first_upgrade, (
        "a downgrade -> upgrade round trip must reproduce the same "
        f"last_seen_at/url as the first upgrade; first={after_first_upgrade!r} "
        f"round-trip={after_round_trip!r}"
    )


def _seed_source_runs(db_path: Path) -> None:
    """Two finished `sourcerun` rows written before 0022 existed — the case
    that matters, since every row already in production predates the column."""
    with sqlite3.connect(db_path) as con:
        for id_, source, status, attempted, succeeded in [
            (1, "amazon", "ok", 12, 12),
            (2, "amazon", "partial", 12, 4),
        ]:
            con.execute(
                "INSERT INTO sourcerun (id, source, started_at, finished_at, "
                "status, books_attempted, books_succeeded) "
                "VALUES (?, ?, '2026-09-04 12:00:00', '2026-09-04 12:01:00', ?, ?, ?)",
                (id_, source, status, attempted, succeeded),
            )
        con.commit()


def test_items_challenged_backfills_to_zero_and_is_not_nullable(tmp_path: Path) -> None:
    """T1.3 (migration 0022). Two properties, both about rows that already
    exist when the column arrives:

    1. Every pre-existing run gets `items_challenged = 0` — NOT NULL. A NULL
       here would silently poison `_apply_backoff`'s >=50%-challenged rule and
       the T6.1 dashboard banner, both of which do arithmetic on this column.
    2. The column is genuinely NOT NULL, so a future INSERT that forgets it
       fails loudly at write time rather than storing a NULL that reads as
       "zero challenges" everywhere downstream.

    Zero is the honest backfill precisely because it is not a measurement:
    runs that predate the counter never counted challenges, and 0 is what
    "we have no evidence of a challenge" already means for this column.
    """
    db_path = tmp_path / "items_challenged.db"
    with _alembic_pointing_at(db_path) as cfg:
        alembic_command.upgrade(cfg, "0021_heartbeat_compaction")
        _seed_source_runs(db_path)
        alembic_command.upgrade(cfg, "head")

    with sqlite3.connect(db_path) as con:
        cur = con.cursor()
        cols = {r[1]: r for r in cur.execute("PRAGMA table_info(sourcerun)")}
        assert "items_challenged" in cols, "0022 did not add the column"
        # PRAGMA table_info: index 3 is `notnull`.
        assert cols["items_challenged"][3] == 1, "items_challenged must be NOT NULL"

        rows = cur.execute(
            "SELECT id, items_challenged FROM sourcerun ORDER BY id"
        ).fetchall()
        assert rows == [(1, 0), (2, 0)], rows

        # The pre-existing columns must be untouched by the table rebuild
        # SQLite performs for an ALTER on a table with constraints.
        assert cur.execute(
            "SELECT books_attempted, books_succeeded FROM sourcerun WHERE id = 2"
        ).fetchone() == (12, 4)

    assert _fk_violations(db_path) == []
