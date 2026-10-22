"""채팅 표 — 수십만 건 채팅을 VOD 시각 순으로 보여 주는 작업대 오른쪽 칸 (02 7-3, 3-2 `chat_panel`).

성능 규칙 (02 9장):
- `QAbstractTableModel` + `QTableView`. `QSortFilterProxyModel`·`QListWidget`·`QTableWidget` 은 쓰지 않는다.
- 행 높이 고정, 줄바꿈 끔. 스크롤은 픽셀 단위(행 높이가 고정이라 행 위치 = 행 번호 × 높이).
- 행 데이터는 병렬 배열: 시각 `array('q')`, 본문 `list[str]`, 미리 casefold 한 본문 `list[str]`,
  종류·역할·흐림은 `bytearray`(작은 정수 코드), 시청자 번호는 `array('l')`. 행마다 dict·튜플을 두지 않는다.
- 필터는 보이는 행의 원본 인덱스 배열을 새로 만들고 `beginResetModel`/`endResetModel`. 그 뒤 선택한
  chat_id 와 스크롤 위치(기준 행의 화면 속 높이)를 복원한다.
- 검색 입력은 멈춘 뒤 250ms 디바운스 후 거른다.
- 재생 위치 → 하이라이트는 정렬된 시각 배열의 bisect 로 찾고, 바뀐 두 행만 dataChanged 한다.

시각은 전부 VOD 시각(ms, `messages.t_ms`)이다. 영상 파일 시각으로의 변환은 호출하는 쪽(작업대)이 한다.

개인정보: 원 닉네임은 다루지 않는다. 받는 것은 messages.jsonl 의 가명 줄(`viewer_id`·`nickname_masked`)뿐이고,
화면의 '시청자 0012' 는 03 14-2 의 표시용 번호다. 이 모듈은 로그에 본문·닉네임을 남기지 않는다.
"""
from __future__ import annotations

import re
import time
from array import array
from bisect import bisect_left, bisect_right
from typing import Iterable, Sequence

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeyEvent
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QTableView,
                               QVBoxLayout, QWidget)

from ..core.timecode import fmt_time
from ..i18n import tr

# ── 코드 표 ───────────────────────────────────────────────────────────────
# 채팅 종류 (03 5-8). 0 만 판정 대상(일반 채팅)이고 나머지는 흐리게 + 배지.
KIND_CODES = {"text": 0, "donation": 1, "subscription": 2, "system": 3,
              "image": 4, "sticker": 5, "video": 6, "rich": 7}
KIND_OTHER = 8
# 배지 표시명. core/schema.py 가 생기면 그쪽 표시명으로 옮긴다 (02 3-1 ui 규칙).
KIND_BADGE = {0: "", 1: "후원", 2: "구독", 3: "시스템", 4: "이미지", 5: "스티커", 6: "영상", 7: "리치", 8: "기타"}
ROLE_CODES = {"viewer": 0, "streamer": 1, "manager": 2}
ROLE_UNKNOWN = 3
ROLE_BADGE = {1: "스트리머", 2: "매니저"}

NULL_T = (1 << 62)          # t_ms 가 null 인 줄의 정렬용 시각 (03 14-2: 맨 뒤)
VNO_ANON = 0                # 익명
VNO_UNKNOWN = -1            # 넘겨받은 viewer_no 에 없는 viewer_id

COL_TIME, COL_VIEWER, COL_NICK, COL_TEXT, COL_KIND = range(5)
HEADERS = ("시각", "시청자", "닉네임", "본문", "종류")

DEBOUNCE_MS = 250           # 02 9장 '입력 멈춘 뒤 250ms'
FOLLOW_PAUSE_MS = 3000      # 사용자가 직접 스크롤하면 이 시간 동안 따라가기를 멈춘다
ROW_HEIGHT = 22

_CTRL = re.compile(r"[\x00-\x1f\x7f  ]")
_VIEWER_Q = re.compile(r"^(?:시청자\s*|#)(\d{1,7})$")

