from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, select

import book_alerter.scheduler as scheduler_mod
from book_alerter.config import Config, JanitorConfig, SourceConfig
from book_alerter.db import models
from book_alerter.enums import ItemKind, MetadataStatus
from book_alerter.janitor import sweep_backups
from book_alerter.scheduler import Scheduler, run_weekly_backup
from book_alerter.sources.base import Source, SourceError
from book_alerter.sources.wob import WobInlineSource
from tests.integration.conftest import WOB_CARRIED_ISBN


class _RecordingSource(Source):
    """A fake `Source` that counts `prepare()`/`cleanup()` calls instead of
    opening a real `BrowserSession` — used to assert the scheduler's
    prepare/cleanup wiring (`_run_source_locked`) without a browser.

    `prepare_error` / `fetch_error`, if set, are raised (once recorded)
    from the respective method — lets a single fixture drive both the
    "everything succeeds" and "something raises" scheduler test cases.
    """

    name = "fake_browser_source"

    def __init__(
        self,
        *,
        prepare_error: Exception | None = None,
        fetch_error: Exception | None = None,
    ) -> None:
        self.prepare_calls = 0
        self.cleanup_calls = 0
        self._prepare_error = prepare_error
        self._fetch_error = fetch_error

    async def prepare(self) -> None:
        self.prepare_calls += 1
        if self._prepare_error is not None:
            raise self._prepare_error

    async def cleanup(self) -> None:
        self.cleanup_calls += 1

    async def fetch(self, item):
        if self._fetch_error is not None:
            raise self._fetch_error
        return []


async def test_scheduler_registers_jobs_from_config():
    cfg = Config(
        sources={
            "wob": SourceConfig(
                enabled=True,
                region="UK",
                schedule="0 */6 * * *",
            ),
        },
    )
    sched = Scheduler(
        config=cfg,
        sources={"wob": AsyncMock()},
        session_factory=lambda: None,
        alert_pipelines={ItemKind.BOOK: AsyncMock()},
    )
    sched.start()
    try:
        jobs = sched.list_jobs()
        names = [j.id for j in jobs]
        assert "source:wob" in names
    finally:
        sched.shutdown()


async def test_scheduler_no_jobs_when_source_disabled():
    cfg = Config(
        sources={
            "wob": SourceConfig(
                enabled=False,
                region="UK",
                schedule="0 */6 * * *",
            ),
        },
    )
    sched = Scheduler(
        config=cfg,
        sources={"wob": AsyncMock()},
        session_factory=lambda: None,
        alert_pipelines={ItemKind.BOOK: AsyncMock()},
    )
    sched.start()
    try:
        names = [j.id for j in sched.list_jobs()]
        assert "source:wob" not in names
    finally:
        sched.shutdown()


async def test_scheduler_skips_unknown_source_in_config():
    # Config references a source that the registry didn't build (e.g. inline
    # impl missing). Scheduler iterates over built sources, so the config-only
    # entry is naturally skipped.
    cfg = Config(
        sources={
            "wob": SourceConfig(
                enabled=True, region="UK",
                schedule="0 */6 * * *",
            ),
            "ghost": SourceConfig(
                enabled=True, region="UK",
                schedule="0 */4 * * *",
            ),
        },
    )
    sched = Scheduler(
        config=cfg,
        sources={"wob": AsyncMock()},  # 'ghost' intentionally absent
        session_factory=lambda: None,
        alert_pipelines={ItemKind.BOOK: AsyncMock()},
    )
    sched.start()
    try:
        names = [j.id for j in sched.list_jobs()]
        assert "source:wob" in names
        assert "source:ghost" not in names
    finally:
        sched.shutdown()


