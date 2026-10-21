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

이번에 하지 않는 것: 연결(±5초)·반응 시각·이어짐·확신도·놓친 후보·출처, 세 세션 이상의 Krippendorff α (D-11 8).

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

from .segments import is_transition

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
              sample: Mapping | None = None) -> list[str]:
    """06 5장의 해석 문구. 사람이 읽는 줄 목록(본문·닉네임 없음)."""
    lines: list[str] = []
    verdicts = {r["key"]: r["verdict"] for r in gate_rows}
    judged = [v for v in verdicts.values() if v not in (V_LOW_N, V_NA)]
    failed = [r for r in gate_rows if r["verdict"] == V_FAIL]
    if header.get("same_machine"):
        lines.append("두 세션이 같은 machine_id 에서 매겨졌다. 기능 시험용이며 실제 일치도로 쓰면 안 된다 (08 D-11 6).")
    if not header.get("representative", True):
        lines.append("노출 기록(view.exposed 또는 분석 세션 열람)이 있는 세션이 있어 일치도 대표값으로 쓰지 않고 따로 보고한다 (06 3-4).")
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
    lines.append("층(무작위·어려움) 정보는 아직 읽지 않는다. 이 판은 비교한 범위 전체를 하나로 보고 판단했다 (06 3-2 는 무작위 층으로만 판단).")
    return lines


# ----------------------------------------------------------------------------- 묶음
def build_agreement(side_a: Mapping, side_b: Mapping, *, settings: Mapping | None = None,
                    buffer_ms: int = DEFAULT_BUFFER_MS, seed_material: str = "",
                    sample_chat_ids: Iterable[str] | None = None, chat_times: Mapping[str, int] | None = None) -> dict:
    """두 세션 → 일치도 결과(보고서 본체). 잠금 확인·파일 읽기는 app 층이 하고, 여기서는 계산만 한다.

    side: {session_id, labeler_id, session_kind, machine_id, scope(None 또는 [(시작, 끝)]), segments[], reactions[],
           view_exposed(목록), analysis_exposed(bool), engine_author(bool)}. segments·reactions 는 현재 유효 라벨.
    """
    st = merge_settings(settings)
    seg = compare_segments(side_a.get("segments") or (), side_b.get("segments") or (),
                           scope_a=side_a.get("scope"), scope_b=side_b.get("scope"), buffer_ms=buffer_ms,
                           settings=st, seed_material=seed_material)
    sc_a, sc_b = side_a.get("scope"), side_b.get("scope")
    sample_scope = None if sc_a is None and sc_b is None else _clip_ranges(sc_a, sc_b, (0, 1 << 62))
    smp = compare_samples(side_a.get("reactions") or (), side_b.get("reactions") or (), chat_ids=sample_chat_ids,
                          chat_times=chat_times, settings=st, seed_material=seed_material, scope=sample_scope)
    ctype = comparison_type(side_a, side_b)
    exposed = sorted(s["session_id"] for s in (side_a, side_b) if s.get("view_exposed") or s.get("analysis_exposed"))
    header = {
        "comparison": ctype,
        "intra_only": ctype == "intra",
        "same_machine": bool(side_a.get("machine_id")) and side_a.get("machine_id") == side_b.get("machine_id"),
        "engine_author_included": bool(side_a.get("engine_author") or side_b.get("engine_author")),
        "view_exposed_sessions": exposed,
        "representative": not exposed,
    }
    grows = gates(seg, smp, st)
    return {
        "header": header,
        "segments": seg,
        "samples": smp,
        "gates": grows,
        "interpretation": interpret(ctype, grows, header, st, smp),
        "settings": st,
        "buffer_ms": buffer_ms,
    }


__all__ = [
    "CATEGORIES", "GRADE_SCALE", "GRADE_RANK", "POSITIVE", "DEFAULT_SETTINGS", "DEFAULT_BUFFER_MS",
    "AgreementError", "merge_settings", "kappa_from_table", "linear_weight", "weighted_kappa_from_table", "ac1_binary",
    "percentile", "seed_from", "SplitMix64", "bootstrap_ci", "match_boundaries", "compare_segments",
    "reaction_value", "compare_samples", "gate_verdict", "gates", "comparison_type", "interpret", "build_agreement",
    "V_PASS", "V_PASS_POINT", "V_UNCERTAIN", "V_FAIL", "V_LOW_N", "V_NA", "VERDICT_KO",
]
