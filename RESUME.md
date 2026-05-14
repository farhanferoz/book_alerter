# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** Phase 7 IN PROGRESS. Task 7.1 (Books CRUD endpoints) live: `GET/POST /api/books` and `GET/PATCH/DELETE /api/books/{id}` with embedded `BookStats`, ISBN-13 normalization, 409-on-duplicate, soft-delete-by-default. New `src/book_alerter/api/deps.py` provides `get_session` / `get_config` / `get_scheduler` for shared FastAPI dependencies. 125 tests passing. Phase 6 complete prior (Tasks 6.1 + 6.2 + simplify pass).
**Branch:** `master` (no worktree)
**Last update:** 2026-05-14, Task 7.1 committed at `15e6dbf`

## Where we are

Phases 0–4 complete:
- **Foundation (0)** through **Scheduler (3)** as before.
- **Stats + alerts (4)**: `BookStats` + `compute_book_stats` in `src/book_alerter/stats.py` (uses the `book_stats` view + percentile_at via linear interp on sorted_totals). `compute_signal` returns BUY / WATCH / WAIT / TARGET_HIT / INSUFFICIENT_DATA. `detect_alert_kinds` in `src/book_alerter/alerts.py` fires `target_hit` / `percentile_cross` / `new_low` on transitions. `AlertPipeline.run(book_ids)` in `src/book_alerter/notifications/dispatcher.py` does the full pipeline (read prior `BookSignalState` → detect → global/per-book/mute/dedup filters → write Alert + NotificationDelivery → persist new BookSignalState). `InAppNotifier` is the only notifier so far. `app.py` lifespan wires `pipeline.run` into `Scheduler`'s `alert_pipeline`.

## Verify on return

```bash
cd /home/ff235/dev/book_alerter && uv run pytest -q
# expected: 125 passed
git log --oneline d953741..HEAD | wc -l
# expected: 61
uv run alembic current
# expected: 0004_book_stats_view (head)
```

## Next action

Dispatch **Phase 7 Task 7.2 (Observations + Stats endpoints)**, plan line 2523.

## Implementer prompt hardening (must apply to EVERY future task dispatch)

> All file edits MUST be within `/home/ff235/dev/book_alerter/`. If any tool reports a workspace-level conflict, STOP and report BLOCKED — do NOT modify files in sibling projects (`/home/ff235/dev/{suroor_ai,podcast_ai,audio_commons,MLResearch,...}`) or the workspace root (`/home/ff235/dev/pyproject.toml`).

## Open decisions / unresolved

_None._ All blockers from Phase 1 resolved.

## Process notes for next session

