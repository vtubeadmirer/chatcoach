"""AI 결과(ai-result) 가져오기 검사와 정규화 (docs/design/06-evaluation-and-agreement.md 6-1·6-2, 03 17장, 08 D-15 7).

입출력 층이다. Qt 를 import 하지 않는다. 이 모듈은 **봉인 파일을 열지 않는다** — 바이트·객체를 받아 검사만 한다.
봉인(받기)과 봉인 해제(열기)는 app/seal.py 가 맡고, 봉인 파일을 여는 곳은 seal.unseal 하나다.

검사 (06 6-2)
- 거부(rejects): 크기 상한 초과, JSON 이 아님(UTF-8·문법·중복 키), NaN·Infinity, schema·schema_version 불일치,
  스키마 검증 실패(열거형 밖 값 포함), created_at 이 날짜·시각이 아님, target.video_no 가 프로젝트와 다름,
  time_axis = media_ms 인데 media.partial_fp 가 프로젝트 영상과 다름, result 와 grade 의 조합이 맞지 않음
  (unconfirmed 인데 grade 가 있음 · graded 인데 grade 가 없음 — 스키마가 막지 않으므로 여기서 막는다).
- 경고(warnings): target.messages_sha256 이 프로젝트 chat.messages_sha256 과 다르거나 없음, 사전 등록 아님.
- 표시만(notes): messages.jsonl 에 없는 chat_id 건수, 중복 chat_id 건수(첫 줄만 쓴다). chat_id 모음을 줄 때만 센다.

4단계 등급만 받는다(08 D-15 7). 스키마 1판에는 이진 형식이 없고 `grade_scheme` 칸도 없다. 엔진 형식이 정해지면
스키마에 `grade_scheme` 을 더한 뒤 여기서 받는다(06 6-1 '이진만 내는 엔진').

문제 보고에는 코드와 경로·건수만 담는다. 채팅 본문·닉네임·전사문은 이 형식에 없고, 메시지에도 값을 넣지 않는다.
"""
from __future__ import annotations

import builtins
import copy
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Collection, Iterable, Mapping

from . import paths
from .core.timecode import calibration_from_json, video_to_chat_ms
from .core.validate import StrictJsonError, load_json_strict

SCHEMA = "chatcoach/ai-result"
SCHEMA_VERSION = 1
SCHEMA_REL = ("schemas", "ai-result.schema.json")
MAX_BYTES = 64 << 20            # 64 MiB. 4시간 방송 채팅 10만 건 × 한 줄 약 200바이트 ≈ 20 MB 의 세 배
GRADES = ("direct", "likely", "related_only", "unclear")     # 4단계 (D-15 7)
RUN_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
TIME_FIELDS = ("reaction_time_ms", "utterance_start_ms", "utterance_end_ms")

# 거부 코드 (seal.jsonl 의 reject_reasons 에 이 코드만 남는다)
R_TOO_LARGE = "too_large"
R_NOT_JSON = "not_json"
R_NAN = "nan_infinity"
R_SCHEMA_ID = "schema_mismatch"
R_SCHEMA_INVALID = "schema_invalid"
R_CREATED_AT = "created_at_invalid"
R_VIDEO_NO = "video_no_mismatch"
R_PROJECT = "project_unreadable"
R_MEDIA_FP = "media_fp_mismatch"
R_RESULT_GRADE = "result_grade_mismatch"
REJECT_CODES = (R_TOO_LARGE, R_NOT_JSON, R_NAN, R_SCHEMA_ID, R_SCHEMA_INVALID, R_CREATED_AT, R_VIDEO_NO, R_PROJECT,
                R_MEDIA_FP, R_RESULT_GRADE)

# 경고 코드
W_MESSAGES_DIFF = "messages_sha256_mismatch"
W_MESSAGES_NONE = "messages_sha256_missing"
W_NOT_PREREG = "not_preregistered"
WARNING_CODES = (W_MESSAGES_DIFF, W_MESSAGES_NONE, W_NOT_PREREG)

