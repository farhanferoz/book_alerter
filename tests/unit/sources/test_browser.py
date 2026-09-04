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
import os
from pathlib import Path

import pytest

import book_alerter.sources.browser as browser_mod
from book_alerter.config import JanitorConfig
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


async def test_browser_session_serialises_same_profile_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """D24: a second BrowserSession.start() on the SAME profile directory
    must wait for the first session's close() — not race Chromium's
    ProcessSingleton lock — and both must eventually succeed."""
    fake_pw = _FakePlaywright()
    monkeypatch.setattr(browser_mod, "async_playwright", _fake_async_playwright(fake_pw))
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", "147.0.7727.0")

    session1 = BrowserSession("amazon", profile_root=tmp_path)
    await session1.start()

    resolved = (tmp_path / "amazon").resolve()
    lock = browser_mod._profile_dir_lock(resolved)
    assert lock.locked()

    session2 = BrowserSession("amazon", profile_root=tmp_path)
    second_start = asyncio.create_task(session2.start())
    await asyncio.sleep(0)  # let second_start run up to its blocked acquire()
    assert not second_start.done(), "second start() must block while first holds the lock"

    await session1.close()
    assert not lock.locked() or second_start.done(), (
        "closing the first session must free the lock for the second"
    )

    await second_start  # now unblocks
    assert second_start.done()
    await session2.close()

    assert fake_pw.chromium.persistent_launch_calls == 2
    assert not lock.locked()


async def test_browser_session_different_profiles_do_not_block_each_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """D24 also requires the converse: two DIFFERENT profile directories
    must never serialise against each other."""
    fake_pw = _FakePlaywright()
    monkeypatch.setattr(browser_mod, "async_playwright", _fake_async_playwright(fake_pw))
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", "147.0.7727.0")

    session_a = BrowserSession("amazon", profile_root=tmp_path)
    session_b = BrowserSession("bookfinder", profile_root=tmp_path)

    await session_a.start()
    second_start = asyncio.create_task(session_b.start())
    await asyncio.sleep(0)
    assert second_start.done(), "a different profile directory must never block on session_a's lock"

    await second_start
    await session_a.close()
    await session_b.close()

    assert fake_pw.chromium.persistent_launch_calls == 2


# --- T1.5 diagnostic capture -------------------------------------------------


def test_write_debug_capture_writes_html_under_source_subdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(browser_mod, "_DEBUG_ROOT", tmp_path)

    path = browser_mod.write_debug_capture("amazon", "<html>challenge</html>", keep_files=20)

    assert path is not None
    assert path.parent == tmp_path / "amazon"
    assert path.suffix == ".html"
    assert path.read_text(encoding="utf-8") == "<html>challenge</html>"


def test_write_debug_capture_defaults_keep_files_to_janitor_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """No `keep_files=` override must behave exactly like passing
    `JanitorConfig().debug_keep_files` explicitly — the whole point of
    reusing the field is that there is only ever one number."""
    monkeypatch.setattr(browser_mod, "_DEBUG_ROOT", tmp_path)
    default_cap = JanitorConfig().debug_keep_files
    assert default_cap > 0, "test assumes the real default leaves room to prune below"

    for i in range(default_cap + 3):
        browser_mod.write_debug_capture("amazon", f"<html>{i}</html>")  # no keep_files=

    remaining = list((tmp_path / "amazon").iterdir())
    assert len(remaining) == default_cap


def test_write_debug_capture_never_raises_on_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A diagnostic dump failing to write must not turn a real fetch
    failure into a second, unrelated one — write_debug_capture swallows
    and logs instead of raising."""
    monkeypatch.setattr(browser_mod, "_DEBUG_ROOT", tmp_path)

    def _boom(self, *a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)

    result = browser_mod.write_debug_capture("amazon", "<html>x</html>", keep_files=20)

    assert result is None


def test_prune_debug_dir_keeps_newest_n_by_mtime(tmp_path: Path) -> None:
    """Direct test of the rotation logic, with explicit mtimes so ordering
    is deterministic regardless of filesystem timestamp resolution."""
    debug_dir = tmp_path / "amazon"
    debug_dir.mkdir()
    paths = [debug_dir / f"{i}.html" for i in range(5)]
    for i, p in enumerate(paths):
        p.write_text("x", encoding="utf-8")
        # Strictly increasing mtimes: paths[4] is newest, paths[0] oldest.
        os.utime(p, (i, i))

    browser_mod._prune_debug_dir(debug_dir, keep_files=2)

    remaining = set(debug_dir.iterdir())
    assert remaining == {paths[4], paths[3]}


def test_prune_debug_dir_keep_files_zero_removes_everything(tmp_path: Path) -> None:
    debug_dir = tmp_path / "amazon"
    debug_dir.mkdir()
    (debug_dir / "a.html").write_text("x", encoding="utf-8")
    (debug_dir / "b.html").write_text("x", encoding="utf-8")

    browser_mod._prune_debug_dir(debug_dir, keep_files=0)

    assert list(debug_dir.iterdir()) == []


# --- bounded acquire_timeout (interactive path) -----------------------------


async def test_browser_session_acquire_timeout_fails_cleanly_without_poisoning_the_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Three-part contract: (1) a session with a short acquire_timeout on an
    already-held profile directory raises BrowserSessionBusy rather than
    hanging or racing Chromium's ProcessSingleton; (2) it leaves nothing
    behind (no lock held, no Playwright driver, _context still None); and
    (3) a later caller still succeeds once the original holder closes —
    the timeout must not poison the lock for anyone after it."""
    fake_pw = _FakePlaywright()
    monkeypatch.setattr(browser_mod, "async_playwright", _fake_async_playwright(fake_pw))
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", "147.0.7727.0")

    session1 = BrowserSession("amazon", profile_root=tmp_path)
    await session1.start()

    session2 = BrowserSession("amazon", profile_root=tmp_path, acquire_timeout=0.05)
    with pytest.raises(browser_mod.BrowserSessionBusy) as exc_info:
        await session2.start()
    assert exc_info.value.profile == "amazon"
    assert session2._context is None
    assert session2._playwright is None
    assert session2._held_lock is None

    resolved = (tmp_path / "amazon").resolve()
    lock = browser_mod._profile_dir_lock(resolved)
    assert lock.locked(), "session1 still holds it — session2's timeout must not touch it"

    await session1.close()
    assert not lock.locked()

    session3 = BrowserSession("amazon", profile_root=tmp_path, acquire_timeout=1.0)
    await session3.start()
    await session3.close()

    # session1 + session3 launched; session2 never did.
    assert fake_pw.chromium.persistent_launch_calls == 2


