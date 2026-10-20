"""프로젝트 폴더 쓰기 통로 — 세션 기록 파일 열기·라벨 추가·철회 (docs/design/03-data-model.md 6-3·6-4·8-1·8-2·10·12·13, 02 6-3).

입출력 층이다. Qt 위젯을 import 하지 않는다. 잠금(locks/open.lock)은 lock_factory 로 주입받고, 주지 않으면
PySide6.QtCore.QLockFile 을 처음 쓸 때 늦게 import 한다(QtCore 만, 위젯 없음).

여는 순서 (ProjectStore.open)
1. project.json 을 app.project.open_project 로 읽고 검증한다. 실패하면 읽기 전용.
2. locks/open.lock 을 잡는다. 못 잡으면 프로젝트 전체를 읽기 전용으로 연다(13-5). 사유에 잠금을 쥔 호스트를 적는다.
3. labels/ 에서 '<이 세션 ID>.<machine4>.jsonl' 꼴의 파일만 연다(같은 세션의 다른 PC 파일 포함, 11장).
   다른 세션의 파일은 디스크에서 읽지 않는다(12-1 '열지 않음'). 이 세션 ID 로 시작하지만 규칙에 맞지 않는
   이름(OneDrive 충돌 사본 등)은 읽지 않고 conflict_files 로 알린다(13-4).
4. 줄 읽기·검사(8-2): 마지막 줄만 잘렸으면 그 조각을 <파일>.partial-<시각> 으로 옮기고(자기 파일·쓰기 가능할 때)
   경고 한 번. 중간 줄이 깨졌거나 스키마에 맞지 않거나 v 가 새 형식이거나 seq 가 끊기면 읽기 전용.
5. 자기 파일이 없으면 첫 줄 session.open 을 쓴다. 같은 세션의 다른 PC 파일이 있으면 그 메타를 그대로 되풀이한다.
   run_info 를 주면 run.start 줄을 이어 쓴다.

라벨 한 건 저장 (append_label, 02 6-3)
  초안(kind·body·entity_id·supersedes·x) → 봉투(v·id·seq·created_at·session_id·labeler_id·app_version)와
  공통 라벨 메타(guide_version·guide_sha12·thresholds_sha12·media_rev·calibration_rev) 자동 채움 →
  판 연결 규칙 검사(10-1) → 스키마 손검사(schemas/label-event.schema.json) → jsonl.append(끝 개행 확인 → write 1회
  → fsync) → 마지막 줄 되읽기 확인 → 메모리 색인 갱신 → 저장된 레코드 사본을 돌려준다.
  실패하면 예외를 올린다(조용히 버리지 않는다, 8-1 의 6). 초안은 호출하는 쪽이 갖고 있으니 다시 시도할 수 있다.

시계·uuid 는 open(clock=…, uuid_factory=…) 로 바꿀 수 있다(테스트). 순서 판단은 벽시계가 아니라 (파일, seq) 와
supersedes 연결로 한다. 로그에는 본문을 남기지 않는다(줄 번호·사유·시간만).
"""
from __future__ import annotations

import builtins
import copy
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from . import jsonl, paths
from . import project as projmod
from .core import visibility as vis
from .core.validate import StrictJsonError, load_json_strict
from .session import (DEFAULT_BLIND, SessionContext, machine4 as machine4_from_id, machine_id,
                      parse_session_file_name, session_file_name)

log = logging.getLogger(__name__)

RECORD_V = 1                         # 이 앱이 쓰고 읽는 기록 형식 버전(봉투 v)
MAX_LINE_BYTES = 1 << 20             # 8-1 의 5: 한 줄 최대 1MB
SAVE_TARGET_MS = 30.0                # 02 9장: 라벨 저장(되읽기 포함) 목표
STALE_LOCK_MS = 0                    # QLockFile staleLockTime. 0 = 시간으로는 오래됐다고 보지 않는다(아래 _acquire_lock)
SCHEMA_REL = ("schemas", "label-event.schema.json")
SCHEMA_MAX_BYTES = 1 << 20

LABEL_KINDS = ("segment", "utterance", "reaction", "continuation", "video_check",
               "missed_candidate", "origin", "adjudication")
OPS_KINDS = ("run.start", "phase.mark", "view.exposed", "warning")   # session.open 은 store 만 쓴다
ENTITY_KINDS = LABEL_KINDS + ("retract",)
AUTO_FIELDS = frozenset({"v", "id", "seq", "session_id", "labeler_id", "created_at", "app_version",
                         "guide_version", "guide_sha12", "thresholds_sha12", "media_rev", "calibration_rev"})
DRAFT_FIELDS = frozenset({"kind", "body", "entity_id", "supersedes", "x"})
RETRACT_REASONS = ("undo", "merged", "split_cleanup", "wrong_target", "other")
UUID4_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")


class StoreError(RuntimeError):
    """store 가 저장을 거부하거나 실패했다. 메시지에 본문을 넣지 않는다."""


class ReadOnlyStoreError(StoreError):
    """읽기 전용으로 연(또는 닫은) store 에 쓰려 했다."""


class LabelValidationError(StoreError, ValueError):
    """기록이 스키마나 store 규칙에 맞지 않는다. problems 에 경로와 사유가 있다."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("기록이 형식에 맞지 않습니다: " + "; ".join(self.problems[:5])
                         + (f" 외 {len(self.problems) - 5}건" if len(self.problems) > 5 else ""))


class ChainError(StoreError, ValueError):
    """판 연결(supersedes) 규칙 위반 (03 10-1)."""


class WriteVerifyError(StoreError):
    """쓴 줄을 되읽어 보니 쓴 것과 다르다. store 는 그 뒤로 읽기 전용이 된다."""


# ---------------------------------------------------------------------------
# 기록 스키마 손검사 (jsonschema 패키지에 기대지 않는다, S-29)
# label-event.schema.json 이 쓰는 키만 안다. 모르는 키가 나오면 ValueError(스키마가 바뀌면 여기도 고쳐야 한다).

_NOTE_KEYS = {"description", "title", "default", "format", "$schema", "$id", "$defs", "$comment", "examples"}
_CHECK_KEYS = {"type", "const", "enum", "pattern", "minimum", "maximum", "minLength", "maxLength",
               "minItems", "maxItems", "uniqueItems", "required", "properties", "additionalProperties",
               "unevaluatedProperties", "items", "prefixItems", "oneOf", "allOf", "if", "then", "else", "$ref"}
_pattern_cache: dict[str, re.Pattern] = {}


def _compile(p: str) -> re.Pattern:
    r = _pattern_cache.get(p)
    if r is None:
        # JSON Schema 의 '$' 는 문자열 끝. 파이썬 '$' 는 끝 줄바꿈 앞에서도 맞으므로 \Z 로 바꾼다.
        src = p[:-1] + r"\Z" if p.endswith("$") and not p.endswith(r"\$") else p
        r = _pattern_cache[p] = re.compile(src)
    return r


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _type_ok(v, t: str) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return _is_int(v)          # 1.0 도 거부한다(S-29 타입 혼동)
    if t == "number":
        return _is_int(v) or (isinstance(v, float) and v == v and v not in (float("inf"), float("-inf")))
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    raise ValueError(f"모르는 type {t!r}")


def _eq(a, b) -> bool:
    """JSON 값 비교. bool 과 정수를 구별한다."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b       # 1 과 1.0 도 다르게 본다(S-29 타입 혼동)


