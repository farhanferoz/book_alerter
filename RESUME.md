# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** Phase 3 COMPLETE + simplify pass. 17 tasks across Phases 0–3, 33 tests passing. Scheduler produces persisted observations end-to-end (cassette → fetch → DB). Alert pipeline is exception-isolated. Next phase: Phase 4 — stats, signal, alerts.
**Branch:** `master` (no worktree)
**Last update:** 2026-05-14, end of autonomous session

## Where we are

Phases 0–3 complete:
- **Foundation (0)**: app boots; `/api/health` returns `{status, config_version}`; structlog JSON logging; SQLite engine + `session_scope`.
- **Data model (1)**: 5 tables + `book_stats` view migrated. Migration chain at `0004_book_stats_view (head)`.
- **Sources (2)**: `Source` ABC, `ObservationCandidate`, `SourceError` in `sources/base.py`. `SubprocessSource` + `InlineSource`. `WobInlineSource` parses Shopify `var meta` JSON. `build_sources(cfg) -> dict[str, Source]` registry.
- **Scheduler (3)**: `Scheduler` in `src/book_alerter/scheduler.py` wraps APScheduler `AsyncIOScheduler`. Per-source cron jobs; `Semaphore(concurrency)` + per-book jitter; writes `SourceRun` audit rows + persists `PriceObservation`; exponential backoff capped at 24 h. `app.py` lifespan starts/stops it. `alert_pipeline` is a no-op placeholder (Phase 4 wires the real one). E2E smoke: cassette → fetch → 4 PriceObservation rows land in DB.

## Verify on return

```bash
cd /home/ff235/dev/book_alerter && uv run pytest -v
# expected: 33 passed
git log --oneline d953741..HEAD
# expected: 30+ commits ending at the most recent docs commit
uv run alembic current
# expected: 0004_book_stats_view (head)
```

## Next action

Dispatch implementer for **Phase 4, Task 4.1: `compute_book_stats` helper** (plan line 1911). Phase 4 covers Tasks 4.1 → 4.4: stats helper (percentile p25/p50/p75 in Python, since SQLite lacks `percentile_cont`); `compute_signal` (target_hit / new_low / percentile_cross / INSUFFICIENT_DATA); alert detection on transitions; full alert pipeline that writes `Alert` rows, dedups, delivers in-app. After Phase 4 the scheduler's no-op `alert_pipeline` gets replaced with the real thing.

## Implementer prompt hardening (must apply to EVERY future task dispatch)

> All file edits MUST be within `/home/ff235/dev/book_alerter/`. If any tool reports a workspace-level conflict, STOP and report BLOCKED — do NOT modify files in sibling projects (`/home/ff235/dev/{suroor_ai,podcast_ai,audio_commons,MLResearch,...}`) or the workspace root (`/home/ff235/dev/pyproject.toml`).

## Open decisions / unresolved

_None._ All blockers from Phase 1 resolved.

## Process notes for next session

- **After every migration task, run `uv run alembic upgrade head`** so the dev DB at `data/book_alerter.db` stays at head. Otherwise the next `alembic revision --autogenerate` fails with "Target database is not up to date." (Discovered during Tasks 1.3 → 1.4 — see CHANGELOG.)
- **`Literal[...]` SQLModel fields** must be declared with `sa_column=Column(String, nullable=False)` because SQLModel 0.0.22's type inference calls `issubclass(Literal, Enum)` → `TypeError`. See `Book.format`, `Book.status`, `PriceObservation.condition`, `SourceRun.status`, `Alert.kind`, `NotificationDelivery.status` for the established pattern.
- **`Condition` Literal lives in `book_alerter.db.models`** and is re-exported by `book_alerter.sources.base`. New sources should import it from `sources.base` (semantic origin) but the canonical definition is in `db.models`.
- **`tests/conftest.py` provides `transient_book(isbn="...")`** for unpersisted `Book` construction. `tests/integration/conftest.py` provides `sqlite_engine` + `make_book` (persisted) + `wob_vcr(record_mode)` (VCR factory) + `WOB_CARRIED_ISBN` / `WOB_MAYBE_NOT_CARRIED_ISBN` / `WOB_CASSETTE_DIR` constants. Reach for these before writing local helpers.

## Incidents this session (for reference, not action)

- **uv workspace Python conflict** (Task 0.1): subagent edited sibling pyproject.tomls; reverted; instead dropped `book_alerter` to `>=3.12,<3.13`. See `docs/CHANGELOG.md` for detail.

## Working agreements (do NOT re-decide)

- Tech stack: Python 3.13 / uv / FastAPI / SQLModel / Alembic / APScheduler / structlog · React 18 / Vite / TS / Tailwind / shadcn/ui / Recharts · Go (printing-press CLIs)
- Deployment: Docker (multi-stage) on NAS; Tailscale-only access; HTTP Basic optional but off by default
- Sources at MVP: Bookfinder (printing-press), WoB UK (inline Python first), Amazon UK (printing-press)
- Push at MVP: **ntfy only**; Telegram + Pushover deferred (slots reserved)
- Region: UK only at MVP; schema pluggable
- Identity: ISBN-pinned; `isbnlib` normalises ISBN-10 → ISBN-13
- Recommendation: hybrid (percentile default + per-book target override); `INSUFFICIENT_DATA` cold-start
- Stats: `book_stats` SQL view + `compute_book_stats()` Python helper (no materialised stats table)
- Money: integer minor units (pence); never floats
- Time: UTC in DB; render local in UI

## Key files

- `docs/superpowers/specs/2026-05-09-book-alerter-design.md` — design spec (authoritative for behaviour)
- `docs/superpowers/plans/2026-05-09-book-alerter-implementation.md` — task-by-task implementation plan
- `docs/CHANGELOG.md` — append-only log of completed implementation tasks (commits, deviations)
- `RESUME.md` — this file (cursor + open decisions only; everything else is in CHANGELOG)

## Conventions for autonomous work

- One subagent dispatch = one plan task. After commit, update CHANGELOG → update RESUME.
- Commits are made by the subagent at task end (per the plan).
- Stop on real ambiguity. Don't ad-lib design decisions; defer to spec + plan; if conflict, surface here.
- If a subagent fails twice on the same task, stop and document.
- Phase boundaries are natural checkpoints — feel free to leave a clean note in RESUME at each.
