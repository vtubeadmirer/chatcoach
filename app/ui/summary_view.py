"""방송 요약 화면 — 주제 덩어리별 숫자를 표 하나로 (08 D-01 '방송 하나를 열면 요약', D-17 5·8, 17 6-1 2번).

받는 것(생성자·set_data 인자. 이 파일은 디스크를 열지 않고 계산도 하지 않는다 — 주제 흐름은 엔진이 낸다, D-17 5):
- topics: app/core/topics(엔진 주제 흐름을 읽는 쪽)의 결과. 모양은 넉넉히 받는다(normalize_topics):
    · 덩어리 목록 — dict 또는 속성 있는 객체(dataclass 등)
    · {'topics' | 'chunks' | 'blocks' | 'segments': [...], 'stats': [...] 또는 {id|index: {...}}} 모양
    · (덩어리 목록, 숫자 목록|dict) 두 개짜리 튜플
  덩어리 하나에서 읽는 칸(앞의 이름이 먼저):
    시작 start_ms · t0_ms · start / 끝 end_ms · t1_ms · end / 아이디 topic_id · id
    이름 name · label · title · topic / 키워드 keywords · tags · terms (문자열 목록, (단어, 점수) 목록, {단어: 점수})
    채팅 수 chat_count · n_chats · chats(정수) / 반응률 reply_rate · reaction_rate · response_rate
      (0~1. 1 보다 크고 100 이하이면 백분율로 본다. 없으면 replied_count · reacted_count ÷ 채팅 수)
    놓친 질문 수 missed_questions · missed_question_count · missed
    본문(화면에만) text · excerpt · transcript · body · summary_text
  숫자 칸은 덩어리 안의 'stats' dict 에 있어도 된다.
- sealed: True 면 '봉인됨 n건' 만 보이고 topics 는 읽지도 않는다(D-15 8, D-17 5 — 시범 프로젝트의 주제 흐름은 봉인 경로).
  sealed_count: 봉인 건수(app.analysis_flow.BroadcastSummaryData.sealed_count). 봉인 표시는 어디서나 '봉인됨 n건'
  (08 D-18 3 — 나란히 보기·메뉴 안내와 같은 말). 건수를 넘기지 않은 옛 부르는 쪽만 '봉인됨' 으로 둔다.

화면 (D-16 클릭 한 번 원칙: 열면 바로 보이고 입력 칸이 없다)
- 위 한 줄: 합계(주제 수 · 채팅 수 · 반응률(채팅 수로 가중) · 놓친 질문 수).
- 표: 주제(이름 + 키워드) · 시간 · 채팅 수 · 반응률 · 놓친 질문. 행을 한 번 누르면(또는 Enter) 그 덩어리 시작 시각으로
  seekRequested(int) 를 보낸다. set_position 으로 재생 위치의 덩어리를 표시한다(시그널은 보내지 않음).
- 아래: 고른 덩어리의 본문(엔진이 준 경우) — **이 화면에만** 보이고 로그·내보내기에는 쓰지 않는다(D-17 8).
  내보내기·저장용은 export_rows() (숫자·키워드·주제 이름만).

로그에는 건수만 남긴다. 주제 이름·키워드·본문은 로그에 쓰지 않는다.
"""
from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.timecode import fmt_display
from ..i18n import tr

LOG = logging.getLogger("chatcoach")

COLUMNS = ("topic", "time", "chats", "reply_rate", "missed")
COLUMN_KO = {"topic": "주제", "time": "시간", "chats": "채팅 수", "reply_rate": "반응률", "missed": "놓친 질문"}
KEYWORDS_SHOWN = 3          # 표 칸에 보이는 키워드 수 (나머지는 툴팁)

_LIST_KEYS = ("topics", "chunks", "blocks", "segments", "items")
_START_KEYS = ("start_ms", "t0_ms", "start")
_END_KEYS = ("end_ms", "t1_ms", "end")
_ID_KEYS = ("topic_id", "id")
_NAME_KEYS = ("name", "label", "title", "topic")
_KW_KEYS = ("keywords", "tags", "terms")
_CHAT_KEYS = ("chat_count", "n_chats", "chats")
_RATE_KEYS = ("reply_rate", "reaction_rate", "response_rate")
_REPLIED_KEYS = ("replied_count", "reacted_count")
_MISSED_KEYS = ("missed_questions", "missed_question_count", "missed")
_BODY_KEYS = ("text", "excerpt", "transcript", "body", "summary_text")


