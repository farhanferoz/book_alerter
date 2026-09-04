"""Property test pinning T3.1's Python current-best selection (plan doc
2026-09-04-review-and-optimisation-plan.md, T3.1) against the pre-migration
`book_stats` view.

`test_book_stats_view_properties.py` already pins the last-seen/freshness
*candidate* model (which stays in SQL, unchanged, as `book_live_offers`
after migration 0020). This file adds the *ranking* step T3.1 moves out of
SQL into Python: cheapest effective total (price + shipping — both always
known here, so the shipping cascade never fires), tied offers broken by
`(source, condition, COALESCE(seller, ''))` ascending — the same tie-break
`current_best`'s correlated subquery encodes today.

Written and run GREEN against the unmodified `book_stats` view before
migration 0020 (`compute_book_stats` reads the view's `current_best`
columns as-is). It must stay green, unchanged, once `compute_book_stats`
is a thin wrapper over the new Python selection — same assertion, same
independent reference model, both sides of the rewrite. That's the
regression safety net for T3.1.
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

# One offer = an identity (source, condition, seller, price, shipping) seen at
# one or more whole-hour offsets. Price and shipping are independent and both
# always present (shipping_minor is never NULL) — this test only pins the
# "shipping known" path; the cascade-estimate path is covered by test_stats.py.
_offer = st.fixed_dictionaries({
    "source": st.sampled_from(_SOURCES),
    "condition": st.sampled_from(_CONDITIONS),
    "seller": st.sampled_from(_SELLERS),
    "price": st.integers(min_value=200, max_value=4000),
    "shipping": st.integers(min_value=0, max_value=500),
    "hours_ago": st.lists(
        st.integers(min_value=0, max_value=288),  # within 12 days
        min_size=1, max_size=6, unique=True,
    ),
})


def _reference_current_best(offers: list[dict]) -> tuple[int, str, str, str] | None:
    """Independent Python model of `book_stats.current_best`.

    Stage 1 (candidates — unchanged by T3.1, still enforced in SQL): an
    offer is live iff it's part of its source's most recent scrape, and
    that source is no more than a day behind the entity's freshest scrape.
    Stage 2 (selection — the part T3.1 moves into Python): cheapest
    effective total among the live offers; ties broken alphabetically by
    (source, condition, seller-or-'').
    """
    last_seen = {id(o): _BASE - timedelta(hours=min(o["hours_ago"])) for o in offers}
    source_latest: dict[str, datetime] = {}
    for o in offers:
        ls = last_seen[id(o)]
        src = o["source"]
        source_latest[src] = max(source_latest.get(src, ls), ls)
    global_latest = max(source_latest.values())
    live = [
        o
        for o in offers
        if last_seen[id(o)] == source_latest[o["source"]]
        and global_latest - source_latest[o["source"]] <= _FRESH_WINDOW
    ]
    if not live:
        return None
    best_total = min(o["price"] + o["shipping"] for o in live)
    tied = [o for o in live if o["price"] + o["shipping"] == best_total]
    winner = min(tied, key=lambda o: (o["source"], o["condition"], o["seller"] or ""))
    return (best_total, winner["source"], winner["condition"], winner["seller"])


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(offers=st.lists(_offer, min_size=1, max_size=8))
def test_current_best_selection_matches_effective_total_reference(engine_with_view, offers):
    with Session(engine_with_view) as s:
        # Isolate each hypothesis example (the fixture's DB persists across them).
        s.connection().exec_driver_sql("DELETE FROM priceobservation")
        s.connection().exec_driver_sql("DELETE FROM book")
        s.commit()
        book = models.Book(
            isbn13="9780000000099", title="t", author="a",
            created_at=_BASE, updated_at=_BASE,
        )
        s.add(book); s.commit(); s.refresh(book)

        for o in offers:
            hours = sorted(o["hours_ago"], reverse=True)  # oldest first = canonical
            total = o["price"] + o["shipping"]
            canonical = models.PriceObservation(
                book_id=book.id, source=o["source"], condition=o["condition"],
                seller=o["seller"], price_minor=o["price"], currency="GBP",
                shipping_minor=o["shipping"], total_minor=total, url="https://x",
                observed_at=_BASE - timedelta(hours=hours[0]), raw={},
            )
            s.add(canonical); s.commit(); s.refresh(canonical)
            for h in hours[1:]:  # later sightings are dups of the canonical
                s.add(models.PriceObservation(
                    book_id=book.id, source=o["source"], condition=o["condition"],
                    seller=o["seller"], price_minor=o["price"], currency="GBP",
                    shipping_minor=o["shipping"], total_minor=total, url="https://x",
                    observed_at=_BASE - timedelta(hours=h), raw={},
                    is_duplicate_of=canonical.id,
                ))
        s.commit()

        stats = compute_book_stats(book.id, s)

    expected = _reference_current_best(offers)
    if expected is None:
        assert stats.current_best_total_minor is None
    else:
        total, source, condition, seller = expected
        assert stats.current_best_total_minor == total
        assert stats.current_best_source == source
        assert stats.current_best_condition == condition
        assert stats.current_best_seller == seller
