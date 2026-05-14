"""Unit tests for the `_in_quiet_hours` pure helper (Task 5.2).

The helper takes a timezone-aware (or naive — only hour/minute matter) datetime
and a `QuietHours | None` and decides if the moment falls inside the configured
window. It supports both normal (start < end) and wrapping (start > end)
windows, e.g. `22:00`–`08:00`. Boundary semantics are spec'd here so future
refactors can't silently flip them: `start` is inclusive, `end` is exclusive.
"""
from __future__ import annotations

from datetime import datetime

from book_alerter.config import QuietHours
from book_alerter.notifications.dispatcher import _in_quiet_hours


def _dt(h: int, m: int = 0) -> datetime:
    """Build a naive datetime — the helper only inspects hour/minute."""
    return datetime(2026, 5, 14, h, m)


# ---- qh=None: always allow ------------------------------------------------


def test_none_quiet_hours_is_always_false() -> None:
    """`qh=None` disables the gate at every hour of the day."""
    for h in range(24):
        assert _in_quiet_hours(_dt(h), None) is False


# ---- normal window (start < end) ------------------------------------------


def test_normal_window_inside_returns_true() -> None:
    qh = QuietHours(start="12:00", end="14:00", tz="UTC")
    assert _in_quiet_hours(_dt(13, 0), qh) is True
    assert _in_quiet_hours(_dt(12, 30), qh) is True
    assert _in_quiet_hours(_dt(13, 59), qh) is True


def test_normal_window_start_is_inclusive() -> None:
    qh = QuietHours(start="12:00", end="14:00", tz="UTC")
    assert _in_quiet_hours(_dt(12, 0), qh) is True


def test_normal_window_end_is_exclusive() -> None:
    qh = QuietHours(start="12:00", end="14:00", tz="UTC")
    assert _in_quiet_hours(_dt(14, 0), qh) is False


def test_normal_window_before_and_after_are_false() -> None:
    qh = QuietHours(start="12:00", end="14:00", tz="UTC")
    assert _in_quiet_hours(_dt(11, 59), qh) is False
    assert _in_quiet_hours(_dt(14, 1), qh) is False
    assert _in_quiet_hours(_dt(0, 0), qh) is False
    assert _in_quiet_hours(_dt(23, 59), qh) is False


# ---- wrapping window (start > end) ----------------------------------------


def test_wrapping_window_evening_side_true() -> None:
    qh = QuietHours(start="22:00", end="08:00", tz="UTC")
    assert _in_quiet_hours(_dt(23, 0), qh) is True


def test_wrapping_window_morning_side_true() -> None:
    qh = QuietHours(start="22:00", end="08:00", tz="UTC")
    assert _in_quiet_hours(_dt(2, 0), qh) is True


def test_wrapping_window_end_is_exclusive() -> None:
    qh = QuietHours(start="22:00", end="08:00", tz="UTC")
    assert _in_quiet_hours(_dt(8, 0), qh) is False


def test_wrapping_window_midday_false() -> None:
    qh = QuietHours(start="22:00", end="08:00", tz="UTC")
    assert _in_quiet_hours(_dt(12, 0), qh) is False


def test_wrapping_window_just_before_start_false() -> None:
    qh = QuietHours(start="22:00", end="08:00", tz="UTC")
    assert _in_quiet_hours(_dt(21, 59), qh) is False


def test_wrapping_window_start_is_inclusive() -> None:
    qh = QuietHours(start="22:00", end="08:00", tz="UTC")
    assert _in_quiet_hours(_dt(22, 0), qh) is True


# ---- degenerate same-start-end window -------------------------------------


def test_same_start_and_end_window_is_always_false() -> None:
    """When start == end the formula falls into the normal-window branch
    (`s > e` is False) and evaluates `s <= cur < e` — vacuously False for
    every time. Documenting so a future refactor doesn't accidentally make
    `08:00`–`08:00` mean "quiet all day"."""
    qh = QuietHours(start="08:00", end="08:00", tz="UTC")
    for h in range(24):
        assert _in_quiet_hours(_dt(h, 0), qh) is False
    # Including the exact boundary minute itself.
    assert _in_quiet_hours(_dt(8, 0), qh) is False
