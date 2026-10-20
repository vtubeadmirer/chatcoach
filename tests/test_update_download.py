"""가짜 릴리스 서버(tools/fake_release_server.py)로 확인 → 준비(stage) 전체 흐름 (docs/design/04 12-3 · 12-4).

서버는 테스트 안에서 127.0.0.1 포트 0 으로 띄운다 (스레드, 끝나면 shutdown). 외부 네트워크는 쓰지 않는다.
- 모드: normal(newer → stage 성공: exe·VERSION·경량이면 heavy 링크, pkg.zip 삭제, <앱>\\_update\\upd-*),
  no_sig · bad_sig · wrong_key → failed/signature, old_version → latest, old_published → failed/format,
  size_mismatch → stage 가 UpdateError('download') 이고 준비 폴더가 지워짐, primary_404 → 2차 경로(REST API)로 newer.
- download: 취소 · 진행률 · 크기 초과('많이 받았습니다') · https 아닌 외부 주소 거부 · HTTP 오류.
- 자체 점검 관문: 실패하면 준비 폴더 1개를 FAILED 표식과 함께 남기고 이전 것은 지운다. cleanup_stage_dirs 는 FAILED 없는 것만 지운다.
"""
from __future__ import annotations

import io
import os
import shutil
import threading
from http.server import ThreadingHTTPServer

import pytest

import release_kit as rk
import fake_release_server as frs

from app import paths, updater
from app.appspec import SPEC
from app.updater import UpdateError

PING = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "PING.EXE")


@pytest.fixture(scope="module")
def rel(tmp_path_factory):
    return rk.build_release(str(tmp_path_factory.mktemp("drel")))


class _Server:
    def __init__(self, rel, handler, port):
        self.rel = rel
        self.handler = handler
        self.url = f"http://127.0.0.1:{port}/"

    def mode(self, mode: str, key: bool = True) -> None:
        self.handler.state = frs._State(self.rel.out, mode, self.rel.key_path if key else None)
        self.handler.requests.clear()

    def asset_url(self, name: str) -> str:
        return f"{self.url}{SPEC.repo}/releases/download/v{self.rel.version}/{name}"


@pytest.fixture(scope="module")
def server(rel):
    class Recording(frs.Handler):
        requests: list[str] = []

        def log_message(self, fmt, *args):  # noqa: N802 — 출력 대신 요청 경로를 모은다
            Recording.requests.append(self.path)

    Recording.state = frs._State(rel.out, "normal", None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Recording)
    t = threading.Thread(target=srv.serve_forever, name="fake-release-server", daemon=True)
    t.start()
    try:
        yield _Server(rel, Recording, srv.server_address[1])
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


@pytest.fixture()
def app_dir(tmp_path):
    """설치본 0.0.1 (ffmpeg·libmpv·deps.txt 있음 → 경량 대상). 한글 폴더 아래."""
    return rk.fake_app(str(tmp_path / "설치 폴더" / "ChatCoach"), "0.0.1")


@pytest.fixture()
def state_path():
    return os.path.join(paths.data_root(), "update", "state.json")


def _check(server, inst, state_path):
    return updater.run_check(SPEC, inst, state_path, force=True, base_url=server.url, pubkeys=server.rel.keys)


def _stage_dirs(app):
    root = os.path.join(app, "_update")
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root) if d.startswith(updater.STAGE_PREFIX))


