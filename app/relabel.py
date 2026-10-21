"""재라벨 계획 파일 relabel/<plan_id>.json 읽기·쓰기, 계획 취소 기록, 계획 색인 relabel/index.json
(docs/design/03-data-model.md 6-14·12-1·12-2, 06 3장·7장, 08 D-11 6·D-12 2·7).

입출력 층이다. Qt 를 import 하지 않는다. 고르기 계산은 app/core/relabel_plan.py 가 하고 여기서는 파일만 다룬다.

- 계획 파일은 한 번만 만든다(이미 있으면 FileExistsError, 덮어쓰지 않는다). 임시 파일에 다 쓰고 fsync →
  하드 링크(못 쓰면 Windows rename)로 '없을 때만' 바꿔 넣는다. 반쯤 쓴 계획 파일은 생기지 않는다.
- 계획을 만드는 쪽(create_plan)은 1차·조정 세션 같은 '만드는 자리' 다. 재라벨 세션(relabel_same·relabel_second)은
  계획을 만들 수 없다: 만들려면 samples/ 와 원 세션 잠금을 읽어야 하는데, 재라벨 세션은 samples/ 를 열지 않는다(03 12-1).
  막힐 때는 디스크에서 무엇도 열기 전에 RelabelBlindError.
- 재라벨 세션이 여는 것은 **자기 계획 파일 하나**(read_plan_for_session)뿐이다. 원 세션의 라벨 파일·samples/ 는
  이 모듈의 어떤 읽기 함수도 열지 않는다(06 7-3). 원 세션 PC 목록은 labels/ 의 **파일 이름만** 본다.
- list_plans 는 계획 파일을 모두 열어 요약 칸(누구·어떤 종류·원 세션·시작 가능 시각)만 돌려준다. 블록 범위·τ_block 은
  돌려주지 않는다. 취소된 계획은 기본으로 빠진다(include_cancelled=True 면 status='cancelled' 로 함께).
- 잠금 파일(locks/<session_id>.json)은 locked_at 과 파일 바이트 sha256 만 쓴다(형식 검사는 app/locking.py 가 맡는다).

계획 취소 (D-12 7, 06 7-1 '검토자가 사유를 적는다')
- cancel_plan: 조정 세션(또는 세션 밖 검토자 도구)에서만. 사유 필수. 계획 파일은 **지우지도 고치지도 않고**
  relabel/<plan_id>.cancel.json 을 한 번만 만든다(계획 파일과 같은 '없을 때만' 쓰기). 읽지 못하는 계획도 취소할 수 있다
  (읽지 못한 계획 하나가 보고서를 영영 막지 않게, 13 4-3 의 4). 취소 기록에는 계획 파일 바이트 sha256 을 남긴다.
- 취소는 형식이 맞는 취소 기록이 있을 때만 인정한다(깨진 취소 기록 → 취소 아님 = 보고서는 계속 막힘, 블라인드 쪽으로 안전).

계획 색인 relabel/index.json (D-12 2, 13 4-3 의 3)
- 계획마다 plan_id·원 세션·라벨러·종류·상태(active·cancelled·unreadable)만 둔다. 블록·시각·τ 는 넣지 않는다.
- 계획을 만들거나 취소할 때 만드는 자리가 계획 파일들을 다시 읽어 통째로 다시 쓴다(임시 파일 → fsync → os.replace,
  원자적). 색인 쓰기가 실패해도 계획·취소 기록은 남는다(색인은 파생물이다). rebuild_index 로 언제든 다시 만든다.
- 세션 화면은 plans_for_labeler 로 색인만 읽고, **그 라벨러 자기 계획 파일만** 연다. 다른 라벨러 계획 본문은 열지 않는다.
  색인이 없거나·깨졌거나·색인에 없는 계획 파일이 있으면 그 자리에서 색인을 한 번 다시 만든다(결과의 rebuilt). 다시
  만들지 못하면 계획을 하나도 열지 않고 재라벨 선택을 막는다(from_index=False, 블라인드 쪽으로 실패).
"""
from __future__ import annotations

import builtins
import hashlib
import json
import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from .core import relabel_plan as rp
from .core import visibility as vis
from .core.ids import SESSION_FILE_RE, SESSION_ID_RE
from .core.validate import StrictJsonError, load_json_strict

LOG = logging.getLogger("chatcoach")

RELABEL_DIR = "relabel"
PLAN_MAX_BYTES = 4 << 20
LOCK_MAX_BYTES = 1 << 20
MANIFEST_MAX_BYTES = 16 << 20
CANCEL_MAX_BYTES = 64 << 10
INDEX_MAX_BYTES = 4 << 20
SAMPLE_FILE_RE = re.compile(r"\AS-[0-9a-f]{8}-d[0-9]+-r[0-9]+\.json\Z")
RAND_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
# 계획을 만들 수 있는 세션 종류(만드는 자리). None = 세션 밖(검토자 도구·테스트).
CREATOR_KINDS = (vis.PRIMARY, vis.ADJUDICATION, None)
# 계획을 취소할 수 있는 자리: 조정 세션(검토자)과 세션 밖 검토자 도구 (06 7-1, D-12 7)
CANCEL_KINDS = (vis.ADJUDICATION, None)

INDEX_NAME = "index.json"
INDEX_SCHEMA = "chatcoach/relabel-index"
CANCEL_SCHEMA = "chatcoach/relabel-cancel"
STATUS_ACTIVE = "active"
STATUS_CANCELLED = "cancelled"
STATUS_UNREADABLE = "unreadable"
STATUSES = (STATUS_ACTIVE, STATUS_CANCELLED, STATUS_UNREADABLE)
REASON_MAX = 1000
_LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
_SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_M4_RE = re.compile(r"\A[a-z2-7]{4}\Z")

__all__ = [
    "RELABEL_DIR", "RelabelBlindError", "PlanSummary", "plan_path", "new_plan_id", "list_plans", "read_plan",
    "read_plan_for_session", "write_plan", "next_plan_no", "source_lock_info", "source_machine4s",
    "load_source_manifests", "create_plan", "verify_plan_on_disk", "session_args",
    "done_ranges_of_view", "SECOND_PLAN_KINDS", "active_plan_for", "create_second_plan",
    "adjudicator_labelers",
    "CANCEL_KINDS", "INDEX_NAME", "STATUS_ACTIVE", "STATUS_CANCELLED", "STATUS_UNREADABLE", "STATUSES",
    "cancel_path", "check_cancel", "read_cancel", "is_cancelled", "cancel_plan",
    "index_path", "check_index", "read_index", "build_index", "rebuild_index", "IndexedPlans", "plans_for_labeler",
]


