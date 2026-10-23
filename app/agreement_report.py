"""일치도 보고서: 잠긴 두 세션을 읽어 report.json · report.md 를 프로젝트 밖에 쓴다
(docs/design/06-evaluation-and-agreement.md 1장·4장·5장·7장, 03 6-14 '잠금'·'보고서', 08 D-11 6·8).

입출력 층이다. Qt 를 import 하지 않는다. 계산은 app.core.agreement 가 한다.

순서 (06 7-1 · 7-3 블라인드)
1. 두 세션의 잠금 파일 `locks/<session_id>.json` 이 **둘 다** 있는지 먼저 본다. 하나라도 없으면 라벨 파일을 하나도
   열지 않고 NotLockedError — 보고서를 만들지 않는다(06 1장 '비교에는 잠긴 세션만', 7-1).
1-1. 보고서를 여는 자리(reader_kind)가 보고서를 쓰고 두 세션을 읽을 수 있는지 본다(03 12-1 visibility). None(세션 밖 —
   보기 전용·검토자 도구)과 조정 세션만 된다. 아니면 ReportBlindError.
1-2. 06 7-1 의 비교 조건(comparison_gate): 비교하는 1차 세션(재라벨 쪽은 계획의 원 세션)을 원 세션으로 하는 relabel/
   계획마다 잠긴 재라벨 세션이 있어야 하고(RelabelPendingError), 관련 잠금의 표본 재현 검증(sample_verify)과 계획 재현
   검증이 모두 통과해야 한다(SampleVerifyError). 이 단계는 잠금·계획·표본 매니페스트만 열고 라벨 파일은 열지 않는다.
2. 잠금 파일에 적힌 기록 파일만 연다. 잠금의 `size` 바이트까지의 SHA-256 이 잠금의 `sha256` 과 같아야 한다.
   그 뒤에 붙은 바이트와 `seq > last_seq` 줄은 '잠금 후 추가' 로 세고 계산에서 뺀다(03 6-14).
   잠금 파일에 없는 같은 세션의 기록 파일(다른 PC 에서 잠금 뒤 만든 것)은 열지 않고 경고로 센다.
3. 두 세션의 기준서 판(guide_version · thresholds_sha256)이 다르면 계산을 거부한다(06 9-4).
4. 현재 유효 라벨(core.resolve) → core.agreement.build_agreement.
5. `<out_root>/<project_id>/<report_id>/report.json · report.md`. out_root 가 프로젝트 폴더·저장소(git 작업 트리) 안이면 거부.

이 모듈은 비교하는 사람(검토자) 쪽 도구다. 라벨링 세션 안에서 부르지 않는다(06 7-3: 라벨링 세션은 data\\reports 를 열지 않음).
보고서에는 채팅 본문·닉네임·근거 한 줄(rationale)·메모가 들어가지 않는다. 남는 것은 숫자·분류·등급·chat_id·시각·ID 뿐이다.

다섯째 묶음(08 D-12 2·3·5·6)
- 연결(±5초)·반응 시각·이어짐·놓친 후보(scope=sample)·출처를 더 계산한다. 새 칸은 report.json 의 `links`·`continuation`·
  `missed`·`origin` 이고, 앞 판의 칸은 그대로 둔다(하위 호환, `format_rev` 2).
- 출처는 두 출처 판정 세션(origin_review)끼리 비교한다. 비교 세션 쌍은 '라벨링 세션끼리'(COMPARABLE_KINDS) 또는
  '출처 판정 세션끼리'(ORIGIN_KINDS) 만 된다. 출처 판정 세션은 재라벨 계획 조건을 보지 않는다(계획이 없다).
- 구간을 고쳐 표본에서 빠진 판정은 일치도에서 빼고 수를 경고·보고서에 1줄 적는다(D-12 2).
- 보고서 머리에 '조정자 = 라벨러' (D-12 3): adjudicator_is_labeler 플래그. 주지 않으면 created_by 가 두 세션의 라벨러
  가운데 하나일 때 참으로 본다(그 근거를 header.adjudicator_source 에 적는다).

여섯째 묶음(08 D-13 3·5, 14 4-2 의 4·5·6)
- 놓친 후보 일치도는 재라벨 둘 사이에서만 낸다. 1차가 낀 보고서의 놓친 후보 절은 '1차는 ④ 없음(D-12 5)' 1줄.
- 재라벨 세션 잠금의 `current_sample`(자기 표본 chat_id 목록·chat_ids_sha256, locking.current_sample_doc)을 읽어
  D-12 2 의 '현재 표본' 으로 쓴다. 해시가 목록과 다르면 LockMismatchError. 없으면(앞 판 잠금) 구간표 근사 + 경고 1줄.
- 채팅 시각(chat_times)은 늘 넘긴다: build_report(chat_times=조회 함수) — 함수는 chat_id 모음을 받아 {chat_id: t_ms} 를
  돌려준다(채팅 색인 캐시 app/chat_index.py 가 맡는다). 주지 않으면 messages.jsonl 을 한 번 훑는다(scan_chat_times).
  보고서 시각은 채팅 시각·종류만 보며 라벨 내용과 섞지 않는다(블라인드).
- 출처 판정 세션끼리의 보고서 머리에 origin/ 대상 파일의 target_id · chat_ids_sha256 · 건수 · basis (D-13 5).
"""
from __future__ import annotations

import builtins
import glob
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Collection, Iterable, Mapping

from . import paths
from .core import agreement as ag
from .core import origin as og
from .core import resolve as rs
from .core import sampling as smp
from .core import visibility as vis
from .core.ids import SESSION_FILE_RE, SESSION_ID_RE
from .core.validate import StrictJsonError, canonical_json, load_json_strict
from .jsonl import ReadResult
from .project import atomic_write, open_project

REPORT_TYPE = "agreement"
LOCK_MAX_BYTES = 4 << 20
EVAL_SETTINGS_MAX_BYTES = 1 << 20
MANIFEST_MAX_BYTES = 64 << 20
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
REPORT_ID_RE = re.compile(r"\AR-[0-9]{8}-[a-z0-9]{4}\Z")
COMPARABLE_KINDS = frozenset({vis.PRIMARY, vis.RELABEL_SAME, vis.RELABEL_SECOND})
ORIGIN_KINDS = frozenset({vis.ORIGIN_REVIEW})       # 출처 판정 세션끼리만 짝 (06 4-7)
FORMAT_REV = 2                                      # 1: 구간·표본만, 2: + 연결·이어짐·놓친 후보·출처 (칸 추가만)

__all__ = [
    "ReportError", "NotLockedError", "RelabelPendingError", "SampleVerifyError", "PlanUnreadableError", "ReportBlindError",
    "LockMismatchError", "GuideMismatchError", "ReportLocationError",
    "LockedSession", "ReportResult", "load_locked_session", "build_report", "write_report", "make_report",
    "render_markdown", "check_out_root", "default_out_root", "reader_block_reason", "pending_relabel_plans",
    "comparison_gate", "COMPARABLE_KINDS", "ORIGIN_KINDS", "FORMAT_REV",
    "scan_chat_times", "origin_target_summaries", "ChatTimesLookup",
]


class ReportError(RuntimeError):
    """보고서를 만들 수 없다. 메시지에 본문·닉네임·경로를 넣지 않는다."""


class NotLockedError(ReportError):
    """두 세션 가운데 잠기지 않은 세션이 있다(06 7-1). 라벨 파일은 열지 않았다."""

    def __init__(self, session_ids: list[str]):
        self.session_ids = list(session_ids)
        super().__init__("잠기지 않은 세션이 있어 보고서를 만들지 않습니다(06 7-1): " + ", ".join(self.session_ids))


class RelabelPendingError(NotLockedError):
    """계획된 재라벨 세션이 아직 잠기지 않았다(06 7-1 '계획된 세션이 모두 잠김'). 라벨 파일은 열지 않았다."""

    def __init__(self, plan_ids: list[str]):
        self.plan_ids = list(plan_ids)
        self.session_ids = []
        ReportError.__init__(self, "계획된 재라벨 세션이 아직 잠기지 않아 보고서를 만들지 않습니다(06 7-1): 계획 "
                             + ", ".join(self.plan_ids))


class SampleVerifyError(ReportError):
    """표본·계획 재현 검증이 통과하지 않았다(06 7-1). 라벨 파일은 열지 않았다."""


class PlanUnreadableError(SampleVerifyError):
    """비교하는(잠긴) 재라벨 세션의 계획 파일을 읽지 못했다 — 깨졌거나 없음(06 7-1). plan_ids 에 그 계획 ID.
    SampleVerifyError 의 하나라 예전처럼 다루면 되고, 해제 조건(seal.unseal_conditions)만 사유 코드를 plan_unreadable 로 가른다."""

    def __init__(self, msg: str, plan_ids: Iterable[str]):
        super().__init__(msg)
        self.plan_ids = list(plan_ids)


class ReportBlindError(ReportError, PermissionError):
    """이 자리(세션 종류)는 보고서를 만들지 않는다(03 12-1: 보고서 쓰기는 조정 세션·세션 밖만). 파일을 열기 전에."""


