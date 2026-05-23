# Products (non-book Amazon items) — Implementation Plan

> **Status:** In progress, 2026-05-23. **Predecessor:** `2026-05-09-book-alerter-implementation.md` (book MVP) is the spine this builds on; everything here is incremental.

**Goal:** Add a Products section to the app that tracks non-book Amazon products (ASIN-keyed) end-to-end — scrape, observe, percentile-rank, alert, notify — using the same machinery as books, with parallel tables and per-product condition scope. Adds a Keepa historical backfill on add so a freshly-added product immediately has chartable history.

**Locked decisions (from user, 2026-05-23):**
1. **Separate parallel tables** for the product side: `Product`, `ProductObservation`, `ProductAlert`, `ProductSignalState`. `NotificationDelivery` is shared (polymorphic FK).
2. **Configurable per-product condition scope.** Default new-only; per-product `track_used` toggle pulls used grades as well.
3. **Amazon UK + Keepa.** Amazon as live scraper; Keepa PNG backfill on product add (mirrors the books-side flow).
4. **Separate top-level `/products` tab** + detail page. Existing book dashboard untouched.

**Cross-cutting durable preferences (memory):**
- **No code duplication.** Where books and products share logic (parser, stats, alert pipeline, dispatcher, FE components), one implementation parameterized on the model class.
- **Prefer enum over string comparisons.** Shared typed string sets (Condition, AlertKind) migrate to `StrEnum`; new ones (ItemKind, ProductStatus) start there.
- **Real e2e tests, not smoke.** A new scenario walks the full lifecycle. Browser smoke via Playwright per the existing memory note.
- **No guesses.** Research/WebSearch where I'm uncertain (Amazon ASIN edge cases, Keepa endpoint quirks).

---

## Architecture at a glance

```
APScheduler
   ├── per-source job (item_kinds: [book])         → AlertPipeline(book_models)    ─┐
   └── per-source job (item_kinds: [book,product]) → AlertPipeline(product_models) ─┴── NotificationDispatcher ─┐
                                                                                                                 ├── InAppNotifier
                                                                                                                 └── NtfyNotifier

Book   ── PriceObservation     ── Alert         ─┐
                                                  ├── NotificationDelivery (exactly one of alert_id / product_alert_id is set; enforced by CHECK)
Product ── ProductObservation  ── ProductAlert  ─┘

Source ABC:  Source.fetch(item: TrackedItem) -> list[ObservationCandidate]
             where TrackedItem is a Protocol that both Book and Product satisfy.

Amazon source: one shared parser (_parse_amazon_dp_html, _parse_amazon_offer_listing_html, _merge_offers).
               AmazonBookSource  → wraps parser with track_used=True
               AmazonProductSource → wraps parser with track_used=product.track_used
```

---

## Overview of phases

| Phase | What it produces | Verifiable result |
|---|---|---|
| P0 | Enums, product models, migrations 0014/0015/0016, product_stats view | `alembic upgrade head` clean on fresh + populated DB; round-trip ORM tests pass |
| P1 | ASIN normalizer, generic Source ABC, shared Amazon parser, AmazonProductSource | One fixture-driven product fetch returns expected `ObservationCandidate`s |
| P2 | Generic stats engine, generic AlertPipeline (parameterised on models) | Product observation → ProductAlert; signal state writes; dispatcher fires |
| P3 | api/products.py CRUD + observations + refetch + stats + keepa + metadata/asin-lookup | All endpoints behind `/openapi.json`; integration tests pass |
| P4 | Scheduler iterates products; `SourceConfig.item_kinds` extension | Adding a product with the scheduler running produces observations on the next cycle |
| P5 | Frontend: shared ItemCard/ItemDetail/AddItemDialog; `/products` routes; productsApi client | Browser walkthrough: add product → list → detail → refetch → see signal pill |
| P6 | Dispatcher product formatting; scenario_07_product_lifecycle | scenario_07 PASS, `run_all.sh` 7/7 |
| P7 | Full test pipeline, tier-3 review, docs (RESUME / CHANGELOG / README) update | Zero ruff findings, all tests green, review report logged |

Each phase ends with a commit (sometimes two — implementation + tests). Phase boundaries are checkpoints — leave a clean note in RESUME at each.