COLOR_DIM = QColor("#7d848c")        # 경계 완충·후원·구독·시스템
COLOR_HL_BG = QColor("#3b3420")      # 재생 위치 행
COLOR_DONATION = QColor("#e0b252")
COLOR_STREAMER = QColor("#64b5f6")
COLOR_LABEL_BG = QColor("#3a2a44")      # 잠긴 1차 라벨이 있는 행 (분석 세션만, 08 D-09 5 — 엔진 청록과 다른 보라)
COLOR_LABEL_FG = QColor("#ce93d8")
COLOR_OWN_BG = QColor("#1f3a28")        # 이 라벨링 세션이 이미 매긴 채팅 (08 D-16 — 분석 보라·엔진 청록과 다른 초록)
COLOR_OWN_FG = QColor("#81c784")


# ── 순수 도우미 ───────────────────────────────────────────────────────────
def _order_key(r: dict):
    t = r.get("t_ms")
    if not isinstance(t, int) or isinstance(t, bool):
        return (1, 0, str(r.get("chat_id") or ""))
    return (0, t, str(r.get("chat_id") or ""))


def _is_anon(r: dict) -> bool:
    vid = r.get("viewer_id")
    return (not vid) or vid == "anon" or bool(r.get("is_anonymous"))


def compute_viewer_no(records: Sequence[dict]) -> dict[str, int]:
    """03 14-2 표시용 번호: (t_ms, chat_id) 오름차순(t_ms null 은 맨 뒤)으로 처음 나온 순서대로 1, 2, 3 …

    익명은 넣지 않는다. 입력 순서와 무관하다.
    """
    out: dict[str, int] = {}
    for r in sorted(records, key=_order_key):
        if _is_anon(r):
            continue
        vid = r["viewer_id"]
        if vid not in out:
            out[vid] = len(out) + 1
    return out


def fmt_viewer_no(no: int) -> str:
    """'시청자 0012' (9999 넘으면 자릿수를 늘림), 익명은 '익명', 모르면 '-'."""
    if no == VNO_ANON:
        return tr("익명")
    if no < 0:
        return "-"
    return tr("시청자 {0}").format(f"{no:04d}")


def clean_line(s: str) -> str:
    """한 줄 표시용: 줄바꿈·제어 문자를 공백으로."""
    return _CTRL.sub(" ", s) if _CTRL.search(s) else s


# 치지직 이모티콘 코드 ({:101:} · {:x_12:} · {:10A:}). 화면 표시에서만 짧은 표시로 바꾼다 —
# 저장된 본문·원문 검색·내보내기는 그대로 둔다. 공백만 사이에 둔 연속 코드는 하나로 묶는다(×개수).
EMOTE_TOKEN = "이모티콘"
_EMOTE_CODE = r"\{:[0-9A-Za-z_\-]{1,40}:\}"
_EMOTE_RUN = re.compile(_EMOTE_CODE + r"(?:\s*" + _EMOTE_CODE + r")*")
_EMOTE_ONE = re.compile(_EMOTE_CODE)


def _emote_token(m: re.Match) -> str:
    n = len(_EMOTE_ONE.findall(m.group(0)))
    name = tr(EMOTE_TOKEN)
    return f"[{name}]" if n == 1 else f"[{name}×{n}]"


def collapse_emotes(s: str) -> str:
    """'{:101:}{:x_12:} 안녕 {:10A:}' → '[이모티콘×2] 안녕 [이모티콘]'. 코드가 없으면 그대로(빠른 길)."""
    if "{:" not in s:
        return s
    return _EMOTE_RUN.sub(_emote_token, s)


def display_text(s: str) -> str:
    """채팅 본문의 화면 표시(한 줄): 이모티콘 코드를 짧게 + 제어 문자를 공백으로. 저장 값은 바꾸지 않는다."""
    return clean_line(collapse_emotes(s))


def search_key(s: str) -> str:
    """검색용 본문: 원문 그대로(casefold) — 이모티콘 코드가 있으면 화면 표시 글자도 뒤에 붙여 '이모티콘' 으로도 찾게 한다.
    둘 사이는 NUL 로 나눠 검색어가 두 쪽에 걸쳐 맞지 않게 한다(검색어의 제어 문자는 입력 칸이 받지 않는다)."""
    f = s.casefold()
    if "{:" not in s:
        return f
    shown = collapse_emotes(s)
    return f if shown == s else f + "\x00" + shown.casefold()


