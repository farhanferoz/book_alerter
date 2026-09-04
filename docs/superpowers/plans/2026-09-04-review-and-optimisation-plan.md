# Book Alerter — review findings and execution plan (2026-09-04)

Author: Farhan Feroz. Status: ratified for implementation, not yet started.

This document is the single input for the implementing agent. It records what the review
found (with the evidence that backs each finding), the decisions already taken, and a
dependency-ordered task list with acceptance checks. It is written so that each task can be
dispatched to a worker with no other context. Where a fact could not be verified during the
review it is labelled **UNVERIFIED** and a Wave 0 probe task exists to settle it before any
code that depends on it is written.

Plan shape: **skeleton-free, spec-complete plan** (no code transcribed here). Tasks carry
exact files, behaviour, and the verification command. Workers write the code.

---

## 0. Evidence base

All numbers below come from a read-only copy of the production database taken on
2026-09-04 (NAS `nasff235`, `/share/CACHEDEV1_DATA/Container/book_alerter/data/book_alerter.db`
plus its WAL), from the source tree at `/home/ff235/dev/book_alerter`, from the captured
fixtures under `/home/ff235/dev/book_alerter/tests/fixtures/`, and from live probes against
`http://100.115.46.9:8090` (all probe rows were hard-deleted afterwards).

| Fact | Value |
|---|---|
| Books tracked | 13 (12 active, 1 bought) |
| Products ever created before this review | 0 |
| `priceobservation` rows | 90,172 (77,835 = 86% are `is_duplicate_of` heartbeat rows) |
| Canonical (non-duplicate) rows | 12,337 (640–1,706 per book) |
| Database file | 50 MB after ~16 weeks |
| Product source runs since deploy | 394, every one with `books_attempted = 0` |
| Amazon (books) runs since 2026-08-18 | daily `partial` or `error` runs; 2026-09-04 12:00 run: 12 attempted, 0 succeeded |
| Books currently carrying `last_scrape_error` = Amazon bot challenge | 10 of 13 |
| Bot challenges logged 2026-09-01 | 32 |
| `GET /api/books` query cost on the prod copy | 13 × 0.19 s (view per book) + 0.12 s medians = **~2.6 s** |
| One `SELECT * FROM book_stats` (all books) | 0.20 s |
| Browser fingerprint the app presents | `Mozilla/5.0 (X11; Linux x86_64) … HeadlessChrome/151.0.7922.34 Safari/537.36`, `navigator.plugins.length = 0` |

Reproduction commands for the timing and fingerprint numbers are in §6.

---

## 1. Decisions already taken (do not re-open)

1. **Amazon Prime is a user setting, off by default.** `RecommendationConfig.amazon_prime: bool = False`, editable in Settings → Recommendation. When on, Amazon-fulfilled offers (seller `Amazon`, `Amazon Resale`, `Amazon Warehouse`; i.e. `seller_class() == amazon_fulfilled`) are treated as free delivery **at stats time**. Raw observations keep the scraped shipping value. Both modes must work.
2. **Products: reliability first, parity second.** Wave 4 makes products dependable; Wave 5 brings the products pages to feature parity with books. Both are in scope.
3. **Vectorisation is not the lever.** Per-item percentile work touches ≤1.7k rows (4 ms). Gains come from query shape, row-count reduction, and browser reuse. No numpy/pandas is to be introduced into the stats path.
4. **No new external dependencies for anti-bot work** (no `playwright-stealth`, no proxy services) unless a Wave 0 probe proves the in-house measures insufficient. Record the probe result before adding any.
5. **Money stays integer pence; time stays UTC in the DB.** Unchanged.
6. **Migrations are Tier 4** (property tests first, fresh-session review). Everything else is Tier 2 per wave.

---

## 2. Findings register

Severity: **S1** wrong data or user-visible failure · **S2** degraded behaviour · **S3** hygiene.

### Shipping (user-reported bug 1)

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| F1 | S1 | **The same Amazon offer flips between £0.00 and £2.80 across scrapes.** 337 of 2,952 distinct Amazon (item, seller, condition, price) offers carry more than one shipping value over their lifetime. Example: JJ_Books, used-VG, £20.00 on book 3 — 390 sightings recorded as 280 / 0 / NULL. World of Books Ltd on book 11 alternates 0 and 280 on the same day. | prod copy; query in §6.2 |
| F1a | — | The parser is **not** misreading a fixed page: running `_extract_shipping_minor` over both captured AOD fixtures returns the correct value for every row, including rows with a second `data-csa-c-delivery-price="fastest"` span. | `tests/fixtures/amazon/9780241638194-uk-offer-listing-{real,live-2026-05-23}.html` |
| F1b | — | The `raw` JSON column stores only the already-parsed fields, so the delivery text at flip time is never captured and the cause cannot be proven from history. | `src/book_alerter/scheduler.py:548` (`raw=c.model_dump()`) |
| F1c | UNVERIFIED | Leading hypothesis: Amazon's delivery promise for a not-logged-in visitor depends on the guessed delivery location and page variant; no delivery postcode is ever pinned. Wave 0 T0.2 settles this. | — |
| F2 | S1 | **World of Books shipping is hard-coded to 0.** WOB charges £0.99 economy delivery below £5 (free above). 255 `used_acceptable` rows average £4.46. | `src/book_alerter/sources/wob.py:245`; https://help.wob.com/support/solutions/articles/75000057344 |
| F3 | S1 | **Unknown shipping ranks as free.** `_persist` stores `total_minor = price + (shipping or 0)`; the `book_stats` view picks `MIN(total_minor)`. A NULL-shipping row therefore beats the identical offer with a known £2.80. 1,444 third-party Amazon rows and all 8,033 Keepa rows have NULL shipping. | `scheduler.py:486`; `db/views.py` `current_best` CTE |
| F4 | S1 | **No Prime handling.** 292 rows record Amazon's own buy box with £2.80 delivery (e.g. book 3 at £17.00). For a Prime household every one is wrong. | prod copy |
| F4a | UNVERIFIED | Amazon UK non-Prime free-delivery rule (search results: £35 general, £10 of books). Official help page returned HTTP 503 twice. Not load-bearing once F4 is a user toggle. | — |
| F5 | S2 | Bookfinder leaves 2,738 rows with `condition = unknown`. | prod copy |
| F6 | S3 | Amazon dp parser defaults the seller to `"Amazon"` when `#merchant-info` is absent, so an unattributed buy box is credited to Amazon. | `amazon.py:_extract_dp_seller` |