# ---------------------------------------------------------------------------- 순수 계산 (Qt 없음)
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _get(obj: Any, keys: Sequence[str]) -> Any:
    """dict 면 키, 아니면 속성. 처음 None 아닌 값."""
    for k in keys:
        if isinstance(obj, Mapping):
            v = obj.get(k)
        else:
            v = getattr(obj, k, None)
        if v is not None:
            return v
    return None


def _as_ms(v) -> int | None:
    if _is_int(v):
        return v
    if isinstance(v, float) and v == v:     # NaN 제외
        return int(round(v))
    return None


def _as_count(v) -> int | None:
    if _is_int(v) and v >= 0:
        return v
    if isinstance(v, float) and v == v and v >= 0 and v == int(v):
        return int(v)
    if isinstance(v, (list, tuple, set, frozenset)):     # 목록이면 개수
        return len(v)
    return None


def _as_rate(v) -> float | None:
    if not _is_num(v) or v != v or v < 0:
        return None
    v = float(v)
    if v > 1.0:
        if v <= 100.0:
            return v / 100.0       # 백분율로 온 값
        return None
    return v


def _as_keywords(v) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        parts = [p.strip() for p in v.replace("#", " ").replace(",", " ").split()]
        return tuple(p for p in parts if p)
    if isinstance(v, Mapping):       # {단어: 점수} — 점수 큰 순서
        items = [(str(k), s if _is_num(s) else 0) for k, s in v.items()]
        items.sort(key=lambda kv: -kv[1])
        return tuple(k for k, _s in items if k)
    out = []
    if isinstance(v, Iterable):
        for x in v:
            if isinstance(x, str):
                w = x
            elif isinstance(x, (list, tuple)) and x and isinstance(x[0], str):
                w = x[0]
            elif isinstance(x, Mapping) and isinstance(x.get("word") or x.get("text"), str):
                w = x.get("word") or x.get("text")
            else:
                continue
            w = w.strip()
            if w and w not in out:
                out.append(w)
    return tuple(out)


@dataclass(frozen=True)
class TopicRow:
    """요약 표 한 줄. body 는 화면에만(D-17 8) — export_rows 에 나가지 않는다."""
    index: int                      # 시작 시각 순서(0부터)
    topic_id: str | None
    start_ms: int
    end_ms: int
    name: str | None
    keywords: tuple[str, ...]
    chat_count: int | None
    reply_rate: float | None        # 0~1
    missed_questions: int | None
    body: str | None = None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.keywords:
            return " · ".join(self.keywords[:KEYWORDS_SHOWN])
        return tr("주제 {n}").format(n=self.index + 1)


@dataclass(frozen=True)
class TopicTable:
    rows: tuple[TopicRow, ...]
    invalid: int                    # 시각을 못 읽어 뺀 덩어리 수


def _split_input(data: Any) -> tuple[list, Any]:
    """입력 → (덩어리 목록, 따로 온 숫자(목록|dict|None))."""
    if data is None:
        return [], None
    if isinstance(data, tuple) and len(data) == 2 and not isinstance(data[0], (str, bytes)) \
            and isinstance(data[0], (list, tuple)):
        return list(data[0]), data[1]
    if isinstance(data, Mapping):
        for k in _LIST_KEYS:
            v = data.get(k)
            if isinstance(v, (list, tuple)):
                return list(v), data.get("stats")
        return [], None
    for k in _LIST_KEYS:                # 결과 객체 (topics 속성)
        v = getattr(data, k, None)
        if isinstance(v, (list, tuple)):
            return list(v), getattr(data, "stats", None)
    if isinstance(data, (list, tuple)):
        return list(data), None
    if isinstance(data, Iterable) and not isinstance(data, (str, bytes)):
        return list(data), None
    return [], None


