from __future__ import annotations

import asyncio
import random
import sqlite3
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
from book_alerter.janitor import janitor_tick
from book_alerter.keepa_backfill import keepa_refresh_tick
from book_alerter.logging_setup import get_logger
from book_alerter.sources.base import ObservationCandidate, Source, SourceError

log = get_logger(__name__)


@dataclass(frozen=True)
class _KindRouting:
    """Per-kind table+column dispatch for the scheduler's iteration loop.

    Centralises the if/else on `ItemKind` that previously appeared in both
    `_run_kind_for_source` (which item table to query, which natural-key
    field to log) and `_persist` (which observation table to write, which
    FK column to filter on). Adding a third kind = add a third entry to
    `_KIND_ROUTING`.
    """

    item_model: type[Book | Product]
    identifier_attr: str        # "isbn13" | "asin"
    observation_model: type[PriceObservation | ProductObservation]
    item_fk_attr: str           # "book_id" | "product_id"


_KIND_ROUTING: dict[ItemKind, _KindRouting] = {
    ItemKind.BOOK: _KindRouting(
        item_model=Book,
        identifier_attr="isbn13",
        observation_model=PriceObservation,
        item_fk_attr="book_id",
    ),
    ItemKind.PRODUCT: _KindRouting(
        item_model=Product,
        identifier_attr="asin",
        observation_model=ProductObservation,
        item_fk_attr="product_id",
    ),
}


# T1.3: bot-challenge handling.
#
# Both browser sources raise a `SourceError` whose message ends in
# "challenge persisted" once their in-source retry is exhausted:
# `sources/amazon.py` ("Amazon bot-protection challenge persisted; ...") and
# `sources/bookfinder.py` ("AWS WAF challenge persisted; ..."). Matching on
# that shared substring is what the plan specifies, and it is the reason the
# phrase must stay in both messages.
#
# A dedicated `BotChallengeError(SourceError)` subclass would be sturdier than
# substring matching -- it would make the contract explicit instead of implied
# by wording. It is not done here because it would mean editing both source
# modules, and the failure mode of getting this wrong is mild and visible: an
# unmatched message just means the item is counted as an ordinary failure,
# exactly as it was before this feature.
_CHALLENGE_MARKER = "challenge persisted"

# Wait before the single retry. A challenge means the source wants us to slow
# down, so retrying immediately would waste the attempt; the range is jittered
# so a whole run's worth of items doesn't retry in lockstep. Module-level so
# tests can shrink it -- 20s per item is not something a test can sit through.
_CHALLENGE_RETRY_DELAY_SECONDS = (20.0, 40.0)

# Fraction of a run's attempted items that must be challenged before the run
# counts as a consecutive error even though some items succeeded.
_CHALLENGE_BACKOFF_RATIO = 0.5


