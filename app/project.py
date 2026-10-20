"""프로젝트 폴더 만들기·열기·project.json 저장 (docs/design/03-data-model.md 2-1·6-1·8-3, 02 5-4, 05 S-09·S-29·S-49).

입출력 층이다. 검증·정규화 규칙은 app.core.validate, ID 규칙은 app.core.ids, 경로 판정은 app.paths 를 쓴다.

- create(): <root>\\<slug>\\ 를 만들고 하위 폴더, chat/protected/salt.json, guide/<버전>.md, project.json(rev 1)을 쓴다.
- open(): project.json 을 엄격 JSON 으로 읽고 검증한다. 실패해도 예외로 막지 않고 read_only + 사유 목록으로 연다.
- Project.update(mutate): 디스크 rev 가 읽은 rev 와 같을 때만 rev+1 로 임시 파일 → os.replace 저장. 다르면 ConflictError.
- 잠금(locks/open.lock, QLockFile)은 이 모듈이 잡지 않는다(03 13-5, ui 쪽 session 이 맡는다).
  그래서 rev 비교와 os.replace 사이의 아주 짧은 틈은 막지 못한다. 잠금이 1차 방어선이다.

채팅 수집(chzzk_chat.collect)은 project.root 와 project.salt() 를 받아 이어 쓴다.
"""
from __future__ import annotations

import base64
import builtins
import copy
import hashlib
import json
import os
import re
import socket
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from . import paths
from .core import ids
from .core.validate import (PROJECT_MAX_BYTES, PROJECT_SCHEMA_VERSION, StrictJsonError, canonical_json,
                            load_json_strict, validate_project)

PROJECT_FILE = "project.json"
SALT_MAX_BYTES = 4096
PATH_WARN_CHARS = 180               # 02 5-4 · S-49: 프로젝트 경로가 이보다 길면 경고
SLUG_TITLE_CHARS = 30
FORBIDDEN_CHARS = set('\\/:*?"<>|')
WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                              *(f"LPT{i}" for i in range(10)), "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"})
DEFAULT_PROTECTED_ZONE = ["chat/protected/", "derived/asr*", "derived/ocr*", "origin/"]
DEFAULT_PURPOSE = "분석 엔진 평가용 정답 라벨 작성(시범)"
DEFAULT_GUIDE_VERSION = "0.1"
SAVE_RETRY_DELAYS = (0.1, 0.3, 1.0, 3.0)   # 8-3: os.replace 실패 시 다시 시도하는 간격(초)
LABELER_RE = r"\A[A-Z][A-Z0-9]{1,7}\Z"


class ConflictError(RuntimeError):
    """디스크의 rev 가 읽었던 rev 와 다르다: 다른 창이나 PC 가 먼저 저장했다. reload() 뒤 다시 고쳐야 한다."""

    def __init__(self, read_rev, disk_rev):
        super().__init__(f"다른 창이나 PC 가 먼저 저장했습니다 (읽은 rev {read_rev}, 디스크 rev {disk_rev})")
        self.read_rev = read_rev
        self.disk_rev = disk_rev


class ReadOnlyError(RuntimeError):
    """읽기 전용으로 연 프로젝트에 저장하려 했다."""


# ---------------------------------------------------------------------------
# 이름·시각 도우미

