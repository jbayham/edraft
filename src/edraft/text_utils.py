from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup


_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINE_RE = re.compile(r"\n{3,}")
_HEADER_LINE_RE = re.compile(r"^(from|sent|to|cc|subject|date)\s*:", flags=re.IGNORECASE)
_ON_WROTE_RE = re.compile(r"^on .+ wrote:\s*$", flags=re.IGNORECASE)
_ORIGINAL_MESSAGE_RE = re.compile(r"^-{2,}\s*original message\s*-{2,}$", flags=re.IGNORECASE)
_AUTO_SIGNATURE_PATTERNS = [
    re.compile(r"^get\s+outlook\s+for\s+(ios|android)$", flags=re.IGNORECASE),
    re.compile(r"^sent\s+from\s+my\s+(iphone|ipad|android|galaxy|mobile device)$", flags=re.IGNORECASE),
    re.compile(r"^sent\s+from\s+outlook\s+for\s+(ios|android)$", flags=re.IGNORECASE),
]


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


def html_to_authored_text(content: str) -> str:
    if not content:
        return ""
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    body = soup.body or soup
    separator = body.find(id="divRplyFwdMsg")
    if separator is not None:
        anchor = separator
        previous = separator.previous_sibling
        while previous is not None and isinstance(previous, str) and previous.strip() == "":
            candidate = previous
            previous = previous.previous_sibling
            candidate.extract()
        if previous is not None and getattr(previous, "name", None) == "hr":
            anchor = previous

        node = anchor
        while node is not None:
            next_node = node.next_sibling
            node.extract()
            node = next_node

    for selector in (
        {"class": "gmail_quote"},
        {"class": "protonmail_quote"},
        {"type": "cite"},
    ):
        for node in body.find_all(attrs=selector):
            node.decompose()

    return strip_quoted_reply_text(normalize_whitespace(body.get_text("\n")))


def to_prompt_text(content: str, content_type: str) -> str:
    if content_type.lower() == "text":
        return normalize_whitespace(content)
    return html_to_text(content)


def extract_authored_email_text(content: str, content_type: str) -> str:
    if content_type.lower() == "text":
        return strip_auto_signature(strip_quoted_reply_text(normalize_whitespace(content)))
    return strip_auto_signature(html_to_authored_text(content))


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


def strip_quoted_reply_text(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        if _ON_WROTE_RE.match(line) or _ORIGINAL_MESSAGE_RE.match(line):
            return "\n".join(lines[:index]).strip()
        if _HEADER_LINE_RE.match(line) and _looks_like_quoted_header_block(lines, index):
            return "\n".join(lines[:index]).strip()
    return "\n".join(lines).strip()


def strip_auto_signature(text: str) -> str:
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    while lines:
        joined_tail = "\n".join(line.strip() for line in lines[-2:] if line.strip())
        if joined_tail and any(pattern.match(joined_tail) for pattern in _AUTO_SIGNATURE_PATTERNS):
            lines = lines[:-2]
            while lines and not lines[-1].strip():
                lines.pop()
            continue
        tail = lines[-1].strip()
        if tail and any(pattern.match(tail) for pattern in _AUTO_SIGNATURE_PATTERNS):
            lines.pop()
            while lines and not lines[-1].strip():
                lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def _looks_like_quoted_header_block(lines: list[str], start_index: int) -> bool:
    header_names: set[str] = set()
    for raw_line in lines[start_index : start_index + 12]:
        line = raw_line.strip()
        if not line:
            continue
        match = _HEADER_LINE_RE.match(line)
        if match:
            header_names.add(match.group(1).casefold())
    return (
        len(header_names) >= 2
        and "from" in header_names
        and bool({"sent", "date", "subject", "to"} & header_names)
    )
