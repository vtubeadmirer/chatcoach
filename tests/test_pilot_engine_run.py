"""시범 엔진 실행 한 번에 (tools/pilot_engine_run.py, 08 D-28 5) — 합성 시범 프로젝트로만.

합성 시범 프로젝트(tmp 아래 pilot-<번호>, tests/test_pilot_unseal_day.build_pilot)·합성 채팅·합성 전사만 쓴다. 실제 시범 폴더
(chatcoach-data\\projects\\pilot-*)는 열지 않는다. 음성 뽑기·전사는 바꿔 끼운 가짜(EXTRACT·TRANSCRIBE)로, 봉인은 실제
tools/engine_to_ai.main 을 같은 프로세스에서 부른다(v0.3 의미 비교·벡터는 engine.semantic.FakeEmbedder).
"""
from __future__ import annotations

import builtins
import json
import os

import pytest

from datetime import timedelta

from app import jsonl, seal
from app import project as appproject
from engine import semantic as S
from engine import topic_vectors as TV
from tests.test_final_set import FakeLock
from tests.test_pilot_unseal_day import SALT, SECRET_NICK, SECRET_TEXT, SYNTH_VNO_BASE, T_SEAL, _messages, tree
from tools import pilot_engine_run as per

START = 60_000                  # 합성 VOD 1:00 부터
END = 360_000                   # 6:00 까지 (합성 채팅은 1:45~5:00)
MEDIA_MS = 7_200_000
SECRET_SPEECH = "합성전사표지"     # 전사문 표지 — 출력에 나오면 안 된다


@pytest.fixture(autouse=True)
def _clear_fake_locks():
    FakeLock.held = set()
    yield
    FakeLock.held = set()


def _asr_doc():
    """asr 시각(wav 0초 기준). VOD = asr + START. 채팅 n 의 시각(100_000 + n·5_000) 2초 뒤에 답하는 발화."""
    segs = []
    for i, n in enumerate((1, 3, 5, 7, 9, 19)):
        a = 100_000 + n * 5_000 + 2_000 - START
        segs.append({"id": i, "start_ms": a, "end_ms": a + 1_500, "text": f"떡볶이 치즈 {SECRET_SPEECH}",
                     "words": [{"start_ms": a, "end_ms": a + 500, "word": " 떡볶이", "prob": 0.9},
                               {"start_ms": a + 600, "end_ms": a + 1_000, "word": " 치즈", "prob": 0.9}]})
    return {"format": "chatcoach.asr", "format_version": 1, "model": {"name": "synthetic"},
            "settings": {"vad": True}, "audio": {"duration_ms": END - START}, "segments": segs}


def make_pilot(projects_root, idx) -> str:
    """라벨링 세션을 열지 않은 합성 시범 프로젝트(project.json · 보정 확인 · 합성 채팅 40줄, labels 비어 있음)."""
    pid = f"33333333-0000-4000-8000-{idx:012x}"
    p = appproject.create(str(projects_root), "pilot", SYNTH_VNO_BASE + idx, "시험", "L01", "0.0.1",
                          T_SEAL - timedelta(hours=1), pid, SALT,
                          guide=appproject.GuideSource("0.1", b"# guide\n", {"reaction": {"time_window_sec": 20}}),
                          machine4="m3fa")

    def verify(d):
        d["calibration"]["revs"][0]["status"] = "verified"
    appproject.open_project(p.root).update(verify, by="L01")
    jsonl.write_all(os.path.join(p.root, "chat", "messages.jsonl"), _messages())
    return p.root