def _show(v) -> str:
    """문제 메시지에 넣을 값 요약. 문자열 본문은 넣지 않는다(근거·전사에 개인정보가 있을 수 있다)."""
    if isinstance(v, str):
        return f"문자열({len(v)}자)"
    if isinstance(v, (dict, list)):
        return type(v).__name__
    return repr(v)


class LabelSchema:
    """label-event.schema.json 해석기. check(record) → 문제 목록(빈 목록이면 통과)."""

    def __init__(self, root: dict):
        if not isinstance(root, dict) or "$defs" not in root:
            raise ValueError("기록 스키마 형식이 아닙니다")
        self.root = root

    def check(self, record) -> list[str]:
        out, _ = self._check(record, self.root, "$")
        return out

    def _ref(self, ref: str) -> dict:
        if not ref.startswith("#/$defs/"):
            raise ValueError(f"지원하지 않는 $ref {ref}")
        return self.root["$defs"][ref[len("#/$defs/"):]]

    def _check(self, v, s: dict, path: str) -> tuple[list[str], set[str]]:
        """(문제 목록, 이 스키마가 평가한 속성 이름들). 후자는 unevaluatedProperties 에 쓴다."""
        unknown = set(s) - _CHECK_KEYS - _NOTE_KEYS
        if unknown:
            raise ValueError(f"해석기가 모르는 스키마 키: {sorted(unknown)}")
        out: list[str] = []
        evaluated: set[str] = set()
        if "$ref" in s:
            o, e = self._check(v, self._ref(s["$ref"]), path)
            out += o
            evaluated |= e
        if "type" in s:
            types = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(_type_ok(v, t) for t in types):
                return out + [f"{path}: 형식이 {'/'.join(types)} 가 아닙니다 ({_show(v)})"], evaluated
        if "const" in s and not _eq(v, s["const"]):
            out.append(f"{path}: 값은 {s['const']!r} 이어야 합니다 ({_show(v)})")
        if "enum" in s and not any(_eq(v, e) for e in s["enum"]):
            out.append(f"{path}: 허용 값 {s['enum']} 밖입니다 ({_show(v)})")
        if isinstance(v, str):
            if "minLength" in s and len(v) < s["minLength"]:
                out.append(f"{path}: {s['minLength']}자 이상이어야 합니다")
            if "maxLength" in s and len(v) > s["maxLength"]:
                out.append(f"{path}: {s['maxLength']}자 이하여야 합니다 ({len(v)}자)")
            if "pattern" in s and not _compile(s["pattern"]).search(v):
                out.append(f"{path}: 형식(정규식)에 맞지 않습니다 ({_show(v)})")
        if _type_ok(v, "number"):
            if "minimum" in s and v < s["minimum"]:
                out.append(f"{path}: {s['minimum']} 이상이어야 합니다 ({v!r})")
            if "maximum" in s and v > s["maximum"]:
                out.append(f"{path}: {s['maximum']} 이하여야 합니다 ({v!r})")
        if isinstance(v, list):
            if "minItems" in s and len(v) < s["minItems"]:
                out.append(f"{path}: 항목이 {s['minItems']}개 이상이어야 합니다")
            if "maxItems" in s and len(v) > s["maxItems"]:
                out.append(f"{path}: 항목이 {s['maxItems']}개 이하여야 합니다")
            if s.get("uniqueItems"):
                for i in range(len(v)):
                    if any(_eq(v[i], v[j]) for j in range(i)):
                        out.append(f"{path}[{i}]: 앞 항목과 겹칩니다")
            prefix = s.get("prefixItems", [])
            for i, item in enumerate(v):
                if i < len(prefix):
                    out += self._check(item, prefix[i], f"{path}[{i}]")[0]
                elif "items" in s:
                    out += self._check(item, s["items"], f"{path}[{i}]")[0]
        if isinstance(v, dict):
            for k in s.get("required", []):
                if k not in v:
                    out.append(f"{path}: 필수 필드 {k!r} 가 없습니다")
            props = s.get("properties", {})
            extra = s.get("additionalProperties", True)
            for k, val in v.items():
                sub = f"{path}.{k}"
                if k in props:
                    out += self._check(val, props[k], sub)[0]
                    evaluated.add(k)
                elif extra is False:
                    out.append(f"{path}: 모르는 필드 {k!r}")
                elif isinstance(extra, dict):
                    out += self._check(val, extra, sub)[0]
                    evaluated.add(k)
        for sub in s.get("allOf", []):
            o, e = self._check(v, sub, path)
            out += o
            evaluated |= e
        if "oneOf" in s:
            hits = [self._check(v, sub, path) for sub in s["oneOf"]]
            ok = [h for h in hits if not h[0]]
            if len(ok) == 1:
                evaluated |= ok[0][1]
            elif len(ok) > 1:
                out.append(f"{path}: 여러 형식에 동시에 맞습니다")
            else:
                best = min((h[0] for h in hits), key=len)
                out += best if len(hits) == 1 else [f"{path}: 허용한 형식 어느 것에도 맞지 않습니다"] + best
        if "if" in s:
            cond, ce = self._check(v, s["if"], path)
            branch = s.get("then") if not cond else s.get("else")
            if not cond:
                evaluated |= ce
            if branch is not None:
                o, e = self._check(v, branch, path)
                out += o
                evaluated |= e
        if s.get("unevaluatedProperties") is False and isinstance(v, dict):
            for k in v:
                if k not in evaluated:
                    out.append(f"{path}: 모르는 필드 {k!r}")
        return out, evaluated