class LockMismatchError(ReportError):
    """잠금 파일과 기록 파일이 맞지 않는다(잠금 뒤 고쳐짐·잘림·형식 오류)."""


class GuideMismatchError(ReportError):
    """두 세션의 기준서 판이 다르다(06 9-4)."""


class ReportLocationError(ReportError):
    """출력 위치가 프로젝트 폴더·저장소 안이다."""


@dataclass
class LockedSession:
    session_id: str
    lock: dict
    lock_sha256: str
    files: list[dict]                    # [{name, size, sha256}] — 잠긴 바이트의 해시
    records: list[dict]                  # 잠긴 범위의 줄 (seq ≤ last_seq)
    meta: dict                           # session.open body (첫 파일)
    machine4s: list[str]
    after_lock_lines: int = 0            # 잠금 뒤 붙은 줄 수 (계산에서 뺌)
    unlisted_files: int = 0              # 잠금에 없는 같은 세션 기록 파일 수 (열지 않음)
    read_problems: list[dict] = field(default_factory=list)

    @property
    def labeler_id(self) -> str | None:
        m = SESSION_ID_RE.match(self.session_id)
        return m.group(1) if m else None

    @property
    def session_kind(self) -> str | None:
        return vis.session_kind_of_id(self.session_id)


@dataclass
class ReportResult:
    report: dict
    markdown: str
    out_dir: str | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------- 잠금 읽기
def _lock_path(project_root: str, session_id: str) -> str:
    return os.path.join(project_root, "locks", f"{session_id}.json")


def _check_sid(session_id) -> str:
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ReportError("세션 ID 형식이 아닙니다")
    return session_id


def _parse_bytes(data: bytes, max_line_bytes: int = 1 << 20) -> ReadResult:
    """app.jsonl.read 와 같은 규칙으로 바이트를 줄 단위로 읽는다(잠긴 범위만 읽으려고 따로 둔다)."""
    res = ReadResult()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
        res.had_bom = True
    lines = data.split(b"\n")
    ends = data.endswith(b"\n")
    if ends:
        lines = lines[:-1]
    last = len(lines)
    for i, raw in enumerate(lines, start=1):
        raw = raw.rstrip(b"\r")
        if not raw.strip():
            continue
        try:
            if len(raw) > max_line_bytes:
                raise ValueError("줄이 너무 깁니다")
            obj = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
            if not isinstance(obj, dict):
                raise ValueError("object 가 아닙니다")
            res.records.append(obj)
        except (ValueError, UnicodeDecodeError):
            if i == last and not ends:
                res.truncated_tail = True
            else:
                res.corrupt_lines.append(i)
    return res


def _reject_constant(name: str):
    raise ValueError(f"JSON 이 아닌 값: {name}")


def load_locked_session(project_root: str, session_id: str) -> LockedSession:
    """잠금 파일과 그 파일이 가리키는 기록 파일(잠긴 바이트까지)을 읽는다. 잠금이 없으면 NotLockedError."""
    sid = _check_sid(session_id)
    lp = _lock_path(project_root, sid)
    try:
        with builtins.open(lp, "rb") as f:
            raw = f.read(LOCK_MAX_BYTES + 1)
    except FileNotFoundError:
        raise NotLockedError([sid]) from None
    except OSError as e:
        raise LockMismatchError(f"잠금 파일을 읽지 못했습니다: {type(e).__name__}") from None
    try:
        lock = load_json_strict(raw, LOCK_MAX_BYTES)
    except StrictJsonError as e:
        raise LockMismatchError(f"잠금 파일이 엄격 JSON 이 아닙니다: {e}") from None
    if not isinstance(lock, dict) or lock.get("session_id") != sid:
        raise LockMismatchError("잠금 파일의 session_id 가 파일 이름과 다릅니다")
    entries = lock.get("files")
    if not isinstance(entries, list) or not entries:
        raise LockMismatchError("잠금 파일에 files[] 가 없습니다")

    labels_dir = os.path.join(project_root, "labels")
    listed: set[str] = set()
    files: list[dict] = []
    results: list[ReadResult] = []
    after = 0
    m4s: list[str] = []
    for ent in entries:
        if not isinstance(ent, dict):
            raise LockMismatchError("잠금 files[] 의 항목 형식이 아닙니다")
        name, size, sha = ent.get("name"), ent.get("size"), ent.get("sha256")
        name = os.path.basename(name) if isinstance(name, str) else name   # 'labels/<…>.jsonl' 도 받는다
        m = SESSION_FILE_RE.match(name) if isinstance(name, str) else None
        if not m or m.group(1) != sid:
            raise LockMismatchError("잠금 files[] 의 이름이 이 세션의 기록 파일 이름 규칙(03 13-2)에 맞지 않습니다")
        if not ag._is_int(size) or size < 0 or not isinstance(sha, str) or not SHA256_RE.match(sha):
            raise LockMismatchError("잠금 files[] 의 size·sha256 형식이 아닙니다")
        last_seq = ent.get("last_seq")
        if last_seq is not None and (not ag._is_int(last_seq) or last_seq < 0):
            raise LockMismatchError("잠금 files[] 의 last_seq 형식이 아닙니다")
        listed.add(name)
        try:
            with builtins.open(os.path.join(labels_dir, name), "rb") as f:
                data = f.read()
        except OSError as e:
            raise LockMismatchError(f"잠긴 기록 파일을 읽지 못했습니다: {type(e).__name__}") from None
        if len(data) < size:
            raise LockMismatchError("기록 파일이 잠글 때보다 짧습니다(잠금 뒤 고쳐짐)")
        locked = data[:size]
        if hashlib.sha256(locked).hexdigest() != sha:
            raise LockMismatchError("기록 파일의 잠긴 부분 해시가 잠금과 다릅니다(잠금 뒤 고쳐짐)")
        if len(data) > size:
            after += max(1, data[size:].count(b"\n"))
        rr = _parse_bytes(locked)
        if last_seq is not None:
            keep = [r for r in rr.records if not (ag._is_int(r.get("seq")) and r["seq"] > last_seq)]
            after += len(rr.records) - len(keep)
            rr.records = keep
        results.append(rr)
        files.append({"name": name, "size": size, "sha256": sha})
        m4s.append(m.group(2))

    unlisted = 0
    for p in glob.glob(os.path.join(glob.escape(labels_dir), f"{sid}.*.jsonl")):
        n = os.path.basename(p)
        if SESSION_FILE_RE.match(n) and n not in listed:
            unlisted += 1

    records: list[dict] = []
    probs: list[dict] = []
    for rr in results:
        records.extend(rr.records)
        if rr.truncated_tail:
            probs.append({"code": rs.P_TRUNCATED_TAIL})
        for ln in rr.corrupt_lines:
            probs.append({"code": rs.P_CORRUPT_LINE, "line": int(ln)})
    opens = [r for r in records if r.get("kind") == "session.open" and r.get("session_id") == sid]
    if not opens or not isinstance(opens[0].get("body"), dict):
        raise LockMismatchError("기록 파일에 session.open 줄이 없습니다")
    meta = opens[0]["body"]
    for o in opens[1:]:
        b = o.get("body") or {}
        if any(b.get(k) != meta.get(k) for k in ("guide_version", "thresholds_sha256", "session_kind", "project_id")):
            raise LockMismatchError("같은 세션의 기록 파일들이 다른 세션 메타를 가집니다")
    return LockedSession(sid, lock, hashlib.sha256(raw).hexdigest(), files, records, meta, sorted(set(m4s)),
                         after, unlisted, probs)


# ---------------------------------------------------------------------------------------------- 보고서 만들기
def _scope_ranges(meta: Mapping) -> list[tuple[int, int]] | None:
    sc = meta.get("scope") if isinstance(meta, Mapping) else None
    if not isinstance(sc, Mapping) or sc.get("type") != "blocks":
        return None
    out = []
    for r in sc.get("ranges") or []:
        if isinstance(r, Mapping):
            a, b = r.get("start_ms"), r.get("end_ms")
        elif isinstance(r, (list, tuple)) and len(r) == 2:
            a, b = r
        else:
            continue
        if ag._is_int(a) and ag._is_int(b) and b > a:
            out.append((a, b))
    return out


def _buffer_ms(project_data: Mapping, guide_version: str | None) -> int:
    for g in (project_data or {}).get("guides") or []:
        if isinstance(g, Mapping) and g.get("guide_version") == guide_version:
            th = g.get("thresholds") if isinstance(g.get("thresholds"), Mapping) else {}
            sg = th.get("segment") if isinstance(th.get("segment"), Mapping) else {}
            v = sg.get("boundary_buffer_sec")
            if ag._is_num(v) and v >= 0:
                return int(round(v * 1000))
    return ag.DEFAULT_BUFFER_MS


def _engine_author(project_data: Mapping, labeler_id: str | None) -> bool:
    for e in (project_data or {}).get("labelers") or []:
        if isinstance(e, Mapping) and e.get("labeler_id") == labeler_id:
            return bool(e.get("engine_author"))
    return False


