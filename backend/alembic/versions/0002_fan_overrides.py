"""fan_overrides table

Revision ID: 0002_fan_overrides
Revises: 0001_initial
Create Date: 2026-09-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_fan_overrides"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fan_overrides",
        sa.Column("node_id", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "telemetry",
        sa.Column("override_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telemetry", "override_seconds")
    op.drop_table("fan_overrides")