async def test_browser_session_acquire_timeout_none_waits_indefinitely() -> None:
    """The default (acquire_timeout=None) is what scheduled source runs
    get — spot-check it doesn't accidentally inherit a default bound."""
    session = BrowserSession("amazon")
    assert session._acquire_timeout is None


# --- T1.4: process-wide concurrency cap --------------------------------------


@pytest.fixture
def reset_browser_concurrency():
    """Restore the module-level cap, which is process-wide state."""
    saved_limit = browser_mod._browser_limit
    saved_sem = browser_mod._browser_semaphore
    yield
    browser_mod._browser_limit = saved_limit
    browser_mod._browser_semaphore = saved_sem


def test_configure_browser_concurrency_is_a_noop_when_unchanged(
    reset_browser_concurrency,
) -> None:
    """A config reload that doesn't touch this setting must not swap the
    semaphore out from under live sessions."""
    before = browser_mod._browser_semaphore
    browser_mod.configure_browser_concurrency(browser_mod._browser_limit)
    assert browser_mod._browser_semaphore is before

    browser_mod.configure_browser_concurrency(browser_mod._browser_limit + 3)
    assert browser_mod._browser_semaphore is not before
    assert browser_mod._browser_limit == before._value + 3


async def test_concurrency_slot_is_released_when_start_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_browser_concurrency,
) -> None:
    """A failed launch must give the slot back. Leak it and the cap silently
    tightens with every failure until no source can open a browser at all --
    and bot challenges make launch failures a routine event, not a rare one.
    """
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", None)
    browser_mod.configure_browser_concurrency(1)
    semaphore = browser_mod._browser_semaphore

    class _Boom:
        async def start(self):
            raise RuntimeError("driver failed to start")

    monkeypatch.setattr(browser_mod, "async_playwright", lambda: _Boom())

    session = BrowserSession("amazon", profile_root=tmp_path)
    with pytest.raises(RuntimeError, match="driver failed to start"):
        await session.start()

    assert semaphore._value == 1, "the slot must be returned on failure"
    # The profile lock must come back too, or the next start() on this
    # profile hangs forever.
    assert not browser_mod._profile_dir_lock(
        (tmp_path / "amazon").resolve()
    ).locked()


async def test_close_releases_the_semaphore_it_acquired_not_the_current_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_browser_concurrency,
) -> None:
    """A config reload mid-session swaps the module-level semaphore. `close()`
    must release the object `start()` took a slot from, or the new semaphore
    gains a phantom slot and the old one never gets its back."""
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", None)
    fake_pw = _FakePlaywright()
    monkeypatch.setattr(browser_mod, "async_playwright", _fake_async_playwright(fake_pw))

    browser_mod.configure_browser_concurrency(2)
    old_sem = browser_mod._browser_semaphore

    session = BrowserSession("amazon", profile_root=tmp_path)
    await session.start()
    assert old_sem._value == 1, "a slot is held while the session is open"

    browser_mod.configure_browser_concurrency(5)  # reload mid-session
    new_sem = browser_mod._browser_semaphore
    await session.close()

    assert old_sem._value == 2, "the original semaphore got its slot back"
    assert new_sem._value == 5, "the new one was never touched"


