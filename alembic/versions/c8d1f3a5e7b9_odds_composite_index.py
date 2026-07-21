"""composite index on odds(bookmaker, market, is_closing) — audit A7

The harness's odds-snapshot loader filters the ~1M-row ``odds`` table on
(bookmaker, market, is_closing) up to ~8 times per backtest run; only
``match_id`` was indexed, so Postgres full-scanned the table each time.

Revision ID: c8d1f3a5e7b9
Revises: b4c7e9d2a1f0
Create Date: 2026-07-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c8d1f3a5e7b9'
down_revision: Union[str, None] = 'b4c7e9d2a1f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_odds_bookmaker_market_is_closing',
        'odds',
        ['bookmaker', 'market', 'is_closing'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_odds_bookmaker_market_is_closing', table_name='odds')
