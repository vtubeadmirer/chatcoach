"""engine/topics.py · app/core/topics.py · schemas/topic-flow.schema.json — 합성 데이터만 쓴다.

방송은 만든 것이다: 18분, 6분마다 주제가 바뀐다(게임 → 요리 → 여행). 시청자는 '시청자N'(sha1('fixture-N')).
"""
import hashlib
import json
import os
import re

import pytest

from app.core import ids
from app.core import topics as T
from engine import topics as E

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALT = b"\x02" * 32
MIN = 60_000

TOPIC_SENTENCES = {
    "game": ["보스 공략 이번에 성공", "아이템 강화 보스 잡자", "던전 보스 패턴 공략", "아이템 던전 강화 공략"],
    "food": ["떡볶이 양념 준비 고추장", "고추장 떡볶이 어묵 넣고", "어묵 떡볶이 양념 맛있다", "양념 고추장 어묵 떡볶이"],
    "trip": ["제주 여행 비행기 예약", "제주 숙소 호텔 예약", "비행기 시간 제주 공항", "공항 호텔 숙소 제주"],
}
CHAT_SENTENCES = {
    "game": ["보스 언제 잡아요?", "아이템 뭐 나왔어요", "공략 보고 하세요 {:d_9:}"],
    "food": ["떡볶이 맛있겠다 ㅋㅋ", "어묵 많이 넣어요", "양념 레시피 알려줘요"],
    "trip": ["제주 언제 가요?", "비행기 표 비싸요", "호텔 어디예요"],
}
ORDER = ["game", "food", "trip"]


def vid(n: int) -> str:
    return ids.viewer_id(SALT, hashlib.sha1(f"fixture-{n}".encode()).hexdigest())


def synth(minutes_per_topic=6, start_ms=0):
    utts, msgs = [], []
    for ti, topic in enumerate(ORDER):
        t0 = start_ms + ti * minutes_per_topic * MIN
        for k in range(minutes_per_topic * 6):          # 10초마다 발화
            s = TOPIC_SENTENCES[topic][k % 4]
            utts.append(E.Utt(t0 + k * 10_000, t0 + k * 10_000 + 3_000, "음 그니까 " + s))
        for k in range(minutes_per_topic * 4):          # 15초마다 채팅
            msgs.append(E.Msg(t0 + k * 15_000 + 2_000, vid(1 + k % 6), CHAT_SENTENCES[topic][k % 3]))
    return utts, msgs


# ── 낱말 ─────────────────────────────────────────────────────────────────

def test_tokens_basic():
    assert E.tokens("{:d_1:} 떡볶이를 만들어요 ㅋㅋㅋ") == ["떡볶이", "만들어"]
    assert E.tokens("그니까 진짜 하하 1234 오오오") == []           # 불용어·숫자만·같은 글자 되풀이
    assert all(T.is_keyword(t) for t in E.tokens("아주아주아주긴낱말입니다 제주도에서"))
    assert "아주아주아주긴낱말입니다" not in E.tokens("아주아주아주긴낱말입니다")   # 6자 넘는 낱말은 버림


# ── 덩어리 ───────────────────────────────────────────────────────────────

def test_three_topics_found():
    utts, msgs = synth()
    part = E.segment(utts, msgs, start_ms=0, end_ms=18 * MIN)
    blocks = part["blocks"]
    assert len(blocks) == 3
    assert abs(blocks[1]["start_ms"] - 6 * MIN) <= MIN and abs(blocks[2]["start_ms"] - 12 * MIN) <= MIN
    assert blocks[1]["change_ms"] == blocks[1]["start_ms"] and blocks[0]["change_ms"] is None
    names = [set(b["name_keywords"]) for b in blocks]
    assert names[0] & {"보스", "공략", "아이템", "던전", "강화"}
    assert names[1] & {"떡볶이", "양념", "고추장", "어묵"}
    assert names[2] & {"제주", "비행기", "호텔", "숙소", "공항"}
    assert all(b["boundary_score"] > 0.15 for b in blocks[1:])
    assert sum(b["utterances"] for b in blocks) == len(utts) and sum(b["chats"] for b in blocks) == len(msgs)


