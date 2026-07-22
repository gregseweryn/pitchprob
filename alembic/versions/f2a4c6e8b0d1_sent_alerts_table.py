"""sent_alerts table — dedup log for the speaking loop

The watch loop (ADR 0016) pushes at most one alert per fixture-selection-book
so a standing edge is announced once, not on every pass. This table is that
memory: read before sending, written after. Keyed for dedup on
(event_id, market, selection, line, bookmaker); verdict/edge/sent_at are kept
for the record and the weekly report.

Revision ID: f2a4c6e8b0d1
Revises: d4b8e2f6a9c1
Create Date: 2026-07-22 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2a4c6e8b0d1'
down_revision: Union[str, None] = 'd4b8e2f6a9c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sent_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.String(length=64), nullable=False),
        sa.Column('market', sa.String(length=16), nullable=False),
        sa.Column('selection', sa.String(length=16), nullable=False),
        sa.Column('line', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('bookmaker', sa.String(length=32), nullable=False),
        sa.Column('verdict', sa.String(length=16), nullable=False),
        sa.Column('edge', sa.Float(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_sent_alerts_event_id'), 'sent_alerts', ['event_id']
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_sent_alerts_event_id'), table_name='sent_alerts')
    op.drop_table('sent_alerts')
