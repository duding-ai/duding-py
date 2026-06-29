"""fix builds status defaults

Revision ID: 319409a0f9f4
Revises: 2b0b08e4a677
Create Date: 2026-02-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "319409a0f9f4"
down_revision: Union[str, Sequence[str], None] = "2b0b08e4a677"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite can't ALTER COLUMN directly. batch_alter_table recreates the table safely.
    with op.batch_alter_table("builds") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(),
            nullable=False,
            server_default=text("'CONFIGURED'"),
        )
        batch.alter_column(
            "deposit_amount_cents",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=text("50000"),
        )
        batch.alter_column(
            "deposit_paid",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=text("0"),
        )
        batch.alter_column(
            "stripe_confirmed",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=text("0"),
        )


def downgrade() -> None:
    with op.batch_alter_table("builds") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(),
            nullable=False,
            server_default=None,
        )
        batch.alter_column(
            "deposit_amount_cents",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=None,
        )
        batch.alter_column(
            "deposit_paid",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=None,
        )
        batch.alter_column(
            "stripe_confirmed",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=None,
        )
