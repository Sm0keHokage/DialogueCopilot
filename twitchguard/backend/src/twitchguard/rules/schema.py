"""Frontmatter validation schema (DR-03, FR-16)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class RuleFrontmatter(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1)
    enabled: bool
    severity: Literal["low", "medium", "high"]
    confidence_threshold: float = Field(ge=0.0, le=1.0)
    action_hint: Literal["delete", "timeout", "warn", "review"] | None = None
    languages: list[str] | None = None


def errors_from_validation(exc: ValidationError) -> list[dict[str, str]]:
    """FR-19: point at the exact offending field."""
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "frontmatter"
        out.append({"field": loc, "message": str(err.get("msg", "invalid value"))})
    return out
