"""시청자 훈련(G) 채점 첫 판 — AI 없는 세 축 (08 D-20 2·3, 21 6-2).

시청자가 쓰기 칸에 쓴 채팅 하나를 보낸 VOD 시각과 함께 받아 **세 축**을 본다.

| 축 | 보는 것 | 단계 |
|---|---|---|
| 주제 적합성 `topic` | 보낸 시각 앞의 주제 흐름 낱말(topic-flow, 앞 `topic_lookback_ms`)과 전사 낱말(앞 `transcript_lookback_ms`)이 쓴 채팅 낱말과 겹치나 | good·ok·weak |
| 대화 확장성 `expand` | 스트리머가 이어 말할 거리가 있나: 질문 꼴(열린 질문 / 예·아니오), 구체적인 낱말, 길이 범위, 이모티콘·'ㅋ' 만인가 | good·ok·weak |
| 반복 여부 `repeat` | 이 훈련에서 앞서 쓴 말과 같거나 비슷한가, 앞 `copy_window_ms` 의 실제·가상 채팅을 베꼈나, 같은 말버릇을 되풀이하나 | good·ok·weak |

- 축마다 **단계 하나와 까닭 코드 하나 + 숫자**만 돌려준다(`AxisResult`). **합계 점수는 없다**(D-20 2).
  화면은 `reason_line()` 또는 자기 문구로 코드 + 숫자를 한 줄로 바꾼다. 결과에는 쓴 글·맞은 낱말이 들어가지 않는다
  (기록·로그에는 단계·코드·숫자만, 21 6-3).
- 문턱은 모두 `DEFAULT_*` 상수와 `ViewerScoreSettings` 에 모았다. guide-config `values.viewer_score` 로 옮길 수 있게
  `settings_from_values()` 가 있다(21 6-2 '시범 뒤 guide-config 로').
- 같은 입력이면 같은 결과(시계·난수 없음, 집합 순서에 기대지 않음).
- 순수 모듈이다(표준 라이브러리만, 엔진 import 없음, 파일·시계·네트워크 없음 — tests/test_layers.py, D-06 앱 → 엔진 경계).
  엔진은 미리 만든 파일(topic-flow, VOD 로 보정한 시각별 전사 낱말)만 주고, 파일을 읽는 쪽이 값을 넘긴다(D-20 3).
- 낱말 나누기는 `app/core/textnorm.py` 의 `tokens(text)` 를 쓴다(엔진의 시각별 전사 낱말 파일과 같은 규칙이어야 겹친다).
  그 모듈이 없으면 이 파일 안의 임시 구현(같은 규칙)을 쓴다(`NORMALIZER` 로 어느 쪽인지 안다).
  반복 여부의 같음 비교 꼴(`canon`)은 이 파일의 `_normalize` 로 만든다. `textnorm.squash` 는 'ㅋ' 같은 자모를 지워
  'ㅋㅋ' 만 되풀이한 것을 못 보고, `textnorm.normalize` 는 NFKC 라 호환 자모 'ㅋ'(U+314B)를 첫소리 자모(U+110F)로
  바꿔 ko_cues 의 자모 목록과 맞지 않기 때문이다(ko_cues.nfkc 는 호환 자모를 그대로 둔다).
- 시청자가 쓴 채팅은 시나리오·LLM 입력에 넣지 않는다(20 6장). 이 모듈은 받은 글을 메모리에서만 쓴다.
- 알려진 약점(첫 판은 그대로): 스트리머가 방금 한 말을 그대로 옮긴 채팅(전사 되풀이)을 잡는 축이 없다. 반복 여부는
  채팅끼리만 본다. 다음 판 후보: 반복 여부에 '전사 베끼기' 코드(내용 낱말 전부가 앞 전사 낱말에 있으면 ok, 글자 n-gram
  포함률이 높으면 weak). 21 6-2 에도 적었다.

예문은 모두 합성이다(시험: tests/test_viewer_score.py).
"""
from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping, Sequence

from . import ko_cues

# ───────────────────────────────────────────────────────────── 정규화 (textnorm 이 없으면 임시 구현)

_EMOTE = re.compile(r"\{:[^{}:]{1,40}:\}")

try:  # 다른 담당(engine_words)이 만드는 공용 모듈. 엔진의 시각별 전사 낱말 파일과 같은 규칙이어야 겹침을 센다
    from . import textnorm as _textnorm
except ImportError:  # pragma: no cover - textnorm 이 있으면 쓰이지 않는다
    _textnorm = None

if _textnorm is not None and callable(getattr(_textnorm, "tokens", None)):
    _tokens_raw = _textnorm.tokens
    NORMALIZER = "textnorm"
else:  # pragma: no cover - 임시 구현(engine/retrieve.tokens 와 같은 규칙)
    NORMALIZER = "fallback"

    _JAMO_RUN = re.compile(r"[ㄱ-ㆎ]+")
    _NON_WORD = re.compile(r"[^0-9a-z가-힣\s]+")
    _PARTICLES = ("에서는", "으로는", "에서", "으로", "한테", "까지", "부터", "처럼", "보다", "이랑",
                  "은", "는", "이", "가", "을", "를", "에", "도", "로", "랑", "의", "만", "와", "과")

    def _tokens_raw(text: str) -> list[str]:
        """임시: 이모티콘·자모·기호를 떼고 낱말로. 조사를 뗀 꼴과 원래 꼴을 둘 다(2자 이상, 처음 나온 순서)."""
        s = _EMOTE.sub(" ", unicodedata.normalize("NFKC", text or ""))
        s = _NON_WORD.sub(" ", _JAMO_RUN.sub(" ", s).lower())
        out: list[str] = []
        for w in s.split():
            cand = [w]
            for p in _PARTICLES:
                if len(w) > len(p) + 1 and w.endswith(p):
                    cand.append(w[: -len(p)])
                    break
            for x in cand:
                if len(x) >= 2 and x not in out:
                    out.append(x)
        return out