### Products (user-reported bug 2)

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| F7 | S1 | **Add-product is gated on a live Amazon render.** The Confirm button is disabled until `POST /api/metadata/asin-lookup` succeeds; that endpoint launches a fresh headless browser and returns 502 on a bot challenge. Books never hit this (OpenLibrary + Google Books). The probe succeeded twice today (18 s, 10 s) while the books scraper was fully blocked at noon — the failure is intermittent, not permanent. | `web/src/components/products/AddProductModal.tsx:104`; `src/book_alerter/api/metadata.py:88`; `src/book_alerter/metadata.py:477` |
| F8 | S1 | **Product alerts never surface.** `api/alerts.py`, `web/src/pages/Alerts.tsx` and `useAlerts.ts` contain no product path; `productalert` rows would be written but never shown. | grep, §6.4 |
| F9 | S2 | **Keepa backfill writes future-dated rows.** The probe product received a Keepa row stamped `2026-09-05T00:00Z` on 2026-09-04. `_DateCalib.__call__` rounds a linear fit with no clamp to today. | `src/book_alerter/keepa_chart.py:324`; probe output |
| F10 | S2 | **Keepa-only history yields an immediate signal.** 406 Keepa rows (all NULL shipping) arrived at creation; `days_of_history = 360`, rank 100, before a single live scrape. No UI caveat. | probe output |
| F11 | S2 | **No non-book fixture exists.** Every file in `tests/fixtures/amazon/` is ISBN-named. Variant (twister) pages, "currently unavailable", single-seller pages are untested. The probe's Echo Dot returned only the buy box and zero AOD rows — UNVERIFIED whether genuine (Amazon-brand device) or a parse gap. | `ls tests/fixtures/amazon` |
| F12 | S3 | `DELETE /api/products/{id}` archives by default; `?hard=true` deletes. ⚠️ CORRECTED 2026-09-04 by T4.6: the UI *did* have two buttons, but Delete omitted `?hard=true` — so it promised a cascading delete in its confirm dialog and silently archived instead. | `src/book_alerter/api/products.py:333` |

### Bot detection (root driver of F1, F7, and the Amazon run failures)

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| F13 | S1 | **The browser announces itself as `HeadlessChrome` with zero plugins.** ⚠️ PARTLY CORRECTED by T0.3 (2026-09-04): `channel="chromium"` fixes the zero-plugins half but NOT the UA half — see the Wave 0 results file. `chromium.launch(headless=True)` uses Playwright's headless shell; the real-Chrome new-headless mode requires `channel="chromium"`. Verified locally with the app's exact launch args. | §6.3; https://playwright.dev/docs/browsers |
| F14 | S1 | **A brand-new browser per item fetch, no cookie persistence.** ⚠️ T0.2 (2026-09-04) proved this is also the **cause of F1** — a cookieless visitor is served Amazon's first-order free-delivery promo, which the parser records as £0.00 shipping. `_fetch_offers_for_asin` (Amazon), `BookfinderInlineSource._render`, and both metadata lookups each run `async_playwright()` → `launch()` → `close()`. Amazon sees a cold automated visitor ~120 times a day. | `amazon.py:225-268`; `bookfinder.py:127-156`; `metadata.py:369,484` |
| F15 | S2 | **Per-item bot failures never trigger source backoff.** `_apply_backoff` counts whole-run errors; a run with 12/12 item failures is `status=error` but the next tick fires anyway. No per-item retry. | `scheduler.py:384-459, 583-597` |
| F16 | S2 | **All four sources fire at the same cron minute**, each launching its own Chromium on a 4-core NAS whose load average was 4.4 during the probe. | `data/config.yaml` schedules; NAS `uptime` |

### Performance

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| F17 | S1 | **Dashboard does 12× the necessary work.** `list_books` calls `compute_book_stats` per book; each call runs `SELECT … FROM book_stats WHERE book_id = ?`, and SQLite materialises every CTE over the whole table per call (EXPLAIN shows `MATERIALIZE … SCAN priceobservation` with no predicate push-down). 2.47 s for 13 books vs 0.20 s for one all-books query. `list_products` has the identical shape. | §6.1; `api/books.py:324-350`; `api/products.py:208-235` |
| F18 | S1 | **86% of the observation table is heartbeat rows** (`is_duplicate_of IS NOT NULL`), scanned by `buyable_last_seen` on every view evaluation, growing ~5,600 rows/week for 13 books with no retention. | prod copy |
| F19 | S3 | `_last_run_for` in `api/sources.py:137` is one query per source (4 sources — cosmetic). | source |
| F20 | S3 | `source_seller_global_shipping_medians` scans the full table (0.12 s) per dashboard render; a per-cycle snapshot exists only in the dispatcher path. | `stats.py:274-310` |

### Hygiene / observability

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| F21 | S3 | The working copy at `/home/ff235/dev/book_alerter` has no `.git` directory; history lives in another clone. The implementing agent must work in a clone that has one. | `ls -la` |
| F22 | S3 | `uv run` from this directory created `/home/ff235/dev/.venv` (workspace-root `pyproject.toml`). Not a defect, but the implementer must not commit anything outside the repo. | observed |
| F23 | S3 | Container `json-file` log became unreadable for `docker logs --since` after the NAS reboot on 2026-09-03 (`invalid character '\x00'`); `--tail` still works. | NAS |
| F24 | S3 | Keepa backfill runs only at item creation; book Keepa rows stop at 2026-06-22. | prod copy; grep `scheduler.py` |
| F25 | S3 | `Signal` in `stats.py` is still a `Literal`; project rule says `StrEnum`. Carried from RESUME. | source |

---

## 3. Target architecture changes (what the waves build toward)

