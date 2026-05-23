"""notif_delivery_polymorphic — NotificationDelivery rows can target a
book Alert OR a ProductAlert (exactly one).

Schema changes:
- `notificationdelivery.alert_id`        : NOT NULL → NULL (still FK to alert.id, still CASCADE)
- `notificationdelivery.product_alert_id`: NEW, nullable INT, FK to productalert.id CASCADE
- new CHECK constraint              : exactly one of the two FKs is set
- new index                         : product_alert_id

SQLite's `ALTER TABLE` is too weak for any of these in place — we use
`op.batch_alter_table` which rebuilds the table. The book_stats view does
not reference notificationdelivery, so no view drop is needed (unlike
migration 0013 which touched priceobservation).

Downgrade: removes product_alert_id + CHECK, restores alert_id to NOT NULL.
Downgrade will fail if any rows have alert_id IS NULL (i.e. product
deliveries that landed before the downgrade) — by design, since the data
genuinely doesn't fit the older schema.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_notif_delivery_polymorphic"
down_revision = "0014_product_tables"
branch_labels = None
depends_on = None

# Only the `fk` template — we deliberately omit `ck` because including it
# would cause `MetaData.reflect()` to DOUBLE-prefix on the downgrade side
# (`ck_notificationdelivery_<existing_name>`), defeating the lookup in
# `drop_constraint`. The fully-qualified CHECK name is the bare string
# below, set both at create-time and at drop-time.
_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

# `(alert_id IS NULL) <> (product_alert_id IS NULL)` is the XOR form. SQLite
# treats IS NULL as 0/1, so 1<>0 (one set, the other null) passes; 1<>1
# (both null) and 0<>0 (both set) fail.
_CHECK_EXPR = "(alert_id IS NULL) <> (product_alert_id IS NULL)"
_CHECK_NAME = "ck_notificationdelivery_alert_xor_product"


def upgrade() -> None:
    with op.batch_alter_table(
        "notificationdelivery",
        naming_convention=_NAMING,
    ) as batch_op:
        batch_op.alter_column("alert_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(
            sa.Column("product_alert_id", sa.Integer(), nullable=True),
        )
        batch_op.create_foreign_key(
            "fk_notificationdelivery_product_alert_id_productalert",
            "productalert",
            ["product_alert_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_notificationdelivery_product_alert_id",
            ["product_alert_id"],
        )
        batch_op.create_check_constraint(_CHECK_NAME, _CHECK_EXPR)


def downgrade() -> None:
    # Migration is destructive of polymorphic data — any row with
    # product_alert_id set and alert_id NULL cannot be expressed in the
    # post-downgrade schema. The downgrade will fail with an integrity
    # violation in that case, which is the safe behaviour.
    with op.batch_alter_table(
        "notificationdelivery",
        naming_convention=_NAMING,
    ) as batch_op:
        batch_op.drop_constraint(_CHECK_NAME, type_="check")
        batch_op.drop_index("ix_notificationdelivery_product_alert_id")
        batch_op.drop_constraint(
            "fk_notificationdelivery_product_alert_id_productalert",
            type_="foreignkey",
        )
        batch_op.drop_column("product_alert_id")
        batch_op.alter_column("alert_id", existing_type=sa.Integer(), nullable=False)
