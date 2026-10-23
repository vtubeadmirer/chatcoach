"""app/reference_set.py — 훈련 기준 '원래 반응' 읽기, 시범 프로젝트 판별·잠김 판정 (08 D-18 7, 18 6-2·6-5).

합성 데이터만 쓴다(채팅 ID·본문·메모는 지어낸 값). 합성 최종 세트 프로젝트는 tests/test_final_set.py 의 proj 를 쓴다.
"""
import json
import os
import shutil
from datetime import timedelta

import pytest

from app import export, reference_set as rs
from app import seal
from app.core import training as tr
from tests.test_agreement import MID_1, NOW, SID_A, _lock as _lock_session
from tests.test_final_set import _clear_locks, cid, proj  # noqa: F401  (합성 최종 세트 프로젝트 fixture)
from tests.test_seal import _primary_only, _ready, _spy_open, data_of, pilot, result_doc  # noqa: F401  (합성 시범 fixture)

PID = "12345678-0000-4000-8000-000000000002"


def _bare(tmp_path, name="p888"):
    root = tmp_path / "data" / "projects" / name
    (root / "labels").mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project_id": PID}), encoding="utf-8")
    return str(root)


def _csv(root, name, rows, header="chat_id,chat_vod_ms,utterance_vod_ms,grade,note"):
    with open(os.path.join(root, "labels", name), "w", encoding="utf-8-sig", newline="") as f:
        f.write(header + "\r\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\r\n")


# ----------------------------------------------------------------------------- (가) 사람 최종 세트
def test_final_set_reference(proj):  # noqa: F811
    ref, why = rs.load_final_set_reference(proj["root"])
    assert why is None and ref.source == tr.REF_FINAL_SET
    # 1 direct · 2 likely · 3 likely → 원래 반응. 4·5 related_only · 6 unconfirmed → 아님
    assert [r.chat_id for r in ref.reactions] == [cid(1), cid(2), cid(3)]
    r1 = ref.reactions[0]
    assert (r1.chat_vod_ms, r1.reaction_vod_ms, r1.grade) == (110_000, 111_500, "direct")
    assert ref.ranges == [(0, 600_000)]
    assert ref.counts["skipped_grade"] == 3 and ref.counts["used"] == 3
    assert ref.sha256 == tr.reference_sha256(tr.REF_FINAL_SET, ref.reactions)
    assert ref.log_reference() == {"source": "final_set", "sha256": ref.sha256, "count": 3, "file": None}
    av = rs.training_availability(proj["root"])
    assert av.enabled and av.reason is None and not av.pilot and av.reference.source == tr.REF_FINAL_SET


def test_final_set_preferred_over_manual_csv(proj):  # noqa: F811
    _csv(proj["root"], "manual-direct-3.csv", [(cid(9), 1000, 2000, "direct", "합성 메모")])
    ref, why = rs.load_reference(proj["root"])
    assert ref.source == tr.REF_FINAL_SET


def test_unlocked_final_set_falls_back_to_manual(proj):  # noqa: F811
    os.remove(os.path.join(proj["root"], "locks", "L01-P-20261024-ab12.json"))
    ref, why = rs.load_final_set_reference(proj["root"])
    assert ref is None and why
    _csv(proj["root"], "manual-direct-3.csv", [(cid(9), 1000, 2000, "direct", "합성 메모")])
    ref, why = rs.load_reference(proj["root"])
    assert why is None and ref.source == tr.REF_MANUAL_CSV and ref.file_name == "manual-direct-3.csv"


