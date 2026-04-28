from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
from typing import Any


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    offset_match = re.search(r"([+-]\d\d:\d\d)$", normalized)
    offset = offset_match.group(1) if offset_match else ""
    core = normalized[: -len(offset)] if offset else normalized
    if "." in core:
        prefix, suffix = core.split(".", 1)
        fractional = "".join(character for character in suffix if character.isdigit())
        if len(fractional) > 6:
            fractional = fractional[:6]
        core = f"{prefix}.{fractional}"
    normalized = f"{core}{offset}"
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
    internet_message_id: str | None = None
    to_recipients: list[Recipient] = field(default_factory=list)
    cc_recipients: list[Recipient] = field(default_factory=list)
    received_at: datetime | None = None
    sent_at: datetime | None = None
    last_modified_at: datetime | None = None
    is_read: bool = False
    is_draft: bool = False
    importance: str | None = None
    has_attachments: bool = False
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
            internet_message_id=payload.get("internetMessageId"),
            subject=payload.get("subject") or "",
            from_recipient=Recipient.from_graph(payload.get("from")),
            to_recipients=parse_recipients(payload.get("toRecipients")),
            cc_recipients=parse_recipients(payload.get("ccRecipients")),
            received_at=parse_datetime(payload.get("receivedDateTime")),
            sent_at=parse_datetime(payload.get("sentDateTime")),
            last_modified_at=parse_datetime(payload.get("lastModifiedDateTime")),
            is_read=bool(payload.get("isRead", False)),
            is_draft=bool(payload.get("isDraft", False)),
            importance=payload.get("importance"),
            has_attachments=bool(payload.get("hasAttachments", False)),
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
class CalendarEvent:
    id: str
    subject: str
    start: datetime
    end: datetime
    organizer: Recipient | None = None
    attendees: list[Recipient] = field(default_factory=list)
    location: str | None = None
    body_preview: str = ""
    body_content: str = ""
    body_content_type: str = "html"
    online_meeting_url: str | None = None
    is_all_day: bool = False
    is_cancelled: bool = False
    show_as: str | None = None
    web_link: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "organizer": self.organizer.address if self.organizer else None,
            "attendees": [attendee.address for attendee in self.attendees],
            "location": self.location,
            "body_preview": self.body_preview,
            "online_meeting_url": self.online_meeting_url,
            "is_all_day": self.is_all_day,
            "is_cancelled": self.is_cancelled,
            "show_as": self.show_as,
            "web_link": self.web_link,
        }


@dataclass(slots=True)
class MeetingIntent:
    is_meeting_request: bool
    needs_clarification: bool
    confidence: float
    summary: str
    duration_minutes: int
    constraints: list[str] = field(default_factory=list)
    requested_window: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_meeting_request": self.is_meeting_request,
            "needs_clarification": self.needs_clarification,
            "confidence": self.confidence,
            "summary": self.summary,
            "duration_minutes": self.duration_minutes,
            "constraints": self.constraints,
            "requested_window": self.requested_window,
            "reason": self.reason,
        }


@dataclass(slots=True)
class MeetingSlot:
    start: datetime
    end: datetime
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
        }


@dataclass(slots=True)
class MeetingPlan:
    intent: MeetingIntent
    query_start: datetime | None
    query_end: datetime | None
    suggested_slots: list[MeetingSlot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "query_start": self.query_start.isoformat() if self.query_start else None,
            "query_end": self.query_end.isoformat() if self.query_end else None,
            "suggested_slots": [slot.to_dict() for slot in self.suggested_slots],
        }


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
class MeetingSuggestion:
    source_message_id: str
    conversation_id: str | None
    subject: str
    sender_email: str
    intent: dict[str, Any]
    query_start: str | None
    query_end: str | None
    suggested_slots: list[dict[str, Any]]
    generated_reply: str
    updated_at: str


@dataclass(slots=True)
class ScanReport:
    examined: int = 0
    skipped: int = 0
    drafted: int = 0
    errors: int = 0


@dataclass(slots=True)
class SourceReference:
    source_type: str
    source_id: str
    title: str | None = None
    web_link: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "web_link": self.web_link,
        }


@dataclass(slots=True)
class BriefingEmailItem:
    graph_message_id: str
    conversation_id: str | None
    subject: str
    sender_name: str | None
    sender_email: str | None
    received_at: str | None
    sent_at: str | None
    body_preview: str
    normalized_body_text: str
    importance: str | None
    is_read: bool
    has_attachments: bool
    web_link: str | None
    direction: str
    source_folder: str
    rationale: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BriefingCalendarEvent:
    id: str
    subject: str
    start: str
    end: str
    organizer_email: str | None = None
    organizer_name: str | None = None
    attendee_emails: list[str] = field(default_factory=list)
    location: str | None = None
    body_preview: str = ""
    online_meeting_url: str | None = None
    web_link: str | None = None
    show_as: str | None = None
    is_cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BriefingFollowUp:
    person: str
    direction: str
    subject: str
    suggested_next_action: str
    source_references: list[SourceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BriefingDraftCandidate:
    graph_message_id: str
    conversation_id: str | None
    subject: str
    sender_email: str | None
    rationale: str
    web_link: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BriefingSection:
    title: str
    content_markdown: str
    source_references: list[SourceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BriefingRequest:
    briefing_date: str
    timezone: str
    email_lookback_days: int
    related_emails_per_event: int
    max_emails: int
    regenerate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BriefingResult:
    briefing_run_id: str
    briefing_date: str
    timezone: str
    model: str
    sections: list[BriefingSection]
    calendar_events: list[BriefingCalendarEvent] = field(default_factory=list)
    email_triage: list[BriefingEmailItem] = field(default_factory=list)
    follow_ups: list[BriefingFollowUp] = field(default_factory=list)
    draft_candidates: list[BriefingDraftCandidate] = field(default_factory=list)
    source_references: list[SourceReference] = field(default_factory=list)
    markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "briefing_run_id": self.briefing_run_id,
            "briefing_date": self.briefing_date,
            "timezone": self.timezone,
            "model": self.model,
            "sections": [section.to_dict() for section in self.sections],
            "calendar_events": [event.to_dict() for event in self.calendar_events],
            "email_triage": [item.to_dict() for item in self.email_triage],
            "follow_ups": [item.to_dict() for item in self.follow_ups],
            "draft_candidates": [item.to_dict() for item in self.draft_candidates],
            "source_references": [source.to_dict() for source in self.source_references],
            "markdown": self.markdown,
        }
