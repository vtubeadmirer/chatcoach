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
- 생성 연결(D-24 3, 25 5-3 4): LMSettings(주소·모델·LLM 확인)가 있으면 run_virtual_make() 가 같은 한 번 안에서
  ① check_command(engine generate --check-server: LM Studio 확인, 보내는 글 없음) → ② generate_command(engine generate
  --out <P>\\scenarios\\gen-<from>-<to>.jsonl --llm-check) → ③ make_command(--generated 그 파일) 를 차례로 따로 프로세스로
  돌린다. ①·② 가 실패하면(lmstudio_off·model_missing·not_local·nicknames_missing …) ③ 을 retrieved 만으로 돌리고 결과에
  llm.code 를 남긴다(상태 줄에 코드 한 줄). 모델 설정이 없으면 ①·② 를 부르지 않는다(이 PC 의 LM Studio 에도 접속하지
  않음 — 20 4-3 '설정 파일에 적은 model_id 하나'). 주소는 이 PC(127.0.0.1·localhost·[::1])만 — 앱은 접속하지 않고 문자열만
  보며(local_url), 엔진이 한 번 더 본다(engine.llm_local, 규칙 한 벌). 앱은 LM Studio 에 직접 접속하지 않는다(D-06 '배치').
- 이 모듈은 Qt 를 쓰지 않는다(워커에서 부른다). 네트워크·클립보드·내보내기가 없다(D-05). 시나리오 파일에 쓰지 않는다
  (쓰기는 도구 쪽 scenario_store.write_scenario). 출력·로그에는 숫자·코드만(본문·닉네임 없음, D-17 8).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
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
MSG_WRITTEN_GEN = "가상 채팅을 만들었습니다 (가져옴 {0}줄 · 생성 {1}줄) — 다음 '스트리머 훈련' 에 섞입니다"
MSG_EXISTING_GEN = "같은 가상 채팅이 이미 있습니다 (가져옴 {0}줄 · 생성 {1}줄)"
MSG_LLM_SKIPPED = " · 생성 0줄: {0}"           # LM Studio 길이 안 됐을 때 상태 줄 끝에 붙는 한 줄(코드 + 짧은 뜻)
LLM_CODE_KO = {
    "lmstudio_off": "LM Studio 서버가 꺼져 있습니다 — 가져온 채팅만 섞었습니다",
    "model_missing": "설정한 모델이 LM Studio 에 올라가 있지 않습니다 — 가져온 채팅만 섞었습니다",
    "not_local": "LM Studio 주소는 이 PC(127.0.0.1·localhost)만 받습니다 — 가져온 채팅만 섞었습니다",
    "nicknames_missing": "원 닉네임 목록이 없어 생성하지 않았습니다(수집한 PC 에서만) — 가져온 채팅만 섞었습니다",
    "pilot_locked": "시범 프로젝트는 생성하지 않습니다",
    "nothing_generated": "거르기를 지난 생성 줄이 없습니다 — 가져온 채팅만 섞었습니다",
}

# 생성 연결 (D-24 3): engine generate 를 저장소 루트에서 부르는 짧은 시작 글(app.viewer_flow 의 _BOOT 와 같은 꼴 — -I 라
# 작업 폴더가 sys.path 에 없으므로 저장소 루트를 넣고 engine.__main__ 을 부른다)
ENGINE_BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); from engine.__main__ import main; "
               "raise SystemExit(main(sys.argv[2:]))")
LMSTUDIO_DEFAULT_URL = "http://127.0.0.1:1234"
GEN_FILE_PREFIX = "gen-"
CHECK_TIMEOUT_S = 30.0
GENERATE_TIMEOUT_S = 1800.0                # 창 40 × (생성 1 + 확인 몇 줄) — 실제 시간은 시범 뒤 잰다(25 6-1)
_LOCAL_URL_RE = re.compile(r"\Ahttps?://(127\.0\.0\.1|localhost|\[::1\])(:([0-9]{1,5}))?(/v1)?/?\Z", re.I)
_MODEL_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,119}\Z")
LLM_STEP_CHECK = "check"
LLM_STEP_GENERATE = "generate"


@dataclass(frozen=True)
class LMSettings:
    """LM Studio 생성 설정(숨은 설정 settings.ini [lmstudio], 앱 메인 창이 읽는다). 값은 local_url·model_ok 를 지난 것."""
    url: str = LMSTUDIO_DEFAULT_URL
    model: str = ""
    llm_check: bool = True

    @property
    def enabled(self) -> bool:
        return bool(self.model)


