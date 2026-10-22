"""가상 채팅 시나리오 파일 읽기·쓰기 — 프로젝트 `scenarios\\<scenario_id>.jsonl` (20 3장 H-1, 08 D-05·D-19 2).

- 실제 채팅 파일(chat\\messages.jsonl)·labels\\·samples\\·origin\\ 와 다른 폴더다. 이 모듈은 scenarios\\ 밖에 쓰지 않는다.
- 쓰기는 원자적: 같은 폴더의 임시 파일에 다 쓰고 fsync → 이름 바꾸기(이미 있으면 덮어쓰지 않는다). 쓰다 끊기면
  임시 파일만 남고(다음 쓰기 때 지운다) 시나리오 이름의 반쪽 파일은 생기지 않는다.
- 한 번 쓴 시나리오는 고치지 않는다. 같은 프로젝트·구간·lines_sha256 인 시나리오가 이미 있으면 새로 쓰지 않고 그 ID 를
  돌려준다(origin 대상 파일과 같은 방식).
- 쓰기 전·읽은 뒤 모두 schemas/scenario.schema.json(store.LabelSchema 해석기) + app.core.scenario.check_records 로
  검사한다. 끝 줄(scenario.end)이 없거나, 깨진 줄·BOM·스키마 밖 칸·다른 kind 가 있으면 열지 않는다.
- 시범 프로젝트(pilot-* 등)는 시범 세션이 모두 잠기기 전에는 읽지도 쓰지도 않는다(D-18 7, 20 1장 8번). 판단은
  reference_set.pilot_block_reason 이 하고, 이 모듈은 그때 scenarios\\ 를 열지 않는다.
- 오류 메시지에는 사유·건수만. 본문·이름은 넣지 않는다(D-17 8).
- 이 모듈은 네트워크를 쓰지 않는다. 앱 밖으로 내보내는 기능이 없다(D-05).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from typing import Mapping, Sequence

from . import jsonl, paths
from .core import scenario as sc
from .core.validate import load_json_strict

SCHEMA_REL = ("schemas", "scenario.schema.json")
SCHEMA_MAX_BYTES = 1 << 20
SCENARIO_DIR = "scenarios"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
PROJECT_JSON_MAX_BYTES = 4 * 1024 * 1024
TMP_SUFFIX = ".tmp"

# 읽기 실패 사유 코드 (숫자·코드만 보인다)
R_MISSING = "missing"
R_TOO_LARGE = "too_large"
R_BOM = "bom"
R_CORRUPT = "corrupt"
R_TRUNCATED = "truncated"
R_INVALID = "invalid"
R_NAME = "name_mismatch"
R_PILOT = "pilot_locked"


class ScenarioStoreError(RuntimeError):
    pass


class ScenarioBlockedError(ScenarioStoreError, PermissionError):
    """시범 프로젝트에서 시범 세션이 모두 잠기기 전 (D-18 7)."""


class ScenarioInvalidError(ScenarioStoreError, ValueError):
    """시나리오 줄이 형식·규칙에 맞지 않음. problems 에 사유(본문 없음)."""

    def __init__(self, msg: str, problems: Sequence[str] = (), code: str = R_INVALID):
        super().__init__(msg)
        self.problems = list(problems)
        self.code = code


@dataclass
class Scenario:
    """읽은 시나리오. records = [header, chat…, end] (파일 그대로)."""
    path: str
    records: list[dict]
    sha256: str                   # 파일 바이트 SHA-256 (training.start 의 scenario_sha256)
    canonical: bool = True        # 파일 바이트가 정규 직렬화와 같은가(앱이 쓴 파일이면 True)

    @property
    def header(self) -> dict:
        return self.records[0]

    @property
    def chats(self) -> list[dict]:
        return self.records[1:-1]

    @property
    def end(self) -> dict:
        return self.records[-1]

    @property
    def scenario_id(self) -> str:
        return self.header["scenario_id"]

    @property
    def range_ms(self) -> tuple[int, int]:
        r = self.header["range"]
        return r["vod_from_ms"], r["vod_to_ms"]


@dataclass
class WriteResult:
    scenario_id: str
    path: str
    created: bool                 # False = 같은 시나리오가 이미 있어 쓰지 않음
    sha256: str


# ---------------------------------------------------------------- 스키마

_schema = None


def schema_path() -> str:
    return os.path.join(paths.code_dir(), *SCHEMA_REL)


def load_schema():
    global _schema
    if _schema is None:
        from .store import LabelSchema
        with open(schema_path(), "rb") as f:
            _schema = LabelSchema(load_json_strict(f.read(SCHEMA_MAX_BYTES + 1), SCHEMA_MAX_BYTES))
    return _schema


def check_record(rec) -> list[str]:
    """한 줄 스키마 검사. 문제 목록(빈 목록이면 통과)."""
    return load_schema().check(rec)


def validate(records: Sequence[Mapping], *, project_id: str | None = None) -> list[str]:
    """줄마다 스키마 + 줄 사이 규칙(core.scenario.check_records)."""
    return sc.check_records(records, schema_check=check_record, project_id=project_id)


# ---------------------------------------------------------------- 경로

def scenarios_dir(project_root: str) -> str:
    return os.path.join(os.path.abspath(project_root), SCENARIO_DIR)


def scenario_path(project_root: str, scenario_id: str) -> str:
    if not isinstance(scenario_id, str) or not sc.SCENARIO_ID_RE.match(scenario_id):
        raise ScenarioStoreError("시나리오 ID 형식이 아닙니다")
    return os.path.join(scenarios_dir(project_root), f"{scenario_id}.jsonl")


def list_scenarios(project_root: str, *, check_pilot: bool = True, pilot_dir: str | None = None) -> list[str]:
    """scenarios\\ 아래 시나리오 ID(이름 순). 내용은 열지 않는다. 시범이 잠기기 전이면 빈 목록."""
    if check_pilot and _pilot_reason(project_root, pilot_dir):
        return []
    try:
        names = os.listdir(scenarios_dir(project_root))
    except OSError:
        return []
    return sorted(n[:-6] for n in names if n.endswith(".jsonl") and sc.SCENARIO_ID_RE.match(n[:-6]))


def _pilot_reason(project_root: str, pilot_dir: str | None) -> str | None:
    from . import reference_set
    return reference_set.pilot_block_reason(os.path.abspath(project_root), pilot_dir=pilot_dir)


def _project_id(project_root: str) -> str | None:
    """project.json 의 project_id (없거나 못 읽으면 None)."""
    p = os.path.join(os.path.abspath(project_root), "project.json")
    try:
        with open(p, "rb") as f:
            raw = f.read(PROJECT_JSON_MAX_BYTES + 1)
        if len(raw) > PROJECT_JSON_MAX_BYTES:
            return None
        doc = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    pid = doc.get("project_id") if isinstance(doc, dict) else None
    return pid if isinstance(pid, str) else None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- 읽기

def read_scenario(path: str, *, project_id: str | None = None) -> Scenario:
    """시나리오 파일 하나를 읽고 검사한다. 맞지 않으면 ScenarioInvalidError(code). 시범 판단은 하지 않는다 —
    프로젝트 단위로 열 때는 load_scenario·latest_scenario 를 쓴다."""
    try:
        size = os.path.getsize(path)
    except OSError:
        raise ScenarioInvalidError("시나리오 파일이 없습니다", code=R_MISSING) from None
    if size > MAX_FILE_BYTES:
        raise ScenarioInvalidError(f"시나리오 파일이 너무 큽니다({size}바이트)", code=R_TOO_LARGE)
    with open(path, "rb") as f:
        data = f.read(MAX_FILE_BYTES + 1)
    if data.startswith(b"\xef\xbb\xbf"):
        raise ScenarioInvalidError("시나리오 파일 앞에 BOM 이 있습니다", code=R_BOM)
    res = jsonl.read(path, max_line_bytes=MAX_LINE_BYTES)
    if res.corrupt_lines:
        raise ScenarioInvalidError(f"깨진 줄이 있습니다({len(res.corrupt_lines)}줄)", code=R_CORRUPT)
    if res.truncated_tail or not data.endswith(b"\n"):
        raise ScenarioInvalidError("마지막 줄이 잘렸습니다(쓰다 끊긴 파일)", code=R_TRUNCATED)
    problems = validate(res.records, project_id=project_id)
    if problems:
        code = R_TRUNCATED if any("scenario.end" in p for p in problems) else R_INVALID
        raise ScenarioInvalidError(f"시나리오 형식 오류 {len(problems)}건", problems, code=code)
    name = os.path.basename(path)
    if name != f"{res.records[0]['scenario_id']}.jsonl":
        raise ScenarioInvalidError("파일 이름과 scenario_id 가 다릅니다", code=R_NAME)
    canon = b"".join(sc.canonical_line(r) for r in res.records)
    return Scenario(path=os.path.abspath(path), records=res.records, sha256=_sha256_bytes(data),
                    canonical=(canon == data))


def load_scenario(project_root: str, scenario_id: str, *, check_pilot: bool = True,
                  pilot_dir: str | None = None) -> Scenario:
    """프로젝트의 시나리오 하나. 시범이 잠기기 전이면 ScenarioBlockedError(파일을 열지 않는다)."""
    if check_pilot:
        why = _pilot_reason(project_root, pilot_dir)
        if why:
            raise ScenarioBlockedError(why)
    return read_scenario(scenario_path(project_root, scenario_id), project_id=_project_id(project_root))


def latest_scenario(project_root: str, range_ms: tuple[int, int] | None = None, *, check_pilot: bool = True,
                    pilot_dir: str | None = None) -> tuple[Scenario | None, dict]:
    """이 프로젝트(와 구간)의 가장 새 시나리오(header.created_at, 같으면 ID 순 뒤). 고르는 창 없이 쓴다(20 5-2).

    (시나리오 또는 None, 숫자 {files, ok, broken, other_range, blocked}). 깨진 파일은 건너뛰고 수만 센다.
    """
    counts = {"files": 0, "ok": 0, "broken": 0, "other_range": 0, "blocked": 0}
    if check_pilot and _pilot_reason(project_root, pilot_dir):
        counts["blocked"] = 1
        return None, counts
    pid = _project_id(project_root)
    best: Scenario | None = None
    best_key = None
    for sid in list_scenarios(project_root, check_pilot=False):
        counts["files"] += 1
        try:
            s = read_scenario(scenario_path(project_root, sid), project_id=pid)
        except (ScenarioInvalidError, OSError):
            counts["broken"] += 1
            continue
        if range_ms is not None and tuple(range_ms) != s.range_ms:
            counts["other_range"] += 1
            continue
        counts["ok"] += 1
        key = (s.header.get("created_at", ""), sid)
        if best_key is None or key > best_key:
            best, best_key = s, key
    return best, counts


# ---------------------------------------------------------------- 쓰기

def _same_scenario(a: Mapping, b: Scenario) -> bool:
    h = b.header
    return (a.get("project_id") == h.get("project_id") and a.get("range") == h.get("range"))


def _cleanup_tmp(d: str) -> None:
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        if n.startswith("SC-") and n.endswith(TMP_SUFFIX):
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass


def _publish(tmp: str, dest: str) -> None:
    """tmp → dest. dest 가 이미 있으면 FileExistsError (덮어쓰지 않는다)."""
    try:
        os.link(tmp, dest)            # 원자적, 있으면 실패
    except FileExistsError:
        raise
    except (OSError, NotImplementedError, AttributeError):
        # 하드링크가 안 되는 곳: Windows 의 os.rename 은 대상이 있으면 실패한다(덮어쓰지 않음).
        if os.name != "nt" and os.path.exists(dest):
            raise FileExistsError(dest) from None
        os.rename(tmp, dest)
        return
    try:
        os.remove(tmp)
    except OSError:
        pass


def write_scenario(project_root: str, records: Sequence[Mapping], *, check_pilot: bool = True,
                   pilot_dir: str | None = None) -> WriteResult:
    """시나리오 줄 목록(core.scenario.assemble 결과)을 scenarios\\<scenario_id>.jsonl 로 쓴다.

    - 검사(스키마 + 규칙 + project.json 의 project_id)를 통과해야 쓴다. 아니면 ScenarioInvalidError.
    - 같은 프로젝트·구간·lines_sha256 인 시나리오가 있으면 쓰지 않고 그 ID(created=False).
    - 같은 이름(같은 날·같은 해시 앞 4자)인데 내용이 다르면 ScenarioStoreError(덮어쓰지 않는다).
    """
    root = os.path.abspath(project_root)
    if check_pilot:
        why = _pilot_reason(root, pilot_dir)
        if why:
            raise ScenarioBlockedError(why)
    recs = [dict(r) for r in records]
    problems = validate(recs, project_id=_project_id(root))
    if problems:
        raise ScenarioInvalidError(f"시나리오 형식 오류 {len(problems)}건", problems)
    header, end = recs[0], recs[-1]
    sid = header["scenario_id"]
    data = b"".join(sc.canonical_line(r) for r in recs)
    if len(data) > MAX_FILE_BYTES:
        raise ScenarioInvalidError(f"시나리오가 너무 큽니다({len(data)}바이트)", code=R_TOO_LARGE)

    d = scenarios_dir(root)
    os.makedirs(d, exist_ok=True)
    _cleanup_tmp(d)
    for other in list_scenarios(root, check_pilot=False):
        try:
            s = read_scenario(scenario_path(root, other))
        except (ScenarioInvalidError, OSError):
            continue
        if s.end.get("lines_sha256") == end["lines_sha256"] and _same_scenario(header, s):
            return WriteResult(scenario_id=s.scenario_id, path=s.path, created=False, sha256=s.sha256)

    dest = scenario_path(root, sid)
    if os.path.exists(dest):
        raise ScenarioStoreError("같은 이름의 다른 시나리오가 이미 있습니다(덮어쓰지 않습니다)")
    tmp = os.path.join(d, f"{sid}.{os.getpid()}-{secrets.token_hex(4)}{TMP_SUFFIX}")
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            _publish(tmp, dest)
        except FileExistsError:
            raise ScenarioStoreError("같은 이름의 다른 시나리오가 이미 있습니다(덮어쓰지 않습니다)") from None
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return WriteResult(scenario_id=sid, path=dest, created=True, sha256=_sha256_bytes(data))


__all__ = [
    "Scenario", "WriteResult", "ScenarioStoreError", "ScenarioBlockedError", "ScenarioInvalidError",
    "schema_path", "load_schema", "check_record", "validate", "scenarios_dir", "scenario_path", "list_scenarios",
    "read_scenario", "load_scenario", "latest_scenario", "write_scenario", "SCENARIO_DIR",
    "R_MISSING", "R_TOO_LARGE", "R_BOM", "R_CORRUPT", "R_TRUNCATED", "R_INVALID", "R_NAME", "R_PILOT",
]
