# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** Phase 5 IN PROGRESS — Task 5.1 (NtfyNotifier) shipped. 22 tasks across Phases 0–5.1, 77 tests passing. End-to-end alerting works including ntfy push when `notifications.channels.ntfy.enabled`. Next: Task 5.2 — quiet hours gate.
**Branch:** `master` (no worktree)
**Last update:** 2026-05-14, Task 5.1 committed at `2f0f6a4`

## Where we are

Phases 0–4 complete:
- **Foundation (0)** through **Scheduler (3)** as before.
- **Stats + alerts (4)**: `BookStats` + `compute_book_stats` in `src/book_alerter/stats.py` (uses the `book_stats` view + percentile_at via linear interp on sorted_totals). `compute_signal` returns BUY / WATCH / WAIT / TARGET_HIT / INSUFFICIENT_DATA. `detect_alert_kinds` in `src/book_alerter/alerts.py` fires `target_hit` / `percentile_cross` / `new_low` on transitions. `AlertPipeline.run(book_ids)` in `src/book_alerter/notifications/dispatcher.py` does the full pipeline (read prior `BookSignalState` → detect → global/per-book/mute/dedup filters → write Alert + NotificationDelivery → persist new BookSignalState). `InAppNotifier` is the only notifier so far. `app.py` lifespan wires `pipeline.run` into `Scheduler`'s `alert_pipeline`.

## Verify on return

```bash
cd /home/ff235/dev/book_alerter && uv run pytest -v
# expected: 77 passed
git log --oneline d953741..HEAD
# expected: 40+ commits ending at the most recent Task 5.1 commit
uv run alembic current
# expected: 0004_book_stats_view (head)
```

## Next action

Dispatch implementer for **Phase 5, Task 5.2: Quiet hours** (plan line 2412). Wraps `_deliver` to skip non-inapp channels during the user's quiet window; tested with `freezegun`. MVP deviation from spec is documented in the plan: pushes are SKIPPED inside quiet hours (not queued); the in-app `Alert` row is still written; next eval after quiet hours re-fires only if the buy condition still holds and the dedup window has passed.

## Implementer prompt hardening (must apply to EVERY future task dispatch)

> All file edits MUST be within `/home/ff235/dev/book_alerter/`. If any tool reports a workspace-level conflict, STOP and report BLOCKED — do NOT modify files in sibling projects (`/home/ff235/dev/{suroor_ai,podcast_ai,audio_commons,MLResearch,...}`) or the workspace root (`/home/ff235/dev/pyproject.toml`).

## Open decisions / unresolved

_None._ All blockers from Phase 1 resolved.

## Process notes for next session

- **After every migration task, run `uv run alembic upgrade head`** so the dev DB at `data/book_alerter.db` stays at head. Otherwise the next `alembic revision --autogenerate` fails with "Target database is not up to date." (Discovered during Tasks 1.3 → 1.4 — see CHANGELOG.)
- **`Literal[...]` SQLModel fields** must be declared with `sa_column=Column(String, nullable=False)` because SQLModel 0.0.22's type inference calls `issubclass(Literal, Enum)` → `TypeError`. See `Book.format`, `Book.status`, `PriceObservation.condition`, `SourceRun.status`, `Alert.kind`, `NotificationDelivery.status` for the established pattern.
- **`Condition` Literal lives in `book_alerter.db.models`** and is re-exported by `book_alerter.sources.base`. New sources should import it from `sources.base` (semantic origin) but the canonical definition is in `db.models`.
- **`tests/conftest.py` provides `transient_book(isbn, *, target_price_minor, percentile_threshold)`** + **`transient_stats(*, observation_count, current_best_total_minor, p50_total_minor, sorted_totals)`** for unpersisted construction. `tests/integration/conftest.py` provides `sqlite_engine` + `engine_with_view` (sqlite_engine with `book_stats` view installed) + `make_book` (persisted) + `wob_vcr(record_mode)` (VCR factory) + `WOB_CARRIED_ISBN` / `WOB_MAYBE_NOT_CARRIED_ISBN` / `WOB_CASSETTE_DIR` constants. Reach for these before writing local helpers.
- **`book_stats` view DDL** lives in `src/book_alerter/db/views.py` as `BOOK_STATS_VIEW_SQL` + `DROP_BOOK_STATS_VIEW_SQL`. Migration 0004 imports from there; integration tests get the view via the `engine_with_view` fixture in `tests/integration/conftest.py`. **Don't redefine the DDL anywhere else.**
- **`AlertKind` Literal lives in `src/book_alerter/config.py`** and is re-exported by `book_alerter.alerts`. `NotificationsConfig.alert_kinds_enabled` and `detect_alert_kinds`'s return tuple both use it; if you add a new alert kind, update only `config.py`.
- **`detect_alert_kinds` returns `(kinds, cur_signal)`** as of the Phase 4 simplify pass — the dispatcher reuses `cur_signal` when persisting `BookSignalState`. Don't recompute `compute_signal` after calling `detect_alert_kinds`.

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