# --------------------------------------------------------------------------- normal
def test_normal_check_and_stage_lite(server, app_dir, state_path):
    server.mode("normal")
    inst = rk.installed(app_dir)
    r = _check(server, inst, state_path)
    assert r.state == "newer" and r.release.version == "0.0.2" and r.release.chosen == "lite", r
    assert any(p.endswith("/releases/latest/download/latest.json") for p in server.handler.requests)
    progress: list[tuple[float, str]] = []
    st = updater.stage(r.release, SPEC, inst, server.url, progress=lambda f, t: progress.append((f, t)),
                       selftest=False)
    assert isinstance(st, updater.StagedUpdate) and st.lite and st.selftest == {}
    root = os.path.join(app_dir, "_update")
    assert os.path.dirname(st.stage_dir) == root and st.stage_id.startswith("upd-")
    assert st.stage_id == os.path.basename(st.stage_dir) and _stage_dirs(app_dir) == [st.stage_id]
    assert st.new_dir == os.path.join(st.stage_dir, "new", "ChatCoach")
    assert os.path.isfile(os.path.join(st.new_dir, "ChatCoach.exe"))
    with open(os.path.join(st.new_dir, "_internal", "VERSION"), encoding="utf-8") as f:
        assert f.read().strip() == "0.0.2"
    # 경량: 설치본의 ffmpeg·libmpv 가 붙어 있다 (자체 점검이 돌 수 있게)
    for rel_name in (("libmpv-2.dll",), ("ffmpeg", "ffmpeg.exe"), ("ffmpeg", "avcodec-61.dll")):
        linked = os.path.join(st.new_dir, "_internal", *rel_name)
        with open(linked, "rb") as a, open(os.path.join(app_dir, "_internal", *rel_name), "rb") as b:
            assert a.read() == b.read()
    assert not os.path.exists(os.path.join(st.stage_dir, "pkg.zip"))
    assert not os.path.exists(os.path.join(st.stage_dir, "pkg.zip.part"))
    # 진행률: 0 → … → 1, 단조 증가
    fracs = [f for f, _ in progress]
    assert fracs[0] == 0.0 and fracs[-1] == 1.0 and fracs == sorted(fracs) and all(0 <= f <= 1 for f in fracs)
    assert progress[0][1].startswith("경량") and progress[-1][1] == "준비 완료"
    assert any(p.endswith(SPEC.asset_name("0.0.2", "lite")) for p in server.handler.requests)
    # 상태 기록
    st_saved = updater.UpdateState.load(state_path)
    assert st_saved.last_seen_version == "0.0.2" and st_saved.last_ok_at


def test_normal_stage_full_when_no_heavy(server, tmp_path, state_path):
    server.mode("normal")
    app = rk.fake_app(str(tmp_path / "app" / "ChatCoach"), "0.0.1", heavy=False)
    inst = rk.installed(app, has_heavy=False)
    r = _check(server, inst, state_path)
    assert r.state == "newer" and r.release.chosen == "full"
    st = updater.stage(r.release, SPEC, inst, server.url, selftest=False)
    assert not st.lite
    assert os.path.isfile(os.path.join(st.new_dir, "_internal", "ffmpeg", "ffmpeg.exe"))
    assert os.path.isfile(os.path.join(st.new_dir, "_internal", "libmpv-2.dll"))
    assert any(p.endswith(SPEC.asset_name("0.0.2", "full")) for p in server.handler.requests)


# --------------------------------------------------------------------------- 실패 모드
def test_no_sig(server, app_dir, state_path):
    server.mode("no_sig")
    r = _check(server, rk.installed(app_dir), state_path)
    assert r.state == "failed" and r.kind == "signature" and "서명 파일" in r.reason
    assert updater.UpdateState.load(state_path).last_fail_at


def test_bad_sig(server, app_dir, state_path):
    server.mode("bad_sig")
    r = _check(server, rk.installed(app_dir), state_path)
    assert r.state == "failed" and r.kind == "signature" and "서명 검증 실패" in r.reason


def test_wrong_key(server, app_dir, state_path):
    server.mode("wrong_key")
    r = _check(server, rk.installed(app_dir), state_path)
    assert r.state == "failed" and r.kind == "signature" and "모르는" in r.reason


def test_old_version(server, app_dir, state_path):
    server.mode("old_version")
    r = _check(server, rk.installed(app_dir), state_path)
    assert r.state == "latest" and r.ok, r


def test_resign_modes_require_key(rel):
    for mode in ("old_version", "old_published"):
        with pytest.raises(SystemExit):
            frs._State(rel.out, mode, None)


def test_old_published(server, app_dir, state_path):
    server.mode("old_published")
    r = _check(server, rk.installed(app_dir), state_path)
    assert r.state == "failed" and r.kind == "format" and "발행 시각" in r.reason


def test_size_mismatch_stage_fails_and_cleans(server, app_dir, state_path):
    server.mode("size_mismatch")
    inst = rk.installed(app_dir)
    r = _check(server, inst, state_path)
    assert r.state == "newer"
    with pytest.raises(UpdateError) as ei:
        updater.stage(r.release, SPEC, inst, server.url, selftest=False)
    assert ei.value.kind == "download"
    assert os.path.isdir(os.path.join(app_dir, "_update")) and _stage_dirs(app_dir) == []


