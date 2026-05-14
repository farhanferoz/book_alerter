---
title: Book Alerter — design spec
date: 2026-05-09
status: draft
owner: ff235
---

# Book Alerter — design spec

A self-hosted personal book-price comparison and alerting tool that runs on a home NAS, polls multiple book retailers periodically, builds its own price history, and surfaces buy/wait recommendations through a web dashboard and push notifications.

> **⚠ Architecture revision (2026-05-14)** — this document still describes the original design where source CLIs were Go binaries generated via `printing-press` and orchestrated through a `SubprocessSource` ABC. As of Phase 8.2, **that path is abandoned**. The actual shipped architecture: a single Python process where every source is an `InlineSource` subclass — `WobInlineSource` uses `httpx`, `BookfinderInlineSource` uses Playwright (headless Chromium) because bookfinder.com is fronted by AWS WAF's `mp_verify` challenge variant which defeats every static-cookie / pure-Go-solver replay attempt. Amazon UK (Phase 8.3) will follow the Playwright pattern. `SubprocessSource` has been removed from the codebase along with `Literal["subprocess","inline"]`/`binary` fields on `SourceConfig`. There are no Go binaries, no `cli_bins/` directory, and no `printing-press` dependency. The original printing-press-aligned descriptions below are kept as historical context but no longer match the code. See the Phase 8 entries in `docs/CHANGELOG.md` for the investigation trail.

## TL;DR

- **Architecture**: single Python container (FastAPI + APScheduler in-process) orchestrating Go source-CLIs (printing-press-generated) over a pluggable `Source` interface; SQLite on a host volume.
- **Sources at MVP**: Bookfinder (meta-search), World of Books UK (direct), Amazon UK (direct, condition-aware).
- **Region**: UK only at MVP; designed for multi-region later.
- **Identity**: ISBN-pinned per book at MVP; bookfinder-style title/author/ISBN search later.
- **Recommendation**: hybrid — statistical default (BUY at ≤ p25 of history) + per-book target-price override.
- **Alerts**: in-app feed always; **MVP push = ntfy only**; Telegram + Pushover are deferred (the `Notifier` slot is reserved). All channels configurable per kind, per channel, per book, per quiet-hours.
- **Frontend**: React + Vite + Tailwind + shadcn/ui + Recharts.
- **Config**: YAML file + UI editor — both write through the same Pydantic schema.

## Goals

1. Maintain a list of books I want to buy, identified by ISBN.
2. Periodically check current prices from Bookfinder, World of Books UK, and Amazon UK (new + used-very-good + used-good).
3. Build my own price history over time and compute statistical context (p25/p50/p75, all-time min/max, observation count).
4. Surface a buy/watch/wait signal per book using a hybrid percentile + per-book-target rule.
5. Notify me on buy-worthy events through in-app feed and push (MVP: ntfy; Telegram and Pushover deferred — pluggable Notifier interface keeps them slot-in additions later).
6. Run reliably on a home NAS with Docker.
7. All configuration must be editable via both file (`data/config.yaml`) and UI.

## Non-goals (deferred or rejected)

- Paid APIs (Keepa). Ruled out.
- Backfill of Amazon historical data. Not available for free; we build our own going forward.
- Multi-region/marketplace at MVP. Designed pluggable; not implemented.
- Title/author search-driven add flow. Replaced for MVP by ISBN-pinning + ISBN/metadata lookup; full bookfinder-style search comes later.
- Bulk import (CSV) and Amazon-wishlist import. Deferred.
- Auto-dismiss of alerts when price rises. Manual dismiss only.
- Telegram and Pushover push channels at MVP. Reserved as adapter slots; first push channel is ntfy.
- Cross-source observation deduplication logic. Schema field is added now (`is_duplicate_of`); the actual dedup pass is deferred until we observe real overlap data.
- Materialised `book_stats` table. Replaced by a SQL view + Python helper, both drift-free; can be swapped for a real table later if needed.
- Multi-user / SaaS. Single-user, LAN-deployed, optional HTTP Basic.

## Key decisions (collected user inputs)

| Decision | Outcome |
|---|---|
| Sources | Bookfinder (meta-search, covers eBay/Abebooks etc.), WoB UK direct, Amazon UK direct (new + used-vg + used-g) |
| Region | UK only at MVP; pluggable schema for future regions |
| Book identity | ISBN-pinned per book at MVP |
| NAS environment | Docker available (Unraid/TrueNAS/QNAP class) |
| Paid APIs | None; Keepa skipped |
| Scale | ~20 books × multiple polls/day |
| UI depth | Dashboard + alerts feed + stats; per-book detail with history chart |
| Recommendation logic | Hybrid: percentile default + per-book target override |
| Notifications | In-app feed + push (MVP: ntfy; Telegram + Pushover deferred); per-kind, per-channel, per-book, per-quiet-hours toggles |
| Auth posture | Tailscale-only access; app ships without auth (Basic remains optional) |
| Add-book flow | Manual ISBN entry + title/author search (via OpenLibrary/Google Books) |
| Tech stack | Python (FastAPI + SQLite + React/Vite + shadcn/ui + Recharts); Go source-CLIs from printing-press |
| Approach | A — CLI-orchestrator (printing-press-aligned plugin model) |