def _normalize(text: str) -> str:
    """같음 비교용 정규화: NFKC(호환 자모 ㅋ·ㅎ 는 그대로)·소문자, 공백 하나로.
    textnorm.squash 는 자모를 지워 'ㅋㅋ' 만 되풀이한 것을 못 보므로 반복 여부에서는 이것을 쓴다."""
    return re.sub(r"\s+", " ", ko_cues.nfkc(text).lower()).strip()


def word_set(text: str) -> frozenset[str]:
    """점수용 낱말 집합(2자 이상)."""
    return frozenset(w for w in _tokens_raw(text or "") if isinstance(w, str) and len(w) >= 2)


# ───────────────────────────────────────────────────────────── 이름·코드

#: 채점 규칙의 판. 문턱·단서 목록·코드를 바꾸면 올린다(viewer-event write 줄의 scorer 칸 'viewer_score/<판>').
#: 0.2(2026-10-23 검토): 기능어 거르기(목록 + 전사 창 문서 빈도), 접두 겹침 좁힘, 서술에 흔한 열린 질문 머리,
#: 말버릇의 열린 질문 첫 낱말·끝 기호, 짧은 글의 n-gram 비율, 전사 창은 보낸 시각 앞에 시작한 것만.
VERSION = "0.2"

AXES = ("topic", "expand", "repeat")
GOOD, OK, WEAK = "good", "ok", "weak"
LEVELS = (GOOD, OK, WEAK)

# 주제 적합성
TOPIC_FLOW_HIT = "topic.flow_hit"          # 주제 흐름 낱말과 겹침(flow ≥ 문턱)
TOPIC_TALK_HITS = "topic.talk_hits"        # 전사 낱말과 여러 개 겹침
TOPIC_TALK_ONE = "topic.talk_one"          # 전사 낱말과 하나 겹침
TOPIC_NO_OVERLAP = "topic.no_overlap"      # 겹침 없음
TOPIC_NO_WORDS = "topic.no_words"          # 쓴 채팅에 낱말이 없음(이모티콘·'ㅋ' 만 등)
TOPIC_NO_CONTEXT = "topic.no_context"      # 그 시각 앞에 주제 흐름·전사 낱말이 없음(판단 못 함 → ok)
# 대화 확장성
EXPAND_OPEN_QUESTION = "expand.open_question"
EXPAND_YN_CONCRETE = "expand.yes_no_concrete"
EXPAND_YN_QUESTION = "expand.yes_no"
EXPAND_CONCRETE = "expand.concrete"
EXPAND_THIN = "expand.thin"
EXPAND_TOO_SHORT = "expand.too_short"
EXPAND_TOO_LONG = "expand.too_long"
EXPAND_REACTION_ONLY = "expand.reaction_only"
# 반복 여부
REPEAT_FRESH = "repeat.fresh"
REPEAT_SELF_SAME = "repeat.self_same"
REPEAT_SELF_SIMILAR = "repeat.self_similar"
REPEAT_COPY_SAME = "repeat.copy_same"
REPEAT_COPY_SIMILAR = "repeat.copy_similar"
REPEAT_HABIT = "repeat.habit"

REASON_CODES: dict[str, tuple[str, ...]] = {
    "topic": (TOPIC_FLOW_HIT, TOPIC_TALK_HITS, TOPIC_TALK_ONE, TOPIC_NO_OVERLAP, TOPIC_NO_WORDS, TOPIC_NO_CONTEXT),
    "expand": (EXPAND_OPEN_QUESTION, EXPAND_YN_CONCRETE, EXPAND_YN_QUESTION, EXPAND_CONCRETE, EXPAND_THIN,
               EXPAND_TOO_SHORT, EXPAND_TOO_LONG, EXPAND_REACTION_ONLY),
    "repeat": (REPEAT_FRESH, REPEAT_SELF_SAME, REPEAT_SELF_SIMILAR, REPEAT_COPY_SAME, REPEAT_COPY_SIMILAR,
               REPEAT_HABIT),
}

# 화면 한 줄 시안(숫자만 끼운다. 쓴 글·낱말은 넣지 않는다). UI 가 자기 문구를 써도 된다.
REASON_TEXT_KO: dict[str, str] = {
    TOPIC_FLOW_HIT: "지금 방송 주제 낱말과 {flow}개 겹쳐요",
    TOPIC_TALK_HITS: "방금 {window_sec}초 동안 나온 말과 {talk}개 겹쳐요",
    TOPIC_TALK_ONE: "방금 나온 말과 1개 겹쳐요",
    TOPIC_NO_OVERLAP: "방금 {window_sec}초 동안의 주제와 겹치는 낱말이 없어요",
    TOPIC_NO_WORDS: "낱말이 없어 주제를 볼 수 없어요",
    TOPIC_NO_CONTEXT: "이 시각 앞에 주제·전사 낱말이 없어 판단하지 않았어요",
    EXPAND_OPEN_QUESTION: "열린 질문이라 이어 말할 거리가 있어요",
    EXPAND_YN_CONCRETE: "예/아니오 질문이지만 구체적인 낱말이 {words}개 있어요",
    EXPAND_YN_QUESTION: "예/아니오 질문이에요. 뭐·어떻게·왜로 물으면 더 이어져요",
    EXPAND_CONCRETE: "구체적인 낱말이 {words}개 있어요. 질문으로 끝내면 더 이어져요",
    EXPAND_THIN: "이어 말할 거리가 적어요(낱말 {words}개)",
    EXPAND_TOO_SHORT: "너무 짧아요({chars}자)",
    EXPAND_TOO_LONG: "길어서 읽기 어려워요({chars}자)",
    EXPAND_REACTION_ONLY: "이모티콘·웃음만 있어요",
    REPEAT_FRESH: "새로운 말이에요",
    REPEAT_SELF_SAME: "앞서 쓴 말과 같아요",
    REPEAT_SELF_SIMILAR: "앞서 쓴 말과 {self_pct}% 비슷해요",
    REPEAT_COPY_SAME: "방금 {window_sec}초 안의 채팅과 같아요",
    REPEAT_COPY_SIMILAR: "방금 {window_sec}초 안의 채팅과 {copy_pct}% 겹쳐요",
    REPEAT_HABIT: "같은 말버릇을 최근 {habit_lookback}번 가운데 {habit}번 썼어요",
}

