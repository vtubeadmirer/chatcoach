"""반응 라벨 편집기 W2 (안내서 v0.1 4장·5장·7-2, 03 6-6~6-9, 02 7-3 '라벨 편집 상태', 07 질문 15·37·38 기본안).

작업대가 `M`(반응 지점 찍기)이나 채팅 더블클릭에서 open_for(chat_id 또는 None, reaction_vod_ms) 를 부르면
오른쪽 칸이 이 편집기로 바뀐다. 저장(`Ctrl+Enter`) 또는 취소(`Esc`) 전에는 다른 라벨로 넘어가지 않는다.

공통 폼 모양
    ReactionEditor(save, cancel, parent=None, *, ops=None, exposed=None)
      save(draft: dict) -> dict      store.append_label 과 같은 초안 {kind, body, entity_id?, supersedes?} → 저장된 레코드
      cancel() -> None              Esc / 취소 버튼
      ops(kind, body) -> dict       (선택) 운영 기록(warning) 쓰기 — store.append_ops
      exposed(what, chat_id, entity_id) (선택) 숨긴 자료를 열었을 때(view.exposed) — 자동 인식 문장 보기
    load_context(...)  작업대 상태(채팅 배열·구간·시작값·세션 종류·위치·재생기·자동 인식 조회)
    open_for(chat_id | None, reaction_vod_ms)   새 반응
    open_existing(reaction_rec, utterances=..., continuation=..., video_checks=...)   고치기·재확인

한 번 저장하면 save 를 여러 번 부른다(순서 고정): 후보 발화(utterance)들 → 반응(reaction) → 이어짐(continuation)
→ 영상 확인(video_check) 회차마다. 중간에 실패하면 편집기를 닫지 않고, 이미 저장된 줄은 기억해 다시 저장할 때
같은 내용이면 건너뛰고 바뀌었으면 그 판을 대체한다(supersedes). 끝나면 finished(result) 신호.

규칙 (저장을 막는 것)
- 후보 발화(candidates[]): 발화 시작·끝, 직접 듣고 적은 문장(heard_text). 자동 인식 문장은 '참고' 로만 보이고
  [붙여 넣기] 로만 가져온다 → heard_text_source(typed / asr_unedited / asr_edited). 원문은 라벨에 넣지 않는다.
- 등급은 미리 고르지 않는다. rules.grade_conditions 로 '조건 충족 등급' 만 보인다(01 3-1, 질문 37).
- 강한 증거 없는 직접 반응은 저장 불가(안내서 4-2 '강한 증거 없이는 직접 반응을 주지 않는다', 질문 38): 연결한 후보의
  강한 증거가 0개면 거부(rules.direct_allowed).
- 조건 충족 등급에 없는 등급을 고르면 grade_vs_conditions='deviates' 이고 사유(override_reason)가 필수다.
- 반응 미확인(Ctrl+5)은 후보 발화가 없을 때만. 대상 채팅은 일반 채팅(kind=text)이고 저스트채팅 구간 안이어야 한다.
- 영상 밖(D-10 3): load_context(media_coverage=...) 로 영상이 덮는 VOD 구간들을 받으면, 대상 채팅의 7-2 범위
  [채팅 − pre_roll, 채팅 + post_roll] 이 그 안에 다 들어와야 저장된다(표본 적격 규칙과 같은 범위). None 이면 검사하지 않는다.
- 채팅 1건 = 현재 유효 라벨 1건(D-10 4): load_context(label_of=…) 로 '그 채팅의 지금 반응 라벨 entity_id' 를 받으면,
  고르는 대상 채팅에 이 편집이 고치는 것과 다른 라벨이 이미 있을 때 저장을 막는다(그 라벨을 고쳐야 한다).
규칙 (저장은 되지만 표시·기록하는 것)
- 직접·높음은 7-2 재확인('들었다' 고 인정된 회차 min_views 이상)이 없으면 '완료' 가 아니다(질문 15 기본안).
  result['complete'] = False, warning(recheck_missing). 타임라인은 속이 빈 표시(작업대 몫). D-16 4 로 시범에서는
  '권장' 이라 저장·잠그기를 막지 않는다(막은 적도 없다 — 표시만 남긴다).
  '들었다' 인정 비율은 기준서 시작값 recheck.heard_min_coverage(없으면 rules.HEARD_MIN_COVERAGE, D-10 6).
- 직접·높음에서 이어짐을 비우면 경고 줄 continuation_missing(D-10 7, 막지 않음). 운영 기록에도 code
  continuation_missing 으로 남긴다(label-event 스키마 warning code 목록에 넣음, D-11 4).
- 확인 범위를 소리로 듣지 않음(audio_not_heard), 근거에 시각 없음(rationale_without_time),
  발화가 채팅보다 앞서는데 '되풀이' 체크(repeat_before_chat), 조건과 다르게 확정(grade_rule_override).

블라인드 (안내서 원칙 3, 03 12장): 이 편집기는 엔진 결과·AI 등급·다른 세션 라벨을 받지도 보이지도 않는다.
자동 인식 문장은 visibility 가 허락할 때만(라벨링 세션은 토글) [보기] 버튼이 생기고, 켜면 exposed 를 부른다.

빠른 입력 (08 D-16): 편집기 맨 위의 단추 6개(Ctrl+Shift+1~6). 라벨러는 '무엇을 들었나' 만 고른다. 단추 하나가
- 후보 발화가 없으면 만든다: 시작 = 편집기를 연 순간의 재생 위치(없으면 반응 지점), 끝 = 시작 + 3초.
  라벨러가 이미 고친 시작·끝은 그대로 둔다.
- 그 후보를 연결 발화로 두고, 단추에 맞는 증거를 체크한다(시간 창은 지금처럼 앱이 계산).
- 등급은 rules.grade_conditions 로 정한다(엔진 결과는 쓰지 않는다). 단추가 바라는 등급이 조건을 채우지 않으면
  아래 등급으로, 그래도 없으면 조건을 채우는 가장 높은 등급으로, 그것도 없으면 불명확. 그래서 늘 '조건과 같음' 이다.
- 확신도 = 확실함, 근거 = '단추 이름 HH:MM:SS(반응 시각)', 들은 내용이 비었으면 단추 이름.
- 그다음 저장(save)을 그대로 부른다. 막는 문제가 있으면 저장하지 않고 이유를 보인다(채운 값은 남아 고칠 수 있다).
- 저장되면 편집기가 닫히고 결과에 status_text('저장: 직접 반응 (채팅 읽고 대답)')가 붙는다(작업대 상태 줄).
기본 화면은 '딸깍' 만이다: 대상 채팅 한 줄 · 단추 6개 · 취소. 나머지(후보 발화·증거·등급·확신도·근거·이어짐·7-2·메시지)는
접힌 '자세히 입력' 안에 있다. 라벨러가 직접 펼치거나 접으면 설정(settings.ini)에 기억한다. 빠른 저장이 막히면
자동으로 펼치고 맨 위에 쉬운 문장 하나로 이유를 보인다(자동 펼침은 기억하지 않는다).
Enter = 기본 단추 '채팅 읽고 대답'(QUICK_DEFAULT). '자세히 입력' 안에 초점이 있으면 Enter 는 그 칸 몫이고 저장은 Ctrl+Enter.
등급을 미리 고르지 않는다: 단추를 누르기 전에는 아무 등급도 골라 두지 않는다(D-16 6).
7-2 두 번 듣기는 D-16 4 로 '권장' 이다: 저장·완료를 막지 않고 미완료 표시(recheck_missing)만 남긴다.

단축키 (02 7-6): Ctrl+1~4 등급, Ctrl+5 반응 미확인, Alt+1~3 확신도, Ctrl+T 근거에 현재 시각, Ctrl+Enter 저장,
Esc 취소, Ctrl+Shift+1~6 빠른 입력(D-16). 수정자 없는 글자 키는 쓰지 않는다(R·[·] 는 review_player.install_shortcuts 가
작업대에 건다). 시각은 모두 VOD(채팅) 시각 ms 다.
"""
from __future__ import annotations

import copy
import logging
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ...core import rules
from ...core import visibility as vis
from ...core.timecode import fmt_time, parse_time, parse_timestamps
from ...i18n import tr
from ..chat_panel import KIND_CODES, ChatData, clean_line, fmt_viewer_no
from ..review_player import POST_ROLL_MS, PRE_ROLL_MS, merge_ranges, review_window
from .continuation_editor import ContinuationEditor
from .evidence_widget import EvidenceWidget, normalize_evidence

LOG = logging.getLogger(__name__)

GRADES = rules.GRADES                      # direct, likely, related_only, unclear
GRADE_LABELS = {"direct": "직접 반응", "likely": "연결 가능성 높음", "related_only": "관련성만 있음",
                "unclear": "불명확"}
UNCONFIRMED = "unconfirmed"
CONFIDENCES = ("certain", "probably", "undeterminable")
CONF_LABELS = {"certain": "확실함", "probably": "아마도", "undeterminable": "판단 불가"}
UNCLEAR_CAUSES = ("multiple_similar_chats", "multiple_utterances", "audio_unclear", "other")
UNCLEAR_LABELS = {"multiple_similar_chats": "비슷한 채팅 여러 개", "multiple_utterances": "후보 발화 여러 개",
                  "audio_unclear": "소리가 안 들림", "other": "기타"}
CANDIDATE_BEFORE_MS = 60_000               # 후보 채팅 범위: 반응 지점 −60초 ~ +5초 (02 7-3)
CANDIDATE_AFTER_MS = 5_000
CANDIDATE_MAX_ROWS = 400
HEARD_MAX = 300                            # utterance.heard_text maxLength
HEARD_RECOMMENDED = 30
RATIONALE_MAX = 300
OVERRIDE_MAX = 300
LINKED_GRADES = ("direct", "likely")       # 연결 발화·반응 시각 필수, 이어짐·재확인 대상
JUST_CHATTING = "just_chatting"
CONTINUATION_MISSING = "continuation_missing"   # 경고 줄·운영 기록 코드(D-10 7, 스키마 목록에 넣음 D-11 4)
# label-event.schema.json $defs/warningBody.code 목록. 이 밖의 코드는 운영 기록에 'other' 로 쓴다.
OPS_WARNING_CODES = frozenset({"audio_not_heard", "recheck_missing", "grade_rule_override", "rationale_without_time",
                               "repeat_before_chat", "calibration_unverified", "relabel_too_early", "segment_too_short",
                               "long_silence_signal", "clock_skew", CONTINUATION_MISSING, "other"})
_KEEP = object()                                 # load_context 에서 '넘기지 않음' 표시(None 과 구분)

QUICK_END_MS = 3_000                             # 빠른 입력의 발화 끝 = 시작 + 3초 (D-16 2)


@dataclass(frozen=True)
class QuickPreset:
    """빠른 입력 단추 하나(D-16 1). want = 단추가 바라는 등급(조건이 허락할 때만 그 등급)."""
    key: str
    label: str
    evidence: tuple[str, ...]
    want: str
    tip: str
    unclear_cause: str | None = None


QUICK_PRESETS: tuple[QuickPreset, ...] = (
    QuickPreset("nickname", "닉네임 부르고 대답", ("nickname_called",), "direct",
                "채팅 쓴 사람의 닉네임을 부르고 대답함 → 닉네임 호명(강) · 직접 반응"),
    QuickPreset("repeat", "채팅 읽고 대답", ("text_repeated",), "direct",
                "채팅 문구를 소리 내 읽고 대답함 → 문구 되풀이(강) · 직접 반응"),
    QuickPreset("reference", "채팅 가리키고 대답", ("direct_reference",), "direct",
                "'이 채팅', '방금 질문' 처럼 그 채팅을 가리키고 대답함 → 직접 가리킴(강) · 직접 반응"),
    QuickPreset("answer", "질문에 대답", ("answer_form",), "likely",
                "질문에 맞는 답 + 시간 창 → '비슷한 채팅: 없음' 이면 연결 가능성 높음, 아니면 관련성만 있음"),
    QuickPreset("topic", "주제만 같음", ("same_topic",), "related_only",
                "그 채팅과 같은 주제를 말했지만 그 채팅에 대한 대답인지는 모름 → 같은 주제(약) · 관련성만 있음"),
    QuickPreset("unknown", "어느 채팅인지 모름", (), "unclear",
                "대답은 했지만 어느 채팅에 한 말인지 가릴 수 없음 → 불명확(비슷한 채팅 여러 개)",
                unclear_cause="multiple_similar_chats"),
)
QUICK_KEYS = tuple(f"Ctrl+Shift+{i}" for i in range(1, len(QUICK_PRESETS) + 1))
QUICK_DEFAULT = "repeat"                         # Enter 로 저장되는 기본 단추('채팅 읽고 대답')
DETAIL_PREF_KEY = "reaction_editor/detail_expanded"   # '자세히 입력' 펼침 기억(사용자 설정 settings.ini)


