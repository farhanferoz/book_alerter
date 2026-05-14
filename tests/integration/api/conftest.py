"""Shared fixtures for API integration tests.

`api_client` builds a minimal FastAPI app wired to a sqlite engine that already
has the `book_stats` view installed (`engine_with_view`). The router(s) under
test are included directly so we don't drag in the full lifespan (scheduler,
sources, notifiers). This is the test-app pattern that Task 7.2+ should reuse:
build a router-only `FastAPI()`, set `app.state.engine` + `app.state.config`,
include the routers you're testing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from book_alerter.api import books
from book_alerter.config import Config


@pytest.fixture
def api_client(engine_with_view, tmp_path: Path):
    cfg = Config.load(tmp_path / "config.yaml")  # path doesn't exist → defaults
    app = FastAPI()
    app.state.engine = engine_with_view
    app.state.config = cfg
    app.include_router(books.router)
    with TestClient(app) as client:
        yield client
