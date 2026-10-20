"""치지직 VOD 채팅 전량 수집 · 원문 페이지 보존 · 파싱 · 가명화 (docs/design/03-data-model.md 6-2·13-1·14장).

ClipSlicer app/chat.py 의 재시도·커서 골격만 가져왔다. 중복 제거·개수 상한·(초, 내용, 닉네임) 3값 축약은 쓰지 않는다.

흐름
    collect() → 페이지마다 GET .../videos/{no}/chats?playerMessageTime={cursor}&previousVideoChatSize=50
              → 응답 원문을 pages-<fetch_id>.jsonl.gz 에 한 줄씩
              → videoChats 항목을 parse_message() 로 messages.jsonl 한 줄 + 보호 구역 신원 정보로
              → 끝나면 임시 폴더에서 제자리로 옮긴다 (중간 실패 때 반쯤 쓴 파일이 남지 않게)

커서 규칙 (2026-10-20 실제 API 관찰)
    응답은 playerMessageTime 이 커서보다 '큰' 것만 준다. 그래서 다음 요청은 nextPlayerMessageTime - 1 로 보내
    같은 시각 채팅이 페이지 경계에서 빠지지 않게 하고, 앞 페이지에서 이미 받은 원문(정렬된 JSON 문자열이 완전히 같은 항목)은
    한 번만 받는다. 걸러낸 수는 fetch 기록의 duplicates_skipped 에 남는다.
    같은 페이지 안의 같은 원문은 서버가 준 그대로 둔다 (중복을 지우지 않는다 — 사유 E 판정에 필요).

로그에는 본문·닉네임·userIdHash 를 남기지 않는다. 개수·시각·페이지 번호만.
"""
from __future__ import annotations

import glob
import gzip
import hashlib
import http.client as http_client
import json
import logging
import os
import random
import re
import shutil
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from . import __version__ as APP_VERSION
from . import jsonl
from .core import ids

LOG = logging.getLogger("chatcoach.chzzk")

API_BASE = "https://api.chzzk.naver.com/service/v1/videos/{video_no}/chats"
VIDEO_API = "https://api.chzzk.naver.com/service/v2/videos/{video_no}"
USER_AGENT = "Mozilla/5.0"
PREVIOUS_SIZE = 50
HTTP_TIMEOUT_SEC = 20.0
RETRY_WAITS = (1.0, 2.0, 4.0)          # 실패 뒤 기다리는 시간. 첫 시도 + 재시도 3번
REQUEST_GAP_SEC = (0.1, 0.2)           # 요청 사이 쉬는 시간 범위
DEFAULT_MAX_PAGES = 20000
TAIL_MARGIN_MS = 10 * 60 * 1000        # VOD 길이 + 10분
UNKNOWN_LIMIT_MS = 48 * 3600 * 1000    # 길이를 모를 때 상한 (스키마 t_ms maximum 과 같다)
MAX_NO_NEW_PAGES = 3                   # 새 항목이 0 인 페이지가 연속 이만큼이면 멈춘다
TEXT_MAX = 4000
MASK_MAX_STARS = 7
NICKNAME_MASKS = ("first_char", "hidden")

KIND_BY_CODE = {1: "text", 2: "image", 3: "sticker", 4: "video", 5: "rich",
                10: "donation", 11: "subscription", 30: "system"}
ROLE_BY_CODE = {"streamer": "streamer",
                "streaming_channel_manager": "manager", "streaming_chat_manager": "manager",
                "common_user": "viewer"}

# 멈춘 사유 (fetch 기록 stop_reason)
STOP_NO_NEXT = "next_cursor_not_integer"       # 완료
STOP_NOT_ADVANCING = "next_cursor_not_advancing"  # 완료
STOP_PAST_LIMIT = "next_cursor_past_limit"      # 완료
STOP_PAGE_LIMIT = "page_limit"                  # 미완료
STOP_NO_NEW = "no_new_items_3_pages"            # 미완료
COMPLETE_REASONS = (STOP_NO_NEXT, STOP_NOT_ADVANCING, STOP_PAST_LIMIT)

_VIDEO_URL_RE = re.compile(r"\A(?:https?://)?(?:m\.)?chzzk\.naver\.com/video/([0-9]{1,12})(?:[/?#].*)?\Z", re.I)
_INT_STR_RE = re.compile(r"\A-?[0-9]{1,18}\Z")

