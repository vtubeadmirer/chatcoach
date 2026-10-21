"""조정(adjudication) 계산 — 잠긴 두(이상) 세션의 불일치 목록과 조정 기록 본문 (docs/design/03-data-model.md 6-12·12-1,
06 5장·7-1, 08 D-12 3·4·7).

하는 일
- 불일치 목록(Case): app.core.agreement 의 짝짓기 결과를 입력으로 받는다(이 모듈은 agreement 를 고치지 않고 공개 함수만 쓴다).
  · 표본 판정: compare_samples 결과의 disagreements(같은 chat_id 짝 가운데 값이 다른 것)를 한 건씩. 한쪽만 '판단 불가'
    인 짝은 agreement 가 따로 세기만 하고 목록에 넣지 않으므로 여기서 reaction_value 로 더한다(include_undeterminable).
  · 활동 구간: compare_segments 결과의 ranges(비교 범위) 안에서 두 세션 분류가 다른 연속 구간을 찾는다. 경계 짝
    (match_boundaries, 30초 창) 사이의 어긋남이 완충(10초) 이하이면 '경계 차이' 로 보고 목록에서 뺀다(4-1 핵심 일치율이
    빼는 칸과 같은 뜻). 완충보다 크면 '경계' 건, 짝이 없는 곳은 '분류' 건이다.
- 한 건마다 양쪽(CaseSide)의 판정·확신도·근거 한 줄을 나란히 둔다(D-12 4). 근거에는 닉네임이 들어갈 수 있으므로
  **화면에만** 쓴다. 보고서·로그에 넣지 않는다(이 모듈 결과를 파일로 쓰는 쪽이 지킨다).
- 조정 기록 본문(adjudicationBody, label-event 스키마): decision(final·exclude_from_eval·keep_as_ambiguous)·final·cause·
  pattern_tag·note·targets. 저장은 app 층이 store.append_label({"kind": "adjudication", ...}) 로 한다.
- 같은 pattern_tag 가 3건 이상이면 기준서 개정 후보(06 5장 시작값).
- 조정자 = 라벨러 표시(D-12 3: 1인 작업이면 본인이 조정자를 겸하고 숨기지 않는다).

core 층: 파일·시계·난수·Qt 를 쓰지 않는다. 입력 dict 를 고치지 않는다(필요하면 깊은 사본).
"""
from __future__ import annotations

import bisect
import copy
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from . import agreement as ag
from .segments import is_transition

# ----------------------------------------------------------------------------- 값 (03 6-12, label-event adjudicationBody)
KIND = "adjudication"
TARGET_REACTION = "reaction"
TARGET_SEGMENT = "segment"
TARGET_KINDS = (TARGET_SEGMENT, TARGET_REACTION)

D_FINAL = "final"
D_EXCLUDE = "exclude_from_eval"
D_AMBIGUOUS = "keep_as_ambiguous"
DECISIONS = (D_FINAL, D_EXCLUDE, D_AMBIGUOUS)
DECISION_KO = {D_FINAL: "최종값 확정", D_EXCLUDE: "평가에서 제외", D_AMBIGUOUS: "진짜 모호함으로 남김"}

CAUSES = ("guide_unclear", "simple_mistake", "truly_ambiguous", "other")
CAUSE_KO = {"guide_unclear": "기준 흐림", "simple_mistake": "단순 실수", "truly_ambiguous": "진짜 모호", "other": "기타"}

# 불일치 건의 갈래
C_GRADE = "grade"                  # 표본: 등급(반응 미확인 포함)이 다름
C_UNCLEAR = "unclear"              # 표본: 한쪽만 불명확
C_UNDETERMINABLE = "undeterminable"  # 표본: 한쪽만 확신도 판단 불가
C_CATEGORY = "category"            # 구간: 분류가 다름
C_BOUNDARY = "boundary"            # 구간: 짝지은 경계가 완충보다 크게 어긋남
CASE_KIND_KO = {C_GRADE: "등급", C_UNCLEAR: "불명확", C_UNDETERMINABLE: "판단 불가", C_CATEGORY: "분류", C_BOUNDARY: "경계"}

