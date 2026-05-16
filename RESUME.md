# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** **MVP COMPLETE + Tier-2 reviewed.** All plan phases 0–13 shipped. Post-MVP quality/perf pass landed on 2026-05-16: bounded windowed stats, lifespan-scoped httpx, per-book scrape error surfacing, BookStats wire-shape dedup, shipping-imputation marker, Monaco live theme, deep healthcheck, first-boot config seed, plus a `simplify` + `find-bugs` + `/second-opinion` cycle. **Next action: first deploy to NAS.**

**Branch:** `master` (no worktree, linear chain). 12 commits beyond the prior MVP-complete head (`bd4ffa5`).
**Last update:** 2026-05-16, Tier-2 review pass — branch is deploy-ready.

## What ships

- **Backend** — FastAPI + SQLModel + Alembic + APScheduler + structlog. Three sources (WoB via httpx, Bookfinder + Amazon UK via Playwright). Two notifiers (in-app always-on + ntfy.sh opt-in). Weekly SQLite backup via `VACUUM INTO`. Lifespan-scoped shared `httpx.AsyncClient` threaded through all non-Playwright HTTP. Per-book scrape health (`last_scrape_attempt_at` + `last_scrape_error`) on `Book`, written by the scheduler. Deep `/api/health` (DB `SELECT 1` + APScheduler `.running`) returns 503 on failure so Docker actually restarts an unhealthy container.
- **Frontend** — Vite + React 19 + TS + Tailwind v4 + shadcn/ui + Recharts + TanStack Query + Monaco. Served by FastAPI from the same port in production. Dashboard shows imputed-shipping marker (`~+£X.XX*`) when the cascade fills in missing postage; per-book red dot when `last_scrape_error` is set. Monaco editor re-themes live via the new `useIsDark` hook.
- **Stats** — `compute_book_stats` bounds the raw-observation scan to `max(WINDOW_DAYS, percentile_window_days)` days; `all_time_min/max` therefore mean "within window" (better signal for long-running deploys). Cascade-imputed shipping pulled via `seller_class()` (`amazon_fulfilled` vs `third_party`), with `(source, seller_class)` global medians gated by `min_global_median_observations`. `BookStats` wire shape now reads `windows[label_for_days(days)]` for percentiles; `current_percentile_rank` retained for custom (non-canonical) windows.
- **Deployment** — Multi-stage Dockerfile (`mcr.microsoft.com/playwright/python:v1.59.0-noble` base + Node 20 builder), `docker-compose.yml` with healthcheck + log rotation + shm_size + PUID/PGID, `.env.example` with every knob documented. First boot writes `data/config.yaml` from defaults so the user has a discoverable seed file.
- **Testing** — 273 unit/integration tests, 6 storyline-style end-to-end scenarios, 1 Docker boot smoke test, 2 live-network canaries (skipped by default).

## Test layers — verify on return

```bash
cd /home/ff235/dev/book_alerter

# Layer 1: unit + integration (≤6 s)
uv run pytest -q
# expected: 273 passed, 3 skipped, 1 deselected
#   - 3 skipped: live BookFinder/Amazon canaries (gated by BOOKFINDER_LIVE=1 / AMAZON_LIVE=1) + one VCR cassette gate
#   - 1 deselected: e2e marker (opt-in only)

# Layer 2: storyline scenarios (≤2 s)
bash tests/scenarios/run_all.sh
# expected: ALL SCENARIOS PASS (6/6)

# Layer 3: Docker boot smoke (~5 s, requires book_alerter:dev image)
docker build -t book_alerter:dev .          # ~20 s cold; cached ~2 s
uv run pytest -m e2e tests/e2e/ -q
# expected: 1 passed

# Frontend pipeline
cd web && npx tsc --noEmit && npx eslint . && npm run build
# expected: clean / clean / 505.12 kB main + 366.31 kB BookDetail chunk + 58.49 kB Advanced chunk

# Database
uv run alembic current
# expected: 0012_book_scrape_health (head)

# First-time setup on a new machine
uv run playwright install chromium
```

## Production smoke

```bash
cd /home/ff235/dev/book_alerter

# One-step deploy
cp .env.example .env  # edit NTFY_* if you want push
docker compose up -d
# wait ~10 s for healthy state

curl -sf http://127.0.0.1:8000/api/health     # 200 {"status":"ok",...}
curl -sfI http://127.0.0.1:8000/              # 200 (SPA shell)
curl -sfI http://127.0.0.1:8000/books/123     # 200 (SPA fallback)

# Teardown (data persists in ./data/)
docker compose down
```