class ChatData:
    """messages.jsonl 줄 묶음 → (t_ms, chat_id) 순 병렬 배열. Qt 를 쓰지 않는다(워커에서 만들어 넘겨도 된다)."""

    __slots__ = ("n", "times", "chat_ids", "texts", "folded", "nicks", "vnos", "kinds", "roles",
                 "dim", "_id_index")

    def __init__(self, records: Sequence[dict], viewer_no: dict[str, int] | None = None):
        n = len(records)
        # 이미 (t_ms, chat_id) 순이면(수집기가 그렇게 쓴다) 정렬을 건너뛴다.
        order: Sequence[int]
        if _already_sorted(records):
            order = range(n)
        else:
            order = sorted(range(n), key=lambda i: _order_key(records[i]))
        if viewer_no is None:
            viewer_no = {}
            for i in order:
                r = records[i]
                if not _is_anon(r):
                    vid = r["viewer_id"]
                    if vid not in viewer_no:
                        viewer_no[vid] = len(viewer_no) + 1
        times = array("q", bytes(8 * n))
        vnos = array("l", bytes(4 * n))
        kinds = bytearray(n)
        roles = bytearray(n)
        chat_ids: list[str] = [""] * n
        texts: list[str] = [""] * n
        folded: list[str] = [""] * n
        nicks: list[str] = [""] * n
        nick_pool: dict[str, str] = {}
        kc, rc, vget = KIND_CODES.get, ROLE_CODES.get, viewer_no.get
        for j, i in enumerate(order):
            r = records[i]
            t = r.get("t_ms")
            times[j] = t if (isinstance(t, int) and not isinstance(t, bool)) else NULL_T
            chat_ids[j] = str(r.get("chat_id") or "")
            s = r.get("text")
            s = s if isinstance(s, str) else ""
            texts[j] = s
            folded[j] = search_key(s)
            nk = r.get("nickname_masked")
            if isinstance(nk, str):
                nicks[j] = nick_pool.setdefault(nk, nk)
            vid = r.get("viewer_id")
            if (not vid) or vid == "anon" or r.get("is_anonymous"):
                vnos[j] = VNO_ANON
            else:
                vnos[j] = vget(vid, VNO_UNKNOWN)
            kinds[j] = kc(r.get("kind"), KIND_OTHER)
            roles[j] = rc(r.get("role"), ROLE_UNKNOWN)
        self.n = n
        self.times, self.chat_ids, self.texts, self.folded = times, chat_ids, texts, folded
        self.nicks, self.vnos, self.kinds, self.roles = nicks, vnos, kinds, roles
        self.dim = bytearray(n)
        self._id_index: dict[str, int] | None = None

    def index_of(self, chat_id: str) -> int | None:
        if self._id_index is None:  # 처음 찾을 때만 만든다 (36만 건이면 수십 ms)
            self._id_index = {c: i for i, c in enumerate(self.chat_ids)}
        return self._id_index.get(chat_id)

    def set_dim_ranges(self, ranges: Iterable[tuple[int, int]]) -> None:
        """[start, end) 반열림 구간 안 채팅을 흐림 표시. 이전 표시는 지운다."""
        dim = bytearray(self.n)
        t = self.times
        for start, end in ranges:
            a, b = bisect_left(t, int(start)), bisect_left(t, int(end))
            if b > a:
                dim[a:b] = b"\x01" * (b - a)
        self.dim = dim


def _already_sorted(records: Sequence[dict]) -> bool:
    prev_t, prev_c, prev_null = None, "", False
    for r in records:
        t = r.get("t_ms")
        c = r.get("chat_id") or ""
        if not isinstance(t, int) or isinstance(t, bool):
            if prev_null and c < prev_c:
                return False
            prev_null, prev_c = True, c
            continue
        if prev_null:
            return False
        if prev_t is not None and (t < prev_t or (t == prev_t and c < prev_c)):
            return False
        prev_t, prev_c = t, c
    return True


