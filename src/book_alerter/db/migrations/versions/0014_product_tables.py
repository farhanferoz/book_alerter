"""product_tables — add Product, ProductObservation, ProductAlert, ProductSignalState.

Mirror of the book stack created in migrations 0001..0003 + 0012 (scrape
health), with two product-specific additions: `product.track_used` (per-row
opt-in to track used grades) and `product.brand` (subtitle for the
dashboard, where books use `author`).

All child→parent FKs are created with ON DELETE CASCADE from day one (matches
the books-side end state after migration 0013). PRAGMA foreign_keys=ON in
`db/session.py` enforces them.

`is_duplicate_of` on `productobservation` is intentionally NO ACTION — it's
a peer relationship between observations (soft "this row dups X"), so
deleting the parent must not cascade-delete every observation marked as its
duplicate. Same rationale as the books side.

Downgrade drops the tables in reverse FK order: productsignalstate,
productalert, productobservation, product. The notificationdelivery side
of the polymorphism lands in migration 0015 so this migration stays a pure
table add.
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0014_product_tables"
down_revision = "0013_fk_cascade_on_book_delete"
branch_labels = None
depends_on = None

_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def upgrade() -> None:
    op.create_table(
        "product",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asin", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("image_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("brand", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("region", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("currency", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target_price_minor", sa.Integer(), nullable=True),
        sa.Column("percentile_threshold", sa.Integer(), nullable=True),
        sa.Column("percentile_window_days", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("bought_price_minor", sa.Integer(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("alert_kinds_disabled", sa.JSON(), nullable=True),
        sa.Column("muted_until", sa.DateTime(), nullable=True),
        sa.Column("track_used", sa.Boolean(), nullable=False),
        sa.Column("last_scrape_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_scrape_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_asin", "product", ["asin"], unique=True)

    op.create_table(
        "productobservation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("seller", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("condition", sa.String(), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("shipping_minor", sa.Integer(), nullable=True),
        sa.Column("total_minor", sa.Integer(), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("is_duplicate_of", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"],
            name="fk_productobservation_product_id_product",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["is_duplicate_of"], ["productobservation.id"],
            name="fk_productobservation_is_duplicate_of_productobservation",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_productobservation_product_id", "productobservation", ["product_id"])
    op.create_index("ix_productobservation_observed_at", "productobservation", ["observed_at"])
    op.create_index(
        "ix_pobs_product_observed", "productobservation", ["product_id", "observed_at"]
    )
    op.create_index(
        "ix_pobs_product_source_observed",
        "productobservation",
        ["product_id", "source", "observed_at"],
    )

    op.create_table(
        "productalert",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("condition", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("fired_at", sa.DateTime(), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("delivered_via", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"],
            name="fk_productalert_product_id_product",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_productalert_product_id", "productalert", ["product_id"])
    op.create_index("ix_productalert_fired_at", "productalert", ["fired_at"])

    op.create_table(
        "productsignalstate",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("last_signal", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("last_all_time_min_total_minor", sa.Integer(), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"],
            name="fk_productsignalstate_product_id_product",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("product_id"),
    )


def downgrade() -> None:
    op.drop_table("productsignalstate")
    op.drop_index("ix_productalert_fired_at", table_name="productalert")
    op.drop_index("ix_productalert_product_id", table_name="productalert")
    op.drop_table("productalert")
    op.drop_index("ix_pobs_product_source_observed", table_name="productobservation")
    op.drop_index("ix_pobs_product_observed", table_name="productobservation")
    op.drop_index("ix_productobservation_observed_at", table_name="productobservation")
    op.drop_index("ix_productobservation_product_id", table_name="productobservation")
    op.drop_table("productobservation")
    op.drop_index("ix_product_asin", table_name="product")
    op.drop_table("product")
