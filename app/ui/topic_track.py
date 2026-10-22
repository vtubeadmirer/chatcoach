"""주제 띠 — 타임라인 위에 주제 덩어리를 색 띠로 그리는 작은 위젯과, 타임라인에 넘길 데이터 함수
(08 D-01 '주제 흐름', D-17 5, 17 6-1 1번 '타임라인에 주제 트랙').

주제 흐름은 엔진이 내고(D-17 5) 앱은 보이기만 한다. 이 파일은 디스크를 열지 않고, 받은 덩어리 목록을 그리기만 한다.
덩어리 목록은 app.ui.summary_view.normalize_topics 가 읽는 모양 그대로 받는다(dict 목록, 속성 있는 객체 목록,
{'topics': [...]} 모양 등). 띠에는 **주제 이름만** 쓰고 전사문·채팅 본문은 받아도 그리지 않는다(D-17 8).

통합하는 쪽(작업대·분석 화면)이 고르는 두 가지 방법
1) 위젯: TopicTrack 을 Timeline 바로 위(같은 폭)에 두고
       timeline.viewChanged.connect(track.set_view)
       track.set_duration(timeline.duration); track.set_view(*timeline.view_range())
       track.seekRequested.connect(<재생 위치 옮기기>)
   를 이으면 확대·스크롤이 타임라인과 함께 움직인다. 클릭 한 번 = 그 덩어리의 시작 시각으로 이동(D-16 클릭 한 번 원칙).
2) 데이터만: topic_markers(topics) 를 Timeline.set_markers('topics', …, label='주제') 로 넘기면 바뀐 시각이 마커 트랙으로
   보인다(timeline.py 를 고치지 않고 쓸 수 있는 가장 작은 방법). 마커 종류 이름은 TOPIC_MARK_KIND, 색은 TOPIC_MARK_COLOR
   (set_marker_style 로 등록).

이 파일은 Qt 위젯 말고는 순수 계산(topic_bands · topic_markers · band_at)이라 화면 없이 시험할 수 있다.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.timecode import fmt_display
from ..i18n import tr

TRACK_H = 22                       # timeline.SEG_H 와 같은 높이
TOPIC_MARK_KIND = "topic"          # topic_markers 의 마커 종류 이름
TOPIC_MARK_COLOR = QColor(129, 199, 132)

BG = QColor(31, 31, 36)            # timeline.TRACK_BG
TEXT_FAINT = QColor(110, 110, 120)

# 주제 띠 색 — 이웃한 덩어리가 다른 색이 되도록 차례로 돈다(활동 분류 색 timeline.CATEGORY_COLORS 와 겹치지 않게 엷은 색).
PALETTE = (
    QColor(77, 182, 172),
    QColor(240, 98, 146),
    QColor(255, 202, 40),
    QColor(121, 134, 203),
    QColor(161, 136, 127),
    QColor(174, 213, 129),
)


@dataclass(frozen=True)
class TopicBand:
    """띠 하나. index = 시작 시각 순서(0부터), name = 주제 이름(없으면 '주제 n')."""
    index: int
    start_ms: int
    end_ms: int
    name: str
    color_index: int

    @property
    def color(self) -> QColor:
        return QColor(PALETTE[self.color_index % len(PALETTE)])


def topic_bands(topics: Any) -> list[TopicBand]:
    """주제 덩어리 목록 → 시작 시각 순서의 띠 목록. 시각이 없거나 끝 ≤ 시작 인 덩어리는 뺀다.
    받는 모양은 summary_view.normalize_topics 와 같다(그 함수를 그대로 쓴다)."""
    from .summary_view import normalize_topics   # 순환 import 를 피해 여기서
    rows = normalize_topics(topics).rows
    return [TopicBand(index=i, start_ms=r.start_ms, end_ms=r.end_ms, name=r.display_name, color_index=i)
            for i, r in enumerate(rows)]


def topic_markers(topics: Any) -> list[tuple[int, str]]:
    """Timeline.set_markers('topics', …) 에 넘길 (시각, 종류) 목록 — 덩어리마다 시작 시각 하나."""
    return [(b.start_ms, TOPIC_MARK_KIND) for b in topic_bands(topics)]


def band_at(bands: Sequence[TopicBand], t_ms: int) -> TopicBand | None:
    """t_ms 를 품은 띠(시작 ≤ t < 끝). 겹치면 시작이 가장 늦은 것. 없으면 None."""
    starts = [b.start_ms for b in bands]
    i = bisect.bisect_right(starts, t_ms) - 1
    while i >= 0:
        b = bands[i]
        if b.start_ms <= t_ms < b.end_ms:
            return b
        i -= 1
    return None


class TopicTrack(QWidget):
    """주제 띠 한 줄. 보이는 범위는 Timeline.viewChanged 를 받아 맞춘다(호출하는 쪽이 잇는다).

    시그널
    - seekRequested(int): 띠를 누르면 그 덩어리의 시작 시각, 띠 밖을 누르면 누른 자리의 시각.
    - topicClicked(int): 누른 띠의 index(topic_bands 순서). 띠 밖이면 보내지 않는다.
    """
    seekRequested = Signal(int)
    topicClicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bands: list[TopicBand] = []
        self._duration = 0
        self._view: tuple[int, int] = (0, 0)
        self._position = 0
        self._sealed = False
        self.setMouseTracking(True)
        self.setFixedHeight(TRACK_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    # ------------------------------------------------------------------ 자료
    def set_topics(self, topics: Any) -> None:
        """주제 덩어리 목록을 바꾼다. None 이면 비운다."""
        self._sealed = False
        self._bands = topic_bands(topics) if topics is not None else []
        self._sync_tooltip()
        self.update()

    def set_sealed(self, sealed: bool = True) -> None:
        """봉인 상태 — 띠를 비우고 '봉인됨' 만 쓴다(D-15 8, D-17 5)."""
        self._sealed = bool(sealed)
        if self._sealed:
            self._bands = []
        self._sync_tooltip()
        self.update()

    def is_sealed(self) -> bool:
        return self._sealed

    def bands(self) -> list[TopicBand]:
        return list(self._bands)

    def set_duration(self, ms: int) -> None:
        self._duration = max(0, int(ms))
        if self._view[1] <= self._view[0]:
            self._view = (0, self._duration)
        self.update()

    def set_view(self, start_ms: int, end_ms: int) -> None:
        """보이는 범위(ms). Timeline.viewChanged(int, int) 에 바로 잇는다."""
        a, b = int(start_ms), int(end_ms)
        if b <= a:
            return
        if (a, b) != self._view:
            self._view = (a, b)
            self.update()

    def view_range(self) -> tuple[int, int]:
        return self._view

    def set_position(self, ms: int) -> None:
        """재생 위치 — 그 자리의 띠에 테두리를 그린다."""
        ms = int(ms)
        old = band_at(self._bands, self._position)
        self._position = ms
        if band_at(self._bands, ms) is not old:
            self.update()

    def current_band(self) -> TopicBand | None:
        return band_at(self._bands, self._position)

    # ------------------------------------------------------------------ 좌표
    def t_to_x(self, t: float) -> float:
        a, b = self._view
        if b <= a:
            return 0.0
        return (t - a) / (b - a) * self.width()

    def x_to_t(self, x: float) -> int:
        a, b = self._view
        if b <= a or self.width() <= 0:
            return 0
        t = a + x / self.width() * (b - a)
        hi = self._duration if self._duration > 0 else b
        return int(round(max(0.0, min(float(hi), t))))

    def band_at_x(self, x: float) -> TopicBand | None:
        return band_at(self._bands, self.x_to_t(x))

    # ------------------------------------------------------------------ Qt
    def sizeHint(self) -> QSize:
        return QSize(400, TRACK_H)

    def _sync_tooltip(self) -> None:
        if self._sealed:
            self.setToolTip(tr("주제 흐름: 봉인됨"))
        elif not self._bands:
            self.setToolTip(tr("주제 흐름이 아직 없습니다"))
        else:
            self.setToolTip("")

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self._sealed:
            return super().mousePressEvent(e)
        x = e.position().x()
        band = self.band_at_x(x)
        if band is not None:
            self.topicClicked.emit(band.index)
            self.seekRequested.emit(band.start_ms)
        elif self._view[1] > self._view[0]:
            self.seekRequested.emit(self.x_to_t(x))
        e.accept()

    def mouseMoveEvent(self, e):
        if not self._sealed and self._bands:
            band = self.band_at_x(e.position().x())
            if band is None:
                self.setToolTip("")
            else:
                self.setToolTip(tr("{name} · {a} ~ {b}").format(
                    name=band.name, a=fmt_display(band.start_ms), b=fmt_display(band.end_ms)))
        super().mouseMoveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        r = QRectF(0, 0, self.width(), self.height())
        p.fillRect(r, BG)
        font = QFont(p.font())
        font.setPointSize(7)
        p.setFont(font)
        if self._sealed or not self._bands:
            p.setPen(TEXT_FAINT)
            p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter,
                       tr("주제: 봉인됨") if self._sealed else tr("주제: 없음"))
            p.end()
            return
        a, b = self._view
        w = r.width()
        cur = band_at(self._bands, self._position)
        for band in self._bands:
            if band.end_ms < a or band.start_ms > b:
                continue
            x0, x1 = max(self.t_to_x(band.start_ms), -1.0), min(self.t_to_x(band.end_ms), w + 1.0)
            rr = QRectF(x0, r.top(), max(x1 - x0, 1.0), r.height())
            col = band.color
            fill = QColor(col)
            fill.setAlpha(110)
            p.fillRect(rr, fill)
            p.fillRect(QRectF(rr.left(), r.bottom() - 3, rr.width(), 3), col)
            p.setPen(QPen(QColor(230, 230, 235, 150), 1))
            p.drawLine(int(rr.left()), int(r.top()), int(rr.left()), int(r.bottom()))
            if band is cur:
                p.setPen(QPen(QColor(255, 255, 255, 220), 2))
                p.drawRect(rr.adjusted(1, 1, -1, -1))
            if rr.width() > 40:
                tr_rect = QRectF(max(rr.left(), 0.0) + 4, r.top(), min(rr.right(), w) - max(rr.left(), 0.0) - 6,
                                 r.height())
                text = p.fontMetrics().elidedText(band.name, Qt.ElideRight, max(int(tr_rect.width()), 0))
                p.setPen(QColor(0, 0, 0, 170))
                p.drawText(tr_rect.adjusted(1, 1, 1, 1), Qt.AlignLeft | Qt.AlignVCenter, text)
                p.setPen(QColor(235, 235, 240))
                p.drawText(tr_rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        p.end()
