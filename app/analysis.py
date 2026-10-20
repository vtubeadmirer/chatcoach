# 참고: vtubeadmirer/clipslicer@07d13cc (app/ffmpeg_tools.py 의 probe·compute_waveform·detect_silence·_run) — 새로 씀
"""ffmpeg / ffprobe 호출 (docs/design/02-architecture.md 3-2 analysis 행, 10장 S-26).

영상은 읽기만 한다. 모든 호출의 공통 규칙:
- ffmpeg 에 -nostdin, 입력 쪽에 -protocol_whitelist file (네트워크·파이프 프로토콜로 새지 않게).
- 입력 경로는 드라이브 문자로 시작하는 로컬 절대 경로만 (UNC·URL 거부). 자세한 문자열 검사는 media.check_path_string.
- subprocess 출력은 bytes 로 받고 utf-8 → cp949 순서로 디코딩한다 (한글 경로가 든 오류 문구).
- CREATE_NO_WINDOW, stdin=DEVNULL, 제한 시간(timeout), 취소 콜백(cancel() 이 True 면 프로세스를 죽인다).

도구는 app.paths.find_tool 로만 찾는다 (frozen 이면 번들 폴더만, S-08).
"""
from __future__ import annotations

import array
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from fractions import Fraction
from typing import Callable

from .paths import find_tool

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CancelCb = Callable[[], bool] | None

PROBE_TIMEOUT = 30.0          # 초. ffprobe 는 머리만 읽는다
_POLL = 0.1                   # 취소·시간 초과 확인 간격(초)
_STDERR_KEEP = 4 * 1024 * 1024  # stderr 는 끝 4MB 만 남긴다 (silencedetect 줄은 짧다)
_FRAC_RE = re.compile(r"^[0-9]+/[0-9]+$")
_DRIVE_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")


class AnalysisError(RuntimeError):
    """ffmpeg/ffprobe 실패."""


class Cancelled(AnalysisError):
    """cancel() 이 True 를 돌려 중단됨."""


class ToolTimeout(AnalysisError):
    """제한 시간 초과로 중단됨."""


# --------------------------------------------------------------------------- 공통
def decode_output(b: bytes | None) -> str:
    """도구 출력 bytes → str. utf-8 을 먼저, 안 되면 cp949, 그래도 안 되면 utf-8 replace."""
    if not b:
        return ""
    for enc in ("utf-8", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def _check_input(path: str) -> str:
    """ffmpeg 에 넘기기 전 최소 검사: 로컬 드라이브 절대 경로인가 (UNC·URL·상대 경로 거부).

    media 가 analysis 를 쓰는 방향(02 3-2)이라 media.check_path_string 을 import 하지 않고 여기서 최소한만 본다.
    """
    p = os.fspath(path)
    if not isinstance(p, str) or "\0" in p or "://" in p or not _DRIVE_ABS_RE.match(p):
        raise AnalysisError(f"로컬 드라이브 절대 경로가 아닙니다: {p!r}")
    return p


def _tool(name: str) -> str:
    exe = find_tool(name)
    if not exe:
        raise AnalysisError(f"{name} 를 찾지 못했습니다")
    return exe


def _run(cmd: list[str], timeout: float | None, cancel: CancelCb = None,
         on_stdout: Callable[[bytes], None] | None = None) -> tuple[int, bytes, bytes]:
    """cmd 를 실행해 (returncode, stdout, stderr) 를 bytes 로 돌려준다.

    on_stdout 이 있으면 stdout 을 덩어리로 넘기고 모으지 않는다 (긴 PCM 파이프).
    시간 초과면 ToolTimeout, 취소면 Cancelled — 둘 다 프로세스를 죽이고 기다린 뒤 올린다.
    시작 전에 cancel() 을 한 번 본다 (이미 취소된 작업은 프로세스를 띄우지 않는다).
    """
    if cancel is not None and cancel():
        raise Cancelled("취소됨")
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)
    out_parts: list[bytes] = []
    err_buf = bytearray()
    errors: list[BaseException] = []

    def read_out():
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                if on_stdout is not None:
                    if not errors:
                        on_stdout(chunk)
                else:
                    out_parts.append(chunk)
        except BaseException as e:  # 콜백 예외를 메인 스레드로 넘긴다
            errors.append(e)
            try:
                proc.kill()
            except OSError:
                pass
            # 파이프를 마저 비워 ffmpeg 가 쓰기에서 막히지 않게
            try:
                while proc.stdout.read(65536):
                    pass
            except (OSError, ValueError):
                pass

    def read_err():
        while True:
            chunk = proc.stderr.read(65536)
            if not chunk:
                break
            err_buf.extend(chunk)
            if len(err_buf) > _STDERR_KEEP * 2:
                del err_buf[: len(err_buf) - _STDERR_KEEP]

    t_out = threading.Thread(target=read_out, daemon=True)
    t_err = threading.Thread(target=read_err, daemon=True)
    t_out.start()
    t_err.start()
    deadline = None if timeout is None else time.monotonic() + timeout
    stop: type[AnalysisError] | None = None
    try:
        while True:
            try:
                proc.wait(timeout=_POLL)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel():
                stop = Cancelled
            elif deadline is not None and time.monotonic() > deadline:
                stop = ToolTimeout
            if stop is not None:
                proc.kill()
                proc.wait()
                break
    finally:
        if proc.poll() is None:  # KeyboardInterrupt 등
            proc.kill()
            proc.wait()
        t_out.join()
        t_err.join()
        proc.stdout.close()
        proc.stderr.close()
    if stop is Cancelled:
        raise Cancelled("취소됨")
    if stop is ToolTimeout:
        raise ToolTimeout(f"제한 시간 {timeout}초 초과")
    if errors:
        raise errors[0]
    return proc.returncode, b"".join(out_parts), bytes(err_buf)