def make_env(tmp_path, idx=1, *, media=True):
    """<tmp>\\chatcoach-data\\{projects\\pilot-…, models, sealed-work} 와 합성 영상 파일."""
    base = tmp_path / "chatcoach-data"
    projects = base / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    root = make_pilot(projects, idx)
    video = tmp_path / "videos" / f"vod-{idx}.mp4"
    video.parent.mkdir(exist_ok=True)
    video.write_bytes(b"\0" * 2048)
    if media:
        ref = {"path_abs": str(video), "path_rel": None, "size": 2048, "mtime_ns": 0, "partial_fp": "0" * 64,
               "full_sha256": None, "ntfs_file_id": None, "volume_serial": None,
               "probe": {"duration_ms": MEDIA_MS, "video_codec": None, "audio_codec": None, "width": None,
                         "height": None, "r_frame_rate": None, "avg_frame_rate": None, "vfr": False, "nb_frames": None}}

        def put(d):
            d["media"]["current"] = ref
            d["media"]["media_rev"] = 1
        appproject.open_project(root).update(put, by="L01")
    models = base / "models"
    (models / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "r1").mkdir(parents=True,
                                                                                                   exist_ok=True)
    (models / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "r1" / "model.bin").write_bytes(b"m")
    e5 = models / S.E5_SMALL["dir"] / "onnx"
    e5.mkdir(parents=True, exist_ok=True)
    (e5 / "model.onnx").write_bytes(b"m")
    (e5 / "tokenizer.json").write_bytes(b"{}")
    py = tmp_path / "venv-engine-python.exe"
    py.write_bytes(b"")
    return {"base": str(base), "projects": str(projects), "root": root, "models": str(models), "python": str(py),
            "videos": str(video.parent), "video_no": str(SYNTH_VNO_BASE + idx),
            "work": str(base / "sealed-work"), "ai": str(base / "ai")}


class Fakes:
    """바꿔 끼우는 음성 뽑기·전사. 받은 인자와 쓴 파일을 적는다."""

    def __init__(self, *, fail_asr=False):
        self.fail_asr = fail_asr
        self.extract_calls = []
        self.asr_calls = []

    def extract(self, video, wav, *, start_ms, dur_ms):
        self.extract_calls.append((start_ms, dur_ms))
        with builtins.open(wav, "wb") as f:
            f.write(b"RIFF" + b"\0" * 1024)
        return {"duration_ms": dur_ms, "elapsed_s": 0.01}

    def transcribe(self, wav, asr, log, *, python, model, device, compute_type, models_dir):
        self.asr_calls.append((model, device, compute_type))
        with builtins.open(log, "wb") as f:
            f.write(b"log\n")
        if self.fail_asr:
            with builtins.open(asr + ".tmp1", "wb") as f:      # 반쪽 임시 파일도 지워져야 한다
                f.write(b"{")
            raise per.RunError("asr_exit_1")
        with builtins.open(asr, "w", encoding="utf-8") as f:
            json.dump(_asr_doc(), f, ensure_ascii=False)
        return {"model": model, "model_bin_sha256": "e" * 64, "device": device, "compute_type": compute_type,
                "fallback": None, "warnings": [], "audio": {"duration_ms": END - START, "duration_after_vad_ms": 9000},
                "timing": {"total_s": 1.0, "realtime_x": 300.0},
                "stats": {"segment_count": 6, "word_count": 12}, "format_problems": []}


@pytest.fixture
def fakes(monkeypatch):
    f = Fakes()
    monkeypatch.setattr(per, "EXTRACT", f.extract)
    monkeypatch.setattr(per, "TRANSCRIBE", f.transcribe)
    monkeypatch.setattr(per, "backend_missing", lambda: [])
    emb = S.FakeEmbedder({"떡볶이": "food"}, dim=32)
    monkeypatch.setattr(S, "load_default", lambda *a, **k: (emb, S.status_doc("ok", info=emb.info())))
    monkeypatch.setattr(TV, "load_embedder", lambda models_dir=None, backend="onnx": (emb, {"status": "ok"}))
    return f


def _argv(env, *extra, project=True):
    a = (["--project", env["root"]] if project else []) + [
        "--start-ms", str(START), "--end-ms", str(END), "--models-dir", env["models"],
        "--engine-python", env["python"], "--videos", env["videos"]]
    return a + list(extra)


def _lines(out: str) -> list[dict]:
    return [json.loads(x) for x in out.strip().splitlines()]


def _no_secrets(text: str, env):
    for s in (SECRET_TEXT, SECRET_NICK, SECRET_SPEECH, "떡볶이", env["root"], env["base"]):
        assert s not in text


# ───────────────────────────────────────────────────────────── dry-run

