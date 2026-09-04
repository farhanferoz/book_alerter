"""Verifies current-best selection (book_live_offers + Python selection,
T3.1) computed via a real alembic migration to head."""
from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, create_engine

from book_alerter.config import RecommendationConfig
from book_alerter.db import models
from book_alerter.stats import _BOOK_SCHEMA, compute_book_stats, compute_stats_for_items


def test_book_stats_view_current_best(tmp_path):
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True,
        capture_output=True,
    )

    engine = create_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000001", title="Title", author="Author",
            created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)

        # 5 observations across 2 sources; "wob" latest=850 (cheapest), "amazon" latest=1100
        rows = [
            ("wob",    1200, now - timedelta(days=10)),
            ("wob",    1000, now - timedelta(days=5)),
            ("wob",     850, now - timedelta(days=1)),  # latest wob
            ("amazon", 1500, now - timedelta(days=8)),
            ("amazon", 1100, now - timedelta(days=2)),  # latest amazon
        ]
        for source, total, obs_at in rows:
            s.add(models.PriceObservation(
                book_id=book.id, source=source, condition="new",
                price_minor=total, currency="GBP", shipping_minor=0,
                total_minor=total, url=f"https://{source}",
                observed_at=obs_at, raw={},
            ))
        s.commit()

        stats = compute_book_stats(book.id, s)

    assert stats.observation_count == 5
    assert stats.current_best_total_minor == 850
    assert stats.current_best_source == "wob"
    assert stats.all_time_min_total_minor == 850
    assert stats.all_time_max_total_minor == 1500


def test_book_stats_view_excludes_stale_source_partition(tmp_path):
    """Regression for the freshness-gate bug fixed in migration 0017.

    When a parser change makes a (source, condition, seller) partition
    stop receiving fresh rows (e.g. Amazon Resale was historically
    tagged as `condition=new`; commit f24668b changed it to `used_vg`),
    the old row sits as latest-of-its-partition indefinitely. Without
    the freshness gate, MIN(total_minor) across rn=1 rows can pick the
    stale row even though it represents an offer that doesn't exist
    anymore.

    This test reproduces the production failure: an old `amazon-new-
    Amazon Resale £28.60` row at T-30min + a fresh `amazon-used_vg-
    Amazon Resale £24.62` row at T should yield current_best=£24.62,
    not £28.60.
    """
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True,
        capture_output=True,
    )
    engine = create_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000002", title="Stale", author="x",
            created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)

        # Pre-fix scrape: Amazon Resale tagged as new at £28.60.
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="new",
            seller="Amazon Resale",
            price_minor=2860, currency="GBP", shipping_minor=0,
            total_minor=2860, url="https://example/old-dp",
            observed_at=now - timedelta(minutes=30), raw={},
        ))
        # Post-fix scrape (just now): the same seller correctly tagged
        # used_vg at £24.62 (and at £28.60 as a separate offer from the
        # dp page — both are real Amazon Resale listings at the moment).
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="used_vg",
            seller="Amazon Resale",
            price_minor=2462, currency="GBP", shipping_minor=0,
            total_minor=2462,
            url="https://example/warehouse-deals",
            observed_at=now, raw={},
        ))
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="used_vg",
            seller="Amazon Resale",
            price_minor=2860, currency="GBP", shipping_minor=0,
            total_minor=2860, url="https://example/new-dp",
            observed_at=now, raw={},
        ))
        s.commit()

        stats = compute_book_stats(book.id, s)

    # The fresh £24.62 used_vg row must win, not the stale £28.60 new row
    # AND not the colliding £28.60 used_vg row at the same observed_at —
    # the ROW_NUMBER tiebreaker must prefer the cheaper total within a
    # partition.
    assert stats.current_best_total_minor == 2462
    assert stats.current_best_condition == "used_vg"
    assert stats.current_best_seller == "Amazon Resale"
    assert stats.current_best_url == "https://example/warehouse-deals"


def test_book_stats_view_excludes_stale_source_when_fresher_exists(tmp_path):
    """Regression for migration 0018: a source whose scraper has stopped must
    not keep winning current_best with its frozen last-known price.

    Live failure: WOB last scraped 8 days ago (its parser broke) showing a
    £16.00 used_vg that no longer existed, while Amazon kept scraping hourly
    and showed a live £17.59. The 0017 per-source gate kept WOB's £16.00 (it
    was WOB's own latest) and it beat Amazon on MIN(total). The cross-source
    gate drops WOB because it lags Amazon's fresh scrape by far more than a
    day, so the live £17.59 wins.
    """
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True,
        capture_output=True,
    )
    engine = create_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000003", title="StaleSource", author="x",
            created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)

        # WOB: frozen 8 days ago at £16.00 (scraper broken — cheaper but stale).
        s.add(models.PriceObservation(
            book_id=book.id, source="wob", condition="used_vg", seller="WOB",
            price_minor=1600, currency="GBP", shipping_minor=0,
            total_minor=1600, url="https://wob/stale",
            observed_at=now - timedelta(days=8), raw={},
        ))
        # Amazon: live, scraped just now at £17.59.
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="used_acceptable",
            seller="Amazon Resale",
            price_minor=1759, currency="GBP", shipping_minor=0,
            total_minor=1759, url="https://amazon/fresh",
            observed_at=now, raw={},
        ))
        s.commit()

        stats = compute_book_stats(book.id, s)

    # The live £17.59 wins; the stale £16.00 WOB offer is excluded.
    assert stats.current_best_total_minor == 1759
    assert stats.current_best_source == "amazon"
    # observation_count still reflects BOTH rows — only current_best is gated.
    assert stats.observation_count == 2