PATTERN_TAG_MAX = 60               # label-event pattern_tag maxLength
NOTE_MAX = 1000                    # label-event note maxLength
REVISION_MIN = 3                   # 같은 pattern_tag 가 이 수 이상이면 기준서 개정 후보 (06 5장 시작값)

_WS = re.compile(r"\s+")


class AdjudicationError(ValueError):
    """조정 입력이 규칙에 맞지 않음(결정·원인 값, final 모양 등). 메시지는 화면에 그대로 보인다."""


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _body(rec: Mapping | None) -> Mapping:
    if not isinstance(rec, Mapping):
        return {}
    b = rec.get("body")
    return b if isinstance(b, Mapping) else {}


# ----------------------------------------------------------------------------- 불일치 한 건
@dataclass(frozen=True)
class CaseSide:
    """불일치 한 건의 한쪽(세션 하나). value 는 비교 값(등급·분류), rationale 은 근거 한 줄(화면 전용)."""
    session_id: str
    labeler_id: str | None
    value: str | None
    confidence: str | None
    rationale: str | None
    entity_id: str | None
    version_id: str | None
    start_ms: int | None = None        # 구간 건: 그 쪽 구간의 시작·끝(표본 건은 None)
    end_ms: int | None = None
    body: dict | None = field(default=None, compare=False, repr=False)   # '이쪽으로' 고를 때 final 로 복사할 본문

    @property
    def has_label(self) -> bool:
        return self.entity_id is not None and self.version_id is not None

    def target(self) -> dict:
        return {"session_id": self.session_id, "entity_id": self.entity_id, "version_id": self.version_id}


@dataclass(frozen=True)
class Case:
    """불일치 한 건. case_id 는 같은 입력이면 같다(표본 'reaction:<chat_id>', 구간 'segment:<시작>-<끝>')."""
    case_id: str
    target_kind: str
    kind: str
    t_ms: int | None
    sides: tuple[CaseSide, ...]
    chat_id: str | None = None
    spans: tuple[tuple[int, int], ...] = ()
    delta_ms: int | None = None        # 경계 건: 두 경계의 차이

    def side(self, session_id: str) -> CaseSide | None:
        return next((s for s in self.sides if s.session_id == session_id), None)

    def targets(self) -> list[dict]:
        """adjudicationBody.targets[] (라벨이 있는 쪽만, 세션 ID 순)."""
        return [s.target() for s in sorted(self.sides, key=lambda s: s.session_id) if s.has_label]

    def target_key(self) -> tuple:
        return target_key(self.target_kind, self.targets())

    def labeler_ids(self) -> list[str]:
        return sorted({s.labeler_id for s in self.sides if isinstance(s.labeler_id, str)})


def target_key(target_kind: str, targets: Iterable[Mapping]) -> tuple:
    """조정 기록과 불일치 건을 잇는 열쇠: (종류, {(세션, 대상)}). 판(version_id)은 열쇠에 넣지 않는다(stale 로 따로 본다)."""
    pairs = frozenset((t.get("session_id"), t.get("entity_id")) for t in targets if isinstance(t, Mapping))
    return (target_kind, pairs)


def _reaction_side(sid: str, labeler: str | None, rec: Mapping | None) -> CaseSide:
    b = _body(rec)
    return CaseSide(sid, labeler, ag.reaction_value(rec) if rec is not None else None,
                    b.get("confidence") if isinstance(b.get("confidence"), str) else None,
                    b.get("rationale") if isinstance(b.get("rationale"), str) else None,
                    rec.get("entity_id") if rec is not None else None, rec.get("id") if rec is not None else None,
                    body=copy.deepcopy(dict(b)) if rec is not None else None)


def _by_chat(reactions: Iterable[Mapping]) -> dict[str, Mapping]:
    """chat_id → 반응 레코드. 같은 chat_id 가 둘 이상이면 agreement 처럼 그 chat_id 를 뺀다."""
    seen: dict[str, Mapping] = {}
    dup: set[str] = set()
    for r in reactions or ():
        cid = _body(r).get("chat_id")
        if not isinstance(cid, str) or not cid:
            continue
        if cid in seen:
            dup.add(cid)
        else:
            seen[cid] = r
    for c in dup:
        seen.pop(c, None)
    return seen


def _chat_t(cid: str, rec: Mapping | None, chat_times: Mapping | None) -> int | None:
    t = (chat_times or {}).get(cid)
    if _is_int(t):
        return t
    t = _body(rec).get("first_visible_ms")
    return t if _is_int(t) else None


