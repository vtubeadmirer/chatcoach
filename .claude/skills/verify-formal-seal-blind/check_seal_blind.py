"""AI 봉인·해제 블라인드 보장의 형식 검증 (docs/design/06-evaluation-and-agreement.md 7장, app/seal.py).

무엇을 하나
  합성 프로젝트 하나를 임시 폴더에 만들고, 실제 코드(app/seal.py · app/relabel.py · app/locking.py ·
  app/agreement_report.py)의 함수를 '동작' 으로 삼아 **도달할 수 있는 모든 상태**를 너비 우선으로 훑는다
  (명시적 상태 모델 검사 — 모델이 아니라 실제 코드를 디스크 위에서 돌린다). 상태가 같은지는 디스크에서 읽은 추상 상태
  (어느 세션이 열림·잠김, 어느 계획이 있음·취소·읽지 못함, seal.jsonl 의 기록 요약, runs·topics 파일)로 가른다.
  동작마다·새 상태마다 성질을 본다. 06 7-1 해제 조건은 이 파일의 oracle_met(설계 문장에서 따로 쓴 판정)과
  실제 seal.unseal_conditions 를 모든 상태에서 견준다.

모델 넷 (모두 끝까지 훑는다 — 깊이 제한 없음, 동작 묶음이 유한해서 상태가 닫힌다. 곱이 커지지 않게 나눴다)
  gate_plans  : A·B·RS 열기·잠그기, 계획 pR2·pRS 만들기·취소. R1(유효)·Rbad(받을 때 거부)은 처음부터 봉인돼 있고,
                상태마다 seal.unseal(R1)·seal.unseal(Rbad) 를 사유 없이 시도해 본다(시도는 되돌리고 이어 가지 않는다).
  gate_faults : 표본 재현 검증 실패·검증 없는 매니페스트·읽지 못하는 계획 파일 pX(와 그 취소)·계획 ID 없는 옛 잠금. 시도는 같다.
  seal        : 1차 A → 계획 pR2 → 재라벨 B 잠금 사슬 × 봉인·짝 봉인·짝 보충·해제(사유 없음/앞당김). 상태마다 자리 검사.
  rerun       : 사슬이 다 된 상태에서 R1 과 R1b(run_id 가 같은 다른 결과 — 엔진을 다시 돌림)의 봉인·해제.

실행 (저장소 루트, 앱 .venv — 표준 라이브러리만 쓴다)
  .venv\\Scripts\\python .claude\\skills\\verify-formal-seal-blind\\check_seal_blind.py
  옵션: --model <이름>|all (기본 all) · --mutants (변이 자가 시험: 일부러 깨뜨린 코드·모델을 잡는지) · --keep (임시 폴더 남김)
        · --no-watchdog (감시 부모 없이 — 디버깅)
  검사는 자식 프로세스에서 돈다. 60초 동안 맥박이 없으면 부모가 끝내고 '멈춤' 과 종료 2 (supervise 참고).
종료 코드: 0 모든 성질 통과 · 1 반례 있음(또는 --mutants 에서 못 잡은 변이) · 2 준비 실패·멈춤

지키는 것
  - 실제 자료(chatcoach-data\\projects\\pilot-* · …\\ai\\pilot-*)를 열지 않는다. 임시 폴더는 %LOCALAPPDATA%\\ChatCoachSmoke\\pytest
    아래에 만들고 끝나면 지운다. 채팅 본문은 합성 표식 한 줄뿐이다.
  - 제품 코드를 고치지 않는다. 속도를 위해 이 프로세스 안에서만 os.fsync 를 아무것도 안 하게 바꾼다(내구성은 성질과 무관).
"""
from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from app import agreement_report as ar  # noqa: E402
from app import jsonl, locking, paths, project, relabel, seal  # noqa: E402
from app.core import visibility as vis  # noqa: E402

_REAL_OPEN = builtins.open
_REAL_OS_OPEN = os.open
_REAL_LISTDIR = os.listdir
_REAL_SCANDIR = os.scandir
os.fsync = lambda fd: None          # 이 프로세스 안에서만. 성질과 무관한 디스크 내구성 대기를 없앤다

