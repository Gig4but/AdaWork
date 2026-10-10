"""seen marks become hidden ads

Revision ID: d7f1b3c5e9a2
Revises: c4e8a2f6b0d3
Create Date: 2026-10-13 10:00:00
"""

from alembic import op

revision = "d7f1b3c5e9a2"
down_revision = "c4e8a2f6b0d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ALTER TABLE: batch mode would rebuild `jobs` and drop the FTS triggers.
    op.execute("ALTER TABLE jobs RENAME COLUMN seen_at TO hidden_at")


def downgrade() -> None:
    op.execute("ALTER TABLE jobs RENAME COLUMN hidden_at TO seen_at")
