"""Capture a real Amazon UK (dp/AOD) or Bookfinder page to disk as a test fixture.

Supersedes capture_amazon_dp.py, capture_amazon_offer_listing.py and
capture_bookfinder.py -- one script now owns every fixture capture. It
drives the same rendering path the app itself uses (the module-level
`_render_amazon_page` helper for Amazon, `BookfinderInlineSource._render`
for Bookfinder) so captures match what the live scraper sees, instead of
guessing at wait conditions independently.

Usage:
    uv run python scripts/capture_amazon_fixture.py --asin B09B96TG33 --kind both \
        --out tests/fixtures/amazon/products/
    uv run python scripts/capture_amazon_fixture.py --asin 9780241638194 --kind dp \
        --out tests/fixtures/amazon/
    uv run python scripts/capture_amazon_fixture.py --source bookfinder --id 9780747532699 \
        --out tests/fixtures/bookfinder/

Each capture writes `<id>-<region>-<kind>-<YYYY-MM-DD>[-pc<postcode>].html`
plus a sidecar `.json` recording, per AOD/offer-listing row, the
`data-csa-c-delivery-price` attribute values and `.aod-delivery-promise`
text found in it (empty on pages with no offer rows, e.g. dp / Bookfinder
search). This is capture tooling only -- see the module docstring above:
no parser changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright
from selectolax.parser import HTMLParser

from book_alerter.sources.amazon import AmazonUKInlineSource, _render_amazon_page
from book_alerter.sources.base import SourceError
from book_alerter.sources.bookfinder import BookfinderInlineSource

# Live-capture timeout. Matches the value the two superseded Amazon scripts
# used (45s) rather than the shorter 30s production-polling default on
# AmazonUKInlineSource/AmazonUKProductInlineSource -- a one-shot manual
# capture can afford to wait longer than a scheduled fleet run.
_TIMEOUT_S = 45.0

# Wait-selector strings copied verbatim from `_fetch_offers_for_asin` in
# src/book_alerter/sources/amazon.py so a captured fixture is rendered
# under the exact same wait condition the live scraper uses for each page.
_DP_WAIT_SELECTOR = (
    "#corePriceDisplay_desktop_feature_div, #corePrice_feature_div, .a-price .a-offscreen"
)
_AOD_WAIT_SELECTOR = "#aod-offer-list, .olpOfferList"


class CaptureSource(StrEnum):
    """Which live site to render. Determines URL construction and which
    `CaptureKind` values are valid (see `_parse_args`)."""

    AMAZON = "amazon"
    BOOKFINDER = "bookfinder"


class CaptureKind(StrEnum):
    """Which page to capture. DP/AOD/BOTH apply to CaptureSource.AMAZON;
    SEARCH is Bookfinder's single page type and is selected automatically
    for CaptureSource.BOOKFINDER."""

    DP = "dp"
    AOD = "aod"
    BOTH = "both"
    SEARCH = "search"


def _extract_delivery_rows(html: str) -> list[dict[str, Any]]:
    """Per-row delivery markers, for the sidecar JSON.

    Row selection mirrors `parse_offer_listing`'s in
    src/book_alerter/sources/amazon.py: modern `#aod-offer-list #aod-offer`
    rows, falling back to legacy `.olpOffer`. This is deliberately NOT the
    parser -- no price/condition/seller extraction, just the raw
    `data-csa-c-delivery-price` attribute values and `.aod-delivery-promise`
    text a later shipping-parser task needs to look at. Pages with no offer
    rows (dp, Bookfinder search) yield an empty list.
    """
    tree = HTMLParser(html)
    rows = tree.css("#aod-offer-list #aod-offer")
    if not rows:
        rows = tree.css(".olpOffer")
    result: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        delivery_prices = [
            node.attributes.get("data-csa-c-delivery-price")
            for node in row.css("[data-csa-c-delivery-price]")
        ]
        promise = row.css_first(".aod-delivery-promise")
        promise_text = (promise.text() or "").strip() if promise is not None else None
        result.append(
            {
                "row_index": i,
                "data_csa_c_delivery_price": delivery_prices,
                "aod_delivery_promise_text": promise_text,
            }
        )
    return result


def _save_capture(
    *,
    identifier: str,
    source: CaptureSource,
    kind: CaptureKind,
    html: str,
    url: str,
    out_dir: Path,
    postcode: str | None,
    captured_on: date,
    region_tag: str,
) -> None:
    stem = f"{identifier}-{region_tag}-{kind.value}-{captured_on.isoformat()}"
    if postcode:
        stem += f"-pc{postcode}"

    html_path = out_dir / f"{stem}.html"
    html_path.write_text(html, encoding="utf-8")

    delivery_rows = _extract_delivery_rows(html)
    sidecar = {
        "identifier": identifier,
        "source": source.value,
        "kind": kind.value,
        "url": url,
        "captured_at": datetime.now(UTC).isoformat(),
        "postcode": postcode,
        "delivery_rows": delivery_rows,
    }
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    print(f"saved {len(html)} bytes -> {html_path}")
    print(f"saved sidecar ({len(delivery_rows)} rows) -> {json_path}")


async def _capture_amazon(
    identifier: str,
    kind: CaptureKind,
    *,
    out_dir: Path,
    postcode: str | None,
    captured_on: date,
) -> None:
    src = AmazonUKInlineSource(region="UK", timeout_s=_TIMEOUT_S)
    page_kinds = (CaptureKind.DP, CaptureKind.AOD) if kind is CaptureKind.BOTH else (kind,)

    if postcode:
        # T0.2 settled this on 2026-09-04: a delivery postcode could NOT be
        # pinned for a logged-out headless session (the glow endpoint needs a
        # CSRF token that is not served, and the glow widget was absent from
        # the page), and pinning made no difference to the delivery promises
        # anyway -- the promise varies with whether Amazon thinks you are a
        # first-time buyer, not with location. Plan task T1.2 was dropped as a
        # result. --postcode is kept only as a filename tag for captures taken
        # under some externally-pinned location; it does not pin anything.
        print(
            f"WARNING: --postcode {postcode!r} only affects the output filename; "
            "live delivery-location pinning does not work for a logged-out "
            "session (settled by plan task T0.2; T1.2 dropped). This capture "
            "reflects Amazon's default, unpinned delivery location.",
            file=sys.stderr,
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768}, locale="en-GB"
            )
            for page_kind in page_kinds:
                if page_kind is CaptureKind.DP:
                    url = src.dp_url(identifier)
                    wait_selector = _DP_WAIT_SELECTOR
                    wait_ms = min(10_000, int(_TIMEOUT_S * 1000))
                else:
                    url = src.offer_listing_url(identifier)
                    wait_selector = _AOD_WAIT_SELECTOR
                    wait_ms = int(_TIMEOUT_S * 1000)
                html = await _render_amazon_page(
                    context,
                    url,
                    wait_selector=wait_selector,
                    wait_ms=wait_ms,
                    navigation_timeout_s=_TIMEOUT_S,
                    source_name="capture_amazon_fixture",
                )
                _save_capture(
                    identifier=identifier,
                    source=CaptureSource.AMAZON,
                    kind=page_kind,
                    html=html,
                    url=url,
                    out_dir=out_dir,
                    postcode=postcode,
                    captured_on=captured_on,
                    region_tag="uk",
                )
        finally:
            await browser.close()


async def _capture_bookfinder(identifier: str, *, out_dir: Path, captured_on: date) -> None:
    src = BookfinderInlineSource(region="UK", timeout_s=_TIMEOUT_S)
    url = src.search_url(identifier)
    html = await src._render(async_playwright, url)
    _save_capture(
        identifier=identifier,
        source=CaptureSource.BOOKFINDER,
        kind=CaptureKind.SEARCH,
        html=html,
        url=url,
        out_dir=out_dir,
        postcode=None,
        captured_on=captured_on,
        region_tag=src._destination.lower(),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a real Amazon UK or Bookfinder page to disk as a test fixture.",
    )
    parser.add_argument(
        "--source",
        type=CaptureSource,
        choices=list(CaptureSource),
        default=CaptureSource.AMAZON,
        help="Site to capture from (default: amazon).",
    )
    parser.add_argument(
        "--asin",
        "--id",
        dest="identifier",
        required=True,
        help="Amazon ASIN or book ISBN-13 for --source amazon; ISBN-13 for "
        "--source bookfinder. (--id is an alias for --asin.)",
    )
    parser.add_argument(
        "--kind",
        type=CaptureKind,
        choices=list(CaptureKind),
        default=None,
        help="Page to capture: dp|aod|both (amazon only; default both). "
        "Bookfinder always captures its single search page.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tests/fixtures/amazon/products/"),
        help="Output directory (default: tests/fixtures/amazon/products/).",
    )
    parser.add_argument(
        "--postcode",
        default=None,
        help="Optional delivery postcode; tags the output filename only -- "
        "see the --source amazon warning for why.",
    )
    args = parser.parse_args(argv)

    if args.source is CaptureSource.BOOKFINDER:
        if args.kind not in (None, CaptureKind.SEARCH):
            parser.error(
                "--kind is not applicable to --source bookfinder "
                "(it always captures the single search page)"
            )
        args.kind = CaptureKind.SEARCH
    else:
        if args.kind is CaptureKind.SEARCH:
            parser.error("--kind search is only valid with --source bookfinder")
        if args.kind is None:
            args.kind = CaptureKind.BOTH
    return args


async def _async_main(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    captured_on = datetime.now(UTC).date()
    if args.source is CaptureSource.AMAZON:
        await _capture_amazon(
            args.identifier,
            args.kind,
            out_dir=args.out,
            postcode=args.postcode,
            captured_on=captured_on,
        )
    else:
        await _capture_bookfinder(args.identifier, out_dir=args.out, captured_on=captured_on)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        asyncio.run(_async_main(args))
    except SourceError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
