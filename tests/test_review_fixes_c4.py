"""C단계 넷째 묶음 검토에서 확인된 문제의 재현 시험 (06 3장·7-1·7-3, 03 12-1·12-2, 08 D-11 7).

1. 보고서: 계획된 재라벨이 잠기기 전·재현 검증 실패·라벨링/분석 자리에서는 만들지 않는다(라벨 파일을 열기 전에).
2. 메뉴: 잠긴 1차를 다시 연 세션·분석 세션·계획된 재라벨이 남은 보기 전용에서 일치도 보고서를 끈다.
3. 재라벨 블록 표본: 맞닿은 두 블록에 걸친 구간도 표본을 만든다(블록마다 τ_block).
4. 잠긴 1차 다시 열기: 같은 사람 재라벨이 잠기기 전에는 막는다.
5. 낮음: 표본 판정 집계를 비교 범위로 자름 · 연 뒤 생긴 다른 PC 파일이 있으면 잠그지 않음 · 대체 쓰기 길의 반쪽 파일.

합성 데이터만 쓴다(실제 채팅·닉네임·프로젝트 없음).
"""
import builtins
import hashlib
import json
import os
import sys
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app import agreement_report as ar  # noqa: E402
from app import locking  # noqa: E402
from app import relabel  # noqa: E402
from app import session as sess  # noqa: E402
from app.core import agreement as ag  # noqa: E402
from tests.test_agreement import MID_1, NOW, SID_A, SID_B, _lock, _m4, proj  # noqa: E402,F401


# ============================================================================== 1. 보고서 비교 조건 (06 7-1)
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


def _spy_labels(monkeypatch, root):
    opened = []
    real = builtins.open
    labels = os.path.normcase(os.path.join(root, "labels"))

    def spy(path, *a, **kw):
        try:
            p = os.path.normcase(os.path.abspath(os.fspath(path)))
        except TypeError:
            p = ""
        if p.startswith(labels):
            opened.append(p)
        return real(path, *a, **kw)
    monkeypatch.setattr(ar.builtins, "open", spy)
    return opened


def test_report_refused_while_planned_relabel_unlocked(proj, monkeypatch):
    """L01 1차·L02 두 번째 사람이 잠겼어도 L01 의 relabel_same 계획이 남았으면 보고서를 만들지 않는다."""
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    p2 = _make_plan(root, "relabel_second", "L02", "aa01")
    _lock(root, SID_B, MID_1)
    _set_lock(root, SID_B, relabel_plan_id=p2["plan_id"])
    ps = _make_plan(root, "relabel_same", "L01", "aa02")
    opened = _spy_labels(monkeypatch, root)
    with pytest.raises(ar.RelabelPendingError) as ei:
        ar.build_report(root, SID_A, SID_B, now=NOW)
    assert ei.value.plan_ids == [ps["plan_id"]] and isinstance(ei.value, ar.NotLockedError)
    assert opened == []                                          # 라벨 파일을 하나도 열지 않았다
    assert ar.pending_relabel_plans(root) == [ps["plan_id"]]
    # relabel_same 세션이 잠기면 보고서를 만든다
    sid_rs = "L01-RS-20261024-ef56"
    with open(os.path.join(root, "labels", f"{SID_A}.{_m4(MID_1)}.jsonl"), "rb") as f:
        data = f.read()
    name = f"{sid_rs}.{_m4(MID_1)}.jsonl"
    with open(os.path.join(root, "labels", name), "wb") as f:
        f.write(data.replace(SID_A.encode(), sid_rs.encode()).replace(b'"primary"', b'"relabel_same"'))
    _lock(root, sid_rs, MID_1)
    _set_lock(root, sid_rs, relabel_plan_id=ps["plan_id"])
    monkeypatch.undo()
    assert ar.pending_relabel_plans(root) == []
    res = ar.build_report(root, SID_A, SID_B, now=NOW)
    assert res.report["report_id"]


def test_report_refused_when_sample_verify_failed(proj, monkeypatch):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    _set_lock(root, SID_B, sample_verify=[{"sample_set_id": "S-0000aaaa-d1-r1", "ok": False, "problems": 3}])
    opened = _spy_labels(monkeypatch, root)
    with pytest.raises(ar.SampleVerifyError):
        ar.build_report(root, SID_A, SID_B, now=NOW)
    assert opened == []
    # 표본 매니페스트가 있는데 검증 없이 잠근 1차도 거부
    _set_lock(root, SID_B, sample_verify=[])
    _set_lock(root, SID_A, sample_manifests=[{"sample_set_id": "S-0000aaaa-d1-r1", "sha256": "c" * 64}])
    with pytest.raises(ar.SampleVerifyError):
        ar.build_report(root, SID_A, SID_B, now=NOW)
    assert opened == []


