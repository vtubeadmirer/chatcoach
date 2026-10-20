"""link v0.2 규칙 수정(2026-10-21)의 회귀 테스트. 합성 데이터만 쓴다.

1. 문구 되풀이: 이어지는 STT 조각을 합쳐 보고, 긴 채팅의 앞부분만 읽어도 잡는다. 인사말만 겹치면 잡지 않는다.
2. 강한 증거 우선: 비슷한 채팅이 있어도 되풀이가 이 채팅에만 맞으면 직접 반응. 같은 문구가 둘 다 맞으면 불명확.
3. '이미 그 주제': 채팅 내용어의 약 3분의 1 이상을 앞 발화가 이미 말했을 때만 켠다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import features as F  # noqa: E402
from engine import link as L  # noqa: E402


def vid(n):
    return "vw_" + "abcdefghijkl"[:11] + "abcdefghijklmnopqrstuvwxyz234567"[n % 32]


def chat(n, t, text):
    return L.Chat("c" + f"{n:020d}"[-20:].replace("0", "a"), t, vid(n), text)


# ── 1. 문구 되풀이 ────────────────────────────────────────────────────────

def test_partial_read_of_long_chat_counts():
    c = "오늘 저녁에 떡볶이 먹으러 가자고 했는데 친구가 갑자기 약속을 취소했어"
    assert F.text_repeated(c, "오늘 저녁에 떡볶이 먹으러 가자고")           # 앞부분 한 덩어리
    assert not F.text_repeated(c, "오늘 저녁에 떡볶이 먹으러 가자고", abs_run_chars=None, partial_run_chars=None)


def test_short_chat_mostly_read_counts():
    assert F.text_repeated("그래 그래 다 괜찮아질거야", "그래 다 괜찮아질거야")


def test_greeting_overlap_is_not_repeat():
    assert not F.text_repeated("감사합니다 오늘도", "아까 누구님이 감사합니다 하셨는데")


def test_scattered_words_are_not_repeat():
    assert not F.text_repeated("라면 뭐 좋아해요?", "라면 저는 짜장 라면 좋아해요")


def test_merged_segments_catch_split_read():
    cfg = L.LinkConfig()
    ch = chat(1, 10_000, "고양이가 키보드 위로 올라가서 자는거 아니였음?")
    u = L.Utterance(0, 14_000, 15_500, "고양이가 키")
    ev = L.evidence_for(ch, u, 10_000, nicknames=[], prior_texts=[], cfg=cfg, utt_vod_ms=14_000,
                        merged_text="고양이가 키보드 위로 올라가서 자는 거")
    assert ev["text_repeated"]["present"] is True
    ev1 = L.evidence_for(ch, u, 10_000, nicknames=[], prior_texts=[], cfg=cfg, utt_vod_ms=14_000)
    assert ev1["text_repeated"]["present"] is False


# ── 2. 강한 증거 우선 ─────────────────────────────────────────────────────

def test_pick_grade_strong_unique_resolves_similar():
    from app.core import rules

    def ev(*keys):
        return {"evidence": {k: (k in keys) for k in rules.EVIDENCE_KEYS}}
    gc = rules.grade_conditions([ev("text_repeated", "within_time_window", "same_topic")],
                                no_similar_chat_nearby=False, streamer_already_on_topic=False)
    assert L._pick_grade(gc) == "unclear"                       # 예전 동작(예시 4 를 지키는 기본)
    assert L._pick_grade(gc, strong_unique=True) == "direct"    # 되풀이가 이 채팅에만 맞을 때


# ── 3. 이미 그 주제 ───────────────────────────────────────────────────────

def test_on_topic_ratio_default():
    assert L.LinkConfig().on_topic_min_ratio == 0.33
