"""One-shot: fetch a real Amazon UK dp HTML for an ISBN, save to disk.

Usage:  uv run python scripts/capture_amazon_dp.py 9781800816015 /tmp/sparta-dp.html

The point is to inspect REAL delivery markup before writing shipping-parser
code, instead of guessing.
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
            await page.goto(src.dp_url(isbn13), wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_selector(
                    "#corePriceDisplay_desktop_feature_div, "
                    "#corePrice_feature_div, .a-price .a-offscreen",
                    timeout=20_000,
                    state="attached",
                )
            except Exception:
                pass
            # Wait for delivery block to actually contain text — Amazon hydrates
            # it asynchronously after the buy-box price renders.
            try:
                await page.wait_for_function(
                    """() => {
                        const sels = [
                            '#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE',
                            '#deliveryBlockMessage',
                            '#dynamicDeliveryMessage',
                        ];
                        for (const s of sels) {
                            const el = document.querySelector(s);
                            if (el && el.innerText && el.innerText.trim().length > 0) return true;
                        }
                        return false;
                    }""",
                    timeout=20_000,
                )
            except Exception:
                pass
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
