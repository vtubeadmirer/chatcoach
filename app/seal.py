"""AI 결과 봉인·해제 (docs/design/06-evaluation-and-agreement.md 7-1·7-2, 03 6-14 'AI 봉인 기록'·17장, 08 D-15 7·8).

입출력 층이다. Qt 를 import 하지 않는다. label-event 스키마와 프로젝트 폴더는 건드리지 않는다 — 기록은 모두
`<앱>\\data\\ai\\<project_id>\\` 아래 새 파일이다(D-15 7).

폴더 (<ai 루트> = 프로젝트가 `…\\projects\\<slug>` 에 있으면 그 옆 `…\\ai`, 아니면 `<데이터>\\ai`. 보고서 폴더와 같은 규칙)
  <ai 루트>\\<project_id>\\sealed\\<sha256>.bin   받은 바이트 그대로(봉인 원본)
  <ai 루트>\\<project_id>\\seal.jsonl             추가 전용 기록 — ai.sealed · ai.unsealed
  <ai 루트>\\<project_id>\\runs\\<run_id>.json     해제한 결과(검사·정규화한 사본, 스키마 1판 모양)

봉인 (seal_bytes · seal_file)
- 언제든 받는다. 받을 때 06 6-2 검사를 **들어온 바이트로** 돌려 '유효 / 거부 코드' 만 남긴다. 내용은 화면·로그·기록에
  내지 않고 해석 결과(run_id·엔진 이름·판정 수)도 남기지 않는다(06 7-2). 기록: 해시·크기·시각·사람·출처 종류·검사 결과.
- 같은 바이트를 다시 받으면 새 줄을 쓰지 않는다(봉인은 한 번).

봉인 해제 (unseal) — **봉인 파일을 여는 곳은 이 함수 하나다.** 해제 전에 내용을 읽는 공개 함수는 없다.
- 06 7-1 조건: 라벨링 세션(1차·재라벨)이 모두 잠김, 1차마다 재라벨 계획이 있음, 계획된 재라벨이 모두 잠김,
  표본·계획 재현 검증 통과 — 뒤 둘은 app.agreement_report.comparison_gate 가 본다(잠금·계획·매니페스트만 열고
  라벨 파일은 열지 않는다).
- 조건 전에 열려면 사유(early_reason)가 있어야 하고, 기록에 conditions_met = false 와 사유가 남는다(03 6-14).
- 기록 ai.unsealed: run_id, 그 시점의 잠금 해시 목록 locks[](session_id·잠금 파일 sha256·locked_at), conditions_met,
  reason, 사전 봉인 여부, 보정 판(media_ms 였을 때), 경고 코드, 뺀 chat_id 건수, runs 파일 sha256.

사전 봉인 (06 7-2): 봉인 시각 < 가장 이른 1차 세션 잠금 시각이면 '사전 봉인'. 저장하지 않고 seal.jsonl 과 잠금 파일의
시각에서 계산한다(03 6-14). 잠긴 1차가 아직 없으면 사전 봉인이다(앞으로의 잠금보다 앞선다).

자리(reader_kind) 검사 (03 12-1 visibility.files_to_open 의 ai_sealed): 라벨링·재라벨·출처 판정 세션은 이 모듈의
어느 함수도 부르지 못한다(SealBlindError, 파일을 열기 전에). 받기·상태 보기는 세션 밖(None)과 조정 세션, 해제는 세션 밖과
조정 세션만. 분석 세션(UNSEALED_ONLY)은 상태 보기(seal_status: '봉인됨 n건')와 해제된 결과 읽기(read_run)만 된다 —
나란히 보기(D-15 7)를 위해서다. 봉인 파일을 여는 unseal 은 그대로 막힌다.

봉인은 암호화가 아니다(06 7-4). 탐색기로 파일을 여는 것은 막지 못한다 — 운영 규칙으로 막는다.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping

from . import ai_results as air
from . import jsonl, paths
from .core import visibility as vis
from .core.ids import SESSION_FILE_RE, SESSION_ID_RE
from .core.validate import StrictJsonError, load_json_strict
from .project import atomic_write, open_project

SEAL_FILE = "seal.jsonl"
SEALED_DIR = "sealed"
RUNS_DIR = "runs"
RECORD_V = 1
K_SEALED = "ai.sealed"
K_UNSEALED = "ai.unsealed"
SOURCE_KINDS = ("engine_link", "file")          # 출처 종류: 엔진 link 를 바로 봉인 · 받은 파일
LOCK_MAX_BYTES = 4 << 20
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
PROJECT_ID_RE = re.compile(r"\A[0-9a-f-]{36}\Z")
LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
REASON_MAX = 500

__all__ = [
    "SealError", "SealBlindError", "SealBlockedError", "AlreadyUnsealedError",
    "SealEntry", "SealStatus", "UnsealConditions", "UnsealResult",
    "default_ai_root", "project_ai_dir", "seal_bytes", "seal_file", "seal_status", "unseal_conditions",
    "unseal", "read_run", "unseal_events", "SOURCE_KINDS",
]


class SealError(RuntimeError):
    """봉인·해제를 할 수 없다. 메시지에 내용·경로를 넣지 않는다(해시·코드·건수만)."""


class SealBlindError(SealError, PermissionError):
    """이 자리(세션 종류)는 data\\ai 를 열지 않는다(06 7-3). 파일을 열기 전에."""


class SealBlockedError(SealError):
    """06 7-1 해제 조건이 되지 않았다. reasons 에 사유 문장(세션·계획 ID 와 건수만)."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("봉인 해제 조건(06 7-1)이 되지 않았습니다: " + "; ".join(self.reasons))


