"""engine/features.py · engine/link.py — 합성 데이터만 쓴다(닉네임 '시청자N', userIdHash = sha1('fixture-N'), video_no 0)."""
import csv
import hashlib
import json
import os
import subprocess
import sys

import pytest

from app.core import ids
from app.core.timecode import Calibration
from engine import features as F
from engine import link as L

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALT = b"\x01" * 32


def uid(n: int) -> str:
    return hashlib.sha1(f"fixture-{n}".encode()).hexdigest()


def vid(n: int) -> str:
    return ids.viewer_id(SALT, uid(n))


# ── features ─────────────────────────────────────────────────────────────

def test_normalize_strips_emotes_jamo_symbols():
    assert F.normalize("{:d_47:} 안녕 ㅋㅋㅋㅋ 하세요!!  ") == "안녕 하세요"
    assert F.normalize("ㅠㅠ 진짜?", keep_question=True) == "진짜?"
    assert F.compact("Hello  World ㅎㅎ") == "helloworld"
    assert F.normalize("") == ""


def test_ngrams_and_similarity():
    assert F.char_ngrams("가나다") == {"가나": 1, "나다": 1}
    assert F.char_ngrams("가") == {"가": 1}
    assert F.similarity("여행 가면 뭐 해요", "여행 가면 뭐 해요") == 1.0
    assert F.similarity("여행", "게임") == 0.0
    assert 0.5 < F.similarity("여행 가면 뭐 해요?", "여행 가면 뭐 하세요?") < 1.0
    assert F.containment(F.char_ngrams("가나다"), F.char_ngrams("가나다라마")) == 1.0


@pytest.mark.parametrize("text,q", [
    ("여행 가면 꼭 하는 거 있어요?", True), ("오늘 뭐 먹었어", True), ("어디 살아요", True),
    ("밥 먹었나요", True), ("내일 갈까요", True), ("왜 그래", True), ("몇 시에 끝나", True),
    ("안녕하세요", False), ("ㅋㅋㅋㅋ", False), ("맛있겠다", False), ("{:d_1:}", False),
])
def test_is_question(text, q):
    assert F.is_question(text) is q


def test_direct_reference_terms():
    assert F.direct_reference_terms("방금 올라온 질문인데요") == ["방금", "질문", "올라온"]
    assert F.direct_reference_terms("아까 물어보신 거") == ["물어보", "아까"]
    assert F.direct_reference_terms("오늘 날씨 좋네요") == []


def test_answer_form():
    assert F.answer_form("여행 가면 꼭 하는 거 있어요?", "여행 가면 저는 무조건 맛집부터 찾아봐요")
    assert F.answer_form("밥 먹었어요?", "네 먹었어요")
    assert not F.answer_form("밥 먹었어요", "네 먹었어요")            # 채팅이 질문이 아님
    assert not F.answer_form("밥 먹었어요?", "오늘 게임 재밌다")        # 답 표지도 내용 공유도 없음
    assert not F.answer_form("밥 먹었어요?", "밥 먹었냐고?")            # 되묻기
    assert not F.answer_form("?", "제가 그")                             # 무엇을 묻는지 모름


def test_text_repeated():
    assert F.text_repeated("여행 가면 꼭 하는 거 있어요?", "아 여행 가면 꼭 하는 거 있어요? 음")
    assert not F.text_repeated("ㅋㅋㅋ", "ㅋㅋㅋ")
    assert not F.text_repeated("안녕", "안녕 여러분")                    # 4글자 미만은 판정 안 함
    assert not F.text_repeated("안녕하세요", "안녕하세요 여러분")          # 인사만 있는 채팅
    assert not F.text_repeated("오늘 저녁 메뉴 추천", "게임 이야기 할게요")
    assert not F.text_repeated("라면 뭐 좋아해요?", "라면 저는 짜장 라면 좋아해요")   # 낱말을 흩어 되받는 답
    assert F.longest_common_substring("가나다라", "xx나다라y") == 3 and F.longest_common_substring("", "a") == 0


