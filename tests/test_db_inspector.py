from datetime import datetime, timezone
from pathlib import Path

import pytest

from edraft.db_inspector import DatabaseInspector
from edraft.models import MailboxMessage, MeetingSuggestion, Recipient
from edraft.state_store import StateStore
from edraft.style_corpus import StyleCorpusStore


def _message(
    message_id: str,
    *,
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
) -> MailboxMessage:
    return MailboxMessage(
        id=message_id,
        conversation_id="conv-1",
        subject=subject,
        from_recipient=Recipient(name=sender.split("@", 1)[0], address=sender),
        to_recipients=[Recipient(name=item.split("@", 1)[0], address=item) for item in recipients],
        cc_recipients=[],
        received_at=datetime.now(timezone.utc),
        body_content=body,
        body_content_type="text",
        body_preview=body,
    )


def test_database_inspector_summary_reports_known_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "edraft.sqlite3"
    state_store = StateStore(database_path)
    style_store = StyleCorpusStore(database_path)
    state_store.record_action(
        source_message_id="msg-1",
        conversation_id="conv-1",
        subject="Need input",
        received_timestamp=datetime.now(timezone.utc).isoformat(),
        action="drafted",
        reason="reply_draft_created",
        created_draft_id="draft-1",
    )
    state_store.record_meeting_suggestion(
        MeetingSuggestion(
            source_message_id="msg-2",
            conversation_id="conv-2",
            subject="Schedule time",
            sender_email="alex@example.com",
            intent={"is_meeting_request": True},
            query_start=None,
            query_end=None,
            suggested_slots=[],
            generated_reply="Sounds good.",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    style_store.upsert_pair(
        inbound_message=_message(
            "in-1",
            sender="alex@example.com",
            recipients=["jude@example.com"],
            subject="Timeline",
            body="Can you share the revised timeline?",
        ),
        reply_message=_message(
            "out-1",
            sender="jude@example.com",
            recipients=["alex@example.com"],
            subject="Re: Timeline",
            body="I should have the revised timeline tomorrow.",
        ),
        correspondent_email="alex@example.com",
        pairing_source="test",
        pairing_confidence=1.0,
    )

    inspector = DatabaseInspector(database_path)
    summary = inspector.summary()

    assert "message_actions" in summary["available_tables"]
    assert "meeting_suggestions" in summary["available_tables"]
    assert "style_reply_pairs" in summary["available_tables"]
    assert summary["tables"]["message_actions"]["row_count"] == 1
    assert summary["tables"]["meeting_suggestions"]["row_count"] == 1
    assert summary["tables"]["style_reply_pairs"]["row_count"] == 1


def test_database_inspector_returns_rows_for_specific_table(tmp_path: Path) -> None:
    database_path = tmp_path / "edraft.sqlite3"
    state_store = StateStore(database_path)
    state_store.record_action(
        source_message_id="msg-1",
        conversation_id="conv-1",
        subject="Need input",
        received_timestamp=datetime.now(timezone.utc).isoformat(),
        action="drafted",
        reason="reply_draft_created",
        created_draft_id="draft-1",
    )

    inspector = DatabaseInspector(database_path)
    payload = inspector.inspect_table("message_actions", limit=5)

    assert payload["table"] == "message_actions"
    assert payload["row_count"] == 1
    assert payload["rows"][0]["source_message_id"] == "msg-1"


def test_database_inspector_rejects_unknown_table(tmp_path: Path) -> None:
    inspector = DatabaseInspector(tmp_path / "edraft.sqlite3")

    with pytest.raises(ValueError):
        inspector.inspect_table("not_a_table", limit=5)


def test_database_inspector_corpus_stats_reports_style_corpus_metrics(tmp_path: Path) -> None:
    database_path = tmp_path / "edraft.sqlite3"
    style_store = StyleCorpusStore(database_path)
    style_store.upsert_pair(
        inbound_message=_message(
            "in-1",
            sender="alex@example.com",
            recipients=["jude@example.com"],
            subject="Timeline",
            body="Can you share the revised timeline?",
        ),
        reply_message=_message(
            "out-1",
            sender="jude@example.com",
            recipients=["alex@example.com"],
            subject="Re: Timeline",
            body="I should have the revised timeline tomorrow.",
        ),
        correspondent_email="alex@example.com",
        pairing_source="test",
        pairing_confidence=1.0,
    )

    inspector = DatabaseInspector(database_path)
    payload = inspector.corpus_stats()

    assert payload["style_corpus_available"] is True
    assert payload["style_corpus"]["pair_count"] == 1
    assert payload["style_corpus"]["correspondent_count"] == 1
    assert payload["style_corpus"]["message_archive"]["message_count"] == 0
    assert payload["style_corpus"]["eval_holdout"]["holdout_case_count"] == 0
    assert payload["style_corpus"]["text_volume"]["total_text_chars"] > 0
    assert payload["style_corpus"]["top_correspondents"][0]["correspondent_email"] == "alex@example.com"


def test_database_inspector_corpus_stats_handles_missing_style_tables(tmp_path: Path) -> None:
    inspector = DatabaseInspector(tmp_path / "edraft.sqlite3")

    payload = inspector.corpus_stats()

    assert payload["style_corpus_available"] is False
    assert payload["style_corpus"] is None
