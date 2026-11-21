"""indexes for the review queue and recent activity; missing space.default_shift_id FK

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21 12:00:00

- ix_event_pending: the pending-review count and queue read only pending events, instead of
  every event the space ever had.
- ix_event_space_received: the dashboard's latest-received list.
- 0001 declared the space -> shift foreign key with use_alter, which op.create_table ignores,
  so it was never created.

The indexes are built CONCURRENTLY: kiosks keep uploading while a big table is indexed.
"""
import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_event_pending", ["space_id", "occurred_at"], sa.text("status = 'pending'")),
    ("ix_event_space_received", ["space_id", "received_at"], None),
]


def upgrade() -> None:
    op.execute("UPDATE space SET default_shift_id = NULL WHERE default_shift_id IS NOT NULL "
               "AND NOT EXISTS (SELECT 1 FROM shift WHERE shift.id = space.default_shift_id)")
    op.create_foreign_key("space_default_shift_id_fkey", "space", "shift",
                          ["default_shift_id"], ["id"], ondelete="SET NULL")
    with op.get_context().autocommit_block():
        for name, cols, where in INDEXES:
            # A concurrent build that was interrupted leaves an INVALID index: rebuild it.
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.create_index(name, "attendance_event", cols, postgresql_concurrently=True,
                            postgresql_where=where)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _, _ in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.drop_constraint("space_default_shift_id_fkey", "space", type_="foreignkey")
