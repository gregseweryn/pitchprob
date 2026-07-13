"""add xg columns to matches

Revision ID: 7c1a2e9d4b02
Revises: 39bcbd65b69a
Create Date: 2026-07-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '7c1a2e9d4b02'
down_revision: Union[str, None] = '39bcbd65b69a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('matches', sa.Column('xg_home', sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column('matches', sa.Column('xg_away', sa.Numeric(precision=5, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column('matches', 'xg_away')
    op.drop_column('matches', 'xg_home')
