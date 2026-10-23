"""창별 주제 벡터 (G 둘째, 22 7-1 의 3 · 08 D-21 1 · D-17 6).

G 시청자 훈련 채점기(app/core/viewer_score)의 주제 적합성은 낱말 겹침만 본다. '뜻은 같고 낱말은 다른' 놓침을 줄이려고
엔진이 **미리** 전사의 고정 길이 창(기본 10초, VOD 0 에 맞춤 — transcript-words 와 같은 창)마다 문장 임베딩을 계산해 둔다.
앱은 임베딩을 계산하지 않는다(앱 → 엔진 경계, D-06·D-20 3). 그래서 시청자가 **쓴 글의 벡터는 없다** — 채점기는
'창 ↔ 창' 유사도로 주제 경계와 같은 주제 줄기·관련 앞선 창을 찾고, 쓴 글 쪽은 낱말 겹침(+ 동의어·2자 머리)으로 본다.

방법(로컬만, D-06·D-17 6. 외부 호출·모델 다운로드 없음)
  1. 보정: engine.transcript_words 와 같은 규칙(project.json 의 활성 보정, --offset-ms·--file-start-ms 가 이김). 그 전에
     asr 시각에 --asr-start-ms(기본 0, asr 를 영상 파일 중간부터 잘라 냈을 때 그 시작)를 더한다(TW.shift_segments).
  2. 창 글: 낱말 시각(asr words[].start_ms)이 있으면 낱말을 그 시각의 창에 시각 순으로 잇는다. 없으면 구간 글 전체를
     구간이 걸친 모든 창에 넣는다. span_ms(기본 = window_ms)가 창보다 길면 그 창 끝에서 span 만큼 앞 창들의 글을 잇는다.
     공백·기호를 뺀 글자가 min_chars 보다 적은 창은 벡터를 만들지 않는다(말 없는 창).
  3. 임베딩: engine.semantic.load_default(기본 e5-small ONNX, 해시 고정)로. 'query: ' 머리를 붙이고 평균 풀링 + L2 정규화.
  4. 저장: 창마다 정규화한 벡터를 float16(리틀엔디언, base64, 기본) 또는 소수 4자리 수 목록으로. **창 글·낱말은 담지 않는다.**
     창 시각·모델 이름·모델 해시·asr 해시·설정 해시·벡터 해시(vectors_sha256)만 함께 둔다.

벡터는 본문이 아니지만 전사에서 나온 값이고 임베딩은 어느 정도 거꾸로 풀 수 있다(검토 2026-10-23 의 transcript-words 와 같은
판단). 그래서 **전사문과 같은 등급**으로 둔다: 기본 위치 <project>\\derived\\asr-topic-vectors.json(보호 구역 derived/asr*,
내보내기·패키지에 들어가지 않음). 시범 프로젝트(폴더 이름 pilot-*)는 **읽기 전에** 거부한다. --asr·--out 이 시범 폴더 안이어도
같다. 시범의 짝 봉인은 tools/engine_to_ai.py --from-project … --vectors --seal(app.seal.seal_vectors_bytes 가 있을 때만).
명령줄 --out 은 그 프로젝트 보호 구역 'derived/asr*' 안만 받는다(아니면 읽기·모델 적재 전에 종료 2, out_unprotected —
engine.transcript_words.out_protected, engine_to_ai 의 vectors_out_unprotected 와 같은 규칙, 검토 2026-10-23).

결과는 schemas/topic-vectors.schema.json(같은 규칙을 validate 가 손으로 본다). 앱 쪽 풀기는 app/core/viewer_score.vectors_from_doc
(validate 가 그 함수로 다시 풀어 본다 — 엔진이 쓴 것을 앱이 그대로 읽는다). 표준 출력에는 숫자만 낸다.

실행(저장소 루트, **엔진 venv** — numpy·onnxruntime·tokenizers 가 있어야 한다)
  .venv-engine\\Scripts\\python -m engine topic-vectors --project <폴더> [--out <json>] [--asr F] [--start-ms N --end-ms N]
         [--offset-ms N] [--file-start-ms N] [--asr-start-ms N] [--window-sec 10] [--span-sec 10] [--encoding f16|round4]
         [--models-dir D] [--backend onnx|lmstudio]
종료 코드: 0 정상 · 1 거부(시범·결과 검사 실패) · 2 입력 오류 · 3 임베딩 실행기·모델 없음(아무것도 쓰지 않음)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import struct
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping, Sequence

from app.core import viewer_score as VS
from app.core.timecode import Calibration, video_to_chat_ms

from . import transcript_words as TW

SCHEMA = VS.TOPIC_VECTORS_SCHEMA            # "chatcoach/topic-vectors"
SCHEMA_VERSION = VS.TOPIC_VECTORS_SCHEMA_VERSION
ENGINE_NAME = "chatcoach-topic-vectors"
ENGINE_VERSION = "0.1.0"
TIME_AXIS = "vod_ms"
DEFAULT_OUT_REL = os.path.join("derived", "asr-topic-vectors.json")
ENCODINGS = {"f16": "f16le-b64", "round4": "round4"}
MAX_WINDOWS = 200_000
TOP_KEYS = ("schema", "schema_version", "run_id", "created_at", "engine", "model", "target", "time_axis",
            "calibration", "window_ms", "span_ms", "encoding", "dim", "range", "stats", "vectors_sha256", "windows")
MODEL_KEYS = ("backend", "name", "repo", "revision", "model_sha256", "dim", "prefix", "pooling")
WINDOW_KEYS = ("start_ms", "end_ms", "v")


class VectorsError(Exception):
    """만들 수 없을 때. code 는 기계가 읽는 짧은 코드. 메시지에는 본문·낱말을 넣지 않는다."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class VectorsConfig:
    """엔진 시작값. 문턱(경계·관련)은 채점기 쪽(viewer_score 의 vec_*) 몫이다."""
    window_ms: int = 10_000
    span_ms: int = 10_000            # 창 하나의 벡터가 덮는 길이(창 끝에서 앞으로). window_ms 의 배수
    min_chars: int = 4               # 공백·기호 뺀 글자가 이보다 적은 창은 벡터를 만들지 않는다
    max_chars: int = 400             # 창 글이 이보다 길면 자른다(e5 는 어차피 128 토큰에서 자른다)
    encoding: str = "f16le-b64"

    def __post_init__(self):
        for k in ("window_ms", "span_ms", "min_chars", "max_chars"):
            v = getattr(self, k)
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise ValueError(f"{k} 는 양의 정수")
        if self.span_ms < self.window_ms or self.span_ms % self.window_ms:
            raise ValueError("span_ms 는 window_ms 의 배수(1배 이상)")
        if self.encoding not in VS.VEC_ENCODINGS:
            raise ValueError(f"encoding 은 {VS.VEC_ENCODINGS} 가운데 하나")

    def to_json(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        b = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(b.encode("utf-8")).hexdigest()


# ───────────────────────────────────────────────────────────── 창 글 (메모리에서만, 파일·출력에 넣지 않는다)

def _compact_len(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum())


def window_texts(segments: Sequence[Mapping], cal: Calibration, file_start_in_vod_ms: int = 0, *,
                 cfg: VectorsConfig = VectorsConfig(), start_ms: int | None = None, end_ms: int | None = None
                 ) -> tuple[list[tuple[int, str]], dict]:
    """asr 구간(영상 파일 시각) → ([(창 번호 k, 창 글)], stats). 창 k = [k·W, (k+1)·W) VOD. 같은 입력이면 같은 결과.

    창 글은 임베딩에만 쓰고 버린다(결과 문서·표준 출력에 넣지 않는다)."""
    W = cfg.window_ms
    lo = 0 if start_ms is None else max(0, int(start_ms))
    hi = None if end_ms is None else int(end_ms)
    if hi is not None and hi < lo:
        raise VectorsError("bad_range", "end_ms 가 start_ms 보다 앞입니다")
    stats = {"segments": len(segments), "segments_used": 0, "word_time_segments": 0, "spread_segments": 0,
             "dropped_before_vod0": 0, "dropped_out_of_range": 0}
    pieces: dict[int, list[tuple[int, int, str]]] = {}     # k → [(vod_ms, 순번, 글)]
    order = 0

    def in_range(v: int) -> bool:
        return v >= lo and (hi is None or v < hi)

    def to_vod(ms: int) -> int:
        return video_to_chat_ms(int(ms), cal, file_start_in_vod_ms)

    for seg in segments:
        used = False
        pts = TW._word_points(seg)
        if pts is not None:
            stats["word_time_segments"] += 1
            for fms, text in pts:
                t = text.strip()
                if not t:
                    continue
                v = to_vod(fms)
                if v < 0:
                    stats["dropped_before_vod0"] += 1
                    continue
                if not in_range(v):
                    stats["dropped_out_of_range"] += 1
                    continue
                order += 1
                pieces.setdefault(v // W, []).append((v, order, t))
                used = True
        else:
            stats["spread_segments"] += 1
            t = str(seg.get("text") or "").strip()
            if t:
                vs, ve = to_vod(seg["start_ms"]), to_vod(seg["end_ms"])
                if ve <= vs:
                    ve = vs + 1
                if ve <= 0:
                    stats["dropped_before_vod0"] += 1
                else:
                    a, b = max(vs, lo, 0), ve if hi is None else min(ve, hi)
                    if b <= a:
                        stats["dropped_out_of_range"] += 1
                    else:
                        order += 1
                        for k in range(a // W, (b - 1) // W + 1):
                            pieces.setdefault(k, []).append((max(vs, k * W), order, t))
                        used = True
        if used:
            stats["segments_used"] += 1

    per_k = {k: " ".join(t for _v, _o, t in sorted(ps)) for k, ps in pieces.items()}
    span_n = cfg.span_ms // W
    out: list[tuple[int, str]] = []
    short = 0
    for k in sorted(per_k):
        text = " ".join(per_k[j] for j in range(k - span_n + 1, k + 1) if j in per_k)[:cfg.max_chars]
        if _compact_len(text) < cfg.min_chars:
            short += 1
            continue
        out.append((k, text))
    stats["windows_with_speech"] = len(per_k)
    stats["skipped_short"] = short
    return out, stats


# ───────────────────────────────────────────────────────────── 벡터 꼴

def _unit(v: Sequence[float]) -> list[float]:
    n = math.sqrt(math.fsum(float(x) * float(x) for x in v))
    if not n or not math.isfinite(n):
        raise VectorsError("bad_vector", "임베딩이 0 벡터이거나 수가 아닙니다")
    return [float(x) / n for x in v]


def encode_vector(v: Sequence[float], encoding: str):
    """정규화한 벡터 → 문서의 `v` 값. f16le-b64: float16 리틀엔디언 바이트의 base64. round4: 소수 4자리 수 목록."""
    if encoding == "f16le-b64":
        return base64.b64encode(struct.pack(f"<{len(v)}e", *v)).decode("ascii")
    if encoding == "round4":
        return [round(float(x), 4) + 0.0 for x in v]   # + 0.0: -0.0 을 0.0 으로
    raise ValueError(f"모르는 encoding: {encoding!r}")


def vectors_sha256(windows: Sequence[Mapping]) -> str:
    """창 목록의 해시: [[start_ms, end_ms, v], …] 를 키 정렬·공백 없는 JSON(ASCII)으로 바꾼 바이트의 sha256."""
    rows = [[w["start_ms"], w["end_ms"], w["v"]] for w in windows]
    b = json.dumps(rows, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(b.encode("ascii")).hexdigest()


def model_info(embedder) -> dict:
    """결과의 model 칸: 실행기 info 에서 이름·해시 등 고정 칸만."""
    info = embedder.info() if embedder is not None else {}
    out = {k: info.get(k) for k in MODEL_KEYS}
    out["backend"] = out["backend"] or "unknown"
    out["name"] = out["name"] or "unknown"
    return out


def build_doc(texts: Sequence[tuple[int, str]], embedder, *, cfg: VectorsConfig, run_id: str, created_at: str,
              calibration: Mapping, stats: Mapping, project_id: str | None = None, video_no: str | None = None,
              asr_sha256: str | None = None, start_ms: int | None = None, end_ms: int | None = None) -> dict:
    """창 글 → topic-vectors 문서. 창 글은 임베딩에만 쓰고 문서에는 넣지 않는다."""
    W = cfg.window_ms
    t0 = time.perf_counter()
    vecs = embedder.encode([t for _k, t in texts]) if texts else []
    embed_s = time.perf_counter() - t0
    if len(vecs) != len(texts):
        raise VectorsError("bad_vector", "임베딩 개수가 창 수와 다릅니다")
    dim = len(vecs[0]) if vecs else int((embedder.info() or {}).get("dim") or 0)
    windows = []
    for (k, _t), v in zip(texts, vecs):
        if len(v) != dim:
            raise VectorsError("bad_vector", "임베딩 차원이 창마다 다릅니다")
        windows.append({"start_ms": k * W, "end_ms": (k + 1) * W, "v": encode_vector(_unit(v), cfg.encoding)})
    if windows:
        r0 = windows[0]["start_ms"] if start_ms is None else max(0, int(start_ms))
        r1 = windows[-1]["end_ms"] if end_ms is None else int(end_ms)
    else:
        r0 = 0 if start_ms is None else max(0, int(start_ms))
        r1 = r0 if end_ms is None else int(end_ms)
    st = dict(stats)
    st.update(windows=len(windows), embed_s=round(embed_s, 3))
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "run_id": run_id, "created_at": created_at,
        "engine": {"name": ENGINE_NAME, "version": ENGINE_VERSION, "config_sha256": cfg.sha256()},
        "model": model_info(embedder),
        "target": {"project_id": project_id, "video_no": video_no, "asr_sha256": asr_sha256},
        "time_axis": TIME_AXIS, "calibration": dict(calibration),
        "window_ms": W, "span_ms": cfg.span_ms, "encoding": cfg.encoding, "dim": dim,
        "range": {"start_ms": r0, "end_ms": max(r0, r1)}, "stats": st,
        "vectors_sha256": vectors_sha256(windows), "windows": windows,
    }


# ───────────────────────────────────────────────────────────── 검사 (스키마와 같은 규칙, 손으로)

_SHA = frozenset("0123456789abcdef")


def _is_sha(v) -> bool:
    return isinstance(v, str) and len(v) == 64 and set(v) <= _SHA


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(doc) -> list[str]:
    """문제 목록(빈 목록이면 통과). 값·본문은 메시지에 넣지 않는다. 벡터는 앱의 vectors_from_doc 으로 다시 풀어 본다."""
    p: list[str] = []
    if not isinstance(doc, Mapping):
        return ["문서가 object 가 아님"]
    extra = sorted(set(doc) - set(TOP_KEYS))
    if extra:
        p.append(f"모르는 칸 {len(extra)}개")      # 본문 칸이 끼어들지 못하게
    for k in TOP_KEYS:
        if k not in doc:
            p.append(f"{k} 없음")
    if p:
        return p
    if doc["schema"] != SCHEMA or doc["schema_version"] != SCHEMA_VERSION:
        p.append("schema·schema_version 이 다름")
    if not isinstance(doc["run_id"], str) or not TW.RUN_ID_RE.match(doc["run_id"]):
        p.append("run_id 꼴이 틀림")
    if not isinstance(doc["created_at"], str) or not doc["created_at"]:
        p.append("created_at 없음")
    eng = doc["engine"]
    if not isinstance(eng, Mapping) or set(eng) != {"name", "version", "config_sha256"} \
            or not isinstance(eng.get("name"), str) or not isinstance(eng.get("version"), str) \
            or not (eng.get("config_sha256") is None or _is_sha(eng.get("config_sha256"))):
        p.append("engine 칸이 틀림")
    m = doc["model"]
    if not isinstance(m, Mapping) or set(m) != set(MODEL_KEYS) or not isinstance(m.get("name"), str) \
            or not m.get("name") or not isinstance(m.get("backend"), str) \
            or not (m.get("model_sha256") is None or _is_sha(m.get("model_sha256"))):
        p.append("model 칸이 틀림")
    tg = doc["target"]
    if not isinstance(tg, Mapping) or set(tg) != {"project_id", "video_no", "asr_sha256"} \
            or not (tg.get("asr_sha256") is None or _is_sha(tg.get("asr_sha256"))):
        p.append("target 칸이 틀림")
    if doc["time_axis"] != TIME_AXIS:
        p.append("time_axis 가 vod_ms 가 아님")
    cal = doc["calibration"]
    if not isinstance(cal, Mapping) or set(cal) != {"source", "rev", "method", "status", "offset_ms",
                                                    "file_start_in_vod_ms"}:
        p.append("calibration 칸이 틀림")
    W, span, dim, enc = doc["window_ms"], doc["span_ms"], doc["dim"], doc["encoding"]
    if not _is_int(W) or W <= 0:
        p.append("window_ms 가 양의 정수가 아님")
    if not _is_int(span) or not _is_int(W) or W <= 0 or span < W or span % W:
        p.append("span_ms 가 window_ms 의 배수가 아님")
    if enc not in VS.VEC_ENCODINGS:
        p.append("encoding 이 틀림")
    if not _is_int(dim) or not 0 <= dim <= VS.VEC_MAX_DIM:
        p.append("dim 이 틀림")
    rg = doc["range"]
    if not isinstance(rg, Mapping) or not _is_int(rg.get("start_ms")) or not _is_int(rg.get("end_ms")) \
            or rg["start_ms"] < 0 or rg["end_ms"] < rg["start_ms"]:
        p.append("range 가 틀림")
    if not isinstance(doc["stats"], Mapping) or any(isinstance(v, str) for v in doc["stats"].values()):
        p.append("stats 가 숫자 칸 object 가 아님")
    wins = doc["windows"]
    if not isinstance(wins, list) or len(wins) > MAX_WINDOWS:
        p.append("windows 가 목록이 아니거나 너무 많음")
        return p
    if p:
        return p
    prev = -1
    bad = 0
    for w in wins:
        if not isinstance(w, Mapping) or set(w) != set(WINDOW_KEYS) or not _is_int(w["start_ms"]) \
                or not _is_int(w["end_ms"]) or w["start_ms"] < 0 or w["start_ms"] % W \
                or w["end_ms"] != w["start_ms"] + W or w["start_ms"] <= prev:
            bad += 1
            continue
        prev = w["start_ms"]
        v = VS.decode_vector(w["v"], enc, dim)
        if v is None:
            bad += 1
            continue
        n = math.sqrt(math.fsum(x * x for x in v))
        if not 0.98 <= n <= 1.02:          # float16·소수 4자리 반올림을 넘는 어긋남
            bad += 1
    if bad:
        p.append(f"windows 줄 {bad}개가 틀림(시각·순서·벡터 꼴·길이)")
    if not _is_sha(doc["vectors_sha256"]) or (not bad and doc["vectors_sha256"] != vectors_sha256(wins)):
        p.append("vectors_sha256 이 맞지 않음")
    if not bad and len(VS.vectors_from_doc(doc)) != len(wins):
        p.append("앱(viewer_score.vectors_from_doc)이 모든 창을 읽지 못함")
    return p


# ───────────────────────────────────────────────────────────── 프로젝트에서 · 쓰기

def default_out_path(project_dir: str) -> str:
    return os.path.join(project_dir, DEFAULT_OUT_REL)


def _refuse_pilot(project_dir: str, asr_path: str | None) -> None:
    if TW.is_pilot_dir(project_dir) or TW.inside_pilot(asr_path):
        raise VectorsError("pilot_locked", "시범 프로젝트는 시범 세션이 모두 잠기기 전에는 평문 주제 벡터 파일을 만들지 않습니다 "
                                           "(D-15 8, D-20 5). 봉인 짝: tools/engine_to_ai.py … --vectors --seal")


def load_embedder(models_dir: str | None = None, backend: str = "onnx"):
    """(embedder, status_doc). engine.semantic 의 기존 로더(e5-small, 해시 고정, 받지 않음). 없으면 VectorsError."""
    from . import semantic as S
    emb, st = S.load_default(models_dir, backend=backend)
    if emb is None:
        raise VectorsError(st.get("status") or "model_missing", "임베딩 실행기·모델을 쓸 수 없습니다")
    return emb, st


def from_project(project_dir: str, *, embedder=None, models_dir: str | None = None, backend: str = "onnx",
                 asr_path: str | None = None, offset_ms: int | None = None, file_start_ms: int | None = None,
                 start_ms: int | None = None, end_ms: int | None = None, cfg: VectorsConfig = VectorsConfig(),
                 now: datetime | None = None, allow_pilot: bool = False, asr_start_ms: int = 0) -> dict:
    """프로젝트 폴더 → topic-vectors 문서(메모리, 파일을 쓰지 않음). 시범은 읽기 전에 거부(allow_pilot 은 봉인 경로만).

    asr_start_ms: asr 를 영상 파일의 어느 시각부터 잘라 냈나(보정 전에 더함, TW.shift_segments). calibration 칸은 키가
    고정이라 stats.asr_start_ms(숫자)에 남긴다.
    embedder 를 주지 않으면 load_embedder(models_dir, backend). 결과가 검사를 못 넘으면 VectorsError('bad_result')."""
    if not allow_pilot:
        _refuse_pilot(project_dir, asr_path)
    try:
        a = TW.asr_start_or_error(asr_start_ms)
    except TW.WordsError as e:
        raise VectorsError(e.code, str(e)) from e
    t0 = time.perf_counter()
    pj = os.path.join(project_dir, "project.json")
    pdata: dict = {}
    if os.path.isfile(pj):
        try:
            pdata = TW._read_json(pj)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            raise VectorsError("bad_project", "project.json 을 읽지 못했습니다") from e
    try:
        segs, asr_sha = TW.read_asr(asr_path or os.path.join(project_dir, "derived", "asr.json"))
        cal, fs, src = TW.calibration_for(pdata, offset_ms=offset_ms, file_start_ms=file_start_ms)
    except TW.WordsError as e:
        raise VectorsError(e.code, str(e)) from e
    segs = TW.shift_segments(segs, a)                 # asr 시각 → 영상 파일 시각(보정 전)
    texts, stats = window_texts(segs, cal, fs, cfg=cfg, start_ms=start_ms, end_ms=end_ms)
    stats["asr_start_ms"] = a
    if embedder is None:
        embedder, _st = load_embedder(models_dir, backend)
    now = now or datetime.now().astimezone()
    pid = pdata.get("project_id")
    vno = (pdata.get("broadcast") or {}).get("video_no") if isinstance(pdata.get("broadcast"), Mapping) else None
    doc = build_doc(texts, embedder, cfg=cfg, run_id=f"tvec-v0-{now.strftime('%Y%m%dT%H%M%S')}",
                    created_at=now.isoformat(timespec="seconds"), calibration=TW.calibration_info(cal, fs, src),
                    stats=stats, project_id=str(pid) if pid else os.path.basename(os.path.abspath(project_dir)),
                    video_no=str(vno) if vno else None, asr_sha256=asr_sha, start_ms=start_ms, end_ms=end_ms)
    doc["stats"]["elapsed_s"] = round(time.perf_counter() - t0, 3)
    probs = validate(doc)
    if probs:
        raise VectorsError("bad_result", f"결과 검사 실패 {len(probs)}건")
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


INPUT_ERRORS = TW.INPUT_ERRORS               # no_asr·bad_asr·bad_project·bad_calibration·bad_range·bad_asr_start
MODEL_ERRORS = ("model_missing", "hash_mismatch", "backend_unavailable", "disabled")


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m engine topic-vectors",
                                 description="asr.json → VOD 창별 주제 벡터(topic-vectors, 로컬 e5-small)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", help=f"결과 파일(기본 <project>\\{DEFAULT_OUT_REL})")
    ap.add_argument("--asr", help="asr.json 경로(기본 <project>\\derived\\asr.json)")
    ap.add_argument("--start-ms", type=int, help="VOD 시각. 이 앞 말은 넣지 않는다")
    ap.add_argument("--end-ms", type=int, help="VOD 시각. 이 뒤(같음 포함) 말은 넣지 않는다")
    ap.add_argument("--offset-ms", type=int, help="보정 오프셋(명령줄 값이 project.json 보다 앞선다)")
    ap.add_argument("--file-start-ms", type=int,
                    help="클립 파일 0초의 VOD 시각(project.json 보정과 짝). asr 를 잘라 낸 시작이 아니다")
    ap.add_argument("--asr-start-ms", type=int, default=0,
                    help="asr 가 영상 파일의 몇 ms 부터 잘라 낸 음성인지. 영상 파일 시각 = asr 시각 + 이 값(보정 전). 기본 0")
    ap.add_argument("--window-sec", type=int, default=10)
    ap.add_argument("--span-sec", type=int, help="창 하나가 덮는 길이(기본 = --window-sec, 그 배수)")
    ap.add_argument("--encoding", choices=tuple(ENCODINGS), default="f16")
    ap.add_argument("--models-dir", help="모델 폴더(기본은 engine.semantic 이 정함)")
    ap.add_argument("--backend", choices=("onnx", "lmstudio"), default="onnx")
    ns = ap.parse_args(argv)
    out = ns.out or default_out_path(ns.project)
    if TW.is_pilot_dir(ns.project) or TW.inside_pilot(out) or TW.inside_pilot(ns.asr):
        # 평문 파일을 쓰기 전에, 입력도 읽기 전에 거부한다(D-15 8, D-20 5)
        print(json.dumps({"error": "pilot_locked",
                          "detail": "시범 프로젝트는 잠기기 전에는 평문 주제 벡터 파일을 만들지 않습니다. 봉인 짝: "
                                    "tools/engine_to_ai.py --from-project … --vectors --seal"}, ensure_ascii=False))
        return 1
    if TW.refuse_unprotected_out(ns.project, out, "주제 벡터"):
        # 벡터는 전사문과 같은 등급(머리말) — 보호 구역 'derived/asr*' 밖에는 읽기·모델 적재 전에 거부(engine_to_ai 와 같은 규칙)
        return 2
    if ns.window_sec <= 0 or (ns.span_sec is not None and ns.span_sec <= 0):
        ap.error("--window-sec·--span-sec 는 양수")
    try:
        cfg = VectorsConfig(window_ms=ns.window_sec * 1000,
                            span_ms=(ns.span_sec or ns.window_sec) * 1000, encoding=ENCODINGS[ns.encoding])
    except ValueError:
        ap.error("--span-sec 는 --window-sec 의 배수")
    try:
        doc = from_project(ns.project, models_dir=ns.models_dir, backend=ns.backend, asr_path=ns.asr,
                           offset_ms=ns.offset_ms, file_start_ms=ns.file_start_ms, start_ms=ns.start_ms,
                           end_ms=ns.end_ms, cfg=cfg, asr_start_ms=ns.asr_start_ms)
        write_json(doc, out)
    except VectorsError as e:
        print(json.dumps({"error": e.code}, ensure_ascii=False))
        return 2 if e.code in INPUT_ERRORS else 3 if e.code in MODEL_ERRORS else 1
    except OSError as e:
        print(json.dumps({"error": type(e).__name__}, ensure_ascii=False))
        return 2
    st, c, m = doc["stats"], doc["calibration"], doc["model"]
    print(json.dumps({"out": out, "windows": st["windows"], "windows_with_speech": st["windows_with_speech"],
                      "skipped_short": st["skipped_short"], "dropped_out_of_range": st["dropped_out_of_range"],
                      "asr_start_ms": st["asr_start_ms"], "dim": doc["dim"], "encoding": doc["encoding"],
                      "model": m["name"], "model_sha256": m["model_sha256"], "calibration_source": c["source"],
                      "calibration_status": c["status"], "embed_s": st["embed_s"], "elapsed_s": st["elapsed_s"]},
                     ensure_ascii=False))
    return 0


__all__ = ["SCHEMA", "SCHEMA_VERSION", "ENGINE_NAME", "ENGINE_VERSION", "DEFAULT_OUT_REL", "VectorsConfig",
           "VectorsError", "window_texts", "encode_vector", "vectors_sha256", "model_info", "build_doc", "validate",
           "default_out_path", "load_embedder", "from_project", "dump", "write_json", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