1. **One browser session per source run, persistent profile per source.** A `BrowserSession` context manager (new module `src/book_alerter/sources/browser.py`) owns Playwright + one persistent context (`user_data_dir = data/browser-profiles/<source>/`), launched with `channel="chromium"` (new headless), a UA with `Headless` removed, `locale="en-GB"`, `timezone_id="Europe/London"`, `viewport` 1366×768. `Source.prepare()` / `Source.cleanup()` hooks (the deferred RESUME item) open/close it once per scheduler run; `fetch()` receives pages from it. Metadata lookups borrow the same helper. Optional delivery-postcode pinning applied once per context (after T0.2).
2. **Current-best selection moves from SQL to Python.** The view is reduced to `*_live_offers` (one row per live offer in each source's freshest scrape, freshness-gated as today). `_compute_stats_impl` ranks candidates by **effective total** = price + (observed shipping, else cascade estimate, with the Prime rule applied). This fixes F3 and F4 in one place and lets the list endpoints load every item's candidates in one query.
3. **Heartbeats become a column, not rows.** `last_seen_at` on the canonical observation row; `_persist` updates it instead of inserting a duplicate. One-off migration folds existing duplicates in and deletes them (~78k rows). `is_duplicate_of` is dropped.
4. **Products get the same add-flow guarantees as books**: creation never depends on a live Amazon render; metadata is filled in by a retrying background job or by the first successful scrape.
5. **Products pages reuse the books components** through an `ItemLike` front-end type, mirroring the backend `ItemLike` protocol.

---

## 4. Execution waves

Conventions for every task: all edits inside `/home/ff235/dev/book_alerter/` (a clone **with** `.git`); tests added in the same task; `uv run pytest -q` and `uv run ruff check src tests` green before commit; front-end tasks also `cd web && npx tsc --noEmit && npx eslint . && npm run build`; migrations followed by `uv run alembic upgrade head` on `data/book_alerter.db`; no `git add -A`; no AI/tool provenance in commit messages or comments. Each task = one commit. Land findings/fixtures to disk as soon as they exist. **Every task also obeys §8 (organisation and cleanup), and `git status --short` must show nothing unexpected before the commit.**

### Wave 0 — live probes and fixtures (no production code changes)

Purpose: turn the UNVERIFIED items into facts and capture the fixtures Waves 1, 2 and 4 test against. Runs from the workstation (Playwright installed locally: `uv run playwright install chromium`), never against the NAS container.

- [x] **T0.1 Fixture-capture script.** ✅ DONE 2026-09-04 (commit `7d3a3c4`) — consolidated: the new script **replaces** `capture_amazon_dp.py`, `capture_amazon_offer_listing.py` and `capture_bookfinder.py` (all three deleted), reuses `_render_amazon_page` and the sources' own URL helpers, and uses `StrEnum` for `--source`/`--kind`. Also captured fixture (e) for T0.4. Original spec: New `scripts/capture_amazon_fixture.py` (argparse: `--asin`, `--kind dp|aod|both`, `--out tests/fixtures/amazon/products/`, `--postcode` optional). Uses the app's own `_render_amazon_page` so captures match what the scraper sees. Writes `<asin>-uk-<kind>-<YYYY-MM-DD>[-pc<postcode>].html` and a sidecar `.json` with the per-row `data-csa-c-delivery-price` values and `.aod-delivery-promise` text. Non-goal: no parser changes.
Verify: script runs for `B09B96TG33`; files exist; `uv run ruff check scripts`.

- [x] **T0.2 Delivery-location pinning probe.** ✅ DONE 2026-09-04 — **Q1 SETTLED, F1c REJECTED.** The flip is not location-dependent: Amazon serves a *conditional* promotional promise (`FREE delivery … on your first order to UK or Ireland`, `data-csa-c-delivery-price="FREE"`) to what it thinks is a first-time visitor, and the parser stores it as `shipping_minor = 0`. Verified with the app's own `parse_offer_listing` over a live capture: **8 of 9 rows recorded as free**, the only correct row being the one with an unconditional promise. **F1 and F14 are the same bug.** Postcode pinning itself did not work (no CSRF token; glow widget absent) and made no difference to the promises. Full evidence in `2026-09-04-wave0-probe-results.md`. Original spec: Determine whether a delivery postcode can be set for a logged-out session in headless Chromium and whether it changes the AOD delivery promise. Candidate mechanism (UNVERIFIED, must be tested, not assumed): the "glow" location widget's `POST /gp/delivery/ajax/address-change.html` with `locationType=LOCATION_INPUT`, `zipCode=<postcode>`, `storeContext=generic`, `deviceType=web`, `pageType=Detail`, `actionSource=glow`, issued via `page.request.post` inside the context, followed by a reload; success = `#glow-ingress-line2` shows the postcode. Capture book 3's AOD (`969353137X`) three times over ≥2 hours with and without pinning; diff the JJ_Books row. Record the result in `docs/superpowers/plans/2026-09-04-wave0-probe-results.md` (new file, table per capture). Non-goal: no production wiring.
Verify: results file exists with ≥3 captures each way and a stated conclusion for F1c.

- [x] **T0.3 Browser-build probe.** ✅ DONE 2026-09-04 — results in `2026-09-04-wave0-probe-results.md`. Answer: the base image **already contains** the full Chromium build (`/ms-playwright/chromium-1217/chrome-linux64/chrome`), so **T1.1 must not add `playwright install chromium`**. Measured correction to F13: `channel="chromium"` does NOT clear `HeadlessChrome` from the UA (it takes `navigator.plugins` 0→5 and makes `window.chrome` real); the explicit `user_agent=` override T1.1 specifies is therefore load-bearing, not optional. Original spec: Confirm the Docker base image (`mcr.microsoft.com/playwright/python:v1.59.0-noble`) contains the full Chromium build required by `channel="chromium"` (run `docker run --rm <image> ls /ms-playwright`), and print `navigator.userAgent` under `channel="chromium"` with the app's launch args. Record in the Wave 0 results file. If the full build is absent, the Dockerfile task in T1.1 must add `playwright install chromium`.

- [ ] **T0.4 Product-page fixtures.** Capture with T0.1: (a) a multi-seller non-Amazon-brand product with AOD rows, (b) a product with size/colour variants (`#twister` present), (c) a "currently unavailable" product, (d) a product with used offers, (e) the Echo Dot `B09B96TG33` (to settle F11). Store under `tests/fixtures/amazon/products/`. Record which of (a)–(e) exhibit which markers in the results file.

- [ ] **T0.5 Bookfinder condition strings.** From the prod copy, list the distinct raw condition texts behind `condition = unknown` (they are not stored — so instead capture two Bookfinder pages for books 2 and 5 with T0.1-style script extended with `--source bookfinder`) and record the unmapped grade strings.

### Wave 1 — browser identity and lifecycle (root cause of the bot blocks)

Depends on T0.2, T0.3.

- [x] **T1.1 `BrowserSession` + `Source.prepare()/cleanup()`.** ✅ DONE 2026-09-04 (`016862c`, `9ed78de`, `cbde587`) — root-verified: `grep -rn async_playwright src/` hits **only** `sources/browser.py`; real fingerprint through the class = no `Headless` in UA, plugins 5, `window.chrome` object, profile dir 0700. **The live Amazon canary passed for the first time** (8/8 against real amazon.co.uk). No Dockerfile change (per T0.3). Docker build + e2e green. ⚠️ Follow-up raised: shared profile dir between metadata lookups and the scheduled product source — see D24. Original spec: New `src/book_alerter/sources/browser.py` with `class BrowserSession` (async context manager): `launch_persistent_context(user_data_dir, channel="chromium", headless=True, args=[...existing...], locale="en-GB", timezone_id="Europe/London", viewport=..., user_agent=<derived>)`. UA derivation: launch, read `browser.version`, build `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/<version> Safari/537.36` (never the literal `HeadlessChrome`). Persistent profile dir: `data/browser-profiles/<source_name>/`, created with mode 700; add to `.dockerignore`/`.gitignore`. `Source` ABC (`sources/base.py`) gains `async def prepare(self) -> None` / `async def cleanup(self) -> None` (default no-op). Scheduler `_run_source_locked` calls `prepare()` before iterating kinds and `cleanup()` in `finally`. `AmazonUKInlineSource`, `AmazonUKProductInlineSource`, `BookfinderInlineSource` implement them by opening one `BrowserSession`; `_fetch_offers_for_asin` and `_render` take a `context` argument instead of launching. `metadata.fetch_amazon_uk_product_metadata` and the Amazon metadata fallback use a short-lived `BrowserSession` with the `amazon_uk_product` profile. Delete the three inline `async_playwright()` blocks. Dockerfile: add `RUN playwright install chromium` only if T0.3 found the full build missing.
Tests: unit test for UA derivation (parametrised on version strings); integration tests in `tests/integration/sources/test_amazon*.py` / `test_bookfinder.py` updated to inject a fake context factory (existing `_render` monkeypatch pattern); scheduler test asserting `prepare`/`cleanup` are called exactly once per run including on exception.
Verify: `uv run pytest -q`; the live canaries `AMAZON_LIVE=1 uv run pytest tests/integration/sources/test_amazon.py -q` pass from the workstation; `docker build -t book_alerter:dev . && uv run pytest -m e2e tests/e2e -q`.

- [ ] ~~**T1.2 Delivery postcode pinning.**~~ ❌ **DROPPED 2026-09-04 by the task's own gate** ("Only if T0.2 concluded the mechanism works") — T0.2 found the mechanism does not work for a logged-out headless session *and* falsified the hypothesis that motivated it. Not deleted, so it can be revisited if stable delivery *dates* are ever needed. Do not implement. Original spec: Only if T0.2 concluded the mechanism works. `SourceConfig.delivery_postcode: str | None = None` (validated `^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$` after upper-casing); `BrowserSession.pin_delivery_location(postcode)` runs once per context after launch when the profile has no pin cookie yet. Settings → Sources card gets a text field. Document in `.env.example` / README.
Verify: unit test for the validator; integration test with a mocked `page.request.post`.

- [ ] **T1.3 Per-item challenge handling and backoff.** In `_run_kind_for_source._one`: on a `SourceError` whose message contains the bot-challenge text, sleep `random.uniform(20, 40)` s and retry **once** with a fresh page in the same context. Count item-level challenge failures; if ≥50% of attempted items in a run were challenged, treat the run as a consecutive error for `_apply_backoff` (today only run-level exceptions count). Extend `SourceRun` with `items_challenged: int` (migration, Tier 4).
Verify: scheduler integration test with a stub source raising the challenge error on first call.

- [ ] **T1.4 Browser concurrency cap and staggering.** New `SchedulerConfig.max_concurrent_browsers: int = 2` (top-level `scheduler:` section in `Config`, default preserves current behaviour for `≤2` sources). A process-wide `asyncio.Semaphore` wraps `BrowserSession.__aenter__`. Default schedules in `_default_sources()` staggered: wob `0 */6`, bookfinder `15 */6`, amazon `30 */6`, amazon_uk_product `45 */6` (existing user config files are untouched; README notes the recommendation).
Verify: config round-trip test; scheduler test that two sources with the cap at 1 do not overlap `prepare()`.

- [ ] **T1.5 Diagnostic capture.** `ObservationCandidate` gains `delivery_text: str | None = None` (Amazon: the `.aod-delivery-promise` text or dp delivery block text; Bookfinder: the shipping label text). Persisted inside `raw` (no schema change). On a bot challenge or an unrecognised layout, `_render_amazon_page` writes the HTML to `data/debug/<source>/<utc-timestamp>.html`, keeping the newest 20 files per source. Directory is gitignored/dockerignored and inside the mounted `data/` volume.
Verify: unit tests for the rotation helper; parser tests assert `delivery_text` is populated on the captured fixtures.

### Wave 2 — shipping correctness

Depends on Wave 1 only for T2.5 (the `delivery_text` evidence); T2.1–T2.4 can start immediately after Wave 3's T3.1 lands (they share the stats layer).

- [x] **T2.1 World of Books delivery rule.** ✅ DONE 2026-09-04 — `_delivery_minor_for()` in `wob.py`; tested at 1/499/500/501/10000 pence, and the regression test was verified to go red when the hard-coded zero is reintroduced. Original spec: `wob.py`: module constants `_WOB_FREE_DELIVERY_THRESHOLD_MINOR = 500`, `_WOB_ECONOMY_DELIVERY_MINOR = 99`; `shipping_minor = 0 if price_minor >= threshold else 99`. Comment cites the WOB help article. Update cassette-based tests' expectations. Non-goal: no basket-level logic (the tracker prices single items).
Verify: unit test at 499 / 500 / 501 pence.

- [ ] **T2.2 Prime toggle (backend).** `RecommendationConfig.amazon_prime: bool = False`. In `stats.py`, a single function `effective_shipping(source, seller, shipping_minor, *, prime: bool, cascade) -> tuple[int, bool]` returning `(pence, is_estimate)`: if `prime` and `seller_class(seller) == amazon_fulfilled` and source in `{"amazon", "amazon_uk_product"}` → `(0, False)`; elif observed → `(observed, False)`; else → `(cascade(...), True)`. Every place that adds shipping (window totals, current-best ranking after T3.1, alert message) goes through it. `BookStats` gains `shipping_is_estimate: bool` and `prime_applied: bool`; `BookStatsOut` mirrors them.
Verify: unit tests for all four branches; alert-pipeline scenario `tests/scenarios/scenario_08_prime_toggle.py` (mirror scenario 01: same observations, toggle flips the signal).

- [ ] **T2.3 Prime toggle (UI).** Settings → Recommendation: a labelled `Switch` "I have Amazon Prime (treat Amazon-fulfilled delivery as free)" using the existing PATCH pattern in `web/src/pages/settings/Recommendation.tsx` (`model_dump(exclude_unset=True)` path already accepts new keys). Offer rows in `SourceBreakdown`, `SnapshotCard`, `PercentileChart` show "Prime" when `prime_applied` and "est." when `shipping_is_estimate`. Regenerate `web/src/api/schema.ts` (`npm run gen:api` against a running backend).
Verify: `npx tsc --noEmit && npx eslint . && npm run build`; manual check in browser against a prod-DB copy (document in commit message body).

- [ ] **T2.4 Unknown shipping never ranks as free.** Delivered by T3.1's Python current-best selection (rank on effective total). Add a regression test: two live offers, same price, one NULL shipping and one 280 → the 280 one wins only if the cascade estimate for the NULL row exceeds 280; the response carries `shipping_is_estimate = true` when the NULL row wins.

- [ ] **T2.5 Amazon flip fix — conditional delivery promise.** ⚠️ **T0.2 has decided this: take the second branch below; it is now the primary fix, not a fallback.** The marker observed live is `on your first order` (full text `FREE delivery <dates> on your first order to UK or Ireland`). This task now hard-depends on **T1.5** (which captures `delivery_text`). Add a `raw.delivery_text`-driven rule: when the promise text contains a conditional ("on orders over", "on your first order", "with Prime") treat it as **not free** and fall back to the cascade estimate with `shipping_is_estimate = true`. Decide from evidence; write the rule that the captures support.
Verify: parser unit tests on the T0.2 captures.

- [ ] **T2.6 Bookfinder condition mapping.** Extend `condition_normalizers.condition_from_grade_text` with the strings T0.5 found (e.g. "Fine", "Near Fine", "As New" → `used_vg`; "Fair" → `used_acceptable`; "Ex-library" keeps its grade). Fixture-based tests.

- [ ] **T2.7 dp seller attribution.** `_extract_dp_seller` returns `None` (not `"Amazon"`) when `#merchant-info` is absent; downstream treats `None` as third-party for the cascade. Test on the `9780747532699-uk-dp.html` fixture.

### Wave 3 — performance and storage

- [x] **T3.1 Stats restructure: candidates in SQL, selection in Python.** ✅ DONE 2026-09-04 (`0bd1f09` migration, `6b07e64` property tests + bench, `e68f591` stats/api) — **3 SELECTs total regardless of batch size**, proven by a real `before_cursor_execute` counter at 5 and 20 items. Root-verified independently: the new views + documented tie-break reproduce the old `current_best` for **all 13 production books, 0 mismatches**; migration round-trips clean. Benchmark **~2.0 s → 0.43–0.45 s** (root re-ran it); through the API, smoke shows `GET /api/books` **2101 ms → 453 ms**. ⚠️ **Misses the ≤0.35 s gate — deliberately carried to T3.2, see D23.** Original spec: Tier 4 (migration).
- Migration `0020_live_offers_views`: replace `book_stats` / `product_stats` with `book_live_offers` / `product_live_offers` = today's `latest_per_offer` CTE with `rn = 1` (one row per live offer, freshness-gated ≤1 day as now), plus `book_history_summary` / `product_history_summary` (today's `agg_history` + `polled`). DDL stays in `db/views.py`.
- `stats.py`: `compute_stats_for_items(item_ids, session, *, schema, cfg, prime, medians) -> dict[int, BookStats]` loads, in **three queries total**: live offers for all requested items, window observations for all items (`observed_at >= now - max_window`), history summaries. Groups in Python, applies `effective_shipping`, picks current best by effective total with the existing deterministic tie-break (source, condition, seller). `compute_book_stats` / `compute_product_stats` become thin wrappers over a single-item call (keep signatures so the dispatcher and detail endpoints are untouched).
- `list_books` / `list_products` call the batch function once.
- Property tests first (`hypothesis`, already a dependency: `.hypothesis/` exists): for random observation sets, the Python selection equals the old view's `current_best` when `prime = False` and all shipping is known.
Verify: `uv run pytest -q`; benchmark script `scripts/bench_stats.py` (new; takes a DB path; prints wall time of `list_books`-equivalent work) run against a prod copy: **target ≤ 0.35 s** for 13 books (baseline 2.6 s). Record before/after numbers in the commit body.

