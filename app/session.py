"""세션(회차) 정보, 이 PC 의 기계 ID, 라벨러 명부, 세션 고르기 계산 (docs/design/03-data-model.md 5-9·6-4·13-1·13-2,
02 3-2·7-2, 08 D-09 1·4·5).

입출력 층이다. Qt 를 import 하지 않는다(QLockFile 같은 잠금은 store 가 주입받는다, D-09 8).

- SessionContext: 한 세션을 여는 데 필요한 값 묶음. store.ProjectStore.open 이 이것으로 기록 파일 이름과
  session.open 첫 줄을 정한다. 세션 ID·라벨러·machine4 형식은 만들 때 검사한다(파일 이름이 되므로, S-46).
- machine_id(data_root): <data>\\machine.json 의 UUID. 없으면 만들고, 깨졌으면 깨진 파일을 보존한 뒤 새로 만든다.
  (machine_id 는 파일 이름을 나누는 데만 쓰여서, 바뀌면 같은 세션의 새 파일이 하나 더 생길 뿐 기록은 잃지 않는다.)
- machine4(machine_id): base32(SHA-256(machine_id)) 앞 4자 소문자. app.project.machine4_of 와 같은 값.
- new_session_id(labeler_id, kind, date, rand4): '<labeler_id>-<P·RS·R2·AD·OR·AN>-<yyyymmdd>-<4자>'.
- new_session(...): 위 셋을 합쳐 새 SessionContext 를 만든다. 시계·난수는 인자로 바꿀 수 있다(테스트).
- resume_session(...): 이미 있는 세션(같은 session_id)을 이 PC 에서 이어 쓰는 SessionContext.
- session_file_name / parse_session_file_name: 'labels/<session_id>.<machine4>.jsonl' 규칙(13-2).
- 라벨러 명부 <data>\\labelers.json (load_labelers·add_labeler): 표시 이름(실명일 수 있음)은 이 PC 에만 둔다.
  프로젝트·라벨·로그에는 labeler_id 만 쓴다(S-24·S-37).
- 세션 고르기 계산 (survey_project·kind_choices): 세션 선택 화면(app/ui/session_dialog.py)이 쓴다.
  labels/·locks/·relabel/·origin/·derived/ 의 **파일 이름만** 본다. 다른 세션 기록의 내용은 읽지 않는다(03 12-1).
  읽는 내용은 고른 라벨러 자기 세션 파일의 첫 줄(session.open, 기준서 버전 표시용)뿐이다.
"""
from __future__ import annotations

import builtins
import glob
import os
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Iterable

from .core import visibility as vis
from .core.ids import SESSION_FILE_RE, SESSION_ID_RE
from .core.validate import StrictJsonError, load_json_strict
from .project import _dump_project, atomic_write, iso, machine4_of

MACHINE_FILE = "machine.json"
MACHINE_MAX_BYTES = 4096
LABELERS_FILE = "labelers.json"
LABELERS_MAX_BYTES = 256 * 1024
LABELERS_V = 1
DISPLAY_NAME_MAX = 40
FIRST_LINE_MAX_BYTES = 1 << 20

# 03 5-9 세션 종류와 코드 (analysis = AN, 08 D-09 4)
KIND_CODES: dict[str, str] = {
    "primary": "P",
    "relabel_same": "RS",
    "relabel_second": "R2",
    "adjudication": "AD",
    "origin_review": "OR",
    "analysis": "AN",
}
CODE_KINDS: dict[str, str] = {v: k for k, v in KIND_CODES.items()}
# 세션 선택 화면에 보이는 이름 (02 7-2 '라벨러·세션 종류')
KIND_LABELS: dict[str, str] = {
    "primary": "1차 라벨링",
    "relabel_same": "같은 사람 재라벨",
    "relabel_second": "두 번째 사람 독립 라벨",
    "adjudication": "조정 (검토자)",
    "origin_review": "채팅 출처 판정 (검토자)",
    "analysis": "분석 보기 (엔진 결과 · 잠긴 1차 라벨)",
}
KIND_ORDER = ("primary", "relabel_same", "relabel_second", "adjudication", "origin_review", "analysis")

