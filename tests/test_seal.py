"""AI 결과 봉인·해제 (06 7-1·7-2·7-3, 03 6-14, 08 D-15 7·8) — app/seal.py, tools/ai_seal.py.

합성 프로젝트(tmp_path)와 합성 엔진 결과만 쓴다. 실제 프로젝트·채팅·닉네임은 쓰지 않는다.
"""
import builtins
import hashlib
import inspect
import json
import os
from datetime import timedelta

import pytest

from app import ai_results as air
from app import jsonl, project, relabel, seal
from app.core import viewer_score as VS
from tests.test_agreement import (MID_1, NOW, PID, SALT, SID_A, SID_B, _grade_pairs, _label_lines,  # noqa: F401
                                  _lock, _write_session, A_SEG, B_SEG, cid, proj)
from tools import ai_seal
from tools import engine_to_ai as e2a

SECRET_RUN = "run-synthmarker-77"
SECRET_ENGINE = "synthengine-marker"


def result_doc(run_id=SECRET_RUN, **kw):
    d = {"schema": "chatcoach/ai-result", "schema_version": 1, "run_id": run_id,
         "created_at": "2026-10-22T10:00:00+09:00",
         "engine": {"name": SECRET_ENGINE, "version": "0.0.1", "preregistered": True},
         "target": {"platform": "chzzk", "video_no": "777", "chat_id_algorithm": "sl-chat-id-v1"},
         "time_axis": "vod_ms",
         "reactions": [{"chat_id": cid(1), "result": "graded", "grade": "likely", "reaction_time_ms": 3000},
                       {"chat_id": cid(1), "result": "unconfirmed", "grade": None},
                       {"chat_id": cid(50), "result": "unconfirmed", "grade": None}]}
    d.update(kw)
    return d


def data_of(d) -> bytes:
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def ai_dir(proj):
    from app.project import open_project
    pid = open_project(proj["root"]).project_id
    return os.path.join(seal.default_ai_root(proj["root"]), pid)


def _make_plan(root, kind, labeler, rand4):
    from app.project import open_project
    data = open_project(root).data
    return relabel.create_plan(root, data, session_kind=kind, labeler_id=labeler, source_session_id=SID_A,
                               done_ranges=[(0, 600_000)], created_by="L01", created_machine4="aaaa",
                               app_version="0.0.1", creator_kind=None, now=NOW + timedelta(hours=1), rand4=rand4)


def _set_lock(root, sid, **fields):
    p = os.path.join(root, "locks", f"{sid}.json")
    with open(p, encoding="utf-8") as f:
        doc = json.load(f)
    doc.update(fields)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def _ready(root):
    """1차 잠금 + 두 번째 사람 계획 + 그 재라벨 잠금 → 06 7-1 조건이 된다."""
    _lock(root, SID_A, MID_1)
    p2 = _make_plan(root, "relabel_second", "L02", "aa01")
    _lock(root, SID_B, MID_1)
    _set_lock(root, SID_B, relabel_plan_id=p2["plan_id"])
    return p2


def _spy_open(monkeypatch, needle):
    opened = []
    real = builtins.open

    def spy(path, *a, **kw):
        try:
            p = os.path.normcase(os.path.abspath(os.fspath(path)))
        except TypeError:
            p = ""
        if needle in p:
            opened.append(p)
        return real(path, *a, **kw)
    monkeypatch.setattr(builtins, "open", spy)
    return opened


# ============================================================================== 봉인
def test_seal_writes_bin_and_record_with_hash_size_time_source_only(proj):
    data = data_of(result_doc())
    e = seal.seal_bytes(proj["root"], data, source="engine_link", by="L01", now=NOW)
    d = ai_dir(proj)
    sha = hashlib.sha256(data).hexdigest()
    assert e.new and e.sha256 == sha and e.size == len(data) and e.check == "valid" and e.presealed is True
    assert os.path.normcase(d).startswith(os.path.normcase(str(proj["tmp"] / "chatcoach-data" / "ai")))
    with open(os.path.join(d, "sealed", f"{sha}.bin"), "rb") as f:
        assert f.read() == data
    raw = open(os.path.join(d, "seal.jsonl"), encoding="utf-8").read()
    rec = json.loads(raw)
    assert set(rec) == {"v", "seq", "kind", "at", "by", "app_version", "sha256", "size", "source", "check",
                        "reject_reasons"}
    assert rec["kind"] == "ai.sealed" and rec["source"] == "engine_link" and rec["reject_reasons"] == []
    assert SECRET_RUN not in raw and SECRET_ENGINE not in raw          # 해석 결과를 남기지 않는다 (06 7-2)
    # 같은 바이트는 다시 기록하지 않는다
    e2 = seal.seal_bytes(proj["root"], data, source="file", by="L01", now=NOW)
    assert not e2.new and e2.source == "engine_link"
    assert len(open(os.path.join(d, "seal.jsonl"), encoding="utf-8").read().splitlines()) == 1
    # 프로젝트 폴더에는 아무것도 생기지 않는다
    assert not os.path.exists(os.path.join(proj["root"], "ai"))


def test_seal_rejected_file_is_sealed_with_codes_and_cannot_unseal(proj):
    d = result_doc()
    d["target"]["video_no"] = "778"
    e = seal.seal_bytes(proj["root"], data_of(d), source="file", by="L01", now=NOW)
    assert e.check == "rejected" and e.reject_reasons == [air.R_VIDEO_NO]
    _ready(proj["root"])
    with pytest.raises(seal.SealError, match="거부"):
        seal.unseal(proj["root"], e.sha256, by="L01", now=NOW)
    bad = seal.seal_bytes(proj["root"], b'{"x": NaN}', source="file", by="L01", now=NOW)
    assert bad.reject_reasons == [air.R_NAN]


def test_seal_refuses_bad_args_and_oversize(proj, monkeypatch):
    with pytest.raises(seal.SealError):
        seal.seal_bytes(proj["root"], b"{}", source="web", by="L01")
    with pytest.raises(seal.SealError):
        seal.seal_bytes(proj["root"], b"{}", source="file", by="nobody")
    monkeypatch.setattr(air, "MAX_BYTES", 10)
    with pytest.raises(seal.SealError, match="상한"):
        seal.seal_bytes(proj["root"], b"x" * 11, source="file", by="L01")
    assert not os.path.exists(ai_dir(proj))


def test_ai_root_inside_project_refused(proj):
    with pytest.raises(seal.SealError, match="프로젝트"):
        seal.seal_bytes(proj["root"], b"{}", source="file", by="L01", ai_root=os.path.join(proj["root"], "x"))


@pytest.mark.parametrize("kind", ["primary", "relabel_same", "relabel_second", "origin_review"])
def test_labeling_sessions_cannot_touch_ai_folder(proj, kind, monkeypatch):
    data = data_of(result_doc())
    seal.seal_bytes(proj["root"], data, source="file", by="L01", now=NOW)
    opened = _spy_open(monkeypatch, os.path.normcase(os.sep + "ai" + os.sep))
    for call in (lambda: seal.seal_bytes(proj["root"], data, source="file", by="L01", reader_kind=kind),
                 lambda: seal.seal_status(proj["root"], reader_kind=kind),
                 lambda: seal.unseal_conditions(proj["root"], reader_kind=kind),
                 lambda: seal.unseal(proj["root"], "0" * 64, by="L01", reader_kind=kind),
                 lambda: seal.read_run(proj["root"], SECRET_RUN, reader_kind=kind),
                 lambda: seal.seal_vectors_bytes(proj["root"], b"{}", pair_sha256="0" * 64, by="L01", reader_kind=kind),
                 lambda: seal.read_vectors(proj["root"], SECRET_RUN, reader_kind=kind)):
        with pytest.raises(seal.SealBlindError):
            call()
    assert opened == []


def test_analysis_session_sees_count_and_unsealed_runs_only(proj, monkeypatch):
    """분석 세션(visibility UNSEALED_ONLY, E 통합): 건수 보기·해제된 결과 읽기만. 봉인·조건·해제는 막고 .bin 은 열지 않는다."""
    root = proj["root"]
    data = data_of(result_doc())
    e = seal.seal_bytes(root, data, source="file", by="L01", now=NOW)
    bins = _spy_open(monkeypatch, os.path.normcase(os.sep + "sealed" + os.sep))
    for call in (lambda: seal.seal_bytes(root, data, source="file", by="L01", reader_kind="analysis"),
                 lambda: seal.unseal_conditions(root, reader_kind="analysis"),
                 lambda: seal.unseal(root, e.sha256, by="L01", reader_kind="analysis")):
        with pytest.raises(seal.SealBlindError):
            call()
    st = seal.seal_status(root, reader_kind="analysis")
    assert st.sealed_count == 1 and st.unsealed_count == 0
    with pytest.raises(seal.SealError, match="해제 기록"):          # 해제 전: runs 도 열지 않는다
        seal.read_run(root, SECRET_RUN, reader_kind="analysis")
    assert bins == []
    _ready(root)
    seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4))
    run = seal.read_run(root, SECRET_RUN, reader_kind="analysis")
    assert run["run_id"] == SECRET_RUN
    assert seal.seal_status(root, reader_kind="analysis").unsealed_count == 1


def test_adjudication_may_seal_and_see_status(proj):
    e = seal.seal_bytes(proj["root"], data_of(result_doc()), source="file", by="L01", reader_kind="adjudication")
    assert seal.seal_status(proj["root"], reader_kind="adjudication").sealed_count == 1 and e.new


# ============================================================================== 상태·사전 봉인
def test_status_counts_and_presealed_against_first_primary_lock(proj, monkeypatch):
    root = proj["root"]
    early = seal.seal_bytes(root, data_of(result_doc("run-a")), source="file", by="L01", now=NOW)   # 09:00
    _lock(root, SID_A, MID_1)                                                                       # 10:00
    late = seal.seal_bytes(root, data_of(result_doc("run-b")), source="file", by="L01",
                           now=NOW + timedelta(hours=2))
    assert late.presealed is False
    opened = _spy_open(monkeypatch, os.path.normcase(os.sep + "sealed" + os.sep))
    st = seal.seal_status(root)
    assert opened == []                                                 # 봉인 파일을 열지 않는다
    assert st.sealed_count == 2 and st.unsealed_count == 0 and st.first_primary_lock_at == "2026-10-21T10:00:00+09:00"
    by = {e.sha256: e for e in st.entries}
    assert by[early.sha256].presealed is True and by[late.sha256].presealed is False
    assert all(e.run_id is None for e in st.entries)                    # 해제 전에는 run_id 도 모른다