- [ ] **T3.2 Heartbeat compaction.** Tier 4 (migration `0021_observation_last_seen`).
- Add `last_seen_at DATETIME NOT NULL DEFAULT observed_at` to `priceobservation` and `productobservation`; backfill `last_seen_at = MAX(observed_at)` over each duplicate group; `DELETE` rows with `is_duplicate_of IS NOT NULL`; drop `is_duplicate_of` and its FK; new index `(book_id, source, last_seen_at)` / product mirror; drop `ix_priceobservation_book_id` (covered by the composite). `VACUUM` is **not** run by the migration (document `sqlite3 data/book_alerter.db 'VACUUM'` as a manual step; the backup job runs first).
- `_persist`: when a prior canonical row matches the full key, `UPDATE last_seen_at` instead of inserting. Views from T3.1 use `last_seen_at` directly (the `buyable_last_seen` GROUP BY disappears).
- Shipping medians: previously weighted by heartbeat rows "on purpose"; after compaction weight canonical rows equally and lower `min_global_median_observations` default from 10 to 5. Note the change in `config.py` comment.
- Down-migration re-creates the columns (data loss of heartbeats is accepted and documented).
Verify: migration round-trip test in `tests/integration/test_migrations.py`; `PRAGMA foreign_key_check` clean; row count on the prod copy drops from 90,172 to ~12,337; `bench_stats.py` re-run.

