"""세션 격리와 블라인드 정책 (docs/design/03-data-model.md 12-1, 02 3-3, 05 S-11, 08 D-08).

이 파일의 표가 '누가 무엇을 열고 보는가' 의 유일한 출처다. store 는 files_to_open 만 보고 파일을 열고,
UI 는 can_show / show_policy 만 묻는다. '열지 않음' 은 화면에서 숨기는 것이 아니라 store 가 그 파일을
디스크에서 읽지 않는다는 뜻이다.

기본은 막힘이다. 표에 없는 세션 종류·자료·항목은 모두 NONE / NEVER 로 답한다.
라벨링 세션(primary·relabel_same·relabel_second)에서는 엔진·AI 결과와 다른 세션 라벨이 어떤 단계에서도 열리지 않는다.

세션 종류 'analysis' (분석 세션, 새 값 후보)
- 08 D-01 '방송 분석 모드'(사람 판정과 엔진 판정을 나란히 봄)와 D-08 의 C단계 화면을 위해 둔다. 03 5-9 에는 아직 없다.
- 엔진 결과·사람 라벨(다른 세션)을 읽는다. 대신 **라벨을 하나도 만들 수 없다**(writable_label_kinds 가 비어 있음).
  자기 세션 파일에는 session.open·run.start·view.exposed 같은 운영 기록만 쓴다.
- 봉인된 평가용 AI 결과(<앱>\\data\\ai\\, 06 7-1)는 열지 않는다. 봉인 규칙은 평가 절차의 것이라 분석 화면이 깨면 안 된다.
- 세션 ID 코드(P·RS·R2·AD·OR)와 label-event 스키마의 sessionKind 열거에는 아직 없다. 넣을 때 코드 'AN' 을 제안한다(SESSION_CODES).

core 층: 파일·시계·Qt 를 쓰지 않는다. 순수 표와 함수뿐이다.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Mapping

# ----------------------------------------------------------------------------- 세션 종류 (03 5-9 + 후보)
PRIMARY = "primary"
RELABEL_SAME = "relabel_same"
RELABEL_SECOND = "relabel_second"
ADJUDICATION = "adjudication"
ORIGIN_REVIEW = "origin_review"
ANALYSIS = "analysis"            # 새 값 후보 (D-08). 03 5-9·스키마에 아직 없음

LABELING_SESSIONS = frozenset({PRIMARY, RELABEL_SAME, RELABEL_SECOND})
SESSION_KINDS = (PRIMARY, RELABEL_SAME, RELABEL_SECOND, ADJUDICATION, ORIGIN_REVIEW, ANALYSIS)
SESSION_CODES = MappingProxyType({
    PRIMARY: "P", RELABEL_SAME: "RS", RELABEL_SECOND: "R2", ADJUDICATION: "AD", ORIGIN_REVIEW: "OR",
    ANALYSIS: "AN",   # 제안. session_id 정규식(ids·스키마)에 아직 없다
})

# ----------------------------------------------------------------------------- 단계
# phase.mark 의 값(03 6-4) + 02 6-2 상태. 표에서 단계에 따라 달라지는 것은 조정 세션의 AI 결과 하나뿐이다.
PREP = "prep"
SEGMENTS = "segments"
REACTIONS = "reactions"
SAMPLES = "samples"
MISSED = "missed"
ORIGIN = "origin"
LOCKED = "locked"
COMPARISON = "comparison"        # 계획된 세션 모두 잠김 + 표본 재현 검증 통과 → AI 봉인 해제 (02 6-2, 06 7-1)
PHASES = (PREP, SEGMENTS, REACTIONS, SAMPLES, MISSED, ORIGIN, LOCKED, COMPARISON)

# ----------------------------------------------------------------------------- 파일 접근 값
NONE = "none"                     # 열지 않음 (디스크에서 읽지 않는다)
HOLD = "hold"                     # 잡기 (locks/open.lock)
READ = "read"
RW = "rw"                         # 읽기·쓰기
READ_DRAW = "read_draw"           # samples/: 읽기·추출
LOCKED_ONLY = "locked_only"       # 다른 세션 labels/: 잠긴 세션만 읽기
OWN = "own"                       # relabel/: 자기 계획만
TOGGLE = "toggle"                 # 기본 숨김. 켜면 view.exposed 를 남기고 그때 연다
READ_CAL_MEDIA = "read_cal_media" # project.json: 읽기(보정·영상만 쓰기)
SEAL_ONLY = "seal_only"           # <앱>\data\ai: 봉인(받기·해시 기록)만, 내용은 열지 않음
OPS_ONLY = "ops_only"             # 자기 세션 labels/: 운영 기록만 쓰기(라벨 없음), 읽기 가능

ACCESS_VALUES = frozenset({NONE, HOLD, READ, RW, READ_DRAW, LOCKED_ONLY, OWN, TOGGLE,
                           READ_CAL_MEDIA, SEAL_ONLY, OPS_ONLY})
_READABLE = frozenset({READ, RW, READ_DRAW, LOCKED_ONLY, OWN, READ_CAL_MEDIA, OPS_ONLY})
_WRITABLE = frozenset({RW, READ_DRAW, READ_CAL_MEDIA, OPS_ONLY})

# 라벨 종류 (label-event.schema.json)
_LABEL_WORK = frozenset({"segment", "utterance", "reaction", "continuation", "video_check",
                         "missed_candidate", "retract"})


@dataclass(frozen=True)
class FileScope:
    """세션 하나가 프로젝트에서 열 수 있는 것 (03 12-1 의 열 하나)."""
    open_lock: str = NONE          # locks/open.lock
    own_labels: str = NONE         # 자기 세션 labels/<session_id>.*.jsonl
    other_labels: str = NONE       # 다른 세션 labels/
    project_json: str = NONE       # project.json
    messages: str = NONE           # chat/messages.jsonl
    samples: str = NONE            # samples/
    relabel_plans: str = NONE      # relabel/<plan>
    identities: str = NONE         # chat/protected/identities.jsonl (원 닉네임)
    raw_pages: str = NONE          # chat/protected/pages-*.jsonl.gz (원문 페이지, 원 닉네임 포함)
    derived_asr: str = NONE        # derived/asr* (자동 인식 문장)
    engine_results: str = NONE     # 엔진 판정 결과(engine link 출력 등, 봉인 전 개발용). 분석 세션만
    origin: str = NONE             # origin/, 출처 라벨
    ai_sealed: str = NONE          # <앱>\data\ai\<project_id>\ (평가용 봉인 AI 결과)
    reports: str = NONE            # <앱>\data\reports\
    writable_label_kinds: frozenset = frozenset()   # 이 세션이 만들 수 있는 라벨 종류

    def access(self, name: str) -> str:
        """자료 이름 → 접근 값. 모르는 이름은 NONE."""
        if name == "writable_label_kinds" or name not in _FILE_FIELDS:
            return NONE
        return getattr(self, name)

    def can_read(self, name: str) -> bool:
        """기본 상태에서 읽어도 되는가. TOGGLE 은 False(켤 때 store 가 view.exposed 를 쓰고 연다)."""
        return self.access(name) in _READABLE

    def can_write(self, name: str) -> bool:
        return self.access(name) in _WRITABLE

    def can_toggle(self, name: str) -> bool:
        return self.access(name) == TOGGLE

    def may_read_other_session(self, locked: bool) -> bool:
        """다른 세션 기록 파일 하나를 열어도 되는가 (잠금 여부를 호출하는 쪽이 준다)."""
        a = self.other_labels
        return a == READ or (a == LOCKED_ONLY and bool(locked))

    def can_write_label(self, kind: str) -> bool:
        return kind in self.writable_label_kinds


_FILE_FIELDS = tuple(f.name for f in fields(FileScope) if f.name != "writable_label_kinds")
BLOCKED = FileScope()   # 모르는 세션 종류: 아무것도 열지 않는다


def _scope(**kw) -> FileScope:
    return FileScope(**kw)


# 03 12-1 표 그대로. 열 = 세션 종류.
_LABELING_COMMON = dict(
    open_lock=HOLD, own_labels=RW, other_labels=NONE, messages=READ,
    identities=TOGGLE, raw_pages=TOGGLE, derived_asr=TOGGLE,
    engine_results=NONE, origin=NONE, ai_sealed=NONE, reports=NONE,
    writable_label_kinds=_LABEL_WORK,
)
_TABLE: Mapping[str, FileScope] = MappingProxyType({
    PRIMARY: _scope(**_LABELING_COMMON, project_json=READ_CAL_MEDIA, samples=READ_DRAW, relabel_plans=NONE),
    RELABEL_SAME: _scope(**_LABELING_COMMON, project_json=READ, samples=NONE, relabel_plans=OWN),
    RELABEL_SECOND: _scope(**_LABELING_COMMON, project_json=READ, samples=NONE, relabel_plans=OWN),
    ADJUDICATION: _scope(
        open_lock=HOLD, own_labels=RW, other_labels=LOCKED_ONLY, project_json=READ, messages=READ,
        samples=READ, relabel_plans=READ, identities=TOGGLE, raw_pages=TOGGLE, derived_asr=READ,
        engine_results=NONE, origin=READ, ai_sealed=SEAL_ONLY, reports=RW,
        writable_label_kinds=frozenset({"adjudication", "retract"}),
    ),
    ORIGIN_REVIEW: _scope(
        open_lock=HOLD, own_labels=RW, other_labels=NONE, project_json=READ, messages=READ,
        samples=NONE, relabel_plans=NONE, identities=NONE, raw_pages=NONE, derived_asr=NONE,
        engine_results=NONE, origin=RW, ai_sealed=NONE, reports=NONE,
        writable_label_kinds=frozenset({"origin", "retract"}),
    ),
    ANALYSIS: _scope(
        open_lock=HOLD, own_labels=OPS_ONLY, other_labels=READ, project_json=READ, messages=READ,
        samples=READ, relabel_plans=READ, identities=TOGGLE, raw_pages=TOGGLE, derived_asr=READ,
        engine_results=READ, origin=READ, ai_sealed=NONE, reports=READ,
        writable_label_kinds=frozenset(),
    ),
})


def files_to_open(session_kind: str, phase: str | None = None) -> FileScope:
    """세션 종류(와 단계) → 열 수 있는 파일 (02 3-3 계약). 모르는 세션 종류는 BLOCKED.

    단계에 따라 바뀌는 것: 조정 세션의 봉인 AI 결과는 COMPARISON(봉인 해제 뒤)에서만 READ, 그 전엔 SEAL_ONLY.
    """
    base = _TABLE.get(session_kind) if isinstance(session_kind, str) else None
    if base is None:
        return BLOCKED
    if session_kind == ADJUDICATION and phase == COMPARISON:
        return _replace(base, ai_sealed=READ)
    return base


def _replace(s: FileScope, **kw) -> FileScope:
    d = {f.name: getattr(s, f.name) for f in fields(FileScope)}
    d.update(kw)
    return FileScope(**d)


# ----------------------------------------------------------------------------- 화면 항목 (can_show)
# view.exposed 의 what 값(03 6-4)과 같은 이름 + 분석 화면의 엔진 결과.
NICKNAME_RAW = "nickname_raw"
ASR_TEXT = "asr_text"
OTHER_SESSION_LABELS = "other_session_labels"
AI_RESULTS = "ai_results"              # 봉인된 평가용 AI 결과
ENGINE_RESULTS = "engine_results"      # 엔진 판정(분석 모드)
ORIGIN_INFO = "origin_info"
RAW_CHAT_PAGE = "raw_chat_page"
ITEMS = (NICKNAME_RAW, ASR_TEXT, OTHER_SESSION_LABELS, AI_RESULTS, ENGINE_RESULTS, ORIGIN_INFO, RAW_CHAT_PAGE)

# 보이기 정책
ALWAYS = "always"     # 그냥 보인다
ON_TOGGLE = "toggle"  # 기본 숨김. 사용자가 켜면 보이고 view.exposed 를 남긴다
NEVER = "never"       # 보이지 않는다(켤 수도 없다)

# 항목 → 그 항목이 기대는 자료(FileScope 필드)
_ITEM_SOURCE = MappingProxyType({
    NICKNAME_RAW: "identities",
    ASR_TEXT: "derived_asr",
    OTHER_SESSION_LABELS: "other_labels",
    AI_RESULTS: "ai_sealed",
    ENGINE_RESULTS: "engine_results",
    ORIGIN_INFO: "origin",
    RAW_CHAT_PAGE: "raw_pages",
})


def show_policy(item: str, phase: str | None, session_kind: str) -> str:
    """항목 하나의 보이기 정책. 파일 표에서 끌어내므로 두 표가 어긋나지 않는다. 모르면 NEVER."""
    src = _ITEM_SOURCE.get(item) if isinstance(item, str) else None
    if src is None:
        return NEVER
    a = files_to_open(session_kind, phase).access(src)
    if a == TOGGLE:
        return ON_TOGGLE
    if src == "origin" and a == RW:
        return ALWAYS
    if a in (READ, LOCKED_ONLY):
        return ALWAYS
    return NEVER   # NONE · SEAL_ONLY 등


def can_show(item: str, phase: str | None, session_kind: str) -> bool:
    """기본 상태에서 화면에 보여도 되는가 (02 3-3 계약). 토글 항목은 False."""
    return show_policy(item, phase, session_kind) == ALWAYS


def can_reveal(item: str, phase: str | None, session_kind: str) -> bool:
    """사용자가 켜면 보일 수 있는가(ALWAYS 또는 토글). 토글로 켤 때는 view.exposed 를 남겨야 한다."""
    return show_policy(item, phase, session_kind) in (ALWAYS, ON_TOGGLE)


def is_labeling_session(session_kind: str) -> bool:
    return session_kind in LABELING_SESSIONS


__all__ = [
    "FileScope", "BLOCKED", "files_to_open", "show_policy", "can_show", "can_reveal", "is_labeling_session",
    "SESSION_KINDS", "SESSION_CODES", "LABELING_SESSIONS", "PHASES", "ITEMS",
    "PRIMARY", "RELABEL_SAME", "RELABEL_SECOND", "ADJUDICATION", "ORIGIN_REVIEW", "ANALYSIS",
    "NONE", "HOLD", "READ", "RW", "READ_DRAW", "LOCKED_ONLY", "OWN", "TOGGLE", "READ_CAL_MEDIA",
    "SEAL_ONLY", "OPS_ONLY", "ALWAYS", "ON_TOGGLE", "NEVER",
]
