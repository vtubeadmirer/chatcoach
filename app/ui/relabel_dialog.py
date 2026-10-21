"""재라벨 계획 만들기 화면 (docs/design/06-evaluation-and-agreement.md 3장·5장·7-1, 03 6-14·12-2, 08 D-11 6).

메뉴 '세션 → 재라벨 계획 만들기…' 가 연다. 잠긴 **자기 1차 세션**에서만 열린다(작업대 can_make_relabel_plan).
고르는 것은 둘뿐이다.
- 종류: 같은 사람 재라벨(relabel_same, 원 세션 라벨러 = 나, 3일 간격) · 두 번째 사람 독립 라벨(relabel_second, 다른 라벨러 ID).
- 두 번째 사람의 라벨러 ID: 이 PC 명부(labelers.json)에서 고르거나 새로 적는다. 예시 값을 미리 채우지 않는다(S-37).
[계획 만들기] → Workbench.create_relabel_plan → relabel/<plan_id>.json (한 번만 만든다, 고치지 않음).

블라인드: 이 화면은 블록 수·시작 가능 시각 같은 숫자만 보인다. 블록 범위·τ_block·층은 보이지 않는다(06 3-2·3-4 —
재라벨러가 같은 PC 에서 이 화면을 볼 수 있다).
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QRadioButton, QVBoxLayout)

from .. import session as sess
from ..i18n import tr

LOG = logging.getLogger("chatcoach")


class RelabelPlanDialog(QDialog):
    """재라벨 계획 만들기. workbench: 잠긴 1차 세션을 연 Workbench. data_root: 라벨러 명부가 있는 data 폴더."""

    def __init__(self, parent=None, *, workbench, data_root: str):
        super().__init__(parent)
        self.setWindowTitle(tr("재라벨 계획 만들기"))
        self.setModal(True)
        self.wb = workbench
        self.data_root = data_root
        self.plan: dict | None = None
        me = workbench.labeler_id
        lay = QVBoxLayout(self)
        head = QLabel(tr("원 세션 {0} (잠김) 의 재라벨 계획을 만듭니다. 계획은 한 번 만들면 고치지 않습니다.")
                      .format(workbench.session.session_id if workbench.session else "-"))
        head.setWordWrap(True)
        lay.addWidget(head)
        grid = QGridLayout()
        self.group = QButtonGroup(self)
        self.rb_same = QRadioButton(tr("같은 사람 재라벨 ({0}, 잠금 3일 뒤부터)").format(me))
        self.rb_second = QRadioButton(tr("두 번째 사람 독립 라벨 (다른 라벨러 ID)"))
        for rb in (self.rb_same, self.rb_second):
            self.group.addButton(rb)
            rb.toggled.connect(lambda _on: self._refresh())
        grid.addWidget(self.rb_same, 0, 0, 1, 2)
        grid.addWidget(self.rb_second, 1, 0, 1, 2)
        grid.addWidget(QLabel(tr("두 번째 사람")), 2, 0)
        self.labeler_box = QComboBox()
        self.labeler_box.setEditable(True)            # 명부에 없으면 새 ID 를 적는다
        self.labeler_box.addItem("", None)
        for e in sess.load_labelers(data_root).entries:
            if e.labeler_id != me:
                self.labeler_box.addItem(e.labeler_id, e.labeler_id)
        self.labeler_box.lineEdit().setPlaceholderText(tr("영문 대문자로 시작, 대문자·숫자 2~8자"))
        self.labeler_box.currentTextChanged.connect(lambda _t: self._refresh())
        grid.addWidget(self.labeler_box, 2, 1)
        lay.addLayout(grid)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton(tr("닫기"))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_make = QPushButton(tr("계획 만들기"))
        self.btn_make.clicked.connect(self._on_make)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_make)
        lay.addLayout(row)
        self.rb_second.setChecked(True)
        self._refresh()

    def chosen_kind(self) -> str:
        return "relabel_same" if self.rb_same.isChecked() else "relabel_second"

    def chosen_labeler(self) -> str | None:
        if self.chosen_kind() == "relabel_same":
            return self.wb.labeler_id
        t = self.labeler_box.currentText().strip().upper()
        return t or None

    def _refresh(self) -> None:
        second = self.chosen_kind() == "relabel_second"
        self.labeler_box.setEnabled(second and self.plan is None)
        lid = self.chosen_labeler()
        ok = self.plan is None and bool(lid) and bool(sess.LABELER_RE.match(lid or ""))
        if second and lid == self.wb.labeler_id:
            ok = False
        self.btn_make.setEnabled(ok)

    def make(self, session_kind: str, labeler_id: str) -> dict | None:
        """계획을 만든다(시험·통합용 직접 호출). 성공하면 계획 dict, 실패하면 None 과 화면 문장."""
        try:
            sess.check_labeler_id(labeler_id)
            plan = self.wb.create_relabel_plan(session_kind, labeler_id)
        except (ValueError, OSError) as e:
            LOG.warning("재라벨 계획 만들기 실패: %s", type(e).__name__)
            self.msg.setText(tr("만들지 못했습니다: {0}").format(str(e)[:300]))
            return None
        self.plan = plan
        start = plan.get("earliest_start_at")
        self.msg.setText(tr("계획 {0} 를 만들었습니다 — 블록 {1}개, 라벨러 {2}{3}").format(
            plan["plan_id"], len(plan.get("selected") or []), plan["labeler_id"],
            tr(", 시작 가능 {0}").format(start) if start else tr(", 바로 시작할 수 있음")))
        self._refresh()
        return plan

    def _on_make(self) -> None:
        lid = self.chosen_labeler()
        if lid:
            self.make(self.chosen_kind(), lid)


__all__ = ["RelabelPlanDialog"]
