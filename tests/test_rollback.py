"""M4 받아들임 기준: 시험용 키로 서명한 가짜 서버로 v0.1.0 → v0.1.1 업데이트, 메뉴 함수로 v0.1.0 으로 되돌림
(docs/design/07-roadmap.md 3-1 M4, 04 12-5). 실제 robocopy · PowerShell 을 한글 임시 폴더에서 돌린다 (local_only, Windows).

- 설치본: tmp\\한글 임시\\테스트 문서\\ChatCoach (exe = PING.EXE 사본 + 끝 표식). 스크립트가 끝에 exe 를
  --updated · --rolled-back 으로 재실행하면 PING 이 모르는 옵션이라 바로 끝난다.
- 자체 점검 관문은 updater.selftest_command 만 python 가짜 점검으로 바꾸고 판정 코드(run_selftest_gate)는 그대로 탄다.
- 시험 키는 테스트 안에서 만든다. 서명 키 폴더(%LOCALAPPDATA%\\ChatCoach-signing)는 읽지 않는다.
- 실패 경로: 체크섬 없음 · 체크섬 다름 · zip 이상(보존 폴더 항목) · 자체 점검 실패 → 설치본은 그대로.
"""
from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import release_kit as rk
import fake_release_server as frs
import make_manifest
import package_release
import sign_manifest as sm

from app import paths, updater
from app.appspec import SPEC
from app.updater import UpdateError

pytestmark = [pytest.mark.local_only,
              pytest.mark.skipif(os.name != "nt", reason="robocopy · Windows PowerShell 필요")]

PING = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "PING.EXE")
OLD_TAIL = b"\x00CC-v0.1.0"
NEW_TAIL = b"\x00CC-v0.1.1"
NO_WINDOW = updater.CREATE_NO_WINDOW

FAKE_SELFTEST = r'''
import json, os, sys
exe, out, expect = sys.argv[1], sys.argv[2], sys.argv[3]
d = os.path.dirname(exe)
with open(os.path.join(d, "_internal", "VERSION"), encoding="utf-8") as f:
    v = f.read().strip()
fail = os.environ.get("CC_FAKE_SELFTEST_FAIL") == "1"
ok = v == expect and os.path.isfile(exe) and not fail
with open(out, "w", encoding="utf-8") as f:
    json.dump({"version": v, "ok": ok, "checks": {"version": {"ok": v == expect, "detail": v},
                                                  "fake": {"ok": not fail, "detail": "시험 점검"}}}, f)
sys.exit(0 if ok else 1)
'''


def _ping(tail: bytes) -> bytes:
    with open(PING, "rb") as f:
        return f.read() + tail


def _dead_pid() -> int:
    p = subprocess.Popen(["cmd", "/c", "exit 0"], creationflags=NO_WINDOW)
    p.wait()
    return p.pid


