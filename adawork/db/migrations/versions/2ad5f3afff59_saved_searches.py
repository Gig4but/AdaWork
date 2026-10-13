"""saved searches

Revision ID: 2ad5f3afff59
Revises: d7f1b3c5e9a2
Create Date: 2026-10-13 18:10:06
"""

import sqlalchemy as sa
from alembic import op

revision = "2ad5f3afff59"
down_revision = "d7f1b3c5e9a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("query", sa.String(), nullable=False),
        sa.Column("notify", sa.Boolean(), nullable=False),
        sa.Column("seen_up_to", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("saved_searches")
