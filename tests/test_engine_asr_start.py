"""asr_start_ms(--asr-start-ms): asr 가 영상 파일의 어느 시각부터 잘라 낸 음성인가 (2026-10-24 시범 봉인의 함정).

영상 파일 시각 = asr 시각 + asr_start_ms, 그 뒤 보정(video_to_chat_ms). --file-start-ms 는 다른 뜻이다(클립 파일이 VOD 의
어디서 시작하나, project.json 보정과 짝) — project.json 보정이 있으면 되돌려 넣어 상쇄되므로 asr 시각을 옮기지 않는다.

시범과 같은 꼴을 **합성 자료**로 만든다(채팅·전사·닉네임은 모두 지어낸 것, video_no 0): 매체는 VOD 전체 파일(project.json
offset 0, 길이 7,200,000ms), wav 만 20:00(1,200,000ms)부터 40분 잘라 전사 → asr 시각 0~2,400,000. 범위 1,200,000~3,600,000.
엔진 venv·앱 venv 어느 쪽에서도 돈다(임베딩은 engine.semantic.FakeEmbedder, 모델 없음).
"""
import argparse
import hashlib
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.core import ids
from app.core.timecode import video_to_chat_ms
from engine import link as L
from engine import semantic as S
from engine import topic_vectors as TV
from engine import topics as TP
from engine import transcript_words as TW
from tools import ai_seal
from tools import engine_to_ai as e2a

SALT = b"\x05" * 32
ASR_START = 1_200_000            # wav 를 뽑은 시작(영상 파일 시각) = 20:00
END = 3_600_000                  # 60:00
ASR_DUR = 2_400_000              # 40분
MEDIA_DUR = 7_200_000            # 매체(VOD 전체) 2시간
CHAT_T = ASR_START + 1000        # 채팅(VOD) 20:01
CHAT_TEXT = "제주도 가보셨어요?"
SEG0_TEXT = "제주도는 작년에 다녀왔어요"
SEG_TEXT = "용암 지대 보스"


def _uid(n):
    return hashlib.sha1(f"fixture-asr-{n}".encode()).hexdigest()


def _msg(n, t_ms, text):
    return {"chat_id": ids.chat_id_base("0", 1_700_000_000_000 + t_ms, _uid(n), 1, text), "t_ms": t_ms,
            "server_time_ms": 1_700_000_000_000 + t_ms, "type_code": 1, "kind": "text", "status": "NORMAL",
            "viewer_id": ids.viewer_id(SALT, _uid(n)), "is_anonymous": False, "nickname_masked": "시*",
            "role": "viewer", "text": text, "raw_ref": {"fetch_id": "F20261024-test", "page": 0, "index": 0}}


def asr_segments():
    """asr 시각(wav 0초 기준) 0~2,400,000. 구간 0: 3,000~5,000 (채팅에 대한 답). 구간 i(1~239): i·10초 + 1초부터 2초.
    홀수 i 는 낱말 시각이 있고 짝수 i 는 없다(두 길 모두 본다)."""
    segs = [{"id": 0, "start_ms": 3000, "end_ms": 5000, "text": SEG0_TEXT, "words": []}]
    for i in range(1, 240):
        a = i * 10_000 + 1000
        s = {"id": i, "start_ms": a, "end_ms": a + 2000, "text": SEG_TEXT, "words": []}
        if i % 2:
            s["words"] = [{"start_ms": a, "end_ms": a + 500, "word": " 용암", "prob": 0.9},
                          {"start_ms": a + 600, "end_ms": a + 1100, "word": " 지대", "prob": 0.9},
                          {"start_ms": a + 1200, "end_ms": a + 2000, "word": " 보스", "prob": 0.9}]
        segs.append(s)
    return segs


