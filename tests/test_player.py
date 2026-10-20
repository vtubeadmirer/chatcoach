"""app/player.py — MpvPlayer 계약 검사 (화면 없이: QT_QPA_PLATFORM=offscreen).

offscreen 에는 진짜 창 핸들이 없어 MpvPlayer 가 스스로 vo=null·ao=null 로 만든다. 그래서 '그림이 나오는가' 는
여기서 보지 않고(실제 창에서 확인), 열기·탐색·위치·프레임 이동·A-B 반복·재생 신호만 본다.
합성 영상은 저장소 bin\\ffmpeg\\ffmpeg.exe 로 테스트 중에 만든다 (실제 방송 데이터 없음).
실제 영상 확인은 환경 변수 CHATCOACH_REAL_VIDEO(경로)·CHATCOACH_REAL_VIDEO_MS(기대 길이) 가 있을 때만 돈다.
"""
import os
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import paths  # noqa: E402
from app import player as player_mod  # noqa: E402
from app.player import MpvPlayer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def _wait(qapp, cond, timeout=5.0):
    """cond() 가 참이 될 때까지 이벤트를 돌린다. 참이 되면 True."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return bool(cond())


def _mpv_or_skip():
    mod, err = player_mod._load_mpv_module()
    if mod is None:
        pytest.skip(f"libmpv 를 불러올 수 없어 재생 검사를 건너뜀: {err}")
    return mod


@pytest.fixture(scope="module")
def synth_video(tmp_path_factory):
    """2초, 25fps, 160x90, 키프레임 간격 2초(정확 탐색이 키프레임 탐색과 달라지게), 440Hz 소리."""
    ffmpeg = paths.find_tool("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg 가 없어 합성 영상을 만들 수 없음 (tools/get_ffmpeg.py)")
    out = str(tmp_path_factory.mktemp("player") / "synth.mp4")
    cmd = [ffmpeg, "-nostdin", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=2",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
           "-c:v", "libx264", "-g", "50", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0 or not os.path.isfile(out):
        pytest.skip(f"합성 영상 만들기 실패: {r.stderr[-300:]!r}")
    return out


def _loaded_player(qapp, path):
    p = MpvPlayer()
    errors = []
    p.errorOccurred.connect(errors.append)
    if not p.available():
        _wait(qapp, lambda: errors, 1.0)
        p.close_player()
        pytest.skip(f"mpv 를 만들 수 없음: {errors or p.error()}")
    assert p.load(path) is True
    assert _wait(qapp, p.is_loaded), f"파일이 열리지 않음: {errors}"
    assert _wait(qapp, lambda: p.duration_ms() > 0)
    return p, errors


# ------------------------------------------------------------------ libmpv 없이도 도는 검사

def test_create_and_close_no_exception(qapp):
    p = MpvPlayer()
    errors = []
    p.errorOccurred.connect(errors.append)
    qapp.processEvents()
    assert p.position_ms() == 0
    assert p.duration_ms() == 0
    p.play(); p.pause(); p.toggle(); p.seek(500); p.frame_step(1); p.frame_step(-1)
    p.set_speed(1.5); p.set_volume(50); p.set_ab_loop(100, 200); p.set_ab_loop(None, None)
    p.close_player()
    p.close_player()  # 두 번 불러도 된다
    assert not p.available()
    assert p.load(r"C:\nothing.mp4") is False
    p.seek(1000); p.play(); p.frame_step(3)
    p.deleteLater()
    qapp.processEvents()


def test_missing_libmpv_reports_error_and_does_not_crash(qapp, monkeypatch):
    monkeypatch.setattr(player_mod, "_load_mpv_module", lambda: (None, "libmpv-2.dll 이 없습니다 (시험)"))
    p = MpvPlayer()
    got = []
    p.errorOccurred.connect(got.append)   # 생성 뒤에 연결해도 받아야 한다 (다음 이벤트 루프에서 보냄)
    assert _wait(qapp, lambda: got, 1.0)
    assert "libmpv" in got[0]
    assert not p.available()
    assert p.error() and "libmpv" in p.error()
    got.clear()
    assert p.load(r"C:\x.mp4") is False
    assert got, "mpv 가 없을 때 load 는 errorOccurred 를 보낸다"
    p.play(); p.toggle(); p.seek(10); p.frame_step(2); p.set_ab_loop(1, 2); p.set_speed(2.0); p.set_volume(10)
    assert p.position_ms() == 0 and p.duration_ms() == 0
    p.close_player()


def test_mpv_create_failure_reports_error(qapp, monkeypatch):
    class _BadMod:
        class MPV:
            def __init__(self, **kw):
                raise OSError("vo 초기화 실패 (시험)")
    monkeypatch.setattr(player_mod, "_load_mpv_module", lambda: (_BadMod, None))
    p = MpvPlayer()
    got = []
    p.errorOccurred.connect(got.append)
    assert _wait(qapp, lambda: got, 1.0)
    assert "mpv" in got[0]
    assert not p.available()
    p.close_player()


def test_required_options_present():
    """S-26 ②·02 10장의 mpv 옵션이 생성 옵션에 있다."""
    o = player_mod._OPTIONS_REQUIRED
    for k in ("ytdl", "sub_auto", "audio_file_auto", "load_scripts", "config", "osc", "input_default_bindings"):
        assert o[k] == "no", k
    assert o["hr_seek"] == "yes"
    assert o["keep_open"] == "yes"


@pytest.mark.parametrize("bad", [
    "https://example.com/v.mp4",
    "\\\\203.0.113.5\\v\\vod.mp4",
    "//server/share/v.mp4",
    "relative\\v.mp4",
    "C:\\v\\list.m3u8",
    "C:\\v\\shortcut.lnk",
    "",
])
def test_check_media_path_rejects(bad):
    assert player_mod.check_media_path(bad)


def test_load_rejects_url_and_playlist_without_touching_mpv(qapp, tmp_path):
    p = MpvPlayer()
    got = []
    p.errorOccurred.connect(got.append)
    qapp.processEvents()
    if not p.available():
        p.close_player()
        pytest.skip(f"mpv 를 만들 수 없음: {p.error()}")
    got.clear()
    m3u = tmp_path / "a.m3u8"
    m3u.write_text("#EXTM3U\nhttp://127.0.0.1:9/x.ts\n", encoding="utf-8")
    for bad in ("http://127.0.0.1:9/x.mp4", "\\\\127.0.0.1\\s\\x.mp4", str(m3u), str(tmp_path / "없음.mp4")):
        assert p.load(bad) is False, bad
    assert len(got) == 4
    assert p.path() is None
    p.close_player()


# ------------------------------------------------------------------ libmpv 로 실제 재생

def test_load_seek_position_synthetic(qapp, synth_video):
    _mpv_or_skip()
    p, errors = _loaded_player(qapp, synth_video)
    try:
        assert abs(p.duration_ms() - 2000) <= 60, p.duration_ms()
        assert p.position_ms() == 0
        assert not p.is_playing()

        positions = []
        p.positionChanged.connect(positions.append)
        p.seek(1000, exact=True)
        assert p.position_ms() == 1000  # 탐색 중에는 목표값
        assert positions and positions[-1] == 1000, "seek 는 바로 positionChanged 를 보낸다"
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        assert abs(p.position_ms() - 1000) <= 40, p.position_ms()

        # 정확 탐색: 키프레임(0초)이 아닌 1.52초에 정확히 선다
        p.seek(1520, exact=True)
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        assert abs(p.position_ms() - 1520) <= 40, p.position_ms()

        # 키프레임 탐색도 끝나면 목표값이 지워진다 (어디에 서는지는 libmpv·캐시에 따라 달라 보지 않는다.
        # 이 합성 영상에서는 정확 탐색과 같은 1520 에 선다 — 2초짜리라 디먹서 캐시 안에서 탐색하기 때문으로 보임)
        p.seek(1000)
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        p.seek(1520, exact=False)
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        assert 0 <= p.position_ms() <= 1520 + 40, p.position_ms()

        # 끝을 넘는 값은 길이로, 음수는 0 으로
        p.seek(10_000)
        assert p.position_ms() <= p.duration_ms()
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        p.seek(-50)
        assert p.position_ms() == 0
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        assert errors == []
    finally:
        p.close_player()


def test_frame_step_synthetic(qapp, synth_video):
    _mpv_or_skip()
    p, errors = _loaded_player(qapp, synth_video)
    try:
        p.seek(1000)
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        p.frame_step(5)   # 25fps → +200ms
        assert _wait(qapp, lambda: abs(p.position_ms() - 1200) <= 20), p.position_ms()
        p.frame_step(-3)  # −120ms
        assert _wait(qapp, lambda: abs(p.position_ms() - 1080) <= 20), p.position_ms()
        p.frame_step(1)
        assert _wait(qapp, lambda: abs(p.position_ms() - 1120) <= 20), p.position_ms()
        assert not p.is_playing()
        assert errors == []
    finally:
        p.close_player()


def test_play_pause_signals_and_ab_loop_synthetic(qapp, synth_video):
    _mpv_or_skip()
    p, errors = _loaded_player(qapp, synth_video)
    try:
        states = []
        positions = []
        p.playingChanged.connect(states.append)
        p.positionChanged.connect(positions.append)
        p.set_ab_loop(1400, 1100)  # 거꾸로 줘도 바꿔 넣는다
        assert p.ab_loop() == (1100, 1400)
        p.seek(1100)
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        p.play()
        assert _wait(qapp, lambda: states and states[-1] is True), states
        assert p.is_playing()
        # 0.9초 재생 — 반복 범위는 0.3초라 B(1400) 를 넘지 않고 A 로 돌아온다
        t0 = time.monotonic()
        seen = []
        while time.monotonic() - t0 < 0.9:
            qapp.processEvents()
            seen.append(p.position_ms())
            time.sleep(0.01)
        assert max(seen) <= 1400 + 80, max(seen)
        assert min(seen[len(seen) // 2:]) >= 1100 - 80, "반복이 A 로 돌아오지 않음"
        assert len(set(positions)) >= 5, "재생 중 positionChanged 가 오지 않음"
        p.pause()
        assert _wait(qapp, lambda: states[-1] is False), states
        p.toggle()
        assert _wait(qapp, lambda: states[-1] is True), states
        p.toggle()
        assert _wait(qapp, lambda: states[-1] is False), states
        p.set_ab_loop(None, None)
        assert p.ab_loop() == (None, None)
        assert errors == []
    finally:
        p.close_player()


def test_end_then_play_restarts_from_zero(qapp, synth_video):
    _mpv_or_skip()
    p, errors = _loaded_player(qapp, synth_video)
    try:
        p.set_speed(4.0)
        assert p.speed() == 4.0
        p.set_volume(150)
        assert p.volume() == 100
        p.set_volume(-5)
        assert p.volume() == 0
        p.seek(1700)
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        p.play()
        # keep-open: 끝에서 멈춘다
        assert _wait(qapp, lambda: not p.is_playing() and p.position_ms() >= p.duration_ms() - 100, 5.0), p.position_ms()
        p.play()
        assert _wait(qapp, lambda: p.is_playing())
        assert p.position_ms() < 1000, p.position_ms()
        p.pause()
        assert errors == []
    finally:
        p.close_player()


def test_seek_before_load_applies_after_open_and_hidden_safe(qapp, synth_video):
    """load 직후(열리기 전) seek 는 열린 뒤 적용된다. 한 번도 show 하지 않은(숨은) 위젯에서도 동작한다."""
    _mpv_or_skip()
    p = MpvPlayer()
    if not p.available():
        p.close_player()
        pytest.skip(f"mpv 를 만들 수 없음: {p.error()}")
    try:
        assert not p.isVisible()
        assert p.load(synth_video)
        p.seek(800)
        assert p.position_ms() == 800
        assert _wait(qapp, lambda: p.is_loaded() and p._seek_target_ms is None)
        assert abs(p.position_ms() - 800) <= 40, p.position_ms()
        p.show(); qapp.processEvents(); p.hide(); qapp.processEvents()
        p.seek(300)
        assert _wait(qapp, lambda: p._seek_target_ms is None)
        assert abs(p.position_ms() - 300) <= 40
        # 다른 파일(같은 파일)을 다시 열면 위치·길이가 새로 잡힌다
        durations = []
        p.durationChanged.connect(durations.append)
        assert p.load(synth_video)
        assert _wait(qapp, lambda: p.is_loaded() and p.duration_ms() > 0)
        assert p.position_ms() == 0
        assert durations and durations[0] == 0 and durations[-1] > 0
    finally:
        p.close_player()
    p.seek(100)  # 닫은 뒤에도 예외 없음
    assert not p.available()


@pytest.mark.skipif(not os.environ.get("CHATCOACH_REAL_VIDEO"), reason="CHATCOACH_REAL_VIDEO 가 없어 실제 영상 확인을 건너뜀")
def test_real_video_duration(qapp):
    path = os.environ["CHATCOACH_REAL_VIDEO"]
    if not os.path.isfile(path):
        pytest.skip("CHATCOACH_REAL_VIDEO 파일이 없음")
    _mpv_or_skip()
    p, errors = _loaded_player(qapp, path)
    try:
        expect = int(os.environ.get("CHATCOACH_REAL_VIDEO_MS", "0"))
        print(f"\nreal duration_ms={p.duration_ms()}")
        if expect:
            assert abs(p.duration_ms() - expect) <= 100, (p.duration_ms(), expect)
        p.seek(p.duration_ms() // 2)
        assert _wait(qapp, lambda: p._seek_target_ms is None, 10.0)
        assert abs(p.position_ms() - p.duration_ms() // 2) <= 100
        assert errors == []
    finally:
        p.close_player()
