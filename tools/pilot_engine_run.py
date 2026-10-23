"""시범 엔진 실행 한 번에 (08 D-28 5): 음성 뽑기 → 전사 → v0.2·v0.3 판정과 짝 셋 봉인 → 중간 파일 지움.

손으로 치던 봉인 명령(24 보고 6장 3단계)의 오타·옵션 착오가 2026-10-24 시각 밀림(D-28)의 직접 원인이라, 한 줄로 묶는다.

편마다(시범 프로젝트 폴더 하나 — --project 여러 번, 또는 --all 로 <데이터>\\projects\\pilot-* 전부):
  1. project.json 의 media.current.path_abs 영상에서 VOD --start-ms ~ --end-ms(기본 20:00~60:00) 음성을
     <데이터>\\sealed-work 에 16kHz 모노 wav 로 뽑는다(tools/extract_audio.py 와 같은 app.analysis.extract_audio_wav).
  2. 엔진 venv 로 전사한다(`python -m engine asr`, 기본 large-v3-turbo cuda float16). GPU 가 실패해 cpu 로 물러나면
     출력의 asr.fallback 이 1 이 된다(전사 표준 오류는 sealed-work 의 log 에만).
  3. tools/engine_to_ai.py 의 main 을 같은 프로세스에서(메모리로) 판마다 부른다:
     --from-project <P> --seal --by L01 --asr <wav 의 asr> --asr-start-ms <start> --start-ms <start> --end-ms <end>
     --engine v0.2|v0.3 --topics --words --vectors. 시각 입력은 asr_start_ms 하나만 쓴다(--file-start-ms 는 받지 않는다,
     tools/README-tools.md '시각 입력 두 가지'). v0.3 가 v0.2 로 물러나면 engine_to_ai 가 봉인하지 않는다(--allow-fallback 제외).
  4. 성공·실패 모두 finally 에서 sealed-work 의 이 실행 파일(wav·asr·log·임시)을 os.remove 로 지운다(휴지통 금지, D-15 8).

시범 폴더에는 아무것도 쓰지 않는다: 실행 내내 열기 감시(tools/pilot_unseal_day.OpenWatch 를 이어 씀)가 그 폴더 안 쓰기를
막고, 읽기는 엔진·봉인에 필요한 project.json · chat · locks 만 둔다(labels·derived 등은 열기 전에 막음). 쓰기는 AI 결과 루트 ·
sealed-work · 모델 폴더 · 임시 폴더만. 라벨링을 시작한 편(labels 폴더에 파일이 있음 — 이름만 센다, 열지 않음)은 다시 봉인해도
사전 봉인 보증이 없으므로 --allow-labeled 없이는 거부한다(15663452 같은 편이 --all 에 섞여도 다시 봉인되지 않게).

--dry-run 은 영상·project.json·엔진 venv·모델(전사·e5) 존재와 ai_seal status(봉인 수·사전 봉인·짝)만 보고 아무것도 쓰지
않는다(감시가 모든 쓰기를 막는다).

표준 출력은 편마다 JSON 한 줄: 숫자·코드·해시 앞 12자·걸린 시간만(경로·채팅·전사문 없음). 마지막 줄은 전체 요약.
이 도구는 엔진 venv 로 돌린다(v0.3·벡터가 같은 프로세스에서 onnxruntime 을 쓴다):
  .venv-engine\\Scripts\\python tools\\pilot_engine_run.py --project <…\\projects\\pilot-…> [--project …] [--dry-run]
  .venv-engine\\Scripts\\python tools\\pilot_engine_run.py --all [--projects-root <…\\projects>] [--dry-run]
종료 코드: 0 모두 됨(dry-run 은 모두 준비됨) · 1 한 편이라도 실패·거부(dry-run 은 준비 안 됨) · 2 입력 오류(아무것도 안 함)
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import builtins
import contextlib
import glob
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import time
from typing import Callable, Mapping, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import pilot_unseal_day as pud  # noqa: E402

TOOL = "pilot_engine_run"
DEFAULT_START_MS = 1_200_000          # VOD 20:00
DEFAULT_END_MS = 3_600_000            # VOD 60:00
ENGINES = ("v0.2", "v0.3")
PAIRS = ("topics", "words", "vectors")
SEALED_WORK = "sealed-work"
#: 시범 폴더 안에서 읽어도 되는 자리: 엔진 입력(project.json·chat)과 봉인의 사전 봉인 계산(locks)
ENGINE_READS = ("project.json", "chat", "locks")
#: v0.3 의미 비교·벡터를 같은 프로세스에서 돌릴 때 있어야 하는 것(앱 .venv 에는 없다)
BACKEND_MODULES = ("numpy", "onnxruntime", "tokenizers")
LABELER = "L01"
EXTRACT_TOLERANCE_MS = 1_000          # 뽑은 wav 길이가 요청 범위와 이만큼 넘게 다르면 멈춘다


class RunError(RuntimeError):
    """한 편을 멈추는 오류. 메시지는 코드뿐(경로·내용 없음)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ───────────────────────────────────────────────────────────── 열기 감시

