"""재라벨 계획 고르기 `sha256-relabel-v1` (Qt·파일·시계·난수 없음).

설계: docs/design/06-evaluation-and-agreement.md 3장(10분 블록·비율·층화·3일 간격·재라벨 표본), 5장(intra/inter),
7장(블라인드), 03-data-model.md 6-14 '재라벨 계획'·12-2, 08 D-11 6·7, schemas/relabel-plan.schema.json.

규칙 요약
  seed_material = f"{project.sample_seed}|relabel|{source_session_id}"      # 프로젝트 + 잠긴 1차 세션 ID
  block k       = 채팅 시계 [k×block_ms, (k+1)×block_ms) ms (시작값 10분)
  후보 블록     = 1차 세션이 라벨을 마친 범위(done_ranges)와 겹친 길이가 min_block_ms(300초) 이상인 블록
                  (방송 끝·영상 끝의 짧은 블록은 300초 이상일 때만, 06 3-1). done_ranges 는 **후보인지 가리는 데만** 쓴다.
  블록 범위     = 블록 칸 [k×block_ms, (k+1)×block_ms) 를 extent(영상·방송 범위 — 1차 라벨과 무관한 값)로만 자른 것.
                  1차 구간의 시작·끝 시각이 블록 범위·scope 에 드러나지 않게 한다(06 3-1, 05 S-33). 계획 파일에는
                  done_ranges 를 남기지 않고 후보 블록 번호와 extent 만 남긴다(재현 검증은 그 둘로 한다).
  무작위 층     = 후보를 block_key = sha256(seed_material|k) 작은 순으로 n_random 개
  어려움 층(v1) = hard_scores 를 주었을 때만. 무작위 층에 없는 블록 중 점수 높은 순(같으면 block_key 작은 순)
  개수          = n_total = max(1, round_half_up((random_share + hard_share) × 후보 수)), 후보 수 상한.
                  hard_scores 가 없거나 후보가 4개 미만이면 n_total 을 모두 무작위로(06 3-2 'MVP 끝의 최소 기능').
  제시 순서     = order_key = sha256(seed_material|order|k) 작은 순 (층이 순서로 드러나지 않게, 06 3-2)
  τ_block       = 블록과 겹치는 1차 표본 매니페스트들(각 계열의 가장 큰 sample_rev) 가운데 draw_no 가 가장 큰 것들의
                  tau_key 최댓값. 그 seed_material 을 같이 적는다(06 3-5). 겹치는 표본이 없으면 둘 다 null.
  재라벨 표본   = 재라벨러가 자기 구간표로 정한 블록 안 적격 채팅 중 sort_key(seed_material, chat_id) ≤ τ_block
  3일 간격      = relabel_same 만: earliest_start_at = 원 세션 잠금 시각 + min_interval_days. relabel_second 는 null.

같은 잠긴 세션이면 relabel_same 계획과 relabel_second 계획이 같은 블록을 고른다(seed 에 세션 종류를 넣지 않는다).
그래서 1차·같은 사람·다른 사람 세 판을 같은 블록에서 비교할 수 있다(06 4-2 의 α).

시각(created_at·잠금 시각·지금)은 모두 부르는 쪽이 ISO 8601(오프셋 포함) 문자열로 넘긴다(02 3-1: core 는 시계를 쓰지 않는다).
계획 파일에 1차 표본 목록(sample_items)이나 원 구간 경계는 넣지 않는다(06 3-5: 빠진 채팅의 자리로 1차 경계가 드러난다).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Iterable, Mapping, Sequence

from app.core.ids import CHAT_ID_RE, SESSION_ID_RE
from app.core import sampling

ALGORITHM = "sha256-relabel-v1"
SCHEMA = "chatcoach/relabel-plan"
SCHEMA_VERSION = 1
UNIT_BLOCK = "block"
DEFAULT_BLOCK_MS = 600_000          # relabel.block_minutes = 10 (06 3-1)
MIN_BLOCK_MS = 300_000              # 짧은 블록은 300초 이상일 때만 (06 3-1)
SMALL_CANDIDATES = 4                # 후보 블록이 4개 미만이면 무작위 층만 (06 3-2)
STRATUM_RANDOM = "random"
STRATUM_HARD = "hard"
RELABEL_SAME = "relabel_same"
RELABEL_SECOND = "relabel_second"
PLAN_KINDS = (RELABEL_SAME, RELABEL_SECOND)
# 시작 가능 판정 (start_gate)
GATE_OK = "ok"
GATE_EARLY = "early"                # 앞당긴 시작: 사유를 받고 warning(relabel_too_early) 를 남기면 열 수 있다
GATE_BLOCKED = "blocked"            # enforce_interval = true 라 열 수 없다

PLAN_ID_RE = re.compile(r"\ARP-[0-9]{8}-[a-z0-9]{4}\Z")
_SEED_RE = re.compile(r"\A[0-9a-f]{32}\Z")
_HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_UUID_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
_MACHINE4_RE = re.compile(r"\A[a-z2-7]{4}\Z")
_MS_MIN, _MS_MAX = 0, 172_800_000

__all__ = [
    "ALGORITHM", "SCHEMA", "SCHEMA_VERSION", "UNIT_BLOCK", "DEFAULT_BLOCK_MS", "MIN_BLOCK_MS", "STRATUM_RANDOM",
    "STRATUM_HARD", "PLAN_KINDS", "RELABEL_SAME", "RELABEL_SECOND", "GATE_OK", "GATE_EARLY", "GATE_BLOCKED",
    "PLAN_ID_RE", "RelabelPlanError", "Settings", "settings_from_values", "make_seed_material", "block_key",
    "order_key", "normalize_ranges", "Block", "block_ranges", "candidate_blocks", "blocks_from_indices", "selection_counts", "hard_score", "Selected",
    "select_blocks", "latest_source_manifests", "tau_for_block", "earliest_start_at", "build_plan", "check_plan",
    "Verification", "verify_plan", "Gate", "start_gate", "block_sample", "scope_of", "plan_refusal",
    "same_machine",
]


class RelabelPlanError(ValueError):
    """입력이 규칙에 맞지 않는다(형식·범위·설정)."""


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool))


# ---------------------------------------------------------------------------- 설정 (guide-config values.relabel)
@dataclass(frozen=True)
class Settings:
    """계획을 고르는 값. guide-config values.relabel 에서 settings_from_values 로 만든다(06 10장)."""
    block_ms: int = DEFAULT_BLOCK_MS
    min_block_ms: int = MIN_BLOCK_MS
    random_share: float = 0.15
    hard_share: float = 0.10
    fraction_min: float = 0.20
    fraction_max: float = 0.30
    min_interval_days: float = 3
    enforce_interval: bool = False

    def __post_init__(self):
        if not _is_int(self.block_ms) or not (60_000 <= self.block_ms <= 7_200_000):
            raise RelabelPlanError("block_ms 는 60,000~7,200,000 정수 ms 여야 합니다")
        if not _is_int(self.min_block_ms) or not (0 < self.min_block_ms <= self.block_ms):
            raise RelabelPlanError("min_block_ms 는 1 이상 block_ms 이하 정수여야 합니다")
        for name in ("random_share", "hard_share", "fraction_min", "fraction_max"):
            v = getattr(self, name)
            if not _is_num(v) or not (0 <= v <= 1):
                raise RelabelPlanError(f"{name} 는 0~1 수여야 합니다")
        if self.random_share + self.hard_share <= 0:
            raise RelabelPlanError("random_share + hard_share 가 0 이면 고를 블록이 없습니다")
        if not _is_num(self.min_interval_days) or not (0 <= self.min_interval_days <= 365):
            raise RelabelPlanError("min_interval_days 는 0~365 여야 합니다")
        if not isinstance(self.enforce_interval, bool):
            raise RelabelPlanError("enforce_interval 은 true/false 여야 합니다")

    def as_dict(self) -> dict:
        return {"block_ms": self.block_ms, "min_block_ms": self.min_block_ms,
                "random_share": self.random_share, "hard_share": self.hard_share,
                "fraction_min": self.fraction_min, "fraction_max": self.fraction_max,
                "min_interval_days": self.min_interval_days, "enforce_interval": self.enforce_interval}


def settings_from_values(values: Mapping | None) -> tuple[Settings, list[str]]:
    """guide-config `values`(또는 project.json guides[].thresholds) → (Settings, 메모 목록).

    - relabel 블록이 없으면 시작값(06 10장)과 메모 1줄.
    - unit 이 'segment'(안내서 v0.1 문구)이면 블록 10분으로 고르고 메모를 남긴다. 구간 단위로 고르면 재라벨러가
      1차 경계를 받아 써 구간 일치도를 잴 수 없기 때문이다(06 3-1, 05 S-33). 계획에는 unit = block 으로 적힌다.
    """
    notes: list[str] = []
    r = (values or {}).get("relabel") if isinstance(values, Mapping) else None
    if not isinstance(r, Mapping):
        return Settings(), ["기준서 시작값에 relabel 이 없어 06 10장 시작값으로 골랐습니다"]
    block_ms = DEFAULT_BLOCK_MS
    if r.get("unit") == UNIT_BLOCK and _is_num(r.get("block_minutes")):
        block_ms = int(round(float(r["block_minutes"]) * 60_000))
    elif r.get("unit") == "segment":
        notes.append("기준서의 relabel.unit 이 segment 라 10분 블록으로 골랐습니다 (06 3-1: 구간 단위는 1차 경계를 드러냄)")
    elif r.get("unit") == UNIT_BLOCK:
        notes.append("relabel.block_minutes 가 없어 10분 블록으로 골랐습니다")
    kw = {}
    for k in ("random_share", "hard_share", "fraction_min", "fraction_max", "min_interval_days"):
        if _is_num(r.get(k)):
            kw[k] = r[k]
    if isinstance(r.get("enforce_interval"), bool):
        kw["enforce_interval"] = r["enforce_interval"]
    return Settings(block_ms=block_ms, min_block_ms=min(MIN_BLOCK_MS, block_ms), **kw), notes


# ---------------------------------------------------------------------------- 키
def make_seed_material(sample_seed: str, source_session_id: str) -> str:
    """'{sample_seed}|relabel|{source_session_id}'. 같은 프로젝트·같은 잠긴 세션이면 언제 누가 만들어도 같다."""
    if not isinstance(sample_seed, str) or not _SEED_RE.match(sample_seed):
        raise RelabelPlanError("sample_seed 는 32자 소문자 hex 여야 합니다")
    if not isinstance(source_session_id, str) or not SESSION_ID_RE.match(source_session_id):
        raise RelabelPlanError(f"source_session_id 형식이 아닙니다: {source_session_id!r}")
    return f"{sample_seed}|relabel|{source_session_id}"


def block_key(seed_material: str, block_index: int) -> str:
    """무작위 층 순서 키 = sha256(UTF-8(seed_material + '|' + 블록 번호)) 소문자 hex."""
    return hashlib.sha256(f"{seed_material}|{block_index}".encode("utf-8")).hexdigest()


def order_key(seed_material: str, block_index: int) -> str:
    """제시 순서 키 = sha256(UTF-8(seed_material + '|order|' + 블록 번호)). 층과 무관하게 섞는다."""
    return hashlib.sha256(f"{seed_material}|order|{block_index}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------- 블록
def normalize_ranges(ranges: Iterable) -> tuple[tuple[int, int], ...]:
    """[start_ms, end_ms) 목록 → 정렬하고 겹치거나 맞닿은 것을 합친 튜플. 형식이 틀리면 RelabelPlanError."""
    spans: list[tuple[int, int]] = []
    for r in ranges or ():
        try:
            a, z = r
        except (TypeError, ValueError):
            raise RelabelPlanError("범위 항목은 [start_ms, end_ms] 쌍이어야 합니다") from None
        if not (_is_int(a) and _is_int(z)) or not (_MS_MIN <= a < z <= _MS_MAX):
            raise RelabelPlanError("범위는 0 ≤ start_ms < end_ms 인 정수 ms 여야 합니다")
        spans.append((a, z))
    spans.sort()
    out: list[tuple[int, int]] = []
    for a, z in spans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], z))
        else:
            out.append((a, z))
    return tuple(out)


@dataclass(frozen=True)
class Block:
    index: int
    ranges: tuple[tuple[int, int], ...]   # 블록 칸을 extent(영상·방송 범위)로만 자른 범위 — 1차 구간 경계는 넣지 않는다
    covered_ms: int                       # 1차 세션이 라벨을 마친 길이 (후보인지 가리는 데만 쓴다)


def block_ranges(index: int, block_ms: int = DEFAULT_BLOCK_MS,
                 extent: Iterable | None = None) -> tuple[tuple[int, int], ...]:
    """블록 k 의 범위 = 칸 [k×block_ms, (k+1)×block_ms) ∩ extent. extent 가 None 이면 칸 전체(06 3-1)."""
    if not _is_int(index) or index < 0:
        raise RelabelPlanError("블록 번호는 0 이상 정수여야 합니다")
    lo, hi = index * block_ms, min((index + 1) * block_ms, _MS_MAX)
    if lo >= hi:
        return ()
    if extent is None:
        return ((lo, hi),)
    out = []
    for a, z in normalize_ranges(extent):
        a2, z2 = max(a, lo), min(z, hi)
        if a2 < z2:
            out.append((a2, z2))
    return normalize_ranges(out)


def candidate_blocks(done_ranges: Iterable, block_ms: int = DEFAULT_BLOCK_MS,
                     min_block_ms: int = MIN_BLOCK_MS, extent: Iterable | None = None) -> list[Block]:
    """1차 세션이 라벨을 마친 범위 → 후보 블록 목록(블록 번호 순). 겹친 길이가 min_block_ms 미만이면 뺀다.

    done_ranges 는 후보인지 가리는 데만 쓴다. 블록 범위는 block_ranges(k, block_ms, extent) — 1차 구간의 시작·끝이
    범위 가장자리가 되지 않는다(06 3-1, 05 S-33). extent 로 잘라 빈 블록은 뺀다.
    """
    spans = normalize_ranges(done_ranges)
    pieces: dict[int, list[tuple[int, int]]] = {}
    for a, z in spans:
        k = a // block_ms
        while k * block_ms < z:
            lo, hi = max(a, k * block_ms), min(z, (k + 1) * block_ms)
            if lo < hi:
                pieces.setdefault(k, []).append((lo, hi))
            k += 1
    ext = normalize_ranges(extent) if extent is not None else None
    out = []
    for k in sorted(pieces):
        cov = sum(z - a for a, z in normalize_ranges(pieces[k]))
        if cov < min_block_ms:
            continue
        rs = block_ranges(k, block_ms, ext)
        if rs:
            out.append(Block(k, rs, cov))
    return out


def blocks_from_indices(indices: Iterable[int], block_ms: int = DEFAULT_BLOCK_MS,
                        extent: Iterable | None = None) -> list[Block]:
    """계획에 적힌 후보 블록 번호 → Block 목록(재현 검증용). covered_ms 는 모르므로 0."""
    ext = normalize_ranges(extent) if extent is not None else None
    out = []
    for k in sorted(set(indices)):
        rs = block_ranges(k, block_ms, ext)
        if not rs:
            raise RelabelPlanError(f"후보 블록 {k} 가 extent 밖입니다")
        out.append(Block(k, rs, 0))
    return out


def _round_half_up(x: Fraction) -> int:
    return int((x * 2 + 1) // 2)


def selection_counts(n_candidates: int, settings: Settings, with_hard: bool) -> tuple[int, int]:
    """(무작위 층 수, 어려움 층 수). 후보 0개면 (0, 0).

    n_total = max(1, round_half_up((random_share + hard_share) × 후보 수)), 후보 수 상한.
    어려움 층을 쓰지 않거나(with_hard False) 후보가 4개 미만이면 모두 무작위. 쓰면 n_hard = round_half_up(hard_share × 후보 수)
    이고 무작위는 1개 이상 남긴다. 비율 계산은 float 오차가 없게 Fraction(str(값)) 으로 한다.
    """
    if not _is_int(n_candidates) or n_candidates < 0:
        raise RelabelPlanError("후보 수는 0 이상 정수여야 합니다")
    if n_candidates == 0:
        return 0, 0
    total = Fraction(str(settings.random_share)) + Fraction(str(settings.hard_share))
    n_total = min(n_candidates, max(1, _round_half_up(total * n_candidates)))
    if not with_hard or n_candidates < SMALL_CANDIDATES or settings.hard_share == 0:
        return n_total, 0
    n_hard = min(_round_half_up(Fraction(str(settings.hard_share)) * n_candidates), n_total - 1)
    return n_total - n_hard, max(0, n_hard)


def hard_score(n_uncertain: int, n_unclear: int, n_boundaries: int, n_labels: int) -> float:
    """어려움 점수 (06 3-2) = (확신도 아마도·판단 불가 라벨 수 + 불명확 등급 수 + 2 × 구간 경계 수) ÷ (블록 안 라벨 수 + 1)."""
    for v in (n_uncertain, n_unclear, n_boundaries, n_labels):
        if not _is_int(v) or v < 0:
            raise RelabelPlanError("어려움 점수의 입력은 0 이상 정수여야 합니다")
    return (n_uncertain + n_unclear + 2 * n_boundaries) / (n_labels + 1)


@dataclass(frozen=True)
class Selected:
    block: Block
    stratum: str
    key: str
    order: int                    # 제시 순서 1부터


def select_blocks(candidates: Sequence[Block], seed_material: str, settings: Settings,
                  hard_scores: Mapping[int, float] | None = None) -> list[Selected]:
    """후보 → 고른 블록(제시 순서). 같은 입력이면 늘 같다. hard_scores 는 블록 번호 → 어려움 점수(v1, 선택)."""
    with_hard = hard_scores is not None
    n_random, n_hard = selection_counts(len(candidates), settings, with_hard)
    keyed = sorted(((block_key(seed_material, b.index), b) for b in candidates), key=lambda kb: kb[0])
    chosen: list[tuple[Block, str, str]] = [(b, STRATUM_RANDOM, k) for k, b in keyed[:n_random]]
    if n_hard:
        taken = {b.index for b, _, _ in chosen}
        rest = []
        for k, b in keyed:
            if b.index in taken:
                continue
            s = hard_scores.get(b.index, 0.0)
            if not _is_num(s) or s != s:
                raise RelabelPlanError("어려움 점수는 수여야 합니다")
            rest.append((-float(s), k, b))
        rest.sort(key=lambda t: (t[0], t[1]))
        chosen += [(b, STRATUM_HARD, k) for _s, k, b in rest[:n_hard]]
    chosen.sort(key=lambda t: order_key(seed_material, t[0].index))
    return [Selected(b, st, k, i + 1) for i, (b, st, k) in enumerate(chosen)]


# ---------------------------------------------------------------------------- τ_block (06 3-5)
def latest_source_manifests(manifests: Iterable[Mapping], source_session_id: str) -> list[Mapping]:
    """원 세션이 뽑은 매니페스트만 골라, 계열(원 구간·draw_no)마다 가장 큰 sample_rev 하나씩(sample_set_id 순)."""
    own = [m for m in manifests
           if isinstance(m, Mapping) and (m.get("source_segment") or {}).get("session_id") == source_session_id]
    latest = sampling.latest_of(own)
    return sorted(latest.values(), key=lambda m: m["sample_set_id"])


def _overlaps(ranges: Sequence[tuple[int, int]], a: int, z: int) -> bool:
    return any(lo < z and a < hi for lo, hi in ranges)


def tau_for_block(block_ranges: Sequence[tuple[int, int]],
                  latest_manifests: Iterable[Mapping]) -> tuple[str | None, str | None]:
    """(seed_material, tau_block_key). 블록과 겹치는 표본 가운데 draw_no 가 가장 큰 것들의 tau_key 최댓값.

    겹치는 표본이 없거나 모두 tau_key 가 null(적격 0)이면 (None, None).
    """
    best: tuple[int, str, str] | None = None        # (draw_no, tau_key, seed_material)
    for m in latest_manifests:
        seg = m.get("source_segment") or {}
        a, z, tau = seg.get("start_ms"), seg.get("end_ms"), m.get("tau_key")
        if not (_is_int(a) and _is_int(z)) or tau is None:
            continue
        if not _overlaps(block_ranges, a, z):
            continue
        if not isinstance(tau, str) or not _HEX64_RE.match(tau):
            raise RelabelPlanError(f"매니페스트 {m.get('sample_set_id')!r} 의 tau_key 형식이 틀렸습니다")
        d = m.get("draw_no")
        sm = m.get("seed_material")
        if not _is_int(d) or not isinstance(sm, str):
            raise RelabelPlanError(f"매니페스트 {m.get('sample_set_id')!r} 의 draw_no·seed_material 이 틀렸습니다")
        cand = (d, tau, sm)
        if best is None or (cand[0], cand[1]) > (best[0], best[1]):
            best = cand
    return (None, None) if best is None else (best[2], best[1])


# ---------------------------------------------------------------------------- 시각
def _parse_iso(v, name: str) -> datetime:
    if not isinstance(v, str):
        raise RelabelPlanError(f"{name} 는 ISO 8601 문자열이어야 합니다")
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        raise RelabelPlanError(f"{name} 가 ISO 8601 이 아닙니다: {v!r}") from None
    if t.tzinfo is None:
        raise RelabelPlanError(f"{name} 에 시간대 오프셋이 없습니다")
    return t


def earliest_start_at(session_kind: str, locked_at: str, min_interval_days: float) -> str | None:
    """relabel_same 이면 잠금 시각 + min_interval_days (같은 오프셋의 ISO, 초 단위), relabel_second 는 None (06 3-3)."""
    if session_kind not in PLAN_KINDS:
        raise RelabelPlanError(f"재라벨 세션 종류가 아닙니다: {session_kind!r}")
    t = _parse_iso(locked_at, "locked_at")
    if session_kind == RELABEL_SECOND:
        return None
    return (t + timedelta(days=float(min_interval_days))).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------- 계획 만들기
def _check_id(name: str, v, rx: re.Pattern) -> str:
    if not isinstance(v, str) or not rx.match(v):
        raise RelabelPlanError(f"{name} 형식이 아닙니다: {v!r}")
    return v


def build_plan(*, plan_id: str, plan_no: int, project_id: str, session_kind: str, labeler_id: str,
               source_session_id: str, source_lock: Mapping, source_machine4s: Iterable[str],
               created_machine4: str, sample_seed: str, done_ranges: Iterable | None, settings: Settings,
               manifests: Iterable[tuple[Mapping, str]] = (), hard_scores: Mapping[int, float] | None = None,
               guide_version: str, thresholds_sha256: str, created_at: str, created_by: str,
               app_version: str, notes: Iterable[str] = (), extent: Iterable | None = None,
               candidate_indices: Iterable[int] | None = None) -> dict:
    """재라벨 계획 dict 를 만든다(파일에 쓰지 않는다). 스키마 relabel-plan.schema.json 모양.

    - source_lock: 원 세션 잠금 {locked_at, sha256(잠금 파일 바이트)}. 잠기지 않은 세션으로는 계획을 만들 수 없다.
    - source_session_id 는 1차(primary) 세션이어야 한다.
    - labeler_id: 이 계획으로 재라벨할 사람. relabel_same 이면 원 세션 라벨러와 같아야 하고, relabel_second 면 달라야 한다.
    - source_machine4s: 원 세션 기록 파일들의 machine4(파일 이름에서). created_machine4: 계획을 만든 PC.
      만든 PC 가 원 세션 PC 중 하나면 same_machine_as_source = true (08 D-11 6: 보고서의 '같은 machine_id').
    - manifests: (매니페스트 dict, 파일 바이트 sha256) 목록. 원 세션 것이 아니면 무시한다. τ_block 계산과
      source_manifests(재현 검증용 참조)에 쓴다. 표본 목록 자체(items)는 계획에 넣지 않는다(06 3-5).
    - done_ranges: 1차가 라벨을 마친 범위. 후보 블록을 가리는 데만 쓰고 계획 파일에는 남기지 않는다(06 3-1).
    - extent: 블록 범위를 자를 영상·방송 범위(1차 라벨과 무관한 값, 예: 영상이 덮는 VOD 범위). None 이면 블록 칸 전체.
    - candidate_indices: 재현 검증(verify_plan)이 계획에 적힌 후보 블록 번호로 다시 고를 때만 준다(done_ranges 대신).
    """
    _check_id("plan_id", plan_id, PLAN_ID_RE)
    if not _is_int(plan_no) or plan_no < 1:
        raise RelabelPlanError("plan_no 는 1 이상 정수여야 합니다")
    _check_id("project_id", project_id, _UUID_RE)
    if session_kind not in PLAN_KINDS:
        raise RelabelPlanError(f"재라벨 세션 종류가 아닙니다: {session_kind!r}")
    _check_id("labeler_id", labeler_id, _LABELER_RE)
    m = SESSION_ID_RE.match(source_session_id) if isinstance(source_session_id, str) else None
    if not m:
        raise RelabelPlanError(f"source_session_id 형식이 아닙니다: {source_session_id!r}")
    if m.group(2) != "P":
        raise RelabelPlanError("재라벨 계획의 원 세션은 1차(primary) 세션이어야 합니다")
    source_labeler = m.group(1)
    if session_kind == RELABEL_SAME and labeler_id != source_labeler:
        raise RelabelPlanError("같은 사람 재라벨(relabel_same)은 원 세션과 같은 라벨러여야 합니다")
    if session_kind == RELABEL_SECOND and labeler_id == source_labeler:
        raise RelabelPlanError("두 번째 사람(relabel_second)은 원 세션과 다른 라벨러여야 합니다")
    if not isinstance(source_lock, Mapping):
        raise RelabelPlanError("원 세션이 잠기지 않았습니다 (locks/<session_id>.json 이 필요합니다)")
    locked_at = source_lock.get("locked_at")
    _parse_iso(locked_at, "source_lock.locked_at")
    _check_id("source_lock.sha256", source_lock.get("sha256"), _HEX64_RE)
    m4s = sorted(set(source_machine4s))
    for v in m4s:
        _check_id("source_machine4s", v, _MACHINE4_RE)
    _check_id("created_machine4", created_machine4, _MACHINE4_RE)
    _check_id("thresholds_sha256", thresholds_sha256, _HEX64_RE)
    if not isinstance(guide_version, str) or not guide_version:
        raise RelabelPlanError("guide_version 이 없습니다")
    _parse_iso(created_at, "created_at")
    seed_material = make_seed_material(sample_seed, source_session_id)

    ext = normalize_ranges(extent) if extent is not None else None
    if ext is not None and not ext:
        raise RelabelPlanError("extent(영상·방송 범위)가 비었습니다")
    if candidate_indices is not None:
        cands = blocks_from_indices(candidate_indices, settings.block_ms, ext)
    else:
        done = normalize_ranges(done_ranges or ())
        if not done:
            raise RelabelPlanError("1차 세션이 라벨을 마친 범위(done_ranges)가 비었습니다")
        cands = candidate_blocks(done, settings.block_ms, settings.min_block_ms, ext)
    if not cands:
        raise RelabelPlanError(f"후보 블록이 없습니다 (라벨을 마친 범위가 {settings.min_block_ms // 1000}초 이상인 블록 0개)")
    chosen = select_blocks(cands, seed_material, settings, hard_scores)

    pairs = [(mf, sha) for mf, sha in manifests]
    for _mf, sha in pairs:
        _check_id("manifest sha256", sha, _HEX64_RE)
    sha_of = {id(mf): sha for mf, sha in pairs}
    latest = latest_source_manifests([mf for mf, _ in pairs], source_session_id)
    selected = []
    for s in chosen:
        sm, tau = tau_for_block(s.block.ranges, latest)
        selected.append({"block_index": s.block.index, "ranges": [[a, z] for a, z in s.block.ranges],
                         "stratum": s.stratum, "order": s.order, "seed_material": sm, "tau_block_key": tau})
    n_random = sum(1 for s in chosen if s.stratum == STRATUM_RANDOM)
    n_hard = len(chosen) - n_random
    frac = len(chosen) / len(cands)
    plan_notes = list(notes)
    if not (settings.fraction_min <= frac <= settings.fraction_max):
        plan_notes.append(f"후보 {len(cands)}개라 고른 비율 {frac:.3f} 가 {settings.fraction_min}~{settings.fraction_max} 밖입니다")
    if hard_scores is None and settings.hard_share > 0:
        plan_notes.append("어려움 층(v1)을 쓰지 않아 무작위 층으로만 골랐습니다 (06 3-2)")
    created_m4_same = created_machine4 in m4s
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "plan_no": plan_no,
        "project_id": project_id,
        "session_kind": session_kind,
        "labeler_id": labeler_id,
        "source_session_id": source_session_id,
        "source_labeler_id": source_labeler,
        "source_lock": {"locked_at": locked_at, "sha256": source_lock["sha256"]},
        "source_machine4s": m4s,
        "created_machine4": created_machine4,
        "same_machine_as_source": created_m4_same,
        "unit": UNIT_BLOCK,
        "block_ms": settings.block_ms,
        "settings": settings.as_dict(),
        "guide_version": guide_version,
        "thresholds_sha256": thresholds_sha256,
        "selection": {
            "algorithm": ALGORITHM,
            "seed_material": seed_material,
            "extent": None if ext is None else [[a, z] for a, z in ext],
            "candidate_blocks": [b.index for b in cands],
            "n_random": n_random,
            "n_hard": n_hard,
            "hard_used": hard_scores is not None,
            "hard_scores": (None if hard_scores is None
                            else {str(k): float(v) for k, v in sorted(hard_scores.items())}),
        },
        "selected": selected,
        "scope": scope_of({"selected": selected}),
        "source_manifests": [{"sample_set_id": mf["sample_set_id"], "sha256": sha_of[id(mf)],
                              "draw_no": mf.get("draw_no"), "tau_key": mf.get("tau_key")} for mf in latest],
        "earliest_start_at": earliest_start_at(session_kind, locked_at, settings.min_interval_days),
        "enforce_interval": settings.enforce_interval if session_kind == RELABEL_SAME else False,
        "notes": plan_notes,
        "created_at": created_at,
        "created_by": created_by,
        "app_version": app_version,
    }


def scope_of(plan: Mapping) -> dict:
    """session.open.scope 값: {"type": "blocks", "ranges": 고른 블록 범위를 합친 목록} (03 12-2)."""
    rs = []
    for s in plan.get("selected") or []:
        rs.extend(tuple(r) for r in s.get("ranges") or [])
    return {"type": "blocks", "ranges": [[a, z] for a, z in normalize_ranges(rs)]}


# ---------------------------------------------------------------------------- 모양 검사 · 재현 검증
_REQUIRED = ("schema", "schema_version", "plan_id", "plan_no", "project_id", "session_kind", "labeler_id",
             "source_session_id", "source_labeler_id", "source_lock", "source_machine4s", "created_machine4",
             "same_machine_as_source", "unit", "block_ms", "settings", "guide_version", "thresholds_sha256",
             "selection", "selected", "scope", "source_manifests", "earliest_start_at", "enforce_interval",
             "notes", "created_at", "created_by", "app_version")


def check_plan(plan) -> list[str]:
    """스키마 없이도 할 수 있는 모양 검사(파일에서 읽은 계획을 쓰기 전에). 문제 목록, 없으면 빈 목록."""
    if not isinstance(plan, Mapping):
        return ["계획이 객체가 아닙니다"]
    p: list[str] = []
    miss = [k for k in _REQUIRED if k not in plan]
    if miss:
        return [f"필수 칸이 없습니다: {miss}"]
    if plan["schema"] != SCHEMA or plan["schema_version"] != SCHEMA_VERSION:
        p.append("relabel-plan 스키마 v1 이 아닙니다")
    if not isinstance(plan["plan_id"], str) or not PLAN_ID_RE.match(plan["plan_id"]):
        p.append("plan_id 형식이 아닙니다")
    if plan["session_kind"] not in PLAN_KINDS:
        p.append("session_kind 가 재라벨 종류가 아닙니다")
    if not isinstance(plan["labeler_id"], str) or not _LABELER_RE.match(plan["labeler_id"]):
        p.append("labeler_id 형식이 아닙니다")
    m = SESSION_ID_RE.match(plan["source_session_id"]) if isinstance(plan["source_session_id"], str) else None
    if not m or m.group(2) != "P" or m.group(1) != plan["source_labeler_id"]:
        p.append("source_session_id 가 1차 세션 ID 가 아니거나 source_labeler_id 와 다릅니다")
    elif plan["session_kind"] == RELABEL_SAME and plan["labeler_id"] != m.group(1):
        p.append("relabel_same 인데 라벨러가 원 세션과 다릅니다")
    elif plan["session_kind"] == RELABEL_SECOND and plan["labeler_id"] == m.group(1):
        p.append("relabel_second 인데 라벨러가 원 세션과 같습니다")
    sel = plan["selected"]
    if not isinstance(sel, list) or not sel:
        p.append("selected 가 비었습니다")
    else:
        try:
            for s in sel:
                normalize_ranges(s["ranges"])
            if plan["scope"] != scope_of(plan):
                p.append("scope 가 selected 범위와 다릅니다")
        except (RelabelPlanError, KeyError, TypeError):
            p.append("selected 의 범위 형식이 틀렸습니다")
    m4s = plan["source_machine4s"]
    if not isinstance(m4s, list) or not all(isinstance(v, str) and _MACHINE4_RE.match(v) for v in m4s):
        p.append("source_machine4s 형식이 틀렸습니다")
    if plan["earliest_start_at"] is not None:
        try:
            _parse_iso(plan["earliest_start_at"], "earliest_start_at")
        except RelabelPlanError as e:
            p.append(str(e))
    elif plan["session_kind"] == RELABEL_SAME:
        p.append("relabel_same 인데 earliest_start_at 이 없습니다")
    return p


@dataclass(frozen=True)
class Verification:
    ok: bool
    problems: tuple[str, ...]


def verify_plan(plan: Mapping, *, sample_seed: str, source_lock: Mapping,
                manifests: Iterable[tuple[Mapping, str]] = (),
                hard_scores: Mapping[int, float] | None = None) -> Verification:
    """계획 파일 하나 + 같은 입력(sample_seed·잠금·매니페스트)으로 고르기를 다시 해서 바이트 단위가 아니라 값 단위로 맞춘다.

    hard_scores 를 주지 않으면 계획에 적힌 selection.hard_scores 를 쓴다(어려움 점수는 1차 라벨로 계산한 값이라
    계획에 남긴다). 고른 블록·층·순서·τ_block·seed_material·earliest_start_at·source_manifests 가 모두 같아야 통과.
    """
    probs = list(check_plan(plan))
    if probs:
        return Verification(False, tuple(probs))
    sel = plan["selection"]
    if sel.get("algorithm") != ALGORITHM:
        return Verification(False, (f"알 수 없는 고르기 알고리즘: {sel.get('algorithm')!r}",))
    if hard_scores is None and sel.get("hard_used"):
        hard_scores = {int(k): v for k, v in (sel.get("hard_scores") or {}).items()}
    try:
        st = plan["settings"]
        settings = Settings(**st)
        again = build_plan(
            plan_id=plan["plan_id"], plan_no=plan["plan_no"], project_id=plan["project_id"],
            session_kind=plan["session_kind"], labeler_id=plan["labeler_id"],
            source_session_id=plan["source_session_id"], source_lock=source_lock,
            source_machine4s=plan["source_machine4s"], created_machine4=plan["created_machine4"],
            sample_seed=sample_seed, done_ranges=None, settings=settings,
            candidate_indices=sel.get("candidate_blocks") or [], extent=sel.get("extent"),
            manifests=manifests, hard_scores=hard_scores, guide_version=plan["guide_version"],
            thresholds_sha256=plan["thresholds_sha256"], created_at=plan["created_at"],
            created_by=plan["created_by"], app_version=plan["app_version"])
    except (RelabelPlanError, TypeError, KeyError, sampling.SamplingError) as e:
        return Verification(False, (f"다시 고르지 못했습니다: {e}",))
    for k in ("source_lock", "unit", "block_ms", "selected", "scope", "source_manifests", "earliest_start_at",
              "enforce_interval", "same_machine_as_source", "source_labeler_id"):
        if again[k] != plan[k]:
            probs.append(f"{k} 가 다시 고른 값과 다릅니다")
    for k in ("algorithm", "seed_material", "extent", "candidate_blocks", "n_random", "n_hard", "hard_used"):
        if again["selection"][k] != sel.get(k):
            probs.append(f"selection.{k} 가 다시 고른 값과 다릅니다")
    return Verification(not probs, tuple(probs))


# ---------------------------------------------------------------------------- 시작 가능 판정 (06 3-3)
@dataclass(frozen=True)
class Gate:
    status: str                    # GATE_OK / GATE_EARLY / GATE_BLOCKED
    earliest_start_at: str | None
    remaining_ms: int              # 남은 시간(0 이상)


def start_gate(plan: Mapping, now: str) -> Gate:
    """지금(now, ISO 오프셋 포함) 이 계획으로 세션을 열 수 있나. relabel_second 는 늘 ok.

    relabel_same 이 earliest_start_at 전이면: enforce_interval 이면 blocked, 아니면 early(사유를 받아
    warning(relabel_too_early) 줄로 남기고 연다 — 계획 파일은 고치지 않는다, 03 6-14).
    """
    t = _parse_iso(now, "now")
    e = plan.get("earliest_start_at")
    if plan.get("session_kind") != RELABEL_SAME or e is None:
        return Gate(GATE_OK, None, 0)
    left = int((_parse_iso(e, "earliest_start_at") - t).total_seconds() * 1000)
    if left <= 0:
        return Gate(GATE_OK, e, 0)
    return Gate(GATE_BLOCKED if plan.get("enforce_interval") else GATE_EARLY, e, left)


# ---------------------------------------------------------------------------- 재라벨 표본 (06 3-5)
def block_sample(eligible_chat_ids: Iterable[str], seed_material: str | None, tau_block_key: str | None) -> list[str]:
    """블록 하나의 재라벨 표본: 재라벨러 자기 구간표로 정한 블록 안 적격 chat_id 중 key ≤ τ_block (key 오름차순).

    τ_block 이 없으면(그 블록에 1차 표본이 없음) 빈 목록.
    """
    if seed_material is None or tau_block_key is None:
        return []
    sampling.parse_seed_material(seed_material)
    if not isinstance(tau_block_key, str) or not _HEX64_RE.match(tau_block_key):
        raise RelabelPlanError("tau_block_key 형식이 틀렸습니다")
    out = []
    for c in set(eligible_chat_ids):
        if not isinstance(c, str) or not CHAT_ID_RE.match(c):
            raise RelabelPlanError(f"chat_id 형식이 아닙니다: {c!r}")
        k = sampling.sort_key(seed_material, c)
        if k <= tau_block_key:
            out.append((k, c))
    return [c for _k, c in sorted(out)]


# ---------------------------------------------------------------------------- 누가 이 계획으로 여나 (D-11 6)
def plan_refusal(plan: Mapping, session_kind: str, labeler_id: str | None) -> str | None:
    """이 라벨러가 이 종류로 이 계획을 쓸 수 없으면 까닭, 쓸 수 있으면 None (시각 조건은 start_gate 가 따로 본다)."""
    if plan.get("session_kind") != session_kind:
        return "다른 종류의 계획입니다"
    if not labeler_id:
        return "라벨러를 고르지 않았습니다"
    if plan.get("labeler_id") != labeler_id:
        return "다른 라벨러에게 맡긴 계획입니다"
    src = plan.get("source_labeler_id")
    if session_kind == RELABEL_SAME and labeler_id != src:
        return "같은 사람 재라벨은 원 세션 라벨러만 열 수 있습니다"
    if session_kind == RELABEL_SECOND and labeler_id == src:
        return "두 번째 사람 세션은 원 세션과 다른 라벨러 ID 로만 열 수 있습니다"
    return None


def same_machine(plan: Mapping, machine4: str | None) -> bool:
    """이 PC(machine4)가 원 세션을 쓴 PC 중 하나인가. relabel_second 면 보고서에 '같은 machine_id' 로 표시한다(D-11 6)."""
    return bool(machine4) and machine4 in (plan.get("source_machine4s") or [])
