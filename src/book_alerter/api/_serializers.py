"""Pydantic helpers for API response serialization.

Used by every *Out model that emits a datetime to the FE.

Every wire datetime is in UTC by project convention — the backend uses
`datetime.now(UTC)` everywhere and SQLite columns are declared as plain
`datetime` (without tzinfo). When the FE later does `new Date(iso)`,
JavaScript reads a naive ISO string as *local* time, shifting every
"X ago" / formatted timestamp by the user's UTC offset.

`UtcDateTime` solves this by always appending the `Z` suffix during
serialization, so the FE parses every datetime as UTC regardless of
whether the in-memory value was tzinfo-aware. See `docs/CHANGELOG.md`
2026-05-23 entry for the bug story.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def to_z_iso(dt: datetime) -> str:
    """Render a datetime as a Z-suffixed ISO 8601 string in UTC.

    - tzinfo=None → assume UTC and append "Z"
    - tzinfo=UTC → emit ISO with "+00:00" replaced by "Z"
    - tzinfo=other → convert to UTC first
    """
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


UtcDateTime = Annotated[
    datetime,
    PlainSerializer(to_z_iso, return_type=str, when_used="json"),
]
"""A `datetime` that always serializes to a Z-suffixed UTC ISO string.

Use in place of `datetime` on every Pydantic `*Out` model field that
flows to the FE. Reads stay typed as `datetime`; the difference is the
JSON-serialization path.
"""
