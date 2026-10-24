"""'파일 → 새 방송…' 한 번에 진행기 (08 D-31 2·3) — 화면 없는 입출력 층(Qt 없음, engine 패키지를 import 하지 않음).

VOD 번호와 영상 파일 하나로 다섯 단계를 차례로 돈다. 단계마다 진행 글(단계 이름·경과 시간·숫자)만 progress 로 내고,
실패하면 그 단계와 까닭 코드 하나로 멈춘다(만든 프로젝트·받은 채팅은 남긴다). 같은 번호로 다시 돌리면 이어서 한다.

| 단계 | 하는 일 | 다시 돌리면 |
|---|---|---|
| project 프로젝트 만들기 | <프로젝트 자리>\\<slug> (app.project.create — tools/new_project.py 가 부르는 함수). 제목·VOD 길이는 영상 정보 API 한 번(app.chzzk_chat.fetch_video_info), 없으면 ClipSlicer 파일 이름의 제목 → '방송 <번호>' | 같은 VOD 번호 프로젝트가 있으면 그것(project.json 이 늦게 바뀐 것) |
| chat 채팅 받기 | app.chzzk_chat.collect (tools/fetch_chat.py 가 부르는 함수, 솔트는 프로젝트의 chat\\protected\\salt.json). 받은 건수만 | chat\\messages.jsonl 이 있으면 건너뜀 |
| media 영상 연결 | app.media.media_ref(부분 지문·ffprobe) → project.json media.current (작업대 '영상 연결' 의 '처음 연결' 과 같은 규칙). ClipSlicer 이름의 시작이 0 이 아니면(클립) 그 시작만 합친 보정 판(미확인)을 더한다 — tools/calibrate.py --file-start-ms 와 같은 저장 | 같은 파일이면 건너뜀. 다른 파일이 연결돼 있으면 멈춤(media_other — 바꾸기는 작업대에서 사유와 함께) |
| anchors 보정 앵커 후보 | tools/find_anchors.py --json 을 따로 프로세스로(후보 수만, 파일 안 씀, 창은 활성 보정의 클립 시작 근처). 확정은 사람 | 늘 다시 센다. 실패해도 멈추지 않는다(경고 코드) |
| engine 엔진 | 엔진 venv(<저장소>\\.venv-engine)를 따로 프로세스로: 음성 뽑기(app.analysis.extract_audio_wav, 영상 처음부터 — 전체 또는 asr_limit_ms) → python -m engine asr → (위치를 모르면 자동 위치 추정, 아래) → tools/engine_to_ai.py --engine v0.3 --topics --words --vectors --out → 도장 | derived\\ 결과가 다 있고 도장이 지금 보정·영상·전사와 같으면 건너뜀(asr.json 만 있으면 전사를 건너뜀, 도장이 다르면 판정만 다시) |

자동 위치 추정(08 D-32). 전사 뒤·판정 전에 app.calibration.media_position 이 'unknown'(보정이 만든 그대로이고, ClipSlicer 이름에
구간이 없고, 영상 길이가 VOD 길이와 다름)이면 tools/find_anchors.py --locate 로 영상이 VOD 의 어디인지 찾는다. 서로 맞는 후보가
LOCATE_MIN_CONSISTENT 이상이면 '자동 위치 추정' 판(미확인, 앵커 없음, 차이 중앙값 − 읽기 지연)을 더하고 판정한다 — 끝 화면 전에
메인 창이 확인 화면을 연다(numbers.calibration = 'auto'). 못 찾으면 판정하지 않고 code 'calibration_needed'
(numbers.calibration = 'needed' — 멈춤이 아니다. 확인 화면에서 정하고 같은 번호로 다시 돌리면 판정한다).
도장 derived\\engine-stamp.json: 판정을 만든 보정 판·저장 오프셋·영상 판·asr.json sha256·판정 파일 이름(숫자·이름만). 판정 파일을
옮긴 뒤 마지막에 쓴다. 없거나 다르면 판정만 다시 한다(D-32 4 — 전사 낱말은 메인 창이 따로 다시 만들 수 있어 도장으로 쓰지 않는다).

엔진 결과 자리(D-31 3). 시범이 아닌 프로젝트는 봉인 없이 그 프로젝트 derived\\ 에, **화면이 읽는 이름 그대로** 둔다:
  derived\\asr.json(전사 — app.viewer_flow.ASR_REL) · derived\\link-v0.3.json(판정 — 작업대 '분석 보기' 와 세션 화면의
  '분석 세션' 조건이 derived\\link-*.json 을 본다, v0.3 가 v0.2 로 물러났으면 link-v0.2.json) · derived\\asr-topic-flow.json
  (주제 흐름 — viewer_flow.TOPICS_REL) · derived\\asr-words.json(전사 낱말 — WORDS_REL) · derived\\asr-topic-vectors.json
  (창별 벡터 — VECTORS_REL). engine_to_ai 는 보호 구역 안 derived\\asr-link.json(+ .topics·.words·.vectors)에 쓰고, 다 쓴 뒤
  위 이름으로 옮긴다(반쯤 쓴 파일이 읽는 이름으로 보이지 않게, 판정 파일을 마지막에). 전사 낱말·벡터는 전사문과 같은 등급이라
  보호 구역 'derived/asr*' 안 이름만 쓴다.
시범 프로젝트(폴더 이름 pilot-* 또는 시범 기록에 적힌 것)는 derived\\ 에 쓰지 않고 tools/pilot_engine_run.py 를 엔진 venv 로
그대로 부른다(같은 봉인 길: VOD 20:00~60:00, v0.2·v0.3 + 짝 셋, sealed-work 중간 파일 os.remove). 끝나면 화면은 시범 잠김
조건이 될 때만 연다(MainWindow.open_analysis).

시각: 음성은 늘 영상 파일 0 초부터 뽑는다(asr_start_ms 0). 그래서 derived\\asr.json 의 시각이 영상 파일 시각과 같고, 시청자 훈련의
전사 낱말 다시 만들기(engine transcript-words, asr 시작을 받지 않음)도 맞는다. 사용자가 고르는 범위는 '처음 N 분'(asr_limit_ms,
숨은 설정 settings.ini [new_broadcast] asr_limit_min — 리허설에서 쓴다)뿐이고, 그때 판정·주제 범위는 그 N 분이 덮는 VOD 구간.
중간 파일(전사 wav·쓰던 asr)은 성공·실패 모두 끝에 os.remove(휴지통 아님).

네트워크: 영상 정보·채팅 API(치지직 공개 API)만. 엔진 프로세스에는 HF_HUB_OFFLINE=1 을 주고, 전사 모델이 캐시에 없으면 받지 않고
멈춘다(whisper_model_missing). e5 모델이 없으면 벡터 없이(v0.3 은 엔진 안에서 v0.2 로 물러난다) 경고만.
출력·로그에는 숫자·코드·단계 이름만(채팅 본문·닉네임·전사문·제목·경로 없음).

바꿔 끼우는 자리(시험은 합성으로, 네트워크·엔진 없이): FETCH_INFO · COLLECT · MEDIA_REF · EXTRACT · RUN_PROCESS.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Sequence

from . import analysis, chzzk_chat, media, paths
from . import calibration as calib
from . import project as project_mod
from .core import timecode as tc

LOG = logging.getLogger("chatcoach")

STEPS = ("project", "chat", "media", "anchors", "engine")
STEP_KO = {"project": "프로젝트 만들기", "chat": "채팅 받기", "media": "영상 연결", "anchors": "보정 앵커 후보",
           "engine": "엔진"}
NEW_PURPOSE = "방송 분석·훈련(파일 → 새 방송, 이 PC 안에서만 처리)"

# derived\ 이름 — 화면이 읽는 이름 그대로(tests/test_new_broadcast.py 가 viewer_flow·workbench 와 대조한다)
ASR_REL = ("derived", "asr.json")
TOPICS_REL = ("derived", "asr-topic-flow.json")
WORDS_REL = ("derived", "asr-words.json")
VECTORS_REL = ("derived", "asr-topic-vectors.json")
LINK_GLOB = "link-*.json"
LINK_NAME = {"v0.3": "link-v0.3.json", "v0.2": "link-v0.2.json"}
STAGE_NAME = "asr-link.json"             # engine_to_ai --out (보호 구역 derived/asr*) — 다 쓰면 위 이름으로 옮긴다
STAGE_SIDES = {"topics": TOPICS_REL, "words": WORDS_REL, "vectors": VECTORS_REL}
STAMP_REL = ("derived", "engine-stamp.json")   # 판정을 만든 보정·영상·전사(숫자·이름만, D-32 4)
STAMP_FORMAT = "chatcoach.engine_stamp"
LOCATE_MIN_CONSISTENT = 3                 # 자동 위치 추정 판을 더할 '서로 맞는 후보' 수(timecode.MIN_ANCHORS_VERIFIED 와 같은 값)

ENGINE_VERSION_V03 = "link-v0.3"          # engine.link.ENGINE_VERSION_V03 (글자로 둔다 — 앱은 엔진을 import 하지 않는다)
ASR_MODEL = "large-v3-turbo"
# engine/asr.KNOWN_REPOS['large-v3-turbo'] 의 허깅페이스 캐시 폴더 · engine/semantic.E5_SMALL 의 파일(이름만 같이 둔다)
WHISPER_CACHE_DIR = "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
E5_FILES = (("multilingual-e5-small", "onnx", "model.onnx"), ("multilingual-e5-small", "onnx", "tokenizer.json"))
ENGINE_VENV_PY = (".venv-engine", "Scripts", "python.exe") if os.name == "nt" else (".venv-engine", "bin", "python")
SETUP_HINT = "엔진 venv 가 없습니다: tools\\setup_engine.ps1"
POSITIVE = ("direct", "likely")
PROJECT_JSON_MAX = 4 << 20
ANCHORS_TOP = 20
ANCHORS_TIMEOUT_S = 600.0
LINK_TIMEOUT_S = 3600.0
PILOT_TIMEOUT_S = 6 * 3600.0
TICK_S = 0.5                               # 진행 글을 다시 낼 간격(긴 전사 동안 경과 시간이 움직이게)
POLL_S = 0.25

# 까닭 코드 → 사람이 읽는 한 줄(상태 줄·경고). 코드 자체에는 내용·경로가 없다.
CODE_KO = {
    "video_no": "VOD 번호를 읽지 못했습니다",
    "video_path": "영상 경로를 받을 수 없습니다(드라이브 문자로 시작하는 로컬 파일만)",
    "video_missing": "영상 파일이 없습니다",
    "project_exists": "같은 이름의 폴더가 이미 있습니다(project.json 이 없거나 다른 방송)",
    "project_read_only": "프로젝트를 쓸 수 없습니다(읽기 전용·검증 실패·다른 창이 엶)",
    "project_write": "프로젝트 폴더를 만들지 못했습니다",
    "chat_salt": "채팅 솔트(chat\\protected\\salt.json)를 읽지 못했습니다",
    "chat_fetch": "채팅을 받지 못했습니다(네트워크·치지직 응답)",
    "chat_incomplete": "채팅을 끝까지 받지 못했습니다(받은 만큼으로 진행)",
    "media_other": "다른 영상이 이미 연결돼 있습니다 — 작업대에서 바꾸세요",
    "media_probe": "영상을 읽지 못했습니다(ffprobe)",
    "media_save": "영상 연결을 project.json 에 저장하지 못했습니다",
    "engine_venv_missing": SETUP_HINT,
    "whisper_model_missing": "전사 모델이 없습니다(<데이터>\\models — 받지 않습니다)",
    "ffmpeg_missing": "ffmpeg 가 없습니다(bin\\ffmpeg)",
    "extract": "음성을 뽑지 못했습니다(ffmpeg)",
    "asr_timeout": "전사가 시간 안에 끝나지 않았습니다",
    "asr_format": "전사 결과 형식이 맞지 않습니다",
    "asr_order": "전사 구간 순서가 조금 어긋났습니다(시각으로 다시 정렬해 씁니다)",
    "e5_missing": "의미 비교 모델이 없어 벡터 없이 했습니다(v0.3 → v0.2)",
    "engine_fallback": "v0.3 의미 비교가 돌지 않아 v0.2 로 판정했습니다",
    "tool_unavailable": "앵커 후보 도구를 띄울 수 없습니다(묶은 exe)",
    "calibration_needed": "영상이 VOD 의 어디인지 찾지 못해 판정하지 않았습니다 — 시간 정렬 화면에서 정하세요",
    "calibration_save": "자동 위치 추정 판을 project.json 에 저장하지 못했습니다",
    "cancelled": "취소했습니다",
}

_CODE_RE = re.compile(r"[^a-z0-9_:]+")
_ERR_TOKEN_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b")
# ClipSlicer '<제목> [<VOD번호>] <시작>-<끝>[_N]' 의 시각: HHMMSS 여섯 자리 또는 'HHhMMmSSs'·'MMmSSs'(tools/pilot_setup 과 같은 꼴)
_T = r"(?:\d{1,3}h)?\d{1,2}m\d{1,2}s|\d{6}"
_CLIP_RE = re.compile(rf"\[(?P<vno>\d{{1,12}})\]\s*(?:(?P<start>{_T})-(?P<end>{_T}))?")
_HMS_RE = re.compile(r"\A(?:(\d+)h)?(\d+)m(\d+)s\Z")


class StepError(RuntimeError):
    """한 단계를 멈추는 오류. 메시지는 코드뿐(경로·내용 없음)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = clean_code(code)


