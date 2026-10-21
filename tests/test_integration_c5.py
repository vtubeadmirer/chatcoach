"""C단계 다섯째 묶음 통합: ④ 놓친 후보(재라벨 세션) · 조정 화면 · 재라벨 계획 취소 · 출처 판정 · relabel/index.json ·
session.open.reopen_note (03 6-4·6-10~6-12·12-1·12-3, 06 2-9·4-6·4-7·5장·7-1, 08 D-12 2·3·4·5·6·7).

화면 없이(offscreen) 합성 프로젝트(test_integration_c4 의 25분·합성 채팅 500건)로 돈다. 부품(플레이어·채팅·타임라인)은 가짜,
폼·화면·store·잠금·계획·보고서는 진짜다. 실제 채팅·닉네임·전사문은 쓰지 않는다(지어낸 문장뿐, tmp_path 안).
"""
import builtins
import io
import json
import os
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from app import agreement_report as ar  # noqa: E402
from app import relabel  # noqa: E402
from app import session as sess  # noqa: E402
from app import store as store_mod  # noqa: E402
from app.core import agreement as ag  # noqa: E402
from app.core import missed as mc  # noqa: E402
from app.core import origin as og  # noqa: E402
from app.core import phase as ph  # noqa: E402
from app.core import timecode as tc  # noqa: E402
from tests.test_integration_c4 import (MID, SID_A, close_wb, ctx, judge, lock_via_dialog, make_long_project,  # noqa: E402
                                       open_wb, primary_session)
from tests.test_integration_c4 import qapp  # noqa: E402,F401
from tests.test_workbench_labeling import NOW  # noqa: E402

SCHEMA = store_mod.load_label_schema()


def _hms(ms):
    return tc.fmt_time(int(ms), ms_digits=False, always_hours=True)


def _records(proj, sid):
    out = []
    for n in sorted(os.listdir(proj.paths.labels_dir)):
        if n.startswith(sid + "."):
            with open(os.path.join(proj.paths.labels_dir, n), encoding="utf-8") as f:
                out += [json.loads(x) for x in f.read().splitlines() if x.strip()]
    return out


class _Spy:
    """builtins.open·io.open·os.open 으로 연 경로(정규화)를 모은다."""

    def __init__(self, monkeypatch):
        self.paths = []
        r_open, r_os_open = builtins.open, os.open

        def note(p):
            try:
                self.paths.append(os.path.normcase(os.path.abspath(os.fspath(p))))
            except TypeError:
                pass

        def w_open(file, *a, **k):
            note(file)
            return r_open(file, *a, **k)

        def w_os_open(path, *a, **k):
            note(path)
            return r_os_open(path, *a, **k)
        monkeypatch.setattr(builtins, "open", w_open)
        monkeypatch.setattr(io, "open", w_open)
        monkeypatch.setattr(os, "open", w_os_open)

    def under(self, *parts):
        pre = os.path.normcase(os.path.join(*parts))
        return [p for p in self.paths if p.startswith(pre)]


# --------------------------------------------------------------------------- 순수: 단계 순서 (core.phase)
def test_phase_missed_gate_only_in_relabel_after_samples_mark():
    seg = {"entity_id": "a", "body": {"start_ms": 0, "end_ms": 10, "category": "just_chatting"}}
    marks = ph.PhaseMarks()
    for i, p in enumerate(("segments", "reactions"), start=1):
        marks.add({"kind": "phase.mark", "seq": i, "body": ph.mark_body(p)})
    g = ph.gates([seg], marks, session_kind="primary")
    assert g[ph.SAMPLES].allowed and not g[ph.MISSED].allowed and "D-12 5" in g[ph.MISSED].reason
    g = ph.gates([seg], marks, session_kind="relabel_second")
    assert not g[ph.MISSED].allowed and "표본 완료" in g[ph.MISSED].reason
    marks.add({"kind": "phase.mark", "seq": 3, "body": ph.mark_body("samples")})
    assert ph.gates([seg], marks, session_kind="relabel_same")[ph.MISSED].allowed
    assert [s["entity_id"] for s in ph.missed_targets([seg], marks)] == ["a"]
    assert not ph.gates([seg], marks)[ph.MISSED].allowed                     # 세션 종류 모름 → 꺼짐
    # 조정·출처 판정 모드는 그 세션에서만, 라벨링 모드는 그 세션에 없다
    assert ph.modes_for("adjudication") == (ph.BROWSE, ph.ADJUDICATE)
    assert ph.modes_for("origin_review") == (ph.BROWSE, ph.ORIGIN)
    assert ph.MISSED in ph.modes_for("relabel_second") and ph.MISSED not in ph.modes_for("primary")
    assert ph.MISSED not in ph.modes_for(None)
    g = ph.gates([], marks, can_write_labels=False, why_no_write="x", session_kind="adjudication")
    assert g[ph.ADJUDICATE].allowed and not g[ph.ORIGIN].allowed and not g[ph.SEGMENTS].allowed


