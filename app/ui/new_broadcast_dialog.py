"""'파일 → 새 방송…' 대화상자 (08 D-31 2). 입력은 둘뿐 — VOD 번호(또는 치지직 다시보기 주소)와 영상 파일.

- 주소를 붙여 넣으면 번호만 남긴다(app.chzzk_chat.parse_video_no — 'https://chzzk.naver.com/video/<번호>').
- 영상 파일은 [고르기…] 로(경로를 붙여 넣어도 된다 — 탐색기 '경로로 복사' 의 따옴표는 뗀다). ClipSlicer 이름
  ('<제목> [<VOD번호>] <시작>-<끝>[_N].mp4')이면 번호 칸이 비었을 때 그 번호로 채운다(app.engine_run.parse_video_file_name).
  번호 칸에 다른 번호가 이미 있으면 바꾸지 않고 한 줄로 알린다.
- 라벨러 ID 는 앱 설정(마지막 라벨러)의 것을 쓴다. 없을 때만 칸이 하나 더 보인다.
- '전사 범위(선택)' 칸 하나(08 D-33 '시작 옮기기'): 긴 영상에서 가운데만 전사할 때 영상 파일 시각으로 '시작-끝'(예 1:20:00-2:00:00),
  '시작' 만 쓰면 끝까지. 비우면 지금처럼 처음부터(같은 번호로 다시 할 때는 있는 전사를 그대로). 클릭은 늘지 않는다 — 쓸 때만 칸 하나.
  읽은 범위는 힌트 줄에 보인다(app.engine_run.parse_range_text). 시범 프로젝트는 범위가 고정이라 이 칸을 쓰지 않는다.
- [시작] 하나. 진행·결과는 메인 창(MainWindow.run_job_modal)이 보인다. 이 화면은 파일을 쓰지 않고 네트워크도 쓰지 않는다.
시험·리허설은 choose_file(파일 고르기 창 대신 부를 함수)을 바꿔 끼운다.
"""
from __future__ import annotations

import os
import re