HttpFn = Callable[[str, float], "tuple[int, str]"]   # (url, timeout) → (HTTP 상태, 본문 문자열)
ProgressCb = Callable[[float, int, int, int], None]  # (진행 0~1, 페이지 수, 메시지 수, 지금 커서 ms)
CancelCb = Callable[[], bool]


class ChatFetchError(Exception):
    """수집 실패 (네트워크·응답 형식·code != 200·이미 받은 프로젝트)."""


class ChatFetchCancelled(ChatFetchError):
    """취소 콜백이 True 를 돌려줌."""


@dataclass
class FetchSummary:
    fetch_id: str
    video_no: str
    pages: int
    messages: int
    complete: bool
    stop_reason: str
    duplicates_skipped: int
    elapsed_sec: float
    kind_counts: dict[str, int] = field(default_factory=dict)
    viewers: int = 0
    messages_path: str = ""
    fetch_record_path: str = ""


# --------------------------------------------------------------------------- 입력 해석
def parse_video_no(text) -> str | None:
    """'15632692' 또는 'https://chzzk.naver.com/video/15632692' → '15632692'. 아니면 None."""
    if text is None:
        return None
    s = str(text).strip()
    if ids.VIDEO_NO_RE.match(s):
        return s
    m = _VIDEO_URL_RE.match(s)
    return m.group(1) if m else None


def limit_ms_for(duration_ms: int | None) -> int:
    """t_ms·커서 상한: VOD 길이 + 10분, 길이를 모르면 48시간. 스키마 상한(48시간)을 넘지 않는다."""
    if duration_ms is None or duration_ms <= 0:
        return UNKNOWN_LIMIT_MS
    return min(int(duration_ms) + TAIL_MARGIN_MS, UNKNOWN_LIMIT_MS)


