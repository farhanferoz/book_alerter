"""Playwright browser-session lifecycle, shared by every Chromium-backed
source and metadata lookup.

Single owner of Playwright launch/close so the launch args, UA derivation,
and persistent-profile handling live in one place — `AmazonUKInlineSource`,
`AmazonUKProductInlineSource`, `BookfinderInlineSource`, and the Amazon
metadata fallbacks all route through this. No other module should import
`async_playwright`.

Why a persistent profile (measured 2026-09-04,
docs/superpowers/plans/2026-09-04-wave0-probe-results.md T0.2/T0.3):

- A fresh, cookieless browser on every fetch gets served Amazon's
  first-order promotional "FREE delivery" promise, which the parser cannot
  distinguish from a genuine free-shipping offer and records as
  `shipping_minor = 0` — 8 of 9 offers on one captured page. A profile that
  persists between runs stops looking like a first-time visitor, which
  converges the scraped shipping cost on the price a real customer pays.
- `channel="chromium"` swaps the headless-shell build for the full Chrome
  build, taking `navigator.plugins.length` from 0 to 5 and giving
  `window.chrome` a real object instead of `undefined` — both are stronger
  headless-detection signals than the user-agent string. It does NOT clear
  the `HeadlessChrome` token from the UA on its own, though, so an explicit
  `user_agent=` override is still required alongside it, not redundant with
  it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    async_playwright,
)

from book_alerter.logging_setup import get_logger

log = get_logger(__name__)

_PROFILE_ROOT = Path("data/browser-profiles")
# T1.5: failure-page dumps land here, one subdirectory per source name —
# same `data/<x>` convention as `_PROFILE_ROOT` above and `keepa.DEFAULT_CACHE_DIR`.
_DEBUG_ROOT = Path("data/debug")

# `--disable-blink-features=AutomationControlled` was the existing app's
# only stealth flag pre-T1.1 (amazon.py / bookfinder.py, both now deleted);
# carried forward unchanged.
_LAUNCH_ARGS: tuple[str, ...] = (
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
)

_VIEWPORT = {"width": 1366, "height": 768}


def derive_user_agent(chrome_version: str) -> str:
    """Build a desktop-Chrome UA string for `chrome_version`.

    Never contains the literal "HeadlessChrome" — measured 2026-09-04:
    `channel="chromium"` alone does not clear that token from
    `navigator.userAgent`; only an explicit override does.
    """
    return (
        f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
    )


async def _probe_chrome_version(playwright: Playwright) -> str:
    """Launch a throwaway (non-persistent) browser just to read the real
    Chrome build version, then close it.

    Kept separate from the persistent-context launch below so the profile
    directory is never touched with the default (HeadlessChrome-bearing)
    user agent before the derived override is known — the throwaway
    browser holds no profile dir at all. Callers should go through
    `_get_chrome_version` below rather than call this directly — the build
    can't change within a process lifetime, so probing on every
    `BrowserSession.start()` is a redundant Chromium launch.
    """
    browser: Browser = await playwright.chromium.launch(
        channel="chromium", headless=True
    )
    try:
        return browser.version
    finally:
        await browser.close()


# Process-wide memo of the probed Chrome version — the installed build is
# fixed for the life of the process, so every BrowserSession.start() after
# the first reuses this instead of launching another throwaway browser.
# `None` means "not probed yet"; a failed probe leaves it `None` too (see
# `_get_chrome_version`) rather than caching a bad/partial result.
#
# Tests inject a known version, or force a re-probe, by monkeypatching this
# module attribute directly (`monkeypatch.setattr(browser,
# "_chrome_version_cache", "999.0.0.0")` / `..., None)`) — `monkeypatch`
# reverts it at teardown, so one test's value can't leak into another.
_chrome_version_cache: str | None = None
# Guards the probe-and-cache step so concurrent first callers (multiple
# sources' prepare() can run at once) don't each launch their own throwaway
# browser racing to populate the cache.
_chrome_version_lock = asyncio.Lock()


async def _get_chrome_version(playwright: Playwright) -> str:
    """Return the real Chrome build version, probing at most once per
    process.

    Double-checked locking: the lock-free check handles the common case
    (already cached) without any contention; the lock + recheck inside it
    means only one concurrent caller ever actually launches the probe
    browser, and the rest just read its result. A failed probe is never
    cached — it propagates to the caller (a `BrowserSession.start()`
    failure) so a transient failure can't wedge every future session with
    no version, and never silently falls back to a hardcoded one.
    """
    global _chrome_version_cache
    if _chrome_version_cache is not None:
        return _chrome_version_cache
    async with _chrome_version_lock:
        if _chrome_version_cache is None:
            _chrome_version_cache = await _probe_chrome_version(playwright)
        return _chrome_version_cache


# D24: two BrowserSessions launched concurrently on the SAME profile
# directory must serialise rather than crash — Chromium's own
# ProcessSingleton lock makes the second `launch_persistent_context()` fail
# outright ("Failed to create a ProcessSingleton for your profile
# directory"), measured against the real installed browser. The shared
# `amazon_uk_product` profile between the scheduled product source and the
# metadata fallbacks is deliberate (see module docstring / D24) — splitting
# it to dodge the collision would re-expose the metadata path to the
# first-order-promo shipping bug it exists to avoid. So: wait, don't crash.
#
# One `asyncio.Lock` per resolved profile-directory path, created lazily and
# never removed — locks are cheap, and removing one while another coroutine
# might be about to look it up is a use-after-free-shaped race not worth the
# memory savings. Keyed by the RESOLVED path (not the profile name string)
# so two different `profile_root`s that happen to reuse a profile name never
# share a lock, and two names that happen to resolve to the same real
# directory always do.
_profile_dir_locks: dict[Path, asyncio.Lock] = {}


def _profile_dir_lock(path: Path) -> asyncio.Lock:
    # No `await` anywhere in this function, so the whole get-or-create is
    # atomic with respect to other coroutines — two concurrent callers for
    # the same path can never both create a Lock and race on which "wins".
    lock = _profile_dir_locks.get(path)
    if lock is None:
        lock = asyncio.Lock()
        _profile_dir_locks[path] = lock
    return lock


# --- T1.5 diagnostic capture -------------------------------------------------
#
# On a bot challenge or an unrecognised page layout, the caller writes the
# rendered HTML here so a human can inspect what Amazon actually served
# instead of just a SourceError message. Retention is two layers, both keyed
# off the SAME `JanitorConfig.debug_keep_files` value rather than a second
# hardcoded number: `_prune_debug_dir` below is an eager write-time trim
# (a burst of failures inside one scheduler run must not fill the disk
# before the next scheduled sweep), and `janitor.sweep_debug_captures` is
# the authoritative periodic sweep, which additionally enforces the
# age cap (`debug_max_age_days`) this write-time trim does not attempt.


def _prune_debug_dir(debug_dir: Path, keep_files: int) -> None:
    """Keep only the newest `keep_files` files in `debug_dir` (by mtime,
    newest first) — same sort order `janitor.sweep_debug_captures` uses, so
    the two layers agree on which files are "newest". Never raises: an
    unremovable stale dump is not worth failing the fetch that triggered
    this call.
    """
    try:
        files = sorted(
            (p for p in debug_dir.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError as e:
        log.warning("debug_capture.prune_failed", dir=str(debug_dir), error=str(e))
        return
    for stale in files[keep_files:]:
        try:
            stale.unlink()
        except OSError as e:
            log.warning("debug_capture.prune_unlink_failed", path=str(stale), error=str(e))


def write_debug_capture(
    source_name: str, html: str, *, keep_files: int | None = None
) -> Path | None:
    """Write `html` to `data/debug/<source_name>/<UTC timestamp>.html`, then
    trim that directory to the newest `keep_files` entries.

    `keep_files` defaults to `JanitorConfig().debug_keep_files` — the
    Pydantic field's own declared default — rather than a literal `20`, so
    this write-time cap and the janitor's periodic sweep are always the same
    number unless a caller deliberately overrides one. The import is local:
    `sources/` has no other reason to depend on `config/`, and this is the
    one value it needs from it.

    Never raises — a failed diagnostic dump must not turn a real fetch
    failure into a second, unrelated one. Returns the path written, or
    `None` if the write itself failed.
    """
    if keep_files is None:
        from book_alerter.config import JanitorConfig

        keep_files = JanitorConfig().debug_keep_files

    debug_dir = _DEBUG_ROOT / source_name
    # Microsecond precision (not the plain-seconds format the weekly-backup
    # filenames use) because two dumps from the same fetch cycle — the dp
    # page's bot check, then the offer-listing page's — can legitimately
    # land inside the same second.
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")
    path = debug_dir / f"{ts}.html"
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    except OSError as e:
        log.warning("debug_capture.write_failed", source=source_name, error=str(e))
        return None

    _prune_debug_dir(debug_dir, keep_files)
    return path


class BrowserSessionBusy(Exception):
    """`BrowserSession.start()`'s `acquire_timeout` expired while waiting
    for another session already using the same profile directory.

    Distinct from a generic failure on purpose: an interactive caller
    (the ASIN-lookup / metadata-fallback endpoints) can catch this
    specifically and answer with a clear, honest reason ("a scheduled
    scrape is using the browser profile right now") instead of either
    hanging indefinitely or surfacing a bare, unexplained timeout.
    """

    def __init__(self, profile: str, timeout_s: float) -> None:
        super().__init__(
            f"browser profile {profile!r} is busy — another session held it "
            f"for over {timeout_s:g}s"
        )
        self.profile = profile
        self.timeout_s = timeout_s


class BrowserSession:
    """Async context manager owning one persistent Chromium `BrowserContext`.

    Usage:
        session = BrowserSession(profile)
        context = await session.start()
        ...
        await session.close()

    or, for a single short-lived use:

        async with BrowserSession(profile) as context:
            page = await context.new_page()
            ...

    `profile` names the persistent profile directory under
    `data/browser-profiles/<profile>/` (mode 700) — pass a source's own
    `self.name`, or a `book_alerter.enums.BrowserProfile` member (a str
    subtype) where no Source instance is available. The directory persists
    between runs by design; see the module docstring for why.

    Two `BrowserSession`s on the SAME profile directory serialise rather
    than racing Chromium's ProcessSingleton lock (D24): `start()` holds a
    per-directory `asyncio.Lock` for the session's whole open lifetime and
    `close()` releases it, so a second concurrent `start()` on the same
    directory simply waits for the first session to close instead of
    crashing with "Failed to create a ProcessSingleton". Different profile
    directories never block each other.

    `acquire_timeout` bounds that wait — `None` (the default) waits
    indefinitely, correct for a scheduled source run, which has nowhere
    better to be. An interactive caller should pass a number; on expiry
    `start()` raises `BrowserSessionBusy` and leaves nothing behind (no
    lock held, no Playwright driver running, `_context` still `None`) —
    same invariant the release-on-failed-start path below already
    protects, reused rather than duplicated.
    """

    def __init__(
        self,
        profile: str,
        *,
        locale: str = "en-GB",
        timezone_id: str = "Europe/London",
        profile_root: Path | None = None,
        acquire_timeout: float | None = None,
    ) -> None:
        self._profile = profile
        self._locale = locale
        self._timezone_id = timezone_id
        self._profile_root = profile_root or _PROFILE_ROOT
        self._acquire_timeout = acquire_timeout
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._held_lock: asyncio.Lock | None = None

    async def start(self) -> BrowserContext:
        if self._context is not None:
            raise RuntimeError(f"BrowserSession({self._profile!r}) already started")

        user_data_dir = (self._profile_root / self._profile).resolve()
        user_data_dir.mkdir(parents=True, exist_ok=True)
        # mkdir's `mode=` argument is masked by the process umask, so set
        # the permission explicitly — the profile directory holds cookies
        # and local storage and must never be group/world readable.
        user_data_dir.chmod(0o700)

        # Held until close() releases it (see class docstring / D24) — NOT
        # a plain `async with`, because the lock has to span two separate
        # method calls (start() acquires, close() releases), not one block.
        lock = _profile_dir_lock(user_data_dir)
        if self._acquire_timeout is None:
            await lock.acquire()
        else:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=self._acquire_timeout)
            except TimeoutError as e:
                # Verified empirically: a cancelled `Lock.acquire()` removes
                # its own waiter and never leaves `_locked` set — nothing to
                # release here, the lock genuinely was not acquired.
                raise BrowserSessionBusy(self._profile, self._acquire_timeout) from e
        try:
            playwright = await async_playwright().start()
            try:
                chrome_version = await _get_chrome_version(playwright)
                user_agent = derive_user_agent(chrome_version)
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir,
                    channel="chromium",
                    headless=True,
                    args=list(_LAUNCH_ARGS),
                    locale=self._locale,
                    timezone_id=self._timezone_id,
                    viewport=_VIEWPORT,
                    user_agent=user_agent,
                )
            except Exception:
                await playwright.stop()
                raise
        except Exception:
            # start() itself failed, so there will be no corresponding
            # close() call to release the lock (BrowserSessionMixin.prepare()
            # never assigns `_browser_session` when `start()` raises, so its
            # cleanup() has nothing to call close() on) — release it here or
            # every later start() on this profile directory deadlocks.
            lock.release()
            raise
        self._playwright = playwright
        self._context = context
        self._held_lock = lock
        return context

    async def close(self) -> None:
        """Close the context then stop the Playwright driver, then release
        the profile-directory lock `start()` acquired. Safe to call on a
        session that was never started (no-op) — callers that default-noop
        `Source.cleanup()` don't need to track whether `prepare()` ran."""
        context, self._context = self._context, None
        playwright, self._playwright = self._playwright, None
        lock, self._held_lock = self._held_lock, None
        if context is not None:
            try:
                await context.close()
            except Exception as e:
                log.warning(
                    "browser_session.context_close_failed",
                    profile=self._profile,
                    error=str(e),
                )
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as e:
                log.warning(
                    "browser_session.playwright_stop_failed",
                    profile=self._profile,
                    error=str(e),
                )
        if lock is not None:
            lock.release()

    async def __aenter__(self) -> BrowserContext:
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


class BrowserSessionMixin:
    """Shared `Source.prepare()`/`cleanup()` for a Source that owns one
    `BrowserSession`, keyed by the Source's own `.name`.

    All three Playwright-backed sources (`AmazonUKInlineSource`,
    `AmazonUKProductInlineSource`, `BookfinderInlineSource`) mix this in
    rather than each re-implementing session open/close — this is the
    "single implementation" `BrowserSession`'s docstring refers to. `fetch()`
    reads the context this opens from `self._context`; it's `None` until
    `prepare()` runs, so a `fetch()` called out of order (e.g. a test that
    skips the scheduler) fails an assertion rather than launching a browser
    implicitly.
    """

    name: str
    _browser_session: BrowserSession | None = None
    _context: BrowserContext | None = None

    async def prepare(self) -> None:
        session = BrowserSession(self.name)
        self._context = await session.start()
        self._browser_session = session

    async def cleanup(self) -> None:
        session, self._browser_session = self._browser_session, None
        self._context = None
        if session is not None:
            await session.close()
