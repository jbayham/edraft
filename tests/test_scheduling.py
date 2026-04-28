from datetime import datetime, timedelta, timezone

from edraft.config import IdentityConfig, SchedulingConfig
from edraft.models import CalendarEvent, MailboxMessage, Recipient, ThreadContext
from edraft.scheduling import CalendarAvailabilityService, MeetingReplyPlanner, SchedulingIntentAnalyzer


class FakeGraphClient:
    def __init__(self, events: list[CalendarEvent]) -> None:
        self.events = events
        self.last_request = None

    def list_calendar_view(self, start: datetime, end: datetime, *, limit: int = 200) -> list[CalendarEvent]:
        self.last_request = {"start": start, "end": end, "limit": limit}
        return self.events


def _message(subject: str, body: str) -> MailboxMessage:
    return MailboxMessage(
        id="msg-1",
        conversation_id="conv-1",
        subject=subject,
        from_recipient=Recipient(name="Alex", address="alex@example.com"),
        to_recipients=[Recipient(name="Jude", address="jude@example.com")],
        cc_recipients=[],
        received_at=datetime.now(timezone.utc),
        body_content=f"<p>{body}</p>",
        body_content_type="html",
        body_preview=body,
    )


def test_scheduling_intent_analyzer_detects_clear_request() -> None:
    analyzer = SchedulingIntentAnalyzer(IdentityConfig(name="Jude Bayham", email="jude@example.com"))
    intent = analyzer.analyze(_message("Time to meet", "Can we schedule 30 minutes next week?"))

    assert intent.is_meeting_request is True
    assert intent.duration_minutes == 30
    assert "duration" in intent.constraints
    assert "timeframe" in intent.constraints


def test_scheduling_intent_analyzer_is_conservative_for_non_scheduling_mail() -> None:
    analyzer = SchedulingIntentAnalyzer(IdentityConfig(name="Jude Bayham", email="jude@example.com"))
    intent = analyzer.analyze(_message("Thanks", "Thanks for the update."))

    assert intent.is_meeting_request is False
    assert intent.needs_clarification is True


def test_calendar_availability_service_finds_free_slots_between_busy_events() -> None:
    events = [
        CalendarEvent(
            id="busy-1",
            subject="Hold",
            start=datetime(2026, 4, 6, 10, 0, tzinfo=timezone.utc),
            end=datetime(2026, 4, 6, 11, 0, tzinfo=timezone.utc),
        ),
        CalendarEvent(
            id="busy-2",
            subject="Hold",
            start=datetime(2026, 4, 6, 13, 0, tzinfo=timezone.utc),
            end=datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc),
        ),
    ]
    service = CalendarAvailabilityService(
        FakeGraphClient(events),
        SchedulingConfig(
            weekdays_only=True,
            minimum_buffer_minutes=0,
            slot_step_minutes=30,
            business_hours_start="09:00",
            business_hours_end="17:00",
        ),
        local_timezone=timezone.utc,
    )

    slots = service.suggest_slots(
        start=datetime(2026, 4, 6, 9, 0, tzinfo=timezone.utc),
        end=datetime(2026, 4, 7, 17, 0, tzinfo=timezone.utc),
        duration_minutes=30,
        max_suggestions=3,
    )

    assert [slot.start.hour for slot in slots] == [9, 9, 11]
    assert [slot.start.minute for slot in slots] == [0, 30, 0]


def test_meeting_reply_planner_returns_clarifying_plan_when_no_openings_exist() -> None:
    now = datetime.now(timezone.utc)
    service = CalendarAvailabilityService(
        FakeGraphClient(
            [
                CalendarEvent(
                    id="all-day",
                    subject="Busy",
                    start=now.replace(hour=0, minute=0, second=0, microsecond=0),
                    end=(now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=14)),
                    is_all_day=True,
                )
            ]
        ),
        SchedulingConfig(
            lookahead_days=1,
            max_suggestions=3,
            weekdays_only=True,
            minimum_buffer_minutes=0,
            slot_step_minutes=30,
            business_hours_start="09:00",
            business_hours_end="17:00",
        ),
        local_timezone=timezone.utc,
    )
    planner = MeetingReplyPlanner(
        graph_client=service.graph_client,
        config=service.config,
        identity=IdentityConfig(name="Jude Bayham", email="jude@example.com"),
    )
    plan = planner.plan(_message("Schedule time", "Could we meet next week?"), ThreadContext(conversation_id="conv-1"))

    assert plan is not None
    assert plan.clarifying is True
    assert plan.candidate_slots == []
