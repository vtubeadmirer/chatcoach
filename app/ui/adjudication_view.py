"""조정 화면 (docs/design/03-data-model.md 6-11·6-12·12-1, 06 3장·5장·7-1, 08 D-12 3·4·7, D-13 5).

조정(adjudication) 세션에서만 연다. 그 세션은 잠긴 세션을 모두 읽을 수 있다(03 12-1 '잠긴 세션만 읽기').
- 위: 조정자 = 라벨러 표시(D-12 3), 비교할 잠긴 세션 목록(체크, 둘 이상), [불일치 불러오기].
- 가운데: 불일치 목록(구간·표본, 시각 순)과 고른 건. 목록 줄에는 갈래·시각·두 값과 조정 여부만 보인다.
  고른 건의 두(이상) 판정을 **나란히** — 판정·확신도·근거 한 줄(D-12 4). 근거는 이 화면에만 보이고 보고서·로그에 쓰지
  않는다. 아래에서 결정(한쪽을 최종값 / 새 값(구간만) / 평가에서 제외 / 진짜 모호함으로 남김), 원인(cause, 필수),
  pattern_tag, 메모를 적고 [조정 저장] → store.append_label(kind=adjudication). 이미 조정한 건을 다시 저장하면 그 조정
  기록의 새 판이 된다(supersedes).
- 아래: 기준서 개정 후보(같은 pattern_tag 3건 이상, 06 5장)와 조정 세션 도구 두 개.
  · [출처 판정 대상 만들기] (D-13 5): 잠긴 1차 세션들의 표본 매니페스트 chat_id 로 origin/OT-*.json(basis =
    sample_items)을 만든다. 대상 dict 는 app.core.origin.build_sample_targets 가 만들고, 파일 쓰기는 생성자에 받은
    write_origin_targets 콜백(통합이 store 경유로 연결)이 한다. 같은 chat_id 목록의 대상 파일이 이미 있으면 만들지 않는다.
  · 두 번째 사람 재라벨 계획 (14 4-2 의 9): 잠긴 1차 세션과 라벨러 ID 를 골라 relabel_second 계획을 만든다
    (app.relabel.create_second_plan). 같은 사람 재라벨이 남은 동안 1차를 다시 열 수 없어도 여기서 만들 수 있다.
  재라벨 계획 취소는 메뉴 '세션 → 재라벨 계획 취소…'(app/ui/plan_cancel_dialog.py) 하나로 한다 — 1366x768 에서
  이 화면 아래 칸이 오른쪽 밖으로 나가던 문제(14 4-2 의 1).

좁은 칸(1366x768 의 오른쪽 칸 471px·731px)에서 가로로 잘리지 않게: 목록과 고른 건은 폭이 좁으면 위아래로 놓고
(NARROW_PX 미만), 고른 건은 세로 스크롤 칸에 넣는다. '새 값' 은 두 줄(바탕·분류 / 시작·끝), 원인·유형 태그·메모는 한 줄에
하나씩. 고르기 칸은 글자 길이로 넓어지지 않게 한다(_shrinkable).

계산은 app.core.adjudicate(불일치 목록·본문·집계)와 app.core.agreement(짝짓기)가 한다. 이 파일은 화면만 맡는다.
로그에는 숫자·ID 만 남긴다(채팅 본문·근거·닉네임 없음).
"""
from __future__ import annotations

import itertools
import logging
import os
from datetime import datetime
from typing import Callable, Iterable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QVBoxLayout, QWidget)

from .. import relabel as relabel_io
from ..core import adjudicate as adj
from ..core import agreement as ag
from ..core import origin as og
from ..core import visibility as vis
from ..core.timecode import fmt_display
from ..i18n import tr

LOG = logging.getLogger("chatcoach")

_CAT_KO = {"just_chatting": "저스트채팅", "game": "게임", "singing": "노래", "other": "기타", "undeterminable": "판단 불가"}
_VAL_KO = {"direct": "직접", "likely": "높음", "related_only": "관련", "unconfirmed": "미확인", "unclear": "불명확",
           "undeterminable": "판단 불가", "invalid": "형식 오류"}
_CONF_KO = {"certain": "확실함", "probably": "아마도", "undeterminable": "판단 불가"}
# 결정 고르기 칸의 값(문자열): 'pick:<session_id>' · 'new' · 'exclude_from_eval' · 'keep_as_ambiguous'
_NEW = "new"
_PICK = "pick"
# 이 폭보다 좁으면 목록과 고른 건을 위아래로 놓는다(1366x768 의 오른쪽 칸 471·731·870px 는 위아래).
# 900 이던 때 실제 창 1366 에서 조정 칸이 900px 이 되어 나란히 배치로 바뀌고 오른쪽 밖 48 이 났다(15 5-2 의 2번) —
# 1200 으로 올려 작업대가 따로 870 이하로 묶지 않아도 노트북 화면에서는 늘 위아래가 되게 한다.
NARROW_PX = 1200