def test_primary_404_uses_rest_api(server, app_dir, state_path):
    server.mode("primary_404")
    inst = rk.installed(app_dir)
    r = _check(server, inst, state_path)
    assert r.state == "newer" and r.release.version == "0.0.2", r
    reqs = server.handler.requests
    assert any(p.endswith("/releases/latest/download/latest.json") for p in reqs)   # 1차 시도 (404)
    assert "/api/releases/latest" in reqs                                           # 2차
    assert any(p.endswith("/releases/download/v0.0.2/latest.json.minisig") for p in reqs)
    # 자산 주소는 매니페스트의 고정 경로라 1차 장애와 상관없이 받힌다
    st = updater.stage(r.release, SPEC, inst, server.url, selftest=False)
    assert os.path.isfile(os.path.join(st.new_dir, "ChatCoach.exe"))


def test_check_without_base_url_on_test_channel_never_hits_fake_server(server, app_dir, state_path, monkeypatch):
    """base_url 이 허용되지 않으면(release 채널) github.com 으로 가야 한다 — 여기서는 fetch 를 가로채 주소만 본다."""
    seen = []

    def fake_get_small(url, limit, spec, installed, accept=None):
        seen.append(url)
        raise UpdateError("network", "차단(테스트)")
    monkeypatch.setattr(updater, "_get_small", fake_get_small)
    r = updater.run_check(SPEC, rk.installed(app_dir, channel="release"), state_path, force=True,
                          base_url=server.url, pubkeys=server.rel.keys)
    assert r.state == "failed" and r.kind == "network"
    assert seen and all(u.startswith("https://") for u in seen) and not any("127.0.0.1" in u for u in seen)


# --------------------------------------------------------------------------- download
def _full_size(rel):
    return os.path.getsize(rel.full_zip)


def test_download_cancel(server, app_dir, tmp_path):
    server.mode("normal")
    url = server.asset_url(SPEC.asset_name("0.0.2", "full"))
    with pytest.raises(UpdateError) as ei:
        updater.download(url, str(tmp_path / "x.part"), _full_size(server.rel), SPEC, rk.installed(app_dir),
                         cancel=lambda: True)
    assert ei.value.kind == "download" and ei.value.message == "취소됨"


def test_download_progress_and_exact_size(server, app_dir, tmp_path):
    server.mode("normal")
    url = server.asset_url(SPEC.asset_name("0.0.2", "full"))
    calls = []
    dst = tmp_path / "x.part"
    updater.download(url, str(dst), _full_size(server.rel), SPEC, rk.installed(app_dir),
                     progress=lambda f, t: calls.append((f, t)), cancel=lambda: False)
    assert calls and all(0 <= f <= 1 for f, _ in calls) and calls[-1][0] == 1.0
    assert all(isinstance(t, str) and "받는 중" in t for _, t in calls)
    with open(server.rel.full_zip, "rb") as f:
        assert dst.read_bytes() == f.read()


def test_download_size_smaller_than_actual(server, app_dir, tmp_path):
    server.mode("normal")
    url = server.asset_url(SPEC.asset_name("0.0.2", "full"))
    # 가짜 서버는 Content-Length 를 보내므로 그 단계에서 먼저 막힌다
    with pytest.raises(UpdateError) as ei:
        updater.download(url, str(tmp_path / "x.part"), _full_size(server.rel) - 10, SPEC, rk.installed(app_dir))
    assert ei.value.kind == "download" and "Content-Length" in ei.value.message


class _NoLengthResponse:
    """Content-Length 없이 보내는 응답 (청크 전송 흉내)."""

    def __init__(self, body: bytes):
        self.headers = {}
        self._b = io.BytesIO(body)

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_more_than_size_without_content_length(app_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "open_url", lambda *a, **kw: _NoLengthResponse(b"x" * 1000))
    with pytest.raises(UpdateError) as ei:
        updater.download("http://127.0.0.1:9/x.zip", str(tmp_path / "x.part"), 100, SPEC, rk.installed(app_dir))
    assert ei.value.kind == "download" and "많이 받았습니다" in ei.value.message
    # 모자라면 '받은 크기' 불일치
    monkeypatch.setattr(updater, "open_url", lambda *a, **kw: _NoLengthResponse(b"x" * 50))
    with pytest.raises(UpdateError) as ei:
        updater.download("http://127.0.0.1:9/x.zip", str(tmp_path / "y.part"), 100, SPEC, rk.installed(app_dir))
    assert ei.value.kind == "download" and "받은 크기" in ei.value.message


