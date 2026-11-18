from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    and_,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import DATABASE_URL


def now():
    return datetime.now(UTC)


def new_id():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


document = JSON().with_variant(JSONB(), "postgresql")


class Entity(Base):
    __tablename__ = "entity"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    external_key: Mapped[str | None] = mapped_column(String(500))
    data: Mapped[dict] = mapped_column(document, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("workspace", "id", name="entity_scope_id"),
        UniqueConstraint("workspace", "kind", "external_key", name="entity_natural_key"),
        Index("entity_list", "workspace", "kind", "created_at"),
        Index("entity_data", "data", postgresql_using="gin"),
    )


class Link(Base):
    """Composite foreign keys prevent relationships crossing data spaces."""

    __tablename__ = "entity_link"
    workspace: Mapped[str] = mapped_column(String(16), primary_key=True)
    from_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    field: Mapped[str] = mapped_column(String(80), primary_key=True)
    to_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(["workspace", "from_id"], ["entity.workspace", "entity.id"]),
        ForeignKeyConstraint(["workspace", "to_id"], ["entity.workspace", "entity.id"]),
        Index("link_target", "workspace", "to_id", "field"),
    )


Index(
    "single_confirmed_match",
    Entity.workspace,
    Entity.data["listing_id"].as_string(),
    unique=True,
    postgresql_where=and_(Entity.kind == "matches", Entity.data["state"].as_string() == "MATCHED"),
)


class Revision(Base):
    __tablename__ = "entity_revision"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(document, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        ForeignKeyConstraint(["workspace", "entity_id"], ["entity.workspace", "entity.id"]),
        UniqueConstraint("entity_id", "version", name="revision_version"),
    )


class Audit(Base):
    __tablename__ = "audit_event"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), default="owner")
    details: Mapped[dict] = mapped_column(document, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ImportBatch(Base):
    __tablename__ = "import_batch"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    options: Mapped[dict] = mapped_column(document, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PREVIEW")
    mapping: Mapped[dict] = mapped_column(document, default=dict)
    import_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    errors_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (ForeignKeyConstraint(["workspace", "source_id"], ["entity.workspace", "entity.id"]),)


class Job(Base):
    __tablename__ = "job"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    idem_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(document, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    result: Mapped[dict | None] = mapped_column(document)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (Index("job_claim", "status", "run_after"),)


class LoginSession(Base):
    __tablename__ = "login_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=2, max_overflow=1)
Session = sessionmaker(engine, expire_on_commit=False)