---

## File map (delta over the existing tree)

```
src/book_alerter/
├── enums.py                           # NEW — single home for shared StrEnums
├── db/
│   ├── models.py                      # MOD — Condition→StrEnum; new Product et al.
│   ├── views.py                       # MOD — PRODUCT_STATS_VIEW_SQL alongside book_stats
│   └── migrations/versions/
│       ├── 0014_product_tables.py     # NEW
│       ├── 0015_notif_delivery_polymorphic.py  # NEW
│       └── 0016_product_stats_view.py # NEW
├── sources/
│   ├── base.py                        # MOD — TrackedItemProtocol; Source.fetch(item)
│   ├── normalizers.py                 # MOD — to_asin, amazon_uk_product_dp_url
│   ├── amazon.py                      # MOD — extract pure parser helpers
│   ├── amazon_product.py              # NEW — AmazonProductSource thin wrapper
│   └── registry.py                    # MOD — register amazon_product source
├── stats.py                           # MOD — generic _compute_stats helper
├── alerts.py                          # MOD — generic detect_alert_kinds (signal_state_model param)
├── notifications/
│   └── dispatcher.py                  # MOD — AlertPipeline takes model classes; formatter branches on kind
├── scheduler.py                       # MOD — _run_source dispatches per item_kind
├── config.py                          # MOD — SourceConfig.item_kinds: list[ItemKind]
├── api/
│   ├── products.py                    # NEW — mirror of books.py
│   ├── metadata.py                    # MOD — POST /api/metadata/asin-lookup
│   └── covers.py                      # MOD — generalize to images proxy OR add api/images.py
└── keepa.py                           # MOD — cache_path/fetch take ASIN directly

tests/
├── unit/
│   ├── test_enums.py                  # NEW
│   ├── sources/test_to_asin.py        # NEW
│   ├── sources/test_amazon_parser.py  # NEW (shared parser; existing book tests stay)
│   └── test_stats_generic.py          # NEW
├── integration/
│   ├── api/test_products_api.py       # NEW (mirror test_books_api.py)
│   ├── sources/test_amazon_product.py # NEW (VCR/fixture)
│   ├── test_product_pipeline.py       # NEW
│   ├── test_notif_delivery_polymorphic.py  # NEW
│   └── conftest.py                    # MOD — make_product fixture + product_engine_with_view
├── fixtures/amazon/                   # NEW — product dp + offer-listing real captures
└── scenarios/scenario_07_product_lifecycle.py  # NEW

web/src/
├── api/
│   ├── schema.ts                      # REGEN
│   ├── booksApi.ts                    # existing
│   └── productsApi.ts                 # NEW
├── components/
│   ├── items/                         # NEW — shared kind-agnostic
│   │   ├── ItemCard.tsx               # extracted from BookCard
│   │   ├── ItemDetail.tsx             # extracted from BookDetail
│   │   └── AddItemDialog.tsx          # extracted from AddBookDialog
│   ├── books/...                      # MOD — thin wrappers calling the generic
│   └── products/...                   # NEW — thin wrappers calling the generic
└── pages/
    ├── BooksDashboard.tsx             # MOD — was Dashboard.tsx
    └── ProductsDashboard.tsx          # NEW
```

---

## P0 — Foundation (data model + migrations)

**Goal:** Persist products and their observations alongside books, with NotificationDelivery polymorphic, no behavior change to existing book flows.

### P0a — Shared enums

Create `src/book_alerter/enums.py`:

```python
from __future__ import annotations
from enum import StrEnum

class Condition(StrEnum):
    NEW = "new"
    USED_VG = "used_vg"
    USED_G = "used_g"
    USED_ACCEPTABLE = "used_acceptable"
    UNKNOWN = "unknown"

class AlertKind(StrEnum):
    TARGET_HIT = "target_hit"
    PERCENTILE_CROSS = "percentile_cross"
    NEW_LOW = "new_low"

class ItemKind(StrEnum):
    BOOK = "book"
    PRODUCT = "product"

class ItemStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    BOUGHT = "bought"

class SourceRunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"

class NotificationDeliveryStatus(StrEnum):
    SENT = "sent"
    ERROR = "error"

class BookFormat(StrEnum):
    PAPERBACK = "paperback"
    HARDCOVER = "hardcover"
    ANY = "any"
```