def test_unique_and_same_topic():
    assert "제주도" in F.unique_content("제주도 가보셨어요?", "제주도는 작년에 갔죠", ["오늘 날씨"])
    assert F.unique_content("제주도 가보셨어요?", "제주도는 작년에 갔죠", ["제주도 얘기 나왔는데"]) == set()
    assert F.same_topic("제주도 가보셨어요?", "제주도 좋아요")
    assert not F.same_topic("제주도 가보셨어요?", "게임 시작합니다")


def test_content_tokens_strip_particles_and_stopwords():
    t = F.content_tokens("제주도에서 진짜 맛집을 찾았어요")
    assert "제주도" in t and "맛집" in t and "진짜" not in t


def test_repetition_helpers():
    assert F.is_low_content("ㅋㅋㅋㅋ") and F.is_low_content("{:a:}") and not F.is_low_content("안녕")
    assert F.repeat_ratio("와와와와") == 1.0 and F.repeat_ratio("") == 0.0
    assert F.similar_indices("여행 가면 뭐 해요?", ["여행 가면 뭐 하세요?", "게임 해요"]) == [0]


def test_jamo_and_distance():
    assert F.jamo("한") == "ㅎㅏㄴ"
    assert F.jamo("a가") == "aㄱㅏ"
    assert F.edit_distance("abc", "abd") == 1 and F.edit_distance("", "ab") == 2
    assert F.jamo_distance_ratio("시청자", "시청자") == 0.0


def test_nickname_called():
    assert F.nickname_called(None, "시청자1님 안녕하세요") is None          # 목록 없음 → 판단 불가
    assert F.nickname_called(["시청자1"], "시청자1님 안녕하세요") is True
    assert F.nickname_called(["시청자1"], "시청자 1 님 안녕하세요") is True   # 공백 무시
    assert F.nickname_called(["감자튀김"], "감자뒤김님 어서오세요") is True     # 소리 비슷한 ASR 오류(자모 1개)
    assert F.nickname_called(["감자튀김"], "고구마님 어서오세요") is False
    assert F.nickname_called(["시청자1"], "시청자1 안녕하세요") is False        # '님' 없음
    assert F.nickname_called(["가"], "가님 안녕") is False                     # 너무 짧은 닉네임
    assert F.nickname_called([], "누구님 안녕") is False


# ── link: 합성 프로젝트 ─────────────────────────────────────────────────

def _msg(n_viewer: int, t_ms: int, text: str, kind="text", type_code=1):
    base = ids.chat_id_base("0", 1_700_000_000_000 + t_ms, uid(n_viewer), type_code, text)
    return {"chat_id": base, "t_ms": t_ms, "server_time_ms": 1_700_000_000_000 + t_ms, "type_code": type_code,
            "kind": kind, "status": "NORMAL", "viewer_id": vid(n_viewer), "is_anonymous": False,
            "nickname_masked": "시*", "role": "viewer", "text": text,
            "raw_ref": {"fetch_id": "F20261020-test", "page": 0, "index": 0}}


def _asr(segs, duration_ms=200000):
    return {"format": "chatcoach.asr", "format_version": 1, "model": {"name": "large-v3-turbo"},
            "settings": {"vad": True}, "audio": {"duration_ms": duration_ms},
            "segments": [{"id": i, "start_ms": s, "end_ms": e, "text": t, "words": []}
                         for i, (s, e, t) in enumerate(segs)]}


