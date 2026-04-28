from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence

from edraft.config import IdentityConfig, SchedulingConfig
from edraft.graph_client import GraphApiError, GraphClient
from edraft.models import CalendarEvent, MailboxMessage, MeetingIntent, MeetingPlan, MeetingSlot, ThreadContext
from edraft.text_utils import to_prompt_text, truncate_text


_MEETING_TERMS = (
    "schedule",
    "meeting",
    "meet",
    "call",
    "chat",
    "talk",
    "sync",
    "connect",
    "catch up",
    "catch-up",
    "coffee",
    "zoom",
    "teams",
)
_REQUEST_PHRASES = (
    "can we",
    "could we",
    "are you available",
    "do you have time",
    "what time works",
    "when works",
    "find a time",
    "schedule a time",
    "set up a time",
    "set up a meeting",
    "book a time",
)
_CONSTRAINT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:15|30|45|60)\s*(?:min|mins|minutes?|minute)\b", flags=re.IGNORECASE), "duration"),
    (re.compile(r"\bhalf\s+hour\b", flags=re.IGNORECASE), "duration"),
    (re.compile(r"\b\d+\s*(?:h|hr|hrs|hour|hours)\b", flags=re.IGNORECASE), "duration"),
    (re.compile(r"\b(?:this|next)\s+week\b", flags=re.IGNORECASE), "timeframe"),
    (re.compile(r"\b(?:today|tomorrow)\b", flags=re.IGNORECASE), "timeframe"),
    (re.compile(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", flags=re.IGNORECASE), "day"),
    (re.compile(r"\b(?:morning|afternoon|evening|midday|mid-morning|after lunch|before noon)\b", flags=re.IGNORECASE), "window"),
    (re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", flags=re.IGNORECASE), "time"),
)


@dataclass(slots=True)
class MeetingPlannerResult:
    plan: MeetingPlan
    clarifying: bool
    candidate_slots: list[MeetingSlot]

    def to_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan.to_dict(),
            "clarifying": self.clarifying,
            "candidate_slots": [slot.to_dict() for slot in self.candidate_slots],
        }


class SchedulingIntentAnalyzer:
    def __init__(self, identity: IdentityConfig | None = None) -> None:
        self.identity = identity

    def analyze(self, message: MailboxMessage, thread_context: ThreadContext | None = None) -> MeetingIntent:
        text = truncate_text(
            to_prompt_text(message.body_content, message.body_content_type)
            or message.body_preview
            or "",
            2000,
        )
        combined = f"{message.subject}\n{text}".casefold()

        term_hits = [term for term in _MEETING_TERMS if term in combined]
        request_hits = [phrase for phrase in _REQUEST_PHRASES if phrase in combined]
        constraint_hits = [label for pattern, label in _CONSTRAINT_PATTERNS if pattern.search(combined)]
        explicit_scheduling = bool(term_hits or request_hits)

        confidence = 0.0
        confidence += min(len(term_hits), 3) * 0.2
        confidence += min(len(request_hits), 2) * 0.25
        confidence += min(len(constraint_hits), 3) * 0.15
        if "?" in combined:
            confidence += 0.1
        if thread_context and thread_context.related_messages:
            confidence += 0.05
        confidence = min(confidence, 1.0)

        duration_minutes = self._infer_duration_minutes(combined)
        requested_window = self._infer_requested_window(constraint_hits, combined)
        summary = self._build_summary(
            explicit_scheduling=explicit_scheduling,
            duration_minutes=duration_minutes,
            requested_window=requested_window,
            term_hits=term_hits,
            request_hits=request_hits,
        )
        needs_clarification = not explicit_scheduling or confidence < 0.25
        reason = self._build_reason(explicit_scheduling, term_hits, request_hits, constraint_hits)
        return MeetingIntent(
            is_meeting_request=explicit_scheduling,
            needs_clarification=needs_clarification,
            confidence=confidence,
            summary=summary,
            duration_minutes=duration_minutes,
            constraints=sorted(set(constraint_hits)),
            requested_window=requested_window,
            reason=reason,
        )

    @staticmethod
    def _infer_duration_minutes(text: str) -> int:
        if re.search(r"\b(15|quarter\s+hour)\b", text, flags=re.IGNORECASE):
            return 15
        if re.search(r"\b(30|half\s+hour)\b", text, flags=re.IGNORECASE):
            return 30
        if re.search(r"\b(45)\b", text, flags=re.IGNORECASE):
            return 45
        if re.search(r"\b(60|1\s*(?:h|hr|hour))\b", text, flags=re.IGNORECASE):
            return 60
        match = re.search(r"\b(\d{1,2})\s*(?:h|hr|hrs|hour|hours)\b", text, flags=re.IGNORECASE)
        if match:
            hours = max(int(match.group(1)), 1)
            return min(hours * 60, 180)
        return 30

    @staticmethod
    def _infer_requested_window(constraint_hits: list[str], text: str) -> str | None:
        windows: list[str] = []
        for label in constraint_hits:
            if label not in windows:
                windows.append(label)
        if not windows:
            return None
        if "timeframe" in windows and "day" in windows:
            return "specific day or week"
        if "timeframe" in windows:
            return "timeframe"
        if "day" in windows and "window" in windows:
            return "day and time window"
        if "time" in windows:
            return "specific time"
        return ", ".join(windows)

    @staticmethod
    def _build_reason(
        explicit_scheduling: bool,
        term_hits: list[str],
        request_hits: list[str],
        constraint_hits: list[str],
    ) -> str:
        if not explicit_scheduling:
            return "no_scheduling_intent"
        parts = []
        if term_hits:
            parts.append(f"terms={','.join(term_hits[:3])}")
        if request_hits:
            parts.append(f"phrases={','.join(request_hits[:2])}")
        if constraint_hits:
            parts.append(f"constraints={','.join(sorted(set(constraint_hits)))}")
        return " | ".join(parts) if parts else "scheduling_intent_detected"

    @staticmethod
    def _build_summary(
        *,
        explicit_scheduling: bool,
        duration_minutes: int,
        requested_window: str | None,
        term_hits: Sequence[str],
        request_hits: Sequence[str],
    ) -> str:
        if not explicit_scheduling:
            return "No clear meeting scheduling request detected."
        details = [f"Sender is asking to schedule a meeting or call ({duration_minutes} minutes by default)."]
        if requested_window:
            details.append(f"Requested window: {requested_window}.")
        if request_hits:
            details.append("Request language: " + ", ".join(request_hits[:2]) + ".")
        elif term_hits:
            details.append("Meeting keywords detected: " + ", ".join(term_hits[:3]) + ".")
        return " ".join(details)


class CalendarAvailabilityService:
    def __init__(
        self,
        graph_client: GraphClient,
        config: SchedulingConfig,
        *,
        local_timezone: timezone | None = None,
    ) -> None:
        self.graph_client = graph_client
        self.config = config
        self.local_timezone = local_timezone or datetime.now().astimezone().tzinfo or timezone.utc

    def suggest_slots(
        self,
        *,
        start: datetime,
        end: datetime,
        duration_minutes: int,
        max_suggestions: int,
    ) -> list[MeetingSlot]:
        events = self.graph_client.list_calendar_view(start, end, limit=200)
        busy_blocks = self._merge_busy_blocks(self._extract_busy_blocks(events))
        slots: list[MeetingSlot] = []
        current_day = start.astimezone(self.local_timezone).date()
        last_day = end.astimezone(self.local_timezone).date()
        while current_day <= last_day and len(slots) < max_suggestions:
            if self.config.weekdays_only and current_day.weekday() >= 5:
                current_day = current_day + timedelta(days=1)
                continue
            day_start = self._combine(current_day, self.config.business_hours_start)
            day_end = self._combine(current_day, self.config.business_hours_end)
            window_start = max(day_start, start.astimezone(self.local_timezone))
            window_end = min(day_end, end.astimezone(self.local_timezone))
            if window_end > window_start:
                free_ranges = self._subtract_busy(window_start, window_end, busy_blocks)
                for free_start, free_end in free_ranges:
                    candidate = self._ceil_to_step(free_start, self.config.slot_step_minutes)
                    while candidate + timedelta(minutes=duration_minutes) <= free_end:
                        slot = MeetingSlot(
                            start=candidate,
                            end=candidate + timedelta(minutes=duration_minutes),
                            label=self._format_slot(candidate, duration_minutes),
                        )
                        slots.append(slot)
                        if len(slots) >= max_suggestions:
                            break
                        candidate += timedelta(minutes=self.config.slot_step_minutes)
                    if len(slots) >= max_suggestions:
                        break
            current_day = current_day + timedelta(days=1)
        return slots

    def _extract_busy_blocks(self, events: Sequence[CalendarEvent]) -> list[tuple[datetime, datetime]]:
        busy: list[tuple[datetime, datetime]] = []
        for event in events:
            show_as = (event.show_as or "busy").casefold()
            if show_as == "free":
                continue
            start = event.start.astimezone(self.local_timezone)
            end = event.end.astimezone(self.local_timezone)
            if end <= start:
                continue
            if event.is_all_day:
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                end = end.replace(hour=0, minute=0, second=0, microsecond=0)
            busy.append(
                (
                    start - timedelta(minutes=self.config.minimum_buffer_minutes),
                    end + timedelta(minutes=self.config.minimum_buffer_minutes),
                )
            )
        return busy

    @staticmethod
    def _merge_busy_blocks(blocks: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
        if not blocks:
            return []
        sorted_blocks = sorted(blocks, key=lambda block: block[0])
        merged: list[tuple[datetime, datetime]] = [sorted_blocks[0]]
        for start, end in sorted_blocks[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        return merged

    @staticmethod
    def _subtract_busy(
        start: datetime,
        end: datetime,
        busy_blocks: Sequence[tuple[datetime, datetime]],
    ) -> list[tuple[datetime, datetime]]:
        free_ranges: list[tuple[datetime, datetime]] = []
        cursor = start
        for busy_start, busy_end in busy_blocks:
            if busy_end <= cursor:
                continue
            if busy_start >= end:
                break
            if busy_start > cursor:
                free_ranges.append((cursor, min(busy_start, end)))
            cursor = max(cursor, busy_end)
            if cursor >= end:
                break
        if cursor < end:
            free_ranges.append((cursor, end))
        return [(range_start, range_end) for range_start, range_end in free_ranges if range_end > range_start]

    def _format_slot(self, start: datetime, duration_minutes: int) -> str:
        end = start + timedelta(minutes=duration_minutes)
        tz_name = start.tzname() or ""
        day = start.strftime("%a %b %d").replace(" 0", " ")
        start_time = start.strftime("%I:%M %p").lstrip("0")
        end_time = end.strftime("%I:%M %p").lstrip("0")
        suffix = f" {tz_name}" if tz_name else ""
        return f"{day}, {start_time}-{end_time}{suffix}"

    def _combine(self, day: date, hhmm: str) -> datetime:
        hours, minutes = [int(part) for part in hhmm.split(":", 1)]
        return datetime.combine(day, time(hour=hours, minute=minutes), tzinfo=self.local_timezone)

    @staticmethod
    def _ceil_to_step(value: datetime, step_minutes: int) -> datetime:
        value = value.replace(second=0, microsecond=0)
        minute_remainder = value.minute % step_minutes
        if minute_remainder == 0:
            return value
        return value + timedelta(minutes=step_minutes - minute_remainder)


class MeetingReplyPlanner:
    def __init__(
        self,
        graph_client: GraphClient,
        config: SchedulingConfig,
        identity: IdentityConfig,
    ) -> None:
        self.config = config
        self.intent_analyzer = SchedulingIntentAnalyzer(identity)
        self.availability = CalendarAvailabilityService(graph_client, config)

    def plan(
        self,
        message: MailboxMessage,
        thread_context: ThreadContext,
        *,
        intent: MeetingIntent | None = None,
    ) -> MeetingPlannerResult | None:
        intent = intent or self.intent_analyzer.analyze(message, thread_context)
        if not intent.is_meeting_request:
            return None
        now = datetime.now(self.availability.local_timezone)
        query_start = self.availability._ceil_to_step(now, self.config.slot_step_minutes)
        query_end = query_start + timedelta(days=self.config.lookahead_days)
        candidate_slots: list[MeetingSlot] = []
        if not intent.needs_clarification:
            try:
                candidate_slots = self.availability.suggest_slots(
                    start=query_start,
                    end=query_end,
                    duration_minutes=intent.duration_minutes,
                    max_suggestions=self.config.max_suggestions,
                )
            except GraphApiError:
                candidate_slots = []
        clarifying = intent.needs_clarification or not candidate_slots
        if clarifying and not intent.needs_clarification:
            intent = replace(
                intent,
                needs_clarification=True,
                reason="calendar_unavailable" if not candidate_slots else "no_openings_found",
                summary=(
                    f"{intent.summary} No clean openings were found in the queried window."
                    if candidate_slots
                    else f"{intent.summary} Calendar availability could not be checked right now."
                ),
            )
        plan = MeetingPlan(
            intent=intent,
            query_start=query_start,
            query_end=query_end,
            suggested_slots=candidate_slots,
        )
        return MeetingPlannerResult(plan=plan, clarifying=clarifying, candidate_slots=candidate_slots)
