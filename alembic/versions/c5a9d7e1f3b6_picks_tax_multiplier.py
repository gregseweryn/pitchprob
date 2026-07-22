"""picks.tax_multiplier — the exact payout regime per bet (ADR 0017)

Betclic's "Bez Podatku 2.0" has three payout regimes (x1.0 inside the
1,000 PLN limit, x0.94 past it for singles, x0.88 with no promo), and a
boolean cannot say which one a pick was priced under. The column records
the multiplier that produced ``price_effective``, so clv_exec stays
auditable; ``tax_free`` survives as compatible sugar (= multiplier 1.0).

Backfill: rows written before this migration knew only two regimes, so
tax_free maps exactly — 1.00 where set, the server default 0.88 otherwise.

Revision ID: c5a9d7e1f3b6
Revises: b7d3f1a9c2e4
Create Date: 2026-07-22 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c5a9d7e1f3b6'
down_revision: Union[str, None] = 'b7d3f1a9c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'picks',
        sa.Column(
            'tax_multiplier',
            sa.Numeric(4, 2),
            nullable=False,
            server_default='0.88',
        ),
    )
    op.execute("UPDATE picks SET tax_multiplier = 1.00 WHERE tax_free")


def downgrade() -> None:
    op.drop_column('picks', 'tax_multiplier')
