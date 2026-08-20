"""Initial GitHarbor schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("github_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=128)),
        sa.Column("upstream_owner", sa.String(length=255), nullable=False),
        sa.Column("upstream_name", sa.String(length=255), nullable=False),
        sa.Column("upstream_full_name", sa.String(length=512), nullable=False),
        sa.Column("upstream_url", sa.Text(), nullable=False),
        sa.Column("clone_url", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.String(length=255)),
        sa.Column("upstream_private", sa.Boolean(), nullable=False, default=False),
        sa.Column("upstream_archived", sa.Boolean(), nullable=False, default=False),
        sa.Column("upstream_fork", sa.Boolean(), nullable=False, default=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("destination_namespace", sa.String(length=255), nullable=False),
        sa.Column("destination_name", sa.String(length=255), nullable=False),
        sa.Column("destination_url", sa.Text()),
        sa.Column("currently_starred", sa.Boolean(), nullable=False, default=False),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("github_id", "kind", name="uq_repository_github_id_kind"),
        sa.UniqueConstraint("destination_namespace", "destination_name", name="uq_destination"),
    )
    op.create_index("ix_repositories_status_kind", "repositories", ["status", "kind"])
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "repository_id", sa.Integer(), sa.ForeignKey("repositories.id", ondelete="CASCADE")
        ),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("trigger", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("message", sa.Text()),
        sa.Column("discovered_owned", sa.Integer(), nullable=False, default=0),
        sa.Column("discovered_starred", sa.Integer(), nullable=False, default=0),
        sa.Column("succeeded", sa.Integer(), nullable=False, default=0),
        sa.Column("failed", sa.Integer(), nullable=False, default=0),
    )
    op.create_index("ix_sync_runs_repository_started", "sync_runs", ["repository_id", "started_at"])


def downgrade() -> None:
    op.drop_table("sync_runs")
    op.drop_table("repositories")
