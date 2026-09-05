"""Add provider-neutral repository identity and explicit wiki URLs."""

import sqlalchemy as sa
from alembic import op

revision = "0004_external_sources"
down_revision = "0003_container_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("repositories") as batch:
        batch.alter_column("github_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(
            sa.Column(
                "source_provider",
                sa.String(length=32),
                nullable=False,
                server_default="github",
            )
        )
        batch.add_column(sa.Column("source_id", sa.String(length=128)))
        batch.add_column(sa.Column("wiki_clone_url", sa.Text()))

    op.execute("UPDATE repositories SET source_id = CAST(github_id AS VARCHAR)")

    with op.batch_alter_table("repositories") as batch:
        batch.create_unique_constraint(
            "uq_repository_source_identity", ["source_provider", "source_id", "kind"]
        )


def downgrade() -> None:
    connection = op.get_bind()
    external_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM repositories WHERE github_id IS NULL")
    ).scalar_one()
    if external_count:
        raise RuntimeError("cannot downgrade while external repository records exist")
    with op.batch_alter_table("repositories") as batch:
        batch.drop_constraint("uq_repository_source_identity", type_="unique")
        batch.drop_column("wiki_clone_url")
        batch.drop_column("source_id")
        batch.drop_column("source_provider")
        batch.alter_column("github_id", existing_type=sa.Integer(), nullable=False)
