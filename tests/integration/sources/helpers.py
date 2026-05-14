"""Shared helpers for Playwright-backed source integration tests."""
from __future__ import annotations

from collections.abc import Callable


def make_fake_playwright_factory(html_to_return: str) -> Callable[[], object]:
    """Build a fake `async_playwright` callable for tests.

    Mirrors the API surface a real-browser source uses inside `_render`:
    `factory()(async ctx mgr) → pw.chromium.launch() → browser` with
    `new_context()` → context with `new_page()` → page exposing
    `goto / wait_for_selector / content`. The page's `content()` returns
    `html_to_return`.
    """

    class _Page:
        async def goto(self, *a, **kw): return None
        async def wait_for_selector(self, *a, **kw): return None
        async def content(self): return html_to_return

    class _Context:
        async def new_page(self): return _Page()

    class _Browser:
        async def new_context(self, **kw): return _Context()
        async def close(self): return None

    class _Chromium:
        async def launch(self, **kw): return _Browser()

    class _PW:
        chromium = _Chromium()

    class _Factory:
        async def __aenter__(self): return _PW()
        async def __aexit__(self, *a): return None

    return lambda: _Factory()
