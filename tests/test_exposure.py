"""app/core/exposure.py — 계획 취소·봉인 해제 뒤 라벨링 세션의 AI 결과 노출 표시 (16 8-1 7번, 06 7-1, D-15 7).

합성 값만 쓴다(세션 ID·시각·계획 ID 모두 지어낸 것). 파일을 만들지 않는다.
"""
from app import relabel
from app.core import exposure as ex
from app.core import visibility as vis

CANCEL_AT = "2026-10-23T10:00:00+09:00"


def cancel_doc(plan_id="RP-20261022-ab12", at=CANCEL_AT):
    """app.relabel.cancel_plan 이 쓰는 모양(검사 통과)의 합성 취소 기록."""
    return {"schema": relabel.CANCEL_SCHEMA, "schema_version": 1, "plan_id": plan_id, "plan_sha256": "a" * 64,
            "plan_readable": True, "source_session_id": "L01-P-20261021-ab12", "session_kind": "relabel_second",
            "labeler_id": "L02", "reason": "합성 사유", "cancelled_at": at, "cancelled_by": "L01",
            "cancelled_machine4": "abcd", "canceller_is_labeler": True, "app_version": "0.0.0"}


def S(sid, kind, created, locked=None, plan=None):
    return ex.SessionInfo(sid, kind, created, locked, plan)


def test_cancel_doc_shape_is_real():
    """시험의 합성 취소 기록이 app.relabel 의 실제 검사를 통과한다(모양이 어긋나면 이 시험이 먼저 깨진다)."""
    from app.core import relabel_plan as rp
    assert rp.PLAN_ID_RE.match("RP-20261022-ab12")
    probs = relabel.check_cancel(cancel_doc())
    assert probs == [], probs


def test_relabel_session_created_after_cancel_is_ai_exposed():
    sessions = [
        S("L02-R2-20261023-aa01", "relabel_second", "2026-10-23T10:00:01+09:00"),   # 취소 뒤
        S("L02-R2-20261023-aa02", "relabel_second", "2026-10-23T09:59:59+09:00"),   # 취소 전
        S("L01-RS-20261023-aa03", "relabel_same", CANCEL_AT),                       # 같은 초 → 노출(보수적)
        S("L01-P-20261023-aa04", "primary", "2026-10-23T11:00:00+09:00"),          # 1차는 취소 규칙 대상 아님
        S("L01-AN-20261023-aa05", "analysis", "2026-10-23T11:00:00+09:00"),        # 라벨링 세션 아님
        S("L01-AD-20261023-aa06", "adjudication", "2026-10-23T11:00:00+09:00"),
    ]
    v = ex.compute_exposure(sessions, cancels=[cancel_doc()])
    assert v.exposed(ex.AI_RESULTS) == {"L02-R2-20261023-aa01", "L01-RS-20261023-aa03"}
    assert v.exposed("ai_results") == v.exposed()
    assert v.reasons("L02-R2-20261023-aa01") == (ex.REASON_PLAN_CANCELLED,)
    m = v.marks_of("L02-R2-20261023-aa01")[0]
    assert m.basis == "RP-20261022-ab12" and m.basis_at == CANCEL_AT and m.what == vis.AI_RESULTS
    assert not v.is_exposed("L02-R2-20261023-aa02") and not v.is_exposed("L01-P-20261023-aa04")
    assert v.exposed("other_session_labels") == frozenset()


def test_time_zones_are_compared_as_instants():
    # 취소 10:00+09:00 == 01:00Z. 01:00:30Z 에 만든 세션은 뒤, 00:59:00Z 는 앞
    sessions = [S("L02-R2-20261023-bb01", "relabel_second", "2026-10-23T01:00:30Z"),
                S("L02-R2-20261023-bb02", "relabel_second", "2026-10-23T00:59:00+00:00")]
    v = ex.compute_exposure(sessions, cancels=[cancel_doc()])
    assert v.exposed() == {"L02-R2-20261023-bb01"}


def test_unknown_times_are_blind_safe():
    sessions = [S("L02-R2-20261023-cc01", "relabel_second", None),
                S("L02-R2-20261023-cc02", "relabel_second", "2026-10-23T12:00:00"),      # 시간대 없음
                S("L02-R2-20261023-cc03", "relabel_second", "어제"),
                S("L02-R2-20261023-cc04", "relabel_second", "2026-10-20T12:00:00+09:00")]
    v = ex.compute_exposure(sessions, cancels=[cancel_doc()])
    for sid in ("L02-R2-20261023-cc01", "L02-R2-20261023-cc02", "L02-R2-20261023-cc03"):
        assert v.reasons(sid) == (ex.REASON_TIME_UNKNOWN,)
    assert not v.is_exposed("L02-R2-20261023-cc04")
    # 취소 시각을 못 읽으면 '맨 처음' — 그 프로젝트의 재라벨 세션은 모두 노출
    v = ex.compute_exposure(sessions[3:], cancels=[cancel_doc(at="깨짐")])
    assert v.exposed() == {"L02-R2-20261023-cc04"}


