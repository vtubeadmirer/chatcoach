"""H-3 로컬 LLM 클라이언트 — LM Studio 의 OpenAI 호환 `/v1/chat/completions` 를 **이 PC 에서만** 부른다
(docs/design/20-plan-virtual-chat.md 4-3·8장 8번, 08 D-06·D-23 4).

얇은 클라이언트다. 지시문 만들기·나온 줄 거르기(닉네임·금칙어·베끼기)는 이 모듈 몫이 아니다(부르는 쪽, H-3 다른 담당).
이 모듈이 지키는 것:

  1. **주소** — `is_local_url` 이 `http(s)://localhost | 127.0.0.1 | [::1]` 만 받는다(D-06, 20 8장 8번). 그 밖(사설망 주소·
     다른 이름·사용자 정보가 든 주소·`127.0.0.2`·10진/16진 IP·끝점 붙은 이름·쿼리)은 `LLMAddressError(not_local)`.
     `localhost` 는 이름 풀이를 하지 않고 **127.0.0.1 로 바꿔서** 부른다(hosts 파일이 다른 곳을 가리켜도 밖으로 안 나감).
     표준 urllib 만 쓰고(requests 없음), **프록시 환경 변수를 무시**하고(ProxyHandler({})), **넘겨주기(3xx)를 따라가지 않는다**
     (`redirect_refused`) — 둘 다 이 PC 밖으로 나가는 뒷길이라서.
  2. **보낼 글 검사** — 보내기 **전에** 모든 메시지 본문을 `check_privacy` 로 본다. 'vw_'(가린 시청자 ID), sha 꼴 16진 덩어리
     (chat_id·해시·userIdHash), UUID, 부르는 쪽이 준 원 닉네임 목록(정규화 2자 이상)이 있으면 `LLMPrivacyError(privacy)` 로
     **보내지 않는다**(D-06 2). 오류·기록에는 걸린 종류와 건수만 넣는다(걸린 글·닉네임 없음).
     닉네임은 쉼표·줄바꿈으로 나눈 조각마다 본다(낱말 목록을 이어 붙인 자리의 거짓 양성 없음). 부르는 쪽이 고정 지시문
     틀(`templates=`)을 주면 그 틀 글자는 닉네임 대조에서 뺀다(틀은 코드 상수 — '방송' 같은 닉네임 때문에 모든 요청이 막히지
     않게). 식별자 꼴 검사는 언제나 글 전체.
  3. **필요한 조각만** — 한 번에 보낼 글자 수 상한(`max_send_chars`, 기본 4000자 ≈ 앞뒤 2분 전사문 + 지시문)을 넘으면
     `too_long` 으로 보내지 않는다(D-06 3 의 기계 쪽 막이. '앞뒤 1~2분' 자르기 자체는 부르는 쪽 몫).
  4. **기록** — 부를 때마다(거부·실패 포함) 기록 한 줄: 언제·어느 서비스·몇 글자·모델 이름·응답 해시·시도 수·코드
     (D-06 5). 본문·닉네임은 넣지 않는다. `log_path` 를 주면 그 JSONL 에 덧붙인다(app.jsonl.append). 주지 않으면 메모리
     (`client.records`)에만.
  5. **실패 처리** — 서버 꺼짐 `lmstudio_off` · 연결 끊김 `connection_lost` · 시간 초과 `timeout` · 형식 오류 `bad_response`
     · 서버 오류(5xx) `server_error` 는 **한 번 다시** 해 본다(`retries=1`). 4xx `http_error`·`model_missing`, 주소·검사·길이
     거부는 다시 하지 않는다. 외부로 넘어가지 않는다(대신 부를 곳이 없다).

시험은 실제 LM Studio 를 부르지 않는다: engine/llm_fake.py 의 가짜 서버(127.0.0.1, 합성 응답) 또는 `transport=` 함수 주입.

표준 라이브러리와 app.core.textnorm(정규화)·app.jsonl(기록 덧붙이기)만 쓴다(tests/test_layers.py 의 엔진 허용 목록).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping, Sequence

from app.core import textnorm as TN

CLIENT_NAME = "engine llm_local"
CLIENT_VERSION = "0.1.0"
RECORD_SCHEMA = "chatcoach-llm-send-v1"
SERVICE = "lmstudio-local"                      # 기록의 '어느 서비스'
DEFAULT_BASE_URL = "http://127.0.0.1:1234"      # LM Studio 기본 서버 주소(engine/semantic.py 와 같음)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
CHAT_PATH = "/v1/chat/completions"
MODELS_PATH = "/v1/models"
MAX_RESPONSE_BYTES = 4 << 20                    # 응답을 이보다 많이 읽지 않는다(형식 오류로 본다)
DEFAULT_MAX_SEND_CHARS = 4000
ROLES = ("system", "user", "assistant")

# 다시 해 볼 만한 실패(한 번만). 나머지는 바로 멈춘다.
RETRY_CODES = frozenset({"lmstudio_off", "connection_lost", "timeout", "bad_response", "server_error"})

KST = timezone(timedelta(hours=9))

# transport(method, url, body, timeout) -> (HTTP 상태, 응답 바이트).
# 연결 실패는 ConnectionRefusedError/ConnectionError/OSError, 시간 초과는 TimeoutError 로 알린다.
Transport = Callable[[str, str, "bytes | None", float], "tuple[int, bytes]"]


# ───────────────────────────────────────────────────────────── 오류

class LLMError(Exception):
    """부를 수 없거나 받은 것이 쓸 수 없을 때. code 는 기계가 읽는 짧은 코드, 메시지에는 본문·닉네임을 넣지 않는다."""

    def __init__(self, code: str, message: str = "", *, status: int | None = None, record: dict | None = None):
        super().__init__(message or code)
        self.code = code
        self.status = status
        self.record = record


class LLMAddressError(LLMError):
    """이 PC(localhost·127.0.0.1·::1) 밖 주소. 아무것도 보내지 않았다."""

    def __init__(self, message: str = "LM Studio 주소는 이 PC(localhost·127.0.0.1·::1)만 받습니다(D-06)"):
        super().__init__("not_local", message)


class LLMPrivacyError(LLMError):
    """보낼 글에 닉네임·식별자 꼴이 있어 보내지 않았다. kinds 는 걸린 종류 → 건수(걸린 글은 없음)."""

    def __init__(self, kinds: Mapping[str, int], record: dict | None = None):
        self.kinds = dict(sorted(kinds.items()))
        summary = ", ".join(f"{k} {v}건" for k, v in self.kinds.items())
        super().__init__("privacy", f"보낼 글에 식별자 꼴이 있어 보내지 않습니다(D-06 2): {summary}", record=record)


# ───────────────────────────────────────────────────────────── 주소 검사

_HOST_OK_RE = re.compile(r"\A[A-Za-z0-9.:\-\[\]]+\Z")


def _split_local(url: str):
    """(scheme, host, port, path) 또는 None. is_local_url 과 _normalize_base 가 같이 쓴다."""
    from urllib.parse import urlsplit
    if not isinstance(url, str) or not url or url != url.strip():
        return None
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in url):      # 공백·제어 문자·비 ASCII(이름 흉내) 거부
        return None
    try:
        sp = urlsplit(url)
        port = sp.port                                         # 범위 밖이면 ValueError
    except ValueError:
        return None
    if sp.scheme.lower() not in ("http", "https"):
        return None
    if sp.username is not None or sp.password is not None or "@" in sp.netloc:
        return None                                            # 'http://127.0.0.1@밖' 류 혼동 막기
    if sp.query or sp.fragment or "#" in url or "?" in url:
        return None
    host = (sp.hostname or "").lower()
    if not host or not _HOST_OK_RE.match(sp.netloc) or host not in LOCAL_HOSTS:
        return None
    return sp.scheme.lower(), host, port, sp.path


def is_local_url(url) -> bool:
    """`http(s)://localhost | 127.0.0.1 | [::1]` (포트·경로 허용, 사용자 정보·쿼리·조각 없음) 이면 True. 그 밖은 모두 False."""
    return _split_local(url) is not None


def require_local_url(url) -> str:
    """주소를 검사하고 실제로 부를 기본 주소(끝 '/' 없음)를 돌려준다. localhost 는 127.0.0.1 로 바꾼다(이름 풀이 안 함)."""
    sp = _split_local(url)
    if sp is None:
        raise LLMAddressError()
    scheme, host, port, path = sp
    netloc = "127.0.0.1" if host == "localhost" else ("[::1]" if host == "::1" else host)
    if port is not None:
        netloc += f":{port}"
    path = path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]                                       # 'http://127.0.0.1:1234/v1' 도 같은 서버로
    return f"{scheme}://{netloc}{path}"


# ───────────────────────────────────────────────────────────── 보낼 글 검사 (D-06 2)

_VIEWER_ID_RE = re.compile(r"vw_", re.I)
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_HEX_RUN_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{12,}(?![0-9a-f])", re.I)
_HEX_PURE_MIN = 32         # 숫자만·글자만이어도 이 길이 이상이면 해시로 본다(userIdHash 32자, sha256 64자)
NICKNAME_MIN_LEN = 2       # engine/retrieve.load_nicknames 와 같은 규칙(한 글자 닉네임은 대조하지 않음)


def _nfkc(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", s or "")


def _is_hash_like(run: str) -> bool:
    has_digit = any(c.isdigit() for c in run)
    has_alpha = any(c.isalpha() for c in run)
    return (has_digit and has_alpha) or len(run) >= _HEX_PURE_MIN


def prepare_nicknames(nicknames: Iterable[str] | None) -> frozenset[str]:
    """원 닉네임 목록을 대조용 꼴(textnorm.squash, 2자 이상)로. 목록은 메모리에서만 쓴다."""
    out = set()
    for n in nicknames or ():
        k = TN.squash(n if isinstance(n, str) else "")
        if len(k) >= NICKNAME_MIN_LEN:
            out.add(k)
    return frozenset(out)


# 닉네임 대조 조각 나누기: 쉼표·줄바꿈·목록 구분 기호. 조각마다 squash 해 본다 — 낱말 목록('점심, 메뉴')을 이어 붙인
# 자리('점심메뉴')에서 생기는 거짓 양성을 없앤다. 한 조각 안의 띄어쓰기·기호('합성 닉네임-가나')는 여전히 무시한다.
_CHUNK_SPLIT_RE = re.compile(r"[,\n\r、，;；|]+")


def _template_regex(template: str) -> "re.Pattern[str]":
    """str.format 틀 → 자리({…})마다 (.*?) 묶음인 정규식(fullmatch 용). 틀 문법이 아니면 글 전체를 글자 그대로."""
    import string
    parts = []
    try:
        for lit, fld, _spec, _conv in string.Formatter().parse(template):
            parts.append(re.escape(lit))
            if fld is not None:
                parts.append("(.*?)")
    except ValueError:
        parts = [re.escape(template)]
    return re.compile("".join(parts), re.S)


def _nickname_text(t: str, templates: Sequence["re.Pattern[str]"]) -> str:
    """닉네임 대조에 쓸 부분. 고정 틀(부르는 쪽 코드의 지시문 틀)과 통째로 맞으면 틀의 글자 부분은 빼고 채운 자리만
    (조각 경계로 줄바꿈을 넣어) 돌려준다. 틀 글자는 코드 상수라 시청자 닉네임이 들어갈 수 없다 — '방송'·'채팅' 같은 흔한
    낱말과 같은 닉네임이 있어도 모든 요청이 막히지 않게(거짓 양성). 맞는 틀이 없으면 글 전체."""
    for rx in templates:
        m = rx.fullmatch(t)
        if m is not None:
            return "\n".join(m.groups())
    return t


def privacy_problems(text: str, nicknames: Iterable[str] | frozenset[str] = (), *,
                     templates: Sequence[str] = ()) -> dict[str, int]:
    """글 하나에서 걸린 종류 → 건수. 종류: viewer_id('vw_') · hash(sha 꼴 16진) · uuid · nickname. 없으면 빈 dict.

    nicknames 는 원 닉네임(그대로 줘도 되고 prepare_nicknames 결과여도 됨). 닉네임은 쉼표·줄바꿈으로 나눈 조각마다
    squash 해서 부분 문자열로 찾는다(조각을 넘어 이어 붙인 꼴은 보지 않음). templates(str.format 고정 틀)를 주면 그 틀과
    통째로 맞는 글은 틀 글자를 빼고 채운 자리만 닉네임 대조한다(식별자 꼴 검사는 언제나 글 전체).
    걸린 글·닉네임은 돌려주지 않는다."""
    t = _nfkc(text if isinstance(text, str) else "")
    out: dict[str, int] = {}
    n = len(_VIEWER_ID_RE.findall(t))
    if n:
        out["viewer_id"] = n
    n = len(_UUID_RE.findall(t))
    if n:
        out["uuid"] = n
    rest = _UUID_RE.sub(" ", t)
    n = sum(1 for m in _HEX_RUN_RE.finditer(rest) if _is_hash_like(m.group(0)))
    if n:
        out["hash"] = n
    nk = nicknames if isinstance(nicknames, frozenset) else prepare_nicknames(nicknames)
    if nk:
        rxs = [_template_regex(_nfkc(x)) for x in templates if isinstance(x, str)]
        chunks = [c for c in (TN.squash(p) for p in _CHUNK_SPLIT_RE.split(_nickname_text(t, rxs))) if c]
        n = sum(1 for k in nk if k and len(k) >= NICKNAME_MIN_LEN and any(k in c for c in chunks))
        if n:
            out["nickname"] = n
    return out


def check_template(template: str) -> None:
    """고정 틀을 등록하기 전에 한 번 본다: 틀 글자에 식별자 꼴(vw_·해시·UUID)이 있으면 ValueError(닉네임은 보지 않음 —
    틀은 코드 상수다)."""
    if not isinstance(template, str) or privacy_problems(template):
        raise ValueError("고정 지시문 틀에 식별자 꼴이 있습니다")


def check_privacy(texts: Iterable[str], nicknames: Iterable[str] | frozenset[str] = (), *,
                  templates: Sequence[str] = ()) -> None:
    """모든 글을 보고 하나라도 걸리면 LLMPrivacyError(종류별 건수). 보내기 전에 부른다."""
    nk = nicknames if isinstance(nicknames, frozenset) else prepare_nicknames(nicknames)
    total: dict[str, int] = {}
    for t in texts:
        for k, v in privacy_problems(t, nk, templates=templates).items():
            total[k] = total.get(k, 0) + v
    if total:
        raise LLMPrivacyError(total)


# ───────────────────────────────────────────────────────────── 기본 transport (urllib 만)

def _build_opener():
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        """3xx 를 따라가지 않는다 — 서버가 다른 주소(밖)로 넘겨도 가지 않고 3xx 상태를 그대로 돌려준다."""

        def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D401
            return None

    # ProxyHandler({}) — HTTP_PROXY·HTTPS_PROXY 환경 변수를 무시한다(프록시가 밖에 있으면 글이 그리로 간다).
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def urllib_transport(method: str, url: str, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    """표준 urllib 로 한 번 부른다. HTTP 오류 상태도 (상태, 바이트) 로 돌려주고, 연결 실패·시간 초과만 예외로 낸다.

    주소는 부르는 쪽(LocalLLMClient)이 이미 검사했지만 여기서도 한 번 더 본다(이 함수를 바로 부르는 경우)."""
    import http.client
    import socket
    import urllib.error
    import urllib.request
    if not is_local_url(url):
        raise LLMAddressError()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _build_opener().open(req, timeout=timeout) as r:
            return int(r.status), _read_capped(r)
    except urllib.error.HTTPError as e:
        try:
            data = _read_capped(e)
        except Exception:                      # noqa: BLE001 — 오류 본문은 못 읽어도 상태는 쓴다
            data = b""
        return int(e.code), data
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            # 연결·보내기 단계의 시간 초과(urllib 가 URLError 로 감싼다). 루프백에서 이것은 '듣는 서버 없음' 이다 —
            # 윈도는 닫힌 루프백 포트에 SYN 을 몇 번 다시 보내 거부가 1~2초 늦게 오므로 짧은 timeout 이면 여기로 온다.
            # 응답을 기다리다 난 시간 초과(아래 raw TimeoutError)와 나눈다.
            raise ConnectionRefusedError("connect timeout") from None
        if isinstance(reason, ConnectionRefusedError):
            raise ConnectionRefusedError("refused") from None
        if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            raise ConnectionResetError("lost") from None
        raise ConnectionError("connect") from None           # 그 밖의 연결 실패는 '꺼짐' 으로 센다
    except (TimeoutError, socket.timeout):
        raise TimeoutError("timeout") from None
    except http.client.RemoteDisconnected:
        raise ConnectionResetError("lost") from None
    except http.client.HTTPException:
        raise _BadHTTP() from None


class _BadHTTP(Exception):
    """HTTP 응답 자체가 깨졌다(상태 줄·길이). bad_response 로 센다."""


def _read_capped(r) -> bytes:
    data = r.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise _BadHTTP()
    return data


# ───────────────────────────────────────────────────────────── 결과

@dataclass(frozen=True)
class ChatResult:
    """받은 글(부르는 쪽이 거른다), 응답한 모델 이름, 글의 sha256, 끝난 까닭, 이번 호출 기록(본문 없음)."""
    text: str
    model: str
    response_sha256: str
    finish_reason: str | None
    record: dict = field(compare=False)


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _parse_completion(data: bytes) -> tuple[str, str | None, str | None, dict]:
    """(content, model, finish_reason, usage 숫자만). 모양이 다르면 ValueError."""
    d = json.loads(data.decode("utf-8"))
    if not isinstance(d, dict):
        raise ValueError("not object")
    ch = d.get("choices")
    if not isinstance(ch, list) or not ch or not isinstance(ch[0], dict):
        raise ValueError("choices")
    msg = ch[0].get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("content"), str):
        raise ValueError("content")
    model = d.get("model") if isinstance(d.get("model"), str) else None
    fr = ch[0].get("finish_reason")
    fr = fr if isinstance(fr, str) else None
    usage = {}
    u = d.get("usage")
    if isinstance(u, dict):
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = u.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                usage[k] = v
    return msg["content"], model, fr, usage


def _parse_models(data: bytes) -> list[str]:
    d = json.loads(data.decode("utf-8"))
    rows = d.get("data") if isinstance(d, dict) else None
    if not isinstance(rows, list):
        raise ValueError("data")
    return [r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)]


def _error_mentions_model(data: bytes) -> bool:
    try:
        d = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    err = d.get("error") if isinstance(d, dict) else None
    s = json.dumps(err, ensure_ascii=False).lower() if err is not None else ""
    return "model" in s


# ───────────────────────────────────────────────────────────── 클라이언트

class LocalLLMClient:
    """LM Studio(OpenAI 호환) 로컬 서버 클라이언트. 만들 때 주소를 검사한다(밖이면 LLMAddressError).

    base_url        'http://127.0.0.1:1234' 꼴(끝의 '/v1' 은 있어도 됨). localhost 는 127.0.0.1 로 부른다.
    timeout         한 번 부를 때 기다리는 초.
    retries         다시 해 볼 횟수(기본 1 — RETRY_CODES 일 때만).
    nicknames       원 닉네임 목록(보낼 글 검사용, 메모리에서만). 부를 때 chat(nicknames=) 로 더할 수도 있다.
    max_send_chars  한 번에 보낼 글자 수 상한(D-06 3). None 이면 상한 없음.
    log_path        주면 기록을 그 JSONL 에 덧붙인다(본문 없음). 없으면 records 에만.
    templates       부르는 쪽 코드의 고정 지시문 틀(str.format 꼴). 만들 때 식별자 꼴을 한 번 보고, 보낼 글 검사의 닉네임
                    대조에서는 틀과 통째로 맞는 메시지의 틀 글자를 뺀다(채운 자리만 대조).
    transport       시험용 함수 주입(llm_fake.FakeTransport). 기본은 urllib_transport.
    clock·sleep     시험용(기록 시각, 다시 하기 전 쉼).
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, timeout: float = 60.0, retries: int = 1,
                 retry_delay: float = 0.5, nicknames: Iterable[str] | None = None,
                 max_send_chars: int | None = DEFAULT_MAX_SEND_CHARS, log_path: str | None = None,
                 transport: Transport | None = None, clock: Callable[[], datetime] | None = None,
                 sleep: Callable[[float], None] | None = None, templates: Sequence[str] = ()):
        self.base_url = require_local_url(base_url)
        for tpl in templates:
            check_template(tpl)                # 만들 때 한 번(식별자 꼴). 닉네임 대조에서는 틀 글자를 뺀다
        self._templates = tuple(templates)
        if timeout <= 0:
            raise ValueError("timeout 은 0 보다 커야 합니다")
        if retries < 0:
            raise ValueError("retries 는 0 이상")
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.retry_delay = float(retry_delay)
        self._nicknames = prepare_nicknames(nicknames)
        self.max_send_chars = max_send_chars
        self.log_path = log_path
        self._transport = transport or urllib_transport
        self._clock = clock or (lambda: datetime.now(KST))
        self._sleep = sleep or time.sleep
        self.records: list[dict] = []

    # ── 내부
    @property
    def host(self) -> str:
        from urllib.parse import urlsplit
        return urlsplit(self.base_url).netloc

    def _url(self, path: str) -> str:
        url = self.base_url + path
        if not is_local_url(url):              # 경로를 붙인 뒤에도 한 번 더(만들 때 검사했지만 막이는 두 겹)
            raise LLMAddressError()
        return url

    def _call(self, method: str, path: str, body: bytes | None, parse: Callable[[bytes], object]):
        """(상태, 읽은 값 또는 None, 응답 바이트, 시도 수, 실패 코드 또는 None).

        2xx 면 parse(바이트) 를 부르고, 그것이 실패하면 bad_response. 다시 해 볼 실패(RETRY_CODES)면 retries 번 더.
        3xx 는 따라가지 않고 redirect_refused, 4xx 는 다시 하지 않고 코드 None·값 None 으로 돌려준다(부르는 쪽이 코드로 바꿈)."""
        url = self._url(path)
        attempts = 0
        code: str | None = None
        status = 0
        data = b""
        while attempts <= self.retries:
            if attempts:
                self._sleep(self.retry_delay)
            attempts += 1
            try:
                status, data = self._transport(method, url, body, self.timeout)
            except LLMAddressError:
                raise
            except TimeoutError:
                code, status, data = "timeout", 0, b""
            except ConnectionRefusedError:
                code, status, data = "lmstudio_off", 0, b""
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                code, status, data = "connection_lost", 0, b""
            except _BadHTTP:
                code, status, data = "bad_response", 0, b""
            except OSError:
                code, status, data = "lmstudio_off", 0, b""
            else:
                if 300 <= status < 400:
                    return status, None, data, attempts, "redirect_refused"
                if 400 <= status < 500:
                    return status, None, data, attempts, None
                if status >= 500 or status < 200:
                    code = "server_error"
                else:
                    try:
                        return status, parse(data), data, attempts, None
                    except (ValueError, UnicodeDecodeError, KeyError, TypeError, AttributeError):
                        code = "bad_response"
            if code not in RETRY_CODES:
                break
        return status, None, data, attempts, code

    def _record(self, rec: dict) -> dict:
        self.records.append(rec)
        if self.log_path:
            from app.jsonl import append    # 엔진 허용 목록(app.jsonl) — 'from app import' 꼴은 층 검사가 막는다
            d = os.path.dirname(os.path.abspath(self.log_path))
            if not os.path.isdir(d):        # app.jsonl.append 는 폴더를 만들지 않는다
                os.makedirs(d, exist_ok=True)
            append(self.log_path, rec)
        return rec

    # ── 서버 확인
    def list_models(self) -> list[str]:
        """GET /v1/models 의 모델 id 목록. 꺼져 있으면 LLMError(lmstudio_off·timeout…)."""
        status, ids, _data, _attempts, code = self._call("GET", MODELS_PATH, None, _parse_models)
        if ids is not None:
            return ids
        code = code or "http_error"
        raise LLMError(code, f"모델 목록을 받지 못했습니다: {code}", status=status or None)

    def check_server(self, model: str | None = None) -> list[str]:
        """서버가 켜져 있는지(20 4-3 '서버 확인'), model 을 주면 그 모델이 올라가 있는지. 모델 목록을 돌려준다.
        서버를 대신 켜거나 모델을 받지 않는다(첫 판)."""
        try:
            ids = self.list_models()
        except LLMError as e:
            if e.code in ("timeout", "connection_lost", "lmstudio_off"):
                raise LLMError("lmstudio_off", "LM Studio 서버가 꺼져 있거나 응답이 없습니다", status=e.status) from None
            raise
        if model is not None and model not in ids:
            raise LLMError("model_missing", "설정한 모델이 LM Studio 에 올라가 있지 않습니다")
        return ids

    # ── 생성
    def chat(self, messages: Sequence[Mapping[str, str]], *, model: str, temperature: float = 0.7,
             max_tokens: int = 256, seed: int | None = None, prompt_template_id: str | None = None,
             nicknames: Iterable[str] | None = None) -> ChatResult:
        """POST /v1/chat/completions 한 번(+ 다시 하기 1회). 성공이면 ChatResult, 아니면 LLMError(code).

        보내기 전에 모든 content 를 검사한다(닉네임·식별자 → LLMPrivacyError, 글자 수 상한 → too_long). 거부·실패도 기록한다."""
        if not isinstance(model, str) or not model:
            raise ValueError("model 이 필요합니다")
        if not (isinstance(temperature, (int, float)) and 0.0 <= float(temperature) <= 2.0):
            raise ValueError("temperature 는 0~2")
        if not (isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens >= 1):
            raise ValueError("max_tokens 는 1 이상 정수")
        msgs = []
        for m in messages:
            role, content = m.get("role"), m.get("content")
            if role not in ROLES or not isinstance(content, str):
                raise ValueError("messages 는 {role: system|user|assistant, content: str} 목록")
            msgs.append({"role": role, "content": content})
        if not msgs:
            raise ValueError("messages 가 비었습니다")
        sent_chars = sum(len(m["content"]) for m in msgs)
        rec = {
            "schema": RECORD_SCHEMA, "client": CLIENT_NAME, "client_version": CLIENT_VERSION,
            "at": self._clock().isoformat(timespec="seconds"), "service": SERVICE, "host": self.host,
            "endpoint": CHAT_PATH, "model_requested": model, "model": None,
            "prompt_template_id": prompt_template_id, "temperature": float(temperature), "max_tokens": max_tokens,
            "seed": seed, "messages": len(msgs), "sent_chars": sent_chars, "sent": False, "attempts": 0,
            "sent_chars_total": 0, "ok": False, "code": None, "http_status": None, "privacy": None,
            "request_sha256": None, "response_sha256": None, "response_chars": None, "finish_reason": None,
            "usage": None, "elapsed_ms": None,
        }
        nk = self._nicknames | prepare_nicknames(nicknames)
        try:
            check_privacy((m["content"] for m in msgs), nk, templates=self._templates)
        except LLMPrivacyError as e:
            rec.update(code="privacy", privacy=e.kinds)
            e.record = self._record(rec)
            raise
        if self.max_send_chars is not None and sent_chars > self.max_send_chars:
            rec["code"] = "too_long"
            raise LLMError("too_long", f"보낼 글이 상한({self.max_send_chars}자)보다 깁니다: {sent_chars}자",
                           record=self._record(rec))
        payload = {"model": model, "messages": msgs, "temperature": float(temperature), "max_tokens": max_tokens,
                   "stream": False}
        if seed is not None:
            payload["seed"] = int(seed)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        rec["request_sha256"] = hashlib.sha256(body).hexdigest()

        t0 = time.monotonic()
        status, result, data, attempts, code = self._call("POST", CHAT_PATH, body, _parse_completion)
        if result is None and code is None:
            code = "model_missing" if status == 404 and _error_mentions_model(data) else "http_error"
        rec.update(sent=attempts > 0, attempts=attempts, sent_chars_total=sent_chars * attempts,
                   http_status=status or None, elapsed_ms=int((time.monotonic() - t0) * 1000))
        if result is None:
            rec["code"] = code
            raise LLMError(code, f"LM Studio 응답을 쓰지 못했습니다: {code}", status=status or None,
                           record=self._record(rec))
        content, rmodel, fr, usage = result
        rmodel = rmodel or model
        h = _sha256_text(content)
        rec.update(ok=True, code=None, model=rmodel, response_sha256=h, response_chars=len(content),
                   finish_reason=fr, usage=usage or None)
        return ChatResult(text=content, model=rmodel, response_sha256=h, finish_reason=fr, record=self._record(rec))


__all__ = [
    "CLIENT_VERSION", "DEFAULT_BASE_URL", "LOCAL_HOSTS", "RECORD_SCHEMA", "SERVICE", "RETRY_CODES",
    "LLMError", "LLMAddressError", "LLMPrivacyError", "ChatResult", "LocalLLMClient",
    "is_local_url", "require_local_url", "prepare_nicknames", "privacy_problems", "check_privacy", "check_template",
    "urllib_transport",
]
