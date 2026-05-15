"""Fetch a real bookfinder page for an ISBN, save to disk.

Usage: uv run python scripts/capture_bookfinder.py <isbn13> <out_path>

Used to validate the parser against current live markup — particularly
for marketplaces (AbeBooks, Biblio) where listings often carry paid
postage that the parser must extract via the data-csa-c-* attrs.
"""
from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

from book_alerter.sources.bookfinder import BookfinderInlineSource


async def main(isbn13: str, out_path: str) -> None:
    src = BookfinderInlineSource(region="UK", timeout_s=45.0)
    html = await src._render(async_playwright, src.search_url(isbn13))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"saved {len(html)} bytes -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