def sample_cases(sample_result: Mapping, side_a: Mapping, side_b: Mapping, *,
                 chat_times: Mapping[str, int] | None = None, include_undeterminable: bool = True,
                 chat_ids: Iterable[str] | None = None, scope: Sequence | None = None) -> list[Case]:
    """표본 판정 불일치 (06 4-0 같은 chat_id 짝). sample_result = agreement.compare_samples(...) 결과.

    side: {session_id, labeler_id, reactions[]} — reactions 는 compare_samples 에 넘긴 것과 같은 현재 유효 레코드.
    include_undeterminable: 한쪽만 '판단 불가' 인 짝도 넣는다. 이때 chat_ids·scope 는 compare_samples 에 준 것과 같게 준다
    (그 짝은 agreement 결과에 목록이 없어 여기서 같은 범위로 다시 고른다).
    """
    sa, sb = side_a["session_id"], side_b["session_id"]
    la, lb = side_a.get("labeler_id"), side_b.get("labeler_id")
    ma, mb = _by_chat(side_a.get("reactions") or ()), _by_chat(side_b.get("reactions") or ())
    out: dict[str, Case] = {}
    for d in sample_result.get("disagreements") or ():
        cid = d.get("chat_id")
        if not isinstance(cid, str) or cid not in ma or cid not in mb:
            continue
        kind = C_UNCLEAR if ag.UNCLEAR in (d.get("a"), d.get("b")) else C_GRADE
        t = d.get("t_ms") if _is_int(d.get("t_ms")) else _chat_t(cid, ma[cid], chat_times)
        out[cid] = Case(f"reaction:{cid}", TARGET_REACTION, kind, t,
                        (_reaction_side(sa, la, ma[cid]), _reaction_side(sb, lb, mb[cid])), chat_id=cid)
    if include_undeterminable:
        keep = None if chat_ids is None else set(chat_ids)
        rng = None if scope is None else [(int(a), int(b)) for a, b in scope]
        for cid in sorted(set(ma) & set(mb)):
            if cid in out or (keep is not None and cid not in keep):
                continue
            va, vb = ag.reaction_value(ma[cid]), ag.reaction_value(mb[cid])
            if va == vb or ag.UNDETERMINABLE not in (va, vb) or ag.INVALID_VALUE in (va, vb):
                continue
            if rng is not None:
                ta, tb = _chat_t(cid, ma[cid], chat_times), _chat_t(cid, mb[cid], chat_times)
                if any(t is not None and not any(lo <= t < hi for lo, hi in rng) for t in (ta, tb)):
                    continue
            out[cid] = Case(f"reaction:{cid}", TARGET_REACTION, C_UNDETERMINABLE, _chat_t(cid, ma[cid], chat_times),
                            (_reaction_side(sa, la, ma[cid]), _reaction_side(sb, lb, mb[cid])), chat_id=cid)
    return sorted(out.values(), key=lambda c: (c.t_ms if c.t_ms is not None else -1, c.chat_id or ""))


class _Line:
    """한 세션의 현재 유효 구간 → 시각별 (분류, 레코드). agreement 와 같은 유효 조건([시작, 끝), 분류 5종)."""

    def __init__(self, segments: Iterable[Mapping]):
        segs = []
        for r in segments or ():
            b = _body(r)
            s, e, c = b.get("start_ms"), b.get("end_ms"), b.get("category")
            if _is_int(s) and _is_int(e) and e > s and c in ag.CATEGORIES:
                segs.append((s, e, c, r))
        segs.sort(key=lambda x: (x[0], x[1]))
        self.segs = segs
        self.starts = [s for s, _, _, _ in segs]

    def at(self, t: int):
        i = bisect.bisect_right(self.starts, t) - 1
        while i >= 0:
            s, e, c, r = self.segs[i]
            if s <= t < e:
                return c, r
            i -= 1
        return None, None

    def edges(self) -> list[int]:
        return sorted({s for s, _, _, _ in self.segs} | {e for _, e, _, _ in self.segs})

    def boundaries(self, ranges: Sequence[tuple[int, int]]) -> list[int]:
        """비교 범위 안(가장자리 제외)의 '활동이 바뀌는 경계'(agreement 와 같은 규칙)."""
        out = []
        for t in self.edges():
            if not any(lo < t < hi for lo, hi in ranges):
                continue
            left, right = self.at(t - 1)[0], self.at(t)[0]
            if left is None and right is None:
                continue
            if is_transition(left, right):
                out.append(t)
        return out


