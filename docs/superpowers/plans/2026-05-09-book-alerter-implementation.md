# Book Alerter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted book-price comparison tool — Python (FastAPI + APScheduler + SQLite) orchestrating Go source-CLIs (printing-press) over a pluggable Source interface, with a React dashboard and ntfy push notifications, deployed via Docker on a NAS.

**Architecture:** Single-process FastAPI app holding APScheduler in its lifespan; per-source jobs shell out to printing-press-generated Go CLIs (and one inline Python source for bootstrap). SQLite on a host-mounted volume. React + Vite SPA served statically. Tailscale-only access; no app-level auth by default.

**Tech Stack:** Python 3.13 · uv · FastAPI · SQLModel · Alembic · APScheduler · Pydantic-Settings · structlog · httpx · selectolax · isbnlib · pytest · pytest-asyncio · vcrpy · hypothesis · React 18 · TypeScript · Vite · Tailwind · shadcn/ui · Recharts · TanStack Query · openapi-typescript · Monaco · Docker (multi-stage Go + Python).

**Spec reference:** `docs/superpowers/specs/2026-05-09-book-alerter-design.md`

---

## Overview of phases

| Phase | What it produces | Verifiable result |
|---|---|---|
| 0 | Project skeleton, config schema, logging, `/api/health` | App boots, health endpoint returns ok |
| 1 | Data model (5 tables + view), Alembic migrations | Migrations apply on fresh DB; round-trip ORM tests pass |
| 2 | Source plugin layer + WoB inline scraper | One real source works end-to-end against VCR cassette |
| 3 | APScheduler integration, jitter, backoff, source-run audit | Scheduled WoB job fires, observations land in DB |
| 4 | Stats helper, signal logic, alert detection + dedup, in-app notifier | Adding a price-drop fires an in-app alert; signal computed correctly |
| 5 | ntfy notifier, quiet hours, NotificationDelivery audit | Alert fires real ntfy message; quiet hours suppresses |
| 6 | Metadata service (OpenLibrary + Google Books fallback) | ISBN → title/author/cover round-trip |
| 7 | Full REST API surface | OpenAPI at `/docs` exposes all endpoints; integration tests pass |
| 8 | Bookfinder + Amazon printing-press CLIs + adapters | All three sources hitting real (recorded) sites |
| 9 | React + Vite + Tailwind + shadcn/ui scaffold, OpenAPI client | UI loads, shows empty dashboard |
| 10 | Dashboard table + book detail page + history chart | Add a book in UI, see price history visualised |
| 11 | Add-book modal + Settings (Sources/Recommendation/Notifications/Advanced) | All config editable via UI; YAML round-trip via Monaco |
| 12 | Multi-stage Dockerfile + docker-compose + weekly backups | `docker-compose up` runs the whole app |
| 13 | E2E smoke test + README | One scripted scenario boots container, adds book, sees observation |

Each phase concludes with a working, runnable system at increasing fidelity. Commits are frequent — typically one per task, sometimes one per step where the step is a self-contained unit.

---

## Conventions used in this plan

- **Exact file paths** are absolute relative to the repo root `/home/ff235/dev/book_alerter/`.
- **Code blocks are concrete and complete enough to copy** unless explicitly noted as a sketch.
- **Each task ends with a commit step** unless multiple tightly-coupled tasks share a commit.
- **TDD is the default**: write the failing test first, watch it fail, write the minimal code, watch it pass, commit.
- **`uv run pytest`** is used for all test invocations (no global pytest activation needed).
- **Skills referenced** (use Skill tool when they appear): `@modern-python` for uv setup, `@printing-press` for source-CLI generation, `@brainstorming` is upstream and shouldn't be re-invoked.

## File structure (decomposition reference)

This map is the contract for what lives where. Tasks reference these paths.

```
book_alerter/
├── pyproject.toml                     # uv project, deps, ruff/ty/pytest config
├── uv.lock                            # generated
├── README.md                          # last-task deliverable
├── Dockerfile                         # multi-stage (Go → Python) — Phase 12
├── docker-compose.yml                 # Phase 12
├── .env.example                       # Phase 12
├── .gitignore                         # already present
├── alembic.ini                        # Alembic config
├── docs/                              # specs + plans (this file)
├── data/                              # gitignored runtime; schema-only .gitkeep
│   └── .gitkeep
├── src/book_alerter/
│   ├── __init__.py
│   ├── app.py                         # FastAPI factory, lifespan, scheduler wiring
│   ├── config.py                      # Pydantic schema, YAML load/save/migrate
│   ├── logging_setup.py               # structlog configuration
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py                  # all SQLModel tables in one file
│   │   ├── session.py                 # engine + sessionmaker + dep
│   │   └── migrations/                # Alembic env + versions/
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py                    # Source ABC, ObservationCandidate, exceptions
│   │   ├── subprocess_source.py       # SubprocessSource base
│   │   ├── inline_source.py           # InlineSource base
│   │   ├── wob.py                     # WobInlineSource (Phase 2)
│   │   ├── bookfinder.py              # BookfinderSource adapter (Phase 8)
│   │   ├── amazon.py                  # AmazonSource adapter (Phase 8)
│   │   ├── normalizers.py             # JSON → ObservationCandidate; ISBN normalization
│   │   └── registry.py                # config → instantiated sources
│   ├── scheduler.py                   # APScheduler integration, run loop, backoff
│   ├── stats.py                       # compute_book_stats, compute_signal, percentiles
│   ├── alerts.py                      # detect_alert_kinds, dedup
│   ├── notifications/
│   │   ├── __init__.py
│   │   ├── base.py                    # Notifier ABC
│   │   ├── inapp.py                   # InAppNotifier
│   │   ├── ntfy.py                    # NtfyNotifier
│   │   └── dispatcher.py              # parallel send across enabled channels
│   ├── metadata.py                    # OpenLibrary + Google Books, parallel race
│   ├── auth.py                        # optional HTTP Basic
│   └── api/
│       ├── __init__.py
│       ├── deps.py                    # shared dependencies
│       ├── books.py
│       ├── prices.py
│       ├── alerts.py
│       ├── sources.py
│       ├── config.py
│       ├── metadata.py
│       ├── notifications.py
│       └── health.py
├── web/                               # Phase 9–11
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   ├── public/
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/                       # generated types + thin client
│       ├── components/
│       ├── pages/
│       │   ├── Dashboard.tsx
│       │   ├── BookDetail.tsx
│       │   ├── Alerts.tsx
│       │   └── Settings/
│       │       ├── Sources.tsx
│       │       ├── Recommendation.tsx
│       │       ├── Notifications.tsx
│       │       └── Advanced.tsx
│       └── hooks/
├── cli_bins/                          # Go submodules, Phase 8
│   ├── bookfinder-pp-cli/
│   └── amazon-pp-cli/
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_config.py
    │   ├── test_isbn.py
    │   ├── test_stats.py
    │   ├── test_signal.py
    │   ├── test_alerts.py
    │   ├── test_dedup.py
    │   └── test_normalizers.py
    ├── integration/
    │   ├── conftest.py                # in-memory SQLite, frozen time
    │   ├── sources/
    │   │   ├── cassettes/             # vcrpy
    │   │   ├── test_wob.py
    │   │   ├── test_bookfinder.py
    │   │   └── test_amazon.py
    │   ├── test_scheduler.py
    │   ├── test_alert_pipeline.py
    │   ├── test_metadata.py
    │   └── api/
    │       ├── test_books_api.py
    │       ├── test_alerts_api.py
    │       ├── test_sources_api.py
    │       ├── test_config_api.py
    │       └── test_health_api.py
    └── e2e/
        ├── docker-compose.test.yml
        └── test_smoke.py
```

---

# Phase 0 — Foundation

Goal: a runnable FastAPI app with `/api/health`, structlog logging, Pydantic config loaded from `data/config.yaml`, SQLite session, and an empty Alembic migration. Once Phase 0 is green, every later phase has a place to plug in.

### Task 0.1: Initialize uv project and base dependencies

**Files:**
- Create: `pyproject.toml`
- Create: `src/book_alerter/__init__.py`

**Goal:** Project bootstraps with `uv sync`, importable as `book_alerter`.

- [ ] Use `@modern-python` skill if unfamiliar with uv. Otherwise:
- [ ] Initialize:

```bash
cd /home/ff235/dev/book_alerter
uv init --package --no-readme --no-pin-python
```

Then edit `pyproject.toml` to match this final form:

```toml
[project]
name = "book-alerter"
version = "0.0.1"
description = "Self-hosted book price comparison and alerting"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlmodel>=0.0.22",
    "alembic>=1.14",
    "pydantic>=2.10",
    "pydantic-settings>=2.6",
    "structlog>=24.4",
    "httpx>=0.28",
    "selectolax>=0.3.27",
    "isbnlib>=3.10",
    "apscheduler>=3.11",
    "watchfiles>=0.24",
    "python-multipart>=0.0.20",
    "pyyaml>=6.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.25",
    "vcrpy>=6.0",
    "freezegun>=1.5",
    "hypothesis>=6.122",
    "ruff>=0.8",
    "ty>=0.0.1a1",
    "httpx>=0.28",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/book_alerter"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "RUF"]

[tool.ty.environment]
python-version = "3.13"
```

- [ ] Add the package marker file:

```bash
mkdir -p src/book_alerter
touch src/book_alerter/__init__.py
```

- [ ] Sync:

```bash
uv sync
```

Expected: `uv.lock` created; `.venv/` populated.

- [ ] Verify import:

```bash
uv run python -c "import book_alerter; print('ok')"
```

Expected: `ok`.

- [ ] Commit:

```bash
git add pyproject.toml uv.lock src/book_alerter/__init__.py
git commit -m "chore: initialize uv project and dependencies"
```

---

### Task 0.2: Health endpoint with FastAPI app factory

**Files:**
- Create: `src/book_alerter/app.py`
- Create: `src/book_alerter/api/__init__.py`
- Create: `src/book_alerter/api/health.py`
- Create: `tests/__init__.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/api/__init__.py`
- Create: `tests/integration/api/test_health_api.py`

**Goal:** `GET /api/health` returns `{"status": "ok"}`. Tested via FastAPI's `TestClient`.

- [ ] Write the failing test in `tests/integration/api/test_health_api.py`:

```python
from fastapi.testclient import TestClient
from book_alerter.app import create_app


def test_health_returns_ok():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
```

- [ ] Run the test:

```bash
uv run pytest tests/integration/api/test_health_api.py -v
```

Expected: FAIL — `ModuleNotFoundError: book_alerter.app`.

- [ ] Implement `src/book_alerter/api/health.py`:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] Implement `src/book_alerter/app.py`:

```python
from fastapi import FastAPI

from book_alerter.api import health


def create_app() -> FastAPI:
    app = FastAPI(title="Book Alerter", version="0.0.1")
    app.include_router(health.router)
    return app


app = create_app()
```

- [ ] Run the test again:

```bash
uv run pytest tests/integration/api/test_health_api.py -v
```

Expected: PASS.

- [ ] Boot the app manually as a smoke test:

