"""asr.json 이 기억하는 '잘라 낸 시작'(audio.asr_start_ms, 08 D-33 '시작 옮기기') — 함정 재현.

새 방송이 긴 영상의 가운데 [a, a+N) 만 전사하면 asr.json 의 시각은 a 만큼 앞당겨진다(wav 0초 기준). D-28 까지는 이 값을
명령줄 --asr-start-ms 로만 받아, 읽는 길 한 군데라도 빠뜨리면 발화가 a 만큼 밀렸다(시범 봉인에서 --file-start-ms 로 대신 써
20분 밀림). D-33: engine asr --asr-start-ms a 가 asr.json 에 그 값을 적고, 읽는 길 — 판정·주제·전사 낱말·벡터(engine_to_ai)·
앵커 후보·위치 찾기(find_anchors)·메인 창의 전사 낱말 다시 만들기(engine transcript-words --project, 명령줄에 asr 시작 없음)·
판정 도장 — 이 모두 스스로 같은 값을 쓴다. 다른 값을 주면 거부하고, 클립 시작(--file-start-ms)과 섞이지 않는다.

합성 자료만: tests/test_engine_asr_start 의 시범 꼴 프로젝트(VOD 전체 파일, wav 를 20:00 부터 40분) · tests/test_find_anchors 의
위치 찾기 합성(3시간 채팅 + 20분 클립) · tests/test_new_broadcast 의 가짜 진행기. 채팅·전사·닉네임은 모두 지어낸 것.
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app import engine_run as er  # noqa: E402
from app import viewer_flow as vf  # noqa: E402
from engine import asr as EA  # noqa: E402
from engine import link as L  # noqa: E402
from engine import topic_vectors as TV  # noqa: E402
from engine import transcript_words as TW  # noqa: E402
from tests import test_engine_asr_start as base  # noqa: E402
from tests import test_new_broadcast as nb  # noqa: E402
from tools import engine_to_ai as e2a  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    import find_anchors as fa  # noqa: E402
finally:
    sys.path.pop(0)
from tests import test_find_anchors as tfa  # noqa: E402

A = base.ASR_START                  # 1,200,000 = 20:00 (영상 파일 시각)


def _record(asr_path: str, value) -> None:
    """asr.json 에 audio.asr_start_ms 를 적는다(engine asr --asr-start-ms 와 같은 칸)."""
    with open(asr_path, encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("audio", {})["asr_start_ms"] = value
    with open(asr_path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


@pytest.fixture
def project(tmp_path):
    """시범 꼴 합성 프로젝트(보정 오프셋 0 확인됨, 매체 2시간) + asr.json 이 '20:00 부터 뽑았다' 를 기억."""
    p = base.make_project(tmp_path)
    _record(os.path.join(p, "derived", "asr.json"), A)
    return p


# ── 1. 규칙(순수) ─────────────────────────────────────────────────────────────

def test_resolve_and_record_rules():
    assert TW.resolve_asr_start(None, None) == 0                 # 옛 asr(기억 없음) · 명령줄 없음 → 0 (전과 같음)
    assert TW.resolve_asr_start(None, A) == A                    # 기억한 값
    assert TW.resolve_asr_start(A, None) == A                    # 기억 없는 asr(시범 sealed-work) → 준 값
    assert TW.resolve_asr_start(A, A) == A                       # 같은 값을 다시 줘도 한 번만
    for given in (0, A + 1, A * 2):
        with pytest.raises(TW.AsrStartConflict):                 # 다른 값 — 두 번 더하거나 덮지 않는다
            TW.resolve_asr_start(given, A)
    with pytest.raises(ValueError):
        TW.resolve_asr_start(-1, None)
    assert TW.recorded_asr_start({"audio": {"duration_ms": 1}}) is None
    assert TW.recorded_asr_start({"audio": {"asr_start_ms": 0}}) == 0
    assert TW.recorded_asr_start({}) is None
    for bad in (-1, True, 1.5, "1200000", None):
        with pytest.raises(ValueError):
            TW.recorded_asr_start({"audio": {"asr_start_ms": bad}})
    assert issubclass(TW.AsrStartConflict, ValueError) and TW.AsrStartConflict.code in TW.INPUT_ERRORS


def test_engine_asr_records_start(tmp_path, monkeypatch):
    """engine asr --asr-start-ms N → asr.json audio.asr_start_ms = N(전사 결과는 그대로). 주지 않으면 칸 없음."""
    from engine import __main__ as EM
    assert EA.mark_asr_start({"audio": {"duration_ms": 5}}, A)["audio"] == {"duration_ms": 5, "asr_start_ms": A}
    with pytest.raises(ValueError):
        EA.mark_asr_start({}, -1)

    def fake_transcribe(wav, **kw):
        return {"format": "chatcoach.asr", "model": {"name": "synthetic", "model_bin_sha256": "0" * 64},
                "settings": {"device": "cpu", "compute_type": "int8"}, "fallback": None, "warnings": [],
                "audio": {"duration_ms": base.ASR_DUR}, "timing": {}, "vram": None, "stats": {},
                "segments": base.asr_segments()}
    monkeypatch.setattr(EA, "transcribe", fake_transcribe)
    for extra, want in ((["--asr-start-ms", str(A)], A), ([], None)):
        out = str(tmp_path / f"asr{len(extra)}.json")
        with redirect_stdout(io.StringIO()):
            EM.main(["asr", str(tmp_path / "합성.wav"), "--out", out] + extra)
        with open(out, encoding="utf-8") as f:
            d = json.load(f)
        assert TW.recorded_asr_start(d) == want and d["audio"]["duration_ms"] == base.ASR_DUR
        assert d["segments"][0]["start_ms"] == 3000                # 시각은 wav 0초 기준 그대로(옮기는 것은 읽는 쪽)


# ── 2. 엔진 읽는 길: 명령줄 없이 기억한 값 ───────────────────────────────────────

def test_link_uses_recorded_start_without_flag(project):
    inp = L.load_project(project)                                # --asr-start-ms 없음
    assert inp.utterances[0].start_ms == 3000 + A and inp.asr_start_ms == A
    assert inp.asr_meta["asr_start_recorded"] == A and inp.asr_meta["warnings"] == []
    doc = L.build_doc(inp, start_ms=A, end_ms=base.END)
    r = base._reaction(doc)
    assert r["result"] == "graded" and r["utterance_start_ms"] == 3000 + A
    assert doc["calibration"]["asr_start_ms"] == A
    assert L.load_project(project, asr_start_ms=A).utterances[0].start_ms == 3000 + A    # 같은 값은 한 번만
    with pytest.raises(TW.AsrStartConflict):
        L.load_project(project, asr_start_ms=0)                  # 옛 습관(0)·다른 값 — 거부


def test_file_start_is_not_asr_start(project):
    """D-28 함정의 반대편: 클립 시작(--file-start-ms)을 asr 시작 자리에 주어도 asr 시각을 옮기지 않고(project.json 보정과
    상쇄), 기억한 asr 시작과 부딪히지도 않는다 — 두 값은 서로 대신하지 않는다."""
    inp = L.load_project(project, file_start_ms=A)
    assert inp.calibration_source == "project.json+cli_file_start"
    assert inp.utterances[0].start_ms == 3000 + A                # 기억한 asr 시작만 더해졌다(file_start 로 또 옮기지 않음)
    assert inp.asr_meta["warnings"] == []                        # asr 시작이 0 이 아니므로 asr_shorter 경고도 없다
    # 기억이 없고 asr_start 도 없으면 D-28 경고가 그대로 남는다(옛 파일)
    _record(os.path.join(project, "derived", "asr.json"), 0)
    w = L.load_project(project, file_start_ms=A).asr_meta["warnings"]
    assert [x["code"] for x in w] == [L.W_ASR_SHORTER]


def test_link_cli_conflict_exit2(project, tmp_path, capsys):
    out = str(tmp_path / "link.json")
    assert L.main(["--project", project, "--out", out, "--engine", "v0.2", "--asr-start-ms", "0"]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "asr_start_conflict"} and not os.path.exists(out)


def test_transcript_words_and_vectors_use_recorded_start(project, monkeypatch):
    w = TW.from_project(project)
    assert w["stats"]["asr_start_ms"] == A and w["stats"]["dropped_out_of_range"] == 0
    assert min(x["start_ms"] for x in w["windows"]) >= A           # VOD 20:00 앞 창 없음(밀리지 않음)
    with pytest.raises(TW.WordsError) as ei:
        TW.from_project(project, asr_start_ms=0)
    assert ei.value.code == "asr_start_conflict"
    monkeypatch.setattr(TV, "load_embedder", lambda models_dir=None, backend="onnx":
                        (base.S.FakeEmbedder({"보스": "boss"}, dim=32), {"status": "ok"}))
    v = TV.from_project(project)
    assert v["stats"]["asr_start_ms"] == A and min(x["start_ms"] for x in v["windows"]) >= A
    with pytest.raises(TV.VectorsError) as ei:
        TV.from_project(project, asr_start_ms=5)
    assert ei.value.code == "asr_start_conflict"


def test_viewer_rebuild_command_gets_recorded_start(project, capsys):
    """메인 창의 전사 낱말 다시 만들기 명령(viewer_flow.words_command — asr 시작을 명령줄로 주지 않는다)을 그대로 엔진에
    넘겨도 창이 VOD 20:00 부터다. 보정이 바뀌어 다시 만들 때 asr 시작을 잃던 길(D-33 전에는 늘 0 이라 문제가 없었다)."""
    cmd = vf.words_command(project, python=sys.executable)
    assert cmd is not None and "--asr-start-ms" not in cmd and "--file-start-ms" not in cmd
    args = cmd[cmd.index("transcript-words") + 1:]
    assert TW.main(args) == 0
    capsys.readouterr()
    with open(TW.default_out_path(project), encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["stats"]["asr_start_ms"] == A and min(x["start_ms"] for x in doc["windows"]) >= A
    assert TW.main(args + ["--asr-start-ms", "0"]) == 2             # 옛 습관 0 은 거부(종료 2)
    assert json.loads(capsys.readouterr().out) == {"error": "asr_start_conflict"}


def test_engine_to_ai_uses_recorded_start_and_refuses_conflict(project, capsys):
    out = os.path.join(project, "derived", "asr-link.json")
    rc = e2a.main(["--from-project", project, "--out", out, "--start-ms", str(A), "--end-ms", str(base.END), "--words"])
    assert rc == 0, capsys.readouterr()
    capsys.readouterr()
    with open(out, encoding="utf-8") as f:
        doc = json.load(f)
    [r] = [x for x in doc["reactions"] if x["chat_id"] == base._msg(1, base.CHAT_T, base.CHAT_TEXT)["chat_id"]]
    assert r["utterance_start_ms"] == 3000 + A
    with open(e2a.words_out_path(out), encoding="utf-8") as f:
        assert json.load(f)["stats"]["asr_start_ms"] == A
    os.remove(out)
    rc = e2a.main(["--from-project", project, "--out", out, "--asr-start-ms", "0"])
    assert rc == 2 and json.loads(capsys.readouterr().out) == {"error": "asr_start_conflict"}
    assert not os.path.exists(out)


# ── 3. 앵커 후보 · 위치 찾기 ─────────────────────────────────────────────────────

CUT = 300_000                        # 20분 클립 가운데 5:00 부터 15분을 뽑아 전사했다고 친다


def _cut_clip(tmp_path):
    """test_find_anchors 의 위치 찾기 합성(영상 0초 = VOD 2:03:00)에서 영상 [CUT, 끝) 만 전사한 asr — 시각을 CUT 만큼
    당기고 audio.asr_start_ms = CUT 을 적는다(새 방송이 '시작 옮기기' 로 만드는 꼴)."""
    msgs, asr, lags = tfa._synth()
    segs = [dict(s, start_ms=s["start_ms"] - CUT, end_ms=s["end_ms"] - CUT) for s in asr["segments"]
            if s["start_ms"] >= CUT]
    asr = dict(asr, segments=segs, audio={"duration_ms": tfa.CLIP_LEN - CUT, "asr_start_ms": CUT})
    return msgs, tfa._write_synth(tmp_path, msgs, asr), lags


def test_find_anchors_locate_uses_recorded_start(tmp_path):
    msgs, path, lags = _cut_clip(tmp_path)
    doc = fa.candidate_counts(str(tmp_path), asr=path, locate=True, rows=True)
    assert doc["asr_start_ms"] == CUT
    med_lag = lags[len(lags) // 2]
    # 자리 = 영상 0초의 VOD 시각(읽기 지연이 든 값). asr 시작을 잃으면 정확히 CUT(5분)만큼 뒤로 나온다
    assert abs((tfa.CLIP_START - doc["located"]["file_start_ms"]) - med_lag) <= 3000, doc["located"]
    assert all(r["video_ms"] >= CUT for r in doc["rows"]) and doc["consistent"] >= 5
    # 창 방식(아는 클립 시작 근처, 앱 '다시 찾기')도 같은 축
    win = fa.candidate_counts(str(tmp_path), asr=path, file_start_ms=tfa.CLIP_START, rows=True)
    assert win["asr_start_ms"] == CUT and all(r["video_ms"] >= CUT for r in win["rows"]) and win["consistent"] >= 5
    with pytest.raises(TW.AsrStartConflict):
        fa.candidate_counts(str(tmp_path), asr=path, locate=True, asr_start_ms=0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fa.main(["--project", str(tmp_path), "--asr", path, "--json", "--locate", "--asr-start-ms", "0"])
    assert rc == 1 and json.loads(buf.getvalue()) == {"error": "asr_start_conflict"}


def test_app_locate_command_runs_with_recorded_start(tmp_path, monkeypatch):
    """앱의 후보 찾기(app.calibration.find_candidates — 새 방송 자동 위치 추정·시간 정렬 화면)가 만드는 명령을 그대로
    find_anchors 에 넘긴다: 명령줄에 asr 시작이 없어도 자리·후보가 맞고, 그 자리로 만든 자동 추정 오프셋이 참값 근처다."""
    from app import calibration as calib
    msgs, path, lags = _cut_clip(tmp_path)
    os.makedirs(os.path.join(str(tmp_path), "derived"), exist_ok=True)
    seen = []

    def run_tool(cmd, **kw):
        seen.append(list(cmd))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fa.main(list(cmd)[3:])                          # [python, -I, find_anchors.py, ...]
        return er.ProcResult(rc, json.loads(buf.getvalue()), [], 0.0)
    monkeypatch.setattr(er, "RUN_PROCESS", run_tool)
    monkeypatch.setattr(er, "anchors_python", lambda engine_python=None: sys.executable)
    found = calib.find_candidates(str(tmp_path), locate=True)
    assert found.error is None and "--asr-start-ms" not in seen[0] and "--file-start-ms" not in seen[0]
    assert found.candidates and all(c.video_ms >= CUT for c in found.candidates)
    med_lag = lags[len(lags) // 2]
    # 저장 오프셋 축(영상 = VOD + offset): 참값 −CLIP_START, 후보 차이 중앙값에는 읽기 지연이 든다
    assert abs(found.median_offset_ms - (-tfa.CLIP_START + med_lag)) <= 3000
    assert abs(calib.lag_corrected(found.median_offset_ms) + tfa.CLIP_START) <= 6000


# ── 4. 새 방송 진행기: 범위 → 뽑기 · 기록 · 판정 범위 · 도장 · 다시 돌리기 ───────────

class RangeFakes(nb.Fakes):
    """진짜 engine asr 처럼 --asr-start-ms 를 asr.json 에 적고, 뽑은 길이를 음성 길이로 쓴다."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.last_dur = None

    def extract(self, video, wav, *, sample_rate=16000, start_ms=None, dur_ms=None, cancel=None):
        start = start_ms or 0
        self.last_dur = min(dur_ms, nb.VIDEO_MS - start) if dur_ms else nb.VIDEO_MS - start
        return super().extract(video, wav, sample_rate=sample_rate, start_ms=start_ms, dur_ms=self.last_dur,
                               cancel=cancel)

    def run_process(self, cmd, **kw):
        res = super().run_process(cmd, **kw)
        cmd = list(cmd)
        if "-m" in cmd and "asr" in cmd and "--out" in cmd:
            out = cmd[cmd.index("--out") + 1]
            with open(out, encoding="utf-8") as f:
                d = json.load(f)
            d["audio"]["duration_ms"] = self.last_dur
            if "--asr-start-ms" in cmd:
                EA.mark_asr_start(d, int(cmd[cmd.index("--asr-start-ms") + 1]))
            with open(out, "w", encoding="utf-8") as f:
                json.dump(d, f)
        return res


