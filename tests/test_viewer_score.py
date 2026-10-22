"""시청자 훈련(G) 채점 첫 판 시험 (08 D-20 2·3, 21 6-2·6-4 의 2번).

예문은 모두 합성이다(실제 채팅·닉네임·전사문이 아니다). 이모티콘 코드도 합성(`{:zz_test_NN:}`).
축마다 3단계가 손 기대값과 같은지, 같은 입력이면 같은 결과인지, 결과에 쓴 글이 들어가지 않는지 본다.
"""
from __future__ import annotations

import json
import os
import string
import subprocess
import sys

import pytest

from app.core import ko_cues
from app.core import viewer_score as vs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

T = 600_000   # 보낸 VOD 시각(10분)


def ctx(topic=(), talk=(), chats=(), **kw):
    return vs.ScoreContext.build(topic_blocks=topic, transcript=talk, chats=chats, **kw)


# 합성 맥락: 주제 흐름 '떡볶이·분식', 방금 전사 '매운 치즈 라면'
CTX = ctx(topic=[(T - 300_000, T + 60_000, ("떡볶이", "분식"))],
          talk=[(T - 30_000, T - 25_000, ["이번엔 매운 치즈 라면을 끓여 볼게요"])])


def score(text, history=(), c=CTX, t=T, **kw):
    return vs.score_chat(text, t, c, history, **kw)


# ───────────────────────────────────────────────────────────── ko_cues

@pytest.mark.parametrize("text,want", ko_cues.SYNTHETIC_QUESTION_EXAMPLES)
def test_question_form_synthetic(text, want):
    assert ko_cues.question_form(text) == want


@pytest.mark.parametrize("text", ["우리 어머니", "그러니까 그렇죠 네", "아까 그 장면 좋았어요", "언제든 와요", "어쨌든 이겼다",
                                  "그러니까", "방금 아까"])
def test_question_form_not_question(text):
    assert ko_cues.question_form(text) is None


@pytest.mark.parametrize("text", ["이거 맞나요", "같이 할까", "그 판 이겼니", "정말 그렇습니까", "내일도 하냐",
                                  "저녁은 먹을까", "어제 거기 갔니"])
def test_question_form_endings_without_mark(text):
    assert ko_cues.question_form(text) == "yes_no"


def test_question_form_fullwidth_and_trailing_laugh():
    assert ko_cues.question_form("맛있어요？") == "yes_no"
    assert ko_cues.question_form("맛있어요? ㅋㅋㅋ {:zz_test_02:}") == "yes_no"
    assert ko_cues.question_form("뭐 먹어요 ㅋㅋ") == "open"


def test_filler_and_tail_marker():
    assert ko_cues.filler_hits("진짜 너무 맛있겠다 진짜") == ("너무", "진짜")
    assert ko_cues.tail_marker("좋다 ㅋㅋㅋ") == "jamo:ㅋ"
    assert ko_cues.tail_marker("좋다 {:zz_test_03:}") == "emote:{:zz_test_03:}"
    assert ko_cues.tail_marker("좋다~") == "punct:~"
    assert ko_cues.tail_marker("좋다") is None
    assert ko_cues.tail_marker("") is None


def test_cue_lists_are_clean():
    for name in ("QUESTION_ENDINGS", "OPEN_QUESTION_WORDS", "OPEN_WORD_EXCEPTIONS", "YES_NO_MARKERS",
                 "FILLER_WORDS", "GENERIC_WORDS"):
        lst = getattr(ko_cues, name)
        assert lst and all(isinstance(x, str) and x for x in lst), name
        assert len(set(lst)) == len(lst), f"{name} 에 겹친 값"
    # 예외 꼴은 열린 질문 낱말로 시작해야 뜻이 있다
    for e in ko_cues.OPEN_WORD_EXCEPTIONS:
        assert ko_cues.starts_with_any(e, ko_cues.OPEN_QUESTION_WORDS), e


# ───────────────────────────────────────────────────────────── 주제 적합성

def test_topic_flow_hit_good():
    r = score("떡볶이 국물에 밥 비벼 드세요").topic
    assert (r.level, r.code) == (vs.GOOD, vs.TOPIC_FLOW_HIT)
    assert r.num("flow") == 1


def test_topic_particle_counts_once():
    # '떡볶이를' 은 '떡볶이를'·'떡볶이' 두 꼴을 내지만 한 낱말
    r = score("떡볶이를 좋아해요").topic
    assert r.num("flow") == 1 and r.num("words") == 2   # 떡볶이를, 좋아해요


def test_topic_talk_two_good_one_ok():
    r = score("매운 치즈 좋아요").topic
    assert (r.level, r.code, r.num("talk"), r.num("flow")) == (vs.GOOD, vs.TOPIC_TALK_HITS, 2, 0)
    r = score("매운 거 잘 드시네요").topic
    assert (r.level, r.code, r.num("talk")) == (vs.OK, vs.TOPIC_TALK_ONE, 1)


def test_topic_inflection_match():
    c = ctx(talk=[(T - 10_000, T - 5_000, ["국물이 진짜 맛있다"])])
    r = score("국물 맛있어요", c=c).topic
    assert r.num("talk") == 2 and r.level == vs.GOOD


def test_topic_none_weak():
    r = score("축구 경기 보셨어요").topic
    assert (r.level, r.code) == (vs.WEAK, vs.TOPIC_NO_OVERLAP)