class RelabelBlindError(PermissionError):
    """이 세션 종류는 그 파일을 열 수 없다(06 7-3). 디스크에서 열기 전에 올린다."""


def _check_plan_id(plan_id) -> str:
    if not isinstance(plan_id, str) or not rp.PLAN_ID_RE.match(plan_id):
        raise ValueError(f"plan_id 형식이 아닙니다: {plan_id!r}")
    return plan_id


def plan_path(project_root: str, plan_id: str) -> str:
    """relabel/<plan_id>.json. plan_id 는 정규식을 통과해야 한다(파일 이름이 되므로)."""
    return os.path.join(project_root, RELABEL_DIR, _check_plan_id(plan_id) + ".json")


def new_plan_id(now: datetime | None = None, rand4: str | None = None) -> str:
    """'RP-<yyyymmdd>-<4자>' (03 13-1). 날짜는 사람이 보기 위한 것이다."""
    t = now or datetime.now().astimezone()
    r = rand4 if rand4 is not None else "".join(RAND_ALPHABET[secrets.randbelow(36)] for _ in range(4))
    return _check_plan_id(f"RP-{t.strftime('%Y%m%d')}-{r}")


def _read_bytes(path: str, limit: int) -> bytes:
    with builtins.open(path, "rb") as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise StrictJsonError(f"파일이 너무 큽니다 (> {limit} 바이트)")
    return raw


def _plan_names(project_root: str) -> list[str]:
    """relabel/ 의 계획 파일 이름에서 plan_id 만(이름 순). 내용은 열지 않는다."""
    try:
        names = sorted(os.listdir(os.path.join(project_root, RELABEL_DIR)))
    except OSError:
        return []
    return [n[:-5] for n in names if n.endswith(".json") and rp.PLAN_ID_RE.match(n[:-5])]


# ---------------------------------------------------------------------------- 읽기
@dataclass(frozen=True)
class PlanSummary:
    """세션 선택 화면이 알아야 하는 계획 요약. problem 이 있으면 쓸 수 없는 계획이다(본문 없이 사유만).

    status: active(쓸 수 있음) · cancelled(취소 기록 있음) · unreadable(읽지 못함, problem 에 사유).
    """
    plan_id: str
    session_kind: str | None = None
    labeler_id: str | None = None
    source_session_id: str | None = None
    source_labeler_id: str | None = None
    source_machine4s: tuple[str, ...] = ()
    earliest_start_at: str | None = None
    enforce_interval: bool = False
    same_machine_as_source: bool = False
    problem: str | None = None
    status: str = STATUS_ACTIVE

    @property
    def usable(self) -> bool:
        return self.problem is None and self.status == STATUS_ACTIVE

    def as_plan(self) -> dict:
        """core.relabel_plan.start_gate·plan_refusal·same_machine 에 넘길 최소 dict."""
        return {"plan_id": self.plan_id, "session_kind": self.session_kind, "labeler_id": self.labeler_id,
                "source_labeler_id": self.source_labeler_id, "source_machine4s": list(self.source_machine4s),
                "earliest_start_at": self.earliest_start_at, "enforce_interval": self.enforce_interval}


def read_plan(project_root: str, plan_id: str) -> dict:
    """계획 파일 하나를 읽고 모양을 검사한다. 파일이 없으면 FileNotFoundError, 형식이 틀리면 ValueError."""
    raw = _read_bytes(plan_path(project_root, plan_id), PLAN_MAX_BYTES)
    try:
        plan = load_json_strict(raw, PLAN_MAX_BYTES)
    except StrictJsonError as e:
        raise ValueError(f"재라벨 계획 {plan_id} 를 읽지 못했습니다: {e}") from None
    probs = rp.check_plan(plan)
    if not probs and plan.get("plan_id") != plan_id:
        probs = ["파일 이름과 plan_id 가 다릅니다"]
    if probs:
        raise ValueError(f"재라벨 계획 {plan_id} 형식이 틀렸습니다: {probs[0]}")
    return plan


def read_plan_for_session(project_root: str, session_kind: str, labeler_id: str, plan_id: str) -> dict:
    """재라벨 세션이 자기 계획을 연다(03 12-1 '자기 계획만'). 다른 사람·다른 종류의 계획이면 ValueError.

    재라벨 종류가 아니면 RelabelBlindError(파일을 열기 전에). 취소된 계획이면 ValueError.
    """
    if vis.files_to_open(session_kind).relabel_plans not in (vis.OWN, vis.READ):
        raise RelabelBlindError("이 세션 종류는 재라벨 계획을 열지 않습니다")
    plan = read_plan(project_root, plan_id)
    if session_kind in rp.PLAN_KINDS:
        why = rp.plan_refusal(plan, session_kind, labeler_id)
        if why:
            raise ValueError(f"재라벨 계획 {plan_id}: {why}")
        if is_cancelled(project_root, plan_id):
            raise ValueError(f"재라벨 계획 {plan_id}: 취소된 계획입니다")
    return plan


def _summary_of(project_root: str, pid: str, cancelled: bool) -> PlanSummary:
    status = STATUS_CANCELLED if cancelled else STATUS_ACTIVE
    try:
        p = read_plan(project_root, pid)
    except (OSError, ValueError) as e:
        why = str(e) if isinstance(e, ValueError) else f"읽지 못했습니다 ({type(e).__name__})"
        return PlanSummary(pid, problem=f"{why} — 계획 형식(relabel-plan v1)이 아닙니다",
                           status=STATUS_CANCELLED if cancelled else STATUS_UNREADABLE)
    return PlanSummary(pid, p["session_kind"], p["labeler_id"], p["source_session_id"],
                       p["source_labeler_id"], tuple(p["source_machine4s"]), p["earliest_start_at"],
                       bool(p["enforce_interval"]), bool(p["same_machine_as_source"]), status=status)


