"""여러 트랙 타임라인 (작업대 아래쪽, docs/design/02-architecture.md 7-3·9장·12-2).

트랙(위 → 아래): 눈금 · 활동 구간(분류 색, 경계 완충 음영) · 채팅 밀도 막대 · 파형 · 마커 트랙들(이름별) · 재생 위치 선.
시각 축은 호출하는 쪽이 정한다(작업대는 VOD 시각). 모든 공개 시각 값은 정수 ms.

출처: vtubeadmirer/clipslicer@07d13cc app/timeline.py 에서 좌표 변환(x_to_t · t_to_x · px_tol_sec),
보기 범위(_set_view · zoom · zoom_to_range · min_view_len · max_zoom), 눈금(_draw_ruler, 프레임 단위 눈금 · fmt_timecode),
자석(snapped), 휠 처리, 확대 막대(로그 눈금)·스크롤바 동기화를 가져와 초 → 정수 ms 로 바꿨다.
그리기는 새로 썼다(02 12-2): 트랙 스택, 분류 색, 완충 음영, 정적 트랙 QPixmap 캐시.
ClipSlicer 의 고정 높이 THUMB_H=64 · 순번 색 seg_color(i) · 썸네일 띠 · PreviewPopup 은 쓰지 않는다.

성능 규칙 (02 9장): 정적 트랙은 QPixmap 한 장에 그려 두고 데이터나 보이는 범위·크기가 바뀔 때만 다시 만든다.
재생 위치가 바뀌면 캐시 그림을 그대로 붙이고 재생 위치 선·호버 선만 그린다(옛 선·새 선 둘레만 다시 그림).
마커 하나 더하기·빼기·바꾸기(add_marker · remove_marker · update_marker)는 캐시 그림의 그 마커 둘레만 다시 칠하고
update(rect) 한다(14 6-1 의 3번). 트랙 전체를 바꿀 때만 set_markers 를 쓴다.

휠 (08 D-09 7, ClipSlicer 습관): 수정자 없음 · Shift · 가로 휠(터치패드) = 가로 스크롤,
Ctrl+휠 · Alt+휠 = 마우스 위치 기준 확대·축소.
클릭·끌기 = seekRequested. 마커·구간 경계 SNAP_PX 안이면 그 자리에 붙는다(Shift 를 누르면 자석 끔).
"""
from __future__ import annotations

import bisect
import math
from typing import Iterable, Sequence

from PySide6.QtCore import QLineF, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QScrollBar, QSizePolicy, QSlider, QToolButton, QToolTip, QVBoxLayout, QWidget

from app.core.segments import record_buffers
from app.core.timecode import fmt_time
from app.i18n import tr

# ---------------------------------------------------------------------------- 크기
RULER_H = 18
SEG_H = 22
DENS_H = 26
WAVE_H = 30
MARK_H = 16
TRACK_GAP = 1
BAR_H = 20              # 아래 막대(스크롤바 · 확대 막대)
SNAP_PX = 8             # 이 픽셀 안의 마커·경계에 자석처럼 붙는다 (ClipSlicer SNAP_PX)
MIN_VIEW_FRAMES = 15    # 가장 크게 확대했을 때 보이는 프레임 수 (ClipSlicer)
MIN_VIEW_MS = 250       # 가장 작은 보기 길이 하한 (ClipSlicer 0.25초)
DEFAULT_FPS = 30.0      # fps 를 모를 때 최소 보기 길이 계산용 (프레임 눈금은 그리지 않는다)
DEFAULT_BUFFER_MS = 10_000   # 경계 완충 시작값 (guide-config segment.boundary_buffer_sec, app/core/segments.py 와 같음)

# ---------------------------------------------------------------------------- 색
BG = QColor(24, 24, 28)
TRACK_BG = QColor(31, 31, 36)
RULER_BG = QColor(36, 36, 42)
TEXT_DIM = QColor(150, 150, 160)
TEXT_FAINT = QColor(110, 110, 120)

# 활동 분류 색 (03 5-1). 표시명은 core/schema.py 가 생기면 그쪽을 쓴다 — 지금은 03 5-1 표를 그대로 옮김.
CATEGORY_COLORS: dict[str | None, QColor] = {
    "just_chatting": QColor(66, 165, 245),
    "game": QColor(102, 187, 106),
    "singing": QColor(171, 71, 188),
    "other": QColor(255, 167, 38),
    "undeterminable": QColor(120, 120, 128),
    None: QColor(70, 70, 78),        # 아직 안 매긴 시간
}
CATEGORY_NAMES: dict[str | None, str] = {
    "just_chatting": "저스트채팅",
    "game": "게임",
    "singing": "노래",
    "other": "기타",
    "undeterminable": "판단 불가",
    None: "미분류",
}

# 마커 트랙: 알려진 이름의 표시명과 위→아래 순서. 그 밖의 이름은 처음 set_markers 한 순서로 뒤에 붙는다.
TRACK_NAMES: dict[str, str] = {
    "silence": "침묵(재검토)",
    "labels": "반응 라벨",
    "engine": "엔진",
    "primary": "잠긴 1차 라벨",       # 분석 세션만 (08 D-09 5). 라벨링 세션에는 이 트랙이 생기지 않는다
    "anchors": "앵커",
}
TRACK_ORDER = ("silence", "labels", "primary", "engine", "anchors")

# 마커 종류 → (색, 속이 빈 표시). 모르는 종류는 DEFAULT_MARK. set_marker_style 로 더하거나 바꾼다.
DEFAULT_MARK = (QColor(255, 167, 38), False)
MARK_STYLES: dict[str, tuple[QColor, bool]] = {
    "reaction": (QColor(255, 167, 38), False),
    "recheck": (QColor(255, 167, 38), True),          # 재확인 필요는 속이 빈 표시 (02 7-3)
    "unconfirmed": (QColor(150, 150, 160), False),    # 반응 미확인
    "engine": (QColor(38, 198, 218), False),
    "engine_weak": (QColor(38, 198, 218), True),
    "primary": (QColor(186, 104, 200), False),       # 잠긴 1차 반응 라벨(보라, 엔진의 청록과 구분)
    "primary_weak": (QColor(186, 104, 200), True),   # 잠긴 1차의 반응 미확인·재확인 모자람
    "anchor": (QColor(255, 213, 79), False),
    "silence": (QColor(176, 190, 197), True),
    "selected": (QColor(255, 255, 255), False),
}


def fmt_timecode(ms: float, fps: float) -> str:
    """프레임 단위 시각: 'MM:SS:FF' (1시간 넘으면 'H:MM:SS:FF'). ClipSlicer fmt_timecode 를 ms 입력으로."""
    fps = fps if fps and fps > 0 else DEFAULT_FPS
    total = int(round(max(ms, 0.0) / 1000.0 * fps))
    nf = int(round(fps))
    frames = total % nf
    secs = total // nf
    h, rem = divmod(secs, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}:{frames:02d}" if h else f"{m:02d}:{sec:02d}:{frames:02d}"