def _stats_for(stats: Any, i: int, item: Any) -> Any:
    if stats is None:
        return None
    if isinstance(stats, (list, tuple)):
        return stats[i] if i < len(stats) else None
    if isinstance(stats, Mapping):
        tid = _get(item, _ID_KEYS)
        for key in (tid, str(tid) if tid is not None else None, i, str(i)):
            if key is not None and key in stats:
                return stats[key]
    return None


def normalize_topics(data: Any) -> TopicTable:
    """app/core/topics 결과(모양은 위 모듈 설명) → 시작 시각 순서의 TopicRow 목록."""
    items, stats = _split_input(data)
    raw: list[tuple[int, int, int, Any, Any]] = []
    invalid = 0
    for i, item in enumerate(items):
        if item is None:
            invalid += 1
            continue
        s, e = _as_ms(_get(item, _START_KEYS)), _as_ms(_get(item, _END_KEYS))
        if s is None or e is None or e <= s:
            invalid += 1
            continue
        raw.append((s, e, i, item, _stats_for(stats, i, item)))
    raw.sort(key=lambda r: (r[0], r[1], r[2]))
    rows = []
    for idx, (s, e, _i, item, st) in enumerate(raw):
        inner = _get(item, ("stats",))
        sources = [x for x in (item, inner if isinstance(inner, Mapping) else None, st) if x is not None]

        def pick(keys, conv, _sources=sources):
            for src in _sources:
                v = conv(_get(src, keys))
                if v is not None:
                    return v
            return None

        chat = pick(_CHAT_KEYS, _as_count)
        rate = pick(_RATE_KEYS, _as_rate)
        if rate is None and chat:
            replied = pick(_REPLIED_KEYS, _as_count)
            if replied is not None and replied <= chat:
                rate = replied / chat
        name = _get(item, _NAME_KEYS)
        name = name.strip() if isinstance(name, str) and name.strip() else None
        kw = ()
        for src in sources:
            kw = _as_keywords(_get(src, _KW_KEYS))
            if kw:
                break
        tid = _get(item, _ID_KEYS)
        body = _get(item, _BODY_KEYS)
        rows.append(TopicRow(index=idx, topic_id=str(tid) if tid is not None else None, start_ms=s, end_ms=e,
                             name=name, keywords=kw, chat_count=chat, reply_rate=rate,
                             missed_questions=pick(_MISSED_KEYS, _as_count),
                             body=body if isinstance(body, str) and body.strip() else None))
    return TopicTable(rows=tuple(rows), invalid=invalid)


def summary_totals(rows: Sequence[TopicRow]) -> dict:
    """합계: 주제 수 · 채팅 수 · 반응률(채팅 수로 가중, 둘 다 있는 덩어리만) · 놓친 질문 수 · 전체 길이."""
    chats = [r.chat_count for r in rows if r.chat_count is not None]
    missed = [r.missed_questions for r in rows if r.missed_questions is not None]
    w = [(r.reply_rate, r.chat_count) for r in rows if r.reply_rate is not None and r.chat_count]
    n_w = sum(c for _r, c in w)
    return {
        "topics": len(rows),
        "chat_count": sum(chats) if chats else None,
        "reply_rate": (sum(rt * c for rt, c in w) / n_w) if n_w else None,
        "missed_questions": sum(missed) if missed else None,
        "duration_ms": sum(r.duration_ms for r in rows),
    }


def export_rows(rows: Sequence[TopicRow]) -> list[dict]:
    """저장·내보내기용 — 숫자·키워드·주제 이름만(D-17 8). 본문(body)은 넣지 않는다."""
    return [{"index": r.index, "topic_id": r.topic_id, "start_ms": r.start_ms, "end_ms": r.end_ms, "name": r.name,
             "keywords": list(r.keywords), "chat_count": r.chat_count, "reply_rate": r.reply_rate,
             "missed_questions": r.missed_questions} for r in rows]


