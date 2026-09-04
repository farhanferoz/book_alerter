"""T4.2 — product-page parser hardening, pinned against every REAL captured
fixture under tests/fixtures/amazon/products/.

An audit found seven committed product fixtures that no test loaded,
violating the plan's own standard ("no fixture without a test that loads
it"). This file is where they earn their keep. What T0.4 established about
each, verified by DOM node counts rather than substring greps:

- B0F3NVWM37-uk-{dp,aod} — multi-seller, non-Amazon-brand, 10 real AOD
  rows, 9 distinct sellers, genuine used_vg/used_g offers. The aod side is
  already exercised by T2.5's acceptance test in test_amazon_parser.py;
  this file adds the dp side and the seller/condition/track_used coverage
  T2.5 didn't need.
- B0CYT8WL1G-uk-{dp,aod} — the variant page (adidas). Its own canonical
  link points at a DIFFERENT ASIN (B0DLSB1WWK) on both dp and aod
  fetches — the exact F26 finding. The dp side still renders the selected
  variant's buy box correctly (Amazon serves the buy box regardless of the
  canonical mismatch) and is genuinely usable; the aod side is not — see
  the dedicated test below for why that's correct on both counts, not a
  gap.
- B0GX54WT36-uk-{dp,aod} — currently unavailable:
  #outOfStockBuyBox_feature_div=1, no price, no add-to-cart, own canonical
  matches its own requested ASIN (a genuine "nothing to sell" response,
  not a wrong-page one).
- B09B96TG33-uk-aod — the genuine single-seller empty listing (0
  #aod-offer, 1 #aod-pinned-offer). The dp side is already covered by the
  T2.7 regression test in test_amazon_parser.py.

Two behaviours were already correct going in and needed no code change,
only these pinning tests — said plainly rather than inventing a change to
justify the task: the empty single-seller listing already returned []
without raising, and parse_dp on the Echo Dot already returned its buy
box. The one genuine gap this task closed:
_UNAVAILABLE_PAGE_MARKERS/#outOfStockBuyBox_feature_div in amazon.py,
which made the B0GX54WT36 aod fetch raise instead of returning [] before
this file was written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from book_alerter.db.models import Product
from book_alerter.enums import Condition
from book_alerter.sources.amazon import (
    AmazonUKProductInlineSource,
    parse_dp,
    parse_offer_listing,
)
from book_alerter.sources.base import SourceError
from tests.integration.sources.test_amazon import (
    _install_fake_render_page,  # type: ignore[attr-defined]
    _prepared,  # type: ignore[attr-defined]
)

PRODUCT_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "amazon" / "products"


def _load(name: str) -> str:
    return (PRODUCT_FIXTURES / name).read_text(encoding="utf-8")


def _product(*, asin: str = "B0F3NVWM37", track_used: bool = False) -> Product:
    return Product(id=1, asin=asin, title="t", track_used=track_used)


# --- B0F3NVWM37: multi-seller, non-Amazon-brand, real used offers ----------


def test_b0f3nvwm37_dp_returns_the_buybox() -> None:
    html = _load("B0F3NVWM37-uk-dp-2026-09-04.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/B0F3NVWM37")
    assert len(offers) == 1
    o = offers[0]
    assert o.price_minor == 4999
    assert o.condition == Condition.NEW
    # T2.7: no #merchant-info on this real capture -> unattributed, not "Amazon".
    assert o.seller is None
    # Unconditional ("Order within 4 hrs..." carries no first-order marker).
    assert o.shipping_minor == 0


def test_b0f3nvwm37_aod_returns_all_ten_rows_with_expected_sellers_and_conditions() -> None:
    html = _load("B0F3NVWM37-uk-aod-2026-09-04.html")
    offers = parse_offer_listing(
        html, fallback_url="https://x.example/", source_name="amazon_uk_product"
    )
    assert len(offers) == 10
    sellers = {o.seller for o in offers}
    assert sellers == {
        "CashC",
        "The Games Exchange Ltd (GEX)",
        "Yard's Games",
        "Retro Games Europe",
        "Fuzion",
        "Hitcouk",
        "Tekzone UK",
        "TheGamery",
        "RAREWAVES",
    }, "expected the 9 distinct sellers T0.4 found on this page"
    conditions = {o.condition for o in offers}
    assert conditions == {Condition.NEW, Condition.USED_VG, Condition.USED_G}, (
        "genuine used_vg/used_g rows must survive parsing, not collapse to new/unknown"
    )


def test_b0f3nvwm37_aod_first_order_promo_rows_report_unknown_shipping() -> None:
    """T2.5 bonus regression (per the task brief) on a second real page,
    independent of the one T2.5's own acceptance test already used —
    same evidence, different fixture, same rule."""
    html = _load("B0F3NVWM37-uk-aod-2026-09-04.html")
    offers = parse_offer_listing(
        html, fallback_url="https://x.example/", source_name="amazon_uk_product"
    )
    by_seller = {o.seller: o for o in offers}

    conditional = [
        o for o in offers
        if o.delivery_text and "on your first order" in o.delivery_text.lower()
    ]
    assert len(conditional) == 8
    assert all(o.shipping_minor is None for o in conditional)

    # The two controls, by name, on the same page: a genuinely unconditional
    # free offer stays 0, and a real paid charge stays untouched.
    assert by_seller["Tekzone UK"].shipping_minor == 0
    assert by_seller["Retro Games Europe"].shipping_minor == 299


async def test_b0f3nvwm37_track_used_true_includes_used_offers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_render_page(
        monkeypatch,
        {
            "/dp/": _load("B0F3NVWM37-uk-dp-2026-09-04.html"),
            "/gp/offer-listing/": _load("B0F3NVWM37-uk-aod-2026-09-04.html"),
        },
    )
    src = _prepared(AmazonUKProductInlineSource(region="UK"))
    offers = await src.fetch(_product(asin="B0F3NVWM37", track_used=True))

    conditions = {o.condition for o in offers}
    assert Condition.USED_VG in conditions
    assert Condition.USED_G in conditions
    # dp (1) + aod (10), no dedup collision (dp's seller is None, no aod row shares that key).
    assert len(offers) == 11


async def test_b0f3nvwm37_track_used_false_excludes_used_offers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_render_page(
        monkeypatch,
        {
            "/dp/": _load("B0F3NVWM37-uk-dp-2026-09-04.html"),
            "/gp/offer-listing/": _load("B0F3NVWM37-uk-aod-2026-09-04.html"),
        },
    )
    src = _prepared(AmazonUKProductInlineSource(region="UK"))
    offers = await src.fetch(_product(asin="B0F3NVWM37", track_used=False))

    assert all(o.condition == Condition.NEW for o in offers)
    assert len(offers) == 9, "2 of the 11 merged rows are used_vg/used_g and must be dropped"


# --- B0CYT8WL1G: the variant page (adidas) ----------------------------------


def test_b0cyt8wl1g_dp_returns_the_selected_variants_buybox() -> None:
    """Variant pages: parse_dp reads whichever buy box Amazon actually
    rendered — the SELECTED variant's price — without any special
    variant-aware logic needed; Amazon already resolves which variant to
    show server-side. This fixture's own canonical disagrees with the
    requested ASIN (see the aod test below), but the buy box itself is
    still the real, currently-displayed price and is safe to read."""
    html = _load("B0CYT8WL1G-uk-dp-2026-09-04.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/B0CYT8WL1G")
    assert len(offers) == 1
    o = offers[0]
    assert o.price_minor == 2811
    assert o.condition == Condition.NEW
    assert o.seller is None  # no #merchant-info on this capture either

    # Not a T2.5 case: the delivery text IS conditional ("on orders
    # dispatched by Amazon over £35"), a genuinely different conditional
    # phrase from "on your first order" that T2.5's marker does not match
    # — flagged in the T4.2 report as a new finding, not silently patched
    # here (T2.5 is a separately-reviewed, closed task).
    assert o.delivery_text is not None
    assert "on orders dispatched by amazon over" in o.delivery_text.lower()
    assert o.shipping_minor == 0, (
        "documents CURRENT behaviour (not converted to None) — this exact "
        "conditional phrasing is a known gap in T2.5's marker set, not "
        "something this task silently fixed"
    )


def test_b0cyt8wl1g_aod_raises_via_both_the_renderer_and_the_bare_parser() -> None:
    """The aod side is NOT a T4.2 gap to fix — it's two independent layers
    of defence both correctly refusing to report this page as "empty",
    verified rather than assumed (an earlier draft of this test asserted
    the opposite outcome and was wrong — caught by actually running it).

    This fixture's own <link rel="canonical"> points at B0DLSB1WWK, a
    different ASIN than the one requested (B0CYT8WL1G) — the exact F26
    finding, and exactly why
    test_render_amazon_page_raises_and_writes_debug_capture_on_canonical_
    mismatch in test_amazon.py uses this same fixture as ITS proof: that
    check, in _render_amazon_page, is what catches it in production.

    But even the BARE parser, called directly here bypassing the renderer
    entirely (so F26's canonical check never runs), still raises — via the
    pre-existing, unmodified _OFFER_LISTING_PAGE_MARKERS check: this page
    has no #aod-container et al. The new T4.2 _UNAVAILABLE_PAGE_MARKERS
    fix deliberately does NOT cover it either —
    #outOfStockBuyBox_feature_div is absent, because this isn't an
    unavailable-product page, it's a wrong-variant redirect, a different
    failure mode that must stay an error rather than being silently
    reinterpreted as "genuinely 0 offers".
    """
    html = _load("B0CYT8WL1G-uk-aod-2026-09-04.html")
    with pytest.raises(SourceError, match="did not match any known Amazon UK layout"):
        parse_offer_listing(
            html, fallback_url="https://x.example/", source_name="amazon_uk_product"
        )


# --- B0GX54WT36: currently unavailable --------------------------------------


def test_b0gx54wt36_dp_returns_empty_without_raising() -> None:
    """Already correct before this task — pinned, not changed. #dp-container
    and #productTitle are present (a real, recognised Amazon page) but no
    price block is — parse_dp's existing "recognised page, no price" path
    already returns [] rather than treating it as an anti-bot variant."""
    html = _load("B0GX54WT36-uk-dp-2026-09-04.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/B0GX54WT36")
    assert offers == []


def test_b0gx54wt36_aod_returns_empty_without_raising() -> None:
    """This one DID need the fix: before _UNAVAILABLE_PAGE_MARKERS, this
    fetch raised SourceError (no #aod-container, and the old check had no
    other way to recognise "genuinely nothing to list"). Its own canonical
    matches its own requested ASIN — a genuine "unavailable", not a
    wrong-page response — so returning [] here is correct, not a case
    F26 needs to catch."""
    html = _load("B0GX54WT36-uk-aod-2026-09-04.html")
    offers = parse_offer_listing(
        html, fallback_url="https://x.example/", source_name="amazon_uk_product"
    )
    assert offers == []


# --- B09B96TG33 (Echo Dot): the genuine single-seller empty listing --------


def test_b09b96tg33_aod_returns_empty_without_raising() -> None:
    """Already correct before this task — pinned, not changed. 0
    #aod-offer rows, 1 #aod-pinned-offer (Amazon's own pinned offer, not a
    third-party row) — a real single-seller page, not an anti-bot variant.
    The dp side of this same ASIN is already covered by the T2.7
    regression test in test_amazon_parser.py (seller=None, no
    #merchant-info)."""
    html = _load("B09B96TG33-uk-aod-2026-09-04.html")
    offers = parse_offer_listing(
        html, fallback_url="https://x.example/", source_name="amazon_uk_product"
    )
    assert offers == []
