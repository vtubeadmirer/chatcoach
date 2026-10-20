"""3단계 무작위 표본 `sha256-tau-v2` (기본) · `sha256-tau-v1` (재현용) (Qt·파일·시계·난수 없음).

설계: docs/design/06-evaluation-and-agreement.md 2장(계산 규칙의 기준), 03-data-model.md 6-13·15장(저장·재현),
02-architecture.md 3-3(계약), schemas/sample-manifest.schema.json, 07 ADR-09, 08 D-10 1·3.

규칙 요약 (두 판 공통)
  seed_material = f"{sample_seed}|{draw_no}"                 # 구간·기준서 버전은 넣지 않는다
  key(c)        = sha256(UTF-8(seed_material + "|" + chat_id)) 의 소문자 hex
  모집단        = 범위 [start, end) 안, kind ∈ kinds, 시각이 정상인 채팅
  τ             = 적격 0개 → None / 적격 ≤ census_max → 적격 중 가장 큰 key(전수) / 그 밖 → key 오름차순 n번째 key
                  이전 판(또는 물려받은 τ)이 있으면 τ = max(이전 τ, 위 값)       # 불감소
  표본          = 적격이면서 key ≤ τ (role = base)

판마다 다른 것
  v1  적격 = 모집단 − 경계 완충 안. 완충은 모든 안쪽 경계(segments.BUFFER_RULE_ALL). 영상 범위는 보지 않는다.
  v2  적격 = 모집단 − 영상 밖(out_of_media) − 경계 완충 안 (처음 걸린 사유 하나로만 센다).
      완충은 분류가 바뀌는 경계에만(segments.BUFFER_RULE_CHANGE, D-10 1).
      영상 밖 = 7-2 확인 범위 [max(0, t − pre_roll), t + post_roll] 가 영상이 덮는 VOD 범위(coverage) 하나 안에 다 들지
      않음(D-10 3). coverage 는 부르는 쪽이 보정 offset 을 반영해 VOD ms 로 넘긴다. coverage 를 넘기지 않으면(None)
      영상 범위를 보지 않고 매니페스트 media.coverage = null 로 'coverage 없음' 을 남긴다.
  완충 범위 자체는 부르는 쪽이 segments.record_buffers(…, rule=buffer_rule(algorithm)) 로 만들어 넘긴다.
  v2 매니페스트는 쓴 완충을 구간 범위로 잘라 boundary_buffers 에 남긴다. v2 완충은 이웃 구간의 분류에 달려 있어
  원 구간 경계만으로는 다시 만들 수 없기 때문이다(매니페스트 하나만 받아도 재현, 06 2-1).

이 모듈이 하는 일
  - `sort_key`, `draw` (02 3-3 계약 그대로), 모집단·적격 계산(`build_population`),
  - 매니페스트 dict 만들기(`build_manifest`) — 파일에 쓰는 것은 부르는 쪽(store 또는 콜백),
  - 재현 검증(`verify_manifest`), 판정 상태 계산(`item_states`, `item_heads`, `progress`), 실제 시청자 보충(`origin_extend`, v1).
시각(drawn_at)·앱 버전·messages.jsonl 해시는 인자로 받는다(02 3-1: core 는 시계·파일을 쓰지 않는다).
"""
from __future__ import annotations

import bisect
import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

from app.core.ids import CHAT_ID_RE
from app.core.segments import BUFFER_RULE_ALL, BUFFER_RULE_CHANGE

ALGORITHM_V1 = "sha256-tau-v1"
ALGORITHM_V2 = "sha256-tau-v2"
ALGORITHMS: tuple[str, ...] = (ALGORITHM_V1, ALGORITHM_V2)
ALGORITHM = ALGORITHM_V2                 # 새로 뽑을 때의 기본 (08 D-10 1)
_BUFFER_RULE_OF = {ALGORITHM_V1: BUFFER_RULE_ALL, ALGORITHM_V2: BUFFER_RULE_CHANGE}
# 7-2 확인 범위 시작값 (guide-config recheck.pre_roll_sec = 10, post_roll_sec = 30). ui/queue_view·review_player 의
# DEFAULT_PRE_ROLL_MS·POST_ROLL_MS 와 같은 값이다(core 는 ui 를 import 할 수 없어 같은 값을 여기 둔다).
DEFAULT_PRE_ROLL_MS = 10_000
DEFAULT_POST_ROLL_MS = 30_000
SCHEMA = "chatcoach/sample-manifest"
SCHEMA_VERSION = 1
DEFAULT_KINDS: tuple[str, ...] = ("text",)
PRESENTATION_ORDERS = ("time", "key")
ROLE_BASE = "base"
ROLE_ORIGIN_EXTEND = "origin_extend"
REASON_INITIAL = "initial"
REASON_BOUNDARY = "boundary_changed"

# 판정 상태 (03 6-13: 저장하지 않고 계산한다)
PENDING = "pending"
JUDGED = "judged"
OUT_OF_SAMPLE = "out_of_sample"

_SEED_RE = re.compile(r"\A[0-9a-f]{32}\Z")
_SEED_MATERIAL_RE = re.compile(r"\A[0-9a-f]{32}\|[1-9][0-9]*\Z")
_KEY_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_SET_ID_RE = re.compile(r"\AS-([0-9a-f]{8})-d([0-9]+)-r([0-9]+)\Z")
_UUID_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_MS_MIN, _MS_MAX = -3_600_000, 172_800_000
_NOT_GIVEN = object()       # verify_manifest(coverage=…) 를 넘기지 않음(None 은 'coverage 없음' 이라 따로 둔다)


class SamplingError(ValueError):
    """입력이 규칙에 맞지 않는다(형식·계열·크기)."""


# ---------------------------------------------------------------------------- 기본 함수
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_algorithm(algorithm) -> str:
    if algorithm not in ALGORITHMS:
        raise SamplingError(f"알 수 없는 표본 알고리즘: {algorithm!r} ({ALGORITHMS} 중 하나)")
    return algorithm


def buffer_rule(algorithm: str) -> str:
    """표본 알고리즘 → 완충 규칙(segments.record_buffers 의 rule). v1 = all_boundaries, v2 = category_change."""
    return _BUFFER_RULE_OF[_check_algorithm(algorithm)]


Coverage = tuple[tuple[int, int], ...]


