"""One-shot: fetch a real Amazon UK offer-listing HTML for an ISBN, save to disk.

Usage:  uv run python scripts/capture_amazon_offer_listing.py 9780241638194 /tmp/gemini-ol.html

The point is to inspect REAL #aod-offer row markup (price + heading + soldBy)
before adjusting the parser selectors. The hand-crafted synthetic fixture
does not capture every quirk of the live page.
"""
from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

from book_alerter.sources.amazon import AmazonUKInlineSource


async def main(isbn13: str, out_path: str) -> None:
    src = AmazonUKInlineSource(region="UK", timeout_s=45.0)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = await browser.new_context(
                viewport={"width": 1366, "height": 768}, locale="en-GB"
            )
            page = await ctx.new_page()
            await page.goto(
                src.offer_listing_url(isbn13),
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            try:
                await page.wait_for_selector(
                    "#aod-offer-list, .olpOfferList",
                    timeout=30_000,
                    state="attached",
                )
            except Exception:
                pass
            # Give Amazon a beat to hydrate row internals (price, heading,
            # soldBy spans) before we snapshot.
            await page.wait_for_timeout(2000)
            html = await page.content()
        finally:
            await browser.close()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"saved {len(html)} bytes -> {out_path}")


if __name__ == "__main__":
    isbn = sys.argv[1]
    out = sys.argv[2]
    asyncio.run(main(isbn, out))