def local_url(url) -> str | None:
    """LM Studio 주소가 이 PC 의 꼴('http(s)://127.0.0.1|localhost|[::1][:포트][/v1]')이면 끝 '/' 를 뗀 주소, 아니면 None.
    접속하지 않는다(문자열만). 엔진(engine.llm_local.is_local_url)보다 좁게 받는다 — 여기서 받은 주소는 엔진도 받는다."""
    if not isinstance(url, str):
        return None
    m = _LOCAL_URL_RE.match(url.strip())
    if not m:
        return None
    if m.group(3) is not None and not 1 <= int(m.group(3)) <= 65535:
        return None
    return url.strip().rstrip("/")


def model_ok(model) -> bool:
    """모델 이름 꼴(영숫자로 시작, 120자 이하, 공백·따옴표 없음). 명령줄 인자로 넘기므로 '-' 로 시작하지 않게."""
    return isinstance(model, str) and bool(_MODEL_RE.match(model))


def lm_settings_from(url, model, llm_check=True) -> tuple[LMSettings, str | None]:
    """설정 값 → (LMSettings, 문제 코드 또는 None). 주소가 이 PC 밖이면 'not_local'(그때 enabled 는 그대로 두고 부르는
    쪽이 생성을 건너뛴다), 모델 꼴이 틀리면 모델을 비운다('bad_model'). 빈 주소는 기본 127.0.0.1:1234."""
    m = model.strip() if isinstance(model, str) else ""
    prob = None
    if m and not model_ok(m):
        m, prob = "", "bad_model"
    u = url.strip() if isinstance(url, str) and url.strip() else LMSTUDIO_DEFAULT_URL
    lu = local_url(u)
    if lu is None:
        prob = "not_local"
        lu = u
    chk = llm_check
    if isinstance(chk, str):
        chk = chk.strip().lower() not in ("0", "false", "off", "no")
    return LMSettings(url=lu, model=m, llm_check=bool(chk)), prob


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
                 extra_args: Sequence[str] = (), python: str | None = None,
                 generated: str | None = None) -> list[str] | None:
    """도구를 부를 명령(목록). 도구가 없으면 None. python 은 기본으로 지금 앱의 해석기(앱 .venv —
    도구는 app.scenario_store 로 검사·쓰기를 하므로 앱 쪽 의존성이 필요하다). generated 를 주면 '--generated 그 파일'
    (engine generate --out 이 쓴 생성 후보 파일)을 더한다."""
    tool = tool_path()
    if tool is None:
        return None
    a, b = int(range_ms[0]), int(range_ms[1])
    cmd = [python or sys.executable, "-I", tool, "--project", os.path.abspath(project_root),
           "--from", str(a), "--to", str(b), "--seed", str(int(seed)), *[str(x) for x in extra_args]]
    if generated:
        cmd += ["--generated", os.path.abspath(generated)]
    return cmd


# ---------------------------------------------------------------------------- 생성 연결 (D-24 3)
def _engine_command(args: Sequence[str], python: str | None = None) -> list[str] | None:
    if tool_path() is None:                       # 묶은 exe·엔진 파일 없음: 생성도 못 한다
        return None
    return [python or sys.executable, "-I", "-c", ENGINE_BOOT, repo_root(), "generate", *[str(x) for x in args]]


def check_command(llm: LMSettings, *, python: str | None = None) -> list[str] | None:
    """① LM Studio 확인 명령(engine generate --check-server). 보내는 글이 없다(GET /v1/models 한 번)."""
    args = ["--check-server", "--url", llm.url]
    if llm.model:
        args += ["--model", llm.model]
    return _engine_command(args, python)


def generation_path(project_root: str, range_ms: tuple[int, int]) -> str:
    """생성 후보 파일 자리 <P>\\scenarios\\gen-<from>-<to>.jsonl (보호 구역. 시나리오 목록 SC-* 에 섞이지 않는다.
    같은 구간이면 다음 만들기가 덮어쓴다 — 엔진은 첫 줄이 generation.header 인 파일만 덮는다)."""
    a, b = int(range_ms[0]), int(range_ms[1])
    return os.path.join(os.path.abspath(project_root), "scenarios", f"{GEN_FILE_PREFIX}{a}-{b}.jsonl")


