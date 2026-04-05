from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


@dataclass(slots=True)
class Recipient:
    name: str
    address: str

    @classmethod
    def from_graph(cls, payload: dict[str, Any] | None) -> "Recipient | None":
        if not payload:
            return None
        address = payload.get("emailAddress", payload)
        raw_address = (address.get("address") or "").strip()
        if not raw_address:
            return None
        return cls(
            name=(address.get("name") or "").strip(),
            address=raw_address.lower(),
        )

    def matches(self, email_address: str) -> bool:
        return self.address.casefold() == email_address.casefold()


def parse_recipients(payload: list[dict[str, Any]] | None) -> list[Recipient]:
    recipients: list[Recipient] = []
    for item in payload or []:
        recipient = Recipient.from_graph(item)
        if recipient:
            recipients.append(recipient)
    return recipients


@dataclass(slots=True)
class MailboxMessage:
    id: str
    conversation_id: str | None
    subject: str
    from_recipient: Recipient | None
    to_recipients: list[Recipient] = field(default_factory=list)
    cc_recipients: list[Recipient] = field(default_factory=list)
    received_at: datetime | None = None
    is_read: bool = False
    is_draft: bool = False
    categories: list[str] = field(default_factory=list)
    body_preview: str = ""
    body_content: str = ""
    body_content_type: str = "html"
    headers: dict[str, str] = field(default_factory=dict)
    web_link: str | None = None
    inference_classification: str | None = None
    parent_folder_id: str | None = None
    conversation_index: str | None = None

    @classmethod
    def from_graph(cls, payload: dict[str, Any]) -> "MailboxMessage":
        body = payload.get("body") or {}
        raw_headers = payload.get("internetMessageHeaders") or []
        headers = {
            str(item.get("name", "")).lower(): str(item.get("value", "")).strip()
            for item in raw_headers
            if item.get("name")
        }
        return cls(
            id=payload["id"],
            conversation_id=payload.get("conversationId"),
            subject=payload.get("subject") or "",
            from_recipient=Recipient.from_graph(payload.get("from")),
            to_recipients=parse_recipients(payload.get("toRecipients")),
            cc_recipients=parse_recipients(payload.get("ccRecipients")),
            received_at=parse_datetime(payload.get("receivedDateTime")),
            is_read=bool(payload.get("isRead", False)),
            is_draft=bool(payload.get("isDraft", False)),
            categories=list(payload.get("categories") or []),
            body_preview=payload.get("bodyPreview") or "",
            body_content=body.get("content") or "",
            body_content_type=(body.get("contentType") or "html").lower(),
            headers=headers,
            web_link=payload.get("webLink"),
            inference_classification=payload.get("inferenceClassification"),
            parent_folder_id=payload.get("parentFolderId"),
            conversation_index=payload.get("conversationIndex"),
        )

    @property
    def sender_address(self) -> str:
        return self.from_recipient.address if self.from_recipient else ""

    @property
    def sender_name(self) -> str:
        return self.from_recipient.name if self.from_recipient else ""

    def all_recipients(self) -> list[Recipient]:
        return [*self.to_recipients, *self.cc_recipients]


@dataclass(slots=True)
class FilterDecision:
    should_draft: bool
    primary_reason: str
    reasons: list[str] = field(default_factory=list)
    score: int = 0
    matched_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_draft": self.should_draft,
            "primary_reason": self.primary_reason,
            "reasons": self.reasons,
            "score": self.score,
            "matched_signals": self.matched_signals,
        }


@dataclass(slots=True)
class ThreadContext:
    conversation_id: str | None
    related_messages: list[MailboxMessage] = field(default_factory=list)


@dataclass(slots=True)
class StyleExample:
    reply_message_id: str
    correspondent_email: str
    subject: str
    inbound_text: str
    reply_text: str
    source: str


@dataclass(slots=True)
class DraftResult:
    id: str
    conversation_id: str | None
    web_link: str | None

    @classmethod
    def from_graph(cls, payload: dict[str, Any]) -> "DraftResult":
        return cls(
            id=payload["id"],
            conversation_id=payload.get("conversationId"),
            web_link=payload.get("webLink"),
        )


@dataclass(slots=True)
class StateRecord:
    source_message_id: str
    conversation_id: str | None
    subject: str
    received_timestamp: str | None
    action: str
    reason: str
    created_draft_id: str | None
    updated_at: str


@dataclass(slots=True)
class ScanReport:
    examined: int = 0
    skipped: int = 0
    drafted: int = 0
    errors: int = 0