from PySide6.QtWidgets import (QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from .. import chzzk_chat, engine_run, media
from ..i18n import tr

LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")       # app.session.LABELER_RE 와 같다
VIDEO_FILTER = "영상 (*.mp4 *.mkv *.ts *.flv *.mov *.webm);;모든 파일 (*)"
CLIPSLICER_DIR = ("Videos", "ClipSlicer")                 # ClipSlicer 가 받은 영상의 기본 자리(처음 고를 때 시작 폴더)


def clean_path_text(text: str | None) -> str:
    """붙여 넣은 경로 글: 앞뒤 공백과 감싼 따옴표를 뗀다."""
    t = (text or "").strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    return t


class NewBroadcastDialog(QDialog):
    """VOD 번호 · 영상 파일(· 라벨러 ID) → values(). [시작] 은 칸이 다 맞을 때만 닫힌다."""

    def __init__(self, parent=None, *, settings=None, labeler_id: str | None = None, video_no: str | None = None,
                 video_path: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("새 방송"))
        self.settings = settings
        self.fixed_labeler = labeler_id if isinstance(labeler_id, str) and LABELER_RE.match(labeler_id) else None
        self.choose_file = self._choose_file_dialog
        lay = QVBoxLayout(self)
        intro = QLabel(tr("VOD 번호와 영상 파일만 주면 프로젝트 만들기 · 채팅 받기 · 영상 연결 · 엔진(전사·판정)까지 한 번에 하고 "
                          "분석 화면을 엽니다. 같은 번호로 다시 하면 이어서 합니다."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        grid = QGridLayout()
        grid.addWidget(QLabel(tr("VOD 번호 또는 다시보기 주소")), 0, 0)
        self.ed_vod = QLineEdit()
        self.ed_vod.setPlaceholderText(tr("예: 15767532 또는 https://chzzk.naver.com/video/15767532"))
        grid.addWidget(self.ed_vod, 0, 1, 1, 2)
        grid.addWidget(QLabel(tr("영상 파일")), 1, 0)
        self.ed_video = QLineEdit()
        self.ed_video.setPlaceholderText(tr("[고르기…] 로 고르거나 경로를 붙여 넣으세요"))
        grid.addWidget(self.ed_video, 1, 1)
        self.btn_pick = QPushButton(tr("고르기…"))
        grid.addWidget(self.btn_pick, 1, 2)
        grid.addWidget(QLabel(tr("전사 범위(선택)")), 2, 0)
        self.ed_range = QLineEdit()
        self.ed_range.setPlaceholderText(tr("비우면 처음부터 · 가운데만: 1:20:00-2:00:00 (영상 파일 시각, 시작만 쓰면 끝까지)"))
        grid.addWidget(self.ed_range, 2, 1, 1, 2)
        self.ed_labeler = None
        if self.fixed_labeler is None:
            grid.addWidget(QLabel(tr("라벨러 ID")), 3, 0)
            self.ed_labeler = QLineEdit()
            self.ed_labeler.setPlaceholderText(tr("예: L01 (이 PC 에 처음 한 번만)"))
            grid.addWidget(self.ed_labeler, 3, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_start = QPushButton(tr("시작"))
        self.btn_start.setDefault(True)
        self.btn_cancel = QPushButton(tr("취소"))
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)
        self.ed_vod.textChanged.connect(self._refresh)
        self.ed_vod.editingFinished.connect(self._normalize_vod)
        self.ed_video.textChanged.connect(self._refresh)
        self.ed_video.editingFinished.connect(lambda: self.set_video(self.ed_video.text()))
        self.ed_range.textChanged.connect(self._refresh)
        if self.ed_labeler is not None:
            self.ed_labeler.textChanged.connect(self._refresh)
        self.btn_pick.clicked.connect(self.pick)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_cancel.clicked.connect(self.reject)
        if video_no:
            self.ed_vod.setText(str(video_no))
            self._normalize_vod()
        if video_path:
            self.set_video(video_path)
        self.resize(640, self.sizeHint().height())
        self._refresh()

    # ------------------------------------------------------------ 값
    def video_no(self) -> str | None:
        return chzzk_chat.parse_video_no(self.ed_vod.text())

    def video_path(self) -> str | None:
        t = clean_path_text(self.ed_video.text())
        return os.path.normpath(t) if t else None

    def labeler_id(self) -> str | None:
        if self.fixed_labeler is not None:
            return self.fixed_labeler
        t = (self.ed_labeler.text() if self.ed_labeler is not None else "").strip().upper()
        return t or None

    def asr_range(self) -> tuple[int, int | None] | None:
        """'전사 범위' 칸 → (시작 ms, 끝 ms 또는 None). 비었으면 None. 못 읽으면 ValueError."""
        return engine_run.parse_range_text(self.ed_range.text())

    def problems(self) -> list[str]:
        out = []
        if not self.video_no():
            out.append(tr("VOD 번호를 읽지 못했습니다 (숫자 또는 https://chzzk.naver.com/video/<번호>)"))
        p = self.video_path()
        if not p:
            out.append(tr("영상 파일을 고르세요"))
        else:
            why = media.check_path_string(p)
            if why:
                out.append(tr("영상 경로를 받을 수 없습니다: {0}").format(tr(why)))
            elif not os.path.isfile(p):
                out.append(tr("영상 파일이 없습니다"))
        try:
            self.asr_range()
        except ValueError:
            out.append(tr("전사 범위를 읽지 못했습니다 (예: 1:20:00-2:00:00 · 끝이 시작보다 뒤)"))
        lid = self.labeler_id()
        if self.fixed_labeler is None and (not lid or not LABELER_RE.match(lid)):
            out.append(tr("라벨러 ID 를 적으세요 (예: L01 — 영문 대문자로 시작, 2~8자)"))
        return out

    def values(self) -> dict:
        """칸 값. asr_start_ms·asr_limit_ms 는 범위 칸이 비었으면 None(engine_run.range_to_request)."""
        try:
            a, n = engine_run.range_to_request(self.asr_range())
        except ValueError:
            a, n = None, None
        return {"video_no": self.video_no(), "video_path": self.video_path(), "labeler_id": self.labeler_id(),
                "asr_start_ms": a, "asr_limit_ms": n}

    # ------------------------------------------------------------ 동작
    def _normalize_vod(self) -> None:
        """주소를 붙여 넣었으면 번호만 남긴다."""
        no = self.video_no()
        if no and self.ed_vod.text().strip() != no:
            self.ed_vod.setText(no)

    def set_video(self, path: str | None) -> None:
        """영상 경로를 칸에 넣고, ClipSlicer 이름의 번호로 빈 번호 칸을 채운다."""
        t = clean_path_text(path)
        if t and self.ed_video.text() != t:
            self.ed_video.setText(t)
        vno, _start = engine_run.parse_video_file_name(t)
        if vno and not self.ed_vod.text().strip():
            self.ed_vod.setText(vno)
        self._refresh()

    def _start_dir(self) -> str:
        from .project_view import DIR_MEDIA, last_dir
        home = os.path.expanduser("~")
        cs = os.path.join(home, *CLIPSLICER_DIR)
        fallback = cs if os.path.isdir(cs) else os.path.join(home, "Videos")
        return last_dir(self.settings, DIR_MEDIA, fallback)

    def _choose_file_dialog(self) -> str | None:
        p, _f = QFileDialog.getOpenFileName(self, tr("영상 파일 고르기"), self._start_dir(), tr(VIDEO_FILTER))
        return p or None

    def pick(self) -> None:
        p = self.choose_file()
        if not p:
            return
        p = os.path.normpath(p)
        from .project_view import DIR_MEDIA, remember_dir
        remember_dir(self.settings, DIR_MEDIA, os.path.dirname(p))
        self.set_video(p)

    def _refresh(self, *_a) -> None:
        lines = []
        no = self.video_no()
        if no:
            lines.append(tr("VOD 번호 {0}").format(no))
        fno, start = engine_run.parse_video_file_name(self.video_path())
        if fno and no and fno != no:
            lines.append(tr("영상 파일 이름의 번호({0})와 다릅니다 — 맞는지 확인하세요").format(fno))
        elif fno and start:
            lines.append(tr("클립: 파일 0초 = VOD {0}").format(engine_run.fmt_s(start / 1000)))
        try:
            rng = self.asr_range()
        except ValueError:
            rng = None
            lines.append(tr("전사 범위를 읽지 못했습니다"))
        if rng is not None:
            lines.append(tr("전사: 영상 {0}").format(engine_run.fmt_range(*rng)))
        self.hint.setText(" · ".join(lines))

    def _on_start(self) -> None:
        probs = self.problems()
        if probs:
            self.hint.setText("\n".join("• " + p for p in probs))
            return
        self.accept()
