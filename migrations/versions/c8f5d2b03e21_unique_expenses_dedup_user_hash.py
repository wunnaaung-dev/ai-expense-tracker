"""unique expenses_dedup user_id and hash

Revision ID: c8f5d2b03e21
Revises: b7e4c1a92d10
Create Date: 2026-08-24 21:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8f5d2b03e21"
down_revision: Union[str, Sequence[str], None] = "b7e4c1a92d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "expenses_dedup",
        "user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "expenses_dedup",
        "hash",
        existing_type=sa.String(),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_expenses_dedup_user_hash",
        "expenses_dedup",
        ["user_id", "hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_expenses_dedup_user_hash",
        "expenses_dedup",
        type_="unique",
    )
    op.alter_column(
        "expenses_dedup",
        "hash",
        existing_type=sa.String(),
        nullable=True,
    )
    op.alter_column(
        "expenses_dedup",
        "user_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
