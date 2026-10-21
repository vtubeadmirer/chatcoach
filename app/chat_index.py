"""채팅 색인 캐시 (14 6-1 의 2번, 03 2-2·7장 '채팅 병렬 배열').

messages.jsonl 을 한 번 읽어 chat_id → (t_ms, kind, type_code) 와 시각 순 배열을 만들고
`<앱>\\data\\cache\\<project_id>\\chat_index.bin` 에 둔다(03 2-2: 다시 만들 수 있는 파생은 앱 data\\cache, 프로젝트 폴더 밖).

담는 것: chat_id · t_ms · kind · type_code 네 칸과 원본 지문(크기·mtime_ns·앞 64KiB sha256)·messages_sha256 뿐이다.
본문·가림 닉네임·viewer_id·role·raw_ref 는 넣지 않는다. 라벨 내용도 없다 — 블라인드(06 7장, 03 12-1)를 우회하지 않는다.

무효화: 원본 크기·mtime_ns·앞부분 sha256 가 하나라도 다르면 버리고 다시 만든다. 캐시 본문 sha256 이 머리와 다르면(깨짐) 다시 만든다.
verify_full=True 면 원본 전체 sha256 까지 맞춘다(03 7장 'messages_sha256 가 같을 때만 쓴다' 를 엄격하게).
형식은 pickle 이 아닌 단순 바이너리다(03 7장 'pickle 금지'):

    MAGIC(8) · 머리 길이 uint32 LE · 머리 JSON(utf-8) · 본문
    본문 = t_ms int64[n] · type_code int32[n] · kind uint8[n] · by_time uint32[m] · sorted_t int64[m]
           · id_off uint32[n+1] · id_bytes
    (n = 줄 수, m = 시각이 있는 줄 수. 정수는 little-endian)

입출력 층이다(app/core 아님). Qt 를 쓰지 않는다 — 워커 스레드·도구에서 불러도 된다.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import struct
import sys
from array import array
from dataclasses import dataclass, field
from typing import Iterable, Mapping

MAGIC = b"CCCHIX01"
SCHEMA = "chatcoach/chat-index"
VERSION = 1
HEAD_BYTES = 64 * 1024
FILE_NAME = "chat_index.bin"
MAX_KINDS = 250                  # kind 칸 uint8. 넘치는 종류는 'other' 로 접는다(지금 종류는 열 개도 안 된다)
MESSAGES_MAX_LINE = 1 << 20      # jsonl.read 기본과 같음
NO_T = -(1 << 63)                # t_ms 없음
NO_CODE = -(1 << 31)             # type_code 없음
_LE = sys.byteorder == "little"


class ChatIndexError(Exception):
    """캐시 파일을 읽을 수 없음(형식·깨짐). load_or_build 는 이것을 받으면 다시 만든다."""


@dataclass(frozen=True)
class SourceStamp:
    """원본 지문. 셋이 모두 같아야 캐시를 쓴다."""
    size: int
    mtime_ns: int
    head_sha256: str

    def as_dict(self) -> dict:
        return {"size": self.size, "mtime_ns": self.mtime_ns, "head_sha256": self.head_sha256,
                "head_bytes": HEAD_BYTES}


def source_stamp(messages_path: str) -> SourceStamp:
    """크기·mtime_ns·앞 64KiB sha256. 파일이 없으면 FileNotFoundError."""
    st = os.stat(messages_path)
    with open(messages_path, "rb") as f:
        head = f.read(HEAD_BYTES)
    return SourceStamp(int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(head).hexdigest())


@dataclass
class ChatIndex:
    """병렬 배열 색인. 파일 순서(i)로 chat_ids·t_ms·type_codes·kind_idx 가 맞물린다."""
    chat_ids: list[str]
    t_ms: array                 # 'q', 없음 = NO_T
    type_codes: array           # 'i' 또는 'l'(4바이트), 없음 = NO_CODE
    kind_idx: array             # 'B'
    kinds: list[str]
    by_time: array              # 'I' 4바이트: 시각이 있는 줄의 i, (t_ms, i) 순
    sorted_t: array             # 'q': by_time 순서의 t_ms (bisect 용)
    stamp: SourceStamp | None = None
    messages_sha256: str | None = None
    corrupt_lines: int = 0
    truncated_tail: bool = False
    duplicates: int = 0         # 같은 chat_id 가 또 나와 뺀 줄 수(첫 줄만 남김)
    skipped: int = 0            # chat_id 가 문자열이 아닌 줄 수
    _pos: dict[str, int] | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ 조회
    def __len__(self) -> int:
        return len(self.chat_ids)

    def _positions(self) -> dict[str, int]:
        if self._pos is None:
            self._pos = {c: i for i, c in enumerate(self.chat_ids)}
        return self._pos

    def __contains__(self, chat_id) -> bool:
        return chat_id in self._positions()

    def position(self, chat_id: str) -> int | None:
        """파일 순서 번호(0부터). 없으면 None."""
        return self._positions().get(chat_id)

    def row(self, i: int) -> tuple[int | None, str, int | None]:
        t = self.t_ms[i]
        c = self.type_codes[i]
        return (None if t == NO_T else int(t)), self.kinds[self.kind_idx[i]], (None if c == NO_CODE else int(c))

    def get(self, chat_id: str) -> tuple[int | None, str, int | None] | None:
        """chat_id → (t_ms 또는 None, kind, type_code 또는 None). 없으면 None."""
        i = self._positions().get(chat_id)
        return None if i is None else self.row(i)

    def t_of(self, chat_id: str) -> int | None:
        i = self._positions().get(chat_id)
        if i is None:
            return None
        t = self.t_ms[i]
        return None if t == NO_T else int(t)

    def kind_of(self, chat_id: str) -> str | None:
        i = self._positions().get(chat_id)
        return None if i is None else self.kinds[self.kind_idx[i]]

    def chat_times(self, chat_ids: Iterable[str] | None = None) -> dict[str, int]:
        """보고서·조정·④ 대상이 넘기는 chat_times(chat_id → t_ms). chat_ids 를 주면 그것만. 시각 없는 채팅은 뺀다."""
        if chat_ids is None:
            return {self.chat_ids[i]: int(self.t_ms[i]) for i in self.by_time}
        pos = self._positions()
        out: dict[str, int] = {}
        for c in chat_ids:
            i = pos.get(c)
            if i is not None and self.t_ms[i] != NO_T:
                out[c] = int(self.t_ms[i])
        return out

    def time_ordered_ids(self) -> list[str]:
        """시각 순 chat_id (같은 시각은 파일 순). 시각 없는 채팅은 없다."""
        ids = self.chat_ids
        return [ids[i] for i in self.by_time]

    def range_positions(self, start_ms: int, end_ms: int) -> list[int]:
        """[start_ms, end_ms) 안 채팅의 파일 순서 번호, 시각 순."""
        lo = bisect.bisect_left(self.sorted_t, int(start_ms))
        hi = bisect.bisect_left(self.sorted_t, int(end_ms))
        return list(self.by_time[lo:hi])

    def range_ids(self, start_ms: int, end_ms: int) -> list[str]:
        ids = self.chat_ids
        return [ids[i] for i in self.range_positions(start_ms, end_ms)]

    def count_in(self, start_ms: int, end_ms: int) -> int:
        return bisect.bisect_left(self.sorted_t, int(end_ms)) - bisect.bisect_left(self.sorted_t, int(start_ms))


# ---------------------------------------------------------------------------- 만들기
_T_MAX, _C_MAX = 1 << 63, 1 << 31


def build_from_records(records: Iterable[Mapping]) -> ChatIndex:
    """messages.jsonl 줄(dict) → 색인. 파일·시계 없음. 같은 chat_id 는 첫 줄만 남긴다."""
    ids: list[str] = []
    ts: list[int] = []
    codes: list[int] = []
    kl: list[int] = []
    kinds: list[str] = []
    kmap: dict[str, int] = {}
    pos: dict[str, int] = {}
    dup = skipped = 0
    for r in records:
        cid = r.get("chat_id")
        if type(cid) is not str or not cid:
            skipped += 1
            continue
        if cid in pos:
            dup += 1
            continue
        pos[cid] = len(ids)
        k = r.get("kind")
        j = kmap.get(k) if type(k) is str else None
        if j is None:
            k = k if type(k) is str and k else "other"
            if len(kinds) >= MAX_KINDS:
                k = "other"
            j = kmap.get(k)
            if j is None:
                j = len(kinds)
                kinds.append(k)
                kmap[k] = j
        t = r.get("t_ms")
        c = r.get("type_code")
        ids.append(cid)
        ts.append(t if type(t) is int and NO_T < t < _T_MAX else NO_T)
        codes.append(c if type(c) is int and NO_CODE < c < _C_MAX else NO_CODE)
        kl.append(j)
    if not kinds:
        kinds.append("other")
    order = sorted(range(len(ids)), key=ts.__getitem__)     # 안정 정렬: 같은 시각은 파일 순
    k0 = 0
    while k0 < len(order) and ts[order[k0]] == NO_T:          # NO_T 가 가장 작아 앞에 모인다
        k0 += 1
    order = order[k0:]
    idx = ChatIndex(ids, array("q", ts), array(_I32, codes), array("B", kl), kinds, array(_U32, order),
                    array("q", [ts[i] for i in order]), duplicates=dup, skipped=skipped)
    idx._pos = pos
    return idx


_SPLIT_RE = re.compile(rb"\]\s*,\s*\[")


def _bulk_parse(lines: list[bytes]) -> list[dict] | None:
    """줄들을 한 번에 파싱(빠른 길). 줄마다 [ ] 로 감싸 이어 붙여 json.loads 한 번 — 줄마다 정확히 object 하나인지 본다.
    줄 사이가 섞여 맞아떨어질 수 있는 모양(']' ',' '[' 가 이어진 줄)·디코딩 오류·개수 불일치면 None(줄마다 파싱으로)."""
    for raw in lines:
        if len(raw) > MESSAGES_MAX_LINE or _SPLIT_RE.search(raw):
            return None
    try:
        text = b"[[" + b"],[".join(lines) + b"]]"
        out = json.loads(text.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(out, list) or len(out) != len(lines):
        return None
    recs: list[dict] = []
    for one in out:
        if not isinstance(one, list) or len(one) != 1 or not isinstance(one[0], dict):
            return None
        recs.append(one[0])
    return recs


def _iter_lines(data: bytes):
    """jsonl.read 와 같은 규칙으로 줄을 읽는다(BOM·빈 줄·깨진 줄·잘린 끝). (records, corrupt 수, 잘린 끝)."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    lines = data.split(b"\n")
    ends_nl = data.endswith(b"\n")
    if ends_nl:
        lines = lines[:-1]
    nonblank = [raw.rstrip(b"\r") for raw in lines]
    nonblank = [raw for raw in nonblank if raw.strip()]
    fast = _bulk_parse(nonblank)
    if fast is not None:
        return fast, 0, False
    last = len(lines)
    recs: list[dict] = []
    corrupt = 0
    truncated = False
    loads = json.loads
    for i, raw in enumerate(lines, start=1):
        raw = raw.rstrip(b"\r")
        if not raw.strip():
            continue
        try:
            if len(raw) > MESSAGES_MAX_LINE:
                raise ValueError("줄이 너무 깁니다")
            obj = loads(raw)
            if not isinstance(obj, dict):
                raise ValueError("object 가 아닙니다")
            recs.append(obj)
        except (ValueError, UnicodeDecodeError, RecursionError):
            if i == last and not ends_nl:
                truncated = True
            else:
                corrupt += 1
    return recs, corrupt, truncated


