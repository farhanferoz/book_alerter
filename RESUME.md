# Book Alerter — Resume

> Lean session-resumption file. Don't bloat. Reference other docs for detail.

**Status:** **MVP COMPLETE + Tier-2/3 reviewed × 3 + pre-deploy quality/perf pass + PRODUCTS FEATURE shipped.** All book-side plan phases 0–13 shipped; the 2026-05-23 products plan (`docs/superpowers/plans/2026-05-23-products-implementation.md`) is end-to-end complete with tier-3 review fixes landed. The app now tracks non-book Amazon products (ASIN-keyed) end-to-end alongside books: scrape via `AmazonUKProductInlineSource`, persist to parallel tables (`Product` / `ProductObservation` / `ProductAlert` / `ProductSignalState`), share the alert/notifier/dispatcher machinery via `_AlertModels` parameterisation, surface via `/api/products/*` + `/products` FE routes, polymorphic `NotificationDelivery` (CHECK-enforced exactly-one of `alert_id` / `product_alert_id`). Tier-3 review caught and fixed two scheduler isolation bugs (per-kind + per-item exception leaks), a refetch fan-out scoping bug (was firing book-only sources on product refetches), and an SSRF gap on the user-supplied `product.image_url`. **Next action: first deploy to NAS** (unchanged — products are additive; existing deploy steps work as-is).

**Branch:** `master` (no worktree, linear chain). 36 commits beyond the prior MVP-complete head (`bd4ffa5`): 27 pre-products + 9 products.
**Last update:** 2026-05-23, 9-commit products feature add. All tests green: **395 unit/integration + 7/7 scenarios + ruff clean + ty clean + FE TS+ESLint+build clean + 0 FK violations + alembic head `0016_product_stats_view`**.

## What ships