def test_change_time_refined_to_first_utterance():
    utts, msgs = synth()
    # 요리 이야기를 창 경계보다 25초 늦게 시작하게 바꾼다 → 바뀐 시각이 그 발화로 당겨지거나 늦춰진다
    shifted = [u if not (6 * MIN <= u.t_ms < 6 * MIN + 25_000) else E.Utt(u.t_ms, u.end_ms, "음 아이템 보스 공략")
               for u in utts]
    part = E.segment(shifted, msgs, start_ms=0, end_ms=18 * MIN)
    b1 = part["blocks"][1]["start_ms"]
    assert b1 % 10_000 == 0 and 5 * MIN <= b1 <= 7 * MIN
    assert b1 in {u.t_ms for u in shifted} or b1 % MIN == 0


def test_min_block_respected():
    utts, msgs = synth()
    part = E.segment(utts, msgs, start_ms=0, end_ms=18 * MIN, cfg=E.TopicConfig(min_block_ms=10 * MIN))
    assert len(part["blocks"]) <= 2


def test_empty_input_one_block():
    part = E.segment([], [], start_ms=0, end_ms=5 * MIN)
    doc = E.build_doc(part, cfg=E.TopicConfig(), run_id="topics-test", created_at="2026-10-22T00:00:00+09:00")
    assert T.validate(doc) == []
    assert len(doc["blocks"]) == 1 and doc["blocks"][0]["keywords"] == [] and doc["blocks"][0]["top_keyword"] is None
    assert all(g["similarity"] is None for g in doc["gaps"])


def test_bad_range():
    with pytest.raises(ValueError):
        E.segment([], [], start_ms=10, end_ms=10)


def test_chat_only_word_needs_several_viewers():
    utts, msgs = synth()
    # 한 시청자만 쓴 낱말(사적인 말일 수 있음)은 키워드가 되지 않는다
    lone = [E.Msg(7 * MIN + k * 1000, vid(9), "강아지 강아지") for k in range(30)]
    part = E.segment(utts, msgs + lone, start_ms=0, end_ms=18 * MIN)
    assert all("강아지" not in b["keywords"] for b in part["blocks"])
    # 여러 시청자가 쓰면 후보가 된다
    many = [E.Msg(7 * MIN + k * 1000, vid(10 + k), "강아지 강아지") for k in range(30)]
    part = E.segment(utts, msgs + many, start_ms=0, end_ms=18 * MIN)
    assert any("강아지" in b["keywords"] for b in part["blocks"])


def test_nickname_terms_excluded():
    utts, msgs = synth()
    extra = [E.Utt(13 * MIN + k * 5_000, 13 * MIN + k * 5_000 + 1000, "감자튀김님 감자튀김님 반가워요") for k in range(20)]
    part = E.segment(utts + extra, msgs, start_ms=0, end_ms=18 * MIN)
    assert any("감자튀김" in b["keywords"] for b in part["blocks"])            # 거르지 않으면 들어온다(대조군)
    part = E.segment(utts + extra, msgs, start_ms=0, end_ms=18 * MIN, nicknames=["감자튀김"])
    assert all("감자튀김" not in b["keywords"] for b in part["blocks"])


