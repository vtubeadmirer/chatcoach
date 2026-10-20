"""C단계 둘째 묶음 검토 수정의 재현 시험 — 반응 편집기 · 구간 폼 · 등급 규칙 · 내보내기 (합성 데이터만, offscreen).

각 시험 이름 앞의 번호는 검토 보고의 확인 항목 순서다.
- 이어짐: 대상 반응이 철회됐거나 직접·높음이 아니면 내보내지 않고, 편집기는 낮춘 반응의 이어짐을 철회한다(안내서 4-3, 03 12-3).
- 내보내기 recheck_incomplete / audio_not_heard 는 '들었다' 고 인정된 회차로 센다(편집기·작업대와 같은 core 규칙).
- 구간 폼 저장은 철회 먼저 · 줄어드는 판 먼저(중간 실패에도 겹침이 생기지 않음).
- '연결 가능성 높음' 은 조건을 넘어 고를 수 없고, '불명확' 은 후보가 있으면 늘 고를 수 있다(01 3-1).
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import export, store  # noqa: E402
from app.core import rules  # noqa: E402
from tests.test_export import (MID_A, SID_A, _clear_locks, ctx, evidence, open_store, proj)  # noqa: E402,F401
from tests.test_reaction_editor import (Clock, FakeLock, _prepare_direct, cid, env, fill_candidate,  # noqa: E402,F401
                                        listen, qapp)
from tests import test_segment_form as tsf  # noqa: E402

MID_C = "cccccccc-0000-4000-8000-000000000003"
SID_C = "L03-P-20261021-ef56"


# ============================================================================ core.rules
def test_rules_unclear_is_always_selectable_with_candidates():
    gc = rules.grade_conditions([{"evidence": {"same_topic": {"present": True}}}],
                                no_similar_chat_nearby=True)
    assert "unclear" not in gc.eligible_grades                   # eligible 은 그대로(엔진이 같은 값을 쓴다)
    assert rules.grade_vs_conditions("unclear", gc) == "consistent"
    assert rules.grade_vs_conditions("likely", gc) == "deviates"
    assert rules.grade_vs_conditions("unclear", rules.grade_conditions([])) == "not_applicable"


def test_rules_likely_allowed_needs_likely_tier():
    weak = rules.grade_conditions([{"evidence": {"same_topic": {"present": True},
                                                 "within_time_window": {"present": True}}}],
                                  no_similar_chat_nearby=False)
    assert not rules.likely_allowed(weak.per_candidate[0])
    two_med = rules.grade_conditions([{"evidence": {"answer_form": {"present": True},
                                                    "unique_content": {"present": True}}}],
                                     no_similar_chat_nearby=True)
    assert rules.likely_allowed(two_med.per_candidate[0])
    blocked = rules.grade_conditions([{"evidence": {"answer_form": {"present": True},
                                                    "unique_content": {"present": True}}}],
                                     no_similar_chat_nearby=False)
    assert not rules.likely_allowed(blocked.per_candidate[0])   # 비슷한 채팅 있음 → 높음 막힘
    direct = rules.grade_conditions([{"evidence": {"nickname_called": {"present": True}}}])
    assert rules.likely_allowed(direct.per_candidate[0])        # 낮춰 고름은 허용(사유는 편집기가 받음)
    assert not rules.likely_allowed(None)


def test_rules_heard_count_uses_coverage():
    ok = {"audio_heard": True, "coverage_ratio": 0.95}
    low = {"audio_heard": True, "coverage_ratio": 0.057}
    no = {"audio_heard": False, "coverage_ratio": 1.0}
    assert rules.heard_count([ok, low, no]) == 1
    assert not rules.recheck_complete("direct", [ok, low])
    assert rules.recheck_complete("likely", [ok, dict(ok)])
    assert rules.recheck_complete("related_only", [])


# ============================================================================ 내보내기
def _rows(root):
    rows, info = export.build_rows(root)
    return rows, info


def test_1_export_drops_continuation_of_retracted_reaction(proj):
    st = open_store(proj["root"], ctx(MID_A, SID_A, "L01"), 500)
    st.retract(proj["rx"]["entity_id"], "undo")                # 작업대 Ctrl+Z 와 같은 호출
    st.close()
    rows, info = _rows(proj["root"])
    a = [r for r in rows if r["session_id"] == SID_A]
    assert not [r for r in a if r["label_kind"] == "reaction"]
    assert not [r for r in a if r["label_kind"] == "continuation"]
    assert any("이어짐 1건" in w for w in info["warnings"])


def test_5_export_drops_continuation_of_downgraded_reaction(proj):
    st = open_store(proj["root"], ctx(MID_A, SID_A, "L01"), 500)
    rx = proj["rx"]
    head = st.head(rx["entity_id"])
    body = dict(head["body"])
    body.update(grade="related_only", eligible_grades=["direct"], grade_vs_conditions="deviates",
                override_reason="합성 사유", linked_utterance_id=None, reaction_time_ms=None)
    st.append_label({"kind": "reaction", "entity_id": rx["entity_id"], "supersedes": head["id"], "body": body})
    st.close()
    rows, _info = _rows(proj["root"])
    a = [r for r in rows if r["session_id"] == SID_A]
    assert [r["grade"] for r in a if r["label_kind"] == "reaction"] == ["related_only"]
    assert not [r for r in a if r["label_kind"] == "continuation"]


def test_4_export_recheck_incomplete_counts_heard_passes(proj):
    st = open_store(proj["root"], ctx(MID_C, SID_C, "L03"), 3000)
    seg = st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 600000, "category": "just_chatting",
                                                       "confidence": "certain", "rationale": "00:00 합성"}})
    utt = st.append_label({"kind": "utterance", "body": {"segment_id": seg["entity_id"], "start_ms": 61000,
                                                         "end_ms": 63000, "heard_text": "합성",
                                                         "heard_text_source": "typed", "speaker": "streamer",
                                                         "modality": "verbal"}})
    rx = st.append_label({"kind": "reaction", "body": {
        "chat_id": "c" + "1" * 20, "segment_id": seg["entity_id"], "entry": "from_utterance", "result": "graded",
        "grade": "direct", "eligible_grades": ["direct"], "grade_vs_conditions": "consistent",
        "candidates": [{"utterance_id": utt["entity_id"], "evidence": evidence(nickname_called=True)}],
        "linked_utterance_id": utt["entity_id"], "first_visible_ms": 60000, "first_visible_source": "chat_clock",
        "reaction_time_ms": 61500, "confidence": "certain", "rationale": "01:01 합성 호명"}})
    for chk, heard, cov in (("first", True, 1.0), ("recheck", False, 0.057)):
        st.append_label({"kind": "video_check", "body": {
            "target_kind": "reaction", "target_entity_id": rx["entity_id"], "check": chk,
            "window_start_ms": 50000, "window_end_ms": 93000, "watched_ranges": [[50000, 52000]],
            "coverage_ratio": cov, "audio_heard": heard}})
    st.close()
    rows, _ = _rows(proj["root"])
    r = next(x for x in rows if x["session_id"] == SID_C and x["label_kind"] == "reaction")
    assert "recheck_incomplete" in r["eval"]["excluded_reasons"] and r["eval"]["strict"] is False
    # 비교: 두 회차를 다 들은 세션 A 의 직접 반응은 recheck_incomplete 가 아니다
    a = next(x for x in rows if x["session_id"] == SID_A and x["label_kind"] == "reaction")
    assert "recheck_incomplete" not in a["eval"]["excluded_reasons"]


def test_4b_export_audio_not_heard_when_no_pass_counts(proj):
    st = open_store(proj["root"], ctx(MID_C, SID_C, "L03"), 3000)
    seg = st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 600000, "category": "just_chatting",
                                                       "confidence": "certain", "rationale": "00:00 합성"}})
    utt = st.append_label({"kind": "utterance", "body": {"segment_id": seg["entity_id"], "start_ms": 61000,
                                                         "end_ms": 63000, "heard_text": "합성",
                                                         "heard_text_source": "typed", "speaker": "streamer",
                                                         "modality": "verbal"}})
    rx = st.append_label({"kind": "reaction", "body": {
        "chat_id": "c" + "1" * 20, "segment_id": seg["entity_id"], "entry": "from_utterance", "result": "graded",
        "grade": "related_only", "eligible_grades": ["related_only"], "grade_vs_conditions": "consistent",
        "candidates": [{"utterance_id": utt["entity_id"], "evidence": evidence(same_topic=True)}],
        "linked_utterance_id": None, "first_visible_ms": 60000, "first_visible_source": "chat_clock",
        "reaction_time_ms": 61500, "confidence": "certain", "rationale": "01:01 합성"}})
    st.append_label({"kind": "video_check", "body": {
        "target_kind": "reaction", "target_entity_id": rx["entity_id"], "check": "first",
        "window_start_ms": 50000, "window_end_ms": 93000, "watched_ranges": [[50000, 52000]],
        "coverage_ratio": 0.05, "audio_heard": True}})          # audio_heard 라도 덮은 비율이 모자람
    st.close()
    rows, _ = _rows(proj["root"])
    r = next(x for x in rows if x["session_id"] == SID_C and x["label_kind"] == "reaction")
    assert "audio_not_heard" in r["eval"]["excluded_reasons"]


def test_low_export_marks_reaction_in_boundary_buffer(proj):
    """전환 앞뒤 10초 안 채팅의 반응은 eval.in_boundary_buffer=True 이고 정답에서 빠진다(안내서 3장, 06 6-6)."""
    st = open_store(proj["root"], ctx(MID_C, SID_C, "L03"), 3000)
    seg = st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 65000, "category": "just_chatting",
                                                       "confidence": "certain", "rationale": "00:00 합성"}})
    st.append_label({"kind": "segment", "body": {"start_ms": 65000, "end_ms": 600000, "category": "game",
                                                 "confidence": "certain", "rationale": "01:05 합성"}})
    st.append_label({"kind": "reaction", "body": {
        "chat_id": "c" + "1" * 20, "segment_id": seg["entity_id"], "entry": "from_utterance",
        "result": "unconfirmed", "grade": None, "eligible_grades": [], "grade_vs_conditions": "not_applicable",
        "candidates": [], "linked_utterance_id": None, "first_visible_ms": 60000,
        "first_visible_source": "chat_clock", "reaction_time_ms": None, "confidence": "certain",
        "rationale": "01:00 합성"}})
    st.close()
    rows, _ = _rows(proj["root"])
    r = next(x for x in rows if x["session_id"] == SID_C and x["label_kind"] == "reaction")
    assert r["eval"]["in_boundary_buffer"] is True                # 60초 채팅, 경계 65초 ±10초
    assert r["eval"]["excluded_reasons"][0] == "in_boundary_buffer" and r["eval"]["strict"] is False
    a = next(x for x in rows if x["session_id"] == SID_A and x["label_kind"] == "reaction")
    assert a["eval"]["in_boundary_buffer"] is False               # 세션 A 의 구간표에는 그 경계가 없다


# ============================================================================ 반응 편집기
def _use_store_retract(env):
    env.ed._retract_cb = lambda eid, reason: env.store.retract(eid, reason)


def _fill_continuation(ed):
    c = ed.continuation
    c.set_type("short")
    c.start_edit.setText("112.0")
    c.end_edit.setText("140.0")
    c._on_span_edited()
    c.conf_box.setCurrentIndex(c.conf_box.findData("certain"))
    c.rationale.setText("00:01:52 합성 이어짐")
    c._on_change()


def test_6_downgrade_existing_reaction_retracts_its_continuation(env):
    _use_store_retract(env)
    ed = env.ed
    _prepare_direct(ed)
    listen(env, 2)
    _fill_continuation(ed)
    res = ed.save()
    assert res is not None and res["continuation"] is not None, ed.msg.text()
    utts = {u["entity_id"]: u for u in res["utterances"]}
    ed.open_existing(res["reaction"], utterances=utts, continuation=res["continuation"],
                     video_checks=res["video_checks"])
    ed.set_grade("related_only")
    ed.override.setText("합성 사유: 다른 채팅을 향함")
    res2 = ed.save()
    assert res2 is not None, ed.msg.text()
    assert res2["reaction"]["body"]["grade"] == "related_only"
    assert res2["continuation_retracted"]["kind"] == "retract"
    eff = env.store.effective()
    assert list(eff.items("continuation")) == []
    assert any(r["kind"] == "retract" for r in res2["written"])


def test_6b_downgrade_without_retract_callback_sends_retract_draft(env):
    """retract 콜백이 없으면 save 에 kind='retract' 초안을 보낸다(작업대 save_label 이 store.retract 로 가른다)."""
    ed = env.ed
    orig = ed._save_cb

    def save(draft):
        if draft["kind"] == "retract":
            return env.store.retract(draft["entity_id"], draft["body"]["reason"])
        return orig(draft)
    ed._save_cb = save
    _prepare_direct(ed)
    listen(env, 2)
    _fill_continuation(ed)
    res = ed.save()
    ed.open_existing(res["reaction"], utterances={u["entity_id"]: u for u in res["utterances"]},
                     continuation=res["continuation"], video_checks=res["video_checks"])
    ed.set_grade("unconfirmed")
    ed.remove_candidate()
    res2 = ed.save()
    assert res2 is not None, ed.msg.text()
    assert list(env.store.effective().items("continuation")) == []


def test_8_likely_above_conditions_is_blocked(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    fill_candidate(ed, 110_000, 113_000, "합성 발화", same_topic=True)
    ed.similar_box.setCurrentIndex(ed.similar_box.findData(False))      # 비슷한 채팅 있음
    ed.set_grade("likely")
    ed.set_confidence("certain")
    ed.rationale.setText("00:01:52 합성")
    ed.override.setText("그냥")
    v = ed.validate()
    assert any("연결 가능성 높음" in e and "저장할 수 없습니다" in e for e in v.errors)
    assert ed.save() is None and [r for r in env.saved if r["kind"] == "reaction"] == []


def test_8b_likely_below_direct_tier_is_allowed_with_reason(env):
    ed = env.ed
    _prepare_direct(ed)                          # 호명(강한 증거) → 직접 단계
    ed.set_grade("likely")
    v = ed.validate()
    assert not any("저장할 수 없습니다" in e for e in v.errors)
    assert v.grade_vs == "deviates" and any("사유" in e for e in v.errors)
    ed.override.setText("합성: 호명이 확실치 않음")
    res = ed.save()
    assert res is not None, ed.msg.text()
    assert res["reaction"]["body"]["grade"] == "likely"


def test_9_unclear_audio_with_one_candidate_needs_no_reason(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    fill_candidate(ed, 110_000, 113_000, "합성 발화", same_topic=True)
    ed.set_grade("unclear")
    ed.unclear_box.setCurrentIndex(ed.unclear_box.findData("audio_unclear"))
    ed.set_confidence("certain")
    ed.rationale.setText("00:01:50 합성 소리 안 들림")
    v = ed.validate()
    assert v.errors == [], v.errors
    assert v.grade_vs == "consistent" and "grade_rule_override" not in [c for c, _t in v.warnings]
    assert "불명확 ✓" in ed.cond_label.text()
    res = ed.save()
    assert res is not None
    rb = res["reaction"]["body"]
    assert rb["grade_vs_conditions"] == "consistent" and rb["override_reason"] is None


def test_low_first_judgment_kept_when_changed_within_one_edit(env):
    ed = env.ed
    _prepare_direct(ed)                          # direct · certain
    listen(env, 1)                               # 1회차
    ed.listen_again()                            # 2회차 시작 → 1회차 판단 스냅샷
    ed.set_confidence("probably")
    a, b = env.review.window()
    env.player.run(a, b)
    env.review.stop_loop()
    res = ed.save()
    assert res is not None, ed.msg.text()        # 진짜 store 의 스키마 검사를 통과(version_id null)
    rb = res["reaction"]["body"]
    assert rb["changed_on_recheck"] is True
    assert rb["first_judgment"] == {"version_id": None, "result": "graded", "grade": "direct",
                                    "confidence": "certain", "rationale": "00:01:52 합성 호명"}
    assert [v["body"]["judgment"]["confidence"] for v in res["video_checks"]] == ["certain", "probably"]


def test_low_partial_save_then_cancel_reports_written_lines(env):
    ed = env.ed
    _prepare_direct(ed)
    listen(env, 1)
    orig = ed._save_cb

    def flaky(draft):
        if draft["kind"] == "video_check":
            raise store.WriteVerifyError("합성 실패")
        return orig(draft)
    ed._save_cb = flaky
    got = []
    ed.partialSaved.connect(got.append)
    assert ed.save() is None and ed.is_open()
    ed.cancel()
    assert len(got) == 1
    assert got[0]["reaction"]["kind"] == "reaction"
    assert [r["kind"] for r in got[0]["written"]] == ["utterance", "reaction"]


def test_low_heard_count_of_prior_checks_uses_coverage(env):
    """불러온 영상 확인 기록도 덮은 비율이 모자라면 '들었다' 로 세지 않는다(내보내기와 같은 규칙)."""
    ed = env.ed
    _prepare_direct(ed)
    res = ed.save()
    fake = [{"body": {"check": "first", "audio_heard": True, "coverage_ratio": 1.0}},
            {"body": {"check": "recheck", "audio_heard": True, "coverage_ratio": 0.05}}]
    ed.open_existing(res["reaction"], utterances={u["entity_id"]: u for u in res["utterances"]}, video_checks=fake)
    assert ed._heard_count() == 1 and not ed.is_complete()


# ============================================================================ 구간 폼
A, B = tsf.A, tsf.B
RA, RB = tsf.RA, tsf.RB


def _overlaps(heads):
    iv = sorted((h["body"]["start_ms"], h["body"]["end_ms"]) for h in heads.values())
    return any(iv[i][1] > iv[i + 1][0] for i in range(len(iv) - 1))


class DiskSink(tsf.Sink):
    """디스크 상태(현재 머리)를 흉내 낸다. fail_retract=True 면 철회에서 실패."""

    def __init__(self, records, fail_retract=False, fail_at=None):
        super().__init__(fail_at=fail_at)
        self.heads = {r["entity_id"]: r for r in records}
        self.fail_retract = fail_retract

    def save(self, draft):
        out = super().save(draft)
        self.heads[draft["entity_id"]] = out
        return out

    def retract(self, eid, reason, merged_into):
        if self.fail_retract:
            raise RuntimeError("합성 실패")
        out = super().retract(eid, reason, merged_into)
        self.heads.pop(eid, None)
        return out


def test_2_merge_save_failure_leaves_gap_not_overlap(qapp):
    recs = [tsf.rec(A, RA, 0, 300_000, "just_chatting"), tsf.rec(B, RB, 300_000, tsf.DUR, "game")]
    sink = DiskSink(recs, fail_retract=True)
    form, _ = tsf.make(qapp, sink, records=recs)
    form.select_segment(B)
    assert form.merge_selected("prev")
    assert form.save() is None                         # 철회에서 실패 → 남은 구간의 새 판은 쓰지 않았다
    assert not _overlaps(sink.heads)
    # 디스크 상태로 다시 열어도 읽기 전용이 아니다
    form2, _ = tsf.make(qapp, tsf.Sink(), records=list(sink.heads.values()))
    assert not form2.read_only and not [p for p in form2.problems if "겹치는" in p]


def test_2b_boundary_move_writes_shrinking_side_first(qapp):
    recs = [tsf.rec(A, RA, 0, 300_000, "just_chatting"), tsf.rec(B, RB, 300_000, tsf.DUR, "game")]
    for fail_at in (0, 1):
        sink = DiskSink(recs, fail_at=fail_at)
        form, _ = tsf.make(qapp, sink, records=recs)
        form.select_segment(A)
        form.choose_edge("end")
        assert form.move_edge_to(400_000)             # A 가 늘고 B 가 준다
        plan = form.plan()
        assert [w["entity_id"] for w in plan.writes] == [B, A]
        form.save()                                   # fail_at 번째 줄에서 실패해도
        assert not _overlaps(sink.heads)
    sink = DiskSink(recs)
    form, _ = tsf.make(qapp, sink, records=recs)
    form.select_segment(A)
    form.choose_edge("end")
    assert form.move_edge_to(200_000)                 # A 가 줄고 B 가 는다
    assert [w["entity_id"] for w in form.plan().writes] == [A, B]
    assert form.save() is not None and not _overlaps(sink.heads)
