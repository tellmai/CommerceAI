"""Index scoped related records for ranked recommendations and plan history."""

from alembic import op

revision = "0003"
down_revision = "0002"

FIELDS = ("candidate_id", "sku_id", "listing_id", "offer_id")


def upgrade():
    for field in FIELDS:
        op.execute(
            f"CREATE INDEX entity_{field}_lookup ON entity "
            f"(workspace, kind, (data->>'{field}'), updated_at DESC, id) WHERE data->>'{field}' IS NOT NULL"
        )
    op.execute(
        "CREATE INDEX entity_keyword_window ON entity (workspace, (data->>'source_id'), "
        "(data->>'window_end'), created_at DESC) WHERE kind = 'keywords'"
    )


def downgrade():
    op.execute("DROP INDEX entity_keyword_window")
    for field in reversed(FIELDS):
        op.execute(f"DROP INDEX entity_{field}_lookup")
