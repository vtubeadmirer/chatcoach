"""C단계 여섯째 묶음 통합: 채팅 색인 캐시·재현 검증 워커·타임라인 부분 갱신의 연결, 조정 세션의 출처 대상·relabel_second
계획(store 경유), 표본 밖 후보(scope = extra), 재라벨 잠금의 current_sample, 프로젝트를 열 때 계획 색인 다시 만들기
(14 4-2 의 1·2·5·6·7·9·11, 14 6-1, 08 D-13 3·5).

화면 없이(offscreen) 합성 프로젝트(test_integration_c4 의 25분·합성 채팅 500건)로 돈다. 부품은 가짜, 폼·화면·store·잠금·
계획·보고서·워커는 진짜다. 실제 채팅·닉네임·전사문은 쓰지 않는다(지어낸 문장뿐, tmp_path 안).
"""
import json
import os
import threading
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer  # noqa: E402

from app import agreement_report as ar  # noqa: E402
from app import chat_index as ci  # noqa: E402
from app import relabel  # noqa: E402
from app import store as store_mod  # noqa: E402
from app.core import missed as mc  # noqa: E402
from app.core import origin as og  # noqa: E402
from app.core import phase as ph  # noqa: E402
from app.core import sampling as sp  # noqa: E402
from app.core import timecode as tc  # noqa: E402
from app.ui import workbench as wbm  # noqa: E402
from app.workers import JobWorker  # noqa: E402
from tests.test_integration_c4 import (PARTS, SID_A, MarkChat, close_wb, ctx, judge, lock_via_dialog,  # noqa: E402
                                       make_long_project, open_wb, primary_session)
from tests.test_integration_c4 import qapp  # noqa: E402,F401
from tests.test_workbench_labeling import NOW, FakePlayer, FakeTimeline  # noqa: E402

SCHEMA = store_mod.load_label_schema()


def _hms(ms):
    return tc.fmt_time(int(ms), ms_digits=False, always_hours=True)


