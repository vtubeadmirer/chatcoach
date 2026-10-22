"""라벨 내보내기 zip — JSONL · 검토용 CSV · manifest.json (docs/design/03-data-model.md 16장, 05 10-4 · S-11 · S-27).

입출력 층이다. Qt·ui 를 import 하지 않는다(tests/test_layers.py). 프로젝트 폴더에는 아무것도 쓰지 않는다(읽기만).

무엇이 나가나 (허용 목록 방식, 16-2 의 full_labels 열)
- labels.jsonl : 라벨 1건 = 한 줄. 열 수 있는 세션들의 **현재 유효 라벨만**(app.core.resolve 의 ACTIVE 머리).
                 철회·충돌(포크·순환)·무효 줄은 나가지 않고 매니페스트 건수로만 남는다. 이어짐은 대상 반응(같은 세션)이
                 현재 유효한 직접·높음일 때만 나간다(03 12-3) — 아니면 빼고 경고에 건수. 행 모양은
                 schemas/export-row.schema.json 이고, 행은 이 파일의 _ROW_KEYS 에 있는 키만 갖는다(목록 밖 키는 버린다).
- segments.csv · reactions.csv · missed.csv : 같은 행을 펼친 검토용 사본(UTF-8 BOM, 열 이름 한 줄, 모든 문자열 칸은
                 safe_cell). 다시 가져올 수 없다(매니페스트 notes).
- manifest.json: $defs/exportManifest. 파일별 크기·SHA-256·행 수, 기준서 버전·문서 해시·시작값 해시(세션의
                 session.open 값), 활성 보정값, messages_sha256, 앱 버전, 내보낸 세션 목록(filters.session_ids), 종류별 행 수,
                 제외 사유 계단 건수(06 6-6: 행마다 처음 걸린 사유 하나), 경고(이어짐을 비운 직접·높음 반응 건수 포함, D-10 7).
'들었다' 인정 비율은 라벨 기준서 판의 recheck.heard_min_coverage(없으면 rules.HEARD_MIN_COVERAGE, D-10 6).
- 그 밖의 프로젝트 파일(chat/protected/·derived/·origin/·samples/·locks/·guide/ 원문·project.json 원문·messages.jsonl
  원문)은 zip 에 들어가지 않는다. derived/ 와 앱 data\\ai\\ 는 열지도 않는다(엔진·AI 결과).

기본값 (05 10-4)
- 원 닉네임·userIdHash·솔트·machine_id·로컬 경로는 어떤 옵션으로도 나가지 않는다(읽지 않는 파일에 있거나 행 키 목록에 없다).
- 채팅 본문(text)·가림 닉네임(nickname_masked)·발화 받아쓰기(heard_text)·증거 메모(note)는 옵션을 켤 때만.
- viewer_id(가명)는 나간다. CSV 에는 표시용 번호 viewer_no(03 14-2)를 옆에 둔다.

블라인드 (안내서 원칙 3, 03 12장, 05 S-11)
- viewer(SessionContext 모양: session_kind·session_id)를 주면 app.core.visibility.files_to_open 표대로만 기록 파일을 연다.
  라벨링 세션(primary·relabel_*)은 자기 세션 파일만, 조정 세션은 자기 것 + 잠긴 세션(locks/<session_id>.json)만.
  허락되지 않은 세션의 파일은 디스크에서 읽지 않는다(이름 목록만 본다).
- viewer 가 없으면(운영자 도구 tools/export_labels.py) 모든 세션을 읽는다. 라벨링 중인 라벨러에게 이 zip 을 주지 않는다.
- 엔진 판정·AI 결과·등급 제안은 넣지 않는다. eligible_grades 는 저장 시점의 '조건 충족 상태'(01 3-1)라 그대로 나간다.

출력 위치
- git 작업 트리(.git 이 있는 폴더 아래, 이 저장소 포함)와 프로젝트 폴더 안은 거부한다(ExportLocationError).
- OneDrive·UNC 아래는 경고하고 쓴다(경고는 결과와 매니페스트 warnings 에).
- 임시 파일에 다 쓴 뒤 os.replace. 같은 이름이 있으면 overwrite=True 일 때만 바꾼다.

이번 판에서 계산하지 않는 것 (eval.excluded_reasons, 06 6-6)
- in_boundary_buffer 는 그 세션의 현재 유효 구간표 경계 ±boundary_buffer_sec(모든 시작·끝, 표본 뽑기와 같은 규칙)로
  채팅 행(반응·놓친 후보)에 센다. 그 세션에 구간 라벨이 없으면 null(모름).
- origin_*, adjudicated_exclude, human_disagreement, agreement_below_gate 는 세지 않는다.
  그래서 eval.strict 는 '이 판이 아는 사유가 없음' 이다. 매니페스트 notes 에 적는다.
- origin 라벨(출처 판정, 16-2 '검토자만')과 adjudication 기록은 행으로 내보내지 않고 건수만 경고로 남긴다.

최종 세트 모드 (build_final_set · export_final_set, 16 8-1 4번, 06 6-5·6-6, 08 D-15 7)
- **잠긴** 1차 세션 하나 + 잠긴 재라벨 세션 + 조정 세션의 조정 기록 + (있으면) 잠긴 출처 판정 세션만 읽는다.
  06 7-1 비교 조건(agreement_report.comparison_gate)을 라벨 파일을 열기 **전에** 본다. 안 잠겼으면 아무 라벨도 열지 않고 거부.
- 조정 기록은 app.core.final_set 이 adjudicate.case_states 와 같은 열쇠로 바꿔 끼운다. exclude_from_eval 은 세트에서 빼고
  (계단 건수에만 adjudicated_exclude), keep_as_ambiguous 는 ambiguous.jsonl 에 따로 둔다. 재라벨에서 달랐는데 조정이 없으면
  human_disagreement. 출처 판정이 있으면 origin_virtual·origin_undeterminable 도 센다.
- zip: labels.jsonl(참고 점수에 들어가는 행 = eval.lenient, export_type ground_truth_lenient. 엄격은 eval.strict 로 거른다)·
  ambiguous.jsonl·CSV 3개·manifest.json. 구간 행은 최종 구간 조각(final_set.segment_timeline)마다 한 줄이다.
- 행 모양은 export-row 그대로다(source = adjudication 이면 조정 값). 라벨 형식(label-event)은 바꾸지 않는다(D-15 7).
"""
from __future__ import annotations

import builtins
import copy
import csv
import hashlib
import io
import json
import logging
import os
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from . import jsonl, paths
from . import project as projmod
from .core import resolve as rs
from .core import rules
from .core import segments as segmod
from .core import visibility as vis
from .core.rules import EVIDENCE_KEYS, V0_1_STRENGTH
from .core.timecode import fmt_display
from .core.validate import StrictJsonError, load_json_strict
from .session import parse_session_file_name
from .store import LabelSchema, MAX_LINE_BYTES

log = logging.getLogger(__name__)

EXPORT_TYPE = "full_labels"
SCHEMA_REL = ("schemas", "export-row.schema.json")
SCHEMA_MAX_BYTES = 1 << 20
MESSAGES_MAX_LINE = 1 << 20

JSONL_NAME = "labels.jsonl"
CSV_SEGMENTS = "segments.csv"
CSV_REACTIONS = "reactions.csv"
CSV_MISSED = "missed.csv"
MANIFEST_NAME = "manifest.json"
ZIP_NAMES = (JSONL_NAME, CSV_SEGMENTS, CSV_REACTIONS, CSV_MISSED, MANIFEST_NAME)   # zip 에 들어가는 이름 전부

ROW_KINDS = ("segment", "reaction", "continuation", "missed_candidate")   # 행으로 나가는 라벨 종류
SKIPPED_KINDS = ("origin", "adjudication")                                # 행으로 내보내지 않고 건수만
MERGED_KINDS = ("utterance", "video_check")                               # 반응 행 안에 펼쳐 넣는다

NOTES = ("CSV 는 참고용이며 다시 가져올 수 없음. 정답 원본은 JSONL. "
         "시각은 VOD 시각 ms(영상 파일 시각 = VOD 시각 + calibration.offset_ms). "
         "eval 은 이 판이 계산하는 사유만 담는다(origin_*·adjudicated_exclude·human_disagreement·"
         "agreement_below_gate 는 아직 세지 않음. in_boundary_buffer 는 그 세션의 현재 구간표 경계로 셈). origin·adjudication 기록은 이 판에서 행으로 내보내지 않는다.")

# 06 6-6 의 순서. 계단 집계는 이 순서에서 처음 걸린 사유 하나로만 센다.
# recheck_incomplete 는 참고 점수에 넣으므로(08 D-16 6b) 참고 점수 대상이 갈리는 human_disagreement 다음에 센다 —
# 참고에서도 빠지는 사유가 같이 걸린 행은 그 사유로 세어 계단표의 '참고 점수 대상' 줄이 참고 n 과 맞는다.
EXCLUDE_ORDER = (
    "non_text_message", "in_boundary_buffer", "origin_virtual", "origin_undeterminable", "conflict_unresolved",
    "out_of_media", "calibration_unverified", "view_exposed", "audio_not_heard",
    "confidence_undeterminable", "grade_unclear", "adjudicated_exclude", "human_disagreement",
    "recheck_incomplete", "confidence_probably", "agreement_below_gate",
)
# 참고 점수(eval.lenient)에 남겨도 되는 사유. 엄격(eval.strict)은 사유가 하나라도 있으면 뺀다.
# recheck_incomplete: 7-2 두 번 듣기를 시범에서 권장으로 낮춰(D-16 4) 참고 점수에는 넣는다(D-16 6b)
LENIENT_OK = frozenset({"recheck_incomplete", "confidence_probably", "agreement_below_gate"})
EXPOSED_BLIND_WHAT = frozenset({"other_session_labels", "ai_results", "origin_info"})   # 06 6-6 view_exposed

