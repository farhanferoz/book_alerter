"""Regression tests for `book_alerter.api._serializers.UtcDateTime`.

Without the Z suffix, the FE's `new Date(iso)` interprets naive ISO as
local time, shifting every displayed timestamp by the user's UTC
offset. These tests pin the contract that every wire datetime carries
the Z suffix, regardless of whether the in-memory value is tz-aware.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from pydantic import BaseModel

from book_alerter.api._serializers import UtcDateTime, to_z_iso


def test_to_z_iso_naive_assumed_utc() -> None:
    dt = datetime(2026, 5, 23, 14, 30, 0, 123456)
    assert to_z_iso(dt) == "2026-05-23T14:30:00.123456Z"


def test_to_z_iso_utc_aware_emits_z() -> None:
    dt = datetime(2026, 5, 23, 14, 30, 0, 123456, tzinfo=UTC)
    assert to_z_iso(dt) == "2026-05-23T14:30:00.123456Z"


def test_to_z_iso_offset_aware_converts_to_utc_then_z() -> None:
    # BST (UTC+1): 15:30 local = 14:30 UTC.
    bst = timezone(timedelta(hours=1))
    dt = datetime(2026, 5, 23, 15, 30, 0, 123456, tzinfo=bst)
    assert to_z_iso(dt) == "2026-05-23T14:30:00.123456Z"


def test_utc_datetime_serializes_with_z_in_pydantic_model() -> None:
    """Drop-in for `datetime` on *Out models. The annotated type must
    flow through `model.model_dump_json()` with a trailing Z, otherwise
    the FE's `new Date(iso)` will parse it as local."""

    class Wire(BaseModel):
        ts: UtcDateTime

    naive = Wire(ts=datetime(2026, 5, 23, 14, 30, 0))
    assert '"ts":"2026-05-23T14:30:00Z"' in naive.model_dump_json()

    aware = Wire(ts=datetime(2026, 5, 23, 14, 30, 0, tzinfo=UTC))
    assert '"ts":"2026-05-23T14:30:00Z"' in aware.model_dump_json()


def test_utc_datetime_optional_none_renders_as_null() -> None:
    """`UtcDateTime | None` should serialize None to JSON null without
    feeding None into the PlainSerializer (which would raise)."""

    class Wire(BaseModel):
        ts: UtcDateTime | None

    assert '"ts":null' in Wire(ts=None).model_dump_json()