def test_origin_kappa_is_one_function():
    """출처 κ 는 core.origin.compare_origins 하나다. agreement 는 그것을 불러 보고서 모양으로 바꾼다."""
    a = [{"kind": "origin", "body": {"chat_id": f"c{i:020x}", "origin": o, "method": m}}
         for i, (o, m) in enumerate((("real", "manual"), ("virtual", "generation_log_match"), ("real", "manual")))]
    b = [{"kind": "origin", "body": {"chat_id": f"c{i:020x}", "origin": o, "method": "manual"}}
         for i, o in enumerate(("real", "virtual", "virtual"))]
    r = og.compare_origins(a, b, strict=False)
    rep = ag.compare_origins(a, b)
    assert rep["n"] == r["all"]["n"] == 3 and rep["kappa"] == r["all"]["kappa"]
    assert rep["human"]["n"] == r["human"]["n"] == 2 and rep["auto_pairs"] == r["auto"]["n"] == 1
    assert rep["confusion"]["rows"] == r["all"]["confusion"]["rows"]
    assert set(ag.AUTO_ORIGIN_METHODS) == set(og.AUTO_METHODS)


# --------------------------------------------------------------------------- 세션 화면: 색인·reopen_note (D-12 2)
def test_session_screen_reads_index_and_reopen_note(qapp, tmp_path, monkeypatch):
    proj = make_long_project(tmp_path)
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    for lid in ("L01", "L02"):
        sess.add_labeler(data_root, lid)
    wb, _doc, _items, _j = primary_session(qapp, proj, n_judge=1)
    p_same = wb.create_relabel_plan("relabel_same", "L01", now=NOW + timedelta(hours=1), rand4="rs01")
    p_sec = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="r201")
    close_wb(wb)
    assert relabel.read_index(proj.root) is not None
    from app.ui.session_dialog import SessionDialog
    spy = _Spy(monkeypatch)
    dlg = SessionDialog(data_root=data_root, machine_id_value=MID, now=lambda: NOW + timedelta(hours=2),
                        rand4=lambda: "zz01")
    dlg.load_context(proj.root, proj.data)
    assert spy.under(proj.root, "relabel", "RP-") == []           # 라벨러를 고르기 전에는 계획 본문을 열지 않는다
    assert dlg.select_labeler("L02")
    opened = spy.under(proj.root, "relabel", "RP-")
    assert opened and all(p_sec["plan_id"].lower() in p for p in opened)    # L02 것만 (L01 계획 본문은 열지 않음)
    c = next(x for x in dlg.choices if x.kind == "relabel_second")
    assert c.enabled and c.plan_id == p_sec["plan_id"]
    monkeypatch.undo()
    # L01: 잠긴 1차가 있으니 새 세션을 고르면 '잘못 잠근 사유' 칸이 보인다 → session.open.reopen_note
    assert dlg.select_labeler("L01")
    c_same = next(x for x in dlg.choices if x.kind == "relabel_same")
    assert c_same.plan_id == p_same["plan_id"]                            # 색인으로 자기 계획만
    assert not dlg.reopen_box.isVisibleTo(dlg)
    assert dlg.choose("new:primary") and dlg.reopen_box.isVisibleTo(dlg)
    dlg.reopen_edit.setText("  표본 판정을 덜 마치고 잠금\n이 세션에서 마저 매김  ")
    dlg._choose_open()
    c2 = dlg.context()
    dlg.deleteLater()
    assert c2 is not None and c2.reopen_note == "표본 판정을 덜 마치고 잠금 이 세션에서 마저 매김"
    wb = open_wb(qapp, proj, c2)
    meta = wb.store.session_meta
    assert meta["reopen_note"] == c2.reopen_note
    first = _records(proj, c2.session_id)[0]
    assert first["kind"] == "session.open" and SCHEMA.check(first) == []
    close_wb(wb)
    bad = dict(first, body=dict(first["body"], reopen_note=" 앞 공백"))
    assert SCHEMA.check(bad)                                          # 스키마: 앞뒤 공백 거부
    with pytest.raises(ValueError):
        sess.check_reopen_note("x" * 301)
    assert sess.clean_reopen_note("   ") is None


