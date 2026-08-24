"""tighten user_storage_config constraints

Revision ID: e1b9c4a76d30
Revises: c8f5d2b03e21
Create Date: 2026-08-24 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1b9c4a76d30"
down_revision: Union[str, Sequence[str], None] = "c8f5d2b03e21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM user_storage_config
        WHERE user_id IS NULL
           OR provider IS NULL
           OR encrypted_credentials IS NULL
           OR target_id IS NULL
        """
    )
    op.execute(
        """
        DELETE FROM user_storage_config
        WHERE id NOT IN (
            SELECT DISTINCT ON (user_id, provider) id
            FROM user_storage_config
            ORDER BY user_id, provider, connected_at DESC
        )
        """
    )
    op.alter_column(
        "user_storage_config",
        "user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "user_storage_config",
        "provider",
        existing_type=sa.String(),
        nullable=False,
    )
    op.alter_column(
        "user_storage_config",
        "encrypted_credentials",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "user_storage_config",
        "target_id",
        existing_type=sa.String(),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_user_storage_config_user_provider",
        "user_storage_config",
        ["user_id", "provider"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_user_storage_config_user_provider",
        "user_storage_config",
        type_="unique",
    )
    op.alter_column(
        "user_storage_config",
        "target_id",
        existing_type=sa.String(),
        nullable=True,
    )
    op.alter_column(
        "user_storage_config",
        "encrypted_credentials",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "user_storage_config",
        "provider",
        existing_type=sa.String(),
        nullable=True,
    )
    op.alter_column(
        "user_storage_config",
        "user_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
