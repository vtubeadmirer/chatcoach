"""QThread 워커. M0 에는 업데이트 확인·준비 두 개만 있다 (02 8장). 라벨 파일에는 쓰지 않는다."""
from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from . import updater
from .appspec import AppSpec

LOG = logging.getLogger("streamlabeler")


class UpdateCheckWorker(QThread):
    """updater.run_check 를 백그라운드에서. result: CheckResult | None (None = 확인할 때가 아님)."""
    result = Signal(object)

    def __init__(self, spec: AppSpec, installed: updater.InstalledInfo, state_path: str, force: bool,
                 base_url: str | None = None, parent=None):
        super().__init__(parent)
        self.spec, self.installed, self.state_path, self.force, self.base_url = spec, installed, state_path, force, base_url

    def run(self):
        try:
            res = updater.run_check(self.spec, self.installed, self.state_path, force=self.force, base_url=self.base_url)
        except Exception as ex:  # 예상 못 한 예외도 '확인 실패' 로 보인다. 조용히 삼키지 않는다
            LOG.exception("업데이트 확인 중 예외")
            res = updater.CheckResult("failed", f"확인 중 오류: {ex}", "network")
        self.result.emit(res)


class UpdateStageWorker(QThread):
    """updater.stage 를 백그라운드에서. progress(0~1, 문구), done(StagedUpdate | None, 오류 문구)."""
    progress = Signal(float, str)
    done = Signal(object, str)

    def __init__(self, release: updater.Release, spec: AppSpec, installed: updater.InstalledInfo,
                 base_url: str | None = None, parent=None):
        super().__init__(parent)
        self.release, self.spec, self.installed, self.base_url = release, spec, installed, base_url
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            staged = updater.stage(self.release, self.spec, self.installed, self.base_url,
                                   progress=self.progress.emit, cancel=lambda: self._cancel)
            self.done.emit(staged, "")
        except updater.UpdateError as ex:
            LOG.warning("업데이트 준비 실패 (%s): %s", ex.kind, ex.message)
            self.done.emit(None, f"[{ex.kind}] {ex.message}")
        except Exception as ex:
            LOG.exception("업데이트 준비 중 예외")
            self.done.emit(None, f"준비 중 오류: {ex}")
