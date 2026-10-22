"""시험용 가짜 LLM — 실제 LM Studio 를 부르지 않고 engine/llm_local.py 를 시험한다 (08 D-23 4, 20 10장 H-3).

두 가지:
  - `FakeLLMServer` — **127.0.0.1 에만** 묶는 threading HTTP 서버. OpenAI 호환 `GET /v1/models`·`POST /v1/chat/completions`
    를 흉내 내고, 미리 정한 **합성** 응답을 차례로 돌려준다. 실제 소켓·urllib 길(프록시 무시·넘겨주기 거부·시간 초과·
    연결 끊김)을 시험할 때 쓴다. 받은 요청은 메모리(`requests`)에만 둔다(파일·로그 없음, 표준 오류에도 안 씀).
  - `FakeTransport` — 네트워크 없이 `LocalLLMClient(transport=…)` 에 꽂는 함수 주입형. 같은 '단계' 표를 쓴다.

단계(step) — dict 하나가 응답 하나. 다 쓰면 `default` 단계를 되풀이한다.
  {"content": "…"}                 200, 채팅 완성 응답(choices[0].message.content). "model"·"finish_reason" 도 줄 수 있다
  {"status": 500, "body": …}        그 상태 코드와 본문(dict 면 JSON, bytes·str 그대로)
  {"raw": b"…"}                     200, 그 바이트 그대로(형식 오류 시험)
  {"redirect": "http://…"}          302 + Location(클라이언트는 따라가면 안 된다)
  {"delay": 초, …}                  그만큼 쉰 뒤 나머지 단계대로(시간 초과 시험). FakeTransport 에서는 쉬지 않고 무시
  {"drop": True}                    응답 없이 연결을 끊는다(FakeTransport: ConnectionResetError)
  {"error": "off"|"timeout"|"reset"}  FakeTransport 전용 — 연결 거부·시간 초과·끊김 예외
  {"respond": f}                    200, content = f(받은 messages 목록) — 요청 자료에 맞춰 합성 줄을 짓는 가짜(f 가 실패하면 500).
                                    default 로 주면 모든 요청에 쓴다(tools/gen_e2e.py)

예문은 모두 합성이다(실제 시청자 채팅·닉네임·이모티콘 코드가 아님. 이모티콘 코드는 {:zz_fake_01:} 꼴).
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable, Mapping, Sequence

FAKE_MODEL = "fake-local-model"
# 합성 채팅 줄(시험용). 실제 채팅에서 가져오지 않았다.
SYNTHETIC_LINES = (
    "오 방금 그 장면 진짜 신기했어요",
    "다음 판은 어떤 캐릭터로 하실 거예요?",
    "저도 그 게임 해 봤는데 어렵더라고요",
    "그 노래 제목이 뭐였죠?",
    "{:zz_fake_01:} 너무 웃겨요",
)
DEFAULT_CONTENT = "\n".join(SYNTHETIC_LINES[:3])


def completion_body(content: str, *, model: str = FAKE_MODEL, finish_reason: str = "stop",
                    usage: Mapping[str, int] | None = None) -> dict:
    """OpenAI 호환 채팅 완성 응답 모양(LM Studio 가 돌려주는 칸만)."""
    return {
        "id": "chatcmpl-fake-0001", "object": "chat.completion", "created": 1792713600, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
        "usage": dict(usage) if usage is not None else {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def models_body(ids: Iterable[str]) -> dict:
    return {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "fake"} for i in ids]}


def _render(step: Mapping, model: str) -> tuple[int, dict, bytes]:
    """단계 → (상태, 머리, 본문 바이트). drop·error·delay 는 부르는 쪽이 따로 본다."""
    if "redirect" in step:
        return 302, {"Location": str(step["redirect"])}, b""
    if "raw" in step:
        raw = step["raw"]
        return int(step.get("status", 200)), {"Content-Type": "application/json"}, (
            raw if isinstance(raw, bytes) else str(raw).encode("utf-8"))
    if "status" in step:
        b = step.get("body", {"error": {"message": "fake error"}})
        data = json.dumps(b, ensure_ascii=False).encode("utf-8") if isinstance(b, (dict, list)) else (
            b if isinstance(b, bytes) else str(b).encode("utf-8"))
        return int(step["status"]), {"Content-Type": "application/json"}, data
    body = completion_body(str(step.get("content", DEFAULT_CONTENT)), model=str(step.get("model", model)),
                           finish_reason=str(step.get("finish_reason", "stop")))
    return 200, {"Content-Type": "application/json"}, json.dumps(body, ensure_ascii=False).encode("utf-8")


def _respond(step: Mapping, js) -> dict:
    """{"respond": f} 단계면 f(받은 messages 목록) 의 글을 content 로 바꾼다(요청에 맞춰 답하는 합성 가짜, 통합 스모크용).
    f 가 실패하거나 글이 아니면 500. 다른 단계는 그대로."""
    fn = step.get("respond")
    if not callable(fn):
        return dict(step)
    msgs = js.get("messages") if isinstance(js, dict) else None
    try:
        content = fn(list(msgs) if isinstance(msgs, list) else [])
    except Exception:  # noqa: BLE001 — 가짜 쪽 실패는 서버 오류로
        return {"status": 500, "body": {"error": {"message": "fake respond failed"}}}
    if not isinstance(content, str):
        return {"status": 500, "body": {"error": {"message": "fake respond failed"}}}
    out = {k: v for k, v in step.items() if k != "respond"}
    out["content"] = content
    return out


class _Script:
    """단계 표를 차례로 꺼낸다(스레드 안전). 다 쓰면 default."""

    def __init__(self, steps: Sequence[Mapping] | None, default: Mapping):
        self._steps = [dict(s) for s in (steps or ())]
        self._default = dict(default)
        self._lock = threading.Lock()

    def next(self) -> dict:
        with self._lock:
            return self._steps.pop(0) if self._steps else dict(self._default)

    def remaining(self) -> int:
        with self._lock:
            return len(self._steps)


# ───────────────────────────────────────────────────────────── 가짜 서버

class FakeLLMServer:
    """127.0.0.1 의 빈 포트에 뜨는 가짜 LM Studio. `with FakeLLMServer([...]) as srv: srv.base_url` 처럼 쓴다.

    steps        채팅 완성 요청에 차례로 줄 단계(위 모듈 설명).
    models       GET /v1/models 가 돌려줄 모델 id. models_steps 를 주면 그 표를 먼저 쓴다(꺼짐 흉내는 서버를 안 띄우면 됨).
    default      단계를 다 쓴 뒤 되풀이할 단계.
    requests     받은 요청 [{method, path, json 또는 None, body_bytes}] — 메모리에만.
    """

    def __init__(self, steps: Sequence[Mapping] | None = None, *, models: Sequence[str] = (FAKE_MODEL,),
                 models_steps: Sequence[Mapping] | None = None, default: Mapping | None = None):
        self.models = list(models)
        self._chat = _Script(steps, default or {"content": DEFAULT_CONTENT})
        self._models = _Script(models_steps, {"__models__": True})
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        assert self._httpd is not None, "서버가 떠 있지 않습니다"
        return int(self._httpd.server_address[1])

    @property
    def host(self) -> str:
        assert self._httpd is not None
        return str(self._httpd.server_address[0])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def remaining(self) -> int:
        return self._chat.remaining()

    def _handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):        # 표준 오류에 아무것도 쓰지 않는다
                return

            def _body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n > 0 else b""

            def _send(self, status: int, headers: Mapping[str, str], data: bytes):
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                if data:
                    self.wfile.write(data)
                self.close_connection = True

            def _serve(self, step: Mapping, render):
                try:
                    if step.get("delay"):
                        time.sleep(float(step["delay"]))
                    if step.get("drop"):
                        self.close_connection = True
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return
                    status, headers, data = render(step)
                    self._send(status, headers, data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass                          # 클라이언트가 먼저 끊음(시간 초과 시험)

            def _record(self, method: str, body: bytes):
                try:
                    js = json.loads(body.decode("utf-8")) if body else None
                except (ValueError, UnicodeDecodeError):
                    js = None
                with srv._lock:
                    srv.requests.append({"method": method, "path": self.path, "json": js, "body_bytes": len(body)})

            def do_GET(self):                     # noqa: N802
                self._record("GET", b"")
                if self.path.rstrip("/") == "/v1/models":
                    step = srv._models.next()

                    def render(s):
                        if s.get("__models__"):
                            return 200, {"Content-Type": "application/json"}, json.dumps(
                                models_body(srv.models)).encode("utf-8")
                        return _render(s, FAKE_MODEL)
                    self._serve(step, render)
                else:
                    self._send(404, {"Content-Type": "application/json"}, b'{"error":"not found"}')

            def do_POST(self):                    # noqa: N802
                body = self._body()
                self._record("POST", body)
                if self.path.rstrip("/") == "/v1/chat/completions":
                    model = FAKE_MODEL
                    js = None
                    try:
                        js = json.loads(body.decode("utf-8"))
                        model = str(js.get("model") or FAKE_MODEL)
                    except (ValueError, UnicodeDecodeError, AttributeError):
                        pass
                    self._serve(_respond(srv._chat.next(), js), lambda s: _render(s, model))
                else:
                    self._send(404, {"Content-Type": "application/json"}, b'{"error":"not found"}')

        return H

    def start(self) -> "FakeLLMServer":
        if self._httpd is not None:
            return self
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())   # 이 PC 의 루프백에만 묶는다
        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="fake-llm", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def __enter__(self) -> "FakeLLMServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def closed_port() -> int:
    """127.0.0.1 에서 아무도 듣지 않는 포트(잠깐 묶었다 놓음). '서버 꺼짐' 시험용."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


