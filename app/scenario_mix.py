"""가상 채팅을 훈련에 섞을 때 앱 쪽이 하는 일 (20 5장 H-4, 11장 9번, 08 D-05·D-16·D-19 2).

- find_scenario(root, range_ms): 이 프로젝트·구간의 가장 새 시나리오(app.scenario_store.latest_scenario). 시범이 잠기기
  전이면 scenarios\\ 를 열지 않는다(그 판단은 scenario_store 가 한다).
- load_messages(root): 실제 채팅(chat\\messages.jsonl) 줄 목록 — 읽기만. 훈련에 섞을 실제 채팅이자 retrieved 본문의 출처.
- make_resolver(root, project_id, messages): retrieved 줄의 (from_project_id, from_chat_id) → 원 채팅 dict. 같은 프로젝트는
  위 목록에서, 같은 채널의 다른 프로젝트는 형제 폴더의 messages.jsonl 에서(처음 쓸 때 한 번 읽음). 이름이 pilot-* 인
  폴더는 열지 않고, 다른 폴더도 시범 판단(reference_set.pilot_block_reason)에 걸리면 읽지 않는다(D-18 7).
- '가상 채팅 만들기'(훈련 메뉴 한 번): tool_available() · make_command() · run_make() 가 엔진 도구
  tools/make_scenario.py 를 **따로 프로세스로** 돌린다(app/ 은 engine/ 을 import 하지 않는다 — engine/__init__ 의 경계).
  개발 PC(저장소에서 도는 앱)에서만 된다. 묶은 exe 에는 엔진 도구가 없어 메뉴가 꺼진다.
- 이 모듈은 Qt 를 쓰지 않는다(워커에서 부른다). 네트워크·클립보드·내보내기가 없다(D-05). 시나리오 파일에 쓰지 않는다
  (쓰기는 도구 쪽 scenario_store.write_scenario). 출력·로그에는 숫자·코드만(본문·닉네임 없음, D-17 8).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from typing import Callable, Mapping, Sequence

from . import jsonl

LOG = logging.getLogger(__name__)

PILOT_PREFIX = "pilot-"
MESSAGES_REL = ("chat", "messages.jsonl")
PROJECT_JSON_MAX_BYTES = 4 * 1024 * 1024
TOOL_REL = ("tools", "make_scenario.py")
ENGINE_REL = ("engine", "retrieve.py")
MAKE_TIMEOUT_S = 600.0
POLL_S = 0.1

# 도구 출력 → 사람에게 보일 한 줄 (숫자·코드만 들어간다)
MSG_WRITTEN = "가상 채팅을 만들었습니다 (가져옴 {0}줄) — 다음 '스트리머 훈련' 에 섞입니다"
MSG_EXISTING = "같은 가상 채팅이 이미 있습니다 (가져옴 {0}줄)"
MSG_NOTHING = "채팅이 드문 곳이 없어 가상 채팅을 만들지 않았습니다 (1분에 실제 채팅 {0}개 이하인 곳만 채웁니다)"
MSG_FAILED = "가상 채팅을 만들지 못했습니다 ({0})"
REASON_NO_TOOL = "가상 채팅 만들기는 개발 PC(저장소에서 도는 앱)의 엔진 도구로만 됩니다"


# ---------------------------------------------------------------------------- 시나리오 찾기
def find_scenario(project_root: str, range_ms: tuple[int, int] | None):
    """(scenario_store.Scenario 또는 None, 숫자 dict). 모듈이 없거나 읽기 오류면 (None, {"error": 종류})."""
    try:
        from . import scenario_store as st
    except ImportError as e:
        return None, {"error": type(e).__name__}
    try:
        return st.latest_scenario(project_root, tuple(range_ms) if range_ms else None)
    except (OSError, ValueError, RuntimeError) as e:
        LOG.warning("시나리오 찾기 실패: %s", type(e).__name__)
        return None, {"error": type(e).__name__}


def virtual_count(scenario) -> int:
    """시나리오의 가상 줄 수(retrieved + generated)."""
    counts = (getattr(scenario, "header", None) or {}).get("counts") or {}
    return sum(int(counts.get(k) or 0) for k in ("retrieved", "generated"))


# ---------------------------------------------------------------------------- 실제 채팅·원 채팅 찾기
def messages_path(project_root: str) -> str:
    return os.path.join(os.path.abspath(project_root), *MESSAGES_REL)


def load_messages(project_root: str) -> list[dict]:
    """chat\\messages.jsonl 의 줄(dict) 목록. 깨진 줄은 jsonl.read 가 뺀다. 파일이 없으면 빈 목록."""
    p = messages_path(project_root)
    if not os.path.isfile(p):
        return []
    return list(jsonl.read(p).records)


def _project_id(project_dir: str) -> str | None:
    p = os.path.join(project_dir, "project.json")
    try:
        with open(p, "rb") as f:
            raw = f.read(PROJECT_JSON_MAX_BYTES + 1)
        if len(raw) > PROJECT_JSON_MAX_BYTES:
            return None
        doc = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    pid = doc.get("project_id") if isinstance(doc, dict) else None
    return pid if isinstance(pid, str) else None


def _pilot_blocked(project_dir: str) -> bool:
    if os.path.basename(os.path.abspath(project_dir)).lower().startswith(PILOT_PREFIX):
        return True
    try:
        from . import reference_set
        return reference_set.pilot_block_reason(project_dir) is not None
    except Exception:  # noqa: BLE001 — 판단이 안 되면 읽지 않는다(시범 쪽으로 안전하게)
        return True


def make_resolver(project_root: str, project_id: str | None, messages: Sequence[Mapping], *,
                  projects_root: str | None = None) -> Callable[[str, str], Mapping | None]:
    """retrieved 본문을 찾는 함수(core.scenario.merge_for_display 의 resolve). 찾지 못하면 None(그 줄은 건너뛴다).

    같은 프로젝트(project_id 가 같음)는 messages 에서 찾는다. 다른 프로젝트는 projects_root(기본: 프로젝트 폴더의 부모)
    바로 아래 폴더 가운데 project.json 의 project_id 가 같은 곳의 messages.jsonl 을, 처음 필요할 때 한 번 읽는다.
    pilot-* 폴더는 project.json 도 열지 않는다. 받은 dict 는 고치지 않는다."""
    root = os.path.abspath(project_root)
    own = {m.get("chat_id"): m for m in messages if isinstance(m, Mapping) and isinstance(m.get("chat_id"), str)}
    parent = os.path.abspath(projects_root) if projects_root else os.path.dirname(root)
    cache: dict[str, dict | None] = {}
    dirs: dict[str, str] | None = None

    def _other_dirs() -> dict[str, str]:
        nonlocal dirs
        if dirs is None:
            dirs = {}
            try:
                names = sorted(os.listdir(parent))
            except OSError:
                names = []
            for n in names:
                if n.lower().startswith(PILOT_PREFIX):
                    continue                              # 시범 폴더는 열지 않는다(이름만 보고)
                d = os.path.join(parent, n)
                if os.path.abspath(d) == root or not os.path.isdir(d):
                    continue
                pid = _project_id(d)
                if pid and pid not in dirs:
                    dirs[pid] = d
        return dirs

    def resolve(from_project_id, from_chat_id):
        if not isinstance(from_chat_id, str):
            return None
        if from_project_id == project_id or not from_project_id:
            return own.get(from_chat_id)
        if from_project_id not in cache:
            d = _other_dirs().get(from_project_id)
            idx = None
            if d is not None and not _pilot_blocked(d):
                try:
                    idx = {m.get("chat_id"): m for m in load_messages(d) if isinstance(m.get("chat_id"), str)}
                except OSError:
                    idx = None
            cache[from_project_id] = idx
        idx = cache[from_project_id]
        return idx.get(from_chat_id) if idx else None

    return resolve


# ---------------------------------------------------------------------------- 가상 채팅 만들기 (엔진 도구)
def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tool_path() -> str | None:
    """tools/make_scenario.py 경로. 묶은 exe 이거나 도구·엔진 파일이 없으면 None(메뉴를 끈다)."""
    if getattr(sys, "frozen", False):
        return None
    base = repo_root()
    tool = os.path.join(base, *TOOL_REL)
    if not (os.path.isfile(tool) and os.path.isfile(os.path.join(base, *ENGINE_REL))):
        return None
    return tool


def tool_available() -> bool:
    return tool_path() is not None


def make_command(project_root: str, range_ms: tuple[int, int], *, seed: int = 0,
                 extra_args: Sequence[str] = (), python: str | None = None) -> list[str] | None:
    """도구를 부를 명령(목록). 도구가 없으면 None. python 은 기본으로 지금 앱의 해석기(앱 .venv —
    도구는 app.scenario_store 로 검사·쓰기를 하므로 앱 쪽 의존성이 필요하다)."""
    tool = tool_path()
    if tool is None:
        return None
    a, b = int(range_ms[0]), int(range_ms[1])
    return [python or sys.executable, "-I", tool, "--project", os.path.abspath(project_root),
            "--from", str(a), "--to", str(b), "--seed", str(int(seed)), *[str(x) for x in extra_args]]


def _decode(b: bytes) -> str:
    for enc in ("utf-8", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def parse_output(stdout: bytes | str) -> dict:
    """도구 출력의 마지막 JSON 줄 → dict. 없으면 {"error": "no_output"}."""
    text = stdout if isinstance(stdout, str) else _decode(stdout or b"")
    for line in reversed([x.strip() for x in text.splitlines() if x.strip()]):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return {"error": "no_output"}


def run_make(cmd: Sequence[str], *, timeout_s: float = MAKE_TIMEOUT_S, cancelled: Callable[[], bool] | None = None,
             popen=subprocess.Popen) -> dict:
    """도구를 돌리고 결과 dict(+ "rc", "elapsed_ms"). 취소되면 프로세스를 끝내고 {"error": "cancelled"}.
    작업 폴더는 저장소 루트(도구 안의 sys.path 처리와 같은 곳). 표준 입력은 막는다."""
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    t0 = time.monotonic()
    try:
        proc = popen(list(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     cwd=repo_root(), env=env, creationflags=flags)
    except OSError as e:
        return {"error": "spawn_failed", "detail": type(e).__name__, "rc": None, "elapsed_ms": 0}
    out = b""
    while True:
        try:
            out, _err = proc.communicate(timeout=POLL_S)
            break
        except subprocess.TimeoutExpired:
            stop = (cancelled is not None and cancelled()) or (time.monotonic() - t0) > timeout_s
            if stop:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    pass
                code = "cancelled" if (cancelled is not None and cancelled()) else "timeout"
                return {"error": code, "rc": None, "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    res = parse_output(out)
    res["rc"] = proc.returncode
    res["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return res


def describe(res: Mapping, *, quiet_max_real: int = 2) -> tuple[bool, str]:
    """도구 결과 → (시나리오가 생겼거나 이미 있음, 상태 줄 한 줄). 숫자·코드만 넣는다."""
    if not isinstance(res, Mapping):
        return False, MSG_FAILED.format("no_output")
    if res.get("error"):
        return False, MSG_FAILED.format(str(res.get("error"))[:40])
    n = int(((res.get("counts") or {}).get("retrieved")) or 0)
    if res.get("written"):
        return True, MSG_WRITTEN.format(n)
    if res.get("existing"):
        return True, MSG_EXISTING.format(n)
    if res.get("reason") == "nothing_to_fill":
        return False, MSG_NOTHING.format(int(quiet_max_real))
    return False, MSG_FAILED.format("rc=" + str(res.get("rc")))


__all__ = ["find_scenario", "virtual_count", "load_messages", "make_resolver", "tool_path", "tool_available",
           "make_command", "parse_output", "run_make", "describe", "REASON_NO_TOOL"]