# --------------------------------------------------------------------------- 파싱 (파일·네트워크 없음)
def _as_int(v) -> int | None:
    """정수로 읽을 수 있는 값만 int. bool·소수부 있는 실수·숫자 아닌 문자열은 None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    if isinstance(v, str) and _INT_STR_RE.match(v.strip()):
        return int(v.strip())
    return None


def _json_field(v) -> tuple[dict | None, bool]:
    """profile·extras: JSON 문자열(또는 이미 dict). (dict 또는 None, 파싱 실패 여부). 값이 없으면 실패가 아니다."""
    if v is None or v == "":
        return None, False
    if isinstance(v, dict):
        return v, False
    if isinstance(v, str):
        try:
            obj = json.loads(v)
        except ValueError:
            return None, True
        if obj is None:
            return None, False
        return (obj, False) if isinstance(obj, dict) else (None, True)
    return None, True


def mask_nickname(nickname: str | None, mode: str = "first_char") -> str | None:
    """first_char: 첫 글자 + '*' × min(7, 길이-1). hidden: '***'. 닉네임이 없으면 None."""
    if mode not in NICKNAME_MASKS:
        raise ValueError(f"알 수 없는 닉네임 가림 방식: {mode!r}")
    if nickname is None or nickname == "":
        return None
    if mode == "hidden":
        return "***"
    return nickname[0] + "*" * min(MASK_MAX_STARS, len(nickname) - 1)


def kind_of(type_code: int | None) -> str:
    return KIND_BY_CODE.get(type_code, "other") if type_code is not None else "other"


def role_of(user_role_code) -> str:
    return ROLE_BY_CODE.get(user_role_code, "unknown") if isinstance(user_role_code, str) else "unknown"


def raw_key(item) -> str:
    """원문 항목의 비교 키: 키 정렬 JSON 문자열 (원문 9필드가 완전히 같은지 본다)."""
    return json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass
class Parsed:
    record: dict              # messages.jsonl 한 줄 (chat_id 는 아직 base)
    user_id_hash: str | None  # 보호 구역에만 간다
    nickname: str | None      # 보호 구역에만 간다


def parse_message(item: dict, *, video_no: str, salt: bytes, fetch_id: str, page: int, index: int,
                  nickname_mask: str = "first_char", limit_ms: int = UNKNOWN_LIMIT_MS) -> Parsed:
    """videoChats 항목 하나 → schemas/chat-message.schema.json 한 줄 + 보호 구역용 신원 정보.

    chat_id 는 ids.chat_id_base 값이다. 같은 base 가 여럿일 때 _2 를 붙이는 일은 호출하는 쪽이 전체를 모은 뒤 한다.
    """
    if not isinstance(item, dict):
        item = {}
    flags: dict[str, bool] = {}

    profile, perr = _json_field(item.get("profile"))
    if perr:
        flags["profile_parse_error"] = True
    _extras, eerr = _json_field(item.get("extras"))
    if eerr:
        flags["extras_parse_error"] = True

    uid = item.get("userIdHash")
    if (uid is None or uid == "") and profile:
        uid = profile.get("userIdHash")
    anonymous = ids.is_anonymous(uid)
    vid = ids.viewer_id(salt, uid)

    nickname = profile.get("nickname") if profile else None
    if not isinstance(nickname, str) or nickname == "":
        nickname = None

    t = _as_int(item.get("playerMessageTime"))
    if t is None or t < 0 or t > min(limit_ms, UNKNOWN_LIMIT_MS):
        t = None
        flags["invalid_time"] = True
    st = _as_int(item.get("messageTime"))
    if st is not None and st < 0:
        st = None

    raw_code = item.get("messageTypeCode")
    code = _as_int(raw_code)

    content = item.get("content")
    text = "" if content is None else (content if isinstance(content, str) else str(content))
    if len(text) > TEXT_MAX:
        text = text[:TEXT_MAX]
        flags["text_truncated"] = True

    status = item.get("messageStatusType")
    status = "" if status is None else str(status)[:40]

    rec = {
        "chat_id": ids.chat_id_base(video_no, item.get("messageTime"), uid, raw_code, content),
        "t_ms": t,
        "server_time_ms": st,
        "type_code": code if code is not None else -1,
        "kind": kind_of(code),
        "status": status,
        "viewer_id": vid,
        "is_anonymous": anonymous,
        "nickname_masked": mask_nickname(nickname, nickname_mask),
        "role": role_of(profile.get("userRoleCode") if profile else None),
        "text": text,
    }
    if flags:
        rec["flags"] = flags
    rec["raw_ref"] = {"fetch_id": fetch_id, "page": page, "index": index}
    return Parsed(rec, None if anonymous else str(uid), nickname)


def parse_page_body(body: str) -> tuple[list, object]:
    """응답 본문 → (videoChats 목록, nextPlayerMessageTime 원값). 형식이 틀리면 ChatFetchError."""
    try:
        obj = json.loads(body)
    except ValueError as ex:
        raise ChatFetchError(f"응답이 JSON 이 아닙니다: {ex}") from None
    if not isinstance(obj, dict):
        raise ChatFetchError("응답이 object 가 아닙니다")
    if obj.get("code") != 200:
        raise ChatFetchError(f"치지직 오류 code={obj.get('code')!r}")
    content = obj.get("content")
    if not isinstance(content, dict):
        raise ChatFetchError("응답에 content 가 없습니다")
    chats = content.get("videoChats")
    if chats is None:
        chats = []
    if not isinstance(chats, list):
        raise ChatFetchError("videoChats 가 목록이 아닙니다")
    return chats, content.get("nextPlayerMessageTime")


def next_cursor(cursor: int, nxt_raw, limit_ms: int) -> tuple[int | None, str | None, bool]:
    """종료 조건 3중. (다음 커서, 멈춘 사유, 경계 건너뜀 여부).

    - 다음 커서가 정수가 아니면 멈춤.
    - 다음 커서(next - 1)가 지금 커서 이하면: next 자체가 지금 커서보다 크면 next 로 한 칸 건너뛰고(같은 ms 의
      채팅이 한 페이지를 넘게 몰린 경우, 그 ms 의 나머지는 받지 못할 수 있다 → boundary_skips), 아니면 멈춤.
    - next 가 상한(VOD 길이 + 10분, 모르면 48시간)을 넘으면 멈춤.
    """
    nxt = _as_int(nxt_raw) if not isinstance(nxt_raw, str) else None   # 문자열 커서는 받지 않는다
    if nxt is None:
        return None, STOP_NO_NEXT, False
    if nxt > limit_ms:
        return None, STOP_PAST_LIMIT, False
    cand = nxt - 1
    if cand > cursor:
        return cand, None, False
    if nxt > cursor:
        return nxt, None, True
    return None, STOP_NOT_ADVANCING, False


# --------------------------------------------------------------------------- HTTP
def urllib_http(url: str, timeout: float) -> tuple[int, str]:
    """기본 http 함수: 쿠키 없이 GET. HTTP 오류도 (상태, 본문) 으로 돌려준다."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        try:
            body = ex.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return ex.code, body


