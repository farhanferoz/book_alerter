# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** Phase 1 COMPLETE + simplify pass applied. 11 tasks done across Phases 0–1, 14 tests passing, 5 tables + `book_stats` view migrated, shared test fixtures (`sqlite_engine`, `make_book`) extracted. Next phase: Phase 2 — Source plugin layer + WoB inline scraper.
**Branch:** `master` (no worktree)
**Last update:** 2026-05-14, end of autonomous session

## Where we are

Phases 0–1 complete. Foundation + data model live:
- `uv run uvicorn book_alerter.app:app` boots; `GET /api/health` returns `{"status":"ok","config_version":<n>}`; structlog JSON logging configured.
- SQLite engine + `session_scope` available; Alembic chain has 4 migrations applied cleanly from scratch:
  `b0b34b4456fa (book) → 30c98243f802 (priceobservation) → 242d0f24dcef (sourcerun/alert/notificationdelivery/booksignalstate) → 0004_book_stats_view`.
- `book_stats` read-only view exposes per-book current best price + all-time min/max + obs count; percentiles deferred to Python (Phase 4).
- Tooling: `migrations/env.py` now has a `render_item` hook that auto-injects `import sqlmodel` into autogen migrations.

## Verify on return

```bash
cd /home/ff235/dev/book_alerter && uv run pytest -v
# expected: 14 passed
git log --oneline d953741..HEAD
# expected: 18 commits ending at bfdb144 (simplify pass)
uv run alembic current
# expected: 0004_book_stats_view (head)
```

## Next action

Dispatch implementer for **Phase 2, Task 2.1: Source ABC + ObservationCandidate** (plan line 1229). Phase 2 covers Tasks 2.1 → 2.4: the `Source` ABC and `ObservationCandidate` pydantic model; `SubprocessSource` + `InlineSource` bases; the WoB inline scraper with a vcrpy cassette; and a source registry that instantiates configured sources. After Phase 2, end-to-end fetch from World of Books UK will land observations in the DB via a hermetic test.

## Implementer prompt hardening (must apply to EVERY future task dispatch)

> All file edits MUST be within `/home/ff235/dev/book_alerter/`. If any tool reports a workspace-level conflict, STOP and report BLOCKED — do NOT modify files in sibling projects (`/home/ff235/dev/{suroor_ai,podcast_ai,audio_commons,MLResearch,...}`) or the workspace root (`/home/ff235/dev/pyproject.toml`).

## Open decisions / unresolved

_None._ All blockers from Phase 1 resolved.

## Process notes for next session

- **After every migration task, run `uv run alembic upgrade head`** so the dev DB at `data/book_alerter.db` stays at head. Otherwise the next `alembic revision --autogenerate` fails with "Target database is not up to date." (Discovered during Tasks 1.3 → 1.4 — see CHANGELOG.)
- **`Literal[...]` SQLModel fields** must be declared with `sa_column=Column(String, nullable=False)` because SQLModel 0.0.22's type inference calls `issubclass(Literal, Enum)` → `TypeError`. See `Book.format`, `Book.status`, `PriceObservation.condition`, `SourceRun.status`, `Alert.kind`, `NotificationDelivery.status` for the established pattern.

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