def _range_req(tmp_path, video, start, limit):
    r = nb._req(tmp_path, video, limit=limit)
    r.asr_start_ms = start
    return r


def _cmd(fk, kind):
    return [c for c, k in zip(fk.cmds, fk.kinds()) if k == kind]


def test_new_broadcast_range_extracts_records_and_judges_span(tmp_path, monkeypatch):
    fk = RangeFakes(tmp_path).install(monkeypatch)
    video = nb._video(tmp_path)
    res = er.run(_range_req(tmp_path, video, 300_000, 120_000))    # 영상 5:00 부터 2분
    assert res.ok, res.summary()
    assert ("extract", 300_000, 120_000) in fk.calls
    [asr_cmd] = _cmd(fk, "asr")
    assert asr_cmd[asr_cmd.index("--asr-start-ms") + 1] == "300000"
    have = er.asr_summary(er.derived_path(res.project_root, er.ASR_REL))
    assert have["asr_start_ms"] == 300_000 and have["duration_ms"] == 120_000
    [link_cmd] = _cmd(fk, "link")
    # asr 시작은 명령줄로 넘기지 않는다(엔진이 asr.json 에서 읽음) — 클립 시작과도 섞지 않는다
    assert "--asr-start-ms" not in link_cmd and "--file-start-ms" not in link_cmd
    assert link_cmd[link_cmd.index("--start-ms") + 1] == "300000"   # 오프셋 0(전체 VOD 파일): 영상 = VOD
    assert link_cmd[link_cmd.index("--end-ms") + 1] == "420000"
    stamp = json.load(open(er.derived_path(res.project_root, er.STAMP_REL), encoding="utf-8"))
    assert stamp["asr_start_ms"] == 300_000 and er.stamp_matches(res.project_root, er.project_mod.open_project(
        res.project_root).data)
    eng = res.step("engine").numbers
    assert eng["asr_start_s"] == 300 and (eng["span_start_s"], eng["span_end_s"]) == (300, 420)
    assert res.span == (300_000, 420_000) and "전사 00:05:00~00:07:00" in res.line()

    # 같은 번호로 범위 칸을 비우고 다시(시간 정렬 확정 뒤의 '판정만 다시' 와 같은 요청) → 있는 전사·판정 그대로
    n_extract = sum(1 for c in fk.calls if c[0] == "extract")
    again = er.run(nb._req(tmp_path, video))
    assert again.ok and again.step("engine").status == "skipped"
    assert sum(1 for c in fk.calls if c[0] == "extract") == n_extract
    assert again.span == (300_000, 420_000)                          # 끝 줄에도 전사 구간

    # 같은 범위를 다시 고름 → 다시 전사하지 않음
    same = er.run(_range_req(tmp_path, video, 300_000, 120_000))
    assert same.step("engine").status == "skipped" and same.step("engine").numbers["asr_redo"] == 0

    # 다른 범위(처음부터 끝까지) → 다시 전사, 시작 0 을 기록, 판정 범위 없음(영상 전체)
    whole = er.run(_range_req(tmp_path, video, 0, None))
    eng = whole.step("engine").numbers
    assert whole.ok and eng["asr_redo"] == 1 and ("extract", None, nb.VIDEO_MS) in fk.calls
    asr_cmd = _cmd(fk, "asr")[-1]
    assert asr_cmd[asr_cmd.index("--asr-start-ms") + 1] == "0"
    link_cmd = _cmd(fk, "link")[-1]
    assert "--start-ms" not in link_cmd and whole.span is None
    assert er.asr_summary(er.derived_path(whole.project_root, er.ASR_REL))["asr_start_ms"] == 0


