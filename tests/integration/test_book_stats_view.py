"""Verifies the book_stats view computed via real alembic migration."""
from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlmodel import Session, create_engine

from book_alerter.db import models
from book_alerter.stats import compute_book_stats


def test_book_stats_view_current_best(tmp_path):
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True,
        capture_output=True,
    )

    engine = create_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000001", title="Title", author="Author",
            created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)

        # 5 observations across 2 sources; "wob" latest=850 (cheapest), "amazon" latest=1100
        rows = [
            ("wob",    1200, now - timedelta(days=10)),
            ("wob",    1000, now - timedelta(days=5)),
            ("wob",     850, now - timedelta(days=1)),  # latest wob
            ("amazon", 1500, now - timedelta(days=8)),
            ("amazon", 1100, now - timedelta(days=2)),  # latest amazon
        ]
        for source, total, obs_at in rows:
            s.add(models.PriceObservation(
                book_id=book.id, source=source, condition="new",
                price_minor=total, currency="GBP", shipping_minor=0,
                total_minor=total, url=f"https://{source}",
                observed_at=obs_at, raw={},
            ))
        s.commit()

        row = s.exec(
            text("SELECT * FROM book_stats WHERE book_id = :id").bindparams(id=book.id)
        ).mappings().first()
        stats = compute_book_stats(book.id, s)

    assert row["observation_count"] == 5
    assert row["current_best_total_minor"] == 850
    assert row["current_best_source"] == "wob"
    assert stats.all_time_min_total_minor == 850
    assert stats.all_time_max_total_minor == 1500


def test_book_stats_view_excludes_stale_source_partition(tmp_path):
    """Regression for the freshness-gate bug fixed in migration 0017.

    When a parser change makes a (source, condition, seller) partition
    stop receiving fresh rows (e.g. Amazon Resale was historically
    tagged as `condition=new`; commit f24668b changed it to `used_vg`),
    the old row sits as latest-of-its-partition indefinitely. Without
    the freshness gate, MIN(total_minor) across rn=1 rows can pick the
    stale row even though it represents an offer that doesn't exist
    anymore.

    This test reproduces the production failure: an old `amazon-new-
    Amazon Resale £28.60` row at T-30min + a fresh `amazon-used_vg-
    Amazon Resale £24.62` row at T should yield current_best=£24.62,
    not £28.60.
    """
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True,
        capture_output=True,
    )
    engine = create_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000002", title="Stale", author="x",
            created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)

        # Pre-fix scrape: Amazon Resale tagged as new at £28.60.
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="new",
            seller="Amazon Resale",
            price_minor=2860, currency="GBP", shipping_minor=0,
            total_minor=2860, url="https://example/old-dp",
            observed_at=now - timedelta(minutes=30), raw={},
        ))
        # Post-fix scrape (just now): the same seller correctly tagged
        # used_vg at £24.62 (and at £28.60 as a separate offer from the
        # dp page — both are real Amazon Resale listings at the moment).
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="used_vg",
            seller="Amazon Resale",
            price_minor=2462, currency="GBP", shipping_minor=0,
            total_minor=2462,
            url="https://example/warehouse-deals",
            observed_at=now, raw={},
        ))
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="used_vg",
            seller="Amazon Resale",
            price_minor=2860, currency="GBP", shipping_minor=0,
            total_minor=2860, url="https://example/new-dp",
            observed_at=now, raw={},
        ))
        s.commit()

        row = s.exec(
            text("SELECT * FROM book_stats WHERE book_id = :id").bindparams(id=book.id)
        ).mappings().first()

    # The fresh £24.62 used_vg row must win, not the stale £28.60 new row
    # AND not the colliding £28.60 used_vg row at the same observed_at —
    # the ROW_NUMBER tiebreaker must prefer the cheaper total within a
    # partition.
    assert row["current_best_total_minor"] == 2462
    assert row["current_best_condition"] == "used_vg"
    assert row["current_best_seller"] == "Amazon Resale"
    assert row["current_best_url"] == "https://example/warehouse-deals"