def list_plans(project_root: str, *, include_cancelled: bool = False) -> list[PlanSummary]:
    """relabel/RP-*.json 의 요약(이름 순). 읽지 못한 계획도 problem 을 달아 돌려준다(예외 없음).

    취소된 계획(형식이 맞는 취소 기록이 있는 것)은 include_cancelled=False(기본)이면 뺀다 — 보고서를 막는 '남은 계획'
    (agreement_report.pending_relabel_plans)과 세션 선택 화면에서 함께 빠진다.
    """
    out = []
    for pid in _plan_names(project_root):
        cancelled = is_cancelled(project_root, pid)
        if cancelled and not include_cancelled:
            continue
        out.append(_summary_of(project_root, pid, cancelled))
    return out


def next_plan_no(project_root: str) -> int:
    """지금 계획들(취소된 것 포함)의 plan_no 최댓값 + 1 (읽지 못한 계획은 건너뜀, 없으면 1)."""
    best = 0
    for pid in _plan_names(project_root):
        try:
            best = max(best, int(read_plan(project_root, pid)["plan_no"]))
        except (OSError, ValueError, TypeError):
            pass
    return best + 1


# ---------------------------------------------------------------------------- 쓰기 (한 번만)
def _dump(plan: Mapping) -> bytes:
    return (json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _fsync_dir(d: str) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_tmp(d: str, base: str, data: bytes) -> str:
    tmp = os.path.join(d, f".{base}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    with builtins.open(tmp, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return tmp


def _write_new(path: str, data: bytes, exists_msg: str) -> None:
    """없을 때만 원자적으로 만든다(임시 파일 → fsync → 하드 링크, 못 쓰면 Windows rename). 있으면 FileExistsError."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    if os.path.exists(path):
        raise FileExistsError(exists_msg)
    tmp = _write_tmp(d, os.path.basename(path), data)
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise FileExistsError(exists_msg) from None
        except (OSError, NotImplementedError, AttributeError):
            if os.name == "nt":
                try:
                    os.rename(tmp, path)          # 대상이 있으면 FileExistsError
                except FileExistsError:
                    raise FileExistsError(exists_msg) from None
            else:
                from .locking import _write_new_or_remove     # 쓰다가 실패하면 반쯤 쓴 파일을 지운다
                _write_new_or_remove(path, data, FileExistsError(exists_msg))
        _fsync_dir(d)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def write_plan(project_root: str, plan: Mapping) -> str:
    """계획을 relabel/<plan_id>.json 으로 만든다(없을 때만). 경로를 돌려준다. 이미 있으면 FileExistsError."""
    probs = rp.check_plan(plan)
    if probs:
        raise ValueError(f"재라벨 계획 형식이 틀렸습니다: {probs[0]}")
    path = plan_path(project_root, plan["plan_id"])
    _write_new(path, _dump(plan), "같은 plan_id 의 계획이 이미 있습니다 (계획은 고치지 않습니다)")
    return path


# ---------------------------------------------------------------------------- 만드는 자리의 입력
def _guard_creator(creator_kind: str | None) -> None:
    if creator_kind not in CREATOR_KINDS:
        raise RelabelBlindError("재라벨 계획은 1차·조정 세션에서만 만듭니다 (재라벨 세션은 samples/·원 세션 잠금을 열지 않습니다)")


def _check_sid(session_id) -> str:
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError(f"session_id 형식이 아닙니다: {session_id!r}")
    return session_id


def source_lock_info(project_root: str, source_session_id: str, *, creator_kind: str | None) -> dict | None:
    """원 세션 잠금 {locked_at, sha256}. 잠금 파일이 없으면 None. 형식이 틀리면 ValueError."""
    _guard_creator(creator_kind)
    sid = _check_sid(source_session_id)
    p = os.path.join(project_root, "locks", sid + ".json")
    try:
        raw = _read_bytes(p, LOCK_MAX_BYTES)
    except FileNotFoundError:
        return None
    try:
        doc = load_json_strict(raw, LOCK_MAX_BYTES)
    except StrictJsonError as e:
        raise ValueError(f"잠금 파일을 읽지 못했습니다: {e}") from None
    if doc.get("session_id", sid) != sid or not isinstance(doc.get("locked_at"), str):
        raise ValueError("잠금 파일에 이 세션의 locked_at 이 없습니다")
    return {"locked_at": doc["locked_at"], "sha256": hashlib.sha256(raw).hexdigest()}


def source_machine4s(project_root: str, source_session_id: str) -> list[str]:
    """원 세션 기록 파일의 machine4 들. labels/ 의 **파일 이름만** 본다(내용은 열지 않는다)."""
    sid = _check_sid(source_session_id)
    try:
        names = os.listdir(os.path.join(project_root, "labels"))
    except OSError:
        return []
    out = set()
    for n in names:
        m = SESSION_FILE_RE.match(n)
        if m and m.group(1) == sid:
            out.add(m.group(2))
    return sorted(out)


def load_source_manifests(project_root: str, source_session_id: str, *,
                          creator_kind: str | None) -> list[tuple[dict, str]]:
    """samples/S-*.json 가운데 원 세션이 뽑은 매니페스트 (dict, 파일 바이트 sha256). 읽지 못한 파일은 건너뛴다.

    재라벨 세션 종류로 부르면 RelabelBlindError(samples/ 를 열기 전에, 03 12-1).
    """
    _guard_creator(creator_kind)
    sid = _check_sid(source_session_id)
    d = os.path.join(project_root, "samples")
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        if not SAMPLE_FILE_RE.match(n):
            continue
        try:
            raw = _read_bytes(os.path.join(d, n), MANIFEST_MAX_BYTES)
            m = load_json_strict(raw, MANIFEST_MAX_BYTES)
        except (OSError, StrictJsonError):
            continue
        if (m.get("source_segment") or {}).get("session_id") != sid or m.get("sample_set_id") != n[:-5]:
            continue
        out.append((m, hashlib.sha256(raw).hexdigest()))
    return out


def _active_guide(project_data: Mapping) -> tuple[str, str, dict]:
    gv = project_data.get("active_guide_version")
    for g in project_data.get("guides") or []:
        if isinstance(g, Mapping) and g.get("guide_version") == gv:
            th = g.get("thresholds") if isinstance(g.get("thresholds"), Mapping) else {}
            sha = g.get("thresholds_sha256")
            if isinstance(sha, str):
                return gv, sha, dict(th)
    raise ValueError("project.json 에 현재 기준서(active_guide_version)의 thresholds_sha256 이 없습니다")


def create_plan(project_root: str, project_data: Mapping, *, session_kind: str, labeler_id: str,
                source_session_id: str, done_ranges: Iterable, created_by: str, created_machine4: str,
                app_version: str, creator_kind: str | None, hard_scores: Mapping[int, float] | None = None,
                now: datetime | None = None, rand4: str | None = None, extent: Iterable | None = None) -> dict:
    """원 세션 잠금·표본 매니페스트·기준서 값을 읽어 계획을 고르고 relabel/ 에 쓴다. 쓴 계획 dict 를 돌려준다.

    - done_ranges: 1차 세션이 라벨을 마친 범위(채팅 시계 ms). 부르는 쪽(store·잠그기 화면)이 계산해 넘긴다.
      후보 블록을 가리는 데만 쓰고 계획 파일에는 남기지 않는다(06 3-1).
    - extent: 블록 범위를 자를 영상·방송 범위(1차 라벨과 무관한 값, 보통 영상이 덮는 VOD 범위). None 이면 블록 칸 전체.
    - 원 세션이 잠기지 않았으면 ValueError(06 7-1: 계획은 1차 잠금 뒤).
    - creator_kind: 지금 열린 세션 종류(없으면 None). 재라벨 세션이면 RelabelBlindError.
    - 쓴 뒤 relabel/index.json 을 다시 만든다(D-12 2). 색인 쓰기 실패는 계획을 무르지 않는다(경고 로그만).
    """
    _guard_creator(creator_kind)
    lock = source_lock_info(project_root, source_session_id, creator_kind=creator_kind)
    if lock is None:
        raise ValueError("원 세션이 잠기지 않았습니다. 세션을 잠근 뒤에 재라벨 계획을 만듭니다 (06 7-1)")
    gv, th_sha, values = _active_guide(project_data)
    settings, notes = rp.settings_from_values(values)
    t = now or datetime.now().astimezone()
    if t.tzinfo is None:
        t = t.astimezone()
    plan = rp.build_plan(
        plan_id=new_plan_id(t, rand4), plan_no=next_plan_no(project_root),
        project_id=project_data.get("project_id"), session_kind=session_kind, labeler_id=labeler_id,
        source_session_id=source_session_id, source_lock=lock,
        source_machine4s=source_machine4s(project_root, source_session_id), created_machine4=created_machine4,
        sample_seed=project_data.get("sample_seed"), done_ranges=done_ranges, settings=settings,
        manifests=load_source_manifests(project_root, source_session_id, creator_kind=creator_kind),
        hard_scores=hard_scores, guide_version=gv, thresholds_sha256=th_sha, extent=extent,
        created_at=t.isoformat(timespec="seconds"), created_by=created_by, app_version=app_version, notes=notes)
    write_plan(project_root, plan)
    _refresh_index_quietly(project_root)
    return plan


def verify_plan_on_disk(project_root: str, project_data: Mapping, plan_id: str, *,
                        creator_kind: str | None) -> rp.Verification:
    """디스크의 계획을 지금 파일들(잠금·매니페스트·project.sample_seed)로 다시 골라 맞춘다(재현 검증).

    재라벨 세션에서는 부르지 않는다(samples/ 를 연다). 일치도 계산·보고서 쪽(조정 세션)이 쓴다.
    """
    _guard_creator(creator_kind)
    try:
        plan = read_plan(project_root, plan_id)
    except (OSError, ValueError) as e:
        return rp.Verification(False, (str(e),))
    lock = source_lock_info(project_root, plan["source_session_id"], creator_kind=creator_kind)
    if lock is None:
        return rp.Verification(False, ("원 세션 잠금 파일이 없습니다",))
    return rp.verify_plan(plan, sample_seed=project_data.get("sample_seed"), source_lock=lock,
                          manifests=load_source_manifests(project_root, plan["source_session_id"],
                                                          creator_kind=creator_kind))


def done_ranges_of_view(view, session_id: str, coverage: Iterable | None = None) -> list[list[int]]:
    """잠긴 1차 세션 뷰(store.locked_session_view 의 view)에서 '라벨을 마친 범위'(06 3-1)를 계산한다.

    store.done_ranges 와 같은 규칙: 구간 완료 표시(phase.mark segments)가 붙은 구간이 하나라도 있으면 그 구간들만, 없으면
    모든 유효 구간의 [시작, 끝) 를 합친다. coverage(영상이 덮는 범위)를 주면 그 안으로 자른다. 숫자만 돌려준다.
    """
    from .core import phase as ph
    segs = view.items("segment", session_id)
    marks = ph.PhaseMarks(r for r in getattr(view, "other_records", ()) or () if r.get("kind") == "phase.mark")
    done = [s for s in segs if marks.done("segments", s["entity_id"])] or list(segs)

    def _int(v) -> bool:
        return isinstance(v, int) and not isinstance(v, bool)
    spans = sorted((int(s["body"]["start_ms"]), int(s["body"]["end_ms"])) for s in done
                   if _int((s.get("body") or {}).get("start_ms")) and _int((s.get("body") or {}).get("end_ms")))
    merged: list[list[int]] = []
    for a, z in spans:
        if z <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], z)
        else:
            merged.append([a, z])
    if coverage is None:
        return merged
    out: list[list[int]] = []
    for ca, cz in sorted((int(a), int(z)) for a, z in coverage):
        for a, z in merged:
            lo, hi = max(a, ca), min(z, cz)
            if hi > lo:
                out.append([lo, hi])
    return out


# 조정 세션에서 relabel_second 계획을 만들 수 있는 자리 (13 4-3 의 2, 14 4-2 의 9)
SECOND_PLAN_KINDS = (vis.ADJUDICATION, None)


def active_plan_for(project_root: str, session_kind: str, source_session_id: str, labeler_id: str) -> str | None:
    """같은 원 세션·같은 라벨러·같은 종류의 쓸 수 있는(취소되지 않은) 계획 plan_id. 없으면 None. 만드는 자리에서만 부른다."""
    for p in list_plans(project_root):
        if p.usable and p.session_kind == session_kind and p.source_session_id == source_session_id \
                and p.labeler_id == labeler_id:
            return p.plan_id
    return None


def adjudicator_labelers(project_root: str) -> frozenset:
    """labels/ 의 **파일 이름만** 보고, 조정(AD) 세션을 연 적이 있는 라벨러 ID 집합(내용은 열지 않는다)."""
    try:
        names = os.listdir(os.path.join(project_root, "labels"))
    except OSError:
        return frozenset()
    out = set()
    for n in names:
        m = SESSION_FILE_RE.match(n)
        if not m:
            continue
        sm = SESSION_ID_RE.match(m.group(1))
        if sm and vis.session_kind_of_id(m.group(1)) == vis.ADJUDICATION:
            out.add(sm.group(1))
    return frozenset(out)


def create_second_plan(project_root: str, project_data: Mapping, *, source_view, source_session_id: str,
                       labeler_id: str, created_by: str, created_machine4: str, app_version: str,
                       creator_kind: str | None, coverage: Iterable | None = None,
                       hard_scores: Mapping[int, float] | None = None, now: datetime | None = None,
                       rand4: str | None = None) -> dict:
    """조정 세션에서 잠긴 1차 세션을 골라 두 번째 사람 재라벨(relabel_second) 계획을 만든다(14 4-2 의 9).

    같은 사람 재라벨이 남은 동안은 그 1차 세션을 다시 열 수 없으므로(session.reopen_refusal) 1차 자리에서 계획을 못 만든다.
    조정 세션은 잠긴 세션을 모두 읽으므로(03 12-1) 여기서 만든다. 고르기·쓰기는 create_plan 과 같다.
    - creator_kind: 조정 세션 또는 None(세션 밖 도구)만. 그 밖이면 파일을 열기 전에 RelabelBlindError.
    - source_view: store.locked_session_view(원 세션) 의 view. done_ranges 를 여기서 계산한다(done_ranges_of_view).
    - coverage: 영상이 덮는 VOD 범위 [(a, z)]. 주면 done_ranges 를 자르고 블록 범위(extent)로도 쓴다(작업대의 1차 길과 같음).
    - 원 세션이 1차가 아니면·라벨러가 원 세션과 같으면·라벨러가 계획을 만드는 사람(created_by, 조정자)이면·
      라벨러가 이 프로젝트에서 조정 세션을 연 적이 있으면(adjudicator_labelers, 블라인드)·같은 원 세션·라벨러의 쓸 수 있는 relabel_second 계획이 이미 있으면
      ValueError. 원 세션이 잠기지 않았으면 create_plan 이 ValueError.
    """
    if creator_kind not in SECOND_PLAN_KINDS:
        raise RelabelBlindError("조정 세션에서만 두 번째 사람 재라벨 계획을 만듭니다")
    sid = _check_sid(source_session_id)
    if vis.session_kind_of_id(sid) != vis.PRIMARY:
        raise ValueError("재라벨 계획의 원 세션은 1차(primary) 세션이어야 합니다")
    if not isinstance(labeler_id, str) or not _LABELER_RE.match(labeler_id):
        raise ValueError(f"labeler_id 형식이 아닙니다: {labeler_id!r}")
    if SESSION_ID_RE.match(sid).group(1) == labeler_id:
        raise ValueError("두 번째 사람은 원 세션과 다른 라벨러여야 합니다")
    # 블라인드(03 12-1): 조정 세션은 잠긴 세션을 모두 읽으므로, 계획을 만드는 조정자 자신과
    # 이 프로젝트에서 조정 세션을 연 적이 있는 라벨러는 '독립 두 번째 사람' 이 될 수 없다.
    if isinstance(created_by, str) and created_by.strip().upper() == labeler_id:
        raise ValueError("계획을 만드는 사람(조정자)은 두 번째 사람 재라벨을 맡을 수 없습니다 — 원 세션 라벨을 이미 봤습니다")
    if labeler_id in adjudicator_labelers(project_root):
        raise ValueError(f"라벨러 {labeler_id} 는 이 프로젝트에서 조정 세션을 열어 다른 세션 라벨을 봤습니다 — "
                         "두 번째 사람 재라벨을 맡을 수 없습니다")
    have = active_plan_for(project_root, rp.RELABEL_SECOND, sid, labeler_id)
    if have is not None:
        raise ValueError(f"같은 원 세션·라벨러의 두 번째 사람 재라벨 계획 {have} 가 이미 있습니다")
    cov = [tuple(r) for r in coverage] if coverage else None
    ranges = done_ranges_of_view(source_view, sid, cov)
    if not ranges:
        raise ValueError("원 세션에 라벨을 마친 범위가 없습니다" + (" (영상이 덮는 구간 안)" if cov else ""))
    return create_plan(project_root, project_data, session_kind=rp.RELABEL_SECOND, labeler_id=labeler_id,
                       source_session_id=sid, done_ranges=ranges, created_by=created_by,
                       created_machine4=created_machine4, app_version=app_version, creator_kind=creator_kind,
                       hard_scores=hard_scores, now=now, rand4=rand4, extent=cov)


def session_args(plan: Mapping) -> dict:
    """app.session.new_session 에 넘길 값: {"scope": 계획의 블록 범위, "relabel_plan_id": plan_id} (03 12-2)."""
    return {"scope": rp.scope_of(plan), "relabel_plan_id": plan["plan_id"]}


# ---------------------------------------------------------------------------- 계획 취소 (D-12 7)
def cancel_path(project_root: str, plan_id: str) -> str:
    """relabel/<plan_id>.cancel.json (계획 파일 이름 규칙 RP-…json 과 겹치지 않는다)."""
    return os.path.join(project_root, RELABEL_DIR, _check_plan_id(plan_id) + ".cancel.json")


_CANCEL_KEYS = ("schema", "schema_version", "plan_id", "plan_sha256", "plan_readable", "source_session_id",
                "session_kind", "labeler_id", "reason", "cancelled_at", "cancelled_by", "cancelled_machine4",
                "canceller_is_labeler", "app_version")


def check_cancel(doc) -> list[str]:
    """취소 기록 모양 검사(스키마 relabel-plan.schema.json 의 $defs.cancelRecord 와 같은 규칙). 문제 목록."""
    if not isinstance(doc, dict):
        return ["취소 기록이 object 가 아닙니다"]
    probs = [f"필드 {k} 가 없습니다" for k in _CANCEL_KEYS if k not in doc]
    probs += [f"모르는 필드 {k}" for k in sorted(set(doc) - set(_CANCEL_KEYS))]
    if probs:
        return probs
    if doc["schema"] != CANCEL_SCHEMA or doc["schema_version"] != 1:
        probs.append("schema 가 chatcoach/relabel-cancel v1 이 아닙니다")
    if not isinstance(doc["plan_id"], str) or not rp.PLAN_ID_RE.match(doc["plan_id"]):
        probs.append("plan_id 형식이 아닙니다")
    if not isinstance(doc["plan_sha256"], str) or not _SHA_RE.match(doc["plan_sha256"]):
        probs.append("plan_sha256 형식이 아닙니다")
    for k in ("plan_readable", "canceller_is_labeler"):
        if not isinstance(doc[k], bool):
            probs.append(f"{k} 는 true/false 여야 합니다")
    sid = doc["source_session_id"]
    if sid is not None and (not isinstance(sid, str) or not SESSION_ID_RE.match(sid)):
        probs.append("source_session_id 형식이 아닙니다")
    if doc["session_kind"] not in (None,) + rp.PLAN_KINDS:
        probs.append("session_kind 값이 아닙니다")
    for k in ("labeler_id", "cancelled_by"):
        v = doc[k]
        if (k == "cancelled_by" or v is not None) and (not isinstance(v, str) or not _LABELER_RE.match(v)):
            probs.append(f"{k} 형식이 아닙니다")
    r = doc["reason"]
    if not isinstance(r, str) or not r.strip() or len(r) > REASON_MAX:
        probs.append(f"reason 은 1~{REASON_MAX}자여야 합니다")
    if not isinstance(doc["cancelled_at"], str) or not doc["cancelled_at"]:
        probs.append("cancelled_at 이 없습니다")
    if not isinstance(doc["cancelled_machine4"], str) or not _M4_RE.match(doc["cancelled_machine4"]):
        probs.append("cancelled_machine4 형식이 아닙니다")
    if not isinstance(doc["app_version"], str):
        probs.append("app_version 은 문자열이어야 합니다")
    return probs


def read_cancel(project_root: str, plan_id: str) -> dict | None:
    """취소 기록. 없으면 None, 형식이 틀리면 ValueError."""
    try:
        raw = _read_bytes(cancel_path(project_root, plan_id), CANCEL_MAX_BYTES)
    except FileNotFoundError:
        return None
    try:
        doc = load_json_strict(raw, CANCEL_MAX_BYTES)
    except StrictJsonError as e:
        raise ValueError(f"계획 {plan_id} 취소 기록을 읽지 못했습니다: {e}") from None
    probs = check_cancel(doc)
    if not probs and doc["plan_id"] != plan_id:
        probs = ["파일 이름과 plan_id 가 다릅니다"]
    if probs:
        raise ValueError(f"계획 {plan_id} 취소 기록 형식이 틀렸습니다: {probs[0]}")
    return doc


def is_cancelled(project_root: str, plan_id: str) -> bool:
    """형식이 맞는 취소 기록이 있으면 True. 없거나 깨졌으면 False(깨진 취소는 취소가 아니다 — 보고서는 계속 막힌다).
    취소 기록이 없으면 파일을 열지 않는다(이름만 본다)."""
    if not os.path.exists(cancel_path(project_root, plan_id)):
        return False
    try:
        return read_cancel(project_root, plan_id) is not None
    except (OSError, ValueError):
        return False


def cancel_plan(project_root: str, plan_id: str, *, reason: str, cancelled_by: str, cancelled_machine4: str,
                app_version: str, canceller_kind: str | None, now: datetime | None = None) -> dict:
    """재라벨 계획을 사유와 함께 취소한다(D-12 7, 06 7-1). 쓴 취소 기록 dict 를 돌려준다.

    - canceller_kind: 지금 열린 세션 종류. 조정 세션 또는 None(세션 밖 검토자 도구)만. 그 밖이면 파일을 열기 전에
      RelabelBlindError.
    - 사유 필수(앞뒤 공백을 지운 뒤 1자 이상, 1000자 이하). 아니면 ValueError.
    - 계획 파일이 없으면 FileNotFoundError, 이미 취소됐으면 FileExistsError. 계획 파일은 고치지도 지우지도 않는다.
    - 계획을 읽지 못해도 취소할 수 있다(plan_readable=false, 원 세션·라벨러·종류는 null).
    - canceller_is_labeler: 취소한 사람이 그 계획의 재라벨러 또는 원 세션 라벨러면 true (D-12 3 과 같은 표시).
    """
    if canceller_kind not in CANCEL_KINDS:
        raise RelabelBlindError("재라벨 계획 취소는 조정 세션(검토자)에서만 합니다 (06 7-1)")
    pid = _check_plan_id(plan_id)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("취소 사유를 적어 주세요 (06 7-1)")
    reason = reason.strip()
    if len(reason) > REASON_MAX:
        raise ValueError(f"취소 사유는 {REASON_MAX}자까지입니다")
    if not isinstance(cancelled_by, str) or not _LABELER_RE.match(cancelled_by):
        raise ValueError(f"labeler_id 형식이 아닙니다: {cancelled_by!r}")
    if not isinstance(cancelled_machine4, str) or not _M4_RE.match(cancelled_machine4):
        raise ValueError(f"machine4 형식이 아닙니다: {cancelled_machine4!r}")
    raw = _read_bytes(plan_path(project_root, pid), PLAN_MAX_BYTES)
    if os.path.exists(cancel_path(project_root, pid)):
        raise FileExistsError(f"계획 {pid} 는 이미 취소되었습니다")
    try:
        plan = read_plan(project_root, pid)
    except (OSError, ValueError):
        plan = None
    t = now or datetime.now().astimezone()
    if t.tzinfo is None:
        t = t.astimezone()
    labs = {plan.get("labeler_id"), plan.get("source_labeler_id")} if plan else set()
    doc = {
        "schema": CANCEL_SCHEMA, "schema_version": 1, "plan_id": pid,
        "plan_sha256": hashlib.sha256(raw).hexdigest(), "plan_readable": plan is not None,
        "source_session_id": plan["source_session_id"] if plan else None,
        "session_kind": plan["session_kind"] if plan else None,
        "labeler_id": plan["labeler_id"] if plan else None,
        "reason": reason, "cancelled_at": t.isoformat(timespec="seconds"), "cancelled_by": cancelled_by,
        "cancelled_machine4": cancelled_machine4, "canceller_is_labeler": cancelled_by in labs,
        "app_version": app_version if isinstance(app_version, str) else "",
    }
    probs = check_cancel(doc)
    if probs:
        raise ValueError(f"취소 기록 형식이 틀렸습니다: {probs[0]}")
    _write_new(cancel_path(project_root, pid), _dump(doc), f"계획 {pid} 는 이미 취소되었습니다")
    _refresh_index_quietly(project_root)
    return doc


# ---------------------------------------------------------------------------- 색인 relabel/index.json (D-12 2)
def index_path(project_root: str) -> str:
    return os.path.join(project_root, RELABEL_DIR, INDEX_NAME)


_ENTRY_KEYS = ("plan_id", "source_session_id", "labeler_id", "session_kind", "status")


def check_index(doc) -> list[str]:
    """색인 모양 검사(스키마 $defs.planIndex 와 같은 규칙). 문제 목록."""
    if not isinstance(doc, dict):
        return ["색인이 object 가 아닙니다"]
    if set(doc) != {"schema", "schema_version", "plans"}:
        return ["색인 필드는 schema·schema_version·plans 뿐이어야 합니다"]
    if doc["schema"] != INDEX_SCHEMA or doc["schema_version"] != 1:
        return ["schema 가 chatcoach/relabel-index v1 이 아닙니다"]
    if not isinstance(doc["plans"], list):
        return ["plans 가 배열이 아닙니다"]
    probs: list[str] = []
    seen: set[str] = set()
    for i, e in enumerate(doc["plans"]):
        if not isinstance(e, dict) or set(e) != set(_ENTRY_KEYS):
            probs.append(f"plans[{i}] 필드가 {list(_ENTRY_KEYS)} 가 아닙니다")
            continue
        pid = e["plan_id"]
        if not isinstance(pid, str) or not rp.PLAN_ID_RE.match(pid) or pid in seen:
            probs.append(f"plans[{i}].plan_id 형식이 아니거나 겹칩니다")
            continue
        seen.add(pid)
        if e["status"] not in STATUSES:
            probs.append(f"plans[{i}].status 값이 아닙니다")
        readable = e["status"] == STATUS_ACTIVE     # 취소·읽지 못함은 원 세션 등을 모를 수 있다(null)
        sid, lab, kind = e["source_session_id"], e["labeler_id"], e["session_kind"]
        if readable or sid is not None:
            if not isinstance(sid, str) or not SESSION_ID_RE.match(sid):
                probs.append(f"plans[{i}].source_session_id 형식이 아닙니다")
        if readable or lab is not None:
            if not isinstance(lab, str) or not _LABELER_RE.match(lab):
                probs.append(f"plans[{i}].labeler_id 형식이 아닙니다")
        if readable or kind is not None:
            if kind not in rp.PLAN_KINDS:
                probs.append(f"plans[{i}].session_kind 값이 아닙니다")
    return probs


def read_index(project_root: str) -> dict | None:
    """색인을 읽는다. 없으면 None, 깨졌으면 ValueError."""
    try:
        raw = _read_bytes(index_path(project_root), INDEX_MAX_BYTES)
    except FileNotFoundError:
        return None
    try:
        doc = load_json_strict(raw, INDEX_MAX_BYTES)
    except StrictJsonError as e:
        raise ValueError(f"재라벨 계획 색인을 읽지 못했습니다: {e}") from None
    probs = check_index(doc)
    if probs:
        raise ValueError(f"재라벨 계획 색인 형식이 틀렸습니다: {probs[0]}")
    return doc


def build_index(project_root: str) -> dict:
    """계획 파일·취소 기록을 모두 읽어 색인 dict 를 만든다(쓰지 않는다). 만드는 자리에서만 부른다."""
    plans = []
    for s in list_plans(project_root, include_cancelled=True):
        if s.problem is not None and s.session_kind is None:
            # 읽지 못한 계획: 취소됐으면 cancelled, 아니면 unreadable. 취소 기록에 원 세션 등이 있으면 그것을 쓴다
            c = None
            try:
                c = read_cancel(project_root, s.plan_id)
            except (OSError, ValueError):
                pass
            plans.append({"plan_id": s.plan_id, "source_session_id": (c or {}).get("source_session_id"),
                          "labeler_id": (c or {}).get("labeler_id"), "session_kind": (c or {}).get("session_kind"),
                          "status": STATUS_CANCELLED if c else STATUS_UNREADABLE})
            continue
        plans.append({"plan_id": s.plan_id, "source_session_id": s.source_session_id, "labeler_id": s.labeler_id,
                      "session_kind": s.session_kind, "status": s.status})
    plans.sort(key=lambda e: e["plan_id"])
    return {"schema": INDEX_SCHEMA, "schema_version": 1, "plans": plans}


def _write_replace(path: str, data: bytes) -> None:
    """임시 파일 → fsync → os.replace (원자적 바꿔 넣기). 실패하면 임시 파일을 지운다."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = _write_tmp(d, os.path.basename(path), data)
    try:
        os.replace(tmp, path)
        _fsync_dir(d)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def rebuild_index(project_root: str, *, creator_kind: str | None) -> dict:
    """색인을 다시 만들어 relabel/index.json 에 원자적으로 쓴다. 쓴 색인 dict. 재라벨 세션이면 RelabelBlindError."""
    _guard_creator(creator_kind)
    doc = build_index(project_root)
    probs = check_index(doc)
    if probs:
        raise ValueError(f"색인 형식이 틀렸습니다: {probs[0]}")
    _write_replace(index_path(project_root), _dump(doc))
    return doc


def _refresh_index_quietly(project_root: str) -> None:
    """계획·취소 기록을 쓴 뒤 색인을 다시 쓴다. 실패해도 예외를 올리지 않는다(색인은 파생물, plans_for_labeler 가
    색인에 없는 계획을 보면 다시 만들고, 그것도 못 하면 계획을 열지 않는다)."""
    try:
        rebuild_index(project_root, creator_kind=None)
    except (OSError, ValueError) as e:
        LOG.warning("재라벨 계획 색인을 쓰지 못했습니다: %s", type(e).__name__)


@dataclass(frozen=True)
class IndexedPlans:
    """세션 화면용 계획 목록. plans = 그 라벨러의 쓸 수 있는(취소되지 않은) 계획 요약.

    - from_index: 색인으로 골랐으면 True. 색인을 읽지도 다시 만들지도 못했으면 False — 이때는 계획을 하나도 열지 않고
      plans=() 이며 problem 에 사유를, unreadable 에 계획 파일마다 그 사유를 단다(블라인드 쪽으로 실패).
    - rebuilt: 색인이 없거나·깨졌거나·계획 파일과 맞지 않아 이번에 다시 만들었으면 그 까닭(아니면 None).
    - unindexed: 다시 만든 뒤에도 색인에 없는 계획 파일 수(그사이 새로 생긴 것). 열지 않고 건너뛴다.
    - opened: 색인을 고른 뒤 이 함수가 연 계획 파일 plan_id(= 그 라벨러 자기 계획만, 시험·점검용). 색인 다시 만들기
      (rebuild_index)가 읽은 것은 넣지 않는다.
    - others: 다른 라벨러의 계획 수(색인에서 센 것, 본문은 열지 않음). cancelled: 그 라벨러의 취소된 계획 수.
    - unreadable: 누구 것인지 알 수 없는 읽지 못한 계획(취소되지 않음)의 요약(본문 없이 사유만). 세션 화면이 '읽을 수
      있는 계획 없음' 의 까닭으로 보인다. 색인이 unreadable 로 적은 것만(파일을 열지 않음).
    """
    plans: tuple[PlanSummary, ...]
    from_index: bool
    unindexed: int = 0
    others: int = 0
    cancelled: int = 0
    opened: tuple[str, ...] = ()
    problem: str | None = None
    unreadable: tuple[PlanSummary, ...] = ()
    rebuilt: str | None = None


def _index_problem(project_root: str, names: list[str]) -> tuple[dict | None, str | None]:
    """(색인, 쓸 수 없는 까닭). 없음·깨짐·계획 파일 가운데 색인에 없는 것이 있음이면 까닭을 돌려준다. 계획 파일은 열지 않는다."""
    try:
        doc = read_index(project_root)
    except (OSError, ValueError) as e:
        return None, str(e) if isinstance(e, ValueError) else f"색인을 읽지 못했습니다 ({type(e).__name__})"
    if doc is None:
        return None, "색인이 없습니다" if names else None
    known = {e["plan_id"] for e in doc["plans"]}
    missing = [n for n in names if n not in known]
    if missing:
        return doc, f"색인에 없는 계획 파일 {len(missing)}개"
    return doc, None


def plans_for_labeler(project_root: str, labeler_id: str) -> IndexedPlans:
    """세션 화면이 쓰는 길(D-12 2). 색인을 읽고 **그 라벨러의 계획 파일만** 연다. 예외 없음.

    색인이 없거나·깨졌거나·색인에 없는 계획 파일이 있으면(넷째 묶음에서 만든 프로젝트, 색인 쓰기가 실패한 뒤 등) 그 자리에서
    rebuild_index 로 한 번 다시 만들고 새 색인으로 고른다 — 색인 만들기는 create_plan·cancel_plan 과 같은 유지 작업이고
    다섯 칸만 남긴다. 다음부터는 다시 열지 않는다. 다시 만들기도 실패하면 계획을 하나도 열지 않고(from_index=False)
    재라벨 선택을 막는 사유를 단다(블라인드 쪽으로 실패, D-12 2). 색인에 없는 계획 파일은 열지 않는다.
    취소 여부는 색인 status 와 취소 기록(형식 검사 포함)을 함께 본다 — 어느 쪽이든 취소면 뺀다. 취소 기록도 다른
    라벨러 것이면 열지 않는다.
    """
    names = _plan_names(project_root)
    doc, why = _index_problem(project_root, names)
    rebuilt = None
    if why is not None:
        try:
            doc = rebuild_index(project_root, creator_kind=None)
            rebuilt = why
        except (OSError, ValueError) as e:
            LOG.warning("재라벨 계획 색인을 다시 만들지 못했습니다: %s", type(e).__name__)
            msg = f"재라벨 계획 색인을 읽지 못해 계획을 열지 않았습니다 ({why})"
            bad = tuple(PlanSummary(pid, problem=msg, status=STATUS_UNREADABLE) for pid in names)
            return IndexedPlans((), False, len(names), 0, 0, (), msg, bad, None)
    entries = {e["plan_id"]: e for e in doc["plans"]} if doc is not None else {}
    out: list[PlanSummary] = []
    opened: list[str] = []
    bad: list[PlanSummary] = []
    others = canc = unindexed = 0
    for pid in names:
        e = entries.get(pid)
        if e is None:                          # 다시 만든 뒤에 새로 생긴 계획 — 누구 것인지 몰라 열지 않는다
            unindexed += 1
            continue
        if e["status"] == STATUS_UNREADABLE and e["labeler_id"] is None:
            if not is_cancelled(project_root, pid):     # 누구 것인지 모름 — 열지 않고 사유만 보인다
                bad.append(PlanSummary(pid, problem="색인에 '읽지 못함' 으로 적힌 계획입니다 — 계획 형식이 아닙니다",
                                       status=STATUS_UNREADABLE))
            continue
        if e["labeler_id"] != labeler_id:
            others += 1
            continue
        if e["status"] == STATUS_CANCELLED or is_cancelled(project_root, pid):
            canc += 1
            continue
        s = _summary_of(project_root, pid, False)
        opened.append(pid)
        if s.problem is not None:             # 색인은 내 계획이라는데 파일을 읽지 못함 — 사유를 보이게 남긴다
            out.append(s)
            continue
        if s.labeler_id != labeler_id:        # 색인과 파일이 다름 — 파일을 믿지 않고 이 라벨러 목록에서 뺀다
            others += 1
            continue
        out.append(s)
    return IndexedPlans(tuple(out), True, unindexed, others, canc, tuple(opened), None, tuple(bad), rebuilt)
