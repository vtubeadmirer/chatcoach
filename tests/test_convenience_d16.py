"""시범 편의 고침 (08 D-16 메모 '한 번 누르기 · 복사 없음 · 숨은 조작 없음'). 합성 데이터만(tmp_path), 실제 설정·프로젝트는
건드리지 않는다(settings.ini 는 tmp 안 QSettings).

1. 시작할 때 마지막 작업 이어서 열기: session.auto_resume_candidate · MainWindow.auto_resume / auto_resume_session · 설정 켜고 끄기
2. 작업대 칸 나누기 크기 기억(hsplit·vsplit) — 조정 모드 넓히기(_split_before)와 함께
3. 채팅 본문의 이모티콘 코드 표시({:101:} → [이모티콘], 연속은 [이모티콘×n]) — 저장 값·원문 검색은 그대로
4. 파일 대화상자 시작 폴더(마지막으로 쓴 폴더 · 기본 프로젝트 위치 · 연결된 영상 폴더)
5. 업데이트 확인 실패(404·네트워크)는 상태 줄에 띄우지 않고, [지금 확인] 때만 쉬운 문장
6. D-16 6b: recheck_incomplete 는 참고 점수(lenient)에 남고 엄격(strict)에서만 빠진다
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings, Qt  # noqa: E402

from app import ai_compare as ac  # noqa: E402
from app import export as export_mod  # noqa: E402
from app import session as sess  # noqa: E402
from app import updater  # noqa: E402
from app.ui import chat_panel as cp  # noqa: E402
from app.ui import project_view as pv  # noqa: E402
from app.ui import workbench as wbm  # noqa: E402
from tests.test_integration_c4 import PARTS, SID_A, close_wb, ctx, make_long_project, open_wb, qapp  # noqa: E402,F401
from tests.test_reaction_editor import env  # noqa: E402,F401
from tests.test_reaction_editor import cid as rcid  # noqa: E402
from tests.test_reaction_editor import make_chats  # noqa: E402


def _settings(tmp_path, name="settings.ini"):
    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


def _touch(*parts):
    p = os.path.join(*parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"{}\n")
    return p


# ============================================================================ 1. 이어서 열기
def test_auto_resume_candidate_only_single_labeling_session(tmp_path):
    root = str(tmp_path / "합성 프로젝트")
    os.makedirs(root)
    assert sess.auto_resume_candidate(root, None, "L01") is None                 # 세션 없음
    _touch(root, "labels", "L01-P-20261021-ab12.m3fa.jsonl")
    _touch(root, "labels", "L02-P-20261021-cd34.m3fa.jsonl")                     # 다른 라벨러 것은 세지 않는다
    e = sess.auto_resume_candidate(root, None, "L01", "m3fa")
    assert e is not None and e.session_id == "L01-P-20261021-ab12" and e.session_kind == "primary"
    # 이어 쓸 세션이 둘(1차 + 조정)이면 사람이 고른다
    _touch(root, "labels", "L01-AD-20261022-ad01.m3fa.jsonl")
    assert sess.auto_resume_candidate(root, None, "L01") is None
    # 1차를 잠그면 이어 쓸 것은 조정 하나 — 조정 세션은 자동으로 열지 않는다
    _touch(root, "locks", "L01-P-20261021-ab12.json")
    assert sess.auto_resume_candidate(root, None, "L01") is None
    # 재라벨(라벨링 세션) 하나만 남으면 그것
    os.remove(os.path.join(root, "labels", "L01-AD-20261022-ad01.m3fa.jsonl"))
    _touch(root, "labels", "L01-R2-20261023-ef56.m3fa.jsonl")
    e = sess.auto_resume_candidate(root, None, "L01")
    assert e is not None and e.session_kind == "relabel_second"
    # 분석 세션 하나만이어도 열지 않는다
    root2 = str(tmp_path / "합성 프로젝트2")
    _touch(root2, "labels", "L01-AN-20261024-an01.m3fa.jsonl")
    assert sess.auto_resume_candidate(root2, None, "L01") is None


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))
    data_root = str(tmp_path / "appdata")
    os.makedirs(data_root, exist_ok=True)
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    w = mw.MainWindow(SPEC, inst, data_root, str(tmp_path / "log.txt"), None, [], set())
    w.check_updates = lambda force=False, on_start=False: None
    w.shown = []
    w.inform = lambda title, text: w.shown.append(("info", text))
    w.warn = lambda title, text: w.shown.append(("warn", text))
    yield w
    if w.workbench is not None:
        w.workbench.wait_jobs(15_000)
    w.close()
    w.deleteLater()


def _prepare_resume(win, proj, labeler="L01"):
    sess.add_labeler(win.data_root, labeler)
    win.settings.setValue("session/last_labeler", labeler)
    pv.push_recent(win.settings, proj.root)


def test_mainwindow_auto_resume_opens_single_session_without_dialog(qapp, tmp_path, win):
    proj = make_long_project(tmp_path)
    close_wb(open_wb(qapp, proj, ctx(SID_A, "primary", "L01")))          # 이어 쓸 1차 세션 하나
    _prepare_resume(win, proj)
    asked = []
    win.session_chooser = lambda out: asked.append(out.project.root) or None
    assert win.resume_enabled() and win.a_resume.isChecked()                 # 기본 켬
    assert win.auto_resume() is True
    assert asked == []                                                       # 세션 화면을 띄우지 않았다
    wb = win.workbench
    assert wb is not None and wb.session is not None and wb.session.session_id == SID_A
    msg = win.statusBar().currentMessage()
    assert msg.startswith("이어서 엽니다: ") and SID_A in msg
    assert wb.wait_jobs(15_000)
    qapp.processEvents()
    assert wb.store is not None and not wb.store.read_only, wb.notes
    win.show_home()
    # 이어 쓸 세션이 둘이면(조정 세션 더함) 세션 화면을 띄운다(지금처럼)
    m4 = os.listdir(os.path.join(proj.root, "labels"))[0].split(".")[1]
    _touch(proj.root, "labels", f"L01-AD-20261022-ad01.{m4}.jsonl")
    assert win.auto_resume() is True and len(asked) == 1
    assert win.workbench.session is None                                     # 가짜 세션 화면이 보기 전용을 골랐다
    win.workbench.wait_jobs(15_000)
    win.show_home()
    # 설정을 끄면 아무것도 열지 않는다(설정 파일에 남는다)
    win.a_resume.setChecked(False)
    assert not win.resume_enabled() and str(win.settings.value("startup/resume_last")).lower() == "false"
    assert win.auto_resume() is False and win.workbench is None


def test_auto_resume_skips_when_no_recent_or_other_labeler(qapp, tmp_path, win):
    assert win.auto_resume() is False                                        # 최근 프로젝트 없음
    proj = make_long_project(tmp_path)
    close_wb(open_wb(qapp, proj, ctx(SID_A, "primary", "L01")))
    _prepare_resume(win, proj, labeler="L02")                                # L02 는 이 프로젝트에 세션이 없다
    asked = []
    win.session_chooser = lambda out: asked.append(1) or None
    assert win.auto_resume() is True and asked == [1]
    assert win.statusBar().currentMessage() == "이어서 엽니다: 합성 방송"


def test_auto_resume_session_refuses_read_only(tmp_path, win):
    class Proj:
        root, data, read_only = str(tmp_path), {}, True

    class Out:
        project, read_only = Proj(), True
    win.settings.setValue("session/last_labeler", "L01")
    assert win.auto_resume_session(Out()) is None


# ============================================================================ 2. 칸 나누기 크기
def test_split_size_helpers():
    assert wbm.parse_split_sizes("700,500", 2) == [700, 500]
    assert wbm.parse_split_sizes(["700", "500"], 2) == [700, 500]           # ini 가 목록으로 돌려줄 때
    for bad in ("", "700", "a,b", "-1,5", "0,0", None, 7):
        assert wbm.parse_split_sizes(bad, 2) is None
    assert wbm.scale_split_sizes([700, 500], 1200) == [700, 500]
    assert wbm.scale_split_sizes([600, 400], 1500) == [900, 600]


def test_workbench_saves_and_restores_splits_with_adjudication_widening(qapp, tmp_path):
    st = _settings(tmp_path)
    wb = wbm.Workbench(parts=PARTS)
    wb.settings = st
    wb.resize(1366, 768)
    wb.show()
    qapp.processEvents()
    total = sum(wb.hsplit.sizes())
    assert total > 0
    wb.hsplit.setSizes([total // 4, total - total // 4])
    vt = sum(wb.vsplit.sizes())
    wb.vsplit.setSizes([vt // 2, vt - vt // 2])
    want_h, want_v = wb.hsplit.sizes(), wb.vsplit.sizes()
    wb.close_workbench("user")                                               # 닫을 때 남긴다
    wb.deleteLater()
    assert wbm.parse_split_sizes(st.value("layout/workbench_hsplit"), 2) == want_h
    assert wbm.parse_split_sizes(st.value("layout/workbench_vsplit"), 2) == want_v

    wb2 = wbm.Workbench(parts=PARTS)
    wb2.settings = st
    wb2.resize(1366, 768)
    wb2.show()
    qapp.processEvents()
    wb2.restore_splits()
    assert abs(wb2.hsplit.sizes()[0] - want_h[0]) <= 2 and abs(wb2.vsplit.sizes()[0] - want_v[0]) <= 2
    # 조정 모드로 넓혀 둔 동안: 넓힌 크기는 그대로, 돌아갈 크기(_split_before)만 바뀐다. 저장도 넓히기 전 크기로
    wb2.hsplit.setSizes([300, sum(wb2.hsplit.sizes()) - 300])
    wide = wb2.hsplit.sizes()                                                # 최소 폭 때문에 300 보다 클 수 있다
    wb2._split_before = [100, 100]
    wb2.restore_splits()
    assert wb2.hsplit.sizes() == wide
    assert abs(wb2._split_before[0] - want_h[0]) <= 2
    st.setValue("layout/workbench_hsplit", "")
    wb2.save_splits()
    assert wbm.parse_split_sizes(st.value("layout/workbench_hsplit"), 2) == wb2._split_before
    wb2.close_workbench("user")
    wb2.deleteLater()


# ============================================================================ 3. 이모티콘 표시
def test_collapse_emotes():
    f = cp.collapse_emotes
    assert f("그냥 글") == "그냥 글"
    assert f("{:101:}") == "[이모티콘]"
    assert f("{:101:}{:x_12:}{:10A:}") == "[이모티콘×3]"
    assert f("{:101:} {:x_12:}") == "[이모티콘×2]"                           # 공백만 사이에 둔 연속도 하나로
    assert f("안녕 {:x_12:}{:x_12:} 반가워 {:10A:}") == "안녕 [이모티콘×2] 반가워 [이모티콘]"
    assert f("{:101:}ㅋㅋ{:101:}") == "[이모티콘]ㅋㅋ[이모티콘]"
    assert f("{: 공백 :} {:} 중괄호{x}") == "{: 공백 :} {:} 중괄호{x}"      # 코드 모양이 아니면 그대로
    assert cp.display_text("{:101:}\n줄") == "[이모티콘] 줄"


def test_chat_panel_shows_token_but_keeps_raw_text_and_search(qapp):
    raw = "{:101:}{:x_12:} 합성 인사 {:10A:}"
    recs = [{"chat_id": "c" + f"{i:020x}", "t_ms": 1_000 * i, "kind": "text", "viewer_id": f"v{i}",
             "nickname_masked": "시**", "text": t, "role": "viewer"}
            for i, t in enumerate((raw, "그냥 합성 채팅", "{:d_1:}"))]
    p = cp.ChatPanel()
    p.set_messages(recs)
    m = p.model
    shown = m.data(m.index(0, cp.COL_TEXT), Qt.DisplayRole)
    assert shown == "[이모티콘×2] 합성 인사 [이모티콘]"
    assert p.chat_data().texts[0] == raw                                     # 저장 값은 그대로
    assert m.data(m.index(2, cp.COL_TEXT), Qt.DisplayRole) == "[이모티콘]"
    p.set_filter("이모티콘")                                                 # 보이는 글자로 찾기
    assert p.row_count() == 2
    p.set_filter("{:x_12:}")                                                 # 원문 코드로도 찾기
    assert p.row_count() == 1
    p.set_filter("합성")
    assert p.row_count() == 2
    p.set_filter("인사 [이모")                                                # 표시 글자의 이어진 부분
    assert p.row_count() == 1
    p.set_filter("")
    assert p.row_count() == 3
    p.deleteLater()


def test_reaction_editor_target_list_shows_emote_token(env):
    recs = make_chats()
    recs[0]["text"] = "{:101:}{:x_12:}{:10A:} 합성 대답 {:x_12:}"
    env.ed._chats = cp.ChatData(recs)
    env.ed.open_for(rcid(1), 112_000)
    lines = [env.ed.chat_list.item(i).text() for i in range(env.ed.chat_list.count())]
    mine = [x for x in lines if "합성 대답" in x]
    assert mine and "[이모티콘×3] 합성 대답 [이모티콘]" in mine[0] and "{:" not in mine[0]
    assert "[이모티콘×3]" in env.ed.quick_target.text()
    assert env.ed._chats.texts[0] == recs[0]["text"]


# ============================================================================ 4. 파일 대화상자 시작 폴더
def test_last_dir_remember_and_default_projects_dir(tmp_path, monkeypatch):
    from app import paths
    st = _settings(tmp_path)
    home = os.path.expanduser("~")
    fb = tmp_path / "기본"
    assert pv.last_dir(st, pv.DIR_MEDIA) == home
    assert pv.last_dir(st, pv.DIR_MEDIA, str(fb)) == home                    # 없는 기본 폴더는 쓰지 않는다
    fb.mkdir()
    assert pv.last_dir(st, pv.DIR_MEDIA, str(fb)) == str(fb)
    used = tmp_path / "영상 폴더"
    used.mkdir()
    pv.remember_dir(st, pv.DIR_MEDIA, str(used))
    pv.remember_dir(st, pv.DIR_MEDIA, str(tmp_path / "없는 폴더"))           # 없는 폴더는 남기지 않는다
    assert pv.last_dir(st, pv.DIR_MEDIA, str(fb)) == os.path.normpath(str(used))
    used.rmdir()
    assert pv.last_dir(st, pv.DIR_MEDIA, str(fb)) == str(fb)                 # 사라졌으면 기본으로
    # 기본 프로젝트 위치: <앱 상위>\chatcoach-data\projects → <data>\projects → 홈
    app_dir = tmp_path / "dev" / "chatcoach"
    app_dir.mkdir(parents=True)
    monkeypatch.setattr(paths, "install_root", lambda: str(app_dir))
    data_root = tmp_path / "appdata"
    assert pv.default_projects_dir(str(data_root)) == home
    (data_root / "projects").mkdir(parents=True)
    assert pv.default_projects_dir(str(data_root)) == str(data_root / "projects")
    sib = tmp_path / "dev" / "chatcoach-data" / "projects"
    sib.mkdir(parents=True)
    assert pv.default_projects_dir(str(data_root)) == str(sib)


def test_project_view_remembers_parent_of_opened_project(qapp, tmp_path, monkeypatch):
    from app import paths
    proj = make_long_project(tmp_path)
    st = _settings(tmp_path)
    view = pv.ProjectView(st)
    view.data_root = str(tmp_path / "appdata")
    with monkeypatch.context() as m:
        m.setattr(paths, "install_root", lambda: str(tmp_path / "없음" / "앱"))
        assert view.start_folder() == os.path.expanduser("~")
    got = []
    view.projectOpened.connect(got.append)
    monkeypatch.setattr(view, "choose_folder", lambda: proj.root)
    out = view.choose_and_open()
    try:
        assert out is not None and out.ok and got
        assert view.start_folder() == os.path.normpath(os.path.dirname(proj.root))
    finally:
        pv.release_outcome(out)
        view.deleteLater()


def test_media_dialog_starts_in_linked_video_folder_then_last_used(qapp, tmp_path, monkeypatch):
    st = _settings(tmp_path)
    wb = wbm.Workbench(parts=PARTS)
    wb.settings = st
    last = tmp_path / "지난 영상"
    last.mkdir()
    cur = tmp_path / "연결된 영상"
    cur.mkdir()
    seen = []
    pick = str(last / "합성.mp4")
    monkeypatch.setattr(wbm.QFileDialog, "getOpenFileName",
                        staticmethod(lambda parent, title, start, flt: (seen.append(start), (pick, ""))[1]))
    assert wb.choose_media_file() == os.path.normpath(pick)
    assert seen[-1] == os.path.expanduser("~")                               # 처음: 기억 없음 → 홈
    assert wb.media_start_dir() == os.path.normpath(str(last))               # 고른 영상의 폴더를 기억
    wb.media_path = str(cur / "연결.mp4")
    assert wb.media_start_dir() == str(cur)                                  # 연결된 영상이 있으면 그 폴더
    wb.close_workbench("user")
    wb.deleteLater()


def test_backup_dialog_starts_in_given_folder(qapp, tmp_path, monkeypatch):
    from app.ui import ops_dialogs
    seen = []
    monkeypatch.setattr(ops_dialogs.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda parent, title, start: (seen.append(start), "")[1]))
    dlg = ops_dialogs.BackupDialog(default_dir=str(tmp_path / "기본"), start_dir=str(tmp_path))
    dlg._choose_dir()
    assert seen == [str(tmp_path)] and dlg.out_dir is None
    dlg.deleteLater()


# ============================================================================ 5. 업데이트 확인 실패 문구
def test_update_404_and_network_failures_stay_quiet(qapp, tmp_path, win):
    bar = win.statusBar()
    wb_msg = "구간 경계 추가(B)는 ① 구간 모드에서 합니다"
    bar.showMessage(wb_msg)
    r404 = updater.CheckResult("failed", "업데이트 확인 실패 — 1차: HTTP Error 404: Not Found / 2차: HTTP Error 404",
                               "network")
    win._on_check(r404)
    assert bar.currentMessage() == wb_msg                                    # 작업대 문장이 그대로
    assert not win.status_chip.isVisibleTo(win) and win.status_chip.text() == ""
    assert "404" in win.update_status                                        # 문제 보고에는 남는다
    # 직접 [지금 확인] 을 누른 경우만 쉬운 문장
    win._manual_check = True
    win._on_check(r404)
    assert bar.currentMessage() == "아직 받을 수 있는 업데이트가 없습니다"
    win._manual_check = True
    win._on_check(updater.CheckResult("failed", "업데이트 확인 실패 — 1차: <urlopen error timed out>", "network"))
    assert "인터넷 연결" in bar.currentMessage() and "urlopen" not in bar.currentMessage()
    # 최신: 칩 없음, 직접 확인이면 한 줄
    bar.showMessage(wb_msg)
    win._on_check(updater.CheckResult("latest"))
    assert bar.currentMessage() == wb_msg and not win.status_chip.isVisibleTo(win)
    # 할 일이 있는 실패(서명)는 칩으로 보인다
    win._on_check(updater.CheckResult("failed", "서명 불일치", "signature"))
    assert win.status_chip.isVisibleTo(win) and "서명" in win.status_chip.text()


def test_manual_check_menu_sets_manual_flag(win):
    calls = []
    win.check_updates = lambda force=False, on_start=False: calls.append((force, win._manual_check))
    win.manual_check_updates()
    assert calls == [(True, True)]


# ============================================================================ 6. D-16 6b recheck_incomplete
def test_export_recheck_incomplete_is_lenient_not_strict():
    row = {"eval": {"excluded_reasons": []}}
    export_mod._apply_reasons(row, ["recheck_incomplete"])
    assert row["eval"]["strict"] is False and row["eval"]["lenient"] is True
    row = {"eval": {"excluded_reasons": []}}
    export_mod._apply_reasons(row, ["recheck_incomplete", "confidence_probably"])
    assert row["eval"]["strict"] is False and row["eval"]["lenient"] is True
    row = {"eval": {"excluded_reasons": []}}
    export_mod._apply_reasons(row, ["recheck_incomplete", "grade_unclear"])
    assert row["eval"]["lenient"] is False
    assert row["eval"]["excluded_reasons"] == ["grade_unclear", "recheck_incomplete"]   # 참고에서도 빠지는 사유가 앞
    assert "recheck_incomplete" in export_mod.LENIENT_OK and "recheck_incomplete" in ac.LENIENT_OK
    # 계단표: recheck_incomplete 는 참고 점수 대상 줄(human_disagreement) 다음에 센다
    for order in (export_mod.EXCLUDE_ORDER, ac.ORDER):
        assert order.index("recheck_incomplete") > order.index("human_disagreement")
        assert all(r in (export_mod.LENIENT_OK | ac.LENIENT_OK) for r in order[order.index("recheck_incomplete"):])


def test_ai_compare_lenient_keeps_recheck_incomplete():
    from tests.test_ai_compare import ai_doc, ai_rx, golden_ai, golden_final, item, row, steps, unsealed_run
    base = ac.compare(unsealed_run(golden_ai()), golden_final(), human_gate=True)["e1"]
    fin = golden_final()
    fin["items"].append(item(row(22, "direct", reasons=["recheck_incomplete"])))
    rx = golden_ai()["reactions"] + [ai_rx(22, "direct", 22_500)]
    out = ac.compare(unsealed_run(ai_doc(reactions=rx)), fin, human_gate=True)["e1"]
    assert out["lenient"]["n"] == base["lenient"]["n"] + 1                   # 참고에는 들어간다
    assert out["strict"]["n"] == base["strict"]["n"]                         # 엄격에서는 빠진다
    s = steps(out["cascade"])
    assert s["recheck_incomplete"][0] == 1
    assert s["human_disagreement"][1] == out["lenient"]["n"]                 # '참고 점수 대상' 줄 = 참고 n


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
