"""E 첫 묶음 검토 수정의 재현 시험 (16 8장, 06 6-6 10번·7-1·7-2, 08 D-15 7·8).

1. 계산된 노출(app.core.exposure)이 최종 세트·AI 비교에 들어간다(전에는 아무도 부르지 않았다).
   봉인 해제·일치도 보고서 쪽은 test_review_fixes_e1_seal.py.
3. '프로젝트 파기' 가 프로젝트 옆 ai\\<pid>(seal.default_ai_root)도 대상에 넣는다.
4. 나란히 보기가 ai_root 를 최종 세트까지 넘기고, 평가에서 빠진 행은 같음/다름 모집단에 넣지 않는다.

합성 프로젝트·합성 엔진 결과만 쓴다. 실제 채팅·닉네임·전사문은 쓰지 않는다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from app import analysis_flow as af
from app import export, jsonl, seal
from app.core import exposure as exm

KST = timezone(timedelta(hours=9))
LATER = datetime(2026, 10, 30, 9, 0, 0, tzinfo=KST)        # 합성 잠금(10-24)보다 뒤


def _pid(root):
    from app.project import open_project
    return open_project(root).project_id


def _write_unseal(root, at, *, ai_root=None, run_id="run-synth-x"):
    d = seal.project_ai_dir(root, _pid(root), ai_root)
    os.makedirs(d, exist_ok=True)
    jsonl.append(os.path.join(d, seal.SEAL_FILE), {"v": 1, "seq": 1, "kind": seal.K_UNSEALED, "at": at,
                                                   "sha256": "0" * 64, "run_id": run_id})
    return d


def _write_cancel(root, plan_id, cancelled_at):
    from app import relabel
    doc = {"schema": relabel.CANCEL_SCHEMA, "schema_version": 1, "plan_id": plan_id, "plan_sha256": "1" * 64,
           "plan_readable": False, "source_session_id": None, "session_kind": None, "labeler_id": None,
           "reason": "합성 취소", "cancelled_at": cancelled_at, "cancelled_by": "L01", "cancelled_machine4": "m3fa",
           "canceller_is_labeler": False, "app_version": "0.0.1"}
    os.makedirs(os.path.join(root, relabel.RELABEL_DIR), exist_ok=True)
    with open(relabel.cancel_path(root, plan_id), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


# ---------------------------------------------------------------------------------------------- 1. 최종 세트
from tests.test_final_set import P, RS, _lock, cid, proj  # noqa: E402,F401


def _rows(fx):
    return {it["row"]["chat_id"]: it["row"] for it in fx.items}


def test_final_set_marks_relabel_locked_after_unseal_as_view_exposed(proj, monkeypatch):  # noqa: F811
    root = proj["root"]
    _lock(root, P, "2026-10-24T10:00:00+09:00")               # 1차는 해제 전에 잠김, 재라벨(12:00)은 해제 뒤
    _write_unseal(root, "2026-10-24T11:00:00.000+09:00")
    calls = []
    real = exm.compute_exposure
    monkeypatch.setattr(exm, "compute_exposure", lambda *a, **k: calls.append(1) or real(*a, **k))
    fx = export.build_final_set(root, relabel_session_ids=[RS])
    assert calls == [1]
    assert fx.info["computed_exposed"] == {RS: [exm.REASON_AI_UNSEALED]}
    assert fx.info["sessions"][RS].exposed is True and not fx.info["sessions"][P].exposed
    rows = _rows(fx)
    r1 = rows[cid(1)]                                         # 두 번 같음 — 재라벨이 노출이라 대표값에서 뺀다
    assert "view_exposed" in r1["eval"]["excluded_reasons"]
    assert r1["eval"]["strict"] is False and r1["eval"]["lenient"] is False
    r6 = rows[cid(6)]                                         # 재라벨이 없는 채팅은 1차(깨끗함) 그대로
    assert "view_exposed" not in r6["eval"]["excluded_reasons"]
    assert any(RS in w and "노출" in w for w in fx.info["warnings"])
    segs = [it["row"] for it in fx.segment_items if it["piece"]["relabeled"]]
    assert segs and all("view_exposed" in r["eval"]["excluded_reasons"] for r in segs)


def test_final_set_unseal_after_all_locks_marks_nothing(proj):  # noqa: F811
    root = proj["root"]
    _write_unseal(root, "2026-10-24T13:00:00.000+09:00")
    fx = export.build_final_set(root, relabel_session_ids=[RS])
    assert fx.info["computed_exposed"] == {}
    assert _rows(fx)[cid(1)]["eval"]["strict"] is True


def test_final_set_relabel_created_after_any_cancel_is_exposed(proj):  # noqa: F811
    root = proj["root"]
    _write_cancel(root, "RP-20261024-zz22", "2026-10-24T08:00:00+09:00")   # RS 는 09:00 에 만듦 → 그 뒤
    fx = export.build_final_set(root, relabel_session_ids=[RS])
    assert fx.info["computed_exposed"] == {RS: [exm.REASON_PLAN_CANCELLED]}
    assert "view_exposed" in _rows(fx)[cid(1)]["eval"]["excluded_reasons"]


def test_final_set_broken_seal_record_counts_as_unseal(proj):  # noqa: F811
    root = proj["root"]
    d = _write_unseal(root, "2026-10-24T13:00:00.000+09:00")
    with open(os.path.join(d, seal.SEAL_FILE), "ab") as f:
        f.write(b"{not json\n")
    fx = export.build_final_set(root, relabel_session_ids=[RS])
    assert set(fx.info["computed_exposed"]) == {P, RS}
    assert any("seal.jsonl" in w for w in fx.info["warnings"])


def test_ai_compare_header_lists_computed_exposed(proj, monkeypatch):  # noqa: F811
    from app import ai_compare
    from tests.test_ai_compare import _engine_bytes
    root = proj["root"]
    _lock(root, P, "2026-10-24T10:00:00+09:00")
    e = seal.seal_bytes(root, _engine_bytes(), source="file", by="L01",
                        now=datetime(2026, 10, 23, 9, 0, 0, tzinfo=KST))
    seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험", now=datetime(2026, 10, 24, 11, 0, tzinfo=KST))
    run_id = seal.seal_status(root).entries[0].run_id
    run = ai_compare.load_unsealed_run(root, run_id)
    res = ai_compare.build_report(root, run, relabel_session_ids=[RS])
    assert res.report["header"]["view_exposed_sessions"] == [RS]


# ---------------------------------------------------------------------------------------------- 3. 파기
def test_disposal_targets_ai_next_to_projects(tmp_path):
    from app import backup
    from tests.test_backup import PID, make_project
    r = make_project(tmp_path / "projects")
    side = seal.default_ai_root(r)
    assert os.path.normcase(side) == os.path.normcase(str(tmp_path / "ai"))
    os.makedirs(os.path.join(side, PID, "sealed"))
    with open(os.path.join(side, PID, "seal.jsonl"), "wb") as f:
        f.write(b"{}\n")
    plan = backup.plan_disposal(r, data_root=str(tmp_path / "appdata"), backup_root=str(tmp_path / "backups"))
    ai = [t.path for t in plan.targets if t.kind == "ai"]
    assert [os.path.normcase(p) for p in ai] == [os.path.normcase(os.path.join(side, PID))]
    # 명시한 ai_roots 만 본다
    plan2 = backup.plan_disposal(r, data_root=str(tmp_path / "appdata"), backup_root=str(tmp_path / "backups"),
                                 ai_roots=[])
    assert not [t for t in plan2.targets if t.kind == "ai"]


# ---------------------------------------------------------------------------------------------- 4. 나란히 보기
def test_side_by_side_custom_ai_root_and_population(proj, tmp_path, monkeypatch):  # noqa: F811
    from tests.test_ai_compare import _engine_bytes
    root = proj["root"]
    # 합성 재라벨 RS 는 계획 없이 잠겨 기본 고르기에 안 든다 — 사람끼리 불일치 행을 만들려고 RS 를 넣어 준다
    real = export.build_final_set
    seen = []

    def with_rs(*a, **k):
        seen.append(k.get("ai_root"))
        return real(*a, relabel_session_ids=[RS], **k)
    monkeypatch.setattr(export, "build_final_set", with_rs)
    custom = str(tmp_path / "다른ai")
    e = seal.seal_bytes(root, _engine_bytes(), source="file", by="L01", ai_root=custom, now=LATER)
    seal.unseal(root, e.sha256, by="L01", ai_root=custom, early_reason="합성 시험", now=LATER)
    assert not os.path.exists(os.path.join(seal.default_ai_root(root), _pid(root)))
    out = af.side_by_side(root, reader_kind="analysis", ai_root=custom)
    assert out.run is not None and seen == [custom]
    final_ids = {r["chat_id"] for r in out.final_rows}
    excl_ids = {r["chat_id"] for r in out.excluded_rows}
    assert cid(2) in excl_ids and cid(2) not in final_ids    # 사람끼리 불일치(조정 없음) → 같음/다름에 넣지 않음
    assert all(r["eval"]["lenient"] for r in out.final_rows)
    assert any("human_disagreement" in w for w in out.warnings)