def _session_ids_from_names(project_root: str) -> list[str]:
    """labels/ 의 파일 이름에서만 세션 ID 를 얻는다(내용은 열지 않는다)."""
    try:
        names = os.listdir(os.path.join(project_root, "labels"))
    except OSError:
        return []
    out = set()
    for n in names:
        m = SESSION_FILE_RE.match(n)
        if m:
            out.add(m.group(1))
    return sorted(out)


def _file_sha256(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with builtins.open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _sample_scope(project_root: str) -> tuple[list[str], dict[str, int], list[dict]]:
    """samples/*.json 의 계열마다 가장 큰 sample_rev 의 items → (chat_id 목록, chat_id → t_ms, [{sample_set_id, sha256}])."""
    mans = []
    hashes = {}
    for p in sorted(glob.glob(os.path.join(glob.escape(os.path.join(project_root, "samples")), "S-*.json"))):
        try:
            with builtins.open(p, "rb") as f:
                raw = f.read(MANIFEST_MAX_BYTES + 1)
            m = load_json_strict(raw, MANIFEST_MAX_BYTES)
            smp.parse_sample_set_id(m["sample_set_id"])
        except (OSError, StrictJsonError, KeyError, TypeError, smp.SamplingError):
            continue
        mans.append(m)
        hashes[m["sample_set_id"]] = hashlib.sha256(raw).hexdigest()
    latest = smp.latest_of(mans)
    ids: set[str] = set()
    times: dict[str, int] = {}
    used = []
    for key in sorted(latest):
        m = latest[key]
        used.append({"sample_set_id": m["sample_set_id"], "sha256": hashes[m["sample_set_id"]]})
        for it in m.get("items") or []:
            if isinstance(it, Mapping) and isinstance(it.get("chat_id"), str):
                ids.add(it["chat_id"])
                if ag._is_int(it.get("t_ms")):
                    times[it["chat_id"]] = it["t_ms"]
    return sorted(ids), times, used


_CHAT_ID_BYTES_RE = re.compile(rb'"chat_id"\s*:\s*"([^"\\]{1,64})"')
ChatTimesLookup = Callable[[Collection[str]], Mapping[str, int]]


def scan_chat_times(messages_path: str, chat_ids: Collection[str]) -> dict[str, int]:
    """messages.jsonl 을 한 번 훑어 chat_ids 의 t_ms 를 찾는다(채팅 색인 캐시가 없을 때의 대체 길, 14 4-2 의 6번).

    줄마다 chat_id 를 바이트 정규식으로 먼저 보고, 찾는 채팅인 줄만 JSON 으로 읽는다(20만 줄에서도 전체 파싱을 피한다).
    다 찾으면 멈춘다. 읽지 못한 줄·시각이 정수가 아닌 줄은 건너뛴다. 파일이 없으면 빈 dict. 본문은 돌려주지 않는다.
    """
    want = {c for c in chat_ids if isinstance(c, str)}
    out: dict[str, int] = {}
    if not want:
        return out
    try:
        f = builtins.open(messages_path, "rb")
    except OSError:
        return out
    with f:
        for raw in f:
            m = _CHAT_ID_BYTES_RE.search(raw)
            if not m:
                continue
            try:
                cid = m.group(1).decode("ascii")
            except UnicodeDecodeError:
                continue
            if cid not in want or cid in out:
                continue
            try:
                obj = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant)
            except (ValueError, UnicodeDecodeError):
                continue
            t = obj.get("t_ms") if isinstance(obj, dict) else None
            if isinstance(obj, dict) and obj.get("chat_id") == cid and ag._is_int(t) and t >= 0:
                out[cid] = t
                if len(out) == len(want):
                    break
    return out


def _needed_chat_ids(*sides: Mapping) -> set[str]:
    """보고서가 시각을 알아야 하는 채팅: 두 세션의 반응·놓친 후보·출처 라벨의 chat_id (본문은 보지 않는다)."""
    out: set[str] = set()
    for s in sides:
        for key in ("reactions", "missed", "origins"):
            for r in s.get(key) or ():
                c = ag._body(r).get("chat_id")
                if isinstance(c, str):
                    out.add(c)
        for c in s.get("current_sample") or ():
            out.add(c)
    return out


def _chat_times_for(lookup: ChatTimesLookup | None, messages_path: str, ids: set[str],
                    fallback: Mapping[str, int] | None) -> tuple[dict[str, int], str, int]:
    """(chat_id → t_ms, 출처 'lookup'·'messages_scan', 찾지 못한 수). 표본 매니페스트의 t_ms 는 빈자리만 메운다."""
    if lookup is not None:
        got = lookup(frozenset(ids)) or {}
        src = "lookup"
    else:
        got = scan_chat_times(messages_path, ids)
        src = "messages_scan"
    times = {c: int(t) for c, t in got.items() if isinstance(c, str) and ag._is_int(t) and c in ids}
    for c, t in (fallback or {}).items():
        times.setdefault(c, t)
    return times, src, len(ids - set(times))


def _lock_current_sample(ls: LockedSession) -> list[str] | None:
    """재라벨 잠금의 current_sample → chat_id 목록(검증). 없으면 None. 해시·목록이 맞지 않으면 LockMismatchError."""
    from . import locking
    try:
        return locking.lock_current_sample(ls.lock)
    except ValueError as e:
        raise LockMismatchError(f"{ls.session_id}: 잠금의 현재 표본 목록이 맞지 않습니다 ({e})") from None


ORIGIN_DIR = "origin"
ORIGIN_TARGET_MAX_BYTES = 16 << 20


def origin_target_summaries(project_root: str) -> tuple[list[dict], int]:
    """origin/ 의 대상 파일 → ([{target_id, chat_ids_sha256, count, basis}] target_id 순, 읽지 못한 파일 수) (D-13 5).
    출처 판정 화면처럼 파일 전부를 합쳐 쓰므로 전부 적는다. 채팅 본문은 대상 파일에 없다(chat_id 만)."""
    d = os.path.join(project_root, ORIGIN_DIR)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return [], 0
    out: list[dict] = []
    bad = 0
    for n in names:
        if n.startswith(".") or not n.endswith(".json"):
            continue
        try:
            with builtins.open(os.path.join(d, n), "rb") as f:
                raw = f.read(ORIGIN_TARGET_MAX_BYTES + 1)
            t = og.parse_targets(load_json_strict(raw, ORIGIN_TARGET_MAX_BYTES))
            if n != f"{t.target_id}.json":
                raise ValueError("이름")
        except (OSError, ValueError, StrictJsonError, TypeError, KeyError):
            bad += 1
            continue
        out.append({"target_id": t.target_id, "chat_ids_sha256": t.chat_ids_sha256, "count": len(t.items),
                    "basis": t.basis})
    out.sort(key=lambda x: x["target_id"])
    return out, bad


def _side(ls: LockedSession, view: rs.EffectiveView, project_data: Mapping, exposure: vis.AnalysisExposure) -> dict:
    exposed = [r for r in view.other_records if r.get("kind") == "view.exposed" and r.get("session_id") == ls.session_id]
    whats = sorted({(r.get("body") or {}).get("what") for r in exposed if isinstance((r.get("body") or {}).get("what"), str)})
    return {
        "session_id": ls.session_id,
        "labeler_id": ls.labeler_id,
        "session_kind": ls.session_kind,
        "machine_id": ls.meta.get("machine_id"),
        "scope": _scope_ranges(ls.meta),
        "segments": view.items(kind="segment", session_id=ls.session_id),
        "reactions": view.items(kind="reaction", session_id=ls.session_id),
        "utterances": view.items(kind="utterance", session_id=ls.session_id),
        "continuations": view.items(kind="continuation", session_id=ls.session_id),
        "missed": view.items(kind="missed_candidate", session_id=ls.session_id),
        "origins": view.items(kind="origin", session_id=ls.session_id),
        "view_exposed": whats,
        "analysis_exposed": exposure.is_exposed(ls.session_id),
        "engine_author": _engine_author(project_data, ls.labeler_id),
    }


# ---------------------------------------------------------------------------------------------- 비교 조건 (06 7-1)
def reader_block_reason(reader_kind: str | None) -> str | None:
    """이 자리(지금 열린 세션 종류, None = 세션 밖)에서 보고서를 만들 수 없는 까닭. 03 12-1 표에서 보고서 쓰기(RW)와
    다른 잠긴 세션 읽기가 모두 되는 세션 종류는 조정 세션뿐이다. 세션 밖(보기 전용·검토자 도구)도 된다."""
    if reader_kind is None:
        return None
    scope = vis.files_to_open(reader_kind)
    if not scope.can_write("reports"):
        return "이 세션 종류에서는 일치도 보고서를 만들지 않습니다 — 보기 전용이나 조정 세션에서 여세요 (03 12-1, 06 7-3)"
    if not all(scope.may_read_other_session(True, k) for k in COMPARABLE_KINDS | ORIGIN_KINDS):
        return "이 세션 종류는 다른 세션의 라벨을 읽지 않습니다 (06 7-3)"
    if not scope.can_read("origin"):
        return "이 세션 종류는 출처 라벨을 읽지 않습니다 (03 12-1)"
    return None


