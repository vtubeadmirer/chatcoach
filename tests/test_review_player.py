"""app/ui/review_player.py — 7-2 확인 범위 반복·속도·들은 범위·회차·화면 위 문구 (화면 없이, 합성 데이터만).

대역 플레이어(FakePlayer)로 위치 신호를 흉내 내 '들은 범위' 셈을 본다. 진짜 MpvPlayer 로는
합성 영상(bin\\ffmpeg)을 만들 수 있고 libmpv 가 있을 때만 범위 반복이 걸리는지 짧게 본다.
"""
import os
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.ui import review_player as rp  # noqa: E402
from app.ui.review_player import ReviewPlayer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


class FakePlayer(QObject):
    """MpvPlayer 와 같은 모양의 대역. 시각은 영상 파일 ms."""
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playingChanged = Signal(bool)
    errorOccurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.pos = 0
        self.playing = False
        self._speed = 1.0
        self._volume = 100
        self.muted = False
        self.ab = (None, None)
        self.osd = []
        self.af = None
        self.seeks = []

    def position_ms(self):
        return self.pos

    def is_playing(self):
        return self.playing

    def speed(self):
        return self._speed

    def volume(self):
        return self._volume

    def play(self):
        if not self.playing:
            self.playing = True
            self.playingChanged.emit(True)

    def pause(self):
        if self.playing:
            self.playing = False
            self.playingChanged.emit(False)

    def seek(self, ms, exact=True):
        self.pos = int(ms)
        self.seeks.append(self.pos)
        self.positionChanged.emit(self.pos)

    def set_speed(self, x):
        self._speed = float(x)

    def set_volume(self, v):
        self._volume = int(v)

    def set_mute(self, m):
        self.muted = bool(m)

    def set_ab_loop(self, a, b):
        self.ab = (a, b)

    def show_text(self, text, duration_ms=2000):
        self.osd.append((text, duration_ms))

    def set_audio_normalize(self, on):
        self.af = bool(on)
        return True

    # 시험 도우미: 재생 중 위치 신호를 step 마다 보낸다
    def run(self, start, end, step=40):
        self.play()
        t = start
        self.pos = t
        self.positionChanged.emit(t)
        while t < end:
            t = min(end, t + step)
            self.pos = t
            self.positionChanged.emit(t)


# ---------------------------------------------------------------------------- 순수 도우미

def test_merge_clip_coverage():
    assert rp.merge_ranges([(5, 10), (0, 5), (20, 30), (8, 12), (40, 40)]) == [(0, 12), (20, 30)]
    assert rp.clip_ranges([(0, 12), (20, 30)], 10, 25) == [(10, 12), (20, 25)]
    assert rp.coverage([(0, 50)], 0, 100) == 0.5
    assert rp.coverage([(0, 500)], 0, 100) == 1.0
    assert rp.coverage([], 0, 100) == 0.0
    assert rp.coverage([(0, 10)], 5, 5) == 0.0


def test_review_window_rule_7_2():
    # 채팅 −10초 ~ 발화 끝 +30초
    assert rp.review_window(100_000, 112_000) == (90_000, 142_000)
    # 발화 끝을 모르면 채팅 시각 기준, 시작은 0 아래로 가지 않음
    assert rp.review_window(4_000, None) == (0, 34_000)
    # 발화가 채팅보다 앞서 끝나도 끝은 채팅 시각 + post_roll 보다 앞당기지 않는다
    assert rp.review_window(100_000, 95_000) == (90_000, 130_000)
    assert rp.review_window(100_000, 110_000, pre_roll_ms=5_000, post_roll_ms=10_000) == (95_000, 120_000)


