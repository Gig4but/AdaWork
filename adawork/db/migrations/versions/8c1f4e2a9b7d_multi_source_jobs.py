"""multi-source jobs and postal codes

Revision ID: 8c1f4e2a9b7d
Revises: 296da014b589
Create Date: 2026-10-06 20:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "8c1f4e2a9b7d"
down_revision = "296da014b589"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_sources",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "job_sources",
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
    )
    op.add_column("job_sources", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("job_sources", sa.Column("detail_at", sa.DateTime(), nullable=True))
    # Until now every job had exactly one source, which held the job's own content.
    op.execute(
        "UPDATE job_sources SET "
        "content_hash = (SELECT content_hash FROM jobs WHERE jobs.id = job_sources.job_id), "
        "status = (SELECT CASE status WHEN 'expired' THEN 'expired' ELSE 'active' END "
        "          FROM jobs WHERE jobs.id = job_sources.job_id), "
        "detail_at = first_seen_at"
    )
    op.add_column(
        "municipalities",
        sa.Column("postal_codes", sa.String(), nullable=False, server_default=""),
    )
    # Rebuilt with postal codes on the next run (the runner rebuilds an empty table).
    op.execute("DELETE FROM municipalities")


def downgrade() -> None:
    op.execute("ALTER TABLE municipalities DROP COLUMN postal_codes")
    for name in ("detail_at", "content_hash", "status", "is_primary"):
        op.execute(f"ALTER TABLE job_sources DROP COLUMN {name}")
