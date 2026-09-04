from __future__ import annotations

from unittest.mock import AsyncMock

from sqlmodel import Session, select

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
    """Second scrape with identical prices flags rows as is_duplicate_of <prior>.

    The book_stats view excludes duplicates, so observation_count stays honest
    and percentile distributions aren't polluted by same-day repeats. See
    Scheduler._persist for the dedup logic and RecommendationConfig.min_days_of_history
    for why this matters to signal correctness.
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
        await scheduler.trigger_now("wob")

    with Session(sqlite_engine) as s:
        canonical = s.exec(
            select(models.PriceObservation).where(
                models.PriceObservation.source == "wob",
                models.PriceObservation.is_duplicate_of.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        dupes = s.exec(
            select(models.PriceObservation).where(
                models.PriceObservation.source == "wob",
                models.PriceObservation.is_duplicate_of.is_not(None),  # type: ignore[union-attr]
            )
        ).all()

    # Each pass scrapes the same N variants. After two passes we expect
    # N canonical rows (from pass 1) and N duplicate rows (from pass 2).
    assert len(canonical) > 0
    assert len(dupes) == len(canonical), (
        f"expected pass-2 to fully duplicate pass-1, got "
        f"{len(canonical)} canonical, {len(dupes)} duplicates"
    )
    # Each duplicate must reference a real canonical row.
    canonical_ids = {o.id for o in canonical}
    for d in dupes:
        assert d.is_duplicate_of in canonical_ids


async def test_persist_dedup_normalizes_seller_case_and_whitespace(sqlite_engine, make_book):
    """If a source returns 'Amazon' on one scrape, ' amazon ' on the next,
    and 'AMAZON' on the third (rendered seller link text is not
    contractually stable on casing or whitespace), `_persist` must flag
    each later row as a duplicate of the first — otherwise the
    `book_stats` view counts each as a canonical observation and the
    percentile distribution drifts.

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
        canonical = s.exec(
            select(models.PriceObservation).where(
                models.PriceObservation.book_id == book_id,
                models.PriceObservation.is_duplicate_of.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        dupes = s.exec(
            select(models.PriceObservation).where(
                models.PriceObservation.book_id == book_id,
                models.PriceObservation.is_duplicate_of.is_not(None),  # type: ignore[union-attr]
            )
        ).all()

    assert len(canonical) == 1, f"expected 1 canonical, got {len(canonical)}"
    assert len(dupes) == len(variants) - 1
    for d in dupes:
        assert d.is_duplicate_of == canonical[0].id


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
