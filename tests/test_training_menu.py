"""메인 창 '훈련 → 스트리머 훈련' 통합과 훈련 노출 (08 D-16·D-18 6·7·8, 18 6-2·6-5 3) — app/ui/mainwindow.py ·
app/core/exposure.py 규칙 5 · app/exposure_io.project_trainings.

합성 데이터만 쓴다: 합성 최종 세트 프로젝트(tests.test_final_set.proj — 1차·같은 사람 재라벨 잠김, 채팅 6개)와 합성 세션 ID.
실제 채팅·닉네임·전사문·시범 프로젝트 폴더는 쓰지 않는다. 시범 이름 사본은 tmp_path 안에 만들고 shutil.rmtree 로 지운다.
사람 클릭 수(D-16): 메뉴 '훈련' 1 + '스트리머 훈련' 1 = 2, 고르는 창·입력 칸 0. 라벨링 세션에서도 같다
('보기 전용으로 다시 열고 훈련', D-19 3) — 저장 안 한 구간 변경이 있을 때만 묻는 창 1 이 더해진다.
"""
import json
import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from app import exposure_io  # noqa: E402
from app import reference_set as rsm  # noqa: E402
from app import training_store as ts  # noqa: E402
from app.core import exposure as ex  # noqa: E402
from app.core import training as trc  # noqa: E402
from tests.test_final_set import _clear_locks, cid, proj  # noqa: E402,F401
from tests.test_integration_c4 import PARTS, ctx, qapp  # noqa: E402,F401
from tests.test_trainer_view import FakePlayer  # noqa: E402

P2 = "L01-P-20261031-bb22"
T0 = "2026-10-31T10:00:00+09:00"


# ----------------------------------------------------------------------------- 규칙 5 (순수)
def _s(sid, kind, created, locked=None):
    return {"session_id": sid, "session_kind": kind, "created_at": created, "locked_at": locked}


def test_rule5_training_exposes_unlocked_and_later_sessions():
    sessions = [_s("L01-P-20261030-aa01", "primary", "2026-10-30T09:00:00+09:00", "2026-10-30T12:00:00+09:00"),
                _s("L01-P-20261031-aa02", "primary", "2026-10-31T09:00:00+09:00"),              # 열 때 안 잠김
                _s("L02-R2-20261101-aa03", "relabel_second", "2026-11-01T09:00:00+09:00"),       # 그 뒤에 만듦
                _s("L01-AN-20261101-aa04", "analysis", "2026-11-01T09:00:00+09:00"),             # 라벨링 아님
                _s("L01-P-20261101-aa05", "primary", None)]                                      # 시각 모름
    v = ex.compute_exposure(sessions, trainings=[{"session_id": "L01-TR-20261031-zz01", "started_at": T0}])
    assert v.exposed() == frozenset()                                     # AI 결과 노출이 아니다
    assert v.exposed_any() == {"L01-P-20261031-aa02", "L02-R2-20261101-aa03", "L01-P-20261101-aa05"}
    assert v.exposed(ex.TRAINING_WHAT) == v.exposed_any()
    assert v.reasons("L01-P-20261031-aa02") == (ex.REASON_TRAINING_OPENED,)
    assert v.reasons("L01-P-20261101-aa05") == (ex.REASON_TIME_UNKNOWN,)
    assert v.view_exposed("L01-P-20261031-aa02", ["ai_results"]) == ["ai_results", "other_session_labels"]
    # 열기 시각을 모르면 맨 처음 — 잠긴 세션도 노출로 본다(블라인드 쪽으로 안전하게)
    v2 = ex.compute_exposure(sessions[:1], trainings=[{"session_id": "L01-TR-20261031-zz01", "started_at": None}])
    assert v2.exposed_any() == {"L01-P-20261030-aa01"}


def _log(root, labeler="L01", seconds=0):
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    return ts.TrainingLog.create(root, labeler, clock=lambda: datetime(2026, 10, 31, 10, 0, seconds, tzinfo=kst),
                                 app_version="0.0.0", check_pilot=False)


def test_project_trainings_reads_start_only(tmp_path):
    root = str(tmp_path / "p")
    os.makedirs(root)
    a = _log(root)
    a.start(project_id="11111111-2222-4333-8444-666666666666", range_ms=(0, 1000),
            reference={"source": "manual_csv", "sha256": "0" * 64, "count": 1, "file": "manual-x.csv"})
    a.toggle(cid(1), 10)
    _log(root, "L02")                                               # 빈 파일: start 를 못 씀 → 세지 않음
    c = _log(root, "L03")
    with open(c.path, "ab") as f:                                   # 형식에 맞지 않는 줄만 → 시각 모름
        f.write(b'{"v": 1, "kind": "training.start"}\n')
    got, warns = exposure_io.project_trainings(root)
    by = {g["session_id"]: g["started_at"] for g in got}
    assert by == {a.session_id: "2026-10-31T10:00:00.000+09:00", c.session_id: None}
    assert len(warns) == 1 and c.session_id in warns[0]
    # compute_for: 그 뒤 만든 라벨링 세션은 사람 라벨 노출, 경고에는 세션 ID·사유 코드만
    view, w2 = exposure_io.compute_for(root, [ex.SessionInfo("L01-P-20261101-bb01", "primary",
                                                             "2026-11-01T09:00:00+09:00")],
                                       include_unseals=False)
    assert view.exposed_any() == {"L01-P-20261101-bb01"}
    assert any("training_opened" in x and "사람 라벨" in x for x in w2)
    view0, _ = exposure_io.compute_for(root, [ex.SessionInfo("L01-P-20261101-bb01", "primary",
                                                             "2026-11-01T09:00:00+09:00")],
                                       include_unseals=False, include_training=False)
    assert view0.exposed_any() == frozenset()


