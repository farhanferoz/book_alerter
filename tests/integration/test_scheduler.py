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