# export-row 의 키 목록(허용 목록). 행은 이 순서로 이 키만 갖는다.
_ROW_KEYS = (
    "label_kind", "label_id", "entity_id", "source", "effective", "superseded_by", "conflict",
    "session_id", "session_kind", "labeler_id", "created_at",
    "guide_version", "guide_sha12", "thresholds_sha12", "app_version", "schema_version", "media_rev", "calibration_rev",
    "start_ms", "end_ms", "category", "active_chat",
    "chat_id", "chat_kind", "chat_t_ms", "viewer_id", "nickname_masked", "text",
    "entry", "sample_set_id", "sample_order", "result", "grade", "eligible_grades", "grade_vs_conditions",
    "override_reason", "unclear_cause", "confidence", "rationale",
    "first_visible_ms", "reaction_time_ms", "delay_ms", "linked_utterance_id", "utterances", "competing_chat_ids",
    "evidence_strong", "evidence_medium", "evidence_weak", "evidence_detail",
    "no_similar_chat_nearby", "streamer_already_on_topic", "video_checks", "first_judgment", "changed_on_recheck",
    "aids_used",
    "reaction_entity_id", "continuation_type", "span_start_ms", "span_end_ms", "observed_end_ms",
    "truncated_at_segment_end", "related_chat_count",
    "missed_scope", "cond1", "cond2", "cond3", "cond4", "exposure_est_ms", "chat_rate_10s", "is_candidate",
    "reason_code", "check_question",
    "eval",
)
_OPTIONAL_TEXT_KEYS = ("text", "nickname_masked")   # 옵션을 끄면 행에서 null

_EVAL_COLS = ("eval_strict", "eval_lenient", "excluded_reasons")
CSV_COLUMNS = {
    CSV_SEGMENTS: ("session_id", "labeler_id", "entity_id", "start_ms", "start_hms", "end_ms", "end_hms",
                   "category", "confidence", "active_chat", "rationale", "guide_version") + _EVAL_COLS,
    CSV_REACTIONS: ("session_id", "labeler_id", "entity_id", "chat_id", "chat_t_ms", "chat_t_hms", "viewer_id",
                    "viewer_no", "text", "entry", "result", "grade", "eligible_grades", "grade_vs_conditions",
                    "confidence", "reaction_time_ms", "reaction_time_hms", "delay_ms", "evidence_strong",
                    "evidence_medium", "evidence_weak", "utterance_ms", "utterance_hms", "heard_text",
                    "recheck_count", "changed_on_recheck", "rationale", "guide_version") + _EVAL_COLS,
    CSV_MISSED: ("session_id", "labeler_id", "entity_id", "chat_id", "chat_t_ms", "chat_t_hms", "viewer_id",
                 "viewer_no", "text", "scope", "cond1", "cond2", "cond3", "cond4", "exposure_est_ms", "chat_rate_10s",
                 "is_candidate", "reason_code", "check_question", "confidence", "rationale", "guide_version")
                + _EVAL_COLS,
}
_CSV_OPTIONAL = {"text": "include_text", "heard_text": "include_heard_text"}   # 괄호 열(16-4): 옵션을 켤 때만


class ExportError(RuntimeError):
    """내보내기를 할 수 없다. 메시지에 본문·닉네임을 넣지 않는다."""


class ExportLocationError(ExportError):
    """출력 위치가 허용되지 않는다(git 작업 트리·프로젝트 폴더 안)."""


@dataclass(frozen=True)
class ExportOptions:
    include_text: bool = False                 # 채팅 본문 + 증거 메모(note)
    include_nickname_masked: bool = False      # 가림 닉네임(원 닉네임은 어떤 옵션으로도 나가지 않음)
    include_heard_text: bool = False           # 발화 받아쓰기
    session_ids: tuple[str, ...] | None = None  # 이 세션들만(없으면 열 수 있는 전부)
    overwrite: bool = False
    # '들었다' 인정 비율(D-10 6). None 이면 라벨의 기준서 판(project.json guides[].thresholds)의
    # recheck.heard_min_coverage, 그것도 없으면 rules.HEARD_MIN_COVERAGE. 주면 모든 판에 이 값(작업대가 편집기와 같은 값을 넘길 때).
    heard_min_coverage: float | None = None


@dataclass
class ExportResult:
    out_path: str
    manifest: dict
    rows: int
    by_kind: dict[str, int]
    session_ids: list[str]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------- CSV 셀 보호 (S-27)

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(v):
    """CSV 문자열 칸 보호(03 16-4, 05 S-27). 숫자·None·bool 은 그대로 돌려준다.

    제어문자(탭·줄바꿈 말고)를 지우고, 앞 공백을 뺀 첫 글자가 = + - @ 탭 이면 앞에 ' 를 붙인다.
    """
    if not isinstance(v, str):
        return v
    v = "".join(ch for ch in v if ch in "\t\n" or ord(ch) >= 0x20)
    if v.lstrip(" ")[:1] in _FORMULA_START:
        return "'" + v
    return v


