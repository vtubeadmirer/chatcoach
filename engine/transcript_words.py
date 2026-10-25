"""시각별 전사 낱말 (08 D-20 3, 21 6-2 표 '주제 적합성 (2)').

G 시청자 훈련 채점기(app/core/viewer_score)는 '보낸 시각 앞 60~120초에 스트리머가 한 말의 낱말' 과 시청자가 쓴 채팅의
낱말이 얼마나 겹치는지 본다. asr.json 의 시각은 **영상 파일 시각**이라 채팅(VOD) 축과 다르다. 앱이 보정을 다시 계산하지 않도록
엔진이 미리 VOD 시각으로 옮긴 파일을 만든다(앱 → 엔진 경계, D-06).

방법(로컬, AI·외부 호출·모델 없음)
  1. 보정: project.json 의 활성 보정(calibration.active_rev, 03 4-2 '영상 파일 시각 = VOD 시각 + offset_ms')을
     app.core.timecode.video_to_chat_ms 로 쓴다(piecewise_linear 도 같은 함수). 명령줄 --offset-ms·--file-start-ms 가 있으면
     engine.link 와 같은 규칙으로 그것이 이긴다. 그 **전에** asr 시각에 asr_start_ms 를 더한다:
     영상 파일 시각 = asr 시각 + asr_start_ms (asr 를 영상 파일 중간부터 잘라 낸 음성으로 만들었을 때, shift_segments).
     asr_start_ms 는 asr.json 이 기억하는 값(audio.asr_start_ms — engine asr --asr-start-ms 가 적는다, 08 D-34)이고, 없으면
     --asr-start-ms(기본 0). 둘 다 있는데 다르면 asr_start_conflict 로 거부한다(resolve_asr_start — 두 번 더하지 않는다).
     --file-start-ms(클립 파일이 VOD 의 어디서 시작하나, project.json 보정과 짝)와 뜻이 다르다.
  2. 낱말: app.core.textnorm.tokens(앱 채점기와 같은 규칙). 2~12자 한글 음절·영문·숫자만 남긴다(더 긴 덩어리는 버리고 센다).
  3. 시각: 낱말 시각(asr words[].start_ms)이 있으면 그 낱말 하나하나를 그 시각의 창에 넣는다. 없으면 구간 낱말 전체를
     구간 [start, end) 가 걸친 모든 창에 넣는다(길이 0 구간은 시작 창 하나).
  4. 창: VOD 0 에 맞춘 window_ms(기본 10초) 창. 창마다 낱말 **집합**을 글자 순으로 정렬해 적는다 — 말한 순서와 되풀이는
     남지 않지만, 10초마다 무슨 말을 했는지는 낱말 수준으로 거의 그대로 읽힌다. 그래서 이 파일은 **전사문(asr.json)과 같은
     등급의 자료**로 다룬다: 보호 구역 'derived/asr*' 안에만 두고, 시범은 봉인으로만(검토 2026-10-23, 03 4-2).
     낱말이 없는 창은 적지 않는다. VOD 0 보다 앞(보정상 방송 전)은 버리고 센다.

결과는 schemas/transcript-words.schema.json(같은 규칙을 validate 가 손으로 본다). 기본 위치 <project>\\derived\\asr-words.json
— 보호 구역 'derived/asr*' 안이라 내보내기·패키지에 들어가지 않는다. 표준 출력에는 숫자만 낸다(낱말을 찍지 않는다).
시범 프로젝트(폴더 이름 pilot-*)는 **읽기 전에** 거부한다(D-15 8, D-20 5 — 시범은 잠기기 전 시청자 훈련 메뉴가 꺼져 있다).
--asr 가 시범 폴더 안을 가리켜도 같다(from_project 도 allow_pilot 이 아니면 거부).
명령줄 --out 은 그 프로젝트 보호 구역 'derived/asr*' 안만 받는다(아니면 읽기·쓰기 전에 종료 2, out_unprotected — 공용 TEMP·
프로젝트 밖·보호 구역 밖 이름에 평문 낱말 파일이 생기지 않게, 검토 2026-10-23).
봉인 짝으로 만드는 길은 tools/engine_to_ai.py --from-project … --words --seal(봉인 함수가 있을 때만).

실행(저장소 루트. 무거운 의존성이 없어 앱 .venv 로도 돈다)
  python -m engine transcript-words --project <폴더> [--out <json>] [--asr F] [--start-ms N --end-ms N]
         [--offset-ms N] [--file-start-ms N] [--asr-start-ms N] [--window-sec 10]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Mapping, Sequence

from app.core import textnorm as TN
from app.core.timecode import Calibration, calibration_from_json, video_to_chat_ms
# 문서 형식·검사는 앱과 같이 쓰는 순수 모듈에 있다(앱은 엔진을 import 하지 않는다, D-06). 이름은 그대로 다시 내보낸다
from app.core.words_doc import (CAL_METHODS, CAL_SOURCES, CAL_STATUSES, MAX_WORD_LEN, MAX_WORDS_PER_WINDOW,  # noqa: F401
                                RUN_ID_RE, SCHEMA, SCHEMA_VERSION, TIME_AXIS, WORD_RE, validate)
from app.core.words_doc import TOP_KEYS as _TOP_KEYS  # noqa: F401
from app.core.words_doc import TOP_REQUIRED as _TOP_REQUIRED  # noqa: F401

ENGINE_NAME = "chatcoach-transcript-words"
ENGINE_VERSION = "0.1.0"
ASR_FORMAT = "chatcoach.asr"
DEFAULT_OUT_REL = os.path.join("derived", "asr-words.json")


class WordsError(Exception):
    """만들 수 없을 때. code 는 기계가 읽는 짧은 코드. 메시지에는 본문·낱말을 넣지 않는다."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


