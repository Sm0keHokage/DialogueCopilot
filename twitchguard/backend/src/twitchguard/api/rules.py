"""Rules API (IR-05..IR-09, UC-02). Moderators read-only (FR-02)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..errors import ApiError
from ..rbac import AuthContext, require_channel_member, require_channel_owner
from ..rules.parser import RuleValidationError, parse_rule_markdown
from ..rules.service import (
    create_rule_version,
    list_current_rules,
    rule_out,
    rule_versions,
    set_rule_enabled,
)
from .deps import get_db

router = APIRouter(prefix="/channels/{channel_id}/rules")


class RuleBody(BaseModel):
    md_content: str


class RulePatch(BaseModel):
    enabled: bool | None = None
    md_content: str | None = None


@router.get("")
async def get_rules(
    channel_id: int,
    ctx: AuthContext = Depends(require_channel_member),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """IR-05."""
    return [rule_out(r) for r in await list_current_rules(db, channel_id)]


@router.post("/validate")
async def validate_rule(
    channel_id: int,
    body: RuleBody,
    ctx: AuthContext = Depends(require_channel_owner),
) -> dict[str, Any]:
    """IR-06: dry-run validation for the upload preview (UC-02 step 3)."""
    try:
        fm, _ = parse_rule_markdown(body.md_content)
    except RuleValidationError as exc:
        return {"valid": False, "errors": exc.errors}
    return {"valid": True, "frontmatter": fm.model_dump()}


@router.post("", status_code=201)
async def create_rule(
    channel_id: int,
    body: RuleBody,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """IR-07: create rule or new version; invalid -> 422 with the offending field (FR-19)."""
    try:
        rule = await create_rule_version(db, channel_id, body.md_content)
    except RuleValidationError as exc:
        raise ApiError(
            422, "invalid_rule", "Rule markdown failed validation",
            field=exc.errors[0]["field"], details=exc.errors,
        ) from exc
    await record(
        db, channel_id=channel_id, actor_type="user", actor_id=ctx.user_id,
        action="rule.saved", target=f"rule:{rule.name}", payload={"version": rule.version},
    )
    await db.commit()
    return rule_out(rule)


@router.patch("/{name}")
async def patch_rule(
    channel_id: int,
    name: str,
    body: RulePatch,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """IR-08: toggle enabled (FR-22) and/or new version from md (FR-20/FR-21)."""
    rule = None
    if body.md_content is not None:
        try:
            fm, _ = parse_rule_markdown(body.md_content)
        except RuleValidationError as exc:
            raise ApiError(
                422, "invalid_rule", "Rule markdown failed validation",
                field=exc.errors[0]["field"], details=exc.errors,
            ) from exc
        if fm.name != name:
            raise ApiError(
                422, "name_mismatch",
                f"Frontmatter name '{fm.name}' does not match rule '{name}'", field="name",
            )
        rule = await create_rule_version(db, channel_id, body.md_content)
    if body.enabled is not None:
        rule = await set_rule_enabled(db, channel_id, name, body.enabled)
    if rule is None:
        raise ApiError(422, "empty_patch", "Nothing to update", field="enabled")
    await record(
        db, channel_id=channel_id, actor_type="user", actor_id=ctx.user_id,
        action="rule.updated", target=f"rule:{name}",
        payload={"enabled": body.enabled, "new_version": body.md_content is not None},
    )
    await db.commit()
    return rule_out(rule)


@router.get("/{name}/versions")
async def get_rule_versions(
    channel_id: int,
    name: str,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """IR-09."""
    return [rule_out(r) for r in await rule_versions(db, channel_id, name)]