SCENARIO_MSGS = [
    # 예시 1: 닉네임 호명 + 질문 되풀이 → 직접
    (1, 10_000, "여행 가면 꼭 하는 거 있어요?"),
    # 예시 2: 8초 뒤 답, 호명 없음 → 높음
    (2, 40_000, "캠핑 가면 꼭 챙기는 거 있어요?"),
    # 예시 4: 같은 질문이 두 시청자에게서 → 둘 다 불명확
    (3, 70_000, "고양이 키우세요?"),
    (4, 70_500, "고양이 키우세요??"),
    # 예시 3: 이미 그 주제를 말하던 중 → 관련성만
    (5, 110_000, "라면 뭐 좋아해요?"),
    # 시간 근접만 → 반응 미확인
    (6, 150_000, "오늘 날씨 좋네요"),
    # 후원은 판정 안 함
    (7, 151_000, "후원합니다"),
]
SCENARIO_SEGS = [
    (13_000, 16_000, "시청자1님이 여행 가면 꼭 하는 거 있냐고 물어보셨는데"),
    (48_000, 51_000, "캠핑 가면 저는 무조건 랜턴부터 챙겨요"),
    (73_000, 75_000, "고양이 키우냐면 두 마리 키워요"),
    (80_000, 99_000, "라면은 매운 라면이 최고죠 라면 이야기 계속"),
    (114_000, 117_000, "라면 저는 짜장 라면 좋아해요"),
    (155_000, 157_000, "자 이제 게임 시작합니다"),
]


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "합성 프로젝트-0"
    (p / "chat" / "protected").mkdir(parents=True)
    (p / "derived").mkdir()
    msgs = []
    for n, t, txt in SCENARIO_MSGS:
        if n == 7:
            msgs.append(_msg(n, t, txt, kind="donation", type_code=10))
        else:
            msgs.append(_msg(n, t, txt))
    with open(p / "chat" / "messages.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    with open(p / "chat" / "protected" / "identities.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for n in range(1, 8):
            f.write(json.dumps({"viewer_id": vid(n), "user_id_hash": uid(n),
                                "nicknames": [{"nickname": f"시청자{n}", "first_seen_ms": 0}]},
                               ensure_ascii=False) + "\n")
    with open(p / "chat" / "fetch-F20261020-test.json", "w", encoding="utf-8") as f:
        json.dump({"fetch_id": "F20261020-test", "video_no": "0"}, f)
    with open(p / "derived" / "asr.json", "w", encoding="utf-8") as f:
        json.dump(_asr(SCENARIO_SEGS), f, ensure_ascii=False)
    return str(p)


def _by_text(doc_or_reactions):
    rs = doc_or_reactions["reactions"] if isinstance(doc_or_reactions, dict) else doc_or_reactions
    ids_by_t = {r["chat_t_ms"]: r for r in rs}
    return {t: ids_by_t.get(t) for _, t, _ in SCENARIO_MSGS}


def test_link_golden_scenario(project, tmp_path):
    out = tmp_path / "out" / "link.json"
    csvp = tmp_path / "out" / "review.csv"
    doc = L.run(project, str(out), review_csv=str(csvp))
    assert L.validate_result(doc) == []
    r = _by_text(doc)
    assert r[10_000]["grade"] == "direct" and r[10_000]["confidence"] == "certain"
    lc = r[10_000]["candidates"][r[10_000]["linked_candidate"]]
    assert lc["evidence"]["nickname_called"]["present"] and lc["evidence"]["text_repeated"]["present"]
    assert r[10_000]["reaction_time_ms"] == 13_000
    assert r[40_000]["grade"] == "likely" and r[40_000]["no_similar_chat_nearby"]
    assert r[70_000]["grade"] == "unclear" and r[70_500]["grade"] == "unclear"
    assert r[70_000]["unclear_cause"] == "multiple_similar_chats"
    assert r[70_500]["chat_id"] in r[70_000]["similar_chat_ids"]
    assert r[110_000]["grade"] == "related_only" and r[110_000]["streamer_already_on_topic"]
    assert r[150_000]["result"] == "unconfirmed" and r[150_000]["grade"] is None
    assert r[150_000]["window_utterances"] == 1 and r[150_000]["candidates"] == []
    assert r[151_000] is None                                   # 후원 제외
    assert doc["stats"]["skipped_non_text"] == {"donation": 1}
    assert doc["calibration"]["needs_anchor_calibration"] is True
    assert doc["calibration"]["source"] == "default" and doc["calibration"]["offset_ms"] == 0
    assert doc["inputs"]["identities_used"] is True
    assert doc["target"]["video_no"] == "0"
    # 저장된 파일 = 돌려준 문서, 그리고 닉네임·채팅 본문·전사문이 없다
    raw = out.read_text(encoding="utf-8")
    assert json.loads(raw)["run_id"] == doc["run_id"]
    for n in range(1, 8):
        assert f"시청자{n}" not in raw
    for _, _, txt in SCENARIO_MSGS:
        assert txt not in raw
    for _, _, txt in SCENARIO_SEGS:
        assert txt not in raw
    # 검토 CSV: 등급 순, 본문 없음
    with open(csvp, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [x["grade"] for x in rows] == ["direct", "likely", "unclear", "unclear", "related_only"]
    assert rows[0]["evidence"].startswith("nickname_called;text_repeated")
    assert "시청자" not in csvp.read_text(encoding="utf-8-sig")


def test_without_identities_nickname_unknown(project, tmp_path):
    doc = L.run(project, str(tmp_path / "o.json"), use_identities=False)
    r = _by_text(doc)
    c = r[10_000]["candidates"][0]
    assert c["evidence"]["nickname_called"]["present"] is None
    assert r[10_000]["grade"] == "direct"                       # 되풀이·직접 지칭이 남아 있다
    assert doc["inputs"]["identities_used"] is False
    assert doc["stats"]["nickname_unknown"] == doc["stats"]["chats_in_scope"]


def test_offset_moves_window(project, tmp_path):
    # 영상이 VOD 보다 30초 늦게 시작(오프셋 +30s 처럼 보이게): 발화가 창 밖으로 밀려 연결되지 않는다
    doc = L.run(project, str(tmp_path / "o.json"), offset_ms=30_000)
    r = _by_text(doc)
    assert r[10_000]["grade"] != "direct"
    assert doc["calibration"]["source"] == "cli"
    # 클립 시작 + 오프셋 상쇄: 결과가 기본과 같다
    a = L.run(project, str(tmp_path / "a.json"))
    b = L.run(project, str(tmp_path / "b.json"), offset_ms=5000, file_start_ms=5000)
    assert [x["grade"] for x in a["reactions"]] == [x["grade"] for x in b["reactions"]]
    assert [x["reaction_time_ms"] for x in a["reactions"]] == [x["reaction_time_ms"] for x in b["reactions"]]


def test_scope_and_truncation(project, tmp_path):
    doc = L.run(project, str(tmp_path / "o.json"), start_ms=0, end_ms=60_000)
    assert {r["chat_t_ms"] for r in doc["reactions"]} == {10_000, 40_000}
    inp = L.load_project(project)
    inp.asr_meta["duration_ms"] = 45_000
    _, st = L.link(inp, start_ms=0, end_ms=60_000)
    assert st["window_truncated_by_audio_end"] == 1


def test_v1_subset(project, tmp_path):
    doc = L.run(project, str(tmp_path / "o.json"), v1_out=str(tmp_path / "v1.json"))
    v1 = json.loads((tmp_path / "v1.json").read_text(encoding="utf-8"))
    assert v1["schema_version"] == 1
    with open(os.path.join(ROOT, "schemas", "ai-result.schema.json"), encoding="utf-8") as f:
        sch = json.load(f)
    assert set(v1) <= set(sch["properties"])
    allowed = set(sch["properties"]["reactions"]["items"]["properties"])
    for r in v1["reactions"]:
        assert set(r) <= allowed
    assert len(v1["reactions"]) == len(doc["reactions"])
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return
    jsonschema.validate(v1, sch)


def test_project_json_calibration_used(project, tmp_path):
    cal = {"active_rev": 1, "revs": [
        {"rev": 0, "offset_ms": 0, "status": "unverified", "method": "single_offset", "anchors": []},
        {"rev": 1, "offset_ms": 2000, "status": "verified", "method": "single_offset", "anchors": []}]}
    with open(os.path.join(project, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"broadcast": {"video_no": "0"}, "calibration": cal}, f)
    doc = L.run(project, str(tmp_path / "o.json"))
    assert doc["calibration"]["source"] == "project.json"
    assert doc["calibration"]["offset_ms"] == 2000
    assert doc["calibration"]["needs_anchor_calibration"] is False


def test_validate_result_catches_problems(project, tmp_path):
    doc = L.run(project, str(tmp_path / "o.json"))
    bad = json.loads(json.dumps(doc))
    r = next(x for x in bad["reactions"] if x["grade"] == "direct")
    r["candidates"][r["linked_candidate"]]["strong"] = 0
    assert any("강한 증거" in p for p in L.validate_result(bad))
    bad = json.loads(json.dumps(doc))
    u = next(x for x in bad["reactions"] if x["result"] == "unconfirmed")
    u["grade"] = "likely"
    assert L.validate_result(bad)


def test_config_hash_stable():
    a, b = L.LinkConfig(), L.LinkConfig()
    assert a.sha256() == b.sha256() and len(a.sha256()) == 64
    assert L.LinkConfig(similar_min=0.6).sha256() != a.sha256()
    assert a.similar_window_ms == 20000


def test_pick_and_confidence_unit():
    from app.core import rules
    gc = rules.grade_conditions([])
    assert L._pick_grade(gc) is None and L._confidence(None, gc) == (None, None)


def test_link_pure_inputs():
    chats = [L.Chat("c" + "0" * 20, 1000, vid(1), "제주도 가보셨어요?")]
    utts = [L.Utterance(0, 3000, 5000, "제주도는 작년에 다녀왔어요")]
    inp = L.Inputs(chats=chats, utterances=utts, nicknames={vid(1): ["시청자1"]}, calibration=Calibration())
    rs, st = L.link(inp)
    assert rs[0]["grade"] == "likely"
    assert st["chats_in_scope"] == 1 and st["candidates_total"] == 1


def test_cli(project, tmp_path):
    out = tmp_path / "cli.json"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "engine.link", "--project", project, "--out", str(out),
                        "--start-ms", "0", "--end-ms", "200000"], cwd=ROOT, capture_output=True, env=env)
    assert p.returncode == 0, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    s = json.loads(p.stdout.decode("utf-8"))
    assert s["stats"]["chats_in_scope"] == 6
    assert "시청자" not in p.stdout.decode("utf-8")
    p = subprocess.run([sys.executable, "-m", "engine.link", "--project", str(tmp_path / "없음"), "--out",
                        str(out)], cwd=ROOT, capture_output=True, env=env)
    assert p.returncode == 1


