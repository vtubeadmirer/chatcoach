"""프로젝트 백업 zip · 보존 기한 · 프로젝트 파기 (07 M4, 05 S-06 · S-39 · 10-3, 03 2-2 · 2-3 · 16-2, 02 5-3, 04 6-7).

입출력 층이다(시계·파일을 쓴다). Qt 는 쓰지 않는다 — UI 는 이 모듈의 함수를 부르고 결과를 보여 준다.

백업 (zip 하나 + 안에 manifest.json, 파일마다 크기·SHA-256)
- 무엇을 담나 (허용 목록, 03 16-2): project.json · guide/ · labels/ · samples/ · locks/(open.lock 제외) · relabel/.
  채팅(chat/messages.jsonl · chat/fetch-*.json)은 '수동 백업' 과 '일 1회 백업 중 주 1회' 만.
  보호 구역(project.json 의 protected_zone ∪ 기본값 chat/protected/ · derived/asr* · derived/ocr* · origin/ · scenarios/ ·
  viewer/)은
  include_protected=True('보호 포함')일 때만. derived/ 의 나머지·캐시·<앱>\\data\\ai\\ 는 넣지 않는다.
  보호 구역 목록은 프로젝트 값과 기본값의 합집합이다 — project.json 이 비어 있어도 기본 보호는 빠지지 않는다.
- 어디에 (02 5-3, S-39): 기본 paths.backup_root()\\<project_id>\\ (= %LOCALAPPDATA%\\ChatCoach\\backups\\…).
  다른 곳을 고르면 check_backup_dest 가 본다: 프로젝트 폴더 안 · 데이터 폴더 안(백업 폴더 자신은 허용) ·
  프로그램(코드) 폴더 · git 작업 트리 안은 거부. OneDrive·UNC 는 경고, '보호 포함' 이면 거부.
- 일 1회 백업(daily_backup_if_due): data\\projects\\* 전부. 오늘 것이 있으면 건너뛴다. 프로젝트당 KEEP_DAILY(14)개,
  오래된 것부터 휴지통으로. 수동 백업은 순환하지 않는다.
- 업데이트 직전 백업(pre_update_backup, 04 6-7): settings.ini · labelers.json + 프로젝트들의 project.json · labels/ ·
  samples/ · locks/ · relabel/. backup_root()\\pre-update\\pre-update-<이전>-to-<새>-<yyyymmddHHMMSS>.zip, 최근 5개.
- 세션 끝 복사(copy_session_file, S-39 ③): 자기 세션 jsonl 하나를 backup_root()\\<project_id>\\sessions\\ 에.
- 되살리기(restore_backup): 새 폴더로만 푼다(있는 폴더를 덮지 않는다). 이름·크기·해시를 manifest 와 맞춘다.

보존·파기
- retention_state: project.json 의 retention_until 과 오늘 날짜로 ok · soon(30일 안) · expired.
- 파기 = 휴지통으로 보내기 (지우지 않는다). plan_disposal 로 대상 목록·경고를 먼저 보여 주고 execute_disposal 로 실행.
  대상: 프로젝트 폴더(또는 '라벨만 남기기' 면 chat/ · derived/ · 보호 구역), backup_root()\\<project_id>\\
  (폴백 data\\backups\\<project_id>\\ 도), data\\cache\\<project_id>\\(chat_index.drop_cache 를 먼저 부른다),
  data\\cache\\<영상 지문 앞 16자>\\, data\\ai\\<project_id>\\, 보고서 폴더의 <project_id>\\.
  결과는 data\\disposal_log.jsonl 에 한 줄(project_id · 시각 · 경로(사용자 홈 가림) · 라벨러 코드 · 결과).
  OneDrive·다른 PC·외부로 보낸 사본은 지울 수 없다 — 경고로 알린다(05 10-2 의 9).
- 휴지통이 없는 곳(네트워크 · 이동식 드라이브 · 260자 넘는 경로)은 '영구 삭제' 로 넘어가지 않고 실패로 남긴다.

로그에는 project_id · 개수 · 예외 종류 이름만 쓴다(경로·제목 없음, S-20).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Iterable

from . import paths
from .core.validate import PROJECT_MAX_BYTES, StrictJsonError, load_json_strict

LOG = logging.getLogger("chatcoach")

BACKUP_SCHEMA = "chatcoach/backup"
BACKUP_SCHEMA_VERSION = 1
DISPOSAL_SCHEMA = "chatcoach/disposal"
DISPOSAL_LOG_NAME = "disposal_log.jsonl"
MANIFEST_NAME = "manifest.json"
MANIFEST_MAX_BYTES = 16 * 1024 * 1024

KEEP_DAILY = 14               # 05 §9 결정 7 · 02 5-3
KEEP_PRE_UPDATE = 5           # 04 6-7
WEEKLY_CHAT_DAYS = 7          # 02 5-3 '채팅 원문은 주 1회'
RETENTION_SOON_DAYS = 30
RESTORE_MAX_BYTES = 4 * 1024 ** 3
RESTORE_MAX_ENTRIES = 200_000

DEFAULT_PROTECTED_ZONE = ("chat/protected/", "derived/asr*", "derived/ocr*", "origin/",
                          "scenarios/", "viewer/", "discern/")   # project.DEFAULT_PROTECTED_ZONE 와 같다. scenarios/: 가상 채팅은 앱 밖으로 안 나간다(D-05). viewer/: 시청자 연습 글(D-20 4). discern/: 가려내기 기록(H-5, 시나리오와 짝지으면 생성 줄이 드러남, 03 2-3)
CORE_DIRS = ("guide", "labels", "samples", "locks", "relabel")        # 03 16-2 '백업' 열
PRE_UPDATE_DIRS = ("labels", "samples", "locks", "relabel")          # 04 6-7
PROJECT_FILE = "project.json"
KINDS = ("daily", "manual")
PRE_UPDATE_DIR = "pre-update"
SESSIONS_DIR = "sessions"

_PID_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
BACKUP_NAME_RE = re.compile(r"\A(?P<kind>daily|manual)-(?P<date>\d{8})-(?P<time>\d{6})"
                            r"(?P<chat>-chat)?(?P<prot>-protected)?(?:-(?P<n>\d{1,3}))?\.zip\Z")
PRE_UPDATE_NAME_RE = re.compile(r"\Apre-update-(?P<from>[0-9A-Za-z.+_-]{1,40})-to-(?P<to>[0-9A-Za-z.+_-]{1,40})"
                                r"-(?P<ts>\d{14})(?:-(?P<n>\d{1,3}))?\.zip\Z")
# 백업에 넣지 않는 이름: 임시 파일(atomic_write · chat_index · 수집 임시 폴더) · 쓰기 시험 파일 · 실행 중 잠금
_SKIP_NAME_RE = re.compile(r"(?i)(?:\A\..*\.tmp\Z|\.tmp\d*\Z|\A\.tmp-|\A\.write_test_)")
_SAFE_SEGMENT_RE = re.compile(r'\A[^\\/:*?"<>|\x00-\x1f]{1,120}\Z')
_WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))})


class BackupError(RuntimeError):
    """백업을 만들거나 읽지 못했다."""


class BackupLocationError(BackupError):
    """백업 출력 위치가 허용되지 않는다."""


class TrashError(OSError):
    """휴지통으로 보내지 못했다. reason: windows_only | network | no_drive | not_fixed_drive | long_path | link |
    no_volume(subst·가상 드라이브) | bin_disabled | bin_too_small | shell_0x… | still_exists."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class DisposalError(RuntimeError):
    """파기를 시작할 수 없다(대상 검사 실패)."""


