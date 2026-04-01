import json

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