def test_new_broadcast_range_start_beyond_video(tmp_path, monkeypatch):
    RangeFakes(tmp_path).install(monkeypatch)
    res = er.run(_range_req(tmp_path, nb._video(tmp_path), nb.VIDEO_MS, None))
    assert not res.ok and res.stopped.key == "engine" and res.stopped.code == "asr_range"
    assert "asr_range" in res.line() and er.CODE_KO["asr_range"] in res.line()


def test_kept_asr_without_range_judges_recorded_span(tmp_path, monkeypatch):
    """전사가 이미 가운데 구간(기억 있음)이고 범위 칸을 비운 채 판정만 다시 하면(도장이 다름) 판정 범위는 그 전사 구간 —
    숨은 설정 '처음 N분' 이 끼어들지 않는다."""
    fk = RangeFakes(tmp_path).install(monkeypatch)
    video = nb._video(tmp_path)
    first = er.run(_range_req(tmp_path, video, 240_000, 180_000))
    assert first.ok
    os.remove(er.derived_path(first.project_root, er.STAMP_REL))     # 옛 도장 없음 → 판정만 다시
    again = er.run(nb._req(tmp_path, video, limit=60_000))
    eng = again.step("engine").numbers
    assert again.ok and eng.get("asr_kept") == 1 and eng["asr_start_s"] == 240
    link_cmd = _cmd(fk, "link")[-1]
    assert (link_cmd[link_cmd.index("--start-ms") + 1], link_cmd[link_cmd.index("--end-ms") + 1]) == ("240000", "420000")