# ───────────────────────────────────────────────────────────── 문턱 (guide-config values.viewer_score 로 옮길 값)

DEFAULT_TOPIC_LOOKBACK_MS = 120_000           # 주제 흐름 덩어리: 보낸 시각 앞 120초와 겹치는 덩어리
DEFAULT_TRANSCRIPT_LOOKBACK_MS = 90_000       # 전사 낱말: 보낸 시각 앞 90초(허용 60~120초)
TRANSCRIPT_LOOKBACK_RANGE_MS = (60_000, 120_000)
DEFAULT_TOPIC_GOOD_FLOW_HITS = 1              # 주제 흐름 낱말이 이만큼 겹치면 good
DEFAULT_TOPIC_GOOD_TALK_HITS = 2              # 전사 낱말이 이만큼 겹치면 good
DEFAULT_TOPIC_OK_TALK_HITS = 1                # 전사 낱말이 이만큼 겹치면 ok
DEFAULT_EXPAND_MIN_CHARS = 4                  # 보이는 글자(이모티콘·공백 뺌)가 이보다 적으면 너무 짧음
DEFAULT_EXPAND_MAX_CHARS = 60                 # 이보다 많으면 너무 김(good → ok)
DEFAULT_EXPAND_CONCRETE_WORDS = 2             # 내용 낱말이 이만큼이면 구체적(주제 겹침 1개 이상이어도 구체적)
DEFAULT_COPY_WINDOW_MS = 45_000               # 베끼기: 보낸 시각 앞 45초의 실제·가상 채팅(허용 30~60초)
COPY_WINDOW_RANGE_MS = (30_000, 60_000)
DEFAULT_NGRAM = 5                             # 글자 n-gram 길이
DEFAULT_COPY_WEAK_PCT = 80                    # 베끼기 겹침 % 이상이면 weak (20 8장 5번 copy_max 0.8)
DEFAULT_COPY_OK_PCT = 50                      # 이상이면 ok
DEFAULT_SELF_WEAK_PCT = 60                    # 앞서 쓴 말과 겹침 % 이상이면 weak
DEFAULT_SELF_OK_PCT = 40                      # 이상이면 ok
DEFAULT_SELF_LOOKBACK = 30                    # 비슷함을 볼 앞선 내 채팅 수(같음은 이 훈련 전부)
DEFAULT_HABIT_LOOKBACK = 5                    # 말버릇을 볼 앞선 내 채팅 수
DEFAULT_HABIT_OK_MIN = 2                      # 그 가운데 같은 말버릇이 이만큼이면 ok
DEFAULT_HABIT_WEAK_MIN = 3                    # 이만큼이면 weak
DEFAULT_MIN_GRAMS = 2                         # 내 글의 n-gram 이 이보다 적으면(짧은 흔한 말) 비율 판정을 하지 않고 같음만 본다
DEFAULT_TALK_COMMON_PCT = 20                  # 전사 창의 이 % 이상에 나오는 낱말은 주제 풀에서 뺀다(기능어, 문서 빈도)
DEFAULT_TALK_COMMON_MIN_WINDOWS = 30          # 전사 창이 이보다 적으면 문서 빈도 거르기를 하지 않는다(표본이 작음)


@dataclass(frozen=True)
class ViewerScoreSettings:
    """채점 문턱. guide-config `values.viewer_score` 에서 `settings_from_values` 로 만든다(시범 뒤)."""
    topic_lookback_ms: int = DEFAULT_TOPIC_LOOKBACK_MS
    transcript_lookback_ms: int = DEFAULT_TRANSCRIPT_LOOKBACK_MS
    topic_good_flow_hits: int = DEFAULT_TOPIC_GOOD_FLOW_HITS
    topic_good_talk_hits: int = DEFAULT_TOPIC_GOOD_TALK_HITS
    topic_ok_talk_hits: int = DEFAULT_TOPIC_OK_TALK_HITS
    expand_min_chars: int = DEFAULT_EXPAND_MIN_CHARS
    expand_max_chars: int = DEFAULT_EXPAND_MAX_CHARS
    expand_concrete_words: int = DEFAULT_EXPAND_CONCRETE_WORDS
    copy_window_ms: int = DEFAULT_COPY_WINDOW_MS
    ngram: int = DEFAULT_NGRAM
    copy_weak_pct: int = DEFAULT_COPY_WEAK_PCT
    copy_ok_pct: int = DEFAULT_COPY_OK_PCT
    self_weak_pct: int = DEFAULT_SELF_WEAK_PCT
    self_ok_pct: int = DEFAULT_SELF_OK_PCT
    self_lookback: int = DEFAULT_SELF_LOOKBACK
    habit_lookback: int = DEFAULT_HABIT_LOOKBACK
    habit_ok_min: int = DEFAULT_HABIT_OK_MIN
    habit_weak_min: int = DEFAULT_HABIT_WEAK_MIN
    min_grams: int = DEFAULT_MIN_GRAMS
    talk_common_pct: int = DEFAULT_TALK_COMMON_PCT
    talk_common_min_windows: int = DEFAULT_TALK_COMMON_MIN_WINDOWS


