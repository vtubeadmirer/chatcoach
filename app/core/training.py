"""스트리머 훈련 — 고르기·견주기 계산 (docs/design/18-progress-2026-10-22-e2.md 6-2·6-3, 08 D-18 6~8, D-01, D-16).

하는 일
- 고르기: 채팅을 한 번 누르면 '읽겠다'(pick), 같은 채팅을 다시 누르면 취소(unpick). toggle_pick 이 다음 상태와
  기록할 줄 종류를 돌려준다. 기록 줄(training.pick·training.unpick)을 차례로 다시 놓으면 picks_from_events 가 같은
  상태를 만든다.
- 견주기: 고른 채팅과 '원래 반응'(reference_set 이 읽은 사람 최종 세트 또는 손라벨)을 같음·나만·원래만으로 나눈다.
  · 같은 채팅 판정 규칙: **chat_id 가 같으면 같은 채팅**이다(same_chat). 시각·본문은 보지 않는다.
  · 숫자: 같음 수, 맞힌 비율(같음 / 원래 반응 수), 고른 시각과 원래 반응 시각의 차 중앙값.
  · 시각 차 = picked_vod_ms − reaction_vod_ms (음수 = 원래 반응보다 먼저 골랐다). 원래 반응 시각이 없는 건은 뺀다.
    중앙값은 정수 ms (짝수 개면 가운데 둘의 평균을 내림).
  · 범위(range_ms)를 주면 원래 반응은 채팅 시각이 그 안([시작, 끝))인 것만 센다. until_ms 를 주면 그 시각까지 본 채팅만
    센다(구간 끝 전에 '견주기' 를 누른 경우 — 아직 지나가지 않은 채팅을 '원래만' 으로 세지 않는다).
- 견주기 화면 도움: next_orig_only(N / Shift+N), listen_start_ms(행을 누르면 소리를 켜고 재생할 자리).

core 층: 파일·시계·난수·Qt 를 쓰지 않는다. 채팅 본문·닉네임은 다루지 않는다(chat_id·시각·숫자만, D-17 8).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .ids import CHAT_ID_RE

# 줄 종류 (schemas/training-event.schema.json 의 kind 와 같다)
K_START = "training.start"
K_PICK = "training.pick"
K_UNPICK = "training.unpick"
K_COMPARE = "training.compare"
K_END = "training.end"
KINDS = (K_START, K_PICK, K_UNPICK, K_COMPARE, K_END)

# 견주기 칸
SAME = "same"            # 내가 골랐고 원래도 반응한 채팅
MINE_ONLY = "mine_only"  # 내가 골랐지만 원래는 반응하지 않은 채팅
ORIG_ONLY = "orig_only"  # 원래 반응했지만 내가 고르지 않은 채팅
STATUSES = (SAME, MINE_ONLY, ORIG_ONLY)

# 원래 반응으로 치는 등급 (18 6-5 1: 사람 최종 세트의 직접·높음)
REFERENCE_GRADES = ("direct", "likely")

# 기준 출처
REF_FINAL_SET = "final_set"
REF_MANUAL_CSV = "manual_csv"
REF_SOURCES = (REF_FINAL_SET, REF_MANUAL_CSV)

DEFAULT_LEAD_MS = 5_000   # 행을 누르면 이만큼 앞에서 재생을 시작한다(원래 대답 '앞뒤' 를 듣게)


class TrainingError(ValueError):
    """입력이 훈련 계산 조건에 맞지 않음."""


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_chat_id(chat_id) -> str:
    if not isinstance(chat_id, str) or not CHAT_ID_RE.match(chat_id):
        raise TrainingError("chat_id 형식이 아닙니다")
    return chat_id


def _check_ms(name: str, v) -> int:
    if not _is_int(v) or v < 0:
        raise TrainingError(f"{name} 는 0 이상 정수(ms)여야 합니다")
    return v


def same_chat(a: str, b: str) -> bool:
    """같은 채팅 판정 규칙: chat_id 가 같으면 같은 채팅이다 (03 13-1 sl-chat-id-v1 은 수집마다 같은 값)."""
    return isinstance(a, str) and isinstance(b, str) and a == b


# ----------------------------------------------------------------------------- 원래 반응
@dataclass(frozen=True)
class RefReaction:
    """원래 반응 한 건. 본문·닉네임은 없다.

    chat_vod_ms: 채팅 VOD 시각. reaction_vod_ms: 원래 대답(반응) 시각 — 최종 세트는 reaction_time_ms(없으면 연결 발화
    시작), 손라벨은 utterance_vod_ms. 모르면 None.
    """
    chat_id: str
    chat_vod_ms: int | None
    reaction_vod_ms: int | None
    grade: str | None = None


def normalize_reference(refs: Iterable[RefReaction]) -> tuple[list[RefReaction], int]:
    """chat_id 하나에 한 건만 남긴다(같은 채팅에 원래 반응이 둘이면 반응 시각이 이른 것). (정리된 목록, 뺀 건수).
    정렬: 채팅 시각(없으면 맨 뒤) → chat_id."""
    best: dict[str, RefReaction] = {}
    dropped = 0
    for r in refs or ():
        if not isinstance(r, RefReaction):
            raise TrainingError("RefReaction 이 아닙니다")
        _check_chat_id(r.chat_id)
        prev = best.get(r.chat_id)
        if prev is None:
            best[r.chat_id] = r
            continue
        dropped += 1
        a = prev.reaction_vod_ms if prev.reaction_vod_ms is not None else float("inf")
        b = r.reaction_vod_ms if r.reaction_vod_ms is not None else float("inf")
        if b < a:
            best[r.chat_id] = r
    out = sorted(best.values(), key=lambda r: (r.chat_vod_ms is None, r.chat_vod_ms or 0, r.chat_id))
    return out, dropped


def reference_sha256(source: str, refs: Iterable[RefReaction]) -> str:
    """기준(원래 반응 목록)의 해시. 훈련 기록 compare 줄의 reference_sha256 (18 6-3 '기준 최종 세트의 해시').
    정규 JSON: {"source", "items": [[chat_id, chat_vod_ms, reaction_vod_ms, grade], …]} (chat_id 순)."""
    if source not in REF_SOURCES:
        raise TrainingError("모르는 기준 출처입니다")
    norm, _ = normalize_reference(refs)
    items = sorted([[r.chat_id, r.chat_vod_ms, r.reaction_vod_ms, r.grade] for r in norm], key=lambda x: x[0])
    data = json.dumps({"source": source, "items": items}, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------------------- 고르기
def toggle_pick(picks: Mapping[str, int], chat_id: str, vod_ms: int) -> tuple[dict[str, int], str]:
    """채팅 한 번 누름. (다음 상태, 기록할 줄 종류 K_PICK|K_UNPICK). 입력 dict 는 고치지 않는다.
    picks: chat_id → 고른 순간의 재생 위치(picked_vod_ms)."""
    _check_chat_id(chat_id)
    _check_ms("vod_ms", vod_ms)
    out = dict(picks or {})
    if chat_id in out:
        del out[chat_id]
        return out, K_UNPICK
    out[chat_id] = vod_ms
    return out, K_PICK


def picks_from_events(records: Iterable[Mapping]) -> dict[str, int]:
    """훈련 기록 줄 → 지금 고른 상태. seq 순으로 pick·unpick 을 다시 놓는다. 모양이 맞지 않는 줄은 건너뛴다."""
    rows = [r for r in records or () if isinstance(r, Mapping) and r.get("kind") in (K_PICK, K_UNPICK)]
    rows.sort(key=lambda r: r.get("seq") if _is_int(r.get("seq")) else 0)
    out: dict[str, int] = {}
    for r in rows:
        b = r.get("body") if isinstance(r.get("body"), Mapping) else {}
        cid = b.get("chat_id")
        if not isinstance(cid, str) or not CHAT_ID_RE.match(cid):
            continue
        if r["kind"] == K_PICK:
            ms = b.get("picked_vod_ms")
            if _is_int(ms) and ms >= 0:
                out[cid] = ms
        else:
            out.pop(cid, None)
    return out


# ----------------------------------------------------------------------------- 견주기
@dataclass(frozen=True)
class CompareRow:
    """견주기 표의 행 하나. chat_vod_ms 는 원래 반응 쪽에서 오거나(같음·원래만), chat_times 로 받는다(나만)."""
    status: str
    chat_id: str
    chat_vod_ms: int | None
    picked_vod_ms: int | None
    reaction_vod_ms: int | None
    diff_ms: int | None          # picked − reaction (같음이고 둘 다 있을 때만)


@dataclass
class Comparison:
    rows: list[CompareRow]
    same: int
    mine_only: int
    orig_only: int
    reference_count: int
    hit_rate: float | None             # 같음 / 원래 반응 수 (원래 반응이 0 이면 None)
    median_diff_ms: int | None         # 같음 행의 diff_ms 중앙값 (없으면 None)
    diff_count: int                    # 중앙값에 들어간 행 수
    excluded_refs: int = 0             # 범위 밖·아직 안 본 원래 반응
    duplicate_refs: int = 0            # 같은 chat_id 로 겹친 원래 반응(하나만 남김)
    range_ms: tuple[int, int] | None = None
    until_ms: int | None = None
    extra: dict = field(default_factory=dict)

    def by_status(self, status: str) -> list[CompareRow]:
        return [r for r in self.rows if r.status == status]

    def summary(self) -> dict:
        """훈련 기록 compare 줄의 숫자 칸 (본문·닉네임 없음)."""
        return {"same": self.same, "mine_only": self.mine_only, "orig_only": self.orig_only,
                "reference_count": self.reference_count, "hit_rate": self.hit_rate,
                "median_diff_ms": self.median_diff_ms, "diff_count": self.diff_count}


def median_int(vals: Sequence[int]) -> int | None:
    """정수 중앙값. 짝수 개면 가운데 둘의 평균을 내림(//2). 비면 None."""
    v = sorted(vals)
    n = len(v)
    if n == 0:
        return None
    if n % 2:
        return v[n // 2]
    return (v[n // 2 - 1] + v[n // 2]) // 2


def _in_range(t: int | None, range_ms: tuple[int, int] | None, until_ms: int | None) -> bool:
    if range_ms is None and until_ms is None:
        return True
    if t is None:
        return False          # 시각을 모르는 원래 반응은 범위를 줄 때 세지 않는다
    if range_ms is not None and not (range_ms[0] <= t < range_ms[1]):
        return False
    if until_ms is not None and t > until_ms:
        return False
    return True


def _coerce_picks(picks) -> dict[str, int]:
    """picks: {chat_id: picked_vod_ms} 또는 [{"chat_id", "picked_vod_ms"}] (뒤의 것이 이긴다)."""
    if picks is None:
        return {}
    if isinstance(picks, Mapping):
        return dict(picks)
    out: dict[str, int] = {}
    for p in picks:
        if not isinstance(p, Mapping):
            raise TrainingError("고른 채팅 항목 형식이 아닙니다")
        out[p.get("chat_id")] = p.get("picked_vod_ms")
    return out


def _coerce_refs(refs) -> list[RefReaction]:
    """refs: RefReaction 또는 {"chat_id", "chat_vod_ms"?, "reaction_vod_ms"?, "grade"?} 의 나열."""
    out: list[RefReaction] = []
    for r in refs or ():
        if isinstance(r, RefReaction):
            out.append(r)
        elif isinstance(r, Mapping):
            ct, rt = r.get("chat_vod_ms"), r.get("reaction_vod_ms")
            out.append(RefReaction(r.get("chat_id"), ct if _is_int(ct) else None, rt if _is_int(rt) else None,
                                   r.get("grade") if isinstance(r.get("grade"), str) else None))
        else:
            raise TrainingError("원래 반응 항목 형식이 아닙니다")
    return out


def compare(picks, refs, *, range_ms: tuple[int, int] | None = None,
            until_ms: int | None = None, chat_times: Mapping[str, int] | None = None) -> Comparison:
    """고른 채팅과 원래 반응을 견준다.

    picks: {chat_id: picked_vod_ms} 또는 [{"chat_id", "picked_vod_ms"}].
    refs: RefReaction 또는 {"chat_id", "chat_vod_ms"?, "reaction_vod_ms"?, "grade"?} 의 나열.
    행 순서: 채팅 시각(모르면 고른 시각) → chat_id. 같은 입력이면 같은 결과다.
    chat_times: '나만' 행의 채팅 시각을 채울 때 쓴다(chat_id → chat_vod_ms). 없으면 None.
    """
    picks = _coerce_picks(picks)
    refs = _coerce_refs(refs)
    if range_ms is not None:
        a, b = range_ms
        if not (_is_int(a) and _is_int(b) and b > a):
            raise TrainingError("range_ms 는 (시작, 끝) 정수, 끝 > 시작")
        range_ms = (a, b)
    if until_ms is not None:
        _check_ms("until_ms", until_ms)
    for cid, ms in (picks or {}).items():
        _check_chat_id(cid)
        _check_ms("picked_vod_ms", ms)
    norm, dup = normalize_reference(refs)
    use = [r for r in norm if _in_range(r.chat_vod_ms, range_ms, until_ms)]
    excluded = len(norm) - len(use)
    ref_by = {r.chat_id: r for r in use}
    rows: list[CompareRow] = []
    diffs: list[int] = []
    for cid, pms in (picks or {}).items():
        r = ref_by.get(cid)
        if r is not None:
            d = pms - r.reaction_vod_ms if r.reaction_vod_ms is not None else None
            if d is not None:
                diffs.append(d)
            rows.append(CompareRow(SAME, cid, r.chat_vod_ms, pms, r.reaction_vod_ms, d))
        else:
            ct = (chat_times or {}).get(cid)
            rows.append(CompareRow(MINE_ONLY, cid, ct if _is_int(ct) else None, pms, None, None))
    for cid, r in ref_by.items():
        if cid not in (picks or {}):
            rows.append(CompareRow(ORIG_ONLY, cid, r.chat_vod_ms, None, r.reaction_vod_ms, None))
    rows.sort(key=lambda x: (x.chat_vod_ms if x.chat_vod_ms is not None else (x.picked_vod_ms or 0), x.chat_id))
    n_same = sum(1 for x in rows if x.status == SAME)
    n_ref = len(use)
    return Comparison(
        rows=rows, same=n_same, mine_only=sum(1 for x in rows if x.status == MINE_ONLY),
        orig_only=sum(1 for x in rows if x.status == ORIG_ONLY), reference_count=n_ref,
        hit_rate=(n_same / n_ref) if n_ref else None, median_diff_ms=median_int(diffs), diff_count=len(diffs),
        excluded_refs=excluded, duplicate_refs=dup, range_ms=range_ms, until_ms=until_ms)


# ----------------------------------------------------------------------------- 견주기 화면 도움
def next_orig_only(rows: Sequence[CompareRow], current: int | None, step: int = 1) -> int | None:
    """N(step=+1) / Shift+N(step=-1): current 다음(앞) '원래만' 행의 번호. 없으면 None. 끝에서 돌지 않는다.
    current 가 None 이면 처음(앞으로) 또는 끝(뒤로)부터 찾는다."""
    if step not in (1, -1):
        raise TrainingError("step 은 1 또는 -1")
    n = len(rows)
    if current is None:
        i = 0 if step == 1 else n - 1
    else:
        i = current + step
    while 0 <= i < n:
        if rows[i].status == ORIG_ONLY:
            return i
        i += step
    return None


def listen_start_ms(row: CompareRow, lead_ms: int = DEFAULT_LEAD_MS) -> int:
    """행을 누르면 소리를 켜고 재생을 시작할 자리: 채팅 시각·원래 반응 시각·고른 시각 가운데 가장 이른 것 − lead_ms
    (0 아래로는 내려가지 않는다). 셋 다 없으면 0."""
    _check_ms("lead_ms", lead_ms)
    ts = [t for t in (row.chat_vod_ms, row.reaction_vod_ms, row.picked_vod_ms) if _is_int(t)]
    if not ts:
        return 0
    return max(0, min(ts) - lead_ms)


__all__ = [
    "K_START", "K_PICK", "K_UNPICK", "K_COMPARE", "K_END", "KINDS", "SAME", "MINE_ONLY", "ORIG_ONLY", "STATUSES",
    "REFERENCE_GRADES", "REF_FINAL_SET", "REF_MANUAL_CSV", "REF_SOURCES", "DEFAULT_LEAD_MS", "TrainingError",
    "same_chat", "RefReaction", "normalize_reference", "reference_sha256", "toggle_pick", "picks_from_events",
    "CompareRow", "Comparison", "median_int", "compare", "next_orig_only", "listen_start_ms",
]