def _load_detail_pref() -> bool:
    try:
        from ...paths import make_settings
        v = make_settings().value(DETAIL_PREF_KEY, False)
    except Exception:  # noqa: BLE001 — 설정을 못 읽으면 접힌 채로
        return False
    return v is True or str(v).lower() in ("true", "1")


def _save_detail_pref(on: bool) -> None:
    try:
        from ...paths import make_settings
        s = make_settings()
        s.setValue(DETAIL_PREF_KEY, bool(on))
        s.sync()
    except Exception as e:  # noqa: BLE001
        LOG.debug("설정 저장 실패: %s", type(e).__name__)


# ---------------------------------------------------------------------------- 후보 발화 상태

@dataclass
class Candidate:
    """후보 발화 하나(화면 상태). saved 는 이 후보의 발화 라벨 현재 판(저장했거나 불러온 것)."""
    start_ms: int | None = None
    end_ms: int | None = None
    heard_text: str = ""
    heard_source: str = "typed"             # typed / asr_unedited / asr_edited
    pasted_text: str | None = None          # 붙여 넣은 원문(같은지 비교용, 저장하지 않음)
    evidence: dict = field(default_factory=lambda: normalize_evidence(None))
    time_manual: bool = False
    saved: dict | None = None
    used_asr: bool = False
    auto_start: bool = False                # 시작이 앱이 채운 값 그대로인가(빠른 입력이 '편집기를 연 위치' 로 바꿔도 됨)

    def filled(self) -> bool:
        """무엇이든 적었는가(빈 후보는 저장하지 않고 '반응 미확인' 을 막지도 않는다)."""
        # 시간 창은 앱이 자동으로 체크하므로 '적었다' 로 치지 않는다
        return bool(self.heard_text.strip() or self.end_ms is not None
                    or any(v.get("present") for k, v in self.evidence.items() if k != rules.TIME_KEY))


@dataclass
class Validation:
    errors: list[str]
    warnings: list[tuple[str, str]]          # (warning code, 문장)
    conditions: rules.GradeConditions | None
    grade_vs: str | None


def utterance_body(c: Candidate, segment_id: str | None) -> dict:
    return {"segment_id": segment_id, "start_ms": int(c.start_ms), "end_ms": int(c.end_ms),
            "heard_text": c.heard_text.strip()[:HEARD_MAX], "heard_text_source": c.heard_source}


def normalize_coverage(coverage) -> list[tuple[int, int]] | None:
    """영상이 덮는 VOD 구간들([시작, 끝) ms 쌍, 또는 {start_ms, end_ms}) → 합친 목록. None 은 None(검사 안 함).
    모양이 틀린 칸은 버린다."""
    if coverage is None:
        return None
    out = []
    for r in coverage:
        if isinstance(r, Mapping):
            r = (r.get("start_ms"), r.get("end_ms"))
        try:
            a, b = r
        except (TypeError, ValueError):
            continue
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and v == v for v in (a, b)):
            out.append((int(a), int(b)))
    return merge_ranges(out)


def review_window_in_media(chat_ms: int, coverage, pre_roll_ms: int = PRE_ROLL_MS,
                           post_roll_ms: int = POST_ROLL_MS) -> bool:
    """대상 채팅의 7-2 확인 범위 [채팅 − pre_roll, 채팅 + post_roll](review_window: 시작은 0 아래로 내리지 않음)가
    영상이 덮는 구간 하나 안에 다 들어오는가(D-10 3). coverage 가 None 이면 늘 True(검사 안 함)."""
    cov = normalize_coverage(coverage)
    if cov is None:
        return True
    a, b = review_window(int(chat_ms), None, pre_roll_ms, post_roll_ms)
    return any(s <= a and b <= e for s, e in cov)


def find_segment(segments: Sequence[Mapping], t_ms: int | None) -> Mapping | None:
    """t_ms 를 [start, end) 로 품는 구간 레코드(또는 body). 겹치면 시작이 늦은 것."""
    if t_ms is None:
        return None
    best = None
    for s in segments or ():
        b = s.get("body", s) if isinstance(s, Mapping) else None
        if not isinstance(b, Mapping):
            continue
        a, e = b.get("start_ms"), b.get("end_ms")
        if isinstance(a, int) and isinstance(e, int) and a <= t_ms < e:
            if best is None or a >= (best.get("body", best)).get("start_ms", -1):
                best = s
    return best


def _seg_id(s: Mapping | None) -> str | None:
    if not isinstance(s, Mapping):
        return None
    v = s.get("entity_id")
    return v if isinstance(v, str) else None


def _seg_body(s: Mapping | None) -> Mapping:
    if not isinstance(s, Mapping):
        return {}
    b = s.get("body", s)
    return b if isinstance(b, Mapping) else {}


def _body_eq(a: Mapping | None, b: Mapping | None) -> bool:
    return isinstance(a, Mapping) and isinstance(b, Mapping) and dict(a) == dict(b)