def _cell(v):
    """행 값 → CSV 칸. 목록은 '|' 로 잇고, bool 은 true/false, None 은 빈칸. 문자열은 safe_cell."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return safe_cell("|".join(str(x) for x in v))
    if isinstance(v, (int, float)):
        return v
    return safe_cell(str(v))


# ---------------------------------------------------------------------------------------------- 출력 위치

def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _under(p: str, root: str) -> bool:
    p, r = _norm(p), _norm(root).rstrip("\\/")
    return p == r or p.startswith(r + os.sep)


def _git_root(path: str) -> str | None:
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def check_output_path(out_path: str, project_root: str | None = None) -> list[str]:
    """출력 위치 검사. 거부면 ExportLocationError, 허용이면 경고 목록(경로는 넣지 않는다).

    거부: 이 앱의 코드 폴더(저장소) 안, .git 이 있는 폴더 아래, 프로젝트 폴더 안.
    경고: OneDrive · UNC 아래.
    """
    if not isinstance(out_path, str) or not out_path.lower().endswith(".zip"):
        raise ExportLocationError("출력 파일은 .zip 이어야 합니다")
    if _under(out_path, paths.code_dir()) or _git_root(out_path) is not None:
        raise ExportLocationError("출력 위치가 저장소(git 작업 트리) 안입니다. 저장소 밖에 저장하세요(공개 저장소에 라벨이 올라가지 않게).")
    if project_root is not None and _under(out_path, project_root):
        raise ExportLocationError("출력 위치가 프로젝트 폴더 안입니다. 프로젝트 밖에 저장하세요(백업·패키지에 섞이지 않게).")
    out: list[str] = []
    if paths.is_under_onedrive(out_path):
        out.append("출력 위치가 OneDrive 아래입니다. 라벨 사본이 클라우드로 동기화됩니다.")
    if paths.is_network_path(out_path):
        out.append("출력 위치가 네트워크(UNC) 경로입니다.")
    return out


# ---------------------------------------------------------------------------------------------- 스키마

_schema_cache: dict[str, dict] = {}


def schema_path() -> str:
    return os.path.join(paths.code_dir(), *SCHEMA_REL)


def _load_export_schema(path: str | None = None) -> tuple[LabelSchema, LabelSchema]:
    """(행 검사기, 매니페스트 검사기). store 의 손검사기를 그대로 쓴다(jsonschema 의존 없음, S-29)."""
    p = path or schema_path()
    root = _schema_cache.get(p)
    if root is None:
        with builtins.open(p, "rb") as f:
            root = _schema_cache[p] = load_json_strict(f.read(SCHEMA_MAX_BYTES + 1), SCHEMA_MAX_BYTES)
    man = dict(root["$defs"]["exportManifest"])
    man["$defs"] = root["$defs"]
    return LabelSchema(root), LabelSchema(man)


# ---------------------------------------------------------------------------------------------- 읽기

@dataclass
class _Chat:
    t_ms: int | None
    kind: str | None
    viewer_id: str | None
    text: str | None = None
    nickname_masked: str | None = None


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _read_messages(path: str, opts: ExportOptions) -> tuple[dict[str, _Chat], dict[str, int], str | None, list[str]]:
    """messages.jsonl → (chat_id → 필요한 칸만, viewer_no, 파일 sha256, 경고). 없으면 빈 값.

    본문·가림 닉네임은 옵션을 켰을 때만 메모리에 둔다. 다른 칸(raw_ref·flags·role 등)은 버린다.
    """
    warns: list[str] = []
    try:
        with builtins.open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return {}, {}, None, ["chat/messages.jsonl 이 없어 채팅 정보(시각·종류·가명)를 채우지 못했습니다."]
    sha = hashlib.sha256(data).hexdigest()
    res = jsonl.read(path, max_line_bytes=MESSAGES_MAX_LINE)
    if res.corrupt_lines or res.truncated_tail:
        warns.append(f"chat/messages.jsonl 에 읽지 못한 줄이 {len(res.corrupt_lines) + int(res.truncated_tail)}개 있습니다.")
    chats: dict[str, _Chat] = {}
    order: list[tuple] = []
    for r in res.records:
        cid = r.get("chat_id")
        if not isinstance(cid, str):
            continue
        t = r.get("t_ms") if _is_int(r.get("t_ms")) else None
        vid = r.get("viewer_id") if isinstance(r.get("viewer_id"), str) else None
        if r.get("is_anonymous") and vid != "anon":
            vid = "anon"
        chats[cid] = _Chat(
            t_ms=t, kind=r.get("kind") if isinstance(r.get("kind"), str) else None, viewer_id=vid,
            text=r.get("text") if opts.include_text and isinstance(r.get("text"), str) else None,
            nickname_masked=(r.get("nickname_masked") if opts.include_nickname_masked
                             and isinstance(r.get("nickname_masked"), str) else None))
        order.append(((0, t, cid) if t is not None else (1, 0, cid), vid))
    # 03 14-2 표시용 번호: (t_ms, chat_id) 오름차순(t_ms null 은 맨 뒤), 처음 나온 순서. 익명은 번호 없음.
    vno: dict[str, int] = {}
    for _, vid in sorted(order, key=lambda x: x[0]):
        if vid and vid != "anon" and vid not in vno:
            vno[vid] = len(vno) + 1
    return chats, vno, sha, warns


@dataclass
class _Loaded:
    records: list[dict]
    session_ids: list[str]
    skipped_files: int
    invalid_lines: int
    warnings: list[str]


def _allowed_sessions(labels_dir: str, project_root: str, names: Iterable[str], viewer) -> set[str] | None:
    """viewer 가 열 수 있는 세션 ID 집합. viewer 가 없으면 None(전부)."""
    if viewer is None:
        return None
    scope = vis.files_to_open(getattr(viewer, "session_kind", None))
    own = getattr(viewer, "session_id", None)
    ok: set[str] = set()
    for name in names:
        parsed = parse_session_file_name(name)
        if parsed is None:
            continue
        sid = parsed[0]
        if sid == own:
            if scope.can_read("own_labels"):
                ok.add(sid)
            continue
        locked = os.path.exists(os.path.join(project_root, "locks", f"{sid}.json"))
        if scope.may_read_other_session(locked):
            ok.add(sid)
    return ok


def _load_labels(project_root: str, opts: ExportOptions, viewer, schema: LabelSchema | None) -> _Loaded:
    labels_dir = os.path.join(project_root, "labels")
    try:
        names = sorted(os.listdir(labels_dir))
    except FileNotFoundError:
        names = []
    allowed = _allowed_sessions(labels_dir, project_root, names, viewer)
    want = set(opts.session_ids) if opts.session_ids is not None else None
    recs: list[dict] = []
    sids: set[str] = set()
    skipped = 0
    invalid = 0
    warns: list[str] = []
    for name in names:
        parsed = parse_session_file_name(name)
        if parsed is None:
            if name.endswith(".jsonl") or ".jsonl." in name:
                skipped += 1          # 충돌 사본·잘린 줄 조각 등(13-4). 읽지 않는다
            continue
        sid = parsed[0]
        if allowed is not None and sid not in allowed:
            continue                  # 블라인드: 열지 않는다
        if want is not None and sid not in want:
            continue
        path = os.path.join(labels_dir, name)
        try:
            res = jsonl.read(path, max_line_bytes=MAX_LINE_BYTES)
        except OSError as e:
            warns.append(f"기록 파일 하나를 읽지 못해 뺐습니다({type(e).__name__}).")
            skipped += 1
            continue
        if res.corrupt_lines:
            # 중간 줄이 깨진 파일은 판 연결이 틀릴 수 있어 통째로 뺀다(store 는 이런 세션을 읽기 전용으로 연다).
            warns.append(f"세션 {sid}: 깨진 줄 {len(res.corrupt_lines)}개가 있어 그 파일을 뺐습니다.")
            skipped += 1
            continue
        if res.truncated_tail:
            warns.append(f"세션 {sid}: 마지막 줄이 잘려 그 줄을 빼고 읽었습니다.")
        for r in res.records:
            if r.get("session_id") != sid:
                invalid += 1
                continue
            if schema is not None and schema.check(r):
                invalid += 1
                continue
            recs.append(r)
        sids.add(sid)
    if skipped:
        warns.append(f"규칙 밖 이름이거나 읽을 수 없는 기록 파일 {skipped}개를 읽지 않았습니다.")
    if invalid:
        warns.append(f"형식 검사를 통과하지 못한 기록 {invalid}줄을 뺐습니다.")
    if want is not None:
        missing = sorted(want - sids)
        if missing:
            warns.append(f"요청한 세션 중 {len(missing)}개를 찾지 못했거나 열 수 없었습니다.")
    return _Loaded(recs, sorted(sids), skipped, invalid, warns)


# ---------------------------------------------------------------------------------------------- 행 만들기

@dataclass
class _SessionInfo:
    kind: str | None = None
    guide_version: str | None = None
    guide_doc_sha256: str | None = None
    thresholds_sha256: str | None = None
    exposed: bool = False
    audio_warned: set = field(default_factory=set)


def _sessions_info(view: rs.EffectiveView) -> dict[str, _SessionInfo]:
    out: dict[str, _SessionInfo] = {}
    for r in view.other_records:
        sid = r.get("session_id")
        if not isinstance(sid, str):
            continue
        info = out.setdefault(sid, _SessionInfo())
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        k = r.get("kind")
        if k == "session.open" and info.kind is None:
            info.kind = body.get("session_kind")
            info.guide_version = body.get("guide_version")
            info.guide_doc_sha256 = body.get("guide_doc_sha256")
            info.thresholds_sha256 = body.get("thresholds_sha256")
        elif k == "view.exposed" and body.get("what") in EXPOSED_BLIND_WHAT:
            info.exposed = True
        elif k == "warning" and body.get("code") == "audio_not_heard" and body.get("action") == "dismissed":
            if isinstance(body.get("entity_id"), str):
                info.audio_warned.add(body["entity_id"])
    return out


class _GuideRules:
    """기준서 버전별 증거 강도표·재확인 횟수·경계 완충·'들었다' 인정 비율(project.json guides[].thresholds)."""

    def __init__(self, data: dict, heard_override: float | None = None):
        self._by: dict[str, tuple[dict, int]] = {}
        self._heard: dict[str, float] = {}
        self._heard_override = None if heard_override is None else float(heard_override)
        for g in data.get("guides") or []:
            if not isinstance(g, dict):
                continue
            th = g.get("thresholds") if isinstance(g.get("thresholds"), dict) else {}
            es = th.get("evidence_strength")
            strength = dict(es) if isinstance(es, dict) and set(es) == set(EVIDENCE_KEYS) else dict(V0_1_STRENGTH)
            rc = th.get("recheck") if isinstance(th.get("recheck"), dict) else {}
            mv = rc.get("min_views_direct_likely")
            sg = th.get("segment") if isinstance(th.get("segment"), dict) else {}
            bs = sg.get("boundary_buffer_sec")
            buf = int(round(bs * 1000)) if isinstance(bs, (int, float)) and not isinstance(bs, bool) and bs >= 0                 else segmod.DEFAULT_BUFFER_MS
            self._by[g.get("guide_version")] = (strength, mv if _is_int(mv) and mv >= 1 else 2, buf)
            self._heard[g.get("guide_version")] = rules.heard_min_coverage(th)

    def strength(self, gv) -> dict:
        return self._by.get(gv, (dict(V0_1_STRENGTH), 2, segmod.DEFAULT_BUFFER_MS))[0]

    def min_views(self, gv) -> int:
        return self._by.get(gv, (None, 2, None))[1]

    def buffer_ms(self, gv) -> int:
        return self._by.get(gv, (None, 2, segmod.DEFAULT_BUFFER_MS))[2]

    def heard_min_coverage(self, gv) -> float:
        """D-10 6: 옵션 값 → 그 판의 recheck.heard_min_coverage → rules.HEARD_MIN_COVERAGE."""
        if self._heard_override is not None:
            return self._heard_override
        return self._heard.get(gv, rules.HEARD_MIN_COVERAGE)


def _sample(body: dict) -> tuple[str | None, int | None]:
    s = body.get("sample_ref")
    if isinstance(s, dict):
        return s.get("sample_set_id"), s.get("order")
    return None, None


def _present_keys(evidence) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    out = []
    for k in EVIDENCE_KEYS:
        it = evidence.get(k)
        if isinstance(it, dict) and it.get("present") is True:
            out.append(k)
    return out


class _RowBuilder:
    def __init__(self, view: rs.EffectiveView, project_data: dict, chats: dict[str, _Chat], opts: ExportOptions):
        self.view = view
        self.opts = opts
        self.chats = chats
        self.sessions = _sessions_info(view)
        self.rules = _GuideRules(project_data, opts.heard_min_coverage)
        self.cal_status: dict[int, str] = {}
        for c in ((project_data.get("calibration") or {}).get("revs") or []):
            if isinstance(c, dict) and _is_int(c.get("rev")):
                self.cal_status[c["rev"]] = c.get("status")
        self.missing_chat = 0
        self.defaulted_modality = 0
        dur = (project_data.get("broadcast") or {}).get("duration_ms")
        self.duration_ms = dur if _is_int(dur) and dur > 0 else 0
        self._buffers: dict[str, list[tuple[int, int]] | None] = {}
        # 같은 세션의 영상 확인 기록: (session_id, 대상 entity_id) → 기록들
        self.checks: dict[tuple[str, str], list[dict]] = {}
        for r in view.items(kind="video_check"):
            b = r.get("body") or {}
            t = b.get("target_entity_id")
            if isinstance(t, str):
                self.checks.setdefault((r["session_id"], t), []).append(r)

    # ------------------------------------------------------------------ 공통
    def _base(self, rec: dict) -> dict:
        sid = rec["session_id"]
        info = self.sessions.get(sid) or _SessionInfo()
        return {
            "label_kind": rec["kind"], "label_id": rec["id"], "entity_id": rec["entity_id"],
            "source": "label", "effective": True, "superseded_by": None, "conflict": False,
            "session_id": sid, "session_kind": info.kind, "labeler_id": rec.get("labeler_id"),
            "created_at": rec.get("created_at"),
            "guide_version": rec.get("guide_version"), "guide_sha12": rec.get("guide_sha12"),
            "thresholds_sha12": rec.get("thresholds_sha12"), "app_version": rec.get("app_version"),
            "schema_version": rec.get("v"), "media_rev": rec.get("media_rev"),
            "calibration_rev": rec.get("calibration_rev"),
        }

    def _chat_fields(self, row: dict, chat_id) -> _Chat | None:
        c = self.chats.get(chat_id) if isinstance(chat_id, str) else None
        if c is None:
            self.missing_chat += 1
        row["chat_id"] = chat_id
        kind = c.kind if c else None
        row["chat_kind"] = kind if kind in ("text", "image", "sticker", "video", "rich", "donation",
                                            "subscription", "system", "other") else (None if kind is None else "other")
        row["chat_t_ms"] = c.t_ms if c else None
        row["viewer_id"] = c.viewer_id if c else None
        row["nickname_masked"] = c.nickname_masked if (c and self.opts.include_nickname_masked) else None
        row["text"] = c.text if (c and self.opts.include_text) else None
        return c

    def _reasons(self, rec: dict, row: dict, *, chat_based: bool) -> list[str]:
        info = self.sessions.get(rec["session_id"]) or _SessionInfo()
        body = rec.get("body") or {}
        rs_: set[str] = set()
        if chat_based and row.get("chat_kind") not in (None, "text"):
            rs_.add("non_text_message")
        if body.get("out_of_media") is True:
            rs_.add("out_of_media")
        if self.cal_status.get(rec.get("calibration_rev")) != "verified":
            rs_.add("calibration_unverified")
        if info.exposed:
            rs_.add("view_exposed")
        conf = row.get("confidence")
        if conf == "undeterminable":
            rs_.add("confidence_undeterminable")
        elif conf == "probably":
            rs_.add("confidence_probably")
        if row.get("label_kind") == "reaction":
            checks = row.get("video_checks") or []
            # '들었다' = audio_heard 이고 coverage_ratio ≥ 기준(편집기·작업대와 같은 core 규칙, 안내서 7-2).
            cov = self.rules.heard_min_coverage(rec.get("guide_version"))
            heard = rules.heard_count(checks, cov)
            if rec["entity_id"] in info.audio_warned or (checks and heard == 0):
                rs_.add("audio_not_heard")
            if not rules.recheck_complete(row.get("grade"), checks,
                                          min_views=self.rules.min_views(rec.get("guide_version")),
                                          min_coverage=cov):
                rs_.add("recheck_incomplete")
            if row.get("grade") == "unclear":
                rs_.add("grade_unclear")
        return [r for r in EXCLUDE_ORDER if r in rs_]

    def _session_buffers(self, sid: str) -> list[tuple[int, int]] | None:
        """그 세션의 현재 유효 구간표로 만든 경계 완충(안내서 3장 '전환 앞뒤 10초'). 구간 라벨이 없으면 None(모름)."""
        if sid not in self._buffers:
            segs = self.view.items(kind="segment", session_id=sid)
            if not segs:
                self._buffers[sid] = None
            else:
                info = self.sessions.get(sid) or _SessionInfo()
                gv = info.guide_version or segs[0].get("guide_version")
                self._buffers[sid] = segmod.record_buffers(segs, self.duration_ms, self.rules.buffer_ms(gv))
        return self._buffers[sid]

    def _in_buffer(self, rec: dict, row: dict) -> bool | None:
        t = row.get("chat_t_ms")
        bufs = self._session_buffers(rec["session_id"])
        if not _is_int(t) or bufs is None:
            return None
        return segmod.in_ranges(t, bufs)

    def _finish(self, rec: dict, row: dict, *, chat_based: bool) -> dict:
        reasons = self._reasons(rec, row, chat_based=chat_based)
        ev: dict[str, Any] = {}
        if chat_based:
            inb = self._in_buffer(rec, row)
            ev["in_boundary_buffer"] = inb
            if inb:
                reasons = [r for r in EXCLUDE_ORDER if r in set(reasons) | {"in_boundary_buffer"}]
        ev.update({"strict": not reasons, "lenient": all(r in LENIENT_OK for r in reasons),
                   "excluded_reasons": reasons})
        row["eval"] = ev
        return {k: row[k] for k in _ROW_KEYS if k in row}

    # ------------------------------------------------------------------ 종류별
    def segment(self, rec: dict) -> dict:
        b = rec.get("body") or {}
        row = self._base(rec)
        row.update(start_ms=b.get("start_ms"), end_ms=b.get("end_ms"), category=b.get("category"),
                   active_chat=b.get("active_chat"), confidence=b.get("confidence"), rationale=b.get("rationale"))
        return self._finish(rec, row, chat_based=False)

    def _utterance(self, sid: str, uid) -> dict | None:
        if not isinstance(uid, str):
            return None
        u = self.view.effective_entity(uid, sid)
        if u is None:
            u = self.view.effective(uid)
            if u is not None and u.get("session_id") != sid:
                u = None
        if u is None or u.get("kind") != "utterance":
            return None
        b = u.get("body") or {}
        modality = b.get("modality")
        if modality not in ("verbal", "nonverbal", "both"):
            modality = "verbal"
            self.defaulted_modality += 1
        speaker = b.get("speaker") if b.get("speaker") in ("streamer", "tts", "other_audio", "unknown") else "unknown"
        return {
            "utterance_id": uid, "start_ms": b.get("start_ms"), "end_ms": b.get("end_ms"),
            "heard_text": b.get("heard_text") if self.opts.include_heard_text and isinstance(b.get("heard_text"), str) else None,
            "heard_text_source": b.get("heard_text_source"),
            "speaker": speaker, "modality": modality,
        }

    def reaction(self, rec: dict) -> dict:
        b = rec.get("body") or {}
        sid = rec["session_id"]
        row = self._base(rec)
        self._chat_fields(row, b.get("chat_id"))
        ssid, sorder = _sample(b)
        cands = [c for c in (b.get("candidates") or []) if isinstance(c, dict)]
        utts, seen = [], set()
        for c in cands:
            uid = c.get("utterance_id")
            if uid in seen:
                continue
            seen.add(uid)
            u = self._utterance(sid, uid)
            if u is not None:
                utts.append(u)
        linked = b.get("linked_utterance_id")
        use = [c for c in cands if c.get("utterance_id") == linked] if linked else cands
        present: set[str] = set()
        for c in use:
            present.update(_present_keys(c.get("evidence")))
        strength = self.rules.strength(rec.get("guide_version"))
        detail = []
        for c in cands:
            ev = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
            items = {}
            for k in EVIDENCE_KEYS:
                it = ev.get(k) if isinstance(ev.get(k), dict) else {}
                one = {"present": it.get("present"), "at_ms": it.get("at_ms")}
                if self.opts.include_text and isinstance(it.get("note"), str):
                    one["note"] = it["note"]
                items[k] = one
            detail.append({"utterance_id": c.get("utterance_id"), "evidence": items})
        checks = sorted(self.checks.get((sid, rec["entity_id"]), []), key=lambda r: (r.get("seq") or 0, r["id"]))
        vchecks = []
        for r in checks:
            cb = r.get("body") or {}
            vchecks.append({"check": cb.get("check"), "checked_at": r.get("created_at"),
                            "window_start_ms": cb.get("window_start_ms"), "window_end_ms": cb.get("window_end_ms"),
                            "audio_heard": cb.get("audio_heard"), "coverage_ratio": cb.get("coverage_ratio")})
        fv, rt = b.get("first_visible_ms"), b.get("reaction_time_ms")
        row.update(
            entry=b.get("entry"), sample_set_id=ssid, sample_order=sorder, result=b.get("result"), grade=b.get("grade"),
            eligible_grades=list(b.get("eligible_grades") or []), grade_vs_conditions=b.get("grade_vs_conditions"),
            override_reason=b.get("override_reason"), unclear_cause=b.get("unclear_cause"),
            confidence=b.get("confidence"), rationale=b.get("rationale"),
            first_visible_ms=fv, reaction_time_ms=rt,
            delay_ms=(rt - fv) if _is_int(rt) and _is_int(fv) else None,
            linked_utterance_id=linked, utterances=utts,
            competing_chat_ids=list(b.get("competing_chat_ids") or []),
            evidence_strong=[k for k in EVIDENCE_KEYS if k in present and strength.get(k) == "strong"],
            evidence_medium=[k for k in EVIDENCE_KEYS if k in present and strength.get(k) == "medium"],
            evidence_weak=[k for k in EVIDENCE_KEYS if k in present and strength.get(k) == "weak"],
            evidence_detail=detail,
            no_similar_chat_nearby=b.get("no_similar_chat_nearby"),
            streamer_already_on_topic=b.get("streamer_already_on_topic"),
            video_checks=vchecks, first_judgment=copy.deepcopy(b.get("first_judgment")),
            changed_on_recheck=b.get("changed_on_recheck"), aids_used=list(b.get("aids_used") or []),
        )
        return self._finish(rec, row, chat_based=True)

    def continuation(self, rec: dict) -> dict:
        b = rec.get("body") or {}
        row = self._base(rec)
        rel = b.get("related_chat_ids")
        row.update(reaction_entity_id=b.get("reaction_entity_id"), continuation_type=b.get("type"),
                   span_start_ms=b.get("span_start_ms"), span_end_ms=b.get("span_end_ms"),
                   observed_end_ms=b.get("observed_end_ms"), truncated_at_segment_end=b.get("truncated_at_segment_end"),
                   related_chat_count=len(rel) if isinstance(rel, list) else None,
                   confidence=b.get("confidence"), rationale=b.get("rationale"))
        return self._finish(rec, row, chat_based=False)

    def missed_candidate(self, rec: dict) -> dict:
        b = rec.get("body") or {}
        row = self._base(rec)
        self._chat_fields(row, b.get("chat_id"))
        ssid, sorder = _sample(b)
        c1 = b.get("cond1") if isinstance(b.get("cond1"), dict) else {}
        c2 = b.get("cond2") if isinstance(b.get("cond2"), dict) else {}
        row.update(sample_set_id=ssid, sample_order=sorder, missed_scope=b.get("scope"),
                   cond1=c1.get("value"), cond2=c2.get("value"), cond3=b.get("cond3"), cond4=b.get("cond4"),
                   exposure_est_ms=c2.get("exposure_est_ms"), chat_rate_10s=c2.get("chat_rate_10s"),
                   is_candidate=b.get("is_candidate"), reason_code=b.get("reason_code"),
                   check_question=b.get("check_question"), confidence=b.get("confidence"), rationale=b.get("rationale"))
        return self._finish(rec, row, chat_based=True)


def _continuation_target_ok(view: rs.EffectiveView, rec: dict) -> bool:
    """이어짐의 대상 반응(같은 세션)이 현재 유효하고 직접·높음인가(안내서 4-3, 03 12-3). 아니면 행으로 내보내지 않는다."""
    t = (rec.get("body") or {}).get("reaction_entity_id")
    if not isinstance(t, str):
        return False
    r = view.effective_entity(t, rec["session_id"])
    if r is None or r.get("kind") != "reaction":
        return False
    b = r.get("body") or {}
    return b.get("result") == "graded" and b.get("grade") in ("direct", "likely")


def _linked_without_continuation(rows: list[dict]) -> int:
    """내보내는 행 가운데 이어짐 행이 붙지 않은 직접·높음 반응 수(같은 세션 기준, D-10 7 경고 건수)."""
    has = {(r["session_id"], r.get("reaction_entity_id")) for r in rows if r.get("label_kind") == "continuation"}
    return sum(1 for r in rows
               if r.get("label_kind") == "reaction" and r.get("result") == "graded"
               and r.get("grade") in ("direct", "likely") and (r["session_id"], r["entity_id"]) not in has)


# ---------------------------------------------------------------------------------------------- 공개 함수

def build_rows(project_dir: str, options: ExportOptions | None = None, *, viewer=None,
               label_schema: LabelSchema | None = None) -> tuple[list[dict], dict]:
    """프로젝트 → (행 목록, 정보). 파일을 쓰지 않는다. 정보: session_ids·warnings·counts·messages_sha256·viewer_no 등.

    행은 export-row 키만 갖고, 세션·seq 순이다. viewer 는 export_labels 와 같다(블라인드).
    """
    opts = options or ExportOptions()
    proj = projmod.open_project(project_dir)
    if proj.data is None or proj.read_only:
        raise ExportError(f"project.json 을 읽을 수 없거나 검증을 통과하지 못했습니다({len(proj.problems)}개 사유)")
    data = proj.data
    if label_schema is None:
        try:
            from .store import load_label_schema
            label_schema = load_label_schema()
        except (OSError, StrictJsonError, ValueError) as e:
            raise ExportError(f"기록 스키마를 읽지 못했습니다: {type(e).__name__}") from None
    loaded = _load_labels(proj.root, opts, viewer, label_schema)
    view = rs.resolve(loaded.records)
    chats, vno, msha, mwarn = _read_messages(proj.paths.messages_jsonl, opts)
    b = _RowBuilder(view, data, chats, opts)

    rows: list[dict] = []
    skipped_kinds: dict[str, int] = {}
    for rec in view.items():
        k = rec.get("kind")
        if k in ROW_KINDS:
            info = b.sessions.get(rec["session_id"])
            if info is None or info.kind not in vis.SESSION_KINDS:
                skipped_kinds["no_session_open"] = skipped_kinds.get("no_session_open", 0) + 1
                continue
            if k == "continuation" and not _continuation_target_ok(view, rec):
                skipped_kinds["orphan_continuation"] = skipped_kinds.get("orphan_continuation", 0) + 1
                continue
            rows.append(getattr(b, k)(rec))
        elif k in SKIPPED_KINDS:
            skipped_kinds[k] = skipped_kinds.get(k, 0) + 1
    warns = list(loaded.warnings) + mwarn
    no_cont = _linked_without_continuation(rows)
    if no_cont:
        warns.append(f"이어짐을 비운 직접·높음 반응 {no_cont}건(이어짐은 선택 항목, D-10 7).")
    if b.missing_chat:
        warns.append(f"messages.jsonl 에 없는 chat_id 를 가리킨 행이 {b.missing_chat}개 있습니다(채팅 정보 빈칸).")
    if b.defaulted_modality:
        warns.append(f"modality 가 비어 있는 발화 {b.defaulted_modality}개를 verbal 로 채웠습니다.")
    for k, n in sorted(skipped_kinds.items()):
        if k == "no_session_open":
            warns.append(f"session.open 이 없거나 모르는 세션 종류의 라벨 {n}건을 뺐습니다.")
        elif k == "orphan_continuation":
            warns.append(f"대상 반응이 철회됐거나 직접·높음이 아닌 이어짐 {n}건을 뺐습니다(03 12-3).")
        else:
            warns.append(f"{k} 기록 {n}건은 이 판에서 행으로 내보내지 않았습니다.")
    conflicts = [c for c in view.conflicts() if c.kind in ROW_KINDS or c.kind is None]
    info = {
        "project": data, "view": view, "session_ids": loaded.session_ids, "warnings": warns,
        "messages_sha256": msha, "viewer_no": vno, "sessions": b.sessions,
        "conflicts": len(conflicts), "invalid_records": loaded.invalid_lines + len(view.invalid_ids),
        "continuation_missing": no_cont,
    }
    return rows, info


def _csv_bytes(name: str, rows: list[dict], vno: dict[str, int], opts: ExportOptions) -> tuple[bytes, int]:
    cols = [c for c in CSV_COLUMNS[name] if c not in _CSV_OPTIONAL or getattr(opts, _CSV_OPTIONAL[c])]
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(cols)
    n = 0
    for r in rows:
        ev = r.get("eval") or {}
        linked = next((u for u in r.get("utterances") or [] if u["utterance_id"] == r.get("linked_utterance_id")), None)
        get = {
            "start_hms": _hms(r.get("start_ms")), "end_hms": _hms(r.get("end_ms")),
            "chat_t_hms": _hms(r.get("chat_t_ms")), "reaction_time_hms": _hms(r.get("reaction_time_ms")),
            "viewer_no": vno.get(r.get("viewer_id")) if r.get("viewer_id") not in (None, "anon") else None,
            "utterance_ms": linked["start_ms"] if linked else None,
            "utterance_hms": _hms(linked["start_ms"]) if linked else "",
            "heard_text": linked.get("heard_text") if linked else None,
            "recheck_count": len(r.get("video_checks") or []) if r.get("label_kind") == "reaction" else None,
            "scope": r.get("missed_scope"),
            "eval_strict": ev.get("strict"), "eval_lenient": ev.get("lenient"),
            "excluded_reasons": ev.get("excluded_reasons") or [],
        }
        w.writerow([_cell(get[c] if c in get else r.get(c)) for c in cols])
        n += 1
    return buf.getvalue().encode("utf-8-sig"), n


def _hms(ms) -> str:
    return fmt_display(ms) if _is_int(ms) else ""


def _labeler_of(data: dict, viewer, created_by: str | None) -> str:
    if created_by:
        return created_by
    if viewer is not None and isinstance(getattr(viewer, "labeler_id", None), str):
        return viewer.labeler_id
    mb = str(data.get("modified_by") or "")
    return mb.split("@", 1)[0] or str(data.get("created_by") or "")


def export_labels(project_dir: str, out_path: str, options: ExportOptions | None = None, *, viewer=None,
                  created_by: str | None = None, clock: Callable[[], datetime] | None = None,
                  uuid_factory: Callable[[], str] | None = None, app_version: str | None = None) -> ExportResult:
    """프로젝트의 현재 유효 라벨을 zip 하나로 내보낸다(full_labels). 프로젝트 폴더에는 쓰지 않는다.

    - out_path: .zip. 저장소·git 작업 트리·프로젝트 폴더 안이면 ExportLocationError. OneDrive·UNC 는 경고.
    - viewer: 세션 하나(SessionContext 모양). 주면 visibility 표대로 열 수 있는 세션만(블라인드). 없으면 전부(운영자).
    - created_by: 매니페스트 created_by(라벨러 코드). 없으면 viewer → project.json modified_by.
    - clock·uuid_factory·app_version: 시험에서 바꾼다.
    """
    opts = options or ExportOptions()
    proj_root = os.path.abspath(project_dir)
    if os.path.basename(proj_root).lower() == projmod.PROJECT_FILE:
        proj_root = os.path.dirname(proj_root)
    loc_warn = check_output_path(out_path, proj_root)
    if os.path.exists(out_path) and not opts.overwrite:
        raise FileExistsError("출력 파일이 이미 있습니다(덮어쓰려면 overwrite)")
    row_schema, man_schema = _load_export_schema()
    rows, info = build_rows(proj_root, opts, viewer=viewer)
    data = info["project"]
    warns: list[str] = loc_warn + info["warnings"]

    good: list[dict] = []
    bad = 0
    for r in rows:
        if row_schema.check(r):
            bad += 1
        else:
            good.append(r)
    if bad:
        warns.append(f"내보내기 행 형식(export-row)에 맞지 않는 행 {bad}개를 뺐습니다.")
        log.warning("export-row 형식에 맞지 않는 행 %d개", bad)

    files: list[tuple[str, bytes, int]] = []
    jl = b"".join(jsonl.dumps_line(r) for r in good)
    files.append((JSONL_NAME, jl, len(good)))
    vno = info["viewer_no"]
    by_csv = {CSV_SEGMENTS: "segment", CSV_REACTIONS: "reaction", CSV_MISSED: "missed_candidate"}
    for name, kind in by_csv.items():
        data_b, n = _csv_bytes(name, [r for r in good if r["label_kind"] == kind], vno, opts)
        files.append((name, data_b, n))

    by_kind = {k: 0 for k in ROW_KINDS}
    excluded: dict[str, int] = {}
    for r in good:
        by_kind[r["label_kind"]] += 1
        rsn = r["eval"]["excluded_reasons"]
        if rsn:
            excluded[rsn[0]] = excluded.get(rsn[0], 0) + 1
    if info["conflicts"]:
        excluded["conflict_unresolved"] = excluded.get("conflict_unresolved", 0) + info["conflicts"]

    # 기준서: 내보낸 세션의 session.open 값(세션이 묶인 판). project.json 과 다르면 경고.
    guides: dict[str, dict] = {}
    pj_guides = {g.get("guide_version"): g for g in data.get("guides") or [] if isinstance(g, dict)}
    for sid in info["session_ids"]:
        s = info["sessions"].get(sid)
        if s is None or not s.guide_version:
            continue
        g = {"guide_version": s.guide_version, "doc_sha256": s.guide_doc_sha256,
             "thresholds_sha256": s.thresholds_sha256}
        if s.guide_version in guides and guides[s.guide_version] != g:
            warns.append(f"기준서 {s.guide_version} 의 해시가 세션마다 다릅니다.")
            continue
        guides[s.guide_version] = g
        pg = pj_guides.get(s.guide_version)
        if pg is None or pg.get("doc_sha256") != g["doc_sha256"] or pg.get("thresholds_sha256") != g["thresholds_sha256"]:
            warns.append(f"세션의 기준서 {s.guide_version} 해시가 project.json 기록과 다릅니다.")
    if not guides:
        av = data.get("active_guide_version")
        pg = pj_guides.get(av)
        if pg is not None:
            guides[av] = {"guide_version": av, "doc_sha256": pg["doc_sha256"],
                          "thresholds_sha256": pg["thresholds_sha256"]}

    cal = data.get("calibration") or {}
    active = cal.get("active_rev", 0)
    crev = next((c for c in cal.get("revs") or [] if isinstance(c, dict) and c.get("rev") == active), {})
    now = (clock or (lambda: datetime.now().astimezone()))()
    if now.tzinfo is None:
        now = now.astimezone()
    if app_version is None:
        from . import __version__ as app_version  # noqa: N811
    manifest = {
        "schema": "chatcoach/export-manifest",
        "schema_version": 1,
        "export_id": (uuid_factory or (lambda: str(uuid.uuid4())))(),
        "export_type": EXPORT_TYPE,
        "project_id": data["project_id"],
        "video_no": str((data.get("broadcast") or {}).get("video_no")),
        "created_at": now.isoformat(timespec="milliseconds"),
        "created_by": _labeler_of(data, viewer, created_by),
        "app_version": app_version,
        "filters": {
            "guide_versions": None,
            "session_ids": list(info["session_ids"]),
            "include_history": False,
            "include_text": bool(opts.include_text),
            "include_nickname_masked": bool(opts.include_nickname_masked),
            "include_heard_text": bool(opts.include_heard_text),
            "include_protected": False,
        },
        "guides": [guides[k] for k in sorted(guides)],
        "calibration": {"active_rev": active, "offset_ms": crev.get("offset_ms", 0),
                        "status": crev.get("status", "unverified")},
        "messages_sha256": info["messages_sha256"],
        "files": [{"name": n, "size": len(b_), "sha256": hashlib.sha256(b_).hexdigest(), "rows": k}
                  for n, b_, k in files],
        "counts": {"by_kind": by_kind, "excluded": excluded, "invalid_records": info["invalid_records"]},
        "warnings": warns,
        "notes": NOTES,
    }
    problems = man_schema.check(manifest)
    if problems:
        raise ExportError("매니페스트가 형식에 맞지 않습니다: " + "; ".join(problems[:3]))
    man_b = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    files.append((MANIFEST_NAME, man_b, None))

    _write_zip(out_path, files, now)
    log.info("내보내기 완료: 행 %d, 세션 %d, 경고 %d", len(good), len(info["session_ids"]), len(warns))
    return ExportResult(out_path=os.path.abspath(out_path), manifest=manifest, rows=len(good), by_kind=by_kind,
                        session_ids=list(info["session_ids"]), warnings=warns)


def _write_zip(out_path: str, files: list[tuple[str, bytes, Any]], now: datetime,
               allowed: tuple[str, ...] = ZIP_NAMES) -> None:
    d = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(d, exist_ok=True)
    tmp = os.path.abspath(out_path) + ".tmp"
    stamp = now.timetuple()[:6]
    if stamp[0] < 1980:
        stamp = (1980, 1, 1, 0, 0, 0)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for name, data, _ in files:
                assert name in allowed
                zi = zipfile.ZipInfo(name, date_time=stamp)
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, data)
        with builtins.open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        os.replace(tmp, out_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------------------------- 최종 세트 모드

FINAL_EXPORT_TYPE = "ground_truth_lenient"
AMBIGUOUS_NAME = "ambiguous.jsonl"
FINAL_ZIP_NAMES = ZIP_NAMES + (AMBIGUOUS_NAME,)
SET_FINAL, SET_EXCLUDED, SET_AMBIGUOUS = "final", "excluded", "ambiguous"
_AMBIGUOUS_REASON = "adjudicated_ambiguous"          # 매니페스트 counts.excluded 에만 (final_set.R_ADJ_AMBIGUOUS)
FINAL_NOTES = ("최종 세트(참고 정답 세트): 잠긴 1차 세션 + 재라벨 + 조정 기록. labels.jsonl 은 eval.lenient 인 행만, 엄격 정답은 "
               "eval.strict 로 거른다. 조정 값으로 바꾼 행은 source = adjudication. 평가 제외(exclude_from_eval)는 행이 없고 "
               "counts.excluded 의 adjudicated_exclude 로만 센다. 진짜 모호함으로 남긴 것(keep_as_ambiguous)은 ambiguous.jsonl. "
               "구간 행은 최종 구간 조각마다 한 줄. 시각은 VOD 시각 ms. CSV 는 참고용이며 다시 가져올 수 없음.")


class FinalSetBlindError(ExportError, PermissionError):
    """이 자리(세션 종류)는 최종 세트를 만들지 않는다(다른 세션 라벨을 읽는 일이라 조정 세션·세션 밖만)."""


@dataclass
class FinalSetExport:
    """최종 세트 행. items 의 각 항목 = {"set": final·excluded·ambiguous, "row": export-row, "report_reasons": [...]}.
    segment_items 는 구간 조각마다 같은 모양에 "piece"(start_ms·end_ms·category·confidence·status·relabeled) 가 더 있다."""
    items: list[dict]
    segment_items: list[dict]
    other_rows: list[dict]
    conflicts: list[dict]
    final_set: Any
    info: dict


def final_set_block_reason(reader_kind: str | None, phase: str | None = None) -> str | None:
    """최종 세트를 만들 수 없는 자리면 그 까닭. 세션 밖(None)·조정 세션이 된다(03 12-1: 잠긴 다른 세션 라벨 읽기).
    분석 세션은 봉인 해제 뒤(phase = COMPARISON — 부르는 쪽이 seal.jsonl 의 ai.unsealed 를 확인한다)에만 된다
    (visibility: 분석 세션의 다른 세션 라벨이 그때 LOCKED_ONLY, 나란히 보기의 '사람 최종')."""
    if reader_kind is None:
        return None
    if reader_kind == vis.ANALYSIS:
        if phase == vis.COMPARISON:
            return None
        return "분석 세션은 AI 결과 봉인을 푼 뒤에만 최종 세트를 봅니다 (D-15 7)"
    if reader_kind != vis.ADJUDICATION:
        return "최종 세트는 조정 세션이나 세션 밖(검토자 도구)에서만 만듭니다 (06 7-3)"
    return None


def _apply_reasons(row: dict, extra: Iterable[str] = (), *, strict_block: bool = False, none: bool = False) -> dict:
    ev = row["eval"]
    rs_ = set(ev.get("excluded_reasons") or ()) | set(extra)
    reasons = [r for r in EXCLUDE_ORDER if r in rs_]
    ev["excluded_reasons"] = reasons
    ev["strict"] = (not reasons) and not strict_block and not none
    ev["lenient"] = all(r in LENIENT_OK for r in reasons) and not none
    return row


def _final_sessions(root: str, data: dict, primary: str | None, relabels: Iterable[str] | None,
                    adjs: Iterable[str] | None, origins: Iterable[str] | None) -> tuple[str, list[str], list[str], list[str], list[str]]:
    """세션 고르기. 라벨 파일은 열지 않는다(이름·잠금·계획만)."""
    from . import agreement_report as ar
    from . import relabel as relabel_io
    names = ar._session_ids_from_names(root)
    locked = {s for s in names if os.path.isfile(os.path.join(root, "locks", f"{s}.json"))}
    warns: list[str] = []
    if primary is None:
        cands = sorted(s for s in names if vis.session_kind_of_id(s) == vis.PRIMARY)
        if len(cands) != 1:
            raise ExportError(f"1차 세션이 {len(cands)}개입니다. --primary 로 하나를 고르세요")
        primary = cands[0]
    if vis.session_kind_of_id(primary) != vis.PRIMARY:
        raise ExportError("바탕 세션은 1차(primary) 세션이어야 합니다")
    if relabels is None:
        rl = []
        for s in sorted(locked):
            if vis.session_kind_of_id(s) not in (vis.RELABEL_SAME, vis.RELABEL_SECOND):
                continue
            pid = ar._read_lock_doc(root, s).get("relabel_plan_id")
            if not isinstance(pid, str):
                warns.append(f"{s}: 재라벨 계획 없이 연 세션이라 최종 세트에 넣지 않았습니다")
                continue
            try:
                plan = relabel_io.read_plan(root, pid)
            except (OSError, ValueError):
                warns.append(f"{s}: 재라벨 계획을 읽지 못해 최종 세트에 넣지 않았습니다")
                continue
            if plan.get("source_session_id") != primary:
                continue
            if relabel_io.is_cancelled(root, pid):
                warns.append(f"{s}: 재라벨 계획이 취소되어 최종 세트에 넣지 않았습니다 (06 7-1)")
                continue
            rl.append(s)
        relabels = rl
    relabels = list(relabels)
    for s in relabels:
        if vis.session_kind_of_id(s) not in (vis.RELABEL_SAME, vis.RELABEL_SECOND):
            raise ExportError("재라벨 세션 목록에 재라벨이 아닌 세션이 있습니다")
    if adjs is None:
        adjs = [s for s in names if vis.session_kind_of_id(s) == vis.ADJUDICATION]
    adjs = sorted(adjs)
    if any(vis.session_kind_of_id(s) != vis.ADJUDICATION for s in adjs):
        raise ExportError("조정 세션 목록에 조정이 아닌 세션이 있습니다")
    if origins is None:
        origins = [s for s in sorted(locked) if vis.session_kind_of_id(s) == vis.ORIGIN_REVIEW]
    origins = sorted(origins)
    if any(vis.session_kind_of_id(s) != vis.ORIGIN_REVIEW for s in origins):
        raise ExportError("출처 판정 세션 목록에 출처 판정이 아닌 세션이 있습니다")
    if len(adjs) > 1:
        warns.append(f"조정 세션이 {len(adjs)}개입니다 — 같은 대상의 조정 기록은 seq 가 큰 것을 씁니다(세션끼리는 순서를 모름)")
    return primary, relabels, adjs, origins, warns


def _read_session_files(root: str, sid: str, schema: LabelSchema | None) -> tuple[list[dict], int]:
    """잠기지 않은 세션(조정)의 기록 파일. 깨진 줄이 있는 파일은 통째로 뺀다(_load_labels 와 같은 규칙)."""
    labels_dir = os.path.join(root, "labels")
    recs: list[dict] = []
    bad = 0
    try:
        names = sorted(os.listdir(labels_dir))
    except FileNotFoundError:
        return recs, 0
    for name in names:
        p = parse_session_file_name(name)
        if p is None or p[0] != sid:
            continue
        res = jsonl.read(os.path.join(labels_dir, name), max_line_bytes=MAX_LINE_BYTES)
        if res.corrupt_lines:
            bad += 1
            continue
        recs.extend(r for r in res.records if r.get("session_id") == sid)
    return recs, bad


def build_final_set(project_dir: str, options: ExportOptions | None = None, *, primary_session_id: str | None = None,
                    relabel_session_ids: Iterable[str] | None = None,
                    adjudication_session_ids: Iterable[str] | None = None,
                    origin_session_ids: Iterable[str] | None = None, reader_kind: str | None = None,
                    label_schema: LabelSchema | None = None, ai_root: str | None = None) -> FinalSetExport:
    """최종 세트 행을 만든다(파일을 쓰지 않는다). 잠기지 않은 라벨 세션이 있으면 라벨 파일을 열기 전에 거부한다.

    계산된 노출(app.core.exposure — 계획 취소 뒤 만든 재라벨, 봉인 해제 때 안 잠겼던·해제 뒤 만든 라벨링 세션)은
    기록된 view.exposed 와 같이 view_exposed 로 친다: 그 세션의 행, 그리고 재라벨 세션이 노출이면 재라벨을 거친 행에
    view_exposed 를 단다(대표값에서 빠지고 숫자는 남는다). ai_root 는 봉인 기록 자리(기본 seal.default_ai_root).

    - primary_session_id: 바탕 1차 세션. 없으면 프로젝트의 1차 세션이 하나일 때 그것.
    - relabel_session_ids: 없으면 그 1차 세션의 계획(취소 안 됨)으로 만든 잠긴 재라벨 세션 전부.
    - adjudication_session_ids: 없으면 조정 세션 전부(잠기지 않아도 읽는다 — 조정 기록만 쓴다).
    - origin_session_ids: 없으면 잠긴 출처 판정 세션 전부. 하나도 없으면 '출처 미판정'.
    """
    from . import agreement_report as ar
    from .core import final_set as fsm
    opts = options or ExportOptions()
    root = os.path.abspath(project_dir)
    if os.path.basename(root).lower() == projmod.PROJECT_FILE:
        root = os.path.dirname(root)
    phase = None
    if reader_kind == vis.ANALYSIS:
        # 분석 세션은 봉인이 하나라도 해제된 뒤에만(seal.jsonl 의 ai.unsealed — 건수만 읽는다, 라벨 파일은 아직 열지 않음)
        from . import seal as sealmod
        try:
            phase = vis.COMPARISON if sealmod.seal_status(root, ai_root=ai_root,
                                                          reader_kind=reader_kind).unsealed_count > 0 else None
        except (sealmod.SealError, OSError):
            phase = None
    why = final_set_block_reason(reader_kind, phase)
    if why:
        raise FinalSetBlindError(why)
    proj = projmod.open_project(root)
    if proj.data is None or proj.read_only:
        raise ExportError(f"project.json 을 읽을 수 없거나 검증을 통과하지 못했습니다({len(proj.problems)}개 사유)")
    data = proj.data
    primary, relabels, adjs, origin_sids, warns = _final_sessions(
        root, data, primary_session_id, relabel_session_ids, adjudication_session_ids, origin_session_ids)
    label_sids = [primary] + relabels + origin_sids
    missing = [s for s in label_sids if not os.path.isfile(os.path.join(root, "locks", f"{s}.json"))]
    if missing:
        raise ar.NotLockedError(missing)
    gate_warn = ar.comparison_gate(root, data, [primary] + relabels)    # 06 7-1 — 라벨 파일은 아직 열지 않음
    warns.extend(gate_warn)
    if label_schema is None:
        try:
            from .store import load_label_schema
            label_schema = load_label_schema()
        except (OSError, StrictJsonError, ValueError) as e:
            raise ExportError(f"기록 스키마를 읽지 못했습니다: {type(e).__name__}") from None

    records: list[dict] = []
    locks: list[dict] = []
    invalid = 0
    metas: dict[str, dict] = {}
    for sid in label_sids:
        ls = ar.load_locked_session(root, sid)
        if ls.meta.get("project_id") not in (None, proj.project_id):
            raise ExportError("세션의 project_id 가 이 프로젝트와 다릅니다")
        metas[sid] = ls.meta
        locks.append({"session_id": sid, "lock_sha256": ls.lock_sha256, "locked_at": ls.lock.get("locked_at"),
                      "files": ls.files})
        if ls.after_lock_lines:
            warns.append(f"{sid}: 잠금 뒤 붙은 줄 {ls.after_lock_lines}개를 뺐습니다")
        for r in ls.records:
            if r.get("session_id") != sid or label_schema.check(r):
                invalid += 1
                continue
            records.append(r)
    for sid in adjs:
        recs, bad = _read_session_files(root, sid, label_schema)
        if bad:
            warns.append(f"{sid}: 깨진 줄이 있는 조정 기록 파일 {bad}개를 뺐습니다")
        for r in recs:
            if label_schema.check(r):
                invalid += 1
                continue
            records.append(r)
    gv = {(metas[s].get("guide_version"), metas[s].get("thresholds_sha256")) for s in [primary] + relabels}
    if len(gv) > 1:
        raise ExportError("1차와 재라벨 세션의 기준서 판이 달라 한 세트에 섞지 않습니다(06 9-4)")
    if invalid:
        warns.append(f"형식 검사를 통과하지 못한 기록 {invalid}줄을 뺐습니다.")

    view = rs.resolve(records)
    chats, vno, msha, mwarn = _read_messages(proj.paths.messages_jsonl, opts)
    warns.extend(mwarn)
    b = _RowBuilder(view, data, chats, opts)
    gv0 = next(iter(gv))[0]
    buffer_ms = b.rules.buffer_ms(gv0)
    # 계산된 노출 (06 6-6 10번): 라벨 형식은 고정이라(D-15 7) 기록하지 않고 여기서 계산한다
    from . import exposure_io
    lock_at = {l["session_id"]: l.get("locked_at") for l in locks}
    xview, xwarn = exposure_io.compute_for(
        root, [exposure_io.session_info_of(s, view.other_records, lock_at.get(s)) for s in [primary] + relabels],
        ai_root=ai_root, reader_kind=reader_kind)
    warns.extend(xwarn)
    computed_exposed = sorted(xview.exposed_any())        # AI 결과 노출 + 훈련 열기 노출(규칙 5, D-18 7)
    for s in computed_exposed:
        b.sessions.setdefault(s, _SessionInfo()).exposed = True
    relabel_exposed = any(s in relabels for s in computed_exposed) or any(
        (b.sessions.get(s) or _SessionInfo()).exposed for s in relabels)

    def side(sid: str) -> dict:
        return {"session_id": sid, "labeler_id": sid.split("-", 1)[0], "scope": ar._scope_ranges(metas[sid]),
                "segments": view.items(kind="segment", session_id=sid),
                "reactions": view.items(kind="reaction", session_id=sid)}

    adj_recs = [r for s in adjs for r in view.items(kind="adjudication", session_id=s)]
    origins = None
    if origin_sids:
        per = []
        for s in origin_sids:
            per.append({(r.get("body") or {}).get("chat_id"): (r.get("body") or {}).get("origin")
                        for r in view.items(kind="origin", session_id=s)})
        origins = fsm.merge_origins(per)
    chains = [c.versions for c in view.conflicts() if c.session_id == primary and c.kind == "reaction"]
    fs = fsm.build_final_set(side(primary), [side(s) for s in relabels], adj_recs, origins=origins,
                             conflict_chains=chains, buffer_ms=buffer_ms)

    items: list[dict] = []
    for st, group in ((SET_FINAL, fs.reactions), (SET_EXCLUDED, fs.excluded), (SET_AMBIGUOUS, fs.ambiguous)):
        for it in group:
            row = b.reaction(it.record)
            row["source"] = it.source
            extra_r = list(it.reasons) + (["view_exposed"] if relabel_exposed and it.relabeled else [])
            _apply_reasons(row, extra_r, strict_block=fsm.R_ADJ_DISAGREEMENT in it.report_reasons,
                           none=st == SET_AMBIGUOUS)
            items.append({"set": st, "row": row, "report_reasons": list(it.report_reasons),
                          "relabeled": it.relabeled, "agreed": it.agreed, "stale": it.stale})
    seg_items: list[dict] = []
    for pc in fs.pieces:
        rec = copy.deepcopy(pc["record"])
        body = dict(rec.get("body") or {})
        body.update(start_ms=pc["start_ms"], end_ms=pc["end_ms"])
        if pc["status"] == fsm.ST_ADJUDICATED:
            body.update(category=pc["category"], confidence=pc["confidence"])
        rec["body"] = body
        row = b.segment(rec)
        st = {fsm.ST_EXCLUDED: SET_EXCLUDED, fsm.ST_AMBIGUOUS: SET_AMBIGUOUS}.get(pc["status"], SET_FINAL)
        extra = ["human_disagreement"] if pc["status"] == fsm.ST_DISAGREE else []
        if relabel_exposed and pc["relabeled"]:
            extra.append("view_exposed")
        if st == SET_EXCLUDED:
            extra.append("adjudicated_exclude")
        if pc["status"] in (fsm.ST_ADJUDICATED, fsm.ST_EXCLUDED, fsm.ST_AMBIGUOUS):
            row["source"] = "adjudication"
        _apply_reasons(row, extra, strict_block=pc["status"] == fsm.ST_ADJUDICATED, none=st == SET_AMBIGUOUS)
        piece = {k: pc[k] for k in ("start_ms", "end_ms", "category", "confidence", "status", "relabeled")}
        seg_items.append({"set": st, "row": row, "piece": piece,
                          "report_reasons": [fsm.R_ADJ_DISAGREEMENT] if pc["status"] == fsm.ST_ADJUDICATED else
                          ([fsm.R_ADJ_AMBIGUOUS] if st == SET_AMBIGUOUS else [])})
    other_rows: list[dict] = []
    for rec in view.items(session_id=primary):
        k = rec.get("kind")
        if k == "missed_candidate":
            other_rows.append(b.missed_candidate(rec))
        elif k == "continuation" and _continuation_target_ok(view, rec):
            other_rows.append(b.continuation(rec))
    if not fs.origin_known:
        warns.append("출처 판정이 없어 '출처 미판정'(origin_unknown) 입니다 — 가상 채팅이 섞였는지 모릅니다 (06 6-5)")
    sm = fs.summary["reactions"]
    if sm["adjudications_unmatched"] or fs.summary["segments"]["unmatched"]:
        warns.append(f"지금 라벨과 이어지지 않는 조정 기록: 반응 {sm['adjudications_unmatched']} · "
                     f"구간 {fs.summary['segments']['unmatched']}건 (대상이 바뀜)")
    if sm["stale"] or fs.summary["segments"]["stale"]:
        warns.append(f"조정 뒤 대상 판이 바뀐 기록(stale): 반응 {sm['stale']} · 구간 {fs.summary['segments']['stale']}건")
    info = {
        "project": data, "project_root": root, "primary_session_id": primary, "relabel_session_ids": relabels,
        "adjudication_session_ids": adjs, "origin_session_ids": origin_sids, "locks": locks, "warnings": warns,
        "messages_sha256": msha, "viewer_no": vno, "sessions": b.sessions, "guide_version": gv0,
        "thresholds_sha256": next(iter(gv))[1], "buffer_ms": buffer_ms, "invalid_records": invalid,
        "metas": metas, "computed_exposed": {s: list(xview.reasons(s)) for s in computed_exposed},
    }
    return FinalSetExport(items, seg_items, other_rows, list(fs.conflicts), fs, info)


def export_final_set(project_dir: str, out_path: str, options: ExportOptions | None = None, *,
                     primary_session_id: str | None = None, relabel_session_ids: Iterable[str] | None = None,
                     adjudication_session_ids: Iterable[str] | None = None,
                     origin_session_ids: Iterable[str] | None = None, reader_kind: str | None = None,
                     created_by: str | None = None, clock: Callable[[], datetime] | None = None,
                     uuid_factory: Callable[[], str] | None = None, app_version: str | None = None,
                     ai_root: str | None = None) -> ExportResult:
    """최종 세트(참고 정답 세트)를 zip 하나로 내보낸다. 출력 위치 규칙은 export_labels 와 같다(먼저 검사)."""
    opts = options or ExportOptions()
    root = os.path.abspath(project_dir)
    if os.path.basename(root).lower() == projmod.PROJECT_FILE:
        root = os.path.dirname(root)
    loc_warn = check_output_path(out_path, root)
    if os.path.exists(out_path) and not opts.overwrite:
        raise FileExistsError("출력 파일이 이미 있습니다(덮어쓰려면 overwrite)")
    row_schema, man_schema = _load_export_schema()
    fx = build_final_set(root, opts, primary_session_id=primary_session_id, relabel_session_ids=relabel_session_ids,
                         adjudication_session_ids=adjudication_session_ids, origin_session_ids=origin_session_ids,
                         reader_kind=reader_kind, ai_root=ai_root)
    data = fx.info["project"]
    warns: list[str] = loc_warn + fx.info["warnings"]
    every = [(it["set"], it["row"]) for it in fx.segment_items + fx.items] + [(SET_FINAL, r) for r in fx.other_rows]
    good: list[dict] = []
    amb: list[dict] = []
    bad = 0
    excluded: dict[str, int] = {}
    for st, r in every:
        if row_schema.check(r):
            bad += 1
            continue
        rsn = list(r["eval"]["excluded_reasons"])
        if st == SET_AMBIGUOUS:       # 열거형 밖이라 행에는 없고 건수에만 (adjudicated_exclude 바로 뒤 순서)
            cut = EXCLUDE_ORDER.index("adjudicated_exclude") + 1
            rsn = [x for x in rsn if x in EXCLUDE_ORDER[:cut]] + [_AMBIGUOUS_REASON] + \
                [x for x in rsn if x not in EXCLUDE_ORDER[:cut]]
        if rsn:
            excluded[rsn[0]] = excluded.get(rsn[0], 0) + 1
        if st == SET_AMBIGUOUS:
            amb.append(r)
        elif st == SET_FINAL and r["eval"]["lenient"]:
            good.append(r)
    if fx.conflicts:
        excluded["conflict_unresolved"] = excluded.get("conflict_unresolved", 0) + len(fx.conflicts)
    if bad:
        warns.append(f"내보내기 행 형식(export-row)에 맞지 않는 행 {bad}개를 뺐습니다.")
    order = {k: i for i, k in enumerate(ROW_KINDS)}
    good.sort(key=lambda r: (order.get(r["label_kind"], 9), r.get("start_ms") or r.get("chat_t_ms") or 0, r["label_id"]))
    files: list[tuple[str, bytes, int]] = [(JSONL_NAME, b"".join(jsonl.dumps_line(r) for r in good), len(good))]
    files.append((AMBIGUOUS_NAME, b"".join(jsonl.dumps_line(r) for r in amb), len(amb)))
    for name, kind in ((CSV_SEGMENTS, "segment"), (CSV_REACTIONS, "reaction"), (CSV_MISSED, "missed_candidate")):
        data_b, n = _csv_bytes(name, [r for r in good if r["label_kind"] == kind], fx.info["viewer_no"], opts)
        files.append((name, data_b, n))
    by_kind = {k: 0 for k in ROW_KINDS}
    for r in good:
        by_kind[r["label_kind"]] += 1
    sessions = [fx.info["primary_session_id"]] + fx.info["relabel_session_ids"] + fx.info["origin_session_ids"] \
        + fx.info["adjudication_session_ids"]
    pj_guides = {g.get("guide_version"): g for g in data.get("guides") or [] if isinstance(g, dict)}
    gvs = fx.info["guide_version"]
    s0 = fx.info["sessions"].get(fx.info["primary_session_id"])
    guides = []
    if s0 is not None and s0.guide_version and s0.guide_doc_sha256 and s0.thresholds_sha256:
        guides.append({"guide_version": s0.guide_version, "doc_sha256": s0.guide_doc_sha256,
                       "thresholds_sha256": s0.thresholds_sha256})
    elif gvs in pj_guides:
        pg = pj_guides[gvs]
        guides.append({"guide_version": gvs, "doc_sha256": pg["doc_sha256"], "thresholds_sha256": pg["thresholds_sha256"]})
    cal = data.get("calibration") or {}
    active = cal.get("active_rev", 0)
    crev = next((c for c in cal.get("revs") or [] if isinstance(c, dict) and c.get("rev") == active), {})
    now = (clock or (lambda: datetime.now().astimezone()))()
    if now.tzinfo is None:
        now = now.astimezone()
    if app_version is None:
        from . import __version__ as app_version  # noqa: N811
    manifest = {
        "schema": "chatcoach/export-manifest", "schema_version": 1,
        "export_id": (uuid_factory or (lambda: str(uuid.uuid4())))(),
        "export_type": FINAL_EXPORT_TYPE, "project_id": data["project_id"],
        "video_no": str((data.get("broadcast") or {}).get("video_no")),
        "created_at": now.isoformat(timespec="milliseconds"),
        "created_by": _labeler_of(data, None, created_by), "app_version": app_version,
        "filters": {"guide_versions": [gvs] if gvs else None, "session_ids": sessions, "include_history": False,
                    "include_text": bool(opts.include_text), "include_nickname_masked": bool(opts.include_nickname_masked),
                    "include_heard_text": bool(opts.include_heard_text), "include_protected": False},
        "guides": guides,
        "calibration": {"active_rev": active, "offset_ms": crev.get("offset_ms", 0),
                        "status": crev.get("status", "unverified")},
        "messages_sha256": fx.info["messages_sha256"],
        "files": [{"name": n, "size": len(b_), "sha256": hashlib.sha256(b_).hexdigest(), "rows": k} for n, b_, k in files],
        "counts": {"by_kind": by_kind, "excluded": excluded, "invalid_records": fx.info["invalid_records"]},
        "warnings": warns, "notes": FINAL_NOTES,
    }
    problems = man_schema.check(manifest)
    if problems:
        raise ExportError("매니페스트가 형식에 맞지 않습니다: " + "; ".join(problems[:3]))
    files.append((MANIFEST_NAME, (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
                  .encode("utf-8"), None))
    _write_zip(out_path, files, now, FINAL_ZIP_NAMES)
    log.info("최종 세트 내보내기: 행 %d, 모호 %d, 경고 %d", len(good), len(amb), len(warns))
    return ExportResult(out_path=os.path.abspath(out_path), manifest=manifest, rows=len(good), by_kind=by_kind,
                        session_ids=sessions, warnings=warns)


__all__ = [
    "safe_cell", "check_output_path", "build_rows", "export_labels", "ExportOptions", "ExportResult",
    "ExportError", "ExportLocationError", "ZIP_NAMES", "CSV_COLUMNS", "EXCLUDE_ORDER", "EXPORT_TYPE",
    "build_final_set", "export_final_set", "FinalSetExport", "FinalSetBlindError", "final_set_block_reason",
    "FINAL_EXPORT_TYPE", "FINAL_ZIP_NAMES", "AMBIGUOUS_NAME", "SET_FINAL", "SET_EXCLUDED", "SET_AMBIGUOUS",
]
