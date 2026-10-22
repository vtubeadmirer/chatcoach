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
- 문턱은 모두 `THRESHOLDS` 한 표(guide-config 칸 이름 → 설정 칸·기본값·범위)에 모았고 `ViewerScoreSettings` 가 그 칸을
  가진다. `settings_from_values()` 가 guide-config `values.viewer_score`(선택 항목, 스키마 `$defs/viewerScore`)를 읽는다 —
  없거나 범위 밖이면 기본값(D-21 4). 보정 측정: tools/viewer_score_calib.py, docs/measurements/2026-10-23-viewer-score-calib.md.
- 같은 입력이면 같은 결과(시계·난수 없음, 집합 순서에 기대지 않음).
- 순수 모듈이다(표준 라이브러리만, 엔진 import 없음, 파일·시계·네트워크 없음 — tests/test_layers.py, D-06 앱 → 엔진 경계).
  엔진은 미리 만든 파일(topic-flow, VOD 로 보정한 시각별 전사 낱말)만 주고, 파일을 읽는 쪽이 값을 넘긴다(D-20 3).
- 낱말 나누기는 `app/core/textnorm.py` 의 `tokens(text)` 를 쓴다(엔진의 시각별 전사 낱말 파일과 같은 규칙이어야 겹친다).
  그 모듈이 없으면 이 파일 안의 임시 구현(같은 규칙)을 쓴다(`NORMALIZER` 로 어느 쪽인지 안다).
  반복 여부의 같음 비교 꼴(`canon`)은 이 파일의 `_normalize` 로 만든다. `textnorm.squash` 는 'ㅋ' 같은 자모를 지워
  'ㅋㅋ' 만 되풀이한 것을 못 보고, `textnorm.normalize` 는 NFKC 라 호환 자모 'ㅋ'(U+314B)를 첫소리 자모(U+110F)로
  바꿔 ko_cues 의 자모 목록과 맞지 않기 때문이다(ko_cues.nfkc 는 호환 자모를 그대로 둔다).
- 시청자가 쓴 채팅은 시나리오·LLM 입력에 넣지 않는다(20 6장). 이 모듈은 받은 글을 메모리에서만 쓴다.
- 주제 적합성의 **선택 입력**(G 둘째, 22 7-1 의 3·5-2 의 3). 셋 다 주지 않으면 결과는 이전과 바이트 단위로 같다.
  · `topic_vectors`: 엔진(engine/topic_vectors.py, e5-small)이 **미리 계산한 창별 전사 벡터**(topic-vectors 문서 또는
    `(start_ms, end_ms, vector)` 줄). 앱은 임베딩을 계산하지 않으므로 **쓴 글의 벡터는 없다** — 그래서 '쓴 글 ↔ 창' 이 아니라
    '창 ↔ 창' 만 쓴다: (가) 이웃 창 유사도가 끊기지 않은 **같은 주제 줄기**를 되돌아보기(90초) 앞으로 `vec_run_max_ms` 까지
    늘리고(경계 = 이웃 창 유사도가 그 파일의 하위 `vec_boundary_pct` 분위 밑), (나) 지금 주제 창과 그 파일의 이웃 창만큼
    가까운(`vec_related_pct` 분위 이상) **앞선 창**(최대 `vec_related_max` 개)의 전사 낱말을 '관련 풀' 로 본다. 두 풀은 쓴 글의
    낱말과 **겹칠 때만** 쓰고, weak(겹침 없음)를 ok 로만 올린다(good 은 지금 되돌아보기 안의 겹침만, 정밀도 우선 D-21 2).
  · `synonyms`: 동의어 묶음(`parse_synonyms` 로 만든 표). 묶음 안 낱말은 같은 낱말로 본다(주제 흐름·전사 풀 모두).
    파일(`chatcoach-data\\config\\synonyms-ko.txt`)을 읽는 것은 부르는 쪽 몫이다(이 모듈은 파일을 열지 않는다).
  · `flow_head2`(설정, 기본 끔): 주제 흐름 낱말만 2자 머리 겹침을 허용('보스' ~ '보스전'). 전사 풀에는 쓰지 않는다.
  · 벡터 문턱(`vec_*`)도 다른 문턱처럼 `THRESHOLDS` 한 표에서 온다(`DEFAULT_VEC_*` 는 그 표에서 나온 옛 이름).
  · 화면 연결: app/viewer_flow.build_context 가 `<P>\\derived\\asr-topic-vectors.json`(asr 해시·보정이 전사 낱말과 같을 때만)과
    동의어 파일을 읽어 context 의 `topic_vectors`·`synonyms` 로 넘기고, viewer_train_view 가 `ScoreContext.build` 에 그대로 준다.
- 전사 베끼기(0.3, 22 7-1 의 4): 반복 여부가 스트리머가 방금(앞 `copy_window_ms`) 한 말도 본다. 서술(질문 아님)의 내용
  낱말이 2개 이상이고 모두 방금 전사 낱말이면 ok, 3개 이상이고 모두 **한 전사 창** 안의 낱말이면 weak(`repeat.talk_copy`).
  전사 낱말 파일에는 낱말 집합만 있어(본문·순서 없음) 21 6-2 의 '글자 n-gram 포함률' 대신 '한 창 안에 모두' 로 본다.