class AlreadyUnsealedError(SealError):
    """이미 해제한 봉인이다."""


@dataclass
class SealEntry:
    """seal.jsonl 의 ai.sealed 한 줄 + 해제 여부. 내용을 해석한 값은 run_id(해제 뒤에만) 하나뿐이다."""
    sha256: str
    size: int
    sealed_at: str
    sealed_by: str
    source: str
    check: str                                   # valid · rejected
    reject_reasons: list[str]
    presealed: bool | None = None                # 사전 봉인 여부(계산값)
    unsealed_at: str | None = None
    run_id: str | None = None                    # 해제한 뒤에만
    conditions_met: bool | None = None           # 해제 때 06 7-1 조건이 됐는지
    new: bool = False                            # 이번 호출에서 새로 봉인했는가


@dataclass
class SealStatus:
    project_id: str
    entries: list[SealEntry]
    first_primary_lock_at: str | None
    problems: list[str] = field(default_factory=list)     # seal.jsonl 의 깨진 줄 등(줄 번호만)

    @property
    def sealed_count(self) -> int:
        """아직 해제하지 않은 봉인 수 — 화면은 해제 전에 '봉인됨 n건' 만 보인다(D-15 7)."""
        return sum(1 for e in self.entries if e.unsealed_at is None)

    @property
    def unsealed_count(self) -> int:
        return sum(1 for e in self.entries if e.unsealed_at is not None)


@dataclass
class UnsealConditions:
    met: bool
    reasons: list[str]                           # 안 된 사유
    warnings: list[str]                          # comparison_gate 경고 등
    labeling_sessions: list[str]                 # 본 라벨링 세션 ID


@dataclass
class UnsealResult:
    sha256: str
    run_id: str
    run_path: str
    conditions_met: bool
    presealed: bool
    record: dict


# ----------------------------------------------------------------------------- 자리·경로
def _guard(reader_kind: str | None, *, unseal: bool, unsealed_view: bool = False) -> None:
    """unsealed_view: 건수 보기(seal_status)·해제된 결과 읽기(read_run) — 분석 세션(UNSEALED_ONLY)도 된다."""
    if reader_kind is None:
        return
    access = vis.files_to_open(reader_kind).access("ai_sealed")
    if unsealed_view and access == vis.UNSEALED_ONLY:
        return
    if unseal:
        if reader_kind == vis.ADJUDICATION:
            return
    else:
        if access in (vis.SEAL_ONLY, vis.READ):
            return
    raise SealBlindError("이 세션에서는 AI 결과 봉인 폴더를 열지 않습니다(06 7-3)")


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


