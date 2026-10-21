"""M4 운영 마감 통합 시험 (D-14 2·8, 07 M4). 합성 데이터만(지어낸 제목·근거, tmp_path 안).

- 출처 판정 세션 session.open.origin_targets (15 5-2 의 7번): 처음 열 때 origin/ 의 대상 파일 ID·해시를 남긴다.
  다른 세션 종류에는 넣지 않는다. 스키마가 이 칸을 받는다.
- store save_block (04 5-5 '업데이트 필요'): 읽기 전용으로 열고 session.open 도 쓰지 않는다.
- 메인 창: 되돌리기 메뉴(소스 실행이면 꺼짐, delete_backup_pending 이면 꺼짐), 파일 메뉴의 백업·되살리기·지우기,
  작업이 도는 동안 세션 메뉴 끄기, 지원 종료 띠, 경로 띠·confirm 묻기, 프로젝트 백업·지우기(가짜 휴지통) 흐름.
"""
import json
import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app import project, session, store  # noqa: E402
from app.core import origin as og  # noqa: E402
from tests.test_store import NOW, PID, SALT, Clock, FakeLock, _guide, ctx, uuids  # noqa: E402,F401

ACCEPTED, REJECTED = 1, 0          # QDialog.DialogCode 값 (exec() 가 돌려주는 int)


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture
def proj_dir(tmp_path):
    base = tmp_path / "한글 문서" / "프로젝트들"
    p = project.create(str(base), "합성 방송 제목", 123, "시험용 목적", "L01", "0.0.1", NOW, PID, SALT,
                       guide=_guide(), machine4="m3fa")
    assert not p.read_only
    return p.root


def _cid(i: int) -> str:
    return "c" + f"{i:020x}"


def _write_target(root, ids, created_at):
    t = og.build_targets(chat_ids=ids, created_at=created_at, created_by="L09", basis="manual")
    d = os.path.join(root, "origin")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, t["target_id"] + ".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(t, f, ensure_ascii=False)
    return t


# ---------------------------------------------------------------------------- session.open.origin_targets
def test_origin_review_session_open_records_targets(proj_dir):
    t1 = _write_target(proj_dir, [_cid(i) for i in range(3)], "2026-10-21T15:00:00+09:00")
    t2 = _write_target(proj_dir, [_cid(i) for i in range(2, 6)], "2026-10-22T15:00:00+09:00")
    with open(os.path.join(proj_dir, "origin", "OT-broken.json"), "w", encoding="utf-8") as f:
        f.write("{")                                                     # 이름이 규칙 밖이라 보지 않는다
    c = ctx(sid="L02-OR-20261021-ab12", kind="origin_review", labeler="L02")
    st = store.ProjectStore.open(proj_dir, c, FakeLock, clock=Clock(), uuid_factory=uuids(), app_version="0.0.1")
    try:
        assert not st.read_only, st.read_only_reasons
        body = st.records()[0]["body"]
        assert body["origin_targets"] == sorted(
            [{"target_id": t["target_id"], "chat_ids_sha256": t["chat_ids_sha256"]} for t in (t1, t2)],
            key=lambda x: x["target_id"])
        assert store.load_label_schema().check(st.records()[0]) == []
    finally:
        st.close()
    # 다시 열면 첫 줄을 다시 쓰지 않는다 (뒤에 생긴 대상 파일은 메타에 들어가지 않는다)
    _write_target(proj_dir, [_cid(9)], "2026-10-23T15:00:00+09:00")
    st2 = store.ProjectStore.open(proj_dir, c, FakeLock, clock=Clock(), uuid_factory=uuids(), app_version="0.0.1")
    try:
        assert len(st2.session_meta["origin_targets"]) == 2
    finally:
        st2.close()


def test_other_sessions_do_not_record_origin_targets(proj_dir):
    _write_target(proj_dir, [_cid(1)], "2026-10-21T15:00:00+09:00")
    st = store.ProjectStore.open(proj_dir, ctx(), FakeLock, clock=Clock(), uuid_factory=uuids(), app_version="0.0.1")
    try:
        assert "origin_targets" not in st.records()[0]["body"]
    finally:
        st.close()