# ----------------------------------------------------------------------------- 채팅 표 API (trainer_ui 담당 요청)
def test_chat_panel_reveal_click_and_tooltip(qapp):  # noqa: F811
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from app.ui.chat_panel import ChatPanel
    p = ChatPanel()
    p.resize(500, 400)
    p.show()
    msgs = [{"chat_id": cid(i), "t_ms": i * 1000, "kind": "text", "viewer_id": "vw_aaaaaaaaaaaa",
             "nickname_masked": "시**", "text": "합성 본문"} for i in range(1, 11)]
    p.set_messages(msgs)
    p.set_reveal_until(3_500)
    assert p.row_count() == 3 and p.total_count() == 10
    p.set_time_ms(9_000)                                            # 재생 위치가 앞서도 드러난 행 안에서만 하이라이트
    assert p.highlighted_chat_id() == cid(3)
    p.set_reveal_until(6_000)
    assert p.row_count() == 6
    p.set_reveal_until(2_000)                                       # 앞으로 돌아가면 줄어든다
    assert p.row_count() == 2 and p.select_chat(cid(5)) is False
    p.set_reveal_until(None)
    assert p.row_count() == 10
    got = []
    p.chatClicked.connect(lambda c, t: got.append((c, t)))
    rect = p.view.visualRect(p.model.index(1, 3))
    QTest.mouseClick(p.view.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert got == [(cid(2), 2000)]
    p.set_label_marks({cid(2): "✓ 고름"}, own=True, tooltip="합성 풍선")
    assert p.model.data(p.model.index(1, 4), Qt.ToolTipRole) == "합성 풍선"
    p.set_label_marks({cid(2): "✓ 직접"}, own=True)                 # 없으면 라벨링 세션 기본 글
    assert "이미 매긴" in p.model.data(p.model.index(1, 4), Qt.ToolTipRole)
    p.close()
    p.deleteLater()


# ----------------------------------------------------------------------------- 메인 창
@pytest.fixture
def win(qapp, tmp_path, monkeypatch):  # noqa: F811
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    from app.ui import trainer_view as tv
    from app.ui import workbench as wbm
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))
    real = tv._default_part
    monkeypatch.setattr(tv, "_default_part", lambda kind: FakePlayer if kind == "player" else real(kind))
    data_root = str(tmp_path / "appdata")
    os.makedirs(data_root, exist_ok=True)
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    w = mw.MainWindow(SPEC, inst, data_root, str(tmp_path / "log.txt"), None, [], set())
    w.check_updates = lambda force=False, on_start=False: None
    w.shown = []
    w.warn = lambda title, text: w.shown.append(("warn", text))
    yield w
    w.close()


def _open(win, root, session, media=True):
    win.session_chooser = lambda outcome: session
    out = win.project_view.open_path(root)
    assert out.ok and win.workbench.wait_jobs(15_000)
    if media:
        win.workbench.media_path = os.path.join(root, "합성영상.mp4")    # 파일은 없어도 된다(가짜 플레이어)
    win.update_training_actions()
    return win.workbench


def _lines(root):
    sids = ts.list_sessions(root)
    return [r["kind"] for s in sids for r in ts.read_valid(ts.session_path(root, s))[0]]


def test_menu_one_click_opens_trainer_and_logs_exposure(win, proj):  # noqa: F811
    root = proj["root"]
    titles = [a.text() for a in win.menuBar().actions()]
    assert titles.index("훈련(&T)") == titles.index("분석(&I)") + 1
    assert [a.text() for a in win.m_training.actions() if a.isVisible() and not a.isSeparator()] == \
        ["스트리머 훈련(&S)", "시청자 훈련(&W)", "가상 채팅 가려내기(&D)"]   # G 시청자 훈련(D-20 2)·H-5 가려내기는 구분선 아래
    win.update_training_actions()                                  # 프로젝트 없음
    assert not win.a_streamer_training.isEnabled() and win.a_training_reason.isVisible()

    wb = _open(win, root, None, media=False)                       # 보기 전용, 영상 없음 → 꺼짐, 까닭 한 줄
    assert not win.a_streamer_training.isEnabled() and "영상" in win.a_training_reason.text()
    wb.media_path = os.path.join(root, "합성영상.mp4")
    win.update_training_actions()
    assert win.a_streamer_training.isEnabled() and not win.a_training_reason.isVisible()

    win.a_streamer_training.trigger()                              # 메뉴 한 번 = 열림(고르는 창 없음)
    view = win.training_view
    assert view is not None and win.stack.currentWidget() is view and view.state() == "training"
    assert view.is_muted() and view.pick_count() == 0
    assert _lines(root) == [trc.K_START]                           # 연 순간 start 줄 = 노출 기록
    win.update_training_actions()
    assert not win.a_streamer_training.isEnabled() and "이미" in win.a_training_reason.text()

    # 흐르는 채팅: 재생 위치(구간 시작 0)까지 온 채팅만 표에 있다 — 합성 채팅은 110초부터
    assert view.chat.row_count() == 0
    view.seek_vod(125_000)
    assert view.chat.row_count() == 2
    assert view.toggle_pick(cid(1)) == "pick" and view.toggle_pick(cid(2)) == "pick"
    assert view.toggle_pick(cid(2)) == "unpick"                    # 같은 채팅 다시 = 취소
    tip = view.chat.model.data(view.chat.model.index(0, 4), 3)    # ToolTipRole
    assert tip and "취소" in tip
    assert view.show_compare()
    nums = view.compare_view.summary()
    # 기준 = 1 direct · 2·3 likely. 구간 끝 전 견주기라 125초까지 화면에 나온 채팅의 원래 반응만 센다(until_ms)
    assert (nums["same"], nums["orig_only"]) == (1, 1)
    assert view.compare_result().until_ms == 125_000
    assert view.chat.row_count() == 6                              # 견주기 뒤에는 다 보인다
    view.btn_close.click()                                         # [닫기] 1번 → 작업대로
    assert win.training_view is None and win.stack.currentWidget() is wb
    assert _lines(root) == [trc.K_START, trc.K_PICK, trc.K_PICK, trc.K_UNPICK, trc.K_COMPARE, trc.K_END]
    blob = b"".join(open(ts.session_path(root, s), "rb").read() for s in ts.list_sessions(root))
    assert "합성 본문".encode() not in blob and "시**".encode() not in blob

    # 훈련을 연 뒤 이 프로젝트에서 만든 라벨링 세션은 대표값에서 빠진다(이미 잠긴 1차·재라벨은 그대로)
    view_x, _w = exposure_io.compute_for(root, [ex.SessionInfo(P2, "primary", "2099-01-01T00:00:00+09:00"),
                                                ex.SessionInfo("L01-P-20261024-ab12", "primary",
                                                               "2000-01-01T09:00:00+09:00",       # 훈련 전에 잠김
                                                               "2000-01-01T10:00:00+09:00")],
                                         include_unseals=False)
    assert view_x.exposed_any() == {P2}
    win.show_home()
    assert win.training_view is None


def _slow_poll(monkeypatch):
    """다시 연 뒤 자동 열기 타이머가 시험 도중(wait_jobs 의 이벤트 처리)에 돌지 않게 — 시험은 _try_pending_training 을
    직접 부른다."""
    from app.ui import mainwindow as mw
    monkeypatch.setattr(mw, "TRAINING_REOPEN_POLL_MS", 600_000)


