import pytest

from book_alerter.sources.normalizers import to_isbn13

SPEC_FIXTURES = [
    ("0241638194", "9780241638194"),
    ("100904852X", "9781009048521"),
    ("9789693531374", "9789693531374"),
    ("024147941X", "9780241479414"),
    ("0753560682", "9780753560686"),
]


@pytest.mark.parametrize("raw, expected", SPEC_FIXTURES)
def test_spec_fixture_normalizes_to_isbn13(raw: str, expected: str) -> None:
    assert to_isbn13(raw) == expected


def test_dashes_tolerated_isbn13() -> None:
    assert to_isbn13("978-0-241-63819-4") == "9780241638194"


def test_dashes_tolerated_isbn10() -> None:
    assert to_isbn13("0-241-63819-4") == "9780241638194"


def test_spaces_tolerated() -> None:
    assert to_isbn13("978 0 241 63819 4") == "9780241638194"


def test_trailing_x_isbn10_normalizes() -> None:
    # ISBN-10 check digit can be 'X' (value 10); canonical handling required.
    assert to_isbn13("100904852X") == "9781009048521"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-an-isbn",
        "9780241638195",  # valid form, wrong checksum
        "123",
    ],
)
def test_invalid_input_raises_value_error(bad: str) -> None:
    with pytest.raises(ValueError):
        to_isbn13(bad)


def test_idempotent() -> None:
    once = to_isbn13("0241638194")
    twice = to_isbn13(once)
    assert once == twice == "9780241638194"
