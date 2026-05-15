import isbnlib


def to_isbn13(raw: str) -> str:
    s = isbnlib.canonical(raw)
    if not s:
        raise ValueError(f"invalid ISBN: {raw!r}")
    if isbnlib.is_isbn10(s):
        s = isbnlib.to_isbn13(s)
    if not isbnlib.is_isbn13(s):
        raise ValueError(f"could not normalize to ISBN-13: {raw!r}")
    return s


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
