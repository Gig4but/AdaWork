"""normalized filter fields

Revision ID: 296da014b589
Revises: e6e03e527819
Create Date: 2026-10-06 15:05:55.963263
"""

import sqlalchemy as sa
from alembic import op

revision = "296da014b589"
down_revision = "e6e03e527819"
branch_labels = None
depends_on = None


# Plain ALTER TABLE on purpose: batch mode would rebuild `jobs`, and SQLite drops the
# FTS sync triggers together with the old table.
LIST_COLUMNS = ("categories", "suitable_for", "benefits")


def upgrade() -> None:
    op.add_column("jobs", sa.Column("salary_month_min", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("salary_month_max", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("education_level", sa.Integer(), nullable=True))
    for name in LIST_COLUMNS:
        op.add_column(
            "jobs", sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
    op.create_index("ix_jobs_education_level", "jobs", ["education_level"])
    op.create_index("ix_jobs_salary_month_min", "jobs", ["salary_month_min"])
    op.drop_index("ix_jobs_category", "jobs")
    op.execute("ALTER TABLE jobs DROP COLUMN category")


def downgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN category VARCHAR(40)")
    op.create_index("ix_jobs_category", "jobs", ["category"])
    op.drop_index("ix_jobs_salary_month_min", "jobs")
    op.drop_index("ix_jobs_education_level", "jobs")
    for name in (*LIST_COLUMNS, "education_level", "salary_month_max", "salary_month_min"):
        op.execute(f"ALTER TABLE jobs DROP COLUMN {name}")