def test_old_stamp_without_asr_start_still_matches(tmp_path, monkeypatch):
    """D-32 도장(asr_start_ms 칸 없음) + 기억 없는 asr(늘 영상 0초부터) → 판정 다시 하지 않음."""
    RangeFakes(tmp_path).install(monkeypatch)
    res = er.run(nb._req(tmp_path, nb._video(tmp_path)))
    assert res.ok
    root = res.project_root
    asr = er.derived_path(root, er.ASR_REL)
    with open(asr, encoding="utf-8") as f:
        d = json.load(f)
    d["audio"].pop("asr_start_ms", None)
    with open(asr, "w", encoding="utf-8") as f:
        json.dump(d, f)
    data = er.project_mod.open_project(root).data
    stamp = er.stamp_doc(root, data, "link-v0.3.json")
    assert stamp["asr_start_ms"] == 0
    stamp.pop("asr_start_ms")
    er.project_mod.atomic_write(er.derived_path(root, er.STAMP_REL), json.dumps(stamp).encode("utf-8"))
    assert er.stamp_matches(root, data)
    stamp["asr_start_ms"] = 5_000                                    # 다른 전사 시작이 적힌 도장 → 다시
    er.project_mod.atomic_write(er.derived_path(root, er.STAMP_REL), json.dumps(stamp).encode("utf-8"))
    assert not er.stamp_matches(root, data)