## What to do first

1. **NAS deploy** (the next-session goal): bring the repo onto the target NAS, `id -u` / `id -g` on the host to get the right values for `PUID/PGID`, copy `.env.example` → `.env` and fill them (+ optional `GOOGLE_BOOKS_API_KEY`, `NTFY_TOPIC`), ensure `./data` is owned by `PUID:PGID`, then `docker compose up -d`. First boot will apply migrations `0001..0012` against an empty SQLite and seed `data/config.yaml` from defaults; deep `/api/health` gates orchestrator readiness. Synology default UID/GID is `1026:100`; Unraid is `99:100`.
2. **Browser smoke** — visit the deployed UI, add a book by ISBN, verify the dashboard renders the new fields (signal pill, mini-bars per window, imputed shipping marker, per-book red dot if a source fails).
3. **Ntfy wiring** — set `NTFY_SERVER` + `NTFY_TOPIC` in `.env` (or via the Notifications settings tab), click "Send test". Channel won't be instantiated if topic is empty.
4. **Live sources** — enable a source in Settings → Sources (default `config.yaml` ships `sources: {}` empty); WoB is the lowest-flake; Bookfinder and Amazon UK both Playwright-based with anti-bot exposure.

## Deferred follow-ups (NOT bugs — design choices or post-MVP scope)

Triage of the end-to-end scenario findings (see CHANGELOG "Scenario findings — triage"):

- **Quiet hours suppress, do NOT defer-and-replay**. Real fix would require a `status="queued"` `NotificationDelivery` row + a periodic drain job. Current behavior preserves all alert state in the in-app feed; only non-bypass push is silently dropped during the window. Documented inline in `dispatcher.py`.
- **Dedup uses wall clock, not `observed_at`**. Correct user-facing behavior; comment added to `_filter_dedup` (commit `f1970f7`). Scenarios needing distinct same-kind alerts across the window must use `freeze_time` (scenario 2 demonstrates).
- **Mute skips `BookSignalState` writes**. By design — preserves pre-mute prev state so `new_low` can still fire on mute-lift when there was a prior all-time min. Edge case (mute brand-new book before any observation) loses `new_low` capability on lift; not a realistic flow.

Carried follow-ups (deferred for first NAS deploy; revisit after production behaviour observed):

- **Source-ABC `prepare()`/`cleanup()` hooks** for per-scheduler-run shared Chromium browser (currently per-fetch shared); + **`PlaywrightInlineSource` base** to dedup the `_render` skeleton between bookfinder.py and amazon.py. Single follow-up PR scoped together. (Phase 8.3 simplify deferred.)
- **Telegram + Pushover notifier slots reserved** but unimplemented. `.env.example` has the env vars; `NotificationsConfig.channels` has no Pydantic types yet. Adding requires Pydantic models + dispatcher wiring + FE channel cards. (Working agreement: ntfy only at MVP.)
- **shadcn base color is `neutral`** (default); plan called for `slate`. Hand-edit `components.json` + re-init if/when the design decision matters. (Phase 9.1 deviation.)
- **`monaco-editor → dompurify`** 2 transitive moderate-severity audit findings. Address with `npm audit fix` or a newer `monaco-editor` pin once `@monaco-editor/react` bumps. (Phase 11.5 deferred.)
- **`openapi-typescript@7` peer-deps `typescript@^5`** but project is on TS6; installed with `--legacy-peer-deps`. Revisit if upstream widens its peer constraint. (Phase 9.2 carried.)
- **`gen:api` requires a running backend** — could add a `scripts/dump-openapi.py` that writes `/openapi.json` to disk for offline regen. (Phase 9.2 deferred.)
- **`POST /api/books` on duplicate ISBN** returns 409 with `detail` string only — doesn't include the existing book's ID. FE shows "Already tracked" without a link-out. Minor backend tweak (add `book_id` to the detail). (Phase 10.2 deviation.)
- **N+1 queries**: `compute_book_stats` in `GET /api/books` (medians hoisted to one scan per request via `source_seller_global_shipping_medians`, but per-book scan + cascade still happens N times); `_last_run_for` in `GET /api/sources`. On a small NAS dataset (≤200 books) this is fine but a `JOIN`+aggregation rewrite is the natural follow-up. (Pre-Phase 8 carried.)

Deferrals new in 2026-05-16 review pass:

- **TTL cache on `source_seller_global_shipping_medians`** — currently bounded to widest WINDOW_DAYS but still a full-table scan per dashboard render. ~30–90 MB peak materialisation at 100 books × 365 days. Within NAS budget today; if it bites in production, cache for ~60s. (Gemini second-opinion G-3, deferred.)
- **Per-source scrape health** — `last_scrape_error` is `Book`-row-grained with last-write-wins across sources, so a book with one failing source and one succeeding source will flicker between error/no-error on the dashboard depending on completion order. Documented design choice; revisit if real-world flicker becomes a UX issue. (Gemini second-opinion G-5b, accepted as design.)
- **React.memo on MiniBars** — premature with 9 dashboard rows; revisit only if dashboard rendering becomes visibly janky.
- **Bound per-book raw observation table** (not just stats reads) — the SQLite table grows unbounded; eventual prune job + retention policy is a natural follow-up once we know how many years of history a user actually wants.
- **Sentry DSN wiring** — `.env.example` has the slot but nothing reads it.

Closed by the 2026-05-16 review pass (no longer deferred):

- ~~Long-lived `httpx.AsyncClient` could lift into FastAPI lifespan~~ — shipped in `dcab912` (B3).
- ~~Per-book scrape failures don't surface beyond logs~~ — shipped in `a9842ca` (B2): `last_scrape_attempt_at` + `last_scrape_error` columns + dashboard indicator.
- ~~Monaco theme not live-updated~~ — shipped in `f937aa2` + `a1d58c8` (C4): `useIsDark` hook drives both Monaco and ThemeToggle.
- ~~Notifier registry frozen at startup~~ — already addressed by `rebuild_runtime()` in `app.py`. Same for source registry.

## Open decisions

_None._ All blockers from Phase 1 resolved.

## Implementer prompt hardening (must apply to EVERY future task dispatch)

> All file edits MUST be within `/home/ff235/dev/book_alerter/`. If any tool reports a workspace-level conflict, STOP and report BLOCKED — do NOT modify files in sibling projects (`/home/ff235/dev/{suroor_ai,podcast_ai,audio_commons,MLResearch,...}`) or the workspace root (`/home/ff235/dev/pyproject.toml`).

## Process notes for next session