def normalize_coverage(coverage: Iterable | None) -> Coverage | None:
    """영상이 덮는 VOD 범위 [start_ms, end_ms) 목록 → 정렬하고 겹치거나 맞닿은 것을 합친 튜플. None 은 None('coverage 없음').

    각 항목은 (start, end) 쌍 또는 [start, end] 목록, start < end 정수 ms. 빈 목록은 '영상이 하나도 없음' (모두 영상 밖).
    """
    if coverage is None:
        return None
    spans: list[tuple[int, int]] = []
    for c in coverage:
        try:
            a, z = c
        except (TypeError, ValueError):
            raise SamplingError("coverage 항목은 [start_ms, end_ms] 쌍이어야 합니다") from None
        if not (_is_int(a) and _is_int(z)) or not (_MS_MIN <= a < z <= _MS_MAX):
            raise SamplingError("coverage 범위는 start_ms < end_ms 인 정수 ms 여야 합니다")
        spans.append((a, z))
    spans.sort()
    out: list[tuple[int, int]] = []
    for a, z in spans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], z))
        else:
            out.append((a, z))
    return tuple(out)


def review_window(t_ms: int, pre_roll_ms: int = DEFAULT_PRE_ROLL_MS, post_roll_ms: int = DEFAULT_POST_ROLL_MS) -> tuple[int, int]:
    """채팅 하나의 7-2 확인 범위 [max(0, t − pre_roll), t + post_roll] (③ 표본 큐가 반복하는 범위와 같다)."""
    return max(0, t_ms - pre_roll_ms), t_ms + post_roll_ms


def window_in_coverage(t_ms: int, coverage: Coverage | None, pre_roll_ms: int = DEFAULT_PRE_ROLL_MS,
                       post_roll_ms: int = DEFAULT_POST_ROLL_MS) -> bool:
    """t 의 7-2 확인 범위가 coverage(normalize_coverage 결과) 의 한 범위 안에 다 드는가. coverage None 이면 늘 True."""
    if coverage is None:
        return True
    a, b = review_window(t_ms, pre_roll_ms, post_roll_ms)
    j = bisect.bisect_right(coverage, (a, _MS_MAX + 1)) - 1
    return j >= 0 and coverage[j][0] <= a and b <= coverage[j][1]


def _check_roll(name: str, v) -> int:
    if not _is_int(v) or v < 0:
        raise SamplingError(f"{name} 는 0 이상 정수 ms 여야 합니다")
    return v


def make_seed_material(sample_seed: str, draw_no: int) -> str:
    """'{sample_seed}|{draw_no}'. sample_seed 는 project.json 의 32자 소문자 hex, draw_no ≥ 1."""
    if not isinstance(sample_seed, str) or not _SEED_RE.match(sample_seed):
        raise SamplingError("sample_seed 는 32자 소문자 hex 여야 합니다")
    if not _is_int(draw_no) or draw_no < 1:
        raise SamplingError("draw_no 는 1 이상의 정수여야 합니다")
    return f"{sample_seed}|{draw_no}"


def parse_seed_material(seed_material: str) -> tuple[str, int]:
    if not isinstance(seed_material, str) or not _SEED_MATERIAL_RE.match(seed_material):
        raise SamplingError("seed_material 형식이 '{32자 hex}|{draw_no}' 가 아닙니다")
    seed, d = seed_material.split("|")
    return seed, int(d)


def sort_key(seed_material: str, chat_id: str) -> str:
    """순서 키 = sha256(UTF-8(seed_material + '|' + chat_id)) 의 소문자 hex (06 2-1)."""
    return hashlib.sha256(f"{seed_material}|{chat_id}".encode("utf-8")).hexdigest()


def ids_sha256(chat_ids: Iterable[str]) -> str:
    """chat_id 를 정렬해 '\\n' 으로 이은 UTF-8 바이트의 sha256 (06 2-7 `chat_ids_sha256`). 빈 목록은 sha256(b'')."""
    return hashlib.sha256("\n".join(sorted(chat_ids)).encode("utf-8")).hexdigest()


def sample_set_id(entity_id: str, draw_no: int, sample_rev: int) -> str:
    """'S-<원 구간 entity_id 앞 8자>-d<draw_no>-r<sample_rev>' (03 6-13)."""
    if not isinstance(entity_id, str) or not _UUID_RE.match(entity_id):
        raise SamplingError("구간 entity_id 는 uuid4 소문자여야 합니다")
    if not _is_int(draw_no) or draw_no < 1 or not _is_int(sample_rev) or sample_rev < 1:
        raise SamplingError("draw_no·sample_rev 는 1 이상의 정수여야 합니다")
    return f"S-{entity_id[:8]}-d{draw_no}-r{sample_rev}"


def parse_sample_set_id(set_id: str) -> tuple[str, int, int]:
    """(entity_id 앞 8자, draw_no, sample_rev). 형식이 아니면 SamplingError."""
    m = _SET_ID_RE.match(set_id) if isinstance(set_id, str) else None
    if not m:
        raise SamplingError(f"sample_set_id 형식이 아닙니다 ({set_id!r})")
    return m.group(1), int(m.group(2)), int(m.group(3))


def series_of(set_id: str) -> tuple[str, int]:
    """계열 = (원 구간 entity_id 앞 8자, draw_no). 같은 계열 안에서 가장 큰 sample_rev 가 유효하다(15-3)."""
    e8, d, _ = parse_sample_set_id(set_id)
    return e8, d


# ---------------------------------------------------------------------------- 추출 (02 3-3 계약)
@dataclass(frozen=True)
class SampleDraw:
    """draw() 결과.

    tau_key: 임계 키 τ (적격 0개면 None). census: 적격 ≤ census_max 라 전부를 씀.
    items: key ≤ τ 인 적격 chat_id, key 오름차순. keys: 적격 chat_id → 순서 키. ranks: chat_id → 적격 중 key 순위(1부터).
    tau_raised_by_prev: τ 가 이번 적격의 n번째 key 가 아니라 prev_tau_key 에서 왔다(경계 변경 뒤 표본이 n 을 넘을 수 있음).
    """
    tau_key: str | None
    census: bool
    items: tuple[str, ...]
    keys: Mapping[str, str]
    ranks: Mapping[str, int]
    eligible_size: int
    requested_size: int
    tau_raised_by_prev: bool = False


def _check_key(name: str, v) -> str:
    if not isinstance(v, str) or not _KEY_RE.match(v):
        raise SamplingError(f"{name} 는 64자 소문자 hex 여야 합니다")
    return v