def test_embedding_alone_can_split():
    """낱말로는 가를 수 없을 때(뜻으로만 이어진 말) 임베딩만으로 가를 수 있다. 임베딩 함수는 합성."""
    utts = [E.Utt(k * 10_000, k * 10_000 + 2000, "ㅋㅋ 가" if k < 36 else "ㅎㅎ 나") for k in range(72)]
    calls = []

    def fake_embed(texts):
        calls.append(len(texts))
        return [[1.0, 0.0] if "ㅋ" in t else [0.0, 1.0] for t in texts]

    no = E.segment(utts, [], start_ms=0, end_ms=12 * MIN)
    assert len(no["blocks"]) == 1
    yes = E.segment(utts, [], start_ms=0, end_ms=12 * MIN, embed=fake_embed)
    assert calls == [12] and len(yes["blocks"]) == 2 and yes["blocks"][1]["start_ms"] == 6 * MIN

    class EncodeObj:                       # engine.semantic.Embedder 모양(.encode)
        def encode(self, texts):
            return fake_embed(texts)

    obj = E.segment(utts, [], start_ms=0, end_ms=12 * MIN, embed=EncodeObj())
    assert [b["start_ms"] for b in obj["blocks"]] == [b["start_ms"] for b in yes["blocks"]]


def test_semantic_off_means_words_only():
    assert E.load_semantic("off") == (None, None)


def test_embedding_wrong_count_rejected():
    utts, msgs = synth()
    with pytest.raises(ValueError):
        E.segment(utts, msgs, start_ms=0, end_ms=18 * MIN, embed=lambda texts: [[1.0]])


def test_doc_has_no_sentences():
    utts, msgs = synth(start_ms=20 * MIN)
    part = E.segment(utts, msgs, start_ms=20 * MIN, end_ms=38 * MIN)
    doc = E.build_doc(part, cfg=E.TopicConfig(), run_id="topics-test", created_at="2026-10-22T00:00:00+09:00",
                      video_no="0")
    assert T.validate(doc) == []
    blob = json.dumps(doc, ensure_ascii=False)
    for s in [x for v in TOPIC_SENTENCES.values() for x in v] + [x for v in CHAT_SENTENCES.values() for x in v]:
        assert s not in blob
    for b in doc["blocks"]:
        assert all(T.is_keyword(k) for k in b["keywords"])
    assert doc["blocks"][0]["start_ms"] == 20 * MIN and doc["blocks"][-1]["end_ms"] == 38 * MIN


def test_pick_boundaries_and_depth():
    sims = [0.9, 0.9, 0.1, 0.9, 0.9, None, 0.9]
    d = E.depth_scores(sims)
    assert d[2] == pytest.approx(1.6) and d[5] is None and d[0] == 0
    assert E.pick_boundaries(d, 8, 2, 0.15, 0.5) == [3]
    assert E.pick_boundaries([None, None], 3, 1, 0.15, 0.5) == []


def test_config_hash_stable():
    assert E.TopicConfig().sha256() == E.TopicConfig().sha256()
    assert E.TopicConfig().sha256() != E.TopicConfig(window_ms=30_000).sha256()


def test_load_embedder_only_engine_modules():
    with pytest.raises(ValueError):
        E.load_embedder("os:system")
    with pytest.raises(ValueError):
        E.load_embedder("engine.features")
    assert E.load_embedder("engine.features:normalize") is not None


# ── 명령줄(합성 프로젝트) ───────────────────────────────────────────────

