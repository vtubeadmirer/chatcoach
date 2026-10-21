"""C단계 여섯째 묶음 남은 정리 (15 5-2 의 1·2·6·7·10·11·12·13·14·15·16번, 08 D-14 2).

화면 없이(offscreen) 돈다. 채팅·라벨은 모두 지어낸 값(tmp_path 안)이다. 스크린샷 정리 시험은 임시 폴더와 가짜 휴지통만 쓴다.
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def _outside(w, top) -> int:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget
    n = 0
    for c in w.findChildren(QWidget):
        if c.isVisibleTo(top) and c.mapTo(top, QPoint(c.width(), 0)).x() > top.width() + 1:
            n += 1
    return n


# ---------------------------------------------------------------------------- 1. 반응 편집기 오른쪽 밖 24
def test_evidence_widget_rebuild_while_hidden_stays_inside(qapp):
    """숨은 칸에서 set_config 로 다시 만들어도 부품이 기본 640px 로 남지 않는다(실제 창의 '끝 656 같음' 24개)."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from app.core import rules
    from app.ui.forms.evidence_widget import EVIDENCE_KEYS, EvidenceWidget
    host = QWidget()
    lay = QVBoxLayout(host)
    inner = QWidget()
    il = QVBoxLayout(inner)
    ev = EvidenceWidget(cfg=rules.V0_1)
    il.addWidget(ev)
    lay.addWidget(inner)
    host.resize(420, 500)
    host.show()
    qapp.processEvents()
    ev.boxes[EVIDENCE_KEYS[0]].setChecked(True)
    ev.notes[EVIDENCE_KEYS[0]].setText("합성 메모")
    inner.hide()
    qapp.processEvents()
    ev.set_config(rules.V0_1)
    qapp.processEvents()
    assert ev.width() < 640 and _outside(ev, ev) == 0              # 숨은 채로 재도 칸 안
    inner.show()
    for _ in range(3):
        qapp.processEvents()
    assert _outside(ev, host) == 0
    v = ev.value()
    assert v[EVIDENCE_KEYS[0]]["present"] is True and v[EVIDENCE_KEYS[0]]["note"] == "합성 메모"   # 값은 그대로
    host.close()


# ---------------------------------------------------------------------------- 2. NARROW_PX 1200 · 870 제한 없음
def test_narrow_px_is_1200_and_workbench_has_no_870_clamp():
    from app.ui import adjudication_view as av
    from app.ui import workbench as wbm
    assert av.NARROW_PX == 1200
    assert not hasattr(wbm, "ADJ_SIDE_MIN_PX")


# ---------------------------------------------------------------------------- 6·7. 출처 대상 둘 이상 경고 · 대상 파일 ID
def _cid(i):
    return "c%020x" % i


def test_origin_view_warns_on_two_target_files_and_reports_ids(qapp):
    from app.core import origin as og
    from app.ui import origin_view as ov
    t1 = og.build_targets(chat_ids=[_cid(i) for i in range(3)], created_at="2026-10-21T15:00:00+09:00",
                          created_by="L01", basis="manual")
    t2 = og.build_targets(chat_ids=[_cid(i) for i in range(2, 5)], created_at="2026-10-22T15:00:00+09:00",
                          created_by="L01", basis="manual")
    msgs = [{"chat_id": _cid(i), "t_ms": 60_000 + i * 5_000, "text": f"합성{i}"} for i in range(6)]
    got: list = []
    v = ov.OriginView(lambda d: {"kind": "origin", "body": d["body"]}, lambda: None)
    v.targetsLoaded.connect(got.append)
    v.load_context(session_kind="origin_review", session_id="L01-OR-20261021-a1b2", targets=[t1], messages=msgs)
    assert v.multi_targets_warning() == "" and not v.multi_warn.isVisibleTo(v)
    assert v.target_ids() == [t1["target_id"]]
    v.load_context(session_kind="origin_review", session_id="L01-OR-20261021-a1b2", targets=[t2, t1], messages=msgs)
    w = v.multi_targets_warning()
    assert "2개" in w and "겹친 대상 1건" in w and v.multi_warn.text() == w and v.multi_warn.isVisibleTo(v)
    assert v.target_ids() == sorted([t1["target_id"], t2["target_id"]])
    refs = v.target_refs()
    assert [r["count"] for r in refs] == [3, 3] and all(len(r["chat_ids_sha256"]) == 64 for r in refs)
    assert got[-1] == refs and len(got) == 2
    assert all("합성" not in str(r) for r in refs)                 # 본문은 들어가지 않는다
    # 볼 수 없는 세션은 비운다
    v.load_context(session_kind="primary", targets=[t1, t2], messages=msgs)
    assert v.target_ids() == [] and v.multi_targets_warning() == "" and got[-1] == []