#: 입력 오류 코드(명령줄 종료 2). 그 밖의 WordsError 는 종료 1
INPUT_ERRORS = ("no_asr", "bad_asr", "bad_project", "bad_calibration", "bad_range", "bad_asr_start",
                "asr_start_conflict")


@dataclass(frozen=True)
class WordsConfig:
    """엔진 시작값(측정으로 고친다). 문턱은 채점기 쪽(viewer_score) 몫이다."""
    window_ms: int = 10000
    max_word_len: int = MAX_WORD_LEN
    max_words_per_window: int = MAX_WORDS_PER_WINDOW

    def __post_init__(self):
        if isinstance(self.window_ms, bool) or not isinstance(self.window_ms, int) or self.window_ms <= 0:
            raise ValueError("window_ms 는 양의 정수")
        if not (2 <= self.max_word_len <= MAX_WORD_LEN):
            raise ValueError(f"max_word_len 은 2~{MAX_WORD_LEN}")
        if not (1 <= self.max_words_per_window <= MAX_WORDS_PER_WINDOW):
            raise ValueError(f"max_words_per_window 는 1~{MAX_WORDS_PER_WINDOW}")

    def to_json(self) -> dict:
        return {**asdict(self), "textnorm": TN.VERSION}

    def sha256(self) -> str:
        b = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(b.encode("utf-8")).hexdigest()


# ───────────────────────────────────────────────────────────── 시범 프로젝트

def is_pilot_dir(project_dir: str | None) -> bool:
    """폴더 이름이 pilot- 로 시작하면 시범 프로젝트(engine.topics·retrieve·tools/engine_to_ai 와 같은 규칙)."""
    return bool(project_dir) and os.path.basename(os.path.abspath(project_dir)).lower().startswith("pilot-")


def inside_pilot(path: str | None) -> bool:
    """경로의 어느 폴더든 이름이 pilot- 로 시작하면 True(--out 이 시범 폴더 안을 가리키는지)."""
    if not path:
        return False
    parts = os.path.normpath(os.path.abspath(path)).replace("\\", "/").split("/")
    return any(p.lower().startswith("pilot-") for p in parts)


#: 전사와 같은 등급의 엔진 산출물(전사 낱말·창별 주제 벡터)을 평문으로 둘 수 있는 곳: 프로젝트 보호 구역 'derived/asr*'
#: (app.backup.DEFAULT_PROTECTED_ZONE 의 한 칸과 같다 — 엔진은 app.backup 을 import 하지 않아 여기에 작게 둔다).
PROTECTED_OUT_DIR = "derived"
PROTECTED_OUT_PREFIX = "asr"