## Architecture

```
   ┌──────────────────── Docker image ────────────────────┐
   │  ┌─────────────┐                                     │
   │  │  FastAPI    │  ←  React SPA (Vite-built) served   │
   │  │   + UI      │     statically from /web            │
   │  └──────┬──────┘                                     │
   │         │                                            │
   │  ┌──────┴──────┐                                     │
   │  │ Orchestrator│  Python: scheduler, sources,        │
   │  │             │  stats, alerts, notifiers           │
   │  └──┬───┬───┬──┘                                     │
   │     │   │   │                                        │
   │     ▼   ▼   ▼                                        │
   │  bookfinder  wob   amazon       (Go binaries from    │
   │  -pp-cli   -pp-cli -pp-cli       printing-press,     │
   │                                  baked in via        │
   │                                  multi-stage build)  │
   └────────────────────┬─────────────────────────────────┘
                        │
                   ┌────▼────┐
                   │ SQLite  │  on ./data host volume
                   └─────────┘
```

**Single process** runs FastAPI (uvicorn) and APScheduler (`AsyncIOScheduler`) in the same asyncio loop. APScheduler dispatches per-source jobs as asyncio tasks; each task shells out to the source's CLI via `asyncio.create_subprocess_exec`.

**Pluggable sources** via a `Source` ABC with two concrete bases — `SubprocessSource` (wraps a printing-press CLI) and `InlineSource` (Python escape hatch). Adding a new source means adding a YAML block + a ~30-LOC adapter file + (optionally) generating a new printing-press CLI.

**Durability boundary** is `./data`. Everything reproducible from `docker-compose up` plus a fresh `data/` dir.

## Repository layout

```
book_alerter/
├── docker-compose.yml
├── Dockerfile                       # multi-stage: go-builder → python-runtime
├── pyproject.toml                   # uv-managed
├── README.md
├── .env.example
├── docs/superpowers/specs/…
├── data/                            # gitignored; runtime mounts
│   ├── book_alerter.db
│   ├── config.yaml
│   ├── backups/
│   └── logs/
├── src/book_alerter/
│   ├── __init__.py
│   ├── app.py                       # FastAPI app factory + lifespan
│   ├── config.py                    # Pydantic schema; load/save/migrate config.yaml
│   ├── db/
│   │   ├── models.py                # SQLModel tables
│   │   ├── migrations/              # Alembic
│   │   └── session.py
│   ├── sources/
│   │   ├── base.py                  # Source ABC + SubprocessSource + InlineSource
│   │   ├── registry.py              # discover & instantiate from config
│   │   ├── bookfinder.py
│   │   ├── wob.py
│   │   ├── amazon.py
│   │   └── normalizers.py           # CLI JSON → ObservationCandidate
│   ├── scheduler.py                 # APScheduler jobs, jitter, backoff
│   ├── stats.py                     # historical percentiles, signal computation
│   ├── alerts.py                    # signal-change detection + dedup
│   ├── notifications/
│   │   ├── base.py                  # Notifier ABC
│   │   ├── inapp.py
│   │   ├── ntfy.py
│   │   ├── telegram.py
│   │   └── pushover.py
│   ├── metadata.py                  # ISBN → title/author via OpenLibrary, fallback Google Books
│   └── api/
│       ├── books.py
│       ├── prices.py
│       ├── alerts.py
│       ├── sources.py
│       ├── config.py
│       └── health.py
├── web/                             # React + Vite + Tailwind frontend
│   ├── package.json
│   ├── src/…
│   └── …
├── cli_bins/                        # printing-press-generated Go source — git submodules
│   ├── bookfinder-pp-cli/           # tracks the published printing-press-library repo path
│   ├── wob-pp-cli/                  # so `git pull --recurse-submodules` updates them
│   └── amazon-pp-cli/               # docker build COPYs the working tree (submodule contents)
└── tests/
    ├── unit/
    ├── integration/
    │   └── sources/
    │       └── cassettes/           # vcr.py recordings
    └── e2e/
```

## Data model

Five tables. Money stored as **integer minor units** (pence). Timestamps **UTC**. ISBNs normalised to **ISBN-13** on input via `isbnlib`.