def test_direct_reference_needs_content_link():
    cfg = L.LinkConfig()
    ch = L.Chat("c" + "1" * 20, 1000, vid(1), "감사합니다 오늘도")
    u = L.Utterance(0, 2000, 3000, "아까 누구님이 감사합니다 하셨는데")
    ev = L.evidence_for(ch, u, 1000, nicknames=[], prior_texts=[], cfg=cfg, utt_vod_ms=2000)
    assert ev["direct_reference"]["present"] is False          # 공유 내용어가 없다(감사합니다는 불용어)
    ch2 = L.Chat("c" + "2" * 20, 1000, vid(2), "제주도 가보셨어요?")
    u2 = L.Utterance(1, 2000, 3000, "방금 제주도 질문 주셨는데")
    ev2 = L.evidence_for(ch2, u2, 1000, nicknames=[], prior_texts=[], cfg=cfg, utt_vod_ms=2000)
    assert ev2["direct_reference"]["present"] is True and ev2["direct_reference"]["at_ms"] == 2000


# ── 검토 수정 재현 (2026-10-20) ──────────────────────────────────────────

def _write_cal(project, cal_json):
    with open(os.path.join(project, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"broadcast": {"video_no": "0"}, "calibration": {"active_rev": 1, "revs": [cal_json]}}, f)