def _err_text(stderr: bytes, limit: int = 600) -> str:
    t = decode_output(stderr).strip()
    return t[-limit:] if t else ""


def _ffmpeg_base() -> list[str]:
    return [_tool("ffmpeg"), "-nostdin", "-hide_banner"]


# --------------------------------------------------------------------------- probe
def _frac_str(v) -> str | None:
    return v if isinstance(v, str) and _FRAC_RE.match(v) else None


def _frac(v: str | None) -> Fraction | None:
    if not v:
        return None
    n, d = v.split("/")
    if int(d) == 0 or int(n) == 0:
        return None
    return Fraction(int(n), int(d))


def _int_or_none(v) -> int | None:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if i >= 0 else None


def probe(path: str, timeout: float | None = PROBE_TIMEOUT, cancel: CancelCb = None) -> dict:
    """ffprobe 요약.

    돌려주는 키: duration_ms(int), container(format_name), video_codec, width, height,
    r_frame_rate, avg_frame_rate(원문 "n/d" 또는 None), fps(float 또는 None, avg 우선), vfr(bool: r 와 avg 가
    다르면 가변 프레임 의심), nb_frames, audio_codec, sample_rate(int), channels(int), has_audio(bool).
    첫 번째 영상·오디오 스트림만 본다 (커버 이미지 attached_pic 은 건너뛴다).
    """
    p = _check_input(path)
    cmd = [_tool("ffprobe"), "-v", "error", "-protocol_whitelist", "file", "-print_format", "json",
           "-show_entries",
           "format=duration,format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate,"
           "avg_frame_rate,nb_frames,sample_rate,channels,duration:stream_disposition=attached_pic",
           p]
    rc, out, err = _run(cmd, timeout, cancel)
    if rc != 0:
        raise AnalysisError(_err_text(err) or f"ffprobe 실패 (코드 {rc})")
    try:
        data = json.loads(decode_output(out))
    except ValueError as e:
        raise AnalysisError(f"ffprobe 출력을 읽지 못했습니다: {e}") from e
    fmt = data.get("format") or {}
    info = {"duration_ms": 0, "container": fmt.get("format_name") or None,
            "video_codec": None, "width": None, "height": None,
            "r_frame_rate": None, "avg_frame_rate": None, "fps": None, "vfr": False, "nb_frames": None,
            "audio_codec": None, "sample_rate": None, "channels": None, "has_audio": False}
    dur_s = None
    try:
        dur_s = float(fmt.get("duration"))
    except (TypeError, ValueError):
        pass
    stream_durs = []
    have_v = False
    for s in data.get("streams") or []:
        try:
            stream_durs.append(float(s.get("duration")))
        except (TypeError, ValueError):
            pass
        kind = s.get("codec_type")
        if kind == "video" and not have_v and not (s.get("disposition") or {}).get("attached_pic"):
            have_v = True
            info["video_codec"] = s.get("codec_name") or None
            info["width"] = _int_or_none(s.get("width"))
            info["height"] = _int_or_none(s.get("height"))
            r = _frac_str(s.get("r_frame_rate"))
            a = _frac_str(s.get("avg_frame_rate"))
            info["r_frame_rate"], info["avg_frame_rate"] = r, a
            fr, fa = _frac(r), _frac(a)
            f = fa or fr
            info["fps"] = round(float(f), 6) if f else None
            info["vfr"] = bool(fr and fa and fr != fa)
            info["nb_frames"] = _int_or_none(s.get("nb_frames"))
        elif kind == "audio" and not info["has_audio"]:
            info["has_audio"] = True
            info["audio_codec"] = s.get("codec_name") or None
            info["sample_rate"] = _int_or_none(s.get("sample_rate"))
            info["channels"] = _int_or_none(s.get("channels"))
    if dur_s is None or not math.isfinite(dur_s):
        dur_s = max((d for d in stream_durs if math.isfinite(d)), default=0.0)
    info["duration_ms"] = max(0, int(round(dur_s * 1000)))
    return info


