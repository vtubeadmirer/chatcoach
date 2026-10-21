"""증거 7항목 체크 칸 (안내서 v0.1 4-1, 03 5-7, label-event.schema.json $defs/evidence).

(채팅, 후보 발화) 짝 하나의 증거다. 반응 편집기가 후보 발화마다 값을 따로 들고 이 칸에 갈아 끼운다.

- 강도(강·중·약)는 라벨에 저장하지 않고 기준서 시작값(evidence_strength)에서 묶음 제목만 정한다(03 5-7).
- '시간 창'(within_time_window)은 앱이 채팅 시각과 발화 시작으로 자동 계산해 체크하고, 라벨러가 확인한다
  (스키마 설명). 라벨러가 손으로 바꾸면 그 뒤로는 자동으로 덮어쓰지 않는다(다른 짝으로 바꾸면 풀린다).
- 체크하면 그때의 VOD 시각을 at_ms 로 적는다(01 G14 '체크한 증거마다 시각 메모'). 메모(note)는 선택.
- 등급을 고르지 않는다. 이 칸은 사실 체크만 한다(01 3-1).
"""
from __future__ import annotations

from typing import Callable, Mapping

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QLineEdit, QWidget

from ...core import rules
from ...core.timecode import fmt_time
from ...i18n import tr

EVIDENCE_KEYS = rules.EVIDENCE_KEYS
EVIDENCE_LABELS = {
    "nickname_called": "닉네임 호명",
    "text_repeated": "문구 되풀이",
    "direct_reference": "직접 가리킴",
    "answer_form": "질문에 맞는 답",
    "unique_content": "채팅에만 있던 내용",
    "within_time_window": "시간 창 안",
    "same_topic": "같은 주제",
}
STRENGTH_LABELS = {"strong": "강", "medium": "중", "weak": "약", "gate": "조건"}
NOTE_MAX = 200


def empty_evidence() -> dict:
    """7항목 모두 '없음'."""
    return {k: {"present": False, "at_ms": None} for k in EVIDENCE_KEYS}


def normalize_evidence(ev: Mapping | None) -> dict:
    """저장 모양으로 맞춘다: 7키 모두, present 는 bool, at_ms 는 int|None, note 는 비어 있으면 뺀다."""
    out = empty_evidence()
    for k in EVIDENCE_KEYS:
        v = (ev or {}).get(k)
        if isinstance(v, Mapping):
            item = {"present": bool(v.get("present")), "at_ms": _ms_or_none(v.get("at_ms"))}
            note = v.get("note")
            if isinstance(note, str) and note.strip():
                item["note"] = note.strip()[:NOTE_MAX]
            out[k] = item
        elif isinstance(v, bool):
            out[k] = {"present": v, "at_ms": None}
    return out


def _ms_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


