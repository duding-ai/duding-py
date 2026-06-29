"""add outreach tables

Revision ID: b3e9f1a2c847
Revises: 419fa8b60712
Create Date: 2026-06-28

"""

from alembic import op
import sqlalchemy as sa

revision = "b3e9f1a2c847"
down_revision = "419fa8b60712"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "outreach_prospects" not in tables:
        op.create_table(
            "outreach_prospects",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source_input", sa.String(), nullable=True),
            sa.Column("source_url", sa.String(), nullable=True),
            sa.Column("business_name", sa.String(), nullable=True),
            sa.Column("contact_name", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("website", sa.String(), nullable=True),
            sa.Column("business_description", sa.Text(), nullable=True),
            sa.Column("lever", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="outreach_pending"),
            sa.Column("follow_up_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_email_subject", sa.String(), nullable=True),
            sa.Column("last_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)")),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_outreach_prospects_email", "outreach_prospects", ["email"])
        op.create_index("ix_outreach_prospects_id", "outreach_prospects", ["id"])
    else:
        existing_cols = {col["name"] for col in inspector.get_columns("outreach_prospects")}
        if "follow_up_count" not in existing_cols:
            op.add_column(
                "outreach_prospects",
                sa.Column("follow_up_count", sa.Integer(), nullable=False, server_default="0"),
            )

    if "outreach_activities" not in tables:
        op.create_table(
            "outreach_activities",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("prospect_id", sa.Integer(), nullable=False),
            sa.Column("activity_type", sa.String(), nullable=False),
            sa.Column("subject", sa.String(), nullable=True),
            sa.Column("body_preview", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)")),
            sa.ForeignKeyConstraint(["prospect_id"], ["outreach_prospects.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_outreach_activities_id", "outreach_activities", ["id"])


def downgrade() -> None:
    op.drop_table("outreach_activities")
    op.drop_table("outreach_prospects")