def draw(eligible_chat_ids: Sequence[str], seed_material: str, n: int, census_max: int,
         prev_tau_key: str | None = None, *, key_fn: Callable[[str, str], str] | None = None) -> SampleDraw:
    """적격 chat_id 묶음에서 표본을 정한다 (06 2-3·2-6, 03 15-1).

    eligible = 구간 안 일반 채팅에서 후원·시스템·(v2) 영상 밖·경계 완충을 뺀 것(부르는 쪽이 현재 구간표·영상 범위로
    만든다 — build_population(…, coverage=…)). draw 자체는 v1·v2 가 같다.
    τ = max(prev_tau_key, 적격 중 n번째 key). 적격이 census_max 이하면 전수(census=True, items = 적격 전부).
    적격이 n 개보다 적은데 census_max 보다 많으면(census_max < n 인 설정) n번째 key 가 없으니 적격 전부를 쓴다(census=False).
    key_fn 은 시험용 주입점(06 2-5 예시처럼 키를 직접 정할 때). 기본은 sort_key.
    """
    if not _is_int(n) or n < 1:
        raise SamplingError("n(requested_size)은 1 이상의 정수여야 합니다")
    if not _is_int(census_max) or census_max < 0:
        raise SamplingError("census_max 는 0 이상의 정수여야 합니다")
    if key_fn is None:
        parse_seed_material(seed_material)
        key_fn = sort_key
    if prev_tau_key is not None:
        _check_key("prev_tau_key", prev_tau_key)
    ids = list(eligible_chat_ids)
    if len(set(ids)) != len(ids):
        raise SamplingError("적격 chat_id 가 겹칩니다")
    keys = {c: key_fn(seed_material, c) for c in ids}
    ordered = sorted(ids, key=lambda c: (keys[c], c))
    ranks = {c: i + 1 for i, c in enumerate(ordered)}
    e = len(ordered)
    if e == 0:
        # 적격이 없으면 τ 도 없다(03 15-1, 스키마 tau_key null). 앞 판 τ 는 이 판에 남지 않는다 — 다음 판이 이 판을
        # previous 로 쓰면 불감소가 끊긴다(드문 경우, 보고 '확인 필요').
        return SampleDraw(None, False, (), keys, ranks, 0, n, False)
    census = e <= census_max
    if census or e <= n:
        base = keys[ordered[-1]]
    else:
        base = keys[ordered[n - 1]]
    tau = base
    raised = False
    if prev_tau_key is not None and prev_tau_key > base:
        tau, raised = prev_tau_key, True
    cut = bisect.bisect_right([keys[c] for c in ordered], tau)
    return SampleDraw(tau, census, tuple(ordered[:cut]), keys, ranks, e, n, raised)


def origin_extend(sd: SampleDraw, real_chat_ids: Iterable[str], max_extend_ratio: float) -> tuple[str | None, tuple[str, ...], bool]:
    """실제 시청자 보충 (06 2-4, v1). τ_real = max(τ, 출처 '실제' 인 적격 중 n번째 key).

    돌려줌: (tau_real_key, 보탤 chat_id(key 순, τ < key ≤ τ_real 이고 실제), 상한에 닿았는가).
    보충 상한은 n × max_extend_ratio(내림). 상한에 닿으면 거기서 멈추고 τ_real 은 마지막으로 보탠 key 다.
    """
    if sd.tau_key is None:
        return None, (), False
    real = set(real_chat_ids)
    real_sorted = sorted((c for c in sd.keys if c in real), key=lambda c: (sd.keys[c], c))
    n = sd.requested_size
    tau_real = sd.tau_key
    if len(real_sorted) >= n and sd.keys[real_sorted[n - 1]] > tau_real:
        tau_real = sd.keys[real_sorted[n - 1]]
    extra = [c for c in real_sorted if sd.tau_key < sd.keys[c] <= tau_real]
    cap = int(n * float(max_extend_ratio))
    capped = len(extra) > cap
    if capped:
        extra = extra[:cap]
        tau_real = sd.keys[extra[-1]] if extra else sd.tau_key
    return tau_real, tuple(extra), capped


# ---------------------------------------------------------------------------- 모집단·적격 (06 2-2, 03 15-2)
def _span(b) -> tuple[int, int]:
    if hasattr(b, "start_ms") and hasattr(b, "end_ms"):
        a, z = b.start_ms, b.end_ms
    else:
        a, z = b
    if not (_is_int(a) and _is_int(z)) or a > z:
        raise SamplingError("완충 범위는 [start_ms, end_ms) 정수여야 합니다")
    return a, z


@dataclass(frozen=True)
class Population:
    """한 구간 범위의 모집단·적격. 시각은 VOD(채팅 시계) ms, 범위는 [range_start_ms, range_end_ms).

    population·eligible: (chat_id, t_ms) 튜플, (t_ms, chat_id) 순.
    excluded_*: 06 2-2 의 1~3번 건수와 영상 밖(D-10 3) 건수(처음 걸린 사유 하나로만 셈). 순서는
    non_text → invalid_time → out_of_media → buffer. 영상 밖과 완충에 같이 걸린 채팅은 out_of_media 로 센다.
    excluded_invalid_time 은 시각이 없거나 정수가 아니거나 시각 범위(스키마 ms) 밖인 채팅 수다. 시각이 없으면 어느 구간인지
    알 수 없어 종류와 상관없이 여기서 센다(같은 채팅이 구간마다 세어진다 — D-10 5 로 그대로 둠).
    coverage: 영상 범위(normalize_coverage 결과). None 이면 영상 범위를 보지 않았다(excluded_out_of_media = 0).
    buffers: 이번 계산에 쓴 경계 완충 중 구간 범위에 걸치는 것을 범위로 자르고 합친 [start, end) 목록. v2 매니페스트의
    boundary_buffers 로 남는다(매니페스트 하나로 재현, 06 2-1).
    """
    range_start_ms: int
    range_end_ms: int
    kinds: tuple[str, ...]
    statuses: tuple[str, ...] | None
    population: tuple[tuple[str, int], ...]
    eligible: tuple[tuple[str, int], ...]
    excluded_non_text: int
    excluded_invalid_time: int
    excluded_buffer: int
    excluded_out_of_media: int = 0
    coverage: Coverage | None = None
    pre_roll_ms: int = DEFAULT_PRE_ROLL_MS
    post_roll_ms: int = DEFAULT_POST_ROLL_MS
    buffers: tuple[tuple[int, int], ...] = ()

    @property
    def eligible_ids(self) -> list[str]:
        return [c for c, _ in self.eligible]

    @property
    def population_ids(self) -> list[str]:
        return [c for c, _ in self.population]

    def t_of(self) -> dict[str, int]:
        return dict(self.population)