def default_ai_root(project_root: str) -> str:
    """기본 ai 루트: 프로젝트가 '…\\projects\\<slug>' 에 있으면 그 옆 '…\\ai', 아니면 <데이터>\\ai (보고서 폴더와 같은 규칙)."""
    root = os.path.abspath(project_root)
    parent = os.path.dirname(root)
    if os.path.basename(parent).lower() == "projects":
        return os.path.join(os.path.dirname(parent), "ai")
    return os.path.join(paths.data_root(), "ai")


def project_ai_dir(project_root: str, project_id: str, ai_root: str | None = None) -> str:
    """<ai 루트>\\<project_id>. 프로젝트 폴더·저장소(git 작업 트리) 안이면 SealError."""
    if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
        raise SealError("project_id 형식이 아닙니다")
    base = os.path.abspath(ai_root or default_ai_root(project_root))
    if _under(base, project_root):
        raise SealError("AI 결과 폴더가 프로젝트 폴더 안입니다(06 7-2: 프로젝트 밖)")
    if _under(base, paths.code_dir()) or _in_git_tree(base):
        raise SealError("AI 결과 폴더가 저장소 안입니다")
    return os.path.join(base, project_id)


def _project(project_root: str):
    p = open_project(project_root)
    if p.data is None or not p.project_id:
        raise SealError("project.json 을 읽지 못했습니다")
    return p


def _now_iso(now: datetime | None) -> str:
    n = now or datetime.now().astimezone()
    if n.tzinfo is None:
        n = n.astimezone()
    return n.isoformat(timespec="milliseconds")


def _parse_dt(s) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        return None
    return d if d.tzinfo is not None else None


def _app_version(v: str | None) -> str:
    if v:
        return v
    from . import __version__
    return __version__


def _check_by(by) -> str:
    if not isinstance(by, str) or not LABELER_RE.match(by):
        raise SealError("by 는 라벨러 ID(예: L01) 여야 합니다")
    return by


# ----------------------------------------------------------------------------- seal.jsonl
def _read_records(d: str) -> tuple[list[dict], list[str]]:
    p = os.path.join(d, SEAL_FILE)
    if not os.path.exists(p):
        return [], []
    res = jsonl.read(p)
    probs = [f"seal.jsonl {n}번째 줄이 깨졌습니다" for n in res.corrupt_lines]
    if res.truncated_tail:
        probs.append("seal.jsonl 마지막 줄이 잘렸습니다")
    return res.records, probs


def _append(d: str, rec: dict) -> dict:
    recs, probs = _read_records(d)
    if probs:
        raise SealError("seal.jsonl 이 깨져 새 기록을 쓰지 않습니다: " + "; ".join(probs[:3]))
    seq = max((r.get("seq", 0) for r in recs if isinstance(r.get("seq"), int)), default=0) + 1
    full = {"v": RECORD_V, "seq": seq, **rec}
    jsonl.append(os.path.join(d, SEAL_FILE), full)
    return full


def _sealed_record(recs: Iterable[dict], sha: str) -> dict | None:
    for r in recs:
        if r.get("kind") == K_SEALED and r.get("sha256") == sha:
            return r
    return None


def _unsealed_record(recs: Iterable[dict], sha: str) -> dict | None:
    for r in recs:
        if r.get("kind") == K_UNSEALED and r.get("sha256") == sha:
            return r
    return None


