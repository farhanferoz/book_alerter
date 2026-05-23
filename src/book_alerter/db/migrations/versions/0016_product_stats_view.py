"""product_stats_view — install the product_stats SQL view.

Mirror of book_stats (migration 0004 + the in-place edits 0005..0011), with
all references to `book`/`priceobservation`/`isbn13`/`book_id` swapped to
their product equivalents. Kept as a separate migration (not folded into
0014) so the view can be dropped + recreated independently if a future
migration restructures `productobservation`.
"""

from alembic import op

from book_alerter.db.views import (
    DROP_PRODUCT_STATS_VIEW_SQL,
    PRODUCT_STATS_VIEW_SQL,
)

revision = "0016_product_stats_view"
down_revision = "0015_notif_delivery_polymorphic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(PRODUCT_STATS_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_PRODUCT_STATS_VIEW_SQL)
