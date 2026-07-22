"""widen odds_ticks.sport_key to 96 for odds-api.io league slugs

The odds-api.io feed (ADR 0016 poller) names leagues with long slugs —
"international-clubs-uefa-champions-league-women-qualification" is 61 chars —
which overflow the original String(48) and abort the whole sweep. The tape
records what the source said, so the column widens rather than truncating.

Revision ID: b7d3f1a9c2e4
Revises: f2a4c6e8b0d1
Create Date: 2026-07-22 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d3f1a9c2e4'
down_revision: Union[str, None] = 'f2a4c6e8b0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('odds_ticks') as batch:
        batch.alter_column(
            'sport_key',
            existing_type=sa.String(length=48),
            type_=sa.String(length=96),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('odds_ticks') as batch:
        batch.alter_column(
            'sport_key',
            existing_type=sa.String(length=96),
            type_=sa.String(length=48),
            existing_nullable=False,
        )
