"""picks table — the forward real-money CLV ledger (Phase 5 part 3)

One row per real bet the operator places at a Polish book: executed price
(quoted and effective under the 12% turnover tax / tax-free promo), the
Pinnacle anchor at bet time, realized settlement, and the two-label CLV
decomposition vs the tape's closing fair (ADR 0011 applied per bet).

Revision ID: e3a5c7d9f1b2
Revises: c8d1f3a5e7b9
Create Date: 2026-07-21 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e3a5c7d9f1b2'
down_revision: Union[str, None] = 'c8d1f3a5e7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'picks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_id', sa.String(length=64), nullable=True),
        sa.Column('home_team', sa.String(length=64), nullable=False),
        sa.Column('away_team', sa.String(length=64), nullable=False),
        sa.Column('kickoff_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('market', sa.String(length=16), nullable=False),
        sa.Column('selection', sa.String(length=16), nullable=False),
        sa.Column('line', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('bookmaker', sa.String(length=32), nullable=False),
        sa.Column('stake_pln', sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column('price_quoted', sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column('tax_free', sa.Boolean(), nullable=False),
        sa.Column('price_effective', sa.Numeric(precision=9, scale=5), nullable=False),
        sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('price_sharp', sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column('sharp_observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ft_home', sa.Integer(), nullable=True),
        sa.Column('ft_away', sa.Integer(), nullable=True),
        sa.Column('gross_return_pln', sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closing_fair_prob', sa.Float(), nullable=True),
        sa.Column('closing_observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('clv_exec', sa.Float(), nullable=True),
        sa.Column('clv_sharp', sa.Float(), nullable=True),
        sa.Column('notes', sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_picks_event_id'), 'picks', ['event_id'], unique=False)
    op.create_index(op.f('ix_picks_kickoff_utc'), 'picks', ['kickoff_utc'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_picks_kickoff_utc'), table_name='picks')
    op.drop_index(op.f('ix_picks_event_id'), table_name='picks')
    op.drop_table('picks')