```python
# Book — one row per book on the wishlist
class Book(SQLModel, table=True):
    id: int = Field(primary_key=True)
    isbn13: str = Field(unique=True, index=True)
    title: str
    author: str
    cover_url: str | None = None
    format: Literal["paperback", "hardcover", "any"] = "any"
    region: str = "UK"                          # ISO 3166-1; pluggable later
    currency: str = "GBP"                       # ISO 4217
    target_price_minor: int | None = None       # NULL → use percentile signal
    percentile_threshold: int | None = None     # NULL → use global default
    status: Literal["active", "archived", "bought"] = "active"
    bought_price_minor: int | None = None
    notes: str | None = None
    alert_kinds_disabled: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    muted_until: datetime | None = None         # temporary mute
    created_at: datetime
    updated_at: datetime

# PriceObservation — fact table; one row per observed offer
class PriceObservation(SQLModel, table=True):
    id: int = Field(primary_key=True)
    book_id: int = Field(foreign_key="book.id", index=True)
    source: str                                 # "bookfinder" | "wob" | "amazon"
    seller: str | None = None
    condition: Literal["new","used_vg","used_g","used_acceptable","unknown"]
    price_minor: int
    currency: str
    shipping_minor: int | None = None
    total_minor: int                            # price + (shipping or 0); precomputed
    url: str
    observed_at: datetime = Field(index=True)
    raw: dict = Field(sa_column=Column(JSON))   # original CLI payload
    is_duplicate_of: int | None = Field(
        default=None, foreign_key="priceobservation.id"
    )

    __table_args__ = (
        Index("ix_obs_book_observed", "book_id", "observed_at"),
        Index("ix_obs_book_source_observed", "book_id", "source", "observed_at"),
    )

# SourceRun — per-scheduled-run audit
class SourceRun(SQLModel, table=True):
    id: int = Field(primary_key=True)
    source: str
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["running", "success", "error", "partial"]
    books_attempted: int = 0
    books_succeeded: int = 0
    error_message: str | None = None
    error_traceback: str | None = None

# Alert — one row per fired alert
class Alert(SQLModel, table=True):
    id: int = Field(primary_key=True)
    book_id: int = Field(foreign_key="book.id", index=True)
    kind: Literal["new_low", "target_hit", "percentile_cross"]
    price_minor: int
    currency: str
    source: str
    condition: str
    message: str
    fired_at: datetime = Field(index=True)
    dismissed_at: datetime | None = None
    delivered_via: list[str] = Field(sa_column=Column(JSON))   # ["inapp", "ntfy", ...]

# NotificationDelivery — per-channel send audit
class NotificationDelivery(SQLModel, table=True):
    id: int = Field(primary_key=True)
    alert_id: int = Field(foreign_key="alert.id", index=True)
    channel: str
    sent_at: datetime
    status: Literal["sent", "error"]
    error_message: str | None = None
```

### `book_stats` SQL view

Read-only aggregation over `Book` × `PriceObservation`. Used by API and any external tool pointed at the SQLite file. Implemented as a CREATE VIEW in Alembic migration `0002_book_stats_view.py`. The view exposes:

- `current_best_total_minor`, `current_best_source`, `current_best_seller`, `current_best_condition`, `current_best_url`
- `p25_total_minor`, `p50_total_minor`, `p75_total_minor`
- `all_time_min_total_minor`, `all_time_max_total_minor`
- `observation_count`, `days_of_history`
- `last_observed_at`

Signal is computed by `stats.compute_signal()` in Python (uses configurable thresholds and per-book overrides; not stable enough as pure SQL). The API combines view rows with the computed signal before returning.

If perf ever requires materialisation, the view is replaced by a real table with the same columns plus a recompute hook on `PriceObservation` insert; consumer code unchanged.

### Schema-evolution guardrails

| Future change | Schema impact |
|---|---|
| Multi-region (e.g. US marketplace) | `Book.region` already present; per-source region resolution handled in adapters; no migration |
| Title/author search add-flow | Add `Book.search_query: dict | None` and make `isbn13` nullable; one migration |
| Format constraint tracking | `Book.format` already supports paperback/hardcover; tighten constraint per source as needed |
| Cross-source dedup | `PriceObservation.is_duplicate_of` already present; populate via background job |

## Source plugin interface

### CLI contract

Every source CLI accepts an ISBN-13 + region, prints JSON to stdout, exits non-zero on failure with an error message to stderr.

```jsonc
{
  "isbn13": "9780000000000",
  "queried_at": "2026-05-09T17:30:00Z",
  "region": "UK",
  "currency": "GBP",
  "offers": [
    {
      "seller": "AwesomeBooks",
      "condition": "used_g",        // "new" | "used_vg" | "used_g" | "used_acceptable" | "unknown"
      "price_minor": 850,
      "shipping_minor": 0,           // null if unknown
      "url": "https://..."
    }
  ],
  "warnings": []                     // soft issues; CLI still exits 0
}
```

