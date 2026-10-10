"""seen marks on jobs

Revision ID: c4e8a2f6b0d3
Revises: a3d5c7e9f1b2
Create Date: 2026-10-10 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e8a2f6b0d3"
down_revision = "a3d5c7e9f1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ALTER TABLE: batch mode would rebuild `jobs` and drop the FTS triggers.
    op.add_column("jobs", sa.Column("seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP COLUMN seen_at")