def test_store_save_block_opens_read_only_without_writing(proj_dir):
    st = store.ProjectStore.open(proj_dir, ctx(), FakeLock, clock=Clock(), uuid_factory=uuids(), app_version="0.0.1",
                                 save_block="합성 사유: 지원 종료")
    try:
        assert st.read_only and "합성 사유: 지원 종료" in (st.read_only_reason or "")
        assert not os.path.exists(st.path)                               # session.open 을 쓰지 않았다
        assert not FakeLock.held                                          # 잠금도 잡지 않았다
        with pytest.raises(store.ReadOnlyStoreError):
            st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 1000, "category": "just_chatting",
                                                          "confidence": "certain", "rationale": "합성"}})
    finally:
        st.close()


# ---------------------------------------------------------------------------- 메인 창
def _window(tmp_path, **kw):
    from app import updater
    from app.appspec import SPEC
    from app.ui.mainwindow import MainWindow
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    win = MainWindow(SPEC, updater.load_installed(SPEC), data_root, "", None, [], set(), **kw)
    win.check_updates = lambda force=False, on_start=False: None
    win.shown_msgs = []
    win.inform = lambda title, text: win.shown_msgs.append(("info", text))
    win.warn = lambda title, text: win.shown_msgs.append(("warn", text))
    return win


def test_menus_exist_and_rollback_off_in_source(qapp, tmp_path):
    win = _window(tmp_path)
    try:
        texts = [a.text() for a in win.menuBar().actions()[0].menu().actions()]
        assert any("프로젝트 백업" in t for t in texts)
        assert any("백업에서 되살리기" in t for t in texts)
        assert any("프로젝트 지우기(휴지통)" in t for t in texts)
        win.update_rollback_action()
        assert "이전 버전으로 되돌리기" in win.a_rollback.text()
        assert not win.a_rollback.isEnabled() and "소스" in win.a_rollback.toolTip()
        assert win.rollback_dialog() is False                             # 불가면 대화상자 없이 False
        assert not win.support_banner.isVisible() and not win.path_banner.isVisible()
    finally:
        win.close()
        win.deleteLater()


def test_rollback_menu_off_when_backup_delete_pending(qapp, tmp_path, monkeypatch):
    from app import updater
    win = _window(tmp_path, delete_backup_pending=True)
    try:
        ok = updater.RollbackStatus(True, "되돌리기: 0.1.1 → 0.1.0", "0.1.1", "0.1.0", "x", False)
        monkeypatch.setattr(win, "rollback_state", lambda repair=False: ok)
        win.update_rollback_action()
        assert not win.a_rollback.isEnabled() and "정리" in win.a_rollback.toolTip()
        assert win.rollback_dialog() is False
        win.delete_backup_pending = False
        win.update_rollback_action()
        assert win.a_rollback.isEnabled() and "0.1.0" in win.a_rollback.text()
        calls = []
        monkeypatch.setattr(win, "start_rollback", lambda st: calls.append(st) or True)
        assert win.rollback_dialog(run=lambda d: REJECTED) is False and not calls
        assert win.rollback_dialog(run=lambda d: ACCEPTED) is True and calls == [ok]
    finally:
        win.close()
        win.deleteLater()


def test_session_menus_off_while_jobs_run(qapp, tmp_path):
    win = _window(tmp_path)
    try:
        win.busy_jobs = 1
        win.update_session_actions()
        assert not any(a.isEnabled() for a in (win.a_lock, win.a_plan, win.a_agreement, win.a_cancel_plan))
        assert "작업" in win.a_lock.toolTip()
        win.busy_jobs = 0
        win.update_session_actions()
        assert "프로젝트를 먼저" in win.a_lock.toolTip()
    finally:
        win.close()
        win.deleteLater()


