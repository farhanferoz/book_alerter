# scripts/

Operational and development scripts. Nothing here is imported by the
application — these are things you run by hand.

Everything writes to a path you pass in, or to `/tmp`. **No script here writes
into the repository except `capture_amazon_fixture.py`, and only into
`tests/fixtures/`.** Probe output, HTML dumps and benchmark results are scratch
and must not be committed.

| Script | Run it when | Needs |
|---|---|---|
| [`smoke_check.py`](#smoke_checkpy) | After any change, before any deploy | A **copy** of a real DB |
| [`bench_stats.py`](#bench_statspy) | Before/after touching the stats path | A **copy** of a real DB |
| [`capture_amazon_fixture.py`](#capture_amazon_fixturepy) | A parser broke, or you need a new fixture | Network + Playwright |
| [`start.sh`](#startsh) | Running the container locally | Docker |

---

## `smoke_check.py`

Fast end-to-end validation. Copies the database you point it at, migrates the
copy to head, boots the real FastAPI app in-process, exercises the real
endpoints, and asserts cross-cutting data invariants (no future-dated
observations, `total_minor` consistent with price + shipping, every
`current_best` offer real and live, `signal` a member of the `Signal` enum).
Prints a per-check table with millisecond timings and exits non-zero on any
failure.

```bash
uv run python scripts/smoke_check.py --db /path/to/copy-of-prod.db
```

About 8 seconds against the production-sized database (13 books, ~90k
observations). It is the "did I break the application" check that unit tests
cannot give you, because it runs against real data at real scale — it has
already caught a response-contract regression that the unit tests missed.

**Always give it a copy.** It migrates what it is given; it copies to a temp
directory first and leaves the input untouched, but do not point it at a live
file.

## `bench_stats.py`

Times the work `GET /api/books` does — select every active book, compute each
one's stats bundle — by calling the route handler directly, with no HTTP layer.

```bash
uv run python scripts/bench_stats.py /path/to/copy-of-prod.db
```

Use it as a before/after pair whenever you touch `stats.py`, the stats views,
or the list endpoints. Reference numbers on a 13-book production copy:
**~1.5 s before** the task-T3.1 restructure, target **≤ 0.35 s** after.

## `capture_amazon_fixture.py`

Captures a real Amazon UK (product page or offer listing) or Bookfinder page to
disk as a test fixture, rendered through the application's own rendering path
so the capture matches what the live scraper sees.

```bash
# Amazon product page + offer listing
uv run python scripts/capture_amazon_fixture.py --asin B09B96TG33 --kind both \
    --out tests/fixtures/amazon/products/

# Bookfinder search page
uv run python scripts/capture_amazon_fixture.py --source bookfinder \
    --id 9780747532699 --out tests/fixtures/bookfinder/
```

Writes `<id>-<region>-<kind>-<YYYY-MM-DD>.html` plus a sidecar `.json` holding
the per-row delivery markers (`data-csa-c-delivery-price` values and
`.aod-delivery-promise` text), which is what the shipping rules are written
against.

Notes:

- This script supersedes the three earlier one-shot capture scripts
  (`capture_amazon_dp.py`, `capture_amazon_offer_listing.py`,
  `capture_bookfinder.py`). Do not add a fourth — extend this one.
- `--postcode` only tags the output filename. Delivery-location pinning does
  **not** work for a logged-out headless session; this was measured and settled
  (see `docs/superpowers/plans/2026-09-04-wave0-probe-results.md`).
- Amazon may serve a bot challenge. The script exits non-zero rather than
  writing a challenge page as if it were a fixture.
- **Verify a fixture actually exhibits the thing you captured it for**, by
  counting DOM nodes rather than grepping the HTML. Marker words appear in
  scripts and CSS: one product page contains the string "twister" 260 times
  while matching `#twister` zero times.

## `start.sh`

Brings the container up locally and waits for it to report healthy.

```bash
scripts/start.sh            # build if needed, up -d, wait, smoke-test
scripts/start.sh logs       # follow logs
scripts/start.sh status     # health + recent log tail
scripts/start.sh restart
scripts/start.sh down
```

---

## Getting a database copy

The scripts above want a copy of the real database, never the live file:

```bash
rsync -a 'nasff235:/share/CACHEDEV1_DATA/Container/book_alerter/data/book_alerter.db*' /tmp/proddb/
```

## Pointing tooling at a specific database

Use the `BOOK_ALERTER_DATABASE_URL` environment variable:

```bash
BOOK_ALERTER_DATABASE_URL="sqlite:///tmp/proddb/book_alerter.db" uv run alembic upgrade head
```

**`alembic -x db_url=...` is silently ignored** — `env.py` calls
`get_database_url()`, which reads only that environment variable. Forgetting
this migrates the application's own `data/book_alerter.db` instead, and looks
exactly like a broken migration.
