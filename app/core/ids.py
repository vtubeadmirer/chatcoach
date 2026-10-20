"""ID 규칙 (docs/design/03-data-model.md 13-1·14-2).

- chat_id  : sl-chat-id-v1. 수집 순서·페이지 경계와 상관없이 같은 값이 나오고, 다른 언어로도 계산할 수 있다.
- viewer_id: hmac-sha256-v1. 프로젝트 솔트로 userIdHash 를 가린다. 방송 사이에서는 같은 시청자를 잇지 못한다.
- fetch_id : 수집 한 번의 이름. 파일 이름에 쓰이므로 정규식을 통과한 값만 경로로 쓴다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Iterable

CHAT_ID_RE = re.compile(r"\Ac[0-9a-f]{20}(_[0-9]+)?\Z")
VIEWER_ID_RE = re.compile(r"\A(vw_[a-z2-7]{12}|anon)\Z")
FETCH_ID_RE = re.compile(r"\AF[0-9]{8}-[a-z0-9]{4}\Z")
VIDEO_NO_RE = re.compile(r"\A[0-9]{1,12}\Z")
ANON = "anon"


def _s(v) -> str:
    """키에 넣을 값: None 은 빈 문자열, 나머지는 API 가 준 그대로의 문자열."""
    return "" if v is None else str(v)


def chat_id_base(video_no, message_time, user_id_hash, type_code, content) -> str:
    """'c' + SHA-256("{video_no}|{messageTime}|{userIdHash}|{messageTypeCode}|{content}") 앞 20자.

    content 는 유니코드 정규화 없이 그대로 쓴다. extras 는 넣지 않는다(요청마다 바뀔 수 있는 토큰이 있다).
    """
    key = f"{_s(video_no)}|{_s(message_time)}|{_s(user_id_hash)}|{_s(type_code)}|{_s(content)}"
    return "c" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def assign_chat_ids(bases: Iterable[str]) -> list[str]:
    """같은 base 가 여럿이면 두 번째부터 _2, _3 … 을 붙인다. 구별할 수 없는 메시지라 어느 쪽이 _2 인지는 의미가 없다."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for b in bases:
        n = seen.get(b, 0) + 1
        seen[b] = n
        out.append(b if n == 1 else f"{b}_{n}")
    return out


def is_anonymous(user_id_hash) -> bool:
    return user_id_hash is None or str(user_id_hash).strip() in ("", "anonymous")


def viewer_id(salt: bytes, user_id_hash) -> str:
    """'vw_' + base32(HMAC-SHA256(salt, userIdHash)) 소문자 앞 12자. 익명이면 'anon'."""
    if is_anonymous(user_id_hash):
        return ANON
    if len(salt) < 16:
        raise ValueError("솔트가 너무 짧습니다")
    mac = hmac.new(salt, str(user_id_hash).encode("utf-8"), hashlib.sha256).digest()
    return "vw_" + base64.b32encode(mac).decode("ascii").lower()[:12]


def salt_id(salt: bytes) -> str:
    """project.json 에 적는 솔트 식별자 (솔트 자체가 아니다)."""
    return hashlib.sha256(salt).hexdigest()[:12]


def fetch_id(yyyymmdd: str, rand4: str) -> str:
    """'F<yyyymmdd>-<4자>'. 날짜와 무작위 4자는 호출하는 쪽이 준다 (core 는 시계·난수를 쓰지 않는다)."""
    fid = f"F{yyyymmdd}-{rand4}"
    if not FETCH_ID_RE.match(fid):
        raise ValueError(f"fetch_id 형식이 아닙니다: {fid!r}")
    return fid


def check_path_id(value: str, pattern: re.Pattern, what: str) -> str:
    """파일 이름이 되는 ID 는 정규식을 통과한 뒤에만 경로로 쓴다 (S-46)."""
    if not isinstance(value, str) or not pattern.match(value):
        raise ValueError(f"{what} 형식이 아닙니다: {value!r}")
    return value
