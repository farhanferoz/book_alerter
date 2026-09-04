"""Daily sweep of everything the application writes under `data/`.

The rule this module enforces is that no runtime directory grows without a
bound. Each sweep below owns one directory, reports what it removed, and
touches nothing outside the data directory it is given.

Two things are deliberate:

- **Every limit is a config value** (`JanitorConfig`), never a constant here.
  A cap you cannot change without a code edit is a cap that gets raised by
  deleting the check.
- **Sweeps never raise.** One unreadable file must not stop the other
  categories from being tidied, so each sweep collects its own errors and
  reports them. The job's contract is "free what you safely can and say what
  you did", not "succeed completely or not at all".

Ordering matters in one place only: this job is scheduled after the weekly
backup so it never tidies a backup that is still being written.
"""

from __future__ import annotations

import gzip
import shutil
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from sqlmodel import Session, select

from book_alerter.config import JanitorConfig
from book_alerter.db import models
from book_alerter.logging_setup import get_logger
from book_alerter.sources.browser import is_profile_in_use
from book_alerter.sources.normalizers import asin_for_amazon_uk

log = get_logger(__name__)

# Chrome rebuilds these on the next launch; dropping them reclaims most of a
# profile's size without losing the cookies that make us a returning visitor.
_PROFILE_CACHE_SUBDIRS = ("Default/Cache", "Default/Code Cache", "Default/GPUCache")

_BACKUP_GLOB = "book_alerter_*.db"


class JanitorCategory(StrEnum):
    """One per directory the janitor owns. Also the `category` field of the
    structured log line, so these strings are an operational interface."""

    BROWSER_PROFILES = "browser_profiles"
    DEBUG_CAPTURES = "debug_captures"
    KEEPA_CACHE = "keepa_cache"
    COVERS = "covers"
    PRODUCT_IMAGES = "product_images"
    BACKUPS = "backups"


@dataclass
class SweepResult:
    category: JanitorCategory
    files_removed: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)
    note: str = ""

    def log(self) -> None:
        log.info(
            "janitor.swept",
            category=str(self.category),
            files_removed=self.files_removed,
            bytes_freed=self.bytes_freed,
            errors=len(self.errors),
            note=self.note,
        )


# --- filesystem helpers -----------------------------------------------------


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _remove_file(path: Path, result: SweepResult) -> None:
    try:
        size = path.stat().st_size
        path.unlink()
    except OSError as e:
        result.errors.append(f"{path.name}: {e}")
        return
    result.files_removed += 1
    result.bytes_freed += size


def _remove_tree(path: Path, result: SweepResult) -> None:
    size = _dir_size(path)
    try:
        shutil.rmtree(path)
    except OSError as e:
        result.errors.append(f"{path.name}: {e}")
        return
    result.files_removed += 1
    result.bytes_freed += size


def _age_days(path: Path, now: float) -> float:
    try:
        return (now - path.stat().st_mtime) / 86_400.0
    except OSError:
        return 0.0


def _child_dirs(parent: Path) -> Iterator[Path]:
    if not parent.is_dir():
        return
    for child in sorted(parent.iterdir()):
        if child.is_dir():
            yield child