# ----------------------------------------------------------------------------- 잠금 (읽기만)
def _lock_docs(project_root: str) -> list[tuple[str, str, dict]]:
    """locks/<session_id>.json → [(session_id, 파일 sha256, 내용)]. 라벨 파일은 열지 않는다. 깨진 잠금은 SealError."""
    d = os.path.join(project_root, "locks")
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        if not n.endswith(".json") or not SESSION_ID_RE.match(n[:-5]):
            continue
        sid = n[:-5]
        try:
            with builtins.open(os.path.join(d, n), "rb") as f:
                raw = f.read(LOCK_MAX_BYTES + 1)
            doc = load_json_strict(raw, LOCK_MAX_BYTES)
        except (OSError, StrictJsonError):
            raise SealError(f"{sid} 의 잠금 파일을 읽지 못했습니다") from None
        if doc.get("session_id") != sid:
            raise SealError(f"{sid} 의 잠금 파일 session_id 가 이름과 다릅니다")
        out.append((sid, hashlib.sha256(raw).hexdigest(), doc))
    return out


def _first_primary_lock_at(locks) -> str | None:
    best: tuple[datetime, str] | None = None
    for sid, _h, doc in locks:
        if vis.session_kind_of_id(sid) != vis.PRIMARY:
            continue
        d = _parse_dt(doc.get("locked_at"))
        if d is not None and (best is None or d < best[0]):
            best = (d, doc["locked_at"])
    return best[1] if best else None


def _is_presealed(sealed_at: str, first_primary_lock_at: str | None) -> bool:
    if first_primary_lock_at is None:
        return True
    a, b = _parse_dt(sealed_at), _parse_dt(first_primary_lock_at)
    return a is not None and b is not None and a < b


# ----------------------------------------------------------------------------- 봉인
def seal_bytes(project_root: str, data: bytes, *, source: str, by: str, now: datetime | None = None,
               ai_root: str | None = None, app_version: str | None = None,
               reader_kind: str | None = None) -> SealEntry:
    """받은 바이트를 봉인한다. 검사는 이 바이트로만 하고(봉인 파일을 다시 열지 않는다) 코드만 남긴다.

    크기 상한을 넘으면 봉인하지 않고 SealError. 거부된 파일도 봉인은 한다(check = rejected — 해제는 거부)."""
    _guard(reader_kind, unseal=False)
    if source not in SOURCE_KINDS:
        raise SealError(f"출처 종류는 {SOURCE_KINDS} 가운데 하나여야 합니다")
    _check_by(by)
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data 는 bytes 여야 합니다")
    data = bytes(data)
    if len(data) > air.MAX_BYTES:
        raise SealError(f"크기 상한({air.MAX_BYTES} 바이트)을 넘어 봉인하지 않습니다")
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    sha = hashlib.sha256(data).hexdigest()
    os.makedirs(os.path.join(d, SEALED_DIR), exist_ok=True)
    recs, _ = _read_records(d)
    old = _sealed_record(recs, sha)
    binp = os.path.join(d, SEALED_DIR, f"{sha}.bin")
    if not os.path.exists(binp):
        atomic_write(binp, data)
    if old is not None:
        return _entry(old, recs, _first_primary_lock_at(_lock_docs(project_root)))
    chk = air.check_bytes(data, proj.data)
    rec = _append(d, {
        "kind": K_SEALED, "at": _now_iso(now), "by": by, "app_version": _app_version(app_version),
        "sha256": sha, "size": len(data), "source": source, "check": chk.check,
        "reject_reasons": list(chk.rejects),
    })
    e = _entry(rec, [], _first_primary_lock_at(_lock_docs(project_root)))
    e.new = True
    return e


def seal_file(project_root: str, path: str, *, source: str = "file", by: str, now: datetime | None = None,
              ai_root: str | None = None, app_version: str | None = None,
              reader_kind: str | None = None) -> SealEntry:
    """받은 파일(봉인 전 원본)을 읽어 seal_bytes. 원본을 지우는 것은 부르는 쪽 몫이다."""
    _guard(reader_kind, unseal=False)
    with builtins.open(path, "rb") as f:
        data = f.read(air.MAX_BYTES + 1)
    return seal_bytes(project_root, data, source=source, by=by, now=now, ai_root=ai_root,
                      app_version=app_version, reader_kind=reader_kind)