def out_protected(project_dir: str, out: str) -> bool:
    """--out 이 그 프로젝트 보호 구역 'derived/asr*' 안인가(대소문자 무시). 프로젝트 밖·다른 드라이브·다른 이름이면 False.

    'derived/asr*' 는 파일 이름이 asr 로 시작하거나(derived/asr-words.json) 그런 이름의 폴더 아래(derived/asr_x/a.json)."""
    root, p = os.path.abspath(project_dir), os.path.abspath(out)
    try:
        rel = os.path.relpath(p, root)
    except ValueError:          # 다른 드라이브
        return False
    if rel == os.curdir or rel.startswith(os.pardir) or os.path.isabs(rel):
        return False
    parts = [x for x in rel.replace("\\", "/").lower().split("/") if x]
    return len(parts) >= 2 and parts[0] == PROTECTED_OUT_DIR and parts[1].startswith(PROTECTED_OUT_PREFIX)


def refuse_unprotected_out(project_dir: str, out: str, what: str) -> bool:
    """--out 이 보호 구역 밖이면 표준 출력에 오류 JSON 한 줄을 찍고 True(부르는 쪽은 종료 2). 쓰기·읽기 전에 부른다.
    tools/engine_to_ai.py 의 words_out_unprotected·vectors_out_unprotected 와 같은 규칙(검토 2026-10-23)."""
    if out_protected(project_dir, out):
        return False
    print(json.dumps({"error": "out_unprotected",
                      "detail": f"{what} 파일은 전사문과 같은 등급이라 프로젝트 보호 구역 안에만 씁니다 "
                                f"(예: --out <프로젝트>\\derived\\asr-….json)"}, ensure_ascii=False))
    return True


def _refuse_pilot(project_dir: str) -> None:
    if is_pilot_dir(project_dir):
        raise WordsError("pilot_locked", "시범 프로젝트는 시범 세션이 모두 잠기기 전에는 전사 낱말 파일을 만들지 않습니다 "
                                         "(D-15 8, D-20 5)")


# ───────────────────────────────────────────────────────────── 읽기

def _reject_const(name):
    raise ValueError(f"JSON 에 {name} 은 쓸 수 없습니다")


def _read_json(path: str) -> dict:
    with open(path, "rb") as f:
        b = f.read()
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    d = json.loads(b.decode("utf-8"), parse_constant=_reject_const)
    if not isinstance(d, dict):
        raise ValueError("JSON 객체가 아닙니다")
    return d


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def read_asr(path: str) -> tuple[list[dict], str]:
    """asr.json → (segments, sha256). 형식이 다르거나 구간 시각이 정수가 아니면 WordsError('bad_asr')."""
    segs, sha, _rec = read_asr_doc(path)
    return segs, sha