REJECT_TEXT = {
    R_TOO_LARGE: "파일이 크기 상한을 넘습니다",
    R_NOT_JSON: "JSON 이 아닙니다(UTF-8·문법·중복 키)",
    R_NAN: "NaN·Infinity 가 있습니다",
    R_SCHEMA_ID: "schema·schema_version 이 chatcoach/ai-result 1 이 아닙니다",
    R_SCHEMA_INVALID: "스키마 검증을 통과하지 않습니다",
    R_CREATED_AT: "created_at 이 날짜·시각 형식이 아닙니다",
    R_VIDEO_NO: "target.video_no 가 프로젝트 방송 번호와 다릅니다",
    R_PROJECT: "프로젝트 정보를 읽지 못해 대조할 수 없습니다",
    R_MEDIA_FP: "media_ms 결과의 영상 지문이 프로젝트 영상과 다릅니다",
    R_RESULT_GRADE: "result 와 grade 의 조합이 맞지 않습니다",
}
WARNING_TEXT = {
    W_MESSAGES_DIFF: "엔진이 쓴 채팅 파일 해시가 프로젝트와 다릅니다(채팅 집합이 다를 수 있음)",
    W_MESSAGES_NONE: "엔진이 쓴 채팅 파일 해시가 없습니다",
    W_NOT_PREREG: "엔진 설정이 사전 등록되지 않았습니다(보고서 머리에 표시)",
}

__all__ = [
    "SCHEMA", "SCHEMA_VERSION", "MAX_BYTES", "GRADES", "REJECT_CODES", "WARNING_CODES", "REJECT_TEXT", "WARNING_TEXT",
    "AiResultError", "CheckResult", "NormalizeInfo", "parse_bytes", "check_doc", "check_bytes", "normalize",
    "load_schema", "project_chat_ids",
]


class AiResultError(ValueError):
    """가져오기 거부. code 는 REJECT_CODES 가운데 하나. 메시지에 값·본문을 넣지 않는다."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(REJECT_TEXT.get(code, code) + (f" ({detail})" if detail else ""))


@dataclass
class CheckResult:
    """06 6-2 검사 결과. 내용을 해석한 값(run_id·엔진 이름·판정)은 담지 않는다 — 봉인 기록에 그대로 써도 된다."""
    rejects: list[str] = field(default_factory=list)       # 거부 코드
    warnings: list[str] = field(default_factory=list)      # 경고 코드
    problems: list[str] = field(default_factory=list)      # 스키마 문제(경로·사유만, 최대 20줄)
    unknown_chat_ids: int | None = None                    # messages.jsonl 에 없는 chat_id 건수(모음을 줬을 때만)
    duplicate_chat_ids: int = 0                            # 종류별 배열 안 중복 chat_id 건수(첫 줄만 쓴다)

    @property
    def ok(self) -> bool:
        return not self.rejects

    @property
    def check(self) -> str:
        return "valid" if self.ok else "rejected"

    def _reject(self, code: str) -> None:
        if code not in self.rejects:
            self.rejects.append(code)

    def _warn(self, code: str) -> None:
        if code not in self.warnings:
            self.warnings.append(code)


@dataclass
class NormalizeInfo:
    """정규화(봉인 해제 때)에서 한 일. runs 기록·ai.unsealed 에 남긴다."""
    time_axis_in: str
    calibration_rev: int | None              # media_ms → vod_ms 로 바꿀 때 쓴 보정 판. vod_ms 면 None
    dropped_unknown: dict[str, int] = field(default_factory=dict)     # 종류별 messages.jsonl 에 없는 chat_id
    dropped_duplicate: dict[str, int] = field(default_factory=dict)   # 종류별 중복(뒤 줄)


# ----------------------------------------------------------------------------- 스키마
_schema_cache: dict[str, Any] = {}


def load_schema(path: str | None = None):
    """schemas/ai-result.schema.json 해석기(store.LabelSchema — if/then 까지 읽는 손검사기). 없으면 OSError."""
    from .store import LabelSchema
    p = path or os.path.join(paths.code_dir(), *SCHEMA_REL)
    s = _schema_cache.get(p)
    if s is None:
        with builtins.open(p, "rb") as f:
            root = load_json_strict(f.read((1 << 20) + 1), 1 << 20)
        s = _schema_cache[p] = LabelSchema({**root, "$defs": root.get("$defs", {})})
    return s


# ----------------------------------------------------------------------------- 읽기
class _NanError(ValueError):
    pass


def _reject_const(name: str):
    raise _NanError(name)


def _no_dup(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise StrictJsonError("중복 키")
        obj[k] = v
    return obj


def parse_bytes(data: bytes, *, max_bytes: int = MAX_BYTES) -> dict:
    """바이트 → dict. 크기·UTF-8·문법·중복 키·NaN 을 본다. 어기면 AiResultError(code). 앞 BOM 은 벗긴다."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data 는 bytes 여야 합니다")
    if len(data) > max_bytes:
        raise AiResultError(R_TOO_LARGE, f"{len(data)} > {max_bytes} 바이트")
    b = bytes(data)
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    try:
        obj = json.loads(b.decode("utf-8"), parse_constant=_reject_const, object_pairs_hook=_no_dup)
    except _NanError:
        raise AiResultError(R_NAN) from None
    except (UnicodeDecodeError, json.JSONDecodeError, StrictJsonError, RecursionError):
        raise AiResultError(R_NOT_JSON) from None
    if not isinstance(obj, dict):
        raise AiResultError(R_NOT_JSON, "최상위가 object 가 아님")
    return obj