def test_topic_generic_and_filler_words_do_not_count():
    c = ctx(talk=[(T - 10_000, T - 5_000, ["오늘 방송 진짜 길다"])])
    r = score("오늘 방송 진짜", c=c).topic
    assert r.code == vs.TOPIC_NO_WORDS and r.level == vs.WEAK
    r = score("오늘 방송 축구", c=c).topic
    assert r.code == vs.TOPIC_NO_OVERLAP and r.num("talk") == 0


def test_topic_no_words_and_no_context():
    assert score("ㅋㅋㅋㅋ {:zz_test_01:}").topic.code == vs.TOPIC_NO_WORDS
    r = score("떡볶이 맛있겠다", c=vs.EMPTY_CONTEXT).topic
    assert (r.level, r.code) == (vs.OK, vs.TOPIC_NO_CONTEXT)
    r = vs.score_chat("떡볶이 맛있겠다", T).topic          # ctx 없음
    assert r.code == vs.TOPIC_NO_CONTEXT


def test_topic_transcript_window_bounds():
    s = vs.DEFAULT_SETTINGS
    inside = ctx(talk=[(T - s.transcript_lookback_ms, T - s.transcript_lookback_ms, ["고양이 간식"])])
    outside = ctx(talk=[(T - s.transcript_lookback_ms - 1, T - s.transcript_lookback_ms - 1, ["고양이 간식"])])
    future = ctx(talk=[(T + 1, T + 5_000, ["고양이 간식"])])
    assert score("고양이 간식 뭐 줘요", c=inside).topic.num("talk") == 2
    assert score("고양이 간식 뭐 줘요", c=outside).topic.code == vs.TOPIC_NO_CONTEXT
    assert score("고양이 간식 뭐 줘요", c=future).topic.code == vs.TOPIC_NO_CONTEXT
    # 보낸 시각을 걸친 줄(시작은 앞, 끝은 뒤)은 넣는다
    spanning = ctx(talk=[(T - 2_000, T + 3_000, ["고양이 간식"])])
    assert score("고양이 간식 뭐 줘요", c=spanning).topic.num("talk") == 2


def test_topic_flow_window_bounds():
    s = vs.DEFAULT_SETTINGS
    old = ctx(topic=[(0, T - s.topic_lookback_ms - 1, ("떡볶이",))])
    edge = ctx(topic=[(0, T - s.topic_lookback_ms, ("떡볶이",))])
    assert score("떡볶이 좋아요", c=old).topic.code == vs.TOPIC_NO_CONTEXT
    assert score("떡볶이 좋아요", c=edge).topic.code == vs.TOPIC_FLOW_HIT


def test_topic_transcript_lookback_setting():
    c = ctx(talk=[(T - 100_000, T - 100_000, ["고양이 간식"])])
    assert score("고양이 간식", c=c).topic.code == vs.TOPIC_NO_CONTEXT          # 기본 90초
    s, notes = vs.settings_from_values({"viewer_score": {"transcript_lookback_sec": 120}})
    assert not notes
    r = score("고양이 간식", c=c, settings=s).topic
    assert r.num("talk") == 2 and r.num("window_sec") == 120


def test_context_accepts_dict_rows():
    c = ctx(topic=[{"start_ms": T - 50_000, "end_ms": T, "keywords": ["분식"]}],
            talk=[{"vod_ms": T - 3_000, "words": ["어묵", "국물"]}, {"start_ms": T - 9_000, "end_ms": T - 8_000,
                                                                     "text": "김밥 한 줄"}, {"bad": 1}, None, (1,)],
            chats=[{"show_vod_ms": T - 1_000, "text": "어묵 국물 최고"}, {"vod_ms": "x", "text": "무시"}])
    r = score("분식 어묵 김밥", c=c)      # 2자 낱말은 정확히 같을 때만 겹친다(0.2 — '분식집' 은 '분식' 과 겹치지 않음)
    assert r.topic.num("flow") == 1 and r.topic.num("talk") == 2
    assert score("어묵 국물 최고", c=c).repeat.code == vs.REPEAT_COPY_SAME


# ───────────────────────────────────────────────────────────── 대화 확장성

@pytest.mark.parametrize("text,level,code", [
    ("ㅋㅋㅋㅋㅋ", vs.WEAK, vs.EXPAND_REACTION_ONLY),
    ("{:zz_test_01:}{:zz_test_02:}", vs.WEAK, vs.EXPAND_REACTION_ONLY),
    ("ㅠㅠ !!", vs.WEAK, vs.EXPAND_REACTION_ONLY),
    ("굿", vs.WEAK, vs.EXPAND_TOO_SHORT),
    ("왜요?", vs.OK, vs.EXPAND_TOO_SHORT),
    ("라면에 계란 넣으면 어떤 맛이에요?", vs.GOOD, vs.EXPAND_OPEN_QUESTION),
    ("치즈는 몇 장 넣어요?", vs.GOOD, vs.EXPAND_OPEN_QUESTION),
    # '몇' 은 서술에도 흔한 머리라 묻는 표지가 없으면 서술로 본다(0.2, '몇 번 해봤어요' 와 같은 꼴)
    ("치즈는 몇 장 넣어요", vs.OK, vs.EXPAND_CONCRETE),
    ("혹시 매운 라면 잘 드세요", vs.GOOD, vs.EXPAND_YN_CONCRETE),
    ("그거 맞나요", vs.OK, vs.EXPAND_YN_QUESTION),
    ("저도 어제 치즈 라면 끓여 먹었어요", vs.OK, vs.EXPAND_CONCRETE),
    ("와 대박이다 ㅋㅋ", vs.WEAK, vs.EXPAND_THIN),
])
def test_expand_levels(text, level, code):
    r = score(text).expand
    assert (r.level, r.code) == (level, code), (text, r)