def _split_key(data) -> tuple[str | None, str | None]:
    if not isinstance(data, str) or not data:
        return None, None
    if data.startswith(_PICK + ":"):
        return _PICK, data[len(_PICK) + 1:]
    return data, None


# 비교 계산에서 신뢰구간은 쓰지 않는다(불일치 목록만 필요) — 부트스트랩을 끈다
_FAST_SETTINGS = {"agreement": {"bootstrap_n": 0}}


def _t(ms) -> str:
    return fmt_display(ms) if isinstance(ms, int) and not isinstance(ms, bool) else "—"


def _shrinkable(box: QComboBox, chars: int = 10) -> QComboBox:
    """고르기 칸이 가장 긴 항목 글자 길이로 넓어지지 않게 한다(좁은 칸에서 오른쪽 밖으로 나가지 않게)."""
    box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    box.setMinimumContentsLength(chars)
    return box


def value_ko(case: adj.Case, v: str | None) -> str:
    if v is None:
        return tr("(판정 없음)")
    return tr((_CAT_KO if case.target_kind == adj.TARGET_SEGMENT else _VAL_KO).get(v, v))


def case_line(case: adj.Case, state: adj.CaseState | None) -> str:
    """목록 한 줄: 갈래·시각·값들·조정 여부(근거·본문 없음)."""
    where = (f"{_t(case.spans[0][0])}~{_t(case.spans[-1][1])}" if case.spans else _t(case.t_ms))
    vals = " / ".join(value_ko(case, s.value) for s in case.sides)
    mark = ""
    if state is not None and state.record is not None:
        d = (state.record.get("body") or {}).get("decision")
        mark = " ✓ " + tr(adj.DECISION_KO.get(d, d or ""))
        if state.targets_changed:
            mark += tr(" (대상 바뀜)")
        elif state.stale:
            mark += tr(" (판 바뀜)")
    kind = tr("구간") if case.target_kind == adj.TARGET_SEGMENT else tr("표본")
    return f"[{kind}·{tr(adj.CASE_KIND_KO.get(case.kind, case.kind))}] {where}  {vals}{mark}"


def _scope_ranges(view) -> list[tuple[int, int]] | None:
    """잠긴 세션 뷰의 session.open.scope(blocks) → [(시작, 끝)]. 전체면 None."""
    for r in getattr(view, "other_records", ()) or ():
        if r.get("kind") != "session.open":
            continue
        sc = (r.get("body") or {}).get("scope")
        if not isinstance(sc, Mapping) or sc.get("type") != "blocks":
            return None
        out = []
        for x in sc.get("ranges") or []:
            if isinstance(x, (list, tuple)) and len(x) == 2 and all(isinstance(v, int) for v in x) and x[1] > x[0]:
                out.append((x[0], x[1]))
        return out
    return None


def buffer_ms_of(project_data: Mapping | None, guide_version: str | None = None) -> int:
    """기준서 segment.boundary_buffer_sec (없으면 10초). guide_version 이 None 이면 active_guide_version."""
    data = project_data or {}
    gv = guide_version or data.get("active_guide_version")
    for g in data.get("guides") or []:
        if isinstance(g, Mapping) and g.get("guide_version") == gv:
            sg = ((g.get("thresholds") or {}).get("segment") or {}) if isinstance(g.get("thresholds"), Mapping) else {}
            v = sg.get("boundary_buffer_sec") if isinstance(sg, Mapping) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                return int(round(v * 1000))
    return ag.DEFAULT_BUFFER_MS