# ----------------------------------------------------------------------------- 검사
def _project_video_no(project_data: Mapping | None) -> str | None:
    b = (project_data or {}).get("broadcast") if isinstance(project_data, Mapping) else None
    v = b.get("video_no") if isinstance(b, Mapping) else None
    return v if isinstance(v, str) else None


def _project_messages_sha(project_data: Mapping | None) -> str | None:
    c = (project_data or {}).get("chat") if isinstance(project_data, Mapping) else None
    v = c.get("messages_sha256") if isinstance(c, Mapping) else None
    return v if isinstance(v, str) else None


def _project_media_fp(project_data: Mapping | None) -> str | None:
    m = (project_data or {}).get("media") if isinstance(project_data, Mapping) else None
    cur = m.get("current") if isinstance(m, Mapping) else None
    v = cur.get("partial_fp") if isinstance(cur, Mapping) else None
    return v if isinstance(v, str) else None


def _valid_datetime(s) -> bool:
    if not isinstance(s, str) or not s:
        return False
    try:
        d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        return False
    return d.tzinfo is not None and "T" in s


def _arrays(doc: Mapping) -> Iterable[tuple[str, list]]:
    for k in ("reactions", "continuations", "missed_candidates", "origins"):
        v = doc.get(k)
        if isinstance(v, list):
            yield k, v


def check_doc(doc, project_data: Mapping | None, *, chat_ids: Collection[str] | None = None,
              schema=None) -> CheckResult:
    """06 6-2 검사. doc 은 parse_bytes 결과. project_data 는 project.json 내용(None 이면 대조 거부).
    chat_ids 를 주면 없는 chat_id 건수를 센다(표시만 — 거부하지 않는다)."""
    res = CheckResult()
    if not isinstance(doc, dict):
        res._reject(R_NOT_JSON)
        return res
    if doc.get("schema") != SCHEMA or doc.get("schema_version") != SCHEMA_VERSION \
            or isinstance(doc.get("schema_version"), bool):
        res._reject(R_SCHEMA_ID)
        return res                                         # 다른 판은 아래 검사가 뜻이 없다
    sch = schema or load_schema()
    probs = sch.check(doc)
    if probs:
        res._reject(R_SCHEMA_INVALID)
        res.problems = probs[:20]
    if not _valid_datetime(doc.get("created_at")):
        res._reject(R_CREATED_AT)
    # result ↔ grade (스키마가 막지 않는 조합)
    for i, r in enumerate(doc.get("reactions") or []):
        if not isinstance(r, dict):
            continue
        g = r.get("grade")
        if r.get("result") == "unconfirmed" and g is not None:
            res._reject(R_RESULT_GRADE)
            res.problems.append(f"$.reactions[{i}]: unconfirmed 인데 grade 가 있습니다")
        elif r.get("result") == "graded" and g not in GRADES:
            res._reject(R_RESULT_GRADE)
            res.problems.append(f"$.reactions[{i}]: graded 인데 4단계 등급이 없습니다")
    # 프로젝트 대조
    if not isinstance(project_data, Mapping):
        res._reject(R_PROJECT)
        return res
    tgt = doc.get("target") if isinstance(doc.get("target"), dict) else {}
    pv = _project_video_no(project_data)
    if pv is None:
        res._reject(R_PROJECT)
    elif tgt.get("video_no") != pv:
        res._reject(R_VIDEO_NO)
    if doc.get("time_axis") == "media_ms":
        media = doc.get("media") if isinstance(doc.get("media"), dict) else {}
        pf = _project_media_fp(project_data)
        if pf is None or media.get("partial_fp") != pf:
            res._reject(R_MEDIA_FP)
    ms = tgt.get("messages_sha256")
    if ms is None:
        res._warn(W_MESSAGES_NONE)
    elif ms != _project_messages_sha(project_data):
        res._warn(W_MESSAGES_DIFF)
    eng = doc.get("engine") if isinstance(doc.get("engine"), dict) else {}
    if eng.get("preregistered") is not True:
        res._warn(W_NOT_PREREG)
    # 표시만: 중복·없는 chat_id
    unknown = 0
    for _k, arr in _arrays(doc):
        seen: set[str] = set()
        for r in arr:
            c = r.get("chat_id") if isinstance(r, dict) else None
            if not isinstance(c, str):
                continue
            if c in seen:
                res.duplicate_chat_ids += 1
                continue
            seen.add(c)
            if chat_ids is not None and c not in chat_ids:
                unknown += 1
    res.unknown_chat_ids = unknown if chat_ids is not None else None
    res.problems = res.problems[:20]
    return res