def test_no_cancel_no_unseal_means_nothing():
    sessions = [S("L02-R2-20261023-dd01", "relabel_second", None)]
    v = ex.compute_exposure(sessions)
    assert v.marks == () and v.exposed() == frozenset()
    assert v.view_exposed("L02-R2-20261023-dd01", ["asr_text"]) == ["asr_text"]


def test_unseal_marks_sessions_not_locked_before_it():
    un = {"kind": "ai.unsealed", "run_id": "run-synth-1", "unsealed_at": "2026-10-25T09:00:00+09:00",
          "conditions_met": True}
    sessions = [
        S("L01-P-20261021-ee01", "primary", "2026-10-21T09:00:00+09:00", "2026-10-21T18:00:00+09:00"),   # 앞에 잠김
        S("L01-P-20261024-ee02", "primary", "2026-10-24T09:00:00+09:00", None),                         # 열려 있었음
        S("L01-P-20261024-ee03", "primary", "2026-10-24T09:00:00+09:00", "2026-10-25T10:00:00+09:00"),   # 해제 뒤 잠김
        S("L02-R2-20261026-ee04", "relabel_second", "2026-10-26T09:00:00+09:00", "2026-10-26T12:00:00+09:00"),
        S("L01-AN-20261026-ee05", "analysis", "2026-10-26T09:00:00+09:00"),
    ]
    v = ex.compute_exposure(sessions, unseals=[un])
    assert v.exposed() == {"L01-P-20261024-ee02", "L01-P-20261024-ee03", "L02-R2-20261026-ee04"}
    assert v.reasons("L02-R2-20261026-ee04") == (ex.REASON_AI_UNSEALED,)
    assert v.marks_of("L01-P-20261024-ee02")[0].basis == "run-synth-1"
    # 해제 시각 칸이 at(app/seal.py 의 ai.unsealed 줄)·created_at 이어도 읽는다
    for key in ("at", "created_at"):
        un2 = {"kind": "ai.unsealed", "run_id": "run-synth-2", key: "2026-10-25T09:00:00+09:00"}
        assert ex.compute_exposure(sessions, unseals=[un2]).exposed() == v.exposed()


def test_cancel_and_unseal_together_deduplicate_and_merge_with_records():
    sessions = [S("L02-R2-20261026-ff01", "relabel_second", "2026-10-26T09:00:00+09:00")]
    v = ex.compute_exposure(sessions, cancels=[cancel_doc(), cancel_doc()],
                            unseals=[{"run_id": "r1", "unsealed_at": "2026-10-25T09:00:00+09:00"}])
    assert v.reasons("L02-R2-20261026-ff01") == (ex.REASON_AI_UNSEALED, ex.REASON_PLAN_CANCELLED)
    assert len(v.marks_of("L02-R2-20261026-ff01")) == 2          # 같은 취소 기록 두 번은 한 번
    assert v.whats("L02-R2-20261026-ff01") == ("ai_results",)
    # 기록된 view.exposed(what) 와 계산값을 합친 목록 — agreement_report._side 의 view_exposed 자리
    assert v.view_exposed("L02-R2-20261026-ff01", ["asr_text", "ai_results", 3]) == ["ai_results", "asr_text"]


def test_session_info_from_open_record_and_dict_inputs():
    rec = {"kind": "session.open", "session_id": "L02-R2-20261023-gg01",
           "body": {"session_kind": "relabel_second", "session_created_at": "2026-10-23T10:30:00+09:00",
                    "relabel_plan_id": "RP-20261022-ab12"}}
    si = ex.session_info(rec, locked_at="2026-10-23T12:00:00+09:00")
    assert si == ex.SessionInfo("L02-R2-20261023-gg01", "relabel_second", "2026-10-23T10:30:00+09:00",
                                "2026-10-23T12:00:00+09:00", "RP-20261022-ab12")
    assert ex.session_info({"kind": "run.start"}) is None
    assert ex.session_info({"kind": "session.open", "session_id": "x", "body": []}) is None
    v = ex.compute_exposure([si, {"session_id": "L02-R2-20261023-gg02", "session_kind": "relabel_second",
                                  "session_created_at": "2026-10-23T10:30:00+09:00"}, {"bad": 1}, None],
                            cancels=[cancel_doc(), "깨진 줄"])
    assert v.exposed() == {"L02-R2-20261023-gg01", "L02-R2-20261023-gg02"}


def test_parse_time():
    assert ex.parse_time("2026-10-23T10:00:00+09:00").utcoffset().total_seconds() == 9 * 3600
    assert ex.parse_time("2026-10-23T01:00:00Z") == ex.parse_time(CANCEL_AT)
    for bad in (None, "", "  ", "2026-10-23T10:00:00", "x", 5):
        assert ex.parse_time(bad) is None


def test_core_layer_rules_hold_for_exposure():
    import os
    from tests import test_layers as tl
    path = os.path.join(tl.CORE, "exposure.py")
    with open(path, encoding="utf-8") as f:
        assert tl.violations(f.read(), path) == []