def is_bot_challenge(exc: BaseException) -> bool:
    """True when `exc` is a source failure caused by an unsolved bot challenge
    rather than an ordinary error (timeout, parse failure, no offers).

    Reads `.message` rather than `str(exc)`: `SourceError.__str__` is
    `"[<source>] <message>"`, so matching the formatted form would also match
    a source whose NAME happened to contain the marker.
    """
    return isinstance(exc, SourceError) and _CHALLENGE_MARKER in exc.message


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
        app_state: object | None = None,
    ) -> None:
        self._cfg = config
        self._sources = sources
        self._session_factory = session_factory
        self._alert_pipelines = alert_pipelines
        self._db_path = Path(db_path) if db_path is not None else None
        # Only used to record `janitor_last_run_at` for /api/health. Optional
        # for the same reason `db_path` is: a test that doesn't care about
        # the janitor can omit it and still use the rest of the scheduler.
        self._app_state = app_state
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

        # T6.5: daily data-directory janitor. Same "enabled AND we have a path"
        # guard as the backup job above -- `data_dir` is the database file's
        # parent, which is the mounted volume every runtime directory the
        # janitor sweeps (browser-profiles/, debug/, keepa-cache/, covers/,
        # backups/) lives under. Registered AFTER the backup job and scheduled
        # an hour later (04:00 vs 03:00 UTC) so a backup is never mid-write
        # while `sweep_backups` is compressing that directory.
        jcfg = self._cfg.janitor
        if jcfg.enabled and self._db_path is not None:
            self._sched.add_job(
                self._run_janitor,
                trigger=CronTrigger.from_crontab(jcfg.schedule, timezone="UTC"),
                id="janitor",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )

        # T6.3: weekly Keepa refresh. Registered only when explicitly enabled
        # -- unlike the janitor and backup jobs, this one talks to a third
        # party whose rate tolerance we have not measured, so it stays off
        # until someone opts in.
        kcfg = self._cfg.keepa
        if kcfg.refresh_enabled:
            self._sched.add_job(
                self._run_keepa_refresh,
                trigger=CronTrigger.from_crontab(
                    kcfg.refresh_schedule, timezone="UTC"
                ),
                id="keepa_refresh",
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

    def _run_janitor(self) -> None:
        """APScheduler entrypoint for the daily janitor sweep.

        No try/except here on purpose: `janitor_tick` is documented never to
        raise (a failing janitor must not take the scheduler down with it) and
        already logs its own failures, so wrapping it again would only add a
        second, quieter path for the same error.
        """
        if self._db_path is None:
            return
        janitor_tick(
            data_dir=self._db_path.parent,
            cfg=self._cfg.janitor,
            backup_dir=Path(self._cfg.backup.directory),
            session_factory=self._session_factory,
            app_state=self._app_state,
        )

    def _run_keepa_refresh(self) -> None:
        """APScheduler entrypoint for the weekly Keepa refresh.

        No try/except, same as `_run_janitor`: `keepa_refresh_tick` is
        documented never to raise and logs per-item failures itself."""
        keepa_refresh_tick(
            self._session_factory,
            enabled=self._cfg.keepa.refresh_enabled,
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
        challenged_total = 0
        kind_exceptions: list[tuple[ItemKind, Exception]] = []
        try:
            # prepare()/cleanup() bracket the whole iteration below (not the
            # no_kinds_to_run early-return above, which fetches nothing).
            # `prepare()` is INSIDE the inner try (not before it) so that a
            # `prepare()` failure still reaches the `finally` below — cleanup()
            # must run even if the source only got as far as opening (part of)
            # its browser session. A failure inside cleanup() is caught and
            # logged rather than propagated, so it can never mask whatever
            # this try/finally would otherwise have raised or returned.
            try:
                await src.prepare()
                for kind in kinds_to_run:
                    try:
                        (
                            ids,
                            attempted,
                            succeeded,
                            challenged,
                        ) = await self._run_kind_for_source(
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
                    challenged_total += challenged

                with self._session_factory() as session:
                    run = session.get(SourceRun, run_id)
                    run.finished_at = datetime.now(UTC)
                    run.books_attempted = attempted_total
                    run.books_succeeded = succeeded_total
                    run.items_challenged = challenged_total
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

                # T1.3: a run where most items were challenged must count as
                # a consecutive error EVEN IF some items succeeded. Without
                # this, one lucky item resets the counter and backoff never
                # engages while the source is plainly blocking us -- which is
                # the observed production state (10 of 13 books carrying a
                # challenge error, runs still finishing 'partial').
                heavily_challenged = (
                    attempted_total > 0
                    and (challenged_total / attempted_total) >= _CHALLENGE_BACKOFF_RATIO
                )
                if heavily_challenged:
                    log.warning(
                        "source.run.heavily_challenged",
                        source=source_name,
                        attempted=attempted_total,
                        challenged=challenged_total,
                    )
                if not heavily_challenged and (
                    succeeded_total > 0
                    or (attempted_total == 0 and not kind_exceptions)
                ):
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
            finally:
                try:
                    await src.cleanup()
                except Exception:
                    log.exception("source.cleanup.failed", source=source_name)
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
    ) -> tuple[list[int], int, int, int]:
        """Per-kind iteration: query the right item table, fetch each item,
        persist observations, return
        (affected_ids, attempted, succeeded, challenged).

        `challenged` counts items whose FINAL outcome was a bot challenge --
        i.e. the retry was also challenged. An item that recovers on the retry
        is a success and is deliberately not counted, because the number feeds
        a backoff rule about how blocked we actually are, not how often the
        challenge appeared.
        """
        routing = _KIND_ROUTING[kind]
        item_model = routing.item_model
        identifier_attr = routing.identifier_attr

        with self._session_factory() as session:
            items = session.exec(
                select(item_model).where(item_model.status == ItemStatus.ACTIVE)
            ).all()
        attempted = len(items)
        succeeded = 0
        challenged = 0
        affected_ids: list[int] = []
        sem = asyncio.Semaphore(sc.concurrency)

        async def _one(item) -> None:
            nonlocal succeeded, challenged
            async with sem:
                delay = random.uniform(*sc.per_book_delay_seconds)
                await asyncio.sleep(delay)
                try:
                    candidates = await asyncio.wait_for(
                        src.fetch(item), timeout=sc.timeout_seconds + 5,
                    )
                except (TimeoutError, SourceError) as e:
                    if not is_bot_challenge(e):
                        log.warning(
                            "source.item.error",
                            source=source_name,
                            kind=kind.value,
                            identifier=getattr(item, identifier_attr, None),
                            error=str(e),
                        )
                        self._record_item_failure(item_model, item.id, str(e))
                        return
                    # Challenged: wait, then give this item exactly one more
                    # go. `src.fetch` opens its own page each call, so the
                    # retry gets a fresh page on the existing browser context
                    # -- the cookies/profile that `prepare()` set up are kept,
                    # which is the point (D20: a fresh identity per fetch is
                    # what made Amazon treat us as a first-time visitor).
                    log.warning(
                        "source.item.challenged",
                        source=source_name,
                        kind=kind.value,
                        identifier=getattr(item, identifier_attr, None),
                        error=str(e),
                    )
                    await asyncio.sleep(
                        random.uniform(*_CHALLENGE_RETRY_DELAY_SECONDS)
                    )
                    try:
                        candidates = await asyncio.wait_for(
                            src.fetch(item), timeout=sc.timeout_seconds + 5,
                        )
                    except (TimeoutError, SourceError) as retry_exc:
                        if is_bot_challenge(retry_exc):
                            challenged += 1
                        log.warning(
                            "source.item.error",
                            source=source_name,
                            kind=kind.value,
                            identifier=getattr(item, identifier_attr, None),
                            error=str(retry_exc),
                            after_challenge_retry=True,
                        )
                        self._record_item_failure(
                            item_model, item.id, str(retry_exc)
                        )
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
        return affected_ids, attempted, succeeded, challenged

    def _persist(
        self,
        source_name: str,
        kind: ItemKind,
        item: Book | Product,
        candidates: list[ObservationCandidate],
    ) -> None:
        """Persist a scrape's candidates, refreshing an unchanged offer in place.

        When the most recent observation for the same (item, source, seller,
        condition) tuple has identical price + shipping, the offer has simply
        been seen again: its `last_seen_at` moves to now and no new row is
        written. Before the heartbeat compaction (migration 0021) each
        re-sighting inserted a duplicate row instead, which is how 86% of the
        production observation table came to be heartbeats.

        `url` is refreshed on the matched row too. It is not part of the dedup
        key, and migration 0019 exists precisely because current-best must
        surface the latest sighting's URL rather than a frozen first-sighting
        one — leaving it stale here would reintroduce that bug.

        Also updates the item row's last_scrape_attempt_at + clears
        last_scrape_error in the SAME session so a per-item scrape costs
        one DB commit, not two.
        """
        routing = _KIND_ROUTING[kind]
        observation_model = routing.observation_model
        item_model = routing.item_model
        item_fk_attr = routing.item_fk_attr

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
                if prior is not None:
                    # Seen again, unchanged: move the sighting forward in place.
                    prior.last_seen_at = now
                    prior.url = c.url
                    session.add(prior)
                else:
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
                            last_seen_at=now,
                            raw=c.model_dump(),
                        )
                    )
            if item.id is not None:
                fresh_item = session.get(item_model, item.id)
                if fresh_item is not None:
                    fresh_item.last_scrape_attempt_at = now
                    fresh_item.last_scrape_error = None
                    session.add(fresh_item)
            session.commit()

        # T3.4: a persisted observation can shift the shipping cascade's
        # cross-item (source, seller_class) medians tier, so the dashboard's
        # 60s-TTL cache (`stats.MediansCache`, `app.state.medians_cache`)
        # must not keep serving a value computed before this scrape. Cheap
        # and idempotent either way: `invalidate()` just clears the cache,
        # and nothing recomputes until the next `GET /api/books`/`/products`
        # actually reads it. `self._app_state` is the same optional hook
        # `_run_janitor` uses for `janitor_last_run_at` — None in tests that
        # don't wire a real app, hence the getattr guard.
        medians_cache = getattr(self._app_state, "medians_cache", None)
        if medians_cache is not None:
            medians_cache.invalidate()

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