class EngineWatch(pud.OpenWatch):
    """시범 폴더(guarded) 안: 쓰기는 모두 막고, 읽기는 ENGINE_READS 아래만. 그 밖은 OpenWatch 규칙(deny·write_ok)."""

    def check(self, path, write: bool) -> None:
        try:
            raw = os.fspath(path)
        except TypeError:
            return
        if isinstance(raw, str) and raw.lower() == os.devnull.lower():
            return                                    # subprocess 가 자식 stdin 으로 여는 널 장치(nul)
        p = pud._norm(raw)
        for g in self._g:
            if pud._under(p, g):
                if write:
                    self._deny("write_in_pilot_project")
                head = os.path.relpath(p, g).split(os.sep, 1)[0]
                if head not in ENGINE_READS:
                    self._deny("read_outside_engine:" + head.split(".", 1)[0][:16])
                self.counts["guarded_reads"] += 1
                return
        super().check(path, write)


# ───────────────────────────────────────────────────────────── 자리·입력

def data_root_of(project: str) -> str:
    """<데이터>\\projects\\<편> 이면 <데이터>, 아니면 엔진 규칙(CHATCOACH_DATA · 저장소 옆 chatcoach-data)."""
    parent = os.path.dirname(os.path.abspath(project))
    if os.path.basename(parent).lower() == "projects":
        return os.path.dirname(parent)
    from engine import asr as A
    return A.data_root()


def default_models_dir() -> str:
    from engine import asr as A
    return A.default_models_dir()


def default_engine_python() -> str:
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(ROOT, ".venv-engine", *sub)


def read_project_json(project: str) -> dict:
    with builtins.open(os.path.join(project, "project.json"), "rb") as f:
        return json.loads(f.read().decode("utf-8-sig"))


def media_of(pj: Mapping) -> tuple[str | None, int | None]:
    cur = ((pj.get("media") or {}).get("current") or {})
    path = cur.get("path_abs")
    dur = (cur.get("probe") or {}).get("duration_ms")
    return (path if isinstance(path, str) and path else None), (dur if isinstance(dur, int) else None)


def find_video(pj: Mapping, videos_dir: str | None) -> tuple[str | None, int | None, str]:
    """(영상 경로, 길이 ms, 출처 코드). project.json media.current.path_abs 가 먼저다.

    앱에서 아직 열지 않은 시범 편은 media.current 가 비어 있다(앱이 영상을 처음 열 때 연결). 그때는 시범 영상 폴더
    (tools/pilot_setup.DEFAULT_VIDEOS)에서 같은 VOD 번호의 파일을 pilot_setup 의 이름 규칙으로 찾는다 — 이름이 VOD 0 부터라고
    말하는 파일(start 0, 파일 시각 = VOD 시각)만 받는다. project.json 에는 쓰지 않는다. 출처: project_json · videos_dir ·
    none · not_from_vod_start."""
    path, dur = media_of(pj)
    if path:
        return path, dur, "project_json"
    vno = str((pj.get("broadcast") or {}).get("video_no") or "")
    if not vno or not videos_dir or not os.path.isdir(videos_dir):
        return None, None, "none"
    from tools import pilot_setup as ps
    clips, _skipped = ps.scan(videos_dir)
    same = [c for c in clips if c.video_no == vno]
    if not same:
        return None, None, "none"
    c = same[0]                                        # pilot_setup 과 같이 이름 순 첫 파일
    if c.start_ms != 0:
        return None, None, "not_from_vod_start"
    return os.path.join(videos_dir, c.file_name), c.end_ms, "videos_dir"