# --- F-B1: cancellation must not leak the profile lock or the browser slot --
#
# `asyncio.CancelledError` is a `BaseException`, not an `Exception`. Before
# this fix, `start()` guarded the launch with `except Exception` and
# `close()` did its two releases at the end of the function body rather than
# in a `finally` — so a cancellation landing mid-launch or mid-close escaped
# both and permanently wedged the per-profile lock and the process-wide
# semaphore slot. Reachable in production: `rebuild_runtime()` (app.py)
# calls `AsyncIOScheduler.shutdown(wait=False)`, which cancels every
# in-flight scheduler job — so a user toggling a source's settings in the
# UI while a scrape is in flight could hit this. Each test below hangs the
# fake Playwright driver at the exact await point being cancelled, since a
# fake that returns immediately would never give `task.cancel()` anything
# real to interrupt.


class _HangingEvent:
    """A `.wait()` that never resolves on its own, standing in for whatever
    Playwright call is being cancelled mid-flight."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def wait_forever(self) -> None:
        self.started.set()
        await asyncio.Event().wait()


class _HangingLaunchChromium(_FakeChromium):
    """`launch_persistent_context()` never returns on its own — the test
    cancels the task waiting on it once the launch has actually started."""

    def __init__(self) -> None:
        super().__init__()
        self._hang = _HangingEvent()

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.persistent_launch_calls += 1
        await self._hang.wait_forever()


class _HangingLaunchPlaywright(_FakePlaywright):
    def __init__(self) -> None:
        self.chromium = _HangingLaunchChromium()
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


async def test_cancel_during_launch_releases_lock_semaphore_and_stops_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_browser_concurrency,
) -> None:
    """Reproduces the reviewer's first repro line: cancelling while
    `launch_persistent_context()` is in flight must restore the semaphore
    to its pre-acquire value, leave the profile lock unheld, and still stop
    the Playwright driver (it must not become an orphan process)."""
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", "147.0.7727.0")
    fake_pw = _HangingLaunchPlaywright()
    monkeypatch.setattr(browser_mod, "async_playwright", _fake_async_playwright(fake_pw))
    configured_limit = 2
    browser_mod.configure_browser_concurrency(configured_limit)
    semaphore = browser_mod._browser_semaphore

    session = BrowserSession("amazon", profile_root=tmp_path)
    task = asyncio.create_task(session.start())
    await fake_pw.chromium._hang.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    resolved = (tmp_path / "amazon").resolve()
    lock = browser_mod._profile_dir_lock(resolved)
    assert not lock.locked(), "profile lock leaked on cancellation during launch"
    assert semaphore._value == configured_limit, (
        "concurrency slot leaked on cancellation during launch"
    )
    assert fake_pw.stop_calls == 1, "the Playwright driver must still be stopped"
    assert not browser_mod.is_profile_in_use(resolved), (
        "a launch that never completed must never be published as in-use"
    )


class _HangingCloseContext(_FakeContext):
    def __init__(self) -> None:
        self._hang = _HangingEvent()

    async def close(self) -> None:
        await self._hang.wait_forever()


class _HangingCloseChromium(_FakeChromium):
    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.persistent_launch_calls += 1
        return _HangingCloseContext()


async def test_cancel_during_close_releases_lock_and_semaphore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_browser_concurrency,
) -> None:
    """Reproduces the reviewer's second repro line: cancelling while
    `context.close()` is in flight must still restore the semaphore and the
    profile lock — before the fix, both releases sat after the two awaits
    at the end of `close()`'s body rather than in a `finally`."""
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", "147.0.7727.0")
    fake_pw = _FakePlaywright()
    fake_pw.chromium = _HangingCloseChromium()
    monkeypatch.setattr(browser_mod, "async_playwright", _fake_async_playwright(fake_pw))
    configured_limit = 2
    browser_mod.configure_browser_concurrency(configured_limit)
    semaphore = browser_mod._browser_semaphore

    session = BrowserSession("amazon", profile_root=tmp_path)
    context = await session.start()
    resolved = (tmp_path / "amazon").resolve()
    assert semaphore._value == configured_limit - 1, "a slot is held while the session is open"
    assert browser_mod.is_profile_in_use(resolved)

    task = asyncio.create_task(session.close())
    await context._hang.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    lock = browser_mod._profile_dir_lock(resolved)
    assert not lock.locked(), "profile lock leaked on cancellation during close()"
    assert semaphore._value == configured_limit, (
        "concurrency slot leaked on cancellation during close()"
    )
    assert not browser_mod.is_profile_in_use(resolved), (
        "in-use marker leaked on cancellation during close()"
    )


# --- F-B7: publishing which profile is in use for the janitor --------------


async def test_profile_marked_in_use_only_while_session_is_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`janitor.sweep_browser_profiles` reads `is_profile_in_use` from a
    worker thread to decide whether it's safe to rmtree a profile — this
    locks down the lifecycle it depends on."""
    fake_pw = _FakePlaywright()
    monkeypatch.setattr(browser_mod, "async_playwright", _fake_async_playwright(fake_pw))
    monkeypatch.setattr(browser_mod, "_chrome_version_cache", "147.0.7727.0")

    resolved = (tmp_path / "amazon").resolve()
    session = BrowserSession("amazon", profile_root=tmp_path)
    assert not browser_mod.is_profile_in_use(resolved)

    await session.start()
    assert browser_mod.is_profile_in_use(resolved)

    await session.close()
    assert not browser_mod.is_profile_in_use(resolved)
