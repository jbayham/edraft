from __future__ import annotations

from edraft.graph_client import GraphClient
from edraft.models import MailboxMessage, ThreadContext


class ThreadContextBuilder:
    def __init__(self, graph_client: GraphClient, *, max_messages: int) -> None:
        self.graph_client = graph_client
        self.max_messages = max_messages

    def build(self, message: MailboxMessage) -> ThreadContext:
        if not message.conversation_id:
            return ThreadContext(conversation_id=None, related_messages=[])
        related = self.graph_client.list_conversation_messages(
            message.conversation_id,
            exclude_message_id=message.id,
            limit=self.max_messages,
        )
        return ThreadContext(conversation_id=message.conversation_id, related_messages=related)
