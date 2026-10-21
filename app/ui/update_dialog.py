# vendored from vtubeadmirer/clipslicer@07d13cc (app/dialogs.py UpdateDialog) — 변경: 노트는 서명된 notes_ko 일반 텍스트만
#   (링크 자동 열기 없음), 릴리스 페이지 주소는 앱이 만든다(open_trusted_url), mandatory 면 건너뛰기 숨김, 라벨 형식 경고 (S-18).
#   M4: 필수 업데이트 문구, '업데이트 필요' 모드의 수동 업데이트 안내 버튼과 상단 고정 안내(SupportBanner),
#   되돌리기 확인 대화상자(RollbackDialog)와 메뉴 문구(rollback_menu_text).
"""새 버전 대화상자 (04 6-3), '업데이트 필요' 안내 (04 5-5), 되돌리기 확인 (04 6-10).

메뉴·상태바 연결은 mainwindow(통합 담당)가 한다. 여기 있는 것은 모양과 문구뿐이고 판정은 app/updater.py 가 한다.
"""
from __future__ import annotations

import logging
import os
import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from ..appspec import SPEC, AppSpec
from ..i18n import tr
from ..updater import Release, RollbackStatus, SupportStatus

LOG = logging.getLogger("chatcoach")
_TRUSTED_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/(tag/v\d+\.\d+\.\d+|latest)$")


def open_trusted_url(url: str) -> bool:
    """앱이 만든 주소만 연다. 외부 문자열을 os.startfile 에 넘기지 않는다 (S-18)."""
    if not _TRUSTED_URL.match(url):
        LOG.warning("신뢰하지 않는 주소는 열지 않음: %r", url)
        return False
    try:
        os.startfile(url)  # noqa: S606 — 검사한 https 주소만
        return True
    except OSError:
        return False


