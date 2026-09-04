"""Property test pinning migration 0023's `metadata_status` backfill formula
(plan task T4.1), a Tier 4 migration (D17: written and run against the raw
SQL before the migration file existed).

Every EXISTING product predates the title-optional add-product flow this
task introduces — the OLD `POST /api/products` required a real `title`, so
no pre-migration row could have been created with the literal placeholder
`f"Amazon product {asin}"` this feature generates. The backfill formula is
therefore general (it would correctly classify a hypothetical future row
that legitimately carries that placeholder, e.g. after a downgrade/upgrade
round-trip) even though on today's actual data every row resolves to "ok":

    metadata_status = 'pending' if title == 'Amazon product ' || asin
                       else 'ok'

This is the honest choice between the two the task names as arguable
("pending" vs "ok" for existing rows): a title that isn't the auto-generated
placeholder is real metadata a source or a user already provided, so "ok" is
correct; a title that IS the exact placeholder for its own row's ASIN has
never had real metadata resolved, so "pending" is correct. Deliberately
independent of `book_alerter.db.models.Product` (a bare `CREATE TABLE` with
just the three columns the formula touches) rather than `engine_with_view`
— same rationale as `test_heartbeat_compaction_backfill_properties.py`: this
keeps proving the formula after the live schema evolves past what this
migration adds.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, text

_CREATE_TABLE_SQL = """
CREATE TABLE product (
    id INTEGER PRIMARY KEY,
    asin TEXT NOT NULL,
    title TEXT NOT NULL
)
"""

# Same UPDATE as migration 0023's _BACKFILL_PRODUCT_METADATA_STATUS_SQL —
# copied here, not imported (Alembic revision filenames start with a digit,
# not importable as a normal module; see the heartbeat-compaction property
# test for the same note).
_BACKFILL_SQL = """
UPDATE product
SET metadata_status = CASE
    WHEN title = 'Amazon product ' || asin THEN 'pending'
    ELSE 'ok'
END
"""

_asin_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_asin = st.text(alphabet=_asin_alphabet, min_size=10, max_size=10)
# Printable ASCII only, same determinism rationale as the heartbeat test's
# whole-hour offsets — avoids exotic-Unicode SQLite TEXT-collation edge
# cases that are not what this formula is about.
_other_title = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=0, max_size=40
)


@st.composite
def _product_row(draw: st.DrawFn) -> tuple[str, str, str]:
    asin = draw(_asin)
    placeholder = f"Amazon product {asin}"
    if draw(st.booleans()):
        return asin, placeholder, "pending"
    # Any other title, including one that happens to look like a
    # placeholder for a DIFFERENT asin — must still resolve "ok" because
    # the comparison is against THIS row's own asin, not any asin.
    title = draw(_other_title.filter(lambda t: t != placeholder))
    return asin, title, "ok"


@settings(max_examples=100, deadline=None)
@given(rows=st.lists(_product_row(), min_size=1, max_size=20))
def test_backfill_matches_placeholder_title_formula(
    rows: list[tuple[str, str, str]],
) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(_CREATE_TABLE_SQL)
        conn.exec_driver_sql("ALTER TABLE product ADD COLUMN metadata_status TEXT")

        next_id = 1
        expected_by_id: dict[int, str] = {}
        for asin, title, expected in rows:
            conn.execute(
                text("INSERT INTO product (id, asin, title) VALUES (:id, :asin, :title)"),
                {"id": next_id, "asin": asin, "title": title},
            )
            expected_by_id[next_id] = expected
            next_id += 1

        conn.execute(text(_BACKFILL_SQL))
        got_rows = conn.execute(text("SELECT id, metadata_status FROM product")).all()

    assert len(got_rows) == len(rows)
    for pid, status in got_rows:
        assert status == expected_by_id[pid], (
            f"product id {pid}: backfilled {status!r}, expected {expected_by_id[pid]!r}"
        )
