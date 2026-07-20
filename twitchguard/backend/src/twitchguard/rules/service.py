"""Rule storage: versioning, hot-reload, built-ins (FR-17, FR-20, FR-21, FR-22)."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import Rule
from .parser import parse_rule_markdown

log = logging.getLogger(__name__)


async def list_current_rules(db: AsyncSession, channel_id: int) -> list[Rule]:
    rows = await db.execute(
        select(Rule)
        .where(Rule.channel_id == channel_id, Rule.is_current.is_(True))
        .order_by(Rule.name)
    )
    return list(rows.scalars())


async def get_active_rules(db: AsyncSession, channel_id: int) -> list[Rule]:
    """Read on every classification batch — this is what makes edits hot (FR-20)."""
    rows = await db.execute(
        select(Rule)
        .where(Rule.channel_id == channel_id, Rule.is_current.is_(True), Rule.enabled.is_(True))
        .order_by(Rule.name)
    )
    return list(rows.scalars())


async def create_rule_version(db: AsyncSession, channel_id: int, md_content: str) -> Rule:
    """New rule, or a new version when the name already exists (FR-21, UC-02 A2)."""
    fm, _body = parse_rule_markdown(md_content)
    current = (
        await db.execute(
            select(Rule).where(
                Rule.channel_id == channel_id, Rule.name == fm.name, Rule.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    version = 1
    if current is not None:
        version = current.version + 1
        await db.execute(
            update(Rule)
            .where(Rule.channel_id == channel_id, Rule.name == fm.name)
            .values(is_current=False)
        )
    rule = Rule(
        channel_id=channel_id,
        name=fm.name,
        version=version,
        md_content=md_content,
        frontmatter=fm.model_dump(),
        enabled=fm.enabled,
        is_current=True,
    )
    db.add(rule)
    await db.flush()
    return rule


async def set_rule_enabled(db: AsyncSession, channel_id: int, name: str, enabled: bool) -> Rule:
    """FR-22: toggle without deleting; the column is authoritative for the engine."""
    rule = (
        await db.execute(
            select(Rule).where(
                Rule.channel_id == channel_id, Rule.name == name, Rule.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    if rule is None:
        raise ApiError(404, "rule_not_found", f"Rule '{name}' not found")
    rule.enabled = enabled
    await db.flush()
    return rule


async def rule_versions(db: AsyncSession, channel_id: int, name: str) -> list[Rule]:
    rows = await db.execute(
        select(Rule)
        .where(Rule.channel_id == channel_id, Rule.name == name)
        .order_by(Rule.version.desc())
    )
    versions = list(rows.scalars())
    if not versions:
        raise ApiError(404, "rule_not_found", f"Rule '{name}' not found")
    return versions


async def seed_builtin_rules(db: AsyncSession, channel_id: int, rules_dir: Path) -> int:
    """FR-17: load built-in rules for a newly connected channel; never overwrites."""
    if not rules_dir.is_dir():
        return 0
    seeded = 0
    for path in sorted(rules_dir.glob("*.md")):
        md = path.read_text(encoding="utf-8")
        try:
            fm, _ = parse_rule_markdown(md)
        except Exception:  # noqa: BLE001 - a broken built-in must not break connect
            log.warning("builtin rule %s failed validation, skipped", path.name)
            continue
        exists = (
            await db.execute(
                select(Rule.id).where(Rule.channel_id == channel_id, Rule.name == fm.name)
            )
        ).first()
        if exists:
            continue
        db.add(
            Rule(
                channel_id=channel_id,
                name=fm.name,
                version=1,
                md_content=md,
                frontmatter=fm.model_dump(),
                enabled=fm.enabled,
                is_current=True,
            )
        )
        seeded += 1
    await db.flush()
    return seeded


def rules_fingerprint(rules: list[Rule]) -> str:
    """Cache-buster for the classification cache (FR-30): changes with any rule change."""
    basis = "|".join(f"{r.name}:{r.version}:{r.enabled}" for r in rules)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def rule_out(rule: Rule) -> dict[str, Any]:
    fm = rule.frontmatter or {}
    return {
        "name": rule.name,
        "version": rule.version,
        "title": fm.get("title", rule.name),
        "severity": fm.get("severity", "low"),
        "confidence_threshold": fm.get("confidence_threshold", 0.8),
        "action_hint": fm.get("action_hint"),
        "languages": fm.get("languages"),
        "enabled": rule.enabled,
        "is_current": rule.is_current,
        "valid": True,
        "md_content": rule.md_content,
        "created_at": rule.created_at,
    }
