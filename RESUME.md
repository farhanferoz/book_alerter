# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** Phase 0 in progress — Task 0.1 complete. Next: Task 0.2.
**Branch:** `master` (no worktree)
**Last update:** 2026-05-09 (after Task 0.1 + uv-workspace incident fix)

## Where we are

Phase 0, Task 0.1 done — uv project initialised, deps installed, `import book_alerter` works. Hit a real incident: `book_alerter` is a member of the user's existing uv workspace at `/home/ff235/dev/`, whose siblings are pinned `<3.13`. Plan originally specified Python 3.13; the implementer subagent attempted to relax the siblings' constraints (out-of-scope edits), which I reverted. Real fix: dropped `book_alerter` to `>=3.12,<3.13` to match the workspace. Spec, plan, and pyproject all updated and committed.

## Next action

Dispatch implementer for **Task 0.2: Health endpoint with FastAPI app factory**. Use the tightened implementer prompt below.

## Implementer prompt hardening (apply to ALL future task dispatches)

Add this to every implementer prompt: **"All file edits MUST be within `/home/ff235/dev/book_alerter/`. If `uv` or any tool reports a workspace-level conflict, STOP and report BLOCKED — do NOT modify files in sibling projects (`/home/ff235/dev/{suroor_ai,podcast_ai,audio_commons,MLResearch,...}`) or the workspace root (`/home/ff235/dev/pyproject.toml`)."**

## Open decisions / unresolved

_None._ Workspace conflict is resolved.

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