def test_expand_too_long_caps_good():
    long_q = "라면에 " + "치즈랑 계란이랑 파랑 " * 6 + "어떻게 넣어요?"
    assert vs.visible_chars(long_q) > vs.DEFAULT_EXPAND_MAX_CHARS
    r = score(long_q).expand
    assert (r.level, r.code) == (vs.OK, vs.EXPAND_TOO_LONG)
    assert r.num("chars") == vs.visible_chars(long_q) and r.num("question") == 2


def test_expand_question_num_codes():
    assert score("뭐 넣어요?").expand.num("question") == 2
    assert score("맛있나요").expand.num("question") == 1
    assert score("맛있겠다").expand.num("question") == 0


def test_visible_chars():
    assert vs.visible_chars("가 나 {:zz_test_01:} 다!") == 4
    assert vs.visible_chars("") == 0


# ───────────────────────────────────────────────────────────── 반복 여부

def test_repeat_fresh_good():
    r = score("라면에 파 넣어요?", history=["떡볶이 좋아해요"]).repeat
    assert (r.level, r.code) == (vs.GOOD, vs.REPEAT_FRESH)


def test_repeat_self_same_after_normalize():
    r = score("떡볶이 최고!!  ㅋㅋㅋㅋ", history=["떡볶이최고 ㅋㅋ", "다른 말"]).repeat
    assert (r.level, r.code) == (vs.WEAK, vs.REPEAT_SELF_SAME)
    # 이모티콘만 되풀이해도 같은 말
    r = score("{:zz_test_05:}", history=["{:ZZ_TEST_05:}"]).repeat
    assert r.code == vs.REPEAT_SELF_SAME


def test_contain_pct_hand_values():
    g = vs.grams("가나다라마바사")          # 3개: 가나다라마, 나다라마바, 다라마바사
    assert len(g) == 3
    assert vs.contain_pct(g, vs.grams("가나다라마바")) == 67    # 2/3
    assert vs.contain_pct(g, vs.grams("다라마바사아자")) == 33  # 1/3
    assert vs.contain_pct(g, g) == 100
    assert vs.contain_pct(frozenset(), g) == 0
    assert vs.grams("가나다라") == frozenset()                 # 5자 못 됨 → 같음 비교만


def test_repeat_self_similar_weak_and_ok():
    base = "치즈라면에계란두개넣었어요"                          # 13자, 5-gram 9개
    weak = score(base + "진짜로", history=[base]).repeat        # 16자 12개 중 9개 = 75%
    assert (weak.level, weak.code, weak.num("self_pct")) == (vs.WEAK, vs.REPEAT_SELF_SIMILAR, 75)
    low = score("치즈라면에계란 하나만 넣을래요", history=[base]).repeat   # 치즈라면에계란하나만넣을래요: 10개 중 3개
    assert (low.level, low.code, low.num("self_pct")) == (vs.GOOD, vs.REPEAT_FRESH, 30)
    mid = score("치즈라면에계란두개 말고 하나만", history=[base]).repeat   # 치즈라면에계란두개말고하나만: 10개 중 5개
    assert mid.num("self_pct") == 50 and (mid.level, mid.code) == (vs.OK, vs.REPEAT_SELF_SIMILAR)


def test_repeat_copy_same_and_window():
    s = vs.DEFAULT_SETTINGS
    c = ctx(chats=[(T - s.copy_window_ms, "라면 국물 미쳤다")])
    assert score("라면 국물 미쳤다", c=c).repeat.code == vs.REPEAT_COPY_SAME
    c_old = ctx(chats=[(T - s.copy_window_ms - 1, "라면 국물 미쳤다")])
    assert score("라면 국물 미쳤다", c=c_old).repeat.code == vs.REPEAT_FRESH
    c_future = ctx(chats=[(T + 1, "라면 국물 미쳤다")])
    assert score("라면 국물 미쳤다", c=c_future).repeat.code == vs.REPEAT_FRESH
    r = score("라면 국물 미쳤다", c=ctx(chats=[(T - 1_000, "라면국물미쳤다"), (T - 500, "라면 국물 미쳤다!")])).repeat
    assert r.num("copies") == 2 and r.level == vs.WEAK


def test_repeat_copy_similar():
    other = "치즈떡볶이진짜맛있겠다"                           # 11자
    c = ctx(chats=[(T - 5_000, other)])
    weak = score(other + "요", c=c).repeat                      # 12자 8개 중 7개 = 88%
    assert (weak.level, weak.code, weak.num("copy_pct")) == (vs.WEAK, vs.REPEAT_COPY_SIMILAR, 88)
    ok = score("치즈떡볶이진짜 싫어요 저는", c=c).repeat           # 치즈떡볶이진짜싫어요저는: 8개 중 3개 = 38%
    assert ok.num("copy_pct") == 38 and ok.level == vs.GOOD
    ok2 = score("치즈떡볶이진짜맛 없겠다", c=c).repeat            # 치즈떡볶이진짜맛없겠다: 7개 중 4개 = 57%
    assert (ok2.level, ok2.code, ok2.num("copy_pct")) == (vs.OK, vs.REPEAT_COPY_SIMILAR, 57)


