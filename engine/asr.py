"""로컬 음성 인식 (D-04): faster-whisper 로 16kHz 모노 wav 를 전사해 asr.json 형식 dict 를 돌려준다.

asr.json 형식은 docs/measurements/2026-10-20-stt.md 2장에 정의한다 (03 D15 의 프로젝트 derived/asr.json).

- 기본 모델 large-v3-turbo, GPU(cuda) float16. GPU 가 실패하면 cpu int8 로 내려가고 결과의 fallback 에 적는다.
- 모델은 허깅페이스에서 받아 models_dir(기본 <chatcoach-data>\\models) 에 캐시한다. 이미 있으면 네트워크를 쓰지 않는다.
- 모델 파일 model.bin 의 sha256 을 결과에 적는다 (계산한 값은 model.bin 옆 .chatcoach-sha256.json 에 캐시).
- initial_prompt(닉네임 힌트 등)는 개인정보가 섞일 수 있어 원문을 결과에 넣지 않고 sha256 과 글자 수만 적는다.
- 전사 문장은 결과 dict 에만 있다. 로그·표준 출력에 문장을 내지 않는다.
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime, timezone

from . import ASR_FORMAT, ASR_FORMAT_VERSION

DEFAULT_MODEL = "large-v3-turbo"
SAMPLE_RATE = 16000
HASH_CACHE_NAME = ".chatcoach-sha256.json"

# 이 이름들은 faster_whisper.utils._MODELS 와 같은 허깅페이스 저장소를 가리킨다 (기록용. 실제 해석은 faster_whisper 가 한다)
KNOWN_REPOS = {
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
}


# ---------------------------------------------------------------- 경로

def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_root() -> str:
    """저장소 밖 실제 데이터 폴더. CHATCOACH_DATA 환경 변수가 있으면 그것, 없으면 저장소 옆 chatcoach-data."""
    env = os.environ.get("CHATCOACH_DATA")
    if env:
        return os.path.abspath(env)
    return os.path.join(os.path.dirname(repo_root()), "chatcoach-data")


def default_models_dir() -> str:
    return os.environ.get("CHATCOACH_MODELS") or os.path.join(data_root(), "models")


# ---------------------------------------------------------------- CUDA DLL

_dll_handles: list = []


def add_cuda_dll_dirs() -> list[str]:
    """pip 의 nvidia-cublas-cu12 · nvidia-cudnn-cu12 휠이 깐 DLL 폴더를 DLL 검색 경로에 더한다 (Windows).

    ctranslate2 는 cublas64_12.dll · cudnn*_9.dll 을 PATH 에서 찾는다. 휠 폴더(site-packages\\nvidia\\*\\bin)는
    PATH 에 없으므로 os.add_dll_directory 와 PATH 앞에 둘 다 넣는다. 더한 폴더 목록을 돌려준다.
    """
    if os.name != "nt":
        return []
    added: list[str] = []
    for sp in sys.path:
        base = os.path.join(sp, "nvidia")
        if not os.path.isdir(base):
            continue
        for d in sorted(glob.glob(os.path.join(base, "*", "bin"))):
            if d in added:
                continue
            try:
                _dll_handles.append(os.add_dll_directory(d))
            except OSError:
                continue
            added.append(d)
    if added:
        os.environ["PATH"] = os.pathsep.join(added + [os.environ.get("PATH", "")])
    return added


# ---------------------------------------------------------------- 해시·오디오

def sha256_file(path: str, chunk: int = 4 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def model_bin_sha256(model_dir: str) -> str | None:
    """model_dir\\model.bin 의 sha256. 크기·수정 시각이 같으면 캐시 파일 값을 쓴다."""
    p = os.path.join(model_dir, "model.bin")
    if not os.path.isfile(p):
        return None
    real = os.path.realpath(p)  # 허깅페이스 캐시는 snapshots 의 링크가 blobs 를 가리킬 수 있다
    st = os.stat(real)
    cache = os.path.join(model_dir, HASH_CACHE_NAME)
    try:
        with open(cache, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("size") == st.st_size and c.get("mtime_ns") == st.st_mtime_ns and c.get("sha256"):
            return c["sha256"]
    except (OSError, ValueError):
        pass
    digest = sha256_file(real)
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump({"file": "model.bin", "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}, f)
    except OSError:
        pass
    return digest


def wav_info(path: str) -> dict:
    """wav 머리에서 길이·표본율·채널을 읽는다. wav 가 아니면 duration_ms=None."""
    try:
        with wave.open(path, "rb") as w:
            n, sr, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        return {"duration_ms": int(round(n * 1000 / sr)) if sr else None, "sample_rate": sr, "channels": ch}
    except (wave.Error, EOFError, OSError):
        return {"duration_ms": None, "sample_rate": None, "channels": None}


def _ms(t) -> int | None:
    if t is None:
        return None
    try:
        f = float(t)
    except (TypeError, ValueError):
        return None
    return int(round(f * 1000)) if math.isfinite(f) else None


def _num(x, nd: int = 4):
    """JSON 에 넣을 수 있는 숫자(유한값)로. NaN·Inf 는 None."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if math.isfinite(f) else None


