from pathlib import Path

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