# ---------------------------------------------------------------------------- 10. drop_cache
def test_drop_cache_removes_tmp_and_empty_folder(tmp_path):
    from app import chat_index as ci
    root = str(tmp_path / "data")
    p = ci.cache_path_for("pid-x", root)
    os.makedirs(os.path.dirname(p))
    for name in (p, p + ".tmp123"):
        with open(name, "wb") as f:
            f.write(b"x")
    assert ci.drop_cache("pid-x", root) is True
    assert not os.path.exists(os.path.dirname(p))                     # 비었으니 폴더도 지움
    assert ci.drop_cache("pid-x", root) is False
    os.makedirs(os.path.dirname(p))
    other = os.path.join(os.path.dirname(p), "keep.txt")
    with open(other, "w") as f:
        f.write("x")
    assert ci.drop_cache("pid-x", root) is False and os.path.isfile(other)   # 다른 파일이 있으면 폴더는 둔다


# ---------------------------------------------------------------------------- 11·16. gui_smoke 도우미
def _smoke():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import gui_smoke
    finally:
        sys.path.pop(0)
    return gui_smoke


def test_screenshot_cleanup_counts_then_trashes_only_prefixed_png(tmp_path, capsys):
    gs = _smoke()
    folder = tmp_path / "screens"
    folder.mkdir()
    for n in ("c4_a.png", "c5_flow.png", "c6_adj.png", "c7_new.png", "c6_note.txt", "readme.png"):
        (folder / n).write_bytes(b"\x89PNG" + b"0" * 10)
    (folder / "c6_dir.png").mkdir()
    got = [os.path.basename(p) for p in gs.screenshot_cleanup_targets(str(folder), ["c4", "c5", "c6"])]
    assert got == ["c4_a.png", "c5_flow.png", "c6_adj.png"]
    with pytest.raises(ValueError):
        gs.screenshot_cleanup_targets(str(folder), ["..\\x"])
    trashed: list = []
    assert gs.screenshot_cleanup(["c4", "c5", "c6"], yes=False, folder=str(folder),
                                 trash=lambda ps: trashed.extend(ps) or True) == 0
    assert trashed == [] and "3개" in capsys.readouterr().out          # --yes 없으면 세기만

    def fake_trash(ps):
        trashed.extend(ps)
        for x in ps:
            os.remove(x)
        return True
    assert gs.screenshot_cleanup(["c4", "c5", "c6"], yes=True, folder=str(folder), trash=fake_trash) == 0
    assert len(trashed) == 3 and (folder / "c7_new.png").exists() and (folder / "readme.png").exists()
    out = capsys.readouterr().out
    assert "c4_a" not in out                                         # 파일 이름은 찍지 않는다
    assert gs.screenshot_cleanup(["c7"], yes=True, folder=str(folder), trash=lambda ps: False) == 1
    assert (folder / "c7_new.png").exists()                          # 휴지통이 안 되면 지우지 않는다


def test_gui_smoke_project_not_required_for_cleanup():
    gs = _smoke()
    a = gs.parse_args(["--screenshot-cleanup"])
    assert a.project is None and a.screenshot_cleanup == "c4,c5,c6" and a.yes is False


