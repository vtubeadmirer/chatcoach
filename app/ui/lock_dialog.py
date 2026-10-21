"""세션 잠그기 화면 (docs/design/06-evaluation-and-agreement.md 7-1, 03 6-14·10-4, 08 D-11 7).

두 단계다.
1. 요약: 세션 ID·종류, 구간 수(저스트채팅 수), 반응 수, 라벨 종류별 수, 표본 진행률(매니페스트마다 판정/표본),
   미완료 경고(구간·반응 완료 표시 없음, 표본 안 뽑음·미판정, 충돌, 다른 PC 파일 등). 잠글 수 없으면 까닭을 보이고
   [다음] 을 끈다.
2. 확인: '잠근 세션은 풀지 않는다 — 고치려면 새 세션' 안내와 확인 칸. 칸을 체크해야 [잠그기] 가 켜진다.
   미완료 경고가 있으면 확인 칸 문구에 '미완료인 채로' 를 넣는다.

잠금 파일은 store 를 거쳐서만 쓴다: [잠그기] → store.lock_session(). 성공하면 sessionLocked(잠금 내용) 을 보내고
accept() 로 닫는다. 실패하면 화면에 사유를 보이고 열어 둔다(잠금 파일은 생기지 않는다).

블라인드: 이 화면은 자기 세션의 숫자만 보인다(store.lock_summary). 라벨 본문·닉네임·다른 세션 정보는 없다.
작업대 연결은 통합 쪽이 한다: LockDialog(parent, store=…).exec() 뒤 store.read_only 로 작업대를 보기 전용으로 바꾼다.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import locking
from .. import session as sess
from ..i18n import tr

LOG = logging.getLogger("chatcoach")

KIND_TEXT = {"segment": "구간", "utterance": "발화", "reaction": "반응", "continuation": "이어짐",
             "video_check": "영상 확인", "missed_candidate": "놓친 후보", "origin": "출처", "adjudication": "조정"}


def summary_rows(s: locking.LockSummary) -> list[tuple[str, str]]:
    """요약 표의 (항목, 값) 줄. 숫자만이다."""
    rows = [
        (tr("세션"), f"{s.session_id} · {sess.KIND_LABELS.get(s.session_kind, s.session_kind)}"),
        (tr("구간"), f"{s.segments}개 (저스트채팅 {s.jc_segments}개)"),
        (tr("반응"), f"{s.reactions}건"),
    ]
    other = [f"{KIND_TEXT[k]} {n}" for k, n in s.label_counts.items() if n and k not in ("segment", "reaction")]
    if other:
        rows.append((tr("그 밖의 라벨"), " · ".join(other)))
    if s.samples:
        pct = (100.0 * s.samples_judged / s.samples_total) if s.samples_total else 100.0
        rows.append((tr("표본 진행"), f"{s.samples_judged} / {s.samples_total} ({pct:.0f}%)"))
        for p in s.samples:
            rows.append(("  " + p.sample_set_id, f"{p.done} / {p.total}"))
    else:
        rows.append((tr("표본 진행"), tr("표본 매니페스트 없음")))
    rows.append((tr("기록 파일"), f"{s.files}개"))
    sc = s.status_counts
    rows.append((tr("판 상태"), f"유효 {sc.get('active', 0)} · 철회 {sc.get('retracted', 0)} · 충돌 {sc.get('conflict', 0)}"))
    return rows


def confirm_text(s: locking.LockSummary) -> str:
    """2단계 확인 칸 문구."""
    if s.warnings:
        return tr("미완료 항목이 있는 채로 잠급니다. 잠근 세션은 풀 수 없고, 고치려면 새 세션을 열어야 한다는 것을 압니다.")
    return tr("잠근 세션은 풀 수 없고, 고치려면 새 세션을 열어야 한다는 것을 압니다.")


class LockDialog(QDialog):
    """세션 잠그기 2단계 대화상자. store: app.store.ProjectStore (이 세션으로 쓰기 가능하게 연 것)."""

    sessionLocked = Signal(object)      # 잠금 내용(dict)

    def __init__(self, parent=None, *, store, sample_verify: list[dict] | None = None,
                 current_sample: list[str] | None = None):
        """current_sample: 재라벨 세션의 자기 표본 chat_id 목록(14 4-2 의 5). 주면 store.lock_session 에 그대로 넘긴다."""
        super().__init__(parent)
        self.setWindowTitle(tr("세션 잠그기"))
        self.setModal(True)
        self.store = store
        self._sample_verify = sample_verify
        self._current_sample = current_sample
        self.lock_doc: dict | None = None
        self.summary = store.lock_summary()

        self.pages = QStackedWidget(self)
        self.pages.addWidget(self._build_summary_page())
        self.pages.addWidget(self._build_confirm_page())
        lay = QVBoxLayout(self)
        lay.addWidget(self.pages)
        self.error_label = QLabel("", self)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c0392b;")
        self.error_label.hide()
        lay.addWidget(self.error_label)
        self._refresh()

    # ------------------------------------------------------------------ 1단계
    def _build_summary_page(self) -> QWidget:
        w = QWidget(self)
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("잠그기 전에 이 세션의 내용을 확인하세요."), w))
        grid = QGridLayout()
        for i, (k, val) in enumerate(summary_rows(self.summary)):
            grid.addWidget(QLabel(k, w), i, 0)
            lab = QLabel(val, w)
            lab.setObjectName(f"row_{i}")
            grid.addWidget(lab, i, 1)
        v.addLayout(grid)
        self.warning_labels: list[QLabel] = []
        for t in self.summary.warning_texts():
            lab = QLabel("⚠ " + t, w)
            lab.setWordWrap(True)
            lab.setStyleSheet("color: #b9770e;")
            v.addWidget(lab)
            self.warning_labels.append(lab)
        self.blocker_labels: list[QLabel] = []
        for t in self.summary.blockers:
            lab = QLabel(tr("잠글 수 없습니다: ") + t, w)
            lab.setWordWrap(True)
            lab.setStyleSheet("color: #c0392b;")
            v.addWidget(lab)
            self.blocker_labels.append(lab)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton(tr("취소"), w)
        self.cancel_btn.clicked.connect(self.reject)
        self.next_btn = QPushButton(tr("다음 →"), w)
        self.next_btn.clicked.connect(self.go_confirm)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.next_btn)
        v.addLayout(row)
        return w

    # ------------------------------------------------------------------ 2단계
    def _build_confirm_page(self) -> QWidget:
        w = QWidget(self)
        v = QVBoxLayout(w)
        head = QLabel(tr("세션 {sid} 를 잠급니다.").format(sid=self.summary.session_id), w)
        head.setStyleSheet("font-weight: bold;")
        v.addWidget(head)
        info = QLabel(tr("잠근 세션에는 더 쓸 수 없고, 다시 열면 읽기 전용입니다. 잠금은 풀지 않습니다 — "
                         "고칠 것이 있으면 새 세션을 열고, 잘못 잠갔으면 다음 세션의 사유 칸에 적으세요."), w)
        info.setWordWrap(True)
        v.addWidget(info)
        self.confirm_box = QCheckBox(confirm_text(self.summary), w)
        self.confirm_box.toggled.connect(self._refresh)
        v.addWidget(self.confirm_box)
        row = QHBoxLayout()
        row.addStretch(1)
        self.back_btn = QPushButton(tr("← 뒤로"), w)
        self.back_btn.clicked.connect(self.go_summary)
        self.lock_btn = QPushButton(tr("잠그기"), w)
        self.lock_btn.clicked.connect(self.do_lock)
        row.addWidget(self.back_btn)
        row.addWidget(self.lock_btn)
        v.addLayout(row)
        return w

    # ------------------------------------------------------------------ 동작
    @property
    def step(self) -> int:
        return self.pages.currentIndex() + 1

    def _refresh(self, *_):
        self.next_btn.setEnabled(self.summary.can_lock)
        self.lock_btn.setEnabled(self.summary.can_lock and self.confirm_box.isChecked() and self.lock_doc is None)

    def go_confirm(self) -> None:
        if not self.summary.can_lock:
            return
        self.pages.setCurrentIndex(1)
        self._refresh()

    def go_summary(self) -> None:
        self.pages.setCurrentIndex(0)
        self.confirm_box.setChecked(False)
        self._refresh()

    def do_lock(self) -> bool:
        """store.lock_session() 을 부른다. 성공하면 True(대화상자를 닫음), 실패하면 사유를 보이고 False."""
        if self.step != 2 or not self.confirm_box.isChecked() or not self.summary.can_lock or self.lock_doc is not None:
            return False
        try:
            if self._current_sample is not None:
                doc = self.store.lock_session(sample_verify=self._sample_verify, current_sample=self._current_sample)
            else:
                doc = self.store.lock_session(sample_verify=self._sample_verify)
        except Exception as e:  # noqa: BLE001 — 사유를 화면에 보이고 열어 둔다
            LOG.warning("세션 잠그기 실패: %s", type(e).__name__)
            self.error_label.setText(tr("잠그지 못했습니다: ") + str(e))
            self.error_label.show()
            return False
        self.lock_doc = doc
        self._refresh()
        self.sessionLocked.emit(doc)
        self.accept()
        return True