async def test_scheduler_runs_wob_end_to_end(sqlite_engine, make_book, wob_vcr):
    """Trigger WoB inline source via scheduler; assert observations persist."""
    with Session(sqlite_engine) as s:
        book = make_book(s, isbn13=WOB_CARRIED_ISBN)
        seeded_book_id = book.id

    cfg = Config(
        sources={
            "wob": SourceConfig(
                enabled=True, region="UK",
                per_book_delay_seconds=(0, 0),
                concurrency=1,
            ),
        },
    )
    src = WobInlineSource(name="wob", region="UK")
    alert_calls: list[list[int]] = []

    async def _capture_alert_pipeline(book_ids: list[int]) -> None:
        alert_calls.append(list(book_ids))

    scheduler = Scheduler(
        config=cfg,
        sources={"wob": src},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={ItemKind.BOOK: _capture_alert_pipeline},
    )

    # Skip scheduler.start() — trigger_now drives _run_source directly so the
    # cron loop never spins up and the test is deterministic.
    with wob_vcr("none").use_cassette(f"wob_{WOB_CARRIED_ISBN}.yaml"):
        run_id = await scheduler.trigger_now("wob")

    assert run_id > 0

    with Session(sqlite_engine) as s:
        obs = s.exec(
            select(models.PriceObservation).where(models.PriceObservation.source == "wob")
        ).all()
    assert len(obs) >= 1, "expected >=1 observation row written by the scheduler"
    for o in obs:
        assert o.source == "wob"
        assert o.price_minor > 0
        assert o.total_minor == o.price_minor + (o.shipping_minor or 0)
        assert o.currency == "GBP"

    with Session(sqlite_engine) as s:
        run = s.exec(select(models.SourceRun).where(models.SourceRun.id == run_id)).one()
    assert run.status == "success"
    assert run.books_attempted == 1
    assert run.books_succeeded == 1

    assert len(alert_calls) == 1
    assert alert_calls[0] == [seeded_book_id]


async def test_scheduler_alert_pipeline_failure_does_not_corrupt_audit(
    sqlite_engine, make_book, wob_vcr,
):
    """A raising alert_pipeline must not flip a successful SourceRun to error."""
    with Session(sqlite_engine) as s:
        make_book(s, isbn13=WOB_CARRIED_ISBN)

    cfg = Config(
        sources={
            "wob": SourceConfig(
                enabled=True, region="UK",
                per_book_delay_seconds=(0, 0), concurrency=1,
            ),
        },
    )

    async def _failing_pipeline(book_ids: list[int]) -> None:
        raise RuntimeError("pipeline blew up")

    scheduler = Scheduler(
        config=cfg,
        sources={"wob": WobInlineSource(name="wob", region="UK")},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={ItemKind.BOOK: _failing_pipeline},
    )

    with wob_vcr("none").use_cassette(f"wob_{WOB_CARRIED_ISBN}.yaml"):
        run_id = await scheduler.trigger_now("wob")

    with Session(sqlite_engine) as s:
        run = s.exec(select(models.SourceRun).where(models.SourceRun.id == run_id)).one()
    assert run.status == "success", (
        "alert pipeline RuntimeError must not corrupt a successful SourceRun audit"
    )
    assert run.books_succeeded == 1


async def test_scheduler_marks_repeat_same_day_observations_as_duplicates(
    sqlite_engine, make_book, wob_vcr,
):
    """Second scrape with identical prices updates the existing row's
    `last_seen_at` in place rather than inserting a new one (migration 0021,
    T3.2 heartbeat compaction — before that, a repeat sighting inserted an
    `is_duplicate_of`-pointing row instead).

    `book_history_summary.observation_count` reads straight off the table
    row count, so this is what keeps it honest and keeps percentile
    distributions from being polluted by same-day repeats. See
    Scheduler._persist for the update-in-place logic and
    RecommendationConfig.min_days_of_history for why this matters to signal
    correctness.
    """
    with Session(sqlite_engine) as s:
        make_book(s, isbn13=WOB_CARRIED_ISBN)

    cfg = Config(
        sources={
            "wob": SourceConfig(
                enabled=True, region="UK",
                per_book_delay_seconds=(0, 0), concurrency=1,
            ),
        },
    )

    async def _noop_pipeline(book_ids: list[int]) -> None:
        return

    scheduler = Scheduler(
        config=cfg,
        sources={"wob": WobInlineSource(name="wob", region="UK")},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={ItemKind.BOOK: _noop_pipeline},
    )

    cassette = f"wob_{WOB_CARRIED_ISBN}.yaml"
    # Single VCR context with playback repeats so both passes hit the same
    # recorded response without exhausting the cassette.
    with wob_vcr("none").use_cassette(
        cassette, allow_playback_repeats=True,
    ):
        await scheduler.trigger_now("wob")
        with Session(sqlite_engine) as s:
            after_pass_1 = s.exec(
                select(models.PriceObservation).where(models.PriceObservation.source == "wob")
            ).all()
            rows_after_pass_1 = {o.id: o.last_seen_at for o in after_pass_1}

        await scheduler.trigger_now("wob")

    with Session(sqlite_engine) as s:
        after_pass_2 = s.exec(
            select(models.PriceObservation).where(models.PriceObservation.source == "wob")
        ).all()

    assert len(rows_after_pass_1) > 0
    # Same rows, not doubled — pass 2 updated in place rather than inserting.
    assert {o.id for o in after_pass_2} == set(rows_after_pass_1)
    # And every row's last_seen_at moved forward with pass 2's re-sighting.
    for o in after_pass_2:
        assert o.last_seen_at > rows_after_pass_1[o.id], (
            f"row {o.id}: last_seen_at did not advance on the repeat scrape"
        )