### Python interface

```python
class ObservationCandidate(BaseModel):
    seller: str | None
    condition: Literal["new","used_vg","used_g","used_acceptable","unknown"]
    price_minor: int
    shipping_minor: int | None
    currency: str
    url: str

class Source(ABC):
    name: str
    @abstractmethod
    async def fetch(self, book: Book) -> list[ObservationCandidate]: ...
    async def healthcheck(self) -> bool: return True

class SubprocessSource(Source):
    """Wraps a printing-press CLI. Subclasses define build_command() + parse()."""
    def __init__(self, name, binary, region, timeout_s, env=None):
        ...
    async def fetch(self, book):
        cmd = self.build_command(book)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=PIPE, stderr=PIPE, env=self.env)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), self.timeout_s)
        if proc.returncode != 0:
            raise SourceError(self.name, stderr.decode())
        return self.parse(stdout.decode())

class InlineSource(Source):
    """Base for Python-implemented sources (escape hatch)."""
```

### Source config (YAML)

```yaml
sources:
  bookfinder:
    enabled: true
    type: subprocess
    binary: bookfinder-pp-cli
    region: UK
    schedule: "0 */6 * * *"
    jitter_seconds: 600
    per_book_delay_seconds: [5, 15]
    concurrency: 1                   # within-source; 1 = strictly sequential
    timeout_seconds: 60
    max_consecutive_errors: 5
  wob:    { enabled: true, ... }
  amazon: { enabled: true, ... }
```

## Scheduler

`APScheduler.AsyncIOScheduler` runs in the FastAPI app lifespan. Each enabled source registers a cron job. On trigger:

1. Open `SourceRun(status="running")`.
2. For each active book: sleep uniform `per_book_delay_seconds`, call `source.fetch(book)`, insert `PriceObservation` rows.
3. Per-book failures logged + counted; do not abort the run.
4. Trigger alert evaluation for affected books.
5. Finalise `SourceRun` (`success` | `partial` | `error`).

### Parallelism rules

| Layer | Parallel? |
|---|---|
| Cross-source | Full — independent asyncio tasks |
| Within a source, across books | Configurable (`concurrency`); default 1 (sequential, polite); ceiling 5 |
| Notification delivery (multiple channels per alert) | Parallel via `asyncio.gather` |
| Book-add metadata enrichment (OpenLibrary + Google Books) | Parallel race; first valid wins |
| Initial sync on startup | All enabled sources kicked off in parallel; per-source `concurrency` cap still applies within each |
| Alert evaluation | Sequential (trivially fast) |

### Politeness defaults

- Sequential within a source (`concurrency: 1`).
- Random delay between books (`per_book_delay_seconds: [5, 15]`).
- Cron jitter (`jitter_seconds: 600`) so sources don't fire at top-of-hour together.
- CLI timeout enforced (`timeout_seconds`); subprocess killed if it hangs.

### Backoff

If a source's `consecutive_errors > max_consecutive_errors`, next run delayed `min(base × 2^errors, 24h)`. Resets on success.

## Recommendation engine

### Stats (computed live)

```python
@dataclass
class BookStats:
    book_id: int
    current_best_total_minor: int | None
    current_best_source: str | None
    current_best_seller: str | None
    current_best_condition: str | None
    current_best_url: str | None
    p25_total_minor: int | None
    p50_total_minor: int | None
    p75_total_minor: int | None
    all_time_min_total_minor: int | None
    all_time_max_total_minor: int | None
    observation_count: int
    days_of_history: int
    signal: Literal["BUY", "WATCH", "WAIT", "TARGET_HIT", "INSUFFICIENT_DATA"]
    pct_below_median: float | None
    last_observed_at: datetime | None
```

`current_best_*` is computed from the **latest observation per source**, then `min(total_minor)` across sources. `p25/p50/p75/min/max` over the full history (excluding rows with `is_duplicate_of IS NOT NULL`).

### Signal logic

```python
def compute_signal(book, stats, cfg) -> Signal:
    if stats.observation_count < cfg.min_observations_for_signal:
        return "INSUFFICIENT_DATA"

    threshold_pct = book.percentile_threshold or cfg.buy_percentile

    if book.target_price_minor is not None:
        tolerance = book.target_price_minor * (1 + cfg.target_tolerance_pct / 100)
        if stats.current_best_total_minor <= book.target_price_minor:
            return "TARGET_HIT"
        if stats.current_best_total_minor <= tolerance:
            return "BUY"
        # Target set but not hit/almost-hit → fall through to percentile logic
        # so the user still gets BUY/WATCH/WAIT context, not just "target unmet".

    if stats.current_best_total_minor <= percentile(stats, threshold_pct):
        return "BUY"
    if stats.current_best_total_minor <= stats.p50_total_minor:
        return "WATCH"
    return "WAIT"
```