def test_repeat_copy_window_setting():
    c = ctx(chats=[(T - 55_000, "라면 국물 미쳤다")])
    assert score("라면 국물 미쳤다", c=c).repeat.code == vs.REPEAT_FRESH     # 기본 45초
    s, _ = vs.settings_from_values({"copy_window_sec": 60})
    r = score("라면 국물 미쳤다", c=c, settings=s).repeat
    assert r.code == vs.REPEAT_COPY_SAME and r.num("window_sec") == 60


def test_repeat_habit_ok_and_weak():
    h2 = ["진짜 맛있겠다", "진짜 대단해요"]
    r = score("진짜 신기하다", history=h2).repeat
    assert (r.level, r.code, r.num("habit")) == (vs.OK, vs.REPEAT_HABIT, 2)
    r = score("진짜 신기하다", history=h2 + ["진짜 웃기네"]).repeat
    assert (r.level, r.code, r.num("habit")) == (vs.WEAK, vs.REPEAT_HABIT, 3)
    # 앞선 5개만 본다
    r = score("진짜 신기하다", history=["진짜 a1", "진짜 a2", "진짜 a3", "b1", "b2", "b3", "b4", "b5"]).repeat
    assert r.num("habit") == 0 and r.level == vs.GOOD


def test_repeat_habit_tail_marker():
    r = score("보스 멋지다 ㅋㅋ", history=["점프 좋다 ㅋㅋㅋ", "아이템 굿 ㅋ"]).repeat
    assert (r.level, r.code, r.num("habit")) == (vs.OK, vs.REPEAT_HABIT, 2)
    r = score("보스 멋지다 {:zz_test_07:}", history=["점프 좋다 {:zz_test_07:}", "굿 {:zz_test_08:}"]).repeat
    assert r.num("habit") == 1 and r.level == vs.GOOD


def test_repeat_topic_word_first_is_not_habit():
    # 주제 낱말로 시작하는 것은 말버릇이 아니다(방송이 그 주제를 이어 가면 자연스럽다)
    hist = ["떡볶이 맵나요", "떡볶이 몇 인분이에요"]
    assert score("떡볶이 어디서 사요", history=hist).repeat.num("habit") == 0
    assert score("떡볶이 어디서 사요", history=hist, c=vs.EMPTY_CONTEXT).repeat.num("habit") == 2


def test_repeat_precedence_self_same_over_copy():
    c = ctx(chats=[(T - 1_000, "라면 국물 미쳤다")])
    r = score("라면 국물 미쳤다", history=["라면 국물 미쳤다"], c=c).repeat
    assert r.code == vs.REPEAT_SELF_SAME and r.num("copies") == 1


# ───────────────────────────────────────────────────────────── 결과 꼴·결정성·개인정보

def test_result_shape_no_total_score():
    r = score("라면에 계란 넣으면 어떤 맛이에요?")
    d = r.as_dict()
    assert set(d) == set(vs.AXES)
    for axis in vs.AXES:
        a = d[axis]
        assert set(a) == {"level", "code", "nums"}
        assert a["level"] in vs.LEVELS and a["code"] in vs.REASON_CODES[axis]
        assert all(isinstance(v, int) for v in a["nums"].values())
    for name in ("total", "score", "sum", "points"):
        assert not hasattr(r, name)


def test_result_has_no_text():
    text = "고양이 간식 츄르는 몇 개 줘요?"
    hist = ["고양이 간식 맛있겠다"]
    c = ctx(talk=[(T - 3_000, T - 1_000, ["고양이 간식 츄르"])], chats=[(T - 2_000, "고양이 너무 귀엽다")])
    r = score(text, history=hist, c=c)
    blob = json.dumps(r.as_dict(), ensure_ascii=False) + repr(r)
    for w in ("고양이", "간식", "츄르", "귀엽"):
        assert w not in blob
    assert "고양이" not in json.dumps(vs.summarize([r]), ensure_ascii=False)


def test_same_input_same_result():
    args = ("라면에 치즈 넣으면 어떤 맛이에요? ㅋㅋ", ["치즈 좋아요", "진짜 맛있겠다"])
    assert score(*args) == score(*args)
    c2 = ctx(topic=[(T - 300_000, T + 60_000, ("분식", "떡볶이"))],
             talk=[(T - 30_000, T - 25_000, ["이번엔 매운 치즈 라면을 끓여 볼게요"])])
    assert score(*args) == score(*args, c=c2)     # 입력 순서가 달라도 같다


_HASHSEED_SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from app.core import viewer_score as vs
T = 600000
c = vs.ScoreContext.build(topic_blocks=[(0, T, ("떡볶이", "분식", "어묵"))],
                          transcript=[(T - 9000, T - 1000, ["매운 치즈 라면 국물 계란"])],
                          chats=[(T - 2000, "치즈 라면 국물 최고"), (T - 1000, "어묵 국물 최고")])