def check_bytes(data: bytes, project_data: Mapping | None, *, chat_ids: Collection[str] | None = None,
                schema=None) -> CheckResult:
    """parse_bytes + check_doc. 읽기에서 거부되면 그 코드 하나만 담은 결과를 돌려준다(예외 없음).
    돌려주는 것은 검사 결과뿐이다 — 해석한 내용은 돌려주지 않는다(봉인 때 내용을 남기지 않게, 06 7-2)."""
    try:
        doc = parse_bytes(data)
    except AiResultError as e:
        return CheckResult(rejects=[e.code])
    return check_doc(doc, project_data, chat_ids=chat_ids, schema=schema)


# ----------------------------------------------------------------------------- 정규화 (봉인 해제 때)
def normalize(doc: Mapping, project_data: Mapping, *, chat_ids: Collection[str] | None = None
              ) -> tuple[dict, NormalizeInfo]:
    """검사를 통과한 doc → 비교에 쓸 사본(스키마 1판 모양 그대로).

    - time_axis = media_ms 면 프로젝트의 **활성 보정 판**으로 시각을 vod_ms 로 바꾸고 time_axis 를 vod_ms 로 둔다.
      쓴 판 번호를 NormalizeInfo.calibration_rev 에 남긴다(06 6-1). media 블록은 그대로 둔다(어느 파일이었는지).
    - 종류별 배열에서 중복 chat_id 는 첫 줄만, chat_ids 를 주면 거기에 없는 chat_id 는 뺀다(06 6-2 '표시만').
    """
    out = copy.deepcopy(dict(doc))
    info = NormalizeInfo(time_axis_in=str(doc.get("time_axis")), calibration_rev=None)
    if doc.get("time_axis") == "media_ms":
        cal_obj = project_data.get("calibration")
        cal = calibration_from_json(cal_obj)
        info.calibration_rev = cal.rev

        def conv(v):
            return video_to_chat_ms(v, cal) if isinstance(v, int) and not isinstance(v, bool) else v
        for r in out.get("reactions") or []:
            for k in TIME_FIELDS:
                if k in r:
                    r[k] = conv(r[k])
        for s in out.get("segments") or []:
            s["start_ms"], s["end_ms"] = conv(s["start_ms"]), conv(s["end_ms"])
        for c in out.get("continuations") or []:
            for k in ("span_start_ms", "span_end_ms"):
                if k in c:
                    c[k] = conv(c[k])
        out["time_axis"] = "vod_ms"
    for k, arr in list(_arrays(out)):
        seen: set[str] = set()
        keep = []
        for r in arr:
            c = r.get("chat_id")
            if c in seen:
                info.dropped_duplicate[k] = info.dropped_duplicate.get(k, 0) + 1
                continue
            seen.add(c)
            if chat_ids is not None and c not in chat_ids:
                info.dropped_unknown[k] = info.dropped_unknown.get(k, 0) + 1
                continue
            keep.append(r)
        out[k] = keep
    return out, info


# ----------------------------------------------------------------------------- 프로젝트 채팅 ID
def project_chat_ids(project_root: str) -> set[str]:
    """chat/messages.jsonl 의 chat_id 모음. 본문은 쓰지 않는다(한 줄씩 읽고 chat_id 만 꺼낸다). 깨진 줄은 건너뛴다.
    파일이 없으면 빈 모음."""
    p = os.path.join(project_root, "chat", "messages.jsonl")
    out: set[str] = set()
    try:
        f = builtins.open(p, "rb")
    except FileNotFoundError:
        return out
    with f:
        for raw in f:
            raw = raw.strip()
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            if not raw:
                continue
            try:
                obj = json.loads(raw.decode("utf-8"), parse_constant=_reject_const)
            except (ValueError, UnicodeDecodeError):
                continue
            c = obj.get("chat_id") if isinstance(obj, dict) else None
            if isinstance(c, str):
                out.add(c)
    return out