def build_index(messages_path: str) -> ChatIndex:
    """messages.jsonl 을 처음부터 읽어 색인을 만든다(캐시를 보지 않음). 원본 지문·전체 sha256 을 같이 적는다."""
    st = os.stat(messages_path)
    with open(messages_path, "rb") as f:
        data = f.read()
    stamp: SourceStamp | None = SourceStamp(len(data), int(st.st_mtime_ns),
                                            hashlib.sha256(data[:HEAD_BYTES]).hexdigest())
    if int(st.st_size) != len(data):
        # 읽는 사이 파일이 바뀌었다(수집이 쓰는 중). 읽은 뒤의 파일 지문을 붙이면 옛 내용 색인이 빠른 지문 검사를
        # 통과하므로, 지문을 비운다 — is_fresh 가 늘 False 라 다음에 다시 만들고, load_or_build 는 저장하지 않는다.
        stamp = None
    sha = hashlib.sha256(data).hexdigest()
    recs, corrupt, truncated = _iter_lines(data)
    del data
    idx = build_from_records(recs)
    idx.stamp = stamp
    idx.messages_sha256 = sha
    idx.corrupt_lines = corrupt
    idx.truncated_tail = truncated
    return idx


# ---------------------------------------------------------------------------- 파일
def _le_bytes(a: array) -> bytes:
    if _LE:
        return a.tobytes()
    b = array(a.typecode, a)
    b.byteswap()
    return b.tobytes()


