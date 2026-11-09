"""kiosk unlock PIN

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09 22:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Salted hash of the kiosk's unlock PIN (the PIN itself is never stored).
    op.add_column('device', sa.Column('unlock_pin', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('device', 'unlock_pin')