def test_book_stats_view_uses_last_seen_not_first_seen(tmp_path):
    """Regression for the dedup-freshness bug (migration 0018).

    A price that's stable-but-live is deduped on every scrape, so its canonical
    (non-dupe) row's observed_at freezes at the FIRST sighting. The old view
    ranked offers by that frozen timestamp, so a since-vanished offer whose
    first sighting happened to be more recent could out-rank the genuinely-live
    one. Live failure: a WOB £16 that stopped appearing on 2026-05-17 beat the
    live £21 because £16 was first-seen on 05-16 and £21 on 05-15.

    Here: a live £20 (first-seen 10 days ago, but re-seen today as a dup) must
    beat a vanished £18 (first-seen 2 days ago, not seen since). First-seen
    logic picks £18; last-seen logic picks £20.
    """
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True,
        capture_output=True,
    )
    engine = create_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000004", title="LastSeen", author="x",
            created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)

        # Live £20: first seen 10 days ago (canonical), re-seen today as a dup.
        live = models.PriceObservation(
            book_id=book.id, source="wob", condition="used_vg", seller="WOB",
            price_minor=2000, currency="GBP", shipping_minor=0, total_minor=2000,
            url="https://wob/live", observed_at=now - timedelta(days=10), raw={},
        )
        s.add(live); s.commit(); s.refresh(live)
        s.add(models.PriceObservation(
            book_id=book.id, source="wob", condition="used_vg", seller="WOB",
            price_minor=2000, currency="GBP", shipping_minor=0, total_minor=2000,
            url="https://wob/live", observed_at=now, raw={},
            is_duplicate_of=live.id,  # today's re-sighting → refreshes last_seen
        ))
        # Vanished £18: first seen 2 days ago (canonical), never seen again.
        s.add(models.PriceObservation(
            book_id=book.id, source="wob", condition="used_vg", seller="WOB",
            price_minor=1800, currency="GBP", shipping_minor=0, total_minor=1800,
            url="https://wob/gone", observed_at=now - timedelta(days=2), raw={},
        ))
        s.commit()

        stats = compute_book_stats(book.id, s)

    # last_seen wins: the live (re-seen-today) £20, not the vanished £18.
    assert stats.current_best_total_minor == 2000
    assert stats.current_best_url == "https://wob/live"


def test_book_stats_view_current_best_url_is_latest_sighting(tmp_path):
    """Regression: current_best_url must come from the LATEST sighting in the
    dedup group, not the canonical (first-seen) row.

    Live failure: a stable Amazon Resale offer was first scraped by the old
    parser, which recorded a useless `/Amazon-Warehouse-Deals/b` category URL.
    Every later scrape re-saw the same offer (same price/seller/condition) and
    was deduped onto that canonical row, so its URL was frozen — even after the
    parser was fixed to emit a proper `/gp/offer-listing/<asin>` link. The
    canonical row keeps the broken URL forever; only the dup rows carry the
    fixed one. current_best must surface the fixed (latest) URL.

    The dedup key is (item, source, seller, condition, price, shipping) — URL is
    NOT part of it — so the latest sighting's URL is always for the same offer.
    """
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True, capture_output=True,
    )
    engine = create_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000009", title="StaleUrl", author="x",
            created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)

        # Canonical first-sighting (old parser): broken category URL.
        canonical = models.PriceObservation(
            book_id=book.id, source="amazon", condition="used_vg",
            seller="Amazon Resale",
            price_minor=2354, currency="GBP", shipping_minor=0, total_minor=2354,
            url="https://www.amazon.co.uk/Amazon-Warehouse-Deals/b?node=358",
            observed_at=now - timedelta(days=5), raw={},
        )
        s.add(canonical); s.commit(); s.refresh(canonical)
        # Today's re-sighting (fixed parser): proper offer-listing URL. Same
        # offer → deduped onto the canonical row.
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="used_vg",
            seller="Amazon Resale",
            price_minor=2354, currency="GBP", shipping_minor=0, total_minor=2354,
            url="https://www.amazon.co.uk/gp/offer-listing/024163/?condition=all",
            observed_at=now, raw={}, is_duplicate_of=canonical.id,
        ))
        s.commit()

        stats = compute_book_stats(book.id, s)

    assert stats.current_best_total_minor == 2354
    assert (
        stats.current_best_url
        == "https://www.amazon.co.uk/gp/offer-listing/024163/?condition=all"
    ), "current_best_url must be the latest sighting's URL, not the frozen canonical one"