def build_population(messages: Iterable[Mapping], start_ms: int, end_ms: int, buffers: Iterable = (), *,
                     kinds: Sequence[str] = DEFAULT_KINDS, statuses: Sequence[str] | None = None,
                     coverage: Iterable | None = None, pre_roll_ms: int = DEFAULT_PRE_ROLL_MS,
                     post_roll_ms: int = DEFAULT_POST_ROLL_MS) -> Population:
    """messages(chat-message 스키마 dict) → 모집단·적격.

    buffers: 경계 완충 [start_ms, end_ms) 목록. core.segments.record_buffers(…, rule=buffer_rule(algorithm)) 의 튜플
    또는 SegmentTimeline.buffer_ranges 의 BufferRange. 구간 범위 밖 완충은 영향이 없다.
    statuses: 넣을 status 목록(None = 전부, v0.1). 목록 밖 채팅은 excluded_non_text 로 센다(스키마에 칸이 따로 없음).
    coverage: 영상이 덮는 VOD 범위 [start_ms, end_ms] 목록(보정 offset 반영은 부르는 쪽). 주면 7-2 확인 범위
    [max(0, t − pre_roll_ms), t + post_roll_ms] 가 한 범위 안에 다 들지 않는 채팅을 적격에서 빼고 excluded_out_of_media 로
    센다(D-10 3, sha256-tau-v2 전용). None 이면 보지 않는다.
    chat_id 형식이 틀리거나 겹치면 SamplingError (수집 쪽 문제를 조용히 넘기지 않는다).
    """
    if not (_is_int(start_ms) and _is_int(end_ms)) or start_ms >= end_ms:
        raise SamplingError("구간 범위는 start_ms < end_ms 정수여야 합니다")
    cov = normalize_coverage(coverage)
    pre, post = _check_roll("pre_roll_ms", pre_roll_ms), _check_roll("post_roll_ms", post_roll_ms)
    kinds_t = tuple(kinds)
    if not kinds_t:
        raise SamplingError("kinds 가 비었습니다")
    stat_t = None if statuses is None else tuple(statuses)
    spans = sorted(_span(b) for b in buffers)
    starts = [a for a, _ in spans]
    # 겹친 완충도 다룰 수 있게 '여기까지의 가장 큰 끝' 을 같이 둔다.
    max_end: list[int] = []
    m = None
    for _, z in spans:
        m = z if m is None or z > m else m
        max_end.append(m)

    def in_buffer(t: int) -> bool:
        j = bisect.bisect_right(starts, t) - 1
        return j >= 0 and t < max_end[j]

    pop: list[tuple[str, int]] = []
    seen: set[str] = set()
    non_text = invalid = 0
    for r in messages:
        cid = r.get("chat_id")
        if not isinstance(cid, str) or not CHAT_ID_RE.match(cid):
            raise SamplingError(f"chat_id 형식이 아닙니다 ({cid!r})")
        if cid in seen:
            raise SamplingError(f"chat_id 가 겹칩니다 ({cid})")
        seen.add(cid)
        t = r.get("t_ms")
        if not _is_int(t) or not (_MS_MIN <= t <= _MS_MAX):
            invalid += 1
            continue
        if not (start_ms <= t < end_ms):
            continue                       # 06 2-2 의 0번: 이 구간의 대상이 아님(세지 않음)
        if r.get("kind") not in kinds_t or (stat_t is not None and r.get("status") not in stat_t):
            non_text += 1
            continue
        pop.append((cid, t))
    pop.sort(key=lambda x: (x[1], x[0]))
    in_media = [x for x in pop if window_in_coverage(x[1], cov, pre, post)]
    elig = [x for x in in_media if not in_buffer(x[1])]
    return Population(start_ms, end_ms, kinds_t, stat_t, tuple(pop), tuple(elig),
                      non_text, invalid, len(in_media) - len(elig), len(pop) - len(in_media), cov, pre, post,
                      clip_buffers(spans, start_ms, end_ms))


def clip_buffers(spans: Iterable, start_ms: int, end_ms: int) -> tuple[tuple[int, int], ...]:
    """완충 [a, z) 목록 → 구간 범위 [start, end) 에 걸치는 것만 범위로 자르고 정렬·합친 튜플(맞닿은 것도 합침).

    범위 안 채팅 t 가 완충 안인지는 자르기 전과 같다. 그래서 v2 매니페스트에 이 값만 남겨도 재현된다."""
    out: list[tuple[int, int]] = []
    for a, z in sorted(_span(b) for b in spans):
        a, z = max(a, start_ms), min(z, end_ms)
        if z <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], z))
        else:
            out.append((a, z))
    return tuple(out)


# ---------------------------------------------------------------------------- 매니페스트 (03 6-13, 스키마)
def _order_items(sd: SampleDraw, chosen: Sequence[tuple[str, str]], t_of: Mapping[str, int], presentation: str) -> list[dict]:
    """chosen = [(chat_id, role)] → items (order 는 1부터 제시 순서)."""
    if presentation == "time":
        seq = sorted(chosen, key=lambda x: (t_of[x[0]], x[0]))
    elif presentation == "key":
        seq = sorted(chosen, key=lambda x: (sd.keys[x[0]], x[0]))
    else:
        raise SamplingError(f"presentation_order 는 {PRESENTATION_ORDERS} 중 하나")
    return [{"order": i + 1, "chat_id": c, "t_ms": t_of[c], "rank": sd.ranks[c],
             "sort_key": sd.keys[c], "role": role} for i, (c, role) in enumerate(seq)]


def _check_source_segment(seg: Mapping) -> dict:
    need = ("session_id", "entity_id", "version_id", "start_ms", "end_ms")
    miss = [k for k in need if k not in seg]
    if miss:
        raise SamplingError(f"source_segment 에 {miss} 가 없습니다")
    if not _UUID_RE.match(str(seg["entity_id"])) or not _UUID_RE.match(str(seg["version_id"])):
        raise SamplingError("source_segment 의 entity_id·version_id 는 uuid4 여야 합니다")
    if not (_is_int(seg["start_ms"]) and _is_int(seg["end_ms"])) or seg["start_ms"] >= seg["end_ms"]:
        raise SamplingError("source_segment 범위가 틀렸습니다")
    return {k: seg[k] for k in need}


