from __future__ import annotations

from book_alerter.db.models import Condition
from book_alerter.logging_setup import get_logger

log = get_logger(__name__)

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

# T2.6: the standard antiquarian-bookseller grades, conventional descending
# order As New / Fine > Near Fine > Very Good > Good > Fair > Poor. "Fair"
# was already mapped below. These specific strings are NOT evidenced by a
# live capture — T0.5 (docs/superpowers/plans/2026-09-04-wave0-probe-results.md)
# captured two real Bookfinder pages and found only "New" and "Used - Like
# New", both already handled; the production `unknown` rows' grade text was
# never persisted (only seller/condition/price/shipping/currency/url), so
# there is no way to recover what they actually said. This is a best-effort
# default matching the plan's own list, not a measured mapping. A single
# "fine" entry covers "Near Fine" too (substring match), so no separate
# "near fine" entry is needed. "Ex-library" is deliberately NOT in this
# table: it is a provenance marker, not a grade, so a string like
# "Ex-Library - Very Good" must resolve on "very good" (already covered by
# a plain substring scan) rather than being force-mapped to a condition of
# its own; "Ex-Library" alone (no grade) correctly falls through to
# 'unknown' below, same as it did before this change.
#
# F-B8 (secondary, robustness): "fine" is ranked AFTER "very good"/"good",
# not before them as an earlier version had it. "fine" and "very good" map
# to the same target (`used_vg`), so their relative order never changes an
# outcome, but "fine" and "good" map to DIFFERENT targets — if a caller's
# grade text ever carries a stray "fine" substring alongside an actual
# "Good" grade (e.g. bookfinder.py's `_CONDITION_RE` capturing a little too
# much before its own bound was tightened, see that file), checking "good"
# first means the real grade wins rather than the incidental "fine". "very
# good" must stay before "good" regardless (it already did) since "good" is
# itself a substring of "very good". No currently-mapped real grade text
# contains "fine" as a substring of "very good"/"good" alone, so this
# reordering does not change any of those three mappings.
_GRADE_HAYSTACK: list[tuple[str, Condition]] = [
    ("like new", "used_vg"),
    ("as new", "used_vg"),
    ("very good", "used_vg"),
    ("good", "used_g"),
    ("fine", "used_vg"),
    ("acceptable", "used_acceptable"),
    ("fair", "used_acceptable"),
    ("poor", "used_acceptable"),
]


def condition_from_token(token: str) -> Condition:
    """Map an exact uppercase token (WoB-style) to a Condition; 'unknown' on miss."""
    return _TOKEN_MAP.get(token.strip().upper(), "unknown")


def condition_from_grade_text(grade_text: str, *, source: str = "unspecified") -> Condition:
    """Map free-form grade text (bookfinder/amazon-style) to a Condition.

    Lowercases + substring-matches against `_GRADE_HAYSTACK` in priority
    order ('like new' wins over 'new'). Returns 'new' when the text reads
    as new without any used qualifier. Falls back to 'unknown'.

    `source` is optional and defaults to `'unspecified'` so every existing
    call site keeps working unchanged; pass the caller's own name (e.g.
    `"bookfinder"`, `"amazon"`) for a diagnosable log line. A non-empty
    grade that reaches neither the haystack nor the bare-new fallback logs
    a warning with the raw text — see the T2.6 note above: the production
    `unknown` rows' grade text was thrown away and unrecoverable, so the
    next occurrence of an unmapped grade should show up in the logs instead
    of silently vanishing again. An empty `grade_text` is not logged: it
    means no grade text was found at all, which is an expected shape (e.g.
    a plain "New" listing with no separate qualifier), not an unrecognised
    grade.
    """
    text = grade_text.strip().lower()
    if not text:
        return "unknown"
    for needle, mapped in _GRADE_HAYSTACK:
        if needle in text:
            return mapped
    if "new" in text and "used" not in text:
        return "new"
    log.warning("condition_normalizers.grade_unmapped", grade_text=grade_text, source=source)
    return "unknown"
