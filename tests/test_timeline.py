"""타임라인 (app/ui/timeline.py) — 화면 없이 (QT_QPA_PLATFORM=offscreen). 합성 데이터만.

앞부분은 vtubeadmirer/clipslicer@07d13cc tests/test_timeline_zoom.py 를 옮긴 것이다(초 → ms, ClipProject → set_* API).
옮기지 않은 것: test_nudge_marker_by_frames (MainWindow 의 마커 미세 이동 — 타임라인이 마커를 고치지 않으므로 대상 없음).
뒷부분은 트랙(구간·밀도·파형·마커)·캐시·좌표·휠·클릭 테스트.
"""
import math
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.ui.timeline import Timeline, buffer_ranges, fmt_timecode  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def make(qapp, duration_ms=600_000, fps=30.0, width=1000):
    w = Timeline()
    w.resize(width, w.height())
    w.set_duration(duration_ms)
    w.set_fps(fps)
    return w


def seg(s, e, cat="just_chatting"):
    return {"start_ms": s, "end_ms": e, "category": cat, "confidence": "certain", "rationale": "합성",
            "active_chat": None, "start_signal": None, "long_silence_reviewed": None, "split_from": None,
            "merged_from": [], "out_of_media": False}


# ---------------------------------------------------------------------------- ClipSlicer test_timeline_zoom.py 에서 옮김
def test_fmt_timecode():
    assert fmt_timecode(0, 30) == "00:00:00"
    assert fmt_timecode(83_500, 30) == "01:23:15"
    assert fmt_timecode(3_600_000 + 1000 / 30, 30) == "1:00:00:01"
    assert fmt_timecode(1000, 59.94) == "00:01:00"      # 60fps 계열은 60 프레임으로 센다
    assert fmt_timecode(-3000, 0) == "00:00:00"


