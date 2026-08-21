"""Track safely managed container image versions."""

import sqlalchemy as sa
from alembic import op

revision = "0003_container_images"
down_revision = "0002_repository_warnings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "container_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Integer(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("github_package_id", sa.Integer(), nullable=False),
        sa.Column("github_version_id", sa.Integer(), nullable=False),
        sa.Column("package_name", sa.String(length=255), nullable=False),
        sa.Column("source_digest", sa.String(length=80), nullable=False),
        sa.Column("destination_digest", sa.String(length=80), nullable=False),
        sa.Column("managed_versions", sa.Text(), nullable=False, default="{}"),
        sa.Column("state", sa.String(length=16), nullable=False, default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "repository_id", "github_version_id", name="uq_container_repository_version"
        ),
    )
    op.create_index(
        "ix_container_images_repository_package",
        "container_images",
        ["repository_id", "github_package_id"],
    )


def downgrade() -> None:
    op.drop_table("container_images")
