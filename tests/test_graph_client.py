import json
from datetime import datetime, timezone

import httpx

from edraft.graph_client import GraphClient


def test_create_reply_draft_uses_threaded_reply_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content.decode()) if request.content else None
        return httpx.Response(
            200,
            json={"id": "draft-1", "conversationId": "conv-1", "webLink": "https://outlook.office.com"},
        )

    client = GraphClient(
        lambda _interactive: "token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.create_reply_draft(source_message_id="message-1", comment=None, reply_mode="reply")
    assert result.id == "draft-1"
    assert captured["method"] == "POST"
    assert str(captured["url"]).endswith("/me/messages/message-1/createReply")
    assert captured["json"] is None
    assert "/send" not in str(captured["url"])


def test_list_calendar_view_uses_calendarview_endpoint_and_parses_events() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "event-1",
                        "subject": "Meeting",
                        "start": {"dateTime": "2026-04-07T15:00:00", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-04-07T15:30:00", "timeZone": "UTC"},
                        "isAllDay": False,
                        "showAs": "busy",
                    }
                ]
            },
        )

    client = GraphClient(
        lambda _interactive: "token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    events = client.list_calendar_view(
        start=datetime(2026, 4, 7, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 4, 7, 11, 0, tzinfo=timezone.utc),
    )

    assert len(events) == 1
    assert events[0].id == "event-1"
    assert captured["method"] == "GET"
    assert "/me/calendarView" in str(captured["url"])
    assert "startDateTime=" in str(captured["url"])
    assert "endDateTime=" in str(captured["url"])
