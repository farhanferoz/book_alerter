"""Unit tests for `book_alerter.sources.browser`.

`derive_user_agent` and the Chrome-version memoisation are the pieces of
`BrowserSession` cheap enough to unit-test without a real Chromium launch
(the version-probe test below fakes the whole Playwright driver) —
everything else (profile-dir handling, the real two-stage launch) is
covered by the integration tests that drive `Source.prepare()`/`cleanup()`
and by the manual fingerprint check run against the real installed browser
(see T1.1 report).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import book_alerter.sources.browser as browser_mod
from book_alerter.sources.browser import BrowserSession, derive_user_agent


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


class _FakeProbeBrowser:
    """Stands in for the throwaway `chromium.launch()` browser used only
    to read `.version`."""

    version = "147.0.7727.0"

    async def close(self) -> None:
        return None


class _FakeContext:
    async def close(self) -> None:
        return None


class _FakeChromium:
    def __init__(self) -> None:
        self.probe_launch_calls = 0
        self.persistent_launch_calls = 0

    async def launch(self, **kwargs):
        self.probe_launch_calls += 1
        return _FakeProbeBrowser()

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.persistent_launch_calls += 1
        return _FakeContext()


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()

    async def stop(self) -> None:
        return None


def _fake_async_playwright(fake_pw: _FakePlaywright):
    """Build a fake `async_playwright` callable: `async_playwright().start()
    -> fake_pw`, matching how `BrowserSession.start()` drives the real one
    (`await async_playwright().start()`, not `async with async_playwright()`)."""

    class _StartOnly:
        async def start(self) -> _FakePlaywright:
            return fake_pw

    return lambda: _StartOnly()


async def test_browser_session_second_start_does_not_reprobe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The Chrome build can't change within a process — two `BrowserSession`s
    (e.g. two sources' `prepare()` in the same scheduler run) must share one
    memoised probe rather than each launching a throwaway browser."""
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", None)
    fake_pw = _FakePlaywright()
    monkeypatch.setattr(browser_mod, "async_playwright", _fake_async_playwright(fake_pw))

    session1 = BrowserSession("amazon", profile_root=tmp_path)
    await session1.start()
    await session1.close()

    session2 = BrowserSession("bookfinder", profile_root=tmp_path)
    await session2.start()
    await session2.close()

    assert fake_pw.chromium.probe_launch_calls == 1
    assert fake_pw.chromium.persistent_launch_calls == 2
    assert browser_mod._chrome_version_cache == "147.0.7727.0"


async def test_get_chrome_version_is_concurrency_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent first callers (multiple sources' prepare() racing) must
    still only trigger one probe launch, not one each."""
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", None)
    fake_pw = _FakePlaywright()

    results = await asyncio.gather(
        *(browser_mod._get_chrome_version(fake_pw) for _ in range(5))
    )

    assert results == ["147.0.7727.0"] * 5
    assert fake_pw.chromium.probe_launch_calls == 1