def make_project(root, name="합성 프로젝트-asr", *, media_ms=MEDIA_DUR, calibration=True, asr_dur=ASR_DUR):
    p = os.path.join(str(root), name)
    os.makedirs(os.path.join(p, "chat"))
    os.makedirs(os.path.join(p, "derived"))
    pj = {"project_id": "p-synth-asr", "broadcast": {"video_no": "0"}}
    if calibration:
        pj["calibration"] = {"active_rev": 1, "revs": [
            {"rev": 0, "offset_ms": 0, "status": "unverified"},
            {"rev": 1, "offset_ms": 0, "method": "single_offset", "status": "verified"}]}
    if media_ms is not None:
        pj["media"] = {"media_rev": 1, "current": {"probe": {"duration_ms": media_ms, "vfr": False}}, "history": []}
    with open(os.path.join(p, "project.json"), "w", encoding="utf-8") as f:
        json.dump(pj, f, ensure_ascii=False)
    with open(os.path.join(p, "chat", "messages.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(_msg(1, CHAT_T, CHAT_TEXT), ensure_ascii=False) + "\n")
    with open(os.path.join(p, "chat", "fetch-F20261024-test.json"), "w", encoding="utf-8") as f:
        json.dump({"fetch_id": "F20261024-test", "video_no": "0"}, f)
    asr = {"format": "chatcoach.asr", "format_version": 1, "model": {"name": "synthetic"}, "settings": {"vad": True},
           "audio": {"duration_ms": asr_dur}, "segments": asr_segments()}
    with open(os.path.join(p, "derived", "asr.json"), "w", encoding="utf-8") as f:
        json.dump(asr, f, ensure_ascii=False)
    return p


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path)


@pytest.fixture
def fake_loader(monkeypatch):
    monkeypatch.setattr(TV, "load_embedder",
                        lambda models_dir=None, backend="onnx": (S.FakeEmbedder({"보스": "boss"}, dim=32), {"status": "ok"}))


def _reaction(doc):
    [r] = [x for x in doc["reactions"] if x["chat_t_ms"] == CHAT_T]
    return r


# ── 1. shift_segments · check_asr_start (순수) ───────────────────────────────

def test_shift_segments_moves_segments_and_word_times_without_mutating():
    segs = [{"start_ms": 10, "end_ms": 20, "text": "보스", "words": [{"start_ms": 11, "end_ms": 12, "word": " 보스"},
                                                                     {"start_ms": 13.5, "end_ms": None, "word": " x"}]},
            {"start_ms": 30, "end_ms": 40, "text": "용암"}]
    before = json.dumps(segs)
    out = TW.shift_segments(segs, 1000)
    assert json.dumps(segs) == before                                    # 입력은 그대로
    assert (out[0]["start_ms"], out[0]["end_ms"], out[1]["start_ms"], out[1]["end_ms"]) == (1010, 1020, 1030, 1040)
    assert out[0]["words"][0] == {"start_ms": 1011, "end_ms": 1012, "word": " 보스"}
    assert out[0]["words"][1]["start_ms"] == 13.5                       # 정수가 아닌 낱말 시각은 그대로(구간 단위로 물러남)
    assert TW._word_points(out[0]) is None and TW._word_points(segs[0]) is None
    assert TW.shift_segments(segs, 0) == segs
    for bad in (-1, True, 1.5, "1200000", None):
        with pytest.raises(ValueError):
            TW.shift_segments(segs, bad)
    assert TW.check_asr_start(ASR_START) == ASR_START


# ── 2. (a) link: asr 0~10초 + asr_start_ms → 판정 시각 +1,200,000 ─────────────

def test_link_asr_start_moves_utterances(project):
    inp = L.load_project(project, asr_start_ms=ASR_START)
    assert inp.utterances[0].start_ms == 3000 + ASR_START and inp.utterances[0].end_ms == 5000 + ASR_START
    assert inp.asr_start_ms == ASR_START and inp.asr_meta["asr_start_ms"] == ASR_START
    assert inp.calibration_source == "project.json" and inp.calibration.offset_ms == 0
    doc = L.build_doc(inp, start_ms=ASR_START, end_ms=END)
    assert L.validate_result(doc) == []
    r = _reaction(doc)
    assert r["result"] == "graded" and r["grade"] == "likely"
    assert r["utterance_start_ms"] == 3000 + ASR_START and r["utterance_end_ms"] == 5000 + ASR_START
    assert r["reaction_time_ms"] == 3000 + ASR_START
    assert doc["calibration"]["asr_start_ms"] == ASR_START and doc["inputs"]["asr_warnings"] == []
    # 옮기지 않으면 채팅 시간 창(20:01~)에 그 답이 없다(그 자리에는 다른 구간 i=120 이 온다)
    inp0 = L.load_project(project)
    doc0 = L.build_doc(inp0, start_ms=ASR_START, end_ms=END)
    r0 = _reaction(doc0)
    assert r0["result"] == "unconfirmed" and r0["utterance_start_ms"] is None
    assert doc0["calibration"]["asr_start_ms"] == 0
    # 음성 끝 = 잘라 낸 시작 + 길이(영상 파일 60:00). 50:00 채팅의 창은 옮기면 잘리지 않고, 안 옮기면(40:00 끝) 잘린다
    late = [L.Chat("c" + "0" * 20, 3_000_000, "v-synth", "보스")]
    assert L.link(replace(inp, chats=late))[1]["window_truncated_by_audio_end"] == 0
    assert L.link(replace(inp0, chats=late))[1]["window_truncated_by_audio_end"] == 1
    # v1 으로 바꿔도 시각이 그대로 옮겨 간다(v1 에는 calibration 칸이 없다)
    v1, _ = e2a.to_ai_result(doc)
    assert [x["utterance_start_ms"] for x in v1["reactions"] if x["result"] == "graded"] == [3000 + ASR_START]
    assert "calibration" not in v1
    with pytest.raises(ValueError):
        L.load_project(project, asr_start_ms=-1)


# ── 3. (b) 시범의 함정 재현: project.json offset 0 + --file-start-ms 1,200,000 ─────

def test_file_start_with_project_calibration_does_not_move_asr(project):
    """함정을 못박는다: project.json 보정이 있을 때 --file-start-ms 는 되돌려 넣어 상쇄된다 → asr 시각 그대로."""
    inp = L.load_project(project, file_start_ms=ASR_START)
    assert inp.calibration_source == "project.json+cli_file_start"
    assert inp.calibration.offset_ms == ASR_START and inp.file_start_in_vod_ms == ASR_START
    assert inp.utterances[0].start_ms == 3000
    assert video_to_chat_ms(3000, inp.calibration, inp.file_start_in_vod_ms) == 3000          # 그대로
    assert video_to_chat_ms(2_399_000, inp.calibration, inp.file_start_in_vod_ms) == 2_399_000
    r = _reaction(L.build_doc(inp, start_ms=ASR_START, end_ms=END))
    assert r["result"] == "unconfirmed"                                                  # 그 답이 VOD 0:03 에 놓였다
    # --asr-start-ms 를 주면 맞게 옮겨진다(--file-start-ms 를 함께 줘도, 빼도 같다)
    for kw in ({"asr_start_ms": ASR_START}, {"asr_start_ms": ASR_START, "file_start_ms": ASR_START}):
        inp2 = L.load_project(project, **kw)
        assert video_to_chat_ms(inp2.utterances[0].start_ms, inp2.calibration, inp2.file_start_in_vod_ms) \
            == 3000 + ASR_START
        r2 = _reaction(L.build_doc(inp2, start_ms=ASR_START, end_ms=END))
        assert r2["grade"] == "likely" and r2["utterance_start_ms"] == 3000 + ASR_START
        assert inp2.asr_meta["warnings"] == []


def test_topics_inputs_follow_asr_start(project):
    utts, _m, _n, _inp = TP.inputs_from_project(project, asr_start_ms=ASR_START)
    assert min(u.t_ms for u in utts) == 3000 + ASR_START and max(u.t_ms for u in utts) == 239 * 10_000 + 1000 + ASR_START
    utts0, _m, _n, _inp = TP.inputs_from_project(project, file_start_ms=ASR_START)      # 함정: 그대로
    assert min(u.t_ms for u in utts0) == 3000


# ── 4. (c) 전사 낱말 · 주제 벡터 창 ────────────────────────────────────────────

def test_words_windows_shift_and_nothing_dropped(project):
    doc = TW.from_project(project, start_ms=ASR_START, end_ms=END, asr_start_ms=ASR_START)
    assert TW.validate(doc) == []
    st = doc["stats"]
    assert st["dropped_out_of_range"] == 0 and st["dropped_before_vod0"] == 0
    assert st["windows"] == 240 and st["asr_start_ms"] == ASR_START
    starts = [w["start_ms"] for w in doc["windows"]]
    assert starts[0] == ASR_START and starts[-1] == END - 10_000
    assert set(doc["calibration"]) == {"source", "rev", "method", "status", "offset_ms", "file_start_in_vod_ms"}
    # 함정(시범과 같은 꼴): 앞 절반을 버리고 뒤 절반을 20:00~40:00 에 놓는다
    trap = TW.from_project(project, start_ms=ASR_START, end_ms=END, file_start_ms=ASR_START)
    ts = trap["stats"]
    assert ts["dropped_out_of_range"] == 1 + 60 * 3 + 59      # 구간 0 + 홀수 i(낱말 셋) 60개 + 짝수 i 59개
    assert ts["windows"] == 120 and max(w["start_ms"] for w in trap["windows"]) < ASR_START + ASR_DUR // 2
    assert ts["asr_start_ms"] == 0
    with pytest.raises(TW.WordsError) as ei:
        TW.from_project(project, asr_start_ms=-5)
    assert ei.value.code == "bad_asr_start"


def test_vectors_windows_shift_and_nothing_dropped(project):
    emb = S.FakeEmbedder({"보스": "boss"}, dim=32)
    doc = TV.from_project(project, embedder=emb, start_ms=ASR_START, end_ms=END, asr_start_ms=ASR_START)
    assert TV.validate(doc) == []
    st = doc["stats"]
    assert st["dropped_out_of_range"] == 0 and st["dropped_before_vod0"] == 0 and st["asr_start_ms"] == ASR_START
    starts = [w["start_ms"] for w in doc["windows"]]
    assert len(starts) == 240 and starts[0] == ASR_START and starts[-1] == END - 10_000
    trap = TV.from_project(project, embedder=emb, start_ms=ASR_START, end_ms=END, file_start_ms=ASR_START)
    assert trap["stats"]["dropped_out_of_range"] == 240 and len(trap["windows"]) == 120
    with pytest.raises(TV.VectorsError) as ei:
        TV.from_project(project, embedder=emb, asr_start_ms=-5)
    assert ei.value.code == "bad_asr_start"


def test_engine_clis_accept_asr_start(project, tmp_path, capsys, fake_loader):
    out = os.path.join(str(tmp_path), "res", "link.json")
    assert L.main(["--project", project, "--out", out, "--engine", "v0.2", "--start-ms", str(ASR_START),
                   "--end-ms", str(END), "--asr-start-ms", str(ASR_START)]) == 0
    o = capsys.readouterr()
    assert CHAT_TEXT not in o.out + o.err and SEG0_TEXT not in o.out + o.err
    with open(out, encoding="utf-8") as f:
        d = json.load(f)
    assert d["calibration"]["asr_start_ms"] == ASR_START and _reaction(d)["utterance_start_ms"] == 3000 + ASR_START
    wout = os.path.join(project, "derived", "asr-words.json")
    assert TW.main(["--project", project, "--start-ms", str(ASR_START), "--end-ms", str(END),
                    "--asr-start-ms", str(ASR_START)]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["out"] == wout and s["windows"] == 240 and s["dropped_out_of_range"] == 0 and s["asr_start_ms"] == ASR_START
    assert TW.main(["--project", project, "--asr-start-ms", "-1"]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "bad_asr_start"}
    assert TV.main(["--project", project, "--start-ms", str(ASR_START), "--end-ms", str(END),
                    "--asr-start-ms", str(ASR_START)]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["windows"] == 240 and s["dropped_out_of_range"] == 0 and s["asr_start_ms"] == ASR_START
    assert TV.main(["--project", project, "--asr-start-ms", "-1"]) == 2
    tout = os.path.join(str(tmp_path), "res", "topics.json")
    assert TP.main(["--project", project, "--out", tout, "--start-ms", str(ASR_START), "--end-ms", str(END),
                    "--asr-start-ms", str(ASR_START)]) == 0
    with open(tout, encoding="utf-8") as f:
        assert json.load(f)["stats"]["utterances"] == 240
    capsys.readouterr()


# ── 5. (d) tools: engine_to_ai · ai_seal attach-pairs ──────────────────────────

def test_engine_to_ai_asr_start_reaches_all_engines(project, capsys, fake_loader):
    out = os.path.join(project, "derived", "asr-link.json")
    base = ["--from-project", project, "--out", out, "--start-ms", str(ASR_START), "--end-ms", str(END),
            "--topics", "--words", "--vectors"]
    assert e2a.main(base + ["--asr-start-ms", str(ASR_START)]) == 0
    o = capsys.readouterr()
    assert o.err == ""                                                 # 경고 없음
    info = json.loads(o.out.strip().splitlines()[-1])
    with open(out, encoding="utf-8") as f:
        v1 = json.load(f)
    assert [r["utterance_start_ms"] for r in v1["reactions"] if r["result"] == "graded"] == [3000 + ASR_START]
    with open(info["words_out"], encoding="utf-8") as f:
        w = json.load(f)
    assert w["stats"]["asr_start_ms"] == ASR_START and w["stats"]["dropped_out_of_range"] == 0
    assert len(w["windows"]) == 240 and w["windows"][0]["start_ms"] == ASR_START
    with open(info["vectors_out"], encoding="utf-8") as f:
        v = json.load(f)
    assert v["stats"]["asr_start_ms"] == ASR_START and len(v["windows"]) == 240
    with open(info["topics_out"], encoding="utf-8") as f:
        t = json.load(f)
    assert t["stats"]["utterances"] == 240
    # 인자 규칙: 음수 · --in 과 함께는 입력 오류(엔진을 돌리기 전)
    assert e2a.main(base + ["--asr-start-ms", "-1"]) == 2
    assert e2a.main(["--in", out, "--out", out + "2", "--asr-start-ms", "5"]) == 2
    capsys.readouterr()


def test_engine_to_ai_v02_direct_path_passes_asr_start(project):
    """--topics 없는 v0.2 길(link 를 바로 부름)도 같은 값을 쓴다."""
    ns = argparse.Namespace(no_identities=False, offset_ms=None, file_start_ms=None, start_ms=ASR_START, end_ms=END,
                            asr_start_ms=ASR_START)
    doc = e2a._run_engine(project, ns)
    assert _reaction(doc)["utterance_start_ms"] == 3000 + ASR_START and doc["calibration"]["asr_start_ms"] == ASR_START
    # ns 에 칸이 없는 옛 호출은 0
    ns0 = argparse.Namespace(no_identities=False, offset_ms=None, file_start_ms=None, start_ms=ASR_START, end_ms=END)
    # (08 D-34) 칸이 없으면 None — 엔진이 asr.json 의 기억(audio.asr_start_ms)을 쓰고, 이 asr 는 기억이 없어 0
    assert e2a.asr_start_of(ns0) is None and e2a._run_engine(project, ns0)["calibration"]["asr_start_ms"] == 0


def _attach_parser():
    p = argparse.ArgumentParser()
    ai_seal._add_engine_args(p)
    return p


def test_ai_seal_engine_pairs_uses_asr_start(project, fake_loader, capsys):
    ns = _attach_parser().parse_args(["--from-engine", "--asr-start-ms", str(ASR_START), "--start-ms", str(ASR_START),
                                      "--end-ms", str(END)])
    assert ns.asr_start_ms == ASR_START
    ns.seal = False                                     # 합성(시범 아님) — attach_project 는 True 로 둔다
    docs = ai_seal.engine_pairs(project, ns, ["topics", "words", "vectors"])
    w, v, t = (json.loads(docs[k].decode("utf-8")) for k in ("words", "vectors", "topics"))
    assert w["stats"]["asr_start_ms"] == ASR_START and w["stats"]["dropped_out_of_range"] == 0 and len(w["windows"]) == 240
    assert v["stats"]["asr_start_ms"] == ASR_START and len(v["windows"]) == 240
    assert t["stats"]["utterances"] == 240
    assert capsys.readouterr().err == ""
    assert _attach_parser().parse_args(["--from-engine"]).asr_start_ms is None   # 08 D-34: 주지 않으면 asr.json 의 기억


def test_ai_seal_attach_pairs_cli_passes_asr_start(project, monkeypatch, capsys):
    """attach-pairs 명령줄 → ENGINE_PAIRS 의 ns.asr_start_ms. 봉인 상태·붙이기는 가짜로 바꿔 끼운다(app.seal 은 그대로)."""
    entry = SimpleNamespace(sha256="a" * 64, unsealed_at=None, check="valid", topics_sha256=None, words_sha256=None,
                            vectors_sha256=None)
    monkeypatch.setattr(ai_seal.seal, "seal_status", lambda project, ai_root=None: SimpleNamespace(problems=[],
                                                                                                   entries=[entry]))
    attached = []

    def fake_attach(project, sha, want, *, by, source, ai_root):
        attached.append((sha, sorted(want), source))
        return SimpleNamespace(ok=True, presealed=True, after_primary_lock=True,
                               rows=[{"kind": k, "status": "attached", "sha256": "b" * 64, "check": "valid",
                                      "reject_reasons": []} for k in want])
    monkeypatch.setattr(ai_seal.seal, "attach_pairs", fake_attach)
    seen = []

    def fake_engine(project, ns, kinds):
        seen.append((ns.asr_start_ms, ns.seal, list(kinds)))
        return {k: b"{}" for k in kinds}
    monkeypatch.setattr(ai_seal, "ENGINE_PAIRS", fake_engine)
    base = ["attach-pairs", "--project", project, "--by", "L01", "--all"]
    assert ai_seal.main(base + ["--from-engine", "--asr-start-ms", str(ASR_START)]) == 0
    assert seen == [(ASR_START, True, ["topics", "words", "vectors"])]
    assert attached == [("a" * 64, ["topics", "vectors", "words"], "engine")]
    assert json.loads(capsys.readouterr().out)["ok"] is True
    # 음수 · 짝 파일과 함께는 입력 오류(엔진을 띄우지 않음)
    assert ai_seal.main(base + ["--from-engine", "--asr-start-ms", "-1"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "args"
    assert ai_seal.main(base + ["--topics", os.path.join(project, "x.json"), "--asr-start-ms", "5"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "args"
    assert len(seen) == 1


# ── 6. (e) 경고 asr_shorter_than_media ────────────────────────────────────────

def test_warning_asr_shorter_than_media(project, tmp_path):
    inp = L.load_project(project, file_start_ms=ASR_START)
    assert inp.asr_meta["warnings"] == [{"code": "asr_shorter_than_media", "asr_duration_ms": ASR_DUR,
                                         "media_duration_ms": MEDIA_DUR, "file_start_ms": ASR_START}]
    doc = L.build_doc(inp, start_ms=ASR_START, end_ms=END)
    assert doc["inputs"]["asr_warnings"] == inp.asr_meta["warnings"] and L.validate_result(doc) == []
    line = L.format_asr_warning(inp.asr_meta["warnings"][0])
    assert "asr_shorter_than_media" in line and "2400.0초" in line and "7200.0초" in line and "60%" in line
    assert "\n" not in line
    # 경고가 없는 경우: asr_start 를 줌 · file_start 없음 · 매체 길이 모름 · 충분히 김 · project.json 보정 없음
    assert L.load_project(project, file_start_ms=ASR_START, asr_start_ms=ASR_START).asr_meta["warnings"] == []
    assert L.load_project(project).asr_meta["warnings"] == []
    for i, kw in enumerate(({"media_ms": None}, {"media_ms": 3_000_000}, {"calibration": False})):
        p = make_project(tmp_path, f"합성 프로젝트-asr-{i}", **kw)
        assert L.load_project(p, file_start_ms=ASR_START).asr_meta["warnings"] == [], kw
    assert L.asr_shorter_warning(None, MEDIA_DUR, file_start_ms=0) is None
    assert L.asr_shorter_warning(ASR_DUR, 0, file_start_ms=0) is None
    assert L.asr_shorter_warning(4_319_999, MEDIA_DUR, file_start_ms=0)["code"] == "asr_shorter_than_media"
    assert L.asr_shorter_warning(4_320_000, MEDIA_DUR, file_start_ms=0) is None


def test_engine_to_ai_prints_warning_to_stderr_and_continues(project, capsys):
    out = os.path.join(str(project), "derived", "asr-link.json")
    args = ["--from-project", project, "--out", out, "--start-ms", str(ASR_START), "--end-ms", str(END),
            "--file-start-ms", str(ASR_START)]
    assert e2a.main(args) == 0                                         # 멈추지 않는다
    o = capsys.readouterr()
    lines = [x for x in o.err.splitlines() if x.strip()]
    assert len(lines) == 1 and "asr_shorter_than_media" in lines[0] and "2400.0초" in lines[0]
    assert "asr_shorter_than_media" not in o.out
    assert CHAT_TEXT not in o.err and SEG0_TEXT not in o.err
    assert e2a.main(args + ["--topics"]) == 0                         # 주제 흐름 길(TP.inputs_from_project)도 한 줄
    assert len([x for x in capsys.readouterr().err.splitlines() if "asr_shorter_than_media" in x]) == 1
    assert e2a.main(args + ["--asr-start-ms", str(ASR_START)]) == 0
    assert "asr_shorter_than_media" not in capsys.readouterr().err


# ── 7. 도움말 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fn,argv", [(L.main, ["-h"]), (TP.main, ["-h"]), (TW.main, ["-h"]), (TV.main, ["-h"]),
                                     (e2a.main, ["-h"]), (ai_seal.main, ["attach-pairs", "-h"])])
def test_help_shows_asr_start(fn, argv, capsys):
    with pytest.raises(SystemExit) as ei:
        fn(argv)
    assert ei.value.code == 0
    assert "--asr-start-ms" in capsys.readouterr().out
