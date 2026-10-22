"""한국어 채팅 꼴 단서 목록 (08 D-20 3, 21 6-2 '대화 확장성' 의 (1)).

시청자 훈련(G) 채점기 `app/core/viewer_score.py` 가 쓰는 낱말·어미 목록과 작은 판별 함수만 둔다.
AI·모델 없이 규칙으로 본다(첫 판). 목록은 손으로 고른 시작값이고, 시범 뒤 보정 자료(21 6-2 의 (2))로 고친다.

순수 모듈이다(표준 라이브러리만, 파일·시계·네트워크 없음, tests/test_layers.py).
이 파일의 예문은 모두 **합성**이다(실제 채팅·닉네임·전사문이 아니다). 이모티콘 코드도 합성(`{:zz_test_01:}` 꼴).

| 묶음 | 뜻 |
|---|---|
| `QUESTION_ENDINGS` | 물음표가 없어도 묻는 말로 보는 어미(문장 끝) |
| `OPEN_QUESTION_WORDS` | 열린 질문 낱말(뭐·어떻게·왜 …). 낱말 머리에 오면 친다 |
| `OPEN_WEAK_HEADS` | 열린 질문 낱말 가운데 서술에도 흔한 머리(뭔·몇·어떤·누가 …). 묻는 표지가 더 있어야 열린 질문 |
| `OPEN_WORD_EXCEPTIONS` | 열린 질문 낱말로 시작하지만 묻는 말이 아닌 꼴(왜냐하면·언제나·뭔가 …) |
| `YES_NO_MARKERS` | 예/아니오 질문을 알리는 낱말(혹시 …). 있으면 물음표가 없어도 묻는 말 |

예/아니오 질문 꼴 = 열린 질문 낱말이 없고, (물음표 | 끝이 `QUESTION_ENDINGS` | `YES_NO_MARKERS` 낱말) 인 말.
| `FILLER_WORDS` | 내용이 거의 없는 낱말(말버릇 후보). 주제 겹침에서 빼고, 말버릇 되풀이에서 센다 |
| `GENERIC_WORDS` | 어느 방송에나 흔한 낱말(오늘·지금·방송 …). 주제 겹침·내용 낱말 수에서 뺀다 |
| `REACTION_JAMO` | 웃음·울음 같은 반응 자모(ㅋ·ㅎ·ㅠ …) |
"""
from __future__ import annotations

import re
import unicodedata

# 물음표가 없어도 묻는 말로 보는 어미. 문장(공백·기호를 뗀) 끝에 오면 묻는 말.
# '어요'·'아요'·'해요' 처럼 서술과 같은 꼴인 어미는 넣지 않는다(물음표가 있어야 묻는 말).
# 한 글자 '니'·'까' 는 넣지 않는다('어머니'·'그러니까'·'아까' 가 걸린다). 대신 받침 규칙으로 본다:
# 앞 글자 받침이 ㅆ 인 '니'(했니·이겼니), 받침이 ㄹ 인 '까'(할까·먹을까)는 묻는 말(`question_form`).
QUESTION_ENDINGS: tuple[str, ...] = (
    "나요", "까요", "는지", "인가요", "은가요", "는가요", "던가요", "신가요", "셨나요",
    "습니까", "합니까", "입니까", "됩니까", "냐고", "냐", "는가", "던가", "을래", "실래요", "할래요",
    "하니", "없니", "되니", "일까",
)
_JONG_SS = 20    # 받침 ㅆ (유니코드 한글 음절 받침 번호)
_JONG_L = 8      # 받침 ㄹ

# 열린 질문 낱말. 낱말 머리에 오면 친다(뭐가·뭘·어떻게·왜요·몇판 …).
OPEN_QUESTION_WORDS: tuple[str, ...] = (
    "무엇", "무슨", "뭐", "뭘", "뭔", "어떤", "어떻", "어때", "어땠", "어떠", "어째", "왜",
    "언제", "어디", "누구", "누가", "누굴", "몇", "얼마", "어느", "어쩌다", "어쩐",
)

# 열린 질문 낱말 가운데 서술문에도 흔히 쓰이는 머리(뭔가 이상해요·몇 번 해봤어요·어떤 사람이·누가 봐도·얼마나 좋은데요·
# 어디 가서·무슨 일이 있어도·어느 날·언제 한번). 이 머리만 있으면 **묻는 말 표지가 하나 더 있어야** 열린 질문으로 본다:
# 물음표, QUESTION_ENDINGS, 받침 규칙(했니·할까), YES_NO_MARKERS 낱말, 또는 끝이 OPEN_COPULA_ENDINGS(몇 판째예요·어디예요).
OPEN_WEAK_HEADS: tuple[str, ...] = ("뭔", "몇", "어떤", "누가", "얼마", "어디", "무슨", "어느", "언제")

