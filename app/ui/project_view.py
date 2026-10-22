"""프로젝트 화면: '폴더에서 열기' · 최근 프로젝트 목록 (docs/design/02-architecture.md 7-1·7-2).

- 열기는 app.project.open 으로 검증한다. 검증 실패·잠금 실패면 읽기 전용으로 열고, 막지 않는 경고(OneDrive·UNC·
  긴 경로·slug 불일치·솔트 없음)와 함께 OpenOutcome 으로 넘긴다. project.json 자체를 못 읽으면 열지 않는다.
- 최근 목록은 settings.ini 의 [projects] recent (경로 문자열 목록, 최대 MAX_RECENT 개, 최근 것이 앞).
  목록에는 경로만 둔다(제목·채팅은 넣지 않는다 — 설정 파일이 개인정보를 갖지 않게).
- 잠금: locks/open.lock 을 QLockFile 로 잡는다(03 12-1 '잡기', 13-5). 못 잡으면 읽기 전용.
  QLockFile 은 QtCore 라 lock_factory 로 주입한다(입출력 층 규칙과 같은 모양). 잠금 객체는 OpenOutcome.lock 에
  담아 넘기고, 화면을 닫을 때 release_outcome() 으로 푼다. 나중에 store(ProjectStore.open(lock_factory=…))가
  잠금을 맡게 되면 이 화면은 잠금을 store 에 넘기면 된다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QLockFile, QSettings, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from .. import project as project_mod
from ..i18n import tr

LOG = logging.getLogger("chatcoach")

RECENT_GROUP = "projects"
RECENT_KEY = "recent"
MAX_RECENT = 10
LOCK_STALE_MS = 0          # 0 = 오래된 잠금 자동 해제 안 함. 죽은 프로세스의 잠금은 QLockFile 이 pid 로 판정해 푼다


# --------------------------------------------------------------------------- 최근 목록
def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def load_recent(settings: QSettings) -> list[str]:
    """settings.ini 의 최근 프로젝트 경로 목록 (최근 것이 앞). 형식이 이상하면 빈 목록."""
    v = settings.value(f"{RECENT_GROUP}/{RECENT_KEY}", [])
    if isinstance(v, str):
        v = [v] if v else []
    if not isinstance(v, (list, tuple)):
        return []
    out, seen = [], set()
    for p in v:
        if isinstance(p, str) and p and _norm(p) not in seen:
            seen.add(_norm(p))
            out.append(p)
    return out[:MAX_RECENT]


def _save_recent(settings: QSettings, items: list[str]) -> None:
    settings.setValue(f"{RECENT_GROUP}/{RECENT_KEY}", list(items[:MAX_RECENT]))
    settings.sync()


def push_recent(settings: QSettings, path: str) -> list[str]:
    """path 를 맨 앞에 넣고(중복 제거) 저장한 목록을 돌려준다."""
    a = os.path.abspath(path)
    items = [a] + [p for p in load_recent(settings) if _norm(p) != _norm(a)]
    _save_recent(settings, items)
    return items[:MAX_RECENT]


def remove_recent(settings: QSettings, path: str) -> list[str]:
    items = [p for p in load_recent(settings) if _norm(p) != _norm(path)]
    _save_recent(settings, items)
    return items


# --------------------------------------------------------------------------- 파일 대화상자의 시작 폴더
# settings.ini [dialogs] 에 마지막으로 쓴 폴더를 남긴다(경로 문자열만). 대화상자는 그 폴더에서 연다.
DIR_PROJECT = "dialogs/project_dir"     # 프로젝트 폴더 고르기: 고른 프로젝트의 상위 폴더(프로젝트들이 있는 곳)
DIR_MEDIA = "dialogs/media_dir"         # 영상 파일 연결
DIR_EXPORT = "dialogs/export_dir"       # 라벨 내보내기 저장
DIR_BACKUP = "dialogs/backup_dir"       # 백업 저장 폴더 · 되살릴 백업 zip


def last_dir(settings, key: str, fallback: str | None = None) -> str:
    """settings 에 남은 폴더(지금도 있는 것만). 없으면 fallback, 그것도 없으면 홈 폴더."""
    v = settings.value(key, "") if settings is not None else ""
    if isinstance(v, str) and v and os.path.isdir(v):
        return v
    if fallback and os.path.isdir(fallback):
        return fallback
    return os.path.expanduser("~")


def remember_dir(settings, key: str, folder: str | None) -> None:
    """고른 폴더를 남긴다. 폴더가 아니면(빈 값·없는 경로) 남기지 않는다."""
    if settings is None or not folder or not os.path.isdir(folder):
        return
    settings.setValue(key, os.path.normpath(folder))
    settings.sync()


def default_projects_dir(data_root: str | None = None) -> str:
    """기억한 폴더가 없을 때 프로젝트 폴더 고르기의 시작 위치.

    ① <앱 폴더의 상위>\\chatcoach-data\\projects (소스 실행이면 저장소 옆, 시범 프로젝트 자리)
    ② <데이터 폴더>\\projects (paths.projects_root 와 같은 자리)  ③ 홈 폴더. 있는 폴더만 고른다(만들거나 열지 않는다)."""
    from .. import paths
    cands = [os.path.join(os.path.dirname(paths.install_root()), "chatcoach-data", "projects")]
    if data_root:
        cands.append(os.path.join(data_root, "projects"))
    for c in cands:
        if os.path.isdir(c):
            return c
    return os.path.expanduser("~")


# --------------------------------------------------------------------------- 열기
@dataclass
class OpenOutcome:
    """열기 결과. project.data 가 None 이면 열지 못한 것(ok=False)."""
    project: project_mod.Project
    warnings: list[str] = field(default_factory=list)   # 막지 않는 경고 (화면 띠에 보인다)
    lock: object | None = None                           # 잡은 QLockFile (없으면 None)

    @property
    def ok(self) -> bool:
        return self.project.data is not None

    @property
    def read_only(self) -> bool:
        return self.project.read_only

    def messages(self) -> list[str]:
        """사람이 읽을 줄 목록: 읽기 전용 사유 → 경고 순."""
        out = []
        if self.project.read_only:
            out += [tr("읽기 전용: {0}").format(p) for p in self.project.problems]
        out += self.warnings
        return out


def default_lock_factory(path: str):
    lk = QLockFile(path)
    lk.setStaleLockTime(LOCK_STALE_MS)
    return lk


def open_checked(path: str, lock_factory: Callable[[str], object] | None = default_lock_factory) -> OpenOutcome:
    """project.open 으로 검증하고, 쓸 수 있는 상태면 잠금을 잡는다. 예외로 막지 않는다."""
    proj = project_mod.open(path)
    out = OpenOutcome(project=proj, warnings=list(proj.warnings))
    if not out.ok:
        return out
    if not proj.read_only and lock_factory is not None:
        lock_path = proj.paths.open_lock
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            lk = lock_factory(lock_path)
            if lk.tryLock(0):
                out.lock = lk
            else:
                proj.read_only = True
                # 03 13-5: 누가 쥐었는지(같은 PC 의 다른 창 / 다른 PC)에 따라 푸는 길을 안내한다. 잠금을 자동으로 지우지 않는다.
                from ..store import lock_failure_help, lock_holder_info
                proj.problems.append(tr(lock_failure_help(lock_holder_info(lk))))
        except OSError as e:
            proj.read_only = True
            proj.problems.append(tr("잠금 파일을 만들지 못했습니다: {0}").format(type(e).__name__))
    return out


def release_outcome(outcome: OpenOutcome | None) -> None:
    if outcome is not None and outcome.lock is not None:
        try:
            outcome.lock.unlock()
        except Exception:  # noqa: BLE001 — 닫는 길에서 막지 않는다
            LOG.exception("프로젝트 잠금 해제 실패")
        outcome.lock = None


# --------------------------------------------------------------------------- 화면
class ProjectView(QWidget):
    """최근 프로젝트 목록과 [폴더에서 열기]. 연 결과는 projectOpened(OpenOutcome) 로 넘긴다."""

    projectOpened = Signal(object)

    def __init__(self, settings: QSettings, parent=None, *,
                 lock_factory: Callable[[str], object] | None = default_lock_factory):
        super().__init__(parent)
        self.settings = settings
        self.lock_factory = lock_factory
        self.before_open: Callable[[str], None] | None = None   # open_checked 직전에 부른다(창이 같은 프로젝트를 닫게)
        self.data_root: str | None = None     # 메인 창이 준다: 기억한 폴더가 없을 때 시작 위치 후보(<data>\projects)
        lay = QVBoxLayout(self)
        title = QLabel(tr("최근 프로젝트"))
        title.setObjectName("panelTitle")
        lay.addWidget(title)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda it: self.open_path(it.data(0x0100)))
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.btn_open = QPushButton(tr("폴더에서 열기…"))
        self.btn_open.clicked.connect(self.choose_and_open)
        self.btn_open_sel = QPushButton(tr("선택한 프로젝트 열기"))
        self.btn_open_sel.clicked.connect(self._open_selected)
        self.btn_forget = QPushButton(tr("목록에서 빼기"))
        self.btn_forget.clicked.connect(self._forget_selected)
        for b in (self.btn_open, self.btn_open_sel, self.btn_forget):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.msg = QLabel("")
        self.msg.setObjectName("hint")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        self.refresh()

    # ------------------------------------------------------------ 목록
    def refresh(self) -> None:
        self.list.clear()
        for p in load_recent(self.settings):
            exists = os.path.isfile(os.path.join(p, project_mod.PROJECT_FILE))
            text = os.path.basename(p.rstrip("\\/")) or p
            it = QListWidgetItem(text if exists else tr("{0}  (찾을 수 없음)").format(text))
            it.setToolTip(p)
            it.setData(0x0100, p)       # Qt.UserRole
            self.list.addItem(it)
        has = self.list.count() > 0
        self.btn_open_sel.setEnabled(has)
        self.btn_forget.setEnabled(has)
        if has:
            self.list.setCurrentRow(0)

    def recent_paths(self) -> list[str]:
        return load_recent(self.settings)

    def _selected_path(self) -> str | None:
        it = self.list.currentItem()
        return it.data(0x0100) if it is not None else None

    def _open_selected(self) -> None:
        p = self._selected_path()
        if p:
            self.open_path(p)

    def _forget_selected(self) -> None:
        p = self._selected_path()
        if p:
            remove_recent(self.settings, p)
            self.refresh()

    # ------------------------------------------------------------ 열기
    def start_folder(self) -> str:
        """프로젝트 폴더 고르기의 시작 위치: 마지막으로 연 프로젝트들의 상위 폴더 → 기본 위치(default_projects_dir)."""
        return last_dir(self.settings, DIR_PROJECT, default_projects_dir(self.data_root))

    def choose_folder(self) -> str | None:
        """폴더 고르기 대화상자. 시험에서는 이 메서드를 바꿔 끼운다."""
        d = QFileDialog.getExistingDirectory(self, tr("프로젝트 폴더 고르기"), self.start_folder())
        return d or None

    def choose_and_open(self) -> OpenOutcome | None:
        d = self.choose_folder()
        if not d:
            return None
        outcome = self.open_path(d)
        if outcome is not None and outcome.ok:
            # 다음에는 이 프로젝트가 든 폴더에서 연다(프로젝트 폴더 자체가 아니라 그 상위 — 다른 프로젝트도 바로 보이게)
            remember_dir(self.settings, DIR_PROJECT, os.path.dirname(os.path.abspath(outcome.project.root)))
        return outcome

    def open_path(self, path: str | None) -> OpenOutcome | None:
        if not path:
            return None
        if self.before_open is not None:
            # 같은 프로젝트를 이미 연 작업대가 잠금을 쥐고 있으면 open_checked 가 늘 읽기 전용이 된다 → 먼저 닫게 한다
            self.before_open(path)
        outcome = open_checked(path, self.lock_factory)
        if not outcome.ok:
            self.msg.setText(tr("열지 못했습니다: {0}").format("; ".join(outcome.project.problems[:3])))
            LOG.warning("프로젝트 열기 실패 (%d개 사유)", len(outcome.project.problems))
            return outcome
        push_recent(self.settings, outcome.project.root)
        self.refresh()
        lines = outcome.messages()
        self.msg.setText("\n".join("• " + m for m in lines))
        LOG.info("프로젝트 열기: 읽기 전용=%s, 경고 %d개", outcome.read_only, len(outcome.warnings))
        self.projectOpened.emit(outcome)
        return outcome
