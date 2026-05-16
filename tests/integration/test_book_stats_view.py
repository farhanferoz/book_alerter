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