class ProjectBusyError(DisposalError):
    """프로젝트가 열려 있다(locks/open.lock)."""


TrashFn = Callable[[str], None]


# ============================================================================ 공통
def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now().astimezone()


def _abs(p: str) -> str:
    return os.path.abspath(p)


def _under(path: str, root: str) -> bool:
    """path 가 root 와 같거나 그 아래인가 (대소문자 무시)."""
    p = os.path.normcase(_abs(path)).rstrip("\\/")
    r = os.path.normcase(_abs(root)).rstrip("\\/")
    return p == r or p.startswith(r + os.sep)


def _is_link(p: str) -> bool:
    try:
        return os.path.islink(p) or bool(getattr(os.path, "isjunction", lambda _p: False)(p))
    except OSError:
        return True


def _in_git_tree(path: str) -> bool:
    d = _abs(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return True
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up


def read_project_json(project_root: str) -> dict:
    """project.json 을 엄격 JSON 으로 읽는다. 없거나 깨졌으면 BackupError."""
    p = os.path.join(project_root, PROJECT_FILE)
    try:
        with open(p, "rb") as f:
            data = f.read(PROJECT_MAX_BYTES + 1)
        obj = load_json_strict(data, PROJECT_MAX_BYTES)
    except (OSError, StrictJsonError, ValueError) as e:
        raise BackupError(f"project.json 을 읽지 못했습니다 ({type(e).__name__})") from e
    if not isinstance(obj, dict):
        raise BackupError("project.json 이 객체가 아닙니다")
    return obj


def project_id_of(project_root: str) -> str:
    pid = read_project_json(project_root).get("project_id")
    if not isinstance(pid, str) or not _PID_RE.match(pid):
        raise BackupError("project.json 의 project_id 형식이 아닙니다")
    return pid


def protected_patterns(project: dict | None) -> list[str]:
    """프로젝트의 protected_zone 과 기본값의 합집합 (보호 쪽으로 실패)."""
    out = list(DEFAULT_PROTECTED_ZONE)
    zone = (project or {}).get("protected_zone")
    if isinstance(zone, list):
        for z in zone:
            if isinstance(z, str) and z.strip() and z not in out:
                out.append(z)
    return out


def in_protected_zone(rel: str, patterns: Iterable[str]) -> bool:
    """프로젝트 기준 상대 경로(rel, '/' 또는 '\\\\')가 보호 구역인가.

    'dir/' 꼴은 그 폴더 아래 전부. 그 밖은 glob 이며, 경로 앞부분(폴더)이 맞아도 그 아래 전부가 보호다
    (예: 'derived/asr*' 는 derived/asr.json 과 derived/asr_v2/x.json 모두). 대소문자 무시.
    """
    r = rel.replace("\\", "/").lstrip("/").lower()
    parts = [x for x in r.split("/") if x]
    for pat in patterns:
        p = pat.replace("\\", "/").lstrip("/").lower()
        if not p:
            continue
        if p.endswith("/"):
            if r.startswith(p) or r + "/" == p:
                return True
            continue
        for i in range(1, len(parts) + 1):
            if fnmatch.fnmatchcase("/".join(parts[:i]), p):
                return True
    return False


def _skip_name(name: str) -> bool:
    return bool(_SKIP_NAME_RE.search(name))


def _walk_files(root: str, rel_dir: str) -> list[str]:
    """root/rel_dir 아래 일반 파일의 상대 경로('/' 구분) 목록. 링크·정션은 따라가지 않고 뺀다(S-46). 정렬."""
    base = os.path.join(root, *rel_dir.split("/")) if rel_dir else root
    out: list[str] = []
    if not os.path.isdir(base) or _is_link(base):
        return out
    stack = [(base, rel_dir)]
    while stack:
        d, rd = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if _skip_name(e.name):
                continue
            rel = f"{rd}/{e.name}" if rd else e.name
            try:
                if e.is_symlink() or _is_link(e.path):
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append((e.path, rel))
                elif e.is_file(follow_symlinks=False):
                    out.append(rel)
            except OSError:
                continue
    return sorted(out)


def select_files(project_root: str, *, include_chat: bool, include_protected: bool,
                 dirs: Iterable[str] = CORE_DIRS, project: dict | None = None) -> list[str]:
    """백업할 상대 경로 목록 (허용 목록, 03 16-2). 보호 구역은 include_protected 일 때만."""
    if project is None:
        try:
            project = read_project_json(project_root)
        except BackupError:
            project = None
    zone = protected_patterns(project)
    rels: list[str] = []
    if os.path.isfile(os.path.join(project_root, PROJECT_FILE)):
        rels.append(PROJECT_FILE)
    for d in dirs:
        rels += _walk_files(project_root, d)
    if include_chat:
        chat = os.path.join(project_root, "chat")
        for name in ("messages.jsonl",):
            if os.path.isfile(os.path.join(chat, name)) and not _is_link(os.path.join(chat, name)):
                rels.append(f"chat/{name}")
        try:
            names = sorted(os.listdir(chat))
        except OSError:
            names = []
        for n in names:
            if re.match(r"\Afetch-[A-Za-z0-9_-]{1,64}\.json\Z", n) and os.path.isfile(os.path.join(chat, n)):
                rels.append(f"chat/{n}")
    if include_protected:
        for rel in _walk_files(project_root, ""):
            if in_protected_zone(rel, zone):
                rels.append(rel)
    out, seen = [], set()
    for rel in rels:
        key = rel.lower()
        if key in seen:
            continue
        if key == "locks/open.lock":
            continue
        if not include_protected and in_protected_zone(rel, zone):
            continue
        seen.add(key)
        out.append(rel)
    return out


def _sha256_file(path: str) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
    return h.hexdigest(), n


def _unique_path(d: str, stem: str, ext: str = ".zip") -> str:
    p = os.path.join(d, stem + ext)
    n = 2
    while os.path.exists(p):
        p = os.path.join(d, f"{stem}-{n}{ext}")
        n += 1
        if n > 999:
            raise BackupError("같은 이름의 백업이 너무 많습니다")
    return p


def _write_zip(dest: str, entries: list[tuple[str, str]], manifest: dict) -> dict:
    """entries = [(zip 안 이름, 원본 경로)]. 임시 파일에 쓰고 검사한 뒤 os.replace. manifest 에 files·skipped 를 채워 돌려준다.

    읽는 사이 사라진 파일은 건너뛰고 manifest.skipped 에 적는다(이유는 예외 종류 이름만).
    """
    d = os.path.dirname(_abs(dest))
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(dest)}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    files, skipped = [], []
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                             strict_timestamps=False) as z:
            for arc, src in entries:
                try:
                    zi = zipfile.ZipInfo.from_file(src, arc, strict_timestamps=False)
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    h = hashlib.sha256()
                    n = 0
                    with open(src, "rb") as f, z.open(zi, "w", force_zip64=True) as out:
                        while True:
                            b = f.read(1 << 20)
                            if not b:
                                break
                            h.update(b)
                            n += len(b)
                            out.write(b)
                except OSError as e:
                    skipped.append({"path": arc, "reason": type(e).__name__})
                    continue
                files.append({"path": arc, "size": n, "sha256": h.hexdigest()})
            manifest = dict(manifest, files=files, skipped=skipped)
            z.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=1, allow_nan=False) + "\n")
        with zipfile.ZipFile(tmp) as z:
            bad = z.testzip()
            if bad is not None:
                raise BackupError("만든 백업 zip 검사에 실패했습니다")
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return manifest


