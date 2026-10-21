"""네이티브 충돌 기록과 비정상 종료 표식 (S-31, docs/design/04-update-and-release.md 6-9).

- `faulthandler` 를 data\\logs\\crash.log 에 켠다. libmpv·Qt 의 네이티브 충돌은 Python 예외 훅에 잡히지 않는다.
  crash.log 는 켤 때 CRASH_LOG_MAX_BYTES 를 넘었으면 crash.log.1 로 한 번 돌린다(둘만 남는다).
  faulthandler 가 쓰는 것은 파일·함수 이름과 줄 번호뿐이라 채팅 본문·닉네임은 들어가지 않는다.
- 실행 중 표식 data\\run\\<pid>.running 을 만들고 정상 종료 때 지운다. 남아 있으면 이전 실행이 비정상 종료한 것이다.
  표식에는 pid·시작 시각·'--' 로 시작하는 플래그만 쓴다(경로가 든 인자는 쓰지 않는다, S-20).
- 업데이트 직후의 '첫 정상 종료' 판정에 이 표식을 쓴다 (_backup 삭제 시점).
- 비정상 종료가 같은 버전에서 몇 번 이어졌는지 data\\run\\streak.json 에 센다(note_start).
  ROLLBACK_SUGGEST_STREAK 번 이상이고 업데이트 직후면 되돌리기를 제안한다 (S-31 ④, 04 6-9).
"""
from __future__ import annotations

import atexit
import faulthandler
import json
import logging
import os
import sys
import time

LOG = logging.getLogger("chatcoach")
CRASH_LOG_NAME = "crash.log"
CRASH_LOG_MAX_BYTES = 256 * 1024      # 넘으면 켤 때 crash.log.1 로 돌린다
STREAK_FILE = "streak.json"
ROLLBACK_SUGGEST_STREAK = 2           # 업데이트 직후 비정상 종료가 이만큼 이어지면 되돌리기 제안
_crash_file = None
_marker: str | None = None


def crash_log_path(data_root: str) -> str:
    return os.path.join(data_root, "logs", CRASH_LOG_NAME)


def _rotate_crash_log(path: str, max_bytes: int) -> bool:
    """path 가 max_bytes 를 넘으면 path.1 로 바꾼다(예전 .1 은 덮어쓴다). 돌렸으면 True."""
    try:
        if os.path.getsize(path) <= max_bytes:
            return False
    except OSError:
        return False
    try:
        os.replace(path, path + ".1")
        return True
    except OSError:
        return False


def enable(data_root: str, *, version: str | None = None, max_bytes: int = CRASH_LOG_MAX_BYTES) -> str | None:
    """crash.log 에 faulthandler 를 켜고 실행 중 표식을 만든다. 표식 경로를 돌려준다 (실패하면 None)."""
    global _crash_file, _marker
    try:
        log_dir = os.path.join(data_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = crash_log_path(data_root)
        _rotate_crash_log(path, max_bytes)
        if _crash_file is not None:
            try:
                faulthandler.disable()
                _crash_file.close()
            except (OSError, ValueError):
                pass
        _crash_file = open(path, "a", encoding="utf-8")
        # 시작 줄: 충돌 기록이 어느 실행의 것인지 알 수 있게. 경로·인자는 쓰지 않는다.
        _crash_file.write(f"=== 시작 {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()}"
                          f"{' v' + version if version else ''} ===\n")
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError:
        _crash_file = None
    try:
        run_dir = os.path.join(data_root, "run")
        os.makedirs(run_dir, exist_ok=True)
        _marker = os.path.join(run_dir, f"{os.getpid()}.running")
        flags = [a for a in sys.argv[1:] if a.startswith("--") and "=" not in a]
        with open(_marker, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "version": version, "flags": flags}, ensure_ascii=False) + "\n")
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
    from ctypes import wintypes
    # 끝난 프로세스도 누가 핸들을 쥐고 있으면 OpenProcess 가 성공한다 → 종료 코드가 STILL_ACTIVE 인지까지 본다.
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True                      # 모르면 살아 있다고 본다(남의 표식을 지우지 않게)
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


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


def note_start(data_root: str, version: str, had_abnormal_exit: bool) -> int:
    """이번 시작을 기록하고 '같은 버전에서 이어진 비정상 종료 횟수' 를 돌려준다.

    had_abnormal_exit = stale_markers() 가 비어 있지 않았는가. 정상이었으면 0 으로 되돌린다.
    버전이 바뀌면 1 부터 다시 센다(업데이트 직후 첫 비정상 종료 = 1). 파일을 못 쓰면 이번 값만 돌려준다.
    """
    path = os.path.join(data_root, "run", STREAK_FILE)
    prev_ver, prev_n = None, 0
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            prev_ver = obj.get("version")
            n = obj.get("abnormal")
            prev_n = n if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < 1000 else 0
    except (OSError, ValueError):
        pass
    if not had_abnormal_exit:
        n = 0
    elif prev_ver == version:
        n = prev_n + 1
    else:
        n = 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": version, "abnormal": n}, f)
        os.replace(tmp, path)
    except OSError:
        pass
    return n


def should_suggest_rollback(streak: int, just_updated: bool) -> bool:
    """업데이트 직후(--updated 로 켜진 버전) 비정상 종료가 ROLLBACK_SUGGEST_STREAK 번 이어졌는가."""
    return bool(just_updated) and streak >= ROLLBACK_SUGGEST_STREAK


def crash_tail(data_root: str, max_bytes: int = 8192) -> str:
    """crash.log 끝부분(가림 처리). 문제 보고에 붙인다. 없으면 빈 문자열."""
    path = crash_log_path(data_root)
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    if size > max_bytes and "\n" in text:
        text = text.split("\n", 1)[1]       # 잘린 첫 줄은 버린다
    from .logging_setup import redact
    return redact(text)