def _seg_side(sid: str, labeler: str | None, rec: Mapping) -> CaseSide:
    b = _body(rec)
    return CaseSide(sid, labeler, b.get("category"),
                    b.get("confidence") if isinstance(b.get("confidence"), str) else None,
                    b.get("rationale") if isinstance(b.get("rationale"), str) else None,
                    rec.get("entity_id"), rec.get("id"), b.get("start_ms"), b.get("end_ms"),
                    body=copy.deepcopy(dict(b)))


def segment_cases(segment_result: Mapping, side_a: Mapping, side_b: Mapping, *,
                  buffer_ms: int = ag.DEFAULT_BUFFER_MS, include_within_buffer: bool = False) -> list[Case]:
    """활동 구간 불일치 (06 4-1·4-0). segment_result = agreement.compare_segments(...) 결과(ranges·boundaries 를 쓴다).

    side: {session_id, labeler_id, segments[]}. 같은 두 구간 판이 여러 조각에서 갈리면(비교 범위가 블록으로 끊긴 경우)
    한 건으로 묶고 spans 에 조각을 모두 둔다 — 조정 기록은 대상(구간 판)에 붙으므로 한 건이어야 열쇠가 하나다.
    """
    ranges = [(int(a), int(b)) for a, b in segment_result.get("ranges") or ()]
    if not ranges:
        return []
    sa, sb = side_a["session_id"], side_b["session_id"]
    la, lb = side_a.get("labeler_id"), side_b.get("labeler_id")
    A, B = _Line(side_a.get("segments") or ()), _Line(side_b.get("segments") or ())
    tol_sec = (segment_result.get("boundaries") or {}).get("tolerance_sec")
    tol_ms = int(round(float(tol_sec) * 1000)) if isinstance(tol_sec, (int, float)) else 30_000
    pairs, _, _ = ag.match_boundaries(A.boundaries(ranges), B.boundaries(ranges), tol_ms)
    pair_spans = {(min(x, y), max(x, y)) for x, y in pairs if x != y}

    edges = sorted(set(A.edges()) | set(B.edges()))
    runs: list[list] = []                         # [시작, 끝, recA, recB]
    for lo, hi in ranges:
        cuts = [lo] + [t for t in edges if lo < t < hi] + [hi]
        for p, q in zip(cuts, cuts[1:]):
            if q <= p:
                continue
            m = p + (q - p) // 2
            ca, ra = A.at(m)
            cb, rb = B.at(m)
            if ca is None or cb is None or ca == cb:
                continue
            if runs and runs[-1][1] == p and runs[-1][2] is ra and runs[-1][3] is rb:
                runs[-1][1] = q
            else:
                runs.append([p, q, ra, rb])

    groups: dict[tuple, list] = {}
    for p, q, ra, rb in runs:
        groups.setdefault((ra.get("id"), rb.get("id")), []).append((p, q, ra, rb))
    out = []
    for parts in groups.values():
        spans = tuple((p, q) for p, q, _, _ in parts)
        ra, rb = parts[0][2], parts[0][3]
        kind, delta = C_CATEGORY, None
        if len(spans) == 1 and spans[0] in pair_spans:
            kind, delta = C_BOUNDARY, spans[0][1] - spans[0][0]
            if delta <= buffer_ms and not include_within_buffer:
                continue
        out.append(Case(f"segment:{spans[0][0]}-{spans[0][1]}", TARGET_SEGMENT, kind, spans[0][0],
                        (_seg_side(sa, la, ra), _seg_side(sb, lb, rb)), spans=spans, delta_ms=delta))
    return sorted(out, key=lambda c: (c.t_ms, c.case_id))


