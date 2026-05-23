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
from sqlalchemy import func
from sqlmodel import Session, select

from book_alerter.config import Config
from book_alerter.db.models import (
    Book,
    PriceObservation,
    Product,
    ProductObservation,
    SourceRun,
)
from book_alerter.enums import ItemKind, ItemStatus
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
    """Wraps APScheduler; registers one job per enabled source.

    A single source may serve multiple ItemKinds (books, products) per its
    declared `Source.item_kinds`. The scheduler iterates the intersection
    of `Source.item_kinds` and the per-source-config `SourceConfig.item_kinds`
    on every run, persisting observations to the right table and routing
    affected ids to the right `alert_pipelines[kind]` after the source run.
    """

    def __init__(
        self,
        config: Config,
        sources: dict[str, Source],
        session_factory: Callable[[], Session],
        alert_pipelines: dict[ItemKind, Callable[[list[int]], Awaitable[None]]],
        db_path: str | Path | None = None,
    ) -> None:
        self._cfg = config
        self._sources = sources
        self._session_factory = session_factory
        self._alert_pipelines = alert_pipelines
        self._db_path = Path(db_path) if db_path is not None else None
        self._sched = AsyncIOScheduler(timezone="UTC")
        self._consecutive_errors: dict[str, int] = {}
        # When a source enters backoff, we set _backoff_until[name] to a future
        # UTC datetime. _run_source checks this at entry and skips if not yet
        # eligible. The cron job continues firing on its normal cadence; backoff
        # is enforced by skipping rather than rescheduling, which avoids
        # APScheduler's awkward "delay next run" semantics.
        self._backoff_until: dict[str, datetime] = {}
        # Per-source lock so a UI-triggered `trigger_now` (refetch button or
        # `POST /api/sources/{name}/run`) cannot race the cron-fired job for
        # the same source. APScheduler's `max_instances=1` only guards
        # cron-to-cron overlap — not the manual trigger path.
        self._source_locks: dict[str, asyncio.Lock] = {}

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

    @property
    def running(self) -> bool:
        """Mirror APScheduler's `running` so the deep healthcheck in
        `api/health.py` actually probes scheduler liveness. Without this
        proxy, `getattr(sched, "running", None)` on a real `Scheduler`
        returned `None`, which the probe treats as "no probe available"
        — so a crashed APScheduler never trips the 503."""
        return self._sched.running

    def shutdown(self) -> None:
        self._sched.shutdown(wait=False)

    async def trigger_now(self, source_name: str) -> int:
        """Manual one-shot. Returns SourceRun.id."""
        return await self._run_source(source_name)

    async def _run_source(self, source_name: str) -> int:
        # Single-flight per source across BOTH cron and manual paths. Without
        # this lock, `POST /api/sources/{name}/run` (or the refetch fan-out)
        # could start a second `_run_source` while the cron run is in flight,
        # producing parallel `SourceRun` rows and racing on the same shared
        # InlineSource instance.
        lock = self._source_locks.setdefault(source_name, asyncio.Lock())
        async with lock:
            return await self._run_source_locked(source_name)

    async def _run_source_locked(self, source_name: str) -> int:
        sc = self._cfg.sources[source_name]
        src = self._sources[source_name]
        # Backoff gate: if we're inside the backoff window, skip this run.
        bu = self._backoff_until.get(source_name)
        if bu is not None and datetime.now(UTC) < bu:
            log.info("source.skipped.backoff", source=source_name, until=bu.isoformat())
            return 0
        # Intersect what the source CAN do (`src.item_kinds`) with what the
        # config WANTS it to do (`sc.item_kinds`). Empty intersection = source
        # is enabled but configured against its capabilities; log + emit a
        # no-op SourceRun so the audit trail records the cycle.
        kinds_to_run = sorted(
            set(sc.item_kinds) & src.item_kinds,
            key=lambda k: k.value,
        )
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

        if not kinds_to_run:
            log.warning(
                "source.run.no_kinds",
                source=source_name,
                src_kinds=[k.value for k in src.item_kinds],
                cfg_kinds=[k.value for k in sc.item_kinds],
            )
            with self._session_factory() as session:
                run = session.get(SourceRun, run_id)
                run.finished_at = datetime.now(UTC)
                run.status = "success"
                session.commit()
            return run_id

        attempted_total = 0
        succeeded_total = 0
        affected_by_kind: dict[ItemKind, list[int]] = {}
        kind_exceptions: list[tuple[ItemKind, Exception]] = []
        try:
            for kind in kinds_to_run:
                try:
                    ids, attempted, succeeded = await self._run_kind_for_source(
                        source_name, src, sc, kind,
                    )
                except Exception as e:
                    # Per-kind isolation: a crash inside one kind's iteration
                    # must NOT swallow the alert pipelines of sibling kinds
                    # whose observations already committed. Record the failure
                    # and continue; the SourceRun row reflects partial /
                    # error based on `succeeded_total` below.
                    log.error(
                        "source.kind.exception",
                        source=source_name,
                        kind=kind.value,
                        error=str(e),
                        tb=traceback.format_exc(),
                    )
                    kind_exceptions.append((kind, e))
                    continue
                affected_by_kind[kind] = ids
                attempted_total += attempted
                succeeded_total += succeeded

            with self._session_factory() as session:
                run = session.get(SourceRun, run_id)
                run.finished_at = datetime.now(UTC)
                run.books_attempted = attempted_total
                run.books_succeeded = succeeded_total
                if kind_exceptions and succeeded_total == 0:
                    # Every kind that didn't crash also had zero successes
                    # (or no kinds ran clean) — treat as error.
                    run.status = "error"
                    run.error_message = "; ".join(
                        f"[{k.value}] {e}" for k, e in kind_exceptions
                    )
                    run.error_traceback = traceback.format_exc()
                elif kind_exceptions:
                    # At least one kind succeeded; others crashed.
                    run.status = "partial"
                    run.error_message = "; ".join(
                        f"[{k.value}] {e}" for k, e in kind_exceptions
                    )
                elif attempted_total == 0:
                    run.status = "success"  # zero items is success, not partial
                elif succeeded_total == attempted_total:
                    run.status = "success"
                elif succeeded_total > 0:
                    run.status = "partial"
                else:
                    run.status = "error"
                session.commit()

            if succeeded_total > 0 or (attempted_total == 0 and not kind_exceptions):
                self._consecutive_errors[source_name] = 0
                self._backoff_until.pop(source_name, None)
            else:
                self._consecutive_errors[source_name] = (
                    self._consecutive_errors.get(source_name, 0) + 1
                )
                self._apply_backoff(source_name)
            # Run alert pipelines AFTER the audit row commits, with their own
            # try/except so a pipeline bug can't corrupt the run record.
            for kind, ids in affected_by_kind.items():
                if not ids:
                    continue
                pipeline = self._alert_pipelines.get(kind)
                if pipeline is None:
                    log.warning(
                        "alert_pipeline.missing",
                        source=source_name,
                        kind=kind.value,
                    )
                    continue
                try:
                    await pipeline(ids)
                except Exception:
                    log.exception(
                        "alert_pipeline.failed",
                        source=source_name,
                        kind=kind.value,
                    )
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

    async def _run_kind_for_source(
        self,
        source_name: str,
        src: Source,
        sc,
        kind: ItemKind,
    ) -> tuple[list[int], int, int]:
        """Per-kind iteration: query the right item table, fetch each item,
        persist observations, return (affected_ids, attempted, succeeded).
        """
        item_model: type[Book | Product]
        identifier_attr: str
        if kind == ItemKind.BOOK:
            item_model = Book
            identifier_attr = "isbn13"
        else:
            item_model = Product
            identifier_attr = "asin"

        with self._session_factory() as session:
            items = session.exec(
                select(item_model).where(item_model.status == ItemStatus.ACTIVE)
            ).all()
        attempted = len(items)
        succeeded = 0
        affected_ids: list[int] = []
        sem = asyncio.Semaphore(sc.concurrency)

        async def _one(item) -> None:
            nonlocal succeeded
            async with sem:
                delay = random.uniform(*sc.per_book_delay_seconds)
                await asyncio.sleep(delay)
                try:
                    candidates = await asyncio.wait_for(
                        src.fetch(item), timeout=sc.timeout_seconds + 5,
                    )
                except (TimeoutError, SourceError) as e:
                    log.warning(
                        "source.item.error",
                        source=source_name,
                        kind=kind.value,
                        identifier=getattr(item, identifier_attr, None),
                        error=str(e),
                    )
                    self._record_item_failure(item_model, item.id, str(e))
                    return
                except Exception as e:
                    # Anything else (Playwright assertion errors, sqlite
                    # OperationalError mid-fetch, unexpected source bugs) is
                    # charged to this item rather than aborting the whole
                    # kind's iteration via asyncio.gather propagation. The
                    # `gather` below catches via `return_exceptions=True` for
                    # defence-in-depth, but per-item recording is the right
                    # place to mark scrape health.
                    log.exception(
                        "source.item.unexpected",
                        source=source_name,
                        kind=kind.value,
                        identifier=getattr(item, identifier_attr, None),
                    )
                    self._record_item_failure(item_model, item.id, str(e))
                    return
                try:
                    self._persist(source_name, kind, item, candidates)
                except Exception as e:
                    # Same rationale: a persist error on one item shouldn't
                    # take down the rest of the batch.
                    log.exception(
                        "source.item.persist_failed",
                        source=source_name,
                        kind=kind.value,
                        identifier=getattr(item, identifier_attr, None),
                    )
                    self._record_item_failure(item_model, item.id, str(e))
                    return
                if item.id is not None:
                    affected_ids.append(item.id)
                succeeded += 1

        await asyncio.gather(*[_one(i) for i in items], return_exceptions=True)
        return affected_ids, attempted, succeeded

    def _persist(
        self,
        source_name: str,
        kind: ItemKind,
        item: Book | Product,
        candidates: list[ObservationCandidate],
    ) -> None:
        """Persist a scrape's candidates, marking exact-match repeats as duplicates.

        A new observation is marked `is_duplicate_of=<prior_canonical_id>` when
        the most recent canonical (non-duplicate) observation for the same
        (item, source, seller, condition) tuple has identical price + shipping.
        Duplicates still land in the table (so we keep a heartbeat of "we did
        check") but the `{book,product}_stats` view excludes them, keeping the
        observation count and percentile distribution honest. See
        `RecommendationConfig.min_days_of_history` for why this matters.

        Also updates the item row's last_scrape_attempt_at + clears
        last_scrape_error in the SAME session so a per-item scrape costs
        one DB commit, not two.
        """
        observation_model: type[PriceObservation | ProductObservation]
        item_model: type[Book | Product]
        item_fk_attr: str
        if kind == ItemKind.BOOK:
            observation_model = PriceObservation
            item_model = Book
            item_fk_attr = "book_id"
        else:
            observation_model = ProductObservation
            item_model = Product
            item_fk_attr = "product_id"

        now = datetime.now(UTC)
        with self._session_factory() as session:
            item_fk_col = getattr(observation_model, item_fk_attr)
            for c in candidates:
                total = c.price_minor + (c.shipping_minor or 0)
                # Match a prior canonical row on the FULL offer identity
                # (item/source/seller/condition/price/shipping). NULL shipping
                # (parser saw no delivery info) and 0 shipping (parser saw
                # "FREE delivery") are DIFFERENT signals — we deliberately
                # don't conflate them so that a scrape which finally extracts
                # a shipping value supersedes an older unknown.
                #
                # Seller is compared after trim + lower to stay in lockstep
                # with `_normalize_seller` in sources/amazon.py — Amazon's
                # seller link text is parsed from rendered HTML and is not
                # contractually stable on casing or whitespace. Every current
                # source happens to strip at parse time, so the trim is
                # defensive (against a future source forgetting to strip and
                # against pre-trim rows that may exist in older DBs).
                if c.seller is None:
                    seller_clause = observation_model.seller.is_(None)  # type: ignore[union-attr]
                else:
                    seller_clause = (
                        func.lower(func.trim(observation_model.seller))
                        == c.seller.strip().lower()
                    )
                prior_q = select(observation_model).where(
                    item_fk_col == item.id,
                    observation_model.source == source_name,
                    seller_clause,
                    observation_model.condition == c.condition,
                    observation_model.price_minor == c.price_minor,
                    observation_model.is_duplicate_of.is_(None),  # type: ignore[union-attr]
                )
                if c.shipping_minor is None:
                    prior_q = prior_q.where(
                        observation_model.shipping_minor.is_(None)  # type: ignore[union-attr]
                    )
                else:
                    prior_q = prior_q.where(
                        observation_model.shipping_minor == c.shipping_minor
                    )
                prior = session.exec(
                    prior_q
                    .order_by(observation_model.observed_at.desc())  # type: ignore[union-attr]
                    .limit(1)
                ).first()
                duplicate_of: int | None = prior.id if prior is not None else None
                session.add(
                    observation_model(
                        **{item_fk_attr: item.id},
                        source=source_name,
                        seller=c.seller,
                        condition=c.condition,
                        price_minor=c.price_minor,
                        currency=c.currency,
                        shipping_minor=c.shipping_minor,
                        total_minor=total,
                        url=c.url,
                        observed_at=now,
                        raw=c.model_dump(),
                        is_duplicate_of=duplicate_of,
                    )
                )
            if item.id is not None:
                fresh_item = session.get(item_model, item.id)
                if fresh_item is not None:
                    fresh_item.last_scrape_attempt_at = now
                    fresh_item.last_scrape_error = None
                    session.add(fresh_item)
            session.commit()

    def _record_item_failure(
        self,
        item_model: type[Book | Product],
        item_id: int | None,
        error: str,
    ) -> None:
        """Persist a failed scrape attempt on the item row. Success path is
        folded into `_persist` so a successful scrape costs one commit.
        Last-write-wins across sources — enough signal for the FE to flag
        "something is broken right now"."""
        if item_id is None:
            return
        # Truncate long Playwright tracebacks so they don't bloat the row.
        truncated = error[: self._ERROR_MSG_CAP]
        with self._session_factory() as session:
            item = session.get(item_model, item_id)
            if item is None:
                return
            item.last_scrape_attempt_at = datetime.now(UTC)
            item.last_scrape_error = truncated
            session.add(item)
            session.commit()

    _ERROR_MSG_CAP = 500

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
