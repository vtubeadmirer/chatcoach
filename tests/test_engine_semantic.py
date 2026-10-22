"""engine/semantic.py · engine/link.py v0.3 — 합성 데이터만 쓴다(채팅·발화·닉네임 모두 지어낸 것).

모델 없이 도는 가짜 임베딩(FakeEmbedder)으로 시험한다. 실제 모델(e5-small) 시험은 엔진 venv 에 모델이 있을 때만 돈다.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import ids, rules  # noqa: E402
from app.core.timecode import Calibration  # noqa: E402
from engine import link as L  # noqa: E402
from engine import semantic as S  # noqa: E402

# 합성 개념표: 글자는 다르지만 뜻이 같은 낱말들
CONCEPTS = {"반려동물": "pet", "냥이": "pet", "고양이": "pet", "강아지": "pet", "멍멍이": "pet",
            "저녁": "meal", "치킨": "meal", "야식": "meal", "몇 시": "time", "열한 시": "time", "끄려고": "time"}


def fake():
    return S.FakeEmbedder(CONCEPTS)


def scorer(cfg=S.SemanticConfig()):
    return S.SemanticScorer(fake(), cfg)


def vid(n):
    return "vw_" + "abcdefghijk" + "abcdefghijklmnopqrstuvwxyz234567"[n % 32]


def uid(n):
    import hashlib
    return hashlib.sha1(f"fixture-{n}".encode()).hexdigest()


def chat(n, t, text):
    return L.Chat(ids.chat_id_base("0", 1_700_000_000_000 + t, uid(n), 1, text), t, vid(n), text)


FILLER = ["자 이제 다음 판 가볼게요", "아 이거 왜 안 되지", "잠깐 물 좀 마시고 올게요", "이 보스 패턴이 진짜 어렵거든요",
          "음악 소리 좀 줄일게요", "이 맵은 처음 해보는 거라서", "아 또 죽었네 진짜", "오늘 컨디션이 좀 별로네"]


def utts_with(answer_at_ms, answer_text, *, start=0, step=7000, n=8):
    """맥락 발화(FILLER) 사이에 대답 하나를 끼운다."""
    us = [L.Utterance(i, start + i * step, start + i * step + 2000, FILLER[i % len(FILLER)]) for i in range(n)]
    us.append(L.Utterance(99, answer_at_ms, answer_at_ms + 2500, answer_text))
    us.sort(key=lambda u: u.start_ms)
    return [L.Utterance(i, u.start_ms, u.end_ms, u.text) for i, u in enumerate(us)]


def inputs(chats, utts):
    return L.Inputs(chats=chats, utterances=utts, nicknames={c.viewer_id: [f"시청자{i}"] for i, c in enumerate(chats)},
                    calibration=Calibration())


# ── 가짜 임베딩 ───────────────────────────────────────────────────────────

def test_fake_embedder_shape_and_ranges():
    e = fake()
    a, b, c, d = e.encode(["반려동물 키우세요?", "집에 냥이 두 마리 있어요", "아 또 죽었네 진짜", "반려동물 키우세요?"])
    assert a == d                                            # 결정적
    assert abs(S.cosine(a, a) - 1.0) < 1e-9
    assert S.cosine(a, b) > 0.9                              # 글자는 다르고 뜻(개념)이 같다
    assert 0.75 < S.cosine(a, c) < 0.85                      # 관계없는 문장도 base 근처(e5 와 비슷한 분포)
    assert e.info()["backend"] == "fake"


def test_index_caches_texts():
    e = fake()
    idx = S.SemanticIndex(e)
    idx.prefetch(["가나다라", "마바사아", "가나다라"])
    n = e.calls
    idx.sim("가나다라", "마바사아")
    assert e.calls == n


# ── 점수 → 기존 증거 키 매핑 ──────────────────────────────────────────────

def test_score_chat_answer_only_for_closest_non_question():
    sc = scorer()
    cands = ["아 또 죽었네 진짜", "집에 냥이 두 마리 있어요 둘 다 까칠해요", "음악 소리 좀 줄일게요"]
    rows = sc.score_chat("반려동물 키우세요?", cands, cands, FILLER + cands, FILLER[:4])
    assert [r.answer for r in rows] == [False, True, False]
    assert rows[1].top and rows[1].rel > 0.05
    assert rows[1].mapped() == {"answer_form": True, "unique_content": True, "same_topic": True}
    # 발화가 되묻기(질문)면 답으로 보지 않는다
    q = ["냥이 키우냐고요?"]
    assert sc.score_chat("반려동물 키우세요?", q, q, FILLER + q, [])[0].answer is False


def test_score_chat_statement_content_needs_new_meaning():
    sc = scorer()
    cands = ["멍멍이는 산책을 좋아하죠"]
    rows = sc.score_chat("우리 강아지 오늘 생일이에요", cands, cands, FILLER + cands, FILLER[:4])
    assert rows[0].answer is False                      # 질문이 아니다
    assert rows[0].content is True                      # 채팅 뜻이 이 발화에서 처음 나옴
    # 스트리머가 채팅 전에 이미 같은 뜻을 말했으면 '처음 나옴' 이 아니다
    prior = FILLER[:3] + ["고양이 키우는 분들 많네요"]
    rows = sc.score_chat("우리 강아지 오늘 생일이에요", cands, cands, prior + cands, prior)
    assert rows[0].content is False


def test_score_chat_skips_low_content_and_short():
    sc = scorer()
    rows = sc.score_chat("ㅋㅋㅋㅋ", ["냥이 귀엽죠"], ["냥이 귀엽죠"], FILLER, [])
    assert rows == [S.SKIPPED]
    rows = sc.score_chat("반려동물 키우세요?", ["네"], ["네"], FILLER, [])
    assert rows[0].sim is None                           # 너무 짧은 발화
    assert sc.score_chat("반려동물 키우세요?", [], [], FILLER, []) == []


def test_semantic_config_hash():
    a, b = S.SemanticConfig(), S.SemanticConfig()
    assert a.sha256() == b.sha256() and len(a.sha256()) == 64
    assert S.SemanticConfig(answer_min_sim=0.9).sha256() != a.sha256()


# ── link v0.3 ────────────────────────────────────────────────────────────

def test_meaning_only_answer_v02_misses_v03_likely():
    ch = chat(1, 20_000, "반려동물 키우세요?")
    utts = utts_with(27_000, "집에 냥이 두 마리 있어요 둘 다 까칠해요")
    inp = inputs([ch], utts)
    r2, _ = L.link(inp)
    assert r2[0]["result"] == "unconfirmed"                  # 같은 글자가 없다(v0 범위 밖, 측정 문서 3장)
    r3, st = L.link(inp, semantic=scorer())
    r = r3[0]
    assert r["grade"] == "likely" and r["reaction_time_ms"] == 27_000
    lc = r["candidates"][r["linked_candidate"]]
    assert lc["evidence"]["answer_form"]["present"] is True
    assert "answer_form" in lc["semantic_only"]
    assert lc["semantic"]["semantic_answer"] is True and lc["semantic"]["sim"] > 0.85
    assert set(lc["evidence"]) == set(rules.EVIDENCE_KEYS)  # 증거 키 7종 그대로
    assert st["semantic"]["candidates_added"] == 1 and st["semantic"]["mapped_only"]["answer_form"] == 1
    assert r["rationale"].endswith("의미 비교")


def test_semantics_never_make_direct():
    ch = chat(1, 20_000, "반려동물 키우세요?")
    inp = inputs([ch], utts_with(27_000, "집에 냥이 두 마리 있어요 둘 다 까칠해요"))
    r, _ = L.link(inp, semantic=scorer(S.SemanticConfig(answer_min_sim=0.0, content_min_sim=0.0,
                                                         topic_min_sim=0.0)))
    assert r[0]["grade"] != "direct"
    for c in r[0]["candidates"]:
        assert c["strong"] == 0


def test_similar_chat_still_blocks_likely():
    c1 = chat(1, 20_000, "반려동물 키우세요?")
    c2 = chat(2, 21_000, "반려동물 키우세요??")
    r, _ = L.link(inputs([c1, c2], utts_with(27_000, "집에 냥이 두 마리 있어요 둘 다 까칠해요")), semantic=scorer())
    assert [x["grade"] for x in r] == ["unclear", "unclear"]  # 어느 채팅인지는 뜻으로 가르지 않는다(예시 4)


def test_tie_between_likely_candidates_resolved_by_meaning():
    ch = chat(1, 20_000, "저녁 뭐 드셨어요?")
    us = [L.Utterance(0, 23_000, 25_000, "네 저는 치킨 반 마리 먹었어요"),
          L.Utterance(1, 31_000, 33_000, "네 그건 이따가 다시 해볼게요")]
    inp = inputs([ch], us)
    r2, _ = L.link(inp)
    assert r2[0]["grade"] == "unclear" and r2[0]["unclear_cause"] == "multiple_utterances"
    r3, st = L.link(inp, semantic=scorer())
    assert r3[0]["grade"] == "likely"
    assert r3[0]["reaction_time_ms"] == 23_000
    assert any(x.startswith("semantic_") for x in r3[0]["reasons"])


def test_graded_without_grade_becomes_unconfirmed_in_v03():
    es = dict(rules.V0_1_STRENGTH, same_topic="gate")      # 같은 주제를 관문으로 → 그 증거만 있는 후보는 등급이 없다
    cfg = L.LinkConfig(rule=rules.RuleConfig(evidence_strength=es))
    ch = chat(1, 20_000, "라면 끓이는 중")
    us = [L.Utterance(0, 5_000, 7_000, "라면 이야기 나와서 말인데"),          # 채팅 앞: 고유 내용이 아니게
          L.Utterance(1, 25_000, 27_000, "라면은 역시 밤에 먹어야")]
    inp = inputs([ch], us)
    r2, _ = L.link(inp, cfg)
    assert r2[0]["result"] == "graded" and r2[0]["grade"] is None          # v0.2 는 그대로
    sc = scorer(S.SemanticConfig(answer_min_sim=2, content_min_sim=2, topic_min_sim=2))   # 의미 매핑은 끔
    r3, st = L.link(inp, cfg, semantic=sc)
    assert r3[0]["result"] == "unconfirmed" and r3[0]["grade"] is None and r3[0]["candidates"] == []
    assert "no_grade_to_unconfirmed" in r3[0]["reasons"]
    assert st["semantic"]["graded_none_to_unconfirmed"] == 1


def test_v02_unchanged_when_semantic_none():
    ch = chat(1, 20_000, "반려동물 키우세요?")
    inp = inputs([ch], utts_with(27_000, "집에 냥이 두 마리 있어요"))
    a, sa = L.link(inp)
    b, sb = L.link(inp, semantic=None)
    assert a == b and "semantic" not in sa
    assert "semantic" not in json.dumps(a)


# ── 결과 문서 · 물러남 ────────────────────────────────────────────────────

def _project(tmp_path, msgs, segs):
    p = tmp_path / "합성-sem"
    (p / "chat").mkdir(parents=True)
    (p / "derived").mkdir()
    with open(p / "chat" / "messages.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for n, t, text in msgs:
            c = chat(n, t, text)
            f.write(json.dumps({"chat_id": c.chat_id, "t_ms": t, "server_time_ms": 1_700_000_000_000 + t,
                                "type_code": 1, "kind": "text", "status": "NORMAL", "viewer_id": vid(n),
                                "is_anonymous": False, "nickname_masked": "시*", "role": "viewer", "text": text,
                                "raw_ref": {"fetch_id": "F20261022-test", "page": 0, "index": 0}},
                               ensure_ascii=False) + "\n")
    with open(p / "chat" / "fetch-F20261022-test.json", "w", encoding="utf-8") as f:
        json.dump({"fetch_id": "F20261022-test", "video_no": "0"}, f)
    with open(p / "derived" / "asr.json", "w", encoding="utf-8") as f:
        json.dump({"format": "chatcoach.asr", "format_version": 1, "model": {"name": "large-v3-turbo"},
                   "settings": {"vad": True}, "audio": {"duration_ms": 120_000},
                   "segments": [{"id": i, "start_ms": u.start_ms, "end_ms": u.end_ms, "text": u.text, "words": []}
                                for i, u in enumerate(segs)]}, f, ensure_ascii=False)
    return str(p)


MSGS = [(1, 20_000, "반려동물 키우세요?"), (2, 70_000, "오늘 날씨 좋네요")]
ANSWER = "집에 냥이 두 마리 있어요 둘 다 까칠해요"


def test_doc_v03_records_model_and_version(tmp_path):
    proj = _project(tmp_path, MSGS, utts_with(27_000, ANSWER))
    out = tmp_path / "o.json"
    doc = L.run(proj, str(out), engine="v0.3", scorer=scorer(), use_identities=False)
    assert L.validate_result(doc) == []
    assert doc["engine"]["version"] == "link-v0.3"
    assert doc["run_id"].startswith("link-v0.3-")
    sem = doc["semantic"]
    assert sem["active"] is True and sem["status"] == "ok" and sem["fallback"] is None
    assert sem["model"]["name"] == "fake-embedder" and sem["config_sha256"] == S.SemanticConfig().sha256()
    assert "semantic-v0.3" in doc["engine"]["components"]["other"]
    assert len(doc["engine"]["components"]["other"]) <= 200
    v02 = L.run(proj, str(tmp_path / "o2.json"), use_identities=False)
    assert doc["engine"]["config_sha256"] != v02["engine"]["config_sha256"]
    assert v02["engine"]["version"] == L.ENGINE_VERSION and "semantic" not in v02
    # 1판 부분도 그대로 뽑힌다
    v1 = L.to_v1(doc)
    assert v1["engine"]["version"] == "link-v0.3" and len(v1["engine"]["version"]) <= 40
    # 본문·전사문이 결과에 없다
    raw = out.read_text(encoding="utf-8")
    for _, _, t in MSGS:
        assert t not in raw
    assert ANSWER not in raw
    for t in FILLER:
        assert t not in raw


def test_fallback_to_v02_when_model_missing(tmp_path):
    proj = _project(tmp_path, MSGS, utts_with(27_000, ANSWER))
    empty = tmp_path / "models"
    empty.mkdir()
    doc = L.run(proj, str(tmp_path / "o.json"), engine="v0.3", models_dir=str(empty), use_identities=False)
    v02 = L.run(proj, str(tmp_path / "o2.json"), use_identities=False)
    assert L.validate_result(doc) == []
    assert doc["engine"]["version"] == L.ENGINE_VERSION
    assert doc["semantic"]["active"] is False and doc["semantic"]["status"] == "model_missing"
    assert doc["semantic"]["fallback"] == "link-v0.2"
    assert "물러남" in doc["engine"]["components"]["other"]
    assert doc["reactions"] == v02["reactions"]
    assert doc["engine"]["config_sha256"] == v02["engine"]["config_sha256"]


def test_hash_mismatch_is_refused(tmp_path):
    d = tmp_path / "models" / S.E5_SMALL["dir"] / "onnx"
    d.mkdir(parents=True)
    (d / "model.onnx").write_bytes(b"not a model")
    (d / "tokenizer.json").write_text("{}", encoding="utf-8")
    emb, st = S.load_default(str(tmp_path / "models"))
    assert emb is None and st["status"] == "hash_mismatch" and st["fallback"] == "link-v0.2"
    assert len(st["model"]["model_sha256"]) == 64
    assert (d / S.HASH_CACHE_NAME).is_file()             # 해시 캐시


def test_backend_off_and_unknown():
    emb, st = S.load_default(backend="off")
    assert emb is None and st["status"] == "disabled"
    with pytest.raises(ValueError):
        S.load_default(backend="cloud")


def test_validate_catches_v03_without_semantic(tmp_path):
    proj = _project(tmp_path, MSGS, utts_with(27_000, ANSWER))
    doc = L.run(proj, str(tmp_path / "o.json"), engine="v0.3", scorer=scorer(), use_identities=False)
    bad = dict(doc, semantic=dict(doc["semantic"], active=False))
    assert any("semantic" in p for p in L.validate_result(bad))
    bad2 = json.loads(json.dumps(doc))
    bad2["reactions"][1].update(result="graded", grade=None, candidates=[])
    assert any("D-17 1" in p for p in L.validate_result(bad2))


def test_build_doc_rejects_unknown_engine():
    with pytest.raises(ValueError):
        L.build_doc(inputs([], []), engine="v9")


# ── LM Studio: localhost 만, 꺼져 있으면 쓰지 않음 ──────────────────────

def test_lmstudio_local_only_and_off():
    with pytest.raises(ValueError):
        S.probe_lmstudio("http://example.com:1234")
    with pytest.raises(ValueError):
        S.LmStudioEmbedder("https://api.example.com", "m")
    # 닫힌 포트(이 PC) → 꺼짐
    assert S.probe_lmstudio("http://127.0.0.1:9", timeout=0.3) == {"running": False, "embedding_models": []}
    emb, st = S.load_default(backend="lmstudio", lmstudio_url="http://127.0.0.1:9")
    assert emb is None and st["status"] == "backend_unavailable"
    emb, st = S.load_default(backend="lmstudio", lmstudio_url="http://example.com")
    assert emb is None and st["status"] == "backend_unavailable"


def test_describe_short():
    assert "물러남" in S.describe(S.status_doc("model_missing"))
    assert S.describe(None).startswith("features-v0.2")


# ── CLI ──────────────────────────────────────────────────────────────────

def test_cli_engine_flags(tmp_path):
    proj = _project(tmp_path, MSGS, utts_with(27_000, ANSWER))
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    empty = tmp_path / "models"
    empty.mkdir()
    p = subprocess.run([sys.executable, "-m", "engine", "link", "--project", proj, "--out", str(tmp_path / "c.json"),
                        "--models-dir", str(empty), "--no-identities"], cwd=ROOT, capture_output=True, env=env)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    s = json.loads(p.stdout.decode("utf-8"))
    assert s["semantic"]["status"] == "model_missing" and s["engine_version"] == L.ENGINE_VERSION
    assert ANSWER not in p.stdout.decode("utf-8")
    p = subprocess.run([sys.executable, "-m", "engine", "link", "--project", proj, "--out", str(tmp_path / "d.json"),
                        "--engine", "v0.2"], cwd=ROOT, capture_output=True, env=env)
    s = json.loads(p.stdout.decode("utf-8"))
    assert p.returncode == 0 and s["semantic"] is None
    p = subprocess.run([sys.executable, "-m", "engine", "semantic-info", "--models-dir", str(empty)],
                       cwd=ROOT, capture_output=True, env=env)
    assert p.returncode == 0
    info = json.loads(p.stdout.decode("utf-8"))
    assert info["status"] == "model_missing" and info["lmstudio"] is None


# ── 실제 모델(있을 때만) ──────────────────────────────────────────────────

def _real_model():
    emb, st = S.load_default()
    return emb


@pytest.mark.skipif(_real_model() is None, reason="e5-small 모델 또는 onnxruntime 없음(엔진 venv 에서만 돈다)")
def test_real_e5_meaning_only_answer():
    emb = _real_model()
    sc = S.SemanticScorer(emb)
    assert sc.status["model"]["model_sha256"] == S.E5_SMALL["model_sha256"]
    cands = ["아 또 죽었네 진짜", "집에 고양이 두 마리 있는데 둘 다 엄청 까칠해요", "음악 소리 좀 줄일게요"]
    rows = sc.score_chat("반려동물 키우세요?", cands, cands, FILLER + cands, FILLER[:4])
    assert max(range(3), key=lambda i: rows[i].sim) == 1
    assert rows[1].answer is True