# ───────────────────────────────────────────────────────────── 함수 주입형

class FakeTransport:
    """`LocalLLMClient(transport=FakeTransport([...]))` — 네트워크 없이 단계 표대로 (상태, 바이트) 를 돌려주거나 예외를 낸다.

    calls 에 받은 (method, url, json 또는 None, timeout) 을 남긴다(메모리에만)."""

    def __init__(self, steps: Sequence[Mapping] | None = None, *, models: Sequence[str] = (FAKE_MODEL,),
                 default: Mapping | None = None):
        self.models = list(models)
        self._script = _Script(steps, default or {"content": DEFAULT_CONTENT})
        self.calls: list[dict] = []

    def __call__(self, method: str, url: str, body: bytes | None, timeout: float) -> tuple[int, bytes]:
        try:
            js = json.loads(body.decode("utf-8")) if body else None
        except (ValueError, UnicodeDecodeError):
            js = None
        self.calls.append({"method": method, "url": url, "json": js, "timeout": timeout})
        if method == "GET" and url.rstrip("/").endswith("/v1/models"):
            return 200, json.dumps(models_body(self.models)).encode("utf-8")
        step = self._script.next()
        err = step.get("error")
        if err == "off":
            raise ConnectionRefusedError("fake off")
        if err == "timeout":
            raise TimeoutError("fake timeout")
        if err == "reset" or step.get("drop"):
            raise ConnectionResetError("fake reset")
        model = str((js or {}).get("model") or FAKE_MODEL) if isinstance(js, dict) else FAKE_MODEL
        status, _headers, data = _render(_respond(step, js), model)
        return status, data


__all__ = ["FAKE_MODEL", "SYNTHETIC_LINES", "DEFAULT_CONTENT", "completion_body", "models_body",
           "FakeLLMServer", "FakeTransport", "closed_port"]
