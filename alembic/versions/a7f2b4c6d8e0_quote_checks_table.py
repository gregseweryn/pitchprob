"""quote_checks table — operator ground truth for the odds-api.io feed

The audit (Etap 5) makes wiring an automatic PL-quote feed into the scanner
conditional on two to three weeks of manual validation. This table records
each comparison: what the operator saw on the bookmaker's site vs what the
feed claimed at that instant, with a NULL feed price meaning "the feed did
not have it" — a distinct, reportable failure mode (ADR 0014).

Revision ID: a7f2b4c6d8e0
Revises: e3a5c7d9f1b2
Create Date: 2026-07-21 11:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7f2b4c6d8e0'
down_revision: Union[str, None] = 'e3a5c7d9f1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'quote_checks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('bookmaker', sa.String(length=32), nullable=False),
        sa.Column('event_id', sa.String(length=64), nullable=True),
        sa.Column('home_team', sa.String(length=64), nullable=False),
        sa.Column('away_team', sa.String(length=64), nullable=False),
        sa.Column('market', sa.String(length=16), nullable=False),
        sa.Column('selection', sa.String(length=16), nullable=False),
        sa.Column('line', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('price_seen', sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column('price_feed', sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column('feed_observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_quote_checks_checked_at'), 'quote_checks', ['checked_at']
    )
    op.create_index(
        op.f('ix_quote_checks_bookmaker'), 'quote_checks', ['bookmaker']
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_quote_checks_bookmaker'), table_name='quote_checks')
    op.drop_index(op.f('ix_quote_checks_checked_at'), table_name='quote_checks')
    op.drop_table('quote_checks')