- **After every migration task, run `uv run alembic upgrade head`** so the dev DB at `data/book_alerter.db` stays at head. Otherwise the next `alembic revision --autogenerate` fails with "Target database is not up to date."
- **`Literal[...]` SQLModel fields** must be declared with `sa_column=Column(String, nullable=False)` because SQLModel 0.0.22's type inference calls `issubclass(Literal, Enum)` → `TypeError`. See `Book.format`, `Book.status`, `PriceObservation.condition`, `SourceRun.status`, `Alert.kind`, `NotificationDelivery.status` for the established pattern.
- **`Condition` Literal lives in `book_alerter.db.models`** and is re-exported by `book_alerter.sources.base`. New sources should import from `sources.base` (semantic origin) but canonical definition is in `db.models`.
- **`tests/conftest.py` provides `transient_book` + `transient_stats`** for unpersisted construction. `tests/integration/conftest.py` provides `sqlite_engine` + `engine_with_view` (with `book_stats` view installed) + `make_book` (persisted) + `wob_vcr` / `metadata_vcr` (VCR factories) + cassette dir constants. Reach for these before writing local helpers.
- **HTTP API cassette convention**: per-source cassettes live under `tests/integration/cassettes/<source>/`. The WoB cassettes still live under `tests/integration/sources/cassettes/` for historical reasons — new HTTP integrations should follow the `cassettes/<name>/` layout. Use `record_mode="none"` for CI replay; include `("method","scheme","host","port","path","query")` in `match_on` when query strings carry request semantics.
- **For new HTTP integrations**, mirror `notifications/ntfy.py` and `sources/wob.py`: per-call `httpx.AsyncClient`, short timeout (5–10s), no retries (rely on race/orchestration). For OL+GB-style fan-outs, use a single shared `AsyncClient` inside the orchestrator and let helpers take it as an argument — see `metadata.lookup_isbn`.
- **New VCR cassettes**: use the shared `_vcr_factory(cassette_dir, default_record_mode, *, match_query)` helper in `tests/integration/conftest.py`. Expose as a `@pytest.fixture` named `<source>_vcr` with a `<NAME>_CASSETTE_DIR` constant.
- **Untrusted JSON extraction** must `isinstance(..., dict)`-guard before calling `.get(...)` on values that the schema documents as objects but real responses sometimes deliver as strings/None. See `_fetch_openlibrary` in `metadata.py`.
- **`Notifier` ABC has `bypasses_quiet_hours: bool = False`** (Phase 5 simplify). New push channels leave it False; the in-app channel sets it True. The dispatcher uses this flag rather than checking `n.name == "inapp"`.
- **`Notifier.send` returns `NotificationResult` (TypedDict)** from `book_alerter.notifications.base`: `{"status": Literal["sent","error"], "error_message"?: str}`.
- **Don't `git add -A`** — `.claudesignore` is auto-generated by ccage and intentionally untracked. Add explicit paths or stage selectively.
- **`book_stats` view DDL** lives in `src/book_alerter/db/views.py` as `BOOK_STATS_VIEW_SQL` + `DROP_BOOK_STATS_VIEW_SQL`. Migration 0004 imports from there; integration tests install via `engine_with_view`. Don't redefine the DDL anywhere else.
- **`AlertKind` Literal lives in `src/book_alerter/config.py`** and is re-exported by `book_alerter.alerts`. If you add a new alert kind, update only `config.py`.
- **`detect_alert_kinds` returns `(kinds, cur_signal)`** as of the Phase 4 simplify — the dispatcher reuses `cur_signal` when persisting `BookSignalState`. Don't recompute `compute_signal` after calling it.
- **API test pattern**: build a router-only `FastAPI()` test app via `api_client` fixture in `tests/integration/api/conftest.py` — `engine_with_view` for the DB, default `Config.load(<missing>)` for state, `app.include_router(<module>.router)`. Avoids scheduler/notifier startup, sub-millisecond tests. Production uses `create_app()` + lifespan.
- **FastAPI dependency style**: use `SessionDep = Annotated[Session, Depends(get_session)]` (module-level) and write handlers as `def foo(... , session: SessionDep, ...)`. Avoids ruff's `B008` while keeping FastAPI auto-DI. Non-defaulted `SessionDep` must precede defaulted query params.
- **Pydantic mirrors for dataclass serialization**: when a handler returns a `@dataclass`, define a small Pydantic `BaseModel` mirror with `.from_dataclass()` and use it as `response_model`. See `BookStatsOut` for `BookStats`.
- **`make_observation` / `make_alert` fixtures** (Tasks 7.2 / 7.3) — insert rows directly via SQLModel session. Capture `obj.id` inside the `with Session(...)` block; rows detach when it exits.
- **Idempotent dismiss pattern** (Task 7.3): `POST /api/alerts/{id}/dismiss` checks `dismissed_at is None` before writing — re-dismissing returns 200 with original timestamp. `POST /api/alerts/dismiss-all` uses single `update(...).values(dismissed_at=now)` and returns `rowcount`. No auto-dismiss anywhere.
- **Config-mutating PATCH pattern** (Task 7.4): `(1)` filter body with `model_dump(exclude_unset=True)` + drop Nones; `(2)` `model_copy(update=patch)`; `(3)` replace in fresh top-level `Config`; `(4)` re-validate end-to-end; `(5)` persist via `Config.save(cfg_path)` (atomic tmp-replace); `(6)` swap `request.app.state.config`. Empty body → 200 no-op, skip the save.
- **Scheduler stub for trigger tests** (Task 7.4): `_StubScheduler` in `api_client` fixture exposes `trigger_now`, `calls: list[str]`, `return_zero_for: set[str]` for backoff simulation.
- **`app.state.config_path`** (Task 7.4): set by lifespan + test fixture. PATCH endpoints pull via `ConfigPathDep` from `api/deps.py`.
- **Monkeypatch at the import site** (Task 7.6): when a handler does `from book_alerter.metadata import lookup_isbn`, patch `"book_alerter.api.metadata.lookup_isbn"`, not the source module. See `tests/integration/api/test_metadata_api.py`.
- **HTTP integration tests without cassettes** (Task 7.6): inject `httpx.MockTransport` by monkeypatching `httpx.AsyncClient` in the target module's namespace. Matches `test_ntfy_notifier.py`.
- **Notifier-by-name lookup** (Task 7.7): `app.state.notifiers: dict[str, Notifier]` keyed by `notifier.name`. Pull via `NotifiersDep`. Test-side `_StubNotifier` exposes `calls` + `next_result`.
- **Refetch fan-out** (Task 7.7): `POST /api/books/{id}/refetch` iterates `cfg.sources.items()`, NOT observed sources. Result shape `{triggered, skipped}` with `reason="disabled"|"backoff_active"`. The 409 contract from `POST /api/sources/{name}/run` doesn't apply (partial success is the norm for fan-out).
- **Synthetic Alert non-persistence** (Task 7.7): `POST /api/notifications/{channel}/test` builds in-memory `Book` + `Alert` (`id=None`). No DB write. The "alert table stays empty" assertion in `test_notifications_api.py` is load-bearing.
- **Config PUT pattern** (Task 7.5): `PUT /api/config` always returns `{diff, applied, errors}`. 422 fires identically in dry-run and apply modes. Backup rotation: single `.bak` overwriting any prior; skipped on first-write. Diff is top-level only by deliberate choice. Env-var substitution NOT re-run on PUT.
- **Cross-cutting router dependencies** (Task 7.8): wire via `app.include_router(router, dependencies=[Depends(my_dep)])` in `create_app()`. For optionally-enabled deps, compute the list once at `create_app()` time (empty when disabled = zero per-request cost). Auth is the canonical example.
- **FE typed client** (Phase 9.2): `web/src/api/client.ts` exports `apiGet/Post/Patch/Put/Delete<P>` typed against generated `paths` from `web/src/api/schema.ts`. No `any` anywhere. Regen via `cd web && npm run gen:api` against a running backend.
- **FE simplify-pass shared helpers** (Phase 10/11 simplifies): `<Skeleton>` primitive at `web/src/components/ui/skeleton.tsx`; `formatErrorMessage` in `web/src/lib/utils.ts`; `<SignalPill>` lifted to `web/src/components/books/signal.tsx`; `diffToRows` + `formatPutError(Message)` in `web/src/lib/config-diff.ts`; `useSavedFlash` in `web/src/hooks/useSavedFlash.ts`. Use these before writing local copies.
- **Mount-key remount pattern** (Phase 11): `<SourceCard>`, `Recommendation`, `Notifications` all use a composite mount key built from server fields. After a successful PATCH + cache invalidate, the new server snapshot becomes the mount key → component remounts → `useState(server)` re-initialises cleanly. Sidesteps `react-hooks/set-state-in-effect` without a `null`-sentinel maze.

