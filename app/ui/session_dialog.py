"""세션 선택 화면 (docs/design/08-decisions-2026-10-20.md D-09 1·4·5, 02 7-2 '5 라벨러·세션 종류', 05 S-37).

프로젝트를 열 때 띄운다. 라벨러 ID 와 세션 종류를 고르면 app.session.SessionContext 를 돌려주고,
고르지 않으면(보기 전용·취소) None 을 돌려준다 — 그러면 작업대는 보기 전용으로 연다(라벨 저장·분석 보기 꺼짐).

화면에 있는 것
- 라벨러: <data>\\labelers.json 명부(이 PC 에만, 표시 이름 포함)에서 고른다. [새 라벨러…] 로 명부에 더한다.
  예시 값을 미리 채우지 않는다(S-37: 두 사람이 예시 ID 를 같이 써 같은 사람 재라벨로 집계되는 일을 막는다).
- 이어 하기: 고른 라벨러 **자기** 세션만 보인다. 다른 라벨러 세션은 이름도 보이지 않는다. 잠긴 세션은 고르면
  읽기 전용으로 열린다(더 쓸 수 없음, 08 D-11 7 — 보기와 잠긴 1차의 재라벨 계획 만들기용). 단 그 1차 세션을 원 세션으로
  하는 자기 같은 사람 재라벨(relabel_same) 계획이 있고 그 재라벨 세션이 아직 잠기지 않았으면 고를 수 없다
  (06 3-3 3일 간격·3-4, app.session.reopen_refusal).
- 새 세션 종류: 1차·재라벨 2종·조정·출처 판정·분석. 지금 프로젝트 상태에서 열 수 있는 것만 켜지고,
  꺼진 것은 까닭을 옆에 적는다(app.session.kind_choices, 이 PC 의 machine4·지금 시각을 넘긴다).
- 계획 요약은 relabel/index.json 으로 고른 라벨러 것만 연다(D-12 2, ProjectSurvey.plans_of → relabel.plans_for_labeler).
  다른 라벨러의 계획 본문은 열지 않는다. 취소된 계획(relabel/<plan_id>.cancel.json)은 고를 수 없다(D-12 7).
- 잘못 잠근 사유(D-11 7·D-12 2): 이 라벨러에게 잠긴 자기 세션이 있고 새 세션을 고르면 선택 칸이 보인다 →
  session.open.reopen_note.
- 재라벨 2종(08 D-11 6, 03 12-2): 고르면 그 라벨러의 계획 파일 하나만 열어(app.relabel.read_plan_for_session)
  scope=blocks·relabel_plan_id 를 session.open 에 넣는다. 같은 사람 재라벨을 3일 간격 전에 열면 사유를 받고
  (ask_early_reason) SessionContext.extra['relabel_too_early'] 로 넘긴다 — 작업대가 store 를 연 뒤 warning 줄로 남긴다.
  켜진 선택의 메모(앞당긴 시작·'같은 machine_id')도 옆에 보인다.
- 기준서 버전 표시(project.json active_guide_version, 본문 해시 앞 8자). 이어 하는 세션은 그 세션의 버전.
- 노출 안내: 분석 세션을 고르면 '열면 기록이 남고 이 라벨러의 라벨링 세션이 노출로 표시된다'(D-09 5).
- [보기 전용으로 열기] · [취소] · [열기].

블라인드(안내서 원칙 3, 03 12장): 이 화면은 엔진 결과·다른 세션 라벨·AI 등급 제안을 보이지 않는다.
읽는 것은 폴더의 파일 이름과, 고른 라벨러 자기 세션 기록 파일의 첫 줄(기준서 버전)뿐이다(app.session.survey_project).
표시 이름은 화면에만 쓰고 로그에 남기지 않는다.

작업대 연결은 통합 쪽이 한다: choose_session(...) 이나 SessionDialog(...).load_context(...) → exec() → context().
"""
from __future__ import annotations

import logging
import os
from dataclasses import replace
from datetime import datetime
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget)

from .. import relabel as relabel_io
from .. import session as sess
from ..i18n import tr

LOG = logging.getLogger("chatcoach")

RESUME_PREFIX = "resume:"
NEW_PREFIX = "new:"


