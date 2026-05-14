from __future__ import annotations

from book_alerter.db.models import Condition

# Sources expose their condition data in two shapes:
#
#   (a) Exact token from a structured field (WoB's `public_title` Shopify
#       variant string yields uppercase tokens like `LIKE_NEW`, `VERY_GOOD`).
#       Look up directly in `_TOKEN_MAP`.
#
#   (b) Free-form grade text from card markup ("Used - Very Good",
#       "Like New" — bookfinder + amazon offer rows). Substring-match in
#       priority order against `_GRADE_HAYSTACK`.
#
# Keep both tables here so a new source can pick the matching helper and
# adding a grade only happens once.

_TOKEN_MAP: dict[str, Condition] = {
    "NEW": "new",
    "LIKE_NEW": "used_vg",
    "VERY_GOOD": "used_vg",
    "GOOD": "used_g",
    "WELL_READ": "used_acceptable",
    "ACCEPTABLE": "used_acceptable",
}

_GRADE_HAYSTACK: list[tuple[str, Condition]] = [
    ("like new", "used_vg"),
    ("very good", "used_vg"),
    ("good", "used_g"),
    ("acceptable", "used_acceptable"),
    ("fair", "used_acceptable"),
    ("poor", "used_acceptable"),
]


def condition_from_token(token: str) -> Condition:
    """Map an exact uppercase token (WoB-style) to a Condition; 'unknown' on miss."""
    return _TOKEN_MAP.get(token.strip().upper(), "unknown")


def condition_from_grade_text(grade_text: str) -> Condition:
    """Map free-form grade text (bookfinder/amazon-style) to a Condition.

    Lowercases + substring-matches against `_GRADE_HAYSTACK` in priority
    order ('like new' wins over 'new'). Returns 'new' when the text reads
    as new without any used qualifier. Falls back to 'unknown'.
    """
    text = grade_text.strip().lower()
    if not text:
        return "unknown"
    for needle, mapped in _GRADE_HAYSTACK:
        if needle in text:
            return mapped
    if "new" in text and "used" not in text:
        return "new"
    return "unknown"