def build_manifest(*, project_id: str, source_segment: Mapping, guide_version: str, thresholds_sha256: str,
                   sample_seed: str, draw_no: int, population: Population, messages_sha256: str,
                   n: int, census_max: int, drawn_at: str, drawn_by: str, app_version: str,
                   previous: Mapping | None = None, inherited_tau_keys: Sequence[str] = (),
                   reason: str | None = None, presentation_order: str = "time",
                   real_chat_ids: Iterable[str] | None = None, max_extend_ratio: float | None = None,
                   algorithm: str | None = None) -> dict:
    """표본 매니페스트 dict 를 만든다(파일에 쓰지 않는다). 스키마 sample-manifest.schema.json 모양.

    - algorithm: None 이면 previous 가 있을 때 그 판의 알고리즘(계열 안에서 규칙을 바꾸지 않는다), 없으면 ALGORITHM(v2).
      previous 와 다른 알고리즘을 주면 SamplingError. population 은 부르는 쪽이 그 알고리즘의 완충 규칙
      (buffer_rule(algorithm))으로 만든 것이어야 한다(여기서는 완충 범위를 다시 만들 수 없어 확인하지 못한다).
      v1 은 영상 범위를 모르므로 population.coverage 가 있으면 SamplingError. v1 매니페스트 모양은 예전과 똑같다.
    - v2 는 excluded.out_of_media 와 media {coverage, pre_roll_ms, post_roll_ms} 를 남긴다. coverage 를 안 줬으면
      media.coverage = null('coverage 없음').
    - previous: 같은 계열(같은 원 구간·draw_no)의 바로 앞 판 매니페스트. 주면 sample_rev = 앞 판 + 1, τ 불감소,
      changes(added/removed) 를 계산한다. reason 기본값은 'boundary_changed'.
    - inherited_tau_keys: 쪼개기·합치기로 새 계열을 시작할 때 원 구간들의 τ (06 2-6). τ = max(물려받은 τ, n번째 key).
      물려받은 출처는 reason 에 적는다(전용 필드 없음, 03 15-4 확인 필요).
    - population 범위는 source_segment 의 [start_ms, end_ms) 와 같아야 한다.
    - real_chat_ids·max_extend_ratio: v1 출처 보충(06 2-4). 주지 않으면 tau_real_key = null.
    """
    seg = _check_source_segment(source_segment)
    if (population.range_start_ms, population.range_end_ms) != (seg["start_ms"], seg["end_ms"]):
        raise SamplingError("모집단 범위가 source_segment 범위와 다릅니다")
    seed_material = make_seed_material(sample_seed, draw_no)
    _check_key("thresholds_sha256", thresholds_sha256)
    _check_key("messages_sha256", messages_sha256)
    prev_tau: str | None = None
    if previous is not None:
        if series_of(previous["sample_set_id"]) != (seg["entity_id"][:8], draw_no):
            raise SamplingError("previous 가 같은 계열(원 구간·draw_no)이 아닙니다")
        if previous.get("seed_material") != seed_material:
            raise SamplingError("previous 의 seed_material 이 다릅니다")
        if previous.get("guide_version") != guide_version:
            raise SamplingError("기준서 버전이 바뀌면 새 계열이라 previous 를 쓰지 않습니다 (06 2-6)")
        prev_alg = previous.get("algorithm")
        if algorithm is None:
            algorithm = prev_alg
        elif algorithm != prev_alg:
            raise SamplingError(f"previous 의 알고리즘({prev_alg!r})과 다릅니다 — 계열 안에서 규칙을 바꾸지 않습니다")
        rev = int(previous["sample_rev"]) + 1
        prev_rev: int | None = int(previous["sample_rev"])
        prev_tau = previous.get("tau_key")
        reason = reason or REASON_BOUNDARY
    else:
        rev, prev_rev = 1, None
        reason = reason or REASON_INITIAL
    algorithm = _check_algorithm(ALGORITHM if algorithm is None else algorithm)
    if algorithm == ALGORITHM_V1 and population.coverage is not None:
        raise SamplingError("sha256-tau-v1 은 영상 범위(coverage)를 쓰지 않습니다")
    for k in inherited_tau_keys:
        _check_key("inherited_tau_keys", k)
        if prev_tau is None or k > prev_tau:
            prev_tau = k
    sd = draw(population.eligible_ids, seed_material, n, census_max, prev_tau)
    chosen = [(c, ROLE_BASE) for c in sd.items]
    tau_real = None
    if real_chat_ids is not None:
        if max_extend_ratio is None:
            raise SamplingError("real_chat_ids 를 주면 max_extend_ratio 도 줘야 합니다")
        tau_real, extra, _capped = origin_extend(sd, real_chat_ids, max_extend_ratio)
        chosen += [(c, ROLE_ORIGIN_EXTEND) for c in extra]
    t_of = population.t_of()
    items = _order_items(sd, chosen, t_of, presentation_order)
    changes = None
    if previous is not None:
        before = {it["chat_id"] for it in previous.get("items", [])}
        after = {it["chat_id"] for it in items}
        changes = {"added": sorted(after - before),
                   "removed": [{"chat_id": c, "reason": REASON_BOUNDARY} for c in sorted(before - after)]}
    pop_ids = population.population_ids
    excluded = {"non_text": population.excluded_non_text, "invalid_time": population.excluded_invalid_time,
                "buffer": population.excluded_buffer}
    out = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "sample_set_id": sample_set_id(seg["entity_id"], draw_no, rev),
        "project_id": project_id,
        "source_segment": seg,
        "guide_version": guide_version,
        "thresholds_sha256": thresholds_sha256,
        "algorithm": algorithm,
        "seed_material": seed_material,
        "draw_no": draw_no,
        "sample_rev": rev,
        "previous_sample_rev": prev_rev,
        "reason": reason,
        "changes": changes,
        "population": {"kinds": list(population.kinds),
                       "statuses": None if population.statuses is None else list(population.statuses),
                       "range_start_ms": population.range_start_ms, "range_end_ms": population.range_end_ms,
                       "size": len(pop_ids), "chat_ids_sha256": ids_sha256(pop_ids),
                       "messages_sha256": messages_sha256},
        "excluded": excluded,
        "eligible": {"size": sd.eligible_size, "chat_ids_sha256": ids_sha256(population.eligible_ids)},
        "requested_size": n,
        "census": sd.census,
        "tau_key": sd.tau_key,
        "tau_real_key": tau_real,
        "items": items,
        "presentation_order": presentation_order,
        "drawn_at": drawn_at,
        "drawn_by": drawn_by,
        "app_version": app_version,
    }
    if algorithm == ALGORITHM_V2:
        excluded["out_of_media"] = population.excluded_out_of_media
        out["media"] = _media_block(population.coverage, population.pre_roll_ms, population.post_roll_ms)
        out["boundary_buffers"] = [[a, z] for a, z in population.buffers]
    return out