class ReactionEditor(QWidget):
    """반응 라벨 한 건을 만드는 오른쪽 칸."""

    finished = Signal(object)        # 저장 결과 dict (reaction·utterances·continuation·continuation_retracted·
                                     # video_checks·complete·warnings·written)
    cancelled = Signal()
    partialSaved = Signal(object)    # 저장이 중간에 실패한 뒤 Esc: {'reaction': 마지막으로 쓴 반응 또는 None, 'written': [...]}
    stateChanged = Signal()          # 조건 충족·검증 상태가 바뀜

    def __init__(self, save: Callable[[dict], dict], cancel: Callable[[], None] | None = None, parent=None, *,
                 ops: Callable[[str, dict], Any] | None = None,
                 exposed: Callable[[str, str | None, str | None], Any] | None = None,
                 retract: Callable[[str, str], Any] | None = None):
        super().__init__(parent)
        self._save_cb = save
        self._cancel_cb = cancel
        self._retract_cb = retract       # (entity_id, reason) → 철회 레코드. 없으면 save 에 kind='retract' 초안을 보낸다
        self._ops_cb = ops
        self._exposed_cb = exposed
        # 맥락
        self._chats: ChatData | None = None
        self._segments: list[Mapping] = []
        self._cfg = rules.V0_1
        self._thresholds: Mapping | None = None
        self._session_kind: str | None = None
        self._phase: str | None = None
        self._position_fn: Callable[[], int] | None = None
        self._asr_lookup: Callable[[int, int], str | None] | None = None
        self.review = None
        self._min_views = 2
        self._min_coverage = rules.HEARD_MIN_COVERAGE   # 기준서 recheck.heard_min_coverage(D-10 6)
        self._pre_roll_ms = PRE_ROLL_MS
        self._post_roll_ms = POST_ROLL_MS
        self._media_coverage: list[tuple[int, int]] | None = None   # 영상이 덮는 VOD 구간들. None = 검사 안 함
        self._label_of: Callable[[str], str | None] | None = None    # chat_id → 그 채팅의 현재 반응 라벨 entity_id
        # 편집 상태
        self._open = False
        self._reaction_ms: int | None = None
        self._raw_click_ms: int | None = None
        self._target: str | None = None
        self._entry = "from_utterance"
        self._sample_ref: dict | None = None
        self._cands: list[Candidate] = []
        self._cur = -1
        self._linked = 0
        self._grade: str | None = None          # GRADES 중 하나 또는 UNCONFIRMED
        self._confidence: str | None = None
        self._existing: dict | None = None       # 고치는 반응의 현재 판
        self._saved_reaction: dict | None = None
        self._prior_checks: list[dict] = []      # 불러온 영상 확인 기록(이 반응)
        self._saved_passes: set[int] = set()     # 이번에 video_check 로 저장한 회차
        self._pass_judgments: dict[int, dict] = {}
        self._written: list[dict] = []           # 이번 편집에서 쓴 줄 전부(실패한 시도 포함, 쓴 순서) — 작업대 Ctrl+Z 용
        self._asr_revealed = False
        self._loading = False
        self._last_validation: Validation | None = None
        self._open_pos_ms: int | None = None     # 편집기를 연 순간의 재생 위치(빠른 입력의 발화 시작, D-16 2)
        self._quick_auto: dict[str, str] = {}    # 빠른 입력이 마지막으로 채운 글(rationale·heard) — 다른 단추면 바꿔 쓴다
        self.last_quick: str | None = None       # 마지막으로 누른 빠른 입력 단추 key(저장 성공 여부와 무관)
        self._status_text: str | None = None     # 빠른 입력 저장 중일 때 결과에 넣을 상태 문장
        self._build()
        self._install_shortcuts()
        self.setEnabled(False)

    # ================================================================ 화면
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.title = QLabel(tr("반응 라벨 편집"))
        f = QFont(self.title.font())
        f.setBold(True)
        self.title.setFont(f)
        lay.addWidget(self.title)

        # 빠른 입력 (D-16 1): 무엇을 들었나만 고르면 증거·등급·확신도·근거를 채워 바로 저장한다.
        # 기본 화면은 '딸깍' 만: 대상 채팅 한 줄 · 단추 6개 · 취소. 나머지는 접힌 '자세히 입력' 안에 있다.
        self.quick_error = QLabel("")            # 빠른 저장이 막혔을 때 쉬운 문장 하나
        self.quick_error.setObjectName("quick_error")
        self.quick_error.setWordWrap(True)
        self.quick_error.setTextFormat(Qt.PlainText)
        self.quick_error.setStyleSheet("color: #b00020; font-weight: bold;")
        self.quick_error.hide()
        lay.addWidget(self.quick_error)
        self.quick_target = QLabel("")           # 대상 채팅 한 줄(시각 · 시청자 · 본문)
        self.quick_target.setObjectName("quick_target")
        self.quick_target.setWordWrap(True)
        self.quick_target.setTextFormat(Qt.PlainText)
        lay.addWidget(self.quick_target)
        qbox = QGroupBox(tr("어떻게 대답했나요?"))       # 제목은 짧게(그룹 제목은 줄바꿈이 없어 칸 폭을 넓힌다)
        qbox.setObjectName("quick_input")
        ql = QVBoxLayout(qbox)
        qhint = QLabel(tr("누르면 바로 저장됩니다. 채팅을 읽고 대답했으면 Enter."))
        qhint.setWordWrap(True)
        ql.addWidget(qhint)
        qgrid = QGridLayout()                    # 2칸씩 — 1366×768 편집기 칸(약 470px)에 들어가게(11 4-2 의 2)
        self.quick_buttons: dict[str, QPushButton] = {}
        for i, p in enumerate(QUICK_PRESETS):
            default = p.key == QUICK_DEFAULT
            b = QPushButton(f"{i + 1}. {tr(p.label)}" + ("  ⏎" if default else ""))
            b.setObjectName(f"quick_{p.key}")
            b.setToolTip(f"{tr(p.tip)} ({QUICK_KEYS[i]}{' · Enter' if default else ''})")
            b.setMinimumHeight(40)
            bf = QFont(b.font())
            bf.setBold(True)
            b.setFont(bf)
            if default:                          # 기본 단추: 눈에 띄게(Enter 로 저장)
                b.setDefault(True)
                b.setStyleSheet("QPushButton { border: 2px solid #1a73e8; border-radius: 4px; padding: 4px; }")
            b.clicked.connect(lambda _c=False, k=p.key: self.quick_input(k))
            self.quick_buttons[p.key] = b
            qgrid.addWidget(b, i // 2, i % 2)
        ql.addLayout(qgrid)
        qrow = QHBoxLayout()
        self.quick_cancel = QPushButton(tr("취소 (Esc)"))
        self.quick_cancel.setObjectName("quick_cancel")
        self.quick_cancel.setToolTip(tr("대답이 아니면 저장하지 않고 닫습니다"))
        self.quick_cancel.clicked.connect(self.cancel)
        qrow.addWidget(self.quick_cancel)
        qrow.addStretch(1)
        self.detail_toggle = QPushButton(tr("자세히 입력 ▸"))
        self.detail_toggle.setObjectName("detail_toggle")
        self.detail_toggle.setCheckable(True)
        self.detail_toggle.setFlat(True)
        self.detail_toggle.toggled.connect(self._on_detail_toggled)
        qrow.addWidget(self.detail_toggle)
        ql.addLayout(qrow)
        lay.addWidget(qbox)

        # 자세히 입력 (접힘): 지금까지의 화면 전부. 아래 lay 는 이 칸의 레이아웃이다
        self.detail = QWidget()
        self.detail.setObjectName("detail")
        top_lay = lay
        lay = QVBoxLayout(self.detail)
        lay.setContentsMargins(0, 0, 0, 0)
        top_lay.addWidget(self.detail)
        top_lay.addStretch(1)

        # 반응 지점
        row = QHBoxLayout()
        self.reaction_label = QLabel("")
        self.reaction_now = QPushButton(tr("지금 위치로"))
        self.reaction_now.clicked.connect(self._reaction_to_now)
        row.addWidget(QLabel(tr("반응 지점")))
        row.addWidget(self.reaction_label, 1)
        row.addWidget(self.reaction_now)
        lay.addLayout(row)

        # 후보 채팅
        box = QGroupBox(tr("대상 채팅 (반응 지점 −60초 ~ +5초)"))
        bl = QVBoxLayout(box)
        hint = QLabel(tr("고른 줄 = 대상 · 체크 = 비슷한 채팅"))
        hint.setWordWrap(True)
        bl.addWidget(hint)
        self.chat_list = QListWidget()
        self.chat_list.setObjectName("candidate_chats")
        self.chat_list.setMinimumHeight(120)
        self.chat_list.currentItemChanged.connect(lambda cur, _prev: self._on_target_changed(cur))
        self.chat_list.itemChanged.connect(lambda _it: self._on_similar_changed())
        bl.addWidget(self.chat_list)
        srow = QHBoxLayout()
        self.similar_box = QComboBox()
        self.similar_box.addItem(tr("같은 시간대 비슷한 채팅: 모름"), None)
        self.similar_box.addItem(tr("같은 시간대 비슷한 채팅: 없음"), True)
        self.similar_box.addItem(tr("같은 시간대 비슷한 채팅: 있음"), False)
        self.similar_box.currentIndexChanged.connect(lambda _i: self._recompute())
        self.on_topic = QCheckBox(tr("스트리머가 이미 그 주제를 말하던 중"))
        self.on_topic.toggled.connect(lambda _on: self._recompute())
        srow.addWidget(self.similar_box)
        srow.addStretch(1)
        bl.addLayout(srow)
        bl.addWidget(self.on_topic)          # 다음 줄 (좁은 화면, 11 4-2 의 2)
        self.target_info = QLabel("")
        self.target_info.setWordWrap(True)
        bl.addWidget(self.target_info)
        lay.addWidget(box)

        # 후보 발화
        cbox = QGroupBox(tr("후보 발화"))
        cl = QVBoxLayout(cbox)
        nav = QHBoxLayout()
        self.cand_prev = QPushButton("◀")
        self.cand_next = QPushButton("▶")
        self.cand_label = QLabel("")
        self.cand_add = QPushButton(tr("+ 후보 추가"))
        self.cand_del = QPushButton(tr("후보 지우기"))
        self.linked_box = QCheckBox(tr("연결 발화 (직접·높음일 때)"))
        # 1366×768 에서 편집기 칸(약 470px)에 들어가게 '연결 발화' 는 다음 줄에 둔다(11 4-2 의 2)
        for w in (self.cand_prev, self.cand_label, self.cand_next, self.cand_add, self.cand_del):
            nav.addWidget(w)
        nav.addStretch(1)
        self.cand_prev.clicked.connect(lambda: self._switch_cand(self._cur - 1))
        self.cand_next.clicked.connect(lambda: self._switch_cand(self._cur + 1))
        self.cand_add.clicked.connect(self.add_candidate)
        self.cand_del.clicked.connect(self.remove_candidate)
        self.linked_box.toggled.connect(self._on_linked_toggled)
        cl.addLayout(nav)
        cl.addWidget(self.linked_box)
        form = QFormLayout()
        trow = QHBoxLayout()
        erow = QHBoxLayout()
        self.utt_start = QLineEdit()
        self.utt_start.setObjectName("utt_start")
        self.utt_end = QLineEdit()
        self.utt_end.setObjectName("utt_end")
        self.utt_start_now = QPushButton(tr("시작=지금"))
        self.utt_end_now = QPushButton(tr("끝=지금"))
        for w in (self.utt_start, self.utt_start_now):
            trow.addWidget(w)
        for w in (self.utt_end, self.utt_end_now):
            erow.addWidget(w)
        self.utt_start_now.clicked.connect(lambda: self._utt_now(self.utt_start))
        self.utt_end_now.clicked.connect(lambda: self._utt_now(self.utt_end))
        self.utt_start.editingFinished.connect(self._on_utt_times)
        self.utt_end.editingFinished.connect(self._on_utt_times)
        form.addRow(tr("발화 시작"), trow)
        form.addRow(tr("발화 끝"), erow)
        hrow = QHBoxLayout()
        self.heard = QLineEdit()
        self.heard.setObjectName("heard_text")
        self.heard.setMaxLength(HEARD_MAX)
        self.heard.setPlaceholderText(tr("직접 듣고 적은 말 ({0}자 안팎 권장)").format(HEARD_RECOMMENDED))
        self.heard.textEdited.connect(self._on_heard_edited)
        self.heard_count = QLabel("0")
        hrow.addWidget(self.heard, 1)
        hrow.addWidget(self.heard_count)
        form.addRow(tr("들은 내용"), hrow)
        arow = QHBoxLayout()
        self.asr_label = QLabel(tr("자동 인식 문장: 숨김"))
        self.asr_label.setWordWrap(True)
        self.asr_show = QPushButton(tr("참고 보기"))
        self.asr_paste = QPushButton(tr("붙여 넣기"))
        self.asr_show.clicked.connect(self.reveal_asr)
        self.asr_paste.clicked.connect(self.paste_asr)
        arow.addWidget(self.asr_label, 1)
        arow.addWidget(self.asr_show)
        arow.addWidget(self.asr_paste)
        form.addRow(tr("참고"), arow)
        cl.addLayout(form)
        self.evidence = EvidenceWidget(cfg=self._cfg)
        self.evidence.changed.connect(self._on_evidence_changed)
        cl.addWidget(QLabel(tr("증거 (이 발화와 대상 채팅의 짝)")))
        cl.addWidget(self.evidence)
        lay.addWidget(cbox)

        # 조건 충족
        self.cond_label = QLabel("")
        self.cond_label.setObjectName("conditions")
        self.cond_label.setWordWrap(True)
        self.cond_label.setTextFormat(Qt.PlainText)
        lay.addWidget(self.cond_label)

        # 등급
        gbox = QGroupBox(tr("등급 (Ctrl+1~5)"))
        gl = QVBoxLayout(gbox)
        ghint = QLabel(tr("Ctrl+1~5 = 왼쪽 위부터 차례로 (반응 미확인 Ctrl+5) · 조건과 다르게 고르면 사유 필수"))
        ghint.setWordWrap(True)
        gl.addWidget(ghint)
        # 2칸씩(좁은 화면에서 잘리지 않게, 11 4-2 의 2). 단축키는 단추 글자에서 빼고 풍선 도움말·위 안내에 둔다 —
        # 1366×768 편집기 칸(약 470px)에서 단추가 오른쪽 밖으로 나가지 않게(14 4-2 의 1)
        grow = QGridLayout()
        self.grade_group = QButtonGroup(self)
        self.grade_buttons: dict[str, QPushButton] = {}
        for i, g in enumerate(GRADES + (UNCONFIRMED,), start=1):
            name = GRADE_LABELS.get(g, tr("반응 미확인"))
            b = QPushButton(tr(name))
            b.setToolTip(f"Ctrl+{i}")
            b.setCheckable(True)
            b.setObjectName(f"grade_{g}")
            self.grade_group.addButton(b)
            self.grade_buttons[g] = b
            grow.addWidget(b, (i - 1) // 2, (i - 1) % 2)
            b.clicked.connect(lambda _c=False, gg=g: self.set_grade(gg))
        gl.addLayout(grow)
        urow = QHBoxLayout()
        self.unclear_box = QComboBox()
        self.unclear_box.addItem(tr("불명확 사유 고르기"), None)
        for c in UNCLEAR_CAUSES:
            self.unclear_box.addItem(tr(UNCLEAR_LABELS[c]), c)
        self.unclear_box.currentIndexChanged.connect(lambda _i: self._recompute())
        urow.addWidget(self.unclear_box)
        self.override = QLineEdit()
        self.override.setObjectName("override_reason")
        self.override.setMaxLength(OVERRIDE_MAX)
        self.override.setPlaceholderText(tr("조건과 다르게 고른 이유 한 줄"))
        self.override.textEdited.connect(lambda _t: self._recompute())
        urow.addWidget(self.override, 1)
        gl.addLayout(urow)
        lay.addWidget(gbox)

        # 확신도
        crow = QHBoxLayout()
        crow.addWidget(QLabel(tr("확신도")))
        cgrid = QGridLayout()                    # 3개를 한 줄에 두면 470px 칸에서 넘친다(14 4-2 의 1) → 2칸씩
        self.conf_group = QButtonGroup(self)
        self.conf_buttons: dict[str, QPushButton] = {}
        for i, c in enumerate(CONFIDENCES, start=1):
            b = QPushButton(f"{tr(CONF_LABELS[c])} (Alt+{i})")
            b.setCheckable(True)
            b.setObjectName(f"conf_{c}")
            self.conf_group.addButton(b)
            self.conf_buttons[c] = b
            cgrid.addWidget(b, (i - 1) // 2, (i - 1) % 2)
            b.clicked.connect(lambda _c=False, cc=c: self.set_confidence(cc))
        crow.addLayout(cgrid)
        crow.addStretch(1)
        lay.addLayout(crow)

        # 근거
        rrow = QHBoxLayout()
        self.rationale = QLineEdit()
        self.rationale.setObjectName("rationale")
        self.rationale.setMaxLength(RATIONALE_MAX)
        self.rationale.setPlaceholderText(tr("근거 한 줄 (시각 포함, 예: 01:12:14 호명)"))
        self.rationale.textEdited.connect(lambda _t: self._recompute())
        self.rationale_now = QPushButton(tr("현재 시각 (Ctrl+T)"))
        self.rationale_now.clicked.connect(self.insert_time)
        rrow.addWidget(QLabel(tr("근거")))
        rrow.addWidget(self.rationale, 1)
        rrow.addWidget(self.rationale_now)
        lay.addLayout(rrow)

        # 이어짐
        self.cont_box = QGroupBox(tr("이어짐 (직접·높음일 때)"))
        cbl = QVBoxLayout(self.cont_box)
        self.continuation = ContinuationEditor(embedded=True)
        self.continuation.changed.connect(self._recompute)
        cbl.addWidget(self.continuation)
        lay.addWidget(self.cont_box)

        # 재확인
        vrow = QHBoxLayout()
        self.recheck_label = QLabel("")
        self.recheck_label.setObjectName("recheck")
        # 들은 뒤 문장(범위·배속·횟수)이 길어 한 줄이면 1366 창의 편집기 칸(약 460px)을 넘는다 → 줄바꿈 (c6 실제 창 측정)
        self.recheck_label.setWordWrap(True)
        self.recheck_btn = QPushButton(tr("7-2 범위 다시 듣기 (R)"))
        self.recheck_btn.clicked.connect(self.listen_again)
        vrow.addWidget(self.recheck_label, 1)
        vrow.addWidget(self.recheck_btn)
        lay.addLayout(vrow)

        self.msg = QLabel("")
        self.msg.setObjectName("messages")
        self.msg.setWordWrap(True)
        self.msg.setTextFormat(Qt.PlainText)
        lay.addWidget(self.msg)

        self.detail_buttons = QWidget()          # 저장·취소 줄도 '자세히 입력' 을 펼쳤을 때만
        brow = QHBoxLayout(self.detail_buttons)
        brow.setContentsMargins(6, 0, 6, 6)
        self.save_btn = QPushButton(tr("저장 (Ctrl+Enter)"))
        self.cancel_btn = QPushButton(tr("취소 (Esc)"))
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn.clicked.connect(self.cancel)
        brow.addWidget(self.save_btn)
        brow.addWidget(self.cancel_btn)
        brow.addStretch(1)
        outer.addWidget(self.detail_buttons)
        self._loading = True
        try:
            self.detail_toggle.setChecked(_load_detail_pref())
        finally:
            self._loading = False
        self._apply_detail(self.detail_toggle.isChecked())

    # ---------------------------------------------------------------- 자세히 입력 접기·펴기
    def _on_detail_toggled(self, on: bool) -> None:
        self._apply_detail(on)
        if not self._loading:
            _save_detail_pref(on)                # 라벨러가 직접 바꾼 것만 기억한다(자동 펼침은 기억하지 않음)

    def _apply_detail(self, on: bool) -> None:
        self.detail.setVisible(bool(on))
        self.detail_buttons.setVisible(bool(on))
        self.detail_toggle.setText(tr("자세히 입력 ▾") if on else tr("자세히 입력 ▸"))

    def detail_expanded(self) -> bool:
        return self.detail_toggle.isChecked()

    def set_detail_expanded(self, on: bool, *, remember: bool = False) -> None:
        """'자세히 입력' 을 펼치거나 접는다. remember=True 면 설정에 남긴다(단추를 누른 것과 같음)."""
        self._loading = not remember
        try:
            self.detail_toggle.setChecked(bool(on))
        finally:
            self._loading = False
        self._apply_detail(bool(on))

    def _quick_fail(self, text: str) -> None:
        """빠른 저장이 막혔다: '자세히 입력' 을 펴고 맨 위에 쉬운 문장 하나."""
        self.set_detail_expanded(True)
        self.quick_error.setText(text)
        self.quick_error.show()

    def keyPressEvent(self, ev):  # noqa: N802
        """Enter = 기본 빠른 입력('채팅 읽고 대답'). '자세히 입력' 안의 칸에 커서가 있으면 그 칸 몫이다(거기서는
        Ctrl+Enter 가 저장). 글자 칸은 Enter 를 부모로 넘기므로 여기서 초점 위치를 본다."""
        if (ev.key() in (Qt.Key_Return, Qt.Key_Enter) and self._open
                and not (ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier))):
            fw = QApplication.focusWidget() or self.focusWidget()
            if fw is None or not (fw is self.detail or self.detail.isAncestorOf(fw)):
                ev.accept()
                self.quick_input(QUICK_DEFAULT)
                return
        super().keyPressEvent(ev)

    def _install_shortcuts(self) -> None:
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
            return s
        self._shortcuts = []
        for i, g in enumerate(GRADES + (UNCONFIRMED,), start=1):
            self._shortcuts.append(sc(f"Ctrl+{i}", lambda gg=g: self.set_grade(gg)))
        for i, c in enumerate(CONFIDENCES, start=1):
            self._shortcuts.append(sc(f"Alt+{i}", lambda cc=c: self.set_confidence(cc)))
        for key, p in zip(QUICK_KEYS, QUICK_PRESETS):
            self._shortcuts.append(sc(key, lambda k=p.key: self.quick_input(k)))
        self._shortcuts.append(sc("Ctrl+T", self.insert_time))
        self._shortcuts.append(sc("Ctrl+Return", self.save))
        self._shortcuts.append(sc("Ctrl+Enter", self.save))
        self._shortcuts.append(sc("Esc", self.cancel))

    # ================================================================ 맥락
    def load_context(self, *, chats=None, segments: Sequence[Mapping] | None = None,
                     thresholds: Mapping | None = None, session_kind: str | None = None, phase: str | None = None,
                     position_fn: Callable[[], int] | None = None, review=None,
                     asr_lookup: Callable[[int, int], str | None] | None = None,
                     heard_min_coverage: float | None = None, media_coverage=_KEEP,
                     label_of: Callable[[str], str | None] | None = None) -> None:
        """작업대 상태를 받는다. 부르지 않은 인자는 그대로 둔다(None 을 넘기면 비운다는 뜻이 아니다).
        단 media_coverage 만은 None 을 넘기면 '검사 안 함' 으로 되돌린다(넘기지 않으면 그대로).

        chats: ChatData(병렬 배열, chat_panel) 또는 messages.jsonl 줄 dict 목록.
        segments: 이 세션의 현재 유효 구간 레코드(entity_id + body) — 반응·발화의 segment_id 를 정한다.
        thresholds: guide-config values(또는 project.json guides[].thresholds). 등급 규칙·재확인 시작값.
        session_kind / phase: visibility 로 자동 인식 문장 보기를 정한다.
        position_fn: 지금 VOD 위치(ms). review: ReviewPlayer. asr_lookup(start_ms, end_ms) → 참고 문장 또는 None.
        heard_min_coverage: '들었다' 인정 비율을 직접 줄 때. 주지 않으면 thresholds 의 recheck.heard_min_coverage,
            그것도 없으면 rules.HEARD_MIN_COVERAGE. review 에도 같은 값을 넘긴다(D-10 6, 전역 상태 없음).
        media_coverage: 영상이 덮는 VOD 구간들 [(start_ms, end_ms), ...]. 대상 채팅의 7-2 범위가 그 밖이면
            저장을 막고 이유를 보인다(D-10 3). None 이면 검사하지 않는다(지금처럼). 빈 목록은 '영상이 덮는 곳 없음'.
        label_of: chat_id → 그 채팅에 이미 있는 (이 세션의) 현재 반응 라벨 entity_id 또는 None (D-10 4).
        """
        if chats is not None:
            self._chats = chats if isinstance(chats, ChatData) else ChatData(list(chats))
        if segments is not None:
            self._segments = list(segments)
        if thresholds is not None:
            self._thresholds = thresholds
            try:
                self._cfg = rules.RuleConfig.from_values(thresholds)
            except (KeyError, TypeError, ValueError) as e:
                LOG.warning("시작값에서 등급 규칙을 만들지 못해 v0.1 기본값을 씀: %s", type(e).__name__)
                self._cfg = rules.V0_1
            rc = thresholds.get("recheck") if isinstance(thresholds, Mapping) else None
            if isinstance(rc, Mapping) and isinstance(rc.get("min_views_direct_likely"), int):
                self._min_views = int(rc["min_views_direct_likely"])
            if isinstance(rc, Mapping):
                for key, attr in (("pre_roll_sec", "_pre_roll_ms"), ("post_roll_sec", "_post_roll_ms")):
                    v = rc.get(key)
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v >= 0:
                        setattr(self, attr, int(round(v * 1000)))
            self._min_coverage = rules.heard_min_coverage(thresholds)
            self.evidence.set_config(self._cfg)
        if heard_min_coverage is not None:
            self._min_coverage = float(heard_min_coverage)
        if media_coverage is not _KEEP:
            self._media_coverage = normalize_coverage(media_coverage)
        if session_kind is not None:
            self._session_kind = session_kind
        if phase is not None:
            self._phase = phase
        if position_fn is not None:
            self._position_fn = position_fn
            self.evidence.set_position_fn(position_fn)
        if review is not None and review is not self.review:
            if self.review is not None:
                self._disconnect_review()
            self.review = review
            review.passStarted.connect(self._on_pass_started)
            review.coverageChanged.connect(self._on_coverage)
        if self.review is not None and (thresholds is not None or heard_min_coverage is not None
                                        or review is not None):
            # 재생기도 편집기와 같은 '들었다' 기준을 쓴다(내보내기·마커와 같은 값)
            if thresholds is not None and hasattr(self.review, "set_thresholds"):
                self.review.set_thresholds(thresholds)
            if hasattr(self.review, "set_config"):
                self.review.set_config(min_coverage=self._min_coverage)
        if asr_lookup is not None:
            self._asr_lookup = asr_lookup
        if label_of is not None:
            self._label_of = label_of
        self.continuation.load_context(chats=self._chats, thresholds=self._thresholds,
                                       position_fn=self._position_fn)
        self._update_asr_controls()

    def _disconnect_review(self) -> None:
        r = self.review
        for sig, slot in (("passStarted", self._on_pass_started), ("coverageChanged", self._on_coverage)):
            try:
                getattr(r, sig).disconnect(slot)
            except (RuntimeError, TypeError, AttributeError):
                pass

    def heard_min_coverage(self) -> float:
        """이 편집기가 쓰는 '들었다' 인정 비율."""
        return self._min_coverage

    def media_coverage(self) -> list[tuple[int, int]] | None:
        return None if self._media_coverage is None else list(self._media_coverage)

    def target_in_media(self) -> bool:
        """대상 채팅의 7-2 범위가 영상 안인가. 영상 범위를 모르거나(None) 대상이 없으면 True."""
        chat_ms = self._chat_time(self._target)
        if chat_ms is None or self._media_coverage is None:
            return True
        return review_window_in_media(chat_ms, self._media_coverage, self._pre_roll_ms, self._post_roll_ms)

    def asr_policy(self) -> str:
        """자동 인식 문장 보이기 정책(visibility). 세션 종류를 모르면 NEVER."""
        if not self._session_kind:
            return vis.NEVER
        return vis.show_policy(vis.ASR_TEXT, self._phase, self._session_kind)

    # ================================================================ 열기
    def is_open(self) -> bool:
        return self._open

    def open_for(self, chat_id: str | None, reaction_vod_ms: int | None, *, sample_ref: dict | None = None,
                 open_pos_ms: int | None = None) -> None:
        """새 반응 편집. chat_id 를 주면 그 채팅이 대상, 없으면 후보 채팅 목록에서 고른다(02 7-3).

        반응 지점이 없으면 지금 위치를 쓴다. sample_ref 를 주면 3단계(표본) 입력이다.
        open_pos_ms: 편집기를 열기 직전의 재생 위치(라벨러가 멈춘 자리, D-16 2). 작업대가 채팅 더블클릭으로 영상을
            채팅 앞으로 옮긴 뒤 열 때 넘긴다. 주지 않으면 지금 재생 위치.
        """
        open_pos = int(open_pos_ms) if isinstance(open_pos_ms, int) and not isinstance(open_pos_ms, bool) \
            else self._position_now()
        if reaction_vod_ms is None:
            reaction_vod_ms = self._position_now()
        self._reset_state()
        self._reaction_ms = None if reaction_vod_ms is None else int(reaction_vod_ms)
        self._open_pos_ms = open_pos if open_pos is not None else self._reaction_ms
        self._raw_click_ms = self._reaction_ms
        self._entry = "from_sample" if sample_ref else "from_utterance"
        self._sample_ref = copy.deepcopy(sample_ref) if sample_ref else None
        start = self._reaction_ms
        self._cands = [Candidate(start_ms=start, auto_start=True)]
        self._cur = 0
        self._linked = 0
        self._begin(chat_id)
        self.title.setText(tr("반응 라벨 편집 — 새 반응"))

    def open_existing(self, reaction: Mapping, *, utterances: Mapping[str, Mapping] | None = None,
                      continuation: Mapping | None = None, video_checks: Sequence[Mapping] = (),
                      open_pos_ms: int | None = None) -> None:
        """이미 저장된 반응을 고친다(재확인 포함). utterances: utterance entity_id → 현재 판 레코드.

        저장하면 같은 entity_id 의 새 판(supersedes = 지금 판)이 된다. 재확인 뒤 판단이 바뀌면 처음 판단을
        first_judgment 로 복사하고 changed_on_recheck 를 켠다(안내서 7-2).
        """
        b = dict(reaction.get("body") or {})
        open_pos = int(open_pos_ms) if isinstance(open_pos_ms, int) and not isinstance(open_pos_ms, bool) \
            else self._position_now()
        self._reset_state()
        self._existing = dict(reaction)
        self._saved_reaction = dict(reaction)
        self._prior_checks = [dict(v) for v in video_checks or ()]
        self._reaction_ms = b.get("reaction_time_ms") if isinstance(b.get("reaction_time_ms"), int) else \
            b.get("first_visible_ms")
        self._raw_click_ms = b.get("raw_click_ms") if isinstance(b.get("raw_click_ms"), int) else self._reaction_ms
        self._entry = b.get("entry") or "from_utterance"
        self._sample_ref = copy.deepcopy(b.get("sample_ref")) if b.get("sample_ref") else None
        cands = []
        linked = 0
        for i, c in enumerate(b.get("candidates") or []):
            uid = c.get("utterance_id")
            urec = (utterances or {}).get(uid) if uid else None
            ub = (urec or {}).get("body") or {}
            cands.append(Candidate(start_ms=ub.get("start_ms"), end_ms=ub.get("end_ms"),
                                   heard_text=str(ub.get("heard_text") or ""),
                                   heard_source=ub.get("heard_text_source") or "typed",
                                   evidence=normalize_evidence(c.get("evidence")), time_manual=True,
                                   saved=dict(urec) if urec else None))
            if uid and uid == b.get("linked_utterance_id"):
                linked = i
        if not cands:
            cands = [Candidate(start_ms=self._reaction_ms, auto_start=True)]
        self._open_pos_ms = open_pos if open_pos is not None else self._reaction_ms
        self._cands, self._cur, self._linked = cands, 0, linked
        self._begin(b.get("chat_id"), similar=b.get("similar_chat_ids") or [])
        self._loading = True
        try:
            self.similar_box.setCurrentIndex(_find_data(self.similar_box, b.get("no_similar_chat_nearby")))
            self.on_topic.setChecked(bool(b.get("streamer_already_on_topic")))
            g = b.get("grade") if b.get("result") == "graded" else UNCONFIRMED
            self._set_grade_ui(g)
            self._set_conf_ui(b.get("confidence"))
            self.rationale.setText(str(b.get("rationale") or ""))
            self.override.setText(str(b.get("override_reason") or ""))
            self.unclear_box.setCurrentIndex(_find_data(self.unclear_box, b.get("unclear_cause")))
        finally:
            self._loading = False
        self.continuation.open_for(reaction.get("entity_id"), self._reaction_ms, b.get("chat_id"),
                                   existing=dict(continuation) if continuation else None)
        self.title.setText(tr("반응 라벨 편집 — 고치기·재확인"))
        self._recompute()

    def _position_now(self) -> int | None:
        """지금 재생 위치(VOD ms). 모르면 None."""
        if self._position_fn is None:
            return None
        try:
            return int(self._position_fn())
        except Exception:  # noqa: BLE001
            return None

    def open_position(self) -> int | None:
        """편집기를 연 순간의 재생 위치(빠른 입력의 발화 시작, D-16 2). 재생 위치를 몰랐으면 반응 지점."""
        return self._open_pos_ms

    def _reset_state(self) -> None:
        self._quick_auto = {}
        self.last_quick = None
        self.quick_error.hide()
        self.set_detail_expanded(_load_detail_pref())    # 열 때마다 라벨러가 고른 펼침 상태로(자동 펼침은 되돌림)
        self._existing = None
        self._saved_reaction = None
        self._prior_checks = []
        self._saved_passes = set()
        self._written = []
        self._pass_judgments = {}
        self._asr_revealed = False
        self._grade = None
        self._confidence = None
        self._target = None
        self._loading = True
        try:
            self._set_grade_ui(None)
            self._set_conf_ui(None)
            self.rationale.clear()
            self.override.clear()
            self.unclear_box.setCurrentIndex(0)
            self.similar_box.setCurrentIndex(0)
            self.on_topic.setChecked(False)
            self.msg.clear()
        finally:
            self._loading = False

    def _begin(self, chat_id: str | None, similar: Sequence[str] = ()) -> None:
        self._open = True
        self.setEnabled(True)
        self._fill_chat_list(chat_id, similar)
        self._load_cand_ui()
        self.continuation.open_for(None, self._reaction_ms, chat_id)
        self.reaction_label.setText("-" if self._reaction_ms is None
                                    else fmt_time(self._reaction_ms, frac_digits=1, always_hours=True))
        if self.review is not None:
            self.review.reset()
        self._update_asr_controls()
        self._recompute()
        self._update_review_window()

    def close_editor(self) -> None:
        """편집 상태를 끝낸다(저장·취소 뒤). 반복은 끈다."""
        self._open = False
        if self.review is not None and self.review.loop_on():
            self.review.stop_loop()
        self.setEnabled(False)

    # ================================================================ 후보 채팅
    def _fill_chat_list(self, chat_id: str | None, similar: Sequence[str]) -> None:
        self._loading = True
        keep = set(similar or ())
        try:
            self.chat_list.clear()
            d = self._chats
            if d is None:
                self.target_info.setText(tr("채팅이 실려 있지 않습니다"))
                return
            idx: list[int] = []
            if self._reaction_ms is not None:
                lo = bisect_left(d.times, self._reaction_ms - CANDIDATE_BEFORE_MS)
                hi = bisect_right(d.times, self._reaction_ms + CANDIDATE_AFTER_MS)
                idx = list(range(lo, hi))
            ti = d.index_of(chat_id) if chat_id else None
            if ti is not None and ti not in idx:
                idx.append(ti)
                idx.sort()
            text_code = KIND_CODES["text"]
            skipped = 0
            rows = 0
            select_item = None
            for i in idx:
                if d.kinds[i] != text_code:
                    skipped += 1
                    continue
                if rows >= CANDIDATE_MAX_ROWS and i != ti:
                    continue
                cid = d.chat_ids[i]
                line = f"{fmt_time(d.times[i], frac_digits=0, always_hours=True)}  {fmt_viewer_no(d.vnos[i])}  " \
                       f"{clean_line(d.texts[i])[:70]}"
                it = QListWidgetItem(line)
                it.setData(Qt.UserRole, cid)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if cid in keep else Qt.Unchecked)
                self.chat_list.addItem(it)
                rows += 1
                if cid == chat_id:
                    select_item = it
            if select_item is not None:
                self.chat_list.setCurrentItem(select_item)
                self._target = chat_id
            elif chat_id and ti is not None:
                self._target = None     # 대상이 일반 채팅이 아님
            self._skipped_non_text = skipped
        finally:
            self._loading = False
        self._update_target_info(chat_id)

    def _update_target_info(self, requested: str | None = None) -> None:
        d = self._chats
        parts = []
        if self._target and d is not None:
            i = d.index_of(self._target)
            if i is not None:
                parts.append(tr("대상: {0} {1}").format(fmt_time(d.times[i], frac_digits=1, always_hours=True),
                                                       fmt_viewer_no(d.vnos[i])))
        elif requested and d is not None and d.index_of(requested) is not None:
            parts.append(tr("고른 채팅은 일반 채팅이 아니라 반응 판정 대상이 아닙니다(후원·시스템 등)"))
        else:
            parts.append(tr("대상 채팅을 고르세요"))
        sk = getattr(self, "_skipped_non_text", 0)
        if sk:
            parts.append(tr("후원·시스템 등 {0}건은 목록에서 뺌").format(sk))
        self.target_info.setText(" · ".join(parts))
        # 빠른 입력 화면의 대상 채팅 한 줄
        i = d.index_of(self._target) if (self._target and d is not None) else None
        if i is not None:
            self.quick_target.setText(tr("채팅: {0}  {1}  {2}").format(
                fmt_time(d.times[i], frac_digits=0, always_hours=True), fmt_viewer_no(d.vnos[i]),
                clean_line(d.texts[i])[:80]))
        else:
            self.quick_target.setText(tr("채팅: 아직 고르지 않음 ('자세히 입력' 의 목록에서 고르세요)"))

    def target_chat_id(self) -> str | None:
        return self._target

    def set_target(self, chat_id: str) -> bool:
        for i in range(self.chat_list.count()):
            it = self.chat_list.item(i)
            if it.data(Qt.UserRole) == chat_id:
                self.chat_list.setCurrentItem(it)
                return True
        return False

    def similar_ids(self) -> list[str]:
        out = []
        for i in range(self.chat_list.count()):
            it = self.chat_list.item(i)
            cid = it.data(Qt.UserRole)
            if it.checkState() == Qt.Checked and cid != self._target:
                out.append(cid)
        return out

    def _on_target_changed(self, item) -> None:
        if self._loading:
            return
        self._target = item.data(Qt.UserRole) if item is not None else None
        self._update_target_info()
        self._sync_time_window()
        self.continuation.set_reaction(None if self._saved_reaction is None else self._saved_reaction.get("entity_id"),
                                       target_chat_id=self._target)
        self._recompute()
        self._update_review_window()

    def _on_similar_changed(self) -> None:
        if self._loading:
            return
        if self.similar_ids() and self.similar_box.currentData() is not False:
            self.similar_box.setCurrentIndex(_find_data(self.similar_box, False))   # 체크했으면 '있음'
        self._recompute()

    def _chat_time(self, chat_id: str | None) -> int | None:
        d = self._chats
        if d is None or not chat_id:
            return None
        i = d.index_of(chat_id)
        return None if i is None else int(d.times[i])

    def _chat_kind_ok(self, chat_id: str | None) -> bool:
        d = self._chats
        if d is None or not chat_id:
            return False
        i = d.index_of(chat_id)
        return i is not None and d.kinds[i] == KIND_CODES["text"]

    # ================================================================ 후보 발화
    def candidates(self) -> list[Candidate]:
        self._store_cand_ui()
        return list(self._cands)

    def current_index(self) -> int:
        return self._cur

    def add_candidate(self) -> int:
        self._store_cand_ui()
        start = None
        if self._position_fn is not None:
            try:
                start = int(self._position_fn())
            except Exception:  # noqa: BLE001
                start = None
        self._cands.append(Candidate(start_ms=start))
        self._cur = len(self._cands) - 1
        self._load_cand_ui()
        self._recompute()
        self._update_review_window()
        return self._cur

    def remove_candidate(self) -> None:
        if len(self._cands) <= 1:
            self._cands = [Candidate(start_ms=self._reaction_ms, auto_start=True)]
            self._cur = 0
            self._linked = 0
        else:
            del self._cands[self._cur]
            if self._linked == self._cur:
                self._linked = 0
            elif self._linked > self._cur:
                self._linked -= 1
            self._cur = max(0, min(self._cur, len(self._cands) - 1))
        self._load_cand_ui()
        self._recompute()
        self._update_review_window()

    def _switch_cand(self, i: int) -> None:
        if not (0 <= i < len(self._cands)) or i == self._cur:
            return
        self._store_cand_ui()
        self._cur = i
        self._load_cand_ui()
        self._recompute()

    def set_linked(self, i: int) -> None:
        if 0 <= i < len(self._cands):
            self._linked = i
            self._load_cand_ui()
            self._recompute()

    def _on_linked_toggled(self, on: bool) -> None:
        if self._loading:
            return
        if on:
            self._linked = self._cur
        elif self._linked == self._cur:
            self._loading = True
            self.linked_box.setChecked(True)     # 연결 발화는 늘 하나: 다른 후보를 골라야 바뀐다
            self._loading = False
        self._recompute()

    def _store_cand_ui(self) -> None:
        if not (0 <= self._cur < len(self._cands)):
            return
        c = self._cands[self._cur]
        start = parse_time(self.utt_start.text())
        if start != c.start_ms:
            c.auto_start = False                 # 라벨러가 시작을 고쳤다(빠른 입력이 덮어쓰지 않는다)
        c.start_ms = start
        c.end_ms = parse_time(self.utt_end.text())
        c.heard_text = self.heard.text()
        c.evidence = self.evidence.value()
        c.time_manual = self.evidence.time_manual()

    def _load_cand_ui(self) -> None:
        if not self._cands:
            return
        c = self._cands[self._cur]
        self._loading = True
        try:
            self.utt_start.setText("" if c.start_ms is None else fmt_time(c.start_ms, always_hours=True))
            self.utt_end.setText("" if c.end_ms is None else fmt_time(c.end_ms, always_hours=True))
            self.heard.setText(c.heard_text)
            self.heard_count.setText(str(len(c.heard_text)))
            self.evidence.set_value(c.evidence, time_manual=c.time_manual)
            self.linked_box.setChecked(self._linked == self._cur)
            n = len(self._cands)
            self.cand_label.setText(tr("후보 발화 {0}/{1}").format(self._cur + 1, n))
            self.cand_prev.setEnabled(self._cur > 0)
            self.cand_next.setEnabled(self._cur < n - 1)
        finally:
            self._loading = False
        self._sync_time_window()
        self._show_asr_text()

    def _utt_now(self, edit: QLineEdit) -> None:
        if self._position_fn is None:
            return
        try:
            edit.setText(fmt_time(int(self._position_fn()), always_hours=True))
        except Exception:  # noqa: BLE001
            return
        self._on_utt_times()

    def _on_utt_times(self) -> None:
        if self._loading:
            return
        self._store_cand_ui()
        self._sync_time_window()
        self._show_asr_text()
        self._recompute()
        self._update_review_window()

    def _sync_time_window(self) -> None:
        c = self._cands[self._cur] if 0 <= self._cur < len(self._cands) else None
        chat_ms = self._chat_time(self._target)
        self.evidence.set_time_window(chat_ms, None if c is None else c.start_ms)
        if c is not None:
            c.evidence = self.evidence.value()

    def _on_heard_edited(self, text: str) -> None:
        self.heard_count.setText(str(len(text)))
        c = self._cands[self._cur]
        c.heard_text = text
        if c.pasted_text is not None:
            c.heard_source = "asr_unedited" if text == c.pasted_text else "asr_edited"
        elif c.heard_source not in ("asr_edited", "asr_unedited"):
            c.heard_source = "typed"
        self._recompute()

    def _on_evidence_changed(self) -> None:
        if self._loading:
            return
        self._store_cand_ui()
        self._recompute()

    # ================================================================ 자동 인식 문장 (참고)
    def _update_asr_controls(self) -> None:
        pol = self.asr_policy()
        has = self._asr_lookup is not None
        self.asr_show.setVisible(has and pol in (vis.ON_TOGGLE, vis.ALWAYS) and not self._asr_revealed
                                 and pol != vis.ALWAYS)
        self.asr_paste.setVisible(has and pol != vis.NEVER)
        if pol == vis.ALWAYS and has:
            self._asr_revealed = True
        self._show_asr_text()

    def asr_visible(self) -> bool:
        return self._asr_revealed and self.asr_policy() != vis.NEVER and self._asr_lookup is not None

    def reveal_asr(self) -> bool:
        """자동 인식 문장 '참고 보기'. 토글 정책이면 exposed(asr_text) 를 남긴다(03 6-4 view.exposed)."""
        pol = self.asr_policy()
        if pol == vis.NEVER or self._asr_lookup is None:
            return False
        if not self._asr_revealed and pol == vis.ON_TOGGLE and self._exposed_cb is not None:
            try:
                self._exposed_cb(vis.ASR_TEXT, self._target,
                                 None if self._existing is None else self._existing.get("entity_id"))
            except Exception as e:  # noqa: BLE001
                self.msg.setText(tr("열람 기록을 남기지 못해 보이지 않습니다: {0}").format(type(e).__name__))
                return False
        self._asr_revealed = True
        for c in self._cands:
            c.used_asr = True
        self._update_asr_controls()
        return True

    def _asr_for_current(self) -> str | None:
        if self._asr_lookup is None or not self._cands:
            return None
        c = self._cands[self._cur]
        a = c.start_ms if c.start_ms is not None else self._reaction_ms
        if a is None:
            return None
        b = c.end_ms if (c.end_ms is not None and c.end_ms > a) else a + 5000
        try:
            t = self._asr_lookup(int(a), int(b))
        except Exception as e:  # noqa: BLE001
            LOG.debug("자동 인식 조회 실패: %s", type(e).__name__)
            return None
        return t if isinstance(t, str) and t.strip() else None

    def _show_asr_text(self) -> None:
        if not self.asr_visible():
            self.asr_label.setText(tr("자동 인식 문장: 숨김") if self._asr_lookup is not None
                                   and self.asr_policy() != vis.NEVER else tr("자동 인식 문장: 없음"))
            return
        t = self._asr_for_current()
        self.asr_label.setText(tr("참고(자동 인식, 직접 듣고 고칠 것): {0}").format(t) if t
                               else tr("참고: 이 범위의 자동 인식 문장이 없습니다"))

    def paste_asr(self) -> bool:
        """참고 문장을 들은 내용 칸에 붙여 넣는다(heard_text_source = asr_unedited, 고치면 asr_edited)."""
        if not self.asr_visible() and not self.reveal_asr():
            return False
        t = self._asr_for_current()
        if not t:
            return False
        t = t.strip()[:HEARD_MAX]
        c = self._cands[self._cur]
        c.pasted_text = t
        c.heard_text = t
        c.heard_source = "asr_unedited"
        c.used_asr = True
        self.heard.setText(t)
        self.heard_count.setText(str(len(t)))
        self._recompute()
        return True

    # ================================================================ 빠른 입력 (D-16)
    def quick_input(self, key: str) -> dict | None:
        """빠른 입력 단추(D-16 1). 후보 발화·연결·증거·등급·확신도·근거·들은 내용을 채우고 save() 를 부른다.

        돌려주는 값은 save() 와 같다(막히면 None — 채운 값은 남고 이유가 메시지 칸에 보인다).
        등급은 rules.grade_conditions 로만 정한다(_quick_grade). 엔진 결과는 이 편집기에 오지 않는다.
        """
        if not self._open:
            return None
        p = next((x for x in QUICK_PRESETS if x.key == key), None)
        if p is None:
            return None
        self.last_quick = p.key
        self.quick_error.hide()
        if not self._target:
            self._quick_fail(tr("어느 채팅에 대한 대답인지 먼저 골라 주세요. 아래 목록에서 그 채팅을 한 번 누르고 다시 단추를 누르면 됩니다."))
            return None
        self._store_cand_ui()
        if not self._cands:
            self._cands = [Candidate(start_ms=self._reaction_ms, auto_start=True)]
            self._linked = 0
        idx = self._linked if 0 <= self._linked < len(self._cands) else 0
        self._cur = idx
        c = self._cands[idx]
        # 발화 시작·끝 (D-16 2): 라벨러가 고친 값은 그대로 둔다
        start = self._open_pos_ms if self._open_pos_ms is not None else self._reaction_ms
        # 반응 시각도 멈춘 자리로 (빠른 입력은 '대답을 듣고 멈춘 순간' 이 반응 지점이다 — 채팅 시각을 넣으면 지연이 0 이 된다)
        if self._open_pos_ms is not None:
            self._reaction_ms = int(self._open_pos_ms)
        # '질문에 대답' 은 라벨러가 이 채팅을 대답 대상으로 고른 것이다: 비슷한 채팅을 따로 체크하지 않았으면 '없음' 으로 본다
        if p.key == "answer" and not self.similar_ids() and self.similar_box.currentData() is None:
            i_none = _find_data(self.similar_box, True)
            if i_none >= 0:
                self.similar_box.setCurrentIndex(i_none)
        if c.start_ms is None or (c.auto_start and start is not None):
            c.start_ms = start
            c.auto_start = True
        if c.end_ms is None and c.start_ms is not None:
            c.end_ms = int(c.start_ms) + QUICK_END_MS
        # 들은 내용 (D-16 3): 비었거나 앞서 빠른 입력이 넣은 글이면 단추 이름
        label = tr(p.label)
        if not c.heard_text.strip() or c.heard_text == self._quick_auto.get("heard") or _is_quick_text(c.heard_text):
            c.heard_text = label[:HEARD_MAX]
            c.heard_source = "typed"
            c.pasted_text = None
            self._quick_auto["heard"] = c.heard_text
        # 증거: 단추의 증거를 체크한다(이미 체크한 다른 증거는 그대로). 시간 창은 앱 계산(_sync_time_window)
        ev = normalize_evidence(c.evidence)
        for k in p.evidence:
            if not ev[k]["present"]:
                ev[k] = {"present": True, "at_ms": c.start_ms}
        c.evidence = ev
        self._linked = idx
        self._load_cand_ui()                     # 화면에 싣고 시간 창을 다시 계산한다
        self._store_cand_ui()
        # 등급 — 조건 충족 규칙으로만
        gc = self.conditions()
        g = self._quick_grade(p, gc)
        self._loading = True
        try:
            self._set_grade_ui(g)
            if g == "unclear":
                cause = p.unclear_cause or gc.unclear_cause or "other"
                self.unclear_box.setCurrentIndex(_find_data(self.unclear_box, cause))
            self._set_conf_ui("certain")
            rat = self.rationale.text().strip()
            if not rat or rat == self._quick_auto.get("rationale") or _is_quick_text(rat):
                t = self._reaction_ms if self._reaction_ms is not None else c.start_ms
                text = f"{label} {_hhmmss(t)}" if t is not None else label
                self.rationale.setText(text[:RATIONALE_MAX])
                self._quick_auto["rationale"] = self.rationale.text().strip()
        finally:
            self._loading = False
        self._recompute()
        self._update_review_window()
        self._status_text = tr("저장: {0} ({1})").format(tr(GRADE_LABELS[g]), label)
        try:
            res = self.save()
        finally:
            self._status_text = None
        if res is None:
            v = self._last_validation
            why = (v.errors[0] if v is not None and v.errors else self.msg.text().strip()) or tr("알 수 없는 문제")
            self._quick_fail(tr("저장하지 못했습니다. {0}. 아래 '자세히 입력' 에서 고친 뒤 저장(Ctrl+Enter)하세요.")
                             .format(why.lstrip("✗! ").rstrip(". ")))
        return res

    def _quick_grade(self, p: QuickPreset, gc: rules.GradeConditions) -> str:
        """단추가 바라는 등급을 조건이 허락하면 그 등급. 아니면 그 아래에서 허락되는 첫 등급, 그래도 없으면 허락되는 가장
        높은 등급, 끝으로 불명확(후보가 있으면 늘 고를 수 있다, 01 3-1). 고른 등급은 늘 조건과 같음(consistent)이다."""
        if p.want == "unclear":
            return "unclear"
        li = self._linked_filled_index()
        linked = gc.per_candidate[li] if (li is not None and li < len(gc.per_candidate)) else None

        def ok(g: str) -> bool:
            if g not in gc.eligible_grades:
                return False
            if g == "direct":
                return rules.direct_allowed(linked)
            if g == "likely":
                return rules.likely_allowed(linked)
            return True

        ranked = [g for g in GRADES if g != "unclear"]
        for g in ranked[ranked.index(p.want):] + ranked:
            if ok(g):
                return g
        return "unclear"

    # ================================================================ 등급·확신도·근거
    def set_grade(self, g: str | None) -> None:
        """등급 버튼(Ctrl+1~4) 또는 반응 미확인(Ctrl+5). 같은 것을 다시 누르면 그대로 둔다."""
        if not self._open:
            return
        self._set_grade_ui(g)
        self._recompute()
        self._update_review_window()

    def _set_grade_ui(self, g: str | None) -> None:
        self._grade = g
        self.grade_group.setExclusive(False)
        for k, b in self.grade_buttons.items():
            b.setChecked(k == g)
        self.grade_group.setExclusive(True)

    def grade(self) -> str | None:
        return self._grade

    def set_confidence(self, c: str | None) -> None:
        if not self._open:
            return
        self._set_conf_ui(c)
        self._recompute()

    def _set_conf_ui(self, c: str | None) -> None:
        self._confidence = c if c in CONFIDENCES else None
        self.conf_group.setExclusive(False)
        for k, b in self.conf_buttons.items():
            b.setChecked(k == self._confidence)
        self.conf_group.setExclusive(True)

    def insert_time(self) -> None:
        """Ctrl+T: 근거 칸 커서 자리에 지금 VOD 시각(h:mm:ss)을 넣는다."""
        if self._position_fn is None:
            return
        try:
            t = fmt_time(int(self._position_fn()), ms_digits=False, always_hours=True)
        except Exception:  # noqa: BLE001
            return
        self.rationale.insert(t + " ")
        self.rationale.setFocus()
        self._recompute()

    def _reaction_to_now(self) -> None:
        if self._position_fn is None:
            return
        try:
            self._reaction_ms = int(self._position_fn())
        except Exception:  # noqa: BLE001
            return
        self.reaction_label.setText(fmt_time(self._reaction_ms, frac_digits=1, always_hours=True))
        self.continuation.set_reaction(None, self._reaction_ms)
        self._recompute()

    # ================================================================ 조건 충족·검증
    def filled_candidates(self) -> list[Candidate]:
        return [c for c in self._cands if c.filled()]

    def conditions(self) -> rules.GradeConditions:
        """채워 둔 후보 발화들의 조건 충족 상태(rules.grade_conditions). 등급을 고르지 않는다."""
        self._store_cand_ui()
        fc = self.filled_candidates()
        return rules.grade_conditions([{"evidence": c.evidence} for c in fc], self._cfg,
                                      no_similar_chat_nearby=self.similar_box.currentData(),
                                      streamer_already_on_topic=self.on_topic.isChecked() or None)

    def _linked_filled_index(self) -> int | None:
        """연결 발화의 '채운 후보' 안 순번. 연결 후보가 비어 있으면 None."""
        fc = self.filled_candidates()
        if not (0 <= self._linked < len(self._cands)):
            return None
        c = self._cands[self._linked]
        return fc.index(c) if c in fc else None

    def validate(self) -> Validation:
        self._store_cand_ui()
        errs: list[str] = []
        warns: list[tuple[str, str]] = []
        g = self._grade
        gc = self.conditions()
        fc = self.filled_candidates()
        # 대상
        if not self._target:
            errs.append(tr("대상 채팅을 고르세요"))
        elif not self._chat_kind_ok(self._target):
            errs.append(tr("대상 채팅이 일반 채팅이 아닙니다(후원·시스템은 판정하지 않음)"))
        chat_ms = self._chat_time(self._target)
        seg = find_segment(self._segments, chat_ms)
        if self._target and self._label_of is not None:
            try:
                other = self._label_of(self._target)
            except Exception:  # noqa: BLE001 — 조회 실패는 막지 않는다(store 가 판 연결을 다시 본다)
                other = None
            mine = (self._existing or self._saved_reaction or {}).get("entity_id")
            if other is not None and other != mine:
                errs.append(tr("이 채팅에는 이미 반응 라벨이 있습니다 — 새로 만들지 않고 그 라벨을 고치세요 (채팅 1건 = 라벨 1건)"))
        if self._target and chat_ms is not None:
            if seg is None or _seg_body(seg).get("category") != JUST_CHATTING:
                errs.append(tr("대상 채팅이 저스트채팅 구간 밖입니다(구간 라벨을 먼저)"))
            elif _seg_id(seg) is None:
                errs.append(tr("구간 라벨에 entity_id 가 없습니다"))
            if not self.target_in_media() and not self._media_coverage:
                errs.append(tr("영상이 연결되지 않아 저장할 수 없습니다 — 먼저 영상을 연결하세요 (소리를 들을 수 없는 자리는 판정하지 않음)"))
            elif not self.target_in_media():
                a, b = review_window(chat_ms, None, self._pre_roll_ms, self._post_roll_ms)
                errs.append(tr("대상 채팅의 확인 범위({0} ~ {1})가 영상 밖이라 소리를 들을 수 없어 저장할 수 없습니다"
                               " (영상이 덮는 자리의 채팅만 판정)").format(
                    fmt_time(a, frac_digits=0, always_hours=True), fmt_time(b, frac_digits=0, always_hours=True)))
        # 등급
        if g is None:
            errs.append(tr("등급을 고르세요 (반응 미확인이면 Ctrl+5)"))
        elif g == UNCONFIRMED:
            if fc:
                errs.append(tr("후보 발화가 있으면 '반응 미확인' 을 고를 수 없습니다(후보를 지우거나 등급을 고르세요)"))
        else:
            if not fc:
                errs.append(tr("등급을 주려면 후보 발화가 1개 이상 필요합니다"))
            for n, c in enumerate(self._cands, start=1):
                if not c.filled():
                    continue
                if c.start_ms is None or c.end_ms is None:
                    errs.append(tr("후보 발화 {0}: 시작·끝 시각이 필요합니다").format(n))
                elif c.end_ms < c.start_ms:
                    errs.append(tr("후보 발화 {0}: 끝이 시작보다 앞섭니다").format(n))
                if not c.heard_text.strip():
                    errs.append(tr("후보 발화 {0}: 직접 듣고 적은 문장이 필요합니다").format(n))
                if (chat_ms is not None and c.start_ms is not None and c.start_ms < chat_ms
                        and c.evidence.get("text_repeated", {}).get("present")):
                    warns.append(("repeat_before_chat",
                                  tr("후보 발화 {0} 가 채팅보다 앞서는데 '문구 되풀이' 를 체크했습니다").format(n)))
                if c.start_ms is not None and find_segment(self._segments, c.start_ms) is None:
                    warns.append(("other", tr("후보 발화 {0} 가 구간 라벨 밖입니다").format(n)))
            if g in LINKED_GRADES:
                li = self._linked_filled_index()
                if li is None:
                    errs.append(tr("직접·높음은 연결 발화가 필요합니다(채운 후보 하나를 '연결 발화' 로)"))
                elif g == "direct" and not rules.direct_allowed(gc.per_candidate[li]):
                    errs.append(tr("연결 발화에 강한 증거가 없어 '직접 반응' 으로 저장할 수 없습니다(안내서 4-2)"))
                elif g == "likely" and not rules.likely_allowed(gc.per_candidate[li]):
                    # 조건을 넘는 등급은 고를 수 없다(01 1-3 규칙 7·3-1, 안내서 4-2). 낮춰 고르는 것만 사유로 허용.
                    errs.append(tr("연결 발화가 '높음' 조건(중간 증거 2개 이상 · 비슷한 채팅 없음 확인 · 이미 그 주제 아님)을 "
                                   "채우지 않아 '연결 가능성 높음' 으로 저장할 수 없습니다(안내서 4-2)"))
                if self._reaction_ms is None:
                    errs.append(tr("반응 지점이 필요합니다"))
            if g == "unclear" and self.unclear_box.currentData() is None:
                errs.append(tr("불명확 사유를 고르세요"))
        gvs = None
        if g is not None:
            gvs = rules.grade_vs_conditions(None if g == UNCONFIRMED else g, gc)
            if gvs == "deviates":
                if not self.override.text().strip():
                    errs.append(tr("조건 충족 등급과 다르게 골랐습니다. 사유 한 줄이 필요합니다"))
                warns.append(("grade_rule_override", tr("조건 충족 등급과 다르게 확정")))
        # 확신도·근거
        if self._confidence is None:
            errs.append(tr("확신도를 고르세요 (Alt+1~3)"))
        rat = self.rationale.text().strip()
        if not rat:
            errs.append(tr("근거 한 줄이 필요합니다"))
        elif not parse_timestamps(rat):
            warns.append(("rationale_without_time", tr("근거에 시각이 없습니다 (Ctrl+T)")))
        # 이어짐
        if g in LINKED_GRADES and self.continuation.is_touched():
            errs.extend(self.continuation.errors())
        elif g in LINKED_GRADES:
            warns.append((CONTINUATION_MISSING, tr("직접·높음 반응인데 이어짐을 비웠습니다 — 저장은 됩니다(이어짐은 선택)")))
        # 들은 범위·재확인
        heard = self._heard_count()
        if self.review is not None and heard == 0:
            warns.append(("audio_not_heard", tr("확인 범위를 소리로 다 듣지 않았습니다")))
        if g in LINKED_GRADES and heard < self._min_views:
            # D-16 4: 시범에서는 '권장'. 막지 않고 미완료 표시(경고·속이 빈 마커)만 남긴다
            warns.append(("recheck_missing", tr("7-2 두 번 듣기 권장 (지금 {0}/{1}) — 저장은 되고 '미완료' 로 표시됩니다")
                          .format(heard, self._min_views)))
        v = Validation(errs, warns, gc, gvs)
        self._last_validation = v
        return v

    def _heard_count(self) -> int:
        """'들었다' 고 인정된 회차 수 = 불러온 영상 확인 기록 + 이번 편집의 회차."""
        prior = rules.heard_count([(v.get("body") or v) for v in self._prior_checks],
                                  self._min_coverage)      # 내보내기·마커와 같은 규칙·같은 비율
        if self.review is None:
            return prior
        return prior + self.review.heard_count()

    def is_complete(self) -> bool:
        """'완료' 인가: 직접·높음이면 들었다고 인정된 회차가 min_views 이상(질문 15 기본안). 나머지는 늘 완료."""
        if self._grade not in LINKED_GRADES:
            return True
        return self._heard_count() >= self._min_views

    def _recompute(self) -> None:
        if self._loading or not self._open:
            return
        v = self.validate()
        gc = v.conditions
        self.cond_label.setText(self._conditions_text(gc))
        g = self._grade
        self.unclear_box.setEnabled(g == "unclear")
        self.override.setEnabled(v.grade_vs == "deviates" or bool(self.override.text().strip()))
        linked = g in LINKED_GRADES
        self.cont_box.setEnabled(linked)
        self.continuation.setEnabled(linked)
        seg = find_segment(self._segments, self._chat_time(self._target))
        e = _seg_body(seg).get("end_ms")
        self.continuation.set_segment_end(e if isinstance(e, int) else None)
        self._update_recheck_label()
        lines = [f"✗ {x}" for x in v.errors] + [f"! {w}" for _c, w in v.warnings]
        self.msg.setText("\n".join(lines))
        self.save_btn.setEnabled(not v.errors)
        self.stateChanged.emit()

    def _conditions_text(self, gc: rules.GradeConditions | None) -> str:
        """'조건 충족' 줄(01 3-1 표). 등급을 고르지 않고 충족 여부만 쓴다."""
        if gc is None:
            return ""
        if gc.result == "unconfirmed":
            return tr("조건 충족: 후보 발화 없음 → '반응 미확인' 만 해당 (Ctrl+5)")
        marks = []
        for g in GRADES:
            ok = g in gc.eligible_grades or g == "unclear"     # 불명확은 후보가 있으면 늘 고를 수 있다(01 3-1)
            marks.append(f"{tr(GRADE_LABELS[g])} {'✓' if ok else '✗'}")
        parts = [tr("조건 충족: ") + " · ".join(marks)]
        for c in gc.per_candidate:
            info = tr("후보 {0}: 강 {1} · 중 {2} · 약 {3}").format(c.index + 1, c.strong, c.medium, c.weak)
            if c.time_only:
                info += " · " + tr("시간 창만 있음(셈 안 함)")
            if "similar_chat" in c.blocked:
                info += " · " + tr("비슷한 채팅 있음 → 높음 막힘")
            elif "similar_chat_unknown" in c.blocked:
                info += " · " + tr("'비슷한 채팅 없음' 확인 전 → 높음 막힘")
            if "already_on_topic" in c.blocked:
                info += " · " + tr("이미 그 주제 중 → 높음 막힘")
            parts.append(info)
        if gc.tied:
            parts.append(tr("강한 증거 없는 '높음' 후보가 여럿"))
        parts.append(tr("등급은 직접 고릅니다 (Ctrl+1~4)"))
        return "\n".join(parts)

    # ================================================================ 재확인
    def _update_review_window(self) -> None:
        if self.review is None or not self._open:
            return
        chat_ms = self._chat_time(self._target)
        if chat_ms is None:
            chat_ms = self._reaction_ms
        if chat_ms is None:
            return
        ends = [c.end_ms for c in self._cands if c.filled() and c.end_ms is not None]
        self.review.set_window_for(chat_ms, max(ends) if ends else self._reaction_ms)
        self.review.set_osd_text(self._osd_text(), show=False)
        self._update_recheck_label()

    def _osd_text(self) -> str:
        d = self._chats
        if d is None or not self._target:
            return tr("판정 중")
        i = d.index_of(self._target)
        if i is None:
            return tr("판정 중")
        return tr("판정 중 · {0} · {1}").format(fmt_viewer_no(d.vnos[i]),
                                                fmt_time(d.times[i], frac_digits=0, always_hours=True))

    def listen_again(self) -> None:
        """'7-2 범위 다시 듣기': 범위 반복을 켜고 새 회차를 시작한다(ReviewPlayer.start_loop)."""
        if self.review is None:
            self.msg.setText(tr("재생기가 연결되어 있지 않습니다"))
            return
        self._update_review_window()
        self.review.start_loop()
        self._update_recheck_label()

    def _on_pass_started(self, idx: int) -> None:
        if idx > 0 and (idx - 1) not in self._pass_judgments:
            self._pass_judgments[idx - 1] = self._judgment()
        self._update_recheck_label()

    def _on_coverage(self, _c: float) -> None:
        self._update_recheck_label()

    def _update_recheck_label(self) -> None:
        if self.review is None:
            self.recheck_label.setText(tr("재생기 없음 — 들은 범위를 잴 수 없습니다"))
            return
        heard = self._heard_count()
        pct = int(round(self.review.current_coverage() * 100))
        need = self._min_views if self._grade in LINKED_GRADES else 1
        state = tr("충족") if heard >= need else tr("부족")
        self.recheck_label.setText(tr("들은 회차 {0}/{1} · 지금 회차 들은 범위 {2}% · {3}").format(
            heard, need, pct, state))

    def _judgment(self) -> dict:
        """지금 판단 스냅샷(회차가 바뀔 때 남긴다). video_check.judgment 에는 앞 셋만 들어간다."""
        g = self._grade
        return {"result": None if g is None else ("unconfirmed" if g == UNCONFIRMED else "graded"),
                "grade": g if g in GRADES else None, "confidence": self._confidence,
                "rationale": self.rationale.text().strip()[:RATIONALE_MAX]}

    # ================================================================ 저장
    def build_reaction_body(self, utterance_ids: Sequence[str], v: Validation) -> dict:
        """스키마 reactionBody. utterance_ids 는 채운 후보 순서대로의 발화 entity_id."""
        g = self._grade
        gc = v.conditions
        chat_ms = self._chat_time(self._target)
        seg = find_segment(self._segments, chat_ms)
        graded = g in GRADES
        li = self._linked_filled_index()
        fc = self.filled_candidates()
        aids = []
        if any(c.used_asr or c.heard_source != "typed" for c in fc) or self._asr_revealed:
            aids.append("asr_text")
        aids.append("grade_conditions")
        body = {
            "chat_id": self._target,
            "segment_id": _seg_id(seg),
            "entry": self._entry,
            "result": "graded" if graded else "unconfirmed",
            "grade": g if graded else None,
            "eligible_grades": list(gc.eligible_grades) if graded else [],
            "grade_vs_conditions": v.grade_vs if graded else "not_applicable",
            "override_reason": (self.override.text().strip() or None) if v.grade_vs == "deviates" else None,
            "unclear_cause": self.unclear_box.currentData() if g == "unclear" else None,
            "candidates": [{"utterance_id": uid, "evidence": normalize_evidence(c.evidence)}
                           for uid, c in zip(utterance_ids, fc)] if graded else [],
            "linked_utterance_id": utterance_ids[li] if (g in LINKED_GRADES and li is not None) else None,
            "first_visible_ms": chat_ms,
            "first_visible_source": "chat_clock",
            "reaction_time_ms": self._reaction_ms if graded else None,
            "raw_click_ms": self._raw_click_ms,
            "snapped": None,
            "no_similar_chat_nearby": self.similar_box.currentData(),
            "similar_chat_ids": self.similar_ids(),
            "streamer_already_on_topic": self.on_topic.isChecked(),
            "confidence": self._confidence,
            "rationale": self.rationale.text().strip()[:RATIONALE_MAX],
            "aids_used": aids,
        }
        if self._sample_ref is not None:
            body["sample_ref"] = copy.deepcopy(self._sample_ref)
        fj, changed = self._first_judgment(body)
        body["first_judgment"] = fj
        body["changed_on_recheck"] = changed
        return body

    def _in_edit_first_judgment(self, body: dict) -> dict | None:
        """한 번의 편집 안에서 첫 회차를 듣고 판단한 뒤 재확인 회차에서 판단을 바꿨으면 첫 회차 판단(7-2, 01 규칙 17).

        그 판단을 담은 판이 없어 version_id 는 null 이다. 근거 칸이 그때 비어 있었으면 최종 근거를 쓴다(스키마 필수).
        """
        if self.review is None or len(self.review.pass_summaries()) < 2:
            return None
        j = self._pass_judgments.get(min(self._pass_judgments)) if self._pass_judgments else None
        if not j or j.get("result") not in ("graded", "unconfirmed") or j.get("confidence") is None:
            return None
        if (j["result"], j.get("grade"), j["confidence"]) == (body["result"], body["grade"], body["confidence"]):
            return None
        return {"version_id": None, "result": j["result"], "grade": j.get("grade"), "confidence": j["confidence"],
                "rationale": j.get("rationale") or body["rationale"]}

    def _first_judgment(self, body: dict) -> tuple[dict | None, bool]:
        """처음 판단(7-2). 고치는 반응이면 재확인(이번에 들은 회차)이 있고 저장된 판과 판단이 바뀌었을 때 그 판을 복사한다.
        새 반응이면 이번 편집의 첫 회차 판단과 최종 판단이 다를 때(_in_edit_first_judgment)."""
        ex = self._existing
        if ex is None:
            fj = self._in_edit_first_judgment(body)
            return (fj, True) if fj is not None else (None, False)
        eb = ex.get("body") or {}
        prev_fj = eb.get("first_judgment")
        listened = self.review is not None and bool(self.review.pass_summaries())
        changed = (eb.get("result"), eb.get("grade"), eb.get("confidence")) != \
                  (body["result"], body["grade"], body["confidence"])
        if listened and changed:
            if isinstance(prev_fj, dict):
                return copy.deepcopy(prev_fj), True
            return {"version_id": ex.get("id"), "result": eb.get("result"), "grade": eb.get("grade"),
                    "confidence": eb.get("confidence"), "rationale": eb.get("rationale")}, True
        return (copy.deepcopy(prev_fj) if isinstance(prev_fj, dict) else None), False

    def _save_versioned(self, kind: str, body: dict, current: dict | None) -> dict:
        """같은 내용이면 저장하지 않고 current 를 돌려준다. 다르면 새 대상 또는 current 를 대체하는 새 판."""
        if current is not None and current.get("kind", kind) == kind and _body_eq(current.get("body"), body):
            return current
        draft: dict = {"kind": kind, "body": body}
        if current is not None and isinstance(current.get("entity_id"), str):
            draft["entity_id"] = current["entity_id"]
            draft["supersedes"] = current.get("id")
        rec = self._save_cb(draft)
        if not isinstance(rec, dict) or not isinstance(rec.get("entity_id"), str):
            raise RuntimeError("save 가 레코드를 돌려주지 않았습니다")
        self._written.append(rec)
        return rec

    def save(self) -> dict | None:
        """Ctrl+Enter. 막는 문제가 있으면 None(편집 계속). 성공하면 결과 dict 를 돌려주고 finished 를 보낸다."""
        if not self._open:
            return None
        v = self.validate()
        if v.errors:
            self._recompute()
            return None
        result: dict = {"utterances": [], "reaction": None, "continuation": None, "video_checks": [],
                        "warnings": [], "complete": False}
        try:
            chat_ms = self._chat_time(self._target)
            graded = self._grade in GRADES
            uids: list[str] = []
            if graded:
                for c in self.filled_candidates():
                    useg = find_segment(self._segments, c.start_ms) or find_segment(self._segments, chat_ms)
                    rec = self._save_versioned("utterance", utterance_body(c, _seg_id(useg)), c.saved)
                    c.saved = rec
                    uids.append(rec["entity_id"])
                    result["utterances"].append(rec)
            rbody = self.build_reaction_body(uids, v)
            rrec = self._save_versioned("reaction", rbody, self._saved_reaction)
            self._saved_reaction = rrec
            result["reaction"] = rrec
            eid = rrec["entity_id"]
            # 이어짐
            if self._grade in LINKED_GRADES and self.continuation.is_touched():
                self.continuation.set_reaction(eid, self._reaction_ms, self._target)
                cbody = self.continuation.body(eid)
                crec = self._save_versioned("continuation", cbody, self.continuation.existing())
                self.continuation.mark_saved(crec)
                result["continuation"] = crec
            elif self._grade not in LINKED_GRADES:
                # 이어짐은 직접·높음 반응에만 매긴다(안내서 4-3, 03 12-3): 낮춘 반응에 남은 이어짐은 철회한다.
                ex = self.continuation.existing()
                if isinstance(ex, dict) and ex.get("kind", "continuation") == "continuation"                         and isinstance(ex.get("entity_id"), str):
                    result["continuation_retracted"] = self._retract(ex, "other")
                    self.continuation.mark_saved(None)
            # 영상 확인 회차
            result["video_checks"] = self._save_video_checks(rrec)
            # 경고 기록
            for code, text in v.warnings:
                self._write_warning(code, eid, text)
                result["warnings"].append(code)
        except Exception as e:  # noqa: BLE001 — 저장 실패는 화면에 드러내고 편집을 계속한다
            LOG.warning("반응 저장 실패: %s", type(e).__name__)
            self.msg.setText(tr("저장 실패 (편집은 그대로): {0}").format(_err_text(e)))
            return None
        result["complete"] = self.is_complete()
        result["written"] = list(self._written)
        if self._status_text:
            result["status_text"] = self._status_text     # 빠른 입력: 작업대 상태 줄에 보일 한 줄
        if not result["complete"]:
            self.msg.setText(tr("저장했습니다 — 7-2 두 번 듣기(권장)가 남아 '미완료' 로 표시됩니다"))
        self.close_editor()
        self.finished.emit(result)
        return result

    def _retract(self, rec: Mapping, reason: str) -> dict:
        """대상 철회. retract 콜백이 없으면 save 에 kind='retract' 초안(작업대 save_label 이 store.retract 로 보냄)."""
        if self._retract_cb is not None:
            out = self._retract_cb(rec["entity_id"], reason)
        else:
            out = self._save_cb({"kind": "retract", "entity_id": rec["entity_id"], "supersedes": rec.get("id"),
                                 "body": {"reason": reason}})
        if not isinstance(out, dict) or not isinstance(out.get("id"), str):
            raise RuntimeError("retract 가 레코드를 돌려주지 않았습니다")
        self._written.append(out)
        return out

    def _save_video_checks(self, rrec: dict) -> list[dict]:
        if self.review is None:
            return []
        w = self.review.window()
        out = []
        has_first = any((v.get("body") or v).get("check") == "first" for v in self._prior_checks)
        final = self._judgment()
        for s in self.review.pass_summaries(w):
            p = s["pass"]
            if p in self._saved_passes:
                continue
            check = "recheck" if (has_first or out or self._saved_passes) else "first"
            j = self._pass_judgments.get(p, final)
            body = {"target_kind": "reaction", "target_entity_id": rrec["entity_id"],
                    "target_version_id": rrec.get("id"), "check": check,
                    "window_start_ms": s["window_start_ms"], "window_end_ms": s["window_end_ms"],
                    "watched_ranges": s["watched_ranges"], "coverage_ratio": s["coverage_ratio"],
                    "audio_heard": bool(s["audio_heard"]), "max_speed": s["max_speed"],
                    "judgment": {"result": j.get("result"), "grade": j.get("grade"),
                                 "confidence": j.get("confidence")}}
            rec = self._save_cb({"kind": "video_check", "body": body})
            self._saved_passes.add(p)
            if isinstance(rec, dict):
                self._written.append(rec)
            out.append(rec)
        return out

    def _write_warning(self, code: str, entity_id: str | None, detail: str) -> None:
        if self._ops_cb is None:
            return
        if code not in OPS_WARNING_CODES:
            detail = f"{code}: {detail}"
            code = "other"
        try:
            self._ops_cb("warning", {"code": code, "action": "shown", "entity_id": entity_id,
                                     "detail": detail[:300]})
        except Exception as e:  # noqa: BLE001 — 경고 기록 실패는 라벨 저장을 되돌리지 않는다
            LOG.warning("경고 기록 실패: %s", type(e).__name__)

    def cancel(self) -> None:
        """Esc: 저장하지 않고 닫는다. 이미 저장된 줄(중간 실패 뒤)은 되돌리지 않는다.

        중간 실패로 이미 쓴 줄이 있으면 partialSaved 로 알린다(작업대가 큐 상태·Ctrl+Z 장부를 맞춘다).
        """
        if not self._open:
            return
        self.close_editor()
        if self._written:
            rx = next((r for r in reversed(self._written) if r.get("kind") == "reaction"), None)
            self.partialSaved.emit({"reaction": rx, "written": list(self._written)})
            self._written = []
        self.cancelled.emit()
        if self._cancel_cb is not None:
            self._cancel_cb()


def _find_data(box: QComboBox, value) -> int:
    """QComboBox.findData 는 None·False 를 잘 못 찾는다: 직접 비교한다(값과 타입이 같아야 함)."""
    for i in range(box.count()):
        d = box.itemData(i)
        if d is value or (type(d) is type(value) and d == value):
            return i
    return 0


def _hhmmss(ms: int) -> str:
    """VOD ms → 'HH:MM:SS' (빠른 입력 근거의 시각. parse_timestamps 가 읽는 모양)."""
    s = max(0, int(ms)) // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


_QUICK_TEXT_RE = re.compile(r"^(?:%s)(?: \d{2}:\d{2}:\d{2})?$" % "|".join(re.escape(p.label) for p in QUICK_PRESETS))


def _is_quick_text(text: str) -> bool:
    """빠른 입력이 넣은 글('단추 이름' 또는 '단추 이름 HH:MM:SS')인가. 그런 글은 다른 단추를 누르면 바꿔 쓴다.
    라벨러가 직접 쓴 글은 건드리지 않는다."""
    return bool(_QUICK_TEXT_RE.match((text or "").strip()))


def _err_text(e: Exception) -> str:
    probs = getattr(e, "problems", None)
    if isinstance(probs, list) and probs:
        return "; ".join(str(p) for p in probs[:3])
    return str(e) or type(e).__name__


__all__ = ["ReactionEditor", "Candidate", "Validation", "find_segment", "utterance_body", "normalize_coverage",
           "review_window_in_media", "GRADES", "GRADE_LABELS", "UNCONFIRMED", "CONFIDENCES", "CONTINUATION_MISSING",
           "QUICK_PRESETS", "QUICK_KEYS", "QUICK_END_MS", "QuickPreset"]
