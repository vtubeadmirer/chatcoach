"""낱말 나누기·정규화 (08 D-20 3, 21 6-2 표의 '(3) 낱말 나누기·정규화').

엔진(engine/retrieve 의 가상 채팅 점수, engine/transcript_words 의 시각별 전사 낱말)과 앱(G 시청자 훈련 채점기
app/core/viewer_score)이 **같은 규칙**으로 낱말을 나누도록 한 곳에 둔다. 엔진이 미리 만든 낱말 파일과 앱이 시청자 채팅에서
뽑은 낱말이 같은 꼴이어야 겹침을 셀 수 있기 때문이다. 규칙을 바꾸면 VERSION 을 올린다(낱말 파일에 이 값이 적힌다).

순수 모듈: 표준 라이브러리(re·unicodedata)만 쓰고 파일·시계·난수를 쓰지 않는다(tests/test_layers).
engine/retrieve.py 의 정규화(_squash·visible_len·tokens)를 그대로 옮겼다 — 결과는 바이트 단위로 같다.

  tokens(text)      점수용 낱말 집합(2자 이상). 이모티콘 코드 {:…:}·자모만의 덩어리(ㅋㅋ·ㅠㅠ)·기호를 지우고 NFKC·소문자,
                    공백으로 나눈 뒤 흔한 조사 하나를 뗀 꼴과 원래 꼴을 둘 다 넣는다.
  squash(text)      대조용 한 덩어리: NFKC·소문자, 한글 음절·영문·숫자만 남기고 공백·기호를 없앰.
  normalize(text)   보이는 글 정규화: NFKC·소문자, 공백 하나로(기호는 남김 — 물음표·의문 어미 찾기용).
  visible_len(text) 이모티콘 코드와 공백을 뺀 글자 수.
  char_ngrams(text) squash 한 글자열의 글자 n-gram 집합(되풀이·베끼기 겹침용, 기본 5).
"""
from __future__ import annotations

import re
import unicodedata

VERSION = "textnorm-v1"

EMOTE_RE = re.compile(r"\{:[^{}:]{1,40}:\}")       # 치지직 이모티콘 코드 {:이름:}
JAMO_RUN_RE = re.compile(r"[ㄱ-ㆎ]+")               # 자모만의 덩어리(ㅋㅋ·ㅎㅎ·ㅠㅠ)
NON_WORD_RE = re.compile(r"[^0-9a-z가-힣\s]+")
_SQUASH_RE = re.compile(r"[^0-9a-z가-힣]+")
_SPACE_RE = re.compile(r"\s+")
#: 뗄 조사(긴 것부터 맞춰 본다). 하나만 뗀다.
PARTICLES = ("에서는", "으로는", "에서", "으로", "한테", "까지", "부터", "처럼", "보다", "이랑",
             "은", "는", "이", "가", "을", "를", "에", "도", "로", "랑", "의", "만", "와", "과")
MIN_TOKEN_LEN = 2


def squash(s: str) -> str:
    """대조용: NFKC, 소문자, 공백·기호 없앰(한글·영문·숫자만)."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return _SQUASH_RE.sub("", s)


def normalize(text: str) -> str:
    """보이는 글 정규화: NFKC·소문자, 공백 덩어리를 하나로, 앞뒤 공백 없앰(기호·이모티콘 코드는 그대로 둔다).
    같은 말 되풀이(정규화 뒤 같음) 대조와 의문 어미 찾기처럼 기호를 남겨야 하는 곳에 쓴다."""
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", text or "").lower()).strip()


def visible_len(text: str) -> int:
    """이모티콘 코드와 공백을 뺀 글자 수."""
    return len(_SPACE_RE.sub("", EMOTE_RE.sub("", text or "")))


def words(text: str) -> list[str]:
    """정규화한 뒤 공백으로 나눈 낱말(순서 유지, 조사 떼기 전, 길이 거르기 전)."""
    s = EMOTE_RE.sub(" ", unicodedata.normalize("NFKC", text or ""))
    s = JAMO_RUN_RE.sub(" ", s).lower()
    s = NON_WORD_RE.sub(" ", s)
    return s.split()


def strip_particle(w: str) -> str:
    """끝의 조사 하나를 뗀 꼴. 떼고 2자 이상 남을 때만 뗀다(못 떼면 그대로)."""
    for p in PARTICLES:
        if len(w) > len(p) + 1 and w.endswith(p):
            return w[: -len(p)]
    return w


def tokens(text: str) -> frozenset[str]:
    """점수용 낱말(2자 이상). 조사를 뗀 꼴과 원래 꼴을 둘 다 넣는다."""
    out = set()
    for w in words(text):
        out.update(x for x in {w, strip_particle(w)} if len(x) >= MIN_TOKEN_LEN)
    return frozenset(out)


def char_ngrams(text: str, n: int = 5) -> frozenset[str]:
    """squash 한 글자열의 글자 n-gram 집합. n 보다 짧으면 그 글자열 하나(비어 있으면 빈 집합)."""
    if n < 1:
        raise ValueError("n 은 1 이상")
    s = squash(text)
    if not s:
        return frozenset()
    if len(s) <= n:
        return frozenset((s,))
    return frozenset(s[i:i + n] for i in range(len(s) - n + 1))


__all__ = ["VERSION", "EMOTE_RE", "JAMO_RUN_RE", "NON_WORD_RE", "PARTICLES", "MIN_TOKEN_LEN",
           "squash", "normalize", "visible_len", "words", "strip_particle", "tokens", "char_ngrams"]