def labels_count(project: str) -> int:
    """labels 폴더의 항목 수(이름만 센다 — 파일은 열지 않는다). 폴더가 없으면 0."""
    try:
        return len(os.listdir(os.path.join(project, "labels")))
    except FileNotFoundError:
        return 0


def whisper_present(model: str, models_dir: str) -> bool:
    """전사 모델이 캐시에 있는가(받지 않는다 — engine.asr.resolve_model_dir 는 없으면 받으므로 부르지 않는다)."""
    if os.path.isdir(model):
        return os.path.isfile(os.path.join(model, "model.bin"))
    from engine import asr as A
    repo = A.KNOWN_REPOS.get(model, model if "/" in model else None)
    if not repo:
        return False
    pat = os.path.join(models_dir, "models--" + repo.replace("/", "--"), "snapshots", "*", "model.bin")
    return bool(glob.glob(pat))


def e5_present(models_dir: str) -> bool:
    from engine import semantic as S
    d = os.path.join(models_dir, S.E5_SMALL["dir"])
    return all(os.path.isfile(os.path.join(d, *S.E5_SMALL[k].split("/"))) for k in ("model_file", "tokenizer_file"))


def backend_missing() -> list[str]:
    return [m for m in BACKEND_MODULES if importlib.util.find_spec(m) is None]


def work_paths(work: str, name: str) -> dict:
    prefix = f"per-{name}-{os.getpid()}"
    return {"prefix": prefix, "wav": os.path.join(work, prefix + ".wav"),
            "asr": os.path.join(work, prefix + ".asr.json"), "log": os.path.join(work, prefix + ".log")}


def cleanup(work: str, prefix: str) -> int:
    """sealed-work 의 이 실행 파일(이름이 prefix 로 시작: wav·asr·log·임시 part/tmp)을 os.remove 로 지운다(휴지통 아님). 지운 수."""
    n = 0
    try:
        names = os.listdir(work)
    except FileNotFoundError:
        return 0
    for nm in names:
        if nm.startswith(prefix):
            p = os.path.join(work, nm)
            if os.path.isfile(p):
                os.remove(p)
                n += 1
    return n


def work_left(work: str) -> int:
    try:
        return len(os.listdir(work))
    except FileNotFoundError:
        return 0


# ───────────────────────────────────────────────────────────── 바꿔 끼울 수 있는 세 단계 (시험은 합성으로)

def extract_default(video: str, wav: str, *, start_ms: int, dur_ms: int) -> dict:
    """tools/extract_audio.py 와 같은 함수. {duration_ms, elapsed_s}."""
    from app import analysis
    r = analysis.extract_audio_wav(video, wav, sample_rate=16000, start_ms=start_ms, dur_ms=dur_ms)
    return {"duration_ms": r["duration_ms"], "elapsed_s": r["elapsed_s"]}


def transcribe_default(wav: str, asr: str, log: str, *, python: str, model: str, device: str, compute_type: str,
                       models_dir: str | None) -> dict:
    """엔진 venv 의 `python -m engine asr` 를 자식으로. 표준 출력(숫자 요약 JSON)을 읽어 돌려주고, 표준 오류는 log 에만."""
    cmd = [python, "-m", "engine", "asr", wav, "--out", asr, "--model", model, "--device", device,
           "--compute-type", compute_type]
    if models_dir:
        cmd += ["--models-dir", models_dir]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    with builtins.open(log, "wb") as lf:
        r = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=lf, env=env)
    out = r.stdout.decode("utf-8", errors="replace")
    i = out.find("{")
    try:
        summary = json.loads(out[i:]) if i >= 0 else None
    except ValueError:
        summary = None
    if r.returncode != 0 or not isinstance(summary, dict):
        raise RunError(f"asr_exit_{r.returncode}")
    return summary


