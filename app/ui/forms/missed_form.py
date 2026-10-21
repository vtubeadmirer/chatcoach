"""놓친 채팅 후보 폼 (4단계) — 작업대 오른쪽 칸 (안내서 6장, 03 6-10, 06 2-9·4-6, 08 D-10 3·D-12 5).

대상 목록(core.missed.select_targets 결과)에서 채팅 하나를 고르고 조건 ②③④·점검 질문·사유 코드·확신도·근거 한 줄을
매겨 missed_candidate 초안을 save 콜백으로 넘긴다. 조건 ① 은 앱이 계산한 값을 보이기만 한다(바꿀 수 없음).

작업대와의 약속 (app/ui/forms/__init__.py 공통 모양)
- `MissedForm(save, cancel=None, *, ops=None)`.
  - `save(draft) -> dict`: draft = {"kind": "missed_candidate", "entity_id", "supersedes", "body"} (body = 03 6-10).
    새 대상이면 entity_id·supersedes 가 None, 이미 판이 있으면 그 대상의 새 판이다. 작업대가 store.append_label 로 잇는다.
    돌려준 레코드(봉투 `id`·`entity_id` 포함)를 그 채팅의 현재 판으로 기억한다.
  - `cancel()`: Esc. 고치던 입력을 버리고 부른다.
  - `ops(kind, body)`: 선택. 저장 뒤 경고(근거에 시각 없음 → warning rationale_without_time)를 운영 기록으로 남길 때.
- `load_context(selection=…, chat_texts=…, scope_ranges=…, media_coverage=…, thresholds=…, …)`:
  selection = core.missed.select_targets(…) 결과(작업대가 자기 세션 표본·반응·구간으로 계산). chat_texts = {chat_id: 본문}
  (닉네임은 넘기지 않는다). scope_ranges = core.missed.scope_ranges_of(session.open.scope, session_kind) (D-12 5).
  media_coverage = 구간 폼·반응 편집기와 같은 '영상이 덮는 VOD 범위' (D-10 3). None = 검사 안 함, [] = 영상 없음(모두 막음).
  넘기지 않은 인자는 그대로 둔다.
- 시그널: `targetChanged(chat_id, t_ms)` — 작업대가 영상을 그 채팅의 확인 범위로 옮긴다. `recordSaved(dict)`, `message(str)`.

영상 밖·범위 밖 (D-10 3, D-12 5): 고른 대상의 7-2 확인 범위가 영상 밖이거나 세션 범위(재라벨 블록) 밖이면 저장을 막고
이유를 보인다. 대상 고르기에서 이미 빠지지만, 영상을 바꾼 뒤 load_context(media_coverage=…) 만 다시 부른 경우를 위해
저장 직전에도 core.missed.validate_draft 로 다시 본다.

블라인드: 이 폼은 받은 자기 세션의 대상·본문만 보인다. 다른 세션 라벨·엔진·AI 를 읽는 길이 없다.

단축키 (02 7-6: 수정자 없는 글자 키는 라벨 값을 바꾸지 않는다)
- `Alt+1`~`Alt+3` 확신도, `Ctrl+T` 근거 칸에 지금 시각, `Ctrl+Enter` 저장, `Esc` 취소,
  `Alt+Down` / `Alt+Up` 다음·앞 대상.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from ...core import missed as mc
from ...core import sampling
from ...core.timecode import fmt_time
from ...i18n import tr

LOG = logging.getLogger(__name__)

CONFIDENCE_NAMES = {"certain": "확실함", "probably": "아마도", "undeterminable": "판단 불가"}
CONFIDENCE_KEYS = {"certain": "Alt+1", "probably": "Alt+2", "undeterminable": "Alt+3"}
SAVE_KEYS = ("Ctrl+Return", "Ctrl+Enter")
CANCEL_KEY = "Escape"
TIME_KEY = "Ctrl+T"
NEXT_KEY, PREV_KEY = "Alt+Down", "Alt+Up"
OPS_WARNING_CODES = frozenset({"rationale_without_time"})
_KEEP = object()        # load_context 인자를 넘기지 않음(None 은 '검사 안 함·제한 없음' 이라 따로 둔다)
_TRI = ((True, "예"), (False, "아니오"), (None, "미입력"))
COND_TEXT = {
    "cond2": "② 읽힐 기회 — 화면에 충분히 보였고 채팅이 너무 빠르지 않았다",
    "cond3": "③ 대화 가치 — 질문·경험 공유 요청·새 관점·소재 (인사·ㅋㅋ·반복 구호는 아님)",
    "cond4": "④ 반응 증거 없음 — 앞뒤 창 안에서 스트리머가 이미 다루거나 답하지 않았다",
    "check_question": "점검 질문 — 읽고 답했다면 대화가 한 번 이상 더 이어졌을 것 같은가",
}


def _ms(t: int | None) -> str:
    return "—" if t is None else fmt_time(int(t), frac_digits=1, always_hours=True)


class _Tri(QWidget):
    """예·아니오·미입력 세 칸 라디오."""

    def __init__(self, on_change: Callable[[], None], parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.group = QButtonGroup(self)
        self.buttons: dict[Any, QRadioButton] = {}
        for i, (v, name) in enumerate(_TRI):
            b = QRadioButton(tr(name))
            self.group.addButton(b, i)
            self.buttons[v] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.buttons[None].setChecked(True)
        self.group.idClicked.connect(lambda _i: on_change())

    def value(self) -> bool | None:
        return _TRI[self.group.checkedId()][0] if self.group.checkedId() >= 0 else None

    def set_value(self, v: bool | None) -> None:
        self.buttons.get(v if isinstance(v, bool) else None, self.buttons[None]).setChecked(True)


class MissedForm(QWidget):
    """4단계 놓친 채팅 후보 판정 칸."""

    targetChanged = Signal(str, int)
    recordSaved = Signal(dict)
    message = Signal(str)

    def __init__(self, save: Callable[[dict], dict], cancel: Callable[[], None] | None = None, parent=None, *,
                 ops: Callable[[str, dict], Any] | None = None):
        super().__init__(parent)
        self._save_cb = save
        self._cancel_cb = cancel
        self._ops_cb = ops
        self._selection: mc.Selection | None = None
        self._targets: list[mc.Target] = []
        self._extras: list[mc.Target] = []           # 표본 밖 후보(scope = extra). 표본 대상 뒤에 붙는다(14 4-2 의 11)
        self._saved: dict[str, Mapping] = {}         # chat_id → 이번 화면에서 저장한(또는 받은) 현재 판
        self._texts: Mapping[str, str] = {}
        self._scope: tuple[tuple[int, int], ...] | None = None
        self._coverage = None                         # sampling.normalize_coverage 결과. None = 검사 안 함
        self._thr = mc.MissedThresholds()
        self._pre = sampling.DEFAULT_PRE_ROLL_MS
        self._post = sampling.DEFAULT_POST_ROLL_MS
        self._read_only = False
        self._position_fn: Callable[[], int] | None = None
        self._cur = -1
        self._loading = False
        self._last_check: mc.Check | None = None
        self._build()
        self._install_shortcuts()
        self._refresh()

    # ================================================================ 화면
    def _build(self) -> None:
        root = QVBoxLayout(self)
        self.lbl_head = QLabel()
        self.lbl_head.setWordWrap(True)
        root.addWidget(self.lbl_head)
        self.list = QListWidget()
        self.list.setMinimumHeight(110)
        self.list.currentRowChanged.connect(self._on_row)
        root.addWidget(self.list, 1)

        self.lbl_chat = QLabel()
        self.lbl_chat.setWordWrap(True)
        self.lbl_chat.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.lbl_chat)
        self.lbl_cond1 = QLabel()
        self.lbl_cond1.setWordWrap(True)
        root.addWidget(self.lbl_cond1)
        self.lbl_aux = QLabel()
        self.lbl_aux.setWordWrap(True)
        root.addWidget(self.lbl_aux)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.tri: dict[str, _Tri] = {}
        for k in ("cond2", "cond3", "cond4", "check_question"):
            w = _Tri(self._on_changed)
            self.tri[k] = w
            form.addRow(QLabel(tr(COND_TEXT[k])), w)
        self.reason = QComboBox()
        self.reason.addItem(tr("— 사유 코드 —"), None)
        for c in mc.REASON_CODES:
            self.reason.addItem(f"{c} · {tr(mc.REASON_NAMES[c])}", c)
        self.reason.currentIndexChanged.connect(lambda _i: self._on_changed())
        form.addRow(QLabel(tr("후보가 아니면 사유")), self.reason)
        conf_row = QWidget()
        cl = QHBoxLayout(conf_row)
        cl.setContentsMargins(0, 0, 0, 0)
        self.conf_group = QButtonGroup(self)
        self.conf_buttons: dict[str, QRadioButton] = {}
        for i, c in enumerate(mc.CONFIDENCES):
            b = QRadioButton(f"{tr(CONFIDENCE_NAMES[c])} ({CONFIDENCE_KEYS[c]})")
            self.conf_group.addButton(b, i)
            self.conf_buttons[c] = b
            cl.addWidget(b)
        cl.addStretch(1)
        self.conf_group.idClicked.connect(lambda _i: self._on_changed())
        form.addRow(QLabel(tr("확신도 (필수)")), conf_row)
        self.rationale = QLineEdit()
        self.rationale.setMaxLength(mc.RATIONALE_MAX)
        self.rationale.setPlaceholderText(tr("근거 한 줄 (시각 포함, Ctrl+T)"))
        self.rationale.textEdited.connect(lambda _t: self._on_changed())
        form.addRow(QLabel(tr("근거")), self.rationale)
        root.addLayout(form)

        self.lbl_result = QLabel()
        self.lbl_result.setWordWrap(True)
        root.addWidget(self.lbl_result)
        self.lbl_errors = QLabel()
        self.lbl_errors.setWordWrap(True)
        self.lbl_errors.setStyleSheet("color: #b3261e;")
        root.addWidget(self.lbl_errors)
        row = QHBoxLayout()
        self.btn_save = QPushButton(tr("저장 (Ctrl+Enter)"))
        self.btn_save.clicked.connect(self.save)
        row.addStretch(1)
        row.addWidget(self.btn_save)
        root.addLayout(row)
        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

    def _install_shortcuts(self) -> None:
        def sc(key: str, fn) -> None:
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
        for c, k in CONFIDENCE_KEYS.items():
            sc(k, lambda c=c: self.set_confidence(c))
        for k in SAVE_KEYS:
            sc(k, self.save)
        sc(CANCEL_KEY, self.cancel)
        sc(TIME_KEY, self.insert_time)
        sc(NEXT_KEY, lambda: self.select_relative(1))
        sc(PREV_KEY, lambda: self.select_relative(-1))

    # ================================================================ 맥락
    def load_context(self, *, selection: mc.Selection | None = _KEEP, chat_texts: Mapping[str, str] | None = None,
                     scope_ranges=_KEEP, media_coverage=_KEEP, thresholds: Mapping | mc.MissedThresholds | None = None,
                     pre_roll_ms: int | None = None, post_roll_ms: int | None = None, read_only: bool | None = None,
                     position_fn: Callable[[], int] | None = None) -> None:
        """작업대 상태를 받는다. 넘기지 않은 인자는 그대로 둔다.
        thresholds: guide-config values(또는 그 missed 칸) 또는 MissedThresholds."""
        prev = self.current_chat_id()
        if thresholds is not None:
            self._thr = thresholds if isinstance(thresholds, mc.MissedThresholds) else mc.thresholds_from_guide(thresholds)
        if pre_roll_ms is not None:
            self._pre = int(pre_roll_ms)
        if post_roll_ms is not None:
            self._post = int(post_roll_ms)
        if scope_ranges is not _KEEP:
            self._scope = mc.normalize_ranges(scope_ranges)
        if media_coverage is not _KEEP:
            self._coverage = sampling.normalize_coverage(media_coverage)
        if chat_texts is not None:
            self._texts = dict(chat_texts)
        if read_only is not None:
            self._read_only = bool(read_only)
        if position_fn is not None:
            self._position_fn = position_fn
        if selection is not _KEEP:
            self._selection = selection
            self._targets = list(selection.targets) if selection is not None else []
            have = {t.chat_id for t in self._targets}
            self._targets += [t for t in self._extras if t.chat_id not in have]
            self._saved = {t.chat_id: t.existing for t in self._targets if t.existing is not None}
            self._fill_list()
            ids = [t.chat_id for t in self._targets]
            row = ids.index(prev) if prev in ids else (self._first_undone() if ids else -1)
            self._select_row(row)
        self._refresh()

    # ================================================================ 조회
    @property
    def targets(self) -> list[mc.Target]:
        return list(self._targets)

    def current_target(self) -> mc.Target | None:
        return self._targets[self._cur] if 0 <= self._cur < len(self._targets) else None

    def current_chat_id(self) -> str | None:
        t = self.current_target()
        return t.chat_id if t is not None else None

    def done_count(self) -> int:
        return sum(1 for t in self._targets if t.chat_id in self._saved)

    @property
    def read_only(self) -> bool:
        return self._read_only

    def media_coverage(self):
        return None if self._coverage is None else list(self._coverage)

    def last_check(self) -> mc.Check | None:
        return self._last_check

    def _first_undone(self) -> int:
        for i, t in enumerate(self._targets):
            if t.chat_id not in self._saved:
                return i
        return 0

    def _target_now(self, t: mc.Target) -> mc.Target:
        """저장한 판을 existing 으로 붙인 대상(다음 저장이 새 판이 되게)."""
        ex = self._saved.get(t.chat_id)
        if ex is t.existing:
            return t
        return mc.Target(t.chat_id, t.t_ms, t.chat_kind, t.sample_ref, t.source_segment_id, t.reaction_entity_id,
                         t.state, t.cond1, t.aux, t.suggested_reason, t.suggested_source, ex)

    # ================================================================ 목록
    def _item_text(self, t: mc.Target) -> str:
        mark = "✓" if t.chat_id in self._saved else "·"
        body = self._saved.get(t.chat_id)
        res = ""
        if body is not None:
            b = body.get("body") if isinstance(body.get("body"), Mapping) else {}
            res = tr("후보") if b.get("is_candidate") else (b.get("reason_code") or "")
        state = tr(mc.STATE_NAMES.get(t.state, t.state))
        return f"{mark} {_ms(t.t_ms)}  [{state}]  {res}"

    def _fill_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for t in self._targets:
            self.list.addItem(QListWidgetItem(self._item_text(t)))
        self.list.blockSignals(False)

    def _update_item(self, i: int) -> None:
        if 0 <= i < self.list.count():
            self.list.item(i).setText(self._item_text(self._targets[i]))

    def add_extra_target(self, target: mc.Target, *, text: str | None = None, select: bool = True) -> bool:
        """표본 밖 후보(scope = extra, sample_ref 없음)를 목록 끝에 더한다(14 4-2 의 11). 이미 목록에 있으면 고르기만.
        target 은 core.missed.extra_target 으로 만든다. 표본 대상(sample_ref 있음)은 받지 않는다(False).
        text: 화면에 보일 채팅 본문(닉네임 없음)."""
        if target.sample_ref is not None:
            return False
        if text is not None:
            self._texts = {**self._texts, target.chat_id: text}
        for i, t in enumerate(self._targets):
            if t.chat_id == target.chat_id:
                if select:
                    self._select_row(i)
                return True
        self._extras = [x for x in self._extras if x.chat_id != target.chat_id] + [target]
        self._targets.append(target)
        if target.existing is not None:
            self._saved[target.chat_id] = target.existing
        self.list.addItem(QListWidgetItem(self._item_text(target)))
        if select:
            self._select_row(len(self._targets) - 1)
        else:
            self._refresh()
        return True

    def extra_targets(self) -> list[mc.Target]:
        return list(self._extras)

    def select_chat(self, chat_id: str) -> bool:
        for i, t in enumerate(self._targets):
            if t.chat_id == chat_id:
                self._select_row(i)
                return True
        return False

    def select_relative(self, step: int) -> bool:
        if not self._targets:
            return False
        i = max(0, min(len(self._targets) - 1, (self._cur if self._cur >= 0 else 0) + step))
        if i == self._cur:
            return False
        self._select_row(i)
        return True

    def _select_row(self, i: int) -> None:
        self.list.blockSignals(True)
        self.list.setCurrentRow(i)
        self.list.blockSignals(False)
        self._on_row(i)

    def _on_row(self, i: int) -> None:
        self._cur = i
        t = self.current_target()
        self._load_answers(t)
        self._refresh()
        if t is not None:
            self.targetChanged.emit(t.chat_id, int(t.t_ms))

    def _load_answers(self, t: mc.Target | None) -> None:
        self._loading = True
        try:
            ex = self._saved.get(t.chat_id) if t is not None else None
            if ex is not None:
                a = mc.answers_from_body(ex.get("body"))
            else:
                a = mc.Answers(reason_code=t.suggested_reason if t is not None else None)
            self.tri["cond2"].set_value(a.cond2)
            self.tri["cond3"].set_value(a.cond3)
            self.tri["cond4"].set_value(a.cond4)
            self.tri["check_question"].set_value(a.check_question)
            j = self.reason.findData(a.reason_code)
            self.reason.setCurrentIndex(j if j >= 0 else 0)
            self.conf_group.setExclusive(False)
            for b in self.conf_buttons.values():
                b.setChecked(False)
            self.conf_group.setExclusive(True)
            if a.confidence in self.conf_buttons:
                self.conf_buttons[a.confidence].setChecked(True)
            self.rationale.setText(a.rationale)
        finally:
            self._loading = False

    # ================================================================ 입력
    def answers(self) -> mc.Answers:
        conf = None
        for c, b in self.conf_buttons.items():
            if b.isChecked():
                conf = c
        return mc.Answers(self.tri["cond2"].value(), self.tri["cond3"].value(), self.tri["cond4"].value(),
                          self.reason.currentData(), self.tri["check_question"].value(), conf, self.rationale.text())

    def set_answers(self, a: mc.Answers) -> None:
        """시험·작업대용: 입력 칸을 한 번에 채운다."""
        self._loading = True
        try:
            for k in ("cond2", "cond3", "cond4", "check_question"):
                self.tri[k].set_value(getattr(a, k))
            j = self.reason.findData(a.reason_code)
            self.reason.setCurrentIndex(j if j >= 0 else 0)
            if a.confidence in self.conf_buttons:
                self.conf_buttons[a.confidence].setChecked(True)
            self.rationale.setText(a.rationale or "")
        finally:
            self._loading = False
        self._refresh()

    def set_confidence(self, c: str) -> None:
        if c in self.conf_buttons and self.isEnabled():
            self.conf_buttons[c].setChecked(True)
            self._on_changed()

    def insert_time(self) -> None:
        """Ctrl+T: 근거 칸 커서 자리에 지금 VOD 시각."""
        if self._position_fn is None:
            return
        try:
            t = fmt_time(int(self._position_fn()), ms_digits=False, always_hours=True)
        except Exception:  # noqa: BLE001
            return
        self.rationale.insert(t + " ")
        self.rationale.setFocus()
        self._on_changed()

    def _on_changed(self) -> None:
        if not self._loading:
            self._refresh()

    # ================================================================ 초안·검사
    def draft(self) -> dict | None:
        t = self.current_target()
        return None if t is None else mc.build_draft(self._target_now(t), self.answers())

    def check(self) -> mc.Check:
        t = self.current_target()
        if t is None:
            return mc.Check((tr("고른 대상이 없습니다"),), ())
        t = self._target_now(t)
        d = mc.build_draft(t, self.answers())
        errs: list[str] = []
        if self._read_only:
            errs.append(tr("읽기 전용입니다"))
        c = mc.validate_draft(d, chat_t_ms=t.t_ms, chat_kind=t.chat_kind, coverage=self._coverage,
                              scope_ranges=self._scope, pre_roll_ms=self._pre, post_roll_ms=self._post, target=t)
        return mc.Check(tuple(errs) + tuple(tr(e) for e in c.errors), c.warnings)

    def save(self) -> dict | None:
        """Ctrl+Enter. 막히면 None(이유를 보임). 성공하면 저장된 레코드."""
        t = self.current_target()
        if t is None:
            self._say(tr("고른 대상이 없습니다"))
            return None
        c = self.check()
        self._last_check = c
        if c.errors:
            self._refresh()
            self._say(" · ".join(c.errors))
            return None
        d = mc.build_draft(self._target_now(t), self.answers())
        try:
            rec = self._save_cb(d)
        except Exception as e:  # noqa: BLE001 — store 의 거부(형식·판 연결·읽기 전용)를 화면에 보인다
            LOG.warning("놓친 후보 저장 실패: %s", type(e).__name__)
            self._say(tr("저장 실패: {0}").format(e))
            return None
        if not isinstance(rec, Mapping) or not isinstance(rec.get("id"), str):
            self._say(tr("save 가 저장된 레코드(id)를 돌려주지 않았습니다"))
            return None
        rec = dict(rec)
        rec.setdefault("body", d["body"])
        self._saved[t.chat_id] = rec
        if t.sample_ref is None:                      # 표본 밖 후보: 다시 실을 때도 저장한 판을 잇게
            self._extras = [self._target_now(x) if x.chat_id == t.chat_id else x for x in self._extras]
        for code, text in c.warnings:
            self._write_warning(code, rec.get("entity_id"), text)
        self._update_item(self._cur)
        self.recordSaved.emit(rec)
        b = d["body"]
        self._say(tr("저장했습니다 — {0}").format(tr("후보") if b["is_candidate"]
                                                  else tr("후보 아님 ({0})").format(b["reason_code"])))
        self._refresh()
        return rec

    def _write_warning(self, code: str, entity_id: str | None, detail: str) -> None:
        if self._ops_cb is None:
            return
        if code not in OPS_WARNING_CODES:
            detail, code = f"{code}: {detail}", "other"
        try:
            self._ops_cb("warning", {"code": code, "action": "shown", "entity_id": entity_id, "detail": detail[:300]})
        except Exception as e:  # noqa: BLE001 — 경고 기록 실패는 저장을 되돌리지 않는다
            LOG.warning("경고 기록 실패: %s", type(e).__name__)

    def cancel(self) -> None:
        """Esc: 고치던 입력을 버리고(저장된 판으로 되돌림) cancel 콜백."""
        self._load_answers(self.current_target())
        self._refresh()
        if self._cancel_cb is not None:
            self._cancel_cb()

    def has_unsaved(self) -> bool:
        t = self.current_target()
        if t is None:
            return False
        ex = self._saved.get(t.chat_id)
        base = mc.answers_from_body(ex.get("body")) if ex is not None else mc.Answers(reason_code=t.suggested_reason)
        now = self.answers()
        return (now.cond2, now.cond3, now.cond4, now.reason_code, now.check_question, now.confidence,
                now.rationale.strip()) != (base.cond2, base.cond3, base.cond4, base.reason_code, base.check_question,
                                           base.confidence, (base.rationale or "").strip())

    # ================================================================ 표시
    def _say(self, text: str) -> None:
        self.lbl_status.setText(text)
        self.message.emit(text)

    def _refresh(self) -> None:
        n = len(self._targets)
        head = tr("④ 놓친 채팅 후보 — 대상 {0}건 · 판정 {1}건").format(n, self.done_count())
        sel = self._selection
        if sel is not None:
            skipped = [(k, v) for k, v in sel.counts.items() if v]
            if skipped:
                names = {mc.SKIP_NOT_JUDGED: "표본 미판정", mc.SKIP_REACTED: "직접·높음",
                         mc.SKIP_OUT_OF_SCOPE: "블록 밖", mc.SKIP_OUT_OF_MEDIA: "영상 밖",
                         mc.SKIP_SAMPLES_NOT_DONE: "③ 미완료", mc.SKIP_NO_MESSAGE: "원문 없음"}
                head += " · " + tr("뺀 것") + " " + ", ".join(f"{tr(names.get(k, k))} {v}" for k, v in skipped)
        if self._read_only:
            head += " · " + tr("읽기 전용")
        self.lbl_head.setText(head)
        t = self.current_target()
        editable = t is not None and not self._read_only
        for w in (*self.tri.values(), self.rationale, *self.conf_buttons.values()):
            w.setEnabled(editable)
        if t is None:
            self.lbl_chat.setText(tr("대상이 없습니다") if not n else tr("대상을 고르세요"))
            self.lbl_cond1.setText("")
            self.lbl_aux.setText("")
            self.lbl_result.setText("")
            self.lbl_errors.setText("")
            self.reason.setEnabled(False)
            self.btn_save.setEnabled(False)
            return
        text = self._texts.get(t.chat_id, "")
        self.lbl_chat.setText(f"{_ms(t.t_ms)}  {text[:200]}")
        c1 = t.cond1
        c1_txt = tr("예") if c1.value else (tr("아니오 — 저스트채팅 구간 밖") if not c1.in_segment
                                            else tr("아니오 — 경계 완충 안"))
        self.lbl_cond1.setText(tr("① 구간·완충 (자동): {0}").format(c1_txt))
        aux = t.aux
        a, z = mc.prior_window(t.t_ms, self._thr)
        exp = tr("미정(보이는 줄 수 시작값 없음)") if aux.exposure_est_ms is None else f"{aux.exposure_est_ms / 1000:.1f}s"
        self.lbl_aux.setText(tr("② 보조값: 직전 10초 채팅 {0}개 · 노출 추정 {1} · ④ 볼 범위 {2}~{3}")
                             .format(aux.chat_rate_10s, exp, _ms(a), _ms(z)))
        ans = self.answers()
        cand = mc.is_candidate(c1.value, ans.cond2, ans.cond3, ans.cond4) and t.chat_kind == mc.TEXT_KIND
        self.reason.setEnabled(editable and not cand and t.chat_kind == mc.TEXT_KIND)
        model = self.reason.model()
        for i in range(self.reason.count()):
            code = self.reason.itemData(i)
            ok = code is None or code in mc.MANUAL_REASON_CODES or (code == "A" and not c1.value) \
                or (code == "F" and t.chat_kind != mc.TEXT_KIND)
            it = model.item(i) if hasattr(model, "item") else None
            if it is not None:
                it.setEnabled(ok)
        if cand:
            self.lbl_result.setText(tr("결과: 후보 (네 조건 모두 예)"))
        else:
            sug = ""
            if t.suggested_reason:
                sug = " · " + tr("앱 제안: {0}").format(t.suggested_reason)
            self.lbl_result.setText(tr("결과: 후보 아님 — 사유 코드 필요") + sug)
        c = self.check()
        self._last_check = c
        warn = [w for _c, w in c.warnings]
        self.lbl_errors.setText("\n".join(c.errors + tuple(tr(w) for w in warn)))
        self.btn_save.setEnabled(editable and c.ok)


__all__ = ["MissedForm", "CONFIDENCE_KEYS", "SAVE_KEYS", "CANCEL_KEY", "TIME_KEY", "NEXT_KEY", "PREV_KEY"]
