"""앱 안 업데이트 왕복 시험 — 04 12-5 의 '손으로 하는 끝까지 시험' 을 자동으로 돌린다 (M0 끝났다의 기준).

사용:
  python tools/update_roundtrip.py --old dist_test --new dist_test2 --new-version 0.0.2 [--work "%TEMP%\\테스트 문서"] [--kind lite|full]
  python tools/update_roundtrip.py --old dist_test --measure-startup        # 시작 → 창 표시 시간만 잰다

하는 일:
  1. 옛 빌드(A, -Channel test)를 <work>\\ChatCoach 에 복사하고 data\\settings.ini 를 만든다 (보존 확인용).
  2. 시험 키쌍을 만들고 새 빌드(B)로 가짜 릴리스(zip 두 개·latest.json·서명)를 준비한다.
  3. 가짜 서버를 127.0.0.1 에 띄운다.
  4. A 를 CHATCOACH_TEST_PUBKEY·CHATCOACH_AUTO_UPDATE=1·--update-base-url 로 실행한다.
     앱이 확인 → 준비(자체 점검 관문) → 교체 스크립트 시작 표식 확인 → 종료 → robocopy 교체 → --updated 로 재실행.
  5. data\\update\\last_result.json 이 ok 이고 _internal\\VERSION 이 B 버전이며 settings.ini 가 그대로이고 _backup 이 있는지 본다.
  6. 재실행된 창을 닫고(WM_CLOSE) 다시 한 번 실행해 _backup 이 지워지는지 본다.
--kind full 이면 A 의 deps.txt 를 어긋나게 바꿔 전체 zip 을 받게 한다.
--rollback 이면 6 대신: 재실행된 B(--updated)가 CHATCOACH_AUTO_ROLLBACK=1 로 [도움말 → 이전 버전으로 되돌리기] 메뉴 함수
(MainWindow.rollback_dialog, 확인 없이)를 불러 A 로 되돌린다. last_result 가 rolled_back 이고 VERSION 이 A 이고
settings.ini 가 그대로이고 skip_version 이 B 인지 본 뒤, --rolled-back 으로 켜진 A 를 닫고 다음 정상 시작에서
_backup 이 지워지는지 본다.
모든 실행은 LOCALAPPDATA 를 <work>\\localappdata 로 바꿔 띄운다(업데이트·되돌리기 직전 백업이 사용자 백업 폴더에 쌓이지 않게).
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from app.appspec import SPEC  # noqa: E402
import fake_release_server as frs  # noqa: E402
import sign_manifest as sm  # noqa: E402

CREATE_NO_WINDOW = 0x08000000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_for(cond, timeout: float, what: str, step: float = 0.25) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    log(f"시간 초과: {what} ({timeout:.0f}초)")
    return False


def procs_under(prefix: str) -> list[int]:
    """prefix 폴더 아래 exe 로 실행 중인 프로세스 pid 목록 (PowerShell, UTF-8 출력)."""
    ps = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
          "Get-Process | Where-Object { $_.Path } | ForEach-Object { \"$($_.Id)|$($_.Path)\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    out = []
    p = os.path.normcase(prefix.rstrip("\\") + "\\")
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        pid, _, path = line.strip().partition("|")
        if pid.isdigit() and os.path.normcase(path).startswith(p):
            out.append(int(pid))
    return out


def close_window(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, creationflags=CREATE_NO_WINDOW)


def read_json(path: str):
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        return json.loads(data.decode("utf-8"))
    except (OSError, ValueError):
        return None


def measure_startup(app_dir: str, env: dict, hold=None, hold_timeout: float = 30.0) -> float:
    """exe 실행 → 로그의 '창 표시' 줄까지. hold() 가 True 가 될 때까지(최대 hold_timeout) 창을 둔 뒤 닫고 초 단위로 돌려준다."""
    exe = os.path.join(app_dir, SPEC.exe_name)
    log_file = os.path.join(app_dir, "data", "logs", "chatcoach.log")
    before = os.path.getsize(log_file) if os.path.isfile(log_file) else 0
    t0 = time.perf_counter()
    p = subprocess.Popen([exe], env=env, cwd=app_dir)

    def shown() -> bool:
        try:
            with open(log_file, "rb") as f:
                f.seek(before)
                return "창 표시" in f.read().decode("utf-8", "replace")
        except OSError:
            return False
    ok = wait_for(shown, 60, "창 표시")
    dt = time.perf_counter() - t0
    if hold is not None:
        wait_for(hold, hold_timeout, "창을 닫기 전 조건")
    else:
        time.sleep(1.0)
    close_window(p.pid)
    p.wait(timeout=30)
    return dt if ok else float("nan")


def _version_of(app_dir: str) -> str:
    try:
        return open(os.path.join(app_dir, "_internal", "VERSION"), encoding="utf-8-sig").read().strip()
    except OSError:
        return ""


def run_rollback_phase(app_dir: str, result_path: str, old_version: str, new_version: str, env: dict,
                       lad: str) -> bool:
    """--updated 로 재실행된 B 가 메뉴 되돌리기를 부르고 → 교체 스크립트(-Rollback) → A 가 --rolled-back 으로 다시 뜬다."""
    t0 = time.perf_counter()
    if not wait_for(lambda: (read_json(result_path) or {}).get("result") in ("rolled_back", "rollback_failed",
                                                                            "blocked"), 300, "되돌리기 결과"):
        return False
    last = read_json(result_path)
    log(f"되돌리기 last_result ({time.perf_counter() - t0:.1f}초): {last}")
    state = read_json(os.path.join(app_dir, "data", "update", "state.json")) or {}
    pre = os.path.join(lad, "ChatCoach", "backups", "pre-update")
    try:
        n_pre = len([n for n in os.listdir(pre) if n.endswith(".zip")])
    except OSError:
        n_pre = 0
    checks = {
        "result rolled_back": last.get("result") == "rolled_back",
        "from == new, to == old": last.get("from") == new_version and last.get("to") == old_version,
        "VERSION == old": _version_of(app_dir) == old_version,
        "settings.ini 보존": open(os.path.join(app_dir, "data", "settings.ini"), encoding="utf-8").read().startswith("[test]\nkeep=1\n"),
        "skip_version == new": state.get("skip_version") == new_version,
        "backup_ok.json 지움": not os.path.isfile(os.path.join(app_dir, "data", "update", "backup_ok.json")),
        "updating.lock 없음": not os.path.isfile(os.path.join(app_dir, "data", "update", "updating.lock")),
        "업데이트·되돌리기 직전 data 백업 2개": n_pre >= 2,
        "libmpv 유지": os.path.isfile(os.path.join(app_dir, "_internal", "libmpv-2.dll")),
    }
    ok = True
    for k, v in checks.items():
        log(f"  {'OK ' if v else 'NG '} {k}")
        ok = ok and bool(v)
    if not wait_for(lambda: bool(procs_under(app_dir)), 60, "--rolled-back 으로 다시 뜬 창"):
        return False
    time.sleep(5)
    for pid in procs_under(app_dir):
        close_window(pid)
    wait_for(lambda: not procs_under(app_dir), 60, "창 닫힘")
    try:
        with open(os.path.join(app_dir, "data", "logs", "chatcoach.log"), encoding="utf-8", errors="replace") as f:
            tail = [ln for ln in f.readlines() if "되돌리기" in ln or "rolled" in ln or "건너뛴" in ln][-8:]
        print("--- chatcoach.log (되돌리기 줄) ---\n" + "".join(tail))
    except OSError:
        pass
    backup_dir = os.path.join(app_dir, "_backup")
    still = os.path.isdir(backup_dir)
    log(f"  {'OK ' if still else 'NG '} --rolled-back 실행에서는 _backup 을 지우지 않음")
    dt = measure_startup(app_dir, dict(env), hold=lambda: not os.path.isdir(backup_dir))
    log(f"되돌린 버전 시작 → 창 표시: {dt:.2f}초")
    gone = wait_for(lambda: not os.path.isdir(backup_dir), 10, "_backup 삭제")
    log(f"  {'OK ' if gone else 'NG '} 다음 정상 시작 뒤 _backup 삭제")
    return ok and still and gone


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--old", required=True, help="옛 빌드 dist 폴더 (안에 ChatCoach\\)")
    ap.add_argument("--new", help="새 빌드 dist 폴더")
    ap.add_argument("--new-version")
    ap.add_argument("--work", default=os.path.join(os.environ.get("TEMP", "."), "테스트 문서"))
    ap.add_argument("--kind", choices=["lite", "full"], default="lite")
    ap.add_argument("--measure-startup", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--rollback", action="store_true", help="업데이트 뒤 메뉴 되돌리기까지 (04 6-10)")
    ap.add_argument("--notes", help="릴리스 노트 파일 (없으면 docs/release-notes/v<새 버전>.md, 그것도 없으면 시험용 노트)")
    a = ap.parse_args(argv)

    work = os.path.abspath(a.work)
    app_dir = os.path.join(work, SPEC.zip_top)
    if "onedrive" in os.path.normcase(work):
        raise SystemExit("작업 폴더는 OneDrive 밖이어야 합니다")
    for pid in procs_under(app_dir):
        log(f"남은 프로세스 종료 {pid}")
        close_window(pid)
    time.sleep(1)
    if os.path.isdir(app_dir):
        shutil.rmtree(app_dir)
    os.makedirs(work, exist_ok=True)
    shutil.copytree(os.path.join(a.old, SPEC.zip_top), app_dir)
    old_version = open(os.path.join(app_dir, "_internal", "VERSION"), encoding="utf-8-sig").read().strip()
    os.makedirs(os.path.join(app_dir, "data"), exist_ok=True)
    with open(os.path.join(app_dir, "data", "settings.ini"), "w", encoding="utf-8") as f:
        f.write("[test]\nkeep=1\n")
    log(f"A 사본: {app_dir} (v{old_version})")

    env = dict(os.environ)
    lad = os.path.join(work, "localappdata")
    os.makedirs(lad, exist_ok=True)
    env["LOCALAPPDATA"] = lad
    base_env = dict(env)
    if a.measure_startup:
        dt = measure_startup(app_dir, env)
        log(f"시작 → 창 표시: {dt:.2f}초")
        return 0

    if not a.new or not a.new_version:
        raise SystemExit("--new 와 --new-version 이 필요합니다")
    # 2. 시험 키·가짜 릴리스
    rel_dir = os.path.join(work, "release")
    shutil.rmtree(rel_dir, ignore_errors=True)
    kp = sm.generate_keypair()
    key_path = os.path.join(work, "test.key")
    with open(key_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(sm.secret_file_text(kp, None))
    notes = a.notes
    if not notes and not os.path.isfile(os.path.join(ROOT, "docs", "release-notes", f"v{a.new_version}.md")):
        # 저장소에 그 버전의 노트가 없으면(시험 전용 버전) 작업 폴더에 시험용 노트를 만든다 (저장소는 건드리지 않음)
        notes = os.path.join(work, f"notes-v{a.new_version}.md")
        with open(notes, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"min_supported: {old_version}\nmandatory: false\n---\n업데이트·되돌리기 왕복 시험용 빌드입니다.\n")
    rc = frs.main(["prepare", "--dist", a.new, "--version", a.new_version, "--out", rel_dir, "--key", key_path]
                  + (["--notes", notes] if notes else []))
    if rc:
        return rc
    if a.kind == "full":
        with open(os.path.join(app_dir, "_internal", "deps.txt"), "a", encoding="utf-8") as f:
            f.write("ffmpeg=mismatch\n")
    # 3. 서버
    frs.Handler.state = frs._State(rel_dir, "normal", key_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), frs.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    log(f"가짜 서버 {base}")
    # 4. A 실행
    env["CHATCOACH_TEST_PUBKEY"] = kp.public_line()
    env["CHATCOACH_AUTO_UPDATE"] = "1"
    if a.rollback:
        env["CHATCOACH_AUTO_ROLLBACK"] = "1"      # --updated 로 켜진 B 만 메뉴 되돌리기를 부른다
    exe = os.path.join(app_dir, SPEC.exe_name)
    t0 = time.perf_counter()
    p = subprocess.Popen([exe, "--update-base-url", base], env=env, cwd=app_dir)
    log(f"A 실행 pid={p.pid}")
    try:
        p.wait(timeout=300)
    except subprocess.TimeoutExpired:
        log("A 가 300초 안에 종료하지 않음 (업데이트가 시작되지 않았나?)")
        close_window(p.pid)
        return 1
    log(f"A 종료 (exit {p.returncode}, {time.perf_counter() - t0:.1f}초) → 교체 스크립트 대기")
    result_path = os.path.join(app_dir, "data", "update", "last_result.json")
    if not wait_for(lambda: read_json(result_path) is not None, 240, "last_result.json"):
        return 1
    last = read_json(result_path)
    log(f"last_result: {last}")
    # 5. 검사
    ok = True
    ver = open(os.path.join(app_dir, "_internal", "VERSION"), encoding="utf-8-sig").read().strip()
    checks = {
        "result ok": last.get("result") == "ok",
        "to == new": last.get("to") == a.new_version,
        "VERSION == new": ver == a.new_version,
        "settings.ini 보존": open(os.path.join(app_dir, "data", "settings.ini"), encoding="utf-8").read() == "[test]\nkeep=1\n",
        "_backup 존재": os.path.isfile(os.path.join(app_dir, "_backup", SPEC.exe_name)),
        "_backup VERSION == old": os.path.isfile(os.path.join(app_dir, "_backup", "_internal", "VERSION")) and
                                   open(os.path.join(app_dir, "_backup", "_internal", "VERSION"), encoding="utf-8-sig").read().strip() == old_version,
        "backup_ok.json": os.path.isfile(os.path.join(app_dir, "data", "update", "backup_ok.json")),
        "ffmpeg 유지": os.path.isfile(os.path.join(app_dir, "_internal", "ffmpeg", "ffmpeg.exe")),
        "libmpv 유지": os.path.isfile(os.path.join(app_dir, "_internal", "libmpv-2.dll")),
        "updating.lock 없음": not os.path.isfile(os.path.join(app_dir, "data", "update", "updating.lock")),
    }
    for k, v in checks.items():
        log(f"  {'OK ' if v else 'NG '} {k}")
        ok = ok and bool(v)
    if a.rollback:
        ok = run_rollback_phase(app_dir, result_path, old_version, a.new_version, base_env, lad) and ok
        srv.shutdown()
        log("왕복 + 되돌리기 시험 " + ("통과" if ok else "실패"))
        return 0 if ok else 1
    # 재실행된 새 버전이 떠 있어야 한다
    if not wait_for(lambda: bool(procs_under(app_dir)), 60, "재실행된 창"):
        ok = False
    else:
        pids = procs_under(app_dir)
        log(f"재실행된 프로세스: {pids}")
        time.sleep(4)   # 창이 뜨고 로그를 쓸 시간
        for pid in pids:
            close_window(pid)
        wait_for(lambda: not procs_under(app_dir), 60, "창 닫힘")
    log_tail = ""
    try:
        with open(os.path.join(app_dir, "data", "logs", "chatcoach.log"), encoding="utf-8", errors="replace") as f:
            log_tail = "".join(f.readlines()[-12:])
    except OSError:
        pass
    print("--- chatcoach.log (끝) ---\n" + log_tail)
    # 6. 다음 정상 시작에서 _backup 삭제 (창이 뜨고 3초 뒤 백그라운드에서 지운다 — 그때까지 창을 둔다)
    backup_dir = os.path.join(app_dir, "_backup")
    still_there = os.path.isdir(backup_dir)
    log(f"  {'OK ' if still_there else 'NG '} --updated 실행에서는 _backup 을 지우지 않음")
    ok = ok and still_there
    dt = measure_startup(app_dir, dict(os.environ), hold=lambda: not os.path.isdir(backup_dir))
    log(f"새 버전 시작 → 창 표시: {dt:.2f}초")
    gone = wait_for(lambda: not os.path.isdir(backup_dir), 10, "_backup 삭제")
    log(f"  {'OK ' if gone else 'NG '} 다음 정상 시작 뒤 _backup 삭제")
    ok = ok and gone
    srv.shutdown()
    log("왕복 시험 " + ("통과" if ok else "실패"))
    if ok and not a.keep:
        pass  # 폴더는 남겨 둔다 (사람이 들여다볼 수 있게). 다음 실행이 지운다
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