sc = vs.ViewerScorer(c)
out = [sc.score(t, T).as_dict() for t in ["떡볶이랑 어묵 중 뭐가 좋아요?", "치즈 라면 국물 최고요", "진짜 맛있다 ㅋㅋ",
                                          "진짜 매운 거 잘 드세요", "진짜 계란 넣어요 ㅋㅋ"]]
out.append(sc.summary())
print(json.dumps(out, ensure_ascii=True, sort_keys=True))
"""


def test_deterministic_across_hash_seeds():
    outs = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        p = subprocess.run([sys.executable, "-c", _HASHSEED_SCRIPT, ROOT], capture_output=True, env=env, timeout=60)
        assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-500:]
        outs.add(p.stdout.strip())
    assert len(outs) == 1


def test_bad_sent_ms():
    for bad in (None, "1", 1.5, True):
        with pytest.raises(ValueError):
            vs.score_chat("떡볶이", bad)


def test_non_str_text_is_empty():
    r = vs.score_chat(None, T, CTX)
    assert r.expand.code == vs.EXPAND_REACTION_ONLY and r.topic.code == vs.TOPIC_NO_WORDS


# ───────────────────────────────────────────────────────────── 한 훈련 이어 쓰기 · 요약 · 문구

def test_scorer_history_and_summary():
    sc = vs.ViewerScorer(CTX)
    a = sc.score("라면에 계란 넣으면 어떤 맛이에요?", T)
    b = sc.score("라면에 계란 넣으면 어떤 맛이에요?", T + 5_000)
    c = sc.score("ㅋㅋㅋㅋ", T + 9_000)
    assert a.repeat.code == vs.REPEAT_FRESH and b.repeat.code == vs.REPEAT_SELF_SAME
    assert len(sc.scores) == 3
    s = sc.summary()
    assert s["writes"] == 3
    assert s["expand"]["levels"] == {"good": 2, "ok": 0, "weak": 1}
    assert s["repeat"]["levels"] == {"good": 2, "ok": 0, "weak": 1}
    assert s["expand"]["top_code"] == vs.EXPAND_OPEN_QUESTION
    assert c.topic.level == vs.WEAK
    assert vs.summarize([]) == {"writes": 0, **{a: {"levels": {"good": 0, "ok": 0, "weak": 0}, "top_code": None}
                                                for a in vs.AXES}}


def _template_keys(tpl):
    return {f for _, f, _, _ in string.Formatter().parse(tpl) if f}


def test_reason_text_covers_codes_and_nums():
    all_codes = {c for codes in vs.REASON_CODES.values() for c in codes}
    assert set(vs.REASON_TEXT_KO) == all_codes
    nums_by_axis = {a: set(dict(getattr(score("라면 뭐 넣어요?"), a).nums)) for a in vs.AXES}
    for axis, codes in vs.REASON_CODES.items():
        for code in codes:
            assert code.startswith(axis + ".")
            have = nums_by_axis[axis]
            if code in vs.TOPIC_VECTOR_CODES:   # 벡터가 있을 때만 나오는 코드: 그때만 붙는 숫자 칸(벡터 시험이 실제 결과로 본다)
                have = have | set(vs.TOPIC_OPTIONAL_NUMS)
            assert _template_keys(vs.REASON_TEXT_KO[code]) <= have, code
    assert not set(vs.TOPIC_OPTIONAL_NUMS) & nums_by_axis["topic"]   # 선택 입력이 없으면 숫자 칸은 이전과 같다
    for a in score("치즈 라면 맛있겠다", history=["치즈 라면 맛있겠다"]).axes():
        line = vs.reason_line(a)
        assert line and "{" not in line
    assert vs.reason_line(vs.AxisResult("topic", vs.OK, "topic.unknown")) == "topic.unknown"


# ───────────────────────────────────────────────────────────── 문턱 (guide-config)

def test_settings_defaults_and_overrides():
    assert vs.settings_from_values(None) == (vs.DEFAULT_SETTINGS, [])
    assert vs.settings_from_values({}) == (vs.DEFAULT_SETTINGS, [])
    s, notes = vs.settings_from_values({"viewer_score": {"copy_weak_pct": 90, "expand_min_chars": 3,
                                                         "topic_lookback_sec": 60.5}})
    assert not notes
    assert (s.copy_weak_pct, s.expand_min_chars, s.topic_lookback_ms) == (90, 3, 60_500)
    assert score("왜요?", settings=s).expand.code == vs.EXPAND_OPEN_QUESTION   # 3자 ≥ 3


def test_settings_bad_values_noted():
    s, notes = vs.settings_from_values({"viewer_score": {"copy_window_sec": 10, "ngram": "5", "habit_ok_min": True,
                                                         "transcript_lookback_sec": 200}})
    assert s == vs.DEFAULT_SETTINGS
    assert len(notes) == 4 and all(n.startswith("viewer_score.") for n in notes)


def test_settings_inverted_reset():
    s, notes = vs.settings_from_values({"copy_ok_pct": 90, "copy_weak_pct": 70, "self_ok_pct": 30})
    assert (s.copy_ok_pct, s.copy_weak_pct) == (vs.DEFAULT_COPY_OK_PCT, vs.DEFAULT_COPY_WEAK_PCT)
    assert s.self_ok_pct == 30
    assert len(notes) == 1 and "copy_ok_pct" in notes[0]


def test_ngram_setting_rebuilds_grams():
    other = "치즈떡볶이진짜맛있겠다"
    c = ctx(chats=[(T - 5_000, other)])                       # 맥락은 기본 n=5 로 만듦
    s, _ = vs.settings_from_values({"ngram": 3})
    r = score(other + "요", c=c, settings=s).repeat             # 3-gram 10개 중 9개 = 90%
    assert r.num("copy_pct") == 90


def test_normalizer_is_known():
    assert vs.NORMALIZER in ("textnorm", "fallback")
    assert vs.word_set("떡볶이를 먹었다 {:zz_test_01:} ㅋㅋ") >= {"떡볶이", "먹었다"}


_SAMPLES = ["떡볶이를 먹었다 {:zz_test_01:} ㅋㅋ", "치즈라면에서는 계란이 최고!!", "ＡＢＣ 게임 3판째", "ㅠㅠ 너무해", ""]

_FALLBACK_SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
sys.modules["app.core.textnorm"] = None          # textnorm 이 없는 것처럼
from app.core import viewer_score as vs
print(json.dumps([vs.NORMALIZER] + [sorted(vs.word_set(t)) for t in json.loads(sys.argv[2])], ensure_ascii=True))
"""


