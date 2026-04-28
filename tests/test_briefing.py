from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

import edraft.cli as cli_module
from edraft.briefing_generator import (
    build_briefing_context,
    build_briefing_prompt,
    render_briefing_markdown,
    result_from_llm_payload,
)
from edraft.briefing_matcher import RelatedEmailMatcher
from edraft.briefing_sync import BriefingEmailSyncer
from edraft.cli import app
from edraft.config import (
    AppConfig,
    BriefingConfig,
    FilterConfig,
    IdentityConfig,
    LLMConfig,
    LoggingConfig,
    ScanConfig,
    SchedulingConfig,
    StateConfig,
    StyleCorpusConfig,
)
from edraft.email_cache import EmailCacheStore
from edraft.models import BriefingRequest, CalendarEvent, MailboxMessage, Recipient
from edraft.style_corpus import StyleCorpusStore


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        identity=IdentityConfig(name="Jude Bayham", email="jude@example.com"),
        scan=ScanConfig(),
        filters=FilterConfig(),
        llm=LLMConfig(),
        style_corpus=StyleCorpusConfig(),
        scheduling=SchedulingConfig(),
        briefing=BriefingConfig(output_directory=tmp_path / "briefings"),
        state=StateConfig(database_path=tmp_path / "edraft.sqlite3"),
        logging=LoggingConfig(format="text"),
        source_path=tmp_path / "edraft.toml",
    )


def _message(message_id: str, *, subject: str, sender: str = "alex@example.com") -> MailboxMessage:
    return MailboxMessage(
        id=message_id,
        conversation_id="conv-1",
        subject=subject,
        from_recipient=Recipient(name="Alex", address=sender),
        to_recipients=[Recipient(name="Jude", address="jude@example.com")],
        cc_recipients=[],
        received_at=datetime.now(timezone.utc) - timedelta(days=1),
        body_preview="Agenda and project notes for the budget meeting.",
        body_content="Agenda and project notes for the budget meeting.",
        body_content_type="text",
    )


def _event() -> CalendarEvent:
    return CalendarEvent(
        id="event-1",
        subject="Budget project meeting",
        start=datetime.now(timezone.utc) + timedelta(hours=2),
        end=datetime.now(timezone.utc) + timedelta(hours=3),
        organizer=Recipient(name="Alex", address="alex@example.com"),
        attendees=[Recipient(name="Jude", address="jude@example.com")],
        body_preview="Discuss budget agenda.",
        web_link="https://outlook.office.com/event",
    )


class FakeGraphClient:
    def __init__(self, messages: list[MailboxMessage]) -> None:
        self.messages = messages
        self.calls = 0

    def iter_messages(
        self,
        *,
        folder: str,
        unread_only: bool,
        limit: int,
        received_after=None,
        include_body: bool = False,
        date_field: str = "receivedDateTime",
    ):
        self.calls += 1
        yield from self.messages[:limit]


class FakeDraftGraphClient:
    def __init__(self, message: MailboxMessage) -> None:
        self.message = message
        self.closed = False

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

    def close(self) -> None:
        self.closed = True


