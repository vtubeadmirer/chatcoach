"""C단계 여섯째 묶음 검토 수정의 재현 시험. 합성 데이터만(지어낸 chat_id·근거 문장, tmp_path 안).

- 잠그기 전 검증(run_job_modal)이 도는 동안 창을 닫으면 닫기를 미루고 작업을 취소한다. 닫힌 store 로 잠그기 화면을 만들지 않는다.
  같은 틈에 [세션 잠그기]를 다시 눌러도 두 번째 워커가 돌지 않는다. 진행 대화상자는 처음부터 보인다(바로 모달).
- 조정 세션에서 조정자 자신(created_by)·조정 세션을 연 적이 있는 라벨러로 relabel_second 계획을 만들지 않는다(블라인드, 03 12-1).
- 채팅 색인: 읽는 사이 파일이 바뀌면 지문을 비워(stamp=None) 옛 색인이 빠른 지문 검사를 통과하지 않고, 캐시로 저장되지 않는다.
"""
import builtins
import hashlib
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app import chat_index as ci  # noqa: E402
from app import relabel  # noqa: E402
from tests.test_c6_ui import AD, MIN, P1, P2, _ctx, _open, proj  # noqa: E402,F401


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


# ---------------------------------------------------------------------------- 창 닫기·다시 들어오기 (mainwindow)
def _window(tmp_path, events, *, job_sec=0.6):
    from PySide6.QtWidgets import QWidget

    from app import updater
    from app.appspec import SPEC
    from app.ui.mainwindow import MainWindow
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    win = MainWindow(SPEC, updater.load_installed(SPEC), data_root, "", None, [], set())
    win.check_updates = lambda force=False: None

    class FakeStore:
        closed = False

    class FakeWB(QWidget):
        def __init__(self):
            super().__init__()
            self.store = FakeStore()

        def can_lock(self):
            return None

        def prepare_lock(self):
            return True

        def current_sample_job(self):
            def job(ctx):
                end = time.monotonic() + job_sec
                while time.monotonic() < end:
                    time.sleep(0.02)
                events.append("job_ran")
                return ["c_synthetic_1"]
            return job

        def resolve_unsaved(self, allow_cancel=False):
            pass

        def close_workbench(self, reason):
            events.append(("close_workbench", reason))
            self.store.closed = True

        def after_session_locked(self):
            events.append("after_locked")

    wb = FakeWB()
    win.workbench = wb
    return win, wb


def _patch_lock_dialog(monkeypatch, events):
    import app.ui.lock_dialog as ld

    class FakeLock:
        def __init__(self, parent, store, sample_verify, current_sample):
            events.append(("LockDialog_built", store.closed))
            self.lock_doc = None

        def exec(self):
            pass

        def deleteLater(self):
            pass
    monkeypatch.setattr(ld, "LockDialog", FakeLock)


