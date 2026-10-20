"""등급 조건 충족 계산 (안내서 v0.1 4-1·4-2, 03 6-7, 02 3-3 계약).

순수 함수다. Qt·파일·네트워크·시계·난수를 쓰지 않는다(02 3-1).

grade_conditions(candidates, cfg) 는 채팅 하나의 연결 후보 발화들과 그 증거로 '규칙이 허락하는 등급' 을 센다.
등급을 골라 주지 않는다(01 3-1). 라벨링 앱은 eligible_grades 로 grade_vs_conditions 를 계산하고,
엔진(engine/link.py)은 per_candidate·tied·similar_blocked 를 보고 자기 등급을 고른다.

해석(안내서에 없는 부분 — 측정 문서 docs/measurements/2026-10-20-link-v0.md 3장에 적음)
1. 시간 근접만으로 등급을 올리지 않는다: within_time_window 는 같은 후보에 다른 증거가 하나라도 있을 때만 센다.
   다른 증거가 없는 후보는 등급을 만들지 않는다(tier=None, reason time_only).
2. '연결 가능성 높음' 은 같은 시간대 비슷한 채팅이 없다고 확인됐을 때(no_similar_chat_nearby=True)만 된다.
   비슷한 채팅이 있으면(False) 막고 '불명확' 을 조건 충족 목록에 넣는다. 모르면(None) 막기만 한다.
3. 스트리머가 이미 그 주제를 말하던 중(streamer_already_on_topic=True)이면 '높음' 을 막는다(4-2 비고).
4. 강한 증거가 없는 후보 둘 이상이 '높음' 단계에서 같으면 '불명확'(후보 여럿, 4-2·예시 4)을 넣는다.
   '직접' 단계 동점은 불명확으로 보지 않는다(첫 반응을 읽은 순간으로 본다). reason multiple_direct 만 남긴다.
5. 증거 값이 None 이면 '판단 불가'(예: 닉네임 목록이 없음)라 세지 않고 reason evidence_unknown:<키> 를 남긴다.
6. 강도 'gate'(v0.1 미사용)는 증거로 세지 않고, 없으면 직접·높음을 막는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

EVIDENCE_KEYS = ("nickname_called", "text_repeated", "direct_reference", "answer_form",
                 "unique_content", "within_time_window", "same_topic")
STRENGTHS = ("strong", "medium", "weak", "gate")
GRADES = ("direct", "likely", "related_only", "unclear")      # 표시 순서(정렬 기준). 저장은 순서 없는 값(03 5-2)
TIME_KEY = "within_time_window"

V0_1_STRENGTH = {
    "nickname_called": "strong", "text_repeated": "strong", "direct_reference": "strong",
    "answer_form": "medium", "unique_content": "medium", "within_time_window": "medium",
    "same_topic": "weak",
}

# 엔진 점수 가중치(안내서 숫자 아님, 엔진 시작값). 후보 순위와 확신도(1·2위 점수 차)에만 쓴다.
SCORE_WEIGHT = {"strong": 3.0, "medium": 1.0, "weak": 0.3, "gate": 0.0}


@dataclass(frozen=True)
class RuleConfig:
    """등급 규칙에 쓰는 시작값. guide-config values 의 reaction·evidence_strength 에서 만든다(초 → ms)."""
    time_window_ms: int = 20000
    direct_min_strong: int = 1
    likely_min_medium: int = 2
    similar_chat_window_ms: int | None = None
    evidence_strength: Mapping[str, str] = field(default_factory=lambda: dict(V0_1_STRENGTH))

    def __post_init__(self):
        es = dict(self.evidence_strength)
        if set(es) != set(EVIDENCE_KEYS):
            raise ValueError("evidence_strength 의 키가 7개 증거와 다릅니다")
        for k, v in es.items():
            if v not in STRENGTHS:
                raise ValueError(f"evidence_strength.{k}: 모르는 강도 {v!r}")
        for name in ("time_window_ms", "direct_min_strong", "likely_min_medium"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ValueError(f"{name}: 1 이상의 정수여야 합니다")
        object.__setattr__(self, "evidence_strength", es)

    @classmethod
    def from_values(cls, values: Mapping[str, Any]) -> "RuleConfig":
        """guide-config 의 values(또는 project.json guides[].thresholds) 에서 만든다."""
        r = values["reaction"]
        sw = r.get("similar_chat_window_sec")
        return cls(
            time_window_ms=_sec_to_ms(r["time_window_sec"]),
            direct_min_strong=int(r["direct_min_strong"]),
            likely_min_medium=int(r["likely_min_medium"]),
            similar_chat_window_ms=None if sw is None else _sec_to_ms(sw),
            evidence_strength=dict(values["evidence_strength"]),
        )


def _sec_to_ms(v) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        raise ValueError("초 값은 숫자여야 합니다")
    return int(round(v * 1000))


V0_1 = RuleConfig()


@dataclass(frozen=True)
class CandidateEvidence:
    """후보 발화 하나. evidence 는 7개 키 → bool | None | {'present': bool|None, ...}. 빠진 키는 None(모름)."""
    evidence: Mapping[str, Any]
    utterance_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True)
class CandidateGrade:
    index: int
    strong: int
    medium: int
    weak: int
    unknown: tuple[str, ...]
    present: tuple[str, ...]        # 센 증거 키(시간 창만 있어 버린 경우는 빠짐)
    time_only: bool
    tier: str | None                # 'direct' | 'likely' | 'related_only' | None (막히기 전 단계가 아니라 막힌 뒤 단계)
    blocked: tuple[str, ...]        # likely 를 막은 사유
    score: float


@dataclass(frozen=True)
class GradeConditions:
    eligible_grades: tuple[str, ...]
    result: str                      # 'graded' | 'unconfirmed'
    reasons: tuple[str, ...]
    per_candidate: tuple[CandidateGrade, ...] = ()
    tied: bool = False               # 강한 증거 없는 '높음' 후보가 둘 이상
    similar_blocked: bool = False    # 비슷한 채팅 때문에 '높음' 이 막힘
    unclear_cause: str | None = None  # 'multiple_utterances' | 'multiple_similar_chats' | None

    def best(self) -> CandidateGrade | None:
        """점수가 가장 높은 후보(같으면 앞 순서). 후보가 없으면 None."""
        if not self.per_candidate:
            return None
        return max(self.per_candidate, key=lambda c: (_tier_rank(c.tier), c.score, -c.index))


def _tier_rank(t: str | None) -> int:
    return {"direct": 3, "likely": 2, "related_only": 1}.get(t, 0)


def _present(v) -> bool | None:
    if isinstance(v, Mapping):
        v = v.get("present")
    if v is None:
        return None
    if not isinstance(v, bool):
        raise TypeError("증거 값은 bool·None·{'present': bool|None} 이어야 합니다")
    return v


def _evidence_of(c) -> Mapping[str, Any]:
    if isinstance(c, CandidateEvidence):
        return c.evidence
    if isinstance(c, Mapping):
        ev = c.get("evidence", c)
        if not isinstance(ev, Mapping):
            raise TypeError("candidate.evidence 는 dict 여야 합니다")
        return ev
    raise TypeError("후보는 CandidateEvidence 또는 dict 여야 합니다")


def grade_candidate(index: int, evidence: Mapping[str, Any], cfg: RuleConfig, *,
                    no_similar_chat_nearby: bool | None, streamer_already_on_topic: bool | None) -> CandidateGrade:
    """후보 하나의 증거를 세어 단계를 정한다(grade_conditions 의 부품)."""
    bad = set(evidence) - set(EVIDENCE_KEYS)
    if bad:
        raise ValueError(f"모르는 증거 키: {sorted(bad)}")
    st = cfg.evidence_strength
    vals = {k: _present(evidence.get(k)) for k in EVIDENCE_KEYS}
    unknown = tuple(k for k in EVIDENCE_KEYS if vals[k] is None)
    # 시간 창 말고 다른 증거(gate 제외)가 하나라도 있는가
    others = [k for k in EVIDENCE_KEYS if k != TIME_KEY and vals[k] and st[k] != "gate"]
    time_only = bool(vals[TIME_KEY]) and not others
    counted = [k for k in EVIDENCE_KEYS if vals[k] and st[k] != "gate" and (k != TIME_KEY or others)]
    s = sum(1 for k in counted if st[k] == "strong")
    m = sum(1 for k in counted if st[k] == "medium")
    w = sum(1 for k in counted if st[k] == "weak")
    gate_missing = [k for k in EVIDENCE_KEYS if st[k] == "gate" and vals[k] is not True]
    score = sum(SCORE_WEIGHT[st[k]] for k in counted)

    blocked: list[str] = []
    tier: str | None
    if gate_missing and (s >= cfg.direct_min_strong or m >= cfg.likely_min_medium):
        blocked.extend(f"gate:{k}" for k in gate_missing)
        tier = "related_only"
    elif s >= cfg.direct_min_strong:
        tier = "direct"
    elif m >= cfg.likely_min_medium:
        if no_similar_chat_nearby is False:
            blocked.append("similar_chat")
        elif no_similar_chat_nearby is None:
            blocked.append("similar_chat_unknown")
        if streamer_already_on_topic:
            blocked.append("already_on_topic")
        tier = "related_only" if blocked else "likely"
    elif s > 0 or m > 0 or w > 0:
        tier = "related_only"
    else:
        tier = None
    return CandidateGrade(index=index, strong=s, medium=m, weak=w, unknown=unknown, present=tuple(counted),
                          time_only=time_only, tier=tier, blocked=tuple(blocked), score=round(score, 4))


def grade_conditions(candidates: Sequence[Any], cfg: RuleConfig = V0_1, *,
                     no_similar_chat_nearby: bool | None = None,
                     streamer_already_on_topic: bool | None = None) -> GradeConditions:
    """채팅 하나의 등급 조건 충족 상태.

    candidates: CandidateEvidence 또는 {'evidence': {...}} dict(반응 라벨 candidates[] 모양)의 목록. 비면 '반응 미확인'.
    no_similar_chat_nearby: 같은 시간대 비슷한 다른 채팅이 없음(True) / 있음(False) / 모름(None).
    streamer_already_on_topic: 스트리머가 이미 그 주제를 말하던 중.
    돌려주는 eligible_grades 는 후보별 결과의 합집합이다(03 6-7). 등급을 고르지 않는다.
    """
    if not isinstance(cfg, RuleConfig):
        cfg = RuleConfig.from_values(cfg)
    if not candidates:
        return GradeConditions(eligible_grades=(), result="unconfirmed", reasons=("no_candidates",))

    per = tuple(grade_candidate(i, _evidence_of(c), cfg, no_similar_chat_nearby=no_similar_chat_nearby,
                                streamer_already_on_topic=streamer_already_on_topic)
                for i, c in enumerate(candidates))
    reasons: list[str] = []
    elig: set[str] = set()
    for c in per:
        if c.tier:
            elig.add(c.tier)
        if c.time_only:
            reasons.append(f"time_only:{c.index}")
        for b in c.blocked:
            reasons.append(f"likely_blocked:{b}:{c.index}")
        for k in c.unknown:
            reasons.append(f"evidence_unknown:{k}:{c.index}")

    similar_blocked = any("similar_chat" in c.blocked for c in per)
    n_likely = sum(1 for c in per if c.tier == "likely")
    n_direct = sum(1 for c in per if c.tier == "direct")
    tied = n_likely >= 2 and n_direct == 0
    cause = None
    if tied:
        elig.add("unclear")
        reasons.append("tie:likely")
        cause = "multiple_utterances"
    if no_similar_chat_nearby is False and any(c.tier for c in per):
        elig.add("unclear")
        reasons.append("similar_chat_nearby")
        cause = cause or "multiple_similar_chats"
    if n_direct >= 2:
        reasons.append("multiple_direct")
    if not elig:
        reasons.append("no_countable_evidence")
    return GradeConditions(eligible_grades=tuple(g for g in GRADES if g in elig), result="graded",
                           reasons=tuple(reasons), per_candidate=per, tied=tied,
                           similar_blocked=similar_blocked, unclear_cause=cause)


def grade_vs_conditions(grade: str | None, gc: GradeConditions) -> str:
    """고른 등급과 조건 충족 상태의 관계(03 5-10). store 가 저장 때 부른다."""
    if gc.result == "unconfirmed" or grade is None:
        return "not_applicable"
    return "consistent" if grade in gc.eligible_grades else "deviates"


def direct_allowed(linked: CandidateGrade | None) -> bool:
    """연결한 후보의 강한 증거가 0개면 직접 반응을 저장하지 않는다(03 6-7 저장 규칙)."""
    return linked is not None and linked.strong > 0


def within_time_window(chat_ms: int, utterance_start_ms: int, cfg: RuleConfig = V0_1) -> bool:
    """채팅이 뜬 뒤 시간 창 안에 발화가 시작됐는가([0, time_window_ms])."""
    for v in (chat_ms, utterance_start_ms):
        if isinstance(v, bool) or not isinstance(v, int):
            raise TypeError("시각은 정수 ms 여야 합니다")
    d = utterance_start_ms - chat_ms
    return 0 <= d <= cfg.time_window_ms
