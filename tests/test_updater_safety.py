"""교체 스크립트(replace.ps1)를 실제 robocopy · PowerShell 로 돌리는 사고 재현 테스트 (local_only, Windows 전용).

ClipSlicer tests/test_updater_safety.py · test_updater.py(_run_replacer) 기법을 옮겼다.
- 가짜 앱: %SystemRoot%\\System32\\PING.EXE 를 ChatCoach.exe 로 복사 (끝에 표식 바이트를 붙여 옛/새를 구분).
  스크립트가 끝에 '<앱>\\ChatCoach.exe --updated' 로 재실행하면 PING 이 모르는 옵션이라 바로 끝난다.
- 앱 폴더는 tmp 아래 한글 폴더('테스트 문서'), 새 폴더는 <앱>\\_update\\upd-test\\new\\ChatCoach\\ (실제 준비 위치).
- 재실행·결과 확인은 data\\update\\last_result.json 의 result 로 한다. -AppPid 는 이미 끝난 프로세스의 pid.
경우: ① 전체 교체 ② 경량 교체(-Lite) ③ 같은 폴더의 다른 창이 안 닫힘(blocked, 죽이지 않음)
     ④ 새 폴더에 필수 파일 없음 ⑤ 백업 실패(_backup 자리에 파일을 두어 robocopy rc 16) ⑥ -NoBackup
     ⑦ 경량 교체의 백업도 이전 버전 전체 사본인가 (04 6-8 5단계)
     ⑧ 지난 backup_ok 는 이번 백업 전에 지운다 ⑨ -Rollback: _backup 에서 되돌림 · 사본이 불완전하면 아무것도 안 바꿈.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import types
from pathlib import Path

import pytest

from app import updater

pytestmark = [pytest.mark.local_only,
              pytest.mark.skipif(os.name != "nt", reason="robocopy · Windows PowerShell 필요")]

PING = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "PING.EXE")
OLD_TAIL = b"\x00SL-OLD-EXE"
NEW_TAIL = b"\x00SL-NEW-EXE"
STAGE_ID = "upd-test"
NO_WINDOW = updater.CREATE_NO_WINDOW


def _w(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _ping_with(tail: bytes) -> bytes:
    with open(PING, "rb") as f:
        return f.read() + tail          # PE 끝에 덧붙인 바이트(오버레이)는 실행에 영향이 없다


def make_app(tmp_path: Path) -> Path:
    app = tmp_path / "테스트 문서" / "ChatCoach"
    _w(app / "ChatCoach.exe", _ping_with(OLD_TAIL))
    _w(app / "_internal" / "python312.dll", b"OLD-PY")
    _w(app / "_internal" / "VERSION", b"0.0.1\n")
    _w(app / "_internal" / "libmpv-2.dll", b"mpv-old")
    _w(app / "_internal" / "ffmpeg" / "ffmpeg.exe", b"ff-old")
    _w(app / "_internal" / "old_only.dll", b"old")
    _w(app / "_internal" / "data" / "inner.txt", b"old-inner")    # 보존 폴더와 같은 이름이지만 _internal 안
    _w(app / "_internal" / "data" / "stale.txt", b"stale")
    _w(app / "data" / "settings.ini", "[x]\n한글=값\n".encode("utf-8"))
    (app / "data" / "update").mkdir(parents=True, exist_ok=True)
    return app


def make_new(app: Path, lite: bool = False, drop: tuple[str, ...] = ()) -> Path:
    new = app / "_update" / STAGE_ID / "new" / "ChatCoach"
    _w(new / "ChatCoach.exe", _ping_with(NEW_TAIL))
    _w(new / "_internal" / "python312.dll", b"NEW-PY")
    _w(new / "_internal" / "VERSION", b"0.0.2\n")
    _w(new / "_internal" / "new_only.dll", b"new")
    _w(new / "_internal" / "data" / "inner.txt", b"new-inner")
    if not lite:
        _w(new / "_internal" / "libmpv-2.dll", b"mpv-new")
        _w(new / "_internal" / "ffmpeg" / "ffmpeg.exe", b"ff-new")
    for rel in drop:
        (new / rel).unlink()
    return new


def run_replacer(app: Path, new: Path, lite: bool = False, wait_sec: int | None = None, no_backup: bool = False,
                 rollback: bool = False, versions: tuple[str, str] = ("0.0.1", "0.0.2"), extra: tuple[str, ...] = (),
                 lock: bool = True):
    """앱이 하는 순서대로: 잠금 → replace.ps1 → 실행. 끝날 때까지 기다린다 (최대 120초).
    extra: 덧붙일 스크립트 인자(-TouchSec · -CopyExe 시험용). lock=False 는 앱이 잠금을 지운(포기한) 경우."""
    stage = app / "_update" / STAGE_ID
    stage.mkdir(parents=True, exist_ok=True)
    script = stage / "replace.ps1"
    updater.write_replacer(str(script))
    if lock:
        updater.write_updating_lock(str(app / "data"), os.getpid(), *versions)
    dead = subprocess.Popen(["cmd", "/c", "exit 0"], creationflags=NO_WINDOW)   # 이미 끝난 '앱' pid
    dead.wait()
    cmd = updater.replacer_command(str(script), str(app), str(new), STAGE_ID, "ChatCoach.exe", dead.pid,
                                   *versions, lite, no_backup, rollback=rollback)
    if wait_sec is not None:
        cmd += ["-WaitSec", str(wait_sec)]
    cmd += list(extra)
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=NO_WINDOW)
    dt = time.monotonic() - t0
    log_path = app / "data" / "logs" / "update.log"
    log = log_path.read_text(encoding="utf-8-sig", errors="replace") if log_path.exists() else ""
    return types.SimpleNamespace(rc=r.returncode, dt=dt, log=log, result=updater.read_last_result(str(app / "data")),
                                 out=updater.decode_output(r.stdout) + updater.decode_output(r.stderr), pid=dead.pid)


def _kill_from(app: Path) -> None:
    """스크립트가 재실행한 가짜 앱(PING)이 남아 있으면 정리한다 (tmp 폴더 삭제가 막히지 않게)."""
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='ChatCoach.exe'\" | Where-Object { "
                    "$_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:SL_APP + '\\', "
                    "[System.StringComparison]::OrdinalIgnoreCase) } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                   capture_output=True, timeout=60, env=dict(os.environ, SL_APP=str(app)), creationflags=NO_WINDOW)


@pytest.fixture()
def app(tmp_path):
    a = make_app(tmp_path)
    yield a
    _kill_from(a)


def _unchanged(app: Path) -> None:
    assert (app / "ChatCoach.exe").read_bytes().endswith(OLD_TAIL)
    assert (app / "_internal" / "python312.dll").read_bytes() == b"OLD-PY"
    assert (app / "_internal" / "old_only.dll").exists()
    assert not (app / "_internal" / "new_only.dll").exists()
    assert (app / "_internal" / "data" / "inner.txt").read_bytes() == b"old-inner"
    assert (app / "data" / "settings.ini").read_text(encoding="utf-8") == "[x]\n한글=값\n"


def _common_after(app: Path, res, result: str) -> None:
    assert res.result is not None, (res.rc, res.out, res.log)
    assert res.result["result"] == result, (res.result, res.log, res.out)
    assert res.result["from"] == "0.0.1" and res.result["to"] == "0.0.2"
    assert (app / "_update" / STAGE_ID / "started").exists()                     # 시작 표식
    assert not (app / "data" / "update" / "updating.lock").exists()             # 잠금은 스크립트가 지운다
    assert "교체 시작" in res.log
    assert res.dt < 120


# --------------------------------------------------------------------------- ① 전체 교체
def test_full_replace(app):
    new = make_new(app)
    res = run_replacer(app, new)
    assert res.rc == 0, (res.out, res.log)
    _common_after(app, res, "ok")
    r = res.result
    assert 0 <= r["backup_rc"] < 8 and 0 <= r["copy_rc"] < 8 and r["restore_rc"] == -1
    # 새 파일로 바뀜
    assert (app / "ChatCoach.exe").read_bytes().endswith(NEW_TAIL)
    assert (app / "_internal" / "python312.dll").read_bytes() == b"NEW-PY"
    assert (app / "_internal" / "VERSION").read_bytes() == b"0.0.2\n"
    assert (app / "_internal" / "new_only.dll").exists() and not (app / "_internal" / "old_only.dll").exists()
    assert (app / "_internal" / "libmpv-2.dll").read_bytes() == b"mpv-new"
    assert (app / "_internal" / "ffmpeg" / "ffmpeg.exe").read_bytes() == b"ff-new"
    # 전체 경로 /XD: <앱>\data 는 보존, _internal\data 는 교체·정리됨 (이름 매칭이 아님)
    assert (app / "data" / "settings.ini").read_text(encoding="utf-8") == "[x]\n한글=값\n"
    assert (app / "_internal" / "data" / "inner.txt").read_bytes() == b"new-inner"
    assert not (app / "_internal" / "data" / "stale.txt").exists()
    # 백업: 옛 파일 전체(_internal\data 포함), 보존 폴더는 빠짐
    backup = app / "_backup"
    assert (backup / "ChatCoach.exe").read_bytes().endswith(OLD_TAIL)
    assert (backup / "_internal" / "old_only.dll").read_bytes() == b"old"
    assert (backup / "_internal" / "data" / "inner.txt").read_bytes() == b"old-inner"
    assert not (backup / "data").exists() and not (backup / "_update").exists() and not (backup / "_backup").exists()
    with open(app / "data" / "update" / "backup_ok.json", encoding="ascii") as f:
        assert json.load(f)["version"] == "0.0.1"
    assert "성공" in res.log
    # 스크립트는 자기 폴더(_update\upd-test)를 지우지 않고, 원본 새 폴더도 그대로다
    assert (app / "_update" / STAGE_ID / "replace.ps1").exists()
    assert (new / "ChatCoach.exe").read_bytes().endswith(NEW_TAIL)


# --------------------------------------------------------------------------- ② 경량 교체
def test_lite_replace_keeps_ffmpeg_and_libmpv(app):
    new = make_new(app, lite=True)
    res = run_replacer(app, new, lite=True)
    assert res.rc == 0, (res.out, res.log)
    _common_after(app, res, "ok")
    assert (app / "_internal" / "ffmpeg" / "ffmpeg.exe").read_bytes() == b"ff-old"     # 유지
    assert (app / "_internal" / "libmpv-2.dll").read_bytes() == b"mpv-old"            # 유지
    assert (app / "ChatCoach.exe").read_bytes().endswith(NEW_TAIL)
    assert (app / "_internal" / "python312.dll").read_bytes() == b"NEW-PY"
    assert (app / "_internal" / "new_only.dll").exists() and not (app / "_internal" / "old_only.dll").exists()
    assert (app / "_internal" / "data" / "inner.txt").read_bytes() == b"new-inner"
    assert (app / "data" / "settings.ini").read_text(encoding="utf-8") == "[x]\n한글=값\n"
    assert (app / "_backup" / "ChatCoach.exe").read_bytes().endswith(OLD_TAIL)
    assert "경량" in res.log and "성공" in res.log


def test_lite_backup_is_complete_previous_version(app):
    """04 6-8 5단계: 백업은 data·_backup·_update 만 뺀 '이전 버전 전체 사본' 이어야 한다 (되돌리기·수동 복구의 근거).
    경량 교체의 /XD _internal\\ffmpeg · /XF libmpv-2.dll 은 7단계(교체)에만 붙는다."""
    new = make_new(app, lite=True)
    res = run_replacer(app, new, lite=True)
    assert res.rc == 0 and res.result["result"] == "ok", (res.out, res.log)
    backup = app / "_backup" / "_internal"
    assert (backup / "libmpv-2.dll").exists(), "경량 교체의 백업에 libmpv-2.dll 이 없다"
    assert (backup / "ffmpeg" / "ffmpeg.exe").exists(), "경량 교체의 백업에 ffmpeg\\ 가 없다"


# --------------------------------------------------------------------------- ③ 다른 창이 열려 있음
def test_other_window_open_blocks_without_killing(app):
    new = make_new(app)
    other = subprocess.Popen([str(app / "ChatCoach.exe"), "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)   # 같은 폴더에서 60초 버티는 '다른 창'
    try:
        res = run_replacer(app, new, wait_sec=3)
        alive = other.poll() is None
    finally:
        other.kill()
        other.wait(10)
    assert alive, "교체 스크립트가 다른 창을 죽였다"
    assert res.rc == 2, (res.out, res.log)
    _common_after(app, res, "blocked")
    assert res.result["backup_rc"] == -1 and res.result["copy_rc"] == -1
    _unchanged(app)
    assert (app / "_internal" / "ffmpeg" / "ffmpeg.exe").read_bytes() == b"ff-old"
    assert not (app / "_backup").exists()
    assert not (app / "data" / "update" / "backup_ok.json").exists()
    assert "기다림" in res.log and "중단" in res.log
    assert res.dt < 60


# --------------------------------------------------------------------------- ④ 필수 파일 없음
def test_missing_required_file_changes_nothing(app):
    new = make_new(app, drop=("_internal/python312.dll",))
    res = run_replacer(app, new)
    assert res.rc == 1, (res.out, res.log)
    _common_after(app, res, "missing_file")
    _unchanged(app)
    assert not (app / "_backup").exists()
    assert "필수 파일 없음" in res.log and "python312.dll" in res.log


def test_full_update_requires_libmpv(app):
    # 전체 교체인데 새 폴더에 libmpv 가 없으면(백신 격리 등) 거부
    new = make_new(app, lite=True)
    res = run_replacer(app, new, lite=False)
    assert res.rc == 1 and res.result["result"] == "missing_file", (res.out, res.log)
    _unchanged(app)


# --------------------------------------------------------------------------- ⑤ 백업 실패
def test_backup_failure_changes_nothing(app):
    (app / "_backup").write_bytes(b"not a folder")       # 백업 대상 자리에 파일 → robocopy rc 16
    new = make_new(app)
    res = run_replacer(app, new)
    assert res.rc == 1, (res.out, res.log)
    _common_after(app, res, "backup_failed")
    assert res.result["backup_rc"] >= 8 and res.result["copy_rc"] == -1
    _unchanged(app)
    assert not (app / "data" / "update" / "backup_ok.json").exists()
    assert "백업 실패" in res.log


# --------------------------------------------------------------------------- ⑥ -NoBackup
def test_no_backup_switch(app):
    new = make_new(app)
    res = run_replacer(app, new, no_backup=True)
    assert res.rc == 0, (res.out, res.log)
    _common_after(app, res, "ok")
    assert res.result["backup_rc"] == -1
    assert not (app / "_backup").exists() and not (app / "data" / "update" / "backup_ok.json").exists()
    assert (app / "ChatCoach.exe").read_bytes().endswith(NEW_TAIL)


# --------------------------------------------------------------------------- ⑧ 지난 backup_ok 는 이번 백업 전에 지운다
def test_stale_backup_ok_removed_when_backup_fails(app):
    """지난 업데이트의 backup_ok 가 남은 채 이번 백업이 실패하면, 반쯤 바뀐 _backup 을 옛 기록이 '완료' 로
    가리키게 된다. 스크립트는 백업 전에 기록을 지운다 → 되돌리기 판정(rollback_status)이 그 사본을 쓰지 않는다."""
    (app / "data" / "update" / "backup_ok.json").write_text('{"version":"0.0.0","at":"x"}', encoding="ascii")
    (app / "_backup").write_bytes(b"not a folder")
    res = run_replacer(app, make_new(app))
    assert res.result["result"] == "backup_failed", (res.out, res.log)
    assert not (app / "data" / "update" / "backup_ok.json").exists()


# --------------------------------------------------------------------------- ⑨ -Rollback (되돌리기)
def _updated_app(app: Path) -> Path:
    """① 과 같은 전체 교체를 한 번 해서 '업데이트 직후' 를 만든다: <앱> = 0.0.2, _backup = 0.0.1."""
    res = run_replacer(app, make_new(app))
    assert res.result["result"] == "ok", (res.out, res.log)
    return app / "_backup"


def test_rollback_restores_previous_version(app):
    backup = _updated_app(app)
    (app / "data" / "settings.ini").write_text("[x]\n한글=되돌리기 전에 바꿈\n", encoding="utf-8")
    res = run_replacer(app, backup, rollback=True, versions=("0.0.2", "0.0.1"))
    assert res.rc == 0, (res.out, res.log)
    assert res.result["result"] == "rolled_back" and res.result["from"] == "0.0.2" and res.result["to"] == "0.0.1"
    assert res.result["backup_rc"] == -1 and res.result["restore_rc"] == -1
    assert (app / "ChatCoach.exe").read_bytes().endswith(OLD_TAIL)
    assert (app / "_internal" / "VERSION").read_bytes() == b"0.0.1\n"
    assert (app / "_internal" / "old_only.dll").exists() and not (app / "_internal" / "new_only.dll").exists()
    assert (app / "_internal" / "data" / "inner.txt").read_bytes() == b"old-inner"     # _internal 안 같은 이름 폴더도 되돌림
    assert (app / "_internal" / "libmpv-2.dll").read_bytes() == b"mpv-old"
    assert (app / "data" / "settings.ini").read_text(encoding="utf-8") == "[x]\n한글=되돌리기 전에 바꿈\n"   # data 는 그대로
    assert backup.is_dir() and (backup / "ChatCoach.exe").read_bytes().endswith(OLD_TAIL)    # 사본은 다음 정상 시작이 지운다
    assert not (app / "data" / "update" / "backup_ok.json").exists()
    assert not (app / "data" / "update" / "updating.lock").exists()
    assert "rollback=True" in res.log and "되돌리기 성공" in res.log
    assert updater.cleanup_after_update(str(app), str(app / "data"), "0.0.1") > 0 and not backup.exists()


def test_rollback_with_incomplete_copy_changes_nothing(app):
    backup = _updated_app(app)
    (backup / "_internal" / "python312.dll").unlink()                  # 사본에서 필수 파일이 사라짐 (백신 격리 등)
    res = run_replacer(app, backup, rollback=True, versions=("0.0.2", "0.0.1"))
    assert res.rc == 1 and res.result["result"] == "missing_file", (res.out, res.log)
    assert (app / "ChatCoach.exe").read_bytes().endswith(NEW_TAIL)
    assert (app / "_internal" / "VERSION").read_bytes() == b"0.0.2\n"


# --------------------------------------------------------------------------- ⑩ 앱이 포기한 교체 · 잠금 갱신 · 되돌리기 실패
@pytest.mark.parametrize("how", ["abandoned_marker", "lock_removed"])
def test_abandoned_by_app_changes_nothing(app, how):
    """앱이 시작 표식을 10초 못 받아 포기했다(잠금 삭제 + stage\abandoned). 늦게 뜬 스크립트는 종료 대기 뒤
    아무것도 바꾸지 않고, 결과도 쓰지 않고, 앱도 다시 띄우지 않고 끝난다."""
    new = make_new(app)
    if how == "abandoned_marker":
        stage = app / "_update" / STAGE_ID
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "abandoned").write_text("1", encoding="ascii")
    res = run_replacer(app, new, lock=(how != "lock_removed"))
    assert res.rc == 3, (res.out, res.log)
    assert res.result is None and "포기" in res.log
    _unchanged(app)
    assert not (app / "_backup").exists()


def _fake_copy(tmp: Path, seconds: int, rc: int) -> Path:
    """robocopy 대신: 불릴 때마다 같은 폴더의 calls.txt 에 한 줄 남기고, seconds 초 잠든 뒤 rc 로 끝난다(복사 안 함)."""
    p = tmp / "가짜 복사" / "fakecopy.cmd"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'@echo off\r\necho call>>"%~dp0calls.txt"\r\nping -n {seconds + 1} 127.0.0.1 >nul\r\n'
                 f"exit /b {rc}\r\n", encoding="ascii")
    return p


def test_lock_touched_while_mirror_runs(app, tmp_path):
    """큰 미러가 2분을 넘겨도 '멈춘 업데이트' 로 보이지 않게, 복사가 도는 동안에도 잠금을 갱신한다.
    가짜 복사 2번(백업·교체) × 4초, 갱신 주기 1초 → 그 사이 잠금 나이가 늘 3초 미만이어야 한다."""
    new = make_new(app)
    fake = _fake_copy(tmp_path, 4, 1)
    stage = app / "_update" / STAGE_ID
    stage.mkdir(parents=True, exist_ok=True)
    script = stage / "replace.ps1"
    updater.write_replacer(str(script))
    root = str(app / "data")
    lock = updater.write_updating_lock(root, os.getpid(), "0.0.1", "0.0.2")
    dead = subprocess.Popen(["cmd", "/c", "exit 0"], creationflags=NO_WINDOW)
    dead.wait()
    cmd = updater.replacer_command(str(script), str(app), str(new), STAGE_ID, "ChatCoach.exe", dead.pid,
                                   "0.0.1", "0.0.2", False) + ["-TouchSec", "1", "-CopyExe", str(fake)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    ages, stale = [], []
    calls = fake.parent / "calls.txt"
    t0 = time.monotonic()
    while proc.poll() is None and time.monotonic() - t0 < 90:
        try:
            if calls.exists():             # 복사가 시작된 뒤부터 (그 앞은 프로세스 목록 훑기 등 준비 단계)
                ages.append(time.time() - os.path.getmtime(lock))
                stale.append(updater.stale_update_lock(root, max_age_sec=3))
        except OSError:
            pass
        time.sleep(0.25)
    proc.wait(30)
    log = (app / "data" / "logs" / "update.log").read_text(encoding="utf-8-sig", errors="replace")
    assert updater.read_last_result(root)["result"] == "ok", log
    assert time.monotonic() - t0 >= 8                                  # 가짜 복사 2번이 실제로 돌았다
    assert ages and max(ages) < 3, (max(ages), log)
    assert not any(stale)


def test_rollback_failed_keeps_backup_ok_and_needs_repair(app, tmp_path):
    """되돌리기 미러가 두 번 다 rc ≥ 8: rollback_failed + --update-failed, backup_ok 는 남는다 → 앱이 [복구] 를 권할 수 있다."""
    backup = _updated_app(app)
    fake = _fake_copy(tmp_path, 0, 16)
    res = run_replacer(app, backup, rollback=True, versions=("0.0.2", "0.0.1"), extra=("-CopyExe", str(fake)))
    assert res.rc == 1 and res.result["result"] == "rollback_failed", (res.out, res.log)
    assert res.result["copy_rc"] == 16 and res.result["restore_rc"] == 16
    assert (app / "data" / "update" / "backup_ok.json").exists()
    assert updater.repair_needed(str(app / "data"))
    st = updater.rollback_status(str(app), str(app / "data"), "0.0.2", repair=True)
    assert st.available and st.target_version == "0.0.1"