- [x] **T3.3 Sources API single query.** ✅ DONE 2026-09-04 (`065e1d8`). Original spec: Replace `_last_run_for` loop with one `SELECT … ROW_NUMBER() OVER (PARTITION BY source ORDER BY started_at DESC)` query. Trivial; fold into T3.1's commit if convenient.

- [ ] **T3.4 Medians snapshot cache.** `source_seller_global_shipping_medians` cached on `app.state` with a 60 s TTL, invalidated by the scheduler after each `_persist` batch. Removes the 0.12 s scan from every dashboard render.

### Wave 4 — products reliability

Depends on Wave 1 (browser session) for T4.1's background refresh.

- [ ] **T4.1 Add-product never blocks on Amazon.** Backend: `ProductCreate.title` becomes optional; when absent the row is created with `title = f"Amazon product {asin}"` and `metadata_status = "pending"` (new column, migration `0022_product_metadata_status`, values `pending|ok|failed` as a `StrEnum` in `enums.py`). A background job (`metadata_refresh` in `scheduler.py`, every 30 min, plus immediately after create) retries `fetch_amazon_uk_product_metadata` through `BrowserSession` with exponential backoff up to 6 attempts, then marks `failed`. The product scraper also fills title/image on its first successful dp parse (`parse_dp` already sees `#productTitle`; extend `ObservationCandidate` with optional `item_title` / `item_image_url` and let `_persist` update a pending product). Frontend: Confirm enabled as soon as `looksLikeAsinOrUrl` passes; preview shows the lookup result when it arrives, otherwise "Details will be filled in after the first scrape"; a pending badge on the product row.
Verify: API test creating a product with `{asin_or_url}` only; scheduler test for the refresh job; FE build.