def _media_block(coverage: Coverage | None, pre_roll_ms: int, post_roll_ms: int) -> dict:
    return {"coverage": None if coverage is None else [[a, z] for a, z in coverage],
            "pre_roll_ms": pre_roll_ms, "post_roll_ms": post_roll_ms}


# ---------------------------------------------------------------------------- 재현 검증 (03 15-5, 06 2-7)
@dataclass(frozen=True)
class Verification:
    ok: bool
    problems: tuple[str, ...]
    recomputed_tau_key: str | None = None
    recomputed_items: tuple[str, ...] = ()

    def summary(self) -> str:
        if self.ok:
            return "재현 검증 통과"
        return f"재현 검증 실패 {len(self.problems)}건: " + "; ".join(self.problems[:3]) + (" …" if len(self.problems) > 3 else "")


def verify_manifest(manifest: Mapping, messages: Iterable[Mapping], buffers=_NOT_GIVEN, *,
                    messages_sha256: str | None = None, previous: Mapping | None = None,
                    prev_tau_key: str | None = None, census_max: int | None = None,
                    real_chat_ids: Iterable[str] | None = None, max_extend_ratio: float | None = None,
                    coverage=_NOT_GIVEN) -> Verification:
    """같은 입력으로 다시 계산해 매니페스트와 견준다. 매니페스트의 algorithm 규칙(v1·v2)으로 다시 계산한다.

    입력: messages.jsonl 줄, 뽑을 때의 구간표로 만든 완충(buffers), 같은 계열 앞 판(previous) 또는 그 tau_key / 물려받은 τ
    (prev_tau_key). messages_sha256 를 주면 채팅 파일이 바뀌었는지도 본다.
    buffers 는 완충 범위 목록이거나 rule → 목록 인 함수다. 함수면 buffer_rule(매니페스트 algorithm) 으로 불러
    v1 은 모든 경계, v2 는 분류가 바뀌는 경계의 완충을 쓴다(예: lambda rule: record_buffers(segs, dur, buf, rule=rule)).
    목록을 넘기면 부르는 쪽이 그 규칙으로 만들었다고 본다.
    v2 는 매니페스트의 boundary_buffers(뽑을 때 쓴 완충, 구간 범위로 자름)로 다시 계산한다. buffers 를 넘기면 그 완충을
    같은 방법으로 잘라 boundary_buffers 와 같은지도 본다(이웃 구간 분류가 바뀌면 다르다). v1 은 buffers 를 넘기지 않으면 완충 없음.
    v2 의 영상 범위는 매니페스트 media(coverage·pre_roll_ms·post_roll_ms)로 다시 계산한다. coverage 를 넘기면(None 포함)
    매니페스트의 media.coverage 와 같은지도 본다(영상 교체·보정 변경 확인용).
    census_max 를 주지 않으면 매니페스트의 census 값을 믿고 따른다(시작값 해시 thresholds_sha256 로 묶여 있으므로).
    문제는 사람이 읽는 문장 목록(chat_id 는 넣되 본문·닉네임은 넣지 않는다).
    """
    p: list[str] = []
    m = manifest
    if m.get("schema") != SCHEMA or m.get("schema_version") != SCHEMA_VERSION:
        p.append("schema·schema_version 이 다릅니다")
    alg = m.get("algorithm")
    if alg not in ALGORITHMS:
        return Verification(False, tuple(p + [f"알고리즘이 {' · '.join(ALGORITHMS)} 가 아닙니다 ({alg!r})"]))
    if callable(buffers):
        buffers = buffers(buffer_rule(alg))
    seg_m = m.get("source_segment") if isinstance(m.get("source_segment"), Mapping) else {}
    if alg == ALGORITHM_V2:
        rec_b = m.get("boundary_buffers")
        try:
            if not isinstance(rec_b, list):
                raise SamplingError("boundary_buffers 가 없습니다")
            rec_t = tuple(_span(b) for b in rec_b)
        except (SamplingError, TypeError, ValueError) as e:
            return Verification(False, tuple(p + [f"sha256-tau-v2 매니페스트의 경계 완충이 틀렸습니다: {e}"]))
        if _is_int(seg_m.get("start_ms")) and _is_int(seg_m.get("end_ms")):
            if clip_buffers(rec_t, seg_m["start_ms"], seg_m["end_ms"]) != rec_t:
                p.append("boundary_buffers 가 구간 범위로 자르고 합친 모양이 아닙니다")
            if buffers is not _NOT_GIVEN:
                try:
                    given = clip_buffers(buffers, seg_m["start_ms"], seg_m["end_ms"])
                except SamplingError as e:
                    given = None
                    p.append(f"넘긴 완충이 틀렸습니다: {e}")
                if given is not None and given != rec_t:
                    p.append("경계 완충이 뽑을 때와 다릅니다 (이웃 구간의 분류·경계가 바뀜)")
        buffers = rec_t
    elif buffers is _NOT_GIVEN:
        buffers = ()
    cov_m = None
    pre, post = DEFAULT_PRE_ROLL_MS, DEFAULT_POST_ROLL_MS
    if alg == ALGORITHM_V2:
        media = m.get("media")
        if not isinstance(media, Mapping):
            return Verification(False, tuple(p + ["sha256-tau-v2 매니페스트에 media 가 없습니다"]))
        try:
            cov_m = normalize_coverage(media.get("coverage"))
            pre = _check_roll("media.pre_roll_ms", media.get("pre_roll_ms"))
            post = _check_roll("media.post_roll_ms", media.get("post_roll_ms"))
        except SamplingError as e:
            return Verification(False, tuple(p + [f"media 가 틀렸습니다: {e}"]))
        if media.get("coverage") is not None and [list(x) for x in cov_m] != media.get("coverage"):
            p.append("media.coverage 가 정렬·합친 모양이 아닙니다")
        if coverage is not _NOT_GIVEN:
            try:
                if normalize_coverage(coverage) != cov_m:
                    p.append("영상 범위(coverage)가 뽑을 때와 다릅니다")
            except SamplingError as e:
                p.append(f"넘긴 coverage 가 틀렸습니다: {e}")
    else:
        if "media" in m or "boundary_buffers" in m or "out_of_media" in (m.get("excluded") or {}):
            p.append("sha256-tau-v1 매니페스트에 v2 칸(media·boundary_buffers·out_of_media)이 있습니다")
        if coverage is not _NOT_GIVEN and coverage is not None:
            p.append("sha256-tau-v1 은 영상 범위를 쓰지 않아 coverage 를 확인할 수 없습니다")
    try:
        seed, d = parse_seed_material(m.get("seed_material"))
        e8, d2, rev = parse_sample_set_id(m.get("sample_set_id"))
        seg = _check_source_segment(m.get("source_segment") or {})
    except (SamplingError, TypeError, AttributeError) as e:
        return Verification(False, tuple(p + [str(e)]))
    if d != m.get("draw_no") or d2 != d:
        p.append("draw_no 가 seed_material·sample_set_id 와 다릅니다")
    if rev != m.get("sample_rev"):
        p.append("sample_rev 가 sample_set_id 와 다릅니다")
    if e8 != seg["entity_id"][:8]:
        p.append("sample_set_id 의 구간 앞 8자가 source_segment 와 다릅니다")
    pop_m = m.get("population") or {}
    if (pop_m.get("range_start_ms"), pop_m.get("range_end_ms")) != (seg["start_ms"], seg["end_ms"]):
        p.append("모집단 범위가 source_segment 범위와 다릅니다")
    if messages_sha256 is not None and pop_m.get("messages_sha256") != messages_sha256:
        p.append("채팅 파일(messages.jsonl) 해시가 뽑을 때와 다릅니다")
    if previous is not None:
        if series_of(previous.get("sample_set_id", "")) != (e8, d):
            p.append("previous 가 같은 계열이 아닙니다")
        elif previous.get("sample_rev") != m.get("previous_sample_rev"):
            p.append("previous_sample_rev 가 앞 판 번호와 다릅니다")
        ptau = previous.get("tau_key")
        if ptau is not None and (prev_tau_key is None or ptau > prev_tau_key):
            prev_tau_key = ptau
    elif rev > 1 and prev_tau_key is None:
        p.append("sample_rev 2 이상인데 앞 판(previous) 또는 그 tau_key 가 없어 τ 불감소를 확인할 수 없습니다")
    try:
        pop = build_population(messages, seg["start_ms"], seg["end_ms"], buffers,
                               kinds=pop_m.get("kinds") or DEFAULT_KINDS, statuses=pop_m.get("statuses"),
                               coverage=cov_m, pre_roll_ms=pre, post_roll_ms=post)
    except SamplingError as e:
        return Verification(False, tuple(p + [f"모집단을 다시 만들 수 없습니다: {e}"]))
    pop_ids = pop.population_ids
    if pop_m.get("size") != len(pop_ids):
        p.append(f"모집단 크기 다름 (매니페스트 {pop_m.get('size')}, 다시 계산 {len(pop_ids)})")
    if pop_m.get("chat_ids_sha256") != ids_sha256(pop_ids):
        p.append("모집단 chat_id 해시가 다릅니다")
    exc = m.get("excluded") or {}
    counts = [("non_text", pop.excluded_non_text), ("invalid_time", pop.excluded_invalid_time),
              ("buffer", pop.excluded_buffer)]
    if alg == ALGORITHM_V2:
        counts.append(("out_of_media", pop.excluded_out_of_media))
    for k, v in counts:
        if exc.get(k) != v:
            p.append(f"제외 {k} 건수 다름 (매니페스트 {exc.get(k)}, 다시 계산 {v})")
    el = m.get("eligible") or {}
    if el.get("size") != len(pop.eligible):
        p.append(f"적격 수 다름 (매니페스트 {el.get('size')}, 다시 계산 {len(pop.eligible)})")
    if el.get("chat_ids_sha256") != ids_sha256(pop.eligible_ids):
        p.append("적격 chat_id 해시가 다릅니다")
    n = m.get("requested_size")
    if not _is_int(n) or n < 1:
        return Verification(False, tuple(p + ["requested_size 가 틀렸습니다"]))
    e = len(pop.eligible)
    cmax = census_max
    if cmax is None:
        cmax = e if m.get("census") else max(e - 1, 0)
    sd = draw(pop.eligible_ids, m["seed_material"], n, cmax, prev_tau_key)
    if m.get("census") != sd.census:
        p.append(f"전수 여부 다름 (매니페스트 {m.get('census')}, 다시 계산 {sd.census})")
    if m.get("tau_key") != sd.tau_key:
        p.append("임계 키 τ 가 다릅니다")
    if prev_tau_key is not None and m.get("tau_key") is not None and m["tau_key"] < prev_tau_key:
        p.append("τ 가 앞 판보다 작아졌습니다 (불감소 위반)")
    chosen = [(c, ROLE_BASE) for c in sd.items]
    tau_real = None
    if real_chat_ids is not None and max_extend_ratio is not None:
        tau_real, extra, _ = origin_extend(sd, real_chat_ids, max_extend_ratio)
        chosen += [(c, ROLE_ORIGIN_EXTEND) for c in extra]
        if m.get("tau_real_key") != tau_real:
            p.append("τ_real 이 다릅니다")
    pres = m.get("presentation_order")
    if pres not in PRESENTATION_ORDERS:
        p.append(f"presentation_order 가 틀렸습니다 ({pres!r})")
        pres = "time"
    want = _order_items(sd, chosen, pop.t_of(), pres)
    got = list(m.get("items") or [])
    check_order = True
    if real_chat_ids is None and any(it.get("role") == ROLE_ORIGIN_EXTEND for it in got):
        # 출처 보충분은 출처 판정 없이는 다시 계산할 수 없으니 base 만 견주고, order 는 보지 않는다.
        got = [it for it in got if it.get("role") != ROLE_ORIGIN_EXTEND]
        got.sort(key=lambda it: it.get("order") or 0)
        check_order = False
        p.append("origin_extend 항목은 출처 판정 없이 확인하지 못했습니다")
    got_ids = [it.get("chat_id") for it in got]
    want_ids = [it["chat_id"] for it in want]
    if set(got_ids) != set(want_ids):
        extra_ids = sorted(set(got_ids) - set(want_ids))
        missing = sorted(set(want_ids) - set(got_ids))
        if extra_ids:
            p.append(f"매니페스트에만 있는 표본 {len(extra_ids)}개 (예: {extra_ids[0]})")
        if missing:
            p.append(f"다시 계산에만 있는 표본 {len(missing)}개 (예: {missing[0]})")
    else:
        bad = 0
        first = None
        fields = ("chat_id", "t_ms", "rank", "role") + (("order",) if check_order else ())
        for g, w in zip(got, want):
            diff = [k for k in fields if g.get(k) != w[k]]
            if "sort_key" in g and g["sort_key"] != w["sort_key"]:
                diff.append("sort_key")
            if diff:
                bad += 1
                first = first or f"{w['chat_id']}({','.join(diff)})"
        if bad:
            p.append(f"표본 항목 {bad}개의 값·순서가 다릅니다 (예: {first})")
    if previous is not None:
        before = {it["chat_id"] for it in previous.get("items", [])}
        after = {it.get("chat_id") for it in (m.get("items") or [])}
        want_ch = {"added": sorted(after - before),
                   "removed": [{"chat_id": c, "reason": REASON_BOUNDARY} for c in sorted(before - after)]}
        if m.get("changes") != want_ch:
            p.append("changes(들어오고 나간 chat_id)가 앞 판과의 차이와 다릅니다")
    elif rev == 1 and m.get("changes") is not None:
        p.append("sample_rev 1 인데 changes 가 있습니다")
    return Verification(not p, tuple(p), sd.tau_key, tuple(want_ids))


