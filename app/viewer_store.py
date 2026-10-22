"""시청자 훈련 기록 쓰기·읽기 — 프로젝트 `viewer\\<session_id>.jsonl` (08 D-01 시청자 훈련, D-20 4, 21 6-3).

- 라벨 기록(labels\\, label-event)·스트리머 훈련 기록(training\\, training-event)과 다른 폴더·다른 종류다.
  정답지에도, 노출 계산(training\\ 만 본다)에도 섞이지 않는다 — 첫 판은 원래 반응·사람 라벨을 보이지 않으므로
  노출로 세지 않는다(21 6-3).
- 한 줄 = 기록 1건(start · write · end). 덧붙이기는 app.jsonl.append(한 줄을 os.write 한 번 + fsync, 잘린 꼬리 뒤에는
  줄바꿈부터) — 원자적 덧붙이기. 쓰기 전에 schemas/viewer-event.schema.json 으로 검사한다(store.LabelSchema 해석기).
  맞지 않으면 쓰지 않는다(파일은 그대로).
- **시청자가 쓴 연습 글(write.text)은 이 파일에만 둔다.** viewer/ 는 보호 구역(project·backup 의 protected_zone 기본값)이라
  내보내기·보호 구역 없는 백업에 들어가지 않는다. 예외 메시지·로그에는 글을 넣지 않는다(경로·사유·숫자만).
  시청자 글은 시나리오·LLM 입력에 넣지 않는다(20 6장) — 이 모듈은 scenarios\\ 를 열지 않는다.
- 다른 사람의 채팅 본문·닉네임·전사문은 칸이 없다(스키마가 모르는 칸을 거부한다). 세 축 단계·까닭 코드·VOD 시각·숫자만.
- 세션 ID: '<labeler_id>-VW-<yyyymmdd>-<4자>' (라벨 세션 종류 코드·스트리머 훈련 'TR' 과 겹치지 않는다).
- 시범 프로젝트(pilot-* 등)는 시범 세션이 모두 잠기기 전에는 새 세션을 만들지 않는다(D-20 5, reference_set).

이 모듈은 Qt 를 쓰지 않고 labels\\ 를 열지 않는다. 시계·난수는 바꿔 끼울 수 있다(시험).
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
from .core.validate import load_json_strict

SCHEMA_REL = ("schemas", "viewer-event.schema.json")
SCHEMA_MAX_BYTES = 1 << 20
RECORD_V = 1
VIEWER_DIR = "viewer"
MAX_LINE_BYTES = 64 * 1024
MAX_TEXT_CHARS = 200                      # 한 줄 연습 채팅 최대 글자 수 (스키마 writeBody.text.maxLength 와 같다)
VIEWER_SESSION_ID_RE = re.compile(r"\A([A-Z][A-Z0-9]{1,7})-VW-([0-9]{8})-([a-z0-9]{4})\Z")
LABELER_ID_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
REASON_CODE_RE = re.compile(r"\A[a-z][a-z0-9_.]{0,39}\Z")   # app.core.viewer_score 의 'topic.flow_hit' 꼴
_RAND_ALPHABET = string.ascii_lowercase + string.digits

K_START = "viewer.start"
K_WRITE = "viewer.write"
K_END = "viewer.end"
KINDS = (K_START, K_WRITE, K_END)
AXES = ("topic", "expand", "repeat")      # 주제 적합성 · 대화 확장성 · 반복 여부 (21 6-2)
LEVELS = ("good", "ok", "weak")           # 좋음 · 보통 · 아쉬움
END_REASONS = ("segment_end", "summary", "closed")
SCENARIO_START_KEYS = ("scenario_id", "scenario_sha256", "virtual_lines")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f  ]")


class ViewerStoreError(RuntimeError):
    pass


class ViewerBlockedError(ViewerStoreError, PermissionError):
    """시범 프로젝트에서 시범 세션이 모두 잠기기 전 (D-20 5)."""


class ViewerRecordError(ViewerStoreError, ValueError):
    """쓰려는 줄이 viewer-event 스키마에 맞지 않음. 메시지에 글은 넣지 않는다(사유만)."""


_schema = None


def schema_path() -> str:
    return os.path.join(paths.code_dir(), *SCHEMA_REL)


def load_schema():
    """viewer-event 스키마 해석기(store.LabelSchema 를 그대로 쓴다)."""
    global _schema
    if _schema is None:
        from .store import LabelSchema
        with open(schema_path(), "rb") as f:
            _schema = LabelSchema(load_json_strict(f.read(SCHEMA_MAX_BYTES + 1), SCHEMA_MAX_BYTES))
    return _schema


def check_record(rec) -> list[str]:
    """문제 목록(빈 목록이면 통과). 문제 글에는 문자열 값을 넣지 않는다(LabelSchema 규칙)."""
    return load_schema().check(rec)


def viewer_dir(project_root: str) -> str:
    return os.path.join(os.path.abspath(project_root), VIEWER_DIR)


def session_path(project_root: str, session_id: str) -> str:
    if not isinstance(session_id, str) or not VIEWER_SESSION_ID_RE.match(session_id):
        raise ViewerStoreError("시청자 훈련 세션 ID 형식이 아닙니다")
    return os.path.join(viewer_dir(project_root), f"{session_id}.jsonl")


def list_sessions(project_root: str) -> list[str]:
    """viewer\\ 아래 세션 ID (이름 순). 내용은 열지 않는다."""
    try:
        names = os.listdir(viewer_dir(project_root))
    except OSError:
        return []
    return sorted(n[:-6] for n in names if n.endswith(".jsonl") and VIEWER_SESSION_ID_RE.match(n[:-6]))


def clean_text(text) -> str:
    """연습 글 정리: 제어 문자·줄바꿈을 공백으로, 앞뒤 공백을 뺀다. 길이는 자르지 않는다(넘으면 write 가 거부)."""
    if not isinstance(text, str):
        return ""
    return _CTRL_RE.sub(" ", text).strip()


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
    sid = f"{labeler_id}-VW-{now.strftime('%Y%m%d')}-{rand4}"
    if not VIEWER_SESSION_ID_RE.match(sid):
        raise ViewerStoreError("시청자 훈련 세션 ID 를 만들지 못했습니다(라벨러 코드 형식)")
    return sid


def empty_levels() -> dict:
    """{축: {good: 0, ok: 0, weak: 0}}."""
    return {a: {lv: 0 for lv in LEVELS} for a in AXES}


def level_counts(scores_list: Iterable[Mapping | None]) -> dict:
    """쓰기마다의 scores({축: {level, code}} 또는 None) → 축마다 단계 분포. None·모르는 단계는 세지 않는다."""
    out = empty_levels()
    for sc in scores_list:
        if not isinstance(sc, Mapping):
            continue
        for a in AXES:
            ax = sc.get(a)
            lv = ax.get("level") if isinstance(ax, Mapping) else None
            if lv in LEVELS:
                out[a][lv] += 1
    return out


def _clean_scores(scores) -> dict | None:
    """세 축 {level, code} 만 남긴다(까닭 글 등 다른 칸은 버린다 — 글의 낱말이 들어갈 수 있다). 모양이 아니면 None."""
    if not isinstance(scores, Mapping):
        return None
    out = {}
    for a in AXES:
        ax = scores.get(a)
        if not isinstance(ax, Mapping) or ax.get("level") not in LEVELS:
            return None
        code = ax.get("code")
        out[a] = {"level": ax["level"], "code": code if isinstance(code, str) and REASON_CODE_RE.match(code) else "other"}
    return out


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


def summarize(records: Iterable[Mapping]) -> dict:
    """기록 줄 → 숫자 요약 {writes, levels, ended, reason}. 글은 돌려주지 않는다(보고·화면 숫자용)."""
    writes, scores, ended, reason = 0, [], False, None
    for r in records:
        k = r.get("kind")
        b = r.get("body") if isinstance(r.get("body"), Mapping) else {}
        if k == K_WRITE:
            writes += 1
            scores.append(b.get("scores"))
        elif k == K_END:
            ended, reason = True, b.get("reason")
    return {"writes": writes, "levels": level_counts(scores), "ended": ended, "reason": reason}


class ViewerLog:
    """시청자 훈련 세션 하나의 기록 파일. 한 번에 한 앱만 쓴다(세션 ID 가 매번 새로)."""

    def __init__(self, project_root: str, session_id: str, labeler_id: str, *,
                 clock: Callable[[], datetime] = _now, uuid_factory: Callable[[], str] | None = None,
                 app_version: str | None = None):
        if not isinstance(labeler_id, str) or not LABELER_ID_RE.match(labeler_id):
            raise ViewerStoreError("라벨러 코드 형식이 아닙니다")
        m = VIEWER_SESSION_ID_RE.match(session_id or "")
        if not m or m.group(1) != labeler_id:
            raise ViewerStoreError("시청자 훈련 세션 ID 와 라벨러 코드가 맞지 않습니다")
        self.project_root = os.path.abspath(project_root)
        self.session_id = session_id
        self.labeler_id = labeler_id
        self.path = session_path(self.project_root, session_id)
        self._clock = clock
        self._uuid = uuid_factory or (lambda: str(uuid.uuid4()))
        self._app_version = app_version or _app_version()
        self._seq = 0
        self.writes = 0
        self.ended = False
        self._scores: list = []
        if os.path.exists(self.path):
            res = read_session(self.path)
            if res.corrupt_lines:
                raise ViewerStoreError(f"시청자 훈련 기록 중간에 깨진 줄이 있습니다({len(res.corrupt_lines)}줄)")
            seqs = [r.get("seq") for r in res.records if isinstance(r.get("seq"), int)]
            self._seq = max(seqs, default=0)
            s = summarize(res.records)
            self.writes, self.ended = s["writes"], s["ended"]
            self._scores = [(r.get("body") or {}).get("scores") for r in res.records if r.get("kind") == K_WRITE]

    # ---------------------------------------------------------------- 만들기
    @classmethod
    def create(cls, project_root: str, labeler_id: str, *, clock: Callable[[], datetime] = _now,
               rand4: Callable[[], str] = _rand4, uuid_factory: Callable[[], str] | None = None,
               app_version: str | None = None, check_pilot: bool = True,
               pilot_dir: str | None = None) -> "ViewerLog":
        """새 세션. viewer\\ 폴더를 만든다(없으면). 같은 이름이 이미 있으면 무작위 4자를 다시 뽑는다.
        시범 프로젝트는 폴더를 만들기 **전에** 막는다(시범 폴더에 아무것도 쓰지 않는다)."""
        root = os.path.abspath(project_root)
        if check_pilot:
            from . import reference_set
            why = reference_set.pilot_block_reason(root, pilot_dir=pilot_dir)
            if why:
                raise ViewerBlockedError(why)
        if not isinstance(labeler_id, str) or not LABELER_ID_RE.match(labeler_id):
            raise ViewerStoreError("라벨러 코드 형식이 아닙니다")
        os.makedirs(viewer_dir(root), exist_ok=True)
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
        raise ViewerStoreError("시청자 훈련 세션 이름을 만들지 못했습니다")

    # ---------------------------------------------------------------- 쓰기
    def _write(self, kind: str, body: dict, now: datetime | None = None) -> dict:
        if self.ended:
            raise ViewerStoreError("이미 끝난 시청자 훈련 세션입니다")
        rec = {"v": RECORD_V, "id": self._uuid(), "seq": self._seq + 1, "kind": kind, "session_id": self.session_id,
               "labeler_id": self.labeler_id, "created_at": _iso(now or self._clock()),
               "app_version": self._app_version, "body": body}
        problems = check_record(rec)
        if problems:
            raise ViewerRecordError(f"시청자 훈련 기록 형식 오류 {len(problems)}건: " + "; ".join(problems[:3]))
        jsonl.append(self.path, rec)
        self._seq += 1
        return rec

    def start(self, *, project_id: str, range_ms: tuple[int, int], scenario: Mapping | None = None) -> dict:
        """viewer.start. scenario(선택): 가상 채팅을 섞었으면 {scenario_id, scenario_sha256, virtual_lines} 가운데 아는 칸."""
        a, b = range_ms
        body = {"mode": "viewer", "project_id": project_id, "source": "replay", "audio": "on",
                "range": {"start_ms": int(a), "end_ms": int(b)}}
        if scenario:
            for k in SCENARIO_START_KEYS:
                if scenario.get(k) is not None:
                    body[k] = scenario[k]
        return self._write(K_START, body)

    def write(self, text: str, vod_ms: int, scores: Mapping | None, *, score_error: str | None = None,
              scorer: str | None = None) -> dict:
        """viewer.write — 연습 채팅 한 줄. text 는 clean_text 뒤 1~MAX_TEXT_CHARS 자. scores 는 {축: {level, code}}
        (다른 칸은 버린다) 또는 None. 쓴 줄을 돌려준다. 실패면 예외(메시지에 글 없음) — 파일은 그대로."""
        t = clean_text(text)
        if not t:
            raise ViewerStoreError("빈 글은 기록하지 않습니다")
        if len(t) > MAX_TEXT_CHARS:
            raise ViewerStoreError(f"글이 너무 깁니다({len(t)}자, 최대 {MAX_TEXT_CHARS}자)")
        if not isinstance(vod_ms, int) or isinstance(vod_ms, bool) or vod_ms < 0:
            raise ViewerStoreError("시각이 0 이상 정수가 아닙니다")
        sc = _clean_scores(scores)
        body = {"vod_ms": vod_ms, "text": t, "chars": len(t), "scores": sc}
        if sc is None:
            err = score_error if isinstance(score_error, str) and REASON_CODE_RE.match(score_error) else "scorer_failed"
            body["score_error"] = err
        if isinstance(scorer, str) and scorer:
            body["scorer"] = scorer
        rec = self._write(K_WRITE, body)
        self.writes += 1
        self._scores.append(sc)
        return rec

    def levels(self) -> dict:
        """지금까지 쓴 줄의 축마다 단계 분포."""
        return level_counts(self._scores)

    def end(self, reason: str = "closed", *, examples: int | None = None) -> dict:
        """viewer.end — 쓴 수와 축마다 분포(숫자만). 한 번만 쓴다(그 뒤 쓰기는 거부)."""
        if reason not in END_REASONS:
            raise ViewerStoreError("모르는 끝 사유입니다")
        body = {"reason": reason, "writes": self.writes, "levels": self.levels()}
        if isinstance(examples, int) and not isinstance(examples, bool) and examples >= 0:
            body["examples"] = examples
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


__all__ = [
    "ViewerLog", "ViewerStoreError", "ViewerBlockedError", "ViewerRecordError", "check_record", "load_schema",
    "schema_path", "viewer_dir", "session_path", "list_sessions", "new_session_id", "clean_text", "read_session",
    "read_valid", "load_records", "summarize", "level_counts", "empty_levels", "AXES", "LEVELS", "END_REASONS",
    "MAX_TEXT_CHARS", "VIEWER_SESSION_ID_RE", "VIEWER_DIR", "KINDS",
]