def test_report_refused_when_plan_reproduction_fails(proj):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    p2 = _make_plan(root, "relabel_second", "L02", "aa03")
    _lock(root, SID_B, MID_1)
    _set_lock(root, SID_B, relabel_plan_id=p2["plan_id"])
    assert ar.build_report(root, SID_A, SID_B, now=NOW).report["report_id"]
    # 원 세션 잠금 파일이 계획을 만든 뒤 바뀌면(해시가 달라짐) 계획 재현 검증이 실패한다
    _set_lock(root, SID_A, app_version="9.9.9")
    with pytest.raises(ar.SampleVerifyError):
        ar.build_report(root, SID_A, SID_B, now=NOW)


@pytest.mark.parametrize("kind", ["primary", "relabel_same", "relabel_second", "analysis", "origin_review"])
def test_report_refused_from_non_report_seats(proj, monkeypatch, kind):
    """03 12-1: 보고서 쓰기는 조정 세션(RW)·세션 밖만. 1차(잠긴 것을 다시 연 경우 포함)·재라벨·분석은 거부."""
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    opened = _spy_labels(monkeypatch, root)
    with pytest.raises(ar.ReportBlindError):
        ar.build_report(root, SID_A, SID_B, now=NOW, reader_kind=kind)
    assert opened == []


def test_report_allowed_from_adjudication_and_outside_session(proj):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    assert ar.reader_block_reason(None) is None and ar.reader_block_reason("adjudication") is None
    assert ar.build_report(root, SID_A, SID_B, now=NOW, reader_kind="adjudication").report["report_id"]


# ============================================================================== 2. 메뉴 (mainwindow.agreement_block_reason)
class _FakeStore:
    locked = True


class _FakeWb:
    def __init__(self, root, kind, with_session=True):
        self.project = type("P", (), {"root": root})()
        self.session = object() if with_session else None
        self.session_kind = kind
        self.store = _FakeStore()


def _reason(wb):
    pytest.importorskip("PySide6")
    from app.ui import mainwindow as mw
    holder = type("W", (), {"workbench": wb})()
    return mw.MainWindow.agreement_block_reason(holder)


def test_menu_off_in_reopened_locked_primary_and_analysis(proj):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    assert _reason(_FakeWb(root, "primary")) is not None          # 잠긴 1차를 다시 연 세션(store.locked)
    assert _reason(_FakeWb(root, "relabel_second")) is not None
    assert _reason(_FakeWb(root, "analysis")) is not None
    assert _reason(_FakeWb(root, "adjudication")) is None
    assert _reason(_FakeWb(root, None, with_session=False)) is None


def test_menu_off_in_view_only_while_planned_relabel_unlocked(proj):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _make_plan(root, "relabel_same", "L01", "bb01")
    why = _reason(_FakeWb(root, None, with_session=False))
    assert why is not None and "06 7-1" in why


# ============================================================================== 3. 맞닿은 두 블록에 걸친 재라벨 구간
SEG_EID = "0a0a0a0a-0000-4000-8000-000000000001"
SEG_VID = "0b0b0b0b-0000-4000-8000-000000000002"


def _relabel_setup(tmp_path, sm2=None):
    pytest.importorskip("PySide6")
    from tests.test_integration_c4 import make_long_project
    pj = make_long_project(tmp_path)
    sm = "a" * 32 + "|1"
    plan = {"plan_id": "RP-20261021-zz09",
            "selected": [{"block_index": 0, "ranges": [[0, 600_000]], "seed_material": sm,
                          "tau_block_key": "6" + "0" * 63},
                         {"block_index": 1, "ranges": [[600_000, 1_200_000]], "seed_material": sm2 or sm,
                          "tau_block_key": "3" + "0" * 63}],
            "scope": {"type": "blocks", "ranges": [[0, 1_200_000]]}}
    seg = {"session_id": "L02-R2-20261021-cd34", "entity_id": SEG_EID, "id": SEG_VID,
           "body": {"start_ms": 300_000, "end_ms": 900_000, "category": "just_chatting"}}
    meta = {"guide_version": pj.data["active_guide_version"], "thresholds_sha256": "b" * 64,
            "scope": plan["scope"]}
    return pj, plan, seg, meta


