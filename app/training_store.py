"""훈련 기록 쓰기·읽기 — 프로젝트 `training\\<session_id>.jsonl` (08 D-18 8, 18 6-3, D-05).

- 라벨 기록(labels\\, label-event)과 다른 폴더·다른 종류다. 정답지에 섞이지 않는다.
- 한 줄 = 기록 1건. 덧붙이기는 app.jsonl.append(한 줄을 os.write 한 번 + fsync, 잘린 꼬리 뒤에는 줄바꿈부터)로 한다.
- 쓰기 전에 schemas/training-event.schema.json 으로 검사한다(store.LabelSchema 해석기). 맞지 않으면 쓰지 않는다.
- 채팅 본문·닉네임·전사문은 칸이 없다(스키마가 모르는 칸을 거부한다). chat_id·VOD 시각·숫자만.
- 세션 ID: '<labeler_id>-TR-<yyyymmdd>-<4자>' (라벨 세션 종류 코드와 겹치지 않는다).
- 시범 프로젝트(pilot-* 등)는 시범 세션이 모두 잠기기 전에는 새 세션을 만들지 않는다(D-18 7, reference_set).

이 모듈은 labels\\ 를 열지 않는다. 시계·난수는 바꿔 끼울 수 있다(시험).
"""
from __future__ import annotations

import os
import re
import secrets
import string
import uuid
from datetime import datetime
from typing import Callable, Iterable, Mapping

from . import jsonl, paths
from .core import training as tr
from .core.validate import load_json_strict

SCHEMA_REL = ("schemas", "training-event.schema.json")
SCHEMA_MAX_BYTES = 1 << 20
RECORD_V = 1
TRAINING_DIR = "training"
MAX_LINE_BYTES = 64 * 1024
TRAINING_SESSION_ID_RE = re.compile(r"\A([A-Z][A-Z0-9]{1,7})-TR-([0-9]{8})-([a-z0-9]{4})\Z")
LABELER_ID_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
_RAND_ALPHABET = string.ascii_lowercase + string.digits
END_REASONS = ("compare", "segment_end", "closed")


class TrainingStoreError(RuntimeError):
    pass


class TrainingBlockedError(TrainingStoreError, PermissionError):
    """시범 프로젝트에서 시범 세션이 모두 잠기기 전 (D-18 7)."""


class TrainingRecordError(TrainingStoreError, ValueError):
    """쓰려는 줄이 training-event 스키마에 맞지 않음. 메시지에 본문은 넣지 않는다(경로·사유만)."""


_schema = None


def schema_path() -> str:
    return os.path.join(paths.code_dir(), *SCHEMA_REL)


def load_schema():
    """training-event 스키마 해석기(store.LabelSchema 를 그대로 쓴다)."""
    global _schema
    if _schema is None:
        from .store import LabelSchema
        with open(schema_path(), "rb") as f:
            _schema = LabelSchema(load_json_strict(f.read(SCHEMA_MAX_BYTES + 1), SCHEMA_MAX_BYTES))
    return _schema


def check_record(rec) -> list[str]:
    """문제 목록(빈 목록이면 통과)."""
    return load_schema().check(rec)


def training_dir(project_root: str) -> str:
    return os.path.join(os.path.abspath(project_root), TRAINING_DIR)


def session_path(project_root: str, session_id: str) -> str:
    if not isinstance(session_id, str) or not TRAINING_SESSION_ID_RE.match(session_id):
        raise TrainingStoreError("훈련 세션 ID 형식이 아닙니다")
    return os.path.join(training_dir(project_root), f"{session_id}.jsonl")


def list_sessions(project_root: str) -> list[str]:
    """training\\ 아래 훈련 세션 ID (이름 순). 내용은 열지 않는다."""
    try:
        names = os.listdir(training_dir(project_root))
    except OSError:
        return []
    out = []
    for n in names:
        if n.endswith(".jsonl") and TRAINING_SESSION_ID_RE.match(n[:-6]):
            out.append(n[:-6])
    return sorted(out)


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(d: datetime) -> str:
    if d.tzinfo is None:
        d = d.astimezone()
    return d.isoformat(timespec="milliseconds")


