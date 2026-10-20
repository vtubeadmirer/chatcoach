"""활동 구간 폼 (W1) — 1단계(구간)에서 작업대 오른쪽 칸 (02 7-3 마지막 문단, 03 6-5·10-3, 안내서 3장).

선택한 구간에 분류(5개)·확신도(3개)·근거 한 줄(필수)·'채팅 대화 활발'(게임일 때)·경계 신호(선택)를 매기고,
경계를 ±1프레임·±1초로 미세 조정하고, 30초(시작값) 미만 구간에는 합치기를 **제안만** 한다(자동으로 합치지 않음).
구간 모델은 app/core/segments.py 의 `SegmentTimeline` 이다. 이 폼은 그 위의 화면과 '디스크 판' 장부만 가진다.

작업대와의 약속 (app/ui/forms/__init__.py 공통 모양)
- `SegmentForm(save, cancel, *, retract=None)`.
  - `save(draft) -> dict`: draft = {"kind": "segment", "entity_id", "supersedes", "body"} (body = 03 6-5 segmentBody).
    작업대가 store.append_label(draft) 로 잇는다. 돌려준 레코드의 `id` 를 다음 판의 supersedes 로 쓴다.
    쪼개기로 생긴 새 구간도 entity_id 를 폼이 정해서 넘긴다(supersedes = None). store 는 호출하는 쪽이 정한 새 대상 ID 를 받는다.
  - `retract(entity_id, reason, merged_into) -> dict`: 작업대가 store.retract(entity_id, reason, merged_into=…) 로 잇는다.
    주지 않으면 철회도 save 로 보낸다: draft = {"kind": "retract", "entity_id", "supersedes", "body": {"reason", "merged_into"?}}.
    (store.append_label 은 retract 를 거부하므로, 이 경우 작업대가 kind 를 보고 store.retract 로 갈라 줘야 한다.)
  - `cancel()`: Esc. 폼은 근거 칸의 고치던 글자를 버리고 이 콜백을 부른다(작업대가 포커스를 가져간다).
- `load_context(duration_ms=…, records=…, …)`: 현재 유효 segment 레코드(자기 세션)와 프레임률·시작값을 받는다.
- `add_boundary(vod_ms)`: 작업대가 'B' 키(02 7-6)에서 부른다. 폼 자신은 'B' 를 잡지 않는다(수정자 없는 글자 키는 작업대 몫).
- `segments_for_timeline()`: timeline.set_segments 에 그대로 넘길 dict 목록(03 6-5 모양 + entity_id). 안 매긴 구간은 category None.
  모델이 바뀔 때마다 `segmentsChanged` 시그널이 나간다. 작업대는 그때 타임라인을 다시 그린다.

실행 취소는 두 층이다 (03 10-1·10-3, 02 7-6 'Ctrl+Z 1단계: 마커 실행 취소')
1. **모델 층 (화면, 디스크에 안 씀).** 분류·확신도·근거·경계 조작은 곧바로 `SegmentTimeline` 에 들어가고 undo 스택(200단계)에
   쌓인다. `Ctrl+Z` / `Ctrl+Y`(또는 `Ctrl+Shift+Z`) 는 이 층만 되돌린다. 저장하기 전에는 아무 줄도 쓰지 않는다.
2. **기록 층 (디스크, 추가만).** `Ctrl+Enter`(저장)는 '지금 모델' 과 '디스크의 현재 판' 의 차이를 10-3 표대로 쓴다.
   - 다 매긴 구간이 디스크에 없으면 새 대상(쪼개기면 split_from 이 붙어 있다), 내용이 다르면 같은 entity_id 의 새 판.
   - 디스크에 있는데 모델에서 사라진 구간은 철회(retract)한다. 합치기로 사라졌으면 reason=merged + merged_into(남은 구간),
     그 밖(쪼개기를 모델 층에서 되돌림 등)은 reason=undo.
   - 디스크에 있는데 모델에서 '안 매긴' 상태가 된 구간(모델 층 undo 로 분류 전으로 돌아감)도 reason=undo 로 철회한다.
   - 철회된 대상이 모델에 다시 나타나면(합치기를 모델 층에서 되돌림) 철회 줄을 supersedes 하는 새 판으로 되살린다.
   그러니 '저장한 뒤 Ctrl+Z' 는 모델만 되돌리고, 다음 저장이 그 되돌림을 새 판·철회 줄로 남긴다. 기록은 지우지 않는다.
   store.retract_last(2·3단계의 Ctrl+Z, 기록 층 한 줄 되돌리기)는 이 폼이 부르지 않는다. 그걸 쓰면 디스크 판이 폼의 장부와
   어긋나므로, 작업대는 그 뒤 load_context 를 다시 불러야 한다.

영상 밖 (08 D-10 3): load_context(media_coverage=…) 로 영상이 덮는 VOD 범위를 받으면, 그 범위와 조금도 겹치지 않는 구간은
저장하지 않는다(보고 들을 수 없는 자리는 매길 수 없다). 저장 계획(plan)의 errors 에 이유를 넣어 저장 전체를 막고 화면에 보인다.
일부만 걸친 구간은 저장한다(보이는 부분으로 판단). 철회는 막지 않는다. coverage None = 검사 안 함, [] = 영상 없음(모두 막음).
분류만 고르고 확신도·근거를 비운 구간은 기록으로 낼 수 없지만 '저장 안 한 변경' 이다(has_unsaved, 11 4-2 의 5).

블라인드: 이 폼은 load_context 로 받은 레코드만 그린다. session_id 를 주면 다른 세션의 줄은 버리고 건수만 알린다.
등급·엔진·AI 는 이 폼과 관계가 없다(구간 단계).

단축키 (02 7-6 원칙: 수정자 없는 글자·숫자 키는 라벨 값을 바꾸지 않는다). 키 표는 아래 상수 `SHORTCUTS`.
- 분류 `Alt+J` 저스트채팅 · `Alt+G` 게임 · `Alt+S` 노래 · `Alt+O` 기타 · `Alt+U` 판단 불가
- 확신도 `Alt+1` 확실함 · `Alt+2` 아마도 · `Alt+3` 판단 불가
- 경계 미세 조정 `Alt+,` / `Alt+.` ±1프레임(가변 프레임률이면 0.1초), `Alt+←` / `Alt+→` ±1초.
  `Alt+Shift` 는 Windows 입력 언어 전환이라 쓰지 않는다. 작업대의 `,`/`.`(재생 1프레임)·`Shift+←/→`(1초)와 짝을 맞췄다.
- `Ctrl+T` 근거 칸에 현재 시각, `Ctrl+Enter` 저장, `Esc` 취소, `Ctrl+Z` / `Ctrl+Y` 모델 층 실행 취소·다시 실행.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ...core.segments import (CATEGORIES, CONFIDENCES, RATIONALE_MAX, MergeSuggestion, Segment, SegmentError,
                              SegmentOverlapError, SegmentThresholds, SegmentTimeline, thresholds_from_guide)
from ...core.timecode import fmt_display
from ...i18n import tr

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------- 표시명 (03 5-1, 5-3, 5-10)
# core/schema.py 가 생기면 그쪽 표시명을 쓴다 (10 5-2: 지금은 timeline·chat_panel 처럼 모듈 안 상수).
CATEGORY_NAMES = {
    "just_chatting": "저스트채팅",
    "game": "게임",
    "singing": "노래",
    "other": "기타",
    "undeterminable": "판단 불가",
}
CONFIDENCE_NAMES = {"certain": "확실함", "probably": "아마도", "undeterminable": "판단 불가"}
SIGNAL_NAMES = [(None, "미입력"), ("screen", "화면"), ("audio", "소리"), ("speech", "말"),
                ("action", "행동"), ("unknown", "모름")]

# ---------------------------------------------------------------------------- 단축키 (02 7-6)
CATEGORY_KEYS = {"just_chatting": "Alt+J", "game": "Alt+G", "singing": "Alt+S",
                 "other": "Alt+O", "undeterminable": "Alt+U"}
CONFIDENCE_KEYS = {"certain": "Alt+1", "probably": "Alt+2", "undeterminable": "Alt+3"}
FRAME_KEYS = ("Alt+,", "Alt+.")
SECOND_KEYS = ("Alt+Left", "Alt+Right")
SAVE_KEYS = ("Ctrl+Return", "Ctrl+Enter")
UNDO_KEY = "Ctrl+Z"
REDO_KEYS = ("Ctrl+Y", "Ctrl+Shift+Z")
TIME_KEY = "Ctrl+T"
CANCEL_KEY = "Escape"

VFR_STEP_MS = 100   # 가변 프레임률(fps 모름)일 때 '1프레임' 대신 0.1초 (02 7-6)
_KEEP = object()    # load_context(media_coverage=…) 를 넘기지 않음(None 은 '검사 안 함' 이라 따로 둔다)


def _norm_coverage(coverage) -> list[tuple[int, int]] | None:
    """[(start, end)] 또는 {start_ms, end_ms} 목록 → 정렬·합친 목록. None 은 None. 모양이 틀린 칸은 버린다."""
    if coverage is None:
        return None
    spans = []
    for r in coverage:
        if isinstance(r, Mapping):
            r = (r.get("start_ms"), r.get("end_ms"))
        try:
            a, b = r
        except (TypeError, ValueError):
            continue
        if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and not isinstance(b, bool) and b > a:
            spans.append((a, b))
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def overlaps_media(start_ms: int, end_ms: int, coverage: list[tuple[int, int]] | None) -> bool:
    """[start, end) 가 영상 범위와 조금이라도 겹치는가. coverage None(모름)이면 True."""
    if coverage is None:
        return True
    return any(a < end_ms and start_ms < b for a, b in coverage)


@dataclass
class _DiskState:
    """디스크에 있는 대상의 현재 머리. body None = 철회됨(머리가 retract 줄)."""
    head_id: str
    body: dict | None


@dataclass(frozen=True)
class SavePlan:
    """저장(Ctrl+Enter)이 쓸 것. writes = segment draft 목록, retracts = (entity_id, reason, merged_into, head_id).
    errors 가 있으면 아무것도 쓰지 않는다.

    쓰는 순서(save): retracts 먼저, 그다음 writes 를 이 순서대로. writes 는 줄어드는 판(쪼개기·경계 옮김의 작아지는 쪽)이
    늘어나는 판보다 먼저 오게 정렬돼 있다(_order_writes). 줄마다 따로 fsync 하므로 중간에 실패·전원 꺼짐이 있어도 디스크에는
    '빈틈'(안 매긴 시간)만 남고 '겹침' 은 생기지 않는다 — 겹침은 다시 열면 읽기 전용이 되는 고칠 수 없는 상태다(segments
    SegmentOverlapError)."""
    writes: tuple[dict, ...]
    retracts: tuple[tuple[str, str, str | None, str], ...]
    errors: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not self.writes and not self.retracts


def _fmt_len(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return tr("{0}시간 {1}분 {2}초").format(h, m, s)
    if m:
        return tr("{0}분 {1}초").format(m, s)
    return tr("{0}초").format(s)


class SegmentForm(QWidget):
    """활동 구간 폼. 시그널:
    - segmentsChanged(): 모델(구간 경계·속성)이 바뀜 → 작업대가 segments_for_timeline() 으로 타임라인을 다시 그린다.
    - selectionChanged(str entity_id, int start_ms): 고른 구간이 바뀜 (작업대가 탐색할지 정한다).
    - recordsSaved(list): 저장으로 쓴 레코드들(save·retract 콜백이 돌려준 것).
    - message(str): 상태 문장 (작업대 상태 줄용). 폼 안 문장 칸에도 같은 문장이 나온다.
    """

    segmentsChanged = Signal()
    selectionChanged = Signal(str, int)
    recordsSaved = Signal(list)
    message = Signal(str)

    def __init__(self, save: Callable[[dict], dict], cancel: Callable[[], None], *,
                 retract: Callable[[str, str, str | None], dict] | None = None,
                 inherit_on_split: bool = False,
                 shortcut_context: Qt.ShortcutContext = Qt.WindowShortcut,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._save_cb = save
        self._cancel_cb = cancel
        self._retract_cb = retract
        self.inherit_on_split = bool(inherit_on_split)
        self._model: SegmentTimeline | None = None
        self._gen: Callable[[], str] = lambda: str(uuid.uuid4())
        self._disk: dict[str, _DiskState] = {}
        self._sel: str | None = None
        self._pos = 0
        self._fps: float | None = None
        self._frame_origin = 0
        self._thr = SegmentThresholds()
        self._read_only = True
        self._problems: list[str] = []
        self._edge = "start"            # 미세 조정할 경계: 고른 구간의 'start' 또는 'end'
        self.follow_position = True     # 재생 위치가 다른 구간으로 가면 그 구간을 고른다(근거를 고치던 중이면 안 함)
        self._updating = False
        self._coverage: list[tuple[int, int]] | None = None   # 영상이 덮는 VOD 범위(D-10 3). None = 검사 안 함
        self._build()
        self._install_shortcuts(shortcut_context)
        self._refresh()

    # ================================================================== 화면 만들기
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)          # 1366×768 에서는 세로로 스크롤 (02 7-1)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        title = QLabel(tr("활동 구간"))
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        v.addWidget(title)

        # 구간 이동 + 머리 문장
        nav = QHBoxLayout()
        self.btn_prev = QPushButton(tr("◀ 이전 구간"))
        self.btn_next = QPushButton(tr("다음 구간 ▶"))
        self.btn_prev.clicked.connect(lambda: self.select_relative(-1))
        self.btn_next.clicked.connect(lambda: self.select_relative(1))
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.btn_next)
        nav.addStretch(1)
        v.addLayout(nav)
        self.lbl_head = QLabel()
        self.lbl_head.setWordWrap(True)
        self.lbl_head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_head)

        # 분류
        v.addWidget(QLabel(tr("분류")))
        grid = QGridLayout()
        self.grp_cat = QButtonGroup(self)
        self.grp_cat.setExclusive(True)
        self.cat_buttons: dict[str, QPushButton] = {}
        for i, cat in enumerate(CATEGORIES):
            b = QPushButton(f"{tr(CATEGORY_NAMES[cat])}  {CATEGORY_KEYS[cat]}")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, c=cat: self.set_category(c))
            self.grp_cat.addButton(b)
            self.cat_buttons[cat] = b
            grid.addWidget(b, i // 2, i % 2)
        v.addLayout(grid)

        self.chk_active = QCheckBox(tr("채팅 대화 활발 (게임일 때)"))
        self.chk_active.clicked.connect(lambda checked: self.set_active_chat(bool(checked)))
        v.addWidget(self.chk_active)

        # 확신도
        v.addWidget(QLabel(tr("확신도")))
        row = QHBoxLayout()
        self.grp_conf = QButtonGroup(self)
        self.grp_conf.setExclusive(True)
        self.conf_buttons: dict[str, QPushButton] = {}
        for conf in CONFIDENCES:
            b = QPushButton(f"{tr(CONFIDENCE_NAMES[conf])}  {CONFIDENCE_KEYS[conf]}")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, c=conf: self.set_confidence(c))
            self.grp_conf.addButton(b)
            self.conf_buttons[conf] = b
            row.addWidget(b)
        v.addLayout(row)

        # 근거 한 줄
        v.addWidget(QLabel(tr("근거 한 줄 (필수, 시각을 넣어 적기)")))
        rrow = QHBoxLayout()
        self.ed_rationale = QLineEdit()
        self.ed_rationale.setMaxLength(RATIONALE_MAX)
        self.ed_rationale.setPlaceholderText(tr("예: 12:03 게임 화면으로 바뀜"))
        self.ed_rationale.editingFinished.connect(self._on_rationale_finished)
        self.ed_rationale.textEdited.connect(lambda _t: self._update_save_state())
        self.btn_time = QPushButton(tr("현재 시각 Ctrl+T"))
        self.btn_time.clicked.connect(self.insert_time)
        rrow.addWidget(self.ed_rationale, 1)
        rrow.addWidget(self.btn_time)
        v.addLayout(rrow)

        # 경계 신호 (선택, 03 5-10 segment.start_signal)
        srow = QHBoxLayout()
        srow.addWidget(QLabel(tr("시작 경계 신호 (선택)")))
        self.cmb_signal = QComboBox()
        for val, name in SIGNAL_NAMES:
            self.cmb_signal.addItem(tr(name), val)
        self.cmb_signal.activated.connect(lambda i: self.set_start_signal(self.cmb_signal.itemData(i)))
        srow.addWidget(self.cmb_signal, 1)
        v.addLayout(srow)

        # 경계 미세 조정
        v.addWidget(QLabel(tr("경계 미세 조정")))
        erow = QHBoxLayout()
        self.rad_start = QRadioButton(tr("시작 경계"))
        self.rad_end = QRadioButton(tr("끝 경계"))
        self.rad_start.setChecked(True)
        self.rad_start.toggled.connect(lambda on: on and self.choose_edge("start"))
        self.rad_end.toggled.connect(lambda on: on and self.choose_edge("end"))
        erow.addWidget(self.rad_start)
        erow.addWidget(self.rad_end)
        erow.addStretch(1)
        v.addLayout(erow)
        nrow = QHBoxLayout()
        self.btn_m1s = QPushButton(tr("−1초"))
        self.btn_m1f = QPushButton(tr("−1프레임"))
        self.btn_p1f = QPushButton(tr("+1프레임"))
        self.btn_p1s = QPushButton(tr("+1초"))
        self.btn_m1s.setToolTip(SECOND_KEYS[0])
        self.btn_m1f.setToolTip(FRAME_KEYS[0])
        self.btn_p1f.setToolTip(FRAME_KEYS[1])
        self.btn_p1s.setToolTip(SECOND_KEYS[1])
        self.btn_m1s.clicked.connect(lambda: self.nudge_seconds(-1))
        self.btn_m1f.clicked.connect(lambda: self.nudge_frames(-1))
        self.btn_p1f.clicked.connect(lambda: self.nudge_frames(1))
        self.btn_p1s.clicked.connect(lambda: self.nudge_seconds(1))
        for b in (self.btn_m1s, self.btn_m1f, self.btn_p1f, self.btn_p1s):
            nrow.addWidget(b)
        v.addLayout(nrow)
        brow = QHBoxLayout()
        self.btn_edge_here = QPushButton(tr("경계를 지금 위치로"))
        self.btn_edge_here.clicked.connect(lambda: self.move_edge_to(self._pos))
        self.btn_split = QPushButton(tr("지금 위치에서 쪼개기 (B)"))
        self.btn_split.clicked.connect(lambda: self.add_boundary(self._pos))
        brow.addWidget(self.btn_edge_here)
        brow.addWidget(self.btn_split)
        v.addLayout(brow)

        # 합치기 (제안 + 직접)
        mrow = QHBoxLayout()
        self.btn_merge_prev = QPushButton(tr("앞 구간에 합치기"))
        self.btn_merge_next = QPushButton(tr("뒤 구간에 합치기"))
        self.btn_merge_prev.clicked.connect(lambda: self.merge_selected("prev"))
        self.btn_merge_next.clicked.connect(lambda: self.merge_selected("next"))
        mrow.addWidget(self.btn_merge_prev)
        mrow.addWidget(self.btn_merge_next)
        v.addLayout(mrow)
        self.lbl_suggest = QLabel()
        self.lbl_suggest.setWordWrap(True)
        v.addWidget(self.lbl_suggest)
        self.btn_next_short = QPushButton(tr("다음 짧은 구간으로"))
        self.btn_next_short.clicked.connect(self.select_next_short)
        v.addWidget(self.btn_next_short)

        # 저장 · 취소 · 실행 취소
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        v.addWidget(line)
        self.lbl_pending = QLabel()
        self.lbl_pending.setWordWrap(True)
        v.addWidget(self.lbl_pending)
        arow = QHBoxLayout()
        self.btn_undo = QPushButton(tr("실행 취소 Ctrl+Z"))
        self.btn_redo = QPushButton(tr("다시 실행 Ctrl+Y"))
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)
        arow.addWidget(self.btn_undo)
        arow.addWidget(self.btn_redo)
        v.addLayout(arow)
        crow = QHBoxLayout()
        self.btn_save = QPushButton(tr("저장 Ctrl+Enter"))
        self.btn_cancel = QPushButton(tr("취소 Esc"))
        self.btn_save.clicked.connect(self.save)
        self.btn_cancel.clicked.connect(self.cancel_edit)
        crow.addWidget(self.btn_save)
        crow.addWidget(self.btn_cancel)
        v.addLayout(crow)

        self.lbl_msg = QLabel()
        self.lbl_msg.setWordWrap(True)
        self.lbl_msg.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        v.addWidget(self.lbl_msg)
        v.addStretch(1)

    def _install_shortcuts(self, context: Qt.ShortcutContext) -> None:
        """QShortcut 표. 키 문자열 → QShortcut (시험·통합 담당이 찾을 수 있게 `self.shortcuts` 에 둔다).
        WindowShortcut: 폼이 보이는 동안만 동작한다. 1단계에만 폼이 보이므로 2단계 Ctrl+Z(기록 철회)와 겹치지 않는다."""
        self.shortcuts: dict[str, QShortcut] = {}

        def sc(key: str, fn) -> None:
            s = QShortcut(QKeySequence(key), self)
            s.setContext(context)
            s.activated.connect(fn)
            self.shortcuts[key] = s

        for cat, key in CATEGORY_KEYS.items():
            sc(key, lambda c=cat: self.set_category(c))
        for conf, key in CONFIDENCE_KEYS.items():
            sc(key, lambda c=conf: self.set_confidence(c))
        sc(FRAME_KEYS[0], lambda: self.nudge_frames(-1))
        sc(FRAME_KEYS[1], lambda: self.nudge_frames(1))
        sc(SECOND_KEYS[0], lambda: self.nudge_seconds(-1))
        sc(SECOND_KEYS[1], lambda: self.nudge_seconds(1))
        for k in SAVE_KEYS:
            sc(k, self.save)
        sc(UNDO_KEY, self.undo)
        for k in REDO_KEYS:
            sc(k, self.redo)
        sc(TIME_KEY, self.insert_time)
        sc(CANCEL_KEY, self.cancel_edit)

    # ================================================================== 작업대 상태 받기
    def load_context(self, *, duration_ms: int, records: Iterable[Mapping] = (), session_id: str | None = None,
                     fps: float | None = None, frame_origin_ms: int = 0,
                     thresholds: SegmentThresholds | Mapping | None = None,
                     position_ms: int = 0, read_only: bool = False,
                     new_id: Callable[[], str] | None = None, media_coverage=_KEEP) -> bool:
        """작업대 상태를 받아 모델을 새로 만든다. 모델 층 undo 스택은 비워진다. 성공하면 True.

        - records: 이 세션의 현재 유효 segment 레코드(봉투 포함: id·entity_id·session_id·body). 다른 kind 는 무시한다.
          session_id 를 주면 그 세션이 아닌 줄은 버린다(블라인드, 03 12-1). 재라벨 세션은 빈 목록으로 시작한다(03 12-2).
        - fps·frame_origin_ms: ±1프레임 격자(타임라인 set_fps 와 같은 값). fps None 이면 가변 프레임률로 보고 0.1초.
        - thresholds: SegmentThresholds 또는 guide-config 의 `thresholds.segment` 절(초 단위). 없으면 v0.1 시작값.
        - 겹치는 기록·형식이 틀린 기록이면 모델을 만들지 않고 읽기 전용으로 둔다(문제는 `problems`, 작업대가 '겹침/빈틈 고치기').
        - media_coverage: 영상이 덮는 VOD 범위 [(start_ms, end_ms)] (D-10 3). 넘기지 않으면 그대로, None 은 검사 안 함,
          [] 는 영상 없음(어떤 구간도 저장하지 않음).
        """
        if media_coverage is not _KEEP:
            self._coverage = _norm_coverage(media_coverage)
        self._problems = []
        self._model = None
        self._disk = {}
        self._sel = None
        self._fps = float(fps) if fps and fps > 0 else None
        self._frame_origin = int(frame_origin_ms)
        self._pos = int(position_ms)
        if thresholds is None:
            self._thr = SegmentThresholds()
        elif isinstance(thresholds, SegmentThresholds):
            self._thr = thresholds
        else:
            self._thr = thresholds_from_guide(thresholds)
        gen = new_id or (lambda: str(uuid.uuid4()))
        self._gen = gen
        mine: list[Mapping] = []
        dropped = 0
        for r in records or ():
            if not isinstance(r, Mapping) or r.get("kind") != "segment":
                continue
            if session_id is not None and r.get("session_id") != session_id:
                dropped += 1
                continue
            mine.append(r)
        if dropped:
            self._problems.append(tr("다른 세션의 구간 기록 {0}건은 보이지 않습니다").format(dropped))
        ok = True
        try:
            model = SegmentTimeline.from_records(int(duration_ms), mine, gen)
        except SegmentOverlapError as e:
            self._problems.append(tr("겹치는 구간 기록 {0}쌍 — '겹침/빈틈 고치기' 가 필요합니다").format(len(e.overlaps)))
            ok = False
        except (SegmentError, TypeError) as e:
            self._problems.append(tr("구간 기록을 읽지 못했습니다: {0}").format(e))
            ok = False
        if ok:
            for r in mine:
                eid, rid = r.get("entity_id"), r.get("id")
                if not isinstance(rid, str):
                    self._problems.append(tr("판 id 가 없는 구간 기록이 있습니다 — 읽기 전용으로 엽니다"))
                    read_only = True
                    continue
                # 비교 기준은 모델이 내는 body (잘린 구간의 out_of_media 같은 표시 차이로 '바뀜' 이 되지 않게)
                self._disk[eid] = _DiskState(rid, model.get(eid).body())
            self._model = model
            seg = model.segment_at(min(max(self._pos, 0), model.duration_ms - 1))
            self._sel = seg.id if seg else model.segments[0].id
        self._read_only = bool(read_only) or not ok
        self._edge = "start"
        self._updating = True
        self.rad_start.setChecked(True)
        self._updating = False
        self._refresh()
        self.segmentsChanged.emit()
        if self._problems:
            self._say(" · ".join(self._problems))
        else:
            self._say("")
        return ok

    def set_position(self, vod_ms: int) -> None:
        """재생 위치(VOD ms). follow_position 이면 위치가 다른 구간으로 가면 그 구간을 고른다(근거를 고치던 중이면 안 함)."""
        self._pos = int(vod_ms)
        if self._model is None or not self.follow_position or self._field_dirty():
            return
        seg = self._model.segment_at(self._pos) if 0 <= self._pos < self._model.duration_ms else None
        if seg is not None and seg.id != self._sel:
            self._select(seg.id, emit=True)

    # ================================================================== 읽기
    @property
    def model(self) -> SegmentTimeline | None:
        return self._model

    @property
    def selected_id(self) -> str | None:
        return self._sel

    @property
    def selected(self) -> Segment | None:
        if self._model is None or self._sel is None:
            return None
        try:
            return self._model.get(self._sel)
        except KeyError:
            return None

    @property
    def problems(self) -> list[str]:
        return list(self._problems)

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def buffer_ms(self) -> int:
        """경계 완충(시작값 10초). timeline.set_segments(…, buffer_ms=form.buffer_ms)."""
        return self._thr.buffer_ms

    @property
    def min_length_ms(self) -> int:
        return self._thr.min_length_ms

    @property
    def media_coverage(self) -> list[tuple[int, int]] | None:
        return None if self._coverage is None else list(self._coverage)

    def set_media_coverage(self, coverage) -> None:
        """영상 범위만 바꾼다(영상 연결·교체 뒤). 모델·undo 스택은 그대로다. None = 검사 안 함, [] = 영상 없음."""
        self._coverage = _norm_coverage(coverage)
        self._refresh()

    def outside_media(self, seg: Segment | None = None) -> bool:
        """그 구간(기본: 고른 구간)이 영상 범위와 조금도 겹치지 않는가(D-10 3 — 저장 불가)."""
        s = seg if seg is not None else self.selected
        if s is None:
            return False
        a, b = s.record_span()
        return not overlaps_media(a, b, self._coverage)

    def _media_error(self, body: Mapping) -> str | None:
        a, b = body.get("start_ms"), body.get("end_ms")
        if not (isinstance(a, int) and isinstance(b, int)) or overlaps_media(a, b, self._coverage):
            return None
        if not self._coverage:
            return tr("영상이 연결되지 않아 구간을 저장할 수 없습니다 — 먼저 영상을 연결하세요 (보고 들을 수 없는 자리는 매기지 않음)")
        cov = ", ".join(f"{fmt_display(x)}~{fmt_display(y)}" for x, y in self._coverage[:3])
        return tr("구간 {0}~{1} 은 영상이 덮지 않는 자리라 저장할 수 없습니다 (영상 범위 {2}). 경계를 영상 안으로 옮기거나 "
                  "Ctrl+Z 로 분류를 되돌리세요").format(fmt_display(a), fmt_display(b), cov)

    def partial_count(self) -> int:
        """분류는 골랐지만 확신도·근거가 빠져 기록으로 낼 수 없는 구간 수(저장 안 한 변경, 11 4-2 의 5)."""
        if self._model is None:
            return 0
        return sum(1 for s in self._model.segments if s.is_labeled and not s.is_complete)

    def segments_for_timeline(self) -> list[dict]:
        """timeline.set_segments 모양(03 6-5 body 필드 + entity_id). 모델의 모든 구간(안 매긴 구간은 category None),
        시각은 타임라인에 보이는(영상 길이로 자른) 값. 영상 밖 보존 구간(outside)은 넣지 않는다."""
        if self._model is None:
            return []
        out = []
        for s in self._model.segments:
            d = s.body()
            d["start_ms"], d["end_ms"] = s.start_ms, s.end_ms
            d["entity_id"] = s.id
            out.append(d)
        return out

    def merge_suggestions(self) -> list[MergeSuggestion]:
        """min_length_ms(시작값 30초) 미만 구간의 합치기 제안. 자동으로 합치지 않는다(03 6-5)."""
        if self._model is None:
            return []
        return self._model.short_segments(self._thr.min_length_ms)

    # ================================================================== 고르기
    def select_segment(self, seg_id: str) -> bool:
        if self._model is None:
            return False
        try:
            self._model.index_of(seg_id)
        except KeyError:
            return False
        self._commit_field()
        self._select(seg_id, emit=True)
        return True

    def select_at(self, vod_ms: int) -> bool:
        if self._model is None:
            return False
        seg = self._model.segment_at(int(vod_ms)) if 0 <= int(vod_ms) < self._model.duration_ms else None
        return seg is not None and self.select_segment(seg.id)

    def select_relative(self, step: int) -> bool:
        if self._model is None or self._sel is None:
            return False
        i = self._model.index_of(self._sel) + int(step)
        if not 0 <= i < len(self._model):
            return False
        return self.select_segment(self._model.segments[i].id)

    def select_next_short(self) -> bool:
        """고른 구간 뒤의 다음 짧은 구간(없으면 처음부터)을 고른다."""
        sug = self.merge_suggestions()
        if not sug or self._model is None:
            return False
        cur = self._model.index_of(self._sel) if self._sel else -1
        order = [self._model.index_of(s.segment_id) for s in sug]
        nxt = next((i for i in order if i > cur), order[0])
        return self.select_segment(self._model.segments[nxt].id)

    def _select(self, seg_id: str | None, *, emit: bool) -> None:
        changed = seg_id != self._sel
        self._sel = seg_id
        self._refresh()
        if emit and changed and seg_id is not None:
            s = self.selected
            self.selectionChanged.emit(seg_id, s.start_ms if s else 0)

    # ================================================================== 속성 고치기 (모델 층)
    def _editable(self) -> bool:
        if self._model is None:
            self._say(tr("구간 기록이 열려 있지 않습니다"))
            return False
        if self._read_only:
            self._say(tr("읽기 전용입니다"))
            return False
        if self.selected is None:
            self._say(tr("고른 구간이 없습니다"))
            return False
        return True

    def _apply(self, fn) -> bool:
        """모델 조작 하나. 거절되면 문장을 띄우고 False. 모델은 그대로다."""
        try:
            edit = fn()
        except SegmentError as e:
            self._say(str(e))
            self._refresh()
            return False
        except KeyError:
            self._say(tr("구간을 찾지 못했습니다"))
            self._refresh()
            return False
        if edit is not None and not edit.empty:
            self._after_model_change()
        else:
            self._refresh()
        return True

    def set_category(self, category: str) -> bool:
        if category not in CATEGORIES or not self._editable():
            self._refresh()
            return False
        self._commit_field()
        ok = self._apply(lambda: self._model.set_props(self._sel, category=category))
        if ok:
            self._say(tr("분류: {0}").format(tr(CATEGORY_NAMES[category])))
        return ok

    def set_confidence(self, confidence: str) -> bool:
        if confidence not in CONFIDENCES or not self._editable():
            self._refresh()
            return False
        self._commit_field()
        ok = self._apply(lambda: self._model.set_props(self._sel, confidence=confidence))
        if ok:
            self._say(tr("확신도: {0}").format(tr(CONFIDENCE_NAMES[confidence])))
        return ok

    def set_rationale(self, text: str) -> bool:
        """근거 한 줄(필수). 빈 글은 받지 않는다(지우려면 모델 층 실행 취소). 앞뒤 공백은 지운다."""
        if not self._editable():
            return False
        t = (text or "").strip()
        if not t:
            self._say(tr("근거 한 줄은 비울 수 없습니다"))
            self._refresh()
            return False
        if len(t) > RATIONALE_MAX:
            self._say(tr("근거는 {0}자까지입니다").format(RATIONALE_MAX))
            return False
        return self._apply(lambda: self._model.set_props(self._sel, rationale=t))

    def set_active_chat(self, on: bool | None) -> bool:
        """'채팅 대화 활발' — 게임 구간에만 (03 6-5, 스키마가 강제)."""
        s = self.selected
        if not self._editable():
            self._refresh()
            return False
        if s.category != "game" and on is not None:
            self._say(tr("'채팅 대화 활발' 은 게임 구간에만 표시합니다"))
            self._refresh()
            return False
        self._commit_field()
        return self._apply(lambda: self._model.set_props(self._sel, active_chat=on))

    def set_start_signal(self, signal: str | None) -> bool:
        if not self._editable():
            self._refresh()
            return False
        self._commit_field()
        return self._apply(lambda: self._model.set_props(self._sel, start_signal=signal))

    def insert_time(self) -> None:
        """Ctrl+T: 근거 칸 커서 자리에 현재 시각(h:mm:ss.s)을 넣는다. 모델에는 칸을 떠날 때(또는 저장 때) 들어간다."""
        if self._model is None or self._read_only:
            return
        self.ed_rationale.insert(fmt_display(self._pos) + " ")
        self.ed_rationale.setFocus(Qt.ShortcutFocusReason)
        self._update_save_state()

    def _field_dirty(self) -> bool:
        s = self.selected
        return s is not None and self.ed_rationale.text().strip() != s.rationale

    def _commit_field(self) -> None:
        """근거 칸의 글자를 모델에 넣는다(비었으면 모델 값으로 되돌림). 다른 조작 앞에 부른다."""
        if self._updating or not self._field_dirty() or self._read_only or self._model is None:
            return
        t = self.ed_rationale.text().strip()
        if t:
            self._apply(lambda: self._model.set_props(self._sel, rationale=t))
        else:
            self._refresh()

    def _on_rationale_finished(self) -> None:
        if self._updating:
            return
        self._commit_field()

    # ================================================================== 경계 (모델 층)
    def add_boundary(self, vod_ms: int) -> bool:
        """'B' (02 7-6): vod_ms 에 경계를 넣어 그 자리 구간을 쪼갠다(10-3). 뒤쪽 새 구간을 고른다.

        새 구간은 기본으로 분류를 물려받지 않는다(inherit_on_split=False): 라벨러가 새 활동의 분류·근거를 직접 매기게
        하려는 것이다. 매기기 전까지 그 구간은 '아직 안 매긴 시간' 이라 저장되지 않는다(03 6-5).
        """
        if self._model is None or self._read_only:
            self._say(tr("읽기 전용입니다") if self._model is not None else tr("구간 기록이 열려 있지 않습니다"))
            return False
        self._commit_field()
        t = int(vod_ms)
        try:
            self._model.add_boundary(t, inherit=self.inherit_on_split)
        except SegmentError as e:
            self._say(str(e))
            return False
        seg = self._model.segment_at(t)
        self._sel = seg.id if seg else self._sel
        self._after_model_change(emit_selection=True)
        self._say(tr("경계 추가: {0}").format(fmt_display(t)))
        return True

    def choose_edge(self, edge: str) -> None:
        """미세 조정할 경계: 'start'(고른 구간의 시작) 또는 'end'(끝)."""
        if edge not in ("start", "end"):
            raise ValueError(edge)
        self._edge = edge
        if not self._updating:
            self._updating = True
            (self.rad_start if edge == "start" else self.rad_end).setChecked(True)
            self._updating = False
        self._refresh()

    def _edge_index(self) -> int | None:
        """고른 구간의 고를 경계 → SegmentTimeline.boundaries 의 번호. 영상 처음·끝이면 None."""
        if self._model is None or self._sel is None:
            return None
        try:
            i = self._model.index_of(self._sel)
        except KeyError:
            return None
        if self._edge == "start":
            return i - 1 if i > 0 else None
        return i if i < len(self._model) - 1 else None

    def edge_ms(self) -> int | None:
        k = self._edge_index()
        return None if k is None else self._model.boundaries[k]

    def frame_step_ms(self, t_ms: int, n: int) -> int:
        """t 에서 n 프레임 옮긴 시각. 고정 프레임률이면 프레임 격자(frame_origin_ms 기준)에 붙이고, 아니면 0.1초씩."""
        if not self._fps:
            return int(t_ms) + int(n) * VFR_STEP_MS
        f = round((int(t_ms) - self._frame_origin) * self._fps / 1000.0)
        return self._frame_origin + int(round((f + int(n)) * 1000.0 / self._fps))

    def move_edge_to(self, t_ms: int) -> bool:
        """고른 경계를 t 로 옮긴다(10-3 경계 이동). 이웃 경계와 너무 가까우면 거절."""
        if self._model is None or self._read_only:
            return False
        k = self._edge_index()
        if k is None:
            self._say(tr("움직일 경계가 없습니다 (영상 처음·끝)"))
            return False
        self._commit_field()
        ok = self._apply(lambda: self._model.move_boundary(k, int(t_ms)))
        if ok:
            self._say(tr("경계: {0}").format(fmt_display(self._model.boundaries[k])))
        return ok

    def nudge_frames(self, n: int) -> bool:
        t = self.edge_ms()
        if t is None:
            self._say(tr("움직일 경계가 없습니다 (영상 처음·끝)"))
            return False
        return self.move_edge_to(self.frame_step_ms(t, n))

    def nudge_seconds(self, n: int) -> bool:
        t = self.edge_ms()
        if t is None:
            self._say(tr("움직일 경계가 없습니다 (영상 처음·끝)"))
            return False
        return self.move_edge_to(t + int(n) * 1000)

    def merge_selected(self, neighbor: str) -> bool:
        """고른 구간을 이웃('prev'|'next')에 합친다. 이웃이 남고(분류·근거도 이웃 것) 고른 구간은 저장 때 merged 로 철회된다."""
        if not self._editable():
            return False
        self._commit_field()
        sid = self._sel
        i = self._model.index_of(sid)
        j = i - 1 if neighbor == "prev" else i + 1
        if not 0 <= j < len(self._model):
            self._say(tr("합칠 이웃 구간이 없습니다"))
            return False
        survivor = self._model.segments[j].id
        try:
            self._model.merge_into(sid, neighbor)
        except SegmentError as e:
            self._say(str(e))
            return False
        self._sel = survivor
        self._after_model_change(emit_selection=True)
        self._say(tr("합쳤습니다 — 저장하면 기록됩니다"))
        return True

    def accept_suggestion(self) -> bool:
        """고른 구간의 합치기 제안을 받아들인다(사람이 누를 때만, 자동 아님)."""
        sug = next((m for m in self.merge_suggestions() if m.segment_id == self._sel), None)
        if sug is None or sug.suggested is None:
            self._say(tr("고른 구간에는 합치기 제안이 없습니다"))
            return False
        return self.merge_selected(sug.suggested)

    # ================================================================== 실행 취소 (모델 층)
    def undo(self) -> bool:
        """모델 층 실행 취소(Ctrl+Z). 디스크에는 쓰지 않는다. 저장된 것이었다면 다음 저장이 새 판·철회로 남긴다."""
        if self._model is None or self._read_only:
            return False
        if self._field_dirty():                  # 칸에 쓰던 글자를 먼저 버린다(그것이 '직전 조작')
            self._refresh()
            return True
        anchor = self._sel_anchor()
        if self._model.undo() is None:
            self._say(tr("되돌릴 것이 없습니다"))
            return False
        self._reselect(anchor)
        self._after_model_change(emit_selection=True)
        self._say(tr("실행 취소 (저장하면 기록됩니다)"))
        return True

    def redo(self) -> bool:
        if self._model is None or self._read_only:
            return False
        anchor = self._sel_anchor()
        if self._model.redo() is None:
            self._say(tr("다시 실행할 것이 없습니다"))
            return False
        self._reselect(anchor)
        self._after_model_change(emit_selection=True)
        return True

    def _sel_anchor(self) -> int:
        s = self.selected
        return s.start_ms if s is not None and not s.outside else 0

    def _reselect(self, anchor_ms: int) -> None:
        """고른 구간이 사라졌으면 예전 시작 시각을 덮는 구간을 고른다."""
        if self._model is None:
            return
        try:
            self._model.index_of(self._sel)
            return
        except (KeyError, TypeError):
            pass
        seg = self._model.segment_at(min(max(anchor_ms, 0), self._model.duration_ms - 1))
        self._sel = seg.id if seg else self._model.segments[0].id

    def _after_model_change(self, *, emit_selection: bool = False) -> None:
        anchor = self._sel_anchor() if self.selected is not None else 0
        self._reselect(anchor)
        self._refresh()
        self.segmentsChanged.emit()
        if emit_selection and self._sel is not None:
            s = self.selected
            self.selectionChanged.emit(self._sel, s.start_ms if s else 0)

    # ================================================================== 저장 (기록 층)
    def plan(self) -> SavePlan:
        """지금 저장하면 쓸 것(03 10-3). 근거 칸의 아직 안 넣은 글자는 넣었다고 보고 계산하지 않는다(save 가 먼저 넣는다)."""
        if self._model is None:
            return SavePlan((), (), ())
        segs = list(self._model.segments) + list(self._model.outside)
        in_model = {s.id: s for s in segs}
        writes: list[dict] = []
        retracts: list[tuple[str, str, str | None, str]] = []
        errors: list[str] = []
        will_exist: set[str] = set()
        for s in segs:
            d = self._disk.get(s.id)
            if s.is_complete:
                will_exist.add(s.id)
                body = s.body()
                if d is None:
                    writes.append({"kind": "segment", "entity_id": s.id, "supersedes": None, "body": body})
                elif d.body is None or d.body != body:
                    writes.append({"kind": "segment", "entity_id": s.id, "supersedes": d.head_id, "body": body})
            elif d is not None and d.body is not None:
                retracts.append((s.id, "undo", None, d.head_id))
        for eid, d in self._disk.items():
            if d.body is None or eid in in_model:
                continue
            survivor = next((s for s in segs if eid in s.merged_from), None)
            if survivor is None:
                retracts.append((eid, "undo", None, d.head_id))
            elif survivor.id not in will_exist:
                rng = f"{fmt_display(survivor.start_ms)}~{fmt_display(survivor.end_ms)}"
                errors.append(tr("합쳐서 남은 구간({0})의 분류·확신도·근거를 채운 뒤 저장하세요").format(rng))
            else:
                retracts.append((eid, "merged", survivor.id, d.head_id))
        for w in writes:
            why = self._media_error(w["body"])
            if why and why not in errors:
                errors.append(why)
        return SavePlan(tuple(self._order_writes(writes, {e for e, *_ in retracts})), tuple(retracts), tuple(errors))

    def _order_writes(self, writes: list[dict], retracted: set[str]) -> list[dict]:
        """중간에 멈춰도 겹침이 생기지 않는 순서로 writes 를 고른다(retracts 는 먼저 쓴다고 본다).

        디스크에 남아 있을 구간(철회 대상 빼고)에서 시작해, 그 판을 썼을 때 다른 구간과 겹치지 않는 write 를 하나씩 고른다.
        그런 순서가 없으면(맞바꾸기 등 — 모델이 겹침 없는 상태에서 오므로 보통은 생기지 않는다) 남은 것을 원래 순서로 붙인다.
        """
        cur: dict[str, tuple[int, int]] = {}
        for eid, d in self._disk.items():
            b = d.body
            if b is None or eid in retracted:
                continue
            if isinstance(b.get("start_ms"), int) and isinstance(b.get("end_ms"), int):
                cur[eid] = (b["start_ms"], b["end_ms"])
        rest = list(writes)
        out: list[dict] = []
        while rest:
            pick = None
            for w in rest:
                b = w["body"]
                a, z = b.get("start_ms"), b.get("end_ms")
                if not (isinstance(a, int) and isinstance(z, int)):
                    pick = w
                    break
                if all(not (a < e and s_ < z) for k, (s_, e) in cur.items() if k != w["entity_id"]):
                    pick = w
                    break
            if pick is None:
                out.extend(rest)
                break
            rest.remove(pick)
            out.append(pick)
            b = pick["body"]
            if isinstance(b.get("start_ms"), int) and isinstance(b.get("end_ms"), int):
                cur[pick["entity_id"]] = (b["start_ms"], b["end_ms"])
        return out

    @property
    def has_unsaved(self) -> bool:
        """저장하지 않은 것이 있는가: 근거 칸의 글자, 저장 계획(새 판·철회), 분류만 고른(확신도·근거가 빠진) 구간."""
        return self._field_dirty() or not self.plan().empty or self.partial_count() > 0

    def save(self) -> list[dict] | None:
        """Ctrl+Enter: 모델과 디스크의 차이를 save·retract 콜백으로 쓴다. 쓴 레코드 목록(없으면 []).
        막히면(근거 빈칸·합친 구간 미완성) None. 중간에 콜백이 실패하면 거기서 멈추고, 이미 쓴 것만 장부에 남긴다."""
        if self._model is None or self._read_only:
            self._say(tr("읽기 전용입니다") if self._model is not None else tr("구간 기록이 열려 있지 않습니다"))
            return None
        s = self.selected
        if s is not None and self._field_dirty():
            if not self.ed_rationale.text().strip():
                self._say(tr("근거 한 줄은 필수입니다"))
                self.ed_rationale.setFocus(Qt.OtherFocusReason)
                return None
            self._commit_field()
        s = self.selected
        if s is not None and s.is_labeled and not s.is_complete:
            missing = []
            if s.confidence is None:
                missing.append(tr("확신도"))
            if not s.rationale:
                missing.append(tr("근거 한 줄"))
            self._say(tr("고른 구간에 {0}이(가) 없습니다").format("·".join(missing)))
            return None
        p = self.plan()
        if p.errors:
            self._say(" · ".join(p.errors))
            return None
        if p.empty:
            self._say(tr("저장할 변경이 없습니다"))
            self._refresh()
            return []
        written: list[dict] = []
        try:
            # 철회 먼저, 그다음 줄어드는 판부터(SavePlan 설명). 중간에 멈추면 빈틈만 남는다.
            for eid, reason, into, head in p.retracts:
                if self._retract_cb is not None:
                    rec = self._retract_cb(eid, reason, into)
                else:
                    body = {"reason": reason}
                    if into is not None:
                        body["merged_into"] = into
                    rec = self._save_cb({"kind": "retract", "entity_id": eid, "supersedes": head, "body": body})
                rid = rec.get("id") if isinstance(rec, Mapping) else None
                if not isinstance(rid, str):
                    raise RuntimeError(tr("retract 가 저장된 레코드(id)를 돌려주지 않았습니다"))
                self._disk[eid] = _DiskState(rid, None)
                written.append(rec)
            for draft in p.writes:
                rec = self._save_cb(draft)
                rid = rec.get("id") if isinstance(rec, Mapping) else None
                if not isinstance(rid, str):
                    raise RuntimeError(tr("save 가 저장된 레코드(id)를 돌려주지 않았습니다"))
                self._disk[draft["entity_id"]] = _DiskState(rid, draft["body"])
                written.append(rec)
        except Exception as e:  # noqa: BLE001 — store 의 거부(형식·판 연결·읽기 전용)를 화면에 보인다
            LOG.warning("구간 저장 실패 (%d줄 쓴 뒤): %s", len(written), type(e).__name__)
            self._say(tr("저장 실패 ({0}줄 쓴 뒤): {1}").format(len(written), e))
            self._refresh()
            if written:
                self.recordsSaved.emit(written)
            return None
        self._refresh()
        self.recordsSaved.emit(written)
        msg = tr("저장했습니다 ({0}줄)").format(len(written))
        partial = len(self._model.incomplete()) - len(self._model.unlabeled())
        if partial:
            msg += " · " + tr("확신도·근거가 빠진 구간 {0}개는 저장하지 않았습니다").format(partial)
        self._say(msg)
        return written

    def discard_changes(self) -> None:
        """저장 안 한 변경을 버리고 디스크 판으로 되돌린다(작업대가 확인을 받은 뒤 부른다). 모델 층 undo 스택도 비워진다."""
        if self._model is None:
            return
        recs = [{"kind": "segment", "id": d.head_id, "entity_id": eid, "body": d.body}
                for eid, d in self._disk.items() if d.body is not None]
        retracted = {eid: d for eid, d in self._disk.items() if d.body is None}
        self.load_context(duration_ms=self._model.duration_ms, records=recs, fps=self._fps,
                          frame_origin_ms=self._frame_origin, thresholds=self._thr, position_ms=self._pos,
                          read_only=self._read_only, new_id=self._gen)
        self._disk.update(retracted)       # 철회한 대상의 머리(되살릴 때 supersedes)는 남긴다

    def cancel_edit(self) -> None:
        """Esc: 근거 칸의 고치던 글자를 버리고 cancel 콜백(작업대로 포커스). 모델·디스크는 그대로다."""
        self._refresh()
        try:
            self._cancel_cb()
        except Exception:  # noqa: BLE001
            LOG.exception("cancel 콜백 실패")

    # ================================================================== 화면 갱신
    def _say(self, text: str) -> None:
        self.lbl_msg.setText(text)
        if text:
            self.message.emit(text)

    def _refresh(self) -> None:
        self._updating = True
        try:
            self._refresh_inner()
        finally:
            self._updating = False

    def _refresh_inner(self) -> None:
        m = self._model
        s = self.selected
        editable = m is not None and not self._read_only and s is not None
        for b in list(self.cat_buttons.values()) + list(self.conf_buttons.values()):
            b.setEnabled(editable)
        for w in (self.ed_rationale, self.btn_time, self.cmb_signal, self.rad_start, self.rad_end,
                  self.btn_edge_here, self.btn_split, self.btn_save, self.btn_cancel):
            w.setEnabled(editable)
        if m is None or s is None:
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self.lbl_head.setText(" · ".join(self._problems) if self._problems else tr("구간 기록이 열려 있지 않습니다"))
            for g in (self.grp_cat, self.grp_conf):
                g.setExclusive(False)
                for b in g.buttons():
                    b.setChecked(False)
                g.setExclusive(True)
            self.ed_rationale.setText("")
            self.chk_active.setChecked(False)
            self.chk_active.setEnabled(False)
            for w in (self.btn_m1s, self.btn_m1f, self.btn_p1f, self.btn_p1s, self.btn_merge_prev,
                      self.btn_merge_next, self.btn_next_short, self.btn_undo, self.btn_redo):
                w.setEnabled(False)
            self.lbl_suggest.setText("")
            self.lbl_pending.setText("")
            return
        i = m.index_of(s.id)
        n = len(m)
        self.btn_prev.setEnabled(i > 0)
        self.btn_next.setEnabled(i < n - 1)
        state =tr("미분류") if not s.is_labeled else (tr("다 매김") if s.is_complete else tr("매기는 중"))
        head = tr("구간 {0}/{1} · {2} ~ {3} ({4}) · {5}").format(
            i + 1, n, fmt_display(s.start_ms), fmt_display(s.end_ms), _fmt_len(s.length_ms), state)
        if s.out_of_media:
            head += " · " + tr("영상 길이 밖에 걸친 기록")
        if self.outside_media(s):
            head += " · " + (tr("영상 없음 — 저장 불가") if not self._coverage else tr("영상이 덮지 않는 자리 — 저장 불가"))
        elif self._coverage is not None:
            a, b = s.record_span()
            if not any(x <= a and b <= y for x, y in self._coverage):
                head += " · " + tr("일부만 영상 안")
        if self._read_only:
            head += " · " + tr("읽기 전용")
        self.lbl_head.setText(head)
        # 분류·확신도 (등급처럼 미리 고르지 않는다: 매긴 값만 눌린 상태로)
        for g, cur in ((self.grp_cat, s.category), (self.grp_conf, s.confidence)):
            g.setExclusive(False)
            for b in g.buttons():
                b.setChecked(False)
            g.setExclusive(True)
        if s.category in self.cat_buttons:
            self.cat_buttons[s.category].setChecked(True)
        if s.confidence in self.conf_buttons:
            self.conf_buttons[s.confidence].setChecked(True)
        game = s.category == "game"
        self.chk_active.setEnabled(editable and game)
        self.chk_active.setChecked(bool(game and s.active_chat))
        if self.ed_rationale.text() != s.rationale:      # 고치던 글자는 버린다(조작 전에 _commit_field 가 넣는다)
            self.ed_rationale.setText(s.rationale)
        idx = next((k for k in range(self.cmb_signal.count()) if self.cmb_signal.itemData(k) == s.start_signal), 0)
        self.cmb_signal.setCurrentIndex(idx)
        # 경계
        has_edge = self._edge_index() is not None
        for w in (self.btn_m1s, self.btn_m1f, self.btn_p1f, self.btn_p1s, self.btn_edge_here):
            w.setEnabled(editable and has_edge)
        self.rad_start.setEnabled(editable and i > 0)
        self.rad_end.setEnabled(editable and i < n - 1)
        frame = tr("1프레임") if self._fps else tr("0.1초(가변 프레임률)")
        self.btn_m1f.setText("−" + frame)
        self.btn_p1f.setText("+" + frame)
        # 합치기
        self.btn_merge_prev.setEnabled(editable and i > 0)
        self.btn_merge_next.setEnabled(editable and i < n - 1)
        sug = self.merge_suggestions()
        mine = next((x for x in sug if x.segment_id == s.id), None)
        self.btn_merge_prev.setText(tr("앞 구간에 합치기") + (" ★" if mine and mine.suggested == "prev" else ""))
        self.btn_merge_next.setText(tr("뒤 구간에 합치기") + (" ★" if mine and mine.suggested == "next" else ""))
        lines = []
        if mine is not None:
            side = tr("앞") if mine.suggested == "prev" else tr("뒤")
            lines.append(tr("이 구간은 {0} 미만입니다 — {1} 구간에 합치기를 제안합니다(★). 자동으로 합치지 않습니다").format(
                _fmt_len(self._thr.min_length_ms), side))
        if sug:
            lines.append(tr("짧은 구간 {0}개").format(len(sug)))
        self.lbl_suggest.setText("\n".join(lines))
        self.btn_next_short.setEnabled(bool(sug))
        # 저장 상태
        self.btn_undo.setEnabled(not self._read_only and (m.can_undo or self._field_dirty()))
        self.btn_redo.setEnabled(not self._read_only and m.can_redo)
        self._update_save_state()

    def _update_save_state(self) -> None:
        if self._model is None:
            self.lbl_pending.setText("")
            return
        p = self.plan()
        parts = []
        unl = len(self._model.unlabeled())
        inc = len(self._model.incomplete()) - unl
        if unl:
            parts.append(tr("안 매긴 구간 {0}개").format(unl))
        if inc:
            parts.append(tr("매기는 중 {0}개").format(inc))
        n = len(p.writes) + len(p.retracts)
        dirty = self._field_dirty()
        if n or dirty:
            parts.append(tr("저장 안 한 변경 {0}건").format(n) + (tr(" (+근거 칸)") if dirty else ""))
        else:
            parts.append(tr("모두 저장됨"))
        if p.errors:
            parts.append(p.errors[0])
        self.lbl_pending.setText(" · ".join(parts))