def slugify(title: str, video_no) -> str:
    """프로젝트 폴더 이름: 제목 앞 30자(파일명 금지 문자·제어 문자·공백 → '_') + '-' + video_no.

    NFC 로 맞춘 뒤 자르고, 끝의 '.'·' '(Windows 가 지우는 문자)를 뗀다. 제목이 비면 '방송'.
    결과는 스키마 slug 패턴 ^[^\\\\/:*?"<>|]{1,30}-[0-9]{1,12}$ 을 늘 만족한다.
    """
    vno = str(video_no)
    ids.check_path_id(vno, ids.VIDEO_NO_RE, "video_no")
    t = unicodedata.normalize("NFC", title or "")
    chars = []
    for ch in t:
        if ch in FORBIDDEN_CHARS or ord(ch) < 32 or ord(ch) == 127 or ch.isspace() \
                or unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Cs"):
            ch = "_"
        if ch == "_" and chars and chars[-1] == "_":
            continue                    # 연속 치환은 하나로
        chars.append(ch)
    head = "".join(chars)[:SLUG_TITLE_CHARS].rstrip(" .")
    if not head.strip("_"):
        head = "방송"
    # Windows 예약 장치 이름(CON·NUL·COM1 …)이 첫 '.' 앞에 오면 폴더를 만들 수 없다 → 앞에 '_'
    # (마침표가 없으면 폴더 이름은 'aux-0' 꼴이라 예약 이름이 아니다)
    if "." in head and head.split(".", 1)[0].rstrip(" ").upper() in WINDOWS_RESERVED:
        head = ("_" + head)[:SLUG_TITLE_CHARS].rstrip(" .")
    return f"{head}-{vno}"


def machine4_of(machine_id: str) -> str:
    """machine4 = base32(SHA-256(machine_id)) 앞 4자, 소문자 (03 13-1)."""
    return base64.b32encode(hashlib.sha256(machine_id.encode("utf-8")).digest()).decode("ascii").lower()[:4]


def default_machine4() -> str:
    """data\\machine.json 의 machine_id 가 있으면 그것으로, 없으면 호스트 이름으로 machine4 를 만든다.

    machine.json 을 만드는 것은 session 모듈의 일이라 여기서는 읽기만 한다(없으면 만들지 않는다).
    """
    try:
        p = os.path.join(paths.data_root(), "machine.json")
        with builtins.open(p, "rb") as f:
            mid = load_json_strict(f.read(), 4096).get("machine_id")
        if isinstance(mid, str) and mid:
            return machine4_of(mid)
    except (OSError, StrictJsonError):
        pass
    return machine4_of("host:" + socket.gethostname().lower())


def _aware(now: datetime | None) -> datetime:
    """OS 시간대 오프셋을 붙인 시각. 시간대가 없으면 OS 오프셋으로 해석한다(+09:00 을 박지 않는다)."""
    if now is None:
        return datetime.now().astimezone()
    return now if now.tzinfo is not None else now.astimezone()


def iso(now: datetime) -> str:
    return _aware(now).isoformat(timespec="seconds")


def _plus_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError("날짜 계산 실패")


# ---------------------------------------------------------------------------
# 경로

@dataclass(frozen=True)
class ProjectPaths:
    """프로젝트 폴더 안 경로 (03 2-1). 다른 모듈은 여기서 경로를 얻는다."""
    root: str

    def _j(self, *p) -> str:
        return os.path.join(self.root, *p)

    @property
    def project_json(self) -> str: return self._j(PROJECT_FILE)
    @property
    def guide_dir(self) -> str: return self._j("guide")
    @property
    def chat_dir(self) -> str: return self._j("chat")
    @property
    def messages_jsonl(self) -> str: return self._j("chat", "messages.jsonl")
    @property
    def protected_dir(self) -> str: return self._j("chat", "protected")
    @property
    def salt_json(self) -> str: return self._j("chat", "protected", "salt.json")
    @property
    def identities_jsonl(self) -> str: return self._j("chat", "protected", "identities.jsonl")
    @property
    def labels_dir(self) -> str: return self._j("labels")
    @property
    def samples_dir(self) -> str: return self._j("samples")
    @property
    def locks_dir(self) -> str: return self._j("locks")
    @property
    def open_lock(self) -> str: return self._j("locks", "open.lock")
    @property
    def derived_dir(self) -> str: return self._j("derived")

    def fetch_json(self, fetch_id: str) -> str:
        return self._j("chat", f"fetch-{ids.check_path_id(fetch_id, ids.FETCH_ID_RE, 'fetch_id')}.json")

    def pages_gz(self, fetch_id: str) -> str:
        return self._j("chat", "protected", f"pages-{ids.check_path_id(fetch_id, ids.FETCH_ID_RE, 'fetch_id')}.jsonl.gz")

    def created_dirs(self) -> list[str]:
        return [self.guide_dir, self.chat_dir, self.protected_dir, self.labels_dir,
                self.samples_dir, self.locks_dir, self.derived_dir]


