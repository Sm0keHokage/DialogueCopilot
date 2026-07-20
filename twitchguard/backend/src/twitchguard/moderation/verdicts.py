"""Model verdict contract (DR-05) and robust parsing (FR-25)."""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)\n?```$", re.DOTALL)


class Verdict(BaseModel):
    message_id: str
    rule: str
    is_violation: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    action_hint: Literal["delete", "timeout", "warn", "review"] | None = None

    @field_validator("action_hint", mode="before")
    @classmethod
    def _normalize_hint(cls, v: object) -> object:
        if isinstance(v, str) and v.strip().lower() in ("", "null", "none"):
            return None
        return v


class VerdictParseError(Exception):
    pass


def parse_verdicts(raw: str) -> list[Verdict]:
    """Parse the model reply into verdicts.

    DR-05 demands a bare JSON array; per FR-25 the parser is resilient to the
    common failure modes (code fences, prose around the array) but rejects
    anything that does not validate against the schema.
    """
    text = raw.strip()
    fence = _CODE_FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            raise VerdictParseError("reply contains no JSON array") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise VerdictParseError(f"invalid JSON: {exc.msg}") from None
    if not isinstance(data, list):
        raise VerdictParseError("top-level JSON value must be an array")
    verdicts: list[Verdict] = []
    for i, item in enumerate(data):
        try:
            verdicts.append(Verdict.model_validate(item))
        except ValidationError as exc:
            errors = exc.errors()
            loc = errors[0].get("loc") if errors else None
            raise VerdictParseError(
                f"item {i} does not match the verdict schema: {loc}"
            ) from None
    return verdicts