class SessionDialog(QDialog):
    """라벨러·세션 종류를 고르는 대화상자. context() → SessionContext 또는 None.

    - data_root: 명부(labelers.json)·machine.json 이 있는 앱 data 폴더.
    - machine_id_value: 이 PC 의 machine_id(테스트용). 없으면 data_root 의 machine.json 에서 얻는다.
    - now / rand4: 새 세션 ID 를 만들 때의 시각·무작위 4자(테스트용). 기본은 지금·secrets.
    - default_labeler: 처음부터 고를 라벨러(명부에 있을 때만). 통합 쪽이 '마지막에 쓴 라벨러' 를 넘길 수 있다.
    """

    sessionChosen = Signal(object)      # 끝날 때: SessionContext 또는 None(보기 전용·취소)

    def __init__(self, parent=None, *, data_root: str, machine_id_value: str | None = None,
                 now: Callable[[], datetime] | None = None, rand4: Callable[[], str] | None = None,
                 default_labeler: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("세션 고르기"))
        self.setModal(True)
        self.data_root = data_root
        self._mid_value = machine_id_value
        self._now = now
        self._rand4 = rand4
        self._default_labeler = default_labeler
        self._ctx: sess.SessionContext | None = None
        self.view_only = False              # [보기 전용으로 열기] 를 눌렀으면 True (취소는 False)
        self.project_root: str | None = None
        self.project_data: dict | None = None
        self.read_only = False
        self.survey: sess.ProjectSurvey | None = None
        self.roster = sess.LabelerRoster()
        self.existing: list[sess.ExistingSession] = []
        self.choices: list[sess.KindChoice] = []
        self._buttons: dict[str, QRadioButton] = {}
        self._this_m4: str | None = None
        self.early_reason: Callable[[sess.KindChoice], str | None] | None = None   # 시험용: 앞당긴 시작 사유
        self._build()

    # ------------------------------------------------------------------ 화면
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        self.title = QLabel(tr("세션 고르기"))
        self.title.setObjectName("panelTitle")
        lay.addWidget(self.title)
        self.ro_label = QLabel("")
        self.ro_label.setWordWrap(True)
        self.ro_label.setVisible(False)
        lay.addWidget(self.ro_label)

        grid = QGridLayout()
        grid.addWidget(QLabel(tr("라벨러")), 0, 0)
        self.labeler_box = QComboBox()
        self.labeler_box.setMinimumWidth(220)
        self.labeler_box.currentIndexChanged.connect(lambda _i: self._refresh_choices())
        grid.addWidget(self.labeler_box, 0, 1)
        self.btn_new_labeler = QPushButton(tr("새 라벨러…"))
        self.btn_new_labeler.setCheckable(True)
        self.btn_new_labeler.toggled.connect(self._toggle_new_labeler)
        grid.addWidget(self.btn_new_labeler, 0, 2)
        grid.addWidget(QLabel(tr("기준서")), 1, 0)
        self.guide_label = QLabel("")
        grid.addWidget(self.guide_label, 1, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        # 새 라벨러 칸 (기본 숨김)
        self.new_box = QFrame()
        nl = QGridLayout(self.new_box)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.addWidget(QLabel(tr("라벨러 ID")), 0, 0)
        self.new_id = QLineEdit()
        self.new_id.setPlaceholderText(tr("영문 대문자로 시작, 대문자·숫자 2~8자"))   # 예시 ID 를 채우지 않는다 (S-37)
        self.new_id.setMaxLength(8)
        nl.addWidget(self.new_id, 0, 1)
        nl.addWidget(QLabel(tr("표시 이름 (선택, 이 PC 에만)")), 1, 0)
        self.new_name = QLineEdit()
        self.new_name.setMaxLength(sess.DISPLAY_NAME_MAX)
        nl.addWidget(self.new_name, 1, 1)
        self.btn_add_labeler = QPushButton(tr("명부에 추가"))
        self.btn_add_labeler.clicked.connect(self._add_labeler)
        nl.addWidget(self.btn_add_labeler, 0, 2, 2, 1)
        self.new_msg = QLabel("")
        self.new_msg.setObjectName("hint")
        self.new_msg.setWordWrap(True)
        nl.addWidget(self.new_msg, 2, 0, 1, 3)
        self.new_box.setVisible(False)
        lay.addWidget(self.new_box)

        self.roster_msg = QLabel("")
        self.roster_msg.setObjectName("hint")
        self.roster_msg.setWordWrap(True)
        self.roster_msg.setVisible(False)
        lay.addWidget(self.roster_msg)

        # 고르기 칸: 이어 하기 + 새 세션 종류. 한 QButtonGroup 으로 묶어 하나만 고른다.
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.buttonToggled.connect(lambda _b, _on: self._update_state())
        self.choice_area = QWidget()
        self.choice_lay = QVBoxLayout(self.choice_area)
        self.choice_lay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.choice_area)
        scroll.setMinimumHeight(220)
        lay.addWidget(scroll, 1)

        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        lay.addWidget(self.notes)

        # 잘못 잠근 사유 (D-11 7·D-12 2): 잠금은 풀지 않으므로 새 세션 session.open.reopen_note 에 적는다.
        # 이 라벨러에게 잠긴 자기 세션이 있고 새 세션을 고를 때만 보인다. 선택 칸이다.
        self.reopen_box = QWidget()
        rl = QHBoxLayout(self.reopen_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel(tr("앞 세션을 잘못 잠갔다면 그 사유 (선택)")))
        self.reopen_edit = QLineEdit()
        self.reopen_edit.setMaxLength(sess.REOPEN_NOTE_MAX)
        self.reopen_edit.setPlaceholderText(tr("예: 표본 판정을 덜 마치고 잠금 — 이 세션에서 마저 매김 (기록에 남음)"))
        rl.addWidget(self.reopen_edit, 1)
        self.reopen_box.setVisible(False)
        lay.addWidget(self.reopen_box)

        row = QHBoxLayout()
        self.btn_view_only = QPushButton(tr("보기 전용으로 열기"))
        self.btn_view_only.setToolTip(tr("세션 없이 엽니다. 라벨 저장과 분석 보기가 꺼집니다."))
        self.btn_view_only.clicked.connect(self._choose_view_only)
        row.addWidget(self.btn_view_only)
        row.addStretch(1)
        self.btn_cancel = QPushButton(tr("취소"))
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        self.btn_open = QPushButton(tr("열기"))
        self.btn_open.setDefault(True)
        self.btn_open.clicked.connect(self._choose_open)
        row.addWidget(self.btn_open)
        lay.addLayout(row)
        self.resize(560, 520)
        self._update_state()

    # ------------------------------------------------------------------ 상태 받기
    def load_context(self, project_root: str, project_data: dict | None, *, project_title: str | None = None,
                     read_only: bool = False, read_only_reasons: list[str] | None = None) -> None:
        """작업대가 연 프로젝트의 상태를 받는다. 폴더 이름만 훑고(app.session.survey_project), 명부를 다시 읽는다.

        read_only 면 띠를 보인다. 세션은 그래도 고를 수 있다(store 가 읽기 전용으로 연다 — 분석 보기는 읽기만 한다).
        """
        self.project_root = project_root
        self.project_data = project_data
        self.read_only = bool(read_only)
        name = project_title or os.path.basename(str(project_root).rstrip("\\/")) or str(project_root)
        self.title.setText(tr("세션 고르기 — {0}").format(name))
        if self.read_only:
            why = "; ".join((read_only_reasons or [])[:2])
            self.ro_label.setText(tr("이 프로젝트는 읽기 전용으로 열렸습니다. 라벨을 저장할 수 없습니다.")
                                  + (f" ({why})" if why else ""))
        self.ro_label.setVisible(self.read_only)
        self.survey = sess.survey_project(project_root, project_data)
        try:
            self._this_m4 = sess.machine4(self._machine_id())
        except (OSError, ValueError):
            self._this_m4 = None
        self._reload_roster(select=self._default_labeler)

    def _machine_id(self) -> str:
        if self._mid_value is None:
            self._mid_value = sess.machine_id(self.data_root)
        return self._mid_value

    def _reload_roster(self, select: str | None = None) -> None:
        self.roster = sess.load_labelers(self.data_root)
        self.roster_msg.setText(self.roster.problem or "")
        self.roster_msg.setVisible(bool(self.roster.problem))
        self.labeler_box.blockSignals(True)
        self.labeler_box.clear()
        self.labeler_box.addItem(tr("라벨러를 고르세요"), None)
        for e in self.roster.entries:
            self.labeler_box.addItem(e.label, e.labeler_id)
        idx = self.labeler_box.findData(select) if select else -1
        self.labeler_box.setCurrentIndex(idx if idx > 0 else 0)
        self.labeler_box.blockSignals(False)
        if not self.roster.entries:
            self.btn_new_labeler.setChecked(True)
        self._refresh_choices()

    # ------------------------------------------------------------------ 라벨러
    def labeler_id(self) -> str | None:
        v = self.labeler_box.currentData()
        return v if isinstance(v, str) else None

    def select_labeler(self, labeler_id: str) -> bool:
        idx = self.labeler_box.findData(labeler_id)
        if idx <= 0:
            return False
        self.labeler_box.setCurrentIndex(idx)
        return True

    def _toggle_new_labeler(self, on: bool) -> None:
        self.new_box.setVisible(on)
        self.new_msg.setText("")
        if on:
            self.new_id.setFocus()

    def _add_labeler(self) -> None:
        lid = self.new_id.text().strip().upper()
        try:
            sess.check_labeler_id(lid)
        except ValueError:
            self.new_msg.setText(tr("라벨러 ID 는 영문 대문자로 시작하는 대문자·숫자 2~8자입니다."))
            return
        if self.roster.get(lid) is not None:
            self.new_msg.setText(tr("{0} 은(는) 이미 명부에 있습니다. 위에서 고르세요.").format(lid))
            self._reload_roster(select=lid)
            return
        try:
            sess.add_labeler(self.data_root, lid, self.new_name.text())
        except ValueError as e:
            self.new_msg.setText(str(e))
            return
        except OSError as e:
            LOG.warning("라벨러 명부를 저장하지 못했습니다: %s", type(e).__name__)
            self.new_msg.setText(tr("라벨러 명부를 저장하지 못했습니다 ({0}).").format(type(e).__name__))
            return
        LOG.info("라벨러 명부에 %s 를 더했습니다", lid)     # 표시 이름은 로그에 남기지 않는다
        self.new_id.clear()
        self.new_name.clear()
        self.btn_new_labeler.setChecked(False)
        self._reload_roster(select=lid)

    # ------------------------------------------------------------------ 고르기 칸
    def _clear_choices(self) -> None:
        for b in list(self.group.buttons()):
            self.group.removeButton(b)
        while self.choice_lay.count():
            it = self.choice_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._buttons = {}

    def _section(self, text: str) -> None:
        lb = QLabel(text)
        lb.setObjectName("panelTitle")
        self.choice_lay.addWidget(lb)

    def _hint(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("hint")
        lb.setWordWrap(True)
        lb.setContentsMargins(24, 0, 0, 0)
        self.choice_lay.addWidget(lb)
        return lb

    def _add_choice(self, key: str, text: str, enabled: bool, why: str = "") -> QRadioButton:
        rb = QRadioButton(text)
        rb.setEnabled(enabled)
        if why:
            rb.setToolTip(why)
        self.group.addButton(rb)
        self._buttons[key] = rb
        self.choice_lay.addWidget(rb)
        if why:
            self._hint(why)
        return rb

    def _refresh_choices(self) -> None:
        self._clear_choices()
        lid = self.labeler_id()
        sv = self.survey
        if sv is None:
            self._update_state()
            return
        if lid is None:
            self.existing, self.choices = [], []
            self._hint(tr("라벨러를 고르면 이어 할 세션과 새 세션 종류가 보입니다."))
            self._update_guide(None)
            self._update_state()
            return
        try:
            self.existing = sess.existing_sessions(self.project_root, lid, sv, self._this_m4)
        except (OSError, ValueError):
            self.existing = []
        self.choices = sess.kind_choices(sv, lid, self.existing, this_machine4=self._this_m4,
                                         now=self._now() if self._now else None)
        if self.existing:
            self._section(tr("이어 하기"))
            for e in self.existing:
                where = tr("이 PC") if e.on_this_pc else tr("다른 PC 에서 시작")
                gv = f"v{e.guide_version}" if e.guide_version else tr("기준서 ?")
                text = f"{e.session_id}  ·  {sess.KIND_LABELS[e.session_kind]}  ·  {gv}  ·  {where}"
                # 잠긴 세션도 고를 수 있다: store 가 읽기 전용으로 연다(풀지 않음, D-11 7). 보기·재라벨 계획 만들기용.
                # 다만 같은 사람 재라벨이 아직 잠기지 않은 1차 세션은 막는다(06 3-3·3-4, sess.reopen_refusal)
                block = sess.reopen_refusal(e, lid, sv, self.existing)
                why = tr(block) if block else (
                    tr("잠긴 세션 — 읽기 전용으로 엽니다 (더 쓸 수 없음 · 1차면 재라벨 계획을 만들 수 있음)")
                    if e.locked else "")
                self._add_choice(RESUME_PREFIX + e.session_id, text, block is None, why)
        self._section(tr("새 세션"))
        for c in self.choices:
            # 켜진 재라벨 선택의 메모(앞당긴 시작·같은 machine_id, D-11 6)도 옆에 보인다
            self._add_choice(NEW_PREFIX + c.kind, c.label, c.enabled, c.reason)
        if sv.roster_kinds and lid not in sv.roster_kinds:
            self._hint(tr("이 라벨러는 프로젝트 명부(project.json)에 없습니다. 열 수는 있고, 가져오기·보고서에서 경고로 표시됩니다."))
        self.choice_lay.addStretch(1)
        # 이어 할 수 있는 세션이 하나뿐이면 그것을 미리 고른다(새 세션을 미리 고르지는 않는다)
        resumable = [e for e in self.existing if e.resumable]
        if len(resumable) == 1:
            self._buttons[RESUME_PREFIX + resumable[0].session_id].setChecked(True)
        self._update_state()

    def choice_buttons(self) -> dict[str, QRadioButton]:
        """'resume:<session_id>' · 'new:<kind>' → 단추 (테스트·통합용)."""
        return dict(self._buttons)

    def choose(self, key: str) -> bool:
        """key = 'new:primary' · 'resume:<session_id>' · 또는 세션 종류 이름('primary'). 켜진 단추만 고를 수 있다."""
        if key in sess.KIND_CODES:
            key = NEW_PREFIX + key
        b = self._buttons.get(key)
        if b is None or not b.isEnabled():
            return False
        b.setChecked(True)
        return True

    def selected_key(self) -> str | None:
        for k, b in self._buttons.items():
            if b.isChecked() and b.isEnabled():
                return k
        return None

    def _selected_kind(self) -> str | None:
        k = self.selected_key()
        if k is None:
            return None
        if k.startswith(NEW_PREFIX):
            return k[len(NEW_PREFIX):]
        sid = k[len(RESUME_PREFIX):]
        return next((e.session_kind for e in self.existing if e.session_id == sid), None)

    def _update_guide(self, key: str | None) -> None:
        sv = self.survey
        if key and key.startswith(RESUME_PREFIX):
            e = next((x for x in self.existing if x.session_id == key[len(RESUME_PREFIX):]), None)
            if e is not None:
                self.guide_label.setText(tr("v{0} (이어 하는 세션의 버전)").format(e.guide_version)
                                         if e.guide_version else tr("이어 하는 세션의 버전 (첫 줄을 읽지 못함)"))
                return
        if sv is None or not sv.active_guide_version:
            self.guide_label.setText(tr("project.json 에 기준서가 없습니다"))
        else:
            sha = f"  ·  {tr('본문 해시')} {sv.guide_sha8}" if sv.guide_sha8 else ""
            self.guide_label.setText(f"v{sv.active_guide_version}{sha}")

    def _update_state(self) -> None:
        key = self.selected_key()
        self._update_guide(key)
        kind = self._selected_kind()
        lid = self.labeler_id()
        lines = sess.exposure_notes(self.survey, lid, kind) if (self.survey and lid and kind) else []
        self.notes.setText("\n".join(lines))
        self.notes.setVisible(bool(lines))
        self.reopen_box.setVisible(bool(key and key.startswith(NEW_PREFIX) and any(e.locked for e in self.existing)))
        self.btn_open.setEnabled(key is not None and lid is not None)

    def reopen_note(self) -> str | None:
        """새 세션 session.open 에 넣을 '잘못 잠근 사유'. 칸이 안 보이거나 비었으면 None."""
        if not self.reopen_box.isVisibleTo(self):
            return None
        try:
            return sess.clean_reopen_note(self.reopen_edit.text())
        except ValueError:
            return None

    # ------------------------------------------------------------------ 끝내기
    def _build_context(self) -> sess.SessionContext | None:
        key, lid = self.selected_key(), self.labeler_id()
        if key is None or lid is None:
            return None
        mid = self._machine_id()
        if key.startswith(RESUME_PREFIX):
            sid = key[len(RESUME_PREFIX):]
            e = next((x for x in self.existing if x.session_id == sid), None)
            if e is not None and self.survey is not None and sess.reopen_refusal(e, lid, self.survey, self.existing):
                return None
            return sess.resume_session(sid, machine_id_value=mid)
        kind = key[len(NEW_PREFIX):]
        extra: dict = {}
        plan_args: dict = {}
        if kind in ("relabel_same", "relabel_second"):
            # 재라벨 2종(D-11 6, 03 12-2): 고른 계획(자기 계획 하나만 연다) → scope=blocks·relabel_plan_id 를 session.open 에
            c = next((x for x in self.choices if x.kind == kind), None)
            if c is None or not c.enabled or not c.plan_id:
                return None
            plan = relabel_io.read_plan_for_session(self.project_root, kind, lid, c.plan_id)
            plan_args = relabel_io.session_args(plan)
            if c.early:
                reason = self.ask_early_reason(c)
                if not reason:
                    return None
                extra["relabel_too_early"] = reason       # 작업대가 store 를 연 뒤 warning(relabel_too_early) 로 남긴다
        taken = set(self.survey.session_ids if self.survey else ())
        note = self.reopen_note()
        if note:
            LOG.info("새 세션에 잘못 잠근 사유를 적습니다 (%d자)", len(note))   # 사유 글은 로그에 남기지 않는다
        for _ in range(20):
            r4 = self._rand4() if self._rand4 else sess.random4()
            ctx = sess.new_session(lid, kind, machine_id_value=mid,
                                   now=self._now() if self._now else None, rand4=r4, reopen_note=note, **plan_args)
            if ctx.session_id not in taken:
                return replace(ctx, extra=extra) if extra else ctx
        raise RuntimeError("겹치지 않는 세션 ID 를 만들지 못했습니다")

    def ask_early_reason(self, choice: sess.KindChoice) -> str | None:
        """같은 사람 재라벨을 3일 간격 전에 열 때 사유를 받는다(06 3-3). 시험에서는 early_reason 으로 바꿔 끼운다."""
        if self.early_reason is not None:
            return self.early_reason(choice)
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, tr("앞당긴 시작"),
                                        tr("원 세션 잠금 뒤 간격이 지나지 않았습니다 ({0}).\n사유를 적으면 기록하고 엽니다:")
                                        .format(choice.reason))
        return text.strip() if ok and text.strip() else None

    def _choose_open(self) -> None:
        try:
            ctx = self._build_context()
        except (OSError, ValueError, RuntimeError) as e:
            LOG.warning("세션을 만들지 못했습니다: %s", type(e).__name__)
            self.notes.setText(tr("세션을 만들지 못했습니다 ({0}).").format(type(e).__name__))
            self.notes.setVisible(True)
            return
        if ctx is None:
            return
        self._ctx = ctx
        self.view_only = False
        LOG.info("세션을 골랐습니다: %s", ctx.session_id)
        self.accept()

    def _choose_view_only(self) -> None:
        self._ctx = None
        self.view_only = True
        self.accept()

    def reject(self) -> None:          # 취소·Esc·창 닫기 → 세션 없음(보기 전용과 같은 결과, D-09 1)
        self._ctx = None
        self.view_only = False
        super().reject()

    def done(self, r: int) -> None:
        super().done(r)
        self.sessionChosen.emit(self._ctx)

    def context(self) -> sess.SessionContext | None:
        """고른 세션. 보기 전용·취소면 None."""
        return self._ctx


def choose_session(parent, project_root: str, project_data: dict | None, *, data_root: str,
                   project_title: str | None = None, read_only: bool = False,
                   read_only_reasons: list[str] | None = None, default_labeler: str | None = None,
                   machine_id_value: str | None = None) -> sess.SessionContext | None:
    """세션 선택 화면을 모달로 띄우고 고른 SessionContext(없으면 None)를 돌려준다."""
    dlg = SessionDialog(parent, data_root=data_root, machine_id_value=machine_id_value,
                        default_labeler=default_labeler)
    dlg.load_context(project_root, project_data, project_title=project_title, read_only=read_only,
                     read_only_reasons=read_only_reasons)
    dlg.exec()
    ctx = dlg.context()
    dlg.deleteLater()
    return ctx


__all__ = ["SessionDialog", "choose_session", "RESUME_PREFIX", "NEW_PREFIX"]
