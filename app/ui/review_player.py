"""7-2 확인 범위 재생기 (docs/design/02-architecture.md 7-3 'review_player', 안내서 v0.1 7-2, 03 6-9).

MpvPlayer 를 '감싸서' 쓴다. 플레이어 위젯을 다른 부모로 옮기지 않는다(창 핸들이 바뀜, 10 5-3 확인 필요 8).
작업대가 가진 플레이어를 그대로 넘기고, 이 객체는 신호를 듣고 명령만 보낸다.

하는 일
- 7-2 확인 범위 [채팅 시각 − pre_roll, 후보 발화 끝 + post_roll] 를 A-B 반복으로 한 번에 건다 (`R`).
- 속도 프리셋 0.5 / 0.75 / 1 / 1.25 / 1.5 / 2 (`[` / `]`). 음 높이 보정은 mpv 기본(켬).
- 들은 범위(watched_ranges): 재생 중이고, 소리가 켜져 있고(음소거 아님·음량 > 0), 속도가 heard_max_speed(기본 2배)
  이하일 때 지나간 VOD 구간을 '듣기 회차(pass)' 마다 모은다. 탐색·반복 되감기처럼 건너뛴 곳은 넣지 않는다.
- 회차: 편집기를 열면 1회차가 시작되고, 범위 반복을 다시 켤 때(이미 들은 것이 있으면) 새 회차가 된다.
  1회차 = 처음 확인(first), 2회차부터 = 다시 듣기(recheck). 직접·높음은 '들었다' 고 인정된 회차가
  min_views(기본 2) 이상이어야 완료다(07 질문 15 기본안).
- 화면 위 문구(OSD): mpv show-text 로 판정 중인 채팅 번호를 띄운다. Qt 위젯을 영상 위에 겹치지 않는다.
- 프레임 번호: 고정 프레임률일 때만. 가변 프레임률이면 ms 만(02 7-3).

시각: 이 모듈의 공개 값은 모두 VOD(채팅) 시각 ms 다. 플레이어와 주고받을 때만 to_file / to_vod 로 바꾼다.

개인정보: 로그에 채팅 본문·닉네임을 남기지 않는다. OSD 문구는 화면에만 간다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from ..core import rules as _rules
from ..core.timecode import fmt_time
from ..i18n import tr

LOG = logging.getLogger(__name__)

SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)     # 02 7-3
HEARD_MAX_SPEED = 2.0                         # session.open.tool.heard_max_speed 기본값 (03 6-4)
HEARD_MIN_COVERAGE = _rules.HEARD_MIN_COVERAGE  # '소리로 들었다' 로 인정하는 덮은 비율 (core.rules 시작값, 확인 필요)
PRE_ROLL_MS = 10_000                          # guide-config recheck.pre_roll_sec
POST_ROLL_MS = 30_000                         # guide-config recheck.post_roll_sec
MIN_VIEWS = 2                                 # guide-config recheck.min_views_direct_likely
MAX_STEP_MS = 1_500                           # 1배속 기준 이보다 크게 뛰면 '건너뜀' 으로 본다(위치 신호는 40ms 마다)
OSD_MS = 4_000
MS_MAX = 172_800_000                          # label-event 스키마 ms 상한


# ---------------------------------------------------------------------------- 순수 도우미 (Qt 없음)

def merge_ranges(ranges: Iterable[Sequence[int]]) -> list[tuple[int, int]]:
    """[시작, 끝) 구간들을 정렬해 겹치거나 맞닿은 것을 합친다. 길이 0 이하인 것은 버린다."""
    rs = sorted((int(a), int(b)) for a, b in ranges if int(b) > int(a))
    out: list[tuple[int, int]] = []
    for a, b in rs:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def clip_ranges(ranges: Iterable[Sequence[int]], start: int, end: int) -> list[tuple[int, int]]:
    """구간들을 [start, end) 안으로 자르고 합친다."""
    out = []
    for a, b in ranges:
        a2, b2 = max(int(a), start), min(int(b), end)
        if b2 > a2:
            out.append((a2, b2))
    return merge_ranges(out)


def coverage(ranges: Iterable[Sequence[int]], start: int, end: int) -> float:
    """[start, end) 를 구간들이 덮은 비율 (0~1). 범위 길이가 0 이하면 0."""
    if end <= start:
        return 0.0
    got = sum(b - a for a, b in clip_ranges(ranges, start, end))
    return round(min(1.0, got / (end - start)), 4)


def review_window(chat_ms: int, utterance_end_ms: int | None, pre_roll_ms: int = PRE_ROLL_MS,
                  post_roll_ms: int = POST_ROLL_MS) -> tuple[int, int]:
    """안내서 7-2 확인 범위: 채팅이 뜨기 pre_roll 전 ~ 후보 발화 끝 post_roll 뒤 (VOD ms).

    발화 끝을 모르면 채팅 시각을 끝으로 본다. 시작은 0 아래로 내리지 않고, 끝은 시작보다 뒤다.
    """
    chat_ms = int(chat_ms)
    tail = chat_ms if utterance_end_ms is None else max(chat_ms, int(utterance_end_ms))
    a = max(0, chat_ms - int(pre_roll_ms))
    b = min(MS_MAX, tail + int(post_roll_ms))
    if b <= a:
        b = a + 1
    return a, b


def next_speed(current: float, step: int) -> float:
    """속도 프리셋에서 한 단계 위(step=+1)·아래(step=-1). 프리셋 밖 값이면 가장 가까운 칸에서 움직인다."""
    i = min(range(len(SPEEDS)), key=lambda k: abs(SPEEDS[k] - float(current)))
    if abs(SPEEDS[i] - float(current)) > 1e-6:
        # 프리셋 밖: 올리면 바로 위 칸, 내리면 바로 아래 칸
        if step > 0:
            ups = [s for s in SPEEDS if s > current]
            return ups[0] if ups else SPEEDS[-1]
        downs = [s for s in SPEEDS if s < current]
        return downs[-1] if downs else SPEEDS[0]
    return SPEEDS[max(0, min(len(SPEEDS) - 1, i + (1 if step > 0 else -1)))]


@dataclass
class ReviewPass:
    """듣기 회차 하나. ranges 는 VOD ms 로 합친 값(확인 범위로 자르지 않은 원본)."""
    index: int
    ranges: list[tuple[int, int]] = field(default_factory=list)
    max_speed: float | None = None

    def add(self, a: int, b: int, speed: float) -> None:
        if b <= a:
            return
        self.ranges = merge_ranges(self.ranges + [(a, b)])
        self.max_speed = speed if self.max_speed is None else max(self.max_speed, speed)

    def summary(self, start: int, end: int, min_coverage: float = HEARD_MIN_COVERAGE) -> dict:
        """video_check 본문에 들어갈 값(03 6-9): 범위·들은 구간(범위로 자름)·덮은 비율·들었다 인정."""
        rs = clip_ranges(self.ranges, start, end)
        cov = coverage(rs, start, end)
        return {
            "window_start_ms": int(start), "window_end_ms": int(end),
            "watched_ranges": [[a, b] for a, b in rs],
            "coverage_ratio": cov,
            "audio_heard": cov >= min_coverage,
            "max_speed": self.max_speed,
        }


# ---------------------------------------------------------------------------- 재생기 감싸개

def _ident(ms: int) -> int:
    return int(ms)


class ReviewPlayer(QObject):
    """MpvPlayer(또는 같은 모양의 대역)를 감싸 7-2 확인을 돕는다. 위젯이 아니다(controls() 가 막대를 만든다)."""

    loopChanged = Signal(bool)
    speedChanged = Signal(float)
    coverageChanged = Signal(float)       # 지금 회차가 지금 범위를 덮은 비율
    passStarted = Signal(int)             # 새 회차 번호(0부터). 편집기는 앞 회차의 판단을 이때 찍어 둔다
    windowChanged = Signal(int, int)
    normalizeChanged = Signal(bool)

    def __init__(self, player, parent=None, *, to_file: Callable[[int], int] | None = None,
                 to_vod: Callable[[int], int] | None = None, pre_roll_ms: int = PRE_ROLL_MS,
                 post_roll_ms: int = POST_ROLL_MS, heard_max_speed: float = HEARD_MAX_SPEED,
                 min_coverage: float = HEARD_MIN_COVERAGE, min_views: int = MIN_VIEWS):
        super().__init__(parent)
        self.player = player
        self._to_file = to_file or _ident
        self._to_vod = to_vod or _ident
        self.pre_roll_ms = int(pre_roll_ms)
        self.post_roll_ms = int(post_roll_ms)
        self.heard_max_speed = float(heard_max_speed)
        self.min_coverage = float(min_coverage)
        self.min_views = int(min_views)
        self._window: tuple[int, int] | None = None
        self._passes: list[ReviewPass] = []
        self._last_vod: int | None = None
        self._playing = bool(_call(player, "is_playing", False))
        sp = _call(player, "speed", 1.0)
        self._speed = float(sp) if isinstance(sp, (int, float)) and not isinstance(sp, bool) else 1.0
        self._muted = False
        self._loop_on = False
        self._normalize: bool | None = None
        self._osd_text = ""
        self._fps: float | None = None
        self._vfr = False
        self._last_cov = -1.0
        self._connected = False
        self._connect()

    # ------------------------------------------------------------ 연결
    def _connect(self) -> None:
        p = self.player
        for sig, slot in (("positionChanged", self._on_position), ("playingChanged", self._on_playing)):
            s = getattr(p, sig, None)
            if s is not None:
                s.connect(slot)
        self._connected = True

    def detach(self) -> None:
        """플레이어 신호를 끊는다. 반복은 끈다. 여러 번 불러도 된다."""
        if not self._connected:
            return
        p = self.player
        for sig, slot in (("positionChanged", self._on_position), ("playingChanged", self._on_playing)):
            s = getattr(p, sig, None)
            if s is not None:
                try:
                    s.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._connected = False
        if self._loop_on:
            self.stop_loop()

    def set_time_map(self, to_file: Callable[[int], int] | None, to_vod: Callable[[int], int] | None) -> None:
        """VOD ↔ 영상 파일 시각 변환(작업대의 TimeMap). None 이면 같은 값."""
        self._to_file = to_file or _ident
        self._to_vod = to_vod or _ident
        self._last_vod = None

    def set_config(self, *, pre_roll_ms: int | None = None, post_roll_ms: int | None = None,
                   heard_max_speed: float | None = None, min_coverage: float | None = None,
                   min_views: int | None = None) -> None:
        if pre_roll_ms is not None:
            self.pre_roll_ms = int(pre_roll_ms)
        if post_roll_ms is not None:
            self.post_roll_ms = int(post_roll_ms)
        if heard_max_speed is not None:
            self.heard_max_speed = float(heard_max_speed)
        if min_coverage is not None:
            self.min_coverage = float(min_coverage)
        if min_views is not None:
            self.min_views = int(min_views)

    def set_thresholds(self, values: dict | None) -> None:
        """guide-config values(또는 project.json guides[].thresholds) 의 recheck 블록을 반영한다."""
        rc = (values or {}).get("recheck") if isinstance(values, dict) else None
        if not isinstance(rc, dict):
            return
        kw = {}
        if _num(rc.get("pre_roll_sec")):
            kw["pre_roll_ms"] = int(round(rc["pre_roll_sec"] * 1000))
        if _num(rc.get("post_roll_sec")):
            kw["post_roll_ms"] = int(round(rc["post_roll_sec"] * 1000))
        if _num(rc.get("min_views_direct_likely")):
            kw["min_views"] = int(rc["min_views_direct_likely"])
        self.set_config(**kw)

    # ------------------------------------------------------------ 범위
    def window(self) -> tuple[int, int] | None:
        return self._window

    def window_for(self, chat_ms: int, utterance_end_ms: int | None) -> tuple[int, int]:
        return review_window(chat_ms, utterance_end_ms, self.pre_roll_ms, self.post_roll_ms)

    def set_window(self, start_ms: int, end_ms: int) -> None:
        """확인 범위를 바꾼다(VOD ms). 반복이 켜져 있으면 반복 범위도 바로 바꾼다. 들은 기록은 지우지 않는다."""
        a, b = max(0, int(start_ms)), int(end_ms)
        if b <= a:
            b = a + 1
        if self._window == (a, b):
            return
        self._window = (a, b)
        if self._loop_on:
            self.player.set_ab_loop(self._to_file(a), self._to_file(b))
        self.windowChanged.emit(a, b)
        self._emit_coverage(force=True)

    def set_window_for(self, chat_ms: int, utterance_end_ms: int | None) -> tuple[int, int]:
        a, b = self.window_for(chat_ms, utterance_end_ms)
        self.set_window(a, b)
        return a, b

    # ------------------------------------------------------------ 회차
    def reset(self) -> None:
        """새 대상: 회차·범위를 비우고 반복을 끈다."""
        if self._loop_on:
            self.stop_loop()
        self._passes = []
        self._window = None
        self._last_vod = None
        self._last_cov = -1.0
        self.begin_pass()

    def begin_pass(self) -> int:
        """새 듣기 회차를 시작한다. 지금 회차가 비어 있으면 새로 만들지 않고 그 번호를 돌려준다."""
        if self._passes and not self._passes[-1].ranges:
            return self._passes[-1].index
        idx = len(self._passes)
        self._passes.append(ReviewPass(idx))
        self._last_vod = None
        self.passStarted.emit(idx)
        self._emit_coverage(force=True)
        return idx

    def passes(self) -> list[ReviewPass]:
        return list(self._passes)

    def current_pass(self) -> ReviewPass | None:
        return self._passes[-1] if self._passes else None

    def pass_summaries(self, window: tuple[int, int] | None = None) -> list[dict]:
        """회차마다 video_check 값. 범위 안에서 들은 것이 없는 회차는 뺀다. 각 dict 에 'pass' 번호가 붙는다."""
        w = window or self._window
        if w is None:
            return []
        out = []
        for p in self._passes:
            s = p.summary(w[0], w[1], self.min_coverage)
            if s["watched_ranges"]:
                s["pass"] = p.index
                out.append(s)
        return out

    def heard_count(self, window: tuple[int, int] | None = None) -> int:
        """범위를 '들었다' 고 인정된 회차 수."""
        return sum(1 for s in self.pass_summaries(window) if s["audio_heard"])

    def current_coverage(self) -> float:
        p = self.current_pass()
        if p is None or self._window is None:
            return 0.0
        return coverage(p.ranges, *self._window)

    def best_coverage(self) -> float:
        if self._window is None or not self._passes:
            return 0.0
        return max(coverage(p.ranges, *self._window) for p in self._passes)

    def _emit_coverage(self, force: bool = False) -> None:
        c = self.current_coverage()
        if force or abs(c - self._last_cov) >= 0.005:
            self._last_cov = c
            self.coverageChanged.emit(c)

    # ------------------------------------------------------------ 소리·속도
    def audible(self) -> bool:
        """소리가 들리는 상태인가(음소거 아님, 음량 > 0)."""
        if self._muted:
            return False
        v = _call(self.player, "volume", 100)
        return not (isinstance(v, (int, float)) and v <= 0)

    def set_mute(self, mute: bool) -> None:
        self._muted = bool(mute)
        _call(self.player, "set_mute", None, self._muted)

    def speed(self) -> float:
        return self._speed

    def set_speed(self, x: float) -> None:
        x = float(x)
        self.player.set_speed(x)
        sp = _call(self.player, "speed", x)
        self._speed = float(sp) if isinstance(sp, (int, float)) and not isinstance(sp, bool) else x
        self.speedChanged.emit(self._speed)

    def speed_up(self) -> None:
        self.set_speed(next_speed(self._speed, +1))

    def speed_down(self) -> None:
        self.set_speed(next_speed(self._speed, -1))

    def set_audio_normalize(self, on: bool) -> bool:
        """음량 고르게(dynaudnorm). 플레이어가 못 하면 False. 켠 상태는 normalize() 로 읽어 라벨 보조 정보로 쓴다."""
        ok = bool(_call(self.player, "set_audio_normalize", False, bool(on)))
        if ok:
            self._normalize = bool(on)
            self.normalizeChanged.emit(self._normalize)
        return ok

    def normalize(self) -> bool | None:
        return self._normalize

    # ------------------------------------------------------------ 반복
    def loop_on(self) -> bool:
        return self._loop_on

    def start_loop(self, *, play: bool = True) -> bool:
        """확인 범위를 A-B 반복으로 걸고 범위 시작으로 간다. 이미 들은 것이 있으면 새 회차(다시 듣기)가 된다."""
        if self._window is None:
            return False
        cur = self.current_pass()
        if cur is not None and cur.ranges:
            self.begin_pass()
        a, b = self._window
        self.player.set_ab_loop(self._to_file(a), self._to_file(b))
        self.player.seek(self._to_file(a))
        self._last_vod = None
        if play:
            self.player.play()
        if not self._loop_on:
            self._loop_on = True
            self.loopChanged.emit(True)
        self.show_osd()
        return True

    def stop_loop(self) -> None:
        self.player.set_ab_loop(None, None)
        if self._loop_on:
            self._loop_on = False
            self.loopChanged.emit(False)

    def toggle_loop(self) -> bool:
        """`R`: 켜기·끄기. 켜졌으면 True."""
        if self._loop_on:
            self.stop_loop()
            return False
        return self.start_loop()

    # ------------------------------------------------------------ 화면 위 문구·프레임
    def set_osd_text(self, text: str, show: bool = True) -> None:
        self._osd_text = str(text or "")
        if show:
            self.show_osd()

    def osd_text(self) -> str:
        return self._osd_text

    def show_osd(self, duration_ms: int = OSD_MS) -> None:
        if self._osd_text:
            _call(self.player, "show_text", None, self._osd_text, int(duration_ms))

    def set_frame_info(self, fps: float | None, vfr: bool) -> None:
        """프레임 번호 표시 조건. 가변 프레임률(vfr)이거나 fps 를 모르면 프레임 번호를 보이지 않는다."""
        self._fps = float(fps) if _num(fps) and fps > 0 else None
        self._vfr = bool(vfr)

    def frame_text(self, file_ms: int) -> str:
        if self._vfr or not self._fps:
            return ""
        return tr("프레임 {0}").format(f"{int(int(file_ms) * self._fps / 1000):,}")

    def position_vod(self) -> int:
        return self._to_vod(int(self.player.position_ms()))

    # ------------------------------------------------------------ 신호
    def _on_playing(self, on: bool) -> None:
        self._playing = bool(on)
        if not on:
            self._last_vod = None

    def _on_position(self, file_ms: int) -> None:
        vod = self._to_vod(int(file_ms))
        last, self._last_vod = self._last_vod, vod
        if last is None or not self._playing:
            return
        d = vod - last
        if self._loop_on and self._window is not None and d < 0 and vod <= self._window[0] + 500:
            self.show_osd()      # 반복이 범위 시작으로 되감김
        if d <= 0 or d > MAX_STEP_MS * max(1.0, self._speed):
            return               # 되감기·탐색·반복 되감김: 들은 것으로 치지 않는다
        if self._speed > self.heard_max_speed + 1e-9 or not self.audible():
            return
        cur = self.current_pass()
        if cur is None:
            self.begin_pass()
            cur = self.current_pass()
        cur.add(last, vod, self._speed)
        self._emit_coverage()

    # ------------------------------------------------------------ 단축키·막대
    def install_shortcuts(self, host: QWidget, context=Qt.WidgetWithChildrenShortcut) -> list[QShortcut]:
        """host 위에 `R`(범위 반복)·`[`·`]`(속도) 를 건다(02 7-6). 텍스트 칸이 포커스면 칸이 글자를 먼저 가져간다."""
        out = []
        for key, fn in (("R", self.toggle_loop), ("[", self.speed_down), ("]", self.speed_up)):
            sc = QShortcut(QKeySequence(key), host)
            sc.setContext(context)
            sc.activated.connect(fn)
            out.append(sc)
        return out

    def controls(self, parent=None) -> "ReviewControls":
        return ReviewControls(self, parent)


class ReviewControls(QWidget):
    """플레이어 아래 막대: 위치·프레임·속도·범위 반복·음량 고르게·들은 범위 (02 7-3 그림의 둘째 줄)."""

    def __init__(self, review: ReviewPlayer, parent=None):
        super().__init__(parent)
        self.review = review
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.pos_label = QLabel("")
        self.speed_box = QComboBox()
        for s in SPEEDS:
            self.speed_box.addItem(f"x{s:g}", s)
        self.loop_btn = QPushButton(tr("7-2 범위 반복 (R)"))
        self.loop_btn.setCheckable(True)
        self.norm_btn = QPushButton(tr("음량 고르게"))
        self.norm_btn.setCheckable(True)
        self.cov_label = QLabel("")
        for w in (self.pos_label, self.speed_box, self.loop_btn, self.norm_btn, self.cov_label):
            lay.addWidget(w)
        lay.addStretch(1)
        self._sync_speed(review.speed())
        self.speed_box.activated.connect(lambda i: review.set_speed(self.speed_box.itemData(i)))
        self.loop_btn.clicked.connect(lambda _=False: review.toggle_loop())
        self.norm_btn.clicked.connect(lambda on: self._on_norm(on))
        review.speedChanged.connect(self._sync_speed)
        review.loopChanged.connect(self.loop_btn.setChecked)
        review.coverageChanged.connect(lambda _c: self._sync_cov())
        review.passStarted.connect(lambda _i: self._sync_cov())
        review.windowChanged.connect(lambda _a, _b: self._sync_cov())
        pos = getattr(review.player, "positionChanged", None)
        if pos is not None:
            pos.connect(self._sync_pos)
        self._sync_cov()

    def _on_norm(self, on: bool) -> None:
        if not self.review.set_audio_normalize(bool(on)):
            self.norm_btn.setChecked(bool(self.review.normalize()))

    def _sync_speed(self, s: float) -> None:
        i = self.speed_box.findData(s)
        if i < 0:
            self.speed_box.addItem(f"x{s:g}", s)
            i = self.speed_box.count() - 1
        self.speed_box.setCurrentIndex(i)

    def _sync_pos(self, file_ms: int) -> None:
        vod = self.review._to_vod(int(file_ms))
        fr = self.review.frame_text(file_ms)
        t = fmt_time(vod, always_hours=True)
        self.pos_label.setText(f"{t}  {fr}" if fr else t)

    def _sync_cov(self) -> None:
        r = self.review
        w = r.window()
        if w is None:
            self.cov_label.setText("")
            return
        n = r.heard_count()
        pct = int(round(r.current_coverage() * 100))
        self.cov_label.setText(tr("들은 범위 {0}% · 들은 회차 {1}").format(pct, n))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _call(obj, name: str, default, *args):
    """있으면 부르고, 없거나 실패하면 default (대역 플레이어·_MissingPlayer 대응)."""
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        LOG.debug("플레이어 %s 실패: %s", name, type(e).__name__)
        return default


__all__ = ["ReviewPlayer", "ReviewControls", "ReviewPass", "SPEEDS", "merge_ranges", "clip_ranges",
           "coverage", "review_window", "next_speed", "HEARD_MAX_SPEED", "HEARD_MIN_COVERAGE", "MIN_VIEWS"]