# ----------------------------------------------------------------------------- 합성 세계
KST = timezone(timedelta(hours=9))
BASE = datetime(2026, 10, 21, 9, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-555555555555"
SALT = bytes(range(32))
MID = "aaaaaaaa-0000-4000-8000-000000000001"
M4 = project.machine4_of(MID)
SID = {"A": "L01-P-20261021-ab12", "B": "L02-R2-20261021-cd34", "RS": "L01-RS-20261024-ef56"}
SID_NAME = {v: k for k, v in SID.items()}
PLAN_RAND = {"pR2": "aa01", "pRS": "aa02"}
GARBAGE_PLAN_ID = "RP-20261021-zz99"
PLAN_SUFFIX_NAME = {"aa01": "pR2", "aa02": "pRS", "zz99": "pX"}
RUN_ID = "run-formal-01"
REAL_DATA = os.path.normcase(os.path.abspath(os.path.join(os.path.dirname(REPO), "chatcoach-data")))
KIND_OF_CODE = {"P": vis.PRIMARY, "RS": vis.RELABEL_SAME, "R2": vis.RELABEL_SECOND}
RELABEL_KINDS = (vis.RELABEL_SAME, vis.RELABEL_SECOND)
LABELING_KINDS = (vis.PRIMARY, vis.RELABEL_SAME, vis.RELABEL_SECOND)
BLIND_KINDS = (vis.PRIMARY, vis.RELABEL_SAME, vis.RELABEL_SECOND, vis.ORIGIN_REVIEW)


def cid(i: int) -> str:
    return "c" + f"{i:020x}"


def _jb(d) -> bytes:
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def result_doc(engine_version: str, video_no: str = "777") -> bytes:
    return _jb({"schema": "chatcoach/ai-result", "schema_version": 1, "run_id": RUN_ID,
                "created_at": "2026-10-21T08:00:00+09:00",
                "engine": {"name": "synthengine-formal", "version": engine_version, "preregistered": True},
                "target": {"platform": "chzzk", "video_no": video_no, "chat_id_algorithm": "sl-chat-id-v1"},
                "time_axis": "vod_ms",
                "reactions": [{"chat_id": cid(1), "result": "graded", "grade": "likely", "reaction_time_ms": 3000}]})


def topics_doc(created_at: str) -> bytes:
    return _jb({"schema": "chatcoach/topic-flow", "schema_version": 1, "run_id": RUN_ID, "created_at": created_at,
                "engine": {"name": "synthtopics", "version": "0.0.1"}, "target": {"video_no": "777"},
                "time_axis": "vod_ms", "window_ms": 60000, "range": {"start_ms": 0, "end_ms": 0}, "blocks": []})


DOCS = {"R1": result_doc("0.0.1"), "R1b": result_doc("0.0.2"), "Rbad": result_doc("0.0.1", video_no="778"),
        "T1": topics_doc("2026-10-21T08:00:00+09:00"), "T2": topics_doc("2026-10-21T08:30:00+09:00")}
SHA = {k: hashlib.sha256(v).hexdigest() for k, v in DOCS.items()}
SHA_NAME = {v: k for k, v in SHA.items()}


def kind_of(sid: str) -> str | None:
    parts = sid.split("-")
    return KIND_OF_CODE.get(parts[1]) if len(parts) == 4 else None


def pname(plan_id) -> str | None:
    if not isinstance(plan_id, str):
        return None
    return PLAN_SUFFIX_NAME.get(plan_id.rsplit("-", 1)[-1], plan_id)


# ----------------------------------------------------------------------------- 디스크 스냅숏
@dataclass
class Snap:
    files: dict
    dirs: frozenset


class _AllDirty:
    """snapshot(ALL_DIRTY): 감시 밖에서 쓴 뒤(처음 상태를 만들 때) 모든 파일을 다시 읽는다."""

    def __contains__(self, _x) -> bool:
        return True


ALL_DIRTY = _AllDirty()


class World:
    """임시 폴더 하나 = 세계. <base>\\chatcoach-data\\projects\\<slug> (프로젝트) + <base>\\chatcoach-data\\ai\\<PID> (봉인)."""

    def __init__(self, keep: bool = False):
        lad = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        parent = os.path.join(lad, "ChatCoachSmoke", "pytest")
        os.makedirs(parent, exist_ok=True)
        self.base = tempfile.mkdtemp(prefix="formal-seal-", dir=parent)
        nb = os.path.normcase(os.path.abspath(self.base))
        if nb.startswith(REAL_DATA) or nb.startswith(os.path.normcase(REPO)):
            raise SystemExit("임시 폴더가 실제 자료 폴더나 저장소 안입니다 — 멈춥니다")
        self.keep = keep
        self.top = os.path.join(self.base, "chatcoach-data")
        paths.set_data_root(os.path.join(self.base, "data"))
        proj = project.create(os.path.join(self.top, "projects"), "합성 방송", 777, "시험용", "L01", "0.0.1",
                              BASE - timedelta(hours=2), PID, SALT,
                              guide=project.GuideSource("0.1", b"# guide\n", {"segment": {"boundary_buffer_sec": 10}}),
                              machine4="m3fa")
        self.root = proj.root
        self.data = proj.data
        jsonl.write_all(os.path.join(self.root, "chat", "messages.jsonl"),
                        [{"chat_id": cid(1), "t_ms": 1000, "text": "합성본문표식", "nickname_masked": "합성닉네임표식"}])
        self.ai_dir = os.path.join(seal.default_ai_root(self.root), PID)
        self.rel_root = os.path.relpath(self.root, self.top)
        self.rel_ai = os.path.relpath(self.ai_dir, self.top)
        self._cache: dict = {}
        self._static = None
        self._mut_abs_raw = [os.path.join(self.root, "labels"), os.path.join(self.root, "locks"),
                             os.path.join(self.root, "relabel"), seal.default_ai_root(self.root)]
        self._mut_abs = [os.path.normcase(os.path.abspath(p)) for p in self._mut_abs_raw]

    def close(self):
        paths.set_data_root(None)
        if not self.keep:
            shutil.rmtree(self.base, ignore_errors=True)

    def allowed_write(self, group: str, d: str) -> bool:
        """동작 갈래마다 써도 되는 곳: 세션 동작은 labels\\·locks\\·relabel\\, 봉인·해제는 data\\ai\\ 아래(프로젝트 밖, D-15 8)."""
        roots = self._mut_abs[:3] if group == "session" else self._mut_abs[3:]
        d = os.path.normcase(os.path.abspath(d))
        return any(d == r or d.startswith(r + os.sep) for r in roots)

    def snapshot(self, dirty: set | frozenset = frozenset()) -> Snap:
        """세계의 파일 바이트. 처음 한 번 전체를 읽고, 그 뒤로는 바뀔 수 있는 곳(labels\\·locks\\·relabel\\·data\\ai\\)만 다시 훑는다
        — 그 밖에 쓰면 check_transition 이 '쓰는 곳' 위반으로 잡는다. (크기, mtime) 가 같고 그 폴더에 쓰기가 없었던 파일은
        앞서 읽은 바이트를 쓴다. dirty = 감시(Spy)가 쓰기를 본 폴더 — 그 폴더의 파일은 늘 다시 읽는다(mtime 눈금이 거칠어도 안전).
        이름의 대소문자는 그대로 둔다 — 되살릴 때 소문자로 쓰면 세션 ID 정규식이 잠금 파일을 못 알아본다."""
        if self._static is None:
            full = self._walk([self.top], dirty)
            mut = [os.path.relpath(r, self.top) for r in self._mut_abs_raw]
            ism = (lambda p: any(p == m or p.startswith(m + os.sep) for m in mut))
            self._static = ({p: b for p, b in full.files.items() if not ism(os.path.dirname(p))},
                            {d for d in full.dirs if not ism(d)})
            return full
        part = self._walk([r for r in self._mut_abs_raw if os.path.isdir(r)], dirty)
        files = dict(self._static[0])
        files.update(part.files)
        return Snap(files, frozenset(self._static[1] | part.dirs))

    def _walk(self, starts, dirty) -> Snap:
        files, dirs = {}, set()
        stack = list(starts)
        while stack:
            dp = stack.pop()
            rel = os.path.relpath(dp, self.top)
            dirs.add(rel)
            force = os.path.normcase(os.path.abspath(dp)) in dirty
            with _REAL_SCANDIR(dp) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                        continue
                    st = e.stat(follow_symlinks=False)
                    sig = (st.st_size, st.st_mtime_ns)
                    p = os.path.join(rel, e.name)
                    c = self._cache.get(p)
                    if force or c is None or c[0] != sig:
                        with _REAL_OPEN(e.path, "rb") as f:
                            b = f.read()
                        self._cache[p] = (sig, b)
                    else:
                        b = c[1]
                    files[p] = b
        return Snap(files, frozenset(dirs))

    def materialize(self, target: Snap, current: Snap) -> None:
        for p in current.files:
            if p not in target.files:
                os.remove(os.path.join(self.top, p))
                self._cache.pop(p, None)
        for d in sorted(target.dirs - current.dirs, key=len):
            os.makedirs(os.path.join(self.top, d), exist_ok=True)
        for p, b in target.files.items():
            if current.files.get(p) != b:
                full = os.path.join(self.top, p)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with _REAL_OPEN(full, "wb") as f:
                    f.write(b)
                st = os.stat(full)
                self._cache[p] = ((st.st_size, st.st_mtime_ns), b)
        # 빈 폴더는 지우지 않는다. 코드는 빈 labels\·locks\·relabel\·runs\ 와 없는 폴더를 똑같이 다룬다(listdir 실패와
        # 빈 목록 모두 '없음'). 그리고 이 PC 에서는 폴더를 지웠다 곧바로 다시 만드는 일이 되풀이되면 보안 프로그램이
        # 프로세스를 멈춰 세웠다(2026-10-23, relabel\ 을 몇 ms 사이에 세 번 지우고 만들 때).


# ----------------------------------------------------------------------------- 추상 상태 (디스크에서 읽은 사실)
def _parse_dt(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


@dataclass
class Facts:
    labels: set = field(default_factory=set)
    locks: dict = field(default_factory=dict)     # sid → {plan, verify, locked_at}
    plans: dict = field(default_factory=dict)     # plan_id → {readable, source, kind}
    cancelled: set = field(default_factory=set)
    recs: list = field(default_factory=list)      # seal.jsonl 기록(파싱 실패 줄은 {"_corrupt": True})
    seal_raw: bytes = b""
    runs: dict = field(default_factory=dict)      # 이름 → 바이트
    topics_files: dict = field(default_factory=dict)
    bins: dict = field(default_factory=dict)

    # --- seal 쪽 도우미
    def sealed(self, name):
        return [r for r in self.recs if r.get("kind") == seal.K_SEALED and r.get("sha256") == SHA[name]]

    def unsealed(self, name):
        return [r for r in self.recs if r.get("kind") == seal.K_UNSEALED and r.get("sha256") == SHA[name]]

    def first_primary_lock_at(self):
        ts = [_parse_dt(d["locked_at"]) for s, d in self.locks.items() if kind_of(s) == vis.PRIMARY]
        ts = [t for t in ts if t is not None]
        return min(ts) if ts else None

    def presealed(self, name):
        s = self.sealed(name)
        if not s:
            return None
        first = self.first_primary_lock_at()
        at = _parse_dt(s[0].get("at"))
        return True if first is None else (at is not None and at < first)

    def key(self):
        def rsum(r):
            k = r.get("kind")
            if r.get("_corrupt"):
                return ("corrupt",)
            n = SHA_NAME.get(r.get("sha256"), r.get("sha256"))
            if k == seal.K_SEALED:
                return ("sealed", n, r.get("check"))
            if k == seal.K_UNSEALED:
                return ("unsealed", n, r.get("run_id"), r.get("conditions_met"), r.get("reason") is not None,
                        isinstance(r.get("topics"), dict))
            return (k, n, SHA_NAME.get(r.get("pair"), r.get("pair")), r.get("check"), bool(r.get("attached")))
        return (tuple(sorted(self.labels)),
                tuple(sorted((s, pname(d["plan"]), d["verify"]) for s, d in self.locks.items())),
                tuple(sorted((pname(p), d["readable"], d["source"], p in self.cancelled) for p, d in self.plans.items())),
                tuple(sorted(rsum(r) for r in self.recs)),
                tuple(sorted(self.runs)), tuple(sorted(self.topics_files)),
                ("R1_presealed", self.presealed("R1")))

    def describe(self) -> str:
        lk = ", ".join(f"{SID_NAME.get(s, s)}(계획 {pname(d['plan'])}, 검증 {d['verify']})" for s, d in sorted(self.locks.items()))
        pl = ", ".join(f"{pname(p)}({'읽음' if d['readable'] else '읽지 못함'}{', 취소' if p in self.cancelled else ''})"
                       for p, d in sorted(self.plans.items()))
        rs = "; ".join(" ".join(str(x) for x in r) for r in self.key()[3])
        return (f"  labels/: {', '.join(SID_NAME.get(s, s) for s in sorted(self.labels)) or '없음'}\n"
                f"  locks/: {lk or '없음'}\n  relabel/: {pl or '없음'}\n  seal.jsonl: {rs or '없음'}\n"
                f"  runs/: {', '.join(sorted(self.runs)) or '없음'} · topics/: {', '.join(sorted(self.topics_files)) or '없음'}")


def facts_of(world: World, snap: Snap) -> Facts:
    f = Facts()
    rr = os.path.normcase(world.rel_root)
    ra = os.path.normcase(world.rel_ai)
    for p, b in snap.files.items():
        d, n = os.path.split(p)
        d = os.path.normcase(d)
        if d == os.path.join(rr, "labels"):
            m = n.split(".")
            if len(m) == 3 and m[2] == "jsonl":
                f.labels.add(m[0])
        elif d == os.path.join(rr, "locks") and n.endswith(".json"):
            sid = n[:-5]
            try:
                doc = json.loads(b.decode("utf-8"))
                ver = doc.get("sample_verify") or []
                if any(not isinstance(v, dict) or v.get("ok") is not True for v in ver):
                    verify = "verify_failed"
                elif doc.get("sample_manifests") and not ver:
                    verify = "manifest_unverified"
                else:
                    verify = "ok"
                f.locks[sid] = {"plan": doc.get("relabel_plan_id"), "verify": verify, "locked_at": doc.get("locked_at")}
            except (ValueError, UnicodeDecodeError, AttributeError):
                f.locks[sid] = {"plan": None, "verify": "corrupt", "locked_at": None}
        elif d == os.path.join(rr, "relabel") and n.startswith("RP-"):
            if n.endswith(".cancel.json"):
                pid = n[:-len(".cancel.json")]
                try:
                    doc = json.loads(b.decode("utf-8"))
                    if doc.get("plan_id") == pid and isinstance(doc.get("reason"), str) and doc["reason"].strip():
                        f.cancelled.add(pid)
                except (ValueError, UnicodeDecodeError, AttributeError):
                    pass
            elif n.endswith(".json"):
                pid = n[:-5]
                try:
                    doc = json.loads(b.decode("utf-8"))
                    ok = doc.get("plan_id") == pid and isinstance(doc.get("source_session_id"), str)
                    f.plans[pid] = {"readable": ok, "source": doc.get("source_session_id") if ok else None,
                                    "kind": doc.get("session_kind") if ok else None}
                except (ValueError, UnicodeDecodeError, AttributeError):
                    f.plans[pid] = {"readable": False, "source": None, "kind": None}
        elif d == ra and n == seal.SEAL_FILE:
            f.seal_raw = b
            for line in b.decode("utf-8", "replace").splitlines():
                if not line.strip():
                    continue
                try:
                    f.recs.append(json.loads(line))
                except ValueError:
                    f.recs.append({"_corrupt": True})
        elif d == os.path.join(ra, seal.RUNS_DIR):
            f.runs[n] = b
        elif d == os.path.join(ra, seal.TOPICS_DIR):
            f.topics_files[n] = b
        elif d == os.path.join(ra, seal.SEALED_DIR):
            f.bins[n] = b
    return f


# ----------------------------------------------------------------------------- 명세: 06 7-1 해제 조건 (oracle)
ORACLE_RULES = {"unlocked", "no_primary", "no_plan", "plan_unreadable", "plan_pending", "session_plan_unreadable",
                "sample_verify"}


def oracle_met(f: Facts, rules=None) -> tuple[bool, list[str]]:
    """06 7-1 과 seal 머리말·comparison_gate 문서에서 따로 쓴 판정. 코드를 부르지 않는다.
    - 라벨링 세션(1차·재라벨: labels/ 파일 이름이나 locks/ 에 있는 것)이 모두 잠김
    - 1차 세션이 하나 이상, 1차마다 그것을 원 세션으로 하는 (읽을 수 있는) 계획이 하나 이상(취소된 계획도 '계획 있음')
    - 취소되지 않은 계획마다: 읽을 수 있어야 하고(읽지 못하면 막음), 원 세션이 1차면 그 계획 ID 를 잠금에 적은 잠긴
      재라벨 세션이 있어야 함(취소된 계획은 세지 않음)
    - 잠긴 재라벨 세션이 적은 계획은 (취소됐어도) 읽을 수 있어야 함
    - 잠긴 라벨링 세션의 표본 재현 검증(sample_verify) 이 모두 ok, 매니페스트가 있으면 검증이 비어 있지 않음
    rules: 변이 자가 시험에서 규칙 하나를 뺀 '틀린 모델' 을 만들 때만 준다."""
    rules = ORACLE_RULES if rules is None else rules
    why: list[str] = []
    labeling = {s for s in f.labels | set(f.locks) if kind_of(s) in LABELING_KINDS}
    if "unlocked" in rules:
        why += [f"잠기지 않은 라벨링 세션 {SID_NAME.get(s, s)}" for s in sorted(labeling) if s not in f.locks]
    primaries = sorted(s for s in labeling if kind_of(s) == vis.PRIMARY)
    if "no_primary" in rules and not primaries:
        why.append("1차 세션 없음")
    readable = {p: d for p, d in f.plans.items() if d["readable"]}
    if "no_plan" in rules:
        why += [f"재라벨 계획이 없는 1차 {SID_NAME.get(p, p)}" for p in primaries
                if not any(d["source"] == p for d in readable.values())]
    refs = {d["plan"] for s, d in f.locks.items() if kind_of(s) in RELABEL_KINDS and isinstance(d["plan"], str)}
    for p, d in sorted(f.plans.items()):
        if p in f.cancelled:
            continue
        if not d["readable"]:
            if "plan_unreadable" in rules:
                why.append(f"읽지 못한 계획 {pname(p)}")
            continue
        if "plan_pending" in rules and d["source"] in primaries and p not in refs:
            why.append(f"잠긴 재라벨 세션이 없는 계획 {pname(p)}")
    if "session_plan_unreadable" in rules:
        why += [f"{SID_NAME.get(s, s)} 의 계획 {pname(d['plan'])} 을 읽지 못함" for s, d in sorted(f.locks.items())
                if kind_of(s) in RELABEL_KINDS and isinstance(d["plan"], str)
                and not (d["plan"] in f.plans and f.plans[d["plan"]]["readable"])]
    if "sample_verify" in rules:
        why += [f"{SID_NAME.get(s, s)} 표본 재현 검증 {d['verify']}" for s, d in sorted(f.locks.items())
                if kind_of(s) in LABELING_KINDS and d["verify"] != "ok"]
    return (not why), why


# ----------------------------------------------------------------------------- 열기 감시
_MUTATORS = ("replace", "rename", "link", "remove", "unlink", "rmdir", "mkdir")


class Spy:
    """builtins.open · os.open · os.listdir · os.scandir 와 디스크를 바꾸는 os 함수(replace·rename·link·remove·unlink·
    rmdir·mkdir) 호출을 적는다(막지 않는다). 겹쳐 쓸 수 있다(바깥 감시도 함께 적는다). 감시하는 코드(app/seal.py·
    relabel.py·locking.py·jsonl.py·project.py)는 이 함수들로만 쓴다(pathlib·shutil 쓰기 없음 — 2026-10-23 확인)."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def __enter__(self):
        calls = self.calls
        self._prev = prev = (builtins.open, os.open, os.listdir, os.scandir)
        self._prev_mut = {n: getattr(os, n) for n in _MUTATORS}

        def _p(x):
            try:
                return os.path.normcase(os.path.abspath(os.fspath(x)))
            except TypeError:
                return ""

        def sp_open(file, mode="r", *a, **kw):
            calls.append(("open", _p(file), mode))
            return prev[0](file, mode, *a, **kw)

        def sp_os_open(path, flags, *a, **kw):
            calls.append(("os.open", _p(path), str(flags)))
            return prev[1](path, flags, *a, **kw)

        def sp_listdir(path="."):
            calls.append(("listdir", _p(path), ""))
            return prev[2](path)

        def sp_scandir(path="."):
            calls.append(("scandir", _p(path), ""))
            return prev[3](path)

        def mut(name, real):
            n_paths = 2 if name in ("replace", "rename", "link") else 1

            def f(*a, **kw):
                r = real(*a, **kw)                 # 실패한 호출(이미 있는 폴더의 mkdir 등)은 디스크를 바꾸지 않았다
                for x in a[:n_paths]:
                    if isinstance(x, (str, bytes, os.PathLike)):
                        calls.append(("mutate", _p(x), name))
                return r
            return f

        builtins.open, os.open, os.listdir, os.scandir = sp_open, sp_os_open, sp_listdir, sp_scandir
        for n, real in self._prev_mut.items():
            setattr(os, n, mut(n, real))
        return self

    def __exit__(self, *exc):
        builtins.open, os.open, os.listdir, os.scandir = self._prev
        for n, real in self._prev_mut.items():
            setattr(os, n, real)
        return False

    def dirty_dirs(self) -> set:
        """디스크를 바꾼 곳의 폴더(정규화 경로): 쓰기로 연 파일·os.replace 등의 대상. 원자적 쓰기는 같은 폴더의 임시 파일을
        거치므로 최종 파일 폴더도 잡힌다. mkdir·rmdir 은 그 폴더의 부모를 적는다."""
        wflags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND
        out = set()
        for k, p, m in self.calls:
            if k == "mutate" and m in ("mkdir", "rmdir"):
                out.add(p)
            elif (k == "open" and any(c in m for c in "wxa+")) or (k == "os.open" and int(m) & wflags) or k == "mutate":
                out.add(os.path.dirname(p))
        return out

    def mutated(self) -> bool:
        return bool(self.dirty_dirs())

    def reads_under(self, d: str) -> list[str]:
        d = os.path.normcase(os.path.abspath(d)) + os.sep
        return [p for k, p, m in self.calls
                if p.startswith(d) and (k in ("listdir", "scandir") or (k == "open" and not any(c in m for c in "wxa")))]


# ----------------------------------------------------------------------------- 동작
SKIP = object()


@dataclass
class Ctx:
    world: World
    now: datetime
    facts: Facts


@dataclass
class Action:
    name: str
    text: str
    fn: object
    group: str = "session"          # session · seal · unseal
    expand: bool = True             # False = 시도만 하고 되돌림(그 상태를 이어 가지 않는다)
    early: bool = False
    target: str | None = None       # 봉인 이름(R1·R1b·Rbad)


def _open_session(name):
    def fn(c: Ctx):
        sid = SID[name]
        if sid in c.facts.labels:
            return SKIP
        p = os.path.join(c.world.root, "labels", f"{sid}.{M4}.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        jsonl.write_all(p, [{"v": 1, "id": "00000000-0000-4000-8000-0000000000aa", "seq": 1, "kind": "session.open",
                             "session_id": sid, "labeler_id": sid.split("-")[0],
                             "created_at": c.now.isoformat(timespec="seconds"), "app_version": "0.0.1",
                             "body": {"project_id": PID, "session_kind": kind_of(sid)}}])
        return "열림"
    return fn


def _lock_session(name, *, plan: str | None = None, verify: str = "ok"):
    def fn(c: Ctx):
        sid = SID[name]
        if sid not in c.facts.labels:
            return SKIP                                  # 열지 않은 세션은 잠그지 않는다(앱도 열린 세션만 잠근다)
        pid = None
        if plan is not None:
            pid = next((p for p in c.facts.plans if pname(p) == plan), None)
            if pid is None:
                return SKIP                              # 재라벨 세션은 계획으로 연다 — 계획 파일이 없으면 이 잠금은 없다
        doc = {"session_id": sid, "project_id": PID, "labeler_id": sid.split("-")[0], "session_kind": kind_of(sid),
               "locked_at": c.now.isoformat(timespec="seconds"), "locked_by": sid.split("-")[0],
               "app_version": "0.0.1", "relabel_plan_id": pid, "files": [], "label_counts": {},
               "sample_manifests": [], "sample_verify": []}
        if verify == "verify_failed":
            doc["sample_verify"] = [{"sample_set_id": "S-00000001-d1-r1", "ok": False, "problems": 1}]
        elif verify == "manifest_unverified":
            doc["sample_manifests"] = [{"sample_set_id": "S-00000001-d1-r1", "sha256": "ab" * 32}]
        locking.write_lock_exclusive(locking.lock_path(c.world.root, sid), locking.dump_lock(doc))
        return "잠김"
    return fn


def _create_plan(plan, kind, labeler):
    def fn(c: Ctx):
        p = relabel.create_plan(c.world.root, c.world.data, session_kind=kind, labeler_id=labeler,
                                source_session_id=SID["A"], done_ranges=[(0, 600_000)], created_by="L01",
                                created_machine4="aaaa", app_version="0.0.1", creator_kind=None, now=c.now,
                                rand4=PLAN_RAND[plan])
        return p["plan_id"]
    return fn


def _garbage_plan(c: Ctx):
    p = relabel.plan_path(c.world.root, GARBAGE_PLAN_ID)
    if os.path.exists(p):
        return SKIP
    # 중간에 잘린 JSON(찢어진 쓰기) → read_plan 이 ValueError, list_plans 가 problem(읽지 못함)으로 돌려준다.
    # 쓰기는 진짜 계획 파일과 같은 길(relabel._write_new: 임시 파일 → 하드 링크)로 한다 — 감시(Spy)를 거치므로 탐색이 본다.
    relabel._write_new(p, b'{"schema": "chatcoach/relabel-plan", "plan_id": "RP-2026', "이미 있음")
    return "깨진 계획 파일"


def _cancel(plan):
    def fn(c: Ctx):
        pid = next((p for p in c.facts.plans if pname(p) == plan), None)
        if pid is None:
            pid = GARBAGE_PLAN_ID if plan == "pX" else f"RP-{c.now:%Y%m%d}-{PLAN_RAND[plan]}"
        relabel.cancel_plan(c.world.root, pid, reason="합성 취소 사유", cancelled_by="L01", cancelled_machine4="aaaa",
                            app_version="0.0.1", canceller_kind=None, now=c.now)
        return "취소됨"
    return fn


def _seal(name, source="engine_link"):
    def fn(c: Ctx):
        e = seal.seal_bytes(c.world.root, DOCS[name], source=source, by="L01", now=c.now)
        return f"new={e.new} check={e.check}"
    return fn


def _seal_topics(c: Ctx):
    e = seal.seal_topics_bytes(c.world.root, DOCS["T1"], pair_sha256=SHA["R1"], by="L01", source="engine_topics",
                               now=c.now)
    return f"new={e.new} check={e.check}"


def _attach(c: Ctx):
    r = seal.attach_pairs(c.world.root, SHA["R1"], {"topics": DOCS["T2"]}, by="L01", source="engine", now=c.now)
    return r


def _unseal(name, early=False):
    def fn(c: Ctx):
        return seal.unseal(c.world.root, SHA[name], by="L01", now=c.now,
                           early_reason="합성 앞당김 사유" if early else None)
    return fn


A = Action
SESSION_ACTIONS = {
    "open_A": A("open_A", "1차 세션 A(L01-P-…ab12)를 연다 — labels/<A>.<m4>.jsonl 이 생긴다", _open_session("A")),
    "open_B": A("open_B", "재라벨 세션 B(L02-R2-…cd34)를 연다 — labels/<B>.<m4>.jsonl", _open_session("B")),
    "open_RS": A("open_RS", "같은 사람 재라벨 세션 RS(L01-RS-…ef56)를 연다", _open_session("RS")),
    "lock_A_ok": A("lock_A_ok", "A 를 잠근다(locking.write_lock_exclusive) — 표본 재현 검증 통과", _lock_session("A")),
    "lock_A_verify_failed": A("lock_A_verify_failed", "A 를 잠근다 — 잠금의 sample_verify 에 ok=false",
                              _lock_session("A", verify="verify_failed")),
    "lock_A_manifest_unverified": A("lock_A_manifest_unverified", "A 를 잠근다 — sample_manifests 는 있는데 sample_verify 가 빔",
                                    _lock_session("A", verify="manifest_unverified")),
    "plan_R2": A("plan_R2", "relabel.create_plan — A 를 원 세션으로 두 번째 사람(L02) 계획 pR2(RP-…-aa01)",
                 _create_plan("pR2", vis.RELABEL_SECOND, "L02")),
    "plan_RS": A("plan_RS", "relabel.create_plan — A 를 원 세션으로 같은 사람(L01) 계획 pRS(RP-…-aa02)",
                 _create_plan("pRS", vis.RELABEL_SAME, "L01")),
    "plan_garbage": A("plan_garbage", "relabel/RP-20261021-zz99.json 이 읽을 수 없는 파일로 생긴다(손상 pX)", _garbage_plan),
    "lock_B_plan": A("lock_B_plan", "B 를 잠근다 — 잠금의 relabel_plan_id = pR2", _lock_session("B", plan="pR2")),
    "lock_B_noplan": A("lock_B_noplan", "B 를 잠근다 — 잠금에 relabel_plan_id 가 없음(옛 형식, 경고만)",
                       _lock_session("B")),
    "lock_B_verify_failed": A("lock_B_verify_failed", "B 를 잠근다 — relabel_plan_id = pR2, sample_verify 에 ok=false",
                              _lock_session("B", plan="pR2", verify="verify_failed")),
    "lock_RS_plan": A("lock_RS_plan", "RS 를 잠근다 — 잠금의 relabel_plan_id = pRS", _lock_session("RS", plan="pRS")),
    "cancel_pR2": A("cancel_pR2", "relabel.cancel_plan(pR2, 사유) — relabel/<pR2>.cancel.json", _cancel("pR2")),
    "cancel_pRS": A("cancel_pRS", "relabel.cancel_plan(pRS, 사유)", _cancel("pRS")),
    "cancel_pX": A("cancel_pX", "relabel.cancel_plan(pX — 읽지 못하는 계획, 사유)", _cancel("pX")),
}
SEAL_ACTIONS = {
    "seal_R1": A("seal_R1", "seal.seal_bytes(판정 R1, run_id=run-formal-01)", _seal("R1"), "seal", target="R1"),
    "seal_R1b": A("seal_R1b", "seal.seal_bytes(R1b — 같은 run_id, 엔진을 다시 돌린 다른 바이트)", _seal("R1b"), "seal",
                  target="R1b"),
    "seal_T1": A("seal_T1", "seal.seal_topics_bytes(주제 흐름 T1, pair=R1)", _seal_topics, "seal", target="R1"),
    "attach_T2": A("attach_T2", "seal.attach_pairs(R1, {topics: T2}) — 짝 보충", _attach, "seal", target="R1"),
    "unseal_R1": A("unseal_R1", "seal.unseal(R1) — 사유 없이", _unseal("R1"), "unseal", target="R1"),
    "unseal_R1_early": A("unseal_R1_early", "seal.unseal(R1, early_reason=…) — 앞당겨 열기", _unseal("R1", True), "unseal",
                         early=True, target="R1"),
    "unseal_R1b": A("unseal_R1b", "seal.unseal(R1b) — 사유 없이", _unseal("R1b"), "unseal", target="R1b"),
    "unseal_R1b_early": A("unseal_R1b_early", "seal.unseal(R1b, early_reason=…)", _unseal("R1b", True), "unseal",
                          early=True, target="R1b"),
}
PROBES = {
    "probe_unseal_R1": A("probe_unseal_R1", "seal.unseal(R1) — 사유 없이 (시도만)", _unseal("R1"), "unseal",
                         expand=False, target="R1"),
    "probe_unseal_Rbad": A("probe_unseal_Rbad", "seal.unseal(Rbad — 받을 때 거부된 결과) (시도만)", _unseal("Rbad"),
                           "unseal", expand=False, target="Rbad"),
}

EXPECTED_SESSION_ERRORS = (ValueError, FileExistsError, FileNotFoundError, locking.AlreadyLockedError)


@dataclass
class Model:
    name: str
    actions: list
    seed: list          # 처음 상태를 만드는 동작(검사 없이 차례로)
    blind_checks: bool


def _acts(*names):
    return [SESSION_ACTIONS[n] if n in SESSION_ACTIONS else SEAL_ACTIONS[n] for n in names]


CHAIN_NAMES = ("open_A", "lock_A_ok", "plan_R2", "open_B", "lock_B_plan")
GATE_SEED = [SEAL_ACTIONS["seal_R1"].fn, _seal("Rbad", "file")]
MODELS = {
    # 해제 관문 1 — 계획 둘(pR2·pRS)과 재라벨 세션 둘(B·RS): 모든 순서의 열기·잠그기·계획·취소. 상태마다 unseal 을 시도만 한다.
    "gate_plans": Model("gate_plans", _acts("open_A", "lock_A_ok", "plan_R2", "plan_RS", "open_B", "open_RS",
                                            "lock_B_plan", "lock_RS_plan", "cancel_pR2", "cancel_pRS")
                        + list(PROBES.values()), seed=GATE_SEED, blind_checks=False),
    # 해제 관문 2 — 고장: 표본 재현 검증 실패·검증 없는 매니페스트·읽지 못하는 계획 파일(과 그 취소)·계획 ID 없는 옛 잠금.
    "gate_faults": Model("gate_faults", _acts("open_A", "lock_A_ok", "lock_A_verify_failed", "lock_A_manifest_unverified",
                                              "plan_R2", "plan_garbage", "open_B", "lock_B_plan", "lock_B_noplan",
                                              "lock_B_verify_failed", "cancel_pR2", "cancel_pX")
                         + list(PROBES.values()), seed=GATE_SEED, blind_checks=False),
    # 봉인·짝 봉인·짝 보충·해제(사유 없음/앞당김) × 관문 사슬(A 열기·잠그기 → pR2 → B 열기·잠그기). 상태마다 자리 검사도 한다.
    "seal": Model("seal", _acts(*CHAIN_NAMES, "seal_R1", "seal_T1", "attach_T2", "unseal_R1", "unseal_R1_early"),
                  seed=[], blind_checks=True),
    # 같은 run_id 의 다른 결과(R1b, 엔진 재실행): 해제한 결과 파일은 바뀌지 않고 run_id 하나는 한 번만 풀린다.
    # 관문 사슬은 처음부터 다 된 상태(사유 없는 해제도 되는 상태)에서 시작한다.
    "rerun": Model("rerun", _acts("seal_R1", "seal_R1b", "unseal_R1", "unseal_R1_early", "unseal_R1b", "unseal_R1b_early"),
                   seed=[SESSION_ACTIONS[n].fn for n in CHAIN_NAMES], blind_checks=False),
}
DEFAULT_MODELS = ("gate_plans", "gate_faults", "seal", "rerun")
_GATE_SEED_TEXT = "합성 프로젝트, seal.seal_bytes 로 R1(유효)·Rbad(받을 때 거부)을 먼저 봉인해 둠"
SEED_TEXT = {"gate_plans": _GATE_SEED_TEXT, "gate_faults": _GATE_SEED_TEXT,
             "seal": "빈 합성 프로젝트(세션·봉인 없음)",
             "rerun": "합성 프로젝트, A 열기·잠그기 → pR2 → B 열기·잠그기까지 다 됨(사유 없는 해제도 되는 상태)"}


# ----------------------------------------------------------------------------- 성질
class Violation(Exception):
    def __init__(self, prop: str, detail: str):
        super().__init__(f"[{prop}] {detail}")
        self.prop, self.detail = prop, detail


def _ai_files(f: Facts):
    return (f.seal_raw, dict(f.runs), dict(f.topics_files), dict(f.bins))


def check_transition(act: Action, S: Facts, T: Facts, outcome, err, spy: Spy, world: World, oracle_rules) -> None:
    """동작 하나의 앞(S)·뒤(T) 디스크와 결과로 본다."""
    # T1 seal.jsonl 은 추가만, 봉인 원본·해제 결과는 지워지거나 바뀌지 않는다(되돌릴 수 없음)
    if not T.seal_raw.startswith(S.seal_raw):
        raise Violation("추가 전용", "seal.jsonl 의 앞부분이 바뀌거나 줄었습니다")
    for kind, a, b in (("sealed/*.bin", S.bins, T.bins), ("runs/*.json", S.runs, T.runs),
                       ("topics/*.json", S.topics_files, T.topics_files)):
        for n, v in a.items():
            if b.get(n) != v:
                raise Violation("되돌릴 수 없음", f"{kind} 의 {n} 이 지워지거나 바뀌었습니다({act.name})")
    # T9 봉인 원본(.bin)을 읽어 여는 곳은 unseal 하나
    if act.group != "unseal":
        r = spy.reads_under(os.path.join(world.ai_dir, seal.SEALED_DIR))
        if r:
            raise Violation("봉인 원본은 unseal 만 연다", f"{act.name} 이 sealed\\ 를 읽었습니다: {r[0]}")
    if err is not None and not isinstance(err, seal.SealError) and act.group != "session":
        raise Violation("예상하지 못한 예외", f"{act.name}: {type(err).__name__}: {err}")
    if err is not None and act.group == "session" and not isinstance(err, EXPECTED_SESSION_ERRORS):
        raise Violation("예상하지 못한 예외", f"{act.name}: {type(err).__name__}: {err}")
    # 거절된 봉인·짝·해제는 봉인 폴더에 아무것도 쓰지 않는다
    if err is not None and act.group in ("seal", "unseal") and _ai_files(S) != _ai_files(T):
        raise Violation("거절은 쓰지 않음", f"{act.name} 이 거절({seal.error_code(err)})됐는데 봉인 폴더가 바뀌었습니다")
    if act.group == "seal":
        new_sealed = len([r for r in T.recs if r.get("kind") == seal.K_SEALED]) - \
            len([r for r in S.recs if r.get("kind") == seal.K_SEALED])
        if act.name in ("seal_T1", "attach_T2") and new_sealed:
            raise Violation("짝 보충은 다시 봉인하지 않음", f"{act.name} 뒤 ai.sealed 줄이 {new_sealed}개 늘었습니다")
        if act.name in ("seal_T1", "attach_T2") and S.unsealed("R1") and err is None:
            raise Violation("해제 뒤 짝 없음", f"R1 이 이미 해제됐는데 {act.name} 이 받아들여졌습니다")
        if act.name in ("seal_T1", "attach_T2") and err is None and S.presealed("R1") != T.presealed("R1"):
            raise Violation("짝 보충은 사전 봉인 여부를 바꾸지 않음", f"{act.name}: {S.presealed('R1')} → {T.presealed('R1')}")
        if act.name == "attach_T2" and err is None and outcome.presealed != S.presealed("R1"):
            raise Violation("짝 보충은 사전 봉인 여부를 바꾸지 않음",
                            f"attach_pairs 가 presealed={outcome.presealed} 라 했지만 판정 봉인 기록은 {S.presealed('R1')}")
        if act.name.startswith("seal_R") and S.sealed(act.target) and T.seal_raw != S.seal_raw:
            raise Violation("봉인은 한 번", f"이미 봉인된 {act.target} 을 다시 받자 seal.jsonl 에 줄이 생겼습니다")
    if act.group == "unseal":
        name = act.target
        met, why = oracle_met(S, oracle_rules)
        if err is None:
            u_new = [r for r in T.unsealed(name) if r not in S.unsealed(name)]
            if not S.sealed(name) or S.sealed(name)[0].get("check") != "valid":
                raise Violation("해제 대상", f"{name} 은 유효하게 봉인되지 않았는데 풀렸습니다")
            if S.unsealed(name):
                raise Violation("해제는 한 번(멱등)", f"이미 해제한 {name} 이 다시 풀렸습니다")
            if len(u_new) != 1:
                raise Violation("해제 기록", f"{name} 해제 뒤 ai.unsealed 새 줄이 {len(u_new)}개입니다")
            rec = u_new[0]
            if not act.early and not met:
                raise Violation("해제 관문(06 7-1)", f"사유 없이 {name} 이 풀렸지만 조건이 안 됨: {'; '.join(why)}")
            if rec.get("conditions_met") is not met:
                raise Violation("해제 기록이 조건을 바르게 적음",
                                f"conditions_met={rec.get('conditions_met')} 이지만 명세 판정은 {met} ({'; '.join(why)})")
            if (rec.get("reason") is None) != met:
                raise Violation("앞당긴 해제는 사유를 남김", f"conditions_met={met} 인데 reason={rec.get('reason')!r}")
            others = [r for r in S.recs if r.get("kind") == seal.K_UNSEALED and r.get("run_id") == rec.get("run_id")]
            if others:
                raise Violation("같은 run_id 는 한 번만 풀림", f"run_id {rec.get('run_id')} 의 다른 결과가 이미 풀려 있었습니다")
        else:
            if S.unsealed(name) and not isinstance(err, seal.AlreadyUnsealedError):
                raise Violation("해제는 한 번(멱등)", f"이미 해제한 {name} 에 {seal.error_code(err)} — already_unsealed 여야 함")
            if (isinstance(err, seal.SealBlockedError) and not act.early and met
                    and S.sealed(name) and S.sealed(name)[0].get("check") == "valid" and not S.unsealed(name)):
                raise Violation("조건이 되면 풀린다(완전성)",
                                f"명세로는 조건이 됐는데 unseal 이 막았습니다: {'; '.join(err.reasons)}")


def check_state(T: Facts, world: World, model: Model, oracle_rules) -> None:
    """새 상태 하나에서 본다(디스크를 바꾸지 않는 호출만 — 감시로 아무것도 쓰지 않았는지 확인한다).
    성질 검사 밖의 예외(코드가 뜻밖에 무너짐)도 반례로 보고한다."""
    try:
        _check_state(T, world, model, oracle_rules)
    except Violation:
        raise
    except Exception as e:  # noqa: BLE001
        raise Violation("예상하지 못한 예외", f"상태 확인 중 {type(e).__name__}: {e}") from None


def _check_state(T: Facts, world: World, model: Model, oracle_rules) -> None:
    root = world.root
    # G1 해제 관문: 실제 unseal_conditions == 명세
    cond = seal.unseal_conditions(root)
    met, why = oracle_met(T, oracle_rules)
    if cond.met != met:
        raise Violation("해제 관문(06 7-1)", f"seal.unseal_conditions.met={cond.met} (사유: {'; '.join(cond.reasons) or '없음'})"
                        f" 인데 명세 판정은 {met} ({'; '.join(why) or '조건 됨'})")
    if cond.met == bool(cond.reasons):
        raise Violation("해제 관문(06 7-1)", f"met={cond.met} 인데 사유 {len(cond.reasons)}개")
    # G2 seal.jsonl 모양
    if any(r.get("_corrupt") for r in T.recs):
        raise Violation("추가 전용", "seal.jsonl 에 깨진 줄")
    for n in SHA:
        if len(T.sealed(n)) > 1:
            raise Violation("봉인은 한 번", f"{n} 의 ai.sealed 가 {len(T.sealed(n))}줄")
        if len(T.unsealed(n)) > 1:
            raise Violation("해제는 한 번(멱등)", f"{n} 의 ai.unsealed 가 {len(T.unsealed(n))}줄")
        if T.unsealed(n) and not (T.sealed(n) and T.sealed(n)[0].get("check") == "valid"):
            raise Violation("해제 대상", f"{n} 은 유효한 봉인 없이 해제 기록이 있습니다")
    runs_unsealed = {r.get("run_id") for r in T.recs if r.get("kind") == seal.K_UNSEALED}
    if len(runs_unsealed) != len([r for r in T.recs if r.get("kind") == seal.K_UNSEALED]):
        raise Violation("같은 run_id 는 한 번만 풀림", "한 run_id 에 해제 기록이 둘 이상")
    # G3 해제 결과 파일은 해제 기록이 있을 때만
    for n in T.runs:
        if n[:-5] not in {(r or "").lower() for r in runs_unsealed}:
            raise Violation("해제 전에는 결과 파일 없음", f"runs\\{n} 가 있는데 해제 기록이 없습니다")
    for n in T.topics_files:
        if n[:-5] not in {(r or "").lower() for r in runs_unsealed}:
            raise Violation("해제 전에는 결과 파일 없음", f"topics\\{n} 가 있는데 해제 기록이 없습니다")
    # G4 seal_status 가 기록과 맞음(봉인 원본은 열지 않음)
    with Spy() as sp:
        st = seal.seal_status(root)
    if sp.reads_under(os.path.join(world.ai_dir, seal.SEALED_DIR)):
        raise Violation("봉인 원본은 unseal 만 연다", "seal_status 가 sealed\\ 를 읽었습니다")
    for e in st.entries:
        n = SHA_NAME.get(e.sha256, e.sha256)
        if (e.unsealed_at is not None) != bool(T.unsealed(n)):
            raise Violation("해제는 되돌릴 수 없음", f"seal_status 의 {n} 해제 여부가 기록과 다릅니다")
        if e.presealed != T.presealed(n):
            raise Violation("사전 봉인 계산", f"{n}: seal_status presealed={e.presealed}, 기록 시각으로는 {T.presealed(n)}")
    # G5 해제 전에는 읽지 않음 / 해제 뒤에는 읽힘
    unsealed_run = bool(runs_unsealed & {RUN_ID})
    with Spy() as sp:
        try:
            doc = seal.read_run(root, RUN_ID)
            rerr = None
        except Exception as e:  # noqa: BLE001 — 무엇이 나오든 아래에서 가른다
            doc, rerr = None, e
    opened = sp.reads_under(os.path.join(world.ai_dir, seal.RUNS_DIR)) + \
        sp.reads_under(os.path.join(world.ai_dir, seal.SEALED_DIR))
    if not unsealed_run:
        if not isinstance(rerr, seal.SealNotUnsealedError) or opened:
            raise Violation("해제 전에는 읽지 않음", f"해제 전 read_run: {type(rerr).__name__ if rerr else '읽힘'}, 연 파일 {opened[:1]}")
    elif doc is None or doc.get("run_id") != RUN_ID:
        raise Violation("해제 뒤에는 읽힘", f"해제된 run 을 read_run 이 읽지 못함: {seal.error_code(rerr) if rerr else '?'}")
    with Spy() as sp:
        try:
            seal.read_topics(root, RUN_ID)
            terr = None
        except Exception as e:  # noqa: BLE001
            terr = e
    if not unsealed_run and (not isinstance(terr, seal.SealNotUnsealedError)
                             or sp.reads_under(os.path.join(world.ai_dir, seal.TOPICS_DIR))):
        raise Violation("해제 전에는 읽지 않음", "해제 전 read_topics 가 막히지 않았거나 topics\\ 를 열었습니다")
    # G6 자리 검사: 라벨링·재라벨·출처 판정 세션은 어떤 seal 함수도 부르지 못하고 아무 파일도 열지 않는다
    if model.blind_checks:
        check_blind(world, unsealed_run)


def blind_calls(root: str):
    z = "0" * 64
    return [
        ("seal_bytes", lambda k: seal.seal_bytes(root, DOCS["R1"], source="file", by="L01", reader_kind=k)),
        ("seal_file", lambda k: seal.seal_file(root, os.path.join(root, "x.json"), by="L01", reader_kind=k)),
        ("seal_status", lambda k: seal.seal_status(root, reader_kind=k)),
        ("unseal_events", lambda k: seal.unseal_events(root, reader_kind=k)),
        ("unseal_conditions", lambda k: seal.unseal_conditions(root, reader_kind=k)),
        ("unseal", lambda k: seal.unseal(root, SHA["R1"], by="L01", early_reason="x", reader_kind=k)),
        ("read_run", lambda k: seal.read_run(root, RUN_ID, reader_kind=k)),
        ("read_topics", lambda k: seal.read_topics(root, RUN_ID, reader_kind=k)),
        ("read_words", lambda k: seal.read_words(root, RUN_ID, reader_kind=k)),
        ("read_vectors", lambda k: seal.read_vectors(root, RUN_ID, reader_kind=k)),
        ("seal_topics_bytes", lambda k: seal.seal_topics_bytes(root, DOCS["T1"], pair_sha256=z, by="L01", reader_kind=k)),
        ("seal_words_bytes", lambda k: seal.seal_words_bytes(root, b"{}", pair_sha256=z, by="L01", reader_kind=k)),
        ("seal_vectors_bytes", lambda k: seal.seal_vectors_bytes(root, b"{}", pair_sha256=z, by="L01", reader_kind=k)),
        ("attach_pairs", lambda k: seal.attach_pairs(root, SHA["R1"], {"topics": DOCS["T2"]}, by="L01", reader_kind=k)),
    ]


ANALYSIS_ALLOWED = {"seal_status", "unseal_events", "read_run", "read_topics", "read_words", "read_vectors"}


def check_blind(world: World, unsealed_run: bool) -> None:
    # 분석 세션(UNSEALED_ONLY): 건수 보기는 되지만 봉인 원본은 열지 않고, 해제 전에는 runs·topics 도 열지 않는다
    for fname in ("seal_status", "unseal_events", "read_run", "read_topics"):
        call = dict(blind_calls(world.root))[fname]
        with Spy() as sp:
            try:
                call(vis.ANALYSIS)
                err = None
            except Exception as e:  # noqa: BLE001
                err = e
        opened = sp.reads_under(os.path.join(world.ai_dir, seal.SEALED_DIR))
        if fname.startswith("read_") and not unsealed_run:
            opened += sp.reads_under(os.path.join(world.ai_dir, seal.RUNS_DIR)) + \
                sp.reads_under(os.path.join(world.ai_dir, seal.TOPICS_DIR))
            if not isinstance(err, seal.SealNotUnsealedError):
                raise Violation("자리 검사(06 7-3)", f"분석 세션의 해제 전 seal.{fname}: "
                                f"{type(err).__name__ if err else '읽힘'} — SealNotUnsealedError 여야 함")
        elif err is not None:
            raise Violation("자리 검사(06 7-3)", f"분석 세션의 seal.{fname} 이 막혔습니다: {seal.error_code(err)}")
        if opened:
            raise Violation("자리 검사(06 7-3)", f"분석 세션의 seal.{fname} 이 {opened[0]} 를 열었습니다")
    for kind in BLIND_KINDS + (vis.ANALYSIS,):
        for fname, call in blind_calls(world.root):
            if kind == vis.ANALYSIS and fname in ANALYSIS_ALLOWED:
                continue
            with Spy() as sp:
                try:
                    call(kind)
                    err = None
                except Exception as e:  # noqa: BLE001 — 무엇이 나오든 아래에서 가른다
                    err = e
            if not isinstance(err, seal.SealBlindError):
                raise Violation("자리 검사(06 7-3)", f"{kind} 자리에서 seal.{fname} 이 SealBlindError 가 아닙니다: "
                                f"{type(err).__name__ if err else '성공'}")
            if sp.calls:
                raise Violation("자리 검사(06 7-3)", f"{kind} 자리의 seal.{fname} 이 막히기 전에 파일을 열었습니다: {sp.calls[0][1]}")


def check_table() -> None:
    """visibility 표(순수): 라벨링·재라벨 세션은 어느 단계에서도 data\\ai·data\\reports 를 열지 않고, AI 결과를 보이지 않으며,
    전사문(derived\\asr*)은 기본으로 열지 않는다(토글 — 켜면 view.exposed 를 남기고 연다)."""
    for kind in LABELING_KINDS:
        for ph in vis.PHASES + (None,):
            s = vis.files_to_open(kind, ph)
            if s.ai_sealed != vis.NONE or s.reports != vis.NONE or s.engine_results != vis.NONE:
                raise Violation("열지 않음 표(03 12-1)", f"{kind}/{ph}: ai_sealed={s.ai_sealed} reports={s.reports}")
            if s.derived_asr not in (vis.NONE, vis.TOGGLE) or s.can_read("derived_asr"):
                raise Violation("열지 않음 표(03 12-1)", f"{kind}/{ph}: derived_asr={s.derived_asr}")
            if vis.can_reveal(vis.AI_RESULTS, ph, kind) or vis.can_show(vis.ASR_TEXT, ph, kind):
                raise Violation("열지 않음 표(03 12-1)", f"{kind}/{ph}: AI 결과·전사문을 기본으로 보입니다")


# ----------------------------------------------------------------------------- 탐색
@dataclass
class Result:
    model: str
    states: int = 0
    transitions: int = 0
    skipped: int = 0
    seconds: float = 0.0
    violation: Violation | None = None
    trace: list = field(default_factory=list)
    facts: Facts | None = None


HEARTBEAT_ENV = "CHATCOACH_FORMAL_HEARTBEAT"
STALL_S = 60


def _beat(world: World) -> None:
    """감시 부모(supervise)에게 '아직 나아가는 중' 을 알린다: 맥박 파일에 이 세계의 임시 폴더를 적는다(mtime 이 맥박)."""
    hb = os.environ.get(HEARTBEAT_ENV)
    if not hb:
        return
    try:
        with _REAL_OPEN(hb, "w", encoding="utf-8") as f:
            f.write(world.base)
    except OSError:
        pass


def run_action(act: Action, world: World, now: datetime, S: Facts):
    with Spy() as spy:
        try:
            out = act.fn(Ctx(world, now, S))
            err = None
        except Exception as e:  # noqa: BLE001
            out, err = None, e
    return out, err, spy


def outcome_text(out, err) -> str:
    if err is None:
        if isinstance(out, seal.UnsealResult):
            return f"성공 (conditions_met={out.conditions_met})"
        if isinstance(out, seal.AttachResult):
            return f"성공 ({', '.join(r['kind'] + ':' + r['status'] for r in out.rows)})"
        return f"성공 ({out})" if out else "성공"
    code = seal.error_code(err) if isinstance(err, seal.SealError) else type(err).__name__
    return f"거절 {type(err).__name__}({code})"


def explore(model: Model, *, keep=False, oracle_rules=None, max_states=20000) -> Result:
    res = Result(model.name)
    t0 = time.perf_counter()
    world = World(keep=keep)
    try:
        for n, fn in enumerate(model.seed):
            fn(Ctx(world, BASE - timedelta(minutes=30 - n), facts_of(world, world.snapshot(ALL_DIRTY))))
        init = world.snapshot(ALL_DIRTY)
        f0 = facts_of(world, init)
        snaps = [init]
        parents: list = [None]
        depth = [0]
        seen = {f0.key(): 0}
        try:
            check_state(f0, world, model, oracle_rules)
        except Violation as v:
            res.violation, res.facts = v, f0
            return res
        queue = deque([0])
        current = init
        while queue:
            i = queue.popleft()
            S = snaps[i]
            if current is not S:
                world.materialize(S, current)
                current = S
            fS = facts_of(world, S)
            now = BASE + timedelta(minutes=depth[i] + 1)
            for act in model.actions:
                out, err, spy = run_action(act, world, now, fS)
                if out is SKIP:
                    res.skipped += 1
                    continue
                res.transitions += 1
                if res.transitions % 25 == 1:
                    _beat(world)
                dirty = spy.dirty_dirs()
                if dirty:
                    T = world.snapshot(dirty)
                    fT = facts_of(world, T)
                else:
                    T, fT = S, fS                    # 디스크를 바꾸는 호출이 하나도 없었다 — 다시 훑지 않는다
                try:
                    bad = sorted(d for d in dirty if not world.allowed_write(act.group, d))
                    if bad:
                        where = "labels·locks·relabel" if act.group == "session" else "data\\ai"
                        raise Violation("쓰는 곳", f"{act.name} 이 {where} 밖에 썼습니다: {bad[0]}")
                    check_transition(act, fS, fT, out, err, spy, world, oracle_rules)
                    k = fT.key()
                    fresh = act.expand and k not in seen
                    if fresh:
                        with Spy() as probe_spy:
                            check_state(fT, world, model, oracle_rules)
                        if probe_spy.mutated():
                            raise Violation("읽기는 쓰지 않음", "상태 확인(unseal_conditions·seal_status·read_*) 이 디스크를 "
                                            f"바꿨습니다: {sorted(probe_spy.dirty_dirs())[:1]}")
                except Violation as v:
                    res.violation, res.facts = v, fT
                    res.trace = _trace(parents, i) + [(act, outcome_text(out, err))]
                    return res
                if fresh:
                    seen[k] = len(snaps)
                    snaps.append(T)
                    parents.append((i, act, outcome_text(out, err)))
                    depth.append(depth[i] + 1)
                    queue.append(len(snaps) - 1)
                    if len(snaps) > max_states:
                        raise SystemExit(f"상태가 {max_states}개를 넘었습니다 — 모델이 닫히지 않습니다")
                if T is not S:
                    world.materialize(S, T)
                current = S
        res.states = len(snaps)
        return res
    finally:
        res.states = res.states or len(locals().get("snaps", []))
        res.seconds = time.perf_counter() - t0
        world.close()


def _trace(parents, i):
    out = []
    while parents[i] is not None:
        pi, act, txt = parents[i]
        out.append((act, txt))
        i = pi
    return list(reversed(out))


def print_result(r: Result) -> None:
    if r.violation is None:
        print(f"통과  모델 {r.model}: 상태 {r.states}개 · 동작 {r.transitions}번 끝까지 훑음 ({r.seconds:.1f}초)")
        return
    print(f"반례  모델 {r.model}: 성질 '{r.violation.prop}' 이 깨짐 ({r.seconds:.1f}초, 상태 {r.states}개 훑던 중)")
    print(f"  0. {SEED_TEXT.get(r.model, '빈 합성 프로젝트')}")
    for n, (act, txt) in enumerate(r.trace, 1):
        print(f"  {n}. {act.text} → {txt}")
    print(f"  위반: {r.violation.detail}")
    if r.facts is not None:
        print("  그때 디스크:")
        print(r.facts.describe())


# ----------------------------------------------------------------------------- 변이 자가 시험
def _patch(obj, name, new):
    old = getattr(obj, name)
    setattr(obj, name, new)
    return lambda: setattr(obj, name, old)


def _m_unlocked_ignored():
    real = seal._labeling_session_ids
    return _patch(seal, "_labeling_session_ids",
                  lambda root, locks: [s for s in real(root, locks) if s in {sid for sid, _h, _d in locks}])


def _m_pending_ignored():
    return _patch(ar, "pending_relabel_plans", lambda *a, **k: [])


def _m_verify_ignored():
    real = ar._read_lock_doc

    def f(root, sid):
        d = dict(real(root, sid))
        d["sample_verify"], d["sample_manifests"] = [], []
        return d
    return _patch(ar, "_read_lock_doc", f)


def _m_reunseal():
    return _patch(seal, "_unsealed_record", lambda recs, sha: None)


def _m_attach_reseals():
    real = seal.attach_pairs

    def f(project_root, pair_sha256, docs, **kw):
        r = real(project_root, pair_sha256, docs, **kw)
        d = seal.project_ai_dir(project_root, PID, kw.get("ai_root"))
        seal._append(d, {"kind": seal.K_SEALED, "at": seal._now_iso(kw.get("now")), "by": "L01", "app_version": "x",
                         "sha256": pair_sha256, "size": 1, "source": "file", "check": "valid", "reject_reasons": []})
        return r
    return _patch(seal, "attach_pairs", f)


def _m_guard_off():
    return _patch(seal, "_guard", lambda *a, **k: None)


def _m_read_before_unseal():
    return _patch(seal, "_unsealed_or_blind",
                  lambda recs, run_id: next((r for r in recs if r.get("run_id") == run_id), {"run_sha256": None}))


MUTANTS = [
    # (이름, 무엇을 깨뜨렸나, 패치, 모델, 'code' 또는 'model')
    ("unlocked_ignored", "unseal_conditions 가 잠기지 않은 라벨링 세션을 보지 않음", _m_unlocked_ignored, "gate_plans", "code"),
    ("pending_ignored", "comparison_gate 가 '잠긴 재라벨 세션이 없는 계획' 을 보지 않음", _m_pending_ignored, "gate_plans",
     "code"),
    ("verify_ignored", "comparison_gate 가 잠금의 표본 재현 검증을 보지 않음", _m_verify_ignored, "gate_faults", "code"),
    ("reunseal", "_unsealed_record 가 해제 기록을 못 찾음(두 번 해제·해제 뒤 짝 붙이기)", _m_reunseal, "seal", "code"),
    ("attach_reseals", "attach_pairs 가 판정을 새로 봉인함(ai.sealed 한 줄 더)", _m_attach_reseals, "seal", "code"),
    ("guard_off", "seal._guard 가 아무 자리도 막지 않음", _m_guard_off, "seal", "code"),
    ("read_before_unseal", "read_run 이 해제 기록 없이도 runs\\ 를 엶", _m_read_before_unseal, "seal", "code"),
    ("oracle_no_unreadable", "틀린 모델: '읽지 못한 계획은 막는다' 규칙을 뺀 명세", None, "gate_faults", "model"),
]


def run_mutants(keep=False) -> int:
    missed = 0
    for name, what, patch, model, kind in MUTANTS:
        undo = patch() if patch else (lambda: None)
        rules = ORACLE_RULES - {"plan_unreadable"} if name == "oracle_no_unreadable" else None
        try:
            r = explore(MODELS[model], keep=keep, oracle_rules=rules)
        finally:
            undo()
        if r.violation is None:
            missed += 1
            print(f"못 잡음  [{kind}] {name}: {what} — 모델 {model} 이 통과해 버렸습니다")
        else:
            print(f"잡음    [{kind}] {name}: {what} — 성질 '{r.violation.prop}', 반례 {len(r.trace)}단계 ({r.seconds:.1f}초)")
            for n, (act, txt) in enumerate(r.trace, 1):
                print(f"          {n}. {act.text} → {txt}")
            print(f"          위반: {r.violation.detail}")
    return 1 if missed else 0


def supervise(argv: list[str]) -> int:
    """검사를 자식 프로세스로 돌리고 맥박을 본다. STALL_S 초 동안 한 걸음도 나아가지 않으면 자식(과 그 아래)을 끝내고
    임시 폴더를 지운 뒤 종료 2 — 끝없이 멈춰 있지 않게. (이 PC 에서는 파일을 몰아서 바꾸는 프로세스를 보안 프로그램이
    통째로 멈춰 세운 일이 있다: 스레드가 모두 Suspended, 2026-10-23. 같은 프로세스 안의 감시 스레드도 함께 멈추므로 부모가 본다.)"""
    fd, hb = tempfile.mkstemp(prefix="formal-seal-hb-", suffix=".txt")
    os.close(fd)
    env = dict(os.environ, **{HEARTBEAT_ENV: hb, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", *argv], cwd=REPO, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def pump():
        for line in p.stdout:
            sys.stdout.write(line.decode("utf-8", "replace"))
            sys.stdout.flush()
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        while p.poll() is None:
            time.sleep(1)
            if time.time() - os.path.getmtime(hb) > STALL_S:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True) if os.name == "nt" \
                    else p.kill()
                p.wait()
                t.join(5)
                try:
                    with _REAL_OPEN(hb, encoding="utf-8") as f:
                        base = f.read().strip()
                    if base and os.path.basename(base).startswith("formal-seal-"):
                        shutil.rmtree(base, ignore_errors=True)
                except OSError:
                    pass
                print(f"멈춤  검사 프로세스가 {STALL_S}초 동안 한 걸음도 나아가지 않아 끝냈습니다(검증 결과 아님). "
                      "보안 프로그램(행위 감시)이 파일을 몰아서 바꾸는 프로세스를 멈춰 세웠을 수 있습니다 — 다시 돌리고, "
                      "되풀이되면 사용자에게 알리세요(보안 설정은 바꾸지 않는다).")
                return 2
        t.join(5)
        return p.returncode
    finally:
        try:
            os.remove(hb)
        except OSError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 봉인·해제 블라인드 보장 형식 검증 (06 7장)")
    ap.add_argument("--model", choices=tuple(MODELS) + ("all",), default="all")
    ap.add_argument("--mutants", action="store_true", help="일부러 깨뜨린 코드·모델을 잡는지 본다")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)       # supervise 가 붙인다
    ap.add_argument("--no-watchdog", action="store_true", help="감시 부모 없이 이 프로세스에서 바로 돈다(디버깅)")
    raw = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(raw)
    if not a.worker and not a.no_watchdog:
        return supervise(raw)
    for k, v in DOCS.items():
        if k.startswith("T") and seal.topics_check(v, None) != ("valid", []):
            print(f"준비 실패: 합성 주제 흐름 {k} 가 topics_check 를 통과하지 못합니다")
            return 2
    if a.mutants:
        return run_mutants(a.keep)
    try:
        check_table()
        print("통과  visibility 표: 라벨링·재라벨 세션 × 모든 단계에서 ai_sealed·reports·engine_results = none, 전사문은 토글뿐")
    except Violation as v:
        print(f"반례  visibility 표: {v}")
        return 1
    bad = 0
    for m in (DEFAULT_MODELS if a.model == "all" else (a.model,)):
        r = explore(MODELS[m], keep=a.keep)
        print_result(r)
        bad += r.violation is not None
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