def _entry(rec: Mapping, recs: Iterable[dict], first_lock: str | None) -> SealEntry:
    e = SealEntry(sha256=rec["sha256"], size=rec.get("size", 0), sealed_at=rec.get("at", ""),
                  sealed_by=rec.get("by", ""), source=rec.get("source", ""), check=rec.get("check", ""),
                  reject_reasons=list(rec.get("reject_reasons") or []))
    e.presealed = _is_presealed(e.sealed_at, first_lock)
    u = _unsealed_record(recs, e.sha256)
    if u is not None:
        e.unsealed_at, e.run_id, e.conditions_met = u.get("at"), u.get("run_id"), u.get("conditions_met")
    return e


def seal_status(project_root: str, *, ai_root: str | None = None, reader_kind: str | None = None) -> SealStatus:
    """seal.jsonl 과 잠금 파일만 읽는다. 봉인 파일(.bin)·runs 는 열지 않는다. 분석 세션도 된다(건수만 보인다)."""
    _guard(reader_kind, unseal=False, unsealed_view=True)
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    recs, probs = _read_records(d)
    first = _first_primary_lock_at(_lock_docs(project_root))
    entries = [_entry(r, recs, first) for r in recs if r.get("kind") == K_SEALED and isinstance(r.get("sha256"), str)]
    return SealStatus(project_id=proj.project_id, entries=entries, first_primary_lock_at=first, problems=probs)


def unseal_events(project_root: str, *, ai_root: str | None = None,
                  reader_kind: str | None = None) -> tuple[list[dict], list[str]]:
    """seal.jsonl 의 ai.unsealed 줄 → ([{run_id, at, sha256, conditions_met}], 깨진 줄 문제). 노출 계산
    (app.core.exposure 규칙 2)에 쓴다. seal.jsonl 만 읽는다(봉인 파일·runs 는 열지 않는다). 자리 규칙은 seal_status 와 같다.
    폴더가 없으면 ([], [])."""
    _guard(reader_kind, unseal=False, unsealed_view=True)
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    recs, probs = _read_records(d)
    out = [{"run_id": r.get("run_id"), "at": r.get("at"), "sha256": r.get("sha256"),
            "conditions_met": r.get("conditions_met")} for r in recs if r.get("kind") == K_UNSEALED]
    return out, probs


# ----------------------------------------------------------------------------- 해제 조건
def _labeling_session_ids(project_root: str, locks) -> list[str]:
    """labels/ 의 파일 이름과 locks/ 이름에서 라벨링 세션(1차·재라벨) ID 를 얻는다. 내용은 열지 않는다."""
    out = {sid for sid, _h, _d in locks}
    try:
        names = os.listdir(os.path.join(project_root, "labels"))
    except OSError:
        names = []
    for n in names:
        m = SESSION_FILE_RE.match(n)
        if m:
            out.add(m.group(1))
    return sorted(s for s in out if vis.session_kind_of_id(s) in vis.LABELING_SESSIONS)


def unseal_conditions(project_root: str, project_data: Mapping | None = None, *,
                      reader_kind: str | None = None) -> UnsealConditions:
    """06 7-1 해제 조건. 잠금·계획·표본 매니페스트만 연다(라벨 파일·봉인 파일은 열지 않는다)."""
    _guard(reader_kind, unseal=True)
    from . import agreement_report as ar
    from . import relabel as relabel_io
    root = os.path.abspath(project_root)
    data = project_data if project_data is not None else _project(root).data
    locks = _lock_docs(root)
    locked = {sid for sid, _h, _d in locks}
    sids = _labeling_session_ids(root, locks)
    reasons: list[str] = []
    warnings: list[str] = []
    unlocked = [s for s in sids if s not in locked]
    if unlocked:
        reasons.append("잠기지 않은 라벨링 세션 " + ", ".join(unlocked))
    primaries = [s for s in sids if vis.session_kind_of_id(s) == vis.PRIMARY]
    if not primaries:
        reasons.append("1차 세션이 없습니다")
    plans = relabel_io.list_plans(root, include_cancelled=True)
    planned = {p.source_session_id for p in plans if p.source_session_id}
    no_plan = [s for s in primaries if s not in planned]
    if no_plan:
        reasons.append("재라벨 계획이 없는 1차 세션 " + ", ".join(no_plan))
    locked_sids = [s for s in sids if s in locked]
    if locked_sids:
        try:
            warnings += ar.comparison_gate(root, data, locked_sids)
        except ar.RelabelPendingError as e:
            reasons.append("잠긴 재라벨 세션이 없는 계획 " + ", ".join(e.plan_ids))
        except ar.SampleVerifyError as e:
            reasons.append(str(e))
        except ar.ReportError as e:
            reasons.append(str(e))
    return UnsealConditions(met=not reasons, reasons=reasons, warnings=warnings, labeling_sessions=sids)


