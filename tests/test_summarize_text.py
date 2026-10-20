import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from FeedSummarizer import lambda_function
from FeedSummarizer.lambda_function import summarize_text


def _completion(content):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


@pytest.fixture
def openai_server(monkeypatch):
    """OpenAI API の代わりに応答するローカルサーバー

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
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    yield state
    server.shutdown()


def test_summarize_text_sends_chat_completion(openai_server):
    openai_server["responses"].append((200, _completion("• 要点")))

    result = summarize_text("タイトル", "本文")

    assert result == "• 要点"
    (request,) = openai_server["requests"]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == lambda_function.OPENAI_MODEL
    messages = request["body"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "【Title】タイトル\n###\n本文"}


def test_summarize_text_shortens_text_on_bad_request(openai_server, monkeypatch):
    monkeypatch.setattr(lambda_function, "DECREMENT_TOKEN", 2)
    error = {"error": {"message": "too long", "type": "invalid_request_error"}}
    openai_server["responses"] += [(400, error), (200, _completion("ok"))]

    assert summarize_text("t", "abcdef") == "ok"

    # 2回目は本文を短くして再送する
    contents = [r["body"]["messages"][-1]["content"] for r in openai_server["requests"]]
    assert contents == ["【Title】t\n###\nabcdef", "【Title】t\n###\nabcd"]


def test_summarize_text_raises_when_all_retries_fail(openai_server):
    error = {"error": {"message": "too long", "type": "invalid_request_error"}}
    openai_server["responses"] += [(400, error)] * lambda_function.MAX_RETRIES

    with pytest.raises(ValueError):
        summarize_text("t", "x" * 10)