# ── 5. 범위 칸 읽기 · 대화상자 · 메인 창 ─────────────────────────────────────────

def test_parse_range_text():
    assert er.parse_range_text("") is None and er.parse_range_text("  ") is None and er.parse_range_text(None) is None
    assert er.parse_range_text("1:20:00-2:00:00") == (4_800_000, 7_200_000)
    assert er.parse_range_text("1:20:00 ~ 2:00:00") == (4_800_000, 7_200_000)
    assert er.parse_range_text("80:00") == (4_800_000, None)
    assert er.parse_range_text("1:20:00-") == (4_800_000, None)
    assert er.parse_range_text("0:05:00.5-0:07:00") == (300_500, 420_000)
    for bad in ("-5:00", "abc", "2:00:00-1:00:00", "1:00:00-1:00:00", "1:00:00-x", "+5:00"):
        with pytest.raises(ValueError):
            er.parse_range_text(bad)
    assert er.range_to_request(None) == (None, None)
    assert er.range_to_request((300_000, 420_000)) == (300_000, 120_000)
    assert er.range_to_request((300_000, None)) == (300_000, None)
    assert er.fmt_range(300_000, 420_000) == "00:05:00~00:07:00" and er.fmt_range(4_800_000, None) == "01:20:00~끝"


def test_range_matches_and_span():
    have = {"asr_start_ms": 300_000, "duration_ms": 120_000}
    assert er.range_matches(have, 300_000, 120_000, 600_000)
    assert er.range_matches(have, 300_000, 121_500, 600_000)          # 2초 안
    assert not er.range_matches(have, 300_000, None, 600_000)         # 끝까지(5분)를 원함
    assert not er.range_matches(have, 0, 120_000, 600_000)
    assert not er.range_matches({"asr_start_ms": 0, "duration_ms": 600_000}, 300_000, None, 600_000)
    assert er.range_matches({"asr_start_ms": 0, "duration_ms": 599_400}, 0, None, 600_000)
    assert er.range_matches({"asr_start_ms": 300_000, "duration_ms": None}, 300_000, None, None)
    assert er.transcribed_span(have, None, 600_000) == (300_000, 420_000, True)
    assert er.transcribed_span({"asr_start_ms": 0, "duration_ms": 600_000}, None, 600_000) == (0, 600_000, False)
    assert er.transcribed_span({"asr_start_ms": 0, "duration_ms": 120_000}, None, 600_000)[2] is True


