"""시청자 훈련(G) 열기 — 메인 창이 부르는 파일 쪽 일 (08 D-20 2·3·5, 21 6-1~6-3). Qt 를 쓰지 않는다.

메인 창의 '훈련 → 시청자 훈련' 한 번(메뉴 2클릭)이 여기를 거쳐 app.ui.viewer_train_view 를 연다.

| 할 일 | 함수 | 규칙 |
|---|---|---|
| 시범 판단 | block_reason | 폴더 이름(pilot-*)·시범 기록(freeze.json·setup.json)이면 시범 세션이 모두 잠기기 전에 끈다(D-20 5). |
|  |  | 라벨링 세션에서는 잠김 판정(계획·매니페스트 열기)도 하지 않고 끈다(06 7-3 — F 와 같음) |
| 구간 | choose_segment | 이 프로젝트의 가장 새 시나리오 구간 → 훈련 기준의 첫 구간(F 와 같은 구간) → 첫 채팅부터 10분 |
| 주제 흐름 | topic_blocks | 해제된 run 의 짝 주제 흐름(seal.read_topics, 보기 전용도 됨). 봉인 중이면 없음 |
| 전사 낱말 | words_status · load_words | <P>\\derived\\asr-words.json(엔진 engine/transcript_words 가 VOD 로 보정해 미리 만든 것). |
|  |  | 검사는 app.core.words_doc(엔진과 같은 규칙). asr.json 해시·보정 판·textnorm 판이 다르면 '오래됨' |
| 전사 낱말 만들기 | words_command · run_words | 없거나 오래됐으면 열 때 엔진 도구를 따로 프로세스로(메인 창이 워커로 돌린다). |
|  |  | 엔진 .venv-engine 해석기가 있으면 그것, 없으면 지금 앱 해석기(무거운 의존성이 없다). 묶은 exe 는 못 함 |

- 앱은 엔진을 import 하지 않는다(D-06) — 전사 낱말은 엔진이 쓴 파일을 읽기만 하고, 만들 때는 명령을 따로 띄운다
  (app.scenario_mix 의 '가상 채팅 만들기' 와 같은 길: run_make).
- 시범 프로젝트(이름 pilot-*)는 어떤 함수도 그 폴더를 열지 않는다(이름만 보고 돌아간다).
- 첫 판은 원래 반응·사람 라벨을 보이지 않으므로 노출로 세지 않는다(21 6-3) — 이 모듈은 training\\ 에 쓰지 않는다.
- 로그·상태 줄에는 숫자와 코드만(낱말·채팅 본문 없음).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from typing import Callable, Mapping, Sequence

from .core import textnorm as TN
from .core import words_doc as WD

LOG = logging.getLogger(__name__)

PILOT_PREFIX = "pilot-"
WORDS_REL = ("derived", "asr-words.json")
ASR_REL = ("derived", "asr.json")
WORDS_MAX_BYTES = 16 * 1024 * 1024
ASR_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_SPAN_MS = 10 * 60_000            # 시나리오·기준 구간이 없을 때 훈련 구간 길이
WORDS_TIMEOUT_S = 300.0
ENGINE_REL = ("engine", "transcript_words.py")
ENGINE_VENV_PY = (".venv-engine", "Scripts", "python.exe")
# -I(격리) 로 띄우면 작업 폴더가 sys.path 에 들어가지 않는다 — 저장소 루트를 인자로 넘겨 넣는다
_BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); from engine.__main__ import main; "
         "raise SystemExit(main(sys.argv[2:]))")

# 전사 낱말 파일 상태 코드
W_OK = "ok"
W_MISSING = "missing"
W_NO_ASR = "no_asr"
W_STALE_ASR = "stale_asr"
W_STALE_CAL = "stale_calibration"
W_STALE_TEXTNORM = "stale_textnorm"
W_BAD = "bad"
W_DEFAULT_CAL = "default_calibration"    # 보정 없이(오프셋 0) 만든 파일 — 시각이 맞는다고 볼 수 없어 쓰지 않는다
W_UNVERIFIED_CAL = "unverified_calibration"   # 보정이 아직 확인 전(unverified, 예: 자동으로 생긴 판 0) — 쓰지 않는다
W_PILOT = "pilot"
# 03 4-2 '반응 라벨링은 verified·skipped 일 때만' 과 같은 기준: 이 상태의 보정으로 만든 낱말만 채점에 쓴다
USABLE_CAL_STATUSES = ("verified", "skipped")
WORDS_NOTE_KO = {
    W_UNVERIFIED_CAL: "전사 낱말: 영상 시각 보정 전이라 쓰지 않았습니다 — 주제 적합성은 주제 흐름만 봅니다",
    W_DEFAULT_CAL: "전사 낱말: 영상 시각 보정이 없어 쓰지 않았습니다 — 주제 적합성은 주제 흐름만 봅니다",
}
REBUILD = (W_MISSING, W_STALE_ASR, W_STALE_CAL, W_STALE_TEXTNORM, W_BAD)

SEG_SCENARIO = "scenario"
SEG_REFERENCE = "reference"
SEG_CHAT = "first_chat"

REASON_LABELING_PILOT = ("시범 프로젝트는 라벨링 세션에서 시청자 훈련을 열지 않습니다 — 시범 세션이 모두 잠긴 뒤 보기 "
                         "전용으로 여세요 (D-20 5)")


# ---------------------------------------------------------------------------- 시범
def is_pilot_name(project_root: str | None) -> bool:
    """폴더 이름이 pilot-* 인가(폴더는 열지 않는다)."""
    return bool(project_root) and os.path.basename(os.path.abspath(project_root)).lower().startswith(PILOT_PREFIX)


def block_reason(project_root: str, *, labeling: bool) -> tuple[str | None, bool]:
    """(시범 때문에 막힌 까닭 또는 None, 시범인데 다 잠겨 열 수 있음).

    - 시범 판단(이름 또는 <데이터>\\pilot 의 시범 기록)이 안 되면 시범으로 본다(블라인드 쪽으로 안전하게).
    - labeling=True(라벨링·출처 판정 세션)면 시범은 잠김 판정 없이 끈다 — 계획·매니페스트를 열지 않는다(06 7-3).
    - 보기 전용이면 reference_set.pilot_block_reason(06 7-1 봉인 해제 조건과 같은 판단)."""
    from . import reference_set as rs
    try:
        pilot = is_pilot_name(project_root) or rs.is_pilot_project(project_root)
    except Exception:  # noqa: BLE001
        pilot = True
    if not pilot:
        return None, False
    if labeling:
        return REASON_LABELING_PILOT, False
    try:
        why = rs.pilot_block_reason(project_root)
    except Exception as e:  # noqa: BLE001
        why = f"시범 잠김을 판단하지 못했습니다 ({type(e).__name__})"
    return (why, False) if why else (None, True)


# ---------------------------------------------------------------------------- 구간
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def choose_segment(*, scenario_range: Sequence[int] | None = None, reference_range: Sequence[int] | None = None,
                   first_chat_ms: int | None = None, duration_ms: int | None = None,
                   span_ms: int = DEFAULT_SPAN_MS) -> tuple[tuple[int, int] | None, str | None]:
    """훈련 구간과 어디서 왔나(scenario · reference · first_chat). 정할 수 없으면 (None, None). 순수."""
    for rng, src in ((scenario_range, SEG_SCENARIO), (reference_range, SEG_REFERENCE)):
        if rng is not None and len(rng) == 2 and _is_int(rng[0]) and _is_int(rng[1]) and rng[1] > rng[0] >= 0:
            return (int(rng[0]), int(rng[1])), src
    if _is_int(first_chat_ms) and first_chat_ms >= 0:
        a = int(first_chat_ms)
        b = a + int(span_ms)
        if _is_int(duration_ms) and duration_ms > a:
            b = min(b, int(duration_ms))
        if b > a:
            return (a, b), SEG_CHAT
    return None, None


# ---------------------------------------------------------------------------- 주제 흐름 (해제된 run 만)
def topic_blocks(project_root: str, *, ai_root: str | None = None) -> tuple[list[tuple[int, int, tuple]], str | None]:
    """해제된 run 의 짝 주제 흐름 → ([(start_ms, end_ms, keywords)], run_id). 없거나 봉인 중·읽기 실패면 ([], None).
    seal.jsonl → topics\\<run_id>.json 만 연다(봉인 파일·라벨은 열지 않는다). 시범 이름이면 아무것도 열지 않는다."""
    if is_pilot_name(project_root):
        return [], None
    try:
        from . import analysis_flow as af
        from . import seal
        from .core import topics as T
        st = seal.seal_status(project_root, ai_root=ai_root, reader_kind=None)
        runs = af.unsealed_run_ids(st)
    except Exception as e:  # noqa: BLE001 — 주제 흐름이 없어도 시청자 훈련은 된다(주제 적합성은 전사 낱말로)
        LOG.info("시청자 훈련 주제 흐름: 봉인 기록 읽기 실패 %s", type(e).__name__)
        return [], None
    for rid in reversed(runs):
        try:
            doc = seal.read_topics(project_root, rid, ai_root=ai_root, reader_kind=None)
            if doc is None:
                continue
            blocks = T.blocks_from_doc(doc)
        except Exception as e:  # noqa: BLE001
            LOG.info("시청자 훈련 주제 흐름: run 읽기 실패 %s", type(e).__name__)
            continue
        return [(int(b.start_ms), int(b.end_ms), tuple(b.keywords)) for b in blocks], rid
    return [], None


# ---------------------------------------------------------------------------- 전사 낱말 파일
def words_path(project_root: str) -> str:
    return os.path.join(os.path.abspath(project_root), *WORDS_REL)


def asr_path(project_root: str) -> str:
    return os.path.join(os.path.abspath(project_root), *ASR_REL)


def _sha256_file(path: str, limit: int) -> str | None:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            n += len(b)
            if n > limit:
                return None
            h.update(b)
    return h.hexdigest()


def _project_cal_status(project_data: Mapping | None) -> str | None:
    """project.json 활성 보정의 상태(verified·unverified·skipped). 보정이 없거나 읽지 못하면 None."""
    pj_cal = (project_data or {}).get("calibration") if isinstance(project_data, Mapping) else None
    if not pj_cal:
        return None
    try:
        from .core.timecode import calibration_from_json
        st = getattr(calibration_from_json(pj_cal), "status", None)
    except (KeyError, TypeError, ValueError):
        return None
    return st if isinstance(st, str) else None


def _expected_calibration(project_data: Mapping | None) -> tuple[int | None, int | None] | None:
    """project.json 의 활성 보정 (rev, offset_ms) — 보정이 없으면 None. 읽지 못하면 (None, None)."""
    pj_cal = (project_data or {}).get("calibration") if isinstance(project_data, Mapping) else None
    if not pj_cal:
        return None
    try:
        from .core.timecode import calibration_from_json
        cal = calibration_from_json(pj_cal)
        return int(cal.rev), int(cal.offset_ms)
    except (KeyError, TypeError, ValueError):
        return None, None


def read_words(project_root: str) -> dict | None:
    """derived\\asr-words.json 을 읽어 검사를 통과한 문서(없으면 None). 형식이 틀리면 ValueError(문제 수만)."""
    p = words_path(project_root)
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as f:
        raw = f.read(WORDS_MAX_BYTES + 1)
    if len(raw) > WORDS_MAX_BYTES:
        raise ValueError("too_big")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    doc = json.loads(raw.decode("utf-8"))
    probs = WD.validate(doc)
    if probs:
        raise ValueError(f"invalid:{len(probs)}")
    return doc


def words_status(project_root: str, project_data: Mapping | None = None) -> tuple[str, dict | None]:
    """(상태 코드, 쓸 수 있는 문서 또는 None). W_OK 일 때만 문서를 준다. 시범 이름이면 W_PILOT(아무것도 열지 않음).

    오래됨: 문서의 asr_sha256 ≠ 지금 asr.json · textnorm 판 ≠ 앱의 textnorm.VERSION · project.json 활성 보정의 판·오프셋
    ≠ 문서(보정이 project.json 에서 왔을 때). 보정 없이(source default) 만든 문서는 W_DEFAULT_CAL(쓰지 않음).
    보정이 확인 전(활성 판 상태가 verified·skipped 가 아님 — 모든 프로젝트에 자동으로 있는 판 0 이 그렇다)이면
    W_UNVERIFIED_CAL(쓰지 않음, 다시 만들어도 같으므로 REBUILD 아님). project_data 가 없으면 문서의 상태로 본다."""
    if is_pilot_name(project_root):
        return W_PILOT, None
    ap = asr_path(project_root)
    has_asr = os.path.isfile(ap)
    try:
        doc = read_words(project_root)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        LOG.info("전사 낱말 파일 읽기 실패: %s", type(e).__name__)
        return (W_BAD if has_asr else W_NO_ASR), None
    if doc is None:
        return (W_MISSING if has_asr else W_NO_ASR), None
    if (doc.get("engine") or {}).get("textnorm") != TN.VERSION:
        return W_STALE_TEXTNORM, None
    if has_asr:
        try:
            sha = _sha256_file(ap, ASR_MAX_BYTES)
        except OSError:
            sha = None
        if sha is None or sha != (doc.get("target") or {}).get("asr_sha256"):
            return W_STALE_ASR, None
    cal = doc.get("calibration") or {}
    want = _expected_calibration(project_data)
    if cal.get("source") == "project.json":
        if want is None or want != (cal.get("rev"), cal.get("offset_ms")):
            return W_STALE_CAL, None
    elif cal.get("source") == "default":
        return (W_STALE_CAL if want is not None else W_DEFAULT_CAL), None
    status = _project_cal_status(project_data) if project_data else None
    if (status or cal.get("status")) not in USABLE_CAL_STATUSES:
        return W_UNVERIFIED_CAL, None
    return W_OK, doc


def tool_available() -> bool:
    """전사 낱말 만들기 명령을 띄울 수 있나(저장소에서 도는 앱이고 엔진 파일이 있다)."""
    if getattr(sys, "frozen", False):
        return False
    from . import scenario_mix
    return os.path.isfile(os.path.join(scenario_mix.repo_root(), *ENGINE_REL))


def engine_python() -> str:
    """엔진 .venv-engine 의 해석기(있으면), 없으면 지금 앱 해석기."""
    from . import scenario_mix
    p = os.path.join(scenario_mix.repo_root(), *ENGINE_VENV_PY)
    return p if os.path.isfile(p) else sys.executable


def words_command(project_root: str, *, python: str | None = None) -> list[str] | None:
    """전사 낱말을 만드는 명령(python -m engine transcript-words --project P 와 같음). 못 띄우면 None.
    시범 이름이면 None(엔진도 읽기 전에 거부하지만 명령조차 만들지 않는다)."""
    if is_pilot_name(project_root) or not tool_available():
        return None
    from . import scenario_mix
    return [python or engine_python(), "-I", "-c", _BOOT, scenario_mix.repo_root(), "transcript-words",
            "--project", os.path.abspath(project_root)]


def run_words(cmd: Sequence[str], *, cancelled: Callable[[], bool] | None = None, popen=None) -> dict:
    """명령을 돌리고 엔진 출력 dict(+ rc, elapsed_ms). 출력은 숫자·코드만(app.scenario_mix.run_make 와 같은 길)."""
    from . import scenario_mix
    kw = {"popen": popen} if popen is not None else {}
    return scenario_mix.run_make(cmd, timeout_s=WORDS_TIMEOUT_S, cancelled=cancelled, **kw)


# ---------------------------------------------------------------------------- 채점 맥락
def build_context(project_root: str, project_data: Mapping | None = None, *,
                  words_doc: Mapping | None = None) -> tuple[dict, dict]:
    """(viewer_train_view.start(context=…) 에 줄 dict, 숫자 정보).

    context = {"topic_blocks": [(s, e, keywords)], "transcript": [{start_ms, end_ms, words}], "settings": None}.
    words_doc 를 주지 않으면 words_status 로 읽는다(W_OK 일 때만 쓴다)."""
    blocks, run_id = topic_blocks(project_root)
    code = W_OK
    if words_doc is None:
        code, words_doc = words_status(project_root, project_data)
    windows = WD.score_windows(words_doc) if words_doc is not None else []
    cal = (words_doc or {}).get("calibration") or {}
    info = {"topic_blocks": len(blocks), "topic_run": 1 if run_id else 0, "words": code,
            "transcript_windows": len(windows), "calibration_status": cal.get("status") or "-"}
    return {"topic_blocks": blocks, "transcript": windows, "settings": None}, info


__all__ = ["block_reason", "is_pilot_name", "choose_segment", "topic_blocks", "words_path", "asr_path", "read_words",
           "words_status", "tool_available", "engine_python", "words_command", "run_words", "build_context",
           "REBUILD", "W_OK", "W_MISSING", "W_NO_ASR", "W_STALE_ASR", "W_STALE_CAL", "W_STALE_TEXTNORM", "W_BAD",
           "W_DEFAULT_CAL", "W_UNVERIFIED_CAL", "USABLE_CAL_STATUSES", "WORDS_NOTE_KO", "W_PILOT", "SEG_SCENARIO", "SEG_REFERENCE", "SEG_CHAT", "DEFAULT_SPAN_MS"]
