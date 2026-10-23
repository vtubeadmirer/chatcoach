"""가상 채팅 가려내기 화면 — H-5 품질 관문 (20 7장, 25 6-2, 03 6-19, 08 D-24 7 가, D-05, D-16 클릭 한 번).

메뉴 '훈련 → 가상 채팅 가려내기' 한 번이면 바로: 시나리오 구간을 **소리 켜고** 튼다 · 채팅이 흐른다 · '가'·'생'·
'가져옴·생성' 표시를 **숨긴다** · 맨 위에 '이 화면에는 생성 채팅이 섞여 있습니다' 한 줄(늘 보임, 20 11장 1번 — D-05 의
'사람을 속이지 않음'). 고르는 창·입력 칸은 없다.

| 동작 | 결과 | 클릭 |
|---|---|---|
| 메뉴 '가상 채팅 가려내기' | 위처럼 열림 | 1 |
| 채팅 옆 '진짜' · '가짜' · '모름' | 그 채팅의 판정. 다른 단추를 누르면 바뀜, 같은 단추를 다시 누르면 지움('안 봄') | 채팅마다 1 |
| 구간 끝 (또는 [끝내고 정답 보기] · Ctrl+Enter) | 줄마다 정답 표시('✓ 생성 · 가짜' 꼴)와 숫자 줄(탐지율·κ·혼동표·표본 수·관문) | 0 (또는 1) |
| Space | 멈춤·재생 | — |

- 단추 셋은 채팅 표의 '종류' 칸에 그린다(JudgeDelegate — 그 칸의 왼쪽·가운데·오른쪽 1/3 이 진짜·가짜·모름).
  누른 판정은 그 줄을 초록으로 칠하고 칸에 판정 이름을 보인다(ChatPanel.set_label_marks).
- 대상: 구간에 보인 시청자의 일반 채팅(kind = text, role = viewer) 전부 — 실제 + 시나리오 줄. 사람이 고르지 않는다
  (20 7-1). 후원·시스템·스트리머 채팅은 보이기만 하고 단추가 없다(누가 봐도 실제라서, 설계와 다르게 한 곳).
- 이름 모양으로 가려지지 않게: 가상 줄은 시나리오의 가림 이름(core.training.split_mixed)을 쓰고, 판정 중에는 표의
  '시청자 번호' 칸과 타임라인의 채팅 밀도를 숨긴다(가상 줄은 줄마다 새 번호라 번호로 드러나고, 밀도는 실제 채팅만
  세어 가상 줄이 채운 자리가 비어 보인다).
- 끝: 계산은 app.core.discern.evaluate(→ origin.compare_origins) 한 번. 정답을 보이기 **전에** 기록 끝 줄을 쓴다 —
  못 쓰면 정답을 보이지 않는다(03 6-19 '먼저 쓰고 보인다'). 정답을 보인 뒤에는 표시를 다시 붙여 채팅을 다시 싣는다.
  화면은 세션(방송) 하나만 세므로(broadcasts=1) 관문은 늘 '표본 적음' 이다. 방송 2편 이상을 합친 통과·실패는
  app.discern_report(tools/discern_report.py)가 기록에서 다시 만들어 낸다(끝 화면에 한 줄로 알린다).
- 기록(log = app.discern_store.DiscernLog): start · judge · end 를 그대로 부른다. 쓰기 실패면 그 누름은 반영하지 않는다.
  없으면 파일에 쓰지 않는다. 어느 쪽이든 discernEvent(dict) 를 내고 events 에 쌓는다. 칸은 chat_id·scenario_id·line_id·
  판정·VOD 시각·숫자뿐이다 — 본문·닉네임·정답(출처)은 넣지 않는다(D-17 8).
- 노출로 세지 않는다: 원래 반응·사람 라벨·엔진 판정을 보이지 않는다(training\\ 에 쓰지 않음).

메뉴 켜기(discern_menu_state): 시범 프로젝트(pilot-*)이고 잠기지 않았으면 → 라벨링 세션(1차·재라벨·조정·출처 판정)이면 →
영상이 없으면 → 시나리오(가상 줄 1개 이상)가 없으면 끈다. 앞 둘은 scenarios\\ 를 **열기 전에** 본다(라벨링 세션은 0번 엶,
20 1장 4번). prepare_discern 이 같은 순서로 보고, 된 뒤에만 시나리오·채팅을 읽어 start 인자를 만든다.

부품·주입: trainer_view 와 같은 모양 — DiscernView(parent, parts={"player", "chat", "timeline"}, clock=…, mix_fn=…,
evaluate_fn=…). 부품은 작업대와 같은 MpvPlayer·ChatPanel·Timeline 을 import 해 쓴다(trainer_view._default_part).
플레이어는 처음부터 이 화면 안의 분할기를 부모로 만들고 옮기지 않는다(10 5-3 의 8). 섞기는 trainer_view 와 같은 길
(_default_mix → core.training.split_mixed → trainer_view._clip_mixed)이고 표시만 붙이지 않는다(show_virtual_marks=False).

시각: 플레이어는 영상 파일 시각, 채팅·타임라인·기록은 VOD 시각. 변환은 timemap(to_file·to_vod). 로그에는 건수·시각만.
"""
from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPalette, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton, QSplitter, QStackedWidget, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget)

from ..core import discern as dc
from ..i18n import tr
from . import trainer_view as _tv

LOG = logging.getLogger(__name__)