`db/models.py` updates:
- `Condition` import moves to `book_alerter.enums`; the old `Literal` alias kept as `Condition = Condition` for now to avoid touching every existing import. (Migrate callers lazily as touched.)
- `Book.status` switches to `ItemStatus` (StrEnum), drops the `sa_column=Column(String, nullable=False)` workaround — `StrEnum` is a real `Enum`, SQLModel handles it.
- Same for `Alert.kind` (uses `AlertKind`), `SourceRun.status`, `NotificationDelivery.status`, `PriceObservation.condition`, `Book.format`.

`config.py` updates:
- `AlertKind` type alias swap (re-export from `enums`).
- `NotificationsConfig.alert_kinds_enabled: list[AlertKind]` continues to validate from JSON strings (StrEnum coerces).

**Backwards-compat check:** all existing YAML configs use plain strings like `"target_hit"` — StrEnum coerces these natively, no migration of YAML needed.

### P0b — Product models

`db/models.py`:

```python
class Product(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    asin: str = Field(unique=True, index=True)
    title: str
    image_url: str | None = None
    brand: str | None = None
    region: str = "UK"
    currency: str = "GBP"
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    percentile_window_days: int | None = None
    status: ItemStatus = Field(default=ItemStatus.ACTIVE)
    bought_price_minor: int | None = None
    notes: str | None = None
    alert_kinds_disabled: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    muted_until: datetime | None = None
    track_used: bool = False
    last_scrape_attempt_at: datetime | None = None
    last_scrape_error: str | None = None
    created_at: datetime
    updated_at: datetime

class ProductObservation(SQLModel, table=True):
    # mirror PriceObservation, FK on product.id w/ CASCADE
    ...

class ProductAlert(SQLModel, table=True):
    # mirror Alert, FK on product.id w/ CASCADE
    ...

class ProductSignalState(SQLModel, table=True):
    # mirror BookSignalState, FK on product.id w/ CASCADE
    ...
```

Same field types as the Book stack. Indexes mirror PriceObservation. `is_duplicate_of` stays self-referential (NO ACTION) per the books-side decision in migration 0013.

### P0c — Migration 0014 (product tables)

`alembic revision --autogenerate -m "product tables"`, then hand-clean:

- Naming convention block at module top: `naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}` (matches migration 0013).
- All FKs on child tables get `ondelete="CASCADE"`.
- `downgrade()` drops in reverse FK order.

### P0d — Migration 0015 (NotificationDelivery polymorphic)

Required because product alerts also need delivery rows. SQLite can't alter columns or add CHECK constraints in place — must rebuild the table.

```python
# Pattern mirrors migration 0013 (drop view, recreate table, copy rows, recreate view).
# Steps:
#   1. ALTER alert.alert_id to nullable (rebuild via batch_alter_table)
#   2. ADD product_alert_id INTEGER NULL with FK + CASCADE
#   3. ADD CHECK ((alert_id IS NULL) != (product_alert_id IS NULL))
```

`db/views.py` doesn't reference NotificationDelivery so no view drop needed here. Verify with `PRAGMA foreign_key_check` after upgrade.

### P0e — Migration 0016 (product_stats view)

`PRODUCT_STATS_VIEW_SQL` in `db/views.py` mirrors `BOOK_STATS_VIEW_SQL` line-for-line with table-name substitution. Migration installs it in upgrade, drops in downgrade.

### P0f — Tests

`tests/unit/test_enums.py`:
- Every enum value equals its previous Literal string (wire-format invariant).
- `Condition.NEW == "new"` (StrEnum string comparison).
- `ItemStatus("archived")` round-trips.