class EvidenceWidget(QWidget):
    """증거 7항목. value() 는 스키마 $defs/evidence 모양의 dict 를 돌려준다."""

    changed = Signal()

    def __init__(self, parent=None, *, cfg: rules.RuleConfig | None = None,
                 position_fn: Callable[[], int] | None = None):
        super().__init__(parent)
        self._cfg = cfg or rules.V0_1
        self._position_fn = position_fn
        self._at: dict[str, int | None] = {k: None for k in EVIDENCE_KEYS}
        self._time_auto: bool | None = None     # 자동 계산 값(모르면 None)
        self._time_manual = False               # 라벨러가 시간 창을 손으로 바꿨는가
        self._loading = False
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        self.boxes: dict[str, QCheckBox] = {}
        self.notes: dict[str, QLineEdit] = {}
        self.at_labels: dict[str, QLabel] = {}
        self.group_labels: dict[str, QLabel] = {}
        self.time_info = QLabel("", self)
        self.time_info.setWordWrap(True)
        self._grid = grid
        self._build()

    # ------------------------------------------------------------ 만들기
    def _build(self) -> None:
        """묶음을 (다시) 만든다.

        15 5-2 의 1번(실제 창 1366 에서 오른쪽 밖 24, 끝 656 같음): 부모 없이 만든 부품은 레이아웃이 자리를 잡기 전까지
        기본 크기 640x480 을 들고 있다. 편집기 칸이 숨은 동안 set_config 로 다시 만들면 보이기 전까지 그대로라
        잘림 셈에 24개(체크 7·시각 7·메모 7·묶음 제목 3)가 오른쪽 밖으로 잡혔다. 부품을 이 칸을 부모로 만들고,
        다 넣은 뒤 레이아웃을 바로 돌려(activate) 숨은 상태에서도 칸 폭 안에 자리를 잡게 한다."""
        grid = self._grid
        while grid.count():
            it = grid.takeAt(0)
            w = it.widget()
            if w is not None and w is not self.time_info:
                w.hide()                      # deleteLater 전까지 남는 옛 부품이 셈·그리기에 끼지 않게
                w.deleteLater()
        self.boxes.clear()
        self.notes.clear()
        self.at_labels.clear()
        self.group_labels.clear()
        row = 0
        st = self._cfg.evidence_strength
        for strength in ("strong", "medium", "weak", "gate"):
            keys = [k for k in EVIDENCE_KEYS if st[k] == strength]
            if not keys:
                continue
            g = QLabel(tr(STRENGTH_LABELS[strength]) + ":", self)
            self.group_labels[strength] = g
            grid.addWidget(g, row, 0)
            for k in keys:
                cb = QCheckBox(tr(EVIDENCE_LABELS[k]), self)
                cb.setObjectName(f"ev_{k}")
                cb.toggled.connect(lambda on, key=k: self._on_toggled(key, on))
                note = QLineEdit(self)
                note.setObjectName(f"note_{k}")
                note.setMaxLength(NOTE_MAX)
                note.setPlaceholderText(tr("메모(선택)"))
                note.textEdited.connect(lambda _t: self._emit())
                at = QLabel("", self)
                at.setMinimumWidth(60)
                self.boxes[k], self.notes[k], self.at_labels[k] = cb, note, at
                grid.addWidget(cb, row, 1)
                grid.addWidget(at, row, 2)
                grid.addWidget(note, row, 3)
                row += 1
                if k == rules.TIME_KEY:
                    grid.addWidget(self.time_info, row, 1, 1, 3)
                    row += 1
        grid.setColumnStretch(3, 1)
        grid.invalidate()
        grid.activate()

    def set_config(self, cfg: rules.RuleConfig) -> None:
        """기준서 시작값이 바뀌면 묶음(강·중·약)을 다시 만든다. 값은 유지한다."""
        v = self.value()
        self._cfg = cfg
        self._build()
        self.set_value(v)

    def set_position_fn(self, fn: Callable[[], int] | None) -> None:
        self._position_fn = fn

    # ------------------------------------------------------------ 값
    def value(self) -> dict:
        out = {}
        for k in EVIDENCE_KEYS:
            on = self.boxes[k].isChecked()
            item = {"present": on, "at_ms": self._at[k] if on else None}
            note = self.notes[k].text().strip()
            if note:
                item["note"] = note[:NOTE_MAX]
            out[k] = item
        return out

    def set_value(self, ev: Mapping | None, *, time_manual: bool | None = None) -> None:
        """값을 갈아 끼운다(신호 없음). time_manual 을 주면 시간 창 수동 표시도 함께 바꾼다."""
        ev = normalize_evidence(ev)
        self._loading = True
        try:
            for k in EVIDENCE_KEYS:
                self.boxes[k].setChecked(ev[k]["present"])
                self._at[k] = ev[k]["at_ms"]
                self.notes[k].setText(ev[k].get("note", ""))
                self._show_at(k)
        finally:
            self._loading = False
        if time_manual is not None:
            self._time_manual = bool(time_manual)

    def clear(self) -> None:
        self.set_value(None, time_manual=False)
        self._time_auto = None
        self.time_info.setText("")

    def time_manual(self) -> bool:
        return self._time_manual

    def counts(self) -> tuple[int, int, int]:
        """체크한 강·중·약 개수(화면 표시용. 등급 셈은 rules.grade_conditions 가 한다)."""
        st = self._cfg.evidence_strength
        on = [k for k in EVIDENCE_KEYS if self.boxes[k].isChecked()]
        return (sum(st[k] == "strong" for k in on), sum(st[k] == "medium" for k in on),
                sum(st[k] == "weak" for k in on))

    # ------------------------------------------------------------ 시간 창 자동
    def set_time_window(self, chat_ms: int | None, utterance_start_ms: int | None) -> bool | None:
        """채팅 시각과 발화 시작으로 '시간 창 안' 을 계산한다. 라벨러가 손대지 않았으면 체크도 맞춘다.

        돌려주는 값: 계산 결과(모르면 None). 표시 문구도 바꾼다. 시간만으로 등급이 오르지 않는 규칙은
        rules.grade_conditions 가 지킨다(다른 증거가 없으면 세지 않음).
        """
        win = self._cfg.time_window_ms
        if chat_ms is None or utterance_start_ms is None:
            self._time_auto = None
            self.time_info.setText(tr("시간 창: 발화 시작을 정하면 계산합니다"))
            return None
        d = int(utterance_start_ms) - int(chat_ms)
        inside = rules.within_time_window(int(chat_ms), int(utterance_start_ms), self._cfg)
        self._time_auto = inside
        sec = f"{d / 1000:+.1f}"
        lim = f"{win / 1000:g}"
        if inside:
            txt = tr("시간 창: 채팅 뒤 {0}초 ({1}초 안 — 다른 증거가 있을 때만 셈)").format(sec, lim)
        elif d < 0:
            txt = tr("시간 창: 발화가 채팅보다 {0}초 앞섬 (창 밖)").format(f"{-d / 1000:.1f}")
        else:
            txt = tr("시간 창: 채팅 뒤 {0}초 ({1}초 넘음, 창 밖)").format(sec, lim)
        if self._time_manual and self.boxes[rules.TIME_KEY].isChecked() != inside:
            txt += " · " + tr("손으로 바꿈")
        self.time_info.setText(txt)
        if not self._time_manual:
            box = self.boxes[rules.TIME_KEY]
            if box.isChecked() != inside:
                self._loading = True
                try:
                    box.setChecked(inside)
                    self._at[rules.TIME_KEY] = int(utterance_start_ms) if inside else None
                    self._show_at(rules.TIME_KEY)
                finally:
                    self._loading = False
                self.changed.emit()
            elif inside and self._at[rules.TIME_KEY] is None:
                self._at[rules.TIME_KEY] = int(utterance_start_ms)
                self._show_at(rules.TIME_KEY)
        return inside

    # ------------------------------------------------------------ 내부
    def _on_toggled(self, key: str, on: bool) -> None:
        if self._loading:
            return
        if key == rules.TIME_KEY:
            self._time_manual = True
        if on:
            pos = None
            if self._position_fn is not None:
                try:
                    pos = int(self._position_fn())
                except Exception:  # noqa: BLE001
                    pos = None
            self._at[key] = pos
        else:
            self._at[key] = None
        self._show_at(key)
        self._emit()

    def _show_at(self, key: str) -> None:
        v = self._at[key]
        self.at_labels[key].setText("" if v is None or not self.boxes[key].isChecked()
                                    else fmt_time(v, frac_digits=1, always_hours=True))

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()


__all__ = ["EvidenceWidget", "EVIDENCE_KEYS", "EVIDENCE_LABELS", "empty_evidence", "normalize_evidence"]