LABELING_KINDS = frozenset({"primary", "relabel_same", "relabel_second", "adjudication", "origin_review"})

BANNER_GENERATED = "이 화면에는 생성 채팅이 섞여 있습니다"
BANNER_RETRIEVED = "이 화면에는 다른 구간에서 가져온 채팅이 섞여 있습니다"
BANNER_RESULT = "정답을 보입니다 — 가상 채팅에 '가'·'생' 표시를 다시 붙였습니다"
HINT_START = "소리를 켜고 재생합니다. 채팅마다 '진짜' · '가짜' · '모름' 을 한 번 누르세요 (다시 누르면 지움)"
JUDGE_TIP = "이 채팅의 판정 — 다른 단추를 누르면 바뀌고, 같은 단추를 다시 누르면 지웁니다('안 봄')"
RESULT_TIP = "정답 · 내 판정 (✓ 맞힘 · ✗ 틀림 · ? 모르겠음 · · 안 봄)"

REASON_PILOT = _tv.REASON_PILOT
REASON_LABELING = "라벨링 세션에서는 가상 채팅 가려내기를 열지 않습니다"
REASON_NO_MEDIA = "영상이 연결되지 않아 가려내기를 열 수 없습니다"
REASON_NO_SCENARIO = "가려낼 가상 채팅이 없습니다"
REASON_NO_CHAT = "실제 채팅이 없어 가려내기를 열 수 없습니다"
REASON_NO_TARGETS = "구간에 가려낼 채팅이 없습니다"

JUDGE_LABELS = ((dc.REAL, "진짜"), (dc.VIRTUAL, "가짜"), (dc.UNSURE, "모름"))
JUDGE_COL_W = 132                 # '종류' 칸을 단추 셋 폭으로
COL_VIEWER = 1                    # chat_panel.COL_VIEWER
COL_KIND = 4                      # chat_panel.COL_KIND
JUDGE_COLORS = {dc.REAL: QColor("#2e7d32"), dc.VIRTUAL: QColor("#c62828"), dc.UNSURE: QColor("#6d6d6d")}


# ---------------------------------------------------------------------------- 메뉴 켜기 (순수)
def discern_menu_state(project_name: str | None, *, session_kind: str | None = None, has_media: bool = True,
                       has_scenario: bool = True, pilot_locked: bool = False) -> tuple[bool, str]:
    """'훈련 → 가상 채팅 가려내기' 메뉴를 켤까. (켜짐, 꺼졌으면 까닭 한 줄).

    순서가 뜻이다: 시범(잠기기 전) → 라벨링 세션 → 영상 → 시나리오. 앞 둘에 걸리면 부르는 쪽은 has_scenario 를 계산하지
    않아도 된다(scenarios\\ 를 열지 않는다)."""
    if _tv.is_pilot_project(project_name) and not pilot_locked:
        return False, tr(REASON_PILOT)
    if session_kind in LABELING_KINDS:
        return False, tr(REASON_LABELING)
    if not has_media:
        return False, tr(REASON_NO_MEDIA)
    if not has_scenario:
        return False, tr(REASON_NO_SCENARIO)
    return True, ""


@dataclass
class DiscernPrep:
    """prepare_discern 결과. ok 면 kwargs 를 DiscernView.start(**kwargs, log=…, media_path=…, …) 에 넘긴다."""
    ok: bool
    reason: str = ""
    kwargs: dict = field(default_factory=dict)


def prepare_discern(project_root: str, project_id: str | None, *, labeler_id: str | None = None,
                    session_kind: str | None = None, has_media: bool = True,
                    pilot_locked: bool = False) -> DiscernPrep:
    """메뉴 한 번의 앞 절반: 막힘 확인(scenarios\\ 를 열기 전) → 가장 새 시나리오 → 실제 채팅 → 본 적 있음·다시 봄.

    scenario_mix 와 같은 길(시범이면 scenario_store 가 scenarios\\ 를 열지 않음, 다른 프로젝트의 pilot-* 는 열지 않음).
    labeler_id 를 주면 discern_store.watched·prior 로 seen_before·repeat 를 계산한다(없으면 None·False)."""
    name = os.path.basename(os.path.normpath(str(project_root)))
    ok, why = discern_menu_state(name, session_kind=session_kind, has_media=has_media, has_scenario=True,
                                 pilot_locked=pilot_locked)
    if not ok:
        return DiscernPrep(False, why)
    from .. import scenario_mix
    scn, _counts = scenario_mix.find_scenario(project_root, None)
    if scn is None or scenario_mix.virtual_count(scn) <= 0:
        return DiscernPrep(False, tr(REASON_NO_SCENARIO))
    try:
        real = scenario_mix.load_messages(project_root)
    except OSError as e:
        LOG.warning("가려내기: 채팅 읽기 실패 %s", type(e).__name__)
        real = []
    if not real:
        return DiscernPrep(False, tr(REASON_NO_CHAT))
    scenario = {"records": scn.records, "scenario_id": scn.scenario_id, "scenario_sha256": scn.sha256,
                "resolve": scenario_mix.make_resolver(project_root, project_id, real)}
    seen, repeat = None, False
    if labeler_id:
        from .. import discern_store as ds
        try:
            seen = dc.seen_before(scn.range_ms, ds.watched(project_root, labeler_id))["seen"]
            repeat = dc.is_repeat(scn.scenario_id, ds.prior(project_root, labeler_id))
        except (OSError, ValueError, RuntimeError) as e:
            LOG.warning("가려내기: 본 적 있음 계산 실패 %s", type(e).__name__)
            seen = None
    return DiscernPrep(True, "", {"chat": real, "scenario": scenario, "segment": scn.range_ms, "seen_before": seen,
                                  "repeat": repeat, "project_name": name, "project_id": project_id,
                                  "session_kind": session_kind, "pilot_locked": pilot_locked})