def test_fallback_tokens_match_textnorm():
    """textnorm 이 없을 때의 임시 구현이 textnorm.tokens 와 같은 낱말을 낸다(엔진 낱말 파일과 겹침이 맞게)."""
    p = subprocess.run([sys.executable, "-c", _FALLBACK_SCRIPT, ROOT, json.dumps(_SAMPLES)], capture_output=True,
                       timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-500:]
    got = json.loads(p.stdout)
    assert got[0] == "fallback"
    try:
        from app.core import textnorm
    except ImportError:
        pytest.skip("textnorm 없음")
    assert got[1:] == [sorted(w for w in textnorm.tokens(t) if len(w) >= 2) for t in _SAMPLES]


def test_pure_module_imports():
    """엔진·Qt 를 들이지 않는다(층 규칙은 test_layers 가 전부 본다. 여기서는 엔진만 따로)."""
    import ast
    for mod in (vs, ko_cues):
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith(("engine", "PySide6")) for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert not (node.module or "").startswith(("engine", "PySide6", "app."))


# ───────────────────────────────────────────────────────────── 검토 2026-10-23 재현 (0.2)

def test_function_words_do_not_count_as_topic():
    """기능어(이렇게·그렇게·하면·되는·거예요 …)끼리 겹친 것은 주제 겹침도, '구체적' 도 아니다."""
    c = ctx(talk=[(10_000, 20_000, "그렇게 하면 되는 거예요 이렇게 하고 저렇게 하면")])
    r = vs.score_chat("그렇게 하면 되는 거예요?", 60_000, c)
    assert r.topic.level != vs.GOOD and r.topic.num("talk") == 0
    assert r.expand.code != vs.EXPAND_YN_CONCRETE and r.expand.level != vs.GOOD
    r = vs.score_chat("이렇게 하면 돼요?", 60_000, c)
    assert r.topic.level != vs.GOOD and r.topic.num("talk") == 0


def test_common_transcript_words_dropped_by_document_frequency():
    """전사 창의 20% 이상에 나오는 낱말은 (목록에 없어도) 주제 풀·내용 낱말에서 빠진다. 창이 적으면 거르지 않는다."""
    many = [(i * 10_000, i * 10_000 + 9_000, f"합성흔말 주제{i:03d}") for i in range(40)]
    c = ctx(talk=many)
    assert len(c.talk_common) == 1
    r = vs.score_chat("합성흔말 좋다", 395_000, c)
    assert r.topic.num("talk") == 0 and r.topic.num("words") == 1
    few = ctx(talk=many[:5])
    assert few.talk_common == frozenset()
    assert vs.score_chat("합성흔말 좋다", 45_000, few).topic.num("talk") == 1


@pytest.mark.parametrize("word,pool,want", [
    ("그래", "그래가지고", False), ("하면", "하면서", False), ("김치전", "김치찌개", False),
    ("그래도", "그래가지고", False), ("분식", "분식집", False),
    ("떡볶이", "떡볶이를", True), ("떡볶이를", "떡볶이", True), ("보스", "보스", True), ("맛있다", "맛있어요", True),
])
def test_match_prefix_rule(word, pool, want):
    assert vs._match(word, frozenset({pool})) is want


def test_prefix_false_overlap_gone():
    c = ctx(talk=[(0, 10_000, "그래가지고")])
    assert vs.score_chat("그래도 재밌네요", 5_000, c).topic.num("talk") == 0
    c = ctx(talk=[(0, 10_000, "김치찌개")])
    assert vs.score_chat("김치전 좋아요", 5_000, c).topic.num("talk") == 0


@pytest.mark.parametrize("text", ["뭔가 이상해요", "어떤 사람이 그랬어요", "몇 번 해봤어요", "누가 봐도 그렇죠",
                                  "얼마나 좋은데요", "어디 가서 자랑해야지", "무슨 일이 있어도 볼게요"])
def test_statement_with_weak_open_head_is_not_open_question(text):
    assert ko_cues.question_form(text) is None
    assert vs.score_chat(text, 0).expand.code != vs.EXPAND_OPEN_QUESTION