def fmt_rate(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def fmt_count(v: int | None) -> str:
    return "—" if v is None else str(v)


def fmt_span(ms: int) -> str:
    sec = max(0, int(ms) // 1000)
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return tr("{h}시간 {m}분").format(h=h, m=m)
    if m and s:
        return tr("{m}분 {s}초").format(m=m, s=s)
    if m:
        return tr("{m}분").format(m=m)
    return tr("{s}초").format(s=s)


def row_at(rows: Sequence[TopicRow], t_ms: int) -> int | None:
    """t_ms 를 품은 행 번호(시작 ≤ t < 끝, 겹치면 시작이 늦은 것). 없으면 None."""
    starts = [r.start_ms for r in rows]
    i = bisect.bisect_right(starts, int(t_ms)) - 1
    while i >= 0:
        if rows[i].start_ms <= t_ms < rows[i].end_ms:
            return i
        i -= 1
    return None


# ---------------------------------------------------------------------------- 화면
class _NumItem(QTableWidgetItem):
    def __init__(self, text: str):
        super().__init__(text)
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)


class _EnterFilter(QObject):
    """표에서 Enter = 고른 행으로 이동 (키보드 사용자도 한 번에)."""

    def __init__(self, view: "SummaryView"):
        super().__init__(view)
        self._view = view

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._view._activate_row(self._view.table.currentRow())
            return True
        return False


def sealed_text(n: int | None) -> str:
    """봉인 표시 글 (08 D-18 3): '봉인됨 n건'. 건수를 모르면(옛 부르는 쪽) '봉인됨'."""
    if n is None:
        return tr("봉인됨")
    return tr("봉인됨 {n}건").format(n=n)


class SummaryView(QWidget):
    """방송 요약. sealed=True 면 '봉인됨 n건' 만 보인다(D-18 3).

    시그널
    - seekRequested(int): 행을 누르면 그 주제 덩어리의 시작 시각(ms).
    - topicActivated(int): 누른 행 번호(TopicRow.index).
    """
    seekRequested = Signal(int)
    topicActivated = Signal(int)

    def __init__(self, topics: Any = None, *, sealed: bool = False, sealed_count: int | None = None, parent=None):
        super().__init__(parent)
        self._rows: tuple[TopicRow, ...] = ()
        self._invalid = 0
        self._sealed = False
        self._sealed_count: int | None = None
        self._build()
        self.set_data(topics, sealed=sealed, sealed_count=sealed_count)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)

        # 봉인됨
        self.sealed_page = QWidget()
        sl = QVBoxLayout(self.sealed_page)
        self.sealed_label = QLabel(tr("봉인됨"))
        self.sealed_label.setAlignment(Qt.AlignCenter)
        sl.addWidget(self.sealed_label)
        self.stack.addWidget(self.sealed_page)

        # 비어 있음
        self.empty_page = QWidget()
        el = QVBoxLayout(self.empty_page)
        self.empty_label = QLabel(tr("주제 흐름이 아직 없습니다. 엔진이 주제 흐름을 내면 여기에 요약이 보입니다."))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        el.addWidget(self.empty_label)
        self.stack.addWidget(self.empty_page)

        # 요약 표
        self.table_page = QWidget()
        tl = QVBoxLayout(self.table_page)
        tl.setContentsMargins(4, 4, 4, 4)
        self.totals_label = QLabel()
        self.totals_label.setWordWrap(True)
        tl.addWidget(self.totals_label)
        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(COLUMN_KO[c]) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(COLUMNS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setCursor(Qt.PointingHandCursor)
        self.table.cellClicked.connect(lambda r, _c: self._activate_row(r))
        self.table.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._show_detail(r))
        self.table.installEventFilter(_EnterFilter(self))
        split.addWidget(self.table)
        self.detail_label = QLabel()     # 본문 — 화면에만 (D-17 8)
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        split.addWidget(self.detail_label)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        tl.addWidget(split, 1)
        self.stack.addWidget(self.table_page)

    # ------------------------------------------------------------------ 자료
    def set_data(self, topics: Any = None, *, sealed: bool = False, sealed_count: int | None = None) -> None:
        """자료를 바꾼다. sealed 면 topics 를 읽지 않고 '봉인됨 n건' 만 보인다(sealed_count 가 없으면 '봉인됨')."""
        self.table.setRowCount(0)
        self.detail_label.clear()
        self.totals_label.clear()
        n = sealed_count if isinstance(sealed_count, int) and not isinstance(sealed_count, bool) else None
        self._sealed_count = max(0, n) if n is not None else None
        if sealed:
            self._sealed = True
            self._rows, self._invalid = (), 0
            self.sealed_label.setText(sealed_text(self._sealed_count))
            self.stack.setCurrentWidget(self.sealed_page)
            LOG.info("방송 요약: 봉인됨 %s건 (열지 않음)", "?" if self._sealed_count is None else self._sealed_count)
            return
        self._sealed = False
        table = normalize_topics(topics)
        self._rows, self._invalid = table.rows, table.invalid
        if not self._rows:
            self.stack.setCurrentWidget(self.empty_page)
            LOG.info("방송 요약: 주제 0개 (뺀 것 %d)", self._invalid)
            return
        self._fill()
        self.stack.setCurrentWidget(self.table_page)
        LOG.info("방송 요약: 주제 %d개 (뺀 것 %d)", len(self._rows), self._invalid)

    def _fill(self) -> None:
        tot = summary_totals(self._rows)
        parts = [tr("주제 {n}개").format(n=tot["topics"]), tr("길이 {d}").format(d=fmt_span(tot["duration_ms"])),
                 tr("채팅 {n}").format(n=fmt_count(tot["chat_count"])),
                 tr("반응률 {r}").format(r=fmt_rate(tot["reply_rate"])),
                 tr("놓친 질문 {n}").format(n=fmt_count(tot["missed_questions"]))]
        if self._invalid:
            parts.append(tr("시각이 없어 뺀 덩어리 {n}").format(n=self._invalid))
        self.totals_label.setText(" · ".join(parts))
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            label = r.display_name
            shown_kw = [k for k in r.keywords[:KEYWORDS_SHOWN]] if r.name else []
            if shown_kw:
                label = f"{label}  ({', '.join(shown_kw)})"
            it = QTableWidgetItem(label)
            if r.keywords:
                it.setToolTip(", ".join(r.keywords))
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, QTableWidgetItem(
                f"{fmt_display(r.start_ms)} ~ {fmt_display(r.end_ms)} ({fmt_span(r.duration_ms)})"))
            self.table.setItem(i, 2, _NumItem(fmt_count(r.chat_count)))
            self.table.setItem(i, 3, _NumItem(fmt_rate(r.reply_rate)))
            self.table.setItem(i, 4, _NumItem(fmt_count(r.missed_questions)))

    def _show_detail(self, row: int) -> None:
        if not (0 <= row < len(self._rows)):
            self.detail_label.clear()
            return
        r = self._rows[row]
        head = f"{r.display_name} — {fmt_display(r.start_ms)} ~ {fmt_display(r.end_ms)}"
        if r.keywords:
            head += "\n" + tr("키워드: {k}").format(k=", ".join(r.keywords))
        self.detail_label.setText(head + ("\n\n" + r.body if r.body else ""))

    def _activate_row(self, row: int) -> None:
        if self._sealed or not (0 <= row < len(self._rows)):
            return
        r = self._rows[row]
        self.topicActivated.emit(r.index)
        self.seekRequested.emit(r.start_ms)

    def set_position(self, ms: int) -> None:
        """재생 위치의 덩어리를 표에서 고른다(시그널은 보내지 않는다)."""
        if self._sealed or not self._rows:
            return
        i = row_at(self._rows, int(ms))
        if i is not None and i != self.table.currentRow():
            self.table.setCurrentCell(i, 0)
            self.table.scrollToItem(self.table.item(i, 0))

    # ------------------------------------------------------------------ 조회
    def is_sealed(self) -> bool:
        return self._sealed

    def sealed_count(self) -> int | None:
        """봉인 중일 때 넘겨받은 봉인 건수(넘기지 않았으면 None)."""
        return self._sealed_count if self._sealed else None

    def rows(self) -> tuple[TopicRow, ...]:
        return self._rows

    def row_count(self) -> int:
        return len(self._rows)

    def invalid_count(self) -> int:
        return self._invalid

    def export_rows(self) -> list[dict]:
        """지금 보이는 요약의 저장용 줄(숫자·키워드·주제 이름만, D-17 8). 봉인 중이면 빈 목록."""
        return [] if self._sealed else export_rows(self._rows)
