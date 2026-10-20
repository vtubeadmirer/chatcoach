"""창 골격: 메뉴·상태바·화면 전환(프로젝트 화면 ↔ 작업대)·업데이트 연동.

화면 전환 (02 7-1): 시작은 프로젝트 화면(최근 목록·폴더에서 열기, app/ui/project_view.py). 프로젝트를 열면
세션 선택 화면(app/ui/session_dialog.py, 08 D-09 1)을 띄우고, 고른 SessionContext 로 작업대(app/ui/workbench.py)를
만든다. 작업대가 store.open(lock_factory=QLockFile, 열 때 잡은 잠금을 넘김)으로 라벨 기록을 연다. 고르지 않으면(보기 전용·
취소) 세션 없이 연다 — 라벨 저장·분석 보기가 꺼진다. [파일 → 프로젝트 닫기] 나 다른 프로젝트 열기는 작업대를 닫고
(플레이어·워커·session.close 줄·잠금 정리) 새로 만든다. session.close 의 reason: 프로젝트 닫기 'user',
다른 프로젝트 열기 'switch_project', 앱 종료 'app_exit'.

업데이트 연동 흐름 (04 6장): 시작 30초 뒤 백그라운드 확인 → 상태바 3상태 → '새 버전' 버튼 → 대화상자 →
준비(진행률·취소) → 교체 전 조건 → 적용(시작 표식 확인) → 앱 종료. 확인 시각은 updater 가 결과 뒤에 쓴다.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMessageBox, QProgressDialog, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, updater
from ..appspec import AppSpec
from ..i18n import tr
from ..workers import UpdateCheckWorker, UpdateStageWorker
from .project_view import ProjectView, load_recent
from .update_dialog import UpdateDialog, open_trusted_url

LOG = logging.getLogger("chatcoach")
# 시험 채널 전용: 환경 변수가 1 이면 새 버전을 찾았을 때 대화상자 없이 바로 준비·적용한다 (04 12-5 손 시험·자동 왕복 시험용).
# release 채널은 읽지 않는다.
AUTO_UPDATE_ENV = "CHATCOACH_AUTO_UPDATE"
MANUAL_GUIDE = ("수동 업데이트: 앱을 끄고 릴리스 페이지에서 전체 zip 을 받아 새 폴더에 푼 뒤, 옛 폴더의 data\\ 를 "
                "새 폴더의 ChatCoach\\ 안으로 복사합니다 (docs/install.md, 04 8장).")


class MainWindow(QMainWindow):
    def __init__(self, spec: AppSpec, installed: updater.InstalledInfo, data_root: str, log_file: str,
                 base_url: str | None, startup_notes: list[str], flags: set[str]):
        super().__init__()
        self.spec, self.installed, self.data_root, self.log_file = spec, installed, data_root, log_file
        self.base_url = base_url
        self.auto = installed.channel in ("test", "source") and os.environ.get(AUTO_UPDATE_ENV, "") == "1"
        self.state_path = os.path.join(data_root, "update", "state.json")
        self._check_worker: UpdateCheckWorker | None = None
        self._stage_worker: UpdateStageWorker | None = None
        self._result: updater.CheckResult | None = None
        self.setWindowTitle(f"{spec.name} {__version__}")
        self.resize(760, 480)

        central = QWidget()
        lay = QVBoxLayout(central)
        title = QLabel(f"{spec.name} {__version__}")
        title.setObjectName("panelTitle")
        lay.addWidget(title)
        info = QLabel(tr("프로젝트를 열면 작업대(영상·타임라인·채팅)가 열립니다. 메뉴 [업데이트] 에서 새 버전을 확인할 수 있습니다.\n"
                         "채널 {0} · 데이터 폴더 {1}").format(installed.channel, data_root))
        info.setWordWrap(True)
        lay.addWidget(info)
        self.notes_label = QLabel("\n".join("• " + n for n in startup_notes))
        self.notes_label.setObjectName("hint")
        self.notes_label.setWordWrap(True)
        self.notes_label.setVisible(bool(startup_notes))
        lay.addWidget(self.notes_label)
        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        lay.addWidget(self.banner)
        # ---- 프로젝트 화면 · 작업대 전환
        self.settings = QSettings(os.path.join(data_root, "settings.ini"), QSettings.Format.IniFormat)
        self.project_view = ProjectView(self.settings, self)
        self.project_view.projectOpened.connect(self.show_workbench)
        self.project_view.before_open = self._before_open
        lay.addWidget(self.project_view, 1)
        self.stack = QStackedWidget()
        self.stack.addWidget(central)
        self.home_page = central
        self.workbench = None
        self.session = None      # app.session.SessionContext — 지금 작업대의 세션(보기 전용이면 None)
        # 세션 고르기: outcome → SessionContext | None. None 이면 세션 선택 화면(모달). 시험은 바꿔 끼운다.
        self.session_chooser = None
        self.setCentralWidget(self.stack)

        m_file = self.menuBar().addMenu(tr("파일(&F)"))
        self._build_project_menu(m_file)
        a_quit = QAction(tr("종료(&X)"), self)
        a_quit.setShortcut("Ctrl+Q")
        a_quit.triggered.connect(self.close)
        m_file.addAction(a_quit)
        self.m_view = self.menuBar().addMenu(tr("보기(&V)"))
        self.m_view.menuAction().setVisible(False)
        # 니모닉은 &D: Alt+U 는 구간 폼의 '판단 불가' 단축키라 메뉴와 겹치면 둘 다 동작하지 않는다(애매한 단축키)
        m_upd = self.menuBar().addMenu(tr("업데이트(&D)"))
        a_check = QAction(tr("지금 확인(&C)"), self)
        a_check.triggered.connect(lambda: self.check_updates(force=True))
        m_upd.addAction(a_check)
        a_manual = QAction(tr("수동 업데이트 안내(&M)"), self)
        a_manual.triggered.connect(lambda: QMessageBox.information(self, tr("수동 업데이트"), tr(MANUAL_GUIDE)))
        m_upd.addAction(a_manual)
        m_help = self.menuBar().addMenu(tr("도움말(&H)"))
        a_report = QAction(tr("문제 보고 복사(&R)"), self)
        a_report.triggered.connect(self.copy_report)
        m_help.addAction(a_report)
        a_about = QAction(tr("정보(&A)"), self)
        a_about.triggered.connect(self.about)
        m_help.addAction(a_about)

        self.status_chip = QLabel(tr("업데이트: 아직 확인하지 않음"))
        self.status_chip.setObjectName("statusChip")
        self.statusBar().addPermanentWidget(self.status_chip)
        self.update_btn = QPushButton("")
        self.update_btn.setObjectName("updateBtn")
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(self.offer_update)
        self.statusBar().addPermanentWidget(self.update_btn)
        self.statusBar().showMessage(tr("준비"))

        if self.auto:
            LOG.info("시험 자동 업데이트 모드 (%s=1): 2초 뒤 확인, 새 버전이면 바로 적용", AUTO_UPDATE_ENV)
            QTimer.singleShot(2_000, lambda: self.check_updates(force=True))
        else:
            QTimer.singleShot(30_000, lambda: self.check_updates(force=False))   # 04 6-2: 시작 30초 뒤
        for flag in flags:
            if self.auto:
                LOG.info("시작 인자 %s (자동 모드라 대화상자 생략)", flag)
            else:
                QTimer.singleShot(400, lambda f=flag: self.notify_startup_flag(f))

    # ---------------------------------------------------------------- 프로젝트 · 화면 전환
    def _build_project_menu(self, m_file) -> None:
        a_open = QAction(tr("프로젝트 열기(&O)…"), self)
        a_open.setShortcut("Ctrl+O")
        a_open.triggered.connect(self.project_view.choose_and_open)
        m_file.addAction(a_open)
        self.m_recent = m_file.addMenu(tr("최근 프로젝트(&R)"))
        self.m_recent.aboutToShow.connect(self._fill_recent_menu)
        self._fill_recent_menu()
        self.a_close_project = QAction(tr("프로젝트 닫기(&W)"), self)
        self.a_close_project.triggered.connect(self.show_home)
        self.a_close_project.setEnabled(False)
        m_file.addAction(self.a_close_project)
        self.a_export = QAction(tr("라벨 내보내기(&E)…"), self)
        self.a_export.triggered.connect(lambda: self.workbench.choose_and_export() if self.workbench else None)
        self.a_export.setEnabled(False)
        m_file.addAction(self.a_export)
        m_file.addSeparator()

    def _fill_recent_menu(self) -> None:
        self.m_recent.clear()
        items = load_recent(self.settings)
        for p in items:
            a = self.m_recent.addAction(os.path.basename(p.rstrip("\\/")) or p)
            a.setToolTip(p)
            a.triggered.connect(lambda _c=False, p=p: self.project_view.open_path(p))
        if not items:
            a = self.m_recent.addAction(tr("(없음)"))
            a.setEnabled(False)

    def choose_session(self, outcome):
        """세션 선택 화면(08 D-09 1). 고른 SessionContext, 보기 전용·취소면 None. session_chooser 가 있으면 그것을 쓴다."""
        if self.session_chooser is not None:
            return self.session_chooser(outcome)
        from .session_dialog import SessionDialog
        data = outcome.project.data or {}
        title = (data.get("broadcast") or {}).get("title") or data.get("slug") or None
        last = self.settings.value("session/last_labeler", "")
        dlg = SessionDialog(self, data_root=self.data_root, default_labeler=last if isinstance(last, str) and last else None)
        dlg.load_context(outcome.project.root, data, project_title=title, read_only=outcome.read_only,
                         read_only_reasons=list(outcome.project.problems))
        dlg.exec()
        ctx = dlg.context()
        dlg.deleteLater()
        if ctx is not None:
            self.settings.setValue("session/last_labeler", ctx.labeler_id)
            self.settings.sync()
        return ctx

    def show_workbench(self, outcome) -> None:
        """연 프로젝트로 세션을 고르고 작업대를 새로 만들어 보인다. 이전 작업대는 닫는다(reason 'switch_project').
        잠금(outcome.lock)은 작업대가 넘겨받아 store 에 준다."""
        from .workbench import Workbench
        had = self.workbench is not None
        self._close_workbench("switch_project" if had else "user")   # 저장 안 한 구간 변경은 그 안에서 묻는다
        try:
            self.session = self.choose_session(outcome)
        except Exception:  # noqa: BLE001 — 세션 화면이 실패해도 보기 전용으로는 연다
            LOG.exception("세션 선택 화면 실패 — 보기 전용으로 엽니다")
            self.session = None
        wb = Workbench(self, session=self.session)   # 세션이 있으면 작업대가 app.store 로 라벨 기록을 연다
        self.workbench = wb
        self.stack.addWidget(wb)
        self.stack.setCurrentWidget(wb)
        wb.statusChanged.connect(lambda t: self.statusBar().showMessage(t))
        self.m_view.clear()
        self.m_view.addAction(wb.analysis_action)
        self.m_view.menuAction().setVisible(wb.analysis_action.isVisible())
        self.a_close_project.setEnabled(True)
        self.a_export.setEnabled(self.session is not None)
        data = outcome.project.data or {}
        title = (data.get("broadcast") or {}).get("title") or data.get("slug") or ""
        base = f"{self.spec.name} {__version__}"
        self.setWindowTitle(f"{base} — {title}" if title else base)
        if self.width() < 1200:
            # 화면(작업 표시줄 뺀 영역)보다 크게 만들지 않는다 — 1366x768 화면에서 창 테두리만큼 잘리던 문제
            avail = self.screen().availableGeometry() if self.screen() is not None else None
            frame_w = max(0, self.frameGeometry().width() - self.width())
            frame_h = max(0, self.frameGeometry().height() - self.height())
            w, h = 1366, 768
            if avail is not None and avail.width() > 0:
                w, h = min(w, avail.width() - frame_w), min(h, avail.height() - frame_h)
            self.resize(w, h)
            if avail is not None and not avail.contains(self.frameGeometry()):
                self.move(avail.topLeft())
        wb.open_project(outcome)
        wb.setFocus()

    def _before_open(self, path: str) -> None:
        """열려는 폴더가 지금 작업대의 프로젝트면 작업대를 먼저 닫아 잠금(locks/open.lock)을 푼다.

        닫지 않으면 open_checked 가 그 잠금 때문에 tryLock 에 실패해 다시 연 프로젝트가 늘 읽기 전용이 된다
        (그 뒤 세션이 있으면 store 만 잠금을 새로 잡아 쓰기 가능 — 둘이 어긋난다). 다른 프로젝트면 그대로 둔다
        (열기가 실패해도 지금 작업대를 잃지 않게). 비교는 project_view 의 경로 정규화와 같다.
        """
        from .project_view import _norm
        wb = self.workbench
        proj = getattr(wb, "project", None) if wb is not None else None
        if proj is None:
            return
        target = os.path.abspath(path)
        if os.path.basename(target).lower() == "project.json":
            target = os.path.dirname(target)
        if _norm(target) == _norm(proj.root):
            self._close_workbench("switch_project")
            self.show_home()

    def _close_workbench(self, reason: str = "user") -> None:
        """작업대를 닫는다. 저장하지 않은 구간 변경이 있으면 저장·버리기를 묻고(취소 없음), store 는 reason 으로 닫는다."""
        wb = self.workbench
        if wb is None:
            return
        try:
            wb.resolve_unsaved(allow_cancel=False)
        except Exception:  # noqa: BLE001 — 닫는 길은 막지 않는다
            LOG.exception("저장하지 않은 변경 처리 실패")
        self.workbench = None
        wb.close_workbench(reason)
        self.stack.removeWidget(wb)
        wb.deleteLater()
        self.m_view.clear()
        self.m_view.menuAction().setVisible(False)
        self.a_export.setEnabled(False)

    def show_home(self) -> None:
        self._close_workbench("user")
        self.session = None
        self.stack.setCurrentWidget(self.home_page)
        self.a_close_project.setEnabled(False)
        self.setWindowTitle(f"{self.spec.name} {__version__}")
        self.project_view.refresh()
        self.statusBar().showMessage(tr("준비"))

    def closeEvent(self, ev):  # noqa: N802
        self._close_workbench("app_exit")
        super().closeEvent(ev)

    # ---------------------------------------------------------------- 확인
    def check_updates(self, force: bool) -> None:
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self.status_chip.setText(tr("업데이트 확인 중…"))
        self._check_worker = UpdateCheckWorker(self.spec, self.installed, self.state_path, force, self.base_url, self)
        self._check_worker.result.connect(self._on_check)
        self._check_worker.start()

    def _on_check(self, res: updater.CheckResult | None) -> None:
        self._result = res
        now = datetime.now().strftime("%H:%M")
        if res is None:
            self.status_chip.setText(tr("업데이트: 아직 확인할 때가 아님 ({0})").format(now))
            return
        if res.state == "failed":
            if res.kind == "signature":
                self.status_chip.setText(tr("서명 검증 실패 — 개발자에게 알려 주세요"))
                self.status_chip.setToolTip(res.reason)
            elif res.kind == "manual":
                self.status_chip.setText(tr("수동 업데이트 필요"))
                self.status_chip.setToolTip(res.reason + "\n" + MANUAL_GUIDE)
            else:
                self.status_chip.setText(tr("업데이트 확인 실패 — {0}").format(res.reason[:80]))
                self.status_chip.setToolTip(res.reason)
            self.update_btn.setVisible(False)
            return
        if res.state == "latest":
            self.status_chip.setText(tr("최신 (마지막 확인 {0})").format(now))
            self.status_chip.setToolTip(res.reason)
            self.update_btn.setVisible(False)
            return
        rel = res.release
        assert rel is not None
        self.status_chip.setText(tr("새 버전 있음 ({0})").format(now))
        self.update_btn.setText(tr("새 버전 v{0}").format(rel.version))
        self.update_btn.setVisible(not res.skipped or rel.manifest.mandatory)
        if res.needs_update:
            self.banner.setText(tr("이 버전은 지원이 끝났습니다 (최소 {0}). 업데이트가 필요합니다.").format(rel.manifest.min_supported))
            self.banner.setVisible(True)
        if self.auto:
            QTimer.singleShot(0, lambda: self.start_stage(rel))
        elif rel.manifest.mandatory and not res.skipped:
            QTimer.singleShot(0, self.offer_update)

    # ---------------------------------------------------------------- 대화상자 · 준비 · 적용
    def offer_update(self) -> None:
        if not self._result or not self._result.release:
            return
        rel = self._result.release
        dlg = UpdateDialog(rel, self._result.needs_update, self.spec, self)
        rc = dlg.exec()
        if dlg.skip:
            st = updater.UpdateState.load(self.state_path)
            st.skip_version = rel.version
            st.save(self.state_path)
            self.update_btn.setVisible(False)
            return
        if rc == UpdateDialog.Accepted:
            self.start_stage(rel)

    def start_stage(self, rel: updater.Release) -> None:
        if self._stage_worker is not None and self._stage_worker.isRunning():
            return
        prog = QProgressDialog(tr("업데이트 준비 중…"), tr("취소"), 0, 1000, self)
        prog.setWindowTitle(tr("업데이트 v{0}").format(rel.version))
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        worker = UpdateStageWorker(rel, self.spec, self.installed, self.base_url, self)
        self._stage_worker = worker
        worker.progress.connect(lambda f, t: (prog.setValue(int(f * 1000)), prog.setLabelText(t)))
        prog.canceled.connect(worker.cancel)

        def done(staged, err: str):
            prog.close()
            if err or staged is None:
                if self.auto:
                    LOG.error("자동 모드: 준비 실패 — %s", err)
                elif not err.startswith("[download] 취소"):
                    QMessageBox.warning(self, tr("업데이트 준비 실패"), err or tr("알 수 없는 오류"))
                return
            self.apply_update(staged)

        worker.done.connect(done)
        worker.start()

    def apply_update(self, staged: updater.StagedUpdate) -> None:
        applier = updater.RobocopyApplier(self.spec, self.installed, self.data_root)
        problems, warnings = applier.preflight(staged)
        if problems:
            if self.auto:
                LOG.error("자동 모드: 교체 전 조건 미충족 — %s", problems)
            else:
                QMessageBox.warning(self, tr("자동 업데이트를 진행할 수 없습니다"), "\n".join("• " + p for p in problems) + "\n\n" + tr(MANUAL_GUIDE))
            return
        if warnings:
            if self.auto:
                LOG.info("자동 모드: 경고를 기록하고 계속 — %s", warnings)
            elif QMessageBox.question(self, tr("계속할까요?"), "\n".join("• " + w for w in warnings) + "\n\n" + tr("그래도 계속합니까?"),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                LOG.info("사용자가 경고 뒤 업데이트를 취소함: %s", warnings)
                return
            LOG.info("사용자가 경고를 보고 계속 진행: %s", warnings)
        try:
            applier.apply(staged)
        except updater.UpdateError as ex:
            if self.auto:
                LOG.error("자동 모드: 적용 실패 — %s", ex.message)
            else:
                QMessageBox.warning(self, tr("자동 업데이트를 진행할 수 없습니다"), ex.message + "\n\n" + tr(MANUAL_GUIDE))
            return
        LOG.info("교체를 위해 종료 (v%s → v%s)", self.installed.version, staged.release.version)
        self.statusBar().showMessage(tr("업데이트를 적용하려고 종료합니다…"))
        QTimer.singleShot(100, QApplication.instance().quit)

    # ---------------------------------------------------------------- 시작 알림
    def notify_startup_flag(self, flag: str) -> None:
        last = updater.read_last_result(self.data_root) or {}
        if flag == "--updated":
            QMessageBox.information(self, tr("업데이트 완료"),
                                    tr("v{0} 으로 업데이트했습니다. (이전 {1})\n이전 버전 사본(_backup)은 다음 정상 시작 뒤에 지워집니다.")
                                    .format(__version__, last.get("from", "?")))
        elif flag == "--update-failed":
            log = os.path.join(self.data_root, "logs", "update.log")
            QMessageBox.warning(self, tr("업데이트 실패"),
                                tr("교체가 실패해 이전 버전으로 돌아왔습니다.\n사유: {0} (backup_rc={1}, copy_rc={2}, restore_rc={3})\n로그: {4}\n\n{5}")
                                .format(last.get("result", "?"), last.get("backup_rc"), last.get("copy_rc"), last.get("restore_rc"), log, MANUAL_GUIDE))
        elif flag == "--update-blocked":
            QMessageBox.information(self, tr("업데이트 보류"),
                                    tr("다른 ChatCoach 창이 열려 있어 업데이트를 보류했습니다. 창을 모두 닫고 다시 시도하세요."))

    # ---------------------------------------------------------------- 도움말
    def copy_report(self) -> None:
        from ..report import build_report
        text = build_report(self.log_file, {"업데이트": self.status_chip.text()})
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage(tr("문제 보고를 클립보드에 복사했습니다"), 4000)

    def about(self) -> None:
        keys = ", ".join(f"{k.label} {k.key_id}" for k in self.spec.update_pubkeys) or tr("(없음)")
        QMessageBox.about(self, tr("정보"),
                          tr("{0} {1}\n채널 {2} · 빌드 {3}\n업데이트 공개키: {4}\n저장소: https://github.com/{5}")
                          .format(self.spec.name, __version__, self.installed.channel,
                                  self.installed.built_at.isoformat() if self.installed.built_at else "-", keys, self.spec.repo))