def read_asr_doc(path: str) -> tuple[list[dict], str, int | None]:
    """asr.json → (segments, sha256, 기억한 asr_start_ms 또는 None). 기억한 값이 0 이상 정수가 아니면 WordsError('bad_asr')."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise WordsError("no_asr", "asr.json 을 읽지 못했습니다") from e
    try:
        b = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
        d = json.loads(b.decode("utf-8"), parse_constant=_reject_const)
    except (UnicodeDecodeError, ValueError) as e:
        raise WordsError("bad_asr", "asr.json 이 JSON 이 아닙니다") from e
    if not isinstance(d, dict) or d.get("format") != ASR_FORMAT or not isinstance(d.get("segments"), list):
        raise WordsError("bad_asr", "chatcoach.asr 형식이 아닙니다")
    segs = []
    for i, s in enumerate(d["segments"]):
        if not isinstance(s, dict) or not _is_int(s.get("start_ms")) or not _is_int(s.get("end_ms")):
            raise WordsError("bad_asr", f"segments[{i}] 시각이 정수가 아닙니다")
        segs.append(s)
    try:
        rec = recorded_asr_start(d)
    except ValueError as e:
        raise WordsError("bad_asr", "audio.asr_start_ms 가 0 이상 정수가 아닙니다") from e
    return segs, _sha256_bytes(raw), rec


def check_asr_start(asr_start_ms) -> int:
    """asr_start_ms 검사: 0 이상 정수(bool 아님). 아니면 ValueError. engine.link·topic_vectors 도 이 함수를 쓴다."""
    if not _is_int(asr_start_ms) or asr_start_ms < 0:
        raise ValueError("asr_start_ms 는 0 이상 정수")
    return int(asr_start_ms)


# asr.json 이 기억하는 '잘라 낸 시작'(08 D-34): audio.asr_start_ms = 이 음성이 영상 파일의 몇 ms 부터인가(engine asr
# --asr-start-ms 가 적는다). 뜻은 --asr-start-ms 와 같다 — 영상 파일 시각 = asr 시각 + 이 값(보정 전). 칸이 없으면 옛 파일(0).
ASR_START_KEY = "asr_start_ms"


class AsrStartConflict(ValueError):
    """--asr-start-ms 로 준 값이 asr.json 이 기억하는 값과 다르다(두 번 더하거나 다른 값으로 덮지 않는다)."""
    code = "asr_start_conflict"


def recorded_asr_start(doc) -> int | None:
    """asr.json dict 가 기억하는 asr_start_ms(audio.asr_start_ms). 칸이 없으면 None. 칸이 있는데 0 이상 정수가 아니면 ValueError."""
    audio = doc.get("audio") if isinstance(doc, Mapping) else None
    if not isinstance(audio, Mapping) or ASR_START_KEY not in audio:
        return None
    return check_asr_start(audio[ASR_START_KEY])


def resolve_asr_start(given, recorded: int | None) -> int:
    """쓸 asr_start_ms. given(명령줄·호출자 값)이 None 이면 recorded(asr.json 이 기억하는 값, 없으면 0).
    given 이 있으면 검사하고, recorded 가 있는데 다르면 AsrStartConflict. recorded 가 None(기억 없는 asr — 시범 sealed-work
    처럼 따로 뽑아 전사한 것)이면 given 그대로. engine.link·topic_vectors·tools/find_anchors 도 이 함수를 쓴다."""
    if given is None:
        return recorded or 0
    a = check_asr_start(given)
    if recorded is not None and a != recorded:
        raise AsrStartConflict(f"asr_start_ms {a} 와 asr.json 의 audio.asr_start_ms {recorded} 가 다릅니다")
    return a


def shift_segments(segments: Sequence[Mapping], asr_start_ms: int = 0) -> list:
    """asr 구간 시각 → 영상 파일 시각: 영상 파일 시각 = asr 시각 + asr_start_ms (보정 video_to_chat_ms 보다 먼저).

    asr.json 의 시각은 wav 0초 기준이다. wav 를 영상 파일 중간부터 뽑았으면(tools/extract_audio.py --start-ms S) S 를 준다.
    구간 start_ms·end_ms 와 낱말 시각 words[].start_ms·end_ms(_word_points 가 읽는 키) 가운데 정수인 것만 옮긴다 —
    정수가 아닌 낱말 시각은 그대로 두어 _word_points 가 전처럼 구간 단위로 물러나게 한다. 0 이면 입력을 그대로(목록만 새로).
    입력 dict 는 고치지 않는다(새 dict). --file-start-ms(클립 파일이 VOD 의 어디서 시작하나)와 다른 값이다."""
    a = check_asr_start(asr_start_ms)
    if a == 0:
        return list(segments)

    def mv(d: Mapping) -> dict:
        o = dict(d)
        for k in ("start_ms", "end_ms"):
            if _is_int(o.get(k)):
                o[k] = o[k] + a
        return o

    out = []
    for s in segments:
        if not isinstance(s, Mapping):
            out.append(s)
            continue
        o = mv(s)
        ws = s.get("words")
        if isinstance(ws, list):
            o["words"] = [mv(w) if isinstance(w, Mapping) else w for w in ws]
        out.append(o)
    return out


def calibration_for(project_data: Mapping | None, *, offset_ms: int | None = None,
                    file_start_ms: int | None = None) -> tuple[Calibration, int, str]:
    """(Calibration, file_start_in_vod_ms, source). engine.link.load_project 와 같은 규칙.

    project.json 의 calibration 이 있으면 활성 판(source 'project.json'). --file-start-ms 만 주면 그 클립 시작을 되돌려 넣어
    읽는다('project.json+cli_file_start', 클립 시작을 두 번 빼지 않게). --offset-ms 를 주면 명령줄 값('cli', 단일 오프셋).
    아무것도 없으면 오프셋 0('default', 앵커로 보정 필요)."""
    pj_cal = (project_data or {}).get("calibration") or None
    cal, src, fs = Calibration(), "default", 0
    try:
        if pj_cal:
            cal, src = calibration_from_json(pj_cal), "project.json"
        if offset_ms is None and file_start_ms is not None and pj_cal:
            fs = int(file_start_ms)
            cal = calibration_from_json(pj_cal, file_start_in_vod_ms=fs)
            src = "project.json+cli_file_start"
        elif offset_ms is not None or file_start_ms is not None:
            cal = replace(cal, offset_ms=int(offset_ms) if offset_ms is not None else cal.offset_ms,
                          method="single_offset", status="unverified")
            fs = int(file_start_ms or 0)
            src = "cli"
    except (KeyError, TypeError, ValueError) as e:
        raise WordsError("bad_calibration", "project.json 의 보정 기록을 읽지 못했습니다") from e
    return cal, fs, src


def calibration_info(cal: Calibration, fs: int, source: str) -> dict:
    """결과의 calibration 칸. offset_ms 는 project.schema 꼴(file_ms = vod_ms + offset_ms, 클립 시작을 합친 값)."""
    return {"source": source, "rev": cal.rev if _is_int(cal.rev) and cal.rev >= 0 else None,
            "method": cal.method if cal.method in CAL_METHODS else "single_offset",
            "status": cal.status if cal.status in CAL_STATUSES else "unverified",
            "offset_ms": int(cal.offset_ms) - int(fs), "file_start_in_vod_ms": int(fs)}


# ───────────────────────────────────────────────────────────── 창 만들기 (순수)

def _keep(tokens, cfg: WordsConfig, stats: dict) -> list[str]:
    out = []
    for t in tokens:
        if len(t) <= cfg.max_word_len and WORD_RE.match(t):
            out.append(t)
        else:
            stats["dropped_long_words"] += 1
    return out


def _word_points(seg: Mapping) -> list[tuple[int, str]] | None:
    """낱말 시각 목록 [(file_ms, 낱말 글)] 또는 None(낱말 시각이 없거나 하나라도 정수가 아니면 구간 단위로)."""
    ws = seg.get("words")
    if not isinstance(ws, list) or not ws:
        return None
    pts = []
    for w in ws:
        if not isinstance(w, Mapping) or not _is_int(w.get("start_ms")):
            return None
        pts.append((w["start_ms"], str(w.get("word") or "")))
    return pts


def build_windows(segments: Sequence[Mapping], cal: Calibration, file_start_in_vod_ms: int = 0, *,
                  cfg: WordsConfig = WordsConfig(), start_ms: int | None = None, end_ms: int | None = None) -> dict:
    """asr 구간(영상 파일 시각) → {window_ms, range, stats, windows}. 같은 입력이면 같은 결과(정렬·집합만 씀).

    start_ms·end_ms(VOD)를 주면 그 [start, end) 안의 낱말만 넣는다. 창은 VOD 0 에 맞춘다."""
    W = cfg.window_ms
    lo = 0 if start_ms is None else max(0, int(start_ms))
    hi = None if end_ms is None else int(end_ms)
    if hi is not None and hi < lo:
        raise WordsError("bad_range", "end_ms 가 start_ms 보다 앞입니다")
    stats = {"segments": len(segments), "segments_used": 0, "word_time_segments": 0, "spread_segments": 0,
             "dropped_long_words": 0, "dropped_before_vod0": 0, "dropped_out_of_range": 0, "capped_words": 0}
    bag: dict[int, set[str]] = {}
    seg_of: dict[int, set[int]] = {}

    def put(k: int, toks: Sequence[str], si: int) -> None:
        if toks:
            bag.setdefault(k, set()).update(toks)
            seg_of.setdefault(k, set()).add(si)

    def in_range(v: int) -> bool:
        return v >= lo and (hi is None or v < hi)

    def to_vod(ms: int) -> int:
        return video_to_chat_ms(int(ms), cal, file_start_in_vod_ms)

    for si, seg in enumerate(segments):
        used = False
        pts = _word_points(seg)
        if pts is not None:
            stats["word_time_segments"] += 1
            for fms, text in pts:
                toks = _keep(sorted(TN.tokens(text)), cfg, stats)
                if not toks:
                    continue
                v = to_vod(fms)
                if v < 0:
                    stats["dropped_before_vod0"] += 1
                    continue
                if not in_range(v):
                    stats["dropped_out_of_range"] += 1
                    continue
                put(v // W, toks, si)
                used = True
        else:
            stats["spread_segments"] += 1
            toks = _keep(sorted(TN.tokens(str(seg.get("text") or ""))), cfg, stats)
            if toks:
                vs, ve = to_vod(seg["start_ms"]), to_vod(seg["end_ms"])
                if ve <= vs:
                    ve = vs + 1                                   # 길이 0 구간은 시작 시각 한 점
                if ve <= 0:
                    stats["dropped_before_vod0"] += 1
                else:
                    a, b = max(vs, lo, 0), ve if hi is None else min(ve, hi)
                    if b <= a:
                        stats["dropped_out_of_range"] += 1
                    else:
                        for k in range(a // W, (b - 1) // W + 1):
                            put(k, toks, si)
                        used = True
        if used:
            stats["segments_used"] += 1

    windows = []
    for k in sorted(bag):
        ws = sorted(bag[k])
        if len(ws) > cfg.max_words_per_window:
            # 넘치면 긴 낱말부터 남긴다(짧은 낱말이 덜 구체적이다). 같은 길이는 글자 순. 남긴 것은 다시 글자 순.
            keep = sorted(ws, key=lambda w: (-len(w), w))[:cfg.max_words_per_window]
            stats["capped_words"] += len(ws) - len(keep)
            ws = sorted(keep)
        windows.append({"start_ms": k * W, "end_ms": (k + 1) * W, "words": ws, "segments": len(seg_of[k])})
    if windows:
        r0 = windows[0]["start_ms"] if start_ms is None else lo
        r1 = windows[-1]["end_ms"] if hi is None else hi
    else:
        r0 = lo
        r1 = hi if hi is not None else lo
    stats["windows"] = len(windows)
    stats["words"] = sum(len(w["words"]) for w in windows)
    return {"window_ms": W, "range": {"start_ms": r0, "end_ms": max(r0, r1)}, "stats": stats, "windows": windows}


def build_doc(part: Mapping, *, cfg: WordsConfig, run_id: str, created_at: str, calibration: Mapping,
              project_id: str | None = None, video_no: str | None = None, asr_sha256: str | None = None) -> dict:
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "run_id": run_id, "created_at": created_at,
        "engine": {"name": ENGINE_NAME, "version": ENGINE_VERSION, "config_sha256": cfg.sha256(),
                   "textnorm": TN.VERSION},
        "target": {"project_id": project_id, "video_no": video_no, "asr_sha256": asr_sha256},
        "time_axis": TIME_AXIS, "calibration": dict(calibration),
        "window_ms": part["window_ms"], "range": dict(part["range"]), "stats": dict(part["stats"]),
        "windows": [dict(w) for w in part["windows"]],
    }


# 검사(validate)는 app.core.words_doc 에 있다(위 import — 앱이 같은 규칙으로 읽는다, 21 보고 확인 필요 '앱 쪽 읽기·검사').


# ───────────────────────────────────────────────────────────── 프로젝트에서 · 쓰기

def default_out_path(project_dir: str) -> str:
    return os.path.join(project_dir, DEFAULT_OUT_REL)


def asr_start_or_error(asr_start_ms) -> int | None:
    """check_asr_start 를 WordsError('bad_asr_start') 로(명령줄 종료 2). None(주지 않음)은 그대로 None."""
    if asr_start_ms is None:
        return None
    try:
        return check_asr_start(asr_start_ms)
    except ValueError as e:
        raise WordsError("bad_asr_start", "asr_start_ms 는 0 이상 정수입니다") from e


def resolve_or_error(given: int | None, recorded: int | None) -> int:
    """resolve_asr_start 를 WordsError('asr_start_conflict' · 'bad_asr_start') 로(명령줄 종료 2)."""
    try:
        return resolve_asr_start(given, recorded)
    except AsrStartConflict as e:
        raise WordsError(AsrStartConflict.code, "asr_start_ms 가 asr.json 의 기록과 다릅니다") from e
    except ValueError as e:
        raise WordsError("bad_asr_start", "asr_start_ms 는 0 이상 정수입니다") from e


def from_project(project_dir: str, *, asr_path: str | None = None, offset_ms: int | None = None,
                 file_start_ms: int | None = None, start_ms: int | None = None, end_ms: int | None = None,
                 cfg: WordsConfig = WordsConfig(), now: datetime | None = None, allow_pilot: bool = False,
                 asr_start_ms: int | None = None) -> dict:
    """프로젝트 폴더 → transcript-words 문서(메모리, 파일을 쓰지 않음). 시범은 읽기 전에 거부(allow_pilot 은 봉인 경로만).

    asr_path 기본 <project>\\derived\\asr.json. asr_start_ms: asr 를 영상 파일의 어느 시각부터 잘라 냈나(보정 전에 더함,
    shift_segments). None 이면 asr.json 이 기억하는 값(없으면 0), 주었는데 그 기억과 다르면 WordsError('asr_start_conflict').
    문서의 calibration 칸은 스키마가 키를 고정해 넣지 못하므로 stats.asr_start_ms(숫자)에 남긴다.
    결과가 검사를 못 넘으면 WordsError('bad_result')."""
    if not allow_pilot:
        _refuse_pilot(project_dir)
        if inside_pilot(asr_path):
            # 시범 폴더 안의 asr.json 을 다른 프로젝트 이름으로 읽어 평문 낱말 파일을 만드는 길을 막는다(검토 2026-10-23)
            raise WordsError("pilot_locked", "시범 프로젝트 안의 asr.json 은 봉인 경로로만 읽습니다 (D-15 8, D-20 5)")
    given = asr_start_or_error(asr_start_ms)
    t0 = time.perf_counter()
    pj = os.path.join(project_dir, "project.json")
    pdata: dict = {}
    if os.path.isfile(pj):
        try:
            pdata = _read_json(pj)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            raise WordsError("bad_project", "project.json 을 읽지 못했습니다") from e
    segs, asr_sha, rec = read_asr_doc(asr_path or os.path.join(project_dir, "derived", "asr.json"))
    a = resolve_or_error(given, rec)
    segs = shift_segments(segs, a)                    # asr 시각 → 영상 파일 시각(보정 전)
    cal, fs, src = calibration_for(pdata, offset_ms=offset_ms, file_start_ms=file_start_ms)
    part = build_windows(segs, cal, fs, cfg=cfg, start_ms=start_ms, end_ms=end_ms)
    now = now or datetime.now().astimezone()
    pid = pdata.get("project_id")
    vno = (pdata.get("broadcast") or {}).get("video_no") if isinstance(pdata.get("broadcast"), Mapping) else None
    doc = build_doc(part, cfg=cfg, run_id=f"words-v0-{now.strftime('%Y%m%dT%H%M%S')}",
                    created_at=now.isoformat(timespec="seconds"), calibration=calibration_info(cal, fs, src),
                    project_id=str(pid) if pid else os.path.basename(os.path.abspath(project_dir)),
                    video_no=str(vno) if vno else None, asr_sha256=asr_sha)
    doc["stats"]["asr_start_ms"] = a
    doc["stats"]["elapsed_s"] = round(time.perf_counter() - t0, 3)
    probs = validate(doc)
    if probs:
        raise WordsError("bad_result", f"결과 검사 실패 {len(probs)}건")
    return doc


def dump(doc: Mapping) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")


def write_json(doc: Mapping, path: str) -> None:
    """원자적 쓰기(임시 파일 → os.replace)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.part-{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(dump(doc))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m engine transcript-words",
                                 description="asr.json → VOD 시각으로 보정한 시각별 전사 낱말(transcript-words)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", help=f"결과 파일(기본 <project>\\{DEFAULT_OUT_REL})")
    ap.add_argument("--asr", help="asr.json 경로(기본 <project>\\derived\\asr.json)")
    ap.add_argument("--start-ms", type=int, help="VOD 시각. 이 앞 낱말은 넣지 않는다")
    ap.add_argument("--end-ms", type=int, help="VOD 시각. 이 뒤(같음 포함) 낱말은 넣지 않는다")
    ap.add_argument("--offset-ms", type=int, help="보정 오프셋(명령줄 값이 project.json 보다 앞선다)")
    ap.add_argument("--file-start-ms", type=int,
                    help="클립 파일 0초의 VOD 시각(project.json 보정과 짝). asr 를 잘라 낸 시작이 아니다")
    ap.add_argument("--asr-start-ms", type=int, default=None,
                    help="asr 가 영상 파일의 몇 ms 부터 잘라 낸 음성인지. 영상 파일 시각 = asr 시각 + 이 값(보정 전). "
                         "주지 않으면 asr.json 의 audio.asr_start_ms(없으면 0), 주었는데 그 값과 다르면 거부")
    ap.add_argument("--window-sec", type=int, default=10)
    ns = ap.parse_args(argv)
    out = ns.out or default_out_path(ns.project)
    if is_pilot_dir(ns.project) or inside_pilot(out) or inside_pilot(ns.asr):
        # 평문 파일을 쓰기 전에, 입력도 읽기 전에 거부한다(D-15 8, D-20 5)
        print(json.dumps({"error": "pilot_locked",
                          "detail": "시범 프로젝트는 잠기기 전에는 평문 전사 낱말 파일을 만들지 않습니다. 봉인 짝: "
                                    "tools/engine_to_ai.py --from-project … --words --seal"}, ensure_ascii=False))
        return 1
    if refuse_unprotected_out(ns.project, out, "전사 낱말"):
        return 2
    if ns.window_sec <= 0:
        ap.error("--window-sec 는 양수")
    try:
        doc = from_project(ns.project, asr_path=ns.asr, offset_ms=ns.offset_ms, file_start_ms=ns.file_start_ms,
                           start_ms=ns.start_ms, end_ms=ns.end_ms, cfg=WordsConfig(window_ms=ns.window_sec * 1000),
                           asr_start_ms=ns.asr_start_ms)
        write_json(doc, out)
    except WordsError as e:
        print(json.dumps({"error": e.code}, ensure_ascii=False))
        return 2 if e.code in INPUT_ERRORS else 1
    except OSError as e:
        print(json.dumps({"error": type(e).__name__}, ensure_ascii=False))
        return 2
    st, c = doc["stats"], doc["calibration"]
    print(json.dumps({"out": out, "windows": st["windows"], "words": st["words"], "segments": st["segments"],
                      "segments_used": st["segments_used"], "dropped_before_vod0": st["dropped_before_vod0"],
                      "dropped_out_of_range": st["dropped_out_of_range"], "asr_start_ms": st["asr_start_ms"],
                      "calibration_source": c["source"], "calibration_status": c["status"],
                      "elapsed_s": st["elapsed_s"]}, ensure_ascii=False))
    return 0


__all__ = ["ENGINE_NAME", "ENGINE_VERSION", "SCHEMA", "SCHEMA_VERSION", "DEFAULT_OUT_REL", "WordsConfig", "WordsError",
           "is_pilot_dir", "inside_pilot", "out_protected", "refuse_unprotected_out", "read_asr", "read_asr_doc",
           "check_asr_start", "ASR_START_KEY", "AsrStartConflict", "recorded_asr_start", "resolve_asr_start",
           "shift_segments", "calibration_for", "calibration_info", "build_windows",
           "build_doc", "validate", "default_out_path", "from_project", "dump", "write_json", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