# ----------------------------------------------------------------------------- 해제 (봉인 파일을 여는 유일한 곳)
def unseal(project_root: str, sha256: str, *, by: str, now: datetime | None = None, ai_root: str | None = None,
           early_reason: str | None = None, app_version: str | None = None,
           reader_kind: str | None = None) -> UnsealResult:
    """봉인 하나를 푼다. 06 7-1 조건이 되지 않으면 SealBlockedError — early_reason(사유)을 주면 conditions_met=false 로
    기록하고 연다. 순서: 자리 검사 → 기록 확인 → 조건 → 봉인 파일 열기(sha256 대조) → 다시 검사(채팅 ID 대조) →
    정규화 → runs\\<run_id>.json 쓰기 → ai.unsealed 기록."""
    _guard(reader_kind, unseal=True)
    if not isinstance(sha256, str) or not SHA256_RE.match(sha256):
        raise SealError("sha256 형식이 아닙니다")
    _check_by(by)
    if early_reason is not None:
        early_reason = early_reason.strip()
        if not early_reason or len(early_reason) > REASON_MAX:
            raise SealError(f"앞당겨 여는 사유는 1~{REASON_MAX}자여야 합니다")
    root = os.path.abspath(project_root)
    proj = _project(root)
    d = project_ai_dir(root, proj.project_id, ai_root)
    recs, probs = _read_records(d)
    if probs:
        raise SealError("seal.jsonl 이 깨져 해제하지 않습니다: " + "; ".join(probs[:3]))
    srec = _sealed_record(recs, sha256)
    if srec is None:
        raise SealError("봉인 기록이 없는 해시입니다")
    if _unsealed_record(recs, sha256) is not None:
        raise AlreadyUnsealedError("이미 해제한 봉인입니다")
    if srec.get("check") != "valid":
        raise SealError("받을 때 거부된 결과는 해제하지 않습니다: " + ",".join(srec.get("reject_reasons") or []))
    cond = unseal_conditions(root, proj.data, reader_kind=reader_kind)
    if not cond.met and early_reason is None:
        raise SealBlockedError(cond.reasons)
    # 채팅 ID 대조가 안 되면(채팅 파일이 없거나 한 줄도 읽지 못함) 정규화가 판정을 모두 '없는 chat_id' 로 빼고,
    # 해제는 한 번뿐이라 되돌릴 수 없다 — 기록을 쓰기 전에, 봉인 파일을 열기도 전에 멈춘다.
    chat_ids = air.project_chat_ids(root)
    if not chat_ids:
        raise SealError("프로젝트 채팅 파일(chat/messages.jsonl)이 없거나 읽을 수 있는 줄이 없어 해제하지 않습니다 "
                        "— 채팅 파일을 되살린 뒤 다시 하세요")
    # ---- 여기서부터 봉인 파일을 연다
    binp = os.path.join(d, SEALED_DIR, f"{sha256}.bin")
    try:
        with builtins.open(binp, "rb") as f:
            data = f.read(air.MAX_BYTES + 1)
    except OSError:
        raise SealError("봉인 파일을 읽지 못했습니다") from None
    if hashlib.sha256(data).hexdigest() != sha256 or len(data) != srec.get("size"):
        raise SealError("봉인 파일이 기록과 다릅니다(바뀌었거나 손상)")
    doc = air.parse_bytes(data)
    chk = air.check_doc(doc, proj.data, chat_ids=chat_ids)
    if not chk.ok:
        raise SealError("지금 프로젝트와 대조하면 거부됩니다: " + ",".join(chk.rejects))
    norm, info = air.normalize(doc, proj.data, chat_ids=chat_ids)
    dropped = sum(info.dropped_unknown.values())
    kept = sum(len(arr) for _k, arr in air._arrays(norm))
    if dropped and not kept:
        raise SealError(f"판정 {dropped}건이 모두 이 프로젝트 채팅에 없는 chat_id 라 해제하지 않습니다 "
                        "— 채팅 파일이 다른 방송이거나 손상됐는지 확인하세요 (06 6-2)")
    run_id = norm["run_id"]
    if not air.RUN_ID_RE.match(run_id):
        raise SealError("run_id 형식이 아닙니다")
    for r in recs:
        if r.get("kind") == K_UNSEALED and r.get("run_id") == run_id and r.get("sha256") != sha256:
            raise SealError(f"같은 run_id({run_id})의 다른 결과가 이미 해제되었습니다")
    run_bytes = (json.dumps(norm, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")
    os.makedirs(os.path.join(d, RUNS_DIR), exist_ok=True)
    run_path = os.path.join(d, RUNS_DIR, f"{run_id}.json")
    atomic_write(run_path, run_bytes)
    locks = _lock_docs(root)
    first = _first_primary_lock_at(locks)
    presealed = _is_presealed(srec.get("at", ""), first)
    rec = _append(d, {
        "kind": K_UNSEALED, "at": _now_iso(now), "by": by, "app_version": _app_version(app_version),
        "sha256": sha256, "run_id": run_id,
        "locks": [{"session_id": sid, "sha256": h, "locked_at": doc_.get("locked_at")} for sid, h, doc_ in locks],
        "conditions_met": cond.met, "reason": early_reason if not cond.met else None,
        "unmet": [] if cond.met else cond.reasons,
        "presealed": presealed, "first_primary_lock_at": first,
        "time_axis_in": info.time_axis_in, "calibration_rev": info.calibration_rev,
        "warnings": list(chk.warnings), "gate_warnings": len(cond.warnings),
        "dropped_unknown": info.dropped_unknown, "dropped_duplicate": info.dropped_duplicate,
        "run_sha256": hashlib.sha256(run_bytes).hexdigest(),
    })
    return UnsealResult(sha256=sha256, run_id=run_id, run_path=run_path, conditions_met=cond.met,
                        presealed=presealed, record=rec)


def read_run(project_root: str, run_id: str, *, ai_root: str | None = None, reader_kind: str | None = None) -> dict:
    """해제한 결과 runs\\<run_id>.json 을 읽는다. ai.unsealed 기록이 없으면(해제 전) 열지 않고 SealError.
    파일 sha256 이 기록의 run_sha256 과 다르면 SealError. 분석 세션도 된다(해제된 것만, visibility UNSEALED_ONLY)."""
    _guard(reader_kind, unseal=True, unsealed_view=True)
    if not isinstance(run_id, str) or not air.RUN_ID_RE.match(run_id):
        raise SealError("run_id 형식이 아닙니다")
    proj = _project(project_root)
    d = project_ai_dir(project_root, proj.project_id, ai_root)
    recs, _ = _read_records(d)
    u = [r for r in recs if r.get("kind") == K_UNSEALED and r.get("run_id") == run_id]
    if not u:
        raise SealError("해제 기록이 없는 run_id 입니다(해제 전에는 열지 않습니다)")
    with builtins.open(os.path.join(d, RUNS_DIR, f"{run_id}.json"), "rb") as f:
        raw = f.read(air.MAX_BYTES + 1)
    if hashlib.sha256(raw).hexdigest() != u[-1].get("run_sha256"):
        raise SealError("해제한 결과 파일이 기록과 다릅니다")
    return air.parse_bytes(raw)