# ---------------------------------------------------------------------------- 숫자 줄 (순수)
def result_lines(res: dc.DiscernResult) -> list[str]:
    """끝 화면의 숫자 줄(글자만, 본문·키 없음)."""
    out = []
    if res.repeat:
        out.append(tr("다시 봄 — 같은 시나리오를 이미 가려냈습니다. 숫자는 내지만 대표값에서 뺍니다"))
    if res.seen_before:
        out.append(tr("이 구간을 본 적 있음 — 실제 채팅을 기억해 탐지율이 높게 나올 수 있습니다"))
    out.append(tr("가짜 탐지율 {0} · κ {1} · 진짜 오인율 {2} · 가져옴 오인율 {3} · 모르겠음 {4}").format(
        dc.fmt_rate(res.detection), dc.fmt_kappa(res.kappa), dc.fmt_rate(res.false_alarm),
        dc.fmt_rate(res.retrieved), dc.fmt_rate(res.unsure)))
    s = res.sample
    g = res.gate
    out.append(tr("판정 {0} · 안 봄 {1} · 표본 생성 {2}줄 · 실제 {3}줄 · 방송 {4}편 — 관문: {5} "
                  "(생성 {6}·실제 {7}·방송 {8} 이상에서 탐지율 ≤ {9} 이고 κ ≤ {10})").format(
        res.judged, res.unseen, s["generated"], s["real"], s["broadcasts"], tr(dc.GATE_KO[g["status"]]),
        dc.GATE_MIN_GENERATED, dc.GATE_MIN_REAL, dc.GATE_MIN_BROADCASTS, dc.GATE_MAX_DETECTION, dc.GATE_MAX_KAPPA))
    if s["broadcasts"] < dc.GATE_MIN_BROADCASTS:
        # 화면은 세션(방송) 하나라 관문이 늘 '표본 적음' 이다 — 통과·실패는 기록을 합쳐 낸다(app.discern_report)
        out.append(tr("관문 통과·실패는 방송 {0}편 이상의 가려내기 기록을 합쳐 따로 냅니다 — 이 화면은 이번 세션의 숫자입니다")
                   .format(dc.GATE_MIN_BROADCASTS))
    return out


def confusion_html(res: dc.DiscernResult) -> str:
    """혼동표(행 = 정답 실제·생성, 열 = 내 판정 진짜·가짜·모르겠음). 정답 쪽 '판별 불가' 행은 늘 0 이라 뺀다."""
    rows = res.confusion["rows"]
    head = "".join(f"<th>{html.escape(tr(x))}</th>" for x in ("진짜", "가짜", "모르겠음"))
    body = ""
    for i, name in ((0, "정답 실제"), (1, "정답 생성")):
        cells = "".join(f"<td align='right'>{int(v)}</td>" for v in rows[i])
        body += f"<tr><th align='left'>{html.escape(tr(name))}</th>{cells}</tr>"
    return (f"<table cellspacing='0' cellpadding='3' border='1'><tr><th></th>{head}</tr>{body}</table>")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ident(ms: int) -> int:
    return int(ms)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _scenario_header(scenario) -> Mapping | None:
    records, _ = _tv._scenario_parts(scenario)
    if records and isinstance(records[0], Mapping) and records[0].get("kind") == "scenario.header":
        return records[0]
    return None


def _scenario_range(scenario) -> tuple[int, int] | None:
    h = _scenario_header(scenario)
    rng = (h or {}).get("range")
    if not isinstance(rng, Mapping):
        return None
    a, b = rng.get("vod_from_ms"), rng.get("vod_to_ms")
    if _is_int(a) and _is_int(b) and b > a >= 0:
        return a, b
    return None