# ============================================================================== 해제 조건 (06 7-1)
def test_unseal_blocked_until_planned_relabels_locked_and_bin_not_opened(proj, monkeypatch):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    opened = _spy_open(monkeypatch, os.path.normcase(os.sep + "sealed" + os.sep))
    c = seal.unseal_conditions(root)
    assert not c.met and any("잠기지 않은" in r for r in c.reasons)
    with pytest.raises(seal.SealBlockedError):
        seal.unseal(root, e.sha256, by="L01", now=NOW)
    _lock(root, SID_A, MID_1)
    c = seal.unseal_conditions(root)
    assert any("재라벨 계획이 없는" in r for r in c.reasons) and any(SID_B in r for r in c.reasons)
    p2 = _make_plan(root, "relabel_second", "L02", "aa01")
    _lock(root, SID_B, MID_1)
    ps = _make_plan(root, "relabel_same", "L01", "aa02")                 # 계획된 같은 사람 재라벨이 남음
    c = seal.unseal_conditions(root)
    assert not c.met and any(ps["plan_id"] in r for r in c.reasons)
    with pytest.raises(seal.SealBlockedError):
        seal.unseal(root, e.sha256, by="L01", now=NOW)
    assert opened == []
    relabel.cancel_plan(root, ps["plan_id"], reason="합성 시험 취소", cancelled_by="L01", cancelled_machine4="aaaa",
                        app_version="0.0.1", canceller_kind=None, now=NOW + timedelta(hours=2))
    _set_lock(root, SID_B, relabel_plan_id=p2["plan_id"])
    assert seal.unseal_conditions(root).met


def test_unseal_blocked_when_sample_verify_failed(proj):
    root = proj["root"]
    _ready(root)
    _set_lock(root, SID_A, sample_verify=[{"sample_set_id": "S-0000aaaa-d1-r1", "ok": False, "problems": 1}])
    c = seal.unseal_conditions(root)
    assert not c.met and any("재현 검증" in r for r in c.reasons)
    assert c.codes == ["sample_verify"] and c.unreadable_plans == []


def _torn_plan(root, plan_id):
    """중간에 잘린 계획 파일(찢어진 쓰기) — relabel.list_plans 가 '읽지 못함(unreadable)' 으로 돌려준다."""
    with open(relabel.plan_path(root, plan_id), "wb") as f:
        f.write(b'{"schema": "chatcoach/relabel-plan", "plan_id": "RP-2026')


def test_unseal_conditions_corrupt_plan_file_is_plan_unreadable(proj):
    """계획 파일이 깨져 막히면 사유 코드는 plan_unreadable(+ 계획 ID) — '잠긴 재라벨 세션이 없는 계획'(relabel_not_locked)이
    아니다(형식 검증 2026-10-23 에서 찾음: 해제 당일에 잠기지 않은 재라벨을 찾으러 가게 됐다). 막는 판정(met)은 그대로다."""
    from tools import pilot_unseal_day as day
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    p2 = _ready(root)
    assert seal.unseal_conditions(root).met
    bad = "RP-20261021-zz99"
    _torn_plan(root, bad)                                               # 남은(취소되지 않은) 계획 하나가 깨짐
    c = seal.unseal_conditions(root)
    assert not c.met and c.codes == ["plan_unreadable"] and c.unreadable_plans == [bad]
    assert len(c.reasons) == len(c.codes) and bad in c.reasons[0] and "잠긴 재라벨 세션이 없는" not in c.reasons[0]
    assert set(c.codes) <= set(seal.UNSEAL_REASON_CODES)
    with pytest.raises(seal.SealBlockedError) as ei:                     # 해제는 그대로 막힌다
        seal.unseal(root, e.sha256, by="L01")
    assert ei.value.reason_codes == ["plan_unreadable"] and ei.value.reasons == c.reasons
    got = day.check_conditions(root)                                    # 해제 당일 도구도 같은 코드
    assert got["reasons"] == [day.R_PLAN_UNREADABLE] and got["plans_unreadable"] == [bad] and not got["met"]
    assert [day.reason_code(r) for r in c.reasons] == [day.R_PLAN_UNREADABLE]   # 코드 없는 옛 결과도 같은 코드
    # 깨진 계획 옆에 정말로 잠기지 않은 계획이 있으면 둘 다 따로 적는다
    ps = _make_plan(root, "relabel_same", "L01", "aa02")
    c = seal.unseal_conditions(root)
    assert sorted(c.codes) == ["plan_unreadable", "relabel_not_locked"] and c.unreadable_plans == [bad]
    i = c.codes.index("relabel_not_locked")
    assert ps["plan_id"] in c.reasons[i] and bad not in c.reasons[i]
    relabel.cancel_plan(root, ps["plan_id"], reason="합성 시험 취소", cancelled_by="L01", cancelled_machine4="aaaa",
                        app_version="0.0.1", canceller_kind=None, now=NOW + timedelta(hours=2))
    # 잠긴 재라벨 세션(B)이 가리키는 계획 파일이 깨져도 plan_unreadable (예전 코드로는 'other')
    os.remove(relabel.plan_path(root, bad))
    _torn_plan(root, p2["plan_id"])
    c = seal.unseal_conditions(root)
    assert not c.met and "plan_unreadable" in c.codes and "other" not in c.codes
    assert c.unreadable_plans == [p2["plan_id"]] and len(c.codes) == len(c.reasons)
    assert day.check_conditions(root)["plans_unreadable"] == [p2["plan_id"]]


# ============================================================================== 해제
def test_unseal_writes_run_and_record_with_lock_hashes(proj):
    root = proj["root"]
    data = data_of(result_doc())
    e = seal.seal_bytes(root, data, source="engine_link", by="L01", now=NOW)        # 1차 잠금 전 → 사전 봉인
    with pytest.raises(seal.SealError, match="해제 기록"):
        seal.read_run(root, SECRET_RUN)
    _ready(root)
    r = seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4))
    assert r.conditions_met and r.presealed and r.run_id == SECRET_RUN
    assert os.path.basename(r.run_path) == f"{SECRET_RUN}.json"
    rec = r.record
    assert rec["kind"] == "ai.unsealed" and rec["reason"] is None and rec["unmet"] == []
    want = {}
    for sid in (SID_A, SID_B):
        with open(os.path.join(root, "locks", f"{sid}.json"), "rb") as f:
            want[sid] = hashlib.sha256(f.read()).hexdigest()
    assert {x["session_id"]: x["sha256"] for x in rec["locks"]} == want
    assert rec["dropped_duplicate"] == {"reactions": 1} and rec["dropped_unknown"] == {"reactions": 1}
    assert rec["warnings"] == [air.W_MESSAGES_NONE] and rec["calibration_rev"] is None
    run = seal.read_run(root, SECRET_RUN)
    assert [x["chat_id"] for x in run["reactions"]] == [cid(1)]
    assert air.check_doc(run, {"broadcast": {"video_no": "777"}}).ok
    st = seal.seal_status(root)
    assert st.sealed_count == 0 and st.unsealed_count == 1 and st.entries[0].run_id == SECRET_RUN
    with pytest.raises(seal.AlreadyUnsealedError):
        seal.unseal(root, e.sha256, by="L01")
    # 해제한 결과가 바뀌면 읽지 않는다
    with open(r.run_path, "ab") as f:
        f.write(b" ")
    with pytest.raises(seal.SealError, match="기록과 다릅니다"):
        seal.read_run(root, SECRET_RUN)


def test_unseal_early_needs_reason_and_records_conditions_not_met(proj):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    with pytest.raises(seal.SealError):
        seal.unseal(root, e.sha256, by="L01", early_reason="   ")
    r = seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험 사유")
    assert r.conditions_met is False and r.record["reason"] == "합성 시험 사유" and r.record["unmet"]


# ============================================================================== 재라벨 없이 해제 (08 D-26)
def _primary_only(proj):
    """합성 프로젝트에서 재라벨 세션 B 의 라벨 파일을 지워 1차 A 하나만 남긴다(D-26 의 '1편 · 1인 · 재라벨 없음' 모양)."""
    root = proj["root"]
    os.remove(os.path.join(root, "labels", f"{SID_B}.{project.machine4_of(MID_1)}.jsonl"))
    return root


def test_unseal_conditions_allow_no_relabel_waives_only_no_relabel_plan(proj):
    """플래그는 '계획 파일이 하나도 없을 때의 no_relabel_plan' 하나만 막지 않는다. 1차 없음·잠기지 않음·표본 검증 실패·
    깨진 계획·남은 재라벨은 플래그가 있어도 막는다. 플래그가 없으면 전과 같다."""
    root = _primary_only(proj)
    a_name = f"{SID_A}.{project.machine4_of(MID_1)}.jsonl"
    a_path = os.path.join(root, "labels", a_name)
    with open(a_path, "rb") as f:
        a_bytes = f.read()
    # 1차 없음: 플래그로 풀리지 않는다
    os.remove(a_path)
    for flag in (False, True):
        c = seal.unseal_conditions(root, allow_no_relabel=flag)
        assert not c.met and c.codes == ["no_primary"] and c.waived == [] and c.plan_files == 0
    with open(a_path, "wb") as f:
        f.write(a_bytes)
    # 1차가 잠기지 않음: 플래그가 있어도 unlocked_sessions 로 막는다(no_relabel_plan 만 빠진다)
    c0 = seal.unseal_conditions(root)
    assert not c0.met and c0.codes == ["unlocked_sessions", "no_relabel_plan"] and c0.waived == []
    c1 = seal.unseal_conditions(root, allow_no_relabel=True)
    assert not c1.met and c1.codes == ["unlocked_sessions"] and c1.waived == ["no_relabel_plan"]
    assert len(c1.reasons) == len(c1.codes) and SID_A in c1.reasons[0]
    # 1차 잠김 · 계획 없음: 플래그 없으면 전처럼 no_relabel_plan, 있으면 조건 충족
    _lock(root, SID_A, MID_1)
    c0 = seal.unseal_conditions(root)
    assert not c0.met and c0.codes == ["no_relabel_plan"] and c0.waived == [] and c0.plan_files == 0
    c1 = seal.unseal_conditions(root, allow_no_relabel=True)
    assert c1.met and c1.codes == [] and c1.reasons == [] and c1.waived == ["no_relabel_plan"] and c1.plan_files == 0
    assert c1.labeling_sessions == [SID_A]
    # 표본 재현 검증 실패는 플래그가 있어도 막는다
    _set_lock(root, SID_A, sample_verify=[{"sample_set_id": "S-0000aaaa-d1-r1", "ok": False, "problems": 1}])
    c1 = seal.unseal_conditions(root, allow_no_relabel=True)
    assert not c1.met and c1.codes == ["sample_verify"] and c1.waived == ["no_relabel_plan"]
    _set_lock(root, SID_A, sample_verify=[])
    # 깨진 계획 파일이 하나라도 있으면 '계획이 아예 없음' 이 아니다 — 아무것도 풀지 않는다
    bad = "RP-20261021-zz99"
    os.makedirs(os.path.dirname(relabel.plan_path(root, bad)), exist_ok=True)
    _torn_plan(root, bad)
    c1 = seal.unseal_conditions(root, allow_no_relabel=True)
    assert not c1.met and c1.waived == [] and c1.plan_files == 1
    assert sorted(c1.codes) == ["no_relabel_plan", "plan_unreadable"] and c1.unreadable_plans == [bad]
    os.remove(relabel.plan_path(root, bad))
    # 계획이 있는데 재라벨이 잠기지 않음(남은 재라벨)도 그대로 막는다
    ps = _make_plan(root, "relabel_same", "L01", "aa02")
    for flag in (False, True):
        c = seal.unseal_conditions(root, allow_no_relabel=flag)
        assert not c.met and c.codes == ["relabel_not_locked"] and c.waived == [] and c.plan_files == 1
        assert ps["plan_id"] in c.reasons[0]
    # 취소된 계획만 남아도 계획 파일은 있다 — 이때는 플래그 없이도 조건이 된다(전과 같음, 플래그가 바꾸지 않음)
    relabel.cancel_plan(root, ps["plan_id"], reason="합성 시험 취소", cancelled_by="L01", cancelled_machine4="aaaa",
                        app_version="0.0.1", canceller_kind=None, now=NOW + timedelta(hours=2))
    for flag in (False, True):
        c = seal.unseal_conditions(root, allow_no_relabel=flag)
        assert c.met and c.waived == [] and c.plan_files == 1


