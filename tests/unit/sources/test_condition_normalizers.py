"""Coverage matrix for `condition_from_grade_text` / `condition_from_token`.

Both sit between every source's raw condition text and the `Condition` enum
persisted to the DB — a grade a source captures but this module fails to
recognise resolves silently to `unknown`. T0.5 (docs/superpowers/plans/
2026-09-04-wave0-probe-results.md) found 2,738 production Bookfinder rows
stuck at `unknown` with the original raw grade text unrecoverable (never
persisted). T2.6 adds a best-effort mapping for the conventional
antiquarian grades plus a diagnostic log line so the *next* occurrence is
recoverable from the logs instead of vanishing the same way.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from book_alerter.sources.condition_normalizers import (
    condition_from_grade_text,
    condition_from_token,
)


@pytest.mark.parametrize(
    ("grade_text", "expected"),
    [
        # Pre-existing entries -- regression guard, not new behaviour.
        ("Like New", "used_vg"),
        ("Very Good", "used_vg"),
        ("Good", "used_g"),
        ("Acceptable", "used_acceptable"),
        ("Fair", "used_acceptable"),
        ("Poor", "used_acceptable"),
        # T2.6: newly mapped antiquarian grades (best-effort, see module
        # comment above _GRADE_HAYSTACK -- not evidenced by a live capture).
        ("Fine", "used_vg"),
        ("Near Fine", "used_vg"),  # covered by the "fine" substring, no separate entry
        ("As New", "used_vg"),
        # Case-insensitivity and surrounding whitespace.
        ("  fine  ", "used_vg"),
        ("NEAR FINE", "used_vg"),
        # "Ex-library" is a provenance marker, not a grade: paired with a
        # real grade it must resolve on the grade, not on "ex-library"
        # itself; alone (no grade) it must fall through to unknown exactly
        # as it did before this change -- it is not in _GRADE_HAYSTACK.
        ("Ex-Library - Very Good", "used_vg"),
        ("Ex-Library, Fine", "used_vg"),
        ("Ex-Library", "unknown"),
        # Bare "new" reads as new; "new" alongside "used" must not.
        ("New", "new"),
        ("Brand New", "new"),
        ("Used - New", "unknown"),
        # No grade text at all -- absence of input, not an unrecognised grade.
        ("", "unknown"),
        ("   ", "unknown"),
        # A genuinely unrecognised grade string.
        ("Reading Copy", "unknown"),
    ],
)
def test_condition_from_grade_text(grade_text: str, expected: str) -> None:
    assert condition_from_grade_text(grade_text) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("NEW", "new"),
        ("LIKE_NEW", "used_vg"),
        ("VERY_GOOD", "used_vg"),
        ("GOOD", "used_g"),
        ("WELL_READ", "used_acceptable"),
        ("ACCEPTABLE", "used_acceptable"),
        ("  new  ", "new"),  # strip + upper
        ("new", "new"),
        ("SOMETHING_ELSE", "unknown"),
        ("", "unknown"),
    ],
)
def test_condition_from_token(token: str, expected: str) -> None:
    assert condition_from_token(token) == expected


def test_unmapped_grade_resolves_unknown_and_logs_diagnostic() -> None:
    """The half of T2.6 that has to keep working: a grade string this module
    doesn't recognise must still resolve to Condition.UNKNOWN (never raise,
    never silently mis-classify) AND must be diagnosable afterwards -- the
    raw text goes into a structured warning log instead of being thrown
    away, which is exactly what made the production `unknown` rows
    unexplainable in the first place."""
    with capture_logs() as logs:
        result = condition_from_grade_text("Reading Copy", source="bookfinder")

    assert result == "unknown"
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1, logs
    assert warnings[0]["event"] == "condition_normalizers.grade_unmapped"
    assert warnings[0]["grade_text"] == "Reading Copy"
    assert warnings[0]["source"] == "bookfinder"


def test_unmapped_grade_defaults_source_when_caller_does_not_pass_one() -> None:
    """Existing call sites (amazon.py, bookfinder.py) don't pass `source` yet
    -- the parameter must default rather than require every caller to be
    updated in lockstep."""
    with capture_logs() as logs:
        condition_from_grade_text("Reading Copy")

    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1, logs
    assert warnings[0]["source"] == "unspecified"


@pytest.mark.parametrize("grade_text", ["Fine", "Very Good", "New", "", "   "])
def test_recognised_or_empty_grade_does_not_log(grade_text: str) -> None:
    """Only a genuinely unrecognised, non-empty grade is diagnostically
    interesting -- a successful match or the absence of any grade text at
    all (e.g. a plain "New" listing with no separate qualifier) is expected
    and must not spam the logs."""
    with capture_logs() as logs:
        condition_from_grade_text(grade_text)

    assert logs == []
