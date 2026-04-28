from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openai import OpenAI

from edraft.briefing_matcher import RelatedEmailMatch, RelatedEmailMatcher
from edraft.briefing_sync import BriefingEmailSyncer
from edraft.config import AppConfig
from edraft.email_cache import EmailCacheStore
from edraft.graph_client import GraphClient
from edraft.models import (
    BriefingCalendarEvent,
    BriefingDraftCandidate,
    BriefingEmailItem,
    BriefingFollowUp,
    BriefingRequest,
    BriefingResult,
    BriefingSection,
    CalendarEvent,
    SourceReference,
)
from edraft.text_utils import to_prompt_text, truncate_text


class BriefingGenerationError(RuntimeError):
    """Raised when a daily briefing cannot be generated."""


class DailyBriefingService:
    def __init__(
        self,
        *,
        config: AppConfig,
        graph_client: GraphClient,
        store: EmailCacheStore,
        syncer: BriefingEmailSyncer,
        generator: "BriefingGenerator",
    ) -> None:
        self.config = config
        self.graph_client = graph_client
        self.store = store
        self.syncer = syncer
        self.generator = generator
        self.logger = logging.getLogger("edraft.briefing")

    def generate(
        self,
        *,
        target_date: date | None = None,
        regenerate: bool = False,
    ) -> BriefingResult:
        tz = resolve_timezone(self.config.briefing.timezone)
        target = target_date or datetime.now(tz).date()
        request = BriefingRequest(
            briefing_date=target.isoformat(),
            timezone=tz.key if isinstance(tz, ZoneInfo) else str(tz),
            email_lookback_days=self.config.briefing.email_lookback_days,
            related_emails_per_event=self.config.briefing.related_emails_per_event,
            max_emails=self.config.briefing.max_emails,
            regenerate=regenerate,
        )
        if self.config.briefing.cache_enabled and not regenerate:
            cached = self.store.latest_briefing(request.briefing_date)
            if cached:
                return result_from_dict(cached["result"])

        sync_report = self.syncer.sync_recent_email(force=regenerate)
        start_local = datetime.combine(target, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        events = [
            event
            for event in self.graph_client.list_calendar_view(start_local, end_local, limit=200)
            if not event.is_cancelled
        ]
        lookback_start = datetime.now(timezone.utc) - timedelta(
            days=self.config.briefing.email_lookback_days
        )
        triage = self.store.triage_candidates(
            since=lookback_start,
            limit=min(self.config.briefing.max_emails, 30),
        )
        follow_up_items = self.store.follow_up_candidates(
            stale_after=datetime.now(timezone.utc) - timedelta(days=3),
            limit=20,
        )
        matcher = RelatedEmailMatcher(
            self.store,
            top_k=self.config.briefing.related_emails_per_event,
        )
        related_by_event = {event.id: matcher.match_for_event(event) for event in events}
        result = self.generator.generate(
            request=request,
            events=events,
            triage=triage,
            follow_up_items=follow_up_items,
            related_by_event=related_by_event,
        )
        self.store.save_briefing_run(
            result,
            request_json=request.to_dict(),
            email_sync_summary=sync_report.to_dict(),
        )
        for event in events:
            self.store.record_event_links(
                briefing_run_id=result.briefing_run_id,
                event_id=event.id,
                links=[
                    (match.email, match.score, match.reasons)
                    for match in related_by_event.get(event.id, [])
                ],
            )
        self.logger.info(
            "Daily briefing generated",
            extra={
                "event": "briefing_generated",
                "briefing_run_id": result.briefing_run_id,
                "briefing_date": result.briefing_date,
                "calendar_event_count": len(events),
                "triage_count": len(triage),
                "follow_up_count": len(follow_up_items),
            },
        )
        return result


class BriefingGenerator:
    def __init__(
        self,
        config: AppConfig,
        *,
        client: OpenAI | None = None,
    ) -> None:
        if config.llm.provider != "openai":
            raise BriefingGenerationError(
                f"Unsupported LLM provider '{config.llm.provider}'. This MVP supports only OpenAI."
            )
        api_key = os.getenv("OPENAI_API_KEY")
        if client is None and not api_key:
            raise BriefingGenerationError("OPENAI_API_KEY must be set to generate briefings.")
        self.config = config
        self.client = client or OpenAI(api_key=api_key)

    def generate(
        self,
        *,
        request: BriefingRequest,
        events: list[CalendarEvent],
        triage: list[BriefingEmailItem],
        follow_up_items: list[BriefingEmailItem],
        related_by_event: dict[str, list[RelatedEmailMatch]],
    ) -> BriefingResult:
        context = build_briefing_context(
            request=request,
            events=events,
            triage=triage,
            follow_up_items=follow_up_items,
            related_by_event=related_by_event,
        )
        prompt = build_briefing_prompt(context)
        payload = self._call_llm(prompt)
        result = result_from_llm_payload(
            payload,
            request=request,
            model=self.config.briefing.model or self.config.llm.model,
            context=context,
        )
        result.markdown = render_briefing_markdown(result)
        return result

    def _call_llm(self, prompt: dict[str, str]) -> dict[str, Any]:
        try:
            request_kwargs = {
                "model": self.config.briefing.model or self.config.llm.model,
                "input": [
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": prompt["user"]},
                ],
                "store": False,
            }
            if self.config.llm.reasoning_effort:
                request_kwargs["reasoning"] = {"effort": self.config.llm.reasoning_effort}
            response = self.client.responses.create(**request_kwargs)
        except Exception as exc:
            raise BriefingGenerationError(f"LLM request failed: {exc}") from exc
        output = (getattr(response, "output_text", "") or "").strip()
        if not output:
            raise BriefingGenerationError("LLM response did not contain text output")
        try:
            return json.loads(_strip_json_fence(output))
        except json.JSONDecodeError as exc:
            raise BriefingGenerationError("LLM briefing response was not valid JSON") from exc


def build_briefing_context(
    *,
    request: BriefingRequest,
    events: list[CalendarEvent],
    triage: list[BriefingEmailItem],
    follow_up_items: list[BriefingEmailItem],
    related_by_event: dict[str, list[RelatedEmailMatch]],
) -> dict[str, Any]:
    return {
        "request": request.to_dict(),
        "calendar_events": [
            {
                **calendar_event_to_briefing(event).to_dict(),
                "body_text": truncate_text(
                    to_prompt_text(event.body_content, event.body_content_type)
                    if event.body_content
                    else event.body_preview,
                    700,
                ),
                "related_emails": [
                    {
                        **email_item_for_prompt(match.email),
                        "match_score": match.score,
                        "match_reasons": match.reasons,
                    }
                    for match in related_by_event.get(event.id, [])
                ],
            }
            for event in events
        ],
        "email_triage_candidates": [email_item_for_prompt(item) for item in triage],
        "follow_up_candidates": [email_item_for_prompt(item) for item in follow_up_items],
    }


def build_briefing_prompt(context: dict[str, Any]) -> dict[str, str]:
    system = "\n".join(
        [
            "You prepare concise Chief of Staff daily briefings for a human reviewer.",
            "Use only facts present in the supplied Microsoft Graph email and calendar context.",
            "Do not invent deadlines, attendees, decisions, links, or commitments.",
            "Separate confirmed facts from inferred priorities and suggested actions.",
            "Cite source email message IDs and calendar event IDs inside the JSON fields.",
            "Return JSON only. Do not wrap it in Markdown.",
            "Style: concise, professional, action-oriented.",
        ]
    )
    user = "\n".join(
        [
            "Create a daily briefing with this exact JSON shape:",
            json.dumps(_output_schema(), indent=2),
            "",
            "Input context:",
            json.dumps(context, indent=2),
        ]
    )
    return {"system": system, "user": user}


def result_from_llm_payload(
    payload: dict[str, Any],
    *,
    request: BriefingRequest,
    model: str,
    context: dict[str, Any],
) -> BriefingResult:
    run_id = str(uuid.uuid4())
    sections = [
        BriefingSection(
            title=str(section.get("title") or "Briefing"),
            content_markdown=str(section.get("content_markdown") or "").strip(),
            source_references=[
                source_from_payload(item) for item in section.get("source_references", [])
            ],
        )
        for section in payload.get("sections", [])
    ]
    if not sections:
        sections = fallback_sections(context)
    source_refs = collect_source_references(context, sections)
    return BriefingResult(
        briefing_run_id=run_id,
        briefing_date=request.briefing_date,
        timezone=request.timezone,
        model=model,
        sections=sections,
        calendar_events=[
            briefing_event_from_payload(event)
            for event in _safe_list(payload.get("calendar_events"))
            if "id" in event and "subject" in event and "start" in event and "end" in event
        ],
        email_triage=[
            email_item_from_payload(item)
            for item in _safe_list(payload.get("email_triage"))
            if item.get("graph_message_id")
        ],
        follow_ups=[
            BriefingFollowUp(
                person=str(item.get("person") or "Unknown"),
                direction=str(item.get("direction") or "follow_up"),
                subject=str(item.get("subject") or ""),
                suggested_next_action=str(item.get("suggested_next_action") or ""),
                source_references=[
                    source_from_payload(source)
                    for source in _safe_list(item.get("source_references"))
                ],
            )
            for item in _safe_list(payload.get("follow_ups"))
        ],
        draft_candidates=[
            BriefingDraftCandidate(
                graph_message_id=str(item.get("graph_message_id")),
                conversation_id=item.get("conversation_id"),
                subject=str(item.get("subject") or ""),
                sender_email=item.get("sender_email"),
                rationale=str(item.get("rationale") or ""),
                web_link=item.get("web_link"),
            )
            for item in _safe_list(payload.get("draft_candidates"))
            if item.get("graph_message_id")
        ],
        source_references=source_refs,
    )


def result_from_dict(payload: dict[str, Any]) -> BriefingResult:
    return BriefingResult(
        briefing_run_id=payload["briefing_run_id"],
        briefing_date=payload["briefing_date"],
        timezone=payload["timezone"],
        model=payload["model"],
        sections=[
            BriefingSection(
                title=section["title"],
                content_markdown=section["content_markdown"],
                source_references=[
                    source_from_payload(source)
                    for source in section.get("source_references", [])
                ],
            )
            for section in payload.get("sections", [])
        ],
        calendar_events=[
            briefing_event_from_payload(event) for event in payload.get("calendar_events", [])
        ],
        email_triage=[
            email_item_from_payload(item) for item in payload.get("email_triage", [])
        ],
        follow_ups=[
            BriefingFollowUp(
                person=item["person"],
                direction=item["direction"],
                subject=item["subject"],
                suggested_next_action=item["suggested_next_action"],
                source_references=[
                    source_from_payload(source)
                    for source in item.get("source_references", [])
                ],
            )
            for item in payload.get("follow_ups", [])
        ],
        draft_candidates=[
            BriefingDraftCandidate(**item) for item in payload.get("draft_candidates", [])
        ],
        source_references=[
            source_from_payload(source) for source in payload.get("source_references", [])
        ],
        markdown=payload.get("markdown", ""),
    )


def render_briefing_markdown(result: BriefingResult) -> str:
    lines = [f"# Chief of Staff Briefing - {result.briefing_date}", ""]
    for section in result.sections:
        lines.extend([f"## {section.title}", "", section.content_markdown.strip() or "_No items._", ""])
    if result.draft_candidates:
        lines.extend(["## Draftable Actions", ""])
        for candidate in result.draft_candidates:
            link = f" [{candidate.web_link}]({candidate.web_link})" if candidate.web_link else ""
            lines.append(f"- `{candidate.graph_message_id}` - {candidate.subject}: {candidate.rationale}{link}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def calendar_event_to_briefing(event: CalendarEvent) -> BriefingCalendarEvent:
    return BriefingCalendarEvent(
        id=event.id,
        subject=event.subject,
        start=event.start.isoformat(),
        end=event.end.isoformat(),
        organizer_email=event.organizer.address if event.organizer else None,
        organizer_name=event.organizer.name if event.organizer else None,
        attendee_emails=[attendee.address for attendee in event.attendees],
        location=event.location,
        body_preview=event.body_preview,
        online_meeting_url=event.online_meeting_url,
        web_link=event.web_link,
        show_as=event.show_as,
        is_cancelled=event.is_cancelled,
    )


def email_item_for_prompt(item: BriefingEmailItem) -> dict[str, Any]:
    return {
        "graph_message_id": item.graph_message_id,
        "conversation_id": item.conversation_id,
        "subject": item.subject,
        "sender_name": item.sender_name,
        "sender_email": item.sender_email,
        "received_at": item.received_at,
        "sent_at": item.sent_at,
        "body_preview": truncate_text(item.body_preview, 500),
        "normalized_body_text": truncate_text(item.normalized_body_text, 1000),
        "importance": item.importance,
        "is_read": item.is_read,
        "has_attachments": item.has_attachments,
        "web_link": item.web_link,
        "direction": item.direction,
        "rationale": item.rationale,
    }


def email_item_from_payload(item: dict[str, Any]) -> BriefingEmailItem:
    return BriefingEmailItem(
        graph_message_id=str(item.get("graph_message_id")),
        conversation_id=item.get("conversation_id"),
        subject=str(item.get("subject") or ""),
        sender_name=item.get("sender_name"),
        sender_email=item.get("sender_email"),
        received_at=item.get("received_at"),
        sent_at=item.get("sent_at"),
        body_preview=str(item.get("body_preview") or ""),
        normalized_body_text=str(item.get("normalized_body_text") or ""),
        importance=item.get("importance"),
        is_read=bool(item.get("is_read", True)),
        has_attachments=bool(item.get("has_attachments", False)),
        web_link=item.get("web_link"),
        direction=str(item.get("direction") or "inbound"),
        source_folder=str(item.get("source_folder") or ""),
        rationale=str(item.get("rationale") or ""),
        score=float(item.get("score") or 0.0),
    )


def briefing_event_from_payload(item: dict[str, Any]) -> BriefingCalendarEvent:
    return BriefingCalendarEvent(
        id=str(item.get("id")),
        subject=str(item.get("subject") or ""),
        start=str(item.get("start") or ""),
        end=str(item.get("end") or ""),
        organizer_email=item.get("organizer_email"),
        organizer_name=item.get("organizer_name"),
        attendee_emails=list(item.get("attendee_emails") or []),
        location=item.get("location"),
        body_preview=str(item.get("body_preview") or ""),
        online_meeting_url=item.get("online_meeting_url"),
        web_link=item.get("web_link"),
        show_as=item.get("show_as"),
        is_cancelled=bool(item.get("is_cancelled", False)),
    )


def source_from_payload(item: dict[str, Any]) -> SourceReference:
    return SourceReference(
        source_type=str(item.get("source_type") or item.get("type") or "unknown"),
        source_id=str(item.get("source_id") or item.get("id") or ""),
        title=item.get("title"),
        web_link=item.get("web_link"),
    )


def collect_source_references(
    context: dict[str, Any],
    sections: list[BriefingSection],
) -> list[SourceReference]:
    sources: dict[tuple[str, str], SourceReference] = {}
    for event in context.get("calendar_events", []):
        sources[("event", event["id"])] = SourceReference(
            source_type="event",
            source_id=event["id"],
            title=event.get("subject"),
            web_link=event.get("web_link"),
        )
        for email in event.get("related_emails", []):
            sources[("email", email["graph_message_id"])] = SourceReference(
                source_type="email",
                source_id=email["graph_message_id"],
                title=email.get("subject"),
                web_link=email.get("web_link"),
            )
    for email in context.get("email_triage_candidates", []):
        sources[("email", email["graph_message_id"])] = SourceReference(
            source_type="email",
            source_id=email["graph_message_id"],
            title=email.get("subject"),
            web_link=email.get("web_link"),
        )
    for section in sections:
        for source in section.source_references:
            sources[(source.source_type, source.source_id)] = source
    return list(sources.values())


def fallback_sections(context: dict[str, Any]) -> list[BriefingSection]:
    events = context.get("calendar_events", [])
    triage = context.get("email_triage_candidates", [])
    summary_lines = [
        f"- {len(events)} calendar event(s) found for the target date.",
        f"- {len(triage)} email triage candidate(s) found in the cached lookback window.",
    ]
    calendar_lines = [
        f"- {event['start']} - {event['subject']} ({event.get('organizer_email') or 'unknown organizer'})"
        for event in events
    ] or ["_No calendar events found._"]
    triage_lines = [
        f"- `{item['graph_message_id']}` {item['subject']} from {item.get('sender_email') or 'unknown'}: {item.get('rationale') or 'recent message'}"
        for item in triage[:10]
    ] or ["_No email triage candidates found._"]
    return [
        BriefingSection("Executive summary", "\n".join(summary_lines)),
        BriefingSection("Today's calendar", "\n".join(calendar_lines)),
        BriefingSection("Email triage", "\n".join(triage_lines)),
    ]


def resolve_timezone(configured: str | None) -> ZoneInfo | timezone:
    if configured:
        try:
            return ZoneInfo(configured)
        except ZoneInfoNotFoundError as exc:
            raise BriefingGenerationError(f"Unknown briefing timezone: {configured}") from exc
    local_tz = datetime.now().astimezone().tzinfo
    return local_tz or timezone.utc


def _output_schema() -> dict[str, Any]:
    return {
        "sections": [
            {
                "title": "Executive summary",
                "content_markdown": "3-6 concise bullets",
                "source_references": [
                    {"source_type": "email|event", "source_id": "Graph ID", "title": "optional", "web_link": "optional"}
                ],
            }
        ],
        "calendar_events": ["normalized event objects with source IDs"],
        "email_triage": ["email item objects with rationale"],
        "follow_ups": [
            {
                "person": "name or email",
                "direction": "i_owe|owes_me|revisit",
                "subject": "thread subject",
                "suggested_next_action": "short action",
                "source_references": [],
            }
        ],
        "draft_candidates": [
            {
                "graph_message_id": "message ID",
                "conversation_id": "conversation ID",
                "subject": "subject",
                "sender_email": "sender",
                "rationale": "why a draft would help",
                "web_link": "optional",
            }
        ],
    }


def _strip_json_fence(text: str) -> str:
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    return match.group(1).strip() if match else text


def _safe_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