async def test_persist_dedup_normalizes_seller_case_and_whitespace(sqlite_engine, make_book):
    """If a source returns 'Amazon' on one scrape, ' amazon ' on the next,
    and 'AMAZON' on the third (rendered seller link text is not
    contractually stable on casing or whitespace), `_persist` must fold each
    later row into the first as an update-in-place re-sighting — otherwise
    `book_history_summary.observation_count` counts each as a distinct
    observation and the percentile distribution drifts.

    Covers both axes (case AND whitespace) so the persist-time match
    stays in lockstep with `_normalize_seller` in sources/amazon.py,
    which strips + lowers.
    """
    from book_alerter.sources.base import ObservationCandidate

    with Session(sqlite_engine) as s:
        book = make_book(s, isbn13="9780747532699")
        book_id = book.id

    scheduler = Scheduler(
        config=Config(),
        sources={},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={ItemKind.BOOK: AsyncMock()},
    )

    def _cand(seller: str) -> ObservationCandidate:
        return ObservationCandidate(
            seller=seller,
            condition="new",
            price_minor=799,
            shipping_minor=0,
            currency="GBP",
            url="https://www.amazon.co.uk/dp/0747532699",
        )

    variants = ["Amazon", " amazon ", "AMAZON", "\tAmazon\n"]
    for variant in variants:
        with Session(sqlite_engine) as s:
            book_row = s.get(models.Book, book_id)
            assert book_row is not None
            scheduler._persist("amazon", ItemKind.BOOK, book_row, [_cand(variant)])

    with Session(sqlite_engine) as s:
        rows = s.exec(
            select(models.PriceObservation).where(models.PriceObservation.book_id == book_id)
        ).all()

    assert len(rows) == 1, (
        f"expected all {len(variants)} variants folded into 1 row, got {len(rows)}"
    )


async def test_scheduler_calls_prepare_and_cleanup_once_when_fetch_raises(
    sqlite_engine, make_book,
):
    """T1.1: `_run_source_locked` must call `prepare()` before iterating
    kinds and `cleanup()` in a `finally` — and cleanup must run even when
    an item's `fetch()` raises, not just on the happy path."""
    with Session(sqlite_engine) as s:
        make_book(s, isbn13="9780000000001")

    src = _RecordingSource(fetch_error=SourceError("fake_browser_source", "boom"))
    cfg = Config(
        sources={
            "fake_browser_source": SourceConfig(
                enabled=True, region="UK",
                per_book_delay_seconds=(0, 0),
                concurrency=1,
            ),
        },
    )
    scheduler = Scheduler(
        config=cfg,
        sources={"fake_browser_source": src},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={ItemKind.BOOK: AsyncMock()},
    )

    run_id = await scheduler.trigger_now("fake_browser_source")

    assert src.prepare_calls == 1
    assert src.cleanup_calls == 1

    with Session(sqlite_engine) as s:
        run = s.exec(select(models.SourceRun).where(models.SourceRun.id == run_id)).one()
    # Every item's fetch raised, so the kind "ran" but succeeded nothing —
    # existing status logic reports this as an error, not a crash of the
    # whole run (kind_exceptions stays empty; only per-item fetch failed).
    assert run.status == "error"
    assert run.books_attempted == 1
    assert run.books_succeeded == 0


async def test_scheduler_calls_cleanup_once_when_prepare_raises(sqlite_engine, make_book):
    """A `prepare()` failure (e.g. the browser fails to launch) must still
    reach `cleanup()` exactly once — `prepare()` sits inside the same
    try/finally as the iteration it guards, not before it."""
    with Session(sqlite_engine) as s:
        make_book(s, isbn13="9780000000002")

    src = _RecordingSource(prepare_error=RuntimeError("chromium launch failed"))
    cfg = Config(
        sources={
            "fake_browser_source": SourceConfig(
                enabled=True, region="UK",
                per_book_delay_seconds=(0, 0),
                concurrency=1,
            ),
        },
    )
    scheduler = Scheduler(
        config=cfg,
        sources={"fake_browser_source": src},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={ItemKind.BOOK: AsyncMock()},
    )

    run_id = await scheduler.trigger_now("fake_browser_source")

    assert src.prepare_calls == 1
    assert src.cleanup_calls == 1

    with Session(sqlite_engine) as s:
        run = s.exec(select(models.SourceRun).where(models.SourceRun.id == run_id)).one()
    assert run.status == "error"
    assert "chromium launch failed" in (run.error_message or "")