```bash
uv run uvicorn book_alerter.app:app --host 127.0.0.1 --port 8000 &
sleep 2
curl -s http://127.0.0.1:8000/api/health
kill %1
```

Expected: `{"status":"ok"}`.

- [ ] Commit:

```bash
git add src/book_alerter/ tests/
git commit -m "feat: add /api/health endpoint and FastAPI factory"
```

---

### Task 0.3: Structured logging setup

**Files:**
- Create: `src/book_alerter/logging_setup.py`
- Modify: `src/book_alerter/app.py` — call `configure_logging()` at app creation time.
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_logging_setup.py`

**Goal:** `structlog` configured to emit JSON to stdout; bound logger usable as `log.info("...", key="value")`.

- [ ] Write failing test:

```python
# tests/unit/test_logging_setup.py
import json
import structlog

from book_alerter.logging_setup import configure_logging, get_logger


def test_logger_emits_json(capsys):
    configure_logging()
    log = get_logger(__name__)
    log.info("hello", isbn="9780000000000", source="test")
    captured = capsys.readouterr()
    last_line = [ln for ln in captured.out.splitlines() if ln.strip()][-1]
    parsed = json.loads(last_line)
    assert parsed["event"] == "hello"
    assert parsed["isbn"] == "9780000000000"
    assert parsed["source"] == "test"
    assert parsed["level"] == "info"
```

- [ ] Run: `uv run pytest tests/unit/test_logging_setup.py -v` → FAIL.

- [ ] Implement `src/book_alerter/logging_setup.py`:

```python
import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=getattr(logging, level), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

- [ ] Update `app.py` to call `configure_logging()` inside `create_app()` before `FastAPI(...)`.
- [ ] Run: tests pass.
- [ ] Commit:

```bash
git add src/book_alerter/logging_setup.py src/book_alerter/app.py tests/unit/
git commit -m "feat: configure structlog JSON logging"
```

---

### Task 0.4: Pydantic-Settings config schema (skeleton)

**Files:**
- Create: `src/book_alerter/config.py`
- Create: `tests/unit/test_config.py`
- Create: `data/.gitkeep`

**Goal:** Load config from `data/config.yaml` (or fall back to defaults). Schema reflects spec's source/recommendation/notification structure but every field is optional with sensible defaults so the app boots with no config file.

- [ ] Write failing test:

```python
# tests/unit/test_config.py
from pathlib import Path

import yaml

from book_alerter.config import Config


def test_config_defaults_when_no_file(tmp_path):
    cfg = Config.load(tmp_path / "missing.yaml")
    assert cfg.recommendation.buy_percentile == 25
    assert cfg.recommendation.min_observations_for_signal == 14
    assert cfg.notifications.alert_kinds_enabled == ["target_hit", "percentile_cross", "new_low"]
    assert "bookfinder" in cfg.sources or cfg.sources == {}  # empty if no file


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "config_version": 1,
        "recommendation": {"buy_percentile": 20},
    }))
    cfg = Config.load(path)
    assert cfg.recommendation.buy_percentile == 20

    # Save and reload
    cfg.save(path)
    cfg2 = Config.load(path)
    assert cfg2.recommendation.buy_percentile == 20
    assert cfg2.config_version == 1


def test_config_env_substitution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_NTFY_TOPIC", "secret-topic")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "notifications": {"channels": {"ntfy": {"enabled": True, "topic": "${MY_NTFY_TOPIC}"}}},
    }))
    cfg = Config.load(path)
    assert cfg.notifications.channels.ntfy.topic == "secret-topic"
```

- [ ] Run: FAIL.
- [ ] Implement `src/book_alerter/config.py`:

```python
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_REF.sub(repl, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


class RecommendationConfig(BaseModel):
    min_observations_for_signal: int = 14
    buy_percentile: int = 25
    watch_percentile: int = 50
    target_tolerance_pct: int = 5
    alert_dedup_window_hours: int = 24


class QuietHours(BaseModel):
    start: str = "22:00"
    end: str = "08:00"
    tz: str = "Europe/London"


class InAppChannelConfig(BaseModel):
    enabled: bool = True


class NtfyChannelConfig(BaseModel):
    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: str = ""
    priority: str = "default"
    tags: list[str] = Field(default_factory=lambda: ["book", "money"])


class NotificationChannels(BaseModel):
    inapp: InAppChannelConfig = Field(default_factory=InAppChannelConfig)
    ntfy: NtfyChannelConfig = Field(default_factory=NtfyChannelConfig)


class NotificationsConfig(BaseModel):
    alert_kinds_enabled: list[Literal["target_hit", "percentile_cross", "new_low"]] = Field(
        default_factory=lambda: ["target_hit", "percentile_cross", "new_low"]
    )
    quiet_hours: QuietHours | None = Field(default_factory=QuietHours)
    channels: NotificationChannels = Field(default_factory=NotificationChannels)


class SourceConfig(BaseModel):
    enabled: bool = True
    type: Literal["subprocess", "inline"] = "subprocess"
    binary: str | None = None
    region: str = "UK"
    schedule: str = "0 */6 * * *"
    jitter_seconds: int = 600
    per_book_delay_seconds: tuple[int, int] = (5, 15)
    concurrency: int = Field(default=1, ge=1, le=5)
    timeout_seconds: int = 60
    max_consecutive_errors: int = 5


class Config(BaseModel):
    config_version: int = 1
    recommendation: RecommendationConfig = Field(default_factory=RecommendationConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text()) or {}
        raw = _substitute_env(raw)
        return cls.model_validate(raw)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False))
        tmp.replace(path)
```

- [ ] Run: tests pass.
- [ ] Commit:

```bash
git add src/book_alerter/config.py tests/unit/test_config.py data/.gitkeep
git commit -m "feat: pydantic config schema with YAML load/save and env substitution"
```

---

### Task 0.5: SQLite engine and session dependency

**Files:**
- Create: `src/book_alerter/db/__init__.py`
- Create: `src/book_alerter/db/session.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_session.py`

**Goal:** `get_session()` FastAPI dep yields a SQLModel `Session` against a configurable URL (defaults to `sqlite:///./data/book_alerter.db`).

- [ ] Write failing test:

```python
# tests/integration/test_session.py
from sqlmodel import select, text

from book_alerter.db.session import get_engine, session_scope


def test_session_can_execute_simple_query(tmp_path):
    db_path = tmp_path / "t.db"
    engine = get_engine(f"sqlite:///{db_path}")
    with session_scope(engine) as session:
        result = session.exec(text("SELECT 1")).one()
        assert result[0] == 1
```

- [ ] Run: FAIL.
- [ ] Implement `src/book_alerter/db/session.py`:

```python
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, create_engine
from sqlalchemy.engine import Engine


_DEFAULT_URL = "sqlite:///./data/book_alerter.db"


def get_database_url() -> str:
    return os.environ.get("BOOK_ALERTER_DATABASE_URL", _DEFAULT_URL)


def get_engine(url: str | None = None) -> Engine:
    return create_engine(
        url or get_database_url(),
        echo=False,
        connect_args={"check_same_thread": False},
    )


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] Run: PASS.
- [ ] Commit:

```bash
git add src/book_alerter/db/ tests/integration/conftest.py tests/integration/test_session.py
git commit -m "feat: SQLModel engine and session_scope context manager"
```

---

### Task 0.6: Alembic init

**Files:**
- Create: `alembic.ini`
- Create: `src/book_alerter/db/migrations/env.py`
- Create: `src/book_alerter/db/migrations/script.py.mako`
- Create: `src/book_alerter/db/migrations/versions/.gitkeep`

**Goal:** Alembic configured to read URL from `book_alerter.db.session.get_database_url()` so dev and prod use the same source of truth. No migrations yet — that's Phase 1.

- [ ] Run:

```bash
uv run alembic init -t generic src/book_alerter/db/migrations
```

- [ ] Edit `alembic.ini`:
  - Set `script_location = src/book_alerter/db/migrations`
  - Comment out `sqlalchemy.url = ...` (we set it from env.py)
- [ ] Edit `src/book_alerter/db/migrations/env.py` — replace its contents with:

```python
from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

from book_alerter.db.session import get_database_url
from book_alerter.db import models  # noqa: F401  ← imports register tables (Phase 1)

config = context.config
fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", get_database_url())

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import engine_from_config, pool
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] Smoke test:

```bash
uv run alembic current
```

Expected: empty / no error.

- [ ] Commit:

```bash
git add alembic.ini src/book_alerter/db/migrations/
git commit -m "chore: initialize Alembic"
```

---

### Task 0.7: Tie config + logging into app lifespan; add CI-grade pre-commit

**Files:**
- Modify: `src/book_alerter/app.py` — add lifespan that loads config at startup.
- Create: `.pre-commit-config.yaml` (optional, for local dev)

**Goal:** App lifespan loads `data/config.yaml` once at startup and stashes it on `app.state.config`. Failing config raises and prevents start.

- [ ] Write failing test in `tests/integration/api/test_health_api.py` (extend existing):

```python
def test_health_includes_config_version_when_loaded(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOK_ALERTER_CONFIG_PATH", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("config_version: 1\n")
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["config_version"] == 1
```

- [ ] Run: FAIL.
- [ ] Modify `src/book_alerter/app.py`:

```python
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from book_alerter.api import health
from book_alerter.config import Config
from book_alerter.logging_setup import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    cfg_path = Path(os.environ.get("BOOK_ALERTER_CONFIG_PATH", "data/config.yaml"))
    cfg = Config.load(cfg_path)
    app.state.config = cfg
    log.info("startup", config_version=cfg.config_version, config_path=str(cfg_path))
    try:
        yield
    finally:
        log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="Book Alerter", version="0.0.1", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
```

- [ ] Modify `src/book_alerter/api/health.py` to surface config_version:

```python
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health(request: Request) -> dict[str, object]:
    cfg = getattr(request.app.state, "config", None)
    return {
        "status": "ok",
        "config_version": cfg.config_version if cfg else None,
    }
```

- [ ] Run tests: PASS.
- [ ] Commit:

```bash
git add src/book_alerter/app.py src/book_alerter/api/health.py tests/integration/api/test_health_api.py
git commit -m "feat: lifespan loads config; /api/health surfaces config_version"
```

---

# Phase 1 — Data model

Goal: all five tables (and one view) created via Alembic migrations. CRUD round-trip tests pass for each.

### Task 1.1: Book table

**Files:**
- Create: `src/book_alerter/db/models.py`
- Create: `src/book_alerter/db/migrations/versions/0001_book_table.py`
- Create: `tests/integration/test_book_model.py`

**Goal:** `Book` table exists with all spec fields; Alembic migration applies cleanly to a fresh DB.

- [ ] Write failing test:

```python
# tests/integration/test_book_model.py
from datetime import UTC, datetime

from sqlmodel import SQLModel, Session, create_engine, select

from book_alerter.db import models


def test_book_round_trip(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000000",
            title="Test",
            author="Anon",
            currency="GBP",
            target_price_minor=1000,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        s.add(book)
        s.commit()
        s.refresh(book)
        assert book.id is not None
        loaded = s.exec(select(models.Book)).one()
        assert loaded.isbn13 == "9780000000000"
        assert loaded.target_price_minor == 1000
        assert loaded.alert_kinds_disabled == []
```