## Working agreements (do NOT re-decide)

- Tech stack: Python 3.12 / uv / FastAPI / SQLModel / Alembic / APScheduler / structlog · React 19 / Vite / TS / Tailwind v4 / shadcn/ui / Recharts · Playwright (for browser-required sources)
- Deployment: Docker (multi-stage) on NAS; Tailscale-only access; HTTP Basic optional but off by default
- Sources at MVP: WoB UK (inline `httpx`), Bookfinder (inline Playwright), Amazon UK (inline Playwright). **Architecture revision 2026-05-14**: original design called for Go source-CLIs generated via `printing-press` + orchestrated through a `SubprocessSource` ABC; that path was abandoned for Phase 8.2 (AWS WAF `mp_verify` defeated every static-cookie / pure-Go-solver replay) and removed entirely. `SubprocessSource` deleted; no Go binaries; no printing-press dependency.
- Push at MVP: **ntfy only**. Telegram + Pushover deferred (no schema slots yet).
- Region: UK only at MVP; schema pluggable.
- Identity: ISBN-pinned; `isbnlib` normalises ISBN-10 → ISBN-13.
- Recommendation: hybrid (percentile default + per-book target override); `INSUFFICIENT_DATA` cold-start.
- Stats: `book_stats` SQL view + `compute_book_stats()` Python helper (no materialised stats table).
- Money: integer minor units (pence); never floats.
- Time: UTC in DB; render local in UI.

## Key files

- `README.md` — user-facing onboarding (Phase 13.2).
- `docs/superpowers/specs/2026-05-09-book-alerter-design.md` — design spec (authoritative for behaviour).
- `docs/superpowers/plans/2026-05-09-book-alerter-implementation.md` — task-by-task implementation plan.
- `docs/CHANGELOG.md` — append-only log of completed implementation tasks (commits, deviations).
- `RESUME.md` — this file (cursor + open decisions only).

## Conventions for autonomous work

- One subagent dispatch = one plan task. After commit, update CHANGELOG → update RESUME.
- Commits are made by the subagent at task end.
- Stop on real ambiguity. Don't ad-lib design decisions; defer to spec + plan; if conflict, surface here.
- If a subagent fails twice on the same task, stop and document.
- Phase boundaries are natural checkpoints — leave a clean note in RESUME at each.