### Defaults (configurable globally and per-book)

| Knob | Default | Meaning |
|---|---|---|
| `min_observations_for_signal` | 14 | Below this, signal is `INSUFFICIENT_DATA` |
| `buy_percentile` | 25 | Current beats 75% of historical observations |
| `watch_percentile` | 50 | Between p25 and p50 |
| `target_tolerance_pct` | 5 | Target almost-hit also fires BUY |

## Alerts & notifications

### Alert kinds (non-exclusive)

| Kind | Trigger |
|---|---|
| `target_hit` | `current ≤ target_price_minor` (only when target set) |
| `percentile_cross` | Signal transition to `BUY` (previous signal was not `BUY`). Fires on `TARGET_HIT → BUY` transitions too (e.g. when a target is unset and percentile logic now classifies as BUY). |
| `new_low` | `current_best_total_minor < previous all_time_min` |

All three may fire for the same observation.

### Lifecycle & dedup

- Within `alert_dedup_window_hours` (default 24), the same `(book_id, kind)` does not re-fire.
- Manual dismiss only.
- `NotificationDelivery` rows make per-channel retries idempotent.

### Channels

```python
class Notifier(ABC):
    name: str
    @abstractmethod
    async def send(self, alert: Alert, book: Book) -> NotificationDelivery: ...

# Concrete at MVP: InAppNotifier, NtfyNotifier
# Reserved slots (post-MVP): TelegramNotifier, PushoverNotifier
```

In-app always creates an `Alert` row. Other channels are best-effort: `asyncio.gather` across enabled notifiers; per-channel errors logged but non-fatal to the alert itself.

### Toggle hierarchy

1. Global per-kind (`notifications.alert_kinds_enabled`)
2. Global per-channel (`notifications.channels.<name>.enabled`)
3. Per-book per-kind (`Book.alert_kinds_disabled`)
4. Per-book temporary mute (`Book.muted_until`)
5. Quiet hours (`notifications.quiet_hours`) — alerts still queued; pushes deferred to end of window

### Push payload (channel-agnostic core)

```
[BUY] Sapiens — £6.99 (was median £9.50, -27%)
WoB UK · used-very-good · click to buy: <url>
```

Each channel adapts to its own format.

## API surface (FastAPI)

| Method & Path | Purpose |
|---|---|
| `GET /api/books` | List with attached `BookStats` |
| `POST /api/books` | Add by ISBN or by metadata search-pick |
| `GET /api/books/{id}` | Detail incl. computed stats |
| `PATCH /api/books/{id}` | Update target/threshold/status/mute/notes/disabled-kinds |
| `DELETE /api/books/{id}` | Soft-delete (archive); `?hard=true` for permanent |
| `POST /api/books/{id}/refetch` | Manual trigger across all enabled sources |
| `GET /api/books/{id}/observations` | Paginated history (chart data) |
| `GET /api/books/{id}/stats` | Full `BookStats` |
| `GET /api/alerts` | Feed; filter kind + dismissed; paginated |
| `POST /api/alerts/{id}/dismiss`, `POST /api/alerts/dismiss-all` | |
| `GET /api/sources` | Per-source status + recent `SourceRun` rows |
| `POST /api/sources/{name}/run` | Trigger immediate scrape |
| `PATCH /api/sources/{name}` | Enable/disable, schedule, concurrency |
| `GET /api/config`, `PUT /api/config` | YAML round-trip with diff preview |
| `GET /api/metadata/lookup?isbn=...` | OpenLibrary; Google Books fallback |
| `GET /api/metadata/search?q=...` | Title/author search; ISBN candidates |
| `GET /api/health` | Liveness + per-source health |
| `POST /api/notifications/{channel}/test` | Synthetic alert through one channel |

### Auth

**Deployment assumes Tailscale-only access** — the app sits on the NAS reachable via Tailscale net, no public exposure. App-level auth is therefore off by default. HTTP Basic remains available via `APP_BASIC_AUTH_USER` / `APP_BASIC_AUTH_PASS` env vars (off when blank) for users on different topologies (LAN-direct or behind Authelia/Nginx basic-auth).

### OpenAPI

Pydantic-driven; Swagger at `/docs`. Frontend consumes generated TypeScript types via `openapi-typescript`.

## UI

### Stack

React + Vite + TypeScript + Tailwind + shadcn/ui + Recharts. Single-page app served statically from `/web` by FastAPI in production. Vite dev server proxies `/api/*` to FastAPI in development.

Choice rationale: the "edit YAML in UI with live validation + diff preview" surface and the multi-series interactive history chart are easier in React than HTMX. Trade-offs accepted: ~150 KB gzipped bundle, ~2 min Docker build for the frontend stage.

