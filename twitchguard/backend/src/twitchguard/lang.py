"""Best-effort language detection for FR-31.

Chat messages are short, so we use a cheap script heuristic instead of a model.
An undetected language applies every rule — better a reviewable false flag than
a silently skipped violation (advisory system, a human decides anyway).
"""
from __future__ import annotations


def detect_language(text: str) -> str | None:
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    lat = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    letters = cyr + lat
    if letters < 3:
        return None
    if cyr / letters > 0.6:
        return "ru"
    if lat / letters > 0.6:
        return "en"
    return None


def rule_applies_to_language(rule_languages: list[str] | None, lang: str | None) -> bool:
    if not rule_languages:
        return True  # language-independent rule
    if lang is None:
        return True
    return lang in rule_languages