# ── 모델 ──────────────────────────────────────────────────────────────────
_R_DISPLAY = Qt.ItemDataRole.DisplayRole
_R_FOREGROUND = Qt.ItemDataRole.ForegroundRole
_R_BACKGROUND = Qt.ItemDataRole.BackgroundRole
_R_FONT = Qt.ItemDataRole.FontRole
_R_TOOLTIP = Qt.ItemDataRole.ToolTipRole
_R_USER = Qt.ItemDataRole.UserRole
_ROLES = frozenset((_R_DISPLAY, _R_FOREGROUND, _R_BACKGROUND, _R_FONT, _R_TOOLTIP, _R_USER))
_KIND_BADGE_TR = {k: tr(v) for k, v in KIND_BADGE.items()}
_ROLE_BADGE_TR = {k: tr(v) for k, v in ROLE_BADGE.items()}


def _fmt_hms(ms: int) -> str:
    """VOD 시각 h:mm:ss (화면 열. 소수는 버림)."""
    return fmt_time(ms, ms_digits=False, always_hours=True)


class ChatTableModel(QAbstractTableModel):
    """보이는 행 = `rows[view_row]` 가 가리키는 ChatData 의 원본 인덱스. 거르지 않으면 rows 는 range."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.data_ = ChatData([])
        self.rows: Sequence[int] = range(0)
        self.hl_src = -1               # 재생 위치 행(원본 인덱스), 없으면 -1
        self.marks: dict[int, str] = {}  # 원본 인덱스 → 사람 라벨 표시(set_label_marks)
        self.marks_own = False           # True = 이 라벨링 세션 자기 라벨(초록), False = 분석 세션의 잠긴 1차 라벨(보라)
        self._label_bg = QBrush(COLOR_LABEL_BG)
        self._label_fg = QBrush(COLOR_LABEL_FG)
        self._own_bg = QBrush(COLOR_OWN_BG)
        self._own_fg = QBrush(COLOR_OWN_FG)
        self._bold = QFont()
        self._bold.setBold(True)
        self._dim_brush = QBrush(COLOR_DIM)
        self._hl_brush = QBrush(COLOR_HL_BG)
        self._don_brush = QBrush(COLOR_DONATION)
        self._str_brush = QBrush(COLOR_STREAMER)

    # Qt 모델 인터페이스 --------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else 5

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < 5:
            return tr(HEADERS[section])
        return None

    def data(self, index: QModelIndex, role=_R_DISPLAY):
        # 그리기 때 셀마다 역할 7~8개로 불린다. Qt.XxxRole 속성 조회는 한 번에 수 µs 라 모듈 상수와 비교한다.
        if role not in _ROLES:
            return None
        r = index.row()
        rows = self.rows
        if r < 0 or r >= len(rows):
            return None
        src = rows[r]
        d = self.data_
        c = index.column()
        if role == _R_DISPLAY:
            if c == COL_TEXT:
                return display_text(d.texts[src])
            if c == COL_TIME:
                t = d.times[src]
                return "—" if t == NULL_T else _fmt_hms(t)
            if c == COL_VIEWER:
                return fmt_viewer_no(d.vnos[src])
            if c == COL_NICK:
                return d.nicks[src]
            if c == COL_KIND:
                mk = self.marks.get(src) if self.marks else None
                if mk:
                    return mk
                k = d.kinds[src]
                if k:
                    return _KIND_BADGE_TR[k]
                return _ROLE_BADGE_TR.get(d.roles[src], "")
            return None
        if role == _R_FOREGROUND:
            k = d.kinds[src]
            if c == COL_KIND:
                if self.marks and src in self.marks:
                    return self._own_fg if self.marks_own else self._label_fg
                if k == 1:
                    return self._don_brush
                if k == 0 and d.roles[src] == 1:
                    return self._str_brush
                return self._dim_brush
            if k or d.dim[src]:
                return self._dim_brush
            return None
        if role == _R_BACKGROUND:
            if src == self.hl_src:
                return self._hl_brush
            if self.marks and src in self.marks:
                return self._own_bg if self.marks_own else self._label_bg
            return None
        if role == _R_FONT:
            return self._bold if (src == self.hl_src and c == COL_TIME) else None
        if role == _R_TOOLTIP:
            if c == COL_KIND and self.marks and src in self.marks:
                return (tr("이 세션에서 이미 매긴 채팅 — 두 번 누르면 그 라벨을 고칩니다") if self.marks_own
                        else tr("잠긴 1차 세션의 사람 라벨 (분석 보기)"))
            if c != COL_TEXT:
                return None
            s = d.texts[src]
            if not (len(s) > 40 or _CTRL.search(s)):
                return None
            return collapse_emotes(s)
        if role == _R_USER:
            return d.chat_ids[src]
        return None

    # 내부 조작 ------------------------------------------------------------
    def reset_all(self, data: ChatData, rows: Sequence[int]) -> None:
        self.beginResetModel()
        self.data_ = data
        self.rows = rows
        self.hl_src = -1
        self.endResetModel()

    def reset_rows(self, rows: Sequence[int]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def view_row_of(self, src: int) -> int:
        """원본 인덱스 → 보이는 행. 걸러져 없으면 -1."""
        rows = self.rows
        if isinstance(rows, range):
            return src if 0 <= src < len(rows) else -1
        i = bisect_left(rows, src)
        return i if i < len(rows) and rows[i] == src else -1

    def view_row_at_time(self, vod_ms: int) -> int:
        """보이는 행 중 t_ms ≤ vod_ms 인 마지막 행. 없으면 -1."""
        rows, times = self.rows, self.data_.times
        if isinstance(rows, range):
            return bisect_right(times, vod_ms, 0, len(rows)) - 1
        return bisect_right(rows, vod_ms, key=times.__getitem__) - 1

    def emit_row_changed(self, view_row: int) -> None:
        if 0 <= view_row < len(self.rows):
            self.dataChanged.emit(self.index(view_row, 0), self.index(view_row, 4))

    def emit_all_changed(self) -> None:
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, 4))


# ── 표 위젯 ───────────────────────────────────────────────────────────────
class _ChatView(QTableView):
    """Enter·더블클릭을 직접 받아 한 번만 알린다 (스타일마다 다른 activated 동작에 기대지 않는다)."""

    enterPressed = Signal(QModelIndex)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            idx = self.currentIndex()
            if idx.isValid():
                self.enterPressed.emit(idx)
                e.accept()
                return
        super().keyPressEvent(e)


class ChatPanel(QWidget):
    """작업대 오른쪽 채팅 표 (02 7-3). 시각은 VOD 시각(ms).

    공개 API: set_messages · set_time_ms · set_filter · set_kind_filter · set_dim_ranges ·
    selected_chat_id · select_chat · highlighted_chat_id · row_count.
    Signals: chatActivated(chat_id, vod_ms) — 더블클릭·Enter. chatSelected(chat_id) — 선택이 바뀔 때(빈 문자열 = 선택 없음).
    """

    chatActivated = Signal(str, int)
    chatSelected = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._data = ChatData([])
        self._query = ""
        self._kinds: frozenset[int] | None = None
        self._follow_pause_until = 0.0
        self._last_time_ms: int | None = None
        self._sel_src = -1             # 기억한 선택(원본 인덱스). 필터에 걸려 잠시 안 보여도 유지 → 해제 때 복원
        self._sel_y: int | None = None  # 그 행이 마지막으로 보였을 때의 화면 속 높이
        self._resetting = False

        self.model = ChatTableModel(self)
        self.view = _ChatView(self)
        v = self.view
        v.setModel(self.model)
        v.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.setSelectionMode(QAbstractItemView.SingleSelection)
        v.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.setWordWrap(False)
        v.setTextElideMode(Qt.ElideRight)
        v.setShowGrid(False)
        v.setAlternatingRowColors(False)
        v.setSortingEnabled(False)
        v.setCornerButtonEnabled(False)
        v.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        v.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        vh = v.verticalHeader()
        vh.setVisible(False)
        vh.setSectionResizeMode(QHeaderView.Fixed)
        vh.setMinimumSectionSize(ROW_HEIGHT)
        vh.setDefaultSectionSize(ROW_HEIGHT)
        hh = v.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setStretchLastSection(False)
        for col, w in ((COL_TIME, 70), (COL_VIEWER, 86), (COL_NICK, 64), (COL_KIND, 58)):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            hh.resizeSection(col, w)
        hh.setSectionResizeMode(COL_TEXT, QHeaderView.Stretch)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText(tr("검색 (본문 · '시청자 12' · '#12')"))
        self.search.setClearButtonEnabled(True)
        self.count_label = QLabel("", self)
        self.count_label.setObjectName("hint")

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(lambda: self._apply_filter(self.search.text()))
        self.search.textChanged.connect(lambda _t: self._debounce.start())

        v.doubleClicked.connect(self._on_activated)
        v.enterPressed.connect(self._on_activated)
        # 오른쪽 메뉴: 작업대가 context_actions(chat_id) → [(글자, fn(chat_id), 켜짐, 풍선 도움말)] 를 준다(14 4-2 의 11)
        self.context_actions = None
        v.setContextMenuPolicy(Qt.CustomContextMenu)
        v.customContextMenuRequested.connect(self._on_context_menu)
        v.verticalScrollBar().actionTriggered.connect(lambda _a: self._pause_follow())
        v.selectionModel().currentRowChanged.connect(lambda cur, _prev: self._emit_selected(cur))

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(QLabel(tr("채팅"), self))
        top.addWidget(self.search, 1)
        top.addWidget(self.count_label)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(v, 1)
        self._update_count()

    # ── 공개 API ──────────────────────────────────────────────────────────
    def set_messages(self, records: list[dict] | ChatData, viewer_no: dict[str, int] | None = None) -> None:
        """messages.jsonl 줄 목록(또는 워커가 미리 만든 ChatData)을 싣는다. 필터·하이라이트·흐림은 초기화."""
        self._debounce.stop()
        data = records if isinstance(records, ChatData) else ChatData(records, viewer_no)
        self._data = data
        self._query = ""
        self._kinds = None
        self._last_time_ms = None
        self._sel_src, self._sel_y = -1, None
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.model.marks = {}
        self.model.reset_all(data, range(data.n))
        self._update_count()

    def set_label_marks(self, marks: dict[str, str] | None, *, own: bool = False) -> int:
        """chat_id → 짧은 표시. '종류' 칸 글씨·행 배경으로 보인다. None·빈 dict 면 지운다. 표에 있는 채팅 수를 돌려준다.

        두 쓰임만 있다(블라인드, 안내서 원칙 3):
        - own=False: 분석 세션의 잠긴 1차 라벨(예: '1차 직접', 보라, 08 D-09 5).
        - own=True: 라벨링 세션(1차·재라벨)이 **자기 세션에서** 이미 매긴 현재 유효 반응 라벨(예: '✓ 직접', 초록,
          08 D-16). 다시 켰을 때 같은 채팅을 또 매기지 않게. 다른 세션의 라벨은 절대 넘기지 않는다(작업대가 거른다).
        """
        out: dict[int, str] = {}
        for cid, text in (marks or {}).items():
            src = self._data.index_of(cid)
            if src is not None and text:
                out[src] = str(text)
        self.model.marks = out
        self.model.marks_own = bool(own)
        self.model.emit_all_changed()
        return len(out)

    def label_marks(self) -> dict[str, str]:
        """지금 보이는 사람 라벨 표시 chat_id → 표시 (시험·점검용)."""
        return {self._data.chat_ids[i]: t for i, t in self.model.marks.items()}

    def set_time_ms(self, vod_ms: int, follow: bool = True) -> None:
        """재생 위치(VOD 시각) 표시: t_ms ≤ vod_ms 인 마지막 보이는 행을 하이라이트. follow 면 그 행이 보이게 스크롤."""
        self._last_time_ms = int(vod_ms)
        m = self.model
        row = m.view_row_at_time(int(vod_ms))
        src = m.rows[row] if row >= 0 else -1
        if src != m.hl_src:
            old_row = m.view_row_of(m.hl_src) if m.hl_src >= 0 else -1
            m.hl_src = src
            m.emit_row_changed(old_row)
            m.emit_row_changed(row)
        if follow and row >= 0 and time.monotonic() >= self._follow_pause_until:
            self._ensure_row_visible(row)

    def set_filter(self, text: str) -> None:
        """검색어를 바로 적용한다(디바운스 없음). 검색 칸 글자도 맞춘다."""
        self._debounce.stop()
        if self.search.text() != text:
            self.search.blockSignals(True)
            self.search.setText(text)
            self.search.blockSignals(False)
        self._apply_filter(text)

    def set_kind_filter(self, kinds: Iterable[str] | None) -> None:
        """보일 채팅 종류(03 5-8 의 kind 이름). None 이면 전부."""
        codes = None if kinds is None else frozenset(KIND_CODES.get(k, KIND_OTHER) for k in kinds)
        if codes == self._kinds:
            return
        self._kinds = codes
        self._refilter()

    def set_dim_ranges(self, ranges: list[tuple[int, int]]) -> None:
        """경계 완충 구간 [start, end) (VOD 시각) 의 채팅을 흐리게."""
        self._data.set_dim_ranges(ranges)
        self.model.emit_all_changed()

    def selected_chat_id(self) -> str | None:
        """지금 선택된(보이는) 채팅. 기억한 선택이 필터에 걸려 안 보이면 None."""
        idx = self.view.selectionModel().selectedRows()
        if not idx:
            return None
        r = idx[0].row()
        if r >= len(self.model.rows):
            return None
        return self._data.chat_ids[self.model.rows[r]]

    def select_chat(self, chat_id: str) -> bool:
        """chat_id 를 선택하고 보이게 한다. 없거나 지금 필터에 걸러져 있으면 False(선택은 그대로)."""
        src = self._data.index_of(chat_id)
        if src is None:
            return False
        row = self.model.view_row_of(src)
        if row < 0:
            return False
        self._select_row(row)
        self._ensure_row_visible(row, center=True)
        return True

    def highlighted_chat_id(self) -> str | None:
        s = self.model.hl_src
        return self._data.chat_ids[s] if s >= 0 else None

    def row_count(self) -> int:
        return len(self.model.rows)

    def total_count(self) -> int:
        return self._data.n

    def chat_data(self) -> ChatData:
        return self._data

    # ── 필터 ──────────────────────────────────────────────────────────────
    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip().casefold()
        if q == self._query:
            return
        self._query = q
        self._refilter()

    def _compute_rows(self) -> Sequence[int]:
        d, q, kinds = self._data, self._query, self._kinds
        n = d.n
        if not q and kinds is None:
            return range(n)
        mv = _VIEWER_Q.match(q) if q else None
        if mv:
            want = int(mv.group(1))
            vn = d.vnos
            idx = [i for i in range(n) if vn[i] == want]
        elif q:
            idx = [i for i, s in enumerate(d.folded) if q in s]
        else:
            idx = None
        if kinds is not None:
            kd = d.kinds
            if idx is None:
                idx = [i for i in range(n) if kd[i] in kinds]
            else:
                idx = [i for i in idx if kd[i] in kinds]
        return array("q", idx)

    def _refilter(self) -> None:
        """행 배열을 새로 만들고 리셋. 선택(걸러져 잠시 사라졌던 것 포함)과 스크롤 위치를 복원한다.

        기준 행: 기억한 선택 채팅이 새 행에 있으면 그것을 같은 화면 높이에, 없으면 리셋 전 맨 위 행
        (걸러져 사라졌으면 그다음 행)을 같은 높이에 둔다.
        """
        m, v, h = self.model, self.view, ROW_HEIGHT
        sb = v.verticalScrollBar()
        cur = sb.value()
        sticky = self._sel_src
        if sticky >= 0:
            r = m.view_row_of(sticky)
            if r >= 0:
                self._sel_y = r * h - cur
        top_src, top_y = -1, 0
        if m.rows:
            r = min(cur // h, len(m.rows) - 1)
            top_src, top_y = m.rows[r], r * h - cur
        self._resetting = True
        try:
            m.reset_rows(self._compute_rows())
            m.hl_src = -1
            if self._last_time_ms is not None:
                self.set_time_ms(self._last_time_ms, follow=False)
            v.updateGeometries()
            row = m.view_row_of(sticky) if sticky >= 0 else -1
            if row >= 0:
                self._select_row(row)
                y = self._sel_y
            elif top_src >= 0 and m.rows:
                rows = m.rows
                row = min(top_src, len(rows) - 1) if isinstance(rows, range) else min(bisect_left(rows, top_src),
                                                                                       len(rows) - 1)
                y = top_y
            else:
                y = None
            if row >= 0:
                vh = max(h, v.viewport().height())
                if y is None or not (0 <= y <= vh - h):  # 기준 행이 화면 밖이었으면 가운데로
                    y = (vh - h) // 2
                sb.setValue(max(0, row * h - y))
        finally:
            self._resetting = False
        self._update_count()

    # ── 선택·스크롤 ───────────────────────────────────────────────────────
    def context_menu_for(self, chat_id: str) -> QMenu | None:
        """chat_id 의 오른쪽 메뉴(보이지 않은 채). 줄 것이 없으면 None. 시험은 이것으로 메뉴 항목을 본다."""
        acts = list(self.context_actions(chat_id) or ()) if self.context_actions is not None else []
        if not acts:
            return None
        menu = QMenu(self)
        for text, fn, enabled, tip in acts:
            a = menu.addAction(text)
            a.setEnabled(bool(enabled))
            a.setToolTip(tip or "")
            a.triggered.connect(lambda _c=False, fn=fn, cid=chat_id: fn(cid))
        menu.setToolTipsVisible(True)
        return menu

    def _on_context_menu(self, pos) -> None:
        idx = self.view.indexAt(pos)
        r = idx.row() if idx.isValid() else -1
        if not (0 <= r < len(self.model.rows)):
            return
        self._select_row(r)
        menu = self.context_menu_for(self._data.chat_ids[self.model.rows[r]])
        if menu is not None:
            menu.exec(self.view.viewport().mapToGlobal(pos))
            menu.deleteLater()

    def _select_row(self, row: int) -> None:
        idx = self.model.index(row, 0)
        self.view.selectionModel().setCurrentIndex(
            idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)

    def _ensure_row_visible(self, row: int, center: bool = False) -> None:
        """행이 화면 밖이면 스크롤. 따라가기는 채팅창처럼 그 행이 맨 아래에 오게(지금까지 온 채팅이 보이게)."""
        v = self.view
        sb = v.verticalScrollBar()
        h = ROW_HEIGHT
        vh = max(h, v.viewport().height())
        top = row * h
        cur = sb.value()
        if cur <= top and top + h <= cur + vh:
            return
        if sb.maximum() < top + h - vh:  # 범위가 아직 갱신되지 않았으면 (리셋 직후)
            v.updateGeometries()
        sb.setValue(max(0, top - (vh - h) // 2) if center else max(0, top + h - vh))

    def _pause_follow(self) -> None:
        self._follow_pause_until = time.monotonic() + FOLLOW_PAUSE_MS / 1000.0

    def _emit_selected(self, cur: QModelIndex) -> None:
        if self._resetting:  # 필터 리셋·복원 중의 선택 변화는 사용자 선택이 아니다
            return
        r = cur.row()
        if 0 <= r < len(self.model.rows):
            src = self.model.rows[r]
            self._sel_src, self._sel_y = src, None
            self.chatSelected.emit(self._data.chat_ids[src])
        else:
            self._sel_src, self._sel_y = -1, None
            self.chatSelected.emit("")

    def _on_activated(self, idx: QModelIndex) -> None:
        r = idx.row()
        if not (0 <= r < len(self.model.rows)):
            return
        src = self.model.rows[r]
        t = self._data.times[src]
        if t == NULL_T:
            return  # 시각이 없는 채팅은 이동할 곳이 없다
        self.chatActivated.emit(self._data.chat_ids[src], int(t))

    def _update_count(self) -> None:
        n, shown = self._data.n, len(self.model.rows)
        self.count_label.setText(f"{n:,}" if shown == n else tr("{0} / {1}").format(f"{shown:,}", f"{n:,}"))
