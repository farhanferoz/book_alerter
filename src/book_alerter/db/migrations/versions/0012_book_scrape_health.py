"""book: last_scrape_attempt_at + last_scrape_error per-book health columns

Surfaces silent scrape failures to the dashboard so a 24/7 deployment
doesn't quietly stop pricing a book. last_scrape_error is cleared on
the next successful attempt; whichever source finishes last wins
(simple last-write-wins is enough — the FE only needs "is something
broken right now").
"""
import sqlalchemy as sa
from alembic import op


revision = "0012_book_scrape_health"
down_revision = "0011_book_stats_drop_all_time_min_max"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("book") as batch:
        batch.add_column(sa.Column("last_scrape_attempt_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_scrape_error", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("book") as batch:
        batch.drop_column("last_scrape_error")
        batch.drop_column("last_scrape_attempt_at")