def test_reference_from_final_export_dict_shape():
    fx = {"items": [
        {"set": "final", "row": {"label_kind": "reaction", "chat_id": cid(1), "chat_t_ms": 10, "result": "graded",
                                 "grade": "direct", "reaction_time_ms": None, "linked_utterance_id": "u1",
                                 "utterances": [{"utterance_id": "u1", "start_ms": 40}], "eval": {"lenient": True}}},
        {"set": "final", "row": {"label_kind": "reaction", "chat_id": cid(2), "chat_t_ms": 20, "result": "graded",
                                 "grade": "likely", "reaction_time_ms": 30, "eval": {"lenient": False}}},
        {"set": "excluded", "row": {"label_kind": "reaction", "chat_id": cid(3), "chat_t_ms": 20,
                                    "result": "graded", "grade": "direct", "eval": {"lenient": True}}},
    ], "segment_items": [{"set": "final", "piece": {"start_ms": 0, "end_ms": 50}},
                         {"set": "final", "piece": {"start_ms": 50, "end_ms": 90}},
                         {"set": "final", "piece": {"start_ms": 200, "end_ms": 300}},
                         {"set": "ambiguous", "piece": {"start_ms": 90, "end_ms": 200}}]}
    ref = rs.reference_from_final_export(fx)
    assert [(r.chat_id, r.reaction_vod_ms) for r in ref.reactions] == [(cid(1), 40)]   # 연결 발화 시작으로 채움
    assert ref.counts["skipped_eval"] == 1
    assert ref.ranges == [(0, 90), (200, 300)] and ref.first_range == (0, 90)
    assert rs.reference_from_final_export({"items": [], "segment_items": []}) is None


# ----------------------------------------------------------------------------- (나) 손라벨 CSV
def test_manual_csv_reads_needed_columns_only(tmp_path):
    root = _bare(tmp_path)
    _csv(root, "manual-direct-4.csv", [
        (cid(1), 10_000, 12_000, "direct", "합성 메모 하나"),
        (cid(2), 20_000, 21_500, "likely", "합성 메모 둘"),
        (cid(3), 30_000, 31_000, "related_only", ""),
        ("합성", 40_000, 41_000, "direct", ""),
        (cid(4), "", 41_000, "direct", ""),
        (cid(1), 10_000, 11_000, "direct", ""),            # 같은 채팅 두 번 → 이른 반응 하나
    ])
    ref = rs.read_manual_csv(os.path.join(root, "labels", "manual-direct-4.csv"))
    assert ref.source == tr.REF_MANUAL_CSV
    assert [(r.chat_id, r.chat_vod_ms, r.reaction_vod_ms) for r in ref.reactions] == [
        (cid(1), 10_000, 11_000), (cid(2), 20_000, 21_500)]
    assert ref.counts == {"rows": 6, "used": 2, "skipped_grade": 1, "skipped_shape": 2, "duplicates": 1}
    assert ref.ranges == [(10_000, 21_501)]
    blob = repr(ref.__dict__)
    assert "합성 메모" not in blob                                     # note 열은 남기지 않는다


def test_manual_csv_header_check(tmp_path):
    root = _bare(tmp_path)
    _csv(root, "manual-x.csv", [(cid(1), 1, 2)], header="chat_id,chat_vod_ms,grade")
    with pytest.raises(ValueError):
        rs.read_manual_csv(os.path.join(root, "labels", "manual-x.csv"))
    assert rs.load_manual_reference(root) is None
    ref, why = rs.load_reference(root)
    assert ref is None and why == rs.REASON_NO_REFERENCE


def test_manual_csv_order_and_names(tmp_path):
    root = _bare(tmp_path)
    _csv(root, "manual-b.csv", [(cid(2), 5, 6, "direct", "")])
    _csv(root, "manual-a.csv", [(cid(1), 5, 6, "related_only", "")])   # 원래 반응 0 → 다음 파일
    open(os.path.join(root, "labels", "other.csv"), "w").close()
    assert rs.manual_csv_files(root) == ["manual-a.csv", "manual-b.csv"]
    assert rs.load_manual_reference(root).file_name == "manual-b.csv"


def test_no_project():
    ref, why = rs.load_reference(os.path.join(os.sep, "없는", "폴더"))
    assert ref is None and why == rs.REASON_NO_PROJECT