# --- F-B2: weekly backup retention survives janitor compression -------------


class _FakeWeeklyDatetime:
    """Stands in for `scheduler_mod.datetime` so each simulated weekly backup
    gets a distinct, monotonically increasing timestamp. `run_weekly_backup`
    only ever calls `.now(UTC)`, once, to build the target filename -- a real
    weekly cadence would never produce two backups in the same second, but a
    tight test loop would, and `VACUUM INTO` refuses to overwrite an existing
    target file."""

    _t = datetime(2026, 1, 1, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # matches datetime.now's signature; tz is unused
        cls._t = cls._t + timedelta(weeks=1)
        return cls._t


def test_backup_retention_settles_at_retain_across_janitor_compression(
    tmp_path, monkeypatch,
):
    """Reproduces F-B2: `sweep_backups` (janitor.py) compresses each backup to
    `book_alerter_<ts>.db.gz` and unlinks the plain `.db` original. Retention
    in `run_weekly_backup` used to glob only `book_alerter_*.db`, so after the
    first janitor pass it saw at most the one not-yet-compressed backup and
    `files[:-retain]` was always empty -- unbounded growth of the single
    largest thing the app writes (~35 MB per backup per the JanitorConfig
    comment). The real backup schedule (Sun 03:00) runs before the daily
    janitor (04:00), so every backup is compressed within a day and then
    immortal.

    Interleaves the real `run_weekly_backup` and the real `sweep_backups`
    over 10 weekly cycles, exactly as the two jobs run in production, and
    asserts the file count settles at `retain` rather than growing to 10.
    """
    db_path = tmp_path / "book_alerter.db"
    sqlite3.connect(str(db_path)).close()  # a valid, empty SQLite file
    backup_dir = tmp_path / "backups"
    retain = 7

    monkeypatch.setattr(scheduler_mod, "datetime", _FakeWeeklyDatetime)
    jcfg = JanitorConfig()
    assert jcfg.compress_backups is True  # the production default this bug needs

    created = []  # the plain `.db` name each cycle's backup was created as
    for _ in range(10):
        target = run_weekly_backup(db_path, backup_dir, retain=retain)
        created.append(target.name)
        sweep_backups(backup_dir, jcfg)

    files = sorted(f.name for f in backup_dir.iterdir())
    assert len(files) == retain, files
    assert all(name.endswith(".gz") for name in files), (
        "the janitor should have compressed every retained backup"
    )
    # Retention kept exactly the newest `retain` cycles (as their compressed
    # form) and pruned the rest -- not "whatever was left after the janitor
    # broke the glob", which before the fix was every `.gz` file ever made.
    assert files == [f"{name}.gz" for name in created[-retain:]]


# --- T6.5: janitor job registration -----------------------------------------


def _janitor_sched(cfg: Config, *, db_path=None, app_state=None) -> Scheduler:
    return Scheduler(
        config=cfg,
        sources={},
        session_factory=lambda: None,
        alert_pipelines={ItemKind.BOOK: AsyncMock()},
        db_path=db_path,
        app_state=app_state,
    )


async def test_janitor_job_registered_by_default(tmp_path):
    sched = _janitor_sched(Config(sources={}), db_path=tmp_path / "book_alerter.db")
    sched.start()
    try:
        assert "janitor" in [j.id for j in sched.list_jobs()]
    finally:
        sched.shutdown()


async def test_janitor_job_not_registered_when_disabled(tmp_path):
    cfg = Config(sources={})
    cfg.janitor.enabled = False
    sched = _janitor_sched(cfg, db_path=tmp_path / "book_alerter.db")
    sched.start()
    try:
        assert "janitor" not in [j.id for j in sched.list_jobs()]
    finally:
        sched.shutdown()


async def test_janitor_job_not_registered_without_db_path():
    """Mirrors the weekly-backup guard: a test (or an embedding) that omits
    `db_path` must still get a usable scheduler rather than a janitor job
    that would sweep a directory derived from None."""
    sched = _janitor_sched(Config(sources={}), db_path=None)
    sched.start()
    try:
        assert "janitor" not in [j.id for j in sched.list_jobs()]
    finally:
        sched.shutdown()


async def test_run_janitor_passes_the_data_and_backup_directories(tmp_path, monkeypatch):
    """The registration tests above would still pass if `_run_janitor` swept
    the wrong directories, so pin the arguments too: `data_dir` is the database
    file's PARENT (the mounted volume), not the database file itself.
    """
    import book_alerter.scheduler as scheduler_mod

    captured = {}
    monkeypatch.setattr(
        scheduler_mod, "janitor_tick", lambda **kw: captured.update(kw) or []
    )

    cfg = Config(sources={})
    cfg.backup.directory = str(tmp_path / "data" / "backups")
    state = object()
    sched = _janitor_sched(
        cfg, db_path=tmp_path / "data" / "book_alerter.db", app_state=state
    )
    sched._run_janitor()

    assert captured["data_dir"] == tmp_path / "data"
    assert captured["backup_dir"] == Path(cfg.backup.directory)
    assert captured["cfg"] is cfg.janitor
    assert captured["app_state"] is state


# --- T1.3: bot-challenge retry, counting, and backoff ------------------------

_CHALLENGE_MSG = "Amazon bot-protection challenge persisted; giving up"


def _challenged() -> SourceError:
    return SourceError("amazon", _CHALLENGE_MSG)


class _ScriptedSource(Source):
    """Raises/returns a scripted outcome per `fetch` call, per item.

    `outcomes` maps an item's isbn13 to a list consumed one entry per call:
    an Exception is raised, anything else is returned. Records every call so
    a test can assert the retry happened exactly once rather than inferring
    it from the end state.
    """

    name = "amazon"

    def __init__(self, outcomes: dict[str, list]) -> None:
        self._outcomes = {k: list(v) for k, v in outcomes.items()}
        self.calls: list[str] = []

    async def prepare(self) -> None:
        return

    async def cleanup(self) -> None:
        return

    async def fetch(self, item):
        key = item.isbn13
        self.calls.append(key)
        outcome = self._outcomes[key].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _challenge_cfg(max_consecutive_errors: int = 5) -> Config:
    return Config(
        sources={
            "amazon": SourceConfig(
                enabled=True, region="UK",
                per_book_delay_seconds=(0, 0), concurrency=1,
                max_consecutive_errors=max_consecutive_errors,
            ),
        },
    )


def _challenge_scheduler(engine, src, max_consecutive_errors: int = 5) -> Scheduler:
    async def _noop(ids: list[int]) -> None:
        return

    return Scheduler(
        config=_challenge_cfg(max_consecutive_errors),
        sources={"amazon": src},
        session_factory=lambda: Session(engine),
        alert_pipelines={ItemKind.BOOK: _noop},
    )


@pytest.fixture
def no_challenge_wait(monkeypatch):
    """The real wait is 20-40s per challenged item — unusable in a test."""
    monkeypatch.setattr(scheduler_mod, "_CHALLENGE_RETRY_DELAY_SECONDS", (0.0, 0.0))


async def test_challenged_item_is_retried_once_and_can_succeed(
    sqlite_engine, make_book, no_challenge_wait
):
    """A challenge buys exactly one more attempt. An item that recovers on the
    retry is a success and is NOT counted as challenged — the count feeds a
    backoff rule about how blocked we are, not how often the page appeared."""
    with Session(sqlite_engine) as s:
        make_book(s, isbn13="9780000000001")

    src = _ScriptedSource({"9780000000001": [_challenged(), []]})
    run_id = await _challenge_scheduler(sqlite_engine, src).trigger_now("amazon")

    assert src.calls == ["9780000000001"] * 2, "expected exactly one retry"
    with Session(sqlite_engine) as s:
        run = s.get(models.SourceRun, run_id)
        assert run.books_succeeded == 1
        assert run.items_challenged == 0


async def test_item_challenged_twice_is_counted_and_recorded_as_failed(
    sqlite_engine, make_book, no_challenge_wait
):
    with Session(sqlite_engine) as s:
        make_book(s, isbn13="9780000000001")

    src = _ScriptedSource(
        {"9780000000001": [_challenged(), _challenged()]}
    )
    run_id = await _challenge_scheduler(sqlite_engine, src).trigger_now("amazon")

    assert src.calls == ["9780000000001"] * 2
    with Session(sqlite_engine) as s:
        run = s.get(models.SourceRun, run_id)
        assert run.items_challenged == 1
        assert run.books_succeeded == 0


async def test_ordinary_source_error_is_not_retried(
    sqlite_engine, make_book, no_challenge_wait
):
    """Only a challenge earns a second attempt. A parse failure or a dead
    listing must still cost exactly one fetch, or every broken item doubles
    the run's work."""
    with Session(sqlite_engine) as s:
        make_book(s, isbn13="9780000000001")

    src = _ScriptedSource({"9780000000001": [SourceError("amazon", "no offers found")]})
    run_id = await _challenge_scheduler(sqlite_engine, src).trigger_now("amazon")

    assert src.calls == ["9780000000001"], "an ordinary error must not retry"
    with Session(sqlite_engine) as s:
        assert s.get(models.SourceRun, run_id).items_challenged == 0


async def test_heavily_challenged_run_engages_backoff_despite_a_success(
    sqlite_engine, make_book, no_challenge_wait
):
    """The gap T1.3 closes. Before this, one succeeding item reset the
    consecutive-error counter, so backoff never engaged while the source was
    plainly blocking us — the observed production state (10 of 13 books
    carrying a challenge error, runs still finishing 'partial')."""
    with Session(sqlite_engine) as s:
        make_book(s, isbn13="9780000000001")
        make_book(s, isbn13="9780000000002")

    src = _ScriptedSource({
        "9780000000001": [_challenged(), _challenged()],
        "9780000000002": [[]],
    })
    # max_consecutive_errors=0 so the very first counted error engages
    # backoff -- this asserts the whole path, not just the counter.
    sched = _challenge_scheduler(sqlite_engine, src, max_consecutive_errors=0)
    run_id = await sched.trigger_now("amazon")

    with Session(sqlite_engine) as s:
        run = s.get(models.SourceRun, run_id)
        assert run.items_challenged == 1
        assert run.books_succeeded == 1, "one item genuinely succeeded"

    # 1 of 2 attempted == 50%, which meets the threshold. Before T1.3 the
    # succeeding item reset this to 0 and no backoff ever engaged.
    assert sched._consecutive_errors["amazon"] == 1
    assert "amazon" in sched._backoff_until, "backoff must engage"


async def test_unexpected_exception_on_challenge_retry_is_recorded_and_counted(
    sqlite_engine, make_book, no_challenge_wait,
):
    """F-B4 + F-B5, reproduced together since they're the same code path.

    Before the fix: an exception from the retry that is NEITHER TimeoutError
    NOR SourceError (here a RuntimeError, standing in for a Playwright
    assertion or a stray sqlite error mid-fetch) was raised from inside the
    `except (TimeoutError, SourceError)` block wrapping the retry, so it
    propagated straight out of the whole try/except -- past the sibling
    `except Exception` (which belongs to the OUTER try, around attempt 1,
    and is never consulted for an exception raised while another is already
    being handled) -- into `asyncio.gather(..., return_exceptions=True)`,
    where it was silently dropped: no log line, no `last_scrape_error`, no
    `_record_item_failure` call, and `challenged` never incremented.
    """
    with Session(sqlite_engine) as s:
        book = make_book(s, isbn13="9780000000001")
        book_id = book.id

    src = _ScriptedSource(
        {"9780000000001": [_challenged(), RuntimeError("playwright target closed")]}
    )
    run_id = await _challenge_scheduler(sqlite_engine, src).trigger_now("amazon")

    assert src.calls == ["9780000000001"] * 2, "the retry must still happen"
    with Session(sqlite_engine) as s:
        run = s.get(models.SourceRun, run_id)
        # The fix: this run is neither silently dropped nor status "success".
        assert run.books_attempted == 1
        assert run.books_succeeded == 0
        assert run.status == "error"
        # F-B5: the item's first attempt was a challenge, so it counts
        # towards `challenged` regardless of the retry's own exception type.
        assert run.items_challenged == 1

        book_row = s.get(models.Book, book_id)
        assert book_row.last_scrape_attempt_at is not None
        assert book_row.last_scrape_error is not None
        assert "playwright target closed" in book_row.last_scrape_error


async def test_mixed_challenged_then_timed_out_and_succeeded_engages_backoff(
    sqlite_engine, make_book, no_challenge_wait,
):
    """F-B5's mixed case, the gap T1.3 exists for. 2 of 3 items are
    challenged and then time out on the retry (`TimeoutError`, not
    `SourceError` -- the shape a challenge interstitial takes when it simply
    never resolves rather than raising its own error) while 1 succeeds.

    Before the fix: `challenged` only incremented when the RETRY's own
    exception was itself `is_bot_challenge` -- a plain `TimeoutError` never
    is, so `challenged_total` stayed 0, `heavily_challenged` was False, and
    the one success reset `_consecutive_errors` to 0. Backoff never engaged
    even though 2 of 3 items were plainly still blocked.
    """
    n_seeded_books = 3
    n_challenged_then_timed_out = 2

    with Session(sqlite_engine) as s:
        make_book(s, isbn13="9780000000001")
        make_book(s, isbn13="9780000000002")
        make_book(s, isbn13="9780000000003")

    src = _ScriptedSource({
        "9780000000001": [_challenged(), TimeoutError()],
        "9780000000002": [_challenged(), TimeoutError()],
        "9780000000003": [[]],
    })
    # max_consecutive_errors=0 so the very first counted error engages
    # backoff -- asserts the whole path, not just the counter.
    sched = _challenge_scheduler(sqlite_engine, src, max_consecutive_errors=0)
    run_id = await sched.trigger_now("amazon")

    with Session(sqlite_engine) as s:
        run = s.get(models.SourceRun, run_id)
        assert run.books_attempted == n_seeded_books
        assert run.books_succeeded == 1
        assert run.items_challenged == n_challenged_then_timed_out, (
            "non-zero: the fix this test targets"
        )

    # 2 of 3 attempted >= 50% threshold -> heavily_challenged, so backoff
    # engages despite the one genuine success.
    assert sched._consecutive_errors["amazon"] == 1
    assert "amazon" in sched._backoff_until, "backoff must engage"


# --- T6.3: weekly Keepa refresh registration ---------------------------------


async def test_keepa_refresh_job_is_not_registered_by_default(tmp_path):
    """Default-off is the shipped state. Unlike the janitor and backup jobs
    this one talks to a third party whose rate tolerance we have not
    measured, so it must not appear unless someone opts in."""
    sched = _janitor_sched(Config(sources={}), db_path=tmp_path / "b.db")
    sched.start()
    try:
        assert "keepa_refresh" not in [j.id for j in sched.list_jobs()]
    finally:
        sched.shutdown()


async def test_keepa_refresh_job_is_registered_when_enabled(tmp_path):
    cfg = Config(sources={})
    cfg.keepa.refresh_enabled = True
    sched = _janitor_sched(cfg, db_path=tmp_path / "b.db")
    sched.start()
    try:
        assert "keepa_refresh" in [j.id for j in sched.list_jobs()]
    finally:
        sched.shutdown()


# --- T4.1: product metadata refresh (scheduler half) -------------------------


async def test_metadata_refresh_job_is_always_registered(tmp_path):
    """Registered unconditionally, unlike the keepa job. It is the only thing
    that ever resolves a PENDING product when the price scraper's dp parse
    doesn't, so a config switch that disabled it would strand those rows."""
    sched = _janitor_sched(Config(sources={}), db_path=tmp_path / "b.db")
    sched.start()
    try:
        assert "metadata_refresh" in [j.id for j in sched.list_jobs()]
    finally:
        sched.shutdown()


def _pending_product(attempts: int, last_attempt: datetime | None) -> models.Product:
    return models.Product(
        asin="B09B96TG33", title="t",
        metadata_status=MetadataStatus.PENDING,
        metadata_attempts=attempts,
        metadata_last_attempt_at=last_attempt,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )


async def test_metadata_refresh_backoff_gate():
    """Attempt N waits BASE * 2**(N-1) minutes. The first attempt is always
    due, which covers both a freshly-created row and one whose immediate
    post-create attempt raced and lost."""
    sched = _janitor_sched(Config(sources={}))
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    assert sched._metadata_refresh_due(_pending_product(0, None), now) is True

    # attempt 1 -> 30 min wait
    assert sched._metadata_refresh_due(
        _pending_product(1, now - timedelta(minutes=29)), now
    ) is False
    assert sched._metadata_refresh_due(
        _pending_product(1, now - timedelta(minutes=30)), now
    ) is True

    # attempt 3 -> 120 min wait, so the backoff really is exponential
    assert sched._metadata_refresh_due(
        _pending_product(3, now - timedelta(minutes=119)), now
    ) is False
    assert sched._metadata_refresh_due(
        _pending_product(3, now - timedelta(minutes=120)), now
    ) is True


async def test_metadata_refresh_backoff_handles_a_naive_timestamp():
    """SQLite returns naive datetimes. Subtracting one from an aware `now`
    raises TypeError, and it would raise inside the scheduled job rather than
    anywhere a test would normally look."""
    sched = _janitor_sched(Config(sources={}))
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    naive = datetime(2026, 9, 4, 10, 0)
    assert sched._metadata_refresh_due(_pending_product(1, naive), now) is True


# --- F-B3: a FAILED product can recover its title ---------------------------


def _failed_product(attempts: int, last_attempt: datetime | None) -> models.Product:
    return models.Product(
        asin="B09B96TG33", title="Amazon product B09B96TG33",
        metadata_status=MetadataStatus.FAILED,
        metadata_attempts=attempts,
        metadata_last_attempt_at=last_attempt,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )


async def test_metadata_refresh_due_uses_a_fixed_cadence_once_failed():
    """A FAILED row is retried on the fixed cadence
    `_METADATA_REFRESH_FAILED_RETRY_HOURS`, not a continued exponential
    doubling from `metadata_attempts` -- attempts is frozen once FAILED (see
    `_refresh_one_product_metadata`), so the exponential formula would divide
    by nothing meaningful and, worse, would keep accelerating retries every
    time a row failed again rather than throttling them."""
    sched = _janitor_sched(Config(sources={}))
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    cap_hours = Scheduler._METADATA_REFRESH_FAILED_RETRY_HOURS

    assert sched._metadata_refresh_due(
        _failed_product(6, now - timedelta(hours=cap_hours - 1)), now
    ) is False
    assert sched._metadata_refresh_due(
        _failed_product(6, now - timedelta(hours=cap_hours)), now
    ) is True
    # A row that has failed many times since (attempts grown well past the
    # 6-attempt budget in an earlier build, or just hypothetically) still
    # uses the SAME fixed cadence -- not a further-exploded exponential wait.
    assert sched._metadata_refresh_due(
        _failed_product(40, now - timedelta(hours=cap_hours)), now
    ) is True


async def test_metadata_refresh_tick_re_admits_a_due_failed_row(
    sqlite_engine, make_product, monkeypatch,
):
    """`_metadata_refresh_tick`'s query used to select PENDING only, so a
    FAILED row was dropped from it for good the moment it went FAILED --
    the scheduler-side half of F-B3's re-admission. One FAILED row past its
    cadence, one FAILED row still inside it: only the former is fetched."""
    cap_hours = Scheduler._METADATA_REFRESH_FAILED_RETRY_HOURS
    now = datetime.now(UTC)

    with Session(sqlite_engine) as s:
        due = make_product(s, asin="B0DUEFAIL1")
        due.metadata_status = MetadataStatus.FAILED
        due.metadata_attempts = Scheduler._METADATA_REFRESH_MAX_ATTEMPTS
        due.metadata_last_attempt_at = now - timedelta(hours=cap_hours + 1)
        s.add(due)

        not_due = make_product(s, asin="B0NOTDUEF1")
        not_due.metadata_status = MetadataStatus.FAILED
        not_due.metadata_attempts = Scheduler._METADATA_REFRESH_MAX_ATTEMPTS
        not_due.metadata_last_attempt_at = now - timedelta(hours=1)
        s.add(not_due)
        s.commit()

    scheduler = Scheduler(
        config=Config(sources={}),
        sources={},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={ItemKind.BOOK: AsyncMock(), ItemKind.PRODUCT: AsyncMock()},
    )
    calls: list[str] = []

    async def _fake_fetch(asin: str) -> None:
        calls.append(asin)
        return None

    monkeypatch.setattr("book_alerter.scheduler.fetch_amazon_uk_product_metadata", _fake_fetch)

    await scheduler._metadata_refresh_tick()

    assert calls == ["B0DUEFAIL1"], "only the due FAILED row should have been refreshed"


@pytest.mark.parametrize("start_status", [MetadataStatus.PENDING, MetadataStatus.FAILED])
async def test_persist_adopts_a_title_for_pending_and_failed_products(
    sqlite_engine, make_product, start_status,
):
    """The core F-B3 regression, reproduced the way the reviewer did: feed
    `_persist` an `ObservationCandidate` carrying `item_title` for a product
    that starts PENDING (already worked) and one that starts FAILED
    (previously never resolved -- `_persist`'s title-adoption gate checked
    PENDING only, so a FAILED product showed its ASIN placeholder as its
    title forever with no path back short of delete-and-recreate)."""
    from book_alerter.sources.base import ObservationCandidate

    with Session(sqlite_engine) as s:
        product = make_product(
            s, asin="B0TEST0001", title="Amazon product B0TEST0001",
        )
        product.metadata_status = start_status
        if start_status == MetadataStatus.FAILED:
            product.metadata_attempts = Scheduler._METADATA_REFRESH_MAX_ATTEMPTS
        s.add(product)
        s.commit()
        product_id = product.id

    scheduler = Scheduler(
        config=Config(),
        sources={},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={ItemKind.PRODUCT: AsyncMock()},
    )

    candidate = ObservationCandidate(
        seller="Amazon",
        condition="new",
        price_minor=1999,
        shipping_minor=0,
        currency="GBP",
        url="https://www.amazon.co.uk/dp/B0TEST0001",
        item_title="Real Product Title",
    )

    with Session(sqlite_engine) as s:
        product_row = s.get(models.Product, product_id)
        assert product_row is not None
        scheduler._persist("amazon_uk_product", ItemKind.PRODUCT, product_row, [candidate])

    with Session(sqlite_engine) as s:
        product_row = s.get(models.Product, product_id)
        assert product_row.title == "Real Product Title"
        assert product_row.metadata_status == MetadataStatus.OK