def _kill_from(app: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='ChatCoach.exe'\" | Where-Object { "
                    "$_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:CC_APP + '\\', "
                    "[System.StringComparison]::OrdinalIgnoreCase) } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                   capture_output=True, timeout=60, env=dict(os.environ, CC_APP=app), creationflags=NO_WINDOW)


def _wait_result(data_root: str, timeout: float = 150.0) -> dict:
    """교체 스크립트가 last_result.json 을 쓸 때까지 (앱이 띄우기 전에 지난 결과를 비켜 두므로 새 결과다)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = updater.read_last_result(data_root)
        if r is not None and not os.path.exists(os.path.join(data_root, "update", "updating.lock")):
            return r
        time.sleep(0.3)
    log = Path(data_root, "logs", "update.log")
    raise AssertionError("교체 스크립트 결과가 없습니다: " + (log.read_text("utf-8-sig", "replace") if log.exists() else ""))


# --------------------------------------------------------------------------- 시험 릴리스 v0.1.1 + 가짜 서버
class Rel:
    def __init__(self, work: Path):
        self.work = work
        dist = work / "dist"
        rk.fake_app(str(dist / SPEC.zip_top), "0.1.1", exe=_ping(NEW_TAIL))
        shutil.rmtree(dist / SPEC.zip_top / "data")
        notes = rk.write_notes(str(work / "v0.1.1.md"), min_supported="0.1.0", body="되돌리기 시험용 합성 노트.\n")
        self.out = work / "out"
        assert package_release.main(["--dist", str(dist), "--out", str(self.out), "--version", "0.1.1",
                                     "--notes", notes]) == 0
        self.full = self.out / SPEC.asset_name("0.1.1", "full")
        self.lite = self.out / SPEC.asset_name("0.1.1", "lite")
        self.manifest = make_manifest.build("0.1.1", str(self.full), str(self.lite), notes,
                                            deps=copy.deepcopy(rk.DEPS))
        self.kp = sm.generate_keypair()            # 시험용 키 (그 자리에서 만듦)
        self.publish(self.out, self.manifest)

    @property
    def keys(self):
        return [("TEST", self.kp.public_line())]

    def publish(self, folder: Path, manifest: dict) -> None:
        data = rk.dumps(manifest)                  # make_manifest.write 와 같은 직렬화 (형식 검사 없이: 실패 경로용)
        (folder / "latest.json").write_bytes(data)
        (folder / "latest.json.minisig").write_bytes(
            sm.sign_bytes(data, self.kp, SPEC.trusted_comment(manifest["version"])).encode("utf-8"))


@pytest.fixture(scope="module")
def rel(tmp_path_factory):
    return Rel(tmp_path_factory.mktemp("rel011"))


@pytest.fixture(scope="module")
def server(rel):
    class H(frs.Handler):
        def log_message(self, fmt, *args):  # noqa: N802
            pass
    H.state = frs._State(str(rel.out), "normal", None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield types_ns(handler=H, url=f"http://127.0.0.1:{srv.server_address[1]}/",
                       serve=lambda folder: setattr(H, "state", frs._State(str(folder), "normal", None)))
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


@pytest.fixture()
def gate(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("fake_gate")
    script = d / "fake_selftest.py"
    script.write_text(FAKE_SELFTEST, encoding="utf-8")
    monkeypatch.setattr(updater, "selftest_command",
                        lambda exe, out, version, channel, data_dir: [sys.executable, str(script), exe, out, version])
    monkeypatch.delenv("CC_FAKE_SELFTEST_FAIL", raising=False)
    for v in paths.ONEDRIVE_VARS:
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


@pytest.fixture()
def installed_010(tmp_path, server, rel):
    """다른 폴더의 v0.1.0 사본 (한글 임시 폴더). 합성 라벨 한 줄과 설정."""
    server.serve(rel.out)
    app = tmp_path / "한글 임시" / "테스트 문서" / "ChatCoach"
    rk.fake_app(str(app), "0.1.0", exe=_ping(OLD_TAIL))
    lab = app / "data" / "projects" / "p-시험" / "labels" / "s-1.jsonl"
    lab.parent.mkdir(parents=True)
    lab.write_bytes('{"schema_version":1,"chat_id":"c-합성-1","grade":"A"}\n'.encode("utf-8"))
    yield app
    _kill_from(str(app))


def _snapshot(app: Path) -> dict:
    return {str(p.relative_to(app)): p.read_bytes() for p in app.rglob("*") if p.is_file()
            and not str(p.relative_to(app)).startswith(("_update", "data" + os.sep + "logs", "data" + os.sep + "update"))}


# --------------------------------------------------------------------------- 받아들임 기준
def test_update_010_to_011_then_rollback_with_menu_function(installed_010, rel, server, gate):
    app = installed_010
    data = str(app / "data")
    state = os.path.join(data, "update", "state.json")
    settings_before = (app / "data" / "settings.ini").read_bytes()
    label_before = (app / "data" / "projects" / "p-시험" / "labels" / "s-1.jsonl").read_bytes()
    backups: list[tuple[str, str]] = []

    # 1) 확인: 시험 키로 서명한 가짜 서버 → 새 버전
    inst = rk.installed(str(app), "0.1.0")
    res = updater.run_check(SPEC, inst, state, force=True, base_url=server.url, pubkeys=rel.keys, on_start=True)
    assert res.state == "newer" and res.release.version == "0.1.1" and not res.needs_update, res.reason
    assert res.release.chosen == "lite"

    # 2) 준비: 받기 · 해시 · zip 검사 · 해제 · 자체 점검 관문
    staged = updater.stage(res.release, SPEC, inst, server.url)
    assert staged.selftest["version"] == "0.1.1" and staged.selftest["ok"] is True
    assert not os.path.exists(os.path.join(staged.new_dir, "_update"))

    # 3) 적용: 실제 replace.ps1 + robocopy
    updater.RobocopyApplier(SPEC, inst, data, pid=_dead_pid(),
                            data_backup=lambda a, b: backups.append((a, b))).apply(staged)
    r = _wait_result(data)
    assert r["result"] == "ok" and r["from"] == "0.1.0" and r["to"] == "0.1.1", r
    assert updater.read_dir_version(str(app)) == "0.1.1"
    assert (app / "ChatCoach.exe").read_bytes().endswith(NEW_TAIL)
    assert (app / "_internal" / "ffmpeg" / "ffmpeg.exe").exists() and (app / "_internal" / "libmpv-2.dll").exists()
    assert (app / "data" / "settings.ini").read_bytes() == settings_before
    assert (app / "data" / "projects" / "p-시험" / "labels" / "s-1.jsonl").read_bytes() == label_before
    assert updater.read_dir_version(str(app / "_backup")) == "0.1.0"
    assert updater.read_backup_ok(data)["version"] == "0.1.0"

    # 4) 새 버전(첫 실행 = --updated)에서 메뉴가 켜진다
    from app.ui.update_dialog import rollback_menu_text
    st = updater.rollback_status(str(app), data, "0.1.1")
    text, enabled, _tip = rollback_menu_text(st)
    assert st.available and st.target_version == "0.1.0" and enabled and "0.1.0" in text

    # 5) 메뉴 함수로 되돌리기: 같은 관문(_backup 의 exe) + 같은 replace.ps1(-Rollback)
    inst2 = rk.installed(str(app), "0.1.1")
    t0 = time.monotonic()
    rb = updater.rollback_now(SPEC, inst2, data, state, pid=_dead_pid(), data_backup=lambda a, b: backups.append((a, b)))
    assert rb.selftest["version"] == "0.1.0" and rb.target_version == "0.1.0"
    assert updater.UpdateState.load(state).skip_version == "0.1.1"
    r = _wait_result(data)
    elapsed = time.monotonic() - t0
    assert r["result"] == "rolled_back" and r["from"] == "0.1.1" and r["to"] == "0.1.0", r
    assert r["backup_rc"] == -1 and 0 <= r["copy_rc"] < 8 and r["restore_rc"] == -1
    assert updater.read_dir_version(str(app)) == "0.1.0"
    assert (app / "ChatCoach.exe").read_bytes().endswith(OLD_TAIL)
    assert (app / "_internal" / "lib" / "core.pyd").exists()
    assert (app / "data" / "settings.ini").read_bytes() == settings_before
    assert (app / "data" / "projects" / "p-시험" / "labels" / "s-1.jsonl").read_bytes() == label_before
    assert not (app / "_backup" / "_update").exists() and not (app / "_internal" / "_update").exists()
    assert backups == [("0.1.0", "0.1.1"), ("0.1.1", "0.1.0")]
    log = (app / "data" / "logs" / "update.log").read_text("utf-8-sig", "replace")
    assert "rollback=True" in log and "되돌리기 성공" in log

    # 6) 되돌린 뒤: 메뉴 꺼짐, 다음 시작 정리에서 사본 삭제, 0.1.1 은 건너뛴 버전
    st = updater.rollback_status(str(app), data, "0.1.0")
    assert not st.available and updater.read_backup_ok(data) is None
    assert updater.cleanup_after_update(str(app), data, "0.1.0") > 0 and not (app / "_backup").exists()
    res = updater.run_check(SPEC, rk.installed(str(app), "0.1.0"), state, force=True, base_url=server.url,
                            pubkeys=rel.keys)
    assert res.state == "newer" and res.skipped
    print(f"\n[측정] 되돌리기 시작 → 결과 기록 {elapsed:.1f}s")


# --------------------------------------------------------------------------- 실패 경로 (설치본은 그대로)
def _serve_variant(rel, server, tmp_path, mutate_manifest=None, lite_zip_bytes: bytes | None = None) -> Path:
    folder = tmp_path / "served"
    shutil.copytree(rel.out, folder)
    m = copy.deepcopy(rel.manifest)
    if lite_zip_bytes is not None:
        p = folder / SPEC.asset_name("0.1.1", "lite")
        p.write_bytes(lite_zip_bytes)
        facts = make_manifest.zip_facts(str(p))
        for a in m["assets"]:
            if a["kind"] == "lite":
                a.update(facts)
    if mutate_manifest:
        mutate_manifest(m)
    rel.publish(folder, m)
    server.serve(folder)
    return folder


def _check_and_stage(app: Path, rel, server):
    inst = rk.installed(str(app), "0.1.0")
    res = updater.run_check(SPEC, inst, os.path.join(str(app / "data"), "update", "state.json"), force=True,
                            base_url=server.url, pubkeys=rel.keys)
    return inst, res


def test_missing_checksum_is_refused(installed_010, rel, server, gate, tmp_path):
    before = _snapshot(installed_010)
    _serve_variant(rel, server, tmp_path, lambda m: [a.pop("sha256") for a in m["assets"]])
    _inst, res = _check_and_stage(installed_010, rel, server)
    assert res.state == "failed" and res.kind == "format" and "sha256" in res.reason
    assert _snapshot(installed_010) == before and not (installed_010 / "_update").exists()


def test_checksum_mismatch_is_refused(installed_010, rel, server, gate, tmp_path):
    before = _snapshot(installed_010)
    _serve_variant(rel, server, tmp_path, lambda m: [a.update(sha256="0" * 64) for a in m["assets"]])
    inst, res = _check_and_stage(installed_010, rel, server)
    assert res.state == "newer"
    with pytest.raises(UpdateError) as ei:
        updater.stage(res.release, SPEC, inst, server.url)
    assert ei.value.kind == "verify" and "sha256" in ei.value.message
    assert os.listdir(installed_010 / "_update") == []                    # 준비 폴더를 지웠다
    assert _snapshot(installed_010) == before


def test_bad_zip_is_refused(installed_010, rel, server, gate, tmp_path):
    """서명·해시는 맞지만 zip 이 보존 폴더(data/)를 덮으려는 항목을 가진 경우 (S-43)."""
    before = _snapshot(installed_010)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(rel.lite) as src, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as dst:
        for i in src.infolist():
            dst.writestr(i, src.read(i))
        dst.writestr(f"{SPEC.zip_top}/data/settings.ini", "[x]\nk=덮어쓰기\n")
    _serve_variant(rel, server, tmp_path, lite_zip_bytes=bad.read_bytes())
    inst, res = _check_and_stage(installed_010, rel, server)
    assert res.state == "newer" and res.release.chosen == "lite"
    with pytest.raises(UpdateError) as ei:
        updater.stage(res.release, SPEC, inst, server.url)
    assert ei.value.kind == "verify" and "보존 폴더" in ei.value.message
    assert os.listdir(installed_010 / "_update") == []
    assert _snapshot(installed_010) == before


def test_truncated_zip_is_refused(installed_010, rel, server, gate, tmp_path):
    """서명·크기·해시는 맞게 다시 냈지만 zip 자체가 잘린 경우."""
    import hashlib
    before = _snapshot(installed_010)
    data = rel.lite.read_bytes()
    broken = data[: len(data) // 2]
    folder = tmp_path / "served"
    shutil.copytree(rel.out, folder)
    (folder / SPEC.asset_name("0.1.1", "lite")).write_bytes(broken)
    m = copy.deepcopy(rel.manifest)
    for a in m["assets"]:
        if a["kind"] == "lite":
            a.update(size=len(broken), sha256=hashlib.sha256(broken).hexdigest())
    rel.publish(folder, m)
    server.serve(folder)
    inst, res = _check_and_stage(installed_010, rel, server)
    assert res.state == "newer"
    with pytest.raises(UpdateError) as ei:
        updater.stage(res.release, SPEC, inst, server.url)
    assert ei.value.kind == "verify" and "zip" in ei.value.message
    assert _snapshot(installed_010) == before


def test_selftest_failure_is_refused(installed_010, rel, server, gate):
    gate.setenv("CC_FAKE_SELFTEST_FAIL", "1")
    before = _snapshot(installed_010)
    inst, res = _check_and_stage(installed_010, rel, server)
    assert res.state == "newer"
    with pytest.raises(UpdateError) as ei:
        updater.stage(res.release, SPEC, inst, server.url)
    assert ei.value.kind == "selftest" and "시험 점검" in ei.value.message
    kept = os.listdir(installed_010 / "_update")
    assert len(kept) == 1 and (installed_010 / "_update" / kept[0] / updater.FAILED_MARKER).exists()
    assert _snapshot(installed_010) == before
    assert not (installed_010 / "data" / "update" / "updating.lock").exists()
