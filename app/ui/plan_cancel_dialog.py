"""재라벨 계획 취소 화면 — 메뉴 '세션 → 재라벨 계획 취소…' (06 7-1, 03 6-14, 08 D-12 7·D-12 3).

막힌 비교(잠기지 않은 계획된 재라벨, 읽지 못한 계획)를 풀려고 검토자가 사유를 적고 계획을 취소한다.
- 조정 세션(검토자)에서만 연다. 1인 작업이면 본인이 검토자를 겸한다(D-12 3) — '취소한 사람 = 라벨러' 를 결과에 보인다.
- 계획 파일은 지우지도 고치지도 않고 relabel/<plan_id>.cancel.json 을 한 번만 만든다(app.relabel.cancel_plan).
  되돌리기는 없다. 그래서 '되돌릴 수 없음' 확인 칸을 체크해야 [취소] 가 된다.
- 목록에는 계획 ID·종류·재라벨러·원 세션만 보인다(조정 세션은 계획을 읽을 수 있다, 03 12-1). 블록·시각은 보이지 않는다.
- 사유 글은 취소 기록에만 들어가고 로그에는 남기지 않는다(ID 만).
- 계획 취소 길은 이 대화상자 하나다. 조정 화면 아래의 취소 칸은 1366x768 에서 오른쪽 밖으로 나가 지웠다(14 4-2 의 1).
  취소한 뒤 통합(mainwindow)이 조정 화면의 refresh_plans() 를 불러 도구 칸을 다시 채운다.
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout)

from .. import relabel as relabel_io
from ..i18n import tr

LOG = logging.getLogger("chatcoach")


def plan_choices(project_root: str) -> list[tuple[str, str]]:
    """취소할 수 있는 계획 [(plan_id, 화면 문구)]: 취소 기록이 없는 것(읽지 못한 계획 포함)."""
    out = []
    try:
        plans = relabel_io.list_plans(project_root, include_cancelled=True)
    except OSError:
        return out
    for p in plans:
        if p.status == relabel_io.STATUS_CANCELLED:
            continue
        if p.problem:
            who = tr("읽지 못한 계획")
        else:
            who = f"{p.session_kind} · {p.labeler_id} · {p.source_session_id}"
        out.append((p.plan_id, f"{p.plan_id}  ({who})"))
    return out


class PlanCancelDialog(QDialog):
    """store: 조정 세션의 ProjectStore(root·session 을 쓴다). doc: 쓴 취소 기록(없으면 None)."""

    def __init__(self, parent=None, *, store, app_version: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("재라벨 계획 취소"))
        self.setModal(True)
        self.store = store
        self.app_version = app_version
        self.doc: dict | None = None
        lay = QVBoxLayout(self)
        info = QLabel(tr("계획을 취소하면 그 계획의 재라벨 세션을 기다리지 않고 보고서를 만들 수 있습니다. "
                         "계획 파일은 그대로 두고 취소 기록만 남깁니다. 되돌릴 수 없습니다 (06 7-1, D-12 7)."))
        info.setWordWrap(True)
        lay.addWidget(info)
        grid = QGridLayout()
        grid.addWidget(QLabel(tr("계획")), 0, 0)
        self.plan_box = QComboBox()
        for pid, text in plan_choices(store.root):
            self.plan_box.addItem(text, pid)
        grid.addWidget(self.plan_box, 0, 1)
        grid.addWidget(QLabel(tr("사유 (필수)")), 1, 0)
        self.reason = QLineEdit()
        self.reason.setMaxLength(relabel_io.REASON_MAX)
        self.reason.setPlaceholderText(tr("예: 두 번째 사람이 없어 이 계획은 쓰지 않음"))
        grid.addWidget(self.reason, 1, 1)
        lay.addLayout(grid)
        self.confirm = QCheckBox(tr("되돌릴 수 없음을 확인했습니다"))
        lay.addWidget(self.confirm)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b_close = QPushButton(tr("닫기"))
        b_close.clicked.connect(self.reject)
        self.btn_cancel_plan = QPushButton(tr("계획 취소"))
        self.btn_cancel_plan.clicked.connect(lambda: self.cancel_plan())
        row.addWidget(b_close)
        row.addWidget(self.btn_cancel_plan)
        lay.addLayout(row)
        if self.plan_box.count() == 0:
            self.msg.setText(tr("취소할 수 있는 재라벨 계획이 없습니다."))
            self.btn_cancel_plan.setEnabled(False)
        if getattr(store, "read_only", False):
            self.msg.setText(tr("조정 세션이 읽기 전용이라 취소할 수 없습니다."))
            self.btn_cancel_plan.setEnabled(False)
        self.resize(640, 240)

    def cancel_plan(self, plan_id: str | None = None, reason: str | None = None) -> dict | None:
        """고른 계획(또는 plan_id)을 사유(또는 칸의 글)로 취소한다. 쓴 취소 기록, 막히면 None 과 화면 문장."""
        pid = plan_id or self.plan_box.currentData()
        why = self.reason.text() if reason is None else reason
        if pid is None:
            self.msg.setText(tr("계획을 고르세요."))
            return None
        if not self.confirm.isChecked():
            self.msg.setText(tr("'되돌릴 수 없음을 확인했습니다' 를 체크하세요."))
            return None
        if getattr(self.store, "read_only", False):
            self.msg.setText(tr("조정 세션이 읽기 전용이라 취소할 수 없습니다."))
            return None
        s = self.store.session
        try:
            doc = relabel_io.cancel_plan(self.store.root, pid, reason=why, cancelled_by=s.labeler_id,
                                         cancelled_machine4=s.machine4, app_version=self.app_version,
                                         canceller_kind=s.session_kind)
        except (OSError, ValueError) as e:            # RelabelBlindError 는 PermissionError(OSError)
            self.msg.setText(tr("취소하지 못했습니다: {0}").format(str(e)[:300]))
            LOG.info("계획 취소 거부: %s", type(e).__name__)
            return None
        self.doc = doc
        LOG.info("재라벨 계획 취소: %s", pid)
        self.msg.setText(tr("계획 {0} 를 취소했습니다.").format(pid)
                         + (tr(" (취소한 사람 = 라벨러, D-12 3)") if doc.get("canceller_is_labeler") else ""))
        self.btn_cancel_plan.setEnabled(False)
        return doc


__all__ = ["PlanCancelDialog", "plan_choices"]
