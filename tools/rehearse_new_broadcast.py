"""'파일 → 새 방송…' 한 바퀴 리허설 (08 D-31 4): 실제 MainWindow 를 **임시 데이터 폴더**로 띄워 사람처럼 몬다.

메뉴 '파일 → 새 방송…' → 대화상자(VOD 번호 글자 입력 · [고르기…] · 라벨러 칸이 있으면 글자 입력) → [시작] → 진행기(실제 채팅
받기·실제 엔진 venv) → 분석 화면(작업대 '분석 보기')까지 한 줄로 돌고, 클릭 수·입력 칸 수·단계별 초·막힌 곳(뜻밖의 대화상자·
경고·실패 단계)·명령줄 필요 횟수를 숫자로 낸다.

- 데이터 폴더: %LOCALAPPDATA%\\ChatCoachSmoke\\rehearsal-<시각> (gui_smoke 와 같은 뿌리). 설정·라벨러 명부·machine.json·
  프로젝트(채팅 사본)가 모두 여기 생기고, 끝에 shutil.rmtree 로 지운다(실제 채팅 사본 — 휴지통 금지). --keep 이면 남긴다(디버그).
- 프로젝트 자리는 그 데이터 폴더의 projects\\ 다(engine_run.default_projects_root 의 --data-dir 규칙). chatcoach-data\\projects·
  시범 폴더·ai 폴더는 열지 않는다. 엔진 모델은 엔진 규칙의 모델 폴더(<chatcoach-data>\\models)에서 읽기만 한다.
- 파일 고르기 창(운영체제 창)은 몰 수 없어 대화상자의 choose_file 을 그 파일을 돌려주는 함수로 바꿔 끼우고, 사람 클릭은
  FILE_DIALOG_CLICKS(파일 한 번 + [열기])로 센다. 메뉴는 QAction.trigger 로 열고 2클릭('파일' · '새 방송…')으로 센다.
- --limit-min N: 숨은 설정 [new_broadcast] asr_limit_min = N(영상 처음 N분만 전사 — 리허설에서만).
- 출력은 숫자·코드·단계 이름만(경로·제목·채팅 본문·닉네임·전사문 없음). 마지막 줄은 JSON 한 줄.

사용: .venv\\Scripts\\python tools\\rehearse_new_broadcast.py --vod <번호> --video <영상 파일> [--limit-min 20] [--offscreen]
종료 코드: 0 분석 화면까지 감·중간 파일 0·데이터 폴더 지움 · 1 그렇지 않음 · 2 입력 거부(시범 자리 등)
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import json
import os
import shutil
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FILE_DIALOG_CLICKS = 2                    # 운영체제 파일 고르기 창: 파일 한 번 + [열기] (폴더는 ClipSlicer 자리에서 열림)
MENU_CLICKS = 2                           # '파일' · '새 방송…'
PILOT_MARKS = ("chatcoach-pilot", os.sep + "pilot-", "sealed-work")


def _tmp_root() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "ChatCoachSmoke", "rehearsal-" + datetime.now().strftime("%Y%m%d-%H%M%S"))


def _leftovers(root: str) -> int:
    """데이터 폴더 안에 남은 중간 파일(전사 wav·쓰던 asr·engine_to_ai 단계 파일) 수 — 이름만 센다."""
    n = 0
    for d, _sub, files in os.walk(root):
        for f in files:
            low = f.lower()
            if low.endswith(".wav") or ".part" in low or low.startswith("asr-link"):
                n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="'파일 → 새 방송…' 한 바퀴 리허설(임시 데이터 폴더, 끝에 지움)")
    ap.add_argument("--vod", required=True, help="치지직 VOD 번호")
    ap.add_argument("--video", required=True, help="영상 파일")
    ap.add_argument("--limit-min", type=int, default=None, help="영상 처음 N분만 전사(숨은 설정, 리허설용)")
    ap.add_argument("--labeler", default="L01", help="라벨러 칸이 나오면 적을 ID (기본 L01)")
    ap.add_argument("--offscreen", action="store_true", help="화면 없이(QT_QPA_PLATFORM=offscreen)")
    ap.add_argument("--keep", action="store_true", help="임시 데이터 폴더를 지우지 않는다(실제 채팅 사본이 남는다 — 디버그만)")
    ap.add_argument("--data-dir", help="앞선 --keep 실행의 데이터 폴더를 다시 쓴다(같은 번호면 이어서, 디버그만 — "
                                       "%%LOCALAPPDATA%%\\ChatCoachSmoke\\rehearsal-* 만 받는다)")
    ap.add_argument("--tick", type=float, default=15.0, help="진행 글을 찍는 간격(초)")
    a = ap.parse_args(argv)
    video = os.path.abspath(a.video)
    low = video.lower()
    if any(m in low for m in PILOT_MARKS):
        print(json.dumps({"error": "pilot_place"}))
        return 2
    if not os.path.isfile(video):
        print(json.dumps({"error": "video_missing"}))
        return 2
    if a.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    data = _tmp_root()
    if a.data_dir:
        d = os.path.abspath(a.data_dir)
        if os.path.dirname(d) != os.path.dirname(data) or not os.path.basename(d).startswith("rehearsal-") \
                or not os.path.isdir(d):
            print(json.dumps({"error": "data_dir"}))
            return 2
        data = d
    os.makedirs(data, exist_ok=True)
    from app import paths
    paths.set_data_root(data)
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("ChatCoachRehearsal")
    import main as main_mod
    main_mod.apply_dark_theme(app)
    from app import updater
    from app.appspec import SPEC
    from app.ui.mainwindow import MainWindow
    from app.ui.workbench import ENGINE_TRACK

    rec: dict = {"clicks": 0, "fields": 0, "blocked": [], "heads": [], "dialog_s": None}
    t0 = time.perf_counter()
    win = MainWindow(SPEC, updater.load_installed(SPEC), data, "", None, [], set())
    win.check_updates = lambda *x, **k: None                     # 업데이트 확인(네트워크)은 하지 않는다
    win.warn = lambda title, text: rec["blocked"].append({"kind": "warn", "line": str(text).splitlines()[0][:160]})
    win.inform = lambda title, text: rec["blocked"].append({"kind": "info", "line": str(text).splitlines()[0][:160]})
    win.show()
    end = time.perf_counter() + 0.6
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.01)
    if a.limit_min:
        win.settings.setValue(win.NEW_BROADCAST_LIMIT_KEY, int(a.limit_min))
        win.settings.sync()
    print(f"== 리허설: 데이터 폴더 새로 만듦 · 전사 범위 {'처음 ' + str(a.limit_min) + '분' if a.limit_min else '전체'}",
          flush=True)

    def fill() -> None:
        dlg = win.new_broadcast_dlg
        if dlg is None or not dlg.isVisible():
            QTimer.singleShot(100, fill)
            return
        rec["dialog_s"] = round(time.perf_counter() - t_menu, 2)
        dlg.ed_vod.setFocus()
        QTest.keyClicks(dlg.ed_vod, a.vod)                        # 칸 1: VOD 번호(글자 입력)
        rec["fields"] += 1
        dlg.choose_file = lambda: video                          # 운영체제 파일 창 대신(사람은 FILE_DIALOG_CLICKS)
        QTest.mouseClick(dlg.btn_pick, Qt.LeftButton)            # [고르기…]
        rec["clicks"] += 1 + FILE_DIALOG_CLICKS
        rec["fields"] += 1                                       # 칸 2: 영상 파일
        rec["labeler_field"] = dlg.ed_labeler is not None
        if dlg.ed_labeler is not None:                           # 이 PC 에 라벨러가 없을 때만 칸 3
            QTest.keyClicks(dlg.ed_labeler, a.labeler)
            rec["fields"] += 1
        QTest.mouseClick(dlg.btn_start, Qt.LeftButton)           # [시작]
        rec["clicks"] += 1

    def watch() -> None:
        """진행 글(단계 이름·경과·숫자)을 찍고, 뜻밖의 모달(경고 상자 등)은 막힌 곳으로 세고 닫는다."""
        txt = win.last_progress_text or ""
        head = txt.split(" · ")[0] if txt else ""
        if head and head not in rec["heads"]:
            rec["heads"].append(head)
        m = QApplication.activeModalWidget()
        if isinstance(m, QMessageBox):
            rec["blocked"].append({"kind": "modal", "line": (m.text() or "")[:160]})
            m.reject()

    def show_tick() -> None:
        if win.last_progress_text:
            print("  .. " + win.last_progress_text, flush=True)

    w_timer = QTimer()
    w_timer.timeout.connect(watch)
    w_timer.start(300)
    p_timer = QTimer()
    p_timer.timeout.connect(show_tick)
    p_timer.start(int(a.tick * 1000))
    QTimer.singleShot(150, fill)
    t_menu = time.perf_counter()
    rec["clicks"] += MENU_CLICKS
    win.a_new_broadcast.trigger()                                # 대화상자 → [시작] → 진행 → 분석 화면까지 여기서 돈다
    t_run_end = time.perf_counter()
    res = win.last_new_broadcast
    wb = win.workbench
    view: dict = {"workbench": wb is not None}
    if wb is not None:
        ok_jobs = wb.wait_jobs(180_000)
        app.processEvents()
        view.update(session=getattr(wb.session, "session_kind", None), analysis_view=wb.analysis_action.isChecked(),
                    jobs_done=ok_jobs, engine_markers=len(wb.timeline.markers(ENGINE_TRACK))
                    if hasattr(wb.timeline, "markers") and callable(wb.timeline.markers) else None,
                    status_line=win.statusBar().currentMessage() == (win.last_new_broadcast_line or ""))
    t_view = time.perf_counter()
    w_timer.stop()
    p_timer.stop()
    left = _leftovers(data)
    steps = []
    if res is not None:
        for s in res.steps:
            keep = {k: v for k, v in s.numbers.items()
                    if isinstance(v, (int, float, type(None))) or k in ("device", "engine_version", "mode",
                                                                         "asr_problem_kinds")}
            steps.append({"key": s.key, "status": s.status, "elapsed_s": s.elapsed_s, "code": s.code, "numbers": keep})
    out = {
        "clicks": rec["clicks"], "fields": rec["fields"], "labeler_field": rec.get("labeler_field"),
        "file_dialog_clicks_assumed": FILE_DIALOG_CLICKS, "menu_clicks": MENU_CLICKS,
        "dialog_open_s": rec["dialog_s"], "run_s": res.elapsed_s if res is not None else None,
        "open_view_s": round(t_view - t_run_end, 1), "total_s": round(t_view - t_menu, 1),
        "ok": bool(res is not None and res.ok), "line": win.last_new_broadcast_line,
        "progress_heads": rec["heads"], "steps": steps, "view": view, "blocked": rec["blocked"],
        "cli_needed": 0, "leftover_intermediate": left, "limit_min": a.limit_min,
    }
    win.close()
    for _ in range(50):
        app.processEvents()
        time.sleep(0.02)
    win.deleteLater()
    app.processEvents()
    removed = False
    if not a.keep:
        for _ in range(10):
            try:
                shutil.rmtree(data)                              # 실제 채팅 사본 — 휴지통 금지(D-15 8)
            except OSError:
                time.sleep(1.0)
                app.processEvents()
                continue
            break
        removed = not os.path.exists(data)
    out["data_root_removed"] = removed
    print(json.dumps(out, ensure_ascii=False))
    good = out["ok"] and view.get("analysis_view") and left == 0 and (removed or a.keep)
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
