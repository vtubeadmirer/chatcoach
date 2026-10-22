"""운영 대화상자: 프로젝트 백업 · 프로젝트 지우기(휴지통) (07 M4, 05 10-3 · S-39, 02 5-3).

판정·실행은 app/backup.py 가 한다. 여기 있는 것은 고르기와 확인 화면뿐이다. 메뉴 연결은 mainwindow.
화면에는 경로를 보여도 되지만(사용자 자신의 PC), 로그에는 경로를 쓰지 않는다(S-20).
"""
from __future__ import annotations

import os
import re

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout)

from ..i18n import tr

LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")

KIND_KO = {
    "project": "프로젝트 폴더 전체",
    "project_chat": "프로젝트의 채팅(chat\\)",
    "project_derived": "프로젝트의 파생 자료(derived\\)",
    "project_protected": "프로젝트의 보호 구역",
    "backups": "이 프로젝트의 백업",
    "cache": "채팅 색인 캐시",
    "media_cache": "영상 캐시(파형·썸네일)",
    "ai": "AI 결과",
    "reports": "보고서",
}


class BackupDialog(QDialog):
    """'파일 → 프로젝트 백업…'. Accepted 면 include_chat · include_protected · out_dir(None = 기본 위치)."""

    def __init__(self, parent=None, *, project_title: str = "", default_dir: str = "", start_dir: str = ""):
        """start_dir: [다른 폴더…] 대화상자가 처음 열릴 폴더(메인 창이 마지막으로 쓴 백업 폴더를 넘긴다)."""
        super().__init__(parent)
        self.setWindowTitle(tr("프로젝트 백업"))
        self.setMinimumWidth(460)
        self.out_dir: str | None = None
        self.default_dir = default_dir
        self.start_dir = start_dir
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("백업할 프로젝트: {0}").format(project_title or "-")))
        self.cb_chat = QCheckBox(tr("채팅 원문 포함 (chat\\messages.jsonl)"))
        self.cb_chat.setChecked(True)
        lay.addWidget(self.cb_chat)
        self.cb_protected = QCheckBox(tr("보호 구역 포함 (원 닉네임·식별자·원문 — 다른 사람에게 보내지 마세요)"))
        self.cb_protected.setChecked(False)
        lay.addWidget(self.cb_protected)
        row = QHBoxLayout()
        self.where = QLabel()
        self.where.setWordWrap(True)
        row.addWidget(self.where, 1)
        b_dir = QPushButton(tr("다른 폴더…"))
        b_dir.clicked.connect(self._choose_dir)
        row.addWidget(b_dir)
        b_def = QPushButton(tr("기본 위치"))
        b_def.clicked.connect(lambda: self.set_out_dir(None))
        row.addWidget(b_def)
        lay.addLayout(row)
        hint = QLabel(tr("기본 위치는 %LOCALAPPDATA%\\ChatCoach\\backups 입니다. 프로젝트·데이터·프로그램 폴더와 git 작업 "
                         "폴더 안은 고를 수 없습니다. OneDrive 는 경고하고, 보호 구역 포함이면 거부합니다."))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        box = QDialogButtonBox()
        ok = box.addButton(tr("백업 만들기"), QDialogButtonBox.AcceptRole)
        ok.setObjectName("backupBtn")
        box.addButton(tr("취소"), QDialogButtonBox.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.set_out_dir(None)

    @property
    def include_chat(self) -> bool:
        return self.cb_chat.isChecked()

    @property
    def include_protected(self) -> bool:
        return self.cb_protected.isChecked()

    def set_out_dir(self, d: str | None) -> None:
        self.out_dir = d or None
        self.where.setText(tr("저장 위치: {0}").format(self.out_dir or (self.default_dir or tr("기본 위치"))))

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("백업 저장 폴더"),
                                             self.out_dir or self.start_dir or self.default_dir or "")
        if d:
            self.set_out_dir(os.path.normpath(d))