def test_dry_run_reports_ready_and_writes_nothing(tmp_path, fakes, capsys):
    env = make_env(tmp_path)
    before = tree(env["base"])
    assert per.main(_argv(env, "--dry-run")) == 0
    out = capsys.readouterr().out
    _no_secrets(out, env)
    row, summary = _lines(out)
    assert row["ready"] and row["problems"] == [] and row["video"] and row["project_json"] and row["labels"] == 0
    assert row["media_ms"] == MEDIA_MS
    assert row["status"] == {"sealed": 0, "valid": 0, "unsealed": 0, "presealed": 0, "pairs_full": 0, "problems": 0}
    assert row["watch"]["violations"] == 0 and row["watch"]["writes"] == 0
    assert summary["ready"] and summary["env"] == {"engine_python": True, "whisper_model": True, "e5_model": True,
                                                    "backend_missing": []}
    assert tree(env["base"]) == before and not os.path.exists(env["ai"]) and not os.path.exists(env["work"])
    assert fakes.extract_calls == [] and fakes.asr_calls == []


def test_dry_run_not_ready_when_model_or_video_missing(tmp_path, fakes, capsys):
    env = make_env(tmp_path, media=False)
    assert per.main(_argv(env, "--dry-run", "--model", "large-v3")) == 1
    row, summary = _lines(capsys.readouterr().out)
    assert row["problems"] == ["video"] and not row["ready"]
    assert summary["env"]["whisper_model"] is False and not summary["ready"]


def test_video_found_in_videos_dir_when_project_json_has_none(tmp_path, fakes, capsys):
    """앱에서 아직 열지 않은 편: media.current 가 비어 있으면 시범 영상 폴더에서 VOD 번호로 찾는다(VOD 0 부터인 파일만)."""
    env = make_env(tmp_path, media=False)
    vids = os.path.join(env["videos"])
    with builtins.open(os.path.join(vids, f"합성 제목 [{env['video_no']}] 000000-020000.mp4"), "wb") as f:
        f.write(b"\0" * 1024)
    pj_before = tree(env["root"])
    assert per.main(_argv(env, "--dry-run")) == 0
    row, _ = _lines(capsys.readouterr().out)
    assert row["video"] and row["video_source"] == "videos_dir" and row["media_ms"] == 7_200_000
    assert per.main(_argv(env, "--pairs", "", "--engines", "v0.2")) == 0
    row, _ = _lines(capsys.readouterr().out)
    assert row["ok"] and row["video_source"] == "videos_dir" and row["work_left"] == 0
    assert tree(env["root"]) == pj_before                       # project.json 에 영상을 연결하지 않는다
    # 이름이 VOD 0 부터가 아니면 받지 않는다(파일 시각 ≠ VOD 시각)
    os.remove(os.path.join(vids, f"합성 제목 [{env['video_no']}] 000000-020000.mp4"))
    with builtins.open(os.path.join(vids, f"합성 제목 [{env['video_no']}] 000100-020000.mp4"), "wb") as f:
        f.write(b"\0")
    assert per.main(_argv(env, "--dry-run")) == 1
    row, _ = _lines(capsys.readouterr().out)
    assert row["video_source"] == "not_from_vod_start" and row["problems"] == ["video"]


def test_short_extract_stops_before_transcribe(tmp_path, fakes, capsys, monkeypatch):
    env = make_env(tmp_path)
    monkeypatch.setattr(per, "EXTRACT", lambda video, wav, *, start_ms, dur_ms: (
        builtins.open(wav, "wb").close(), {"duration_ms": dur_ms - 5_000})[1])
    assert per.main(_argv(env)) == 1
    row, _ = _lines(capsys.readouterr().out)
    assert row["error"] == "extract_length" and fakes.asr_calls == [] and row["work_left"] == 0


