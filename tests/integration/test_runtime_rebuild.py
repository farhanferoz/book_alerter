"""Hot-reload smoke test for `rebuild_runtime`.

Goal: enabling ntfy via `PUT /api/config` (or `PATCH /api/sources`) should
install the notifier into `app.state.notifiers` immediately, without a
container restart. Pre-2026-05-15 the lifespan built the registry once and
config changes only took effect after restart.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlmodel import SQLModel

from book_alerter.app import _build_runtime, rebuild_runtime
from book_alerter.config import Config, NtfyChannelConfig
from book_alerter.db.session import get_engine


def _make_app(tmp_path: Path) -> FastAPI:
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    cfg = Config()  # all defaults — ntfy disabled
    app.state.config = cfg
    app.state.config_path = tmp_path / "config.yaml"
    _build_runtime(app, cfg, engine)
    return app


async def test_rebuild_installs_newly_enabled_ntfy_notifier(tmp_path: Path):
    app = _make_app(tmp_path)
    try:
        assert set(app.state.notifiers.keys()) == {"inapp"}

        # Enable ntfy via a config swap, then rebuild.
        new_cfg = app.state.config.model_copy(deep=True)
        new_cfg.notifications.channels.ntfy = NtfyChannelConfig(
            enabled=True, server="https://ntfy.sh", topic="test-topic",
        )
        app.state.config = new_cfg
        rebuild_runtime(app)

        assert set(app.state.notifiers.keys()) == {"inapp", "ntfy"}
    finally:
        app.state.scheduler.shutdown()


async def test_rebuild_disables_notifier_that_was_turned_off(tmp_path: Path):
    app = _make_app(tmp_path)
    try:
        cfg_on = app.state.config.model_copy(deep=True)
        cfg_on.notifications.channels.ntfy = NtfyChannelConfig(
            enabled=True, server="https://ntfy.sh", topic="t",
        )
        app.state.config = cfg_on
        rebuild_runtime(app)
        assert "ntfy" in app.state.notifiers

        cfg_off = app.state.config.model_copy(deep=True)
        cfg_off.notifications.channels.ntfy = NtfyChannelConfig(enabled=False)
        app.state.config = cfg_off
        rebuild_runtime(app)

        assert "ntfy" not in app.state.notifiers
    finally:
        app.state.scheduler.shutdown()


def test_rebuild_is_noop_without_engine(tmp_path: Path):
    """API tests use a router-only app with no engine; rebuild_runtime must
    not blow up in that context."""
    app = FastAPI()
    app.state.config = Config()
    # No engine, no scheduler attached.
    rebuild_runtime(app)  # should be a silent no-op