def test_habit_open_questions_and_exclamations_not_weak():
    r = vs.score_chat("어떻게 외우셨어요?", 0, None, ["어떻게 잡아요", "어떻게 가요", "어떻게 하셨어요"])
    assert r.repeat.level != vs.WEAK and r.expand.code == vs.EXPAND_OPEN_QUESTION
    r = vs.score_chat("제주도 좋네요!", 0, None, ["보스 잡았다!", "호텔 멋져요!", "대박!"])
    assert r.repeat.level == vs.OK and r.repeat.code == vs.REPEAT_HABIT
    # 이모티콘·웃음 끝 표지는 그대로 weak 까지 간다
    r = vs.score_chat("제주도 좋네요 ㅋㅋ", 0, None, ["보스 잡았다 ㅋㅋ", "호텔 멋져요 ㅋㅋ", "대박 ㅋㅋㅋ"])
    assert (r.repeat.level, r.repeat.code, r.repeat.num("habit")) == (vs.WEAK, vs.REPEAT_HABIT, 3)


def test_short_common_phrase_not_copy_similar():
    c = ctx(chats=[(31_000, "안녕하세요 ㅋㅋ")])
    r = vs.score_chat("안녕하세요", 40_000, c).repeat
    assert r.level == vs.GOOD and r.num("copy_pct") == 0
    c = ctx(chats=[(31_000, "안녕하세요")])
    assert vs.score_chat("안녕하세요", 40_000, c).repeat.code == vs.REPEAT_COPY_SAME    # 같음은 그대로 본다


def test_transcript_window_by_start_not_future():
    c = ctx(talk=[(30_000, 40_000, ("독수리",))])
    assert vs.score_chat("독수리 봤어요?", 29_999, c).topic.code == vs.TOPIC_NO_CONTEXT
    assert vs.score_chat("독수리 봤어요?", 30_000, c).topic.code == vs.TOPIC_NO_CONTEXT   # 보낸 시각에 시작한 창
    assert vs.score_chat("독수리 봤어요?", 30_001, c).topic.code == vs.TOPIC_TALK_ONE
    assert vs.score_chat("독수리 봤어요?", 120_000, c).topic.code == vs.TOPIC_TALK_ONE   # 정확히 90초 앞
    assert vs.score_chat("독수리 봤어요?", 120_001, c).topic.code == vs.TOPIC_NO_CONTEXT


def test_context_and_scorer_repr_have_no_text():
    marker = "합성채팅본문zz리뷰표지"
    c = ctx(topic=[(0, 1_000, (marker,))], talk=[(0, 1_000, marker)], chats=[(1_000, marker + " 하나 둘")])
    for text in (repr(c), repr(vs.ViewerScorer(c)), repr(c.talk), str(c)):
        assert marker not in text and "하나" not in text
    assert "chats=1" in repr(c)


# ───────────────────────────────────────────────────────────── 전사 베끼기 (0.3, 22 7-1 의 4)
# 합성 전사 두 창: 30~20초 앞 '매운 치즈 라면을 끓여 볼게요', 15~5초 앞 '계란도 하나 넣고 파를 썰어요'
TALK2 = [(T - 30_000, T - 20_000, ["이번엔 매운 치즈 라면을 끓여 볼게요"]),
         (T - 15_000, T - 5_000, ["계란도 하나 넣고 파를 썰어요"])]
CTX_TALK = ctx(talk=TALK2)


def test_version_is_0_3_with_talk_copy_code():
    assert vs.VERSION == "0.3"
    assert vs.REPEAT_TALK_COPY in vs.REASON_CODES["repeat"] and vs.REPEAT_TALK_COPY.startswith("repeat.")


@pytest.mark.parametrize("text, level, code, nums", [
    # 한 창 안의 낱말 3개만 옮긴 서술 → weak
    ("매운 치즈 라면", vs.WEAK, vs.REPEAT_TALK_COPY, {"talk_words": 3, "talk_copy": 3, "talk_one": 3}),
    # 낱말 2개만 옮김 → ok
    ("치즈 라면", vs.OK, vs.REPEAT_TALK_COPY, {"talk_words": 2, "talk_copy": 2, "talk_one": 2}),
    # 3개지만 두 창에 걸침(한 말을 그대로 옮긴 것이 아님) → ok
    ("치즈 라면 계란", vs.OK, vs.REPEAT_TALK_COPY, {"talk_words": 3, "talk_copy": 3, "talk_one": 2}),
    # 내 낱말을 하나 더함 → good
    ("매운 치즈 라면 국물 최고", vs.GOOD, vs.REPEAT_FRESH, {"talk_words": 5, "talk_copy": 3, "talk_one": 0}),
    # 낱말 하나뿐인 반응은 베끼기로 보지 않는다(주제 적합성이 따로 본다)
    ("라면!", vs.GOOD, vs.REPEAT_FRESH, {"talk_words": 1, "talk_copy": 1}),
])
def test_talk_copy_three_levels(text, level, code, nums):
    r = score(text, c=CTX_TALK).repeat
    assert (r.level, r.code) == (level, code), text
    for k, v in nums.items():
        assert r.num(k) == v, (text, k)


def test_talk_copy_questions_are_not_copy():
    # 스트리머 낱말로 묻는 것은 베끼기가 아니다(되묻기는 대화 확장성이 본다)
    for text in ("매운 치즈 라면 맛 어때요?", "매운 치즈 라면?", "치즈 라면 맞아요?"):
        r = score(text, c=CTX_TALK).repeat
        assert r.code != vs.REPEAT_TALK_COPY, text