def test_labeling_session_reopens_view_only_and_trains(win, proj, monkeypatch):  # noqa: F811
    """D-19 3: 라벨링 세션에서 메뉴 한 번 = 세션을 닫고(잠금 풀기) 세션 화면 없이 보기 전용으로 다시 연 뒤 훈련.
    저장 안 한 변경이 없으면 사람 클릭은 메뉴 2 뿐(묻는 창 0). 훈련 화면을 라벨링 세션 위에 쌓지 않는다."""
    _slow_poll(monkeypatch)
    root = proj["root"]
    # 둘째 1차 세션(P2)을 열면 최종 세트가 '1차 세션 하나' 규칙으로 안 만들어진다 — 열기 전 기준을 그대로 쓰게 한다
    ref0, _w = rsm.load_final_set_reference(root)
    assert ref0 is not None
    reads = []
    monkeypatch.setattr(rsm, "load_final_set_reference", lambda r: reads.append(r) or (ref0, None))
    shown_chooser = []
    wb = _open(win, root, ctx(P2, "primary", "L01"))
    assert reads == []                                             # 라벨링 세션에서는 기준(다른 세션 라벨)을 읽지 않는다
    win.session_chooser = lambda outcome: shown_chooser.append(1) or ctx(P2, "primary", "L01")   # 다시 열 때 안 불려야 함
    assert win.a_streamer_training.isEnabled() and not win.a_training_reason.isVisible()
    assert "보기 전용" in win.a_streamer_training.text() and "대표값" in win.a_streamer_training.toolTip()
    asked = []
    monkeypatch.setattr(wb, "resolve_unsaved", lambda allow_cancel=True: asked.append(allow_cancel) or True)
    win.a_streamer_training.trigger()                              # 메뉴 한 번
    assert asked[0] is True                                        # 저장 안 한 변경 확인은 취소할 수 있게(그 뒤 닫기의
    #                                                                두 번째 확인은 이미 처리돼 묻는 창이 뜨지 않는다)
    assert win.training_view is None and ts.list_sessions(root) == []   # 라벨링 세션 위에서는 열지 않았다
    nwb = win.workbench
    assert nwb is not wb and nwb.session is None and shown_chooser == []   # 세션 화면 없이 보기 전용
    assert getattr(nwb.outcome, "lock", None) is not None          # 라벨링 세션의 잠금이 풀려 새 작업대가 다시 잡았다
    assert win._pending_training is not None
    assert nwb.wait_jobs(15_000)
    nwb.media_path = os.path.join(root, "합성영상.mp4")
    view = win._try_pending_training()
    assert view is not None and win.training_view is view and win.stack.currentWidget() is view
    assert win._pending_training is None and _lines(root) == [trc.K_START]
    assert win._training_labeler() == "L01"                        # 다시 열기 전 세션의 라벨러(묻지 않음)
    win.update_training_actions()
    assert win.a_streamer_training.text() == "스트리머 훈련(&S)"
    view.btn_close.click()
    assert win.training_view is None and win.stack.currentWidget() is nwb
    win.show_home()


def test_reopen_cancel_on_unsaved_stays_in_session(win, proj, monkeypatch):  # noqa: F811
    """저장 안 한 변경을 묻는 창에서 '취소'(또는 반응 편집 중)면 그대로 머문다 — 세션·잠금·훈련 기록 그대로."""
    _slow_poll(monkeypatch)
    wb = _open(win, proj["root"], ctx(P2, "primary", "L01"))
    monkeypatch.setattr(wb, "resolve_unsaved", lambda allow_cancel=True: False)
    assert win.streamer_training() is None
    assert win.workbench is wb and wb.session is not None and win._pending_training is None
    assert win.training_view is None and ts.list_sessions(proj["root"]) == []
    win.show_home()


def test_reopen_stays_when_missed_form_unsaved(win, proj, monkeypatch):  # noqa: F811
    """재라벨 ④ 놓친 후보 폼에 저장 전 입력이 있으면 '다시 열고 훈련' 이 세션을 닫지 않는다(입력이 사라지지 않게).
    묻는 창 없이 상태 줄 한 줄(D-16). 검토 의견 재현."""
    from app.core import phase as ph
    _slow_poll(monkeypatch)
    wb = _open(win, proj["root"], ctx(P2, "primary", "L01"))

    class Missed:
        def has_unsaved(self):
            return True
    asked = []
    monkeypatch.setattr(wb, "resolve_unsaved", lambda allow_cancel=True: asked.append(1) or True)
    old_mode, old_mf = wb.mode, wb.missed_form
    wb.mode, wb.missed_form = ph.MISSED, Missed()
    try:
        assert win.reopen_view_only_and_train() is False
        assert win.workbench is wb and wb.session is not None and win._pending_training is None
        assert asked == [] and "④" in win.statusBar().currentMessage()
        assert win.training_view is None and ts.list_sessions(proj["root"]) == []
    finally:
        wb.mode, wb.missed_form = old_mode, old_mf
    win.show_home()


def test_blocked_start_leaves_no_empty_training_file(win, proj, monkeypatch):  # noqa: F811
    """훈련 화면이 start 에서 막히면(섞기 막힘 등) 먼저 만든 0바이트 training\\<id>.jsonl 을 지운다. 검토 의견 재현."""
    from app.ui import trainer_view as tv
    root = proj["root"]
    _open(win, root, None)

    def blocked(self, **_k):
        self.set_blocked("합성 막힘")
        return False
    monkeypatch.setattr(tv.TrainerView, "start", blocked)
    for _ in range(2):                                             # 누를 때마다 쌓이지 않는다
        assert win.streamer_training() is None
        assert win.statusBar().currentMessage() == "합성 막힘"
    assert ts.list_sessions(root) == []
    tdir = ts.training_dir(root)
    assert not os.path.isdir(tdir) or os.listdir(tdir) == []
    win.show_home()


def test_training_labeler_hint_cleared_on_close(win, proj):  # noqa: F811
    """다시 열기 전 세션의 라벨러 힌트는 그 작업대가 닫히면 비운다(다른 프로젝트 훈련 기록으로 새지 않게)."""
    _open(win, proj["root"], None)
    win._training_labeler_hint = "L02"
    assert win._training_labeler() == "L02"
    win.show_home()
    assert win._training_labeler_hint is None


def test_pending_training_dropped_when_project_changes(win, proj, monkeypatch):  # noqa: F811
    _slow_poll(monkeypatch)
    wb = _open(win, proj["root"], ctx(P2, "primary", "L01"))
    monkeypatch.setattr(wb, "resolve_unsaved", lambda allow_cancel=True: True)
    assert win.reopen_view_only_and_train() is True
    win.workbench.wait_jobs(15_000)
    win.show_home()                                                # 기다리는 사이 닫음 → 열지 않는다
    assert win._try_pending_training() is None and win._pending_training is None
    assert ts.list_sessions(proj["root"]) == []


def test_pilot_labeling_session_keeps_menu_off(win, proj, tmp_path, monkeypatch):  # noqa: F811
    """시범 프로젝트(잠기기 전)는 라벨링 세션에서도 메뉴가 꺼진 채다 — 다시 열지 않는다(D-19 3, D-18 7)."""
    dst = str(tmp_path / "data" / "projects" / "pilot-합성")
    shutil.copytree(proj["root"], dst)
    try:
        calls = []
        monkeypatch.setattr(rsm, "pilot_sessions_locked", lambda root: calls.append("locked") or (True, []))
        monkeypatch.setattr(rsm, "load_final_set_reference", lambda root: calls.append("final") or (None, "x"))
        monkeypatch.setattr(rsm, "load_manual_reference", lambda root: calls.append("manual"))
        wb = _open(win, dst, ctx(P2, "primary", "L01"))
        assert not win.a_streamer_training.isEnabled()
        assert "시범" in win.a_training_reason.text()
        assert win.streamer_training() is None
        assert calls == []                                         # 잠김 판정·기준 읽기를 라벨링 세션에서 하지 않는다
        assert win.workbench is wb and wb.session is not None and win._pending_training is None
        assert ts.list_sessions(dst) == []
        win.show_home()
    finally:
        shutil.rmtree(dst)


