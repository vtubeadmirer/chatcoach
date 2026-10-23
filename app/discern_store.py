"""가상 채팅 가려내기 기록 쓰기·읽기 — 프로젝트 `discern\\<session_id>.jsonl` (H-5, 20 7장, 25 6-2, 03 6-19, 08 D-24 7 가).

- 라벨 기록(labels\\)·스트리머 훈련(training\\)·시청자 훈련(viewer\\)과 다른 폴더·다른 종류다. 정답지에도, 노출
  계산(training\\ 만 본다)에도 섞이지 않는다 — 가려내기는 원래 반응·사람 라벨·엔진 판정을 보이지 않는다.
- 한 줄 = 기록 1건(start · judge · end). 덧붙이기는 app.jsonl.append(한 줄을 os.write 한 번 + fsync, 잘린 꼬리 뒤에는
  줄바꿈부터). 쓰기 전에 schemas/discern-event.schema.json 으로 검사한다(store.LabelSchema 해석기). 맞지 않으면 쓰지 않는다.
- **판정 줄에는 chat_id 또는 scenario_id·line_id, 판정, 누른 순간의 VOD 시각, 벽시계만** 적는다. 본문·닉네임·가림 이름·
  출처(정답)는 칸이 없다(스키마가 모르는 칸을 거부한다). start·end 줄은 ID·해시·숫자만.
- 세션 ID: '<labeler_id>-DS-<yyyymmdd>-<4자>' (라벨 세션 종류 코드·TR·VW 와 겹치지 않는다).
- 시범 프로젝트(pilot-* 등)는 시범 세션이 모두 잠기기 전에는 새 세션을 만들지 않는다(폴더를 만들기 **전에** 막음, D-18 7).
- '본 적 있음'(20 7-3)의 입력 watched(): 그 라벨러의 스트리머 훈련·시청자 훈련·가려내기 start 줄의 구간과, labels\\ 의
  **파일 이름**(그 라벨러의 세션이 있으면 방송 전체를 본 것으로 친다 — 내용은 읽지 않는다). '다시 봄' 의 입력 prior().

이 모듈은 Qt 를 쓰지 않고 labels\\ 의 내용·scenarios\\ 를 열지 않는다. 시계·난수는 바꿔 끼울 수 있다(시험).
오류 메시지·로그에는 경로·사유·숫자만.
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
from .core import discern as dc
from .core.validate import load_json_strict

SCHEMA_REL = ("schemas", "discern-event.schema.json")
SCHEMA_MAX_BYTES = 1 << 20
RECORD_V = 1
DISCERN_DIR = "discern"
MAX_LINE_BYTES = 64 * 1024
K_START = "discern.start"
K_JUDGE = "discern.judge"
K_END = "discern.end"
KINDS = (K_START, K_JUDGE, K_END)
END_REASONS = ("segment_end", "finish", "closed")
DISCERN_SESSION_ID_RE = re.compile(r"\A([A-Z][A-Z0-9]{1,7})-DS-([0-9]{8})-([a-z0-9]{4})\Z")
LABELER_ID_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
CHAT_ID_RE = re.compile(r"\Ac[0-9a-f]{20}(_[0-9]+)?\Z")
SCENARIO_ID_RE = re.compile(r"\ASC-[0-9]{8}-[0-9a-f]{4}\Z")
LINE_ID_RE = re.compile(r"\As[0-9]{4,6}\Z")
_RAND_ALPHABET = string.ascii_lowercase + string.digits


class DiscernStoreError(RuntimeError):
    pass


class DiscernBlockedError(DiscernStoreError, PermissionError):
    """시범 프로젝트에서 시범 세션이 모두 잠기기 전 (D-18 7)."""


class DiscernRecordError(DiscernStoreError, ValueError):
    """쓰려는 줄이 discern-event 스키마에 맞지 않음. 메시지에 본문은 넣지 않는다(사유만)."""


_schema = None


def schema_path() -> str:
    return os.path.join(paths.code_dir(), *SCHEMA_REL)


def load_schema():
    """discern-event 스키마 해석기(store.LabelSchema 를 그대로 쓴다)."""
    global _schema
    if _schema is None:
        from .store import LabelSchema
        with open(schema_path(), "rb") as f:
            _schema = LabelSchema(load_json_strict(f.read(SCHEMA_MAX_BYTES + 1), SCHEMA_MAX_BYTES))
    return _schema


def check_record(rec) -> list[str]:
    """문제 목록(빈 목록이면 통과)."""
    return load_schema().check(rec)


def discern_dir(project_root: str) -> str:
    return os.path.join(os.path.abspath(project_root), DISCERN_DIR)


def session_path(project_root: str, session_id: str) -> str:
    if not isinstance(session_id, str) or not DISCERN_SESSION_ID_RE.match(session_id):
        raise DiscernStoreError("가려내기 세션 ID 형식이 아닙니다")
    return os.path.join(discern_dir(project_root), f"{session_id}.jsonl")


def list_sessions(project_root: str) -> list[str]:
    """discern\\ 아래 세션 ID (이름 순). 내용은 열지 않는다."""
    try:
        names = os.listdir(discern_dir(project_root))
    except OSError:
        return []
    return sorted(n[:-6] for n in names if n.endswith(".jsonl") and DISCERN_SESSION_ID_RE.match(n[:-6]))


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


def _is_ms(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def new_session_id(labeler_id: str, now: datetime, rand4: str) -> str:
    sid = f"{labeler_id}-DS-{now.strftime('%Y%m%d')}-{rand4}"
    if not DISCERN_SESSION_ID_RE.match(sid):
        raise DiscernStoreError("가려내기 세션 ID 를 만들지 못했습니다(라벨러 코드 형식)")
    return sid


def read_session(path: str) -> jsonl.ReadResult:
    """기록 파일을 읽는다(app.jsonl.read). 형식 검사는 하지 않는다 — check_record 로 따로."""
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


def judge_body(key: str, answer: str, vod_ms: int, wall_at: str) -> dict:
    """짝 키(core.discern.target_key) → 판정 줄 body. 실제 채팅 = chat_id, 시나리오 줄 = scenario_id·line_id."""
    if answer not in (*dc.ANSWERS, dc.NONE):
        raise DiscernStoreError("판정 값이 아닙니다")
    if not _is_ms(vod_ms):
        raise DiscernStoreError("시각이 0 이상 정수가 아닙니다")
    try:
        k = dc.parse_key(key)
    except dc.DiscernError as e:
        raise DiscernStoreError(str(e)) from None
    if "chat_id" in k:
        if not CHAT_ID_RE.match(k["chat_id"]):
            raise DiscernStoreError("chat_id 형식이 아닙니다")
        return {"chat_id": k["chat_id"], "answer": answer, "judged_vod_ms": vod_ms, "wall_at": wall_at}
    if not SCENARIO_ID_RE.match(k["scenario_id"]) or not LINE_ID_RE.match(k["line_id"]):
        raise DiscernStoreError("시나리오 줄 번호 형식이 아닙니다")
    return {"scenario_id": k["scenario_id"], "line_id": k["line_id"], "answer": answer, "judged_vod_ms": vod_ms,
            "wall_at": wall_at}


class DiscernLog:
    """가려내기 세션 하나의 기록 파일. 한 번에 한 앱만 쓴다(세션 ID 가 매번 새로)."""

    def __init__(self, project_root: str, session_id: str, labeler_id: str, *,
                 clock: Callable[[], datetime] = _now, uuid_factory: Callable[[], str] | None = None,
                 app_version: str | None = None):
        if not isinstance(labeler_id, str) or not LABELER_ID_RE.match(labeler_id):
            raise DiscernStoreError("라벨러 코드 형식이 아닙니다")
        m = DISCERN_SESSION_ID_RE.match(session_id or "")
        if not m or m.group(1) != labeler_id:
            raise DiscernStoreError("가려내기 세션 ID 와 라벨러 코드가 맞지 않습니다")
        self.project_root = os.path.abspath(project_root)
        self.session_id = session_id
        self.labeler_id = labeler_id
        self.path = session_path(self.project_root, session_id)
        self._clock = clock
        self._uuid = uuid_factory or (lambda: str(uuid.uuid4()))
        self._app_version = app_version or _app_version()
        self._seq = 0
        self.started = False
        self.ended = False
        self.answers: dict[str, str] = {}
        if os.path.exists(self.path):
            res = read_session(self.path)
            if res.corrupt_lines:
                raise DiscernStoreError(f"가려내기 기록 중간에 깨진 줄이 있습니다({len(res.corrupt_lines)}줄)")
            seqs = [r.get("seq") for r in res.records if isinstance(r.get("seq"), int)]
            self._seq = max(seqs, default=0)
            self.started = any(r.get("kind") == K_START for r in res.records)
            self.ended = any(r.get("kind") == K_END for r in res.records)
            self.answers = dc.answers_from_events(r for r in res.records if r.get("kind") == K_JUDGE)

    # ---------------------------------------------------------------- 만들기
    @classmethod
    def create(cls, project_root: str, labeler_id: str, *, clock: Callable[[], datetime] = _now,
               rand4: Callable[[], str] = _rand4, uuid_factory: Callable[[], str] | None = None,
               app_version: str | None = None, check_pilot: bool = True,
               pilot_dir: str | None = None) -> "DiscernLog":
        """새 세션. discern\\ 폴더를 만든다(없으면). 같은 이름이 이미 있으면 무작위 4자를 다시 뽑는다.
        시범 프로젝트는 폴더를 만들기 **전에** 막는다."""
        root = os.path.abspath(project_root)
        if check_pilot:
            from . import reference_set
            why = reference_set.pilot_block_reason(root, pilot_dir=pilot_dir)
            if why:
                raise DiscernBlockedError(why)
        if not isinstance(labeler_id, str) or not LABELER_ID_RE.match(labeler_id):
            raise DiscernStoreError("라벨러 코드 형식이 아닙니다")
        os.makedirs(discern_dir(root), exist_ok=True)
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
        raise DiscernStoreError("가려내기 세션 이름을 만들지 못했습니다")

    # ---------------------------------------------------------------- 쓰기
    def _write(self, kind: str, body: dict, now: datetime | None = None) -> dict:
        if self.ended:
            raise DiscernStoreError("이미 끝난 가려내기 세션입니다")
        rec = {"v": RECORD_V, "id": self._uuid(), "seq": self._seq + 1, "kind": kind, "session_id": self.session_id,
               "labeler_id": self.labeler_id, "created_at": _iso(now or self._clock()),
               "app_version": self._app_version, "body": body}
        problems = check_record(rec)
        if problems:
            raise DiscernRecordError(f"가려내기 기록 형식 오류 {len(problems)}건")
        jsonl.append(self.path, rec)
        self._seq += 1
        return rec

    def start(self, *, project_id: str, scenario_id: str, scenario_sha256: str, range_ms: tuple[int, int],
              shown: Mapping[str, int], seen_before: bool | None, repeat: bool) -> dict:
        """discern.start — 화면을 연 순간. shown = 출처별 대상 수 {replay, retrieved, generated}."""
        a, b = range_ms
        body = {"mode": "discern", "project_id": project_id, "scenario_id": scenario_id,
                "scenario_sha256": scenario_sha256, "range": {"start_ms": int(a), "end_ms": int(b)},
                "audio": "on", "marks_hidden": True,
                "shown": {s: int((shown or {}).get(s) or 0) for s in dc.SOURCES},
                "seen_before": None if seen_before is None else bool(seen_before), "repeat": bool(repeat)}
        rec = self._write(K_START, body)
        self.started = True
        return rec

    def judge(self, key: str, answer: str, vod_ms: int) -> dict:
        """discern.judge — 단추 한 번. answer 는 기록할 값(real·virtual·unsure, 지우면 none). 쓴 줄을 돌려준다.
        상태(answers)는 줄을 쓴 뒤에만 바꾼다."""
        if not self.started:
            raise DiscernStoreError("시작 줄 전에는 판정을 쓰지 않습니다")
        now = self._clock()
        rec = self._write(K_JUDGE, judge_body(key, answer, vod_ms, _iso(now)), now)
        if answer == dc.NONE:
            self.answers.pop(key, None)
        else:
            self.answers[key] = answer
        return rec

    def end(self, reason: str = "closed", *, judged: int, unseen: int, result: Mapping | None = None) -> dict:
        """discern.end — 한 번만 쓴다(그 뒤 쓰기는 거부). result = DiscernResult.numbers() (정답을 보일 때만)."""
        if reason not in END_REASONS:
            raise DiscernStoreError("모르는 끝 사유입니다")
        body = {"reason": reason, "judged": int(judged), "unseen": int(unseen)}
        if result is not None:
            body["result"] = dict(result)
        rec = self._write(K_END, body)
        self.ended = True
        return rec


def load_records(project_root: str, session_ids: Iterable[str] | None = None) -> list[dict]:
    """여러 세션의 통과한 줄 (세션 ID → seq 순)."""
    out = []
    for sid in session_ids if session_ids is not None else list_sessions(project_root):
        p = session_path(project_root, sid)
        if not os.path.isfile(p):
            continue
        recs, _ = read_valid(p)
        out.extend(sorted(recs, key=lambda r: r.get("seq", 0)))
    return out


# ---------------------------------------------------------------------------- 본 적 있음 · 다시 봄
def _pilot_blocked(project_root: str) -> bool:
    from . import reference_set
    try:
        return bool(reference_set.pilot_block_reason(project_root))
    except Exception:  # noqa: BLE001 — 판단 못 하면 막는 쪽
        return True


def _start_ranges(records: Iterable[Mapping], labeler_id: str, kind: str) -> list[tuple[int, int]]:
    out = []
    for r in records:
        if r.get("kind") != kind or r.get("labeler_id") != labeler_id:
            continue
        rng = (r.get("body") or {}).get("range")
        if isinstance(rng, Mapping) and _is_ms(rng.get("start_ms")) and _is_ms(rng.get("end_ms")):
            out.append((int(rng["start_ms"]), int(rng["end_ms"])))
    return out


def _labeling_sessions(project_root: str, labeler_id: str) -> int:
    """labels\\ 의 파일 이름 중 그 라벨러의 세션 수(내용은 읽지 않는다, session.survey_project 와 같은 방식)."""
    from .core.ids import SESSION_FILE_RE
    try:
        names = os.listdir(os.path.join(os.path.abspath(project_root), "labels"))
    except OSError:
        return 0
    sids = set()
    for n in names:
        m = SESSION_FILE_RE.match(n)
        if m and m.group(1).split("-", 1)[0] == labeler_id:
            sids.add(m.group(1))
    return len(sids)


def watched(project_root: str, labeler_id: str, *, exclude_session: str | None = None) -> list[tuple[str, tuple | None]]:
    """'본 적 있음' 의 입력(core.discern.seen_before 의 watched): [(종류, 구간 또는 None = 방송 전체)].

    그 라벨러의 training\\·viewer\\ 의 start 구간, discern\\ 의 start 구간(exclude_session 은 뺀다), labels\\ 의 그 라벨러
    세션(파일 이름만, 방송 전체로 침). 시범 프로젝트(잠기기 전)는 아무것도 열지 않고 빈 목록."""
    root = os.path.abspath(project_root)
    if _pilot_blocked(root):
        return []
    out: list[tuple[str, tuple | None]] = []
    from . import training_store, viewer_store
    try:
        out += [(dc.WATCH_TRAINING, r) for r in _start_ranges(training_store.load_records(root), labeler_id,
                                                              "training.start")]
    except (OSError, ValueError, RuntimeError):
        pass
    try:
        out += [(dc.WATCH_VIEWER, r) for r in _start_ranges(viewer_store.load_records(root), labeler_id,
                                                            viewer_store.K_START)]
    except (OSError, ValueError, RuntimeError):
        pass
    sids = [s for s in list_sessions(root) if s != exclude_session]
    out += [(dc.WATCH_DISCERN, r) for r in _start_ranges(load_records(root, sids), labeler_id, K_START)]
    out += [(dc.WATCH_LABELING, None)] * _labeling_sessions(root, labeler_id)
    return out


def prior(project_root: str, labeler_id: str, *, exclude_session: str | None = None) -> list[dict]:
    """'다시 봄' 의 입력(core.discern.is_repeat): 그 라벨러의 앞 가려내기 세션 [{session_id, scenario_id, judged}].
    judged = 그 세션의 유효 판정 수."""
    root = os.path.abspath(project_root)
    if _pilot_blocked(root):
        return []
    out = []
    for sid in list_sessions(root):
        if sid == exclude_session or not sid.startswith(labeler_id + "-"):
            continue
        recs = load_records(root, [sid])
        starts = [r for r in recs if r.get("kind") == K_START and r.get("labeler_id") == labeler_id]
        if not starts:
            continue
        judged = len(dc.answers_from_events(r for r in recs if r.get("kind") == K_JUDGE))
        out.append({"session_id": sid, "scenario_id": (starts[0].get("body") or {}).get("scenario_id"),
                    "judged": judged})
    return out


__all__ = [
    "DiscernLog", "DiscernStoreError", "DiscernBlockedError", "DiscernRecordError", "check_record", "load_schema",
    "schema_path", "discern_dir", "session_path", "list_sessions", "new_session_id", "read_session", "read_valid",
    "load_records", "judge_body", "watched", "prior", "DISCERN_SESSION_ID_RE", "DISCERN_DIR", "KINDS", "K_START",
    "K_JUDGE", "K_END", "END_REASONS",
]
