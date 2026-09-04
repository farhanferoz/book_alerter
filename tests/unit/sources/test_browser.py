"""Unit tests for `book_alerter.sources.browser`.

`derive_user_agent` is the one piece of `BrowserSession` cheap enough to
unit-test without a real Chromium launch — everything else (profile-dir
handling, the two-stage launch) is covered by the integration tests that
drive `Source.prepare()`/`cleanup()` and by the manual fingerprint check
run against the real installed browser (see T1.1 report).
"""

from __future__ import annotations

import pytest

from book_alerter.sources.browser import derive_user_agent


@pytest.mark.parametrize(
    "chrome_version",
    [
        "147.0.7727.0",
        "139.0.7258.5",
        "120.0.0.0",
        "99.0.4844.51",
    ],
)
def test_derive_user_agent_never_contains_headless(chrome_version: str) -> None:
    """Measured 2026-09-04: `channel="chromium"` alone leaves the
    `HeadlessChrome` token in `navigator.userAgent` — only an explicit
    `user_agent=` override clears it. This locks that override's shape so
    it can never regress to the literal `HeadlessChrome`."""
    ua = derive_user_agent(chrome_version)
    assert "Headless" not in ua
    assert "HeadlessChrome" not in ua


@pytest.mark.parametrize(
    "chrome_version",
    ["147.0.7727.0", "139.0.7258.5", "120.0.0.0"],
)
def test_derive_user_agent_embeds_the_version(chrome_version: str) -> None:
    ua = derive_user_agent(chrome_version)
    assert f"Chrome/{chrome_version}" in ua


def test_derive_user_agent_looks_like_desktop_chrome() -> None:
    ua = derive_user_agent("147.0.7727.0")
    assert ua.startswith("Mozilla/5.0 (X11; Linux x86_64)")
    assert "AppleWebKit/537.36" in ua
    assert "Safari/537.36" in ua