_schema_cache: dict[str, LabelSchema] = {}


def schema_path() -> str:
    return os.path.join(paths.code_dir(), *SCHEMA_REL)


def load_label_schema(path: str | None = None) -> LabelSchema:
    """schemas/label-event.schema.json 을 읽어 해석기를 만든다(경로별로 한 번). 없으면 OSError."""
    p = path or schema_path()
    s = _schema_cache.get(p)
    if s is None:
        with builtins.open(p, "rb") as f:
            s = _schema_cache[p] = LabelSchema(load_json_strict(f.read(SCHEMA_MAX_BYTES + 1), SCHEMA_MAX_BYTES))
    return s


# ---------------------------------------------------------------------------
# 시각

def _default_clock() -> datetime:
    return datetime.now().astimezone()


def _iso_ms(t: datetime) -> str:
    """OS 시간대 오프셋을 붙인 ISO 8601(밀리초). 시간대가 없는 시각은 OS 오프셋으로 해석한다(S-34)."""
    if t.tzinfo is None:
        t = t.astimezone()
    return t.isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# 기록 파일 하나

@dataclass
class SessionFile:
    name: str
    path: str
    machine4: str
    own: bool
    records: list[dict] = field(default_factory=list)
    truncated_tail: bool = False

    @property
    def last_seq(self) -> int:
        return self.records[-1]["seq"] if self.records else 0


# ---------------------------------------------------------------------------