def _sleep_cancellable(sec: float, cancel: CancelCb | None, sleep: Callable[[float], None]) -> None:
    """sec 초를 0.1초 조각으로 쉬며 조각마다 취소를 본다 (sleep 은 테스트에서 바꿔 끼운다)."""
    left = sec
    while True:
        if cancel and cancel():
            raise ChatFetchCancelled("취소됨")
        if left <= 0:
            return
        step = min(0.1, left)
        sleep(step)
        left -= step


def http_get(url: str, http: HttpFn, cancel: CancelCb | None = None,
             sleep: Callable[[float], None] = time.sleep) -> tuple[int, str, str]:
    """GET 한 번(+재시도). (HTTP 상태, 본문, 요청 시각 ISO). 재시도: 네트워크 오류·429·5xx·JSON 아님, 1·2·4초 뒤.

    code != 200 은 재시도하지 않고 오류다 (parse_page_body 가 본다). 다른 4xx 도 바로 오류.
    """
    last = ""
    for attempt in range(len(RETRY_WAITS) + 1):
        if cancel and cancel():
            raise ChatFetchCancelled("취소됨")
        requested_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        try:
            status, body = http(url, HTTP_TIMEOUT_SEC)
        # TimeoutError 는 OSError. 본문을 읽다 끊기면 http.client.IncompleteRead(HTTPException 계열, OSError 아님)
        except (urllib.error.URLError, OSError, ValueError, http_client.HTTPException) as ex:
            last = f"{type(ex).__name__}"
        else:
            if status == 200:
                try:
                    json.loads(body)
                    return status, body, requested_at
                except ValueError:
                    last = "JSON 아님"
            elif status == 429 or status >= 500:
                last = f"HTTP {status}"
            else:
                raise ChatFetchError(f"HTTP {status}")
        LOG.warning("채팅 요청 실패 (%d/%d): %s", attempt + 1, len(RETRY_WAITS) + 1, last)
        if attempt < len(RETRY_WAITS):
            _sleep_cancellable(RETRY_WAITS[attempt], cancel, sleep)
    raise ChatFetchError(f"채팅 서버 응답 없음: {last}")


def fetch_video_duration_ms(video_no: str, http: HttpFn | None = None,
                            sleep: Callable[[float], None] = time.sleep) -> int | None:
    """VOD 길이(ms). 영상 정보 API 의 content.duration(초). 모르면 None (상한은 48시간이 된다)."""
    no = parse_video_no(video_no)
    if not no:
        raise ValueError(f"치지직 영상 번호가 아닙니다: {video_no!r}")
    try:
        _st, body, _ = http_get(VIDEO_API.format(video_no=no), http or urllib_http, sleep=sleep)
        obj = json.loads(body)
        dur = _as_int((obj.get("content") or {}).get("duration")) if obj.get("code") == 200 else None
    except (ChatFetchError, ValueError, AttributeError):
        return None
    return dur * 1000 if dur and dur > 0 else None


# --------------------------------------------------------------------------- 수집 + 저장
def chat_paths(project_dir: str, fetch_id: str) -> dict[str, str]:
    """프로젝트 안 채팅 파일 경로 (03 2-1). fetch_id 는 정규식 검사 뒤에만 경로로 쓴다."""
    ids.check_path_id(fetch_id, ids.FETCH_ID_RE, "fetch_id")
    chat = os.path.join(project_dir, "chat")
    prot = os.path.join(chat, "protected")
    return {
        "chat": chat, "protected": prot,
        "messages": os.path.join(chat, "messages.jsonl"),
        "fetch": os.path.join(chat, f"fetch-{fetch_id}.json"),
        "pages": os.path.join(prot, f"pages-{fetch_id}.jsonl.gz"),
        "identities": os.path.join(prot, "identities.jsonl"),
    }


def _remove_stale_tmp(p: dict[str, str]) -> int:
    """앞선 수집이 강제 종료로 남긴 임시 폴더(chat/.tmp-* 옛 위치, chat/protected/.tmp-*)를 지운다. 지운 수."""
    n = 0
    for d in glob.glob(os.path.join(glob.escape(p["chat"]), ".tmp-*")) +             glob.glob(os.path.join(glob.escape(p["protected"]), ".tmp-*")):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    if n:
        LOG.warning("앞선 수집이 남긴 임시 폴더 %d개를 지움", n)
    return n