def test_labeling_session_without_locks_keeps_menu_off(win, proj, tmp_path):  # noqa: F811
    """잠긴 세션도 손라벨 파일도 없으면 다시 열기 전에 끈다(파일 이름만 봄) — 다시 열었는데 훈련이 안 열리는 일을 막는다."""
    dst = str(tmp_path / "합성-잠금없음")
    shutil.copytree(proj["root"], dst)
    try:
        shutil.rmtree(os.path.join(dst, "locks"))
        wb = _open(win, dst, ctx(P2, "primary", "L01"))
        assert not win.a_streamer_training.isEnabled()
        assert win.a_training_reason.text() == rsm.REASON_NO_REFERENCE
        assert win.streamer_training() is None and win.workbench is wb
        win.show_home()
    finally:
        shutil.rmtree(dst)


def test_agreement_report_off_during_training(win, proj):  # noqa: F811
    """D-19 4: 일치도 보고서는 채팅 단위 정답을 보인다 — 훈련 중에는 끄고, 닫으면 다시 켠다. 사람 클릭 0."""
    _open(win, proj["root"], None)
    assert win.agreement_block_reason() is None
    win.update_session_actions()
    assert win.a_agreement.isEnabled()
    assert win.streamer_training() is not None
    why = win.agreement_block_reason()
    assert why and "훈련 중" in why
    assert not win.a_agreement.isEnabled()
    assert win.agreement_dialog(run=lambda d: None) is None
    win.close_training()
    assert win.agreement_block_reason() is None and win.a_agreement.isEnabled()
    win.show_home()


def test_training_closes_side_view_and_blocks_analysis_menu(win, proj, monkeypatch):  # noqa: F811
    """훈련 중에는 원래 반응·엔진 판정이 보이는 창이 없어야 한다(훈련 점수 블라인드): 열 때 '엔진과 나란히 보기'·
    '방송 요약' 창을 닫고, 훈련 중에는 분석 메뉴·AI 비교 보고서를 끈다. 닫으면 다시 켜진다. 사람 클릭 0."""
    from PySide6.QtWidgets import QDialog
    from app import analysis_flow as af
    monkeypatch.setattr(af, "seal_counts", lambda root, reader_kind=None, ai_root=None: (0, ["run-합성"]))
    _open(win, proj["root"], ctx("L01-AN-20261031-aa11", "analysis", "L01"))
    before = win.analysis_block_reasons(read_seal=True)
    assert before["view"] is None and before["summary"] is None and before["menu"] is None
    side, summ = QDialog(win), QDialog(win)
    side.show()
    summ.show()
    win.side_view_dialog, win.summary_dialog = side, summ
    assert win.a_streamer_training.isEnabled()
    view = win.streamer_training()
    assert view is not None
    assert win.side_view_dialog is None and win.summary_dialog is None
    assert not side.isVisible() and not summ.isVisible()
    r = win.analysis_block_reasons(read_seal=True)
    assert all(r[k] and "훈련 중" in r[k] for k in ("menu", "view", "summary", "report", "seal", "unseal"))
    assert not win.m_analysis.isEnabled() and not win.a_side_by_side.isEnabled() and not win.a_ai_compare.isEnabled()
    win.update_analysis_actions()
    assert not win.a_summary.isEnabled()
    assert win.side_by_side_view(show=False) is None and win.side_view_dialog is None
    assert win.broadcast_summary_view(show=False) is None and win.summary_dialog is None
    win.close_training()
    assert win.m_analysis.isEnabled()
    after = win.analysis_block_reasons(read_seal=True)
    assert after["view"] is None and after["menu"] is None
    win.show_home()


def test_pilot_project_menu_off_without_reading_labels(win, proj, tmp_path, monkeypatch):  # noqa: F811
    dst = str(tmp_path / "data" / "projects" / "pilot-합성")
    shutil.copytree(proj["root"], dst)
    try:
        # 시범 판별 뒤에는 최종 세트도 손라벨도 만들지 않는다
        from app import export
        calls = []
        monkeypatch.setattr(export, "build_final_set", lambda *a, **k: calls.append(1))
        monkeypatch.setattr(rsm, "load_manual_reference", lambda *a, **k: calls.append(2))
        monkeypatch.setattr(rsm, "pilot_sessions_locked", lambda root: (False, ["합성 까닭"]))
        _open(win, dst, None)
        assert not win.a_streamer_training.isEnabled()
        assert win.a_training_reason.text() == rsm.REASON_PILOT_LOCKED
        assert win.streamer_training() is None and calls == [] and ts.list_sessions(dst) == []
        win.show_home()
    finally:
        shutil.rmtree(dst)


def test_no_reference_menu_off(win, proj, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rsm, "load_final_set_reference", lambda root: (None, "empty"))
    _open(win, proj["root"], None)
    assert not win.a_streamer_training.isEnabled()
    assert win.a_training_reason.text() == rsm.REASON_NO_REFERENCE
    win.show_home()


def test_trainer_view_is_freed_after_close(win, proj):  # noqa: F811
    import gc
    import weakref
    from PySide6.QtCore import QCoreApplication, QEvent
    _open(win, proj["root"], None)
    view = win.streamer_training()
    ref = weakref.ref(view)
    del view
    QCoreApplication.processEvents()          # 열 때 건 초점 타이머(singleShot 0)가 돈다 — 실제 앱에서는 이벤트 루프가 돈다
    win.close_training()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()
    gc.collect()
    assert ref() is None
    win.show_home()


