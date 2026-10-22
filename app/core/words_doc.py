"""시각별 전사 낱말 문서(transcript-words)의 형식과 검사 (08 D-20 3, 21 6-2 '주제 적합성 (2)').

엔진(engine/transcript_words — 만든다)과 앱(app/viewer_flow — 시청자 훈련을 열 때 읽는다)이 **같은 검사**를 쓰도록
한 곳에 둔다. 앱은 엔진을 import 하지 않는다(D-06 앱 → 엔진 경계). 엔진은 이미 app.core(textnorm·timecode)를 쓰므로
이 모듈을 import 한다. schemas/transcript-words.schema.json 과 같은 규칙을 손으로 본다(jsonschema 없이).

순수 모듈: 표준 라이브러리만, 파일·시계·난수 없음(tests/test_layers). 문제 글에는 낱말을 넣지 않는다.

  validate(doc) -> list[str]        문제 목록(빈 목록이면 통과)
  score_windows(doc) -> list[dict]  채점기(app.core.viewer_score.ScoreContext.build 의 transcript)에 바로 넣을 창
                                    [{start_ms, end_ms, words}] — 검사를 통과한 문서에만 쓴다
"""
from __future__ import annotations

import math
import re
from typing import Mapping

SCHEMA = "chatcoach/transcript-words"
SCHEMA_VERSION = 1
TIME_AXIS = "vod_ms"
MAX_WORD_LEN = 12
MAX_WORDS_PER_WINDOW = 200
WORD_RE = re.compile(r"^[0-9a-z가-힣]{2,%d}$" % MAX_WORD_LEN)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
CAL_SOURCES = ("project.json", "project.json+cli_file_start", "cli", "default")
CAL_METHODS = ("single_offset", "piecewise_linear")
CAL_STATUSES = ("verified", "unverified", "skipped")

TOP_REQUIRED = ("schema", "schema_version", "run_id", "created_at", "engine", "target", "time_axis", "calibration",
                "window_ms", "range", "windows")
TOP_KEYS = TOP_REQUIRED + ("stats",)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _sha_or_none(v) -> bool:
    return v is None or (isinstance(v, str) and bool(SHA_RE.match(v)))


def _str_or_none(v) -> bool:
    return v is None or isinstance(v, str)


def validate(doc) -> list[str]:
    """transcript-words 문서 검사. 문제 목록(빈 목록이면 통과). 문제 글에는 낱말을 넣지 않는다."""
    if not isinstance(doc, Mapping):
        return ["문서가 객체가 아님"]
    p = [f"{k} 없음" for k in TOP_REQUIRED if k not in doc]
    if set(doc) - set(TOP_KEYS):
        p.append("모르는 칸")
    if p:
        return p
    if doc["schema"] != SCHEMA:
        p.append("schema 값")
    if doc["schema_version"] != SCHEMA_VERSION or isinstance(doc["schema_version"], bool):
        p.append("schema_version 값")
    if not (isinstance(doc["run_id"], str) and RUN_ID_RE.match(doc["run_id"])):
        p.append("run_id 형식")
    if not (isinstance(doc["created_at"], str) and doc["created_at"]):
        p.append("created_at 형식")
    eng = doc["engine"]
    if not (isinstance(eng, Mapping) and set(eng) == {"name", "version", "config_sha256", "textnorm"}
            and all(isinstance(eng[k], str) and eng[k] for k in ("name", "version", "textnorm"))
            and _sha_or_none(eng["config_sha256"])):
        p.append("engine 형식")
    tg = doc["target"]
    if not (isinstance(tg, Mapping) and set(tg) == {"project_id", "video_no", "asr_sha256"}
            and _str_or_none(tg["project_id"]) and _str_or_none(tg["video_no"]) and _sha_or_none(tg["asr_sha256"])):
        p.append("target 형식")
    if doc["time_axis"] != TIME_AXIS:
        p.append("time_axis 값")
    c = doc["calibration"]
    if not (isinstance(c, Mapping)
            and set(c) == {"source", "rev", "method", "status", "offset_ms", "file_start_in_vod_ms"}
            and c["source"] in CAL_SOURCES and c["method"] in CAL_METHODS and c["status"] in CAL_STATUSES
            and (c["rev"] is None or (_is_int(c["rev"]) and c["rev"] >= 0))
            and _is_int(c["offset_ms"]) and _is_int(c["file_start_in_vod_ms"])):
        p.append("calibration 형식")
    W = doc["window_ms"]
    if not (_is_int(W) and W > 0):
        p.append("window_ms 양의 정수 아님")
        W = None
    rng = doc["range"]
    if not (isinstance(rng, Mapping) and set(rng) == {"start_ms", "end_ms"} and _is_int(rng.get("start_ms"))
            and _is_int(rng.get("end_ms")) and rng["start_ms"] <= rng["end_ms"]):
        p.append("range 형식")
    st = doc.get("stats")
    if st is not None and not (isinstance(st, Mapping) and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in st.values())):
        p.append("stats 는 숫자만")
    ws = doc["windows"]
    if not isinstance(ws, list):
        return p + ["windows 배열 아님"]
    prev = None
    for i, w in enumerate(ws):
        at = f"windows[{i}]"
        if not (isinstance(w, Mapping) and set(w) == {"start_ms", "end_ms", "words", "segments"}):
            p.append(f"{at} 칸")
            continue
        s, e = w["start_ms"], w["end_ms"]
        if not (_is_int(s) and _is_int(e) and 0 <= s < e):
            p.append(f"{at} 시각")
            continue
        if W is not None and (s % W or e - s != W):
            p.append(f"{at} 창 경계")
        if prev is not None and s < prev:
            p.append(f"{at} 순서·겹침")
        prev = e
        words = w["words"]
        if not (isinstance(words, list) and 1 <= len(words) <= MAX_WORDS_PER_WINDOW
                and all(isinstance(x, str) and WORD_RE.match(x) for x in words)):
            p.append(f"{at} words 형식")
        elif words != sorted(set(words)):
            p.append(f"{at} words 정렬·겹침")
        if not (_is_int(w["segments"]) and w["segments"] >= 1):
            p.append(f"{at} segments")
    return p


def score_windows(doc: Mapping) -> list[dict]:
    """검사를 통과한 문서 → 채점기 transcript 입력 [{start_ms, end_ms, words(tuple)}] (시각 순)."""
    return [{"start_ms": int(w["start_ms"]), "end_ms": int(w["end_ms"]), "words": tuple(w["words"])}
            for w in doc.get("windows") or ()]


__all__ = ["SCHEMA", "SCHEMA_VERSION", "TIME_AXIS", "MAX_WORD_LEN", "MAX_WORDS_PER_WINDOW", "WORD_RE", "RUN_ID_RE",
           "SHA_RE", "CAL_SOURCES", "CAL_METHODS", "CAL_STATUSES", "TOP_REQUIRED", "TOP_KEYS", "validate",
           "score_windows"]