def _files(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return sorted((p for p in parent.iterdir() if p.is_file()), key=lambda p: p.name)


# --- sweeps -----------------------------------------------------------------


def sweep_browser_profiles(data_dir: Path, cfg: JanitorConfig) -> SweepResult:
    """Cap each persistent browser profile.

    Caches go first because they are pure rebuildable bulk; only if the profile
    is still over its cap does the whole profile go. Losing a profile costs one
    cold visit, which matters -- a cookieless visit is what makes Amazon serve
    the first-order delivery promo -- so it is the last resort, not the first.

    F-B7: this sweep runs as a sync APScheduler job, i.e. in the event loop's
    thread-pool executor, with no coordination with `browser._profile_dir_locks`
    -- and it couldn't take an `asyncio.Lock` from a worker thread even if it
    wanted to. `metadata_refresh` opens the `amazon_uk_product` profile on an
    unconditional 30-minute interval regardless of the scheduled-source
    windows this job is normally timed to avoid, so a profile can genuinely
    be open when this fires. Searched `browser.py` for existing per-profile
    state before adding `is_profile_in_use`: `_profile_dir_locks` is the only
    other one, and it's asyncio-only, unusable from this thread -- hence the
    new thread-safe registry there instead of reusing it. A profile a live
    Chromium is using is skipped entirely (not just the cache subdirs) rather
    than rmtree'd out from under it, which would corrupt the profile and cost
    exactly the cookieless-visitor regression this profile exists to avoid.
    """
    result = SweepResult(JanitorCategory.BROWSER_PROFILES)
    root = data_dir / "browser-profiles"
    for profile in _child_dirs(root):
        if is_profile_in_use(profile.resolve()):
            result.note = f"{profile.name}: skipped, in use"
            continue
        before = _dir_size(profile)
        if before <= cfg.browser_profile_max_bytes:
            continue
        for rel in _PROFILE_CACHE_SUBDIRS:
            cache = profile / rel
            if cache.is_dir():
                _remove_tree(cache, result)
        after = _dir_size(profile)
        if after > cfg.browser_profile_max_bytes:
            _remove_tree(profile, result)
            result.note = f"{profile.name}: dropped whole profile ({after} bytes over cap)"
        else:
            result.note = f"{profile.name}: caches only ({before}->{after} bytes)"
    return result


def sweep_debug_captures(data_dir: Path, cfg: JanitorConfig, now: float) -> SweepResult:
    """Keep the newest N failure dumps per source, and nothing older than the
    age limit. Both bounds are needed: the count alone lets a stale dump live
    forever on a quiet source, and the age alone lets a failure burst fill the
    disk in a day."""
    result = SweepResult(JanitorCategory.DEBUG_CAPTURES)
    root = data_dir / "debug"
    for source_dir in _child_dirs(root):
        files = sorted(
            _files(source_dir), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True
        )
        for i, f in enumerate(files):
            too_many = i >= cfg.debug_keep_files
            too_old = _age_days(f, now) > cfg.debug_max_age_days
            if too_many or too_old:
                _remove_file(f, result)
    return result


def sweep_keepa_cache(
    data_dir: Path, cfg: JanitorConfig, known_asins: set[str], now: float
) -> SweepResult:
    """Drop chart PNGs for items that no longer exist, and anything past the
    age limit. The cache filename is `<asin>-r<days>.png`, and for books the
    ASIN is the ISBN-10 form -- so the caller must pass ASINs, not ISBN-13s."""
    result = SweepResult(JanitorCategory.KEEPA_CACHE)
    root = data_dir / "keepa-cache"
    for f in _files(root):
        if f.suffix != ".png":
            continue
        asin = f.stem.rsplit("-r", 1)[0]
        if asin not in known_asins or _age_days(f, now) > cfg.keepa_cache_max_age_days:
            _remove_file(f, result)
    return result


def sweep_covers(data_dir: Path, known_ids: set[str]) -> SweepResult:
    """Drop cover images whose book no longer exists. Covers are named by
    ISBN-13 with no extension (`covers.cover_path`), and have no age rule --
    a cover for a tracked book stays useful indefinitely."""
    result = SweepResult(JanitorCategory.COVERS)
    for f in _files(data_dir / "covers"):
        if f.name not in known_ids:
            _remove_file(f, result)
    return result


def sweep_product_images(data_dir: Path, known_asins: set[str]) -> SweepResult:
    """Drop cached product images whose product no longer exists.

    `api/products.py` caches these as `data/product-images/<asin>`, its own
    docstring calling them "parallel to `data/covers/<isbn13>`" -- but only
    covers were being swept, so this directory grew without a cap and kept
    an image for every product ever deleted. Same no-age rule as covers: an
    image for a tracked product stays useful indefinitely, so membership of
    the tracked set is the only test.

    Keyed on PRODUCT asins specifically, not the union set `sweep_keepa_cache`
    uses -- that one folds in ASINs derived from book ISBNs, which would
    retain an orphaned image whose product was deleted but whose ASIN happens
    to match a tracked book's.
    """
    result = SweepResult(JanitorCategory.PRODUCT_IMAGES)
    for f in _files(data_dir / "product-images"):
        if f.name not in known_asins:
            _remove_file(f, result)
    return result


def sweep_backups(backup_dir: Path, cfg: JanitorConfig) -> SweepResult:
    """Compress uncompressed backups in place.

    Retention stays with the backup job that creates them; this only changes
    how much space the retained copies take. Compression is lossless and the
    original is removed only after the gzip is written, so an interrupted run
    leaves the original intact rather than a truncated archive.
    """
    result = SweepResult(JanitorCategory.BACKUPS)
    if not cfg.compress_backups or not backup_dir.is_dir():
        return result
    saved = 0
    for src in sorted(backup_dir.glob(_BACKUP_GLOB)):
        if src.suffix == ".gz":
            continue
        dest = src.with_suffix(src.suffix + ".gz")
        if dest.exists():
            continue
        try:
            original = src.stat().st_size
            with src.open("rb") as fin, gzip.open(dest, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            compressed = dest.stat().st_size
            src.unlink()
        except OSError as e:
            result.errors.append(f"{src.name}: {e}")
            if dest.exists():
                dest.unlink(missing_ok=True)
            continue
        result.files_removed += 1
        saved += original - compressed
    result.bytes_freed = saved
    result.note = f"compressed {result.files_removed} backup(s)"
    return result


# --- entry point ------------------------------------------------------------


def known_item_keys(session: Session) -> tuple[set[str], set[str], set[str]]:
    """(asins, cover_ids, product_asins) for every item that still exists.

    Three sets because three directories key their files differently. Books
    are keyed by ISBN-13 on disk for covers but by their Amazon ASIN
    (ISBN-10) for Keepa charts. `product_asins` is deliberately NOT folded
    into `asins`: product images must be matched against tracked products
    only, or a deleted product's image survives whenever its ASIN collides
    with an ASIN derived from a tracked book's ISBN.
    """
    isbns = [row for row in session.exec(select(models.Book.isbn13)).all()]
    product_asins = {row for row in session.exec(select(models.Product.asin)).all()}
    asins = set(product_asins)
    for isbn in isbns:
        try:
            asins.add(asin_for_amazon_uk(isbn))
        except Exception:
            continue
    return asins, set(isbns), product_asins


def run_janitor(
    *,
    data_dir: Path,
    cfg: JanitorConfig,
    backup_dir: Path,
    session: Session,
    now: float | None = None,
) -> list[SweepResult]:
    """Run every sweep. Returns one result per category, already logged."""
    clock = time.time() if now is None else now
    asins, cover_ids, product_asins = known_item_keys(session)
    results = [
        sweep_browser_profiles(data_dir, cfg),
        sweep_debug_captures(data_dir, cfg, clock),
        sweep_keepa_cache(data_dir, cfg, asins, clock),
        sweep_covers(data_dir, cover_ids),
        sweep_product_images(data_dir, product_asins),
        sweep_backups(backup_dir, cfg),
    ]
    for r in results:
        r.log()
    log.info(
        "janitor.finished",
        files_removed=sum(r.files_removed for r in results),
        bytes_freed=sum(r.bytes_freed for r in results),
    )
    return results


def total_bytes_freed(results: Iterable[SweepResult]) -> int:
    return sum(r.bytes_freed for r in results)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def janitor_tick(
    *,
    data_dir: Path,
    cfg: JanitorConfig,
    backup_dir: Path,
    session_factory: Callable[[], Session],
    app_state: object | None = None,
) -> list[SweepResult]:
    """One scheduled run. Records the completion time on `app_state` so
    `/api/health` can report it -- a cleanup job that dies quietly is only
    noticed when the disk fills, which is far too late.

    Never raises: a failing janitor must not take the scheduler down with it.
    """
    if not cfg.enabled:
        log.info("janitor.disabled")
        return []
    try:
        with session_factory() as session:
            results = run_janitor(
                data_dir=data_dir, cfg=cfg, backup_dir=backup_dir, session=session
            )
    except Exception as e:
        log.error("janitor.failed", error=str(e))
        return []
    if app_state is not None:
        app_state.janitor_last_run_at = utc_now_iso()
    return results