def test_next_speed_presets():
    assert rp.SPEEDS == (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
    assert rp.next_speed(1.0, +1) == 1.25
    assert rp.next_speed(1.0, -1) == 0.75
    assert rp.next_speed(2.0, +1) == 2.0
    assert rp.next_speed(0.5, -1) == 0.5
    assert rp.next_speed(1.1, +1) == 1.25 and rp.next_speed(1.1, -1) == 1.0


# ---------------------------------------------------------------------------- 들은 범위

def test_watched_ranges_only_while_playing_audible_and_slow(qapp):
    p = FakePlayer()
    r = ReviewPlayer(p)
    r.reset()
    r.set_window(0, 10_000)
    p.run(0, 4_000)
    assert r.current_pass().ranges == [(0, 4_000)]
    # 정지 중 탐색은 듣지 않은 것
    p.pause()
    p.seek(6_000)
    assert r.current_pass().ranges == [(0, 4_000)]
    # 음소거면 세지 않음
    r.set_mute(True)
    p.run(6_000, 7_000)
    assert r.current_pass().ranges == [(0, 4_000)]
    r.set_mute(False)
    # 2배 넘으면 세지 않음 (heard_max_speed 2.0)
    r.set_speed(2.0)
    p.run(7_000, 8_000)
    assert (7_000, 8_000) in r.current_pass().ranges
    r.set_speed(3.0)
    p.run(8_000, 9_000, step=120)
    assert all(b <= 8_000 for _a, b in r.current_pass().ranges)
    # 음량 0 도 소리 없음
    r.set_speed(1.0)
    p.set_volume(0)
    p.run(9_000, 10_000)
    assert all(b <= 8_000 for _a, b in r.current_pass().ranges)
    s = r.pass_summaries()[0]
    assert s["window_start_ms"] == 0 and s["window_end_ms"] == 10_000
    assert s["watched_ranges"] == [[0, 4_000], [7_000, 8_000]]
    assert s["coverage_ratio"] == 0.5 and s["audio_heard"] is False
    assert s["max_speed"] == 2.0


def test_jumps_are_not_heard(qapp):
    p = FakePlayer()
    r = ReviewPlayer(p)
    r.reset()
    r.set_window(0, 60_000)
    p.play()
    p.seek(1_000)
    p.seek(1_040)
    p.seek(30_000)        # 앞으로 크게 뛴 것은 건너뜀
    p.seek(30_040)
    p.seek(10_000)        # 되감기
    rs = r.current_pass().ranges
    assert rs == [(1_000, 1_040), (30_000, 30_040)]


def test_time_map_vod_vs_file(qapp):
    p = FakePlayer()
    off = 5_000           # 영상 파일 0 = VOD 5000
    r = ReviewPlayer(p, to_file=lambda v: v - off, to_vod=lambda f: f + off)
    r.reset()
    r.set_window(10_000, 20_000)       # VOD
    assert r.start_loop() is True
    assert p.ab == (5_000, 15_000)     # 파일 시각으로 걸림
    assert p.seeks[-1] == 5_000
    p.run(5_000, 15_000)
    assert r.current_coverage() == 1.0
    assert r.heard_count() == 1
    assert r.position_vod() == 20_000


def test_loop_toggle_and_passes(qapp):
    p = FakePlayer()
    r = ReviewPlayer(p)
    started = []
    loops = []
    r.passStarted.connect(started.append)
    r.loopChanged.connect(loops.append)
    r.reset()
    assert started == [0]
    a, b = r.set_window_for(100_000, 110_000)
    assert (a, b) == (90_000, 140_000)
    assert r.toggle_loop() is True and r.loop_on()
    assert p.ab == (90_000, 140_000) and p.playing
    p.run(90_000, 140_000)
    assert r.heard_count() == 1
    assert r.toggle_loop() is False and p.ab == (None, None)
    # 다시 켜면 새 회차(다시 듣기)
    r.toggle_loop()
    assert started == [0, 1]
    p.run(90_000, 140_000)
    assert r.heard_count() == 2
    sums = r.pass_summaries()
    assert [s["pass"] for s in sums] == [0, 1]
    assert all(s["audio_heard"] for s in sums)
    assert loops == [True, False, True]
    # 빈 회차에서 다시 켜도 회차가 늘지 않는다
    r.stop_loop()
    r.begin_pass()
    n = len(r.passes())
    r.start_loop(play=False)
    assert len(r.passes()) == n


def test_window_change_updates_loop_and_keeps_history(qapp):
    p = FakePlayer()
    r = ReviewPlayer(p)
    r.reset()
    r.set_window(0, 10_000)
    r.start_loop()
    p.run(0, 10_000)
    r.set_window(0, 20_000)
    assert p.ab == (0, 20_000)
    assert r.current_coverage() == 0.5     # 같은 기록을 새 범위로 다시 잼
    assert r.heard_count() == 0


def test_speed_up_down_and_signal(qapp):
    p = FakePlayer()
    r = ReviewPlayer(p)
    got = []
    r.speedChanged.connect(got.append)
    r.speed_up()
    r.speed_up()
    r.speed_down()
    assert got == [1.25, 1.5, 1.25] and p.speed() == 1.25


def test_osd_text_on_loop_start_and_wrap(qapp):
    p = FakePlayer()
    r = ReviewPlayer(p)
    r.reset()
    r.set_window(1_000, 5_000)
    r.set_osd_text("판정 중 · 시청자 0001", show=False)
    assert p.osd == []
    r.start_loop()
    assert p.osd and p.osd[-1][0] == "판정 중 · 시청자 0001"
    n = len(p.osd)
    p.run(1_000, 5_000)
    p.seek(1_000)          # 반복이 범위 시작으로 되감김
    assert len(p.osd) == n + 1


def test_frame_text_only_for_constant_fps(qapp):
    r = ReviewPlayer(FakePlayer())
    r.set_frame_info(30.0, vfr=False)
    assert r.frame_text(1_000) == "프레임 30"
    r.set_frame_info(30.0, vfr=True)
    assert r.frame_text(1_000) == ""
    r.set_frame_info(None, vfr=False)
    assert r.frame_text(1_000) == ""


def test_thresholds_and_normalize(qapp):
    p = FakePlayer()
    r = ReviewPlayer(p)
    r.set_thresholds({"recheck": {"pre_roll_sec": 5, "post_roll_sec": 20, "min_views_direct_likely": 3}})
    assert (r.pre_roll_ms, r.post_roll_ms, r.min_views) == (5_000, 20_000, 3)
    assert r.window_for(10_000, 12_000) == (5_000, 32_000)
    assert r.set_audio_normalize(True) is True and p.af is True and r.normalize() is True


def test_detach_stops_tracking(qapp):
    p = FakePlayer()
    r = ReviewPlayer(p)
    r.reset()
    r.set_window(0, 10_000)
    r.detach()
    p.run(0, 5_000)
    assert r.current_pass().ranges == []
    r.detach()   # 두 번 불러도 됨


def test_controls_and_shortcuts_build(qapp):
    p = FakePlayer()
    r = ReviewPlayer(p)
    host = QWidget()
    scs = r.install_shortcuts(host)
    assert [s.key().toString() for s in scs] == ["R", "[", "]"]
    c = r.controls(host)
    r.reset()
    r.set_window(0, 1_000)
    r.toggle_loop()
    assert c.loop_btn.isChecked()
    p.run(0, 1_000)
    assert "100%" in c.cov_label.text()
    r.speed_up()
    assert c.speed_box.currentData() == 1.25
    host.deleteLater()


def test_missing_player_methods_do_not_crash(qapp):
    class Bare(QObject):
        positionChanged = Signal(int)

        def set_ab_loop(self, a, b):
            pass

        def seek(self, ms, exact=True):
            pass

        def play(self):
            pass

        def set_speed(self, x):
            pass

        def position_ms(self):
            return 0
    r = ReviewPlayer(Bare())
    r.reset()
    r.set_window(0, 1_000)
    r.set_osd_text("x")
    assert r.start_loop() is True
    assert r.set_audio_normalize(True) is False
    assert r.audible()


# ---------------------------------------------------------------------------- 진짜 MpvPlayer (있을 때만)

def _wait(qapp, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return bool(cond())


def test_real_mpv_loop_and_heard(qapp, tmp_path):
    from app import paths
    from app import player as player_mod
    mod, err = player_mod._load_mpv_module()
    if mod is None:
        pytest.skip(f"libmpv 없음: {err}")
    ffmpeg = paths.find_tool("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg 없음")
    out = str(tmp_path / "합성.mp4")
    cmd = [ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=3",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", out]
    r0 = subprocess.run(cmd, capture_output=True, timeout=60)
    if r0.returncode != 0:
        pytest.skip("합성 영상 실패")
    p = player_mod.MpvPlayer()
    if not p.available():
        p.close_player()
        pytest.skip("mpv 생성 실패")
    try:
        assert p.load(out)
        assert _wait(qapp, lambda: p.is_loaded() and p.duration_ms() > 0)
        r = ReviewPlayer(p)
        r.reset()
        r.set_window(500, 2_000)
        assert r.start_loop()
        assert p.ab_loop() == (500, 2_000)
        assert _wait(qapp, lambda: r.current_coverage() > 0.3, 6.0), r.current_pass()
        r.stop_loop()
        assert p.ab_loop() == (None, None)
        r.detach()
    finally:
        p.close_player()
