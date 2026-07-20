"""Markdown rule parsing (FR-16, FR-19)."""
from __future__ import annotations

import re

import yaml
from pydantic import ValidationError

from .schema import RuleFrontmatter, errors_from_validation

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class RuleValidationError(Exception):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
        self.errors = errors


def parse_rule_markdown(md_content: str) -> tuple[RuleFrontmatter, str]:
    """Returns (validated frontmatter, markdown body). Raises RuleValidationError."""
    match = _FRONTMATTER_RE.match(md_content.strip() + "\n") or _FRONTMATTER_RE.match(md_content)
    if match is None:
        raise RuleValidationError(
            [{"field": "frontmatter", "message": "missing YAML frontmatter block (--- ... ---)"}]
        )
    raw_yaml, body = match.group(1), match.group(2)
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f" (line {mark.line + 1})" if mark else ""
        raise RuleValidationError(
            [{"field": "frontmatter", "message": f"invalid YAML{line}"}]
        ) from exc
    if not isinstance(data, dict):
        raise RuleValidationError(
            [{"field": "frontmatter", "message": "frontmatter must be a YAML mapping"}]
        )
    try:
        fm = RuleFrontmatter.model_validate(data)
    except ValidationError as exc:
        raise RuleValidationError(errors_from_validation(exc)) from exc
    return fm, body.strip()