DEFAULT_SETTINGS = ViewerScoreSettings()

# guide-config 칸 이름 → (설정 이름, 단위 배수, 최소, 최대). 초 단위 칸은 ms 로 바꾼다.
_GUIDE_KEYS: dict[str, tuple[str, int, int, int]] = {
    "topic_lookback_sec": ("topic_lookback_ms", 1000, 10_000, 600_000),
    "transcript_lookback_sec": ("transcript_lookback_ms", 1000, *TRANSCRIPT_LOOKBACK_RANGE_MS),
    "topic_good_flow_hits": ("topic_good_flow_hits", 1, 1, 10),
    "topic_good_talk_hits": ("topic_good_talk_hits", 1, 1, 10),
    "topic_ok_talk_hits": ("topic_ok_talk_hits", 1, 1, 10),
    "expand_min_chars": ("expand_min_chars", 1, 1, 50),
    "expand_max_chars": ("expand_max_chars", 1, 10, 500),
    "expand_concrete_words": ("expand_concrete_words", 1, 1, 10),
    "copy_window_sec": ("copy_window_ms", 1000, *COPY_WINDOW_RANGE_MS),
    "ngram": ("ngram", 1, 2, 10),
    "copy_weak_pct": ("copy_weak_pct", 1, 1, 100),
    "copy_ok_pct": ("copy_ok_pct", 1, 1, 100),
    "self_weak_pct": ("self_weak_pct", 1, 1, 100),
    "self_ok_pct": ("self_ok_pct", 1, 1, 100),
    "self_lookback": ("self_lookback", 1, 1, 1000),
    "habit_lookback": ("habit_lookback", 1, 1, 50),
    "habit_ok_min": ("habit_ok_min", 1, 1, 50),
    "habit_weak_min": ("habit_weak_min", 1, 1, 50),
    "min_grams": ("min_grams", 1, 1, 10),
    "talk_common_pct": ("talk_common_pct", 1, 5, 100),
    "talk_common_min_windows": ("talk_common_min_windows", 1, 2, 100_000),
}


def settings_from_values(values: Mapping | None) -> tuple[ViewerScoreSettings, list[str]]:
    """guide-config `values`(또는 그 안의 `viewer_score` 칸) → (설정, 메모 목록).

    없는 칸은 기본값. 숫자가 아니거나 범위 밖이면 그 칸만 기본값으로 두고 메모에 칸 이름을 적는다.
    ok 문턱이 weak·good 문턱과 뒤집히면(예: copy_ok_pct > copy_weak_pct) 둘 다 기본값으로 돌린다.
    """
    notes: list[str] = []
    if not isinstance(values, Mapping):
        return DEFAULT_SETTINGS, notes
    v = values.get("viewer_score") if isinstance(values.get("viewer_score"), Mapping) else values
    changes: dict[str, int] = {}
    for key, (attr, mul, lo, hi) in _GUIDE_KEYS.items():
        if key not in v:
            continue
        raw = v.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            notes.append(f"viewer_score.{key}: 숫자가 아님 → 기본값")
            continue
        val = int(round(float(raw) * mul))
        if not lo <= val <= hi:
            notes.append(f"viewer_score.{key}: 범위 밖 → 기본값")
            continue
        changes[attr] = val
    s = replace(DEFAULT_SETTINGS, **changes)
    for ok_attr, weak_attr in (("copy_ok_pct", "copy_weak_pct"), ("self_ok_pct", "self_weak_pct"),
                               ("habit_ok_min", "habit_weak_min"), ("topic_ok_talk_hits", "topic_good_talk_hits"),
                               ("expand_min_chars", "expand_max_chars")):
        if getattr(s, ok_attr) > getattr(s, weak_attr):
            notes.append(f"viewer_score.{ok_attr}·{weak_attr}: 순서가 뒤집힘 → 기본값")
            s = replace(s, **{ok_attr: getattr(DEFAULT_SETTINGS, ok_attr),
                              weak_attr: getattr(DEFAULT_SETTINGS, weak_attr)})
    return s, notes


# ───────────────────────────────────────────────────────────── 결과

@dataclass(frozen=True)
class AxisResult:
    """한 축의 결과: 단계, 대표 까닭 코드, 숫자(이름순). 쓴 글·낱말은 담지 않는다."""
    axis: str
    level: str
    code: str
    nums: tuple[tuple[str, int], ...] = ()

    def num(self, key: str, default: int | None = None) -> int | None:
        for k, v in self.nums:
            if k == key:
                return v
        return default

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "nums": dict(self.nums)}


@dataclass(frozen=True)
class ViewerScore:
    """세 축 결과. 합계 점수는 없다(D-20 2)."""
    topic: AxisResult
    expand: AxisResult
    repeat: AxisResult

    def axes(self) -> tuple[AxisResult, AxisResult, AxisResult]:
        return (self.topic, self.expand, self.repeat)

    def levels(self) -> dict[str, str]:
        return {a.axis: a.level for a in self.axes()}

    def as_dict(self) -> dict:
        """기록(viewer-event)·로그용: 단계·코드·숫자만."""
        return {a.axis: a.as_dict() for a in self.axes()}


def _result(axis: str, level: str, code: str, **nums: int) -> AxisResult:
    return AxisResult(axis, level, code, tuple(sorted((k, int(v)) for k, v in nums.items())))


def reason_line(result: AxisResult) -> str:
    """화면 한 줄 시안(코드 + 숫자). 모르는 코드면 코드를 그대로."""
    tpl = REASON_TEXT_KO.get(result.code)
    if not tpl:
        return result.code
    try:
        return tpl.format(**dict(result.nums))
    except (KeyError, IndexError, ValueError):
        return result.code