### Pages

#### Dashboard (`/`)
- Header strip: total active books · # signals=BUY · # unread alerts · per-source health dots · last successful scan timestamp
- Filter bar: signal, status, source-health, sort
- Main table: cover · title+author · best price (source badge + condition pill) · signal pill · % vs median (sparkline-coloured) · days of history · last seen · row actions (refetch, mute, open)
- Click row → book detail
- Collapsible right sidebar: unread alerts feed; dismiss inline
- Empty state: "Paste an ISBN to start" CTA

#### Book detail (`/books/:id`)
- Header card: cover, title, author, ISBN, format, status — inline-editable
- Snapshot card: current best price + which source/condition/seller/url; last observed timestamp
- Signal card: signal pill, target distance, percentile context ("18th percentile of 73 obs over 24 days")
- History chart: Recharts line chart, multi-series (one per `(source, condition)`), legend toggleable, alert markers as annotations
- Source breakdown table: latest observation per source, freshness indicator
- Settings panel: target_price · percentile_threshold · alert kinds · mute_until · notes
- Actions: refetch now · mark as bought (with bought_price) · archive · delete

#### Alerts (`/alerts`)
- Filter by kind, dismissed/active, book
- Bulk-select + dismiss
- Each row links to the book and the offer URL

#### Settings (`/settings`)
Tabbed:
- **Sources** — per-source: enabled, schedule (cron picker + jitter), concurrency slider, per-book delay, manual "run now", last 10 runs with status
- **Recommendation** — global thresholds (min_obs, buy_pct, watch_pct, dedup window)
- **Notifications** — per-channel config (test-send button), per-kind toggles, quiet hours
- **Advanced** — raw `config.yaml` editor (Monaco) with live JSON-schema validation and diff preview before save; atomic write with backup

#### Add book (modal)
- Tab A — paste ISBN → auto-fetch metadata → confirm
- Tab B — search title/author → result list with covers → pick edition → confirm
- Confirm-and-save also kicks off an immediate one-shot scrape so user sees data quickly

### Visual conventions

- Tailwind + shadcn/ui components
- Dark mode by default, light-mode toggle
- Mobile responsive — tables collapse to cards under `sm:` (640 px)
- Signal palette: BUY (green), TARGET_HIT (emerald, prominent), WATCH (amber), WAIT (slate), INSUFFICIENT_DATA (neutral)

## Configuration

Single Pydantic schema (`config.py`) is the source of truth. Two write paths:

```
                              ┌──────────────────┐
                              │  Pydantic schema │
                              └────────┬─────────┘
              ┌────────────────────────┴────────────────────────┐
              │                                                 │
   ┌──────────▼──────────┐                          ┌───────────▼──────────┐
   │  data/config.yaml   │  ←  watch + reload  →    │   PUT /api/config     │
   │ (atomic write +     │                          │   (validate, diff,    │
   │  rotating backup)   │                          │    save, reload)      │
   └─────────────────────┘                          └───────────────────────┘
```

- **Atomic writes**: write `config.yaml.tmp`, fsync, rename. Pre-write backup → `data/backups/config.yaml.{timestamp}`.
- **File-edit detection**: `watchfiles` triggers `Config.reload()`. Same code path as API saves.
- **Schema migrations**: idempotent upgraders (`_v1_to_v2`, etc.) run before validation; migrated YAML written back so the file stays canonical.
- **Secrets**: `${VAR}` substitution at load. Secrets never written to YAML. UI shows masked "set in env" indicators.
- **Bad config on startup**: app refuses to start. On hot-reload after-start: fall back to last-known-good and surface error in UI banner + `/api/health`.

### Example `config.yaml`

```yaml
config_version: 1                    # bumped by upgraders when schema changes

recommendation:
  min_observations_for_signal: 14
  buy_percentile: 25
  watch_percentile: 50
  target_tolerance_pct: 5
  alert_dedup_window_hours: 24

notifications:
  alert_kinds_enabled: [target_hit, percentile_cross, new_low]
  quiet_hours: { start: "22:00", end: "08:00", tz: "Europe/London" }
  channels:
    inapp:    { enabled: true }
    ntfy:
      enabled: true
      server: https://ntfy.sh
      topic: ${NTFY_TOPIC}
      priority: default
      tags: [book, money]
    # telegram + pushover deferred post-MVP — config block reserved so the
    # adapter slot is obvious; channels won't appear in the UI until built.
    # telegram:
    #   enabled: false
    #   bot_token: ${TELEGRAM_BOT_TOKEN}
    #   chat_id: ${TELEGRAM_CHAT_ID}
    # pushover:
    #   enabled: false
    #   user_key: ${PUSHOVER_USER_KEY}
    #   app_token: ${PUSHOVER_APP_TOKEN}

sources:
  bookfinder:
    enabled: true
    type: subprocess
    binary: bookfinder-pp-cli
    region: UK
    schedule: "0 */6 * * *"
    jitter_seconds: 600
    per_book_delay_seconds: [5, 15]
    concurrency: 1
    timeout_seconds: 60
    max_consecutive_errors: 5
  wob:    { enabled: true, type: subprocess, binary: wob-pp-cli,    region: UK, schedule: "15 */6 * * *", … }
  amazon: { enabled: true, type: subprocess, binary: amazon-pp-cli, region: UK, schedule: "30 */6 * * *", … }
```