`tests/integration/test_migrations.py` (already exists if there's coverage; otherwise create):
- `alembic upgrade head` on an empty fresh DB and on a populated copy of the dev DB.
- `PRAGMA foreign_key_check` returns no violations after each.
- Downgrade 0016→0013 reverses cleanly.

`tests/integration/test_notif_delivery_polymorphic.py`:
- Insert with `alert_id` only → OK.
- Insert with `product_alert_id` only → OK.
- Insert with both → IntegrityError on CHECK.
- Insert with neither → IntegrityError on CHECK.

**Commit boundary:** P0a+b in one commit (model layer); 0c+0d+0e in one commit (migrations); 0f tests in one commit. Three commits.

---

## P1 — Sources

**Goal:** AmazonProductSource fetches a Product's offers via the same parser the book source uses, with shared `Source.fetch(item)` signature.

### P1a — ASIN normalizer

`sources/normalizers.py` additions:
- `_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")`.
- `to_asin(raw: str) -> str`:
  - Strips whitespace; upper-cases; if it matches `_ASIN_RE`, return as-is.
  - Otherwise URL-parse and look for `/dp/<asin>`, `/gp/product/<asin>`, `/gp/aw/d/<asin>`, accepting any TLD (.co.uk, .com, .de).
  - Validates the recovered token against `_ASIN_RE`; raises `ValueError` otherwise.
- `amazon_uk_product_dp_url(asin: str) -> str`: `f"https://www.amazon.co.uk/dp/{asin}"`.
- The existing `amazon_uk_dp_url(isbn13)` is renamed in spirit (it already does `asin_for_amazon_uk(isbn13)`), but stays for back-compat.

### P1b — Generic Source ABC

`sources/base.py`:

```python
class TrackedItem(Protocol):
    id: int | None
    region: str
    currency: str
    last_scrape_error: str | None
    last_scrape_attempt_at: datetime | None
    @property
    def identifier(self) -> str: ...

class Source(ABC):
    name: str
    item_kinds: ClassVar[set[ItemKind]]   # which TrackedItem kinds this Source handles
    @abstractmethod
    async def fetch(self, item: TrackedItem) -> list[ObservationCandidate]: ...
```

Book and Product both expose `.identifier` (Book returns `self.isbn13`, Product returns `self.asin`). Add the property methods.

Existing book Source impls update:
- `fetch(self, book: Book)` → `fetch(self, item: TrackedItem)`; rename local `book` → `item`. Books-only sources (WoB, Bookfinder) declare `item_kinds = {ItemKind.BOOK}`.

### P1c — Shared Amazon parser

`sources/amazon.py` refactor:

- Extract `_parse_amazon_dp_html(html: str, *, track_used: bool) -> list[ObservationCandidate]` — current dp-page parsing, with `track_used` controlling whether used grades make it through.
- Extract `_parse_amazon_offer_listing_html(html: str, *, track_used: bool) -> list[ObservationCandidate]`.
- `_merge_offers` already pure; takes no change.
- Existing `AmazonBookSource.fetch` becomes a thin wrapper calling the parsers with `track_used=True` (current behavior is implicitly all-conditions).
- `_render_dp` / `_render_offer_listing` stay private to the module but are reused by AmazonProductSource via a `_fetch_for_asin(asin, *, track_used)` orchestrator helper.

### P1d — AmazonProductSource

`sources/amazon_product.py`:

```python
class AmazonProductSource(Source):
    name = "amazon_uk_product"
    item_kinds = {ItemKind.PRODUCT}

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None: ...

    async def fetch(self, item: TrackedItem) -> list[ObservationCandidate]:
        product = item  # narrow via runtime check
        return await _fetch_for_asin(product.asin, track_used=product.track_used)
```

Registry update in `sources/registry.py` adds `"amazon_uk_product": AmazonProductSource`.

### P1e — Tests

`tests/unit/sources/test_to_asin.py`: ASIN variants, URL variants, garbage rejection.
`tests/unit/sources/test_amazon_parser.py`: parametrize the shared parser over fixtures with `track_used` true/false — asserts used grades drop out when false.
`tests/integration/sources/test_amazon_product.py`: VCR fixture for a known product ASIN. Live canary skipped by default, gated on `AMAZON_PRODUCT_LIVE=1`.

**Commit boundary:** P1a+b in one commit; P1c+d in one commit; P1e in one commit. Three commits.

---

## P2 — Stats + alerts (generic engine)

**Goal:** One stats function, one alert pipeline, parameterised on model classes. No duplication.

### P2a — Generalize stats

`stats.py`:

```python
def _compute_stats(
    session: Session,
    item_id: int,
    *,
    observation_model: type[PriceObservation | ProductObservation],
    window_days: int,
    source_seller_global_medians: dict[tuple[str, str], int] | None = None,
    default_shipping_minor: int,
    min_global_median_observations: int,
) -> BookStats:  # name kept; output dataclass is kind-agnostic
    ...

def compute_book_stats(book_id, session, window_days, **kw) -> BookStats:
    return _compute_stats(session, book_id, observation_model=PriceObservation, ...)

def compute_product_stats(product_id, session, window_days, **kw) -> BookStats:
    return _compute_stats(session, product_id, observation_model=ProductObservation, ...)

def source_seller_global_shipping_medians(
    session,
    *,
    observation_model: type[PriceObservation | ProductObservation] = PriceObservation,
    min_observations: int,
) -> dict[tuple[str, str], int]:
    ...
```

`BookStats` dataclass rename considered but **deferred** — touches too many call sites. We document that `compute_product_stats` returns the same shape and `book_id` field is reused as `item_id`. Minor cosmetic issue documented in RESUME's working-agreements section.

### P2b — Generalize AlertPipeline

`notifications/dispatcher.py`:

```python
class AlertPipeline:
    def __init__(
        self,
        item_model: type[Book | Product],
        observation_model: type[PriceObservation | ProductObservation],
        alert_model: type[Alert | ProductAlert],
        signal_state_model: type[BookSignalState | ProductSignalState],
        stats_fn: Callable[..., BookStats],
        notifiers: list[Notifier],
        ...
    ) -> None: ...
```

`create_app` instantiates two pipelines and selects by item kind at `.run()` time. Per-item lock map keyed on `(ItemKind, item_id)`.

`alerts.py::detect_alert_kinds`: take `signal_state_model` as parameter; unchanged logic.

### P2c — Tests

`tests/integration/test_product_pipeline.py`:
- Seed a product, seed observations, run pipeline → ProductAlert + ProductSignalState rows exist; in-app notifier receives the alert; quiet hours suppress correctly.
- Dual-kind concurrency test: book + product processed concurrently without interference.

**Commit boundary:** P2a alone (touches stats); P2b+c in one commit (pipeline + tests). Two commits.

---

## P3 — API surface

### P3a — api/products.py

Mirror `api/books.py`. Endpoints:
- `GET /api/products` (excludes archived by default; `?include_archived=true`)
- `POST /api/products` — accepts `{asin_or_url, title, image_url?, brand?, target_price_minor?, percentile_threshold?, percentile_window_days?, notes?, track_used?}`. `to_asin` normalizes the input. 409 on duplicate.
- `GET /api/products/{id}`
- `PATCH /api/products/{id}` — patch any of `target_price_minor`, `percentile_threshold`, `percentile_window_days`, `status`, `muted_until`, `notes`, `alert_kinds_disabled`, `track_used`.
- `DELETE /api/products/{id}` — soft by default; `?hard=true` cascades via FK.
- `GET /api/products/{id}/observations` — same cursor-paginated shape as books.
- `POST /api/products/{id}/refetch` — fans out across configured product sources.
- `GET /api/products/{id}/stats` — `BookStats` mirror.
- `POST /api/products/{id}/keepa-backfill` (see P3b).
- `GET /api/products/{id}/keepa-chart.png` (see P3b).
- `GET /api/products/{id}/image` — proxy for `product.image_url` mirroring `/api/covers/{isbn13}`.

DTOs `ProductOut`, `ProductCreate`, `ProductPatch`, `ProductStatsOut` defined here.

### P3b — Keepa for products

`keepa.py` refactor (small):
- `cache_path(cache_dir, asin, range_days) -> Path` — accepts ASIN directly.
- `fetch_chart_png(asin, ...)` — same.
- Existing `cache_path(cache_dir, isbn13, range_days)` callers in books API now compute the ASIN themselves first.

Per-product endpoints:
- `POST /api/products/{id}/keepa-backfill` — invokes `_keepa_backfill_blocking(product_id, asin, session_factory, *, observation_model=ProductObservation)` — same helper as books, model-parameterised.
- `GET /api/products/{id}/keepa-chart.png` — proxies the PNG.

Auto-backfill on `POST /api/products` mirrors books-side BackgroundTasks call.

### P3c — POST /api/metadata/asin-lookup

`api/metadata.py`:
- Accepts `{input: str}` (ASIN or URL).
- Calls `to_asin`, then runs a one-shot Playwright dp-page render.
- Extracts `<title>` text (strips trailing " : Amazon.co.uk: ..."), `#landingImage` `src`, `#bylineInfo` text → returns `{asin, title, image_url, brand}`.
- 502 on render failure; 422 on bad input.

Time budget: 10s. No retries.

### P3d — API tests

Mirror `tests/integration/api/test_books_api.py` for products: every endpoint, 404/409/422 paths, refetch fan-out + skip-on-disabled, Keepa backfill stub.

**Commit boundary:** P3a in one commit; P3b in one commit; P3c in one commit; P3d in one commit. Four commits.

---

## P4 — Scheduler + config

`config.py`:

```python
class SourceConfig(BaseModel):
    enabled: bool = True
    region: str = "UK"
    schedule: str = "0 */6 * * *"
    jitter_seconds: int = 600
    per_book_delay_seconds: tuple[int, int] = (5, 15)
    concurrency: int = Field(default=1, ge=1, le=5)
    timeout_seconds: int = 60
    max_consecutive_errors: int = 5
    item_kinds: list[ItemKind] = Field(default_factory=lambda: [ItemKind.BOOK])
```

Default `[BOOK]` preserves current behavior. Existing YAML configs validate unchanged.

`scheduler.py`:
- `_run_source(name, source, ...)` now:
  - Pull the configured `item_kinds` for this source.
  - For each kind, query the corresponding item table (`Book` / `Product`) WHERE `status='active'`.
  - Iterate items respecting the existing per-book-delay + concurrency knobs (rename to `per_item_delay_seconds` for honesty; back-compat parse `per_book_delay_seconds` for existing configs).
  - Per-item lock keyed on `(ItemKind, item_id)`.
- Each source instance only handles items in `item_kinds ∩ source.item_kinds` (intersection).

`.env.example` documents the new field.

**Commit boundary:** one commit.

---

## P5 — Frontend

### P5a — Schema regen + productsApi

```bash
cd web && npm run gen:api
```

`web/src/api/productsApi.ts` — mirror `booksApi.ts` shape using `apiGet/Post/Patch/Delete<P>` against the generated paths. No `any`.

### P5b — Shared item components

Extract from books-side:
- `<ItemCard>` (was `<BookCard>`) — takes `kind`, `identifier`, `title`, `subtitle` (author or brand), `imageSrc`, `stats`. Used by both.
- `<ItemDetail>` — same lifting; track-used toggle is product-only (passed as a prop slot).
- `<AddItemDialog>` — takes `kind`, identifier label, lookup endpoint. Books pass `"ISBN"` + `/api/metadata/lookup`; products pass `"ASIN or URL"` + `/api/metadata/asin-lookup`.

`web/src/components/books/*` and `products/*` become thin wrappers that pass the right config. **Kill drift at the source** (memory directive).

### P5c — Routes + nav

`web/src/App.tsx`:
- New route `/products` → `ProductsDashboard`.
- New route `/products/:id` → `ProductDetail`.
- Top nav adds "Products" tab.

`ProductsDashboard.tsx`: a 30-line file that calls `ItemDashboard` with `productsApi` + `kind='product'`. `BooksDashboard.tsx` is the same with the other API.

### P5d — Browser smoke

Per the `feedback_ui_verify_in_browser` memory:
- Start dev server inside the container.
- Use the in-container Playwright to:
  - Navigate to `/products`.
  - Click "Add product"; paste a known ASIN; confirm title + image are auto-filled.
  - Submit; confirm the new row appears.
  - Click in; confirm Refetch triggers and observations populate.
  - Confirm a target_price alert renders correctly.
- Capture screenshots into `/tmp/products-screens/`.

**Commit boundary:** P5a alone; P5b alone; P5c+d in one commit. Three commits.

---

## P6 — Notifications + scenario

### P6a — Dispatcher

`notifications/dispatcher.py`:
- The shared in-app + ntfy formatters branch on `isinstance(alert, ProductAlert)` (or via a kind discriminator) to pick the title prefix ("📚 Book:" vs "📦 Product:") and the URL (book detail vs product detail).
- `NotificationDelivery` write inspects alert type and sets the right FK column.

### P6b — scenario_07

`tests/scenarios/scenario_07_product_lifecycle.py`:
- Phase A: Add product via `POST /api/products`. Mock Keepa to return a tiny price history (5 days, range £10-£20).
- Phase B: Inject a current-best observation at £8 → triggers `target_hit` AND `new_low`.
- Phase C: Assert ProductAlert row exists, NotificationDelivery row exists for each enabled channel (in-app + ntfy-stub), inapp feed lists the alert.
- Phase D: Mute the product; new observation triggers no new alert. Lift mute; next observation at lower price fires `new_low`.
- Frozen clock via freezegun matching the pattern from scenario_01.

Added to `tests/scenarios/run_all.sh`.

**Commit boundary:** P6a in one commit; P6b in one commit. Two commits.

---

## P7 — Validation, review, docs

### P7a — Full test pipeline

```bash
cd /home/ff235/dev/book_alerter
uv run pytest -q                    # expect: > 350 passed, ≤4 skipped
bash tests/scenarios/run_all.sh     # expect: ALL SCENARIOS PASS (7/7)
uv run ruff check ./src ./tests     # expect: zero findings
uv run alembic upgrade head         # expect: head = 0016_product_stats_view
cd web && npx tsc --noEmit && npx eslint . && npm run build
```

Any failure → fix BEFORE review. No `--no-verify`, no skipped tests.

### P7b — Tier-3 review

Per CLAUDE.md project: 16+ commits and public API/on-disk format → Tier-3 floor.

Sequence (handled by `/tier-review` skill):
1. `simplify` first (single pass, end-to-end).
2. Parallel fan-out: `differential-review`, `find-bugs`, `security-review`, test-coverage audit, conventions audit.
3. `fp-check` against any flagged bugs.
4. `/second-opinion` cold external review.
5. Adversarial pass: re-read the diff with "what's the worst that could happen" hat on.

Findings logged into `RESUME.md` deferred-followups if accepted as design; fixed-in-place otherwise.

### P7c — Docs

- `RESUME.md`: status line updated, "What ships" expanded with Products section, "Test layers" includes the new scenarios, "Key files" section adds product-relevant paths, "Working agreements" notes Amazon source is dual-purpose + Condition/AlertKind are now StrEnums + SourceConfig.item_kinds exists.
- `docs/CHANGELOG.md`: per-commit log of the products feature.
- `README.md`: short "Products" section with `curl` examples for the new endpoints; "What it tracks" expanded.
- This plan doc: mark `Status: complete` at the top with the commit hash range.

---

## Risks + watchouts (carried into review)

1. **Amazon anti-bot exposure doubles.** Each pipeline cycle now hits Amazon for `N_books + N_products` ASINs. Per-source `max_consecutive_errors` gate already exists; we should think about a per-kind concurrency cap if needed (deferred until observed).
2. **NotificationDelivery polymorphic FK.** CHECK constraint approach; downstream queries need `coalesce(alert_id, product_alert_id)` patterns. Verified by the polymorphic test in P0f.
3. **Stats refactor blast radius.** Generalizing `compute_book_stats` to take model classes touches every test that constructs stats; the dataclass naming (`BookStats`, `book_id`) is deliberately not renamed to keep the diff small. Document in RESUME.
4. **Per-item delay knob rename.** `per_book_delay_seconds` → `per_item_delay_seconds`. Back-compat parser reads the old key if present; doc in CHANGELOG.
5. **Keepa for non-book ASINs.** Keepa indexes Amazon products broadly, not just books. Verify against ≥1 real non-book ASIN before claiming this works.
6. **Project rename DEFERRED.** Package stays `book_alerter`. README + UI surface "Book Alerter — Books & Products" cosmetically; full rename is a separate follow-up.

---

## Out of scope (this plan)

- Other retailers (eBay, Argos, Currys, etc.). The architecture supports them, but MVP is Amazon + Keepa only.
- Bulk import of products (CSV, etc.).
- Product variants (parent ASIN → child variants). Each ASIN is its own tracker.
- Product categorization / tags. Add later if needed.
- Telegram / Pushover notifier slots (still deferred from book MVP).
- Public auth changes — products inherit the same optional HTTP Basic gate.
