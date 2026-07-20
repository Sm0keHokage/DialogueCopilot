"""Initial schema (DR-01..DR-08).

Revision ID: 0001
Revises:
Create Date: 2026-07-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("twitch_user_id", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("encrypted_access_token", postgresql.BYTEA(), nullable=False),
        sa.Column("encrypted_refresh_token", postgresql.BYTEA(), nullable=False),
        sa.Column("token_expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "eventsub_status", sa.Text(), nullable=False, server_default=sa.text("'inactive'")
        ),
        sa.Column(
            "backend_config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "action_proxy_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("needs_reauth", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("twitch_user_id", sa.Text(), nullable=False, unique=True),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "channel_id", sa.BigInteger(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("encrypted_access_token", postgresql.BYTEA(), nullable=True),
        sa.Column("encrypted_refresh_token", postgresql.BYTEA(), nullable=True),
        sa.Column("token_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "moderator_invites",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "channel_id", sa.BigInteger(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("channel_id", "login"),
    )
    op.create_table(
        "rules",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "channel_id", sa.BigInteger(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("md_content", sa.Text(), nullable=False),
        sa.Column("frontmatter", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("channel_id", "name", "version"),
    )
    op.create_index("ix_rules_channel_current", "rules", ["channel_id", "is_current"])
    op.create_table(
        "flags",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "channel_id", sa.BigInteger(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("twitch_message_id", sa.Text(), nullable=False),
        sa.Column("author_login", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Text(), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("rule_name", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("action_hint", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'new'")),
        sa.Column("reviewed_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_flags_channel_status_created", "flags", ["channel_id", "status", "created_at"]
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "channel_id", sa.BigInteger(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "channel_id", sa.BigInteger(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("messages_processed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("flags_created", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "classification_failed", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("requests", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("channel_id", "day"),
    )


def downgrade() -> None:
    for table in ("usage", "audit_log", "flags", "rules", "moderator_invites", "users", "channels"):
        op.drop_table(table)
