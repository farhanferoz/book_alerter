"""Shared httpx.AsyncClient plumbing.

A single client lives on `app.state.http` for the FastAPI process lifetime.
HTTP handlers pull it via `Depends(get_http)`; scheduler-driven sources and
notifiers get it injected at construction time by `_build_runtime`.

Every consumer accepts `http: AsyncClient | None`; `shared_or_fresh(http)`
yields the shared client when present or opens a one-shot client otherwise,
so unit tests and CLI use that bypass the lifespan still work.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import Request

_DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def build_shared_client() -> httpx.AsyncClient:
    """Process-wide client for non-Playwright HTTP. Per-request overrides
    (timeout, headers) are passed at the call site."""
    return httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True)


async def get_http(request: Request) -> httpx.AsyncClient | None:
    """Lifespan-scoped client; None outside lifespan (tests / CLI)."""
    return getattr(request.app.state, "http", None)


@asynccontextmanager
async def shared_or_fresh(
    http: httpx.AsyncClient | None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield the shared client if provided, else open a fresh one-shot
    client with the same defaults. The shared client is NOT closed on
    exit — its lifecycle is owned by the FastAPI lifespan."""
    if http is not None:
        yield http
    else:
        async with build_shared_client() as client:
            yield client