def _rand4() -> str:
    return "".join(secrets.choice(_RAND_ALPHABET) for _ in range(4))


def _app_version() -> str:
    try:
        from . import __version__
        return __version__
    except ImportError:
        return "0.0.0"


def new_session_id(labeler_id: str, now: datetime, rand4: str) -> str:
    sid = f"{labeler_id}-TR-{now.strftime('%Y%m%d')}-{rand4}"
    if not TRAINING_SESSION_ID_RE.match(sid):
        raise TrainingStoreError("훈련 세션 ID 를 만들지 못했습니다(라벨러 코드 형식)")
    return sid


def read_session(path: str) -> jsonl.ReadResult:
    """훈련 기록 파일을 읽는다(app.jsonl.read). 형식 검사는 하지 않는다 — check_record 로 따로."""
    return jsonl.read(path, max_line_bytes=MAX_LINE_BYTES)


def read_valid(path: str) -> tuple[list[dict], dict]:
    """(스키마를 통과한 줄, 숫자 요약 {lines, invalid, corrupt, truncated_tail})."""
    res = read_session(path)
    good, bad = [], 0
    for r in res.records:
        if check_record(r):
            bad += 1
        else:
            good.append(r)
    return good, {"lines": len(res.records), "invalid": bad, "corrupt": len(res.corrupt_lines),
                  "truncated_tail": res.truncated_tail}