- **Backend** — FastAPI + SQLModel + Alembic + APScheduler + structlog. Four sources: WoB (httpx, books), Bookfinder (Playwright, books), Amazon UK book source (Playwright, books), and `AmazonUKProductInlineSource` (Playwright, products — uses Product.asin directly, honours `track_used`). All sources share the `Source.fetch(item: TrackedItem)` ABC; scheduler intersects `Source.item_kinds` ∩ `SourceConfig.item_kinds` to route per-kind iteration. One `AlertPipeline` implementation is parameterised on `_AlertModels` so books and products share dedup/quiet-hours/notifier dispatch with the right tables substituted in. Two notifiers (in-app always-on + ntfy.sh opt-in) — both take `AlertLike` / `ItemLike` protocols so book and product alerts route through the same channels. `NotificationDelivery` is polymorphic: exactly one of `alert_id` / `product_alert_id` is set per row (CHECK constraint). Weekly SQLite backup. Per-item scrape health (`last_scrape_attempt_at` + `last_scrape_error`) on `Book` AND `Product`. Deep `/api/health` (DB `SELECT 1` + APScheduler `.running`) returns 503 on failure.
- **Frontend** — Vite + React 19 + TS + Tailwind v4 + shadcn/ui + Recharts + TanStack Query + Monaco. Two top-level routes: `/` (books dashboard, unchanged) and `/products` (new). Products dashboard is deliberately leaner than books (no signal pill / mini-bars / chart yet — extend if you want); `/products/:id` detail page has image + title header, refetch/archive/delete actions, target-price form, track-used toggle, recent-observations table. Add-product modal pastes ASIN or Amazon URL → debounced `/api/metadata/asin-lookup` → preview → confirm.
- **Stats** — Single `_compute_stats_impl(item_id, session, window_days, *, schema, ...)` parameterised on `_ItemSchema` (observation_table / id_column / stats_view). `compute_book_stats` and `compute_product_stats` are 3-line wrappers. `source_seller_global_shipping_medians(session, *, schema=...)` likewise. Cascade-imputed shipping pulled via `seller_class()` (`amazon_fulfilled` vs `third_party`), with `(source, seller_class)` global medians gated by `min_global_median_observations`. `BookStats` dataclass is item-kind-agnostic — used for both kinds; the `book_id` field is reused as the item id (documented; full rename deferred).
- **Enums** — New `src/book_alerter/enums.py` is the canonical home for shared StrEnums: `Condition`, `AlertKind`, `ItemKind`, `ItemStatus`, `SourceRunStatus`, `NotificationDeliveryStatus`, `BookFormat`. Wire format identical to the previous Literal strings. Field columns stay `Column(String, nullable=False)` (not `Column(Enum(...))`) to dodge SQLAlchemy's name-vs-value pitfall — equality via StrEnum's `str` subclassing works both ways.
- **Polymorphic NotificationDelivery** — migration 0015 made `alert_id` nullable and added nullable `product_alert_id` FK→productalert CASCADE + the `(alert_id IS NULL) <> (product_alert_id IS NULL)` CHECK. Dispatcher writes the row with the kind-specific FK column via `_AlertModels.delivery_fk_attr` (`alert_id` for books, `product_alert_id` for products). Constraint test pins the four-case matrix.
- **Refetch fan-out** — `_run_refetch(cfg, scheduler, *, kind)` (in `api/books.py`) is shared by both `POST /api/books/{id}/refetch` and `POST /api/products/{id}/refetch`. Sources whose `SourceConfig.item_kinds` doesn't include the kind being refetched are skipped with `reason="kind_unsupported"` (alongside the existing `disabled` / `backoff_active` reasons).
- **Image SSRF guard** — `product.image_url` is user-controllable; `api/products.py::_is_safe_image_url` rejects anything that isn't `https://` pointing at an Amazon CDN host (m.media-amazon.com, images-na.ssl-images-amazon.com, images-eu.ssl-images-amazon.com, etc.) before the image proxy fetches it.
- **Deployment** — Multi-stage Dockerfile (`mcr.microsoft.com/playwright/python:v1.59.0-noble` base + Node 20 builder), `docker-compose.yml` with healthcheck + log rotation + shm_size + PUID/PGID, `.env.example` with every knob documented. First boot writes `data/config.yaml` from defaults so the user has a discoverable seed file. No new env vars for the products feature; new YAML knob is `sources.<name>.item_kinds: [book|product]` (default `[book]` preserves the pre-products config).
- **Testing** — 395 unit/integration tests (+93 over the prior 302 baseline), 7 storyline-style end-to-end scenarios, 1 Docker boot smoke test, 2 live-network canaries (skipped by default).

## Test layers — verify on return

```bash
cd /home/ff235/dev/book_alerter

# Layer 1: unit + integration (≤8 s)
uv run pytest -q
# expected: 395 passed, 3 skipped, 1 deselected
#   - 3 skipped: live BookFinder/Amazon canaries (gated by BOOKFINDER_LIVE=1 / AMAZON_LIVE=1) + one VCR cassette gate
#   - 1 deselected: e2e marker (opt-in only)

# Layer 2: storyline scenarios (≤3 s)
bash tests/scenarios/run_all.sh
# expected: ALL SCENARIOS PASS (7/7)
# scenario_07_product_lifecycle is the product mirror of scenario_01

# Layer 3: Docker boot smoke (~5 s, requires book_alerter:dev image)
docker build -t book_alerter:dev .          # ~20 s cold; cached ~2 s
uv run pytest -m e2e tests/e2e/ -q
# expected: 1 passed

# Frontend pipeline
cd web && npx tsc --noEmit && npx eslint . && npm run build
# expected: clean / clean / ~603 kB main + ~372 kB BookDetail chunk + ~58 kB Advanced chunk
# (chunk-size warning is pre-existing, not products-specific)

# Database
uv run alembic current
# expected: 0016_product_stats_view (head)

# FK pragma check
uv run python -c "import sqlite3; con=sqlite3.connect('data/book_alerter.db'); cur=con.cursor(); cur.execute('PRAGMA foreign_keys=ON'); cur.execute('PRAGMA foreign_key_check'); print('violations:', len(cur.fetchall()))"
# expected: violations: 0

# First-time setup on a new machine
uv run playwright install chromium
```