class _Cancelled(Exception):
    pass


def clean_code(code: str | None) -> str:
    """코드 글자: 소문자·숫자·'_'·':' 만, 60자까지(경로·내용이 섞이지 않게)."""
    c = _CODE_RE.sub("_", str(code or "error").lower()).strip("_") or "error"
    return c[:60]


# ---------------------------------------------------------------------------- 이름·자리
def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_engine_python() -> str:
    """저장소의 엔진 venv 해석기(개발 모드). 없으면 그대로 경로만(엔진 단계가 engine_venv_missing 으로 멈춘다)."""
    return os.path.join(repo_root(), *ENGINE_VENV_PY)


def engine_data_root() -> str:
    """엔진이 쓰는 데이터 폴더(engine/asr.data_root 와 같은 규칙): CHATCOACH_DATA, 없으면 저장소 옆 chatcoach-data."""
    env = os.environ.get("CHATCOACH_DATA")
    return os.path.abspath(env) if env else os.path.join(os.path.dirname(repo_root()), "chatcoach-data")


def default_models_dir() -> str:
    """모델 폴더(engine/asr.default_models_dir 와 같은 규칙): CHATCOACH_MODELS, 없으면 <엔진 데이터>\\models."""
    return os.environ.get("CHATCOACH_MODELS") or os.path.join(engine_data_root(), "models")


def default_projects_root() -> str:
    """새 방송 프로젝트를 둘 폴더.

    --data-dir 로 데이터 폴더를 정했으면 <그 폴더>\\projects(리허설·시험이 실제 자리를 건드리지 않게). 아니면 프로젝트 열기의
    시작 자리와 같은 규칙(project_view.default_projects_dir ①): 앱 폴더 옆 chatcoach-data\\projects 가 있으면 그것(소스 실행 —
    시범 프로젝트들이 있는 곳), 없으면 <데이터 폴더>\\projects(paths.projects_root)."""
    if paths.data_mode() == "override":
        return paths.projects_root()
    cand = os.path.join(os.path.dirname(paths.install_root()), "chatcoach-data", "projects")
    if os.path.isdir(cand):
        return cand
    return paths.projects_root()


