from __future__ import annotations

from unittest.mock import AsyncMock

from book_alerter.config import Config, SourceConfig
from book_alerter.scheduler import Scheduler


async def test_scheduler_registers_jobs_from_config():
    cfg = Config(
        sources={
            "wob": SourceConfig(
                enabled=True,
                type="inline",
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
                type="inline",
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
                enabled=True, type="inline", region="UK",
                schedule="0 */6 * * *",
            ),
            "ghost": SourceConfig(
                enabled=True, type="inline", region="UK",
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


async def test_scheduler_runs_wob_end_to_end(sqlite_engine, make_book):
    """Trigger WoB inline source via scheduler; assert observations persist."""
    from pathlib import Path

    import vcr
    from sqlmodel import Session, select

    from book_alerter.config import Config, SourceConfig
    from book_alerter.db import models
    from book_alerter.scheduler import Scheduler
    from book_alerter.sources.wob import WobInlineSource

    # Seed one Book matching the cassette ISBN.
    ISBN = "9780241638194"
    with Session(sqlite_engine) as s:
        make_book(s, isbn13=ISBN)

    # Build scheduler with WoB inline source + no per-book delay.
    cfg = Config(
        sources={
            "wob": SourceConfig(
                enabled=True, type="inline", region="UK",
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

    # We DO NOT call scheduler.start() — we use trigger_now directly so the
    # cron loop never spins up. This keeps the test deterministic.

    cassette_dir = Path(__file__).parent / "sources" / "cassettes"
    my_vcr = vcr.VCR(
        cassette_library_dir=str(cassette_dir),
        record_mode="none",  # MUST replay; fail loud if cassette missing
        match_on=("method", "scheme", "host", "port", "path"),
        decode_compressed_response=True,
    )

    with my_vcr.use_cassette(f"wob_{ISBN}.yaml"):
        run_id = await scheduler.trigger_now("wob")

    assert run_id > 0

    # Assert observations landed.
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

    # Assert SourceRun audit was written and marked success.
    with Session(sqlite_engine) as s:
        run = s.exec(select(models.SourceRun).where(models.SourceRun.id == run_id)).one()
    assert run.status == "success"
    assert run.books_attempted == 1
    assert run.books_succeeded == 1

    # Assert the alert pipeline got the book id of the affected book.
    assert len(alert_calls) == 1
    assert len(alert_calls[0]) == 1