## Production smoke

```bash
cd /home/ff235/dev/book_alerter

# Convenience wrapper (added 2026-05-16) — up + wait-for-healthy + smoke
scripts/start.sh                  # up
scripts/start.sh status           # health + last 30 log lines
scripts/start.sh logs             # follow logs
scripts/start.sh down             # stop and remove the container

# Or use docker compose directly:
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

1. **NAS deploy** (the next-session goal): bring the repo onto the target NAS, `id -u` / `id -g` on the host to get the right values for `PUID/PGID`, copy `.env.example` → `.env` and fill them (+ optional `GOOGLE_BOOKS_API_KEY`, `NTFY_TOPIC`), ensure `./data` is owned by `PUID:PGID`, then `docker compose up -d`. First boot will apply migrations `0001..0016` (the products tables + polymorphic notification delivery + product_stats view) against an empty SQLite and seed `data/config.yaml` from defaults; deep `/api/health` gates orchestrator readiness. Synology default UID/GID is `1026:100`; Unraid is `99:100`.
2. **Browser smoke** — visit the deployed UI, add a book by ISBN AND add a product by ASIN/URL, verify both dashboards render (signal pill, mini-bars per window, imputed shipping marker, per-book/per-product red dot if a source fails). Add a product → confirm the Add-Product modal pre-fills title/image from the asin-lookup endpoint.
3. **Ntfy wiring** — set `NTFY_SERVER` + `NTFY_TOPIC` in `.env` (or via the Notifications settings tab), click "Send test". Channel won't be instantiated if topic is empty. Both book and product alerts route through the same channels.
4. **Live sources** — enable a source in Settings → Sources (default `config.yaml` ships `sources: {}` empty); WoB is the lowest-flake book source. To enable products, add an `amazon_uk_product` source with `item_kinds: [product]` and `enabled: true` in `config.yaml`. The `sources` UI editor may not yet surface `item_kinds`; edit the YAML directly via Settings → Advanced if so. Amazon UK product is Playwright-based with the same anti-bot exposure as the book Amazon source.

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

- **TTL cache on `source_seller_global_shipping_medians`** — partial mitigation landed 2026-05-23 (snapshot precomputed once per pipeline cycle in `dispatcher.run`). Still a full-table scan per dashboard render; if it bites in production, cache for ~60s. (Gemini second-opinion G-3, partially addressed.)
- **Per-source scrape health** — `last_scrape_error` is `Book`-row-grained with last-write-wins across sources, so a book with one failing source and one succeeding source will flicker between error/no-error on the dashboard depending on completion order. Documented design choice; revisit if real-world flicker becomes a UX issue. (Gemini second-opinion G-5b, accepted as design.)
- **React.memo on MiniBars** — premature with 9 dashboard rows; revisit only if dashboard rendering becomes visibly janky.
- **Bound per-book raw observation table** (not just stats reads) — the SQLite table grows unbounded; eventual prune job + retention policy is a natural follow-up once we know how many years of history a user actually wants.
- **Sentry DSN wiring** — `.env.example` has the slot but nothing reads it.

Closed by the 2026-05-23 pre-deploy pass (no longer deferred):

- ~~`Scheduler.running` not exposed — deep healthcheck always returned "no probe available"~~ — shipped in `ae2b581`: added `@property running` mirroring `self._sched.running` so a crashed APScheduler now trips the 503.
- ~~`rebuild_runtime` could brick automation on a bad config~~ — shipped in `ae2b581`: now builds the new runtime BEFORE shutting down the old, so `PUT /api/config` with a malformed cron / unknown source name keeps the previous scheduler serving.
- ~~Two source runs finishing within the dedup window could double-fire the same alert~~ — shipped in `ae2b581`: per-book `asyncio.Lock` in `AlertPipeline` serialises same-book overlap; distinct books still process in parallel. Same commit added the per-source lock in `Scheduler._run_source` so manual `trigger_now` can't race the cron-fired job.
- ~~Hard-delete left orphan rows in PriceObservation / Alert / NotificationDelivery / BookSignalState~~ — shipped in `ae2b581` (initial hand-cascade) and superseded by `8708cbb` (migration 0013 + `PRAGMA foreign_keys=ON` + schema-enforced ON DELETE CASCADE; hand-cascade removed).
- ~~Bookfinder could persist a negative item price after rounding amplification~~ — shipped in `ae2b581`: parser now rolls back to "unknown shipping split" when shipping ends up larger than the visible total.
- ~~`p50 = 0` in alert message rendering raised ZeroDivisionError post-commit~~ — shipped in `ae2b581`: guard the `(p50 - current) / p50` divisor with `p50 > 0`, so a degenerate £0-observation history doesn't break the notify path silently.
- ~~SQLite default journal serialised dashboard reads behind scraper writes~~ — shipped in `ffff236`: `journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=5000` + `temp_store=MEMORY` PRAGMAs on every connection.
- ~~`compute_book_stats` re-ran the global shipping-median scan per book~~ — shipped in `ffff236`: snapshot computed once per `AlertPipeline.run` and threaded into `_run_one`.
- ~~Bot-marker fragility: Amazon + Bookfinder reported 0 offers silently when anti-bot served an unknown variant~~ — shipped in `908424d`: parsers now assert positive page markers (`#dp-container` / `#productTitle`, `#aod-container`, `#book-search-input-desktop`, etc.) before returning []; raise SourceError on unknown layouts. Extended to WoB in `aa3516f`.
- ~~`_PRICE_AMOUNT_RE` could drift onto unrelated rails (frequently-bought-together, recommended titles)~~ — shipped in `fc29860`: fallback regex scoped to the `twister-plus-buying-options-price-data` div via selectolax; full-page search retained as last-ditch fallback for older layouts.
- ~~WoB `_LDJSON_RE` + `_META_RE` regexes are brittle to template tweaks~~ — shipped in `aa3516f`: both extraction paths migrated to selectolax DOM lookups; positive-marker check added consistent with Amazon/Bookfinder.
- ~~FK cascade lived in app code, not the schema~~ — shipped in `8708cbb`: migration 0013 adds ON DELETE CASCADE to four FKs (priceobservation/alert/notificationdelivery/booksignalstate → book/alert); `PRAGMA foreign_keys=ON` per-connection in `db/session.py`. Audit confirmed 0 orphans across all child tables; row counts preserved across migration.
- ~~ruff backlog of 72 cosmetic findings~~ — shipped in `4c555ba` + `7394747`: auto-fixed where safe, hand-wrapped 5 legit E501s, configured per-file ignores for Alembic-generated migrations + test-setup semicolon idioms + intentional typography (en-dashes for ranges, `×` for multiplication, `∨` for logical-or in math comments). `ruff check ./src ./tests` now reports zero findings.