def pair_family(session_a: str, session_b: str) -> str | None:
    """비교할 수 있는 세션 쌍의 갈래: 'labeling'(1차·재라벨끼리) · 'origin'(출처 판정끼리). 섞였거나 모르면 None."""
    ka, kb = vis.session_kind_of_id(session_a), vis.session_kind_of_id(session_b)
    if ka in COMPARABLE_KINDS and kb in COMPARABLE_KINDS:
        return "labeling"
    if ka in ORIGIN_KINDS and kb in ORIGIN_KINDS:
        return "origin"
    return None


def _read_lock_doc(root: str, sid: str) -> dict:
    """잠금 파일 내용(엄격 JSON). 라벨 파일은 열지 않는다. 없으면 NotLockedError."""
    try:
        with builtins.open(_lock_path(root, sid), "rb") as f:
            raw = f.read(LOCK_MAX_BYTES + 1)
    except FileNotFoundError:
        raise NotLockedError([sid]) from None
    except OSError as e:
        raise LockMismatchError(f"잠금 파일을 읽지 못했습니다: {type(e).__name__}") from None
    try:
        doc = load_json_strict(raw, LOCK_MAX_BYTES)
    except StrictJsonError as e:
        raise LockMismatchError(f"잠금 파일이 엄격 JSON 이 아닙니다: {e}") from None
    if not isinstance(doc, dict) or doc.get("session_id") != sid:
        raise LockMismatchError("잠금 파일의 session_id 가 파일 이름과 다릅니다")
    return doc


def _locked_relabel_plan_ids(root: str) -> dict[str, list[str]]:
    """locks/ 의 재라벨 세션 잠금 → {relabel_plan_id: [session_id]}. 잠금 파일만 연다(라벨 파일은 열지 않는다)."""
    out: dict[str, list[str]] = {}
    try:
        names = sorted(os.listdir(os.path.join(root, "locks")))
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        sid = n[:-5]
        if not SESSION_ID_RE.match(sid) or vis.session_kind_of_id(sid) not in (vis.RELABEL_SAME, vis.RELABEL_SECOND):
            continue
        try:
            pid = _read_lock_doc(root, sid).get("relabel_plan_id")
        except ReportError:
            continue
        if isinstance(pid, str):
            out.setdefault(pid, []).append(sid)
    return out


def pending_relabel_plans(project_root: str, source_session_ids: Iterable[str] | None = None) -> list[str]:
    """잠긴 재라벨 세션이 아직 없는 계획의 plan_id 목록(06 7-1). source_session_ids 를 주면 그 1차 세션의 계획만 본다.
    읽지 못한 계획 파일은 원 세션을 알 수 없어 늘 '아직' 으로 센다(블라인드 쪽으로 안전하게)."""
    from . import relabel as relabel_io
    root = os.path.abspath(project_root)
    want = None if source_session_ids is None else set(source_session_ids)
    locked = _locked_relabel_plan_ids(root)
    out = []
    for p in relabel_io.list_plans(root):
        if not p.usable:
            out.append(p.plan_id)
        elif (want is None or p.source_session_id in want) and p.plan_id not in locked:
            out.append(p.plan_id)
    return out


def comparison_gate(project_root: str, project_data: Mapping, session_ids: Iterable[str]) -> list[str]:
    """06 7-1 '계획된 세션이 모두 잠김 + 표본 재현 검증 통과' 를 본다. 통과하면 경고 문장 목록을 돌려준다.

    잠금 파일·relabel/ 계획·samples/ 매니페스트(계획 재현 검증)만 연다. 라벨 파일은 열지 않는다.
    - 비교하는 1차 세션 = 1차 쪽 세션 + 재라벨 쪽 잠금의 relabel_plan_id 가 가리키는 계획의 원 세션.
    - 그 1차 세션을 원 세션으로 하는 계획마다 잠긴 재라벨 세션이 있어야 한다. 아니면 RelabelPendingError.
    - 관련 잠금(1차·계획 세션·비교하는 두 세션)의 sample_verify 가 모두 ok 이고, 표본 매니페스트가 있는 잠금은
      검증 결과가 비어 있지 않아야 한다. 계획은 verify_plan_on_disk 로 다시 골라 맞아야 한다. 아니면 SampleVerifyError.
    - 비교하는 재라벨 세션의 계획은 **취소됐어도** 다시 골라 맞아야 한다(취소는 '남은 계획' 에서 빼는 것이지 재현 검증을
      면제하지 않는다). 취소된 계획으로 만든 세션은 경고 1줄을 달고 대표값에서 뺀다(build_report, 06 7-1).
    """
    return _comparison_gate(project_root, project_data, session_ids)[0]


def _comparison_gate(project_root: str, project_data: Mapping, session_ids: Iterable[str],
                     cancel_check=None) -> tuple[list[str], list[str]]:
    """comparison_gate 본체. (경고 문장 목록, 계획이 취소된 비교 세션 ID 목록)."""
    from . import relabel as relabel_io
    root = os.path.abspath(project_root)
    sids = list(session_ids)
    docs = {sid: _read_lock_doc(root, sid) for sid in sids}
    warnings: list[str] = []
    primaries: set[str] = set()
    compared_plans: set[str] = set()
    cancelled_sids: list[str] = []
    for sid in sids:
        if vis.session_kind_of_id(sid) == vis.PRIMARY:
            primaries.add(sid)
            continue
        if vis.session_kind_of_id(sid) in ORIGIN_KINDS:
            continue                      # 출처 판정 세션은 재라벨 계획이 없다 (06 4-7)
        pid = docs[sid].get("relabel_plan_id")
        if not isinstance(pid, str):
            warnings.append(f"{sid}: 재라벨 계획 없이 연 세션입니다 (계획 조건을 보지 못함)")
            continue
        try:
            plan = relabel_io.read_plan(root, pid)
        except (OSError, ValueError):
            raise PlanUnreadableError(f"{sid} 의 재라벨 계획 {pid} 를 읽지 못했습니다", [pid]) from None
        primaries.add(plan["source_session_id"])
        compared_plans.add(pid)
        if relabel_io.is_cancelled(root, pid):
            cancelled_sids.append(sid)
            warnings.append(f"{sid}: 재라벨 계획 {pid} 가 취소되었습니다 — 대표값으로 쓰지 않고 따로 보고합니다 (06 7-1)")
    pending = pending_relabel_plans(root, primaries)
    if pending:
        raise RelabelPendingError(pending)
    locked = _locked_relabel_plan_ids(root)
    related = set(sids) | primaries
    bad: list[str] = []
    for p in relabel_io.list_plans(root, include_cancelled=True):
        if p.plan_id not in compared_plans and (not p.usable or p.source_session_id not in primaries):
            continue                      # 비교하지 않는 취소·읽지 못한 계획은 보지 않는다 (취소로 막힘을 푼다, D-12 7)
        related.update(locked.get(p.plan_id, ()))
        if cancel_check is not None:
            cancel_check()                # 계획 재현 검증 사이에서 멈출 수 있다(보고서 워커, 14 6-1 의 1)
        v = relabel_io.verify_plan_on_disk(root, project_data, p.plan_id, creator_kind=None)
        if not v.ok:
            bad.append(f"계획 {p.plan_id} 재현 검증 실패 {len(v.problems)}건")
    for sid in sorted(related):
        doc = docs.get(sid) or _read_lock_doc(root, sid)
        ver = doc.get("sample_verify") or []
        if not isinstance(ver, list) or any(not isinstance(v, Mapping) or v.get("ok") is not True for v in ver):
            bad.append(f"{sid}: 표본 재현 검증이 통과하지 않았습니다")
        elif doc.get("sample_manifests") and not ver:
            bad.append(f"{sid}: 표본 매니페스트가 있는데 재현 검증 없이 잠갔습니다")
    if bad:
        raise SampleVerifyError("재현 검증이 통과하지 않아 보고서를 만들지 않습니다(06 7-1): " + "; ".join(bad))
    return warnings, cancelled_sids


