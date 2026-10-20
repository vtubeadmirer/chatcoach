"""채팅–발화 짝의 특징 계산 (엔진 시험판 B단계, 08 D-03 3번).

안내서 v0.1 4-1 의 증거 7종 가운데 글자로 잴 수 있는 것을 계산한다. 모두 순수 함수이고
파일·네트워크·시계를 쓰지 않는다. 닉네임 원문은 인자로만 받고 어디에도 저장하지 않는다.

여기 있는 기준(유사도 하한 등)은 모두 엔진 시작값이다. 안내서 숫자가 아니라서 guide-config 에 두지 않고
LinkConfig(engine/link.py)에 둔다. 측정 문서: docs/measurements/2026-10-20-link-v0.md
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Sequence

# ── 정규화 ────────────────────────────────────────────────────────────────

_EMOTE = re.compile(r"\{:[^{}:\s]{1,40}:\}")                 # 치지직 이모티콘 토큰 {:이름:}
_JAMO_RUN = re.compile(r"[ㄱ-ㆎᄀ-ᇿ]+")       # ㅋㅋ ㅎㅎ ㅠㅠ ㄱㄱ 같은 낱자 묶음
_NON_WORD = re.compile(r"[^0-9a-z가-힣\s?]+")         # 한글 음절·영숫자·공백·? 만 남긴다
_SPACES = re.compile(r"\s+")


def normalize(text: str, *, keep_question: bool = False, keep_space: bool = True) -> str:
    """비교용 정규화. 이모티콘 토큰·낱자(ㅋㅋ 등)·기호를 지우고 소문자로, 공백은 하나로.

    keep_question=True 면 '?' 를 남긴다(질문형 판별용). keep_space=False 면 공백도 지운다.
    """
    if not text:
        return ""
    s = _EMOTE.sub(" ", text)
    s = _JAMO_RUN.sub(" ", s)
    s = s.lower()
    s = _NON_WORD.sub(" ", s)
    if not keep_question:
        s = s.replace("?", " ")
    s = _SPACES.sub(" ", s).strip()
    if not keep_space:
        s = s.replace(" ", "")
    return s


def compact(text: str) -> str:
    """공백까지 지운 정규화 문자열."""
    return normalize(text, keep_space=False)


# ── 문자 n-gram · 유사도 ─────────────────────────────────────────────────

def char_ngrams(text: str, n: int = 2) -> Counter:
    """공백을 지운 정규화 문자열의 문자 n-gram 개수. 길이가 n 보다 짧으면 문자열 하나를 그대로 넣는다."""
    s = compact(text)
    if not s:
        return Counter()
    if len(s) < n:
        return Counter([s])
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def dice(a: Counter, b: Counter) -> float:
    """두 n-gram 묶음의 Dice 계수(0~1, 중복 개수 반영)."""
    ta, tb = sum(a.values()), sum(b.values())
    if ta == 0 or tb == 0:
        return 0.0
    inter = sum((a & b).values())
    return 2.0 * inter / (ta + tb)


def containment(part: Counter, whole: Counter) -> float:
    """part 의 n-gram 가운데 whole 에 들어 있는 비율(0~1). 채팅 문구를 '거의 그대로 읽었는지' 에 쓴다."""
    tp = sum(part.values())
    if tp == 0:
        return 0.0
    return sum((part & whole).values()) / tp


def similarity(a: str, b: str, n: int = 2) -> float:
    """두 문장의 문자 bigram Dice 유사도."""
    return dice(char_ngrams(a, n), char_ngrams(b, n))


# ── 낱말(내용어) ──────────────────────────────────────────────────────────

# 끝에서 떼어 낼 조사·어미(긴 것부터). 형태소 분석기 없이 쓰는 거친 근사다.
_PARTICLES = sorted([
    "에서는", "으로는", "이에요", "인가요", "인데요", "이라고", "라고요", "했어요", "해요", "에서", "으로", "한테",
    "까지", "부터", "처럼", "보다", "이랑", "하고", "이요", "인데", "는데", "에요", "예요", "이야", "이다",
    "은", "는", "이", "가", "을", "를", "에", "도", "로", "랑", "요", "의", "만", "와", "과", "야", "죠",
], key=len, reverse=True)

STOPWORDS = frozenset({
    "그냥", "진짜", "정말", "너무", "완전", "근데", "그럼", "그래서", "그리고", "이거", "그거", "저거", "여기", "거기",
    "저기", "지금", "오늘", "우리", "저희", "저는", "제가", "나는", "내가", "이제", "약간", "좀", "많이", "다들",
    "혹시", "아니", "네네", "이런", "그런", "저런", "어떤", "무슨", "뭐야", "뭔가", "하는", "있는", "없는", "같은",
    "같아", "같아요", "있어", "있어요", "없어", "없어요", "해서", "하면", "했는데", "안녕", "안녕하세요", "감사합니다",
    "언니", "형", "누나", "오빠", "님", "ㅋㅋ", "방송", "시청자",
    # 내용어가 아닌 흔한 동사·연결형(조사 떼기로 걸러지지 않는 것)
    "가면", "하면", "되면", "보면", "나면", "있으면", "없으면", "해도", "하고", "있냐", "있나", "하냐", "가서", "해서",
    "있다", "없다", "한다", "했다", "된다", "그게", "이게", "저게", "뭔데", "어때",
})


def _strip_particle(w: str) -> str:
    for p in _PARTICLES:
        if len(w) > len(p) + 1 and w.endswith(p):
            return w[: -len(p)]
    return w


def content_tokens(text: str, min_len: int = 2) -> set[str]:
    """내용어 후보. 정규화 → 공백으로 자름 → 불용어·짧은 낱말 제거.

    조사 떼기는 '제주도 → 제주' 처럼 틀릴 수 있어서, 뗀 형태와 원래 형태를 둘 다 넣는다.
    """
    out = set()
    for w in normalize(text).split():
        if w in STOPWORDS:
            continue
        for x in {w, _strip_particle(w)}:
            if len(x) >= min_len and x not in STOPWORDS:
                out.add(x)
    return out


# ── 질문형 ────────────────────────────────────────────────────────────────

_Q_WORDS = ("뭐", "뭘", "뭔", "어디", "언제", "누구", "누가", "왜", "어떻게", "어때", "얼마", "몇", "무슨", "어떤", "어느")
# 어미만으로 질문을 판정할 때 쓰는 의문 전용 어미(~어요·~죠 같은 어미는 '?' 가 있을 때만 질문으로 본다).
_Q_ONLY_ENDINGS = ("나요", "까요", "가요", "는지", "냐", "니", "던가", "래요")


def is_question(text: str) -> bool:
    """질문형인지. '?' 가 있거나, 의문사(뭐·어디·언제 …)가 들어 있거나, 의문 전용 어미(~나요·~까요·~냐 …)로 끝난다."""
    s = normalize(text, keep_question=True)
    if not s:
        return False
    if "?" in s:
        return True
    words = s.split()
    for w in words:
        for q in _Q_WORDS:
            if w.startswith(q):
                return True
    last = words[-1]
    return any(last.endswith(e) and len(last) > len(e) for e in _Q_ONLY_ENDINGS)


# ── 직접 지칭 ─────────────────────────────────────────────────────────────

DIRECT_REFERENCE_TERMS = ("방금", "질문", "물어보", "물어봤", "채팅", "아까", "님이", "말씀하신", "올라온")


def direct_reference_terms(utterance: str) -> list[str]:
    """발화에 들어 있는 직접 지칭 표현(사전 순서). 없으면 빈 목록."""
    s = compact(utterance)
    return [t for t in DIRECT_REFERENCE_TERMS if t in s]


# ── 답의 형태 ─────────────────────────────────────────────────────────────

_ANSWER_MARKERS = ("네", "예", "아니", "아뇨", "응", "저는", "제가", "맞아", "글쎄", "그건", "그거는", "당연히")


def answer_form(chat: str, utterance: str) -> bool:
    """채팅이 질문이고, 발화가 그 답처럼 보이는가.

    시간 근접만으로는 참이 되지 않는다: 발화가 답 표지(네·아니·저는 …)로 시작하거나 채팅과 내용어를 하나 이상 공유해야 한다.
    발화 자체가 질문이면(되묻기) 참으로 보지 않는다.
    """
    if is_low_content(chat) or not is_question(chat):
        return False                      # '?' 하나 같은 채팅은 무엇을 묻는지 알 수 없다
    u = normalize(utterance)
    if not u or is_question(utterance):
        return False
    first = u.split()[0]
    if any(first.startswith(m) for m in _ANSWER_MARKERS):
        return True
    return bool(shared_content(chat, utterance))


def shared_content(chat: str, utterance: str) -> set[str]:
    """채팅의 내용어 가운데 발화(공백 무시)에 그대로 들어 있는 것."""
    u = compact(utterance)
    return {t for t in content_tokens(chat) if t in u}


# ── 문구 되풀이 · 고유 내용 · 같은 주제 ───────────────────────────────────

def longest_common_substring(a: str, b: str) -> int:
    """두 문자열의 가장 긴 공통 연속 부분 문자열 길이."""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def text_repeated(chat: str, utterance: str, *, min_chars: int = 4, min_containment: float = 0.6,
                  min_run_ratio: float = 0.6, max_chars: int = 200) -> bool:
    """채팅 문구를 읽거나 거의 그대로 되풀이했는가.

    두 조건을 모두 본다: 채팅 bigram 의 min_containment 이상이 발화에 있고, 채팅 길이의 min_run_ratio 이상이
    발화에 한 덩어리로 이어져 나온다. 답이 질문의 낱말을 흩어서 되받는 경우('라면 뭐 좋아해요?' → '… 라면 좋아해요')를
    되풀이로 보지 않으려는 것이다. 정규화 뒤 min_chars 글자보다 짧은 채팅과 내용어가 없는 채팅(인사만 등)은
    판정하지 않는다(False).
    """
    c = compact(chat)[:max_chars]
    if len(c) < min_chars or not content_tokens(chat):
        return False                      # 인사·감탄만 있는 채팅은 되풀이해도 그 채팅을 읽었다고 보기 어렵다
    u = compact(utterance)
    if containment(char_ngrams(c), char_ngrams(u)) < min_containment:
        return False
    return longest_common_substring(c, u) >= min_run_ratio * len(c)


def unique_content(chat: str, utterance: str, prior_utterances: Iterable[str] = ()) -> set[str]:
    """채팅에만 있던 구체적 내용이 발화에 등장했는가 — 그런 낱말 집합(비면 증거 없음).

    채팅 내용어 가운데 발화에 나온 것에서, 그 전 시간대 스트리머 발화(prior_utterances)에 이미 있던 것을 뺀다.
    """
    hit = shared_content(chat, utterance)
    if not hit:
        return set()
    prior = "".join(compact(p) for p in prior_utterances)
    return {t for t in hit if t not in prior}


def same_topic(chat: str, utterance: str, *, min_dice: float = 0.2) -> bool:
    """같은 주제(약한 증거). 내용어를 공유하거나 bigram 유사도가 min_dice 이상."""
    if shared_content(chat, utterance):
        return True
    return similarity(chat, utterance) >= min_dice


# ── 반복·도배 ─────────────────────────────────────────────────────────────

def is_low_content(text: str, min_chars: int = 2) -> bool:
    """정규화하면 거의 남지 않는 채팅(ㅋㅋㅋ·이모티콘만·'?' 만 등)."""
    return len(compact(text)) < min_chars


def repeat_ratio(text: str) -> float:
    """가장 많이 나온 글자가 차지하는 비율(공백 무시). '와와와와와' → 1.0. 빈 문자열은 0."""
    s = compact(text)
    if not s:
        return 0.0
    return Counter(s).most_common(1)[0][1] / len(s)


def similar_indices(target: str, others: Sequence[str], min_similarity: float = 0.5) -> list[int]:
    """others 가운데 target 과 bigram 유사도가 min_similarity 이상인 것의 위치."""
    tg = char_ngrams(target)
    if not tg:
        return []
    return [i for i, o in enumerate(others) if dice(tg, char_ngrams(o)) >= min_similarity]


# ── 닉네임 호명 (자모 편집 거리) ─────────────────────────────────────────

_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = ("", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ",
         "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ")


def jamo(text: str) -> str:
    """한글 음절을 초·중·종성 낱자로 푼다. 다른 글자는 그대로 둔다."""
    out = []
    for ch in text:
        o = ord(ch) - 0xAC00
        if 0 <= o < 11172:
            out.append(_CHO[o // 588])
            out.append(_JUNG[(o % 588) // 28])
            j = _JONG[o % 28]
            if j:
                out.append(j)
        else:
            out.append(ch)
    return "".join(out)


def edit_distance(a: str, b: str) -> int:
    """레벤슈타인 거리(삽입·삭제·치환 1)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def jamo_distance_ratio(a: str, b: str) -> float:
    """자모로 푼 두 문자열의 편집 거리 / 더 긴 쪽 길이(0~1)."""
    ja, jb = jamo(a), jamo(b)
    m = max(len(ja), len(jb))
    return edit_distance(ja, jb) / m if m else 0.0