- [ ] **T4.2 Product page parser hardening.** Using T0.4 fixtures: variant pages — read the selected variant's buy box only and record `variant_asin` if the URL ASIN differs; "currently unavailable" — return `[]` without raising (page markers present); single-seller pages — dp-only path must not raise on an AOD page with zero rows (already tolerated; add the fixture test); used offers honour `track_used`. Add `tests/integration/sources/test_amazon_product_fixtures.py`.
Verify: every T0.4 fixture has a passing test naming its expected offers.

- [x] **T4.3 Keepa date clamp and future-row guard.** ✅ DONE 2026-09-04 (commit `75145c7`) — clamp in `_DateCalib.__call__` plus a drop-and-count guard in `keepa_backfill`; 16 keepa tests green. Original spec: `keepa_chart._DateCalib.__call__` clamps to `today`; `keepa_backfill` drops any extracted date `> today` and logs a count. Test with a frozen clock (`freeze_time` is already used in scenarios).

- [ ] **T4.4 Keepa-only history caveat.** `BookStats` gains `live_observation_count: int` (non-Keepa canonical rows in the window). `compute_signal` still fires (decision: Keepa history is valid history), but the FE shows "Based on Keepa history only — no live offer yet" on the signal pill when `live_observation_count == 0`. Alert messages carry the same suffix.

- [x] **T4.5 Product alerts surface.** ✅ DONE 2026-09-04 (commit `f75c7a1`) — union feed over both tables driven by the existing `_AlertModels` registry (no per-kind duplication); local `AlertKind` Literal replaced by the `StrEnum`; alerts addressed by `(item_kind, id)` because ids collide across tables (regression test seeds colliding ids); rows carry their own title so the client-side `useBooks` lookup is gone from both page and sidebar. 17 alerts tests green; FE tsc/eslint/build green. Original spec: `api/alerts.py` returns a union of `Alert` and `ProductAlert` rows (`kind`, `item_kind`, `item_id`, `title`, message, fired_at, dismissed_at), with dismiss / dismiss-all covering both tables; `Alerts.tsx` links to `/products/:id` for product rows. Notification test endpoint unchanged.
Verify: API tests for mixed listing and dismiss; scenario 07 extended to assert the alert appears in `GET /api/alerts`.

- [x] **T4.6 Archive vs delete in the products UI.** ✅ DONE 2026-09-04 — ⚠️ **F12 was partly inaccurate**: the products UI *does* have both buttons. The real defect was worse — Delete called the endpoint **without `?hard=true`**, so it soft-archived while its confirm dialog promised a cascading permanent delete. Fixed to send `?hard=true`; both dialogs now state the actual consequence. The **books** ActionBar was already correct (it passes `?hard=true` and documents why), so no change was needed there. Original spec: `ProductDetail.tsx` action bar: "Archive" (default) and "Delete permanently" (confirm dialog → `?hard=true`). Mirror on the books detail page if it has the same gap (check `ActionBar.tsx`; fix both or note why not).

### Wave 5 — products feature parity (phase 2)

Depends on Wave 3 (batch stats) and T4.5.

- [x] **T5.1 Shared item types and hooks.** ✅ DONE 2026-09-04 (`23b1f8c`) — `web/src/lib/item.ts` + `web/src/hooks/useItems.ts`; `kind` derived from the generated `ItemKind` schema, not hand-written. Deliberate deviation: does **not** wrap `useBooks`/`useProducts` conditionally (that trips `react-hooks/rules-of-hooks`, and calling both unconditionally would double-fetch); each makes one `useQuery` with `kind` as data, matching existing query-key shapes. Original spec: `web/src/lib/item.ts`: `type ItemLike = { kind: "book" | "product"; id; title; imageUrl; stats; signal; … }` built from `BookOut` / `ProductOut`; `useItems(kind)`, `useItem(kind, id)` wrap the existing hooks. No behaviour change for books.

- [ ] **T5.2 Products dashboard parity.** Reuse `BookTable`, `columns.tsx`, `MiniBars`, `SignalPill`, `BookFilters`, `BookRowMenu` via `ItemLike` (rename to `ItemTable` etc. only if the rename is mechanical; otherwise wrap). Products dashboard gains signal pill, mini bars, current-best with source/seller, filters, row menu (refetch / archive / mute).