class DisposalDialog(QDialog):
    """'파일 → 프로젝트 지우기(휴지통)…' 의 확인 화면. 휴지통으로 보낸다(영구 삭제 없음, 05 10-3).

    planner(keep_labels: bool) → backup.DisposalPlan 을 받아, 고르기가 바뀔 때마다 대상 목록을 다시 보인다(지우지 않음).
    Accepted 면 plan(마지막으로 보인 계획) · labeler_id · keep_labels."""

    def __init__(self, parent=None, *, planner, project_title: str = "", labeler_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("프로젝트 지우기 (휴지통)"))
        self.setMinimumWidth(560)
        self.planner = planner
        self.plan = None
        self.problem: str | None = None
        lay = QVBoxLayout(self)
        head = QLabel(tr("프로젝트 '{0}' 를 휴지통으로 보냅니다. 휴지통에서 되살릴 수 있고, 휴지통을 비워야 디스크에서 "
                         "지워집니다.").format(project_title or "-"))
        head.setWordWrap(True)
        lay.addWidget(head)
        self.rb_all = QRadioButton(tr("전부 지우기 (프로젝트 폴더·백업·캐시·AI 결과·보고서)"))
        self.rb_keep = QRadioButton(tr("라벨만 남기기 (채팅·파생 자료·보호 구역·백업·캐시를 지우고 라벨·표본·잠금은 남김)"))
        self.rb_all.setChecked(True)
        for rb in (self.rb_all, self.rb_keep):
            rb.toggled.connect(lambda on: on and self.replan())
            lay.addWidget(rb)
        self.targets = QPlainTextEdit()
        self.targets.setObjectName("disposalTargets")
        self.targets.setReadOnly(True)
        self.targets.setMinimumHeight(160)
        lay.addWidget(self.targets)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("라벨러 코드 (기록에 남김)")))
        self.ed_labeler = QLineEdit(labeler_id or "")
        self.ed_labeler.setMaxLength(8)
        self.ed_labeler.setPlaceholderText("L01")
        self.ed_labeler.textChanged.connect(self._sync)
        row.addWidget(self.ed_labeler)
        row.addStretch(1)
        lay.addLayout(row)
        self.cb_sure = QCheckBox(tr("대상 목록을 확인했습니다. 휴지통으로 보냅니다."))
        self.cb_sure.toggled.connect(self._sync)
        lay.addWidget(self.cb_sure)
        box = QDialogButtonBox()
        self.b_ok = box.addButton(tr("휴지통으로 보내기"), QDialogButtonBox.AcceptRole)
        self.b_ok.setObjectName("disposeBtn")
        box.addButton(tr("취소"), QDialogButtonBox.RejectRole)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.replan()

    @property
    def keep_labels(self) -> bool:
        return self.rb_keep.isChecked()

    @property
    def labeler_id(self) -> str:
        return self.ed_labeler.text().strip().upper()

    def replan(self) -> None:
        try:
            self.plan = self.planner(self.keep_labels)
            self.problem = None
        except Exception as e:  # noqa: BLE001 — 계획 실패는 사유를 보이고 실행을 막는다
            self.plan, self.problem = None, str(e) or type(e).__name__
        self.targets.setPlainText(self.describe())
        self._sync()

    def describe(self) -> str:
        if self.plan is None:
            return tr("지울 수 없습니다: {0}").format(self.problem or "")
        lines = [tr("대상 {0}개:").format(len(self.plan.targets))]
        for t in self.plan.targets:
            mark = tr(" (휴지통 불가 — 남김: {0})").format(t.trash_problem) if t.trash_problem else ""
            lines.append(f"· {tr(KIND_KO.get(t.kind, t.kind))}{mark}\n    {t.path}")
        if self.plan.warnings:
            lines.append("")
            lines += ["⚠ " + tr(w) for w in self.plan.warnings]
        return "\n".join(lines)

    def _sync(self, *_a) -> None:
        self.b_ok.setEnabled(self.plan is not None and bool(self.plan.targets) and self.cb_sure.isChecked()
                             and bool(LABELER_RE.match(self.labeler_id)))

    def _accept(self) -> None:
        if self.b_ok.isEnabled():
            self.accept()
