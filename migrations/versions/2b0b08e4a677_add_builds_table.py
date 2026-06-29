"""add builds table

Revision ID: 2b0b08e4a677
Revises: c97fa5bbc7cf
Create Date: 2026-02-24 10:48:06.361711

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2b0b08e4a677"
down_revision: Union[str, Sequence[str], None] = "c97fa5bbc7cf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ONLY add the builds table. Do not touch any existing tables.
    op.create_table(
        "builds",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("build_id", sa.String(), nullable=False),
        sa.Column("contact_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("business_name", sa.String(), nullable=True),
        sa.Column("business_type", sa.String(), nullable=False),
        sa.Column("lead_volume_tier", sa.String(), nullable=False),
        sa.Column(
            "stripe_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("package_tier", sa.String(), nullable=False),
        sa.Column("total_price_cents", sa.Integer(), nullable=False),
        sa.Column("timeline_days", sa.Integer(), nullable=False),
        sa.Column(
            "deposit_amount_cents",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("50000"),
        ),
        sa.Column(
            "deposit_paid", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("deposit_payment_intent_id", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'CONFIGURED'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("build_id", name="uq_builds_build_id"),
    )
    op.create_index("ix_builds_build_id", "builds", ["build_id"], unique=True)
    op.create_index("ix_builds_email", "builds", ["email"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_builds_email", table_name="builds")
    op.drop_index("ix_builds_build_id", table_name="builds")
    op.drop_table("builds")