예문은 모두 합성이다(시험: tests/test_viewer_score.py).
"""
from __future__ import annotations

import base64
import binascii
import bisect
import math
import re
import struct
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
#: 0.3(2026-10-23, 22 7-1 의 4): 반복 여부에 '전사 베끼기'(repeat.talk_copy) — 스트리머가 방금 한 말의 낱말만 옮긴 서술.
#:     같은 판에서(G 둘째) 문턱을 THRESHOLDS 한 표로 모으고(guide-config values.viewer_score), 주제 적합성의 선택 입력
#:     (창별 벡터·동의어·flow_head2)과 habit_soft_ok_only 를 더했다. 선택 입력·켜고 끄는 칸은 기본으로 결과를 바꾸지 않는다.
VERSION = "0.3"

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
TOPIC_RUN_HIT = "topic.run_hit"            # (벡터) 되돌아보기 앞으로 이어진 같은 주제 줄기의 전사 낱말과 겹침 → ok
TOPIC_RELATED_HIT = "topic.related_hit"    # (벡터) 지금 주제와 가까운 앞선 창의 전사 낱말과 겹침 → ok
#: 창별 벡터가 있을 때만 나오는 코드와, 그때만 결과 숫자에 붙는 칸(벡터·동의어가 없으면 숫자 칸은 이전과 같다)
TOPIC_VECTOR_CODES = (TOPIC_RUN_HIT, TOPIC_RELATED_HIT)
TOPIC_OPTIONAL_NUMS = ("run", "run_sec", "rel", "syn")
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
REPEAT_TALK_COPY = "repeat.talk_copy"      # 전사 베끼기: 내용 낱말이 모두 방금 전사 낱말(0.3, _talk_copy)

REASON_CODES: dict[str, tuple[str, ...]] = {
    "topic": (TOPIC_FLOW_HIT, TOPIC_TALK_HITS, TOPIC_TALK_ONE, TOPIC_NO_OVERLAP, TOPIC_NO_WORDS, TOPIC_NO_CONTEXT,
              TOPIC_RUN_HIT, TOPIC_RELATED_HIT),
    "expand": (EXPAND_OPEN_QUESTION, EXPAND_YN_CONCRETE, EXPAND_YN_QUESTION, EXPAND_CONCRETE, EXPAND_THIN,
               EXPAND_TOO_SHORT, EXPAND_TOO_LONG, EXPAND_REACTION_ONLY),
    "repeat": (REPEAT_FRESH, REPEAT_SELF_SAME, REPEAT_SELF_SIMILAR, REPEAT_COPY_SAME, REPEAT_COPY_SIMILAR,
               REPEAT_HABIT, REPEAT_TALK_COPY),
}

# 화면 한 줄 시안(숫자만 끼운다. 쓴 글·낱말은 넣지 않는다). UI 가 자기 문구를 써도 된다.
REASON_TEXT_KO: dict[str, str] = {
    TOPIC_FLOW_HIT: "지금 방송 주제 낱말과 {flow}개 겹쳐요",
    TOPIC_TALK_HITS: "방금 {window_sec}초 동안 나온 말과 {talk}개 겹쳐요",
    TOPIC_TALK_ONE: "방금 나온 말과 1개 겹쳐요",
    TOPIC_NO_OVERLAP: "방금 {window_sec}초 동안의 주제와 겹치는 낱말이 없어요",
    TOPIC_NO_WORDS: "낱말이 없어 주제를 볼 수 없어요",
    TOPIC_NO_CONTEXT: "이 시각 앞에 주제·전사 낱말이 없어 판단하지 않았어요",
    TOPIC_RUN_HIT: "조금 전까지 {run_sec}초 이어진 같은 주제의 말과 {run}개 겹쳐요",
    TOPIC_RELATED_HIT: "앞서 같은 주제를 말할 때 나온 말과 {rel}개 겹쳐요",
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
    REPEAT_TALK_COPY: "방금 {window_sec}초 동안 스트리머가 한 말의 낱말만 옮겼어요({talk_copy}개). 내 생각을 하나 더해 보세요",
}

# ───────────────────────────────────────────────────────────── 문턱 (guide-config values.viewer_score)

@dataclass(frozen=True)
class Threshold:
    """문턱 하나: guide-config 칸 이름(키) → 설정 칸·기본값·범위.

    - attr: `ViewerScoreSettings` 칸 이름. default: 그 칸의 기본값(**코드 단위**: ms·개수·% 또는 bool).
      문턱 숫자는 이 표에만 적는다(옛 `DEFAULT_*` 이름은 이 표에서 나온다).
    - scale: guide-config 값 × scale = 코드 값(초 단위 칸은 1000). bool 칸은 1.
    - lo·hi: guide-config 단위의 허용 범위(양 끝 포함). schemas/guide-config.schema.json `$defs/viewerScore` 의
      minimum·maximum 과 같아야 한다(tests/test_viewer_score_calib.py 가 맞춰 본다). bool 칸은 (False, True).
    """
    attr: str
    default: int | bool
    scale: int
    lo: int | bool
    hi: int | bool
    note: str = ""

    @property
    def is_bool(self) -> bool:
        return isinstance(self.default, bool)


#: 채점 문턱 묶음(22 7-1 의 2번, D-21 4). guide-config `values.viewer_score` 의 칸 이름 → `Threshold`.
#: 기본값은 손으로 고른 시작값이다. 보정 측정(tools/viewer_score_calib.py, docs/measurements/2026-10-23-viewer-score-calib.md)
#: 의 후보는 시범 뒤 더 큰 표본으로 다시 잰 다음에 바꾼다(표본 30건 — 과적합).
THRESHOLDS: dict[str, Threshold] = {
    "topic_lookback_sec": Threshold("topic_lookback_ms", 120_000, 1000, 10, 600,
                                    "주제 흐름 덩어리: 보낸 시각 앞 이 초와 겹치는 덩어리"),
    "transcript_lookback_sec": Threshold("transcript_lookback_ms", 90_000, 1000, 60, 120,
                                         "전사 낱말: 보낸 시각 앞 이 초 안에 시작한 창(설계 60~120초)"),
    "topic_good_flow_hits": Threshold("topic_good_flow_hits", 1, 1, 1, 10, "주제 흐름 낱말이 이만큼 겹치면 good"),
    "topic_good_talk_hits": Threshold("topic_good_talk_hits", 2, 1, 1, 10, "전사 낱말이 이만큼 겹치면 good"),
    "topic_ok_talk_hits": Threshold("topic_ok_talk_hits", 1, 1, 1, 10, "전사 낱말이 이만큼 겹치면 ok"),
    "flow_head2": Threshold("flow_head2", False, 1, False, True,
                            "켜면 주제 흐름 낱말에 한해 2자 낱말의 머리 겹침도 센다('보스'~'보스전', 22 5-2 의 3)"),
    "expand_min_chars": Threshold("expand_min_chars", 4, 1, 1, 50,
                                  "보이는 글자(이모티콘·공백 뺌)가 이보다 적으면 너무 짧음"),
    "expand_max_chars": Threshold("expand_max_chars", 60, 1, 10, 500, "이보다 많으면 너무 김(good → ok)"),
    "expand_concrete_words": Threshold("expand_concrete_words", 2, 1, 1, 10,
                                       "내용 낱말이 이만큼이면 구체적(주제 겹침 1개 이상이어도 구체적)"),
    "copy_window_sec": Threshold("copy_window_ms", 45_000, 1000, 30, 60,
                                 "베끼기: 보낸 시각 앞 이 초의 실제·가상 채팅(설계 30~60초)"),
    "ngram": Threshold("ngram", 5, 1, 2, 10, "글자 n-gram 길이"),
    "copy_weak_pct": Threshold("copy_weak_pct", 80, 1, 1, 100, "베끼기 겹침 % 이상이면 weak (20 8장 5번 copy_max 0.8)"),
    "copy_ok_pct": Threshold("copy_ok_pct", 50, 1, 1, 100, "베끼기 겹침 % 이상이면 ok"),
    "self_weak_pct": Threshold("self_weak_pct", 60, 1, 1, 100, "앞서 쓴 말과 겹침 % 이상이면 weak"),
    "self_ok_pct": Threshold("self_ok_pct", 40, 1, 1, 100, "앞서 쓴 말과 겹침 % 이상이면 ok"),
    "self_lookback": Threshold("self_lookback", 30, 1, 1, 1000, "비슷함을 볼 앞선 내 채팅 수(같음은 이 훈련 전부)"),
    "habit_lookback": Threshold("habit_lookback", 5, 1, 1, 50, "말버릇을 볼 앞선 내 채팅 수"),
    "habit_ok_min": Threshold("habit_ok_min", 2, 1, 1, 50, "그 가운데 같은 말버릇이 이만큼이면 ok"),
    "habit_weak_min": Threshold("habit_weak_min", 3, 1, 1, 50, "이만큼이면 weak"),
    "habit_soft_ok_only": Threshold("habit_soft_ok_only", False, 1, False, True,
                                    "켜면 끝 표지(이모티콘·웃음 자모·'~'·'!')와 흔한 첫 낱말(오늘·진짜 …)의 되풀이는 "
                                    "ok 까지만(22 5-2 의 4). 끄면 끝 기호 '~'·'!' 만 ok 까지"),
    "talk_copy_ok_words": Threshold("talk_copy_ok_words", 2, 1, 1, 10,
                                    "전사 베끼기: 서술의 내용 낱말이 이만큼 이상이고 모두 방금 전사 낱말이면 ok(0.3)"),
    "talk_copy_weak_words": Threshold("talk_copy_weak_words", 3, 1, 1, 10,
                                      "전사 베끼기: 이만큼 이상이고 모두 한 전사 창 안의 낱말이면 weak(0.3)"),
    # 창별 주제 벡터 문턱(G 둘째 통합에서 숫자로 정함 — 벡터 담당 측정의 기본 20/50/6, 22 측정은 docs/measurements 참고).
    # 벡터가 없으면(파일이 없거나 창이 vec_min_windows 보다 적음) 이 칸들은 결과에 아무 영향이 없다.
    "vec_run_max_sec": Threshold("vec_run_max_ms", 300_000, 1000, 0, 1800,
                                 "(벡터) 같은 주제 줄기를 되돌아보기 앞으로 늘리는 한도(보낸 시각 앞 이 초). 0 이면 끔"),
    "vec_boundary_pct": Threshold("vec_boundary_pct", 20, 1, 1, 99,
                                  "(벡터) 이웃 창 유사도가 그 파일의 이 분위(%) 밑이면 주제 경계"),
    "vec_related_pct": Threshold("vec_related_pct", 50, 1, 1, 99,
                                 "(벡터) 앞선 창이 지금 주제 창과 그 파일 이웃 창 유사도의 이 분위 이상이면 '관련'"),
    "vec_related_max": Threshold("vec_related_max", 6, 1, 0, 50, "(벡터) 관련 창 최대 수(가까운 순). 0 이면 끔"),
    "vec_min_windows": Threshold("vec_min_windows", 12, 1, 2, 100_000,
                                 "(벡터) 벡터 창이 이보다 적으면 분위를 믿지 못해 벡터를 쓰지 않는다"),
    "vec_max_gap_sec": Threshold("vec_max_gap_ms", 20_000, 1000, 1, 300,
                                 "(벡터) 이웃 창 사이가 이 초보다 벌어지면(말 없는 틈) 이어지지 않은 것(= 경계)"),
    "min_grams": Threshold("min_grams", 2, 1, 1, 10,
                           "내 글의 n-gram 이 이보다 적으면(짧은 흔한 말) 비율 판정을 하지 않고 같음만 본다"),
    "talk_common_pct": Threshold("talk_common_pct", 20, 1, 5, 100,
                                 "전사 창의 이 % 이상에 나오는 낱말은 주제 풀에서 뺀다(기능어, 문서 빈도)"),
    "talk_common_min_windows": Threshold("talk_common_min_windows", 30, 1, 2, 100_000,
                                         "전사 창이 이보다 적으면 문서 빈도 거르기를 하지 않는다(표본이 작음)"),
}

# 옛 이름(시험·다른 모듈이 쓴다). 값은 THRESHOLDS 한 곳에서 온다.
DEFAULT_TOPIC_LOOKBACK_MS = THRESHOLDS["topic_lookback_sec"].default
DEFAULT_TRANSCRIPT_LOOKBACK_MS = THRESHOLDS["transcript_lookback_sec"].default
TRANSCRIPT_LOOKBACK_RANGE_MS = (THRESHOLDS["transcript_lookback_sec"].lo * 1000,
                                THRESHOLDS["transcript_lookback_sec"].hi * 1000)
DEFAULT_TOPIC_GOOD_FLOW_HITS = THRESHOLDS["topic_good_flow_hits"].default
DEFAULT_TOPIC_GOOD_TALK_HITS = THRESHOLDS["topic_good_talk_hits"].default
DEFAULT_TOPIC_OK_TALK_HITS = THRESHOLDS["topic_ok_talk_hits"].default
DEFAULT_FLOW_HEAD2 = THRESHOLDS["flow_head2"].default
DEFAULT_EXPAND_MIN_CHARS = THRESHOLDS["expand_min_chars"].default
DEFAULT_EXPAND_MAX_CHARS = THRESHOLDS["expand_max_chars"].default
DEFAULT_EXPAND_CONCRETE_WORDS = THRESHOLDS["expand_concrete_words"].default
DEFAULT_COPY_WINDOW_MS = THRESHOLDS["copy_window_sec"].default
COPY_WINDOW_RANGE_MS = (THRESHOLDS["copy_window_sec"].lo * 1000, THRESHOLDS["copy_window_sec"].hi * 1000)
DEFAULT_NGRAM = THRESHOLDS["ngram"].default
DEFAULT_COPY_WEAK_PCT = THRESHOLDS["copy_weak_pct"].default
DEFAULT_COPY_OK_PCT = THRESHOLDS["copy_ok_pct"].default
DEFAULT_SELF_WEAK_PCT = THRESHOLDS["self_weak_pct"].default
DEFAULT_SELF_OK_PCT = THRESHOLDS["self_ok_pct"].default
DEFAULT_SELF_LOOKBACK = THRESHOLDS["self_lookback"].default
DEFAULT_HABIT_LOOKBACK = THRESHOLDS["habit_lookback"].default
DEFAULT_HABIT_OK_MIN = THRESHOLDS["habit_ok_min"].default
DEFAULT_HABIT_WEAK_MIN = THRESHOLDS["habit_weak_min"].default
DEFAULT_HABIT_SOFT_OK_ONLY = THRESHOLDS["habit_soft_ok_only"].default
DEFAULT_TALK_COPY_OK_WORDS = THRESHOLDS["talk_copy_ok_words"].default
DEFAULT_TALK_COPY_WEAK_WORDS = THRESHOLDS["talk_copy_weak_words"].default
DEFAULT_VEC_RUN_MAX_MS = THRESHOLDS["vec_run_max_sec"].default
DEFAULT_VEC_BOUNDARY_PCT = THRESHOLDS["vec_boundary_pct"].default
DEFAULT_VEC_RELATED_PCT = THRESHOLDS["vec_related_pct"].default
DEFAULT_VEC_RELATED_MAX = THRESHOLDS["vec_related_max"].default
DEFAULT_VEC_MIN_WINDOWS = THRESHOLDS["vec_min_windows"].default
DEFAULT_VEC_MAX_GAP_MS = THRESHOLDS["vec_max_gap_sec"].default
DEFAULT_MIN_GRAMS = THRESHOLDS["min_grams"].default
DEFAULT_TALK_COMMON_PCT = THRESHOLDS["talk_common_pct"].default
DEFAULT_TALK_COMMON_MIN_WINDOWS = THRESHOLDS["talk_common_min_windows"].default


@dataclass(frozen=True)
class ViewerScoreSettings:
    """채점 문턱. guide-config `values.viewer_score` 에서 `settings_from_values` 로 만든다. 칸·기본값은 THRESHOLDS."""
    topic_lookback_ms: int = DEFAULT_TOPIC_LOOKBACK_MS
    transcript_lookback_ms: int = DEFAULT_TRANSCRIPT_LOOKBACK_MS
    topic_good_flow_hits: int = DEFAULT_TOPIC_GOOD_FLOW_HITS
    topic_good_talk_hits: int = DEFAULT_TOPIC_GOOD_TALK_HITS
    topic_ok_talk_hits: int = DEFAULT_TOPIC_OK_TALK_HITS
    flow_head2: bool = DEFAULT_FLOW_HEAD2
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
    habit_soft_ok_only: bool = DEFAULT_HABIT_SOFT_OK_ONLY
    talk_copy_ok_words: int = DEFAULT_TALK_COPY_OK_WORDS
    talk_copy_weak_words: int = DEFAULT_TALK_COPY_WEAK_WORDS
    vec_run_max_ms: int = DEFAULT_VEC_RUN_MAX_MS
    vec_boundary_pct: int = DEFAULT_VEC_BOUNDARY_PCT
    vec_related_pct: int = DEFAULT_VEC_RELATED_PCT
    vec_related_max: int = DEFAULT_VEC_RELATED_MAX
    vec_min_windows: int = DEFAULT_VEC_MIN_WINDOWS
    vec_max_gap_ms: int = DEFAULT_VEC_MAX_GAP_MS
    min_grams: int = DEFAULT_MIN_GRAMS
    talk_common_pct: int = DEFAULT_TALK_COMMON_PCT
    talk_common_min_windows: int = DEFAULT_TALK_COMMON_MIN_WINDOWS


DEFAULT_SETTINGS = ViewerScoreSettings()


def settings_from_values(values: Mapping | None) -> tuple[ViewerScoreSettings, list[str]]:
    """guide-config `values`(또는 그 안의 `viewer_score` 칸) → (설정, 메모 목록).

    `values.viewer_score` 가 있으면 그 칸을, 없으면 받은 dict 자체를 viewer_score 칸으로 본다.
    없는 칸은 기본값(THRESHOLDS). 형식이 틀리거나(숫자 칸에 bool·문자열, bool 칸에 숫자) guide 단위 범위 밖이면
    그 칸만 기본값으로 두고 메모에 칸 이름을 적는다. 모르는 칸은 무시한다(스키마가 막는다).
    ok 문턱이 weak·good 문턱과 뒤집히면(예: copy_ok_pct > copy_weak_pct) 둘 다 기본값으로 돌린다.
    메모에는 칸 이름만 넣는다(값은 넣지 않는다).
    """
    notes: list[str] = []
    if not isinstance(values, Mapping):
        return DEFAULT_SETTINGS, notes
    v = values.get("viewer_score") if isinstance(values.get("viewer_score"), Mapping) else values
    changes: dict[str, int | bool] = {}
    for key, th in THRESHOLDS.items():
        if key not in v:
            continue
        raw = v.get(key)
        if th.is_bool:
            if not isinstance(raw, bool):
                notes.append(f"viewer_score.{key}: 참·거짓이 아님 → 기본값")
                continue
            changes[th.attr] = raw
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw:
            notes.append(f"viewer_score.{key}: 숫자가 아님 → 기본값")
            continue
        if not th.lo <= raw <= th.hi:
            notes.append(f"viewer_score.{key}: 범위 밖 → 기본값")
            continue
        changes[th.attr] = int(round(float(raw) * th.scale))
    s = replace(DEFAULT_SETTINGS, **changes)
    for ok_attr, weak_attr in (("copy_ok_pct", "copy_weak_pct"), ("self_ok_pct", "self_weak_pct"),
                               ("habit_ok_min", "habit_weak_min"), ("topic_ok_talk_hits", "topic_good_talk_hits"),
                               ("expand_min_chars", "expand_max_chars"),
                               ("talk_copy_ok_words", "talk_copy_weak_words")):
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


# ───────────────────────────────────────────────────────────── 주제 축 선택 입력: 창별 벡터 · 동의어 (G 둘째)

#: 엔진 engine/topic_vectors.py 가 쓰는 문서(schemas/topic-vectors.schema.json). 앱은 이 모듈로만 푼다(파일은 부르는 쪽이 읽음).
TOPIC_VECTORS_SCHEMA = "chatcoach/topic-vectors"
TOPIC_VECTORS_SCHEMA_VERSION = 1
VEC_ENCODINGS = ("f16le-b64", "round4")      # float16 리틀엔디언 base64 · 소수 4자리 수 목록
VEC_MAX_DIM = 4096

# 벡터 문턱(vec_*)은 THRESHOLDS 표에 있다(DEFAULT_VEC_* 는 그 표에서 나온 이름). 아래는 문턱이 아니라 속도 상수다.
VEC_CURRENT_MAX = 3                  # 관련 창을 찾을 때 견주는 '지금 주제' 창 수(마지막 것부터). 채점 한 번 = 창 수 × 이 수 내적


def _vs(s: "ViewerScoreSettings", name: str, default: int) -> int:
    """설정 칸(정수) 읽기. 설정 대신 다른 객체를 줘도(시험·옛 호출) 칸이 없거나 정수가 아니면 default."""
    v = getattr(s, name, default)
    return default if isinstance(v, bool) or not isinstance(v, int) else v


def decode_vector(value, encoding: str, dim: int) -> tuple[float, ...] | None:
    """topic-vectors 창 하나의 `v` → 수 튜플(정규화 전). 꼴이 틀리면 None(그 창만 버린다)."""
    if isinstance(dim, bool) or not isinstance(dim, int) or not 1 <= dim <= VEC_MAX_DIM:
        return None
    if encoding == "f16le-b64":
        if not isinstance(value, str):
            return None
        try:
            raw = base64.b64decode(value.encode("ascii"), validate=True)
        except (binascii.Error, ValueError, UnicodeEncodeError):
            return None
        if len(raw) != 2 * dim:
            return None
        xs = struct.unpack(f"<{dim}e", raw)
    elif encoding == "round4":
        if not isinstance(value, list) or len(value) != dim:
            return None
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
            return None
        xs = tuple(float(x) for x in value)
    else:
        return None
    if not all(math.isfinite(x) for x in xs):
        return None
    return tuple(xs)


def vectors_from_doc(doc) -> list[tuple[int, int, tuple[float, ...]]]:
    """topic-vectors 문서(dict) → [(start_ms, end_ms, 벡터)]. 형식이 다르면 빈 목록(→ 벡터 없이 지금처럼 채점).

    엄한 검사(해시·칸 모양)는 engine/topic_vectors.validate 몫이다. 여기서는 읽을 수 있는 창만 남긴다."""
    if not isinstance(doc, Mapping) or doc.get("schema") != TOPIC_VECTORS_SCHEMA \
            or doc.get("schema_version") != TOPIC_VECTORS_SCHEMA_VERSION or doc.get("time_axis") != "vod_ms":
        return []
    enc, dim, wins = doc.get("encoding"), doc.get("dim"), doc.get("windows")
    if enc not in VEC_ENCODINGS or not isinstance(wins, list):
        return []
    out = []
    for w in wins:
        if not isinstance(w, Mapping):
            continue
        a, b = _as_int(w.get("start_ms")), _as_int(w.get("end_ms"))
        if a is None or b is None or b <= a:
            continue
        v = decode_vector(w.get("v"), enc, dim)
        if v is not None:
            out.append((a, b, v))
    return out


def _unit_vec(v: Sequence[float]) -> tuple[float, ...] | None:
    n = math.sqrt(math.fsum(x * x for x in v))
    if not n or not math.isfinite(n):
        return None
    return tuple(x / n for x in v)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    """내적(같은 순서로 더해 결정적). fsum 보다 열 배쯤 빠르다 — 채점 한 번에 창 수 × 3 번 부른다."""
    return sum(map(float.__mul__, a, b))


def _quantile(sorted_vals: Sequence[float], pct: int) -> float:
    """가장 가까운 순위 분위(결정적). pct 0~100."""
    n = len(sorted_vals)
    k = max(0, min(n - 1, math.ceil(n * max(0, min(100, pct)) / 100) - 1))
    return sorted_vals[k]


@dataclass(frozen=True, repr=False)
class _Vecs:
    """창별 벡터(정규화)와 이웃 창 유사도. 문턱(경계·관련)은 그 파일의 이웃 창 유사도 분위로 정한다(모델 눈금에 기대지 않음)."""
    starts: tuple[int, ...]
    ends: tuple[int, ...]
    vecs: tuple[tuple[float, ...], ...]
    adj: tuple[float | None, ...]     # adj[i] = cos(i-1, i). 이어지지 않으면 None. adj[0] = None
    boundary_cut: float
    related_cut: float

    def __repr__(self) -> str:   # 벡터 값을 보이지 않는다
        return f"_Vecs(windows={len(self.starts)})"

    @classmethod
    def build(cls, rows: Iterable, s: "ViewerScoreSettings") -> "_Vecs | None":
        if isinstance(rows, Mapping):
            rows = vectors_from_doc(rows)
        seen: dict[int, tuple[int, int, tuple[float, ...]]] = {}
        dim = None
        for r in rows or ():
            if not isinstance(r, (tuple, list)) or len(r) < 3:
                continue
            a, b, v = _as_int(r[0]), _as_int(r[1]), r[2]
            if a is None or b is None or b <= a or not isinstance(v, (tuple, list)) or not v:
                continue
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v):
                continue
            if dim is None:
                dim = len(v)
            if len(v) != dim or a in seen:       # 차원이 다른 줄·같은 시작의 둘째 줄은 버린다
                continue
            u = _unit_vec(v)
            if u is not None:
                seen[a] = (a, b, u)
        items = [seen[k] for k in sorted(seen)]
        if len(items) < max(2, _vs(s, "vec_min_windows", DEFAULT_VEC_MIN_WINDOWS)):
            return None
        gap = _vs(s, "vec_max_gap_ms", DEFAULT_VEC_MAX_GAP_MS)
        adj: list[float | None] = [None]
        for i in range(1, len(items)):
            adj.append(_dot(items[i - 1][2], items[i][2]) if items[i][0] - items[i - 1][1] <= gap else None)
        vals = sorted(x for x in adj if x is not None)
        if len(vals) < 2:
            return None
        return cls(tuple(r[0] for r in items), tuple(r[1] for r in items), tuple(r[2] for r in items), tuple(adj),
                   _quantile(vals, _vs(s, "vec_boundary_pct", DEFAULT_VEC_BOUNDARY_PCT)),
                   _quantile(vals, _vs(s, "vec_related_pct", DEFAULT_VEC_RELATED_PCT)))

    def _cut(self, i: int) -> bool:
        """창 i-1 과 i 사이가 주제 경계인가(이어지지 않음 포함)."""
        a = self.adj[i]
        return a is None or a < self.boundary_cut

    def expand(self, sent_ms: int, base_lo: int, s: "ViewerScoreSettings"
               ) -> tuple[int | None, tuple[tuple[int, int], ...]] | None:
        """(같은 주제 줄기의 시작 시각 또는 None, 관련 앞선 창 [(start, end)]) 또는 None(되돌아보기 안에 벡터 창이 없음).

        - 되돌아보기 [base_lo, sent_ms) 안에 시작한 창들 가운데 마지막 경계 뒤가 '지금 주제' 다.
        - 되돌아보기 안에 경계가 없으면 그 앞으로 경계·틈을 만날 때까지(보낸 시각 앞 vec_run_max_ms 까지) 줄기를 늘린다.
        - 관련 창: 줄기(없으면 되돌아보기) 앞에 끝난 창 가운데 지금 주제 창 어느 것과의 유사도가 related_cut 이상인 것,
          가까운 순 vec_related_max 개(같으면 이른 창)."""
        c = bisect.bisect_left(self.starts, sent_ms) - 1
        a0 = bisect.bisect_left(self.starts, base_lo)
        if c < a0:
            return None
        cur0 = a0
        for i in range(c, a0, -1):
            if self._cut(i):
                cur0 = i
                break
        run_lo = None
        run_max = _vs(s, "vec_run_max_ms", DEFAULT_VEC_RUN_MAX_MS)
        if cur0 == a0 and run_max > 0:
            j, limit = a0, sent_ms - run_max
            while j > 0 and not self._cut(j) and self.starts[j - 1] >= limit:
                j -= 1
            if j < a0:
                run_lo = self.starts[j]
        rel: list[tuple[float, int, int]] = []
        rel_max = _vs(s, "vec_related_max", DEFAULT_VEC_RELATED_MAX)
        if rel_max > 0:
            stop = run_lo if run_lo is not None else base_lo
            cur = self.vecs[max(cur0, c - VEC_CURRENT_MAX + 1):c + 1]   # 지금 주제의 마지막 창 몇 개(속도)
            for k in range(bisect.bisect_left(self.starts, stop)):
                if self.ends[k] > stop:
                    continue
                sim = max(_dot(v, self.vecs[k]) for v in cur)
                if sim >= self.related_cut:
                    rel.append((-sim, self.starts[k], self.ends[k]))
            rel.sort()
        return run_lo, tuple((a, b) for _sim, a, b in rel[:rel_max])


class _Synonyms:
    """동의어 표: 낱말 꼴 → 같은 묶음의 다른 꼴들. repr 에는 개수만(낱말이 로그에 새지 않게)."""
    __slots__ = ("_m",)

    def __init__(self, m: Mapping[str, frozenset[str]] | None = None):
        self._m = dict(m or {})

    def get(self, w: str) -> frozenset[str]:
        return self._m.get(w, frozenset())

    def __len__(self) -> int:
        return len(self._m)

    def __repr__(self) -> str:
        return f"_Synonyms(words={len(self._m)})"


NO_SYNONYMS = _Synonyms()


def parse_synonyms(text: str) -> dict[str, frozenset[str]]:
    """동의어 묶음 글(파일 내용) → {낱말 꼴: 같은 묶음의 다른 꼴들}. 파일은 부르는 쪽이 읽는다(이 모듈은 열지 않는다).

    한 줄에 한 묶음, 낱말은 쉼표·세로줄·공백으로 나눈다. '#' 뒤는 주석. 낱말은 채점과 같은 규칙(`word_set`)으로 꼴을 만든다
    ('고양이' → '고양이'·'고양'). 꼴이 하나뿐인 묶음은 버린다. 한 낱말이 여러 묶음에 있으면 합친다."""
    out: dict[str, set[str]] = {}
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0]
        entries = [e for e in re.split(r"[,|\s]+", line) if e]
        groups = [word_set(e) for e in entries]
        allf = frozenset().union(*groups) if groups else frozenset()
        if len([g for g in groups if g]) < 2:
            continue
        for g in groups:
            for w in g:
                out.setdefault(w, set()).update(allf - g)
    return {w: frozenset(v - {w}) for w, v in out.items() if v - {w}}


def _synonyms_of(synonyms) -> _Synonyms:
    if synonyms is None:
        return NO_SYNONYMS
    if isinstance(synonyms, _Synonyms):
        return synonyms
    if isinstance(synonyms, str):
        return _Synonyms(parse_synonyms(synonyms))
    m: dict[str, set[str]] = {}
    if isinstance(synonyms, Mapping):
        for k, vs in synonyms.items():
            if isinstance(k, str) and isinstance(vs, Iterable) and not isinstance(vs, str):
                m.setdefault(k, set()).update(x for x in vs if isinstance(x, str) and x != k)
    else:                                   # 묶음 목록 [[낱말, …], …]
        return _Synonyms(parse_synonyms("\n".join(" ".join(x for x in g if isinstance(x, str))
                                                  for g in synonyms if isinstance(g, (list, tuple, set, frozenset)))))
    return _Synonyms({k: frozenset(v) for k, v in m.items() if v})


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
    - topic_vectors(선택): 엔진이 미리 계산한 창별 전사 벡터 — topic-vectors 문서(dict) 또는 `(start_ms, end_ms, vector)`.
      창이 `vec_min_windows` 보다 적거나 못 읽으면 None(지금처럼). 주제 적합성만 쓴다(모듈 머리말).
    - synonyms(선택): `parse_synonyms` 결과 dict, 묶음 글(str), 또는 묶음 목록. 없으면 동의어 없이.

    repr 에는 개수만 나온다(채팅 본문·전사 낱말이 로그·assert 출력에 새지 않게).
    """
    topic: _Timed
    talk: _Timed
    chat_ms: tuple[int, ...]
    chat_keys: tuple[tuple[str, frozenset[str]], ...]
    ngram: int = DEFAULT_NGRAM
    talk_common: frozenset[str] = frozenset()
    vec: _Vecs | None = None
    synonyms: _Synonyms = NO_SYNONYMS

    def __repr__(self) -> str:
        return (f"ScoreContext(topic={len(self.topic.items)}, talk={len(self.talk.items)}, chats={len(self.chat_ms)}, "
                f"talk_common={len(self.talk_common)}, ngram={self.ngram}"
                f"{'' if self.vec is None else f', vec={len(self.vec.starts)}'}"
                f"{'' if not len(self.synonyms) else f', synonyms={len(self.synonyms)}'})")

    @classmethod
    def build(cls, topic_blocks: Iterable = (), transcript: Iterable = (), chats: Iterable = (), *,
              settings: ViewerScoreSettings | None = None, topic_vectors=None, synonyms=None) -> "ScoreContext":
        s = settings or DEFAULT_SETTINGS
        vec = _Vecs.build(topic_vectors, s) if topic_vectors is not None else None
        syn = _synonyms_of(synonyms)
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
                   tuple((r[1], r[2]) for r in cs), s.ngram, common, vec, syn)

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
    """(결과, 주제와 겹친 내 낱말들). 겹친 낱말은 대화 확장성의 '구체적' 판단과 말버릇 단위 거르기에도 쓴다.

    선택 입력(모듈 머리말): 동의어(ctx.synonyms)·주제 흐름 2자 머리(flow_head2)는 겹침 자체를 넓힌다(겹친 낱말에 든다).
    창별 벡터(ctx.vec)는 겹침이 없을 때만 '같은 주제 줄기'·'관련 앞선 창' 풀을 더 보고 weak → ok 로만 올린다(겹친 낱말에
    넣지 않는다 — 다른 축은 그대로). 선택 입력이 있을 때만 숫자 칸(syn·run·run_sec·rel)이 붙는다."""
    window_sec = s.transcript_lookback_ms // 1000
    base_lo = sent_ms - s.transcript_lookback_ms
    flow_pool = ctx.topic.words_between(sent_ms - s.topic_lookback_ms, sent_ms)
    talk_pool = ctx.talk.words_started(base_lo, sent_ms)
    syn = ctx.synonyms
    head2 = getattr(s, "flow_head2", DEFAULT_FLOW_HEAD2) is True

    def forms(u: frozenset[str]) -> list[str]:
        if not len(syn):
            return sorted(u)
        ext = set(u)
        for w in u:
            ext |= syn.get(w)
        return sorted(ext)

    def flow_match(w: str) -> bool:
        return _match(w, flow_pool) or (head2 and _match_head2(w, flow_pool))

    flow_hit = tuple(u for u in units if any(flow_match(w) for w in forms(u)))
    talk_hit = tuple(u for u in units if any(_match(w, talk_pool) for w in forms(u)))
    hit = tuple(u for u in units if u in flow_hit or u in talk_hit)
    nums = dict(flow=len(flow_hit), talk=len(talk_hit), words=len(units), window_sec=window_sec)
    if len(syn):   # 동의어로만 겹친 낱말 수(같은 낱말·꼴로는 안 겹침)
        nums["syn"] = sum(1 for u in hit if not any(flow_match(w) or _match(w, talk_pool) for w in sorted(u)))
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
    ext = ctx.vec.expand(sent_ms, base_lo, s) if ctx.vec is not None else None
    if ext is not None:
        run_lo, related = ext
        run_pool = ctx.talk.words_started(run_lo, base_lo) if run_lo is not None else frozenset()
        rel_pool: set[str] = set()
        for a, b in related:
            rel_pool |= ctx.talk.words_started(a, b)
        run_hit = sum(1 for u in units if any(_match(w, run_pool) for w in forms(u))) if run_pool else 0
        rel_hit = sum(1 for u in units if any(_match(w, frozenset(rel_pool)) for w in forms(u))) if rel_pool else 0
        nums.update(run=run_hit, rel=rel_hit, run_sec=(sent_ms - run_lo) // 1000 if run_lo is not None else 0)
        if run_hit:
            return _result("topic", OK, TOPIC_RUN_HIT, **nums), hit
        if rel_hit:
            return _result("topic", OK, TOPIC_RELATED_HIT, **nums), hit
    return _result("topic", WEAK, TOPIC_NO_OVERLAP, **nums), hit


def _match_head2(word: str, pool: frozenset[str]) -> bool:
    """2자 머리 겹침(주제 흐름 풀에만, flow_head2): 한쪽이 2자이고 다른 쪽(3자 이상)이 그것으로 시작('보스' ~ '보스전')."""
    for p in pool:
        short, long_ = (word, p) if len(word) <= len(p) else (p, word)
        if len(short) == 2 and len(long_) >= 3 and long_.startswith(short):
            return True
    return False


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


def _strong_units(units: frozenset[str], soft: bool = False) -> frozenset[str]:
    """weak 까지 갈 수 있는 말버릇 단위. 끝 기호 '!'·'~'(`t:punct:`)는 늘 뺀다.
    soft(설정 habit_soft_ok_only, 22 5-2 의 4)면 끝 표지 전부(`t:` — 이모티콘·웃음 자모·기호)와 흔한 첫 낱말(`h:` 가운데
    말버릇 후보·흔한 낱말 `_STOP` 을 품은 것, 예: '오늘 …')도 빼서 ok 까지만 가게 한다. 말버릇 후보 낱말(`w:`)은 그대로."""
    if not soft:
        return frozenset(u for u in units if not u.startswith("t:punct:"))
    return frozenset(u for u in units
                     if not u.startswith("t:") and not (u.startswith("h:") and word_set(u[2:]) & _STOP))


def _talk_copy(text: str, sent_ms: int, ctx: ScoreContext, s: ViewerScoreSettings) -> tuple[int, int, int]:
    """전사 베끼기: (내 내용 낱말 수, 그 가운데 앞 copy_window_ms 의 전사 낱말과 겹친 수, 한 전사 창 안에서 겹친 최대 수).

    전사 낱말 파일은 창마다 낱말 **집합**만 남긴다(본문·말한 순서 없음, D-20 3). 그래서 글자 n-gram 포함률 대신
    '내용 낱말이 모두 방금 전사에 있나' 와 '한 창(스트리머의 한두 문장) 안에 모두 있나' 로 본다. 겹침은 주제 적합성과
    같은 `_match`(조사·활용 꼴)와 같은 거름(흔한 낱말·기능어·전사 창 문서 빈도)을 쓴다. 숫자만 돌려준다."""
    units = _content_units(text, ctx.talk_common)
    if not units:
        return 0, 0, 0
    lo, hi = sent_ms - s.copy_window_ms, sent_ms
    pool = ctx.talk.words_started(lo, hi)
    if not pool:
        return len(units), 0, 0
    copied = sum(1 for u in units if any(_match(w, pool) for w in sorted(u)))
    one = 0
    if copied == len(units):   # 한 창 안에 모두 있는지는 다 겹칠 때만 본다
        i = bisect.bisect_left(ctx.talk.starts, lo)
        j = bisect.bisect_left(ctx.talk.starts, hi)
        for _a, _b, ws in ctx.talk.items[i:j]:
            if ws:
                one = max(one, sum(1 for u in units if any(_match(w, ws) for w in sorted(u))))
    return len(units), copied, one


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
    strong = _strong_units(units, s.habit_soft_ok_only)
    habit = habit_strong = 0
    for h in history[-s.habit_lookback:]:
        hu = _habit_units(h, topic_words)
        if units & hu:
            habit += 1
            if strong & hu:
                habit_strong += 1
    # 전사 베끼기(0.3): 질문은 스트리머의 낱말을 써도 베끼기로 보지 않는다(되묻기는 대화 확장성이 따로 본다)
    talk_words, talk_copy, talk_one = _talk_copy(text, sent_ms, ctx, s)
    # 문턱: ok = 내용 낱말 talk_copy_ok_words 개 이상이 모두 방금 전사 낱말, weak = talk_copy_weak_words 개 이상이 모두
    # **한 전사 창(10초)** 안의 낱말(한 말을 그대로 옮김). 값은 THRESHOLDS(guide-config values.viewer_score)
    tc_ok = _vs(s, "talk_copy_ok_words", DEFAULT_TALK_COPY_OK_WORDS)
    tc_weak = _vs(s, "talk_copy_weak_words", DEFAULT_TALK_COPY_WEAK_WORDS)
    statement = ko_cues.question_form(text) is None
    all_copied = statement and talk_words >= 1 and talk_copy == talk_words
    talk_weak = all_copied and talk_words >= tc_weak and talk_one == talk_words
    talk_ok = all_copied and talk_words >= tc_ok
    nums = dict(self_pct=self_pct, copy_pct=copy_pct, copies=copies, habit=habit,
                habit_lookback=s.habit_lookback, window_sec=s.copy_window_ms // 1000,
                talk_copy=talk_copy, talk_one=talk_one, talk_words=talk_words)
    if self_same:
        return _result("repeat", WEAK, REPEAT_SELF_SAME, **nums)
    if copies:
        return _result("repeat", WEAK, REPEAT_COPY_SAME, **nums)
    if self_pct >= s.self_weak_pct:
        return _result("repeat", WEAK, REPEAT_SELF_SIMILAR, **nums)
    if copy_pct >= s.copy_weak_pct:
        return _result("repeat", WEAK, REPEAT_COPY_SIMILAR, **nums)
    if talk_weak:
        return _result("repeat", WEAK, REPEAT_TALK_COPY, **nums)
    if habit_strong >= s.habit_weak_min:   # 끝 기호('!'·'~')만 겹친 것은 ok 까지만
        return _result("repeat", WEAK, REPEAT_HABIT, **{**nums, "habit": habit_strong})
    if self_pct >= s.self_ok_pct:
        return _result("repeat", OK, REPEAT_SELF_SIMILAR, **nums)
    if copy_pct >= s.copy_ok_pct:
        return _result("repeat", OK, REPEAT_COPY_SIMILAR, **nums)
    if talk_ok:
        return _result("repeat", OK, REPEAT_TALK_COPY, **nums)
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
