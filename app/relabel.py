"""재라벨 계획 파일 relabel/<plan_id>.json 읽기·쓰기 (docs/design/03-data-model.md 6-14·12-1·12-2, 06 3장·7장, 08 D-11 6).

입출력 층이다. Qt 를 import 하지 않는다. 고르기 계산은 app/core/relabel_plan.py 가 하고 여기서는 파일만 다룬다.

- 계획 파일은 한 번만 만든다(이미 있으면 FileExistsError, 덮어쓰지 않는다). 임시 파일에 다 쓰고 fsync →
  하드 링크(못 쓰면 Windows rename)로 '없을 때만' 바꿔 넣는다. 반쯤 쓴 계획 파일은 생기지 않는다.
- 계획을 만드는 쪽(create_plan)은 1차·조정 세션 같은 '만드는 자리' 다. 재라벨 세션(relabel_same·relabel_second)은
  계획을 만들 수 없다: 만들려면 samples/ 와 원 세션 잠금을 읽어야 하는데, 재라벨 세션은 samples/ 를 열지 않는다(03 12-1).
  막힐 때는 디스크에서 무엇도 열기 전에 RelabelBlindError.
- 재라벨 세션이 여는 것은 **자기 계획 파일 하나**(read_plan_for_session)뿐이다. 원 세션의 라벨 파일·samples/ 는
  이 모듈의 어떤 읽기 함수도 열지 않는다(06 7-3). 원 세션 PC 목록은 labels/ 의 **파일 이름만** 본다.
- list_plans 는 세션 선택 화면(app.session.survey_project)이 쓴다. 계획 파일의 요약 칸(누구·어떤 종류·원 세션·
  시작 가능 시각)만 돌려준다. 블록 범위·τ_block 은 돌려주지 않는다.
- 잠금 파일(locks/<session_id>.json)은 locked_at 과 파일 바이트 sha256 만 쓴다(형식 검사는 app/locking.py 가 맡는다).
"""
from __future__ import annotations

import builtins
import hashlib
import json
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

RELABEL_DIR = "relabel"
PLAN_MAX_BYTES = 4 << 20
LOCK_MAX_BYTES = 1 << 20
MANIFEST_MAX_BYTES = 16 << 20
SAMPLE_FILE_RE = re.compile(r"\AS-[0-9a-f]{8}-d[0-9]+-r[0-9]+\.json\Z")
RAND_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
# 계획을 만들 수 있는 세션 종류(만드는 자리). None = 세션 밖(검토자 도구·테스트).
CREATOR_KINDS = (vis.PRIMARY, vis.ADJUDICATION, None)

__all__ = [
    "RELABEL_DIR", "RelabelBlindError", "PlanSummary", "plan_path", "new_plan_id", "list_plans", "read_plan",
    "read_plan_for_session", "write_plan", "next_plan_no", "source_lock_info", "source_machine4s",
    "load_source_manifests", "create_plan", "verify_plan_on_disk", "session_args",
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


# ---------------------------------------------------------------------------- 읽기
@dataclass(frozen=True)
class PlanSummary:
    """세션 선택 화면이 알아야 하는 계획 요약. problem 이 있으면 쓸 수 없는 계획이다(본문 없이 사유만)."""
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

    @property
    def usable(self) -> bool:
        return self.problem is None

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

    재라벨 종류가 아니면 RelabelBlindError(파일을 열기 전에).
    """
    if vis.files_to_open(session_kind).relabel_plans not in (vis.OWN, vis.READ):
        raise RelabelBlindError("이 세션 종류는 재라벨 계획을 열지 않습니다")
    plan = read_plan(project_root, plan_id)
    if session_kind in rp.PLAN_KINDS:
        why = rp.plan_refusal(plan, session_kind, labeler_id)
        if why:
            raise ValueError(f"재라벨 계획 {plan_id}: {why}")
    return plan


def list_plans(project_root: str) -> list[PlanSummary]:
    """relabel/RP-*.json 의 요약(이름 순). 읽지 못한 계획도 problem 을 달아 돌려준다(예외 없음)."""
    d = os.path.join(project_root, RELABEL_DIR)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        if not n.endswith(".json") or not rp.PLAN_ID_RE.match(n[:-5]):
            continue
        pid = n[:-5]
        try:
            p = read_plan(project_root, pid)
        except (OSError, ValueError) as e:
            why = str(e) if isinstance(e, ValueError) else f"읽지 못했습니다 ({type(e).__name__})"
            out.append(PlanSummary(pid, problem=f"{why} — 계획 형식(relabel-plan v1)이 아닙니다"))
            continue
        out.append(PlanSummary(pid, p["session_kind"], p["labeler_id"], p["source_session_id"],
                               p["source_labeler_id"], tuple(p["source_machine4s"]), p["earliest_start_at"],
                               bool(p["enforce_interval"]), bool(p["same_machine_as_source"])))
    return out


def next_plan_no(project_root: str) -> int:
    """지금 계획들의 plan_no 최댓값 + 1 (읽지 못한 계획은 건너뜀, 없으면 1)."""
    best = 0
    for s in list_plans(project_root):
        if s.usable:
            try:
                best = max(best, int(read_plan(project_root, s.plan_id)["plan_no"]))
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


def write_plan(project_root: str, plan: Mapping) -> str:
    """계획을 relabel/<plan_id>.json 으로 만든다(없을 때만). 경로를 돌려준다. 이미 있으면 FileExistsError."""
    probs = rp.check_plan(plan)
    if probs:
        raise ValueError(f"재라벨 계획 형식이 틀렸습니다: {probs[0]}")
    path = plan_path(project_root, plan["plan_id"])
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    if os.path.exists(path):
        raise FileExistsError("같은 plan_id 의 계획이 이미 있습니다 (계획은 고치지 않습니다)")
    data = _dump(plan)
    tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    with builtins.open(tmp, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise
        except (OSError, NotImplementedError, AttributeError):
            if os.name == "nt":
                os.rename(tmp, path)          # 대상이 있으면 FileExistsError
            else:
                from .locking import _write_new_or_remove     # 쓰다가 실패하면 반쯤 쓴 계획 파일을 지운다
                _write_new_or_remove(path, data, FileExistsError("같은 plan_id 의 계획이 이미 있습니다"))
        _fsync_dir(d)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
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


def session_args(plan: Mapping) -> dict:
    """app.session.new_session 에 넘길 값: {"scope": 계획의 블록 범위, "relabel_plan_id": plan_id} (03 12-2)."""
    return {"scope": rp.scope_of(plan), "relabel_plan_id": plan["plan_id"]}
