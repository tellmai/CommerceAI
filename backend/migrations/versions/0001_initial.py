"""Initial scoped entity, revision, import, job and session storage."""

from alembic import op
from pathlib import Path

revision = "0001"
down_revision = None


def upgrade():
    ddl = (Path(__file__).resolve().parents[1] / "initial_schema.sql").read_text()
    ddl = "\n".join(line for line in ddl.splitlines() if not line.lstrip().startswith("--"))
    for statement in ddl.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for name in [
        "entity_link",
        "entity_revision",
        "import_batch",
        "audit_event",
        "login_session",
        "job",
        "entity",
    ]:
        op.drop_table(name)