def build_cases(result: Mapping, side_a: Mapping, side_b: Mapping, *, chat_times: Mapping[str, int] | None = None,
                sample_chat_ids: Iterable[str] | None = None, include_undeterminable: bool = True) -> list[Case]:
    """agreement.build_agreement(side_a, side_b, ...) 결과 → 구간 불일치 + 표본 불일치 (시각 순).

    side 는 build_agreement 에 넘긴 것과 같은 dict(session_id·labeler_id·scope·segments·reactions).
    """
    sc_a, sc_b = side_a.get("scope"), side_b.get("scope")
    scope = None
    if sc_a is not None or sc_b is not None:
        scope = _intersect(sc_a, sc_b)
    buf = int(result.get("buffer_ms", ag.DEFAULT_BUFFER_MS))
    segs = segment_cases(result.get("segments") or {}, side_a, side_b, buffer_ms=buf)
    # build_agreement 는 지금 구간표로 보아 표본 자리가 아닌 판정을 일치도에서 뺀다(D-12 2). 한쪽만 판단 불가인 짝도
    # 같은 규칙으로 고르도록 같은 공개 함수로 거른다(그 함수가 없는 판이면 그대로).
    split = getattr(ag, "split_current_sample", None)
    if split is not None:
        cut = []
        for s in (side_a, side_b):
            r = split(s.get("reactions") or (), s.get("missed") or (), s.get("segments") or (), scope=s.get("scope"),
                      buffer_ms=buf, chat_times=chat_times, current_sample=s.get("current_sample"))
            cut.append({**s, "reactions": r["reactions"]})
        side_a, side_b = cut
    smps = sample_cases(result.get("samples") or {}, side_a, side_b, chat_times=chat_times,
                        include_undeterminable=include_undeterminable, chat_ids=sample_chat_ids, scope=scope)
    return sorted(segs + smps, key=lambda c: (c.t_ms if c.t_ms is not None else -1, c.target_kind, c.case_id))


def _intersect(sc_a: Sequence | None, sc_b: Sequence | None) -> list[tuple[int, int]]:
    cur = [(0, 1 << 62)]
    for sc in (sc_a, sc_b):
        if sc is None:
            continue
        cur = [(max(lo, int(a)), min(hi, int(b))) for lo, hi in cur for a, b in sc if min(hi, int(b)) > max(lo, int(a))]
    return sorted(cur)


def merge_cases(case_lists: Iterable[Sequence[Case]]) -> list[Case]:
    """세 세션 이상: 짝마다 만든 목록을 합친다. 표본 건은 chat_id 로 한 건에 모으고(세션마다 한 쪽), 구간 건은 그대로 둔다."""
    by_chat: dict[str, Case] = {}
    rest: dict[str, Case] = {}
    for cases in case_lists:
        for c in cases:
            if c.target_kind != TARGET_REACTION or c.chat_id is None:
                key = c.case_id + "|" + "|".join(sorted(s.session_id for s in c.sides))
                rest.setdefault(key, c)
                continue
            prev = by_chat.get(c.chat_id)
            if prev is None:
                by_chat[c.chat_id] = c
                continue
            sides = {s.session_id: s for s in prev.sides}
            for s in c.sides:
                sides.setdefault(s.session_id, s)
            kinds = {prev.kind, c.kind}
            kind = C_GRADE if C_GRADE in kinds else C_UNCLEAR if C_UNCLEAR in kinds else C_UNDETERMINABLE
            by_chat[c.chat_id] = Case(prev.case_id, prev.target_kind, kind, prev.t_ms,
                                      tuple(sides[k] for k in sorted(sides)), chat_id=prev.chat_id)
    out = list(by_chat.values()) + list(rest.values())
    return sorted(out, key=lambda c: (c.t_ms if c.t_ms is not None else -1, c.target_kind, c.case_id))


# ----------------------------------------------------------------------------- 조정 기록 본문
def normalize_pattern_tag(tag) -> str | None:
    """앞뒤 공백을 지우고 안쪽 공백을 한 칸으로. 빈 값이면 None. 60자 넘으면 AdjudicationError."""
    if tag is None:
        return None
    if not isinstance(tag, str):
        raise AdjudicationError("pattern_tag 는 문자열이어야 합니다")
    t = _WS.sub(" ", tag).strip()
    if not t:
        return None
    if len(t) > PATTERN_TAG_MAX:
        raise AdjudicationError(f"pattern_tag 는 {PATTERN_TAG_MAX}자까지입니다")
    return t


