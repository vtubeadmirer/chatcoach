"""활동 구간 모델 (Qt 없음) — 경계 → 빈틈없는 구간, 속성, 병합 제안, 경계 완충, undo/redo.

설계: docs/design/03-data-model.md 5-1(활동 분류)·5-3(확신도)·6-5(segment)·10-3(구간 편집),
안내서 docs/labeling-guide/v0.1.md 3장, 시작값 schemas/guide-config.schema.json `segment`.
ClipSlicer app/segments.py 의 '마커 → 빈틈없는 구간' 과 'undo 200단계' 골격을 개작했다.
ClipSlicer 와 달리 (S-13)
  - 구간 속성은 시작 시각 키가 아니라 구간 고정 ID(entity_id)에 붙는다. 경계를 옮겨도 속성이 그대로 따라간다.
  - 영상 길이 밖 경계는 조용히 버리지 않는다. 새 경계는 예외(OutOfMediaError), 불러온 기록은 out_of_media 로 표시해 남긴다.

규칙 (core 층, 02 3-1): Qt·파일·네트워크·시계·난수를 쓰지 않는다. 새 구간 ID 는 생성자에 받은 `new_id()` 로 만든다.
시각은 모두 VOD 시각 정수 ms. 구간은 반열린 구간 [start_ms, end_ms).

모델
  - `segments`: [0, duration_ms) 를 빈틈·겹침 없이 덮는 구간 목록(시간순). 이 불변식은 모든 조작 뒤에 지켜진다.
    category 가 None 인 구간은 '아직 안 매긴 시간' 이다(03 6-5). 기록(to_records)에는 다 매긴 구간만 나간다.
  - `outside`: 시작부터 영상 길이 밖인 기록 구간(영상이 바뀐 경우). 타임라인에는 없고, 고치지 않고 그대로 보존한다.
  - 영상 끝(또는 0 앞)에 걸친 기록 구간은 타임라인에서는 잘라 보이되, 잘린 쪽의 원래 값(orig_start_ms/orig_end_ms)을
    그대로 갖고 있다가 to_records 에서 되돌려 쓴다. out_of_media = True.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------- 열거값 (03 5-1, 5-3, label-event 스키마)
CATEGORIES: tuple[str, ...] = ("just_chatting", "game", "singing", "other", "undeterminable")
CONFIDENCES: tuple[str, ...] = ("certain", "probably", "undeterminable")
START_SIGNALS: tuple[str | None, ...] = ("screen", "audio", "speech", "action", "unknown", None)

# ---------------------------------------------------------------------------- 시작값 (guide-config v0.1)
DEFAULT_BUFFER_MS = 10_000        # segment.boundary_buffer_sec = 10 (전환 앞뒤 각각)
DEFAULT_MIN_LENGTH_MS = 30_000    # segment.min_length_sec = 30
DEFAULT_MIN_GAP_MS = 100          # 두 경계 사이 최소 간격(구간 최소 길이). 안내서 값이 아니라 조작 실수 방지용
UNDO_LIMIT = 200
RATIONALE_MAX = 300               # label-event 스키마 rationale maxLength

_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_MS_MIN, _MS_MAX = -3_600_000, 172_800_000   # label-event 스키마 $defs.ms


# ---------------------------------------------------------------------------- 예외
class SegmentError(ValueError):
    """구간 조작이 거절됨. 모델은 바뀌지 않는다."""


class OutOfMediaError(SegmentError):
    """경계가 영상 길이 (0, duration_ms) 밖이다."""


class BoundaryTooCloseError(SegmentError):
    """경계가 이웃 경계와 min_gap_ms 보다 가깝다(길이 0 또는 너무 짧은 구간이 생김)."""


class SegmentOverlapError(SegmentError):
    """불러온 기록 구간끼리 겹친다. `overlaps` = [(앞 entity_id, 뒤 entity_id), …]."""

    def __init__(self, msg: str, overlaps: list[tuple[str, str]]):
        super().__init__(msg)
        self.overlaps = overlaps


class IncompleteSegmentError(SegmentError):
    """분류·확신도·근거 중 빠진 것이 있어 기록 레코드를 만들 수 없다."""


# ---------------------------------------------------------------------------- 값 객체
@dataclass(frozen=True)
class Segment:
    """구간 한 개. 바꿀 수 없는 값(undo 스냅샷을 싸게 만들려고). 고칠 때는 dataclasses.replace 로 새 값을 만든다."""
    id: str
    start_ms: int
    end_ms: int
    category: str | None = None
    confidence: str | None = None
    rationale: str = ""
    active_chat: bool | None = None
    start_signal: str | None = None
    long_silence_reviewed: bool | None = None
    split_from: str | None = None
    merged_from: tuple[str, ...] = ()
    orig_start_ms: int | None = None   # 0 앞에서 잘린 기록 구간의 원래 시작 (start_ms == 0 일 때만 의미)
    orig_end_ms: int | None = None     # 영상 끝에서 잘린 기록 구간의 원래 끝 (end_ms == duration 일 때만 의미)
    outside: bool = False              # 시작부터 영상 길이 밖(타임라인에 없음)

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def out_of_media(self) -> bool:
        return self.outside or self.orig_start_ms is not None or self.orig_end_ms is not None

    @property
    def is_complete(self) -> bool:
        """기록 레코드로 낼 수 있는가 (스키마 필수: category·confidence·rationale)."""
        return self.category is not None and self.confidence is not None and bool(self.rationale)

    @property
    def is_labeled(self) -> bool:
        return self.category is not None

    def contains(self, t_ms: int) -> bool:
        return self.start_ms <= t_ms < self.end_ms

    def record_span(self) -> tuple[int, int]:
        """기록에 쓸 [시작, 끝) — 잘린 쪽은 원래 값."""
        s = self.orig_start_ms if self.orig_start_ms is not None else self.start_ms
        e = self.orig_end_ms if self.orig_end_ms is not None else self.end_ms
        return s, e

    def body(self) -> dict:
        """03 6-5 / label-event 스키마 segmentBody 모양."""
        s, e = self.record_span()
        return {
            "start_ms": s,
            "end_ms": e,
            "category": self.category,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "active_chat": self.active_chat,
            "start_signal": self.start_signal,
            "long_silence_reviewed": self.long_silence_reviewed,
            "split_from": self.split_from,
            "merged_from": list(self.merged_from),
            "out_of_media": self.out_of_media,
        }


@dataclass(frozen=True)
class MergeSuggestion:
    """30초(시작값) 미만 구간 합치기 제안. 자동으로 합치지 않는다(03 6-5 `segment_too_short` 경고)."""
    segment_id: str
    length_ms: int
    prev_id: str | None          # 앞 구간 (없으면 None)
    next_id: str | None          # 뒤 구간
    suggested: str | None        # 'prev' | 'next' — 같은 분류인 이웃 우선, 아니면 앞 구간, 앞이 없으면 뒤


@dataclass(frozen=True)
class BufferRange:
    """경계 완충 [start_ms, end_ms). 가까운 경계 둘의 완충이 겹치면 하나로 합친다. boundaries = 포함된 경계 시각."""
    start_ms: int
    end_ms: int
    boundaries: tuple[int, ...]

    def contains(self, t_ms: int) -> bool:
        return self.start_ms <= t_ms < self.end_ms


@dataclass(frozen=True)
class Edit:
    """두 상태의 차이 — store 가 10-3 표대로 기록 줄을 쓰는 데 쓴다.

    updated: 같은 entity_id 로 새 판이 필요한 구간 id (내용이 바뀜)
    created: 새 대상 id (쪼개기로 생김, 또는 redo/undo 로 되살아남)
    retracted: [(id, merged_into 또는 None)] — 합치기면 merged_into = 남은 구간 id
    순서는 시간순. 다 매기지 않은(incomplete) 구간도 포함되니, 기록할 때 is_complete 를 확인한다.
    """
    updated: tuple[str, ...] = ()
    created: tuple[str, ...] = ()
    retracted: tuple[tuple[str, str | None], ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.updated or self.created or self.retracted)


@dataclass(frozen=True)
class RecordIssue:
    """from_records / check_records 가 찾은 문제. kind = 'overlap' | 'gap' | 'out_of_media'."""
    kind: str
    start_ms: int
    end_ms: int
    ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------- 시작값 변환
@dataclass(frozen=True)
class SegmentThresholds:
    buffer_ms: int = DEFAULT_BUFFER_MS
    min_length_ms: int = DEFAULT_MIN_LENGTH_MS


def thresholds_from_guide(segment_cfg: Mapping) -> SegmentThresholds:
    """guide-config 의 `thresholds.segment` 절(초 단위) → ms 정수. 값 검증은 core/thresholds 몫이고 여기선 형만 본다."""
    def ms(key: str) -> int:
        v = segment_cfg[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise TypeError(f"{key} 는 숫자여야 한다: {v!r}")
        return int(round(float(v) * 1000))
    return SegmentThresholds(buffer_ms=ms("boundary_buffer_sec"), min_length_ms=ms("min_length_sec"))


# ---------------------------------------------------------------------------- 검사 도우미
def _check_ms(name: str, v) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"{name} 는 정수 ms 여야 한다: {v!r}")
    return v


def _check_uuid(v) -> str:
    if not isinstance(v, str) or not _UUID4_RE.match(v):
        raise SegmentError(f"구간 ID 가 소문자 UUID v4 가 아니다: {v!r}")
    return v


def _check_props(category, confidence, rationale, active_chat, start_signal, long_silence_reviewed) -> None:
    if category is not None and category not in CATEGORIES:
        raise SegmentError(f"알 수 없는 분류: {category!r}")
    if confidence is not None and confidence not in CONFIDENCES:
        raise SegmentError(f"알 수 없는 확신도: {confidence!r}")
    if not isinstance(rationale, str):
        raise SegmentError("근거는 문자열이어야 한다")
    if len(rationale) > RATIONALE_MAX:
        raise SegmentError(f"근거가 {RATIONALE_MAX}자를 넘는다")
    if active_chat is not None:
        if not isinstance(active_chat, bool):
            raise SegmentError("active_chat 은 True/False/None")
        if category != "game":
            raise SegmentError("'채팅 대화 활발'(active_chat) 은 게임 구간에만 쓴다")
    if start_signal not in START_SIGNALS:
        raise SegmentError(f"알 수 없는 경계 신호: {start_signal!r}")
    if long_silence_reviewed is not None and not isinstance(long_silence_reviewed, bool):
        raise SegmentError("long_silence_reviewed 는 True/False/None")


_LABEL_FIELDS = ("category", "confidence", "rationale", "active_chat")
_UNSET = object()


def diff_segments(before: Sequence[Segment], after: Sequence[Segment]) -> Edit:
    """두 구간 상태(타임라인 + outside)의 차이. 합치기는 남은 구간의 merged_from 이 늘어난 것으로 알아낸다."""
    b = {s.id: s for s in before}
    a = {s.id: s for s in after}
    merged_into: dict[str, str] = {}
    for s in after:
        old = b.get(s.id)
        prev_merged = set(old.merged_from) if old else set()
        for m in s.merged_from:
            if m not in prev_merged:
                merged_into[m] = s.id
    updated = tuple(s.id for s in after if s.id in b and b[s.id] != s)
    created = tuple(s.id for s in after if s.id not in b)
    retracted = tuple((s.id, merged_into.get(s.id)) for s in before if s.id not in a)
    return Edit(updated=updated, created=created, retracted=retracted)


# ---------------------------------------------------------------------------- 본체
class SegmentTimeline:
    """영상 한 편의 활동 구간 편집 상태 (한 세션의 현재 유효 구간).

    조작 함수는 성공하면 Edit(바뀐 것)를 돌려주고 undo 스택에 한 단계를 쌓는다.
    거절하면 SegmentError 계열 예외를 내고 아무것도 바꾸지 않는다(undo 에도 쌓지 않는다).
    """

    def __init__(self, duration_ms: int, new_id: Callable[[], str], *,
                 min_gap_ms: int = DEFAULT_MIN_GAP_MS, undo_limit: int = UNDO_LIMIT,
                 _segments: Sequence[Segment] | None = None, _outside: Sequence[Segment] = ()):
        _check_ms("duration_ms", duration_ms)
        if duration_ms <= 0:
            raise SegmentError("duration_ms 는 0보다 커야 한다")
        _check_ms("min_gap_ms", min_gap_ms)
        if min_gap_ms < 1:
            raise SegmentError("min_gap_ms 는 1 이상")
        self.duration_ms = duration_ms
        self.min_gap_ms = min_gap_ms
        self.undo_limit = undo_limit
        self._new_id = new_id
        if _segments is None:
            _segments = (Segment(id=self._make_id(set()), start_ms=0, end_ms=duration_ms),)
        self._segs: tuple[Segment, ...] = tuple(_segments)
        self._outside: tuple[Segment, ...] = tuple(_outside)
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        self._check_invariant()

    # ------------------------------------------------------------------ 읽기
    @property
    def segments(self) -> tuple[Segment, ...]:
        return self._segs

    @property
    def outside(self) -> tuple[Segment, ...]:
        """시작부터 영상 길이 밖인 기록 구간 (보존만 한다)."""
        return self._outside

    @property
    def boundaries(self) -> list[int]:
        """안쪽 경계(구간 전환 시각) 목록. 0 과 duration 은 포함하지 않는다."""
        return [s.start_ms for s in self._segs[1:]]

    def __len__(self) -> int:
        return len(self._segs)

    def __iter__(self):
        return iter(self._segs)

    def index_of(self, seg_id: str) -> int:
        for i, s in enumerate(self._segs):
            if s.id == seg_id:
                return i
        raise KeyError(seg_id)

    def get(self, seg_id: str) -> Segment:
        for s in self._segs + self._outside:
            if s.id == seg_id:
                return s
        raise KeyError(seg_id)

    def index_at(self, t_ms: int) -> int | None:
        """t 를 포함하는 구간 번호. [0, duration) 밖이면 None (끝 시각 duration 자체도 밖이다)."""
        _check_ms("t_ms", t_ms)
        if t_ms < 0 or t_ms >= self.duration_ms:
            return None
        starts = [s.start_ms for s in self._segs]
        return bisect.bisect_right(starts, t_ms) - 1

    def segment_at(self, t_ms: int) -> Segment | None:
        i = self.index_at(t_ms)
        return None if i is None else self._segs[i]

    def nearest_boundary(self, t_ms: int, tol_ms: int) -> int | None:
        """t 에서 tol_ms 안의 가장 가까운 안쪽 경계 번호(boundaries 의 index). 없으면 None. 같은 거리면 앞쪽."""
        bs = self.boundaries
        if not bs:
            return None
        j = bisect.bisect_left(bs, t_ms)
        best, best_d = None, tol_ms
        for k in (j - 1, j):
            if 0 <= k < len(bs):
                d = abs(bs[k] - t_ms)
                if d <= best_d and (best is None or d < best_d):
                    best, best_d = k, d
        return best

    def unlabeled(self) -> list[Segment]:
        """아직 분류를 매기지 않은 구간."""
        return [s for s in self._segs if not s.is_labeled]

    def incomplete(self) -> list[Segment]:
        """기록으로 낼 수 없는(분류·확신도·근거 중 빠진 것이 있는) 구간."""
        return [s for s in self._segs if not s.is_complete]

    # ------------------------------------------------------------------ 불변식
    def _check_invariant(self, segs: Sequence[Segment] | None = None) -> None:
        segs = self._segs if segs is None else segs
        if not segs:
            raise AssertionError("구간이 하나도 없다")
        if segs[0].start_ms != 0 or segs[-1].end_ms != self.duration_ms:
            raise AssertionError("구간이 [0, duration) 을 덮지 않는다")
        seen: set[str] = set()
        for a, b in zip(segs, segs[1:]):
            if a.end_ms != b.start_ms:
                raise AssertionError(f"빈틈 또는 겹침: {a.end_ms} != {b.start_ms}")
        for s in segs:
            if s.end_ms - s.start_ms < 1:
                raise AssertionError("길이 0 이하 구간")
            if s.id in seen:
                raise AssertionError(f"중복 ID {s.id}")
            seen.add(s.id)
        for s in self._outside:
            if s.id in seen:
                raise AssertionError(f"중복 ID {s.id}")
            seen.add(s.id)

    def check(self) -> None:
        """불변식 검사(테스트·디버그용). 깨졌으면 AssertionError."""
        self._check_invariant()

    def _make_id(self, used: set[str]) -> str:
        v = _check_uuid(self._new_id())
        if v in used:
            raise SegmentError(f"new_id() 가 이미 있는 ID 를 돌려줬다: {v}")
        return v

    def _used_ids(self) -> set[str]:
        return {s.id for s in self._segs} | {s.id for s in self._outside}

    # ------------------------------------------------------------------ undo / redo
    def _snapshot(self) -> tuple:
        return (self._segs, self._outside)

    def _all(self, snap: tuple) -> tuple[Segment, ...]:
        return snap[0] + snap[1]

    def _commit(self, segs: Sequence[Segment], outside: Sequence[Segment] | None = None) -> Edit:
        new_segs = tuple(segs)
        new_out = self._outside if outside is None else tuple(outside)
        before = self._snapshot()
        old_out = self._outside
        self._outside = new_out
        try:
            self._check_invariant(new_segs)
        except AssertionError:
            self._outside = old_out
            raise
        self._segs = new_segs
        self._undo.append(before)
        if len(self._undo) > self.undo_limit:
            del self._undo[0]
        self._redo.clear()
        return diff_segments(self._all(before), self._all(self._snapshot()))

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> Edit | None:
        """직전 조작을 되돌린다. 되돌릴 것이 없으면 None. 돌려주는 Edit 는 되돌린 결과의 차이(10-1 Ctrl+Z 기록용)."""
        if not self._undo:
            return None
        cur = self._snapshot()
        prev = self._undo.pop()
        self._redo.append(cur)
        self._segs, self._outside = prev
        return diff_segments(self._all(cur), self._all(prev))

    def redo(self) -> Edit | None:
        if not self._redo:
            return None
        cur = self._snapshot()
        nxt = self._redo.pop()
        self._undo.append(cur)
        if len(self._undo) > self.undo_limit:
            del self._undo[0]
        self._segs, self._outside = nxt
        return diff_segments(self._all(cur), self._all(nxt))

    # ------------------------------------------------------------------ 경계 조작
    def _check_boundary_time(self, t_ms: int) -> None:
        _check_ms("t_ms", t_ms)
        if t_ms <= 0 or t_ms >= self.duration_ms:
            raise OutOfMediaError(f"경계 {t_ms}ms 가 영상 길이 (0, {self.duration_ms}) 밖이다")

    def add_boundary(self, t_ms: int, *, inherit: bool = True, start_signal: str | None = None) -> Edit:
        """t 에 경계를 넣어 그 자리 구간 A 를 A(끝 줄임) + C(새 대상, split_from = A) 로 쪼갠다 (10-3 쪼개기).

        inherit=True 면 C 가 A 의 분류·확신도·근거·active_chat 을 물려받는다(라벨러가 고칠 것을 전제한 초기값).
        start_signal 은 C 의 시작 경계 신호. A 의 long_silence_reviewed 는 물려주지 않는다.
        """
        self._check_boundary_time(t_ms)
        if start_signal not in START_SIGNALS:
            raise SegmentError(f"알 수 없는 경계 신호: {start_signal!r}")
        i = self.index_at(t_ms)
        a = self._segs[i]
        if t_ms - a.start_ms < self.min_gap_ms or a.end_ms - t_ms < self.min_gap_ms:
            raise BoundaryTooCloseError(f"경계 {t_ms}ms 가 이웃 경계와 {self.min_gap_ms}ms 보다 가깝다")
        new_id = self._make_id(self._used_ids())
        left = replace(a, end_ms=t_ms, orig_end_ms=None)
        right = Segment(
            id=new_id, start_ms=t_ms, end_ms=a.end_ms,
            category=a.category if inherit else None,
            confidence=a.confidence if inherit else None,
            rationale=a.rationale if inherit else "",
            active_chat=a.active_chat if inherit else None,
            start_signal=start_signal,
            split_from=a.id,
            orig_end_ms=a.orig_end_ms,
        )
        segs = list(self._segs)
        segs[i:i + 1] = [left, right]
        return self._commit(segs)

    def add_boundaries(self, times: Iterable[int], **kw) -> Edit:
        """여러 경계를 한 번의 조작(undo 한 단계)으로 넣는다. 하나라도 거절되면 전부 넣지 않는다."""
        before_snap = self._snapshot()
        saved_undo, saved_redo = list(self._undo), list(self._redo)
        try:
            for t in sorted(times):
                self.add_boundary(t, **kw)
        except Exception:
            self._segs, self._outside = before_snap
            self._undo, self._redo = saved_undo, saved_redo
            raise
        if self._snapshot() == before_snap:
            self._undo, self._redo = saved_undo, saved_redo
            return Edit()
        self._undo = saved_undo
        self._redo.clear()
        self._undo.append(before_snap)
        if len(self._undo) > self.undo_limit:
            del self._undo[0]
        return diff_segments(self._all(before_snap), self._all(self._snapshot()))

    def move_boundary(self, index: int, t_ms: int, *, clamp: bool = False) -> Edit:
        """안쪽 경계 index(boundaries 기준)를 t 로 옮긴다 (10-3 경계 이동: 앞 구간 끝 + 뒤 구간 시작).

        이웃 경계를 넘거나 min_gap_ms 보다 가까워지면 clamp=False 는 예외, clamp=True 는 허용 범위 끝에 붙인다.
        영상 길이 밖으로는 clamp 여부와 상관없이 OutOfMediaError.
        """
        bs = self.boundaries
        if not 0 <= index < len(bs):
            raise SegmentError(f"경계 번호 {index} 가 없다")
        self._check_boundary_time(t_ms)
        a, b = self._segs[index], self._segs[index + 1]
        lo, hi = a.start_ms + self.min_gap_ms, b.end_ms - self.min_gap_ms
        if lo > hi:
            raise BoundaryTooCloseError("이 경계는 움직일 자리가 없다")
        if t_ms < lo or t_ms > hi:
            if not clamp:
                raise BoundaryTooCloseError(f"경계를 {t_ms}ms 로 옮기면 이웃 경계와 너무 가깝거나 넘는다 (허용 {lo}~{hi})")
            t_ms = min(max(t_ms, lo), hi)
        if t_ms == b.start_ms:
            return Edit()
        segs = list(self._segs)
        segs[index] = replace(a, end_ms=t_ms)
        segs[index + 1] = replace(b, start_ms=t_ms)
        return self._commit(segs)

    def remove_boundary(self, index: int, *, keep: str = "left") -> Edit:
        """안쪽 경계 index 를 지워 맞닿은 두 구간을 합친다 (10-3 합치기 A ← B).

        keep='left' 면 앞 구간 A 가 남아 끝이 늘고 merged_from 에 B 가 붙으며, B 는 retract(merged, merged_into=A).
        keep='right' 면 뒤 구간이 남아 시작이 당겨진다(속성은 남는 구간 것). start_signal 은 앞 구간 것을 쓴다.
        """
        bs = self.boundaries
        if not 0 <= index < len(bs):
            raise SegmentError(f"경계 번호 {index} 가 없다")
        if keep not in ("left", "right"):
            raise SegmentError("keep 은 'left' 또는 'right'")
        a, b = self._segs[index], self._segs[index + 1]
        survivor, gone = (a, b) if keep == "left" else (b, a)
        merged = tuple(survivor.merged_from) + tuple(m for m in (gone.id,) + gone.merged_from
                                                     if m not in survivor.merged_from)
        new = replace(survivor, start_ms=a.start_ms, end_ms=b.end_ms,
                      start_signal=a.start_signal,
                      orig_start_ms=a.orig_start_ms, orig_end_ms=b.orig_end_ms,
                      merged_from=merged)
        segs = list(self._segs)
        segs[index:index + 2] = [new]
        return self._commit(segs)

    def merge_into(self, seg_id: str, neighbor: str) -> Edit:
        """구간 seg_id 를 이웃(neighbor='prev'|'next')에 합친다. 이웃이 남고 seg_id 는 merged 로 철회된다.
        병합 제안(MergeSuggestion.suggested)을 사람이 받아들일 때 쓴다."""
        i = self.index_of(seg_id)
        if neighbor == "prev":
            if i == 0:
                raise SegmentError("앞 구간이 없다")
            return self.remove_boundary(i - 1, keep="left")
        if neighbor == "next":
            if i == len(self._segs) - 1:
                raise SegmentError("뒤 구간이 없다")
            return self.remove_boundary(i, keep="right")
        raise SegmentError("neighbor 는 'prev' 또는 'next'")

    # ------------------------------------------------------------------ 속성
    def set_props(self, seg_id: str, *, category=_UNSET, confidence=_UNSET, rationale=_UNSET,
                  active_chat=_UNSET, start_signal=_UNSET, long_silence_reviewed=_UNSET) -> Edit:
        """구간 속성을 바꾼다 (10-3 분류·확신도만 변경 → 새 판). 넘기지 않은 값은 그대로.

        분류를 게임이 아닌 것으로 바꾸면서 active_chat 을 넘기지 않으면 active_chat 은 None 으로 비운다
        (스키마: 게임이 아니면 null). 게임이 아닌데 active_chat 을 True/False 로 넘기면 예외.
        rationale 은 앞뒤 공백을 지운다.
        """
        i = self.index_of(seg_id)
        s = self._segs[i]
        cat = s.category if category is _UNSET else category
        ac = s.active_chat if active_chat is _UNSET else active_chat
        if active_chat is _UNSET and cat != "game":
            ac = None
        rat = s.rationale if rationale is _UNSET else rationale
        if isinstance(rat, str):
            rat = rat.strip()
        conf = s.confidence if confidence is _UNSET else confidence
        sig = s.start_signal if start_signal is _UNSET else start_signal
        lsr = s.long_silence_reviewed if long_silence_reviewed is _UNSET else long_silence_reviewed
        _check_props(cat, conf, rat, ac, sig, lsr)
        new = replace(s, category=cat, confidence=conf, rationale=rat, active_chat=ac,
                      start_signal=sig, long_silence_reviewed=lsr)
        if new == s:
            return Edit()
        segs = list(self._segs)
        segs[i] = new
        return self._commit(segs)

    def discard_outside(self, seg_id: str) -> Edit:
        """영상 길이 밖 보존 구간을 사람이 확인하고 지운다(store 는 retract 로 기록). undo 가능."""
        out = [s for s in self._outside if s.id != seg_id]
        if len(out) == len(self._outside):
            raise KeyError(seg_id)
        return self._commit(self._segs, out)

    # ------------------------------------------------------------------ 병합 제안 · 경계 완충
    def short_segments(self, min_length_ms: int = DEFAULT_MIN_LENGTH_MS) -> list[MergeSuggestion]:
        """min_length_ms 보다 짧은 구간마다 합치기 제안. 구간이 하나뿐이면(영상 자체가 짧음) 제안하지 않는다.

        추천 방향: 같은 분류인 이웃 → 앞 구간 → 뒤 구간 순. 분류를 안 매긴 구간끼리는 '같은 분류' 로 보지 않는다.
        """
        _check_ms("min_length_ms", min_length_ms)
        out: list[MergeSuggestion] = []
        n = len(self._segs)
        if n < 2:
            return out
        for i, s in enumerate(self._segs):
            if s.length_ms >= min_length_ms:
                continue
            prev = self._segs[i - 1] if i > 0 else None
            nxt = self._segs[i + 1] if i + 1 < n else None
            same_prev = prev is not None and s.category is not None and prev.category == s.category
            same_next = nxt is not None and s.category is not None and nxt.category == s.category
            if same_prev:
                sug = "prev"
            elif same_next:
                sug = "next"
            elif prev is not None:
                sug = "prev"
            else:
                sug = "next"
            out.append(MergeSuggestion(segment_id=s.id, length_ms=s.length_ms,
                                       prev_id=prev.id if prev else None,
                                       next_id=nxt.id if nxt else None, suggested=sug))
        return out

    def buffer_ranges(self, buffer_ms: int = DEFAULT_BUFFER_MS) -> list[BufferRange]:
        """안쪽 경계마다 [b - buffer, b + buffer) 를 완충으로 (안내서 3장 '전환 앞뒤 10초'). 영상 범위로 자르고,
        겹치거나 맞닿은 완충은 하나로 합친다. 0 과 duration(영상 처음·끝)은 전환이 아니라 완충을 두지 않는다."""
        _check_ms("buffer_ms", buffer_ms)
        if buffer_ms < 0:
            raise SegmentError("buffer_ms 는 0 이상")
        if buffer_ms == 0:
            return []
        out: list[BufferRange] = []
        for b in self.boundaries:
            lo, hi = max(0, b - buffer_ms), min(self.duration_ms, b + buffer_ms)
            if out and lo <= out[-1].end_ms:
                last = out[-1]
                out[-1] = BufferRange(last.start_ms, max(last.end_ms, hi), last.boundaries + (b,))
            else:
                out.append(BufferRange(lo, hi, (b,)))
        return out

    def in_buffer(self, t_ms: int, buffer_ms: int = DEFAULT_BUFFER_MS) -> bool:
        """t 가 어느 경계의 완충 안인가 (b - buffer <= t < b + buffer, buffer_ranges 와 같은 반열린 구간). 채팅 반응 평가 제외 판정용."""
        _check_ms("t_ms", t_ms)
        bs = self.boundaries
        if not bs or buffer_ms <= 0:
            return False
        j = bisect.bisect_left(bs, t_ms)
        for k in (j - 1, j):
            if 0 <= k < len(bs) and bs[k] - buffer_ms <= t_ms < bs[k] + buffer_ms:
                return True
        return False

    # ------------------------------------------------------------------ 기록 레코드
    def record(self, seg_id: str) -> dict:
        """구간 하나의 기록 레코드 {'kind': 'segment', 'entity_id', 'body'}. 봉투의 나머지(id·seq·supersedes·
        기준서 태그 등)는 store 가 채운다(03 6-3). 다 매기지 않았으면 IncompleteSegmentError."""
        s = self.get(seg_id)
        if not s.is_complete:
            raise IncompleteSegmentError(f"구간 {seg_id} 는 분류·확신도·근거가 다 있지 않다")
        return {"kind": "segment", "entity_id": s.id, "body": s.body()}

    def to_records(self, *, strict: bool = False) -> list[dict]:
        """현재 유효 구간 → 03 6-5 segment 레코드 목록 (시간순, 영상 밖 보존 구간은 뒤에).

        다 매기지 않은 구간은 '아직 안 매긴 시간' 이라 내지 않는다. strict=True 면 그런 구간이 있을 때 예외.
        """
        if strict and self.incomplete():
            raise IncompleteSegmentError(f"다 매기지 않은 구간 {len(self.incomplete())}개")
        out = [{"kind": "segment", "entity_id": s.id, "body": s.body()} for s in self._segs if s.is_complete]
        out += [{"kind": "segment", "entity_id": s.id, "body": s.body()} for s in self._outside]
        return out

    @staticmethod
    def _parse_record(rec: Mapping) -> Segment:
        if not isinstance(rec, Mapping):
            raise SegmentError("레코드가 dict 가 아니다")
        kind = rec.get("kind", "segment")
        if kind != "segment":
            raise SegmentError(f"segment 가 아닌 레코드: {kind!r}")
        sid = _check_uuid(rec.get("entity_id"))
        body = rec.get("body")
        if not isinstance(body, Mapping):
            raise SegmentError(f"{sid}: body 가 없다")
        for k in ("start_ms", "end_ms", "category", "confidence", "rationale"):
            if k not in body:
                raise SegmentError(f"{sid}: body.{k} 가 없다")
        s, e = body["start_ms"], body["end_ms"]
        for name, v in (("start_ms", s), ("end_ms", e)):
            if isinstance(v, bool) or not isinstance(v, int) or not _MS_MIN <= v <= _MS_MAX:
                raise SegmentError(f"{sid}: {name} 가 범위 안 정수 ms 가 아니다: {v!r}")
        if s >= e:
            raise SegmentError(f"{sid}: start_ms >= end_ms")
        cat, conf, rat = body["category"], body["confidence"], body["rationale"]
        if cat is None or conf is None or not isinstance(rat, str) or not rat:
            raise SegmentError(f"{sid}: 분류·확신도·근거가 비어 있다")
        ac = body.get("active_chat")
        sig = body.get("start_signal")
        lsr = body.get("long_silence_reviewed")
        try:
            _check_props(cat, conf, rat, ac, sig, lsr)
        except SegmentError as ex:
            raise SegmentError(f"{sid}: {ex}") from None
        split_from = body.get("split_from")
        if split_from is not None:
            _check_uuid(split_from)
        merged = body.get("merged_from") or []
        if not isinstance(merged, (list, tuple)):
            raise SegmentError(f"{sid}: merged_from 이 목록이 아니다")
        for m in merged:
            _check_uuid(m)
        return Segment(id=sid, start_ms=s, end_ms=e, category=cat, confidence=conf, rationale=rat,
                       active_chat=ac, start_signal=sig, long_silence_reviewed=lsr,
                       split_from=split_from, merged_from=tuple(merged))

    @staticmethod
    def check_records(duration_ms: int, records: Iterable[Mapping]) -> list[RecordIssue]:
        """기록 구간 묶음의 겹침·빈틈·영상 밖을 찾는다 (10-3 '다음에 열 때 검사' → '겹침/빈틈 고치기' 화면용).
        레코드 형식이 틀리면 SegmentError. 문제는 시간순."""
        _check_ms("duration_ms", duration_ms)
        segs = sorted((SegmentTimeline._parse_record(r) for r in records), key=lambda s: (s.start_ms, s.end_ms))
        issues: list[RecordIssue] = []
        cursor = 0
        last_id: str | None = None
        for s in segs:
            if s.end_ms <= 0 or s.start_ms >= duration_ms:
                issues.append(RecordIssue("out_of_media", s.start_ms, s.end_ms, (s.id,)))
                continue
            if s.start_ms < 0 or s.end_ms > duration_ms:
                issues.append(RecordIssue("out_of_media", s.start_ms, s.end_ms, (s.id,)))
            st, en = max(0, s.start_ms), min(duration_ms, s.end_ms)
            if st < cursor:
                issues.append(RecordIssue("overlap", st, min(cursor, en), (last_id, s.id)))
            elif st > cursor:
                issues.append(RecordIssue("gap", cursor, st, ()))
            if en > cursor:
                cursor, last_id = en, s.id
        if cursor < duration_ms:
            issues.append(RecordIssue("gap", cursor, duration_ms, ()))
        issues.sort(key=lambda x: (x.start_ms, x.end_ms))
        return issues

    @classmethod
    def from_records(cls, duration_ms: int, records: Iterable[Mapping], new_id: Callable[[], str], *,
                     min_gap_ms: int = DEFAULT_MIN_GAP_MS, undo_limit: int = UNDO_LIMIT) -> "SegmentTimeline":
        """현재 유효 segment 레코드들 → 타임라인. undo 스택은 비어 있다.

        - 빈틈은 new_id() 로 만든 '안 매긴' 구간으로 메운다(기록하지 않으면 레코드는 그대로).
        - 0 앞이나 영상 끝에 걸친 구간은 잘라 보이고 원래 값을 보존한다(out_of_media).
        - 시작부터 영상 밖이면 outside 로 보존한다. 조용히 버리지 않는다.
        - 겹침은 고칠 수 없으므로 SegmentOverlapError (overlaps 에 ID 쌍). check_records 로 먼저 보여 줄 수 있다.
        """
        _check_ms("duration_ms", duration_ms)
        parsed = [cls._parse_record(r) for r in records]
        ids = [s.id for s in parsed]
        if len(set(ids)) != len(ids):
            raise SegmentError("같은 entity_id 의 레코드가 둘 이상이다 (현재 유효 판만 넘겨야 한다)")
        inside: list[Segment] = []
        outside: list[Segment] = []
        for s in parsed:
            if s.end_ms <= 0 or s.start_ms >= duration_ms:
                outside.append(replace(s, outside=True))
                continue
            st, en = s.start_ms, s.end_ms
            o_st = st if st < 0 else None
            o_en = en if en > duration_ms else None
            inside.append(replace(s, start_ms=max(0, st), end_ms=min(duration_ms, en),
                                  orig_start_ms=o_st, orig_end_ms=o_en))
        inside.sort(key=lambda s: (s.start_ms, s.end_ms))
        overlaps = [(a.id, b.id) for a, b in zip(inside, inside[1:]) if b.start_ms < a.end_ms]
        if overlaps:
            raise SegmentOverlapError(f"겹치는 구간 {len(overlaps)}쌍", overlaps)
        used = set(ids)
        filled: list[Segment] = []
        cursor = 0

        def gap(a: int, b: int) -> None:
            nid = _check_uuid(new_id())
            if nid in used:
                raise SegmentError(f"new_id() 가 이미 있는 ID 를 돌려줬다: {nid}")
            used.add(nid)
            filled.append(Segment(id=nid, start_ms=a, end_ms=b))

        for s in inside:
            if s.start_ms > cursor:
                gap(cursor, s.start_ms)
            filled.append(s)
            cursor = s.end_ms
        if cursor < duration_ms:
            gap(cursor, duration_ms)
        outside.sort(key=lambda s: (s.start_ms, s.end_ms))
        return cls(duration_ms, new_id, min_gap_ms=min_gap_ms, undo_limit=undo_limit,
                   _segments=filled, _outside=outside)


# ---------------------------------------------------------------------------- 기록 구간표의 경계 완충 (모델 없이)
def record_buffers(segments: Iterable[Mapping], duration_ms: int, buffer_ms: int) -> list[tuple[int, int]]:
    """현재 유효 구간 레코드(또는 body·{start_ms, end_ms} dict) 목록의 경계마다 [b − 완충, b + 완충) (안내서 3장).

    모든 시작·끝을 경계로 본다(SegmentTimeline.buffer_ranges 와 같은 규칙). 0 과 duration_ms(영상 처음·끝)는 전환이
    아니라 빼고, duration_ms 가 0(모름)이면 끝 쪽은 자르지 않는다. 겹치거나 맞닿은 완충은 하나로 합친다.
    작업대 표본 뽑기·재현 검증과 내보내기(eval.in_boundary_buffer)가 같이 쓴다.
    """
    bs: set[int] = set()
    for s in segments or ():
        b = s.get("body") if isinstance(s.get("body"), Mapping) else s
        for k in ("start_ms", "end_ms"):
            v = b.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                bs.add(v)
    out: list[tuple[int, int]] = []
    if buffer_ms <= 0:
        return out
    for b in sorted(bs):
        if b <= 0 or (duration_ms and b >= duration_ms):
            continue
        lo, hi = max(0, b - buffer_ms), (min(duration_ms, b + buffer_ms) if duration_ms else b + buffer_ms)
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def in_ranges(t_ms: int, ranges: Sequence[tuple[int, int]]) -> bool:
    """t 가 반열린 구간 [a, b) 목록 중 하나 안인가."""
    return any(a <= t_ms < b for a, b in ranges)
