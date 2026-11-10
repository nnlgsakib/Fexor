"""shift check-in / check-out windows, 12/24-hour time format

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('shift', sa.Column('checkin_close_minutes', sa.Integer(), nullable=True))
    op.add_column('shift', sa.Column('checkout_open_minutes', sa.Integer(), nullable=True))
    op.add_column('space', sa.Column('time_format', sa.String(3), nullable=False,
                                     server_default='24h'))


def downgrade() -> None:
    op.drop_column('space', 'time_format')
    op.drop_column('shift', 'checkout_open_minutes')
    op.drop_column('shift', 'checkin_close_minutes')
