import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from edraft.email_cache import EmailCacheStore
from edraft.models import MailboxMessage, Recipient


def _message(
    message_id: str,
    *,
    sender: str = "alex@example.com",
    recipients: list[str] | None = None,
    subject: str = "Budget deadline",
    body: str = "This is due tomorrow.",
    received_at: datetime | None = None,
    conversation_id: str = "conv-1",
    is_read: bool = False,
) -> MailboxMessage:
    return MailboxMessage(
        id=message_id,
        conversation_id=conversation_id,
        internet_message_id=f"<{message_id}@example.com>",
        subject=subject,
        from_recipient=Recipient(name=sender.split("@", 1)[0], address=sender),
        to_recipients=[
            Recipient(name=item.split("@", 1)[0], address=item)
            for item in (recipients or ["jude@example.com"])
        ],
        cc_recipients=[],
        received_at=received_at or datetime.now(timezone.utc),
        body_preview=body,
        body_content=body,
        body_content_type="text",
        importance="high",
        is_read=is_read,
        web_link="https://outlook.office.com/message",
    )


def test_migrations_are_idempotent_and_do_not_create_calendar_cache(tmp_path: Path) -> None:
    database_path = tmp_path / "edraft.sqlite3"
    EmailCacheStore(database_path)
    EmailCacheStore(database_path)

    connection = sqlite3.connect(database_path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    migrations = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    connection.close()

    assert migrations == 1
    assert "email_messages" in tables
    assert "briefing_runs" in tables
    assert not any("calendar" in table and table != "schema_migrations" for table in tables)


def test_email_upsert_deduplicates_and_indexes_participants(tmp_path: Path) -> None:
    store = EmailCacheStore(tmp_path / "edraft.sqlite3")
    message = _message("msg-1")
    store.upsert_message(message, source_folder="inbox", direction="inbound")
    store.upsert_message(
        _message("msg-1", subject="Updated subject"),
        source_folder="inbox",
        direction="inbound",
    )

    loaded = store.get_message("msg-1")
    related = store.search_related_messages(
        keywords=["updated"],
        participant_emails=["alex@example.com"],
        before=datetime.now(timezone.utc) + timedelta(days=1),
        limit=5,
    )

    assert loaded is not None
    assert loaded.subject == "Updated subject"
    assert [item.graph_message_id for item in related] == ["msg-1"]


def test_sync_freshness_uses_last_successful_sync(tmp_path: Path) -> None:
    store = EmailCacheStore(tmp_path / "edraft.sqlite3")

    assert not store.is_sync_fresh(
        folder="inbox",
        direction="inbound",
        lookback_days=7,
        freshness_minutes=30,
    )
    store.record_sync(folder="inbox", direction="inbound", lookback_days=7, success=True)

    assert store.is_sync_fresh(
        folder="inbox",
        direction="inbound",
        lookback_days=7,
        freshness_minutes=30,
    )


def test_triage_and_stale_thread_queries(tmp_path: Path) -> None:
    store = EmailCacheStore(tmp_path / "edraft.sqlite3")
    now = datetime.now(timezone.utc)
    urgent = _message("urgent", received_at=now - timedelta(hours=1))
    stale = _message(
        "stale",
        subject="Old student question",
        body="Can you review this?",
        received_at=now - timedelta(days=5),
        conversation_id="conv-stale",
    )
    store.upsert_message(urgent, source_folder="inbox", direction="inbound")
    store.upsert_message(stale, source_folder="inbox", direction="inbound")

    triage = store.triage_candidates(since=now - timedelta(days=7), limit=5)
    follow_ups = store.follow_up_candidates(stale_after=now - timedelta(days=3), limit=5)

    assert triage[0].graph_message_id == "urgent"
    assert any(item.graph_message_id == "stale" for item in follow_ups)
