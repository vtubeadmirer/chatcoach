"""출처 판정 — 대상 채팅을 한 건씩 실제·가상·판별 불가로 정함 (06 4-7, 03 6-11·12-1, 08 D-12 6).

화면 (작업대 오른쪽 칸에 끼운다. 작업대·메뉴 연결은 통합 담당이 한다. 큐 모드(queue_view)와 같은 한 건씩 흐름)
  ┌ 머리 ───────────────────────────────────────────────────────────┐
  │ 출처 판정: 대상 120   판정 37 (실제 30 · 가상 5 · 판별 불가 2)   자동 4  │
  │ 대상 파일 OT-20261021-3fa9                                       │
  ├ 대상 카드 ──────────────────────────────────────────────────────┤
  │ 대상 #38 · 01:20:41 · 시청자 0233 · 대기                          │
  │ "본문"                                                           │
  │ [자동] 생성 기록 대조 → 가상 (점수 0.93 · 2위 0.41) [자동 결과 받기 Ctrl+G] │
  │ 확인 범위 01:20:31 ~ 01:21:11 [범위 반복 R]                        │
  ├ 판정 ──────────────────────────────────────────────────────────┤
  │ 출처 [실제 Ctrl+1] [가상 Ctrl+2] [판별 불가 Ctrl+3]                 │
  │ 확신도 [확실함 Alt+1] [아마도 Alt+2]   (판별 불가면 적지 않음)       │
  │ 근거 방법 [수동 ▾]                                                │
  │ 근거 [__________________] [현재 시각 Ctrl+T]                     │
  │ [저장 Ctrl+Enter]                                                │
  │ [◀ 이전 PgUp]  [다음 ▶ PgDown]  [다음 대기]                         │
  └────────────────────────────────────────────────────────────────┘

규칙
  - 대상은 origin/ 의 대상 파일(core.origin 의 형식)이 정한 목록뿐이다. 이 화면에 대상 고르기·더하기 기능은 없다.
  - 블라인드(06 7장, 03 12-1): 출처 판정 세션은 정해진 것만 본다 — 대상 채팅의 시각·시청자 번호·본문, 대상 파일의
    자동 대조 결과, 자기 세션의 출처 라벨. 다른 세션 라벨·표본·반응·엔진 결과·원 닉네임은 받지 않는다.
    세션 종류가 origin_review 가 아니면(visibility 의 origin 이 RW 가 아니면) 아무것도 들고 있지 않는다.
  - '자동' 은 표시만 한다(D-12 6). 검토자가 [자동 결과 받기] 로 받으면 그 출처·method·generation_ref·점수를 채우고,
    확신도·근거는 검토자가 적는다. 받은 뒤 다른 출처를 고르면 method 는 수동으로 돌아간다.
  - 판정은 save(draft) 콜백(store 경유)으로만 저장한다. 파일은 대상 파일 읽기·쓰기 도우미만 둔다(통합 쪽이
    visibility(origin = rw)와 프로젝트 잠금을 확인하고 부른다). 저장하면 다음 대기 대상으로 간다.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Callable, Iterable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from app.core import origin as og
from app.core import timecode as tc
from app.core import visibility as vis
from app.core.validate import StrictJsonError, load_json_strict
from app.i18n import tr
from app.ui.queue_view import DEFAULT_POST_ROLL_MS, DEFAULT_PRE_ROLL_MS, _viewer_text

log = logging.getLogger(__name__)

TARGETS_MAX_BYTES = 32 << 20
JUDGED = "judged"
PENDING = "pending"
# 화면에서 고를 수 있는 근거 방법. 생성 기록 대조는 자동 결과를 받을 때만, OCR 대조는 OCR 자료가 생길 때까지 없음.
PICKABLE_METHODS = (og.M_MANUAL, og.M_VIEWER_ID, og.M_NONE)


# ---------------------------------------------------------------------------- 대상 파일 (통합 쪽 콜백용)
def targets_path(origin_dir: str, target_id: str) -> str:
    if not isinstance(target_id, str) or not og.TARGET_ID_RE.match(target_id):
        raise ValueError("대상 파일 ID 형식이 아닙니다")         # 경로에 들어가는 이름 검사(S-46)
    return os.path.join(origin_dir, f"{target_id}.json")


def read_targets_file(path: str) -> og.OriginTargets:
    """엄격 JSON 으로 읽고 core.origin.parse_targets 로 검사한다. 파일 이름이 target_id 와 다르면 ValueError."""
    with open(path, "rb") as f:
        data = f.read(TARGETS_MAX_BYTES + 1)
    obj = load_json_strict(data, TARGETS_MAX_BYTES)
    t = og.parse_targets(obj)
    if os.path.basename(path) != f"{t.target_id}.json":
        raise ValueError("대상 파일 이름이 target_id 와 다릅니다")
    return t


def list_targets(origin_dir: str) -> list[og.OriginTargets]:
    """origin/ 의 대상 파일(OT-*.json) 전부. 읽지 못한 파일은 경고 로그(이름·오류 종류만)를 남기고 건너뛴다."""
    out: list[og.OriginTargets] = []
    try:
        names = sorted(os.listdir(origin_dir))
    except OSError:
        return out
    for name in names:
        if not (name.startswith("OT-") and name.endswith(".json")):
            continue
        try:
            out.append(read_targets_file(os.path.join(origin_dir, name)))
        except (OSError, ValueError, StrictJsonError) as e:
            log.warning("출처 대상 파일을 읽지 못했습니다: %s (%s)", name, type(e).__name__)
    return out


def write_targets_file(origin_dir: str, obj: Mapping) -> str:
    """origin/<target_id>.json 을 새로 쓴다(불변 — 이미 있으면 FileExistsError). 쓰기 전에 parse_targets 로 검사한다."""
    t = og.parse_targets(obj)
    path = targets_path(origin_dir, t.target_id)
    if os.path.exists(path):
        raise FileExistsError(path)
    os.makedirs(origin_dir, exist_ok=True)
    data = (json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if os.path.exists(path):
        os.remove(tmp)
        raise FileExistsError(path)
    os.replace(tmp, path)
    return path


def _score_text(h: og.AutoHint) -> str:
    parts = []
    if h.score is not None:
        parts.append(tr("점수 {0:.2f}").format(h.score))
    if h.second_score is not None:
        parts.append(tr("2위 {0:.2f}").format(h.second_score))
    return f" ({' · '.join(parts)})" if parts else ""


# ---------------------------------------------------------------------------- 화면
class OriginView(QWidget):
    """출처 판정 한 건씩. 폼 공통 모양: 생성자에 save(draft) -> record, cancel(), 상태는 load_context(...)."""

    currentChanged = Signal(int)          # 지금 대상 index(0부터). 없으면 -1
    rangeRequested = Signal(int, int)     # 확인 범위 (VOD ms)
    statusChanged = Signal(str)
    allJudged = Signal()
    # 실은 대상 파일 목록(target_refs() 모양). 출처 세션이 어느 대상 파일을 봤는지 통합 쪽이 기록하라고 낸다(15 5-2 의 7번)
    targetsLoaded = Signal(list)

    def __init__(self, save: Callable[[dict], dict], cancel: Callable[[], None], parent=None):
        super().__init__(parent)
        self._save = save
        self._cancel = cancel
        self._targets: og.CombinedTargets | None = None
        self._refs: list[dict] = []
        self._items: list[og.TargetItem] = []
        self._msgs: dict[str, Mapping] = {}
        self._viewer_no: Mapping[str, int] | None = None
        self._heads: dict[str, dict] = {}
        self._dup_labels = 0
        self._idx = -1
        self._player = None
        self._to_file: Callable[[int], int] = lambda ms: ms
        self._to_vod: Callable[[int], int] = lambda ms: ms
        self._pre = DEFAULT_PRE_ROLL_MS
        self._post = DEFAULT_POST_ROLL_MS
        self._autoplay = True
        self._loop_on = False
        self._allowed = False
        self._session_id: str | None = None
        self._auto_taken = False            # 지금 대상에서 [자동 결과 받기] 를 눌렀나
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
        self.meta = QLabel()
        self.meta.setTextFormat(Qt.PlainText)
        self.meta.setWordWrap(True)
        # 대상 파일이 둘 이상이면 합쳐서 판정한다 — 경고 1줄(15 5-2 의 6·8번: 1차 표본이 바뀌면 옛 대상 파일이 남는다)
        self.multi_warn = QLabel()
        self.multi_warn.setTextFormat(Qt.PlainText)
        self.multi_warn.setWordWrap(True)
        self.multi_warn.setObjectName("multi_targets_warning")
        self.multi_warn.setStyleSheet("color: #b9770e;")
        self.multi_warn.hide()
        lay.addWidget(self.head)
        lay.addWidget(self.meta)
        lay.addWidget(self.multi_warn)

        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        cl = QVBoxLayout(card)
        self.item_line = QLabel()
        self.item_line.setTextFormat(Qt.PlainText)
        self.item_line.setWordWrap(True)
        self.body = QLabel()
        self.body.setTextFormat(Qt.PlainText)        # 채팅 본문은 서식 없이 (HTML 해석 금지)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f = self.body.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        self.body.setFont(f)
        ar = QHBoxLayout()
        self.auto_line = QLabel()
        self.auto_line.setTextFormat(Qt.PlainText)
        self.auto_line.setWordWrap(True)
        self.auto_line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.auto_btn = QPushButton(tr("자동 결과 받기 Ctrl+G"))
        # 연결은 묶인 메서드로만 한다(이 파일 전체). self 를 잡은 람다·partial 은 Qt 연결이 강하게 들고 있어 화면이
        # 지워지지 않고 프로세스 끝(QApplication 뒤)까지 남는다 — 종료 때 접근 위반의 원인(D-18 10)
        self.auto_btn.clicked.connect(self._on_auto_btn)
        ar.addWidget(self.auto_line, 1)
        ar.addWidget(self.auto_btn)
        rr = QHBoxLayout()
        self.range_line = QLabel()
        self.range_line.setTextFormat(Qt.PlainText)
        self.loop_btn = QPushButton(tr("범위 반복 R"))
        self.loop_btn.setCheckable(True)
        self.loop_btn.clicked.connect(self._on_loop_btn)
        rr.addWidget(self.range_line, 1)
        rr.addWidget(self.loop_btn)
        cl.addWidget(self.item_line)
        cl.addWidget(self.body)
        cl.addLayout(ar)
        cl.addLayout(rr)
        lay.addWidget(card)

        orow = QHBoxLayout()
        orow.addWidget(QLabel(tr("출처")))
        self.origin_group = QButtonGroup(self)
        self.origin_group.setExclusive(True)
        self.origin_btns: dict[str, QPushButton] = {}
        for i, o in enumerate(og.ORIGINS, 1):
            b = QPushButton(tr(f"{og.ORIGIN_KO[o]} Ctrl+{i}"))
            b.setCheckable(True)
            self.origin_group.addButton(b, i)       # 눌림은 idClicked 하나로 받는다(아래)
            self.origin_btns[o] = b
            orow.addWidget(b)
        self.origin_group.idClicked.connect(self._on_origin_id)
        lay.addLayout(orow)

        crow = QHBoxLayout()
        crow.addWidget(QLabel(tr("확신도")))
        self.conf_group = QButtonGroup(self)
        self.conf_group.setExclusive(True)
        self.conf_btns: dict[str, QPushButton] = {}
        for i, c in enumerate(og.CONFIDENCES, 1):
            b = QPushButton(tr(f"{og.CONF_KO[c]} Alt+{i}"))
            b.setCheckable(True)
            self.conf_group.addButton(b, i)
            self.conf_btns[c] = b
            crow.addWidget(b)
        self.conf_hint = QLabel()
        self.conf_hint.setTextFormat(Qt.PlainText)
        crow.addWidget(self.conf_hint, 1)
        lay.addLayout(crow)

        mrow = QHBoxLayout()
        mrow.addWidget(QLabel(tr("근거 방법")))
        self.method_box = QComboBox()
        self._fill_methods(None)
        mrow.addWidget(self.method_box, 1)
        lay.addLayout(mrow)

        rl = QHBoxLayout()
        rl.addWidget(QLabel(tr("근거")))
        self.rationale = QLineEdit()
        self.rationale.setMaxLength(og.RATIONALE_MAX)
        self.rationale.setPlaceholderText(tr("시각을 넣어 한 줄 (예: 01:20:45 생성 기록에 같은 문구, 3초 차)"))
        self.time_btn = QPushButton(tr("현재 시각 Ctrl+T"))
        self.time_btn.clicked.connect(self._on_time_btn)
        rl.addWidget(self.rationale, 1)
        rl.addWidget(self.time_btn)
        lay.addLayout(rl)

        self.save_btn = QPushButton(tr("저장 Ctrl+Enter"))
        self.save_btn.clicked.connect(self._on_save_btn)
        lay.addWidget(self.save_btn)

        nr = QHBoxLayout()
        self.prev_btn = QPushButton(tr("◀ 이전 PgUp"))
        self.prev_btn.clicked.connect(self._on_prev_btn)
        self.next_btn = QPushButton(tr("다음 ▶ PgDown"))
        self.next_btn.clicked.connect(self._on_next_btn)
        self.pending_btn = QPushButton(tr("다음 대기"))
        self.pending_btn.clicked.connect(self._on_pending_btn)
        nr.addWidget(self.prev_btn)
        nr.addWidget(self.next_btn)
        nr.addWidget(self.pending_btn)
        lay.addLayout(nr)

        self.msg = QLabel()
        self.msg.setTextFormat(Qt.PlainText)
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        lay.addStretch(1)

    # 버튼 받기 (clicked(bool) 의 bool 은 버린다)
    def _on_auto_btn(self, _checked: bool = False) -> None:
        self.take_auto()

    def _on_loop_btn(self, _checked: bool = False) -> None:
        self.toggle_loop()

    def _on_origin_id(self, i: int) -> None:
        if 1 <= i <= len(og.ORIGINS):
            self._on_origin_clicked(og.ORIGINS[i - 1])

    def _on_time_btn(self, _checked: bool = False) -> None:
        self.insert_time()

    def _on_save_btn(self, _checked: bool = False) -> None:
        self.save_current()

    def _on_prev_btn(self, _checked: bool = False) -> None:
        self.prev()

    def _on_next_btn(self, _checked: bool = False) -> None:
        self.next()

    def _on_pending_btn(self, _checked: bool = False) -> None:
        self.next_pending()

    def _fill_methods(self, hint: og.AutoHint | None) -> None:
        self.method_box.blockSignals(True)
        self.method_box.clear()
        methods = list(PICKABLE_METHODS)
        if hint is not None and hint.method not in methods:
            methods.insert(0, hint.method)
        for m in methods:
            self.method_box.addItem(tr(og.METHOD_KO[m]), m)
        self.method_box.setCurrentIndex(self.method_box.findData(og.M_MANUAL))
        self.method_box.blockSignals(False)

    def _install_shortcuts(self) -> None:
        """작업대가 같은 키(R)를 창 단위로 잡으면 겹치므로 set_play_keys(False) 로 이쪽 R 을 끈다(큐 모드와 같음)."""
        self.shortcuts: dict[str, QShortcut] = {}

        def sc(key: str, fn) -> None:
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
            self.shortcuts[key] = s

        # 값은 단축키 이름표(objectName)에 두고 한 메서드가 sender() 로 읽는다(람다 없이)
        for i, o in enumerate(og.ORIGINS, 1):
            sc(f"Ctrl+{i}", self._on_origin_shortcut)
            self.shortcuts[f"Ctrl+{i}"].setObjectName(o)
        for i, c in enumerate(og.CONFIDENCES, 1):
            sc(f"Alt+{i}", self._on_conf_shortcut)
            self.shortcuts[f"Alt+{i}"].setObjectName(c)
        sc("Ctrl+G", self.take_auto)
        sc("Ctrl+T", self.insert_time)
        sc("Ctrl+Return", self.save_current)
        sc("Ctrl+Enter", self.save_current)
        sc("R", self.toggle_loop)
        sc("PgDown", self.next)
        sc("PgUp", self.prev)
        sc("Esc", self._on_cancel)

    def _on_origin_shortcut(self) -> None:
        s = self.sender()
        if s is not None and s.objectName() in og.ORIGINS:
            self.set_origin(s.objectName())

    def _on_conf_shortcut(self) -> None:
        s = self.sender()
        if s is not None and s.objectName() in og.CONFIDENCES:
            self.set_confidence(s.objectName())

    def set_play_keys(self, on: bool) -> None:
        self.shortcuts["R"].setEnabled(bool(on))

    # ------------------------------------------------------------------ 상태 받기
    def load_context(self, *, session_kind: str | None, session_id: str | None = None,
                     targets: Iterable[og.OriginTargets | Mapping] = (),
                     messages: Mapping[str, Mapping] | Iterable[Mapping] = (),
                     labels: Iterable[Mapping] = (), viewer_no: Mapping[str, int] | None = None, player=None,
                     to_file: Callable[[int], int] | None = None, to_vod: Callable[[int], int] | None = None,
                     pre_roll_ms: int = DEFAULT_PRE_ROLL_MS, post_roll_ms: int = DEFAULT_POST_ROLL_MS,
                     autoplay: bool = True, start_at: str | None = None) -> None:
        """작업대 상태를 받는다.

        session_kind: origin_review 가 아니면 대상·채팅·라벨을 들고 있지 않는다(블라인드).
        targets: origin/ 대상 파일(OriginTargets 또는 그 dict, list_targets 결과). 여럿이면 target_id 순으로 합친다.
        messages: chat_id → 채팅 dict (또는 dict 목록). 대상 채팅의 시각·본문·시청자만 쓴다.
        labels: 자기 세션의 현재 유효 출처 라벨(resolve 결과). session_id 를 주면 다른 세션 레코드는 버린다.
        player: MpvPlayer 계약 모양(seek·set_ab_loop·play·show_text·position_ms). 없으면 rangeRequested 만 낸다.
        start_at: 이 chat_id 에서 시작. 없으면 첫 대기 대상.
        """
        scope = vis.files_to_open(session_kind or "") if session_kind else vis.BLOCKED
        self._allowed = scope.access("origin") == vis.RW and scope.can_write_label("origin")
        self._session_id = session_id
        self._player = player if self._allowed else None
        self._to_file = to_file or (lambda ms: ms)
        self._to_vod = to_vod or (lambda ms: ms)
        self._pre, self._post = int(pre_roll_ms), int(post_roll_ms)
        self._autoplay = autoplay
        self._viewer_no = viewer_no if self._allowed else None
        if not self._allowed:
            self._targets, self._items, self._msgs, self._heads = None, [], {}, {}
            self._refs = []
            self._idx = -1
            self._refresh()
            self.currentChanged.emit(-1)
            self.targetsLoaded.emit([])
            return
        parsed: list[og.OriginTargets] = []
        for t in targets:
            if isinstance(t, og.OriginTargets):
                parsed.append(t)
            else:
                try:
                    parsed.append(og.parse_targets(t))
                except og.OriginError as e:
                    log.warning("출처 대상 파일 형식 오류로 건너뜀 (%s)", type(e).__name__)
        self._targets = og.combine_targets(parsed) if parsed else None
        self._refs = [{"target_id": t.target_id, "chat_ids_sha256": t.chat_ids_sha256, "basis": t.basis,
                       "count": len(t.items)} for t in sorted(parsed, key=lambda x: x.target_id)]
        if len(self._refs) > 1:
            log.warning("출처 판정: 대상 파일 %d개를 합쳐 씀 (겹친 대상 %d)", len(self._refs),
                        self._targets.duplicates if self._targets else 0)
        self.targetsLoaded.emit([dict(r) for r in self._refs])
        self._items = [it for _, it in self._targets.items] if self._targets else []
        wanted = {it.chat_id for it in self._items}
        if isinstance(messages, Mapping):
            src = (messages.get(c) for c in wanted)
        else:
            src = (m for m in messages if isinstance(m, Mapping) and m.get("chat_id") in wanted)
        # 대상 채팅만, 보일 필드만 남긴다(엔진·반응·다른 키는 들고 있지 않는다)
        self._msgs = {m["chat_id"]: {k: m.get(k) for k in ("chat_id", "t_ms", "text", "viewer_id", "is_anonymous")}
                      for m in src if m}
        self._set_labels(labels)
        self._idx = -1
        if self._items:
            idx = 0
            if start_at is not None:
                idx = next((i for i, it in enumerate(self._items) if it.chat_id == start_at), 0)
            else:
                idx = next((i for i, it in enumerate(self._items) if it.chat_id not in self._heads), 0)
            self.go(idx)
        else:
            self._refresh()
            self.currentChanged.emit(-1)

    def set_labels(self, labels: Iterable[Mapping]) -> None:
        """판정 기록만 다시 받는다(철회 뒤 등). 지금 자리는 그대로."""
        self._set_labels(labels)
        self._refresh()

    def _set_labels(self, labels: Iterable[Mapping]) -> None:
        if not self._allowed:
            self._heads, self._dup_labels = {}, 0
            return
        heads, dup = og.heads_by_chat(labels, session_id=self._session_id)
        wanted = {it.chat_id for it in self._items}
        self._heads = {c: r for c, r in heads.items() if c in wanted}
        self._dup_labels = dup

    # ------------------------------------------------------------------ 조회
    def targets(self) -> og.CombinedTargets | None:
        return self._targets

    def target_ids(self) -> list[str]:
        """지금 실은 대상 파일 ID(target_id 순). 대상이 없거나 이 세션에서 볼 수 없으면 빈 목록."""
        return [r["target_id"] for r in self._refs]

    def target_refs(self) -> list[dict]:
        """지금 실은 대상 파일마다 {target_id, chat_ids_sha256, basis, count} (숫자·ID 만, 채팅 본문 없음).
        출처 세션의 session.open·잠금에 남길 값의 모양이다(15 5-2 의 7번 — 기록 칸은 스키마·잠금 쪽 일)."""
        return [dict(r) for r in self._refs]

    def multi_targets_warning(self) -> str:
        """대상 파일이 둘 이상이면 경고 문장 1줄, 아니면 빈 문자열(15 5-2 의 6·8번)."""
        n = len(self._refs)
        if n < 2:
            return ""
        dup = self._targets.duplicates if self._targets is not None else 0
        return tr("⚠ 대상 파일이 {0}개라 합쳐서 판정합니다 (겹친 대상 {1}건은 한 번만). 1차 표본이 바뀌었으면 "
                  "옛 대상 파일이 남아 있을 수 있습니다 — 판정할 파일이 맞는지 확인하세요").format(n, dup)

    def current_index(self) -> int:
        return self._idx

    def current_item(self) -> og.TargetItem | None:
        return self._items[self._idx] if 0 <= self._idx < len(self._items) else None

    def state_of(self, chat_id: str) -> str | None:
        if not any(it.chat_id == chat_id for it in self._items):
            return None
        return JUDGED if chat_id in self._heads else PENDING

    def progress(self) -> og.Progress | None:
        return og.progress(self._targets, self._heads) if self._targets is not None else None

    def review_range(self, item: og.TargetItem | None = None) -> tuple[int, int] | None:
        it = item or self.current_item()
        if it is None:
            return None
        m = self._msgs.get(it.chat_id)
        t = (m or {}).get("t_ms")
        if not isinstance(t, int) or isinstance(t, bool):
            return None
        return max(0, t - self._pre), t + self._post

    def origin(self) -> str | None:
        for o, b in self.origin_btns.items():
            if b.isChecked():
                return o
        return None

    def confidence(self) -> str | None:
        for c, b in self.conf_btns.items():
            if b.isChecked():
                return c
        return None

    def method(self) -> str:
        return self.method_box.currentData() or og.M_MANUAL

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
        return self.go(self._idx + 1) if self._idx + 1 < len(self._items) else False

    def prev(self) -> bool:
        return self.go(self._idx - 1) if self._idx > 0 else False

    def next_pending(self) -> bool:
        n = len(self._items)
        for k in range(1, n + 1):
            j = (self._idx + k) % n
            if self._items[j].chat_id not in self._heads:
                return self.go(j)
        return False

    def _enter_item(self) -> None:
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
        except Exception:                               # 플레이어가 없어도 판정은 계속한다
            log.exception("출처 판정: 플레이어 조작 실패")

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
                log.exception("출처 판정: 반복 설정 실패")
        if self._loop_on:
            self.rangeRequested.emit(*rng)
        self.loop_btn.setChecked(self._loop_on)

    # ------------------------------------------------------------------ 입력
    def set_origin(self, o: str) -> None:
        b = self.origin_btns.get(o)
        if b is not None and b.isEnabled():
            b.setChecked(True)
            self._on_origin_clicked(o)

    def _on_origin_clicked(self, o: str) -> None:
        it = self.current_item()
        hint = it.auto if it else None
        if self._auto_taken and hint is not None and o != hint.origin:
            self._auto_taken = False
            self.method_box.setCurrentIndex(self.method_box.findData(og.M_MANUAL))
            self._say(tr("자동 결과와 다른 출처를 골라 근거 방법을 수동으로 바꿨습니다"))
        self._refresh_conf()

    def set_confidence(self, c: str) -> None:
        b = self.conf_btns.get(c)
        if b is not None and b.isEnabled():
            b.setChecked(True)

    def set_method(self, m: str) -> bool:
        i = self.method_box.findData(m)
        if i < 0:
            return False
        self.method_box.setCurrentIndex(i)
        return True

    def take_auto(self) -> bool:
        """[자동 결과 받기] — 자동 대조 결과의 출처·method 를 채운다. 저장은 하지 않는다(확신도·근거는 검토자)."""
        it = self.current_item()
        if it is None or it.auto is None or not self._allowed:
            self._say(tr("이 대상에는 자동 대조 결과가 없습니다"))
            return False
        h = it.auto
        self.origin_btns[h.origin].setChecked(True)
        self.set_method(h.method)
        self._auto_taken = True
        if not self.rationale.text().strip():
            if h.generation_ref:
                self.rationale.setText(tr("자동: {0} {1}#{2}").format(
                    tr(og.METHOD_KO[h.method]), h.generation_ref["file"], h.generation_ref["gen_id"]))
            else:
                self.rationale.setText(tr("자동: {0}").format(tr(og.METHOD_KO[h.method])))
        self._refresh_conf()
        self._say(tr("자동 결과를 채웠습니다 — 확신도를 고르고 근거를 확인한 뒤 저장하세요"))
        return True

    def insert_time(self) -> None:
        ms = None
        if self._player is not None:
            try:
                ms = self._to_vod(int(self._player.position_ms()))
            except Exception:
                ms = None
        if ms is None:
            it = self.current_item()
            m = self._msgs.get(it.chat_id) if it else None
            t = (m or {}).get("t_ms")
            ms = t if isinstance(t, int) else None
        if ms is None:
            return
        self.rationale.insert(tc.fmt_time(max(0, ms), ms_digits=False, always_hours=True) + " ")
        self.rationale.setFocus()

    # ------------------------------------------------------------------ 저장
    def _blocked_reason(self) -> str | None:
        if not self._allowed:
            return tr("이 세션에서는 출처 판정을 할 수 없습니다")
        if self._targets is None:
            return tr("출처 판정 대상이 없습니다 (origin\\ 의 대상 파일)")
        if self.current_item() is None:
            return tr("대상을 고르세요")
        return None

    def build_draft(self) -> dict:
        """지금 입력 → 초안. 규칙에 맞지 않으면 og.OriginError."""
        it = self.current_item()
        o = self.origin()
        if o is None:
            raise og.OriginError(tr("출처를 고르세요 (Ctrl+1 실제 · Ctrl+2 가상 · Ctrl+3 판별 불가)"))
        m = self.method()
        hint = it.auto
        head = self._heads.get(it.chat_id)
        head_t = (head["entity_id"], head["id"]) if head and head.get("entity_id") and head.get("id") else None
        conf = None if o == og.UNDETERMINABLE else self.confidence()
        if o != og.UNDETERMINABLE and conf is None:
            raise og.OriginError(tr("확신도를 고르세요 (Alt+1 확실함 · Alt+2 아마도)"))
        if m in og.AUTO_METHODS and hint is not None and hint.method == m and hint.origin == o and self._auto_taken:
            return og.draft_from_auto(hint, chat_id=it.chat_id, rationale=self.rationale.text(), confidence=conf,
                                      head=head_t, targets=self._targets)
        if m == og.M_GENERATION:                        # 자동 결과를 받지 않고는 생성 기록 항목이 없다
            raise og.OriginError(tr("생성 기록 대조는 [자동 결과 받기] 로만 고를 수 있습니다"))
        return og.origin_draft(chat_id=it.chat_id, origin=o, rationale=self.rationale.text(), confidence=conf,
                               method=m, head=head_t, targets=self._targets)

    def save_current(self) -> dict | None:
        why = self._blocked_reason()
        if why:
            self._say(why)
            return None
        try:
            draft = self.build_draft()
        except og.OriginError as e:
            self._say(tr("저장하지 않았습니다: {0}").format(str(e)))
            if not self.rationale.text().strip():
                self.rationale.setFocus()
            return None
        try:
            rec = self._save(draft)
        except Exception as e:                          # store 의 거부(형식·읽기 전용 등)는 문장으로 보인다
            log.warning("출처 판정 저장 거부: %s", type(e).__name__)
            self._say(tr("저장하지 못했습니다: {0}").format(str(e)))
            return None
        self.mark_saved(rec if isinstance(rec, Mapping) else {"kind": "origin", "body": draft["body"]})
        return rec

    def mark_saved(self, record: Mapping) -> None:
        b = record.get("body") if isinstance(record.get("body"), Mapping) else record
        cid = b.get("chat_id")
        if not any(it.chat_id == cid for it in self._items):
            self._refresh()
            return
        self._heads[cid] = dict(record)
        self._say(tr("저장함"))
        if all(it.chat_id in self._heads for it in self._items):
            self._refresh()
            self.allJudged.emit()
            return
        if not self.next_pending():
            self._refresh()

    def _on_cancel(self) -> None:
        try:
            self._cancel()
        except Exception:
            log.exception("출처 판정 cancel 콜백 실패")

    # ------------------------------------------------------------------ 그리기
    def _reset_inputs(self) -> None:
        """대상에 들어가면 입력을 비우고, 이미 판정한 대상이면 그 판정을 채운다(고쳐 저장하면 새 판)."""
        self._auto_taken = False
        for grp, btns in ((self.origin_group, self.origin_btns), (self.conf_group, self.conf_btns)):
            grp.setExclusive(False)
            for b in btns.values():
                b.setChecked(False)
            grp.setExclusive(True)
        it = self.current_item()
        self._fill_methods(it.auto if it else None)
        self.rationale.clear()
        self.msg.clear()
        head = self._heads.get(it.chat_id) if it else None
        if head:
            hb = head.get("body") if isinstance(head.get("body"), Mapping) else head
            if hb.get("origin") in self.origin_btns:
                self.origin_btns[hb["origin"]].setChecked(True)
            if hb.get("confidence") in self.conf_btns:
                self.conf_btns[hb["confidence"]].setChecked(True)
            if hb.get("method") in og.AUTO_METHODS and it.auto is not None and it.auto.method == hb.get("method"):
                self._auto_taken = True
            self.set_method(hb.get("method") or og.M_MANUAL)
            if isinstance(hb.get("rationale"), str):
                self.rationale.setText(hb["rationale"])

    def _say(self, text: str) -> None:
        self.msg.setText(text)
        self.statusChanged.emit(text)

    def _refresh_conf(self) -> None:
        und = self.origin() == og.UNDETERMINABLE
        can = self._allowed and self.current_item() is not None
        for b in self.conf_btns.values():
            b.setEnabled(can and not und)
        self.conf_hint.setText(tr("판별 불가면 확신도를 적지 않습니다") if und else "")

    def _refresh(self) -> None:
        if not self._allowed:
            self.head.setText(tr("이 세션에서는 출처 판정을 할 수 없습니다"))
            self.meta.setText("")
        elif self._targets is None:
            self.head.setText(tr("출처 판정 대상이 없습니다 (origin\\ 의 대상 파일)"))
            self.meta.setText("")
        else:
            pr = og.progress(self._targets, self._heads)
            self.head.setText(tr("출처 판정: 대상 {0:,}   판정 {1:,} (실제 {2} · 가상 {3} · 판별 불가 {4})   자동 {5}").format(
                pr.total, pr.done, pr.by_origin[og.REAL], pr.by_origin[og.VIRTUAL],
                pr.by_origin[og.UNDETERMINABLE], pr.auto))
            extra = ""
            if self._targets.duplicates:
                extra += tr(" · 파일 사이 겹친 대상 {0} 뺌").format(self._targets.duplicates)
            if self._dup_labels:
                extra += tr(" · 한 채팅에 라벨이 둘 이상 {0}").format(self._dup_labels)
            self.meta.setText(tr("대상 파일 {0}{1}").format(", ".join(self._targets.target_ids), extra))
        warn = self.multi_targets_warning() if self._allowed else ""
        self.multi_warn.setText(warn)
        self.multi_warn.setVisible(bool(warn))
        it = self.current_item()
        if it is None:
            self.item_line.setText("")
            self.body.setText("")
            self.auto_line.setText("")
            self.range_line.setText("")
        else:
            msg = self._msgs.get(it.chat_id)
            head = self._heads.get(it.chat_id)
            if head:
                hb = head.get("body") if isinstance(head.get("body"), Mapping) else head
                state = tr("판정함 ({0})").format(tr(og.ORIGIN_KO.get(hb.get("origin"), "?")))
                if og.is_auto(head):
                    state += tr(" · 자동")
            else:
                state = tr("대기")
            t = (msg or {}).get("t_ms")
            when = tc.fmt_time(t, ms_digits=False, always_hours=True) if isinstance(t, int) else "-"
            self.item_line.setText(tr("대상 #{0} · {1} · {2} · {3}").format(
                it.order, when, _viewer_text(msg, self._viewer_no), state))
            text = (msg or {}).get("text")
            self.body.setText(f"“{text}”" if isinstance(text, str) else tr("(채팅 본문을 찾지 못했습니다)"))
            h = it.auto
            if h is not None:
                self.auto_line.setText(tr("[자동] {0} → {1}{2}").format(
                    tr(og.METHOD_KO[h.method]), tr(og.ORIGIN_KO[h.origin]), _score_text(h)))
            else:
                self.auto_line.setText(tr("자동 대조 결과 없음"))
            rng = self.review_range(it)
            self.range_line.setText(tr("확인 범위 {0} ~ {1}").format(
                tc.fmt_time(rng[0], ms_digits=False, always_hours=True),
                tc.fmt_time(rng[1], ms_digits=False, always_hours=True)) if rng else "")
        can = self._allowed and it is not None
        for w in (self.rationale, self.time_btn, self.save_btn, self.method_box, *self.origin_btns.values()):
            w.setEnabled(can)
        self.auto_btn.setEnabled(can and it.auto is not None)
        self.auto_btn.setVisible(it is not None and it.auto is not None)
        self._refresh_conf()
        self.loop_btn.setChecked(self._loop_on and it is not None)
        self.loop_btn.setEnabled(it is not None and self.review_range(it) is not None)
        has = bool(self._items)
        self.prev_btn.setEnabled(has and self._idx > 0)
        self.next_btn.setEnabled(has and self._idx + 1 < len(self._items))
        self.pending_btn.setEnabled(has and any(i.chat_id not in self._heads for i in self._items))


__all__ = ["OriginView", "targets_path", "read_targets_file", "list_targets", "write_targets_file",
           "JUDGED", "PENDING", "PICKABLE_METHODS"]
