"""
context_enricher.py — 上下文丰富化引擎 v1.0

检测用户消息中的指代语言（"这个"、"上面说的"等），
自动从对话历史中提取相关上下文，拼接到当前消息前。
0 LLM 调用，纯规则驱动。
"""

import re
from typing import List, Dict, Optional, Tuple

# Referential language patterns (Chinese)
_REFERENTIAL_PATTERNS = [
    re.compile(r"这个|这些|那个|那些|它们?|这里|那里"),
    re.compile(r"上面说的|刚才说的|前面提到|之前说的|你说的那个"),
    re.compile(r"同样的|一样的|还是那个|跟刚才一样"),
    re.compile(r"继续|接着|然后呢|还有呢|再.*一下"),
]

# Topic continuity signals
_CONTINUATION_PATTERNS = [
    re.compile(r"^(好的?|嗯|行|可以|没问题|OK|ok)[\s，。,.]"),
    re.compile(r"^(那|所以|因此|这样的话)"),
]


def detect_referential_language(message: str) -> bool:
    """Check if message contains referential/anaphoric language."""
    return any(p.search(message) for p in _REFERENTIAL_PATTERNS)


def detect_continuation(message: str) -> bool:
    """Check if message is a continuation of previous topic."""
    return any(p.search(message) for p in _CONTINUATION_PATTERNS)


def needs_context_enrichment(message: str) -> bool:
    """Determine if the message needs historical context prepended."""
    # Short messages with referential language are strong signals
    if len(message) < 30 and detect_referential_language(message):
        return True
    # Continuation signals
    if detect_continuation(message):
        return True
    # Explicit referential language
    if detect_referential_language(message):
        return True
    return False


def extract_relevant_history(
    messages: List[Dict],
    current_message: str,
    max_turns: int = 3,
    max_chars: int = 800,
) -> str:
    """
    Extract relevant recent history to prepend as context.

    Args:
        messages: conversation history (list of {role, content})
        current_message: the current user message
        max_turns: max number of recent turns to include
        max_chars: max total characters for context

    Returns:
        Context string to prepend, or empty string
    """
    if not messages:
        return ""

    # Take last N messages (both user and assistant)
    recent = messages[-max_turns * 2:] if len(messages) > max_turns * 2 else messages

    context_parts = []
    total_chars = 0

    for msg in reversed(recent):
        role_label = "用户" if msg.get("role") == "user" else "助手"
        content = msg.get("content", "")
        if not content:
            continue

        # Truncate long messages
        if len(content) > 200:
            content = content[:200] + "..."

        part = f"[{role_label}]: {content}"
        if total_chars + len(part) > max_chars:
            break

        context_parts.insert(0, part)
        total_chars += len(part)

    if not context_parts:
        return ""

    return "\n".join(context_parts)


def enrich_message(
    message: str,
    history: List[Dict],
    max_turns: int = 3,
) -> Tuple[str, bool]:
    """
    Main entry point: enrich user message with context if needed.

    Returns:
        (enriched_message, was_enriched)
    """
    if not needs_context_enrichment(message):
        return message, False

    context = extract_relevant_history(history, message, max_turns=max_turns)
    if not context:
        return message, False

    enriched = f"[对话上下文]\n{context}\n\n[当前问题]\n{message}"
    return enriched, True
