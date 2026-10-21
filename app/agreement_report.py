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
from typing import Any, Iterable, Mapping

from . import paths
from .core import agreement as ag
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

__all__ = [
    "ReportError", "NotLockedError", "RelabelPendingError", "SampleVerifyError", "ReportBlindError",
    "LockMismatchError", "GuideMismatchError", "ReportLocationError",
    "LockedSession", "ReportResult", "load_locked_session", "build_report", "write_report", "make_report",
    "render_markdown", "check_out_root", "default_out_root", "reader_block_reason", "pending_relabel_plans",
    "comparison_gate",
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
    if not all(scope.may_read_other_session(True, k) for k in COMPARABLE_KINDS):
        return "이 세션 종류는 다른 세션의 라벨을 읽지 않습니다 (06 7-3)"
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
    """
    from . import relabel as relabel_io
    root = os.path.abspath(project_root)
    sids = list(session_ids)
    docs = {sid: _read_lock_doc(root, sid) for sid in sids}
    warnings: list[str] = []
    primaries: set[str] = set()
    for sid in sids:
        if vis.session_kind_of_id(sid) == vis.PRIMARY:
            primaries.add(sid)
            continue
        pid = docs[sid].get("relabel_plan_id")
        if not isinstance(pid, str):
            warnings.append(f"{sid}: 재라벨 계획 없이 연 세션입니다 (계획 조건을 보지 못함)")
            continue
        try:
            plan = relabel_io.read_plan(root, pid)
        except (OSError, ValueError):
            raise SampleVerifyError(f"{sid} 의 재라벨 계획 {pid} 를 읽지 못했습니다") from None
        primaries.add(plan["source_session_id"])
    pending = pending_relabel_plans(root, primaries)
    if pending:
        raise RelabelPendingError(pending)
    locked = _locked_relabel_plan_ids(root)
    related = set(sids) | primaries
    bad: list[str] = []
    for p in relabel_io.list_plans(root):
        if not p.usable or p.source_session_id not in primaries:
            continue
        related.update(locked.get(p.plan_id, ()))
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
    return warnings


def build_report(project_root: str, session_a: str, session_b: str, *, eval_settings: Mapping | None = None,
                 samples_only: bool = False, now: datetime | None = None, app_version: str | None = None,
                 created_by: str | None = None, reader_kind: str | None = None) -> ReportResult:
    """잠긴 두 세션 → 보고서 dict · Markdown (아직 쓰지 않음). 잠기지 않았으면 NotLockedError(라벨 파일을 열기 전에).

    reader_kind: 보고서를 여는 자리의 세션 종류(None = 세션 밖). reader_block_reason 이 막으면 ReportBlindError.
    06 7-1 조건(comparison_gate)이 안 되면 RelabelPendingError·SampleVerifyError — 모두 라벨 파일을 열기 전에.
    """
    sa, sb = _check_sid(session_a), _check_sid(session_b)
    if sa == sb:
        raise ReportError("같은 세션끼리는 비교하지 않습니다")
    for s in (sa, sb):
        if vis.session_kind_of_id(s) not in COMPARABLE_KINDS:
            raise ReportError("비교할 수 있는 세션 종류가 아닙니다(1차·같은 사람 재라벨·두 번째 사람)")
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
    gate_warnings = comparison_gate(root, proj.data, (sa, sb))
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
    sample_ids, chat_times, used_manifests = _sample_scope(root) if samples_only else (None, None, [])
    messages_sha = _file_sha256(proj.paths.messages_jsonl)
    exposure = vis.analysis_exposure(_session_ids_from_names(root))

    view_a = rs.resolve(la.records)
    view_b = rs.resolve(lb.records)
    side_a = _side(la, view_a, proj.data, exposure)
    side_b = _side(lb, view_b, proj.data, exposure)

    def sess_input(ls: LockedSession, side: Mapping) -> dict:
        return {"session_id": ls.session_id, "labeler_id": ls.labeler_id, "session_kind": ls.session_kind,
                "machine4": ls.machine4s, "files": ls.files, "lock_sha256": ls.lock_sha256,
                "locked_at": ls.lock.get("locked_at"), "relabel_plan_id": ls.meta.get("relabel_plan_id"),
                "scope": [list(r) for r in side["scope"]] if side["scope"] is not None else "whole"}

    inputs = {
        "sessions": [sess_input(la, side_a), sess_input(lb, side_b)],
        "guide": {"guide_version": ga[0], "thresholds_sha256": ga[1], "boundary_buffer_ms": buffer_ms},
        "eval_settings": {"values": settings, "sha256": settings_sha},
        "data": {"project_id": proj.project_id, "messages_sha256": messages_sha,
                 "sample_manifests": used_manifests, "dataset_split": proj.data.get("dataset_split"),
                 "sample_scope": "sample_manifests" if samples_only else "all_effective_reactions"},
    }
    inputs_sha = hashlib.sha256(canonical_json(inputs)).hexdigest()
    inputs["bootstrap_seed"] = inputs_sha[:16]

    res = ag.build_agreement(side_a, side_b, settings=settings, buffer_ms=buffer_ms, seed_material=inputs_sha,
                             sample_chat_ids=sample_ids, chat_times=chat_times)
    when = now or datetime.now().astimezone()
    report_id = f"R-{when.strftime('%Y%m%d')}-{inputs_sha[:4]}"
    warnings = list(gate_warnings)
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

    from . import __version__
    report = {
        "schema": "chatcoach.report.agreement",
        "schema_version": 1,
        "report_id": report_id,
        "report_type": REPORT_TYPE,
        "created_at": when.isoformat(timespec="seconds"),
        "created_by": created_by,
        "app_version": app_version or __version__,
        "inputs": inputs,
        "inputs_sha256": inputs_sha,
        "header": {**res["header"],
                   "sessions": [{"session_id": s["session_id"], "labeler_id": s["labeler_id"],
                                 "session_kind": s["session_kind"], "machine4": ls.machine4s,
                                 "view_exposed": s["view_exposed"], "analysis_exposed": s["analysis_exposed"],
                                 "engine_author": s["engine_author"]}
                                for s, ls in ((side_a, la), (side_b, lb))]},
        "segments": res["segments"],
        "samples": res["samples"],
        "gates": res["gates"],
        "interpretation": res["interpretation"],
        "warnings": warnings,
        "not_computed": ["연결(±5초)·반응 시각", "이어짐", "확신도", "놓친 채팅 후보", "출처", "Krippendorff α(세 세션 이상)"],
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
                created_by: str | None = None, reader_kind: str | None = None) -> ReportResult:
    """build_report + write_report. out_root 가 없으면 default_out_root. 출력 위치는 읽기 전에 먼저 검사한다."""
    root = os.path.abspath(project_root)
    out = out_root or default_out_root(root)
    check_out_root(out, root)
    res = build_report(root, session_a, session_b, eval_settings=eval_settings, samples_only=samples_only,
                       now=now, created_by=created_by, reader_kind=reader_kind)
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
        L.append("- **노출 기록 있음** — 대표값으로 쓰지 않고 따로 보고: " + ", ".join(h.get("view_exposed_sessions") or []))
    if h.get("engine_author_included"):
        L.append("- **엔진 작성자 포함**")
    L.append("")
    L.append("| 세션 | labeler_id | 종류 | machine4 | 노출 |")
    L.append("|---|---|---|---|---|")
    for s in h["sessions"]:
        ex = ", ".join(s["view_exposed"]) + (" · 분석 세션" if s["analysis_exposed"] else "")
        L.append(f"| {s['session_id']} | {s['labeler_id']} | {s['session_kind']} | {', '.join(s['machine4'])} | {ex or '없음'} |")
    L.append("")
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
    if rep.get("warnings"):
        L.append("## 경고")
        L.append("")
        for w in rep["warnings"]:
            L.append(f"- {w}")
        L.append("")
    L.append("이번 판에서 계산하지 않은 것: " + ", ".join(rep.get("not_computed") or []))
    L.append("")
    return "\n".join(L)
