"""World of Books delivery rule (finding F2).

Shipping used to be hard-coded to zero, which made every sub-£5 WoB offer look
99p cheaper than it is — and WoB is where the cheap used copies come from, so
the error landed exactly where it mattered most.
"""

from __future__ import annotations

import pytest

from book_alerter.sources.wob import (
    _WOB_ECONOMY_DELIVERY_MINOR,
    _WOB_FREE_DELIVERY_THRESHOLD_MINOR,
    _delivery_minor_for,
)


@pytest.mark.parametrize(
    ("price_minor", "expected"),
    [
        (1, _WOB_ECONOMY_DELIVERY_MINOR),
        (499, _WOB_ECONOMY_DELIVERY_MINOR),
        (500, 0),
        (501, 0),
        (10_000, 0),
    ],
)
def test_delivery_charge_switches_at_the_threshold(price_minor: int, expected: int) -> None:
    assert _delivery_minor_for(price_minor) == expected


# The rule as World of Books publishes it, restated independently of the
# implementation so a change to their terms fails here rather than silently
# skewing totals.
PUBLISHED_FREE_DELIVERY_THRESHOLD_MINOR = 5_00
PUBLISHED_ECONOMY_DELIVERY_MINOR = 99


def test_constants_match_the_published_rule() -> None:
    assert _WOB_FREE_DELIVERY_THRESHOLD_MINOR == PUBLISHED_FREE_DELIVERY_THRESHOLD_MINOR
    assert _WOB_ECONOMY_DELIVERY_MINOR == PUBLISHED_ECONOMY_DELIVERY_MINOR


def test_parsed_offers_carry_the_rule_not_a_hard_coded_zero() -> None:
    """The parser must apply the rule, not just define it."""
    from book_alerter.sources import wob

    cheap = _delivery_minor_for(250)
    dear = _delivery_minor_for(2_500)
    assert (cheap, dear) == (_WOB_ECONOMY_DELIVERY_MINOR, 0)
    # and the call site is wired to the helper rather than a literal
    src = (wob.__file__ or "").replace(".pyc", ".py")
    with open(src, encoding="utf-8") as fh:
        body = fh.read()
    assert "shipping_minor=_delivery_minor_for(price_minor)" in body
    assert "shipping_minor=0" not in body