def test_zoom_down_to_frames(qapp):
    w = make(qapp)
    assert math.isclose(w.min_view_len(), 15 / 30 * 1000)
    for _ in range(60):
        w.zoom(2.0)
    assert math.isclose(w.view_len, 500) and math.isclose(w.zoom_factor(), 1200)
    assert w.zoom_slider.value() == w.ZOOM_STEPS            # 끝까지 확대 = 막대 끝
    w.zoom_reset()
    assert w.view_len == 600_000 and w.zoom_slider.value() == 0
    w.zoom_slider.setValue(w.ZOOM_STEPS // 2)              # 로그 눈금: 가운데 ≈ √1200 배
    assert abs(w.zoom_factor() - math.sqrt(1200)) / math.sqrt(1200) < 0.02
    assert abs(w.zoom_slider.value() - w.ZOOM_STEPS // 2) <= 1


def test_zoom_to_segment(qapp):
    w = make(qapp)
    w.set_segments([seg(0, 100_000), seg(100_000, 160_000, "game"), seg(160_000, 600_000)])
    w.set_position(130_000)
    assert w.zoom_to_segment()
    assert w.view_start < 100_000 and w.view_start + w.view_len > 160_000 and w.view_len < 70_000
    assert w.zoom_to_segment(5_000) and w.view_start == 0.0


def test_snap_to_markers_and_boundaries(qapp):
    w = make(qapp)
    w.set_segments([seg(0, 20_000), seg(20_000, 600_000, "game")])
    w.set_markers("labels", [(10_000, "reaction"), (12_000, "recheck")])
    w.zoom_to_range(0, 100_000, pad=0)                     # 1000px = 100초 → 8px = 0.8초
    assert w.snapped(10_500) == (10_000, 10_000)           # 마커에 붙음
    assert w.snapped(10_500, enabled=False) == (10_500, None)   # Shift = 자석 끔
    assert w.snapped(19_600) == (20_000, 20_000)           # 구간 경계에 붙음
    assert w.snapped(15_000) == (15_000, None)
    w.clear_markers("labels")
    assert w.snapped(10_500) == (10_500, None)
    w.zoom_to_range(0, 10_000, pad=0)                      # 더 확대하면 허용 범위가 줄어든다 (0.08초)
    assert w.snapped(19_900) == (19_900, None)
    assert w.snapped(20_050) == (20_000, 20_000)


# ---------------------------------------------------------------------------- 좌표 · 보기 범위
@pytest.mark.parametrize("view", [(0, 600_000), (123_456, 130_000), (599_000, 600_000)])
def test_coordinate_round_trip(qapp, view):
    w = make(qapp)
    w.set_view(*view)
    a, b = w.view_range()
    for t in (a, (a + b) // 2, b, a + 1):
        x = w.t_to_x(t)
        back = w.x_to_t(x)
        assert abs(back - t) <= 1                          # 정수 ms 반올림만큼
    for x in (0, 1, 250, 999, 1000):
        t = w.x_to_t(x)
        assert abs(w.t_to_x(t) - x) <= max(1.0, 1000 / w.view_len)   # 1ms 가 1px 보다 넓으면 그만큼


def test_x_to_t_clamps_and_px_tol(qapp):
    w = make(qapp)
    assert w.x_to_t(-50) == 0 and w.x_to_t(5000) == 600_000
    assert math.isclose(w.px_tol_ms(10), 6000)            # 1000px = 600초 → 10px = 6초


def test_zoom_bounds(qapp):
    w = make(qapp)
    w.zoom(0.1)                                           # 전체보다 더 축소할 수 없다
    assert w.view_range() == (0, 600_000) and w.zoom_factor() == 1.0
    for _ in range(100):
        w.zoom(3.0, 599_999)                              # 끝에 붙여 확대: 범위가 영상 밖으로 나가지 않음
    assert math.isclose(w.view_len, 500)
    assert w.view_range()[1] <= 600_000 and w.view_start >= 0
    w.set_view(-10_000, 5_000)                            # 영상 앞으로 넘친 범위는 0 으로
    assert w.view_range() == (0, 15_000)
    w.set_view(590_000, 700_000)
    assert w.view_range() == (490_000, 600_000)           # 영상 끝으로 넘친 범위는 길이를 지키며 앞으로
    w.set_fps(None)                                       # 가변 프레임률: 프레임 눈금 없음, 최소 보기 길이는 30fps 기준
    assert math.isclose(w.min_view_len(), 500)
    w.set_fps(60)
    assert math.isclose(w.min_view_len(), 250)            # 15프레임 = 0.25초 (하한과 같음)


def test_zoom_keeps_anchor(qapp):
    w = make(qapp)
    anchor = 200_000
    x_before = w.t_to_x(anchor)
    w.zoom(4.0, anchor)
    assert abs(w.t_to_x(anchor) - x_before) < 0.5          # 마우스 아래 시각이 제자리
    assert math.isclose(w.view_len, 150_000)


def test_view_changed_signal_and_scrollbar(qapp):
    w = make(qapp)
    got = []
    w.viewChanged.connect(lambda a, b: got.append((a, b)))
    w.set_view(100_000, 160_000)
    assert got[-1] == (100_000, 160_000)
    assert w.scroll.isEnabled() and w.scroll.value() == 100_000 and w.scroll.pageStep() == 60_000
    w.scroll.setValue(200_000)                            # 스크롤바로 옮기기
    assert w.view_range() == (200_000, 260_000) and got[-1] == (200_000, 260_000)
    n = len(got)
    w.set_view(200_000, 260_000)                          # 같은 범위면 신호 없음
    assert len(got) == n


def test_follow_position(qapp):
    w = make(qapp)
    w.set_view(0, 60_000)
    w.set_position(30_000)
    assert w.view_range() == (0, 60_000)
    w.set_position(90_000)                                # 보이는 범위 밖 → 따라감 (앞 10% 여백)
    a, b = w.view_range()
    assert a <= 90_000 <= b and b - a == 60_000
    w.follow = False
    w.set_position(300_000)
    assert w.view_range() == (a, b)


# ---------------------------------------------------------------------------- 트랙
def test_buffer_ranges():
    assert buffer_ranges([0, 100_000, 105_000, 600_000], 600_000, 10_000) == [(90_000, 115_000)]
    assert buffer_ranges([5_000, 300_000], 600_000, 10_000) == [(0, 15_000), (290_000, 310_000)]
    assert buffer_ranges([300_000], 600_000, 0) == []


def test_marker_tracks(qapp):
    w = make(qapp)
    h0 = w.height()
    w.set_markers("engine", [(5_000, "engine")])
    w.set_markers("labels", [(30_000, "reaction"), (10_000, "recheck"), (20_000, "unconfirmed")])
    w.set_markers("custom", [(1, "새 종류")], label="합성 트랙")
    names = [n for n, _ in w.track_rects()]
    assert names == ["ruler", "segments", "density", "waveform", "marks:labels", "marks:engine", "marks:custom"]
    assert w.height() > h0
    assert w.markers("labels") == [(10_000, "recheck"), (20_000, "unconfirmed"), (30_000, "reaction")]  # 정렬
    h1 = w.height()
    w.set_markers("labels", [])                           # 빈 목록: 트랙은 남는다
    assert "marks:labels" in [n for n, _ in w.track_rects()] and w.height() == h1
    w.clear_markers("labels")
    assert "marks:labels" not in [n for n, _ in w.track_rects()] and w.height() < h1
    w.clear_markers("없는 트랙")                           # 없는 이름은 조용히 넘어간다
    rects = [r for _, r in w.track_rects()]
    for a, b in zip(rects, rects[1:]):
        assert a.bottom() <= b.top()                      # 겹치지 않음


def test_segments_accept_record_wrapper_and_skip_bad(qapp):
    w = make(qapp)
    w.set_segments([{"kind": "segment", "entity_id": "x", "body": seg(0, 50_000)},
                    seg(50_000, 600_000, None),           # 아직 안 매긴 시간
                    {"start_ms": 10, "end_ms": 5},        # 뒤집힌 범위 → 버림
                    {"category": "game"}])                # 시각 없음 → 버림
    assert w._segments == [(0, 50_000, "just_chatting"), (50_000, 600_000, None)]
    assert w._buffers == [(40_000, 60_000)]
    w.grab()


def _full_data(w, duration):
    w.set_segments([seg(0, duration // 3), seg(duration // 3, 2 * duration // 3, "game"),
                    seg(2 * duration // 3, duration, None)])
    w.set_density([(i * 7919) % 50 for i in range(duration // 1000)], 1000)
    w.set_waveform([((i * 104729) % 100) / 100 for i in range(4000)], duration)
    w.set_markers("labels", [(i * 37_000, "reaction" if i % 3 else "recheck") for i in range(duration // 37_000)])
    w.set_markers("engine", [(i * 11_000, "engine") for i in range(duration // 11_000)])
    w.set_markers("anchors", [(duration // 2, "anchor")])


@pytest.mark.parametrize("fps", [30.0, None])
def test_offscreen_render_all_zoom_levels(qapp, fps):
    w = make(qapp, fps=fps)
    w.grab()                                              # 데이터 없이
    _full_data(w, 600_000)
    w.set_position(123_456)
    w.set_loop_range(120_000, 130_000)
    for view in [(0, 600_000), (100_000, 160_000), (123_000, 124_000), (123_000, 123_500)]:
        w.set_view(*view)
        pix = w.grab()
        assert not pix.isNull() and pix.width() == 1000
    empty = Timeline()
    empty.resize(400, empty.height())
    assert not empty.grab().isNull()                      # 영상 없음 안내만


def test_cache_not_rebuilt_on_position_only(qapp):
    w = make(qapp)
    _full_data(w, 600_000)
    w.grab()
    builds = w.cache_builds
    assert builds >= 1
    for t in range(0, 600_000, 7_000):                    # 전체 보기: 재생 위치만 움직임
        w.set_position(t)
        w.grab()
    assert w.cache_builds == builds
    w.set_view(0, 60_000)                                 # 보이는 범위가 바뀌면 다시 만든다
    w.grab()
    assert w.cache_builds == builds + 1
    w.set_markers("labels", [(1_000, "reaction")])        # 데이터가 바뀌면 다시 만든다
    w.grab()
    assert w.cache_builds == builds + 2
    w.set_loop_range(1_000, 2_000)                        # 반복 범위는 캐시 밖
    w.grab()
    assert w.cache_builds == builds + 2


def test_position_update_is_fast(qapp):
    """02 9장 '재생 위치 갱신 8ms 이하'. offscreen 에서는 show+repaint 가 그리지 않아 grab()(위젯 전체를 그림 —
    실제 창의 '옛 선·새 선 둘레만' 보다 많이 그린다)으로 잰다."""
    w = make(qapp, duration_ms=8 * 3_600_000, width=1600)
    _full_data(w, 8 * 3_600_000)
    w.grab()
    t0 = time.perf_counter()
    n = 200
    for i in range(n):
        w.set_position(i * 60_000)
        w.grab()
    avg_ms = (time.perf_counter() - t0) / n * 1000
    t1 = time.perf_counter()
    w.set_view(3_600_000, 3_600_000 + 600_000)
    w.grab()
    rebuild_ms = (time.perf_counter() - t1) * 1000
    print(f"재생 위치 갱신 평균 {avg_ms:.2f}ms, 범위 바뀜(캐시 다시 만듦) {rebuild_ms:.1f}ms")
    assert avg_ms < 8.0


# ---------------------------------------------------------------------------- 마우스 · 휠
def _mouse(w, kind, x, y, mods=None):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    mods = mods if mods is not None else Qt.NoModifier
    btn = Qt.LeftButton
    btns = Qt.NoButton if kind == "release" else Qt.LeftButton
    t = {"press": QMouseEvent.MouseButtonPress, "move": QMouseEvent.MouseMove,
         "release": QMouseEvent.MouseButtonRelease}[kind]
    p = QPointF(x, y)
    ev = QMouseEvent(t, p, w.mapToGlobal(p), Qt.NoButton if kind == "move" else btn, btns, mods)
    QApplication.sendEvent(w, ev)


def _wheel(w, x, dy, dx=0, mods=None):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    mods = mods if mods is not None else Qt.NoModifier
    p = QPointF(x, 30)
    ev = QWheelEvent(p, w.mapToGlobal(p), QPoint(0, 0), QPoint(dx, dy), Qt.NoButton, mods,
                     Qt.NoScrollPhase, False)
    QApplication.sendEvent(w, ev)


def test_click_drag_emits_seek_with_snap(qapp):
    from PySide6.QtCore import Qt
    w = make(qapp)
    w.set_markers("labels", [(60_000, "reaction")])
    got = []
    w.seekRequested.connect(got.append)
    _mouse(w, "press", 500, 30)
    assert got[-1] == 300_000 and w.scrubbing
    _mouse(w, "move", 250, 30)
    assert got[-1] == 150_000
    _mouse(w, "move", 101, 30)                             # 60초 마커에서 1px → 붙음
    assert got[-1] == 60_000
    _mouse(w, "move", 101, 30, Qt.ShiftModifier)           # Shift = 자석 끔
    assert got[-1] == 60_600
    _mouse(w, "release", 750, 30)
    assert got[-1] == 450_000 and not w.scrubbing
    n = len(got)
    _mouse(w, "press", 500, w.height() - 5)                # 아래 막대 자리는 탐색하지 않음
    assert len(got) == n


def test_wheel_zoom_at_mouse_and_scroll(qapp):
    from PySide6.QtCore import Qt
    w = make(qapp)
    t_under = w.x_to_t(250)
    _wheel(w, 250, 120)                                   # 휠 = 마우스 위치 기준 확대
    assert math.isclose(w.zoom_factor(), 1.25, rel_tol=1e-6)
    assert abs(w.x_to_t(250) - t_under) <= 1
    _wheel(w, 250, -120, mods=Qt.ControlModifier)         # Ctrl+휠도 확대·축소
    assert math.isclose(w.zoom_factor(), 1.0, rel_tol=1e-6)
    w.set_view(100_000, 200_000)
    _wheel(w, 500, -120, mods=Qt.ShiftModifier)           # Shift+휠 = 가로 스크롤 (뒤로)
    assert w.view_range() == (115_000, 215_000)
    _wheel(w, 500, 0, dx=120)                             # 가로 휠(터치패드) = 스크롤
    assert w.view_range() == (100_000, 200_000)