# ----------------------------------------------------------------------------- 시범 프로젝트
def test_is_pilot_by_name_and_records(tmp_path):
    assert rs.is_pilot_project(_bare(tmp_path, "pilot-111"))
    assert rs.is_pilot_project(_bare(tmp_path, "Pilot-222"))
    other = _bare(tmp_path, "p333")
    assert not rs.is_pilot_project(other)
    pdir = tmp_path / "data" / "pilot"
    pdir.mkdir()
    assert rs.default_pilot_dir(other) == str(pdir)
    (pdir / "freeze.json").write_text(json.dumps({"schema": "chatcoach/pilot-freeze", "projects": [{"project_id": PID}]}),
                                      encoding="utf-8")
    assert rs.is_pilot_project(other)
    (pdir / "freeze.json").write_text(json.dumps({"schema": "chatcoach/pilot-freeze"}), encoding="utf-8")
    assert not rs.is_pilot_project(other)
    (pdir / "setup.json").write_text(json.dumps({"items": [{"project_root": other}]}), encoding="utf-8")
    assert rs.is_pilot_project(other)
    (pdir / "setup.json").write_text("깨진", encoding="utf-8")
    assert not rs.is_pilot_project(other)


def _copy_as_pilot(proj, tmp_path):  # noqa: F811
    dst = tmp_path / "pilotcopy" / "projects" / "pilot-777"
    shutil.copytree(proj["root"], dst)
    return str(dst)


def test_pilot_unlocked_blocks_without_reading_labels(proj, tmp_path, monkeypatch):  # noqa: F811
    root = _copy_as_pilot(proj, tmp_path)
    try:
        _csv(root, "manual-direct-1.csv", [(cid(1), 1, 2, "direct", "")])
        ok, reasons = rs.pilot_sessions_locked(root)
        assert not ok and reasons                    # 1차·재라벨은 잠겼지만 재라벨 계획이 없다(06 7-1)
        monkeypatch.setattr(export, "build_final_set", lambda *a, **k: pytest.fail("최종 세트를 열었다"))
        monkeypatch.setattr(rs, "read_manual_csv", lambda *a, **k: pytest.fail("손라벨을 열었다"))
        av = rs.training_availability(root)
        assert not av.enabled and av.pilot and av.reason == rs.REASON_PILOT_LOCKED and av.reference is None
    finally:
        shutil.rmtree(os.path.dirname(os.path.dirname(root)))


def test_pilot_all_locked_enables(proj, tmp_path, monkeypatch):  # noqa: F811
    root = _copy_as_pilot(proj, tmp_path)
    try:
        monkeypatch.setattr(seal, "unseal_conditions",
                            lambda *a, **k: seal.UnsealConditions(met=True, reasons=[], warnings=[],
                                                                  labeling_sessions=[]))
        assert rs.pilot_sessions_locked(root) == (True, [])
        assert rs.pilot_block_reason(root) is None
        av = rs.training_availability(root)
        assert av.enabled and av.pilot and av.reference.source == tr.REF_FINAL_SET
    finally:
        shutil.rmtree(os.path.dirname(os.path.dirname(root)))


def test_pilot_lock_check_error_is_blocked(tmp_path, monkeypatch):
    root = _bare(tmp_path, "pilot-999")

    def boom(*a, **k):
        raise seal.SealError("합성 오류")
    monkeypatch.setattr(seal, "unseal_conditions", boom)
    ok, reasons = rs.pilot_sessions_locked(root)
    assert not ok and reasons == ["잠금 상태를 확인하지 못했습니다 (SealError)"]
    assert rs.pilot_block_reason(root) == rs.REASON_PILOT_LOCKED


# ----------------------------------------------------------------------------- 해제 기록이 있으면 잠김 (08 D-27 3)
# 합성 시범 프로젝트(tests.test_seal.pilot — 폴더 pilot-777, 1차 A · 두 번째 사람 재라벨 B)와 합성 엔진 결과만 쓴다.
def _as_conditions(root):
    c = seal.unseal_conditions(root)
    return c.met, list(c.reasons)


