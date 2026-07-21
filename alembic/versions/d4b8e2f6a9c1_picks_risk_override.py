"""picks.risk_override / risk_note — the Phase 3 breaker's audit trail

The drawdown circuit breaker and the exposure limits can be bypassed, but
only explicitly and only on the record: `risk_override` marks the bet and
`risk_note` names the limits that were breached at the time (ADR 0015).
The weekly report reads these columns back, so an override cannot quietly
disappear from the season's story.

Revision ID: d4b8e2f6a9c1
Revises: a7f2b4c6d8e0
Create Date: 2026-07-21 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4b8e2f6a9c1'
down_revision: Union[str, None] = 'a7f2b4c6d8e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default so rows written before this migration read as "not an
    # override" rather than NULL — the report must never show a blank there.
    op.add_column(
        'picks',
        sa.Column(
            'risk_override',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'picks', sa.Column('risk_note', sa.String(length=256), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('picks', 'risk_note')
    op.drop_column('picks', 'risk_override')