# ───────────────────────────────────────────────────────────── 맥락(미리 만든 파일에서 읽은 값)

def _as_int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == v:
        return int(v)
    return None


def _timed_words(entry, word_keys: tuple[str, ...]) -> tuple[int, int, frozenset[str]] | None:
    """(start_ms, end_ms, 낱말들) 또는 {"start_ms","end_ms", 낱말 칸|"text"} 또는 {"vod_ms", …} → 정규화한 낱말 집합."""
    if isinstance(entry, Mapping):
        a = _as_int(entry.get("start_ms", entry.get("vod_ms")))
        b = _as_int(entry.get("end_ms", entry.get("vod_ms", a)))
        words = None
        for k in word_keys:
            if k in entry:
                words = entry.get(k)
                break
        if words is None and isinstance(entry.get("text"), str):
            words = [entry["text"]]
    elif isinstance(entry, (tuple, list)) and len(entry) >= 3:
        a, b, words = _as_int(entry[0]), _as_int(entry[1]), entry[2]
    elif isinstance(entry, (tuple, list)) and len(entry) == 2:
        a = b = _as_int(entry[0])
        words = entry[1]
    else:
        return None
    if a is None or b is None:
        return None
    if isinstance(words, str):
        words = [words]
    if not isinstance(words, Iterable):
        return None
    ws: set[str] = set()
    for w in words:
        if isinstance(w, str):
            ws.update(word_set(w))
    ws -= _STOP
    return (min(a, b), max(a, b), frozenset(ws))


def _chat_entry(entry) -> tuple[int, str] | None:
    if isinstance(entry, Mapping):
        ms = _as_int(entry.get("vod_ms", entry.get("show_vod_ms", entry.get("time_ms"))))
        text = entry.get("text")
    elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
        ms, text = _as_int(entry[0]), entry[1]
    else:
        return None
    if ms is None or not isinstance(text, str):
        return None
    return ms, text


@dataclass(frozen=True, repr=False)
class _Timed:
    starts: tuple[int, ...]
    items: tuple[tuple[int, int, frozenset[str]], ...]
    max_len: int

    @classmethod
    def build(cls, rows: list[tuple[int, int, frozenset[str]]]) -> "_Timed":
        rows = sorted(rows, key=lambda r: (r[0], r[1], tuple(sorted(r[2]))))
        return cls(tuple(r[0] for r in rows), tuple(rows), max((r[1] - r[0] for r in rows), default=0))

    def __repr__(self) -> str:   # 낱말을 보이지 않는다(로그·assert 출력에 전사 낱말이 새지 않게)
        return f"_Timed(rows={len(self.items)})"

    def words_between(self, lo: int, hi: int) -> frozenset[str]:
        """[lo, hi) 와 겹치는 줄(끝 ≥ lo, 시작 < hi)의 낱말 합집합 — 주제 흐름 덩어리(길게 이어지는 주제)에 쓴다."""
        out: set[str] = set()
        i = bisect.bisect_left(self.starts, lo - self.max_len)
        j = bisect.bisect_left(self.starts, hi)
        for a, b, ws in self.items[i:j]:
            if b >= lo and a < hi:
                out.update(ws)
        return frozenset(out)

    def words_started(self, lo: int, hi: int) -> frozenset[str]:
        """시작이 [lo, hi) 안인 줄의 낱말 합집합 — 전사 낱말 창에 쓴다. 보낸 시각(hi) 이후에 시작한 창(아직 듣지 못한
        말)은 넣지 않고, 되돌아보기도 정확히 hi - lo 다(창 끝이 아니라 시작으로 자른다)."""
        out: set[str] = set()
        i = bisect.bisect_left(self.starts, lo)
        j = bisect.bisect_left(self.starts, hi)
        for _a, _b, ws in self.items[i:j]:
            out.update(ws)
        return frozenset(out)


def _drop_common(rows: list[tuple[int, int, frozenset[str]]], s: "ViewerScoreSettings"
                 ) -> tuple[list[tuple[int, int, frozenset[str]]], frozenset[str]]:
    """전사 창의 문서 빈도로 기능어를 뺀다(목록 없이 데이터로). 창이 talk_common_min_windows 개 이상일 때만,
    talk_common_pct % 이상의 창에 나오는 낱말을 모든 창에서 뺀다. (거른 창들, 뺀 낱말 집합)."""
    if len(rows) < max(2, s.talk_common_min_windows):
        return rows, frozenset()
    df: dict[str, int] = {}
    for _a, _b, ws in rows:
        for w in ws:
            df[w] = df.get(w, 0) + 1
    limit = len(rows) * s.talk_common_pct
    common = frozenset(w for w, n in df.items() if n * 100 >= limit)
    if not common:
        return rows, common
    return [(a, b, ws - common) for a, b, ws in rows], common


