"""odds_ticks table — the live odds tape (ADR 0012)

Revision ID: b4c7e9d2a1f0
Revises: 96560fcb3148
Create Date: 2026-07-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4c7e9d2a1f0'
down_revision: Union[str, None] = '96560fcb3148'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('odds_ticks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('sport_key', sa.String(length=48), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('commence_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('home_team', sa.String(length=64), nullable=False),
    sa.Column('away_team', sa.String(length=64), nullable=False),
    sa.Column('bookmaker', sa.String(length=32), nullable=False),
    sa.Column('market', sa.String(length=16), nullable=False),
    sa.Column('selection', sa.String(length=16), nullable=False),
    sa.Column('line', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('price', sa.Numeric(precision=8, scale=3), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_odds_ticks_sport_key'), 'odds_ticks', ['sport_key'], unique=False)
    op.create_index(op.f('ix_odds_ticks_event_id'), 'odds_ticks', ['event_id'], unique=False)
    op.create_index(op.f('ix_odds_ticks_commence_time'), 'odds_ticks', ['commence_time'], unique=False)
    op.create_index(op.f('ix_odds_ticks_observed_at'), 'odds_ticks', ['observed_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_odds_ticks_observed_at'), table_name='odds_ticks')
    op.drop_index(op.f('ix_odds_ticks_commence_time'), table_name='odds_ticks')
    op.drop_index(op.f('ix_odds_ticks_event_id'), table_name='odds_ticks')
    op.drop_index(op.f('ix_odds_ticks_sport_key'), table_name='odds_ticks')
    op.drop_table('odds_ticks')
