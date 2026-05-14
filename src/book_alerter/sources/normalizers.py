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
