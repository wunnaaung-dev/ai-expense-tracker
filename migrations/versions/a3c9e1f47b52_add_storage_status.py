"""add storage_status to user_storage_config

Revision ID: a3c9e1f47b52
Revises: f2a7d3e58c41
Create Date: 2026-10-19 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3c9e1f47b52"
down_revision: Union[str, Sequence[str], None] = "f2a7d3e58c41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_storage_config",
        sa.Column("storage_status", sa.String(), nullable=False, server_default="none"),
    )
    op.add_column(
        "user_storage_config",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing rows with a provisioned target (Notion databases) are already usable.
    op.execute("UPDATE user_storage_config SET storage_status = 'ready' WHERE target_id IS NOT NULL")
    op.create_check_constraint(
        "ck_user_storage_config_status",
        "user_storage_config",
        "storage_status IN ('none', 'provisioning', 'ready', 'failed', "
        "'needs_permission', 'schema_broken', 'missing')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_storage_config_status", "user_storage_config", type_="check")
    op.drop_column("user_storage_config", "status_changed_at")
    op.drop_column("user_storage_config", "storage_status")
