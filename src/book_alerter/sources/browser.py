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
    browser holds no profile dir at all.
    """
    browser: Browser = await playwright.chromium.launch(
        channel="chromium", headless=True
    )
    try:
        return browser.version
    finally:
        await browser.close()


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
    """

    def __init__(
        self,
        profile: str,
        *,
        locale: str = "en-GB",
        timezone_id: str = "Europe/London",
        profile_root: Path | None = None,
    ) -> None:
        self._profile = profile
        self._locale = locale
        self._timezone_id = timezone_id
        self._profile_root = profile_root or _PROFILE_ROOT
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> BrowserContext:
        if self._context is not None:
            raise RuntimeError(f"BrowserSession({self._profile!r}) already started")

        user_data_dir = self._profile_root / self._profile
        user_data_dir.mkdir(parents=True, exist_ok=True)
        # mkdir's `mode=` argument is masked by the process umask, so set
        # the permission explicitly — the profile directory holds cookies
        # and local storage and must never be group/world readable.
        user_data_dir.chmod(0o700)

        playwright = await async_playwright().start()
        try:
            chrome_version = await _probe_chrome_version(playwright)
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
        self._playwright = playwright
        self._context = context
        return context

    async def close(self) -> None:
        """Close the context then stop the Playwright driver. Safe to call
        on a session that was never started (no-op) — callers that
        default-noop `Source.cleanup()` don't need to track whether
        `prepare()` ran."""
        context, self._context = self._context, None
        playwright, self._playwright = self._playwright, None
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
