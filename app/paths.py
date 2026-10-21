# vendored from vtubeadmirer/clipslicer@07d13cc (app/paths.py) — 변경: 02 5-5 표대로 새로 씀(설치·코드·데이터 루트 판정, 시험 파일 판정, 번들 한정 도구 탐색, PATH 격리 import mpv, OneDrive·UNC 판정)
"""경로 판정의 유일한 곳 (docs/design/02-architecture.md 5-5).

다른 모듈은 경로를 조립하지 않고 이 모듈의 함수를 쓴다.
PySide6 는 make_settings() 안에서만 import 한다 (tools·테스트·자체 점검이 Qt 없이 읽는다).

ClipSlicer 와 다른 점:
- 설정 파일 판정에 open(path, "a") 를 쓰지 않는다 (빈 settings.ini 를 만드는 부작용). 시험 파일을 쓰고 지운다.
- 번들(frozen) 실행에서는 ffmpeg·libmpv 를 번들 폴더에서만 찾고 PATH 를 보지 않는다 (S-08).
- import mpv 동안만 PATH 를 libmpv 폴더 하나로 바꿨다가 되돌린다 (S-08).

경로 점검(path_issues · startup_path_issues · project_path_issues)도 여기 있다 (S-09 · S-49):
OneDrive·UNC·긴 경로·쓰기 불가·한글 폴더. 메시지에는 경로를 넣지 않는다 (S-20).
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys

from .appspec import SPEC

LIBMPV_DLLS = ("libmpv-2.dll", "mpv-2.dll")
ONEDRIVE_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")

# 모듈 전역 캐시 (reset_for_tests() 로 비운다)
_override: str | None = None      # set_data_root() 로 지정된 폴더 (--data-dir)
_data_root: str | None = None
_data_mode: str | None = None     # "override" | "portable" | "fallback"
_backup_root: str | None = None
_dll_dirs: dict[str, object] = {}  # os.add_dll_directory 핸들 (같은 폴더를 두 번 넣지 않게)


def _frozen() -> bool:
    """PyInstaller 로 묶인 exe 로 실행 중인가."""
    return bool(getattr(sys, "frozen", False))


def _local_appdata() -> str:
    """%LOCALAPPDATA%. 환경 변수가 없으면 ~\\AppData\\Local."""
    return os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")


def install_root() -> str:
    """<앱> 폴더. frozen 이면 exe 가 있는 폴더, 소스 실행이면 저장소 루트."""
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def code_dir() -> str:
    """코드·번들 파일 폴더. frozen 이면 sys._MEIPASS(= <앱>\\_internal), 소스 실행이면 저장소 루트."""
    if _frozen():
        return getattr(sys, "_MEIPASS", None) or os.path.join(install_root(), "_internal")
    return install_root()


def _writable(d: str) -> bool:
    """d 를 만들고 시험 파일(.write_test_<pid>)을 써 본 뒤 지운다. 어느 단계든 실패하면 False.

    settings.ini 같은 실제 파일은 만들지 않는다. 쓰기는 되지만 지우기가 안 되는 폴더도 False 로 본다
    (임시 파일 + os.replace 저장이 실패하기 때문).
    """
    probe = os.path.join(d, f".write_test_{os.getpid()}")
    try:
        os.makedirs(d, exist_ok=True)
        with open(probe, "wb") as f:
            f.write(b"ok")
    except (OSError, ValueError):  # ValueError: 경로에 NUL 같은 문자가 섞인 경우
        return False
    try:
        os.remove(probe)
    except OSError:
        return False
    return True


def set_data_root(path: str | None) -> None:
    """데이터 폴더를 직접 지정한다 (--data-dir). None 이면 지정을 푼다. 캐시를 비운다."""
    global _override, _data_root, _data_mode, _backup_root
    _override = os.path.abspath(path) if path else None
    _data_root = _data_mode = _backup_root = None


def data_root() -> str:
    """사용자 데이터 폴더 data\\. 처음 부를 때 정하고 캐시한다. 폴더를 만든다.

    우선순위: ① set_data_root() 로 지정된 폴더 ② <앱>\\data (시험 파일로 쓰기 확인)
    ③ %LOCALAPPDATA%\\ChatCoach\\data (폴백). ① 에 쓸 수 없으면 OSError — 조용히 다른 곳에 쓰지 않는다.
    """
    global _data_root, _data_mode
    if _data_root is not None:
        return _data_root
    if _override:
        if not _writable(_override):
            raise OSError(f"지정한 데이터 폴더에 쓸 수 없습니다: {_override}")
        d, mode = _override, "override"
    else:
        d = os.path.join(install_root(), "data")
        mode = "portable"
        if not _writable(d):
            d = os.path.join(_local_appdata(), SPEC.name, "data")
            mode = "fallback"
            os.makedirs(d, exist_ok=True)
    _data_root, _data_mode = d, mode
    return d


def data_mode() -> str:
    """데이터 폴더를 정한 방식: "override"(--data-dir) | "portable"(<앱>\\data) | "fallback"(%LOCALAPPDATA%)."""
    data_root()
    return _data_mode or "portable"


def reset_for_tests() -> None:
    """모듈 캐시(지정 폴더·데이터·백업 폴더)를 비운다. 테스트 전용."""
    global _override, _data_root, _data_mode, _backup_root
    _override = _data_root = _data_mode = _backup_root = None


def backup_root() -> str:
    """백업 폴더. %LOCALAPPDATA%\\ChatCoach\\backups, 쓸 수 없으면 data_root()\\backups (02 5-3)."""
    global _backup_root
    if _backup_root is not None:
        return _backup_root
    d = os.path.join(_local_appdata(), SPEC.name, "backups")
    if not _writable(d):
        d = os.path.join(data_root(), "backups")
        os.makedirs(d, exist_ok=True)
    _backup_root = d
    return d


def _tool_dirs() -> list[str]:
    """ffmpeg·ffprobe 를 찾는 폴더 (순서대로)."""
    if _frozen():
        c = code_dir()
        return [os.path.join(c, "ffmpeg"), c]
    r = install_root()
    return [os.path.join(r, "bin", "ffmpeg"), os.path.join(r, "bin")]


def find_tool(name: str) -> str | None:
    """ffmpeg / ffprobe 실행 파일 경로. 없으면 None.

    frozen 이면 code_dir()\\ffmpeg 와 code_dir() 만 본다 — PATH 를 보지 않는다 (S-08: 다른 계정이 심은 exe).
    소스 실행이면 <루트>\\bin\\ffmpeg, <루트>\\bin, 그다음 PATH.
    """
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    for d in _tool_dirs():
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    if _frozen():
        return None
    return shutil.which(name)


def libmpv_dir() -> str:
    """libmpv-2.dll 을 찾는 폴더 하나. frozen 이면 code_dir(), 소스 실행이면 <루트>\\bin."""
    if _frozen():
        return code_dir()
    return os.path.join(install_root(), "bin")


def _libmpv_file() -> str | None:
    """libmpv_dir() 안의 libmpv DLL 경로. 없으면 None. (DLL 검색 경로는 건드리지 않는다)"""
    d = libmpv_dir()
    for dll in LIBMPV_DLLS:
        p = os.path.join(d, dll)
        if os.path.isfile(p):
            return p
    return None


def prepare_libmpv() -> str | None:
    """libmpv DLL 이 있는 폴더를 os.add_dll_directory 에 넣고 그 폴더를 돌려준다. DLL 이 없으면 None.

    libmpv 가 같은 폴더의 다른 DLL 을 찾을 수 있게 한다. PATH 는 바꾸지 않는다 (그건 import_mpv() 가 잠깐만 한다).
    """
    if _libmpv_file() is None:
        return None
    d = libmpv_dir()
    if d not in _dll_dirs and hasattr(os, "add_dll_directory"):
        _dll_dirs[d] = os.add_dll_directory(d)
    return d


def import_mpv():
    """python-mpv 모듈을 번들 libmpv 로 import 해서 돌려준다 (S-08).

    python-mpv 는 PATH 에서 DLL 을 찾으므로, import 하는 동안만 PATH 를 libmpv 폴더 하나로 바꾸고
    finally 에서 되돌린다. 다른 폴더에 있는 같은 이름의 DLL 을 집지 않게 하기 위해서다.
    DLL 이 없으면 FileNotFoundError, python-mpv 가 없으면 ImportError.
    """
    if "mpv" in sys.modules:
        return sys.modules["mpv"]
    d = prepare_libmpv()
    if d is None:
        raise FileNotFoundError(f"libmpv-2.dll 이 없습니다: {libmpv_dir()}")
    old = os.environ.get("PATH")
    os.environ["PATH"] = d
    try:
        return importlib.import_module("mpv")
    finally:
        if old is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old


def missing_tools() -> list[str]:
    """ffmpeg.exe · ffprobe.exe · libmpv-2.dll 중 없는 것의 파일 이름 목록. (DLL 검색 경로는 건드리지 않는다)"""
    missing = []
    if not find_tool("ffmpeg"):
        missing.append("ffmpeg.exe")
    if not find_tool("ffprobe"):
        missing.append("ffprobe.exe")
    if _libmpv_file() is None:
        missing.append("libmpv-2.dll")
    return missing


def settings_path() -> str:
    """설정 파일 경로 data_root()\\settings.ini. 파일을 만들지 않는다."""
    return os.path.join(data_root(), "settings.ini")


def make_settings():
    """QSettings(settings_path(), IniFormat). 레지스트리는 쓰지 않는다."""
    from PySide6.QtCore import QSettings
    return QSettings(settings_path(), QSettings.Format.IniFormat)


def is_under_onedrive(path: str) -> bool:
    """path 가 %OneDrive% · %OneDriveCommercial% · %OneDriveConsumer% 폴더(또는 그 아래)인가. 대소문자 무시 (S-09)."""
    p = os.path.normcase(os.path.abspath(path))
    for var in ONEDRIVE_VARS:
        root = os.environ.get(var)
        if not root:
            continue
        r = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
        if p == r or p.startswith(r + os.sep):
            return True
    return False


def projects_root() -> str:
    """기본 프로젝트 위치 data_root()\\projects (03 2-1). 폴더를 만들지 않는다."""
    return os.path.join(data_root(), "projects")


# ---------------------------------------------------------------------------
# 경로 점검 (S-09 · S-49, 07 M4 '경로 확인'): 앱을 켤 때와 프로젝트를 열 때 부른다.
#
# 메시지에는 경로를 넣지 않는다 — 로그·문제 보고에 그대로 남겨도 사용자 이름이 새지 않게 (S-20).
# 수준: "confirm" = 이동 권장/그래도 계속 을 묻는 대화상자 (S-09 ①: 경고만 띄우고 넘어가지 않는다)
#       "error"   = 그대로 쓰면 저장이 실패한다
#       "warn"    = 막지 않는 경고 (상태바·알림)
#       "info"    = 참고

APP_PATH_WARN_CHARS = 120       # S-49: 앱 경로
PROJECT_PATH_WARN_CHARS = 180   # S-49 · 02 5-4: 프로젝트 경로 (project.PATH_WARN_CHARS 와 같은 값)
DATA_PATH_WARN_CHARS = 150      # 데이터·백업 폴더: 아래에 projects\<slug 40자>\labels\<세션 파일 40자> 가 붙는다

PATH_ROLES = ("app", "data", "backup", "project")
_LONG_LIMIT = {"app": APP_PATH_WARN_CHARS, "data": DATA_PATH_WARN_CHARS,
               "backup": DATA_PATH_WARN_CHARS, "project": PROJECT_PATH_WARN_CHARS}
_ROLE_KO = {"app": "프로그램 폴더", "data": "데이터 폴더", "backup": "백업 폴더", "project": "프로젝트 폴더"}


class PathIssue:
    """경로 점검 결과 한 건. code: onedrive | onedrive_name | unc | long | not_writable | missing | non_ascii."""

    __slots__ = ("code", "level", "role", "message")

    def __init__(self, code: str, level: str, role: str, message: str):
        self.code, self.level, self.role, self.message = code, level, role, message

    def __repr__(self) -> str:
        return f"PathIssue({self.code!r}, {self.level!r}, {self.role!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, PathIssue) and (self.code, self.level, self.role) == (other.code, other.level, other.role)

    def __hash__(self) -> int:
        return hash((self.code, self.level, self.role))


def looks_like_onedrive(path: str) -> bool:
    """환경 변수와 상관없이 경로 조각 이름이 'OneDrive' 로 시작하는가 (예: 'OneDrive - 회사').

    환경 변수가 없는 계정(서비스·다른 사용자로 실행)에서도 잡기 위한 보조 판정이다.
    """
    p = os.path.abspath(path or ".").replace("/", "\\")
    return any(part.lower().startswith("onedrive") for part in p.split("\\")[1:] if part)


def has_non_ascii(path: str) -> bool:
    """경로에 ASCII 밖 글자(한글 등)가 있는가."""
    return any(ord(ch) > 127 for ch in (path or ""))


def probe_writable(d: str) -> bool:
    """이미 있는 폴더 d 에 시험 파일을 쓰고 지워 본다. 폴더를 만들지 않는다 (없으면 False)."""
    if not os.path.isdir(d):
        return False
    return _writable(d)


def path_issues(path: str, role: str = "project", *, check_write: bool = True) -> list[PathIssue]:
    """한 경로의 점검 결과. role: app | data | backup | project.

    - OneDrive(환경 변수 조상) · UNC → confirm (S-09 ①)
    - 이름만 OneDrive 처럼 보임 → warn
    - 길이 → warn (app 120 · data/backup 150 · project 180, S-49)
    - 쓸 수 없음 · 없음 → error (check_write 일 때. app 은 폴백이 있으니 warn)
    - 한글 등 ASCII 밖 글자 → info (대부분 괜찮다. 외부 도구가 실패하면 영어 폴더로)
    """
    if role not in PATH_ROLES:
        raise ValueError(f"알 수 없는 role: {role!r}")
    who = _ROLE_KO[role]
    out: list[PathIssue] = []
    if is_network_path(path):
        out.append(PathIssue("unc", "confirm", role,
                             f"{who}가 네트워크(UNC) 경로에 있습니다. 동시 저장·잠금을 믿을 수 없고 자동 업데이트·휴지통이 동작하지 않습니다. 내 PC 폴더로 옮기는 것을 권장합니다."))
    elif is_under_onedrive(path):
        out.append(PathIssue("onedrive", "confirm", role,
                             f"{who}가 OneDrive 아래에 있습니다. 채팅·라벨이 클라우드로 올라가고, 동기화 충돌로 저장이 실패하거나 파기한 뒤에도 OneDrive 휴지통·버전 기록에 사본이 남습니다. OneDrive 밖(예: %LOCALAPPDATA%\\Programs\\ChatCoach)으로 옮기는 것을 권장합니다."))
    elif looks_like_onedrive(path):
        out.append(PathIssue("onedrive_name", "warn", role,
                             f"{who} 경로에 'OneDrive' 라는 이름의 폴더가 있습니다. 동기화되는 폴더라면 OneDrive 밖으로 옮기세요."))
    a = os.path.abspath(path or ".")
    limit = _LONG_LIMIT[role]
    if len(a) > limit:
        out.append(PathIssue("long", "warn", role,
                             f"{who} 경로가 {len(a)}자로 {limit}자를 넘습니다. 긴 경로 때문에 저장이 실패할 수 있습니다. 더 짧은 위치로 옮기세요."))
    if check_write:
        if not os.path.isdir(a):
            out.append(PathIssue("missing", "error", role, f"{who}가 없습니다."))
        elif not _writable(a):
            out.append(PathIssue("not_writable", "warn" if role == "app" else "error", role,
                                 f"{who}에 쓸 수 없습니다." + (" 데이터는 %LOCALAPPDATA%\\ChatCoach\\data 에 둡니다(폴백)." if role == "app"
                                                          else " 저장이 실패합니다. 쓸 수 있는 폴더로 옮기세요.")))
    if has_non_ascii(a):
        out.append(PathIssue("non_ascii", "info", role,
                             f"{who} 경로에 한글 등 영문 밖 글자가 있습니다. 대부분 괜찮지만, 영상·업데이트 도구가 실패하면 영문 폴더로 옮겨 보세요."))
    return out


def startup_path_issues() -> list[PathIssue]:
    """앱을 켤 때: 프로그램·데이터·백업 폴더 점검. 같은 문제가 여러 폴더에 있으면 앞의 것만 (코드·수준 기준)."""
    out: list[PathIssue] = []
    seen: set[tuple[str, str]] = set()
    try:
        groups = [(install_root(), "app"), (data_root(), "data"), (backup_root(), "backup")]
    except OSError:
        groups = [(install_root(), "app")]
    for p, role in groups:
        for it in path_issues(p, role):
            key = (it.code, it.level)
            if it.code in ("onedrive", "unc", "non_ascii") and key in seen:
                continue
            seen.add(key)
            out.append(it)
    return out


def project_path_issues(project_root: str) -> list[PathIssue]:
    """프로젝트를 열 때·만들 때의 점검."""
    return path_issues(project_root, "project")


def is_network_path(path: str) -> bool:
    r"""UNC 경로(\\서버\공유)인가. \\?\UNC\ 는 네트워크, \\?\C:\ · \\.\ 장치 경로는 아니다.

    네트워크 드라이브에 붙인 문자(Z: 등)는 잡지 못한다.
    """
    p = (path or "").replace("/", "\\")
    if p.startswith(("\\\\?\\", "\\\\.\\")):
        return p[4:].upper().startswith("UNC\\")
    return p.startswith("\\\\")