# ---------------------------------------------------------------------------- 판정 상태 (03 6-13)
def latest_of(manifests: Iterable[Mapping]) -> dict[tuple[str, int], Mapping]:
    """계열 → 가장 큰 sample_rev 의 매니페스트."""
    out: dict[tuple[str, int], Mapping] = {}
    for m in manifests:
        e8, d, rev = parse_sample_set_id(m["sample_set_id"])
        cur = out.get((e8, d))
        if cur is None or rev > cur["sample_rev"]:
            out[(e8, d)] = m
    return out


def _body(rec: Mapping) -> Mapping:
    b = rec.get("body")
    return b if isinstance(b, Mapping) else rec


def _same_series(b: Mapping, ser: tuple[str, int]) -> bool:
    try:
        return series_of((b.get("sample_ref") or {}).get("sample_set_id", "")) == ser
    except SamplingError:
        return False


def item_states(manifest: Mapping, reactions: Iterable[Mapping]) -> tuple[dict[str, str], tuple[str, ...]]:
    """(chat_id → pending/judged, out_of_sample chat_id 목록) — 판정 상태 집계 규칙(03 6-13, 08 D-10 4).

    reactions: 한 세션의 현재 유효 reaction 레코드(또는 body). 표본 채팅 하나는 다음 중 하나가 있으면 judged 다.
      - entry = from_sample 이고 sample_ref 가 같은 계열(원 구간·draw_no)을 가리키는 반응.
      - entry = from_utterance(② 반응) 인 그 채팅의 반응 (D-10 4: '채팅 1건 = 현재 유효 라벨 1건'. ③ 은 새 라벨을
        만들지 않고 그 라벨을 고치므로, ③ 에서 고치지 않아도 그 라벨이 곧 그 표본의 판정이다).
      - entry = from_sample 이지만 다른 계열(분할·병합 전 원 구간 등)을 가리키는 그 채팅의 반응. 06 2-6: 원 구간에서
        뽑힌 채팅이 새 구간에 남으면 그 판정은 유지된다. 작업대(reaction_for_chat)도 이 라벨을 그 채팅의 라벨로 본다.
    가장 큰 sample_rev 의 items 에 없는 chat_id 의 같은 계열 from_sample 판정은 out_of_sample (지우지 않고 표본 통계에서만
    뺀다). ② 반응·다른 계열 판정은 이 계열 표본에서 빠져도 그 계열(또는 ②)의 라벨일 뿐이라 out_of_sample 로 세지 않는다.
    """
    ser = series_of(manifest["sample_set_id"])
    from_sample: set[str] = set()
    other: set[str] = set()                    # ② 반응 + 다른 계열 from_sample
    for rec in reactions:
        b = _body(rec)
        cid = b.get("chat_id")
        if not isinstance(cid, str):
            continue
        if b.get("entry") == "from_sample":
            if _same_series(b, ser):
                from_sample.add(cid)
            else:
                other.add(cid)
        elif b.get("entry") == "from_utterance":
            other.add(cid)
    ids = [it["chat_id"] for it in manifest.get("items", [])]
    judged = from_sample | other
    states = {c: (JUDGED if c in judged else PENDING) for c in ids}
    out = tuple(sorted(from_sample - set(ids)))
    return states, out