def _app_version(v: str | None) -> str:
    if v:
        return v
    try:
        from . import __version__
        return __version__
    except ImportError:
        return "0.0.0"


# ============================================================================ 위치 검사
def check_backup_dest(out_dir: str, project_root: str | None = None, *, include_protected: bool = False) -> list[str]:
    """백업 출력 위치 검사. 막는 경우 BackupLocationError, 막지 않는 경고는 목록으로.

    허용: paths.backup_root() 아래(폴백 data\\backups 포함), 그 밖의 '데이터 폴더 밖' 폴더.
    거부: 프로젝트 폴더 안, 데이터 폴더 안(백업 폴더 제외), 프로그램(코드) 폴더 안, git 작업 트리 안,
          '보호 포함' 인데 OneDrive·UNC.
    """
    if not isinstance(out_dir, str) or not out_dir.strip():
        raise BackupLocationError("백업 위치가 없습니다")
    warns: list[str] = []
    if project_root and _under(out_dir, project_root):
        raise BackupLocationError("백업 위치가 프로젝트 폴더 안입니다. 프로젝트 밖을 고르세요")
    try:
        broot = paths.backup_root()
    except OSError:
        broot = None
    in_backup_root = bool(broot) and _under(out_dir, broot)
    if not in_backup_root:
        try:
            droot = paths.data_root()
        except OSError:
            droot = None
        if droot and _under(out_dir, droot):
            raise BackupLocationError("백업 위치가 데이터 폴더 안입니다. 앱 폴더를 지우면 함께 사라집니다(S-39). 데이터 폴더 밖을 고르세요")
        if _under(out_dir, paths.code_dir()) or _under(out_dir, paths.install_root()):
            raise BackupLocationError("백업 위치가 프로그램 폴더 안입니다. 프로그램 폴더 밖을 고르세요")
        if _in_git_tree(out_dir):
            raise BackupLocationError("백업 위치가 저장소(git 작업 트리) 안입니다. 저장소 밖을 고르세요")
    risky = paths.is_network_path(out_dir) or paths.is_under_onedrive(out_dir) or paths.looks_like_onedrive(out_dir)
    if risky and include_protected:
        raise BackupLocationError("보호 구역(원 닉네임·식별자)을 넣은 백업은 OneDrive·네트워크 폴더에 둘 수 없습니다")
    if risky:
        warns.append("백업 위치가 OneDrive 또는 네트워크 폴더입니다. 채팅·라벨 사본이 클라우드·다른 PC 로 퍼집니다. 내 PC 폴더를 권장합니다.")
    return warns


# ============================================================================ 백업
@dataclass
class BackupResult:
    path: str
    project_id: str
    kind: str
    manifest: dict
    warnings: list[str] = field(default_factory=list)
    rotated: list[str] = field(default_factory=list)


def default_backup_dir(project_id: str, backup_root: str | None = None) -> str:
    if not _PID_RE.match(project_id or ""):
        raise BackupError("project_id 형식이 아닙니다")
    return os.path.join(backup_root or paths.backup_root(), project_id)


def backup_project(project_root: str, *, kind: str = "manual", include_chat: bool | None = None,
                   include_protected: bool = False, out_dir: str | None = None, now: datetime | None = None,
                   backup_root: str | None = None, keep: int = KEEP_DAILY, trash: TrashFn | None = None,
                   app_version: str | None = None) -> BackupResult:
    """프로젝트 하나를 zip 으로. zip 안은 '<프로젝트 폴더 이름>/…' + manifest.json.

    include_chat 기본: manual 이면 True, daily 면 False. out_dir 가 없으면 backup_root()\\<project_id>\\.
    kind == 'daily' 이고 기본 위치면 끝에 KEEP_DAILY 개만 남긴다(오래된 것부터 휴지통).
    """
    if kind not in KINDS:
        raise ValueError(f"알 수 없는 백업 종류: {kind!r}")
    root = _abs(project_root)
    proj = read_project_json(root)
    pid = proj.get("project_id")
    if not isinstance(pid, str) or not _PID_RE.match(pid):
        raise BackupError("project.json 의 project_id 형식이 아닙니다")
    if include_chat is None:
        include_chat = kind == "manual"
    default_dir = default_backup_dir(pid, backup_root)
    target_dir = _abs(out_dir) if out_dir else default_dir
    warns = check_backup_dest(target_dir, root, include_protected=include_protected) if out_dir else []
    if include_protected:
        warns.append("보호 구역(원 닉네임·식별자·원문)이 들어 있습니다. 이 파일을 다른 사람에게 보내지 마세요.")
    rels = select_files(root, include_chat=include_chat, include_protected=include_protected, project=proj)
    folder = os.path.basename(root.rstrip("\\/")) or "project"
    now = _now(now)
    stem = f"{kind}-{now.strftime('%Y%m%d-%H%M%S')}{'-chat' if include_chat else ''}{'-protected' if include_protected else ''}"
    os.makedirs(target_dir, exist_ok=True)
    dest = _unique_path(target_dir, stem)
    manifest = {
        "schema": BACKUP_SCHEMA, "schema_version": BACKUP_SCHEMA_VERSION, "kind": kind,
        "created_at": now.isoformat(timespec="seconds"), "app_version": _app_version(app_version),
        "project_id": pid, "folder": folder, "include_chat": bool(include_chat),
        "include_protected": bool(include_protected), "protected_zone": protected_patterns(proj),
    }
    entries = [(f"{folder}/{rel}", os.path.join(root, *rel.split("/"))) for rel in rels]
    manifest = _write_zip(dest, entries, manifest)
    rotated: list[str] = []
    if kind == "daily" and os.path.normcase(target_dir) == os.path.normcase(_abs(default_dir)):
        try:
            rotated = rotate_backups(target_dir, keep=keep, kind="daily", trash=trash)
        except TrashError as e:
            warns.append(f"오래된 백업을 휴지통으로 보내지 못했습니다({e.reason}). 직접 정리하세요.")
    LOG.info("백업: %s %s 파일 %d개 (채팅 %s, 보호 %s), 순환 %d개", pid, kind, len(manifest["files"]),
             "포함" if include_chat else "제외", "포함" if include_protected else "제외", len(rotated))
    return BackupResult(dest, pid, kind, manifest, warns, rotated)