@dataclass(frozen=True, repr=False)
class ScoreContext:
    """한 구간의 채점 맥락. `build()` 로 만든다(정렬·정규화를 한 번만).

    - topic_blocks: topic-flow 덩어리(VOD 축) `(start_ms, end_ms, keywords)` 또는 `{"start_ms","end_ms","keywords"}`
      (engine/retrieve.load_topic_flow 의 꼴과 같다)
    - transcript: VOD 로 보정한 시각별 전사 낱말 `(start_ms, end_ms, words)` · `(vod_ms, words)` 또는
      `{"start_ms","end_ms","words"|"tokens"|"text"}` · `{"vod_ms", …}`
    - chats: 화면에 흐르는 실제·가상 채팅 `(vod_ms, text)` 또는 `{"vod_ms"|"show_vod_ms", "text"}`.
      **시청자가 쓴 연습 채팅은 넣지 않는다**(그것은 history 로 따로 본다). 본문은 정규화한 꼴·n-gram 으로만 남긴다.
    - talk_common: 전사 창의 문서 빈도로 뺀 흔한 낱말(기능어, `_drop_common`). 쓴 채팅의 내용 낱말에서도 뺀다.

    repr 에는 개수만 나온다(채팅 본문·전사 낱말이 로그·assert 출력에 새지 않게).
    """
    topic: _Timed
    talk: _Timed
    chat_ms: tuple[int, ...]
    chat_keys: tuple[tuple[str, frozenset[str]], ...]
    ngram: int = DEFAULT_NGRAM
    talk_common: frozenset[str] = frozenset()

    def __repr__(self) -> str:
        return (f"ScoreContext(topic={len(self.topic.items)}, talk={len(self.talk.items)}, chats={len(self.chat_ms)}, "
                f"talk_common={len(self.talk_common)}, ngram={self.ngram})")

    @classmethod
    def build(cls, topic_blocks: Iterable = (), transcript: Iterable = (), chats: Iterable = (), *,
              settings: ViewerScoreSettings | None = None) -> "ScoreContext":
        s = settings or DEFAULT_SETTINGS
        tb = [r for r in (_timed_words(e, ("keywords", "words", "tokens")) for e in (topic_blocks or ())) if r]
        tr = [r for r in (_timed_words(e, ("words", "tokens", "keywords")) for e in (transcript or ())) if r]
        tr, common = _drop_common(tr, s)
        cs = []
        for e in chats or ():
            c = _chat_entry(e)
            if c is None:
                continue
            key = canon(c[1])
            if key:
                cs.append((c[0], key, grams(key, s.ngram)))
        cs.sort(key=lambda r: (r[0], r[1]))
        return cls(_Timed.build(tb), _Timed.build(tr), tuple(r[0] for r in cs),
                   tuple((r[1], r[2]) for r in cs), s.ngram, common)

    def recent_chat_keys(self, lo: int, hi: int) -> tuple[tuple[str, frozenset[str]], ...]:
        i = bisect.bisect_left(self.chat_ms, lo)
        j = bisect.bisect_right(self.chat_ms, hi)
        return self.chat_keys[i:j]


# ───────────────────────────────────────────────────────────── 낱말 도구

_STOP = frozenset(ko_cues.FILLER_WORDS) | frozenset(ko_cues.GENERIC_WORDS)
_RUN3 = re.compile(r"(.)\1{2,}")
_SQUASH = re.compile(r"[\s\W_]+", re.UNICODE)
_MEANINGFUL = re.compile(r"[0-9A-Za-z가-힣]")


def canon(text: str) -> str:
    """같음 비교용 꼴: 이모티콘을 떼고 정규화, 공백·기호 없앰, 같은 글자 3번 넘게 이어지면 2번으로.
    이모티콘·기호뿐이라 비면 이모티콘 코드만 이은 꼴(이모티콘만 되풀이한 것도 같은 말로 본다)."""
    s = _normalize(_EMOTE.sub(" ", text or ""))
    s = _RUN3.sub(r"\1\1", _SQUASH.sub("", s))
    if s:
        return s
    return "".join(m.lower() for m in _EMOTE.findall(unicodedata.normalize("NFKC", text or "")))


def grams(key: str, n: int = DEFAULT_NGRAM) -> frozenset[str]:
    """글자 n-gram 집합. 글이 n 자보다 짧으면 빈 집합(같음 비교만 한다)."""
    if len(key) < n:
        return frozenset()
    return frozenset(key[i:i + n] for i in range(len(key) - n + 1))


def contain_pct(mine: frozenset[str], other: frozenset[str]) -> int:
    """내 n-gram 가운데 남의 글에도 있는 비율(0~100, 반올림 정수)."""
    if not mine:
        return 0
    inter = len(mine & other)
    return (200 * inter + len(mine)) // (2 * len(mine))


def visible_chars(text: str) -> int:
    """이모티콘 코드와 공백을 뺀 글자 수."""
    return len(re.sub(r"\s+", "", _EMOTE.sub("", unicodedata.normalize("NFKC", text or ""))))


#: 접두 겹침에서 짧은 쪽이 '줄기 + 이 끝 한 글자' 이면 활용 꼴로 본다(맛있다 ~ 맛있어요). 명사 끝(김치전 ~ 김치찌개)은 아님.
_INFLECT_TAIL = frozenset("다요고서네지게죠면니어아해")


def _match(word: str, pool: frozenset[str]) -> bool:
    """겹침: 같은 꼴이면 겹친다. 접두 비교는 좁게 본다(textnorm 이 이미 조사를 뗐다).

    - 2자 낱말은 정확히 같을 때만('그래' ≠ '그래가지고', '하면' ≠ '하면서').
    - 짧은 쪽이 3자 이상이고 **통째로** 긴 쪽의 머리면 겹침('떡볶이' ~ '떡볶이를').
    - 짧은 쪽이 3자 이상이고 끝 한 글자만 다르며 그 끝이 활용 어미(`_INFLECT_TAIL`)면 겹침('맛있다' ~ '맛있어요').
      명사 끝이 다른 것('김치전' ~ '김치찌개', '그래도' ~ '그래가지고')은 겹치지 않는다.
    """
    if word in pool:
        return True
    for p in pool:
        short = word if len(word) <= len(p) else p
        long_ = p if short is word else word
        n = len(short)
        if n < 3:
            continue
        if long_.startswith(short):
            return True
        if long_.startswith(short[:-1]) and short[-1] in _INFLECT_TAIL:
            return True
    return False


