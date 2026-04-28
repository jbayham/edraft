from datetime import datetime, timedelta, timezone
from pathlib import Path

from edraft.config import (
    AppConfig,
    BriefingConfig,
    FilterConfig,
    IdentityConfig,
    LLMConfig,
    LoggingConfig,
    ScanConfig,
    SchedulingConfig,
    StyleCorpusConfig,
    StateConfig,
)
from edraft.models import DraftResult, MailboxMessage, Recipient, ThreadContext
from edraft.scanner import InboxScanner
from edraft.state_store import StateStore


class FakeGraphClient:
    def __init__(self, message: MailboxMessage) -> None:
        self.message = message
        self.created = 0
        self.last_received_after: datetime | None = None
        self.calendar_events: list[object] = []

    def list_messages(
        self,
        *,
        folder: str,
        unread_only: bool,
        limit: int,
        received_after: datetime | None = None,
    ) -> list[MailboxMessage]:
        self.last_received_after = received_after
        return [self.message]

    def get_message(self, message_id: str) -> MailboxMessage:
        return self.message

    def list_conversation_messages(
        self,
        conversation_id: str,
        *,
        exclude_message_id: str | None = None,
        limit: int = 5,
    ) -> list[MailboxMessage]:
        return []

    def create_reply_draft(
        self,
        *,
        source_message_id: str,
        comment: str,
        reply_mode: str = "reply",
    ) -> DraftResult:
        self.created += 1
        return DraftResult(id="draft-1", conversation_id="conv-1", web_link=None)

    def list_calendar_view(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int = 200,
    ) -> list[object]:
        return self.calendar_events

    def add_category_to_message(self, message_id: str, category: str, existing: list[str]) -> None:
        return None

    def update_message_body_html(self, message_id: str, html_content: str) -> None:
        return None


class FakeDraftGenerator:
    def __init__(self) -> None:
        self.last_meeting_plan = None

    def generate(
        self,
        message: MailboxMessage,
        thread_context: ThreadContext,
        style_examples: list[object] | None = None,
        meeting_plan: object | None = None,
    ) -> str:
        self.last_meeting_plan = meeting_plan
        return "Thanks, I will take a look."


def test_scanner_skips_duplicate_message(tmp_path: Path) -> None:
    message = MailboxMessage(
        id="msg-1",
        conversation_id="conv-1",
        subject="Need input",
        from_recipient=Recipient(name="Alex", address="alex@example.com"),
        to_recipients=[Recipient(name="Jude", address="jude@example.com")],
        cc_recipients=[],
        received_at=datetime.now(timezone.utc),
        body_content="<p>Hi Jude, can you review this?</p>",
        body_content_type="html",
    )
    graph_client = FakeGraphClient(message)
    state_store = StateStore(tmp_path / "edraft.sqlite3")
    state_store.record_action(
        source_message_id="msg-1",
        conversation_id="conv-1",
        subject="Need input",
        received_timestamp=None,
        action="drafted",
        reason="reply_draft_created",
        created_draft_id="draft-1",
    )
    config = AppConfig(
        identity=IdentityConfig(name="Jude Bayham", email="jude@example.com"),
        scan=ScanConfig(),
        filters=FilterConfig(),
        llm=LLMConfig(),
        style_corpus=StyleCorpusConfig(),
        scheduling=SchedulingConfig(),
        briefing=BriefingConfig(),
        state=StateConfig(database_path=tmp_path / "edraft.sqlite3"),
        logging=LoggingConfig(),
        source_path=tmp_path / "edraft.toml",
    )
    scanner = InboxScanner(
        config=config,
        graph_client=graph_client,
        state_store=state_store,
        draft_generator=FakeDraftGenerator(),
    )

    report = scanner.scan_once()

    assert report.skipped == 1
    assert report.drafted == 0
    assert graph_client.created == 0
    assert graph_client.last_received_after is not None
    expected_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    assert abs((graph_client.last_received_after - expected_cutoff).total_seconds()) < 5


def test_scanner_uses_meeting_planner_for_scheduling_requests(tmp_path: Path) -> None:
    message = MailboxMessage(
        id="msg-2",
        conversation_id="conv-2",
        subject="Schedule time",
        from_recipient=Recipient(name="Alex", address="alex@example.com"),
        to_recipients=[Recipient(name="Jude", address="jude@example.com")],
        cc_recipients=[],
        received_at=datetime.now(timezone.utc),
        body_content="<p>Can we schedule 30 minutes next week?</p>",
        body_content_type="html",
    )
    graph_client = FakeGraphClient(message)
    state_store = StateStore(tmp_path / "edraft.sqlite3")
    config = AppConfig(
        identity=IdentityConfig(name="Jude Bayham", email="jude@example.com"),
        scan=ScanConfig(),
        filters=FilterConfig(),
        llm=LLMConfig(signature_block="Thanks,\nJude"),
        style_corpus=StyleCorpusConfig(),
        scheduling=SchedulingConfig(),
        briefing=BriefingConfig(),
        state=StateConfig(database_path=tmp_path / "edraft.sqlite3"),
        logging=LoggingConfig(),
        source_path=tmp_path / "edraft.toml",
    )
    draft_generator = FakeDraftGenerator()
    scanner = InboxScanner(
        config=config,
        graph_client=graph_client,
        state_store=state_store,
        draft_generator=draft_generator,
    )

    report = scanner.scan_once(dry_run=False)

    assert report.drafted == 1
    assert graph_client.created == 1
    assert draft_generator.last_meeting_plan is not None
    meeting_state = state_store.get_meeting_suggestion("msg-2")
    assert meeting_state is not None
    assert meeting_state.intent["is_meeting_request"] is True