def test_unseal_allow_no_relabel_records_relabel_none_and_flagless_unchanged(proj):
    root = _primary_only(proj)
    e1 = seal.seal_bytes(root, data_of(result_doc("run-noflag-1")), source="engine_link", by="L01", now=NOW)
    e2 = seal.seal_bytes(root, data_of(result_doc("run-noflag-2")), source="engine_link", by="L01", now=NOW)
    # 1차가 잠기기 전: 플래그가 있어도 막는다(사유 코드에 no_relabel_plan 은 없음)
    with pytest.raises(seal.SealBlockedError) as ei:
        seal.unseal(root, e1.sha256, by="L01", allow_no_relabel=True)
    assert ei.value.reason_codes == ["unlocked_sessions"]
    _lock(root, SID_A, MID_1)
    # 플래그 없이는 전처럼 막는다 — 봉인 파일도 열지 않고 아무것도 쓰지 않는다
    sj = os.path.join(ai_dir(proj), "seal.jsonl")
    before = open(sj, "rb").read()
    with pytest.raises(seal.SealBlockedError) as ei:
        seal.unseal(root, e1.sha256, by="L01")
    assert ei.value.reason_codes == ["no_relabel_plan"] and open(sj, "rb").read() == before
    assert seal.seal_status(root).unsealed_count == 0
    # 플래그를 주면 풀리고 기록에 relabel = none · allow_no_relabel = true
    r = seal.unseal(root, e1.sha256, by="L01", now=NOW + timedelta(days=1), allow_no_relabel=True)
    rec = r.record
    assert r.conditions_met is True and rec["conditions_met"] is True and rec["reason"] is None and rec["unmet"] == []
    assert rec["allow_no_relabel"] is True and rec["relabel"] == "none"
    assert [x["session_id"] for x in rec["locks"]] == [SID_A] and rec["presealed"] is True
    line = json.loads(open(sj, encoding="utf-8").read().splitlines()[-1])
    assert line["kind"] == "ai.unsealed" and line["relabel"] == "none" and line["allow_no_relabel"] is True
    assert seal.read_run(root, "run-noflag-1")["run_id"] == "run-noflag-1"
    # 두 번째 봉인은 플래그 없이 여전히 막힌다(플래그는 한 번 준 것이 남지 않는다)
    with pytest.raises(seal.SealBlockedError):
        seal.unseal(root, e2.sha256, by="L01")


def test_unseal_flag_on_planned_project_records_planned_and_flagless_record_shape_unchanged(proj):
    root = proj["root"]
    e1 = seal.seal_bytes(root, data_of(result_doc("run-planned-1")), source="file", by="L01", now=NOW)
    e2 = seal.seal_bytes(root, data_of(result_doc("run-planned-2")), source="file", by="L01", now=NOW)
    _ready(root)
    r1 = seal.unseal(root, e1.sha256, by="L01", now=NOW + timedelta(days=4))
    assert "allow_no_relabel" not in r1.record and "relabel" not in r1.record        # 플래그 없음 = 기록 모양 그대로
    c = seal.unseal_conditions(root, allow_no_relabel=True)
    assert c.met and c.waived == [] and c.plan_files == 1                           # 계획이 있으면 플래그는 할 일이 없다
    r2 = seal.unseal(root, e2.sha256, by="L01", now=NOW + timedelta(days=4), allow_no_relabel=True)
    assert r2.record["allow_no_relabel"] is True and r2.record["relabel"] == "planned" and r2.conditions_met


def test_unseal_detects_tampered_bin_and_run_id_clash(proj):
    root = proj["root"]
    _ready(root)
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    binp = os.path.join(ai_dir(proj), "sealed", f"{e.sha256}.bin")
    with open(binp, "ab") as f:
        f.write(b" ")
    with pytest.raises(seal.SealError, match="기록과 다릅니다"):
        seal.unseal(root, e.sha256, by="L01")
    os.remove(binp)
    seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01")   # 원본을 다시 받으면 되살아난다
    seal.unseal(root, e.sha256, by="L01")
    other = seal.seal_bytes(root, data_of(result_doc(created_at="2026-10-23T10:00:00+09:00")), source="file", by="L01")
    with pytest.raises(seal.SealError, match="run_id"):
        seal.unseal(root, other.sha256, by="L01")
    with pytest.raises(seal.SealError, match="봉인 기록"):
        seal.unseal(root, "f" * 64, by="L01")


def test_only_unseal_opens_sealed_bin():
    """봉인 파일(.bin)을 읽는 코드는 unseal 하나다. 다른 공개 함수는 sealed\\ 를 쓰기(봉인)만 한다."""
    src = inspect.getsource(seal)
    assert src.count("builtins.open(binp") == 1
    assert "builtins.open(binp" in inspect.getsource(seal.unseal)
    for name in seal.__all__:
        obj = getattr(seal, name)
        if callable(obj) and inspect.isfunction(obj) and name != "unseal":
            assert "builtins.open(binp" not in inspect.getsource(obj)
    assert "SEALED_DIR" not in inspect.getsource(air)


# ============================================================================== 동시 해제 (seal.jsonl 잠금, 형식 검증 2026-10-23)
def _unsealed_lines(proj):
    return [r for r in _records(proj) if r["kind"] == "ai.unsealed"]


def test_concurrent_unseal_threads_write_one_record(proj, monkeypatch):
    """두 스레드가 같은 봉인을 함께 푼다. 둘 다 '아직 안 풂' 확인과 조건을 지난 뒤에야 쓰기 쪽으로 가게 막아(장벽) 경쟁을
    꼭 만든다. 잠금이 없으면 ai.unsealed 가 두 줄 생겼다. 이제 한 줄 · runs 파일 하나 · 늦은 쪽은 차례로 두 번 부른 것과 같은
    AlreadyUnsealedError 이고 봉인 파일도 열지 않는다."""
    import threading
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    _ready(root)
    real = seal.unseal_conditions
    gate = threading.Barrier(2, timeout=30)

    def cond_then_wait(*a, **k):
        c = real(*a, **k)
        gate.wait()                     # 두 스레드 모두 앞쪽 확인을 지났다
        return c
    monkeypatch.setattr(seal, "unseal_conditions", cond_then_wait)
    bins = _spy_open(monkeypatch, os.path.normcase(os.sep + "sealed" + os.sep))
    done, errs = [], []

    def go():
        try:
            done.append(seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4)))
        except Exception as x:          # noqa: BLE001 — 무엇이 나오든 아래에서 본다
            errs.append(x)
    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert len(done) == 1 and len(errs) == 1, errs
    assert type(errs[0]) is seal.AlreadyUnsealedError and errs[0].code == "already_unsealed"
    assert str(errs[0]) == "이미 해제한 봉인입니다"
    assert len(_unsealed_lines(proj)) == 1
    assert os.listdir(os.path.join(ai_dir(proj), "runs")) == [f"{SECRET_RUN}.json"]
    assert len(bins) == 1                                   # 봉인 파일은 이긴 쪽만 한 번 열었다
    assert not os.path.exists(os.path.join(ai_dir(proj), seal.SEAL_LOCK_FILE))
    monkeypatch.setattr(seal, "unseal_conditions", real)
    with pytest.raises(seal.AlreadyUnsealedError):           # 차례로 한 번 더: 같은 예외
        seal.unseal(root, e.sha256, by="L01")
    assert len(_unsealed_lines(proj)) == 1


_RACE_CHILD = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from app import seal
root, sha, gate_dir = sys.argv[2:5]
real = seal.unseal_conditions


def cond_then_wait(*a, **k):
    c = real(*a, **k)
    open(os.path.join(gate_dir, "ready-%d" % os.getpid()), "w").close()
    deadline = time.time() + 30
    while len(os.listdir(gate_dir)) < 2 and time.time() < deadline:      # 두 프로세스가 모두 앞쪽 확인을 지날 때까지
        time.sleep(0.01)
    return c


seal.unseal_conditions = cond_then_wait
try:
    r = seal.unseal(root, sha, by="L01")
    print(json.dumps({"ok": True, "run_id": r.run_id}))
except seal.SealError as e:
    print(json.dumps({"ok": False, "code": seal.error_code(e), "type": type(e).__name__}))