def whisper_present(models_dir: str) -> bool:
    """전사 모델(large-v3-turbo)이 허깅페이스 캐시에 있는가(받지 않는다)."""
    pat = os.path.join(glob.escape(models_dir), WHISPER_CACHE_DIR, "snapshots", "*", "model.bin")
    return bool(glob.glob(pat))


def e5_present(models_dir: str) -> bool:
    """의미 비교·벡터 모델(multilingual-e5-small onnx)이 있는가."""
    return all(os.path.isfile(os.path.join(models_dir, *p)) for p in E5_FILES)


def is_pilot(project_root: str) -> bool:
    """시범 프로젝트인가: 폴더 이름 pilot-* 또는 시범 기록(freeze.json·setup.json)에 적힘(app.reference_set). 판단이 안 되면
    시범으로 본다(봉인 길 — 블라인드 쪽으로 안전하게)."""
    if os.path.basename(os.path.abspath(project_root)).lower().startswith("pilot-"):
        return True
    try:
        from . import reference_set as rs
        return bool(rs.is_pilot_project(project_root))
    except Exception as e:  # noqa: BLE001
        LOG.warning("새 방송: 시범 판단 실패 %s — 봉인 길로", type(e).__name__)
        return True


def find_projects(projects_root: str, video_no: str) -> list[str]:
    """projects_root 바로 아래에서 broadcast.video_no 가 같은 프로젝트 폴더(project.json 이 늦게 바뀐 것이 앞).
    폴더 이름이 '-<번호>' 로 끝나는 것만 연다(slug 규칙 '<제목>-<번호>', 시범 'pilot-<번호>-<번호>')."""
    try:
        names = os.listdir(projects_root)
    except OSError:
        return []
    found: list[tuple[float, str]] = []
    for name in names:
        if not name.endswith("-" + video_no):
            continue
        root = os.path.join(projects_root, name)
        pj = os.path.join(root, project_mod.PROJECT_FILE)
        try:
            with open(pj, "rb") as f:
                d = json.loads(f.read(PROJECT_JSON_MAX + 1).decode("utf-8-sig"))
            mtime = os.path.getmtime(pj)
        except (OSError, ValueError):
            continue
        b = d.get("broadcast") if isinstance(d, dict) else None
        if isinstance(b, dict) and str(b.get("video_no")) == video_no:
            found.append((mtime, root))
    found.sort(key=lambda x: (-x[0], x[1]))
    return [r for _t, r in found]


def _clip_time(tok: str | None) -> int | None:
    if not tok:
        return None
    if len(tok) == 6 and tok.isdigit():
        h, m, s = int(tok[:2]), int(tok[2:4]), int(tok[4:])
    else:
        mm = _HMS_RE.match(tok)
        if not mm:
            return None
        h, m, s = int(mm.group(1) or 0), int(mm.group(2)), int(mm.group(3))
    if m > 59 or s > 59:
        return None
    return ((h * 60 + m) * 60 + s) * 1000


def parse_video_file_name(path: str | None) -> tuple[str | None, int | None]:
    """영상 파일 이름 → (VOD 번호, 클립 시작 ms). ClipSlicer 이름 '<제목> [<VOD번호>] <시작>-<끝>[_N].mp4' 만 읽는다.
    번호가 없으면 (None, None), 시각이 없거나 끝 ≤ 시작이면 시작 None. 번호는 앞의 0 을 뗀다(0 은 번호가 아님)."""
    if not path:
        return None, None
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    m = _CLIP_RE.search(stem)
    if not m:
        return None, None
    vno = str(int(m.group("vno")))
    if vno == "0":
        return None, None
    a, b = _clip_time(m.group("start")), _clip_time(m.group("end"))
    return vno, (a if a is not None and b is not None and b > a else None)


def title_from_file_name(path: str | None) -> str | None:
    """ClipSlicer 이름의 '[번호]' 앞 제목(없으면 None). 프로젝트 제목이 영상 정보 API 에서 오지 않을 때만 쓴다."""
    if not path:
        return None
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    m = _CLIP_RE.search(stem)
    if not m:
        return None
    t = "".join(ch for ch in stem[:m.start()] if ch.isprintable()).strip()
    return t or None


def derived_path(project_root: str, rel: Sequence[str]) -> str:
    return os.path.join(os.path.abspath(project_root), *rel)


def find_link(derived_dir: str) -> str | None:
    """derived\\link-*.json 가운데 가장 최근(수정 시각) 파일 — 작업대 workbench.find_link_file 과 같은 고르기."""
    files = [p for p in glob.glob(os.path.join(glob.escape(derived_dir), LINK_GLOB)) if os.path.isfile(p)]
    return max(files, key=lambda p: (os.path.getmtime(p), p)) if files else None


def engine_outputs(project_root: str, *, need_vectors: bool = True) -> dict:
    """derived\\ 의 엔진 결과가 다 있는가(파일 이름만 본다). {asr, link, topics, words, vectors, complete}."""
    d = os.path.join(os.path.abspath(project_root), "derived")
    have = {"asr": os.path.isfile(derived_path(project_root, ASR_REL)), "link": find_link(d) is not None,
            "topics": os.path.isfile(derived_path(project_root, TOPICS_REL)),
            "words": os.path.isfile(derived_path(project_root, WORDS_REL)),
            "vectors": os.path.isfile(derived_path(project_root, VECTORS_REL))}
    have["complete"] = all(have[k] for k in ("asr", "link", "topics", "words")) and (have["vectors"] or not need_vectors)
    return have


