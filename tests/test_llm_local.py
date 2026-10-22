"""H-3 로컬 LLM 클라이언트 (engine/llm_local.py) · 가짜 서버 (engine/llm_fake.py) — 모두 합성.

실제 LM Studio 는 부르지 않는다(08 D-23 4). 가짜 서버는 127.0.0.1 의 빈 포트에만 뜬다. 아래 `_only_loopback` 이
모든 시험에서 socket.create_connection 을 감싸 **루프백이 아닌 주소와 LM Studio 기본 포트(1234)** 로의 연결을 시험 실패로
만든다(외부 호출 0 확인). 예문·닉네임·식별자는 합성이다(이모티콘 코드는 {:zz_fake_01:} 꼴).
기록 파일은 pytest tmp_path(conftest 가 %LOCALAPPDATA%\\ChatCoachSmoke\\pytest 아래로 옮김)에만 쓴다.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import socket
from datetime import datetime, timedelta, timezone

import pytest

from engine import llm_fake as F
from engine import llm_local as L

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))
FIXED = datetime(2026, 10, 23, 12, 0, tzinfo=KST)

# 합성 전사문(실제 방송 아님)
TRANSCRIPT = "오늘은 새로 나온 퍼즐 게임을 해 볼게요. 첫 판은 연습이라 천천히 갑니다. 이 블록을 여기로 옮기면 되겠죠?"
SYSTEM = "시청자 채팅 3개를 한 줄에 하나씩 쓰세요. 실제 사람 이름을 부르지 마세요."
SYN_NICK = "합성닉네임가나"          # 합성 원 닉네임


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _msgs(user: str = TRANSCRIPT):
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


@pytest.fixture(autouse=True)
def _only_loopback(monkeypatch):
    """루프백 밖·포트 1234 로 가는 연결을 막고, 어디로 연결했는지 남긴다."""
    seen: list[tuple[str, int]] = []
    real = socket.create_connection

    def guard(address, *a, **kw):
        host, port = address[0], int(address[1])
        seen.append((host, port))
        assert host in ("127.0.0.1", "::1"), f"루프백 밖 연결 시도: {host}"
        assert port != 1234, "실제 LM Studio 기본 포트로 연결 시도"
        return real(address, *a, **kw)

    monkeypatch.setattr(socket, "create_connection", guard)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    return seen


def _client(url, **kw):
    kw.setdefault("timeout", 2.0)
    kw.setdefault("retry_delay", 0.0)
    kw.setdefault("clock", lambda: FIXED)
    return L.LocalLLMClient(url, **kw)


# ───────────────────────────────────────────────────────────── 주소 검사

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234", "http://127.0.0.1:1234/", "http://127.0.0.1:1234/v1", "http://localhost:1234",
    "http://LOCALHOST:1234", "http://[::1]:1234", "http://127.0.0.1", "https://127.0.0.1:8443",
])
def test_is_local_url_accepts(url):
    assert L.is_local_url(url)


@pytest.mark.parametrize("url", [
    None, "", 1234, "127.0.0.1:1234", "localhost:1234", "ftp://127.0.0.1/", "file:///C:/x",
    "http://192.168.0.10:1234", "http://10.0.0.5:1234", "http://example.com", "http://api.example.com/v1",
    "http://127.0.0.2:1234", "http://0.0.0.0:1234", "http://127.1:1234", "http://2130706433:1234",
    "http://0x7f000001:1234", "http://[::ffff:127.0.0.1]:1234", "http://[0:0:0:0:0:0:0:1]:1234",
    "http://localhost.:1234", "http://localhost.example.com:1234", "http://127.0.0.1.nip.io:1234",
    "http://127.0.0.1@example.com/", "http://user:pw@127.0.0.1:1234", "http://example.com#@127.0.0.1",
    "http://127.0.0.1:1234?next=http://example.com", "http://127.0.0.1:99999", "http://127.0.0.1:12a4",
    " http://127.0.0.1:1234", "http://127.0.0.1:1234\n", "http://ｌｏｃａｌｈｏｓｔ:1234", "http://[::1%25eth0]:1234",
    "http://127.0.0.1%2e:1234", "http:///v1",
])
def test_is_local_url_rejects(url):
    assert not L.is_local_url(url)


def test_require_local_url_rewrites_localhost():
    assert L.require_local_url("http://localhost:1234/v1/") == "http://127.0.0.1:1234"
    assert L.require_local_url("http://[::1]:5000") == "http://[::1]:5000"
    assert L.is_local_url(L.DEFAULT_BASE_URL)


def test_non_local_client_refused_before_any_call():
    tr = F.FakeTransport()
    for url in ("http://192.168.0.10:1234", "http://example.com", "http://127.0.0.1@example.com"):
        with pytest.raises(L.LLMAddressError) as ei:
            L.LocalLLMClient(url, transport=tr)
        assert ei.value.code == "not_local"
    assert tr.calls == []


def test_localhost_called_as_127_0_0_1():
    tr = F.FakeTransport([{"content": "합성 줄 하나"}])
    c = _client("http://localhost:4321", transport=tr)
    c.chat(_msgs(), model=F.FAKE_MODEL)
    assert tr.calls[0]["url"] == "http://127.0.0.1:4321/v1/chat/completions"


def test_urllib_transport_refuses_non_local_directly():
    with pytest.raises(L.LLMAddressError):
        L.urllib_transport("GET", "http://example.com/v1/models", None, 1.0)


# ───────────────────────────────────────────────────────────── 보낼 글 검사 (D-06 2)

@pytest.mark.parametrize("text,kind", [
    ("이 채팅 vw_abcdefgh2345 이 쓴 거", "viewer_id"),
    ("VW_ABCDEFGH2345", "viewer_id"),
    ("해시 " + hashlib.sha256(b"synthetic").hexdigest(), "hash"),
    ("chat c" + "0123456789abcdef0123", "hash"),
    ("숫자만 " + "1" * 32, "hash"),
    ("id 12345678-1234-4abc-8def-1234567890ab 입니다", "uuid"),
])
def test_privacy_patterns(text, kind):
    assert kind in L.privacy_problems(text)


def test_privacy_nickname_squashed_and_one_char_ignored():
    assert L.privacy_problems(f"{SYN_NICK}님 안녕", [SYN_NICK]) == {"nickname": 1}
    assert L.privacy_problems("합성 닉네임-가나 왔다", [SYN_NICK]) == {"nickname": 1}     # 띄어쓰기·기호 무시
    assert L.privacy_problems("가 나 다", ["가"]) == {}                                  # 한 글자 닉네임은 대조 안 함


@pytest.mark.parametrize("text", [
    TRANSCRIPT, "{:zz_fake_01:} 너무 웃겨요", "전화 말고 문자로 01012345678 은 검사 대상 아님", "deadbeef 는 짧다",
    "2026년 10월 23일 12시", "facebook 이야기",
])
def test_privacy_clean_text_passes(text):
    assert L.privacy_problems(text) == {}


@pytest.mark.parametrize("bad", [
    "앞 장면에서 vw_abcdefgh2345 가 말함", hashlib.sha256(b"x").hexdigest(), f"{SYN_NICK} 어서 와요",
])
def test_privacy_blocks_send_and_records(bad, tmp_path):
    tr = F.FakeTransport()
    log = str(tmp_path / "llm-send.jsonl")
    c = _client("http://127.0.0.1:1", transport=tr, nicknames=[SYN_NICK], log_path=log)
    with pytest.raises(L.LLMPrivacyError) as ei:
        c.chat(_msgs(TRANSCRIPT + " " + bad), model=F.FAKE_MODEL)
    assert ei.value.code == "privacy"
    assert tr.calls == []                                     # 보내지 않았다
    rec = ei.value.record
    assert rec["sent"] is False and rec["attempts"] == 0 and rec["code"] == "privacy" and rec["privacy"]
    blob = json.dumps(rec, ensure_ascii=False) + str(ei.value) + _read(log)
    assert bad not in blob and SYN_NICK not in blob and TRANSCRIPT not in blob   # 걸린 글·닉네임·본문 없음


def test_nicknames_per_call_are_added():
    tr = F.FakeTransport()
    c = _client("http://127.0.0.1:1", transport=tr)
    with pytest.raises(L.LLMPrivacyError):
        c.chat(_msgs(f"{SYN_NICK} 왔어요"), model=F.FAKE_MODEL, nicknames=[SYN_NICK])
    assert tr.calls == []


def test_too_long_not_sent():
    tr = F.FakeTransport()
    c = _client("http://127.0.0.1:1", transport=tr, max_send_chars=50)
    with pytest.raises(L.LLMError) as ei:
        c.chat(_msgs(), model=F.FAKE_MODEL)
    assert ei.value.code == "too_long" and tr.calls == []
    assert ei.value.record["sent_chars"] == len(SYSTEM) + len(TRANSCRIPT)


# ───────────────────────────────────────────────────────────── 가짜 서버로 성공 길

def test_chat_success_records_counts_not_text(tmp_path, _only_loopback):
    content = "\n".join(F.SYNTHETIC_LINES[:3])
    log = str(tmp_path / "llm-send.jsonl")
    with F.FakeLLMServer([{"content": content, "model": "fake-served-model"}]) as srv:
        assert srv.host == "127.0.0.1"
        c = _client(srv.base_url, log_path=log)
        r = c.chat(_msgs(), model=F.FAKE_MODEL, temperature=0.3, max_tokens=64, seed=7, prompt_template_id="synth-v0")
        got = srv.requests[-1]
        port = srv.port
    assert r.text == content and r.model == "fake-served-model" and r.finish_reason == "stop"
    assert r.response_sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
    body = got["json"]
    assert got["path"] == "/v1/chat/completions" and body["temperature"] == 0.3 and body["max_tokens"] == 64
    assert body["seed"] == 7 and body["model"] == F.FAKE_MODEL and body["stream"] is False
    rec = r.record
    assert rec["ok"] is True and rec["attempts"] == 1 and rec["sent"] is True
    assert rec["sent_chars"] == len(SYSTEM) + len(TRANSCRIPT) == rec["sent_chars_total"]
    assert rec["model"] == "fake-served-model" and rec["model_requested"] == F.FAKE_MODEL
    assert rec["service"] == L.SERVICE and rec["host"].startswith("127.0.0.1:") and rec["at"] == FIXED.isoformat()
    assert rec["response_chars"] == len(content) and rec["usage"]["completion_tokens"] == 20
    lines = _read(log).splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == rec
    blob = lines[0]
    for s in (content, TRANSCRIPT, SYSTEM, *F.SYNTHETIC_LINES[:3]):
        assert s not in blob                                  # 기록에 본문 없음
    assert {p for _h, p in _only_loopback} == {port}           # 가짜 서버 포트만 연결


def test_proxy_env_ignored(monkeypatch, _only_loopback):
    dead = F.closed_port()
    for k in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(k, f"http://127.0.0.1:{dead}")
    with F.FakeLLMServer([{"content": "합성 줄"}]) as srv:
        r = _client(srv.base_url).chat(_msgs(), model=F.FAKE_MODEL)
    assert r.text == "합성 줄"
    assert dead not in {p for _h, p in _only_loopback}


def test_redirect_not_followed():
    with F.FakeLLMServer([{"redirect": "http://example.invalid/v1/chat/completions"},
                          {"redirect": "/v1/chat/completions"}]) as srv:
        c = _client(srv.base_url)
        for _ in range(2):
            with pytest.raises(L.LLMError) as ei:
                c.chat(_msgs(), model=F.FAKE_MODEL)
            assert ei.value.code == "redirect_refused" and ei.value.record["attempts"] == 1
        assert len([q for q in srv.requests if q["method"] == "POST"]) == 2   # 넘겨준 곳으로 다시 가지 않음


# ───────────────────────────────────────────────────────────── 꺼짐 · 시간 초과 · 형식 오류 · 다시 하기

def test_server_off():
    url = f"http://127.0.0.1:{F.closed_port()}"
    c = _client(url, timeout=1.0)
    with pytest.raises(L.LLMError) as ei:
        c.chat(_msgs(), model=F.FAKE_MODEL)
    assert ei.value.code == "lmstudio_off" and ei.value.record["attempts"] == 2   # 한 번 더 해 봄
    with pytest.raises(L.LLMError) as ei:
        c.check_server(F.FAKE_MODEL)
    assert ei.value.code == "lmstudio_off"


def test_timeout_then_give_up():
    with F.FakeLLMServer([{"delay": 1.5, "content": "늦은 줄"}, {"delay": 1.5, "content": "늦은 줄"}]) as srv:
        c = _client(srv.base_url, timeout=0.3)
        with pytest.raises(L.LLMError) as ei:
            c.chat(_msgs(), model=F.FAKE_MODEL)
    assert ei.value.code == "timeout" and ei.value.record["attempts"] == 2
    assert ei.value.record["sent_chars_total"] == 2 * ei.value.record["sent_chars"]


def test_timeout_then_retry_succeeds():
    with F.FakeLLMServer([{"delay": 1.5, "content": "늦은 줄"}, {"content": "두 번째 합성 줄"}]) as srv:
        r = _client(srv.base_url, timeout=0.3).chat(_msgs(), model=F.FAKE_MODEL)
    assert r.text == "두 번째 합성 줄" and r.record["attempts"] == 2


@pytest.mark.parametrize("bad", [
    {"raw": b"not json"}, {"raw": b"[1,2]"}, {"raw": b'{"choices": []}'},
    {"raw": b'{"choices": [{"message": {"content": null}}]}'}, {"raw": b"\xff\xfe"},
])
def test_bad_format_retried_then_fails(bad):
    with F.FakeLLMServer([bad, bad]) as srv:
        with pytest.raises(L.LLMError) as ei:
            _client(srv.base_url).chat(_msgs(), model=F.FAKE_MODEL)
    assert ei.value.code == "bad_response" and ei.value.record["attempts"] == 2


def test_bad_format_then_good():
    with F.FakeLLMServer([{"raw": b"{"}, {"content": "고친 합성 줄"}]) as srv:
        r = _client(srv.base_url).chat(_msgs(), model=F.FAKE_MODEL)
    assert r.text == "고친 합성 줄" and r.record["attempts"] == 2


def test_server_error_retry_and_4xx_no_retry():
    with F.FakeLLMServer([{"status": 500}, {"content": "합성 줄"}]) as srv:
        r = _client(srv.base_url).chat(_msgs(), model=F.FAKE_MODEL)
    assert r.record["attempts"] == 2
    with F.FakeLLMServer([{"status": 400}, {"content": "안 와야 함"}]) as srv:
        with pytest.raises(L.LLMError) as ei:
            _client(srv.base_url).chat(_msgs(), model=F.FAKE_MODEL)
        assert srv.remaining() == 1
    assert ei.value.code == "http_error" and ei.value.status == 400 and ei.value.record["attempts"] == 1
    with F.FakeLLMServer([{"status": 404, "body": {"error": {"message": "model not loaded"}}}]) as srv:
        with pytest.raises(L.LLMError) as ei:
            _client(srv.base_url).chat(_msgs(), model="없는-모델")
    assert ei.value.code == "model_missing"


def test_connection_dropped():
    with F.FakeLLMServer([{"drop": True}, {"drop": True}]) as srv:
        with pytest.raises(L.LLMError) as ei:
            _client(srv.base_url).chat(_msgs(), model=F.FAKE_MODEL)
    assert ei.value.code == "connection_lost" and ei.value.record["attempts"] == 2


def test_retries_zero():
    tr = F.FakeTransport([{"error": "off"}, {"content": "안 와야 함"}])
    with pytest.raises(L.LLMError) as ei:
        _client("http://127.0.0.1:1", transport=tr, retries=0).chat(_msgs(), model=F.FAKE_MODEL)
    assert ei.value.code == "lmstudio_off" and len(tr.calls) == 1


@pytest.mark.parametrize("err,code", [("off", "lmstudio_off"), ("timeout", "timeout"), ("reset", "connection_lost")])
def test_fake_transport_errors(err, code):
    tr = F.FakeTransport([{"error": err}, {"error": err}])
    with pytest.raises(L.LLMError) as ei:
        _client("http://127.0.0.1:1", transport=tr).chat(_msgs(), model=F.FAKE_MODEL)
    assert ei.value.code == code and len(tr.calls) == 2


def test_retry_sleep_injected():
    slept = []
    tr = F.FakeTransport([{"error": "off"}, {"content": "합성"}])
    c = L.LocalLLMClient("http://127.0.0.1:1", transport=tr, retry_delay=0.25, sleep=slept.append)
    c.chat(_msgs(), model=F.FAKE_MODEL)
    assert slept == [0.25]


# ───────────────────────────────────────────────────────────── 서버 확인

def test_check_server_models():
    with F.FakeLLMServer(models=["model-a", "model-b"]) as srv:
        c = _client(srv.base_url)
        assert c.check_server("model-b") == ["model-a", "model-b"]
        with pytest.raises(L.LLMError) as ei:
            c.check_server("model-z")
        assert ei.value.code == "model_missing"
        assert srv.requests[-1]["path"] == "/v1/models" and srv.requests[-1]["method"] == "GET"


def test_check_server_bad_models_body():
    with F.FakeLLMServer(models_steps=[{"raw": b"oops"}, {"raw": b"oops"}]) as srv:
        with pytest.raises(L.LLMError) as ei:
            _client(srv.base_url).check_server()
    assert ei.value.code == "bad_response"


# ───────────────────────────────────────────────────────────── 인자 검사 · 정적 검사

@pytest.mark.parametrize("kw", [
    {"model": ""}, {"model": F.FAKE_MODEL, "temperature": 3.0}, {"model": F.FAKE_MODEL, "max_tokens": 0},
    {"model": F.FAKE_MODEL, "max_tokens": True},
])
def test_bad_arguments(kw):
    with pytest.raises(ValueError):
        _client("http://127.0.0.1:1", transport=F.FakeTransport()).chat(_msgs(), **kw)


def test_bad_messages():
    c = _client("http://127.0.0.1:1", transport=F.FakeTransport())
    for msgs in ([], [{"role": "tool", "content": "x"}], [{"role": "user", "content": None}]):
        with pytest.raises(ValueError):
            c.chat(msgs, model=F.FAKE_MODEL)


def _imports(path):
    tree = ast.parse(_read(path))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0:
            out.add((n.module or "").split(".")[0])
    return out


def test_no_third_party_http_and_no_external_url_literal():
    for name in ("llm_local.py", "llm_fake.py"):
        path = os.path.join(ROOT, "engine", name)
        imps = _imports(path)
        assert not imps & {"requests", "httpx", "aiohttp", "urllib3", "openai"}, (name, imps)
        src = _read(path)
        for m in __import__("re").finditer(r"https?://([A-Za-z0-9.\-\[\]:]+)", src):
            host = m.group(1)
            assert host.startswith(("127.0.0.1", "localhost", "[::1]")) or host in ("", ":"), (name, host)