def test_step_list_matches_c5_49_with_renames():
    gs = _smoke()
    assert len(gs.STEPS_C5) == 49 and len(set(gs.STEPS_C5)) == 49
    want = gs.expected_steps()
    assert len(want) == 49                                           # 49 − 5 + 2(합친 이름) + 3(새 단계)
    assert "agree_a_label" in want and "agree_b_label" in want and "cache_drop" in want
    assert not {"agree_a_segments", "agree_open_b"} & set(want)
    missing, extra = gs.compare_steps(want[:-1] + ["new_step"])
    assert missing == [want[-1]] and extra == ["new_step"]
    # 지금 gui_smoke 안의 단계 이름(문자열·f"{p}_…")이 모두 목록에 있다 — 새 단계를 더하면 목록도 고치게
    src = open(os.path.join(ROOT, "tools", "gui_smoke.py"), encoding="utf-8").read()
    import re
    names = set(re.findall(r'self\.check\("([a-z_0-9]+)"', src))
    pref = set(re.findall(r'self\.check\(f"\{p\}_([a-z_0-9]+)"', src))
    names |= {f"{p}_{s}" for s in pref for p in ("agree", "adj")}
    names -= {"labeling_flow", "agreement_flow", "adjudication_flow"}  # 건너뜀·예외 때만 찍는 묶음 이름
    names -= {"agree_b_missed", "agree_tools", "agree_cancel_plan", "agree_origin", "agree_adjudicate",
              "adj_analysis"}                                       # 도우미가 흐름마다 다르게 찍는 것
    assert names <= set(want), sorted(names - set(want))


# ---------------------------------------------------------------------------- 12. 잠그기 직전 구간표가 바뀌면 다시 계산
class _LockStore:
    def __init__(self):
        from app import locking
        self.summary = locking.LockSummary(session_id="L02-R2-20261022-b001", session_kind="relabel_second",
                                           segments=1, jc_segments=1, reactions=3, label_counts={},
                                           status_counts={"active": 4})
        self.calls: list = []

    def lock_summary(self):
        return self.summary

    def lock_session(self, sample_verify=None, current_sample=None):
        self.calls.append(list(current_sample) if current_sample is not None else None)
        return {"session_id": self.summary.session_id}


def _lock(dlg):
    dlg.go_confirm()
    dlg.confirm_box.setChecked(True)
    return dlg.do_lock()


def test_lock_dialog_recomputes_current_sample_when_segments_changed(qapp):
    from app.ui.lock_dialog import LockDialog
    st = _LockStore()
    d = LockDialog(None, store=st, current_sample=["c_a", "c_b"], recheck=lambda: ["c_a", "c_c", "c_d"])
    assert _lock(d) and st.calls == [["c_a", "c_c", "c_d"]] and d.rechecked
    st = _LockStore()
    d = LockDialog(None, store=st, current_sample=["c_a"], recheck=lambda: None)
    assert _lock(d) and st.calls == [["c_a"]] and not d.rechecked

    def broken():
        raise RuntimeError("합성 실패")
    st = _LockStore()
    d = LockDialog(None, store=st, current_sample=["c_a"], recheck=broken)
    assert not _lock(d) and st.calls == [] and not d.error_label.isHidden()     # 잠그지 않고 사유를 보인다
    # 1차(current_sample 없음)는 부르지 않는다
    st = _LockStore()
    d = LockDialog(None, store=st, recheck=broken)
    assert _lock(d) and st.calls == [None]


def test_lock_dialog_finds_workbench_recheck_on_parent(qapp):
    from PySide6.QtWidgets import QWidget

    from app.ui.lock_dialog import LockDialog
    parent = QWidget()
    parent.workbench = types.SimpleNamespace(current_sample_recheck=lambda: ["c_z"])
    st = _LockStore()
    d = LockDialog(parent, store=st, current_sample=["c_a"])
    assert _lock(d) and st.calls == [["c_z"]]
    parent.deleteLater()


