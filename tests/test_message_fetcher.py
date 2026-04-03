from datetime import datetime, timedelta, timezone

from edraft.message_fetcher import MessageFetcher
from edraft.models import MailboxMessage, Recipient


class FakeGraphClient:
    def __init__(self, messages: list[MailboxMessage]) -> None:
        self.messages = messages

    def list_messages(
        self,
        *,
        folder: str,
        unread_only: bool,
        limit: int,
        received_after: datetime | None = None,
    ) -> list[MailboxMessage]:
        return self.messages[:limit]


def _message(message_id: str, *, received_at: datetime | None) -> MailboxMessage:
    return MailboxMessage(
        id=message_id,
        conversation_id="conv-1",
        subject="Need input",
        from_recipient=Recipient(name="Alex", address="alex@example.com"),
        to_recipients=[Recipient(name="Jude", address="jude@example.com")],
        cc_recipients=[],
        received_at=received_at,
        categories=[],
    )


def test_fetch_unread_messages_skips_messages_older_than_cutoff() -> None:
    now = datetime.now(timezone.utc)
    fetcher = MessageFetcher(
        FakeGraphClient(
            [
                _message("old", received_at=now - timedelta(hours=30)),
                _message("new", received_at=now - timedelta(hours=3)),
            ]
        ),
        processed_category=None,
    )

    messages = fetcher.fetch_unread_messages(
        folders=["inbox"],
        unread_only=True,
        max_messages=10,
        received_after=now - timedelta(hours=24),
    )

    assert [message.id for message in messages] == ["new"]


def test_fetch_unread_messages_skips_messages_without_received_timestamp_when_cutoff_is_set() -> None:
    now = datetime.now(timezone.utc)
    fetcher = MessageFetcher(
        FakeGraphClient(
            [
                _message("missing", received_at=None),
                _message("new", received_at=now - timedelta(hours=1)),
            ]
        ),
        processed_category=None,
    )

    messages = fetcher.fetch_unread_messages(
        folders=["inbox"],
        unread_only=True,
        max_messages=10,
        received_after=now - timedelta(hours=24),
    )

    assert [message.id for message in messages] == ["new"]
