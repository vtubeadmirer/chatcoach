"""스트리머 훈련 — 견주기 화면 (08 D-18 6, 18 6-2 '견주기 화면', D-16 클릭 한 번).

열면 바로 표가 보인다. 칸은 셋이다.

| 칸 | 뜻 |
|---|---|
| 같음 | 내가 골랐고 원래도 반응한 채팅 |
| 나만 | 내가 골랐지만 원래는 반응하지 않은 채팅 |
| 원래만 | 원래 반응했지만 내가 고르지 않은 채팅 |

위에 숫자 줄 하나: 같음 수, 맞힌 비율(같음 / 원래 반응), 고른 시각과 원래 반응 시각의 차 중앙값, 나만·원래만 수.

- 행 한 번 누름 → listenRequested(chat_id, 시작 vod_ms, 끝 vod_ms). 훈련 화면(app.ui.trainer_view)이 그 자리로 가서
  **소리를 켜고** 원래 대답 앞뒤를 한 번 재생한다. 이 화면은 플레이어를 직접 만지지 않는다(플레이어는 하나, 옮기지 않음).
- N / Shift+N: 다음·앞 '원래만' 으로 건너뛰고 같은 재생을 한다(나란히 보기와 같은 키). 단추에도 같은 동작과 풍선 도움말.
- 입력 칸·고르는 창은 없다(D-16).

계산은 이 화면이 하지 않는다. 훈련 화면이 app.core.training.compare(...) 를 불러 받은 결과를 set_result 로 넘긴다.
받는 결과는 두 모양이다.
- app.core.training.Comparison: rows(CompareRow: status·chat_id·chat_vod_ms·picked_vod_ms·reaction_vod_ms·diff_ms),
  hit_rate, median_diff_ms, reference_count.
- dict (시험·다른 계산): {"same"|"mine_only"|"orig_only": [{"chat_id", "picked_vod_ms", "reaction_vod_ms", "delta_ms"}
  또는 chat_id 문자열, ...], "hit_rate"?, "median_diff_ms"?, "reference_count"?}.
없는 숫자는 행에서 다시 센다. 중앙값은 core.training.median_int 와 같게 짝수 개면 가운데 둘 평균의 내림.

개인정보: 채팅 본문은 화면에만 보인다(chat_text 로 받은 것을 표 칸에만). 로그·신호·기록에는 chat_id 와 숫자만(D-17 8).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.timecode import fmt_time
from ..i18n import tr

LOG = logging.getLogger(__name__)

CAT_SAME, CAT_MINE, CAT_ORIG = "same", "mine_only", "orig_only"
CATEGORIES = (CAT_SAME, CAT_MINE, CAT_ORIG)
CAT_KO = {CAT_SAME: "같음", CAT_MINE: "나만", CAT_ORIG: "원래만"}
CAT_COLORS = {CAT_SAME: QColor("#81c784"), CAT_MINE: QColor("#64b5f6"), CAT_ORIG: QColor("#e0b252")}

# 원래 대답 앞뒤 재생 범위: 채팅이 뜨기 조금 전 ~ 원래 대답 시각 조금 뒤.
LISTEN_PRE_MS = 5_000       # app.core.training.DEFAULT_LEAD_MS 와 같은 값
LISTEN_POST_MS = 8_000

COL_CAT, COL_TIME, COL_TEXT, COL_PICK, COL_REACT, COL_DELTA = range(6)
HEADERS = ("칸", "채팅 시각", "채팅", "내가 고른 시각", "원래 대답 시각", "차(초)")


# ---------------------------------------------------------------------------- 순수 도우미 (Qt 없음)
def _int_or_none(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == v:
        return int(round(v))
    return None


def listen_window(chat_ms: int | None, reaction_ms: int | None, pre_ms: int = LISTEN_PRE_MS,
                  post_ms: int = LISTEN_POST_MS) -> tuple[int, int] | None:
    """원래 대답 앞뒤 재생 범위 (VOD ms). 시작 = 채팅·대답 중 이른 것 − pre, 끝 = 늦은 것 + post.

    원래 반응이 없으면(나만) 채팅 시각 앞뒤를 듣는다 — 원래 스트리머가 그 채팅 뒤에 무엇을 했는지. 둘 다 없으면 None.
    """
    pts = [int(x) for x in (chat_ms, reaction_ms) if _int_or_none(x) is not None]
    if not pts:
        return None
    a = max(0, min(pts) - int(pre_ms))
    b = max(pts) + int(post_ms)
    return a, max(b, a + 1)


@dataclass(frozen=True)
class CompareRow:
    category: str                 # same · mine_only · orig_only
    chat_id: str
    chat_ms: int | None           # 채팅 VOD 시각 (모르면 None)
    picked_ms: int | None         # 내가 고른 순간의 재생 위치
    reaction_ms: int | None       # 원래 대답 시각
    delta_ms: int | None          # 고른 시각 − 원래 대답 시각 (같음만)

    def window(self) -> tuple[int, int] | None:
        return listen_window(self.chat_ms if self.chat_ms is not None else self.picked_ms, self.reaction_ms)


def _item_fields(item) -> dict:
    if isinstance(item, str):
        return {"chat_id": item}
    if isinstance(item, Mapping):
        return dict(item)
    return {}


def _first_int(d: Mapping, *keys) -> int | None:
    for k in keys:
        v = _int_or_none(d.get(k))
        if v is not None:
            return v
    return None


def _rows_from_comparison(result, chat_time) -> list[CompareRow]:
    out: list[CompareRow] = []
    seen: set[str] = set()
    for r in getattr(result, "rows", None) or ():
        cat, cid = getattr(r, "status", None), getattr(r, "chat_id", None)
        if cat not in CATEGORIES or not isinstance(cid, str) or not cid or cid in seen:
            continue
        seen.add(cid)
        cms = _int_or_none(getattr(r, "chat_vod_ms", None))
        if cms is None and chat_time is not None:
            cms = chat_time(cid)
        out.append(CompareRow(cat, cid, cms, _int_or_none(getattr(r, "picked_vod_ms", None)),
                              _int_or_none(getattr(r, "reaction_vod_ms", None)),
                              _int_or_none(getattr(r, "diff_ms", None))))
    return out


def _get(result, key):
    if isinstance(result, Mapping):
        return result.get(key)
    return getattr(result, key, None)


def rows_from_result(result, chat_time: Callable[[str], int | None] | None = None) -> list[CompareRow]:
    """compare 결과(Comparison 또는 dict) → 표 행 (채팅 시각 순, 모르면 고른·대답 시각).
    같은 chat_id 가 여러 칸에 있으면 먼저 나온 칸만."""
    out: list[CompareRow] = []
    seen: set[str] = set()
    if result is None:
        return out
    if not isinstance(result, Mapping):
        out = _rows_from_comparison(result, chat_time)
        result = {}
    for cat in CATEGORIES:
        items = result.get(cat)
        if not isinstance(items, (list, tuple)):
            continue
        for it in items:
            d = _item_fields(it)
            cid = d.get("chat_id")
            if not isinstance(cid, str) or not cid or cid in seen:
                continue
            seen.add(cid)
            cms = _first_int(d, "chat_vod_ms", "chat_t_ms", "t_ms")
            if cms is None and chat_time is not None:
                cms = chat_time(cid)
            picked = _first_int(d, "picked_vod_ms", "picked_ms") if cat != CAT_ORIG else None
            react = _first_int(d, "reaction_vod_ms", "reaction_time_ms", "reaction_ms") if cat != CAT_MINE else None
            delta = None
            if cat == CAT_SAME:
                delta = _first_int(d, "delta_ms")
                if delta is None and picked is not None and react is not None:
                    delta = picked - react
            out.append(CompareRow(cat, cid, cms, picked, react, delta))

    def key(r: CompareRow):
        t = r.chat_ms if r.chat_ms is not None else (r.picked_ms if r.picked_ms is not None else r.reaction_ms)
        return (t is None, t if t is not None else 0, r.chat_id)
    out.sort(key=key)
    return out


def _median(vals: list[int]) -> int | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) // 2


def summary_numbers(result, rows: Iterable[CompareRow]) -> dict:
    """숫자 줄: same·mine_only·orig_only·reference(원래 반응 수)·hit_rate·median_delta_ms.

    결과(Comparison 또는 dict)에 hit_rate·median_diff_ms·reference_count 가 있으면 그 값을 쓰고(계산 담당이 기준),
    없으면 행에서 센다."""
    rows = list(rows)
    n = {c: sum(1 for r in rows if r.category == c) for c in CATEGORIES}
    ref = _int_or_none(_get(result, "reference_count"))
    if ref is None:
        ref = n[CAT_SAME] + n[CAT_ORIG]
    hr = _get(result, "hit_rate")
    if not (isinstance(hr, (int, float)) and not isinstance(hr, bool)):
        hr = (n[CAT_SAME] / ref) if ref else None
    med = _int_or_none(_get(result, "median_diff_ms"))
    if med is None:
        med = _median([r.delta_ms for r in rows if r.category == CAT_SAME and r.delta_ms is not None])
    return {"same": n[CAT_SAME], "mine_only": n[CAT_MINE], "orig_only": n[CAT_ORIG], "reference": ref,
            "hit_rate": None if hr is None else float(hr), "median_delta_ms": med}


def _fmt_sec(ms: int | None, signed: bool = False) -> str:
    if ms is None:
        return "—"
    s = ms / 1000.0
    return f"{s:+.1f}" if signed else f"{s:.1f}"


def fmt_summary(nums: Mapping) -> str:
    hr = nums.get("hit_rate")
    hr_s = "—" if hr is None else f"{hr * 100:.0f}%"
    return tr("같음 {same} · 맞힌 비율 {hr} (같음 {same} / 원래 반응 {ref}) · 시각 차 중앙값 {med}초 · "
              "나만 {mine} · 원래만 {orig}").format(
        same=nums.get("same", 0), hr=hr_s, ref=nums.get("reference", 0),
        med=_fmt_sec(nums.get("median_delta_ms"), signed=True), mine=nums.get("mine_only", 0),
        orig=nums.get("orig_only", 0))


def _fmt_t(ms: int | None) -> str:
    return "—" if ms is None else fmt_time(int(ms), ms_digits=False, always_hours=True)


# ---------------------------------------------------------------------------- 화면
class TrainCompareView(QWidget):
    """견주기 표. 행 한 번 누름 = 그 자리 듣기(listenRequested). N / Shift+N = 다음·앞 '원래만'."""

    listenRequested = Signal(str, int, int)   # (chat_id, 시작 vod_ms, 끝 vod_ms)

    def __init__(self, parent=None, *, result=None,
                 chat_time: Callable[[str], int | None] | None = None,
                 chat_text: Callable[[str], str | None] | None = None):
        super().__init__(parent)
        self._result = None
        self._rows: list[CompareRow] = []
        self._nums: dict = summary_numbers(None, [])
        self._chat_text = chat_text
        self._current = -1
        self.listen_count = 0          # 시험·점검용: 듣기를 부른 횟수

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(tr("견주기 — 행을 누르면 그 자리로 가서 소리를 켜고 원래 대답을 듣습니다"))
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.numbers = QLabel("")
        self.numbers.setObjectName("panelTitle")
        self.numbers.setWordWrap(True)
        lay.addWidget(self.numbers)

        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels([tr(h) for h in HEADERS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        for c in range(len(HEADERS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_TEXT, QHeaderView.Stretch)
        # Qt 연결은 묶인 메서드로만(self 를 잡은 람다는 화면이 지워지지 않게 붙든다)
        self.table.cellClicked.connect(self._on_cell_clicked)
        lay.addWidget(self.table, 1)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton(tr("◀ 앞 '원래만'"))
        self.prev_btn.setToolTip(tr("앞 '원래만' 으로 가서 원래 대답 듣기 (Shift+N)"))
        self.prev_btn.setFocusPolicy(Qt.NoFocus)
        self.prev_btn.clicked.connect(self._on_prev_btn)
        self.next_btn = QPushButton(tr("다음 '원래만' ▶"))
        self.next_btn.setToolTip(tr("다음 '원래만' 으로 가서 원래 대답 듣기 (N)"))
        self.next_btn.setFocusPolicy(Qt.NoFocus)
        self.next_btn.clicked.connect(self._on_next_btn)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        self.nav_msg = QLabel("")
        self.nav_msg.setObjectName("hint")
        nav.addWidget(self.nav_msg)
        lay.addLayout(nav)

        self.shortcuts: dict[str, QShortcut] = {}
        for key, fn in (("N", self.next_orig_only), ("Shift+N", self.prev_orig_only)):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
            self.shortcuts[key] = s

        self.set_result(result, chat_time=chat_time, chat_text=chat_text)

    # ------------------------------------------------------------------ 자료
    def set_result(self, result, *, chat_time: Callable[[str], int | None] | None = None,
                   chat_text: Callable[[str], str | None] | None = None) -> None:
        """compare 결과(Comparison 또는 dict, None = 비움)를 싣는다.
        chat_time(chat_id) → VOD ms, chat_text(chat_id) → 화면용 본문(한 줄)."""
        if chat_text is not None:
            self._chat_text = chat_text
        self._result = result
        self._rows = rows_from_result(self._result, chat_time)
        self._nums = summary_numbers(self._result, self._rows)
        self._current = -1
        self.numbers.setText(fmt_summary(self._nums))
        self._fill()
        has_orig = self._nums["orig_only"] > 0
        self.prev_btn.setEnabled(has_orig)
        self.next_btn.setEnabled(has_orig)
        self.nav_msg.setText("" if self._rows else tr("고른 채팅도 원래 반응도 없습니다"))
        LOG.info("견주기: 같음 %d · 나만 %d · 원래만 %d", self._nums["same"], self._nums["mine_only"],
                 self._nums["orig_only"])

    def _fill(self) -> None:
        t = self.table
        t.setRowCount(0)
        t.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            text = ""
            if self._chat_text is not None:
                try:
                    text = self._chat_text(r.chat_id) or ""
                except Exception:  # noqa: BLE001 — 본문은 보조. 못 읽어도 표는 보인다
                    text = ""
            cells = (tr(CAT_KO[r.category]), _fmt_t(r.chat_ms), text, _fmt_t(r.picked_ms), _fmt_t(r.reaction_ms),
                     _fmt_sec(r.delta_ms, signed=True) if r.delta_ms is not None else "")
            for c, v in enumerate(cells):
                it = QTableWidgetItem(v)
                if c == COL_CAT:
                    it.setForeground(QBrush(CAT_COLORS[r.category]))
                    it.setData(Qt.UserRole, r.chat_id)
                t.setItem(i, c, it)

    # ------------------------------------------------------------------ 읽기
    def rows(self) -> list[CompareRow]:
        return list(self._rows)

    def summary(self) -> dict:
        return dict(self._nums)

    def current_row(self) -> int:
        return self._current

    def numbers_text(self) -> str:
        return self.numbers.text()

    # ------------------------------------------------------------------ 동작
    def _on_cell_clicked(self, row: int, _col: int = 0) -> None:
        self.activate_row(row)

    def _on_prev_btn(self, *_a) -> None:
        self.prev_orig_only()

    def _on_next_btn(self, *_a) -> None:
        self.next_orig_only()

    def activate_row(self, i: int) -> bool:
        """행 한 번 누름: 그 행을 고르고 원래 대답 앞뒤 듣기를 요청한다. 범위를 모르면 False."""
        if not (0 <= i < len(self._rows)):
            return False
        self._current = i
        self.table.blockSignals(True)
        self.table.selectRow(i)
        self.table.blockSignals(False)
        it = self.table.item(i, 0)
        if it is not None:
            self.table.scrollToItem(it)
        r = self._rows[i]
        w = r.window()
        if w is None:
            self.nav_msg.setText(tr("이 채팅은 시각을 몰라 들을 수 없습니다"))
            return False
        self.nav_msg.setText("")
        self.listen_count += 1
        self.listenRequested.emit(r.chat_id, int(w[0]), int(w[1]))
        return True

    def _step_orig(self, step: int) -> bool:
        idx = [i for i, r in enumerate(self._rows) if r.category == CAT_ORIG]
        if not idx:
            self.nav_msg.setText(tr("'원래만' 이 없습니다"))
            return False
        cur = self._current
        # app.core.training.next_orig_only 와 같게 끝에서 돌지 않는다. 아무 행도 안 골랐으면 처음(앞으로)·끝(뒤로)부터
        if step > 0:
            cand = [i for i in idx if i > cur]
            target = cand[0] if cand else None
        else:
            cand = [i for i in idx if i < cur] if cur >= 0 else idx
            target = cand[-1] if cand else None
        if target is None:
            self.nav_msg.setText(tr("마지막 '원래만' 입니다") if step > 0 else tr("첫 '원래만' 입니다"))
            return False
        return self.activate_row(target)

    def next_orig_only(self) -> bool:
        """N: 다음 '원래만' (끝이면 멈춤)."""
        return self._step_orig(+1)

    def prev_orig_only(self) -> bool:
        """Shift+N: 앞 '원래만' (처음이면 멈춤)."""
        return self._step_orig(-1)