def _tag_group(tag: str) -> str:
    return tag.casefold()


def segment_final(case: Case, base_session_id: str, *, category: str | None = None,
                  start_ms: int | None = None, end_ms: int | None = None) -> dict:
    """구간 건의 '새 값': 한쪽 구간 본문을 바탕으로 분류·시작·끝을 바꾼 final. 근거·확신도는 바탕 쪽 것을 둔다
    (조정 사유는 note 에 적는다). 표본(반응) 건의 새 값은 이번 판에서 만들지 않는다 — 반응 본문은 증거·연결 발화까지
    필요해서, 조정에서는 한쪽을 고르거나 '모호함으로 남김' 을 쓴다."""
    if case.target_kind != TARGET_SEGMENT:
        raise AdjudicationError("새 값은 활동 구간 건에서만 만듭니다. 표본 건은 한쪽을 고르거나 모호함으로 남깁니다")
    side = case.side(base_session_id)
    if side is None or side.body is None:
        raise AdjudicationError("바탕으로 쓸 쪽의 구간 본문이 없습니다")
    final = copy.deepcopy(side.body)
    if category is not None:
        if category not in ag.CATEGORIES:
            raise AdjudicationError(f"분류 값이 아닙니다: {category!r}")
        final["category"] = category
        if final.get("active_chat") is True and category != "game":
            final.pop("active_chat", None)        # active_chat 은 game 일 때만(label-event segmentBody)
    for k, v in (("start_ms", start_ms), ("end_ms", end_ms)):
        if v is not None:
            if not _is_int(v) or v < 0:
                raise AdjudicationError(f"{k} 는 0 이상의 정수(ms)여야 합니다")
            final[k] = v
    _check_final(case, final)
    return final


def _check_final(case: Case, final) -> None:
    if not isinstance(final, Mapping):
        raise AdjudicationError("final 은 object 여야 합니다")
    if case.target_kind == TARGET_SEGMENT:
        s, e, c = final.get("start_ms"), final.get("end_ms"), final.get("category")
        if not (_is_int(s) and _is_int(e) and e > s):
            raise AdjudicationError("최종 구간의 시작·끝이 올바르지 않습니다 (끝 > 시작)")
        if c not in ag.CATEGORIES:
            raise AdjudicationError("최종 구간의 분류가 올바르지 않습니다")
    else:
        if final.get("chat_id") != case.chat_id:
            raise AdjudicationError("최종 판정의 chat_id 가 이 건의 채팅과 다릅니다")


def make_body(case: Case, *, decision: str, cause: str, pick: str | None = None, final: Mapping | None = None,
              pattern_tag: str | None = None, note: str | None = None) -> dict:
    """adjudicationBody (label-event 스키마 모양).

    - decision = final: pick(고른 쪽 session_id — 그 쪽 본문을 그대로 final 로) 또는 final(새 값, segment_final) 하나.
    - decision = exclude_from_eval·keep_as_ambiguous: pick·final 을 주지 않는다(final 칸을 쓰지 않는다).
    - cause 필수(기준 흐림·단순 실수·진짜 모호·기타). pattern_tag·note 는 선택.
    """
    if decision not in DECISIONS:
        raise AdjudicationError(f"decision 값이 아닙니다: {decision!r}")
    if cause not in CAUSES:
        raise AdjudicationError("불일치 원인(cause)을 고르세요")
    targets = case.targets()
    if not targets:
        raise AdjudicationError("조정할 라벨이 없습니다")
    body: dict = {"target_kind": case.target_kind, "targets": targets, "decision": decision}
    if decision == D_FINAL:
        if (pick is None) == (final is None):
            raise AdjudicationError("최종값은 한쪽 고르기(pick) 또는 새 값(final) 하나로 정합니다")
        if pick is not None:
            side = case.side(pick)
            if side is None or side.body is None:
                raise AdjudicationError("고른 쪽의 판정이 없습니다")
            fv = copy.deepcopy(side.body)
        else:
            fv = copy.deepcopy(dict(final))
        _check_final(case, fv)
        body["final"] = fv
    elif pick is not None or final is not None:
        raise AdjudicationError(f"{DECISION_KO[decision]} 에는 최종값을 주지 않습니다")
    body["cause"] = cause
    body["pattern_tag"] = normalize_pattern_tag(pattern_tag)
    if note is not None:
        if not isinstance(note, str):
            raise AdjudicationError("메모는 문자열이어야 합니다")
        n = note.strip()
        if len(n) > NOTE_MAX:
            raise AdjudicationError(f"메모는 {NOTE_MAX}자까지입니다")
        if n:
            body["note"] = n
    return body


