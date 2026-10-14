"""travel times; an index for counting hidden ads

Revision ID: 1592b84d5c9c
Revises: 2ad5f3afff59
Create Date: 2026-10-13 20:19:37
"""

import sqlalchemy as sa
from alembic import op

revision = "1592b84d5c9c"
down_revision = "2ad5f3afff59"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "travel_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=24), nullable=False),
        sa.Column("depart", sa.String(length=5), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("origin", "depart", "mode"),
    )
    op.create_table(
        "travel_times",
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["set_id"], ["travel_sets.id"]),
        sa.PrimaryKeyConstraint("set_id", "location_id"),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_jobs_hidden_at", "jobs", ["hidden_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_hidden_at", table_name="jobs")
    op.drop_table("travel_times")
    op.drop_table("travel_sets")