def _content_units(text: str, extra_stop: frozenset[str] = frozenset()) -> tuple[frozenset[str], ...]:
    """내용 낱말: 띄어 쓴 낱말마다 점수용 꼴(조사 뗀 꼴 포함)의 묶음. 말버릇·흔한 낱말·열린 질문 낱말은 뺀다.
    extra_stop: 맥락이 데이터로 찾은 흔한 낱말(전사 창 문서 빈도, ScoreContext.talk_common)도 뺀다.

    '떡볶이를' 이 '떡볶이를'·'떡볶이' 두 꼴을 내도 한 낱말로 센다. 같은 낱말을 두 번 써도 하나(정렬해 돌려준다).
    """
    units: set[frozenset[str]] = set()
    stop = _STOP | extra_stop if extra_stop else _STOP
    for piece in _EMOTE.sub(" ", unicodedata.normalize("NFKC", text or "")).split():
        ws = frozenset(w for w in word_set(piece) - stop
                       if not ko_cues.starts_with_any(w, ko_cues.OPEN_QUESTION_WORDS))
        if ws:
            units.add(ws)
    return tuple(sorted(units, key=lambda u: tuple(sorted(u))))


EMPTY_CONTEXT = ScoreContext.build()

# ───────────────────────────────────────────────────────────── 세 축

def _score_topic(units: tuple[frozenset[str], ...], sent_ms: int, ctx: ScoreContext, s: ViewerScoreSettings
                 ) -> tuple[AxisResult, tuple[frozenset[str], ...]]:
    """(결과, 주제와 겹친 내 낱말들). 겹친 낱말은 대화 확장성의 '구체적' 판단과 말버릇 단위 거르기에도 쓴다."""
    window_sec = s.transcript_lookback_ms // 1000
    flow_pool = ctx.topic.words_between(sent_ms - s.topic_lookback_ms, sent_ms)
    talk_pool = ctx.talk.words_started(sent_ms - s.transcript_lookback_ms, sent_ms)
    flow_hit = tuple(u for u in units if any(_match(w, flow_pool) for w in sorted(u)))
    talk_hit = tuple(u for u in units if any(_match(w, talk_pool) for w in sorted(u)))
    hit = tuple(u for u in units if u in flow_hit or u in talk_hit)
    nums = dict(flow=len(flow_hit), talk=len(talk_hit), words=len(units), window_sec=window_sec)
    if not units:
        return _result("topic", WEAK, TOPIC_NO_WORDS, **nums), hit
    if not flow_pool and not talk_pool:
        return _result("topic", OK, TOPIC_NO_CONTEXT, **nums), hit
    if len(flow_hit) >= s.topic_good_flow_hits:
        return _result("topic", GOOD, TOPIC_FLOW_HIT, **nums), hit
    if len(talk_hit) >= s.topic_good_talk_hits:
        return _result("topic", GOOD, TOPIC_TALK_HITS, **nums), hit
    if len(talk_hit) >= s.topic_ok_talk_hits:
        return _result("topic", OK, TOPIC_TALK_ONE, **nums), hit
    return _result("topic", WEAK, TOPIC_NO_OVERLAP, **nums), hit


def _score_expand(text: str, n_words: int, topic_hits: int, s: ViewerScoreSettings) -> AxisResult:
    chars = visible_chars(text)
    form = ko_cues.question_form(text)
    q = {"open": 2, "yes_no": 1}.get(form or "", 0)   # 0 묻는 말 아님, 1 예/아니오, 2 열린 질문
    nums = dict(chars=chars, words=n_words, topic_words=topic_hits, question=q)
    if not _MEANINGFUL.search(_EMOTE.sub(" ", unicodedata.normalize("NFKC", text or ""))):
        return _result("expand", WEAK, EXPAND_REACTION_ONLY, **nums)
    if chars < s.expand_min_chars:
        return _result("expand", OK if q else WEAK, EXPAND_TOO_SHORT, **nums)
    concrete = topic_hits >= 1 or n_words >= s.expand_concrete_words
    if form == "open":
        level, code = GOOD, EXPAND_OPEN_QUESTION
    elif form == "yes_no":
        level, code = (GOOD, EXPAND_YN_CONCRETE) if concrete else (OK, EXPAND_YN_QUESTION)
    else:
        level, code = (OK, EXPAND_CONCRETE) if concrete else (WEAK, EXPAND_THIN)
    if chars > s.expand_max_chars and level == GOOD:
        level, code = OK, EXPAND_TOO_LONG
    return _result("expand", level, code, **nums)


def _habit_units(text: str, topic_words: frozenset[str]) -> frozenset[str]:
    """말버릇 단위: 말버릇 후보 낱말('w:'), 첫 낱말('h:', 주제 낱말·열린 질문 낱말이 아니면), 끝 표지('t:', 이모티콘·
    웃음 자모·'~'·'!'). 끝 기호('t:punct:…')는 약한 단위라 그것만 겹치면 ok 까지만 간다(`_score_repeat`).
    열린 질문을 연달아 쓰는 것(대화 확장성이 권하는 꼴)은 말버릇으로 벌하지 않는다."""
    units = {"w:" + w for w in ko_cues.filler_hits(text)}
    ws = re.findall(r"\S+", _EMOTE.sub(" ", unicodedata.normalize("NFKC", text or "").lower()))
    if ws:
        first = canon(ws[0])
        if len(first) >= 2 and not (word_set(ws[0]) & topic_words) \
                and not ko_cues.starts_with_any(first, ko_cues.OPEN_QUESTION_WORDS):
            units.add("h:" + first)
    tail = ko_cues.tail_marker(text)
    if tail:
        units.add("t:" + tail)
    return frozenset(units)


def _strong_units(units: frozenset[str]) -> frozenset[str]:
    """weak 까지 갈 수 있는 말버릇 단위(끝 기호 '!'·'~' 는 뺀다)."""
    return frozenset(u for u in units if not u.startswith("t:punct:"))