Deferrals new in 2026-05-23 products tier-3 review pass:

- **Keepa backfill duplication** — `_keepa_backfill_blocking` in `api/books.py` and `api/products.py` are near-line-for-line clones (model class + helper-fn substitution). Tier-3 simplify reviewer flagged as a candidate for extraction into a single `_keepa_backfill_for_schema(item_id, identifier, *, session_factory, schema, fetch_png, dp_url_for)` parameterised on `_ItemSchema`. Worth doing; deferred to avoid mixing with the bug-fix commit.
- **Scheduler kind routing dispatch in `_run_kind_for_source`** — two if/else blocks switch on `ItemKind` to pick `(item_model, identifier_attr, observation_model, item_fk_attr)`. Could be absorbed into `_ItemSchema` or a new `_KindRouting` keyed by `ItemKind`. Worth doing; the third dispatch-on-kind site after stats + dispatcher.
- **`Signal` Literal → `StrEnum`** — `Signal = Literal["BUY","WATCH","WAIT","TARGET_HIT","INSUFFICIENT_DATA"]` in `stats.py` is still a Literal. Migrate to `StrEnum` in `enums.py` per the project enum-preferred rule. Bigger refactor (touches every alert-pipeline call site); not on the critical path.
- **`BookStats.book_id` field rename to `item_id`** — wire-format field name leaks into product API responses via `BookStatsOut.book_id`. Plan-deferred. Add `@property def book_id` shim if renaming for the FE clients.
- **FE `ProductsDashboard` / `ProductDetail` use local `priceCell` + `fmtDateTime` helpers** instead of the shared `formatMoneyMinor` / `formatDateTime` in `web/src/lib/format.ts`. Reach for the shared helpers when adding the next FE iteration.
- **`to_asin` docstring drift** — the `amzn.eu/d/<code>` short-link example in the docstring describes behaviour the regex doesn't support (10-char ASIN exact match; amzn.eu short codes are 8-9 chars and need an HTTP redirect to resolve). Either drop the example or implement the resolution. Docstring noise; behaviour unchanged.
- **`refetch_book` source filtering by kind** — symmetric to the product-side fix that prompted this section. Book refetches now correctly skip product-only sources via the shared `_run_refetch` helper, so this is **closed** but worth flagging that the fix touched both endpoints.
- **Amazon source rate-limit on the doubled scrape volume** — adding products means each scheduler cycle hits Amazon for `N_books + N_products` ASINs. Per-source `max_consecutive_errors` gate already exists; consider a per-source-per-kind concurrency cap if anti-bot tightens.
- **Per-product scrape health vs per-book** — same last-write-wins flicker design as books (doc'd in the books deferred list above). Applies symmetrically to products.

Closed by the 2026-05-23 products tier-3 review pass (no longer deferred):

- ~~Per-kind exception in scheduler dropped sibling kind's alert pipeline~~ — shipped in `f388efa`: each kind's `_run_kind_for_source` wrapped in its own try/except; `kind_exceptions` list drives SourceRun status. Regression test in `test_scheduler_products.py::test_scheduler_isolates_per_item_unexpected_exception`.
- ~~Per-item exception in `_one` aborted entire kind iteration~~ — shipped in `f388efa`: `_one` catches every `Exception` and charges the item via `_record_item_failure`; `asyncio.gather` gains `return_exceptions=True`. Regression test pins the contract.
- ~~`POST /api/products/{id}/refetch` fanned out to book-only sources~~ — shipped in `f388efa`: shared `_run_refetch(cfg, scheduler, *, kind)` helper filters sources by `SourceConfig.item_kinds`. New `RefetchSkipped.reason="kind_unsupported"`. Books-side gap closed in the same commit.
- ~~User-controllable `product.image_url` → SSRF probe via image proxy~~ — shipped in `f388efa`: `_is_safe_image_url` rejects non-https or non-Amazon-CDN hosts before the proxy fetches.
- ~~`api/products.py` late-imports + `_ = (Condition, Path, HttpDep)` sentinel block~~ — shipped in `f388efa`: lifted to top-of-file imports per ruff I001; `_product_image_cache_path` is now pure (no mkdir side effect).
- ~~`product_source_seller_global_shipping_medians` dead wrapper~~ — shipped in `f388efa`: deleted. `Stats = BookStats` alias added.

Closed by the 2026-05-16 review pass (no longer deferred):

- ~~Long-lived `httpx.AsyncClient` could lift into FastAPI lifespan~~ — shipped in `dcab912` (B3).
- ~~Per-book scrape failures don't surface beyond logs~~ — shipped in `a9842ca` (B2): `last_scrape_attempt_at` + `last_scrape_error` columns + dashboard indicator.
- ~~Monaco theme not live-updated~~ — shipped in `f937aa2` + `a1d58c8` (C4): `useIsDark` hook drives both Monaco and ThemeToggle.
- ~~Notifier registry frozen at startup~~ — already addressed by `rebuild_runtime()` in `app.py`. Same for source registry.
- ~~Amazon source skips the used market when a new copy is in stock~~ — shipped in `b57f82a`: `fetch()` now renders both dp + offer-listing every call and `_merge_offers` dedups overlapping rows by `(seller, condition, price)` preferring concrete shipping over `None`. Doubles per-Amazon-book wall-clock (≈5 s → ≈10 s); still well inside the 6 h scheduler cadence at ≤200 books.
- ~~scenario_01 fails on Phase B's "first computed signal" assertion~~ — shipped in `0df2403`: scenario observations are anchored at `2026-01-01` but the pipeline ran on the real clock, so `compute_book_stats`'s 90-day window filtered them all out (today is past the window). Wrapped `main()` in `freeze_time("2026-01-18 12:00:00")`. Test-rot only; no production change.
- ~~scenarios 05 + 06 carry the same fixed-date pattern without freeze_time~~ — shipped in `d55a559`: both wrapped in `freeze_time` at deterministic instants past their observation ranges, mirroring scenario_01's pattern. Currently pass without it but will rot the same way if any future change ties their assertions to windowed stats.
- ~~Scheduler `_persist` matches sellers case-sensitively while `_merge_offers` casefolds~~ — shipped in `4c78f55` + `9137eb9`: case-insensitive (via `func.lower()`) initially, then expanded to trim+lower after a gemini second-opinion flagged a latent whitespace asymmetry (`_normalize_seller` strips, `_persist` didn't). Both layers now run identical ASCII `strip().lower()` semantics, with `func.trim(func.lower(...))` on the SQL side.
- ~~Amazon offer-listing parser drops used grades + reports £50 RRP + leaks "Sold by" label~~ — shipped in `9b1a74e` (Tier-1 simplify follow-up `5f25bfd`): three bugs surfaced when the new dp+offer-listing merge first ran against real Amazon HTML for `9780241638194`. Real Amazon uses an apex pricing template where the offer price lives in `.apex-pricetopay-accessibility-label` (sibling of `.a-price`, not a child), the RRP strike-through sits inside `.a-price.apex-basisprice-value`, and the seller is on `aria-label="X. Opens a new page"`. Parser now tries the apex label first, expands the strike-through skip list with `apex-basisprice-value` + `centralizedApexBasisPriceCSS`, and pulls seller from the aria-label (handles both marketplace `<a>` and Amazon-direct `<span>` variants). Trimmed real-HTML fixture (`tests/fixtures/amazon/9780241638194-uk-offer-listing-real.html`) pins the new selectors against drift. Companion capture script at `scripts/capture_amazon_offer_listing.py` mirrors the existing dp capture.
- ~~Need a one-command convenience wrapper to start the app and tail health~~ — shipped in `9b1a74e`: `scripts/start.sh up|down|restart|logs|status` wraps `docker compose` + health polling + smoke check.

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
- **(Products) Shared StrEnums in `src/book_alerter/enums.py`.** New typed string sets default to StrEnum. Existing book-side `Literal[...]` types were migrated to StrEnum where they're shared with products (`Condition`, `AlertKind`, `ItemStatus`, `BookFormat`, etc.). Wire format unchanged; `Column(String, nullable=False)` retained so storage = `.value` (not `.name`).
- **(Products) `_AlertModels` parameterisation.** `AlertPipeline.__init__(... , models: _AlertModels)`. Two module-level bundles (`BOOK_MODELS` / `PRODUCT_MODELS`) in `notifications/dispatcher.py`. Every kind-specific class + the dispatch table for `NotificationDelivery.delivery_fk_attr` lives there. Adding a third item kind = add a third `_AlertModels`.
- **(Products) `_ItemSchema` parameterisation.** `stats.py` uses `_BOOK_SCHEMA` / `_PRODUCT_SCHEMA` to substitute `observation_table` / `id_column` / `stats_view` in the SQL templates. Hardcoded constants — no user input reaches the substitution.
- **(Products) `TrackedItem` Protocol** in `sources/base.py` is the surface book + product sources both program against. Each source's `fetch(item)` asserts `isinstance(item, Book|Product)` before unpacking — defence in depth; scheduler's `item_kinds` intersection is the primary filter.
- **(Products) Per-kind + per-item exception isolation in scheduler.** `_run_source_locked` wraps each kind in its own try/except so a sibling kind's crash doesn't drop alert-pipeline calls for the kinds that succeeded. `_one` catches every `Exception` (not just `TimeoutError`/`SourceError`) and charges the single item via `_record_item_failure`. `asyncio.gather(..., return_exceptions=True)` for defence in depth.
- **(Products) `_run_refetch(cfg, scheduler, *, kind)`** in `api/books.py` is shared by both refetch endpoints. Sources whose `SourceConfig.item_kinds` doesn't contain the kind are skipped with `reason="kind_unsupported"`.
- **(Products) `product.image_url` is user-controllable** via `POST /api/products`. The image proxy at `GET /api/products/{id}/image` runs `_is_safe_image_url` (https + Amazon CDN host allowlist) before fetching to prevent SSRF.
- **(Products) Polymorphic `NotificationDelivery`** — exactly one of `alert_id` / `product_alert_id` is non-NULL per row, enforced by `ck_notificationdelivery_alert_xor_product` CHECK constraint (migration 0015 + `__table_args__` in `db/models.py`). Dispatcher writes via `_AlertModels.delivery_fk_attr`.

## Working agreements (do NOT re-decide)

- Tech stack: Python 3.12 / uv / FastAPI / SQLModel / Alembic / APScheduler / structlog · React 19 / Vite / TS / Tailwind v4 / shadcn/ui / Recharts · Playwright (for browser-required sources)
- Deployment: Docker (multi-stage) on NAS; Tailscale-only access; HTTP Basic optional but off by default
- Sources at MVP: WoB UK (inline `httpx`, books), Bookfinder (inline Playwright, books), Amazon UK (inline Playwright, books), **AmazonUKProductInlineSource (inline Playwright, products)** (added 2026-05-23). **Architecture revision 2026-05-14**: original design called for Go source-CLIs generated via `printing-press` + orchestrated through a `SubprocessSource` ABC; that path was abandoned for Phase 8.2 (AWS WAF `mp_verify` defeated every static-cookie / pure-Go-solver replay) and removed entirely. `SubprocessSource` deleted; no Go binaries; no printing-press dependency.
- Push at MVP: **ntfy only**. Telegram + Pushover deferred (no schema slots yet).
- Region: UK only at MVP; schema pluggable.
- Identity: ISBN-pinned for books; ASIN-pinned for products. `isbnlib` normalises ISBN-10 → ISBN-13; `sources/normalizers.py::to_asin` accepts bare ASINs and full Amazon URLs across TLDs (`/dp/`, `/gp/product/`, `/gp/aw/d/`, etc.).
- Item kinds: **books** + **products** (added 2026-05-23). Separate parallel tables (`Book` ↔ `Product`, `PriceObservation` ↔ `ProductObservation`, `Alert` ↔ `ProductAlert`, `BookSignalState` ↔ `ProductSignalState`); polymorphic `NotificationDelivery`. Adding a third kind requires the same pattern: new tables + new `_AlertModels` bundle + new `_ItemSchema` + new scheduler kind in `_run_kind_for_source`.
- Recommendation: hybrid (percentile default + per-book/per-product target override); `INSUFFICIENT_DATA` cold-start.
- Stats: `book_stats` + `product_stats` SQL views + `compute_book_stats()` / `compute_product_stats()` Python helpers (no materialised stats table). Both wrap a single `_compute_stats_impl` parameterised on `_ItemSchema`.
- Money: integer minor units (pence); never floats.
- Time: UTC in DB; render local in UI.

## Key files

- `README.md` — user-facing onboarding (Phase 13.2).
- `docs/superpowers/specs/2026-05-09-book-alerter-design.md` — design spec (authoritative for book behaviour).
- `docs/superpowers/plans/2026-05-09-book-alerter-implementation.md` — task-by-task implementation plan for the book MVP.
- `docs/superpowers/plans/2026-05-23-products-implementation.md` — task-by-task implementation plan for the products feature. **Complete** as of 2026-05-23 (commit `f388efa`).
- `docs/CHANGELOG.md` — append-only log of completed implementation tasks (commits, deviations).
- `RESUME.md` — this file (cursor + open decisions only).

### Products-specific key files (added 2026-05-23)

- `src/book_alerter/enums.py` — single home for shared StrEnums.
- `src/book_alerter/db/models.py` — Book + Product stacks side-by-side; polymorphic `NotificationDelivery` with `__table_args__` CHECK constraint.
- `src/book_alerter/db/views.py` — `BOOK_STATS_VIEW_SQL` + `PRODUCT_STATS_VIEW_SQL` (mirror views kept as separate strings, not generated).
- `src/book_alerter/db/migrations/versions/0014_product_tables.py` — adds the four product tables with ON DELETE CASCADE.
- `src/book_alerter/db/migrations/versions/0015_notif_delivery_polymorphic.py` — `alert_id` nullable + `product_alert_id` + CHECK.
- `src/book_alerter/db/migrations/versions/0016_product_stats_view.py` — installs `product_stats` view.
- `src/book_alerter/sources/base.py` — `Source.fetch(item: TrackedItem)`, `Source.item_kinds` ClassVar.
- `src/book_alerter/sources/amazon.py` — shared `_render_amazon_page` / `_fetch_offers_for_asin`; `AmazonUKInlineSource` (book) + `AmazonUKProductInlineSource` (product).
- `src/book_alerter/sources/normalizers.py` — `to_asin(raw)` + `amazon_uk_product_dp_url(asin)`.
- `src/book_alerter/stats.py` — `_ItemSchema`, `_compute_stats_impl`, `compute_book_stats` / `compute_product_stats`, `Stats` alias.
- `src/book_alerter/notifications/dispatcher.py` — `_AlertModels`, `BOOK_MODELS`, `PRODUCT_MODELS`, parameterised `AlertPipeline`.
- `src/book_alerter/notifications/base.py` — `AlertLike` + `ItemLike` protocols.
- `src/book_alerter/scheduler.py` — `alert_pipelines: dict[ItemKind, Callable]`, `_run_kind_for_source`, per-kind + per-item exception isolation, `_persist` parameterised on `ItemKind`.
- `src/book_alerter/api/products.py` — products CRUD + observations + refetch + stats + Keepa + image proxy with `_is_safe_image_url`.
- `src/book_alerter/api/books.py` — shared `_run_refetch(cfg, scheduler, *, kind)` lives here.
- `src/book_alerter/api/metadata.py` — `POST /api/metadata/asin-lookup`.
- `src/book_alerter/keepa.py` — `fetch_chart_png_for_asin(asin, ...)` is the ASIN-keyed entry point; `fetch_chart_png(isbn13, ...)` is the books-side wrapper.
- `src/book_alerter/covers.py` — `fetch_and_cache_url(path, url, ...)` is the generic helper; `fetch_and_cache(isbn13, url)` is the books wrapper.
- `web/src/pages/ProductsDashboard.tsx` + `ProductDetail.tsx`.
- `web/src/components/products/AddProductModal.tsx`.
- `web/src/hooks/useProducts.ts` + `useProduct.ts`.
- `tests/integration/test_scheduler_products.py` — pins the per-item exception isolation contract.
- `tests/integration/test_product_alert_pipeline.py` — pins polymorphic delivery FK routing.
- `tests/integration/test_notif_delivery_polymorphic.py` — pins the CHECK constraint matrix.
- `tests/integration/test_migrations.py` — migration upgrade/downgrade round-trip.
- `tests/integration/api/test_products_api.py` — full CRUD coverage.
- `tests/scenarios/scenario_07_product_lifecycle.py` — storyline-style e2e for the product alert lifecycle.

## Conventions for autonomous work

- One subagent dispatch = one plan task. After commit, update CHANGELOG → update RESUME.
- Commits are made by the subagent at task end.
- Stop on real ambiguity. Don't ad-lib design decisions; defer to spec + plan; if conflict, surface here.
- If a subagent fails twice on the same task, stop and document.
- Phase boundaries are natural checkpoints — leave a clean note in RESUME at each.
