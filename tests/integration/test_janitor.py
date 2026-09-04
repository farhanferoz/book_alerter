"""Janitor sweeps (plan task T6.5).

Every runtime directory the application writes to must have a cap that is
actually enforced. Each test here drives one sweep against a real temporary
data directory and asserts both halves of the contract: the thing over the
limit goes, and the thing under it stays. A janitor that deletes nothing and a
janitor that deletes everything both pass a one-sided test.
"""

from __future__ import annotations

import gzip
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session

from book_alerter.config import JanitorConfig
from book_alerter.db import models
from book_alerter.janitor import (
    JanitorCategory,
    janitor_tick,
    known_item_keys,
    run_janitor,
    sweep_backups,
    sweep_browser_profiles,
    sweep_covers,
    sweep_debug_captures,
    sweep_keepa_cache,
    sweep_product_images,
)

DAY = 86_400.0
NOW = 1_800_000_000.0
CACHE_BYTES_PLANTED = 10_000  # 5_000 in Cache + 5_000 in Code Cache


def _write(path: Path, size: int = 16, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


# --- browser profiles -------------------------------------------------------


def test_browser_profile_under_cap_is_untouched(tmp_path: Path):
    profile = tmp_path / "browser-profiles" / "amazon"
    _write(profile / "Default" / "Cookies", 100)
    _write(profile / "Default" / "Cache" / "blob", 100)

    res = sweep_browser_profiles(tmp_path, JanitorConfig(browser_profile_max_bytes=1024 * 1024))

    assert res.files_removed == 0
    assert (profile / "Default" / "Cookies").exists()
    assert (profile / "Default" / "Cache" / "blob").exists()


def test_browser_profile_over_cap_drops_caches_first_and_keeps_cookies(tmp_path: Path):
    """Cookies are what make us a returning visitor, so caches must go first."""
    profile = tmp_path / "browser-profiles" / "amazon"
    _write(profile / "Default" / "Cookies", 50)
    _write(profile / "Default" / "Cache" / "big", CACHE_BYTES_PLANTED // 2)
    _write(profile / "Default" / "Code Cache" / "big", CACHE_BYTES_PLANTED // 2)

    res = sweep_browser_profiles(tmp_path, JanitorConfig(browser_profile_max_bytes=2_048))

    assert not (profile / "Default" / "Cache").exists()
    assert not (profile / "Default" / "Code Cache").exists()
    assert (profile / "Default" / "Cookies").exists(), "must not nuke the profile prematurely"
    assert res.bytes_freed >= CACHE_BYTES_PLANTED


def test_browser_profile_still_over_cap_after_caches_is_dropped_entirely(tmp_path: Path):
    profile = tmp_path / "browser-profiles" / "amazon"
    _write(profile / "Default" / "Cookies", 50)
    _write(profile / "Default" / "huge-not-a-cache", 9_000)

    sweep_browser_profiles(tmp_path, JanitorConfig(browser_profile_max_bytes=2_048))

    assert not profile.exists(), "a profile still over cap must go entirely"


# --- debug captures ---------------------------------------------------------


def test_debug_keeps_newest_n_per_source(tmp_path: Path):
    planted, keep = 10, 3
    d = tmp_path / "debug" / "amazon"
    for i in range(planted):
        _write(d / f"cap{i}.html", mtime=NOW - i * 60)

    res = sweep_debug_captures(tmp_path, JanitorConfig(debug_keep_files=keep), NOW)

    remaining = sorted(p.name for p in d.iterdir())
    assert remaining == ["cap0.html", "cap1.html", "cap2.html"], remaining
    assert res.files_removed == planted - keep
    assert res.category is JanitorCategory.DEBUG_CAPTURES


def test_debug_drops_files_past_the_age_limit_even_within_the_count(tmp_path: Path):
    """Age and count are independent bounds; a quiet source must not keep a
    year-old dump just because it has fewer than N files."""
    d = tmp_path / "debug" / "amazon"
    _write(d / "fresh.html", mtime=NOW - DAY)
    _write(d / "ancient.html", mtime=NOW - 400 * DAY)

    sweep_debug_captures(tmp_path, JanitorConfig(debug_keep_files=20, debug_max_age_days=14), NOW)

    assert (d / "fresh.html").exists()
    assert not (d / "ancient.html").exists()


def test_debug_sweeps_each_source_directory_independently(tmp_path: Path):
    keep = 2
    for source in ("amazon", "bookfinder"):
        for i in range(4):
            _write(tmp_path / "debug" / source / f"c{i}.html", mtime=NOW - i * 60)

    sweep_debug_captures(tmp_path, JanitorConfig(debug_keep_files=keep), NOW)

    for source in ("amazon", "bookfinder"):
        assert len(list((tmp_path / "debug" / source).iterdir())) == keep


# --- keepa cache ------------------------------------------------------------


def test_keepa_cache_drops_orphans_and_keeps_known_asins(tmp_path: Path):
    d = tmp_path / "keepa-cache"
    _write(d / "0140449264-r365.png", mtime=NOW)
    _write(d / "B000GONE01-r365.png", mtime=NOW)

    res = sweep_keepa_cache(tmp_path, JanitorConfig(), {"0140449264"}, NOW)

    assert (d / "0140449264-r365.png").exists()
    assert not (d / "B000GONE01-r365.png").exists()
    assert res.files_removed == 1


def test_keepa_cache_drops_stale_even_when_the_item_still_exists(tmp_path: Path):
    d = tmp_path / "keepa-cache"
    _write(d / "0140449264-r365.png", mtime=NOW - 90 * DAY)

    sweep_keepa_cache(tmp_path, JanitorConfig(keepa_cache_max_age_days=30), {"0140449264"}, NOW)

    assert not (d / "0140449264-r365.png").exists()


def test_keepa_cache_ignores_non_png_files(tmp_path: Path):
    d = tmp_path / "keepa-cache"
    _write(d / "notes.txt")
    sweep_keepa_cache(tmp_path, JanitorConfig(), set(), NOW)
    assert (d / "notes.txt").exists()


# --- covers -----------------------------------------------------------------


def test_covers_drops_only_unknown_isbns(tmp_path: Path):
    d = tmp_path / "covers"
    _write(d / "9780241638194")
    _write(d / "9780000000000")

    res = sweep_covers(tmp_path, {"9780241638194"})

    assert (d / "9780241638194").exists()
    assert not (d / "9780000000000").exists()
    assert res.category is JanitorCategory.COVERS


def test_sweep_product_images_drops_only_untracked_asins(tmp_path: Path):
    """T6.5/section-8 gap found in the plan-adherence audit: `api/products.py`
    caches `data/product-images/<asin>` but nothing swept it, so an image
    survived every product deletion and the directory had no cap at all."""
    d = tmp_path / "product-images"
    _write(d / "B09B96TG33")
    _write(d / "B0DEADBEEF")

    res = sweep_product_images(tmp_path, {"B09B96TG33"})

    assert (d / "B09B96TG33").exists(), "a tracked product keeps its image"
    assert not (d / "B0DEADBEEF").exists(), "an orphan must be removed"
    assert res.category is JanitorCategory.PRODUCT_IMAGES
    assert res.files_removed == 1


def test_sweep_product_images_survives_a_missing_directory(tmp_path: Path):
    """A fresh install, or one that has never tracked a product, has no such
    directory -- the sweep must be a no-op rather than an error."""
    res = sweep_product_images(tmp_path, set())
    assert res.files_removed == 0
    assert res.errors == []


# --- backups ----------------------------------------------------------------


def test_backups_are_compressed_in_place_and_stay_readable(tmp_path: Path):
    d = tmp_path / "backups"
    payload = b"SQLite format 3\x00" + b"y" * 5_000
    d.mkdir(parents=True)
    (d / "book_alerter_20260101T000000Z.db").write_bytes(payload)

    res = sweep_backups(d, JanitorConfig())

    gz = d / "book_alerter_20260101T000000Z.db.gz"
    assert gz.exists(), "compressed copy must exist"
    assert not (d / "book_alerter_20260101T000000Z.db").exists(), "original must be replaced"
    assert gzip.decompress(gz.read_bytes()) == payload, "compression must be lossless"
    assert res.bytes_freed > 0


def test_backups_compression_can_be_disabled(tmp_path: Path):
    d = tmp_path / "backups"
    d.mkdir(parents=True)
    (d / "book_alerter_20260101T000000Z.db").write_bytes(b"z" * 100)

    sweep_backups(d, JanitorConfig(compress_backups=False))

    assert (d / "book_alerter_20260101T000000Z.db").exists()


def test_backups_already_compressed_are_left_alone(tmp_path: Path):
    d = tmp_path / "backups"
    d.mkdir(parents=True)
    gz = d / "book_alerter_20260101T000000Z.db.gz"
    gz.write_bytes(gzip.compress(b"already"))
    before = gz.read_bytes()

    res = sweep_backups(d, JanitorConfig())

    assert gz.read_bytes() == before
    assert res.files_removed == 0


# --- item-existence lookup + whole run --------------------------------------


def test_known_item_keys_covers_both_isbn13_and_amazon_asin(engine_with_view):
    with Session(engine_with_view) as s:
        now = datetime.now(UTC)
        s.add(models.Book(isbn13="9780140449266", title="T", author="A",
                          created_at=now, updated_at=now))
        s.add(models.Product(asin="B09B96TG33", title="P",
                             created_at=now, updated_at=now))
        s.commit()
        asins, cover_ids, product_asins = known_item_keys(s)

    assert "9780140449266" in cover_ids, "covers are keyed by ISBN-13"
    assert "B09B96TG33" in asins
    assert "0140449264" in asins, "book Keepa charts are keyed by the ISBN-10 ASIN form"
    # Product images match tracked PRODUCTS only. If the book-derived ASIN
    # leaked into this set, a deleted product's image would survive whenever
    # its ASIN collided with one derived from a tracked book's ISBN.
    assert product_asins == {"B09B96TG33"}


def test_run_janitor_reports_every_category_and_survives_empty_dirs(
    engine_with_view, tmp_path: Path
):
    """A fresh install has none of these directories; the job must still run."""
    with Session(engine_with_view) as s:
        results = run_janitor(
            data_dir=tmp_path,
            cfg=JanitorConfig(),
            backup_dir=tmp_path / "backups",
            session=s,
            now=time.time(),
        )

    assert {r.category for r in results} == set(JanitorCategory)
    assert all(r.files_removed == 0 for r in results)
    assert all(not r.errors for r in results)


# --- scheduled tick ---------------------------------------------------------


class _State:
    """Stand-in for `app.state`, which is a plain attribute bag."""


def test_tick_stamps_app_state_so_a_dead_janitor_is_visible(engine_with_view, tmp_path: Path):
    state = _State()
    janitor_tick(
        data_dir=tmp_path,
        cfg=JanitorConfig(),
        backup_dir=tmp_path / "backups",
        session_factory=lambda: Session(engine_with_view),
        app_state=state,
    )
    assert getattr(state, "janitor_last_run_at", None) is not None


def test_tick_is_a_no_op_when_disabled(engine_with_view, tmp_path: Path):
    state = _State()
    _write(tmp_path / "covers" / "9780000000000")

    results = janitor_tick(
        data_dir=tmp_path,
        cfg=JanitorConfig(enabled=False),
        backup_dir=tmp_path / "backups",
        session_factory=lambda: Session(engine_with_view),
        app_state=state,
    )

    assert results == []
    assert (tmp_path / "covers" / "9780000000000").exists(), "disabled must delete nothing"
    assert getattr(state, "janitor_last_run_at", None) is None


def test_tick_swallows_failures_rather_than_killing_the_scheduler(tmp_path: Path):
    def exploding_session_factory():
        raise RuntimeError("database is gone")

    results = janitor_tick(
        data_dir=tmp_path,
        cfg=JanitorConfig(),
        backup_dir=tmp_path / "backups",
        session_factory=exploding_session_factory,
        app_state=_State(),
    )
    assert results == []
