import re
from urllib.parse import urlparse

import isbnlib

# Amazon ASIN format: exactly 10 chars, uppercase alphanumeric, B0-prefixed for
# non-book products and the ISBN-10 itself for books. We accept anything that
# matches the 10-char alnum shape after normalization; Amazon does the actual
# validity check at fetch time.
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

# Path segments where an ASIN can appear in any Amazon URL.
# `/dp/<asin>`             — canonical product page.
# `/gp/product/<asin>`     — older format, still in the wild.
# `/gp/aw/d/<asin>`        — mobile redirect.
# `/exec/obidos/asin/<asin>` — legacy affiliate links, occasionally hit.
_ASIN_PATH_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|exec/obidos/asin|product|d)/([A-Z0-9]{10})(?:[/?]|$)",
    re.IGNORECASE,
)


def to_isbn13(raw: str) -> str:
    s = isbnlib.canonical(raw)
    if not s:
        raise ValueError(f"invalid ISBN: {raw!r}")
    if isbnlib.is_isbn10(s):
        s = isbnlib.to_isbn13(s)
    if not isbnlib.is_isbn13(s):
        raise ValueError(f"could not normalize to ISBN-13: {raw!r}")
    return s


def to_asin(raw: str) -> str:
    """Extract a 10-char Amazon ASIN from either a bare ASIN or a URL.

    Accepts:
    - "B07XYZ1234" (bare ASIN, any case)
    - "https://www.amazon.co.uk/dp/B07XYZ1234"
    - "https://www.amazon.com/dp/B07XYZ1234?ref=..."
    - "https://www.amazon.de/gp/product/B07XYZ1234/"
    - "amazon.co.uk/dp/B07XYZ1234"
    - "/dp/B07XYZ1234"
    - "https://amzn.eu/d/abcXYZ12" — short-link form (10 chars after /d/)

    Returns the uppercase ASIN. Raises ValueError on garbage.

    Does NOT verify that Amazon actually has a product for this ASIN —
    that's the Source's job at fetch time. Format validation only.
    """
    candidate = (raw or "").strip().upper()
    # Bare ASIN — bypass URL parse so we accept the no-scheme case cleanly.
    if _ASIN_RE.fullmatch(candidate):
        return candidate

    # Try URL/path parsing. Tolerate missing scheme by prefixing `//` if the
    # raw looks like host-prefixed but no scheme.
    parse_target = raw.strip()
    if "://" not in parse_target and not parse_target.startswith("/"):
        parse_target = "//" + parse_target
    parsed = urlparse(parse_target)
    if parsed.path:
        m = _ASIN_PATH_RE.search(parsed.path)
        if m:
            return m.group(1).upper()
    raise ValueError(f"could not extract ASIN from {raw!r}")


def asin_for_amazon_uk(isbn13: str) -> str:
    """Return Amazon UK's ASIN path segment for an ISBN-13.

    Amazon UK and Keepa both index books by ISBN-10 (which serves as the
    book ASIN). 978-prefixed ISBN-13s map back to a unique ISBN-10; the
    newer 979 prefix has no ISBN-10 form and we fall back to the ISBN-13.
    Shared between the Amazon scraper (sources/amazon.py) and the Keepa
    PNG fetcher (keepa.py).
    """
    isbn10 = isbnlib.to_isbn10(isbn13)
    return isbn10 if isbn10 else isbn13


def amazon_uk_dp_url(isbn13: str) -> str:
    """Amazon UK dp URL for an ISBN-13 (book). For products, prefer
    `amazon_uk_product_dp_url(asin)` to avoid the ISBN conversion step."""
    return f"https://www.amazon.co.uk/dp/{asin_for_amazon_uk(isbn13)}"


def amazon_uk_product_dp_url(asin: str) -> str:
    """Amazon UK dp URL for a product ASIN (already in ASIN form)."""
    return f"https://www.amazon.co.uk/dp/{asin}"
