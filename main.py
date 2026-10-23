# vendored from vtubeadmirer/clipslicer@07d13cc (main.py) — 변경: 02 6-5 의 시작 순서(crashguard, Qt 플러그인 환경변수 정리,
#   업데이트 잠금·단일 인스턴스, 설치 위치 검사, 비정상 종료 표식), --selftest 인자 확장, WebEngine·--site 삭제,
#   _backup 삭제는 창 표시 뒤 (ClipSlicer 는 창을 만들기 전에 지웠다).
"""ChatCoach 실행 진입점."""
from __future__ import annotations

import os
import sys
import time
import traceback

_T0 = time.perf_counter()   # 시작 시간 측정 (02 9장: 시작 → 창 표시)
STARTUP_FLAGS = ("--updated", "--update-failed", "--update-blocked", "--rolled-back")   # 마지막 = updater.ROLLBACK_FLAG


def parse_args(argv: list[str]) -> dict:
    """아주 작은 인자 파서. argparse 는 Qt 인자와 섞이면 불편하다.

    한 번에 열기(08 D-16·D-29 3): --project <폴더> 는 그 프로젝트를 연다(마지막 작업 이어서 열기 대신). --analysis 는 분석
    세션으로 열고 나란히 보기를 띄운다. --confirm 은 거기에 'AI 확인 큐' 필터까지(--analysis 를 뜻한다). 없으면 메뉴 길 그대로."""
    a: dict = {"selftest": None, "expect_version": None, "expect_channel": None, "data_dir": None,
               "update_base_url": None, "flags": set(), "project": None, "analysis": False, "confirm": False}
    i = 0
    while i < len(argv):
        x = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if x == "--selftest":
            a["selftest"] = nxt or "selftest.json"
            i += 2 if nxt else 1
            continue
        if x in ("--expect-version", "--expect-channel", "--data-dir", "--update-base-url", "--project") \
                and nxt is not None:
            a[x[2:].replace("-", "_")] = nxt
            i += 2
            continue
        if x in ("--analysis", "--confirm"):
            a[x[2:]] = True
        if x in STARTUP_FLAGS:
            a["flags"].add(x)
        i += 1
    if a["confirm"]:
        a["analysis"] = True
    return a


def start_request(args: dict) -> dict | None:
    """parse_args 결과 → MainWindow.start_request ({project, analysis, confirm}) 또는 None(--project 없음 = 메뉴 길)."""
    if not args.get("project"):
        return None
    return {"project": os.path.abspath(args["project"]), "analysis": bool(args.get("analysis")),
            "confirm": bool(args.get("confirm"))}


def _clean_qt_env(code_dir: str) -> None:
    """다른 Qt 설치가 남긴 환경변수 때문에 창이 안 뜨는 것을 막는다 (S-48). 번들 실행에서만."""
    for k in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH"):
        os.environ.pop(k, None)
    plugins = os.path.join(code_dir, "PySide6", "plugins")
    if os.path.isdir(plugins):
        os.environ["QT_PLUGIN_PATH"] = plugins
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(plugins, "platforms")


def apply_dark_theme(app) -> None:
    from PySide6.QtGui import QColor, QPalette
    from app.style import QSS
    app.setStyle("Fusion")
    p = QPalette()
    bg, base, text = QColor(37, 37, 42), QColor(28, 28, 32), QColor(228, 228, 232)
    p.setColor(QPalette.Window, bg)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, bg)
    p.setColor(QPalette.ToolTipBase, base)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor(50, 50, 56))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.Highlight, QColor(66, 165, 245))
    p.setColor(QPalette.HighlightedText, QColor(10, 10, 10))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 120, 128))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 120, 128))
    app.setPalette(p)
    app.setStyleSheet(QSS)


def app_icon():
    from PySide6.QtGui import QIcon
    from app.paths import code_dir, install_root
    for name in ("icon.ico", "icon.png"):
        for d in (os.path.join(code_dir(), "assets"), os.path.join(install_root(), "assets")):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return QIcon(p)
    return QIcon()