def test_file_start_only_does_not_subtract_twice(project):
    """calibrate 가 클립 시작을 합쳐 저장한 보정 + --file-start-ms 만 주면 클립 시작을 한 번만 뺀다."""
    from dataclasses import replace
    from app.core import timecode as tc
    cal = replace(Calibration(offset_ms=900), rev=1, status="verified", created_at="2026-10-20T00:00:00+09:00",
                  created_by="T")
    obj = tc.calibration_to_json(cal, file_start_in_vod_ms=300_000)
    assert obj["offset_ms"] == 900 - 300_000
    _write_cal(project, obj)
    inp = L.load_project(project, file_start_ms=300_000)
    assert tc.chat_to_video_ms(400_000, inp.calibration, inp.file_start_in_vod_ms) == 100_900
    assert tc.chat_to_video_ms(310_000, inp.calibration, inp.file_start_in_vod_ms) == 10_900
    assert inp.calibration.status == "verified" and inp.calibration_source == "project.json+cli_file_start"
    # 클립 시작 없이 읽은 것과 같은 변환
    inp0 = L.load_project(project)
    assert tc.chat_to_video_ms(400_000, inp0.calibration, inp0.file_start_in_vod_ms) == 100_900
    # --offset-ms 를 함께 주면 명령줄 값이 이긴다(합친 값이 아닌 원래 오프셋)
    inp2 = L.load_project(project, offset_ms=900, file_start_ms=300_000)
    assert tc.chat_to_video_ms(400_000, inp2.calibration, inp2.file_start_in_vod_ms) == 100_900
    assert inp2.calibration_source == "cli"