@dataclass
class BackupInfo:
    path: str
    kind: str
    created: datetime
    include_chat: bool
    include_protected: bool
    size: int


def list_backups(project_id: str, *, backup_root: str | None = None) -> list[BackupInfo]:
    """backup_root()\\<project_id>\\ 의 백업 zip (이름 규칙에 맞는 것만). 오래된 것부터."""
    d = default_backup_dir(project_id, backup_root)
    out: list[BackupInfo] = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        m = BACKUP_NAME_RE.match(n)
        if not m:
            continue
        try:
            created = datetime.strptime(m["date"] + m["time"], "%Y%m%d%H%M%S")
            size = os.path.getsize(os.path.join(d, n))
        except (ValueError, OSError):
            continue
        out.append(BackupInfo(os.path.join(d, n), m["kind"], created, bool(m["chat"]), bool(m["prot"]), size))
    out.sort(key=lambda b: (b.created, b.path))
    return out


def rotate_backups(directory: str, *, keep: int = KEEP_DAILY, kind: str = "daily",
                   trash: TrashFn | None = None) -> list[str]:
    """directory 의 kind 백업을 이름(시각) 순으로 보고 최근 keep 개만 남긴다. 나머지는 휴지통. 보낸 경로 목록.

    휴지통으로 못 보내면 TrashError 를 올린다(영구 삭제로 넘어가지 않는다). 그때까지 보낸 것은 이미 휴지통에 있다.
    """
    if keep < 1:
        raise ValueError("keep 은 1 이상")
    trash = trash or send_to_trash
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    if kind == "pre_update":
        found = sorted((m["ts"], n) for n in names if (m := PRE_UPDATE_NAME_RE.match(n)))
    else:
        found = sorted((m["date"] + m["time"] + (m["n"] or "").zfill(3), n)
                       for n in names if (m := BACKUP_NAME_RE.match(n)) and m["kind"] == kind)
    old = [n for _, n in found[:-keep]] if len(found) > keep else []
    sent = []
    for n in old:
        p = os.path.join(directory, n)
        trash(p)
        sent.append(p)
    return sent


@dataclass
class DailyReport:
    done: list[BackupResult] = field(default_factory=list)
    skipped: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)   # (project_id 또는 '?', 예외 종류 이름)


def _project_dirs(projects_dir: str) -> list[str]:
    try:
        names = sorted(os.listdir(projects_dir))
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(projects_dir, n)
        if os.path.isdir(p) and not _is_link(p) and os.path.isfile(os.path.join(p, PROJECT_FILE)):
            out.append(p)
    return out


def daily_backup_if_due(projects_dir: str | None = None, *, extra_roots: Iterable[str] = (),
                        now: datetime | None = None, backup_root: str | None = None, keep: int = KEEP_DAILY,
                        trash: TrashFn | None = None, app_version: str | None = None) -> DailyReport:
    """data\\projects\\* 전부(+ extra_roots: 앱 밖 프로젝트)의 일 1회 백업. 오늘 것이 있으면 건너뛴다.

    마지막 '채팅 포함' 일일 백업이 WEEKLY_CHAT_DAYS 일 넘게 지났으면 이번 것에 채팅을 넣는다.
    프로젝트 하나가 실패해도 나머지는 계속한다(실패는 report.failed).
    """
    now = _now(now)
    today = now.date()
    rep = DailyReport()
    roots: list[str] = []
    if projects_dir is None:
        projects_dir = paths.projects_root()
    roots += _project_dirs(projects_dir)
    seen = {os.path.normcase(_abs(r)) for r in roots}
    for r in extra_roots:
        k = os.path.normcase(_abs(r))
        if k not in seen and os.path.isfile(os.path.join(r, PROJECT_FILE)):
            roots.append(r)
            seen.add(k)
    for root in roots:
        pid = "?"
        try:
            pid = project_id_of(root)
            infos = [b for b in list_backups(pid, backup_root=backup_root) if b.kind == "daily"]
            if any(b.created.date() == today for b in infos):
                rep.skipped += 1
                continue
            last_chat = max((b.created.date() for b in infos if b.include_chat), default=None)
            with_chat = last_chat is None or (today - last_chat).days >= WEEKLY_CHAT_DAYS
            rep.done.append(backup_project(root, kind="daily", include_chat=with_chat, now=now,
                                           backup_root=backup_root, keep=keep, trash=trash, app_version=app_version))
        except (BackupError, OSError, ValueError) as e:
            rep.failed.append((pid, type(e).__name__))
            LOG.warning("일일 백업 실패: %s (%s)", pid, type(e).__name__)
    return rep


def copy_session_file(project_root: str, session_file: str, *, backup_root: str | None = None) -> str:
    """세션 종료 때 자기 세션 기록(labels/<session>.jsonl) 하나를 backup_root()\\<project_id>\\sessions\\ 에 복사 (S-39 ③).

    같은 이름이 있으면 새 내용으로 바꾼다(추가만 하는 파일이라 새 것이 옛 것을 포함한다).
    """
    root = _abs(project_root)
    src = _abs(session_file)
    if not _under(src, os.path.join(root, "labels")) or not src.lower().endswith(".jsonl") or _is_link(src):
        raise BackupError("세션 기록 파일이 이 프로젝트의 labels 폴더에 없습니다")
    pid = project_id_of(root)
    d = os.path.join(default_backup_dir(pid, backup_root), SESSIONS_DIR)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, os.path.basename(src))
    tmp = f"{dest}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return dest


def _safe_ver(v: str) -> str:
    s = re.sub(r"[^0-9A-Za-z.+_-]", "_", str(v or "?"))[:40]
    return s or "_"


