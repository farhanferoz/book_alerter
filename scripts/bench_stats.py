"""Benchmark the dashboard's per-request stats computation against a real DB.

Usage:  uv run python scripts/bench_stats.py <path-to-sqlite-db>

Measures wall time of the same work `GET /api/books` does — select every
active book, then compute each one's `BookStats` bundle — by calling the
`list_books` route handler directly (no HTTP layer, no FastAPI app needed;
`list_books(session, cfg)` is a plain function once you supply the objects
its `Annotated[..., Depends(...)]` hints would otherwise inject).

Written first, against the unmodified code, to pin the BEFORE number ahead
of migration 0020 + the `stats.py` restructure (plan task T3.1). Rerun
after the restructure lands to confirm the target: <= 0.35s for 13 books
(prod copy shape as of 2026-09-04).

T3.4 gave `list_books` a `request: Request` parameter (it reads
`request.app.state.medians_cache` — a 60s-TTL cache, so the very first call
still pays the same `source_seller_global_shipping_medians` scan this
script measures). A `SimpleNamespace` stand-in supplies just the
`.app.state.medians_cache` path `list_books` actually reads — no real
Starlette `Request`/ASGI scope needed, matching this script's whole point
of calling the handler as a plain function.
"""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

from book_alerter.api.books import list_books
from book_alerter.config import Config
from book_alerter.db.session import get_engine, session_scope
from book_alerter.stats import MediansCache


def main(db_path: str) -> None:
    engine = get_engine(f"sqlite:///{db_path}")
    cfg = Config()
    state = SimpleNamespace(medians_cache=MediansCache())
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    with session_scope(engine) as session:
        start = time.perf_counter()
        books = list_books(request, session, cfg)
        elapsed = time.perf_counter() - start
    print(f"list_books: {len(books)} books in {elapsed:.3f}s")


_EXPECTED_ARGC = 2  # argv[0] + db path

if __name__ == "__main__":
    if len(sys.argv) != _EXPECTED_ARGC:
        print("usage: uv run python scripts/bench_stats.py <path-to-sqlite-db>", file=sys.stderr)
        raise SystemExit(1)
    main(sys.argv[1])
