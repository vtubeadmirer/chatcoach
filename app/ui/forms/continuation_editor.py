"""이어짐 편집기 (안내서 v0.1 4-3, 03 5-4·6-8, label-event.schema.json $defs/continuationBody).

반응 편집기 안에 끼워 쓰거나(embedded=True, 저장 버튼 없음) 따로 쓴다(공통 폼 모양: save·cancel 콜백).

- 이어짐 4종: 이어지지 않음 / 짧게 이어짐 / 확장됨 / 새 주제로 전환. 이어짐은 직접·높음 반응에만 붙는다
  (편집기가 켜고 끈다. 검사 도구도 본다).
- '이어지지 않음' 이 아니면 대화가 이어진 구간(span) 시작·끝이 필수(스키마).
- 구간 끝 자르기: span 끝이 소속 저스트채팅 구간 끝보다 뒤면 구간 끝에서 자르고 '잘림' 을 표시한다.
  자르기 전 끝은 observed_end_ms 로 남긴다(03 6-8).
- 관련 채팅: span 안의 다른 시청자 일반 채팅을 골라 related_chat_ids 로 남긴다. 개수는 저장하지 않고 목록에서 센다.
- 4종은 미리 고르지 않는다. 시작값(짧게 = 1분 미만, 확장 = 1분 이상 또는 다른 채팅 3개 이상)으로 '기준 참고'
  문구만 보인다(07 W2 '다른 채팅 3개 이상이면 확장됨 제안' 을 문구로만 둔다).

순수 계산(truncate_span·continuation_hint)은 Qt 를 쓰지 않는다. core/rules 에 '이어짐 제안·절단' 이 생기면 옮긴다.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Callable, Mapping, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from ...core.timecode import fmt_time, parse_time
from ...i18n import tr

TYPES = ("none", "short", "extended", "new_topic")
TYPE_LABELS = {"none": "이어지지 않음", "short": "짧게 이어짐", "extended": "확장됨", "new_topic": "새 주제로 전환"}
CONFIDENCES = ("certain", "probably", "undeterminable")
CONF_LABELS = {"certain": "확실함", "probably": "아마도", "undeterminable": "판단 불가"}
SHORT_MAX_MS = 60_000          # guide-config continuation.short_max_sec
EXTENDED_MIN_OTHER = 3         # guide-config continuation.extended_min_other_chats
RELATED_MAX_ROWS = 300
RATIONALE_MAX = 300


# ---------------------------------------------------------------------------- 순수 계산

def truncate_span(start_ms: int, end_ms: int, segment_end_ms: int | None) -> tuple[int, int, int | None, bool]:
    """(span 시작, 잘린 끝, observed_end_ms, 잘렸는가). 구간 끝을 모르면 자르지 않는다."""
    s, e = int(start_ms), int(end_ms)
    if segment_end_ms is not None and e > int(segment_end_ms):
        cut = max(s, int(segment_end_ms))
        return s, cut, e, True
    return s, e, None, False


def continuation_hint(span_ms: int | None, related_count: int, short_max_ms: int = SHORT_MAX_MS,
                      extended_min_other: int = EXTENDED_MIN_OTHER) -> str | None:
    """시작값으로 본 4-3 기준 참고(새 주제 전환은 사람만 정하므로 넣지 않는다). 값은 TYPES 중 하나 또는 None."""
    if related_count >= extended_min_other:
        return "extended"
    if span_ms is None:
        return None
    return "extended" if span_ms >= short_max_ms else "short"


class ContinuationEditor(QWidget):
    """이어짐 라벨 하나. draft() 는 store 초안 {kind: 'continuation', body, entity_id?, supersedes?}."""

    changed = Signal()
    finished = Signal(object)      # 따로 쓸 때 저장 결과

    def __init__(self, save: Callable[[dict], dict] | None = None, cancel: Callable[[], None] | None = None,
                 parent=None, *, embedded: bool = False):
        super().__init__(parent)
        self._save = save
        self._cancel = cancel
        self.embedded = bool(embedded)
        self._chats = None
        self._segment_end: int | None = None
        self._position_fn: Callable[[], int] | None = None
        self._short_max_ms = SHORT_MAX_MS
        self._extended_min = EXTENDED_MIN_OTHER
        self._reaction_eid: str | None = None
        self._reaction_ms: int | None = None
        self._target_chat: str | None = None
        self._existing: dict | None = None
        self._loading = False
        self._build()
        self.setEnabled(False)

    # ------------------------------------------------------------ 화면
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        self.type_buttons: dict[str, QPushButton] = {}
        for t in TYPES:
            b = QPushButton(tr(TYPE_LABELS[t]))
            b.setCheckable(True)
            b.setObjectName(f"cont_{t}")
            self.type_group.addButton(b)
            self.type_buttons[t] = b
            row.addWidget(b)
        self.type_group.buttonClicked.connect(lambda _b: self._on_change())
        lay.addLayout(row)

        form = QFormLayout()
        span_row = QHBoxLayout()
        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText(tr("시작"))
        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText(tr("끝"))
        self.start_now = QPushButton(tr("시작=지금"))
        self.end_now = QPushButton(tr("끝=지금"))
        for w in (self.start_edit, self.start_now, QLabel("~"), self.end_edit, self.end_now):
            span_row.addWidget(w)
        self.start_now.clicked.connect(lambda: self._set_now(self.start_edit))
        self.end_now.clicked.connect(lambda: self._set_now(self.end_edit))
        self.start_edit.editingFinished.connect(self._on_span_edited)
        self.end_edit.editingFinished.connect(self._on_span_edited)
        form.addRow(tr("이어진 구간"), span_row)
        self.trunc_label = QLabel("")
        form.addRow("", self.trunc_label)
        self.related_list = QListWidget()
        self.related_list.setMaximumHeight(110)
        self.related_list.itemChanged.connect(lambda _i: self._on_change())
        form.addRow(tr("같은 주제로 이어진 채팅"), self.related_list)
        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        form.addRow("", self.hint_label)
        self.conf_box = QComboBox()
        self.conf_box.addItem(tr("확신도 고르기"), None)
        for c in CONFIDENCES:
            self.conf_box.addItem(tr(CONF_LABELS[c]), c)
        self.conf_box.currentIndexChanged.connect(lambda _i: self._on_change())
        form.addRow(tr("확신도"), self.conf_box)
        self.rationale = QLineEdit()
        self.rationale.setMaxLength(RATIONALE_MAX)
        self.rationale.setPlaceholderText(tr("근거 한 줄 (시각 포함)"))
        self.rationale.textEdited.connect(lambda _t: self._on_change())
        form.addRow(tr("근거"), self.rationale)
        lay.addLayout(form)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        self.save_btn = QPushButton(tr("이어짐 저장"))
        self.cancel_btn = QPushButton(tr("취소"))
        btns = QHBoxLayout()
        btns.addWidget(self.save_btn)
        btns.addWidget(self.cancel_btn)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn.clicked.connect(self._do_cancel)
        self.save_btn.setVisible(not self.embedded)
        self.cancel_btn.setVisible(not self.embedded)

    # ------------------------------------------------------------ 맥락
    def load_context(self, *, chats=None, segment_end_ms: int | None = None, thresholds: Mapping | None = None,
                     position_fn: Callable[[], int] | None = None) -> None:
        """chats: ChatData(병렬 배열). segment_end_ms: 반응이 속한 저스트채팅 구간 끝(VOD ms).
        thresholds: guide-config values(continuation 블록). position_fn: 지금 VOD 위치."""
        self._chats = chats
        self._segment_end = segment_end_ms
        if position_fn is not None:
            self._position_fn = position_fn
        c = (thresholds or {}).get("continuation") if isinstance(thresholds, Mapping) else None
        if isinstance(c, Mapping):
            if _num(c.get("short_max_sec")):
                self._short_max_ms = int(round(c["short_max_sec"] * 1000))
            if _num(c.get("extended_min_other_chats")):
                self._extended_min = int(c["extended_min_other_chats"])
        self._refresh_related()
        self._update_info()

    def set_segment_end(self, segment_end_ms: int | None) -> None:
        self._segment_end = segment_end_ms
        self._update_info()

    def open_for(self, reaction_entity_id: str | None, reaction_time_ms: int | None,
                 target_chat_id: str | None = None, existing: dict | None = None) -> None:
        """새로(또는 existing 레코드를 고쳐) 연다. reaction_entity_id 는 반응을 저장한 뒤 정해져도 된다."""
        self._reaction_eid = reaction_entity_id
        self._reaction_ms = reaction_time_ms
        self._target_chat = target_chat_id
        self._existing = existing
        self._loading = True
        try:
            self.type_group.setExclusive(False)
            for b in self.type_buttons.values():
                b.setChecked(False)
            self.type_group.setExclusive(True)
            self.start_edit.clear()
            self.end_edit.clear()
            self.conf_box.setCurrentIndex(0)
            self.rationale.clear()
            self.msg.clear()
            body = (existing or {}).get("body") if existing else None
            if isinstance(body, Mapping):
                self.set_body(body)
            elif reaction_time_ms is not None:
                self.start_edit.setText(fmt_time(reaction_time_ms, always_hours=True))
        finally:
            self._loading = False
        self._refresh_related(selected=(body or {}).get("related_chat_ids") if isinstance(body, Mapping) else None)
        self._update_info()

    def set_reaction(self, reaction_entity_id: str | None, reaction_time_ms: int | None = None,
                     target_chat_id: str | None = None) -> None:
        """반응 저장 뒤 entity_id 를 알려 준다(반응 편집기가 부른다)."""
        self._reaction_eid = reaction_entity_id
        if reaction_time_ms is not None:
            self._reaction_ms = reaction_time_ms
        if target_chat_id is not None and target_chat_id != self._target_chat:
            self._target_chat = target_chat_id
            self._refresh_related(selected=self.related_ids())

    # ------------------------------------------------------------ 값
    def chosen_type(self) -> str | None:
        for t, b in self.type_buttons.items():
            if b.isChecked():
                return t
        return None

    def set_type(self, t: str | None) -> None:
        self.type_group.setExclusive(False)
        for k, b in self.type_buttons.items():
            b.setChecked(k == t)
        self.type_group.setExclusive(True)
        self._on_change()

    def is_touched(self) -> bool:
        """무엇이든 입력했는가(아무것도 안 했으면 반응 편집기가 이어짐을 저장하지 않는다)."""
        return bool(self.chosen_type() or self.end_edit.text().strip() or self.related_ids()
                    or self.conf_box.currentData() or self.rationale.text().strip())

    def related_ids(self) -> list[str]:
        out = []
        for i in range(self.related_list.count()):
            it = self.related_list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out

    def span(self) -> tuple[int | None, int | None]:
        return parse_time(self.start_edit.text()), parse_time(self.end_edit.text())

    def set_body(self, body: Mapping) -> None:
        t = body.get("type")
        if t in TYPES:
            self.type_buttons[t].setChecked(True)
        s = body.get("span_start_ms")
        e = body.get("observed_end_ms") if isinstance(body.get("observed_end_ms"), int) else body.get("span_end_ms")
        self.start_edit.setText("" if not isinstance(s, int) else fmt_time(s, always_hours=True))
        self.end_edit.setText("" if not isinstance(e, int) else fmt_time(e, always_hours=True))
        i = self.conf_box.findData(body.get("confidence"))
        self.conf_box.setCurrentIndex(max(0, i))
        self.rationale.setText(str(body.get("rationale") or ""))

    def errors(self) -> list[str]:
        """저장을 막는 문제(빈 목록이면 저장 가능)."""
        errs = []
        t = self.chosen_type()
        if t is None:
            errs.append(tr("이어짐 종류를 고르세요"))
        s, e = self.span()
        if self.start_edit.text().strip() and s is None:
            errs.append(tr("이어진 구간 시작을 읽을 수 없습니다"))
        if self.end_edit.text().strip() and e is None:
            errs.append(tr("이어진 구간 끝을 읽을 수 없습니다"))
        if t is not None and t != "none":
            if s is None or e is None:
                errs.append(tr("이어진 구간 시작·끝이 필요합니다"))
            elif e <= s:
                errs.append(tr("이어진 구간 끝이 시작보다 뒤여야 합니다"))
        if self.conf_box.currentData() is None:
            errs.append(tr("이어짐 확신도를 고르세요"))
        if not self.rationale.text().strip():
            errs.append(tr("이어짐 근거 한 줄이 필요합니다"))
        if self._reaction_eid is None and not self.embedded:
            errs.append(tr("이어짐이 붙을 반응이 없습니다"))
        return errs

    def body(self, reaction_entity_id: str | None = None) -> dict:
        """스키마 continuationBody. 구간 끝 자르기를 여기서 한다."""
        t = self.chosen_type()
        s, e = self.span()
        out = {
            "reaction_entity_id": reaction_entity_id or self._reaction_eid,
            "type": t,
            "span_start_ms": None, "span_end_ms": None, "observed_end_ms": None,
            "truncated_at_segment_end": False,
            "related_chat_ids": self.related_ids(),
            "confidence": self.conf_box.currentData(),
            "rationale": self.rationale.text().strip(),
        }
        if s is not None and e is not None and e > s:
            s2, e2, obs, cut = truncate_span(s, e, self._segment_end)
            out.update(span_start_ms=s2, span_end_ms=e2, observed_end_ms=obs, truncated_at_segment_end=cut)
        elif s is not None and t == "none":
            out["span_start_ms"] = s
        return out

    def draft(self, reaction_entity_id: str | None = None) -> dict:
        d = {"kind": "continuation", "body": self.body(reaction_entity_id)}
        if self._existing and isinstance(self._existing.get("entity_id"), str):
            d["entity_id"] = self._existing["entity_id"]
            d["supersedes"] = self._existing.get("id")
        return d

    def mark_saved(self, rec: dict | None) -> None:
        """저장된 레코드를 '지금 판' 으로 기억한다(다시 저장하면 그 판을 대체한다). None 이면 잊는다(철회됨 → 다음 저장은 새 대상)."""
        if isinstance(rec, dict) or rec is None:
            self._existing = rec

    def existing(self) -> dict | None:
        return self._existing

    # ------------------------------------------------------------ 따로 쓸 때
    def save(self) -> dict | None:
        errs = self.errors()
        if errs:
            self.msg.setText(" · ".join(errs))
            return None
        if self._save is None:
            return None
        try:
            rec = self._save(self.draft())
        except Exception as e:  # noqa: BLE001 — 저장 실패는 화면에 드러낸다
            self.msg.setText(tr("저장 실패: {0}").format(_err_text(e)))
            return None
        if isinstance(rec, dict):
            self.mark_saved(rec)
        self.msg.setText(tr("저장했습니다"))
        self.finished.emit(rec)
        return rec

    def _do_cancel(self) -> None:
        if self._cancel is not None:
            self._cancel()

    # ------------------------------------------------------------ 내부
    def _set_now(self, edit: QLineEdit) -> None:
        if self._position_fn is None:
            return
        try:
            edit.setText(fmt_time(int(self._position_fn()), always_hours=True))
        except Exception:  # noqa: BLE001
            return
        self._on_span_edited()

    def _on_span_edited(self) -> None:
        self._refresh_related(selected=self.related_ids())
        self._on_change()

    def _on_change(self) -> None:
        if self._loading:
            return
        self._update_info()
        self.changed.emit()

    def _refresh_related(self, selected: Sequence[str] | None = None) -> None:
        """관련 채팅 후보: 반응 시각 ~ span 끝(없으면 +2분) 사이의 다른 일반 채팅. 대상 채팅은 뺀다."""
        keep = set(selected or [])
        self.related_list.blockSignals(True)
        try:
            self.related_list.clear()
            d = self._chats
            if d is None or not hasattr(d, "times"):
                return
            s, e = self.span()
            a = s if s is not None else self._reaction_ms
            if a is None:
                return
            b = e if (e is not None and e > a) else a + 120_000
            lo, hi = bisect_left(d.times, int(a)), bisect_right(d.times, int(b))
            from ..chat_panel import KIND_CODES, display_text, fmt_viewer_no   # 지연 import: 순환 피함
            text_code = KIND_CODES["text"]
            shown = 0
            for i in range(lo, hi):
                cid = d.chat_ids[i]
                if cid == self._target_chat or d.kinds[i] != text_code:
                    continue
                if shown >= RELATED_MAX_ROWS:
                    break
                line = f"{fmt_time(d.times[i], frac_digits=0, always_hours=True)}  {fmt_viewer_no(d.vnos[i])}  " \
                       f"{display_text(d.texts[i])[:60]}"
                it = QListWidgetItem(line)
                it.setData(Qt.UserRole, cid)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if cid in keep else Qt.Unchecked)
                self.related_list.addItem(it)
                shown += 1
        finally:
            self.related_list.blockSignals(False)

    def _update_info(self) -> None:
        s, e = self.span()
        if s is not None and e is not None and e > s:
            _s2, e2, obs, cut = truncate_span(s, e, self._segment_end)
            self.trunc_label.setText(tr("구간 끝 {0} 에서 잘림 (실제 끝 {1})").format(
                fmt_time(e2, always_hours=True), fmt_time(obs, always_hours=True)) if cut else "")
            span_ms = e2 - s
        else:
            self.trunc_label.setText("")
            span_ms = None
        n = len(self.related_ids())
        h = continuation_hint(span_ms, n, self._short_max_ms, self._extended_min)
        parts = []
        if span_ms is not None:
            parts.append(tr("이어진 길이 {0}초").format(f"{span_ms / 1000:.1f}"))
        parts.append(tr("관련 채팅 {0}개").format(n))
        if h:
            parts.append(tr("기준 참고: {0} (고르는 것은 라벨러)").format(tr(TYPE_LABELS[h])))
        self.hint_label.setText(" · ".join(parts))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _err_text(e: Exception) -> str:
    probs = getattr(e, "problems", None)
    if isinstance(probs, list) and probs:
        return "; ".join(str(p) for p in probs[:3])
    return str(e) or type(e).__name__


__all__ = ["ContinuationEditor", "truncate_span", "continuation_hint", "TYPES", "TYPE_LABELS"]