def test_labeled_pilot_is_refused_without_opening_labels(tmp_path, fakes, capsys):
    env = make_env(tmp_path)
    os.makedirs(os.path.join(env["root"], "labels"), exist_ok=True)
    with builtins.open(os.path.join(env["root"], "labels", "x.jsonl"), "wb") as f:
        f.write(b"{}\n")
    assert per.main(_argv(env, "--dry-run")) == 1
    row, _ = _lines(capsys.readouterr().out)
    assert row["labels"] == 1 and row["problems"] == ["labeling_started"]
    assert per.main(_argv(env)) == 1
    row, summary = _lines(capsys.readouterr().out)
    assert row["error"] == "labeling_started" and not row["ok"] and summary["failed"] == 1
    assert fakes.extract_calls == [] and not os.path.exists(env["ai"])


# ───────────────────────────────────────────────────────────── 실제 실행 (가짜 뽑기·전사, 실제 봉인)

def test_run_seals_v02_v03_with_pairs_and_removes_work_files(tmp_path, fakes, capsys, monkeypatch):
    env = make_env(tmp_path)
    seen = []
    real_seal = per.SEAL
    monkeypatch.setattr(per, "SEAL", lambda argv: (seen.append(list(argv)), real_seal(argv))[1])
    pilot_before = tree(env["root"])
    assert per.main(_argv(env)) == 0
    out = capsys.readouterr().out
    _no_secrets(out, env)
    row, summary = _lines(out)
    assert row["ok"] and "error" not in row and summary == {**summary, "ok": 1, "failed": 0, "work_left": 0}
    # 뽑기·전사 인자: VOD 범위, 기본 모델 large-v3-turbo cuda float16
    assert fakes.extract_calls == [(START, END - START)]
    assert fakes.asr_calls == [("large-v3-turbo", "cuda", "float16")]
    assert row["asr"]["fallback"] == 0 and row["asr"]["device"] == "cuda" and row["asr"]["segments"] == 6
    # engine_to_ai 인자: asr_start_ms 하나만(file_start_ms 없음), v0.2 → v0.3, 짝 셋
    assert [a[a.index("--engine") + 1] for a in seen] == ["v0.2", "v0.3"]
    for a in seen:
        assert a[a.index("--asr-start-ms") + 1] == str(START) and a[a.index("--start-ms") + 1] == str(START)
        assert a[a.index("--end-ms") + 1] == str(END) and "--file-start-ms" not in a
        assert {"--seal", "--topics", "--words", "--vectors"} <= set(a) and a[a.index("--by") + 1] == "L01"
        assert os.path.dirname(a[a.index("--asr") + 1]) == env["work"]
    # 봉인 2건, 둘 다 유효·사전 봉인·짝 셋, v0.3 는 물러나지 않음
    s2, s3 = row["seals"]
    assert s2["engine_version"] != "link-v0.3" and s3["engine_version"] == "link-v0.3" and not s3["engine_fallback"]
    for s in (s2, s3):
        assert s["exit"] == 0 and s["check"] == "valid" and s["presealed"] is True and s["new"] is True
        assert len(s["sealed_12"]) == 12 and s["size"] > 0
        assert all(s[k]["check"] == "valid" and len(s[k]["sealed_12"]) == 12 for k in per.PAIRS)
    assert row["before"]["sealed"] == 0
    assert row["after"] == {"sealed": 2, "valid": 2, "unsealed": 0, "presealed": 2, "pairs_full": 2, "problems": 0}
    st = seal.seal_status(env["root"])
    assert sorted(e.sha256[:12] for e in st.entries) == sorted([s2["sealed_12"], s3["sealed_12"]])
    # 중간 파일은 모두 지움(wav·asr·log), 시범 폴더는 그대로, 감시 위반 0
    assert row["removed"] == 3 and row["work_left"] == 0 and os.listdir(env["work"]) == []
    assert tree(env["root"]) == pilot_before
    assert row["watch"]["violations"] == 0 and row["watch"]["guarded_reads"] > 0
    assert set(row["elapsed_s"]) == {"extract", "asr", "seal", "total"}


def test_failure_still_removes_work_files_and_seals_nothing(tmp_path, fakes, capsys):
    env = make_env(tmp_path)
    fakes.fail_asr = True
    assert per.main(_argv(env)) == 1
    row, summary = _lines(capsys.readouterr().out)
    assert row["error"] == "asr_exit_1" and not row["ok"] and "seals" not in row
    assert row["removed"] == 3 and row["work_left"] == 0 and os.listdir(env["work"]) == []   # wav·log·asr 임시
    assert seal.seal_status(env["root"]).sealed_count == 0


