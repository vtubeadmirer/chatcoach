"""engine/topic_vectors.py · schemas/topic-vectors.schema.json · app/core/viewer_score 주제 축의 선택 입력
(창별 벡터·동의어·2자 머리) · tools/engine_to_ai --vectors — 합성 데이터만 쓴다(전사·채팅·낱말 모두 지어낸 것).

모델 없이 가짜 임베딩(engine.semantic.FakeEmbedder 또는 손으로 만든 벡터)으로 시험한다. 실제 e5-small 은 쓰지 않는다.
"""
import hashlib
import json
import math
import os
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from app.core import ids
from app.core import viewer_score as VS
from app.core.timecode import Calibration
from engine import semantic as S
from engine import topic_vectors as TV

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 합성 개념표(가짜 임베딩이 글자는 달라도 같은 개념을 가깝게 한다)
CONCEPTS = {"용암": "lava", "불바다": "lava", "보스": "boss", "두목": "boss"}


def fake():
    return S.FakeEmbedder(CONCEPTS, dim=64)


def seg(a, b, text, words=None):
    s = {"start_ms": a, "end_ms": b, "text": text}
    if words is not None:
        s["words"] = [{"start_ms": x, "end_ms": y, "word": w} for x, y, w in words]
    return s


SALT = b"" * 32


def _msg_rec(n, t_ms, text):
    uid = hashlib.sha1(f"fixture-{n}".encode()).hexdigest()
    return {"chat_id": ids.chat_id_base("0", 1_700_000_000_000 + t_ms, uid, 1, text), "t_ms": t_ms,
            "server_time_ms": 1_700_000_000_000 + t_ms, "type_code": 1, "kind": "text", "status": "NORMAL",
            "viewer_id": ids.viewer_id(SALT, uid), "is_anonymous": False, "nickname_masked": "시*", "role": "viewer",
            "text": text, "raw_ref": {"fetch_id": "F20261023-test", "page": 0, "index": 0}}


ASR_SEGMENTS = [
    seg(12000, 14000, "용암 지대를 건너가요", [(12000, 12200, " 용암"), (12300, 12900, " 지대를"), (13300, 14000, " 건너가요")]),
    seg(25000, 37000, "보스 체력이 절반 남았어요"),
    seg(52000, 52400, "음"),                                      # 너무 짧은 창(건너뜀)
]
ASR_WORDS = ("용암", "지대를", "건너가요", "보스", "체력이", "절반", "남았어요")


