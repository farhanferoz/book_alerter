from __future__ import annotations

from unittest.mock import AsyncMock

from sqlmodel import Session, select

from book_alerter.config import Config, SourceConfig
from book_alerter.db import models
from book_alerter.scheduler import Scheduler
from book_alerter.sources.wob import WobInlineSource
from tests.integration.conftest import WOB_CARRIED_ISBN


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
        alert_pipeline=AsyncMock(),
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
        alert_pipeline=AsyncMock(),
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
        alert_pipeline=AsyncMock(),
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
        alert_pipeline=_capture_alert_pipeline,
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
        alert_pipeline=_failing_pipeline,
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
        alert_pipeline=_noop_pipeline,
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


async def test_persist_dedup_is_case_insensitive_on_seller(sqlite_engine, make_book):
    """If a source returns 'Amazon' on one scrape and 'amazon' on the next
    (Amazon's rendered seller link text is not contractually stable on
    casing), `_persist` must still flag the second row as a duplicate of
    the first — otherwise the `book_stats` view counts both as canonical
    observations and the percentile distribution drifts.
    """
    from book_alerter.sources.base import ObservationCandidate

    with Session(sqlite_engine) as s:
        book = make_book(s, isbn13="9780747532699")
        book_id = book.id

    scheduler = Scheduler(
        config=Config(),
        sources={},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipeline=AsyncMock(),
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

    with Session(sqlite_engine) as s:
        first_book = s.get(models.Book, book_id)
        assert first_book is not None
        scheduler._persist("amazon", first_book, [_cand("Amazon")])
        second_book = s.get(models.Book, book_id)
        assert second_book is not None
        # Same offer, different seller casing — must still dedup.
        scheduler._persist("amazon", second_book, [_cand("amazon")])

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

    assert len(canonical) == 1
    assert len(dupes) == 1
    assert dupes[0].is_duplicate_of == canonical[0].id
