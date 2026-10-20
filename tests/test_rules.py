"""app/core/rules.py — 등급 조건 충족 계산 (안내서 v0.1 4-1·4-2, 예시 4-4 골든)."""
import ast
import os

import pytest

from app.core import rules
from app.core.rules import (EVIDENCE_KEYS, V0_1, CandidateEvidence, RuleConfig, grade_conditions,
                            grade_vs_conditions)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ev(*present, unknown=()):
    d = {k: False for k in EVIDENCE_KEYS}
    for k in present:
        d[k] = True
    for k in unknown:
        d[k] = None
    return {"evidence": d}


V0_1_VALUES = {
    "reaction": {"time_window_sec": 20, "direct_min_strong": 1, "likely_min_medium": 2,
                 "similar_chat_window_sec": None},
    "evidence_strength": dict(rules.V0_1_STRENGTH),
}


# ── 안내서 4-4 예시 4건 (골든) ───────────────────────────────────────────

def test_golden_1_direct_nickname_and_repeat():
    # "○○님이 여행 가면 뭐 하냐고 물어보셨는데…" → 닉네임 호명 + 질문 되풀이(+ 직접 지칭, 시간 창)
    gc = grade_conditions([ev("nickname_called", "text_repeated", "direct_reference", "within_time_window",
                              "same_topic")], no_similar_chat_nearby=True)
    assert gc.result == "graded"
    assert gc.eligible_grades == ("direct",)
    assert gc.best().tier == "direct" and gc.best().strong == 3


def test_golden_2_likely_two_medium_no_similar():
    # 8초 뒤 "여행 가면 저는 무조건 맛집부터…" (호명·되풀이 없음, 다른 여행 채팅 없음) → 중간 2개 이상
    gc = grade_conditions([ev("answer_form", "within_time_window", "same_topic")], no_similar_chat_nearby=True,
                          streamer_already_on_topic=False)
    assert gc.eligible_grades == ("likely",)
    assert not gc.tied and not gc.similar_blocked


def test_golden_3_already_on_topic_related_only():
    # 이미 여행 이야기를 하던 중 → 관련성만 있음
    gc = grade_conditions([ev("answer_form", "within_time_window", "same_topic")], no_similar_chat_nearby=True,
                          streamer_already_on_topic=True)
    assert gc.eligible_grades == ("related_only",)
    assert any("already_on_topic" in r for r in gc.reasons)


def test_golden_4_similar_chats_unclear():
    # 거의 같은 질문이 두 시청자에게서 동시에 → 두 채팅 모두 불명확
    for _ in range(2):
        gc = grade_conditions([ev("answer_form", "within_time_window", "same_topic")],
                              no_similar_chat_nearby=False)
        assert "likely" not in gc.eligible_grades
        assert "unclear" in gc.eligible_grades
        assert gc.similar_blocked and gc.unclear_cause == "multiple_similar_chats"


# ── 4-2 규칙 ─────────────────────────────────────────────────────────────

def test_no_candidates_unconfirmed():
    gc = grade_conditions([])
    assert gc.result == "unconfirmed" and gc.eligible_grades == () and gc.reasons == ("no_candidates",)
    assert grade_vs_conditions(None, gc) == "not_applicable"


def test_time_proximity_alone_does_not_grade():
    gc = grade_conditions([ev("within_time_window")], no_similar_chat_nearby=True)
    assert gc.result == "graded"            # 후보는 있다(계약: 비지 않으면 graded)
    assert gc.eligible_grades == ()
    c = gc.per_candidate[0]
    assert c.time_only and c.tier is None and c.medium == 0
    assert "time_only:0" in gc.reasons and "no_countable_evidence" in gc.reasons


