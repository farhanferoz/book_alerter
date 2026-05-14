from __future__ import annotations

import asyncio
import random
import sqlite3
import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from book_alerter.config import Config
from book_alerter.db.models import Book, PriceObservation, SourceRun
from book_alerter.logging_setup import get_logger
from book_alerter.sources.base import ObservationCandidate, Source, SourceError

log = get_logger(__name__)


def run_weekly_backup(
    db_path: str | Path,
    backup_dir: str | Path,
    retain: int = 7,
) -> Path:
    """Run `VACUUM INTO` to a timestamped file in `backup_dir`; prune old files.

    Returns the path to the new backup file. Uses a raw `sqlite3.connect`
    because `VACUUM INTO` cannot run inside an open transaction, and SQLModel
    sessions are always transactional.
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    # ISO timestamp with `:` swapped for `-` so the filename is portable across
    # filesystems. Lexicographic sort == chronological sort.
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    target = backup_dir / f"book_alerter_{ts}.db"

    # `sqlite3.connect` opens in autocommit-equivalent mode when we pass
    # `isolation_level=None`. VACUUM INTO refuses to run inside a transaction,
    # and the default driver implicitly opens one on the first statement.
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        # Parameter binding doesn't work for filenames in VACUUM INTO; the
        # path comes from config, not user input. Escape any single quotes
        # defensively.
        escaped = str(target).replace("'", "''")
        conn.execute(f"VACUUM INTO '{escaped}'")
    finally:
        conn.close()

    size = target.stat().st_size if target.exists() else 0
    log.info("backup.created", path=str(target), bytes=size)

    # Retention: keep the newest `retain` files; delete the rest. ISO names
    # sort lexicographically == chronologically.
    try:
        files = sorted(backup_dir.glob("book_alerter_*.db"))
        old = files[:-retain] if retain > 0 else files
        for f in old:
            try:
                f.unlink()
                log.info("backup.pruned", path=str(f))
            except OSError as e:
                log.warning("backup.prune_failed", path=str(f), error=str(e))
    except OSError as e:
        log.warning("backup.retention_scan_failed", dir=str(backup_dir), error=str(e))

    return target


class Scheduler:
    """Wraps APScheduler; registers one job per enabled source."""

    def __init__(
        self,
        config: Config,
        sources: dict[str, Source],
        session_factory: Callable[[], Session],
        alert_pipeline: Callable[[list[int]], Awaitable[None]],
        db_path: str | Path | None = None,
    ) -> None:
        self._cfg = config
        self._sources = sources
        self._session_factory = session_factory
        self._alert_pipeline = alert_pipeline
        self._db_path = Path(db_path) if db_path is not None else None
        self._sched = AsyncIOScheduler(timezone="UTC")
        self._consecutive_errors: dict[str, int] = {}
        # When a source enters backoff, we set _backoff_until[name] to a future
        # UTC datetime. _run_source checks this at entry and skips if not yet
        # eligible. The cron job continues firing on its normal cadence; backoff
        # is enforced by skipping rather than rescheduling, which avoids
        # APScheduler's awkward "delay next run" semantics.
        self._backoff_until: dict[str, datetime] = {}

    def start(self) -> None:
        for name, _src in self._sources.items():
            sc = self._cfg.sources.get(name)
            if sc is None or not sc.enabled:
                continue
            trigger = CronTrigger.from_crontab(sc.schedule, timezone="UTC")
            self._sched.add_job(
                self._run_source,
                trigger=trigger,
                id=f"source:{name}",
                args=[name],
                jitter=sc.jitter_seconds,
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )

        # Weekly SQLite backup. Only register if enabled AND we have a path —
        # tests that don't care about backups can omit `db_path` and still use
        # the rest of the scheduler.
        bcfg = self._cfg.backup
        if bcfg.enabled and self._db_path is not None:
            self._sched.add_job(
                self._run_backup,
                trigger=CronTrigger.from_crontab(bcfg.schedule, timezone="UTC"),
                id="weekly_backup",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )

        self._sched.start()
        log.info("scheduler.started", n_jobs=len(self._sched.get_jobs()))

    def _run_backup(self) -> None:
        """APScheduler entrypoint for the weekly backup job."""
        if self._db_path is None:
            return
        bcfg = self._cfg.backup
        try:
            run_weekly_backup(self._db_path, bcfg.directory, retain=bcfg.retain)
        except Exception as e:
            log.error(
                "backup.failed",
                error=str(e),
                tb=traceback.format_exc(),
            )

    def list_jobs(self) -> list[Any]:
        return self._sched.get_jobs()

    def shutdown(self) -> None:
        self._sched.shutdown(wait=False)

    async def trigger_now(self, source_name: str) -> int:
        """Manual one-shot. Returns SourceRun.id."""
        return await self._run_source(source_name)

    async def _run_source(self, source_name: str) -> int:
        sc = self._cfg.sources[source_name]
        src = self._sources[source_name]
        # Backoff gate: if we're inside the backoff window, skip this run.
        bu = self._backoff_until.get(source_name)
        if bu is not None and datetime.now(UTC) < bu:
            log.info("source.skipped.backoff", source=source_name, until=bu.isoformat())
            return 0
        with self._session_factory() as session:
            run = SourceRun(
                source=source_name,
                started_at=datetime.now(UTC),
                status="running",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            # Capture id before the session closes — with expire_on_commit=True
            # the attribute would trigger a refresh on a closed session.
            run_id: int = run.id or 0

        affected_book_ids: list[int] = []
        attempted = 0
        succeeded = 0
        try:
            with self._session_factory() as session:
                books = session.exec(
                    select(Book).where(Book.status == "active")
                ).all()
            attempted = len(books)
            sem = asyncio.Semaphore(sc.concurrency)

            async def _one(book: Book) -> None:
                nonlocal succeeded
                async with sem:
                    delay = random.uniform(*sc.per_book_delay_seconds)
                    await asyncio.sleep(delay)
                    try:
                        candidates = await asyncio.wait_for(
                            src.fetch(book), timeout=sc.timeout_seconds + 5
                        )
                    except (SourceError, asyncio.TimeoutError) as e:
                        log.warning(
                            "source.book.error",
                            source=source_name,
                            isbn=book.isbn13,
                            error=str(e),
                        )
                        return
                    self._persist(source_name, book, candidates)
                    affected_book_ids.append(book.id or 0)
                    succeeded += 1

            await asyncio.gather(*[_one(b) for b in books])

            with self._session_factory() as session:
                run = session.get(SourceRun, run_id)
                run.finished_at = datetime.now(UTC)
                run.books_attempted = attempted
                run.books_succeeded = succeeded
                if succeeded == attempted:
                    run.status = "success"
                elif succeeded > 0:
                    run.status = "partial"
                else:
                    run.status = "error"
                session.commit()

            if succeeded > 0:
                self._consecutive_errors[source_name] = 0
                self._backoff_until.pop(source_name, None)
            else:
                self._consecutive_errors[source_name] = (
                    self._consecutive_errors.get(source_name, 0) + 1
                )
                self._apply_backoff(source_name)
            # Run alert pipeline AFTER the audit row commits, with its own
            # try/except so a Phase 4 pipeline bug can't corrupt the run record.
            try:
                await self._alert_pipeline(affected_book_ids)
            except Exception:
                log.exception("alert_pipeline.failed", source=source_name)
        except Exception as e:
            log.error(
                "source.run.exception",
                source=source_name,
                error=str(e),
                tb=traceback.format_exc(),
            )
            with self._session_factory() as session:
                run = session.get(SourceRun, run_id)
                run.finished_at = datetime.now(UTC)
                run.status = "error"
                run.error_message = str(e)
                run.error_traceback = traceback.format_exc()
                session.commit()
            self._consecutive_errors[source_name] = (
                self._consecutive_errors.get(source_name, 0) + 1
            )
            self._apply_backoff(source_name)

        return run_id

    def _persist(
        self,
        source_name: str,
        book: Book,
        candidates: list[ObservationCandidate],
    ) -> None:
        with self._session_factory() as session:
            for c in candidates:
                total = c.price_minor + (c.shipping_minor or 0)
                session.add(
                    PriceObservation(
                        book_id=book.id,
                        source=source_name,
                        seller=c.seller,
                        condition=c.condition,
                        price_minor=c.price_minor,
                        currency=c.currency,
                        shipping_minor=c.shipping_minor,
                        total_minor=total,
                        url=c.url,
                        observed_at=datetime.now(UTC),
                        raw=c.model_dump(),
                    )
                )
            session.commit()

    def _apply_backoff(self, source_name: str) -> None:
        sc = self._cfg.sources[source_name]
        n = self._consecutive_errors.get(source_name, 0)
        if n <= sc.max_consecutive_errors:
            return
        delay_s = min(60 * (2 ** (n - sc.max_consecutive_errors)), 24 * 3600)
        self._backoff_until[source_name] = datetime.now(UTC) + timedelta(seconds=delay_s)
        log.warning(
            "source.backoff",
            source=source_name,
            delay_s=delay_s,
            errors=n,
            until=self._backoff_until[source_name].isoformat(),
        )
