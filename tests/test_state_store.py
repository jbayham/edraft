from pathlib import Path

from edraft.models import MeetingSuggestion
from edraft.state_store import StateStore


def test_terminal_actions_prevent_duplicate_processing(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "edraft.sqlite3")
    store.record_action(
        source_message_id="msg-1",
        conversation_id="conv-1",
        subject="Subject",
        received_timestamp=None,
        action="drafted",
        reason="reply_draft_created",
        created_draft_id="draft-1",
    )
    assert store.has_terminal_record("msg-1")


def test_error_action_is_not_terminal(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "edraft.sqlite3")
    store.record_action(
        source_message_id="msg-1",
        conversation_id="conv-1",
        subject="Subject",
        received_timestamp=None,
        action="error",
        reason="transient failure",
    )
    assert not store.has_terminal_record("msg-1")


def test_meeting_suggestion_round_trips_through_state_store(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "edraft.sqlite3")
    suggestion = MeetingSuggestion(
        source_message_id="msg-1",
        conversation_id="conv-1",
        subject="Schedule time",
        sender_email="alex@example.com",
        intent={"is_meeting_request": True, "duration_minutes": 30},
        query_start="2026-04-07T10:00:00+00:00",
        query_end="2026-04-14T10:00:00+00:00",
        suggested_slots=[{"start": "2026-04-08T15:00:00+00:00", "end": "2026-04-08T15:30:00+00:00"}],
        generated_reply="Sounds good.",
        updated_at="2026-04-07T10:00:00+00:00",
    )
    store.record_meeting_suggestion(suggestion)

    loaded = store.get_meeting_suggestion("msg-1")

    assert loaded == suggestion
