"""시간 정렬(보정) 확정 화면 (08 D-32, 02 7-5 — 계획 표의 calibration_dialog 를 이 화면으로).

영상이 VOD 의 어디인지(보정 오프셋)를 앵커 후보를 들어 보고 정한다. 메인 창이 **보기 전용(세션 없음)으로 연 작업대 위에**
쌓는다(스트리머 훈련과 같은 자리) — 프로젝트 잠금은 작업대가 잡고, project.json 쓰기는 작업대의 통로(write)로 한다.
플레이어는 이 화면 안에서 만든다(TrainerView 와 같은 까닭: mpv 창 핸들).

- 후보: app.calibration.find_candidates(tools/find_anchors.py --json --list 를 따로 프로세스로, 뒤에서). 위치를 모르면(보정이 만든
  그대로) --locate, 아니면 활성 보정의 클립 시작 근처 창. 본문은 넘겨받은 lookup(chat_id) 로 앱 안에서만 보인다.
  전사 글은 보이지 않는다(라벨링 세션에서 토글·기록 대상 — 들려주기만, 근거는 코드).
- 고른 후보: 영상 [발화 시작 − PRE_MS, 발화 끝 + POST_MS] 를 반복 재생. **1 맞음 · 2 아님 · 3 모름**(숫자 줄·숫자 패드, 한글
  입력 상태도 키 위치로 — analysis_view AI 확인 큐와 같은 방식) → 저장하지 않고 다음 대기 건으로. 판정은 화면 안에만 둔다.
- 맞음 ≥ 3 이고 timecode 경고(흩어짐 3초·이상치) 없음이면 [확정 (Enter)] → verified 판(앵커 = 맞음 건, 오프셋 = 차이 중앙값 −
  읽기 지연, app.calibration.verified_rev_json). [건너뛰기…] 는 사유 → skipped 판(오프셋은 지금 활성 판 그대로).
  자리를 못 찾았거나 후보가 틀리면 '영상 0초 = VOD 시각' 칸에 아는 값을 넣고 [다시 찾기](그 근처 창으로 후보를 다시).
- [닫기] 는 아무것도 쓰지 않는다. finished(outcome) — 'verified' · 'skipped' · 'closed'.
로그에는 숫자·코드만(본문·닉네임 없음).
"""
from __future__ import annotations

import logging
from typing import Callable, Mapping

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import calibration as calib
from ..core import timecode as tc
from ..i18n import tr

LOG = logging.getLogger("chatcoach")

PRE_MS = 3000              # 발화 시작 앞 (채팅을 읽기 직전부터 들리게)
POST_MS = 1500             # 발화 끝 뒤
NO_END_MS = 4000           # 발화 끝을 모를 때 시작 뒤 이만큼
TABLE_MIN_W = 560          # 후보 표 최소 폭(px)
COLS = ("#", "채팅 시각", "영상 시각", "어긋남", "근거", "판정")
VERDICT_KO = {calib.YES: "맞음", calib.NO: "아님", calib.UNSURE: "모름", None: "대기"}
BASIS_KO = {"text": "채팅 읽음", "name": "닉네임 호명", "name?": "닉네임?"}
KEYS = {Qt.Key_1: calib.YES, Qt.Key_2: calib.NO, Qt.Key_3: calib.UNSURE}
VKEYS = {0x31: calib.YES, 0x32: calib.NO, 0x33: calib.UNSURE,          # 위 숫자 줄
         0x61: calib.YES, 0x62: calib.NO, 0x63: calib.UNSURE}          # 숫자 패드
WARN_KO = {"too_few_anchors": "맞음이 3개보다 적습니다", "spread_large": "맞음끼리 3초 넘게 어긋납니다",
           "outliers": "혼자 크게 어긋난 맞음이 있습니다(표에 표시) — 다시 들어 보세요"}


def _default_part(kind: str):
    if kind == "player":
        from ..player import MpvPlayer
        return MpvPlayer
    raise KeyError(kind)


def basis_text(basis: str) -> str:
    head = (basis or "").split("+", 1)[0]
    return tr(BASIS_KO.get(head, head or "-"))


def file_start_text(offset_ms: int) -> str:
    """저장 오프셋 → '영상 0초 = VOD h:mm:ss.s' 의 VOD 쪽(음수면 VOD 0 앞)."""
    return tc.fmt_display(-int(offset_ms))