def _ev(*keys):
    return {"evidence": {k: True for k in keys}}


def test_already_on_topic_with_similar_chats_is_related_only():
    """안내서 4-4 예시 3: 이미 그 주제 + 비슷한 채팅 여럿 → 관련성만(증거가 많아도 '불명확' 으로 뒤집히지 않는다)."""
    from app.core import rules
    many = rules.grade_conditions([_ev("answer_form", "within_time_window", "same_topic")],
                                  no_similar_chat_nearby=False, streamer_already_on_topic=True)
    few = rules.grade_conditions([_ev("same_topic", "within_time_window")],
                                 no_similar_chat_nearby=False, streamer_already_on_topic=True)
    assert L._pick_grade(many) == "related_only"
    assert L._pick_grade(few) == "related_only"
    # 이미 그 주제가 아니면 비슷한 채팅 때문에 불명확(예시 4 취지는 그대로)
    sim = rules.grade_conditions([_ev("answer_form", "within_time_window", "same_topic")],
                                 no_similar_chat_nearby=False, streamer_already_on_topic=False)
    assert L._pick_grade(sim) == "unclear"


def test_two_direct_different_scores_reaction_is_first():
    """직접 후보 둘의 점수가 달라도 반응 시각은 첫 직접 후보(안내서 4-3 '처음 반응한 시각')."""
    from app.core import rules
    gc = rules.grade_conditions([_ev("direct_reference", "within_time_window", "same_topic"),
                                 _ev("nickname_called", "text_repeated", "within_time_window", "same_topic")],
                                no_similar_chat_nearby=True, streamer_already_on_topic=False)
    assert L._pick_grade(gc) == "direct"
    assert gc.best().index == 1
    assert L._reaction_candidate("direct", gc).index == 0
    assert L._reaction_candidate("likely", gc).index == 1


def test_similar_window_marked_as_engine_assumption(project, tmp_path):
    doc = L.run(project, str(tmp_path / "o.json"))
    assert doc["assumptions"] == {"similar_chat_window_ms": 20000, "similar_chat_window_source": "engine_default"}
    assert "assumptions" not in L.to_v1(doc)