def test_time_window_counts_only_with_other_evidence():
    # 중간 1개 + 시간 창 = 중간 2개 → 높음
    assert grade_conditions([ev("unique_content", "within_time_window")],
                            no_similar_chat_nearby=True).eligible_grades == ("likely",)
    # 약한 증거 + 시간 창 → 중간 1개 → 관련성만 (시간 창이 '높음' 으로 올리지 않음)
    gc = grade_conditions([ev("same_topic", "within_time_window")], no_similar_chat_nearby=True)
    assert gc.eligible_grades == ("related_only",)


def test_one_medium_related_only():
    assert grade_conditions([ev("answer_form")], no_similar_chat_nearby=True).eligible_grades == ("related_only",)


def test_weak_only_related_only():
    assert grade_conditions([ev("same_topic")], no_similar_chat_nearby=True).eligible_grades == ("related_only",)


def test_strong_without_time_window_is_direct():
    assert grade_conditions([ev("direct_reference")]).eligible_grades == ("direct",)


def test_direct_not_blocked_by_similar_chat_but_unclear_added():
    gc = grade_conditions([ev("text_repeated", "within_time_window")], no_similar_chat_nearby=False)
    assert gc.eligible_grades == ("direct", "unclear")
    assert not gc.similar_blocked


def test_similar_unknown_blocks_likely_without_unclear():
    gc = grade_conditions([ev("answer_form", "unique_content")], no_similar_chat_nearby=None)
    assert gc.eligible_grades == ("related_only",)
    assert "likely_blocked:similar_chat_unknown:0" in gc.reasons
    assert not gc.similar_blocked


def test_tie_two_likely_candidates_unclear():
    gc = grade_conditions([ev("answer_form", "within_time_window"), ev("unique_content", "within_time_window")],
                          no_similar_chat_nearby=True)
    assert gc.tied and gc.unclear_cause == "multiple_utterances"
    assert gc.eligible_grades == ("likely", "unclear")


def test_direct_plus_likely_not_tied():
    gc = grade_conditions([ev("nickname_called"), ev("answer_form", "unique_content")], no_similar_chat_nearby=True)
    assert not gc.tied
    assert gc.eligible_grades == ("direct", "likely")
    assert gc.best().index == 0


def test_two_direct_not_unclear():
    gc = grade_conditions([ev("nickname_called"), ev("nickname_called")], no_similar_chat_nearby=True)
    assert gc.eligible_grades == ("direct",) and "multiple_direct" in gc.reasons
    assert gc.best().index == 0             # 같은 점수면 앞 후보(먼저 읽은 순간)


def test_unknown_evidence_not_counted():
    gc = grade_conditions([ev("answer_form", unknown=("nickname_called",))], no_similar_chat_nearby=True)
    c = gc.per_candidate[0]
    assert c.unknown == ("nickname_called",) and c.strong == 0
    assert "evidence_unknown:nickname_called:0" in gc.reasons
    assert gc.eligible_grades == ("related_only",)


def test_missing_key_is_unknown():
    gc = grade_conditions([{"evidence": {"answer_form": True}}], no_similar_chat_nearby=True)
    assert len(gc.per_candidate[0].unknown) == 6


def test_evidence_item_dict_form_and_dataclass():
    d = {k: {"present": False, "at_ms": None} for k in EVIDENCE_KEYS}
    d["nickname_called"] = {"present": True, "at_ms": 1000, "note": "x"}
    assert grade_conditions([{"evidence": d}]).eligible_grades == ("direct",)
    assert grade_conditions([CandidateEvidence(evidence=d)]).eligible_grades == ("direct",)


def test_bad_inputs_rejected():
    with pytest.raises(ValueError):
        grade_conditions([{"evidence": {"bogus": True}}])
    with pytest.raises(TypeError):
        grade_conditions([{"evidence": {"answer_form": 1}}])
    with pytest.raises(TypeError):
        grade_conditions(["x"])


def test_grade_vs_conditions():
    gc = grade_conditions([ev("answer_form", "within_time_window")], no_similar_chat_nearby=True)
    assert grade_vs_conditions("likely", gc) == "consistent"
    assert grade_vs_conditions("direct", gc) == "deviates"


