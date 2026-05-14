# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** Phase 7 IN PROGRESS. Tasks 7.1 + 7.2 + 7.3 + 7.4 + 7.5 + 7.6 live. Task 7.6 adds the metadata router (`src/book_alerter/api/metadata.py`, prefix `/api/metadata`): `GET /api/metadata/lookup?isbn=...` normalizes via `to_isbn13` (422 on invalid) then races OL+GB through `lookup_isbn` (**404** when both empty). `GET /api/metadata/search?q=...&limit=...` is a Google Books free-text wrapper (`limit` capped at 40 via `Query(ge=1, le=40)`; 422 out-of-range; empty `q` → 422) returning `list[BookMetadataWithIsbn]` for the add-book UI's click-to-add candidates. New `search_books(query, limit=10)` in `book_alerter.metadata` prefers native `ISBN_13`, falls back to promoting `ISBN_10` via `to_isbn13`, filters items lacking title/author/ISBN (no ISBN → can't add, row useless). `isinstance(...)` guards on every level of the untrusted JSON. New `BookMetadataWithIsbn` model (isbn13 + title + author + optional cover_url) is the search wire shape; `BookMetadata` is unchanged. 175 tests passing.
**Branch:** `master` (no worktree)
**Last update:** 2026-05-14, Task 7.6 committed at `2ea43d7`

## Where we are

Phases 0–4 complete:
- **Foundation (0)** through **Scheduler (3)** as before.
- **Stats + alerts (4)**: `BookStats` + `compute_book_stats` in `src/book_alerter/stats.py` (uses the `book_stats` view + percentile_at via linear interp on sorted_totals). `compute_signal` returns BUY / WATCH / WAIT / TARGET_HIT / INSUFFICIENT_DATA. `detect_alert_kinds` in `src/book_alerter/alerts.py` fires `target_hit` / `percentile_cross` / `new_low` on transitions. `AlertPipeline.run(book_ids)` in `src/book_alerter/notifications/dispatcher.py` does the full pipeline (read prior `BookSignalState` → detect → global/per-book/mute/dedup filters → write Alert + NotificationDelivery → persist new BookSignalState). `InAppNotifier` is the only notifier so far. `app.py` lifespan wires `pipeline.run` into `Scheduler`'s `alert_pipeline`.

## Verify on return

```bash
cd /home/ff235/dev/book_alerter && uv run pytest -q
# expected: 175 passed
git log --oneline d953741..HEAD | wc -l
# expected: 76
uv run alembic current
# expected: 0004_book_stats_view (head)
```

## Next action

