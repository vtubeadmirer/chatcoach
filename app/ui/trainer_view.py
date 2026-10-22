"""스트리머 훈련 — 훈련 재생 화면 (08 D-01 '스트리머 훈련', D-05, D-16 클릭 한 번, D-18 6·7·8, 18 6-2).

열면 바로: 영상(소리 꺼짐) · 흐르는 채팅 표 · 타임라인 · 맨 위 '고른 수 n'. 고르는 창·입력 칸은 없다.

| 동작 | 결과 |
|---|---|
| 채팅 한 번 누름 | '읽겠다' 로 고름 (누른 순간의 재생 위치를 함께 적음). 채팅 표의 '종류' 칸에 '✓ 고름' |
| 같은 채팅 다시 누름 | 고르기 취소 |
| Space | 멈춤·재생 |
| 구간 끝, 또는 [견주기] (Ctrl+Enter) | 견주기 화면(app.ui.train_compare_view)으로 |

- 훈련 중에는 원래 반응·엔진 판정·사람 라벨을 **보이지 않는다**(정답 미리 보기 방지). 기준(reference)은 함수로 받을 수
  있고, 그러면 견주기를 누르는 순간에만 부른다 — 그전에는 메모리에도 없다. 타임라인의 파형(목소리 자리)도 숨긴다.
- 견주기 화면에서 행을 누르면 그 자리로 가서 **소리를 켜고** 원래 대답 앞뒤를 한 번 재생하고 멈춘다.
- 소리 끄기는 영상 소리 통째로(18 6-5 4). 목소리만 빼는 분리는 하지 않는다.

- 흐르는 채팅: 훈련 중에는 ChatPanel.set_reveal_until(재생 위치) 로 아직 안 온 채팅을 표에서 뺀다(스크롤해도 안 보임).
  한 번 누름은 ChatPanel.chatClicked 로 받는다. '✓ 고름' 표시의 풍선 글은 set_label_marks(tooltip=PICK_TIP).
- 메인 창(app/ui/mainwindow '훈련 → 스트리머 훈련')이 작업대 위에 쌓아 보이고, [닫기](closeRequested) 면 close_view() 뒤
  작업대로 돌아간다. 영상이 연결되지 않은 프로젝트에서는 메뉴가 꺼진다(채팅이 흐르지 않으므로).

부품: 작업대와 같은 MpvPlayer·ChatPanel·Timeline 을 import 해 쓴다(02 3-3 공통 계약). 시험은 parts= 로 가짜 부품을 넣는다.
플레이어는 처음부터 이 화면 안의 분할기(vsplit)를 부모로 만들고 옮기지 않는다(10 5-3 의 8). 이 화면도 부모를 주어
만든다(TrainerView(main_window)) — 부모 없이 만든 뒤 다른 창에 넣으면 mpv 창 핸들이 바뀌어 영상이 검게 남는다.

계산은 app.core.training.compare(picks, refs, range_ms=구간, chat_times=…, until_ms=…) -> Comparison 을 부른다.
until_ms 는 구간 끝 전에 [견주기]·Ctrl+Enter 를 누를 때 그때까지 화면에 나온 가장 늦은 VOD 시각(뒤로 옮겨도 줄지 않는다),
구간 끝에서 열리면 None 이다 — 아직 안 나온 채팅의 원래 반응을 '원래만'·분모에 넣지 않으려고(compare_fn 으로 바꿔
넣을 수 있다 — 같은 인자로 부르고, Comparison 이나 dict 를 돌려주면 된다). picks = {chat_id: picked_vod_ms}.
refs 는 reference 로 받은 것(app.reference_set.ReferenceSet · core.training.RefReaction 목록 · dict 목록)을 RefReaction 으로
바꾼 것이다. 채팅 시각(chat_vod_ms)을 모르면 채팅 표에서 채운다 — 구간 거르기는 compare 가 채팅 시각으로 한다.

훈련 기록(18 6-3): log(app.training_store.TrainingLog)를 주면 start·toggle·compare·end 를 그대로 부른다(쓰기 실패면
그 누름은 반영하지 않고 까닭을 보인다). 없으면 파일에 쓰지 않는다. 어느 쪽이든 줄 하나마다 trainingEvent(dict) 를 내고
events 에 쌓는다. 칸은 chat_id·시각·숫자뿐이다 — 채팅 본문·닉네임·전사문은 넣지 않는다(D-17 8).

메뉴 켜기(D-18 7): 정식 판단은 app.reference_set.training_availability(시범 목록·잠금을 본다). 이 화면의
training_menu_state() 는 폴더 이름(pilot-*)만 보는 이중 잠금이고, start() 가 한 번 더 부른다(메뉴를 거치지 않고 불러도
시범 프로젝트가 열리지 않게).

시각: 플레이어는 영상 파일 시각, 채팅·타임라인·기록은 VOD 시각. 변환은 timemap(to_file·to_vod, 작업대 TimeMap 과 같은 모양).
로그에는 건수·시각만 남긴다.

가상 채팅 섞기(H-4, 20 5장, D-19 2): start(scenario=…) 를 주면 mix_fn(real, scenario) 로 실제 채팅과 시나리오 줄을
섞어 흘린다. 기본 mix_fn 은 app.core.scenario.merge_for_display(real, records, resolve=…, clip_to_range=True)
(H-1 의 정식 이름 — 작업 지시의 가정 이름 'mix' 는 쓰지 않는다). 가상 채팅을 섞을 때는 실제 채팅을 훈련 구간 안만
싣고(retrieved 의 원 채팅은 같은 방송 구간 밖이라 같은 표에 원 가림 닉네임으로 보이면 가명이 이어진다, 20 8장 3번),
실제 줄과 본문이 같은 가상 줄은 뺀다(_clip_mixed). 시나리오가 없으면 지금(F)처럼 다 싣는다. scenario = 시나리오 줄 목록 또는
{"records", "resolve"}(retrieved 본문을 원 messages.jsonl 에서 읽는 함수 — 부르는 쪽이 준다).
결과는 app.core.training.split_mixed 로 화면 줄을 만든다: 가상 줄의 화면 id 는 'v:'+line_id, 이름은 시나리오의 가림
이름(없으면 '시청자N'), 원래 채팅의 닉네임·viewer_id 는 버린다. messages.jsonl 에는 아무것도 쓰지 않는다.
- 연습 화면 표시(20 2장·5-2·9장 3): 가상 줄의 닉네임 칸 앞에 출처 한 글자('가'·'생', 좁은 칸에서도 잘리지 않게),
  뒤에 ' · 가져옴'·' · 생성' — 예 '생 두* · 생성'. show_virtual_marks=False 면
  숨긴다(H-5 가려내기용 — 그 화면은 '생성 채팅이 섞여 있음' 한 줄을 따로 보여야 한다). 견주기는 늘 드러낸다.
- 가상 채팅도 고르기·취소(한 번 누름)가 된다. 견주기 점수(같음·나만·원래만·맞힌 비율·시각 차)는 replay 만으로
  낸다 — compare_fn 에는 실제 채팅 고르기만 넘긴다. 가상 고르기는 core.training.compare_virtual 로 따로 세어
  견주기 아래 '가상 채팅 m개 가운데 k개 고름' 과 '가상 고름' 행으로 보인다.
- 기록: 가상 고르기의 trainingEvent 는 chat_id 대신 line_id·source. 파일 기록은 log 에 toggle_virtual(line_id, source,
  vod_ms) 가 있을 때만 쓴다(training-event 스키마에 선택 칸이 더해진 뒤). start·compare 의 선택 칸(scenario·virtual)도
  log 가 그 인자를 받을 때만 넘긴다. 시나리오가 없으면 지금(F)과 바이트까지 같다.
"""
from __future__ import annotations

