import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from FeedSummarizer import lambda_function
from FeedSummarizer.lambda_function import summarize_text


def _message(content, stop_reason="end_turn"):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": lambda_function.CLAUDE_MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


@pytest.fixture
def claude_server(monkeypatch):
    """Claude API の代わりに応答するローカルサーバー

    responses に (ステータス, 本文) を積むと順に返し、受け取ったリクエストを requests に記録する
    """
    state = {"responses": [], "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            state["requests"].append(
                {"path": self.path, "body": json.loads(self.rfile.read(length))}
            )
            status, body = state["responses"].pop(0)
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    yield state
    server.shutdown()


def test_summarize_text_sends_message(claude_server):
    # 思考ブロックが先頭に来ても、テキストブロックだけを要約として使う
    claude_server["responses"].append(
        (
            200,
            _message(
                [
                    {"type": "thinking", "thinking": "", "signature": "sig"},
                    {"type": "text", "text": "• 要点"},
                ]
            ),
        )
    )

    result = summarize_text("タイトル", "本文")

    assert result == "• 要点"
    (request,) = claude_server["requests"]
    assert request["path"] == "/v1/messages"
    body = request["body"]
    assert body["model"] == "claude-haiku-5-5"
    assert body["system"] == lambda_function.SYSTEM_PROMPT
    assert body["output_config"] == {"effort": "low"}
    assert body["messages"] == [
        {
            "role": "user",
            "content": "<article>\n【Title】タイトル\n###\n本文\n</article>",
        }
    ]


def test_summarize_text_truncates_long_text(claude_server, monkeypatch):
    monkeypatch.setattr(lambda_function, "MAX_TEXT_LENGTH", 4)
    claude_server["responses"].append((200, _message([{"type": "text", "text": "ok"}])))

    summarize_text("t", "abcdef")

    content = claude_server["requests"][0]["body"]["messages"][0]["content"]
    assert content == "<article>\n【Title】t\n###\nabcd\n</article>"


def test_summarize_text_raises_on_refusal(claude_server):
    claude_server["responses"].append(
        (200, {**_message([]), "stop_reason": "refusal", "stop_details": None})
    )

    with pytest.raises(ValueError, match="Refused"):
        summarize_text("t", "text")


def test_summarize_text_raises_on_api_error(claude_server):
    error = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low",
        },
    }
    claude_server["responses"].append((400, error))

    with pytest.raises(lambda_function.anthropic.BadRequestError) as excinfo:
        summarize_text("t", "text")

    # 失敗の理由が Slack の一言で分かる
    note = lambda_function.summary_failed_note(excinfo.value)
    assert note.startswith(
        "（要約できませんでした: BadRequestError / invalid_request_error"
    )
    assert "credit balance is too low" in note


def test_summarize_text_raises_on_empty_text():
    with pytest.raises(ValueError):
        summarize_text("t", "   ")