def test_download_refuses_non_https_external(app_dir, tmp_path):
    for url, channel in (("http://example.com/ChatCoach.zip", "test"),
                         ("http://127.0.0.1:9/x.zip", "release"),        # release 채널은 루프백 http 도 안 됨
                         ("ftp://example.com/x.zip", "test")):
        with pytest.raises(UpdateError) as ei:
            updater.download(url, str(tmp_path / "x.part"), 100, SPEC, rk.installed(app_dir, channel=channel))
        assert ei.value.kind == "network", (url, ei.value.kind)


def test_download_http_error_and_size_range(server, app_dir, tmp_path):
    server.mode("normal")
    with pytest.raises(UpdateError) as ei:
        updater.download(server.asset_url("missing.zip"), str(tmp_path / "x.part"), 100, SPEC, rk.installed(app_dir))
    assert ei.value.kind == "download" and "404" in ei.value.message
    for size in (0, -1, SPEC.max_zip_bytes + 1):
        with pytest.raises(UpdateError) as ei:
            updater.download(server.asset_url("x.zip"), str(tmp_path / "x.part"), size, SPEC, rk.installed(app_dir))
        assert ei.value.kind == "download"


def test_stage_sha256_mismatch_cleans(server, app_dir, state_path):
    from dataclasses import replace
    server.mode("normal")
    inst = rk.installed(app_dir)
    r = _check(server, inst, state_path)
    m = r.release.manifest
    bad_assets = tuple(replace(a, sha256="0" * 64) if a.kind == "lite" else a for a in m.assets)
    bad = replace(r.release, manifest=replace(m, assets=bad_assets))
    with pytest.raises(UpdateError) as ei:
        updater.stage(bad, SPEC, inst, server.url, selftest=False)
    assert ei.value.kind == "verify" and "sha256" in ei.value.message
    assert _stage_dirs(app_dir) == []


def test_stage_cancel_cleans(server, app_dir, state_path):
    server.mode("normal")
    inst = rk.installed(app_dir)
    r = _check(server, inst, state_path)
    with pytest.raises(UpdateError) as ei:
        updater.stage(r.release, SPEC, inst, server.url, cancel=lambda: True, selftest=False)
    assert ei.value.message == "취소됨" and _stage_dirs(app_dir) == []


# --------------------------------------------------------------------------- 자체 점검 관문
def test_selftest_failure_keeps_one_failed_stage(server, app_dir, state_path, monkeypatch):
    server.mode("normal")
    inst = rk.installed(app_dir)
    r = _check(server, inst, state_path)
    gate_calls = []

    def failing_gate(new_app_dir, version, channel, stage_dir, spec=SPEC):
        gate_calls.append((new_app_dir, version, channel, stage_dir))
        raise UpdateError("selftest", "새 버전이 이 PC 에서 실행되지 않습니다 (시험)")
    monkeypatch.setattr(updater, "run_selftest_gate", failing_gate)

    with pytest.raises(UpdateError) as ei:
        updater.stage(r.release, SPEC, inst, server.url)
    assert ei.value.kind == "selftest"
    first = _stage_dirs(app_dir)
    assert len(first) == 1
    first_dir = os.path.join(app_dir, "_update", first[0])
    assert os.path.isfile(os.path.join(first_dir, "FAILED"))
    assert os.path.isdir(os.path.join(first_dir, "new", "ChatCoach"))      # 원인 분석용으로 남김
    nd, ver, ch, sd = gate_calls[0]
    assert ver == "0.0.2" and ch == "test" and sd == first_dir and nd == os.path.join(first_dir, "new", "ChatCoach")

    with pytest.raises(UpdateError):
        updater.stage(r.release, SPEC, inst, server.url)
    second = _stage_dirs(app_dir)
    assert len(second) == 1 and second != first                               # 이전 실패 폴더는 지움
    assert os.path.isfile(os.path.join(app_dir, "_update", second[0], "FAILED"))

    # 정상 시작 정리: FAILED 표식이 있는 것은 남기고, 이름 형식이 맞는 나머지만 지운다
    assert updater.cleanup_stage_dirs(app_dir) == 0 and _stage_dirs(app_dir) == second
    root = os.path.join(app_dir, "_update")
    os.makedirs(os.path.join(root, "upd-leftover", "new"))
    os.makedirs(os.path.join(root, "keep-me"))
    with open(os.path.join(root, "upd-file.txt"), "w") as f:
        f.write("x")
    assert updater.cleanup_stage_dirs(app_dir) == 1
    assert sorted(os.listdir(root)) == sorted(second + ["keep-me", "upd-file.txt"])
    assert updater.cleanup_stage_dirs(str(app_dir) + "-없음") == 0