## Error handling — failure isolation hierarchy

| Failure scope | Behaviour | User-visible |
|---|---|---|
| One book within a source run | Logged + counted; run continues with next book | Per-book "last error" in detail view |
| One source (CLI hard-fails, network down, parse error) | `SourceRun.status="error"`; backoff applied | Source dot turns red; error log in Settings → Sources |
| All sources for one book | `signal="INSUFFICIENT_DATA"` until recovery; existing observations remain | Book card shows "no fresh data" badge |
| DB locked / temp filesystem error | Retry with exponential backoff (3 attempts) inside the source job | Logged; surfaces only if persistent |
| Notifier failure | Per-channel `NotificationDelivery.status="error"`; Alert row still created; retry on next eval | Settings → Notifications shows last channel error |
| Schema migration failure on startup | App refuses to start | Container logs; Docker auto-restart loop until fixed |
| Config validation failure on hot-reload | Fall back to previous valid config | UI banner + Settings → Advanced shows diff |

### Logging

`structlog` JSON to stdout (Docker captures) plus rotating file in `data/logs/book_alerter.log`. One log line per source-run, per book-fetch, per alert-fire. A trace ID links a scheduler trigger → all related events.

### Sentry

Optional, off by default. If `SENTRY_DSN` env var present, init Sentry SDK with low sample rate.

## Deployment

### Multi-stage Dockerfile

```dockerfile
# stage 1: build Go CLIs
FROM golang:1.27-alpine AS go-builder
WORKDIR /build
COPY cli_bins/ /build/cli_bins/
RUN cd cli_bins/bookfinder-pp-cli && go build -o /out/bookfinder-pp-cli ./cmd/... \
 && cd /build/cli_bins/wob-pp-cli      && go build -o /out/wob-pp-cli      ./cmd/... \
 && cd /build/cli_bins/amazon-pp-cli   && go build -o /out/amazon-pp-cli   ./cmd/...

# stage 2: Python runtime
FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY src/ ./src/
COPY web/dist/ ./web/dist/
COPY --from=go-builder /out/* /usr/local/bin/
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "book_alerter.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

(Frontend `web/dist/` is built in a separate Vite stage in CI or pre-built locally.)

### docker-compose.yml

```yaml
services:
  book_alerter:
    build: .
    image: book_alerter:latest
    container_name: book_alerter
    ports:
      - "127.0.0.1:8000:8000"        # LAN-only by default
    volumes:
      - ./data:/app/data
    env_file: .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
    deploy:
      resources:
        limits: { memory: 512M, cpus: '1.0' }
```

### `.env.example`

```
TZ=Europe/London
NTFY_TOPIC=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
PUSHOVER_USER_KEY=
PUSHOVER_APP_TOKEN=
SENTRY_DSN=
APP_BASIC_AUTH_USER=
APP_BASIC_AUTH_PASS=
```

### Backups

SQLite `.backup` runs weekly via APScheduler to `data/backups/`. Retain last 7. One-line config knob to disable.

### Updating

- App: `docker-compose pull && docker-compose up -d`. Alembic migrations run on startup.
- Source CLIs (when a site changes): `cd cli_bins/<source> && /printing-press-reprint` → commit submodule SHA → rebuild image.

## Testing

```
tests/
├── unit/                # stats math, signal logic, dedup, config schema, normalizers
├── integration/
│   ├── conftest.py      # in-memory SQLite, frozen time, mocked notifiers
│   ├── sources/
│   │   ├── cassettes/   # vcr.py recordings per source
│   │   ├── test_bookfinder.py
│   │   ├── test_wob.py
│   │   └── test_amazon.py
│   ├── test_scheduler.py
│   ├── test_alert_pipeline.py
│   └── test_api.py      # httpx AsyncClient against FastAPI app
└── e2e/
    ├── docker-compose.test.yml
    └── test_smoke.py    # boot container, healthcheck, add ISBN, assert obs land