class ProjectStore:
    """프로젝트 폴더 입출력의 유일한 쓰기 통로(02 3-2). UI 스레드에서만 쓴다(02 8장)."""

    def __init__(self, root: str, project: projmod.Project, session: SessionContext, *,
                 clock: Callable[[], datetime], uuid_factory: Callable[[], str], app_version: str):
        self.root = root
        self.project = project
        self.session = session
        self.read_only = False
        self.read_only_reasons: list[str] = []
        self.warnings: list[str] = []
        self.conflict_files: list[str] = []          # 13-4: 규칙에 맞지 않아 읽지 않은 이 세션의 파일
        self.partial_files: list[str] = []           # 8-2 의 3: 잘린 마지막 줄을 옮겨 둔 파일
        self.last_append_ms: float | None = None     # 마지막 저장에 걸린 시간(되읽기 포함)
        self.closed = False
        self._clock = clock
        self._uuid = uuid_factory
        self._app_version = app_version
        self._schema: LabelSchema | None = None
        self._lock = None
        self._machine_id: str | None = session.machine_id
        self._files: list[SessionFile] = []
        self._own: SessionFile | None = None
        self._meta: dict | None = None               # session.open body
        self._by_id: dict[str, dict] = {}
        self._versions: dict[str, list[str]] = {}     # entity_id → 판 id (쓴 순서)
        self._superseded: set[str] = set()
        self._entity_kind: dict[str, str] = {}
        self._rev = 0                                 # 색인이 바뀔 때마다 +1 (effective 캐시 무효화)
        self._eff_cache: tuple[int, Any] | None = None
        self._pj_stamp: tuple[int, int] | None = None  # project.json (mtime_ns, size) — 라벨 메타를 새로 읽을지 판단

    # ------------------------------------------------------------------ 열기
    @classmethod
    def open(cls, path: str, session: SessionContext, lock_factory: Callable[[str], Any] | None = None, *,
             clock: Callable[[], datetime] | None = None, uuid_factory: Callable[[], str] | None = None,
             app_version: str | None = None, data_root: str | None = None, run_info: dict | None = None,
             schema: LabelSchema | None = None) -> "ProjectStore":
        """프로젝트를 이 세션으로 연다. 쓸 수 없는 사정이 있으면 예외 대신 read_only=True 와 사유로 연다.

        - lock_factory(lock_path) → tryLock(timeout)·unlock() 을 가진 객체. 기본 QtCore.QLockFile.
        - session.machine_id 가 없으면 data_root(없으면 app.paths.data_root())의 machine.json 에서 읽는다.
          그 machine_id 의 machine4 가 session.machine4 와 다르면 ValueError(다른 PC 의 파일 이름에 쓰지 않는다).
        - run_info 를 주면 run.start 줄을 쓴다(body 모양은 스키마 runStartBody).
        잘못된 인자(세션 종류가 기존 기록과 다름, 기준서 버전 바꾸기 등)는 ValueError.
        """
        if app_version is None:
            from . import __version__ as app_version  # noqa: N811
        proj = projmod.open_project(path, machine4=session.machine4)
        st = cls(proj.root, proj, session, clock=clock or _default_clock,
                 uuid_factory=uuid_factory or (lambda: str(uuid.uuid4())), app_version=app_version)
        st.warnings += proj.warnings
        st._pj_stamp = st._project_stamp()
        if proj.read_only:
            st._set_ro(*[f"project.json: {p}" for p in proj.problems[:5]])
        try:
            st._schema = schema or load_label_schema()
        except (OSError, StrictJsonError, ValueError) as e:
            st._set_ro(f"기록 스키마(schemas/label-event.schema.json)를 읽지 못했습니다: {type(e).__name__}")
        mid = session.machine_id
        if mid is None:
            mid = machine_id(data_root or paths.data_root())
            if machine4_from_id(mid) != session.machine4:
                raise ValueError("session.machine4 가 이 PC 의 machine_id 와 맞지 않습니다")
        st._machine_id = mid
        if not st.read_only:
            st._acquire_lock(lock_factory)
        try:
            st._load()
            st._prepare_meta()
            if not st.read_only:
                if st._own is None or not st._own.records:
                    st._write_session_open()
                if run_info is not None:
                    st._append_record(st._envelope("run.start", run_info))
        except BaseException:
            st._release_lock()
            raise
        if st.read_only:
            log.warning("세션 %s 를 읽기 전용으로 열었습니다: %s", session.session_id, "; ".join(st.read_only_reasons[:3]))
        return st

    @property
    def read_only_reason(self) -> str | None:
        return "; ".join(self.read_only_reasons) if self.read_only_reasons else None

    @property
    def path(self) -> str:
        """이 PC 가 쓰는 기록 파일 경로 labels/<session_id>.<machine4>.jsonl."""
        return os.path.join(self.root, "labels", self.session.file_name)

    @property
    def session_meta(self) -> dict | None:
        """session.open body 사본(guide_version·해시·blind·scope 등)."""
        return copy.deepcopy(self._meta)

    def _set_ro(self, *reasons: str) -> None:
        self.read_only = True
        for r in reasons:
            if r not in self.read_only_reasons:
                self.read_only_reasons.append(r)

    def _acquire_lock(self, lock_factory) -> None:
        """locks/open.lock. 못 잡으면 읽기 전용(13-5).

        staleLockTime 은 0 으로 둔다. QLockFile 은 같은 PC 에서 잠금을 쥔 프로세스가 죽었으면 시간과 상관없이
        오래된 잠금으로 보고 가져온다. 시간을 주면(기본 30초) 몇 시간째 라벨링 중인 살아 있는 창의 잠금도
        그 시간이 지나면 빼앗긴다 — QLockFile 은 잠금 파일을 갱신하지 않기 때문이다.
        """
        lock_path = os.path.join(self.root, "locks", "open.lock")
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            if lock_factory is None:
                lock_factory = _default_lock_factory()
            lk = lock_factory(lock_path)
            if hasattr(lk, "setStaleLockTime"):
                lk.setStaleLockTime(STALE_LOCK_MS)
            ok = bool(lk.tryLock(0))
        except (OSError, ImportError) as e:
            self._set_ro(f"프로젝트 잠금(locks/open.lock)을 만들 수 없습니다: {type(e).__name__}")
            return
        if ok:
            self._lock = lk
            return
        self._set_ro("다른 창이나 PC 가 이 프로젝트를 쓰고 있습니다" + _lock_holder(lk))

    def _release_lock(self) -> None:
        lk, self._lock = self._lock, None
        if lk is not None:
            try:
                lk.unlock()
            except Exception:  # noqa: BLE001 — 닫기는 실패해도 계속한다
                log.exception("프로젝트 잠금 풀기 실패")

    # ------------------------------------------------------------------ 읽기
    def _load(self) -> None:
        sid = self.session.session_id
        labels = os.path.join(self.root, "labels")
        try:
            names = sorted(os.listdir(labels))
        except FileNotFoundError:
            names = []
            if not self.read_only:
                os.makedirs(labels, exist_ok=True)
        own_name = self.session.file_name
        for name in names:
            parsed = parse_session_file_name(name)
            if parsed is None:
                # 이 세션 ID 로 시작하는 기록 파일 같은데 이름이 규칙 밖 → 충돌 사본(13-4). 다른 세션 것은 보지도 않는다.
                if name.startswith(sid) and ".jsonl" in name and ".partial-" not in name:
                    self.conflict_files.append(name)
                continue
            if parsed[0] != sid:
                continue
            sf = SessionFile(name=name, path=os.path.join(labels, name), machine4=parsed[1], own=(name == own_name))
            self._read_file(sf)
            self._files.append(sf)
            if sf.own:
                self._own = sf
        if self.conflict_files:
            self.warnings.append(f"이름 규칙에 맞지 않는 이 세션의 기록 파일 {len(self.conflict_files)}개를 읽지 않았습니다(충돌 사본).")
        # 판 색인. 같은 id 가 두 파일에 있으면 내용이 같아야 한다(13-3 의 5).
        for sf in self._files:
            for rec in sf.records:
                old = self._by_id.get(rec["id"])
                if old is not None:
                    if not _eq(old, rec):
                        self._set_ro(f"{sf.name}: 같은 id 의 다른 기록이 있습니다")
                    continue
                self._index(rec)

    def _read_file(self, sf: SessionFile) -> None:
        try:
            res = jsonl.read(sf.path, max_line_bytes=MAX_LINE_BYTES)
        except OSError as e:
            self._set_ro(f"{sf.name}: 읽지 못했습니다 ({type(e).__name__})")
            return
        if res.corrupt_lines:
            shown = ", ".join(map(str, res.corrupt_lines[:5]))
            self._set_ro(f"{sf.name}: {shown} 번째 줄이 깨졌습니다")
        if res.truncated_tail:
            sf.truncated_tail = True
            if sf.own and not self.read_only:
                self._isolate_tail(sf)
            else:
                self.warnings.append(f"{sf.name}: 마지막 줄이 잘려 있어 그 줄을 빼고 읽었습니다.")
        recs = res.records
        problems: list[str] = []
        for i, r in enumerate(recs, start=1):
            v = r.get("v")
            if _is_int(v) and v > RECORD_V:
                self._set_ro(f"{sf.name}: 이 앱보다 새 기록 형식(v{v})이 있습니다")
                break
            if self._schema is not None:
                p = self._schema.check(r)
                if p:
                    problems.append(f"{i}번째 기록: {p[0]}")
            if r.get("seq") != i:
                problems.append(f"{i}번째 기록: seq 가 {i} 가 아닙니다")
            if r.get("session_id") != self.session.session_id or r.get("labeler_id") != self.session.labeler_id:
                problems.append(f"{i}번째 기록: 세션·라벨러가 파일 이름과 다릅니다")
        if recs:
            first = recs[0]
            body = first.get("body") if isinstance(first.get("body"), dict) else {}
            if first.get("kind") != "session.open":
                problems.append("첫 줄이 session.open 이 아닙니다")
            elif body.get("machine4") != sf.machine4:
                problems.append("첫 줄의 machine4 가 파일 이름과 다릅니다")
            if any(r.get("kind") == "session.open" for r in recs[1:]):
                problems.append("session.open 이 첫 줄 말고도 있습니다")
        if problems:
            self._set_ro(*[f"{sf.name}: {p}" for p in problems[:5]])
        sf.records = recs

    def _isolate_tail(self, sf: SessionFile) -> None:
        """잘린 마지막 줄 조각을 <파일>.partial-<시각> 으로 옮기고 파일을 마지막 LF 까지 줄인다(8-2 의 3).

        jsonl.append 는 끝이 LF 가 아니면 LF 를 먼저 쓰므로, 줄이지 않고 이어 쓰면 잘린 조각이 '중간에 깨진 줄' 로
        남아 다음에 열 때 세션 전체가 읽기 전용이 된다. 그래서 잠금을 쥔 자기 파일에서만 조각을 떼어 낸다.
        """
        with builtins.open(sf.path, "rb") as f:
            data = f.read()
        cut = data.rfind(b"\n") + 1          # LF 가 없으면 0 (파일 전체가 잘린 한 줄)
        tail = data[cut:]
        stamp = self._clock().strftime("%Y%m%d-%H%M%S")
        dest = f"{sf.path}.partial-{stamp}"
        n = 1
        while os.path.exists(dest):
            n += 1
            dest = f"{sf.path}.partial-{stamp}-{n}"
        projmod.atomic_write(dest, tail)
        with builtins.open(sf.path, "r+b") as f:
            f.truncate(cut)
            f.flush()
            os.fsync(f.fileno())
        self.partial_files.append(os.path.basename(dest))
        self.warnings.append(f"{sf.name}: 마지막 줄이 잘려 있어 {os.path.basename(dest)} 로 옮겼습니다({len(tail)}바이트).")
        log.warning("잘린 마지막 줄 격리: %s (%d 바이트)", os.path.basename(dest), len(tail))

    def _index(self, rec: dict) -> None:
        self._rev += 1
        self._by_id[rec["id"]] = rec
        kind = rec.get("kind")
        if kind not in ENTITY_KINDS:
            return
        eid = rec.get("entity_id")
        if not isinstance(eid, str):
            return
        self._versions.setdefault(eid, []).append(rec["id"])
        if kind != "retract":
            self._entity_kind.setdefault(eid, kind)
        sup = rec.get("supersedes")
        if isinstance(sup, str):
            self._superseded.add(sup)

    def _prepare_meta(self) -> None:
        """session.open 메타를 정한다: 기존 파일(자기 것 → 다른 PC 것)의 첫 줄, 없으면 새로 만든다."""
        existing = None
        for sf in sorted(self._files, key=lambda f: not f.own):
            if sf.records and sf.records[0].get("kind") == "session.open":
                existing = sf.records[0]["body"]
                break
        data = self.project.data
        if existing is not None:
            meta = copy.deepcopy(existing)
            if meta.get("session_kind") != self.session.session_kind:
                raise ValueError("세션 종류가 기존 기록과 다릅니다")
            if self.session.guide_version is not None and self.session.guide_version != meta.get("guide_version"):
                raise ValueError("세션 안에서는 기준서 버전을 바꿀 수 없습니다(03 9-2). 새 세션을 만드세요.")
            if data and meta.get("project_id") != data.get("project_id"):
                self._set_ro("기록의 project_id 가 이 프로젝트와 다릅니다")
            meta["machine_id"] = self._machine_id
            meta["machine4"] = self.session.machine4
            self._meta = meta
        elif data is None:
            self._meta = None
            return
        else:
            gv = self.session.guide_version or data.get("active_guide_version")
            g = _guide(data, gv)
            if g is None:
                raise ValueError(f"project.json 에 기준서 {gv!r} 가 없습니다")
            s = self.session
            created = s.session_created_at or _iso_ms(self._clock())
            meta = {
                "project_id": data["project_id"],
                "session_kind": s.session_kind,
                "session_created_at": created,
                "machine_id": self._machine_id,
                "machine4": s.machine4,
                "guide_version": gv,
                "guide_doc_sha256": g["doc_sha256"],
                "thresholds_sha256": g["thresholds_sha256"],
                "scope": copy.deepcopy(s.scope) if s.scope is not None else {"type": "whole"},
                "relabel_plan_id": s.relabel_plan_id,
                "blind": copy.deepcopy(s.blind) if s.blind is not None else dict(DEFAULT_BLIND),
                "dataset_split": data.get("dataset_split", "dev"),
            }
            if s.tool is not None:
                meta["tool"] = copy.deepcopy(s.tool)
            if s.tool_models is not None:
                meta["tool_models"] = copy.deepcopy(s.tool_models)
            self._meta = meta
        # 9-1: 세션의 기준서 해시가 프로젝트 기록과 다르면 경고
        if data is not None:
            g = _guide(data, self._meta.get("guide_version"))
            if g is None or g.get("doc_sha256") != self._meta.get("guide_doc_sha256") \
                    or g.get("thresholds_sha256") != self._meta.get("thresholds_sha256"):
                self.warnings.append("세션의 기준서·시작값 해시가 project.json 의 기록과 다릅니다.")

    def _write_session_open(self) -> None:
        if self._meta is None:
            raise StoreError("세션 메타를 만들 수 없습니다")
        if self._own is None:
            self._own = SessionFile(name=self.session.file_name, path=self.path,
                                    machine4=self.session.machine4, own=True)
            self._files.append(self._own)
        self._append_record(self._envelope("session.open", copy.deepcopy(self._meta)))

    # ------------------------------------------------------------------ 봉투
    def _envelope(self, kind: str, body: dict) -> dict:
        own = self._own
        return {
            "v": RECORD_V,
            "id": self._new_uuid(),
            "seq": (own.last_seq if own else 0) + 1,
            "kind": kind,
            "session_id": self.session.session_id,
            "labeler_id": self.session.labeler_id,
            "created_at": _iso_ms(self._clock()),
            "app_version": self._app_version,
            "body": body,
        }

    def _project_stamp(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.project.paths.project_json)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def refresh_project(self) -> bool:
        """project.json 이 이 store 가 읽은 뒤에 바뀌었으면(수정 시각·크기) 다시 읽어 project.data 를 바꾼다.

        라벨의 media_rev·calibration_rev 는 저장하는 순간의 project.json 값이어야 한다(D2). 작업대나 다른 도구가
        project.json 을 고쳐도 store 가 옛 값을 붙이지 않게 append_label·retract 가 매번 부른다(os.stat 한 번).
        다시 읽기가 실패하거나(검증 실패·다른 project_id) 하면 이전 값을 그대로 두고 경고 로그만 남긴다.
        바꿨으면 True.
        """
        stamp = self._project_stamp()
        if stamp is None or stamp == self._pj_stamp:
            return False
        self._pj_stamp = stamp
        p = projmod.open_project(self.root, machine4=self.session.machine4)
        old = self.project.data or {}
        if p.data is None or p.read_only or p.data.get("project_id") != old.get("project_id"):
            log.warning("바뀐 project.json 을 다시 읽지 못해 이전 값을 씁니다 (%d개 사유)", len(p.problems))
            return False
        self.project.data = p.data
        return True

    def _label_meta(self) -> dict:
        self.refresh_project()
        meta = self._meta or {}
        data = self.project.data or {}
        return {
            "guide_version": meta.get("guide_version"),
            "guide_sha12": str(meta.get("guide_doc_sha256", ""))[:12],
            "thresholds_sha12": str(meta.get("thresholds_sha256", ""))[:12],
            "media_rev": (data.get("media") or {}).get("media_rev"),
            "calibration_rev": (data.get("calibration") or {}).get("active_rev"),
        }

    def _new_uuid(self) -> str:
        u = self._uuid()
        if not isinstance(u, str) or not UUID4_RE.match(u):
            raise StoreError("uuid_factory 가 UUID v4 소문자를 주지 않았습니다")
        if u in self._by_id:
            raise StoreError("이미 있는 id 입니다(uuid 충돌)")
        return u

    # ------------------------------------------------------------------ 쓰기
    def _check_writable(self) -> None:
        if self.closed:
            raise ReadOnlyStoreError("닫은 store 입니다")
        if self.read_only:
            raise ReadOnlyStoreError("읽기 전용으로 연 세션입니다: " + "; ".join(self.read_only_reasons[:3]))

    def _append_record(self, rec: dict) -> dict:
        """검사 → 한 줄 쓰기 → 되읽기 확인 → 색인. 저장된 레코드(내부 객체)를 돌려준다."""
        self._check_writable()
        if self._schema is None:
            raise StoreError("기록 스키마가 없습니다")
        problems = self._schema.check(rec)
        if problems:
            raise LabelValidationError(problems)
        line = jsonl.dumps_line(rec)
        if len(line) > MAX_LINE_BYTES:
            raise LabelValidationError([f"$: 한 줄이 {MAX_LINE_BYTES} 바이트를 넘습니다 ({len(line)})"])
        own = self._own
        assert own is not None
        os.makedirs(os.path.dirname(own.path), exist_ok=True)
        jsonl.append(own.path, rec)
        # 되읽기: 파일 끝 len(line) 바이트가 방금 쓴 줄과 같아야 한다(파일 전체를 다시 읽지 않는다 — 30ms 목표)
        try:
            with builtins.open(own.path, "rb") as f:
                f.seek(-len(line), os.SEEK_END)
                back = f.read(len(line))
            ok = back == line and _eq(json.loads(back.decode("utf-8")), rec)
        except (OSError, ValueError):
            ok = False
        if not ok:
            self._set_ro("방금 쓴 줄을 되읽어 확인하지 못했습니다")
            raise WriteVerifyError("방금 쓴 줄을 되읽어 확인하지 못했습니다. 세션을 읽기 전용으로 바꿨습니다.")
        own.records.append(rec)
        self._index(rec)
        return rec

    def _timed_append(self, rec: dict) -> dict:
        t0 = time.perf_counter()
        out = self._append_record(rec)
        self.last_append_ms = (time.perf_counter() - t0) * 1000.0
        if self.last_append_ms > SAVE_TARGET_MS:
            log.warning("라벨 저장이 %.1fms 걸렸습니다(목표 %.0fms)", self.last_append_ms, SAVE_TARGET_MS)
        return copy.deepcopy(out)

    def _heads(self, eid: str) -> list[str]:
        return [i for i in self._versions.get(eid, []) if i not in self._superseded]

    def head(self, entity_id: str) -> dict | None:
        """대상의 현재 머리 판(철회 줄일 수도 있다). 없거나 갈라졌으면 None."""
        h = self._heads(entity_id)
        return copy.deepcopy(self._by_id[h[0]]) if len(h) == 1 else None

    def append_label(self, draft: dict) -> dict:
        """라벨 한 건 저장. draft = {kind, body, entity_id?, supersedes?, x?}. 저장된 레코드 사본을 돌려준다.

        - entity_id 를 주지 않으면 새 대상(새 uuid, supersedes null). 주면 그 대상을 고치는 새 판이고,
          supersedes 는 그 대상의 현재 머리 판 id 여야 한다(같은 세션·같은 대상·같은 종류, 철회된 대상 되살리기 포함).
        - 봉투·공통 라벨 메타는 store 가 채운다. 초안에 그 필드가 있으면 거부한다(UI 가 지우거나 바꿀 수 없다).
        - 거부: LabelValidationError(형식), ChainError(판 연결), ReadOnlyStoreError(읽기 전용).
        """
        self._check_writable()
        if not isinstance(draft, dict):
            raise LabelValidationError(["$: 초안이 object 가 아닙니다"])
        bad = sorted(set(draft) - DRAFT_FIELDS)
        if bad:
            auto = [k for k in bad if k in AUTO_FIELDS]
            raise LabelValidationError([f"$: store 가 채우는 필드라 초안에 넣을 수 없습니다 {k!r}" for k in auto]
                                       + [f"$: 모르는 필드 {k!r}" for k in bad if k not in AUTO_FIELDS])
        kind = draft.get("kind")
        if kind == "retract":
            raise LabelValidationError(["$.kind: 철회는 retract() 로 합니다"])
        if kind not in LABEL_KINDS:
            raise LabelValidationError([f"$.kind: 라벨 종류가 아닙니다 ({_show(kind)})"])
        self._check_kind_allowed(kind)
        body = draft.get("body")
        if not isinstance(body, dict):
            raise LabelValidationError(["$.body: object 가 아닙니다"])
        self._check_scope(kind, body)
        eid = draft.get("entity_id")
        sup = draft.get("supersedes")
        if eid is None:
            if sup is not None:
                raise ChainError("supersedes 를 주려면 entity_id 도 줘야 합니다")
            eid = self._new_uuid()
        else:
            self._check_chain(eid, sup, kind)
        rec = self._envelope(kind, copy.deepcopy(body))
        rec["entity_id"] = eid
        rec["supersedes"] = sup
        rec.update(self._label_meta())
        if "x" in draft:
            rec["x"] = copy.deepcopy(draft["x"])
        return self._timed_append(_ordered(rec))

    def _check_kind_allowed(self, kind: str) -> None:
        """03 12-1·02 3-3: 이 세션 종류가 만들 수 있는 라벨 종류는 app.core.visibility 표만 따른다."""
        sk = self.session.session_kind
        if vis.files_to_open(sk).can_write_label(kind):
            return
        if kind == "origin":
            raise LabelValidationError(["$.kind: origin 라벨은 origin_review 세션에서만 만듭니다(03 12-3)"])
        raise LabelValidationError([f"$.kind: {sk} 세션은 {kind} 기록을 만들 수 없습니다(03 12-1)"])

    def _check_scope(self, kind: str, body: dict) -> None:
        """03 12-2: session.open.scope 가 blocks 면 라벨의 시각이 범위 [시작, 끝) 하나 안에 들어야 한다.

        본문에 VOD 시각이 있는 종류만 본다(구간·발화·반응 시각·지속·영상 확인 창). 채팅 ID 만 가진 종류
        (missed_candidate·origin 등)는 store 가 채팅 시각을 모르므로 여기서 거르지 않는다(화면이 범위 안 채팅만 보인다).
        """
        scope = (self._meta or {}).get("scope")
        if not isinstance(scope, dict) or scope.get("type") != "blocks":
            return
        ranges = [r for r in scope.get("ranges") or []
                  if isinstance(r, list) and len(r) == 2 and all(_is_int(x) for x in r)]
        spans = _body_spans(kind, body)
        for a, b in spans:
            if not any(lo <= a and b <= hi and a < hi for lo, hi in ranges):
                raise LabelValidationError([f"$.body: 재라벨 범위(scope.blocks) 밖의 시각입니다 ({a}~{b}ms, 03 12-2)"])

    def _check_chain(self, eid: str, sup, kind: str) -> None:
        """03 10-1: supersedes 는 같은 세션·같은 대상·같은 종류(retract 제외)의 현재 판(머리)만 가리킨다."""
        if not isinstance(eid, str) or not UUID4_RE.match(eid):
            raise ChainError("entity_id 는 UUID v4 소문자여야 합니다")
        versions = self._versions.get(eid)
        if not versions:
            if sup is not None:
                raise ChainError("없는 대상의 판을 supersedes 할 수 없습니다")
            if eid in self._by_id:
                raise ChainError("entity_id 가 다른 줄의 id 와 같습니다")
            return                                  # 호출하는 쪽이 정한 새 대상 ID
        if sup is None:
            raise ChainError("이미 있는 대상입니다. 고치려면 supersedes 에 현재 판 id 를 주세요")
        heads = self._heads(eid)
        if len(heads) != 1:
            raise ChainError("판 연결이 갈라진(충돌) 대상입니다. 조정이 필요합니다")
        if sup != heads[0]:
            raise ChainError("supersedes 가 그 대상의 현재 판이 아닙니다(다른 곳에서 먼저 고쳤을 수 있습니다)")
        target = self._by_id[sup]
        ekind = self._entity_kind.get(eid)
        if target.get("kind") != "retract" and target.get("kind") != kind:
            raise ChainError("다른 종류의 판을 supersedes 할 수 없습니다")
        if ekind is not None and ekind != kind:
            raise ChainError("대상의 종류와 다른 종류로 되살릴 수 없습니다")

    def retract(self, label_id: str, reason: str, *, note: str | None = None,
                merged_into: str | None = None) -> dict:
        """대상을 철회한다(삭제 대신, 03 10-1). label_id 는 대상의 entity_id 또는 현재 머리 판 id.

        retract 줄: entity_id = 대상, supersedes = 현재 판. 이미 철회됐거나 머리가 아닌 판이면 ChainError.
        """
        self._check_writable()
        self._check_kind_allowed("retract")
        if reason not in RETRACT_REASONS:
            raise LabelValidationError([f"$.body.reason: 허용 값 {list(RETRACT_REASONS)} 밖입니다"])
        if label_id in self._versions:
            eid = label_id
        else:
            rec = self._by_id.get(label_id)
            if rec is None or rec.get("kind") not in ENTITY_KINDS:
                raise ChainError("철회할 라벨을 찾지 못했습니다")
            eid = rec["entity_id"]
        heads = self._heads(eid)
        if len(heads) != 1:
            raise ChainError("판 연결이 갈라진(충돌) 대상입니다. 조정이 필요합니다")
        head = self._by_id[heads[0]]
        if label_id != eid and label_id != head["id"]:
            raise ChainError("현재 판이 아닌 판은 철회할 수 없습니다(다른 곳에서 먼저 고쳤을 수 있습니다)")
        if head.get("kind") == "retract":
            raise ChainError("이미 철회된 대상입니다")
        if reason == "merged" and merged_into is None:
            raise LabelValidationError(["$.body.merged_into: reason 이 merged 면 합쳐진 대상이 필요합니다"])
        body: dict = {"reason": reason}
        if merged_into is not None:
            body["merged_into"] = merged_into
        if note is not None:
            body["note"] = note
        rec = self._envelope("retract", body)
        rec["entity_id"] = eid
        rec["supersedes"] = head["id"]
        rec.update(self._label_meta())
        return self._timed_append(_ordered(rec))

    def retract_last(self) -> dict:
        """Ctrl+Z (03 10-1, 02 3-3): 이 PC 가 이 세션에 마지막으로 쓴 라벨 줄을 한 줄로 되돌린다.

        방금 만든 대상 → retract(undo). 방금 고친 판 → 그 전 판의 body 로 새 판. 방금 철회 → 철회 전 판의 body 로 되살림.
        되돌린 줄도 '마지막 줄' 이 되므로 한 번 더 누르면 되돌리기를 되돌린다.
        """
        self._check_writable()
        last = next((r for r in reversed(self._own.records if self._own else [])
                     if r.get("kind") in ENTITY_KINDS), None)
        if last is None:
            raise StoreError("되돌릴 라벨이 없습니다")
        eid = last["entity_id"]
        if self._heads(eid) != [last["id"]]:
            raise ChainError("마지막 줄이 더는 그 대상의 현재 판이 아닙니다")
        if last.get("supersedes") is None:
            return self.retract(last["id"], "undo")
        prev = self._by_id.get(last["supersedes"])
        if prev is None:
            raise ChainError("이전 판을 찾지 못했습니다")
        if prev.get("kind") == "retract":           # 되살린 판을 되돌림 → 다시 철회
            return self.retract(last["id"], "undo")
        draft = {"kind": prev["kind"], "body": prev["body"], "entity_id": eid, "supersedes": last["id"]}
        if "x" in prev:
            draft["x"] = prev["x"]
        return self.append_label(draft)

    def append_ops(self, kind: str, body: dict) -> dict:
        """세션 운영 기록 한 줄(run.start·phase.mark·view.exposed·warning, 03 6-4). 저장된 레코드 사본."""
        self._check_writable()
        if kind not in OPS_KINDS:
            raise LabelValidationError([f"$.kind: 운영 기록 종류가 아닙니다 ({_show(kind)})"])
        if not isinstance(body, dict):
            raise LabelValidationError(["$.body: object 가 아닙니다"])
        return self._timed_append(self._envelope(kind, copy.deepcopy(body)))

    def update_project(self, mutate: Callable[[dict], None]) -> dict:
        """project.json 을 rev 비교 후 저장(8-3). 읽기 전용이면 ReadOnlyStoreError."""
        self._check_writable()
        return self.project.update(mutate, by=self.session.labeler_id)

    # ------------------------------------------------------------------ 조회
    def records(self) -> list[dict]:
        """이 세션의 모든 기록(이 PC 파일 먼저, 그다음 다른 PC 파일, 파일 안은 seq 순) 사본."""
        out: list[dict] = []
        for sf in sorted(self._files, key=lambda f: (not f.own, f.name)):
            out += sf.records
        return copy.deepcopy(out)

    def effective(self, *, copy_records: bool = True):
        """현재 유효 라벨 뷰 = app.core.resolve.resolve(records()) (03 11장).

        copy_records=False 면 사본을 만들지 않고 내부 기록을 그대로 resolve 에 넘기고, 기록이 바뀌지 않았으면 지난 결과를
        다시 준다(라벨 저장마다 화면을 다시 그리는 길, 02 8장 16ms). resolve 는 입력 dict 를 고치지 않는다. 받은 뷰의
        dict 는 store 의 내부 객체이므로 **고치지 말 것**(읽기 전용). 고칠 일이 있으면 기본값(사본)을 쓴다.
        """
        from .core.resolve import resolve
        if copy_records:
            return resolve(self.records())
        c = self._eff_cache
        if c is not None and c[0] == self._rev:
            return c[1]
        recs: list[dict] = []
        for sf in sorted(self._files, key=lambda f: (not f.own, f.name)):
            recs += sf.records
        view = resolve(recs)
        self._eff_cache = (self._rev, view)
        return view

    # ------------------------------------------------------------------ 닫기
    def close(self) -> None:
        """잠금을 풀고 닫는다. 그 뒤로는 쓸 수 없다. 여러 번 불러도 된다.

        session.close 줄은 쓰지 않는다: label-event 스키마의 kind 목록에 없어, 쓰면 다음에 열 때 이 앱이
        그 세션을 '스키마 위반' 으로 읽기 전용으로 연다(보고서 '설계와 다르게 한 곳' 참고).
        """
        if self.closed:
            return
        self.closed = True
        self._release_lock()

    def __enter__(self) -> "ProjectStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------

