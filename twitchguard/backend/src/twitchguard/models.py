"""ORM models per §7 (DR-01..DR-08).

PostgreSQL-specific types carry SQLite variants so the test suite can run
without external services; production stays on the DR-specified types.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow

BigIntPK = BigInteger().with_variant(Integer(), "sqlite")
JSONType = JSONB().with_variant(SQLITE_JSON(), "sqlite")
TextArray = ARRAY(Text()).with_variant(SQLITE_JSON(), "sqlite")
TZDateTime = DateTime(timezone=True)


class Channel(Base):
    """DR-01."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    twitch_user_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_access_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encrypted_refresh_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    eventsub_status: Mapped[str] = mapped_column(Text, nullable=False, default="inactive")
    backend_config: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    action_proxy_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # FR-08: set when the refresh token is invalid -> "reconnect required".
    needs_reauth: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Parallel AI classifier agents for high-traffic chats (one Twitch reader
    # account regardless — AR-03; only classification fans out).
    classifier_workers: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class Account(Base):
    """Local account system (личный кабинет): email+nick+password, kept fully
    separate from Twitch identity (NFR-Sec-01/AR-01 — this table never stores
    a Twitch credential, only the scrypt hash of a locally-chosen password,
    see accounts.py). A `User` row (Twitch identity) may optionally link to
    one `Account` via `User.account_id` once its owner verifies their email
    and connects Twitch (see api/auth.py callback)."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # stored lowercase
    nick: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # stored lowercase
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_token_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    verify_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class User(Base):
    """DR-08 plus encrypted per-user tokens needed by Action Proxy (FR-41/FR-54)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    twitch_user_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    login: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)  # owner | moderator
    channel_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional link to a local account (личный кабинет) — set once its owner
    # verifies their email and connects Twitch (api/auth.py callback).
    account_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("accounts.id"),
        nullable=True,
    )
    encrypted_access_token: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    encrypted_refresh_token: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    scopes: Mapped[list[str] | None] = mapped_column(TextArray, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class ModeratorInvite(Base):
    """Owner-managed moderator allowlist; a user gets the moderator role on first login."""

    __tablename__ = "moderator_invites"
    __table_args__ = (UniqueConstraint("channel_id", "login"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    login: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)


class Rule(Base):
    """DR-02."""

    __tablename__ = "rules"
    __table_args__ = (
        UniqueConstraint("channel_id", "name", "version"),
        Index("ix_rules_channel_current", "channel_id", "is_current"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    md_content: Mapped[str] = mapped_column(Text, nullable=False)
    frontmatter: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)


class Flag(Base):
    """DR-04."""

    __tablename__ = "flags"
    __table_args__ = (
        Index("ix_flags_channel_status_created", "channel_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    twitch_message_id: Mapped[str] = mapped_column(Text, nullable=False)
    author_login: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[str] = mapped_column(Text, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    rule_name: Mapped[str] = mapped_column(Text, nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    action_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)


class AuditLog(Base):
    """DR-06."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    channel_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=True,
    )
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)  # user | system
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)


class Usage(Base):
    """DR-07."""

    __tablename__ = "usage"
    __table_args__ = (UniqueConstraint("channel_id", "day"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    messages_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flags_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classification_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
