"""메인 창 '훈련 → 스트리머 훈련' 통합과 훈련 노출 (08 D-16·D-18 6·7·8, 18 6-2·6-5 3) — app/ui/mainwindow.py ·
app/core/exposure.py 규칙 5 · app/exposure_io.project_trainings.

합성 데이터만 쓴다: 합성 최종 세트 프로젝트(tests.test_final_set.proj — 1차·같은 사람 재라벨 잠김, 채팅 6개)와 합성 세션 ID.
실제 채팅·닉네임·전사문·시범 프로젝트 폴더는 쓰지 않는다. 시범 이름 사본은 tmp_path 안에 만들고 shutil.rmtree 로 지운다.
사람 클릭 수(D-16): 메뉴 '훈련' 1 + '스트리머 훈련' 1 = 2, 고르는 창·입력 칸 0.
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


def test_labeling_session_blocks_training(win, proj):  # noqa: F811
    _open(win, proj["root"], ctx(P2, "primary", "L01"))
    assert not win.a_streamer_training.isEnabled() and "라벨링 세션" in win.a_training_reason.text()
    assert win.streamer_training() is None and ts.list_sessions(proj["root"]) == []
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
