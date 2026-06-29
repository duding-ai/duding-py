"""set builds server defaults

Revision ID: 419fa8b60712
Revises: 319409a0f9f4
Create Date: 2026-02-24

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "419fa8b60712"
down_revision = "319409a0f9f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("builds") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(),
            nullable=False,
            server_default="CONFIGURED",
        )

        batch.alter_column(
            "deposit_amount_cents",
            existing_type=sa.Integer(),
            nullable=False,
            server_default="50000",
        )

        batch.alter_column(
            "deposit_paid",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        )

        batch.alter_column(
            "stripe_confirmed",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
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
