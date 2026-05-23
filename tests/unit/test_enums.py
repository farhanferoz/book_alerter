"""Wire-format invariants for the shared StrEnums.

These tests pin the exact string values that the database, the YAML config,
and external API consumers see. Any change here is a wire-format break and
must be paired with a data migration.
"""

from __future__ import annotations

import json

import pytest

from book_alerter.enums import (
    AlertKind,
    BookFormat,
    Condition,
    ItemKind,
    ItemStatus,
    NotificationDeliveryStatus,
    SourceRunStatus,
)


def test_condition_values_match_legacy_literal() -> None:
    """These five strings appear in real DB rows; any rename breaks reads."""
    assert {c.value for c in Condition} == {
        "new", "used_vg", "used_g", "used_acceptable", "unknown",
    }


def test_alert_kind_values_match_legacy_literal() -> None:
    """Persisted in `alert.kind` and `productalert.kind` and surfaced over
    /api/alerts. Renames break dashboards."""
    assert {k.value for k in AlertKind} == {
        "target_hit", "percentile_cross", "new_low",
    }


def test_item_status_values_match_legacy_literal() -> None:
    """Persisted in `book.status` and `product.status`; UI filters on these."""
    assert {s.value for s in ItemStatus} == {"active", "archived", "bought"}


def test_source_run_status_values_match_legacy_literal() -> None:
    assert {s.value for s in SourceRunStatus} == {
        "running", "success", "error", "partial",
    }


def test_notification_delivery_status_values_match_legacy_literal() -> None:
    assert {s.value for s in NotificationDeliveryStatus} == {"sent", "error"}


def test_book_format_values_match_legacy_literal() -> None:
    assert {f.value for f in BookFormat} == {"paperback", "hardcover", "any"}


def test_item_kind_values_are_lowercase() -> None:
    assert {k.value for k in ItemKind} == {"book", "product"}


@pytest.mark.parametrize(
    ("member", "expected_str"),
    [
        (Condition.NEW, "new"),
        (Condition.USED_VG, "used_vg"),
        (AlertKind.TARGET_HIT, "target_hit"),
        (ItemKind.BOOK, "book"),
        (ItemKind.PRODUCT, "product"),
        (ItemStatus.ACTIVE, "active"),
    ],
)
def test_str_equality_with_bare_strings(member, expected_str) -> None:
    """StrEnum equality with plain strings — load-bearing for DB roundtrips,
    where the value comes back as a `str` (not a StrEnum) after reading from
    a `Column(String)` column."""
    assert member == expected_str
    assert expected_str == member


def test_str_enum_json_serialises_to_value() -> None:
    """Wire format — Pydantic uses str() to render StrEnum, which gives the
    lowercase value, not the uppercase member name. Identical to what the
    `Literal[...]` era produced."""
    payload = {
        "condition": Condition.USED_VG,
        "kind": AlertKind.NEW_LOW,
        "item_kind": ItemKind.PRODUCT,
    }
    raw = json.dumps(payload)
    assert json.loads(raw) == {
        "condition": "used_vg",
        "kind": "new_low",
        "item_kind": "product",
    }


def test_str_enum_round_trip_through_string_column_value() -> None:
    """Emulates the DB roundtrip path: write enum value, read back as str,
    compare against enum member. The comparison must succeed both ways."""
    stored: str = str(Condition.USED_G)
    assert stored == "used_g"
    # And the equality must still hold against the enum member.
    assert Condition(stored) is Condition.USED_G
    assert stored == Condition.USED_G
