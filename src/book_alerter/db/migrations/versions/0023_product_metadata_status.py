"""product_metadata_status — track whether a product's Amazon title/image
lookup has resolved yet.

Plan task T4.1 (2026-09-04 review-and-optimisation plan): add-product must
never block on a live Amazon fetch (F7 — the Confirm button used to stay
disabled until `POST /api/metadata/asin-lookup` succeeded, and that endpoint
launches a browser and can 502 on a bot challenge). `ProductCreate.title`
becomes optional; a product created without one gets a placeholder title and
`metadata_status = "pending"`, resolved later by a background retry job or
by the product scraper's own first successful dp parse.

Adds three columns to `product`:
- `metadata_status TEXT NOT NULL` — `pending` / `ok` / `failed`
  (`book_alerter.enums.MetadataStatus`).
- `metadata_attempts INTEGER NOT NULL DEFAULT 0` — retry counter for the
  `metadata_refresh` scheduler job's exponential backoff (up to 6 attempts,
  then `failed`).
- `metadata_last_attempt_at DATETIME` — nullable; last time
  `metadata_refresh` tried this row, independent of
  `last_scrape_attempt_at` (which tracks PRICE-scrape health, a different
  concern this task deliberately does not conflate it with).

Backfill for `metadata_status` (the only column with a non-trivial
per-row value — `metadata_attempts` defaults to 0 and
`metadata_last_attempt_at` to NULL for every existing row, which needs no
formula): every product in this database predates the title-optional flow
this migration enables, so no pre-migration row COULD have been created
with the literal placeholder title (`f"Amazon product {asin}"`,
`api/products.py`'s `_DEFAULT_TITLE_TEMPLATE`) this feature generates —
the old `POST /api/products` required a real `title`. The chosen formula is
therefore general rather than a blanket guess:

    metadata_status = 'pending' if title == 'Amazon product ' || asin
                       else 'ok'

On today's actual data this resolves every existing row to `ok` (there are
real products in production whose metadata is genuinely fine — a blanket
`pending` would be dishonest about that and would immediately queue every
one of them into the retry job for no reason), while remaining correct for
the hypothetical case a downgrade-then-upgrade round-trip could reintroduce
a row that legitimately carries the placeholder. Pinned by a property test
(`tests/integration/test_product_metadata_status_backfill_properties.py`)
written and run against this exact SQL before this migration file existed
(D17, Tier 4).

No view touches `product` directly (only `productobservation`), so unlike
migration 0021 this needs no view drop/recreate dance.

Downgrade drops all three columns — documented, accepted loss of retry
state and status; a subsequent upgrade re-derives `metadata_status` from
the same formula, which is idempotent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_product_metadata_status"
down_revision = "0022_source_run_items_challenged"
branch_labels = None
depends_on = None

# Kept in sync with api/products.py's `_DEFAULT_TITLE_TEMPLATE.format(asin=asin)`
# ("Amazon product {asin}") and the property test's copy of this same SQL —
# see both for why this exact string, not imported (Alembic revision
# filenames start with a digit, not importable as a normal module).
_BACKFILL_PRODUCT_METADATA_STATUS_SQL = """
UPDATE product
SET metadata_status = CASE
    WHEN title = 'Amazon product ' || asin THEN 'pending'
    ELSE 'ok'
END
"""


def upgrade() -> None:
    op.add_column("product", sa.Column("metadata_status", sa.String(), nullable=True))
    op.add_column(
        "product",
        sa.Column("metadata_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "product", sa.Column("metadata_last_attempt_at", sa.DateTime(), nullable=True)
    )
    op.execute(_BACKFILL_PRODUCT_METADATA_STATUS_SQL)
    with op.batch_alter_table("product") as batch_op:
        batch_op.alter_column("metadata_status", existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("product") as batch_op:
        batch_op.drop_column("metadata_last_attempt_at")
        batch_op.drop_column("metadata_attempts")
        batch_op.drop_column("metadata_status")
