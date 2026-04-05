from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from edraft.config import StyleCorpusConfig
from edraft.models import MailboxMessage, Recipient
from edraft.style_corpus import StyleCorpusStore, StyleExampleRetriever
from edraft.style_eval import StyleEvaluator


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


class FakeResponses:
    def create(self, **kwargs):
        return SimpleNamespace(
            output_text='{"tone_match": 4, "brevity_match": 5, "commitment_safety": 4, "clarity": 5, "overall": 4, "notes": "Close to the original tone."}'
        )


class FakeGenerator:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="gpt-5.4", reasoning_effort="medium")
        self.client = SimpleNamespace(responses=FakeResponses())

    def generate(self, message, thread_context, style_examples=None) -> str:
        return "Thanks,\n\nI can send an update tomorrow.\n\nThanks,\nJude"


def test_style_evaluator_scores_held_out_cases(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    store = StyleCorpusStore(tmp_path / "edraft.sqlite3")
    old_inbound = _message(
        "in-old",
        sender="alex@example.com",
        recipients=["jude@example.com"],
        subject="Timeline",
        body="When can you share the revised timeline?",
        received_at=now - timedelta(days=60),
    )
    old_reply = _message(
        "out-old",
        sender="jude@example.com",
        recipients=["alex@example.com"],
        subject="Re: Timeline",
        body="I should have the revised timeline ready next week.",
        received_at=now - timedelta(days=59),
    )
    holdout_inbound = _message(
        "in-holdout",
        sender="alex@example.com",
        recipients=["jude@example.com"],
        subject="Timeline",
        body="Do you have a quick update on the revised timeline?",
        received_at=now - timedelta(days=5),
    )
    holdout_reply = _message(
        "out-holdout",
        sender="jude@example.com",
        recipients=["alex@example.com"],
        subject="Re: Timeline",
        body="I should be able to send the revised timeline tomorrow.",
        received_at=now - timedelta(days=4),
    )
    store.upsert_pair(
        inbound_message=old_inbound,
        reply_message=old_reply,
        correspondent_email="alex@example.com",
        pairing_source="test",
        pairing_confidence=1.0,
    )
    store.upsert_pair(
        inbound_message=holdout_inbound,
        reply_message=holdout_reply,
        correspondent_email="alex@example.com",
        pairing_source="test",
        pairing_confidence=1.0,
    )

    config = StyleCorpusConfig(eval_holdout_days=30, eval_max_cases=5, max_examples=2)
    evaluator = StyleEvaluator(
        config=config,
        generator=FakeGenerator(),
        store=store,
        retriever=StyleExampleRetriever(store, config),
    )

    report = evaluator.evaluate(limit=1)

    assert report["evaluated_cases"] == 1
    assert report["averages"]["overall"] == 4
    assert report["cases"][0]["style_example_ids"] == ["out-old"]
