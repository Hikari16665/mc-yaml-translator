"""Minimal OpenAI-compatible streaming (and non-stream) mock server for tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse


HandlerFn = Callable[[dict[str, Any], dict[str, str]], tuple[int, Any]]


def _sse_chunks(text: str, n: int = 4) -> list[bytes]:
    if not text:
        pieces = [""]
    else:
        size = max(1, (len(text) + n - 1) // n)
        pieces = [text[i : i + size] for i in range(0, len(text), size)]
    lines: list[bytes] = []
    for i, part in enumerate(pieces):
        payload = {"id": "chatcmpl-test", "choices": [{"index": 0, "delta": {"content": part}}]}
        if i == 0:
            payload["choices"][0]["delta"]["role"] = "assistant"
        lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
    lines.append(b"data: [DONE]\n\n")
    return lines


class MockOpenAI:
    """Configurable mock. `handler(body, headers) -> (status, payload)`.

    payload may be:
      - str: streamed as SSE content (the raw model output)
      - dict: if status==200, streamed as JSON string; otherwise returned as error JSON
      - ("raw_body", bytes, content_type)
    """

    def __init__(self, handler: HandlerFn | None = None) -> None:
        self.handler = handler or default_handler
        self.requests: list[dict[str, Any]] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

    def start(self) -> "MockOpenAI":
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except json.JSONDecodeError:
                    body = {}
                rec = {
                    "path": urlparse(self.path).path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
                mock.requests.append(rec)
                if rec["path"] not in {
                    "/chat/completions",
                    "/v1/chat/completions",
                } and not rec["path"].endswith("/chat/completions"):
                    self._send(404, {"error": {"message": "not found"}})
                    return
                status, payload = mock.handler(body, rec["headers"])
                if status != 200:
                    if isinstance(payload, (dict, list)):
                        self._send(status, payload)
                    else:
                        self._send(status, {"error": {"message": str(payload)}})
                    return
                if body.get("stream") is False:
                    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                    resp = {
                        "id": "chatcmpl-test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": content},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                    self._send(200, resp)
                    return
                content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                for chunk in _sse_chunks(content):
                    self.wfile.write(chunk)
                    self.wfile.flush()

            def _send(self, status: int, payload: Any) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        # Port 0 → ephemeral
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._httpd.server_address
        self.base_url = f"http://127.0.0.1:{port}/v1"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "MockOpenAI":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def _user_text(body: dict[str, Any]) -> str:
    msgs = body.get("messages") or []
    for m in reversed(msgs):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def ok_payload(translation: str) -> str:
    return json.dumps({"status": "ok", "translation": translation}, ensure_ascii=False)


def default_handler(body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    """Echo the source with a 译: prefix; placeholders stay intact."""
    src = _user_text(body)
    return 200, ok_payload("译:" + src)


def scripted_handler(script: Callable[[dict[str, Any], int], tuple[int, Any]]) -> HandlerFn:
    counter = {"n": 0}

    def inner(body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
        counter["n"] += 1
        return script(body, counter["n"])

    return inner
