"""놓친 채팅 후보 (4단계) — 대상 고르기와 초안 검사 (Qt·파일·시계·난수 없음).

설계: docs/design/06-evaluation-and-agreement.md 2-9(대상)·4-6(일치도), 03-data-model.md 5-5(사유 코드)·6-10(필드)·12-3
(단계 순서), 안내서 v0.1 6장(네 조건·사유 코드), 08 D-10 3(영상 밖은 막음)·D-12 5(재라벨 블록 안만).

대상 (06 2-9)
  3단계 표본(계열마다 가장 큰 sample_rev 의 items) 가운데, 이 세션의 현재 유효 반응 라벨이
  '반응 미확인(result = unconfirmed)' · '관련성만 있음' · '불명확' 인 채팅. 판정이 아직 없거나 직접·높음이면 대상이 아니다.
  표본 밖에서 라벨러가 따로 찾은 후보(scope = extra)는 비율 계산에서 빠지므로 여기서 고르지 않는다(초안 검사는 받는다).
  - 범위 (D-12 5): scope_ranges 를 주면 그 안(반열린 [a, b))의 채팅만. 재라벨 세션(relabel_same·relabel_second)은
    계획의 블록 범위를 꼭 넘긴다(scope_ranges_of 가 session.open.scope 에서 꺼낸다). 1차 세션은 None(전체).
  - 영상 밖 (D-10 3): 7-2 확인 범위 [t − pre_roll, t + post_roll] 가 coverage 한 범위 안에 다 들지 않으면 대상에서 빼고
    out_of_media 로 센다. coverage None = 검사 안 함, () = 영상 없음(모두 영상 밖). 초안 검사도 같은 규칙으로 막는다.
  - 단계 순서 (03 12-3): samples_done(③ 완료 표시가 있는 구간 entity_id 모음)을 주면, 표본의 원 구간이 그 안에 없을 때 뺀다.

조건 (안내서 6장)
  ① 구간·완충 — 앱이 계산하고 라벨러가 바꿀 수 없다: 지금 구간표의 저스트채팅 구간 안 + 경계 완충 밖.
     표본은 뽑을 때 ① 을 지켰지만 그 뒤 구간을 고쳤을 수 있어 다시 센다. 실패하면 사유 A 를 자동 제안한다.
  ② 읽힐 기회 — 라벨러가 판단한다. 보조값(직전 10초 채팅 수, 노출 시간 추정)은 앱이 계산해 보인다.
     기준서 max_chat_rate_per_10s·visible_lines 가 정해졌을 때만 사유 B 를 자동 제안한다(v0.1 은 둘 다 미정 → 제안 없음).
  ③ 대화 가치, ④ 앞뒤 prior_handling_window(시작값 120초) 안에 이미 다루지 않음 — 라벨러가 판단한다.
  후보 = 네 조건이 모두 참. 아니면 사유 코드가 반드시 있다. kind ≠ text 면 사유 F 가 자동(라벨러가 바꿀 수 없음).

사유 코드의 앞뒤 맞춤 (이 모듈의 규칙, 03 5-5 를 좁힘)
  A 는 ① 이 실패했을 때만, F 는 kind ≠ text 일 때만 고를 수 있다. B·C·D·E 는 그 사유가 가리키는 조건을 '예' 로 표시했으면
  고를 수 없다(B ↔ ②, C·E ↔ ③, D ↔ ④). G(판단 불가)는 늘 고를 수 있다. 사람이 고른 코드(B·C·D·E·G)만 사유 코드 κ 에
  들어간다(06 4-6) — reason_code_source 가 auto 인 F 와 auto_suggested 를 그대로 받은 A 는 일치도에서 뺀다.

블라인드: 이 모듈은 받은 자기 세션 레코드만 다룬다. 다른 세션 라벨·엔진·AI 결과를 받는 인자가 없다.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from app.core import sampling
from app.core.ids import CHAT_ID_RE
from app.core.timecode import parse_timestamps

KIND = "missed_candidate"
SCOPE_SAMPLE = "sample"
SCOPE_EXTRA = "extra"
SCOPES = (SCOPE_SAMPLE, SCOPE_EXTRA)
JUST_CHATTING = "just_chatting"
TEXT_KIND = "text"

# 대상이 되는 반응 상태 (06 2-9)
STATE_UNCONFIRMED = "unconfirmed"
STATE_RELATED_ONLY = "related_only"
STATE_UNCLEAR = "unclear"
TARGET_STATES = (STATE_UNCONFIRMED, STATE_RELATED_ONLY, STATE_UNCLEAR)
STATE_NAMES = {STATE_UNCONFIRMED: "반응 미확인", STATE_RELATED_ONLY: "관련성만 있음", STATE_UNCLEAR: "불명확"}
# 표본 밖에서 라벨러가 따로 고른 후보(scope = extra, 14 4-2 의 11). 대상 고르기(select_targets)에는 들지 않는다.
STATE_EXTRA = "extra"
STATE_NAMES[STATE_EXTRA] = "표본 밖"

# 사유 코드 (안내서 6장, 03 5-5)
REASON_CODES = ("A", "B", "C", "D", "E", "F", "G")
REASON_NAMES = {
    "A": "구간 밖 (경계 완충 포함)",
    "B": "기회 부족 (너무 짧게 보였거나 너무 빨랐음)",
    "C": "대화 가치 낮음",
    "D": "이미 다룸",
    "E": "반복·도배 채팅",
    "F": "후원·시스템 메시지",
    "G": "판단 불가",
}
MANUAL_REASON_CODES = ("B", "C", "D", "E", "G")     # 사람이 고르는 코드 (06 4-6 사유 코드 κ 대상)
AUTO_REASON_CODES = ("A", "F")
REASON_COND = {"B": "cond2", "C": "cond3", "E": "cond3", "D": "cond4"}   # 그 사유가 가리키는 조건
SOURCE_AUTO = "auto"
SOURCE_SUGGESTED = "auto_suggested"
SOURCE_MANUAL = "manual"
CONFIDENCES = ("certain", "probably", "undeterminable")
RATIONALE_MAX = 300
RATE_WINDOW_MS = 10_000                              # '직전 10초 채팅 수' (스키마 chat_rate_10s)

# 기준서 시작값 (guide-config missed, v0.1)
DEFAULT_MIN_VISIBLE_MS = 10_000
DEFAULT_PRIOR_WINDOW_MS = 120_000

# 대상에서 뺀 까닭 (Selection.counts 의 키)
SKIP_NOT_JUDGED = "not_judged"            # 표본인데 아직 반응 판정이 없음
SKIP_REACTED = "reacted"                  # 직접·높음 (4단계 대상 아님)
SKIP_OUT_OF_SCOPE = "out_of_scope"        # 세션 범위(재라벨 블록) 밖 — D-12 5
SKIP_OUT_OF_MEDIA = "out_of_media"        # 7-2 확인 범위가 영상 밖 — D-10 3
SKIP_SAMPLES_NOT_DONE = "samples_not_done"  # 원 구간에 ③ 완료 표시가 없음 — 03 12-3
SKIP_NO_MESSAGE = "no_message"            # 채팅 원문에 없는 chat_id(시각 모름)
SKIP_KEYS = (SKIP_NOT_JUDGED, SKIP_REACTED, SKIP_OUT_OF_SCOPE, SKIP_OUT_OF_MEDIA, SKIP_SAMPLES_NOT_DONE,
             SKIP_NO_MESSAGE)

RELABEL_KINDS = ("relabel_same", "relabel_second")


class MissedError(ValueError):
    """입력이 규칙에 맞지 않는다(범위·시작값 모양)."""


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _body(rec: Mapping) -> Mapping:
    b = rec.get("body") if isinstance(rec, Mapping) else None
    return b if isinstance(b, Mapping) else (rec if isinstance(rec, Mapping) else {})


# ---------------------------------------------------------------------------- 시작값
@dataclass(frozen=True)
class MissedThresholds:
    min_visible_ms: int = DEFAULT_MIN_VISIBLE_MS
    max_chat_rate_per_10s: int | None = None
    visible_lines: int | None = None
    prior_window_ms: int = DEFAULT_PRIOR_WINDOW_MS


def thresholds_from_guide(values: Mapping | None) -> MissedThresholds:
    """guide-config 의 values(또는 그 안의 missed 칸) → 시작값. 없거나 모양이 틀린 칸은 v0.1 기본값."""
    if not isinstance(values, Mapping):
        return MissedThresholds()
    m = values.get("missed") if isinstance(values.get("missed"), Mapping) else values

    def sec(k, d):
        v = m.get(k)
        return int(round(float(v) * 1000)) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else d

    def pos_int(k):
        v = m.get(k)
        return v if _is_int(v) and v >= 1 else None

    return MissedThresholds(sec("min_visible_sec", DEFAULT_MIN_VISIBLE_MS), pos_int("max_chat_rate_per_10s"),
                            pos_int("visible_lines"), sec("prior_handling_window_sec", DEFAULT_PRIOR_WINDOW_MS))


# ---------------------------------------------------------------------------- 범위
def normalize_ranges(ranges: Iterable | None) -> tuple[tuple[int, int], ...] | None:
    """[(a, b)] 또는 [[a, b]] → 정렬·합친 튜플. None 은 None(범위 제한 없음). 모양이 틀리면 MissedError."""
    if ranges is None:
        return None
    spans = []
    for r in ranges:
        try:
            a, b = r
        except (TypeError, ValueError):
            raise MissedError("범위 항목은 [start_ms, end_ms] 쌍이어야 합니다") from None
        if not (_is_int(a) and _is_int(b)) or a >= b:
            raise MissedError("범위는 start_ms < end_ms 정수 ms 여야 합니다")
        spans.append((a, b))
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return tuple(out)


def scope_ranges_of(scope: Mapping | None, session_kind: str | None = None) -> tuple[tuple[int, int], ...] | None:
    """session.open.scope → 4단계 범위. {"type": "blocks", "ranges"} 면 그 범위, whole·없음이면 None(전체).

    재라벨 세션(D-12 5: 재라벨 블록 안만)인데 블록 범위가 없으면 빈 튜플(아무 채팅도 대상이 아님)을 돌려준다.
    """
    ranges = None
    if isinstance(scope, Mapping) and scope.get("type") == "blocks":
        ranges = normalize_ranges(scope.get("ranges") or [])
    if session_kind in RELABEL_KINDS and ranges is None:
        return ()
    return ranges


def in_ranges(t_ms: int, ranges: Sequence[tuple[int, int]] | None) -> bool:
    """t 가 반열린 [a, b) 목록 안인가. ranges None(제한 없음)이면 True."""
    if ranges is None:
        return True
    return any(a <= t_ms < b for a, b in ranges)


def in_media(t_ms: int, coverage, pre_roll_ms: int = sampling.DEFAULT_PRE_ROLL_MS,
             post_roll_ms: int = sampling.DEFAULT_POST_ROLL_MS) -> bool:
    """D-10 3 과 같은 규칙(표본·구간 폼·반응 편집기와 같은 coverage 인자). coverage None 이면 늘 True."""
    return sampling.window_in_coverage(t_ms, sampling.normalize_coverage(coverage), pre_roll_ms, post_roll_ms)


def prior_window(t_ms: int, thr: MissedThresholds = MissedThresholds()) -> tuple[int, int]:
    """조건 ④ 를 볼 범위 [t − 창, t + 창] (안내서 '앞뒤 2분')."""
    return max(0, t_ms - thr.prior_window_ms), t_ms + thr.prior_window_ms


# ---------------------------------------------------------------------------- 반응 상태
def target_state(reaction_body: Mapping | None) -> str | None:
    """반응 body → 4단계 대상이면 상태(unconfirmed·related_only·unclear), 아니면 None."""
    if not isinstance(reaction_body, Mapping):
        return None
    r = reaction_body.get("result")
    if r == "unconfirmed":
        return STATE_UNCONFIRMED
    if r == "graded" and reaction_body.get("grade") in (STATE_RELATED_ONLY, STATE_UNCLEAR):
        return reaction_body["grade"]
    return None


# ---------------------------------------------------------------------------- 조건 ① · ② 보조값
@dataclass(frozen=True)
class Cond1:
    value: bool
    in_segment: bool
    in_buffer: bool
    segment_id: str | None

    def as_body(self) -> dict:
        return {"value": self.value, "in_segment": self.in_segment, "in_buffer": self.in_buffer}


def cond1_for(t_ms: int, segments: Iterable[Mapping], buffers: Iterable = ()) -> Cond1:
    """① 저스트채팅 구간 안 + 경계 완충 밖. segments = 이 세션의 현재 유효 구간 레코드(entity_id + body).
    buffers = core.segments.record_buffers(…) 결과(완충 [a, b) 목록)."""
    seg_id = None
    for s in segments or ():
        b = _body(s)
        a, z = b.get("start_ms"), b.get("end_ms")
        if _is_int(a) and _is_int(z) and a <= t_ms < z and b.get("category") == JUST_CHATTING:
            eid = s.get("entity_id") if isinstance(s, Mapping) else None
            seg_id = eid if isinstance(eid, str) else None
            break
    in_seg = seg_id is not None
    in_buf = False
    for r in buffers or ():
        a, z = (r.start_ms, r.end_ms) if hasattr(r, "start_ms") else r
        if a <= t_ms < z:
            in_buf = True
            break
    return Cond1(in_seg and not in_buf, in_seg, in_buf, seg_id)


@dataclass(frozen=True)
class Cond2Aux:
    chat_rate_10s: int
    exposure_est_ms: int | None
    visible_lines: int | None

    def as_body(self, value: bool | None) -> dict:
        return {"value": value, "exposure_est_ms": self.exposure_est_ms, "chat_rate_10s": self.chat_rate_10s,
                "visible_lines": self.visible_lines}


def chat_times(messages: Iterable[Mapping]) -> list[tuple[int, str]]:
    """채팅창에 올라온 순서 [(t_ms, chat_id)] (모든 종류 — 후원·구독도 창을 밀어 올린다). 시각 없는 채팅은 뺀다."""
    out = [(m["t_ms"], m["chat_id"]) for m in messages or ()
           if isinstance(m, Mapping) and _is_int(m.get("t_ms")) and isinstance(m.get("chat_id"), str)]
    out.sort()
    return out


def cond2_aux(chat_id: str, t_ms: int, ordered: Sequence[tuple[int, str]], thr: MissedThresholds) -> Cond2Aux:
    """② 보조값. ordered = chat_times(messages).
    chat_rate_10s = [t − 10초, t) 에 올라온 다른 채팅 수.
    exposure_est_ms = 이 채팅 뒤로 visible_lines 개가 더 올라오기까지의 시간(그만큼 올라오면 창 밖으로 밀린다고 본다).
    visible_lines 가 미정(null)이거나 뒤에 채팅이 그만큼 없으면 None."""
    lo = bisect.bisect_left(ordered, (t_ms - RATE_WINDOW_MS, ""))
    hi = bisect.bisect_left(ordered, (t_ms, ""))
    rate = sum(1 for _t, c in ordered[lo:hi] if c != chat_id)
    exp = None
    n = thr.visible_lines
    if n is not None:
        i = bisect.bisect_left(ordered, (t_ms, chat_id))
        if i >= len(ordered) or ordered[i] != (t_ms, chat_id):
            i -= 1                      # 원문에 없는 채팅: 그 시각 바로 앞 줄을 기준으로 센다
        j = i + n
        if 0 <= i and j < len(ordered):
            exp = max(0, ordered[j][0] - t_ms)
    return Cond2Aux(rate, exp, n)


def suggest_reason(cond1: Cond1, chat_kind: str | None, aux: Cond2Aux | None,
                   thr: MissedThresholds) -> tuple[str | None, str | None]:
    """앱이 정하거나 제안하는 사유 (code, source). F(자동) > A(① 실패, 제안) > B(시작값이 정해졌을 때만, 제안)."""
    if chat_kind != TEXT_KIND:
        return "F", SOURCE_AUTO
    if not cond1.value:
        return "A", SOURCE_SUGGESTED
    if aux is not None:
        fast = thr.max_chat_rate_per_10s is not None and aux.chat_rate_10s > thr.max_chat_rate_per_10s
        short = aux.exposure_est_ms is not None and aux.exposure_est_ms < thr.min_visible_ms
        if fast or short:
            return "B", SOURCE_SUGGESTED
    return None, None


# ---------------------------------------------------------------------------- 대상 고르기 (06 2-9)
@dataclass(frozen=True)
class Target:
    chat_id: str
    t_ms: int
    chat_kind: str | None
    sample_ref: dict | None             # {"sample_set_id", "order"}. None = 표본 밖 후보(scope = extra)
    source_segment_id: str | None       # 표본을 뽑은 원 구간 entity_id
    reaction_entity_id: str | None
    state: str                          # unconfirmed · related_only · unclear
    cond1: Cond1
    aux: Cond2Aux
    suggested_reason: str | None
    suggested_source: str | None
    existing: Mapping | None = None     # 이 채팅의 현재 유효 missed_candidate 레코드(고치기)

    @property
    def segment_id(self) -> str | None:
        return self.cond1.segment_id

    @property
    def done(self) -> bool:
        return self.existing is not None

    @property
    def scope(self) -> str:
        return SCOPE_EXTRA if self.sample_ref is None else SCOPE_SAMPLE


def extra_target(chat_id: str, t_ms: int, chat_kind: str | None, segments: Iterable[Mapping], *,
                 buffers: Iterable = (), ordered: Sequence[tuple[int, str]] = (),
                 thresholds: MissedThresholds = MissedThresholds(), existing: Mapping | None = None) -> Target:
    """표본 밖에서 라벨러가 고른 채팅 하나를 4단계 대상 모양으로 (scope = extra, 14 4-2 의 11).

    ① 은 표본 대상과 같이 지금 구간표로 계산하고(바꿀 수 없음), ② 보조값은 ordered(chat_times 모양, 그 채팅 앞뒤만 있어도
    됨)로 센다. sample_ref·원 구간·반응 entity_id 는 없다. 범위(블록)·영상 검사는 저장할 때 validate_draft 가 한다.
    비율 계산(놓친 후보 일치도)에는 들지 않는다 — core.agreement 가 extra 를 따로 센다.
    """
    if not isinstance(chat_id, str) or not CHAT_ID_RE.match(chat_id):
        raise MissedError("chat_id 형식이 아닙니다")
    if not _is_int(t_ms):
        raise MissedError("대상 채팅의 시각을 모릅니다")
    c1 = cond1_for(t_ms, segments, buffers)
    aux = cond2_aux(chat_id, t_ms, ordered, thresholds)
    code, source = suggest_reason(c1, chat_kind, aux, thresholds)
    return Target(chat_id, int(t_ms), chat_kind, None, None, None, STATE_EXTRA, c1, aux, code, source, existing)


@dataclass(frozen=True)
class Selection:
    targets: tuple[Target, ...]
    counts: Mapping[str, int] = field(default_factory=dict)
    sample_total: int = 0               # 범위와 상관없이 본 표본 채팅 수(겹침 제거)

    @property
    def done(self) -> int:
        return sum(1 for t in self.targets if t.done)

    def by_chat(self) -> dict[str, Target]:
        return {t.chat_id: t for t in self.targets}


def current_by_chat(missed_records: Iterable[Mapping]) -> dict[str, Mapping]:
    """현재 유효 missed_candidate 레코드 → chat_id 별 (같은 채팅이 둘이면 seq 가 큰 쪽)."""
    out: dict[str, Mapping] = {}
    for r in missed_records or ():
        if not isinstance(r, Mapping) or r.get("kind", KIND) != KIND:
            continue
        cid = _body(r).get("chat_id")
        if not isinstance(cid, str):
            continue
        cur = out.get(cid)
        if cur is None or (r.get("seq") or 0) >= (cur.get("seq") or 0):
            out[cid] = r
    return out


def select_targets(manifests: Iterable[Mapping], reactions: Iterable[Mapping], messages: Iterable[Mapping],
                   segments: Iterable[Mapping], *, buffers: Iterable = (), scope_ranges=None, coverage=None,
                   pre_roll_ms: int = sampling.DEFAULT_PRE_ROLL_MS, post_roll_ms: int = sampling.DEFAULT_POST_ROLL_MS,
                   samples_done: Iterable[str] | None = None, missed_records: Iterable[Mapping] = (),
                   thresholds: MissedThresholds = MissedThresholds()) -> Selection:
    """4단계 대상(06 2-9). 모든 인자는 이 세션의 것.

    manifests: 표본 매니페스트(재라벨 세션은 작업대가 메모리에서 만든 것). 계열마다 가장 큰 sample_rev 만 쓴다.
    reactions·segments·missed_records: 현재 유효 레코드(entity_id·id·body). buffers: 지금 구간표의 경계 완충.
    scope_ranges: scope_ranges_of 결과(None = 전체). coverage: 영상이 덮는 VOD 범위(None = 검사 안 함).
    결과 targets 는 시각 순(같으면 chat_id 순). 같은 채팅이 여러 계열에 있으면 처음 것 하나만 둔다.
    """
    rngs = normalize_ranges(scope_ranges) if scope_ranges is not None else None
    cov = sampling.normalize_coverage(coverage)
    done_set = None if samples_done is None else set(samples_done)
    reacts = [r for r in reactions or () if isinstance(r, Mapping)]
    segs = list(segments or ())
    bufs = list(buffers or ())
    msgs = {m["chat_id"]: m for m in messages or () if isinstance(m, Mapping) and isinstance(m.get("chat_id"), str)}
    ordered = chat_times(msgs.values())
    existing = current_by_chat(missed_records)
    counts = {k: 0 for k in SKIP_KEYS}
    seen: set[str] = set()
    out: list[Target] = []
    latest = sampling.latest_of([m for m in manifests or () if isinstance(m, Mapping)])
    for _ser, man in sorted(latest.items()):
        heads = sampling.item_heads(man, reacts)
        src = (man.get("source_segment") or {}).get("entity_id")
        for it in sorted(man.get("items") or [], key=lambda x: x.get("order") or 0):
            cid = it.get("chat_id")
            if not isinstance(cid, str) or not CHAT_ID_RE.match(cid) or cid in seen:
                continue
            seen.add(cid)
            msg = msgs.get(cid)
            t = msg.get("t_ms") if msg is not None else None
            if not _is_int(t):
                counts[SKIP_NO_MESSAGE] += 1
                continue
            if not in_ranges(t, rngs):
                counts[SKIP_OUT_OF_SCOPE] += 1
                continue
            if done_set is not None and src not in done_set:
                counts[SKIP_SAMPLES_NOT_DONE] += 1
                continue
            head = heads.get(cid)
            if head is None:
                counts[SKIP_NOT_JUDGED] += 1
                continue
            state = target_state(_body(head))
            if state is None:
                counts[SKIP_REACTED] += 1
                continue
            if not sampling.window_in_coverage(t, cov, pre_roll_ms, post_roll_ms):
                counts[SKIP_OUT_OF_MEDIA] += 1
                continue
            c1 = cond1_for(t, segs, bufs)
            aux = cond2_aux(cid, t, ordered, thresholds)
            code, source = suggest_reason(c1, msg.get("kind"), aux, thresholds)
            out.append(Target(cid, t, msg.get("kind"), {"sample_set_id": man["sample_set_id"], "order": it["order"]},
                              src if isinstance(src, str) else None, head.get("entity_id"), state, c1, aux,
                              code, source, existing.get(cid)))
    out.sort(key=lambda x: (x.t_ms, x.chat_id))
    return Selection(tuple(out), counts, len(seen))


# ---------------------------------------------------------------------------- 초안 (03 6-10)
@dataclass(frozen=True)
class Answers:
    """라벨러 입력. cond2·cond3·cond4·check_question 은 True/False/None(미입력)."""
    cond2: bool | None = None
    cond3: bool | None = None
    cond4: bool | None = None
    reason_code: str | None = None
    check_question: bool | None = None
    confidence: str | None = None
    rationale: str = ""


def is_candidate(cond1: bool, cond2, cond3, cond4) -> bool:
    """네 조건이 모두 참일 때만 후보 (안내서 6장)."""
    return cond1 is True and cond2 is True and cond3 is True and cond4 is True


def reason_source(code: str | None, target: Target) -> str | None:
    """고른 코드의 출처: F 는 auto, 앱 제안을 그대로 받으면 auto_suggested, 그 밖은 manual. 코드 없으면 None."""
    if code is None:
        return None
    if code == "F" and target.suggested_source == SOURCE_AUTO:
        return SOURCE_AUTO
    if code == target.suggested_reason and target.suggested_source == SOURCE_SUGGESTED:
        return SOURCE_SUGGESTED
    return SOURCE_MANUAL


def answers_from_body(body: Mapping | None) -> Answers:
    """저장된 body → Answers (고치기로 열 때)."""
    b = body if isinstance(body, Mapping) else {}
    c2 = b.get("cond2") if isinstance(b.get("cond2"), Mapping) else {}
    return Answers(c2.get("value"), b.get("cond3"), b.get("cond4"), b.get("reason_code"), b.get("check_question"),
                   b.get("confidence"), b.get("rationale") or "")


def build_draft(target: Target, answers: Answers) -> dict:
    """대상 + 입력 → store.append_label 초안 {kind, entity_id, supersedes, body}. 검사는 validate_draft 로 따로 한다.
    이미 이 채팅의 판이 있으면 같은 대상의 새 판(entity_id·supersedes = 그 레코드), 없으면 새 대상(둘 다 None).
    is_candidate 면 reason_code 는 비운다. kind ≠ text 면 F 를 강제한다(라벨러가 바꿀 수 없음)."""
    cand = target.chat_kind == TEXT_KIND and is_candidate(target.cond1.value, answers.cond2, answers.cond3,
                                                            answers.cond4)
    code = None if cand else answers.reason_code
    if target.chat_kind != TEXT_KIND:
        code, cand = "F", False
    body = {
        "chat_id": target.chat_id,
        "segment_id": target.segment_id,
        "scope": target.scope,
        "sample_ref": dict(target.sample_ref) if target.sample_ref is not None else None,
        "cond1": target.cond1.as_body(),
        "cond2": target.aux.as_body(answers.cond2),
        "cond3": answers.cond3,
        "cond4": answers.cond4,
        "is_candidate": cand,
        "reason_code": code,
        "reason_code_source": reason_source(code, target),
        "check_question": answers.check_question,
        "confidence": answers.confidence,
        "rationale": (answers.rationale or "").strip(),
    }
    ex = target.existing
    eid = ex.get("entity_id") if isinstance(ex, Mapping) else None
    sup = ex.get("id") if isinstance(ex, Mapping) else None
    return {"kind": KIND, "entity_id": eid if isinstance(eid, str) else None,
            "supersedes": sup if isinstance(eid, str) and isinstance(sup, str) else None, "body": body}


@dataclass(frozen=True)
class Check:
    errors: tuple[str, ...]
    warnings: tuple[tuple[str, str], ...]     # (경고 코드, 문장) — 코드는 label-event warning.code 값

    @property
    def ok(self) -> bool:
        return not self.errors


def _tri(v) -> bool:
    return v is None or isinstance(v, bool)


def validate_draft(draft: Mapping, *, chat_t_ms: int | None, chat_kind: str | None, coverage=None,
                   scope_ranges=None, pre_roll_ms: int = sampling.DEFAULT_PRE_ROLL_MS,
                   post_roll_ms: int = sampling.DEFAULT_POST_ROLL_MS, target: Target | None = None) -> Check:
    """초안 검사(스키마 missedCandidateBody + 이 모듈의 규칙). errors 가 있으면 저장하지 않는다.

    chat_t_ms·chat_kind: 대상 채팅의 시각·종류(채팅 원문에서). coverage·scope_ranges: select_targets 와 같은 인자.
    target 을 주면 ① 과 sample_ref·segment_id 가 앱 계산값과 같은지도 본다(라벨러가 ① 을 바꿀 수 없음).
    """
    errs: list[str] = []
    warns: list[tuple[str, str]] = []
    if not isinstance(draft, Mapping) or draft.get("kind") != KIND:
        return Check(("초안 종류가 missed_candidate 가 아닙니다",), ())
    b = draft.get("body")
    if not isinstance(b, Mapping):
        return Check(("초안 body 가 없습니다",), ())
    eid, sup = draft.get("entity_id"), draft.get("supersedes")
    if (eid is None) != (sup is None):
        errs.append("고치기 초안은 entity_id 와 supersedes 를 같이 줘야 합니다")
    cid = b.get("chat_id")
    if not isinstance(cid, str) or not CHAT_ID_RE.match(cid):
        errs.append("chat_id 형식이 아닙니다")
    # 범위·영상 (D-12 5, D-10 3)
    if not _is_int(chat_t_ms):
        errs.append("대상 채팅의 시각을 모릅니다")
    else:
        rngs = normalize_ranges(scope_ranges) if scope_ranges is not None else None
        if not in_ranges(chat_t_ms, rngs):
            errs.append("세션 범위(재라벨 블록) 밖 채팅입니다 — 놓친 후보는 블록 안에서만 매깁니다 (D-12 5)")
        cov = sampling.normalize_coverage(coverage)
        if not sampling.window_in_coverage(chat_t_ms, cov, pre_roll_ms, post_roll_ms):
            errs.append("영상이 연결되지 않았습니다 — 저장할 수 없습니다 (D-10 3)" if cov == ()
                        else "확인 범위가 영상 밖입니다 — 보고 들을 수 없는 자리는 매길 수 없습니다 (D-10 3)")
    # scope · sample_ref
    scope = b.get("scope")
    sref = b.get("sample_ref")
    if scope not in SCOPES:
        errs.append("scope 는 sample 또는 extra 입니다")
    elif scope == SCOPE_SAMPLE:
        if not (isinstance(sref, Mapping) and isinstance(sref.get("sample_set_id"), str)
                and _is_int(sref.get("order")) and sref["order"] >= 1):
            errs.append("표본 안 판정(scope = sample)은 sample_ref 가 필요합니다")
    elif sref is not None:
        errs.append("표본 밖 후보(scope = extra)에는 sample_ref 를 넣지 않습니다")
    # ①
    c1 = b.get("cond1")
    if not (isinstance(c1, Mapping) and all(isinstance(c1.get(k), bool) for k in ("value", "in_segment", "in_buffer"))):
        errs.append("조건 ① 값이 없습니다")
        c1v = False
    else:
        c1v = c1["value"]
        if c1v != (c1["in_segment"] and not c1["in_buffer"]):
            errs.append("조건 ① 값이 구간·완충 판정과 맞지 않습니다")
        if c1["in_segment"] != (b.get("segment_id") is not None):
            errs.append("segment_id 는 저스트채팅 구간 안일 때만 있습니다")
    if target is not None:
        if c1 != target.cond1.as_body() or b.get("segment_id") != target.segment_id:
            errs.append("조건 ① 은 앱이 계산합니다 — 바꿀 수 없습니다")
        if cid != target.chat_id or (scope == SCOPE_SAMPLE and sref != target.sample_ref):
            errs.append("대상 채팅·표본 참조가 고른 대상과 다릅니다")
    # ②③④ · 점검 질문
    c2 = b.get("cond2")
    c2v = c2.get("value") if isinstance(c2, Mapping) else None
    if not isinstance(c2, Mapping) or not _tri(c2v):
        errs.append("조건 ② 값 모양이 틀렸습니다")
    c3, c4 = b.get("cond3"), b.get("cond4")
    if not (_tri(c3) and _tri(c4) and _tri(b.get("check_question"))):
        errs.append("조건 ③·④·점검 질문은 예·아니오·미입력 중 하나입니다")
    cand = b.get("is_candidate")
    want = is_candidate(c1v, c2v, c3, c4) and chat_kind == TEXT_KIND
    if cand is not want:
        errs.append("후보 여부가 네 조건과 맞지 않습니다 (네 조건이 모두 '예' 일 때만 후보)")
    # 사유 코드
    code, src = b.get("reason_code"), b.get("reason_code_source")
    if cand is True:
        if code is not None or src is not None:
            errs.append("후보이면 사유 코드를 비웁니다")
    else:
        if code not in REASON_CODES:
            errs.append("후보가 아니면 사유 코드를 고르세요 (B·C·D·E·G, 또는 자동 A·F)")
        else:
            if chat_kind != TEXT_KIND and code != "F":
                errs.append("후원·시스템 등 일반 채팅이 아니면 사유는 F(자동)입니다")
            if code == "F" and chat_kind == TEXT_KIND:
                errs.append("F 는 일반 채팅이 아닐 때만 붙습니다")
            if code == "A" and c1v:
                errs.append("A(구간 밖)는 조건 ① 이 실패했을 때만 고를 수 있습니다")
            ck = REASON_COND.get(code)
            if ck is not None:
                v = c2v if ck == "cond2" else b.get(ck)
                if v is True:
                    n = {"cond2": "②", "cond3": "③", "cond4": "④"}[ck]
                    errs.append(f"사유 {code} 인데 조건 {n} 를 '예' 로 표시했습니다")
            if src not in (SOURCE_AUTO, SOURCE_SUGGESTED, SOURCE_MANUAL):
                errs.append("reason_code_source 가 없습니다")
            elif src == SOURCE_AUTO and code != "F":
                errs.append("자동(auto) 사유는 F 뿐입니다")
            if target is not None and src != reason_source(code, target):
                errs.append("사유 출처가 앱 제안과 맞지 않습니다")
    # 확신도 · 근거 (안내서 6장: 확신도 필수)
    if b.get("confidence") not in CONFIDENCES:
        errs.append("확신도를 고르세요 (Alt+1~3)")
    rat = b.get("rationale")
    if not isinstance(rat, str) or not rat.strip():
        errs.append("근거 한 줄이 필요합니다")
    elif len(rat) > RATIONALE_MAX:
        errs.append(f"근거는 {RATIONALE_MAX}자까지입니다")
    elif not parse_timestamps(rat):
        warns.append(("rationale_without_time", "근거에 시각이 없습니다 (Ctrl+T)"))
    return Check(tuple(errs), tuple(warns))


__all__ = [
    "KIND", "SCOPES", "SCOPE_SAMPLE", "SCOPE_EXTRA", "TARGET_STATES", "STATE_NAMES", "REASON_CODES", "REASON_NAMES",
    "MANUAL_REASON_CODES", "AUTO_REASON_CODES", "REASON_COND", "SKIP_KEYS", "MissedError", "MissedThresholds",
    "thresholds_from_guide", "normalize_ranges", "scope_ranges_of", "in_ranges", "in_media", "prior_window",
    "target_state", "Cond1", "cond1_for", "Cond2Aux", "chat_times", "cond2_aux", "suggest_reason", "Target",
    "STATE_EXTRA", "extra_target",
    "Selection", "current_by_chat", "select_targets", "Answers", "is_candidate", "reason_source",
    "answers_from_body", "build_draft", "Check", "validate_draft",
]
