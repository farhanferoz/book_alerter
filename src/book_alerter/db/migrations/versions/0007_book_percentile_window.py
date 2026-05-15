"""book.percentile_window_days per-book override"""
from alembic import op
import sqlalchemy as sa


revision = "0007_book_percentile_window"
down_revision = "0006_buyable_current_best"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("book") as batch_op:
        batch_op.add_column(
            sa.Column("percentile_window_days", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("book") as batch_op:
        batch_op.drop_column("percentile_window_days")