class CalibrationView(QWidget):
    """보정 확정 화면. start() 로 시작한다."""

    finished = Signal(str)          # 'verified' · 'skipped' · 'closed'
    messageShown = Signal(str)

    def __init__(self, parent=None, *, parts: dict | None = None):
        super().__init__(parent)
        parts = parts or {}
        self.root: str | None = None
        self.data: dict = {}
        self.media_path: str | None = None
        self.labeler_id = "L01"
        self._write: Callable | None = None
        self._provider: Callable[[bool, int], calib.Found] | None = None
        self._lookup: Callable[[str], tuple | None] = lambda _cid: None
        self._run_bg: Callable | None = None
        self.found = calib.Found()
        self.cands: list[calib.Candidate] = []
        self.current: int | None = None
        self.saved: dict | None = None
        self.outcome: str | None = None
        self._busy = False
        self._closed = False
        self._has_media = False
        self.setFocusPolicy(Qt.StrongFocus)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 2)
        top = QHBoxLayout()
        title = QLabel(tr("시간 정렬(보정)"))
        title.setObjectName("panelTitle")
        top.addWidget(title)
        self.top_label = QLabel("")
        self.top_label.setObjectName("hint")
        self.top_label.setWordWrap(True)
        top.addWidget(self.top_label, 1)
        self.btn_close = QPushButton(tr("닫기"))
        self.btn_close.setToolTip(tr("아무것도 저장하지 않고 닫습니다"))
        self.btn_close.setFocusPolicy(Qt.NoFocus)
        self.btn_close.clicked.connect(self._on_close_btn)
        top.addWidget(self.btn_close)
        lay.addLayout(top)

        # 플레이어는 조상 사슬을 먼저 이 화면에 붙인 뒤 그 안에서 만든다(TrainerView 와 같은 까닭)
        self.split = QSplitter(Qt.Horizontal, self)
        left = QWidget(self.split)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.player = (parts.get("player") or _default_part("player"))(left)
        ll.addWidget(self.player, 1)
        self.chat_label = QLabel("")
        self.chat_label.setWordWrap(True)
        self.chat_label.setTextInteractionFlags(Qt.NoTextInteraction)
        f = self.chat_label.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)              # 들으면서 맞춰 볼 글 — 한눈에
        f.setBold(True)
        self.chat_label.setFont(f)
        ll.addWidget(self.chat_label)
        row = QHBoxLayout()
        self.verdict_buttons: dict[str, QPushButton] = {}
        for v, label in ((calib.YES, "맞음 1"), (calib.NO, "아님 2"), (calib.UNSURE, "모름 3")):
            b = QPushButton(tr(label))
            b.setMinimumHeight(32)
            b.setFocusPolicy(Qt.NoFocus)
            row.addWidget(b)
            self.verdict_buttons[v] = b
        self.verdict_buttons[calib.YES].clicked.connect(self._on_yes)
        self.verdict_buttons[calib.NO].clicked.connect(self._on_no)
        self.verdict_buttons[calib.UNSURE].clicked.connect(self._on_unsure)
        self.btn_replay = QPushButton(tr("다시 듣기 R"))
        self.btn_replay.setFocusPolicy(Qt.NoFocus)
        self.btn_replay.clicked.connect(self._on_replay)
        row.addWidget(self.btn_replay)
        ll.addLayout(row)
        self.split.addWidget(left)
        self.table = QTableWidget(0, len(COLS), self.split)
        self.table.setHorizontalHeaderLabels([tr(c) for c in COLS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_table_selection)
        self.split.addWidget(self.table)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.table.setMinimumWidth(TABLE_MIN_W)          # 여섯 칸이 가로 스크롤 없이 보이게(처음 크기는 sizeHint 라 좁다)
        lay.addWidget(self.split, 1)

        self.fit_label = QLabel("")
        self.fit_label.setWordWrap(True)
        lay.addWidget(self.fit_label)
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel(tr("영상 0초 = VOD")))
        self.ed_start = QLineEdit()
        self.ed_start.setPlaceholderText(tr("예: 1:02:03"))
        self.ed_start.setMaximumWidth(220)
        self.ed_start.setToolTip(tr("영상 0초가 방송(VOD)의 몇 분인지 알면 넣고 [다시 찾기] — 그 근처에서 후보를 다시 찾습니다"))
        self.ed_start.returnPressed.connect(self._on_refind)
        bottom.addWidget(self.ed_start)
        self.btn_refind = QPushButton(tr("다시 찾기"))
        self.btn_refind.setFocusPolicy(Qt.NoFocus)
        self.btn_refind.clicked.connect(self._on_refind)
        bottom.addWidget(self.btn_refind)
        bottom.addStretch(1)
        self.btn_skip = QPushButton(tr("건너뛰기…"))
        self.btn_skip.setToolTip(tr("앵커를 못 찾았을 때 사유를 적고 지금 오프셋 그대로 넘깁니다(03 4-2)"))
        self.btn_skip.setFocusPolicy(Qt.NoFocus)
        self.btn_skip.clicked.connect(self._on_skip)
        bottom.addWidget(self.btn_skip)
        self.btn_confirm = QPushButton(tr("확정 (Enter)"))
        self.btn_confirm.setDefault(True)
        self.btn_confirm.setMinimumHeight(32)
        self.btn_confirm.setFocusPolicy(Qt.NoFocus)
        self.btn_confirm.clicked.connect(self._on_confirm)
        bottom.addWidget(self.btn_confirm)
        lay.addLayout(bottom)
        self._install_keys()
        self._sync()

    # ------------------------------------------------------------------ 시작
    def start(self, root: str, data: Mapping | None, *, media_path: str | None, labeler_id: str,
              write: Callable[[Callable[[dict], None]], object], lookup: Callable[[str], tuple | None] | None = None,
              provider: Callable[[bool, int], calib.Found] | None = None,
              run_bg: Callable[[Callable[[], object], Callable[[object, str], None]], None] | None = None,
              find: bool = True) -> bool:
        """root·data(project.json)·media_path·labeler_id·write(mutate)(작업대 쓰기 통로). lookup(chat_id) → (채팅 시각,
        시청자 표시, 본문) 또는 None. provider(locate, file_start_ms) → Found(기본 find_candidates). run_bg(fn, done) 는 뒤에서
        돌릴 함수(없으면 그 자리에서). find=False 면 후보를 찾지 않는다(시험은 set_found 로 넣는다)."""
        self.root, self.data = root, dict(data or {})
        self.media_path, self.labeler_id = media_path, labeler_id or "L01"
        self._write = write
        self._lookup = lookup or (lambda _cid: None)
        self._provider = provider or self._default_provider
        self._run_bg = run_bg
        self._has_media = bool(media_path)
        if self._has_media:
            try:
                self.player.load(media_path)
            except Exception:  # noqa: BLE001 — 플레이어가 없어도 표는 쓴다
                self._has_media = False
        self._set_top()
        if find:
            pos = calib.media_position(self.data, media_path)
            self.find(locate=(pos == "unknown"), file_start_ms=calib.approx_file_start_ms(self.data))
        self._sync()
        return True

    def _default_provider(self, locate: bool, file_start_ms: int) -> calib.Found:
        # 화면을 닫으면(close_view) 도구 프로세스도 끝낸다(run_process 가 취소를 보고 프로세스 나무를 끝냄)
        return calib.find_candidates(self.root, locate=locate, file_start_ms=file_start_ms,
                                     cancelled=self.is_closed)

    def is_closed(self) -> bool:
        return self._closed

    def find(self, *, locate: bool, file_start_ms: int = 0) -> None:
        """후보 찾기(뒤에서). 끝나면 set_found."""
        if self._busy or self._closed:
            return
        self._busy = True
        self.fit_label.setText(tr("후보를 찾는 중… (영상이 VOD 의 어디인지)") if locate else tr("후보를 찾는 중…"))
        self._sync()
        prov = self._provider

        def job():
            return prov(bool(locate), int(file_start_ms))

        def done(res, err: str) -> None:
            self._busy = False
            if self._closed:
                return
            if not isinstance(res, calib.Found):
                res = calib.Found(error=(err or "no_result")[:40])
            self.set_found(res)
        if self._run_bg is None:
            try:
                r = job()
                done(r, "")
            except Exception as e:  # noqa: BLE001
                done(None, type(e).__name__)
        else:
            self._run_bg(job, done)

    # ------------------------------------------------------------------ 후보
    def set_found(self, found: calib.Found) -> None:
        self.found = found
        self.cands = list(found.candidates)
        self.current = None
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.cands))
        for i, c in enumerate(self.cands):
            self._fill_row(i, c)
        self.table.blockSignals(False)
        LOG.info("시간 정렬: 후보 %d · 서로 맞는 %s · 자리 %s · 오류 %s", len(self.cands), found.consistent,
                 "있음" if found.located else "-", found.error or "-")
        self._set_top()
        if self.cands:
            self.select(0)
        else:
            self.chat_label.setText("")
        self._sync()

    def _fill_row(self, i: int, c: calib.Candidate) -> None:
        med = self.found.median_offset_ms
        dev = (c.offset_ms - med) if med is not None else None
        vals = (str(i + 1), tc.fmt_display(c.chat_ms), tc.fmt_display(c.video_ms),
                tc.fmt_offset(dev) if dev is not None else "-", basis_text(c.basis), tr(VERDICT_KO.get(c.verdict, "-")))
        for col, v in enumerate(vals):
            it = self.table.item(i, col)
            if it is None:
                it = QTableWidgetItem(v)
                self.table.setItem(i, col, it)
            else:
                it.setText(v)

    def select(self, i: int) -> None:
        if not (0 <= i < len(self.cands)):
            return
        self.current = i
        if self.table.currentRow() != i:
            self.table.blockSignals(True)
            self.table.selectRow(i)
            self.table.blockSignals(False)
        c = self.cands[i]
        info = self._lookup(c.chat_id)
        if info:
            t, who, text = info
            self.chat_label.setText(tr("채팅 {0} · {1} · {2}").format(tc.fmt_display(int(t)), who, text))
        else:
            self.chat_label.setText(tr("채팅 {0}").format(tc.fmt_display(c.chat_ms)))
        self.play_current()
        self._sync()

    def loop_window(self, i: int | None = None) -> tuple[int, int] | None:
        """후보 i 의 반복 재생 범위(영상 파일 시각)."""
        i = self.current if i is None else i
        if i is None or not (0 <= i < len(self.cands)):
            return None
        c = self.cands[i]
        end = c.video_end_ms if c.video_end_ms is not None else c.video_ms + NO_END_MS
        return max(0, c.video_ms - PRE_MS), end + POST_MS

    def play_current(self) -> None:
        w = self.loop_window()
        if w is None or not self._has_media:
            return
        try:
            self.player.set_ab_loop(*w)
            self.player.seek(w[0], True)
            self.player.play()
        except Exception:  # noqa: BLE001 — 재생 실패로 판정을 막지 않는다
            LOG.info("시간 정렬: 재생 실패")

    # ------------------------------------------------------------------ 판정
    def judge(self, verdict: str) -> None:
        """고른 후보에 판정(맞음·아님·모름)을 달고 다음 대기 건으로."""
        if self.current is None or verdict not in calib.VERDICTS or self._closed:
            return
        i = self.current
        self.cands[i].verdict = verdict
        self._fill_row(i, self.cands[i])
        nxt = next((k for k in list(range(i + 1, len(self.cands))) + list(range(0, i))
                    if self.cands[k].verdict is None), None)
        if nxt is not None:
            self.select(nxt)
        else:
            try:
                self.player.pause()
            except Exception:  # noqa: BLE001
                pass
        self._sync()

    def fit(self) -> tc.OffsetFit | None:
        return calib.fit_yes(self.cands)

    def can_confirm(self) -> bool:
        return not self._busy and not self._closed and self._write is not None and calib.ready(self.fit())

    # ------------------------------------------------------------------ 저장
    def _save(self, build: Callable[[int, int, Mapping], dict]) -> dict | None:
        try:
            rj = calib.add_rev(self._write, build)
        except Exception as e:  # noqa: BLE001 — Conflict·ReadOnly·검증 실패: 쓰지 않았다고 알린다
            self.fit_label.setText(tr("저장하지 못했습니다: {0}").format(type(e).__name__))
            LOG.warning("시간 정렬: 저장 실패 %s", type(e).__name__)
            return None
        self.saved = rj
        return rj

    def confirm(self) -> bool:
        """맞음 건으로 verified 판을 더하고 finished('verified')."""
        if not self.can_confirm():
            return False
        f = self.fit()
        by, now, cands = self.labeler_id, calib.now_iso(), list(self.cands)

        def build(rev: int, media_rev: int, _d) -> dict:
            return calib.verified_rev_json(f, cands, rev=rev, media_rev=media_rev, by=by, now_iso=now)
        rj = self._save(build)
        if rj is None:
            return False
        self._finish("verified", rj)
        return True

    def skip(self, reason: str | None = None) -> bool:
        """사유를 받아 skipped 판(오프셋은 지금 활성 판 그대로)을 더하고 finished('skipped'). 사유가 없으면 쓰지 않는다."""
        if self._busy or self._closed or self._write is None:
            return False
        if reason is None:
            txt, ok = QInputDialog.getText(self, tr("건너뛰기"), tr("시간 정렬을 건너뛰는 사유 (기록·상태 줄에 남습니다)"))
            reason = txt if ok else ""
        reason = (reason or "").strip()
        if not reason:
            return False
        by, now = self.labeler_id, calib.now_iso()

        def build(rev: int, media_rev: int, d) -> dict:
            return calib.skipped_rev_json(d, rev=rev, media_rev=media_rev, by=by, now_iso=now, reason=reason)
        rj = self._save(build)
        if rj is None:
            return False
        self._finish("skipped", rj)
        return True

    def refind(self, text: str | None = None) -> bool:
        """'영상 0초 = VOD 시각' 칸의 값 근처 창으로 후보를 다시 찾는다(판정은 지운다)."""
        t = tc.parse_time(self.ed_start.text() if text is None else text)
        if t is None or t < 0:
            self.fit_label.setText(tr("VOD 시각을 읽지 못했습니다 (예: 1:02:03)"))
            return False
        self.find(locate=False, file_start_ms=int(t))
        return True

    def _finish(self, outcome: str, rj: dict | None) -> None:
        self.outcome = outcome
        off = rj.get("offset_ms") if rj else None
        if isinstance(off, int):
            self.messageShown.emit(tr("시간 정렬 저장: 판 {0} · {1} · 영상 0초 = VOD {2}").format(
                rj.get("rev"), rj.get("status"), file_start_text(off)))
        self.close_view()
        self.finished.emit(outcome)

    def close_view(self) -> None:
        """플레이어를 닫는다(여러 번 불러도 된다). 저장은 하지 않는다."""
        if self._closed:
            return
        self._closed = True
        try:
            self.player.close_player()
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event):
        self.close_view()
        super().closeEvent(event)

    # ------------------------------------------------------------------ 표시
    def _set_top(self) -> None:
        parts = []
        md = calib.media_duration_ms(self.data)
        if md:
            parts.append(tr("영상 {0}").format(tc.fmt_time(md, ms_digits=False)))
        pos = calib.media_position(self.data, self.media_path)
        cur = calib.active_calibration(self.data)
        state = {"confirmed": tr("확인됨"), "estimated": tr("자동 추정 — 확인 전"), "clip": tr("파일 이름의 클립 시작"),
                 "full": tr("VOD 전체 파일"), "unknown": tr("모름"), "manual": tr("확인 전")}.get(pos, pos)
        parts.append(tr("지금: 영상 0초 = VOD {0} ({1})").format(file_start_text(cur.offset_ms), state))
        f = self.found
        if f.located:
            parts.append(tr("찾은 자리 ≈ VOD {0}").format(tc.fmt_display(int(f.located.get("file_start_ms") or 0))))
        if f.error:
            parts.append(tr("후보를 찾지 못했습니다 ({0})").format(f.error))
        elif self.cands:
            parts.append(tr("후보 {0} · 서로 맞는 {1}").format(len(self.cands), f.consistent or 0))
        elif not self._busy and self.root:
            parts.append(tr("후보 없음 — 아는 VOD 시각을 넣고 [다시 찾기] 하거나 건너뛰세요"))
        self.top_label.setText(" · ".join(parts))

    def _sync(self) -> None:
        has = self.current is not None and not self._busy and not self._closed
        for b in self.verdict_buttons.values():
            b.setEnabled(has)
        self.btn_replay.setEnabled(has and self._has_media)
        self.btn_refind.setEnabled(not self._busy and not self._closed)
        self.btn_skip.setEnabled(not self._busy and not self._closed and self._write is not None)
        self.btn_confirm.setEnabled(self.can_confirm())
        if self._busy:
            return
        n = calib.counts(self.cands)
        f = self.fit()
        head = tr("맞음 {0} · 아님 {1} · 모름 {2} · 대기 {3}").format(n[calib.YES], n[calib.NO], n[calib.UNSURE],
                                                                 n["pending"])
        if f is None:
            tail = tr("고른 후보를 듣고 스트리머가 그 채팅을 읽거나 닉네임을 부르면 1, 아니면 2, 모르겠으면 3 — 맞음 3개면 확정")
        else:
            off = calib.lag_corrected(f.offset_ms)
            tail = tr("확정하면 영상 0초 = VOD {0} (차이 중앙값에서 읽기 지연 {1:.1f}초를 뺌) · 흩어짐 {2}").format(
                file_start_text(off), calib.READ_LAG_MS / 1000, tc.fmt_offset(f.spread_ms))
            warns = [tr(WARN_KO.get(w, w)) for w in f.warnings]
            if warns:
                tail += " · " + " · ".join(warns)
            yes = [i for i, c in enumerate(self.cands) if c.verdict == calib.YES]
            for k, out in zip(yes, f.outliers):
                it = self.table.item(k, 5)
                if it is not None:
                    it.setText(tr("맞음(어긋남)") if out else tr("맞음"))
        self.fit_label.setText(head + " — " + tail)

    # ------------------------------------------------------------------ 키
    def _install_keys(self) -> None:
        """1·2·3·R·Enter 는 이 화면과 자식 칸에 건 eventFilter 로만 받는다(이 화면 안에 초점이 있을 때 — 메뉴 단축키와 겹치지
        않게 QShortcut 을 두지 않는다). 한글 입력 상태의 숫자·R(ㄱ)은 키 위치(nativeVirtualKey)로 되살린다."""
        self.installEventFilter(self)
        for w in self.findChildren(QWidget):
            if w.focusPolicy() != Qt.NoFocus:
                w.installEventFilter(self)

    def eventFilter(self, obj, ev):  # noqa: N802
        try:
            if ev.type() != QEvent.Type.KeyPress or self._closed:
                return False
            if obj is self.ed_start or self.ed_start.hasFocus() or self.window().focusWidget() is self.ed_start:
                # 칸에 글을 쓰는 중에는 가로채지 않는다(Enter = 다시 찾기). QLineEdit 은 Enter 를 returnPressed 로 알린 뒤
                # 부모로 올려 보내므로, 화면(self)에서 받은 그 Enter 도 확정으로 번지지 않게 초점으로 막는다
                return False
            if ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
                return False
            vk = int(ev.nativeVirtualKey())
            txt = ev.text()
            v = KEYS.get(ev.key()) or VKEYS.get(vk) or {"1": calib.YES, "2": calib.NO, "3": calib.UNSURE}.get(txt)
            if v is not None:
                self.judge(v)
                return True
            if ev.key() == Qt.Key_R or (vk == 0x52 and txt and "ㄱ" <= txt[0] <= "ㆎ"):   # 한글 'ㄱ' 자리 = R
                self.play_current()
                return True
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.confirm()
                return True
        except Exception:  # noqa: BLE001 — 키 하나 때문에 화면을 멈추지 않는다
            return False
        return False

    # ------------------------------------------------------------------ 단추
    def _on_table_selection(self) -> None:
        r = self.table.currentRow()
        if r >= 0 and r != self.current:
            self.select(r)

    def _on_yes(self, _c: bool = False) -> None:
        self.judge(calib.YES)

    def _on_no(self, _c: bool = False) -> None:
        self.judge(calib.NO)

    def _on_unsure(self, _c: bool = False) -> None:
        self.judge(calib.UNSURE)

    def _on_replay(self, _c: bool = False) -> None:
        self.play_current()

    def _on_confirm(self, _c: bool = False) -> None:
        self.confirm()

    def _on_skip(self, _c: bool = False) -> None:
        self.skip()

    def _on_refind(self, _c: bool = False) -> None:
        self.refind()

    def _on_close_btn(self, _c: bool = False) -> None:
        if self._closed:
            return
        self.outcome = "closed"
        self.close_view()
        self.finished.emit("closed")