- [ ] **T5.3 Product detail parity.** `HeaderCard`, `SnapshotCard`, `SignalCard`, `PercentileChart`, `HistoryChart`, `SourceBreakdown`, `KeepaChart`, `SettingsPanel`, `ActionBar` accept `ItemLike` + the `/api/products/...` endpoints. Backend: confirm `GET /api/products/{id}/observations` and `/stats` already expose everything the book components read (`BookStatsOut` is shared — yes; observations page shape — verify `ProductObservationsPage` matches `ObservationsPage`, align if not).

- [ ] **T5.4 Products settings parity.** Per-product alert-kind disable, mute-until, percentile threshold/window, target price, notes — all already in `ProductPatch`; expose in the shared `SettingsPanel`.

- [ ] **T5.5 Products in global surfaces.** App shell nav badge counts include product alerts; Settings → Sources shows per-kind attempted/succeeded for `amazon_uk_product`.
Verify for the wave: FE pipeline clean; Playwright e2e scenario (new `tests/e2e/test_products_ui.py` using the existing Docker boot smoke pattern) that adds a product, refetches, and asserts the chart and signal pill render.

### Wave 6 — observability and hygiene

- [ ] **T6.1 Scrape-health summary.** `GET /api/sources` adds `last_24h: {attempted, succeeded, challenged}` per source (one query over `SourceRun`); the dashboard shows a banner "Amazon blocked N of M items in the last run" when `challenged > 0`.

- [ ] **T6.2 `Signal` to `StrEnum`** (F25). Mechanical; ruff `PLR2004` clean.

- [ ] **T6.3 Periodic Keepa refresh** (F24). Optional weekly job re-running the backfill for active items (dedup by date already exists). Decide at implementation time whether Keepa's PNG endpoint tolerates 13+ requests/week; default off.

- [ ] **T6.4 Docs.** README: Prime toggle, postcode pinning, browser profiles directory, staggered schedules, `VACUUM` after T3.2, log-file note (F23: recommend `logging.options.mode: non-blocking` only if measured to help; otherwise document `docker logs --tail`).

