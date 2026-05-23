"""Shared string enums used across models, sources, alerts, and notifications.

All enums here are `StrEnum` (PEP 663, Python 3.11+). Two properties matter:

1. **Wire format identical to the previous `Literal[...]` strings.**  Each
   member's value is the lowercase token stored in SQLite and emitted in JSON
   API responses. Existing rows + existing YAML configs continue to parse
   without migration.
2. **Equality with plain strings works both ways.**  `Condition.NEW == "new"`
   and `"new" == Condition.NEW` both return True, so code that round-trips a
   value through SQLite (where the column is `String`, not a SQLAlchemy
   `Enum`) can keep comparing with the enum member regardless of whether the
   runtime value is a `str` or a real `Condition` instance.

We deliberately keep model columns as `Column(String, nullable=False)` (not
`Column(Enum(...))`) for two reasons:
- SQLAlchemy's default `Enum` type persists `member.name` (uppercase) which
  would silently break wire format if we forgot `values_callable`. String
  columns sidestep the trap.
- It mirrors the pattern already documented in `RESUME.md` for the
  pre-existing `Literal[...]`-typed fields, so the migration is "swap the
  type annotation" with zero column DDL change.
"""

from __future__ import annotations

from enum import StrEnum


class Condition(StrEnum):
    """Used-grade taxonomy for `PriceObservation.condition` and
    `ProductObservation.condition`. UNKNOWN is the fallthrough for parsers
    that can't pin a grade."""

    NEW = "new"
    USED_VG = "used_vg"
    USED_G = "used_g"
    USED_ACCEPTABLE = "used_acceptable"
    UNKNOWN = "unknown"


class AlertKind(StrEnum):
    """Kinds of alerts the pipeline can fire. Order matters only for the
    dispatch precedence in `detect_alert_kinds` (NEW_LOW > TARGET_HIT >
    PERCENTILE_CROSS for the same observation), which is encoded in the
    function, not here."""

    TARGET_HIT = "target_hit"
    PERCENTILE_CROSS = "percentile_cross"
    NEW_LOW = "new_low"


class ItemKind(StrEnum):
    """Discriminator for tracked-item polymorphism. Used by `SourceConfig` to
    declare which kinds a source serves, by the scheduler to route per-kind
    iteration, and by the dispatcher to choose alert-title prefixes."""

    BOOK = "book"
    PRODUCT = "product"


class ItemStatus(StrEnum):
    """Lifecycle of a tracked item. ACTIVE = scrape + alert; ARCHIVED =
    soft-deleted, hidden from the dashboard but rows preserved; BOUGHT = user
    purchased and recorded the price; the engine stops alerting."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    BOUGHT = "bought"


class SourceRunStatus(StrEnum):
    """Per-scheduler-job audit row status. PARTIAL = some items succeeded,
    others raised SourceError."""

    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"


class NotificationDeliveryStatus(StrEnum):
    """One row per (alert, channel). ERROR keeps the error_message column
    populated so the dashboard can surface "ntfy failed at 21:03"."""

    SENT = "sent"
    ERROR = "error"


class BookFormat(StrEnum):
    """User-facing format preference on a Book. ANY accepts whichever
    Amazon/WoB happen to offer cheapest; PAPERBACK / HARDCOVER are filters
    we'd apply at display time (not currently enforced — see RESUME)."""

    PAPERBACK = "paperback"
    HARDCOVER = "hardcover"
    ANY = "any"