import inspect
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping, Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSplitter, QStackedWidget, QVBoxLayout, \
    QWidget

from ..i18n import tr
from .train_compare_view import TrainCompareView

LOG = logging.getLogger(__name__)

PILOT_PREFIX = "pilot-"
PICK_MARK = "✓ 고름"
PICK_TIP = "'읽겠다' 로 고른 채팅 — 다시 한 번 누르면 취소합니다"
PICK_FUTURE_TOL_MS = 1_000      # 재생 위치보다 이만큼 뒤의 채팅까지는 고를 수 있다(표 갱신 40ms·반올림 여유)
END_TOL_MS = 200                # 구간 끝 이만큼 앞이면 끝난 것으로 본다
SOURCE_REPLAY = "replay"        # D-05: 실제 채팅 재생

VIRTUAL_TAG = {"retrieved": "가져옴", "generated": "생성"}   # 연습 화면의 가상 표시 (20 2장)
# 닉네임 칸 **앞** 한 글자 표시: 출처를 한눈에 가른다. 닉네임 칸은 폭이 좁아(64px, 뒤를 줄임표로 자름) 뒤의
# ' · 가져옴'·' · 생성' 이 잘려 retrieved 와 generated 가 같아 보일 수 있다 — 앞 글자는 잘리지 않는다(H-3 준비).
VIRTUAL_MARK = {"retrieved": "가", "generated": "생"}

REASON_NO_REFERENCE = "라벨이 잠긴 구간이 없습니다"
REASON_NO_SCENARIO = "가상 채팅이 아직 없습니다"
REASON_PILOT = "시범 세션이 모두 잠기기 전에는 시범 프로젝트로 훈련하지 않습니다"


# ---------------------------------------------------------------------------- 메뉴 켜기 (순수)
def is_pilot_project(project_name: str | None) -> bool:
    """시범 프로젝트 폴더 이름(pilot-*)인가. 경로를 주면 마지막 이름으로 본다."""
    if not project_name:
        return False
    name = os.path.basename(os.path.normpath(str(project_name)))
    return name.lower().startswith(PILOT_PREFIX)


def training_menu_state(project_name: str | None, *, has_reference: bool, pilot_locked: bool = False) -> tuple[bool, str]:
    """'훈련 → 스트리머 훈련' 메뉴를 켤까. (켜짐, 꺼졌으면 까닭 한 줄).

    - 시범 프로젝트(pilot-*)이고 시범 세션이 모두 잠기지 않았으면 끈다(D-18 7). 이 검사가 먼저다 — 기준이 있는지
      알아보려고 시범 프로젝트의 라벨을 여는 일도 없게, 부르는 쪽은 이 값이 False 면 has_reference 를 계산하지 않아도 된다.
    - 기준(사람 최종 세트, 없으면 손라벨)이 없으면 끈다.
    """
    if is_pilot_project(project_name) and not pilot_locked:
        return False, tr(REASON_PILOT)
    if not has_reference:
        return False, tr(REASON_NO_REFERENCE)
    return True, ""


def first_segment(segments: Iterable[Sequence[int]] | None) -> tuple[int, int] | None:
    """기준이 있는 구간들 [(start_ms, end_ms)] 중 가장 이른 것. 없거나 모두 길이 0 이하면 None (18 6-2 '첫 구간')."""
    best = None
    for s in segments or ():
        try:
            a, b = int(s[0]), int(s[1])
        except (TypeError, ValueError, IndexError):
            continue
        if b > a and (best is None or (a, b) < best):
            best = (a, b)
    return best


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ident(ms: int) -> int:
    return int(ms)