# ---------------------------------------------------------------------------- 단추 셋 (채팅 표 '종류' 칸)
class JudgeDelegate(QStyledItemDelegate):
    """판정 중 대상 줄의 '종류' 칸에 [진짜 | 가짜 | 모름] 을 그리고, 누른 자리(1/3)로 판정한다. 대상이 아니거나 판정
    중이 아니면 보통 칸처럼 그린다."""

    def __init__(self, owner: "DiscernView"):
        super().__init__(owner)
        self._owner = owner

    @staticmethod
    def part_at(rect: QRect, x: int) -> int | None:
        """칸 안 x → 0·1·2 (진짜·가짜·모름). 칸 밖이면 None."""
        if rect.width() <= 0 or not (rect.left() <= x <= rect.right()):
            return None
        return min(2, max(0, (x - rect.left()) * 3 // rect.width()))

    def paint(self, painter, option, index):
        cid = index.data(Qt.UserRole)
        st = self._owner._cell_state(cid)
        if st is None:
            super().paint(painter, option, index)
            return
        _, chosen = st
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""                     # 칸 바탕(판정한 줄의 초록·선택·재생 위치)만 그리고 글자는 단추로
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        r = option.rect
        painter.save()
        w = r.width() / 3.0
        pen_fg = option.palette.color(QPalette.Text)
        for i, (ans, label) in enumerate(JUDGE_LABELS):
            cell = QRect(int(r.left() + i * w) + 1, r.top() + 2, int(w) - 2, r.height() - 4)
            if chosen == ans:
                painter.fillRect(cell, JUDGE_COLORS[ans])
                painter.setPen(QPen(QColor("#ffffff")))
            else:
                painter.setPen(QPen(QColor("#7d848c")))
                painter.drawRect(cell.adjusted(0, 0, -1, -1))
                painter.setPen(QPen(pen_fg))
            painter.drawText(cell, Qt.AlignCenter, tr(label))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            cid = index.data(Qt.UserRole)
            if self._owner._cell_state(cid) is not None:
                part = self.part_at(option.rect, int(event.position().x()))
                if part is not None:
                    self._owner.judge(cid, JUDGE_LABELS[part][0])
                    return True
        return super().editorEvent(event, model, option, index)


# ---------------------------------------------------------------------------- 화면
class DiscernView(QWidget):
    """가상 채팅 가려내기. start() 로 시작한다."""

    discernEvent = Signal(dict)         # 기록 한 줄 {"kind", …body 칸}. log 를 주면 이미 쓴 뒤에 낸다
    judgedCountChanged = Signal(int)
    resultShown = Signal(dict)          # DiscernResult.numbers() + seen_before·repeat
    messageShown = Signal(str)
    closeRequested = Signal()

    def __init__(self, parent=None, *, parts: dict | None = None, clock: Callable[[], str] | None = None,
                 mix_fn: Callable[..., object] | None = None,
                 evaluate_fn: Callable[..., dc.DiscernResult] | None = None):
        super().__init__(parent)
        self._mix_fn = mix_fn or _tv._default_mix
        self._evaluate = evaluate_fn or dc.evaluate
        self._clock = clock or _now_iso
        self.events: list[dict] = []
        self._targets: dict[str, dc.Target] = {}    # 화면 id(chat_id · 'v:'+line_id) → 대상
        self._answers: dict[str, str] = {}          # 짝 키 → 판정
        self._rows: list[dict] = []                 # 판정 중 화면 줄(표시 없음)
        self._vlines: dict = {}
        self._viewer_no: dict | None = None
        self._segment: tuple[int, int] | None = None
        self._scenario_id: str | None = None
        self._seen_before: bool | None = None
        self._repeat = False
        self._to_file: Callable[[int], int] = _ident
        self._to_vod: Callable[[int], int] = _ident
        self._vod_pos = 0
        self._has_media = False
        self._autoplay = False
        self._state = "idle"                        # idle · judging · result · closed
        self._started = False
        self._muted = True
        self._log = None
        self._result: dc.DiscernResult | None = None
        self.setFocusPolicy(Qt.StrongFocus)

        parts = parts or {}
        self.chat = (parts.get("chat") or _tv._default_part("chat"))()
        self.timeline = (parts.get("timeline") or _tv._default_part("timeline"))()

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 2)
        top = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setObjectName("panelTitle")
        top.addWidget(self.count_label)
        self.msg = QLabel("")
        self.msg.setObjectName("hint")
        self.msg.setWordWrap(True)
        top.addWidget(self.msg, 1)
        self.btn_play = QPushButton(tr("▶ 재생"))
        self.btn_play.setToolTip(tr("재생 / 일시정지 (Space)"))
        self.btn_play.setFocusPolicy(Qt.NoFocus)
        self.btn_play.clicked.connect(self._on_play_btn)
        top.addWidget(self.btn_play)
        self.btn_finish = QPushButton(tr("끝내고 정답 보기"))
        self.btn_finish.setToolTip(tr("판정을 끝내고 정답과 숫자를 봅니다 (Ctrl+Enter) — 구간 끝에서도 저절로 넘어갑니다"))
        self.btn_finish.setFocusPolicy(Qt.NoFocus)
        self.btn_finish.clicked.connect(self._on_finish_btn)
        top.addWidget(self.btn_finish)
        self.btn_close = QPushButton(tr("닫기"))
        self.btn_close.setToolTip(tr("가려내기를 끝내고 작업대로 돌아갑니다 (기록은 남습니다)"))
        self.btn_close.setFocusPolicy(Qt.NoFocus)
        self.btn_close.clicked.connect(self._on_close_btn)
        top.addWidget(self.btn_close)
        root.addLayout(top)

        # 늘 보이는 한 줄 (20 11장 1번)
        self.banner = QLabel("")
        self.banner.setObjectName("panelTitle")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("padding: 3px 6px; background: #3b3420; color: #f0d890;")
        root.addWidget(self.banner)

        self.stack = QStackedWidget(self)
        self.blocked_label = QLabel("")
        self.blocked_label.setAlignment(Qt.AlignCenter)
        self.blocked_label.setWordWrap(True)
        self.stack.addWidget(self.blocked_label)

        body = QWidget(self)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        self.hsplit = QSplitter(Qt.Horizontal, body)
        self.vsplit = QSplitter(Qt.Vertical, self.hsplit)
        self.player = (parts.get("player") or _tv._default_part("player"))(self.vsplit)
        self.vsplit.addWidget(self.player)
        self.vsplit.addWidget(self.timeline)
        self.vsplit.setStretchFactor(0, 3)
        self.vsplit.setStretchFactor(1, 1)
        self.hsplit.addWidget(self.vsplit)
        self.right = QWidget()
        rl = QVBoxLayout(self.right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.RichText)
        self.result_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.result_label.setVisible(False)
        rl.addWidget(self.result_label)
        rl.addWidget(self.chat, 1)
        self.hsplit.addWidget(self.right)
        self.hsplit.setStretchFactor(0, 3)
        self.hsplit.setStretchFactor(1, 2)
        bl.addWidget(self.hsplit, 1)
        self.stack.addWidget(body)
        self.page_body = body
        root.addWidget(self.stack, 1)

        # 채팅 표: 검색 칸 숨김(입력 칸 없음, D-16), '종류' 칸에 단추 셋
        search = getattr(self.chat, "search", None)
        if search is not None:
            search.setVisible(False)
        self.delegate = JudgeDelegate(self)
        view = getattr(self.chat, "view", None)
        self._chat_view = view if view is not None and hasattr(view, "setItemDelegateForColumn") else None
        if self._chat_view is not None:
            self._chat_view.setItemDelegateForColumn(COL_KIND, self.delegate)
        if hasattr(self.timeline, "set_fixed_tracks_visible"):
            self.timeline.set_fixed_tracks_visible(segments=False, density=False, waveform=False)
        self.timeline.seekRequested.connect(self._on_timeline_seek)
        if hasattr(self.chat, "chatActivated"):
            self.chat.chatActivated.connect(self._on_chat_activated)     # 끝 화면: 두 번 누르면 그 자리로

        self.player.positionChanged.connect(self._on_player_position)
        self.player.playingChanged.connect(self._on_playing)
        self.player.errorOccurred.connect(self._on_player_error)
        if hasattr(self.player, "fileLoaded"):
            self.player.fileLoaded.connect(self._on_file_loaded)
        if hasattr(self.player, "clicked"):
            self.player.clicked.connect(self._on_play_btn)

        self.shortcuts: dict[str, QShortcut] = {}
        for key, fn in (("Space", self.toggle_play), ("Ctrl+Return", self._on_finish_btn),
                        ("Ctrl+Enter", self._on_finish_btn)):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
            self.shortcuts[key] = s

        self._update_count()
        self.set_blocked(tr("가려낼 구간을 아직 열지 않았습니다"))

    # ------------------------------------------------------------------ 시작
    def set_blocked(self, reason: str) -> None:
        self.blocked_label.setText(reason)
        self.stack.setCurrentWidget(self.blocked_label)
        self.btn_play.setEnabled(False)
        self.btn_finish.setEnabled(False)
        self.banner.setVisible(False)

    def start(self, *, chat, scenario, segment: Sequence[int] | None = None, media_path: str | None = None,
              timemap=None, viewer_no: Mapping[str, int] | None = None, duration_ms: int | None = None,
              project_name: str | None = None, project_id: str | None = None, pilot_locked: bool = False,
              session_kind: str | None = None, log=None, seen_before: bool | None = None,
              repeat: bool = False) -> bool:
        """가려내기를 연다. 막히면 까닭 한 줄을 보이고 False.

        chat: messages.jsonl 줄 목록. scenario: {"records", "resolve", "scenario_id", "scenario_sha256"}(prepare_discern 이
        만든 것) — mix_fn(real, scenario) 에 그대로 넘긴다. segment: 없으면 시나리오 머리줄의 구간.
        log: app.discern_store.DiscernLog (start 줄을 못 쓰면 열지 않는다). seen_before·repeat: 계산값(20 7-3·7-1).
        막힘 판단(시범·라벨링 세션)은 시나리오를 섞기 **전에** 한다."""
        ok, why = discern_menu_state(project_name, session_kind=session_kind, has_media=bool(media_path),
                                     has_scenario=scenario is not None, pilot_locked=pilot_locked)
        if not ok:
            self.set_blocked(why)
            return False
        seg = (int(segment[0]), int(segment[1])) if segment is not None else _scenario_range(scenario)
        if seg is None or seg[1] <= seg[0]:
            self.set_blocked(tr(REASON_NO_SCENARIO))
            return False
        info = _tv._scenario_info(scenario)
        sid = info.get("scenario_id")
        if not isinstance(sid, str) or not sid:
            self.set_blocked(tr(REASON_NO_SCENARIO))
            return False
        mixed = self._mix(chat, scenario, viewer_no, seg)
        if mixed is None:
            return False
        rows, vno, vlines = mixed
        targets = self._build_targets(rows, vlines, sid, seg)
        if not any(t.source != dc.SRC_REPLAY for t in targets.values()):
            self.set_blocked(tr(REASON_NO_SCENARIO))
            return False
        shown = {s: sum(1 for t in targets.values() if t.source == s) for s in dc.SOURCES}
        self._log = log
        if log is not None:
            try:
                log.start(project_id=project_id or "", scenario_id=sid, scenario_sha256=info.get("scenario_sha256") or "",
                          range_ms=seg, shown=shown, seen_before=seen_before, repeat=bool(repeat))
            except Exception as e:  # noqa: BLE001 — 기록 없이 열지 않는다
                LOG.warning("가려내기 기록 시작 실패: %s", type(e).__name__)
                self._log = None
                self.set_blocked(tr("가려내기 기록을 시작하지 못했습니다 ({0})").format(type(e).__name__))
                return False
        self._segment = seg
        self._scenario_id = sid
        self._rows = rows
        self._vlines = vlines
        self._viewer_no = vno
        self._targets = targets
        self._answers = {}
        self._result = None
        self._seen_before = seen_before
        self._repeat = bool(repeat)
        if timemap is not None:
            self._to_file = lambda ms: int(timemap.to_file(int(ms)))
            self._to_vod = lambda ms: int(timemap.to_vod(int(ms)))
        else:
            self._to_file, self._to_vod = _ident, _ident
        self.chat.set_messages(rows, dict(vno) if vno is not None else None)
        if hasattr(self.chat, "set_label_marks"):
            self.chat.set_label_marks(None)
        if self._chat_view is not None:
            self._chat_view.setColumnHidden(COL_VIEWER, True)
            hh = self._chat_view.horizontalHeader()
            hh.resizeSection(COL_KIND, JUDGE_COL_W)
        dur = int(duration_ms) if _is_int(duration_ms) and duration_ms > 0 else seg[1]
        self.timeline.set_duration(max(dur, seg[1]))
        self.timeline.set_view(seg[0], seg[1])
        n_gen = shown[dc.SRC_GENERATED]
        self.banner.setText(tr(BANNER_GENERATED if n_gen else BANNER_RETRIEVED))
        self.banner.setVisible(True)
        self.result_label.setVisible(False)
        self.stack.setCurrentWidget(self.page_body)
        self.btn_play.setEnabled(True)
        self.btn_finish.setEnabled(True)
        self._state = "judging"
        self._set_muted(False)                         # 소리 켜고 (25 6-2)
        self._vod_pos = seg[0]
        self._apply_position(seg[0])
        self._has_media = False
        self._autoplay = True
        if media_path and self.player.load(media_path) is not False:
            self._has_media = True
        if self._has_media:
            self.player.seek(self._to_file(seg[0]))
            self._say(tr(HINT_START))
        else:
            self._autoplay = False
            self._say(tr("영상을 열지 못했습니다"))
        self._started = True
        self._emit("discern.start", scenario_id=sid, range={"start_ms": seg[0], "end_ms": seg[1]}, audio="on",
                   marks_hidden=True, shown=shown, seen_before=seen_before, repeat=bool(repeat))
        self._update_count()
        LOG.info("가려내기 시작: 구간 %d~%d ms · 대상 %d (실제 %d · 가져옴 %d · 생성 %d)", seg[0], seg[1], len(targets),
                 shown[dc.SRC_REPLAY], shown[dc.SRC_RETRIEVED], n_gen)
        QTimer.singleShot(0, self, self.setFocus)
        return True

    def _mix(self, chat, scenario, viewer_no, seg):
        """trainer_view 와 같은 섞기(표시 없이). → (화면 줄, viewer_no, 가상 줄 dict) 또는 막고 None."""
        from ..core import training as trn
        if not isinstance(chat, (list, tuple)):
            self.set_blocked(tr("가려내기에는 채팅 줄 목록이 필요합니다"))
            return None
        try:
            mixed = self._mix_fn(list(chat), scenario)
            if isinstance(mixed, tuple) and len(mixed) == 2 and isinstance(mixed[0], (list, tuple)):
                mixed = mixed[0]
            rows, vlines = trn.split_mixed(mixed, real=chat)
        except Exception as e:  # noqa: BLE001
            LOG.warning("가려내기 섞기 실패: %s", type(e).__name__)
            self.set_blocked(tr("가상 채팅을 섞지 못했습니다 ({0})").format(type(e).__name__))
            return None
        rows, vlines, n_out, n_dup = _tv._clip_mixed(rows, vlines, seg)
        if n_out or n_dup:
            LOG.info("가려내기 섞기: 구간 밖 실제 채팅 %d줄 · 실제와 본문이 같은 가상 줄 %d줄 뺌", n_out, n_dup)
        if not vlines:
            self.set_blocked(tr(REASON_NO_SCENARIO))
            return None
        vno = None
        if viewer_no is not None:
            vno = dict(viewer_no)
            nxt = max([n for n in vno.values() if isinstance(n, int)], default=0)
            for r in rows:
                vid = r.get("viewer_id")
                if trn.is_virtual_id(r.get("chat_id")) and isinstance(vid, str) and vid not in vno:
                    nxt += 1
                    vno[vid] = nxt
        return rows, vno, vlines

    @staticmethod
    def _build_targets(rows: list, vlines: Mapping, scenario_id: str, seg: tuple[int, int]) -> dict:
        """화면 줄 → 대상(20 7-1: 구간에 보인 채팅 전부). 실제 줄은 시청자의 일반 채팅만."""
        from ..core import training as trn
        out: dict[str, dc.Target] = {}
        for r in rows:
            cid = r.get("chat_id")
            t = r.get("t_ms")
            if not isinstance(cid, str) or not cid or not _is_int(t) or not (seg[0] <= t < seg[1]):
                continue
            if trn.is_virtual_id(cid):
                ln = vlines.get(cid)
                if ln is None:
                    continue
                out[cid] = dc.Target(dc.target_key(scenario_id=scenario_id, line_id=ln.line_id), ln.source, int(t))
                continue
            # 후원·시스템 등과 스트리머·매니저 채팅은 표의 배지로 실제임이 드러나 대상에서 뺀다(viewer·unknown 만)
            if r.get("kind", "text") != "text" or r.get("role") in ("streamer", "manager"):
                continue
            if dc.KEY_SEP in cid:
                continue
            out[cid] = dc.Target(cid, dc.SRC_REPLAY, int(t))
        return out

    # ------------------------------------------------------------------ 판정
    def targets(self) -> dict[str, dc.Target]:
        """화면 id → 대상 (시험·점검용)."""
        return dict(self._targets)

    def target_count(self) -> int:
        return len(self._targets)

    def judged_count(self) -> int:
        return len(self._answers)

    def answers(self) -> dict[str, str]:
        """짝 키 → 판정 (real·virtual·unsure)."""
        return dict(self._answers)

    def answer_of(self, display_id: str) -> str | None:
        t = self._targets.get(display_id)
        return self._answers.get(t.key) if t is not None else None

    def _cell_state(self, display_id) -> tuple[str, str | None] | None:
        """단추 칸을 그릴 줄이면 (화면 id, 지금 판정), 아니면 None."""
        if self._state != "judging" or not isinstance(display_id, str) or display_id not in self._targets:
            return None
        return display_id, self.answer_of(display_id)

    def judge(self, display_id: str, answer: str) -> str | None:
        """단추 한 번: 기록한 값(real·virtual·unsure, 같은 단추를 다시 눌렀으면 none) 또는 None(못 누름, 까닭은 msg)."""
        if self._state != "judging" or answer not in dc.ANSWERS:
            return None
        t = self._targets.get(display_id)
        if t is None:
            return None
        now = max(0, int(self._vod_pos))
        if t.vod_ms is None or t.vod_ms > now + _tv.PICK_FUTURE_TOL_MS:
            self._say(tr("아직 올라오지 않은 채팅입니다"))
            return None
        nxt, rec = dc.apply_answer(self._answers, t.key, answer)
        if self._log is not None:
            try:
                self._log.judge(t.key, rec, now)
            except Exception as e:  # noqa: BLE001
                LOG.warning("가려내기 기록 쓰기 실패: %s", type(e).__name__)
                self._say(tr("가려내기 기록을 쓰지 못해 반영하지 않았습니다 ({0})").format(type(e).__name__))
                return None
        self._answers = nxt
        self._emit("discern.judge", **dc.parse_key(t.key), answer=rec, judged_vod_ms=now, wall_at=self._clock())
        self._refresh_marks()
        self._say("")
        return rec

    def _refresh_marks(self) -> None:
        if hasattr(self.chat, "set_label_marks"):
            marks = {vid: tr(dc.ANSWER_KO[a]) for vid, t in self._targets.items()
                     if (a := self._answers.get(t.key)) is not None}
            try:
                self.chat.set_label_marks(marks, own=True, tooltip=tr(JUDGE_TIP))
            except TypeError:
                self.chat.set_label_marks(marks, own=True)
        self._update_count()

    def _update_count(self) -> None:
        n = len(self._answers)
        self.count_label.setText(tr("판정 {0} / {1}").format(n, len(self._targets)))
        self.judgedCountChanged.emit(n)

    # ------------------------------------------------------------------ 재생
    def _set_muted(self, mute: bool) -> None:
        if hasattr(self.player, "set_mute"):
            self.player.set_mute(bool(mute))
        else:
            self.player.set_volume(0 if mute else 100)
        self._muted = bool(mute)

    def is_muted(self) -> bool:
        return self._muted

    def toggle_play(self) -> None:
        if self._state not in ("judging", "result") or not self._has_media:
            return
        self.player.toggle()

    def seek_vod(self, vod_ms: int) -> None:
        vod_ms = int(vod_ms)
        self._vod_pos = vod_ms
        if self._has_media:
            self.player.seek(self._to_file(vod_ms))
        self._apply_position(vod_ms)

    def _on_play_btn(self, *_a) -> None:
        self.toggle_play()
        self.setFocus()

    def _on_finish_btn(self, *_a) -> None:
        self.finish("finish")

    def _on_close_btn(self, *_a) -> None:
        self.closeRequested.emit()

    def _on_timeline_seek(self, vod_ms: int) -> None:
        if self._state in ("judging", "result"):
            self.seek_vod(vod_ms)
            self.setFocus()

    def _on_file_loaded(self) -> None:
        if self._autoplay and self._state == "judging":
            self._autoplay = False
            self.player.play()

    def _on_playing(self, playing: bool) -> None:
        self.btn_play.setText(tr("❚❚ 일시정지") if playing else tr("▶ 재생"))

    def _on_player_error(self, msg: str) -> None:
        self._say(str(msg))

    def _on_player_position(self, file_ms: int) -> None:
        vod = self._to_vod(int(file_ms))
        self._vod_pos = vod
        self._apply_position(vod)
        if self._state == "judging" and self._segment is not None and vod >= self._segment[1] - _tv.END_TOL_MS:
            self.player.pause()
            self.finish("segment_end")

    def _apply_position(self, vod: int) -> None:
        if hasattr(self.chat, "set_reveal_until"):
            self.chat.set_reveal_until(int(vod) if self._state == "judging" else None)
        self.chat.set_time_ms(int(vod))
        self.timeline.set_position(int(vod))

    def position_vod(self) -> int:
        return int(self._vod_pos)

    # ------------------------------------------------------------------ 끝 (정답·숫자)
    def state(self) -> str:
        return self._state

    def result(self) -> dc.DiscernResult | None:
        return self._result

    def finish(self, reason: str = "finish") -> bool:
        """판정을 끝내고 정답·숫자를 보인다. 계산·기록이 안 되면 판정에 남고 False.
        reason: 'finish'([끝내고 정답 보기]·Ctrl+Enter) · 'segment_end'(구간 끝)."""
        if self._state != "judging":
            return False
        reason = reason if reason in ("finish", "segment_end") else "finish"
        try:
            res = self._evaluate(list(self._targets.values()), dict(self._answers), broadcasts=1,
                                 seen_before=self._seen_before, repeat=self._repeat)
        except Exception as e:  # noqa: BLE001
            LOG.warning("가려내기 계산 실패: %s", type(e).__name__)
            self._say(tr("가려내기 계산에 실패했습니다 ({0})").format(type(e).__name__))
            return False
        nums = res.numbers()
        # 정답을 보이기 전에 끝 줄을 쓴다(03 6-19). 못 쓰면 보이지 않는다
        if self._log is not None:
            try:
                self._log.end(reason, judged=res.judged, unseen=res.unseen, result=nums)
            except Exception as e:  # noqa: BLE001
                LOG.warning("가려내기 기록 끝 줄 실패: %s", type(e).__name__)
                self._say(tr("가려내기 기록을 쓰지 못해 정답을 보이지 않았습니다 ({0})").format(type(e).__name__))
                return False
        if self._has_media:
            self.player.pause()
        self._state = "result"
        self._result = res
        self._show_answers(res)
        self._emit("discern.end", reason=reason, judged=res.judged, unseen=res.unseen, result=nums)
        self.resultShown.emit({**nums, "seen_before": self._seen_before, "repeat": self._repeat})
        self.btn_finish.setEnabled(False)
        self._say(tr("정답을 붙였습니다 — 채팅을 두 번 누르면 그 자리로 갑니다"))
        LOG.info("가려내기 끝(%s): 대상 %d · 판정 %d · 탐지 %s · κ %s · 관문 %s", reason, res.targets, res.judged,
                 dc.fmt_rate(res.detection), dc.fmt_kappa(res.kappa), res.gate["status"])
        return True

    def _show_answers(self, res: dc.DiscernResult) -> None:
        """표시를 다시 붙여 채팅을 싣고(20 9장 3 '끝나면 보임'), 줄마다 정답·판정, 위에 숫자 줄·혼동표."""
        from ..core import training as trn
        marked = []
        for r in self._rows:
            if trn.is_virtual_id(r.get("chat_id")) and r.get("source") in _tv.VIRTUAL_TAG:
                src = r["source"]
                r = dict(r)
                r["nickname_masked"] = f"{tr(_tv.VIRTUAL_MARK[src])} {r['nickname_masked']} · {tr(_tv.VIRTUAL_TAG[src])}"
            marked.append(r)
        self.chat.set_messages(marked, dict(self._viewer_no) if self._viewer_no is not None else None)
        if hasattr(self.chat, "set_reveal_until"):
            self.chat.set_reveal_until(None)
        by_key = {row["key"]: row for row in res.rows}
        marks = {vid: dc.answer_mark(by_key[t.key]) for vid, t in self._targets.items() if t.key in by_key}
        if hasattr(self.chat, "set_label_marks"):
            try:
                self.chat.set_label_marks(marks, own=True, tooltip=tr(RESULT_TIP))
            except TypeError:
                self.chat.set_label_marks(marks, own=True)
        self.chat.set_time_ms(int(self._vod_pos))
        lines = "".join(f"<div>{html.escape(x)}</div>" for x in result_lines(res))
        self.result_label.setText(lines + confusion_html(res))
        self.result_label.setVisible(True)
        self.banner.setText(tr(BANNER_RESULT))

    def answer_marks(self) -> dict[str, str]:
        """끝 화면의 줄 표시 화면 id → '✓ 생성 · 가짜' 꼴 (판정 중이면 빈 dict)."""
        if self._result is None:
            return {}
        by_key = {row["key"]: row for row in self._result.rows}
        return {vid: dc.answer_mark(by_key[t.key]) for vid, t in self._targets.items() if t.key in by_key}

    def _on_chat_activated(self, _cid: str, vod_ms: int) -> None:
        if self._state == "result" and _is_int(vod_ms) and vod_ms >= 0:
            self.seek_vod(vod_ms)

    # ------------------------------------------------------------------ 닫기
    def close_view(self) -> None:
        """가려내기를 끝낸다(여러 번 불러도 된다). 정답 전이면 끝 줄(closed, 숫자 없음)을 쓰고 플레이어를 닫는다."""
        if self._state == "closed":
            return
        if self._started and self._state == "judging":
            judged = len(self._answers)
            unseen = len(self._targets) - judged
            if self._log is not None:
                try:
                    self._log.end("closed", judged=judged, unseen=unseen)
                except Exception as e:  # noqa: BLE001 — 닫기는 막지 않는다
                    LOG.warning("가려내기 기록 끝 줄 실패: %s", type(e).__name__)
            self._emit("discern.end", reason="closed", judged=judged, unseen=unseen)
        self._state = "closed"
        try:
            self.player.close_player()
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event):
        self.close_view()
        super().closeEvent(event)

    # ------------------------------------------------------------------ 도우미
    def _emit(self, kind: str, **fields) -> None:
        ev = {"kind": kind, **fields}
        self.events.append(ev)
        self.discernEvent.emit(ev)

    def _say(self, text: str) -> None:
        self.msg.setText(text)
        if text:
            self.messageShown.emit(text)


__all__ = [
    "DiscernView", "JudgeDelegate", "DiscernPrep", "discern_menu_state", "prepare_discern", "result_lines",
    "confusion_html", "LABELING_KINDS", "BANNER_GENERATED", "BANNER_RETRIEVED", "BANNER_RESULT",
    "REASON_PILOT", "REASON_LABELING", "REASON_NO_MEDIA", "REASON_NO_SCENARIO", "REASON_NO_CHAT", "JUDGE_LABELS",
]
