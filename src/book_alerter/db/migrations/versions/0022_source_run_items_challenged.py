"""source_run_items_challenged — count bot-challenged items per run.

Plan task T1.3 (2026-09-04 review-and-optimisation plan), Tier 4.

`SourceRun` already records `books_attempted` / `books_succeeded`, but a run
where every item was turned away by a bot challenge is indistinguishable from
one where every item legitimately had no offers: both are "attempted N,
succeeded 0". Two consumers need that distinction and neither can currently
get it:

- `Scheduler._apply_backoff` today only counts run-LEVEL exceptions as
  consecutive errors. A run in which Amazon challenged all 12 items finishes
  "successfully" with zero observations, so backoff never engages and the next
  run walks into the same wall. T1.3 makes a run with >=50% of its attempted
  items challenged count as a consecutive error.
- The T6.1 dashboard banner currently derives "challenged" from failed
  attempts generally, which is documented in the UI as an over-broad proxy.
  This column is what makes that figure exact.

Backfill is 0 for every existing row, and the column is NOT NULL. Zero is the
honest value rather than a guess: runs that predate the counter never counted
challenges, and 0 already means "no evidence of a challenge" for this column.
NOT NULL matters because both consumers do arithmetic on it — a NULL would
read as "no challenges" through `>= 50%` and through the banner's sum alike,
which is exactly the silent-wrong-answer this column exists to remove.

Additive and reversible: the downgrade drops the column and loses only counts
recorded since the upgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_source_run_items_challenged"
down_revision = "0021_heartbeat_compaction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `server_default="0"` is what backfills the existing rows: SQLite cannot
    # add a NOT NULL column without one. It is deliberately left on the column
    # afterwards rather than dropped in a second step — a table rebuild just to
    # remove a default that says the same thing as the model's `default=0`
    # would be churn, and it keeps a raw INSERT that omits the column honest.
    with op.batch_alter_table("sourcerun") as batch_op:
        batch_op.add_column(
            sa.Column(
                "items_challenged",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("sourcerun") as batch_op:
        batch_op.drop_column("items_challenged")
