"""add business_settings and blueprint

Revision ID: c97fa5bbc7cf
Revises: f50e806496e2
Create Date: 2025-12-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON

# revision identifiers, used by Alembic.
revision = "c97fa5bbc7cf"
down_revision = "f50e806496e2"  # keep whatever your previous revision id was
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1) business_settings table ---
    op.create_table(
        "business_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_name", sa.String(), nullable=False),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("logo_url", sa.String(), nullable=True),
        sa.Column("website_url", sa.String(), nullable=True),
        sa.Column("email_from_name", sa.String(), nullable=True),
        sa.Column("email_from_address", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # --- 2) blueprints table ---
    op.create_table(
        "blueprints",
        sa.Column("id", sa.String(), primary_key=True),  # UUID-as-string
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("business_name", sa.String(), nullable=False),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("leads_per_week", sa.Integer(), nullable=True),
        sa.Column("follow_up_type", sa.String(), nullable=True),
        sa.Column("workflow_rating", sa.Integer(), nullable=True),
        sa.Column("brand_rating", sa.Integer(), nullable=True),
        sa.Column("posts_last_week", sa.Integer(), nullable=True),
        sa.Column("ads_running", sa.Boolean(), nullable=True),
        sa.Column("scores_json", SQLiteJSON(), nullable=True),
        sa.Column("loss_estimation", sa.String(), nullable=True),
        sa.Column("summary_json", SQLiteJSON(), nullable=True),
        sa.Column("recommendation_text", sa.Text(), nullable=True),
        sa.Column("pdf_path", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("blueprints")
    op.drop_table("business_settings")