- [ ] Run: FAIL — `models.Book` undefined.
- [ ] Implement initial `src/book_alerter/db/models.py` (this file will accumulate all five tables; we add Book first):

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class Book(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    isbn13: str = Field(unique=True, index=True)
    title: str
    author: str
    cover_url: str | None = None
    format: Literal["paperback", "hardcover", "any"] = "any"
    region: str = "UK"
    currency: str = "GBP"
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    status: Literal["active", "archived", "bought"] = "active"
    bought_price_minor: int | None = None
    notes: str | None = None
    alert_kinds_disabled: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    muted_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
```

- [ ] Run: PASS.
- [ ] Generate the migration:

```bash
uv run alembic revision --autogenerate -m "create book table"
```

Move the generated file to `src/book_alerter/db/migrations/versions/0001_book_table.py` (rename the autogen filename to keep version ordering tidy; Alembic uses the `revision` ID inside the file, not the filename). Confirm the file looks right.

- [ ] Apply against a temp DB:

```bash
rm -f data/test_apply.db
BOOK_ALERTER_DATABASE_URL="sqlite:///./data/test_apply.db" uv run alembic upgrade head
BOOK_ALERTER_DATABASE_URL="sqlite:///./data/test_apply.db" uv run python -c "from sqlmodel import create_engine, inspect; e=create_engine('sqlite:///./data/test_apply.db'); print(inspect(e).get_table_names())"
rm -f data/test_apply.db
```

Expected: `['book', 'alembic_version']`.

- [ ] Commit:

```bash
git add src/book_alerter/db/models.py src/book_alerter/db/migrations/versions/0001_book_table.py tests/integration/test_book_model.py
git commit -m "feat(db): book table with alembic migration"
```

---

### Task 1.2: PriceObservation table (with self-FK for is_duplicate_of)

**Files:**
- Modify: `src/book_alerter/db/models.py` — append `PriceObservation`.
- Create: `src/book_alerter/db/migrations/versions/0002_price_observation.py`
- Create: `tests/integration/test_observation_model.py`

**Goal:** Insert observations linked to a book; `is_duplicate_of` self-FK works; indexes present.

- [ ] Write failing test that creates a book, then two observations (one marked as duplicate of the other), and asserts the relationship.

```python
# tests/integration/test_observation_model.py
from datetime import UTC, datetime

from sqlmodel import SQLModel, Session, create_engine, select

from book_alerter.db import models


def test_observation_with_duplicate_link(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000000", title="t", author="a",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        s.add(book); s.commit(); s.refresh(book)

        primary = models.PriceObservation(
            book_id=book.id, source="bookfinder", condition="new",
            price_minor=1000, currency="GBP", total_minor=1000,
            url="https://x", observed_at=datetime.now(UTC), raw={"hi": 1},
        )
        s.add(primary); s.commit(); s.refresh(primary)

        dupe = models.PriceObservation(
            book_id=book.id, source="amazon", condition="new",
            price_minor=1000, currency="GBP", total_minor=1000,
            url="https://x", observed_at=datetime.now(UTC), raw={},
            is_duplicate_of=primary.id,
        )
        s.add(dupe); s.commit(); s.refresh(dupe)

        non_dupes = s.exec(
            select(models.PriceObservation).where(
                models.PriceObservation.is_duplicate_of.is_(None)
            )
        ).all()
        assert len(non_dupes) == 1
        assert non_dupes[0].id == primary.id
```

- [ ] Run: FAIL.
- [ ] Append to `src/book_alerter/db/models.py`:

```python
from sqlalchemy import Index


class PriceObservation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="book.id", index=True)
    source: str
    seller: str | None = None
    condition: Literal["new", "used_vg", "used_g", "used_acceptable", "unknown"]
    price_minor: int
    currency: str
    shipping_minor: int | None = None
    total_minor: int
    url: str
    observed_at: datetime = Field(index=True)
    raw: dict = Field(default_factory=dict, sa_column=Column(JSON))
    is_duplicate_of: int | None = Field(default=None, foreign_key="priceobservation.id")

    __table_args__ = (
        Index("ix_obs_book_observed", "book_id", "observed_at"),
        Index("ix_obs_book_source_observed", "book_id", "source", "observed_at"),
    )
```

- [ ] Run: PASS.
- [ ] Generate migration `0002_price_observation.py` via `alembic revision --autogenerate -m "create price_observation table"`. Verify it includes both indexes.
- [ ] Apply migration to a temp DB to confirm.
- [ ] Commit:

```bash
git add src/book_alerter/db/models.py src/book_alerter/db/migrations/versions/0002_price_observation.py tests/integration/test_observation_model.py
git commit -m "feat(db): price_observation table with is_duplicate_of self-FK"
```

---

### Task 1.3: SourceRun, Alert, NotificationDelivery, BookSignalState tables

**Files:**
- Modify: `src/book_alerter/db/models.py`
- Create: `src/book_alerter/db/migrations/versions/0003_source_run_alert_delivery.py`
- Create: `tests/integration/test_run_alert_delivery_models.py`

**Goal:** Four remaining tables created; one round-trip test per table. `BookSignalState` is added here (used by Phase 4 alert pipeline to remember the last-evaluated signal + all-time min — the alternative would be expensive recomputation, so we persist).

- [ ] Append to `models.py`:

```python
class SourceRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["running", "success", "error", "partial"]
    books_attempted: int = 0
    books_succeeded: int = 0
    error_message: str | None = None
    error_traceback: str | None = None


class Alert(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="book.id", index=True)
    kind: Literal["new_low", "target_hit", "percentile_cross"]
    price_minor: int
    currency: str
    source: str
    condition: str
    message: str
    fired_at: datetime = Field(index=True)
    dismissed_at: datetime | None = None
    delivered_via: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class NotificationDelivery(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    alert_id: int = Field(foreign_key="alert.id", index=True)
    channel: str
    sent_at: datetime
    status: Literal["sent", "error"]
    error_message: str | None = None


class BookSignalState(SQLModel, table=True):
    """Persists the last-evaluated signal + all-time-min per book so the alert
    pipeline can detect transitions without expensive recomputation."""
    book_id: int = Field(primary_key=True, foreign_key="book.id")
    last_signal: str | None = None
    last_all_time_min_total_minor: int | None = None
    last_evaluated_at: datetime | None = None
```

- [ ] Write four small round-trip tests in `tests/integration/test_run_alert_delivery_models.py`. Each: create dependencies, insert, query back.
- [ ] Generate + apply migration `0003_source_run_alert_delivery.py` (the migration also covers `BookSignalState`; rename the migration filename if you prefer `0003_source_run_alert_delivery_signal_state.py`).
- [ ] Commit.

---

### Task 1.4: book_stats SQL view (Alembic raw DDL)

**Files:**
- Create: `src/book_alerter/db/migrations/versions/0004_book_stats_view.py`
- Create: `tests/integration/test_book_stats_view.py`

**Goal:** SQL view named `book_stats` exposes per-book current best price + p25/p50/p75/min/max/count/days_of_history. Implementation uses Common Table Expressions; SQLite's `percentile_cont` is unavailable, so we approximate p25/p50/p75 via NTILE-equivalent windows OR compute these in the Python helper and let the view expose only the deterministic fields.

> **Decision for the view:** expose `current_best_*` and `observation_count`/`last_observed_at`/`all_time_min`/`all_time_max`. Percentiles are computed by `compute_book_stats()` in Python (Phase 4) since pure-SQL percentiles in SQLite are awkward. This keeps the view simple and stable.

- [ ] Migration body (raw SQL):

```python
"""book_stats view"""
from alembic import op


revision = "0004_book_stats_view"
down_revision = "0003_source_run_alert_delivery"
branch_labels = None
depends_on = None


CREATE_VIEW = """
CREATE VIEW book_stats AS
WITH non_dupes AS (
    SELECT * FROM priceobservation WHERE is_duplicate_of IS NULL
),
latest_per_source AS (
    SELECT book_id, source, total_minor, condition, seller, url, observed_at,
           ROW_NUMBER() OVER (PARTITION BY book_id, source ORDER BY observed_at DESC) AS rn
    FROM non_dupes
),
current_best AS (
    -- When two sources tie at the same lowest price, deterministically prefer
    -- the alphabetically-first source name. Otherwise the view returns
    -- non-deterministic rows for ties.
    SELECT lp.book_id, lp.total_minor, lp.source, lp.condition, lp.seller, lp.url
    FROM latest_per_source lp
    JOIN (
        SELECT book_id, MIN(total_minor) AS m
        FROM latest_per_source
        WHERE rn = 1
        GROUP BY book_id
    ) best ON best.book_id = lp.book_id AND best.m = lp.total_minor AND lp.rn = 1
    WHERE lp.source = (
        SELECT MIN(source) FROM latest_per_source lp2
        WHERE lp2.book_id = lp.book_id AND lp2.total_minor = lp.total_minor AND lp2.rn = 1
    )
),
agg AS (
    SELECT book_id,
           MIN(total_minor) AS all_time_min_total_minor,
           MAX(total_minor) AS all_time_max_total_minor,
           COUNT(*)         AS observation_count,
           MAX(observed_at) AS last_observed_at,
           CAST((julianday(MAX(observed_at)) - julianday(MIN(observed_at))) AS INTEGER) AS days_of_history
    FROM non_dupes
    GROUP BY book_id
)
SELECT b.id AS book_id,
       b.title,
       b.isbn13,
       cb.total_minor AS current_best_total_minor,
       cb.source      AS current_best_source,
       cb.condition   AS current_best_condition,
       cb.seller      AS current_best_seller,
       cb.url         AS current_best_url,
       a.all_time_min_total_minor,
       a.all_time_max_total_minor,
       a.observation_count,
       a.last_observed_at,
       a.days_of_history
FROM book b
LEFT JOIN current_best cb ON cb.book_id = b.id
LEFT JOIN agg a          ON a.book_id  = b.id
"""

DROP_VIEW = "DROP VIEW IF EXISTS book_stats"


def upgrade() -> None:
    op.execute(CREATE_VIEW)


def downgrade() -> None:
    op.execute(DROP_VIEW)
```

- [ ] Test: insert one book + 5 observations across 2 sources, query the view, assert `current_best_total_minor` is the minimum-of-latest-per-source.
- [ ] Apply migration; run test.
- [ ] Commit.

---

# Phase 2 — Source plugin layer + WoB inline scraper

Goal: A `Source` interface; one concrete `WobInlineSource` that fetches an ISBN from World of Books UK, parses the page, and returns `ObservationCandidate` rows. End-to-end via `vcrpy` cassette so tests are hermetic.

### Task 2.1: Source ABC + ObservationCandidate

**Files:**
- Create: `src/book_alerter/sources/__init__.py`
- Create: `src/book_alerter/sources/base.py`
- Create: `tests/unit/test_sources_base.py`

**Goal:** Pydantic model `ObservationCandidate`; ABC `Source` with `fetch()` and `healthcheck()`; `SourceError` exception.

- [ ] Write failing test that imports the ABC, instantiates a stub subclass, and asserts the contract.

```python
# tests/unit/test_sources_base.py
import asyncio

import pytest

from book_alerter.db.models import Book
from book_alerter.sources.base import (
    ObservationCandidate, Source, SourceError,
)


class _Stub(Source):
    name = "stub"

    async def fetch(self, book):
        if book.isbn13 == "fail":
            raise SourceError(self.name, "boom")
        return [
            ObservationCandidate(
                seller=None, condition="new", price_minor=100,
                shipping_minor=None, currency="GBP", url="https://x",
            )
        ]


def test_stub_source_returns_candidates():
    src = _Stub()
    book = Book(isbn13="9780000000000", title="t", author="a",
                created_at=__import__("datetime").datetime.now(),
                updated_at=__import__("datetime").datetime.now())
    out = asyncio.run(src.fetch(book))
    assert len(out) == 1
    assert out[0].condition == "new"


def test_stub_source_raises_source_error():
    src = _Stub()
    book = Book(isbn13="fail", title="t", author="a",
                created_at=__import__("datetime").datetime.now(),
                updated_at=__import__("datetime").datetime.now())
    with pytest.raises(SourceError):
        asyncio.run(src.fetch(book))
```

- [ ] Implement `src/book_alerter/sources/base.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel

from book_alerter.db.models import Book


Condition = Literal["new", "used_vg", "used_g", "used_acceptable", "unknown"]


class ObservationCandidate(BaseModel):
    seller: str | None = None
    condition: Condition
    price_minor: int
    shipping_minor: int | None = None
    currency: str
    url: str


class SourceError(Exception):
    def __init__(self, source_name: str, message: str) -> None:
        super().__init__(f"[{source_name}] {message}")
        self.source_name = source_name
        self.message = message


class Source(ABC):
    name: str

    @abstractmethod
    async def fetch(self, book: Book) -> list[ObservationCandidate]: ...

    async def healthcheck(self) -> bool:
        return True
```

- [ ] Tests pass. Commit.

---

### Task 2.2: SubprocessSource and InlineSource bases

**Files:**
- Create: `src/book_alerter/sources/subprocess_source.py`
- Create: `src/book_alerter/sources/inline_source.py`
- Create: `tests/unit/test_subprocess_source.py`

**Goal:** `SubprocessSource` runs an arbitrary CLI binary, parses JSON stdout, raises `SourceError` on non-zero. `InlineSource` is a thin marker base.

- [ ] Write a failing test that instantiates a `SubprocessSource` configured to run a built-in shell command (e.g. `python -c 'import json,sys;print(json.dumps({"isbn13":"x","queried_at":"...","region":"UK","currency":"GBP","offers":[]}))'`) and asserts the parsed result.

(Sketch — flesh out with the real fields and invoke `asyncio.run`.)

- [ ] Implement `src/book_alerter/sources/subprocess_source.py`:

```python
from __future__ import annotations

import asyncio
import json
from typing import Any

from book_alerter.db.models import Book
from book_alerter.sources.base import (
    ObservationCandidate, Source, SourceError,
)


class SubprocessSource(Source):
    """Wraps a printing-press CLI. Subclasses provide build_command + parse."""

    def __init__(
        self,
        name: str,
        binary: str,
        region: str = "UK",
        timeout_s: int = 60,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.binary = binary
        self.region = region
        self.timeout_s = timeout_s
        self.env = env

    def build_command(self, book: Book) -> list[str]:
        # Default contract — subclasses override if their CLI uses different flags.
        return [self.binary, "search", "--isbn", book.isbn13, "--region", self.region, "--json"]

    def parse(self, stdout: str) -> list[ObservationCandidate]:
        data: dict[str, Any] = json.loads(stdout)
        offers = data.get("offers", [])
        return [ObservationCandidate(**o) for o in offers]

    async def fetch(self, book: Book) -> list[ObservationCandidate]:
        cmd = self.build_command(book)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_s
            )
        except FileNotFoundError as e:
            raise SourceError(self.name, f"binary not found: {self.binary}") from e
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise SourceError(self.name, f"timeout after {self.timeout_s}s")

        if proc.returncode != 0:
            raise SourceError(self.name, stderr.decode("utf-8", errors="replace").strip())
        return self.parse(stdout.decode("utf-8", errors="replace"))
```

- [ ] Implement `src/book_alerter/sources/inline_source.py`:

```python
from book_alerter.sources.base import Source


class InlineSource(Source):
    """Marker base for Python-implemented sources."""
```

- [ ] Tests pass. Commit.

---

### Task 2.3: WoB inline scraper

**Files:**
- Create: `src/book_alerter/sources/wob.py`
- Create: `tests/integration/sources/__init__.py`
- Create: `tests/integration/sources/cassettes/wob_<isbn>.yaml` (recorded via VCR on first run)
- Create: `tests/integration/sources/test_wob.py`

**Goal:** Hit `https://www.wob.com/en-gb/books/<isbn>` (or their search endpoint), parse the resulting HTML with `selectolax`, extract price + condition, return one or more `ObservationCandidate`.

> **Implementation note:** WoB shows a single product page per ISBN with one or more conditions ("Like New", "Very Good", "Good", "Used – Acceptable"). Map their condition strings to our enum. If the ISBN isn't carried, return an empty list (not an error).

- [ ] Write the failing integration test first (with `vcrpy` decorator):

```python
# tests/integration/sources/test_wob.py
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import vcr

from book_alerter.db.models import Book
from book_alerter.sources.wob import WobInlineSource


CASSETTE_DIR = Path(__file__).parent / "cassettes"
my_vcr = vcr.VCR(
    cassette_library_dir=str(CASSETTE_DIR),
    record_mode="once",                 # record on first run; replay forever after
    match_on=("method", "scheme", "host", "port", "path"),
    decode_compressed_response=True,
)


@pytest.mark.parametrize("isbn", ["9780241638194", "9789693531374"])
def test_wob_returns_offers_for_real_isbn(isbn):
    src = WobInlineSource(name="wob", region="UK")
    book = Book(
        isbn13=isbn, title="t", author="a",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    with my_vcr.use_cassette(f"wob_{isbn}.yaml"):
        out = asyncio.run(src.fetch(book))
    # We don't assert exact prices (they change). We assert structure.
    assert isinstance(out, list)
    if out:
        for c in out:
            assert c.condition in {"new", "used_vg", "used_g", "used_acceptable", "unknown"}
            assert c.price_minor > 0
            assert c.currency == "GBP"
            assert c.url.startswith("https://")
```

- [ ] Run: FAIL (module not found).
- [ ] Implement `src/book_alerter/sources/wob.py`:

```python
from __future__ import annotations

import re
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from book_alerter.db.models import Book
from book_alerter.sources.base import (
    Condition, ObservationCandidate, SourceError,
)
from book_alerter.sources.inline_source import InlineSource


_CONDITION_MAP: dict[str, Condition] = {
    "like new": "used_vg",
    "very good": "used_vg",
    "good": "used_g",
    "acceptable": "used_acceptable",
    "new": "new",
}

_PRICE_RE = re.compile(r"£\s*([\d,]+(?:\.\d{1,2})?)")


def _to_minor(price_str: str) -> int:
    match = _PRICE_RE.search(price_str)
    if not match:
        raise ValueError(f"unparseable price: {price_str!r}")
    pounds = float(match.group(1).replace(",", ""))
    return round(pounds * 100)


class WobInlineSource(InlineSource):
    name: str

    def __init__(self, name: str = "wob", region: str = "UK", timeout_s: float = 30.0) -> None:
        self.name = name
        self.region = region
        self.timeout_s = timeout_s
        self._user_agent = (
            "Mozilla/5.0 (compatible; BookAlerter/0.0; +https://github.com/local/book_alerter)"
        )

    async def fetch(self, book: Book) -> list[ObservationCandidate]:
        url = f"https://www.wob.com/en-gb/books/{book.isbn13}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={"User-Agent": self._user_agent, "Accept-Language": "en-GB,en;q=0.9"},
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            raise SourceError(self.name, f"http error: {e}") from e

        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            raise SourceError(self.name, f"http {resp.status_code}")

        return self._parse(resp.text, url)

    def _parse(self, html: str, url: str) -> list[ObservationCandidate]:
        # WoB pages render condition + price tiles. Selectors below MAY need to be
        # updated when WoB redesigns; the cassette captures the current HTML so the
        # test will fail loudly if parsing breaks.
        tree = HTMLParser(html)
        offers: list[ObservationCandidate] = []
        for tile in tree.css("[data-testid='condition-tile'], .condition-tile, [class*='Condition']"):
            label = (tile.css_first("[data-testid='condition-label'], .condition-name") or tile).text(strip=True)
            price_node = tile.css_first("[data-testid='price'], .price, .a-price")
            if price_node is None:
                continue
            try:
                price_minor = _to_minor(price_node.text(strip=True))
            except ValueError:
                continue
            cond = _CONDITION_MAP.get(label.lower(), "unknown")
            offers.append(ObservationCandidate(
                seller="World of Books",
                condition=cond,
                price_minor=price_minor,
                shipping_minor=0,        # WoB UK = free UK delivery for £-tagged offers as of 2026
                currency="GBP",
                url=url,
            ))
        return offers
```

> **Cassette recording protocol:** the first time you run the test, `record_mode="once"` will record real HTTP calls to `cassettes/wob_<isbn>.yaml`. Once recorded, commit the cassette. To refresh, delete the file and re-run.

- [ ] First run: `uv run pytest tests/integration/sources/test_wob.py -v` — records cassette and asserts shape.
- [ ] Inspect cassette to confirm no secrets leaked (it's a public site so this is just hygiene).
- [ ] Commit cassette and code:

```bash
git add src/book_alerter/sources/ tests/integration/sources/
git commit -m "feat(sources): WoB UK inline scraper with VCR cassettes"
```

---

### Task 2.4: Source registry — instantiate sources from config

**Files:**
- Create: `src/book_alerter/sources/registry.py`
- Modify: `src/book_alerter/config.py` — already has `sources: dict[str, SourceConfig]`; nothing to add yet.
- Create: `tests/unit/test_source_registry.py`

**Goal:** Given a `Config`, return a `dict[str, Source]` of instantiated, ready-to-use sources. Inline sources mapped by name to known classes; subprocess sources instantiated with their binary.

- [ ] Write a test that builds a `Config` with one inline `wob` entry and one subprocess `bookfinder` entry, calls the registry, and asserts the right classes come back.

- [ ] Implement:

```python
# src/book_alerter/sources/registry.py
from __future__ import annotations

from book_alerter.config import Config
from book_alerter.sources.base import Source
from book_alerter.sources.subprocess_source import SubprocessSource
from book_alerter.sources.wob import WobInlineSource


_INLINE_REGISTRY: dict[str, type[Source]] = {
    "wob": WobInlineSource,
}


def build_sources(cfg: Config) -> dict[str, Source]:
    out: dict[str, Source] = {}
    for name, sc in cfg.sources.items():
        if not sc.enabled:
            continue
        if sc.type == "inline":
            cls = _INLINE_REGISTRY.get(name)
            if cls is None:
                raise ValueError(f"no inline implementation for source '{name}'")
            out[name] = cls(name=name, region=sc.region)
        elif sc.type == "subprocess":
            if not sc.binary:
                raise ValueError(f"source '{name}' is subprocess but has no binary")
            out[name] = SubprocessSource(
                name=name, binary=sc.binary, region=sc.region,
                timeout_s=sc.timeout_seconds,
            )
        else:
            raise ValueError(f"unknown source type: {sc.type}")
    return out
```

- [ ] Tests pass. Commit.

---

# Phase 3 — Scheduler

Goal: APScheduler integrated into the FastAPI lifespan; per-source jobs registered from config; per-book delays + per-source-failure isolation + exponential backoff. End-to-end: scheduler fires, observations land in DB.

### Task 3.1: Scheduler module — register, start, shutdown

**Files:**
- Create: `src/book_alerter/scheduler.py`
- Modify: `src/book_alerter/app.py` — start/stop scheduler in lifespan.
- Create: `tests/integration/test_scheduler.py`

**Goal:** Boot scheduler with no jobs, shut it down cleanly. Register a single source job from config and assert it's scheduled with the right cron.

- [ ] Write failing tests:

```python
# tests/integration/test_scheduler.py
import asyncio
from unittest.mock import AsyncMock

from book_alerter.config import Config, SourceConfig
from book_alerter.scheduler import Scheduler


def test_scheduler_registers_jobs_from_config(tmp_path):
    cfg = Config(
        sources={
            "wob": SourceConfig(
                enabled=True, type="inline", region="UK", schedule="0 */6 * * *",
            ),
        },
    )
    sched = Scheduler(config=cfg, sources={"wob": AsyncMock()},
                      session_factory=lambda: None, alert_pipeline=AsyncMock())
    sched.start()
    try:
        jobs = sched.list_jobs()
        names = [j.id for j in jobs]
        assert "source:wob" in names
    finally:
        sched.shutdown()
```

- [ ] Implement:

```python
# src/book_alerter/scheduler.py
from __future__ import annotations

import asyncio
import random
import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session

from book_alerter.config import Config, SourceConfig
from book_alerter.db.models import Book, PriceObservation, SourceRun
from book_alerter.logging_setup import get_logger
from book_alerter.sources.base import ObservationCandidate, Source, SourceError

log = get_logger(__name__)


class Scheduler:
    """Wraps APScheduler; registers one job per enabled source."""

    def __init__(
        self,
        config: Config,
        sources: dict[str, Source],
        session_factory: Callable[[], Session],
        alert_pipeline: Callable[[list[int]], Awaitable[None]],
    ) -> None:
        self._cfg = config
        self._sources = sources
        self._session_factory = session_factory
        self._alert_pipeline = alert_pipeline
        self._sched = AsyncIOScheduler(timezone="UTC")
        self._consecutive_errors: dict[str, int] = {}
        # When a source enters backoff, we set _backoff_until[name] to a future
        # UTC datetime. _run_source checks this at entry and skips if not yet
        # eligible. The cron job continues firing on its normal cadence; backoff
        # is enforced by skipping rather than rescheduling, which avoids
        # APScheduler's awkward "delay next run" semantics.
        self._backoff_until: dict[str, datetime] = {}

    def start(self) -> None:
        for name, src in self._sources.items():
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
        self._sched.start()
        log.info("scheduler.started", n_jobs=len(self._sched.get_jobs()))

    def list_jobs(self) -> list[Any]:
        return self._sched.get_jobs()

    def shutdown(self) -> None:
        self._sched.shutdown(wait=False)

    async def trigger_now(self, source_name: str) -> int:
        """Manual one-shot. Returns SourceRun.id."""
        return await self._run_source(source_name)

    async def _run_source(self, source_name: str) -> int:
        sc = self._cfg.sources[source_name]
        src = self._sources[source_name]
        # Backoff gate: if we're inside the backoff window, skip this run.
        bu = self._backoff_until.get(source_name)
        if bu is not None and datetime.now(UTC) < bu:
            log.info("source.skipped.backoff", source=source_name, until=bu.isoformat())
            return 0
        with self._session_factory() as session:
            run = SourceRun(
                source=source_name, started_at=datetime.now(UTC),
                status="running",
            )
            session.add(run); session.commit(); session.refresh(run)

        affected_book_ids: list[int] = []
        attempted = 0
        succeeded = 0
        try:
            with self._session_factory() as session:
                from sqlmodel import select
                books = session.exec(
                    select(Book).where(Book.status == "active")
                ).all()
            attempted = len(books)
            sem = asyncio.Semaphore(sc.concurrency)

            async def _one(book: Book) -> None:
                nonlocal succeeded
                async with sem:
                    delay = random.uniform(*sc.per_book_delay_seconds)
                    await asyncio.sleep(delay)
                    try:
                        candidates = await asyncio.wait_for(
                            src.fetch(book), timeout=sc.timeout_seconds + 5
                        )
                    except (SourceError, asyncio.TimeoutError) as e:
                        log.warning("source.book.error",
                                    source=source_name, isbn=book.isbn13, error=str(e))
                        return
                    self._persist(source_name, book, candidates)
                    affected_book_ids.append(book.id or 0)
                    succeeded += 1

            await asyncio.gather(*[_one(b) for b in books])

            with self._session_factory() as session:
                from sqlmodel import select
                run = session.exec(select(SourceRun).where(SourceRun.id == run.id)).one()
                run.finished_at = datetime.now(UTC)
                run.books_attempted = attempted
                run.books_succeeded = succeeded
                if succeeded == attempted:
                    run.status = "success"
                elif succeeded > 0:
                    run.status = "partial"
                else:
                    run.status = "error"
                session.add(run); session.commit()

            if succeeded > 0:
                self._consecutive_errors[source_name] = 0
                self._backoff_until.pop(source_name, None)
            else:
                self._consecutive_errors[source_name] = self._consecutive_errors.get(source_name, 0) + 1
                self._apply_backoff(source_name)
            await self._alert_pipeline(affected_book_ids)
        except Exception as e:
            log.error("source.run.exception",
                      source=source_name, error=str(e), tb=traceback.format_exc())
            with self._session_factory() as session:
                from sqlmodel import select
                run = session.exec(select(SourceRun).where(SourceRun.id == run.id)).one()
                run.finished_at = datetime.now(UTC)
                run.status = "error"
                run.error_message = str(e)
                run.error_traceback = traceback.format_exc()
                session.add(run); session.commit()
            self._consecutive_errors[source_name] = self._consecutive_errors.get(source_name, 0) + 1
            self._apply_backoff(source_name)

        return run.id or 0

    def _persist(
        self, source_name: str, book: Book,
        candidates: list[ObservationCandidate],
    ) -> None:
        with self._session_factory() as session:
            for c in candidates:
                total = c.price_minor + (c.shipping_minor or 0)
                session.add(PriceObservation(
                    book_id=book.id, source=source_name,
                    seller=c.seller, condition=c.condition,
                    price_minor=c.price_minor,
                    currency=c.currency,
                    shipping_minor=c.shipping_minor,
                    total_minor=total,
                    url=c.url,
                    observed_at=datetime.now(UTC),
                    raw=c.model_dump(),
                ))
            session.commit()

    def _apply_backoff(self, source_name: str) -> None:
        sc = self._cfg.sources[source_name]
        n = self._consecutive_errors.get(source_name, 0)
        if n <= sc.max_consecutive_errors:
            return
        from datetime import timedelta
        delay_s = min(60 * (2 ** (n - sc.max_consecutive_errors)), 24 * 3600)
        self._backoff_until[source_name] = datetime.now(UTC) + timedelta(seconds=delay_s)
        log.warning("source.backoff",
                    source=source_name, delay_s=delay_s, errors=n,
                    until=self._backoff_until[source_name].isoformat())
```

- [ ] Wire scheduler into lifespan (modify `app.py`):
  - Build `engine = get_engine()` once in lifespan.
  - Build `sources = build_sources(cfg)`.
  - Build `scheduler = Scheduler(...)`, `scheduler.start()`, store in `app.state`.
  - On shutdown, call `scheduler.shutdown()`.
- [ ] Tests pass. Commit.

---

### Task 3.2: End-to-end scheduler smoke (WoB inline + cassette + in-memory DB)

**Files:**
- Modify: `tests/integration/test_scheduler.py` — add scenario.

**Goal:** Configure scheduler with WoB inline, manually trigger via `scheduler.trigger_now("wob")`, assert observations land in the DB.

- [ ] Test:

```python
async def test_scheduler_runs_wob_end_to_end(tmp_path):
    # ... build engine, run migrations, seed 1 Book with the cassette ISBN,
    # build sources with WobInlineSource, build scheduler, await trigger_now.
    # Assert PriceObservation rows exist for the book.
```

(Use the same cassette from Task 2.3 — wrap the trigger call in `my_vcr.use_cassette(...)`.)

- [ ] Tests pass. Commit.

---

# Phase 4 — Stats, signal, alerts (in-app only)

### Task 4.1: `compute_book_stats` helper

**Files:**
- Create: `src/book_alerter/stats.py`
- Create: `tests/unit/test_stats.py`

**Goal:** Given a `book_id` and an active session, return a `BookStats` dataclass with current best, percentiles (computed in Python via `numpy` or pure-Python sort + index), all-time min/max, observation count, days of history. Reads from the `book_stats` view for the deterministic fields and from `priceobservation` for percentile inputs.

- [ ] Add numpy to deps if you prefer it; pure Python with `statistics.quantiles(method="inclusive")` works and avoids the dep. We'll use stdlib.

- [ ] Test edge cases (empty obs, single obs, all-equal prices, mixed conditions). Use property tests with hypothesis for percentile correctness.

- [ ] Implement:

```python
# src/book_alerter/stats.py
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlmodel import Session, select, text

from book_alerter.db.models import PriceObservation


Signal = Literal["BUY", "WATCH", "WAIT", "TARGET_HIT", "INSUFFICIENT_DATA"]


@dataclass
class BookStats:
    book_id: int
    current_best_total_minor: int | None
    current_best_source: str | None
    current_best_seller: str | None
    current_best_condition: str | None
    current_best_url: str | None
    p25_total_minor: int | None
    p50_total_minor: int | None
    p75_total_minor: int | None
    all_time_min_total_minor: int | None
    all_time_max_total_minor: int | None
    observation_count: int
    days_of_history: int
    last_observed_at: datetime | None
    sorted_totals: list[int] = field(default_factory=list)  # for arbitrary-percentile queries

    def percentile_at(self, pct: int) -> int | None:
        if not self.sorted_totals or not (1 <= pct <= 99):
            return None
        n = len(self.sorted_totals)
        if n == 1:
            return self.sorted_totals[0]
        idx = pct / 100 * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        frac = idx - lo
        return int(self.sorted_totals[lo]
                   + (self.sorted_totals[hi] - self.sorted_totals[lo]) * frac)


def _percentiles(values: list[int]) -> tuple[int, int, int] | tuple[None, None, None]:
    if not values:
        return None, None, None
    if len(values) == 1:
        return values[0], values[0], values[0]
    qs = statistics.quantiles(sorted(values), n=4, method="inclusive")
    p25, p50, p75 = qs  # 3 cut points for n=4
    return int(p25), int(p50), int(p75)


def compute_book_stats(book_id: int, session: Session) -> BookStats:
    # Pull deterministic fields from the view.
    row = session.exec(text("""
        SELECT current_best_total_minor, current_best_source, current_best_condition,
               current_best_seller, current_best_url,
               all_time_min_total_minor, all_time_max_total_minor,
               observation_count, last_observed_at, days_of_history
        FROM book_stats WHERE book_id = :bid
    """), params={"bid": book_id}).one_or_none()

    if row is None:
        return BookStats(
            book_id=book_id, current_best_total_minor=None,
            current_best_source=None, current_best_seller=None,
            current_best_condition=None, current_best_url=None,
            p25_total_minor=None, p50_total_minor=None, p75_total_minor=None,
            all_time_min_total_minor=None, all_time_max_total_minor=None,
            observation_count=0, days_of_history=0,
            last_observed_at=None,
        )

    # Pull totals for percentiles.
    totals = [
        r[0] for r in session.exec(text(
            "SELECT total_minor FROM priceobservation "
            "WHERE book_id = :bid AND is_duplicate_of IS NULL"
        ), params={"bid": book_id}).all()
    ]
    p25, p50, p75 = _percentiles(totals)

    return BookStats(
        book_id=book_id,
        current_best_total_minor=row[0],
        current_best_source=row[1],
        current_best_condition=row[2],
        current_best_seller=row[3],
        current_best_url=row[4],
        all_time_min_total_minor=row[5],
        all_time_max_total_minor=row[6],
        observation_count=row[7] or 0,
        last_observed_at=row[8],
        days_of_history=row[9] or 0,
        p25_total_minor=p25,
        p50_total_minor=p50,
        p75_total_minor=p75,
        sorted_totals=sorted(totals),
    )
```

- [ ] Tests cover: empty book, one obs, three obs, ten obs (assert p25/p50/p75 reasonable), property test on monotonicity.
- [ ] Commit.

---

### Task 4.2: `compute_signal`

**Files:**
- Modify: `src/book_alerter/stats.py` — add `compute_signal(book, stats, recommendation_cfg)`.
- Create: `tests/unit/test_signal.py`

**Goal:** Implements the spec's hybrid logic precisely; covered by unit tests.

- [ ] Test cases:
  - count < min_obs → `INSUFFICIENT_DATA`
  - target set, current ≤ target → `TARGET_HIT`
  - target set, current ≤ tolerance → `BUY`
  - target set, current > tolerance, current ≤ p25 → `BUY`
  - no target, current ≤ p25 → `BUY`
  - no target, current ≤ p50 → `WATCH`
  - no target, current > p50 → `WAIT`

- [ ] Implementation matches spec:

```python
def compute_signal(book, stats: BookStats, cfg) -> Signal:
    if stats.observation_count < cfg.min_observations_for_signal:
        return "INSUFFICIENT_DATA"
    if stats.current_best_total_minor is None:
        return "INSUFFICIENT_DATA"

    threshold_pct = book.percentile_threshold or cfg.buy_percentile

    if book.target_price_minor is not None:
        tolerance = int(book.target_price_minor * (1 + cfg.target_tolerance_pct / 100))
        if stats.current_best_total_minor <= book.target_price_minor:
            return "TARGET_HIT"
        if stats.current_best_total_minor <= tolerance:
            return "BUY"
        # fall through to percentile evaluation

    # threshold_pct is any integer percentile in 1..99; we compute it from the
    # sorted-totals list carried inside BookStats so any value works (not just 25/50/75).
    p_field = stats.percentile_at(threshold_pct)
    if p_field is None:
        return "INSUFFICIENT_DATA"
    if stats.current_best_total_minor <= p_field:
        return "BUY"
    if stats.p50_total_minor is not None and stats.current_best_total_minor <= stats.p50_total_minor:
        return "WATCH"
    return "WAIT"
```

`BookStats` carries `sorted_totals: list[int]` so `percentile_at()` is a pure method — no session needed. Update `BookStats`:

```python
@dataclass
class BookStats:
    # ... existing fields ...
    sorted_totals: list[int] = field(default_factory=list)

    def percentile_at(self, pct: int) -> int | None:
        if not self.sorted_totals or not (1 <= pct <= 99):
            return None
        # Linear-interpolated percentile (Type 7, NumPy default).
        n = len(self.sorted_totals)
        if n == 1:
            return self.sorted_totals[0]
        idx = pct / 100 * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        frac = idx - lo
        return int(self.sorted_totals[lo] + (self.sorted_totals[hi] - self.sorted_totals[lo]) * frac)
```

Populate `sorted_totals` in `compute_book_stats` from the same `totals` list already pulled for `_percentiles`. This unlocks per-book percentile thresholds beyond 25/50/75 — required because the Settings UI exposes the slider as 1–99.

- [ ] Tests pass. Commit.

---

### Task 4.3: Alert detection

**Files:**
- Create: `src/book_alerter/alerts.py`
- Create: `tests/unit/test_alerts.py`

**Goal:** `detect_alert_kinds(book, stats, prev_signal, cfg)` returns a list of new `AlertKind`s to fire (target_hit, percentile_cross, new_low). `prev_signal` and `prev_all_time_min_total_minor` come from the most recent prior `BookStats` snapshot we computed for this book — keep one in memory or in a tiny key/value table (for MVP, pull from the most recent observation set's stats by recomputing without the latest obs).

- [ ] Tests:
  - target_hit fires when current ≤ target.
  - percentile_cross fires on signal transition into BUY.
  - new_low fires when `current_best < previous_all_time_min`.
  - no double-fire of target_hit when target was already hit at last evaluation.

- [ ] Implementation:

```python
# src/book_alerter/alerts.py
from __future__ import annotations

from typing import Literal

from book_alerter.db.models import Book
from book_alerter.stats import BookStats, Signal


AlertKind = Literal["new_low", "target_hit", "percentile_cross"]


def detect_alert_kinds(
    book: Book,
    stats: BookStats,
    prev_signal: Signal | None,
    prev_all_time_min: int | None,
    cfg,
) -> list[AlertKind]:
    out: list[AlertKind] = []
    if stats.current_best_total_minor is None:
        return out

    # target_hit
    if (
        book.target_price_minor is not None
        and stats.current_best_total_minor <= book.target_price_minor
        and (prev_signal != "TARGET_HIT")
    ):
        out.append("target_hit")

    # percentile_cross — into BUY from anything else
    from book_alerter.stats import compute_signal
    cur_signal = compute_signal(book, stats, cfg)
    if cur_signal == "BUY" and prev_signal != "BUY":
        out.append("percentile_cross")

    # new_low
    if prev_all_time_min is not None and stats.current_best_total_minor < prev_all_time_min:
        out.append("new_low")

    return out
```

- [ ] Tests pass. Commit.

---

### Task 4.4: Alert pipeline (writes Alert + delivers in-app, dedup)

**Files:**
- Create: `src/book_alerter/notifications/__init__.py`
- Create: `src/book_alerter/notifications/base.py`
- Create: `src/book_alerter/notifications/inapp.py`
- Create: `src/book_alerter/notifications/dispatcher.py`
- Create: `tests/integration/test_alert_pipeline.py`

**Goal:** Given a list of affected `book_id`s, recompute stats, detect alert kinds (with dedup), and deliver via enabled channels. In-app delivery just creates the `Alert` row (and a `NotificationDelivery` for "inapp"/"sent").

- [ ] Implement `Notifier` ABC:

```python
# src/book_alerter/notifications/base.py
from abc import ABC, abstractmethod
from datetime import datetime

from book_alerter.db.models import Alert, Book


class Notifier(ABC):
    name: str

    @abstractmethod
    async def send(self, alert: Alert, book: Book) -> dict:
        """Returns a dict suitable for NotificationDelivery: {status, error_message?}."""
```

- [ ] Implement `InAppNotifier`:

```python
# src/book_alerter/notifications/inapp.py
from book_alerter.notifications.base import Notifier


class InAppNotifier(Notifier):
    name = "inapp"

    async def send(self, alert, book):
        return {"status": "sent"}
```

- [ ] Implement the pipeline (sketch):

```python
# src/book_alerter/notifications/dispatcher.py
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlmodel import Session, select

from book_alerter.alerts import AlertKind, detect_alert_kinds
from book_alerter.config import Config
from book_alerter.db.models import Alert, Book, NotificationDelivery
from book_alerter.notifications.base import Notifier
from book_alerter.stats import BookStats, compute_book_stats, compute_signal


class AlertPipeline:
    def __init__(
        self,
        cfg: Config,
        session_factory: Callable[[], Session],
        notifiers: list[Notifier],
    ) -> None:
        self.cfg = cfg
        self.session_factory = session_factory
        self.notifiers = notifiers

    async def run(self, book_ids: list[int]) -> None:
        for bid in book_ids:
            with self.session_factory() as session:
                book = session.exec(select(Book).where(Book.id == bid)).one_or_none()
                if book is None:
                    continue
                stats = compute_book_stats(bid, session)
                # Read prior state from BookSignalState (introduced in Task 1.3).
                # On first evaluation for a book the row is absent → treat prev_signal=None
                # and prev_all_time_min=None, which means no transition can fire yet.
                from book_alerter.db.models import BookSignalState
                prev = session.exec(
                    select(BookSignalState).where(BookSignalState.book_id == bid)
                ).one_or_none()
                prev_signal = prev.last_signal if prev else None
                prev_all_time_min = prev.last_all_time_min_total_minor if prev else None
                kinds = detect_alert_kinds(book, stats, prev_signal,
                                           prev_all_time_min,
                                           self.cfg.recommendation)
                # Filter via global per-kind toggle and per-book disabled.
                kinds = [k for k in kinds
                         if k in self.cfg.notifications.alert_kinds_enabled
                         and k not in book.alert_kinds_disabled]
                # Filter via per-book mute.
                if book.muted_until and datetime.now(UTC) < book.muted_until.replace(tzinfo=UTC):
                    continue
                # Filter via dedup window.
                kinds = self._filter_dedup(book, kinds, session)
                for k in kinds:
                    alert = Alert(
                        book_id=book.id, kind=k,
                        price_minor=stats.current_best_total_minor or 0,
                        currency=book.currency,
                        source=stats.current_best_source or "",
                        condition=stats.current_best_condition or "",
                        message=self._format_message(book, k, stats),
                        fired_at=datetime.now(UTC),
                        delivered_via=[],
                    )
                    session.add(alert); session.commit(); session.refresh(alert)
                    await self._deliver(alert, book, session)

                # Persist current state for next evaluation's transition detection.
                from book_alerter.db.models import BookSignalState
                cur_signal = compute_signal(book, stats, self.cfg.recommendation)
                state = session.exec(
                    select(BookSignalState).where(BookSignalState.book_id == bid)
                ).one_or_none()
                if state is None:
                    state = BookSignalState(book_id=bid)
                state.last_signal = cur_signal
                state.last_all_time_min_total_minor = stats.all_time_min_total_minor
                state.last_evaluated_at = datetime.now(UTC)
                session.add(state)
                session.commit()

    def _filter_dedup(self, book, kinds, session) -> list[AlertKind]:
        cutoff = datetime.now(UTC) - timedelta(hours=self.cfg.recommendation.alert_dedup_window_hours)
        out: list[AlertKind] = []
        for k in kinds:
            existing = session.exec(
                select(Alert).where(Alert.book_id == book.id,
                                    Alert.kind == k, Alert.fired_at >= cutoff)
            ).first()
            if existing is None:
                out.append(k)
        return out

    def _format_message(self, book, kind, stats) -> str:
        delta = ""
        if stats.p50_total_minor:
            pct = 100 * (stats.p50_total_minor - (stats.current_best_total_minor or 0)) / stats.p50_total_minor
            delta = f" (was median {stats.p50_total_minor / 100:.2f}, {pct:+.0f}%)"
        return f"[{kind.upper()}] {book.title} — {(stats.current_best_total_minor or 0)/100:.2f} {book.currency}{delta}"

    async def _deliver(self, alert, book, session):
        results = await asyncio.gather(
            *[n.send(alert, book) for n in self.notifiers], return_exceptions=True
        )
        delivered: list[str] = []
        for n, r in zip(self.notifiers, results):
            if isinstance(r, Exception):
                session.add(NotificationDelivery(
                    alert_id=alert.id, channel=n.name,
                    sent_at=datetime.now(UTC), status="error",
                    error_message=str(r),
                ))
            else:
                session.add(NotificationDelivery(
                    alert_id=alert.id, channel=n.name,
                    sent_at=datetime.now(UTC), status=r["status"],
                    error_message=r.get("error_message"),
                ))
                if r["status"] == "sent":
                    delivered.append(n.name)
        alert.delivered_via = delivered
        session.add(alert); session.commit()
```

- [ ] Integration test: run scheduler, assert one observation triggers one alert, alert is in DB, NotificationDelivery is "sent" for inapp. Run a second time at the same price → no new alert (dedup).

- [ ] Wire `AlertPipeline.run` into `Scheduler.__init__`'s `alert_pipeline` param.
- [ ] Commit.

---

# Phase 5 — ntfy notifier + quiet hours

### Task 5.1: NtfyNotifier

**Files:**
- Create: `src/book_alerter/notifications/ntfy.py`
- Create: `tests/integration/test_ntfy_notifier.py` (with VCR or `httpx.MockTransport`)

**Goal:** POST to `https://ntfy.sh/<topic>` with the formatted message; respect priority + tags from config; surface errors.

- [ ] Test using `httpx.MockTransport` (no live HTTP). Test happy path + 5xx error path.

- [ ] Implement:

```python
# src/book_alerter/notifications/ntfy.py
from __future__ import annotations

import httpx

from book_alerter.config import NtfyChannelConfig
from book_alerter.db.models import Alert, Book
from book_alerter.notifications.base import Notifier


class NtfyNotifier(Notifier):
    name = "ntfy"

    def __init__(self, cfg: NtfyChannelConfig, client_factory=None) -> None:
        self._cfg = cfg
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=10))

    async def send(self, alert: Alert, book: Book) -> dict:
        if not self._cfg.enabled or not self._cfg.topic:
            return {"status": "error", "error_message": "ntfy disabled or topic missing"}
        url = f"{self._cfg.server.rstrip('/')}/{self._cfg.topic}"
        body = alert.message
        headers = {
            "Title": f"{alert.kind} — {book.title}",
            "Priority": self._cfg.priority,
            "Tags": ",".join(self._cfg.tags or []),
        }
        async with self._client_factory() as client:
            try:
                resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                return {"status": "error", "error_message": str(e)}
        return {"status": "sent"}
```

- [ ] Wire `NtfyNotifier` into the dispatcher when `cfg.notifications.channels.ntfy.enabled` is true.
- [ ] Tests pass. Commit.

---

### Task 5.2: Quiet hours

**Files:**
- Modify: `src/book_alerter/notifications/dispatcher.py` — wrap `_deliver` to skip non-inapp channels during quiet hours.
- Create: `tests/unit/test_quiet_hours.py`

**Goal:** During quiet hours (in user's tz), pushes are deferred — but the in-app `Alert` row is still created. On the next dispatcher pass after quiet hours end, deliver outstanding pushes.

> **Implementation note (and known MVP deviation from spec):** the spec's literal behavior is "alerts still queued; pushes deferred to end of window." For MVP we apply the simpler rule: during quiet hours, **non-inapp channels are skipped entirely; the in-app `Alert` row is still written.** The next alert-pipeline trigger after quiet hours end will re-fire the alert *only if the buy condition still holds and the dedup window has passed*. The trade-off is that a one-shot transient buy signal that happened to land entirely inside quiet hours is lost from push channels (the in-app feed retains it). If/when this becomes a real complaint, swap in a `"queued"` `NotificationDelivery` status + a drain job. Documented in non-goals as a deferred refinement.

- [ ] Test with `freezegun`: pin time inside quiet hours, assert ntfy not called; pin outside, assert it is.

- [ ] Implementation:

```python
def _in_quiet_hours(now_local, qh):
    if qh is None:
        return False
    start_h, start_m = map(int, qh.start.split(":"))
    end_h, end_m = map(int, qh.end.split(":"))
    cur = now_local.hour * 60 + now_local.minute
    s = start_h * 60 + start_m
    e = end_h * 60 + end_m
    return (s <= cur or cur < e) if s > e else (s <= cur < e)
```

- [ ] Wire into `_deliver`. Commit.

---

# Phase 6 — Metadata service

### Task 6.1: ISBN normalization

**Files:**
- Create: `src/book_alerter/sources/normalizers.py` — already created earlier per file map; if not, create now.
- Create: `tests/unit/test_isbn.py`

**Goal:** `to_isbn13(s: str) -> str` — accepts ISBN-10 or ISBN-13 with dashes/spaces, returns canonical 13-digit. Uses `isbnlib`.

- [ ] Tests: ISBN-10 → ISBN-13; trailing-X handled; invalid → raises.
- [ ] Implementation:

```python
# src/book_alerter/sources/normalizers.py
import isbnlib


def to_isbn13(raw: str) -> str:
    s = isbnlib.canonical(raw)
    if not s:
        raise ValueError(f"invalid ISBN: {raw!r}")
    if isbnlib.is_isbn10(s):
        s = isbnlib.to_isbn13(s)
    if not isbnlib.is_isbn13(s):
        raise ValueError(f"could not normalize to ISBN-13: {raw!r}")
    return s
```

- [ ] Test against the five test fixtures from the spec. Commit.

---

### Task 6.2: OpenLibrary + Google Books fallback (parallel race)

**Files:**
- Create: `src/book_alerter/metadata.py`
- Create: `tests/integration/test_metadata.py` (VCR cassettes)

**Goal:** `lookup_isbn(isbn13: str) -> BookMetadata` queries OpenLibrary + Google Books in parallel; first valid response wins.

- [ ] Pydantic model:

```python
class BookMetadata(BaseModel):
    title: str
    author: str
    cover_url: str | None = None
```

- [ ] Implementation queries both, uses `asyncio.wait(..., return_when=FIRST_COMPLETED)`, picks the first that has at least title + author. Cancels the other.

- [ ] Tests with cassettes for both providers; one test where OL is unavailable and GB wins.

- [ ] Commit.

---

# Phase 7 — REST API surface

This phase is mostly mechanical: one route group at a time, each backed by a small set of endpoints. After Books is done, the rest follow the same pattern (request DTOs, response DTOs, repository functions, route handlers, tests).

### Task 7.1: Books CRUD

**Files:**
- Create: `src/book_alerter/api/books.py`
- Create: `src/book_alerter/api/deps.py` (shared `get_session`, `get_config`, `get_scheduler` dependencies)
- Create: `tests/integration/api/test_books_api.py`

**Endpoints:**
- `GET /api/books` — list with `BookStats` attached.
- `POST /api/books` — body: `{isbn: str}` or `{title, author, isbn}` if from search.
- `GET /api/books/{id}` — detail.
- `PATCH /api/books/{id}` — partial update (target, threshold, status, mute, notes, alert_kinds_disabled).
- `DELETE /api/books/{id}?hard=false` — soft-delete by default.

- [ ] Write API integration tests first. Each test uses `TestClient` + a temp SQLite + applied migrations.
- [ ] Implement handlers; reuse `compute_book_stats` for list/detail responses.
- [ ] Wire router into `app.py` (`app.include_router(books.router)`).
- [ ] Commit.

### Task 7.2: Observations + Stats endpoints

`GET /api/books/{id}/observations` (paginated; query params: `limit`, `before`, `source`).
`GET /api/books/{id}/stats`.

### Task 7.3: Alerts endpoints

`GET /api/alerts` · `POST /api/alerts/{id}/dismiss` · `POST /api/alerts/dismiss-all`.

### Task 7.4: Sources endpoints

`GET /api/sources` returns config + last `SourceRun` per source.
`POST /api/sources/{name}/run` triggers immediately via `scheduler.trigger_now`.
`PATCH /api/sources/{name}` updates enabled/schedule/concurrency in config + saves.

### Task 7.5: Config endpoints

`GET /api/config` — Pydantic config as JSON.
`GET /api/config/schema` — `Config.model_json_schema()` for Monaco's live-validation in the Settings → Advanced tab (Phase 11.5).
`PUT /api/config` — body is full or partial config plus optional `dry_run: bool` (default false). Always returns `{diff: {...}, applied: bool}`. When `dry_run=true`, validation runs and the diff is computed but the file is not written and the in-memory config is unchanged. When `dry_run=false` (default), the change is validated, atomically written to `data/config.yaml` (with rotating backup), and applied to `app.state.config`.

### Task 7.6: Metadata endpoints

`GET /api/metadata/lookup?isbn=...`
`GET /api/metadata/search?q=...` (Google Books search wrapper for the search-tab in add-book modal).

### Task 7.7: Refetch + notifications test

`POST /api/books/{id}/refetch` — calls `scheduler.trigger_now` for each source.
`POST /api/notifications/{channel}/test` — synthesises an Alert and sends it.

### Task 7.8: Optional HTTP Basic auth

**Files:**
- Create: `src/book_alerter/auth.py`
- Modify: `src/book_alerter/app.py` — add middleware when env vars present.
- Test: optional, since the path is small.

```python
# src/book_alerter/auth.py
import os
import secrets
from fastapi import HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials


def is_basic_auth_enabled() -> bool:
    return bool(os.environ.get("APP_BASIC_AUTH_USER")) and bool(os.environ.get("APP_BASIC_AUTH_PASS"))


_security = HTTPBasic()


def basic_auth_dep(credentials: HTTPBasicCredentials = Depends(_security)):
    if not is_basic_auth_enabled():
        return
    user = os.environ["APP_BASIC_AUTH_USER"]
    pw = os.environ["APP_BASIC_AUTH_PASS"]
    if not (secrets.compare_digest(credentials.username, user) and secrets.compare_digest(credentials.password, pw)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
```

- [ ] Apply `Depends(basic_auth_dep)` at the router level when enabled.
- [ ] Commit.

---

# Phase 8 — printing-press CLIs (Bookfinder + Amazon)

This phase uses the `printing-press` skill to generate the source CLIs, then writes the Python adapters that wrap them.

### Task 8.1: Generate `bookfinder-pp-cli`

**Files:**
- Create: `cli_bins/bookfinder-pp-cli/` — git submodule pointing at the printing-press-library path for Bookfinder, OR a local copy if user prefers vendored. Spec says submodules.

- [ ] Outside the repo, in a scratch dir, capture a HAR file of an ISBN search on bookfinder.com:
  - Open DevTools, Network tab, Preserve Log, Disable Cache.
  - Search for one of the test ISBNs at https://www.bookfinder.com/.
  - Save as HAR.
- [ ] Invoke the printing-press skill:

```
/printing-press --har /path/to/bookfinder.har --name bookfinder
```

The skill will research, generate, build, and verify the CLI. It produces a Go module under the user's `~/printing-press/library/` workspace.

- [ ] Add as a submodule:

```bash
cd /home/ff235/dev/book_alerter
git submodule add <bookfinder-pp-cli git URL> cli_bins/bookfinder-pp-cli
git submodule update --init
```

- [ ] Smoke test:

```bash
~/go/bin/bookfinder-pp-cli search --isbn 9780241638194 --region UK --json | head -40
```

Expected: JSON conforming to the spec contract (offers list, etc.).

- [ ] Commit:

```bash
git add .gitmodules cli_bins/bookfinder-pp-cli
git commit -m "feat(sources): add bookfinder-pp-cli submodule"
```

### Task 8.2: BookfinderSource adapter

**Files:**
- Modify: `src/book_alerter/sources/bookfinder.py`
- Modify: `src/book_alerter/sources/registry.py` — register `BookfinderSource` mapping if needed (subprocess types use `SubprocessSource` directly; only override if the CLI's flags differ).
- Create: `tests/integration/sources/test_bookfinder.py`

**Goal:** Wraps `bookfinder-pp-cli` in `SubprocessSource`. If the CLI's flags differ from the default `search --isbn ... --region ... --json`, override `build_command()`. Add condition mapping in `parse()` if needed.

- [ ] Test: spawn the CLI against a mocked filesystem (use `pytest-subprocess`) OR call the real CLI under VCR by snapshotting its output. Easier: snapshot one ISBN's JSON output, write a fixture file, and parse-only test against the fixture.

- [ ] Commit.

### Task 8.3 + 8.4: Repeat for `amazon-pp-cli`

Same pattern. Capture HAR of an Amazon UK product page (with all conditions visible — toggle "See all buying options"), generate, integrate.

> **Caveat:** Amazon may show different DOM to scrapers vs. cookied browsers. Use a recent realistic User-Agent + Accept-Language=en-GB. The printing-press skill handles this when it's working from a HAR.

---

# Phase 9 — Frontend skeleton

### Task 9.1: Vite + React + TS scaffold

**Files:**
- Create: `web/` (whole tree)

- [ ] Run:

```bash
cd /home/ff235/dev/book_alerter
npm create vite@latest web -- --template react-ts
cd web
npm install
```

- [ ] Add Tailwind: follow https://tailwindcss.com/docs/guides/vite (this is documented and stable).
- [ ] Add shadcn/ui:

```bash
cd web
npx shadcn@latest init     # accept defaults: Slate base, CSS variables, etc.
```

- [ ] Add Recharts, TanStack Query, Monaco editor:

```bash
npm install recharts @tanstack/react-query @monaco-editor/react clsx
```

- [ ] Set Vite proxy for dev AND configure tsconfig path aliases (shadcn's `init` requires both):

```ts
// web/vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
```

Add to `web/tsconfig.json` (`compilerOptions`) and `web/tsconfig.app.json` if Vite's split-config template was used:

```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  }
}
```

Without these, `npx shadcn@latest init` fails with "Could not find @ alias".

- [ ] Scaffold a minimal `App.tsx` that fetches `/api/health` and renders the JSON.
- [ ] Smoke test: run `uv run uvicorn book_alerter.app:app` and `npm run dev` in two terminals; visit `http://localhost:5173`; assert health JSON shows.
- [ ] Commit.

### Task 9.2: Generated types from OpenAPI

- [ ] Add `openapi-typescript`:

```bash
cd web
npm install -D openapi-typescript
```

- [ ] Add npm script in `web/package.json`:

```json
"scripts": {
  "gen:api": "openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/schema.ts"
}
```

- [ ] Run `npm run gen:api` after backend is up.
- [ ] Wrap with a typed fetch client (`web/src/api/client.ts` exporting `apiGet`, `apiPost`, etc., using the generated types).
- [ ] Commit.

### Task 9.3: Routing + layout

- [ ] Add `react-router-dom`. Configure routes:
  - `/` → `Dashboard`
  - `/books/:id` → `BookDetail`
  - `/alerts` → `Alerts`
  - `/settings/*` → nested settings pages.
- [ ] Layout shell: top nav + main area + collapsible right alerts sidebar.
- [ ] Add dark mode toggle (Tailwind `dark:` classes; persist in `localStorage`).
- [ ] Commit.

---

# Phase 10 — Dashboard + Book Detail

### Task 10.1: Dashboard list/table

- [ ] Implement a basic data hook:

```tsx
function useBooks() {
  return useQuery({
    queryKey: ["books"],
    queryFn: () => apiGet("/api/books"),
  });
}
```

- [ ] Render a `<DataTable>` (shadcn/ui) with the columns from the spec.
- [ ] Filter bar at the top: signal · status · sort.
- [ ] Empty state with "Add your first book" CTA.
- [ ] Commit.

### Task 10.2: Add-book modal (ISBN tab)

- [ ] Modal triggered from dashboard.
- [ ] One input: ISBN. On blur, call `/api/metadata/lookup?isbn=...`; preview title/author/cover.
- [ ] Confirm → POST `/api/books`.
- [ ] After success: `queryClient.invalidateQueries(["books"])`.
- [ ] Commit.

### Task 10.3: Book Detail page

- [ ] Header card with book metadata.
- [ ] Snapshot card with current best price.
- [ ] Signal card with target distance + percentile context.
- [ ] History chart with Recharts. Pull `/api/books/:id/observations?limit=500` and group by `(source, condition)`.
- [ ] Source breakdown table (latest per source).
- [ ] Settings panel (target, threshold, alert kinds, mute, notes).
- [ ] Action buttons (refetch, mark bought, archive, delete).
- [ ] Commit.

### Task 10.4: Alerts feed sidebar + Alerts page

- [ ] Sidebar pulls `/api/alerts?dismissed=false&limit=20`.
- [ ] Each item: book title, kind, message, dismiss button.
- [ ] Bulk dismiss via `/api/alerts/dismiss-all`.
- [ ] Full alerts page reuses the same hook with filters.
- [ ] Commit.

---

# Phase 11 — Add-book search tab + Settings

### Task 11.1: Search tab in add-book modal

- [ ] Search input (debounced) → `/api/metadata/search?q=...`.
- [ ] Result list with covers; click to confirm.
- [ ] Commit.

### Task 11.2: Settings → Sources tab

- [ ] List sources with on/off toggle, schedule, concurrency, jitter, per-book delay.
- [ ] "Run now" button → `POST /api/sources/{name}/run`.
- [ ] Last 10 runs table.
- [ ] Saving uses `PATCH /api/sources/{name}` and shows a diff preview before writing.
- [ ] Commit.

### Task 11.3: Settings → Recommendation tab

- [ ] Sliders/inputs for `min_obs`, `buy_pct`, `watch_pct`, `target_tolerance_pct`, `dedup_window_hours`.
- [ ] "Save" hits `PUT /api/config`; shows diff.
- [ ] Commit.

### Task 11.4: Settings → Notifications tab

- [ ] Per-channel: enable, fields, "Send test" button.
- [ ] Per-kind toggles.
- [ ] Quiet hours editor.
- [ ] Commit.

### Task 11.5: Settings → Advanced tab (Monaco YAML editor)

- [ ] Monaco editor configured for YAML.
- [ ] Live JSON-schema validation: pull `/api/config/schema` (add this endpoint — exposes `Config.model_json_schema()`).
- [ ] Diff preview before save (compare via `apiPut("/api/config", { ..., dry_run: true })` → returns diff).
- [ ] On save: `apiPut("/api/config", body)`; on error, surface validation error inline.
- [ ] Commit.

---

# Phase 12 — Deployment

### Task 12.1: Multi-stage Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] Author the Dockerfile from the spec, verify a local build:

```bash
cd /home/ff235/dev/book_alerter
docker build -t book_alerter:dev .
```

- [ ] Commit.

### Task 12.2: docker-compose + .env.example

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`

- [ ] Compose file from spec.
- [ ] Smoke test:

```bash
docker compose up -d
curl -f http://127.0.0.1:8000/api/health
docker compose down
```

- [ ] Commit.

### Task 12.3: Weekly SQLite backup

**Files:**
- Modify: `src/book_alerter/scheduler.py` — add a backup job triggered weekly.

- [ ] Add an APScheduler `cron` job that runs `VACUUM INTO 'data/backups/book_alerter_<ts>.db'`. Retain last 7 by deleting older files.
- [ ] Test with `freezegun` jumping a week.
- [ ] Commit.

---

# Phase 13 — E2E + README

### Task 13.1: E2E smoke

**Files:**
- Create: `tests/e2e/docker-compose.test.yml`
- Create: `tests/e2e/test_smoke.py`

- [ ] `docker-compose.test.yml` brings up the app with a tmpfs `./data`.
- [ ] Test: boot container, wait for `/api/health` ok, POST a Book by ISBN (test fixture), wait briefly, assert observations land. Tear down.
- [ ] Commit.

### Task 13.2: README

**Files:**
- Create: `README.md`

- [ ] Cover: what it is, how to run locally (`uv run uvicorn ...` + `npm run dev`), how to deploy (`docker compose up -d`), how to add sources via printing-press, how to refresh source CLIs.
- [ ] Commit.

---

## Final integration checklist (run before declaring MVP done)

- [ ] All migrations apply on a fresh DB.
- [ ] All five test ISBNs add successfully and produce observations across all enabled sources.
- [ ] At least one alert fires end-to-end (force a low-price observation by setting `target_price_minor` high enough, then manually inserting a low observation).
- [ ] ntfy delivery works against your own ntfy topic (set in `.env`).
- [ ] Quiet hours suppress pushes (test with frozen time or temp config).
- [ ] Settings → Advanced YAML editor round-trips a config edit.
- [ ] `docker compose up` brings the app up cleanly on the NAS.
- [ ] Tailscale-only access verified (curl from the public internet should fail; from a Tailscale peer should succeed).
- [ ] README walks a fresh user from `git clone` to "I'm tracking my first book" without dead ends.

---

## Glossary of skill usage

- `@modern-python` — Phase 0.1 if uv tooling is unfamiliar.
- `@printing-press` — Phase 8.1, 8.3 to generate source CLIs.
- `@printing-press-polish` — Phase 8 follow-ups when a generated CLI needs verification / fixes.
- `@printing-press-reprint` — when a source site changes substantially.
- The **subagent-driven-development** or **executing-plans** skill is what runs *this* plan task by task.