def test_direct_allowed():
    gc = grade_conditions([ev("answer_form", "unique_content")], no_similar_chat_nearby=True)
    assert not rules.direct_allowed(gc.best())
    assert rules.direct_allowed(grade_conditions([ev("nickname_called")]).best())
    assert not rules.direct_allowed(None)


# ── 시작값 ───────────────────────────────────────────────────────────────

def test_from_values_matches_default():
    cfg = RuleConfig.from_values(V0_1_VALUES)
    assert cfg == V0_1
    assert grade_conditions([ev("answer_form", "within_time_window")], V0_1_VALUES,
                            no_similar_chat_nearby=True).eligible_grades == ("likely",)


def test_thresholds_change_regrades():
    vals = {**V0_1_VALUES, "reaction": {**V0_1_VALUES["reaction"], "likely_min_medium": 3}}
    gc = grade_conditions([ev("answer_form", "within_time_window")], vals, no_similar_chat_nearby=True)
    assert gc.eligible_grades == ("related_only",)
    vals = {**V0_1_VALUES, "reaction": {**V0_1_VALUES["reaction"], "direct_min_strong": 2}}
    assert grade_conditions([ev("nickname_called")], vals).eligible_grades == ("related_only",)
    assert grade_conditions([ev("nickname_called", "text_repeated")], vals).eligible_grades == ("direct",)


def test_gate_strength_blocks_high_grades():
    st = dict(rules.V0_1_STRENGTH, within_time_window="gate")
    cfg = RuleConfig(evidence_strength=st)
    # 관문 통과(시간 창 안) → 중간 2개면 높음
    assert grade_conditions([ev("answer_form", "unique_content", "within_time_window")], cfg,
                            no_similar_chat_nearby=True).eligible_grades == ("likely",)
    # 관문 실패 → 직접·높음 막힘
    gc = grade_conditions([ev("nickname_called")], cfg)
    assert gc.eligible_grades == ("related_only",)
    assert any("gate:within_time_window" in r for r in gc.reasons)


def test_config_validation():
    with pytest.raises(ValueError):
        RuleConfig(evidence_strength={"nickname_called": "strong"})
    with pytest.raises(ValueError):
        RuleConfig(evidence_strength=dict(rules.V0_1_STRENGTH, same_topic="huge"))
    with pytest.raises(ValueError):
        RuleConfig(time_window_ms=0)
    with pytest.raises(ValueError):
        RuleConfig.from_values({**V0_1_VALUES, "reaction": {**V0_1_VALUES["reaction"], "time_window_sec": float("nan")}})


def test_within_time_window():
    assert rules.within_time_window(1000, 1000)
    assert rules.within_time_window(1000, 21000)
    assert not rules.within_time_window(1000, 21001)
    assert not rules.within_time_window(1000, 999)       # 채팅보다 먼저 시작한 발화
    with pytest.raises(TypeError):
        rules.within_time_window(1000.0, 2000)


def test_matches_guide_config_schema_keys():
    import json
    with open(os.path.join(ROOT, "schemas", "guide-config.schema.json"), encoding="utf-8") as f:
        s = json.load(f)
    es = s["$defs"]["values"]["properties"]["evidence_strength"]
    assert tuple(es["required"]) == EVIDENCE_KEYS
    with open(os.path.join(ROOT, "schemas", "label-event.schema.json"), encoding="utf-8") as f:
        le = json.load(f)
    assert tuple(le["$defs"]["evidence"]["required"]) == EVIDENCE_KEYS
    assert set(rules.STRENGTHS) == set(s["$defs"]["strength"]["enum"])


def test_layer_pure_imports():
    with open(os.path.join(ROOT, "app", "core", "rules.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert mods <= {"__future__", "dataclasses", "typing"}
