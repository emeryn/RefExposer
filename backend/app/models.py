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
    """Access granted to a user or a group on a referential ('*' = every referential)."""

    __tablename__ = "grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referential_id: Mapped[str] = mapped_column(String(64), index=True)
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
