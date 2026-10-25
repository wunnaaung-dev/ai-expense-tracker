"""user entry settings: default_currency, default_payment_channel, week_start

Revision ID: a9d2f6b14e70
Revises: d4e8a1c95f20
Create Date: 2026-10-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9d2f6b14e70"
down_revision: Union[str, Sequence[str], None] = "d4e8a1c95f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The currency is now a user setting, no longer overwritten on every save; keep each user's value.
    op.drop_constraint("ck_users_last_currency", "users", type_="check")
    op.alter_column("users", "last_currency", new_column_name="default_currency")
    op.create_check_constraint(
        "ck_users_default_currency",
        "users",
        "default_currency IN ('THB', 'MMK', 'USD')",
    )
    op.add_column(
        "users",
        sa.Column("default_payment_channel", sa.String(), nullable=False, server_default="Cash"),
    )
    op.create_check_constraint(
        "ck_users_default_payment_channel",
        "users",
        "default_payment_channel IN ('Cash', 'Bank')",
    )
    op.add_column(
        "users",
        sa.Column("week_start", sa.String(), nullable=False, server_default="monday"),
    )
    op.create_check_constraint(
        "ck_users_week_start",
        "users",
        "week_start IN ('monday', 'sunday')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_week_start", "users", type_="check")
    op.drop_column("users", "week_start")
    op.drop_constraint("ck_users_default_payment_channel", "users", type_="check")
    op.drop_column("users", "default_payment_channel")
    op.drop_constraint("ck_users_default_currency", "users", type_="check")
    op.alter_column("users", "default_currency", new_column_name="last_currency")
    op.create_check_constraint(
        "ck_users_last_currency",
        "users",
        "last_currency IN ('THB', 'MMK', 'USD')",
    )