_KEY_ORDER = ("v", "id", "seq", "kind", "session_id", "labeler_id", "created_at", "app_version",
              "entity_id", "supersedes", "guide_version", "guide_sha12", "thresholds_sha12",
              "media_rev", "calibration_rev", "body", "x")


def _ordered(rec: dict) -> dict:
    """사람이 메모장으로 볼 때 읽기 쉬운 키 순서(봉투 → 메타 → body)."""
    return {k: rec[k] for k in _KEY_ORDER if k in rec}


# 03 12-2 범위 검사에 쓰는 본문 시각 필드: (시작, 끝) 쌍 또는 한 점
_SPAN_FIELDS = {
    "segment": [("start_ms", "end_ms")],
    "utterance": [("start_ms", "end_ms")],
    "continuation": [("span_start_ms", "span_end_ms")],
    "video_check": [("window_start_ms", "window_end_ms")],
}
_POINT_FIELDS = {"reaction": ["reaction_time_ms"]}


def _body_spans(kind: str, body: dict) -> list[tuple[int, int]]:
    """본문의 VOD 시각을 [시작, 끝) 목록으로. 한 점 t 는 (t, t+1). 정수가 아닌 값은 건너뛴다(스키마 검사가 잡는다)."""
    out = []
    for a, b in _SPAN_FIELDS.get(kind, ()):
        s, e = body.get(a), body.get(b)
        if _is_int(s) and _is_int(e):
            out.append((s, max(e, s + 1)))
    for a in _POINT_FIELDS.get(kind, ()):
        t = body.get(a)
        if _is_int(t):
            out.append((t, t + 1))
    return out