def _score_repeat(text: str, sent_ms: int, ctx: ScoreContext, history: Sequence[str], topic_words: frozenset[str],
                  s: ViewerScoreSettings) -> AxisResult:
    key = canon(text)
    mine = grams(key, s.ngram)
    # 짧은 흔한 말(n-gram 이 min_grams 개보다 적음: 인사·짧은 감탄)은 비율이 쉽게 100 이 되므로 같음만 본다
    if len(mine) < s.min_grams:
        mine = frozenset()
    prior = [canon(h) for h in history]
    self_same = bool(key) and key in prior
    self_pct = max((contain_pct(mine, grams(p, s.ngram)) for p in prior[-s.self_lookback:]), default=0)
    recent = ctx.recent_chat_keys(sent_ms - s.copy_window_ms, sent_ms)
    copies = sum(1 for k, _ in recent if key and k == key)
    same_n = ctx.ngram == s.ngram   # 맥락을 다른 n 으로 만들었으면 다시 잘라 본다
    copy_pct = max((contain_pct(mine, g if same_n else grams(k, s.ngram)) for k, g in recent), default=0)
    units = _habit_units(text, topic_words)
    strong = _strong_units(units)
    habit = habit_strong = 0
    for h in history[-s.habit_lookback:]:
        hu = _habit_units(h, topic_words)
        if units & hu:
            habit += 1
            if strong & hu:
                habit_strong += 1
    nums = dict(self_pct=self_pct, copy_pct=copy_pct, copies=copies, habit=habit,
                habit_lookback=s.habit_lookback, window_sec=s.copy_window_ms // 1000)
    if self_same:
        return _result("repeat", WEAK, REPEAT_SELF_SAME, **nums)
    if copies:
        return _result("repeat", WEAK, REPEAT_COPY_SAME, **nums)
    if self_pct >= s.self_weak_pct:
        return _result("repeat", WEAK, REPEAT_SELF_SIMILAR, **nums)
    if copy_pct >= s.copy_weak_pct:
        return _result("repeat", WEAK, REPEAT_COPY_SIMILAR, **nums)
    if habit_strong >= s.habit_weak_min:   # 끝 기호('!'·'~')만 겹친 것은 ok 까지만
        return _result("repeat", WEAK, REPEAT_HABIT, **{**nums, "habit": habit_strong})
    if self_pct >= s.self_ok_pct:
        return _result("repeat", OK, REPEAT_SELF_SIMILAR, **nums)
    if copy_pct >= s.copy_ok_pct:
        return _result("repeat", OK, REPEAT_COPY_SIMILAR, **nums)
    if habit >= s.habit_ok_min:
        return _result("repeat", OK, REPEAT_HABIT, **nums)
    return _result("repeat", GOOD, REPEAT_FRESH, **nums)


def score_chat(text: str, sent_ms: int, ctx: ScoreContext | None = None, history: Sequence[str] = (), *,
               settings: ViewerScoreSettings | None = None) -> ViewerScore:
    """채팅 하나를 세 축으로 본다.

    text: 시청자가 쓴 글. sent_ms: 보낸 VOD 시각(ms). ctx: `ScoreContext.build(...)`(없으면 빈 맥락).
    history: 이 훈련에서 **앞서** 쓴 내 채팅(오래된 것 → 최근, 이번 글은 넣지 않는다).
    """
    if isinstance(sent_ms, bool) or not isinstance(sent_ms, int):
        raise ValueError("sent_ms 는 정수(ms)여야 한다")
    s = settings or DEFAULT_SETTINGS
    c = ctx or EMPTY_CONTEXT
    text = text if isinstance(text, str) else ""
    hist = [h for h in history if isinstance(h, str)]
    units = _content_units(text, c.talk_common)
    topic, hit_units = _score_topic(units, sent_ms, c, s)
    topic_words = frozenset().union(*hit_units) if hit_units else frozenset()
    expand = _score_expand(text, len(units), len(hit_units), s)
    repeat = _score_repeat(text, sent_ms, c, hist, topic_words, s)
    return ViewerScore(topic, expand, repeat)


# ───────────────────────────────────────────────────────────── 한 훈련 동안 이어 쓰기 · 구간 끝 요약

@dataclass
class ViewerScorer:
    """한 훈련(구간)의 채점기. `score()` 할 때마다 쓴 글을 메모리의 history 에 더한다(파일에 쓰지 않는다)."""
    ctx: ScoreContext = EMPTY_CONTEXT
    settings: ViewerScoreSettings = DEFAULT_SETTINGS
    _history: list[str] = field(default_factory=list, repr=False)
    _scores: list[ViewerScore] = field(default_factory=list, repr=False)

    def score(self, text: str, sent_ms: int) -> ViewerScore:
        r = score_chat(text, sent_ms, self.ctx, self._history, settings=self.settings)
        self._history.append(text if isinstance(text, str) else "")
        self._scores.append(r)
        return r

    @property
    def scores(self) -> tuple[ViewerScore, ...]:
        return tuple(self._scores)

    def summary(self) -> dict:
        return summarize(self._scores)


def summarize(scores: Iterable[ViewerScore]) -> dict:
    """구간 끝 요약(21 6-1): 쓴 수와 축마다 단계 분포, 축마다 가장 잦은 까닭 코드. 숫자·코드만."""
    rows = list(scores)
    out: dict = {"writes": len(rows)}
    for axis in AXES:
        dist = {lv: 0 for lv in LEVELS}
        codes: dict[str, int] = {}
        for r in rows:
            a = getattr(r, axis)
            dist[a.level] = dist.get(a.level, 0) + 1
            codes[a.code] = codes.get(a.code, 0) + 1
        top = min(codes.items(), key=lambda kv: (-kv[1], kv[0]))[0] if codes else None
        out[axis] = {"levels": dist, "top_code": top}
    return out
