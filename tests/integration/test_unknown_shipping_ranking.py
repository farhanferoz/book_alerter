"""Unknown shipping must never rank as free (finding F3, plan task T2.4).

The old SQL `current_best` ranked on `total_minor`, and `_persist` computes
`total = price + (shipping or 0)`. So a row whose shipping was never scraped
was stored as though delivery were free, and therefore beat the *same offer*
with a known £2.80 delivery charge. On the production copy that affected 1,444
third-party Amazon rows and all 8,033 Keepa rows.

T3.1 moved current-best selection into Python and routes every row through
`effective_shipping`, which substitutes the cascade estimate when shipping is
unknown. These tests pin that behaviour from both sides — an unknown-shipping
row must lose when its estimate is worse, and win when its estimate is better.
A test that only checked one direction would pass against a rule that ignored
shipping entirely.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from book_alerter.db import models
from book_alerter.stats import compute_book_stats, effective_shipping

KNOWN_SHIPPING_MINOR = 280
PRICE_MINOR = 2_000
# A cascade estimate deliberately worse than any real shipping figure here.
STUB_CASCADE_MINOR = 999
# The cascade's fallback when nothing can be learned from history. Set well
# ABOVE the known charge on purpose: with the production default of 280 it
# would exactly tie, and the tie-break (source, condition, seller) would decide
# the winner instead of the shipping rule — the test would then pass even if
# unknown shipping were still treated as free.
ESTIMATE_WORSE_THAN_KNOWN_MINOR = 500


# Every offer in one scrape must share a timestamp, because that is what
# `_persist` does (it computes `now` once, before the loop). The live-offers
# view keys "present in the most recent scrape" off equality with the newest
# `observed_at` for that source, so giving each row its own `datetime.now()`
# makes every offer look like a separate scrape and silently drops all but the
# last one. Getting this wrong cost a debugging round.
SCRAPED_AT = datetime.now(UTC)


def _offer(
    session: Session,
    *,
    book_id: int,
    seller: str,
    shipping_minor: int | None,
    price_minor: int = PRICE_MINOR,
    source: str = "amazon",
) -> models.PriceObservation:
    """One live offer. `total_minor` is written exactly as `_persist` writes
    it, including the `or 0` that caused the bug — the fix must not depend on
    the stored total being corrected."""
    obs = models.PriceObservation(
        book_id=book_id,
        source=source,
        condition="new",
        seller=seller,
        price_minor=price_minor,
        currency="GBP",
        shipping_minor=shipping_minor,
        total_minor=price_minor + (shipping_minor or 0),
        url=f"https://amazon.example/{seller}",
        observed_at=SCRAPED_AT,
        last_seen_at=SCRAPED_AT,
        raw={},
    )
    session.add(obs)
    session.commit()
    session.refresh(obs)
    return obs


def test_effective_shipping_never_treats_unknown_as_free() -> None:
    """The seam itself, independent of any database."""
    known, known_est = effective_shipping(
        "amazon", "SomeSeller", KNOWN_SHIPPING_MINOR, cascade=lambda s, x: STUB_CASCADE_MINOR
    )
    assert (known, known_est) == (KNOWN_SHIPPING_MINOR, False)

    unknown, unknown_est = effective_shipping(
        "amazon", "SomeSeller", None, cascade=lambda s, x: STUB_CASCADE_MINOR
    )
    assert unknown == STUB_CASCADE_MINOR, "unknown shipping must use the cascade estimate, not 0"
    assert unknown_est is True, "an estimated figure must be reported as an estimate"


def test_known_shipping_beats_unknown_when_the_estimate_is_worse(
    engine_with_view, make_book
):
    """Same price, one offer with a known £2.80 and one with no shipping data.

    With no other rows to learn from, the cascade falls back to its global
    default, which is worse than £2.80 — so the *known* offer must win. Under
    the old rule the unknown-shipping row won by pretending delivery was free.
    """
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000021")
        _offer(s, book_id=book.id, seller="KnownShip", shipping_minor=KNOWN_SHIPPING_MINOR)
        _offer(s, book_id=book.id, seller="NoShipData", shipping_minor=None)
        stats = compute_book_stats(
            book.id, s, default_shipping_minor=ESTIMATE_WORSE_THAN_KNOWN_MINOR
        )

    assert stats.current_best_seller == "KnownShip", (
        "an offer with unknown shipping must not win merely because its stored "
        "total_minor omitted the shipping it never scraped"
    )
    assert stats.current_best_total_minor == PRICE_MINOR + KNOWN_SHIPPING_MINOR


def test_unknown_shipping_still_wins_when_its_estimate_is_better(
    engine_with_view, make_book
):
    """The rule is 'estimate it', not 'penalise it'.

    Here the unknown-shipping offer is £5 cheaper, so even a pessimistic
    estimate leaves it ahead. If this fails, the fix has over-corrected into
    always preferring rows with observed shipping.
    """
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000022")
        _offer(s, book_id=book.id, seller="KnownShip", shipping_minor=KNOWN_SHIPPING_MINOR)
        _offer(
            s,
            book_id=book.id,
            seller="NoShipData",
            shipping_minor=None,
            price_minor=PRICE_MINOR - 500,
        )
        stats = compute_book_stats(
            book.id, s, default_shipping_minor=ESTIMATE_WORSE_THAN_KNOWN_MINOR
        )

    assert stats.current_best_seller == "NoShipData"


def test_a_free_offer_is_still_recognised_as_free(engine_with_view, make_book):
    """Shipping of 0 is data, not absence of data. An offer that genuinely
    ships free must keep beating one that charges — otherwise the fix has
    conflated 'zero' with 'unknown'."""
    with Session(engine_with_view) as s:
        book = make_book(s, isbn13="9780000000023")
        _offer(s, book_id=book.id, seller="ChargesShip", shipping_minor=KNOWN_SHIPPING_MINOR)
        _offer(s, book_id=book.id, seller="ShipsFree", shipping_minor=0)
        stats = compute_book_stats(
            book.id, s, default_shipping_minor=ESTIMATE_WORSE_THAN_KNOWN_MINOR
        )

    assert stats.current_best_seller == "ShipsFree"
    assert stats.current_best_total_minor == PRICE_MINOR