def test_v03_fallback_stops_after_v02(tmp_path, fakes, capsys, monkeypatch):
    """의미 비교가 돌지 않으면 engine_to_ai 가 v0.3 를 봉인하지 않는다 — 실패로 보고, 중간 파일은 지움."""
    env = make_env(tmp_path)
    monkeypatch.setattr(S, "load_default", lambda *a, **k: (None, S.status_doc("model_missing")))
    assert per.main(_argv(env, "--pairs", "topics,words")) == 1
    row, _ = _lines(capsys.readouterr().out)
    s2, s3 = row["seals"]
    assert s2["check"] == "valid" and s2["vectors"] is None
    assert s3["exit"] == 1 and s3["error"] == "engine_fallback" and not row["ok"] and row["work_left"] == 0


def test_watch_blocks_reading_labels_and_writing_in_pilot(tmp_path, fakes, capsys, monkeypatch):
    env = make_env(tmp_path)
    os.makedirs(os.path.join(env["root"], "labels"), exist_ok=True)

    def nosy_seal(argv):
        with builtins.open(os.path.join(env["root"], "labels", "peek.jsonl"), "rb"):
            pass
        return 0, {}
    monkeypatch.setattr(per, "SEAL", nosy_seal)
    assert per.main(_argv(env)) == 1
    row, _ = _lines(capsys.readouterr().out)
    assert row["error"] == "open_watch:read_outside_engine:labels" and row["work_left"] == 0
    w = per.EngineWatch([env["root"]], conditions_only=False, write_ok=[env["work"]])
    with w:
        with builtins.open(os.path.join(env["root"], "project.json"), "rb"):
            pass
        os.close(os.open(os.devnull, os.O_RDWR))         # subprocess 가 여는 널 장치는 막지 않는다
        with pytest.raises(per.pud.WatchViolation):
            builtins.open(os.path.join(env["root"], "chat", "new.txt"), "wb")
        with pytest.raises(per.pud.WatchViolation):
            builtins.open(os.path.join(env["root"], "derived", "asr.json"), "rb")
        with pytest.raises(per.pud.WatchViolation):
            builtins.open(os.path.join(str(tmp_path), "elsewhere.txt"), "wb")
    assert w.summary()["violation_codes"] == {"read_outside_engine:derived": 1, "write_in_pilot_project": 1,
                                              "write_outside_allowed": 1}


# ───────────────────────────────────────────────────────────── 고르기·인자

def test_all_selects_pilots_and_env_not_ready_stops_before_anything(tmp_path, fakes, capsys):
    env = make_env(tmp_path, 1)
    make_env(tmp_path, 2)
    assert per.main(["--all", "--projects-root", env["projects"], "--dry-run", "--models-dir", env["models"],
                     "--engine-python", env["python"]]) == 0
    lines = _lines(capsys.readouterr().out)
    assert [x["project"] for x in lines[:-1]] == sorted(os.listdir(env["projects"])) and lines[-1]["projects"] == 2
    assert per.main(["--all", "--projects-root", env["projects"], "--models-dir", env["models"],
                     "--engine-python", os.path.join(str(tmp_path), "none.exe")]) == 2
    [line] = _lines(capsys.readouterr().out)
    assert line["error"] == "env_not_ready" and line["env"]["engine_python"] is False
    assert fakes.extract_calls == [] and not os.path.exists(env["work"])


@pytest.mark.parametrize("extra", [["--engines", "v0.4"], ["--engines", "v0.2,v0.2"], ["--pairs", "chat"],
                                   ["--start-ms", "10", "--end-ms", "10"], ["--by", "x"]])
def test_bad_args_exit_2(tmp_path, fakes, capsys, extra):
    env = make_env(tmp_path)
    assert per.main(_argv(env) + extra) == 2
    assert _lines(capsys.readouterr().out)[0]["error"] == "args"


def test_no_file_start_option(capsys):
    with pytest.raises(SystemExit):
        per.main(["--project", "x", "--file-start-ms", "5"])
    capsys.readouterr()
