"""다섯째 묶음 검토 지적 재현 시험 (합성 데이터, tmp_path 만).

1. 비교하는 재라벨 세션의 계획이 취소돼도 재현 검증(verify_plan_on_disk)을 건너뛰지 않고, 그 세션은 대표값에서 뺀다(06 7-1).
2. 조정 기록 열쇠: 세 번째 세션이 생겨 표본 건의 대상이 늘면 옛 기록을 '대상 바뀜' 으로 보이고, 다시 저장하면 그 기록을
   supersedes 한다(같은 채팅에 유효한 조정 기록이 둘 남지 않음).
3. ④ 를 매긴 뒤 ③ 을 직접·높음으로 고친 채팅의 놓친 후보는 일치도에서 빠진다(06 2-9).
(재라벨 색인 지적은 tests/test_relabel_index.py 에 있다.)
"""
import os
from datetime import timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import agreement_report as ar  # noqa: E402
from app import relabel  # noqa: E402
from app.core import adjudicate as adj  # noqa: E402
from app.core import agreement as ag  # noqa: E402
from app.core import relabel_plan as rp  # noqa: E402
from tests.test_integration_c4 import (SID_A, close_wb, ctx, lock_via_dialog, make_long_project,  # noqa: E402
                                       open_wb, primary_session)
from tests.test_integration_c4 import qapp  # noqa: E402,F401
from tests.test_workbench_labeling import NOW  # noqa: E402


# ============================================================================ 1. 취소된 계획의 세션 비교
def _locked_second_then_cancel(qapp, tmp_path):
    proj = make_long_project(tmp_path)
    wb, _doc, _a, _j = primary_session(qapp, proj)
    p_sec = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="r201")
    close_wb(wb)
    sid_b = "L02-R2-20261021-ef56"
    wb = open_wb(qapp, proj, ctx(sid_b, "relabel_second", "L02", **relabel.session_args(p_sec)))
    lock_via_dialog(wb)
    close_wb(wb)
    relabel.cancel_plan(proj.root, p_sec["plan_id"], reason="합성: 계획 오류", cancelled_by="L01",
                        cancelled_machine4="abcd", app_version="0", canceller_kind=None)
    return proj, p_sec, sid_b


def test_cancelled_plan_session_is_verified_and_not_representative(qapp, tmp_path, monkeypatch):
    proj, p_sec, sid_b = _locked_second_then_cancel(qapp, tmp_path)
    calls = []
    real = relabel.verify_plan_on_disk
    monkeypatch.setattr(relabel, "verify_plan_on_disk", lambda *a, **k: (calls.append(a[2]), real(*a, **k))[1])
    res = ar.build_report(proj.root, SID_A, sid_b, created_by="L01", reader_kind=None)
    assert p_sec["plan_id"] in calls                                  # 취소돼도 재현 검증을 한다
    assert sum("취소" in w for w in res.warnings) == 1
    h = res.report["header"]
    assert h["representative"] is False and h["cancelled_plan_sessions"] == [sid_b]
    assert h["view_exposed_sessions"] == []
    assert "재라벨 계획 취소됨" in res.markdown and "노출 기록 있음" not in res.markdown
    assert any("재라벨 계획이 취소된" in s for s in res.report["interpretation"])
    assert ar.comparison_gate(proj.root, {"sample_seed": None}, ()) == []   # 공개 모양(경고 목록)은 그대로


def test_cancelled_plan_that_fails_verify_blocks_report(qapp, tmp_path, monkeypatch):
    proj, p_sec, sid_b = _locked_second_then_cancel(qapp, tmp_path)
    monkeypatch.setattr(relabel, "verify_plan_on_disk",
                        lambda *a, **k: rp.Verification(False, ("합성 실패",)))
    with pytest.raises(ar.SampleVerifyError, match=p_sec["plan_id"]):
        ar.build_report(proj.root, SID_A, sid_b, created_by="L01", reader_kind=None)


# ============================================================================ 2. 조정 기록 열쇠 (세 세션)
def _side(sid, val):
    return adj.CaseSide(sid, "L01", val, None, None, "e_" + sid, "v_" + sid, body={"chat_id": "c1", "grade": val})


def _cases():
    c12 = adj.Case("reaction:c1", adj.TARGET_REACTION, adj.C_GRADE, 1000,
                   (_side("S1", "direct"), _side("S2", "related_only")), chat_id="c1")
    c23 = adj.Case("reaction:c1", adj.TARGET_REACTION, adj.C_GRADE, 1000,
                   (_side("S2", "related_only"), _side("S3", "direct")), chat_id="c1")
    return c12, c23


