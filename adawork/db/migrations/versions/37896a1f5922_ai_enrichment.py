"""ai enrichment

Revision ID: 37896a1f5922
Revises: 8c1f4e2a9b7d
Create Date: 2026-10-06 21:36:49.448419
"""

import sqlalchemy as sa
from alembic import op

revision = "37896a1f5922"
down_revision = "8c1f4e2a9b7d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_cache",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=40), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.add_column("jobs", sa.Column("rules_hash", sa.String(length=64), nullable=True))
    op.add_column("jobs", sa.Column("ai_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "ai_hash")
    op.drop_column("jobs", "rules_hash")
    op.drop_table("llm_cache")
