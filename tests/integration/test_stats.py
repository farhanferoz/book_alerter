"""Tests for `compute_book_stats` — reads from the `book_stats` view."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, strategies as st
from sqlmodel import Session

from book_alerter.db import models
from book_alerter.stats import BookStats, _percentiles, compute_book_stats


def _add_obs(session: Session, *, book_id: int, total: int, source: str = "wob",
             observed_at: datetime | None = None, is_duplicate_of: int | None = None) -> models.PriceObservation:
    obs = models.PriceObservation(
        book_id=book_id,
        source=source,
        condition="new",
        price_minor=total,
        currency="GBP",
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
    assert stats.p25_total_minor is None
    assert stats.p50_total_minor is None
    assert stats.p75_total_minor is None
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
    assert stats.p25_total_minor == 500
    assert stats.p50_total_minor == 500
    assert stats.p75_total_minor == 500
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
    assert stats.p25_total_minor == 150
    assert stats.p50_total_minor == 200
    assert stats.p75_total_minor == 250
    assert stats.observation_count == 3
    assert stats.sorted_totals == [100, 200, 300]


def test_ten_observations_p25_lt_p50_lt_p75(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000013")
        totals = [100 * i for i in range(1, 11)]  # 100..1000
        for i, total in enumerate(totals):
            _add_obs(s, book_id=book.id, total=total, source=f"src_{i:02d}")
        stats = compute_book_stats(book.id, s)

    assert stats.p25_total_minor is not None
    assert stats.p50_total_minor is not None
    assert stats.p75_total_minor is not None
    assert stats.p25_total_minor < stats.p50_total_minor < stats.p75_total_minor
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
    assert stats.percentile_at(50) == stats.p50_total_minor
    # p10 of [100,200,...,1000] via linear interp:
    #   idx = 0.10 * 9 = 0.9 -> 100 + (200-100)*0.9 = 190
    assert stats.percentile_at(10) == 190
    assert stats.percentile_at(10) < stats.p25_total_minor


def test_percentile_at_returns_none_for_empty():
    empty = BookStats(
        book_id=1,
        current_best_total_minor=None,
        current_best_source=None,
        current_best_seller=None,
        current_best_condition=None,
        current_best_url=None,
        p25_total_minor=None,
        p50_total_minor=None,
        p75_total_minor=None,
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
        current_best_source=None,
        current_best_seller=None,
        current_best_condition=None,
        current_best_url=None,
        p25_total_minor=None,
        p50_total_minor=None,
        p75_total_minor=None,
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
        current_best_source=None,
        current_best_seller=None,
        current_best_condition=None,
        current_best_url=None,
        p25_total_minor=None,
        p50_total_minor=None,
        p75_total_minor=None,
        all_time_min_total_minor=None,
        all_time_max_total_minor=None,
        observation_count=len(values),
        days_of_history=0,
        last_observed_at=None,
        sorted_totals=sorted(values),
    )
    # Sample a few percentile pairs.
    pcts = [1, 10, 25, 50, 75, 90, 99]
    for a, b in zip(pcts, pcts[1:]):
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


def test_percentiles_helper_directly():
    assert _percentiles([]) == (None, None, None)
    assert _percentiles([42]) == (42, 42, 42)
    p25, p50, p75 = _percentiles([100, 200, 300])
    assert (p25, p50, p75) == (150, 200, 250)
