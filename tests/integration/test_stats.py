"""Tests for `compute_book_stats` — reads from the `book_stats` view."""
from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st
from sqlmodel import Session

from book_alerter.db import models
from book_alerter.stats import BookStats, compute_book_stats, seller_class


def _add_obs(
    session: Session, *, book_id: int, total: int, source: str = "wob",
    observed_at: datetime | None = None,
    is_duplicate_of: int | None = None,
) -> models.PriceObservation:
    # shipping_minor=0 (not None) so the row is treated as "buyable" by the
    # book_stats view's current_best CTE. Tests that need a known shipping
    # value pass it explicitly via other helpers.
    obs = models.PriceObservation(
        book_id=book_id,
        source=source,
        condition="new",
        price_minor=total,
        currency="GBP",
        shipping_minor=0,
        total_minor=total,
        url=f"https://{source}/{total}",
        observed_at=observed_at or datetime.now(UTC),
        raw={},
        is_duplicate_of=is_duplicate_of,
    )
    session.add(obs)
    session.commit()
    session.refresh(obs)
    return obs


def test_empty_book_returns_zero_count_stats(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000010")
        stats = compute_book_stats(book.id, s)

    assert isinstance(stats, BookStats)
    assert stats.book_id == book.id
    assert stats.observation_count == 0
    assert stats.current_best_total_minor is None
    assert stats.windows["3m"].p25 is None
    assert stats.windows["3m"].p50 is None
    assert stats.windows["3m"].p75 is None
    assert stats.sorted_totals == []
    assert stats.all_time_min_total_minor is None
    assert stats.all_time_max_total_minor is None
    assert stats.days_of_history == 0
    assert stats.last_observed_at is None


def test_one_observation_yields_that_value_for_all_percentiles(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000011")
        _add_obs(s, book_id=book.id, total=500)
        stats = compute_book_stats(book.id, s)

    assert stats.observation_count == 1
    assert stats.current_best_total_minor == 500
    assert stats.windows["3m"].p25 == 500
    assert stats.windows["3m"].p50 == 500
    assert stats.windows["3m"].p75 == 500
    assert stats.percentile_at(33) == 500
    assert stats.sorted_totals == [500]


def test_three_observations_yields_reasonable_percentiles(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000012")
        for total in (100, 200, 300):
            _add_obs(s, book_id=book.id, total=total, source=f"src_{total}")
        stats = compute_book_stats(book.id, s)

    # statistics.quantiles(sorted([100,200,300]), n=4, method="inclusive")
    # = [150.0, 200.0, 250.0]
    assert stats.windows["3m"].p25 == 150
    assert stats.windows["3m"].p50 == 200
    assert stats.windows["3m"].p75 == 250
    assert stats.observation_count == 3
    assert stats.sorted_totals == [100, 200, 300]


def test_ten_observations_p25_lt_p50_lt_p75(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000013")
        totals = [100 * i for i in range(1, 11)]  # 100..1000
        for i, total in enumerate(totals):
            _add_obs(s, book_id=book.id, total=total, source=f"src_{i:02d}")
        stats = compute_book_stats(book.id, s)

    assert stats.windows["3m"].p25 is not None
    assert stats.windows["3m"].p50 is not None
    assert stats.windows["3m"].p75 is not None
    assert stats.windows["3m"].p25 < stats.windows["3m"].p50 < stats.windows["3m"].p75
    assert stats.sorted_totals == sorted(totals)


def test_percentile_at_arbitrary_value(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000014")
        totals = [100 * i for i in range(1, 11)]
        for i, total in enumerate(totals):
            _add_obs(s, book_id=book.id, total=total, source=f"src_{i:02d}")
        stats = compute_book_stats(book.id, s)

    # Consistency: percentile_at(50) should match p50.
    # NOTE: p50 is computed via statistics.quantiles inclusive (round-cast to int);
    # percentile_at uses linear interpolation. For the even-length sequence
    # 100..1000, both yield the same value 550.
    assert stats.percentile_at(50) == stats.windows["3m"].p50
    # p10 of [100,200,...,1000] via linear interp:
    #   idx = 0.10 * 9 = 0.9 -> 100 + (200-100)*0.9 = 190
    assert stats.percentile_at(10) == 190
    assert stats.percentile_at(10) < stats.windows["3m"].p25


def test_percentile_at_returns_none_for_empty():
    empty = BookStats(
        book_id=1,
        current_best_total_minor=None,
        current_best_price_minor=None,
        current_best_shipping_minor=None,
        current_best_source=None,
        current_best_seller=None,
        current_best_condition=None,
        current_best_url=None,
        all_time_min_total_minor=None,
        all_time_max_total_minor=None,
        observation_count=0,
        days_of_history=0,
        last_observed_at=None,
    )
    assert empty.percentile_at(50) is None


def test_percentile_at_clamps_out_of_range_pct():
    stats = BookStats(
        book_id=1,
        current_best_total_minor=None,
        current_best_price_minor=None,
        current_best_shipping_minor=None,
        current_best_source=None,
        current_best_seller=None,
        current_best_condition=None,
        current_best_url=None,
        all_time_min_total_minor=None,
        all_time_max_total_minor=None,
        observation_count=3,
        days_of_history=0,
        last_observed_at=None,
        sorted_totals=[100, 200, 300],
    )
    assert stats.percentile_at(0) is None
    assert stats.percentile_at(100) is None
    assert stats.percentile_at(-5) is None
    assert stats.percentile_at(150) is None


@given(st.lists(st.integers(min_value=1, max_value=1_000_000), min_size=2, max_size=50))
def test_percentile_monotonicity_property(values):
    """For any sorted list of positive ints, percentile_at(a) <= percentile_at(b) for a<b."""
    stats = BookStats(
        book_id=1,
        current_best_total_minor=None,
        current_best_price_minor=None,
        current_best_shipping_minor=None,
        current_best_source=None,
        current_best_seller=None,
        current_best_condition=None,
        current_best_url=None,
        all_time_min_total_minor=None,
        all_time_max_total_minor=None,
        observation_count=len(values),
        days_of_history=0,
        last_observed_at=None,
        sorted_totals=sorted(values),
    )
    # Sample a few percentile pairs.
    pcts = [1, 10, 25, 50, 75, 90, 99]
    for a, b in itertools.pairwise(pcts):
        pa = stats.percentile_at(a)
        pb = stats.percentile_at(b)
        assert pa is not None and pb is not None
        assert pa <= pb, f"percentile_at({a})={pa} > percentile_at({b})={pb} for {sorted(values)}"


def test_duplicate_observations_excluded_from_percentiles(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000015")
        # 3 real, 2 duplicates of the first
        base = _add_obs(s, book_id=book.id, total=100, source="wob_a")
        _add_obs(s, book_id=book.id, total=200, source="wob_b")
        _add_obs(s, book_id=book.id, total=300, source="wob_c")
        _add_obs(s, book_id=book.id, total=999, source="dup_x", is_duplicate_of=base.id)
        _add_obs(s, book_id=book.id, total=888, source="dup_y", is_duplicate_of=base.id)
        stats = compute_book_stats(book.id, s)

    assert stats.sorted_totals == [100, 200, 300]
    assert stats.observation_count == 3


def _add_obs_with_shipping(
    session: Session,
    *,
    book_id: int,
    price: int,
    shipping: int | None,
    source: str = "wob",
    seller: str | None = None,
    observed_at: datetime | None = None,
) -> models.PriceObservation:
    """Helper for shipping-aware tests. `shipping=None` mirrors a Keepa
    historical row; the view treats those as eligible buyable rows but the
    distribution-builder folds them in via the per-book estimate."""
    total = price if shipping is None else price + shipping
    obs = models.PriceObservation(
        book_id=book_id,
        source=source,
        seller=seller,
        condition="new",
        price_minor=price,
        currency="GBP",
        shipping_minor=shipping,
        total_minor=total,
        url=f"https://{source}/{price}",
        observed_at=observed_at or datetime.now(UTC),
        raw={},
    )
    session.add(obs)
    session.commit()
    session.refresh(obs)
    return obs


def test_window_excludes_observations_older_than_cutoff(engine_with_view, make_book):
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000020")
        _add_obs(s, book_id=book.id, total=100, source="recent",
                 observed_at=now - timedelta(days=10))
        _add_obs(s, book_id=book.id, total=999, source="old",
                 observed_at=now - timedelta(days=120))
        stats = compute_book_stats(book.id, s, window_days=90)

    assert stats.sorted_totals == [100]
    assert stats.percentile_window_days == 90


def test_window_default_is_90_days(engine_with_view, make_book):
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000021")
        _add_obs(s, book_id=book.id, total=100, source="recent",
                 observed_at=now - timedelta(days=30))
        _add_obs(s, book_id=book.id, total=200, source="old",
                 observed_at=now - timedelta(days=200))
        stats = compute_book_stats(book.id, s)

    assert stats.sorted_totals == [100]
    assert stats.percentile_window_days == 90


def test_shipping_estimate_folds_keepa_into_distribution(engine_with_view, make_book):
    """Two real observations carry shipping (£0 and £2.80); a Keepa-style
    NULL-shipping row at £15 item price should be folded in via the
    shipping cascade. With no per-(book, keepa) or per-source-global data
    for 'keepa', the cascade falls through to per-book median ([0, 280] →
    £1.40), giving an imputed total of 1500 + 140 = 1640.

    `shipping_estimate_minor` reports the value used to impute the CURRENT
    row's shipping. The current row here is the wob £20.00 listing with
    observed £0 shipping, so no imputation runs and the field is None.
    """
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000022")
        _add_obs_with_shipping(s, book_id=book.id, price=2000, shipping=0,
                               source="wob")
        _add_obs_with_shipping(s, book_id=book.id, price=1800, shipping=280,
                               source="amazon")
        _add_obs_with_shipping(s, book_id=book.id, price=1500, shipping=None,
                               source="keepa")
        stats = compute_book_stats(book.id, s)

    assert stats.current_best_shipping_minor == 0
    assert stats.shipping_estimate_minor is None  # current row had observed shipping
    # Distribution: real totals (2000, 2080) + cascade-imputed (1500 + 140)
    assert stats.sorted_totals == [1640, 2000, 2080]


def test_cascade_terminal_default_imputes_when_no_shipping_signal(
    engine_with_view, make_book,
):
    """A book whose only observations have NULL shipping (all Keepa) still
    gets a distribution — the cascade's terminal default kicks in so rows
    aren't dropped just because no shipping was ever observed. The
    `shipping_estimate_minor` field stays None because the view returns no
    `current_best` for Keepa-only books (Keepa is excluded from `buyable`),
    so there's no current row to estimate against."""
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000023")
        _add_obs_with_shipping(s, book_id=book.id, price=1500, shipping=None,
                               source="keepa")
        _add_obs_with_shipping(s, book_id=book.id, price=1600, shipping=None,
                               source="keepa")
        stats = compute_book_stats(book.id, s, default_shipping_minor=280)

    assert sorted(stats.sorted_totals) == [1780, 1880]
    assert stats.all_time_min_total_minor == 1780


def test_current_percentile_rank_basic(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000024")
        for total in (1000, 1500, 2000, 2500, 3000):
            _add_obs_with_shipping(
                s, book_id=book.id, price=total, shipping=0,
                source=f"src_{total}",
            )
        stats = compute_book_stats(book.id, s)

    # current_best = cheapest of 5 → rank 1/5 = 20%
    assert stats.current_best_total_minor == 1000
    assert stats.windows["3m"].rank == 20
    assert stats.current_effective_total_minor == 1000


def test_current_effective_total_uses_estimate_when_current_shipping_null(
    engine_with_view, make_book,
):
    """If the current row has NULL shipping but we have observed shipping
    elsewhere for the book, the effective total used for percentile rank is
    price + median observed shipping."""
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000025")
        _add_obs_with_shipping(s, book_id=book.id, price=2000, shipping=300,
                               source="wob")
        # Cheapest row has NULL shipping → wins current_best (view doesn't
        # filter on shipping NULL), but rank comparison estimates £3 shipping.
        _add_obs_with_shipping(s, book_id=book.id, price=1500, shipping=None,
                               source="bookfinder")
        stats = compute_book_stats(book.id, s)

    assert stats.shipping_estimate_minor == 300
    # current_best is the £15 row (view picks lowest total); effective adds
    # the £3 estimate → £18
    assert stats.current_best_total_minor == 1500
    assert stats.current_effective_total_minor == 1800


# ---------------------------------------------------------------------------
# Cascade behaviour: each fallback level fires in the right circumstances.
# ---------------------------------------------------------------------------


def test_cascade_step1_uses_observed_shipping(engine_with_view, make_book):
    """When a row carries observed shipping, the cascade never runs — the
    row's own total is used as-is regardless of medians."""
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000030")
        _add_obs_with_shipping(s, book_id=book.id, price=1000, shipping=250,
                               source="wob")
        _add_obs_with_shipping(s, book_id=book.id, price=2000, shipping=400,
                               source="amazon")
        stats = compute_book_stats(book.id, s)

    assert stats.sorted_totals == [1250, 2400]


def test_cascade_step2_uses_book_source_median(engine_with_view, make_book):
    """A NULL-shipping row prefers the per-(book, source) median over the
    per-book median across sources."""
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000031")
        # WOB: two observed-shipping rows, median = 50
        _add_obs_with_shipping(s, book_id=book.id, price=1000, shipping=0,
                               source="wob")
        _add_obs_with_shipping(s, book_id=book.id, price=1100, shipping=100,
                               source="wob")
        # Amazon: one row with high shipping, lifts the per-book median
        _add_obs_with_shipping(s, book_id=book.id, price=2000, shipping=500,
                               source="amazon")
        # A WOB row with NULL shipping — cascade step 2 should fire and use
        # the WOB-specific median (50), NOT the per-book median (100).
        _add_obs_with_shipping(s, book_id=book.id, price=900, shipping=None,
                               source="wob")
        stats = compute_book_stats(book.id, s)

    # Imputed total for the NULL row: 900 + 50 = 950
    assert 950 in stats.sorted_totals


def test_cascade_step3_uses_source_global_median(engine_with_view, make_book):
    """When the book has no per-(book, source) history for a given source,
    fall back to the per-source-global median across all books."""
    with Session(engine_with_view) as s:
        # Seed a second book with WOB shipping data so the global median
        # exists for source='wob'.
        other = make_book(s, isbn13="9780000000040")
        _add_obs_with_shipping(s, book_id=other.id, price=500, shipping=180,
                               source="wob")
        _add_obs_with_shipping(s, book_id=other.id, price=600, shipping=180,
                               source="wob")

        book = make_book(s, isbn13="9780000000032")
        # Book has only ONE WOB row, and it lacks shipping — so per-(book,
        # source) median is empty. Per-source-global = 180.
        _add_obs_with_shipping(s, book_id=book.id, price=800, shipping=None,
                               source="wob")
        # Add an amazon row with observed shipping so per-book median exists
        # — we want to prove step 3 wins over step 4 (per-book median = 900).
        _add_obs_with_shipping(s, book_id=book.id, price=2000, shipping=900,
                               source="amazon")
        # Bypass the sparse-bucket threshold; this test is about cascade
        # tier ordering, not the threshold.
        stats = compute_book_stats(book.id, s, min_global_median_observations=1)

    # The WOB null-shipping row should be imputed at 800 + 180 = 980 (step 3),
    # not 800 + 900 = 1700 (step 4).
    assert 980 in stats.sorted_totals
    assert 1700 not in stats.sorted_totals


def test_cascade_step4_falls_back_to_per_book_median(engine_with_view, make_book):
    """Keepa rows always fall through to per-book median: there's never a
    per-(book, keepa) median (Keepa never carries shipping) and typically
    no per-source-global for keepa either."""
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000033")
        _add_obs_with_shipping(s, book_id=book.id, price=1500, shipping=300,
                               source="wob")
        _add_obs_with_shipping(s, book_id=book.id, price=1500, shipping=None,
                               source="keepa")
        stats = compute_book_stats(book.id, s)

    # Keepa row imputed at 1500 + 300 = 1800 via step 4
    assert sorted(stats.sorted_totals) == [1800, 1800]


def test_cascade_terminal_default_used_when_all_tiers_miss(
    engine_with_view, make_book,
):
    """If a book has zero observed shipping anywhere AND no per-(source,
    seller_class) global fallback applies, NULL-shipping rows fall through
    to the cascade's terminal default rather than being dropped."""
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000034")
        _add_obs_with_shipping(s, book_id=book.id, price=1500, shipping=None,
                               source="keepa")
        _add_obs_with_shipping(s, book_id=book.id, price=1600, shipping=None,
                               source="keepa")
        stats = compute_book_stats(book.id, s, default_shipping_minor=280)

    assert sorted(stats.sorted_totals) == [1780, 1880]
    assert stats.all_time_min_total_minor == 1780


# ---------------------------------------------------------------------------
# All-time bounds: Keepa-inclusive via cascade.
# ---------------------------------------------------------------------------


def test_all_time_min_includes_keepa_via_imputation(engine_with_view, make_book):
    """A Keepa archive row cheaper than any live offer should set the
    all-time low (after shipping imputation). The legacy view excluded
    Keepa and would have reported a higher floor."""
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000035")
        # Live offers: 2000 and 2200 (with observed shipping)
        _add_obs_with_shipping(s, book_id=book.id, price=1900, shipping=100,
                               source="wob")
        _add_obs_with_shipping(s, book_id=book.id, price=2100, shipping=100,
                               source="amazon")
        # Keepa: historic £14 item; cascade imputes shipping = 100 via step 4
        _add_obs_with_shipping(s, book_id=book.id, price=1400, shipping=None,
                               source="keepa")
        stats = compute_book_stats(book.id, s)

    # 1400 + 100 = 1500 — that's the new all-time min
    assert stats.all_time_min_total_minor == 1500
    assert stats.all_time_max_total_minor == 2200


# ---------------------------------------------------------------------------
# Windowed percentile bands (1m / 3m / 12m).
# ---------------------------------------------------------------------------


def test_windows_partition_correctly_by_observed_at(engine_with_view, make_book):
    """Each window contains exactly the imputed totals from its cutoff."""
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000036")
        # 5 days ago, 60 days ago, 200 days ago — fall into 1m, 3m, 12m
        _add_obs_with_shipping(s, book_id=book.id, price=1000, shipping=0,
                               source="a", observed_at=now - timedelta(days=5))
        _add_obs_with_shipping(s, book_id=book.id, price=1500, shipping=0,
                               source="b", observed_at=now - timedelta(days=60))
        _add_obs_with_shipping(s, book_id=book.id, price=2000, shipping=0,
                               source="c", observed_at=now - timedelta(days=200))
        stats = compute_book_stats(book.id, s)

    assert stats.windows["1m"].count == 1
    assert stats.windows["3m"].count == 2
    assert stats.windows["12m"].count == 3
    # p50 should differ across windows because the membership differs.
    assert stats.windows["1m"].p50 == 1000
    assert stats.windows["3m"].p50 in (1000, 1250, 1500)  # linear interp
    assert stats.windows["12m"].p50 == 1500


def test_windows_empty_when_no_observations(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000037")
        stats = compute_book_stats(book.id, s)

    for label in ("1m", "3m", "12m"):
        w = stats.windows[label]
        assert w.count == 0
        assert w.rank is None
        assert w.p5 is w.p25 is w.p50 is w.p75 is w.p95 is None


def test_window_rank_reflects_current_position(engine_with_view, make_book):
    """The window's rank field reports where the current effective total
    sits within that window's imputed distribution."""
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000038")
        # 5 observations in last 20 days at totals 100, 200, 300, 400, 500
        for i, total in enumerate([500, 400, 300, 200, 100]):
            _add_obs_with_shipping(
                s, book_id=book.id, price=total, shipping=0,
                source=f"src_{i}",
                observed_at=now - timedelta(days=i * 3),
            )
        stats = compute_book_stats(book.id, s)

    # current_best is the cheapest row (100), so rank = 1/5 = 20%
    assert stats.windows["1m"].count == 5
    assert stats.windows["1m"].rank == 20


# ---------------------------------------------------------------------------
# Seller-class cascade keying — Amazon-fulfilled vs third-party shipping.
# ---------------------------------------------------------------------------


def test_seller_class_classification():
    assert seller_class("Amazon") == "amazon_fulfilled"
    assert seller_class("Amazon Marketplace") == "amazon_fulfilled"
    assert seller_class("Amazon.co.uk") == "amazon_fulfilled"
    assert seller_class("TheGlobalBuyer") == "third_party"
    assert seller_class("World of Books") == "third_party"
    assert seller_class(None) == "third_party"
    assert seller_class("") == "third_party"


def test_third_party_amazon_does_not_inherit_prime_zero_shipping(
    engine_with_view, make_book,
):
    """Regression: when most Amazon offers globally are Prime (shipping=0),
    a third-party Amazon row whose own shipping is NULL should NOT inherit
    the Prime-dominant zero. With (source, seller_class) keying it falls
    past the empty third-party bucket to the cascade's terminal default."""
    with Session(engine_with_view) as s:
        # Foreign book: lots of Prime Amazon shipping=0 observations.
        prime_book = make_book(s, isbn13="9780000000050")
        for _ in range(5):
            _add_obs_with_shipping(
                s, book_id=prime_book.id, price=2000, shipping=0,
                source="amazon", seller="Amazon",
            )

        # Target book: only one live row, third-party Amazon seller, no shipping.
        target = make_book(s, isbn13="9780000000051")
        _add_obs_with_shipping(
            s, book_id=target.id, price=3795, shipping=None,
            source="amazon", seller="TheGlobalBuyer",
        )
        # Disable sparse-bucket threshold so the test isolates the
        # seller-class behavior (the threshold is verified separately).
        stats = compute_book_stats(
            target.id, s,
            default_shipping_minor=280,
            min_global_median_observations=1,
        )

    # NOT 3795+0=3795 (the buggy old behavior). Instead 3795+280=4075.
    assert stats.current_effective_total_minor == 4075
    assert stats.shipping_estimate_minor == 280


def test_amazon_fulfilled_still_uses_prime_global_median(
    engine_with_view, make_book,
):
    """An Amazon-fulfilled row with NULL shipping correctly inherits the
    (amazon, amazon_fulfilled) global median (typically 0 for Prime)."""
    with Session(engine_with_view) as s:
        prime_book = make_book(s, isbn13="9780000000052")
        for _ in range(5):
            _add_obs_with_shipping(
                s, book_id=prime_book.id, price=2000, shipping=0,
                source="amazon", seller="Amazon",
            )

        target = make_book(s, isbn13="9780000000053")
        _add_obs_with_shipping(
            s, book_id=target.id, price=1599, shipping=None,
            source="amazon", seller="Amazon",
        )
        # Bypass sparse-bucket threshold; this test asserts tier-2 hit.
        stats = compute_book_stats(
            target.id, s,
            default_shipping_minor=280,
            min_global_median_observations=1,
        )

    # Prime median is 0, so effective = 1599 + 0 = 1599 (not 1599 + default).
    assert stats.current_effective_total_minor == 1599
    assert stats.shipping_estimate_minor == 0


def test_sparse_global_bucket_excluded_below_threshold(
    engine_with_view, make_book,
):
    """If only a handful of (source, seller_class) shipping rows have ever
    been observed, the bucket is excluded from the cascade tier so a sparse
    coincidence (e.g. 3 third-party Amazon rows all at £0) doesn't impute
    free shipping onto an unrelated row."""
    with Session(engine_with_view) as s:
        # Only 3 third-party Amazon observations globally, all at 0.
        thin = make_book(s, isbn13="9780000000060")
        for _ in range(3):
            _add_obs_with_shipping(
                s, book_id=thin.id, price=2000, shipping=0,
                source="amazon", seller="TheGlobalBuyer",
            )

        target = make_book(s, isbn13="9780000000061")
        _add_obs_with_shipping(
            s, book_id=target.id, price=3795, shipping=None,
            source="amazon", seller="AnotherSeller",
        )
        # Threshold = 10 → the 3-row bucket is excluded → terminal default fires.
        stats = compute_book_stats(
            target.id, s,
            default_shipping_minor=280,
            min_global_median_observations=10,
        )

    assert stats.shipping_estimate_minor == 280
    assert stats.current_effective_total_minor == 4075


def test_keepa_only_book_distribution_uses_default(
    engine_with_view, make_book,
):
    """The pre-fix degenerate case: Keepa-only book whose live offer is a
    third-party seller with no shipping. Pre-fix: imputed = [], distribution
    empty. Post-fix: every row imputed via terminal default; distribution
    reflects historical price spread + the default shipping increment."""
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000054")
        # Keepa history: 5 historical observations.
        for i, price in enumerate([1400, 1500, 1600, 1700, 1800]):
            _add_obs_with_shipping(
                s, book_id=book.id, price=price, shipping=None,
                source="keepa", seller="Amazon Marketplace",
                observed_at=now - timedelta(days=i * 20),
            )
        # One live third-party Amazon row, no shipping.
        _add_obs_with_shipping(
            s, book_id=book.id, price=1500, shipping=None,
            source="amazon", seller="TheGlobalBuyer",
        )
        stats = compute_book_stats(book.id, s, default_shipping_minor=280)

    # Every imputed row = price + 280: 5 Keepa (1680..2080) + 1 live (1780).
    assert sorted(stats.sorted_totals) == [1680, 1780, 1780, 1880, 1980, 2080]
    assert stats.all_time_min_total_minor == 1680
    assert stats.all_time_max_total_minor == 2080
    # Current = 1500 + 280 = 1780; rank = bisect_right(...,1780) / 6 = 3/6 = 50%.
    assert stats.current_effective_total_minor == 1780
    assert stats.windows["3m"].rank == 50
