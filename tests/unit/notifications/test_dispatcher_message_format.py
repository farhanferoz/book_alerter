"""Regression tests for `AlertPipeline._format_message`.

Prior-format bugs pinned here:

- A1: the old phrase `(was median 19.60, +35%)` made "below median" read
  as a positive percentage, confusing recipients into thinking the
  current price was ABOVE the median.
- A2: the old format had no "total" / breakdown signal, so the
  recipient could not tell whether the displayed price was item-only
  or item+shipping.
- S3 (adversarial shipping-chain review, 2026-09-04): when shipping was
  never observed, `current_best_total_minor` is item-only (scheduler
  folds unknown shipping to 0 before storing `total_minor`) but the
  message still labelled it "total", and the unknown-shipping branch
  dropped the breakdown entirely instead of saying shipping was
  estimated. Separately, `p50` is built from cascade-*imputed* totals
  (`stats.py`'s `imputed` list) while the old `current` was raw and
  shipping-less, so the "N% below median" figure compared two different
  metrics. D34: every user-facing price must read
  `current_effective_total_minor`, never `current_best_total_minor`.
"""

from __future__ import annotations

from book_alerter.notifications.dispatcher import AlertPipeline
from book_alerter.stats import BookStats, WindowStats

# Sentinel so `_stats()` can default `effective` to `current` (the realistic
# case: current_effective_total_minor == current_best_total_minor whenever
# shipping was actually observed) while still letting a test set it to a
# genuinely different value to exercise the unknown-shipping divergence —
# same pattern as `tests/conftest.py`'s `transient_stats` fixture.
_USE_CURRENT: object = object()


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
    effective: int | object | None = _USE_CURRENT,
    shipping_estimate: int | None = None,
) -> BookStats:
    if effective is _USE_CURRENT:
        effective = current
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
        current_effective_total_minor=effective,
        shipping_estimate_minor=shipping_estimate,
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
    assert "(item 10.20, incl. 2.50 shipping)" in msg


def test_message_free_shipping_renders_as_free_ship() -> None:
    s = _stats(current=1270, item_minor=1270, ship_minor=0, p50_90d=1960)
    msg = _format(s)
    assert "total 12.70 GBP" in msg
    assert "(item 12.70, free shipping)" in msg


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
    assert msg == "[NEW_LOW] Test Book — total 12.70 GBP (item 10.20, incl. 2.50 shipping)"


def test_message_at_median_when_pct_rounds_to_zero() -> None:
    """When current and median differ by less than 0.5%, "0% above" /
    "0% below" contradicts the small sign and confuses readers. Phrase
    it as "at <window> median" instead."""
    # current == p50 exactly
    s = _stats(current=2000, item_minor=2000, ship_minor=0, p50_90d=2000)
    msg = _format(s)
    assert ", at 90d median 20.00" in msg
    assert "below" not in msg and "above" not in msg

    # current 0.2% above median
    s = _stats(current=2004, item_minor=2004, ship_minor=0, p50_90d=2000)
    msg = _format(s)
    assert ", at 90d median 20.00" in msg

    # current 0.2% below median
    s = _stats(current=1996, item_minor=1996, ship_minor=0, p50_90d=2000)
    msg = _format(s)
    assert ", at 90d median 20.00" in msg


def test_message_uses_effective_total_not_raw_when_shipping_unknown() -> None:
    """S3: `current_best_total_minor` is item-only when shipping was never
    observed. The message must report `current_effective_total_minor`
    (the cascade-imputed figure) or a TARGET_HIT/BUY notification quotes a
    price the buyer will not actually pay."""
    s = _stats(
        current=799,  # raw: item price only, unknown shipping folded to 0 upstream
        item_minor=799,
        ship_minor=None,  # never observed
        effective=1079,  # cascade-imputed: item + estimated shipping
        shipping_estimate=280,
        p50_90d=1200,
    )
    msg = _format(s, kind="target_hit")
    assert msg.startswith("[TARGET_HIT] Test Book — total 10.79 GBP")
    assert "total 7.99" not in msg


def test_message_shows_estimate_when_shipping_unknown_but_estimable() -> None:
    """Wording matched verbatim from the existing FE treatment
    (web/src/components/books/detail/SnapshotCard.tsx: "shipping not
    listed (ranked using ~£X estimate)") rather than invented here, so
    the notification and the detail page agree."""
    s = _stats(
        current=799, item_minor=799, ship_minor=None,
        effective=1079, shipping_estimate=280, p50_90d=1200,
    )
    msg = _format(s, kind="target_hit")
    assert "shipping not listed" in msg
    assert "~2.80 estimate" in msg


def test_message_shipping_not_listed_with_no_estimate_available() -> None:
    """Defensive (today's `stats.py` always sets an estimate alongside a
    NULL current_best_shipping_minor, so this shouldn't arise in
    practice — see `_stats_for_one_item`): a stats bundle with neither a
    known shipping figure nor an estimate must still say so honestly
    rather than silently dropping the breakdown and calling the
    item-only figure a "total"."""
    s = _stats(
        current=1270, item_minor=1270, ship_minor=None,
        effective=1270, shipping_estimate=None, p50_90d=1960,
    )
    msg = _format(s)
    assert msg.startswith("[NEW_LOW] Test Book — total 12.70 GBP")
    assert "shipping not listed" in msg
    assert "estimate" not in msg


def test_message_pct_uses_effective_total_against_imputed_median() -> None:
    """S3's second defect: `p50` is built from cascade-*imputed* totals,
    so comparing it against the raw shipping-less current overstated how
    far below median the item was. Both sides of the percentage must be
    the same metric (D34)."""
    # Raw 7.99 vs median 12.00 reads ~33% below; the true gap (against the
    # effective 10.79) is ~10%.
    s = _stats(
        current=799, item_minor=799, ship_minor=None,
        effective=1079, shipping_estimate=280, p50_90d=1200,
    )
    msg = _format(s)
    assert "10% below 90d median 12.00" in msg
    assert "33%" not in msg