def seal_default(argv: Sequence[str]) -> tuple[int, dict]:
    """tools/engine_to_ai.main 을 같은 프로세스에서 부르고 그 표준 출력 JSON 한 줄을 받는다. 표준 오류(asr 경고 줄)는 줄 수만."""
    from tools import engine_to_ai as eta
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = eta.main(list(argv))
    lines = [x for x in out.getvalue().splitlines() if x.strip().startswith("{")]
    try:
        doc = json.loads(lines[-1]) if lines else {}
    except ValueError:
        doc = {}
    doc["_stderr_lines"] = len([x for x in err.getvalue().splitlines() if x.strip()])
    return code, doc


EXTRACT: Callable[..., dict] = extract_default
TRANSCRIBE: Callable[..., dict] = transcribe_default
SEAL: Callable[[Sequence[str]], tuple[int, dict]] = seal_default


# ───────────────────────────────────────────────────────────── 요약 (숫자·코드·해시 앞 12자만)

def _h12(v) -> str | None:
    return v[:12] if isinstance(v, str) and v else None


def asr_numbers(s: Mapping) -> dict:
    fb = s.get("fallback")
    st = s.get("stats") or {}
    tm = s.get("timing") or {}
    au = s.get("audio") or {}
    return {"model_bin_12": _h12(s.get("model_bin_sha256")), "device": s.get("device"),
            "compute_type": s.get("compute_type"), "fallback": 1 if fb else 0,
            "fallback_from": (fb or {}).get("from_device") if isinstance(fb, Mapping) else None,
            "warnings": len(s.get("warnings") or []), "format_problems": len(s.get("format_problems") or []),
            "audio_ms": au.get("duration_ms"), "after_vad_ms": au.get("duration_after_vad_ms"),
            "segments": st.get("segment_count"), "words": st.get("word_count"),
            "realtime_x": tm.get("realtime_x"), "total_s": tm.get("total_s")}


def seal_numbers(engine: str, code: int, d: Mapping, elapsed: float) -> dict:
    if "error" in d or "sealed" not in d:
        return {"engine": engine, "exit": code, "error": d.get("error") or "no_output", "code": d.get("code"),
                "engine_version": d.get("engine_version"), "elapsed_s": round(elapsed, 1)}
    sem = d.get("semantic") if isinstance(d.get("semantic"), Mapping) else {}
    row = {"engine": engine, "exit": code, "sealed_12": _h12(d.get("sealed")), "size": d.get("size"),
           "check": d.get("check"), "reject_reasons": len(d.get("reject_reasons") or []), "new": d.get("new"),
           "presealed": d.get("presealed"), "engine_version": d.get("engine_version"),
           "semantic_status": sem.get("status"), "engine_fallback": d.get("engine_fallback"),
           "asr_warnings": d.get("_stderr_lines", 0)}
    for k in PAIRS:
        if f"{k}_sealed" in d:
            row[k] = {"sealed_12": _h12(d.get(f"{k}_sealed")), "size": d.get(f"{k}_size"), "check": d.get(f"{k}_check")}
        else:
            row[k] = None
    row["elapsed_s"] = round(elapsed, 1)
    return row


def status_numbers(project: str, ai_root: str | None) -> dict:
    """ai_seal status 의 숫자(app.seal.seal_status): 봉인 수·유효·해제·사전 봉인·짝 셋이 모두 있는 수."""
    from app import seal
    st = seal.seal_status(project, ai_root=ai_root)
    ents = list(st.entries)
    full = [e for e in ents if all(getattr(e, f"{k}_sha256") is not None for k in seal.PAIR_KINDS)]
    return {"sealed": st.sealed_count, "valid": sum(1 for e in ents if e.check == "valid"),
            "unsealed": st.unsealed_count, "presealed": sum(1 for e in ents if e.presealed),
            "pairs_full": len(full), "problems": len(st.problems or [])}