Dispatch **Phase 7 Task 7.7 (Refetch + notifications test — `POST /api/books/{id}/refetch` calls `scheduler.trigger_now` for each source; `POST /api/notifications/{channel}/test` synthesises an Alert and sends it through the named channel)**, plan line 2549.

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
- **`make_observation` fixture (Task 7.2)** in `tests/integration/api/conftest.py` inserts `PriceObservation` rows directly via a SQLModel session, auto-computing `total_minor = price_minor + (shipping_minor or 0)` and accepting `is_duplicate_of` for duplicate-row scenarios. Reuse for Task 7.3+ alerts/runs fixtures rather than going through the full source pipeline. Note: capture `obs.id` immediately after `make_observation` returns — the row detaches from its session when the `with Session(...)` block exits.
- **`make_alert` fixture (Task 7.3)** in `tests/integration/api/conftest.py` inserts `Alert` rows directly via a SQLModel session (defaults: `kind="target_hit"`, `price_minor=500`, `currency="GBP"`, `source="wob"`, `condition="used_g"`, `message="test alert"`, `dismissed_at=None`, `delivered_via=[]`). Same detached-instance gotcha as `make_observation` — capture `alert.id` inside the `with Session(...)` block. Reuse for Task 7.7 (alert-related) fixtures.
- **Idempotent dismiss pattern (Task 7.3)**: `POST /api/alerts/{id}/dismiss` checks `alert.dismissed_at is None` before writing — re-dismissing returns 200 with the original timestamp preserved. `POST /api/alerts/dismiss-all` uses a single `update(Alert).where(Alert.dismissed_at.is_(None)).values(dismissed_at=now)` via `session.exec(...)` and returns `result.rowcount` — never iterates row-by-row. Manual dismiss only (spec line 40); no auto-dismiss anywhere.
- **Config-mutating PATCH pattern (Task 7.4)**: `PATCH /api/sources/{name}` and the upcoming Task 7.5 `PATCH /api/config` follow the same shape. (1) Filter the patch body with `payload.model_dump(exclude_unset=True)` then drop None-valued entries — None means "don't change" (matches `BookPatch` semantics). (2) Build the new sub-model via `current.model_copy(update=patch_data)`. (3) Replace it inside a fresh top-level `Config` via `cfg.model_copy(update={"sources": {**cfg.sources, name: updated}})`. (4) Re-validate end-to-end with `Config.model_validate(new_cfg.model_dump())` (defensive; catches edge cases `model_copy` would skip). (5) Persist via the existing `Config.save(cfg_path)` (atomic tmp-replace). (6) Swap `request.app.state.config = new_cfg`. Empty body is a 200 no-op — **skip the save entirely** when `patch_data` is empty so the YAML file isn't created with defaults that don't match the live config. The config path is read off `request.app.state.config_path` (set by lifespan + test fixture) via `ConfigPathDep` from `api/deps.py`.
- **Scheduler stub for trigger tests (Task 7.4)**: the `api_client` fixture in `tests/integration/api/conftest.py` attaches `_StubScheduler` to `app.state.scheduler` — a minimal async stub exposing `trigger_now(name) -> int`, `calls: list[str]` for dispatch assertions, and `return_zero_for: set[str]` for backoff-gate simulation. Production uses a real `Scheduler` instance attached during lifespan; tests rely on the stub. Single-fixture wiring works because tests just mutate `client.app.state.scheduler.return_zero_for.add("wob")` when they need the backoff path.
- **`app.state.config_path` (Task 7.4)**: set by both `lifespan` (after `cfg = Config.load(cfg_path)`) and the `api_client` test fixture (before the app starts). PATCH-style endpoints that persist config back to disk pull it via `ConfigPathDep`. If you build a new test app outside of `api_client`, remember to set this attribute — the dep will `AttributeError` otherwise.
- **Monkeypatch at the import site, not the source module (Task 7.6)**: when an API handler does `from book_alerter.metadata import lookup_isbn` (or any star-style import), the handler binds the name in its own module namespace at import time. Tests must `monkeypatch.setattr("book_alerter.api.metadata.lookup_isbn", fake)` — patching `"book_alerter.metadata.lookup_isbn"` leaves the handler's already-resolved reference pointing at the original function and the fake never fires. Same rule applies to `search_books` and any other dependency imported by name. If you instead `import book_alerter.metadata as m` and call `m.lookup_isbn(...)`, patching the source module works — but the established pattern in this codebase is direct-name imports, so default to import-site patching. See `tests/integration/api/test_metadata_api.py` for the canonical example.
- **HTTP integration tests without cassettes (Task 7.6)**: when the code-under-test builds its own `httpx.AsyncClient` internally (e.g. `search_books`), inject `httpx.MockTransport` by monkeypatching `httpx.AsyncClient` in the target module's namespace to wrap a fake client that injects the transport. Cleaner than recording a real cassette for a small controlled payload; matches the `test_ntfy_notifier.py` pattern. The handler retains its 5s timeout etc. — only the transport is swapped.
- **Config PUT pattern (Task 7.5)**: `PUT /api/config` always returns `{diff, applied, errors}` and validates in both dry-run and apply modes — 422 fires identically in either. The wire shape is opinionated: do **NOT** add 200-with-errors as a "validation failed" channel. **Backup rotation** uses `shutil.copy2(config_path, config_path.with_suffix(suffix + ".bak"))` before `Config.save` — single rotating backup, overwrites any prior `.bak`, **skipped on first-write** (`config_path.exists()` guard). Don't ring-rotate (`.bak.1`, `.bak.2`); the user can keep their own snapshots if they need history. **Diff is top-level only** by deliberate choice — computed via `model_dump(mode="json")` on both sides so nested Pydantic models become plain dicts and `dict.__eq__` does deep equality. Recursive diff was rejected for MVP; UI renders the block-level changes. Env-var substitution is NOT re-run on PUT — `_substitute_env` only fires in `Config.load` from YAML on disk; the PUT body is the already-materialized config dict.

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