def make_draft(case: Case, body: Mapping, existing: Mapping | None = None) -> dict:
    """store.append_label 에 넘길 초안. existing(이 건의 현재 조정 기록)이 있으면 그 판을 고치는 새 판."""
    d = {"kind": KIND, "body": copy.deepcopy(dict(body))}
    if existing is not None:
        d["entity_id"] = existing["entity_id"]
        d["supersedes"] = existing["id"]
    return d


# ----------------------------------------------------------------------------- 상태·집계
@dataclass(frozen=True)
class CaseState:
    """한 건의 조정 상태. record = 지금 유효한 조정 기록(없으면 None). stale = 기록이 가리킨 판이 지금 판과 다름,
    또는 기록의 대상 묶음이 지금 건과 다름(targets_changed — 세 번째 세션이 생겨 표본 건에 한쪽이 늘었거나 줄었을 때).
    다시 저장하면 이 record 를 supersedes 해서, 같은 채팅에 유효한 조정 기록이 둘 남지 않는다."""
    case_id: str
    record: dict | None = field(default=None, compare=False)
    stale: bool = False
    targets_changed: bool = False

    @property
    def decided(self) -> bool:
        return self.record is not None


def case_states(cases: Sequence[Case], adjudications: Iterable[Mapping]) -> dict[str, CaseState]:
    """불일치 건 → 조정 상태. adjudications = 조정 세션의 현재 유효 adjudication 레코드(view.items('adjudication', sid)).

    열쇠는 (target_kind, {(세션, 대상)}). 같은 열쇠의 기록이 여럿이면 seq 가 큰(나중) 것을 쓴다.
    표본(반응) 건은 열쇠가 꼭 같지 않아도, 기록의 대상 묶음이 건의 대상 묶음의 부분집합·상위집합이면 그 기록으로 본다
    (세 세션 이상에서 merge_cases 가 같은 채팅 건에 세션을 더하거나, 세션 체크를 풀어 줄였을 때). 이때 targets_changed·
    stale 이 켜진다. 구간 건은 짝마다 따로 두므로(merge_cases) 꼭 같은 열쇠만 쓴다.
    """
    by_key: dict[tuple, Mapping] = {}
    for r in adjudications or ():
        b = _body(r)
        if r.get("kind", KIND) != KIND or b.get("target_kind") not in TARGET_KINDS:
            continue
        k = target_key(b["target_kind"], b.get("targets") or ())
        prev = by_key.get(k)
        if prev is None or (r.get("seq") or 0) >= (prev.get("seq") or 0):
            by_key[k] = r
    out = {}
    for c in cases:
        key = c.target_key()
        r = by_key.get(key)
        changed = False
        if r is None and c.target_kind == TARGET_REACTION and key[1]:
            near = [rec for (kind, pairs), rec in by_key.items()
                    if kind == TARGET_REACTION and pairs and (pairs < key[1] or pairs > key[1])]
            if near:
                r = max(near, key=lambda rec: rec.get("seq") or 0)
                changed = True
        stale = changed
        if r is not None and not stale:
            vers = {(t.get("session_id"), t.get("entity_id")): t.get("version_id") for t in _body(r).get("targets") or ()}
            stale = any(vers.get((s.session_id, s.entity_id)) != s.version_id for s in c.sides if s.has_label)
        out[c.case_id] = CaseState(c.case_id, copy.deepcopy(dict(r)) if r is not None else None, stale, changed)
    return out


def summary(cases: Sequence[Case], states: Mapping[str, CaseState]) -> dict:
    """숫자만: 건수·조정함·남음·결정별·원인별·갈래별."""
    by_dec: Counter = Counter()
    by_cause: Counter = Counter()
    by_kind: Counter = Counter(c.kind for c in cases)
    decided = stale = 0
    for c in cases:
        st = states.get(c.case_id)
        if st is None or st.record is None:
            continue
        decided += 1
        stale += int(st.stale)
        b = _body(st.record)
        by_dec[b.get("decision")] += 1
        by_cause[b.get("cause")] += 1
    return {"total": len(cases), "decided": decided, "pending": len(cases) - decided, "stale": stale,
            "by_decision": dict(sorted(by_dec.items())), "by_cause": dict(sorted(by_cause.items())),
            "by_kind": dict(sorted(by_kind.items()))}