# --------------------------------------------------------------------------- 처음부터 끝까지
def test_missed_adjudication_cancel_and_origin(qapp, tmp_path, monkeypatch):
    proj = make_long_project(tmp_path)
    # ---- 1차(L01) → 잠금 → 계획 두 개(같은 사람·다른 사람). 같은 사람 계획이 남으면 보고서가 막힌다
    wb, _doc, a_items, a_judged = primary_session(qapp, proj)
    assert ph.MISSED not in wb.mode_buttons                       # 1차에는 ④ 단추가 없다 (D-12 5)
    assert not wb.gates()[ph.MISSED].allowed
    p_same = wb.create_relabel_plan("relabel_same", "L01", now=NOW + timedelta(hours=1), rand4="rs01")
    p_sec = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="r201")
    close_wb(wb)
    # ---- 두 번째 사람(L02): 블록 안 구간(저스트채팅 + 끝 2분 게임) → ③ 3건 → ③ 완료 → ④ 2건
    sid_b = "L02-R2-20261021-ef56"
    s_b = ctx(sid_b, "relabel_second", "L02", **relabel.session_args(p_sec))
    wb = open_wb(qapp, proj, s_b)
    assert ph.MISSED in wb.mode_buttons
    lo, hi = wb.relabel_scope()[0]
    assert wb.set_mode(ph.SEGMENTS)
    f = wb.segment_form
    for b in (lo, hi):
        if 0 < b < f.model.duration_ms:
            assert f.add_boundary(b)
    g_cut = hi - 120_000
    assert f.add_boundary(g_cut)
    assert f.select_at((lo + g_cut) // 2)
    assert f.set_category("just_chatting") and f.set_confidence("certain") and f.set_rationale(f"{_hms(lo + 5_000)} 합성 잡담")
    assert f.select_at((g_cut + hi) // 2)
    assert f.set_category("game") and f.set_confidence("probably") and f.set_rationale(f"{_hms(g_cut + 5_000)} 합성 게임")
    assert f.save() is not None
    assert wb.mark_phase_done() is not None and wb.set_mode(ph.REACTIONS) and wb.mark_phase_done() is not None
    assert wb.set_mode(ph.SAMPLES)
    q = wb.queue
    assert q.manifest is not None
    b_judged = [judge(q, "certain", "합성 답 없음"), judge(q, "undeterminable", "합성 소리 불명"),
                judge(q, "probably", "합성 답 없음")]
    assert not wb.gates()[ph.MISSED].allowed                       # ③ 완료 표시 전
    assert wb.btn_phase_done.text() == "표본 완료 표시" and wb.mark_phase_done() is not None
    assert wb.gates()[ph.MISSED].allowed
    assert wb.set_mode(ph.MISSED)
    mf = wb.missed_form
    sel = wb.missed_selection()
    assert sel is not None and {t.chat_id for t in sel.targets} == set(b_judged)
    assert all(lo <= t.t_ms < hi for t in sel.targets) and wb.right.currentWidget() is mf
    t0, t1 = sel.targets[0], sel.targets[1]
    assert mf.select_chat(t0.chat_id)
    mf.set_answers(mc.Answers(True, True, True, None, True, "certain", f"{_hms(t0.t_ms)} 합성 질문"))
    r0 = mf.save()
    assert r0 is not None and r0["body"]["is_candidate"] is True, mf.lbl_status.text()
    assert mf.select_chat(t1.chat_id)
    mf.set_answers(mc.Answers(True, False, True, "C", False, "probably", f"{_hms(t1.t_ms)} 합성 인사"))
    r1 = mf.save()
    assert r1 is not None and r1["body"]["reason_code"] == "C" and r1["body"]["reason_code_source"] == "manual"
    assert wb.mark_phase_done() is not None                          # ④ 완료 표시 (phase.mark missed)
    for r in _records(proj, sid_b):
        assert SCHEMA.check(r) == [], (r["kind"], SCHEMA.check(r))
    assert sum(1 for r in _records(proj, sid_b) if r["kind"] == "missed_candidate") == 2
    lock_via_dialog(wb)
    close_wb(wb)
    # ---- 보고서는 같은 사람 계획이 남아 막힌다 → 조정 세션에서 그 계획을 사유와 함께 취소
    assert ar.pending_relabel_plans(proj.root) == [p_same["plan_id"]]
    sid_ad = "L01-AD-20261022-ad01"
    spy = _Spy(monkeypatch)
    wb = open_wb(qapp, proj, ctx(sid_ad, "adjudication", "L01"))
    assert wb.mode == ph.ADJUDICATE and tuple(wb.mode_buttons) == (ph.BROWSE, ph.ADJUDICATE)
    av = wb.adjudication_view
    assert av is not None and wb.right.currentWidget() is av
    n = av.load_cases()
    assert n >= 2 and "조정자 = 라벨러" in av.banner.text()           # D-12 3 표시
    kinds = {c.target_kind for c in av.cases}
    assert kinds == {"segment", "reaction"}
    # 근거 한 줄까지 나란히 (D-12 4) — 화면에만
    rc = next(i for i, c in enumerate(av.cases) if c.target_kind == "reaction")
    av.select_case(rc)
    texts = av.side_texts(av.cases[rc])
    assert len(texts) == 2 and all(t["rationale"] != "—" for t in texts)
    saved = []
    for i, c in enumerate(av.cases[:2]):
        av.select_case(i)
        assert av.choose_decision(f"pick:{c.sides[0].session_id}")
        av.cause_box.setCurrentIndex(av.cause_box.findData("guide_unclear"))
        av.tag_edit.setText("합성-경계흐림")
        rec = av.save_current()
        assert rec is not None, av.last_error
        saved.append(rec)
    assert all(SCHEMA.check(r) == [] for r in saved)
    assert {r["body"]["pattern_tag"] for r in saved} == {"합성-경계흐림"}
    # 계획 취소(별도 계획: 같은 사람 재라벨) — 메뉴 길과 같은 화면
    from app.ui.plan_cancel_dialog import PlanCancelDialog
    dlg = PlanCancelDialog(store=wb.store, app_version="0.0.0")
    i = dlg.plan_box.findData(p_same["plan_id"])
    assert i >= 0
    dlg.plan_box.setCurrentIndex(i)
    assert dlg.cancel_plan(reason="합성 시험: 같은 사람 재라벨을 하지 않음") is None    # 확인 칸 전
    dlg.confirm.setChecked(True)
    doc = dlg.cancel_plan(reason="합성 시험: 같은 사람 재라벨을 하지 않음")
    dlg.deleteLater()
    assert doc is not None and doc["canceller_is_labeler"] is True
    assert relabel.is_cancelled(proj.root, p_same["plan_id"]) and ar.pending_relabel_plans(proj.root) == []
    st_idx = {e["plan_id"]: e["status"] for e in relabel.read_index(proj.root)["plans"]}
    assert st_idx == {p_same["plan_id"]: "cancelled", p_sec["plan_id"]: "active"}
    assert os.path.isfile(relabel.plan_path(proj.root, p_same["plan_id"]))     # 계획 파일은 남는다
    # 조정 세션은 보고서를 쓸 수 있다(03 12-1): 조정자 = 라벨러 표시, 놓친 후보 줄
    res = ar.make_report(proj.root, SID_A, sid_b, out_root=str(tmp_path / "보고서"), created_by="L01",
                         reader_kind="adjudication")
    rep = res.report
    assert rep["header"]["adjudicator_is_labeler"] is True
    assert rep["missed"]["judged"]["b"] == 2 and rep["missed"]["judged"]["a"] == 0
    assert "links" in rep
    md = open(os.path.join(res.out_dir, "report.md"), encoding="utf-8").read()
    assert "놓친" in md
    close_wb(wb)
    monkeypatch.undo()
    # ---- 출처 판정: 합성 대상 파일 → 출처 판정 세션에서 3건
    from app.ui.origin_view import write_targets_file
    ids = list(a_judged[:3])
    tgt = og.build_targets(chat_ids=ids, created_at="2026-10-22T10:00:00+09:00", created_by="L01",
                           basis="sample_items", note="합성 시험",
                           auto={ids[0]: {"origin": "virtual", "method": "generation_log_match",
                                          "generation_ref": {"file": "gen-합성.jsonl", "gen_id": "g1"},
                                          "score": 0.9, "second_score": 0.2}})
    write_targets_file(os.path.join(proj.root, "origin"), tgt)
    sid_or = "L01-OR-20261022-or01"
    spy = _Spy(monkeypatch)
    wb = open_wb(qapp, proj, ctx(sid_or, "origin_review", "L01"))
    assert wb.mode == ph.ORIGIN and tuple(wb.mode_buttons) == (ph.BROWSE, ph.ORIGIN)
    ov = wb.origin_view
    assert ov.progress().total == 3 and ov.current_item().chat_id == ids[0]
    assert ov.take_auto()
    ov.set_confidence("probably")
    assert ov.save_current() is not None
    ov.set_origin("real")
    ov.set_confidence("certain")
    ov.rationale.setText(f"{_hms(1_000)} 합성 시청자 채팅")
    assert ov.save_current() is not None
    ov.set_origin("undeterminable")
    ov.rationale.setText(f"{_hms(2_000)} 합성 판단 어려움")
    assert ov.save_current() is not None
    assert ov.progress().done == 3 and ov.progress().auto == 1
    assert wb.mark_phase_done() is not None
    close_wb(wb)
    monkeypatch.undo()
    # 출처 판정 세션은 다른 세션 기록·표본·재라벨 계획을 열지 않는다(03 12-1)
    assert spy.under(proj.paths.labels_dir, SID_A) == [] and spy.under(proj.paths.labels_dir, sid_b) == []
    assert spy.under(proj.paths.samples_dir) == [] and spy.under(proj.root, "relabel") == []
    recs = [r for r in _records(proj, sid_or) if r["kind"] == "origin"]
    assert len(recs) == 3 and all(SCHEMA.check(r) == [] for r in recs)
    assert sum(1 for r in recs if og.is_auto(r)) == 1


def test_mainwindow_cancel_menu_only_in_adjudication(qapp, tmp_path):
    from app import updater
    from app.appspec import SPEC
    from app.ui.mainwindow import MainWindow
    from app.ui import project_view as pv
    from app.ui.workbench import Workbench
    from tests.test_integration_c4 import PARTS
    proj = make_long_project(tmp_path)
    wb, _doc, _items, _j = primary_session(qapp, proj, n_judge=1)
    plan = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="r201")
    close_wb(wb)
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    win = MainWindow(SPEC, updater.load_installed(SPEC), data_root, "", None, [], set())
    win.check_updates = lambda force=False: None
    try:
        for kind, sid, allowed in (("primary", "L01-P-20261022-pp01", False),
                                   ("adjudication", "L01-AD-20261022-ad02", True)):
            w = Workbench(win, parts=PARTS, session=ctx(sid, kind, "L01"), store_options={"app_version": "0.0.0"})
            win.workbench = w
            w.open_project(pv.open_checked(proj.root), sync=True)
            win.update_session_actions()
            assert win.a_cancel_plan.isEnabled() is allowed, win.a_cancel_plan.toolTip()
            if allowed:
                def run(d):
                    d.plan_box.setCurrentIndex(d.plan_box.findData(plan["plan_id"]))
                    d.confirm.setChecked(True)
                    d.cancel_plan(reason="합성 시험 취소")
                doc = win.plan_cancel_dialog(run=run)
                assert doc is not None and doc["plan_id"] == plan["plan_id"]
                assert relabel.is_cancelled(proj.root, plan["plan_id"])
            w.close_workbench("user")
            win.workbench = None
            w.deleteLater()
    finally:
        win.close()
