# vendored from vtubeadmirer/clipslicer@07d13cc (app/dialogs.py UpdateDialog) — 변경: 노트는 서명된 notes_ko 일반 텍스트만
#   (링크 자동 열기 없음), 릴리스 페이지 주소는 앱이 만든다(open_trusted_url), mandatory 면 건너뛰기 숨김, 라벨 형식 경고 (S-18).
"""새 버전 대화상자 (04 6-3)."""
from __future__ import annotations

import logging
import os
import re

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from ..appspec import SPEC, AppSpec
from ..i18n import tr
from ..updater import Release

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
