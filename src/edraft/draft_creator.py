from __future__ import annotations

from edraft.graph_client import GraphClient
from edraft.models import DraftResult, MailboxMessage


class DraftCreator:
    """Create reply drafts only.

    This module intentionally has no send-email behavior. It uses Graph reply-draft
    endpoints so the result stays in Outlook Drafts for manual review.
    """

    def __init__(self, graph_client: GraphClient, *, reply_mode: str) -> None:
        self.graph_client = graph_client
        self.reply_mode = reply_mode

    def create(self, message: MailboxMessage, draft_text: str) -> DraftResult:
        return self.graph_client.create_reply_draft(
            source_message_id=message.id,
            comment=draft_text,
            reply_mode=self.reply_mode,
        )