# ───────────────────────────────────────────────────────────── 한 편

def _watch(project: str, *, write_ok: Sequence[str] | None) -> EngineWatch:
    return EngineWatch([project], conditions_only=False, write_ok=write_ok)


def dry_run_project(project: str, ns) -> dict:
    """아무것도 쓰지 않고 준비 상태만. {ready, …}. 감시 아래(쓰기 모두 막음)."""
    name = os.path.basename(project)
    row: dict = {"project": name, "dry_run": True}
    w = _watch(project, write_ok=None)
    problems: list[str] = []
    with w:
        try:
            pj = read_project_json(project)
        except (OSError, ValueError):
            pj = None
            problems.append("project_json")
        video, dur, src = find_video(pj or {}, ns.videos)
        row["project_json"] = pj is not None
        row["video_source"] = src
        row["video"] = bool(video) and os.path.isfile(video)
        row["video_mb"] = round(os.path.getsize(video) / 1048576) if row["video"] else None
        row["media_ms"] = dur
        if not row["video"]:
            problems.append("video")
        if dur is not None and ns.end_ms > dur:
            problems.append("range_past_media")
        row["labels"] = labels_count(project)
        if row["labels"] and not ns.allow_labeled:
            problems.append("labeling_started")
        try:
            row["status"] = status_numbers(project, ns.ai_root)
        except Exception as e:  # noqa: BLE001 — 봉인 기록이 깨졌거나 project.json 이 안 맞음
            row["status"] = {"error": type(e).__name__}
            problems.append("seal_status")
    row["watch"] = w.summary()
    if w.violations:
        problems.append("open_watch")
    row["problems"] = problems
    row["ready"] = not problems
    return row


def run_project(project: str, ns) -> dict:
    """한 편: 뽑기 → 전사 → 판마다 봉인. 중간 파일은 finally 에서 지운다. {ok, …}."""
    from app import seal
    t0 = time.perf_counter()
    name = os.path.basename(project)
    row: dict = {"project": name, "dry_run": False, "ok": False}
    work = os.path.abspath(ns.work_dir or os.path.join(data_root_of(project), SEALED_WORK))
    if pud._under(pud._norm(work), pud._norm(project)):
        row["error"] = "work_in_project"
        return row
    os.makedirs(work, exist_ok=True)
    wp = work_paths(work, name)
    ai_root = os.path.abspath(ns.ai_root or seal.default_ai_root(project))
    models = os.path.abspath(ns.models_dir or default_models_dir())
    w = _watch(project, write_ok=[ai_root, work, models, tempfile.gettempdir()])
    times: dict = {}
    try:
        with w:
            if any(glob.glob(os.path.join(work, wp["prefix"] + "*"))):
                raise RunError("work_exists")
            n_labels = labels_count(project)
            if n_labels and not ns.allow_labeled:
                raise RunError("labeling_started")
            pj = read_project_json(project)
            video, dur, row["video_source"] = find_video(pj, ns.videos)
            if not video or not os.path.isfile(video):
                raise RunError("video_missing")
            if dur is not None and ns.end_ms > dur:
                raise RunError("range_past_media")
            row["before"] = status_numbers(project, ns.ai_root)
            t = time.perf_counter()
            ex = EXTRACT(video, wp["wav"], start_ms=ns.start_ms, dur_ms=ns.end_ms - ns.start_ms)
            times["extract"] = round(time.perf_counter() - t, 1)
            row["extract"] = {"duration_ms": ex.get("duration_ms"),
                              "wav_mb": round(os.path.getsize(wp["wav"]) / 1048576, 1)}
            if abs((ex.get("duration_ms") or 0) - (ns.end_ms - ns.start_ms)) > EXTRACT_TOLERANCE_MS:
                raise RunError("extract_length")          # 영상이 범위보다 짧다 — asr 시각과 VOD 범위가 어긋난다
            t = time.perf_counter()
            s = TRANSCRIBE(wp["wav"], wp["asr"], wp["log"], python=ns.engine_python, model=ns.model,
                           device=ns.device, compute_type=ns.compute_type, models_dir=ns.models_dir)
            times["asr"] = round(time.perf_counter() - t, 1)
            row["asr"] = asr_numbers(s)
            if row["asr"]["format_problems"]:
                raise RunError("asr_format")
            seals = []
            for eng in ns.engines:
                argv = ["--from-project", project, "--seal", "--by", ns.by, "--asr", wp["asr"],
                        "--asr-start-ms", str(ns.start_ms), "--start-ms", str(ns.start_ms), "--end-ms", str(ns.end_ms),
                        "--engine", eng] + [f"--{k}" for k in ns.pairs]
                if ns.ai_root:
                    argv += ["--ai-root", ns.ai_root]
                if ns.models_dir:
                    argv += ["--models-dir", ns.models_dir]
                if ns.allow_fallback:
                    argv.append("--allow-fallback")
                t = time.perf_counter()
                code, d = SEAL(argv)
                seals.append(seal_numbers(eng, code, d, time.perf_counter() - t))
                if code != 0:
                    break
            times["seal"] = round(sum(x["elapsed_s"] for x in seals), 1)
            row["seals"] = seals
            row["after"] = status_numbers(project, ns.ai_root)
            row["ok"] = len(seals) == len(ns.engines) and not w.violations \
                and all(x["exit"] == 0 and x.get("check") == "valid" for x in seals)
    except RunError as e:
        row["error"] = e.code
    except pud.WatchViolation as e:
        row["error"] = "open_watch:" + str(e)
    except Exception as e:  # noqa: BLE001 — 어느 단계든 실패하면 코드만 남기고 지운다
        row["error"] = type(e).__name__
    finally:
        try:
            row["removed"] = cleanup(work, wp["prefix"])
        except OSError as e:
            row["ok"], row["cleanup_error"] = False, type(e).__name__
        row["work_left"] = work_left(work)
        row["watch"] = w.summary()
        times["total"] = round(time.perf_counter() - t0, 1)
        row["elapsed_s"] = times
    return row


