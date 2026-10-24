"""Relational model: users, groups, per-referential grants, sessions, API tokens and audit log."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes: they are stored as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Base(DeclarativeBase):
    pass


# advanced: may create internal referentials (and edit those it manages)
ROLES = ("admin", "advanced", "user")
# service: account of a tool, API tokens only (created and managed by administrators, never signs in)
AUTH_SOURCES = ("local", "ldap", "oidc", "service")
SERVICE_PASSWORD = "!service"  # never a valid password hash
# active: may use the application · pending: waiting for an administrator · rejected: refused
USER_STATUSES = ("active", "pending", "rejected")
LEVELS = {"read": 1, "manage": 2}
ALL_REFERENTIALS = "*"
# Grant scope covering a whole category: "category:Threat intelligence". A referential identifier cannot
# contain a colon, so the two forms never collide.
CATEGORY_PREFIX = "category:"
MAX_CATEGORY_LENGTH = 100

user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    # External identities (LDAP DN, OIDC subject) and approval workflow
    auth_source: Mapped[str] = mapped_column(String(16), default="local", server_default="local")
    external_id: Mapped[str | None] = mapped_column(String(512), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Second factor (TOTP, RFC 6238): secret encrypted at rest, last accepted time step (no replay),
    # hashes of the one-time recovery codes
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_last_step: Mapped[int | None] = mapped_column(Integer)
    recovery_codes: Mapped[list | None] = mapped_column(JSON)

    groups: Mapped[list["Group"]] = relationship(secondary=user_groups, back_populates="members", lazy="selectin")

    __table_args__ = (CheckConstraint("role IN ('admin', 'advanced', 'user')", name="ck_users_role"),)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Groups synchronised from LDAP / OIDC: membership is managed by the identity provider
    source: Mapped[str] = mapped_column(String(16), default="local", server_default="local")
    external_id: Mapped[str | None] = mapped_column(String(512), index=True)

    members: Mapped[list[User]] = relationship(secondary=user_groups, back_populates="groups", lazy="selectin")


class Grant(Base):
    """Access granted to a user or a group.

    `referential_id` holds the scope: a referential identifier, `*` (every referential), or `category:<name>`
    (every referential of that category, resolved at each request, so one added to the category later is
    covered without touching the rights).
    """

    __tablename__ = "grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referential_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(16), default="read")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_by: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[User | None] = relationship(lazy="joined")
    group: Mapped[Group | None] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (group_id IS NULL)", name="ck_grants_subject"),
        CheckConstraint("level IN ('read', 'manage')", name="ck_grants_level"),
    )


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))

    user: Mapped[User] = relationship(lazy="joined")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="joined")


class WebauthnCredential(Base):
    """Security key or passkey (WebAuthn / FIDO2) registered as a second factor."""

    __tablename__ = "webauthn_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[str] = mapped_column(String(1024), unique=True)  # base64url
    public_key: Mapped[str] = mapped_column(String(2048))  # COSE key, base64url
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[list | None] = mapped_column(JSON)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    """Settings edited from the administration UI (proxy, LDAP, OpenID Connect). Secrets are encrypted."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    updated_by: Mapped[str | None] = mapped_column(String(64))


class ReferentialDefinition(Base):
    """Referential created and edited from the administration UI (YAML files remain supported)."""

    __tablename__ = "referential_definitions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))


class InternalRecord(Base):
    """Row of an internal referential (created and fed from RefExposer: editor or API)."""

    __tablename__ = "internal_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referential_id: Mapped[str] = mapped_column(String(64), index=True)
    row_key: Mapped[str] = mapped_column(String(512))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (UniqueConstraint("referential_id", "row_key", name="uq_internal_records_key"),)


class SourceSecret(Base):
    """Credential of a source (API key, token, password), referenced as ${secret:<name>} in source headers, Basic
    authentication or Git token. The value is encrypted at rest and never returned by the API."""

    __tablename__ = "source_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(String(8192))
    # Hosts the secret may be sent to (fnmatch patterns, e.g. "*.example.com"); any host when empty
    hosts: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))