def nickname_called(nicknames: Sequence[str] | None, utterance: str, *,
                    max_ratio: float = 0.3, min_nick_chars: int = 2) -> bool | None:
    """발화에서 닉네임을 '…님' 으로 불렀는가.

    - nicknames 가 None 이면(가림 전 닉네임 목록이 없음) None = 판단 불가.
    - 발화의 '님' 앞 글자(공백 무시)를 닉네임 길이 −1 ~ +1 글자만큼 떼어 자모 편집 거리 비율이 max_ratio 이하이면 참.
      ASR 이 닉네임을 비슷한 소리로 적는 경우를 받으려는 것이다.
    - 정규화하면 min_nick_chars 글자보다 짧은 닉네임은 판정하지 않는다(거짓 양성이 너무 많다).
    """
    if nicknames is None:
        return None
    u = compact(utterance)
    if "님" not in u:
        return False
    nicks = [compact(n) for n in nicknames]
    nicks = [n for n in nicks if len(n) >= min_nick_chars]
    if not nicks:
        return False
    idxs = [i for i, ch in enumerate(u) if ch == "님"]
    for n in nicks:
        ln = len(n)
        for i in idxs:
            for k in range(max(min_nick_chars, ln - 1), ln + 2):
                if k > i:
                    break
                cand = u[i - k:i]
                if jamo_distance_ratio(n, cand) <= max_ratio:
                    return True
    return False
