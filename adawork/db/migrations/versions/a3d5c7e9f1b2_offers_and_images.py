"""offers, red flags and job images

Revision ID: a3d5c7e9f1b2
Revises: 37896a1f5922
Create Date: 2026-10-09 21:00:00
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "a3d5c7e9f1b2"
down_revision = "37896a1f5922"
branch_labels = None
depends_on = None

BATCH = 2000


def upgrade() -> None:
    # Plain ALTER TABLE: batch mode would rebuild `jobs` and drop the FTS triggers.
    op.add_column(
        "jobs", sa.Column("is_offer", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "jobs", sa.Column("red_flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
    )
    op.create_index("ix_jobs_is_offer", "jobs", ["is_offer"])
    op.create_table(
        "job_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("file", sa.String(), nullable=True),
        sa.Column("phash", sa.String(length=16), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("text", sa.String(), nullable=True),
        sa.Column("text_model", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "url"),
    )
    op.create_index("ix_job_images_job_id", "job_images", ["job_id"])
    op.create_index("ix_job_images_status", "job_images", ["status"])
    op.create_index("ix_job_images_phash", "job_images", ["phash"])
    _flag_existing_jobs()


def _flag_existing_jobs() -> None:
    """Red flags for jobs stored before this version (new jobs get them when stored)."""
    from adawork.normalize.offers import red_flags

    conn = op.get_bind()
    last = 0
    while True:
        rows = conn.execute(
            sa.text(
                "SELECT id, title, description FROM jobs WHERE id > :last ORDER BY id LIMIT :n"
            ),
            {"last": last, "n": BATCH},
        ).all()
        if not rows:
            break
        updates = [
            {"id": job_id, "flags": json.dumps(flags)}
            for job_id, title, description in rows
            if (flags := red_flags(title, description))
        ]
        if updates:
            conn.execute(sa.text("UPDATE jobs SET red_flags = :flags WHERE id = :id"), updates)
        last = rows[-1][0]


def downgrade() -> None:
    op.drop_index("ix_job_images_phash", "job_images")
    op.drop_index("ix_job_images_status", "job_images")
    op.drop_index("ix_job_images_job_id", "job_images")
    op.drop_table("job_images")
    op.drop_index("ix_jobs_is_offer", "jobs")
    op.execute("ALTER TABLE jobs DROP COLUMN red_flags")
    op.execute("ALTER TABLE jobs DROP COLUMN is_offer")