def test_talk_copy_window_bounds():
    s = vs.DEFAULT_SETTINGS
    one = [(T - s.copy_window_ms, T - s.copy_window_ms + 10_000, ["매운 치즈 라면을 끓여 볼게요"])]
    assert score("매운 치즈 라면", c=ctx(talk=one)).repeat.code == vs.REPEAT_TALK_COPY      # 정확히 45초 앞에 시작
    old = [(T - s.copy_window_ms - 1, T - s.copy_window_ms + 9_999, ["매운 치즈 라면을 끓여 볼게요"])]
    r = score("매운 치즈 라면", c=ctx(talk=old)).repeat
    assert r.code == vs.REPEAT_FRESH and r.num("talk_copy") == 0                          # 그보다 앞이면 안 봄
    future = [(T, T + 10_000, ["매운 치즈 라면을 끓여 볼게요"])]
    assert score("매운 치즈 라면", c=ctx(talk=future)).repeat.code == vs.REPEAT_FRESH        # 아직 안 들은 말
    s60, _ = vs.settings_from_values({"copy_window_sec": 60})
    old55 = [(T - 55_000, T - 45_000, ["매운 치즈 라면을 끓여 볼게요"])]
    assert score("매운 치즈 라면", c=ctx(talk=old55)).repeat.code == vs.REPEAT_FRESH
    assert score("매운 치즈 라면", c=ctx(talk=old55), settings=s60).repeat.code == vs.REPEAT_TALK_COPY


def test_talk_copy_no_context_and_empty_text():
    for c in (vs.EMPTY_CONTEXT, ctx(topic=[(T - 60_000, T, ("매운", "치즈", "라면"))])):   # 주제 흐름만으로는 안 봄
        r = score("매운 치즈 라면", c=c).repeat
        assert r.code == vs.REPEAT_FRESH and r.num("talk_copy") == 0
    r = score("{:zz_test_03:} ㅋㅋ", c=CTX_TALK).repeat
    assert r.num("talk_words") == 0 and r.code != vs.REPEAT_TALK_COPY


def test_talk_copy_precedence():
    # 앞서 쓴 내 말과 같으면 그것이 먼저(같음 weak), 채팅 베끼기도 먼저
    r = score("매운 치즈 라면", history=["매운 치즈 라면"], c=CTX_TALK).repeat
    assert r.code == vs.REPEAT_SELF_SAME and r.num("talk_copy") == 3
    c = ctx(talk=TALK2, chats=[(T - 1_000, "매운 치즈 라면")])
    assert score("매운 치즈 라면", c=c).repeat.code == vs.REPEAT_COPY_SAME
    # 전사 베끼기 weak 는 말버릇 weak 보다 먼저, 전사 베끼기 ok 는 말버릇 ok 보다 먼저
    r = score("매운 치즈 라면 ㅋㅋ", history=["보스 잡았다 ㅋㅋ", "호텔 멋져요 ㅋㅋ", "대박 ㅋㅋㅋ"], c=CTX_TALK).repeat
    assert (r.level, r.code) == (vs.WEAK, vs.REPEAT_TALK_COPY) and r.num("habit") == 3
    r = score("치즈 라면 ㅋㅋ", history=["보스 잡았다 ㅋㅋ", "호텔 멋져요 ㅋㅋ"], c=CTX_TALK).repeat
    assert (r.level, r.code) == (vs.OK, vs.REPEAT_TALK_COPY)


def test_talk_copy_common_words_do_not_count():
    # 전사 창 문서 빈도로 뺀 흔한 낱말(기능어)은 양쪽에서 빠진다 — 남은 내 낱말이 하나뿐이면 베끼기 아님
    rows = [(i * 10_000, i * 10_000 + 9_000, ["합성공통 합성낱말%d" % i]) for i in range(40)]
    c = ctx(talk=rows)
    assert "합성공통" in c.talk_common
    r = vs.score_chat("합성공통 합성낱말39", 399_500, c).repeat
    assert r.code != vs.REPEAT_TALK_COPY and (r.num("talk_words"), r.num("talk_copy")) == (1, 1)


def test_talk_copy_reason_line_has_numbers_only():
    r = score("매운 치즈 라면", c=CTX_TALK).repeat
    line = vs.reason_line(r)
    assert "45초" in line and "3개" in line and "{" not in line
    for w in ("매운", "치즈", "라면"):
        assert w not in line and w not in json.dumps(r.as_dict(), ensure_ascii=False)


def test_talk_copy_deterministic_and_fast():
    import time
    # 창마다 다른 합성 낱말 + 열 창에 한 번 '매운 치즈 라면'(문서 빈도 10% — 흔한 낱말로 빠지지 않음)
    talk = [(i * 10_000, i * 10_000 + 9_000, ["합성낱말%d" % i] + (["매운 치즈 라면"] if i % 10 == 0 else []))
            for i in range(1440)]
    c = ctx(talk=talk)
    t0 = time.perf_counter()
    rs = [vs.score_chat("매운 치즈 라면", 5_005_000 + k, c).repeat for k in range(200)]
    assert time.perf_counter() - t0 < 2.0
    assert rs[0].code == vs.REPEAT_TALK_COPY and all(x == rs[0] for x in rs)