def test_third_session_marks_old_adjudication_changed_and_resave_supersedes():
    c12, c23 = _cases()
    body = adj.make_body(c12, decision=adj.D_FINAL, cause=adj.CAUSES[0], pick="S1")
    rec = {"kind": adj.KIND, "id": "adj_v1", "entity_id": "adj_e1", "seq": 1, "body": body}
    st2 = adj.case_states([c12], [rec])["reaction:c1"]
    assert st2.decided and not st2.stale and not st2.targets_changed
    merged = adj.merge_cases([[c12], [c23]])
    assert [s.session_id for s in merged[0].sides] == ["S1", "S2", "S3"]
    st3 = adj.case_states(merged, [rec])["reaction:c1"]
    assert st3.decided and st3.stale and st3.targets_changed           # 예전: decided=False('미조정')
    assert adj.summary(merged, {"reaction:c1": st3})["stale"] == 1
    d = adj.make_draft(merged[0], adj.make_body(merged[0], decision=adj.D_FINAL, cause=adj.CAUSES[0], pick="S1"),
                       st3.record)
    assert d["supersedes"] == "adj_v1" and d["entity_id"] == "adj_e1"  # 같은 채팅에 유효 기록이 둘 남지 않음
    # 거꾸로(세 세션에서 조정 → 두 세션만 체크)도 같은 기록으로 본다
    rec3 = {"kind": adj.KIND, "id": "adj_v2", "entity_id": "adj_e1", "seq": 2, "body": d["body"]}
    back = adj.case_states([c12], [rec3])["reaction:c1"]
    assert back.decided and back.targets_changed


def test_segment_cases_keep_exact_key_only():
    s1 = adj.CaseSide("S1", "L01", "just_chatting", None, None, "seg1", "v1", 0, 10_000)
    s2 = adj.CaseSide("S2", "L01", "game", None, None, "seg2", "v2", 0, 10_000)
    s3 = adj.CaseSide("S3", "L01", "game", None, None, "seg3", "v3", 0, 10_000)
    c12 = adj.Case("segment:0-10000", adj.TARGET_SEGMENT, adj.C_CATEGORY, 0, (s1, s2), spans=((0, 10_000),))
    c13 = adj.Case("segment:0-10000", adj.TARGET_SEGMENT, adj.C_CATEGORY, 0, (s1, s3), spans=((0, 10_000),))
    rec = {"kind": adj.KIND, "id": "a1", "entity_id": "e1", "seq": 1,
           "body": {"target_kind": adj.TARGET_SEGMENT, "targets": c12.targets()}}
    st = adj.case_states([c13], [rec])
    assert not st["segment:0-10000"].decided                          # 다른 짝의 구간 기록을 끌어오지 않는다


# ============================================================================ 3. ③ 을 고친 뒤의 놓친 후보 (06 2-9)
def _rx(c, grade, eid):
    b = {"chat_id": c, "entry": "from_sample", "confidence": "certain", "first_visible_ms": 1000}
    b.update(result="unconfirmed", grade=None) if grade == "unconfirmed" else b.update(result="graded", grade=grade)
    return {"kind": "reaction", "entity_id": eid, "body": b}


def _mc(c):
    return {"kind": "missed_candidate", "body": {"chat_id": c, "scope": "sample", "is_candidate": False,
                                                 "reason_code": "C", "reason_code_source": "manual",
                                                 "first_visible_ms": 1000, "confidence": "certain"}}


def _sides(grade_x):
    X, Y = "c" + "0" * 19 + "1", "c" + "0" * 19 + "2"
    a = {"session_id": "S1", "reactions": [_rx(X, grade_x, "a1"), _rx(Y, "related_only", "a2")],
         "missed": [_mc(X), _mc(Y)], "segments": [], "scope": None}
    b = {"session_id": "S2", "reactions": [_rx(X, grade_x, "b1"), _rx(Y, "unconfirmed", "b2")],
         "missed": [_mc(X), _mc(Y)], "segments": [], "scope": None}
    return a, b


def test_missed_candidate_dropped_when_reaction_regraded_direct():
    res = ag.build_agreement(*_sides("direct"))
    m = res["missed"]
    assert m["dropped_not_target"] == {"a": 1, "b": 1}
    assert m["judged"]["both"] == 1 and m["reason_code"]["n"] == 1     # 예전: 2 (대상 밖 채팅 X 가 세어짐)
    assert any("06 2-9" in s for s in res["interpretation"])
    still = ag.build_agreement(*_sides("unclear"))["missed"]
    assert still["dropped_not_target"] == {"a": 0, "b": 0} and still["judged"]["both"] == 2


def test_drop_missed_not_target_keeps_unknown_cases():
    X = "c" + "0" * 19 + "1"
    keep, n = ag.drop_missed_not_target([_mc(X)], [])                 # 반응이 없음 → 가를 수 없어 남김
    assert n == 0 and len(keep) == 1
    keep, n = ag.drop_missed_not_target([_mc(X)], [_rx(X, "direct", "a"), _rx(X, "direct", "b")])   # 둘 이상
    assert n == 0 and len(keep) == 1
    extra = {"kind": "missed_candidate", "body": {**_mc(X)["body"], "scope": "extra"}}
    assert ag.drop_missed_not_target([extra], [_rx(X, "direct", "a")]) == ([extra], 0)
