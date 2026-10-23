"""엔진 로컬 HTTP 막이 (engine/netguard.py) 와 그것을 쓰는 두 길 — engine/llm_local.py(채팅) · engine/semantic.py(임베딩).
25 보고 5-2 의 5번: 프록시 환경 변수가 걸려 있어도 127.0.0.1 로만 가고, 3xx 를 따라가지 않으며, 밖 주소는 거부한다.

실제 LM Studio 는 부르지 않는다(D-24 6). 서버는 모두 이 파일이 127.0.0.1 의 빈 포트에 띄우는 합성 서버다:
  target  부를 곳(합성 응답)          proxy  '프록시' 인 척하는 서버(요청이 오면 실패)     trap  3xx 가 넘겨주는 곳
아래 `_net` 이 모든 시험에서 socket.create_connection·socket.getaddrinfo 를 감싸 **루프백 밖 연결·이름 풀이**
(localhost 이름 풀이 포함)와 LM Studio 기본 포트(1234) 연결을 시험 실패로 만든다(외부 호출 0 확인).
예문은 합성이고, 본문·닉네임은 쓰지 않는다.
"""
from __future__ import annotations

import ast
import json
import os
import socket
import socketserver
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engine import llm_local as L
from engine import netguard as NG
from engine import semantic as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
LOOPBACK = ("127.0.0.1", "::1")


# ───────────────────────────────────────────────────────────── 합성 서버

class _Srv:
    """127.0.0.1 의 빈 포트에 뜨는 작은 HTTP 서버. route(method, path, body) → (상태, 머리, 바이트). hits 에 (method, path)."""

    def __init__(self, route=None):
        self.hits: list[tuple[str, str]] = []
        self.route = route or (lambda m, p, b: (200, {}, b"{}"))
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):        # 표준 오류에 아무것도 쓰지 않는다
                pass

            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                srv.hits.append((self.command, self.path))
                st, hd, data = srv.route(self.command, self.path, body)
                self.send_response(st)
                for k, v in hd.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _go

        class _Server(ThreadingHTTPServer):
            daemon_threads = True

            def server_bind(self):            # getfqdn(이름 풀이)을 하지 않는다
                socketserver.TCPServer.server_bind(self)
                self.server_name, self.server_port = self.server_address[:2]

        self._httpd = _Server(("127.0.0.1", 0), H)
        self._t = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()


def _json(d) -> bytes:
    return json.dumps(d).encode("utf-8")


MODELS = {"object": "list", "data": [{"id": "synthetic-embed-small"}, {"id": "synthetic-chat-7b"}]}


def _lmstudio_route(m, p, b):
    """합성 LM Studio: /v1/models · /v1/embeddings(입력 수만큼 3차원 벡터)."""
    if p.rstrip("/") == "/v1/models":
        return 200, {}, _json(MODELS)
    if p == "/v1/embeddings":
        n = len(json.loads(b.decode("utf-8"))["input"])
        return 200, {}, _json({"data": [{"index": i, "embedding": [1.0, float(i), 0.0]} for i in range(n)]})
    return 404, {}, _json({"error": "not found"})


def _redirect_to(location: str, code: int = 302):
    return lambda m, p, b: (code, {"Location": location}, b"")