class PatchTimeline(FakeTimeline):
    """부분 갱신 API(add_marker·remove_marker)를 가진 가짜 타임라인. 부른 횟수를 센다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.full_sets = 0
        self.patches = 0

    def set_markers(self, name, items):
        super().set_markers(name, items)
        self.full_sets += 1

    def add_marker(self, name, t_ms, kind, label=None):
        self.patches += 1
        self.markers.setdefault(name, []).append((t_ms, kind))
        self.markers[name].sort()

    def remove_marker(self, name, t_ms, kind=None):
        cur = self.markers.get(name) or []
        for i, (t, k) in enumerate(cur):
            if t == t_ms and (kind is None or k == kind):
                del cur[i]
                self.patches += 1
                return True
        return False


PATCH_PARTS = {"player": FakePlayer, "chat": MarkChat, "timeline": PatchTimeline}


def _open(qapp, proj, s, parts=PARTS):
    from app.ui import project_view as pv
    out = pv.open_checked(proj.root)
    wb = wbm.Workbench(parts=parts, session=s, store_options={"app_version": "0.0.0"})
    wb.resize(1366, 768)
    wb.show()
    wb.open_project(out, sync=True)
    qapp.processEvents()
    assert wb.store is not None, wb.notes
    return wb


def _records(proj, sid):
    out = []
    for n in sorted(os.listdir(proj.paths.labels_dir)):
        if n.startswith(sid + "."):
            with open(os.path.join(proj.paths.labels_dir, n), encoding="utf-8") as f:
                out += [json.loads(x) for x in f.read().splitlines() if x.strip()]
    return out


# --------------------------------------------------------------------------- 색인 → 채팅 줄 (모집단이 같다)
def test_index_records_give_same_population(tmp_path):
    proj = make_long_project(tmp_path)
    path = proj.paths.messages_jsonl
    idx = ci.build_index(path)
    got = wbm.index_records(idx, path)
    assert got is not None
    rows, sha = got
    msgs, sha2 = wbm.read_messages(path)
    assert sha == sha2 and len(rows) == len(msgs)
    assert set(rows[0]) == {"chat_id", "t_ms", "kind"}           # 시각·종류만(본문·닉네임 없음)
    for a, z in ((0, 600_000), (600_000, 1_200_000)):
        pa = sp.build_population(rows, a, z, [(590_000, 610_000)], coverage=[(0, 1_500_000)])
        pb = sp.build_population(msgs, a, z, [(590_000, 610_000)], coverage=[(0, 1_500_000)])
        assert pa.eligible == pb.eligible and pa.excluded_non_text == pb.excluded_non_text
    # 원본이 바뀌면(빠른 지문) None → 파일 읽기로 물러난다
    with open(path, "ab") as f:
        f.write(b"\n")
    assert wbm.index_records(idx, path) is None
    # 같은 chat_id 가 있었던 색인은 쓰지 않는다(build_population 의 SamplingError 를 숨기지 않게)
    idx2 = ci.build_from_records([{"chat_id": "c" + "0" * 20, "t_ms": 1, "kind": "text"}] * 2)
    assert idx2.duplicates == 1 and wbm.index_records(idx2, path) is None


def test_workbench_builds_index_and_uses_it(qapp, tmp_path):
    proj = make_long_project(tmp_path)
    s = ctx("L01-P-20261022-ix01", "primary", "L01")
    wb = _open(qapp, proj, s)
    try:
        assert wb.chat_index is not None and wb.chat_index_state == ci.BUILT and len(wb.chat_index) > 400
        cid = wb.chat_index.chat_ids[10]
        assert wb.chat_info(cid)[0] == wb.chat_index.t_of(cid)
        look = wb.chat_times_provider()()
        assert look is not None and look(frozenset([cid])) == {cid: wb.chat_index.t_of(cid)}
        assert not os.path.isdir(os.path.join(str(tmp_path), "data"))      # data_root 없음 → 캐시 파일 없음
        with open(proj.paths.messages_jsonl, "ab") as f:                   # 원본이 바뀌면 보고서는 파일을 훑는다
            f.write(b"\n")
        assert wb.chat_times_provider()() is None
    finally:
        close_wb(wb)
    # data_root 를 주면 <data_root>\cache\<project_id>\chat_index.bin (프로젝트 밖), 두 번째는 캐시를 읽는다
    droot = str(tmp_path / "앱 data")
    for want in (ci.BUILT, ci.HIT):
        from app.ui import project_view as pv
        wb = wbm.Workbench(parts=PARTS, session=s, store_options={"app_version": "0.0.0"})
        wb.data_root = droot
        wb.open_project(pv.open_checked(proj.root), sync=True)
        try:
            assert wb.chat_index_state in (want, ci.REBUILT)
        finally:
            close_wb(wb)
    p = ci.cache_path_for(proj.project_id, droot)
    assert os.path.isfile(p) and not p.startswith(proj.root)
    data = open(p, "rb").read()
    assert "합성 채팅".encode("utf-8") not in data and b"vw_" not in data


# --------------------------------------------------------------------------- 1차 → 재라벨(④·extra·current_sample) → 조정
def test_relabel_extra_current_sample_and_adjudication_tools(qapp, tmp_path):
    proj = make_long_project(tmp_path)
    wb, _doc, a_items, _a_judged = primary_session(qapp, proj)
    p_sec = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="r201")
    close_wb(wb)
    sid_b = "L02-R2-20261021-ef56"
    s_b = ctx(sid_b, "relabel_second", "L02", **relabel.session_args(p_sec))
    wb = _open(qapp, proj, s_b, PATCH_PARTS)
    try:
        lo, hi = wb.relabel_scope()[0]
        assert wb.set_mode(ph.SEGMENTS)
        f = wb.segment_form
        for b in (lo, hi):
            if 0 < b < f.model.duration_ms:
                assert f.add_boundary(b)
        assert f.select_at((lo + hi) // 2)
        assert f.set_category("just_chatting") and f.set_confidence("certain") and \
            f.set_rationale(f"{_hms(lo + 5_000)} 합성 잡담")
        assert f.save() is not None
        assert wb.mark_phase_done() is not None and wb.set_mode(ph.REACTIONS) and wb.mark_phase_done() is not None
        assert wb.set_mode(ph.SAMPLES)
        q = wb.queue
        tl = wb.timeline
        full0, patch0 = tl.full_sets, tl.patches
        b_judged = [judge(q, "certain", "합성 답 없음"), judge(q, "probably", "합성 답 없음")]
        # 타임라인 부분 갱신(14 6-1 의 3): 판정 저장은 바뀐 마커만 고친다 — 결과는 전체 계산과 같다
        assert tl.full_sets == full0 and tl.patches > patch0 and wb.timeline_patches >= 2
        full = wbm.reaction_marks(wb._label_heads["reaction"].values(), wb._label_heads["video_check"].values(),
                                  min_views=wb._min_views(), min_coverage=0.6)
        assert sorted(tl.markers[wbm.LABEL_TRACK]) == sorted(full)
        # ④ 전에는 오른쪽 메뉴 항목이 꺼져 있다
        inside = next(c for c in wb.chat_index.range_ids(lo + 30_000, hi - 30_000)
                      if c not in set(b_judged))
        acts = wb.chat_context_actions(inside)
        assert len(acts) == 1 and acts[0][0] == "표본 밖 후보로 적기" and acts[0][2] is False
        assert wb.mark_phase_done() is not None and wb.gates()[ph.MISSED].allowed
        # ④ 대상은 색인으로 계산해도 파일을 읽어 계산한 것과 같다
        assert wb.set_mode(ph.MISSED)
        sel = wb.missed_selection()
        assert sel is not None and {t.chat_id for t in sel.targets} == set(b_judged)
        args = dict(values=wb.guide_values(), project_data=dict(proj.data), meta=wb.store.session_meta,
                    drawn_by="L02", app_version="0.0.0", coverage=wb.media_coverage(),
                    reactions=list(wb._label_heads["reaction"].values()), missed_records=[], session_kind="relabel_second")
        tg = ph.missed_targets(wb.segment_heads(), wb._marks)
        by_file = wbm.prepare_missed_selection(proj.paths.messages_jsonl, wb.segment_heads(), wb.relabel_plan, tg, **args)
        assert [t.chat_id for t in by_file.selection.targets] == [t.chat_id for t in sel.targets]
        assert [t.aux for t in by_file.selection.targets] == [t.aux for t in sel.targets]
        mf = wb.missed_form
        assert all(mf._texts.get(t.chat_id) for t in sel.targets)          # 본문은 채팅 표에서(대상만)
        # 표본 밖 후보(scope = extra): 블록 밖 채팅은 막힌다, 블록 안은 ④ 목록 끝에 더해 저장
        outside = next((c for c in wb.chat_index.time_ordered_ids() if not lo <= wb.chat_index.t_of(c) < hi), None)
        if outside is not None:
            assert wb.chat_context_actions(outside)[0][2] is False and not wb.add_extra_candidate(outside)
        acts = wb.chat_context_actions(inside)
        assert acts[0][2] is True
        acts[0][1](inside)                                                  # 메뉴 항목을 누른 것과 같다
        t = mf.current_target()
        assert t is not None and t.chat_id == inside and t.sample_ref is None and t.state == mc.STATE_EXTRA
        mf.set_answers(mc.Answers(True, True, True, None, True, "certain", f"{_hms(t.t_ms)} 합성 질문 후보"))
        rec = mf.save()
        assert rec is not None, mf.lbl_status.text()
        assert rec["body"]["scope"] == "extra" and rec["body"]["sample_ref"] is None
        assert SCHEMA.check(rec) == []
        # 둘러보기로 갔다 다시 와도 표본 밖 후보는 목록에 남는다(저장한 판으로)
        assert wb.set_mode(ph.BROWSE) and wb.set_mode(ph.MISSED)
        ex = [x for x in wb.missed_form.targets if x.chat_id == inside]
        assert len(ex) == 1 and ex[0].existing is not None and wb.missed_form.select_chat(inside)
        assert wb.mark_phase_done() is not None
        # 재라벨 잠금의 current_sample (14 4-2 의 5): 블록 표본 목록을 워커 일로 계산해 잠금에 넣는다
        job = wb.current_sample_job()
        assert job is not None
        w = JobWorker.single("current_sample", job)
        w.run()
        cur = w.results["current_sample"]
        assert set(b_judged) <= set(cur) and all(lo <= wb.chat_index.t_of(c) < hi for c in cur)
        from app.ui.lock_dialog import LockDialog
        assert wb.prepare_lock()
        dlg = LockDialog(store=wb.store, sample_verify=[], current_sample=cur)
        dlg.go_confirm()
        dlg.confirm_box.setChecked(True)
        assert dlg.do_lock(), dlg.error_label.text()
        doc_b = dlg.lock_doc
        dlg.deleteLater()
        assert doc_b["current_sample"]["count"] == len(cur)
    finally:
        close_wb(wb)
    # ---- 보고서: 현재 표본은 잠금 값(given), 시각은 조회 함수(lookup), 표본 밖 후보는 비교하지 않고 센다
    idx = ci.build_index(proj.paths.messages_jsonl)
    res = ar.make_report(proj.root, SID_A, sid_b, out_root=str(tmp_path / "보고서"), created_by="L01",
                         chat_times=idx.chat_times)
    h = res.report["header"]
    assert h["current_sample_method"]["b"] == "given" and h["chat_times"]["source"] == "lookup"
    assert res.report["missed"]["judged"]["extra_b"] == 1
    # ---- 조정 세션: 출처 판정 대상 만들기·relabel_second 계획 만들기 (store 경유, D-13 5, 14 4-2 의 7·9)
    sid_ad = "L01-AD-20261022-ad01"
    wb = _open(qapp, proj, ctx(sid_ad, "adjudication", "L01"))
    try:
        av = wb.adjudication_view
        assert av is not None and av.write_origin_targets == wb.store.write_origin_targets
        assert av.btn_origin_targets.isEnabled()
        obj = av.make_origin_targets()
        assert obj is not None and obj["basis"] == og.SAMPLE_BASIS
        want = {c for v in a_items.values() for c in v}
        assert {it["chat_id"] for it in obj["items"]} == want
        path = os.path.join(proj.root, "origin", obj["target_id"] + ".json")
        assert os.path.isfile(path)
        assert av.make_origin_targets() is None and "이미 있습니다" in av.tool_msg.text()
        with pytest.raises(FileExistsError):
            wb.store.write_origin_targets(obj)
        # 조정 화면이 넓어진다(14 4-2 의 1)
        sizes = wb.hsplit.sizes()
        assert sizes[1] >= sizes[0]
        plan = av.make_second_plan(SID_A, "L03", now=NOW + timedelta(hours=3), rand4="r301")
        assert plan is not None and plan["session_kind"] == "relabel_second" and plan["labeler_id"] == "L03"
        assert relabel.active_plan_for(proj.root, "relabel_second", SID_A, "L03") == plan["plan_id"]
        assert av.make_second_plan(SID_A, "L03") is None                   # 같은 원 세션·라벨러는 하나만
    finally:
        close_wb(wb)
    # 조정이 아닌 세션은 대상 파일을 쓸 수 없다(파일을 열기 전에 막음)
    wb = _open(qapp, proj, ctx("L01-AN-20261022-an01", "analysis", "L01"))
    try:
        with pytest.raises(PermissionError):
            wb.store.write_origin_targets(obj)
    finally:
        close_wb(wb)


# --------------------------------------------------------------------------- 메인 창: 색인 다시 만들기 · 워커 바쁨 표시
def test_mainwindow_index_rebuild_and_job_modal(qapp, tmp_path):
    from app import updater
    from app.appspec import SPEC
    from app.ui import project_view as pv
    from app.ui.mainwindow import MainWindow
    proj = make_long_project(tmp_path)
    wb, _doc, _items, _j = primary_session(qapp, proj, n_judge=1)
    plan = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="r201")
    close_wb(wb)
    os.remove(relabel.index_path(proj.root))
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    win = MainWindow(SPEC, updater.load_installed(SPEC), data_root, "", None, [], set())
    win.check_updates = lambda force=False: None
    try:
        out = pv.open_checked(proj.root)
        assert win.refresh_relabel_index(out) == "rebuilt"
        assert [e["plan_id"] for e in relabel.read_index(proj.root)["plans"]] == [plan["plan_id"]]
        st = os.stat(relabel.index_path(proj.root))
        assert win.refresh_relabel_index(out) == "same"                   # 같으면 쓰지 않는다
        assert os.stat(relabel.index_path(proj.root)).st_mtime_ns == st.st_mtime_ns
        pv.release_outcome(out)

        class RO:
            project, read_only = proj, True
        assert win.refresh_relabel_index(RO()) == "skipped"
        # 워커 + 바쁨 표시: 끝까지
        w = JobWorker([("a", lambda c: 1), ("b", lambda c: 2)])
        assert win.run_job_modal(w, title="시험", label="합성 일") == "done" and w.results == {"a": 1, "b": 2}
        assert QApplicationOverride.none()
        # 취소: 첫 일이 도는 동안 취소하면 둘째 일은 돌지 않는다
        gate = threading.Event()
        ran = []

        def slow(c):
            gate.wait(5)
            ran.append("a")
            return 1
        w = JobWorker([("a", slow), ("b", lambda c: ran.append("b"))])
        QTimer.singleShot(30, lambda: (w.cancel(), gate.set()))
        assert win.run_job_modal(w, title="시험", label="합성 일") == "cancelled" and ran == ["a"]
        assert win.busy_jobs == 0
    finally:
        win.close()
        win.deleteLater()


class QApplicationOverride:
    @staticmethod
    def none() -> bool:
        from PySide6.QtWidgets import QApplication
        return QApplication.overrideCursor() is None


# --------------------------------------------------------------------------- 보고서 대화상자: 워커·취소
def test_agreement_dialog_worker_cancel_writes_nothing(qapp, tmp_path):
    from app.ui.agreement_dialog import AgreementDialog
    proj = make_long_project(tmp_path)
    wb, _doc, _items, _j = primary_session(qapp, proj, n_judge=1)
    close_wb(wb)
    # 같은 사람 재라벨 없이 비교할 잠긴 1차 두 개를 만들 수는 없으니, 1차 하나와 자기 자신은 거부(워커 길에서도 같은 문장)
    out = str(tmp_path / "보고서")
    ev = threading.Event()
    dlg = AgreementDialog(project_root=proj.root, created_by="L01",
                          chat_times_provider=lambda: (ev.wait(5), None)[1])
    try:
        assert dlg.run(SID_A, SID_A, out) is None and "같은 세션" in dlg.msg.text()
        ev.clear()
        QTimer.singleShot(20, lambda: (dlg.cancel_run(), ev.set()))
        assert dlg.run(SID_A, "L02-R2-20261021-ef56", out) is None
        assert ("취소" in dlg.msg.text()) or ("만들지 못했습니다" in dlg.msg.text())
        assert not os.path.isdir(out) or not os.listdir(out)
        assert not dlg.is_running() and not dlg.busy.isVisible()
    finally:
        dlg.deleteLater()


def test_report_cancel_check_stops_before_write(tmp_path, qapp):
    """make_report(cancel_check=…): 쓰기 직전 취소면 아무 파일도 남지 않는다."""
    proj = make_long_project(tmp_path)
    wb, _doc, _items, _j = primary_session(qapp, proj, n_judge=1)
    p_sec = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="r201")
    close_wb(wb)
    sid_b = "L02-R2-20261021-ef56"
    wb = _open(qapp, proj, ctx(sid_b, "relabel_second", "L02", **relabel.session_args(p_sec)))
    try:
        lo, hi = wb.relabel_scope()[0]
        assert wb.set_mode(ph.SEGMENTS)
        f = wb.segment_form
        for b in (lo, hi):
            if 0 < b < f.model.duration_ms:
                assert f.add_boundary(b)
        assert f.select_at((lo + hi) // 2) and f.set_category("just_chatting") and f.set_confidence("certain") \
            and f.set_rationale(f"{_hms(lo + 5_000)} 합성 잡담")
        assert f.save() is not None
        lock_via_dialog(wb)
    finally:
        close_wb(wb)
    calls = []

    class Stop(Exception):
        pass

    def check():
        calls.append(1)
        if len(calls) >= 3:
            raise Stop()
    out = str(tmp_path / "보고서")
    with pytest.raises(Stop):
        ar.make_report(proj.root, SID_A, sid_b, out_root=out, created_by="L01", cancel_check=check)
    assert not os.path.isdir(out) or not os.listdir(out)
    res = ar.make_report(proj.root, SID_A, sid_b, out_root=out, created_by="L01", cancel_check=lambda: None)
    assert sorted(os.listdir(res.out_dir)) == ["report.json", "report.md"]
