"""생성 채팅 거르기 — G 채점기(viewer_score)를 H-3 생성 후보에 다시 쓴다 (20 6장, 08 D-20 1·D-22 3·D-23 4).

로컬 LLM 이 만든 가상 채팅 후보(scenario.chat 모양, source=generated)를 **보일 시각 순**으로 하나씩 G 채점기의 세 축으로
보고 거른다. 시청자 훈련이 '시청자가 쓴 글' 을 보던 눈으로 '가상 시청자가 쓴 글' 을 본다.

| 축 | 하는 일 |
|---|---|
| 주제 적합성 `topic` | **weak 면 뺀다**(그 시각 앞의 주제 흐름·전사 낱말과 겹침이 없음 · 낱말이 없음). 맥락이 없어 판단 못 함(`topic.no_context`)은 ok 라 남긴다 |
| 반복 여부 `repeat` | **weak 면 뺀다**(앞서 남긴 생성 줄과 같음·비슷함, 앞 `copy_window_ms` 의 실제·가져온 채팅 베낌, 스트리머 말 베낌, 같은 말버릇 되풀이) |
| 대화 확장성 `expand` | **기록만** 한다(단계·까닭 코드별 수). 거르지 않는다 — 리액션·짧은 감탄도 실제 채팅에 흔하다 |

- history(반복 여부의 '앞서 쓴 내 채팅')는 **앞서 남긴 생성 줄**이다. 걸러 낸 줄은 화면에 나오지 않으므로 넣지 않는다.
- 맥락(ScoreContext)은 부르는 쪽이 만든다: 주제 흐름·전사 낱말(시청자 훈련과 같은 파일, app/viewer_flow.build_context)과
  화면에 함께 흐를 채팅(실제 + retrieved 본문). 생성 줄은 맥락 chats 에 넣지 않는다(history 로 본다).
- 자리(quota): `plan_quota` 가 1분 창마다 넣을 수 있는 생성 수를 정한다 — 드문 창만, 창 목표(target_per_min)에서
  실제 + 이미 넣은 가상(retrieved)을 뺀 만큼, 구간 전체 가상 비율 상한(`max_virtual_ratio`)과 생성 비율 상한
  (`max_generated_ratio`, 생성 / (실제 + 가상))을 넘지 않게 앞 창부터 하나씩 돌려 나눈다(engine/retrieve.allocate 와 같은 방식).
  자리가 없는 후보는 채점하지 않고 `no_room` 으로 뺀다(history 에도 넣지 않는다).
- 결과·통계에는 **숫자·코드만** 있다(본문·낱말 없음, 20 8장 11). 후보 dict 는 고치지 않는다.
- 순수 모듈이다(표준 라이브러리 + app.core.viewer_score 만. 파일·시계·난수·네트워크 없음 — tests/test_layers.py).
- 같은 입력이면 같은 결과(후보는 (보일 시각, gen_id, 입력 차례) 순으로 본다).

예문은 모두 합성이다(시험: tests/test_gen_filter.py).
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from . import viewer_score as VS

#: 거르기 판. 거르는 축·코드·자리 규칙을 바꾸면 올린다(통계 filter 칸 'gscore-v1/<판>').
VERSION = "0.1"
FILTER_ID = "gscore-v1"
SCORER = f"viewer_score/{VS.VERSION}"

GENERATED = "generated"

# 빼는 까닭(채점 전). 채점으로 빼는 까닭은 viewer_score 의 weak 코드 그대로(topic.* · repeat.*)
DROP_BAD_TIME = "bad_time"        # 보일 시각이 없거나 구간 밖
DROP_BAD_TEXT = "bad_text"        # 본문이 문자열이 아니거나 비었음
DROP_NOT_GENERATED = "not_generated"
DROP_NO_ROOM = "no_room"          # 그 창에 넣을 자리가 없음(드문 창 아님 · 창 목표 찼음 · 비율 상한)

#: 채점으로 빼는 축(이 판의 규칙). 확장성은 기록만.
EXCLUDE_AXES = ("topic", "repeat")
RECORD_ONLY_AXES = ("expand",)
#: 빼는 축 안에서도 기록만 하는 까닭 코드(08 D-24 4 · D-25 5): 생성 줄의 '같은 말버릇 되풀이' 는 가짜 응답이 같은 틀이라
#: 지금은 비율을 잴 수 없어 빼지 않는다. 시범 뒤 실제 비율을 보고 다시 정한다.
RECORD_ONLY_CODES = ("repeat.habit",)


# ───────────────────────────────────────────────────────────── 자리 (창별 생성 수)

@dataclass
class GenQuota:
    """창별 남은 생성 자리. `plan_quota` 로 만든다. `room(t)`·`take(t)` 로 쓴다(filter_generated 가 부른다).

    windows: ((start_ms, end_ms), …) 시작 순. left: 창별 남은 자리. info: 숫자만(상한 계산)."""
    windows: tuple[tuple[int, int], ...]
    left: list[int]
    info: dict = field(default_factory=dict)

    def __post_init__(self):
        self._starts = tuple(a for a, _b in self.windows)

    def _index(self, t: int) -> int | None:
        i = bisect.bisect_right(self._starts, t) - 1
        if i < 0 or not (self.windows[i][0] <= t < self.windows[i][1]):
            return None
        return i

    def room(self, t: int) -> bool:
        i = self._index(t)
        return i is not None and self.left[i] > 0

    def take(self, t: int) -> None:
        i = self._index(t)
        if i is None or self.left[i] <= 0:
            raise ValueError("자리가 없는 창에 넣으려 했습니다")
        self.left[i] -= 1

    @property
    def total(self) -> int:
        return sum(self.left)


def _cap(ratio: float, base: int) -> int:
    """x / (base + x) ≤ ratio 를 만족하는 가장 큰 x (ratio 는 0 이상 1 미만)."""
    return max(0, math.floor(ratio * base / (1.0 - ratio) + 1e-9))


def plan_quota(windows: Iterable, virtual_times: Iterable[int] = (), *, max_virtual_ratio: float,
               max_generated_ratio: float | None = None) -> GenQuota:
    """창별 생성 자리.

    windows: engine.retrieve.Window(start_ms·end_ms·real·target·quiet 속성) 또는 (start_ms, end_ms, real, target, quiet).
    virtual_times: 이미 넣은 가상 줄(retrieved)의 보일 시각.
    max_virtual_ratio: 구간 전체 (retrieved + generated) / (실제 + 가상) 상한(0 이상 1 미만, header.fill 과 같은 값).
    max_generated_ratio: generated / (실제 + 가상) 상한(0 이상 1 미만). None 이면 이 상한은 없다.
    실제가 0 이면 자리도 0 이다(20 5-1 '실제 채팅이 하나도 없다' 는 첫 판에서 하지 않는다)."""
    for name, r in (("max_virtual_ratio", max_virtual_ratio), ("max_generated_ratio", max_generated_ratio)):
        if r is None and name == "max_generated_ratio":
            continue
        if isinstance(r, bool) or not isinstance(r, (int, float)) or not (0.0 <= float(r) < 1.0):
            raise ValueError(f"{name} 는 0 이상 1 미만")
    rows: list[tuple[int, int, int, int, bool]] = []
    for w in windows:
        if isinstance(w, (tuple, list)):
            a, b, real, target, quiet = w
        else:
            a, b, real, target, quiet = w.start_ms, w.end_ms, w.real, w.target, w.quiet
        rows.append((int(a), int(b), int(real), int(target), bool(quiet)))
    rows.sort()
    starts = [r[0] for r in rows]
    have = [0] * len(rows)
    n_virtual = 0
    for t in virtual_times:
        if isinstance(t, bool) or not isinstance(t, int):
            continue
        n_virtual += 1
        i = bisect.bisect_right(starts, t) - 1
        if 0 <= i < len(rows) and rows[i][0] <= t < rows[i][1]:
            have[i] += 1
    real_total = sum(r[2] for r in rows)
    need = [max(0, r[3] - r[2] - have[i]) if r[4] else 0 for i, r in enumerate(rows)]
    cap_virtual = _cap(float(max_virtual_ratio), real_total) if real_total else 0
    cap = max(0, cap_virtual - n_virtual)
    cap_generated = None
    if max_generated_ratio is not None:
        cap_generated = _cap(float(max_generated_ratio), real_total + n_virtual) if real_total else 0
        cap = min(cap, cap_generated)
    got, left = [0] * len(rows), cap
    while left > 0:          # 앞 창부터 하나씩 돌려 나눈다(한 창에 몰리지 않게)
        moved = False
        for i, k in enumerate(need):
            if left == 0:
                break
            if got[i] < k:
                got[i] += 1
                left -= 1
                moved = True
        if not moved:
            break
    info = {"real": real_total, "virtual_before": n_virtual, "need": sum(need), "cap_virtual": cap_virtual,
            "cap_generated": -1 if cap_generated is None else cap_generated, "room": sum(got),
            "quiet_windows": sum(1 for r in rows if r[4])}
    return GenQuota(tuple((r[0], r[1]) for r in rows), got, info)


# ───────────────────────────────────────────────────────────── 거르기

@dataclass(frozen=True)
class GenVerdict:
    """후보 하나의 판정. 본문은 담지 않는다. index = 입력 차례."""
    index: int
    keep: bool
    reason: str | None            # 뺀 까닭 코드(남겼으면 None)
    score: VS.ViewerScore | None  # 채점했으면 세 축 결과(단계·코드·숫자만)

    def as_dict(self) -> dict:
        d: dict = {"index": self.index, "keep": self.keep, "reason": self.reason}
        if self.score is not None:
            d["levels"] = self.score.levels()
            d["codes"] = {a.axis: a.code for a in self.score.axes()}
        return d


@dataclass
class GenFilterResult:
    kept: list[Mapping]           # 남긴 후보(원 dict 그대로, 보일 시각 순)
    verdicts: list[GenVerdict]    # 입력 차례대로
    stats: dict                   # 숫자·코드만


def show_time(cand: Mapping, range_ms: Sequence[int] | None = None) -> int | None:
    """보일 VOD 시각: show_vod_ms, 없으면 구간 시작 + show_rel_ms(app.core.scenario.show_vod_ms 와 같은 규칙)."""
    v = cand.get("show_vod_ms")
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    r = cand.get("show_rel_ms")
    if range_ms is not None and isinstance(r, int) and not isinstance(r, bool):
        return int(range_ms[0]) + r
    return None


def _weak_reason(score: VS.ViewerScore) -> str | None:
    for axis in EXCLUDE_AXES:
        a = getattr(score, axis)
        if a.level == VS.WEAK and a.code not in RECORD_ONLY_CODES:
            return a.code
    return None


def _empty_stats() -> dict:
    return {"filter": f"{FILTER_ID}/{VERSION}", "scorer": SCORER, "candidates": 0, "scored": 0, "kept": 0,
            "dropped": {}, "axis_weak": {"topic": 0, "repeat": 0, "both": 0},
            "expand": {lv: 0 for lv in VS.LEVELS}, "expand_codes": {},
            "expand_scored": {lv: 0 for lv in VS.LEVELS}, "topic_no_context": 0}


def filter_generated(candidates: Sequence[Mapping], ctx: VS.ScoreContext | None = None, *,
                     settings: VS.ViewerScoreSettings | None = None, quota: GenQuota | None = None,
                     range_ms: Sequence[int] | None = None) -> GenFilterResult:
    """생성 후보를 보일 시각 순으로 거른다.

    candidates: scenario.chat 모양 dict(source=generated, text, show_vod_ms|show_rel_ms, gen_id …). 고치지 않는다.
    ctx: `viewer_score.ScoreContext.build(topic_blocks, transcript, chats=실제+retrieved 본문, …)`. 없으면 빈 맥락
      (주제는 no_context → ok, 반복은 history 로만).
    quota: `plan_quota` 결과(쓰면 줄어든다). None 이면 자리 제한 없음.
    range_ms: 훈련 구간 [from, to). 주면 밖의 후보는 bad_time, show_rel_ms 도 푼다.
    """
    st = _empty_stats()
    s = settings or VS.DEFAULT_SETTINGS
    order = []
    for i, c in enumerate(candidates):
        t = show_time(c, range_ms) if isinstance(c, Mapping) else None
        gid = c.get("gen_id") if isinstance(c, Mapping) else None
        order.append((t if t is not None else -1, gid if isinstance(gid, str) else "", i))
    order.sort()
    verdicts: dict[int, GenVerdict] = {}
    kept: list[Mapping] = []
    history: list[str] = []
    dropped: dict[str, int] = {}

    def drop(i: int, why: str, score=None) -> None:
        dropped[why] = dropped.get(why, 0) + 1
        verdicts[i] = GenVerdict(i, False, why, score)

    for t, _gid, i in order:
        c = candidates[i]
        st["candidates"] += 1
        if not isinstance(c, Mapping) or c.get("source", GENERATED) != GENERATED:
            drop(i, DROP_NOT_GENERATED)
            continue
        text = c.get("text")
        if not isinstance(text, str) or not text.strip():
            drop(i, DROP_BAD_TEXT)
            continue
        if t < 0 or (range_ms is not None and not (int(range_ms[0]) <= t < int(range_ms[1]))):
            drop(i, DROP_BAD_TIME)
            continue
        if quota is not None and not quota.room(t):
            drop(i, DROP_NO_ROOM)
            continue
        score = VS.score_chat(text, t, ctx, history, settings=s)
        st["scored"] += 1
        st["expand_scored"][score.expand.level] += 1
        if score.topic.code == VS.TOPIC_NO_CONTEXT:
            st["topic_no_context"] += 1
        tw, rw = score.topic.level == VS.WEAK, score.repeat.level == VS.WEAK
        if tw:
            st["axis_weak"]["topic"] += 1
        if rw:
            st["axis_weak"]["repeat"] += 1
        if tw and rw:
            st["axis_weak"]["both"] += 1
        why = _weak_reason(score)
        if why is not None:
            drop(i, why, score)
            continue
        if quota is not None:
            quota.take(t)
        history.append(text)
        kept.append(c)
        verdicts[i] = GenVerdict(i, True, None, score)
        st["expand"][score.expand.level] += 1
        st["expand_codes"][score.expand.code] = st["expand_codes"].get(score.expand.code, 0) + 1
    st["kept"] = len(kept)
    st["dropped"] = dict(sorted(dropped.items()))
    st["expand_codes"] = dict(sorted(st["expand_codes"].items()))
    if quota is not None:
        st["quota"] = dict(quota.info)
    return GenFilterResult(kept, [verdicts[i] for i in range(len(candidates))], st)


def dropped_by_axis(stats: Mapping) -> dict[str, int]:
    """통계의 dropped 를 시나리오 머리 filters.dropped 에 적을 묶음으로: gscore_topic · gscore_repeat · no_room · 그 밖.
    (낱말 없이 숫자만. 칸 이름은 'gen_' 앞머리로 retrieved 쪽 수와 섞이지 않게 부르는 쪽이 붙인다.)"""
    out = {"gscore_topic": 0, "gscore_repeat": 0}
    for code, n in (stats.get("dropped") or {}).items():
        if code.startswith("topic."):
            out["gscore_topic"] += int(n)
        elif code.startswith("repeat."):
            out["gscore_repeat"] += int(n)
        else:
            out[code] = out.get(code, 0) + int(n)
    return out


__all__ = ["VERSION", "FILTER_ID", "SCORER", "GENERATED", "DROP_BAD_TIME", "DROP_BAD_TEXT", "DROP_NOT_GENERATED",
           "DROP_NO_ROOM", "EXCLUDE_AXES", "RECORD_ONLY_AXES", "GenQuota", "plan_quota", "GenVerdict",
           "GenFilterResult", "show_time", "filter_generated", "dropped_by_axis"]