def asr_summary(path: str) -> dict | None:
    """asr.json 이 chatcoach.asr 형식이면 {segments, words, duration_ms}(숫자만), 아니면 None."""
    try:
        with open(path, "rb") as f:
            d = json.loads(f.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("format") != "chatcoach.asr" or not isinstance(d.get("segments"), list):
        return None
    segs = d["segments"]
    words = sum(len(s.get("words") or []) for s in segs if isinstance(s, dict))
    dur = (d.get("audio") or {}).get("duration_ms") if isinstance(d.get("audio"), dict) else None
    return {"segments": len(segs), "words": words, "duration_ms": dur if isinstance(dur, int) else None}


def grade_counts(link_path: str) -> dict:
    """판정 파일(ai-result) → {graded, direct, likely, related_only, unclear, unconfirmed} 숫자. 읽지 못하면 빈 dict."""
    try:
        with open(link_path, "rb") as f:
            d = json.loads(f.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        return {}
    out = {"graded": 0, "direct": 0, "likely": 0, "related_only": 0, "unclear": 0, "unconfirmed": 0}
    for r in (d.get("reactions") or []) if isinstance(d, dict) else []:
        if not isinstance(r, dict):
            continue
        if r.get("result") == "graded":
            out["graded"] += 1
            g = r.get("grade")
            if g in out:
                out[g] += 1
        elif r.get("result") == "unconfirmed":
            out["unconfirmed"] += 1
    return out


# engine/asr.validate_asr 의 문제 글(번호·키 이름만 담는다) → 종류. 'order'(구간 시작이 앞 구간보다 이름)만 견딘다:
# 판정(engine.link.load_asr)·주제(engine.topics)는 발화를 시각으로 다시 정렬하고, 전사 낱말·벡터는 창(시각)별로 모아 순서를
# 보지 않는다. 시각이 정수가 아니거나 키가 빠진 것은 뒤 단계가 읽지 못하므로 멈춘다(asr_format:<종류>).
ASR_PROBLEM_MARKS = (("시작이 앞 구간보다 이름", "order"), ("시각이 정수·순서가 아님", "time"), ("없음", "missing"),
                     ("없는 키", "missing"), ("다름", "format"))
ASR_TOLERATED = frozenset({"order"})


def asr_problem_kinds(problems: Sequence[str]) -> set[str]:
    out = set()
    for p in problems:
        out.add(next((k for mark, k in ASR_PROBLEM_MARKS if mark in str(p)), "other"))
    return out


def count_lines(path: str) -> int:
    """JSONL 줄 수(본문을 해석하지 않는다)."""
    n, last = 0, b"\n"
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            n += b.count(b"\n")
            last = b[-1:]
    return n + (0 if last == b"\n" else 1)


def fmt_s(sec: float) -> str:
    s = max(0, int(sec))
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------------------- 따로 프로세스
@dataclass
class ProcResult:
    rc: int | None
    doc: dict                                # 마지막 JSON 객체(없으면 {})
    docs: list = field(default_factory=list)  # 표준 출력의 JSON 객체 전부(줄마다, 또는 들여 쓴 하나)
    elapsed_s: float = 0.0
    error: str | None = None                 # spawn_failed · timeout · cancelled
    err_code: str | None = None              # 표준 오류 마지막 예외 이름(있으면) — 내용은 남기지 않는다


def _decode(b: bytes | None) -> str:
    for enc in ("utf-8", "cp949"):
        try:
            return (b or b"").decode(enc)
        except UnicodeDecodeError:
            continue
    return (b or b"").decode("utf-8", errors="replace")


def parse_json_output(text: str) -> list[dict]:
    """표준 출력 → JSON 객체 목록. 들여 쓴 객체 하나(engine asr)면 그것, 아니면 줄마다(engine_to_ai·pilot_engine_run)."""
    t = (text or "").strip()
    i = t.find("{")
    if i >= 0:
        try:
            obj = json.loads(t[i:])
            if isinstance(obj, dict):
                return [obj]
        except ValueError:
            pass
    out = []
    for line in t.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _kill_tree(proc) -> None:
    """자식과 그 아래 프로세스까지 끝낸다(pilot_engine_run 은 전사를 손자 프로세스로 띄운다)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def run_process(cmd: Sequence[str], *, cancelled: Callable[[], bool] | None = None,
                tick: Callable[[float], None] | None = None, timeout_s: float | None = None,
                env: Mapping[str, str] | None = None, cwd: str | None = None) -> ProcResult:
    """명령을 돌리고 표준 출력의 JSON 을 읽는다. 표준 입력은 막고 창은 띄우지 않는다. tick(경과 초)은 기다리는 동안
    POLL_S 마다. 취소·시간 초과면 프로세스 나무를 끝낸다. 표준 오류는 마지막 예외 이름만 남긴다."""
    t0 = time.monotonic()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(list(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=cwd or repo_root(), env=dict(env) if env is not None else None, creationflags=flags)
    except OSError as e:
        return ProcResult(None, {}, [], 0.0, "spawn_failed", type(e).__name__)
    while True:
        try:
            out, err = proc.communicate(timeout=POLL_S)
            break
        except subprocess.TimeoutExpired:
            el = time.monotonic() - t0
            if tick is not None:
                tick(el)
            if cancelled is not None and cancelled():
                _kill_tree(proc)
                return ProcResult(None, {}, [], round(el, 1), "cancelled")
            if timeout_s is not None and el > timeout_s:
                _kill_tree(proc)
                return ProcResult(None, {}, [], round(el, 1), "timeout")
    docs = parse_json_output(_decode(out))
    errs = _ERR_TOKEN_RE.findall(_decode(err)[-4000:])
    return ProcResult(proc.returncode, docs[-1] if docs else {}, docs, round(time.monotonic() - t0, 1), None,
                      errs[-1][:40] if errs else None)


def tool_env() -> dict:
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return env


def engine_env() -> dict:
    """엔진 프로세스 환경: 허깅페이스에 접속하지 않는다(모델은 캐시에 있는 것만, D-31 네트워크)."""
    env = tool_env()
    env.update({"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "TRANSFORMERS_OFFLINE": "1"})
    return env


def anchors_python(engine_python: str | None = None) -> str | None:
    """앵커 후보 도구를 띄울 해석기: 엔진 venv 가 있으면 그것, 없으면 지금 앱 해석기(묶은 exe 면 None — 도구가 없다)."""
    p = engine_python or default_engine_python()
    if os.path.isfile(p):
        return p
    if getattr(sys, "frozen", False):
        return None
    return sys.executable


# ---------------------------------------------------------------------------- 바꿔 끼우는 자리
def _video_info_default(video_no: str) -> dict:
    return chzzk_chat.fetch_video_info(video_no)


FETCH_INFO: Callable[[str], dict] = _video_info_default
COLLECT: Callable[..., object] = chzzk_chat.collect
MEDIA_REF: Callable[..., dict] = media.media_ref
EXTRACT: Callable[..., dict] = analysis.extract_audio_wav
RUN_PROCESS: Callable[..., ProcResult] = run_process


# ---------------------------------------------------------------------------- 요청·결과
@dataclass
class Request:
    video_no: str
    video_path: str
    labeler_id: str
    projects_root: str
    asr_limit_ms: int | None = None          # 처음 N ms 만 전사(None = 영상 전체). 리허설용 숨은 설정
    engine_python: str | None = None         # None → default_engine_python()
    models_dir: str | None = None            # None → default_models_dir()
    device: str = "cuda"
    compute_type: str = "float16"


@dataclass
class Step:
    key: str
    status: str = "pending"                  # done · skipped · failed · cancelled
    elapsed_s: float = 0.0
    code: str | None = None                  # 실패 까닭, 또는 끝났지만 남길 경고(chat_incomplete·e5_missing …)
    numbers: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return STEP_KO.get(self.key, self.key)


@dataclass
class Result:
    video_no: str | None = None
    project_root: str | None = None
    pilot: bool = False
    steps: list[Step] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def stopped(self) -> Step | None:
        return next((s for s in self.steps if s.status in ("failed", "cancelled")), None)

    @property
    def ok(self) -> bool:
        return len(self.steps) == len(STEPS) and all(s.status in ("done", "skipped") for s in self.steps)

    @property
    def cancelled(self) -> bool:
        s = self.stopped
        return s is not None and s.status == "cancelled"

    def step(self, key: str) -> Step | None:
        return next((s for s in self.steps if s.key == key), None)

    def number(self, key: str, name: str):
        s = self.step(key)
        return s.numbers.get(name) if s is not None else None

    @property
    def chats(self):
        return self.number("chat", "messages")

    @property
    def utterances(self):
        return self.number("engine", "utterances")

    @property
    def direct(self):
        return self.number("engine", "direct")

    @property
    def calibration(self) -> str | None:
        """엔진 단계가 남긴 보정 표시: 'auto'(자동 위치 추정 판, 사람 확인 전) · 'needed'(자리를 못 찾아 판정 안 함) · None."""
        v = self.number("engine", "calibration")
        return v if v in ("auto", "needed") else None

    def line(self) -> str:
        """상태 줄 한 줄(숫자만): '새 방송 끝: 채팅 n · 발화 n · 반응(direct) n' 또는 멈춘 단계와 까닭."""
        def num(v) -> str:
            return f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else "-"
        if self.ok and self.calibration == "needed":
            return (f"새 방송: 판정 전 — 영상이 VOD 의 어디인지 정해야 합니다 · 채팅 {num(self.chats)} · "
                    f"발화 {num(self.utterances)}")
        if self.ok:
            direct = "봉인됨" if self.pilot else num(self.direct)
            tail = " · 보정: 자동 추정(확인 전)" if self.calibration == "auto" else ""
            return f"새 방송 끝: 채팅 {num(self.chats)} · 발화 {num(self.utterances)} · 반응(direct) {direct}{tail}"
        s = self.stopped
        if s is None:
            return "새 방송: 끝나지 않았습니다"
        i = STEPS.index(s.key) + 1 if s.key in STEPS else 0
        head = "새 방송 취소" if s.status == "cancelled" else "새 방송 멈춤"
        why = CODE_KO.get(s.code or "") or CODE_KO.get((s.code or "").split(":", 1)[0], "")
        return f"{head}: {i}/{len(STEPS)} {s.name} — {s.code}" + (f" ({why})" if why and s.status != "cancelled" else "")

    def summary(self) -> dict:
        """로그·리허설용 숫자 묶음(경로·내용 없음)."""
        return {"ok": self.ok, "pilot": self.pilot, "elapsed_s": round(self.elapsed_s, 1),
                "steps": [{"key": s.key, "status": s.status, "elapsed_s": s.elapsed_s, "code": s.code,
                           "numbers": dict(s.numbers)} for s in self.steps]}


# ---------------------------------------------------------------------------- 진행
class _Run:
    """한 번의 진행: 요청·진행 알림·취소와 단계 사이에 넘길 값."""

    def __init__(self, req: Request, progress, cancelled, result: Result):
        self.req, self.result = req, result
        self._progress, self._cancelled = progress, cancelled
        self.t0 = self.step_t0 = time.monotonic()
        self.index, self.key, self.sub, self.nums = 0, STEPS[0], "", ""
        self._last = 0.0
        self.last_text = ""
        self.frac = 0.0
        self.vno: str | None = None
        self.proj = None
        self.duration_ms: int | None = None
        self.info: dict | None = None
        self.clip_start_ms = 0

    @property
    def root(self) -> str:
        return self.proj.root

    def cancelled(self) -> bool:
        return bool(self._cancelled is not None and self._cancelled())

    def check(self) -> None:
        if self.cancelled():
            raise _Cancelled()

    def begin(self, index: int, key: str) -> None:
        self.index, self.key, self.sub, self.nums, self.frac = index, key, "", "", 0.0
        self.step_t0 = time.monotonic()
        self.say(force=True)

    def say(self, nums: str | None = None, *, sub: str | None = None, frac: float | None = None,
            force: bool = False) -> None:
        """진행 글: 'i/5 단계 · 부분 · 이 단계 m:ss (전체 m:ss) · 숫자'. TICK_S 보다 자주 내지 않는다(force 빼고)."""
        if nums is not None:
            self.nums = nums
        if sub is not None:
            self.sub, force = sub, True
        if frac is not None:
            self.frac = max(0.0, min(1.0, float(frac)))
        now = time.monotonic()
        if not force and now - self._last < TICK_S:
            return
        self._last = now
        parts = [f"{self.index + 1}/{len(STEPS)} {STEP_KO[self.key]}"]
        if self.sub:
            parts.append(self.sub)
        parts.append(f"{fmt_s(now - self.step_t0)} (전체 {fmt_s(now - self.t0)})")
        if self.nums:
            parts.append(self.nums)
        self.last_text = " · ".join(parts)
        if self._progress is not None:
            try:
                self._progress((self.index + self.frac) / len(STEPS), self.last_text)
            except Exception:  # noqa: BLE001 — 알림 실패로 진행을 멈추지 않는다
                LOG.exception("새 방송: 진행 알림 실패")

    def tick(self, _el: float = 0.0) -> None:
        self.say()

    def tick_cancel(self) -> bool:
        """오래 걸리는 함수의 취소 콜백 겸 진행 글(analysis·chzzk 가 0.1~0.2초마다 부른다)."""
        self.say()
        return self.cancelled()

    def video_info(self) -> dict:
        if self.info is None:
            try:
                info = FETCH_INFO(self.vno)
            except Exception as e:  # noqa: BLE001 — 제목·길이는 없어도 된다
                LOG.info("새 방송: 영상 정보 못 읽음 %s", type(e).__name__)
                info = {}
            self.info = info if isinstance(info, dict) else {}
        return self.info


def run(req: Request, *, progress: Callable[[float, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None) -> Result:
    """다섯 단계를 차례로. progress(전체 0~1, 진행 글), cancelled() → True 면 지금 하던 일을 끝내고 멈춘다.
    예외를 올리지 않는다 — 멈춘 단계·코드는 Result 에."""
    res = Result(video_no=chzzk_chat.parse_video_no(req.video_no))
    r = _Run(req, progress, cancelled, res)
    for i, key in enumerate(STEPS):
        st = Step(key)
        res.steps.append(st)
        t = time.monotonic()
        try:
            r.check()
            r.begin(i, key)
            _STEP_FN[key](r, st)
            if st.status == "pending":
                st.status = "done"
        except _Cancelled:
            st.status, st.code = "cancelled", "cancelled"
        except StepError as e:
            st.status, st.code = "failed", e.code
        except Exception as e:  # noqa: BLE001 — 어느 단계든 예상 못 한 실패는 예외 이름만 남긴다
            LOG.exception("새 방송: %s 단계 예외", key)
            st.status, st.code = "failed", clean_code(type(e).__name__)
        finally:
            st.elapsed_s = round(time.monotonic() - t, 1)
        LOG.info("새 방송: %s %s %.1f초%s", key, st.status, st.elapsed_s, f" ({st.code})" if st.code else "")
        if st.status in ("failed", "cancelled"):
            break
    res.elapsed_s = round(time.monotonic() - r.t0, 1)
    return res


# ---------------------------------------------------------------------------- (a) 프로젝트
def _step_project(r: _Run, st: Step) -> None:
    req = r.req
    r.vno = chzzk_chat.parse_video_no(req.video_no)
    if not r.vno:
        raise StepError("video_no")
    found = find_projects(req.projects_root, r.vno)
    st.numbers["found"] = len(found)
    if found:
        proj = project_mod.open_project(found[0])
        st.status = "skipped"
    else:
        r.say(sub="영상 정보")
        info = r.video_info()
        title = info.get("title") or title_from_file_name(req.video_path) or f"방송 {r.vno}"
        dur = info.get("duration_ms") if isinstance(info.get("duration_ms"), int) else None
        try:
            proj = project_mod.create(req.projects_root, title, r.vno, NEW_PURPOSE, req.labeler_id,
                                      duration_ms=dur or 0)
        except FileExistsError:
            raise StepError("project_exists") from None
        except ValueError:
            raise StepError("project_create") from None
        except OSError:
            raise StepError("project_write") from None
        st.status = "done"
    if proj.data is None or proj.read_only:
        raise StepError("project_read_only")
    r.proj = proj
    r.result.project_root = proj.root
    r.result.pilot = is_pilot(proj.root)
    dur = ((proj.data or {}).get("broadcast") or {}).get("duration_ms")
    if isinstance(dur, int) and not isinstance(dur, bool) and dur > 0:
        r.duration_ms = dur
    elif r.info and isinstance(r.info.get("duration_ms"), int):
        r.duration_ms = r.info["duration_ms"]
    st.numbers.update(pilot=1 if r.result.pilot else 0,
                      vod_s=(r.duration_ms // 1000) if r.duration_ms else None)


# ---------------------------------------------------------------------------- (b) 채팅
def _step_chat(r: _Run, st: Step) -> None:
    mp = r.proj.paths.messages_jsonl
    if os.path.isfile(mp):
        st.status = "skipped"
        st.numbers["messages"] = count_lines(mp)
        return
    try:
        salt = r.proj.salt()
    except (OSError, ValueError):
        raise StepError("chat_salt") from None
    dur = r.duration_ms
    if dur is None:
        r.say(sub="영상 정보")
        d = r.video_info().get("duration_ms")
        dur = d if isinstance(d, int) and d > 0 else None

    def on_progress(frac: float, pages: int, msgs: int, _cursor_ms: int) -> None:
        r.say(f"{msgs:,}건 · {pages:,}쪽", frac=frac)
    r.say(sub="받는 중")
    try:
        s = COLLECT(r.vno, r.root, salt, duration_ms=dur, on_progress=on_progress, cancel=r.tick_cancel)
    except chzzk_chat.ChatFetchCancelled:
        raise _Cancelled() from None
    except chzzk_chat.ChatFetchError:
        raise StepError("chat_fetch") from None
    st.numbers.update(messages=int(s.messages), pages=int(s.pages), viewers=int(s.viewers),
                      complete=1 if s.complete else 0)
    r.say(f"{int(s.messages):,}건 · {int(s.pages):,}쪽", frac=1.0, force=True)
    if not s.complete:
        st.code = "chat_incomplete"
    st.status = "done"


# ---------------------------------------------------------------------------- (c) 영상
_default_calibration = calib.is_default_calibration    # 활성 보정이 만든 그대로(판 0 · 오프셋 0 · 앵커 없음 · 미확인)인가


def _step_media(r: _Run, st: Step) -> None:
    path = r.req.video_path
    if media.check_path_string(path):
        raise StepError("video_path")
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        raise StepError("video_missing")
    vno, clip = parse_video_file_name(path)
    r.clip_start_ms = clip if clip and vno == r.vno else 0
    cur = ((r.proj.data or {}).get("media") or {}).get("current")
    if isinstance(cur, Mapping) and cur:
        try:
            size = os.path.getsize(path)
            same = cur.get("size") == size and cur.get("partial_fp") == media.partial_fp(path, size)
        except (OSError, ValueError):
            raise StepError("media_probe") from None
        if not same:
            raise StepError("media_other")
        st.status = "skipped"
        dur = (cur.get("probe") or {}).get("duration_ms") if isinstance(cur.get("probe"), Mapping) else None
        st.numbers.update(duration_s=(dur // 1000) if isinstance(dur, int) else None,
                          media_rev=((r.proj.data or {}).get("media") or {}).get("media_rev"))
        return
    r.say(sub="지문·ffprobe")
    try:
        ref = MEDIA_REF(path, r.root)
    except (analysis.AnalysisError, OSError, ValueError):
        raise StepError("media_probe") from None
    r.check()
    by = r.req.labeler_id
    clip_start = r.clip_start_ms
    added = {"cal_rev": None}

    def mutate(d: dict) -> None:
        m = d.setdefault("media", {"media_rev": 0, "current": None, "history": []})
        m["media_rev"] = int(m.get("media_rev", 0)) + 1
        m["current"] = ref
        if clip_start and _default_calibration(d.get("calibration")):
            # 클립(파일 0초 = VOD clip_start): 그 시작만 합친 보정 판(앵커 없음·미확인). calibrate.py --file-start-ms 와 같은 저장
            rev = tc.next_rev(d.get("calibration"))
            cal = tc.Calibration(offset_ms=0, rev=rev, method="single_offset", media_rev=m["media_rev"],
                                 status="unverified", created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                                 created_by=by, note=f"file_start_in_vod_ms={clip_start} (파일 이름에서, offset_ms 에 합쳐 "
                                                     "저장) · 앵커 없음 · 새 방송")
            d["calibration"] = tc.append_rev(d.get("calibration"), tc.calibration_to_json(cal, clip_start))
            added["cal_rev"] = rev
    try:
        r.proj.update(mutate, by=by)
    except (project_mod.ConflictError, project_mod.ReadOnlyError, ValueError, OSError):
        raise StepError("media_save") from None
    dur = (ref.get("probe") or {}).get("duration_ms")
    st.numbers.update(duration_s=(dur // 1000) if isinstance(dur, int) else None,
                      media_rev=(r.proj.data.get("media") or {}).get("media_rev"),
                      clip_start_s=clip_start // 1000 if clip_start else 0, calibration_rev=added["cal_rev"])
    if isinstance(dur, int):
        r.say(f"영상 {fmt_s(dur / 1000)}", frac=1.0, force=True)
    st.status = "done"


# ---------------------------------------------------------------------------- (d) 앵커 후보
def _step_anchors(r: _Run, st: Step) -> None:
    py = anchors_python(r.req.engine_python)
    tool = os.path.join(repo_root(), "tools", "find_anchors.py")
    if py is None or not os.path.isfile(tool):
        st.status, st.code = "skipped", "tool_unavailable"
        return
    cmd = [py, "-I", tool, "--project", r.root, "--json", "--top", str(ANCHORS_TOP)]
    asr = derived_path(r.root, ASR_REL)
    if not r.result.pilot and asr_summary(asr) is not None:
        cmd += ["--asr", asr]                       # 다시 돌릴 때 전사가 있으면 (a) 발화 기준 후보
    cal = (r.proj.data or {}).get("calibration")
    # 활성 보정이 말하는 클립 시작(자동 추정·확정 판 포함). 만든 그대로면 파일 이름의 클립 시작
    fs = r.clip_start_ms if calib.is_default_calibration(cal) else calib.approx_file_start_ms(r.proj.data)
    if fs:
        cmd += ["--file-start-ms", str(fs)]
    p = RUN_PROCESS(cmd, cancelled=r.cancelled, tick=r.tick, timeout_s=ANCHORS_TIMEOUT_S, env=tool_env())
    if p.error == "cancelled":
        raise _Cancelled()
    if p.rc != 0 or not isinstance(p.doc.get("candidates"), int):
        st.code = clean_code("anchors_" + str(p.doc.get("error") or p.error or f"exit_{p.rc}"))
        st.status = "done"                          # 후보는 제안일 뿐 — 실패해도 엔진으로 간다
        return
    st.numbers.update(candidates=p.doc["candidates"], mode=p.doc.get("mode"),
                      consistent=p.doc.get("consistent"), chats=p.doc.get("chats"))
    r.say(f"후보 {p.doc['candidates']}개", frac=1.0, force=True)
    st.status = "done"


# ---------------------------------------------------------------------------- (e) 엔진
def _vod_range_for_limit(r: _Run, limit_ms: int) -> tuple[int, int]:
    """영상 처음 limit_ms 가 덮는 VOD(채팅 시각) 구간 — project.json 활성 보정으로."""
    try:
        cal = tc.calibration_from_json((r.proj.data or {}).get("calibration")) \
            if (r.proj.data or {}).get("calibration") else tc.Calibration()
    except (KeyError, TypeError, ValueError):
        cal = tc.Calibration()
    a, b = tc.video_to_chat_ms(0, cal), tc.video_to_chat_ms(int(limit_ms), cal)
    return max(0, min(a, b)), max(0, max(a, b))


def _remove(paths_: Sequence[str]) -> int:
    n = 0
    for p in paths_:
        try:
            if os.path.isfile(p):
                os.remove(p)                        # 휴지통 아님(전사 wav 는 중간 파일, D-15 8)
                n += 1
        except OSError:
            LOG.warning("새 방송: 중간 파일을 지우지 못함")
    return n


def _transcribe(r: _Run, st: Step, py: str, models: str, asr: str) -> None:
    d = os.path.dirname(asr)
    tag = f"{os.getpid()}-{int(time.time())}"
    wav = os.path.join(d, f"asr-audio.{tag}.wav")       # 보호 구역(derived/asr*) 안의 중간 파일
    part = os.path.join(d, f"asr.{tag}.part.json")
    limit = r.req.asr_limit_ms if r.req.asr_limit_ms and r.req.asr_limit_ms > 0 else None
    try:
        r.say(sub="음성 뽑기", frac=0.02)
        t = time.monotonic()
        try:
            ex = EXTRACT(os.path.normpath(r.req.video_path), wav, sample_rate=16000, start_ms=None, dur_ms=limit,
                         cancel=r.tick_cancel)
        except analysis.Cancelled:
            raise _Cancelled() from None
        except (analysis.AnalysisError, OSError, ValueError):
            raise StepError("extract") from None
        audio_ms = int(ex.get("duration_ms") or 0)
        st.numbers.update(audio_s=audio_ms // 1000, extract_s=round(time.monotonic() - t, 1),
                          limit_s=(limit // 1000) if limit else None)
        r.check()
        r.say(f"음성 {fmt_s(audio_ms / 1000)}", sub="전사", frac=0.1)
        cmd = [py, "-m", "engine", "asr", wav, "--out", part, "--model", ASR_MODEL, "--device", r.req.device,
               "--compute-type", r.req.compute_type, "--models-dir", models]
        t = time.monotonic()
        p = RUN_PROCESS(cmd, cancelled=r.cancelled, tick=r.tick, timeout_s=1800.0 + 4.0 * audio_ms / 1000,
                        env=engine_env())
        if p.error == "cancelled":
            raise _Cancelled()
        if p.error == "timeout":
            raise StepError("asr_timeout")
        stats = p.doc.get("stats") if isinstance(p.doc.get("stats"), Mapping) else None
        probs = [x for x in (p.doc.get("format_problems") or []) if isinstance(x, str)]
        # engine asr 는 형식 문제가 있으면 asr.json 을 다 쓴 뒤 종료 1 — 그때는 문제 종류로 가른다
        if stats is None or p.rc not in (0, 1) or (p.rc == 1 and not probs):
            raise StepError(f"asr_exit_{p.rc}" + (f":{p.err_code}" if p.err_code else ""))
        if probs:
            kinds = asr_problem_kinds(probs)
            st.numbers.update(asr_problems=len(probs), asr_problem_kinds=",".join(sorted(kinds)))
            if not kinds <= ASR_TOLERATED:
                raise StepError("asr_format:" + sorted(kinds - ASR_TOLERATED)[0])
            st.code = "asr_order"
        if not os.path.isfile(part):
            raise StepError("asr_missing")
        os.replace(part, asr)
        tm = p.doc.get("timing") if isinstance(p.doc.get("timing"), Mapping) else {}
        st.numbers.update(utterances=stats.get("segment_count"), words=stats.get("word_count"),
                          asr_s=round(time.monotonic() - t, 1), device=p.doc.get("device"),
                          asr_fallback=1 if p.doc.get("fallback") else 0, realtime_x=tm.get("realtime_x"))
        r.say(f"발화 {int(stats.get('segment_count') or 0):,}", force=True)
    finally:
        left = [wav, part] + glob.glob(glob.escape(wav) + ".part-*")
        st.numbers["removed"] = st.numbers.get("removed", 0) + _remove(left)


def _link(r: _Run, st: Step, py: str, models: str, e5: bool) -> str:
    d = os.path.join(r.root, "derived")
    stage = os.path.join(d, STAGE_NAME)
    stem = os.path.splitext(stage)[0]
    sides = {k: f"{stem}.{k}.json" for k in STAGE_SIDES}
    _remove([stage] + list(sides.values()))            # 지난 실행이 남긴 반쯤 쓴 파일
    cmd = [py, os.path.join(repo_root(), "tools", "engine_to_ai.py"), "--from-project", r.root, "--out", stage,
           "--engine", "v0.3", "--topics", "--words", "--models-dir", models]
    if e5:
        cmd.append("--vectors")
    limit = r.req.asr_limit_ms if r.req.asr_limit_ms and r.req.asr_limit_ms > 0 else None
    if limit:
        a, b = _vod_range_for_limit(r, limit)
        cmd += ["--start-ms", str(a), "--end-ms", str(b)]
    r.say(sub="판정·주제·낱말·벡터" if e5 else "판정·주제·낱말", frac=0.8)
    t = time.monotonic()
    p = RUN_PROCESS(cmd, cancelled=r.cancelled, tick=r.tick, timeout_s=LINK_TIMEOUT_S, env=engine_env())
    try:
        if p.error == "cancelled":
            raise _Cancelled()
        if p.error == "timeout":
            raise StepError("link_timeout")
        if p.rc != 0 or "out" not in p.doc:
            raise StepError("link_" + str(p.doc.get("code") or p.doc.get("error") or f"exit_{p.rc}"))
        kinds = ["topics", "words"] + (["vectors"] if e5 else [])
        for k in kinds:
            if not os.path.isfile(sides[k]):
                raise StepError(f"link_missing_{k}")
        for k in kinds:                                # 짝을 먼저, 판정(분석 세션 조건)을 마지막에
            os.replace(sides[k], derived_path(r.root, STAGE_SIDES[k]))
        ver = p.doc.get("engine_version")
        name = LINK_NAME["v0.3"] if ver == ENGINE_VERSION_V03 else LINK_NAME["v0.2"]
        dst = os.path.join(d, name)
        os.replace(stage, dst)
    finally:
        _remove([stage] + list(sides.values()))
    g = grade_counts(dst)
    st.numbers.update(reactions=g.get("graded"), direct=g.get("direct"), likely=g.get("likely"),
                      unconfirmed=g.get("unconfirmed"), engine_version=ver if isinstance(ver, str) else None,
                      topic_blocks=p.doc.get("topic_blocks"), word_windows=p.doc.get("word_windows"),
                      vector_windows=p.doc.get("vector_windows"), link_s=round(time.monotonic() - t, 1))
    if ver != ENGINE_VERSION_V03:
        st.code = st.code or "engine_fallback"
    return dst


def _numbers_from_outputs(project_root: str) -> dict:
    a = asr_summary(derived_path(project_root, ASR_REL)) or {}
    link = find_link(os.path.join(os.path.abspath(project_root), "derived"))
    g = grade_counts(link) if link else {}
    return {"utterances": a.get("segments"), "words": a.get("words"), "reactions": g.get("graded"),
            "direct": g.get("direct"), "likely": g.get("likely")}


# ---------------------------------------------------------------------------- 도장 (D-32 4)
def _sha256_path(path: str) -> str | None:
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
    except OSError:
        return None
    return h.hexdigest()


def stamp_doc(project_root: str, data: Mapping | None, link_name: str | None) -> dict:
    """판정을 만든 보정 판·저장 오프셋·영상 판·전사 sha256·판정 파일 이름(숫자·이름만)."""
    act = calib.active_rev((data or {}).get("calibration") if isinstance(data, Mapping) else None) or {}
    m = (data or {}).get("media") if isinstance(data, Mapping) else None
    return {"format": STAMP_FORMAT, "version": 1, "calibration_rev": act.get("rev"), "offset_ms": act.get("offset_ms"),
            "media_rev": (m or {}).get("media_rev") if isinstance(m, Mapping) else None,
            "asr_sha256": _sha256_path(derived_path(project_root, ASR_REL)), "link": link_name}


def write_stamp(project_root: str, data: Mapping | None, link_name: str) -> None:
    doc = stamp_doc(project_root, data, link_name)
    project_mod.atomic_write(derived_path(project_root, STAMP_REL),
                             json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"))


def stamp_matches(project_root: str, data: Mapping | None) -> bool:
    """도장이 있고 지금 보정·영상·전사와 같으며 그 판정 파일이 있는가. 아니면 판정을 다시 해야 한다."""
    try:
        with open(derived_path(project_root, STAMP_REL), "rb") as f:
            old = json.loads(f.read(1 << 16).decode("utf-8-sig"))
    except (OSError, ValueError):
        return False
    if not isinstance(old, dict) or old.get("format") != STAMP_FORMAT:
        return False
    name = old.get("link")
    if not isinstance(name, str) or name not in LINK_NAME.values() or \
            not os.path.isfile(os.path.join(os.path.abspath(project_root), "derived", name)):
        return False
    now = stamp_doc(project_root, data, name)
    return all(old.get(k) == now[k] for k in ("calibration_rev", "offset_ms", "media_rev", "asr_sha256"))


# ---------------------------------------------------------------------------- 자동 위치 추정 (D-32)
def _auto_locate(r: _Run, st: Step) -> bool:
    """find_anchors --locate 로 클립 자리를 찾아 '자동 위치 추정' 판을 더한다. 더했으면 True."""
    r.say(sub="자동 위치 추정", frac=0.75)
    t = time.monotonic()
    found = calib.find_candidates(r.root, locate=True, engine_python=r.req.engine_python, cancelled=r.cancelled,
                                  tick=r.tick)
    st.numbers["locate_s"] = round(time.monotonic() - t, 1)
    if found.error == "cancelled":
        raise _Cancelled()
    if found.error:
        st.numbers["locate_error"] = clean_code(found.error)
        return False
    loc = found.located or {}
    st.numbers.update(located=1 if found.located else 0, located_consistent=found.consistent,
                      located_votes=loc.get("peak_votes"))
    if not found.located or (found.consistent or 0) < LOCATE_MIN_CONSISTENT or found.median_offset_ms is None:
        return False
    by, now = r.req.labeler_id, datetime.now().astimezone().isoformat(timespec="seconds")

    def build(rev: int, media_rev: int, _d) -> dict:
        return calib.auto_rev_json(found.median_offset_ms, rev=rev, media_rev=media_rev, by=by, now_iso=now,
                                   consistent=found.consistent or 0, peak=loc.get("peak"), second=loc.get("second"))
    try:
        rj = calib.add_rev(lambda m: r.proj.update(m, by=by), build)
    except (project_mod.ConflictError, project_mod.ReadOnlyError, ValueError, OSError):
        raise StepError("calibration_save") from None
    st.numbers.update(calibration="auto", calibration_rev=rj.get("rev"),
                      file_start_s=round(-int(rj.get("offset_ms") or 0) / 1000, 1))
    r.say(f"영상 0초 ≈ VOD {fmt_s(-int(rj.get('offset_ms') or 0) / 1000)} · 서로 맞는 후보 {found.consistent}", force=True)
    return True


def _step_engine(r: _Run, st: Step) -> None:
    if r.result.pilot:
        _engine_pilot(r, st)
        return
    py = r.req.engine_python or default_engine_python()
    if not os.path.isfile(py):
        raise StepError("engine_venv_missing")
    models = os.path.abspath(r.req.models_dir or default_models_dir())
    e5 = e5_present(models)
    pos = calib.media_position(r.proj.data, r.req.video_path)
    st.numbers["position"] = pos
    if pos == "estimated":
        st.numbers["calibration"] = "auto"           # 앞 실행의 자동 추정 판이 아직 사람 확인 전 — 끝에 확인 화면
    if pos != "unknown" and engine_outputs(r.root, need_vectors=e5)["complete"] and stamp_matches(r.root, r.proj.data):
        st.status = "skipped"
        st.numbers.update(_numbers_from_outputs(r.root))
        return
    os.makedirs(os.path.join(r.root, "derived"), exist_ok=True)
    asr = derived_path(r.root, ASR_REL)
    have = asr_summary(asr)
    if have is None:
        if not whisper_present(models):
            raise StepError("whisper_model_missing")
        if paths.find_tool("ffmpeg") is None:
            raise StepError("ffmpeg_missing")
        _transcribe(r, st, py, models, asr)
    else:
        st.numbers.update(utterances=have["segments"], words=have["words"], asr_kept=1)
    r.check()
    if pos == "unknown" and not _auto_locate(r, st):
        st.numbers["calibration"] = "needed"
        st.code = "calibration_needed"               # 멈춤이 아니다 — 확인 화면에서 정한 뒤 다시 돌리면 판정한다
        r.say("판정 전 — 영상이 VOD 의 어디인지 정해야 합니다", frac=1.0, force=True)
        st.status = "done"
        return
    dst = _link(r, st, py, models, e5)
    write_stamp(r.root, r.proj.data, os.path.basename(dst))
    if not e5:
        st.code = "e5_missing"
    n = st.numbers
    r.say(f"발화 {n.get('utterances') or 0:,} · 반응 {n.get('reactions') or 0:,}(direct {n.get('direct') or 0:,})",
          frac=1.0, force=True)
    st.status = "done"


def _engine_pilot(r: _Run, st: Step) -> None:
    """시범: tools/pilot_engine_run.py 를 엔진 venv 로(봉인 길 그대로). 짝이 모두 붙은 유효한 봉인이 있으면 건너뜀."""
    from . import seal
    try:
        status = seal.seal_status(r.root)
        full = [e for e in status.entries if e.check == "valid"
                and all(getattr(e, f"{k}_sha256", None) for k in seal.PAIR_KINDS)]
    except Exception as e:  # noqa: BLE001 — 봉인 기록을 못 읽으면 도구가 다시 본다
        LOG.info("새 방송(시범): 봉인 상태 못 읽음 %s", type(e).__name__)
        full = []
    if full:
        st.status = "skipped"
        st.numbers.update(sealed=len(full))
        return
    py = r.req.engine_python or default_engine_python()
    if not os.path.isfile(py):
        raise StepError("engine_venv_missing")
    models = os.path.abspath(r.req.models_dir or default_models_dir())
    cmd = [py, os.path.join(repo_root(), "tools", "pilot_engine_run.py"), "--project", r.root, "--models-dir", models]
    r.say(sub="시범 봉인(음성 뽑기·전사·판정)")
    p = RUN_PROCESS(cmd, cancelled=r.cancelled, tick=r.tick, timeout_s=PILOT_TIMEOUT_S, env=engine_env())
    if p.error == "cancelled":
        raise _Cancelled()
    rows = [x for x in p.docs if "project" in x]
    row = rows[-1] if rows else {}
    if p.rc != 0 or not row.get("ok"):
        raise StepError("pilot_" + str(row.get("error") or p.doc.get("error") or f"exit_{p.rc}"))
    st.numbers.update(utterances=(row.get("asr") or {}).get("segments"), sealed=len(row.get("seals") or []),
                      presealed=sum(1 for x in row.get("seals") or [] if x.get("presealed")),
                      removed=row.get("removed"))
    st.status = "done"


_STEP_FN = {"project": _step_project, "chat": _step_chat, "media": _step_media, "anchors": _step_anchors,
            "engine": _step_engine}


__all__ = ["STEPS", "STEP_KO", "Request", "Result", "Step", "ProcResult", "run", "run_process", "find_projects",
           "parse_video_file_name", "title_from_file_name", "default_projects_root", "default_engine_python",
           "default_models_dir", "engine_outputs", "find_link", "asr_summary", "grade_counts", "is_pilot",
           "ASR_REL", "TOPICS_REL", "WORDS_REL", "VECTORS_REL", "LINK_GLOB", "LINK_NAME", "STAGE_NAME", "SETUP_HINT",
           "CODE_KO", "clean_code", "FETCH_INFO", "COLLECT", "MEDIA_REF", "EXTRACT", "RUN_PROCESS"]
