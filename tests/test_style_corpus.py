from datetime import datetime, timedelta, timezone
from pathlib import Path

from edraft.config import IdentityConfig, StyleCorpusConfig
from edraft.models import MailboxMessage, Recipient, ThreadContext
from edraft.style_corpus import StyleCorpusStore, StyleCorpusSyncer, StyleExampleRetriever


def _message(
    message_id: str,
    *,
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
    received_at: datetime,
    conversation_id: str = "conv-1",
) -> MailboxMessage:
    return MailboxMessage(
        id=message_id,
        conversation_id=conversation_id,
        subject=subject,
        from_recipient=Recipient(name=sender.split("@", 1)[0], address=sender),
        to_recipients=[Recipient(name=recipient.split("@", 1)[0], address=recipient) for recipient in recipients],
        cc_recipients=[],
        received_at=received_at,
        body_content=body,
        body_content_type="text",
        body_preview=body,
    )


class FakeGraphClient:
    def __init__(self, sent_messages: list[MailboxMessage], conversations: dict[str, list[MailboxMessage]]) -> None:
        self.sent_messages = sent_messages
        self.conversations = conversations

    def list_messages(
        self,
        *,
        folder: str,
        unread_only: bool,
        limit: int,
        received_after=None,
        include_body: bool = False,
    ) -> list[MailboxMessage]:
        return self.sent_messages[:limit]

    def list_conversation_messages(
        self,
        conversation_id: str,
        *,
        exclude_message_id: str | None = None,
        limit: int = 5,
    ) -> list[MailboxMessage]:
        return [
            message
            for message in self.conversations.get(conversation_id, [])
            if message.id != exclude_message_id
        ][:limit]


def test_style_corpus_sync_pairs_sent_reply_with_prior_inbound(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    inbound = _message(
        "in-1",
        sender="alex@example.com",
        recipients=["jude@example.com"],
        subject="Budget question",
        body="Can you send the updated budget numbers?",
        received_at=now - timedelta(hours=2),
    )
    reply = _message(
        "out-1",
        sender="jude@example.com",
        recipients=["alex@example.com"],
        subject="Re: Budget question",
        body=(
            "Yes, I can send those this afternoon.\n"
            "Jude\n"
            "From:\n"
            "Alex <alex@example.com>\n"
            "Sent:\n"
            "Friday, April 3, 2026 10:00\n"
            "Subject:\n"
            "Budget question"
        ),
        received_at=now - timedelta(hours=1),
    )
    store = StyleCorpusStore(tmp_path / "edraft.sqlite3")
    syncer = StyleCorpusSyncer(
        FakeGraphClient([reply], {"conv-1": [inbound]}),
        store,
        identity=IdentityConfig(name="Jude Bayham", email="jude@example.com"),
        config=StyleCorpusConfig(sync_max_messages=10, min_reply_chars=10),
    )

    report = syncer.sync()
    pairs = store.find_pairs_by_correspondent("alex@example.com", limit=5)

    assert report.paired == 1
    assert len(pairs) == 1
    assert pairs[0].inbound_message_id == "in-1"
    assert "updated budget numbers" in pairs[0].inbound_text
    assert pairs[0].reply_text == "Yes, I can send those this afternoon.\nJude"


def test_style_retriever_prefers_same_sender_before_generic_matches(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    store = StyleCorpusStore(tmp_path / "edraft.sqlite3")

    same_sender_inbound = _message(
        "in-1",
        sender="alex@example.com",
        recipients=["jude@example.com"],
        subject="Dataset timing",
        body="When will the dataset be available?",
        received_at=now - timedelta(days=10),
    )
    same_sender_reply = _message(
        "out-1",
        sender="jude@example.com",
        recipients=["alex@example.com"],
        subject="Re: Dataset timing",
        body="I expect to have it ready next week after the final checks.",
        received_at=now - timedelta(days=9),
    )
    generic_inbound = _message(
        "in-2",
        sender="other@example.com",
        recipients=["jude@example.com"],
        subject="Dataset timing",
        body="Do you have an update on the dataset timeline?",
        received_at=now - timedelta(days=8),
    )
    generic_reply = _message(
        "out-2",
        sender="jude@example.com",
        recipients=["other@example.com"],
        subject="Re: Dataset timing",
        body="I am still checking a couple of issues before I can share it.",
        received_at=now - timedelta(days=7),
    )
    store.upsert_pair(
        inbound_message=same_sender_inbound,
        reply_message=same_sender_reply,
        correspondent_email="alex@example.com",
        pairing_source="test",
        pairing_confidence=1.0,
    )
    store.upsert_pair(
        inbound_message=generic_inbound,
        reply_message=generic_reply,
        correspondent_email="other@example.com",
        pairing_source="test",
        pairing_confidence=1.0,
    )

    retriever = StyleExampleRetriever(store, StyleCorpusConfig(max_examples=2, max_example_chars=200))
    current_message = _message(
        "in-current",
        sender="alex@example.com",
        recipients=["jude@example.com"],
        subject="Dataset timing",
        body="Do you know when the updated dataset will be ready?",
        received_at=now,
        conversation_id="conv-current",
    )

    examples = retriever.retrieve(current_message, ThreadContext(conversation_id=None, related_messages=[]))

    assert len(examples) == 2
    assert examples[0].correspondent_email == "alex@example.com"
    assert {example.source for example in examples} == {"same_sender", "similar_conversation"}


def test_style_corpus_sync_prunes_stale_pair_when_reply_no_longer_qualifies(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    stale_reply = _message(
        "out-stale",
        sender="jude@example.com",
        recipients=["alex@example.com"],
        subject="Re: Quick note",
        body="Get\nOutlook for Android",
        received_at=now - timedelta(hours=1),
    )
    inbound = _message(
        "in-stale",
        sender="alex@example.com",
        recipients=["jude@example.com"],
        subject="Quick note",
        body="Can you send a quick note?",
        received_at=now - timedelta(hours=2),
    )
    store = StyleCorpusStore(tmp_path / "edraft.sqlite3")
    store.upsert_pair(
        inbound_message=inbound,
        reply_message=stale_reply,
        correspondent_email="alex@example.com",
        pairing_source="test",
        pairing_confidence=1.0,
    )

    syncer = StyleCorpusSyncer(
        FakeGraphClient([stale_reply], {"conv-1": [inbound]}),
        store,
        identity=IdentityConfig(name="Jude Bayham", email="jude@example.com"),
        config=StyleCorpusConfig(sync_max_messages=10, min_reply_chars=10),
    )

    report = syncer.sync()

    assert report.paired == 0
    assert store.find_pairs_by_correspondent("alex@example.com", limit=5) == []