def _msg_rec(n, t_ms, text, kind="text"):
    uid = hashlib.sha1(f"fixture-{n}".encode()).hexdigest()
    return {"chat_id": ids.chat_id_base("0", 1_700_000_000_000 + t_ms, uid, 1, text), "t_ms": t_ms,
            "server_time_ms": 1_700_000_000_000 + t_ms, "type_code": 1, "kind": kind, "status": "NORMAL",
            "viewer_id": vid(n), "is_anonymous": False, "nickname_masked": "시*", "role": "viewer", "text": text,
            "raw_ref": {"fetch_id": "F20261022-test", "page": 0, "index": 0}}


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "합성 프로젝트-주제"
    (p / "chat").mkdir(parents=True)
    (p / "derived").mkdir()
    utts, msgs = synth()
    with open(p / "chat" / "messages.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for i, m in enumerate(msgs):
            f.write(json.dumps(_msg_rec(1 + i % 6, m.t_ms, m.text), ensure_ascii=False) + "\n")
    with open(p / "chat" / "fetch-F20261022-test.json", "w", encoding="utf-8") as f:
        json.dump({"fetch_id": "F20261022-test", "video_no": "0"}, f)
    asr = {"format": "chatcoach.asr", "format_version": 1, "model": {"name": "large-v3-turbo"},
           "settings": {"vad": True}, "audio": {"duration_ms": 18 * MIN},
           "segments": [{"id": i, "start_ms": u.t_ms, "end_ms": u.end_ms, "text": u.text, "words": []}
                        for i, u in enumerate(utts)]}
    with open(p / "derived" / "asr.json", "w", encoding="utf-8") as f:
        json.dump(asr, f, ensure_ascii=False)
    return str(p)


def test_cli_writes_valid_doc_and_prints_numbers_only(project, tmp_path, capsys):
    out = tmp_path / "out" / "topic-flow.json"
    rc = E.main(["--project", project, "--out", str(out), "--start-ms", "0", "--end-ms", str(18 * MIN)])
    assert rc == 0
    printed = capsys.readouterr().out
    with open(out, encoding="utf-8") as f:
        doc = json.load(f)
    assert T.validate(doc) == []
    assert doc["target"]["video_no"] == "0" and re.match(r"^[0-9a-f]{64}$", doc["target"]["asr_sha256"])
    assert len(doc["blocks"]) == 3
    for b in doc["blocks"]:
        for k in b["keywords"]:
            assert k not in printed                       # 표준 출력에 키워드를 내지 않는다
    assert not [n for n in os.listdir(out.parent) if ".part-" in n]


def test_cli_refuses_pilot_project(project, tmp_path, capsys):
    """시범 프로젝트(pilot-*)는 평문 --out 을 쓰지 않는다 — 봉인 경로만(D-15 8, D-17 5). engine.link CLI 도 같다."""
    import shutil
    from engine import link as L
    pilot = tmp_path / "pilot-15600000"
    shutil.copytree(project, pilot)
    out = tmp_path / "t.json"
    assert E.main(["--project", str(pilot), "--out", str(out)]) == 1
    assert "pilot_needs_seal" in capsys.readouterr().out and not out.exists()
    assert L.main(["--project", str(pilot), "--out", str(out), "--engine", "v0.2"]) == 1
    assert "pilot_needs_seal" in capsys.readouterr().out and not out.exists()


def test_cli_file_start_offset(project, tmp_path):
    """영상 파일이 VOD 2분에서 시작하면 발화 시각이 2분 밀린다(채팅은 그대로)."""
    out = tmp_path / "o.json"
    E.main(["--project", project, "--out", str(out), "--file-start-ms", str(2 * MIN)])
    with open(out, encoding="utf-8") as f:
        doc = json.load(f)
    assert T.validate(doc) == []
    assert doc["range"]["end_ms"] >= 20 * MIN - 10_000


# ── 스키마 파일 ↔ core 검사 ──────────────────────────────────────────────

def _schema():
    with open(os.path.join(ROOT, "schemas", "topic-flow.schema.json"), encoding="utf-8") as f:
        return json.load(f)


def test_schema_matches_core():
    s = _schema()
    assert s["properties"]["schema"]["const"] == T.SCHEMA
    assert s["properties"]["schema_version"]["const"] == T.SCHEMA_VERSION
    assert s["properties"]["time_axis"]["const"] == T.TIME_AXIS
    assert tuple(s["required"]) == T._REQUIRED_TOP
    assert set(s["properties"]) == set(T._TOP_KEYS)
    bi = s["properties"]["blocks"]["items"]
    assert tuple(bi["required"]) == T._BLOCK_KEYS and set(bi["properties"]) == set(T._BLOCK_KEYS)
    assert s["$defs"]["keyword"]["pattern"] == T.KEYWORD_RE.pattern
    assert bi["properties"]["keywords"]["maxItems"] == T.MAX_KEYWORDS
    assert bi["properties"]["name_keywords"]["maxItems"] == T.MAX_NAME_KEYWORDS
    assert set(s["properties"]["gaps"]["items"]["required"]) == set(T._GAP_KEYS)


def test_schema_with_jsonschema_if_available():
    jsonschema = pytest.importorskip("jsonschema")
    utts, msgs = synth()
    doc = E.build_doc(E.segment(utts, msgs, start_ms=0, end_ms=18 * MIN), cfg=E.TopicConfig(),
                      run_id="topics-test", created_at="2026-10-22T00:00:00+09:00")
    jsonschema.validate(doc, _schema())


# ── app/core/topics ─────────────────────────────────────────────────────

def _doc(blocks=None):
    blocks = blocks if blocks is not None else [
        {"index": 0, "start_ms": 0, "end_ms": 6 * MIN, "keywords": ["보스", "공략"], "name_keywords": ["보스", "공략"],
         "top_keyword": "보스", "change_ms": None, "boundary_score": None, "utterances": 3, "chats": 4},
        {"index": 1, "start_ms": 6 * MIN, "end_ms": 10 * MIN, "keywords": ["떡볶이"], "name_keywords": ["떡볶이"],
         "top_keyword": "떡볶이", "change_ms": 6 * MIN, "boundary_score": 0.8, "utterances": 2, "chats": 1},
    ]
    return {"schema": T.SCHEMA, "schema_version": 1, "run_id": "topics-x", "created_at": "2026-10-22T00:00:00+09:00",
            "engine": {"name": "e", "version": "1"}, "target": {}, "time_axis": "vod_ms", "window_ms": MIN,
            "range": {"start_ms": 0, "end_ms": 10 * MIN}, "blocks": blocks}


def test_core_validate_ok_and_errors():
    assert T.validate(_doc()) == []
    d = _doc()
    d["blocks"][0]["keywords"] = ["보스 공략 이번에", "공략"]           # 문장은 키워드가 아니다
    assert any("keywords" in x for x in T.validate(d))
    d = _doc()
    d["blocks"][1]["start_ms"] = 6 * MIN + 1
    assert any("이어지지" in x for x in T.validate(d))
    d = _doc()
    d["blocks"][0]["top_keyword"] = "공략"
    assert any("top_keyword" in x for x in T.validate(d))
    d = _doc()
    d["text"] = "전사문"
    assert any("모르는 칸" in x for x in T.validate(d))
    d = _doc()
    d["blocks"][0]["transcript"] = "전사문"
    assert any("모르는 칸" in x for x in T.validate(d))
    d = _doc()
    d["range"]["end_ms"] = 11 * MIN
    assert any("덮지" in x for x in T.validate(d))
    d = _doc()
    d["stats"] = {"note": "글"}
    assert any("stats" in x for x in T.validate(d))
    assert T.validate([]) == ["문서가 객체가 아님"]
    with pytest.raises(ValueError):
        T.blocks_from_doc({"schema": T.SCHEMA})


def test_core_block_index_at():
    bl = T.blocks_from_doc(_doc())
    assert bl[1].name == "떡볶이" and bl[0].name == "보스 · 공략" and bl[0].minutes == 6.0
    assert T.block_index_at(bl, 0) == 0 and T.block_index_at(bl, 6 * MIN - 1) == 0
    assert T.block_index_at(bl, 6 * MIN) == 1 and T.block_index_at(bl, 10 * MIN) == 1
    assert T.block_index_at(bl, 10 * MIN + 1) is None and T.block_index_at(bl, -1) is None
    assert T.block_index_at([], 0) is None
    assert T.change_times(bl) == [6 * MIN]


def test_core_summarize():
    bl = T.blocks_from_doc(_doc())
    msgs = [{"chat_id": f"c{i}", "t_ms": i * MIN, "kind": "text"} for i in range(10)]   # 0..9분, 덩어리0 에 6개
    msgs += [{"chat_id": "d1", "t_ms": MIN, "kind": "donation"},
             {"chat_id": "late", "t_ms": 11 * MIN, "kind": "text"},
             {"chat_id": "bad", "t_ms": None, "kind": "text"}]
    s = T.summarize(bl, msgs, reacted_chat_ids=["c1", "c1", "c7", "zzz"],
                    questions={"c2": True, "c3": False, "c8": False, "c9": None, "nope": False})
    r0, r1 = s.rows
    assert (r0.chats, r0.reacted, r0.questions, r0.missed_questions) == (6, 1, 2, 1)
    assert r0.chats_per_min == 1.0 and r0.reaction_rate == round(1 / 6, 3)
    assert (r1.chats, r1.reacted, r1.questions, r1.missed_questions, r1.questions_unknown) == (4, 1, 2, 1, 1)
    assert s.total.chats == 10 and s.total.missed_questions == 2 and s.total.chats_per_min == 1.0
    assert s.outside_chats == 1 and s.unmatched_reactions == 1 and s.unmatched_questions == 1
    allk = T.summarize(bl, msgs, kinds=None)
    assert allk.total.chats == 11


def test_core_summarize_ignores_reactions_on_excluded_kinds():
    """후원 채팅에 붙은 반응·질문은 세지 않는다 — 반응률이 1 을 넘지 않고, 화면이 '3%' 로 잘못 읽지 않는다."""
    pytest.importorskip("PySide6")
    from app.ui import summary_view as SV
    bl = T.blocks_from_doc(_doc())
    msgs = [{"chat_id": "c1", "t_ms": 1_000, "kind": "text"},
            {"chat_id": "c2", "t_ms": 2_000, "kind": "donation"},
            {"chat_id": "c3", "t_ms": 3_000, "kind": "donation"}]
    s = T.summarize(bl, msgs, reacted_chat_ids=["c1", "c2", "c3"], questions={"c2": False})
    r0 = s.rows[0]
    assert (r0.chats, r0.reacted, r0.reaction_rate) == (1, 1, 1.0)
    assert (r0.questions, r0.missed_questions) == (0, 0)
    assert s.total.reaction_rate == 1.0
    assert s.unmatched_reactions == 0 and s.unmatched_questions == 0     # 걸러진 것은 '목록에 없음' 도 아니다
    tbl = SV.normalize_topics(s.rows)
    assert tbl.rows[0].reply_rate == pytest.approx(1.0)
    # kinds=None 이면 후원도 채팅으로 세므로 3/3
    allk = T.summarize(bl, msgs, reacted_chat_ids=["c1", "c2", "c3"], kinds=None)
    assert (allk.rows[0].chats, allk.rows[0].reacted, allk.rows[0].reaction_rate) == (3, 3, 1.0)
    # 같은 id 가 후원으로 먼저, 글로 나중에 오면 글로 센다
    mixed = T.summarize(bl, [{"chat_id": "x", "t_ms": 0, "kind": "donation"},
                             {"chat_id": "x", "t_ms": 0, "kind": "text"}], reacted_chat_ids=["x"])
    assert (mixed.rows[0].chats, mixed.rows[0].reacted) == (1, 1)


def test_core_summary_table_and_export():
    bl = T.blocks_from_doc(_doc())
    s = T.summarize(bl, [{"chat_id": "a", "t_ms": 0, "kind": "text"}], questions={"a": None})
    tbl = T.summary_table(s)
    assert tbl[0] == [h for _, h in T.SUMMARY_COLUMNS]
    assert len(tbl) == 4 and all(len(r) == len(T.SUMMARY_COLUMNS) for r in tbl)
    assert tbl[1][0] == "1" and tbl[1][3] == "보스 · 공략" and tbl[2][5] == "0.0" and tbl[2][7] == "-"
    assert tbl[1][8] == "1 (모름 1)" and tbl[-1][3] == "합계"
    ex = T.summary_export(s, topic_run_id="topics-x")
    assert json.loads(json.dumps(ex)) == ex and ex["rows"][0]["name"] == "보스 · 공략"
    empty = T.summarize([], [])
    assert empty.total.chats == 0 and empty.total.chats_per_min is None and len(T.summary_table(empty)) == 2
