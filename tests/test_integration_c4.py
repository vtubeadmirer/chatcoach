"""C단계 넷째 묶음 통합: 세션 잠그기 → 재라벨 계획 → 두 번째 사람 세션(같은 매니페스트의 블록 표본) → 일치도 보고서 →
분석 세션의 잠긴 1차 라벨 (06 3장·4장·5장·7장, 03 6-14·12-1·12-2, 08 D-09 5·D-11 6·7·8).

화면 없이(QT_QPA_PLATFORM=offscreen) 합성 프로젝트(합성 채팅 500건, 25분, 영상 연결은 project.json 의 합성 mediaRef)로
돈다. 부품(플레이어·채팅·타임라인)은 공통 계약 모양의 가짜, 폼·큐·store·잠금·계획·보고서는 진짜다.
실제 채팅·닉네임·전사문은 쓰지 않는다(지어낸 문장뿐). 실제 프로젝트 폴더는 건드리지 않는다(tmp_path).
"""
import builtins
import io
import json
import os
import sys
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import agreement_report as ar  # noqa: E402
from app import locking  # noqa: E402
from app import project as project_mod  # noqa: E402
from app import session as sess  # noqa: E402
from app import store as store_mod  # noqa: E402
from app.core import phase as ph  # noqa: E402
from app.core import sampling as sp  # noqa: E402
from app.core import timecode as tc  # noqa: E402
from app.ui import project_view as pv  # noqa: E402
from app.ui import workbench as wbm  # noqa: E402
from tests.test_workbench_labeling import NOW, FakeChat, FakePlayer, FakeTimeline, fake_media_ref  # noqa: E402

DUR = 1_500_000                 # 25분: 10분 블록 후보 3개(마지막은 300초)
STEP_L = 3_000
N_L = DUR // STEP_L - 2
MID = "0f3a9c2e-5b7d-4e1f-8a6b-1c2d3e4f5a6b"      # 한 PC 에서 L01·L02 (D-11 6 '같은 machine_id')
SID_A = "L01-P-20261021-ab12"


