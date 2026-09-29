"""fan_overrides.desired_state

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


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
