"""큐 모드 — 3단계 무작위 표본을 한 건씩 판정 (02 7-4, 06 2장, 03 6-7·6-13).

화면 (작업대 오른쪽 칸에 끼운다. 작업대 연결은 통합 담당이 한다)
  ┌ 머리 ───────────────────────────────────────────────────────────┐
  │ 표본: 구간 01:02:10 ~ 01:48:33   진행 37 / 100   적격 1,795       │
  │ 시드 a91f…  draw 1  rev 1  τ 0e4b…  완충 60·후원 45 은 적격 밖 [재현 검증] │
  ├ 표본 카드 ──────────────────────────────────────────────────────┤
  │ 표본 #38 · 01:20:41 · 시청자 0233 · 대기                          │
  │ "본문"                                                           │
  │ 확인 범위 01:20:31 ~ 01:21:11 [범위 반복 R]                        │
  ├ 판정 ──────────────────────────────────────────────────────────┤
  │ [반응 있음 → 편집기 M]                                            │
  │ 확신도 [확실함 Alt+1] [아마도 Alt+2] [판단 불가 Alt+3]             │
  │ 근거 [__________________] [현재 시각 Ctrl+T]                     │
  │ [반응 미확인 저장 Ctrl+5]  [판단 불가로 저장]                       │
  │ [◀ 이전 PgUp]  [다음 ▶ PgDown]  [다음 대기]                         │
  └────────────────────────────────────────────────────────────────┘

규칙
  - 표본은 프로그램이 뽑는다. 이 화면에 뽑기·다시 뽑기·고르기 기능은 없다(안내서 2장). 매니페스트를 받아 보여 주기만 한다.
  - 블라인드(안내서 원칙 3, 03 12장): 엔진 결과·다른 세션 라벨·AI 등급 제안·출처 배지·2단계 연결 표시를 보이지 않는다.
    채팅은 시각·시청자 번호·본문만 쓰고, 판정 기록은 자기 세션의 from_sample 반응만 센다(session_id 로 거른다).
    등급을 미리 고르지 않는다. '반응 있음' 은 반응 편집기(7-3, 같은 부품)를 여는 요청일 뿐이다.
  - '반응 미확인' 과 '판단 불가' 는 여기서 바로 reaction 초안(result=unconfirmed)을 save 콜백으로 넘긴다.
    판단 불가 = result unconfirmed + confidence undeterminable (안내서 5장: 판정 자체를 못 내림).
  - 저장하면 다음 표본으로 간다(02 7-4). 들어가면 7-2 범위 [채팅 − pre_roll, 채팅 + post_roll] 반복·자동 재생.
    아직 후보 발화가 없으니 '발화 끝' 대신 채팅 시각을 쓴다(반응 편집기가 발화를 정하면 그쪽이 범위를 다시 잡는다).
  - 파일에 쓰지 않는다. 라벨은 save(draft) 콜백(store 경유), 매니페스트 파일은 아래 `write_manifest_file` 을
    통합 쪽이 콜백으로 감싸 쓴다(층 원칙 4: 라벨 파일은 store 만).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Iterable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from app.core import sampling as sp
from app.core import timecode as tc
from app.core import visibility as vis
from app.core.validate import StrictJsonError, load_json_strict
from app.i18n import tr

log = logging.getLogger(__name__)

MANIFEST_MAX_BYTES = 64 << 20
RATIONALE_MAX = 300
DEFAULT_PRE_ROLL_MS = 10_000     # recheck.pre_roll_sec (안내서 7-2)
DEFAULT_POST_ROLL_MS = 30_000    # recheck.post_roll_sec
CONFIDENCES = ("certain", "probably", "undeterminable")
CONF_LABELS = {"certain": "확실함", "probably": "아마도", "undeterminable": "판단 불가"}


# ---------------------------------------------------------------------------- 매니페스트 파일 (통합 쪽 콜백용)
def manifest_path(samples_dir: str, sample_set_id: str) -> str:
    sp.parse_sample_set_id(sample_set_id)          # 경로에 들어가는 이름 검사(형식이 아니면 SamplingError)
    return os.path.join(samples_dir, f"{sample_set_id}.json")


def write_manifest_file(samples_dir: str, manifest: Mapping) -> str:
    """samples/<sample_set_id>.json 을 새로 쓴다. 이미 있으면 FileExistsError(매니페스트는 불변, 03 15-3).

    임시 이름에 다 쓰고 fsync 한 뒤 바꿔 넣는다. 부르는 쪽이 visibility(samples=read_draw)와 프로젝트 잠금을 확인한다.
    """
    path = manifest_path(samples_dir, manifest["sample_set_id"])
    if os.path.exists(path):
        raise FileExistsError(path)
    os.makedirs(samples_dir, exist_ok=True)
    data = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if os.path.exists(path):                       # 그사이 누가 썼으면 덮지 않는다
        os.remove(tmp)
        raise FileExistsError(path)
    os.replace(tmp, path)
    return path


def read_manifest_file(path: str) -> dict:
    """엄격 JSON 으로 읽는다(NaN·중복 키 거부). schema·algorithm 이 다르면 ValueError."""
    with open(path, "rb") as f:
        data = f.read(MANIFEST_MAX_BYTES + 1)
    obj = load_json_strict(data, MANIFEST_MAX_BYTES)
    if obj.get("schema") != sp.SCHEMA or obj.get("algorithm") != sp.ALGORITHM:
        raise ValueError(f"표본 매니페스트가 아닙니다: {os.path.basename(path)}")
    sp.parse_sample_set_id(obj.get("sample_set_id"))
    return obj


def list_manifests(samples_dir: str) -> list[dict]:
    """samples/ 의 매니페스트 전부(읽지 못한 파일은 경고 로그만 남기고 건너뜀). sample_set_id 순."""
    out: list[dict] = []
    try:
        names = sorted(os.listdir(samples_dir))
    except OSError:
        return out
    for name in names:
        if not (name.startswith("S-") and name.endswith(".json")):
            continue
        try:
            out.append(read_manifest_file(os.path.join(samples_dir, name)))
        except (OSError, ValueError, StrictJsonError, sp.SamplingError) as e:
            log.warning("표본 매니페스트를 읽지 못했습니다: %s (%s)", name, type(e).__name__)
    return out


# ---------------------------------------------------------------------------- 초안 (03 6-7, 스키마 reactionBody)
def unconfirmed_draft(manifest: Mapping, item: Mapping, *, confidence: str, rationale: str,
                      head: tuple[str, str] | None = None) -> dict:
    """표본 채팅 하나의 '반응 미확인' reaction 초안. head = (entity_id, 현재 머리 label id) 면 그 대상의 새 판."""
    if confidence not in CONFIDENCES:
        raise ValueError(f"확신도 값이 아닙니다 ({confidence!r})")
    rationale = (rationale or "").strip()
    if not rationale:
        raise ValueError("근거 한 줄이 비었습니다")
    if len(rationale) > RATIONALE_MAX:
        raise ValueError(f"근거는 {RATIONALE_MAX}자 이하")
    body = {
        "chat_id": item["chat_id"],
        "segment_id": manifest["source_segment"]["entity_id"],
        "entry": "from_sample",
        "sample_ref": {"sample_set_id": manifest["sample_set_id"], "order": item["order"]},
        "result": "unconfirmed",
        "grade": None,
        "eligible_grades": [],
        "grade_vs_conditions": "not_applicable",
        "candidates": [],
        "linked_utterance_id": None,
        "first_visible_ms": item["t_ms"],
        "first_visible_source": "chat_clock",
        "reaction_time_ms": None,
        "confidence": confidence,
        "rationale": rationale,
    }
    d: dict[str, Any] = {"kind": "reaction", "body": body}
    if head is not None:
        d["entity_id"], d["supersedes"] = head
    return d


def editor_context(manifest: Mapping, item: Mapping, head: tuple[str, str] | None = None) -> dict:
    """반응 편집기 열기 요청에 담는 것. 편집기는 이걸로 entry·sample_ref·segment_id 를 채운다(등급·후보는 비어 있음)."""
    ctx = {
        "chat_id": item["chat_id"],
        "t_ms": item["t_ms"],
        "segment_id": manifest["source_segment"]["entity_id"],
        "entry": "from_sample",
        "sample_ref": {"sample_set_id": manifest["sample_set_id"], "order": item["order"]},
        "first_visible_ms": item["t_ms"],
        "first_visible_source": "chat_clock",
    }
    if head is not None:
        ctx["entity_id"], ctx["supersedes"] = head
    return ctx


def _short(h: str | None, k: int = 4) -> str:
    return (h[:k] + "…") if h else "-"


def _viewer_text(m: Mapping | None, viewer_no: Mapping[str, int] | None) -> str:
    if not m:
        return "-"
    if m.get("is_anonymous") or m.get("viewer_id") == "anon":
        return tr("익명")
    no = (viewer_no or {}).get(m.get("viewer_id"))
    return tr("시청자 {0}").format(f"{no:04d}") if no else tr("시청자")


# ---------------------------------------------------------------------------- 화면
class QueueView(QWidget):
    """표본 한 건씩 판정. 폼 공통 모양: 생성자에 save(draft) -> record, cancel(), 상태는 load_context(...)."""

    reactionEditorRequested = Signal(dict)   # '반응 있음' → 통합 쪽이 반응 편집기(7-3)를 연다. 저장되면 mark_saved(record)
    currentChanged = Signal(int)             # 지금 표본의 index(0부터). 없으면 -1
    rangeRequested = Signal(int, int)        # 7-2 확인 범위 (VOD ms) — 플레이어를 직접 받지 않았을 때 통합 쪽이 쓴다
    statusChanged = Signal(str)
    allJudged = Signal()

    def __init__(self, save: Callable[[dict], dict], cancel: Callable[[], None], parent=None, *,
                 verify: Callable[[dict], sp.Verification] | None = None):
        super().__init__(parent)
        self._save = save
        self._cancel = cancel
        self._verify = verify
        self._manifest: dict | None = None
        self._items: list[dict] = []
        self._msgs: dict[str, Mapping] = {}
        self._viewer_no: Mapping[str, int] | None = None
        self._states: dict[str, str] = {}
        self._heads: dict[str, tuple[str, str]] = {}
        self._out_of_sample: tuple[str, ...] = ()
        self._idx = -1
        self._player = None
        self._to_file: Callable[[int], int] = lambda ms: ms
        self._to_vod: Callable[[int], int] = lambda ms: ms
        self._pre = DEFAULT_PRE_ROLL_MS
        self._post = DEFAULT_POST_ROLL_MS
        self._autoplay = True
        self._loop_on = False
        self._can_read = False
        self._can_write = False
        self._session_id: str | None = None
        self._last_verify: sp.Verification | None = None
        self._build()
        self._install_shortcuts()
        self._refresh()

    # ------------------------------------------------------------------ 화면 만들기
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.head = QLabel()
        self.head.setTextFormat(Qt.PlainText)
        self.head.setWordWrap(True)
        row = QHBoxLayout()
        self.meta = QLabel()
        self.meta.setTextFormat(Qt.PlainText)
        self.meta.setWordWrap(True)
        self.meta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.verify_btn = QPushButton(tr("재현 검증"))
        self.verify_btn.clicked.connect(self.run_verify)
        row.addWidget(self.meta, 1)
        row.addWidget(self.verify_btn)
        self.verify_msg = QLabel()
        self.verify_msg.setTextFormat(Qt.PlainText)
        self.verify_msg.setWordWrap(True)
        lay.addWidget(self.head)
        lay.addLayout(row)
        lay.addWidget(self.verify_msg)

        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        cl = QVBoxLayout(card)
        self.item_line = QLabel()
        self.item_line.setTextFormat(Qt.PlainText)
        self.body = QLabel()
        self.body.setTextFormat(Qt.PlainText)     # 채팅 본문은 서식 없이 (HTML 해석 금지)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f = self.body.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        self.body.setFont(f)
        rr = QHBoxLayout()
        self.range_line = QLabel()
        self.range_line.setTextFormat(Qt.PlainText)
        self.loop_btn = QPushButton(tr("범위 반복 R"))
        self.loop_btn.setCheckable(True)
        self.loop_btn.clicked.connect(lambda _=False: self.toggle_loop())
        rr.addWidget(self.range_line, 1)
        rr.addWidget(self.loop_btn)
        cl.addWidget(self.item_line)
        cl.addWidget(self.body)
        cl.addLayout(rr)
        lay.addWidget(card)

        self.react_btn = QPushButton(tr("반응 있음 → 편집기 M"))
        self.react_btn.clicked.connect(lambda _=False: self.request_reaction_editor())
        lay.addWidget(self.react_btn)

        cr = QHBoxLayout()
        cr.addWidget(QLabel(tr("확신도")))
        self.conf_group = QButtonGroup(self)
        self.conf_group.setExclusive(True)
        self.conf_btns: dict[str, QPushButton] = {}
        for i, c in enumerate(CONFIDENCES, 1):
            b = QPushButton(tr(f"{CONF_LABELS[c]} Alt+{i}"))
            b.setCheckable(True)
            self.conf_group.addButton(b, i)
            self.conf_btns[c] = b
            cr.addWidget(b)
        lay.addLayout(cr)

        rl = QHBoxLayout()
        rl.addWidget(QLabel(tr("근거")))
        self.rationale = QLineEdit()
        self.rationale.setMaxLength(RATIONALE_MAX)
        self.rationale.setPlaceholderText(tr("시각을 넣어 한 줄 (예: 01:20:45 답 없이 다른 주제)"))
        self.time_btn = QPushButton(tr("현재 시각 Ctrl+T"))
        self.time_btn.clicked.connect(lambda _=False: self.insert_time())
        rl.addWidget(self.rationale, 1)
        rl.addWidget(self.time_btn)
        lay.addLayout(rl)

        sr = QHBoxLayout()
        self.unconf_btn = QPushButton(tr("반응 미확인 저장 Ctrl+5"))
        self.unconf_btn.clicked.connect(lambda _=False: self.judge_unconfirmed())
        self.undet_btn = QPushButton(tr("판단 불가로 저장"))
        self.undet_btn.clicked.connect(lambda _=False: self.judge_undeterminable())
        sr.addWidget(self.unconf_btn)
        sr.addWidget(self.undet_btn)
        lay.addLayout(sr)

        nr = QHBoxLayout()
        self.prev_btn = QPushButton(tr("◀ 이전 PgUp"))
        self.prev_btn.clicked.connect(lambda _=False: self.prev())
        self.next_btn = QPushButton(tr("다음 ▶ PgDown"))
        self.next_btn.clicked.connect(lambda _=False: self.next())
        self.pending_btn = QPushButton(tr("다음 대기"))
        self.pending_btn.clicked.connect(lambda _=False: self.next_pending())
        nr.addWidget(self.prev_btn)
        nr.addWidget(self.next_btn)
        nr.addWidget(self.pending_btn)
        lay.addLayout(nr)

        self.msg = QLabel()
        self.msg.setTextFormat(Qt.PlainText)
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        lay.addStretch(1)

    def _install_shortcuts(self) -> None:
        """키 문자열 → QShortcut 을 self.shortcuts 에 둔다. 작업대가 같은 키(M·R)를 창 단위로 잡으면 두 단축키가
        겹쳐(Qt 의 '애매한 단축키') 둘 다 동작하지 않으므로, 작업대는 set_play_keys(False) 로 이쪽 M·R 을 끄고
        자기 M·R 에서 request_reaction_editor·toggle_loop 를 부른다."""
        self.shortcuts: dict[str, QShortcut] = {}

        def sc(key: str, fn) -> None:
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
            self.shortcuts[key] = s

        sc("M", self.request_reaction_editor)
        sc("Ctrl+5", self.judge_unconfirmed)
        for i, c in enumerate(CONFIDENCES, 1):
            sc(f"Alt+{i}", lambda c=c: self.set_confidence(c))
        sc("Ctrl+T", self.insert_time)
        sc("R", self.toggle_loop)
        sc("PgDown", self.next)
        sc("PgUp", self.prev)
        sc("Esc", self._on_cancel)

    def set_play_keys(self, on: bool) -> None:
        """M(편집기 열기)·R(범위 반복) 단축키를 켜고 끈다. 작업대에 끼울 때는 끄고 작업대가 같은 일을 한다."""
        for k in ("M", "R"):
            self.shortcuts[k].setEnabled(bool(on))

    def set_reactions(self, reactions: Iterable[Mapping]) -> None:
        """판정 기록만 다시 받는다(작업대의 Ctrl+Z 철회 뒤 등). 지금 표본 자리는 그대로 둔다."""
        self._set_reactions(reactions)
        self._refresh()

    # ------------------------------------------------------------------ 상태 받기
    def load_context(self, *, manifest: Mapping | None, messages: Mapping[str, Mapping] | Iterable[Mapping] = (),
                     session_kind: str | None = None, phase: str | None = vis.SAMPLES,
                     session_id: str | None = None, reactions: Iterable[Mapping] = (),
                     viewer_no: Mapping[str, int] | None = None, player=None,
                     to_file: Callable[[int], int] | None = None, to_vod: Callable[[int], int] | None = None,
                     pre_roll_ms: int = DEFAULT_PRE_ROLL_MS, post_roll_ms: int = DEFAULT_POST_ROLL_MS,
                     autoplay: bool = True, start_at: str | None = None) -> None:
        """작업대 상태를 받는다.

        manifest: 이 계열의 가장 큰 sample_rev 매니페스트(sp.latest_of). None 이면 '표본 없음'.
        messages: chat_id → 채팅 dict (또는 dict 목록). 표본 채팅의 시각·본문·시청자만 쓴다.
        reactions: 자기 세션의 현재 유효 reaction 레코드. session_id 를 주면 다른 세션 레코드는 버린다(블라인드).
        player: MpvPlayer 계약 모양(seek·set_ab_loop·play·show_text·position_ms). 없으면 rangeRequested 만 낸다.
        to_file / to_vod: VOD ↔ 영상 파일 시각 변환(작업대 TimeMap). 없으면 같은 값.
        start_at: 이 chat_id 에서 시작. 없으면 첫 '대기' 표본.
        """
        scope = vis.files_to_open(session_kind or "", phase) if session_kind else vis.BLOCKED
        self._can_read = scope.can_read("samples")
        self._can_write = scope.can_write_label("reaction")
        self._session_id = session_id
        self._manifest = dict(manifest) if (manifest is not None and self._can_read) else None
        self._items = sorted((self._manifest or {}).get("items", []), key=lambda it: it["order"])
        wanted = {it["chat_id"] for it in self._items}
        if isinstance(messages, Mapping):
            src = (messages.get(c) for c in wanted)
        else:
            src = (m for m in messages if m.get("chat_id") in wanted)
        # 표본 채팅만, 보일 필드만 남긴다(엔진·출처 등 다른 키는 들고 있지 않는다)
        self._msgs = {m["chat_id"]: {k: m.get(k) for k in ("chat_id", "t_ms", "text", "viewer_id", "is_anonymous", "kind")}
                      for m in src if m}
        self._viewer_no = viewer_no
        self._player = player
        self._to_file = to_file or (lambda ms: ms)
        self._to_vod = to_vod or (lambda ms: ms)
        self._pre, self._post = int(pre_roll_ms), int(post_roll_ms)
        self._autoplay = autoplay
        self._last_verify = None
        self._set_reactions(reactions)
        self._idx = -1
        if self._items:
            idx = 0
            if start_at is not None:
                idx = next((i for i, it in enumerate(self._items) if it["chat_id"] == start_at), 0)
            else:
                idx = next((i for i, it in enumerate(self._items) if self._states.get(it["chat_id"]) != sp.JUDGED), 0)
            self.go(idx)
        else:
            self._refresh()
            self.currentChanged.emit(-1)

    def _set_reactions(self, reactions: Iterable[Mapping]) -> None:
        own = []
        for r in reactions:
            sid = r.get("session_id")
            if self._session_id is not None and sid is not None and sid != self._session_id:
                continue
            own.append(r)
        self._heads = {}
        if self._manifest is None:
            self._states, self._out_of_sample = {}, ()
            return
        self._states, self._out_of_sample = sp.item_states(self._manifest, own)
        ser = sp.series_of(self._manifest["sample_set_id"])
        for r in own:
            b = r.get("body") if isinstance(r.get("body"), Mapping) else None
            if not b or b.get("entry") != "from_sample" or not r.get("entity_id") or not r.get("id"):
                continue
            try:
                if sp.series_of((b.get("sample_ref") or {}).get("sample_set_id", "")) != ser:
                    continue
            except sp.SamplingError:
                continue
            self._heads[b["chat_id"]] = (r["entity_id"], r["id"])

    # ------------------------------------------------------------------ 조회
    @property
    def manifest(self) -> dict | None:
        return self._manifest

    def current_index(self) -> int:
        return self._idx

    def current_item(self) -> dict | None:
        return self._items[self._idx] if 0 <= self._idx < len(self._items) else None

    def state_of(self, chat_id: str) -> str | None:
        return self._states.get(chat_id)

    def out_of_sample(self) -> tuple[str, ...]:
        return self._out_of_sample

    def progress(self) -> sp.Progress | None:
        return sp.progress(self._manifest, self._states) if self._manifest is not None else None

    def review_range(self, item: Mapping | None = None) -> tuple[int, int] | None:
        """7-2 확인 범위(VOD ms): [채팅 − pre_roll, 채팅 + post_roll]. 0 아래는 0."""
        it = item or self.current_item()
        if it is None:
            return None
        t = int(it["t_ms"])
        return max(0, t - self._pre), t + self._post

    # ------------------------------------------------------------------ 이동
    def go(self, index: int) -> bool:
        if not self._items:
            return False
        index = max(0, min(len(self._items) - 1, int(index)))
        changed = index != self._idx
        self._idx = index
        self._reset_inputs()
        self._enter_item()
        self._refresh()
        if changed:
            self.currentChanged.emit(index)
        return True

    def next(self) -> bool:
        if self._idx + 1 < len(self._items):
            return self.go(self._idx + 1)
        return False

    def prev(self) -> bool:
        if self._idx > 0:
            return self.go(self._idx - 1)
        return False

    def next_pending(self) -> bool:
        n = len(self._items)
        for k in range(1, n + 1):
            j = (self._idx + k) % n
            if self._states.get(self._items[j]["chat_id"]) != sp.JUDGED:
                return self.go(j)
        return False

    def _enter_item(self) -> None:
        """들어가면 범위 반복·자동 재생(02 7-4)과 화면 위 문구."""
        rng = self.review_range()
        if rng is None:
            return
        a, b = rng
        self.rangeRequested.emit(a, b)
        self._loop_on = True
        p = self._player
        if p is None:
            return
        try:
            fa, fb = self._to_file(a), self._to_file(b)
            p.set_ab_loop(fa, fb)
            p.seek(fa)
            if self._autoplay:
                p.play()
            if hasattr(p, "show_text"):
                it = self.current_item()
                m = self._msgs.get(it["chat_id"]) if it else None
                p.show_text(f"{_viewer_text(m, self._viewer_no)}  {(m or {}).get('text') or ''}", 4000)
        except Exception:                              # 플레이어가 없어도 판정은 계속한다
            log.exception("큐 모드: 플레이어 조작 실패")

    def toggle_loop(self) -> None:
        rng = self.review_range()
        if rng is None:
            return
        self._loop_on = not self._loop_on
        p = self._player
        if p is not None:
            try:
                if self._loop_on:
                    p.set_ab_loop(self._to_file(rng[0]), self._to_file(rng[1]))
                    p.seek(self._to_file(rng[0]))
                else:
                    p.set_ab_loop(None, None)
            except Exception:
                log.exception("큐 모드: 반복 설정 실패")
        if self._loop_on:
            self.rangeRequested.emit(*rng)
        self.loop_btn.setChecked(self._loop_on)

    # ------------------------------------------------------------------ 판정
    def set_confidence(self, c: str) -> None:
        b = self.conf_btns.get(c)
        if b is not None and b.isEnabled():
            b.setChecked(True)

    def confidence(self) -> str | None:
        for c, b in self.conf_btns.items():
            if b.isChecked():
                return c
        return None

    def insert_time(self) -> None:
        """근거 칸에 지금 재생 위치(VOD, h:mm:ss)를 넣는다. 플레이어가 없으면 표본 채팅 시각."""
        ms = None
        if self._player is not None:
            try:
                ms = self._to_vod(int(self._player.position_ms()))
            except Exception:
                ms = None
        if ms is None:
            it = self.current_item()
            ms = int(it["t_ms"]) if it else None
        if ms is None:
            return
        self.rationale.insert(tc.fmt_time(max(0, ms), ms_digits=False, always_hours=True) + " ")
        self.rationale.setFocus()

    def _blocked_reason(self) -> str | None:
        if self._manifest is None:
            return tr("표본이 없습니다")
        if not self._can_write:
            return tr("이 세션은 반응 라벨을 저장할 수 없습니다")
        if self.current_item() is None:
            return tr("표본을 고르세요")
        return None

    def request_reaction_editor(self) -> dict | None:
        """'반응 있음' — 편집기 열기 요청만 한다(저장하지 않음, 7-6 의 M)."""
        why = self._blocked_reason()
        if why:
            self._say(why)
            return None
        it = self.current_item()
        ctx = editor_context(self._manifest, it, self._heads.get(it["chat_id"]))
        self.reactionEditorRequested.emit(ctx)
        return ctx

    def judge_unconfirmed(self) -> dict | None:
        """반응 미확인 저장 (확신도는 고른 것. 판단 불가를 골랐으면 판단 불가로 저장)."""
        c = self.confidence()
        if c is None:
            self._say(tr("확신도를 고르세요 (Alt+1 확실함 · Alt+2 아마도 · Alt+3 판단 불가)"))
            return None
        return self._save_unconfirmed(c)

    def judge_undeterminable(self) -> dict | None:
        self.set_confidence("undeterminable")
        return self._save_unconfirmed("undeterminable")

    def _save_unconfirmed(self, conf: str) -> dict | None:
        why = self._blocked_reason()
        if why:
            self._say(why)
            return None
        it = self.current_item()
        try:
            draft = unconfirmed_draft(self._manifest, it, confidence=conf, rationale=self.rationale.text(),
                                      head=self._heads.get(it["chat_id"]))
        except ValueError as e:
            self._say(tr("저장하지 않았습니다: {0}").format(str(e)))
            if not self.rationale.text().strip():
                self.rationale.setFocus()
            return None
        try:
            rec = self._save(draft)
        except Exception as e:                        # store 의 거부(형식·읽기 전용 등)는 문장으로 보인다
            log.warning("큐 모드 저장 거부: %s", type(e).__name__)
            self._say(tr("저장하지 못했습니다: {0}").format(str(e)))
            return None
        self.mark_saved(rec if isinstance(rec, Mapping) else {"kind": "reaction", "body": draft["body"]})
        return rec

    def mark_saved(self, record: Mapping) -> None:
        """반응 라벨이 저장됐다(여기서 저장했거나 편집기가 저장함) → 판정함 표시하고 다음 표본으로(02 7-4)."""
        b = record.get("body") if isinstance(record.get("body"), Mapping) else record
        cid = b.get("chat_id")
        if cid not in self._states:
            self._refresh()
            return
        self._states[cid] = sp.JUDGED
        if record.get("entity_id") and record.get("id"):
            self._heads[cid] = (record["entity_id"], record["id"])
        self._say(tr("저장함"))
        if all(v == sp.JUDGED for v in self._states.values()):
            self._refresh()
            self.allJudged.emit()
            return
        if not self.next():
            self.next_pending()
        else:
            self._refresh()

    def _on_cancel(self) -> None:
        try:
            self._cancel()
        except Exception:
            log.exception("큐 모드 cancel 콜백 실패")

    # ------------------------------------------------------------------ 재현 검증
    def run_verify(self) -> sp.Verification | None:
        if self._verify is None or self._manifest is None:
            self.verify_msg.setText(tr("재현 검증을 할 수 없습니다 (검증 입력 없음)"))
            return None
        try:
            v = self._verify(self._manifest)
        except Exception as e:
            log.warning("재현 검증 오류: %s", type(e).__name__)
            v = sp.Verification(False, (tr("검증 중 오류: {0}").format(type(e).__name__),))
        self._last_verify = v
        self.verify_msg.setText(v.summary())
        self.statusChanged.emit(v.summary())
        return v

    # ------------------------------------------------------------------ 그리기
    def _reset_inputs(self) -> None:
        self.conf_group.setExclusive(False)
        for b in self.conf_btns.values():
            b.setChecked(False)
        self.conf_group.setExclusive(True)
        self.rationale.clear()
        self.msg.clear()

    def _say(self, text: str) -> None:
        self.msg.setText(text)
        self.statusChanged.emit(text)

    def _refresh(self) -> None:
        m = self._manifest
        has = m is not None and bool(self._items)
        if m is None:
            self.head.setText(tr("표본 없음") if self._can_read else tr("이 세션에서는 표본을 볼 수 없습니다"))
            self.meta.setText("")
        else:
            s = m["source_segment"]
            pr = sp.progress(m, self._states)
            self.head.setText(tr("표본: 구간 {0} ~ {1}   진행 {2} / {3}   적격 {4:,}").format(
                tc.fmt_time(s["start_ms"], ms_digits=False, always_hours=True),
                tc.fmt_time(s["end_ms"], ms_digits=False, always_hours=True),
                pr.done, pr.denominator_text(), int(m["eligible"]["size"])))
            seed, draw_no = sp.parse_seed_material(m["seed_material"])
            ex = m["excluded"]
            census = tr(" · 전수") if m.get("census") else ""
            self.meta.setText(tr("시드 {0}  draw {1}  rev {2}  τ {3}{4}  완충 {5}·후원 등 {6}·시각 이상 {7} 은 적격 밖").format(
                _short(seed), draw_no, m["sample_rev"], _short(m.get("tau_key")), census,
                ex["buffer"], ex["non_text"], ex["invalid_time"]))
        self.verify_btn.setEnabled(self._verify is not None and m is not None)
        it = self.current_item()
        if it is None:
            self.item_line.setText(tr("표본이 없습니다") if m is not None else "")
            self.body.setText("")
            self.range_line.setText("")
        else:
            msg = self._msgs.get(it["chat_id"])
            state = tr("판정함") if self._states.get(it["chat_id"]) == sp.JUDGED else tr("대기")
            self.item_line.setText(tr("표본 #{0} · {1} · {2} · {3}").format(
                it["order"], tc.fmt_time(it["t_ms"], ms_digits=False, always_hours=True),
                _viewer_text(msg, self._viewer_no), state))
            text = (msg or {}).get("text")
            self.body.setText(f"“{text}”" if isinstance(text, str) else tr("(채팅 본문을 찾지 못했습니다)"))
            a, b = self.review_range(it)
            self.range_line.setText(tr("확인 범위 {0} ~ {1}").format(
                tc.fmt_time(a, ms_digits=False, always_hours=True), tc.fmt_time(b, ms_digits=False, always_hours=True)))
        self.loop_btn.setChecked(self._loop_on and it is not None)
        can = has and self._can_write and it is not None
        for w in (self.react_btn, self.unconf_btn, self.undet_btn, self.rationale, self.time_btn,
                  *self.conf_btns.values()):
            w.setEnabled(can)
        self.loop_btn.setEnabled(it is not None)
        self.prev_btn.setEnabled(has and self._idx > 0)
        self.next_btn.setEnabled(has and self._idx + 1 < len(self._items))
        self.pending_btn.setEnabled(has and any(v != sp.JUDGED for v in self._states.values()))