class MarkChat(FakeChat):
    """set_label_marks 를 받는 가짜 채팅 표(분석 세션의 잠긴 1차 라벨 표시를 센다)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.marks = {}

    def set_label_marks(self, marks):
        self.marks = dict(marks or {})
        return len(self.marks)


PARTS = {"player": FakePlayer, "chat": MarkChat, "timeline": FakeTimeline}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def cidl(i):
    return f"c{i:020x}"


def make_long_project(tmp_path):
    """25분 합성 프로젝트. 명부(labelers[])는 비워 둔다(재라벨 종류를 막지 않게). 영상은 방송 전체를 덮는다."""
    proj = project_mod.create(str(tmp_path / "한글 프로젝트"), "합성 방송", "0", labeler_id="L01",
                              created_app_version="0.0.0", now=NOW, salt=bytes(range(32)), sample_seed="a" * 32,
                              engine_author=None, machine4="test", duration_ms=DUR)
    with open(proj.paths.messages_jsonl, "wb") as f:
        for i in range(N_L):
            m = {"chat_id": cidl(i), "t_ms": 1_000 + i * STEP_L, "server_time_ms": 1_760_000_000_000 + i * STEP_L,
                 "type_code": 1, "kind": "text", "status": "normal", "viewer_id": "vw_synthaaaaaa" + "abcdefg"[i % 7],
                 "is_anonymous": False, "nickname_masked": "시**", "role": "viewer", "text": f"합성 채팅 {i}",
                 "raw_ref": {"fetch_id": "F20261021-test", "page": 0, "index": i}}
            f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
    cal = tc.Calibration(offset_ms=0, rev=1, status="verified", created_at=NOW.isoformat(), created_by="L01")
    ref = fake_media_ref(proj.root, DUR)

    def mut(d):
        d["calibration"] = tc.append_rev(d["calibration"], tc.calibration_to_json(cal))
        d["media"]["current"] = ref
        d["media"]["media_rev"] = 1
    proj.update(mut, now=NOW, by="L01")
    return proj


def ctx(sid, kind, labeler, **kw):
    return sess.SessionContext(labeler, kind, sid, sess.machine4(MID), machine_id=MID,
                               session_created_at="2026-10-21T09:00:00+09:00", **kw)


def open_wb(qapp, proj, s):
    out = pv.open_checked(proj.root)
    wb = wbm.Workbench(parts=PARTS, session=s, store_options={"app_version": "0.0.0"})
    wb.resize(1366, 768)
    wb.show()
    wb.open_project(out, sync=True)
    qapp.processEvents()
    assert wb.store is not None, wb.notes
    return wb


def close_wb(wb):
    wb.close_workbench("user")
    wb.deleteLater()


def lock_via_dialog(wb):
    from app.ui.lock_dialog import LockDialog
    assert wb.can_lock() is None and wb.prepare_lock()
    dlg = LockDialog(store=wb.store, sample_verify=wb.sample_verify_results())
    dlg.go_confirm()
    dlg.confirm_box.setChecked(True)
    assert dlg.do_lock(), dlg.error_label.text()
    doc = dlg.lock_doc
    dlg.deleteLater()
    wb.after_session_locked()
    return doc


def judge(q, how, note):
    it = q.current_item()
    assert it is not None
    if how == "undeterminable":
        q.rationale.setText(f"{tc.fmt_time(it['t_ms'], ms_digits=False, always_hours=True)} {note}")
        assert q.judge_undeterminable() is not None
    else:
        q.set_confidence(how)
        q.rationale.setText(f"{tc.fmt_time(it['t_ms'], ms_digits=False, always_hours=True)} {note}")
        assert q.judge_unconfirmed() is not None
    return it["chat_id"]


def primary_session(qapp, proj, n_judge=2):
    """L01 1차: 저스트채팅 구간 3개(경계 600·1200초 = 10분 블록 경계) → 완료 표시 → 구간마다 표본·앞 n_judge 건 판정
    → 잠그기. 구간이 블록과 같아서 어느 블록이 뽑혀도 두 번째 사람의 블록 표본이 그 구간 표본과 같다."""
    wb = open_wb(qapp, proj, ctx(SID_A, "primary", "L01"))
    assert wb.set_mode(ph.SEGMENTS)
    f = wb.segment_form
    assert f.add_boundary(600_000) and f.add_boundary(1_200_000) and len(f.model.segments) == 3
    for s in f.model.segments:
        f.select_segment(s.id)
        assert f.set_category("just_chatting") and f.set_confidence("certain") and f.set_rationale("00:00:05 합성 잡담")
    assert f.save() is not None and wb.mark_phase_done() is not None
    assert wb.set_mode(ph.REACTIONS) and wb.mark_phase_done() is not None
    assert wb.set_mode(ph.SAMPLES)
    items, judged = {}, []
    box = wb.sample_seg_box
    for i in range(box.count()):
        box.setCurrentIndex(i)
        wb._on_sample_target(sync=True)
        wb.draw_sample(sync=True)
        man = wb.queue.manifest
        assert man is not None and man["algorithm"] == sp.ALGORITHM_V2
        items[box.itemData(i)] = [it["chat_id"] for it in man["items"]]
        for k in range(n_judge):
            judged.append(judge(wb.queue, "certain", "합성 답 없음"))
    doc = lock_via_dialog(wb)
    return wb, doc, items, judged


# --------------------------------------------------------------------------- 순수 도우미
def test_relabel_buffers_block_edges_are_not_boundaries():
    segs = [{"body": {"start_ms": 600_000, "end_ms": 900_000, "category": "just_chatting"}},
            {"body": {"start_ms": 900_000, "end_ms": 1_200_000, "category": "game"}}]
    # 블록 [600000, 1200000): 가장자리 600000·1200000 은 경계가 아니고, 분류가 바뀌는 900000 만 완충
    assert wbm.relabel_buffers(segs, [(600_000, 1_200_000)], 10_000) == [(890_000, 910_000)]
    same = [{"body": {"start_ms": 600_000, "end_ms": 900_000, "category": "just_chatting"}},
            {"body": {"start_ms": 900_000, "end_ms": 1_200_000, "category": "just_chatting"}}]
    assert wbm.relabel_buffers(same, [(600_000, 1_200_000)], 10_000) == []
    # 블록 밖 구간은 보지 않는다
    assert wbm.relabel_buffers(segs, [(0, 600_000)], 10_000) == []
    plan = {"selected": [{"ranges": [[600_000, 1_200_000]], "block_index": 1}]}
    assert wbm.plan_block_for(plan, 600_000, 900_000)["block_index"] == 1
    assert wbm.plan_block_for(plan, 500_000, 900_000) is None


def test_chat_panel_label_marks_role_and_clear(qapp):
    from PySide6.QtCore import Qt
    from app.ui.chat_panel import COL_KIND, ChatPanel
    p = ChatPanel()
    recs = [{"chat_id": cidl(i), "t_ms": i * 1000, "kind": "text", "viewer_id": "vw_synthaaaaaaa",
             "text": f"합성 {i}", "nickname_masked": "시**", "role": "viewer"} for i in range(3)]
    p.set_messages(recs)
    assert p.set_label_marks({cidl(1): "1차 직접", "c" + "f" * 20: "없는 채팅"}) == 1
    m = p.model
    assert m.data(m.index(1, COL_KIND), Qt.DisplayRole) == "1차 직접"
    assert m.data(m.index(1, COL_KIND), Qt.BackgroundRole) is not None
    assert m.data(m.index(0, COL_KIND), Qt.BackgroundRole) is None
    assert p.label_marks() == {cidl(1): "1차 직접"}
    p.set_messages(recs)                               # 새 채팅을 실으면 표시도 지운다
    assert p.label_marks() == {}
    p.deleteLater()


# --------------------------------------------------------------------------- store: 라벨 마친 범위 · 잠긴 세션 읽기
def test_store_done_ranges_and_locked_view_blind(qapp, tmp_path):
    proj = make_long_project(tmp_path)
    wb, doc, _items, judged = primary_session(qapp, proj)
    st = wb.store
    assert st.locked and st.read_only and doc["session_id"] == SID_A
    assert st.done_ranges() == [[0, DUR]]
    assert st.done_ranges(coverage=[(100_000, 700_000)]) == [[100_000, 700_000]]
    assert st.readable_locked_sessions() == []                     # 1차 세션은 남의 세션을 열지 않는다
    close_wb(wb)

    def fake(kind, labeler, sid):
        return store_mod.ProjectStore.open(proj.root, ctx(sid, kind, labeler), lambda p: _NoLock(),
                                           app_version="0.0.0")
    opened = []
    real = builtins.open

    def watch(file, *a, **k):
        opened.append(os.path.basename(os.fspath(file)))
        return real(file, *a, **k)
    # 다른 1차 세션·두 번째 사람: 잠긴 L01 파일·잠금을 디스크에서 열기 전에 막힌다
    for kind, lab, sid in (("primary", "L02", "L02-P-20261022-cd34"), ("relabel_second", "L02", "L02-R2-20261022-ef56")):
        s2 = fake(kind, lab, sid)
        opened.clear()
        builtins.open = io.open = watch
        try:
            assert s2.readable_locked_sessions() == []
            with pytest.raises(locking.LockBlindError):
                s2.locked_session_view(SID_A)
        finally:
            builtins.open = io.open = real
        assert not [n for n in opened if n.startswith(SID_A)], opened
        s2.close()
    # 분석 세션: 잠긴 1차만 읽는다
    an = fake("analysis", "L01", "L01-AN-20261023-gh78")
    assert an.readable_locked_sessions() == [SID_A]
    view, msgs = an.locked_session_view(SID_A)
    assert len(view.items("segment", SID_A)) == 3 and len(view.items("reaction", SID_A)) == len(judged)
    assert msgs == []
    # 잠금 뒤 붙은 줄은 뷰에서 빠진다(잠금 후 추가)
    lab = os.path.join(proj.paths.labels_dir, sess.session_file_name(SID_A, sess.machine4(MID)))
    with open(lab, "rb") as fh:
        last = json.loads(fh.read().splitlines()[-1])
    extra = dict(last, seq=last["seq"] + 1, id="9" * 8 + "-0000-4000-8000-" + "0" * 12, kind="warning",
                 body={"code": "other", "action": "shown"})
    with open(lab, "ab") as fh:
        fh.write((json.dumps(extra) + "\n").encode("utf-8"))
    view2, msgs2 = an.locked_session_view(SID_A)
    assert len(view2.items("reaction", SID_A)) == len(judged) and any("잠금 후 추가" in m for m in msgs2)
    # 잠기지 않은 세션은 열지 않는다
    with pytest.raises(FileNotFoundError):
        an.locked_session_view("L03-P-20261022-zz99")
    an.close()


class _NoLock:
    def tryLock(self, timeout=0):
        return True

    def unlock(self):
        pass


# --------------------------------------------------------------------------- 처음부터 끝까지
def test_lock_plan_second_person_report_and_analysis(qapp, tmp_path, monkeypatch):
    proj = make_long_project(tmp_path)
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    for lid in ("L01", "L02"):
        sess.add_labeler(data_root, lid)
    # ---- 1차 → 잠금 → 계획
    wb, doc, a_items, a_judged = primary_session(qapp, proj)
    assert all(v["ok"] for v in doc["sample_verify"]) and len(doc["sample_verify"]) == 3
    assert not wb.gates()[ph.SEGMENTS].allowed and wb.mode == ph.BROWSE
    assert wb.can_lock() is not None                                    # 다시 잠글 수 없다
    plan = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="abcd")
    assert plan["labeler_id"] == "L02" and len(plan["selected"]) == 1 and plan["selected"][0]["tau_block_key"]
    assert plan["same_machine_as_source"] is True
    close_wb(wb)
    # ---- 세션 화면: 두 번째 사람(L02) — 계획의 scope·plan_id 가 session.open 에 들어간다
    from app.ui.session_dialog import SessionDialog
    dlg = SessionDialog(data_root=data_root, machine_id_value=MID, now=lambda: NOW + timedelta(hours=2),
                        rand4=lambda: "ef56")
    dlg.load_context(proj.root, proj.data)
    assert dlg.select_labeler("L02")
    c = next(x for x in dlg.choices if x.kind == "relabel_second")
    assert c.enabled and c.plan_id == plan["plan_id"] and c.same_machine and "machine_id" in c.reason
    assert dlg.choose("new:relabel_second")
    dlg._choose_open()
    ctx_b = dlg.context()
    dlg.deleteLater()
    assert ctx_b is not None and ctx_b.relabel_plan_id == plan["plan_id"] and ctx_b.scope == plan["scope"]
    # ---- 두 번째 사람 세션: L01 기록·잠금·samples/ 를 열지 않는다(열기 감시)
    forbidden = (os.path.normcase(os.path.join(proj.paths.labels_dir, SID_A)),
                 os.path.normcase(os.path.join(proj.root, "locks", SID_A)),
                 os.path.normcase(proj.paths.samples_dir))
    seen, bad = [0], []
    r_open, r_os_open = builtins.open, os.open

    def note(p):
        try:
            s = os.path.normcase(os.path.abspath(os.fspath(p)))
        except TypeError:
            return
        seen[0] += 1
        if any(s.startswith(x) for x in forbidden):
            bad.append(s)

    def w_open(file, *a, **k):
        note(file)
        return r_open(file, *a, **k)

    def w_os_open(path, *a, **k):
        note(path)
        return r_os_open(path, *a, **k)
    monkeypatch.setattr(builtins, "open", w_open)
    monkeypatch.setattr(io, "open", w_open)
    monkeypatch.setattr(os, "open", w_os_open)
    wb = open_wb(qapp, proj, ctx_b)
    lo, hi = wb.relabel_scope()[0]
    assert wb.relabel_plan is not None and (lo, hi) == tuple(plan["scope"]["ranges"][0])
    assert wb.set_mode(ph.SEGMENTS)
    f = wb.segment_form
    for b in (lo, hi):
        if 0 < b < f.model.duration_ms:
            assert f.add_boundary(b)
    assert f.select_at((lo + hi) // 2)
    assert f.set_category("just_chatting") and f.set_confidence("certain") and f.set_rationale("00:10:05 합성 잡담")
    recs = f.save()
    assert recs is not None and len(recs) == 1
    assert wb.mark_phase_done() is not None and wb.set_mode(ph.REACTIONS) and wb.mark_phase_done() is not None
    assert wb.set_mode(ph.SAMPLES)
    q = wb.queue
    man = q.manifest
    assert man is not None and man["from_plan"]["plan_id"] == plan["plan_id"]
    b_items = [it["chat_id"] for it in man["items"]]
    a_block = next(v for v in a_items.values() if set(v) & set(b_items))
    assert set(b_items) == set(a_block)                    # 같은 구간표·같은 순서 키·τ_block → 같은 표본
    assert "τ" not in q.meta.text() and not q.verify_btn.isEnabled()      # τ_block 은 화면에 없다(06 3-4)
    # 앞 표본은 1차가 판정한 것과 같다: 하나는 같게(확실함), 하나는 판단 불가, 하나는 직접(편집기)
    b1 = judge(q, "certain", "합성 답 없음")
    b2 = judge(q, "undeterminable", "합성 소리 불명")
    it3 = q.current_item()
    wb.mark_reaction()
    assert wb.is_editing()
    ed = wb.editor
    t3 = int(it3["t_ms"])
    ed.utt_start.setText(f"{(t3 + 1_000) / 1000:.3f}")
    ed.utt_end.setText(f"{(t3 + 3_000) / 1000:.3f}")
    ed._on_utt_times()
    ed.heard.setText("합성 발화")
    ed._on_heard_edited("합성 발화")
    ed.evidence.boxes["nickname_called"].setChecked(True)
    ed.set_grade("direct")
    ed.set_confidence("certain")
    ed.rationale.setText(f"{tc.fmt_time(t3 + 1_000, ms_digits=False, always_hours=True)} 합성 호명")
    assert ed.save() is not None and not wb.is_editing()
    assert {b1, b2} <= set(a_judged)                     # 시각 순 제시라 앞 두 건이 1차가 판정한 것과 같다
    # 라벨링 세션: 잠긴 1차 라벨은 어디에도 없다
    assert wb.primary_labels() == {"sessions": [], "segments": 0, "markers": 0, "chats": 0}
    assert "primary" not in wb.timeline.markers and wb.chat.marks == {}
    doc_b = lock_via_dialog(wb)
    assert doc_b["relabel_plan_id"] == plan["plan_id"] and doc_b["sample_verify"] == []
    close_wb(wb)
    monkeypatch.undo()
    assert seen[0] > 0 and not bad, bad
    # ---- 일치도 보고서 (두 세션 모두 잠긴 뒤, 프로젝트 밖)
    from app.ui.agreement_dialog import AgreementDialog, locked_comparable_sessions
    assert locked_comparable_sessions(proj.root) == sorted([SID_A, ctx_b.session_id])
    ad = AgreementDialog(project_root=proj.root, created_by="L01")
    res = ad.run(SID_A, ctx_b.session_id, str(tmp_path / "보고서"))
    ad.deleteLater()
    assert res is not None, ad.msg.text()
    rep = res.report
    assert rep["header"]["comparison"] == "inter" and rep["header"]["same_machine"] is True
    assert rep["samples"]["judged"]["both"] >= 2
    assert sorted(os.listdir(res.out_dir)) == ["report.json", "report.md"]
    assert not os.path.normcase(res.out_dir).startswith(os.path.normcase(proj.root))
    # ---- 분석 세션: 잠긴 1차(L01)만 보인다. 두 번째 사람(R2) 세션은 1차가 아니라 보이지 않는다
    wb = open_wb(qapp, proj, ctx("L01-AN-20261023-gh78", "analysis", "L01"))
    pl = wb.primary_labels()
    assert pl["sessions"] == [SID_A] and pl["segments"] == 3 and pl["chats"] == len(a_judged)
    assert len(wb.timeline.markers.get("primary", [])) == len(a_judged)
    assert {k for _t, k in wb.timeline.markers["primary"]} == {"primary_weak"}     # 반응 미확인 → 속이 빈 표시
    assert len(wb.timeline.segments) == 3                       # 자기 구간이 없어 잠긴 1차 구간을 그린다
    assert set(wb.chat.marks) == set(a_judged) and all(v.startswith("1차") for v in wb.chat.marks.values())
    assert "engine" not in wb.timeline.markers
    close_wb(wb)


def test_locked_primary_reopens_read_only_and_can_make_plan(qapp, tmp_path):
    proj = make_long_project(tmp_path)
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    sess.add_labeler(data_root, "L01")
    wb, _doc, _items, _j = primary_session(qapp, proj, n_judge=1)
    close_wb(wb)
    from app.ui.session_dialog import SessionDialog
    dlg = SessionDialog(data_root=data_root, machine_id_value=MID, now=lambda: NOW, rand4=lambda: "zz01")
    dlg.load_context(proj.root, proj.data)
    assert dlg.select_labeler("L01")
    assert dlg.choice_buttons()["resume:" + SID_A].isEnabled()           # 잠긴 세션도 고를 수 있다(읽기 전용)
    assert dlg.selected_key() is None                                    # 미리 고르지는 않는다
    assert dlg.choose("resume:" + SID_A)
    dlg._choose_open()
    c = dlg.context()
    dlg.deleteLater()
    wb = open_wb(qapp, proj, c)
    try:
        assert wb.store.locked and wb.store.read_only and not wb.gates()[ph.SEGMENTS].allowed
        assert wb.can_lock() is not None and wb.can_make_relabel_plan() is None
        n = len(os.listdir(os.path.join(proj.root, "labels")))
        plan = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="kk11")
        assert plan["source_session_id"] == SID_A and len(os.listdir(os.path.join(proj.root, "labels"))) == n
    finally:
        close_wb(wb)


def test_relabel_same_early_start_reason_is_recorded(qapp, tmp_path):
    proj = make_long_project(tmp_path)
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    sess.add_labeler(data_root, "L01")
    wb, _doc, _items, _j = primary_session(qapp, proj, n_judge=1)
    plan = wb.create_relabel_plan("relabel_same", "L01", now=NOW + timedelta(hours=1), rand4="wxyz")
    assert plan["earliest_start_at"] is not None
    close_wb(wb)
    from app.ui.session_dialog import SessionDialog
    asked = []
    dlg = SessionDialog(data_root=data_root, machine_id_value=MID, now=lambda: NOW + timedelta(days=1),
                        rand4=lambda: "rs01")
    dlg.early_reason = lambda c: asked.append(c.remaining_ms) or "합성 일정 사유"
    dlg.load_context(proj.root, proj.data)
    assert dlg.select_labeler("L01")
    c = next(x for x in dlg.choices if x.kind == "relabel_same")
    assert c.enabled and c.early and c.remaining_ms > 0
    assert dlg.choose("new:relabel_same")
    dlg._choose_open()
    ctx_rs = dlg.context()
    dlg.deleteLater()
    assert asked and ctx_rs.extra == {"relabel_too_early": "합성 일정 사유"}
    wb = open_wb(qapp, proj, ctx_rs)
    warns = [r for r in wb.store.records() if r["kind"] == "warning"]
    assert [w["body"]["code"] for w in warns] == ["relabel_too_early"] and warns[0]["body"]["detail"] == "합성 일정 사유"
    close_wb(wb)
    # 사유를 주지 않으면(취소) 세션을 만들지 않는다
    dlg = SessionDialog(data_root=data_root, machine_id_value=MID, now=lambda: NOW + timedelta(days=1),
                        rand4=lambda: "rs02")
    dlg.early_reason = lambda c: None
    dlg.load_context(proj.root, proj.data)
    assert dlg.select_labeler("L01")
    # 이미 그 계획으로 연 세션이 있어 두 번째는 막힌다
    c2 = next(x for x in dlg.choices if x.kind == "relabel_same")
    assert not c2.enabled and "이미" in c2.reason
    dlg.deleteLater()


def test_mainwindow_session_and_report_menus(qapp, tmp_path, monkeypatch):
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))
    proj = make_long_project(tmp_path)
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    win = mw.MainWindow(SPEC, inst, data_root, str(tmp_path / "log.txt"), None, [], set())
    try:
        titles = [a.text() for a in win.menuBar().actions()]
        assert any("세션" in t for t in titles) and any("보고" in t for t in titles)
        assert not any(k in t for t in titles for k in ("&S", "&O", "&G", "&J", "&U"))   # 폼 Alt 단축키와 겹치지 않음
        win.update_session_actions()
        assert not win.a_lock.isEnabled() and not win.a_plan.isEnabled() and not win.a_agreement.isEnabled()
        win.session_chooser = lambda outcome: ctx(SID_A, "primary", "L01")
        out = win.project_view.open_path(proj.root)
        assert out.ok and win.workbench.wait_jobs(15_000)
        win.update_session_actions()
        assert win.a_lock.isEnabled() and not win.a_plan.isEnabled()
        assert not win.a_agreement.isEnabled()                 # 라벨링 세션 중에는 보고서를 열지 않는다(06 7-3)
        wb = win.workbench
        assert wb.set_mode(ph.SEGMENTS)
        f = wb.segment_form
        f.select_at(10_000)
        assert f.set_category("just_chatting") and f.set_confidence("certain") and f.set_rationale("00:00:05 합성")
        assert f.save() is not None and wb.mark_phase_done() is not None

        def run(d):
            d.go_confirm()
            d.confirm_box.setChecked(True)
            d.do_lock()
        doc = win.lock_session_dialog(run=run)
        assert doc is not None and wb.store.locked
        # 잠긴 1차 세션 창에서는 보고서를 열지 않는다(03 12-1: 1차는 reports 없음 · 06 7-3)
        assert not win.a_lock.isEnabled() and win.a_plan.isEnabled() and not win.a_agreement.isEnabled()
        plan = win.relabel_plan_dialog(run=lambda d: d.make("relabel_second", "L02"))
        assert plan is not None and os.path.isfile(os.path.join(proj.root, "relabel", plan["plan_id"] + ".json"))
        assert win.agreement_dialog(run=lambda d: d.run(SID_A, SID_A, str(tmp_path / "out"))) is None
        # 보기 전용으로 열어도, 계획된 재라벨(L02)이 잠기기 전에는 끈다(06 7-1)
        win.session_chooser = lambda outcome: None
        out = win.project_view.open_path(proj.root)
        assert out.ok and win.workbench.wait_jobs(15_000) and win.workbench.session is None
        win.update_session_actions()
        assert not win.a_agreement.isEnabled() and "06 7-1" in win.a_agreement.toolTip()
    finally:
        win.close()