def test_pilot_locked_without_unseal_record_is_unseal_conditions(pilot):  # noqa: F811
    """(a) 해제 기록이 없으면 전과 같다 — 봉인 기록(ai.sealed)만 있어도 unseal_conditions 판단 그대로."""
    root = pilot["root"]
    assert not seal.conditions_met_unseal_exists(root)                  # seal.jsonl 없음
    ok, reasons = rs.pilot_sessions_locked(root)
    assert (ok, reasons) == _as_conditions(root) and not ok and reasons
    seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    assert not seal.conditions_met_unseal_exists(root)                  # ai.sealed 뿐
    assert rs.pilot_sessions_locked(root) == _as_conditions(root)
    assert rs.pilot_block_reason(root) == rs.REASON_PILOT_LOCKED
    _ready(root)                                                        # 06 7-1 조건이 됨(풀지는 않음)
    assert not seal.conditions_met_unseal_exists(root)
    assert rs.pilot_sessions_locked(root) == _as_conditions(root) == (True, [])
    assert rs.pilot_block_reason(root) is None


def test_pilot_locked_after_conditions_met_unseal_with_flag(pilot, monkeypatch):  # noqa: F811
    """(b) 재라벨 계획 없이 플래그로 푼 기록(conditions_met = true · allow_no_relabel = true)이 있으면 (True, []).
    플래그 없는 unseal_conditions 는 여전히 no_relabel_plan 으로 막지만 화면은 다시 막지 않는다. runs\\ 는 열지 않는다."""
    root = _primary_only(pilot)
    _lock_session(root, SID_A, MID_1)
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    assert rs.pilot_sessions_locked(root) == _as_conditions(root) and not rs.pilot_sessions_locked(root)[0]
    r = seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=1), allow_no_relabel=True)
    assert r.record["conditions_met"] is True and r.record["allow_no_relabel"] is True and r.record["relabel"] == "none"
    c = seal.unseal_conditions(root)
    assert not c.met and c.codes == ["no_relabel_plan"]                 # 플래그 없는 해제 조건은 그대로
    opened = _spy_open(monkeypatch, os.path.normcase(os.sep + "runs" + os.sep))
    assert seal.conditions_met_unseal_exists(root)
    assert rs.pilot_sessions_locked(root) == (True, [])
    assert rs.pilot_block_reason(root) is None
    assert opened == []
    real = seal.unseal_conditions
    monkeypatch.setattr(seal, "unseal_conditions",
                        lambda *a, **k: pytest.fail("해제 기록이 있는데 unseal_conditions 를 불렀다"))
    assert rs.pilot_sessions_locked(root) == (True, [])
    monkeypatch.setattr(seal, "unseal_conditions", real)
    # seal.jsonl 이 깨지면 기록을 믿지 않고 전처럼 unseal_conditions 로 본다
    sj = os.path.join(os.path.dirname(os.path.dirname(r.run_path)), seal.SEAL_FILE)     # <ai>\<project_id>\seal.jsonl
    with open(sj, "ab") as f:
        f.write(b"{broken\n")
    assert not seal.conditions_met_unseal_exists(root)
    assert rs.pilot_sessions_locked(root) == _as_conditions(root) == (False, c.reasons)


def test_pilot_early_unseal_record_does_not_count_as_locked(pilot):  # noqa: F811
    """(c) 앞당겨 연 기록(conditions_met = false)만 있으면 잠긴 것으로 보지 않는다 — unseal_conditions 판단 그대로."""
    root = pilot["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    r = seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험 사유")
    assert r.record["conditions_met"] is False
    assert not seal.conditions_met_unseal_exists(root)
    ok, reasons = rs.pilot_sessions_locked(root)
    assert (ok, reasons) == _as_conditions(root) and not ok and reasons
    assert rs.pilot_block_reason(root) == rs.REASON_PILOT_LOCKED