def test_close_during_lock_prep_is_deferred_and_no_lock_dialog_on_closed_store(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QProgressDialog
    events = []
    _patch_lock_dialog(monkeypatch, events)
    win, wb = _window(tmp_path, events)
    win.show()
    seen = {}

    def try_close():
        seen["visible_dialogs"] = sum(1 for d in win.findChildren(QProgressDialog) if d.isVisible())
        events.append("user closes window")
        seen["accepted"] = win.close()
        seen["wb_after_close"] = win.workbench is wb
    QTimer.singleShot(100, try_close)
    assert win.lock_session_dialog() is None
    assert seen["visible_dialogs"] == 1                     # 진행 대화상자가 처음부터 떠 있다(바로 모달)
    assert seen["accepted"] is False and seen["wb_after_close"]   # 도는 동안에는 닫지 않는다
    assert not any(isinstance(e, tuple) and e[0] == "LockDialog_built" for e in events)
    assert ("close_workbench", "app_exit") not in events
    # 미뤄 둔 닫기는 작업이 끝난 뒤 이벤트 루프에서 일어난다
    deadline = time.monotonic() + 3
    while ("close_workbench", "app_exit") not in events and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert ("close_workbench", "app_exit") in events
    assert win.workbench is None and win.busy_jobs == 0
    win.deleteLater()


def test_second_lock_click_during_prep_does_not_start_second_worker(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    events = []
    _patch_lock_dialog(monkeypatch, events)
    win, wb = _window(tmp_path, events, job_sec=0.3)
    inner = {}
    QTimer.singleShot(50, lambda: inner.setdefault("v", win.lock_session_dialog()))
    try:
        win.lock_session_dialog()
        assert inner["v"] is None
        assert events.count("job_ran") == 1
        assert sum(1 for e in events if isinstance(e, tuple) and e[0] == "LockDialog_built") == 1
        assert ("LockDialog_built", False) in events
    finally:
        win.workbench = None
        win.close()
        win.deleteLater()


def test_lock_dialog_not_built_when_workbench_replaced_during_prep(qapp, tmp_path, monkeypatch):
    events = []
    _patch_lock_dialog(monkeypatch, events)
    win, wb = _window(tmp_path, events, job_sec=0.0)
    real = win.lock_prep

    def prep_then_store_closed(w):
        out = real(w)
        w.store.closed = True                      # 다른 길(프로젝트 바꾸기 등)로 store 가 닫힌 경우
        return out
    win.lock_prep = prep_then_store_closed
    try:
        assert win.lock_session_dialog() is None
        assert not any(isinstance(e, tuple) and e[0] == "LockDialog_built" for e in events)
    finally:
        win.workbench = None
        win.close()
        win.deleteLater()


# ---------------------------------------------------------------------------- relabel_second 계획의 블라인드
def test_second_plan_refuses_adjudicator_as_second_person(qapp, proj):  # noqa: F811
    from app.ui import adjudication_view as av
    st = _open(proj, _ctx(AD, "adjudication", "L01"))
    try:
        v = av.AdjudicationView(store=st, app_version="0.0.1", coverage=lambda: [(0, 40 * MIN)])
        assert v.make_second_plan(P2, "L01") is None and "조정자" in v.tool_msg.text()
        assert relabel.list_plans(proj) == []
        with pytest.raises(ValueError, match="조정자"):
            st.create_second_plan(P2, "L01", coverage=[(0, 40 * MIN)])
        assert relabel.list_plans(proj) == []
        assert not os.path.isdir(os.path.join(proj, relabel.RELABEL_DIR)) or \
            not [n for n in os.listdir(os.path.join(proj, relabel.RELABEL_DIR)) if n.startswith("RP-")]
    finally:
        st.close()


def test_second_plan_refuses_labeler_who_opened_an_adjudication_session(qapp, proj):  # noqa: F811
    from app.ui import adjudication_view as av
    # L03 이 이 프로젝트에서 조정 세션을 연 적이 있다(파일 이름만 본다 — 합성 빈 기록 파일)
    other_ad = "L03-AD-20261022-zz01"
    with open(os.path.join(proj, "labels", other_ad + ".abcd.jsonl"), "wb"):
        pass
    assert relabel.adjudicator_labelers(proj) >= {"L03"}
    st = _open(proj, _ctx(AD, "adjudication", "L01"))
    try:
        v = av.AdjudicationView(store=st, app_version="0.0.1", coverage=lambda: [(0, 40 * MIN)])
        assert v.make_second_plan(P1, "L03") is None and "조정 세션" in v.tool_msg.text()
        assert relabel.list_plans(proj) == []
        plan = v.make_second_plan(P1, "L04", rand4="sc04")   # 조정 세션을 연 적 없는 사람은 그대로 된다
        assert plan is not None and plan["labeler_id"] == "L04" and plan["created_by"] == "L01"
    finally:
        st.close()


def test_adjudicator_labelers_reads_names_only(tmp_path):
    d = tmp_path / "labels"
    d.mkdir()
    for n in ("L05-AD-20261022-aa01.abcd.jsonl", "L06-P-20261022-aa02.abcd.jsonl", "L07-AN-20261022-aa03.abcd.jsonl",
              "not-a-session.txt"):
        (d / n).write_bytes(b"{broken")
    assert relabel.adjudicator_labelers(str(tmp_path)) == frozenset({"L05"})
    assert relabel.adjudicator_labelers(str(tmp_path / "none")) == frozenset()


# ---------------------------------------------------------------------------- 채팅 색인: 읽는 사이 바뀜
def _line(i):
    cid = "c" + hashlib.sha256(f"synthetic-{i}".encode()).hexdigest()[:20]
    return json.dumps({"chat_id": cid, "t_ms": i * 1000, "kind": "chat", "type_code": 1}) + "\n"


def test_index_built_while_file_grows_is_not_fresh_and_not_cached(tmp_path, monkeypatch):
    p = str(tmp_path / "messages.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for i in range(1, 4):
            f.write(_line(i))
    real_open = builtins.open

    class Wrap:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def read(self, *a):
            with real_open(p, "a", encoding="utf-8") as g:
                g.write(_line(10))                    # 읽기 직전 한 줄(쓰는 쪽)
            data = self.f.read(*a)
            with real_open(p, "a", encoding="utf-8") as g:
                g.write(_line(11))                    # 읽은 뒤 또 한 줄
            return data
    calls = {"n": 0}

    def fake_open(path, mode="r", *a, **k):
        f = real_open(path, mode, *a, **k)
        if path == p and "b" in mode and calls["n"] == 0:
            calls["n"] += 1
            return Wrap(f)
        return f
    monkeypatch.setattr(ci, "open", fake_open, raising=False)
    cache = str(tmp_path / "cache" / "chat_index.bin")
    idx, status = ci.load_or_build(p, cache)
    monkeypatch.delattr(ci, "open", raising=False)
    assert status == ci.BUILT
    assert idx.stamp is None
    assert not ci.is_fresh(idx, p) and not ci.is_fresh(idx, p, verify_full=True)
    assert not os.path.exists(cache)                     # 지문 없는 색인은 캐시로 남기지 않는다
    # 다음에 열면 다시 만들어 지금 파일과 맞는다
    idx2, status2 = ci.load_or_build(p, cache)
    with real_open(p, "rb") as f:
        n_lines = sum(1 for _ in f)
    assert status2 == ci.BUILT and len(idx2.chat_ids) == n_lines and ci.is_fresh(idx2, p, verify_full=True)
    assert os.path.exists(cache)
