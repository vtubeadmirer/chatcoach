"""세션(회차) 정보와 이 PC 의 기계 ID (docs/design/03-data-model.md 5-9·6-4·13-1·13-2, 02 3-2).

입출력 층이다. Qt 위젯을 import 하지 않는다(QLockFile 같은 잠금은 store 가 주입받는다).

- SessionContext: 한 세션을 여는 데 필요한 값 묶음. store.ProjectStore.open 이 이것으로 기록 파일 이름과
  session.open 첫 줄을 정한다. 세션 ID·라벨러·machine4 형식은 만들 때 검사한다(파일 이름이 되므로, S-46).
- machine_id(data_root): <data>\\machine.json 의 UUID. 없으면 만들고, 깨졌으면 깨진 파일을 보존한 뒤 새로 만든다.
  (machine_id 는 파일 이름을 나누는 데만 쓰여서, 바뀌면 같은 세션의 새 파일이 하나 더 생길 뿐 기록은 잃지 않는다.)
- machine4(machine_id): base32(SHA-256(machine_id)) 앞 4자 소문자. app.project.machine4_of 와 같은 값.
- new_session_id(labeler_id, kind, date, rand4): '<labeler_id>-<P·RS·R2·AD·OR>-<yyyymmdd>-<4자>'.
- new_session(...): 위 셋을 합쳐 새 SessionContext 를 만든다. 시계·난수는 인자로 바꿀 수 있다(테스트).
- session_file_name / parse_session_file_name: 'labels/<session_id>.<machine4>.jsonl' 규칙(13-2).
"""
from __future__ import annotations

import builtins
import os
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from .core.validate import StrictJsonError, load_json_strict
from .project import _dump_project, atomic_write, iso, machine4_of

MACHINE_FILE = "machine.json"
MACHINE_MAX_BYTES = 4096

# 03 5-9 세션 종류와 코드
KIND_CODES: dict[str, str] = {
    "primary": "P",
    "relabel_same": "RS",
    "relabel_second": "R2",
    "adjudication": "AD",
    "origin_review": "OR",
}
CODE_KINDS: dict[str, str] = {v: k for k, v in KIND_CODES.items()}

# label-event.schema.json 의 $defs 와 같은 정규식 (\Z: 끝 줄바꿈 허용하지 않음)
LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
SESSION_ID_RE = re.compile(r"\A([A-Z][A-Z0-9]{1,7})-(P|RS|R2|AD|OR)-([0-9]{8})-([a-z0-9]{4})\Z")
MACHINE4_RE = re.compile(r"\A[a-z2-7]{4}\Z")
RAND4_RE = re.compile(r"\A[a-z0-9]{4}\Z")
UUID4_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
SESSION_FILE_RE = re.compile(
    r"\A([A-Z][A-Z0-9]{1,7}-(?:P|RS|R2|AD|OR)-[0-9]{8}-[a-z0-9]{4})\.([a-z2-7]{4})\.jsonl\Z")
RAND_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

DEFAULT_BLIND = {"to_ai": True, "to_other_sessions": True, "to_origin": True, "nickname_masked_default": True}


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
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(p, f"{p}.corrupt-{stamp}")
        except OSError:
            pass
    mid = (uuid_factory or (lambda: str(uuid.uuid4())))()
    if not UUID4_RE.match(mid):
        raise ValueError("machine_id 는 UUID v4 소문자여야 합니다")
    os.makedirs(data_root, exist_ok=True)
    atomic_write(p, _dump_project({"machine_id": mid, "created_at": iso(now or datetime.now().astimezone())}))
    return mid


def _yyyymmdd(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y%m%d")
    s = str(d)
    if not re.fullmatch(r"[0-9]{8}", s):
        raise ValueError(f"날짜는 yyyymmdd 여야 합니다: {s!r}")
    return s


def new_session_id(labeler_id: str, kind: str, d, rand4: str) -> str:
    """'<labeler_id>-<종류 코드>-<yyyymmdd>-<4자>'. kind 는 'primary' 같은 세션 종류나 'P' 같은 코드.

    날짜는 사람이 보기 위한 것이다(순서 판단에 쓰지 않는다, 13-1). 형식이 맞지 않으면 ValueError.
    """
    code = KIND_CODES.get(kind, kind)
    if code not in CODE_KINDS:
        raise ValueError(f"모르는 세션 종류입니다: {kind!r}")
    if not isinstance(labeler_id, str) or not LABELER_RE.match(labeler_id):
        raise ValueError(f"labeler_id 형식이 아닙니다: {labeler_id!r}")
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


def new_session(labeler_id: str, session_kind: str = "primary", *, data_root: str | None = None,
                machine_id_value: str | None = None, now: datetime | None = None,
                rand4: str | None = None, guide_version: str | None = None, scope: dict | None = None,
                relabel_plan_id: str | None = None, blind: dict | None = None, tool: dict | None = None,
                tool_models: dict | None = None) -> SessionContext:
    """새 세션. machine_id_value 가 없으면 data_root(없으면 app.paths.data_root())의 machine.json 에서 얻는다.

    now 는 OS 시간대가 붙은 시각(없으면 지금). 날짜 부분이 session_id 에, 전체가 session_created_at 에 들어간다.
    """
    if machine_id_value is None:
        if data_root is None:
            from . import paths
            data_root = paths.data_root()
        machine_id_value = machine_id(data_root)
    t = now if now is not None else datetime.now().astimezone()
    if t.tzinfo is None:
        t = t.astimezone()
    sid = new_session_id(labeler_id, session_kind, t.date(), rand4 or random4())
    return SessionContext(
        labeler_id=labeler_id, session_kind=session_kind, session_id=sid,
        machine4=machine4(machine_id_value), machine_id=machine_id_value,
        session_created_at=t.isoformat(timespec="seconds"), guide_version=guide_version,
        scope=scope, relabel_plan_id=relabel_plan_id, blind=blind, tool=tool, tool_models=tool_models,
    )
