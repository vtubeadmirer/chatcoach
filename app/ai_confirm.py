"""AI 확인 큐 — 사람 라벨 없는 엔진 양성을 사람이 한 건씩 판정한 기록 (06 6-4 E3 'AI 확인 큐', 08 D-29 3).

입출력 층이다. Qt 를 import 하지 않는다. 큐 계산(queue_items · summarize · human_values)은 파일을 열지 않는다.

큐 (queue_items)
- 엔진 등급이 direct·likely(반응 있음)인 채팅 가운데 사람 최종 세트에 그 chat_id 가 없거나 result 가 unconfirmed 인 것
  (= 06 E3 의 '사람 라벨 없음' + E1 거짓 양성). 사람이 등급을 매긴 채팅(관련성만·불명확 포함)은 넣지 않는다.
- 엔진 결과의 같은 chat_id 는 첫 줄만(06 6-2). 사람 최종 세트에 같은 chat_id 가 둘 이상이면 조정 값(source = adjudication),
  없으면 첫 줄(app/ui/analysis_view.human_sides 와 같은 규칙). effective = false · conflict = true 인 행은 뺀다.
- 사람 최종 세트 = set = final 인 행 전부(평가에서 빠진 행 포함 — 사람이 매긴 값이 있으면 큐에 넣지 않는다). 조정에서
  '평가 제외'·'모호함으로 남김' 으로 정한 채팅은 최종 세트 밖이라 큐에 든다.
- 시각 순(채팅 시각 → 모르면 뒤, chat_id 순). 판정이 없는 것이 '대기'.

정밀도(확인 뒤) (summarize, 06 6-4 E3)
  (사람 확인된 참 + 맞음) / (엔진 양성 − 모름 − 대기)
  사람 확인된 참 = 엔진 양성 가운데 사람 최종 값이 direct·likely 인 것. '아님' 과 사람이 관련성만·불명확으로 매긴 것은 분모에만
  든다. 판정 파일이 없으면 '판정 없음'(값 None). 큐 밖 chat_id 의 판정(예: 사람 최종 세트가 나중에 바뀜)은 세지 않고
  outside_queue 로만 센다.

파일 (해제된 run 옆, 시범 프로젝트 폴더 밖 — app.seal 의 폴더 규칙 그대로)
  <ai 루트>\\<project_id>\\confirm\\<run_id>.jsonl
  한 줄 = {"v":1,"chat_id","run_id","verdict":"yes"|"no"|"unsure","by":<labeler_id>,"at":ISO,"app_version","seq":n}
  (schemas/ai-confirm.schema.json). 같은 chat_id 를 다시 판정하면 새 줄 — 마지막 줄이 이긴다(되돌리기).
  본문·닉네임·근거 글은 쓰지 않는다.

블라인드 (06 7장)
- 읽기·쓰기 모두 **파일을 열기 전에** seal.unseal_events(seal.jsonl 만 읽음)로 그 run 이 해제됐는지 본다. 해제 기록이
  없으면 ConfirmBlindError(code not_unsealed) — 해제 전에는 쓰지도 읽지도 않는다.
- 자리: 읽기는 세션 밖(None)·조정·분석 세션, 쓰기는 세션 밖·분석 세션만. 라벨링(1차·재라벨)·출처 판정 세션은 무엇을 열기
  전에 ConfirmBlindError(code reader_blind). 쓸 때는 그 chat_id 가 그 run 의 판정에 있는지도 본다(seal.read_run).

로그·오류 문장에는 건수·코드·run_id 만 남긴다. 채팅 본문·닉네임·전사문은 다루지 않는다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from . import jsonl, paths
from .core import visibility as vis
from .core.validate import load_json_strict

V = 1
CONFIRM_DIR = "confirm"
SCHEMA_REL = ("schemas", "ai-confirm.schema.json")
SCHEMA_MAX_BYTES = 1 << 20
MAX_BYTES = 4 << 20                    # 판정 파일 상한(한 줄 ~200바이트 — 2만 줄)
MAX_LINE_BYTES = 4096

YES, NO, UNSURE = "yes", "no", "unsure"
VERDICTS = (YES, NO, UNSURE)
VERDICT_KO = {None: "대기", YES: "맞음", NO: "아님", UNSURE: "모름"}
POSITIVE = ("direct", "likely")        # '반응 있음' (06 4-3, app.core.agreement.POSITIVE 와 같다)

# 사람 최종 값 (human_values)
H_POSITIVE = "positive"                # graded · direct·likely
H_UNCONFIRMED = "unconfirmed"          # 반응 미확인
H_OTHER = "other"                      # graded 인데 관련성만·불명확(·등급 없음)
H_ABSENT = "absent"                    # 최종 세트에 없음

PLAY_PRE_MS = 5_000                    # 큐 한 건의 반복 재생: 채팅 시각 − 5초 ~
PLAY_POST_MS = 3_000                   # ~ 발화 끝 + 3초

READ_KINDS = (None, vis.ADJUDICATION, vis.ANALYSIS)
WRITE_KINDS = (None, vis.ANALYSIS)

RUN_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
CHAT_ID_RE = re.compile(r"\Ac[0-9a-f]{20}(_[0-9]+)?\Z")
LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
APP_VERSION_RE = re.compile(r"\A[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?\Z")

__all__ = [
    "V", "CONFIRM_DIR", "SCHEMA_REL", "VERDICTS", "VERDICT_KO", "YES", "NO", "UNSURE", "PLAY_PRE_MS", "PLAY_POST_MS",
    "ConfirmError", "ConfirmBlindError", "QueueItem", "ConfirmLog", "ConfirmStore",
    "queue_items", "human_values", "engine_positives", "summarize", "summary_line", "summary_cells",
    "confirm_path", "read_log", "read_verdicts", "load_verdicts", "append_verdict", "check_record", "load_schema",
]


class ConfirmError(ValueError):
    """판정 기록을 읽거나 쓸 수 없다. 메시지에 본문·경로를 넣지 않는다(코드·건수만). .code = 원인 코드."""
    code = "confirm_error"

    def __init__(self, msg: str = "", *, code: str | None = None):
        super().__init__(msg)
        if code:
            self.code = code


class ConfirmBlindError(ConfirmError, PermissionError):
    """이 자리는 판정 기록을 열지 않거나(reader_blind), 그 run 이 아직 해제되지 않았다(not_unsealed). 파일을 열기 전에."""
    code = "reader_blind"


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ---------------------------------------------------------------------------- 큐 (파일 없음)
@dataclass(frozen=True)
class QueueItem:
    """큐 한 건. 엔진 쪽 값은 숫자·등급뿐이다(본문 없음)."""
    chat_id: str
    t_ms: int | None                   # 채팅 VOD 시각 (모르면 None)
    grade: str                         # 엔진 등급 direct · likely
    reaction_time_ms: int | None = None
    utterance_start_ms: int | None = None
    utterance_end_ms: int | None = None
    human: str = H_ABSENT              # H_ABSENT · H_UNCONFIRMED
    verdict: str | None = None         # None = 대기

    @property
    def pending(self) -> bool:
        return self.verdict is None

    def play_window(self) -> tuple[int, int] | None:
        """반복 재생 범위 (VOD ms): 채팅 시각 − 5초 ~ 발화 끝 + 3초. 채팅 시각을 모르면 반응 시각·발화 시작으로 대신한다.
        끝은 발화 끝·반응 시각·채팅 시각 가운데 가장 늦은 것 + 3초. 시각이 하나도 없으면 None."""
        anchor = next((v for v in (self.t_ms, self.reaction_time_ms, self.utterance_start_ms) if _is_int(v)), None)
        if anchor is None:
            return None
        tail = max(v for v in (anchor, self.utterance_end_ms, self.reaction_time_ms) if _is_int(v))
        a = max(0, int(anchor) - PLAY_PRE_MS)
        b = max(a + 1, int(tail) + PLAY_POST_MS)
        return a, b


def _result_of(run) -> Mapping:
    """ai-result dict 또는 load_unsealed_run 의 {result, …} → ai-result dict."""
    if isinstance(run, Mapping) and "reactions" not in run and isinstance(run.get("result"), Mapping):
        return run["result"]
    return run if isinstance(run, Mapping) else {}


def engine_positives(run) -> dict[str, Mapping]:
    """엔진 결과 → {chat_id: 첫 줄} 가운데 result = graded · grade ∈ direct·likely 인 것(06 6-2 첫 줄만)."""
    first: dict[str, Mapping] = {}
    rows = _result_of(run).get("reactions")
    for r in rows if isinstance(rows, list) else ():
        if not isinstance(r, Mapping) or not isinstance(r.get("chat_id"), str) or not r["chat_id"]:
            continue
        first.setdefault(r["chat_id"], r)
    return {c: r for c, r in first.items() if r.get("result") == "graded" and r.get("grade") in POSITIVE}


def _rows_of(final_rows) -> list[Mapping]:
    """사람 최종 세트 → export-row 반응 행. export-row dict 그대로, 또는 export.build_final_set 의 item({set, row})은
    set = final 만."""
    out: list[Mapping] = []
    for x in final_rows or ():
        if not isinstance(x, Mapping):
            continue
        if "row" in x and "label_kind" not in x:
            if x.get("set") != "final" or not isinstance(x.get("row"), Mapping):
                continue
            x = x["row"]
        if x.get("label_kind") == "reaction":
            out.append(x)
    return out


def _human_rows(final_rows) -> dict[str, Mapping]:
    """chat_id → 쓸 행. effective = false · conflict = true 는 뺀다. 조정 값이 원 라벨보다 앞선다(없으면 첫 줄)."""
    out: dict[str, Mapping] = {}
    for row in _rows_of(final_rows):
        if row.get("effective") is False or row.get("conflict") is True:
            continue
        cid = row.get("chat_id")
        if not isinstance(cid, str) or not cid:
            continue
        if cid in out and not (row.get("source") == "adjudication" and out[cid].get("source") != "adjudication"):
            continue
        out[cid] = row
    return out


def _hvalue(row: Mapping) -> str:
    if row.get("result") == "unconfirmed":
        return H_UNCONFIRMED
    if row.get("result") == "graded" and row.get("grade") in POSITIVE:
        return H_POSITIVE
    return H_OTHER


def human_values(final_rows) -> dict[str, str]:
    """사람 최종 세트 → {chat_id: H_POSITIVE · H_UNCONFIRMED · H_OTHER}. 없는 chat_id 는 H_ABSENT 로 본다."""
    return {c: _hvalue(r) for c, r in _human_rows(final_rows).items()}


def _clean_verdicts(verdicts) -> dict[str, str]:
    return {c: v for c, v in (verdicts or {}).items() if isinstance(c, str) and v in VERDICTS} \
        if isinstance(verdicts, Mapping) else {}


def queue_items(run, final_rows, verdicts=None, chat_times: Mapping[str, int] | None = None) -> list[QueueItem]:
    """AI 확인 큐 (위 규칙). verdicts: {chat_id: 'yes'|'no'|'unsure'}(load_verdicts) — 없으면 모두 대기."""
    eng = engine_positives(run)
    rows = _human_rows(final_rows)
    vd = _clean_verdicts(verdicts)
    out: list[QueueItem] = []
    for cid, r in eng.items():
        h = rows.get(cid)
        hv = H_ABSENT if h is None else _hvalue(h)
        if hv not in (H_ABSENT, H_UNCONFIRMED):
            continue
        t = None
        if chat_times is not None and _is_int(chat_times.get(cid)):
            t = chat_times[cid]
        elif h is not None and _is_int(h.get("chat_t_ms")):
            t = h["chat_t_ms"]
        elif _is_int(r.get("chat_t_ms")):
            t = r["chat_t_ms"]
        out.append(QueueItem(
            chat_id=cid, t_ms=t, grade=r["grade"],
            reaction_time_ms=r.get("reaction_time_ms") if _is_int(r.get("reaction_time_ms")) else None,
            utterance_start_ms=r.get("utterance_start_ms") if _is_int(r.get("utterance_start_ms")) else None,
            utterance_end_ms=r.get("utterance_end_ms") if _is_int(r.get("utterance_end_ms")) else None,
            human=hv, verdict=vd.get(cid)))
    out.sort(key=lambda q: (q.t_ms is None, q.t_ms if q.t_ms is not None else 0, q.chat_id))
    return out


def _ratio(a, b):
    return (a / b) if b else None


def summarize(run, final_rows, verdicts=None) -> dict:
    """보고서 'AI 확인 큐 판정' 숫자 (파일 없음). verdicts None = 판정 파일 없음('판정 없음', 정밀도(확인 뒤) None)."""
    eng = engine_positives(run)
    hv = human_values(final_rows)
    q = queue_items(run, final_rows, verdicts)
    n = len(eng)
    true_ = sum(1 for c in eng if hv.get(c) == H_POSITIVE)
    other = sum(1 for c in eng if hv.get(c) == H_OTHER)
    unconf = sum(1 for it in q if it.human == H_UNCONFIRMED)
    pend = sum(1 for it in q if it.verdict is None)
    yes = sum(1 for it in q if it.verdict == YES)
    no = sum(1 for it in q if it.verdict == NO)
    uns = sum(1 for it in q if it.verdict == UNSURE)
    qids = {it.chat_id for it in q}
    vd = _clean_verdicts(verdicts)
    den = n - uns - pend
    return {
        "verdict_file": verdicts is not None,
        "engine_positive": n, "human_true": true_, "human_other": other,
        "queue": len(q), "queue_human_unconfirmed": unconf, "queue_no_human_label": len(q) - unconf,
        "pending": pend, "yes": yes, "no": no, "unsure": uns,
        "outside_queue": sum(1 for c in vd if c not in qids),
        "precision_lower": _ratio(true_, n),
        "precision_confirmed": _ratio(true_ + yes, den) if verdicts is not None and den > 0 else None,
        "formula": "(human_true + yes) / (engine_positive - unsure - pending)",
    }


def _f3(v) -> str:
    return "—" if v is None else f"{v:.3f}"


def summary_line(s: Mapping | None) -> str:
    """'AI 확인 큐 판정' 한 줄 (보고서 report.md·도구 출력). 숫자·코드만."""
    if not isinstance(s, Mapping) or s.get("status") == "not_provided":
        return "AI 확인 큐 판정: 엔진이 반응 판정을 내지 않음"
    if not s.get("verdict_file"):
        return f"AI 확인 큐 판정: 판정 없음 (큐 {s.get('queue', 0)}건)"
    return (f"AI 확인 큐 판정: 대기 {s['pending']} · 맞음 {s['yes']} · 아님 {s['no']} · 모름 {s['unsure']} · "
            f"정밀도(확인 뒤) {_f3(s.get('precision_confirmed'))} = (사람 확인된 참 {s['human_true']} + 맞음 {s['yes']}) / "
            f"(엔진 양성 {s['engine_positive']} − 모름 {s['unsure']} − 대기 {s['pending']})")


def summary_cells(s: Mapping | None) -> tuple[str, str]:
    """요약 표 두 칸: ('대기·맞음·아님·모름' 또는 '판정 없음', 정밀도(확인 뒤) 또는 '-')."""
    if not isinstance(s, Mapping) or s.get("status") == "not_provided":
        return "-", "-"
    if not s.get("verdict_file"):
        return "판정 없음", "-"
    p = s.get("precision_confirmed")
    return f"{s['pending']}·{s['yes']}·{s['no']}·{s['unsure']}", ("-" if p is None else f"{p:.3f}")


# ---------------------------------------------------------------------------- 스키마
_schema = None


def load_schema():
    """ai-confirm 스키마 해석기(store.LabelSchema 를 그대로 쓴다)."""
    global _schema
    if _schema is None:
        from .store import LabelSchema
        with open(os.path.join(paths.code_dir(), *SCHEMA_REL), "rb") as f:
            _schema = LabelSchema(load_json_strict(f.read(SCHEMA_MAX_BYTES + 1), SCHEMA_MAX_BYTES))
    return _schema


def check_record(rec) -> list[str]:
    """문제 목록(빈 목록이면 통과)."""
    return load_schema().check(rec)


# ---------------------------------------------------------------------------- 파일
def _guard(reader_kind: str | None, *, write: bool) -> None:
    allowed = WRITE_KINDS if write else READ_KINDS
    if reader_kind not in allowed:
        raise ConfirmBlindError("이 세션에서는 AI 확인 큐 판정 기록을 " + ("쓰지" if write else "열지") + " 않습니다 (06 7-3)",
                                code="reader_blind")


def _check_run_id(run_id) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ConfirmError("run_id 형식이 아닙니다", code="run_id_format")
    return run_id


def _ai_dir(project_root: str, ai_root: str | None) -> str:
    from . import seal
    from .project import open_project
    proj = open_project(project_root)
    if proj.data is None or not proj.project_id:
        raise ConfirmError("project.json 을 읽지 못했습니다", code="project_json")
    try:
        return seal.project_ai_dir(project_root, proj.project_id, ai_root)
    except seal.SealError as e:
        raise ConfirmError(str(e), code=getattr(e, "code", None) or "ai_dir") from None


def _require_unsealed(project_root: str, run_id: str, *, ai_root: str | None, reader_kind: str | None) -> None:
    """seal.jsonl 만 읽어 그 run 의 ai.unsealed 기록이 있는지 본다. 없으면 판정 파일을 열기 전에 ConfirmBlindError."""
    from . import seal
    try:
        events, _probs = seal.unseal_events(project_root, ai_root=ai_root, reader_kind=reader_kind)
    except seal.SealBlindError as e:
        raise ConfirmBlindError(str(e), code="reader_blind") from None
    except (seal.SealError, OSError) as e:
        raise ConfirmError(f"봉인 기록을 읽지 못했습니다: {type(e).__name__}", code="seal_unreadable") from None
    if not any(ev.get("run_id") == run_id for ev in events):
        raise ConfirmBlindError("해제 기록이 없는 run 입니다 — 해제 전에는 판정을 쓰지도 읽지도 않습니다 (06 7장)",
                                code="not_unsealed")


def confirm_path(project_root: str, run_id: str, *, ai_root: str | None = None) -> str:
    """<ai 루트>\\<project_id>\\confirm\\<run_id>.jsonl (경로만 — 해제 여부는 보지 않는다)."""
    return os.path.join(_ai_dir(os.path.abspath(project_root), ai_root), CONFIRM_DIR, f"{_check_run_id(run_id)}.jsonl")


@dataclass
class ConfirmLog:
    """판정 파일 한 개를 읽은 결과. exists = False 면 '판정 없음'."""
    exists: bool
    verdicts: dict[str, str] = field(default_factory=dict)      # chat_id → 마지막 줄의 판정
    records: list[dict] = field(default_factory=list)           # 스키마를 통과한 줄(파일 순)
    problems: list[str] = field(default_factory=list)           # 깨진 줄·스키마 밖 줄(줄 번호·사유만)
    last_seq: int = 0


def _read_file(path: str, run_id: str) -> ConfirmLog:
    try:
        size = os.path.getsize(path)
    except OSError:
        return ConfirmLog(exists=False)
    if size > MAX_BYTES:
        raise ConfirmError("판정 파일이 너무 큽니다", code="too_large")
    res = jsonl.read(path, max_line_bytes=MAX_LINE_BYTES)
    log = ConfirmLog(exists=True)
    log.problems += [f"{n}행: 깨진 줄" for n in res.corrupt_lines]
    if res.truncated_tail:
        log.problems.append("끝 줄 잘림")
    for i, rec in enumerate(res.records, 1):
        probs = check_record(rec)
        if probs:
            log.problems.append(f"{i}번째 기록: 형식 밖 ({len(probs)})")
            continue
        if rec["run_id"] != run_id:
            log.problems.append(f"{i}번째 기록: 다른 run")
            continue
        log.records.append(rec)
        log.verdicts[rec["chat_id"]] = rec["verdict"]        # 마지막 줄이 이긴다
        log.last_seq = max(log.last_seq, rec["seq"])
    return log


def read_log(project_root: str, run_id: str, *, ai_root: str | None = None,
             reader_kind: str | None = None) -> ConfirmLog:
    """판정 파일을 읽는다. 순서: 자리 → run_id 형식 → 해제 기록(seal.jsonl) → 판정 파일. 해제 전이면 열지 않는다."""
    _guard(reader_kind, write=False)
    _check_run_id(run_id)
    root = os.path.abspath(project_root)
    _require_unsealed(root, run_id, ai_root=ai_root, reader_kind=reader_kind)
    return _read_file(confirm_path(root, run_id, ai_root=ai_root), run_id)


def read_verdicts(project_root: str, run_id: str, *, ai_root: str | None = None,
                  reader_kind: str | None = None) -> dict[str, str] | None:
    """{chat_id: 판정} 또는 None(판정 파일 없음 — 보고서 '판정 없음')."""
    log = read_log(project_root, run_id, ai_root=ai_root, reader_kind=reader_kind)
    return dict(log.verdicts) if log.exists else None


def load_verdicts(project_root: str, run_id: str, *, ai_root: str | None = None,
                  reader_kind: str | None = None) -> dict[str, str]:
    """{chat_id: 'yes'|'no'|'unsure'} — 같은 chat_id 는 마지막 줄. 파일이 없으면 빈 dict."""
    return read_verdicts(project_root, run_id, ai_root=ai_root, reader_kind=reader_kind) or {}


def _now_iso(now: datetime | None) -> str:
    n = now or datetime.now().astimezone()
    if n.tzinfo is None:
        n = n.astimezone()
    return n.isoformat(timespec="milliseconds")


def append_verdict(project_root: str, run_id: str, chat_id: str, verdict: str, *, by: str,
                   app_version: str | None = None, now: datetime | None = None, ai_root: str | None = None,
                   reader_kind: str | None = None) -> dict:
    """판정 한 줄을 더한다(되돌리기도 새 줄). 순서: 자리 → 값 검사 → 해제 기록(seal.jsonl) → 그 run 의 판정에 chat_id 가
    있는지(seal.read_run) → 판정 파일(깨진 줄이 있으면 쓰지 않음) → 스키마 검사 → 추가. 쓴 줄(dict)을 돌려준다."""
    _guard(reader_kind, write=True)
    _check_run_id(run_id)
    if not isinstance(chat_id, str) or not CHAT_ID_RE.match(chat_id):
        raise ConfirmError("chat_id 형식이 아닙니다", code="chat_id_format")
    if verdict not in VERDICTS:
        raise ConfirmError("판정 값이 아닙니다(yes·no·unsure)", code="verdict_value")
    if not isinstance(by, str) or not LABELER_RE.match(by):
        raise ConfirmError("판정한 사람(라벨러 ID) 형식이 아닙니다", code="bad_by")
    if app_version is None:
        from . import __version__ as app_version
    if not isinstance(app_version, str) or not APP_VERSION_RE.match(app_version):
        raise ConfirmError("app_version 형식이 아닙니다", code="app_version")
    root = os.path.abspath(project_root)
    _require_unsealed(root, run_id, ai_root=ai_root, reader_kind=reader_kind)
    from . import seal
    try:
        result = seal.read_run(root, run_id, ai_root=ai_root, reader_kind=reader_kind)
    except seal.SealError as e:
        raise ConfirmError(f"해제한 결과를 읽지 못했습니다 ({seal.error_code(e)})", code="run_unreadable") from None
    except (OSError, ValueError) as e:
        raise ConfirmError(f"해제한 결과를 읽지 못했습니다 ({type(e).__name__})", code="run_unreadable") from None
    if not any(isinstance(r, Mapping) and r.get("chat_id") == chat_id for r in result.get("reactions") or ()):
        raise ConfirmError("이 run 의 판정에 없는 chat_id 입니다", code="chat_not_in_run")
    path = confirm_path(root, run_id, ai_root=ai_root)
    log = _read_file(path, run_id)
    if any("깨진 줄" in p or "형식 밖" in p or "다른 run" in p for p in log.problems):
        raise ConfirmError("판정 파일에 읽지 못한 줄이 있어 더 쓰지 않습니다: " + "; ".join(log.problems[:3]),
                           code="confirm_jsonl_corrupt")
    rec = {"v": V, "chat_id": chat_id, "run_id": run_id, "verdict": verdict, "by": by, "at": _now_iso(now),
           "app_version": app_version, "seq": log.last_seq + 1}
    probs = check_record(rec)
    if probs:
        raise ConfirmError("판정 줄이 스키마에 맞지 않습니다: " + "; ".join(probs[:3]), code="schema")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    jsonl.append(path, rec)
    return rec


class ConfirmStore:
    """나란히 보기(AnalysisView)에 넘기는 읽기·쓰기 함수 묶음 — 화면은 파일을 열지 않는다(06 7-3).
    load(run_id) → {chat_id: 판정}, save(run_id, chat_id, verdict) → 쓴 줄. 자리·해제 검사는 위 함수가 한다."""

    def __init__(self, project_root: str, *, reader_kind: str | None, by: str | None,
                 app_version: str | None = None, ai_root: str | None = None):
        self.project_root = project_root
        self.reader_kind = reader_kind
        self.by = by
        self.app_version = app_version
        self.ai_root = ai_root

    def load(self, run_id: str) -> dict[str, str]:
        return load_verdicts(self.project_root, run_id, ai_root=self.ai_root, reader_kind=self.reader_kind)

    def save(self, run_id: str, chat_id: str, verdict: str) -> dict:
        if not self.by:
            raise ConfirmError("판정한 사람(라벨러)을 모릅니다 — 분석 세션에서 여세요", code="bad_by")
        return append_verdict(self.project_root, run_id, chat_id, verdict, by=self.by, app_version=self.app_version,
                              ai_root=self.ai_root, reader_kind=self.reader_kind)


def counts(items: Sequence[QueueItem]) -> dict[str, int]:
    """큐 → {total, done, pending, yes, no, unsure} (진행 줄)."""
    out = {"total": len(items), "pending": 0, YES: 0, NO: 0, UNSURE: 0}
    for it in items:
        if it.verdict is None:
            out["pending"] += 1
        else:
            out[it.verdict] += 1
    out["done"] = out["total"] - out["pending"]
    return out


def progress_text(items: Sequence[QueueItem]) -> str:
    """'진행 n / 전체 · 맞음 a · 아님 b · 모름 c'."""
    c = counts(items)
    return f"진행 {c['done']} / {c['total']} · 맞음 {c[YES]} · 아님 {c[NO]} · 모름 {c[UNSURE]}"


def next_pending_index(items: Sequence[QueueItem], current: int | None) -> int | None:
    """current 다음의 대기 건 자리. 뒤에 없으면 앞에서부터 찾는다. 대기 건이 없으면 None."""
    n = len(items)
    start = -1 if current is None else current
    for k in range(1, n + 1):
        i = (start + k) % n
        if items[i].verdict is None:
            return i
    return None


__all__ += ["counts", "progress_text", "next_pending_index", "H_POSITIVE", "H_UNCONFIRMED", "H_OTHER", "H_ABSENT"]