def build_report(project_root: str, session_a: str, session_b: str, *, eval_settings: Mapping | None = None,
                 samples_only: bool = False, now: datetime | None = None, app_version: str | None = None,
                 created_by: str | None = None, reader_kind: str | None = None,
                 adjudicator_is_labeler: bool | None = None,
                 chat_times: ChatTimesLookup | None = None, cancel_check=None) -> ReportResult:
    """잠긴 두 세션 → 보고서 dict · Markdown (아직 쓰지 않음). 잠기지 않았으면 NotLockedError(라벨 파일을 열기 전에).

    reader_kind: 보고서를 여는 자리의 세션 종류(None = 세션 밖). reader_block_reason 이 막으면 ReportBlindError.
    06 7-1 조건(comparison_gate)이 안 되면 RelabelPendingError·SampleVerifyError — 모두 라벨 파일을 열기 전에.
    adjudicator_is_labeler: '조정자 = 라벨러' 표시(D-12 3). None 이면 created_by 가 두 세션 라벨러 가운데 하나인지로 정한다.
    chat_times: 채팅 시각 조회 함수 — chat_id 모음(frozenset)을 받아 {chat_id: t_ms}(모르는 것은 빼고)를 돌려준다.
      채팅 색인 캐시(app/chat_index.py)가 넘긴다. None 이면 messages.jsonl 을 한 번 훑는다(scan_chat_times).
    """
    sa, sb = _check_sid(session_a), _check_sid(session_b)
    if sa == sb:
        raise ReportError("같은 세션끼리는 비교하지 않습니다")
    family = pair_family(sa, sb)
    if family is None:
        raise ReportError("비교할 수 있는 세션 쌍이 아닙니다(1차·같은 사람 재라벨·두 번째 사람끼리, 또는 출처 판정끼리)")
    why = reader_block_reason(reader_kind)
    if why:
        raise ReportBlindError(why)
    root = os.path.abspath(project_root)
    # 1) 잠금 확인 — 둘 다 있어야 라벨 파일을 연다 (06 7-1, 7-3)
    missing = [s for s in (sa, sb) if not os.path.isfile(_lock_path(root, s))]
    if missing:
        raise NotLockedError(missing)
    proj = open_project(root)
    if proj.data is None:
        raise ReportError("project.json 을 읽지 못했습니다")
    # 1-2) 06 7-1 비교 조건 — 잠금·계획·표본 매니페스트만 본다(라벨 파일은 아직 열지 않음)
    gate_warnings, plan_cancelled = _comparison_gate(root, proj.data, (sa, sb), cancel_check)
    if cancel_check is not None:
        cancel_check()
    la = load_locked_session(root, sa)
    lb = load_locked_session(root, sb)
    for ls in (la, lb):
        if ls.meta.get("project_id") not in (None, proj.project_id):
            raise LockMismatchError("세션의 project_id 가 이 프로젝트와 다릅니다")
    ga = (la.meta.get("guide_version"), la.meta.get("thresholds_sha256"))
    gb = (lb.meta.get("guide_version"), lb.meta.get("thresholds_sha256"))
    if ga != gb:
        raise GuideMismatchError("두 세션의 기준서 판(guide_version·thresholds_sha256)이 달라 한 점수에 섞지 않습니다(06 9-4)")

    settings = ag.merge_settings(eval_settings)
    settings_sha = hashlib.sha256(canonical_json(settings)).hexdigest()
    buffer_ms = _buffer_ms(proj.data, ga[0])
    sample_ids, manifest_times, used_manifests = _sample_scope(root) if samples_only else (None, None, [])
    messages_sha = _file_sha256(proj.paths.messages_jsonl)
    exposure = vis.analysis_exposure(_session_ids_from_names(root))

    view_a = rs.resolve(la.records)
    view_b = rs.resolve(lb.records)
    side_a = _side(la, view_a, proj.data, exposure)
    side_b = _side(lb, view_b, proj.data, exposure)
    # 계산된 AI 결과 노출(app.core.exposure 규칙 1, 06 6-6 10번): 계획 취소 뒤 만든 재라벨 세션.
    # 라벨 형식은 고정이라(D-15 7) 기록하지 않고, 기록된 view.exposed 와 같은 칸(view_exposed)에 합친다.
    # 봉인 해제(규칙 2)는 이 보고서가 봉인 폴더(data\\ai)를 열지 않는다는 약속 때문에 보지 않는다 — AI 비교 쪽(최종 세트)이 본다.
    # 훈련 열기(규칙 5, D-18 7)는 프로젝트 training\\ 의 시작 시각만 읽어 같은 칸에 합친다(사람 라벨 노출)
    from . import exposure_io
    xview, exposure_warnings = exposure_io.compute_for(
        root, [exposure_io.session_info_of(ls.session_id, v.other_records, ls.lock.get("locked_at"))
               for ls, v in ((la, view_a), (lb, view_b))], reader_kind=reader_kind, include_unseals=False)
    for s in (side_a, side_b):
        s["view_exposed"] = xview.view_exposed(s["session_id"], s["view_exposed"])
    cur_missing: list[str] = []
    for s, ls in ((side_a, la), (side_b, lb)):
        s["plan_cancelled"] = s["session_id"] in plan_cancelled      # 취소된 계획으로 만든 세션 → 대표값에서 뺌
        cur = _lock_current_sample(ls)                                # 14 4-2 의 5번: 잠금의 자기 표본 목록
        if cur is not None:
            s["current_sample"] = cur
        elif ls.session_kind in (vis.RELABEL_SAME, vis.RELABEL_SECOND):
            cur_missing.append(ls.session_id)
    # 14 4-2 의 6번: 채팅 시각을 늘 넘긴다 (색인 조회 함수, 없으면 messages.jsonl 을 훑음). 시각만 — 라벨 내용 없음
    need = _needed_chat_ids(side_a, side_b) | set(sample_ids or ())
    times, times_src, times_missing = _chat_times_for(chat_times, proj.paths.messages_jsonl, need, manifest_times)
    origin_targets, origin_bad = origin_target_summaries(root) if family == "origin" else ([], 0)
    origin_seen_note = None
    if family == "origin":
        # 15 5-2 의 7번: 세션이 열 때 본 대상 파일(session.open.origin_targets)이 두 세션에 다 있으면 그 파일만 적는다.
        # 옛 세션(칸 없음)이 하나라도 있으면 앞 판처럼 origin/ 전부를 적는다.
        seen = [ls.meta.get("origin_targets") for ls in (la, lb)]
        if all(isinstance(s, list) for s in seen):
            ids = {r.get("target_id") for s in seen for r in s if isinstance(r, Mapping)}
            origin_targets = [t for t in origin_targets if t["target_id"] in ids]
            origin_seen_note = "session_open"
        else:
            origin_seen_note = "all_files"

    def sess_input(ls: LockedSession, side: Mapping) -> dict:
        cs = ls.lock.get("current_sample") if isinstance(ls.lock.get("current_sample"), Mapping) else None
        return {"session_id": ls.session_id, "labeler_id": ls.labeler_id, "session_kind": ls.session_kind,
                "machine4": ls.machine4s, "files": ls.files, "lock_sha256": ls.lock_sha256,
                "locked_at": ls.lock.get("locked_at"), "relabel_plan_id": ls.meta.get("relabel_plan_id"),
                "scope": [list(r) for r in side["scope"]] if side["scope"] is not None else "whole",
                "current_sample_sha256": cs.get("chat_ids_sha256") if cs else None}

    data_in = {"project_id": proj.project_id, "messages_sha256": messages_sha,
               "sample_manifests": used_manifests, "dataset_split": proj.data.get("dataset_split"),
               "sample_scope": "sample_manifests" if samples_only else "all_effective_reactions"}
    if family == "origin":
        data_in["origin_targets"] = origin_targets
    inputs = {
        "sessions": [sess_input(la, side_a), sess_input(lb, side_b)],
        "guide": {"guide_version": ga[0], "thresholds_sha256": ga[1], "boundary_buffer_ms": buffer_ms},
        "eval_settings": {"values": settings, "sha256": settings_sha},
        "data": data_in,
    }
    inputs_sha = hashlib.sha256(canonical_json(inputs)).hexdigest()
    inputs["bootstrap_seed"] = inputs_sha[:16]

    if adjudicator_is_labeler is None:
        adj = bool(created_by) and created_by in {la.labeler_id, lb.labeler_id}
        adj_src = "inferred_created_by" if created_by else "unknown"
    else:
        adj, adj_src = bool(adjudicator_is_labeler), "flag"
    res = ag.build_agreement(side_a, side_b, settings=settings, buffer_ms=buffer_ms, seed_material=inputs_sha,
                             sample_chat_ids=sample_ids, chat_times=times,
                             adjudicator_is_labeler=None if adj_src == "unknown" else adj)
    when = now or datetime.now().astimezone()
    report_id = f"R-{when.strftime('%Y%m%d')}-{inputs_sha[:4]}"
    warnings = list(gate_warnings) + exposure_warnings
    for sid in cur_missing:
        warnings.append(f"{sid}: 잠금에 현재 표본 목록(current_sample)이 없어 구간표로 다시 봤습니다 — 영상 밖·τ 변화는 "
                        "보지 않는 근사입니다 (08 D-12 2)")
    if times_missing:
        warnings.append(f"채팅 기록에서 시각을 찾지 못한 채팅 {times_missing}건은 라벨의 첫 표시 시각으로 봤습니다")
    if family == "origin":
        if not origin_targets:
            warnings.append("출처 판정 대상 파일(origin/)이 없어 대상 파일 ID 를 적지 못했습니다 (08 D-13 5)")
        if origin_bad:
            warnings.append(f"읽지 못한 출처 판정 대상 파일 {origin_bad}개는 머리에 적지 않았습니다")
        if len(origin_targets) >= 2:
            warnings.append(f"출처 판정 대상 파일이 {len(origin_targets)}개입니다 — 출처 판정 화면은 이 파일들을 합쳐 씁니다. "
                            "1차 표본이 바뀌어 옛 대상 파일이 남았는지 확인하세요 (15 5-2 의 6·8번)")
        if origin_seen_note == "all_files":
            warnings.append("세션이 어느 대상 파일을 봤는지 기록이 없는 옛 세션이 있어 origin/ 의 대상 파일 전부를 적었습니다")
    for ls in (la, lb):
        if ls.after_lock_lines:
            warnings.append(f"{ls.session_id}: 잠금 뒤 붙은 줄 {ls.after_lock_lines}개를 계산에서 뺐습니다")
        if ls.unlisted_files:
            warnings.append(f"{ls.session_id}: 잠금에 없는 기록 파일 {ls.unlisted_files}개를 열지 않았습니다")
        if ls.read_problems:
            warnings.append(f"{ls.session_id}: 기록 파일 읽기 문제 {len(ls.read_problems)}건")
    for side, view in ((side_a, view_a), (side_b, view_b)):
        n_conf = len(view.conflicts())
        if n_conf:
            warnings.append(f"{side['session_id']}: 충돌(조정 대기) 라벨 {n_conf}건은 계산에서 빠졌습니다")
    if res["samples"]["judged"]["duplicate_chat_a"] or res["samples"]["judged"]["duplicate_chat_b"]:
        warnings.append("같은 채팅에 유효 반응 라벨이 둘 이상인 채팅은 비교에서 뺐습니다")
    dr, dm = res["samples"]["dropped_from_sample"], res["missed"]["dropped_from_sample"]
    if dr["a"] or dr["b"] or dm["a"] or dm["b"]:
        warnings.append(f"구간을 고쳐 표본에서 빠진 판정: 반응 A {dr['a']} · B {dr['b']}, 놓친 후보 A {dm['a']} · B {dm['b']}"
                        " — 남겨 두고 일치도에서 뺐습니다 (08 D-12 2)")
    nt = res["missed"].get("dropped_not_target") or {}
    if nt.get("a") or nt.get("b"):
        warnings.append(f"③ 판정을 고쳐 4단계 대상이 아니게 된 놓친 후보: A {nt.get('a', 0)} · B {nt.get('b', 0)}"
                        " — 남겨 두고 일치도에서 뺐습니다 (06 2-9)")
    nr = res["missed"].get("dropped_no_reaction") or {}
    if nr.get("a") or nr.get("b"):
        warnings.append(f"③ 반응을 물러 근거가 없어진 놓친 후보: A {nr.get('a', 0)} · B {nr.get('b', 0)}"
                        " — 남겨 두고 일치도에서 뺐습니다 (06 2-9)")
    for key, label in (("a", "A"), ("b", "B")):
        p = res["links"]["problems"][key]
        if p["broken"]:
            warnings.append(f"세션 {label}: 연결 발화를 찾지 못한 반응 {p['broken']}건은 연결 비교에서 뺐습니다")

    from . import __version__
    report = {
        "schema": "chatcoach.report.agreement",
        "schema_version": 1,
        "format_rev": FORMAT_REV,
        "report_id": report_id,
        "report_type": REPORT_TYPE,
        "created_at": when.isoformat(timespec="seconds"),
        "created_by": created_by,
        "app_version": app_version or __version__,
        "inputs": inputs,
        "inputs_sha256": inputs_sha,
        "header": {**res["header"],
                   "pair_family": family,
                   "adjudicator_source": adj_src,
                   "chat_times": {"source": times_src, "requested": len(need), "missing": times_missing},
                   "current_sample_method": {"a": res["samples"]["dropped_from_sample"]["method_a"],
                                             "b": res["samples"]["dropped_from_sample"]["method_b"]},
                   **({"origin_targets": origin_targets} if family == "origin" else {}),
                   "sessions": [{"session_id": s["session_id"], "labeler_id": s["labeler_id"],
                                 "session_kind": s["session_kind"], "machine4": ls.machine4s,
                                 "view_exposed": s["view_exposed"], "analysis_exposed": s["analysis_exposed"],
                                 "engine_author": s["engine_author"]}
                                for s, ls in ((side_a, la), (side_b, lb))]},
        "segments": res["segments"],
        "samples": res["samples"],
        "links": res["links"],
        "continuation": res["continuation"],
        "missed": res["missed"],
        "origin": res["origin"],
        "gates": res["gates"],
        "interpretation": res["interpretation"],
        "warnings": warnings,
        "not_computed": ["확신도", "Krippendorff α(세 세션 이상)", "층(무작위·어려움) 나눠 보기",
                         "연결·이어짐·놓친 후보·출처의 신뢰구간"],
    }
    return ReportResult(report, render_markdown(report), None, warnings)