def main() -> int:
    args = parse_args(sys.argv[1:])
    from app import paths
    if args["data_dir"]:
        paths.set_data_root(args["data_dir"])
    if args["selftest"]:   # 1. 창 없이 점검만
        from app import selftest
        return selftest.run(args["selftest"], args["expect_version"], args["expect_channel"])

    data_root = paths.data_root()
    from app import __version__ as _ver
    from app import crashguard   # 2.
    crashguard.enable(data_root, version=_ver)
    stale_pids = crashguard.stale_markers(data_root)
    crashguard.clear_stale_markers(data_root)
    # 같은 버전에서 이어진 비정상 종료 횟수 (업데이트 뒤 연속 2회면 되돌리기 제안, 04 6-9)
    abnormal_streak = crashguard.note_start(data_root, _ver, bool(stale_pids))
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:   # 3.
        _clean_qt_env(paths.code_dir())

    from PySide6.QtCore import QLockFile, Qt, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app import __version__, updater
    from app.appspec import SPEC
    from app.i18n import tr
    from app.logging_setup import LOG, install_error_dialog, setup as setup_logging

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)   # 4.
    app.setApplicationName(SPEC.name)
    app.setWindowIcon(app_icon())
    apply_dark_theme(app)
    log_file = setup_logging(__version__)
    LOG.info("인자: %s | 데이터: %s (%s)", [a for a in sys.argv[1:] if not os.path.exists(a)], data_root, paths.data_mode())

    # 5. 업데이트 적용 중 잠금, 단일 인스턴스
    if updater.update_in_progress(data_root) and not args["flags"]:
        LOG.warning("업데이트 적용 중이라 실행하지 않음")
        QMessageBox.information(None, SPEC.name, tr("업데이트를 적용하는 중입니다. 끝나면 자동으로 다시 열립니다.\n잠시 뒤에 다시 실행해 주세요."))
        return 0
    instance_lock = QLockFile(os.path.join(data_root, "instance.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(200):
        LOG.warning("다른 창이 이미 열려 있음")
        QMessageBox.information(None, SPEC.name, tr("ChatCoach 가 이미 열려 있습니다. (같은 폴더에서는 한 창만 뜹니다)"))
        return 0

    # 6. 설치 위치 검사, 7. 비정상 종료 표식
    notes: list[str] = []
    install_root = paths.install_root()
    if paths.data_mode() == "fallback":
        notes.append(tr("프로그램 폴더에 쓸 수 없어 데이터를 %LOCALAPPDATA%\\ChatCoach\\data 에 둡니다 (폴백 모드). 이 위치에서는 폴더째 복사와 자동 업데이트가 되지 않습니다."))
    # 프로그램·데이터·백업 폴더 점검 (S-09 · S-49): OneDrive·UNC·긴 경로·쓰기 불가는 창 위쪽 띠, confirm 은 대화상자
    try:
        path_issues = paths.startup_path_issues()
    except Exception:
        LOG.exception("경로 점검 실패")
        path_issues = []
    for it in path_issues:
        LOG.info("경로 점검: %s %s %s", it.role, it.code, it.level)
    if updater.stale_update_lock(data_root):
        notes.append(tr("지난 업데이트가 중간에 멈춘 흔적이 있습니다. 문제가 있으면 수동 업데이트 안내를 보세요."))
    if stale_pids:
        notes.append(tr("이전 실행이 비정상 종료되었습니다 (pid {0}). data\\logs\\crash.log 를 확인하세요.").format(", ".join(map(str, stale_pids))))
    missing = paths.missing_tools()
    if missing:
        notes.append(tr("번들에 없는 파일: {0}").format(", ".join(missing)))

    installed = updater.load_installed(SPEC)
    # 9. 지난 교체의 _backup 삭제는 '첫 정상 종료 뒤 다음 시작' 에서만 (S-31, 04 6-9):
    #    --updated 로 켜진 지금 실행에서는 지우지 않고, --update-failed 면 사본을 남기며, 이전 실행이 비정상 종료였으면 남긴다.
    #    --rolled-back 실행도 지우지 않는다(되돌린 버전이 정상 종료한 다음 시작에 지운다).
    #    준비 폴더(_update) 정리는 사용 중 표식(ACTIVE)이 없는 것만 지우므로 언제 돌아도 안전하다.
    #    지울 차례인 실행에서는 되돌리기 메뉴를 처음부터 끈다(정리 스레드와 겹치지 않게).
    delete_backup = not (args["flags"] & {"--updated", "--update-failed", updater.ROLLBACK_FLAG}) and not stale_pids
    from app.ui.mainwindow import MainWindow   # 8.
    try:
        win = MainWindow(SPEC, installed, data_root, log_file, args["update_base_url"], notes, args["flags"],
                         abnormal_streak=abnormal_streak, delete_backup_pending=delete_backup and installed.frozen,
                         path_issues=path_issues)
    except Exception:
        LOG.exception("메인 창 생성 실패")
        QMessageBox.critical(None, tr("실행 실패"), tr("창을 만들지 못했습니다.\n\n") + traceback.format_exc().strip().splitlines()[-1])
        return 1
    install_error_dialog(win)
    win.session_backup = True
    # 인자 없이 켠 실행(바탕 화면 바로 가기)만: 마지막 프로젝트·세션을 이어서 연다(설정으로 끌 수 있음, 08 D-16 메모)
    req = start_request(args)
    win.resume_on_start = not args["flags"] and req is None
    # --project … [--analysis] [--confirm]: 시작 알림 뒤 그 프로젝트를 연다(분석 세션 · AI 확인 큐, 08 D-29 3)
    win.start_request = req
    win.show()
    LOG.info("창 표시: 시작 후 %.0f ms", (time.perf_counter() - _T0) * 1000)
    win.start_background_backups()

    def _cleanup():
        import threading

        def work():
            try:
                if delete_backup:
                    updater.cleanup_after_update(install_root, data_root, __version__)
                updater.cleanup_stage_dirs(install_root)
            except Exception:
                LOG.exception("업데이트 뒤 정리 실패")
        threading.Thread(target=work, name="post-update-cleanup", daemon=True).start()
    QTimer.singleShot(3000, _cleanup)
    app.aboutToQuit.connect(crashguard.mark_clean_exit)
    rc = app.exec()
    instance_lock.unlock()
    return rc


if __name__ == "__main__":
    sys.exit(main())