def test_support_banner_from_state(qapp, tmp_path):
    from app import __version__, updater
    data_root = tmp_path / "data"
    st = updater.UpdateState(last_ok_at="2026-10-21T00:00:00+00:00", last_min_supported="99.0.0")
    st.save(str(data_root / "update" / "state.json"))
    win = _window(tmp_path)
    try:
        win.show()
        assert win.support_banner.isVisible() and "지원이 끝났습니다" in win.support_banner.label.text()
        assert win.support_banner.b_manual.isVisible()
        assert updater.label_save_block_reason(win.state_path, __version__)
    finally:
        win.close()
        win.deleteLater()


def test_path_banner_and_confirm(qapp, tmp_path):
    from app.paths import PathIssue
    issues = [PathIssue("onedrive", "confirm", "app", "합성 경고: 프로그램 폴더가 OneDrive 아래"),
              PathIssue("non_ascii", "info", "data", "합성 참고")]
    win = _window(tmp_path, path_issues=issues)
    try:
        win.show()
        assert win.path_banner.isVisible() and "OneDrive" in win.path_banner.text()
        assert "합성 참고" not in win.path_banner.text()                  # info 는 띠에 내지 않는다
        asked = []
        assert win.confirm_startup_paths(ask=lambda it: asked.append(it.code) or False) is False
        assert win.confirm_startup_paths(ask=lambda it: asked.append(it.code) or True) is True
        assert win.confirm_startup_paths(ask=lambda it: asked.append(it.code) or False) is True   # 한 번 계속하면 다시 묻지 않음
        assert asked == ["onedrive", "onedrive"]
    finally:
        win.close()
        win.deleteLater()


def test_backup_and_dispose_flow(qapp, tmp_path, proj_dir):
    from app import backup
    win = _window(tmp_path)
    try:
        def accept_backup(d):
            d.cb_chat.setChecked(False)
            return ACCEPTED
        res = win.backup_project_dialog(root=proj_dir, run=accept_backup)
        assert res is not None and os.path.isfile(res.path) and not res.manifest["include_chat"]
        assert backup.verify_backup(res.path) == []
        # 지우기: 가짜 휴지통(임시 폴더로 옮김). 확인 칸·라벨러 코드가 있어야 단추가 켜진다
        bin_dir = tmp_path / "가짜 휴지통"
        bin_dir.mkdir()
        moved = []

        def fake_trash(p):
            dst = bin_dir / f"{len(moved)}"
            shutil.move(p, dst)
            moved.append(p)

        def accept_dispose(d):
            assert d.plan is not None and not d.b_ok.isEnabled()
            d.ed_labeler.setText("L01")
            assert not d.b_ok.isEnabled()
            d.cb_sure.setChecked(True)
            assert d.b_ok.isEnabled()
            return ACCEPTED
        out = win.dispose_project_dialog(root=proj_dir, run=accept_dispose, trash=fake_trash)
        assert out is not None and out.ok
        assert not os.path.exists(proj_dir)
        kinds = {t.kind for t in out.trashed}
        assert "project" in kinds and "backups" in kinds
        log = backup.disposal_log(win.data_root)
        assert log and log[-1]["labeler_id"] == "L01" and log[-1]["mode"] == "all"
    finally:
        win.close()
        win.deleteLater()


def _open_in_window(win, proj_dir):
    from app.ui.project_view import open_checked
    win.choose_session = lambda o: None                      # 세션 없이 보기만
    win.confirm_project_path = lambda o: True
    out = open_checked(proj_dir)
    win.show_workbench(out)
    return out


def _accept_dispose(d):
    d.ed_labeler.setText("L01")
    d.cb_sure.setChecked(True)
    return ACCEPTED


def _mover(tmp_path):
    bin_dir = tmp_path / "가짜 휴지통"
    bin_dir.mkdir(exist_ok=True)
    moved = []

    def fake_trash(p):
        shutil.move(p, bin_dir / str(len(moved)))
        moved.append(p)
    return fake_trash, moved