@pytest.fixture(autouse=True)
def _net(monkeypatch):
    """루프백 밖 연결·이름 풀이와 포트 1234 연결을 막고 기록한다. 프록시 변수는 시험마다 비우고 시작."""
    seen = {"connect": [], "resolve": []}
    real_conn, real_gai = socket.create_connection, socket.getaddrinfo

    def conn(address, *a, **kw):
        host, port = address[0], int(address[1])
        seen["connect"].append((host, port))
        assert host in LOOPBACK, "루프백 밖 연결 시도"
        assert port != 1234, "실제 LM Studio 기본 포트로 연결 시도"
        return real_conn(address, *a, **kw)

    def gai(host, *a, **kw):
        seen["resolve"].append(host)
        assert host in LOOPBACK, "숫자 루프백이 아닌 이름 풀이 시도"
        return real_gai(host, *a, **kw)

    monkeypatch.setattr(socket, "create_connection", conn)
    monkeypatch.setattr(socket, "getaddrinfo", gai)
    for k in (*PROXY_VARS, "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    return seen


def _set_proxy(monkeypatch, proxy_url: str):
    for k in PROXY_VARS:
        monkeypatch.setenv(k, proxy_url)


# ───────────────────────────────────────────────────────────── 주소

@pytest.mark.parametrize("url", ["http://127.0.0.1:1235", "http://localhost:8080/v1", "http://[::1]:9", "https://127.0.0.1"])
def test_is_local_url_accepts(url):
    assert NG.is_local_url(url)


@pytest.mark.parametrize("url", [
    None, "", "http://example.com", "http://192.168.0.10:1234", "http://10.0.0.5", "http://127.0.0.2:1234",
    "http://0.0.0.0:1234", "http://127.1:1234", "http://2130706433", "http://0x7f000001", "http://[::ffff:127.0.0.1]",
    "http://localhost.:1234", "http://localhost.example.com", "http://127.0.0.1@example.com/", "http://u:p@127.0.0.1",
    "http://127.0.0.1:1234?next=http://example.com", "http://example.com#@127.0.0.1", "ftp://127.0.0.1/",
    "http://ｌｏｃａｌｈｏｓｔ:1234", " http://127.0.0.1",
])
def test_is_local_url_rejects(url):
    assert not NG.is_local_url(url)


def test_require_local_url_and_pin():
    assert NG.require_local_url("http://localhost:1234/v1/") == "http://127.0.0.1:1234"
    assert NG.require_local_url("http://[::1]:5000") == "http://[::1]:5000"
    assert NG.pin_loopback("http://LOCALHOST:7/v1/models") == "http://127.0.0.1:7/v1/models"
    with pytest.raises(NG.NotLocalError) as ei:
        NG.require_local_url("http://example.com")
    assert ei.value.code == "not_local" and isinstance(ei.value, ValueError)


@pytest.mark.parametrize("url", ["http://example.com/v1/models", "http://192.168.0.10:1234/v1/models",
                                 "http://127.0.0.1@example.com/", "http://localhost.:1234/"])
def test_external_refused_before_any_socket(monkeypatch, _net, url):
    _set_proxy(monkeypatch, "http://127.0.0.1:9")            # 프록시가 걸려 있어도
    with pytest.raises(NG.NotLocalError):
        NG.local_request("GET", url, None, 1.0)
    with pytest.raises(L.LLMAddressError):
        L.urllib_transport("GET", url, None, 1.0)
    assert _net["connect"] == [] and _net["resolve"] == []    # 소켓·이름 풀이 0


# ───────────────────────────────────────────────────────────── 프록시 무시

def test_proxy_env_would_be_used_by_plain_urllib(monkeypatch):
    """대조: 표준 urllib 의 기본 opener 는 프록시 변수를 따른다(이 시험 장치가 의미 있음을 보임). 프록시도 이 PC 의 합성 서버."""
    with _Srv() as proxy, _Srv() as target:
        _set_proxy(monkeypatch, proxy.url)
        with urllib.request.build_opener().open(target.url + "/v1/models", timeout=3) as r:
            r.read()
        assert len(proxy.hits) == 1 and target.hits == []


def test_local_request_ignores_proxy_env(monkeypatch, _net):
    with _Srv() as proxy, _Srv(_lmstudio_route) as target:
        _set_proxy(monkeypatch, proxy.url)
        st, data = NG.local_request("GET", target.url + "/v1/models", None, 3.0)
        assert st == 200 and json.loads(data) == MODELS
        assert proxy.hits == [] and target.hits == [("GET", "/v1/models")]
    assert {h for h, _p in _net["connect"]} == {"127.0.0.1"}
    assert {p for _h, p in _net["connect"]} == {target.port}


def test_localhost_pinned_to_127_no_name_lookup(monkeypatch, _net):
    with _Srv(_lmstudio_route) as target:
        st, _ = NG.local_request("GET", f"http://localhost:{target.port}/v1/models", None, 3.0)
    assert st == 200
    assert "localhost" not in _net["resolve"] and _net["connect"] == [("127.0.0.1", target.port)]


# ───────────────────────────────────────────────────────────── 3xx 안 따름

@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_not_followed_local_target(code):
    with _Srv() as trap, _Srv(_redirect_to(None, code)) as target:
        target.route = _redirect_to(trap.url + "/v1/models", code)
        st, _ = NG.local_request("POST", target.url + "/v1/embeddings", b"{}", 3.0)
        assert st == code
        assert trap.hits == [] and len(target.hits) == 1


def test_redirect_to_external_not_followed(_net):
    with _Srv(_redirect_to("http://example.invalid/v1/models")) as target:
        st, _ = NG.local_request("GET", target.url + "/v1/models", None, 3.0)
    assert st == 302 and "example.invalid" not in _net["resolve"]


def test_redirect_to_same_server_not_followed():
    with _Srv(_redirect_to("/v1/other")) as target:
        st, _ = NG.local_request("GET", target.url + "/v1/models", None, 3.0)
        assert st == 302 and target.hits == [("GET", "/v1/models")]


def test_response_size_cap():
    big = b"x" * 5000
    with _Srv(lambda m, p, b: (200, {}, big)) as target:
        with pytest.raises(NG.BadHTTPError):
            NG.local_request("GET", target.url + "/", None, 3.0, max_bytes=1000)
        st, data = NG.local_request("GET", target.url + "/", None, 3.0, max_bytes=5000)
        assert st == 200 and data == big


def test_closed_port_is_refused():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(ConnectionRefusedError):
        NG.local_request("GET", f"http://127.0.0.1:{port}/v1/models", None, 0.5)


# ───────────────────────────────────────────────────────────── llm_local 이 같은 함수를 씀

def test_llm_local_uses_netguard():
    assert L.is_local_url is NG.is_local_url
    assert L.LOCAL_HOSTS == NG.LOCAL_HOSTS
    assert L._BadHTTP is NG.BadHTTPError
    assert _imported(os.path.join(ROOT, "engine", "llm_local.py")).isdisjoint({"urllib.request", "http.client"})
    # 규칙 몸통(opener·프록시·3xx)은 netguard 한 곳 — llm_local 은 urllib.request 를 직접 쓰지 않는다


def test_llm_local_proxy_ignored_and_redirect_refused(monkeypatch):
    with _Srv() as proxy, _Srv() as trap, _Srv(_lmstudio_route) as target:
        _set_proxy(monkeypatch, proxy.url)
        c = L.LocalLLMClient(target.url, timeout=3.0, retry_delay=0.0)
        assert "synthetic-chat-7b" in c.list_models()
        target.route = _redirect_to(trap.url + "/v1/models")
        with pytest.raises(L.LLMError) as ei:
            c.list_models()
        assert ei.value.code == "redirect_refused"
        assert proxy.hits == [] and trap.hits == []


# ───────────────────────────────────────────────────────────── semantic(임베딩) 이 같은 함수를 씀

def test_semantic_external_refused(_net):
    for url in ("http://example.com:1234", "https://api.example.com", "http://127.0.0.1@example.com",
                "http://2130706433:1234", "http://localhost.:1234"):
        with pytest.raises(ValueError):
            S.probe_lmstudio(url)
        with pytest.raises(NG.NotLocalError):
            S.LmStudioEmbedder(url, "m")
        emb, st = S.load_default(backend="lmstudio", lmstudio_url=url)
        assert emb is None and st["status"] == "backend_unavailable"
    assert _net["connect"] == [] and _net["resolve"] == []


def test_semantic_probe_and_encode_ignore_proxy(monkeypatch, _net):
    with _Srv() as proxy, _Srv(_lmstudio_route) as target:
        _set_proxy(monkeypatch, proxy.url)
        p = S.probe_lmstudio(f"http://localhost:{target.port}", timeout=3.0)
        assert p == {"running": True, "embedding_models": ["synthetic-embed-small"]}
        emb, st = S.load_default(backend="lmstudio", lmstudio_url=f"http://localhost:{target.port}/v1")
        assert st["status"] == "ok" and emb.base_url == target.url            # localhost → 127.0.0.1, '/v1' 뗌
        vecs = emb.encode(["합성 문장 하나", "합성 문장 둘"])
        assert len(vecs) == 2 and all(abs(sum(x * x for x in v) - 1.0) < 1e-9 for v in vecs)
        assert proxy.hits == []
        assert ("POST", "/v1/embeddings") in target.hits
    assert {h for h, _p in _net["connect"]} == {"127.0.0.1"} and "localhost" not in _net["resolve"]


def test_semantic_redirect_not_followed():
    with _Srv() as trap, _Srv(_redirect_to(None)) as target:
        target.route = _redirect_to(trap.url + "/v1/models")
        assert S.probe_lmstudio(target.url, timeout=3.0) == {"running": False, "embedding_models": []}
        emb = S.LmStudioEmbedder(target.url, "synthetic-embed-small", timeout=3.0)
        with pytest.raises(S.LmStudioCallError) as ei:
            emb.encode(["합성 문장"])
        assert ei.value.code == "redirect_refused" and isinstance(ei.value, OSError)
        assert trap.hits == [] and len(target.hits) == 2


def test_semantic_bad_and_error_responses():
    with _Srv(lambda m, p, b: (500, {}, b"{}")) as target:
        assert S.probe_lmstudio(target.url, timeout=3.0)["running"] is False
        with pytest.raises(S.LmStudioCallError) as ei:
            S.LmStudioEmbedder(target.url, "m", timeout=3.0).encode(["합성"])
        assert ei.value.code == "http_error" and ei.value.status == 500
        target.route = lambda m, p, b: (200, {}, b"not json")
        assert S.probe_lmstudio(target.url, timeout=3.0)["running"] is False
        with pytest.raises(S.LmStudioCallError) as ei:
            S.LmStudioEmbedder(target.url, "m", timeout=3.0).encode(["합성"])
        assert ei.value.code == "bad_response"
        target.route = lambda m, p, b: (200, {}, _json({"data": []}))       # 입력 수와 다름
        with pytest.raises(S.LmStudioCallError):
            S.LmStudioEmbedder(target.url, "m", timeout=3.0).encode(["합성"])


def test_semantic_has_no_direct_urlopen():
    assert _imported(os.path.join(ROOT, "engine", "semantic.py")).isdisjoint({"urllib.request", "http.client"})


# ───────────────────────────────────────────────────────────── 표준 라이브러리만

def _imported(path: str) -> set[str]:
    """파일이 import 하는 모듈 이름(함수 안 import 포함). 상대 import 는 '.' 을 앞에 붙인다."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add("." * node.level + (node.module or ""))
    return mods


def test_netguard_stdlib_only():
    std = set(sys.stdlib_module_names) | {"__future__"}
    mods = _imported(os.path.join(ROOT, "engine", "netguard.py"))
    assert not any(m.startswith(".") for m in mods)
    assert {m.split(".")[0] for m in mods} <= std, mods
