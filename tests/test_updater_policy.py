"""M4 업데이터 정책: 되돌리기 판정·제안, min_supported·mandatory, beta(prerelease) 채널, 대화상자 (docs/design/04 5-5 · 6-2 · 6-9 · 6-10).

실제 robocopy 는 쓰지 않는다 (그것은 test_rollback.py · test_updater_safety.py). 교체 스크립트 실행은 가짜 Popen 으로 막는다.
가짜 서버(tools/fake_release_server.py)는 127.0.0.1 포트 0 으로만 띄우고, 시험 키는 그 자리에서 만든다.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

import release_kit as rk
import fake_release_server as frs

from app import paths, updater
from app.appspec import SPEC
from app.updater import UpdateError

NOW = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- 공용
def _w(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def after_update(tmp_path, current: str = "0.1.1", previous: str = "0.1.0", backup_ok: str | None = "same",
                 result: dict | None = None) -> tuple[str, str]:
    """업데이트 직후 모양: <앱> = current, <앱>\\_backup = previous 전체 사본, backup_ok.json, last_result(ok)."""
    app = rk.fake_app(str(tmp_path / "테스트 문서" / "ChatCoach"), current)
    rk.fake_app(os.path.join(app, "_backup"), previous)
    import shutil
    shutil.rmtree(os.path.join(app, "_backup", "data"))          # 백업에는 보존 폴더가 없다
    root = os.path.join(app, "data")
    os.makedirs(os.path.join(root, "update"), exist_ok=True)
    if backup_ok is not None:
        v = previous if backup_ok == "same" else backup_ok
        _w(os.path.join(root, "update", "backup_ok.json"), json.dumps({"version": v, "at": "x"}).encode())
    res = {"result": "ok", "from": previous, "to": current} if result is None else result
    if res:
        _w(os.path.join(root, "update", "last_result.json"), json.dumps(res).encode())
    return app, root


# --------------------------------------------------------------------------- rollback_status
def test_rollback_status_available_after_update(tmp_path):
    app, root = after_update(tmp_path)
    st = updater.rollback_status(app, root, "0.1.1")
    assert st.available and st.target_version == "0.1.0" and st.current_version == "0.1.1"
    assert st.backup_dir == os.path.join(app, "_backup") and "0.1.1 → 0.1.0" in st.reason and not st.repair


@pytest.mark.parametrize("case, want", [
    ("source", "소스로 실행"),
    ("no_backup_dir", "사본이 없습니다"),
    ("no_backup_ok", "백업 완료 기록이 없습니다"),
    ("ok_points_elsewhere", "백업 완료 기록(0.0.9)과 다릅니다"),
    ("same_version", "지금 버전(0.1.1)과 같습니다"),
    ("missing_libmpv", "필수 파일이 없습니다"),
    ("in_progress", "업데이트가 진행 중"),
])
def test_rollback_status_unavailable(tmp_path, case, want):
    kw = {}
    if case == "no_backup_ok":
        kw["backup_ok"] = None
    if case == "ok_points_elsewhere":
        kw["backup_ok"] = "0.0.9"
    if case == "same_version":
        kw["previous"] = "0.1.1"
    app, root = after_update(tmp_path, **kw)
    if case == "no_backup_dir":
        import shutil
        shutil.rmtree(os.path.join(app, "_backup"))
    if case == "missing_libmpv":
        os.remove(os.path.join(app, "_backup", "_internal", "libmpv-2.dll"))
    if case == "in_progress":
        updater.write_updating_lock(root, 1, "0.1.1", "0.1.0")
    st = updater.rollback_status(app, root, "0.1.1", frozen=(case != "source"))
    assert not st.available and want in st.reason, st.reason


def test_rollback_status_repair_allows_same_version(tmp_path):
    app, root = after_update(tmp_path, previous="0.1.1")
    assert not updater.rollback_status(app, root, "0.1.1").available
    st = updater.rollback_status(app, root, "0.1.1", repair=True)
    assert st.available and st.repair and st.reason.startswith("복구")


def test_rollback_status_bom_version_file(tmp_path):
    app, root = after_update(tmp_path)
    _w(os.path.join(app, "_backup", "_internal", "VERSION"), b"\xef\xbb\xbf0.1.0\r\n")
    assert updater.read_dir_version(os.path.join(app, "_backup")) == "0.1.0"
    assert updater.rollback_status(app, root, "0.1.1").available


# --------------------------------------------------------------------------- 제안 · 연속 비정상 종료
def test_streak_from_crashguard_drives_suggestion(tmp_path):
    """연속 비정상 종료 횟수는 crashguard.note_start 가 센다. 업데이트 직후 2회째에 되돌리기를 권한다."""
    from app import crashguard
    app, root = after_update(tmp_path)
    seq = [crashguard.note_start(root, "0.1.1", ab) for ab in (False, True, True)]   # --updated 실행 · 충돌 · 충돌
    assert seq == [0, 1, 2]
    assert updater.rollback_suggestion(app, root, "0.1.1", abnormal_streak=seq[1]) is None
    assert updater.rollback_suggestion(app, root, "0.1.1", abnormal_streak=seq[2]) is not None


def test_suggestion_after_two_abnormal_exits(tmp_path):
    app, root = after_update(tmp_path)
    assert updater.rollback_suggestion(app, root, "0.1.1", abnormal_streak=1) is None
    s = updater.rollback_suggestion(app, root, "0.1.1", abnormal_streak=2)
    assert s is not None and "2번 연속 비정상 종료" in s.reason and s.status.target_version == "0.1.0"
    # backup_ok 가 없으면(불완전한 사본) 권하지 않는다
    os.remove(os.path.join(root, "update", "backup_ok.json"))
    assert updater.rollback_suggestion(app, root, "0.1.1", abnormal_streak=5) is None


def test_suggestion_stale_update_lock_even_same_version(tmp_path):
    """교체 중 전원 차단: 지금 버전 숫자가 옛 것이어도(파이썬 코드가 아직 안 바뀜) 사본으로 고치기를 권한다."""
    app, root = after_update(tmp_path, current="0.1.0", previous="0.1.0", result={})
    lock = updater.write_updating_lock(root, 1, "0.1.0", "0.1.1")
    old = time.time() - 600
    os.utime(lock, (old, old))
    s = updater.rollback_suggestion(app, root, "0.1.0")
    assert s is not None and "중간에 멈췄습니다" in s.reason and s.status.repair and s.status.available


def test_suggestion_restore_failed(tmp_path):
    app, root = after_update(tmp_path, result={"result": "restore_failed", "from": "0.1.0", "to": "0.1.1"})
    assert updater.rollback_suggestion(app, root, "0.1.1") is None                       # 플래그 없음
    s = updater.rollback_suggestion(app, root, "0.1.1", flags={"--update-failed"})
    assert s is not None and "섞였을 수 있습니다" in s.reason


@pytest.mark.parametrize("current", ["0.1.0", "0.1.1"])
def test_suggestion_and_menu_after_rollback_failed(tmp_path, current):
    """되돌리기 미러가 두 번 실패(rollback_failed): 스크립트는 backup_ok 를 남기고 --update-failed 로 다시 띄운다.
    섞인 폴더가 옛 버전 코드(0.1.0)로 떠도, 새 버전(0.1.1)으로 떠도 [복구]·[되돌리기] 제안과 메뉴가 있어야 한다."""
    app, root = after_update(tmp_path, current=current, previous="0.1.0",
                             result={"result": "rollback_failed", "from": "0.1.1", "to": "0.1.0"})
    assert updater.repair_needed(root)
    s = updater.rollback_suggestion(app, root, current, flags={"--update-failed"})
    assert s is not None and "되돌리기가 모두 실패" in s.reason and s.status.available and s.status.repair
    assert s.status.target_version == "0.1.0"
    assert s.status.reason.startswith("복구" if current == "0.1.0" else "되돌리기")
    # 메뉴(mainwindow.rollback_state)가 쓰는 판정: repair_needed 이면 repair=True
    menu = updater.rollback_status(app, root, current, repair=updater.repair_needed(root))
    assert menu.available
    # 사본은 지우지 않는다
    assert updater.cleanup_after_update(app, root, current) == 0 and os.path.isdir(os.path.join(app, "_backup"))


def test_repair_needed_cases(tmp_path):
    app, root = after_update(tmp_path)
    assert not updater.repair_needed(root)                                   # ok
    p = os.path.join(root, "update", "last_result.json")
    for result, want in (("restore_failed", True), ("rollback_failed", True), ("copy_failed", False),
                         ("rolled_back", False), ("blocked", False)):
        _w(p, json.dumps({"result": result, "from": "0.1.0", "to": "0.1.1"}).encode())
        assert updater.repair_needed(root) is want, result
    os.remove(p)
    lock = updater.write_updating_lock(root, 1, "0.1.0", "0.1.1")
    assert not updater.repair_needed(root)                                   # 진행 중(갱신됨)은 아니다
    old = time.time() - 600
    os.utime(lock, (old, old))
    assert updater.repair_needed(root)                                       # 멈춘 업데이트


def test_no_suggestion_after_copy_failed_restored(tmp_path):
    """교체 실패 → 복구 성공이면 이미 이전 버전이다. 사본 = 지금 버전이라 권할 것이 없다."""
    app, root = after_update(tmp_path, current="0.1.0", previous="0.1.0",
                             result={"result": "copy_failed", "from": "0.1.0", "to": "0.1.1"})
    assert updater.rollback_suggestion(app, root, "0.1.0", flags={"--update-failed"}, abnormal_streak=3) is None


# --------------------------------------------------------------------------- 정리 (_backup 삭제 시점)
def test_cleanup_after_rolled_back(tmp_path):
    app, root = after_update(tmp_path, current="0.1.0", previous="0.1.0",
                             result={"result": "rolled_back", "from": "0.1.1", "to": "0.1.0"})
    assert updater.cleanup_after_update(app, root, "0.1.0") > 0
    assert not os.path.exists(os.path.join(app, "_backup"))
    assert not os.path.exists(os.path.join(root, "update", "backup_ok.json"))


def test_cleanup_after_rolled_back_keeps_foreign_copy(tmp_path):
    # 되돌린 뒤인데 사본 버전이 지금과 다르면(설명할 수 없는 상태) 지우지 않는다
    app, root = after_update(tmp_path, current="0.1.0", previous="0.0.9",
                             result={"result": "rolled_back", "from": "0.1.1", "to": "0.1.0"})
    assert updater.cleanup_after_update(app, root, "0.1.0") == 0 and os.path.isdir(os.path.join(app, "_backup"))


# --------------------------------------------------------------------------- 되돌리기 실행 (가짜 Popen · 가짜 관문)
class _Popen:
    calls: list = []
    killed: list = []
    mode = "silent"

    def __init__(self, cmd, **kw):
        _Popen.calls.append(cmd)
        self._done = None
        if _Popen.mode == "start":
            app, sid = cmd[cmd.index("-App") + 1], cmd[cmd.index("-Id") + 1]
            with open(os.path.join(app, "_update", sid, "started"), "w", encoding="ascii") as f:
                f.write("1")

    def poll(self):
        return self._done

    def kill(self):
        _Popen.killed.append(self)
        self._done = 1

    def wait(self, timeout=None):
        return self._done


@pytest.fixture()
def fake_launch(monkeypatch):
    _Popen.calls = []
    _Popen.killed = []
    _Popen.mode = "silent"
    monkeypatch.setattr(updater.subprocess, "Popen", _Popen)
    monkeypatch.setattr(updater, "execution_policy_report", lambda: "정책(테스트)")
    for v in paths.ONEDRIVE_VARS:
        monkeypatch.delenv(v, raising=False)
    gate_calls = []

    def gate(app_dir, version, channel, stage_dir, spec=SPEC):
        gate_calls.append((app_dir, version, channel))
        return {"ok": True, "version": version}
    monkeypatch.setattr(updater, "run_selftest_gate", gate)
    return _Popen, gate_calls


def test_rollback_launches_same_script_with_rollback_switch(tmp_path, fake_launch):
    popen, gate_calls = fake_launch
    popen.mode = "start"
    app, root = after_update(tmp_path)
    state_path = os.path.join(root, "update", "state.json")
    inst = rk.installed(app, "0.1.1")
    backups = []
    staged = updater.rollback_now(replace(SPEC, replacer_start_wait_sec=1), inst, root, state_path, pid=4242,
                                  data_backup=lambda a, b: backups.append((a, b)))
    # 관문은 _backup 의 exe 로, 기대 버전 = 사본 버전
    assert gate_calls == [(os.path.join(app, "_backup"), "0.1.0", "test")]
    assert backups == [("0.1.1", "0.1.0")]
    cmd, = popen.calls
    assert cmd[cmd.index("-New") + 1] == os.path.join(app, "_backup")
    assert cmd[cmd.index("-AppPid") + 1] == "4242"
    assert cmd[cmd.index("-FromVer") + 1] == "0.1.1" and cmd[cmd.index("-ToVer") + 1] == "0.1.0"
    assert cmd[-2:] == ["-NoBackup", "-Rollback"] and "-Lite" not in cmd
    assert os.path.isfile(os.path.join(staged.stage_dir, "replace.ps1"))
    # 되돌린 버전은 건너뛰기에 들어간다 (04 6-10)
    assert updater.UpdateState.load(state_path).skip_version == "0.1.1"
    # 잠금은 스크립트가 지운다 / 지난 결과는 비켜 둔다 (멈춘 업데이트 감지가 맞게)
    assert updater.update_in_progress(root)
    assert updater.read_last_result(root) is None
    assert os.path.isfile(os.path.join(root, "update", "last_result.prev.json"))


def test_rollback_blocked_restores_skip_and_last_result(tmp_path, fake_launch):
    app, root = after_update(tmp_path)
    state_path = os.path.join(root, "update", "state.json")
    updater.UpdateState(skip_version="0.0.7").save(state_path)
    with pytest.raises(UpdateError) as ei:
        updater.rollback_now(replace(SPEC, replacer_start_wait_sec=0.3), rk.installed(app, "0.1.1"), root, state_path)
    assert ei.value.kind == "blocked"
    # 늦게 뜬 스크립트가 '안 됐다' 고 알린 되돌리기를 이어 가지 않게: 포기 표식 + 살아 있던 스크립트를 끝냄
    popen, _ = fake_launch
    cmd, = popen.calls
    assert len(popen.killed) == 1
    assert os.path.isfile(os.path.join(app, "_update", cmd[cmd.index("-Id") + 1], "abandoned"))
    assert updater.UpdateState.load(state_path).skip_version == "0.0.7"
    assert updater.read_last_result(root)["result"] == "ok"           # 비켜 둔 결과를 되돌렸다
    assert not os.path.exists(os.path.join(root, "update", "updating.lock"))
    assert updater.rollback_status(app, root, "0.1.1").available       # 다시 시도할 수 있다


def test_rollback_refused_when_unavailable(tmp_path, fake_launch):
    popen, gate_calls = fake_launch
    app, root = after_update(tmp_path, backup_ok=None)
    with pytest.raises(UpdateError) as ei:
        updater.rollback_now(SPEC, rk.installed(app, "0.1.1"), root)
    assert ei.value.kind == "precheck" and "백업 완료 기록" in ei.value.message
    assert popen.calls == [] and gate_calls == []
    assert not os.path.isdir(os.path.join(app, "_update")) or os.listdir(os.path.join(app, "_update")) == []


def test_rollback_data_backup_failure_stops_before_launch(tmp_path, fake_launch):
    popen, _ = fake_launch
    app, root = after_update(tmp_path)

    def broken(a, b):
        raise OSError("디스크 가득 (시험)")
    with pytest.raises(UpdateError) as ei:
        updater.rollback_now(SPEC, rk.installed(app, "0.1.1"), root, data_backup=broken)
    assert ei.value.kind == "precheck" and "data 백업 실패" in ei.value.message
    assert popen.calls == [] and not os.path.exists(os.path.join(root, "update", "updating.lock"))
    assert os.listdir(os.path.join(app, "_update")) == []                # 준비 폴더도 남기지 않는다


def test_rollback_repair_does_not_mark_skip(tmp_path, fake_launch):
    popen, _ = fake_launch
    popen.mode = "start"
    app, root = after_update(tmp_path, current="0.1.0", previous="0.1.0", result={})
    sp = os.path.join(root, "update", "state.json")
    updater.rollback_now(replace(SPEC, replacer_start_wait_sec=1), rk.installed(app, "0.1.0"), root, sp, repair=True)
    assert updater.UpdateState.load(sp).skip_version is None


def test_rollback_rechecks_after_gate(tmp_path, fake_launch, monkeypatch):
    """관문이 도는 동안 시작 정리가 사본을 지우기 시작하면(backup_ok 를 먼저 지움) 되돌리기를 시작하지 않는다."""
    popen, _ = fake_launch
    app, root = after_update(tmp_path)

    def gate(app_dir, version, channel, stage_dir, spec=SPEC):
        os.remove(os.path.join(root, "update", "backup_ok.json"))
        return {"ok": True, "version": version}
    monkeypatch.setattr(updater, "run_selftest_gate", gate)
    with pytest.raises(UpdateError) as ei:
        updater.rollback_now(SPEC, rk.installed(app, "0.1.1"), root)
    assert ei.value.kind == "precheck" and "바뀌었습니다" in ei.value.message and popen.calls == []


def test_apply_sets_aside_last_result_and_restores_when_blocked(tmp_path, fake_launch):
    app, root = after_update(tmp_path, current="0.1.0", previous="0.0.9")
    staged_dir = os.path.join(app, "_update", "upd-x")
    new_dir = os.path.join(staged_dir, "new", SPEC.zip_top)
    os.makedirs(new_dir)
    name = SPEC.asset_name("0.1.1", "full")
    asset = updater.Asset("full", name, 10, "0" * 64, SPEC.asset_url("0.1.1", name), 10, 1)
    lite = updater.Asset("lite", SPEC.asset_name("0.1.1", "lite"), 10, "0" * 64,
                         SPEC.asset_url("0.1.1", SPEC.asset_name("0.1.1", "lite")), 10, 1)
    m = updater.Manifest(1, SPEC.name, "0.1.1", NOW, "0.1.0", False, 1, ("0.1",), "", {}, (asset, lite))
    staged = updater.StagedUpdate(updater.Release(m, "TEST", "0" * 16, "full"), staged_dir, "upd-x", new_dir, False, {})
    with pytest.raises(UpdateError):
        updater.RobocopyApplier(replace(SPEC, replacer_start_wait_sec=0.3), rk.installed(app, "0.1.0"), root).apply(staged)
    assert updater.read_last_result(root)["result"] == "ok"


# --------------------------------------------------------------------------- 실제 관문 (가짜 exe = python 스크립트)
FAKE_SELFTEST = r'''
import json, os, sys
exe, out, expect = sys.argv[1], sys.argv[2], sys.argv[3]
d = os.path.dirname(exe)
with open(os.path.join(d, "_internal", "VERSION"), encoding="utf-8") as f:
    v = f.read().strip()
fail = os.environ.get("CC_FAKE_SELFTEST_FAIL") == "1"
os.makedirs(os.path.join(d, "_update"), exist_ok=True)      # 옛 자체 점검처럼 설치 폴더에 빈 _update\ 를 남긴다
ok = v == expect and not fail
with open(out, "w", encoding="utf-8") as f:
    json.dump({"version": v, "ok": ok, "checks": {"version": {"ok": v == expect, "detail": v},
                                                  "fake": {"ok": not fail, "detail": "시험"}}}, f)
sys.exit(0 if ok else 1)
'''


@pytest.fixture()
def fake_selftest(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("fake_selftest_script")
    script = d / "fake_selftest.py"
    script.write_text(FAKE_SELFTEST, encoding="utf-8")
    monkeypatch.setattr(updater, "selftest_command",
                        lambda exe, out, version, channel, data_dir: [sys.executable, str(script), exe, out, version])
    monkeypatch.delenv("CC_FAKE_SELFTEST_FAIL", raising=False)
    return monkeypatch


def test_real_gate_on_backup_passes_and_leaves_no_trace(tmp_path, fake_selftest):
    app, root = after_update(tmp_path)
    st = updater.stage_rollback(SPEC, rk.installed(app, "0.1.1"), root)
    assert st.selftest["version"] == "0.1.0" and st.target_version == "0.1.0"
    assert not os.path.exists(os.path.join(app, "_backup", "_update"))      # 관문이 만든 빈 폴더를 치웠다
    assert os.path.isfile(os.path.join(st.stage_dir, updater.ACTIVE_MARKER))


def test_real_gate_failure_refuses_rollback(tmp_path, fake_selftest):
    fake_selftest.setenv("CC_FAKE_SELFTEST_FAIL", "1")
    app, root = after_update(tmp_path)
    sp = os.path.join(root, "update", "state.json")
    with pytest.raises(UpdateError) as ei:
        updater.rollback_now(SPEC, rk.installed(app, "0.1.1"), root, sp)
    assert ei.value.kind == "selftest" and "fake" in ei.value.message
    kept = [n for n in os.listdir(os.path.join(app, "_update")) if n.startswith(updater.STAGE_PREFIX)]
    assert len(kept) == 1 and os.path.isfile(os.path.join(app, "_update", kept[0], updater.FAILED_MARKER))
    assert not os.path.exists(sp) or updater.UpdateState.load(sp).skip_version is None
    assert not os.path.exists(os.path.join(root, "update", "updating.lock"))
    assert updater.read_dir_version(app) == "0.1.1"                          # 아무것도 바꾸지 않았다
    assert updater.rollback_status(app, root, "0.1.1").available             # 사본은 그대로


def test_gate_rejects_report_with_other_version(tmp_path, fake_selftest):
    app, root = after_update(tmp_path)
    # 사본 VERSION 과 backup_ok 는 0.1.0 인데, 관문에 다른 기대 버전을 주면 보고 버전이 달라 실패
    with pytest.raises(UpdateError) as ei:
        updater.run_selftest_gate(os.path.join(app, "_backup"), "0.1.9", "test", str(tmp_path))
    assert ei.value.kind == "selftest"


def test_gate_ignores_stale_report(tmp_path, monkeypatch):
    """지난 실행의 selftest.json(ok) 이 남아 있어도 이번 실행이 못 쓰면 통과가 아니다."""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "selftest.json").write_text('{"ok": true, "version": "0.1.0", "checks": {}}', encoding="utf-8")
    monkeypatch.setattr(updater, "selftest_command",
                        lambda exe, out, version, channel, data_dir: [sys.executable, "-c", "pass"])
    with pytest.raises(UpdateError) as ei:
        updater.run_selftest_gate(str(tmp_path), "0.1.0", "test", str(stage))
    assert ei.value.kind == "selftest"


# --------------------------------------------------------------------------- min_supported · mandatory (질문 46 기본안)
@pytest.fixture(scope="module")
def rel(tmp_path_factory):
    return rk.build_release(str(tmp_path_factory.mktemp("prel")), version="0.1.1")


def _fetch_of(data, sig):
    return lambda spec, inst, base: (data, sig)


def test_support_status_matrix():
    S = updater.UpdateState
    assert not updater.support_status(S(), "0.1.0").required_pending                  # 확인한 적 없음: 강제 없음
    st = updater.support_status(S(last_min_supported="0.1.1"), "0.1.0")
    assert st.needs_update and not st.can_save_labels and "지원이 끝났습니다" in st.message()
    assert updater.support_status(S(last_min_supported="0.1.0"), "0.1.0").can_save_labels
    st = updater.support_status(S(mandatory_version="0.1.1"), "0.1.0")
    assert st.mandatory_version == "0.1.1" and st.can_save_labels and st.required_pending
    assert updater.support_status(S(mandatory_version="0.1.1"), "0.1.1").mandatory_version is None   # 이미 받음
    assert not updater.support_status(S(last_min_supported="garbage"), "0.1.0").needs_update
    assert not updater.support_status(S(last_min_supported="9.9.9"), "dev").needs_update


def test_run_check_records_policy_and_keeps_it_offline(tmp_path, rel):
    sp = str(tmp_path / "state.json")
    inst = rk.installed(str(tmp_path / "app"), "0.1.0")
    data, sig = rel.variant(lambda o: o.update(min_supported="0.1.1", mandatory=True))
    r = updater.run_check(SPEC, inst, sp, now=NOW, force=True, pubkeys=rel.keys, fetch=_fetch_of(data, sig))
    assert r.state == "newer" and r.needs_update and r.release.manifest.mandatory
    st = updater.UpdateState.load(sp)
    assert st.last_min_supported == "0.1.1" and st.mandatory_version == "0.1.1"
    assert updater.label_save_block_reason(sp, "0.1.0") is not None
    assert updater.label_save_block_reason(sp, "0.1.1") is None

    def offline(spec, inst, base):
        raise UpdateError("network", "오프라인 (시험)")
    r = updater.run_check(SPEC, inst, sp, now=NOW + timedelta(hours=2), force=True, pubkeys=rel.keys, fetch=offline)
    assert r.state == "failed"
    assert updater.label_save_block_reason(sp, "0.1.0") is not None                 # 확인 실패는 막음을 풀지 않는다
    # 다음 매니페스트가 min_supported 를 내리면 풀린다
    data, sig = rel.variant(lambda o: o.update(min_supported="0.1.0", mandatory=False))
    updater.run_check(SPEC, inst, sp, now=NOW + timedelta(hours=3), force=True, pubkeys=rel.keys, fetch=_fetch_of(data, sig))
    assert updater.label_save_block_reason(sp, "0.1.0") is None
    assert not updater.load_support_status(sp, "0.1.0").required_pending


def test_latest_result_clears_policy(tmp_path, rel):
    sp = str(tmp_path / "state.json")
    updater.UpdateState(last_min_supported="0.1.1", mandatory_version="0.1.1").save(sp)
    inst = rk.installed(str(tmp_path / "app"), "0.1.1")
    r = updater.run_check(SPEC, inst, sp, now=NOW, force=True, pubkeys=rel.keys, fetch=_fetch_of(rel.data, rel.sig))
    assert r.state == "latest"
    st = updater.UpdateState.load(sp)
    assert st.last_min_supported is None and st.mandatory_version is None


def test_mandatory_checks_on_every_start(tmp_path, rel):
    """mandatory 면 주기(24시간)와 상관없이 시작할 때마다 확인·알림. 주기 타이머(on_start=False)는 주기를 지킨다."""
    sp = str(tmp_path / "state.json")
    inst = rk.installed(str(tmp_path / "app"), "0.1.0")
    data, sig = rel.variant(lambda o: o.update(mandatory=True))
    calls = []

    def fetch(spec, i, base):
        calls.append(1)
        return data, sig
    r = updater.run_check(SPEC, inst, sp, now=NOW, pubkeys=rel.keys, fetch=fetch)
    assert r.state == "newer" and len(calls) == 1
    later = NOW + timedelta(minutes=5)
    assert updater.run_check(SPEC, inst, sp, now=later, pubkeys=rel.keys, fetch=fetch) is None
    r = updater.run_check(SPEC, inst, sp, now=later, pubkeys=rel.keys, fetch=fetch, on_start=True)
    assert r is not None and len(calls) == 2
    # 건너뛰기를 눌러도 mandatory 면 건너뛰지 않는다
    st = updater.UpdateState.load(sp)
    st.skip_version = "0.1.1"
    st.save(sp)
    r = updater.run_check(SPEC, inst, sp, now=later, pubkeys=rel.keys, fetch=fetch, on_start=True)
    assert r.state == "newer" and not r.skipped
    # mandatory 가 아니면 시작해도 주기를 지킨다
    updater.UpdateState(last_ok_at=later.isoformat()).save(sp)
    assert updater.run_check(SPEC, inst, sp, now=later, pubkeys=rel.keys, fetch=fetch, on_start=True) is None


def test_state_roundtrip_with_policy_fields(tmp_path):
    p = str(tmp_path / "s.json")
    st = updater.UpdateState(skip_version="0.1.1")
    st.record_ok(NOW, "0.1.2", "0.1.1", "0.1.2")
    st.record_fail(NOW, "x")
    st.save(p)
    back = updater.UpdateState.load(p)
    assert back == st and back.last_min_supported == "0.1.1" and back.mandatory_version == "0.1.2"


# --------------------------------------------------------------------------- beta 채널 (질문 45 기본안)
def test_normalize_channel():
    assert updater.normalize_channel("beta") == "beta" and updater.normalize_channel(" Beta ") == "beta"
    for v in (None, "", "stable", "betaa", 1, "prerelease"):
        assert updater.normalize_channel(v) == "stable"


def test_pick_beta_release():
    def r(tag, draft=False, prerelease=False, assets=("latest.json", "latest.json.minisig")):
        return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
                "assets": [{"name": n, "browser_download_url": f"https://x/{tag}/{n}"} for n in assets]}
    rels = [r("v0.1.0"), r("v0.1.2", prerelease=True), r("v0.9.0", draft=True), r("v0.1.3", assets=("a.zip",)),
            r("vX"), r("0.2.0"), "junk", {"tag_name": "v0.5.0"}]
    got = updater.pick_beta_release(rels)
    assert got["latest.json"] == "https://x/v0.1.2/latest.json"
    assert updater.pick_beta_release([r("v1.0.0", draft=True)]) is None
    assert updater.pick_beta_release({"not": "list"}) is None
    # draft 키가 없으면 초안일 수 있다고 보고 건너뛴다
    assert updater.pick_beta_release([{"tag_name": "v0.3.0", "assets": r("v0.3.0")["assets"]}]) is None


@pytest.fixture()
def beta_server(rel):
    class H(frs.Handler):
        requests: list[str] = []
        list_ok = True

        def log_message(self, fmt, *args):  # noqa: N802
            H.requests.append(self.path)

        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0] == "/api/releases":
                if not H.list_ok:
                    return self._send(500, b"down")
                base = f"http://127.0.0.1:{self.server.server_address[1]}/{self.repo}/releases/download/v{rel.version}/"
                obj = [
                    {"tag_name": "v0.0.9", "draft": False, "prerelease": False,
                     "assets": [{"name": "latest.json", "browser_download_url": base + "old/latest.json"}]},
                    {"tag_name": f"v{rel.version}", "draft": False, "prerelease": True,
                     "assets": [{"name": n, "browser_download_url": base + n} for n in ("latest.json", "latest.json.minisig")]},
                    {"tag_name": "v9.9.9", "draft": True, "prerelease": False,
                     "assets": [{"name": "latest.json", "browser_download_url": base + "draft/latest.json"}]},
                ]
                return self._send(200, json.dumps(obj).encode(), "application/json")
            return super().do_GET()

    H.state = frs._State(rel.out, "primary_404", None)     # 1차 고정 링크(latest)는 막아 둔다: beta 는 목록을 본다
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield H, f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


def test_beta_channel_sees_prerelease(tmp_path, rel, beta_server):
    H, url = beta_server
    inst = rk.installed(str(tmp_path / "app"), "0.1.0")
    sp = str(tmp_path / "state.json")
    r = updater.run_check(SPEC, inst, sp, now=NOW, force=True, base_url=url, pubkeys=rel.keys, channel="beta")
    assert r.state == "newer" and r.release.version == rel.version, r.reason
    assert any(p.startswith("/api/releases?per_page=10") for p in H.requests)
    assert not any("draft" in p or "/old/" in p for p in H.requests)
    assert not any("/releases/latest/download/" in p for p in H.requests)


def test_beta_channel_falls_back_to_stable_paths(tmp_path, rel, beta_server):
    H, url = beta_server
    H.list_ok = False
    try:
        inst = rk.installed(str(tmp_path / "app"), "0.1.0")
        r = updater.run_check(SPEC, inst, str(tmp_path / "s.json"), now=NOW, force=True, base_url=url,
                              pubkeys=rel.keys, channel="beta")
        # 1차는 primary_404, 2차 /api/releases/latest 로 받는다
        assert r.state == "newer", r.reason
        assert any(p == "/api/releases/latest" for p in H.requests)
    finally:
        H.list_ok = True


def test_stable_channel_never_lists_releases(tmp_path, rel, beta_server):
    H, url = beta_server
    inst = rk.installed(str(tmp_path / "app"), "0.1.0")
    updater.run_check(SPEC, inst, str(tmp_path / "s.json"), now=NOW, force=True, base_url=url, pubkeys=rel.keys)
    assert not any(p.startswith("/api/releases?") for p in H.requests)


# --------------------------------------------------------------------------- 대화상자 (offscreen)
@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


def _texts(w):
    from PySide6.QtWidgets import QLabel, QPushButton
    return [x.text() for x in w.findChildren(QLabel)] + [x.text() for x in w.findChildren(QPushButton)]


def test_update_dialog_mandatory_and_needs_update(qapp, rel):
    from PySide6.QtWidgets import QPushButton
    from app.ui.update_dialog import UpdateDialog
    m = updater.parse_manifest(rel.variant(lambda o: o.update(mandatory=True))[0])
    d = UpdateDialog(updater.Release(m, "TEST", "0" * 16, "full"), needs_update=True)
    t = _texts(d)
    assert any("필수 업데이트" in x for x in t) and not any("건너뛰기" in x for x in t)
    assert d.findChild(QPushButton, "manualBtn") is not None
    m2 = updater.parse_manifest(rel.data)
    d2 = UpdateDialog(updater.Release(m2, "TEST", "0" * 16, "lite"))
    t2 = _texts(d2)
    assert any("건너뛰기" in x for x in t2) and d2.findChild(QPushButton, "manualBtn") is None
    assert not any("필수 업데이트" in x for x in t2)


def test_rollback_menu_text_and_dialog(qapp, tmp_path):
    from PySide6.QtWidgets import QPushButton
    from app.ui.update_dialog import RollbackDialog, rollback_menu_text
    app, root = after_update(tmp_path)
    st = updater.rollback_status(app, root, "0.1.1")
    text, enabled, tip = rollback_menu_text(st)
    assert enabled and "0.1.0" in text and "0.1.1 → 0.1.0" in tip
    d = RollbackDialog(st, reason="업데이트한 뒤 2번 연속 비정상 종료했습니다 (시험)")
    t = _texts(d)
    assert any("비정상 종료" in x for x in t) and "되돌리기" in t and "나중에" in t
    assert any("다시 권하지 않습니다" in x for x in t)
    assert d.findChild(QPushButton, "rollbackBtn").isEnabled()
    off = updater.rollback_status(app, root, "0.1.0")          # 같은 버전 → 꺼짐
    text, enabled, tip = rollback_menu_text(off)
    assert not enabled and "같습니다" in tip
    d3 = RollbackDialog(off)
    assert not d3.findChild(QPushButton, "rollbackBtn").isEnabled()
    rep = updater.rollback_status(app, root, "0.1.0", repair=True)
    rd = RollbackDialog(rep, data_backup=False)
    t = _texts(rd)
    assert "복구" in t and not any("data 백업" in x for x in t)


def test_support_banner(qapp):
    from app.ui.update_dialog import SupportBanner, manual_update_url, _TRUSTED_URL
    b = SupportBanner()
    assert b.isHidden()
    b.set_status(updater.support_status(updater.UpdateState(last_min_supported="0.2.0"), "0.1.0"))
    assert not b.isHidden() and not b.b_manual.isHidden() and "지원이 끝났습니다" in b.label.text()
    b.set_status(updater.support_status(updater.UpdateState(mandatory_version="0.2.0"), "0.1.0"))
    assert not b.isHidden() and b.b_manual.isHidden()
    b.set_status(updater.support_status(updater.UpdateState(), "0.1.0"))
    assert b.isHidden()
    assert _TRUSTED_URL.match(manual_update_url())