# ───────────────────────────────────────────────────────────── 명령줄

def _err(detail: str) -> int:
    print(json.dumps({"error": "args", "detail": detail}, ensure_ascii=False))
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python tools/pilot_engine_run.py",
                                 description="시범 엔진 실행: 음성 뽑기 → 전사 → v0.2·v0.3 판정·짝 봉인 → 중간 파일 지움")
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--project", action="append", help="시범 프로젝트 폴더(여러 번)")
    sel.add_argument("--all", action="store_true", help="--projects-root 의 pilot-* 전부")
    ap.add_argument("--projects-root", help="--all 의 프로젝트 폴더(기본 <데이터>\\projects)")
    ap.add_argument("--start-ms", type=int, default=DEFAULT_START_MS, help="VOD 시작(기본 1200000 = 20:00)")
    ap.add_argument("--end-ms", type=int, default=DEFAULT_END_MS, help="VOD 끝(기본 3600000 = 60:00)")
    ap.add_argument("--by", default=LABELER, help="봉인하는 검토자 라벨러 ID(기본 L01)")
    ap.add_argument("--ai-root", help="AI 결과 루트(기본: 프로젝트 옆 ai)")
    ap.add_argument("--videos", default=None,
                    help="project.json 에 영상이 없을 때 VOD 번호로 찾을 시범 영상 폴더(기본 ~\\Videos\\ChatCoach-pilot)")
    ap.add_argument("--work-dir", help="중간 파일 폴더(기본 <데이터>\\sealed-work)")
    ap.add_argument("--engine-python", default=default_engine_python(), help="전사에 쓸 엔진 venv python")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute-type", default="float16")
    ap.add_argument("--models-dir", help="모델 폴더(기본 <데이터>\\models)")
    ap.add_argument("--engines", default=",".join(ENGINES), help="봉인할 판(쉼표, 기본 v0.2,v0.3)")
    ap.add_argument("--pairs", default=",".join(PAIRS), help="짝(쉼표, 기본 topics,words,vectors, 빈 값 = 짝 없음)")
    ap.add_argument("--allow-fallback", action="store_true", help="v0.3 가 v0.2 로 물러나도 봉인(engine_to_ai 와 같음)")
    ap.add_argument("--allow-labeled", action="store_true",
                    help="labels 에 파일이 있는 편(라벨링 시작)도 봉인한다 — 사전 봉인 보증 없음")
    ap.add_argument("--dry-run", action="store_true", help="준비 상태만 보고 아무것도 쓰지 않는다")
    ns = ap.parse_args(argv)
    if ns.videos is None:
        from tools import pilot_setup as ps
        ns.videos = ps.DEFAULT_VIDEOS
    ns.engines = [x for x in ns.engines.split(",") if x]
    ns.pairs = [x for x in ns.pairs.split(",") if x]
    if not ns.engines or any(x not in ENGINES for x in ns.engines) or len(set(ns.engines)) != len(ns.engines):
        return _err("--engines 는 v0.2·v0.3 가운데")
    if any(x not in PAIRS for x in ns.pairs):
        return _err("--pairs 는 topics·words·vectors 가운데")
    if ns.start_ms < 0 or ns.end_ms <= ns.start_ms:
        return _err("0 <= --start-ms < --end-ms")
    if not pud.LABELER_RE.match(ns.by or ""):
        return _err("--by 는 라벨러 ID(예: L01)")
    if ns.all:
        from engine import asr as A
        root = os.path.abspath(ns.projects_root or os.path.join(A.data_root(), "projects"))
        try:
            projects = pud.find_pilots(root)
        except pud.DayError:
            return _err("--projects-root 를 읽지 못했습니다")
    else:
        projects = [os.path.abspath(p) for p in ns.project]
    if not projects:
        return _err("시범 프로젝트가 없습니다")
    for p in projects:
        if not os.path.isfile(os.path.join(p, "project.json")):
            return _err("project.json 이 없는 폴더가 있습니다")
    if len({pud._norm(p) for p in projects}) != len(projects):
        return _err("같은 편이 두 번 있습니다")
    models = ns.models_dir or default_models_dir()
    need_backend = "v0.3" in ns.engines or "vectors" in ns.pairs
    env = {"engine_python": os.path.isfile(ns.engine_python), "whisper_model": whisper_present(ns.model, models),
           "e5_model": e5_present(models) if need_backend else None,
           "backend_missing": backend_missing() if need_backend else []}
    env_ok = env["engine_python"] and env["whisper_model"] and (not need_backend or
                                                               (env["e5_model"] and not env["backend_missing"]))
    t0 = time.perf_counter()
    if ns.dry_run:
        rows = [dry_run_project(p, ns) for p in projects]
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        ok = env_ok and all(r["ready"] for r in rows)
        print(json.dumps({"tool": TOOL, "dry_run": True, "projects": len(rows), "ready": ok, "env": env,
                          "range_ms": [ns.start_ms, ns.end_ms], "engines": ns.engines, "pairs": ns.pairs,
                          "elapsed_s": round(time.perf_counter() - t0, 1)}, ensure_ascii=False))
        return 0 if ok else 1
    if not env_ok:
        # 엔진 venv·모델·실행기가 없으면 아무것도 뽑기 전에 멈춘다(긴 전사 뒤 v0.3 가 물러나 반만 봉인되지 않게)
        print(json.dumps({"tool": TOOL, "error": "env_not_ready", "env": env}, ensure_ascii=False))
        return 2
    rows = []
    for p in projects:
        r = run_project(p, ns)
        rows.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
    ok = all(r["ok"] for r in rows)
    print(json.dumps({"tool": TOOL, "dry_run": False, "projects": len(rows), "ok": sum(1 for r in rows if r["ok"]),
                      "failed": sum(1 for r in rows if not r["ok"]),
                      "work_left": rows[-1]["work_left"] if rows else None,
                      "elapsed_s": round(time.perf_counter() - t0, 1)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