def pre_update_backup(project_roots: Iterable[str], from_ver: str, to_ver: str, *, data_root: str | None = None,
                      backup_root: str | None = None, now: datetime | None = None, keep: int = KEEP_PRE_UPDATE,
                      trash: TrashFn | None = None) -> str:
    """업데이트 직전 data 백업 (04 6-7). 만든 zip 경로. 실패하면 BackupError/OSError — 업데이트를 멈춰야 한다.

    zip 안: data/settings.ini · data/labelers.json, projects/<project_id>/{project.json, labels/, samples/, locks/, relabel/}.
    chat/ · derived/ · 보호 구역은 넣지 않는다(identities 사본을 늘리지 않는다, S-06).
    project.json 을 못 읽는 프로젝트는 폴더 경로 해시로 이름을 붙여 라벨만이라도 담는다.
    """
    now = _now(now)
    droot = data_root or paths.data_root()
    d = os.path.join(backup_root or paths.backup_root(), PRE_UPDATE_DIR)
    os.makedirs(d, exist_ok=True)
    stem = f"pre-update-{_safe_ver(from_ver)}-to-{_safe_ver(to_ver)}-{now.strftime('%Y%m%d%H%M%S')}"
    dest = _unique_path(d, stem)
    entries: list[tuple[str, str]] = []
    for name in ("settings.ini", "labelers.json"):
        p = os.path.join(droot, name)
        if os.path.isfile(p) and not _is_link(p):
            entries.append((f"data/{name}", p))
    projects = []
    seen: set[str] = set()
    for r in project_roots:
        root = _abs(r)
        key = os.path.normcase(root)
        if key in seen or not os.path.isdir(root):
            continue
        seen.add(key)
        try:
            proj = read_project_json(root)
            pid = proj.get("project_id")
            if not isinstance(pid, str) or not _PID_RE.match(pid):
                raise BackupError("project_id")
        except BackupError:
            proj = None
            pid = "noid-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        rels = select_files(root, include_chat=False, include_protected=False, dirs=PRE_UPDATE_DIRS, project=proj)
        entries += [(f"projects/{pid}/{rel}", os.path.join(root, *rel.split("/"))) for rel in rels]
        projects.append(pid)
    manifest = {"schema": BACKUP_SCHEMA, "schema_version": BACKUP_SCHEMA_VERSION, "kind": "pre_update",
                "created_at": now.isoformat(timespec="seconds"), "from_version": str(from_ver),
                "to_version": str(to_ver), "projects": projects, "include_chat": False, "include_protected": False}
    manifest = _write_zip(dest, entries, manifest)
    try:
        rotate_backups(d, keep=keep, kind="pre_update", trash=trash)
    except TrashError:
        LOG.warning("업데이트 직전 백업 순환: 휴지통으로 보내지 못함")
    LOG.info("업데이트 직전 백업: 프로젝트 %d개, 파일 %d개", len(projects), len(manifest["files"]))
    return dest


# ============================================================================ 읽기·검사·되살리기
def read_manifest(zip_path: str) -> dict:
    try:
        with zipfile.ZipFile(zip_path) as z:
            info = z.getinfo(MANIFEST_NAME)
            if info.file_size > MANIFEST_MAX_BYTES:
                raise BackupError("manifest.json 이 너무 큽니다")
            data = z.read(MANIFEST_NAME)
    except KeyError as e:
        raise BackupError("manifest.json 이 없습니다") from e
    except (OSError, zipfile.BadZipFile) as e:
        raise BackupError(f"백업 zip 을 열지 못했습니다 ({type(e).__name__})") from e
    try:
        obj = load_json_strict(data, MANIFEST_MAX_BYTES)
    except (StrictJsonError, ValueError) as e:
        raise BackupError("manifest.json 이 올바른 JSON 이 아닙니다") from e
    if not isinstance(obj, dict) or obj.get("schema") != BACKUP_SCHEMA or not isinstance(obj.get("files"), list):
        raise BackupError("ChatCoach 백업 manifest 가 아닙니다")
    return obj


def _safe_arcname(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name or ":" in name:
        return False
    for seg in name.split("/"):
        if seg in ("", ".", "..") or not _SAFE_SEGMENT_RE.match(seg) or seg.rstrip(" .") != seg:
            return False
        if seg.split(".")[0].upper() in _WINDOWS_RESERVED:
            return False
    return True


def verify_backup(zip_path: str) -> list[str]:
    """zip 의 이름 규칙·크기·SHA-256 을 manifest 와 맞춘다. 문제 목록(비면 정상). 내용은 메모리에 다 올리지 않는다."""
    problems: list[str] = []
    try:
        man = read_manifest(zip_path)
    except BackupError as e:
        return [str(e)]
    want = {}
    for f in man["files"]:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str):
            problems.append("manifest 의 files 항목 형식이 틀립니다")
            continue
        want[f["path"]] = f
    try:
        with zipfile.ZipFile(zip_path) as z:
            infos = z.infolist()
            if len(infos) > RESTORE_MAX_ENTRIES:
                return ["zip 항목이 너무 많습니다"]
            total = 0
            names = set()
            for info in infos:
                n = info.filename
                if n == MANIFEST_NAME:
                    continue
                if info.is_dir():
                    continue
                names.add(n)
                if not _safe_arcname(n):
                    problems.append("zip 안에 쓸 수 없는 이름이 있습니다")
                    continue
                if n not in want:
                    problems.append("manifest 에 없는 파일이 zip 에 있습니다")
                    continue
                total += info.file_size
                if total > RESTORE_MAX_BYTES:
                    return problems + ["풀었을 때 크기가 너무 큽니다"]
                h = hashlib.sha256()
                size = 0
                with z.open(info) as src:
                    while True:
                        b = src.read(1 << 20)
                        if not b:
                            break
                        h.update(b)
                        size += len(b)
                        if size > RESTORE_MAX_BYTES:
                            return problems + ["풀었을 때 크기가 너무 큽니다"]
                if size != want[n].get("size") or h.hexdigest() != want[n].get("sha256"):
                    problems.append("파일 크기·해시가 manifest 와 다릅니다")
            if set(want) - names:
                problems.append("manifest 의 파일이 zip 에 없습니다")
    except (OSError, zipfile.BadZipFile) as e:
        problems.append(f"백업 zip 을 읽지 못했습니다 ({type(e).__name__})")
    return problems


@dataclass
class RestoreResult:
    path: str
    files: int
    warnings: list[str] = field(default_factory=list)


