r"""verify-chatcoach 스킬(.claude/skills/verify-chatcoach)의 얇은 껍데기: gui_smoke 를 smoke_watch 로 감시하며 돌리고
증거를 한 폴더에 모은다. 앱을 몰고 판정하는 것은 그대로 tools/gui_smoke.py 다.

  .venv\Scripts\python tools\verify_chatcoach.py --doctor
  .venv\Scripts\python tools\verify_chatcoach.py --feature labeling [--feature training …] [--window] [--window-size 1366x768]
  .venv\Scripts\python tools\verify_chatcoach.py --cleanup

기능 이름 → gui_smoke 인자는 FEATURES 표(base · labeling · agreement · adjudication · analysis · training · viewer ·
discern · all). 기본 프로젝트는 개발용 <P>(chatcoach-data\projects\p15767532, 읽기만 — 흐름은 gui_smoke 가 사본에서 돈다).
영상은 --video → 환경 변수 CHATCOACH_VERIFY_VIDEO → <chatcoach-data>\config\verify-video.txt 첫 줄 순서로 찾는다
(영상 이름은 저장소에 적지 않는다 — 문서에서는 <V1>).

gui_smoke 에 더하는 것
- 시범 거부: 프로젝트·영상 경로의 어느 마디든 'pilot' · 'pilot-…' · 'chatcoach-pilot' · 'sealed-work' 이면 아무것도
  열기 전에 종료 2 (경로 글자만 본다).
- 실행마다 빈 데이터 폴더(%LOCALAPPDATA%\ChatCoachSmoke\chatcoach_smoke_vdata_<시각>, --data-dir)로 띄우고 끝에 지운다
  — 다른 스모크와 설정·명부·색인 캐시를 나누지 않는다.
- 증거 폴더: <chatcoach-data>\screens\verify-<기능>-<시각>\ — 스크린샷 verify-*.png · run.log(자식 출력) ·
  watch\watch.log·stack.txt(smoke_watch) · summary.json(숫자·단계 이름만). --cleanup 은 이 폴더를 지우지 않는다.
- 끝 요약 줄('== n passed, m failed')이 없으면 rc 125 (smoke_watch 가 밖에서 멈춘 실행을 rc 0 으로 보고하던 일, 26 5-2 의 4).
- 부수 효과 검사: <P> 안 파일(상대 경로·크기·수정 시각, 내용은 열지 않음) 전후 대조, 이번 실행이 남긴 chatcoach_smoke_* 수.
- 출력은 숫자·단계 이름·경로뿐(채팅 본문·닉네임·영상 이름 없음). watch.log 의 cmd 줄에는 명령줄(영상 경로)이 있다.

종료 코드: 0 모두 통과 · 1 FAIL 있음(또는 사본 남음 · <P> 바뀜 · doctor/cleanup 문제) · 2 거부·준비 안 됨 ·
124 시간 초과(smoke_watch 가 자식 나무를 끝냄) · 125 끝 요약 줄 없음.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
for _p in (TOOLS, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gui_smoke as GS          # noqa: E402  (tools/ 안의 이웃 모듈 — 맨 위 import 는 표준 라이브러리뿐)
import smoke_watch as SW        # noqa: E402

DATA_HOME = os.path.join(os.path.dirname(ROOT), "chatcoach-data")
SCREENS_DIR = GS.SCREENS_DIR                                    # <chatcoach-data>\screens (gui_smoke 가 이 밖을 거부)
DEFAULT_PROJECT = os.path.join(DATA_HOME, "projects", "p15767532")
VIDEO_ENV = "CHATCOACH_VERIFY_VIDEO"
VIDEO_CONFIG = os.path.join(DATA_HOME, "config", "verify-video.txt")
EVIDENCE_PREFIX = "verify-"
VDATA_KIND = "vdata"                                            # 실행마다의 데이터 폴더: chatcoach_smoke_vdata_<시각>
DEFAULT_MAX_S = 1200.0
TIMEOUT_RC, NO_SUMMARY_RC = SW.TIMEOUT_RC, 125

FEATURES: dict[str, tuple[str, ...]] = {
    "base": (),                                                 # 창 → 열기 → 채팅 → 타임라인 → 영상 → 탐색 → … → 닫기
    "labeling": ("--store-copy", "--labeling-flow"),
    "agreement": ("--agreement-flow",),
    "adjudication": ("--adjudication-flow",),
    "analysis": ("--analysis-flow",),
    "training": ("--training-flow",),
    "viewer": ("--viewer-flow",),
    "discern": ("--discern-flow",),
}
FEATURES["all"] = tuple(dict.fromkeys(f for k in list(FEATURES) for f in FEATURES[k]))   # 단계 목록 대조까지

# 기능마다 <P> 에 있어야 하는 것(이름만 본다, 내용은 열지 않음). 'labels/manual-*.csv' 는 1개 이상
NEEDS = {
    "labeling": ("chat/messages.jsonl",),
    "agreement": ("chat/messages.jsonl",),
    "adjudication": ("chat/messages.jsonl",),
    "analysis": ("chat/messages.jsonl",),
    "training": ("chat/messages.jsonl", "labels/manual-*.csv"),
    "viewer": ("chat/messages.jsonl", "derived/asr.json"),
    "discern": ("chat/messages.jsonl", "labels/manual-*.csv", "chat/protected/identities.jsonl"),
}

_STEP_RE = re.compile(r"\A(PASS|FAIL) (\S+) +(-?\d+) ms(?:  (.*))?\Z")
_FINAL_RE = re.compile(r"\A== (\d+) passed, (\d+) failed, ([\d.]+) s, platform=(\S+)")
_SUSPEND_RE = re.compile(r"external_suspend=([\d.]+)s")
_STEPLIST_RE = re.compile(r"\A== 단계 목록.*: (\d+)/(\d+) missing=")
_WATCH_PID_RE = re.compile(r"\A\[\s*[\d.]+\] pid\s+(\d+)\s*\Z")
_WATCH_END_RE = re.compile(r"\A\[\s*[\d.]+\] end ")


# ---------------------------------------------------------------------------- 경로
def pilot_reason(path: str | None) -> str | None:
    """시범 자리면 까닭 코드. 경로 글자만 본다(폴더를 열지 않는다)."""
    if not path:
        return None
    for part in re.split(r"[\\/]+", os.path.abspath(path)):
        p = part.lower()
        if p == "pilot" or p.startswith("pilot-") or p == "chatcoach-pilot" or p == "sealed-work":
            return "pilot_path"
    return None


def resolve_video(arg: str | None) -> tuple[str | None, str]:
    """(영상 경로 또는 None, 어디서 왔나: arg · env · config · none)."""
    if arg:
        return arg, "arg"
    v = os.environ.get(VIDEO_ENV, "").strip()
    if v:
        return v, "env"
    try:
        with open(VIDEO_CONFIG, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip().strip('"')
                if line and not line.startswith("#"):
                    return line, "config"
    except OSError:
        pass
    return None, "none"


def missing_inputs(project: str, features) -> list[str]:
    out = []
    want = ["project.json"] + [n for f in features for n in NEEDS.get(f, ())]
    for rel in dict.fromkeys(want):
        if rel.endswith("*.csv"):
            d = os.path.join(project, os.path.dirname(rel))
            pre = os.path.basename(rel).split("*")[0]
            try:
                ok = any(n.startswith(pre) and n.lower().endswith(".csv") for n in os.listdir(d))
            except OSError:
                ok = False
        else:
            ok = os.path.isfile(os.path.join(project, *rel.split("/")))
        if not ok:
            out.append(rel)
    return out


def tree_snapshot(root: str) -> dict[str, tuple[int, int]]:
    """root 아래 파일의 (크기, 수정 시각 ns). 내용은 열지 않는다(os.stat 만)."""
    out = {}
    for d, _dirs, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return out


def diff_snapshot(a: dict, b: dict) -> dict:
    return {"changed": sum(1 for k in a if k in b and a[k] != b[k]),
            "added": sum(1 for k in b if k not in a), "removed": sum(1 for k in a if k not in b)}


def smoke_copies() -> set[str]:
    root = GS.smoke_tmp_root()
    try:
        return {n for n in os.listdir(root) if n.startswith(GS.SMOKE_TMP_PREFIX) and os.path.isdir(os.path.join(root, n))}
    except OSError:
        return set()


def evidence_dirs() -> list[str]:
    try:
        return sorted(os.path.join(SCREENS_DIR, n) for n in os.listdir(SCREENS_DIR)
                      if n.startswith(EVIDENCE_PREFIX) and os.path.isdir(os.path.join(SCREENS_DIR, n)))
    except OSError:
        return []


# ---------------------------------------------------------------------------- 프로세스 (읽기만, 끝내기는 우리가 띄운 것만)
def processes() -> dict[int, tuple[int, str]] | None:
    """python.exe · pythonw.exe 의 {pid: (부모 pid, 명령줄)}. 못 보면 None."""
    if os.name != "nt":
        return None
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
          "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = {}
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        bits = line.split("\t", 2)
        if len(bits) == 3 and bits[0].strip().isdigit():
            out[int(bits[0])] = (int(bits[1]) if bits[1].strip().isdigit() else 0, bits[2])
    return out


def smoke_roots(procs: dict) -> list[int]:
    """gui_smoke 를 도는 프로세스 나무의 뿌리 pid(venv 런처와 진짜 파이썬 둘 중 위쪽만)."""
    hits = {pid for pid, (_pp, cmd) in procs.items() if "gui_smoke.py" in cmd and pid != os.getpid()}
    return sorted(pid for pid in hits if procs[pid][0] not in hits)


def kill_tree(pid: int) -> int:
    snap = SW._snapshot() or {}
    n = 0
    for p in reversed(SW.tree_pids(snap, pid)):
        h = SW._kernel32.OpenProcess(0x0001, False, p)              # PROCESS_TERMINATE
        if h:
            try:
                if SW._kernel32.TerminateProcess(h, 1):
                    n += 1
            finally:
                SW._kernel32.CloseHandle(h)
    return n


def orphan_pids() -> list[int]:
    """우리 증거 폴더의 watch.log 에 pid 는 있고 'end' 줄이 없는 실행의 자식 pid(껍데기가 중간에 죽은 실행)."""
    out = []
    for d in evidence_dirs():
        try:
            with open(os.path.join(d, "watch", "watch.log"), encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        pids = [int(m.group(1)) for m in map(_WATCH_PID_RE.match, lines) if m]
        if pids and not any(_WATCH_END_RE.match(x) for x in lines):
            out.append(pids[-1])
    return out


# ---------------------------------------------------------------------------- doctor
class Report:
    def __init__(self):
        self.n = {"OK": 0, "WARN": 0, "FAIL": 0}

    def __call__(self, level: str, name: str, detail: str = "") -> None:
        self.n[level] += 1
        print(f"{level:<4} {name:<16} {detail}", flush=True)


def doctor(args) -> int:
    r = Report()
    venv = os.path.normcase(os.path.join(ROOT, ".venv"))
    in_venv = os.path.normcase(os.path.abspath(sys.prefix)).startswith(venv)
    r("OK" if in_venv else "WARN", "python", f"{sys.version.split()[0]} venv={'.venv' if in_venv else 'other'}")
    try:
        import PySide6
        r("OK", "pyside6", PySide6.__version__)
    except ImportError as e:
        r("FAIL", "pyside6", type(e).__name__)
    import importlib.util
    r("OK" if importlib.util.find_spec("mpv") else "FAIL", "python-mpv", "found" if importlib.util.find_spec("mpv") else "없음")
    try:
        from app import __version__ as ver
        from app import paths
        miss = paths.missing_tools()
        r("OK" if not miss else "FAIL", "bin", "ffmpeg·ffprobe·libmpv ok" if not miss else "없음: " + ",".join(miss))
    except Exception as e:  # noqa: BLE001
        ver = "?"
        r("FAIL", "app", type(e).__name__)
    head = _git("rev-parse", "--short", "HEAD")
    dirty = len([x for x in (_git("status", "--porcelain") or "").splitlines() if x.strip()])
    r("OK", "build", f"app={ver} git={head or '?'} dirty_files={dirty}")
    # 프로젝트·영상
    feats = args.feature or ["labeling"]
    project = os.path.abspath(args.project or DEFAULT_PROJECT)
    if pilot_reason(project):
        r("FAIL", "project", "시범 자리 — 열지 않음 (pilot_path)")
    elif not os.path.isdir(project):
        r("FAIL", "project", f"없음: {project}")
    else:
        miss = missing_inputs(project, feats)
        r("OK" if not miss else "FAIL", "project", f"{project} features={','.join(feats)}"
          + (f" 없음={','.join(miss)}" if miss else " 입력 ok"))
        lock = os.path.join(project, "locks", "open.lock")
        if os.path.exists(lock):
            r("WARN", "open_lock", "locks/open.lock 있음 — 앱이 열고 있거나 지난 실행이 남김(지우지 말고 확인)")
    video, src = resolve_video(args.video)
    need_video = any(f != "base" for f in feats)
    if video and pilot_reason(video):
        r("FAIL", "video", f"시범 자리 — 열지 않음 (from={src})")
    elif video and os.path.isfile(video):
        r("OK", "video", f"from={src} bytes={os.path.getsize(video)}")
    else:
        r("FAIL" if need_video else "WARN", "video", f"from={src} 파일 없음" + (" — 흐름은 영상이 있어야 함" if need_video else ""))
    # 증거·사본 자리
    os.makedirs(SCREENS_DIR, exist_ok=True)
    r("OK" if os.access(SCREENS_DIR, os.W_OK) else "FAIL", "evidence_root", f"{SCREENS_DIR} 이전 증거 {len(evidence_dirs())}개")
    now = time.time()
    copies = smoke_copies()
    old = sum(1 for n in copies if now - os.path.getmtime(os.path.join(GS.smoke_tmp_root(), n)) > GS.SMOKE_STALE_S)
    r("OK" if not copies else "WARN", "smoke_copies", f"{GS.smoke_tmp_root()} 사본 {len(copies)}개(1시간 넘음 {old})"
      + (" — --cleanup" if copies else ""))
    legacy = GS.legacy_tmp_leftovers()
    r("OK" if not legacy else "WARN", "legacy_temp", f"예전 TEMP 위치 사본 {legacy}개" + (" — 지우기는 사용자 확인 뒤" if legacy else ""))
    shared = os.path.join(GS.smoke_tmp_root(), "data", "settings.ini")
    if os.path.isfile(shared):
        from PySide6.QtCore import QSettings
        model = QSettings(shared, QSettings.Format.IniFormat).value("lmstudio/model")
        r("WARN" if model else "OK", "smoke_settings", "공용 스모크 설정에 lmstudio/model 남음 — --cleanup" if model
          else "lmstudio 설정 없음")
    # 다른 스모크·외부 정지
    procs = processes()
    if procs is None:
        r("WARN", "processes", "프로세스 목록을 못 봄")
    else:
        roots = smoke_roots(procs)
        for pid in roots:
            pr = SW.probe_tree(pid)
            sus = f" Suspended {pr.suspended}/{pr.threads}" if pr else ""
            ext = pr is not None and pr.mostly_suspended
            r("FAIL", "smoke_running", f"pid={pid}{sus}" + (" — 밖에서 멈춤으로 보임(Acronis 등)" if ext else
                                                           " — 두 번 몰지 않는다. 끝날 때까지 기다리거나 --cleanup"))
        apps = sum(1 for _pp, cmd in procs.values() if "main.py" in cmd and "chatcoach" in cmd.lower())
        if not roots:
            r("OK", "smoke_running", "없음")
        r("OK", "user_app", f"사용자 앱(main.py) {apps}개 실행 중 — 건드리지 않음" if apps else "실행 중인 앱 없음")
    print(f"== doctor: ok {r.n['OK']} · warn {r.n['WARN']} · fail {r.n['FAIL']}", flush=True)
    return 0 if not r.n["FAIL"] else 1


def _git(*a) -> str | None:
    try:
        r = subprocess.run(["git", "-C", ROOT, *a], capture_output=True, timeout=30)
        return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------------------- 실행
def run(args) -> int:
    feats = list(dict.fromkeys(args.feature or ["labeling"]))
    project = os.path.abspath(args.project or DEFAULT_PROJECT)
    video, vsrc = resolve_video(args.video)
    for what, p in (("project", project), ("video", video)):
        if pilot_reason(p):
            print(f"거부: {what} 가 시범 자리입니다 (pilot_path) — 아무것도 열지 않았습니다", flush=True)
            return 2
    if not os.path.isfile(os.path.join(project, "project.json")):
        print(f"준비 안 됨: project.json 없음 ({project})", flush=True)
        return 2
    miss = missing_inputs(project, feats)
    need_video = any(f != "base" for f in feats)
    if miss:
        print(f"준비 안 됨: {project} 에 없음 {','.join(miss)} (features={','.join(feats)})", flush=True)
        return 2
    if need_video and not (video and os.path.isfile(video)):
        print(f"준비 안 됨: 영상 없음(from={vsrc}) — --video · {VIDEO_ENV} · {VIDEO_CONFIG}", flush=True)
        return 2
    procs = processes() or {}
    busy = smoke_roots(procs)
    if busy:
        print(f"거부: 다른 gui_smoke 가 돌고 있습니다 pid={busy} — 두 번 몰지 않는다(--doctor · --cleanup)", flush=True)
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = "+".join(feats)
    evid = os.path.join(SCREENS_DIR, f"{EVIDENCE_PREFIX}{tag}-{stamp}")
    os.makedirs(os.path.join(evid, "watch"), exist_ok=True)
    data_dir = os.path.join(GS.smoke_tmp_root(), f"{GS.SMOKE_TMP_PREFIX}{VDATA_KIND}_{stamp}")
    os.makedirs(data_dir, exist_ok=True)
    flags = [f for k in feats for f in FEATURES[k]]
    child = ["--project", project, "--data-dir", data_dir,
             "--screenshot", os.path.join(evid, f"{EVIDENCE_PREFIX}{tag}.png")]
    if video and os.path.isfile(video):
        child += ["--video", video]
    if not args.window:
        child.append("--offscreen")
    if args.window_size:
        child += ["--window-size", args.window_size]
    if args.timeout:
        child += ["--timeout", str(args.timeout)]
    child += list(dict.fromkeys(flags))

    before_copies = smoke_copies()
    before_tree = tree_snapshot(project)
    t0 = time.perf_counter()
    started = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    lines: list[str] = []
    log = open(os.path.join(evid, "run.log"), "w", encoding="utf-8", newline="\n")   # noqa: SIM115

    def tee(line: str) -> None:
        print(line, flush=True)
        lines.append(line)
        log.write(line + "\n")
        log.flush()
    tee(f"[verify] features={tag} platform={'window' if args.window else 'offscreen'} evidence={evid}")
    cmd, stack = SW.build_cmd(child, os.path.join(evid, "watch", "stack.txt"), python=sys.executable)
    try:
        res = SW.watch(cmd, out_dir=os.path.join(evid, "watch"), stack_path=stack, stall_s=args.stall,
                       max_s=args.max_time if args.max_time and args.max_time > 0 else None, echo=tee)
        tee(res.summary())
    finally:
        log.close()
    elapsed = time.perf_counter() - t0

    # ---- 끝난 뒤: 데이터 폴더 지우기, 부수 효과, 증거
    data_left = GS.remove_tree_checked(data_dir)
    after_tree = tree_snapshot(project)
    new_left = sorted(smoke_copies() - before_copies)
    d = diff_snapshot(before_tree, after_tree)
    steps, final, susp, steplist = [], None, 0.0, None
    for ln in lines:
        m = _STEP_RE.match(ln)
        if m:
            detail = m.group(4) or ""
            steps.append({"name": m.group(2), "ok": m.group(1) == "PASS", "ms": int(m.group(3)),
                          "skip": detail.startswith("skip")})
            s = _SUSPEND_RE.search(detail)
            susp += float(s.group(1)) if s else 0.0
            continue
        m = _FINAL_RE.match(ln)
        if m:
            final = {"passed": int(m.group(1)), "failed": int(m.group(2)), "seconds": float(m.group(3)),
                     "platform": m.group(4)}
            continue
        m = _STEPLIST_RE.match(ln)
        if m:
            steplist = {"seen": int(m.group(1)), "want": int(m.group(2)), "complete": m.group(1) == m.group(2)}
    shots = sorted(n for n in os.listdir(evid) if n.lower().endswith(".png"))
    shot_bytes = sum(os.path.getsize(os.path.join(evid, n)) for n in shots)
    failed = [s["name"] for s in steps if not s["ok"]]
    skipped = sum(1 for s in steps if s["skip"])
    if res.timed_out:
        rc = TIMEOUT_RC
    elif final is None:
        rc = NO_SUMMARY_RC
    elif res.rc != 0 or failed:
        rc = res.rc or 1
    else:
        rc = 0
    side_bad = bool(new_left or data_left or any(d.values()))
    if rc == 0 and side_bad:
        rc = 1
    longest = max((s.length(res.elapsed_s) for s in res.stalls), default=0.0)
    summary = {
        "schema": "chatcoach/verify-run", "schema_version": 1, "started_at": started, "elapsed_s": round(elapsed, 1),
        "features": feats, "gui_smoke_flags": list(dict.fromkeys(flags)),
        "platform": "window" if args.window else "offscreen", "window_size": args.window_size,
        "video_from": vsrc if video else None, "app_git": _git("rev-parse", "--short", "HEAD"),
        "rc": rc, "child_rc": res.rc, "timed_out": res.timed_out, "final_line": final is not None,
        "passed": sum(1 for s in steps if s["ok"] and not s["skip"]), "failed": len(failed), "skipped": skipped,
        "failed_steps": failed, "smoke_summary": final, "step_list": steplist,
        "steps": steps,
        "external_suspend_s": round(susp, 1),
        "watch": {"stalls": len(res.stalls), "longest_stall_s": round(longest, 1), "max_suspended": res.max_suspended,
                  "max_threads": res.max_threads, "dumps": res.dumps},
        "screenshots": {"count": len(shots), "bytes": shot_bytes},
        "side_effects": {"project_files": d, "copies_left": len(new_left), "data_dir_left": len(data_left)},
    }
    with open(os.path.join(evid, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"== verify: features={tag} rc={rc} pass={summary['passed']} fail={len(failed)} skip={skipped} "
          f"steps={len(steps)} time={elapsed:.1f}s platform={summary['platform']}"
          + (f" failed_steps={failed}" if failed else ""), flush=True)
    print(f"== 증거: {evid} (스크린샷 {len(shots)}개 {shot_bytes} bytes · run.log · watch\\watch.log · summary.json)",
          flush=True)
    print(f"== 부수 효과: <P> 파일 바뀜 {d['changed']} · 더해짐 {d['added']} · 빠짐 {d['removed']} · "
          f"남은 사본 {len(new_left)} · 데이터 폴더 남음 {len(data_left)} · external_suspend {susp:.1f}s · "
          f"Suspended 최대 {res.max_suspended}/{res.max_threads} · 멈춤 {len(res.stalls)}번", flush=True)
    if new_left:
        print("   남은 사본(직접 보거나 --cleanup): " + ", ".join(os.path.join(GS.smoke_tmp_root(), n) for n in new_left))
    if rc == NO_SUMMARY_RC:
        print("   끝 요약 줄이 없음 — 끝까지 못 감. watch\\watch.log 의 smp 줄이 MOSTLY/ALL-SUSPENDED 면 밖에서 멈춘 것"
              "(Acronis 신뢰 목록, 사용자 몫). 다음: smoke_watch.py --summarize <증거>\\watch", flush=True)
    if res.max_threads and res.max_suspended >= SW.MOSTLY_SUSPENDED * res.max_threads:
        print("   외부 정지로 보임(Suspended 80% 이상) — 코드 문제가 아님. Acronis 신뢰 목록에 .venv python.exe(사용자 몫)",
              flush=True)
    return rc


# ---------------------------------------------------------------------------- 정리
def cleanup(args) -> int:
    """이 껍데기가 띄우고 남긴 것만 치운다. 증거 폴더(screens\\verify-*)·<P>·사용자 앱 data\\ 는 건드리지 않는다."""
    bad = 0
    procs = processes()
    if procs is None:
        print("WARN 프로세스 목록을 못 봄 — 자식 끝내기는 건너뜀", flush=True)
        procs = {}
    ours = [pid for pid in orphan_pids() if pid in procs and "gui_smoke.py" in procs[pid][1]]
    for pid in ours:
        print(f"끝냄: 우리 실행이 남긴 gui_smoke 나무 pid={pid} ({kill_tree(pid)}개)", flush=True)
    if ours:
        time.sleep(1.0)
        procs = processes() or {}
    others = smoke_roots(procs)
    if others:
        print(f"WARN 다른 gui_smoke 가 돌고 있음 pid={others} — 남이 띄운 것이라 끝내지 않음, 1시간 넘은 사본만 지움", flush=True)
        removed, failed = GS.purge_stale_copies()
    else:
        removed = failed = 0
        for n in sorted(smoke_copies()):
            if GS.remove_tree_checked(os.path.join(GS.smoke_tmp_root(), n)):
                failed += 1
            else:
                removed += 1
    print(f"사본·실행 데이터 폴더: 지움 {removed} · 못 지움 {failed} ({GS.smoke_tmp_root()})", flush=True)
    bad += failed
    # 공용 스모크 데이터 폴더(gui_smoke 를 직접 돌린 것): 채팅 색인 캐시 · lmstudio 설정(실제 1234 에 붙지 않게, 26 5-2 의 13)
    shared = os.path.join(GS.smoke_tmp_root(), "data")
    project = os.path.abspath(args.project or DEFAULT_PROJECT)
    if os.path.isdir(shared) and not pilot_reason(project):
        try:
            with open(os.path.join(project, "project.json"), encoding="utf-8") as f:
                pid_ = (json.load(f) or {}).get("project_id")
        except (OSError, ValueError):
            pid_ = None
        if pid_:
            from app import chat_index as ci
            print(f"공용 스모크 데이터의 채팅 색인 캐시: {'지움' if ci.drop_cache(pid_, shared) else '없음'}", flush=True)
        ini = os.path.join(shared, "settings.ini")
        if os.path.isfile(ini):
            from PySide6.QtCore import QSettings
            s = QSettings(ini, QSettings.Format.IniFormat)
            had = [k for k in ("lmstudio/url", "lmstudio/model", "lmstudio/llm_check") if s.contains(k)]
            for k in had:
                s.remove(k)
            s.sync()
            print(f"공용 스모크 설정의 lmstudio 키: 지움 {len(had)}", flush=True)
    if not pilot_reason(project) and os.path.exists(os.path.join(project, "locks", "open.lock")):
        print("WARN <P>\\locks\\open.lock 남음 — <P> 에는 쓰지 않으므로 지우지 않음(앱이 오래된 잠금을 처리하는지 확인)",
              flush=True)
    legacy = GS.legacy_tmp_leftovers()
    if legacy:
        print(f"WARN 예전 TEMP 위치 사본 {legacy}개 — 지우기는 사용자 확인 뒤", flush=True)
    ev = evidence_dirs()
    unfinished = sum(1 for d in ev if not os.path.isfile(os.path.join(d, "summary.json")))
    print(f"증거는 그대로: {SCREENS_DIR}\\{EVIDENCE_PREFIX}* {len(ev)}개" + (f" (가장 새 것 {ev[-1]})" if ev else "")
          + (f" · 끝나지 않은 실행 {unfinished}개(summary.json 없음 — 증거 아님)" if unfinished else ""), flush=True)
    return 0 if not bad else 1


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="verify-chatcoach: gui_smoke 를 감시하며 돌리고 증거를 모은다")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--doctor", action="store_true", help="읽기만: 몰 만한 상태인가")
    g.add_argument("--cleanup", action="store_true", help="남은 사본·실행 데이터·우리 자식 프로세스만 치움(증거는 남김)")
    ap.add_argument("--feature", action="append", choices=list(FEATURES), help="몰 기능(여러 번 가능, 기본 labeling)")
    ap.add_argument("--project", help=f"프로젝트 폴더(기본 {DEFAULT_PROJECT}) — 시범 자리는 거부")
    ap.add_argument("--video", help=f"영상 파일(기본 {VIDEO_ENV} → {VIDEO_CONFIG})")
    ap.add_argument("--window", action="store_true", help="실제 창으로(기본 offscreen)")
    ap.add_argument("--window-size", help="메인 창 크기(예: 1366x768)")
    ap.add_argument("--timeout", type=float, help="gui_smoke 단계 하나의 대기 상한(초, 기본 60)")
    ap.add_argument("--stall", type=float, default=SW.DEFAULT_STALL_S, help="출력이 이만큼 없으면 멈춤으로 보고 스택 요청")
    ap.add_argument("--max-time", type=float, default=DEFAULT_MAX_S, help="이보다 길면 자식 나무를 끝냄(초, 0 = 없음)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    args = parse_args(argv)
    if args.doctor:
        return doctor(args)
    if args.cleanup:
        return cleanup(args)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