def _int_or_none(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _ref_items(reference) -> list[dict]:
    """기준 → [{"chat_id", "chat_vod_ms", "reaction_vod_ms", "grade"}]. 그 밖의 칸은 버린다(본문·닉네임을 넘기지 않게).

    받는 것: ReferenceSet(.reactions) · RefReaction 같은 속성 객체 목록 · dict 목록 · chat_id 문자열 목록."""
    items = getattr(reference, "reactions", reference)
    out = []
    for r in items or ():
        if isinstance(r, str):
            d = {"chat_id": r}
        elif isinstance(r, Mapping):
            d = r
        else:
            d = {k: getattr(r, k, None) for k in ("chat_id", "chat_vod_ms", "reaction_vod_ms", "grade")}
        cid = d.get("chat_id")
        if not isinstance(cid, str) or not cid:
            continue
        rt = None
        for k in ("reaction_vod_ms", "reaction_time_ms", "reaction_ms"):
            rt = _int_or_none(d.get(k))
            if rt is not None:
                break
        g = d.get("grade")
        out.append({"chat_id": cid, "chat_vod_ms": _int_or_none(d.get("chat_vod_ms")), "reaction_vod_ms": rt,
                    "grade": g if isinstance(g, str) else None})
    return out


def _default_compare(picks, refs, *, range_ms=None, chat_times=None, until_ms=None):
    """app.core.training.compare 로 (F 첫 묶음 계산 담당). dict 기준을 RefReaction 으로 바꿔 넘긴다.
    until_ms: 구간 끝 전에 견주기를 누르면 그때까지 화면에 나온 채팅 시각 — 아직 안 나온 채팅의 원래 반응은
    '원래만'·분모에서 뺀다 (구간 끝이면 None)."""
    from ..core import training as trn
    rr = [trn.RefReaction(r["chat_id"], r.get("chat_vod_ms"), r.get("reaction_vod_ms"), r.get("grade")) for r in refs]
    return trn.compare(dict(picks), rr, range_ms=range_ms, chat_times=chat_times, until_ms=until_ms)


def _scenario_parts(scenario):
    """scenario → (시나리오 줄 목록, resolve). 받는 모양: 줄 목록 · {"records", "resolve"?} · .records/.resolve 객체."""
    if isinstance(scenario, (list, tuple)):
        return list(scenario), None
    if isinstance(scenario, Mapping):
        return scenario.get("records"), scenario.get("resolve")
    return getattr(scenario, "records", None), getattr(scenario, "resolve", None)


def _default_mix(real, scenario):
    """시나리오 담당(H-1, app.core.scenario)의 섞기 함수 merge_for_display(real, records, resolve=…, clip_to_range=True)
    를 부른다. clip_to_range=True: 실제 채팅은 시나리오 구간 [from, to) 안만 싣는다 — retrieved 의 원 채팅(같은 방송
    구간 밖)이 같은 표에 원 가림 닉네임으로 보이지 않게(20 8장 3번). 화면도 훈련 구간으로 한 번 더 자른다(_clip_mixed).
    돌려준 MergeResult 는 core.training.split_mixed 가 화면 줄로 고른다."""
    from ..core import scenario as scn
    records, resolve = _scenario_parts(scenario)
    if not records:
        raise ValueError("시나리오 줄이 없습니다")
    return scn.merge_for_display(real, records, resolve=resolve, clip_to_range=True)


def _norm_text(s) -> str:
    """본문 견주기용: 공백을 하나로, 대소문자 무시."""
    return " ".join(str(s or "").split()).casefold()


def _clip_mixed(rows: list, vlines: dict, seg: tuple[int, int] | None) -> tuple[list, dict, int, int]:
    """섞은 화면 줄 → (실제 줄은 구간 [시작, 끝) 안만, 남은 실제 줄과 본문이 같은 가상 줄은 뺀 것, 뺀 실제 수,
    뺀 가상 수). seg 가 None 이면 실제 줄은 그대로 둔다. 숫자만 돌려준다(본문은 로그에 남기지 않는다)."""
    from ..core import training as trn
    kept: list = []
    n_out = 0
    for r in rows:
        if trn.is_virtual_id(r.get("chat_id")):
            kept.append(r)
            continue
        t = r.get("t_ms")
        if seg is not None and not (isinstance(t, int) and not isinstance(t, bool) and seg[0] <= t < seg[1]):
            n_out += 1
            continue
        kept.append(r)
    real_texts = {_norm_text(r.get("text")) for r in kept if not trn.is_virtual_id(r.get("chat_id"))}
    real_texts.discard("")
    out: list = []
    vl = dict(vlines)
    n_dup = 0
    for r in kept:
        vid = r.get("chat_id")
        if trn.is_virtual_id(vid) and _norm_text(r.get("text")) in real_texts:
            vl.pop(vid, None)
            n_dup += 1
            continue
        out.append(r)
    return out, vl, n_out, n_dup


def _scenario_info(scenario) -> dict:
    """시나리오의 id·해시 (기록 선택 칸 scenario_id·scenario_sha256). 모르면 비운다."""
    out = {}
    records, _ = _scenario_parts(scenario)
    hdr0 = records[0] if records and isinstance(records[0], Mapping) and records[0].get("kind") == "scenario.header" \
        else None
    if hdr0 is not None and isinstance(hdr0.get("scenario_id"), str):
        out["scenario_id"] = hdr0["scenario_id"]
    for key, names in (("scenario_id", ("scenario_id", "id")), ("scenario_sha256", ("scenario_sha256", "sha256"))):
        if key in out:
            continue
        for n in names:
            v = scenario.get(n) if isinstance(scenario, Mapping) else getattr(scenario, n, None)
            if v is None and not isinstance(scenario, Mapping):
                hdr = getattr(scenario, "header", None)
                v = hdr.get(n) if isinstance(hdr, Mapping) else None
            if isinstance(v, str) and v:
                out[key] = v
                break
    return out


def _accepts(fn, name: str) -> bool:
    """fn 이 이름 인자 name 을 받는가 (기록 쪽에 선택 칸이 생겼는지 볼 때)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


# ---------------------------------------------------------------------------- 부품
def _default_part(kind: str):
    if kind == "player":
        from ..player import MpvPlayer
        return MpvPlayer
    if kind == "chat":
        from .chat_panel import ChatPanel
        return ChatPanel
    from .timeline import Timeline
    return Timeline


# ---------------------------------------------------------------------------- 화면
class TrainerView(QWidget):
    """스트리머 훈련 재생 + 견주기. start() 로 시작한다."""

    trainingEvent = Signal(dict)        # 훈련 기록 한 줄 (18 6-3) {"kind", …body 칸}. log 를 주면 이미 쓴 뒤에 낸다
    pickCountChanged = Signal(int)
    compareShown = Signal(dict)         # 견주기 숫자 (train_compare_view.summary_numbers 모양)
    messageShown = Signal(str)
    closeRequested = Signal()           # [닫기] — 메인 창이 close_view() 뒤 작업대로 돌아간다

    def __init__(self, parent=None, *, parts: dict | None = None,
                 compare_fn: Callable[..., object] | None = None,
                 clock: Callable[[], str] | None = None,
                 mix_fn: Callable[..., object] | None = None):
        super().__init__(parent)
        self._compare_fn = compare_fn or _default_compare
        self._mix_fn = mix_fn or _default_mix
        self._vlines: dict = {}                   # 가상 화면 id → core.training.VirtualLine (시나리오가 있을 때만)
        self._vpicks: dict[str, int] = {}         # 가상 화면 id → 고른 순간의 VOD 시각
        self._scenario: dict | None = None        # {"scenario_id", "scenario_sha256"} (없으면 None = 지금의 F)
        self._vsum = None                         # 마지막 견주기의 core.training.VirtualSummary
        self._clock = clock or _now_iso
        self.events: list[dict] = []
        self._picks: dict[str, int] = {}          # chat_id → 고른 순간의 VOD 시각 (고른 순서)
        self._segment: tuple[int, int] | None = None
        self._reference = None                    # list 또는 callable (견주기 때만 펼친다)
        self._reference_hash: str | None = None
        self._to_file: Callable[[int], int] = _ident
        self._to_vod: Callable[[int], int] = _ident
        self._vod_pos = 0
        self._seen_until = 0                      # 훈련 중 화면에 채팅이 나온 가장 늦은 VOD 시각 (이른 견주기의 until_ms)
        self._has_media = False
        self._autoplay = False
        self._state = "idle"                      # idle · training · compare · closed
        self._listen_end: int | None = None       # 견주기 듣기: 이 VOD 시각에 멈춘다
        self._last_click: tuple[str, float] | None = None
        self._result = None                       # Comparison 또는 dict
        self._started = False
        self._muted = False
        self._log = None                          # app.training_store.TrainingLog (있으면)
        self._end_reason = "closed"               # training.end 의 reason: compare · segment_end · closed
        self.setFocusPolicy(Qt.StrongFocus)

        parts = parts or {}
        self.chat = (parts.get("chat") or _default_part("chat"))()
        self.timeline = (parts.get("timeline") or _default_part("timeline"))()

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
        # Qt 연결은 묶인 메서드로만 한다(self 를 잡은 람다는 화면이 지워지지 않게 붙든다 — analysis_view 종료 접근 위반과 같은 원인)
        self.btn_play.clicked.connect(self._on_play_btn)
        top.addWidget(self.btn_play)
        self.btn_compare = QPushButton(tr("견주기"))
        self.btn_compare.setToolTip(tr("원래 반응과 견주기 (Ctrl+Enter) — 구간 끝에서도 저절로 넘어갑니다"))
        self.btn_compare.setFocusPolicy(Qt.NoFocus)
        self.btn_compare.clicked.connect(self._on_compare_btn)
        top.addWidget(self.btn_compare)
        self.btn_close = QPushButton(tr("닫기"))
        self.btn_close.setToolTip(tr("훈련을 끝내고 작업대로 돌아갑니다 (기록은 남습니다)"))
        self.btn_close.setFocusPolicy(Qt.NoFocus)
        self.btn_close.clicked.connect(self._on_close_btn)
        top.addWidget(self.btn_close)
        root.addLayout(top)

        # 막힌 상태(메뉴 밖에서 불렸거나 자료가 없을 때) — 까닭 한 줄
        self.stack = QStackedWidget(self)
        self.blocked_label = QLabel("")
        self.blocked_label.setAlignment(Qt.AlignCenter)
        self.blocked_label.setWordWrap(True)
        self.stack.addWidget(self.blocked_label)

        # 플레이어(MpvPlayer)는 만들 때 winId() 로 창 핸들을 mpv 에 넘긴다. 그래서 조상 사슬을 먼저 이 화면에 붙여 두고 그
        # 안(vsplit)에서 만든다 — 부모 없는 분할기에 넣었다가 옮기면 창 핸들이 새로 만들어져 mpv 가 옛 핸들에 그려 영상이
        # 검게 남는다(실제 창에서 확인, 10 5-3 의 8)
        body = QWidget(self)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        self.hsplit = QSplitter(Qt.Horizontal, body)
        self.vsplit = QSplitter(Qt.Vertical, self.hsplit)
        self.player = (parts.get("player") or _default_part("player"))(self.vsplit)
        self.vsplit.addWidget(self.player)
        self.vsplit.addWidget(self.timeline)
        self.vsplit.setStretchFactor(0, 3)
        self.vsplit.setStretchFactor(1, 1)
        self.hsplit.addWidget(self.vsplit)
        self.right = QStackedWidget()
        self.right.addWidget(self.chat)
        self.compare_view = TrainCompareView()
        self.right.addWidget(self.compare_view)
        self.hsplit.addWidget(self.right)
        self.hsplit.setStretchFactor(0, 3)
        self.hsplit.setStretchFactor(1, 2)
        bl.addWidget(self.hsplit, 1)
        self.stack.addWidget(body)
        self.page_body = body
        root.addWidget(self.stack, 1)

        # 채팅 표: 검색 칸은 숨긴다(입력 칸 없음, D-16 — Space 가 검색어로 들어가지도 않게). 오른쪽 메뉴도 주지 않는다.
        search = getattr(self.chat, "search", None)
        if search is not None:
            search.setVisible(False)
        if hasattr(self.chat, "chatClicked"):
            self.chat.chatClicked.connect(self._on_chat_id_clicked)       # ChatPanel 공개 신호(한 번 누름)
        else:
            view = getattr(self.chat, "view", None)
            if view is not None and hasattr(view, "clicked"):
                view.clicked.connect(self._on_chat_clicked)
        # 타임라인: 파형(목소리 자리 = 대답 자리 힌트)과 구간 칸은 숨긴다. 채팅 밀도는 둔다.
        if hasattr(self.timeline, "set_fixed_tracks_visible"):
            self.timeline.set_fixed_tracks_visible(segments=False, density=True, waveform=False)
        if hasattr(self.timeline, "set_marker_style"):
            self.timeline.set_marker_style("train_pick", QColor(255, 255, 255), False)
            self.timeline.set_marker_style("train_orig", QColor("#e0b252"), True)
        self.timeline.seekRequested.connect(self._on_timeline_seek)

        self.player.positionChanged.connect(self._on_player_position)
        self.player.playingChanged.connect(self._on_playing)
        self.player.errorOccurred.connect(self._on_player_error)
        if hasattr(self.player, "fileLoaded"):
            self.player.fileLoaded.connect(self._on_file_loaded)
        if hasattr(self.player, "clicked"):
            self.player.clicked.connect(self._on_play_btn)
        self.compare_view.listenRequested.connect(self.listen)

        # 단축키: 이 화면 안에 초점이 있을 때만(작업대의 Space 와 겹치지 않게)
        self.shortcuts: dict[str, QShortcut] = {}
        for key, fn in (("Space", self.toggle_play), ("Ctrl+Return", self.show_compare),
                        ("Ctrl+Enter", self.show_compare)):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
            self.shortcuts[key] = s

        self._update_count()
        self.set_blocked(tr("훈련할 구간을 아직 열지 않았습니다"))

    # ------------------------------------------------------------------ 시작
    def set_blocked(self, reason: str) -> None:
        """까닭 한 줄만 보이는 상태."""
        self.blocked_label.setText(reason)
        self.stack.setCurrentWidget(self.blocked_label)
        self.btn_play.setEnabled(False)
        self.btn_compare.setEnabled(False)

    def start(self, *, chat, segment: Sequence[int] | None = None, segments: Iterable[Sequence[int]] | None = None,
              reference=None, reference_hash: str | None = None, media_path: str | None = None, timemap=None,
              viewer_no: Mapping[str, int] | None = None, duration_ms: int | None = None,
              density: Sequence[int] | None = None, density_bucket_ms: int = 10_000,
              project_name: str | None = None, pilot_locked: bool = False,
              log=None, project_id: str | None = None, scenario=None,
              show_virtual_marks: bool = True) -> bool:
        """훈련을 연다. 막히면 까닭 한 줄을 보이고 False.

        chat: messages.jsonl 줄 목록 또는 chat_panel.ChatData.
        segment = 훈련할 VOD 범위. 없으면 segments 의 첫 구간, 그것도 없으면 reference.first_range(ReferenceSet).
        reference: ReferenceSet · RefReaction 목록 · dict 목록, 또는 그것을 돌려주는 함수(견주기 때만 부른다).
        reference_hash: 기준 해시(없으면 ReferenceSet.sha256). timemap: to_file·to_vod (작업대 TimeMap). 없으면 같은 시각.
        log: app.training_store.TrainingLog (TrainingLog.create 가 시범 잠금을 한 번 더 본다). project_id: start 줄 칸.
        scenario: 섞을 시나리오(core.scenario 가 읽은 것, mix_fn 에 그대로 넘긴다). None 이면 지금의 F(실제 채팅만).
          섞기가 실패하거나 가상 줄이 0개면 막고 까닭 한 줄(기록 시작 전에 — 노출로 세지 않는다).
        show_virtual_marks: 연습 화면의 '가져옴'·'생성' 표시(기본 보임, 20 5-2). False 는 가려내기(H-5)용.
        """
        ok, why = training_menu_state(project_name, has_reference=reference is not None, pilot_locked=pilot_locked)
        if not ok:
            self.set_blocked(why)
            return False
        if segment is not None:
            seg = (int(segment[0]), int(segment[1]))
        else:
            seg = first_segment(segments)
            if seg is None and not callable(reference):
                seg = first_segment([getattr(reference, "first_range", None) or ()])
        if seg is None or seg[1] <= seg[0]:
            self.set_blocked(tr(REASON_NO_REFERENCE))
            return False
        vlines: dict = {}
        scen_info = None
        if scenario is not None:
            mixed = self._mix_scenario(chat, scenario, viewer_no, show_virtual_marks, seg)
            if mixed is None:
                return False
            chat, viewer_no, vlines = mixed
            scen_info = _scenario_info(scenario)
        self._log = log
        if log is not None:
            ref_info = reference.log_reference() if hasattr(reference, "log_reference") else {}
            extra = ({"scenario": {**scen_info, "virtual_lines": len(vlines)}}
                     if scen_info is not None and _accepts(log.start, "scenario") else {})
            try:
                log.start(project_id=project_id or "", range_ms=seg, reference=ref_info, **extra)
            except Exception as e:  # noqa: BLE001 — 기록 없이 훈련하지 않는다(노출을 세어야 한다)
                LOG.warning("훈련 기록 시작 실패: %s", type(e).__name__)
                self._log = None
                self.set_blocked(tr("훈련 기록을 시작하지 못했습니다 ({0})").format(type(e).__name__))
                return False
        self._segment = seg
        self._reference = reference
        self._reference_hash = reference_hash or (getattr(reference, "sha256", None)
                                                  if isinstance(getattr(reference, "sha256", None), str) else None)
        self._picks = {}
        self._vpicks = {}
        self._vlines = vlines
        self._scenario = scen_info
        self._vsum = None
        self._result = None
        self._listen_end = None
        self._last_click = None
        self._end_reason = "closed"
        if timemap is not None:
            self._to_file = lambda ms: int(timemap.to_file(int(ms)))
            self._to_vod = lambda ms: int(timemap.to_vod(int(ms)))
        else:
            self._to_file, self._to_vod = _ident, _ident
        self.chat.set_messages(chat, dict(viewer_no) if viewer_no is not None else None)
        if hasattr(self.chat, "set_label_marks"):
            self.chat.set_label_marks(None)
        dur = int(duration_ms) if isinstance(duration_ms, int) and duration_ms > 0 else seg[1]
        self.timeline.set_duration(max(dur, seg[1]))
        if density:
            self.timeline.set_density(list(density), int(density_bucket_ms))
        if hasattr(self.timeline, "clear_markers"):
            self.timeline.clear_markers("train_pick")
            self.timeline.clear_markers("train_orig")
        self.timeline.set_view(seg[0], seg[1])
        self.right.setCurrentWidget(self.chat)
        self.stack.setCurrentWidget(self.page_body)
        self.btn_play.setEnabled(True)
        self.btn_compare.setEnabled(True)
        self._state = "training"
        self._set_muted(True)
        self._vod_pos = seg[0]
        self._seen_until = seg[0]
        self._apply_position(seg[0])
        self._has_media = False
        self._autoplay = True
        if media_path:
            if self.player.load(media_path) is not False:
                self._has_media = True
        if self._has_media:
            self.player.seek(self._to_file(seg[0]))
            self._say(tr("소리를 끄고 재생합니다. 읽고 싶은 채팅을 한 번 누르세요"))
        else:
            self._autoplay = False
            self._say(tr("영상이 연결되지 않아 채팅만 보입니다"))
        self._started = True
        start_extra = {}
        if scen_info is not None:       # 20 5-3 선택 칸 (없으면 생략 = 지금의 F)
            start_extra = {**scen_info, "virtual_lines": len(vlines)}
        self._emit("training.start", mode="streamer", source=SOURCE_REPLAY, audio="muted",
                   range={"start_ms": seg[0], "end_ms": seg[1]}, wall_at=self._clock(), **start_extra)
        self._update_count()
        LOG.info("스트리머 훈련 시작: 구간 %d~%d ms · 가상 채팅 %d줄", seg[0], seg[1], len(vlines))
        QTimer.singleShot(0, self, self.setFocus)
        return True

    def _mix_scenario(self, chat, scenario, viewer_no, show_marks: bool, seg: tuple[int, int] | None = None):
        """실제 채팅 + 시나리오 → (화면 줄 목록, viewer_no, 가상 줄 dict). 막히면 까닭을 보이고 None.

        가상 채팅을 섞을 때 실제 채팅은 훈련 구간 [시작, 끝) 안만 싣는다(seg). retrieved 줄의 원 채팅은 같은 방송의
        구간 밖에 있어, 다 실으면 같은 본문이 원 가림 닉네임과 함께 같은 표에 보여 가명 ↔ 원 가림 이름이 이어지고
        가상인 티가 난다(20 8장 3번). 구간 밖 줄은 어차피 고를 수 없고 견주기도 구간으로 거른다(숫자는 그대로).
        남은 실제 줄과 본문(공백·대소문자 무시)이 같은 가상 줄은 뺀다(한 번 더 막기)."""
        from ..core import training as trn
        if not isinstance(chat, (list, tuple)):
            self.set_blocked(tr("가상 채팅을 섞으려면 채팅 줄 목록이 필요합니다"))
            return None
        try:
            mixed = self._mix_fn(list(chat), scenario)
            if isinstance(mixed, tuple) and len(mixed) == 2 and isinstance(mixed[0], (list, tuple)):
                mixed = mixed[0]             # (줄 목록, 정보) 모양도 받는다
            rows, vlines = trn.split_mixed(mixed, real=chat)
        except Exception as e:  # noqa: BLE001 — 깨진 시나리오·섞기 모듈 없음: 훈련을 열지 않는다
            LOG.warning("가상 채팅 섞기 실패: %s", type(e).__name__)
            self.set_blocked(tr("가상 채팅을 섞지 못했습니다 ({0})").format(type(e).__name__))
            return None
        rows, vlines, n_out, n_dup = _clip_mixed(rows, vlines, seg)
        if n_out or n_dup:
            LOG.info("가상 채팅 섞기: 구간 밖 실제 채팅 %d줄 · 실제와 본문이 같은 가상 줄 %d줄 뺌", n_out, n_dup)
        if not vlines:
            self.set_blocked(tr(REASON_NO_SCENARIO))
            return None
        vno = None
        if viewer_no is not None:            # 넘겨받은 번호표에 가상 가명 번호를 뒤에 붙인다('-' 로 드러나지 않게)
            vno = dict(viewer_no)
            nxt = max([n for n in vno.values() if isinstance(n, int)], default=0)
            for r in rows:
                vid = r.get("viewer_id")
                if trn.is_virtual_id(r.get("chat_id")) and isinstance(vid, str) and vid not in vno:
                    nxt += 1
                    vno[vid] = nxt
        if show_marks:
            for r in rows:
                if trn.is_virtual_id(r.get("chat_id")) and r.get("source") in VIRTUAL_TAG:
                    src = r["source"]
                    r["nickname_masked"] = f"{tr(VIRTUAL_MARK[src])} {r['nickname_masked']} · {tr(VIRTUAL_TAG[src])}"
        return rows, vno, vlines

    # ------------------------------------------------------------------ 고르기
    def picks(self) -> list[dict]:
        """지금 고른 실제 채팅 [{"chat_id", "picked_vod_ms"}] (고른 시각 순). 가상 채팅은 virtual_picks()."""
        return [{"chat_id": c, "picked_vod_ms": t} for c, t in sorted(self._picks.items(), key=lambda kv: (kv[1], kv[0]))]

    def virtual_picks(self) -> list[dict]:
        """지금 고른 가상 채팅 [{"line_id", "source", "picked_vod_ms"}] (고른 시각 순)."""
        out = []
        for vid, t in sorted(self._vpicks.items(), key=lambda kv: (kv[1], kv[0])):
            ln = self._vlines.get(vid)
            if ln is not None:
                out.append({"line_id": ln.line_id, "source": ln.source, "picked_vod_ms": t})
        return out

    def virtual_line_count(self) -> int:
        """섞인 가상 채팅 줄 수 (시나리오가 없으면 0)."""
        return len(self._vlines)

    def pick_count(self) -> int:
        """고른 수 (실제 + 가상). 맨 위 '고른 수 n' 과 같다."""
        return len(self._picks) + len(self._vpicks)

    def is_picked(self, chat_id: str) -> bool:
        return chat_id in self._picks or chat_id in self._vpicks

    def _chat_time(self, chat_id: str) -> int | None:
        data = self._chat_data()
        if data is None:
            return None
        i = data.index_of(chat_id)
        if i is None:
            return None
        t = int(data.times[i])
        from .chat_panel import NULL_T
        return None if t == NULL_T else t

    def _chat_text(self, chat_id: str) -> str | None:
        """견주기 표 칸에만 보이는 본문(한 줄). 기록·로그에는 넘기지 않는다."""
        data = self._chat_data()
        if data is None:
            return None
        i = data.index_of(chat_id)
        if i is None:
            return None
        from .chat_panel import display_text
        return display_text(data.texts[i])

    def _chat_data(self):
        fn = getattr(self.chat, "chat_data", None)
        return fn() if callable(fn) else None

    def toggle_pick(self, chat_id: str) -> str | None:
        """한 번 누름의 뜻: 안 골랐으면 고름, 골랐으면 취소. 'pick' · 'unpick' · None(못 고름, 까닭은 msg)."""
        if self._state != "training" or not chat_id:
            return None
        now = max(0, int(self._vod_pos))
        if chat_id in self._vlines:
            return self._toggle_virtual(chat_id, now)
        if chat_id in self._picks:
            if not self._log_toggle(chat_id, now):
                return None
            del self._picks[chat_id]
            self._emit("training.unpick", chat_id=chat_id, vod_ms=now, wall_at=self._clock())
            self._refresh_picks()
            return "unpick"
        t = self._chat_time(chat_id)
        seg = self._segment
        if t is None:
            self._say(tr("시각이 없는 채팅은 고를 수 없습니다"))
            return None
        if seg is not None and not (seg[0] <= t < seg[1]):
            self._say(tr("훈련 구간 밖의 채팅입니다"))
            return None
        if t > now + PICK_FUTURE_TOL_MS:
            self._say(tr("아직 올라오지 않은 채팅입니다"))
            return None
        if not self._log_toggle(chat_id, now):
            return None
        self._picks[chat_id] = now
        self._seen_until = max(int(self._seen_until), int(t))   # 고른 채팅은 본 것 (이른 견주기 until_ms 가 그 줄을 빼지 않게)
        self._emit("training.pick", chat_id=chat_id, picked_vod_ms=now, wall_at=self._clock(), source=SOURCE_REPLAY)
        self._refresh_picks()
        self._say("")
        return "pick"

    def _toggle_virtual(self, vid: str, now: int) -> str | None:
        """가상 채팅 한 번 누름: 실제 채팅과 같은 규칙(구간 안·아직 안 온 채팅 금지)으로 고름·취소.
        견주기 점수와는 따로 센다. 기록 줄은 chat_id 대신 line_id·source (20 5-3)."""
        ln = self._vlines[vid]
        if vid in self._vpicks:
            if not self._log_virtual(ln, now):
                return None
            del self._vpicks[vid]
            self._emit("training.unpick", line_id=ln.line_id, source=ln.source, vod_ms=now, wall_at=self._clock())
            self._refresh_picks()
            return "unpick"
        t = ln.show_vod_ms
        seg = self._segment
        if t is None:
            self._say(tr("시각이 없는 채팅은 고를 수 없습니다"))
            return None
        if seg is not None and not (seg[0] <= t < seg[1]):
            self._say(tr("훈련 구간 밖의 채팅입니다"))
            return None
        if t > now + PICK_FUTURE_TOL_MS:
            self._say(tr("아직 올라오지 않은 채팅입니다"))
            return None
        if not self._log_virtual(ln, now):
            return None
        self._vpicks[vid] = now
        self._seen_until = max(int(self._seen_until), int(t))   # 고른 줄은 본 것 (견주기 shown ≥ picked)
        self._emit("training.pick", line_id=ln.line_id, picked_vod_ms=now, wall_at=self._clock(), source=ln.source)
        self._refresh_picks()
        self._say("")
        return "pick"

    def _log_virtual(self, ln, vod_ms: int) -> bool:
        """가상 고르기 기록. log 에 toggle_virtual 이 있을 때만 쓴다(training-event 에 선택 칸이 더해진 뒤, 20 5-3).
        없으면 파일에 쓰지 않고 화면 신호(trainingEvent)만 낸다 — 지금 스키마는 pick 줄에 chat_id·source=replay 만 받는다."""
        if self._log is None or not hasattr(self._log, "toggle_virtual"):
            return True
        try:
            self._log.toggle_virtual(ln.line_id, ln.source, vod_ms)
            return True
        except Exception as e:  # noqa: BLE001
            LOG.warning("훈련 기록 쓰기 실패(가상): %s", type(e).__name__)
            self._say(tr("훈련 기록을 쓰지 못해 반영하지 않았습니다 ({0})").format(type(e).__name__))
            return False

    def _log_toggle(self, chat_id: str, vod_ms: int) -> bool:
        """기록이 있으면 먼저 쓴다. 실패면 이 누름을 반영하지 않는다(기록과 화면이 어긋나지 않게)."""
        if self._log is None:
            return True
        try:
            self._log.toggle(chat_id, vod_ms)
            return True
        except Exception as e:  # noqa: BLE001
            LOG.warning("훈련 기록 쓰기 실패: %s", type(e).__name__)
            self._say(tr("훈련 기록을 쓰지 못해 반영하지 않았습니다 ({0})").format(type(e).__name__))
            return False

    def _on_play_btn(self, *_a) -> None:
        self.toggle_play()
        self.setFocus()

    def _on_compare_btn(self, *_a) -> None:
        self.show_compare()

    def _on_close_btn(self, *_a) -> None:
        self.closeRequested.emit()

    def _on_chat_clicked(self, idx) -> None:
        if self._state != "training" or not idx.isValid():
            return
        cid = idx.data(Qt.UserRole)
        if isinstance(cid, str):
            self._on_chat_id_clicked(cid)

    def _on_chat_id_clicked(self, cid: str, _vod_ms: int = -1) -> None:
        if self._state != "training" or not isinstance(cid, str) or not cid:
            return
        # 두 번 누름(더블클릭)은 Qt 가 clicked 를 두 번 낸다 — 같은 채팅을 더블클릭 간격 안에 또 누르면 한 번으로 본다
        now = time.monotonic()
        app = QApplication.instance()
        gap = (app.doubleClickInterval() / 1000.0) if app is not None else 0.4
        last = self._last_click
        self._last_click = (cid, now)
        if last is not None and last[0] == cid and now - last[1] < gap:
            return
        self.toggle_pick(cid)

    def _refresh_picks(self) -> None:
        if hasattr(self.chat, "set_label_marks"):
            marks = {c: tr(PICK_MARK) for c in (*self._picks, *self._vpicks)}
            tip = tr(PICK_TIP)
            try:
                self.chat.set_label_marks(marks, own=True, tooltip=tip)
            except TypeError:            # tooltip 인자가 없는 부품(시험 가짜)
                self.chat.set_label_marks(marks, own=True)
        if hasattr(self.timeline, "set_markers"):
            self.timeline.set_markers("train_pick", [(t, "train_pick") for t in (*self._picks.values(),
                                                                                 *self._vpicks.values())],
                                      label=tr("고른 채팅"))
        self._update_count()

    def _update_count(self) -> None:
        n = self.pick_count()
        self.count_label.setText(tr("고른 수 {0}").format(n))
        self.pickCountChanged.emit(n)

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
        if self._state not in ("training", "compare") or not self._has_media:
            return
        if self._state == "compare":
            self._listen_end = None      # 듣기 중에 Space 로 다시 틀면 끝까지 이어 듣는다
        self.player.toggle()

    def seek_vod(self, vod_ms: int) -> None:
        vod_ms = int(vod_ms)
        self._vod_pos = vod_ms
        if self._has_media:
            self.player.seek(self._to_file(vod_ms))
        self._apply_position(vod_ms)

    def _on_timeline_seek(self, vod_ms: int) -> None:
        if self._state in ("training", "compare"):
            self._listen_end = None
            self.seek_vod(vod_ms)
            self.setFocus()

    def _on_file_loaded(self) -> None:
        if self._autoplay and self._state == "training":
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
        if self._state == "training" and self._segment is not None and vod >= self._segment[1] - END_TOL_MS:
            self.player.pause()
            self.show_compare(reason="segment_end")
        elif self._state == "compare" and self._listen_end is not None and vod >= self._listen_end:
            self._listen_end = None
            self.player.pause()

    def _apply_position(self, vod: int) -> None:
        # 흐르는 채팅: 훈련 중에는 재생 위치까지 온 채팅만 표에 둔다(스크롤해도 앞으로 올 채팅이 보이지 않게, 18 6-2)
        if self._state == "training":
            # 뒤로 옮겨도 이미 나온 채팅은 본 것으로 친다 (견주기 until_ms)
            self._seen_until = max(int(self._seen_until), int(vod))
        if hasattr(self.chat, "set_reveal_until"):
            self.chat.set_reveal_until(int(vod) if self._state == "training" else None)
        self.chat.set_time_ms(int(vod))
        self.timeline.set_position(int(vod))

    def position_vod(self) -> int:
        return int(self._vod_pos)

    # ------------------------------------------------------------------ 견주기
    def state(self) -> str:
        return self._state

    def show_compare(self, reason: str = "compare") -> bool:
        """견주기 화면으로. 기준을 이때 처음 펼치고 compare 를 부른다. 계산·기록이 안 되면 훈련에 남고 False.
        reason: 'compare'(단추·Ctrl+Enter) · 'segment_end'(구간 끝) — training.end 의 reason 이 된다."""
        if self._state != "training":
            return False
        ref = self._reference
        try:
            ref = ref() if callable(ref) else ref
        except Exception as e:  # noqa: BLE001
            LOG.warning("훈련 기준 읽기 실패: %s", type(e).__name__)
            self._say(tr("원래 반응을 읽지 못했습니다 ({0})").format(type(e).__name__))
            return False
        if self._reference_hash is None and isinstance(getattr(ref, "sha256", None), str):
            self._reference_hash = ref.sha256
        items = _ref_items(ref)
        for it in items:                 # 채팅 시각을 모르면 채팅 표에서 (구간 거르기는 compare 가 채팅 시각으로 한다)
            if it["chat_vod_ms"] is None:
                it["chat_vod_ms"] = self._chat_time(it["chat_id"])
        seg = self._segment
        chat_times = {c: t for c in self._picks if (t := self._chat_time(c)) is not None}
        # 구간 끝 전에 누른 견주기: 아직 화면에 안 나온 채팅의 원래 반응은 '원래만'·분모에 넣지 않는다.
        # 구간 끝(segment_end)이거나 이미 끝까지 봤으면 None (구간 전체).
        until = None
        if reason != "segment_end":
            seen = max(int(self._seen_until), int(self._vod_pos))
            if seg is None or seen < seg[1] - END_TOL_MS:
                until = seen
        try:
            result = self._compare_fn(dict(self._picks), items, range_ms=seg, chat_times=chat_times, until_ms=until)
        except (ImportError, AttributeError) as e:
            LOG.warning("견주기 계산 모듈 없음: %s", type(e).__name__)
            self._say(tr("견주기 계산(app.core.training)을 쓸 수 없습니다"))
            return False
        except Exception as e:  # noqa: BLE001
            LOG.warning("견주기 계산 실패: %s", type(e).__name__)
            self._say(tr("견주기 계산에 실패했습니다 ({0})").format(type(e).__name__))
            return False
        # 가상 채팅 고르기는 점수와 따로 센다 (compare_fn 에는 실제 고르기만 넘겼다)
        vsum = None
        if self._scenario is not None:
            from ..core import training as trn
            vsum = trn.compare_virtual(self._vpicks, self._vlines, range_ms=seg, until_ms=until)
        # 정답을 보이기 전에 기록한다(노출을 세게, 06 7장). 못 쓰면 보이지 않는다
        if self._log is not None and hasattr(result, "summary"):
            extra = ({"virtual": vsum.numbers()} if vsum is not None and _accepts(self._log.compare, "virtual")
                     else {})
            try:
                self._log.compare(result, self._reference_hash or "", seg, **extra)
            except Exception as e:  # noqa: BLE001
                LOG.warning("훈련 기록 견주기 줄 실패: %s", type(e).__name__)
                self._say(tr("훈련 기록을 쓰지 못해 견주기를 열지 않았습니다 ({0})").format(type(e).__name__))
                return False
        if self._has_media:
            self.player.pause()
        self._state = "compare"
        if hasattr(self.chat, "set_reveal_until"):
            self.chat.set_reveal_until(None)          # 정답을 본 뒤에는 채팅도 다 보여도 된다
        self._end_reason = reason if reason in ("compare", "segment_end") else "compare"
        self._result = result if result is not None else {}
        self._vsum = vsum
        self.compare_view.set_result(self._result, chat_time=self._chat_time, chat_text=self._chat_text,
                                     virtual=vsum)
        nums = self.compare_view.summary()
        self._emit("training.compare", same=nums["same"], mine_only=nums["mine_only"], orig_only=nums["orig_only"],
                   reference_count=nums["reference"], hit_rate=nums["hit_rate"],
                   median_diff_ms=nums["median_delta_ms"], reference_sha256=self._reference_hash,
                   range={"start_ms": seg[0], "end_ms": seg[1]} if seg else None, until_ms=until,
                   **(vsum.numbers() if vsum is not None else {}))
        # 이제 정답이 보여도 된다: 타임라인에 원래 반응 자리
        if hasattr(self.timeline, "set_markers"):
            marks = [(r.reaction_ms, "train_orig") for r in self.compare_view.rows() if r.reaction_ms is not None]
            self.timeline.set_markers("train_orig", marks, label=tr("원래 반응"))
        self.right.setCurrentWidget(self.compare_view)
        self.btn_compare.setEnabled(False)
        self._say(tr("행을 누르면 소리를 켜고 원래 대답을 듣습니다 · N / Shift+N = 다음·앞 '원래만'"))
        self.compareShown.emit({**nums, **(vsum.numbers() if vsum is not None else {})})
        LOG.info("스트리머 훈련 견주기: 고른 %d · 같음 %d · 원래만 %d · 가상 고름 %d/%d", len(self._picks), nums["same"],
                 nums["orig_only"], vsum.picked if vsum else 0, vsum.shown if vsum else 0)
        QTimer.singleShot(0, self, self.compare_view.table.setFocus)
        return True

    def compare_result(self):
        """마지막 견주기 결과 (core.training.Comparison 또는 dict). 견주기 전이면 None."""
        return self._result

    def virtual_result(self):
        """마지막 견주기의 가상 채팅 묶음 (core.training.VirtualSummary). 시나리오가 없거나 견주기 전이면 None."""
        return self._vsum

    def listen(self, chat_id: str, a_vod: int, b_vod: int) -> None:
        """견주기 행 누름: 그 자리로 가서 소리를 켜고 [a, b] 를 한 번 재생하고 멈춘다."""
        if self._state != "compare":
            return
        self._set_muted(False)
        self._listen_end = int(b_vod)
        self.seek_vod(int(a_vod))
        if self._has_media:
            self.player.play()
        self.compare_view.table.setFocus()     # N / Shift+N 가 바로 먹게

    def listen_end(self) -> int | None:
        return self._listen_end

    # ------------------------------------------------------------------ 끝
    def close_view(self) -> None:
        """훈련을 끝낸다(여러 번 불러도 된다). training.end 를 내고 플레이어를 닫는다."""
        if self._state == "closed":
            return
        if self._started:
            reason = self._end_reason if self._result is not None else "closed"
            if self._log is not None:
                try:
                    self._log.end(reason)
                except Exception as e:  # noqa: BLE001 — 닫기는 막지 않는다
                    LOG.warning("훈련 기록 끝 줄 실패: %s", type(e).__name__)
            self._emit("training.end", reason=reason, picks=len(self._picks),
                       **({"virtual_picks": len(self._vpicks)} if self._scenario is not None else {}))
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
        self.trainingEvent.emit(ev)

    def _say(self, text: str) -> None:
        self.msg.setText(text)
        if text:
            self.messageShown.emit(text)
