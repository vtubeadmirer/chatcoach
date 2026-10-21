"""채팅 출처 판정 — 대상 목록·판정 검증·출처 일치도 (06 4-0·4-7, 03 5-6·6-11·12-1·12-3, 08 D-12 6).

출처 판정 세션(origin_review, 코드 OR)이 하는 일
- 대상 목록(origin/ 의 대상 파일, 아래 '대상 파일')이 정한 채팅마다 출처를 실제·가상·판별 불가로 정하고,
  근거 한 줄과 확신도를 남긴다. 대상 밖 채팅에는 출처 라벨을 만들지 않는다(정해진 모집단이어야 일치도를 낸다).
- 생성 기록과 대조해 정해지는 것(method = generation_log_match · viewer_id_match)은 '자동' 표시만 한다(D-12 6).
  대조를 하는 생성기는 아직 없다. 대상 파일이 항목마다 자동 대조 결과(auto)를 들고 올 수 있고, 화면은 그것을
  '자동' 배지로 보이며, 검토자가 받으면 그 method·generation_ref 로 저장한다. 검토자가 다른 출처를 고르면 수동이다.

대상 파일 `origin/<target_id>.json` (이 모듈이 정한 형식, 03 에 아직 없음 — 보고의 '설계와 다르게 한 곳')
    {
      "schema": "chatcoach-origin-targets-v1",
      "target_id": "OT-20261021-3fa9",          # 'OT-<만든 날>-<chat_id 목록 해시 앞 4자>' (난수 없음)
      "created_at": "2026-10-21T15:00:00+09:00", "created_by": "L01",
      "basis": "manual",                        # 대상을 고른 방법(자유 문자열, 100자 이하, 예: 'sample_items', 'manual')
      "note": "",                               # 선택, 300자 이하
      "chat_ids_sha256": "…",                   # items 의 chat_id 를 order 순으로 '\n' 으로 이은 UTF-8 의 SHA-256
      "items": [
        {"order": 1, "chat_id": "c…", "auto": null},
        {"order": 2, "chat_id": "c…", "auto": {"origin": "virtual", "method": "generation_log_match",
                                               "generation_ref": {"file": "gen-….jsonl", "gen_id": "g17"},
                                               "score": 0.93, "second_score": 0.41}}
      ]
    }
  본문·닉네임은 넣지 않는다(채팅은 chat_id 로만 가리킨다).

출처 일치도 (06 4-7, D-12 6: 혼동표·명목 κ 까지)
- compare_origins(A 라벨들, B 라벨들, targets=…) → 3×3 혼동표(행 = A, 열 = B, 순서 real·virtual·undeterminable)와
  명목 κ(app.core.agreement.kappa_from_table). '전체' 와 '사람이 정한 짝'(양쪽 모두 자동 method 가 아님),
  '자동이 낀 짝'(한쪽이라도 자동)을 나눠 낸다(06 4-7 '나눠 보인다').
- 결과에는 숫자·분류·chat_id 만 남긴다. 근거(rationale)는 넣지 않는다 — 닉네임이 들어갈 수 있다(agreement 와 같은 규칙).

core 층: 파일·시계·난수·Qt 를 쓰지 않는다. 입력 dict 를 고치지 않는다.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .agreement import kappa_from_table
from .ids import CHAT_ID_RE

# ----------------------------------------------------------------------------- 값 (03 5-6, label-event originBody)
REAL = "real"
VIRTUAL = "virtual"
UNDETERMINABLE = "undeterminable"        # 출처 '판별 불가' (확신도 '판단 불가' 와 다른 칸)
ORIGINS = (REAL, VIRTUAL, UNDETERMINABLE)
ORIGIN_KO = {REAL: "실제", VIRTUAL: "가상", UNDETERMINABLE: "판별 불가"}

M_GENERATION = "generation_log_match"
M_VIEWER_ID = "viewer_id_match"
M_OCR = "ocr_match"
M_MANUAL = "manual"
M_NONE = "none"
METHODS = (M_GENERATION, M_VIEWER_ID, M_OCR, M_MANUAL, M_NONE)
METHOD_KO = {M_GENERATION: "생성 기록 대조", M_VIEWER_ID: "봇 계정 가명 일치", M_OCR: "OCR 대조",
             M_MANUAL: "수동", M_NONE: "근거 없음"}
AUTO_METHODS = frozenset({M_GENERATION, M_VIEWER_ID})   # '생성 기록과 대조해 결정되는 것' (D-12 6)

CONFIDENCES = ("certain", "probably")    # 실제·가상일 때 고르는 확신도 (안내서 v0.2 5장 [제안])
CONF_KO = {"certain": "확실함", "probably": "아마도"}

RATIONALE_MAX = 300
INVALID = "invalid"                      # 모양이 맞지 않는 출처 라벨(비교에서 따로 셈)

TARGETS_SCHEMA = "chatcoach-origin-targets-v1"
TARGET_ID_RE = re.compile(r"\AOT-[0-9]{8}-[0-9a-f]{4}\Z")
TARGETS_MAX_ITEMS = 100_000
_REF_FILE_MAX = 200
_REF_GEN_MAX = 100
_BASIS_MAX = 100
_NOTE_MAX = 300
_DATE_RE = re.compile(r"\A([0-9]{4})-([0-9]{2})-([0-9]{2})T")

# 06 4-7 마지막 줄을 보고서에 그대로 붙이는 문구
LOW_AGREEMENT_NOTE = ("출처 일치도가 낮으면 라벨러 문제보다 생성 기록·OCR 정렬 문제일 가능성이 큽니다 (06 4-7).")
AUTO_NOTE = ("'자동' 은 생성 기록 대조(generation_log_match)·봇 계정 가명 일치(viewer_id_match)로 정한 판정입니다. "
             "같은 대조 결과를 두 사람이 받으면 일치가 부풀므로 '사람이 정한 짝' 의 κ 를 따로 봅니다 (06 4-7, D-12 6).")


class OriginError(ValueError):
    """대상 파일·출처 초안이 규칙에 맞지 않음. 메시지에 본문·근거를 넣지 않는다(위치·사유만)."""


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _body(rec: Mapping) -> Mapping:
    b = rec.get("body") if isinstance(rec, Mapping) else None
    return b if isinstance(b, Mapping) else (rec if isinstance(rec, Mapping) else {})


# ----------------------------------------------------------------------------- 대상 파일
@dataclass(frozen=True)
class AutoHint:
    """대상 항목에 붙은 자동 대조 결과. '자동' 배지로 보이기만 하고, 검토자가 받아야 라벨이 된다."""
    origin: str
    method: str
    generation_ref: dict | None = None
    score: float | None = None
    second_score: float | None = None

    def as_dict(self) -> dict:
        return {"origin": self.origin, "method": self.method,
                "generation_ref": copy.deepcopy(self.generation_ref),
                "score": self.score, "second_score": self.second_score}


@dataclass(frozen=True)
class TargetItem:
    order: int
    chat_id: str
    auto: AutoHint | None = None


@dataclass(frozen=True)
class OriginTargets:
    target_id: str
    created_at: str
    created_by: str
    basis: str
    note: str
    chat_ids_sha256: str
    items: tuple[TargetItem, ...]

    @property
    def chat_ids(self) -> tuple[str, ...]:
        return tuple(it.chat_id for it in self.items)

    def item_of(self, chat_id: str) -> TargetItem | None:
        for it in self.items:
            if it.chat_id == chat_id:
                return it
        return None


def chat_ids_digest(chat_ids: Sequence[str]) -> str:
    """대상 chat_id 목록(order 순) → SHA-256 hex. '\n' 으로 잇고 UTF-8 로 해시한다."""
    return hashlib.sha256("\n".join(chat_ids).encode("utf-8")).hexdigest()


def make_target_id(created_at: str, chat_ids: Sequence[str]) -> str:
    """'OT-<yyyymmdd>-<목록 해시 앞 4자>'. 날짜는 created_at(ISO 8601)의 날짜 부분. 같은 날 같은 목록이면 같은 ID."""
    m = _DATE_RE.match(created_at) if isinstance(created_at, str) else None
    if not m:
        raise OriginError("created_at 이 ISO 8601 이 아닙니다")
    return f"OT-{m.group(1)}{m.group(2)}{m.group(3)}-{chat_ids_digest(chat_ids)[:4]}"


def _check_ref(ref, where: str, problems: list[str]) -> None:
    if ref is None:
        return
    if not isinstance(ref, Mapping) or set(ref) != {"file", "gen_id"}:
        problems.append(f"{where}: generation_ref 는 {{file, gen_id}} 여야 합니다")
        return
    f, g = ref.get("file"), ref.get("gen_id")
    if not isinstance(f, str) or not f or len(f) > _REF_FILE_MAX or "/" in f or "\\" in f or f.startswith("."):
        problems.append(f"{where}: generation_ref.file 은 origin/ 안 파일 이름이어야 합니다 (경로·빈 값 불가)")
    if not isinstance(g, str) or not g or len(g) > _REF_GEN_MAX:
        problems.append(f"{where}: generation_ref.gen_id 는 1~{_REF_GEN_MAX}자 문자열이어야 합니다")


def _check_scores(score, second, where: str, problems: list[str]) -> None:
    for name, v in (("score", score), ("second_score", second)):
        if v is not None and (not _is_num(v) or not 0 <= v <= 1):
            problems.append(f"{where}: {name} 는 0~1 의 수 또는 null 이어야 합니다")
    if _is_num(score) and _is_num(second) and second > score:
        problems.append(f"{where}: second_score 가 score 보다 큽니다")
    if second is not None and score is None:
        problems.append(f"{where}: score 없이 second_score 만 있습니다")


def _parse_auto(a, where: str, problems: list[str]) -> AutoHint | None:
    if a is None:
        return None
    if not isinstance(a, Mapping):
        problems.append(f"{where}: auto 는 object 또는 null 이어야 합니다")
        return None
    extra = set(a) - {"origin", "method", "generation_ref", "score", "second_score"}
    if extra:
        problems.append(f"{where}: auto 에 모르는 키 {sorted(extra)}")
    o, m = a.get("origin"), a.get("method")
    n0 = len(problems)
    if o not in ORIGINS:
        problems.append(f"{where}: auto.origin 값이 아닙니다")
    if m not in AUTO_METHODS:
        problems.append(f"{where}: auto.method 는 generation_log_match 또는 viewer_id_match 여야 합니다")
    ref = a.get("generation_ref")
    _check_ref(ref, where + ".auto", problems)
    if m == M_GENERATION and ref is None:
        problems.append(f"{where}: generation_log_match 자동 결과에는 generation_ref 가 있어야 합니다")
    _check_scores(a.get("score"), a.get("second_score"), where + ".auto", problems)
    if len(problems) > n0:
        return None
    return AutoHint(o, m, dict(ref) if ref is not None else None, a.get("score"), a.get("second_score"))


def parse_targets(obj: Mapping) -> OriginTargets:
    """대상 파일 dict → OriginTargets. 어기면 OriginError(문제를 모두 모아 한 메시지로)."""
    if not isinstance(obj, Mapping):
        raise OriginError("대상 파일이 object 가 아닙니다")
    p: list[str] = []
    allowed = {"schema", "target_id", "created_at", "created_by", "basis", "note", "chat_ids_sha256", "items"}
    extra = set(obj) - allowed
    if extra:
        p.append(f"$: 모르는 키 {sorted(extra)}")
    if obj.get("schema") != TARGETS_SCHEMA:
        raise OriginError(f"출처 대상 파일이 아닙니다 (schema 가 {TARGETS_SCHEMA} 가 아님)")
    tid = obj.get("target_id")
    if not isinstance(tid, str) or not TARGET_ID_RE.match(tid):
        p.append("$.target_id: 'OT-<yyyymmdd>-<4자>' 형식이 아닙니다")
    ca = obj.get("created_at")
    if not isinstance(ca, str) or not _DATE_RE.match(ca):
        p.append("$.created_at: ISO 8601 이 아닙니다")
    by = obj.get("created_by")
    if not isinstance(by, str) or not re.match(r"\A[A-Z][A-Z0-9]{1,7}\Z", by):
        p.append("$.created_by: 라벨러 ID 형식이 아닙니다")
    basis = obj.get("basis")
    if not isinstance(basis, str) or not basis or len(basis) > _BASIS_MAX:
        p.append(f"$.basis: 1~{_BASIS_MAX}자 문자열이어야 합니다")
    note = obj.get("note", "")
    if not isinstance(note, str) or len(note) > _NOTE_MAX:
        p.append(f"$.note: {_NOTE_MAX}자 이하 문자열이어야 합니다")
    raw = obj.get("items")
    items: list[TargetItem] = []
    if not isinstance(raw, list) or not raw:
        p.append("$.items: 비어 있지 않은 배열이어야 합니다")
        raw = []
    elif len(raw) > TARGETS_MAX_ITEMS:
        p.append(f"$.items: 항목이 너무 많습니다 (> {TARGETS_MAX_ITEMS})")
        raw = []
    seen_c: set[str] = set()
    seen_o: set[int] = set()
    for i, it in enumerate(raw):
        w = f"$.items[{i}]"
        if not isinstance(it, Mapping):
            p.append(f"{w}: object 가 아닙니다")
            continue
        ex = set(it) - {"order", "chat_id", "auto"}
        if ex:
            p.append(f"{w}: 모르는 키 {sorted(ex)}")
        o, c = it.get("order"), it.get("chat_id")
        if not _is_int(o) or o < 1:
            p.append(f"{w}.order: 1 이상의 정수가 아닙니다")
        elif o in seen_o:
            p.append(f"{w}.order: 겹칩니다 ({o})")
        if not isinstance(c, str) or not CHAT_ID_RE.match(c):
            p.append(f"{w}.chat_id: 채팅 ID 형식이 아닙니다")
        elif c in seen_c:
            p.append(f"{w}.chat_id: 같은 채팅이 두 번 있습니다")
        hint = _parse_auto(it.get("auto"), w, p)
        if _is_int(o) and isinstance(c, str):
            seen_o.add(o)
            seen_c.add(c)
            items.append(TargetItem(o, c, hint))
    items.sort(key=lambda t: t.order)
    if not p:
        orders = [t.order for t in items]
        if orders != list(range(1, len(items) + 1)):
            p.append("$.items: order 가 1 부터 빈틈없이 이어지지 않습니다")
        dig = chat_ids_digest([t.chat_id for t in items])
        if obj.get("chat_ids_sha256") != dig:
            p.append("$.chat_ids_sha256: items 의 chat_id 목록과 맞지 않습니다")
        elif tid != make_target_id(ca, [t.chat_id for t in items]):
            p.append("$.target_id: created_at 날짜·목록 해시와 맞지 않습니다")
    if p:
        raise OriginError("; ".join(p))
    return OriginTargets(tid, ca, by, basis, note, obj["chat_ids_sha256"], tuple(items))


def build_targets(*, chat_ids: Sequence[str], created_at: str, created_by: str, basis: str = "manual",
                  note: str = "", auto: Mapping[str, Mapping] | None = None) -> dict:
    """대상 파일 dict 를 만든다(쓰기는 화면 쪽 write_targets_file). chat_ids 순서가 order 다.

    auto: chat_id → 자동 대조 결과 dict({origin, method, generation_ref?, score?, second_score?}). 대상 밖 키는 거부.
    만든 dict 는 parse_targets 를 통과한다(통과하지 못하면 OriginError).
    """
    ids = list(chat_ids)
    auto = dict(auto or {})
    stray = set(auto) - set(ids)
    if stray:
        raise OriginError(f"auto 에 대상 밖 채팅이 {len(stray)}건 있습니다")
    items = []
    for i, c in enumerate(ids, 1):
        a = auto.get(c)
        if a is not None:
            a = {"origin": a.get("origin"), "method": a.get("method"),
                 "generation_ref": copy.deepcopy(a.get("generation_ref")),
                 "score": a.get("score"), "second_score": a.get("second_score")}
        items.append({"order": i, "chat_id": c, "auto": a})
    obj = {
        "schema": TARGETS_SCHEMA,
        "target_id": make_target_id(created_at, ids),
        "created_at": created_at,
        "created_by": created_by,
        "basis": basis,
        "note": note,
        "chat_ids_sha256": chat_ids_digest(ids),
        "items": items,
    }
    parse_targets(obj)
    return obj


@dataclass(frozen=True)
class CombinedTargets:
    """여러 대상 파일을 합친 판정 목록. 같은 chat_id 는 앞 파일(target_id 순) 것 하나만 남긴다."""
    items: tuple[tuple[str, TargetItem], ...]       # (target_id, 항목) — 판정 순서
    duplicates: int                                  # 다른 파일과 겹쳐 뺀 수
    target_ids: tuple[str, ...]

    @property
    def chat_ids(self) -> tuple[str, ...]:
        return tuple(it.chat_id for _, it in self.items)

    def item_of(self, chat_id: str) -> TargetItem | None:
        for _, it in self.items:
            if it.chat_id == chat_id:
                return it
        return None


def combine_targets(targets: Iterable[OriginTargets]) -> CombinedTargets:
    ts = sorted(targets, key=lambda t: t.target_id)
    seen: set[str] = set()
    out: list[tuple[str, TargetItem]] = []
    dup = 0
    for t in ts:
        for it in t.items:
            if it.chat_id in seen:
                dup += 1
                continue
            seen.add(it.chat_id)
            out.append((t.target_id, it))
    return CombinedTargets(tuple(out), dup, tuple(t.target_id for t in ts))


def _target_set(targets) -> frozenset | None:
    if targets is None:
        return None
    if isinstance(targets, (OriginTargets, CombinedTargets)):
        return frozenset(targets.chat_ids)
    return frozenset(targets)


# ----------------------------------------------------------------------------- 판정 검증 (originBody + 이 모듈의 규칙)
_BODY_KEYS = {"chat_id", "origin", "method", "generation_ref", "ocr_ref", "score", "second_score",
              "confidence", "rationale"}
_REQUIRED = ("chat_id", "origin", "method", "confidence", "rationale")


def validate_origin_body(body: Mapping, *, targets=None) -> list[str]:
    """출처 라벨 body 의 문제 목록(비면 통과). 스키마 originBody 규칙에 더해 아래를 본다.

    - 실제·가상: 확신도 certain·probably 만(스키마는 판단 불가도 받지만 안내서 v0.2 5장은 '판별 불가' 를 고르라고 함).
    - 판별 불가: 확신도 null (안내서 v0.2 5장 '확신도를 따로 적지 않는다').
    - generation_log_match 는 generation_ref, ocr_match 는 ocr_ref 가 있어야 한다. method none 은 판별 불가에만.
    - 근거는 한 줄(줄바꿈 없음), 앞뒤 공백을 뺀 1~300자.
    - targets(대상 파일·합친 목록·chat_id 모음)를 주면 그 안의 채팅만.
    """
    if not isinstance(body, Mapping):
        return ["$.body: object 가 아닙니다"]
    p: list[str] = []
    extra = set(body) - _BODY_KEYS
    if extra:
        p.append(f"$.body: 모르는 키 {sorted(extra)}")
    for k in _REQUIRED:
        if k not in body:
            p.append(f"$.body.{k}: 없습니다")
    c = body.get("chat_id")
    if not isinstance(c, str) or not CHAT_ID_RE.match(c):
        p.append("$.body.chat_id: 채팅 ID 형식이 아닙니다")
    else:
        ts = _target_set(targets)
        if ts is not None and c not in ts:
            p.append("$.body.chat_id: 출처 판정 대상 목록에 없는 채팅입니다")
    o = body.get("origin")
    if o not in ORIGINS:
        p.append("$.body.origin: 실제·가상·판별 불가 중 하나가 아닙니다")
    m = body.get("method")
    if m not in METHODS:
        p.append("$.body.method: 판정 근거 값이 아닙니다")
    conf = body.get("confidence")
    if o in (REAL, VIRTUAL):
        if conf not in CONFIDENCES:
            p.append("$.body.confidence: 실제·가상이면 확신도(확실함·아마도)를 골라야 합니다")
    elif o == UNDETERMINABLE and conf is not None:
        p.append("$.body.confidence: 판별 불가면 확신도를 적지 않습니다 (null)")
    ref = body.get("generation_ref")
    _check_ref(ref, "$.body", p)
    if m == M_GENERATION and ref is None:
        p.append("$.body.generation_ref: 생성 기록 대조면 생성 기록 항목이 있어야 합니다")
    ocr = body.get("ocr_ref")
    if ocr is not None:
        if (not isinstance(ocr, Mapping) or set(ocr) != {"frame_ms", "line_index"}
                or not _is_int(ocr.get("frame_ms")) or not -3_600_000 <= ocr["frame_ms"] <= 172_800_000
                or not _is_int(ocr.get("line_index")) or ocr["line_index"] < 0):
            p.append("$.body.ocr_ref: {frame_ms, line_index} 형식이 아닙니다")
    if m == M_OCR and ocr is None:
        p.append("$.body.ocr_ref: OCR 대조면 프레임 시각·줄이 있어야 합니다")
    if m == M_NONE and o in (REAL, VIRTUAL):
        p.append("$.body.method: 근거 없음은 판별 불가에만 씁니다")
    _check_scores(body.get("score"), body.get("second_score"), "$.body", p)
    r = body.get("rationale")
    if not isinstance(r, str) or not r.strip():
        p.append("$.body.rationale: 근거 한 줄이 비었습니다")
    else:
        if len(r) > RATIONALE_MAX:
            p.append(f"$.body.rationale: {RATIONALE_MAX}자 이하여야 합니다")
        if "\n" in r or "\r" in r:
            p.append("$.body.rationale: 한 줄이어야 합니다 (줄바꿈 불가)")
        if r != r.strip():
            p.append("$.body.rationale: 앞뒤 공백을 빼고 적습니다")
    return p


def check_origin_body(body: Mapping, *, targets=None) -> None:
    p = validate_origin_body(body, targets=targets)
    if p:
        raise OriginError("; ".join(p))


def origin_draft(*, chat_id: str, origin: str, rationale: str, confidence: str | None = None,
                 method: str = M_MANUAL, generation_ref: Mapping | None = None, ocr_ref: Mapping | None = None,
                 score: float | None = None, second_score: float | None = None,
                 head: tuple[str, str] | None = None, targets=None) -> dict:
    """store.append_label 에 넘길 출처 라벨 초안. head = (entity_id, 현재 머리 판 id) 면 그 대상의 새 판.

    판별 불가면 confidence 는 무시하고 null 로 둔다. 근거는 앞뒤 공백을 벗긴다. 규칙을 어기면 OriginError.
    """
    body = {
        "chat_id": chat_id,
        "origin": origin,
        "method": method,
        "generation_ref": copy.deepcopy(dict(generation_ref)) if generation_ref is not None else None,
        "ocr_ref": copy.deepcopy(dict(ocr_ref)) if ocr_ref is not None else None,
        "score": score,
        "second_score": second_score,
        "confidence": None if origin == UNDETERMINABLE else confidence,
        "rationale": (rationale or "").strip() if isinstance(rationale, str) else rationale,
    }
    check_origin_body(body, targets=targets)
    d: dict[str, Any] = {"kind": "origin", "body": body}
    if head is not None:
        d["entity_id"], d["supersedes"] = head
    return d


def draft_from_auto(hint: AutoHint, *, chat_id: str, rationale: str, confidence: str | None,
                    head: tuple[str, str] | None = None, targets=None) -> dict:
    """자동 대조 결과를 검토자가 받은 초안. method·generation_ref·점수는 자동 결과 그대로, 확신도·근거는 검토자 것."""
    return origin_draft(chat_id=chat_id, origin=hint.origin, rationale=rationale, confidence=confidence,
                        method=hint.method, generation_ref=hint.generation_ref, score=hint.score,
                        second_score=hint.second_score, head=head, targets=targets)


def is_auto(rec: Mapping) -> bool:
    """출처 라벨이 '자동'(생성 기록 대조·봇 계정 가명 일치)인가."""
    return _body(rec).get("method") in AUTO_METHODS


def origin_value(rec: Mapping) -> str:
    """출처 라벨 → 비교 값(real·virtual·undeterminable). 형식이 맞지 않으면 'invalid'."""
    b = _body(rec)
    if b.get("origin") not in ORIGINS or validate_origin_body(b):
        return INVALID
    return b["origin"]


# ----------------------------------------------------------------------------- 세션 안 상태 (화면용)
def heads_by_chat(records: Iterable[Mapping], *, session_id: str | None = None) -> tuple[dict[str, dict], int]:
    """현재 유효 출처 라벨(resolve 결과) → chat_id → 레코드. session_id 를 주면 다른 세션 레코드는 버린다(블라인드).

    kind 가 origin 이 아닌 레코드는 버린다. 같은 chat_id 에 대상(entity)이 둘 이상이면 그 chat_id 는 빼고 수를 돌려준다.
    """
    seen: dict[str, dict] = {}
    dup: set[str] = set()
    for r in records:
        if not isinstance(r, Mapping):
            continue
        if r.get("kind") not in (None, "origin"):
            continue
        sid = r.get("session_id")
        if session_id is not None and sid is not None and sid != session_id:
            continue
        cid = _body(r).get("chat_id")
        if not isinstance(cid, str):
            continue
        if cid in seen:
            dup.add(cid)
        else:
            seen[cid] = dict(r)
    for c in dup:
        seen.pop(c, None)
    return seen, len(dup)


@dataclass(frozen=True)
class Progress:
    done: int
    total: int
    by_origin: Mapping[str, int]
    auto: int                  # 판정 중 자동 method 인 것
    out_of_targets: int        # 대상 밖 채팅의 출처 라벨(만들 수 없어야 하지만 있으면 센다)


def progress(targets, heads: Mapping[str, Mapping]) -> Progress:
    ts = _target_set(targets) or frozenset()
    by = Counter()
    auto = 0
    done = 0
    for cid, r in heads.items():
        if cid not in ts:
            continue
        done += 1
        by[_body(r).get("origin")] += 1
        if is_auto(r):
            auto += 1
    return Progress(done, len(ts), {o: by.get(o, 0) for o in ORIGINS}, auto,
                    sum(1 for c in heads if c not in ts))


# ----------------------------------------------------------------------------- 일치도 (06 4-7)
def confusion_from_pairs(pairs: Iterable[tuple[str, str]]) -> dict:
    """(A 값, B 값) 짝들 → {"labels": [real, virtual, undeterminable], "rows": 3×3 (행 = A, 열 = B),
    "row_totals", "col_totals", "n"}. 세 값 밖의 짝은 넣지 않는다(부르는 쪽이 미리 거른다)."""
    t = Counter((a, b) for a, b in pairs if a in ORIGINS and b in ORIGINS)
    rows = [[int(t.get((x, y), 0)) for y in ORIGINS] for x in ORIGINS]
    return {"labels": list(ORIGINS), "rows": rows,
            "row_totals": [sum(r) for r in rows],
            "col_totals": [sum(rows[i][j] for i in range(3)) for j in range(3)],
            "n": sum(sum(r) for r in rows)}


def nominal_kappa(confusion: Mapping) -> dict:
    """3×3 혼동표 → 명목 Cohen κ {n, p_o, p_e, kappa}. 계산할 수 없으면(빈 표, p_e = 1) kappa = None."""
    labels = list(confusion["labels"])
    rows = confusion["rows"]
    table = {(labels[i], labels[j]): rows[i][j] for i in range(len(labels)) for j in range(len(labels))
             if rows[i][j]}
    return kappa_from_table(table, labels)


def _block(pairs: Sequence[tuple[str, str]]) -> dict:
    conf = confusion_from_pairs(pairs)
    k = nominal_kappa(conf)
    return {"confusion": conf, "n": conf["n"], "p_o": k["p_o"], "p_e": k["p_e"], "kappa": k["kappa"]}


def compare_origins(labels_a: Iterable[Mapping], labels_b: Iterable[Mapping], *, targets=None,
                    strict: bool = True) -> dict:
    """두 세션의 현재 유효 출처 라벨 → 출처 일치도 (06 4-0: 같은 chat_id 끼리 짝, 4-7).

    출처 κ 계산은 이 함수 하나다(통합에서 합침). app.core.agreement.compare_origins 는 이 함수를 불러 보고서 모양으로
    바꾸기만 한다.

    입력: 레코드 dict(봉투 + body) 또는 body dict 의 모음. 한 세션 것만 넣는다(세션 거르기는 부르는 쪽).
    targets: 대상(OriginTargets·CombinedTargets·chat_id 모음). 주면 대상 안 채팅만 비교하고 밖은 센다.
    strict: True(기본)면 출처 판정 화면의 규칙(validate_origin_body)에 맞지 않는 라벨을 형식 오류로 뺀다.
      False 면 출처 값(실제·가상·판별 불가)만 본다 — 보고서는 이미 저장된(스키마를 통과한) 라벨을 세므로 이쪽을 쓴다.

    결과 (숫자·분류·chat_id 만, 근거 없음)
      categories              ["real", "virtual", "undeterminable"]
      n_a, n_b                 각 세션의 비교 대상 라벨 수(대상 안·형식 맞음·중복 아님)
      labels_a, labels_b       각 세션의 대상 안 라벨 chat_id 수(형식 오류 포함, 중복 뺌)
      pairs                    둘 다 판정한 수 (= all.n)
      only_a, only_b           한쪽만 판정한 수
      invalid_a, invalid_b     형식이 맞지 않아 뺀 라벨 수
      invalid_pairs            두 세션 모두 라벨이 있지만 한쪽이라도 형식 오류라 짝에서 뺀 chat_id 수
      duplicate_a, duplicate_b 같은 chat_id 에 대상이 둘 이상이라 뺀 chat_id 수
      out_of_targets_a/_b      대상 밖 채팅 라벨 수 (targets 를 안 주면 0)
      targets                  대상 수 (없으면 None)
      all / human / auto       {"confusion": {labels, rows(3×3, 행 A·열 B), row_totals, col_totals, n},
                                "n", "p_o", "p_e", "kappa"}
                               human = 양쪽 모두 자동 method 아님, auto = 한쪽이라도 자동 (06 4-7 나눠 보임)
      auto_pairs               {"both": 양쪽 자동, "one_side": 한쪽만 자동}
      undeterminable           {"both", "a_only", "b_only"} — 판별 불가 일치 표
      by_origin_a, by_origin_b 세션별 출처 분포(비교 대상 라벨 기준)
      disagreements            [{"chat_id", "a", "b", "auto": bool}] chat_id 순
      notes                    [LOW_AGREEMENT_NOTE, AUTO_NOTE]
    """
    ts = _target_set(targets)

    def value_of(r) -> str:
        if strict:
            return origin_value(r)
        v = _body(r).get("origin")
        return v if v in ORIGINS else INVALID

    def side(labels):
        heads, dup = heads_by_chat(labels)
        out: dict[str, tuple[str, bool]] = {}
        seen: set[str] = set()
        invalid = outside = 0
        for cid, r in heads.items():
            if ts is not None and cid not in ts:
                outside += 1
                continue
            seen.add(cid)
            v = value_of(r)
            if v == INVALID:
                invalid += 1
                continue
            out[cid] = (v, is_auto(r))
        return out, invalid, dup, outside, seen

    a, inv_a, dup_a, out_a, seen_a = side(labels_a)
    b, inv_b, dup_b, out_b, seen_b = side(labels_b)
    common = sorted(set(a) & set(b))
    invalid_pairs = len((seen_a & seen_b) - set(common))
    all_p, human_p, auto_p = [], [], []
    both_auto = one_auto = 0
    und = {"both": 0, "a_only": 0, "b_only": 0}
    dis = []
    for c in common:
        (va, aa), (vb, ab) = a[c], b[c]
        all_p.append((va, vb))
        if aa or ab:
            auto_p.append((va, vb))
            if aa and ab:
                both_auto += 1
            else:
                one_auto += 1
        else:
            human_p.append((va, vb))
        if va == UNDETERMINABLE and vb == UNDETERMINABLE:
            und["both"] += 1
        elif va == UNDETERMINABLE:
            und["a_only"] += 1
        elif vb == UNDETERMINABLE:
            und["b_only"] += 1
        if va != vb:
            dis.append({"chat_id": c, "a": va, "b": vb, "auto": bool(aa or ab)})
    return {
        "categories": list(ORIGINS),
        "n_a": len(a), "n_b": len(b),
        "labels_a": len(seen_a), "labels_b": len(seen_b),
        "pairs": len(common),
        "only_a": len(set(a) - set(b)), "only_b": len(set(b) - set(a)),
        "invalid_a": inv_a, "invalid_b": inv_b, "invalid_pairs": invalid_pairs,
        "duplicate_a": dup_a, "duplicate_b": dup_b,
        "out_of_targets_a": out_a, "out_of_targets_b": out_b,
        "targets": len(ts) if ts is not None else None,
        "all": _block(all_p), "human": _block(human_p), "auto": _block(auto_p),
        "auto_pairs": {"both": both_auto, "one_side": one_auto},
        "undeterminable": und,
        "by_origin_a": {o: sum(1 for v, _ in a.values() if v == o) for o in ORIGINS},
        "by_origin_b": {o: sum(1 for v, _ in b.values() if v == o) for o in ORIGINS},
        "disagreements": dis,
        "notes": [LOW_AGREEMENT_NOTE, AUTO_NOTE],
    }


__all__ = [
    "REAL", "VIRTUAL", "UNDETERMINABLE", "ORIGINS", "ORIGIN_KO", "METHODS", "METHOD_KO", "AUTO_METHODS",
    "CONFIDENCES", "CONF_KO", "RATIONALE_MAX", "INVALID", "TARGETS_SCHEMA", "TARGET_ID_RE",
    "OriginError", "AutoHint", "TargetItem", "OriginTargets", "CombinedTargets",
    "chat_ids_digest", "make_target_id", "parse_targets", "build_targets", "combine_targets",
    "validate_origin_body", "check_origin_body", "origin_draft", "draft_from_auto", "is_auto", "origin_value",
    "heads_by_chat", "Progress", "progress",
    "confusion_from_pairs", "nominal_kappa", "compare_origins", "LOW_AGREEMENT_NOTE", "AUTO_NOTE",
]
