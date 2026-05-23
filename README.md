# Book Alerter

Self-hosted price tracker for the UK market. Tracks two kinds of items:

- **Books** across World of Books, Bookfinder, and Amazon UK (ISBN-keyed).
- **Non-book Amazon products** across Amazon UK (ASIN-keyed), with optional Keepa historical backfill on add.

Computes per-item percentile stats from observed prices and fires alerts when an item hits your target, crosses the 25th-percentile "buy" threshold, or sets a new all-time low. Both kinds share the same alert pipeline, notification channels, dashboard chrome, and signal logic — only the natural-key (ISBN vs ASIN) and the source mix differ.

Designed to run as a single Docker container on a NAS behind Tailscale. SQLite for storage (one file, host-mounted), FastAPI + APScheduler for the backend, React + Tailwind for the UI. Money is stored as integer minor units throughout, so no float drift.

UK only at MVP. Currency is GBP. Prices are total landed cost (item + shipping) where the source exposes both.

## Contents

- [Architecture](#architecture)
- [Quickstart — local dev](#quickstart--local-dev)
- [Quickstart — Docker deploy](#quickstart--docker-deploy-recommended-for-nas)
- [Adding a tracked book](#adding-a-tracked-book)
- [Adding a tracked product](#adding-a-tracked-product)
- [Configuration](#configuration)
- [Adding a new source](#adding-a-new-source)
- [Notifications](#notifications)
- [Testing](#testing)
- [Backups](#backups)
- [Tailscale / access control](#tailscale--access-control)
- [Project status](#project-status)

## Architecture

- **Backend**: FastAPI + SQLModel + Alembic + APScheduler, Python 3.12.
- **Frontend**: Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui + TanStack Query / Table; built statically and served by FastAPI from the same port in production. Two top-level routes: `/` (Books) and `/products`.
- **Sources** (all inline Python, no subprocess):
  - **Books**: World of Books via `httpx` + `selectolax`; Bookfinder and Amazon UK via Playwright (Chromium).
  - **Products**: `amazon_uk_product` via Playwright (uses Product.asin directly, honours per-product `track_used` toggle).
  - Sources declare `item_kinds: frozenset[ItemKind]`; the scheduler intersects that with `SourceConfig.item_kinds` to route per-kind iteration.
- **Notifiers**: in-app (always on, bypasses quiet hours, surfaced in the UI sidebar) and ntfy.sh (optional, push to phone). Both notifiers receive book and product alerts via the same dispatcher.
- **Storage**: one SQLite database on a host-mounted volume. Two parallel item stacks (Book / Product) with their own observations / alerts / signal-state tables; one shared NotificationDelivery table with a polymorphic FK enforced by CHECK constraint. Weekly `VACUUM INTO` snapshots kept in `data/backups/`.
- **Deploy**: one Docker image, one compose file, one volume (`./data`).

See `docs/superpowers/specs/2026-05-09-book-alerter-design.md` for the full design doc.

## Quickstart — local dev

Requires [uv](https://github.com/astral-sh/uv), Node ≥ 20, and Python 3.12.

```bash
git clone <repo-url> book_alerter
cd book_alerter
uv sync
uv run alembic upgrade head
uv run playwright install chromium   # once per machine, for Bookfinder + Amazon sources
uv run uvicorn book_alerter.app:app --reload
```

In a second terminal:

```bash
cd web
npm install --legacy-peer-deps
npm run dev
```

- Backend: <http://127.0.0.1:8000> (API + OpenAPI docs at `/docs`)
- Frontend dev server: <http://localhost:5173>

The Vite dev server proxies `/api` to the backend on `127.0.0.1:8000` (see `web/vite.config.ts`), so the SPA hits the live FastAPI process while you iterate.

## Quickstart — Docker deploy (recommended for NAS)

```bash
git clone <repo-url> book_alerter
cd book_alerter
cp .env.example .env
# Edit .env if you want ntfy push, Basic auth, or a non-default timezone.
docker compose up -d
# UI + API: http://<host>:8000
```

- State (SQLite DB, `config.yaml`, backups) is bind-mounted at `./data` on the host, so `docker compose down && docker compose up -d` is lossless.
- Logs: `docker compose logs -f book_alerter`.
- Health: `curl -fsS http://localhost:8000/api/health` returns `{"status":"ok",...}`.
- The container runs as an unprivileged `pwuser` (uid/gid 1001) under `init: true` with `shm_size: 1gb` so Playwright's Chromium has enough `/dev/shm` to render non-trivial pages.
- Port `8000` is published on `0.0.0.0` so Tailscale peers can reach it; lock down at the Tailscale ACL or firewall layer. Bind to `127.0.0.1:8000:8000` in `docker-compose.yml` if you're behind a reverse proxy on the same host.

## Adding a tracked book

Via the UI: click **Add book**, paste an ISBN-10 or ISBN-13, confirm the metadata lookup.

Via the API:

```bash
curl -X POST http://localhost:8000/api/books \
  -H 'content-type: application/json' \
  -d '{
        "isbn": "9780099490548",
        "title": "Captain Corelli'\''s Mandolin",
        "author": "Louis de Bernieres"
      }'
```

Optional fields on create / `PATCH /api/books/{id}`:

- `target_price_minor` — fires a `target_hit` alert when the cheapest landed price across all sources falls at or below this value (within the configured `target_tolerance_pct`).
- `percentile_threshold` — per-book override of the default `recommendation.buy_percentile` (default 25).
- `format` — `paperback` | `hardcover` | `any` (default `any`).
- `notes` — freeform.

`DELETE /api/books/{id}` soft-deletes (sets `status="archived"`); pass `?hard=true` to actually drop the row. Archived books are excluded from the default list — `GET /api/books?include_archived=true` brings them back.

## Adding a tracked product

Via the UI: navigate to **Products** in the top nav, click **Add product**, paste either a 10-character ASIN (`B07XYZ1234`) or any Amazon URL containing one (`https://www.amazon.co.uk/dp/B07XYZ1234`, `/gp/product/...`, etc.). The dialog auto-fetches title + image + brand via a one-shot Playwright scrape of the Amazon UK dp page, then shows a preview before you confirm.

Via the API:

```bash
curl -X POST http://localhost:8000/api/products \
  -H 'content-type: application/json' \
  -d '{
        "asin_or_url": "https://www.amazon.co.uk/dp/B07XYZ1234",
        "title": "Anker PowerCore 10000",
        "brand": "Anker",
        "track_used": false
      }'
```

`asin_or_url` accepts bare ASINs or full URLs across TLDs; the server normalises via `to_asin`. Returns 422 on garbage input, 409 on duplicate ASIN (with the existing `product_id` in the detail).

Optional fields on create / `PATCH /api/products/{id}`:

- `target_price_minor` — fires `target_hit` when the cheapest landed price falls at or below this value (within `target_tolerance_pct`).
- `percentile_threshold` — per-product override of `recommendation.buy_percentile`.
- `track_used` — when `true`, tracks used grades from the Amazon offer-listing page. Default `false` because most non-book products have no meaningful used market on Amazon. Useful for collectibles / vintage cameras / games.
- `brand` — appears in the dashboard subtitle (where books show author).
- `notes` — freeform.

Other endpoints (mirror of `/api/books/*`):

- `GET /api/products` — list (excludes archived; `?include_archived=true` to include).
- `GET /api/products/{id}` — single product + stats.
- `GET /api/products/{id}/observations?limit=100&before=<iso>&source=...` — cursor-paginated price history (newest first, deduplicated).
- `POST /api/products/{id}/refetch` — fan out across every enabled product-serving source. Returns `{triggered, skipped}` with skip reasons `disabled` / `backoff_active` / `kind_unsupported`.
- `GET /api/products/{id}/stats` — full stats bundle (same shape as `GET /api/books/{id}/stats`).
- `POST /api/products/{id}/keepa-backfill` — one-shot Keepa PNG fetch + OCR + persist (idempotent; skipped if a `source='keepa'` row already exists). Also runs automatically as a background task after `POST /api/products`.
- `GET /api/products/{id}/keepa-chart.png` — proxies the Keepa price-history PNG with a 24h server-side cache.

To actually scrape products on the cron, add the source to `data/config.yaml`:

```yaml
sources:
  amazon_uk_product:
    enabled: true
    item_kinds: [product]
    schedule: "0 */6 * * *"
    jitter_seconds: 600
    per_book_delay_seconds: [5, 15]
    concurrency: 1
    timeout_seconds: 60
    max_consecutive_errors: 5
```

Existing book-source configs do NOT need an `item_kinds` field — it defaults to `[book]` for backwards compatibility. A source whose `item_kinds` doesn't intersect its `Source.item_kinds` capability is a no-op cycle (visible in the SourceRun audit row).

## Configuration

- The app reads `data/config.yaml` (`/app/data/config.yaml` in the container). On first boot, a default config is written if the file is missing.
- The **Settings** UI exposes the same config under four tabs (Sources, Recommendation, Notifications, Advanced). The Advanced tab is a Monaco YAML editor with dry-run validation + a diff preview before save.
- Any `${VAR}` reference in `config.yaml` is substituted from process env at load time, so the convention for secrets (ntfy topic, etc.) is to put the value in `.env` and reference it from YAML.

See `.env.example` for every supported env var. The notable ones:

| Var | Purpose | Default |
| --- | --- | --- |
| `BOOK_ALERTER_DATABASE_URL` | SQLite URL | `sqlite:////app/data/book_alerter.db` |
| `BOOK_ALERTER_CONFIG_PATH` | YAML config path | `/app/data/config.yaml` |
| `NTFY_SERVER`, `NTFY_TOPIC` | Referenced from `config.yaml` for ntfy push | — |
| `APP_BASIC_AUTH_USER`, `APP_BASIC_AUTH_PASS` | Optional HTTP Basic guard; both must be non-empty to enable | off |
| `TZ` | Container timezone (affects log timestamps + quiet-hours window) | `Europe/London` |

## Adding a new source

The source layer is plain Python — there is no separate CLI per source.

1. Subclass `InlineSource` from `src/book_alerter/sources/inline_source.py`.
2. Implement `async def fetch(self, book: Book) -> list[ObservationCandidate]` (signature in `src/book_alerter/sources/base.py`). Return one candidate per offer found, with seller, condition, item price, shipping, currency, and URL.
3. Register the class in `src/book_alerter/sources/registry.py` under a stable name.
4. Add a `sources.<name>` block to `config.yaml` (`enabled`, `region`, `schedule`, `concurrency`, `timeout_seconds`, etc. — see `SourceConfig` in `src/book_alerter/config.py` for the full schema).

Reference implementations:

- `src/book_alerter/sources/wob.py` — pure `httpx` + `selectolax`. The simple case.
- `src/book_alerter/sources/bookfinder.py` — Playwright. Renders the search results page in Chromium.
- `src/book_alerter/sources/amazon.py` — Playwright with `/dp/` + `offer-listing` fallback. Demonstrates the in-fetch shared-browser pattern (one Chromium instance per `fetch` call, reused across selectors).

> **Note on `printing-press`** — earlier phases scaffolded `printing-press` as the source authoring toolchain. That approach was abandoned in Phase 8 once AWS WAF / anti-bot conditions on Bookfinder and Amazon defeated every static-cookie / subprocess approach. Sources are now first-class inline Python with full access to Playwright's runtime. Don't try to bring it back; see the 2026-05-14 architecture revision note in the plan for the post-mortem.

## Notifications

- **In-app** — always on, bypasses quiet hours. Surfaced in the right-hand Alerts sidebar and on the `/alerts` page. Dismiss individually or via "dismiss all".
- **ntfy.sh** — opt-in via Settings → Notifications (or by toggling `notifications.channels.ntfy.enabled` in `config.yaml`). Works against any ntfy server (default `https://ntfy.sh`). Per-channel "Send test" button in the UI verifies wiring end-to-end.
- **Quiet hours** — suppress non-bypass channels in a configurable window (default 22:00–08:00 Europe/London). In-app alerts still land. No replay of suppressed pushes after the window closes; this is a known MVP behaviour and is documented in `docs/CHANGELOG.md` under the Phase 5 entry.

Alert kinds: `target_hit`, `percentile_cross`, `new_low`. Each kind can be disabled globally (`notifications.alert_kinds_enabled`) or per-book (`PATCH /api/books/{id}` with `alert_kinds_disabled`).

## Testing

| Command | Purpose | Expected |
| --- | --- | --- |
| `uv run pytest -q` | Unit + integration tests | 221 passed, 2 skipped |
| `bash tests/scenarios/run_all.sh` | End-to-end scenario suite (signal transitions, dedup, quiet hours, mute, kind toggles, API surface) | 6/6 PASS |
| `uv run pytest -m e2e tests/e2e/` | Docker smoke test (boots `book_alerter:dev`, exercises API + DB) | 1 passed, ~5 s |

The two skipped pytest cases are live Bookfinder / Amazon canaries gated by `BOOKFINDER_LIVE=1` / `AMAZON_LIVE=1`; they're skipped by default so the suite stays network-free.

The e2e Docker test requires the `book_alerter:dev` image to exist locally — build it once with `docker build -t book_alerter:dev .` before running.

## Backups

- Weekly `VACUUM INTO` cron job runs at Sunday 03:00 UTC by default. Schedule, target directory, and retention are configurable under `backup:` in `config.yaml`.
- The last 7 snapshots are kept under `data/backups/`; older ones are pruned.
- To restore: stop the container, copy a snapshot over `data/book_alerter.db`, start again. `alembic upgrade head` runs on every boot via the entrypoint so older schema versions are migrated forward automatically.

## Tailscale / access control

The default posture is "private network = auth boundary":

- The container binds `0.0.0.0:8000`; the published port is reachable from anything that can route to the host (Tailscale peers, LAN clients, etc.).
- Restrict ingress at the Tailscale ACL or upstream firewall layer.
- For defence-in-depth or non-Tailscale deployments, set `APP_BASIC_AUTH_USER` + `APP_BASIC_AUTH_PASS` in `.env`. Both must be non-empty to enable; the guard wraps every route under `/api/*` and the SPA shell.

## Project status

Implemented through Plan Phases 0–13. MVP-complete for UK second-hand book tracking with three live sources, in-app + ntfy notifications, scheduled scraping with jitter and backoff, weekly backups, and a single-port Docker deploy. Known deferred follow-ups (a scheduler-level shared Chromium, ntfy quiet-hours replay, additional sources beyond UK) are tracked in `docs/CHANGELOG.md` and the "Deferred follow-ups" section of `RESUME.md`.