def _guide(data: dict, version) -> dict | None:
    for g in data.get("guides") or []:
        if isinstance(g, dict) and g.get("guide_version") == version:
            return g
    return None


def _default_lock_factory():
    """기본 잠금 = PySide6.QtCore.QLockFile (QtCore 만, 위젯 아님).

    입출력 층은 PySide6 를 import 문으로 들이지 않는다(tests/test_layers.py). 잠금은 원래 호출하는 쪽(ui·session)이
    주입하는 것이 원칙이고(02 3-2), 이 기본값은 주입을 잊었을 때 잠금 없이 쓰지 않게 하려는 안전장치라
    처음 쓸 때 importlib 로 늦게 부른다. PySide6 가 없으면 ImportError → open 이 읽기 전용으로 연다.
    """
    import importlib
    return importlib.import_module("PySide6.QtCore").QLockFile


def _lock_holder(lk) -> str:
    """QLockFile.getLockInfo() 결과에서 호스트·프로그램 이름(PySide6 는 (pid, host, app) 튜플)."""
    try:
        info = lk.getLockInfo() if hasattr(lk, "getLockInfo") else None
    except Exception:  # noqa: BLE001
        info = None
    if not isinstance(info, (tuple, list)):
        return ""
    strs = [x for x in info if isinstance(x, str) and x]
    if len(strs) >= 2:
        return f" (호스트 {strs[0]}, 프로그램 {strs[1]})"
    if strs:
        return f" (호스트 {strs[0]})"
    return ""


open_store = ProjectStore.open