def make_project(root, name="합성 프로젝트-벡터", offset_ms=2500):
    p = os.path.join(str(root), name)
    os.makedirs(os.path.join(p, "chat"))
    os.makedirs(os.path.join(p, "derived"))
    with open(os.path.join(p, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": "p-synth-vec", "broadcast": {"video_no": "0"},
                   "calibration": {"active_rev": 1, "revs": [
                       {"rev": 0, "offset_ms": 0, "status": "unverified"},
                       {"rev": 1, "offset_ms": offset_ms, "method": "single_offset", "status": "verified"}]}}, f)
    with open(os.path.join(p, "chat", "messages.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for i, (t, text) in enumerate([(9000, "용암 뜨거워요?"), (21000, "보스 언제 잡아요 {:s_9:}")]):
            f.write(json.dumps(_msg_rec(1 + i, t, text), ensure_ascii=False) + "\n")
    with open(os.path.join(p, "chat", "fetch-F20261023-test.json"), "w", encoding="utf-8") as f:
        json.dump({"fetch_id": "F20261023-test", "video_no": "0"}, f)
    asr = {"format": "chatcoach.asr", "format_version": 1, "model": {"name": "synthetic"}, "settings": {"vad": True},
           "audio": {"duration_ms": 60000}, "segments": ASR_SEGMENTS}
    with open(os.path.join(p, "derived", "asr.json"), "w", encoding="utf-8") as f:
        json.dump(asr, f, ensure_ascii=False)
    return p


# ── 엔진: 창 글 · 문서 ──────────────────────────────────────────────────────────

def test_window_texts_word_times_and_spread():
    cal = Calibration(offset_ms=2500)
    texts, st = TV.window_texts(ASR_SEGMENTS, cal, 0)
    got = dict(texts)
    # 낱말 시각 → VOD(파일 − 2500): 9500·9800·10800 → 창 0 에 '용암 지대를', 창 1 에 '건너가요'
    assert got[0] == "용암 지대를" and got[1] == "건너가요"
    # 낱말 시각 없는 구간 22500~34500 → 창 2·3 에 같은 글
    assert got[2] == got[3] == "보스 체력이 절반 남았어요"
    assert 4 not in got and st["skipped_short"] == 1 and st["word_time_segments"] == 1
    # span 20초: 창 k 의 글은 창 k-1 과 k 의 글
    texts2, _ = TV.window_texts(ASR_SEGMENTS, cal, 0, cfg=TV.VectorsConfig(span_ms=20_000))
    assert dict(texts2)[1] == "용암 지대를 건너가요"
    with pytest.raises(ValueError):
        TV.VectorsConfig(span_ms=15_000)
    with pytest.raises(TV.VectorsError):
        TV.window_texts(ASR_SEGMENTS, cal, 0, start_ms=5000, end_ms=1000)


def test_from_project_doc_has_no_text_and_validates(tmp_path):
    p = make_project(tmp_path)
    doc = TV.from_project(p, embedder=fake())
    assert TV.validate(doc) == []
    assert [w["start_ms"] for w in doc["windows"]] == [0, 10000, 20000, 30000]
    assert all(w["end_ms"] == w["start_ms"] + 10000 for w in doc["windows"])
    assert doc["model"]["backend"] == "fake" and doc["model"]["name"] == "fake-embedder"
    assert doc["encoding"] == "f16le-b64" and doc["dim"] == len(VS.vectors_from_doc(doc)[0][2])
    with open(os.path.join(p, "derived", "asr.json"), "rb") as f:
        assert doc["target"]["asr_sha256"] == hashlib.sha256(f.read()).hexdigest()
    assert doc["calibration"]["offset_ms"] == 2500 and doc["calibration"]["status"] == "verified"
    raw = TV.dump(doc).decode("utf-8")
    for w in ASR_WORDS:                                  # 창 글·낱말은 문서에 없다
        assert w not in raw
    assert doc["vectors_sha256"] == TV.vectors_sha256(doc["windows"])


def test_vectors_roundtrip_close_to_embedding(tmp_path):
    p = make_project(tmp_path)
    emb = fake()
    texts, _ = TV.window_texts(ASR_SEGMENTS, Calibration(offset_ms=2500), 0)
    want = [tuple(x / math.sqrt(sum(y * y for y in v)) for x in v) for v in emb.encode([t for _k, t in texts])]
    for enc in ("f16le-b64", "round4"):
        doc = TV.from_project(p, embedder=emb, cfg=TV.VectorsConfig(encoding=enc))
        assert TV.validate(doc) == []
        got = VS.vectors_from_doc(doc)
        assert len(got) == len(want)
        for (_a, _b, v), w in zip(got, want):
            assert sum(x * y for x, y in zip(v, w)) > 0.999
    # 같은 입력 → 같은 벡터·해시(시계만 다름)
    d1 = TV.from_project(p, embedder=fake())
    d2 = TV.from_project(p, embedder=fake())
    assert d1["windows"] == d2["windows"] and d1["vectors_sha256"] == d2["vectors_sha256"]


def test_validate_catches_problems(tmp_path):
    doc = TV.from_project(make_project(tmp_path), embedder=fake())

    def probs(**ch):
        d = json.loads(json.dumps(doc))
        for k, v in ch.items():
            d[k] = v
        return TV.validate(d)

    assert probs(text="창 글")                                  # 모르는 칸(본문이 끼어들 자리)
    assert probs(vectors_sha256="0" * 64)
    assert probs(encoding="f32")
    assert probs(windows=list(reversed(doc["windows"])))        # 순서
    bad = json.loads(json.dumps(doc))
    bad["windows"][0]["v"] = bad["windows"][0]["v"][:-8]         # 길이가 틀린 벡터
    assert TV.validate(bad)
    bad = json.loads(json.dumps(doc))
    bad["windows"][0]["words"] = ["보스"]
    assert TV.validate(bad)
    assert TV.validate([]) and TV.validate({})


def test_schema_matches_module():
    with open(os.path.join(ROOT, "schemas", "topic-vectors.schema.json"), encoding="utf-8") as f:
        s = json.load(f)
    assert s["properties"]["schema"]["const"] == TV.SCHEMA and s["properties"]["schema_version"]["const"] == 1
    assert set(s["required"]) == set(TV.TOP_KEYS) and s["additionalProperties"] is False
    assert set(s["properties"]["model"]["required"]) == set(TV.MODEL_KEYS)
    assert set(s["properties"]["windows"]["items"]["required"]) == set(TV.WINDOW_KEYS)
    assert s["properties"]["encoding"]["enum"] == list(VS.VEC_ENCODINGS)


def test_schema_with_jsonschema_if_available(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    with open(os.path.join(ROOT, "schemas", "topic-vectors.schema.json"), encoding="utf-8") as f:
        s = json.load(f)
    for enc in ("f16le-b64", "round4"):
        doc = TV.from_project(make_project(tmp_path, name=f"합성-{enc}"), embedder=fake(),
                              cfg=TV.VectorsConfig(encoding=enc))
        jsonschema.validate(doc, s)


# ── 엔진: 시범 거부 · 명령줄 ────────────────────────────────────────────────────

def test_pilot_refused_before_reading(tmp_path, monkeypatch, capsys):
    p = make_project(tmp_path, name="pilot-합성")
    opened = []
    real_open = open

    def spy(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", spy)
    with pytest.raises(TV.VectorsError) as e:
        TV.from_project(p, embedder=fake())
    assert e.value.code == "pilot_locked"
    assert TV.main(["--project", p]) == 1
    other = make_project(tmp_path, name="일반-합성")
    pilot_asr = os.path.join(p, "derived", "asr.json")
    with pytest.raises(TV.VectorsError):
        TV.from_project(other, embedder=fake(), asr_path=pilot_asr)
    assert TV.main(["--project", other, "--asr", pilot_asr]) == 1
    assert TV.main(["--project", other, "--out", os.path.join(p, "derived", "v.json")]) == 1
    assert not [f for f in opened if "pilot-" in f]
    monkeypatch.undo()
    assert "pilot_locked" in capsys.readouterr().out
    assert not os.path.exists(os.path.join(p, "derived", "asr-topic-vectors.json"))
    # 봉인 경로(allow_pilot)는 읽는다
    assert TV.from_project(p, embedder=fake(), allow_pilot=True)["windows"]


def test_cli_default_out_numbers_only(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(TV, "load_embedder", lambda models_dir=None, backend="onnx": (fake(), {"status": "ok"}))
    p = make_project(tmp_path)
    assert TV.main(["--project", p]) == 0
    out = os.path.join(p, "derived", "asr-topic-vectors.json")
    printed = capsys.readouterr().out
    with open(out, encoding="utf-8") as f:
        doc = json.load(f)
    assert TV.validate(doc) == [] and doc["stats"]["windows"] == 4
    for w in ASR_WORDS:
        assert w not in printed
    info = json.loads(printed.strip().splitlines()[-1])
    assert info["windows"] == 4 and info["model"] == "fake-embedder"
    assert not [n for n in os.listdir(os.path.dirname(out)) if ".part-" in n]
    assert TV.main(["--project", p, "--out", out + "2", "--encoding", "round4", "--span-sec", "20"]) == 0
    with open(out + "2", encoding="utf-8") as f:
        d2 = json.load(f)
    assert d2["encoding"] == "round4" and d2["span_ms"] == 20000 and TV.validate(d2) == []


def test_cli_model_missing_writes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(S, "load_default", lambda *a, **k: (None, S.status_doc("model_missing")))
    p = make_project(tmp_path)
    assert TV.main(["--project", p]) == 3
    assert json.loads(capsys.readouterr().out.strip())["error"] == "model_missing"
    assert not os.path.exists(os.path.join(p, "derived", "asr-topic-vectors.json"))
    assert TV.main(["--project", p, "--asr", os.path.join(p, "없음.json")]) == 2


def test_cli_refuses_out_outside_protected_zone(tmp_path, capsys, monkeypatch):
    """--out 은 프로젝트 보호 구역 'derived/asr*' 안만(engine_to_ai --vectors 와 같은 규칙, 검토 2026-10-23).
    밖이면 모델을 띄우거나 asr 을 읽기 전에 종료 2(out_unprotected), 아무것도 쓰지 않는다."""
    loaded = []
    monkeypatch.setattr(TV, "load_embedder",
                        lambda models_dir=None, backend="onnx": (loaded.append(1), (fake(), {"status": "ok"}))[1])
    p = make_project(tmp_path)
    bad = [os.path.join(p, "derived", "vectors.json"), os.path.join(str(tmp_path), "pub", "asr-v.json")]
    for out in bad:
        assert TV.main(["--project", p, "--out", out]) == 2 and not os.path.exists(out)
    assert not loaded
    assert [json.loads(x)["error"] for x in capsys.readouterr().out.strip().splitlines()] == ["out_unprotected"] * 2
    assert TV.main(["--project", p, "--out", os.path.join(p, "derived", "asr-v.json")]) == 0 and loaded


def test_engine_main_passthrough(tmp_path, monkeypatch):
    from engine.__main__ import main as engine_main
    monkeypatch.setattr(TV, "load_embedder", lambda models_dir=None, backend="onnx": (fake(), {"status": "ok"}))
    p = make_project(tmp_path)
    out = os.path.join(p, "derived", "asr-tv.json")
    assert engine_main(["topic-vectors", "--project", p, "--out", out, "--window-sec", "20"]) == 0
    with open(out, encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["window_ms"] == 20000 and TV.validate(doc) == []


# ── 앱 채점기: 선택 입력이 없으면 지금처럼 ───────────────────────────────────────────

TALK = [(0, 10000, ["떡볶이", "어묵"]), (10000, 20000, ["양념", "맵기"]), (20000, 30000, ["용암", "지대"])]
FLOW = [(0, 30000, ["떡볶이", "보스전"])]
SAMPLES = ["떡볶이 맛있겠다", "보스 언제 잡아요?", "오늘 날씨 좋네요", "ㅋㅋㅋ", "냥이 귀엽다", "용암 지대 무서워요"]


def test_no_optional_inputs_same_as_before():
    base = VS.ScoreContext.build(FLOW, TALK, ())
    for opt in ({"topic_vectors": None, "synonyms": None}, {"topic_vectors": [], "synonyms": {}},
                {"topic_vectors": {"schema": "다른 것"}, "synonyms": ""}):
        ctx = VS.ScoreContext.build(FLOW, TALK, (), **opt)
        assert ctx.vec is None and not len(ctx.synonyms) and repr(ctx) == repr(base)
        for t in SAMPLES:
            a, b = VS.score_chat(t, 35000, base), VS.score_chat(t, 35000, ctx)
            assert a == b and not {"syn", "run", "rel", "run_sec"} & set(dict(b.topic.nums))


# ── 앱 채점기: 동의어 · 2자 머리 ─────────────────────────────────────────────────

def test_parse_synonyms():
    m = VS.parse_synonyms("# 합성 묶음\n고양이, 냥이 | 냐옹이\n보스 두목  # 주석\n혼자\n\n")
    assert "냥이" in m["고양이"] and "냐옹이" in m["고양이"] and "고양이" in m["냥이"]
    assert m["보스"] == frozenset({"두목"}) and "혼자" not in m
    assert all(w not in v for w, v in m.items())


def test_synonym_counts_as_overlap():
    talk = [(0, 10000, ["고양이", "간식"])]
    plain = VS.ScoreContext.build((), talk, ())
    syn = VS.ScoreContext.build((), talk, (), synonyms="고양이, 냥이")
    a = VS.score_chat("냥이 귀여워", 20000, plain).topic
    b = VS.score_chat("냥이 귀여워", 20000, syn).topic
    assert a.level == "weak" and a.code == VS.TOPIC_NO_OVERLAP
    assert b.level in ("ok", "good") and b.num("syn") == 1 and b.num("talk") == 1   # 뜻은 같고 낱말은 다른 글 → ok 이상
    assert "냥이" not in repr(syn) and "고양이" not in repr(syn)
    # 묶음 목록·dict 도 받는다
    for s in ([["고양이", "냥이"]], {"냥이": ["고양이"]}):
        assert VS.score_chat("냥이 귀여워", 20000, VS.ScoreContext.build((), talk, (), synonyms=s)).topic.level != "weak"


def test_flow_head2_opt_in():
    flow = [(0, 30000, ["보스전"])]
    ctx = VS.ScoreContext.build(flow, [(0, 10000, ["다른말"])], ())
    off = VS.score_chat("보스 세다", 20000, ctx).topic
    on = VS.score_chat("보스 세다", 20000, ctx, settings=replace(VS.DEFAULT_SETTINGS, flow_head2=True)).topic
    assert off.code != VS.TOPIC_FLOW_HIT and on.level == "good" and on.code == VS.TOPIC_FLOW_HIT
    # 전사 풀에는 2자 머리를 쓰지 않는다
    ctx2 = VS.ScoreContext.build((), [(0, 10000, ["보스전"])], ())
    assert VS.score_chat("보스 세다", 20000, ctx2,
                         settings=replace(VS.DEFAULT_SETTINGS, flow_head2=True)).topic.level == "weak"


# ── 앱 채점기: 창별 벡터(창 ↔ 창) ─────────────────────────────────────────────────

def _topic_vec(name, dim=16):
    """합성 주제 벡터: 주제마다 다른 축 하나(같은 주제의 창은 같은 벡터)."""
    v = [0.05] * dim
    v[{"A": 0, "B": 1, "C": 2}[name]] = 1.0
    return v


def _rows(spec):
    """spec: 주제 이름 문자열('AAAB…'), 창 하나 10초."""
    return [(k * 10000, (k + 1) * 10000, _topic_vec(t)) for k, t in enumerate(spec)]


def _talk_with(word_at: dict, n: int):
    """창마다 서로 다른 합성 낱말(겹치지 않게) + 지정한 창에 지정한 낱말."""
    out = []
    for k in range(n):
        ws = [f"채움{k:02d}말"]
        if k in word_at:
            ws.append(word_at[k])
        out.append((k * 10000, (k + 1) * 10000, ws))
    return out


def test_same_topic_run_lifts_weak_to_ok():
    # 창 0~29 주제 A 가 이어짐. '용암맵' 은 창 15(150~160초)에만. 보낸 시각 260초 → 되돌아보기 170~260초에는 없음
    rows, talk = _rows("A" * 30), _talk_with({15: "용암맵"}, 30)
    plain = VS.ScoreContext.build((), talk, ())
    vec = VS.ScoreContext.build((), talk, (), topic_vectors=rows)
    assert vec.vec is not None and "_Vecs(windows=30)" in repr(vec.vec)
    a = VS.score_chat("용암맵 어려워요", 260_000, plain).topic
    b = VS.score_chat("용암맵 어려워요", 260_000, vec).topic
    assert a.level == "weak" and b.level == "ok" and b.code == VS.TOPIC_RUN_HIT
    assert b.num("run") == 1 and b.num("run_sec") >= 110 and VS.reason_line(b).count("1개") == 1
    # 줄기 사이에 주제가 바뀌면(창 18 이 B) 늘리지 않는다 → weak
    rows2 = _rows("A" * 18 + "B" + "A" * 11)
    c = VS.score_chat("용암맵 어려워요", 260_000, VS.ScoreContext.build((), talk, (), topic_vectors=rows2)).topic
    assert c.level == "weak" and c.num("run") == 0
    # 한도(vec_run_max_ms)를 넘으면 늘리지 않는다
    s = replace(VS.DEFAULT_SETTINGS, vec_run_max_ms=100_000)
    assert VS.score_chat("용암맵 어려워요", 260_000, VS.ScoreContext.build((), talk, (), settings=s, topic_vectors=rows),
                         settings=s).topic.level == "weak"


def test_related_earlier_window_lifts_weak_to_ok():
    # A(0~9) → B(10~29) → A(30~40). '불바다' 는 창 2 에만. 400초에 쓰면 되돌아보기(310~400)는 A, 줄기는 창 30 에서 끊김
    spec = "A" * 10 + "B" * 20 + "A" * 11
    rows, talk = _rows(spec), _talk_with({2: "불바다"}, len(spec))
    vec = VS.ScoreContext.build((), talk, (), topic_vectors=rows)
    r = VS.score_chat("불바다 맵 또 나오나", 400_000, vec).topic
    assert r.level == "ok" and r.code == VS.TOPIC_RELATED_HIT and r.num("rel") == 1
    assert VS.score_chat("불바다 맵 또 나오나", 400_000, VS.ScoreContext.build((), talk, ())).topic.level == "weak"
    # 관련 창 끔 → weak. 다른 주제(B) 창의 낱말은 관련으로 보지 않는다
    s0 = replace(VS.DEFAULT_SETTINGS, vec_related_max=0)
    assert VS.score_chat("불바다 맵", 400_000, VS.ScoreContext.build((), talk, (), settings=s0, topic_vectors=rows),
                         settings=s0).topic.level == "weak"
    talk_b = _talk_with({15: "불바다"}, len(spec))
    assert VS.score_chat("불바다 맵", 400_000,
                         VS.ScoreContext.build((), talk_b, (), topic_vectors=rows)).topic.level == "weak"


def test_vectors_never_make_good_and_keep_other_axes():
    rows, talk = _rows("A" * 30), _talk_with({15: "용암맵", 16: "불기둥"}, 30)
    plain = VS.ScoreContext.build((), talk, ())
    vec = VS.ScoreContext.build((), talk, (), topic_vectors=rows)
    a = VS.score_chat("용암맵 불기둥 어때요?", 260_000, plain)
    b = VS.score_chat("용암맵 불기둥 어때요?", 260_000, vec)
    assert b.topic.level == "ok" and b.topic.num("run") == 2      # 두 개 겹쳐도 ok 까지(정밀도 우선)
    assert a.expand == b.expand and a.repeat == b.repeat


def test_vector_guards():
    talk = _talk_with({}, 10)
    few = VS.ScoreContext.build((), talk, (), topic_vectors=_rows("A" * 5))         # vec_min_windows(12) 미만
    assert few.vec is None
    mixed = _rows("A" * 20) + [(300_000, 310_000, [1.0, 2.0])]                     # 차원이 다른 줄은 버림
    assert VS.ScoreContext.build((), talk, (), topic_vectors=mixed).vec is not None
    zero = [(k * 10000, (k + 1) * 10000, [0.0] * 4) for k in range(20)]            # 0 벡터만 → 없음
    assert VS.ScoreContext.build((), talk, (), topic_vectors=zero).vec is None
    # 되돌아보기 안에 벡터 창이 없으면 벡터를 보지 않는다(숫자 칸도 안 붙음)
    ctx = VS.ScoreContext.build((), _talk_with({}, 40), (), topic_vectors=_rows("A" * 20))
    r = VS.score_chat("아무말 대잔치", 395_000, ctx).topic
    assert r.level == "weak" and r.num("run") is None


def test_decode_vector_and_vectors_from_doc():
    assert VS.decode_vector("!!", "f16le-b64", 2) is None
    assert VS.decode_vector("AAAA", "f16le-b64", 2) is None                  # 길이 틀림(3바이트)
    assert VS.decode_vector("AAAAAA==", "f16le-b64", 2) == (0.0, 0.0)
    assert VS.decode_vector([0.5, True], "round4", 2) is None
    assert VS.decode_vector([0.5, 0.25], "round4", 2) == (0.5, 0.25)
    assert VS.decode_vector([0.5], "round4", 0) is None
    assert VS.vectors_from_doc({"schema": VS.TOPIC_VECTORS_SCHEMA}) == []
    assert VS.vectors_from_doc(None) == []


def test_engine_doc_feeds_scorer(tmp_path):
    """엔진이 쓴 문서를 앱이 그대로 읽는다(창 4개라 vec_min_windows 를 낮춰 본다)."""
    doc = TV.from_project(make_project(tmp_path), embedder=fake())
    s = replace(VS.DEFAULT_SETTINGS, vec_min_windows=2)
    ctx = VS.ScoreContext.build((), [], (), settings=s, topic_vectors=doc)
    assert ctx.vec is not None and len(ctx.vec.starts) == 4


def test_settings_fields_exist_for_vector_thresholds():
    names = {f.name for f in fields(VS.ViewerScoreSettings)}
    for n in ("vec_run_max_ms", "vec_boundary_pct", "vec_related_pct", "vec_related_max", "vec_min_windows",
              "vec_max_gap_ms", "flow_head2"):
        assert n in names


# ── tools/engine_to_ai --vectors ─────────────────────────────────────────────────

@pytest.fixture
def fake_loader(monkeypatch):
    monkeypatch.setattr(TV, "load_embedder", lambda models_dir=None, backend="onnx": (fake(), {"status": "ok"}))


def test_engine_to_ai_vectors_out(tmp_path, capsys, fake_loader):
    from tools import engine_to_ai as e2a
    p = make_project(tmp_path)
    outside = os.path.join(str(tmp_path), "res", "ai.json")
    assert e2a.main(["--from-project", p, "--out", outside, "--vectors"]) == 2
    assert "vectors_out_unprotected" in capsys.readouterr().out
    assert not os.path.exists(outside) and not os.path.exists(e2a.vectors_out_path(outside))
    out = os.path.join(p, "derived", "asr-link.json")
    assert e2a.main(["--from-project", p, "--out", out, "--vectors"]) == 0
    info = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert info["vectors_out"] == os.path.join(p, "derived", "asr-link.vectors.json")
    with open(info["vectors_out"], encoding="utf-8") as f:
        doc = json.load(f)
    assert TV.validate(doc) == [] and info["vector_windows"] == len(doc["windows"]) == 4
    assert e2a.main(["--in", out, "--out", out + "2", "--vectors"]) == 2
    assert e2a.main(["--from-project", p, "--out", out, "--vectors", "--semantic-backend", "off"]) == 2


def test_engine_to_ai_vectors_model_missing(tmp_path, capsys, monkeypatch):
    from tools import engine_to_ai as e2a
    monkeypatch.setattr(S, "load_default", lambda *a, **k: (None, S.status_doc("model_missing")))
    p = make_project(tmp_path)
    out = os.path.join(p, "derived", "asr-link.json")
    assert e2a.main(["--from-project", p, "--out", out, "--vectors"]) == 1
    res = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert res == {"error": "vectors", "code": "model_missing"}
    assert not os.path.exists(out) and not os.path.exists(e2a.vectors_out_path(out))


def test_engine_to_ai_vectors_seal_needs_sealer(tmp_path, capsys, monkeypatch):
    from app import seal
    from tools import engine_to_ai as e2a
    monkeypatch.delattr(seal, e2a.VECTORS_SEAL_FN, raising=False)
    calls = []
    monkeypatch.setattr(seal, "seal_bytes", lambda *a, **k: calls.append("seal"))
    monkeypatch.setattr(e2a, "_run_engine", lambda *a, **k: calls.append("engine"))
    p = make_project(tmp_path)
    ai_root = os.path.join(str(tmp_path), "ai")
    rc = e2a.main(["--from-project", p, "--seal", "--by", "L01", "--vectors", "--ai-root", ai_root])
    assert rc == 1 and "vectors_seal_unsupported" in capsys.readouterr().out
    assert calls == [] and not os.path.exists(ai_root)


def test_engine_to_ai_vectors_seal_pairs(tmp_path, capsys, monkeypatch, fake_loader):
    from app import seal
    from tools import engine_to_ai as e2a
    got = {}

    def fake_seal(project, data, **k):
        got["ai"] = data
        return SimpleNamespace(sha256="c" * 64, size=len(data), check="valid", reject_reasons=[], new=True,
                               presealed=False)

    def fake_vec(project, data, *, pair_sha256, by, source, ai_root):
        got.update(vec=data, pair=pair_sha256, by=by, source=source)
        return SimpleNamespace(sha256=hashlib.sha256(data).hexdigest(), size=len(data), check="valid",
                               reject_reasons=[])

    monkeypatch.setattr(seal, "seal_bytes", fake_seal)
    monkeypatch.setattr(seal, e2a.VECTORS_SEAL_FN, fake_vec, raising=False)
    p = make_project(tmp_path, name="pilot-합성-봉인")          # 시범도 봉인 경로로는 만든다
    assert e2a.main(["--from-project", p, "--seal", "--by", "L01", "--vectors"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert got["pair"] == "c" * 64 and got["source"] == "engine_vectors" and got["by"] == "L01"
    assert TV.validate(json.loads(got["vec"].decode("utf-8"))) == []
    assert out["vectors_sealed"] == hashlib.sha256(got["vec"]).hexdigest() and out["vectors_check"] == "valid"
    for w in ASR_WORDS:
        assert w not in json.dumps(out, ensure_ascii=False)
    assert not os.path.exists(os.path.join(p, "derived", "asr-topic-vectors.json"))
    # 시범에 --out 은 거부
    assert e2a.main(["--from-project", p, "--out", os.path.join(p, "derived", "asr-l.json"), "--vectors"]) == 1
    assert "pilot_needs_seal" in capsys.readouterr().out
