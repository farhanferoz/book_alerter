"""Weekly SQLite backup job (Phase 12.3).

Tests the module-level `run_weekly_backup` function directly. The Scheduler
class only registers it as a cron trigger; the interesting behavior (VACUUM
INTO + retention) lives in the function. That gives us deterministic,
in-process tests without spinning up APScheduler's event loop.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time
from sqlmodel import Session, SQLModel

from book_alerter.db import models  # noqa: F401 — registers models on metadata
from book_alerter.db.session import get_engine
from book_alerter.scheduler import run_weekly_backup


def _seed_db(db_path):
    """Create a SQLite DB with schema + one Book row, then close the engine."""
    engine = get_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(models.Book(
            isbn13="9780000000001", title="t", author="a",
            created_at=now, updated_at=now,
        ))
        s.commit()
    engine.dispose()


def _is_valid_sqlite(path) -> bool:
    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        return result is not None and result[0] == "ok"
    finally:
        conn.close()


def test_run_weekly_backup_creates_valid_backup(tmp_path):
    db_path = tmp_path / "book_alerter.db"
    backup_dir = tmp_path / "backups"
    _seed_db(db_path)

    with freeze_time("2026-05-09 03:00:00", tz_offset=0):
        target = run_weekly_backup(db_path, backup_dir, retain=7)

    assert target.exists(), "backup file was not created"
    assert target.name == "book_alerter_2026-05-09T03-00-00.db"
    assert target.parent == backup_dir
    # Round-trip: backup must be a valid SQLite DB with our data.
    assert _is_valid_sqlite(target)
    conn = sqlite3.connect(str(target))
    try:
        rows = conn.execute("SELECT isbn13 FROM book").fetchall()
    finally:
        conn.close()
    assert rows == [("9780000000001",)]


def test_run_weekly_backup_retains_only_last_seven(tmp_path):
    db_path = tmp_path / "book_alerter.db"
    backup_dir = tmp_path / "backups"
    _seed_db(db_path)

    # Simulate 8 weekly runs at Sunday 03:00 UTC, starting 2026-01-04 (a Sunday).
    base = datetime(2026, 1, 4, 3, 0, 0, tzinfo=timezone.utc)
    created: list = []
    for week in range(8):
        when = base + timedelta(weeks=week)
        with freeze_time(when):
            created.append(run_weekly_backup(db_path, backup_dir, retain=7))

    surviving = sorted(backup_dir.glob("book_alerter_*.db"))
    assert len(surviving) == 7, f"expected exactly 7 retained backups, got {len(surviving)}"

    # Oldest (week 0) must be pruned; the 7 newest must survive.
    assert created[0] not in surviving
    for f in created[1:]:
        assert f in surviving

    # Every surviving file is a valid SQLite database.
    for f in surviving:
        assert _is_valid_sqlite(f), f"corrupt backup at {f}"


def test_run_weekly_backup_works_when_dir_missing(tmp_path):
    db_path = tmp_path / "book_alerter.db"
    backup_dir = tmp_path / "nested" / "does-not-exist" / "backups"
    _seed_db(db_path)

    target = run_weekly_backup(db_path, backup_dir, retain=7)
    assert target.exists()
    assert backup_dir.is_dir()


def test_run_weekly_backup_retain_one(tmp_path):
    """retain=1 keeps exactly the most recent file."""
    db_path = tmp_path / "book_alerter.db"
    backup_dir = tmp_path / "backups"
    _seed_db(db_path)

    base = datetime(2026, 1, 4, 3, 0, 0, tzinfo=timezone.utc)
    for week in range(3):
        with freeze_time(base + timedelta(weeks=week)):
            run_weekly_backup(db_path, backup_dir, retain=1)

    surviving = sorted(backup_dir.glob("book_alerter_*.db"))
    assert len(surviving) == 1
    # The retained file is the one created in week 2.
    expected = (base + timedelta(weeks=2)).strftime("%Y-%m-%dT%H-%M-%S")
    assert surviving[0].name == f"book_alerter_{expected}.db"


@pytest.mark.asyncio
async def test_scheduler_registers_weekly_backup_job(tmp_path):
    """Scheduler.start() registers the backup cron job when db_path is set."""
    from unittest.mock import AsyncMock

    from book_alerter.config import Config
    from book_alerter.scheduler import Scheduler

    db_path = tmp_path / "book_alerter.db"
    _seed_db(db_path)

    sched = Scheduler(
        config=Config(),
        sources={},
        session_factory=lambda: None,
        alert_pipeline=AsyncMock(),
        db_path=db_path,
    )
    sched.start()
    try:
        ids = [j.id for j in sched.list_jobs()]
        assert "weekly_backup" in ids
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_scheduler_skips_backup_when_disabled(tmp_path):
    from unittest.mock import AsyncMock

    from book_alerter.config import BackupConfig, Config
    from book_alerter.scheduler import Scheduler

    db_path = tmp_path / "book_alerter.db"
    _seed_db(db_path)

    cfg = Config(backup=BackupConfig(enabled=False))
    sched = Scheduler(
        config=cfg,
        sources={},
        session_factory=lambda: None,
        alert_pipeline=AsyncMock(),
        db_path=db_path,
    )
    sched.start()
    try:
        ids = [j.id for j in sched.list_jobs()]
        assert "weekly_backup" not in ids
    finally:
        sched.shutdown()