# ----------------------------------------------------------------------------- 가상 채팅 섞기 (H-4, 20 5장, D-19 2)
def _make_scenario_module():
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("_tool_make_scenario", os.path.join(here, "tools", "make_scenario.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _inproc_runner(calls):
    """'가상 채팅 만들기' 의 따로 프로세스 대신 같은 도구(tools/make_scenario.main)를 같은 인자로 이 프로세스에서."""
    import contextlib
    import io
    from app import scenario_mix
    mod = _make_scenario_module()

    def run(cmd, cancelled):
        calls.append(list(cmd))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mod.main(list(cmd[3:]))
        res = scenario_mix.parse_output(buf.getvalue())
        res["rc"] = rc
        return res
    return run


def _add_outside_chats(root, rng, n=12):
    """훈련 구간 밖(뒤)에 합성 후보 채팅을 더한다(같은 방송의 retrieved 후보). 본문은 서로 다르고 실제 채팅과 겹치지 않는다."""
    from app import jsonl
    p = os.path.join(root, "chat", "messages.jsonl")
    recs = list(jsonl.read(p).records)
    for i in range(n):
        recs.append({"chat_id": cid(500 + i), "t_ms": rng[1] + 5_000 + i * 3_000, "server_time_ms": 500 + i,
                     "type_code": 1, "kind": "text", "status": "NORMAL", "viewer_id": f"vw_{i:012d}",
                     "is_anonymous": False, "nickname_masked": "시**", "role": "common_user",
                     "text": f"합성 후보 채팅 {i}번", "flags": {}, "raw_ref": f"f:1:{500 + i}"})
    os.remove(p)                                                   # write_all 은 있는 파일을 덮지 않는다
    jsonl.write_all(p, recs)
    with open(p, "rb") as f:
        return f.read()


def test_virtual_make_then_mix_on_by_default(win, proj):  # noqa: F811
    """시나리오가 없으면 '가상 채팅 만들기' 한 번(도구를 워커로) → 그 뒤 '가상 채팅 섞기' 가 기본으로 켜지고
    '스트리머 훈련' 한 번에 섞인다. 가상 고르기는 견주기 점수와 따로 센다. 사람 클릭: 만들기 2 + 훈련 2."""
    from app import scenario_store as sst
    root = proj["root"]
    rng = win._training_range(rsm.training_availability(root))
    assert rng is not None
    msgs_before = _add_outside_chats(root, rng)
    calls = []
    win.scenario_runner = _inproc_runner(calls)
    wb = _open(win, root, None)
    assert win.a_make_virtual.isVisible() and win.a_make_virtual.isEnabled()
    assert not win.a_mix_virtual.isVisible()
    files_before = {os.path.relpath(os.path.join(d, f), root) for d, _x, fs in os.walk(root) for f in fs}
    res = win.make_virtual_chat()                                  # = a_make_virtual.trigger()
    assert res and res.get("written") is True and res.get("rc") == 0
    assert calls and calls[0][1] == "-I" and calls[0][calls[0].index("--from") + 1] == str(rng[0])
    ids = sst.list_scenarios(root)
    assert len(ids) == 1
    new = {os.path.relpath(os.path.join(d, f), root) for d, _x, fs in os.walk(root) for f in fs} - files_before
    assert new == {os.path.join("scenarios", ids[0] + ".jsonl")}   # scenarios\ 밖에 쓰지 않는다
    with open(os.path.join(root, "chat", "messages.jsonl"), "rb") as f:
        assert f.read() == msgs_before                             # 실제 채팅 파일은 그대로
    assert ts.list_sessions(root) == []                            # 만들기는 훈련을 열지 않는다(노출 없음)
    win.update_training_actions()
    assert win.a_mix_virtual.isVisible() and win.a_mix_virtual.isChecked() and not win.a_make_virtual.isVisible()
    assert "줄" in win.a_mix_virtual.text()
    # 두 번째 만들기는 같은 시나리오(같은 줄)라 새로 쓰지 않는다
    assert win.make_virtual_chat().get("written") is False and len(sst.list_scenarios(root)) == 1

    win.a_streamer_training.trigger()
    view = win.training_view
    assert view is not None and view.virtual_line_count() > 0
    # retrieved 원 채팅(같은 방송 구간 밖)은 표에 실리지 않는다 — 가상 줄과 본문이 같은 실제 줄 0개(20 8장 3번)
    d = view.chat.chat_data()
    vtexts = {d.texts[i] for i in range(d.n) if d.chat_ids[i].startswith("v:")}
    rtexts = {d.texts[i] for i in range(d.n) if not d.chat_ids[i].startswith("v:")}
    assert vtexts and not (vtexts & rtexts)
    sg = view._segment
    assert all(sg[0] <= int(d.times[i]) < sg[1] for i in range(d.n) if not d.chat_ids[i].startswith("v:"))
    recs = ts.read_valid(ts.session_path(root, ts.list_sessions(root)[0]))[0]
    scn = sst.load_scenario(root, ids[0])
    assert recs[0]["body"]["scenario_id"] == ids[0] and recs[0]["body"]["scenario_sha256"] == scn.sha256
    assert recs[0]["body"]["virtual_lines"] == view.virtual_line_count()
    # 구간 안의 가상 채팅 하나 + 실제 채팅 하나를 고른다
    seg = view._segment
    lns = sorted((ln for ln in view._vlines.values() if seg[0] <= ln.show_vod_ms < seg[1]), key=lambda x: x.show_vod_ms)
    assert lns
    view.seek_vod(max(lns[0].show_vod_ms, 125_000) + 100)
    assert view.toggle_pick(lns[0].vid) == "pick"
    assert view.toggle_pick(cid(1)) == "pick"
    assert view.show_compare()
    nums = view.compare_view.summary()
    assert nums["same"] + nums["mine_only"] == 1                   # 가상 고르기는 점수에 들어가지 않는다
    vs = view.virtual_result()
    assert vs.picked == 1 and vs.shown >= 1
    view.btn_close.click()
    kinds = _lines(root)
    assert kinds == [trc.K_START, trc.K_PICK, trc.K_PICK, trc.K_COMPARE, trc.K_END]
    blob = open(ts.session_path(root, ts.list_sessions(root)[0]), "rb").read()
    assert "합성 후보".encode() not in blob and "시**".encode() not in blob and b'"v:' not in blob
    sblob = open(scn.path, "rb").read()
    assert "합성 후보".encode() not in sblob and b"vw_" not in sblob and "시**".encode() not in sblob
    win.show_home()
    assert wb is not None


def test_virtual_mix_off_is_replay_only(win, proj):  # noqa: F811
    """'가상 채팅 섞기' 를 한 번 눌러 끄면 실제 채팅만(F 와 같은 start 줄). 이 실행 동안 프로젝트마다 기억한다."""
    root = proj["root"]
    rng = win._training_range(rsm.training_availability(root))
    _add_outside_chats(root, rng)
    win.scenario_runner = _inproc_runner([])
    _open(win, root, None)
    assert win.make_virtual_chat().get("written") is True
    win.update_training_actions()
    assert win.a_mix_virtual.isChecked()
    win.a_mix_virtual.trigger()                                    # 끄기 1클릭
    win.update_training_actions()
    assert not win.a_mix_virtual.isChecked()
    view = win.streamer_training()
    assert view is not None and view.virtual_line_count() == 0
    rec = ts.read_valid(ts.session_path(root, ts.list_sessions(root)[0]))[0][0]
    assert "scenario_id" not in rec["body"] and "virtual_lines" not in rec["body"]
    win.close_training()
    win.show_home()


def test_labeling_session_never_opens_scenarios(win, proj, monkeypatch):  # noqa: F811
    """라벨링 세션에서는 scenarios\\ 를 열지 않고(20 1장 4번) 가상 채팅 항목을 숨긴다."""
    from app import scenario_mix
    from app import scenario_store as sst
    root = proj["root"]
    os.makedirs(os.path.join(root, "scenarios"), exist_ok=True)
    touched = []
    real_listdir = os.listdir
    monkeypatch.setattr(scenario_mix, "find_scenario", lambda *a, **k: touched.append("find") or (None, {}))
    monkeypatch.setattr(sst, "latest_scenario", lambda *a, **k: touched.append("latest") or (None, {}))
    monkeypatch.setattr(os, "listdir", lambda p=".": (touched.append("ls") if "scenarios" in str(p) else None)
                        or real_listdir(p))
    _open(win, root, ctx(P2, "primary", "L01"))
    win.m_training.aboutToShow.emit()
    assert win.a_streamer_training.isEnabled()
    assert not win.a_mix_virtual.isVisible() and not win.a_make_virtual.isVisible()
    assert win.make_virtual_chat() is None
    assert touched == []
    win.show_home()


def test_make_virtual_off_without_tool(win, proj, monkeypatch):  # noqa: F811
    """묶은 exe(엔진 도구 없음)에서는 '가상 채팅 만들기' 가 꺼지고 풍선에 까닭 한 줄."""
    from app import scenario_mix
    monkeypatch.setattr(scenario_mix, "tool_path", lambda: None)
    _open(win, proj["root"], None)
    assert win.a_make_virtual.isVisible() and not win.a_make_virtual.isEnabled()
    assert win.a_make_virtual.toolTip() == scenario_mix.REASON_NO_TOOL
    assert win.make_virtual_chat() is None
    win.show_home()


# ----------------------------------------------------------------------------- 생성 연결 (D-24 3·5·1, 25 5-3 4·6)
# '훈련 → 가상 채팅 만들기' 한 번 = LM Studio 확인 → generate → make_scenario --generated. 실제 LM Studio 는 부르지 않는다:
# engine.llm_fake.FakeLLMServer(127.0.0.1 의 빈 포트) 와 127.0.0.1 닫힌 포트만. 소켓 연결은 가짜 서버 포트만 허용하고
# 나머지(1234 포함)는 막아 센다. 생성 응답·확인 답·닉네임·전사 낱말은 모두 합성이다.
NICK_SYN = "합성닉네임가"           # 합성 원 닉네임(identities.jsonl) — 생성 줄·보낸 글에 나오면 안 된다
BAD_SYN = "합성나쁜말"              # 확인 가짜가 '예' 라고 답할 합성 표지
GEN_TEMPLATES = ("오 {w} 얘기 나오니까 궁금해지네요", "저도 {w} 진짜 좋아해요 요즘", "{w} 다음엔 뭐 하실 거예요?",
                 "와 {w} 이건 처음 들어봐요", "{w} 하는 거 보니까 재밌어요 ㅋㅋ", "혹시 {w} 언제부터 하셨어요?")


def _gen_setup(root, rng):
    """합성 원 닉네임 목록·전사 낱말(구간 전체)을 프로젝트에 더한다(생성의 닉네임 대조·맥락). 본문은 합성."""
    import json as _json
    from tests.test_generate import words_doc
    os.makedirs(os.path.join(root, "chat", "protected"), exist_ok=True)
    with open(os.path.join(root, "chat", "protected", "identities.jsonl"), "w", encoding="utf-8") as f:
        f.write(_json.dumps({"viewer_id": "vw_aaaaaaaaaaaa", "nicknames": [{"nickname": NICK_SYN}]},
                            ensure_ascii=False) + "\n")
    os.makedirs(os.path.join(root, "derived"), exist_ok=True)
    lo, hi = (max(0, rng[0] - 120_000) // 10_000) * 10_000, rng[1] + 10_000
    wins = [(s, ["떡볶이", "보스", "캐릭터", "노래"]) for s in range(lo, hi, 10_000)]
    doc = words_doc(wins)
    doc["range"] = {"start_ms": lo, "end_ms": hi}
    with open(os.path.join(root, "derived", "asr-words.json"), "w", encoding="utf-8") as f:
        _json.dump(doc, f, ensure_ascii=False)


def _respond(messages):
    """가짜 서버 respond(합성): 확인 요청이면 '아니오'(BAD_SYN 줄은 '예'), 생성 요청이면 자료 낱말로 n 줄 + 나쁜 줄 1."""
    import re as _re
    from engine import generate as G
    sysm = str(messages[0].get("content")) if messages else ""
    user = str(messages[-1].get("content")) if messages else ""
    if sysm == G.CHECK_SYSTEM_V1:
        return "예" if BAD_SYN in user else "아니오"
    m = _re.search(r"방금 나온 낱말: ([^\n]*)", user)
    words = [w.strip() for w in (m.group(1).split(",") if m else []) if w.strip() and w.strip() != "(없음)"] or ["오늘"]
    n = int((_re.search(r"채팅 (\d+)줄", user) or [None, 3])[1])
    out = [f"{BAD_SYN} 들어간 합성 줄"]
    for i in range(n):
        out.append(GEN_TEMPLATES[i % len(GEN_TEMPLATES)].format(w=words[i % len(words)]))
    return "\n".join(f"{i}. {s}" for i, s in enumerate(out, 1))


class _Loopback:
    """with 블록 안 소켓 연결은 127.0.0.1 의 allow 포트만. 나머지는 막고 센다(실제 LM Studio 1234 포함)."""

    def __init__(self, monkeypatch, allow=()):
        import socket
        self.allow, self.allowed, self.blocked = set(allow), 0, []
        real = socket.socket.connect

        def connect(sock, addr):
            if isinstance(addr, tuple) and addr[0] == "127.0.0.1" and int(addr[1]) in self.allow:
                self.allowed += 1
                return real(sock, addr)
            self.blocked.append(addr[1] if isinstance(addr, tuple) and len(addr) > 1 else -1)
            raise ConnectionRefusedError("시험: 허용 밖 연결")
        monkeypatch.setattr(socket.socket, "connect", connect)


def _inproc_gen_runner(calls):
    """세 단계의 따로 프로세스 대신 같은 도구를 같은 인자로 이 프로세스에서: engine generate(…-c BOOT 루트 generate …) ·
    tools/make_scenario.py. 단계 이름만 calls 에 남긴다(인자 목록은 따로 cmds)."""
    import contextlib
    import io
    from app import scenario_mix
    from engine import generate as G
    mod = _make_scenario_module()
    cmds = []

    def run(cmd, cancelled):
        cmd = list(cmd)
        cmds.append(cmd)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            if "-c" in cmd and cmd[cmd.index("-c") + 1] == scenario_mix.ENGINE_BOOT:
                i = cmd.index("-c")
                assert cmd[i + 2] == scenario_mix.repo_root() and cmd[i + 3] == "generate"
                calls.append("check" if "--check-server" in cmd else "generate")
                rc = G.main(cmd[i + 4:])
            else:
                calls.append("make")
                rc = mod.main(list(cmd[3:]))
        res = scenario_mix.parse_output(buf.getvalue())
        res["rc"] = rc
        return res
    run.cmds = cmds
    return run


def _set_llm(win, url, model="synthetic-model", check=True):
    from app.ui import mainwindow as mw
    win.settings.setValue(mw.LMSTUDIO_URL_KEY, url)
    win.settings.setValue(mw.LMSTUDIO_MODEL_KEY, model)
    win.settings.setValue(mw.LMSTUDIO_CHECK_KEY, "1" if check else "0")


def _tree(root):
    return {os.path.relpath(os.path.join(d, f), root) for d, _x, fs in os.walk(root) for f in fs}


def test_make_virtual_generates_with_fake_server(win, proj, monkeypatch):  # noqa: F811
    """D-24 3: 메뉴 한 번 = LM Studio 확인 → 생성(--llm-check) → make_scenario --generated. 가짜 서버(127.0.0.1)만.
    사람 클릭: 메뉴 '훈련' + '가상 채팅 만들기' = 2 (고르는 창·입력 칸 0). 생성 줄은 '생' 으로 섞인다."""
    from app import scenario_mix
    from app import scenario_store as sst
    from engine import generate as G
    from engine import llm_fake as LF
    root = proj["root"]
    rng = win._training_range(rsm.training_availability(root))
    msgs_before = _add_outside_chats(root, rng)
    _gen_setup(root, rng)
    calls = []
    runner = _inproc_gen_runner(calls)
    win.scenario_runner = runner
    with LF.FakeLLMServer(default={"respond": _respond}, models=("synthetic-model",)) as srv:
        guard = _Loopback(monkeypatch, allow={srv.port})
        base_url = srv.base_url
        _set_llm(win, base_url)
        _open(win, root, None)
        assert win.a_make_virtual.isVisible() and win.a_make_virtual.isEnabled()
        assert "LM Studio(synthetic-model)" in win.a_make_virtual.toolTip()
        before = _tree(root)
        res = win.make_virtual_chat()                              # = a_make_virtual.trigger() (클릭 1)
        reqs = list(srv.requests)
    assert calls == ["check", "generate", "make"]
    assert res["rc"] == 0 and res["written"] is True
    li = res["llm"]
    assert li["tried"] is True and li["code"] is None and li["generated_lines"] > 0
    assert guard.blocked == [] and guard.allowed > 0               # 가짜 서버 밖 연결 0
    gen_cmd = runner.cmds[1]
    assert "--llm-check" in gen_cmd and gen_cmd[gen_cmd.index("--url") + 1] == base_url
    out_path = gen_cmd[gen_cmd.index("--out") + 1]
    assert out_path == scenario_mix.generation_path(root, rng)
    assert runner.cmds[2][runner.cmds[2].index("--generated") + 1] == out_path
    # 생성 줄이 시나리오에 들어갔고, 걸러진 나쁜 줄은 없다
    ids = sst.list_scenarios(root)
    assert len(ids) == 1
    scn = sst.load_scenario(root, ids[0])
    n_gen = int(scn.header["counts"]["generated"])
    assert n_gen > 0 and res["counts"]["generated"] == n_gen
    gens = [r for r in scn.records if r.get("source") == "generated"]
    assert len(gens) == n_gen and all(BAD_SYN not in r["text"] and NICK_SYN not in r["text"] for r in gens)
    assert scn.header["generator"]["backend"] == "lmstudio" and scn.header["generator"]["model_id"] == "synthetic-model"
    msg = win.statusBar().currentMessage()
    assert msg == scenario_mix.MSG_WRITTEN_GEN.format(int(scn.header["counts"]["retrieved"]), n_gen)
    # 서버에 간 것: GET /v1/models(확인 2번 — 메뉴 단계 + generate 안) · 생성 POST · 확인 POST. 보낸 글에 닉네임·본문 없음
    posts = [r["json"] for r in reqs if r["method"] == "POST"]
    sysm = [p["messages"][0]["content"] for p in posts]
    assert sysm.count(G.SYSTEM_V1) >= 1 and sysm.count(G.CHECK_SYSTEM_V1) >= n_gen
    body = __import__("json").dumps(posts, ensure_ascii=False)
    assert NICK_SYN not in body and "합성 본문" not in body and "합성 후보" not in body and "vw_" not in body
    # 쓴 파일은 scenarios\ 아래만: 시나리오 1 + 생성 후보 1 + 보낸 기록 1. 실제 채팅은 그대로
    new = _tree(root) - before
    assert new == {os.path.join("scenarios", ids[0] + ".jsonl"), os.path.relpath(out_path, root),
                   os.path.join("scenarios", "llm-send.jsonl")}
    with open(os.path.join(root, "chat", "messages.jsonl"), "rb") as f:
        assert f.read() == msgs_before
    with open(os.path.join(root, "scenarios", "llm-send.jsonl"), "rb") as f:
        log = f.read()
    assert BAD_SYN.encode() not in log and NICK_SYN.encode() not in log and "궁금".encode() not in log
    assert b'"llm-check-v1"' in log and b'"gen-chat-v1"' in log
    assert ts.list_sessions(root) == []                            # 만들기는 훈련을 열지 않는다(노출 없음)
    # 섞인 훈련: 생성 줄이 '생' 표시로 보인다(스트리머 훈련 클릭 1)
    win.update_training_actions()
    assert win.a_mix_virtual.isVisible() and win.a_mix_virtual.isChecked()
    view = win.streamer_training()
    assert view is not None and view.virtual_line_count() == n_gen + int(scn.header["counts"]["retrieved"])
    rec = ts.read_valid(ts.session_path(root, ts.list_sessions(root)[0]))[0][0]
    assert rec["body"]["scenario_id"] == ids[0]
    win.close_training()
    blob = open(ts.session_path(root, ts.list_sessions(root)[0]), "rb").read()
    assert not any(r["text"].encode() in blob for r in gens)       # 훈련 기록에 생성 본문 없음
    win.show_home()


def test_make_virtual_server_off_retrieved_only(win, proj, monkeypatch):  # noqa: F811
    """서버 꺼짐(127.0.0.1 닫힌 포트): 생성 0 · 상태 줄에 'lmstudio_off' 한 줄 · retrieved 만으로 시나리오. 생성 단계는
    부르지 않는다. 사람 클릭 그대로 2."""
    from app import scenario_mix
    from app import scenario_store as sst
    from engine import llm_fake as LF
    root = proj["root"]
    rng = win._training_range(rsm.training_availability(root))
    _add_outside_chats(root, rng)
    _gen_setup(root, rng)
    port = LF.closed_port()
    calls = []
    win.scenario_runner = _inproc_gen_runner(calls)
    guard = _Loopback(monkeypatch, allow={port})
    _set_llm(win, f"http://127.0.0.1:{port}")
    _open(win, root, None)
    res = win.make_virtual_chat()
    assert calls == ["check", "make"]                              # 꺼져 있으면 generate 를 부르지 않는다
    assert res["written"] is True and res["llm"]["code"] == "lmstudio_off" and res["llm"]["step"] == "check"
    assert res["counts"]["generated"] == 0 and res["counts"]["retrieved"] > 0
    msg = win.statusBar().currentMessage()
    assert "lmstudio_off" in msg and msg.startswith(scenario_mix.MSG_WRITTEN.format(res["counts"]["retrieved"]))
    assert msg.count("\n") == 0                                    # 한 줄
    assert guard.blocked == []
    assert not os.path.exists(scenario_mix.generation_path(root, rng))
    assert not os.path.exists(os.path.join(root, "scenarios", "llm-send.jsonl"))   # 보낸 것 없음
    assert len(sst.list_scenarios(root)) == 1
    win.show_home()


def test_make_virtual_without_model_never_contacts_lmstudio(win, proj, monkeypatch):  # noqa: F811
    """모델 설정이 없으면(기본) LM Studio 확인·생성을 부르지 않는다 — 지금까지와 같은 retrieved 만, 연결 0."""
    from app.ui import mainwindow as mw
    root = proj["root"]
    rng = win._training_range(rsm.training_availability(root))
    _add_outside_chats(root, rng)
    calls = []
    win.scenario_runner = _inproc_gen_runner(calls)
    guard = _Loopback(monkeypatch, allow=())
    _open(win, root, None)
    assert win.a_make_virtual.toolTip() == mw.MAKE_TIP_RETRIEVED
    res = win.make_virtual_chat()
    assert calls == ["make"] and res["written"] is True and res["llm"]["tried"] is False
    assert "생성" not in win.statusBar().currentMessage()
    assert guard.blocked == [] and guard.allowed == 0
    win.show_home()


@pytest.mark.parametrize("url", ["http://192.168.0.5:1234", "http://example.com:1234", "http://127.0.0.1.evil:1234",
                                 "http://user@127.0.0.1:1234", "file:///c:/x", "http://127.0.0.1:99999"])
def test_make_virtual_non_local_url_refused_before_spawn(win, proj, monkeypatch, url):  # noqa: F811
    """설정 주소가 이 PC 밖이면 명령도 만들지 않는다(not_local) — retrieved 만, 연결 0."""
    root = proj["root"]
    rng = win._training_range(rsm.training_availability(root))
    _add_outside_chats(root, rng)
    calls = []
    win.scenario_runner = _inproc_gen_runner(calls)
    guard = _Loopback(monkeypatch, allow=())
    _set_llm(win, url)
    _open(win, root, None)
    res = win.make_virtual_chat()
    assert calls == ["make"] and res["llm"]["code"] == "not_local"
    assert "not_local" in win.statusBar().currentMessage()
    assert guard.blocked == [] and guard.allowed == 0
    win.show_home()


def test_make_virtual_model_missing_and_no_identities(win, proj, monkeypatch):  # noqa: F811
    """올라가지 않은 모델 → model_missing(확인 단계), 원 닉네임 목록 없음 → nicknames_missing(생성 단계, 보낸 것 0).
    둘 다 retrieved 만으로 만든다."""
    from engine import llm_fake as LF
    root = proj["root"]
    rng = win._training_range(rsm.training_availability(root))
    _add_outside_chats(root, rng)
    with LF.FakeLLMServer(default={"respond": _respond}, models=("synthetic-model",)) as srv:
        _Loopback(monkeypatch, allow={srv.port})
        calls = []
        win.scenario_runner = _inproc_gen_runner(calls)
        _set_llm(win, srv.base_url, model="not-loaded-model")
        _open(win, root, None)
        res = win.make_virtual_chat()
        assert calls == ["check", "make"] and res["llm"]["code"] == "model_missing"
        assert "model_missing" in win.statusBar().currentMessage()
        win.show_home()
        _gen_setup(root, rng)
        os.remove(os.path.join(root, "chat", "protected", "identities.jsonl"))
        calls.clear()
        _set_llm(win, srv.base_url)
        _open(win, root, None)
        res = win.make_virtual_chat()
        posts = [r for r in srv.requests if r["method"] == "POST"]
    assert calls == ["check", "generate", "make"] and res["llm"]["code"] == "nicknames_missing"
    assert posts == [] and res["counts"]["generated"] == 0
    assert "nicknames_missing" in win.statusBar().currentMessage()
    win.show_home()


def test_check_command_real_subprocess_with_fake_server(proj):  # noqa: F811
    """따로 프로세스 길(-I -c ENGINE_BOOT 저장소 generate --check-server)이 실제로 돈다 — 가짜 서버(127.0.0.1)만."""
    from app import scenario_mix
    from engine import llm_fake as LF
    with LF.FakeLLMServer(models=("synthetic-model",)) as srv:
        llm, prob = scenario_mix.lm_settings_from(srv.base_url, "synthetic-model")
        assert prob is None
        res = scenario_mix.run_make(scenario_mix.check_command(llm), timeout_s=60)
        reqs = list(srv.requests)
    assert res["rc"] == 0 and res["server"] == "on" and res["model"] == "ok"
    assert [r["method"] for r in reqs] == ["GET"]
    res = scenario_mix.run_make(scenario_mix.check_command(
        scenario_mix.lm_settings_from(f"http://127.0.0.1:{LF.closed_port()}", "m")[0]), timeout_s=60)
    assert res["rc"] == 3 and res["error"] == "lmstudio_off"


def test_lm_settings_parse():
    from app import scenario_mix as sm
    s, p = sm.lm_settings_from("", "")
    assert s.url == sm.LMSTUDIO_DEFAULT_URL and not s.enabled and p is None and s.llm_check is True
    s, p = sm.lm_settings_from("http://localhost:1234/", "qwen2.5-7b-instruct", "0")
    assert s.url == "http://localhost:1234" and s.enabled and s.llm_check is False and p is None
    assert sm.lm_settings_from("http://[::1]:1234/v1", "org/model-GGUF")[1] is None
    assert sm.lm_settings_from("http://127.0.0.1:1234", "--evil")[0].enabled is False      # 옵션처럼 보이는 이름
    assert sm.lm_settings_from("http://127.0.0.1:1234", "--evil")[1] == "bad_model"
    assert sm.lm_settings_from("http://10.0.0.1:1234", "m")[1] == "not_local"
    assert sm.generate_threshold_args(["--quiet-max-real", "45", "--target-per-min", "60"]) == ["--threshold", "45/60"]
    assert sm.generate_threshold_args(["--threshold", "test", "--max-virtual-ratio", "0.4"]) == \
        ["--threshold", "test", "--max-virtual-ratio", "0.4"]
    assert sm.generate_threshold_args([]) == []
    ok, m = sm.describe({"written": True, "counts": {"retrieved": 3, "generated": 2}})
    assert ok and m == sm.MSG_WRITTEN_GEN.format(3, 2)
    ok, m = sm.describe({"written": True, "counts": {"retrieved": 3}, "llm": {"tried": True, "code": "lmstudio_off"}})
    assert ok and m.startswith(sm.MSG_WRITTEN.format(3)) and "lmstudio_off" in m
