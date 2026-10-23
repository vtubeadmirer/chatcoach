"""엔진의 로컬 HTTP 막이 — 이 PC(localhost·127.0.0.1·::1)의 서버만 부르는 공통 함수
(docs/design/20-plan-virtual-chat.md 8장 8번, 08 D-06·D-17 6·D-24 7, 25 보고 5-2 의 5번).

engine/llm_local.py(LM Studio 채팅)와 engine/semantic.py(LM Studio 임베딩)가 **같은 규칙**으로 부르도록 한곳에 모았다.

  1. **주소** — `is_local_url` 은 `http(s)://localhost | 127.0.0.1 | [::1]` (포트·경로 허용)만 받는다. 그 밖은 모두 거부:
     사설망·다른 이름·`127.0.0.2`·`0.0.0.0`·10진/16진/줄인 IP(`2130706433`·`0x7f000001`·`127.1`)·IPv4 매핑 IPv6
     (`[::ffff:127.0.0.1]`)·풀어 쓴 IPv6·끝점 붙은 이름(`localhost.`)·사용자 정보(`127.0.0.1@밖`)·쿼리·조각·공백·비 ASCII.
     거부는 `NotLocalError`(ValueError, code 'not_local') — 소켓을 열기 전, 이름 풀이도 하기 전이다.
  2. **이름 풀이 안 함** — `localhost` 는 **127.0.0.1 로 바꿔서** 부른다(hosts 파일이 다른 곳을 가리켜도 밖으로 안 나감).
  3. **프록시 무시** — `ProxyHandler({})`. HTTP_PROXY·HTTPS_PROXY·ALL_PROXY 환경 변수와 윈도 인터넷 설정의 프록시를 모두
     쓰지 않는다(프록시가 밖에 있으면 보낸 글이 그리로 간다).
  4. **넘겨주기(3xx) 안 따름** — 서버가 다른 주소(밖)로 넘겨도 가지 않고 3xx 상태를 그대로 돌려준다. 부르는 쪽이 실패로 센다
     (llm_local 은 `redirect_refused`).
  5. **응답 크기 상한** — `max_bytes` 보다 많이 읽지 않는다(넘으면 `BadHTTPError`).

`local_request` 는 HTTP 오류 상태도 (상태, 바이트) 로 돌려주고, 연결 실패·시간 초과만 표준 예외로 낸다:
  ConnectionRefusedError(듣는 서버 없음·연결 단계 시간 초과) · ConnectionResetError(연결 끊김) · ConnectionError(그 밖 연결 실패)
  · TimeoutError(응답 기다리다 시간 초과) · BadHTTPError(HTTP 응답 자체가 깨짐·크기 넘음) · NotLocalError(주소 거부).

표준 라이브러리만 쓴다(tests/test_layers.py 의 엔진 규칙). 본문을 기록·출력하지 않는다.
"""
from __future__ import annotations

import re
from typing import Mapping

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
DEFAULT_MAX_RESPONSE_BYTES = 4 << 20

_HOST_OK_RE = re.compile(r"\A[A-Za-z0-9.:\-\[\]]+\Z")


class NotLocalError(ValueError):
    """이 PC(localhost·127.0.0.1·::1) 밖 주소. 아무것도 보내지 않았다(소켓·이름 풀이 전)."""

    code = "not_local"

    def __init__(self, message: str = "주소는 이 PC(localhost·127.0.0.1·::1)만 받습니다(D-06)"):
        super().__init__(message)


class BadHTTPError(Exception):
    """HTTP 응답 자체가 깨졌다(상태 줄·길이) 또는 크기 상한을 넘었다. 부르는 쪽은 bad_response 로 센다."""

    code = "bad_response"


# ───────────────────────────────────────────────────────────── 주소 검사

def split_local(url) -> "tuple[str, str, int | None, str] | None":
    """(scheme, host(소문자), port, path) 또는 None(이 PC 주소가 아님)."""
    from urllib.parse import urlsplit
    if not isinstance(url, str) or not url or url != url.strip():
        return None
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in url):      # 공백·제어 문자·비 ASCII(이름 흉내) 거부
        return None
    try:
        sp = urlsplit(url)
        port = sp.port                                         # 범위 밖·숫자 아님이면 ValueError
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
    return split_local(url) is not None


