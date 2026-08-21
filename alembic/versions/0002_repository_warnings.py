"""Add persistent repository synchronization warnings."""

import sqlalchemy as sa
from alembic import op

revision = "0002_repository_warnings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repositories", sa.Column("last_warning", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("repositories", "last_warning")
