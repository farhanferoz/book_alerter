"""Shared httpx.AsyncClient plumbing.

A single client lives on `app.state.http` for the lifetime of the FastAPI
process. Callers that have a Request (HTTP handlers) pull it via
`Depends(get_http)`; non-handler callers (sources running in the scheduler,
the ntfy notifier) get it injected at construction time by `_build_runtime`.

Every consumer falls back to per-call `async with httpx.AsyncClient(...)`
when `client` is None, which keeps unit tests and standalone CLI use
working without a live app.
"""

from __future__ import annotations

import httpx
from fastapi import Request


def build_shared_client() -> httpx.AsyncClient:
    """Construct the process-wide client used for everything that's not
    a long-lived scrape (Playwright handles its own sessions).

    Timeout matches the most-generous per-call value previously used in
    the sources/metadata modules; per-request overrides (e.g. WOB's 30s)
    can still be passed at the call site."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=10.0),
        follow_redirects=True,
    )


async def get_http(request: Request) -> httpx.AsyncClient | None:
    """FastAPI dependency exposing the lifespan-scoped client.

    Returns None in test contexts whose fixture bypasses the lifespan
    (the api_client fixture attaches a stub scheduler but no http client).
    Every downstream consumer accepts `None` and falls back to a per-call
    `async with httpx.AsyncClient(...)`."""
    return getattr(request.app.state, "http", None)
