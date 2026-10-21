"""일치도 보고서 화면 (docs/design/06-evaluation-and-agreement.md 4장·5장·7-1·7-3, 03 6-14 '보고서', 08 D-11 6·8).

메뉴 '보고 → 일치도 보고서…' 가 연다. 잠긴 두 세션(1차·같은 사람 재라벨·두 번째 사람)을 고르고 출력 폴더를 정하면
app.agreement_report.make_report 가 report.json · report.md 를 프로젝트 **밖**에 쓴다.

- 고를 수 있는 세션은 locks/<session_id>.json 이 있는 것만이다(이름만 본다). 잠기지 않은 세션은 목록에 없다 —
  계산은 두 세션이 모두 잠긴 뒤에만 한다(06 7-1). make_report 도 다시 확인한다(NotLockedError, 라벨 파일을 열기 전).
- 화면에는 판정 표(지표·값·판정)와 '같은 machine_id' 같은 머리 표시, 쓴 폴더만 보인다. 채팅 본문·닉네임·근거는 없다.
- 메인 창은 보기 전용(세션 없음)과 조정 세션에서만 이 메뉴를 켠다. 잠긴 세션을 다시 연 1차·재라벨 세션, 분석 세션에서는
  끈다(03 12-1: 보고서 쓰기는 조정 세션만, 06 7-3). 계획된 재라벨이 아직 잠기지 않았으면 그것도 끈다(06 7-1).
  make_report 는 reader_kind 와 06 7-1 조건(comparison_gate)을 라벨 파일을 열기 전에 다시 본다.
"""
from __future__ import annotations

import logging
import os
import re

from PySide6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from .. import agreement_report as ar
from ..core import visibility as vis
from ..i18n import tr

LOG = logging.getLogger("chatcoach")
_LOCK_RE = re.compile(r"\A(.+)\.json\Z")


def locked_comparable_sessions(project_root: str) -> list[str]:
    """locks/ 의 **파일 이름**에서 비교할 수 있는 잠긴 세션 ID (1차·재라벨 2종). 내용은 열지 않는다."""
    from ..core.ids import SESSION_ID_RE
    try:
        names = sorted(os.listdir(os.path.join(project_root, "locks")))
    except OSError:
        return []
    out = []
    for n in names:
        m = _LOCK_RE.match(n)
        sid = m.group(1) if m else None
        if sid and SESSION_ID_RE.match(sid) and vis.session_kind_of_id(sid) in ar.COMPARABLE_KINDS:
            out.append(sid)
    return out


def summary_lines(report: dict) -> list[str]:
    """보고서 → 화면 줄(숫자·판정만)."""
    h = report.get("header") or {}
    lines = [tr("보고서 {0} · {1}").format(report.get("report_id"),
                                         tr("1인 재라벨 (intra)") if h.get("comparison") == "intra" else tr("2인 (inter)"))]
    if h.get("same_machine"):
        lines.append(tr("같은 machine_id — 기능 시험용, 실제 일치도로 쓰지 않음 (08 D-11 6)"))
    if not h.get("representative", True):
        lines.append(tr("노출 기록이 있어 대표값으로 쓰지 않음"))
    for g in report.get("gates") or []:
        v = g.get("value")
        vs = "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
        lines.append(f"{g.get('label_kind')} · {g.get('metric')} = {vs} (n={g.get('n')}) → {g.get('verdict_ko')}")
    for w in (report.get("warnings") or [])[:5]:
        lines.append("⚠ " + w)
    return lines


class AgreementDialog(QDialog):
    """잠긴 두 세션의 일치도 보고서를 만든다. project_root: 연 프로젝트 폴더."""

    def __init__(self, parent=None, *, project_root: str, created_by: str | None = None,
                 reader_kind: str | None = None):
        super().__init__(parent)
        self.reader_kind = reader_kind          # 지금 열린 세션 종류(None = 보기 전용). build_report 가 다시 본다
        self.setWindowTitle(tr("일치도 보고서"))
        self.setModal(True)
        self.project_root = project_root
        self.created_by = created_by
        self.result: ar.ReportResult | None = None
        self.sessions = locked_comparable_sessions(project_root)
        lay = QVBoxLayout(self)
        info = QLabel(tr("잠긴 두 세션을 고르세요. 잠기지 않은 세션은 목록에 없습니다 (06 7-1)."))
        info.setWordWrap(True)
        lay.addWidget(info)
        grid = QGridLayout()
        self.box_a, self.box_b = QComboBox(), QComboBox()
        for box in (self.box_a, self.box_b):
            for s in self.sessions:
                box.addItem(s, s)
        if len(self.sessions) >= 2:
            self.box_b.setCurrentIndex(1)
        grid.addWidget(QLabel(tr("세션 A")), 0, 0)
        grid.addWidget(self.box_a, 0, 1)
        grid.addWidget(QLabel(tr("세션 B")), 1, 0)
        grid.addWidget(self.box_b, 1, 1)
        grid.addWidget(QLabel(tr("출력 폴더")), 2, 0)
        self.out_edit = QLineEdit(ar.default_out_root(project_root))
        grid.addWidget(self.out_edit, 2, 1)
        lay.addLayout(grid)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_close = QPushButton(tr("닫기"))
        self.btn_close.clicked.connect(self.reject)
        self.btn_make = QPushButton(tr("보고서 만들기"))
        self.btn_make.clicked.connect(self._on_make)
        row.addWidget(self.btn_close)
        row.addWidget(self.btn_make)
        lay.addLayout(row)
        if len(self.sessions) < 2:
            self.msg.setText(tr("잠긴 세션이 {0}개입니다. 두 세션이 모두 잠겨야 보고서를 만듭니다.").format(len(self.sessions)))
        self.resize(620, 360)
        for box in (self.box_a, self.box_b):
            box.currentIndexChanged.connect(lambda _i: self._refresh())
        self._refresh()

    def _refresh(self) -> None:
        a, b = self.box_a.currentData(), self.box_b.currentData()
        self.btn_make.setEnabled(bool(a) and bool(b) and a != b)

    def run(self, session_a: str, session_b: str, out_root: str | None = None) -> ar.ReportResult | None:
        """보고서를 만든다(시험·통합용 직접 호출). 성공하면 ReportResult, 실패하면 None 과 화면 문장."""
        out = out_root or self.out_edit.text().strip() or None
        try:
            res = ar.make_report(self.project_root, session_a, session_b, out_root=out, created_by=self.created_by,
                                 reader_kind=self.reader_kind)
        except ar.ReportError as e:
            LOG.warning("일치도 보고서 실패: %s", type(e).__name__)
            self.msg.setText(tr("보고서를 만들지 못했습니다: {0}").format(str(e)[:300]))
            return None
        except (OSError, ValueError) as e:
            LOG.warning("일치도 보고서 실패: %s", type(e).__name__)
            self.msg.setText(tr("보고서를 만들지 못했습니다 ({0})").format(type(e).__name__))
            return None
        self.result = res
        self.msg.setText("\n".join(summary_lines(res.report) + [tr("쓴 폴더: {0}").format(res.out_dir)]))
        LOG.info("일치도 보고서 %s 를 썼습니다", res.report.get("report_id"))
        return res

    def _on_make(self) -> None:
        a, b = self.box_a.currentData(), self.box_b.currentData()
        if a and b and a != b:
            self.run(a, b)


__all__ = ["AgreementDialog", "locked_comparable_sessions", "summary_lines"]
