"""multi-provider users and default storage provider

Revision ID: f2a7d3e58c41
Revises: e1b9c4a76d30
Create Date: 2026-10-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a7d3e58c41"
down_revision: Union[str, Sequence[str], None] = "e1b9c4a76d30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users: new identity/profile columns
    op.add_column("users", sa.Column("notion_user_id", sa.String(), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.String(), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("default_storage_provider", sa.String(), nullable=True))
    op.create_unique_constraint("uq_users_notion_user_id", "users", ["notion_user_id"])

    # Existing users all logged in with Google; keep Notion for those already using it.
    op.execute(
        """
        UPDATE users SET default_storage_provider = CASE
            WHEN EXISTS (
                SELECT 1 FROM user_storage_config c
                WHERE c.user_id = users.id AND c.provider = 'notion'
            ) THEN 'notion'
            ELSE 'google_sheets'
        END
        """
    )

    op.alter_column("users", "default_storage_provider", existing_type=sa.String(), nullable=False)
    op.alter_column("users", "google_sub", existing_type=sa.String(), nullable=True)
    op.alter_column("users", "email", existing_type=sa.String(), nullable=True)
    op.create_check_constraint(
        "ck_users_has_identity",
        "users",
        "google_sub IS NOT NULL OR notion_user_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_users_default_storage_provider",
        "users",
        "default_storage_provider IN ('notion', 'google_sheets')",
    )

    # user_storage_config: default lives on users now; target_id NULL = not provisioned
    op.drop_column("user_storage_config", "is_active")
    op.alter_column("user_storage_config", "target_id", existing_type=sa.String(), nullable=True)
    op.create_check_constraint(
        "ck_user_storage_config_provider",
        "user_storage_config",
        "provider IN ('notion', 'google_sheets')",
    )

    # Cascade user deletes
    op.drop_constraint("user_storage_config_user_id_fkey", "user_storage_config", type_="foreignkey")
    op.create_foreign_key(
        "user_storage_config_user_id_fkey",
        "user_storage_config",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("expenses_dedup_user_id_fkey", "expenses_dedup", type_="foreignkey")
    op.create_foreign_key(
        "expenses_dedup_user_id_fkey",
        "expenses_dedup",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("expenses_dedup_user_id_fkey", "expenses_dedup", type_="foreignkey")
    op.create_foreign_key(
        "expenses_dedup_user_id_fkey", "expenses_dedup", "users", ["user_id"], ["id"]
    )
    op.drop_constraint("user_storage_config_user_id_fkey", "user_storage_config", type_="foreignkey")
    op.create_foreign_key(
        "user_storage_config_user_id_fkey", "user_storage_config", "users", ["user_id"], ["id"]
    )

    op.drop_constraint("ck_user_storage_config_provider", "user_storage_config", type_="check")
    # Fails if any config is still unprovisioned (target_id NULL).
    op.alter_column("user_storage_config", "target_id", existing_type=sa.String(), nullable=False)
    op.add_column(
        "user_storage_config",
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
    )

    op.drop_constraint("ck_users_default_storage_provider", "users", type_="check")
    op.drop_constraint("ck_users_has_identity", "users", type_="check")
    # Fails if Notion-only users (no google_sub / email) exist.
    op.alter_column("users", "email", existing_type=sa.String(), nullable=False)
    op.alter_column("users", "google_sub", existing_type=sa.String(), nullable=False)
    op.drop_constraint("uq_users_notion_user_id", "users", type_="unique")
    op.drop_column("users", "default_storage_provider")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "notion_user_id")
