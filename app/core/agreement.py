"""사람끼리의 일치도 계산 (docs/design/06-evaluation-and-agreement.md 4-0~4-3·4-8·5장, 08 D-11 6·8).

이번 판(D-11 8)이 계산하는 것
- 활동 구간: 1초 칸 Cohen κ(명목, '판단 불가' 도 한 분류), 시간 일치율, 핵심 일치율(양쪽 경계 ± 완충 칸 제외),
  저스트채팅 IoU, 5×5 혼동표, 세션마다 판단 불가 시간 비율, 경계 짝짓기(4-0: 30초 창 안에서 |Δt| 가 작은 쌍부터 1:1)
  와 |Δt| 중앙값·90분위·완충(10초) 안 비율·한쪽에만 있는 경계 수.
- 표본 판정(같은 chat_id 짝): 반응 있음(직접·높음) 이진 일치율·κ·Gwet AC1·유병률, 등급 4단계 선형 가중 κ(06 4-2 가중표)
  ·정확 일치율·한 단계/두 단계 이상 어긋남, 가중치 없는 κ, 불명확 표, 판단 불가 표(둘 다 / 한쪽만), 한쪽만 판정한 수.
- 신뢰구간(4-8): 10분 블록 단위 부트스트랩(블록 복원 추출, 1000회, 95% 백분위 구간). 블록이 10개 미만이면 내지 않는다.
  난수는 쓰지 않는다. 입력 해시에서 만든 시드로 도는 결정적 의사난수(splitmix64)를 순수 함수로 둔다(06 1장).
- 5장 해석 문구: 1인 재라벨(intra)·2인(inter), 기준 통과·미달·불확실·건수 부족, 같은 machine_id 표시(D-11 6).

다섯째 묶음(08 D-11 8 '다음 묶음', D-12 2·3·5·6)에서 더한 것
- 연결 대상(06 4-0·4-3): 같은 chat_id 안에서 연결 발화 시작 차이 ≤ ±5초면 짝. 연결 F1 = 2×짝 ÷ (A 연결 + B 연결),
  한쪽만 찾은 수, 반응 시각 |Δt| 중앙값·90분위·±2초 안 비율.
- 이어짐(06 4-5): 둘 다 직접·높음인 같은 chat_id 짝에서 4값 명목 κ·혼동표, 비움·판단 불가 따로 셈, 원시값(이어진 길이
  차이 중앙값, 다른 시청자 채팅 수 차이 중앙값, '잘림' 일치율).
- 놓친 채팅 후보(06 4-6, 2-9): 두 세션 모두 scope=sample 로 판정한 채팅. 후보 여부 κ·AC1(Gwet)·유병률, 조건 ②③④ 일치율,
  사유 코드 명목 κ·혼동표(둘 다 '후보 아님' 이고 사람이 고른 B·C·D·E·G 만. 자동 A·F 와 source=auto 는 건수만).
  표본 밖에서 따로 찾은 것(extra)은 비교하지 않고 센다.
- 출처(06 4-7, D-12 6): 실제·가상·판별 불가 3×3 혼동표·명목 κ. 생성 기록·봇 가명 대조로 정한 것(method)은 '자동' 으로
  따로 세고, 사람이 정한 것끼리의 κ 를 함께 낸다. 이 함수는 통합 때 core/origin 쪽 같은 함수와 하나로 합친다.
- 표본에서 빠진 판정(D-12 2): 세션의 현재 구간표로 다시 보면 표본 적격 자리(저스트채팅 구간 안 · 경계 완충 밖)가 아닌
  from_sample 판정과 scope=sample 놓친 후보는 일치도에서 빼고 수만 센다. 판정 자체는 그대로 남는다.
- 보고서 머리의 '조정자 = 라벨러' 자리(D-12 3, 입력 플래그).

이번에도 하지 않는 것: 확신도(4-4), 세 세션 이상의 Krippendorff α, 층(무작위·어려움) 나눠 보기, 새 지표의 신뢰구간.

입력은 resolve 결과(현재 유효 라벨)의 레코드 dict(또는 body dict)다. 본문·닉네임·근거(rationale)는 결과에 넣지 않는다
— 근거 한 줄에는 '13:42 ○○님 호명' 처럼 닉네임이 들어갈 수 있다. 결과에 남는 것은 숫자·분류·등급·chat_id·시각뿐이다.

core 층: 파일·시계·난수·Qt 를 쓰지 않는다. 입력 dict 를 고치지 않는다.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
from collections import Counter
from typing import Any, Callable, Iterable, Mapping, Sequence

from .segments import is_transition, record_buffers

# ----------------------------------------------------------------------------- 값
CATEGORIES = ("just_chatting", "game", "singing", "other", "undeterminable")
JUST_CHATTING = "just_chatting"
UNDETERMINABLE = "undeterminable"     # 확신도 '판단 불가' (반응) · 분류 '판단 불가' (구간)

# 반응 등급 척도 (06 4-2): 순위 1(약함) → 4(강함). '불명확' 은 척도 밖.
GRADE_SCALE = ("unconfirmed", "related_only", "likely", "direct")
GRADE_RANK = {g: i + 1 for i, g in enumerate(GRADE_SCALE)}
UNCLEAR = "unclear"
INVALID_VALUE = "invalid"             # 모양이 맞지 않는 반응(등급 없음 등). 따로 센다
POSITIVE = frozenset({"direct", "likely"})   # '반응 있음' (06 4-3, 안내서 4-3)

# 분석 설정 기본값 (06 10장 eval_settings 중 이번에 쓰는 것). block_minutes 는 06 10장 표에 없어 여기서 더했다
# (재라벨 블록 relabel.block_minutes 와 같은 10분, 4-8 '10분 블록 단위 부트스트랩').
DEFAULT_SETTINGS: dict = {
    "agreement": {
        "grid_ms": 1000,
        "boundary_tolerance_sec": 30,
        "bootstrap_n": 1000,
        "min_blocks_for_ci": 10,
        "min_n_for_metric": 30,
        "block_minutes": 10,
        "ci_level": 0.95,
        "utterance_match_tolerance_sec": 5,      # 06 10장 · 4-0 연결 짝 ±5초
        "reaction_time_tolerance_sec": 2,        # 06 10장 · 4-3 반응 시각 ±2초 안 비율
    },
    "gate": {
        "segment_kappa": 0.80,
        "binary_kappa": 0.70,
        "grade_kappa_w": 0.60,
        "boundary_median_sec": 5,
    },
}
DEFAULT_BUFFER_MS = 10_000            # 기준서 segment.boundary_buffer_sec 시작값 (core.segments 와 같음)
FEW_DIRECT_WARN = 50                  # 직접 반응 짝이 이보다 적으면 '결론을 미루라' 문구 (06 4-8 '30건 안팎')
AC1_LOW, AC1_HIGH = 0.20, 0.80        # 유병률이 이 밖이면 κ 옆에 AC1 을 함께 보인다 (06 4-3)

MASK64 = (1 << 64) - 1

# 판정 코드
V_PASS = "pass"
V_PASS_POINT = "pass_point_only"      # 기준을 넘었지만 블록 부족으로 신뢰구간 없음
V_UNCERTAIN = "uncertain"             # 점추정은 넘었지만 신뢰구간 경계가 기준 밖
V_FAIL = "fail"
V_LOW_N = "low_n"                     # 비교 건수 < min_n_for_metric — 판단에 쓰지 않음
V_NA = "not_computable"
VERDICT_KO = {
    V_PASS: "통과", V_PASS_POINT: "통과 (블록 부족 — 점추정만)", V_UNCERTAIN: "불확실",
    V_FAIL: "미달", V_LOW_N: "건수 부족 — 판단에 쓰지 않음", V_NA: "계산 불가",
}


class AgreementError(ValueError):
    """입력이 비교 조건에 맞지 않음(설정 값 잘못 등)."""


# ----------------------------------------------------------------------------- 설정
def merge_settings(override: Mapping | None = None) -> dict:
    """DEFAULT_SETTINGS 위에 override 를 얹은 사본. 모르는 블록·키는 그대로 기록만 하고, 아는 키는 형식을 검사한다."""
    out = copy.deepcopy(DEFAULT_SETTINGS)
    for blk, vals in (override or {}).items():
        if not isinstance(vals, Mapping):
            raise AgreementError(f"설정 블록 {blk!r} 은 object 여야 합니다")
        dst = out.setdefault(blk, {})
        for k, v in vals.items():
            dst[k] = copy.deepcopy(v)
    a, g = out["agreement"], out["gate"]
    for k in ("grid_ms", "bootstrap_n", "min_blocks_for_ci", "min_n_for_metric", "block_minutes"):
        if not _is_int(a.get(k)) or a[k] < (0 if k == "bootstrap_n" else 1):
            raise AgreementError(f"agreement.{k} 는 양의 정수여야 합니다")
    if not _is_num(a.get("boundary_tolerance_sec")) or a["boundary_tolerance_sec"] < 0:
        raise AgreementError("agreement.boundary_tolerance_sec 는 0 이상의 수여야 합니다")
    if not _is_num(a.get("ci_level")) or not 0 < a["ci_level"] < 1:
        raise AgreementError("agreement.ci_level 은 0 과 1 사이여야 합니다")
    for k in ("utterance_match_tolerance_sec", "reaction_time_tolerance_sec"):
        if not _is_num(a.get(k)) or a[k] < 0:
            raise AgreementError(f"agreement.{k} 는 0 이상의 수여야 합니다")
    for k in ("segment_kappa", "binary_kappa", "grade_kappa_w", "boundary_median_sec"):
        if not _is_num(g.get(k)):
            raise AgreementError(f"gate.{k} 는 수여야 합니다")
    return out


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _body(rec: Mapping) -> Mapping:
    b = rec.get("body") if isinstance(rec, Mapping) else None
    return b if isinstance(b, Mapping) else rec


# ----------------------------------------------------------------------------- κ 류 (표 → 값)
def kappa_from_table(table: Mapping[tuple, int], categories: Sequence | None = None) -> dict:
    """Cohen κ (명목). table: {(A 값, B 값): 건수}. 결과 {n, p_o, p_e, kappa}. 계산할 수 없으면 kappa=None.

    p_o = Σ_c n_cc / N, p_e = Σ_c (A 의 c 비율)(B 의 c 비율), κ = (p_o − p_e) / (1 − p_e).
    p_e = 1 (두 세션이 모두 한 값만 씀) 이면 κ 는 정의되지 않는다(None).
    """
    n = sum(table.values())
    if n <= 0:
        return {"n": 0, "p_o": None, "p_e": None, "kappa": None}
    cats = list(categories) if categories is not None else sorted({k[0] for k in table} | {k[1] for k in table})
    ra: Counter = Counter()
    cb: Counter = Counter()
    agree = 0
    for (x, y), c in table.items():
        ra[x] += c
        cb[y] += c
        if x == y:
            agree += c
    p_o = agree / n
    p_e = sum((ra[c] / n) * (cb[c] / n) for c in cats)
    kappa = None if abs(1 - p_e) < 1e-12 else (p_o - p_e) / (1 - p_e)
    return {"n": n, "p_o": p_o, "p_e": p_e, "kappa": kappa}


def linear_weight(i: int, j: int, k: int) -> float:
    """06 4-2 가중표: w_ij = 1 − |i − j| / (k − 1). k=4: 같으면 1, 한 단계 2/3, 두 단계 1/3, 세 단계 0."""
    return 1 - abs(i - j) / (k - 1)


def weighted_kappa_from_table(table: Mapping[tuple, int], scale: Sequence = GRADE_SCALE) -> dict:
    """선형 가중 κ (06 4-2). table 의 값은 scale 안의 것만 받는다(밖이면 AgreementError).

    p_o^w = Σ w_ij n_ij / N, p_e^w = Σ w_ij n_i· n_·j / N², κ_w = (p_o^w − p_e^w) / (1 − p_e^w).
    """
    rank = {v: i for i, v in enumerate(scale)}
    k = len(scale)
    n = sum(table.values())
    if n <= 0:
        return {"n": 0, "p_o_w": None, "p_e_w": None, "kappa_w": None}
    row: Counter = Counter()
    col: Counter = Counter()
    po = 0.0
    for (x, y), c in table.items():
        if x not in rank or y not in rank:
            raise AgreementError(f"척도 밖 값: {x!r}, {y!r}")
        row[x] += c
        col[y] += c
        po += linear_weight(rank[x], rank[y], k) * c
    po /= n
    pe = sum(linear_weight(rank[x], rank[y], k) * row[x] * col[y] for x in scale for y in scale) / (n * n)
    kw = None if abs(1 - pe) < 1e-12 else (po - pe) / (1 - pe)
    return {"n": n, "p_o_w": po, "p_e_w": pe, "kappa_w": kw}


def ac1_binary(table: Mapping[tuple, int]) -> dict:
    """Gwet AC1 (이진, 06 4-3). 키는 (bool, bool). π = (A 양성 비율 + B 양성 비율)/2, p_e = 2π(1−π)."""
    n = sum(table.values())
    if n <= 0:
        return {"n": 0, "prevalence": None, "ac1": None}
    pa = sum(c for (x, _), c in table.items() if x) / n
    pb = sum(c for (_, y), c in table.items() if y) / n
    po = sum(c for (x, y), c in table.items() if x == y) / n
    pi = (pa + pb) / 2
    pe = 2 * pi * (1 - pi)
    ac1 = None if abs(1 - pe) < 1e-12 else (po - pe) / (1 - pe)
    return {"n": n, "prevalence": pi, "ac1": ac1}


# ----------------------------------------------------------------------------- 분위수·결정적 의사난수·부트스트랩
def percentile(values: Sequence[float], q: float) -> float | None:
    """선형 보간 분위수(numpy 기본 'linear' 와 같음). q 는 0~1. 빈 목록이면 None."""
    xs = sorted(values)
    if not xs:
        return None
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def seed_from(material: str) -> int:
    """시드 문자열(입력 해시 등) → 64비트 정수. sha256 앞 8바이트(빅 엔디언)."""
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


class SplitMix64:
    """결정적 의사난수 (splitmix64). OS 난수·시계를 쓰지 않는다 — 같은 시드면 어느 PC·Python 판에서도 같은 수열이다."""

    def __init__(self, seed: int):
        self.state = seed & MASK64

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        """0 ≤ r < n 의 고른 정수(거부 표집으로 치우침 없음)."""
        if n <= 0:
            raise ValueError("n 은 1 이상")
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            r = self.next_u64()
            if r < limit:
                return r % n


def bootstrap_ci(blocks: Sequence[Any], stat: Callable[[Sequence[Any]], float | None], *, n_boot: int,
                 seed_material: str, level: float = 0.95, min_blocks: int = 10) -> dict:
    """블록 부트스트랩 백분위 신뢰구간 (06 4-8). blocks: 블록마다의 집계 값. stat(뽑은 블록 목록) → 값 또는 None.

    결과 {status, lo, hi, n_blocks, n_boot, n_undefined}. status: ok / too_few_blocks / no_valid_resample / off.
    """
    nb = len(blocks)
    base = {"n_blocks": nb, "n_boot": n_boot, "lo": None, "hi": None, "n_undefined": 0}
    if n_boot <= 0:
        return {**base, "status": "off"}
    if nb < min_blocks:
        return {**base, "status": "too_few_blocks"}
    rng = SplitMix64(seed_from(seed_material))
    vals: list[float] = []
    undefined = 0
    for _ in range(n_boot):
        pick = [blocks[rng.below(nb)] for _ in range(nb)]
        v = stat(pick)
        if v is None:
            undefined += 1
        else:
            vals.append(v)
    if not vals:
        return {**base, "status": "no_valid_resample", "n_undefined": undefined}
    a = (1 - level) / 2
    return {**base, "status": "ok", "lo": percentile(vals, a), "hi": percentile(vals, 1 - a), "n_undefined": undefined}


def _sum_counters(items: Iterable[Counter]) -> Counter:
    out: Counter = Counter()
    for c in items:
        out.update(c)
    return out


# ----------------------------------------------------------------------------- 활동 구간
class _Timeline:
    """한 세션의 현재 유효 구간 → 시각별 분류. 구간은 [start_ms, end_ms)."""

    def __init__(self, segments: Iterable[Mapping]):
        segs = []
        for r in segments:
            b = _body(r)
            s, e, c = b.get("start_ms"), b.get("end_ms"), b.get("category")
            if _is_int(s) and _is_int(e) and e > s and c in CATEGORIES:
                segs.append((s, e, c))
        segs.sort()
        self.segs = segs
        self.starts = [s for s, _, _ in segs]

    def cat_at(self, t: int) -> str | None:
        i = bisect.bisect_right(self.starts, t) - 1
        while i >= 0:
            s, e, c = self.segs[i]
            if s <= t < e:
                return c
            if e <= t:
                return None
            i -= 1
        return None

    def extent(self) -> tuple[int, int] | None:
        if not self.segs:
            return None
        return self.segs[0][0], max(e for _, e, _ in self.segs)

    def boundaries(self, ranges: Sequence[tuple[int, int]]) -> list[int]:
        """비교 범위 안(가장자리 제외)의 '활동이 바뀌는 경계' (core.segments.is_transition: 분류가 다르거나 빈틈과 맞닿음).

        범위 가장자리(블록 가장자리·비교 범위 끝)는 경계가 아니다(06 3-1).
        """
        edges = sorted({s for s, _, _ in self.segs} | {e for _, e, _ in self.segs})
        out = []
        for t in edges:
            if not any(lo < t < hi for lo, hi in ranges):
                continue
            left, right = self.cat_at(t - 1), self.cat_at(t)
            if left is None and right is None:
                continue
            if is_transition(left, right):
                out.append(t)
        return out


def _clip_ranges(scope_a: Sequence | None, scope_b: Sequence | None, extent: tuple[int, int]) -> list[tuple[int, int]]:
    """두 세션 범위(None = 방송 전체)와 구간이 덮는 바깥 범위의 교집합."""
    cur = [extent]
    for sc in (scope_a, scope_b):
        if sc is None:
            continue
        nxt = []
        for lo, hi in cur:
            for a, b in sc:
                x, y = max(lo, int(a)), min(hi, int(b))
                if y > x:
                    nxt.append((x, y))
        cur = nxt
    cur.sort()
    merged: list[tuple[int, int]] = []
    for lo, hi in cur:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def match_boundaries(a: Sequence[int], b: Sequence[int], tolerance_ms: int) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """경계 1:1 짝짓기 (06 4-0): |Δt| ≤ 허용 창인 쌍 가운데 |Δt| 가 작은 쌍부터 차례로. 같으면 (A 시각, B 시각) 순.

    결과 (짝 [(a, b)], A 에만 있는 경계, B 에만 있는 경계).
    """
    cand = sorted((abs(x - y), x, y) for x in a for y in b if abs(x - y) <= tolerance_ms)
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs = []
    for _, x, y in cand:
        if x in used_a or y in used_b:
            continue
        used_a.add(x)
        used_b.add(y)
        pairs.append((x, y))
    pairs.sort()
    return pairs, [x for x in sorted(a) if x not in used_a], [y for y in sorted(b) if y not in used_b]


def _seg_stats(blocks: Sequence[Mapping]) -> dict:
    allc = _sum_counters(b["all"] for b in blocks)
    core = _sum_counters(b["core"] for b in blocks)
    k = kappa_from_table(allc, CATEGORIES)
    kc = kappa_from_table(core, CATEGORIES)
    inter = sum(b["jc_inter"] for b in blocks)
    union = sum(b["jc_union"] for b in blocks)
    return {"all": allc, "core": core, "kappa": k, "core_kappa": kc,
            "jc_iou": (inter / union) if union else None, "jc_inter": inter, "jc_union": union}


def compare_segments(segments_a: Iterable[Mapping], segments_b: Iterable[Mapping], *,
                     scope_a: Sequence | None = None, scope_b: Sequence | None = None,
                     buffer_ms: int = DEFAULT_BUFFER_MS, settings: Mapping | None = None,
                     seed_material: str = "") -> dict:
    """활동 구간 일치도 (06 4-1, 4-0 경계 짝짓기).

    - scope_a/b: 세션이 라벨을 만들 수 있는 범위 [(시작, 끝)] (session.open scope=blocks 의 ranges). None = 방송 전체.
    - 비교 범위 = 두 범위의 교집합 ∩ 두 세션 구간이 덮는 바깥 범위. 그 안을 grid_ms 칸으로 나눈다(범위마다 시작에서,
      끝에 남는 짧은 칸은 버린다). 칸의 분류는 칸 가운데 시각의 구간 분류다.
    - 한쪽이라도 구간이 없는 칸은 일치도에 넣지 않고 uncovered 로 센다.
    - 핵심 칸 = 어느 쪽 경계 b 에 대해서도 (b − 완충, b + 완충) 과 겹치지 않는 칸.
    """
    st = merge_settings(settings)
    ag = st["agreement"]
    grid = int(ag["grid_ms"])
    block_ms = int(ag["block_minutes"]) * 60_000
    tol_ms = int(round(float(ag["boundary_tolerance_sec"]) * 1000))
    ta, tb = _Timeline(segments_a), _Timeline(segments_b)
    ea, eb = ta.extent(), tb.extent()
    empty = {
        "ranges": [], "cells_total": 0, "cells_compared": 0, "uncovered": {"a_only": 0, "b_only": 0, "neither": 0},
        "time_agreement": None, "kappa": None, "p_o": None, "p_e": None, "core": {"cells": 0, "agreement": None, "kappa": None},
        "jc_iou": None, "confusion": _empty_confusion(CATEGORIES), "undeterminable_ratio": {"a": None, "b": None},
        "boundaries": _boundary_summary([], [], [], buffer_ms, tol_ms), "ci": {}, "n_blocks": 0,
        "segments_count": {"a": len(ta.segs), "b": len(tb.segs)},
    }
    if ea is None or eb is None:
        return empty
    extent = (min(ea[0], eb[0]), max(ea[1], eb[1]))
    ranges = _clip_ranges(scope_a, scope_b, extent)
    if not ranges:
        return empty

    ba, bb = ta.boundaries(ranges), tb.boundaries(ranges)
    pairs, only_a, only_b = match_boundaries(ba, bb, tol_ms)
    windows: list[tuple[int, int]] = []          # 경계 ± 완충 열린 구간을 합친 것 (겹치지 않게 정렬)
    for lo_w, hi_w in sorted((x - buffer_ms, x + buffer_ms) for x in set(ba) | set(bb)):
        if windows and lo_w <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], hi_w))
        else:
            windows.append((lo_w, hi_w))
    wstarts = [w[0] for w in windows]

    def near_boundary(t0: int, t1: int) -> bool:
        """칸 [t0, t1) 이 어느 (b − 완충, b + 완충) 과 겹치는가."""
        i = bisect.bisect_left(wstarts, t1) - 1      # 시작이 t1 보다 앞인 마지막 창
        return i >= 0 and windows[i][1] > t0

    blocks: dict[int, dict] = {}
    unc = {"a_only": 0, "b_only": 0, "neither": 0}
    total = 0
    und_a = und_b = 0
    for lo, hi in ranges:
        t = lo
        while t + grid <= hi:
            total += 1
            m = t + grid // 2
            ca, cb = ta.cat_at(m), tb.cat_at(m)
            if ca is None or cb is None:
                unc["a_only" if cb is None and ca is not None else "b_only" if ca is None and cb is not None else "neither"] += 1
                t += grid
                continue
            blk = blocks.setdefault(t // block_ms, {"all": Counter(), "core": Counter(), "jc_inter": 0, "jc_union": 0})
            blk["all"][(ca, cb)] += 1
            if not near_boundary(t, t + grid):
                blk["core"][(ca, cb)] += 1
            ja, jb = ca == JUST_CHATTING, cb == JUST_CHATTING
            blk["jc_inter"] += int(ja and jb)
            blk["jc_union"] += int(ja or jb)
            und_a += int(ca == UNDETERMINABLE)
            und_b += int(cb == UNDETERMINABLE)
            t += grid

    blist = [blocks[k] for k in sorted(blocks)]
    s = _seg_stats(blist)
    n = s["kappa"]["n"]
    core_n = s["core_kappa"]["n"]
    bsum = _boundary_summary(pairs, only_a, only_b, buffer_ms, tol_ms)

    nboot, minb, lvl = int(ag["bootstrap_n"]), int(ag["min_blocks_for_ci"]), float(ag["ci_level"])
    ci = {
        "kappa": bootstrap_ci(blist, lambda bs: _seg_stats(bs)["kappa"]["kappa"], n_boot=nboot,
                              seed_material=seed_material + "|segment.kappa", level=lvl, min_blocks=minb),
    }
    # 경계 |Δt| 중앙값: 짝을 앞쪽 시각의 블록으로 묶어 다시 뽑는다
    pblocks: dict[int, list[int]] = {}
    for x, y in pairs:
        pblocks.setdefault(min(x, y) // block_ms, []).append(abs(x - y))
    plist = [pblocks[k] for k in sorted(pblocks)]
    ci["boundary_median_sec"] = bootstrap_ci(
        plist, lambda bs: _median_sec([d for b in bs for d in b]), n_boot=nboot,
        seed_material=seed_material + "|segment.boundary", level=lvl, min_blocks=minb)

    return {
        "ranges": [list(r) for r in ranges],
        "cells_total": total,
        "cells_compared": n,
        "uncovered": unc,
        "time_agreement": s["kappa"]["p_o"],
        "kappa": s["kappa"]["kappa"],
        "p_o": s["kappa"]["p_o"],
        "p_e": s["kappa"]["p_e"],
        "core": {"cells": core_n, "agreement": s["core_kappa"]["p_o"], "kappa": s["core_kappa"]["kappa"],
                 "excluded_cells": n - core_n},
        "jc_iou": s["jc_iou"],
        "jc_seconds": {"intersection": s["jc_inter"], "union": s["jc_union"]},
        "confusion": _confusion(s["all"], CATEGORIES),
        "undeterminable_ratio": {"a": (und_a / n) if n else None, "b": (und_b / n) if n else None},
        "boundaries": bsum,
        "ci": ci,
        "n_blocks": len(blist),
        "segments_count": {"a": len(ta.segs), "b": len(tb.segs)},
    }


def _median_sec(diffs_ms: Sequence[int]) -> float | None:
    m = percentile(diffs_ms, 0.5)
    return None if m is None else m / 1000


def _boundary_summary(pairs, only_a, only_b, buffer_ms: int, tol_ms: int) -> dict:
    d = [abs(x - y) for x, y in pairs]
    return {
        "a_count": len(pairs) + len(only_a),
        "b_count": len(pairs) + len(only_b),
        "matched": len(pairs),
        "a_only": len(only_a),
        "b_only": len(only_b),
        "median_sec": _median_sec(d),
        "p90_sec": None if not d else percentile(d, 0.9) / 1000,
        "within_buffer_ratio": (sum(1 for x in d if x <= buffer_ms) / len(d)) if d else None,
        "buffer_sec": buffer_ms / 1000,
        "tolerance_sec": tol_ms / 1000,
        "deltas_sec": [x / 1000 for x in sorted(d)],
    }


def _empty_confusion(cats: Sequence) -> dict:
    return {"labels": list(cats), "rows": [[0] * len(cats) for _ in cats]}


def _confusion(table: Mapping[tuple, int], cats: Sequence) -> dict:
    """행 = A, 열 = B."""
    return {"labels": list(cats), "rows": [[int(table.get((x, y), 0)) for y in cats] for x in cats]}


# ----------------------------------------------------------------------------- 표본 판정
def reaction_value(rec: Mapping) -> str:
    """반응 라벨 → 비교 값. 확신도 판단 불가 → 'undeterminable'(따로 셈), 결과 unconfirmed → 'unconfirmed'(반응 미확인),
    등급 direct·likely·related_only → 그대로, unclear → 'unclear'(척도 밖). 그 밖은 'invalid'."""
    b = _body(rec)
    if b.get("confidence") == UNDETERMINABLE:
        return UNDETERMINABLE
    if b.get("result") == "unconfirmed":
        return "unconfirmed"
    g = b.get("grade")
    if g in ("direct", "likely", "related_only"):
        return g
    if g == UNCLEAR:
        return UNCLEAR
    return INVALID_VALUE


def _by_chat(reactions: Iterable[Mapping]) -> tuple[dict[str, Mapping], int]:
    """chat_id → 반응 body. 같은 chat_id 가 둘 이상이면(채팅 1건 = 라벨 1건 위반) 그 chat_id 는 빼고 센다."""
    seen: dict[str, Mapping] = {}
    dup: set[str] = set()
    for r in reactions:
        b = _body(r)
        cid = b.get("chat_id")
        if not isinstance(cid, str) or not cid:
            continue
        if cid in seen:
            dup.add(cid)
        else:
            seen[cid] = b
    for c in dup:
        seen.pop(c, None)
    return seen, len(dup)


def _sample_stats(blocks: Sequence[Counter]) -> dict:
    t = _sum_counters(blocks)
    binary: Counter = Counter()
    for (x, y), c in t.items():
        binary[(x in POSITIVE, y in POSITIVE)] += c
    return {"grade": t, "binary": binary,
            "kappa_w": weighted_kappa_from_table(t, GRADE_SCALE),
            "kappa_grade": kappa_from_table(t, GRADE_SCALE),
            "kappa_bin": kappa_from_table(binary, (True, False)),
            "ac1": ac1_binary(binary)}


def compare_samples(reactions_a: Iterable[Mapping], reactions_b: Iterable[Mapping], *,
                    chat_ids: Iterable[str] | None = None, chat_times: Mapping[str, int] | None = None,
                    settings: Mapping | None = None, seed_material: str = "",
                    scope: Sequence | None = None) -> dict:
    """표본 판정 일치도 (06 4-0 같은 chat_id 짝, 4-2 등급, 4-3 이진).

    - chat_ids 를 주면 그 채팅만 본다(표본 항목). None 이면 두 세션의 현재 유효 반응 전부.
    - 확신도 판단 불가는 한쪽이라도 있으면 등급·이진에서 빼고 '판단 불가 표' 로 센다(4-0).
    - 불명확은 한쪽이라도 있으면 척도·이진에서 빼고 '불명확 표' 로 센다(4-2, 4-3 예).
    - 블록(신뢰구간)은 chat_times[chat_id], 없으면 A 쪽 first_visible_ms 로 정한다.
    - scope 를 주면(두 세션 범위의 교집합 [(시작, 끝)]) 채팅 시각(chat_times, 없으면 그 쪽 first_visible_ms)이 그 밖인
      반응은 비교에서 빼고 judged.outside_scope_a·b 로 따로 센다(06 3-5 '한쪽만 판정한 건수' 는 비교한 블록 안의 수).
      시각을 모르는 반응은 뺄 수 없어 남긴다.
    """
    st = merge_settings(settings)
    ag = st["agreement"]
    block_ms = int(ag["block_minutes"]) * 60_000
    ma, dup_a = _by_chat(reactions_a)
    mb, dup_b = _by_chat(reactions_b)
    if chat_ids is not None:
        keep = set(chat_ids)
        ma = {k: v for k, v in ma.items() if k in keep}
        mb = {k: v for k, v in mb.items() if k in keep}
    out_a = out_b = 0
    if scope is not None:
        rng = [(int(a), int(b)) for a, b in scope]

        def inside(cid: str, body: Mapping) -> bool:
            t = (chat_times or {}).get(cid)
            if not _is_int(t):
                t = body.get("first_visible_ms")
            if not _is_int(t):
                return True
            return any(lo <= t < hi for lo, hi in rng)
        n_a, n_b = len(ma), len(mb)
        ma = {k: v for k, v in ma.items() if inside(k, v)}
        mb = {k: v for k, v in mb.items() if inside(k, v)}
        out_a, out_b = n_a - len(ma), n_b - len(mb)
    both = sorted(set(ma) & set(mb))
    und = {"both": 0, "a_only": 0, "b_only": 0}
    unc = {"both": 0, "a_only": 0, "b_only": 0}
    inv = 0
    entries: Counter = Counter()
    blocks: dict[int, Counter] = {}
    disagreements = []
    direct_pairs = 0
    for cid in both:
        ra, rb = ma[cid], mb[cid]
        va, vb = reaction_value(ra), reaction_value(rb)
        entries[f"{ra.get('entry')}/{rb.get('entry')}"] += 1
        if va == INVALID_VALUE or vb == INVALID_VALUE:
            inv += 1
            continue
        if va == UNDETERMINABLE or vb == UNDETERMINABLE:
            und["both" if va == vb else "a_only" if va == UNDETERMINABLE else "b_only"] += 1
            continue
        if va == UNCLEAR or vb == UNCLEAR:
            unc["both" if va == vb else "a_only" if va == UNCLEAR else "b_only"] += 1
            if va != vb:
                disagreements.append(_case(cid, ra, chat_times, va, vb))
            continue
        t = (chat_times or {}).get(cid)
        if not _is_int(t):
            t = ra.get("first_visible_ms") if _is_int(ra.get("first_visible_ms")) else 0
        blocks.setdefault(t // block_ms, Counter())[(va, vb)] += 1
        direct_pairs += int(va == "direct" or vb == "direct")
        if va != vb:
            disagreements.append(_case(cid, ra, chat_times, va, vb))

    blist = [blocks[k] for k in sorted(blocks)]
    s = _sample_stats(blist)
    n = s["kappa_w"]["n"]
    diffs = Counter()
    for (x, y), c in s["grade"].items():
        diffs[abs(GRADE_RANK[x] - GRADE_RANK[y])] += c
    bt = s["binary"]
    nboot, minb, lvl = int(ag["bootstrap_n"]), int(ag["min_blocks_for_ci"]), float(ag["ci_level"])
    ci = {
        "binary_kappa": bootstrap_ci(blist, lambda bs: _sample_stats(bs)["kappa_bin"]["kappa"], n_boot=nboot,
                                     seed_material=seed_material + "|sample.binary", level=lvl, min_blocks=minb),
        "grade_kappa_w": bootstrap_ci(blist, lambda bs: _sample_stats(bs)["kappa_w"]["kappa_w"], n_boot=nboot,
                                      seed_material=seed_material + "|sample.grade", level=lvl, min_blocks=minb),
    }
    prev = s["ac1"]["prevalence"]
    disagreements.sort(key=lambda d: (d["t_ms"] if d["t_ms"] is not None else -1, d["chat_id"]))
    return {
        "judged": {"a": len(ma), "b": len(mb), "both": len(both),
                   "a_only": len(set(ma) - set(mb)), "b_only": len(set(mb) - set(ma)),
                   "duplicate_chat_a": dup_a, "duplicate_chat_b": dup_b, "invalid_pairs": inv,
                   "outside_scope_a": out_a, "outside_scope_b": out_b},
        "restricted_to_chat_ids": chat_ids is not None,
        "restricted_to_scope": scope is not None,
        "entries": dict(sorted(entries.items())),
        "undeterminable": und,
        "unclear": {**unc, "agreement": (unc["both"] / sum(unc.values())) if sum(unc.values()) else None},
        "grade": {
            "n": n,
            "exact": (diffs[0] / n) if n else None,
            "one_step": (diffs[1] / n) if n else None,
            "two_plus": (sum(c for d, c in diffs.items() if d >= 2) / n) if n else None,
            "kappa_w": s["kappa_w"]["kappa_w"], "p_o_w": s["kappa_w"]["p_o_w"], "p_e_w": s["kappa_w"]["p_e_w"],
            "kappa_unweighted": s["kappa_grade"]["kappa"],
            "confusion": _confusion(s["grade"], tuple(reversed(GRADE_SCALE))),
        },
        "binary": {
            "n": s["kappa_bin"]["n"],
            "both_positive": bt.get((True, True), 0), "a_only": bt.get((True, False), 0),
            "b_only": bt.get((False, True), 0), "both_negative": bt.get((False, False), 0),
            "agreement": s["kappa_bin"]["p_o"], "kappa": s["kappa_bin"]["kappa"],
            "prevalence": prev, "ac1": s["ac1"]["ac1"],
            "show_ac1": prev is not None and (prev < AC1_LOW or prev > AC1_HIGH),
        },
        "direct_pairs": direct_pairs,
        "ci": ci,
        "n_blocks": len(blist),
        "disagreements": disagreements,
    }


def _case(cid: str, ra: Mapping, chat_times, va: str, vb: str) -> dict:
    t = (chat_times or {}).get(cid)
    if not _is_int(t):
        t = ra.get("first_visible_ms") if _is_int(ra.get("first_visible_ms")) else None
    return {"chat_id": cid, "t_ms": t, "a": va, "b": vb}


# ----------------------------------------------------------------------------- 공통: 레코드·범위
def _eid(rec: Mapping) -> str | None:
    """레코드의 entity_id (레코드 dict 에만 있다. body 만 받으면 None)."""
    v = rec.get("entity_id") if isinstance(rec, Mapping) else None
    return v if isinstance(v, str) and v else None


def _chat_t(cid: str, body: Mapping, chat_times: Mapping[str, int] | None) -> int | None:
    """채팅 시각: chat_times 에 있으면 그것, 없으면 그 라벨의 first_visible_ms. 모르면 None."""
    t = (chat_times or {}).get(cid)
    if _is_int(t):
        return t
    t = body.get("first_visible_ms") if isinstance(body, Mapping) else None
    return t if _is_int(t) else None


def _norm_scope(scope: Sequence | None) -> list[tuple[int, int]] | None:
    if scope is None:
        return None
    return [(int(a), int(b)) for a, b in scope]


def _in_scope(t: int | None, scope: list[tuple[int, int]] | None) -> bool:
    """범위가 없거나 시각을 모르면 True(뺄 수 없어 남긴다 — compare_samples 와 같은 규칙)."""
    if scope is None or t is None:
        return True
    return any(lo <= t < hi for lo, hi in scope)


def _by_chat_records(records: Iterable[Mapping], keep=None) -> tuple[dict[str, Mapping], int]:
    """chat_id → 레코드(또는 body). 같은 chat_id 가 둘 이상이면 그 chat_id 는 빼고 센다. keep(body) 가 거짓이면 건너뜀."""
    seen: dict[str, Mapping] = {}
    dup: set[str] = set()
    for r in records:
        b = _body(r)
        cid = b.get("chat_id")
        if not isinstance(cid, str) or not cid:
            continue
        if keep is not None and not keep(b):
            continue
        if cid in seen:
            dup.add(cid)
        else:
            seen[cid] = r
    for c in dup:
        seen.pop(c, None)
    return seen, len(dup)


# ----------------------------------------------------------------------------- 표본에서 빠진 판정 (D-12 2)
def sample_regions(segments: Iterable[Mapping], *, scope: Sequence | None = None,
                   buffer_ms: int = DEFAULT_BUFFER_MS) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """세션의 현재 구간표 → (저스트채팅 구간 [(시작, 끝)], 경계 완충 [(시작, 끝)]). 표본 적격 자리(06 2-2 의 1·2번)를 다시 본다.

    완충은 core.segments.record_buffers 의 v2 규칙(분류가 바뀌는 경계만, sha256-tau-v2).
    - scope 가 있으면(재라벨 블록) 블록마다 그 안의 구간만으로 계산한다 — 블록 가장자리는 경계가 아니다(06 3-1,
      작업대 relabel_buffers 와 같은 방법).
    - scope 가 없으면 구간들의 가장 늦은 끝을 영상 끝으로 보고 계산한다(보고서는 영상을 열지 않는다).
    영상 밖(D-10 3)은 보지 않는다 — 판정이 있었다면 그때 영상 안이었고, 구간을 고쳐도 바뀌지 않는다.
    """
    segs = []
    for r in segments or ():
        b = _body(r)
        s, e, c = b.get("start_ms"), b.get("end_ms"), b.get("category")
        if _is_int(s) and _is_int(e) and e > s:
            segs.append({"start_ms": s, "end_ms": e, "category": c})
    jc = sorted((s["start_ms"], s["end_ms"]) for s in segs if s["category"] == JUST_CHATTING)
    sc = _norm_scope(scope)
    bufs: list[tuple[int, int]] = []
    if sc is None:
        dur = max((s["end_ms"] for s in segs), default=0)
        bufs = list(record_buffers(segs, dur, buffer_ms))
    else:
        for lo, hi in sc:
            inside = [{"start_ms": s["start_ms"] - lo, "end_ms": s["end_ms"] - lo, "category": s["category"]}
                      for s in segs if lo <= s["start_ms"] and s["end_ms"] <= hi]
            bufs += [(a + lo, z + lo) for a, z in record_buffers(inside, hi - lo, buffer_ms)]
    return jc, sorted(bufs)


def split_current_sample(reactions: Iterable[Mapping], missed: Iterable[Mapping], segments: Iterable[Mapping], *,
                         scope: Sequence | None = None, buffer_ms: int = DEFAULT_BUFFER_MS,
                         chat_times: Mapping[str, int] | None = None,
                         current_sample: Iterable[str] | None = None) -> dict:
    """표본에서 빠진 판정을 가른다 (D-12 2: 판정은 남기고 일치도에서는 현재 표본 안만 센다, 빠진 수는 1줄).

    - 대상: entry = from_sample 인 반응과 scope = sample 인 놓친 후보. ② 라벨(from_utterance)은 ② 라벨로 남는다(03 6-13).
    - current_sample(chat_id 목록)을 주면 그 안인지로 가른다. 없으면 sample_regions 로 다시 본다: 채팅 시각이 저스트채팅 구간
      안이고 경계 완충 밖이어야 '현재 표본 자리'. 구간표가 비었거나 시각을 모르면 가를 수 없어 남긴다.
    결과 {reactions, missed, dropped_reactions, dropped_missed, method}. 입력 dict 는 고치지 않는다(같은 dict 를 가리킨다).
    """
    reactions, missed, segments = list(reactions or ()), list(missed or ()), list(segments or ())
    if current_sample is not None:
        cur = set(current_sample)

        def in_sample(cid: str, b: Mapping) -> bool:
            return cid in cur
        method = "given"
    elif any(_is_int(_body(s).get("start_ms")) for s in segments):
        jc, bufs = sample_regions(segments, scope=scope, buffer_ms=buffer_ms)

        def in_sample(cid: str, b: Mapping) -> bool:
            t = _chat_t(cid, b, chat_times)
            if t is None:
                return True
            return any(a <= t < z for a, z in jc) and not any(a <= t < z for a, z in bufs)
        method = "segments"
    else:
        return {"reactions": reactions, "missed": missed, "dropped_reactions": 0, "dropped_missed": 0, "method": "none"}
    kr, km = [], []
    dr = dm = 0
    for r in reactions:
        b = _body(r)
        cid = b.get("chat_id")
        if b.get("entry") == "from_sample" and isinstance(cid, str) and not in_sample(cid, b):
            dr += 1
            continue
        kr.append(r)
    for r in missed:
        b = _body(r)
        cid = b.get("chat_id")
        if b.get("scope") == "sample" and isinstance(cid, str) and not in_sample(cid, b):
            dm += 1
            continue
        km.append(r)
    return {"reactions": kr, "missed": km, "dropped_reactions": dr, "dropped_missed": dm, "method": method}


def drop_missed_not_target(missed: Iterable[Mapping], reactions: Iterable[Mapping]) -> tuple[list, int]:
    """④ 를 매긴 뒤 ③ 판정을 직접·높음으로 고쳐 06 2-9 대상(반응 미확인·관련성만 있음·불명확)이 아니게 된 채팅의
    놓친 후보(scope=sample)를 일치도에서 뺀다. 기록은 남긴다(빼는 수만 센다).

    reactions = 그 세션의 현재 유효 반응 전부(표본 자르기 전). 그 채팅의 유효 반응이 꼭 하나 있고 그것이 대상이 아닐 때만
    뺀다 — 반응이 없거나 둘 이상이면 가를 수 없어 남긴다. 결과 (남긴 목록, 뺀 수)."""
    from .missed import target_state
    by: dict[str, list] = {}
    for r in reactions or ():
        cid = _body(r).get("chat_id")
        if isinstance(cid, str):
            by.setdefault(cid, []).append(r)
    keep: list = []
    n = 0
    for m in missed or ():
        b = _body(m)
        cid = b.get("chat_id")
        rs = by.get(cid) if isinstance(cid, str) else None
        if b.get("scope") == "sample" and rs and len(rs) == 1 and target_state(_body(rs[0])) is None:
            n += 1
            continue
        keep.append(m)
    return keep, n


def drop_missed_stale(missed: Iterable[Mapping], reactions: Iterable[Mapping]) -> tuple[list, dict]:
    """놓친 후보(scope=sample) 가운데 지금 ③ 판정으로 보면 06 2-9 대상이 아닌 것을 일치도에서 뺀다 (14 4-2 의 4번, 08 D-13 2).

    - not_target: 그 채팅의 유효 반응이 꼭 하나이고 그것이 대상(미확인·관련성만·불명확)이 아니다(③ 을 직접·높음으로 고침).
    - no_reaction: 그 채팅의 유효 반응이 없다(③ 을 물렀음 — ④ 는 ③ 이 대상일 때만 생기므로 근거가 없어졌다).
    유효 반응이 둘 이상이면 가를 수 없어 남긴다. extra(표본 밖 후보)는 ③ 과 상관없이 남긴다. 기록은 남기고 수만 센다.
    reactions = 그 세션의 현재 유효 반응 전부(표본 자르기 전). 결과 (남긴 목록, {not_target, no_reaction}).
    drop_missed_not_target 은 앞 판 동작(반응 없음은 남김) 그대로 둔다(하위 호환).
    """
    from .missed import target_state
    by: dict[str, list] = {}
    for r in reactions or ():
        cid = _body(r).get("chat_id")
        if isinstance(cid, str):
            by.setdefault(cid, []).append(r)
    keep: list = []
    cnt = {"not_target": 0, "no_reaction": 0}
    for m in missed or ():
        b = _body(m)
        cid = b.get("chat_id")
        if b.get("scope") == "sample" and isinstance(cid, str):
            rs = by.get(cid)
            if not rs:
                cnt["no_reaction"] += 1
                continue
            if len(rs) == 1 and target_state(_body(rs[0])) is None:
                cnt["not_target"] += 1
                continue
        keep.append(m)
    return keep, cnt


# 놓친 후보(④)가 있는 세션 종류 — 재라벨 세션만(08 D-12 5). 1차는 ④ 를 하지 않는다(D-13 4).
MISSED_KINDS = frozenset({"relabel_same", "relabel_second"})
MISSED_PRIMARY_NOTE = "1차는 ④ 없음(D-12 5)"


def missed_pair_applicable(kind_a: str | None, kind_b: str | None) -> bool:
    """놓친 후보 일치도를 낼 짝인가 (08 D-13 3). 한쪽이라도 1차면 아니다(1차는 ④ 가 없어 늘 '한쪽만').
    두 쪽 다 재라벨이면 낸다(같은 블록 = 두 세션 범위의 교집합, build_agreement 의 sample_scope). 종류를 모르면(시험 입력)
    앞 판처럼 낸다."""
    return "primary" not in (kind_a, kind_b)


# ----------------------------------------------------------------------------- 연결 대상 · 반응 시각 (06 4-0 · 4-3)
def _links(reactions: Iterable[Mapping], utterances: Iterable[Mapping], scope, chat_times) -> tuple[dict, dict]:
    """chat_id → {utt_start, rt}. 연결 = linked_utterance_id 가 있는 현재 유효 반응(직접·높음). 발화를 못 찾으면 broken."""
    ustart: dict[str, int] = {}
    for u in utterances or ():
        e = _eid(u)
        s = _body(u).get("start_ms")
        if e and _is_int(s):
            ustart[e] = s
    out: dict[str, dict] = {}
    cnt = {"broken": 0, "duplicate_chat": 0, "outside_scope": 0}
    by, dup = _by_chat_records(reactions)
    cnt["duplicate_chat"] = dup
    for cid, r in by.items():
        b = _body(r)
        uid = b.get("linked_utterance_id")
        if not isinstance(uid, str) or not uid:
            continue
        if not _in_scope(_chat_t(cid, b, chat_times), scope):
            cnt["outside_scope"] += 1
            continue
        if uid not in ustart:
            cnt["broken"] += 1
            continue
        rt = b.get("reaction_time_ms")
        out[cid] = {"utt_start": ustart[uid], "rt": rt if _is_int(rt) else None}
    return out, cnt


def compare_links(reactions_a: Iterable[Mapping], reactions_b: Iterable[Mapping],
                  utterances_a: Iterable[Mapping], utterances_b: Iterable[Mapping], *,
                  settings: Mapping | None = None, scope: Sequence | None = None,
                  chat_times: Mapping[str, int] | None = None) -> dict:
    """연결 대상 일치 (06 4-0: (chat_id, 발화) 단위, 같은 chat_id 안에서 발화 시작 차이 ≤ ±5초면 짝).

    반응 1건은 연결 발화가 하나라 chat_id 마다 연결은 많아야 1개다. 그래서 '차이가 작은 쌍부터' 는 같은 chat_id 의 두 연결이
    창 안인지 보는 것과 같다. 창 밖이면 두 연결 모두 짝 없음(far 로도 센다).
    연결 F1 = 2 × 짝 ÷ (A 연결 수 + B 연결 수) (4-3, 사람끼리는 어느 쪽도 정답이 아니라 대칭).
    반응 시각: 짝지은 연결의 reaction_time_ms |Δt| 중앙값·90분위·±2초 안 비율.
    입력 반응·발화는 레코드 dict(entity_id 필요). 발화 entity_id 는 세션마다 달라 시작 시각으로 맞춘다.
    """
    st = merge_settings(settings)
    ag = st["agreement"]
    tol = int(round(float(ag["utterance_match_tolerance_sec"]) * 1000))
    rtol = int(round(float(ag["reaction_time_tolerance_sec"]) * 1000))
    sc = _norm_scope(scope)
    la, ca = _links(reactions_a, utterances_a, sc, chat_times)
    lb, cb = _links(reactions_b, utterances_b, sc, chat_times)
    matched = []
    far = 0
    for cid in sorted(set(la) & set(lb)):
        d = abs(la[cid]["utt_start"] - lb[cid]["utt_start"])
        if d <= tol:
            matched.append(cid)
        else:
            far += 1
    m = len(matched)
    na, nb = len(la), len(lb)
    rts = [abs(la[c]["rt"] - lb[c]["rt"]) for c in matched if la[c]["rt"] is not None and lb[c]["rt"] is not None]
    utt = [abs(la[c]["utt_start"] - lb[c]["utt_start"]) for c in matched]
    mn = int(ag["min_n_for_metric"])
    return {
        "a_links": na, "b_links": nb, "matched": m, "a_only": na - m, "b_only": nb - m,
        "same_chat_far": far,
        "f1": (2 * m / (na + nb)) if (na + nb) else None,
        "tolerance_sec": tol / 1000,
        "utterance_start_median_sec": _median_sec(utt),
        "reaction_time": {
            "n": len(rts),
            "median_sec": _median_sec(rts),
            "p90_sec": None if not rts else percentile(rts, 0.9) / 1000,
            "within_tolerance_ratio": (sum(1 for x in rts if x <= rtol) / len(rts)) if rts else None,
            "tolerance_sec": rtol / 1000,
        },
        "problems": {"a": ca, "b": cb},
        "low_n": (na + nb) < mn,
    }


# ----------------------------------------------------------------------------- 이어짐 (06 4-5)
CONTINUATION_TYPES = ("none", "short", "extended", "new_topic")
MISSING = "missing"


def _cont_map(continuations: Iterable[Mapping]) -> tuple[dict[str, Mapping], int]:
    """reaction_entity_id → 이어짐 body. 한 반응에 유효 이어짐이 둘 이상이면 빼고 센다."""
    seen: dict[str, Mapping] = {}
    dup: set[str] = set()
    for r in continuations or ():
        b = _body(r)
        rid = b.get("reaction_entity_id")
        if not isinstance(rid, str):
            continue
        if rid in seen:
            dup.add(rid)
        else:
            seen[rid] = b
    for k in dup:
        seen.pop(k, None)
    return seen, len(dup)


def _span_ms(b: Mapping) -> int | None:
    s, e = b.get("span_start_ms"), b.get("span_end_ms")
    return (e - s) if _is_int(s) and _is_int(e) and e >= s else None


def compare_continuations(reactions_a: Iterable[Mapping], reactions_b: Iterable[Mapping],
                          continuations_a: Iterable[Mapping], continuations_b: Iterable[Mapping], *,
                          settings: Mapping | None = None, scope: Sequence | None = None,
                          chat_times: Mapping[str, int] | None = None) -> dict:
    """이어짐 일치 (06 4-5). 같은 chat_id 반응 짝 가운데 둘 다 직접·높음(판단 불가 아님)인 것만 비교한다.

    - 이어짐 4값은 명목 척도 κ(Cohen)와 4×4 혼동표. 한쪽이라도 비운 것(D-10 7 선택)은 'missing' 으로 따로 센다.
    - 이어짐 확신도가 판단 불가면 κ 에서 빼고 따로 센다(06 4-0).
    - 원시값: 둘 다 span 이 있는 짝의 이어진 길이 차이 중앙값(초), 다른 시청자 채팅 수(related_chat_ids) 차이 중앙값,
      '잘림'(truncated_at_segment_end) 일치율.
    반응은 레코드 dict(entity_id 로 이어짐을 찾는다)여야 한다. body 만 주면 이어짐을 찾지 못해 missing 으로 센다.
    """
    st = merge_settings(settings)
    sc = _norm_scope(scope)
    ma, _ = _by_chat_records(reactions_a)
    mb, _ = _by_chat_records(reactions_b)
    conts_a, dup_a = _cont_map(continuations_a)
    conts_b, dup_b = _cont_map(continuations_b)
    table: Counter = Counter()
    missing = {"both": 0, "a_only": 0, "b_only": 0}
    und = {"both": 0, "a_only": 0, "b_only": 0}
    span_d: list[int] = []
    rel_d: list[int] = []
    trunc_same = trunc_n = 0
    pairs = 0
    disagreements = []
    for cid in sorted(set(ma) & set(mb)):
        ra, rb = ma[cid], mb[cid]
        ba, bb = _body(ra), _body(rb)
        if reaction_value(ba) not in POSITIVE or reaction_value(bb) not in POSITIVE:
            continue
        if not _in_scope(_chat_t(cid, ba, chat_times), sc) or not _in_scope(_chat_t(cid, bb, chat_times), sc):
            continue
        pairs += 1
        ca = conts_a.get(_eid(ra) or "")
        cb = conts_b.get(_eid(rb) or "")
        ta = ca.get("type") if ca and ca.get("type") in CONTINUATION_TYPES else None
        tb = cb.get("type") if cb and cb.get("type") in CONTINUATION_TYPES else None
        if ta is None or tb is None:
            missing["both" if ta is None and tb is None else "a_only" if ta is None else "b_only"] += 1
            continue
        ua, ub = ca.get("confidence") == UNDETERMINABLE, cb.get("confidence") == UNDETERMINABLE
        if ua or ub:
            und["both" if ua and ub else "a_only" if ua else "b_only"] += 1
            continue
        table[(ta, tb)] += 1
        if ta != tb:
            disagreements.append({"chat_id": cid, "t_ms": _chat_t(cid, ba, chat_times), "a": ta, "b": tb})
        sa, sb = _span_ms(ca), _span_ms(cb)
        if ta != "none" and tb != "none" and sa is not None and sb is not None:
            span_d.append(abs(sa - sb))
        la, lb = ca.get("related_chat_ids"), cb.get("related_chat_ids")
        if isinstance(la, list) and isinstance(lb, list):
            rel_d.append(abs(len(la) - len(lb)))
        xa, xb = ca.get("truncated_at_segment_end"), cb.get("truncated_at_segment_end")
        if isinstance(xa, bool) and isinstance(xb, bool):
            trunc_n += 1
            trunc_same += int(xa == xb)
    k = kappa_from_table(table, CONTINUATION_TYPES)
    rel = percentile(rel_d, 0.5)
    return {
        "pairs_positive_both": pairs,
        "n": k["n"],
        "agreement": k["p_o"], "kappa": k["kappa"], "p_e": k["p_e"],
        "confusion": _confusion(table, CONTINUATION_TYPES),
        "missing": missing,
        "undeterminable": und,
        "duplicate_continuation": {"a": dup_a, "b": dup_b},
        "raw": {
            "span_n": len(span_d), "span_diff_median_sec": _median_sec(span_d),
            "related_n": len(rel_d), "related_count_diff_median": rel,
            "truncated_n": trunc_n, "truncated_agreement": (trunc_same / trunc_n) if trunc_n else None,
        },
        "disagreements": disagreements,
        "low_n": k["n"] < int(st["agreement"]["min_n_for_metric"]),
    }


# ----------------------------------------------------------------------------- 놓친 채팅 후보 (06 4-6, 2-9)
REASON_CODES = ("A", "B", "C", "D", "E", "F", "G")
HUMAN_REASON_CODES = ("B", "C", "D", "E", "G")     # 사람이 고르는 코드 (06 4-6). A·F 는 자동이라 일치도를 부풀린다
AUTO_REASON_CODES = frozenset({"A", "F"})


def _cond(b: Mapping, key: str):
    v = b.get(key)
    if isinstance(v, Mapping):
        v = v.get("value")
    return v if isinstance(v, bool) else None


def compare_missed(missed_a: Iterable[Mapping], missed_b: Iterable[Mapping], *, settings: Mapping | None = None,
                   scope: Sequence | None = None, chat_times: Mapping[str, int] | None = None) -> dict:
    """놓친 채팅 후보 일치 (06 4-6). 대상은 두 세션 모두 scope = sample(2-9 의 체계적 대상)로 판정한 같은 chat_id.

    - 후보 여부(is_candidate) 2×2: 일치율·κ·Gwet AC1·유병률. 후보 비율이 낮을 것이라 AC1 을 늘 같이 보인다.
    - 조건 ②③④ 각각의 일치율(둘 다 값이 있는 짝만). ① 은 자동 계산이라 뺀다.
    - 사유 코드: 둘 다 '후보 아님' 인 짝 가운데 두 코드가 모두 B·C·D·E·G 이고 어느 쪽도 reason_code_source = auto 가 아닌 것만
      명목 κ·5×5 혼동표. 자동 A·F(또는 source auto)가 낀 짝은 건수만(auto_excluded).
    - extra(표본 밖에서 따로 찾은 후보)는 비교하지 않고 센다.
    - scope(두 세션 범위의 교집합)를 주면 그 밖 채팅은 빼고 센다 — 놓친 후보는 재라벨 블록 안에서만 비교한다(D-12 5).
    """
    st = merge_settings(settings)
    sc = _norm_scope(scope)

    def sample_only(b: Mapping) -> bool:
        return b.get("scope") == "sample"
    extra = {"a": 0, "b": 0}
    outside = {"a": 0, "b": 0}
    maps = []
    dups = []
    for side, recs in (("a", missed_a), ("b", missed_b)):
        recs = list(recs or ())
        extra[side] = sum(1 for r in recs if _body(r).get("scope") == "extra")
        m, d = _by_chat_records(recs, keep=sample_only)
        n0 = len(m)
        m = {c: r for c, r in m.items() if _in_scope(_chat_t(c, _body(r), chat_times), sc)}
        outside[side] = n0 - len(m)
        maps.append(m)
        dups.append(d)
    ma, mb = maps
    both = sorted(set(ma) & set(mb))
    cand: Counter = Counter()
    conds = {k: {"n": 0, "same": 0} for k in ("cond2", "cond3", "cond4")}
    reason: Counter = Counter()
    auto_excl = 0
    missing_code = 0
    disagreements = []
    for cid in both:
        ba, bb = _body(ma[cid]), _body(mb[cid])
        xa, xb = ba.get("is_candidate"), bb.get("is_candidate")
        if not isinstance(xa, bool) or not isinstance(xb, bool):
            continue
        cand[(xa, xb)] += 1
        for k in conds:
            va, vb = _cond(ba, k), _cond(bb, k)
            if va is not None and vb is not None:
                conds[k]["n"] += 1
                conds[k]["same"] += int(va == vb)
        if xa != xb:
            disagreements.append({"chat_id": cid, "t_ms": _chat_t(cid, ba, chat_times),
                                  "a": "candidate" if xa else ba.get("reason_code"),
                                  "b": "candidate" if xb else bb.get("reason_code")})
        if xa or xb:
            continue
        code_a, code_b = ba.get("reason_code"), bb.get("reason_code")
        if code_a not in REASON_CODES or code_b not in REASON_CODES:
            missing_code += 1
            continue
        if (code_a in AUTO_REASON_CODES or code_b in AUTO_REASON_CODES
                or ba.get("reason_code_source") == "auto" or bb.get("reason_code_source") == "auto"):
            auto_excl += 1
            continue
        reason[(code_a, code_b)] += 1
        if code_a != code_b:
            disagreements.append({"chat_id": cid, "t_ms": _chat_t(cid, ba, chat_times), "a": code_a, "b": code_b})
    kc = kappa_from_table(cand, (True, False))
    ac = ac1_binary(cand)
    kr = kappa_from_table(reason, HUMAN_REASON_CODES)
    mn = int(st["agreement"]["min_n_for_metric"])
    disagreements.sort(key=lambda d: (d["t_ms"] if d["t_ms"] is not None else -1, d["chat_id"]))
    return {
        "judged": {"a": len(ma), "b": len(mb), "both": len(both), "a_only": len(set(ma) - set(mb)),
                   "b_only": len(set(mb) - set(ma)), "extra_a": extra["a"], "extra_b": extra["b"],
                   "outside_scope_a": outside["a"], "outside_scope_b": outside["b"],
                   "duplicate_chat_a": dups[0], "duplicate_chat_b": dups[1]},
        "candidate": {
            "n": kc["n"],
            "both_candidate": cand.get((True, True), 0), "a_only": cand.get((True, False), 0),
            "b_only": cand.get((False, True), 0), "both_not": cand.get((False, False), 0),
            "agreement": kc["p_o"], "kappa": kc["kappa"], "prevalence": ac["prevalence"], "ac1": ac["ac1"],
            "low_n": kc["n"] < mn,
        },
        "conditions": {k: {"n": v["n"], "agreement": (v["same"] / v["n"]) if v["n"] else None} for k, v in conds.items()},
        "reason_code": {
            "n": kr["n"], "agreement": kr["p_o"], "kappa": kr["kappa"], "p_e": kr["p_e"],
            "confusion": _confusion(reason, HUMAN_REASON_CODES),
            "auto_excluded": auto_excl, "missing_code": missing_code,
            "low_n": kr["n"] < mn,
        },
        "disagreements": disagreements,
    }


# ----------------------------------------------------------------------------- 출처 (06 4-7, D-12 6)
ORIGIN_VALUES = ("real", "virtual", "undeterminable")
AUTO_ORIGIN_METHODS = frozenset({"generation_log_match", "viewer_id_match"})   # 기록 대조로 정한 것 = '자동'


def compare_origins(origins_a: Iterable[Mapping], origins_b: Iterable[Mapping], *,
                    settings: Mapping | None = None) -> dict:
    """출처 판정 일치 (06 4-7). 입력은 출처 판정 라벨(03 6-11 origin body: chat_id·origin·method …)의 레코드 또는 body.

    - 같은 chat_id 짝. 실제·가상·판별 불가 3×3 혼동표(행 A, 열 B)와 명목 κ(판별 불가도 한 값).
    - 한쪽이라도 method 가 자동(생성 기록 대조·봇 가명 대조)이면 '자동' 짝으로 센다. 자동 결정은 표시만 한다(D-12 6):
      전체 κ 와 함께 사람이 정한 짝끼리의 κ(human)를 따로 낸다.
    - 출처 값이 셋 밖이면 invalid 로 센다.
    계산은 app.core.origin.compare_origins 하나다(통합에서 합침, strict=False: 저장된 라벨의 출처 값만 본다).
    이 함수는 그 결과를 보고서 모양(judged·n·agreement·kappa·human·auto_pairs)으로 바꾸기만 한다. origin 은 이
    모듈의 kappa_from_table 을 쓰므로 맨 위에서 import 하면 순환이 된다 — 함수 안에서 늦게 import 한다.
    """
    from . import origin as og          # 순환 import 를 피해 늦게 (origin → agreement.kappa_from_table)
    st = merge_settings(settings)
    r = og.compare_origins(list(origins_a or ()), list(origins_b or ()), strict=False)
    both = r["pairs"] + r["invalid_pairs"]
    mn = int(st["agreement"]["min_n_for_metric"])
    al, hu = r["all"], r["human"]
    return {
        "compared": r["labels_a"] > 0 and r["labels_b"] > 0,
        "judged": {"a": r["labels_a"], "b": r["labels_b"], "both": both, "a_only": r["labels_a"] - both,
                   "b_only": r["labels_b"] - both, "duplicate_chat_a": r["duplicate_a"],
                   "duplicate_chat_b": r["duplicate_b"], "invalid_pairs": r["invalid_pairs"]},
        "n": al["n"], "agreement": al["p_o"], "kappa": al["kappa"], "p_e": al["p_e"],
        "confusion": _confusion(_table_of(al["confusion"]), ORIGIN_VALUES),
        "auto_pairs": r["auto"]["n"],
        "human": {"n": hu["n"], "agreement": hu["p_o"], "kappa": hu["kappa"],
                  "confusion": _confusion(_table_of(hu["confusion"]), ORIGIN_VALUES), "low_n": hu["n"] < mn},
        "auto_methods": sorted(AUTO_ORIGIN_METHODS),
        "undeterminable": dict(r["undeterminable"]),
        "disagreements": [dict(d) for d in r["disagreements"]],
        "low_n": al["n"] < mn,
    }


def _table_of(conf: Mapping) -> dict[tuple, int]:
    """origin.confusion_from_pairs 모양({labels, rows}) → {(A, B): 건수}."""
    labs = list(conf["labels"])
    return {(labs[i], labs[j]): conf["rows"][i][j] for i in range(len(labs)) for j in range(len(labs))}


# ----------------------------------------------------------------------------- 판정과 해석 (5장)
def gate_verdict(value: float | None, ci: Mapping | None, gate: float, *, n: int, min_n: int,
                 higher_is_better: bool = True) -> str:
    """기준 판정 (06 5장). 점추정으로 판단하고, 신뢰구간 경계가 기준 밖이면 '불확실'. 건수 < min_n 이면 판단하지 않음."""
    if value is None:
        return V_NA
    if n < min_n:
        return V_LOW_N
    ok = value >= gate if higher_is_better else value <= gate
    if not ok:
        return V_FAIL
    if not ci or ci.get("status") != "ok":
        return V_PASS_POINT
    bound = ci["lo"] if higher_is_better else ci["hi"]
    if (higher_is_better and bound < gate) or (not higher_is_better and bound > gate):
        return V_UNCERTAIN
    return V_PASS


def gates(segment: Mapping, sample: Mapping, settings: Mapping) -> list[dict]:
    """5장 판단 기준표 네 줄."""
    g = settings["gate"]
    mn = int(settings["agreement"]["min_n_for_metric"])
    rows = [
        ("segment_kappa", "활동 구간", "κ (1초 칸)", segment.get("kappa"), segment.get("ci", {}).get("kappa"),
         float(g["segment_kappa"]), segment.get("cells_compared", 0), True, "≥"),
        ("binary_kappa", "반응 있음", "이진 κ", sample.get("binary", {}).get("kappa"),
         sample.get("ci", {}).get("binary_kappa"), float(g["binary_kappa"]), sample.get("binary", {}).get("n", 0), True, "≥"),
        ("grade_kappa_w", "반응 등급", "선형 가중 κ", sample.get("grade", {}).get("kappa_w"),
         sample.get("ci", {}).get("grade_kappa_w"), float(g["grade_kappa_w"]), sample.get("grade", {}).get("n", 0), True, "≥"),
        ("boundary_median_sec", "구간 경계", "|Δt| 중앙값(초)", segment.get("boundaries", {}).get("median_sec"),
         segment.get("ci", {}).get("boundary_median_sec"), float(g["boundary_median_sec"]),
         segment.get("boundaries", {}).get("matched", 0), False, "≤"),
    ]
    out = []
    for key, kind, metric, val, ci, gate, n, hib, op in rows:
        v = gate_verdict(val, ci, gate, n=n, min_n=mn, higher_is_better=hib)
        out.append({"key": key, "label_kind": kind, "metric": metric, "value": val, "gate": gate, "op": op, "n": n,
                    "ci": None if not ci or ci.get("status") != "ok" else [ci["lo"], ci["hi"]],
                    "ci_status": (ci or {}).get("status"), "verdict": v, "verdict_ko": VERDICT_KO[v]})
    return out


def comparison_type(a: Mapping, b: Mapping) -> str:
    """같은 labeler_id 면 intra(1인 재라벨), 다르면 inter(2인) (06 5장: 명부의 labeler_id 로 구분)."""
    return "intra" if a.get("labeler_id") == b.get("labeler_id") else "inter"


def interpret(ctype: str, gate_rows: Sequence[Mapping], header: Mapping, settings: Mapping,
              sample: Mapping | None = None, origin: Mapping | None = None) -> list[str]:
    """06 5장의 해석 문구. 사람이 읽는 줄 목록(본문·닉네임 없음)."""
    lines: list[str] = []
    verdicts = {r["key"]: r["verdict"] for r in gate_rows}
    judged = [v for v in verdicts.values() if v not in (V_LOW_N, V_NA)]
    failed = [r for r in gate_rows if r["verdict"] == V_FAIL]
    if header.get("same_machine"):
        lines.append("두 세션이 같은 machine_id 에서 매겨졌다. 기능 시험용이며 실제 일치도로 쓰면 안 된다 (08 D-11 6).")
    if header.get("adjudicator_is_labeler"):
        lines.append("조정자 = 라벨러: 1인 작업이라 라벨러가 조정을 겸한다. 불일치 원인 판단에 본인 판정 쪽 쏠림이 있을 수 있다 (08 D-12 3).")
    if not header.get("representative", True):
        if header.get("view_exposed_sessions", True):
            lines.append("노출 기록(view.exposed 또는 분석 세션 열람)이 있는 세션이 있어 일치도 대표값으로 쓰지 않고 따로 보고한다 (06 3-4).")
        if header.get("cancelled_plan_sessions"):
            lines.append("재라벨 계획이 취소된 세션이 있어 일치도 대표값으로 쓰지 않고 따로 보고한다 (06 7-1, 08 D-12 7).")
    if ctype == "intra":
        lines.append("같은 사람 재라벨(intra-rater): 한 사람이 기준을 일관되게 쓰는지를 잰다. 높아도 기준이 명확하다는 증거는 "
                     "아니고, 다른 사람과의 일치의 '상한' 으로만 읽는다.")
        if failed:
            lines.append("기준에 못 미친 지표가 있다: " + ", ".join(r["label_kind"] for r in failed)
                         + ". 기준이 흐리거나 라벨러가 흔들린다. 다른 사람과는 더 낮을 것이므로 멈추고 기준서를 고친다.")
        elif judged:
            lines.append("판단한 지표가 기준을 넘었지만 intra 만 있으므로 '잠정 (intra 만)' 이다. 엄격 점수도 잠정으로 낸다.")
    else:
        lines.append("다른 사람(inter-rater): 기준서가 사람을 바꿔도 같은 결과를 내는지를 잰다.")
        if failed:
            lines.append("기준에 못 미친 지표가 있다: " + ", ".join(r["label_kind"] for r in failed)
                         + ". 불일치 사례를 기준 흐림 · 단순 실수 · 진짜 모호로 나눠 본다.")
        elif judged:
            lines.append("판단한 지표가 기준을 넘었다: 기준이 전달된다.")
        lines.append("검토자 확인: 두 labeler_id 가 실제로 다른 사람인지 확인한다(예시 ID 를 같이 쓰는 실수 대비, 06 5장).")
    unc = [r for r in gate_rows if r["verdict"] == V_UNCERTAIN]
    if unc:
        lines.append("신뢰구간 경계가 기준 밖이라 '불확실' 을 붙였다: " + ", ".join(r["label_kind"] for r in unc) + ".")
    low = [r for r in gate_rows if r["verdict"] == V_LOW_N]
    if low:
        lines.append(f"비교 건수가 {settings['agreement']['min_n_for_metric']} 미만인 지표는 흐리게 보이고 판단에 쓰지 않았다: "
                     + ", ".join(r["label_kind"] for r in low) + ".")
    if any(r["ci_status"] == "too_few_blocks" for r in gate_rows):
        lines.append(f"블록이 {settings['agreement']['min_blocks_for_ci']}개 미만인 지표는 신뢰구간을 내지 않았다 (블록 부족 — 점추정만).")
    if sample is not None and sample.get("grade", {}).get("n", 0) and sample.get("direct_pairs", 0) < FEW_DIRECT_WARN:
        lines.append(f"직접 반응이 들어간 짝이 {sample.get('direct_pairs', 0)}건이다. 이 정도에서는 κ 신뢰구간이 넓으니 결론을 미룬다 (06 4-8).")
    if header.get("engine_author_included"):
        lines.append("라벨러 가운데 엔진 작성자가 있다. 엔진 규칙을 아는 사람의 라벨에는 블라인드로도 막을 수 없는 쏠림이 있다 (06 5장).")
    if origin is not None and origin.get("compared") and origin.get("kappa") is not None:
        lines.append("출처 일치도가 낮으면 라벨러 문제보다 생성 기록·OCR 정렬 문제일 가능성이 크다 (06 4-7). "
                     f"자동(기록 대조)으로 정한 짝 {origin.get('auto_pairs', 0)}건은 '자동' 으로 따로 셌다 (08 D-12 6).")
    lines.append("층(무작위·어려움) 정보는 아직 읽지 않는다. 이 판은 비교한 범위 전체를 하나로 보고 판단했다 (06 3-2 는 무작위 층으로만 판단).")
    return lines


# ----------------------------------------------------------------------------- 묶음
def build_agreement(side_a: Mapping, side_b: Mapping, *, settings: Mapping | None = None,
                    buffer_ms: int = DEFAULT_BUFFER_MS, seed_material: str = "",
                    sample_chat_ids: Iterable[str] | None = None, chat_times: Mapping[str, int] | None = None,
                    adjudicator_is_labeler: bool | None = None) -> dict:
    """두 세션 → 일치도 결과(보고서 본체). 잠금 확인·파일 읽기는 app 층이 하고, 여기서는 계산만 한다.

    side: {session_id, labeler_id, session_kind, machine_id, scope(None 또는 [(시작, 끝)]), segments[], reactions[],
           view_exposed(목록), analysis_exposed(bool), engine_author(bool)}. segments·reactions 는 현재 유효 라벨.
    선택 칸(다섯째 묶음): utterances[], continuations[], missed[](missed_candidate), origins[](origin),
           current_sample(chat_id 목록 — 주면 D-12 2 의 '현재 표본' 을 구간표 대신 이것으로 본다),
           plan_cancelled(bool — 취소된 재라벨 계획으로 만든 세션이면 대표값에서 뺀다, 06 7-1).
    adjudicator_is_labeler: 보고서 머리의 '조정자 = 라벨러' (D-12 3). None 이면 표시하지 않음(모름).
    """
    st = merge_settings(settings)
    seg = compare_segments(side_a.get("segments") or (), side_b.get("segments") or (),
                           scope_a=side_a.get("scope"), scope_b=side_b.get("scope"), buffer_ms=buffer_ms,
                           settings=st, seed_material=seed_material)
    sc_a, sc_b = side_a.get("scope"), side_b.get("scope")
    sample_scope = None if sc_a is None and sc_b is None else _clip_ranges(sc_a, sc_b, (0, 1 << 62))
    # D-12 2: 각 세션의 현재 구간표로 보면 표본 자리가 아닌 판정은 일치도에서 뺀다(판정은 남김, 수만 셈)
    cut = {}
    for key, side in (("a", side_a), ("b", side_b)):
        cut[key] = split_current_sample(side.get("reactions") or (), side.get("missed") or (),
                                        side.get("segments") or (), scope=side.get("scope"), buffer_ms=buffer_ms,
                                        chat_times=chat_times, current_sample=side.get("current_sample"))
    smp = compare_samples(cut["a"]["reactions"], cut["b"]["reactions"], chat_ids=sample_chat_ids,
                          chat_times=chat_times, settings=st, seed_material=seed_material, scope=sample_scope)
    smp["dropped_from_sample"] = {"a": cut["a"]["dropped_reactions"], "b": cut["b"]["dropped_reactions"],
                                  "method_a": cut["a"]["method"], "method_b": cut["b"]["method"]}
    links = compare_links(cut["a"]["reactions"], cut["b"]["reactions"], side_a.get("utterances") or (),
                          side_b.get("utterances") or (), settings=st, scope=sample_scope, chat_times=chat_times)
    cont = compare_continuations(cut["a"]["reactions"], cut["b"]["reactions"], side_a.get("continuations") or (),
                                 side_b.get("continuations") or (), settings=st, scope=sample_scope,
                                 chat_times=chat_times)
    # 06 2-9: ③ 를 직접·높음으로 고쳤거나 물러 대상이 아니게 된 채팅의 놓친 후보는 남겨 두고 일치도에서 뺀다(수만 셈)
    m_keep, m_cnt = {}, {}
    for key, side in (("a", side_a), ("b", side_b)):
        m_keep[key], m_cnt[key] = drop_missed_stale(cut[key]["missed"], side.get("reactions") or ())
    missed = compare_missed(m_keep["a"], m_keep["b"], settings=st, scope=sample_scope, chat_times=chat_times)
    missed["dropped_from_sample"] = {"a": cut["a"]["dropped_missed"], "b": cut["b"]["dropped_missed"]}
    missed["dropped_not_target"] = {"a": m_cnt["a"]["not_target"], "b": m_cnt["b"]["not_target"]}
    missed["dropped_no_reaction"] = {"a": m_cnt["a"]["no_reaction"], "b": m_cnt["b"]["no_reaction"]}
    # 08 D-13 3: 놓친 후보 일치도는 재라벨 둘 사이에서만. 1차가 끼면 숫자는 남기되 '1차는 ④ 없음' 으로 표시한다
    applicable = missed_pair_applicable(side_a.get("session_kind"), side_b.get("session_kind"))
    missed["pair_applicable"] = applicable
    missed["pair_note"] = None if applicable else MISSED_PRIMARY_NOTE
    origin = compare_origins(side_a.get("origins") or (), side_b.get("origins") or (), settings=st)
    ctype = comparison_type(side_a, side_b)
    exposed = sorted(s["session_id"] for s in (side_a, side_b) if s.get("view_exposed") or s.get("analysis_exposed"))
    plan_cancelled = sorted(s["session_id"] for s in (side_a, side_b) if s.get("plan_cancelled"))
    header = {
        "comparison": ctype,
        "intra_only": ctype == "intra",
        "same_machine": bool(side_a.get("machine_id")) and side_a.get("machine_id") == side_b.get("machine_id"),
        "engine_author_included": bool(side_a.get("engine_author") or side_b.get("engine_author")),
        "view_exposed_sessions": exposed,
        "cancelled_plan_sessions": plan_cancelled,
        "representative": not exposed and not plan_cancelled,
        "adjudicator_is_labeler": None if adjudicator_is_labeler is None else bool(adjudicator_is_labeler),
    }
    grows = gates(seg, smp, st)
    interp = interpret(ctype, grows, header, st, smp, origin)
    dr = smp["dropped_from_sample"]["a"] + smp["dropped_from_sample"]["b"] + sum(missed["dropped_from_sample"].values())
    if dr:
        interp.append(f"구간을 고쳐 표본에서 빠진 판정 {dr}건(반응 A {smp['dropped_from_sample']['a']} · B "
                      f"{smp['dropped_from_sample']['b']}, 놓친 후보 A {missed['dropped_from_sample']['a']} · B "
                      f"{missed['dropped_from_sample']['b']})은 남겨 두고 일치도에서는 뺐다 (08 D-12 2).")
    nt = missed["dropped_not_target"]
    if nt["a"] or nt["b"]:
        interp.append(f"③ 판정을 고쳐 4단계 대상이 아니게 된 채팅의 놓친 후보 {nt['a'] + nt['b']}건(A {nt['a']} · B {nt['b']})"
                      "은 남겨 두고 일치도에서는 뺐다 (06 2-9).")
    nr = missed["dropped_no_reaction"]
    if nr["a"] or nr["b"]:
        interp.append(f"③ 반응을 물러 근거가 없어진 놓친 후보 {nr['a'] + nr['b']}건(A {nr['a']} · B {nr['b']})"
                      "은 남겨 두고 일치도에서는 뺐다 (06 2-9).")
    return {
        "header": header,
        "segments": seg,
        "samples": smp,
        "links": links,
        "continuation": cont,
        "missed": missed,
        "origin": origin,
        "gates": grows,
        "interpretation": interp,
        "settings": st,
        "buffer_ms": buffer_ms,
    }


__all__ = [
    "CATEGORIES", "GRADE_SCALE", "GRADE_RANK", "POSITIVE", "DEFAULT_SETTINGS", "DEFAULT_BUFFER_MS",
    "AgreementError", "merge_settings", "kappa_from_table", "linear_weight", "weighted_kappa_from_table", "ac1_binary",
    "percentile", "seed_from", "SplitMix64", "bootstrap_ci", "match_boundaries", "compare_segments",
    "reaction_value", "compare_samples", "gate_verdict", "gates", "comparison_type", "interpret", "build_agreement",
    "sample_regions", "split_current_sample", "drop_missed_not_target", "drop_missed_stale",
    "missed_pair_applicable", "MISSED_KINDS", "MISSED_PRIMARY_NOTE", "compare_links", "compare_continuations", "compare_missed",
    "compare_origins", "CONTINUATION_TYPES", "REASON_CODES", "HUMAN_REASON_CODES", "ORIGIN_VALUES",
    "AUTO_ORIGIN_METHODS",
    "V_PASS", "V_PASS_POINT", "V_UNCERTAIN", "V_FAIL", "V_LOW_N", "V_NA", "VERDICT_KO",
]