def generate_threshold_args(extra_args: Sequence[str]) -> list[str]:
    """make_scenario 쪽 문턱 인자(--threshold · --quiet-max-real · --target-per-min · --max-virtual-ratio) → engine generate
    인자(--threshold Q/T · --max-virtual-ratio). 생성과 고르기가 같은 드문 창을 보게 한다. 모르는 인자는 넘기지 않는다."""
    args = [str(x) for x in extra_args]

    def val(flag):
        for i, a in enumerate(args[:-1]):
            if a == flag:
                return args[i + 1]
        return None
    out: list[str] = []
    th, q, t = val("--threshold"), val("--quiet-max-real"), val("--target-per-min")
    if th is not None:
        out += ["--threshold", th]
    elif q is not None or t is not None:
        out += ["--threshold", f"{q if q is not None else 2}/{t if t is not None else 6}"]
    mvr = val("--max-virtual-ratio")
    if mvr is not None:
        out += ["--max-virtual-ratio", mvr]
    return out


def generate_command(project_root: str, range_ms: tuple[int, int], llm: LMSettings, *, out_path: str,
                     seed: int = 0, extra_args: Sequence[str] = (), python: str | None = None) -> list[str] | None:
    """② 생성 명령(engine generate --out <생성 후보 파일> --model … --url …[--llm-check]). 보낸 기록은 엔진 기본 자리
    <P>\\scenarios\\llm-send.jsonl(숫자·해시만, D-24 2)."""
    a, b = int(range_ms[0]), int(range_ms[1])
    args = ["--project", os.path.abspath(project_root), "--from", str(a), "--to", str(b), "--seed", str(int(seed)),
            "--model", llm.model, "--url", llm.url, "--out", os.path.abspath(out_path),
            *generate_threshold_args(extra_args)]
    if llm.llm_check:
        args.append("--llm-check")
    return _engine_command(args, python)


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


def _code(res: Mapping | None, default: str) -> str:
    c = (res or {}).get("error") if isinstance(res, Mapping) else None
    c = c if isinstance(c, str) and re.fullmatch(r"[a-z0-9_:]{1,40}", c) else None
    return c or default


def run_virtual_make(project_root: str, range_ms: tuple[int, int], *, seed: int = 0, extra_args: Sequence[str] = (),
                     llm: LMSettings | None = None, runner: Callable | None = None,
                     cancelled: Callable[[], bool] | None = None) -> dict:
    """'훈련 → 가상 채팅 만들기' 한 번의 일(워커에서 부른다, Qt 없음). 결과는 make_scenario 출력 dict 에 "llm" 칸
    {"tried", "code", "step", "generated_lines", "checked", "steps": [...]} 을 더한 것(숫자·코드만).

    llm 이 없거나 모델 설정이 없으면 make_scenario 만(retrieved, 지금까지와 같음 — LM Studio 에 접속하지 않는다).
    있으면 ① 확인 → ② 생성(--out) → ③ make_scenario --generated. ①·② 가 안 되면 ③ 을 retrieved 만으로, llm.code 에
    까닭 코드(lmstudio_off·model_missing·not_local·nicknames_missing·nothing_generated …). 취소되면 {"error": "cancelled"}.
    runner(cmd, cancelled) → dict 는 시험용(기본은 run_make, 단계마다 시간 상한)."""
    def run(cmd, timeout_s):
        if runner is not None:
            return runner(cmd, cancelled)
        return run_make(cmd, timeout_s=timeout_s, cancelled=cancelled)

    info: dict = {"tried": False, "code": None, "step": None, "generated_lines": 0, "steps": []}
    gen_path = None
    if llm is not None and llm.enabled:
        info["tried"] = True
        if local_url(llm.url) is None:                    # 접속 전에(앱은 접속하지 않지만 명령도 만들지 않는다)
            info.update(code="not_local", step=LLM_STEP_CHECK)
        else:
            cmd = check_command(llm)
            res = run(cmd, CHECK_TIMEOUT_S) if cmd is not None else {"error": "no_tool"}
            info["steps"].append({"step": LLM_STEP_CHECK, "rc": res.get("rc"), "error": res.get("error"),
                                  "elapsed_ms": res.get("elapsed_ms")})
            if res.get("error") == "cancelled":
                return {"error": "cancelled", "llm": info}
            if res.get("server") != "on" or res.get("error"):
                info.update(code=_code(res, "lmstudio_off"), step=LLM_STEP_CHECK)
            else:
                out = generation_path(project_root, range_ms)
                cmd = generate_command(project_root, range_ms, llm, out_path=out, seed=seed, extra_args=extra_args)
                g = run(cmd, GENERATE_TIMEOUT_S) if cmd is not None else {"error": "no_tool"}
                st = g.get("stats") if isinstance(g.get("stats"), Mapping) else {}
                chk = st.get("llm_check") if isinstance(st.get("llm_check"), Mapping) else {}
                info["steps"].append({"step": LLM_STEP_GENERATE, "rc": g.get("rc"), "error": g.get("error"),
                                      "elapsed_ms": g.get("elapsed_ms"), "requests": st.get("requests"),
                                      "candidates": st.get("candidates"), "lines": g.get("lines"),
                                      "checked": chk.get("calls")})
                if g.get("error") == "cancelled":
                    return {"error": "cancelled", "llm": info}
                if g.get("error") or not g.get("written"):
                    info.update(code=_code(g, "generate_failed"), step=LLM_STEP_GENERATE)
                elif not int(g.get("lines") or 0):
                    info.update(code="nothing_generated", step=LLM_STEP_GENERATE)
                else:
                    info["generated_lines"] = int(g.get("lines") or 0)
                    gen_path = out
    cmd = make_command(project_root, range_ms, seed=seed, extra_args=extra_args, generated=gen_path)
    if cmd is None:
        return {"error": "no_tool", "llm": info}
    res = run(cmd, MAKE_TIMEOUT_S)
    res = dict(res) if isinstance(res, Mapping) else {"error": "no_output"}
    if gen_path is not None and res.get("error"):
        # 생성 줄을 합치다 실패(생성 파일 깨짐·nicknames_missing 등) — retrieved 만으로 한 번 더(D-24 3 '꺼지면 retrieved 만')
        info.update(code="merge_" + _code(res, "failed")[:34], step="merge")
        cmd = make_command(project_root, range_ms, seed=seed, extra_args=extra_args)
        res = run(cmd, MAKE_TIMEOUT_S) if cmd is not None else {"error": "no_tool"}
        res = dict(res) if isinstance(res, Mapping) else {"error": "no_output"}
    res["llm"] = info
    return res


