"""book_stats view"""
from alembic import op

from book_alerter.db.views import BOOK_STATS_VIEW_SQL, DROP_BOOK_STATS_VIEW_SQL

revision = "0004_book_stats_view"
down_revision = "242d0f24dcef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(BOOK_STATS_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)
