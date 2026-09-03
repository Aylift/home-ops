"""initial schema: nodes, telemetry, events

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "telemetry",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("humidity", sa.Float(), nullable=True),
        sa.Column("pressure", sa.Float(), nullable=True),
        sa.Column("ah_inside", sa.Float(), nullable=True),
        sa.Column("ah_outside", sa.Float(), nullable=True),
        sa.Column("fan_active", sa.Boolean(), nullable=True),
        sa.Column("mode", sa.String(64), nullable=True),
        sa.Column("action", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_telemetry_node_timestamp", "telemetry", ["node_id", "timestamp"]
    )
    op.create_index("ix_telemetry_timestamp", "telemetry", ["timestamp"])

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("message", sa.String(256), nullable=True),
    )
    op.create_index("ix_events_node_timestamp", "events", ["node_id", "timestamp"])

    op.execute(
        sa.text(
            "INSERT INTO nodes (node_id, name, enabled) VALUES ('basement', 'Basement', true)"
        )
    )


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("telemetry")
    op.drop_table("nodes")
