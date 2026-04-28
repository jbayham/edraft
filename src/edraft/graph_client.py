from __future__ import annotations

import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from edraft.models import DraftResult, MailboxMessage, Recipient
from edraft.models import CalendarEvent


class GraphApiError(RuntimeError):
    """Raised when Microsoft Graph returns an error response."""


class GraphClient:
    """Thin Microsoft Graph client for reading mail, calendars, and reply drafts."""

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
        include_body: bool = False,
        date_field: str = "receivedDateTime",
    ) -> list[MailboxMessage]:
        return list(
            self.iter_messages(
                folder=folder,
                unread_only=unread_only,
                limit=limit,
                received_after=received_after,
                include_body=include_body,
                date_field=date_field,
            )
        )

    def iter_messages(
        self,
        *,
        folder: str,
        unread_only: bool,
        limit: int,
        received_after: datetime | None = None,
        include_body: bool = False,
        date_field: str = "receivedDateTime",
    ) -> Iterator[MailboxMessage]:
        if date_field not in {"receivedDateTime", "sentDateTime"}:
            raise ValueError("date_field must be receivedDateTime or sentDateTime")
        select_fields = [
            "id",
            "conversationId",
            "internetMessageId",
            "subject",
            "from",
            "toRecipients",
            "ccRecipients",
            "receivedDateTime",
            "sentDateTime",
            "lastModifiedDateTime",
            "isRead",
            "isDraft",
            "importance",
            "hasAttachments",
            "categories",
            "bodyPreview",
            "webLink",
        ]
        if include_body:
            select_fields.extend(["body", "internetMessageHeaders"])
        params = {
            "$top": min(limit, 100),
            "$orderby": f"{date_field} desc",
            "$select": ",".join(select_fields),
        }
        filters: list[str] = []
        if unread_only:
            filters.append("isRead eq false")
        if received_after is not None:
            cutoff = received_after.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            filters.append(f"{date_field} ge {cutoff}")
        if filters:
            params["$filter"] = " and ".join(filters)
        next_url: str | None = f"{self.BASE_URL}/me/mailFolders/{urllib.parse.quote(folder)}/messages"
        yielded = 0
        while next_url and yielded < limit:
            response = self._request("GET", next_url, params=params if next_url.startswith(self.BASE_URL) else None)
            payload = response.json()
            for item in payload.get("value", []):
                yield MailboxMessage.from_graph(item)
                yielded += 1
                if yielded >= limit:
                    break
            next_url = payload.get("@odata.nextLink")
            params = None

    def get_message(self, message_id: str) -> MailboxMessage:
        params = {
            "$select": ",".join(
                [
                    "id",
                    "conversationId",
                    "internetMessageId",
                    "subject",
                    "from",
                    "toRecipients",
                    "ccRecipients",
                    "receivedDateTime",
                    "sentDateTime",
                    "lastModifiedDateTime",
                    "isRead",
                    "isDraft",
                    "importance",
                    "hasAttachments",
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
                    "internetMessageId",
                    "subject",
                    "from",
                    "toRecipients",
                    "ccRecipients",
                    "receivedDateTime",
                    "sentDateTime",
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

    def list_calendar_view(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int = 200,
    ) -> list[CalendarEvent]:
        params = {
            "startDateTime": self._format_utc(start),
            "endDateTime": self._format_utc(end),
            "$top": min(limit, 100),
            "$select": ",".join(
                [
                    "id",
                    "subject",
                    "organizer",
                    "attendees",
                    "start",
                    "end",
                    "location",
                    "bodyPreview",
                    "body",
                    "onlineMeeting",
                    "isAllDay",
                    "isCancelled",
                    "showAs",
                    "webLink",
                ]
            ),
        }
        events: list[CalendarEvent] = []
        next_url: str | None = f"{self.BASE_URL}/me/calendarView"
        while next_url and len(events) < limit:
            response = self._request(
                "GET",
                next_url,
                params=params if next_url.startswith(self.BASE_URL) else None,
                extra_headers={"Prefer": 'outlook.timezone="UTC"'},
            )
            payload = response.json()
            for item in payload.get("value", []):
                events.append(self._calendar_event_from_graph(item))
                if len(events) >= limit:
                    break
            next_url = payload.get("@odata.nextLink")
            params = None
        return events

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
        extra_headers: dict[str, str] | None = None,
        interactive: bool = False,
    ) -> httpx.Response:
        url = path_or_url if path_or_url.startswith("http") else f"{self.BASE_URL}{path_or_url}"
        headers = {
            "Authorization": f"Bearer {self._token_provider(interactive)}",
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        response = self._client.request(method, url, params=params, json=json, headers=headers)
        for _attempt in range(2):
            if response.status_code not in {429, 503}:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = min(float(retry_after), 5.0) if retry_after else 1.0
            except ValueError:
                delay = 1.0
            time.sleep(delay)
            response = self._client.request(method, url, params=params, json=json, headers=headers)
        if response.status_code >= 400:
            raise GraphApiError(
                f"Graph API {method} {url} failed with {response.status_code}: {response.text}"
            )
        return response

    @staticmethod
    def _format_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _calendar_event_from_graph(payload: dict[str, Any]) -> CalendarEvent:
        start = GraphClient._parse_graph_datetime(payload.get("start"))
        end = GraphClient._parse_graph_datetime(payload.get("end"))
        return CalendarEvent(
            id=payload["id"],
            subject=payload.get("subject") or "",
            start=start,
            end=end,
            organizer=Recipient.from_graph(payload.get("organizer")),
            attendees=[
                recipient
                for recipient in (
                    Recipient.from_graph(item) for item in payload.get("attendees", [])
                )
                if recipient is not None
            ],
            location=((payload.get("location") or {}).get("displayName") or None),
            body_preview=payload.get("bodyPreview") or "",
            body_content=(payload.get("body") or {}).get("content") or "",
            body_content_type=((payload.get("body") or {}).get("contentType") or "html").lower(),
            online_meeting_url=((payload.get("onlineMeeting") or {}).get("joinUrl") or None),
            is_all_day=bool(payload.get("isAllDay", False)),
            is_cancelled=bool(payload.get("isCancelled", False)),
            show_as=(payload.get("showAs") or None),
            web_link=payload.get("webLink"),
        )

    @staticmethod
    def _parse_graph_datetime(payload: dict[str, Any] | None) -> datetime:
        if not payload:
            raise GraphApiError("Graph calendar event is missing a start/end time")
        value = payload.get("dateTime")
        if not value:
            raise GraphApiError("Graph calendar event is missing a dateTime value")
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