def item_heads(manifest: Mapping, reactions: Iterable[Mapping]) -> dict[str, Mapping]:
    """표본 chat_id → 그 채팅을 판정한 현재 유효 reaction 레코드 (③ 에서 '고치기' 로 열 라벨, D-10 4).

    같은 채팅에 둘 이상이면(D-10 4 전에 만든 기록) 같은 계열 from_sample > 다른 계열 from_sample(분할·병합 전 원 구간
    계열, 06 2-6) > ② from_utterance 순으로 고른다(작업대 reaction_for_chat 도 from_sample 을 앞에 둔다).
    레코드에 entity_id·id 가 없으면 넣지 않는다.
    """
    ser = series_of(manifest["sample_set_id"])
    want = {it["chat_id"] for it in manifest.get("items", [])}
    out: dict[str, Mapping] = {}
    rank_of: dict[str, int] = {}
    for rec in reactions:
        if not isinstance(rec, Mapping) or not rec.get("entity_id") or not rec.get("id"):
            continue
        b = _body(rec)
        cid = b.get("chat_id")
        if cid not in want:
            continue
        e = b.get("entry")
        if e == "from_sample":
            rank = 3 if _same_series(b, ser) else 2
        elif e == "from_utterance":
            rank = 1
        else:
            continue
        if rank >= rank_of.get(cid, 0):
            out[cid], rank_of[cid] = rec, rank
    return out


def head_is_this_series(manifest: Mapping, rec: Mapping | None) -> bool:
    """item_heads 가 고른 레코드가 이 매니페스트 계열의 from_sample 판정인가. 아니면(② 반응·다른 계열 판정)
    ③ 은 새 라벨을 만들지 않고 그 라벨을 편집기(M)로 고친다(D-10 4)."""
    if rec is None:
        return False
    b = _body(rec)
    return b.get("entry") == "from_sample" and _same_series(b, series_of(manifest["sample_set_id"]))


@dataclass(frozen=True)
class Progress:
    done: int
    total: int
    base: int
    extra: int

    def denominator_text(self) -> str:
        """'100' 또는 경계 변경으로 늘었으면 '100 + 3' (02 7-4)."""
        return f"{self.base} + {self.extra}" if self.extra else f"{self.base}"


def progress(manifest: Mapping, states: Mapping[str, str]) -> Progress:
    """진행 = 판정한 표본 / 표본 수. 분모의 기준은 requested_size(적격이 그보다 적으면 표본 수 전체)."""
    total = len(manifest.get("items", []))
    n = int(manifest.get("requested_size") or 0)
    base = total if manifest.get("census") else min(n, total)
    done = sum(1 for v in states.values() if v == JUDGED)
    return Progress(done, total, base, max(0, total - base))