# label-event.schema.json 의 $defs 와 같은 정규식 (\Z: 끝 줄바꿈 허용하지 않음). 세션 ID 는 core.ids 가 원본이다.
LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
MACHINE4_RE = re.compile(r"\A[a-z2-7]{4}\Z")
RAND4_RE = re.compile(r"\A[a-z0-9]{4}\Z")
UUID4_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
LOCK_FILE_RE = re.compile(r"\A(" + SESSION_ID_RE.pattern[2:-2] + r")\.json\Z")   # locks/<session_id>.json (6-14)
RELABEL_PLAN_RE = re.compile(r"\ARP-[0-9]{8}-[a-z0-9]{4}\.json\Z")
RAND_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

DEFAULT_BLIND = {"to_ai": True, "to_other_sessions": True, "to_origin": True, "nickname_masked_default": True}

__all__ = [
    "KIND_CODES", "CODE_KINDS", "KIND_LABELS", "KIND_ORDER", "LABELER_RE", "SESSION_ID_RE", "SESSION_FILE_RE",
    "DEFAULT_BLIND", "SessionContext", "machine4", "machine_id", "new_session_id", "random4", "new_session",
    "resume_session", "default_blind", "session_file_name", "parse_session_file_name",
    "LabelerEntry", "LabelerRoster", "load_labelers", "add_labeler", "check_labeler_id", "clean_display_name",
    "ExistingSession", "ProjectSurvey", "KindChoice", "survey_project", "kind_choices", "exposure_notes",
]


def machine4(machine_id_value: str) -> str:
    """machine4 = base32(SHA-256(machine_id)) 앞 4자, 소문자 (03 13-1)."""
    return machine4_of(machine_id_value)


def machine_id(data_root: str, *, uuid_factory: Callable[[], str] | None = None,
               now: datetime | None = None) -> str:
    """<data_root>\\machine.json 의 machine_id(UUID v4 소문자). 없으면 만들어 저장하고 돌려준다.

    파일이 깨졌거나 machine_id 형식이 아니면 원본을 machine.json.corrupt-<시각> 으로 옮겨 두고 새로 만든다.
    """
    p = os.path.join(data_root, MACHINE_FILE)
    try:
        with builtins.open(p, "rb") as f:
            raw = f.read(MACHINE_MAX_BYTES + 1)
    except FileNotFoundError:
        raw = None
    if raw is not None:
        try:
            mid = load_json_strict(raw, MACHINE_MAX_BYTES).get("machine_id")
            if isinstance(mid, str) and UUID4_RE.match(mid):
                return mid
        except StrictJsonError:
            pass
        _preserve_corrupt(p, now)
    mid = (uuid_factory or (lambda: str(uuid.uuid4())))()
    if not UUID4_RE.match(mid):
        raise ValueError("machine_id 는 UUID v4 소문자여야 합니다")
    os.makedirs(data_root, exist_ok=True)
    atomic_write(p, _dump_project({"machine_id": mid, "created_at": iso(now or datetime.now().astimezone())}))
    return mid