'''


def test_concurrent_unseal_two_processes_write_one_record(proj, tmp_path):
    """앱과 tools/ai_seal.py(또는 pilot_unseal_day --go)처럼 **두 프로세스**가 같은 봉인을 함께 푼다(둘 다 앞쪽 확인을 지난
    뒤에 쓰기로 가게 파일로 맞춘다). ai.unsealed 한 줄 · runs\\<run_id>.json 하나 · 늦은 쪽은 already_unsealed."""
    import subprocess
    import sys
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    _ready(root)
    script = tmp_path / "race_child.py"
    script.write_text(_RACE_CHILD, encoding="utf-8")
    gate = tmp_path / "gate"
    gate.mkdir()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    ps = [subprocess.Popen([sys.executable, str(script), repo, root, e.sha256, str(gate)], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env, cwd=str(tmp_path)) for _ in range(2)]
    outs = []
    for p in ps:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode("utf-8", "replace")[-2000:]
        outs.append(json.loads(out.decode("utf-8").strip().splitlines()[-1]))
    assert sorted(o["ok"] for o in outs) == [False, True], outs
    lost = next(o for o in outs if not o["ok"])
    assert lost == {"ok": False, "code": "already_unsealed", "type": "AlreadyUnsealedError"}
    assert len(_unsealed_lines(proj)) == 1
    assert os.listdir(os.path.join(ai_dir(proj), "runs")) == [f"{SECRET_RUN}.json"]
    assert not os.path.exists(os.path.join(ai_dir(proj), seal.SEAL_LOCK_FILE))
    assert seal.read_run(root, SECRET_RUN)["run_id"] == SECRET_RUN


def test_seal_writers_wait_for_held_lock_and_break_stale_lock(proj, monkeypatch):
    """seal.jsonl 에 쓰는 곳(unseal·attach_pairs·짝 봉인·seal_bytes)은 모두 같은 잠금을 기다린다. 다른 프로세스가 쥔(새)
    잠금이면 기다리다 seal_busy 로 멈추고 아무것도 쓰지 않으며 남의 잠금은 건드리지 않는다. 읽기(seal_status)는 기다리지 않는다.
    오래된 잠금(비정상 종료로 남음)은 지우고 잡는다."""
    import time
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    _ready(root)
    lock = os.path.join(ai_dir(proj), seal.SEAL_LOCK_FILE)
    with open(lock, "wb") as f:
        f.write(b"other-process\n")
    monkeypatch.setattr(seal, "SEAL_LOCK_TIMEOUT_S", 0.3)
    n0 = len(_records(proj))
    calls = {"unseal": lambda: seal.unseal(root, e.sha256, by="L01"),
             "attach_pairs": lambda: seal.attach_pairs(root, e.sha256, _pairs(root), by="L01"),
             "seal_topics_bytes": lambda: seal.seal_topics_bytes(root, data_of(topic_doc()), pair_sha256=e.sha256,
                                                                 by="L01"),
             "seal_bytes": lambda: seal.seal_bytes(root, data_of(result_doc("run-other")), source="file", by="L01")}
    for name, fn in calls.items():
        with pytest.raises(seal.SealError) as ei:
            fn()
        assert ei.value.code == "seal_busy", name
    assert "seal_busy" in seal.SEAL_ERROR_CODES
    assert len(_records(proj)) == n0 and not os.path.isdir(os.path.join(ai_dir(proj), "runs"))
    assert len(os.listdir(os.path.join(ai_dir(proj), "sealed"))) == 1           # 새 봉인 원본도 쓰지 않았다
    assert open(lock, "rb").read() == b"other-process\n"
    assert seal.seal_status(root).sealed_count == 1                             # 읽기는 기다리지 않는다
    old = time.time() - seal.SEAL_LOCK_STALE_S - 60
    os.utime(lock, (old, old))
    r = seal.attach_pairs(root, e.sha256, _pairs(root), by="L01", now=NOW + timedelta(days=1))
    assert r.ok and r.attached == ["topics", "words", "vectors"]
    assert seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4)).conditions_met
    assert not os.path.exists(lock) and len(_unsealed_lines(proj)) == 1


# ============================================================================== 명령줄
def test_cli_seal_status_check_unseal(proj, tmp_path, capsys):
    root = proj["root"]
    f = tmp_path / "incoming.json"
    f.write_bytes(data_of(result_doc()))
    assert ai_seal.main(["seal", "--project", root, "--file", str(f), "--by", "L01", "--remove-source"]) == 0
    out = capsys.readouterr().out
    line = json.loads(out)
    assert line["check"] == "valid" and line["source_removed"] and not f.exists()
    assert SECRET_RUN not in out and SECRET_ENGINE not in out
    assert ai_seal.main(["status", "--project", root]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["sealed_count"] == 1 and st["entries"][0]["sha256_12"] == line["sealed"][:12]
    assert ai_seal.main(["check", "--project", root]) == 1
    assert json.loads(capsys.readouterr().out)["conditions_met"] is False
    assert ai_seal.main(["unseal", "--project", root, "--sha256", line["sealed"], "--by", "L01"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "SealBlockedError"
    _ready(root)
    assert ai_seal.main(["unseal", "--project", root, "--sha256", line["sealed"], "--by", "L01"]) == 0
    u = json.loads(capsys.readouterr().out)
    assert u["run_id"] == SECRET_RUN and u["conditions_met"] is True and u["locks"] == 2


# ============================================================================== 시각별 전사 낱말 짝 봉인 (22 5-2 의 7)
SECRET_WORD = "합성표지낱말"          # 합성 낱말 — 기록·출력·오류에 나오면 안 된다


def words_doc(project_id=None, video_no="777", words=("보스", SECRET_WORD), **kw):
    d = {"schema": "chatcoach/transcript-words", "schema_version": 1, "run_id": "words-synth-1",
         "created_at": "2026-10-23T10:00:00+09:00",
         "engine": {"name": "transcript_words", "version": "0.1.0", "config_sha256": None, "textnorm": "textnorm-v1"},
         "target": {"project_id": project_id, "video_no": video_no, "asr_sha256": "a" * 64},
         "time_axis": "vod_ms",
         "calibration": {"source": "project.json", "rev": 0, "method": "single_offset", "status": "verified",
                         "offset_ms": 0, "file_start_in_vod_ms": 0},
         "window_ms": 10_000, "range": {"start_ms": 0, "end_ms": 600_000},
         "windows": [{"start_ms": 100_000, "end_ms": 110_000, "words": sorted(words), "segments": 1}]}
    d.update(kw)
    return d


def _pid(root):
    from app.project import open_project
    return open_project(root).project_id


def test_seal_words_pairs_and_unseals_with_ai_result(proj, monkeypatch):
    root = proj["root"]
    wdata = data_of(words_doc(project_id=_pid(root)))
    with pytest.raises(seal.SealError, match="짝"):                 # 짝 판정 봉인이 없으면 받지 않는다
        seal.seal_words_bytes(root, wdata, pair_sha256="0" * 64, by="L01")
    e = seal.seal_bytes(root, data_of(result_doc()), source="engine_link", by="L01", now=NOW)
    we = seal.seal_words_bytes(root, wdata, pair_sha256=e.sha256, by="L01", source="engine_words", now=NOW)
    assert we.new and we.check == "valid" and we.reject_reasons == [] and we.sha256 == hashlib.sha256(wdata).hexdigest()
    assert we.run_id is None and we.unsealed_at is None
    assert seal.seal_words_bytes(root, wdata, pair_sha256=e.sha256, by="L01").new is False   # 같은 바이트는 한 번
    st = seal.seal_status(root)
    assert st.sealed_count == 1 and st.entries[0].words_sha256 == we.sha256 and st.entries[0].topics_sha256 is None
    d = ai_dir(proj)
    raw = open(os.path.join(d, "seal.jsonl"), encoding="utf-8").read()
    recs = [json.loads(x) for x in raw.splitlines()]
    assert [r["kind"] for r in recs] == ["ai.sealed", "words.sealed"]
    assert set(recs[1]) == {"v", "seq", "kind", "at", "by", "app_version", "sha256", "size", "source", "pair",
                            "check", "reject_reasons"}
    assert recs[1]["pair"] == e.sha256 and recs[1]["source"] == "engine_words"
    assert SECRET_WORD not in raw and "보스" not in raw                     # 낱말을 기록에 남기지 않는다
    # 해제 전에는 읽지 않는다, 라벨링 세션은 받지도 읽지도 못한다
    with pytest.raises(seal.SealError, match="해제 기록"):
        seal.read_words(root, SECRET_RUN, reader_kind="analysis")
    for kind in ("primary", "relabel_second", "origin_review"):
        with pytest.raises(seal.SealBlindError):
            seal.seal_words_bytes(root, wdata, pair_sha256=e.sha256, by="L01", reader_kind=kind)
        with pytest.raises(seal.SealBlindError):
            seal.read_words(root, SECRET_RUN, reader_kind=kind)
    # 판정을 풀면 같이 풀린다
    _ready(root)
    res = seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4))
    assert res.record["words"]["sha256"] == we.sha256 and res.record["words_error"] is None
    assert "topics" not in res.record                                      # 주제 흐름 짝이 없으면 그 칸은 없다
    doc = seal.read_words(root, res.run_id, reader_kind="analysis")
    assert doc == json.loads(wdata)
    assert os.path.isfile(os.path.join(d, seal.WORDS_DIR, f"{res.run_id}.json"))
    # 풀린 결과에는 나중에 붙이지 않는다
    with pytest.raises(seal.SealError, match="이미 해제"):
        seal.seal_words_bytes(root, data_of(words_doc(project_id=_pid(root), words=("공략",))),
                              pair_sha256=e.sha256, by="L01")
    # 해제한 파일이 바뀌면 읽지 않는다
    with open(os.path.join(d, seal.WORDS_DIR, f"{res.run_id}.json"), "ab") as f:
        f.write(b" ")
    with pytest.raises(seal.SealError, match="기록과 다릅니다"):
        seal.read_words(root, res.run_id)
    assert not os.path.exists(os.path.join(root, "ai"))                    # 프로젝트 폴더에는 아무것도 없다


def test_seal_words_rejects_with_codes_only(proj):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    bad_fmt = seal.seal_words_bytes(root, data_of(words_doc(window_ms=0)), pair_sha256=e.sha256, by="L01")
    assert bad_fmt.check == "rejected" and bad_fmt.reject_reasons == ["words_format"]
    bad_json = seal.seal_words_bytes(root, b'{"x": NaN}', pair_sha256=e.sha256, by="L01")
    assert bad_json.reject_reasons == ["words_json"]
    other_video = seal.seal_words_bytes(root, data_of(words_doc(video_no="999")), pair_sha256=e.sha256, by="L01")
    assert other_video.reject_reasons == ["words_video_mismatch"]
    other_proj = seal.seal_words_bytes(root, data_of(words_doc(project_id="0" * 8 + "-0000-0000-0000-" + "0" * 12)),
                                       pair_sha256=e.sha256, by="L01")
    assert other_proj.reject_reasons == ["words_project_mismatch"]
    for x in (bad_fmt, other_video, other_proj):
        assert SECRET_WORD not in json.dumps(x.reject_reasons, ensure_ascii=False)
    assert seal.seal_status(root).entries[0].words_sha256 is None           # 거부된 것은 짝으로 치지 않는다
    with pytest.raises(seal.SealError, match="출처"):
        seal.seal_words_bytes(root, b"{}", pair_sha256=e.sha256, by="L01", source="engine_topics")
    with pytest.raises(seal.SealError, match="sha256"):
        seal.seal_words_bytes(root, b"{}", pair_sha256="xyz", by="L01")
    _ready(root)
    res = seal.unseal(root, e.sha256, by="L01")
    assert "words" not in res.record and seal.read_words(root, res.run_id) is None


def test_seal_words_file_and_oversize(proj, tmp_path, monkeypatch):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    f = tmp_path / "w.json"
    f.write_bytes(data_of(words_doc()))
    we = seal.seal_words_file(root, str(f), pair_sha256=e.sha256, by="L01")
    assert we.source == "file" and we.check == "valid" and f.exists()    # 원본을 지우는 것은 부르는 쪽 몫
    monkeypatch.setattr(seal, "WORDS_MAX_BYTES", 10)
    with pytest.raises(seal.SealError, match="상한"):
        seal.seal_words_bytes(root, b"x" * 11, pair_sha256=e.sha256, by="L01")


def test_seal_words_unseal_problem_does_not_block_ai_result(proj):
    """짝 전사 낱말 .bin 이 깨졌어도 판정 해제는 된다 — 코드만 남기고 낱말은 풀지 않는다."""
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    we = seal.seal_words_bytes(root, data_of(words_doc()), pair_sha256=e.sha256, by="L01")
    with open(os.path.join(ai_dir(proj), "sealed", f"{we.sha256}.bin"), "ab") as f:
        f.write(b" ")
    _ready(root)
    res = seal.unseal(root, e.sha256, by="L01")
    assert res.record["words"] is None and res.record["words_error"] == "words_bin_mismatch"
    assert seal.read_words(root, res.run_id) is None
    assert not os.path.exists(os.path.join(ai_dir(proj), seal.WORDS_DIR, f"{res.run_id}.json"))


def test_engine_to_ai_finds_words_sealer():
    """tools/engine_to_ai --words --seal 이 찾는 이름(WORDS_SEAL_FN)이 이 함수다 — 엔진 쪽은 고칠 것이 없다."""
    from tools import engine_to_ai as e2a
    assert e2a._words_sealer() is seal.seal_words_bytes
    sig = inspect.signature(seal.seal_words_bytes)
    assert list(sig.parameters)[:2] == ["project_root", "data"]
    for k in ("pair_sha256", "by", "source", "ai_root"):
        assert sig.parameters[k].kind is inspect.Parameter.KEYWORD_ONLY


# ============================================================================== 창별 주제 벡터 짝 봉인 (23 5-2 의 2)
def _vhash(windows):
    rows = [[w["start_ms"], w["end_ms"], w["v"]] for w in windows]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def vectors_doc(project_id=None, video_no="777", **kw):
    """합성 topic-vectors(round4, 4차원 단위 벡터 2창). 창 글·낱말은 없다(엔진 문서와 같다)."""
    wins = [{"start_ms": 100_000, "end_ms": 110_000, "v": [1.0, 0.0, 0.0, 0.0]},
            {"start_ms": 110_000, "end_ms": 120_000, "v": [0.0, 0.6, 0.8, 0.0]}]
    d = {"schema": "chatcoach/topic-vectors", "schema_version": 1, "run_id": "tvec-synth-1",
         "created_at": "2026-10-23T10:00:00+09:00",
         "engine": {"name": "chatcoach-topic-vectors", "version": "0.1.0", "config_sha256": None},
         "model": {"backend": "fake", "name": "fake-embedder", "repo": None, "revision": None, "model_sha256": None,
                   "dim": 4, "prefix": None, "pooling": None},
         "target": {"project_id": project_id, "video_no": video_no, "asr_sha256": "a" * 64},
         "time_axis": "vod_ms",
         "calibration": {"source": "project.json", "rev": 0, "method": "single_offset", "status": "verified",
                         "offset_ms": 0, "file_start_in_vod_ms": 0},
         "window_ms": 10_000, "span_ms": 10_000, "encoding": "round4", "dim": 4,
         "range": {"start_ms": 0, "end_ms": 600_000}, "stats": {"windows": 2},
         "vectors_sha256": _vhash(wins), "windows": wins}
    d.update(kw)
    return d


def test_vectors_doc_matches_engine_rules():
    """합성 벡터 문서가 엔진 검사(engine.topic_vectors.validate)도 통과하고, 벡터 해시 식이 엔진과 같다."""
    TV = pytest.importorskip("engine.topic_vectors")
    d = vectors_doc(project_id=PID)
    assert TV.validate(d) == []
    assert seal._vectors_hash(d["windows"]) == TV.vectors_sha256(d["windows"]) == d["vectors_sha256"]
    assert tuple(TV.TOP_KEYS) == seal.VECTORS_TOP_KEYS and tuple(TV.WINDOW_KEYS) == seal.VECTORS_WINDOW_KEYS
    assert seal.vectors_check(data_of(d), {"project_id": PID, "broadcast": {"video_no": "777"}}) == ("valid", [])


def test_seal_vectors_pairs_and_unseals_with_ai_result(proj):
    root = proj["root"]
    vdata = data_of(vectors_doc(project_id=_pid(root)))
    with pytest.raises(seal.SealError, match="짝"):
        seal.seal_vectors_bytes(root, vdata, pair_sha256="0" * 64, by="L01")
    e = seal.seal_bytes(root, data_of(result_doc()), source="engine_link", by="L01", now=NOW)
    ve = seal.seal_vectors_bytes(root, vdata, pair_sha256=e.sha256, by="L01", source="engine_vectors", now=NOW)
    assert ve.new and ve.check == "valid" and ve.sha256 == hashlib.sha256(vdata).hexdigest() and ve.run_id is None
    assert seal.seal_vectors_bytes(root, vdata, pair_sha256=e.sha256, by="L01").new is False
    st = seal.seal_status(root)
    assert st.sealed_count == 1 and st.entries[0].vectors_sha256 == ve.sha256 and st.entries[0].words_sha256 is None
    d = ai_dir(proj)
    raw = open(os.path.join(d, "seal.jsonl"), encoding="utf-8").read()
    recs = [json.loads(x) for x in raw.splitlines()]
    assert [r["kind"] for r in recs] == ["ai.sealed", "vectors.sealed"]
    assert set(recs[1]) == {"v", "seq", "kind", "at", "by", "app_version", "sha256", "size", "source", "pair",
                            "check", "reject_reasons"}
    assert recs[1]["source"] == "engine_vectors" and "0.6" not in raw         # 벡터 수를 기록에 남기지 않는다
    with pytest.raises(seal.SealNotUnsealedError):
        seal.read_vectors(root, SECRET_RUN, reader_kind="analysis")
    _ready(root)
    res = seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4))
    assert res.record["vectors"]["sha256"] == ve.sha256 and res.record["vectors_error"] is None
    assert "words" not in res.record and "topics" not in res.record
    assert [p["kind"] for p in res.record["pairs"]] == ["vectors"] and res.record["pairs"][0]["error"] is None
    doc = seal.read_vectors(root, res.run_id, reader_kind="analysis")
    assert doc == json.loads(vdata) and len(VS.vectors_from_doc(doc)) == 2
    with pytest.raises(seal.SealError, match="이미 해제"):
        seal.seal_vectors_bytes(root, data_of(vectors_doc(project_id=_pid(root), run_id="tvec-synth-2")),
                                pair_sha256=e.sha256, by="L01")
    with open(os.path.join(d, seal.VECTORS_DIR, f"{res.run_id}.json"), "ab") as f:
        f.write(b" ")
    with pytest.raises(seal.SealError, match="기록과 다릅니다"):
        seal.read_vectors(root, res.run_id)
    assert not os.path.exists(os.path.join(root, "ai"))


def test_seal_vectors_rejects_with_codes_only(proj, tmp_path, monkeypatch):
    root = proj["root"]
    pid = _pid(root)
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    bad_hash = vectors_doc(project_id=pid, vectors_sha256="b" * 64)
    extra_key = {**vectors_doc(project_id=pid), "text": "합성 창 글"}            # 창 글이 끼어들면 거부
    bad_row = vectors_doc(project_id=pid)
    bad_row["windows"][1]["start_ms"] = 105_000
    bad_vec = vectors_doc(project_id=pid)
    bad_vec["windows"][0]["v"] = [1.0, 0.0]                                    # 차원이 다름
    bad_vec["vectors_sha256"] = _vhash(bad_vec["windows"])
    cases = [(bad_hash, "vectors_format"), (extra_key, "vectors_format"), (bad_row, "vectors_format"),
             (bad_vec, "vectors_format"), (vectors_doc(project_id=pid, window_ms=0), "vectors_format"),
             (vectors_doc(project_id=pid, video_no="999"), "vectors_video_mismatch"),
             (vectors_doc(project_id="0" * 8 + "-0000-0000-0000-" + "0" * 12), "vectors_project_mismatch")]
    for doc, code in cases:
        x = seal.seal_vectors_bytes(root, data_of(doc), pair_sha256=e.sha256, by="L01")
        assert x.check == "rejected" and x.reject_reasons == [code], code
    assert seal.seal_vectors_bytes(root, b'{"x": NaN}', pair_sha256=e.sha256, by="L01").reject_reasons == \
        ["vectors_json"]
    raw = open(os.path.join(ai_dir(proj), "seal.jsonl"), encoding="utf-8").read()
    assert "합성 창 글" not in raw
    assert seal.seal_status(root).entries[0].vectors_sha256 is None           # 거부된 것은 짝으로 치지 않는다
    with pytest.raises(seal.SealError, match="출처"):
        seal.seal_vectors_bytes(root, b"{}", pair_sha256=e.sha256, by="L01", source="engine_words")
    f = tmp_path / "v.json"
    f.write_bytes(data_of(vectors_doc(project_id=pid)))
    vf = seal.seal_vectors_file(root, str(f), pair_sha256=e.sha256, by="L01")
    assert vf.source == "file" and vf.check == "valid" and f.exists()
    monkeypatch.setattr(seal, "VECTORS_MAX_BYTES", 10)
    with pytest.raises(seal.SealError, match="상한"):
        seal.seal_vectors_bytes(root, b"x" * 11, pair_sha256=e.sha256, by="L01")


def test_engine_to_ai_finds_vectors_sealer():
    """tools/engine_to_ai --vectors --seal 이 찾는 이름(VECTORS_SEAL_FN)이 이 함수다(words 와 같은 인자)."""
    assert e2a._vectors_sealer() is seal.seal_vectors_bytes
    sig = inspect.signature(seal.seal_vectors_bytes)
    assert list(sig.parameters)[:2] == ["project_root", "data"]
    assert list(sig.parameters) == list(inspect.signature(seal.seal_words_bytes).parameters)
    for k in ("pair_sha256", "by", "source", "ai_root"):
        assert sig.parameters[k].kind is inspect.Parameter.KEYWORD_ONLY


# ============================================================================== 해제 기록의 엔진 판·짝 목록 (23 5-2 의 10)
def test_unseal_records_engine_version_and_pairs_and_old_records_still_read(proj):
    root = proj["root"]
    pid = _pid(root)
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    we = seal.seal_words_bytes(root, data_of(words_doc(project_id=pid)), pair_sha256=e.sha256, by="L01")
    ve = seal.seal_vectors_bytes(root, data_of(vectors_doc(project_id=pid)), pair_sha256=e.sha256, by="L01")
    with open(os.path.join(ai_dir(proj), "sealed", f"{ve.sha256}.bin"), "ab") as f:
        f.write(b" ")                                                          # 벡터 .bin 만 깨짐
    _ready(root)
    res = seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4))
    rec = res.record
    assert rec["engine_version"] == "0.0.1"
    assert rec["pairs"] == [
        {"kind": "words", "sha256": we.sha256, "file_sha256": rec["words"]["file_sha256"], "error": None},
        {"kind": "vectors", "sha256": ve.sha256, "file_sha256": None, "error": "vectors_bin_mismatch"}]
    assert rec["vectors"] is None and rec["vectors_error"] == "vectors_bin_mismatch"
    assert seal.read_vectors(root, res.run_id) is None and seal.read_words(root, res.run_id) is not None
    st = seal.seal_status(root)
    assert st.entries[0].engine_version == "0.0.1" and st.entries[0].vectors_sha256 == ve.sha256
    ev, _ = seal.unseal_events(root)
    assert ev[0]["engine_version"] == "0.0.1" and ev[0]["run_id"] == res.run_id
    # 옛 기록(engine_version·pairs·vectors 칸 없음)도 그대로 읽힌다 — seal.jsonl 하위 호환
    p = os.path.join(ai_dir(proj), "seal.jsonl")
    lines = [json.loads(x) for x in open(p, encoding="utf-8").read().splitlines()]
    for r in lines:
        if r["kind"] == "ai.unsealed":
            for k in ("engine_version", "pairs", "vectors", "vectors_error"):
                r.pop(k)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines))
    st = seal.seal_status(root)
    assert st.problems == [] and st.entries[0].engine_version is None and st.entries[0].run_id == res.run_id
    assert seal.unseal_events(root)[0][0]["engine_version"] is None
    assert seal.read_vectors(root, res.run_id) is None
    assert seal.read_words(root, res.run_id) == words_doc(project_id=pid)
    assert seal.read_run(root, res.run_id)["run_id"] == SECRET_RUN


@pytest.mark.parametrize("reader", [None, "adjudication", "analysis"])
def test_every_reader_is_blind_before_unseal(proj, monkeypatch, reader):
    """해제 전: 네 읽기 함수 모두 SealNotUnsealedError(SealBlindError) — runs·topics·words·vectors·sealed 를 열지 않는다."""
    root = proj["root"]
    pid = _pid(root)
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    seal.seal_words_bytes(root, data_of(words_doc(project_id=pid)), pair_sha256=e.sha256, by="L01")
    seal.seal_vectors_bytes(root, data_of(vectors_doc(project_id=pid)), pair_sha256=e.sha256, by="L01")
    opened = []
    real = builtins.open

    def spy(path, *a, **kw):
        p = os.path.normcase(os.path.abspath(os.fspath(path)))
        if any(os.sep + x + os.sep in p for x in ("runs", "topics", "words", "vectors", "sealed")):
            opened.append(p)
        return real(path, *a, **kw)
    monkeypatch.setattr(builtins, "open", spy)
    for fn in (seal.read_run, seal.read_topics, seal.read_words, seal.read_vectors):
        with pytest.raises(seal.SealBlindError) as ei:
            fn(root, SECRET_RUN, reader_kind=reader)
        assert isinstance(ei.value, seal.SealNotUnsealedError) and "해제 기록" in str(ei.value)
    assert opened == []


def test_cli_status_shows_pairs_and_verify_empty(proj, capsys):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    seal.seal_vectors_bytes(root, data_of(vectors_doc(project_id=_pid(root))), pair_sha256=e.sha256, by="L01")
    assert ai_seal.main(["status", "--project", root]) == 0
    row = json.loads(capsys.readouterr().out)["entries"][0]
    assert row["pairs"] == {"topics": False, "words": False, "vectors": True} and row["engine_version"] is None
    assert ai_seal.main(["verify", "--project", root]) == 0                     # 해제된 run 이 없으면 빈 목록
    assert json.loads(capsys.readouterr().out) == {"runs": [], "ok": True}
    with pytest.raises(SystemExit):                                             # --sha256 과 --all 은 하나만
        ai_seal.main(["unseal", "--project", root, "--by", "L01"])
    capsys.readouterr()


# ============================================================================== 해제 당일 리허설 (D-22 2, 합성 시범 프로젝트)
TOPIC_SECRET = "합성주제표지"


def topic_doc(video_no="777"):
    """합성 주제 흐름(app.core.topics 형식, tests/test_topics_integration.topic_doc 과 같은 모양)."""
    from app.core import topics as T
    return {
        "schema": T.SCHEMA, "schema_version": T.SCHEMA_VERSION, "run_id": "topics-synth-1",
        "created_at": "2026-10-23T08:30:00+09:00",
        "engine": {"name": "synthetic-topics", "version": "0.0.1", "config_sha256": None, "embedding": None},
        "target": {"video_no": video_no, "messages_sha256": None, "asr_sha256": None},
        "time_axis": T.TIME_AXIS, "window_ms": 20_000, "range": {"start_ms": 100_000, "end_ms": 200_000},
        "stats": {"windows": 5, "blocks": 2},
        "blocks": [
            {"index": 0, "start_ms": 100_000, "end_ms": 140_000, "keywords": [TOPIC_SECRET, "보스"],
             "name_keywords": [TOPIC_SECRET], "top_keyword": TOPIC_SECRET, "change_ms": None, "boundary_score": None,
             "utterances": 4, "chats": 3},
            {"index": 1, "start_ms": 140_000, "end_ms": 200_000, "keywords": ["요리", "라면"],
             "name_keywords": ["요리", "라면"], "top_keyword": "요리", "change_ms": 140_000, "boundary_score": 0.4,
             "utterances": 5, "chats": 3},
        ],
        "gaps": [{"at_ms": 140_000, "similarity": 0.1, "depth": 0.4}],
    }


PILOT_RUN = "link-v03-synth-pilot"


def engine_doc():
    """engine.link 결과 2판 초안 모양(합성, v0.3 판)."""
    return {"schema": "chatcoach/ai-result", "schema_version": 2, "run_id": PILOT_RUN,
            "created_at": "2026-10-23T10:00:00+09:00",
            "engine": {"name": SECRET_ENGINE, "version": e2a.V03_VERSION, "preregistered": True,
                       "components": {"vad": None, "asr": "synth", "ocr": None, "other": None}},
            "target": {"platform": "chzzk", "video_no": "777", "chat_id_algorithm": "sl-chat-id-v1"},
            "time_axis": "vod_ms", "media": None,
            "semantic": {"active": True, "status": "ok", "fallback": None},
            "reactions": [{"chat_id": cid(1), "result": "graded", "grade": "direct", "reaction_time_ms": 3000,
                           "utterance_start_ms": 3000, "utterance_end_ms": 4000, "score": 0.9}]}


@pytest.fixture
def pilot(tmp_path):
    """합성 시범 프로젝트: 폴더 이름 pilot-777(시범 이름 규칙), 1차·두 번째 사람 재라벨 세션(tests.test_agreement 장면)."""
    base = tmp_path / "chatcoach-data" / "projects"
    p = project.create(str(base), "pilot", 777, "시험용", "L01", "0.0.1", NOW, PID, SALT,
                       guide=project.GuideSource("0.1", b"# guide\n", {"segment": {"boundary_buffer_sec": 10}}),
                       machine4="m3fa")
    root = p.root
    jsonl.write_all(os.path.join(root, "chat", "messages.jsonl"), [{"chat_id": cid(1), "t_ms": 1000, "text": "합성"}])
    ra, rb = _grade_pairs()
    _write_session(root, SID_A, MID_1, _label_lines(SID_A, MID_1, A_SEG, ra, kind="primary", start=0))
    _write_session(root, SID_B, MID_1, _label_lines(SID_B, MID_1, B_SEG, rb, kind="relabel_second", start=5000))
    return {"root": root, "tmp": tmp_path}


def _tree(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            st = os.stat(p)
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return out


def _last_json(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_pilot_unseal_day_rehearsal(pilot, monkeypatch, capsys):
    """해제 당일 리허설(합성 시범): engine_to_ai --topics --words --vectors --seal → 조건 미충족 거부(읽기도 블라인드)
    → 세션 잠금 → ai_seal unseal --all → verify → 네 파일(판정·주제 흐름·전사 낱말·주제 벡터)이 모두 읽힌다.
    사람 클릭 0: 모든 단계가 명령(도구)이다. 시범 프로젝트 폴더에는 아무것도 쓰지 않는다."""
    root = pilot["root"]
    assert os.path.basename(root) == "pilot-777" and e2a._is_pilot(root)
    pid = _pid(root)
    tdoc, wdoc, vdoc = topic_doc(), words_doc(project_id=pid), vectors_doc(project_id=pid)

    def fake_engine(project_dir, ns, topics_out=None):
        assert ns.engine == "v0.3" and ns.seal
        if topics_out is not None:
            topics_out["doc"] = tdoc
        return engine_doc()
    monkeypatch.setattr(e2a, "_run_engine", fake_engine)
    monkeypatch.setattr(e2a, "_run_words", lambda project_dir, ns: wdoc)
    monkeypatch.setattr(e2a, "_run_vectors", lambda project_dir, ns: vdoc)
    before = _tree(root)

    # 1. 봉인 — 시범도 봉인 경로로는 만든다. 짝 셋이 거부 없이 붙는다
    rc = e2a.main(["--from-project", root, "--seal", "--by", "L01", "--engine", "v0.3",
                   "--topics", "--words", "--vectors"])
    raw_out = capsys.readouterr().out
    out = json.loads(raw_out.strip().splitlines()[-1])
    assert rc == 0, out
    assert out["check"] == out["topics_check"] == out["words_check"] == out["vectors_check"] == "valid"
    assert out["engine_version"] == e2a.V03_VERSION and out["engine_fallback"] is False
    for secret in (SECRET_WORD, TOPIC_SECRET, PILOT_RUN, SECRET_ENGINE, "0.6"):
        assert secret not in raw_out                                         # 내용은 찍지 않는다
    assert _tree(root) == before                                             # 시범 폴더는 그대로
    st = seal.seal_status(root)
    assert st.sealed_count == 1
    ent = st.entries[0]
    assert (ent.topics_sha256, ent.words_sha256, ent.vectors_sha256) == \
        (out["topics_sealed"], out["words_sealed"], out["vectors_sealed"])

    # 2. 조건 미충족: check·unseal --all 거부, 아무것도 풀리지 않는다. 읽기 함수는 모두 블라인드
    assert ai_seal.main(["check", "--project", root]) == 1
    assert _last_json(capsys)["conditions_met"] is False
    assert ai_seal.main(["unseal", "--project", root, "--all", "--by", "L01"]) == 1
    assert _last_json(capsys)["error"] == "SealBlockedError"
    for fn in (seal.read_run, seal.read_topics, seal.read_words, seal.read_vectors):
        with pytest.raises(seal.SealBlindError):
            fn(root, PILOT_RUN, reader_kind="analysis")
    d = seal.project_ai_dir(root, pid)
    assert sorted(os.listdir(d)) == ["seal.jsonl", "sealed"]                  # runs·topics·words·vectors 없음

    # 3. 세션 잠금(1차 + 두 번째 사람 재라벨 계획·잠금)
    _ready(root)
    assert ai_seal.main(["check", "--project", root]) == 0
    capsys.readouterr()

    # 4. 해제: 명령 한 번에 판정과 짝 셋이 함께 풀린다
    assert ai_seal.main(["unseal", "--project", root, "--all", "--by", "L01"]) == 0
    u = _last_json(capsys)
    assert u["errors"] == [] and len(u["unsealed"]) == 1
    row = u["unsealed"][0]
    assert row["run_id"] == PILOT_RUN and row["conditions_met"] is True and row["engine_version"] == e2a.V03_VERSION
    assert [(p["kind"], p["error"]) for p in row["pairs"]] == [("topics", None), ("words", None), ("vectors", None)]

    # 5. 확인: verify 와 네 읽기 함수
    assert ai_seal.main(["verify", "--project", root]) == 0
    v = _last_json(capsys)
    assert v["ok"] is True and v["runs"] == [{"run_id": PILOT_RUN, "engine_version": e2a.V03_VERSION, "run": "ok",
                                              "topics": "ok", "words": "ok", "vectors": "ok"}]
    run = seal.read_run(root, PILOT_RUN, reader_kind="analysis")
    assert [x["chat_id"] for x in run["reactions"]] == [cid(1)] and run["engine"]["version"] == e2a.V03_VERSION
    assert seal.read_topics(root, PILOT_RUN, reader_kind="analysis") == tdoc
    assert seal.read_words(root, PILOT_RUN, reader_kind="analysis") == wdoc
    vec = seal.read_vectors(root, PILOT_RUN, reader_kind="analysis")
    assert vec == vdoc and len(VS.vectors_from_doc(vec)) == 2
    assert seal.seal_status(root).entries[0].engine_version == e2a.V03_VERSION
    # 두 번째 unseal --all 은 할 일이 없다(같은 것을 두 번 풀지 않는다)
    assert ai_seal.main(["unseal", "--project", root, "--all", "--by", "L01"]) == 0
    assert _last_json(capsys)["unsealed"] == []
    assert _tree(root) == {**before, **{k: v for k, v in _tree(root).items() if k.startswith(("locks", "relabel"))}}


# ============================================================================== 원인 코드 (24 5-2 의 5)
def test_seal_error_codes_name_the_cause(proj):
    """SealError 마다 원인 코드(.code) — 메시지(str)는 사람이 읽는 문장 그대로. 해제 당일 요약은 'unseal:<코드>'."""
    root = proj["root"]
    _ready(root)
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    binp = os.path.join(ai_dir(proj), "sealed", f"{e.sha256}.bin")
    with open(binp, "ab") as f:
        f.write(b" ")
    with pytest.raises(seal.SealError) as ei:
        seal.unseal(root, e.sha256, by="L01")
    assert ei.value.code == "sealed_mismatch" and seal.error_code(ei.value) == "sealed_mismatch"
    assert "기록과 다릅니다" in str(ei.value) and not str(ei.value).startswith("sealed_mismatch")
    os.remove(binp)
    seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01")
    # 채팅 파일이 없으면 messages_missing (봉인 파일을 열기 전에)
    msgs = os.path.join(root, "chat", "messages.jsonl")
    keep = open(msgs, "rb").read()
    os.remove(msgs)
    with pytest.raises(seal.SealError) as ei:
        seal.unseal(root, e.sha256, by="L01")
    assert ei.value.code == "messages_missing"
    with open(msgs, "wb") as f:
        f.write(keep)
    seal.unseal(root, e.sha256, by="L01")
    other = seal.seal_bytes(root, data_of(result_doc(created_at="2026-10-23T10:00:00+09:00")), source="file", by="L01")
    with pytest.raises(seal.SealError) as ei:
        seal.unseal(root, other.sha256, by="L01")
    assert ei.value.code == "dup_run_id"
    cases = [(lambda: seal.unseal(root, e.sha256, by="L01"), "already_unsealed"),
             (lambda: seal.unseal(root, "f" * 64, by="L01"), "no_seal_record"),
             (lambda: seal.unseal(root, "xyz", by="L01"), "sha_format"),
             (lambda: seal.unseal(root, other.sha256, by="bad"), "bad_by"),
             (lambda: seal.read_run(root, "run-never-unsealed"), "not_unsealed"),
             (lambda: seal.seal_status(root, reader_kind="primary"), "reader_blind"),
             (lambda: seal.seal_words_bytes(root, b"{}", pair_sha256="0" * 64, by="L01"), "pair_missing"),
             (lambda: seal.seal_words_bytes(root, b"{}", pair_sha256=e.sha256, by="L01"), "pair_unsealed")]
    for fn, code in cases:
        with pytest.raises(seal.SealError) as ei:
            fn()
        assert ei.value.code == code, code
    assert seal.SealBlockedError(["x"]).code == "conditions_unmet"
    assert seal.error_code(OSError("x")) == "OSError" and seal.error_code(seal.SealError("x")) == "seal_error"


def test_every_seal_error_code_is_listed():
    """seal.py 의 raise SealError(…) 는 모두 code= 를 주고, 그 코드는 모두 SEAL_ERROR_CODES 에 있다(요약·문서가 같은 목록)."""
    import ast
    tree = ast.parse(inspect.getsource(seal))
    used, missing = set(), 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) \
                and isinstance(node.exc.func, ast.Name) and node.exc.func.id == "SealError":
            kw = [k for k in node.exc.keywords if k.arg == "code"]
            if not kw:
                missing += 1
            else:
                used.add(kw[0].value.value)
    used |= {c.code for c in (seal.SealError, seal.SealBlindError, seal.SealNotUnsealedError, seal.SealBlockedError,
                              seal.AlreadyUnsealedError)}
    assert missing == 0
    assert len(used) > 20 and used <= set(seal.SEAL_ERROR_CODES), sorted(used - set(seal.SEAL_ERROR_CODES))
    assert len(set(seal.SEAL_ERROR_CODES)) == len(seal.SEAL_ERROR_CODES)


def test_cli_errors_carry_code(proj, capsys):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    assert ai_seal.main(["unseal", "--project", root, "--sha256", e.sha256, "--by", "L01"]) == 1
    j = json.loads(capsys.readouterr().out)
    assert j["error"] == "SealBlockedError" and j["code"] == "conditions_unmet"
    assert ai_seal.main(["unseal", "--project", root, "--sha256", "f" * 64, "--by", "L01"]) == 1
    j = json.loads(capsys.readouterr().out)
    assert j["code"] == "no_seal_record"
    _ready(root)
    os.remove(os.path.join(ai_dir(proj), "sealed", f"{e.sha256}.bin"))
    assert ai_seal.main(["unseal", "--project", root, "--all", "--by", "L01"]) == 1
    j = json.loads(capsys.readouterr().out)
    assert [x["code"] for x in j["errors"]] == ["sealed_unreadable"] and j["unsealed"] == []


# ============================================================================== 짝 보충 (D-23 3, 24 5-2 의 1)
def _pairs(root):
    pid = _pid(root)
    return {"topics": data_of(topic_doc()), "words": data_of(words_doc(project_id=pid)),
            "vectors": data_of(vectors_doc(project_id=pid))}


def _records(proj):
    p = os.path.join(ai_dir(proj), "seal.jsonl")
    return [json.loads(x) for x in open(p, encoding="utf-8").read().splitlines()]


def test_attach_pairs_to_presealed_judgement_after_lock_then_unseal(proj):
    """판정만 사전 봉인 → 잠금 → 짝 셋을 나중에 붙임 → 해제하면 짝 셋이 같은 해제에서 풀린다. 판정은 다시 봉인하지 않는다."""
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="engine_link", by="L01", now=NOW)   # 1차 잠금 전
    _ready(root)
    n_before = len(_records(proj))
    # now 를 고정한다 — 지금 시각이면 기록의 at(예 '…:30.664')에 '0.6' 이 우연히 들어가 아래 검사가 가끔 틀렸다
    r = seal.attach_pairs(root, e.sha256, _pairs(root), by="L01", source="engine", now=NOW + timedelta(days=1))
    assert r.ok and r.attached == ["topics", "words", "vectors"] and r.presealed is True and r.after_primary_lock is True
    assert [x["status"] for x in r.rows] == ["attached"] * 3 and all(x["check"] == "valid" for x in r.rows)
    recs = _records(proj)
    assert len(recs) == n_before + 3
    assert [x["kind"] for x in recs if x["kind"] == "ai.sealed"] == ["ai.sealed"]       # 판정 봉인은 하나 그대로
    new = recs[n_before:]
    assert [x["kind"] for x in new] == ["topics.sealed", "words.sealed", "vectors.sealed"]
    assert all(x["pair"] == e.sha256 and x["attached"] is True and x["after_primary_lock"] is True for x in new)
    assert [x["source"] for x in new] == ["engine_topics", "engine_words", "engine_vectors"]
    raw = open(os.path.join(ai_dir(proj), "seal.jsonl"), encoding="utf-8").read()
    assert SECRET_WORD not in raw and TOPIC_SECRET not in raw and "0.6" not in raw
    st = seal.seal_status(root)
    ent = st.entries[0]
    assert ent.presealed is True and ent.pairs_attached == ["topics", "words", "vectors"]
    assert all(getattr(ent, f"{k}_sha256") for k in seal.PAIR_KINDS)
    # 두 번째는 할 일이 없다(덮어쓰지 않는다)
    r2 = seal.attach_pairs(root, e.sha256, _pairs(root), by="L01")
    assert r2.ok and r2.attached == [] and [x["status"] for x in r2.rows] == ["present"] * 3
    assert len(_records(proj)) == n_before + 3
    res = seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4))
    assert res.presealed is True
    assert [(p["kind"], p["error"]) for p in res.record["pairs"]] == [("topics", None), ("words", None), ("vectors", None)]
    rows, ok = ai_seal.verify_project(root)
    assert ok and rows[0]["topics"] == rows[0]["words"] == rows[0]["vectors"] == "ok"
    with pytest.raises(seal.SealError) as ei:                        # 풀린 판정에는 붙이지 않는다
        seal.attach_pairs(root, e.sha256, {"words": _pairs(root)["words"]}, by="L01")
    assert ei.value.code == "pair_unsealed"


def test_attach_pairs_all_or_nothing_and_refusals(proj):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    n0 = len(_records(proj))
    docs = _pairs(root)
    docs["vectors"] = data_of(vectors_doc(project_id=_pid(root), video_no="999"))     # 다른 방송 → 거부
    r = seal.attach_pairs(root, e.sha256, docs, by="L01")
    assert not r.ok and [x["status"] for x in r.rows] == ["skipped", "skipped", "rejected"]
    assert r.rows[2]["reject_reasons"] == ["vectors_video_mismatch"]
    assert len(_records(proj)) == n0                                  # 하나도 붙이지 않았다
    assert seal.seal_status(root).entries[0].words_sha256 is None
    # 한 종류가 이미 있으면 그것만 present, 나머지는 붙인다
    seal.seal_words_bytes(root, _pairs(root)["words"], pair_sha256=e.sha256, by="L01")
    r = seal.attach_pairs(root, e.sha256, _pairs(root), by="L01")
    assert [x["status"] for x in r.rows] == ["attached", "present", "attached"]
    assert seal.seal_status(root).entries[0].pairs_attached == ["topics", "vectors"]
    assert r.after_primary_lock is False                              # 잠긴 1차가 아직 없다
    rej = seal.seal_bytes(root, b'{"schema": "x"}', source="file", by="L01")
    assert rej.check == "rejected"
    for fn, code in [(lambda: seal.attach_pairs(root, rej.sha256, _pairs(root), by="L01"), "pair_not_valid"),
                     (lambda: seal.attach_pairs(root, "0" * 64, _pairs(root), by="L01"), "pair_missing"),
                     (lambda: seal.attach_pairs(root, e.sha256, {"asr": b"{}"}, by="L01"), "pair_kind"),
                     (lambda: seal.attach_pairs(root, e.sha256, {}, by="L01"), "pair_kind"),
                     (lambda: seal.attach_pairs(root, e.sha256, _pairs(root), by="L01", source="x"), "source_kind"),
                     (lambda: seal.attach_pairs(root, "xyz", _pairs(root), by="L01"), "pair_sha_format")]:
        with pytest.raises(seal.SealError) as ei:
            fn()
        assert ei.value.code == code, code
    for kind in ("primary", "relabel_same", "origin_review"):
        with pytest.raises(seal.SealBlindError):
            seal.attach_pairs(root, e.sha256, _pairs(root), by="L01", reader_kind=kind)


def test_cli_attach_pairs_files_prefix_and_remove(proj, tmp_path, capsys):
    root = proj["root"]
    e1 = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    e2 = seal.seal_bytes(root, data_of(result_doc(run_id="run-synth-other")), source="file", by="L01", now=NOW)
    files = {}
    for k, b in _pairs(root).items():
        files[k] = tmp_path / f"{k}.json"
        files[k].write_bytes(b)
    args = ["attach-pairs", "--project", root, "--by", "L01"] + [x for k in seal.PAIR_KINDS
                                                                  for x in (f"--{k}", str(files[k]))]
    assert ai_seal.main(args + ["--sha256", e1.sha256[:12]]) == 0      # status 가 찍는 앞 12자
    out = capsys.readouterr().out
    j = json.loads(out)
    assert j["targets"] == 1 and j["ok"] and [x["status"] for x in j["results"][0]["rows"]] == ["attached"] * 3
    assert SECRET_WORD not in out and TOPIC_SECRET not in out and SECRET_RUN not in out
    assert ai_seal.main(args + ["--all", "--remove-source"]) == 0       # e1 은 present, e2 만 붙음
    j = json.loads(capsys.readouterr().out)
    by = {r["sha256_12"]: [x["status"] for x in r["rows"]] for r in j["results"]}
    assert by == {e1.sha256[:12]: ["present"] * 3, e2.sha256[:12]: ["attached"] * 3} and j["source_removed"]
    assert not any(f.exists() for f in files.values())
    assert ai_seal.main(["status", "--project", root]) == 0
    st = json.loads(capsys.readouterr().out)
    assert all(x["pairs_attached"] == list(seal.PAIR_KINDS) for x in st["entries"])
    assert ai_seal.main(["attach-pairs", "--project", root, "--by", "L01", "--sha256", "abc"]) == 2   # 짝 입력 없음
    assert json.loads(capsys.readouterr().out)["code"] == "args"
    assert ai_seal.main(args[:5] + ["--sha256", "0" * 12, "--topics", str(tmp_path / "none.json")]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "no_seal_record"


def test_cli_attach_pairs_pilot_needs_sealed_work_and_engine_injection(pilot, tmp_path, monkeypatch, capsys):
    """시범(pilot-*)은 전사 낱말·벡터 파일이 <데이터>\\sealed-work 아래여야 한다(읽기 전에 종료 2). --from-engine 은 엔진 함수를
    한 번만 부르고(빠진 짝만), 모두 있으면 부르지 않는다. 엔진이 실패하면 종료 1 과 코드."""
    root = pilot["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="engine_link", by="L01", now=NOW)
    pb = _pairs(root)
    outside = tmp_path / "words.json"
    outside.write_bytes(pb["words"])
    opened = _spy_open(monkeypatch, os.path.normcase(str(outside)))
    assert ai_seal.main(["attach-pairs", "--project", root, "--by", "L01", "--all", "--words", str(outside)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "pair_file_unprotected" and opened == []
    monkeypatch.undo()
    sw = ai_seal.sealed_work_dir(root)
    assert os.path.normcase(sw) == os.path.normcase(str(pilot["tmp"] / "chatcoach-data" / "sealed-work"))
    calls = []

    def fake_engine(project, ns, kinds):
        calls.append(list(kinds))
        assert ns.seal is True and ns.asr == "synth.asr.json"
        return {k: pb[k] for k in kinds}
    monkeypatch.setattr(ai_seal, "ENGINE_PAIRS", fake_engine)
    base = ["attach-pairs", "--project", root, "--by", "L01", "--all", "--from-engine", "--asr", "synth.asr.json"]
    assert ai_seal.main(base + ["--kinds", "words"]) == 0
    assert calls == [["words"]]
    j = json.loads(capsys.readouterr().out)
    assert j["engine_run"] and [x["status"] for x in j["results"][0]["rows"]] == ["attached"]
    assert ai_seal.main(base) == 0                                     # topics·vectors 만 만든다
    assert calls[-1] == ["topics", "vectors"]
    capsys.readouterr()
    assert ai_seal.main(base) == 0 and len(calls) == 2                 # 모두 있으면 엔진을 띄우지 않는다
    j = json.loads(capsys.readouterr().out)
    assert j["missing"] == [] and j["engine_run"] is False
    assert seal.seal_status(root).entries[0].pairs_attached == ["topics", "words", "vectors"]
    # 엔진 실패
    e2 = seal.seal_bytes(root, data_of(result_doc(run_id="run-synth-2")), source="engine_link", by="L01", now=NOW)

    def broken(project, ns, kinds):
        raise ai_seal.EngineUnavailable("vectors:no_model")
    monkeypatch.setattr(ai_seal, "ENGINE_PAIRS", broken)
    assert ai_seal.main(["attach-pairs", "--project", root, "--by", "L01", "--sha256", e2.sha256,
                         "--from-engine"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "EngineUnavailable", "code": "vectors:no_model"}
    assert seal.seal_status(root).entries[1].pairs_attached == []
    assert e.sha256 != e2.sha256


# ============================================================================== reference_set 자리 (24 5-2 의 9)
@pytest.mark.parametrize("kind", ["primary", "relabel_same", "relabel_second", "origin_review", "unknown_kind"])
def test_reference_set_blind_seats_open_nothing(pilot, monkeypatch, kind):
    """라벨링·출처 판정 세션(봉인 AI 결과를 못 여는 자리)이 훈련 기준을 부르면 project.json·잠금·라벨·seal.jsonl 을
    하나도 열지 않고 꺼진다(최종 세트가 다른 세션 라벨과 seal.jsonl 을 읽기 때문)."""
    from app import reference_set as rs
    root = pilot["root"]
    _ready(root)
    seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    seen = []
    real_open, real_listdir, real_isfile = builtins.open, os.listdir, os.path.isfile

    def spy_open(p, *a, **kw):
        seen.append(("open", os.fspath(p)))
        return real_open(p, *a, **kw)

    def spy_listdir(p="."):
        seen.append(("listdir", os.fspath(p)))
        return real_listdir(p)

    def spy_isfile(p):
        seen.append(("isfile", os.fspath(p)))
        return real_isfile(p)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "listdir", spy_listdir)
    monkeypatch.setattr(os.path, "isfile", spy_isfile)
    av = rs.training_availability(root, reader_kind=kind)
    assert not av.enabled and av.reason == rs.REASON_SEAT and av.reference is None
    assert rs.load_reference(root, reader_kind=kind) == (None, rs.REASON_SEAT)
    assert rs.load_final_set_reference(root, reader_kind=kind) == (None, rs.SEAT_BLIND)
    assert rs.pilot_block_reason(root, reader_kind=kind) == rs.REASON_SEAT
    assert rs.pilot_sessions_locked(root, reader_kind=kind) == (False, [rs.REASON_SEAT])
    assert seen == []


def test_reference_set_out_of_session_and_other_seats_unchanged(pilot):
    from app import reference_set as rs
    root = pilot["root"]
    _ready(root)
    for kind in (None, "adjudication", "analysis"):
        assert not rs.seat_blind(kind)
        assert rs.pilot_sessions_locked(root, reader_kind=kind) == rs.pilot_sessions_locked(root)
        assert rs.pilot_block_reason(root, reader_kind=kind) == rs.pilot_block_reason(root)
    assert all(rs.seat_blind(k) for k in ("primary", "relabel_same", "relabel_second", "origin_review", "x"))
