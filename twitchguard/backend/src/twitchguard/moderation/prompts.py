"""Classifier prompt: fixed template built from rule sections (FR-24, DR-05)."""
from __future__ import annotations

import json
from collections.abc import Sequence

from ..models import Rule
from .types import ChatMessage

VERDICT_SCHEMA = """[
  {
    "message_id": "string",
    "rule": "string",
    "is_violation": true,
    "confidence": 0.0,
    "reason": "string",
    "action_hint": "delete|timeout|warn|review|null"
  }
]"""

CORRECTIVE_INSTRUCTION = (
    "Your previous reply was not a valid JSON array matching the verdict schema. "
    "Respond again with ONLY the JSON array — no prose, no markdown fences, no text "
    "before or after the array."
)


def _rule_body(rule: Rule) -> str:
    md = rule.md_content
    if md.startswith("---"):
        end = md.find("---", 3)
        if end != -1:
            md = md[end + 3 :]
    return md.strip()


def build_prompt(messages: Sequence[ChatMessage], rules: Sequence[Rule]) -> str:
    rules_block = "\n\n".join(
        f"### Rule `{r.name}` (severity: {(r.frontmatter or {}).get('severity', 'low')})\n"
        f"{_rule_body(r)}"
        for r in rules
    )
    messages_block = json.dumps(
        [
            {"message_id": m.message_id, "author": m.author_login, "text": m.text}
            for m in messages
        ],
        ensure_ascii=False,
        indent=2,
    )
    return (
        "You are a strict Twitch chat moderation classifier. You never talk to chat; "
        "you only classify messages against the moderation rules below. A human "
        "moderator reviews every flag, so be precise but do not be timid about "
        "flagging real violations.\n\n"
        "## Moderation rules\n\n"
        f"{rules_block}\n\n"
        "## Chat messages to classify (JSON)\n\n"
        f"{messages_block}\n\n"
        "## Task\n\n"
        "Evaluate EVERY message against EVERY rule. Output a verdict object only for "
        "(message, rule) pairs where is_violation is true; skip non-violations. If "
        "nothing violates anything, output an empty array [].\n\n"
        "Respond with ONLY a JSON array matching this schema exactly (no text outside "
        "the JSON):\n"
        f"{VERDICT_SCHEMA}\n\n"
        "confidence is your calibrated probability (0..1) that the message truly "
        "violates the rule. reason is one short sentence in the language of the "
        "message explaining why. action_hint is your suggested moderator action or "
        "null."
    )
