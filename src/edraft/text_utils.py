from __future__ import annotations

import re

from bs4 import BeautifulSoup


_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINE_RE = re.compile(r"\n{3,}")


def normalize_whitespace(text: str) -> str:
    collapsed_lines = []
    for line in text.splitlines():
        collapsed_lines.append(_SPACE_RE.sub(" ", line).strip())
    normalized = "\n".join(collapsed_lines)
    normalized = _BLANK_LINE_RE.sub("\n\n", normalized)
    return normalized.strip()


def html_to_text(content: str) -> str:
    if not content:
        return ""
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return normalize_whitespace(soup.get_text("\n"))


def to_prompt_text(content: str, content_type: str) -> str:
    if content_type.lower() == "text":
        return normalize_whitespace(content)
    return html_to_text(content)


def truncate_text(text: str, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    if max_chars <= 3:
        return stripped[:max_chars]
    return f"{stripped[: max_chars - 3].rstrip()}..."