class AdjudicationView(QWidget):
    """조정 화면. store: 조정 세션의 ProjectStore(또는 같은 메서드를 가진 것).

    chat_info(chat_id) → (t_ms 또는 None, 화면에 보일 채팅 한 줄 또는 None). 없으면 chat_id 만 보인다.
    write_origin_targets(대상 dict) → 쓴 경로. 출처 판정 대상 파일을 쓰는 콜백(통합이 store 경유로 연결, 불변 — 이미 있으면
      FileExistsError). 없으면 [출처 판정 대상 만들기] 가 꺼진다.
    list_origin_targets() → 이미 있는 대상(app.core.origin.OriginTargets 목록). 없으면 origin/ 을 origin_view.list_targets
      로 읽는다(조정 세션은 origin/ 을 읽을 수 있다, 03 12-1).
    coverage() → 영상이 덮는 VOD 범위 [(a, z)] 또는 None. relabel_second 계획의 done_ranges·블록 범위를 자른다.
    create_second_plan(원 세션 ID, 라벨러 ID, *, coverage, app_version, now, rand4) → 계획 dict. relabel_second 계획을
      만드는 콜백(통합이 store.create_second_plan 으로 연결). 없으면 이 화면이 relabel.create_second_plan 을 바로 부른다.
    now() → 지금(시간대 있는 datetime). 시험이 주입한다. 없으면 PC 시각.
    """

    def __init__(self, parent=None, *, store, app_version: str = "",
                 chat_info: Callable[[str], tuple] | None = None,
                 write_origin_targets: Callable[[dict], str] | None = None,
                 list_origin_targets: Callable[[], Iterable[og.OriginTargets]] | None = None,
                 coverage: Callable[[], list | None] | None = None,
                 create_second_plan: Callable[..., dict] | None = None,
                 now: Callable[[], datetime] | None = None):
        super().__init__(parent)
        self.store = store
        self.app_version = app_version
        self.chat_info = chat_info
        self.create_second_plan_cb = create_second_plan
        self.write_origin_targets = write_origin_targets
        self.list_origin_targets = list_origin_targets
        self.coverage = coverage
        self._now = now
        self.me = store.session.labeler_id
        self.cases: list[adj.Case] = []
        self.states: dict[str, adj.CaseState] = {}
        self.current: adj.Case | None = None
        self.labelers: list[str] = []
        self.last_error: str | None = None
        self._build()
        self.refresh_sessions()
        self.refresh_plans()
        self._refresh_bottom()

    # ------------------------------------------------------------------ 화면 만들기
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        lay.addWidget(self.banner)
        top = QHBoxLayout()
        self.session_list = QListWidget()
        self.session_list.setMaximumHeight(90)
        top.addWidget(self.session_list, 1)
        self.btn_load = QPushButton(tr("불일치 불러오기"))
        self.btn_load.clicked.connect(lambda: self.load_cases())
        top.addWidget(self.btn_load)
        lay.addLayout(top)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        lay.addWidget(self.summary_label)

        self.split = QSplitter(Qt.Vertical)
        self.split.setChildrenCollapsible(False)
        self.case_list = QListWidget()
        self.case_list.setWordWrap(True)
        self.case_list.setMinimumHeight(80)
        self.case_list.currentRowChanged.connect(self._on_row)
        self.split.addWidget(self.case_list)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        self.case_title = QLabel("")
        self.case_title.setWordWrap(True)
        rl.addWidget(self.case_title)
        self.side_grid = QGridLayout()
        rl.addLayout(self.side_grid)
        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.addWidget(QLabel(tr("결정")), 0, 0)
        self.decision_box = _shrinkable(QComboBox(), 14)
        self.decision_box.currentIndexChanged.connect(lambda _i: self._refresh_new_value())
        form.addWidget(self.decision_box, 0, 1)
        # 새 값(구간만): 두 줄 — 바탕·분류 / 시작·끝 (14 4-2 의 1)
        self.new_row = QWidget()
        ng = QGridLayout(self.new_row)
        ng.setContentsMargins(0, 0, 0, 0)
        ng.addWidget(QLabel(tr("새 값 바탕")), 0, 0)
        self.new_base = _shrinkable(QComboBox(), 6)
        ng.addWidget(self.new_base, 0, 1)
        ng.addWidget(QLabel(tr("분류")), 0, 2)
        self.new_cat = _shrinkable(QComboBox(), 4)
        for c in ag.CATEGORIES:
            self.new_cat.addItem(tr(_CAT_KO[c]), c)
        ng.addWidget(self.new_cat, 0, 3)
        ng.addWidget(QLabel(tr("시작(초)")), 1, 0)
        self.new_start = QSpinBox()
        self.new_start.setRange(0, 24 * 3600)
        ng.addWidget(self.new_start, 1, 1)
        ng.addWidget(QLabel(tr("끝(초)")), 1, 2)
        self.new_end = QSpinBox()
        self.new_end.setRange(0, 24 * 3600)
        ng.addWidget(self.new_end, 1, 3)
        ng.setColumnStretch(1, 1)
        ng.setColumnStretch(3, 1)
        form.addWidget(self.new_row, 1, 0, 1, 2)
        form.addWidget(QLabel(tr("원인")), 2, 0)
        self.cause_box = _shrinkable(QComboBox(), 10)
        self.cause_box.addItem(tr("(고르세요)"), None)
        for c in adj.CAUSES:
            self.cause_box.addItem(tr(adj.CAUSE_KO[c]), c)
        form.addWidget(self.cause_box, 2, 1)
        form.addWidget(QLabel(tr("유형 태그")), 3, 0)
        self.tag_edit = QLineEdit()
        self.tag_edit.setMaxLength(adj.PATTERN_TAG_MAX)
        self.tag_edit.setPlaceholderText(tr("같은 유형끼리 같은 태그 (선택)"))
        form.addWidget(self.tag_edit, 3, 1)
        form.addWidget(QLabel(tr("메모")), 4, 0)
        self.note_edit = QLineEdit()
        self.note_edit.setMaxLength(adj.NOTE_MAX)
        self.note_edit.setPlaceholderText(tr("조정 이유 (선택, 채팅 내용·닉네임은 적지 않기)"))
        form.addWidget(self.note_edit, 4, 1)
        rl.addLayout(form)
        row = QHBoxLayout()
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        row.addWidget(self.msg, 1)
        self.btn_save = QPushButton(tr("조정 저장"))
        self.btn_save.clicked.connect(lambda: self.save_current())
        row.addWidget(self.btn_save)
        rl.addLayout(row)
        rl.addStretch(1)
        self.detail = QScrollArea()
        self.detail.setWidgetResizable(True)
        self.detail.setFrameShape(QFrame.NoFrame)
        self.detail.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.detail.setWidget(right)
        self.split.addWidget(self.detail)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 3)
        lay.addWidget(self.split, 1)

        self.revision_label = QLabel("")
        self.revision_label.setWordWrap(True)
        lay.addWidget(self.revision_label)

        # 조정 세션 도구: 출처 판정 대상 만들기(D-13 5) · 두 번째 사람 재라벨 계획(14 4-2 의 9)
        tools = QGridLayout()
        tools.setColumnStretch(0, 1)
        self.btn_origin_targets = QPushButton(tr("출처 판정 대상 만들기"))
        self.btn_origin_targets.setToolTip(tr("잠긴 1차 세션들의 표본 채팅으로 출처 판정 대상 파일(origin\\OT-…)을 만듭니다"))
        self.btn_origin_targets.clicked.connect(lambda: self.make_origin_targets())
        tools.addWidget(self.btn_origin_targets, 0, 0, 1, 3, Qt.AlignLeft)
        self.second_title = QLabel(tr("두 번째 사람 재라벨 계획 (잠긴 1차 · 라벨러 ID)"))
        self.second_title.setWordWrap(True)
        tools.addWidget(self.second_title, 1, 0, 1, 3)
        self.second_src = _shrinkable(QComboBox(), 12)
        tools.addWidget(self.second_src, 2, 0)
        self.second_labeler = QLineEdit()
        self.second_labeler.setMaxLength(8)
        self.second_labeler.setPlaceholderText(tr("예: L02"))
        self.second_labeler.setMaximumWidth(90)
        tools.addWidget(self.second_labeler, 2, 1)
        self.btn_second_plan = QPushButton(tr("계획 만들기"))
        self.btn_second_plan.clicked.connect(lambda: self.make_second_plan())
        tools.addWidget(self.btn_second_plan, 2, 2)
        lay.addLayout(tools)
        self.tool_msg = QLabel("")
        self.tool_msg.setWordWrap(True)
        lay.addWidget(self.tool_msg)
        self._set_editable(False)

    def resizeEvent(self, ev) -> None:          # noqa: N802 — Qt 이름
        super().resizeEvent(ev)
        self._apply_orientation(self.width())

    def _apply_orientation(self, width: int) -> None:
        want = Qt.Horizontal if width >= NARROW_PX else Qt.Vertical
        if self.split.orientation() != want:
            self.split.setOrientation(want)

    # ------------------------------------------------------------------ 세션 고르기·불러오기
    def _writable(self) -> bool:
        return not getattr(self.store, "read_only", False)

    def _locked_sessions(self) -> list[str]:
        try:
            return [s for s in self.store.readable_locked_sessions()
                    if vis.session_kind_of_id(s) in (vis.PRIMARY, vis.RELABEL_SAME, vis.RELABEL_SECOND)]
        except OSError:
            return []

    def refresh_sessions(self) -> None:
        self.session_list.clear()
        sids = self._locked_sessions()
        for s in sids:
            it = QListWidgetItem(s)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.session_list.addItem(it)
        self.btn_load.setEnabled(len(sids) >= 2)
        if len(sids) < 2:
            self.summary_label.setText(tr("잠긴 세션이 둘 이상 있어야 조정할 수 있습니다 (지금 {0}개).").format(len(sids)))

    def checked_sessions(self) -> list[str]:
        return [self.session_list.item(i).text() for i in range(self.session_list.count())
                if self.session_list.item(i).checkState() == Qt.Checked]

    def _side(self, sid: str) -> dict:
        view, msgs = self.store.locked_session_view(sid)
        if msgs:
            LOG.info("조정: %s 읽기 경고 %d건", sid, len(msgs))
        labeler = sid.split("-", 1)[0]            # 세션 ID 의 앞 칸이 labeler_id (03 13-1)
        return {"session_id": sid, "labeler_id": labeler, "scope": _scope_ranges(view),
                "segments": view.items(kind="segment", session_id=sid),
                "reactions": view.items(kind="reaction", session_id=sid)}

    def _chat_times(self, sides) -> dict[str, int] | None:
        if self.chat_info is None:
            return None
        out = {}
        for s in sides:
            for r in s["reactions"]:
                cid = (r.get("body") or {}).get("chat_id")
                if isinstance(cid, str) and cid not in out:
                    t = (self.chat_info(cid) or (None, None))[0]
                    if isinstance(t, int):
                        out[cid] = t
        return out

    def load_cases(self, session_ids: list[str] | None = None) -> int:
        """고른 잠긴 세션들(둘 이상)의 짝마다 불일치를 모아 목록을 채운다. 건수를 돌려준다."""
        sids = list(session_ids) if session_ids is not None else self.checked_sessions()
        if len(sids) < 2:
            self.summary_label.setText(tr("세션을 둘 이상 고르세요."))
            return 0
        data = getattr(getattr(self.store, "project", None), "data", None)
        buf = buffer_ms_of(data)
        sides = {s: self._side(s) for s in sids}
        times = self._chat_times(sides.values())
        lists = []
        for a, b in itertools.combinations(sids, 2):
            res = ag.build_agreement(sides[a], sides[b], settings=_FAST_SETTINGS, buffer_ms=buf, chat_times=times)
            lists.append(adj.build_cases(res, sides[a], sides[b], chat_times=times))
        self.cases = adj.merge_cases(lists)
        self.labelers = sorted({s["labeler_id"] for s in sides.values()})
        self._reload_states()
        hdr = adj.header(self.me, self.labelers)
        self.banner.setText(tr(hdr["banner"]) if hdr["banner"] else tr("조정자 {0} · 라벨러 {1}").format(
            self.me, ", ".join(self.labelers)))
        self._fill_case_list()
        LOG.info("조정: 세션 %d개, 불일치 %d건", len(sids), len(self.cases))
        return len(self.cases)

    def _own_adjudications(self) -> list[dict]:
        view = self.store.effective()
        return view.items(kind=adj.KIND, session_id=self.store.session.session_id)

    def _reload_states(self) -> None:
        own = self._own_adjudications()
        self.states = adj.case_states(self.cases, own)
        self._refresh_bottom(own)

    def _fill_case_list(self) -> None:
        row = self.case_list.currentRow()
        self.case_list.blockSignals(True)
        self.case_list.clear()
        for c in self.cases:
            self.case_list.addItem(case_line(c, self.states.get(c.case_id)))
        self.case_list.blockSignals(False)
        s = adj.summary(self.cases, self.states)
        self.summary_label.setText(tr("불일치 {0}건 · 조정함 {1} · 남음 {2}").format(s["total"], s["decided"], s["pending"])
                                   + (tr(" · 판 바뀜 {0}").format(s["stale"]) if s["stale"] else ""))
        if self.cases:
            self.case_list.setCurrentRow(min(max(row, 0), len(self.cases) - 1))
        else:
            self.select_case(None)

    # ------------------------------------------------------------------ 한 건 보기
    def _on_row(self, row: int) -> None:
        self.select_case(row if 0 <= row < len(self.cases) else None)

    def _clear_grid(self) -> None:
        while self.side_grid.count():
            w = self.side_grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

    def side_texts(self, case: adj.Case) -> list[dict]:
        """나란히 보일 칸(세션마다): 세션·판정·확신도·근거 한 줄·범위. 화면 전용(파일에 쓰지 않는다)."""
        out = []
        for s in case.sides:
            # ' ~ ' 앞뒤 빈칸: 좁은 칸에서 줄을 바꿀 수 있게(한 낱말이면 칸 폭이 그만큼 넓어진다)
            rng = f"{_t(s.start_ms)} ~ {_t(s.end_ms)}" if case.target_kind == adj.TARGET_SEGMENT else ""
            out.append({"session_id": s.session_id, "value": value_ko(case, s.value),
                        "confidence": tr(_CONF_KO.get(s.confidence, s.confidence or "—")),
                        "rationale": s.rationale or "—", "range": rng})
        return out

    def select_case(self, row: int | None) -> None:
        self._clear_grid()
        self.msg.setText("")
        if row is None:
            self.current = None
            self.case_title.setText(tr("불일치 목록에서 한 건을 고르세요."))
            self._set_editable(False)
            return
        c = self.current = self.cases[row]
        if c.target_kind == adj.TARGET_REACTION:
            t, text = (self.chat_info(c.chat_id) if self.chat_info else (None, None)) or (None, None)
            title = tr("표본 판정 · 채팅 {0} · {1}").format(c.chat_id, _t(c.t_ms if c.t_ms is not None else t))
            if text:
                title += "\n" + text
        else:
            spans = ", ".join(f"{_t(a)} ~ {_t(b)}" for a, b in c.spans)
            title = tr("활동 구간 · {0}").format(spans)
            if c.delta_ms is not None:
                title += tr(" · 경계 차이 {0:.1f}초").format(c.delta_ms / 1000)
        self.case_title.setText(title)
        heads = (tr("세션"), tr("판정"), tr("확신도"), tr("근거"))
        for j, h in enumerate(heads):
            self.side_grid.addWidget(QLabel(f"<b>{h}</b>"), j, 0)
        for i, st in enumerate(self.side_texts(c), start=1):
            vals = (st["session_id"], st["value"] + (f"  ({st['range']})" if st["range"] else ""),
                    st["confidence"], st["rationale"])
            for j, v in enumerate(vals):
                # 세션 ID 는 '-' 뒤에서 줄을 바꿀 수 있게(보이는 글자에만 폭 없는 빈칸) — 세 세션이 471px 칸에 들어가게
                lab = QLabel(v.replace("-", "-​") if j == 0 else v)
                lab.setWordWrap(True)
                lab.setTextFormat(Qt.PlainText)          # 근거 글이 HTML 로 해석되지 않게
                self.side_grid.addWidget(lab, j, i)
            self.side_grid.setColumnStretch(i, 1)
        self._fill_decisions(c)
        self._load_existing(c)
        self._set_editable(self._writable())

    def _fill_decisions(self, c: adj.Case) -> None:
        self.decision_box.blockSignals(True)
        self.decision_box.clear()
        for s in c.sides:
            if s.has_label:
                self.decision_box.addItem(tr("최종값 = {0} 쪽 ({1})").format(s.session_id, value_ko(c, s.value)),
                                          f"{_PICK}:{s.session_id}")
        if c.target_kind == adj.TARGET_SEGMENT:
            self.decision_box.addItem(tr("최종값 = 새 값 (분류·범위 고침)"), _NEW)
        self.decision_box.addItem(tr(adj.DECISION_KO[adj.D_EXCLUDE]), adj.D_EXCLUDE)
        self.decision_box.addItem(tr(adj.DECISION_KO[adj.D_AMBIGUOUS]), adj.D_AMBIGUOUS)
        self.decision_box.blockSignals(False)
        self.new_base.clear()
        for s in c.sides:
            if s.body is not None:
                self.new_base.addItem(s.session_id, s.session_id)
        self._refresh_new_value()

    def _load_existing(self, c: adj.Case) -> None:
        st = self.states.get(c.case_id)
        rec = st.record if st else None
        self.cause_box.setCurrentIndex(0)
        self.tag_edit.setText("")
        self.note_edit.setText("")
        if rec is None:
            return
        b = rec.get("body") or {}
        i = self.cause_box.findData(b.get("cause"))
        self.cause_box.setCurrentIndex(max(i, 0))
        self.tag_edit.setText(b.get("pattern_tag") or "")
        self.note_edit.setText(b.get("note") or "")
        d = b.get("decision")
        want = None
        if d == adj.D_FINAL:
            fin = b.get("final") or {}
            for s in c.sides:
                if s.body is not None and s.body == fin:
                    want = f"{_PICK}:{s.session_id}"
            if want is None and c.target_kind == adj.TARGET_SEGMENT:
                want = _NEW
        elif d in (adj.D_EXCLUDE, adj.D_AMBIGUOUS):
            want = d
        if want is not None:
            self.choose_decision(want)
        if st.targets_changed:
            self.msg.setText(tr("조정 뒤 비교하는 세션이 바뀌었습니다(세션이 늘거나 줄었음). 다시 보고 저장하세요."))
        elif st.stale:
            self.msg.setText(tr("조정 뒤 대상 판이 바뀌었습니다. 다시 보고 저장하세요."))

    def choose_decision(self, key: str) -> bool:
        """결정 칸을 key('pick:<session_id>'·'new'·'exclude_from_eval'·'keep_as_ambiguous')로 고른다. 없으면 False."""
        i = self.decision_box.findData(key)
        if i < 0:
            return False
        self.decision_box.setCurrentIndex(i)
        return True

    def _refresh_new_value(self) -> None:
        what, _ = _split_key(self.decision_box.currentData())
        on = what == _NEW and self.current is not None
        self.new_row.setVisible(on)
        if on:
            s = self.current.side(self.new_base.currentData()) or self.current.sides[0]
            i = self.new_cat.findData(s.value)
            self.new_cat.setCurrentIndex(max(i, 0))
            self.new_start.setValue((s.start_ms or 0) // 1000)
            self.new_end.setValue((s.end_ms or 0) // 1000)

    def _set_editable(self, on: bool) -> None:
        for w in (self.decision_box, self.cause_box, self.tag_edit, self.note_edit, self.btn_save, self.new_row):
            w.setEnabled(on)

    # ------------------------------------------------------------------ 저장
    def build_body(self) -> dict:
        """지금 칸의 값 → adjudicationBody. 틀리면 AdjudicationError(메시지는 화면에 보인다)."""
        c = self.current
        if c is None:
            raise adj.AdjudicationError("고른 불일치가 없습니다")
        what, sid = _split_key(self.decision_box.currentData())
        if what is None:
            raise adj.AdjudicationError("결정을 고르세요")
        kw = {"cause": self.cause_box.currentData(), "pattern_tag": self.tag_edit.text(), "note": self.note_edit.text()}
        if kw["cause"] is None:
            raise adj.AdjudicationError("불일치 원인을 고르세요")
        if what == _PICK:
            return adj.make_body(c, decision=adj.D_FINAL, pick=sid, **kw)
        if what == _NEW:
            fin = adj.segment_final(c, self.new_base.currentData(), category=self.new_cat.currentData(),
                                    start_ms=self.new_start.value() * 1000, end_ms=self.new_end.value() * 1000)
            return adj.make_body(c, decision=adj.D_FINAL, final=fin, **kw)
        return adj.make_body(c, decision=what, **kw)

    def save_current(self) -> dict | None:
        """고른 건의 조정을 저장한다. 저장된 레코드(사본) 또는 None(막힘, msg 에 이유)."""
        self.last_error = None
        if not self._writable():
            self.last_error = tr("읽기 전용이라 저장하지 않았습니다.")
            self.msg.setText(self.last_error)
            return None
        try:
            body = self.build_body()
            st = self.states.get(self.current.case_id)
            rec = self.store.append_label(adj.make_draft(self.current, body, st.record if st else None))
        except Exception as e:            # 형식·판 연결·읽기 전용 거부는 화면에 이유를 보인다
            self.last_error = str(e)
            self.msg.setText(self.last_error)
            LOG.info("조정 저장 거부: %s", type(e).__name__)
            return None
        LOG.info("조정 저장: %s", (body or {}).get("decision"))
        self._reload_states()
        self._fill_case_list()
        self.msg.setText(tr("저장했습니다."))
        return rec

    # ------------------------------------------------------------------ 아래: 개정 후보·도구
    def _refresh_bottom(self, own: list[dict] | None = None) -> None:
        try:
            own = own if own is not None else self._own_adjudications()
        except Exception:                 # 화면을 막지 않는다
            own = []
        cands = adj.revision_candidates(own)
        if cands:
            self.revision_label.setText(tr("기준서 개정 후보 (같은 유형 태그 {0}건 이상): ").format(adj.REVISION_MIN)
                                        + ", ".join(f"{c['pattern_tag']} ×{c['count']}" for c in cands))
        else:
            self.revision_label.setText(tr("기준서 개정 후보 없음 (같은 유형 태그 {0}건 이상일 때 보임)").format(adj.REVISION_MIN))
        comp = QCompleter(adj.known_tags(own), self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.tag_edit.setCompleter(comp)

    def _locked_primaries(self) -> list[str]:
        return [s for s in self._locked_sessions() if vis.session_kind_of_id(s) == vis.PRIMARY]

    def refresh_plans(self) -> None:
        """도구 칸을 지금 상태로 다시 채운다(계획을 만들거나 취소한 뒤 통합이 부른다, 옛 이름 그대로).

        재라벨 계획 취소는 이 화면에서 하지 않는다(메뉴 대화상자만, 14 4-2 의 1)."""
        prims = self._locked_primaries()
        cur = self.second_src.currentData()
        self.second_src.clear()
        for s in prims:
            self.second_src.addItem(s, s)
        if cur is not None:
            self.second_src.setCurrentIndex(max(self.second_src.findData(cur), 0))
        w = self._writable()
        for x in (self.second_src, self.second_labeler, self.btn_second_plan):
            x.setEnabled(w and bool(prims))
        self.btn_origin_targets.setEnabled(w and bool(prims) and self.write_origin_targets is not None)
        if not w:
            self.btn_origin_targets.setToolTip(tr("읽기 전용 세션입니다"))
        elif self.write_origin_targets is None:
            self.btn_origin_targets.setToolTip(tr("대상 파일을 쓸 곳이 연결되지 않았습니다"))
        elif not prims:
            self.btn_origin_targets.setToolTip(tr("잠긴 1차 세션이 없습니다"))

    def _now_iso(self) -> str:
        t = self._now() if self._now else datetime.now().astimezone()
        if t.tzinfo is None:
            t = t.astimezone()
        return t.isoformat(timespec="seconds")

    def _existing_targets(self) -> list[og.OriginTargets]:
        if self.list_origin_targets is not None:
            return list(self.list_origin_targets())
        from .origin_view import list_targets
        return list_targets(os.path.join(self.store.root, "origin"))

    def make_origin_targets(self) -> dict | None:
        """잠긴 1차 세션들의 표본 매니페스트로 출처 판정 대상 파일을 만든다(D-13 5). 쓴 대상 dict, 막히면 None(tool_msg)."""
        if not self._writable():
            self.tool_msg.setText(tr("읽기 전용이라 만들지 않았습니다."))
            return None
        if self.write_origin_targets is None:
            self.tool_msg.setText(tr("대상 파일을 쓸 곳이 연결되지 않았습니다."))
            return None
        prims = self._locked_primaries()
        if not prims:
            self.tool_msg.setText(tr("잠긴 1차 세션이 없어 대상을 만들 수 없습니다."))
            return None
        kind = self.store.session.session_kind
        try:
            manifests: list[dict] = []
            for sid in prims:
                manifests += [m for m, _sha in relabel_io.load_source_manifests(self.store.root, sid,
                                                                                 creator_kind=kind)]
            obj, pick = og.build_sample_targets(manifests, created_at=self._now_iso(), created_by=self.me,
                                                session_ids=prims)
            same = og.same_target_exists(self._existing_targets(), obj)
            if same is not None:
                self.tool_msg.setText(tr("같은 채팅 목록의 대상 파일 {0} 이 이미 있습니다. 새로 만들지 않았습니다.").format(same))
                return None
            self.write_origin_targets(obj)
        except FileExistsError:
            self.tool_msg.setText(tr("같은 ID 의 대상 파일이 이미 있습니다(대상 파일은 고치지 않습니다)."))
            return None
        except (OSError, ValueError, RuntimeError) as e:   # OriginError·RelabelBlindError·store 의 읽기 전용 거부 포함
            self.tool_msg.setText(tr("대상을 만들지 못했습니다: {0}").format(str(e)[:300]))
            LOG.info("출처 판정 대상 만들기 거부: %s", type(e).__name__)
            return None
        LOG.info("출처 판정 대상 %s: 채팅 %d건, 매니페스트 %d개, 세션 %d개", obj["target_id"], len(pick.chat_ids),
                 len(pick.manifests), len(pick.sessions))
        self.tool_msg.setText(tr("출처 판정 대상 {0} 를 만들었습니다 (채팅 {1}건 · 1차 표본 {2}개 · 세션 {3}개).").format(
            obj["target_id"], len(pick.chat_ids), len(pick.manifests), len(pick.sessions)))
        return obj

    def make_second_plan(self, source_session_id: str | None = None, labeler_id: str | None = None, *,
                         now: datetime | None = None, rand4: str | None = None) -> dict | None:
        """고른 잠긴 1차 세션과 라벨러 ID 로 relabel_second 계획을 만든다(14 4-2 의 9). 쓴 계획, 막히면 None(tool_msg)."""
        sid = source_session_id or self.second_src.currentData()
        lab = (labeler_id if labeler_id is not None else self.second_labeler.text()).strip().upper()
        if not self._writable():
            self.tool_msg.setText(tr("읽기 전용이라 만들지 않았습니다."))
            return None
        if not sid:
            self.tool_msg.setText(tr("잠긴 1차 세션을 고르세요."))
            return None
        if not lab:
            self.tool_msg.setText(tr("두 번째 사람의 라벨러 ID 를 적으세요."))
            return None
        s = self.store.session
        try:
            cov = self.coverage() if self.coverage else None
            when = now or (self._now() if self._now else None)
            if self.create_second_plan_cb is not None:
                plan = self.create_second_plan_cb(sid, lab, coverage=cov, app_version=self.app_version or None,
                                                  now=when, rand4=rand4)
            else:
                view, _msgs = self.store.locked_session_view(sid)
                plan = relabel_io.create_second_plan(
                    self.store.root, self.store.project.data, source_view=view, source_session_id=sid,
                    labeler_id=lab, created_by=s.labeler_id, created_machine4=s.machine4,
                    app_version=self.app_version, creator_kind=s.session_kind, coverage=cov, now=when, rand4=rand4)
        except (OSError, ValueError, RuntimeError) as e:   # RelabelBlindError(PermissionError)·RelabelPlanError·store 거부
            self.tool_msg.setText(tr("계획을 만들지 못했습니다: {0}").format(str(e)[:300]))
            LOG.info("두 번째 사람 재라벨 계획 거부: %s", type(e).__name__)
            return None
        LOG.info("재라벨 계획 %s: relabel_second 블록 %d개", plan["plan_id"], len(plan["selected"]))
        self.tool_msg.setText(tr("두 번째 사람 재라벨 계획 {0} 를 만들었습니다 (블록 {1}개, 라벨러 {2}).").format(
            plan["plan_id"], len(plan["selected"]), lab))
        self.second_labeler.setText("")
        self.refresh_plans()
        return plan


class AdjudicationDialog(QDialog):
    """메뉴에서 여는 창 하나짜리 껍데기."""

    def __init__(self, parent=None, *, store, app_version: str = "", chat_info=None, **kw):
        super().__init__(parent)
        self.setWindowTitle(tr("조정"))
        self.resize(1100, 700)
        lay = QVBoxLayout(self)
        self.view = AdjudicationView(self, store=store, app_version=app_version, chat_info=chat_info, **kw)
        lay.addWidget(self.view)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton(tr("닫기"))
        b.clicked.connect(self.accept)
        row.addWidget(b)
        lay.addLayout(row)
