# Book Alerter — Implementation Changelog

Append-only log of completed implementation tasks. Newest entries on top within a date.

Each entry: plan task ID(s), one-line summary, commit SHA(s), notable deviations from plan (if any).

---

## 2026-05-09

### Pre-implementation (specification + planning)

- `2eafd01` — Spec drafted (846 lines)
- `22521e4` — Spec polished per reviewer's advisory recommendations
- `7ecba3d` — User-supplied test ISBN fixtures added to spec
- `84ca651` — Spec narrowed: ntfy-only push; Tailscale auth posture
- `6cc7189` — Implementation plan drafted (13 phases, ~80 tasks, ~2840 lines)
- `a5d030c` — Plan fixed per reviewer: BookSignalState, working backoff, dry_run config, tsconfig alias, view tie-break, BookStats.percentile_at
- `d953741` — RESUME + CHANGELOG scaffolds added

### Phase 0 — Foundation

- **Task 0.1** — `defc8c1` Initialize uv project + base dependencies. `uv sync` succeeds; `import book_alerter` works.
  - **Incident & fix**: `0b93bbc` Subagent encountered a Python-version conflict because `book_alerter` is a member of an existing uv workspace at `/home/ff235/dev/` whose other members are pinned to `>=3.12,<3.13` (likely for PyTorch XPU wheel compatibility). Subagent attempted to resolve by relaxing the siblings' constraints — out of scope. Reverted those changes; instead lowered `book_alerter` to `>=3.12,<3.13` (none of our deps need 3.13). Spec + plan updated. Future implementer prompts now hard-constrain edits to `/home/ff235/dev/book_alerter/`.
- **Task 0.2** — `7a2ff49` `/api/health` endpoint via FastAPI factory `create_app()`. Test `test_health_returns_ok` passes; `curl /api/health → {"status":"ok"}`. Files: `src/book_alerter/{app.py,api/{__init__.py,health.py}}`, `tests/{__init__.py,integration/{__init__.py,api/{__init__.py,test_health_api.py}}}`. No deviations.
- **Task 0.3** — `35ccd14` structlog JSON logging configured. `configure_logging()` called in `create_app()`. 2/2 tests passing. Pragmatic additions beyond plan: handler cleanup + `structlog.reset_defaults()` for test-reconfiguration safety — acceptable.
- **Task 0.4** — `782e1fe` Pydantic Config schema (Recommendation/Notifications/Source/etc.); YAML load/save with atomic write; `${ENV_VAR}` substitution. 5/5 tests passing. No deviations.
- **Task 0.5** — `d49abe9` SQLModel engine + `session_scope()` context manager; reads `BOOK_ALERTER_DATABASE_URL` env (default `sqlite:///./data/book_alerter.db`); commit-on-exit / rollback-on-exception / close-always. 6/6 tests passing.
- **Task 0.6** — `bb66612` Alembic initialised. `alembic.ini` at repo root; `env.py` imports `get_database_url()` and uses `SQLModel.metadata`; `db/models.py` placeholder for Phase 1 tables. `alembic current` clean. 6/6 tests still passing.
- **Task 0.7** — `b588023` App lifespan loads config from `data/config.yaml` (path override via `BOOK_ALERTER_CONFIG_PATH`), stashes on `app.state.config`. `/api/health` surfaces `config_version`. 7/7 tests passing.

**Phase 0 complete.** Foundation is runnable: `uv run uvicorn book_alerter.app:app` boots, `GET /api/health` returns `{"status":"ok","config_version":<n>}`, structured JSON logs, SQLite session manager, Alembic ready for Phase 1 migrations.

- **Simplify pass** — `40d183d` Two findings applied: `get_engine` now uses explicit `None` check instead of truthiness (empty string would have silently fallen through); tidied a task-referencing comment in `migrations/env.py`. Other reviewer findings deferred (pydantic-settings refactor, Literal log level) — over-engineering for the current scope.
