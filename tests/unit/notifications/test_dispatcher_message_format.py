"""Regression tests for `AlertPipeline._format_message`.

Two prior-format bugs are pinned here:

- A1: the old phrase `(was median 19.60, +35%)` made "below median" read
  as a positive percentage, confusing recipients into thinking the
  current price was ABOVE the median.
- A2: the old format had no "total" / breakdown signal, so the
  recipient could not tell whether the displayed price was item-only
  or item+shipping.
"""

from __future__ import annotations

from book_alerter.notifications.dispatcher import AlertPipeline
from book_alerter.stats import BookStats, WindowStats


class _FakeItem:
    """Minimum surface for `_format_message(item=...)`."""
    title: str = "Test Book"
    currency: str = "GBP"


def _stats(
    *,
    current: int,
    item_minor: int | None,
    ship_minor: int | None,
    p50_90d: int | None,
) -> BookStats:
    windows = {"3m": WindowStats(count=10, p50=p50_90d)}
    return BookStats(
        book_id=1,
        current_best_total_minor=current,
        current_best_price_minor=item_minor,
        current_best_shipping_minor=ship_minor,
        current_best_source="amazon",
        current_best_seller="X",
        current_best_condition="new",
        current_best_url="https://x",
        all_time_min_total_minor=None,
        all_time_max_total_minor=None,
        observation_count=10,
        days_of_history=30,
        last_observed_at=None,
        percentile_window_days=90,
        windows=windows,
    )


def _format(stats: BookStats, kind: str = "new_low") -> str:
    # Call as bound method without constructing the full pipeline.
    return AlertPipeline._format_message(  # type: ignore[arg-type]
        None,  # self — unused
        _FakeItem(),  # type: ignore[arg-type]
        kind,
        stats,
    )


def test_message_labels_total_and_shows_item_plus_ship_breakdown() -> None:
    s = _stats(current=1270, item_minor=1020, ship_minor=250, p50_90d=1960)
    msg = _format(s)
    assert msg.startswith("[NEW_LOW] Test Book — total 12.70 GBP")
    assert "(item 10.20 + 2.50 ship)" in msg


def test_message_free_shipping_renders_as_free_ship() -> None:
    s = _stats(current=1270, item_minor=1270, ship_minor=0, p50_90d=1960)
    msg = _format(s)
    assert "total 12.70 GBP" in msg
    assert "(item 12.70, free ship)" in msg


def test_message_pct_below_median_phrasing_no_sign_ambiguity() -> None:
    """Current 12.70 vs 90d median 19.60 → 35% below — must say "below"."""
    s = _stats(current=1270, item_minor=1020, ship_minor=250, p50_90d=1960)
    msg = _format(s)
    assert "35% below 90d median 19.60" in msg
    # The old format embedded "+35%" — make sure that's gone.
    assert "+35%" not in msg
    assert "was median" not in msg


def test_message_pct_above_median_phrasing_when_current_exceeds_median() -> None:
    """When current > median (e.g. for a TARGET_HIT on a target above
    median), the phrasing must flip to "above" and present a positive
    percentage too."""
    s = _stats(current=2500, item_minor=2500, ship_minor=0, p50_90d=2000)
    msg = _format(s, kind="target_hit")
    assert "25% above 90d median 20.00" in msg


def test_message_no_delta_when_p50_unavailable() -> None:
    s = _stats(current=1270, item_minor=1020, ship_minor=250, p50_90d=None)
    msg = _format(s)
    assert msg == "[NEW_LOW] Test Book — total 12.70 GBP (item 10.20 + 2.50 ship)"


def test_message_omits_breakdown_when_shipping_unknown() -> None:
    """Defensive: alerts shouldn't fire on shipping-unknown rows (the
    book_stats view filters those out before signals compute), but the
    formatter must not crash if it ever encounters one."""
    s = _stats(current=1270, item_minor=1270, ship_minor=None, p50_90d=1960)
    msg = _format(s)
    assert msg.startswith("[NEW_LOW] Test Book — total 12.70 GBP")
    assert "item " not in msg
    assert "ship" not in msg