def revision_candidates(adjudications: Iterable[Mapping], min_count: int = REVISION_MIN) -> list[dict]:
    """같은 pattern_tag(대소문자·공백 무시)가 min_count 건 이상이면 기준서 개정 후보 (06 5장).

    결과: [{pattern_tag(처음 쓴 표기), count, by_target_kind, by_cause}] 건수 많은 순, 같으면 태그 순. 숫자·태그만 담는다.
    """
    groups: dict[str, dict] = {}
    for r in adjudications or ():
        b = _body(r)
        try:
            tag = normalize_pattern_tag(b.get("pattern_tag"))
        except AdjudicationError:
            continue
        if tag is None:
            continue
        g = groups.setdefault(_tag_group(tag), {"pattern_tag": tag, "count": 0, "by_target_kind": Counter(),
                                                "by_cause": Counter()})
        g["count"] += 1
        g["by_target_kind"][b.get("target_kind")] += 1
        g["by_cause"][b.get("cause")] += 1
    out = []
    for g in groups.values():
        if g["count"] >= min_count:
            out.append({"pattern_tag": g["pattern_tag"], "count": g["count"],
                        "by_target_kind": dict(sorted(g["by_target_kind"].items())),
                        "by_cause": dict(sorted(g["by_cause"].items()))})
    return sorted(out, key=lambda g: (-g["count"], _tag_group(g["pattern_tag"])))


def known_tags(adjudications: Iterable[Mapping]) -> list[str]:
    """이미 쓴 pattern_tag 목록(입력 도움용, 처음 쓴 표기, 태그 순)."""
    seen: dict[str, str] = {}
    for r in adjudications or ():
        try:
            t = normalize_pattern_tag(_body(r).get("pattern_tag"))
        except AdjudicationError:
            continue
        if t is not None:
            seen.setdefault(_tag_group(t), t)
    return [seen[k] for k in sorted(seen)]


def adjudicator_is_labeler(adjudicator_id: str | None, labeler_ids: Iterable[str | None]) -> bool:
    """조정자가 비교한 세션의 라벨러 가운데 하나인가 (D-12 3: 1인 작업이면 본인이 겸하고 표시한다)."""
    return isinstance(adjudicator_id, str) and adjudicator_id in {x for x in labeler_ids if isinstance(x, str)}


def header(adjudicator_id: str | None, labeler_ids: Iterable[str | None]) -> dict:
    """조정 화면·보고서 머리: {adjudicator, labelers, adjudicator_is_labeler, banner(문장 또는 None)}."""
    labs = sorted({x for x in labeler_ids if isinstance(x, str)})
    same = adjudicator_is_labeler(adjudicator_id, labs)
    banner = (f"조정자 = 라벨러 ({adjudicator_id}). 1인 작업이라 본인이 조정을 겸한다 (08 D-12 3)." if same else None)
    return {"adjudicator": adjudicator_id, "labelers": labs, "adjudicator_is_labeler": same, "banner": banner}


__all__ = [
    "KIND", "TARGET_REACTION", "TARGET_SEGMENT", "TARGET_KINDS", "D_FINAL", "D_EXCLUDE", "D_AMBIGUOUS", "DECISIONS",
    "DECISION_KO", "CAUSES", "CAUSE_KO", "C_GRADE", "C_UNCLEAR", "C_UNDETERMINABLE", "C_CATEGORY", "C_BOUNDARY",
    "CASE_KIND_KO", "PATTERN_TAG_MAX", "NOTE_MAX", "REVISION_MIN", "AdjudicationError", "CaseSide", "Case",
    "target_key", "sample_cases", "segment_cases", "build_cases", "merge_cases", "normalize_pattern_tag",
    "segment_final", "make_body", "make_draft", "CaseState", "case_states", "summary", "revision_candidates",
    "known_tags", "adjudicator_is_labeler", "header",
]