def test_dispose_refuses_read_only_open_when_other_pc_holds_lock(qapp, tmp_path, proj_dir):
    """다른 PC(OneDrive 동기화)가 쥔 잠금 때문에 이 창이 읽기 전용으로 열었다. 이 창에서 지우기를 해도
    '열려 있음' 검사를 건너뛰지 않는다 — 그 사람이 지금 라벨링 중일 수 있다."""
    lockp = os.path.join(proj_dir, "locks", "open.lock")
    os.makedirs(os.path.dirname(lockp), exist_ok=True)
    with open(lockp, "w", encoding="utf-8", newline="\n") as f:
        f.write("4242\nChatCoach\nSYNTH-OTHER-PC\n")                     # 합성 호스트 이름
    win = _window(tmp_path)
    try:
        out = _open_in_window(win, proj_dir)
        assert out.lock is None                                           # 잠금을 잡지 못했다(읽기 전용)
        assert win._current_project_root() is not None and not win._workbench_holds_lock()
        trash, moved = _mover(tmp_path)
        assert win.dispose_project_dialog(root=proj_dir, run=_accept_dispose, trash=trash) is None
        assert moved == [] and os.path.isdir(proj_dir)
        assert win.shown_msgs and "열고 있습니다" in win.shown_msgs[-1][1]
    finally:
        win.close()
        win.deleteLater()


def test_dispose_open_project_this_window_holds_lock(qapp, tmp_path, proj_dir):
    """이 창이 잠금을 쥐고 연 프로젝트는 지울 수 있다(작업대를 닫아 잠금을 푼 뒤 휴지통으로)."""
    win = _window(tmp_path)
    try:
        out = _open_in_window(win, proj_dir)
        assert out.lock is not None and win._workbench_holds_lock()
        trash, moved = _mover(tmp_path)
        res = win.dispose_project_dialog(root=proj_dir, run=_accept_dispose, trash=trash)
        assert res is not None and res.ok and not os.path.exists(proj_dir)
    finally:
        win.close()
        win.deleteLater()


def test_dispose_rechecks_lock_after_confirm(qapp, tmp_path, proj_dir):
    """확인 대화상자가 떠 있는 동안 다른 창이 열면, 보내기 직전 검사에서 멈춘다."""
    from PySide6.QtCore import QLockFile
    win = _window(tmp_path)
    lk = QLockFile(os.path.join(proj_dir, "locks", "open.lock"))
    trash, moved = _mover(tmp_path)

    def accept_then_open(d):
        os.makedirs(os.path.dirname(lk.fileName()), exist_ok=True)
        assert lk.tryLock(0)                                               # 다른 창이 지금 연다
        return _accept_dispose(d)
    try:
        assert win.dispose_project_dialog(root=proj_dir, run=accept_then_open, trash=trash) is None
        assert moved == [] and os.path.isdir(proj_dir)
    finally:
        lk.unlock()
        win.close()
        win.deleteLater()


def test_rollback_menu_uses_repair_when_folder_may_be_mixed(qapp, tmp_path, monkeypatch):
    """rollback_failed · restore_failed · 멈춘 업데이트가 남아 있으면 메뉴도 repair=True 로 판정한다."""
    from app import updater
    win = _window(tmp_path)
    try:
        seen = []
        monkeypatch.setattr(updater, "rollback_status", lambda *a, **kw: seen.append(kw.get("repair")) or
                            updater.RollbackStatus(False, "시험", "0.1.0"))
        win.rollback_state()
        p = os.path.join(win.data_root, "update", "last_result.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="ascii") as f:
            json.dump({"result": "rollback_failed", "from": "0.1.1", "to": "0.1.0"}, f)
        win.rollback_state()
        assert seen == [False, True]
    finally:
        win.close()
        win.deleteLater()


def test_dispose_refuses_when_other_window_holds_lock(qapp, tmp_path, proj_dir):
    from PySide6.QtCore import QLockFile
    win = _window(tmp_path)
    lk = QLockFile(os.path.join(proj_dir, "locks", "open.lock"))
    os.makedirs(os.path.dirname(lk.fileName()), exist_ok=True)
    assert lk.tryLock(0)
    try:
        assert win.dispose_project_dialog(root=proj_dir, run=lambda d: ACCEPTED) is None
        assert win.shown_msgs and "열고 있습니다" in win.shown_msgs[0][1] and os.path.isdir(proj_dir)
    finally:
        lk.unlock()
        win.close()
        win.deleteLater()
