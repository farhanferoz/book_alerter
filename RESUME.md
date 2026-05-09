# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** Phase 0 COMPLETE. 7 tasks done, 7 tests passing, simplify pass applied. Next phase: Phase 1 — Data model (5 tables + book_stats view).
**Branch:** `master` (no worktree)
**Last update:** 2026-05-09, end of autonomous session

## Where we are

Phase 0 done. Foundation runs end-to-end: `uv run uvicorn book_alerter.app:app` boots, `GET /api/health` returns `{"status":"ok","config_version":<n>}`, structlog JSON logging configured, SQLite engine + session_scope, Alembic ready for migrations. Code reviewed via the simplify skill — two minor fixes applied (guard `get_engine` against empty url; tidy comment).

## Verify on return

```bash
cd /home/ff235/dev/book_alerter && uv run pytest -v
# expected: 7 passed
git log --oneline d953741..HEAD
# expected: 11 commits ending at 40d183d
```

## Next action

Dispatch implementer for **Phase 1, Task 1.1: Book table + Alembic migration**. The plan covers Tasks 1.1 → 1.4 (Book; PriceObservation w/ self-FK; SourceRun + Alert + NotificationDelivery + BookSignalState; book_stats view). After Phase 1 the foundation will have all DB tables and the read-only stats view.

## Implementer prompt hardening (must apply to EVERY future task dispatch)

> All file edits MUST be within `/home/ff235/dev/book_alerter/`. If any tool reports a workspace-level conflict, STOP and report BLOCKED — do NOT modify files in sibling projects (`/home/ff235/dev/{suroor_ai,podcast_ai,audio_commons,MLResearch,...}`) or the workspace root (`/home/ff235/dev/pyproject.toml`).

## Open decisions / unresolved

_None._ All blockers from this session resolved.

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
