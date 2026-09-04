"""Fast end-to-end smoke check against a production DB copy.

Usage:  uv run python scripts/smoke_check.py --db <copy-of-prod-db>

Copies `--db` (and its `-wal`/`-shm` siblings, if present) into a temp dir,
runs `alembic upgrade head` against the copy, boots the real FastAPI app
in-process (`TestClient`, no network, no browser) against it, and exercises
the real endpoints the dashboard depends on. Every configured source is
disabled and the weekly backup job is turned off for the run — see
`_write_harness_config` — so `Scheduler.start()` registers zero APScheduler
jobs and nothing fires in the background while the checks run.

Never touches the file passed via `--db`: it is read once (copy), never
opened for writing. Meant to run in well under 60s after every wave of an
autonomous implementation, so it deliberately skips anything that would
freeze current pricing bugs (see the invariant checks below) — it asserts
structure, health, and timing, not pricing behaviour.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
import typing
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

if typing.TYPE_CHECKING:
    from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- Check plumbing ----------------------------------------------------------


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


class CheckFailed(Exception):
    """Raised by a `Check.runner` to report a definite failure."""


class CheckSkipped(Exception):
    """Raised by a `Check.runner` when the check can't run against the
    current schema/data — a prerequisite check didn't produce what it
    needed, or a column this check depends on isn't present (schema
    evolution). SKIP, not FAIL: a harness that reds out on every migration
    is a failed harness."""


@dataclass
class Check:
    name: str
    runner: Callable[[], str]


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str
    elapsed_ms: float


def run_check(check: Check) -> CheckResult:
    start = time.perf_counter()
    try:
        detail = check.runner()
        status = CheckStatus.PASS
    except CheckSkipped as exc:
        status, detail = CheckStatus.SKIP, str(exc)
    except CheckFailed as exc:
        status, detail = CheckStatus.FAIL, str(exc)
    except Exception as exc:
        # A check that blows up unexpectedly is a FAIL, not a crashed script.
        status, detail = CheckStatus.FAIL, f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - start) * 1000
    return CheckResult(name=check.name, status=status, detail=detail, elapsed_ms=elapsed_ms)


def _assert_status(resp, expected: int = 200) -> None:
    if resp.status_code != expected:
        raise CheckFailed(f"expected HTTP {expected}, got {resp.status_code}: {resp.text[:300]}")


# --- Setup: copy DB, disable background jobs, migrate -----------------------


def _copy_db(src: Path, dest_dir: Path) -> Path:
    """Copy `src` (and its `-wal`/`-shm` siblings, if present) into
    `dest_dir`. `src` itself is only ever opened for reading."""
    dest = dest_dir / src.name
    for suffix in ("", "-wal", "-shm"):
        sibling = src.with_name(src.name + suffix)
        if sibling.exists():
            shutil.copy2(sibling, dest_dir / sibling.name)
    return dest


def _write_harness_config(config_cls, cfg_path: Path) -> None:
    """Write a `Config` with every source, the weekly backup and the janitor disabled.

    `Scheduler.start()` only registers a cron job for a source when
    `SourceConfig.enabled` is True (`src/book_alerter/scheduler.py`), only
    registers the backup job when `BackupConfig.enabled` is True, and only
    registers the janitor when `JanitorConfig.enabled` is True. With every
    source disabled, `build_sources()` also returns an empty dict, so no
    Playwright/httpx source objects are constructed. There is no separate
    "test mode" flag; those three `enabled` fields are the switch.

    The janitor is disabled here for a concrete reason, not for symmetry:
    its sweeps derive every path from `db_path.parent`, and this harness
    points the app at a COPY of the production database inside a temporary
    directory, so an enabled janitor would sweep that temp directory.

    NOT disabled, because it cannot be: `metadata_refresh` is registered
    unconditionally (`scheduler.py`, T4.1 — "gating it behind config would
    mean a user could disable the only thing that ever resolves a PENDING
    row"). It is an `IntervalTrigger(minutes=30)`, so its first fire is
    start+30 minutes and no smoke run comes close — the whole check runs in
    ~8 seconds. That is a timing argument, not a guarantee: a smoke run held
    open past 30 minutes WOULD drive a real Playwright session against live
    Amazon for any product row whose metadata lookup is due. Do not hold
    this harness open.
    """
    cfg = config_cls()
    disabled_sources = {
        name: sc.model_copy(update={"enabled": False}) for name, sc in cfg.sources.items()
    }
    cfg = cfg.model_copy(
        update={
            "sources": disabled_sources,
            "backup": cfg.backup.model_copy(update={"enabled": False}),
            "janitor": cfg.janitor.model_copy(update={"enabled": False}),
        }
    )
    cfg.save(cfg_path)


def _run_migrations(db_path: Path) -> None:
    """Run `alembic upgrade head` against `db_path`.

    Reuses the pattern from `tests/integration/test_migrations.py`'s
    `_alembic_pointing_at`: point `env.py`'s DB URL at the copy via
    `BOOK_ALERTER_DATABASE_URL` (set by the caller before this runs) and
    drive the Python API directly rather than shelling out.
    """
    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig

    migrations_dir = REPO_ROOT / "src" / "book_alerter" / "db" / "migrations"
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(migrations_dir))
    alembic_command.upgrade(cfg, "head")


def _allowed_signal_values(signal_type: object) -> set[str]:
    """Derive the allowed `Signal` wire values from the app's own
    definition, not a hardcoded list — `Signal` is currently a
    `typing.Literal[...]` alias in `stats.py` but is expected to become a
    `StrEnum`; this works against either shape."""
    if isinstance(signal_type, type) and issubclass(signal_type, Enum):
        return {member.value for member in signal_type}
    args = typing.get_args(signal_type)
    if not args:
        raise CheckSkipped(f"could not derive allowed Signal values from {signal_type!r}")
    return set(args)


# --- Endpoint checks ----------------------------------------------------------

_REQUIRED_BOOK_KEYS = {"id", "isbn13", "title", "author", "status", "stats"}
_REQUIRED_PRODUCT_KEYS = {"id", "asin", "title", "status", "stats"}
_REQUIRED_STATS_KEYS = {"signal", "current_best_total_minor", "observation_count", "windows"}
# The alerts feed spans books and products, so a row identifies its item by
# (item_kind, item_id) rather than a book_id, and carries its own title.
_REQUIRED_ALERT_KEYS = {
    "id", "item_kind", "item_id", "title", "kind", "price_minor", "fired_at",
}
_REQUIRED_SOURCE_KEYS = {"name", "config", "last_run"}


def check_health(client: TestClient) -> str:
    resp = client.get("/api/health")
    _assert_status(resp)
    data = resp.json()
    if data.get("status") != "ok":
        raise CheckFailed(f"health status={data.get('status')!r}, errors={data.get('errors')}")
    return f"config_version={data.get('config_version')}"


def check_list_books(client: TestClient, ctx: dict[str, object]) -> str:
    resp = client.get("/api/books")
    _assert_status(resp)
    books = resp.json()
    ctx["books"] = books
    if not books:
        raise CheckFailed(
            "books list is empty — expected a non-empty result against a prod DB copy"
        )
    for b in books:
        missing = _REQUIRED_BOOK_KEYS - b.keys()
        if missing:
            raise CheckFailed(f"book {b.get('id')} missing key(s): {sorted(missing)}")
        stats_missing = _REQUIRED_STATS_KEYS - b["stats"].keys()
        if stats_missing:
            raise CheckFailed(f"book {b.get('id')} stats missing key(s): {sorted(stats_missing)}")
    # Pick the book with the most observations for the detail checks below —
    # maximises the odds that /observations has rows to assert on.
    ctx["book_id"] = max(books, key=lambda b: b["stats"]["observation_count"])["id"]
    return f"{len(books)} book(s)"


def check_list_products(client: TestClient, ctx: dict[str, object]) -> str:
    resp = client.get("/api/products")
    _assert_status(resp)
    products = resp.json()
    ctx["products"] = products
    for p in products:
        missing = _REQUIRED_PRODUCT_KEYS - p.keys()
        if missing:
            raise CheckFailed(f"product {p.get('id')} missing key(s): {sorted(missing)}")
    # An empty list is CORRECT on a prod copy that tracks no products —
    # do not treat it as degenerate.
    return f"{len(products)} product(s)"


def check_list_alerts(client: TestClient) -> str:
    resp = client.get("/api/alerts")
    _assert_status(resp)
    data = resp.json()
    if "items" not in data or "next_before" not in data:
        raise CheckFailed(f"unexpected alerts page shape: {sorted(data.keys())}")
    for a in data["items"]:
        missing = _REQUIRED_ALERT_KEYS - a.keys()
        if missing:
            raise CheckFailed(f"alert {a.get('id')} missing key(s): {sorted(missing)}")
    kinds = sorted({a["item_kind"] for a in data["items"]})
    return f"{len(data['items'])} alert(s) across {kinds or ['-']}"


def check_list_sources(client: TestClient) -> str:
    resp = client.get("/api/sources")
    _assert_status(resp)
    sources = resp.json()
    if not sources:
        raise CheckFailed("sources list is empty — expected the configured source registry")
    for s in sources:
        missing = _REQUIRED_SOURCE_KEYS - s.keys()
        if missing:
            raise CheckFailed(f"source {s.get('name')} missing key(s): {sorted(missing)}")
    return f"{len(sources)} source(s)"


def check_book_detail(client: TestClient, ctx: dict[str, object]) -> str:
    book_id = ctx.get("book_id")
    if book_id is None:
        raise CheckSkipped("no book_id selected — GET /api/books didn't complete")
    resp = client.get(f"/api/books/{book_id}")
    _assert_status(resp)
    data = resp.json()
    if data.get("id") != book_id:
        raise CheckFailed(f"expected id={book_id}, got {data.get('id')}")
    missing = _REQUIRED_BOOK_KEYS - data.keys()
    if missing:
        raise CheckFailed(f"missing key(s): {sorted(missing)}")
    return f"book_id={book_id} title={data.get('title')!r}"


def check_book_stats(client: TestClient, ctx: dict[str, object], allowed_signals: set[str]) -> str:
    book_id = ctx.get("book_id")
    if book_id is None:
        raise CheckSkipped("no book_id selected — GET /api/books didn't complete")
    resp = client.get(f"/api/books/{book_id}/stats")
    _assert_status(resp)
    data = resp.json()
    missing = _REQUIRED_STATS_KEYS - data.keys()
    if missing:
        raise CheckFailed(f"missing key(s): {sorted(missing)}")
    signal = data.get("signal")
    if signal is None:
        raise CheckFailed("expected a non-null signal from a book-scoped /stats call")
    if signal not in allowed_signals:
        raise CheckFailed(f"signal {signal!r} not in {sorted(allowed_signals)}")
    return f"book_id={book_id} signal={signal} observation_count={data.get('observation_count')}"


def check_book_observations(client: TestClient, ctx: dict[str, object]) -> str:
    book_id = ctx.get("book_id")
    if book_id is None:
        raise CheckSkipped("no book_id selected — GET /api/books didn't complete")
    resp = client.get(f"/api/books/{book_id}/observations")
    _assert_status(resp)
    data = resp.json()
    if "items" not in data or "next_before" not in data:
        raise CheckFailed(f"unexpected observations page shape: {sorted(data.keys())}")
    if not data["items"]:
        raise CheckFailed(
            f"book {book_id} was selected for having the most observations, "
            "but /observations returned none"
        )
    return f"book_id={book_id} {len(data['items'])} observation(s) on page 1"


# --- Cross-cutting DB invariants ---------------------------------------------
#
# These query the copied+migrated DB directly through the running app's own
# engine (`app.state.engine`), using stable model table/column names — never
# the `book_stats`/`product_stats` views, which a later wave replaces with
# `book_live_offers`/`product_live_offers`. `is_duplicate_of` is checked for
# presence via `hasattr` on the live model class before use, since a later
# wave (migration 0021) drops it in favour of a `last_seen_at` column.


@dataclass(frozen=True)
class _ItemKindSpec:
    label: str
    list_path: str
    observation_model: type
    item_fk_attr: str


def check_no_future_observations(session: Session, specs: Sequence[_ItemKindSpec]) -> str:
    parts = []
    violations = []
    for spec in specs:
        table = spec.observation_model.__tablename__
        total = session.exec(text(f"SELECT COUNT(*) FROM {table}")).one()[0]
        future = session.exec(
            text(f"SELECT COUNT(*) FROM {table} WHERE observed_at > datetime('now')")
        ).one()[0]
        parts.append(f"{spec.label}: {future}/{total} future-dated")
        if future:
            violations.append(f"{spec.label} has {future} observation(s) dated in the future")
    if violations:
        raise CheckFailed("; ".join(violations))
    return "; ".join(parts)


def check_total_minor_consistency(session: Session, specs: Sequence[_ItemKindSpec]) -> str:
    """`total_minor == price_minor + shipping_minor` for rows with an
    OBSERVED shipping value. Rows with `shipping_minor IS NULL` are
    deliberately excluded: at ingestion (`scheduler.py::_persist`) their
    stored `total_minor` currently falls back to `price_minor + 0`, which
    is exactly the "unknown shipping ranks as free" behaviour Wave 2 is
    fixing — asserting on those rows would freeze that bug into this
    harness. The observed-shipping subset checked here is a pure arithmetic
    invariant, unaffected by that fix.
    """
    parts = []
    violations = []
    for spec in specs:
        table = spec.observation_model.__tablename__
        checked = session.exec(
            text(f"SELECT COUNT(*) FROM {table} WHERE shipping_minor IS NOT NULL")
        ).one()[0]
        bad = session.exec(
            text(
                f"SELECT COUNT(*) FROM {table} WHERE shipping_minor IS NOT NULL "
                "AND total_minor != price_minor + shipping_minor"
            )
        ).one()[0]
        parts.append(f"{spec.label}: {checked} row(s) with observed shipping, {bad} inconsistent")
        if bad:
            violations.append(
                f"{spec.label}: {bad} row(s) where total_minor != price_minor + shipping_minor"
            )
    if violations:
        raise CheckFailed("; ".join(violations))
    return "; ".join(parts)


def check_current_best_offers_exist(
    client: TestClient, session: Session, specs: Sequence[_ItemKindSpec]
) -> str:
    """Every non-null `current_best_url`/`current_best_source` on a
    book/product's stats must be backed by an actual, non-duplicate
    observation row for that item — i.e. the "best offer" the dashboard
    shows isn't a dangling reference. Existence only: this does not assert
    that the SELECTED offer is the cheapest or correctly ranked (that's the
    behaviour Wave 2 is fixing)."""
    parts = []
    missing: list[str] = []
    checked_total = 0
    for spec in specs:
        resp = client.get(spec.list_path)
        _assert_status(resp)
        table = spec.observation_model.__tablename__
        has_dup_col = hasattr(spec.observation_model, "is_duplicate_of")
        dup_clause = "AND is_duplicate_of IS NULL" if has_dup_col else ""
        n_checked = 0
        for item in resp.json():
            stats = item["stats"]
            url = stats.get("current_best_url")
            source = stats.get("current_best_source")
            if url is None or source is None:
                continue
            condition = stats.get("current_best_condition")
            cond_clause = "AND condition = :condition" if condition is not None else ""
            params = {"item_id": item["id"], "source": source, "url": url}
            if condition is not None:
                params["condition"] = condition
            row = session.exec(
                text(
                    f"SELECT COUNT(*) FROM {table} WHERE {spec.item_fk_attr} = :item_id "
                    f"AND source = :source AND url = :url {cond_clause} {dup_clause}"
                ).bindparams(**params)
            ).one()
            n_checked += 1
            if row[0] == 0:
                missing.append(f"{spec.label} {item['id']} ({source} {url!r})")
        checked_total += n_checked
        parts.append(f"{spec.label}: {n_checked} current_best reference(s) checked")
    if checked_total == 0:
        raise CheckSkipped("no item currently has a current_best offer to verify")
    if missing:
        raise CheckFailed(
            f"{len(missing)} current_best reference(s) with no matching observation row: "
            f"{missing[:5]}"
        )
    return "; ".join(parts)


def check_signal_membership(client: TestClient, allowed_signals: set[str]) -> str:
    seen: set[str] = set()
    for path in ("/api/books", "/api/products"):
        resp = client.get(path)
        _assert_status(resp)
        for item in resp.json():
            signal = item["stats"]["signal"]
            if signal is not None:
                seen.add(signal)
    bad = seen - allowed_signals
    if bad:
        raise CheckFailed(f"signal value(s) not in {sorted(allowed_signals)}: {sorted(bad)}")
    if not seen:
        raise CheckSkipped("no book/product currently has a non-null signal to check")
    return f"observed {sorted(seen)} subset of {sorted(allowed_signals)}"


# --- Reporting ----------------------------------------------------------------


def _print_report(results: list[CheckResult], total_s: float) -> None:
    name_w = max((len(r.name) for r in results), default=4)
    print(f"{'CHECK':<{name_w}}  STATUS  MS       DETAIL")
    for r in results:
        print(f"{r.name:<{name_w}}  {r.status.value.upper():<6}  {r.elapsed_ms:>7.1f}  {r.detail}")
    n_pass = sum(1 for r in results if r.status == CheckStatus.PASS)
    n_fail = sum(1 for r in results if r.status == CheckStatus.FAIL)
    n_skip = sum(1 for r in results if r.status == CheckStatus.SKIP)
    print("-" * (name_w + 24))
    outcome = "OK" if n_fail == 0 else "FAILED"
    print(
        f"{outcome}: {len(results)} check(s), {n_pass} pass, {n_fail} fail, {n_skip} skip "
        f"in {total_s * 1000:.1f} ms ({total_s:.2f} s)"
    )


# --- Main ----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fast end-to-end smoke check: boots the real app against a COPY of a "
        "production DB and exercises the real endpoints. Never modifies --db."
    )
    parser.add_argument(
        "--db",
        required=True,
        type=Path,
        help="Path to a COPY of a production DB — never the live file. Copied to a temp "
        "dir before use; the given path is only ever opened for reading.",
    )
    args = parser.parse_args()

    src_db = args.db.resolve()
    if not src_db.is_file():
        parser.error(f"--db path does not exist or is not a file: {src_db}")

    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="book_alerter_smoke_") as tmp:
        tmp_path = Path(tmp)
        db_copy = _copy_db(src_db, tmp_path)
        cfg_path = tmp_path / "config.yaml"
        cover_dir = tmp_path / "covers"

        # Every `book_alerter.*` import in this script is deferred to this
        # point, after these env vars are set — `covers.py` binds COVER_DIR
        # from BOOK_ALERTER_COVER_DIR at IMPORT time, and `app.lifespan`
        # reads BOOK_ALERTER_CONFIG_PATH / BOOK_ALERTER_DATABASE_URL at
        # startup. Importing book_alerter before this point would point the
        # app at the wrong config/DB/cover cache.
        os.environ["BOOK_ALERTER_DATABASE_URL"] = f"sqlite:///{db_copy}"
        os.environ["BOOK_ALERTER_CONFIG_PATH"] = str(cfg_path)
        os.environ["BOOK_ALERTER_COVER_DIR"] = str(cover_dir)
        # Guarantee unauthenticated access regardless of the host shell's env.
        os.environ.pop("APP_BASIC_AUTH_USER", None)
        os.environ.pop("APP_BASIC_AUTH_PASS", None)

        from book_alerter.config import Config

        _write_harness_config(Config, cfg_path)
        _run_migrations(db_copy)

        from fastapi.testclient import TestClient

        from book_alerter.app import create_app
        from book_alerter.db import models
        from book_alerter.stats import Signal

        allowed_signals = _allowed_signal_values(Signal)
        specs = (
            _ItemKindSpec("book", "/api/books", models.PriceObservation, "book_id"),
            _ItemKindSpec("product", "/api/products", models.ProductObservation, "product_id"),
        )

        app = create_app()
        ctx: dict[str, object] = {}
        with TestClient(app) as client, Session(app.state.engine) as session:
            checks = [
                Check("GET /api/health", lambda: check_health(client)),
                Check("GET /api/books", lambda: check_list_books(client, ctx)),
                Check("GET /api/products", lambda: check_list_products(client, ctx)),
                Check("GET /api/alerts", lambda: check_list_alerts(client)),
                Check("GET /api/sources", lambda: check_list_sources(client)),
                Check("GET /api/books/{id}", lambda: check_book_detail(client, ctx)),
                Check(
                    "GET /api/books/{id}/stats",
                    lambda: check_book_stats(client, ctx, allowed_signals),
                ),
                Check(
                    "GET /api/books/{id}/observations",
                    lambda: check_book_observations(client, ctx),
                ),
                Check(
                    "invariant: no future-dated observations",
                    lambda: check_no_future_observations(session, specs),
                ),
                Check(
                    "invariant: total_minor == price + shipping",
                    lambda: check_total_minor_consistency(session, specs),
                ),
                Check(
                    "invariant: current_best offer exists",
                    lambda: check_current_best_offers_exist(client, session, specs),
                ),
                Check(
                    "invariant: signal is a member of Signal",
                    lambda: check_signal_membership(client, allowed_signals),
                ),
            ]
            results = [run_check(c) for c in checks]

    total_s = time.perf_counter() - t0
    _print_report(results, total_s)
    return 0 if all(r.status != CheckStatus.FAIL for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