# ---------------------------------------------------------------- VRAM 표본

class VramSampler:
    """nvidia-smi 를 0.5초마다 불러 GPU 메모리 사용량 최댓값(MiB)을 잰다. 다른 프로그램 몫도 포함된 '대략' 값."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.exe = shutil.which("nvidia-smi")
        self.baseline: int | None = None
        self.peak: int | None = None
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def _read(self) -> int | None:
        if not self.exe:
            return None
        try:
            out = subprocess.run([self.exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, timeout=5,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return int(out.stdout.decode("ascii", "replace").strip().splitlines()[0])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return None

    def _loop(self):
        while not self._stop.is_set():
            v = self._read()
            if v is not None and (self.peak is None or v > self.peak):
                self.peak = v
            self._stop.wait(self.interval)

    def start(self):
        self.baseline = self._read()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def stop(self) -> dict:
        self._stop.set()
        if self._t:
            self._t.join(timeout=10)
        delta = (self.peak - self.baseline) if (self.peak is not None and self.baseline is not None) else None
        return {"baseline_mib": self.baseline, "peak_mib": self.peak, "delta_mib": delta}


# ---------------------------------------------------------------- 모델

def resolve_model_dir(model: str, models_dir: str | None = None) -> tuple[str, str | None]:
    """모델 이름(또는 로컬 폴더)을 실제 폴더로. (폴더, 허깅페이스 저장소 이름 또는 None).

    캐시에 있으면 네트워크 없이(local_files_only) 찾고, 없을 때만 내려받는다.
    """
    if os.path.isdir(model):
        return os.path.abspath(model), None
    from faster_whisper.utils import download_model  # 늦은 import
    cache = models_dir or default_models_dir()
    os.makedirs(cache, exist_ok=True)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    try:
        path = download_model(model, local_files_only=True, cache_dir=cache)
    except Exception:  # 캐시에 없음 → 받기
        path = download_model(model, local_files_only=False, cache_dir=cache)
    return path, KNOWN_REPOS.get(model, model if "/" in model else None)


def _model_info(model: str, model_dir: str, repo: str | None) -> dict:
    files = {}
    for name in sorted(os.listdir(model_dir)):
        p = os.path.join(model_dir, name)
        if os.path.isfile(p) and not name.startswith("."):
            files[name] = os.path.getsize(p)
    rev = os.path.basename(model_dir) if os.sep + "snapshots" + os.sep in model_dir else None
    return {"name": model, "repo": repo, "revision": rev, "model_bin_sha256": model_bin_sha256(model_dir),
            "files": files}


def _versions() -> dict:
    out = {"name": "faster-whisper"}
    for mod, key in (("faster_whisper", "version"), ("ctranslate2", "ctranslate2")):
        try:
            m = __import__(mod)
            out[key] = getattr(m, "__version__", None)
        except Exception:
            out[key] = None
    return out


# ---------------------------------------------------------------- 전사

def _run(model_dir: str, wav_path: str, device: str, compute_type: str, kw: dict) -> tuple[list, object, float, float]:
    from faster_whisper import WhisperModel  # 늦은 import
    t0 = time.perf_counter()
    wm = WhisperModel(model_dir, device=device, compute_type=compute_type)
    t1 = time.perf_counter()
    seg_iter, info = wm.transcribe(wav_path, **kw)
    segs = list(seg_iter)  # 생성기: 실제 디코딩(과 GPU 오류)은 여기서 일어난다
    t2 = time.perf_counter()
    del wm
    return segs, info, t1 - t0, t2 - t1


def _seg_dict(s, with_words: bool) -> dict:
    d = {
        "id": int(getattr(s, "id", 0) or 0),
        "start_ms": _ms(s.start),
        "end_ms": _ms(s.end),
        "text": (s.text or "").strip(),
        "avg_logprob": _num(s.avg_logprob),
        "no_speech_prob": _num(s.no_speech_prob),
        "compression_ratio": _num(getattr(s, "compression_ratio", None)),
        "temperature": _num(getattr(s, "temperature", None), 2),
        "words": [],
    }
    if with_words and getattr(s, "words", None):
        d["words"] = [{"start_ms": _ms(w.start), "end_ms": _ms(w.end), "word": w.word, "prob": _num(w.probability)}
                      for w in s.words]
    return d


def _stats(segments: list[dict]) -> dict:
    n = len(segments)

    def mean(key):
        vals = [s[key] for s in segments if s.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "segment_count": n,
        "word_count": sum(len(s["words"]) for s in segments),
        "char_count": sum(len("".join(s["text"].split())) for s in segments),
        "speech_ms": sum(max(0, (s["end_ms"] or 0) - (s["start_ms"] or 0)) for s in segments),
        "mean_no_speech_prob": mean("no_speech_prob"),
        "mean_avg_logprob": mean("avg_logprob"),
    }


def transcribe(wav_path: str, model: str = DEFAULT_MODEL, device: str = "cuda", compute_type: str = "float16",
               language: str | None = "ko", initial_prompt: str | None = None, vad: bool = True,
               word_timestamps: bool = True, *, models_dir: str | None = None, beam_size: int = 5,
               vad_parameters: dict | None = None, measure_vram: bool = False,
               allow_cpu_fallback: bool = True) -> dict:
    """wav_path 를 전사해 asr.json 형식 dict 를 돌려준다 (형식: docs/measurements/2026-10-20-stt.md 2장).

    device='cuda' 가 실패하면(DLL 없음·메모리 부족 등) allow_cpu_fallback 일 때 cpu int8 로 다시 돌리고
    결과 fallback 에 원래 설정과 오류 요약을 적는다. 모델을 찾거나 받지 못하면 예외를 그대로 올린다.
    """
    if not os.path.isfile(wav_path):
        raise FileNotFoundError(wav_path)
    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    t_all = time.perf_counter()
    cuda_dirs = add_cuda_dll_dirs() if device != "cpu" else []
    model_dir, repo = resolve_model_dir(model, models_dir)
    t_resolved = time.perf_counter()

    vparams = dict(vad_parameters or {})
    kw = dict(language=language, initial_prompt=initial_prompt, vad_filter=vad, word_timestamps=word_timestamps,
              beam_size=beam_size)
    if vad and vparams:
        kw["vad_parameters"] = vparams

    sampler = VramSampler().start() if (measure_vram and device != "cpu") else None
    fallback = None
    warnings: list[str] = []
    used_device, used_ct = device, compute_type
    try:
        try:
            segs, info, load_s, tr_s = _run(model_dir, wav_path, device, compute_type, kw)
        except IndexError as e:
            # faster-whisper 1.2.1 의 find_alignment 가 빈 정렬에서 IndexError 를 낸다 (VAD 를 끄고 large-v3 로
            # 긴 무음·배경음을 돌릴 때 재현). 장치 문제가 아니므로 같은 장치에서 단어 시각 없이 다시 돈다.
            if not word_timestamps:
                raise
            warnings.append(f"word_timestamps_failed: {type(e).__name__}: {str(e)[:200]}")
            kw["word_timestamps"] = False
            word_timestamps = False
            segs, info, load_s, tr_s = _run(model_dir, wav_path, device, compute_type, kw)
    except Exception as e:  # noqa: BLE001 — GPU 쪽 오류 종류가 많다 (RuntimeError·OSError 등)
        if device == "cpu" or not allow_cpu_fallback:
            if sampler:
                sampler.stop()
            raise
        fallback = {"from_device": device, "from_compute_type": compute_type,
                    "error": f"{type(e).__name__}: {str(e)[:300]}"}
        used_device, used_ct = "cpu", "int8"
        segs, info, load_s, tr_s = _run(model_dir, wav_path, used_device, used_ct, kw)
    vram = sampler.stop() if sampler else None

    segments = [_seg_dict(s, word_timestamps) for s in segs]
    winfo = wav_info(wav_path)
    dur_ms = _ms(getattr(info, "duration", None)) or winfo["duration_ms"]
    after_vad_ms = _ms(getattr(info, "duration_after_vad", None))
    total_s = time.perf_counter() - t_all
    st = os.stat(wav_path)

    return {
        "format": ASR_FORMAT,
        "format_version": ASR_FORMAT_VERSION,
        "created_at": started_at,
        "engine": _versions(),
        "model": _model_info(model, model_dir, repo),
        "settings": {
            "device": used_device,
            "compute_type": used_ct,
            "requested_device": device,
            "requested_compute_type": compute_type,
            "language": language,
            "vad": bool(vad),
            "vad_parameters": vparams or None,
            "word_timestamps": bool(word_timestamps),
            "beam_size": beam_size,
            "initial_prompt_sha256": hashlib.sha256(initial_prompt.encode("utf-8")).hexdigest() if initial_prompt else None,
            "initial_prompt_chars": len(initial_prompt) if initial_prompt else 0,
            "cuda_dll_dirs": len(cuda_dirs),
        },
        "fallback": fallback,
        "warnings": warnings,
        "input": {"file_name": os.path.basename(wav_path), "size": st.st_size, "sha256": sha256_file(wav_path),
                  "sample_rate": winfo["sample_rate"], "channels": winfo["channels"]},
        "audio": {"duration_ms": dur_ms, "duration_after_vad_ms": after_vad_ms},
        "language": {"detected": getattr(info, "language", None),
                     "probability": _num(getattr(info, "language_probability", None))},
        "timing": {
            "model_resolve_s": round(t_resolved - t_all, 3),
            "model_load_s": round(load_s, 3),
            "transcribe_s": round(tr_s, 3),
            "total_s": round(total_s, 3),
            # 실시간 배수: 오디오 길이 / 전사 시간 (모델 로드 제외). 1 보다 크면 실시간보다 빠르다
            "realtime_x": round((dur_ms / 1000) / tr_s, 2) if (dur_ms and tr_s > 0) else None,
        },
        "vram": vram,
        "stats": _stats(segments),
        "segments": segments,
    }


def write_asr_json(result: dict, out_path: str) -> None:
    """임시 파일에 쓰고 os.replace 로 바꾼다 (중간에 끊겨도 반쪽 파일이 남지 않게). NaN 은 거부."""
    d = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(d, exist_ok=True)
    tmp = out_path + f".tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, allow_nan=False, indent=1)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


REQUIRED_TOP = ("format", "format_version", "created_at", "engine", "model", "settings", "fallback", "warnings", "input",
                "audio", "language", "timing", "stats", "segments")
REQUIRED_SEG = ("start_ms", "end_ms", "text", "avg_logprob", "no_speech_prob", "words")
REQUIRED_WORD = ("start_ms", "end_ms", "word", "prob")


def validate_asr(d: dict) -> list[str]:
    """asr.json dict 의 형식 문제 목록 (비면 통과). 내용(문장)은 보지 않는다."""
    p: list[str] = []
    if not isinstance(d, dict):
        return ["최상위가 dict 가 아님"]
    for k in REQUIRED_TOP:
        if k not in d:
            p.append(f"없는 키 {k}")
    if d.get("format") != ASR_FORMAT:
        p.append("format 다름")
    if not isinstance(d.get("segments"), list):
        return p + ["segments 가 list 가 아님"]
    for k in ("name", "model_bin_sha256"):
        if k not in (d.get("model") or {}):
            p.append(f"model.{k} 없음")
    prev = -1
    for i, s in enumerate(d["segments"]):
        for k in REQUIRED_SEG:
            if k not in s:
                p.append(f"segments[{i}].{k} 없음")
        a, b = s.get("start_ms"), s.get("end_ms")
        if not (isinstance(a, int) and isinstance(b, int) and 0 <= a <= b):
            p.append(f"segments[{i}] 시각이 정수·순서가 아님")
        elif a < prev:
            p.append(f"segments[{i}] 시작이 앞 구간보다 이름")
        else:
            prev = a
        for j, w in enumerate(s.get("words") or []):
            for k in REQUIRED_WORD:
                if k not in w:
                    p.append(f"segments[{i}].words[{j}].{k} 없음")
    return p
