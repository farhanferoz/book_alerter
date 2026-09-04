from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, select

import book_alerter.scheduler as scheduler_mod
from book_alerter.config import Config, SourceConfig
from book_alerter.db import models
from book_alerter.enums import ItemKind
from book_alerter.scheduler import Scheduler
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