def test_dialog_range_field(qapp, tmp_path):
    from app.ui.new_broadcast_dialog import NewBroadcastDialog
    video = nb._video(tmp_path)
    dlg = NewBroadcastDialog(None, settings=None, labeler_id="L01", video_no=nb.VNO, video_path=video)
    assert dlg.ed_range.text() == "" and dlg.values()["asr_start_ms"] is None and dlg.values()["asr_limit_ms"] is None
    assert not dlg.problems()
    dlg.ed_range.setText("0:05:00-0:07:00")
    v = dlg.values()
    assert (v["asr_start_ms"], v["asr_limit_ms"]) == (300_000, 120_000)
    assert "전사: 영상 00:05:00~00:07:00" in dlg.hint.text()
    dlg.ed_range.setText("0:07:00-0:05:00")
    assert any("전사 범위" in p for p in dlg.problems()) and "전사 범위를 읽지 못했습니다" in dlg.hint.text()
    dlg.btn_start.click()
    assert dlg.result() != NewBroadcastDialog.Accepted
    dlg.ed_range.setText("1:00:00")
    dlg.btn_start.click()
    assert dlg.result() == NewBroadcastDialog.Accepted and dlg.values()["asr_start_ms"] == 3_600_000
    dlg.deleteLater()


def test_mainwindow_passes_range_to_request(win, tmp_path, monkeypatch):
    """대화상자 범위 칸 → Request.asr_start_ms·asr_limit_ms. 칸이 비면 숨은 설정(처음 N분)만, 칸이 있으면 칸이 이긴다."""
    got = []

    def runner(req, progress=None, cancelled=None):
        got.append(req)
        return er.Result(video_no=req.video_no)
    win.new_broadcast_runner = runner
    win.settings.setValue("session/last_labeler", "L01")
    win.settings.setValue(win.NEW_BROADCAST_LIMIT_KEY, 20)
    video = nb._video(tmp_path)

    def fill(text):
        def run(dlg):
            dlg.ed_vod.setText(nb.VNO)
            dlg.set_video(video)
            dlg.ed_range.setText(text)
            dlg.btn_start.click()
            return dlg.result()
        return run
    win.new_broadcast_dialog(run=fill("0:05:00-0:07:00"))
    win.new_broadcast_dialog(run=fill("0:05:00"))
    win.new_broadcast_dialog(run=fill(""))
    assert [(r.asr_start_ms, r.asr_limit_ms) for r in got] == [(300_000, 120_000), (300_000, None), (None, 1_200_000)]
    win.start_new_broadcast(nb.VNO, video)                          # 시간 정렬 뒤 '판정만 다시' 의 부름 — 범위 없음
    assert (got[-1].asr_start_ms, got[-1].asr_limit_ms) == (None, 1_200_000)


qapp = nb.qapp                                                       # Qt 앱·메인 창 고정물(합성 데이터 폴더)
win = nb.win