def test_relabel_sample_across_adjacent_blocks(tmp_path):
    from app.core import relabel_plan as rp
    from app.core import sampling as sp
    from app.ui import workbench as wbm
    pj, plan, seg, meta = _relabel_setup(tmp_path)
    assert wbm.plan_block_for(plan, 300_000, 900_000) is None           # 옛 규칙: 블록 하나에 통째로 들어가야 했다
    pieces = wbm.plan_blocks_for(plan, 300_000, 900_000)
    assert [(p[0]["block_index"], p[1], p[2]) for p in pieces] == [(0, 300_000, 600_000), (1, 600_000, 900_000)]
    assert wbm.plan_blocks_for(plan, 1_100_000, 1_300_000) is None      # 블록 밖이 섞이면 None
    cov = [(0, 1_500_000)]
    man, msgs = wbm.prepare_relabel_sample(pj.paths.messages_jsonl, seg, [seg], plan, values={},
                                           project_data=pj.data, meta=meta, drawn_by="L02", app_version="0.0.0",
                                           coverage=cov, drawn_at="2026-10-21T10:00:00+09:00")
    got = {it["chat_id"] for it in man["items"]}
    assert man["from_plan"]["block_indices"] == [0, 1] and got and set(msgs) == got
    # 블록마다 그 블록의 τ_block 으로 고른 표본이 모두 들어 있다(06 3-5)
    with open(pj.paths.messages_jsonl, encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    sm = plan["selected"][0]["seed_material"]
    for blk, lo, hi in ((plan["selected"][0], 300_000, 600_000), (plan["selected"][1], 600_000, 900_000)):
        ids = [r["chat_id"] for r in rows if lo <= r["t_ms"] < hi]
        mine = set(rp.block_sample(ids, sm, blk["tau_block_key"]))
        assert mine and mine <= got
    assert all(sp.sort_key(sm, c) <= "6" + "0" * 63 for c in got)


def test_relabel_sample_blocks_with_different_seed_ask_to_split(tmp_path):
    from app.ui import workbench as wbm
    pj, plan, seg, meta = _relabel_setup(tmp_path, sm2="a" * 32 + "|2")
    with pytest.raises(ValueError) as ei:
        wbm.prepare_relabel_sample(pj.paths.messages_jsonl, seg, [seg], plan, values={}, project_data=pj.data,
                                   meta=meta, drawn_by="L02", app_version="0.0.0", coverage=[(0, 1_500_000)])
    assert "나눈" in str(ei.value)


# ============================================================================== 4. 잠긴 1차 다시 열기
def _survey(plans, locked):
    return sess.ProjectSurvey(session_ids=(SID_A,), locked_ids=frozenset(locked), relabel_plans=len(plans),
                              origin_files=0, engine_results=False, active_guide_version="0.1", guide_sha8=None,
                              roster_kinds={}, plans=tuple(plans))


def test_reopen_locked_primary_refused_until_relabel_same_locked():
    plan = relabel.PlanSummary("RP-20261021-rs01", "relabel_same", "L01", SID_A, "L01", ("aaaa",),
                               "2026-10-24T09:00:00+09:00")
    primary = sess.ExistingSession(SID_A, "primary", ("aaaa",), True, True, "0.1", None)
    sv = _survey([plan], [SID_A])
    # 계획만 있고 재라벨 세션이 아직 없음(3일 대기 중) → 막음
    why = sess.reopen_refusal(primary, "L01", sv, [primary])
    assert why and plan.plan_id in why
    # 재라벨 세션을 열었지만 아직 안 잠김 → 막음
    rs_open = sess.ExistingSession("L01-RS-20261024-ef56", "relabel_same", ("aaaa",), False, True, "0.1",
                                   plan.plan_id)
    assert sess.reopen_refusal(primary, "L01", sv, [primary, rs_open])
    # 잠긴 뒤에는 연다
    rs_locked = sess.ExistingSession(rs_open.session_id, "relabel_same", ("aaaa",), True, True, "0.1", plan.plan_id)
    assert sess.reopen_refusal(primary, "L01", sv, [primary, rs_locked]) is None
    # 다른 라벨러의 계획(relabel_second)·계획 없음은 막지 않는다
    p2 = relabel.PlanSummary("RP-20261021-r201", "relabel_second", "L02", SID_A, "L01", ("aaaa",), None)
    assert sess.reopen_refusal(primary, "L01", _survey([p2], [SID_A]), [primary]) is None
    assert sess.reopen_refusal(primary, "L01", _survey([], [SID_A]), [primary]) is None


def test_session_dialog_disables_reopen_during_relabel_same_wait(tmp_path):
    pytest.importorskip("PySide6")
    from tests.test_integration_c4 import MID, close_wb, make_long_project, primary_session
    from tests.test_integration_c4 import NOW as INOW
    from tests.test_integration_c4 import SID_A as ISID
    from tests.test_integration_c4 import qapp as _q  # noqa: F401
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    proj_ = make_long_project(tmp_path)
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    sess.add_labeler(data_root, "L01")
    wb, _doc, _items, _j = primary_session(app, proj_, n_judge=1)
    wb.create_relabel_plan("relabel_same", "L01", now=INOW + timedelta(hours=1), rand4="rs09")
    close_wb(wb)
    from app.ui.session_dialog import SessionDialog
    dlg = SessionDialog(data_root=data_root, machine_id_value=MID, now=lambda: INOW + timedelta(days=1),
                        rand4=lambda: "zz02")
    dlg.load_context(proj_.root, proj_.data)
    assert dlg.select_labeler("L01")
    assert not dlg.choice_buttons()["resume:" + ISID].isEnabled()
    assert not dlg.choose("resume:" + ISID)
    dlg.deleteLater()


# ============================================================================== 5. 낮음
def _R(cid, grade, t):
    return {"chat_id": cid, "entry": "from_sample", "confidence": "certain", "first_visible_ms": t,
            "result": "graded", "grade": grade}


def test_compare_samples_counts_only_inside_compared_scope():
    a = [_R(f"c{i:020x}", "related_only", i * 10_000) for i in range(30)]           # 0~290초
    b = [_R(f"c{i:020x}", "related_only", i * 10_000) for i in range(10, 14)]       # 100~130초만
    whole = ag.compare_samples(a, b)
    assert whole["judged"]["a_only"] == 26
    cut = ag.compare_samples(a, b, scope=[(100_000, 140_000)])
    j = cut["judged"]
    assert j["both"] == 4 and j["a_only"] == 0 and j["outside_scope_a"] == 26 and j["outside_scope_b"] == 0
    side = {"segments": [], "scope": None}
    res = ag.build_agreement({**side, "reactions": a}, {**side, "reactions": b, "scope": [(100_000, 140_000)]})
    assert res["samples"]["judged"]["a_only"] == 0 and res["samples"]["restricted_to_scope"] is True


def test_write_fallback_removes_half_written_file(tmp_path, monkeypatch):
    """하드 링크·rename 을 못 쓰는(비 Windows) 길에서 쓰다가 실패하면 잠금·계획 파일을 남기지 않는다."""
    path = str(tmp_path / "locks" / "L01-P-20261021-ab12.json")
    real_open = builtins.open

    class Boom:
        def __init__(self, f):
            self.f = f

        def write(self, data):
            self.f.write(data[:5])
            raise OSError(28, "디스크 가득")

        def __getattr__(self, k):
            return getattr(self.f, k)

        def __enter__(self):
            return self

        def __exit__(self, *e):
            self.f.close()

    def fake_open(p, mode="r", *a, **k):
        f = real_open(p, mode, *a, **k)
        return Boom(f) if mode == "xb" and os.path.abspath(p) == os.path.abspath(path) else f

    def no_link(*a, **k):
        raise OSError("하드 링크 없음")
    monkeypatch.setattr(locking.os, "link", no_link)
    monkeypatch.setattr(locking.os, "name", "posix")
    monkeypatch.setattr(locking.builtins, "open", fake_open)
    with pytest.raises(OSError):
        locking.write_lock_exclusive(path, b'{"x": 1}\n')
    monkeypatch.undo()
    assert not os.path.exists(path)
    assert [n for n in os.listdir(os.path.dirname(path))] == []          # 임시 파일도 남지 않음


def test_lock_refused_when_other_pc_file_appeared_after_open(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.core import phase as ph
    from app.store import StoreError
    from tests.test_integration_c4 import close_wb, ctx, make_long_project, open_wb
    from tests.test_integration_c4 import SID_A as ISID
    app = QApplication.instance() or QApplication(sys.argv[:1])
    pj = make_long_project(tmp_path)
    wb = open_wb(app, pj, ctx(ISID, "primary", "L01"))
    try:
        assert wb.set_mode(ph.SEGMENTS)
        f = wb.segment_form
        f.select_at(10_000)
        assert f.set_category("just_chatting") and f.set_confidence("certain") and f.set_rationale("00:00:05 합성")
        assert f.save() is not None and wb.mark_phase_done() is not None
        other = os.path.join(pj.paths.labels_dir, f"{ISID}.zzzz.jsonl")      # OneDrive 로 들어온 다른 PC 파일(합성)
        with open(other, "wb") as fh:
            fh.write(b"{}\n")
        with pytest.raises(StoreError):
            wb.store.lock_session()
        assert not locking.is_locked(pj.root, ISID)
        os.remove(other)
    finally:
        close_wb(wb)