NO_SUCH_PID = 2_000_000_000      # 실제로 있을 수 없는 pid


def test_stage_active_marker_protects_in_use_dirs(server, app_dir, state_path):
    """준비 중·준비된 폴더에는 ACTIVE(pid) 표식이 있고, 그 pid 가 살아 있으면 정리가 지우지 않는다."""
    server.mode("normal")
    inst = rk.installed(app_dir)
    r = _check(server, inst, state_path)
    st = updater.stage(r.release, SPEC, inst, server.url, selftest=False)
    marker = os.path.join(st.stage_dir, updater.ACTIVE_MARKER)
    with open(marker, encoding="ascii") as f:
        assert f.read().strip() == str(os.getpid())
    assert updater.stage_in_use(st.stage_dir)
    assert updater.cleanup_stage_dirs(app_dir) == 0 and os.path.isdir(st.stage_dir)
    # 표식의 pid 가 죽었으면 정리 대상
    with open(marker, "w", encoding="ascii") as f:
        f.write(str(NO_SUCH_PID))
    assert not updater.stage_in_use(st.stage_dir)
    assert updater.cleanup_stage_dirs(app_dir) == 1 and not os.path.exists(st.stage_dir)


def test_stage_in_use_bad_marker(tmp_path):
    d = tmp_path / "upd-x"
    d.mkdir()
    assert not updater.stage_in_use(str(d))                       # 표식 없음
    for text in ("", "abc", "-", "1.5"):
        (d / updater.ACTIVE_MARKER).write_text(text, encoding="ascii")
        assert not updater.stage_in_use(str(d)), text


def test_keep_one_failed_stage_spares_in_use(tmp_path):
    root = tmp_path / "_update"
    keep, inuse, old, dead, other = (root / n for n in ("upd-keep", "upd-inuse", "upd-old", "upd-dead", "other"))
    for d in (keep, inuse, old, dead, other):
        d.mkdir(parents=True)
    (keep / updater.ACTIVE_MARKER).write_text(str(os.getpid()), encoding="ascii")
    (inuse / updater.ACTIVE_MARKER).write_text(str(os.getpid()), encoding="ascii")   # 다른 창이 준비 중인 폴더
    (old / updater.FAILED_MARKER).write_text("selftest failed\n", encoding="utf-8")
    (dead / updater.ACTIVE_MARKER).write_text(str(NO_SUCH_PID), encoding="ascii")
    updater.keep_one_failed_stage(str(root), str(keep))
    assert (keep / updater.FAILED_MARKER).exists() and not (keep / updater.ACTIVE_MARKER).exists()
    assert inuse.exists() and other.exists()
    assert not old.exists() and not dead.exists()


def test_selftest_gate_cannot_run_missing_exe(tmp_path):
    with pytest.raises(UpdateError) as ei:
        updater.run_selftest_gate(str(tmp_path / "nope"), "0.0.2", "test", str(tmp_path))
    assert ei.value.kind == "selftest" and "실행할 수 없습니다" in ei.value.message


@pytest.mark.skipif(not os.path.isfile(PING), reason="Windows PING.EXE 필요")
def test_selftest_gate_nonzero_exit(tmp_path):
    """--selftest 를 모르는 exe(PING 사본)는 exit ≠ 0 → 관문 실패. 사용자 data 대신 준비 폴더 아래 --data-dir 를 준다."""
    new = tmp_path / "new" / "ChatCoach"
    new.mkdir(parents=True)
    shutil.copy(PING, new / "ChatCoach.exe")
    with pytest.raises(UpdateError) as ei:
        updater.run_selftest_gate(str(new), "0.0.2", "test", str(tmp_path))
    assert ei.value.kind == "selftest" and "exit" in ei.value.message
