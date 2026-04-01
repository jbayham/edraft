from edraft.config import IdentityConfig
from edraft.draft_creator import DraftCreator
from edraft.models import MailboxMessage, Recipient


def test_render_reply_html_uses_paragraphs_and_signature_break() -> None:
    html = DraftCreator.render_reply_html("First paragraph.\n\nThanks,\nJude")
    assert "<p>First paragraph.</p>" in html
    assert "<p>Thanks,<br>Jude</p>" in html


def test_render_reply_html_splits_long_paragraphs() -> None:
    html = DraftCreator.render_reply_html(
        "Sentence one is long enough to count as part of a longer analytical reply. "
        "Sentence two continues the reasoning and adds more detail for the reviewer. "
        "Sentence three shifts to the recommendation and keeps the explanation concrete. "
        "Sentence four closes with a clear suggested action for the recipient.\n\nThanks,\nJude"
    )
    assert html.count("<p>") >= 3


def test_merge_reply_html_replaces_existing_top_reply_only() -> None:
    existing = """
    <html><body>
    <div class="elementToProof">Old reply.</div>
    <div><br></div>
    <hr>
    <div id="divRplyFwdMsg">Quoted thread</div>
    </body></html>
    """
    merged = DraftCreator.merge_reply_html(
        reply_html='<div class="edraft-reply"><p>New reply.</p></div><div><br></div>',
        existing_html=existing,
    )

    assert "Old reply." not in merged
    assert '<div class="edraft-reply"><p>New reply.</p></div>' in merged
    assert '<div id="divRplyFwdMsg">Quoted thread</div>' in merged
    assert "<hr/>" in merged or "<hr>" in merged


def test_auto_reply_mode_uses_reply_all_when_other_recipients_present() -> None:
    creator = DraftCreator(
        graph_client=None,  # type: ignore[arg-type]
        reply_mode="auto",
        identity=IdentityConfig(name="Jude Bayham", email="jbayham@colostate.edu"),
    )
    message = MailboxMessage(
        id="msg-1",
        conversation_id="conv-1",
        subject="Hello",
        from_recipient=Recipient(name="Sender", address="sender@example.com"),
        to_recipients=[
            Recipient(name="Bayham,Jude", address="jude.bayham@colostate.edu"),
            Recipient(name="Charles Sims", address="cbsims@utk.edu"),
        ],
        cc_recipients=[],
    )
    assert creator.resolve_reply_mode(message) == "reply_all"


def test_auto_reply_mode_uses_reply_for_single_recipient_thread() -> None:
    creator = DraftCreator(
        graph_client=None,  # type: ignore[arg-type]
        reply_mode="auto",
        identity=IdentityConfig(name="Jude Bayham", email="jbayham@colostate.edu"),
    )
    message = MailboxMessage(
        id="msg-1",
        conversation_id="conv-1",
        subject="Hello",
        from_recipient=Recipient(name="Sender", address="sender@example.com"),
        to_recipients=[Recipient(name="Bayham,Jude", address="jude.bayham@colostate.edu")],
        cc_recipients=[],
    )
    assert creator.resolve_reply_mode(message) == "reply"