def test_workbench_current_sample_recheck_uses_segment_digest():
    from app.ui import workbench as wbm
    segs = [{"entity_id": "e1", "id": "v1", "body": {"start_ms": 0, "end_ms": 600_000, "category": "just_chatting",
                                                       "rationale": "합성 근거"}}]
    stub = types.SimpleNamespace(segment_heads=lambda: list(segs))
    d1 = wbm.Workbench.segments_digest(stub)
    segs[0]["body"]["rationale"] = "다른 합성 근거"                  # 근거는 지문에 들어가지 않는다
    assert wbm.Workbench.segments_digest(stub) == d1
    runs: list = []
    stub.segments_digest = lambda: wbm.Workbench.segments_digest(stub)
    stub.current_sample_job = lambda: (lambda ctx: runs.append(ctx.check()) or ["c_new"])
    stub._current_sample_basis = d1
    assert wbm.Workbench.current_sample_recheck(stub) is None and runs == []   # 그대로면 다시 계산 안 함
    segs[0]["body"]["end_ms"] = 540_000
    assert wbm.Workbench.segments_digest(stub) != d1
    assert wbm.Workbench.current_sample_recheck(stub) == ["c_new"] and runs == [None]
    stub._current_sample_basis = None                                 # 계산한 적 없으면(1차) 부르지 않음
    assert wbm.Workbench.current_sample_recheck(stub) is None


# ---------------------------------------------------------------------------- 13. ③ 직접·높음 + ④ 기록 → 경고 1줄
def _missed_stub():
    from app.ui import workbench as wbm
    notes, said = [], []
    stub = types.SimpleNamespace(
        _label_heads={"missed_candidate": {
            "m1": {"kind": "missed_candidate", "body": {"chat_id": "c1", "scope": "sample"}},
            "m2": {"kind": "missed_candidate", "body": {"chat_id": "c2", "scope": "extra"}}}},
        MISSED_STALE_WARNING=wbm.Workbench.MISSED_STALE_WARNING, _note=notes.append, _say=said.append)
    stub.missed_heads_for = lambda c: wbm.Workbench.missed_heads_for(stub, c)
    return wbm.Workbench._warn_missed_after_reaction, stub, notes, said


@pytest.mark.parametrize("body,want", [
    ({"chat_id": "c1", "result": "graded", "grade": "direct"}, True),
    ({"chat_id": "c1", "result": "graded", "grade": "likely"}, True),
    ({"chat_id": "c1", "result": "graded", "grade": "related_only"}, False),   # 아직 ④ 대상
    ({"chat_id": "c1", "result": "unconfirmed", "grade": None}, False),
    ({"chat_id": "c2", "result": "graded", "grade": "direct"}, False),         # extra 는 ③ 과 상관없음
    ({"chat_id": "c3", "result": "graded", "grade": "direct"}, False),         # ④ 기록 없음
])
def test_warn_when_reaction_fixed_to_direct_with_missed_record(body, want):
    fn, stub, notes, said = _missed_stub()
    assert fn(stub, {"kind": "reaction", "body": body}) is want
    assert (len(notes), len(said)) == ((1, 1) if want else (0, 0))
    if want:
        assert "④" in notes[0] and "일치도에서 빠집니다" in notes[0]
    assert fn(stub, {"kind": "segment", "body": body}) is False


# ---------------------------------------------------------------------------- 15. 작업대 일 바쁨 신호
def test_workbench_jobs_busy_signal(qapp, tmp_path):
    import threading

    from tests.test_c6_integration import _open
    from tests.test_integration_c4 import close_wb, ctx, make_long_project
    proj = make_long_project(tmp_path)
    wb = _open(qapp, proj, ctx("L01-P-20261022-jb01", "primary", "L01"))
    seen: list = []
    try:
        wb.jobsBusyChanged.connect(seen.append)
        assert wb.jobs_busy() is False
        gate = threading.Event()
        done: list = []
        wb._run_job("t_job", lambda cancelled: gate.wait(5) and "ok", lambda r, e: done.append(r), False)
        assert wb.jobs_busy() and seen == [True]
        wb._run_job("t_job2", lambda cancelled: "ok2", lambda r, e: done.append(r), False)
        assert seen == [True]                                           # 바뀔 때만
        gate.set()
        assert wb.wait_jobs(5000)
        assert sorted(done) == ["ok", "ok2"] and seen == [True, False] and not wb.jobs_busy()
        wb._run_job("t_sync", lambda cancelled: 1, lambda r, e: None, True)
        assert seen == [True, False]                                    # 바로 도는 일은 신호 없음
    finally:
        close_wb(wb)