- **After every migration task, run `uv run alembic upgrade head`** so the dev DB at `data/book_alerter.db` stays at head. Otherwise the next `alembic revision --autogenerate` fails with "Target database is not up to date." (Discovered during Tasks 1.3 → 1.4 — see CHANGELOG.)
- **`Literal[...]` SQLModel fields** must be declared with `sa_column=Column(String, nullable=False)` because SQLModel 0.0.22's type inference calls `issubclass(Literal, Enum)` → `TypeError`. See `Book.format`, `Book.status`, `PriceObservation.condition`, `SourceRun.status`, `Alert.kind`, `NotificationDelivery.status` for the established pattern.
- **`Condition` Literal lives in `book_alerter.db.models`** and is re-exported by `book_alerter.sources.base`. New sources should import it from `sources.base` (semantic origin) but the canonical definition is in `db.models`.
- **`tests/conftest.py` provides `transient_book(isbn, *, title, author, target_price_minor, percentile_threshold)`** + **`transient_stats(*, observation_count, current_best_total_minor, p50_total_minor, sorted_totals)`** for unpersisted construction. `tests/integration/conftest.py` provides `sqlite_engine` + `engine_with_view` (sqlite_engine with `book_stats` view installed) + `make_book` (persisted) + `wob_vcr(record_mode)` + `metadata_vcr(record_mode)` (VCR factories) + `WOB_CARRIED_ISBN` / `WOB_MAYBE_NOT_CARRIED_ISBN` / `WOB_CASSETTE_DIR` / `METADATA_CASSETTE_DIR` constants. Reach for these before writing local helpers.
- **HTTP API cassette convention**: per-source cassettes live under `tests/integration/cassettes/<source>/` (e.g. `cassettes/metadata/` for OL+GB). The WoB cassettes still live under `tests/integration/sources/cassettes/` for historical reasons — new HTTP integrations should follow the `cassettes/<name>/` layout. Use `record_mode="none"` for CI replay and include `("method","scheme","host","port","path","query")` in `match_on` when query strings carry the request semantics (e.g. ISBN lookups).
- **For new HTTP integrations**, mirror `notifications/ntfy.py` and `sources/wob.py`: per-call `httpx.AsyncClient`, short timeout (5–10s), no retries (rely on race/orchestration). For OL+GB-style fan-outs, use a single shared `AsyncClient` inside the orchestrator and let helpers take it as an argument — see `metadata.lookup_isbn`.
- **New VCR cassettes**: use the shared `_vcr_factory(cassette_dir, default_record_mode, *, match_query)` helper in `tests/integration/conftest.py` rather than copy-pasting the `vcr.VCR(...)` setup. Expose as a `@pytest.fixture` named `<source>_vcr` and add a `<NAME>_CASSETTE_DIR` constant alongside the existing WoB/metadata pair.
- **Untrusted JSON extraction (e.g. third-party APIs)** must `isinstance(..., dict)`-guard before calling `.get(...)` on values that the *schema* documents as objects but real responses sometimes deliver as strings/None. See `_fetch_openlibrary` in `metadata.py` for the pattern — bare-string `cover` and non-dict `authors[0]` are documented historical OL quirks; the guards let the provider degrade to `None` (race waits for the other) rather than `AttributeError`.
- **`Notifier` ABC has `bypasses_quiet_hours: bool = False`** (Phase 5 simplify). New push channels leave it False; the in-app channel sets it True. The dispatcher uses this flag rather than checking `n.name == "inapp"` — keep new channels consistent with this pattern.
- **`Notifier.send` returns `NotificationResult` (TypedDict)** from `book_alerter.notifications.base`: `{"status": Literal["sent","error"], "error_message"?: str}`. Don't return plain `dict` — narrow the type so the dispatcher's `r["status"]` access is type-checked.
- **Don't `git add -A`** during simplify/follow-up commits — `.claudesignore` is auto-generated by ccage and intentionally untracked. Add explicit paths or stage selectively.
- **`book_stats` view DDL** lives in `src/book_alerter/db/views.py` as `BOOK_STATS_VIEW_SQL` + `DROP_BOOK_STATS_VIEW_SQL`. Migration 0004 imports from there; integration tests get the view via the `engine_with_view` fixture in `tests/integration/conftest.py`. **Don't redefine the DDL anywhere else.**
- **`AlertKind` Literal lives in `src/book_alerter/config.py`** and is re-exported by `book_alerter.alerts`. `NotificationsConfig.alert_kinds_enabled` and `detect_alert_kinds`'s return tuple both use it; if you add a new alert kind, update only `config.py`.
- **`detect_alert_kinds` returns `(kinds, cur_signal)`** as of the Phase 4 simplify pass — the dispatcher reuses `cur_signal` when persisting `BookSignalState`. Don't recompute `compute_signal` after calling `detect_alert_kinds`.
- **API test pattern (Task 7.1)**: build a router-only `FastAPI()` test app rather than invoking `create_app()` + its lifespan. The `api_client` fixture in `tests/integration/api/conftest.py` is the template — `engine_with_view` for the DB, a default `Config.load(<missing-path>)` for `app.state.config`, `app.include_router(<module>.router)`. This avoids scheduler/notifier startup, gives full control over the engine (`book_stats` view installed), and keeps tests under a millisecond. Reuse + extend this fixture for Task 7.2+ (observations, alerts, settings endpoints); add more routers to the fixture as endpoints land. Production app uses `create_app()` + lifespan as usual — health test still covers that path.
- **FastAPI dependency style (Task 7.1)**: use `SessionDep = Annotated[Session, Depends(get_session)]` (module-level) and write handlers as `def foo(... , session: SessionDep, ...)`. Avoids ruff's `B008` (`Depends(...)` in default argument) while keeping FastAPI's auto-DI working. Non-defaulted `SessionDep` must precede defaulted query params in the signature — reorder if needed.
- **Pydantic mirrors for dataclass serialization**: when a handler needs to return a `@dataclass` from `stats.py` (or elsewhere), define a small Pydantic `BaseModel` mirror with a `.from_dataclass()` classmethod and use that in `response_model`. Cleaner OpenAPI schema than `arbitrary_types_allowed=True`, and lets you exclude internal fields (e.g. `sorted_totals` from `BookStats`).

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
