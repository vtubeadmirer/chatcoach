"""업데이터의 순수 함수와 교체 스크립트 텍스트 (ClipSlicer tests/test_updater.py 를 StreamLabeler 업데이터에 맞게 고쳐 씀).

- parse_deps · choose_asset(경량/전체 행렬) · decode_output(UTF-8 · cp949 · 대체 문자).
- render_replacer: /XD 는 appspec 의 세 보존 폴더 전체 경로만, Stop-Process 없음, started 표식 먼저, /MIR · /R:2,
  backup_ok.json · last_result.json, 경량 분기(_internal\\ffmpeg · libmpv-2.dll), -NoBackup 분기.
  write_replacer 는 BOM 으로 시작. PowerShell 파서로 문법 오류 0건 (Windows 에서만).
- replacer_command 인자 순서, updating.lock(진행 중 · 멈춘 업데이트), read_last_result, cleanup_after_update, UpdateState.
- RobocopyApplier.preflight(소스 실행 · UNC · OneDrive 경고 · 디스크) 와 apply(시작 표식 대기, 없으면 blocked + 잠금 삭제).
- main.parse_args.
실제 robocopy·PowerShell 로 교체를 돌리는 테스트는 tests/test_updater_safety.py (local_only).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
import types
from dataclasses import replace
from datetime import datetime, timezone

import pytest

import release_kit as rk

from app import paths, updater
from app.appspec import SPEC
from app.updater import UpdateError

BOM = b"\xef\xbb\xbf"


def _asset(kind: str, version: str = "0.0.2", unpacked: int = 10_000) -> updater.Asset:
    name = SPEC.asset_name(version, kind)
    return updater.Asset(kind, name, 1234, "0" * 64, SPEC.asset_url(version, name), unpacked, 5)


def _manifest(deps=None, version: str = "0.0.2", unpacked: int = 10_000) -> updater.Manifest:
    return updater.Manifest(1, SPEC.name, version, datetime.now(timezone.utc), "0.0.1", False, 1, ("0.1",), "노트",
                            rk.DEPS if deps is None else deps, (_asset("full", version, unpacked), _asset("lite", version, unpacked)))


def _staged(app_dir: str, chosen: str = "full", stage_id: str = "upd-test") -> updater.StagedUpdate:
    release = updater.Release(_manifest(), "TEST", "0123456789ABCDEF", chosen)
    stage_dir = os.path.join(app_dir, "_update", stage_id)
    new_dir = os.path.join(stage_dir, "new", SPEC.zip_top)
    os.makedirs(new_dir, exist_ok=True)
    return updater.StagedUpdate(release, stage_dir, stage_id, new_dir, chosen == "lite", {})


# --------------------------------------------------------------------------- deps · 자산 선택 · 출력 디코딩
def test_parse_deps():
    assert updater.parse_deps("ffmpeg=a\r\n# 주석\n\nlibmpv = b \nbroken\n=novalue\nx=1=2\n") == \
        {"ffmpeg": "a", "libmpv": "b", "x": "1=2"}
    assert updater.parse_deps("") == {} and updater.parse_deps(None) == {}
    assert updater.parse_deps(rk.DEPS_TEXT) == rk.LOCAL_DEPS


def test_choose_asset_matrix(tmp_path):
    app = str(tmp_path / "app")
    m = _manifest()
    ok = rk.installed(app)
    assert updater.choose_asset(m, ok) == "lite"                                              # 모두 일치
    assert updater.choose_asset(m, rk.installed(app, has_heavy=False)) == "full"              # ffmpeg·libmpv 없음
    assert updater.choose_asset(m, rk.installed(app, deps={})) == "full"                      # deps.txt 없음 (옛 빌드)
    assert updater.choose_asset(m, rk.installed(app, deps=dict(rk.LOCAL_DEPS, ffmpeg="new"))) == "full"
    assert updater.choose_asset(m, rk.installed(app, deps=dict(rk.LOCAL_DEPS, libmpv="new"))) == "full"
    assert updater.choose_asset(m, rk.installed(app, deps=dict(rk.LOCAL_DEPS, ffmpeg_sha256="f" * 64))) == "full"
    assert updater.choose_asset(_manifest(deps={}), ok) == "full"                              # 매니페스트 deps 비어 있음
    extra = dict(rk.DEPS, vapoursynth={"tag": "r1", "sha256": "2" * 64})
    assert updater.choose_asset(_manifest(deps=extra), ok) == "full"                          # 로컬에 없는 의존성
    assert updater.choose_asset(m, rk.installed(app, deps=dict(rk.LOCAL_DEPS, other="z"))) == "lite"   # 로컬에만 있는 줄은 무시


def test_decode_output():
    assert updater.decode_output(None) == "" and updater.decode_output(b"") == ""
    s = "12|C:\\문서\\a.exe"
    assert updater.decode_output(s.encode("utf-8")) == s
    assert updater.decode_output(s.encode("cp949")) == s
    out = updater.decode_output(b"\xff\xfe\xfa")
    assert out != "" and "\ufffd" in out


# --------------------------------------------------------------------------- 교체 스크립트 텍스트
@pytest.fixture(scope="module")
def script_text():
    return updater.render_replacer()


def _line(text: str, start: str) -> str:
    found = [ln for ln in text.splitlines() if ln.strip().startswith(start)]
    assert len(found) == 1, (start, found)
    return found[0].strip()


def test_replacer_xd_is_exactly_the_three_keep_dirs_as_full_paths(script_text):
    xd = _line(script_text, "$xd = @(")
    assert xd == '$xd = @((Join-Path $App "data"), (Join-Path $App "_backup"), (Join-Path $App "_update"))'
    assert xd.count("Join-Path") == len(SPEC.keep_dirs) == 3
    # 이름만 주는 /XD 나 다른 보존 이름(ClipSlicer 시절의 사용자 파일)이 없다
    for name in ("settings.ini", "web_profile", "youtube_token", "bookmarks", "samples"):
        assert name not in script_text, name
    assert '"/XD") + $xd' in script_text
    # 경로 변수도 appspec 값으로 조립된다
    assert '$dataDir = Join-Path $App "data"' in script_text
    assert '$backup = Join-Path $App "_backup"' in script_text
    assert '$stage = Join-Path (Join-Path $App "_update") $Id' in script_text


def test_replacer_follows_spec(script_text):
    spec = replace(SPEC, keep_dirs=("data", "_backup", "_update", "extra"))
    xd = _line(updater.render_replacer(spec), "$xd = @(")
    assert xd.count("Join-Path") == 4 and '(Join-Path $App "extra")' in xd


def test_replacer_never_kills_processes(script_text):
    for bad in ("Stop-Process", "taskkill", ".Kill(", "Kill()"):
        assert bad not in script_text, bad
    assert "Wait-Process -Id $AppPid" in script_text
    assert "죽이지 않는다" in script_text


def test_replacer_writes_started_marker_first(script_text):
    started = script_text.index('(Join-Path $stage "started")')
    for later in ("Wait-Process", "Get-Process", "robocopy", "Mirror $App $backup", "Mirror $New $App"):
        assert started < script_text.index(later), later
    # 시작 표식 앞에는 폴더 준비와 함수 정의뿐 (프로세스 대기·복사 없음)
    head = script_text[:started]
    assert "Start-Sleep" not in head and "& robocopy" not in head.split("function Mirror")[0]


def test_replacer_robocopy_options_and_results(script_text):
    assert '"/MIR"' in script_text and '"/R:2"' in script_text and '"/W:1"' in script_text
    assert '"backup_ok.json"' in script_text and '"last_result.json"' in script_text
    assert "if ($backupRc -ge 8)" in script_text and "if ($copyRc -ge 8)" in script_text
    assert "if ($restoreRc -ge 8)" in script_text
    for result in ('"blocked"', '"missing_file"', '"backup_failed"', '"copy_failed"', '"restore_failed"', '"ok"'):
        assert f"Finish {result}" in script_text, result
    for flag in ('"--updated"', '"--update-failed"', '"--update-blocked"'):
        assert flag in script_text
    assert 'logs\\update.log' in script_text
    # 결과 기록은 잠금 삭제·재실행보다 먼저 (Finish 함수 안의 순서)
    finish = script_text[script_text.index("function Finish"):script_text.index("# 1. 시작 표식")]
    assert finish.index("last_result.json") < finish.index("Remove-Item -LiteralPath $lock") < finish.index("Start-Process")


def test_replacer_lite_branch(script_text):
    # 경량 제외 목록은 교체 단계에서만 쓴다. 백업·복구는 전체 사본이어야 한다 (04 6-8 5단계)
    lite_xd = _line(script_text, "$liteXd = @(")
    assert '(Join-Path $App "_internal\\ffmpeg")' in lite_xd
    assert _line(script_text, "$liteXf = @(") == '$liteXf = @("libmpv-2.dll")'
    assert _line(script_text, "$backupRc = Mirror") == "$backupRc = Mirror $App $backup $false"
    assert _line(script_text, "$copyRc = Mirror") == "$copyRc = Mirror $New $App $Lite"
    assert _line(script_text, "$restoreRc = Mirror") == "$restoreRc = Mirror $backup $App $false"
    req = _line(script_text, "$required = @(")
    assert req == '$required = @("StreamLabeler.exe", "_internal\\python312.dll", "_internal\\VERSION")'
    assert _line(script_text, "if (-not $Lite) { $required +=") == \
        'if (-not $Lite) { $required += @("_internal\\libmpv-2.dll") }'


def test_replacer_no_backup_branch(script_text):
    params = script_text[script_text.index("param("):script_text.index(")\n$ErrorActionPreference")]
    for p in ("[int]$AppPid", "[string]$App", "[string]$New", "[string]$Id", "[string]$Exe", "[string]$FromVer",
              "[string]$ToVer", "[switch]$Lite", "[switch]$NoBackup", "[int]$WaitSec = 120"):
        assert p in params, p
    assert "if (-not $NoBackup) {" in script_text
    nb = script_text[script_text.index("if (-not $NoBackup) {"):script_text.index("# 7. 교체")]
    assert "Mirror $App $backup" in nb and "backup_ok.json" in nb


def test_write_replacer_bom(tmp_path):
    p = tmp_path / "replace.ps1"
    updater.write_replacer(str(p))
    data = p.read_bytes()
    assert data.startswith(BOM) and not data.startswith(BOM + BOM)
    assert data[3:].decode("utf-8") == updater.render_replacer()
    assert "교체 시작".encode("utf-8") in data


_PS_PARSE = r"""
$path = $env:SL_REPLACER
$text = Get-Content -LiteralPath $path -Raw -Encoding UTF8
$errs = $null
$null = [System.Management.Automation.PSParser]::Tokenize($text, [ref]$errs)
"TOKENIZE=" + $errs.Count
foreach ($e in $errs) { "E1 " + $e.Message }
$tokens = $null
$perrs = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$perrs)
"PARSE=" + $perrs.Count
foreach ($e in $perrs) { "E2 " + $e.Message }
"""


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 필요")
def test_replacer_powershell_syntax(tmp_path):
    script = tmp_path / "replace.ps1"
    updater.write_replacer(str(script))
    probe = tmp_path / "probe.ps1"
    probe.write_text(_PS_PARSE, encoding="ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
                       capture_output=True, timeout=120, env=dict(os.environ, SL_REPLACER=str(script)),
                       creationflags=updater.CREATE_NO_WINDOW)
    out = updater.decode_output(r.stdout) + updater.decode_output(r.stderr)
    assert "TOKENIZE=0" in out and "PARSE=0" in out, out


# --------------------------------------------------------------------------- 실행 인자 · 잠금 · 결과
def test_replacer_command_order():
    base = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", "s.ps1",
            "-AppPid", "123", "-App", r"C:\앱", "-New", r"C:\앱\_update\upd-1\new\StreamLabeler", "-Id", "upd-1",
            "-Exe", "StreamLabeler.exe", "-FromVer", "0.0.1", "-ToVer", "0.0.2"]
    args = ("s.ps1", r"C:\앱", r"C:\앱\_update\upd-1\new\StreamLabeler", "upd-1", "StreamLabeler.exe", 123, "0.0.1", "0.0.2")
    assert updater.replacer_command(*args, lite=False) == base
    assert updater.replacer_command(*args, lite=True) == base + ["-Lite"]
    assert updater.replacer_command(*args, lite=False, no_backup=True) == base + ["-NoBackup"]
    assert updater.replacer_command(*args, lite=True, no_backup=True) == base + ["-Lite", "-NoBackup"]
    assert "-WaitSec" not in updater.replacer_command(*args, lite=False)   # 기본 대기는 스크립트 기본값(120초)


def test_updating_lock(tmp_path):
    root = str(tmp_path / "data")
    assert not updater.update_in_progress(root) and not updater.stale_update_lock(root)
    p = updater.write_updating_lock(root, 4321, "0.0.1", "0.0.2")
    assert p == os.path.join(root, "update", "updating.lock")
    with open(p, encoding="utf-8") as f:
        obj = json.load(f)
    assert obj["pid"] == 4321 and obj["from"] == "0.0.1" and obj["to"] == "0.0.2"
    assert updater.parse_iso_utc(obj["started_at"]).tzinfo is not None
    assert updater.update_in_progress(root) and not updater.stale_update_lock(root)
    old = time.time() - 180
    os.utime(p, (old, old))
    assert not updater.update_in_progress(root)
    assert updater.stale_update_lock(root)                        # 멈춘 업데이트: 잠금 오래됨 + last_result 없음
    with open(os.path.join(root, "update", "last_result.json"), "w", encoding="ascii") as f:
        f.write('{"result":"ok","to":"0.0.2"}')
    assert not updater.stale_update_lock(root)                    # 결과가 있으면 멈춘 것이 아니다


def test_read_last_result(tmp_path):
    root = tmp_path / "data"
    assert updater.read_last_result(str(root)) is None
    (root / "update").mkdir(parents=True)
    p = root / "update" / "last_result.json"
    p.write_text('{"result":"ok","from":"0.0.1","to":"0.0.2","backup_rc":1,"copy_rc":3,"restore_rc":-1,"at":"x"}',
                 encoding="ascii")
    assert updater.read_last_result(str(root))["copy_rc"] == 3
    p.write_bytes(BOM + b'{"result":"blocked"}')                  # BOM 은 벗겨 읽는다 (load_json_strict 규칙)
    assert updater.read_last_result(str(root)) == {"result": "blocked"}
    for broken in (b'{"result": ', b"[1]", b"\xff\xfe", b""):
        p.write_bytes(broken)
        assert updater.read_last_result(str(root)) is None, broken


def _backup_tree(app):
    os.makedirs(os.path.join(app, "_backup", "_internal"), exist_ok=True)
    with open(os.path.join(app, "_backup", "_internal", "core.dll"), "wb") as f:
        f.write(b"x" * 5000)
    with open(os.path.join(app, "_backup", "StreamLabeler.exe"), "wb") as f:
        f.write(b"old")


def _last(root, **kv):
    os.makedirs(os.path.join(root, "update"), exist_ok=True)
    with open(os.path.join(root, "update", "last_result.json"), "w", encoding="ascii") as f:
        json.dump(kv, f)
    with open(os.path.join(root, "update", "backup_ok.json"), "w", encoding="ascii") as f:
        f.write('{"version":"0.0.1"}')


def test_cleanup_after_update_only_when_ok_and_current(tmp_path):
    app = rk.fake_app(str(tmp_path / "app"), "0.0.2")
    root = os.path.join(app, "data")
    _backup_tree(app)
    # 결과 없음 → 보존
    assert updater.cleanup_after_update(app, root, "0.0.2") == 0 and os.path.isdir(os.path.join(app, "_backup"))
    # 실패 결과 → 보존
    for result in ("copy_failed", "restore_failed", "backup_failed", "blocked", "missing_file"):
        _last(root, result=result, to="0.0.2")
        assert updater.cleanup_after_update(app, root, "0.0.2") == 0, result
        assert os.path.isdir(os.path.join(app, "_backup"))
    # 성공했지만 대상 버전이 지금 버전이 아님(되돌린 뒤 등) → 보존
    _last(root, result="ok", to="0.0.3")
    assert updater.cleanup_after_update(app, root, "0.0.2") == 0 and os.path.isdir(os.path.join(app, "_backup"))
    # 성공 + 지금 버전 → _backup 과 backup_ok.json 삭제, 나머지는 그대로
    _last(root, result="ok", to="0.0.2")
    assert updater.cleanup_after_update(app, root, "0.0.2") >= 5003
    assert not os.path.exists(os.path.join(app, "_backup"))
    assert not os.path.exists(os.path.join(root, "update", "backup_ok.json"))
    assert os.path.isfile(os.path.join(root, "update", "last_result.json"))
    assert os.path.isfile(os.path.join(root, "settings.ini"))
    assert os.path.isfile(os.path.join(app, "_internal", "python312.dll"))
    # 두 번째는 할 일 없음
    assert updater.cleanup_after_update(app, root, "0.0.2") == 0


def test_update_state_roundtrip(tmp_path):
    p = str(tmp_path / "data" / "update" / "state.json")
    st = updater.UpdateState(skip_version="0.0.9")
    now = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)
    st.record_ok(now, "0.0.2")
    st.record_fail(now, "가" * 600)
    st.save(p)
    assert not os.path.exists(p + ".tmp")
    back = updater.UpdateState.load(p)
    assert back == st and len(back.last_fail_reason) == 500 and back.last_ok_at == now.isoformat()
    with open(p, "wb") as f:
        f.write(b"{broken")
    assert updater.UpdateState.load(p) == updater.UpdateState()


# --------------------------------------------------------------------------- RobocopyApplier
@pytest.fixture()
def no_onedrive(monkeypatch):
    for v in paths.ONEDRIVE_VARS:
        monkeypatch.delenv(v, raising=False)


def test_preflight_ok(tmp_path, no_onedrive):
    app = rk.fake_app(str(tmp_path / "app"), "0.0.1")
    problems, warnings = updater.RobocopyApplier(SPEC, rk.installed(app), paths.data_root()).preflight(_staged(app))
    assert problems == [] and warnings == []
    assert not any(n.startswith(".update_write_test") for n in os.listdir(app))   # 시험 파일은 지운다


def test_preflight_source_run(tmp_path, no_onedrive):
    app = rk.fake_app(str(tmp_path / "app"), "0.0.1")
    problems, _ = updater.RobocopyApplier(SPEC, rk.installed(app, frozen=False), paths.data_root()).preflight(_staged(app))
    assert any("소스로 실행" in p for p in problems)


def test_preflight_unc_path(tmp_path, no_onedrive, monkeypatch):
    """UNC 경로는 막는다. 네트워크에 닿지 않게 쓰기 시험 · 크기 계산 · 디스크 조회를 가로챈다."""
    unc = r"\\server\share\StreamLabeler"
    real_open = open
    touched = []

    def fake_open(path, *a, **kw):
        if str(path).startswith("\\\\"):
            touched.append(path)
            raise PermissionError("네트워크 경로(테스트)")
        return real_open(path, *a, **kw)
    monkeypatch.setattr(updater, "open", fake_open, raising=False)
    monkeypatch.setattr(updater, "_dir_size", lambda root, exclude=(): 0)
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda p: types.SimpleNamespace(total=10 ** 12, used=0, free=10 ** 12))
    staged = _staged(str(tmp_path / "local"))
    problems, warnings = updater.RobocopyApplier(SPEC, rk.installed(unc), paths.data_root()).preflight(staged)
    assert any("네트워크 드라이브" in p for p in problems), problems
    assert touched and warnings == []
    assert paths.is_network_path(r"\\server\share") and paths.is_network_path("//server/share/x")
    assert paths.is_network_path(r"\\?\UNC\server\share") and not paths.is_network_path(r"\\?\C:\x")
    assert not paths.is_network_path(r"\\.\C:\x") and not paths.is_network_path(r"C:\x")


def test_preflight_onedrive_is_warning_only(tmp_path, no_onedrive, monkeypatch):
    od = tmp_path / "OneDrive - 개인"
    monkeypatch.setenv("OneDrive", str(od))
    app = rk.fake_app(str(od / "프로그램" / "StreamLabeler"), "0.0.1")
    problems, warnings = updater.RobocopyApplier(SPEC, rk.installed(app), paths.data_root()).preflight(_staged(app))
    assert problems == [] and len(warnings) == 1 and "OneDrive" in warnings[0]
    # 대소문자만 다른 환경 변수 값도 같은 폴더로 본다
    monkeypatch.setenv("OneDrive", str(od).upper())
    assert updater.RobocopyApplier(SPEC, rk.installed(app), paths.data_root()).preflight(_staged(app))[1]


def test_preflight_disk_space(tmp_path, no_onedrive, monkeypatch):
    app = rk.fake_app(str(tmp_path / "app"), "0.0.1")
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda p: types.SimpleNamespace(total=100, used=100, free=0))
    problems, _ = updater.RobocopyApplier(SPEC, rk.installed(app), paths.data_root()).preflight(_staged(app))
    assert any("디스크 여유" in p for p in problems)


class _FakePopen:
    """교체 스크립트 대신. mode: silent(아무것도 안 함, 계속 실행 중) · start(started 표식을 씀) · exit(바로 끝남)."""
    calls: list = []
    mode = "silent"

    def __init__(self, cmd, **kw):
        _FakePopen.calls.append((cmd, kw))
        if _FakePopen.mode == "start":
            app, sid = cmd[cmd.index("-App") + 1], cmd[cmd.index("-Id") + 1]
            with open(os.path.join(app, "_update", sid, "started"), "w", encoding="ascii") as f:
                f.write("999")

    def poll(self):
        return 1 if _FakePopen.mode == "exit" else None


@pytest.fixture()
def fake_popen(monkeypatch):
    _FakePopen.calls = []
    _FakePopen.mode = "silent"
    monkeypatch.setattr(updater.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(updater, "execution_policy_report", lambda: "정책(테스트)")
    return _FakePopen


def test_apply_blocked_when_script_never_starts(tmp_path, no_onedrive, fake_popen):
    app = rk.fake_app(str(tmp_path / "app"), "0.0.1")
    root = paths.data_root()
    spec = replace(SPEC, replacer_start_wait_sec=0.5)
    staged = _staged(app)
    t0 = time.monotonic()
    with pytest.raises(UpdateError) as ei:
        updater.RobocopyApplier(spec, rk.installed(app), root).apply(staged)
    dt = time.monotonic() - t0
    assert ei.value.kind == "blocked" and "자동 교체가 막혀" in ei.value.message
    assert 0.5 <= dt < 5, dt
    assert not os.path.exists(os.path.join(root, "update", "updating.lock"))      # 잠금을 지운다
    assert not updater.update_in_progress(root)
    # 실행하려던 명령: appspec 값으로 만든 스크립트 + 인자
    (cmd, kw), = fake_popen.calls
    script = os.path.join(staged.stage_dir, "replace.ps1")
    assert cmd == updater.replacer_command(script, app, staged.new_dir, staged.stage_id, SPEC.exe_name, os.getpid(),
                                           "0.0.1", "0.0.2", False)
    assert kw["cwd"] == app and kw["stdin"] == subprocess.DEVNULL
    assert kw["creationflags"] & updater.CREATE_NO_WINDOW and kw["creationflags"] & updater.CREATE_NEW_PROCESS_GROUP
    with open(script, "rb") as f:
        assert f.read(3) == BOM


def test_apply_blocked_quickly_when_script_exits(tmp_path, no_onedrive, fake_popen):
    fake_popen.mode = "exit"
    app = rk.fake_app(str(tmp_path / "app"), "0.0.1")
    t0 = time.monotonic()
    with pytest.raises(UpdateError) as ei:
        updater.RobocopyApplier(SPEC, rk.installed(app), paths.data_root()).apply(_staged(app))
    assert ei.value.kind == "blocked" and time.monotonic() - t0 < 5     # 10초를 다 기다리지 않는다


def test_apply_returns_when_started_marker_appears(tmp_path, no_onedrive, fake_popen):
    fake_popen.mode = "start"
    app = rk.fake_app(str(tmp_path / "app"), "0.0.1")
    root = paths.data_root()
    staged = _staged(app, chosen="lite")
    updater.RobocopyApplier(replace(SPEC, replacer_start_wait_sec=0.5), rk.installed(app), root).apply(staged)
    lock = os.path.join(root, "update", "updating.lock")
    assert os.path.isfile(lock) and updater.update_in_progress(root)          # 잠금은 스크립트가 지운다
    with open(lock, encoding="utf-8") as f:
        obj = json.load(f)
    assert obj["pid"] == os.getpid() and obj["from"] == "0.0.1" and obj["to"] == "0.0.2"
    (cmd, _), = fake_popen.calls
    assert cmd[-1] == "-Lite"


def test_apply_popen_failure_removes_lock(tmp_path, no_onedrive, monkeypatch):
    """powershell 을 띄우지 못해도(OSError) '표식 없음' 과 같다: 잠금을 지우고 UpdateError('blocked'), 앱은 산다.
    잠금이 남으면 2분 동안 다시 켠 앱이 '업데이트 적용 중' 으로 바로 닫히고, 그 뒤엔 '멈춘 업데이트' 로 보인다 (04 6-9)."""
    def no_powershell(cmd, **kw):
        raise FileNotFoundError(2, "지정된 파일을 찾을 수 없습니다", cmd[0])
    monkeypatch.setattr(updater.subprocess, "Popen", no_powershell)
    monkeypatch.setattr(updater, "execution_policy_report", lambda: "정책(테스트)")
    app = rk.fake_app(str(tmp_path / "app"), "0.0.1")
    root = paths.data_root()
    with pytest.raises(UpdateError) as ei:
        updater.RobocopyApplier(SPEC, rk.installed(app), root).apply(_staged(app))
    assert ei.value.kind == "blocked"
    assert not os.path.exists(os.path.join(root, "update", "updating.lock"))


def test_apply_refuses_on_preflight_problem(tmp_path, no_onedrive, fake_popen):
    app = rk.fake_app(str(tmp_path / "app"), "0.0.1")
    root = paths.data_root()
    with pytest.raises(UpdateError) as ei:
        updater.RobocopyApplier(SPEC, rk.installed(app, frozen=False), root).apply(_staged(app))
    assert ei.value.kind == "precheck"
    assert fake_popen.calls == [] and not os.path.exists(os.path.join(root, "update", "updating.lock"))


def test_rollback_not_yet():
    with pytest.raises(UpdateError) as ei:
        updater.RobocopyApplier(SPEC, rk.installed("x"), "y").rollback()
    assert ei.value.kind == "precheck"


# --------------------------------------------------------------------------- main.parse_args
@pytest.fixture(scope="module")
def main_mod():
    spec = importlib.util.spec_from_file_location("sl_main_for_tests", os.path.join(rk.ROOT, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_args_selftest(main_mod):
    a = main_mod.parse_args(["--selftest", "out.json", "--expect-version", "1.2.3", "--expect-channel", "test",
                             "--data-dir", r"C:\임시\data"])
    assert a["selftest"] == "out.json" and a["expect_version"] == "1.2.3" and a["expect_channel"] == "test"
    assert a["data_dir"] == r"C:\임시\data" and a["update_base_url"] is None and a["flags"] == set()
    assert main_mod.parse_args(["--selftest"])["selftest"] == "selftest.json"


def test_parse_args_defaults_and_base_url(main_mod):
    a = main_mod.parse_args([])
    assert a == {"selftest": None, "expect_version": None, "expect_channel": None, "data_dir": None,
                 "update_base_url": None, "flags": set()}
    assert main_mod.parse_args(["--update-base-url", "http://127.0.0.1:1234/"])["update_base_url"] == "http://127.0.0.1:1234/"
    # 값이 없는 옵션은 무시
    assert main_mod.parse_args(["--data-dir"])["data_dir"] is None


def test_parse_args_startup_flags(main_mod):
    a = main_mod.parse_args(["--updated", "-platform", "offscreen", "--update-failed", "--update-blocked", "--unknown"])
    assert a["flags"] == {"--updated", "--update-failed", "--update-blocked"}
    assert set(main_mod.STARTUP_FLAGS) == {"--updated", "--update-failed", "--update-blocked"}
    # 교체 스크립트가 재실행에 쓰는 플래그와 같다
    text = updater.render_replacer()
    for flag in main_mod.STARTUP_FLAGS:
        assert f'"{flag}"' in text