def _netloc(host: str, port: int | None) -> str:
    n = "127.0.0.1" if host == "localhost" else ("[::1]" if host == "::1" else host)
    return n if port is None else f"{n}:{port}"


def pin_loopback(url: str) -> str:
    """주소를 검사하고 `localhost` 를 127.0.0.1 로 바꾼 같은 주소(경로 그대로). 밖이면 NotLocalError."""
    sp = split_local(url)
    if sp is None:
        raise NotLocalError()
    scheme, host, port, path = sp
    return f"{scheme}://{_netloc(host, port)}{path}"


def require_local_url(url) -> str:
    """서버 기본 주소(끝 '/' 없음, 끝 '/v1' 뗌)를 돌려준다. localhost 는 127.0.0.1 로. 밖이면 NotLocalError.

    'http://localhost:1234/v1/' → 'http://127.0.0.1:1234'."""
    sp = split_local(url)
    if sp is None:
        raise NotLocalError()
    scheme, host, port, path = sp
    path = path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]                                       # 'http://127.0.0.1:1234/v1' 도 같은 서버로
    return f"{scheme}://{_netloc(host, port)}{path}"


# ───────────────────────────────────────────────────────────── 부르기 (urllib 만)

def build_opener():
    """프록시를 쓰지 않고(ProxyHandler({})) 넘겨주기를 따라가지 않는 urllib opener."""
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        """3xx 를 따라가지 않는다 — 서버가 다른 주소(밖)로 넘겨도 가지 않고 3xx 상태를 HTTPError 로 그대로 둔다."""

        def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D401
            return None

    # ProxyHandler({}) — 환경 변수·윈도 인터넷 설정의 프록시를 무시한다(빈 표가 기본 getproxies() 를 대신한다).
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def read_capped(r, max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES) -> bytes:
    """응답을 max_bytes 까지만 읽는다. 넘으면 BadHTTPError."""
    data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BadHTTPError("response too large")
    return data


def local_request(method: str, url: str, body: bytes | None = None, timeout: float = 30.0, *,
                  max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
                  headers: Mapping[str, str] | None = None) -> tuple[int, bytes]:
    """이 PC 의 서버를 한 번 부른다 → (HTTP 상태, 응답 바이트). 3xx·4xx·5xx 도 상태로 돌려준다(따라가지 않음).

    주소가 밖이면 소켓을 열기 전에 NotLocalError. localhost 는 127.0.0.1 로 바꿔 부른다(이름 풀이 안 함)."""
    import http.client
    import socket
    import urllib.error
    import urllib.request
    target = pin_loopback(url)                                 # 밖이면 여기서 NotLocalError
    h = {"Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    req = urllib.request.Request(target, data=body, method=method, headers=h)
    if not is_local_url(req.full_url):                         # 두 겹(Request 가 주소를 바꾸지 않았는지)
        raise NotLocalError()
    try:
        with build_opener().open(req, timeout=timeout) as r:
            return int(r.status), read_capped(r, max_bytes)
    except urllib.error.HTTPError as e:                        # 3xx(안 따름)·4xx·5xx
        try:
            data = read_capped(e, max_bytes)
        except Exception:                      # noqa: BLE001 — 오류 본문은 못 읽어도 상태는 쓴다
            data = b""
        return int(e.code), data
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            # 연결·보내기 단계의 시간 초과(urllib 가 URLError 로 감싼다). 루프백에서 이것은 '듣는 서버 없음' 이다 —
            # 윈도는 닫힌 루프백 포트에 SYN 을 몇 번 다시 보내 거부가 1~2초 늦게 오므로 짧은 timeout 이면 여기로 온다.
            raise ConnectionRefusedError("connect timeout") from None
        if isinstance(reason, ConnectionRefusedError):
            raise ConnectionRefusedError("refused") from None
        if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            raise ConnectionResetError("lost") from None
        raise ConnectionError("connect") from None           # 그 밖의 연결 실패
    except (TimeoutError, socket.timeout):
        raise TimeoutError("timeout") from None
    except http.client.RemoteDisconnected:
        raise ConnectionResetError("lost") from None
    except http.client.HTTPException:
        raise BadHTTPError("bad http") from None


__all__ = [
    "LOCAL_HOSTS", "DEFAULT_MAX_RESPONSE_BYTES", "NotLocalError", "BadHTTPError",
    "split_local", "is_local_url", "pin_loopback", "require_local_url",
    "build_opener", "read_capped", "local_request",
]