def buffer_ranges(boundaries: Sequence[int], duration_ms: int, buffer_ms: int) -> list[tuple[int, int]]:
    """안쪽 경계마다 [b - buffer, b + buffer) 를 영상 범위로 자르고, 겹치거나 맞닿으면 합친다(모든 경계 = sha256-tau-v1 규칙).
    타임라인 음영은 이제 분류가 바뀌는 경계만 쓴다(_segment_buffers). 이 함수는 경계 목록만 있을 때를 위해 남긴다."""
    out: list[tuple[int, int]] = []
    if buffer_ms <= 0:
        return out
    for b in sorted(set(boundaries)):
        if b <= 0 or b >= duration_ms:
            continue
        lo, hi = max(0, b - buffer_ms), min(duration_ms, b + buffer_ms)
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _seg_fields(d: dict) -> tuple[int, int, str | None] | None:
    """segment dict (03 6-5 body, 또는 {'body': …} 로 감싼 기록) → (start_ms, end_ms, category). 못 읽으면 None."""
    body = d.get("body") if isinstance(d.get("body"), dict) else d
    try:
        s, e = int(body["start_ms"]), int(body["end_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    if e <= s:
        return None
    return s, e, body.get("category")


class Timeline(QWidget):
    """여러 트랙 타임라인. 시각 = 정수 ms (축은 호출하는 쪽이 정함)."""
    seekRequested = Signal(int)          # 클릭·끌기 위치 (자석 적용 뒤)
    viewChanged = Signal(int, int)       # 보이는 범위 (start_ms, end_ms)

    ZOOM_STEPS = 1000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0
        self._position = 0
        self.view_start = 0.0            # ms (확대 중 부드럽게 움직이도록 float)
        self.view_len = 0.0
        self.follow = True               # 재생 위치가 보이는 범위를 벗어나면 따라간다
        self._fps: float | None = None   # 고정 프레임률일 때만. None 이면 프레임 눈금 없음 (7-3 '가변 프레임이면 끔')
        self._frame_origin_ms = 0
        # 데이터
        self._segments: list[tuple[int, int, str | None]] = []
        self._buffer_ms = DEFAULT_BUFFER_MS
        self._buffers: list[tuple[int, int]] = []
        self._density: list[int] = []
        self._density_bucket = 0
        self._density_max = 0
        self._wave: list[float] = []
        self._wave_span = 0
        self._tracks: dict[str, tuple[list[int], list[str]]] = {}   # 이름 → (정렬된 시각, 종류)
        self._track_labels: dict[str, str] = {}
        self._track_seq: list[str] = []      # 처음 나온 순서
        self._kind_times: dict[str, dict[str, list[int]]] = {}   # 촘촘한 트랙 그리기용 색인 (이름 → 종류 → 시각)
        self._snap_points: list[int] = []
        self._loop: tuple[int, int] | None = None
        self._styles = dict(MARK_STYLES)
        # 상호작용
        self.scrubbing = False
        self.hover_x = -1
        self.snap_t: int | None = None
        # 캐시
        self._data_rev = 0
        self._cache: QPixmap | None = None
        self._cache_key = None
        self.cache_builds = 0            # 테스트·벤치용: 정적 트랙을 다시 그린 횟수
        self.cache_patches = 0           # 테스트·벤치용: 마커 부분 갱신으로 캐시 그림 일부만 다시 칠한 횟수
        self.last_dirty_rects: list[QRect] = []   # 마지막 부분 갱신의 사각형 (위젯 좌표)
        self._last_head_x: int | None = None

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._build_bar()
        self._update_height()

    # ------------------------------------------------------------------ 아래 막대 (ClipSlicer TimelineWidget)
    def _build_bar(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addStretch(1)
        self._bar = QWidget(self)
        self._bar.setFixedHeight(BAR_H)
        bar = QHBoxLayout(self._bar)
        bar.setContentsMargins(0, 2, 0, 0)
        bar.setSpacing(4)
        self.scroll = QScrollBar(Qt.Horizontal, self._bar)
        self.scroll.setRange(0, 0)
        self.scroll.setFocusPolicy(Qt.NoFocus)
        bar.addWidget(self.scroll, 1)

        def tb(text, tip, fn):
            b = QToolButton(self._bar)
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False: fn())
            bar.addWidget(b)
            return b
        tb("−", tr("축소 (Ctrl+-)"), self.zoom_out)
        self.zoom_slider = QSlider(Qt.Horizontal, self._bar)
        self.zoom_slider.setRange(0, self.ZOOM_STEPS)
        self.zoom_slider.setFixedWidth(110)
        self.zoom_slider.setFocusPolicy(Qt.NoFocus)
        self.zoom_slider.setToolTip(tr("타임라인 확대 (휠 · Ctrl+= / Ctrl+-). 끝까지 당기면 프레임 단위"))
        self.zoom_slider.valueChanged.connect(lambda v: self._on_slider(v))
        bar.addWidget(self.zoom_slider)
        tb("+", tr("확대 (Ctrl+=)"), self.zoom_in)
        tb(tr("전체"), tr("전체 보기 (Ctrl+0)"), self.zoom_reset)
        lay.addWidget(self._bar)
        self._syncing = False
        self.scroll.valueChanged.connect(lambda v: self._on_scroll(v))

    def _sync_bar(self):
        if self._syncing:
            return
        self._syncing = True
        try:
            d = self._duration
            self.zoom_slider.blockSignals(True)
            mz = self.max_zoom() if d > 0 else 1.0
            v = math.log(max(self.zoom_factor(), 1.0)) / math.log(mz) if mz > 1.0 else 0.0
            self.zoom_slider.setValue(int(round(v * self.ZOOM_STEPS)))
            self.zoom_slider.setEnabled(d > 0)
            self.zoom_slider.blockSignals(False)
            if d <= 0 or self.view_len >= d - 1e-6:
                self.scroll.setEnabled(False)
                self.scroll.setRange(0, 0)
            else:
                self.scroll.setEnabled(True)
                page = int(self.view_len)
                self.scroll.setRange(0, max(0, d - page))
                self.scroll.setPageStep(max(1, page))
                self.scroll.setSingleStep(max(1, page // 10))
                self.scroll.setValue(int(self.view_start))
        finally:
            self._syncing = False

    def _on_slider(self, v: int):
        """확대 막대 (로그 눈금): 0 = 전체, 끝 = 약 15프레임."""
        if self._syncing or self._duration <= 0:
            return
        self.set_zoom_factor(self.max_zoom() ** (v / self.ZOOM_STEPS))

    def _on_scroll(self, v: int):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.scroll_to(v)
        finally:
            self._syncing = False

    # ------------------------------------------------------------------ 트랙 배치
    def _marker_track_names(self) -> list[str]:
        known = [n for n in TRACK_ORDER if n in self._tracks]
        rest = [n for n in self._track_seq if n in self._tracks and n not in TRACK_ORDER]
        return known + rest

    def track_rects(self) -> list[tuple[str, QRectF]]:
        """(트랙 이름, 칸) 위 → 아래. 이름: 'ruler' · 'segments' · 'density' · 'waveform' · 'marks:<이름>'."""
        w = float(self.width())
        y = 0.0
        out = []
        for name, h in self._fixed_tracks():
            out.append((name, QRectF(0, y, w, h)))
            y += h + TRACK_GAP
        for n in self._marker_track_names():
            out.append(("marks:" + n, QRectF(0, y, w, MARK_H)))
            y += MARK_H + TRACK_GAP
        return out

    def _fixed_tracks(self) -> list[tuple[str, int]]:
        """눈금 + 보이는 고정 칸(구간·밀도·파형). 기본은 모두 보인다."""
        hidden = getattr(self, "_hidden_fixed", frozenset())
        return [(n, h) for n, h in (("ruler", RULER_H), ("segments", SEG_H), ("density", DENS_H), ("waveform", WAVE_H))
                if n not in hidden]

    def set_fixed_tracks_visible(self, *, segments: bool = True, density: bool = True, waveform: bool = True) -> None:
        """고정 칸(구간·채팅 밀도·파형)을 숨기거나 보인다. 눈금은 늘 보인다. 기본값(모두 보임)은 작업대와 같다.
        방송 분석 화면(app/ui/analysis_view)처럼 마커 트랙만 쓰는 화면이 빈 칸을 숨길 때 쓴다."""
        hidden = frozenset(n for n, on in (("segments", segments), ("density", density), ("waveform", waveform))
                           if not on)
        if hidden == getattr(self, "_hidden_fixed", frozenset()):
            return
        self._hidden_fixed = hidden
        self._update_height()
        self._data_changed()

    def _area_height(self) -> int:
        fixed = self._fixed_tracks()
        return (sum(h for _n, h in fixed) + len(self._tracks) * MARK_H
                + (len(fixed) + len(self._tracks)) * TRACK_GAP)

    def _update_height(self):
        self.setFixedHeight(self._area_height() + BAR_H)

    def _area_rect(self) -> QRect:
        return QRect(0, 0, self.width(), max(0, self.height() - BAR_H))

    # ------------------------------------------------------------------ 좌표 변환 (ClipSlicer, ms)
    @property
    def duration(self) -> int:
        return self._duration

    @property
    def position(self) -> int:
        return self._position

    def x_to_t(self, x: float) -> int:
        if self.view_len <= 0 or self.width() <= 0:
            return 0
        t = self.view_start + x / self.width() * self.view_len
        return int(round(max(0.0, min(float(self._duration), t))))

    def t_to_x(self, t: float) -> float:
        if self.view_len <= 0:
            return 0.0
        return (t - self.view_start) / self.view_len * self.width()

    def px_tol_ms(self, px: float) -> float:
        return px / max(self.width(), 1) * self.view_len

    def view_range(self) -> tuple[int, int]:
        return int(round(self.view_start)), int(round(self.view_start + self.view_len))

    # ------------------------------------------------------------------ 보기 범위 (ClipSlicer)
    def zoom_factor(self) -> float:
        return self._duration / self.view_len if self.view_len > 0 else 1.0

    def min_view_len(self) -> float:
        """가장 크게 확대했을 때 보이는 길이: 약 15프레임 (최소 0.25초). fps 를 모르면 30fps 로 본다."""
        fps = self._fps or DEFAULT_FPS
        return min(float(self._duration), max(float(MIN_VIEW_MS), MIN_VIEW_FRAMES / fps * 1000.0))

    def max_zoom(self) -> float:
        return max(1.0, self._duration / max(self.min_view_len(), 1e-6))

    def _set_view(self, start: float, length: float):
        if self._duration <= 0:
            return
        length = max(min(length, float(self._duration)), self.min_view_len())
        start = max(0.0, min(start, self._duration - length))
        if abs(start - self.view_start) > 1e-6 or abs(length - self.view_len) > 1e-6:
            self.view_start, self.view_len = start, length
            self._sync_bar()
            self.update()
            a, b = self.view_range()
            self.viewChanged.emit(a, b)

    def set_view(self, start_ms: int, end_ms: int):
        self._set_view(float(start_ms), float(end_ms) - float(start_ms))

    def zoom(self, factor: float, anchor_ms: float | None = None):
        if self._duration <= 0 or factor <= 0:
            return
        if anchor_ms is None:
            anchor_ms = self._position
        new_len = self.view_len / factor
        frac = (anchor_ms - self.view_start) / self.view_len if self.view_len > 0 else 0.5
        self._set_view(anchor_ms - frac * new_len, new_len)

    def zoom_in(self):
        self.zoom(1.5, self._zoom_anchor())

    def zoom_out(self):
        self.zoom(1 / 1.5, self._zoom_anchor())

    def _zoom_anchor(self) -> float:
        """재생 위치가 보이면 그곳, 아니면 화면 가운데."""
        if self.view_start <= self._position <= self.view_start + self.view_len:
            return float(self._position)
        return self.view_start + self.view_len / 2

    def set_zoom_factor(self, factor: float):
        if self._duration <= 0:
            return
        factor = max(1.0, min(factor, self.max_zoom()))
        anchor = self._zoom_anchor()
        new_len = self._duration / factor
        frac = (anchor - self.view_start) / self.view_len if self.view_len > 0 else 0.5
        self._set_view(anchor - frac * new_len, new_len)

    def zoom_reset(self):
        self._set_view(0.0, float(self._duration))

    def zoom_to_range(self, a_ms: int, b_ms: int, pad: float = 0.05):
        """[a, b] 가 화면에 꽉 차게 (양옆 pad 비율 여백)."""
        if self._duration <= 0 or b_ms <= a_ms:
            return
        span = float(b_ms - a_ms)
        self._set_view(a_ms - span * pad, span * (1 + 2 * pad))

    def zoom_to_segment(self, t_ms: int | None = None) -> bool:
        """t(기본: 재생 위치)가 든 구간에 맞춰 확대."""
        t = self._position if t_ms is None else int(t_ms)
        starts = [s for s, _e, _c in self._segments]
        i = bisect.bisect_right(starts, t) - 1
        if i < 0 or not (self._segments[i][0] <= t < self._segments[i][1]):
            return False
        s, e, _ = self._segments[i]
        self.zoom_to_range(s, e)
        return True

    def scroll_to(self, start_ms: float):
        self._set_view(float(start_ms), self.view_len)

    # ------------------------------------------------------------------ 데이터 API
    def set_duration(self, ms: int):
        ms = max(0, int(ms))
        changed = ms != self._duration
        self._duration = ms
        self._position = min(self._position, ms)
        if changed:
            self.view_start, self.view_len = 0.0, float(ms)
            self._buffers = self._segment_buffers()
            self._data_changed()
            self._sync_bar()
            self.viewChanged.emit(0, ms)

    def set_fps(self, fps: float | None, origin_ms: int = 0):
        """고정 프레임률이면 fps, 가변 프레임률이거나 모르면 None (프레임 눈금을 끈다, 02 7-3).
        origin_ms = 축 시각에서 영상 파일 0 프레임의 자리 (작업대: 파일 시작의 VOD 시각)."""
        self._fps = float(fps) if fps and fps > 0 else None
        self._frame_origin_ms = int(origin_ms)
        self._set_view(self.view_start, self.view_len)   # 최소 보기 길이가 바뀔 수 있다
        self._data_changed()

    def set_position(self, ms: int):
        ms = int(ms)
        if ms == self._position:
            return
        self._position = ms
        if self.follow and self._duration > 0 and self.view_len < self._duration and not self.scrubbing:
            if ms < self.view_start or ms > self.view_start + self.view_len:
                self._set_view(ms - self.view_len * 0.1, self.view_len)   # 범위가 바뀌면 전체 다시 그림
                return
        self._update_head()

    def set_segments(self, segments: Iterable[dict], buffer_ms: int | None = None):
        """03 6-5 모양 dict 목록 (또는 {'body': …} 로 감싼 기록). 시간순으로 정렬해 그린다. buffer_ms = 경계 완충."""
        segs = [f for f in (_seg_fields(d) for d in segments) if f is not None]
        segs.sort(key=lambda f: (f[0], f[1]))
        self._segments = segs
        if buffer_ms is not None:
            self._buffer_ms = max(0, int(buffer_ms))
        self._buffers = self._segment_buffers()
        self._data_changed()

    def _segment_buffers(self) -> list[tuple[int, int]]:
        """완충 음영: 분류가 바뀌는 경계에만(08 D-10 1, core.segments.record_buffers 의 기본 규칙 — 표본 뽑기와 같다).
        빈틈·안 매긴 구간(category None)과 맞닿은 경계는 전환으로 본다. 영상 처음·끝은 경계가 아니다."""
        if self._duration <= 0:
            return []
        return record_buffers([{"start_ms": s, "end_ms": e, "category": c} for s, e, c in self._segments],
                              self._duration, self._buffer_ms)

    def _boundaries(self) -> list[int]:
        out = set()
        for s, e, _ in self._segments:
            out.add(s)
            out.add(e)
        return sorted(out)

    def set_density(self, counts: Sequence[int], bucket_ms: int):
        """채팅 밀도: counts[i] = [i*bucket_ms, (i+1)*bucket_ms) 의 채팅 수."""
        self._density = [max(0, int(c)) for c in counts] if bucket_ms > 0 else []
        self._density_bucket = int(bucket_ms) if bucket_ms > 0 else 0
        self._density_max = max(self._density, default=0)
        self._data_changed()

    def set_waveform(self, values: Sequence[float], span_ms: int):
        """파형: values(0~1)가 [0, span_ms) 를 고르게 덮는다."""
        self._wave = [min(1.0, max(0.0, float(v))) for v in values] if span_ms > 0 else []
        self._wave_span = int(span_ms) if span_ms > 0 else 0
        self._data_changed()

    def set_markers(self, name: str, items: Iterable[tuple[int, str]], label: str | None = None):
        """이름별 마커 트랙. items = [(ms, kind)]. 빈 목록이면 빈 트랙이 남는다(clear_markers 로 지움)."""
        pairs = sorted((int(t), str(k)) for t, k in items)
        new_track = name not in self._tracks
        self._kind_times.pop(name, None)
        self._tracks[name] = ([t for t, _ in pairs], [k for _, k in pairs])
        if label is not None:
            self._track_labels[name] = label
        if name not in self._track_seq:
            self._track_seq.append(name)
        if new_track:
            self._update_height()
        self._data_changed()

    def clear_markers(self, name: str):
        if name in self._tracks:
            del self._tracks[name]
            self._kind_times.pop(name, None)
            self._update_height()
            self._data_changed()

    def markers(self, name: str) -> list[tuple[int, str]]:
        ts, ks = self._tracks.get(name, ([], []))
        return list(zip(ts, ks))

    # ------------------------------------------------------------------ 마커 부분 갱신 (14 6-1 의 3번)
    # 라벨 한 줄 저장·지우기·고치기 때 set_markers(전체 정렬 + 정적 트랙 전체 다시 그리기) 대신 쓴다.
    # 정적 캐시가 지금 보기와 맞으면 그 마커 둘레 사각형만 캐시 그림에 다시 칠하고 update(rect) 한다.
    # 캐시가 없거나 맞지 않거나, 촘촘함(dense) 판정이 바뀌거나, 새 트랙이면 전체 다시 그리기로 물러난다.
    def add_marker(self, name: str, t_ms: int, kind: str, label: str | None = None) -> None:
        """마커 하나 더함. 같은 (시각, 종류) 가 이미 있어도 하나 더 넣는다(set_markers 와 같음)."""
        t, k = int(t_ms), str(kind)
        if name not in self._tracks:
            self.set_markers(name, [(t, k)], label)
            return
        if label is not None:
            self._track_labels[name] = label
        ts, ks = self._tracks[name]
        was_dense = self._is_dense(name)
        i = self._insert_pos(ts, ks, t, k)
        ts.insert(i, t)
        ks.insert(i, k)
        self._index_add(name, t, k)
        self._snap_add(t)
        self._marks_changed(name, [t], was_dense)

    def remove_marker(self, name: str, t_ms: int, kind: str | None = None) -> bool:
        """시각 t 의 마커 하나를 뺀다(kind 를 주면 그 종류만). 없으면 False. 빈 트랙은 남는다(set_markers 와 같음)."""
        t = int(t_ms)
        if name not in self._tracks:
            return False
        ts, ks = self._tracks[name]
        i = self._find(ts, ks, t, kind)
        if i is None:
            return False
        was_dense = self._is_dense(name)
        self._index_remove(name, t, ks[i])
        del ts[i]
        del ks[i]
        self._snap_remove(t)
        self._marks_changed(name, [t], was_dense)
        return True

    def update_marker(self, name: str, old_t_ms: int, old_kind: str | None, new_t_ms: int, new_kind: str) -> bool:
        """마커 하나를 옮기거나 종류를 바꾼다(예: 반응 → 재확인). 옛 마커가 없으면 아무것도 하지 않고 False."""
        ot, nt, nk = int(old_t_ms), int(new_t_ms), str(new_kind)
        if name not in self._tracks:
            return False
        ts, ks = self._tracks[name]
        i = self._find(ts, ks, ot, old_kind)
        if i is None:
            return False
        was_dense = self._is_dense(name)
        self._index_remove(name, ot, ks[i])
        del ts[i]
        del ks[i]
        j = self._insert_pos(ts, ks, nt, nk)
        ts.insert(j, nt)
        ks.insert(j, nk)
        self._index_add(name, nt, nk)
        if ot != nt:
            self._snap_remove(ot)
            self._snap_add(nt)
        self._marks_changed(name, [ot, nt], was_dense)
        return True

    @staticmethod
    def _insert_pos(ts: list[int], ks: list[str], t: int, k: str) -> int:
        """(시각, 종류) 정렬 순서를 지키는 자리 (set_markers 의 sorted 와 같은 순서)."""
        lo, hi = bisect.bisect_left(ts, t), bisect.bisect_right(ts, t)
        while lo < hi and ks[lo] <= k:
            lo += 1
        return lo

    @staticmethod
    def _find(ts: list[int], ks: list[str], t: int, kind: str | None) -> int | None:
        lo, hi = bisect.bisect_left(ts, t), bisect.bisect_right(ts, t)
        for i in range(lo, hi):
            if kind is None or ks[i] == kind:
                return i
        return None

    def _snap_add(self, t: int) -> None:
        pts = self._snap_points
        j = bisect.bisect_left(pts, t)
        if j >= len(pts) or pts[j] != t:
            pts.insert(j, t)

    def _snap_remove(self, t: int) -> None:
        """다른 마커·구간 경계가 같은 시각에 남아 있으면 자석 자리를 지우지 않는다."""
        for ts, _ in self._tracks.values():
            j = bisect.bisect_left(ts, t)
            if j < len(ts) and ts[j] == t:
                return
        if any(s == t or e == t for s, e, _ in self._segments):
            return
        pts = self._snap_points
        j = bisect.bisect_left(pts, t)
        if j < len(pts) and pts[j] == t:
            del pts[j]

    def _marker_dirty_rect(self, name: str, t: int) -> QRect | None:
        """시각 t 마커가 칠하는 칸(위젯 좌표, 테두리 여유 포함). 보이는 범위 밖이면 None."""
        r = dict(self.track_rects()).get("marks:" + name)
        if r is None or self._duration <= 0 or self.view_len <= 0:
            return None
        x = self.t_to_x(t)
        pad = self._mark_rad() + 3
        if x + pad < 0 or x - pad > self.width():
            return None
        return QRect(int(math.floor(x - pad)), int(r.top()), int(math.ceil(2 * pad)) + 2, int(math.ceil(r.height())))

    def _marks_changed(self, name: str, times: Sequence[int], was_dense: bool) -> None:
        rects = [rc for rc in (self._marker_dirty_rect(name, t) for t in times) if rc is not None]
        if not rects:
            return                       # 보이는 범위 밖 — 캐시 그림은 그대로 맞다(촘촘함도 보이는 범위로만 정함)
        if was_dense != self._is_dense(name) or self._cache is None or self._cache_key != self._cache_key_now():
            self._data_rev += 1          # 전체 다시 그리기 (자석 자리는 이미 고쳤다)
            self.update()
            return
        self._patch_marks(name, rects)
        for rc in rects:
            self.update(rc)

    def _patch_marks(self, name: str, rects: Sequence[QRect]) -> None:
        """캐시 그림에서 rects 만 그 마커 트랙을 다시 칠한다(clip). 사각형 밖은 건드리지 않는다."""
        tr_rect = dict(self.track_rects())["marks:" + name]
        p = QPainter(self._cache)
        try:
            p.setRenderHint(QPainter.Antialiasing, False)
            font = QFont()
            font.setPointSize(7)
            p.setFont(font)
            pad_ms = self.px_tol_ms(self._mark_rad() + 3)
            for rc in rects:
                p.save()
                p.setClipRect(rc)
                win = (self.x_to_t_f(rc.left()) - pad_ms, self.x_to_t_f(rc.right() + 1) + pad_ms)
                self._draw_marks(p, tr_rect, name, window=win)
                p.restore()
        finally:
            p.end()
        self.cache_patches += 1
        self.last_dirty_rects = [QRect(rc) for rc in rects]

    def x_to_t_f(self, x: float) -> float:
        """x_to_t 의 반올림·자르기 없는 판 (부분 갱신의 시각 창 계산용)."""
        if self.view_len <= 0 or self.width() <= 0:
            return 0.0
        return self.view_start + x / self.width() * self.view_len

    def set_marker_style(self, kind: str, color: QColor, hollow: bool = False):
        self._styles[kind] = (QColor(color), bool(hollow))
        self._data_changed()

    def set_loop_range(self, a_ms: int | None, b_ms: int | None):
        """A-B 반복 범위 표시 (7-2 범위 반복). 둘 중 하나라도 None 이면 지운다. 캐시 밖에서 그린다."""
        self._loop = (int(a_ms), int(b_ms)) if a_ms is not None and b_ms is not None and b_ms > a_ms else None
        self.update()

    def _data_changed(self):
        self._data_rev += 1
        self._rebuild_snap()
        self.update()

    # ------------------------------------------------------------------ 자석 (ClipSlicer snapped)
    def _rebuild_snap(self):
        pts = set(self._boundaries())
        for ts, _ in self._tracks.values():
            pts.update(ts)
        self._snap_points = sorted(pts)

    def snap_targets(self) -> list[int]:
        """붙을 자리: 구간 경계 · 모든 마커 트랙의 마커 (보이는 범위 안)."""
        a, b = self.view_range()
        lo = bisect.bisect_left(self._snap_points, a)
        hi = bisect.bisect_right(self._snap_points, b)
        return self._snap_points[lo:hi]

    def snapped(self, t: int, enabled: bool = True) -> tuple[int, int | None]:
        """(붙인 시각, 붙은 자리 또는 None). SNAP_PX 안의 가장 가까운 자리에 붙인다."""
        t = int(t)
        if not enabled or not self._snap_points:
            return t, None
        tol = self.px_tol_ms(SNAP_PX)
        pts = self._snap_points
        j = bisect.bisect_left(pts, t)
        best = None
        for k in (j - 1, j):
            if 0 <= k < len(pts) and (best is None or abs(pts[k] - t) < abs(best - t)):
                best = pts[k]
        if best is not None and abs(best - t) <= tol:
            return best, best
        return t, None

    # ------------------------------------------------------------------ 그리기
    def _cache_key_now(self):
        dpr = self.devicePixelRatioF()
        return (self.width(), self._area_rect().height(), dpr, round(self.view_start, 3), round(self.view_len, 3),
                self._data_rev)

    def _ensure_cache(self):
        key = self._cache_key_now()
        if self._cache is not None and key == self._cache_key:
            return
        area = self._area_rect()
        dpr = key[2]
        pix = QPixmap(max(1, int(area.width() * dpr)), max(1, int(area.height() * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(BG)
        p = QPainter(pix)
        try:
            p.setRenderHint(QPainter.Antialiasing, False)
            self._draw_static(p)
        finally:
            p.end()
        self._cache, self._cache_key = pix, key
        self.cache_builds += 1

    def _draw_static(self, p: QPainter):
        if self._duration <= 0 or self.view_len <= 0:
            p.setPen(TEXT_FAINT)
            p.drawText(QRectF(self._area_rect()), Qt.AlignCenter, tr("영상을 열면 타임라인이 보입니다"))
            return
        font = QFont()
        font.setPointSize(7)
        p.setFont(font)
        for name, r in self.track_rects():
            if name == "ruler":
                self._draw_ruler(p, r)
            elif name == "segments":
                self._draw_segments(p, r)
            elif name == "density":
                self._draw_density(p, r)
            elif name == "waveform":
                self._draw_waveform(p, r)
            else:
                self._draw_marks(p, r, name[len("marks:"):])

    def _track_title(self, p: QPainter, r: QRectF, text: str):
        p.setPen(QColor(0, 0, 0, 170))
        p.drawText(r.adjusted(5, 1, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, text)
        p.setPen(QColor(200, 200, 210, 200))
        p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, text)

    def _draw_ruler(self, p: QPainter, r: QRectF):
        """눈금 (ClipSlicer _draw_ruler): 크게 확대하면 프레임 단위 눈금(00:01:23:15). fps 가 없으면 시각 눈금만."""
        p.fillRect(r, RULER_BG)
        w = r.width()
        top = r.top()
        px_per_ms = w / max(self.view_len, 1e-6)
        end = self.view_start + self.view_len
        fps = self._fps
        if fps:
            frame_ms = 1000.0 / fps
            px_per_frame = px_per_ms * frame_ms
            if px_per_frame >= 6:
                o = self._frame_origin_ms
                n = next((k for k in (1, 2, 5, 10, 15, 30, 60) if k * px_per_frame >= 70), 60)
                f0 = int(math.floor((self.view_start - o) / frame_ms))
                f1 = int(math.ceil((end - o) / frame_ms)) + 1
                p.setPen(QColor(90, 90, 100))
                for f in range(f0, f1 + 1):
                    x = self.t_to_x(o + f * frame_ms)
                    if 0 <= x <= w:
                        p.drawLine(int(x), int(top), int(x), int(top + 3))
                p.setPen(TEXT_DIM)
                for f in range((f0 // n) * n, f1 + 1, n):
                    t = o + f * frame_ms
                    x = self.t_to_x(t)
                    if x >= 0 and t >= 0:
                        p.drawLine(int(x), int(top), int(x), int(top + 6))
                        p.drawText(int(x) + 3, int(top + 14), fmt_timecode(t, fps))
                return
        step = 3_600_000
        for cand in (100, 200, 500, 1000, 2000, 5000, 10_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000,
                     900_000, 1_800_000, 3_600_000):
            if cand * px_per_ms >= 70:
                step = cand
                break
        p.setPen(TEXT_DIM)
        t = (int(self.view_start) // step) * step
        while t <= end:
            x = self.t_to_x(t)
            if x >= 0:
                p.drawLine(int(x), int(top), int(x), int(top + 5))
                p.drawText(int(x) + 3, int(top + 14), fmt_time(t, frac_digits=1 if step < 1000 else 0))
            t += step

    def _draw_segments(self, p: QPainter, r: QRectF):
        p.fillRect(r, TRACK_BG)
        w = r.width()
        a, b = self.view_start, self.view_start + self.view_len
        normal = QFont(p.font())
        bold = QFont(normal)
        bold.setBold(True)
        for s, e, cat in self._segments:
            if e < a or s > b:
                continue
            x0, x1 = max(self.t_to_x(s), -1.0), min(self.t_to_x(e), w + 1.0)
            rr = QRectF(x0, r.top(), max(x1 - x0, 1.0), r.height())
            col = QColor(CATEGORY_COLORS.get(cat, CATEGORY_COLORS["other"]))
            if cat is None:
                p.fillRect(rr, QColor(45, 45, 52))
                p.fillRect(rr, QBrush(QColor(80, 80, 90), Qt.BDiagPattern))   # 아직 안 매긴 시간
            else:
                fill = QColor(col)
                fill.setAlpha(120)
                p.fillRect(rr, fill)
                p.fillRect(QRectF(rr.left(), r.bottom() - 3, rr.width(), 3), col)
            if rr.width() > 40:
                p.setFont(bold)
                name = CATEGORY_NAMES.get(cat, str(cat))
                self._track_title(p, QRectF(max(rr.left(), 0.0), r.top(), rr.width(), r.height()), name)
                p.setFont(normal)
        # 경계 완충 음영 (02 7-3 '완충 음영')
        for lo, hi in self._buffers:
            if hi < a or lo > b:
                continue
            x0, x1 = self.t_to_x(lo), self.t_to_x(hi)
            rr = QRectF(x0, r.top(), max(x1 - x0, 1.0), r.height())
            p.fillRect(rr, QColor(0, 0, 0, 120))
            p.fillRect(rr, QBrush(QColor(255, 255, 255, 40), Qt.Dense6Pattern))
        # 경계선
        p.setPen(QPen(QColor(230, 230, 235, 200), 1))
        for t in self._boundaries():
            if a <= t <= b and 0 < t < self._duration:
                x = int(self.t_to_x(t))
                p.drawLine(x, int(r.top()), x, int(r.bottom()))

    def _column_max(self, values: Sequence, unit_ms: float, x: int) -> float:
        """화면 열 x 가 덮는 시간에 든 값들의 최댓값."""
        n = len(values)
        t0 = self.view_start + x / self.width() * self.view_len
        t1 = self.view_start + (x + 1) / self.width() * self.view_len
        i0 = int(t0 // unit_ms)
        if i0 >= n or t1 <= 0:
            return 0.0
        i0 = max(0, i0)
        i1 = min(n, max(i0 + 1, int(math.ceil(t1 / unit_ms))))
        return max(values[i0:i1])

    def _draw_density(self, p: QPainter, r: QRectF):
        """채팅 밀도 막대. 높이는 방송 전체의 가장 바쁜 버킷 기준(확대해도 높이가 흔들리지 않게)."""
        p.fillRect(r, TRACK_BG)
        if not self._density or self._density_max <= 0:
            self._track_title(p, r, tr("채팅 밀도 (없음)"))
            return
        w = int(r.width())
        top = float(self._density_max)
        bottom = r.bottom()
        hmax = r.height() - 2
        # 경계 완충 안은 흐리게 (평가 제외 구간임을 보이게)
        poly = QPolygonF()
        poly.append(QPointF(0, bottom))
        unit = float(self._density_bucket)
        for x in range(w):
            v = self._column_max(self._density, unit, x)
            y = bottom - v / top * hmax
            poly.append(QPointF(x, y))
            poly.append(QPointF(x + 1, y))
        poly.append(QPointF(w, bottom))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 167, 38, 170))
        p.drawPolygon(poly)
        a, b = self.view_start, self.view_start + self.view_len
        for lo, hi in self._buffers:
            if hi < a or lo > b:
                continue
            x0, x1 = self.t_to_x(lo), self.t_to_x(hi)
            p.fillRect(QRectF(x0, r.top(), max(x1 - x0, 1.0), r.height()), QColor(0, 0, 0, 90))
        self._track_title(p, r, tr("채팅 밀도"))

    def _draw_waveform(self, p: QPainter, r: QRectF):
        p.fillRect(r, TRACK_BG)
        if not self._wave or self._wave_span <= 0:
            self._track_title(p, r, tr("파형 (없음)"))
            return
        w = int(r.width())
        mid = r.center().y()
        half = r.height() / 2 - 1
        unit = self._wave_span / len(self._wave)
        top_pts, bot_pts = [], []
        for x in range(w):
            v = self._column_max(self._wave, unit, x) * half
            top_pts.append(QPointF(x + 0.5, mid - v))
            bot_pts.append(QPointF(x + 0.5, mid + v))
        poly = QPolygonF(top_pts + bot_pts[::-1])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 200, 175, 150))
        p.drawPolygon(poly)
        self._track_title(p, r, tr("파형"))

    def _mark_rad(self) -> float:
        return max(3.0, MARK_H / 2 - 3)

    def _is_dense(self, name: str) -> bool:
        """보이는 범위의 마커가 너무 촘촘하면 세로 눈금만 그린다 (모양 없이)."""
        ts = self._tracks.get(name, ([], []))[0]
        a, b = self.view_range()
        return (bisect.bisect_right(ts, b) - bisect.bisect_left(ts, a)) > self.width() / 3

    # ------------------------------------------------------------------ 촘촘한 트랙의 열 계산 (16 5-2 12번)
    # 확대·축소·스크롤 때마다 보이는 마커 수십만 개를 하나씩 x 로 바꾸던 것을, 트랙별 '종류 → 정렬된 시각' 색인에서
    # 열마다 bisect 한 번으로 건너뛰는 계산으로 바꾼다. 결과(열 번호 목록)는 예전 {floor((t - view_start) * scale)} 와
    # 같다. 색인은 처음 촘촘하게 그릴 때 만들고, add/remove/update_marker 는 색인을 그 자리에서 고친다(다시 만들지 않음).
    def _kind_index(self, name: str) -> dict[str, list[int]]:
        idx = self._kind_times.get(name)
        if idx is None:
            idx = {}
            ts, ks = self._tracks.get(name, ([], []))
            for t, k in zip(ts, ks):
                lst = idx.get(k)
                if lst is None:
                    lst = idx[k] = []
                lst.append(t)            # ts 가 (시각, 종류) 순으로 정렬돼 있어 종류별 목록도 정렬돼 있다
            self._kind_times[name] = idx
        return idx

    def _index_add(self, name: str, t: int, k: str) -> None:
        idx = self._kind_times.get(name)
        if idx is not None:
            bisect.insort(idx.setdefault(k, []), t)

    def _index_remove(self, name: str, t: int, k: str) -> None:
        idx = self._kind_times.get(name)
        if idx is None:
            return
        lst = idx.get(k)
        j = bisect.bisect_left(lst, t) if lst else 0
        if lst and j < len(lst) and lst[j] == t:
            del lst[j]
            if not lst:
                del idx[k]
        else:                            # 어긋남(있을 수 없음) — 안전하게 다음 그리기 때 다시 만든다
            self._kind_times.pop(name, None)

    @staticmethod
    def _occupied_columns(times: list[int], a: int, b: int, vs: float, scale: float, out: set) -> None:
        """times(정렬) 중 [a, b] 안의 시각이 하나라도 있는 열 c = floor((t - vs) * scale) 를 out 에 더한다.
        열마다 첫 시각 하나만 보고 다음 열의 시작 시각으로 bisect 한다(보이는 열 수만큼만 돈다)."""
        if scale <= 0:
            return
        i = bisect.bisect_left(times, a)
        j = bisect.bisect_right(times, b)
        inv = 1.0 / scale
        floor, ceil, bl = math.floor, math.ceil, bisect.bisect_left
        while i < j:
            c = floor((times[i] - vs) * scale)
            out.add(c)
            # 다음 열이 시작하는 정수 시각. 부동소수 오차에 대비해 1ms 앞에서 찾는다(앞당겨진 한 칸은 다음 바퀴가 다시 본다).
            i = bl(times, ceil(vs + (c + 1) * inv) - 1, i + 1, j)

    def _dense_columns(self, name: str, a: int, b: int, vs: float, scale: float) -> list[tuple[tuple, list[int]]]:
        """[(모양 열쇠 (rgba, hollow), 정렬된 열 번호들)] — 모양 열쇠 순(겹칠 때 쌓임 순서가 늘 같게)."""
        by_style: dict[tuple, set] = {}
        for k, times in self._kind_index(name).items():
            col, hollow = self._styles.get(k, DEFAULT_MARK)
            self._occupied_columns(times, a, b, vs, scale, by_style.setdefault((col.rgba(), hollow), set()))
        return [(sk, sorted(cols)) for sk, cols in sorted(by_style.items(), key=lambda kv: kv[0]) if cols]

    def _draw_marks(self, p: QPainter, r: QRectF, name: str, window: tuple[float, float] | None = None):
        """마커 트랙 하나. window = (시작 ms, 끝 ms) 를 주면 그 시각 안의 마커만 그린다(부분 갱신, 칠하기는 clip 이 막는다).
        촘촘함(dense)은 늘 보이는 범위 전체로 정한다 — 부분 갱신과 전체 그리기의 모양이 같게."""
        p.fillRect(r, TRACK_BG)
        ts, ks = self._tracks.get(name, ([], []))
        a, b = self.view_range()
        dense = self._is_dense(name)
        if window is not None:
            a, b = max(a, int(math.floor(window[0]))), min(b, int(math.ceil(window[1])))
        lo = bisect.bisect_left(ts, a)
        hi = bisect.bisect_right(ts, b)
        cy = r.center().y()
        rad = max(3.0, r.height() / 2 - 3)
        by_style: dict[tuple, list] = {}
        key_of: dict[str, tuple] = {}
        vs = self.view_start
        scale = self.width() / self.view_len if self.view_len > 0 else 0.0
        if dense:
            # 촘촘하면 화면 열마다 한 줄만 (20만 마커를 한 줄씩 긋지 않는다). 열 = floor(x), 선은 열 가운데.
            # 보이는 마커를 하나씩 훑지 않고 종류별 시각 목록에서 열마다 한 번 건너뛴다(_occupied_columns, 16 5-2 12번).
            y0, y1 = r.top() + 2, r.bottom() - 2
            p.setRenderHint(QPainter.Antialiasing, False)
            for (rgba, _hollow), cols in self._dense_columns(name, a, b, vs, scale):
                p.setPen(QPen(QColor.fromRgba(rgba), 1))
                p.drawLines([QLineF(c + 0.5, y0, c + 0.5, y1) for c in cols])
            self._track_title(p, r, self._track_labels.get(name) or TRACK_NAMES.get(name, name))
            return
        for i in range(lo, hi):
            k = ks[i]
            sk = key_of.get(k)
            if sk is None:
                col, hollow = self._styles.get(k, DEFAULT_MARK)
                sk = key_of[k] = (col.rgba(), hollow)
            by_style.setdefault(sk, []).append((ts[i] - vs) * scale)
        p.setRenderHint(QPainter.Antialiasing, True)
        # 겹친 마커의 쌓임 순서를 정해 둔다(색·속 빔 순) — 부분 갱신과 전체 그리기가 같은 그림이 되게
        for (rgba, hollow), xs in sorted(by_style.items(), key=lambda kv: kv[0]):
            col = QColor.fromRgba(rgba)
            p.setPen(QPen(col, 1.5))
            p.setBrush(Qt.NoBrush if hollow else QBrush(col))
            for x in xs:
                p.drawPolygon(QPolygonF([QPointF(x, cy - rad), QPointF(x + rad, cy), QPointF(x, cy + rad),
                                         QPointF(x - rad, cy)]))
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setBrush(Qt.NoBrush)
        self._track_title(p, r, self._track_labels.get(name) or TRACK_NAMES.get(name, name))

    def _head_x(self) -> int | None:
        if self._duration <= 0 or self.view_len <= 0:
            return None
        x = int(self.t_to_x(self._position))
        return x if -2 <= x <= self.width() + 2 else None

    def _update_head(self):
        """재생 위치만 바뀜: 옛 선·새 선 둘레만 다시 그린다 (캐시 그림은 그대로)."""
        h = self._area_rect().height()
        nx = self._head_x()
        for x in (self._last_head_x, nx):
            if x is not None:
                self.update(QRect(x - 8, 0, 17, h))

    def paintEvent(self, event):
        self._ensure_cache()
        p = QPainter(self)
        try:
            area = self._area_rect()
            clip = event.rect().intersected(area)
            if not clip.isEmpty():
                p.drawPixmap(clip, self._cache, self._src_rect(clip))
            bar_y = area.bottom() + 1
            if event.rect().bottom() >= bar_y:
                p.fillRect(QRect(0, bar_y, self.width(), BAR_H), BG)
            if self._duration <= 0:
                self._last_head_x = None
                return
            h = area.height()
            if self._loop is not None:
                x0, x1 = self.t_to_x(self._loop[0]), self.t_to_x(self._loop[1])
                p.fillRect(QRectF(x0, 0, max(x1 - x0, 1.0), RULER_H), QColor(255, 235, 59, 90))
                p.setPen(QPen(QColor(255, 235, 59, 200), 1))
                p.drawLine(QPointF(x0, 0), QPointF(x0, h))
                p.drawLine(QPointF(x1, 0), QPointF(x1, h))
            if self.hover_x >= 0 and not self.scrubbing:
                p.setPen(QPen(QColor(255, 255, 255, 80), 1))
                p.drawLine(self.hover_x, 0, self.hover_x, h)
            if self.snap_t is not None:
                x = int(self.t_to_x(self.snap_t))
                p.setPen(QPen(QColor(255, 235, 59), 1, Qt.DashLine))
                p.drawLine(x, 0, x, h)
            x = self._head_x()
            self._last_head_x = x
            if x is not None:
                p.setPen(QPen(QColor(255, 255, 255), 2))
                p.drawLine(x, 0, x, h)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255))
                p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 7)]))
        finally:
            p.end()

    def _src_rect(self, clip: QRect) -> QRect:
        d = self._cache.devicePixelRatio()
        return QRectF(clip.x() * d, clip.y() * d, clip.width() * d, clip.height() * d).toRect()

    # ------------------------------------------------------------------ 마우스
    def _in_area(self, y: float) -> bool:
        return 0 <= y < self._area_rect().height()

    def _seek_at(self, x: float, mods) -> None:
        t, self.snap_t = self.snapped(self.x_to_t(x), not (mods & Qt.ShiftModifier))
        self.seekRequested.emit(int(t))

    def mousePressEvent(self, e):
        if self._duration <= 0 or e.button() != Qt.LeftButton or not self._in_area(e.position().y()):
            return
        self.scrubbing = True
        self._seek_at(e.position().x(), e.modifiers())
        self.update()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        old = self.hover_x
        self.hover_x = int(x) if self._in_area(e.position().y()) else -1
        if self.scrubbing:
            self._seek_at(x, e.modifiers())
            self.update()
            return
        if self._duration > 0 and self.hover_x >= 0:
            t = self.x_to_t(x)
            text = fmt_time(t, frac_digits=1)
            if self._density and self._density_bucket > 0:
                i = t // self._density_bucket
                if 0 <= i < len(self._density):
                    text += tr("  채팅 {0}").format(self._density[i])
            QToolTip.showText(e.globalPosition().toPoint(), text, self)
        h = self._area_rect().height()
        for hx in (old, self.hover_x):
            if hx >= 0:
                self.update(QRect(hx - 2, 0, 5, h))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or not self.scrubbing:
            return
        self.scrubbing = False
        self._seek_at(e.position().x(), e.modifiers())
        self.snap_t = None
        self.update()

    def wheelEvent(self, e):
        """ClipSlicer 습관 (08 D-09 7): 휠 = 가로 스크롤, Ctrl+휠(또는 Alt+휠) = 마우스 위치 기준 확대·축소.
        Shift+휠 · 가로 휠(터치패드)도 가로 스크롤이다. 위로 굴리면 앞(이른 시각)으로 간다."""
        if self._duration <= 0:
            return
        d = e.angleDelta()
        dx, dy = d.x(), d.y()
        mods = e.modifiers()
        steps = (dy or dx) / 120.0               # Alt+휠은 가로 값으로 오는 경우가 있다 (ClipSlicer)
        if mods & (Qt.ControlModifier | Qt.AltModifier):
            if steps:
                self.zoom(1.25 ** steps, self.x_to_t(e.position().x()))
        elif steps:
            self.scroll_to(self.view_start - steps * self.view_len * 0.15)
        e.accept()

    def leaveEvent(self, _e):
        if self.hover_x >= 0:
            h = self._area_rect().height()
            self.update(QRect(self.hover_x - 2, 0, 5, h))
        self.hover_x = -1
        QToolTip.hideText()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update()
