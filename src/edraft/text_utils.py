from __future__ import annotations

import html
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


def plain_text_to_html(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""

    blocks = re.split(r"\n\s*\n", normalized)
    html_blocks: list[str] = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if len(lines) >= 2 and looks_like_signature(lines[-2:]):
            body_lines = lines[:-2]
            if body_lines:
                html_blocks.extend(_render_non_signature_lines(body_lines))
            html_blocks.append(f"<p>{'<br>'.join(html.escape(line) for line in lines[-2:])}</p>")
            continue
        html_blocks.extend(_render_non_signature_lines(lines))

    return "".join(html_blocks)


def _render_non_signature_lines(lines: list[str]) -> list[str]:
    html_blocks: list[str] = []
    if all(re.match(r"^\d+\.\s+", line) for line in lines):
        items = "".join(
            f"<li>{html.escape(re.sub(r'^\\d+\\.\\s+', '', line).strip())}</li>"
            for line in lines
        )
        html_blocks.append(f"<ol>{items}</ol>")
        return html_blocks
    if all(re.match(r"^[-*]\s+", line) for line in lines):
        items = "".join(
            f"<li>{html.escape(re.sub(r'^[-*]\\s+', '', line).strip())}</li>"
            for line in lines
        )
        html_blocks.append(f"<ul>{items}</ul>")
        return html_blocks

    if len(lines) == 1:
        for paragraph in split_long_paragraph(lines[0]):
            html_blocks.append(f"<p>{html.escape(paragraph)}</p>")
    else:
        escaped = "<br>".join(html.escape(line) for line in lines)
        html_blocks.append(f"<p>{escaped}</p>")
    return html_blocks


def split_long_paragraph(text: str) -> list[str]:
    stripped = text.strip()
    if len(stripped) < 280:
        return [stripped]
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", stripped)
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    if len(sentences) < 4:
        return [stripped]
    return [" ".join(sentences[index : index + 2]) for index in range(0, len(sentences), 2)]


def looks_like_signature(lines: list[str]) -> bool:
    if len(lines) != 2:
        return False
    closers = {"thanks", "thanks,", "best", "best,", "regards", "regards,", "sincerely", "sincerely,"}
    first = lines[0].strip().casefold()
    second = lines[1].strip()
    return first in closers and 1 <= len(second.split()) <= 3