class Conversation(Base):
    """Conversation of a user with the assistant, kept until the user deletes it.

    It belongs to one account: the API filters on `user_id` and reports another user's conversation as unknown.
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    model: Mapped[str | None] = mapped_column(String(128))
    # Agent mode asked for by the user on this conversation
    agent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, index=True)


class ChatMessage(Base):
    """One message of a conversation. Assistant messages carry the trace of the tools they used."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(32), ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(String(1_000_000), default="")
    # Assistant message: [{tool, arguments, ok, summary}] of the steps it went through
    steps: Mapped[list | None] = mapped_column(JSON)
    # Assistant message: [{kind: sql|file, ...}] handed to the user (a query to open in the console, a file)
    artifacts: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (CheckConstraint("role in ('user', 'assistant')", name="ck_chat_messages_role"),)


class ReferentialSnapshot(Base):
    """Published version kept for time travel (`storage.time_travel`): the Parquet file of that version, and
    what it held. The file is a hard link on the published file, so a snapshot costs nothing until the version
    it shares its blocks with is replaced."""

    __tablename__ = "referential_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referential_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    row_count: Mapped[int | None] = mapped_column(Integer)
    # Name of the file inside data/<id>/snapshots/
    file: Mapped[str] = mapped_column(String(255))
    size: Mapped[int | None] = mapped_column(Integer)
    # Hash of the column names and types: two snapshots with different schemas are compared on their common columns
    schema_hash: Mapped[str] = mapped_column(String(32), default="")
    columns: Mapped[list | None] = mapped_column(JSON)
    # The file is encrypted (the referential was confidential when it was published)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (UniqueConstraint("referential_id", "file", name="uq_snapshots_file"),)


# Watchdog: how its runs are started, and what one found
WATCHDOG_TRIGGERS = ("cron", "on_sync")
WATCHDOG_STATUSES = ("ok", "finding", "error")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


class WatchdogTask(Base):
    """A scheduled investigation run by the assistant on a fixed set of referentials (watchdog.py).

    It runs with the rights of the account it belongs to, narrowed to `referential_ids`: the agent cannot read
    anything else, whatever the model asks for. Its answer is a structured verdict, and the recipients are only
    told when it actually found something.
    """

    __tablename__ = "watchdog_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    # Account whose rights the investigation runs with (its author, or whoever an administrator chose)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The only referentials this watchdog may read: chosen explicitly, never "everything"
    referential_ids: Mapped[list] = mapped_column(JSON, default=list)
    trigger_type: Mapped[str] = mapped_column(String(16), default="cron")
    schedule: Mapped[str | None] = mapped_column(String(100))  # crontab expression (cron trigger)
    prompt: Mapped[str] = mapped_column(String(8000), default="")
    template: Mapped[str | None] = mapped_column(String(32))  # the pre-filled template it was created from
    # Who is told when a finding is detected: e-mail addresses, and ids of configured notification channels
    notify_emails: Mapped[list | None] = mapped_column(JSON)
    notify_channels: Mapped[list | None] = mapped_column(JSON)
    # Below this severity a finding is recorded but nobody is woken up
    min_severity: Mapped[str] = mapped_column(String(16), default="LOW")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[User] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("trigger_type IN ('cron', 'on_sync')", name="ck_watchdog_trigger"),
    )


class WatchdogRun(Base):
    """One investigation: what started it, what the agent concluded, and whether the recipients were told."""

    __tablename__ = "watchdog_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("watchdog_tasks.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # cron · on_sync:<referential> · manual
    trigger: Mapped[str] = mapped_column(String(80), default="cron")
    status: Mapped[str] = mapped_column(String(16), default="ok")
    finding_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    severity: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(String(2000), default="")
    details_markdown: Mapped[str] = mapped_column(String(200_000), default="")
    affected_items_count: Mapped[int | None] = mapped_column(Integer)
    steps: Mapped[list | None] = mapped_column(JSON)  # tool calls of the agent, as in a conversation
    # [{target, ok, detail}] of the notifications sent (empty when there was nothing to tell)
    notifications: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(2000))

    __table_args__ = (CheckConstraint("status IN ('ok', 'finding', 'error')", name="ck_watchdog_run_status"),)


class AuditLog(Base):
    """Kept even when the user is deleted (no foreign key)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(255))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)


Index("ix_audit_log_target", AuditLog.target)
