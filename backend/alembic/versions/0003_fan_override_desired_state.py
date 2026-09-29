"""fan_overrides.desired_state

Revision ID: 0003_fan_override_desired_state
Revises: 0002_fan_overrides
Create Date: 2026-09-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_fan_override_desired_state"
down_revision: Union[str, None] = "0002_fan_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fan_overrides",
        sa.Column(
            "desired_state",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("fan_overrides", "desired_state")