# --------------------------------------------------------------------------- 음성 추출
def _seek_args(start_ms: int | None, dur_ms: int | None) -> tuple[list[str], list[str]]:
    pre, post = [], []
    if start_ms is not None:
        if start_ms < 0:
            raise ValueError("start_ms 는 0 이상")
        if start_ms > 0:
            pre = ["-ss", f"{start_ms / 1000:.3f}"]
    if dur_ms is not None:
        if dur_ms <= 0:
            raise ValueError("dur_ms 는 0 보다 커야 한다")
        post = ["-t", f"{dur_ms / 1000:.3f}"]
    return pre, post


def _auto_timeout(path: str, dur_ms: int | None, cancel: CancelCb) -> float:
    """제한 시간 기본값: 60초 + 처리할 길이(초) × 1. (실측 5분 영상 추출이 수 초라 넉넉하다)"""
    if dur_ms is None:
        dur_ms = probe(path, cancel=cancel)["duration_ms"]
    return 60.0 + dur_ms / 1000.0


def extract_audio_wav(path: str, out_wav: str, sample_rate: int = 16000, start_ms: int | None = None,
                      dur_ms: int | None = None, cancel: CancelCb = None, timeout: float | None = None) -> dict:
    """영상의 첫 오디오를 모노 16bit PCM WAV 로 뽑는다 (STT 입력, 08 D-03 1).

    out_wav 옆 임시 파일(<out_wav>.part-<pid>.wav)에 쓰고 끝나면 os.replace. 실패·취소면 임시 파일을 지운다.
    timeout=None 이면 60초 + 처리 길이(초). 돌려주는 값: {"path", "sample_rate", "channels", "duration_ms", "elapsed_s"}.
    """
    p = _check_input(path)
    out = _check_input(os.path.abspath(out_wav))
    if int(sample_rate) <= 0:
        raise ValueError("sample_rate 는 양수")
    pre, post = _seek_args(start_ms, dur_ms)
    if timeout is None:
        timeout = _auto_timeout(p, dur_ms, cancel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = f"{out}.part-{os.getpid()}.wav"
    cmd = (_ffmpeg_base() + ["-v", "error", "-y"] + pre
           + ["-protocol_whitelist", "file", "-i", p] + post
           + ["-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(int(sample_rate)),
              "-c:a", "pcm_s16le", "-f", "wav", tmp])
    t0 = time.perf_counter()
    try:
        rc, _, err = _run(cmd, timeout, cancel)
        if rc != 0:
            raise AnalysisError(_err_text(err) or f"ffmpeg 실패 (코드 {rc})")
        os.replace(tmp, out)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    elapsed = time.perf_counter() - t0
    import wave
    with wave.open(out, "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
    return {"path": out, "sample_rate": sr, "channels": ch,
            "duration_ms": int(round(n * 1000 / sr)) if sr else 0, "elapsed_s": round(elapsed, 3)}


# --------------------------------------------------------------------------- 파형
def compute_waveform(path: str, points: int, sample_rate: int = 8000, cancel: CancelCb = None,
                     timeout: float | None = None) -> list[float]:
    """영상 전체를 모노 sample_rate PCM 으로 파이프 디코드해 points 개 구간의 절대 피크(0~1) 목록.

    임시 파일 없이 stdout 을 덩어리로 받아 바로 구간 피크로 줄인다 (10시간 VOD 도 메모리가 늘지 않는다).
    구간 길이는 ffprobe 길이로 정한다. 실제 디코드가 짧으면 남는 구간은 0, 길면 마지막 구간에 합친다.
    항상 길이 points 인 리스트. 전체가 무음이면 모두 0.0. 오디오가 없으면 AnalysisError.
    """
    if points <= 0:
        raise ValueError("points 는 양수")
    p = _check_input(path)
    info = probe(p, cancel=cancel)
    if not info["has_audio"]:
        raise AnalysisError("오디오 스트림이 없습니다")
    total = max(1, int(info["duration_ms"] * sample_rate / 1000))
    per = max(1, math.ceil(total / points))
    if timeout is None:
        timeout = 60.0 + info["duration_ms"] / 1000.0
    peaks = [0] * points
    state = {"idx": 0, "fill": 0, "cur": 0, "rest": b""}

    def feed(chunk: bytes):
        data = state["rest"] + chunk
        cut = len(data) - (len(data) % 2)
        state["rest"] = data[cut:]
        arr = array.array("h")
        arr.frombytes(data[:cut])
        if sys.byteorder == "big":
            arr.byteswap()
        pos, n = 0, len(arr)
        while pos < n:
            take = min(per - state["fill"], n - pos)
            seg = arr[pos:pos + take]
            m = max(max(seg), -min(seg))
            if m > state["cur"]:
                state["cur"] = m
            state["fill"] += take
            pos += take
            if state["fill"] >= per:
                i = min(state["idx"], points - 1)
                peaks[i] = max(peaks[i], state["cur"])
                state["idx"] += 1
                state["fill"] = 0
                state["cur"] = 0

    cmd = (_ffmpeg_base() + ["-v", "error", "-protocol_whitelist", "file", "-i", p,
                             "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(int(sample_rate)),
                             "-f", "s16le", "-"])
    rc, _, err = _run(cmd, timeout, cancel, on_stdout=feed)
    if rc != 0:
        raise AnalysisError(_err_text(err) or f"ffmpeg 실패 (코드 {rc})")
    if state["fill"]:
        i = min(state["idx"], points - 1)
        peaks[i] = max(peaks[i], state["cur"])
    top = max(peaks)
    return [v / top for v in peaks] if top > 0 else [0.0] * points


# --------------------------------------------------------------------------- 무음
_SIL_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def detect_silence(path: str, noise_db: float = -35, min_sec: float = 0.6, cancel: CancelCb = None,
                   timeout: float | None = None) -> list[tuple[int, int]]:
    """silencedetect 로 무음 구간 [(start_ms, end_ms)] (시작 순).

    파일 끝까지 무음이면 끝을 ffprobe 길이(ms)로 닫는다. 음수 시작(인코더 지연)은 0 으로 자른다.
    """
    p = _check_input(path)
    info = probe(p, cancel=cancel)
    if not info["has_audio"]:
        raise AnalysisError("오디오 스트림이 없습니다")
    if timeout is None:
        timeout = 60.0 + info["duration_ms"] / 1000.0
    cmd = (_ffmpeg_base() + ["-nostats", "-v", "info", "-protocol_whitelist", "file", "-i", p,
                             "-map", "0:a:0", "-vn", "-sn", "-dn",
                             "-af", f"silencedetect=noise={float(noise_db)}dB:d={float(min_sec)}",
                             "-f", "null", "-"])
    rc, _, err = _run(cmd, timeout, cancel)
    if rc != 0:
        raise AnalysisError(_err_text(err) or f"ffmpeg 실패 (코드 {rc})")
    spans: list[tuple[int, int]] = []
    start = None
    for line in decode_output(err).splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0, int(round(float(m.group(1)) * 1000)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            end = max(start, int(round(float(m.group(1)) * 1000)))
            spans.append((start, end))
            start = None
    if start is not None:
        spans.append((start, max(start, info["duration_ms"])))
    return spans
