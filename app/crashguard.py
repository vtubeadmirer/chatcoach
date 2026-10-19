"""네이티브 충돌 기록과 비정상 종료 표식 (S-31, docs/design/04-update-and-release.md 6-9).

- `faulthandler` 를 data\\logs\\crash.log 에 켠다. libmpv·Qt 의 네이티브 충돌은 Python 예외 훅에 잡히지 않는다.
- 실행 중 표식 data\\run\\<pid>.running 을 만들고 정상 종료 때 지운다. 남아 있으면 이전 실행이 비정상 종료한 것이다.
- 업데이트 직후의 '첫 정상 종료' 판정에 이 표식을 쓴다 (_backup 삭제 시점).
"""
from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import sys

LOG = logging.getLogger("streamlabeler")
_crash_file = None
_marker: str | None = None


def enable(data_root: str) -> str | None:
    """crash.log 에 faulthandler 를 켜고 실행 중 표식을 만든다. 표식 경로를 돌려준다 (실패하면 None)."""
    global _crash_file, _marker
    try:
        log_dir = os.path.join(data_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        _crash_file = open(os.path.join(log_dir, "crash.log"), "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError:
        _crash_file = None
    try:
        run_dir = os.path.join(data_root, "run")
        os.makedirs(run_dir, exist_ok=True)
        _marker = os.path.join(run_dir, f"{os.getpid()}.running")
        with open(_marker, "w", encoding="utf-8") as f:
            f.write(f"{sys.argv!r}\n")
        atexit.register(mark_clean_exit)
    except OSError:
        _marker = None
    return _marker


def mark_clean_exit() -> None:
    """정상 종료: 표식을 지운다. 여러 번 불러도 된다."""
    global _marker
    if _marker:
        try:
            os.remove(_marker)
        except OSError:
            pass
        _marker = None


def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    SYNCHRONIZE = 0x00100000
    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return False
    ctypes.windll.kernel32.CloseHandle(h)
    return True


def stale_markers(data_root: str) -> list[int]:
    """살아 있지 않은 프로세스의 표식 목록(pid). 비정상 종료의 증거다. 자기 자신은 뺀다."""
    run_dir = os.path.join(data_root, "run")
    out: list[int] = []
    try:
        names = os.listdir(run_dir)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".running"):
            continue
        stem = name[:-len(".running")]
        if not stem.isdigit() or int(stem) == os.getpid():
            continue
        pid = int(stem)
        if not _pid_alive(pid):
            out.append(pid)
    return out


def clear_stale_markers(data_root: str) -> int:
    n = 0
    for pid in stale_markers(data_root):
        try:
            os.remove(os.path.join(data_root, "run", f"{pid}.running"))
            n += 1
        except OSError:
            pass
    return n
