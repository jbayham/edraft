from __future__ import annotations

from datetime import datetime

from edraft.graph_client import GraphClient
from edraft.models import MailboxMessage


class MessageFetcher:
    def __init__(
        self,
        graph_client: GraphClient,
        *,
        processed_category: str | None,
    ) -> None:
        self.graph_client = graph_client
        self.processed_category = processed_category.casefold() if processed_category else None

    def fetch_unread_messages(
        self,
        *,
        folders: list[str],
        unread_only: bool,
        max_messages: int,
        received_after: datetime | None = None,
    ) -> list[MailboxMessage]:
        results: list[MailboxMessage] = []
        for folder in folders:
            remaining = max_messages - len(results)
            if remaining <= 0:
                break
            messages = self.graph_client.list_messages(
                folder=folder,
                unread_only=unread_only,
                limit=remaining,
                received_after=received_after,
            )
            for message in messages:
                if self.processed_category and any(
                    category.casefold() == self.processed_category for category in message.categories
                ):
                    continue
                if received_after and (
                    message.received_at is None or message.received_at < received_after
                ):
                    continue
                results.append(message)
                if len(results) >= max_messages:
                    break
        return results
