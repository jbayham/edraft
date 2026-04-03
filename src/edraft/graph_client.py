from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from edraft.models import DraftResult, MailboxMessage


class GraphApiError(RuntimeError):
    """Raised when Microsoft Graph returns an error response."""


class GraphClient:
    """Thin Microsoft Graph client for reading mail and creating reply drafts."""

    BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(
        self,
        token_provider: Callable[[bool], str],
        *,
        timeout_seconds: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get_me(self) -> dict[str, Any]:
        response = self._request("GET", "/me", interactive=True)
        return response.json()

    def list_messages(
        self,
        *,
        folder: str,
        unread_only: bool,
        limit: int,
        received_after: datetime | None = None,
    ) -> list[MailboxMessage]:
        params = {
            "$top": min(limit, 100),
            "$orderby": "receivedDateTime desc",
            "$select": ",".join(
                [
                    "id",
                    "conversationId",
                    "subject",
                    "from",
                    "toRecipients",
                    "ccRecipients",
                    "receivedDateTime",
                    "isRead",
                    "isDraft",
                    "categories",
                    "bodyPreview",
                    "webLink",
                ]
            ),
        }
        filters: list[str] = []
        if unread_only:
            filters.append("isRead eq false")
        if received_after is not None:
            cutoff = received_after.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            filters.append(f"receivedDateTime ge {cutoff}")
        if filters:
            params["$filter"] = " and ".join(filters)
        messages: list[MailboxMessage] = []
        next_url: str | None = f"{self.BASE_URL}/me/mailFolders/{urllib.parse.quote(folder)}/messages"
        while next_url and len(messages) < limit:
            response = self._request("GET", next_url, params=params if next_url.startswith(self.BASE_URL) else None)
            payload = response.json()
            for item in payload.get("value", []):
                messages.append(MailboxMessage.from_graph(item))
                if len(messages) >= limit:
                    break
            next_url = payload.get("@odata.nextLink")
            params = None
        return messages

    def get_message(self, message_id: str) -> MailboxMessage:
        params = {
            "$select": ",".join(
                [
                    "id",
                    "conversationId",
                    "subject",
                    "from",
                    "toRecipients",
                    "ccRecipients",
                    "receivedDateTime",
                    "isRead",
                    "isDraft",
                    "categories",
                    "bodyPreview",
                    "body",
                    "internetMessageHeaders",
                    "webLink",
                    "inferenceClassification",
                    "parentFolderId",
                    "conversationIndex",
                ]
            )
        }
        response = self._request("GET", f"/me/messages/{urllib.parse.quote(message_id)}", params=params)
        return MailboxMessage.from_graph(response.json())

    def list_conversation_messages(
        self,
        conversation_id: str,
        *,
        exclude_message_id: str | None = None,
        limit: int = 5,
    ) -> list[MailboxMessage]:
        escaped = conversation_id.replace("'", "''")
        params = {
            "$top": min(limit + 5, 20),
            "$filter": f"conversationId eq '{escaped}'",
            "$select": ",".join(
                [
                    "id",
                    "conversationId",
                    "subject",
                    "from",
                    "toRecipients",
                    "ccRecipients",
                    "receivedDateTime",
                    "isDraft",
                    "bodyPreview",
                    "body",
                ]
            ),
        }
        response = self._request("GET", "/me/messages", params=params)
        messages = [
            MailboxMessage.from_graph(item)
            for item in response.json().get("value", [])
            if item.get("id") != exclude_message_id and not item.get("isDraft", False)
        ]
        min_datetime = datetime.min.replace(tzinfo=timezone.utc)
        messages.sort(key=lambda item: item.received_at or min_datetime)
        return messages[-limit:]

    def create_reply_draft(
        self,
        *,
        source_message_id: str,
        comment: str | None = None,
        reply_mode: str = "reply",
    ) -> DraftResult:
        action = "createReplyAll" if reply_mode == "reply_all" else "createReply"
        payload = {"comment": comment} if comment is not None else None
        response = self._request(
            "POST",
            f"/me/messages/{urllib.parse.quote(source_message_id)}/{action}",
            json=payload,
        )
        return DraftResult.from_graph(response.json())

    def update_message_body_html(self, message_id: str, html_content: str) -> None:
        self._request(
            "PATCH",
            f"/me/messages/{urllib.parse.quote(message_id)}",
            json={
                "body": {
                    "contentType": "HTML",
                    "content": html_content,
                }
            },
        )

    def add_category_to_message(self, message_id: str, category: str, existing: list[str]) -> None:
        categories = list(dict.fromkeys([*existing, category]))
        self._request(
            "PATCH",
            f"/me/messages/{urllib.parse.quote(message_id)}",
            json={"categories": categories},
        )

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        interactive: bool = False,
    ) -> httpx.Response:
        url = path_or_url if path_or_url.startswith("http") else f"{self.BASE_URL}{path_or_url}"
        headers = {
            "Authorization": f"Bearer {self._token_provider(True)}",
            "Accept": "application/json",
        }
        response = self._client.request(method, url, params=params, json=json, headers=headers)
        if response.status_code >= 400:
            raise GraphApiError(
                f"Graph API {method} {url} failed with {response.status_code}: {response.text}"
            )
        return response