def storage_kind(path: str) -> str | None:
    """경고 대상 위치 종류: 'unc' | 'onedrive' | None. (storage_warning_acknowledged.path_kind 에 쓴다)"""
    if paths.is_network_path(path):
        return "unc"
    if paths.is_under_onedrive(path):
        return "onedrive"
    return None


def path_warnings(root: str) -> list[str]:
    """막지 않는 경고 목록 (S-09·S-49)."""
    out = []
    a = os.path.abspath(root)
    if len(a) > PATH_WARN_CHARS:
        out.append(f"프로젝트 경로가 {len(a)}자로 {PATH_WARN_CHARS}자를 넘습니다. 저장이 실패할 수 있습니다(긴 경로).")
    k = storage_kind(root)
    if k == "unc":
        out.append("프로젝트가 네트워크(UNC) 경로 아래에 있습니다. 동시 저장·잠금이 믿을 만하지 않습니다.")
    elif k == "onedrive":
        out.append("프로젝트가 OneDrive 폴더 아래에 있습니다. 동기화 중 충돌·보호 구역 업로드가 생길 수 있습니다. 이동을 권장합니다.")
    return out


# ---------------------------------------------------------------------------
# 파일 쓰기

def _fsync_dir(d: str) -> None:
    # Windows 는 폴더 fsync 를 지원하지 않는다. 다른 OS 에서만 시도한다.
    if os.name == "nt":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: str, data: bytes, *, retry_delays=SAVE_RETRY_DELAYS, sleep=time.sleep) -> None:
    """임시 파일에 다 쓰고 fsync → os.replace. replace 가 PermissionError 등으로 실패하면 간격을 두고 다시 시도.
    끝내 실패하면 마지막 OSError 를 그대로 올린다(조용히 False 를 돌려주지 않는다, 8-3)."""
    d = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    with builtins.open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    last: OSError | None = None
    try:
        for delay in (0.0,) + tuple(retry_delays):
            if delay:
                sleep(delay)
            try:
                os.replace(tmp, path)
                _fsync_dir(d)
                return
            except OSError as e:
                last = e
        assert last is not None
        raise last
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _dump_project(obj: dict) -> bytes:
    """project.json 바이트: 사람이 읽기 쉬운 들여쓰기, 한글 그대로, NaN 금지, 끝 LF."""
    return (json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# 기준서

@dataclass
class GuideSource:
    """프로젝트에 붙일 기준서 한 판. doc_bytes 는 guide/<version>.md 로 그대로 복사된다."""
    version: str
    doc_bytes: bytes
    thresholds: dict
    reason: str | None = None


def default_guide() -> tuple[GuideSource, list[str]]:
    """기본 기준서 v0.1: docs/labeling-guide/v0.1.md 와 assets/guide-config.v0.1.json 의 values.

    시작값 파일이 아직 없으면 thresholds 를 빈 객체로 두고 경고를 돌려준다(project.json 의 x 에도 표시).
    """
    warns: list[str] = []
    base = paths.code_dir()
    doc = os.path.join(base, "docs", "labeling-guide", f"v{DEFAULT_GUIDE_VERSION}.md")
    with builtins.open(doc, "rb") as f:
        doc_bytes = f.read()
    cfg = os.path.join(base, "assets", f"guide-config.v{DEFAULT_GUIDE_VERSION}.json")
    thresholds: dict = {}
    try:
        with builtins.open(cfg, "rb") as f:
            c = load_json_strict(f.read(), 1024 * 1024)
        v = c.get("values")
        if isinstance(v, dict):
            thresholds = v
        else:
            warns.append("시작값 파일에 values 가 없습니다. thresholds 를 빈 객체로 넣었습니다.")
    except FileNotFoundError:
        warns.append("시작값 파일(assets/guide-config.v0.1.json)이 없습니다. thresholds 를 빈 객체로 넣었습니다.")
    return GuideSource(DEFAULT_GUIDE_VERSION, doc_bytes, thresholds), warns


def guide_entry(g: GuideSource, now: datetime, by: str) -> dict:
    return {
        "guide_version": g.version,
        "doc_path": f"guide/v{g.version}.md",
        "doc_sha256": hashlib.sha256(g.doc_bytes).hexdigest(),
        "thresholds": g.thresholds,
        "thresholds_sha256": hashlib.sha256(canonical_json(g.thresholds)).hexdigest(),
        "added_at": iso(now),
        "added_by": by,
        "reason": g.reason,
    }


# ---------------------------------------------------------------------------
# Project

@dataclass
class Project:
    root: str
    data: dict | None                      # project.json 내용 (읽기 실패면 None)
    read_only: bool = False
    problems: list[str] = field(default_factory=list)   # 읽기 전용 사유
    warnings: list[str] = field(default_factory=list)   # 막지 않는 경고 (경로·솔트 없음 등)
    machine4: str | None = None

    @property
    def paths(self) -> ProjectPaths:
        return ProjectPaths(self.root)

    @property
    def rev(self) -> int | None:
        return self.data.get("rev") if self.data else None

    @property
    def project_id(self) -> str | None:
        return self.data.get("project_id") if self.data else None

    @property
    def storage_kind(self) -> str | None:
        return storage_kind(self.root)

    def salt(self) -> bytes:
        """chat/protected/salt.json 의 솔트 32바이트. project.json 의 salt_id 와 다르면 ValueError.
        파일이 없으면 FileNotFoundError(라벨러 패키지처럼 보호 구역이 빠진 경우)."""
        with builtins.open(self.paths.salt_json, "rb") as f:
            s = load_json_strict(f.read(), SALT_MAX_BYTES)
        hx = s.get("salt_hex")
        if not isinstance(hx, str) or len(hx) != 64 or any(c not in "0123456789abcdef" for c in hx):
            raise ValueError("salt.json 의 salt_hex 형식이 아닙니다")
        salt = bytes.fromhex(hx)
        want = ((self.data or {}).get("pseudonymization") or {}).get("salt_id")
        if want is not None and ids.salt_id(salt) != want:
            raise ValueError("salt.json 이 project.json 의 salt_id 와 다릅니다")
        return salt

    def reload(self) -> "Project":
        """디스크에서 다시 읽어 이 객체를 바꾼다."""
        p = open_project(self.root, machine4=self.machine4)
        self.data, self.read_only, self.problems, self.warnings = p.data, p.read_only, p.problems, p.warnings
        return self

    def update(self, mutate: Callable[[dict], None], *, now: datetime | None = None,
               by: str | None = None) -> dict:
        """mutate(사본) 로 고친 뒤 rev+1 로 저장하고 새 내용을 돌려준다.

        - 디스크 rev 가 이 객체가 읽은 rev 와 다르면 ConflictError (저장하지 않음).
        - schema·schema_version·project_id·slug·created_* 는 바꿀 수 없다(ValueError).
        - 고친 결과가 검증을 통과하지 않으면 ValueError (저장하지 않음).
        - by 는 라벨러 ID. 없으면 직전 modified_by 의 라벨러를 쓴다. modified_by = '<by>@<machine4>'.
        - 직전 project.json 은 project.json.bak 으로 남긴다(8-3).
        """
        if self.read_only or self.data is None:
            raise ReadOnlyError("읽기 전용으로 연 프로젝트입니다: " + "; ".join(self.problems[:3]))
        pj = self.paths.project_json
        with builtins.open(pj, "rb") as f:
            disk_bytes = f.read()
        disk = load_json_strict(disk_bytes, PROJECT_MAX_BYTES)
        if disk.get("rev") != self.data.get("rev"):
            raise ConflictError(self.data.get("rev"), disk.get("rev"))
        new = copy.deepcopy(disk)
        mutate(new)
        if not isinstance(new, dict):
            raise ValueError("mutate 가 dict 를 망가뜨렸습니다")
        for k in ("schema", "schema_version", "project_id", "slug", "created_at", "created_by", "created_app_version"):
            if new.get(k) != disk.get(k):
                raise ValueError(f"{k} 는 바꿀 수 없습니다")
        who = by or str(disk.get("modified_by", "")).split("@", 1)[0]
        m4 = self.machine4 or default_machine4()
        new["rev"] = int(disk["rev"]) + 1
        new["modified_at"] = iso(_aware(now))
        new["modified_by"] = f"{who}@{m4}"
        problems = validate_project(new)
        if problems:
            raise ValueError("저장할 내용이 검증을 통과하지 않습니다: " + "; ".join(problems[:5]))
        atomic_write(pj + ".bak", disk_bytes)
        atomic_write(pj, _dump_project(new))
        self.data = new
        return copy.deepcopy(new)


def open_project(path: str, *, machine4: str | None = None) -> Project:
    """프로젝트 폴더(또는 그 안의 project.json)를 연다. 예외로 막지 않고 문제가 있으면 read_only=True.

    읽기 전용 사유: 파일 없음·읽기 실패, 엄격 JSON 위반, schema_version 이 새 형식, 스키마 검증 실패, 솔트 불일치.
    경고(막지 않음): 경로 180자 초과, OneDrive·UNC 아래, 폴더 이름 ≠ slug, salt.json 없음.
    """
    root = os.path.abspath(path)
    if os.path.basename(root).lower() == PROJECT_FILE:
        root = os.path.dirname(root)
    proj = Project(root=root, data=None, machine4=machine4)
    proj.warnings = path_warnings(root)
    try:
        with builtins.open(proj.paths.project_json, "rb") as f:
            raw = f.read(PROJECT_MAX_BYTES + 1)
        proj.data = load_json_strict(raw, PROJECT_MAX_BYTES)
    except OSError as e:
        proj.read_only = True
        proj.problems.append(f"project.json 을 읽지 못했습니다: {type(e).__name__}")
        return proj
    except StrictJsonError as e:
        proj.read_only = True
        proj.problems.append(f"project.json 이 엄격 JSON 규칙에 맞지 않습니다: {e}")
        return proj
    problems = validate_project(proj.data)
    if problems:
        proj.read_only = True
        proj.problems += problems
        return proj
    if proj.data["slug"] != os.path.basename(root):
        proj.warnings.append("폴더 이름이 project.json 의 slug 와 다릅니다(옮기거나 이름을 바꾼 것으로 보입니다).")
    if not os.path.exists(proj.paths.salt_json):
        proj.warnings.append("chat/protected/salt.json 이 없습니다. 채팅 수집·가명화를 할 수 없습니다.")
    else:
        try:
            proj.salt()
        except (OSError, ValueError) as e:
            proj.read_only = True
            proj.problems.append(f"솔트 확인 실패: {e}")
    return proj


def create(root: str, title: str, video_no, purpose: str = DEFAULT_PURPOSE, labeler_id: str = "L01",
           created_app_version: str | None = None, now: datetime | None = None,
           project_id: str | None = None, salt: bytes | None = None, *,
           sample_seed: str | None = None, guide: GuideSource | None = None,
           engine_author: bool | None = None, machine4: str | None = None,
           duration_ms: int = 0, nickname_mask: str = "first_char") -> Project:
    """<root>\\<slug>\\ 에 새 프로젝트를 만들고 open_project() 결과를 돌려준다.

    모르는 값은 스키마가 허용하는 빈 값으로: broadcast.duration_ms 0·channel_* null, media 미지정(media_rev 0,
    current null), chat null, calibration rev 0 (오프셋 0, unverified). 영상 정보 API·ffprobe 값은
    다른 모듈이 update() 로 채운다.
    labelers[] 는 engine_author 를 알 때(True/False)만 만든 사람을 primary 로 넣는다. 모르면 빈 목록(명부에 넣을 때 묻는다).
    now·project_id·salt·sample_seed 를 주지 않으면 여기서 만든다(입출력 층이라 시계·난수를 써도 된다).
    이미 폴더가 있으면 FileExistsError — 덮어쓰지 않는다.
    """
    if not re.match(LABELER_RE, labeler_id or ""):
        raise ValueError(f"labeler_id 형식이 아닙니다: {labeler_id!r}")
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 500:
        raise ValueError("purpose 는 1~500자여야 합니다")
    if created_app_version is None:
        from . import __version__ as created_app_version  # noqa: N811
    vno = str(video_no)
    slug = slugify(title, vno)
    now = _aware(now)
    project_id = project_id or str(uuid.uuid4())
    salt = os.urandom(32) if salt is None else salt
    if not isinstance(salt, (bytes, bytearray)) or len(salt) != 32:
        raise ValueError("솔트는 32바이트여야 합니다")
    sample_seed = sample_seed or os.urandom(16).hex()
    m4 = machine4 or default_machine4()
    warns: list[str] = []
    if guide is None:
        guide, gw = default_guide()
        warns += gw

    labelers = []
    if engine_author is not None:
        labelers.append({"labeler_id": labeler_id, "role": "primary",
                         "assigned_session_kinds": ["primary"], "engine_author": bool(engine_author)})
    ts = iso(now)
    data = {
        "schema": "chatcoach/project",
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_id": project_id,
        "rev": 1,
        "slug": slug,
        "created_at": ts,
        "created_by": labeler_id,
        "created_app_version": created_app_version,
        "modified_at": ts,
        "modified_by": f"{labeler_id}@{m4}",
        "purpose": purpose,
        "retention_until": _plus_months(now.date(), 12).isoformat(),
        "broadcast": {
            "platform": "chzzk", "video_no": vno, "channel_id": None, "channel_name": None,
            "title": unicodedata.normalize("NFC", title or "")[:300], "duration_ms": int(duration_ms),
            "live_open_at": None, "publish_at": None, "adult": None,
        },
        "media": {"media_rev": 0, "current": None, "history": []},
        "chat": None,
        "pseudonymization": {"method": "hmac-sha256-v1", "salt_id": ids.salt_id(bytes(salt)),
                             "nickname_mask": nickname_mask},
        "guides": [guide_entry(guide, now, labeler_id)],
        "active_guide_version": guide.version,
        "calibration": {"active_rev": 0, "revs": [{
            "rev": 0, "media_rev": 0, "method": "single_offset", "offset_ms": 0, "anchors": [],
            "residual_max_ms": None, "status": "unverified", "skip_reason": None,
            "broadcast_delay_ms": None, "delay_anchors": [], "created_at": ts, "created_by": labeler_id,
        }]},
        "sample_seed": sample_seed,
        "dataset_split": "dev",
        "labelers": labelers,
        "protected_zone": list(DEFAULT_PROTECTED_ZONE),
        "storage_warning_acknowledged": None,
    }
    if not guide.thresholds:
        data["x"] = {"guide_thresholds_missing": True}
    problems = validate_project(data)
    if problems:
        raise ValueError("새 project.json 이 검증을 통과하지 않습니다: " + "; ".join(problems[:5]))

    folder = os.path.join(os.path.abspath(root), slug)
    os.makedirs(os.path.abspath(root), exist_ok=True)
    os.mkdir(folder)                    # 이미 있으면 FileExistsError
    pp = ProjectPaths(folder)
    for d in pp.created_dirs():
        os.makedirs(d, exist_ok=True)
    atomic_write(os.path.join(folder, data["guides"][0]["doc_path"].replace("/", os.sep)), guide.doc_bytes)
    salt_doc = {"salt_hex": bytes(salt).hex(), "created_at": ts}
    atomic_write(pp.salt_json, _dump_project(salt_doc))
    atomic_write(pp.project_json, _dump_project(data))   # 마지막에 쓴다: project.json 이 있으면 완성된 폴더
    proj = open_project(folder, machine4=m4)
    proj.warnings = warns + proj.warnings
    return proj


# 과제 명세의 이름 open(path). 이 모듈 안의 파일 열기는 모두 builtins.open 을 쓰므로 내장 open 을 가려도 안전하다.
open = open_project  # noqa: A001