def _default_rand4() -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    r = random.SystemRandom()
    return "".join(r.choice(alphabet) for _ in range(4))


def _write_json_atomic(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def collect(video_no, project_dir: str, salt: bytes, nickname_mask: str = "first_char",
            duration_ms: int | None = None, http: HttpFn | None = None,
            on_progress: ProgressCb | None = None, cancel: CancelCb | None = None,
            now: Callable[[], datetime] | None = None, rand4: Callable[[], str] | None = None,
            *, max_pages: int = DEFAULT_MAX_PAGES, sleep: Callable[[float], None] | None = None,
            request_gap: tuple[float, float] = REQUEST_GAP_SEC) -> FetchSummary:
    """VOD 채팅 전체를 받아 <project>/chat/ 에 저장한다. 이미 messages.jsonl(또는 identities.jsonl)이 있으면 거부.

    만드는 파일: chat/messages.jsonl, chat/fetch-<fetch_id>.json,
                chat/protected/pages-<fetch_id>.jsonl.gz, chat/protected/identities.jsonl.
    모두 chat/protected/.tmp-<fetch_id>/ (보호 구역 안) 에 먼저 쓰고 끝에서 옮긴다. 실패·취소 때는 임시 폴더를 지우고 예외를 올린다.
    """
    no = parse_video_no(video_no)
    if not no:
        raise ValueError(f"치지직 영상 번호가 아닙니다: {video_no!r}")
    if nickname_mask not in NICKNAME_MASKS:
        raise ValueError(f"알 수 없는 닉네임 가림 방식: {nickname_mask!r}")
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 16:
        raise ValueError("솔트가 너무 짧습니다")
    if max_pages < 1:
        raise ValueError("max_pages 는 1 이상")
    salt = bytes(salt)
    http = http or urllib_http
    sleep = sleep or time.sleep
    now = now or (lambda: datetime.now().astimezone())
    rand4 = rand4 or _default_rand4

    started = now()
    fid = ids.fetch_id(started.strftime("%Y%m%d"), rand4())
    p = chat_paths(project_dir, fid)
    for k in ("messages", "identities"):
        if os.path.exists(p[k]):
            raise ChatFetchError(f"이미 받은 채팅이 있습니다 ({k}). 다시 받으려면 먼저 옮기거나 지우세요")
    os.makedirs(p["protected"], exist_ok=True)
    _remove_stale_tmp(p)
    # 임시 폴더는 보호 구역 안에 둔다: 강제 종료로 except 가 못 돌아도 원문 페이지·신원 정보가 보호 구역 밖에 남지 않게
    tmp = os.path.join(p["protected"], f".tmp-{fid}")
    os.makedirs(os.path.join(tmp, "protected"))
    t_tmp = {k: os.path.join(tmp, os.path.relpath(p[k], p["chat"])) for k in ("messages", "fetch", "pages", "identities")}

    limit = limit_ms_for(duration_ms)
    endpoint = API_BASE.format(video_no=no)
    t0 = time.monotonic()
    LOG.info("채팅 수집 시작: video=%s fetch=%s 상한=%dms", no, fid, limit)
    moved: list[str] = []
    try:
        records: list[dict] = []
        idents: dict[str, dict] = {}          # viewer_id → {viewer_id, user_id_hash, nicknames}
        prev_keys: set[str] = set()           # 앞 페이지까지 받은 원문 키
        prev_bases: set[str] = set()
        dup_skipped = near_dup = boundary_skips = 0
        cursor = 0
        page = 0
        no_new_run = 0
        stop_reason = ""
        with gzip.open(t_tmp["pages"], "wb") as gz:
            while True:
                if cancel and cancel():
                    raise ChatFetchCancelled("취소됨")
                url = f"{endpoint}?playerMessageTime={cursor}&previousVideoChatSize={PREVIOUS_SIZE}"
                status, body, requested_at = http_get(url, http, cancel, sleep)
                gz.write(jsonl.dumps_line({"page": page, "request_url": url, "requested_at": requested_at,
                                           "status": status, "body": body}))
                chats, nxt_raw = parse_page_body(body)
                new_keys: set[str] = set()
                new_bases: set[str] = set()
                new_count = 0
                for i, item in enumerate(chats):
                    k = raw_key(item)
                    if k in prev_keys:
                        dup_skipped += 1
                        continue
                    pr = parse_message(item, video_no=no, salt=salt, fetch_id=fid, page=page, index=i,
                                       nickname_mask=nickname_mask, limit_ms=limit)
                    if pr.record["chat_id"] in prev_bases:
                        near_dup += 1          # 원문은 다르지만 chat_id 재료 5값이 앞 페이지와 같다 (확인용)
                    new_keys.add(k)
                    new_bases.add(pr.record["chat_id"])
                    records.append(pr.record)
                    new_count += 1
                    vid = pr.record["viewer_id"]
                    if pr.user_id_hash is not None:
                        ent = idents.get(vid)
                        if ent is None:
                            ent = idents[vid] = {"viewer_id": vid, "user_id_hash": pr.user_id_hash, "nicknames": []}
                        if pr.nickname is not None and all(n["nickname"] != pr.nickname for n in ent["nicknames"]):
                            ent["nicknames"].append({"nickname": pr.nickname, "first_seen_ms": pr.record["t_ms"]})
                prev_keys |= new_keys
                prev_bases |= new_bases
                page += 1
                no_new_run = 0 if new_count else no_new_run + 1

                cur, reason, skipped = next_cursor(cursor, nxt_raw, limit)
                boundary_skips += int(skipped)
                if on_progress:
                    on_progress(min(0.99, (cur if cur is not None else limit) / limit), page, len(records),
                                cur if cur is not None else cursor)
                if page % 100 == 0:
                    LOG.info("채팅 수집 중: %d페이지 %d건 커서=%dms", page, len(records), cursor)
                if reason:
                    stop_reason = reason
                    break
                if no_new_run >= MAX_NO_NEW_PAGES:
                    stop_reason = STOP_NO_NEW
                    break
                if page >= max_pages:
                    stop_reason = STOP_PAGE_LIMIT
                    break
                cursor = cur
                _sleep_cancellable(random.uniform(*request_gap) if request_gap[1] > 0 else 0.0, cancel, sleep)

        chat_ids = ids.assign_chat_ids(r["chat_id"] for r in records)
        for r, cid in zip(records, chat_ids):
            r["chat_id"] = cid
        jsonl.write_all(t_tmp["messages"], records)
        with open(t_tmp["messages"], "rb") as f:
            msha = hashlib.sha256(f.read()).hexdigest()
        jsonl.write_all(t_tmp["identities"], idents.values())

        complete = stop_reason in COMPLETE_REASONS
        kinds = dict(Counter(r["kind"] for r in records))
        finished = now()
        fetch_rec = {
            "fetch_id": fid,
            "video_no": no,
            "endpoint": endpoint,
            "params": {"previousVideoChatSize": PREVIOUS_SIZE, "cursor_rule": "nextPlayerMessageTime-1",
                       "first_cursor": 0},
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "pages": page,
            "messages": len(records),
            "viewers": len(idents),
            "complete": complete,
            "stop_reason": stop_reason,
            "duration_ms": duration_ms,
            "limit_ms": limit,
            "max_pages": max_pages,
            "duplicates_skipped": dup_skipped,
            "same_chat_id_not_identical": near_dup,
            "boundary_skips": boundary_skips,
            "kind_counts": kinds,
            "nickname_mask": nickname_mask,
            "salt_id": ids.salt_id(salt),
            "user_agent": USER_AGENT,
            "app_version": APP_VERSION,
            "messages_sha256": msha,
        }
        _write_json_atomic(t_tmp["fetch"], fetch_rec)

        # 옮기기: messages.jsonl 을 맨 끝에 (그 파일이 있다 = 수집 완료)
        for k in ("pages", "identities", "fetch", "messages"):
            os.replace(t_tmp[k], p[k])
            moved.append(p[k])
        shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        for m in moved:   # 옮기다 실패하면 옮긴 것도 되돌린다
            try:
                os.remove(m)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        LOG.warning("채팅 수집 실패: video=%s fetch=%s (임시 파일 지움)", no, fid)
        raise

    elapsed = time.monotonic() - t0
    LOG.info("채팅 수집 끝: %d페이지 %d건 완료=%s 사유=%s 걸러낸 중복=%d %.1f초",
             page, len(records), complete, stop_reason, dup_skipped, elapsed)
    return FetchSummary(fetch_id=fid, video_no=no, pages=page, messages=len(records), complete=complete,
                        stop_reason=stop_reason, duplicates_skipped=dup_skipped, elapsed_sec=elapsed,
                        kind_counts=kinds, viewers=len(idents), messages_path=p["messages"],
                        fetch_record_path=p["fetch"])
