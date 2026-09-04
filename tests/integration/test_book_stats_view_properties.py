"""Property-based check of the current_best last-seen candidate model.

Generates randomised offer sets — multiple sources, conditions, sellers, repeat
sightings (dups) at varied timestamps — and asserts `compute_book_stats`'
`current_best_total_minor` equals an independent Python reference of the same
last-seen logic. Explores the timestamp / dedup space that the hand-written
example tests in test_book_stats_view.py can only spot-check.

The freshness/last-seen candidate model checked here is unchanged by T3.1 —
it's still enforced in SQL by `book_live_offers`. Shipping is always known
(0) in every generated offer, so effective-total ranking (T3.1's Python
selection) reduces to ranking by total_minor exactly as the old view did;
`test_stats_current_best_selection_properties.py` is the sibling property
test that varies price/shipping independently to pin the ranking step itself.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlmodel import Session

from book_alerter.db import models
from book_alerter.stats import compute_book_stats

_SOURCES = ["amazon", "wob", "bookfinder"]
_CONDITIONS = ["new", "used_vg", "used_g"]
_SELLERS = ["S1", "S2"]
_BASE = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
_FRESH_WINDOW = timedelta(days=1)  # mirrors the view's `julianday(...) <= 1.0`

# One offer = an identity (source, condition, seller, total) seen at one or
# more whole-hour offsets. Whole hours keep the 24h freshness boundary exact
# on both sides (julianday of an N-hour gap is exactly N/24), so the view and
# the reference never straddle the boundary differently.
_offer = st.fixed_dictionaries({
    "source": st.sampled_from(_SOURCES),
    "condition": st.sampled_from(_CONDITIONS),
    "seller": st.sampled_from(_SELLERS),
    "total": st.integers(min_value=500, max_value=5000),
    "hours_ago": st.lists(
        st.integers(min_value=0, max_value=288),  # within 12 days
        min_size=1, max_size=6, unique=True,
    ),
})


def _reference_current_best(offers: list[dict]) -> int | None:
    """Cheapest total among offers present in their source's most recent scrape,
    from a source no more than a day behind the freshest scrape."""
    last_seen = {  # per offer: newest sighting
        id(o): _BASE - timedelta(hours=min(o["hours_ago"])) for o in offers
    }
    source_latest: dict[str, datetime] = {}
    for o in offers:
        ls = last_seen[id(o)]
        src = o["source"]
        source_latest[src] = max(source_latest.get(src, ls), ls)
    global_latest = max(source_latest.values())
    live = [
        o["total"]
        for o in offers
        if last_seen[id(o)] == source_latest[o["source"]]
        and global_latest - source_latest[o["source"]] <= _FRESH_WINDOW
    ]
    return min(live) if live else None


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(offers=st.lists(_offer, min_size=1, max_size=8))
def test_current_best_matches_last_seen_reference(engine_with_view, offers):
    with Session(engine_with_view) as s:
        # Isolate each hypothesis example (the fixture's DB persists across them).
        s.connection().exec_driver_sql("DELETE FROM priceobservation")
        s.connection().exec_driver_sql("DELETE FROM book")
        s.commit()
        book = models.Book(
            isbn13="9780000000000", title="t", author="a",
            created_at=_BASE, updated_at=_BASE,
        )
        s.add(book); s.commit(); s.refresh(book)

        for o in offers:
            hours = sorted(o["hours_ago"], reverse=True)  # oldest first = canonical
            canonical = models.PriceObservation(
                book_id=book.id, source=o["source"], condition=o["condition"],
                seller=o["seller"], price_minor=o["total"], currency="GBP",
                shipping_minor=0, total_minor=o["total"], url="https://x",
                observed_at=_BASE - timedelta(hours=hours[0]), raw={},
            )
            s.add(canonical); s.commit(); s.refresh(canonical)
            for h in hours[1:]:  # later sightings are dups of the canonical
                s.add(models.PriceObservation(
                    book_id=book.id, source=o["source"], condition=o["condition"],
                    seller=o["seller"], price_minor=o["total"], currency="GBP",
                    shipping_minor=0, total_minor=o["total"], url="https://x",
                    observed_at=_BASE - timedelta(hours=h), raw={},
                    is_duplicate_of=canonical.id,
                ))
        s.commit()

        stats = compute_book_stats(book.id, s)

    assert stats.current_best_total_minor == _reference_current_best(offers)