def restore_backup(zip_path: str, dest_parent: str, *, now: datetime | None = None) -> RestoreResult:
    """프로젝트 백업 zip 을 dest_parent 아래 새 폴더로 푼다. 있는 폴더는 덮지 않는다.

    폴더 이름은 manifest 의 folder. 이미 있으면 '<folder>-restored-<yyyymmdd-HHMMSS>'.
    같은 project_id 의 프로젝트가 둘이 되므로, 원본이 없어졌을 때만 쓰라고 화면이 알린다.
    """
    problems = verify_backup(zip_path)
    if problems:
        raise BackupError("백업 검사 실패: " + "; ".join(problems[:3]))
    man = read_manifest(zip_path)
    if man.get("kind") not in KINDS:
        raise BackupError("프로젝트 백업이 아닙니다(업데이트 직전 백업은 직접 풀어 필요한 파일만 옮기세요)")
    folder = man.get("folder")
    if not isinstance(folder, str) or not _safe_arcname(folder) or "/" in folder:
        raise BackupError("manifest 의 folder 가 쓸 수 없는 이름입니다")
    parent = _abs(dest_parent)
    os.makedirs(parent, exist_ok=True)
    warns: list[str] = []
    if man.get("include_protected") and (paths.is_under_onedrive(parent) or paths.is_network_path(parent)):
        raise BackupError("보호 구역이 든 백업은 OneDrive·네트워크 폴더에 풀 수 없습니다")
    target = os.path.join(parent, folder)
    if os.path.exists(target):
        target = os.path.join(parent, f"{folder}-restored-{_now(now).strftime('%Y%m%d-%H%M%S')}")
        if os.path.exists(target):
            raise BackupError("되살릴 폴더 이름이 이미 있습니다")
        warns.append("같은 이름의 폴더가 있어 새 이름으로 풀었습니다. 같은 프로젝트가 둘이 되지 않게 하나만 쓰세요.")
    tmp = os.path.join(parent, f".restore-{uuid.uuid4().hex[:12]}")
    os.mkdir(tmp)
    n = 0
    try:
        with zipfile.ZipFile(zip_path) as z:
            for f in man["files"]:
                arc = f["path"]
                if not arc.startswith(folder + "/"):
                    raise BackupError("zip 안 파일이 프로젝트 폴더 밖을 가리킵니다")
                rel = arc[len(folder) + 1:]
                out = os.path.join(tmp, *rel.split("/"))
                if not _under(out, tmp):
                    raise BackupError("zip 안 파일이 프로젝트 폴더 밖을 가리킵니다")
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with z.open(arc) as src, open(out, "xb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                n += 1
        # 빈 폴더는 zip 에 없으니 프로젝트 기본 폴더를 만들어 둔다 (project.ProjectPaths.created_dirs 와 같은 목록)
        for sub in ("guide", "chat", os.path.join("chat", "protected"), "labels", "samples", "locks", "derived"):
            os.makedirs(os.path.join(tmp, sub), exist_ok=True)
        os.rename(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)       # 방금 만든 임시 폴더(우리 파일만)
        raise
    LOG.info("백업 되살리기: %s 파일 %d개", man.get("project_id"), n)
    return RestoreResult(target, n, warns)


# ============================================================================ 보존 기한
@dataclass(frozen=True)
class RetentionState:
    status: str            # ok | soon | expired | unknown
    until: date | None
    days_left: int | None


def retention_state(project: dict, today: date, *, soon_days: int = RETENTION_SOON_DAYS) -> RetentionState:
    """project.json 의 retention_until(YYYY-MM-DD) 로 보존 상태. 순수 함수(today 를 받는다)."""
    raw = (project or {}).get("retention_until")
    try:
        until = date.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        until = None
    if until is None:
        return RetentionState("unknown", None, None)
    left = (until - today).days
    if left < 0:
        return RetentionState("expired", until, left)
    if left <= soon_days:
        return RetentionState("soon", until, left)
    return RetentionState("ok", until, left)


def retention_message(state: RetentionState) -> str | None:
    """화면 표시용 한 줄. ok 면 None."""
    if state.status == "expired":
        return f"보존 기한({state.until.isoformat()})이 {-state.days_left}일 지났습니다. 목적을 이뤘으면 '파일 → 프로젝트 지우기(휴지통)' 로 파기하세요."
    if state.status == "soon":
        return f"보존 기한({state.until.isoformat()})까지 {state.days_left}일 남았습니다."
    if state.status == "unknown":
        return "보존 기한이 적혀 있지 않습니다."
    return None


# ============================================================================ 휴지통
_DRIVE_FIXED = 3


def can_trash(path: str) -> str | None:
    """휴지통으로 보낼 수 있으면 None, 아니면 사유 코드. 휴지통이 없는 곳에서 셸은 조용히 영구 삭제하므로 미리 막는다."""
    if os.name != "nt":
        return "windows_only"
    a = _abs(path)
    if paths.is_network_path(a):
        return "network"
    drive = os.path.splitdrive(a)[0]
    if not re.match(r"\A[A-Za-z]:\Z", drive or ""):
        return "no_drive"
    import ctypes
    try:
        t = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
    except (AttributeError, OSError):
        return "no_drive"
    if t != _DRIVE_FIXED:
        return "not_fixed_drive"
    if len(a) >= 260:
        return "long_path"
    if _is_link(a):
        return "link"
    return _recycle_bin_problem(a)


_BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket"
_NO_RECYCLE_POLICY = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"


def _volume_guid(a: str) -> str | None:
    """경로가 있는 볼륨의 GUID('{…}'). subst·가상 드라이브처럼 볼륨 마운트 지점이 없으면 None."""
    import ctypes
    k32 = ctypes.windll.kernel32
    vp = ctypes.create_unicode_buffer(1024)
    if not k32.GetVolumePathNameW(a, vp, len(vp)):
        return None
    vn = ctypes.create_unicode_buffer(64)
    if not k32.GetVolumeNameForVolumeMountPointW(vp.value, vn, len(vn)):
        return None
    m = re.search(r"\{[0-9A-Fa-f-]{36}\}", vn.value)
    return m.group(0) if m else None


def _reg_dword(hive, key: str, name: str) -> int | None:
    import winreg
    try:
        with winreg.OpenKey(hive, key) as k:
            v, t = winreg.QueryValueEx(k, name)
    except OSError:
        return None
    return v if isinstance(v, int) else None


def _tree_size(a: str) -> int:
    if os.path.isfile(a):
        try:
            return os.path.getsize(a)
        except OSError:
            return 0
    total = 0
    for dirpath, _dirs, files in os.walk(a):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _recycle_bin_problem(a: str) -> str | None:
    """이 대상을 휴지통이 실제로 받는가. 받지 못하는 곳에서 셸은 묻지 않고 영구 삭제한다(FOF_NOCONFIRMATION).

    - no_volume: 볼륨 GUID 가 없다(subst·가상 드라이브) — 고정 드라이브로 보여도 휴지통이 없다.
    - bin_disabled: 정책 NoRecycleFiles=1 이거나 그 볼륨의 '휴지통으로 이동하지 않음'(NukeOnDelete=1).
    - bin_too_small: 대상 크기가 그 볼륨 휴지통의 최대 크기(MaxCapacity, MB)보다 크다.
    볼륨 설정이 레지스트리에 아직 없으면(한 번도 쓰지 않은 볼륨) 크기는 보지 않는다 — 그때는 send_to_trash 의
    FOF_WANTNUKEWARNING 이 영구 삭제 전에 묻는다."""
    try:
        import winreg
    except ImportError:
        return "windows_only"
    try:
        guid = _volume_guid(a)
    except (AttributeError, OSError):
        return "no_volume"
    if guid is None:
        return "no_volume"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        if _reg_dword(hive, _NO_RECYCLE_POLICY, "NoRecycleFiles") == 1:
            return "bin_disabled"
    key = _BITBUCKET + "\\Volume\\" + guid
    if _reg_dword(winreg.HKEY_CURRENT_USER, key, "NukeOnDelete") == 1:
        return "bin_disabled"
    cap_mb = _reg_dword(winreg.HKEY_CURRENT_USER, key, "MaxCapacity")
    if cap_mb is not None and _tree_size(a) > cap_mb * 1024 * 1024:
        return "bin_too_small"
    return None


def send_to_trash(path: str) -> None:
    """파일·폴더 하나를 Windows 휴지통으로 보낸다(SHFileOperationW + FOF_ALLOWUNDO). 실패하면 TrashError.

    영구 삭제로 넘어가지 않는다. 휴지통에서 되살릴 수 있다.
    """
    reason = can_trash(path)
    if reason:
        raise TrashError(reason)
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]

    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
    FOF_WANTNUKEWARNING = 0x4000    # 휴지통이 못 받아 영구 삭제로 넘어가려 하면 조용히 지우지 않고 묻는다(거절 → 중단)
    a = _abs(path)
    buf = ctypes.create_unicode_buffer(a + "\0")          # 끝 NUL 두 개
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, ctypes.c_void_p)
    op.pTo = None
    op.fFlags = FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI | FOF_WANTNUKEWARNING
    fn = ctypes.windll.shell32.SHFileOperationW
    fn.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    fn.restype = ctypes.c_int
    rc = fn(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise TrashError(f"shell_{rc:#x}")
    if os.path.lexists(a):
        raise TrashError("still_exists")


# ============================================================================ 파기
@dataclass
class DisposalTarget:
    kind: str       # project | project_chat | project_derived | project_protected | backups | cache | media_cache | ai | reports
    path: str
    trash_problem: str | None = None


@dataclass
class DisposalPlan:
    project_id: str
    project_root: str
    keep_labels: bool
    targets: list[DisposalTarget]
    warnings: list[str]
    data_root: str
    pre_update_backups: int = 0


@dataclass
class DisposalResult:
    project_id: str
    trashed: list[DisposalTarget] = field(default_factory=list)
    failed: list[tuple[DisposalTarget, str]] = field(default_factory=list)
    cache_index_dropped: bool = False
    log_path: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed


def _media_fps(project: dict) -> list[str]:
    out = []
    media = project.get("media") if isinstance(project.get("media"), dict) else {}
    refs = [media.get("current")] + list(media.get("history") or [])
    for r in refs:
        fp = r.get("partial_fp") if isinstance(r, dict) else None
        if isinstance(fp, str) and re.match(r"\A[0-9a-f]{64}\Z", fp):
            if fp[:16] not in out:
                out.append(fp[:16])
    return out


def _default_report_roots(project_root: str, droot: str) -> list[str]:
    roots = [os.path.join(droot, "reports")]
    try:
        from .agreement_report import default_out_root
        roots.append(default_out_root(project_root))
    except Exception:                       # 보고서 모듈을 못 읽어도 파기는 계속한다
        pass
    return roots


def _default_ai_roots(project_root: str, droot: str) -> list[str]:
    """AI 결과(봉인 원본·seal.jsonl·runs) 루트 후보: <데이터>\\ai 와 app.seal.default_ai_root(프로젝트가
    '…\\projects\\<slug>' 에 있으면 그 옆 '…\\ai'). 보고서 루트와 같은 규칙."""
    roots = [os.path.join(droot, "ai")]
    try:
        from .seal import default_ai_root
        roots.append(default_ai_root(project_root))
    except Exception:                       # 봉인 모듈을 못 읽어도 파기는 계속한다
        pass
    return roots


def _count_pre_update_with(pid: str, broot: str) -> int:
    d = os.path.join(broot, PRE_UPDATE_DIR)
    n = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        if not PRE_UPDATE_NAME_RE.match(name):
            continue
        try:
            if pid in (read_manifest(os.path.join(d, name)).get("projects") or []):
                n += 1
        except BackupError:
            continue
    return n


def plan_disposal(project_root: str, *, keep_labels: bool = False, data_root: str | None = None,
                  backup_root: str | None = None, report_roots: Iterable[str] | None = None,
                  ignore_open_lock: bool = False, ai_roots: Iterable[str] | None = None) -> DisposalPlan:
    """'프로젝트 파기' 의 대상·경고 목록. 아무것도 지우지 않는다. 화면이 이것을 보여 주고 확인을 받은 뒤 execute_disposal.

    keep_labels=True('라벨만 남기기'): 프로젝트 폴더의 chat/ · derived/ · 보호 구역만 보내고 labels/ · samples/ · locks/ ·
    relabel/ · guide/ · project.json 은 남긴다. 백업·캐시·AI·보고서는 어느 쪽이든 보낸다(백업에 채팅 사본이 있다).
    locks/open.lock 이 있으면(프로젝트가 열려 있으면) ProjectBusyError — 닫고 다시. 비정상 종료로 남은 것이면 ignore_open_lock.
    """
    root = _abs(project_root)
    if not os.path.isdir(root) or _is_link(root):
        raise DisposalError("프로젝트 폴더가 아닙니다")
    proj = read_project_json(root)
    pid = proj.get("project_id")
    if not isinstance(pid, str) or not _PID_RE.match(pid):
        raise DisposalError("project.json 의 project_id 형식이 아닙니다")
    droot = _abs(data_root or paths.data_root())
    broot = _abs(backup_root or paths.backup_root())
    # 프로젝트 폴더가 앱·데이터·백업 폴더나 사용자 홈을 품으면 거부 (잘못 고른 상위 폴더)
    for guard in (droot, broot, paths.install_root(), os.path.expanduser("~")):
        if _under(guard, root):
            raise DisposalError("이 폴더는 프로젝트 폴더가 아닙니다(앱·데이터·홈 폴더를 품고 있음)")
    if os.path.splitdrive(root)[1].strip("\\/") == "":
        raise DisposalError("드라이브 최상위는 파기할 수 없습니다")
    if not ignore_open_lock and os.path.exists(os.path.join(root, "locks", "open.lock")):
        raise ProjectBusyError("프로젝트가 열려 있습니다. 닫은 뒤 다시 하세요")

    targets: list[DisposalTarget] = []

    def add(kind: str, p: str, base: str) -> None:
        if os.path.lexists(p) and _under(p, base) and os.path.normcase(_abs(p)) != os.path.normcase(_abs(base)):
            if all(os.path.normcase(t.path) != os.path.normcase(_abs(p)) for t in targets):
                targets.append(DisposalTarget(kind, _abs(p)))

    add("backups", os.path.join(broot, pid), broot)
    add("backups", os.path.join(droot, "backups", pid), droot)
    add("cache", os.path.join(droot, "cache", pid), droot)
    for fp16 in _media_fps(proj):
        add("media_cache", os.path.join(droot, "cache", fp16), droot)
    for ar_ in (ai_roots if ai_roots is not None else _default_ai_roots(root, droot)):
        ar_ = _abs(ar_)
        if _under(ar_, root):
            continue
        add("ai", os.path.join(ar_, pid), ar_)
    for rr in (report_roots if report_roots is not None else _default_report_roots(root, droot)):
        rr = _abs(rr)
        if _under(rr, root):
            continue
        add("reports", os.path.join(rr, pid), rr)
    if keep_labels:
        zone = protected_patterns(proj)
        add("project_chat", os.path.join(root, "chat"), root)
        add("project_derived", os.path.join(root, "derived"), root)
        try:
            names = sorted(os.listdir(root))
        except OSError:
            names = []
        for n in names:
            p = os.path.join(root, n)
            rel = n + ("/" if os.path.isdir(p) else "")
            if n.lower() in ("chat", "derived", PROJECT_FILE):
                continue
            if in_protected_zone(rel, zone) or in_protected_zone(n, zone):
                add("project_protected", p, root)
        # 남기는 폴더 안을 가리키는 보호 구역(project.json 의 protected_zone, 예: 'samples/identities/')도 보낸다.
        # 가장 위의 보호 폴더 하나만 대상으로 넣고, 그 아래 파일은 따로 넣지 않는다
        for n in names:
            if n.lower() in ("chat", "derived") or not os.path.isdir(os.path.join(root, n)):
                continue
            if any(_under(os.path.join(root, n), t.path) for t in targets):
                continue
            for rel in _walk_files(root, n):
                parts = rel.split("/")
                hit = None
                for i in range(1, len(parts)):
                    if in_protected_zone("/".join(parts[:i]) + "/", zone):
                        hit = os.path.join(root, *parts[:i])
                        break
                if hit is None and in_protected_zone(rel, zone):
                    hit = os.path.join(root, *parts)
                if hit is not None and not any(_under(hit, t.path) for t in targets):
                    add("project_protected", hit, root)
    else:
        targets.append(DisposalTarget("project", root))

    for t in targets:
        t.trash_problem = can_trash(t.path)
    warns = ["휴지통으로 보냅니다. 휴지통을 비워야 디스크에서 지워집니다.",
             "다른 PC·공유 드라이브·외부 라벨러에게 보낸 사본과 직접 옮긴 백업은 이 명령이 지우지 못합니다."]
    if any(paths.is_under_onedrive(t.path) or paths.looks_like_onedrive(t.path) for t in targets):
        warns.append("OneDrive 아래 대상이 있습니다. OneDrive 웹의 휴지통과 버전 기록에도 사본이 남으니 거기서도 지우세요.")
    bad = [t for t in targets if t.trash_problem]
    if bad:
        warns.append(f"휴지통으로 보낼 수 없는 대상이 {len(bad)}개 있습니다(네트워크·이동식·가상 드라이브, 긴 경로, "
                     f"휴지통이 꺼졌거나 대상보다 작음). 이 대상은 남깁니다. 직접 지우세요.")
    n_pre = _count_pre_update_with(pid, broot)
    if n_pre and not keep_labels:
        warns.append(f"업데이트 직전 백업 {n_pre}개에 이 프로젝트의 라벨이 들어 있습니다(다른 프로젝트와 한 파일이라 남깁니다). "
                     f"최근 {KEEP_PRE_UPDATE}개만 남고 자동으로 밀려납니다.")
    if keep_labels:
        warns.append("라벨만 남깁니다. 라벨의 근거·메모에 닉네임을 적었다면 그 내용은 남습니다.")
    return DisposalPlan(pid, root, bool(keep_labels), targets, warns, droot, n_pre)


def _iso(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def execute_disposal(plan: DisposalPlan, *, labeler_id: str, trash: TrashFn | None = None,
                     now: datetime | None = None, app_version: str | None = None) -> DisposalResult:
    """plan 의 대상을 휴지통으로 보내고 data\\disposal_log.jsonl 에 한 줄 남긴다.

    순서: chat_index.drop_cache → 백업·캐시·AI·보고서 → 프로젝트(마지막). 하나가 실패해도 나머지는 계속하고,
    실패는 result.failed 와 기록에 남는다. 휴지통으로 못 보내는 대상은 건드리지 않는다.
    """
    if not isinstance(labeler_id, str) or not _LABELER_RE.match(labeler_id):
        raise ValueError("labeler_id 형식이 아닙니다")
    trash = trash or send_to_trash
    now = _now(now)
    res = DisposalResult(plan.project_id)
    try:
        from . import chat_index
        res.cache_index_dropped = bool(chat_index.drop_cache(plan.project_id, plan.data_root))
    except (OSError, ValueError, ImportError, AttributeError) as e:
        LOG.warning("파기: 채팅 색인 캐시 지우기 실패 (%s)", type(e).__name__)
    order = sorted(plan.targets, key=lambda t: t.kind.startswith("project"))
    for t in order:
        if not os.path.lexists(t.path):
            continue
        if t.trash_problem:
            res.failed.append((t, t.trash_problem))
            continue
        try:
            trash(t.path)
            res.trashed.append(t)
        except TrashError as e:
            res.failed.append((t, e.reason))
        except OSError as e:
            res.failed.append((t, type(e).__name__))
    from .logging_setup import redact
    rec = {"schema": DISPOSAL_SCHEMA, "schema_version": 1, "at": _iso(now), "project_id": plan.project_id,
           "labeler_id": labeler_id, "mode": "keep_labels" if plan.keep_labels else "all", "method": "trash",
           "app_version": _app_version(app_version), "cache_index_dropped": res.cache_index_dropped,
           "trashed": [{"kind": t.kind, "path": redact(t.path)} for t in res.trashed],
           "failed": [{"kind": t.kind, "path": redact(t.path), "reason": r} for t, r in res.failed],
           "pre_update_backups_kept": plan.pre_update_backups}
    log_path = os.path.join(plan.data_root, DISPOSAL_LOG_NAME)
    try:
        from . import jsonl
        os.makedirs(plan.data_root, exist_ok=True)
        jsonl.append(log_path, rec)
        res.log_path = log_path
    except (OSError, ValueError) as e:
        LOG.error("파기 기록을 쓰지 못했습니다 (%s)", type(e).__name__)
    LOG.info("프로젝트 파기: %s (%s) 휴지통 %d건, 실패 %d건", plan.project_id,
             "라벨만 남김" if plan.keep_labels else "전부", len(res.trashed), len(res.failed))
    return res


def disposal_log(data_root: str | None = None) -> list[dict]:
    """data\\disposal_log.jsonl 의 기록 (오래된 것부터). 없으면 빈 목록."""
    from . import jsonl
    p = os.path.join(data_root or paths.data_root(), DISPOSAL_LOG_NAME)
    if not os.path.isfile(p):
        return []
    r = jsonl.read(p)
    return list(r.records)


__all__ = [
    "BackupError", "BackupLocationError", "TrashError", "DisposalError", "ProjectBusyError",
    "BackupResult", "BackupInfo", "DailyReport", "RestoreResult", "RetentionState",
    "DisposalTarget", "DisposalPlan", "DisposalResult",
    "KEEP_DAILY", "KEEP_PRE_UPDATE", "WEEKLY_CHAT_DAYS", "DEFAULT_PROTECTED_ZONE",
    "read_project_json", "project_id_of", "protected_patterns", "in_protected_zone", "select_files",
    "check_backup_dest", "default_backup_dir", "backup_project", "list_backups", "rotate_backups",
    "daily_backup_if_due", "copy_session_file", "pre_update_backup",
    "read_manifest", "verify_backup", "restore_backup",
    "retention_state", "retention_message",
    "can_trash", "send_to_trash",
    "plan_disposal", "execute_disposal", "disposal_log",
]