def test_book_stats_view_all_sources_stale_still_shows_cheapest(tmp_path):
    """The gate is RELATIVE, not wall-clock: when every source is old, the book
    must still surface its cheapest known offer rather than going price-less.

    Guards against a future "absolute staleness" tweak silently blanking quiet
    books — which would also suppress their alerts (current_best feeds alerting).
    """
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True, capture_output=True,
    )
    engine = create_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000005", title="Quiet", author="x",
            created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)
        # Both sources last scraped ~30 days ago, within a day of each other.
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="new", seller="Amazon",
            price_minor=2000, currency="GBP", shipping_minor=0, total_minor=2000,
            url="https://amazon", observed_at=now - timedelta(days=30), raw={},
        ))
        s.add(models.PriceObservation(
            book_id=book.id, source="wob", condition="used_vg", seller="WOB",
            price_minor=1800, currency="GBP", shipping_minor=0, total_minor=1800,
            url="https://wob", observed_at=now - timedelta(days=30, hours=2), raw={},
        ))
        s.commit()
        stats = compute_book_stats(book.id, s)

    assert stats.current_best_total_minor == 1800  # cheapest, NOT NULL
    assert stats.current_best_source == "wob"


def test_book_stats_view_freshness_gate_boundary_is_one_day(tmp_path):
    """The cross-source gate is `<= 1 day`, inclusive. A source exactly 24h
    behind the freshest scrape is KEPT; 24h-and-a-bit is dropped. Pins the
    boundary direction independently of the property test (which shares the
    `<= 1 day` constant on both sides and so can't catch a `<`/`<=` flip).

    Computes both books in one `compute_stats_for_items` call — exercising
    the batched path (candidates for two entities in a single query) instead
    of two separate `compute_book_stats` calls.
    """
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True, capture_output=True,
    )
    engine = create_engine(url)
    now = datetime.now(UTC)

    def _book_with_wob_lag(s, isbn, lag):
        book = models.Book(
            isbn13=isbn, title="t", author="x", created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)
        # Fresh amazon at £30; cheaper wob (£10) lagging the freshest scrape.
        s.add(models.PriceObservation(
            book_id=book.id, source="amazon", condition="new", seller="Amazon",
            price_minor=3000, currency="GBP", shipping_minor=0, total_minor=3000,
            url="https://amazon", observed_at=now, raw={},
        ))
        s.add(models.PriceObservation(
            book_id=book.id, source="wob", condition="used_vg", seller="WOB",
            price_minor=1000, currency="GBP", shipping_minor=0, total_minor=1000,
            url="https://wob", observed_at=now - lag, raw={},
        ))
        s.commit()
        return book.id

    with Session(engine) as s:
        at_boundary = _book_with_wob_lag(s, "9780000000006", timedelta(hours=24))
        just_over = _book_with_wob_lag(s, "9780000000007", timedelta(hours=24, minutes=1))
        ids = [at_boundary, just_over]
        stats_by_id = compute_stats_for_items(
            ids, s,
            schema=_BOOK_SCHEMA,
            cfg=RecommendationConfig(),
            window_days=dict.fromkeys(ids, 90),
        )

    # Exactly 24h behind → wob kept → its cheaper £10 wins.
    assert stats_by_id[at_boundary].current_best_total_minor == 1000
    assert stats_by_id[at_boundary].current_best_source == "wob"
    # 24h+1min behind → wob dropped → fresh amazon £30 wins.
    assert stats_by_id[just_over].current_best_total_minor == 3000
    assert stats_by_id[just_over].current_best_source == "amazon"


def test_book_stats_view_null_and_empty_seller_do_not_duplicate(tmp_path):
    """A NULL-seller offer and an ''-seller offer from the same source +
    condition at the same lowest total must be treated as ONE offer, not two.

    `book_live_offers`' `latest_per_offer` CTE partitions seller by
    COALESCE(seller,'') so the two land in the same partition (one rn=1
    winner reaches the Python selection) — without that, both would surface
    as distinct candidates for what current_best_seller should read as a
    single offer.
    """
    db_path = tmp_path / "t.db"
    url = f"sqlite:///{db_path}"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**os.environ, "BOOK_ALERTER_DATABASE_URL": url},
        check=True, capture_output=True,
    )
    engine = create_engine(url)
    now = datetime.now(UTC)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000008", title="NullSeller", author="x",
            created_at=now, updated_at=now,
        )
        s.add(book); s.commit(); s.refresh(book)
        for seller in (None, ""):
            s.add(models.PriceObservation(
                book_id=book.id, source="amazon", condition="new", seller=seller,
                price_minor=1500, currency="GBP", shipping_minor=0, total_minor=1500,
                url="https://x", observed_at=now, raw={},
            ))
        s.commit()
        stats = compute_book_stats(book.id, s)

    assert stats.current_best_total_minor == 1500