def _preserve_corrupt(p: str, now: datetime | None) -> str | None:
    """깨진 파일을 <파일>.corrupt-<시각> 으로 옮겨 둔다(지우지 않는다). 옮긴 이름, 실패하면 None."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    dest = f"{p}.corrupt-{stamp}"
    try:
        os.replace(p, dest)
        return dest
    except OSError:
        return None


def _yyyymmdd(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y%m%d")
    s = str(d)
    if not re.fullmatch(r"[0-9]{8}", s):
        raise ValueError(f"날짜는 yyyymmdd 여야 합니다: {s!r}")
    return s


def check_labeler_id(labeler_id) -> str:
    """labeler_id 형식(영문 대문자로 시작, 대문자·숫자 2~8자) 검사. 맞으면 그대로, 아니면 ValueError."""
    if not isinstance(labeler_id, str) or not LABELER_RE.match(labeler_id):
        raise ValueError(f"labeler_id 형식이 아닙니다: {labeler_id!r}")
    return labeler_id


def new_session_id(labeler_id: str, kind: str, d, rand4: str) -> str:
    """'<labeler_id>-<종류 코드>-<yyyymmdd>-<4자>'. kind 는 'primary' 같은 세션 종류나 'P' 같은 코드.

    날짜는 사람이 보기 위한 것이다(순서 판단에 쓰지 않는다, 13-1). 형식이 맞지 않으면 ValueError.
    """
    code = KIND_CODES.get(kind, kind)
    if code not in CODE_KINDS:
        raise ValueError(f"모르는 세션 종류입니다: {kind!r}")
    check_labeler_id(labeler_id)
    if not isinstance(rand4, str) or not RAND4_RE.match(rand4):
        raise ValueError(f"무작위 4자 형식이 아닙니다: {rand4!r}")
    sid = f"{labeler_id}-{code}-{_yyyymmdd(d)}-{rand4}"
    if not SESSION_ID_RE.match(sid):
        raise ValueError(f"session_id 형식이 아닙니다: {sid!r}")
    return sid


def random4(rng: Callable[[int], int] | None = None) -> str:
    """세션 ID 끝 무작위 4자 [a-z0-9]. rng(n) 은 0..n-1 정수를 준다(기본 secrets.randbelow)."""
    pick = rng or secrets.randbelow
    return "".join(RAND_ALPHABET[pick(len(RAND_ALPHABET))] for _ in range(4))


def session_file_name(session_id: str, m4: str) -> str:
    """기록 파일 이름 '<session_id>.<machine4>.jsonl' (13-2). 둘 다 정규식 검사를 통과해야 한다."""
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError(f"session_id 형식이 아닙니다: {session_id!r}")
    if not isinstance(m4, str) or not MACHINE4_RE.match(m4):
        raise ValueError(f"machine4 형식이 아닙니다: {m4!r}")
    return f"{session_id}.{m4}.jsonl"


def parse_session_file_name(name: str) -> tuple[str, str] | None:
    """규칙에 맞는 기록 파일 이름이면 (session_id, machine4), 아니면 None(충돌 사본 등, 13-4)."""
    m = SESSION_FILE_RE.match(name or "")
    return (m.group(1), m.group(2)) if m else None


def default_blind(session_kind: str) -> dict:
    """세션이 시작할 때의 블라인드 상태(session.open.blind, 03 6-4). app.core.visibility 표에서 끌어낸다.

    primary·relabel_* 는 모두 가림(DEFAULT_BLIND 와 같음). 분석 세션은 잠긴 1차 라벨·출처를 읽으므로
    to_other_sessions·to_origin 이 False 다. 봉인 AI 결과는 어느 세션도 기본으로 열지 않는다(to_ai True).
    """
    sc = vis.files_to_open(session_kind)
    return {
        "to_ai": not sc.can_read("ai_sealed"),
        "to_other_sessions": sc.other_labels == vis.NONE,
        "to_origin": not sc.can_read("origin"),
        "nickname_masked_default": True,
    }


@dataclass(frozen=True)
class SessionContext:
    """세션 하나를 열 때의 값. 앞 네 칸이 공통 계약이고 나머지는 session.open 첫 줄에 들어가는 선택 값이다.

    - machine_id: 이 PC 의 UUID. 주면 machine4 와 맞는지 검사한다. 없으면 store 가 data\\machine.json 에서 읽는다.
    - session_created_at: 세션을 처음 만든 시각(ISO, 오프셋 포함). 이어 쓰는 세션이면 store 가 첫 줄의 값을 쓴다.
    - guide_version: 묶을 기준서 버전. None 이면 project.json 의 active_guide_version.
    - scope / relabel_plan_id / blind / tool / tool_models: 03 6-4·12-2. None 이면 기본값(전체 범위, 모두 가림).
    """
    labeler_id: str
    session_kind: str
    session_id: str
    machine4: str
    machine_id: str | None = None
    session_created_at: str | None = None
    guide_version: str | None = None
    scope: dict | None = None
    relabel_plan_id: str | None = None
    blind: dict | None = None
    tool: dict | None = None
    tool_models: dict | None = None
    extra: dict = field(default_factory=dict)   # 앞으로 늘 칸(지금은 쓰지 않음)

    def __post_init__(self):
        if not isinstance(self.labeler_id, str) or not LABELER_RE.match(self.labeler_id):
            raise ValueError(f"labeler_id 형식이 아닙니다: {self.labeler_id!r}")
        if self.session_kind not in KIND_CODES:
            raise ValueError(f"모르는 세션 종류입니다: {self.session_kind!r}")
        m = SESSION_ID_RE.match(self.session_id) if isinstance(self.session_id, str) else None
        if not m:
            raise ValueError(f"session_id 형식이 아닙니다: {self.session_id!r}")
        if m.group(1) != self.labeler_id:
            raise ValueError("session_id 의 라벨러가 labeler_id 와 다릅니다")
        if m.group(2) != KIND_CODES[self.session_kind]:
            raise ValueError("session_id 의 종류 코드가 session_kind 와 다릅니다")
        if not isinstance(self.machine4, str) or not MACHINE4_RE.match(self.machine4):
            raise ValueError(f"machine4 형식이 아닙니다: {self.machine4!r}")
        if self.machine_id is not None:
            if not isinstance(self.machine_id, str) or not UUID4_RE.match(self.machine_id):
                raise ValueError("machine_id 는 UUID v4 소문자여야 합니다")
            if machine4(self.machine_id) != self.machine4:
                raise ValueError("machine4 가 machine_id 에서 나온 값이 아닙니다")

    @property
    def kind_code(self) -> str:
        return KIND_CODES[self.session_kind]

    @property
    def file_name(self) -> str:
        return session_file_name(self.session_id, self.machine4)


def _machine_id_value(data_root: str | None, machine_id_value: str | None) -> str:
    if machine_id_value is not None:
        return machine_id_value
    if data_root is None:
        from . import paths
        data_root = paths.data_root()
    return machine_id(data_root)


def new_session(labeler_id: str, session_kind: str = "primary", *, data_root: str | None = None,
                machine_id_value: str | None = None, now: datetime | None = None,
                rand4: str | None = None, guide_version: str | None = None, scope: dict | None = None,
                relabel_plan_id: str | None = None, blind: dict | None = None, tool: dict | None = None,
                tool_models: dict | None = None) -> SessionContext:
    """새 세션. machine_id_value 가 없으면 data_root(없으면 app.paths.data_root())의 machine.json 에서 얻는다.

    now 는 OS 시간대가 붙은 시각(없으면 지금). 날짜 부분이 session_id 에, 전체가 session_created_at 에 들어간다.
    blind 를 주지 않으면 primary·relabel_* 는 None(store 가 DEFAULT_BLIND), 그 밖의 종류는 default_blind(종류).
    """
    mid = _machine_id_value(data_root, machine_id_value)
    t = now if now is not None else datetime.now().astimezone()
    if t.tzinfo is None:
        t = t.astimezone()
    sid = new_session_id(labeler_id, session_kind, t.date(), rand4 or random4())
    if blind is None and session_kind not in vis.LABELING_SESSIONS:
        blind = default_blind(session_kind)
    return SessionContext(
        labeler_id=labeler_id, session_kind=session_kind, session_id=sid,
        machine4=machine4(mid), machine_id=mid,
        session_created_at=t.isoformat(timespec="seconds"), guide_version=guide_version,
        scope=scope, relabel_plan_id=relabel_plan_id, blind=blind, tool=tool, tool_models=tool_models,
    )


def resume_session(session_id: str, *, data_root: str | None = None,
                   machine_id_value: str | None = None) -> SessionContext:
    """이미 있는 세션을 이 PC 에서 이어 쓴다. 라벨러·종류는 session_id 에서 얻는다.

    session.open 메타(만든 시각·기준서 버전·scope·blind)는 store 가 기존 기록 파일 첫 줄에서 그대로 가져온다.
    이 PC 의 파일이 아직 없으면(다른 PC 에서 시작한 세션) store 가 같은 메타로 이 PC 파일의 첫 줄을 쓴다.
    """
    m = SESSION_ID_RE.match(session_id) if isinstance(session_id, str) else None
    if not m:
        raise ValueError(f"session_id 형식이 아닙니다: {session_id!r}")
    mid = _machine_id_value(data_root, machine_id_value)
    return SessionContext(labeler_id=m.group(1), session_kind=CODE_KINDS[m.group(2)], session_id=session_id,
                          machine4=machine4(mid), machine_id=mid)


# ---------------------------------------------------------------------------
# 라벨러 명부 <data>\labelers.json (이 PC 에만, 03 2-2)

@dataclass(frozen=True)
class LabelerEntry:
    labeler_id: str
    display_name: str | None = None      # 실명일 수 있다. 화면에만 쓰고 로그·프로젝트에 넣지 않는다
    added_at: str | None = None

    @property
    def label(self) -> str:
        return f"{self.labeler_id} ({self.display_name})" if self.display_name else self.labeler_id


@dataclass
class LabelerRoster:
    entries: list[LabelerEntry] = field(default_factory=list)
    problem: str | None = None           # 파일이 깨졌을 때의 사유(본문 없음). 깨진 파일은 다음 저장 때 보존된다

    @property
    def ids(self) -> list[str]:
        return [e.labeler_id for e in self.entries]

    def get(self, labeler_id: str) -> LabelerEntry | None:
        return next((e for e in self.entries if e.labeler_id == labeler_id), None)


_CTRL_RE = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")


def clean_display_name(name) -> str | None:
    """표시 이름 정리: 제어·방향 바꿈 문자 제거, 앞뒤 공백 제거, 40자 상한. 비면 None."""
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("표시 이름은 문자열이어야 합니다")
    s = " ".join(_CTRL_RE.sub("", name).split())
    if len(s) > DISPLAY_NAME_MAX:
        raise ValueError(f"표시 이름은 {DISPLAY_NAME_MAX}자 이하여야 합니다")
    return s or None


def _labelers_path(data_root: str) -> str:
    return os.path.join(data_root, LABELERS_FILE)


def load_labelers(data_root: str) -> LabelerRoster:
    """<data_root>\\labelers.json 을 읽는다. 없으면 빈 명부. 깨졌으면 빈 명부 + problem(파일은 그대로 둔다).

    형식: {"v": 1, "labelers": [{"labeler_id": "…", "display_name": "…"|null, "added_at": "…"}]}.
    형식에 맞지 않는 항목은 건너뛴다(그 수를 problem 에 적는다). 같은 labeler_id 는 처음 것만.
    """
    p = _labelers_path(data_root)
    try:
        with builtins.open(p, "rb") as f:
            raw = f.read(LABELERS_MAX_BYTES + 1)
    except FileNotFoundError:
        return LabelerRoster()
    except OSError as e:
        return LabelerRoster(problem=f"라벨러 명부를 읽지 못했습니다 ({type(e).__name__})")
    try:
        doc = load_json_strict(raw, LABELERS_MAX_BYTES)
    except StrictJsonError:
        return LabelerRoster(problem="라벨러 명부(labelers.json)가 깨졌습니다. 새로 추가하면 깨진 파일은 옆에 보존됩니다.")
    items = doc.get("labelers")
    if not isinstance(items, list):
        return LabelerRoster(problem="라벨러 명부(labelers.json)의 형식이 아닙니다.")
    out: list[LabelerEntry] = []
    bad = 0
    for it in items:
        try:
            if not isinstance(it, dict):
                raise ValueError
            lid = check_labeler_id(it.get("labeler_id"))
            dn = clean_display_name(it.get("display_name"))
            at = it.get("added_at") if isinstance(it.get("added_at"), str) else None
        except ValueError:
            bad += 1
            continue
        if any(e.labeler_id == lid for e in out):
            continue
        out.append(LabelerEntry(lid, dn, at))
    return LabelerRoster(out, problem=(f"라벨러 명부의 항목 {bad}개가 형식에 맞지 않아 건너뛰었습니다." if bad else None))


def add_labeler(data_root: str, labeler_id: str, display_name: str | None = None, *,
                now: datetime | None = None) -> LabelerRoster:
    """명부에 라벨러를 더한다(이미 있으면 표시 이름만 바꾼다). 원자적으로 저장하고 새 명부를 돌려준다.

    명부 파일이 깨져 있었으면 그 파일을 labelers.json.corrupt-<시각> 으로 옮겨 두고 새로 쓴다(지우지 않는다).
    형식이 틀리면 ValueError. 저장 실패는 OSError 를 그대로 올린다.
    """
    lid = check_labeler_id(labeler_id)
    dn = clean_display_name(display_name)
    roster = load_labelers(data_root)
    p = _labelers_path(data_root)
    if roster.problem and os.path.exists(p) and not roster.entries:
        _preserve_corrupt(p, now)
    stamp = iso(now or datetime.now().astimezone())
    entries = list(roster.entries)
    old = roster.get(lid)
    if old is None:
        entries.append(LabelerEntry(lid, dn, stamp))
    else:
        entries[entries.index(old)] = LabelerEntry(lid, dn, old.added_at)
    doc = {"v": LABELERS_V, "labelers": [
        {"labeler_id": e.labeler_id, "display_name": e.display_name, "added_at": e.added_at} for e in entries]}
    os.makedirs(data_root, exist_ok=True)
    atomic_write(p, _dump_project(doc))
    return LabelerRoster(entries)


# ---------------------------------------------------------------------------
# 세션 고르기 계산 (세션 선택 화면용, D-09 1)

@dataclass(frozen=True)
class ExistingSession:
    """한 라벨러의 이미 있는 세션 하나(labels/ 파일 이름에서)."""
    session_id: str
    session_kind: str
    machine4s: tuple[str, ...]
    locked: bool                          # locks/<session_id>.json 이 있음 → 더 쓸 수 없음 (6-14)
    on_this_pc: bool
    guide_version: str | None = None      # 자기 세션 파일 첫 줄에서 (읽지 못하면 None)

    @property
    def resumable(self) -> bool:
        return not self.locked


@dataclass(frozen=True)
class ProjectSurvey:
    """세션 선택 화면이 프로젝트에서 알아야 하는 것. 파일 이름만으로 만든다(라벨러 자기 세션 첫 줄 빼고)."""
    session_ids: tuple[str, ...]          # labels/ 의 모든 세션 ID (이름 규칙에 맞는 것)
    locked_ids: frozenset                 # locks/<session_id>.json 이 있는 세션
    relabel_plans: int                    # relabel/RP-*.json 수
    origin_files: int                     # origin/ 안 파일 수
    engine_results: bool                  # derived/link-*.json 이 있음
    active_guide_version: str | None
    guide_sha8: str | None                # 그 기준서 본문 SHA-256 앞 8자 (표시용)
    roster_kinds: dict                    # project.json labelers[]: labeler_id → 맡긴 세션 종류 tuple

    def mine(self, labeler_id: str) -> list[str]:
        return [s for s in self.session_ids if s.startswith(labeler_id + "-")
                and SESSION_ID_RE.match(s).group(1) == labeler_id]


def _names(d: str) -> list[str]:
    try:
        return sorted(os.listdir(d))
    except OSError:
        return []


def survey_project(project_root: str, project_data: dict | None) -> ProjectSurvey:
    """프로젝트 폴더의 이름들을 훑는다. 실패해도 예외 없이 빈 값으로(세션 선택 화면은 막지 않는다)."""
    sids: list[str] = []
    for name in _names(os.path.join(project_root, "labels")):
        parsed = parse_session_file_name(name)
        if parsed and parsed[0] not in sids:
            sids.append(parsed[0])
    locked = frozenset(m.group(1) for m in (LOCK_FILE_RE.match(n) for n in _names(os.path.join(project_root, "locks")))
                       if m)
    plans = sum(1 for n in _names(os.path.join(project_root, "relabel")) if RELABEL_PLAN_RE.match(n))
    origin = sum(1 for n in _names(os.path.join(project_root, "origin")) if not n.startswith("."))
    engine = bool([p for p in glob.glob(os.path.join(glob.escape(os.path.join(project_root, "derived")), "link-*.json"))
                   if os.path.isfile(p)])
    data = project_data or {}
    gv = data.get("active_guide_version") if isinstance(data.get("active_guide_version"), str) else None
    sha8 = None
    for g in data.get("guides") or []:
        if isinstance(g, dict) and g.get("guide_version") == gv and isinstance(g.get("doc_sha256"), str):
            sha8 = g["doc_sha256"][:8]
    roster: dict[str, tuple] = {}
    for e in data.get("labelers") or []:
        if isinstance(e, dict) and isinstance(e.get("labeler_id"), str):
            kinds = e.get("assigned_session_kinds")
            roster[e["labeler_id"]] = tuple(k for k in kinds if isinstance(k, str)) if isinstance(kinds, list) else ()
    return ProjectSurvey(tuple(sids), locked, plans, origin, engine, gv, sha8, roster)


def _first_record_guide(path: str) -> str | None:
    """기록 파일 첫 줄(session.open)의 guide_version. 읽지 못하면 None. 첫 줄만 읽는다."""
    try:
        with builtins.open(path, "rb") as f:
            line = f.readline(FIRST_LINE_MAX_BYTES + 1)
        rec = load_json_strict(line.strip(), FIRST_LINE_MAX_BYTES)
    except (OSError, StrictJsonError):
        return None
    body = rec.get("body") if rec.get("kind") == "session.open" else None
    gv = body.get("guide_version") if isinstance(body, dict) else None
    return gv if isinstance(gv, str) else None


def existing_sessions(project_root: str, labeler_id: str, survey: ProjectSurvey,
                      this_machine4: str | None = None) -> list[ExistingSession]:
    """그 라벨러 자기 세션만(다른 라벨러 세션은 이름도 돌려주지 않는다). 첫 줄에서 기준서 버전을 읽는다."""
    check_labeler_id(labeler_id)
    labels = os.path.join(project_root, "labels")
    by_sid: dict[str, list[str]] = {}
    for name in _names(labels):
        parsed = parse_session_file_name(name)
        if parsed and parsed[0] in survey.mine(labeler_id):
            by_sid.setdefault(parsed[0], []).append(parsed[1])
    out = []
    for sid in survey.mine(labeler_id):
        m4s = tuple(sorted(by_sid.get(sid, ())))
        gv = None
        for m4 in ((this_machine4,) if this_machine4 in m4s else ()) + m4s:
            gv = _first_record_guide(os.path.join(labels, session_file_name(sid, m4)))
            if gv:
                break
        out.append(ExistingSession(sid, CODE_KINDS[SESSION_ID_RE.match(sid).group(2)], m4s,
                                   sid in survey.locked_ids, this_machine4 in m4s, gv))
    return out


@dataclass(frozen=True)
class KindChoice:
    """새 세션 종류 하나를 지금 열 수 있는지와 그 까닭."""
    kind: str
    label: str
    enabled: bool
    reason: str = ""                       # 막힌 까닭(화면에 보임). 열 수 있으면 빈 문자열이나 짧은 메모


def kind_choices(survey: ProjectSurvey, labeler_id: str | None,
                 mine: Iterable[ExistingSession] = ()) -> list[KindChoice]:
    """지금 프로젝트 상태에서 새로 열 수 있는 세션 종류(02 7-2 5단계, D-09 1).

    - primary: 늘 열 수 있다. 다만 이어 할 수 있는 같은 라벨러의 1차 세션이 있으면 새로 만들지 않고 이어 하게 한다.
    - relabel_same·relabel_second: 재라벨 계획(relabel/RP-*.json)이 있어야 한다. 계획 고르기는 v1 이라 지금은 막는다.
    - adjudication: 잠긴 세션(locks/<session_id>.json)이 2개 이상.
    - origin_review: 출처 자료(origin/)가 있어야 한다.
    - analysis: 엔진 결과(derived/link-*.json)나 잠긴 1차 세션이 있어야 한다.
    - 프로젝트 명부(project.json labelers[])에 그 라벨러가 있고 맡긴 종류가 정해져 있으면 그 밖의 종류는 막는다
      (분석 세션은 라벨을 만들지 않아 명부의 종류 목록에 없으므로 막지 않는다). 명부에 없는 라벨러는 막지 않는다(13-3 의 9).
    """
    mine = list(mine)
    assigned = survey.roster_kinds.get(labeler_id) if labeler_id else None
    locked_primary = [s for s in survey.locked_ids if vis.session_kind_of_id(s) == "primary"]
    out = []
    for kind in KIND_ORDER:
        ok, why = True, ""
        if kind == "primary":
            cont = next((s for s in mine if s.session_kind == "primary" and s.resumable), None)
            if cont is not None:
                ok, why = False, f"이어 할 1차 세션 {cont.session_id} 가 있습니다. 위에서 이어 하기를 고르세요"
        elif kind in ("relabel_same", "relabel_second"):
            ok = False
            why = ("재라벨 계획(relabel\\)이 없습니다" if survey.relabel_plans == 0
                   else "재라벨 계획 고르기는 아직 없습니다 (v1)")
        elif kind == "adjudication":
            n = len(survey.locked_ids)
            if n < 2:
                ok, why = False, f"잠긴 세션이 2개 이상 있어야 합니다 (지금 {n}개)"
        elif kind == "origin_review":
            if survey.origin_files == 0:
                ok, why = False, "출처 자료(origin\\)가 없습니다"
        elif kind == "analysis":
            if not survey.engine_results and not locked_primary:
                ok, why = False, "엔진 결과(derived\\link-*.json)도 잠긴 1차 세션도 없습니다"
        if ok and assigned and kind != "analysis" and kind not in assigned:
            ok, why = False, "프로젝트 명부에서 이 라벨러에게 맡기지 않은 종류입니다"
        out.append(KindChoice(kind, KIND_LABELS[kind], ok, why))
    return out


def exposure_notes(survey: ProjectSurvey, labeler_id: str, session_kind: str) -> list[str]:
    """고른 세션에 대한 노출 안내(D-09 5). 엔진 결과·라벨 내용은 말하지 않는다 — 기록이 남는다는 사실만."""
    notes: list[str] = []
    exp = vis.analysis_exposure(survey.session_ids)
    if session_kind == "analysis":
        notes.append("분석 세션은 엔진 결과와 잠긴 1차 라벨을 봅니다. 라벨은 만들 수 없습니다.")
        open_primary = [s for s in survey.mine(labeler_id)
                        if vis.session_kind_of_id(s) in vis.LABELING_SESSIONS and s not in survey.locked_ids]
        notes.append("분석 세션을 연 기록이 남고, 이 라벨러의 라벨링 세션은 일치도 보고서에 '노출' 로 표시됩니다.")
        if open_primary:
            notes.append(f"이 라벨러의 잠기지 않은 라벨링 세션이 {len(open_primary)}개 있습니다. "
                         "그 라벨링을 마치고 잠근 뒤에 분석 세션을 여는 것이 좋습니다.")
    elif session_kind in vis.LABELING_SESSIONS and labeler_id in exp.labelers:
        notes.append("이 라벨러는 이 프로젝트에서 분석 세션을 연 적이 있어, 이 세션은 일치도 보고서에 '노출' 로 표시됩니다.")
    return notes