class UpdateDialog(QDialog):
    """결과: Accepted = 지금 업데이트, Rejected = 나중에. skip 속성이 True 면 '이 버전 건너뛰기'."""

    def __init__(self, release: Release, needs_update: bool = False, spec: AppSpec = SPEC, parent=None):
        super().__init__(parent)
        self.release = release
        self.spec = spec
        self.skip = False
        m = release.manifest
        self.setWindowTitle(tr("새 버전 {0}").format(m.version))
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        kind = tr("경량") if release.chosen == "lite" else tr("전체")
        head = tr("ChatCoach {0} 을(를) 받을 수 있습니다. ({1} {2:.0f} MB, 서명 키 {3})").format(
            m.version, kind, release.download_size / 1e6, release.signed_by)
        lay.addWidget(QLabel(head))
        if needs_update:
            w = QLabel(tr("이 버전은 더 이상 지원되지 않습니다. 업데이트해야 새 라벨을 저장할 수 있습니다."))
            w.setObjectName("hint")
            w.setWordWrap(True)
            lay.addWidget(w)
        if m.mandatory:
            w = QLabel(tr("필수 업데이트입니다. 건너뛸 수 없고, 시작할 때마다 알립니다."))
            w.setObjectName("mandatoryHint")
            w.setWordWrap(True)
            lay.addWidget(w)
        for warn in release.warnings:
            w = QLabel("⚠ " + warn)
            w.setWordWrap(True)
            lay.addWidget(w)
        notes = QPlainTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(m.notes_ko or tr("(릴리스 노트 없음)"))
        notes.setMinimumHeight(160)
        lay.addWidget(notes)
        row = QHBoxLayout()
        b_page = QPushButton(tr("릴리스 페이지 열기"))
        b_page.clicked.connect(lambda: open_trusted_url(self.spec.release_page_url(m.version)))
        row.addWidget(b_page)
        if needs_update:
            # 자동 교체가 막힌 PC 도 빠져나갈 수 있게 '업데이트 필요' 에서는 수동 안내를 반드시 함께 보인다 (5-5)
            b_manual = QPushButton(tr("수동 업데이트 안내"))
            b_manual.setObjectName("manualBtn")
            b_manual.clicked.connect(lambda: open_trusted_url(manual_update_url(self.spec)))
            row.addWidget(b_manual)
        row.addStretch(1)
        if not m.mandatory:
            b_skip = QPushButton(tr("이 버전 건너뛰기"))
            b_skip.clicked.connect(self._skip)
            row.addWidget(b_skip)
        lay.addLayout(row)
        box = QDialogButtonBox()
        b_now = box.addButton(tr("지금 업데이트"), QDialogButtonBox.AcceptRole)
        b_now.setObjectName("updateBtn")
        box.addButton(tr("나중에"), QDialogButtonBox.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _skip(self):
        self.skip = True
        self.reject()


def manual_update_url(spec: AppSpec = SPEC) -> str:
    """수동 업데이트 안내(04 8장)의 시작점: 최신 릴리스 페이지. 앱이 만든 주소라 open_trusted_url 을 통과한다."""
    return f"{spec.github_base}{spec.repo}/releases/latest"


def rollback_menu_text(status: RollbackStatus) -> tuple[str, bool, str]:
    """메뉴 항목의 (문구, 켜짐, 툴팁). 되돌릴 수 있는 동안만 켠다 (_backup 은 첫 정상 종료 다음 시작에 지워진다)."""
    if status.available and status.target_version:
        return (tr("이전 버전으로 되돌리기 ({0})").format(status.target_version), True,
                tr("{0} → {1}. 라벨·설정은 그대로입니다.").format(status.current_version, status.target_version))
    return tr("이전 버전으로 되돌리기"), False, status.reason


class RollbackDialog(QDialog):
    """되돌리기 확인. reason 이 있으면 '실패 뒤 제안' 으로 쓴다 (04 6-9 · 6-10). Accepted = 되돌리기."""

    def __init__(self, status: RollbackStatus, reason: str | None = None, parent=None, data_backup: bool = True,
                 note: str | None = None):
        """note: 덧붙일 경고 1줄 (예: 되돌릴 버전이 min_supported 보다 낮아 되돌린 뒤 바로 '업데이트 필요' 가 됨)."""
        super().__init__(parent)
        self.status = status
        target, current = status.target_version or "?", status.current_version
        repair = target == current
        self.setWindowTitle(tr("이전 버전 사본으로 복구") if repair else tr("이전 버전으로 되돌리기"))
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        if reason:
            w = QLabel(reason)
            w.setObjectName("suggestReason")
            w.setWordWrap(True)
            lay.addWidget(w)
        if repair:
            head = tr("프로그램 파일을 {0} 사본으로 다시 맞춥니다.").format(target)
        else:
            head = tr("ChatCoach 를 {0} 에서 {1} 으로 되돌립니다.").format(current, target)
        lay.addWidget(QLabel(head))
        lines = [
            tr("· 라벨·설정·프로젝트(data\\)는 바꾸지 않습니다.") +
            (tr(" 되돌리기 직전에 data 백업을 한 번 더 만듭니다.") if data_backup else ""),
            tr("· 앱이 닫히고 1~3분 뒤 다시 열립니다. 그동안 이 폴더의 프로그램을 실행하지 마세요."),
            tr("· 되돌리기 전에 사본이 이 PC 에서 실행되는지 먼저 점검합니다 (최대 90초)."),
        ]
        if not repair:
            lines.append(tr("· {0} 은 다시 권하지 않습니다. 더 높은 버전이 나오면 다시 알립니다.").format(current))
            lines.append(tr("· {0} 에서 새 형식으로 저장한 줄은 {1} 에서 읽기 전용으로 보일 수 있습니다.").format(current, target))
        body = QLabel("\n".join(lines))
        body.setWordWrap(True)
        lay.addWidget(body)
        if note:
            w = QLabel("⚠ " + note)
            w.setObjectName("rollbackNote")
            w.setWordWrap(True)
            lay.addWidget(w)
        box = QDialogButtonBox()
        b_ok = box.addButton(tr("복구") if repair else tr("되돌리기"), QDialogButtonBox.AcceptRole)
        b_ok.setObjectName("rollbackBtn")
        b_ok.setEnabled(status.available)
        box.addButton(tr("나중에") if reason else tr("취소"), QDialogButtonBox.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)


class SupportBanner(QFrame):
    """'업데이트 필요' 모드·필수 업데이트의 상단 고정 안내 (04 5-5). 닫기 버튼이 없다.
    신호: update_requested([업데이트]), manual_requested([수동 업데이트 안내] — 주소는 이 위젯이 연다)."""
    update_requested = Signal()
    manual_requested = Signal()

    def __init__(self, spec: AppSpec = SPEC, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.setObjectName("supportBanner")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        self.label = QLabel()
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.b_update = QPushButton(tr("업데이트"))
        self.b_update.clicked.connect(self.update_requested)
        row.addWidget(self.b_update)
        self.b_manual = QPushButton(tr("수동 업데이트 안내"))
        self.b_manual.clicked.connect(self._manual)
        row.addWidget(self.b_manual)
        self.set_status(None)

    def _manual(self):
        self.manual_requested.emit()
        open_trusted_url(manual_update_url(self.spec))

    def set_status(self, status: SupportStatus | None) -> None:
        """status 가 없거나 강제할 것이 없으면 숨긴다. 지원 종료면 수동 안내 버튼을 반드시 보인다."""
        if status is None or not status.required_pending:
            self.setVisible(False)
            return
        self.label.setText(tr(status.message()))
        self.b_manual.setVisible(status.needs_update)
        self.setVisible(True)