def describe(res: Mapping, *, quiet_max_real: int = 2) -> tuple[bool, str]:
    """도구 결과 → (시나리오가 생겼거나 이미 있음, 상태 줄 한 줄). 숫자·코드만 넣는다. 생성을 시도했다가 안 됐으면
    (res["llm"]["code"]) 끝에 ' · 생성 0줄: <코드> — <짧은 뜻>' 을 붙인다(D-24 3 'lmstudio_off 한 줄')."""
    if not isinstance(res, Mapping):
        return False, MSG_FAILED.format("no_output")
    llm = res.get("llm") if isinstance(res.get("llm"), Mapping) else {}
    tail = ""
    if llm.get("tried") and llm.get("code"):
        c = str(llm.get("code"))[:40]
        ko = LLM_CODE_KO.get(c)
        tail = MSG_LLM_SKIPPED.format(f"{c} — {ko}" if ko else c)
    if res.get("error"):
        return False, MSG_FAILED.format(str(res.get("error"))[:40]) + tail
    counts = res.get("counts") or {}
    n = int(counts.get("retrieved") or 0)
    g = int(counts.get("generated") or 0)
    if res.get("written"):
        return True, (MSG_WRITTEN_GEN.format(n, g) if g else MSG_WRITTEN.format(n)) + tail
    if res.get("existing"):
        return True, (MSG_EXISTING_GEN.format(n, g) if g else MSG_EXISTING.format(n)) + tail
    if res.get("reason") == "nothing_to_fill":
        return False, MSG_NOTHING.format(int(quiet_max_real)) + tail
    return False, MSG_FAILED.format("rc=" + str(res.get("rc"))) + tail


__all__ = ["find_scenario", "virtual_count", "load_messages", "make_resolver", "tool_path", "tool_available",
           "make_command", "parse_output", "run_make", "describe", "REASON_NO_TOOL",
           "LMSettings", "LMSTUDIO_DEFAULT_URL", "local_url", "model_ok", "lm_settings_from", "check_command",
           "generation_path", "generate_threshold_args", "generate_command", "run_virtual_make",
           "MSG_WRITTEN_GEN", "MSG_EXISTING_GEN", "MSG_LLM_SKIPPED", "LLM_CODE_KO"]