# 서술에 흔한 머리 + 이 끝이면 열린 질문으로 본다(…예요·…이에요·…이야·…임 은 묻는 말에 흔하다).
OPEN_COPULA_ENDINGS: tuple[str, ...] = ("예요", "이에요", "에요", "이야", "임", "인데", "이죠")

# 열린 질문 낱말로 시작하지만 묻는 말이 아닌 꼴(낱말 머리 비교).
OPEN_WORD_EXCEPTIONS: tuple[str, ...] = (
    "왜냐", "뭐든", "뭐라도", "무엇이든", "언제나", "언제든", "어디든", "어디서든", "누구나", "누구든",
    "얼마든", "몇몇", "어떻든", "어느새", "어느덧", "어느정도", "얼마전", "뭔가", "어떻게든",
)

# 예/아니오 질문을 알리는 낱말(낱말 머리 비교). 있으면 물음표가 없어도 묻는 말로 본다.
YES_NO_MARKERS: tuple[str, ...] = ("혹시", "설마", "정말요", "진짜요", "맞나", "맞죠", "아닌가")

# 내용이 거의 없는 낱말(말버릇 후보). 정규화(소문자·NFKC) 뒤 꼴로 적는다.
FILLER_WORDS: tuple[str, ...] = (
    "진짜", "정말", "그냥", "근데", "그런데", "아니", "완전", "너무", "약간", "살짝", "그거", "이거", "저거",
    "그게", "이게", "저게", "레알", "대박", "와우", "헐", "아니근데", "솔직히", "일단", "약간좀", "좀",
    "오오", "우와", "이야", "그쵸", "그죠", "맞아", "맞아요", "인정",
)

# 어느 방송에나 흔해 주제 겹침의 근거가 못 되는 낱말(주제 겹침·내용 낱말 수에서 뺀다).
GENERIC_WORDS: tuple[str, ...] = (
    "오늘", "지금", "방송", "방금", "아까", "이번", "다음", "여기", "거기", "저기", "우리", "저희", "제가", "저도",
    "나도", "너무", "정도", "생각", "사람", "하나", "그럼", "그러면", "그래서", "그리고", "있어요", "없어요",
    "해요", "했어요", "하는", "있는", "같아요", "같은", "합니다", "입니다", "님들", "여러분",
    # 기능어·흔한 활용형(전사문 거의 모든 창에 나와 주제 겹침의 근거가 못 된다 — 검토 2026-10-23)
    "이렇게", "그렇게", "저렇게", "이런", "그런", "저런", "이제", "그래", "그래도", "그래요", "그니까", "그러니까",
    "근데요", "있잖아", "있잖아요", "하면", "하고", "해서", "하는데", "하면서", "했는데", "하지만", "되는", "되면",
    "되고", "돼요", "됐어요", "거예요", "거야", "거죠", "건데", "있고", "없고", "있어", "없어", "있다", "없다",
    "했다", "한다", "하다", "된다", "때문에",
)

# 반응 자모(웃음·울음·놀람). 이것만 있는 채팅은 이어 말할 거리가 적다.
REACTION_JAMO: str = "ㅋㅎㅠㅜㄷㅇㅊㄱ"

# 합성 예문 (시험·문서용, 실제 채팅이 아님). (글, question_form 기대값)
SYNTHETIC_QUESTION_EXAMPLES: tuple[tuple[str, str | None], ...] = (
    ("오늘 점심은 뭐 드셨어요?", "open"),
    ("그 보스는 어떻게 잡아요", "open"),
    ("왜 그 길로 가셨어요?", "open"),
    ("몇 판째예요", "open"),
    ("혹시 그 게임 해 보셨어요", "yes_no"),
    ("떡볶이 맵나요", "yes_no"),
    ("이번 판 이기면 끝이죠?", "yes_no"),
    ("왜냐하면 그게 더 빠르거든요", None),
    ("언제나 응원합니다", None),
    ("오늘 방송 재밌어요", None),
    ("ㅋㅋㅋㅋ {:zz_test_01:}", None),
    # 서술에 흔한 머리(OPEN_WEAK_HEADS)만 있고 묻는 표지가 없으면 서술(검토 2026-10-23)
    ("뭔가 이상해요", None),
    ("어떤 사람이 그랬어요", None),
    ("몇 번 해봤어요", None),
    ("누가 봐도 그렇죠", None),
    ("얼마나 좋은데요", None),
    ("무슨 일이 있어도 볼게요", None),
    ("몇 번 해봤어요?", "open"),
    ("어디예요", "open"),
    ("어떤 게임 할까", "open"),
)

_EMOTE = re.compile(r"\{:[^{}:]{1,40}:\}")
_TAIL_PUNCT = re.compile(r"[\s.!~,;:^…ㅋㅎㅠㅜ]+$")
_WORD = re.compile(r"[0-9a-z가-힣]+")


_COMPAT_JAMO_RUN = re.compile(r"[^ㄱ-ㆎ]+")