# ---------------------------------------------------------------------------------------------- 쓰기
def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _under(p: str, root: str) -> bool:
    p, r = _norm(p), _norm(root).rstrip("\\/")
    return p == r or p.startswith(r + os.sep)


def _in_git_tree(path: str) -> bool:
    d = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return True
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up


def check_out_root(out_root: str, project_root: str) -> None:
    """출력 폴더 검사: 프로젝트 폴더 안·저장소(코드 폴더·git 작업 트리) 안이면 ReportLocationError."""
    if not isinstance(out_root, str) or not out_root:
        raise ReportLocationError("출력 폴더가 없습니다")
    if _under(out_root, project_root):
        raise ReportLocationError("출력 폴더가 프로젝트 폴더 안입니다. 프로젝트 밖에 쓰세요(06 7-3, 03 2-2)")
    if _under(out_root, paths.code_dir()) or _in_git_tree(out_root):
        raise ReportLocationError("출력 폴더가 저장소(git 작업 트리) 안입니다. 저장소 밖에 쓰세요")


def default_out_root(project_root: str) -> str:
    """기본 출력 폴더: 프로젝트가 '…\\projects\\<slug>' 에 있으면 그 옆 '…\\reports', 아니면 <data>\\reports (03 2-2)."""
    root = os.path.abspath(project_root)
    parent = os.path.dirname(root)
    if os.path.basename(parent).lower() == "projects":
        return os.path.join(os.path.dirname(parent), "reports")
    return os.path.join(paths.data_root(), "reports")


def write_report(result: ReportResult, out_root: str, project_root: str) -> str:
    """<out_root>/<project_id>/<report_id>/ 에 report.json · report.md 를 쓰고 그 폴더를 돌려준다."""
    check_out_root(out_root, project_root)
    rep = result.report
    pid = rep["inputs"]["data"]["project_id"]
    if not isinstance(pid, str) or not re.match(r"\A[0-9a-f-]{36}\Z", pid):
        raise ReportError("project_id 형식이 아닙니다")
    if not REPORT_ID_RE.match(rep["report_id"]):
        raise ReportError("report_id 형식이 아닙니다")
    d = os.path.join(out_root, pid, rep["report_id"])
    os.makedirs(d, exist_ok=True)
    data = (json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=False, sort_keys=False) + "\n").encode("utf-8")
    atomic_write(os.path.join(d, "report.json"), data)
    atomic_write(os.path.join(d, "report.md"), result.markdown.encode("utf-8"))
    result.out_dir = d
    return d


def make_report(project_root: str, session_a: str, session_b: str, *, out_root: str | None = None,
                eval_settings: Mapping | None = None, samples_only: bool = False, now: datetime | None = None,
                created_by: str | None = None, reader_kind: str | None = None,
                adjudicator_is_labeler: bool | None = None,
                chat_times: ChatTimesLookup | None = None, cancel_check=None) -> ReportResult:
    """build_report + write_report. out_root 가 없으면 default_out_root. 출력 위치는 읽기 전에 먼저 검사한다.

    cancel_check(): 워커가 넘기는 취소 확인(취소면 예외). 계획 재현 검증 사이·쓰기 직전에 부른다 — 쓰기 전에 멈추면
    아무 파일도 남지 않는다."""
    root = os.path.abspath(project_root)
    out = out_root or default_out_root(root)
    check_out_root(out, root)
    res = build_report(root, session_a, session_b, eval_settings=eval_settings, samples_only=samples_only,
                       now=now, created_by=created_by, reader_kind=reader_kind,
                       adjudicator_is_labeler=adjudicator_is_labeler, chat_times=chat_times,
                       cancel_check=cancel_check)
    if cancel_check is not None:
        cancel_check()
    write_report(res, out, root)
    return res


def load_eval_settings(path: str) -> dict:
    with builtins.open(path, "rb") as f:
        raw = f.read(EVAL_SETTINGS_MAX_BYTES + 1)
    try:
        obj = load_json_strict(raw, EVAL_SETTINGS_MAX_BYTES)
    except StrictJsonError as e:
        raise ReportError(f"분석 설정 파일이 엄격 JSON 이 아닙니다: {e}") from None
    if not isinstance(obj, dict):
        raise ReportError("분석 설정 파일은 object 여야 합니다")
    return obj