- [ ] **T6.5 Data-directory janitor (runtime clutter).** 🟡 **MOSTLY DONE 2026-09-04** (commit `67f09ed`): `src/book_alerter/janitor.py` with all five sweeps, `JanitorConfig` (every limit a config value), `janitor_last_run_at` on `/api/health`, `janitor_tick()` entry point, 18 tests green. **REMAINING: register the APScheduler job** — one call to `janitor_tick()` in `scheduler.py`, deferred because `scheduler.py` is being restructured by T1.1 concurrently. Original spec: One APScheduler job `janitor` (daily 04:00 UTC, after the backup job) in a new module `src/book_alerter/janitor.py`, config section `janitor:` with `enabled: bool = True` and the limits below. It enforces, inside the mounted `data/` volume only:
- `browser-profiles/<source>/`: cap at 200 MB per profile; when exceeded, delete the profile's `Default/Cache`, `Default/Code Cache`, `Default/GPUCache` first, then the whole profile (cookies re-warm on the next run). Log sizes before/after.
- `debug/<source>/`: keep newest 20 files (T1.5's rotation) **and** delete anything older than 14 days.
- `keepa-cache/`: delete PNGs whose item no longer exists (ASIN not in `book`/`product`) or older than 30 days (the 24 h TTL already makes them stale).
- `covers/`: delete files whose ISBN/ASIN no longer exists.
- `backups/`: retention stays `retain` files, but **new backups are gzip-compressed** (`book_alerter_<ts>.db.gz`, written via `sqlite3.Connection.backup()` into a temp file then `gzip` in the same job; the NAS holds 246 MB of uncompressed weekly copies today at 35 MB each). Restore instructions in README. Existing uncompressed backups are compressed in place on the first janitor run.
- Report: one structured log line per category `{category, files_removed, bytes_freed}` and a `GET /api/health` field `janitor_last_run_at`.
Tests: unit tests with a `tmp_path` data dir for every rule; the item-existence rules use `engine_with_view`.
Verify: `uv run pytest -q`; run the job once against a copy of the NAS data dir and record bytes freed in the commit body.

- [ ] **T6.6 Repository tidy-up.** Add `.hypothesis/` and `data/browser-profiles/`, `data/debug/` to `.gitignore` (the first is only in `.dockerignore` today); remove the dead `cli_bins/**` ignore entries (the Go CLI path was abandoned in May); delete `data/amazon_dbg.png` from any clone that has it (it is git-ignored but is clutter). Move `scripts/capture_amazon_fixture.py` (T0.1) and `scripts/bench_stats.py` (T3.1) under a `scripts/` README listing every script and when to run it. Confirm `tests/fixtures/amazon/` follows the layout `<asin>-uk-<kind>-<date>.html` for books and `products/<asin>-uk-<kind>-<date>.html` for products; rename the two legacy `-real` / `-uk-dp` files to the dated form and update the tests that load them.
Verify: `git status --short` empty after commit; `uv run pytest -q`.

- [x] **T6.7 Fast end-to-end validation harness.** ✅ DONE 2026-09-04 (commit `9dcb8a5`) — 12 checks, **7.78 s** against the production copy, input DB md5 unchanged. Verified by poisoning a copy with one future-dated, total-inconsistent row: both invariants went **FAIL** and the run exited 1. Baseline captured for T3.1: `GET /api/books` = **2101 ms**. Original spec: (Added 2026-09-04 during execution, on the user's instruction that validation be real and take under a minute — see RESUME decision D22.) New `scripts/smoke_check.py`: takes `--db <path>` (a **copy** of a production DB; never the live file), copies it to a temp dir, runs `alembic upgrade head` against the copy, boots the real FastAPI app in-process against it (`TestClient`, no network, no browser), and exercises the real endpoints — `/api/health`, `/api/books`, `/api/products`, `/api/alerts`, `/api/sources`, plus one book detail, its `/stats` and `/observations`. For each it asserts HTTP 200, a non-degenerate response shape (books list non-empty on a prod copy; every item carries the keys the dashboard reads), and prints the wall time. It also asserts cross-cutting data invariants: no observation dated in the future, every `total_minor` equals price + shipping under the active shipping rule, every `current_best` referenced offer exists and is live, and `signal` is a member of the `Signal` enum. Exits non-zero with a per-check report on any failure. Whole run must finish in **under 60 s**; print a total. It asserts structure, health and timing only — never the pricing behaviour Wave 2 is changing.
Verify: `uv run python scripts/smoke_check.py --db <scratch>/proddb/book_alerter.db` exits 0 in under 60 s; `uv run ruff check scripts`.

---

## 8. Organisation and cleanup standards (binding on every task)

The user's requirement: no mess, no clutter, no garbage — in the repository, in the deliverables, and inside the running container.

**Repository**
- New code goes in the module that already owns the concern (`sources/`, `stats.py`, `scheduler.py`, `api/<resource>.py`, `web/src/components/<domain>/`). A new module needs a one-line justification in the commit body naming what was searched for and not found.
- Temporary scripts, probe outputs, HTML dumps and benchmark results never enter the repo. They live in the session scratchpad; only the fixtures named in Wave 0 and the two scripts named in T6.6 are promoted, with descriptive names and a `scripts/README.md` entry.
- Fixtures are dated, ASIN-named, and stored under the layout in T6.6. No fixture without a test that loads it; no test loading a fixture that is not committed.
- Every task ends with `git status --short` showing only the intended files. Untracked leftovers are deleted or explicitly gitignored with a comment saying why.
- `docs/superpowers/plans/` holds plans; `docs/CHANGELOG.md` holds what shipped; `RESUME.md` holds only the cursor. Nothing else is created under `docs/` by this plan except the Wave 0 results file.
- Dead code found while implementing a task is removed in that task only if it is inside the task's write set; otherwise it is listed in the commit body for a later tidy task, not left with a TODO.

**Running container / `data/` volume**
- Everything the app writes at runtime lives under `data/` in a named subdirectory with a documented retention rule (T6.5). No writes anywhere else in the container filesystem; the Docker e2e test asserts that the only new paths after a scheduler run are under `/app/data`.
- Every cache or capture directory has a size or age cap enforced by the janitor, and the cap is a config value with a default, not a constant buried in code.
- Logs: structured, one line per event, no per-item debug logging at INFO level; HTML dumps only on failure and only under `data/debug/` with rotation.
- Database: heartbeat rows become a column (T3.2); the observation tables hold one row per distinct offer; `VACUUM` after the compaction migration is a documented manual step.
- Backups compressed; retention enforced; restore path documented and tested once against a copy.
- Browser profiles are the only persistent state the scraper keeps; they are bounded, disposable, and never contain credentials (no login flows exist).

**Deliverables**
- The implementing agent's final report lists, per wave: commits, test counts before/after, benchmark numbers before/after, and the output of `git status --short` and `du -sh data/*` on the NAS after deploy.

---

## 5. Dispatch and review protocol

- **Wave ordering**: 0 → 1 → (2 ∥ 3) → 4 → 5 → 6. Inside a wave, tasks with disjoint write sets run concurrently; the write sets are the file lists in each task. T3.1 and T2.2 both touch `stats.py` — serialise T3.1 first.
- **Review tier per wave**: Wave 0 none (artefacts only); Waves 1, 2, 4, 5, 6 Tier 2 (`simplify` → `find-bugs` → `/second-opinion` → `fp-check`); Wave 3 Tier 4 (property tests written before the migration; fresh-session review).
- **Every worker brief carries**: the task text verbatim, the conventions paragraph at the top of §4, the two standing clauses (land findings to disk immediately; do only what the brief names), and the verification command. Workers commit; the root updates `docs/CHANGELOG.md` and `RESUME.md`.
- **Deployment**: after Waves 1–3 land, build and deploy (`git push` → GHCR → `ssh nasff235 'cd /share/CACHEDEV1_DATA/Container/book_alerter && docker compose pull && docker compose up -d'`), run the §6 checks against a fresh prod copy, and only then start Wave 4. Take a manual backup before the Wave 3 migration deploy.
- **Stop conditions**: a worker fails the same task twice → stop and record; any probe in Wave 0 contradicts a Wave 1/2 assumption → amend this document before dispatching the dependent task.

---

## 6. Reproduction commands (read-only)

6.1 Dashboard cost (prod copy at `$DB`):
```bash
python3 - <<'EOF'
import sqlite3, time
cur = sqlite3.connect('book_alerter.db').cursor()
t=time.perf_counter(); cur.execute("select * from book_stats").fetchall(); print('all books', time.perf_counter()-t)
t=time.perf_counter()
for b in range(1,14): cur.execute("select * from book_stats where book_id=?", (b,)).fetchall()
print('per book x13', time.perf_counter()-t)
EOF
```
6.2 Shipping flips:
```sql
SELECT book_id, seller, condition, price_minor,
       COUNT(DISTINCT COALESCE(shipping_minor,-1)) AS nvals,
       GROUP_CONCAT(DISTINCT COALESCE(shipping_minor,-1)) AS vals, COUNT(*) AS n
FROM priceobservation WHERE source='amazon'
GROUP BY 1,2,3,4 HAVING nvals > 1 ORDER BY n DESC;
```
6.3 Browser fingerprint (workstation, cached headless shell):
```bash
uv run python - <<'EOF'
import asyncio, glob
from playwright.async_api import async_playwright
exe = sorted(glob.glob('/home/ff235/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell'))[-1]
async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, executable_path=exe, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        p = await (await b.new_context(locale="en-GB")).new_page()
        print(await p.evaluate("navigator.userAgent"), await p.evaluate("navigator.plugins.length"))
        await b.close()
asyncio.run(main())
EOF
```
6.4 Product-alert surface gap:
```bash
grep -n product src/book_alerter/api/alerts.py web/src/pages/Alerts.tsx web/src/hooks/useAlerts.ts   # expect no output
```
6.5 Prod snapshot (NAS docker is at `/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker`, not on the non-interactive PATH):
```bash
rsync -a 'nasff235:/share/CACHEDEV1_DATA/Container/book_alerter/data/book_alerter.db*' <scratch>/proddb/
```

---

## 7. Out of scope (deliberately)

- Telegram / Pushover notifiers, Sentry wiring, Go source CLIs (all previously deferred; unchanged).
- Proxy rotation or paid anti-bot services (see Decision 4).
- Basket-level delivery economics (free-delivery thresholds across multiple items).
- Amazon Product Advertising API (requires an Associates account with sales; not pursued).
