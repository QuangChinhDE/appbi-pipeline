"""Production core invariants: forced password change, and one run per pipeline.

Three changes, all of them things the application was asking the database to
promise while nothing enforced it.

1. `users.password_change_required` — an account created by the bootstrap
   one-time secret must not stay usable on that secret.

2. `uq_run_idempotency_key` — a partial unique index on
   `(workspace_id, idempotency_key)`. The API read-then-wrote, which is safe
   with one replica and wrong with two: production runs `replicas: 2`, so two
   concurrent requests with the same key both saw "no existing run" and both
   inserted.

3. `uq_pipeline_active_run` — a partial unique index over the active statuses,
   so a pipeline cannot have two runs in flight. Same race, worse consequence:
   two Airbyte jobs writing the same destination.

Both indexes are partial. A plain unique index would forbid more than one
finished run per pipeline, and NULL idempotency keys would collide under some
engines; `WHERE` keeps the constraint to exactly the case that must be unique.

Revision ID: d4a1f07c2b18
Revises: c82c6e3a8fb7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d4a1f07c2b18"
down_revision: str | None = "c82c6e3a8fb7"
branch_labels = None
depends_on = None

# The statuses that mean "this run is still going". Written out rather than
# imported from the application so the migration keeps meaning what it meant on
# the day it ran, even after the enum grows.
ACTIVE_STATUSES = ("QUEUED", "STARTING", "RUNNING", "CANCEL_REQUESTED")


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_change_required", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )

    # Existing data may already violate these. Failing here is correct: the
    # alternative is an index that silently does not exist on the one
    # deployment that needed it.
    statuses = ", ".join(f"'{status}'" for status in ACTIVE_STATUSES)
    op.execute(
        "CREATE UNIQUE INDEX uq_run_idempotency_key "
        "ON pipeline_runs (workspace_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_pipeline_active_run "
        "ON pipeline_runs (pipeline_id) "
        f"WHERE status IN ({statuses})"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_pipeline_active_run")
    op.execute("DROP INDEX IF EXISTS uq_run_idempotency_key")
    op.drop_column("users", "password_change_required")