# ---------------------------------------------------------------------------------------------- Markdown
def _f(v, nd: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _ci(ci: Mapping | None) -> str:
    if not ci:
        return "—"
    st = ci.get("status")
    if st == "ok":
        return f"[{ci['lo']:.3f}, {ci['hi']:.3f}]"
    return {"too_few_blocks": f"블록 부족 ({ci.get('n_blocks', 0)}개) — 점추정만", "off": "끔",
            "no_valid_resample": "계산 불가"}.get(st, "—")


_CAT_KO = {"just_chatting": "저스트채팅", "game": "게임", "singing": "노래", "other": "기타", "undeterminable": "판단 불가"}
_GRADE_KO = {"direct": "직접", "likely": "높음", "related_only": "관련", "unconfirmed": "미확인", "unclear": "불명확",
             "undeterminable": "판단 불가"}


def render_markdown(rep: Mapping) -> str:
    """사람이 읽는 보고서. 숫자·분류·등급·ID 만 쓴다(본문·닉네임·근거 없음)."""
    h = rep["header"]
    seg = rep["segments"]
    sm = rep["samples"]
    L: list[str] = []
    L.append(f"# 일치도 보고서 {rep['report_id']}")
    L.append("")
    L.append(f"- 만든 때: {rep['created_at']} · 앱 {rep['app_version']} · 비교: "
             f"{'1인 재라벨 (intra-rater)' if h['comparison'] == 'intra' else '2인 (inter-rater)'}")
    L.append(f"- 기준서 {rep['inputs']['guide']['guide_version']} · thresholds {str(rep['inputs']['guide']['thresholds_sha256'])[:12]}"
             f" · 분석 설정 {rep['inputs']['eval_settings']['sha256'][:12]} · 입력 해시 {rep['inputs_sha256'][:12]}")
    if h.get("same_machine"):
        L.append("- **같은 machine_id** — 두 세션이 같은 PC 에서 매겨졌다. 기능 시험용이며 실제 일치도로 쓰지 않는다.")
    if not h.get("representative", True):
        if h.get("view_exposed_sessions"):
            L.append("- **노출 기록 있음** — 대표값으로 쓰지 않고 따로 보고: " + ", ".join(h["view_exposed_sessions"]))
        if h.get("cancelled_plan_sessions"):
            L.append("- **재라벨 계획 취소됨** — 대표값으로 쓰지 않고 따로 보고: " + ", ".join(h["cancelled_plan_sessions"]))
    if h.get("engine_author_included"):
        L.append("- **엔진 작성자 포함**")
    if h.get("adjudicator_is_labeler"):
        src = {"flag": "입력 플래그", "inferred_created_by": "만든 사람 = 라벨러"}.get(h.get("adjudicator_source"), "")
        L.append(f"- **조정자 = 라벨러** — 1인 작업이라 라벨러가 조정을 겸한다 (08 D-12 3{', ' + src if src else ''})")
    if h.get("pair_family") == "origin":
        ots = h.get("origin_targets") or []
        if ots:
            L.append("- 출처 판정 대상 (08 D-13 5): " + " · ".join(
                f"{t['target_id']} (chat_ids_sha256 {t['chat_ids_sha256']}, {t['count']}건, {t['basis']})" for t in ots))
        else:
            L.append("- 출처 판정 대상 (08 D-13 5): 대상 파일 없음")
    L.append("")
    L.append("| 세션 | labeler_id | 종류 | machine4 | 노출 |")
    L.append("|---|---|---|---|---|")
    for s in h["sessions"]:
        ex = ", ".join(s["view_exposed"]) + (" · 분석 세션" if s["analysis_exposed"] else "")
        L.append(f"| {s['session_id']} | {s['labeler_id']} | {s['session_kind']} | {', '.join(s['machine4'])} | {ex or '없음'} |")
    L.append("")
    if h.get("pair_family") == "origin":
        return _render_origin_only(rep, L)
    L.append("## 판단 기준 (06 5장)")
    L.append("")
    L.append("| 라벨 종류 | 지표 | 값 | 기준 | 비교 건수 | 95% 구간 | 판정 |")
    L.append("|---|---|---|---|---|---|---|")
    for g in rep["gates"]:
        ci = "—" if g["ci"] is None else f"[{g['ci'][0]:.3f}, {g['ci'][1]:.3f}]"
        if g["ci"] is None and g["ci_status"] == "too_few_blocks":
            ci = "블록 부족 — 점추정만"
        metric = g["metric"].replace("|", "\\|")          # 표 칸을 나누지 않게
        L.append(f"| {g['label_kind']} | {metric} | {_f(g['value'])} | {g['op']} {g['gate']} | {g['n']} | {ci} | {g['verdict_ko']} |")
    L.append("")
    L.append("## 해석")
    L.append("")
    for line in rep["interpretation"]:
        L.append(f"- {line}")
    L.append("")
    L.append("## 활동 구간 (06 4-1)")
    L.append("")
    b = seg["boundaries"]
    L.append(f"- 비교 범위 {len(seg['ranges'])}개, 칸 {seg['cells_total']} 중 비교 {seg['cells_compared']} "
             f"(한쪽만 구간 있음 A {seg['uncovered']['a_only']} · B {seg['uncovered']['b_only']} · 둘 다 없음 {seg['uncovered']['neither']}), 블록 {seg['n_blocks']}개")
    L.append(f"- 시간 일치율 {_pct(seg['time_agreement'])} · κ {_f(seg['kappa'])} (95% {_ci(seg['ci'].get('kappa'))})")
    L.append(f"- 핵심 일치율 {_pct(seg['core']['agreement'])} (경계 ±{b['buffer_sec']:g}초 칸 {seg['core'].get('excluded_cells', 0)}개 제외) · 핵심 κ {_f(seg['core']['kappa'])}")
    L.append(f"- 저스트채팅 IoU {_f(seg['jc_iou'])}")
    ur = seg["undeterminable_ratio"]
    L.append(f"- 판단 불가 시간 비율: A {_pct(ur['a'])} · B {_pct(ur['b'])}")
    L.append(f"- 경계: A {b['a_count']} · B {b['b_count']} · 짝 {b['matched']} (창 {b['tolerance_sec']:g}초) · A 만 {b['a_only']} · B 만 {b['b_only']}")
    L.append(f"- |Δt| 중앙값 {_f(b['median_sec'], 1)}초 (95% {_ci(seg['ci'].get('boundary_median_sec'))}) · 90분위 {_f(b['p90_sec'], 1)}초 · "
             f"완충 {b['buffer_sec']:g}초 안 {_pct(b['within_buffer_ratio'])}")
    L.append("")
    L.append("혼동표 (초, 행 = A, 열 = B)")
    L.append("")
    labs = seg["confusion"]["labels"]
    L.append("| A \\ B | " + " | ".join(_CAT_KO.get(x, x) for x in labs) + " |")
    L.append("|---" * (len(labs) + 1) + "|")
    for x, row in zip(labs, seg["confusion"]["rows"]):
        L.append(f"| {_CAT_KO.get(x, x)} | " + " | ".join(str(v) for v in row) + " |")
    L.append("")
    L.append("## 표본 판정 (06 4-2 · 4-3)")
    L.append("")
    j = sm["judged"]
    L.append(f"- 판정: A {j['a']} · B {j['b']} · 둘 다 {j['both']} · A 만 {j['a_only']} · B 만 {j['b_only']}"
             f" · 범위: {'표본 매니페스트의 채팅' if sm['restricted_to_chat_ids'] else '두 세션의 현재 유효 반응 전부'}")
    if sm.get("restricted_to_scope"):
        L.append(f"- 비교 블록 밖이라 뺀 판정: A {j.get('outside_scope_a', 0)} · B {j.get('outside_scope_b', 0)}"
                 " (두 세션 범위의 교집합만 비교, 06 3-5)")
    u = sm["undeterminable"]
    L.append(f"- 판단 불가(확신도): 둘 다 {u['both']} · A 만 {u['a_only']} · B 만 {u['b_only']} — 등급·이진에서 뺌")
    c = sm["unclear"]
    L.append(f"- 불명확: 둘 다 {c['both']} · A 만 {c['a_only']} · B 만 {c['b_only']} · 불명확 일치율 {_pct(c['agreement'])} — 척도·이진에서 뺌")
    bi = sm["binary"]
    L.append(f"- 반응 있음(직접·높음) n={bi['n']}: 둘 다 양성 {bi['both_positive']} · A 만 {bi['a_only']} · B 만 {bi['b_only']} · 둘 다 음성 {bi['both_negative']}")
    ac1 = f" · AC1 {_f(bi['ac1'])}" if bi["show_ac1"] else f" · (AC1 {_f(bi['ac1'])})"
    L.append(f"  - 일치율 {_pct(bi['agreement'])} · κ {_f(bi['kappa'])} (95% {_ci(sm['ci'].get('binary_kappa'))}) · 유병률 {_pct(bi['prevalence'])}{ac1}")
    gr = sm["grade"]
    L.append(f"- 등급 n={gr['n']}: 정확 일치 {_pct(gr['exact'])} · 한 단계 {_pct(gr['one_step'])} · 두 단계 이상 {_pct(gr['two_plus'])}")
    L.append(f"  - 선형 가중 κ {_f(gr['kappa_w'])} (95% {_ci(sm['ci'].get('grade_kappa_w'))}) · 가중치 없는 κ {_f(gr['kappa_unweighted'])}")
    L.append("")
    gl = gr["confusion"]["labels"]
    L.append("| A \\ B | " + " | ".join(_GRADE_KO.get(x, x) for x in gl) + " |")
    L.append("|---" * (len(gl) + 1) + "|")
    for x, row in zip(gl, gr["confusion"]["rows"]):
        L.append(f"| {_GRADE_KO.get(x, x)} | " + " | ".join(str(v) for v in row) + " |")
    L.append("")
    dis = sm["disagreements"]
    L.append(f"불일치 사례 {len(dis)}건 (chat_id · VOD 시각 · A · B)")
    L.append("")
    for d in dis[:200]:
        t = d["t_ms"]
        ts = "—" if t is None else f"{t // 3600000:d}:{t // 60000 % 60:02d}:{t // 1000 % 60:02d}"
        L.append(f"- {d['chat_id']} · {ts} · {_GRADE_KO.get(d['a'], d['a'])} · {_GRADE_KO.get(d['b'], d['b'])}")
    if len(dis) > 200:
        L.append(f"- … {len(dis) - 200}건 더 (report.json)")
    L.append("")
    dfs = sm.get("dropped_from_sample")
    dmf = (rep.get("missed") or {}).get("dropped_from_sample")
    if dfs is not None:
        L.append(f"구간을 고쳐 표본에서 빠진 판정 (남겨 두고 일치도에서 뺌, 08 D-12 2): 반응 A {dfs['a']} · B {dfs['b']}"
                 + (f" · 놓친 후보 A {dmf['a']} · B {dmf['b']}" if dmf else ""))
        L.append("")
    _render_ext(rep, L)
    _render_tail(rep, L)
    return "\n".join(L)


def _table(L: list[str], conf: Mapping, names: Mapping) -> None:
    labs = conf["labels"]
    L.append("| A \\ B | " + " | ".join(str(names.get(x, x)) for x in labs) + " |")
    L.append("|---" * (len(labs) + 1) + "|")
    for x, row in zip(labs, conf["rows"]):
        L.append(f"| {names.get(x, x)} | " + " | ".join(str(v) for v in row) + " |")
    L.append("")


_CONT_KO = {"none": "이어지지 않음", "short": "짧게", "extended": "확장", "new_topic": "새 주제"}
_ORIGIN_KO = {"real": "실제", "virtual": "가상", "undeterminable": "판별 불가"}
_LOW = " (건수 부족 — 판단에 쓰지 않음)"


def _render_ext(rep: Mapping, L: list[str]) -> None:
    """다섯째 묶음 칸(연결·이어짐·놓친 후보). 앞 판 보고서(칸 없음)는 건너뛴다."""
    lk = rep.get("links")
    if lk:
        rt = lk["reaction_time"]
        L.append("## 연결 대상 · 반응 시각 (06 4-0 · 4-3)")
        L.append("")
        L.append(f"- 연결: A {lk['a_links']} · B {lk['b_links']} · 짝 {lk['matched']} (발화 시작 ±{lk['tolerance_sec']:g}초) · "
                 f"A 만 {lk['a_only']} · B 만 {lk['b_only']} (같은 채팅인데 창 밖 {lk['same_chat_far']})")
        L.append(f"- 연결 F1 {_f(lk['f1'])}{_LOW if lk.get('low_n') else ''} · 짝의 발화 시작 |Δt| 중앙값 "
                 f"{_f(lk['utterance_start_median_sec'], 1)}초")
        L.append(f"- 반응 시각 n={rt['n']}: |Δt| 중앙값 {_f(rt['median_sec'], 1)}초 · 90분위 {_f(rt['p90_sec'], 1)}초 · "
                 f"±{rt['tolerance_sec']:g}초 안 {_pct(rt['within_tolerance_ratio'])}")
        L.append("")
    ct = rep.get("continuation")
    if ct:
        L.append("## 이어짐 (06 4-5)")
        L.append("")
        mi, ud, raw = ct["missing"], ct["undeterminable"], ct["raw"]
        L.append(f"- 둘 다 직접·높음인 짝 {ct['pairs_positive_both']} 가운데 비교 {ct['n']} · 비움: 둘 다 {mi['both']} · "
                 f"A 만 {mi['a_only']} · B 만 {mi['b_only']} · 판단 불가: 둘 다 {ud['both']} · A 만 {ud['a_only']} · B 만 {ud['b_only']}")
        L.append(f"- 일치율 {_pct(ct['agreement'])} · κ {_f(ct['kappa'])}{_LOW if ct.get('low_n') else ''}")
        L.append(f"- 원시값: 이어진 길이 차이 중앙값 {_f(raw['span_diff_median_sec'], 1)}초 (n={raw['span_n']}) · 다른 시청자 채팅 수 "
                 f"차이 중앙값 {_f(raw['related_count_diff_median'], 1)} (n={raw['related_n']}) · 잘림 일치 "
                 f"{_pct(raw['truncated_agreement'])} (n={raw['truncated_n']})")
        L.append("")
        _table(L, ct["confusion"], _CONT_KO)
    ms = rep.get("missed")
    if ms and ms.get("pair_applicable") is False:
        L.append("## 놓친 채팅 후보 (06 4-6 · 2-9)")
        L.append("")
        L.append(f"- {ms.get('pair_note') or ag.MISSED_PRIMARY_NOTE} — 놓친 후보 일치도는 같은 블록의 같은 사람 재라벨 ↔ "
                 "다른 사람 재라벨 보고서에서 낸다 (08 D-13 3)")
        L.append("")
    elif ms:
        j, c, rc = ms["judged"], ms["candidate"], ms["reason_code"]
        L.append("## 놓친 채팅 후보 (06 4-6 · 2-9, 재라벨 블록 안 — 08 D-12 5)")
        L.append("")
        L.append(f"- 판정(scope=sample): A {j['a']} · B {j['b']} · 둘 다 {j['both']} · A 만 {j['a_only']} · B 만 {j['b_only']} · "
                 f"표본 밖에서 찾은 것(extra, 비교 안 함) A {j['extra_a']} · B {j['extra_b']}")
        L.append(f"- 후보 여부 n={c['n']}: 둘 다 후보 {c['both_candidate']} · A 만 {c['a_only']} · B 만 {c['b_only']} · 둘 다 아님 {c['both_not']}")
        L.append(f"  - 일치율 {_pct(c['agreement'])} · κ {_f(c['kappa'])} · AC1 {_f(c['ac1'])} · 유병률 {_pct(c['prevalence'])}"
                 f"{_LOW if c.get('low_n') else ''}")
        cd = ms["conditions"]
        L.append("- 조건 일치율: " + " · ".join(f"{k[-1]}번 {_pct(cd[k]['agreement'])} (n={cd[k]['n']})"
                                            for k in ("cond2", "cond3", "cond4")) + " (①은 자동이라 뺌)")
        L.append(f"- 사유 코드(사람이 고른 B·C·D·E·G) n={rc['n']}: 일치율 {_pct(rc['agreement'])} · κ {_f(rc['kappa'])}"
                 f"{_LOW if rc.get('low_n') else ''} · 자동(A·F·auto)이 낀 짝 {rc['auto_excluded']}건은 건수만")
        L.append("")
        _table(L, rc["confusion"], {})
    og = rep.get("origin")
    if og and og.get("compared"):
        _render_origin(og, L)


def _render_origin(og: Mapping, L: list[str]) -> None:
    j, hu = og["judged"], og["human"]
    L.append("## 출처 (06 4-7 · 08 D-12 6)")
    L.append("")
    L.append(f"- 판정: A {j['a']} · B {j['b']} · 둘 다 {j['both']} · A 만 {j['a_only']} · B 만 {j['b_only']}")
    L.append(f"- 전체 n={og['n']}: 일치율 {_pct(og['agreement'])} · κ {_f(og['kappa'])}{_LOW if og.get('low_n') else ''}"
             f" · 자동(기록 대조) 짝 {og['auto_pairs']}건")
    L.append(f"- 사람이 정한 짝끼리 n={hu['n']}: 일치율 {_pct(hu['agreement'])} · κ {_f(hu['kappa'])}{_LOW if hu.get('low_n') else ''}")
    L.append("- 출처 일치도가 낮으면 라벨러 문제보다 생성 기록·OCR 정렬 문제일 가능성이 크다.")
    L.append("")
    _table(L, og["confusion"], _ORIGIN_KO)


def _render_tail(rep: Mapping, L: list[str]) -> None:
    if rep.get("warnings"):
        L.append("## 경고")
        L.append("")
        for w in rep["warnings"]:
            L.append(f"- {w}")
        L.append("")
    L.append("이번 판에서 계산하지 않은 것: " + ", ".join(rep.get("not_computed") or []))
    L.append("")


def _render_origin_only(rep: Mapping, L: list[str]) -> str:
    """출처 판정 세션끼리의 보고서: 해석 · 출처 · 경고만."""
    L.append("## 해석")
    L.append("")
    for line in rep["interpretation"]:
        L.append(f"- {line}")
    L.append("")
    og = rep.get("origin") or {}
    if og.get("compared"):
        _render_origin(og, L)
    else:
        L.append("두 세션 가운데 출처 판정이 없는 세션이 있어 출처 일치도를 내지 않았다.")
        L.append("")
    _render_tail(rep, L)
    return "\n".join(L)