```

| Layer | Tools | What it proves |
|---|---|---|
| Unit | `pytest`, `hypothesis` | Stats edge cases (single observation, all-equal prices, missing shipping); dedup logic; config validation; ISBN normalization. Property tests for percentile correctness. |
| Integration | `pytest-asyncio`, `vcr.py`, `freezegun` | Each source produces correct `ObservationCandidate` from recorded HTTP. Scheduler fires on cron. Alert pipeline fires expected kinds. API endpoints return correct schema. |
| E2E | docker-compose + httpx | Container boots; healthcheck green; one synthetic book added end-to-end produces observations. |

VCR cassettes are the brittleness firewall: recorded once per source, refreshed deliberately when a site changes (CI failure → re-record + commit).

### Test fixtures

User-supplied ISBNs for cassette recording, integration tests, and hand testing. The mix of ISBN-10 (4) + ISBN-13 (1) is intentional — exercises the `isbnlib` normalisation path.

| ISBN (as given) | Form | Used for |
|---|---|---|
| `0241638194` | ISBN-10 | source happy-path |
| `100904852X` | ISBN-10 | trailing-X normalisation |
| `9789693531374` | ISBN-13 | non-Anglophone publisher prefix |
| `024147941X` | ISBN-10 | second happy-path |
| `0753560682` | ISBN-10 | third happy-path |

Pre-commit: `ruff`, `ty`, fast unit tests. Heavy integration tier runs locally on demand.

CI not required for MVP. Test layout maps directly to GitHub Actions later if desired.

## Observability

- `/api/health` — per-source dots, DB ping, scheduler heartbeat, last successful run per source.
- Logs in `data/logs/` (rotated daily) + stdout (Docker captures).
- Optional `/api/metrics` (Prometheus) — deferred.

## Implementation order (bootstrap sequence)

This sequence proves each layer end-to-end before the next:

1. **Skeleton** — uv-managed Python project, FastAPI hello-world, SQLModel + Alembic, one Book table, `/api/health` returning ok.
2. **Single InlineSource (WoB UK)** — 50-line Python+httpx scraper implementing `Source.fetch()`. Proves the orchestrator pipeline end-to-end without printing-press involvement.
3. **Scheduler + observations + view** — APScheduler in lifespan, `PriceObservation` writes from WoB, `book_stats` view, `compute_book_stats()` helper.
4. **Stats + signal + alerts (in-app only)** — recommendation logic, `Alert` rows, in-app notifier.
5. **Frontend baseline** — Vite + Tailwind + shadcn/ui scaffold, dashboard + add-book modal + book detail. OpenAPI-typed client.
6. **Bookfinder via printing-press** — `/printing-press` generates `bookfinder-pp-cli`; wrap in `BookfinderSource(SubprocessSource)`. Confirm parity with WoB pipeline.
7. **Amazon via printing-press** — same shape; condition-aware extraction.
8. **Push notifications — ntfy only at MVP**. Telegram and Pushover deferred; the `Notifier` interface is in place so they can land in a future iteration without core changes.
9. **Settings UI** — sources tab, recommendation tab, notifications tab, advanced (Monaco) tab.
10. **Multi-stage Dockerfile + compose + e2e smoke** — production-ready container.
11. **Backups, observability polish, README**.

(Optionally migrate WoB to `wob-pp-cli` via printing-press once the pattern is proven.)

## Open questions / explicitly deferred

- **camelcamelcamel RSS as redundant Amazon source.** Useful drop-event detector; per-product RSS feeds are public. Evaluate post-MVP based on Amazon-direct reliability.
- **Multi-region rollout.** Schema is ready; need per-source region resolution + UI selector. Likely sub-project after MVP.
- **Title/author search add-flow.** Partial OpenLibrary support at MVP for ISBN-pinned add; full bookfinder-style search-and-pick is a sub-project.
- **Cross-source dedup pass.** Schema field `is_duplicate_of` is in. Logic deferred until we observe real seller-name overlap data.
- **Bulk import / Amazon wishlist scrape.** Deferred.
- **Mark-as-bought retro analytics** — minimal columns are present (`status="bought"`, `bought_price_minor`); UI surface is post-MVP.

## Glossary

| Term | Meaning |
|---|---|
| ISBN-13 | 13-digit International Standard Book Number; canonical identifier for editions |
| ASIN | Amazon Standard Identification Number; per-marketplace item ID |
| Bookfinder | bookfinder.com — meta-search across many sellers |
| WoB | World of Books (wob.com) — UK used-book retailer |
| Keepa | keepa.com — Amazon price-history service (paid API; out of scope) |
| CCC | camelcamelcamel.com — Amazon price-tracker; RSS feeds public |
| printing-press | CLI generator at printingpress.dev; produces Go CLIs from API specs / HAR / URLs |
| pp-cli | printing-press-generated CLI |
| ntfy | ntfy.sh — simple HTTP-based pub/sub for push notifications |