def nfkc(text: str) -> str:
    """NFKC 하되 호환 자모(ㅋ·ㅎ·ㅠ …, U+3131~318E)는 그대로 둔다.
    그냥 NFKC 하면 'ㅋ' 가 첫소리 자모(U+110F)로 바뀌어 'ㅋ' 와 비교할 수 없게 된다."""
    return _COMPAT_JAMO_RUN.sub(lambda m: unicodedata.normalize("NFKC", m.group()), text or "")


def _clean(text: str) -> str:
    """NFKC(자모는 그대로)·소문자, 이모티콘 코드를 공백으로."""
    return _EMOTE.sub(" ", nfkc(text)).lower()


def _words(text: str) -> list[str]:
    return _WORD.findall(_clean(text))


def starts_with_any(word: str, heads: tuple[str, ...]) -> bool:
    return any(word.startswith(h) for h in heads)


def open_word_counts(text: str) -> tuple[int, int]:
    """(강한 열린 질문 낱말 수, 서술에도 흔한 머리 OPEN_WEAK_HEADS 로 시작하는 낱말 수). 예외 꼴은 뺀다."""
    strong = weak = 0
    for w in _words(text):
        if starts_with_any(w, OPEN_WORD_EXCEPTIONS) or not starts_with_any(w, OPEN_QUESTION_WORDS):
            continue
        if starts_with_any(w, OPEN_WEAK_HEADS):
            weak += 1
        else:
            strong += 1
    return strong, weak


def open_word_count(text: str) -> int:
    """열린 질문 낱말로 시작하는 낱말 수(예외 꼴은 뺀다). 서술에도 흔한 머리도 센다(question_form 은 따로 본다)."""
    return sum(open_word_counts(text))


def _jong(ch: str) -> int:
    """한글 음절의 받침 번호(0 = 받침 없음). 음절이 아니면 -1."""
    o = ord(ch) - 0xAC00
    return o % 28 if 0 <= o < 11172 else -1


def _tail(text: str) -> str:
    """끝 판별용: 이모티콘·뒤 기호·웃음 자모를 떼고 공백을 없앤 꼴(물음표는 남긴다)."""
    s = _TAIL_PUNCT.sub("", _clean(text).rstrip())
    return re.sub(r"\s+", "", s)


def question_form(text: str) -> str | None:
    """묻는 말의 꼴: 'open'(열린 질문), 'yes_no'(예/아니오 질문), None(묻는 말이 아님).

    - 강한 열린 질문 낱말(뭐·뭘·어떻게·왜·누구 …, 예외 꼴 빼고)이 있으면 'open'(물음표가 없어도: '뭐 먹어요' 는 묻는 말).
    - 서술에도 흔한 머리(OPEN_WEAK_HEADS: 뭔·몇·어떤·누가 …)만 있으면 묻는 표지(아래 yes_no 의 표지 가운데 하나)나
      OPEN_COPULA_ENDINGS 끝이 더 있을 때만 'open'. 없으면 서술('뭔가 이상해요'·'몇 번 해봤어요' 는 None).
    - 아니면 물음표가 있거나, 끝이 QUESTION_ENDINGS 이거나, 받침 규칙, YES_NO_MARKERS 낱말이 있으면 'yes_no'.
    """
    strong, weak = open_word_counts(text)
    if strong > 0:
        return "open"
    tail = _tail(text)
    bare = tail.rstrip("?？")
    asked = ("?" in tail or "？" in (text or "")
             or any(bare.endswith(e) for e in QUESTION_ENDINGS)
             or (len(bare) >= 2 and ((bare[-1] == "니" and _jong(bare[-2]) == _JONG_SS)
                                     or (bare[-1] == "까" and _jong(bare[-2]) == _JONG_L)))
             or any(starts_with_any(w, YES_NO_MARKERS) for w in _words(text)))
    if weak > 0 and (asked or any(bare.endswith(e) for e in OPEN_COPULA_ENDINGS)):
        return "open"
    return "yes_no" if asked else None


def filler_hits(text: str) -> tuple[str, ...]:
    """글에 든 말버릇 후보 낱말(정렬, 중복 없음). 낱말 전체가 같을 때만 친다."""
    fillers = set(FILLER_WORDS)
    return tuple(sorted({w for w in _words(text) if w in fillers}))


def tail_marker(text: str) -> str | None:
    """끝 말버릇 표지: 마지막 이모티콘 코드('emote:<코드>'), 끝 웃음·울음 자모('jamo:ㅋ' 꼴), 끝 '~'·'!'. 없으면 None."""
    s = nfkc(text).rstrip()
    if not s:
        return None
    m = re.search(r"(\{:[^{}:]{1,40}:\})\s*$", s)
    if m:
        return "emote:" + m.group(1).lower()
    last = s[-1]
    if last in REACTION_JAMO:
        return "jamo:" + last
    if last in "~!":
        return "punct:" + last
    return None
