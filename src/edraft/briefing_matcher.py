from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from edraft.email_cache import EmailCacheStore
from edraft.models import BriefingEmailItem, CalendarEvent
from edraft.text_utils import to_prompt_text


STOPWORDS = {
    "about",
    "after",
    "before",
    "from",
    "meeting",
    "with",
    "your",
    "this",
    "that",
    "and",
    "the",
    "for",
    "you",
}


@dataclass(slots=True)
class RelatedEmailMatch:
    email: BriefingEmailItem
    score: float
    reasons: list[str]


class RelatedEmailMatcher:
    def __init__(self, store: EmailCacheStore, *, top_k: int) -> None:
        self.store = store
        self.top_k = top_k

    def match_for_event(self, event: CalendarEvent) -> list[RelatedEmailMatch]:
        keywords = extract_event_keywords(event)
        participants = event_participant_emails(event)
        candidates = self.store.search_related_messages(
            keywords=keywords,
            participant_emails=participants,
            before=event.start,
            limit=max(self.top_k * 8, 20),
        )
        matches = [self._score(event, keywords, participants, item) for item in candidates]
        matches = [match for match in matches if match.score > 0]
        matches.sort(key=lambda match: match.score, reverse=True)
        return matches[: self.top_k]

    def _score(
        self,
        event: CalendarEvent,
        keywords: list[str],
        participants: list[str],
        item: BriefingEmailItem,
    ) -> RelatedEmailMatch:
        reasons: list[str] = []
        score = 0.0
        text = f"{item.subject}\n{item.body_preview}\n{item.normalized_body_text}".casefold()
        keyword_hits = [keyword for keyword in keywords if keyword.casefold() in text]
        if keyword_hits:
            score += min(len(keyword_hits), 5) * 1.0
            reasons.append("keyword:" + ",".join(keyword_hits[:4]))
        if item.sender_email and item.sender_email.casefold() in participants:
            score += 2.0
            reasons.append("sender_attendee_overlap")
        if item.direction == "outbound" and participants:
            score += 0.5
            reasons.append("recent_outbound_context")
        message_time = _item_time(item)
        if message_time is not None:
            age_days = max((event.start - message_time).total_seconds() / 86400, 0.0)
            if age_days <= 14:
                score += max(0.0, 1.5 - (age_days / 14))
                reasons.append("recent_before_meeting")
        return RelatedEmailMatch(email=item, score=round(score, 3), reasons=reasons)


def extract_event_keywords(event: CalendarEvent) -> list[str]:
    body = to_prompt_text(event.body_content, event.body_content_type) if event.body_content else ""
    text = f"{event.subject}\n{event.body_preview}\n{body}".casefold()
    keywords: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text):
        if token in STOPWORDS or token in keywords:
            continue
        keywords.append(token)
        if len(keywords) >= 12:
            break
    return keywords


def event_participant_emails(event: CalendarEvent) -> list[str]:
    emails = []
    if event.organizer and event.organizer.address:
        emails.append(event.organizer.address.casefold())
    for attendee in event.attendees:
        if attendee.address:
            emails.append(attendee.address.casefold())
    return list(dict.fromkeys(emails))


def _item_time(item: BriefingEmailItem) -> datetime | None:
    value = item.received_at or item.sent_at
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
