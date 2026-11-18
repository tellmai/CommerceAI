"""Enforce a single confirmed canonical SKU per platform SKU."""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS single_confirmed_match ON entity (workspace, (data->>'listing_id')) WHERE kind = 'matches' AND data->>'state' = 'MATCHED'"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS single_confirmed_match")