def test_briefing_sync_skips_fresh_cache(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = EmailCacheStore(config.state.database_path)
    store.record_sync(folder="inbox", direction="inbound", lookback_days=7, success=True)
    store.record_sync(folder="sentitems", direction="outbound", lookback_days=7, success=True)
    graph = FakeGraphClient([_message("msg-1", subject="Budget")])
    syncer = BriefingEmailSyncer(graph_client=graph, store=store, config=config)

    report = syncer.sync_recent_email()

    assert report.skipped_fresh == 2
    assert graph.calls == 0


def test_related_email_matching_uses_fresh_in_memory_event(tmp_path: Path) -> None:
    store = EmailCacheStore(tmp_path / "edraft.sqlite3")
    store.upsert_message(_message("msg-1", subject="Budget project notes"), source_folder="inbox", direction="inbound")
    matcher = RelatedEmailMatcher(store, top_k=5)

    matches = matcher.match_for_event(_event())

    assert matches
    assert matches[0].email.graph_message_id == "msg-1"
    assert "sender_attendee_overlap" in matches[0].reasons


def test_prompt_construction_and_markdown_rendering() -> None:
    request = BriefingRequest(
        briefing_date="2026-04-27",
        timezone="UTC",
        email_lookback_days=7,
        related_emails_per_event=5,
        max_emails=100,
    )
    email = _message("msg-1", subject="Budget deadline")
    context = build_briefing_context(
        request=request,
        events=[_event()],
        triage=[],
        follow_up_items=[],
        related_by_event={},
    )
    prompt = build_briefing_prompt(context)
    result = result_from_llm_payload(
        {
            "sections": [
                {
                    "title": "Executive summary",
                    "content_markdown": "- One confirmed meeting.",
                    "source_references": [{"source_type": "event", "source_id": "event-1"}],
                }
            ],
            "draft_candidates": [
                {
                    "graph_message_id": email.id,
                    "conversation_id": email.conversation_id,
                    "subject": email.subject,
                    "sender_email": email.sender_address,
                    "rationale": "Awaiting response",
                }
            ],
        },
        request=request,
        model="gpt-5.4",
        context=context,
    )
    result.markdown = render_briefing_markdown(result)

    assert "Do not invent" in prompt["system"]
    assert "source_id" in prompt["user"]
    assert "# Chief of Staff Briefing - 2026-04-27" in result.markdown
    assert "`msg-1`" in result.markdown


def test_brief_show_cli_reads_cached_briefing(tmp_path: Path) -> None:
    config_path = tmp_path / "edraft.toml"
    database_path = tmp_path / "edraft.sqlite3"
    config_path.write_text(
        f"""
[identity]
name = "Jude Bayham"
email = "jude@example.com"

[state]
database_path = "{database_path}"
        """.strip()
    )
    request = BriefingRequest(
        briefing_date="2026-04-27",
        timezone="UTC",
        email_lookback_days=7,
        related_emails_per_event=5,
        max_emails=100,
    )
    result = result_from_llm_payload(
        {"sections": [{"title": "Executive summary", "content_markdown": "- Cached."}]},
        request=request,
        model="gpt-5.4",
        context={"calendar_events": [], "email_triage_candidates": []},
    )
    result.markdown = render_briefing_markdown(result)
    EmailCacheStore(database_path).save_briefing_run(
        result,
        request_json=request.to_dict(),
        email_sync_summary={},
    )

    runner = CliRunner()
    response = runner.invoke(
        app,
        ["brief-show", "--config", str(config_path), "--date", "2026-04-27"],
    )

    assert response.exit_code == 0
    assert "Cached" in response.output


def test_brief_cli_saves_output_by_default(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    request = BriefingRequest(
        briefing_date="2026-04-27",
        timezone="UTC",
        email_lookback_days=7,
        related_emails_per_event=5,
        max_emails=100,
    )
    result = result_from_llm_payload(
        {"sections": [{"title": "Executive summary", "content_markdown": "- Saved."}]},
        request=request,
        model="gpt-5.4",
        context={"calendar_events": [], "email_triage_candidates": []},
    )
    result.markdown = render_briefing_markdown(result)

    class FakeService:
        def __init__(self, **kwargs) -> None:
            pass

        def generate(self, *, target_date=None, regenerate: bool = False):
            return result

    class FakeGraphAuthenticator:
        def __init__(self, settings) -> None:
            pass

        def get_access_token(self, interactive: bool = True) -> str:
            return "token"

    class FakeGraphClient:
        def __init__(self, token_provider) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "load_app_config", lambda path=None: config)
    monkeypatch.setattr(cli_module, "configure_logging", lambda logging_config: None)
    monkeypatch.setattr(cli_module, "load_auth_settings", lambda: object())
    monkeypatch.setattr(cli_module, "GraphAuthenticator", FakeGraphAuthenticator)
    monkeypatch.setattr(cli_module, "GraphClient", FakeGraphClient)
    monkeypatch.setattr(cli_module, "BriefingGenerator", lambda config: object())
    monkeypatch.setattr(cli_module, "DailyBriefingService", FakeService)

    runner = CliRunner()
    response = runner.invoke(app, ["brief", "--date", "2026-04-27"])

    assert response.exit_code == 0
    assert "Saved" in response.output
    output_path = config.briefing.output_directory / "briefing-2026-04-27.md"
    assert output_path.exists()
    assert "Saved" in output_path.read_text()


def test_brief_draft_cli_reuses_reply_draft_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path)
    message = _message("msg-1", subject="Needs reply")
    graph = FakeDraftGraphClient(message)
    created = {}

    class FakeDraftGenerator:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def generate(self, message, context, style_examples):
            return "Thanks, I will take a look."

    class FakeDraftCreator:
        def __init__(self, graph_client, *, reply_mode, identity) -> None:
            self.graph_client = graph_client

        def create(self, message, draft_text):
            created["message_id"] = message.id
            created["draft_text"] = draft_text
            return type(
                "Draft",
                (),
                {
                    "id": "draft-1",
                    "conversation_id": message.conversation_id,
                    "web_link": "https://outlook.office.com/draft",
                },
            )()

    def fake_build_components(config_path=None, *, require_llm: bool):
        return config, graph, None, StyleCorpusStore(config.state.database_path)

    monkeypatch.setattr("edraft.cli.build_components", fake_build_components)
    monkeypatch.setattr("edraft.cli.DraftGenerator", FakeDraftGenerator)
    monkeypatch.setattr("edraft.cli.DraftCreator", FakeDraftCreator)

    runner = CliRunner()
    response = runner.invoke(app, ["brief-draft", "msg-1"])

    assert response.exit_code == 0
    assert created == {"message_id": "msg-1", "draft_text": "Thanks, I will take a look."}
    assert "draft-1" in response.output
