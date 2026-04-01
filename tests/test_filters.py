from edraft.config import FilterConfig, IdentityConfig
from edraft.filters import MessageFilter
from edraft.models import MailboxMessage, Recipient


IDENTITY = IdentityConfig(name="Jude Bayham", email="jude@example.com")


def make_message(
    *,
    sender: str = "person@example.com",
    to: list[str] | None = None,
    cc: list[str] | None = None,
    subject: str = "Hello",
    body: str = "<p>Hi Jude, can you review this?</p>",
    headers: dict[str, str] | None = None,
) -> MailboxMessage:
    return MailboxMessage(
        id="msg-1",
        conversation_id="conv-1",
        subject=subject,
        from_recipient=Recipient(name="Sender", address=sender),
        to_recipients=[Recipient(name="", address=value) for value in (to or ["jude@example.com"])],
        cc_recipients=[Recipient(name="", address=value) for value in (cc or [])],
        body_content=body,
        body_content_type="html",
        headers=headers or {},
    )


def test_newsletter_header_is_skipped() -> None:
    message = make_message(headers={"list-unsubscribe": "<mailto:list@example.com>"})
    decision = MessageFilter(FilterConfig(sender_patterns=["no-reply"]), IDENTITY).evaluate(message)
    assert not decision.should_draft
    assert "newsletter_or_bulk" in decision.reasons


def test_no_reply_sender_is_skipped() -> None:
    message = make_message(sender="no-reply@updates.example.com")
    decision = MessageFilter(FilterConfig(sender_patterns=["no-reply"]), IDENTITY).evaluate(message)
    assert not decision.should_draft
    assert "automated_or_no_reply" in decision.reasons


def test_cc_only_is_skipped() -> None:
    message = make_message(to=["team@example.com"], cc=["jude@example.com"])
    decision = MessageFilter(FilterConfig(), IDENTITY).evaluate(message)
    assert not decision.should_draft
    assert "cc_only" in decision.reasons


def test_directly_addressed_message_is_eligible() -> None:
    message = make_message()
    decision = MessageFilter(FilterConfig(), IDENTITY).evaluate(message)
    assert decision.should_draft
    assert decision.score >= 2


def test_alias_address_in_to_recognizes_identity() -> None:
    identity = IdentityConfig(name="Jude Bayham", email="jbayham@colostate.edu")
    message = MailboxMessage(
        id="msg-1",
        conversation_id="conv-1",
        subject="Hello",
        from_recipient=Recipient(name="Sender", address="person@example.com"),
        to_recipients=[Recipient(name="Bayham,Jude", address="jude.bayham@colostate.edu")],
        cc_recipients=[],
        body_content="<p>Can you take a look?</p>",
        body_content_type="html",
        headers={},
    )
    decision = MessageFilter(FilterConfig(), identity).evaluate(message)
    assert decision.should_draft
    assert "recipient:to_alias" in decision.matched_signals


def test_group_broadcast_without_salutation_is_skipped() -> None:
    message = make_message(
        to=["jude@example.com", "alex@example.com", "sam@example.com", "taylor@example.com", "team@example.com"],
        body="<p>Please see the weekly notes.</p>",
    )
    decision = MessageFilter(FilterConfig(group_alias_patterns=["team@"]), IDENTITY).evaluate(message)
    assert not decision.should_draft
    assert "not_confidently_addressed_to_me" in decision.reasons


def test_calendar_message_is_skipped() -> None:
    message = make_message(
        subject="Accepted: Planning session",
        headers={"content-class": "urn:content-classes:calendarmessage"},
    )
    decision = MessageFilter(FilterConfig(), IDENTITY).evaluate(message)
    assert not decision.should_draft
    assert "automated_or_no_reply" in decision.reasons
