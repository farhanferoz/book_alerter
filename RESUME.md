# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** Pre-Phase-0 (scaffolding ready, no implementation tasks committed yet)
**Branch:** `master` (no worktree)
**Last update:** 2026-05-09

## Where we are

Brainstorming + planning complete. Spec and implementation plan written, reviewed, polished, committed. About to begin **Phase 0 — Foundation** task by task using the subagent-driven-development skill.

## Next action

Dispatch a subagent for **Task 0.1: Initialize uv project and base dependencies** (see plan §Phase 0). The subagent should follow the task's TDD-style steps verbatim and commit at the end.

After review, advance to Task 0.2, then 0.3, ..., 0.7. Each task = one subagent dispatch.

## Open decisions / unresolved

_None at the moment._ If a subagent hits something that requires my judgment, I stop and document it here.

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