class TrainingLog:
    """훈련 세션 하나의 기록 파일. 한 번에 한 앱만 쓴다(같은 세션을 두 곳에서 열지 않는다 — 세션 ID 가 매번 새로)."""

    def __init__(self, project_root: str, session_id: str, labeler_id: str, *,
                 clock: Callable[[], datetime] = _now, uuid_factory: Callable[[], str] | None = None,
                 app_version: str | None = None):
        if not isinstance(labeler_id, str) or not LABELER_ID_RE.match(labeler_id):
            raise TrainingStoreError("라벨러 코드 형식이 아닙니다")
        m = TRAINING_SESSION_ID_RE.match(session_id or "")
        if not m or m.group(1) != labeler_id:
            raise TrainingStoreError("훈련 세션 ID 와 라벨러 코드가 맞지 않습니다")
        self.project_root = os.path.abspath(project_root)
        self.session_id = session_id
        self.labeler_id = labeler_id
        self.path = session_path(self.project_root, session_id)
        self._clock = clock
        self._uuid = uuid_factory or (lambda: str(uuid.uuid4()))
        self._app_version = app_version or _app_version()
        self._seq = 0
        self.picks: dict[str, int] = {}
        if os.path.exists(self.path):
            res = read_session(self.path)
            if res.corrupt_lines:
                raise TrainingStoreError(f"훈련 기록 중간에 깨진 줄이 있습니다({len(res.corrupt_lines)}줄)")
            seqs = [r.get("seq") for r in res.records if isinstance(r.get("seq"), int)]
            self._seq = max(seqs, default=0)
            self.picks = tr.picks_from_events(res.records)

    # ---------------------------------------------------------------- 만들기
    @classmethod
    def create(cls, project_root: str, labeler_id: str, *, clock: Callable[[], datetime] = _now,
               rand4: Callable[[], str] = _rand4, uuid_factory: Callable[[], str] | None = None,
               app_version: str | None = None, check_pilot: bool = True,
               pilot_dir: str | None = None) -> "TrainingLog":
        """새 훈련 세션. training\\ 폴더를 만든다(없으면). 같은 이름이 이미 있으면 무작위 4자를 다시 뽑는다."""
        root = os.path.abspath(project_root)
        if check_pilot:
            from . import reference_set
            why = reference_set.pilot_block_reason(root, pilot_dir=pilot_dir)
            if why:
                raise TrainingBlockedError(why)
        if not isinstance(labeler_id, str) or not LABELER_ID_RE.match(labeler_id):
            raise TrainingStoreError("라벨러 코드 형식이 아닙니다")
        os.makedirs(training_dir(root), exist_ok=True)
        now = clock()
        for _ in range(20):
            sid = new_session_id(labeler_id, now, rand4())
            p = session_path(root, sid)
            try:
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
            except FileExistsError:
                continue
            os.close(fd)
            return cls(root, sid, labeler_id, clock=clock, uuid_factory=uuid_factory, app_version=app_version)
        raise TrainingStoreError("훈련 세션 이름을 만들지 못했습니다")

    # ---------------------------------------------------------------- 쓰기
    def _write(self, kind: str, body: dict, now: datetime | None = None) -> dict:
        rec = {"v": RECORD_V, "id": self._uuid(), "seq": self._seq + 1, "kind": kind, "session_id": self.session_id,
               "labeler_id": self.labeler_id, "created_at": _iso(now or self._clock()),
               "app_version": self._app_version, "body": body}
        problems = check_record(rec)
        if problems:
            raise TrainingRecordError(f"훈련 기록 형식 오류 {len(problems)}건: " + "; ".join(problems[:3]))
        jsonl.append(self.path, rec)
        self._seq += 1
        return rec

    def start(self, *, project_id: str, range_ms: tuple[int, int], reference: Mapping) -> dict:
        """training.start. reference = ReferenceSet.log_reference()."""
        a, b = range_ms
        return self._write(tr.K_START, {"mode": "streamer", "project_id": project_id, "source": "replay",
                                        "audio": "muted", "range": {"start_ms": a, "end_ms": b},
                                        "reference": dict(reference)})

    def toggle(self, chat_id: str, vod_ms: int) -> dict:
        """채팅 한 번 누름 → pick 또는 unpick 줄. 쓴 줄을 돌려준다(줄을 쓴 뒤에만 상태를 바꾼다)."""
        nxt, kind = tr.toggle_pick(self.picks, chat_id, vod_ms)
        now = self._clock()
        if kind == tr.K_PICK:
            body = {"chat_id": chat_id, "picked_vod_ms": vod_ms, "wall_at": _iso(now), "source": "replay"}
        else:
            body = {"chat_id": chat_id, "vod_ms": vod_ms, "wall_at": _iso(now)}
        rec = self._write(kind, body, now)
        self.picks = nxt
        return rec

    def compare(self, result: tr.Comparison, reference_sha256: str, range_ms: tuple[int, int]) -> dict:
        """training.compare — 숫자만(건수·맞힌 비율·시각 차 중앙값·기준 해시)."""
        a, b = range_ms
        body = {**result.summary(), "reference_sha256": reference_sha256, "range": {"start_ms": a, "end_ms": b},
                "until_ms": result.until_ms}
        return self._write(tr.K_COMPARE, body)

    def end(self, reason: str = "closed") -> dict:
        if reason not in END_REASONS:
            raise TrainingStoreError("모르는 끝 사유입니다")
        return self._write(tr.K_END, {"reason": reason, "picks": len(self.picks)})


def load_records(project_root: str, session_ids: Iterable[str] | None = None) -> list[dict]:
    """여러 훈련 세션의 통과한 줄 (세션 ID → seq 순). 노출 계산 등에서 쓴다."""
    out = []
    for sid in session_ids if session_ids is not None else list_sessions(project_root):
        p = session_path(project_root, sid)
        if not os.path.isfile(p):
            continue
        recs, _ = read_valid(p)
        out.extend(sorted(recs, key=lambda r: r.get("seq", 0)))
    return out


__all__ = [
    "TrainingLog", "TrainingStoreError", "TrainingBlockedError", "TrainingRecordError", "check_record",
    "load_schema", "schema_path", "training_dir", "session_path", "list_sessions", "new_session_id",
    "read_session", "read_valid", "load_records", "TRAINING_SESSION_ID_RE", "TRAINING_DIR", "END_REASONS",
]
