"""employers, their names, reviews, mentions and summaries; jobs.employer_id

Revision ID: b5d2e8f4a1c7
Revises: 1592b84d5c9c
Create Date: 2026-10-14 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "b5d2e8f4a1c7"
down_revision = "1592b84d5c9c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ico", sa.String(length=8), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("match", sa.String(length=10), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("dic", sa.String(length=14), nullable=True),
        sa.Column("legal_form", sa.String(length=3), nullable=True),
        sa.Column("founded", sa.Date(), nullable=True),
        sa.Column("ended", sa.Date(), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("nace_main", sa.String(length=6), nullable=True),
        sa.Column("employee_band", sa.String(length=3), nullable=True),
        sa.Column("insolvency", sa.JSON(), nullable=False),
        sa.Column("vat_unreliable", sa.String(length=20), nullable=True),
        sa.Column("agency", sa.JSON(), nullable=True),
        sa.Column("looks_agency", sa.Boolean(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("satisfaction", sa.Integer(), nullable=True),
        sa.Column("links", sa.JSON(), nullable=False),
        sa.Column("reviews_at", sa.DateTime(), nullable=True),
        sa.Column("mentions_at", sa.DateTime(), nullable=True),
        sa.Column("jobs_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ico"),
    )
    op.create_index("ix_employers_checked_at", "employers", ["checked_at"])
    op.create_index("ix_employers_satisfaction", "employers", ["satisfaction"])
    op.create_table(
        "employer_names",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("employer_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_employer_names_employer_id", "employer_names", ["employer_id"])
    op.create_table(
        "employer_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employer_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("scale", sa.String(length=8), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("texts", sa.JSON(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employer_id", "source"),
    )
    op.create_table(
        "employer_mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employer_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("snippet", sa.String(), nullable=False),
        sa.Column("passages", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_employer_mentions_employer_id", "employer_mentions", ["employer_id"])
    op.create_table(
        "employer_summaries",
        sa.Column("employer_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("inputs", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=40), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("employer_id"),
    )
    # Not batch mode: rebuilding the jobs table would drop its full-text triggers.
    op.execute(
        "ALTER TABLE jobs ADD COLUMN employer_id INTEGER "
        "REFERENCES employers (id) ON DELETE SET NULL"
    )
    op.create_index("ix_jobs_employer_id", "jobs", ["employer_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_employer_id", table_name="jobs")
    # Not batch mode (see upgrade). Left in place, its reference to the dropped employers
    # table would fail every write to jobs.
    op.execute("ALTER TABLE jobs DROP COLUMN employer_id")
    op.drop_table("employer_summaries")
    op.drop_index("ix_employer_mentions_employer_id", table_name="employer_mentions")
    op.drop_table("employer_mentions")
    op.drop_table("employer_reviews")
    op.drop_index("ix_employer_names_employer_id", table_name="employer_names")
    op.drop_table("employer_names")
    op.drop_index("ix_employers_satisfaction", table_name="employers")
    op.drop_index("ix_employers_checked_at", table_name="employers")
    op.drop_table("employers")
