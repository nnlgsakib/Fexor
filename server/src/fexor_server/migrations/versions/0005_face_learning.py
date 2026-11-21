"""learned face templates: face_template.source, space.auto_update_faces

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21 23:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing templates all came from photos an admin added.
    op.add_column('face_template', sa.Column('source', sa.String(10), nullable=False,
                                             server_default='enrolled'))
    op.add_column('space', sa.Column('auto_update_faces', sa.Boolean(), nullable=False,
                                     server_default=sa.true()))


def downgrade() -> None:
    op.execute("DELETE FROM face_template WHERE source = 'auto'")
    op.drop_column('space', 'auto_update_faces')
    op.drop_column('face_template', 'source')