def _from_le(typecode: str, raw: bytes) -> array:
    a = array(typecode)
    a.frombytes(raw)
    if not _LE:
        a.byteswap()
    return a


def _tc(size: int, signed: bool) -> str:
    """바이트 수가 size 인 array typecode (플랫폼마다 'i'·'l' 크기가 다르다)."""
    for tc in (("b", "h", "i", "l", "q") if signed else ("B", "H", "I", "L", "Q")):
        if array(tc).itemsize == size:
            return tc
    raise RuntimeError(f"{size}바이트 정수 배열을 만들 수 없습니다")


_I32, _U32 = _tc(4, True), _tc(4, False)


def to_bytes(idx: ChatIndex) -> bytes:
    """색인 → 캐시 파일 바이트. 머리에 본문 sha256 를 적어 깨짐을 알아챈다."""
    ids_b = [c.encode("utf-8") for c in idx.chat_ids]
    off = array(_U32, [0])
    acc = 0
    for b in ids_b:
        acc += len(b)
        off.append(acc)
    parts = [_le_bytes(array("q", idx.t_ms)), _le_bytes(array(_I32, idx.type_codes)),
             bytes(idx.kind_idx), _le_bytes(array(_U32, idx.by_time)), _le_bytes(array("q", idx.sorted_t)),
             _le_bytes(off), b"".join(ids_b)]
    body = b"".join(parts)
    head = {
        "schema": SCHEMA, "version": VERSION,
        "source": idx.stamp.as_dict() if idx.stamp else None,
        "messages_sha256": idx.messages_sha256,
        "count": len(idx.chat_ids), "timed": len(idx.by_time), "kinds": list(idx.kinds),
        "id_bytes": acc, "corrupt_lines": idx.corrupt_lines, "truncated_tail": idx.truncated_tail,
        "duplicates": idx.duplicates, "skipped": idx.skipped,
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }
    hb = json.dumps(head, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return MAGIC + struct.pack("<I", len(hb)) + hb + body


def from_bytes(data: bytes) -> ChatIndex:
    """캐시 파일 바이트 → 색인. 형식이 틀리거나 깨졌으면 ChatIndexError."""
    if len(data) < 12 or data[:8] != MAGIC:
        raise ChatIndexError("채팅 색인 파일 머리가 다릅니다")
    (hl,) = struct.unpack_from("<I", data, 8)
    if 12 + hl > len(data):
        raise ChatIndexError("채팅 색인 파일 머리가 잘렸습니다")
    try:
        head = json.loads(data[12:12 + hl].decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        raise ChatIndexError("채팅 색인 파일 머리를 읽지 못했습니다") from None
    if not isinstance(head, dict) or head.get("schema") != SCHEMA or head.get("version") != VERSION:
        raise ChatIndexError("채팅 색인 판이 다릅니다")
    body = memoryview(data)[12 + hl:]
    if hashlib.sha256(body).hexdigest() != head.get("body_sha256"):
        raise ChatIndexError("채팅 색인 본문이 깨졌습니다")
    try:
        n, m, nid = int(head["count"]), int(head["timed"]), int(head["id_bytes"])
        kinds = [str(k) for k in head["kinds"]]
        sizes = (8 * n, 4 * n, n, 4 * m, 8 * m, 4 * (n + 1), nid)
        if sum(sizes) != len(body) or not kinds:
            raise ChatIndexError("채팅 색인 본문 길이가 머리와 다릅니다")
        pos = 0
        segs = []
        for s in sizes:
            segs.append(bytes(body[pos:pos + s]))
            pos += s
        ts = _from_le("q", segs[0])
        codes = _from_le(_I32, segs[1])
        kidx = array("B", segs[2])
        by_time = _from_le(_U32, segs[3])
        sorted_t = _from_le("q", segs[4])
        off = _from_le(_U32, segs[5])
        blob = segs[6].decode("utf-8")
        if blob.isascii():
            ids = [blob[off[i]:off[i + 1]] for i in range(n)]
        else:                               # 오프셋은 바이트 단위 — 아스키가 아닐 때만 바이트로 자른다
            raw = segs[6]
            ids = [raw[off[i]:off[i + 1]].decode("utf-8") for i in range(n)]
        if kidx and max(kidx) >= len(kinds):
            raise ChatIndexError("채팅 색인 종류 번호가 범위 밖입니다")
        src = head.get("source")
        stamp = (SourceStamp(int(src["size"]), int(src["mtime_ns"]), str(src["head_sha256"]))
                 if isinstance(src, dict) and int(src.get("head_bytes", -1)) == HEAD_BYTES else None)
    except ChatIndexError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, IndexError):
        raise ChatIndexError("채팅 색인 머리 칸이 틀렸습니다") from None
    return ChatIndex(ids, ts, codes, kidx, kinds, by_time, sorted_t, stamp=stamp,
                     messages_sha256=head.get("messages_sha256"), corrupt_lines=int(head.get("corrupt_lines") or 0),
                     truncated_tail=bool(head.get("truncated_tail")), duplicates=int(head.get("duplicates") or 0),
                     skipped=int(head.get("skipped") or 0))


def save_index(idx: ChatIndex, cache_path: str) -> None:
    """임시 파일에 쓰고 os.replace 로 바꿔 넣는다(반쯤 쓴 캐시가 남지 않게)."""
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    tmp = f"{cache_path}.tmp{os.getpid()}"
    try:
        with open(tmp, "wb") as f:
            f.write(to_bytes(idx))
        os.replace(tmp, cache_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def read_index(cache_path: str) -> ChatIndex:
    """캐시 파일을 읽는다(원본과 맞는지는 보지 않음). 없으면 FileNotFoundError, 깨졌으면 ChatIndexError."""
    with open(cache_path, "rb") as f:
        return from_bytes(f.read())


def _full_sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_fresh(idx: ChatIndex, messages_path: str, *, verify_full: bool = False) -> bool:
    """캐시가 지금 원본과 맞는가: 크기·mtime_ns·앞부분 sha256 (verify_full 이면 전체 sha256 도)."""
    if idx.stamp is None:
        return False
    try:
        now = source_stamp(messages_path)
    except OSError:
        return False
    if now != idx.stamp:
        return False
    if verify_full:
        try:
            return _full_sha(messages_path) == idx.messages_sha256
        except OSError:
            return False
    return True


# ---------------------------------------------------------------------------- 위치
def cache_dir_for(project_id: str, data_root: str | None = None) -> str:
    """<앱>\\data\\cache\\<project_id>\\ (03 2-2). project_id 는 경로 조각이라 글자를 거른다."""
    pid = str(project_id or "")
    if not pid or any(ch in pid for ch in '\\/:*?"<>|') or pid in (".", "..") or len(pid) > 80:
        raise ValueError("project_id 가 경로에 쓸 수 없는 값입니다")
    if data_root is None:
        from . import paths
        data_root = paths.data_root()
    return os.path.join(data_root, "cache", pid)


def cache_path_for(project_id: str, data_root: str | None = None) -> str:
    return os.path.join(cache_dir_for(project_id, data_root), FILE_NAME)


# ---------------------------------------------------------------------------- 한 번에
HIT, BUILT, REBUILT = "hit", "built", "rebuilt"


def load_or_build(messages_path: str, cache_path: str, *, verify_full: bool = False,
                  save: bool = True) -> tuple[ChatIndex, str]:
    """캐시가 맞으면 읽고(HIT), 없으면 만들고(BUILT), 맞지 않거나 깨졌으면 다시 만든다(REBUILT).

    캐시 쓰기가 실패해도(읽기 전용 폴더 등) 만든 색인은 돌려준다 — 캐시는 버려도 되는 파생이다.
    원본이 없으면 FileNotFoundError.
    """
    status = BUILT
    try:
        idx = read_index(cache_path)
    except FileNotFoundError:
        idx = None
    except (OSError, ChatIndexError):
        idx, status = None, REBUILT
    if idx is not None:
        if is_fresh(idx, messages_path, verify_full=verify_full):
            return idx, HIT
        status = REBUILT
    idx = build_index(messages_path)
    if save and idx.stamp is not None:      # 지문이 없는 색인(읽는 사이 바뀜)은 캐시로 남기지 않는다
        try:
            save_index(idx, cache_path)
        except OSError:
            pass
    return idx, status


def for_project(project, *, verify_full: bool = False, data_root: str | None = None) -> tuple[ChatIndex, str]:
    """app.project.Project → (색인, 상태). project_id 가 없으면 캐시 없이 만든다(BUILT, 저장 안 함)."""
    msgs = project.paths.messages_jsonl
    pid = getattr(project, "project_id", None)
    if not pid:
        return build_index(msgs), BUILT
    return load_or_build(msgs, cache_path_for(pid, data_root), verify_full=verify_full)


def drop_cache(project_id: str, data_root: str | None = None) -> bool:
    """'색인 다시 만들기' 용: 캐시 파일을 지운다. 지웠으면 True."""
    p = cache_path_for(project_id, data_root)
    try:
        os.remove(p)
        return True
    except FileNotFoundError:
        return False
