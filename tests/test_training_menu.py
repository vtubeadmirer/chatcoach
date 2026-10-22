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
    assert [a.text() for a in win.m_training.actions() if a.isVisible()] == ["스트리머 훈련(&S)"]
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
