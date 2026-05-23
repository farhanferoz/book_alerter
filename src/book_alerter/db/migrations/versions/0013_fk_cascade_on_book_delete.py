"""fk_cascade_on_book_delete — add ON DELETE CASCADE to book/alert FKs.

Moves the hand-rolled cascade in `api/books.delete_book` (delete the four
child tables before delete-cascading the book) into the schema, where
SQLite enforces it once `PRAGMA foreign_keys=ON` is set per-connection
(see `db/session.py`).

Affected FKs:
- priceobservation.book_id      -> book.id    CASCADE
- alert.book_id                 -> book.id    CASCADE
- notificationdelivery.alert_id -> alert.id   CASCADE
- booksignalstate.book_id       -> book.id    CASCADE

Unaffected (kept as NO ACTION):
- priceobservation.is_duplicate_of -> priceobservation.id
  Soft "this row dups X" is a peer relationship between observations;
  deleting the parent must NOT cascade-delete every observation marked
  as its duplicate.

Audited 2026-05-23 against the live DB: 0 orphan rows in every child
table, so the table recreates copy data cleanly.

Implementation: SQLite cannot ALTER an FK in place; the standard idiom
is to recreate the table. Each `batch_alter_table` block runs that
dance — Alembic uses `naming_convention` to give the unnamed reflected
FK a deterministic name so `drop_constraint` can target it. The new
FK is created with `ondelete="CASCADE"`.
"""

import sqlalchemy as sa  # noqa: F401  (Alembic templates expect this import)
from alembic import op

from book_alerter.db.views import BOOK_STATS_VIEW_SQL, DROP_BOOK_STATS_VIEW_SQL

revision = "0013_fk_cascade_on_book_delete"
down_revision = "0012_book_scrape_health"
branch_labels = None
depends_on = None

_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def upgrade() -> None:
    # `book_stats` references priceobservation; SQLite refuses the
    # batch-table-rename dance while a dependent view exists. Drop it,
    # do the schema swap, then recreate it from the canonical DDL.
    op.execute(DROP_BOOK_STATS_VIEW_SQL)

    with op.batch_alter_table("alert", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_alert_book_id_book", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_alert_book_id_book", "book", ["book_id"], ["id"], ondelete="CASCADE",
        )

    with op.batch_alter_table("booksignalstate", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_booksignalstate_book_id_book", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_booksignalstate_book_id_book", "book", ["book_id"], ["id"], ondelete="CASCADE",
        )

    with op.batch_alter_table("notificationdelivery", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_notificationdelivery_alert_id_alert", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_notificationdelivery_alert_id_alert", "alert", ["alert_id"], ["id"], ondelete="CASCADE",
        )

    with op.batch_alter_table("priceobservation", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_priceobservation_book_id_book", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_priceobservation_book_id_book", "book", ["book_id"], ["id"], ondelete="CASCADE",
        )

    op.execute(BOOK_STATS_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_BOOK_STATS_VIEW_SQL)

    with op.batch_alter_table("priceobservation", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_priceobservation_book_id_book", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_priceobservation_book_id_book", "book", ["book_id"], ["id"],
        )

    with op.batch_alter_table("notificationdelivery", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_notificationdelivery_alert_id_alert", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_notificationdelivery_alert_id_alert", "alert", ["alert_id"], ["id"],
        )

    with op.batch_alter_table("booksignalstate", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_booksignalstate_book_id_book", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_booksignalstate_book_id_book", "book", ["book_id"], ["id"],
        )

    with op.batch_alter_table("alert", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_alert_book_id_book", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_alert_book_id_book", "book", ["book_id"], ["id"],
        )

    op.execute(BOOK_STATS_VIEW_SQL)
