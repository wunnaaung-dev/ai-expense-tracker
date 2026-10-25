"""add users.last_currency

Revision ID: d4e8a1c95f20
Revises: a3c9e1f47b52
Create Date: 2026-10-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e8a1c95f20"
down_revision: Union[str, Sequence[str], None] = "a3c9e1f47b52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_currency", sa.String(), nullable=False, server_default="THB"),
    )
    op.create_check_constraint(
        "ck_users_last_currency",
        "users",
        "last_currency IN ('THB', 'MMK', 'USD')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_last_currency", "users", type_="check")
    op.drop_column("users", "last_currency")
