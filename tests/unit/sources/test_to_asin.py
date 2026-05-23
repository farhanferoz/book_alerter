"""Coverage matrix for `to_asin`.

ASIN inputs come from the FE Add-Product form: users paste either bare ASINs
or any of the many shapes of Amazon URL. Garbage in must raise; valid input
in any shape must return the same 10-char uppercase ASIN.
"""

from __future__ import annotations

import pytest

from book_alerter.sources.normalizers import to_asin


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Bare ASIN, various cases.
        ("B07XYZ1234", "B07XYZ1234"),
        ("b07xyz1234", "B07XYZ1234"),
        ("  B07XYZ1234  ", "B07XYZ1234"),
        # ISBN-10 form (books are ASIN==ISBN-10).
        ("0241638194", "0241638194"),
        # dp URLs across TLDs and schemes.
        ("https://www.amazon.co.uk/dp/B07XYZ1234", "B07XYZ1234"),
        ("https://www.amazon.com/dp/B07XYZ1234", "B07XYZ1234"),
        ("https://www.amazon.de/dp/B07XYZ1234", "B07XYZ1234"),
        ("http://www.amazon.co.uk/dp/B07XYZ1234", "B07XYZ1234"),
        ("www.amazon.co.uk/dp/B07XYZ1234", "B07XYZ1234"),
        ("amazon.co.uk/dp/B07XYZ1234", "B07XYZ1234"),
        ("/dp/B07XYZ1234", "B07XYZ1234"),
        # Query strings ignored.
        ("https://www.amazon.co.uk/dp/B07XYZ1234?ref=foo&tag=bar", "B07XYZ1234"),
        # Trailing slash and path segments after.
        ("https://www.amazon.co.uk/dp/B07XYZ1234/", "B07XYZ1234"),
        ("https://www.amazon.co.uk/dp/B07XYZ1234/ref=foo", "B07XYZ1234"),
        # Legacy and alternative path shapes.
        ("https://www.amazon.co.uk/gp/product/B07XYZ1234", "B07XYZ1234"),
        ("https://www.amazon.co.uk/gp/product/B07XYZ1234/?th=1", "B07XYZ1234"),
        ("https://www.amazon.co.uk/gp/aw/d/B07XYZ1234", "B07XYZ1234"),
        ("https://www.amazon.co.uk/exec/obidos/asin/B07XYZ1234", "B07XYZ1234"),
        # Long descriptive prefix that included an ASIN later in the path.
        (
            "https://www.amazon.co.uk/Product-Description-Stuff/dp/B07XYZ1234/ref=...",
            "B07XYZ1234",
        ),
    ],
)
def test_extracts_asin_from_supported_inputs(raw: str, expected: str) -> None:
    assert to_asin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not-an-asin",
        "B07XYZ123",  # 9 chars — too short.
        "B07XYZ12345",  # 11 chars — too long.
        "https://www.amazon.co.uk/",
        "https://www.amazon.co.uk/dp/",
        "https://www.amazon.co.uk/dp/SHORT",
        "https://www.example.com/some/other/path",
        "https://www.amazon.co.uk/some/path/without/asin",
        # ASIN-shaped junk in the wrong place — query string isn't a valid
        # source of the ASIN (we only look at path segments).
        "https://www.amazon.co.uk/?asin=B07XYZ1234",
    ],
)
def test_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError, match="could not extract ASIN"):
        to_asin(raw)


def test_returns_uppercase_when_url_path_lowercase() -> None:
    """ASINs are case-insensitive on Amazon, and we normalize to uppercase
    so duplicate detection works."""
    assert to_asin("https://www.amazon.co.uk/dp/b07xyz1234") == "B07XYZ1234"
