"""app/analysis.py — 합성 2초 영상(앞 1초 440Hz 사인파, 뒤 1초 무음)으로 probe·추출·무음·파형을 검사한다."""
import os
import subprocess
import wave

import pytest

from app import analysis, paths


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    ffmpeg = paths.find_tool("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg 없음")
    d = tmp_path_factory.mktemp("clip 한글")  # 한글·공백 경로
    p = d / "합성 영상.mp4"
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
           "-f", "lavfi", "-i", "aevalsrc='if(lt(t,1),0.5*sin(2*PI*440*t),0)':s=48000:d=2",
           "-c:v", "mpeg4", "-c:a", "aac", "-ac", "2", "-shortest", str(p)]
    subprocess.run(cmd, check=True, capture_output=True, creationflags=analysis.CREATE_NO_WINDOW)
    return str(p)


def test_probe(clip):
    info = analysis.probe(clip)
    assert abs(info["duration_ms"] - 2000) <= 100
    assert "mp4" in info["container"]
    assert info["video_codec"] == "mpeg4"
    assert (info["width"], info["height"]) == (160, 120)
    assert info["r_frame_rate"] == "25/1"
    assert info["fps"] == 25.0
    assert info["vfr"] is False
    assert info["audio_codec"] == "aac"
    assert info["sample_rate"] == 48000
    assert info["channels"] == 2
    assert info["has_audio"] is True


def test_probe_rejects_url_and_unc():
    for p in ("http://127.0.0.1/a.mp4", r"\\server\share\a.mp4", "a.mp4"):
        with pytest.raises(analysis.AnalysisError):
            analysis.probe(p)


def test_probe_missing_file(tmp_path):
    with pytest.raises(analysis.AnalysisError):
        analysis.probe(str(tmp_path / "없음.mp4"))


def _wav(path):
    with wave.open(path, "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()


def test_extract_full(clip, tmp_path):
    out = tmp_path / "출력" / "a.wav"
    r = analysis.extract_audio_wav(clip, str(out))
    sr, ch, sw, n = _wav(str(out))
    assert (sr, ch, sw) == (16000, 1, 2)
    assert abs(n - 32000) <= 1600
    assert r["sample_rate"] == 16000 and r["channels"] == 1
    assert abs(r["duration_ms"] - 2000) <= 100
    assert r["elapsed_s"] >= 0
    assert os.listdir(out.parent) == ["a.wav"]  # 임시 파일이 남지 않는다


def test_extract_range(clip, tmp_path):
    out = tmp_path / "b.wav"
    analysis.extract_audio_wav(clip, str(out), start_ms=500, dur_ms=1000, sample_rate=8000)
    sr, ch, _, n = _wav(str(out))
    assert (sr, ch) == (8000, 1)
    assert abs(n - 8000) <= 400


def test_extract_replaces_existing(clip, tmp_path):
    out = tmp_path / "c.wav"
    out.write_bytes(b"old")
    analysis.extract_audio_wav(clip, str(out), dur_ms=300)
    assert _wav(str(out))[3] > 0


def test_extract_cancel_leaves_nothing(clip, tmp_path):
    out = tmp_path / "d.wav"
    with pytest.raises(analysis.Cancelled):
        analysis.extract_audio_wav(clip, str(out), cancel=lambda: True, timeout=30)
    assert os.listdir(tmp_path) == []


def test_extract_timeout(clip, tmp_path):
    # 아주 작은 제한 시간 — 끝나기 전에 걸리면 ToolTimeout, 그 전에 끝나면 정상 (어느 쪽이든 임시 파일은 없다)
    out = tmp_path / "e.wav"
    try:
        analysis.extract_audio_wav(clip, str(out), timeout=0.0)
    except analysis.ToolTimeout:
        assert not out.exists()
    assert not [f for f in os.listdir(tmp_path) if ".part-" in f]


def test_extract_bad_args(clip, tmp_path):
    with pytest.raises(ValueError):
        analysis.extract_audio_wav(clip, str(tmp_path / "x.wav"), start_ms=-1)
    with pytest.raises(ValueError):
        analysis.extract_audio_wav(clip, str(tmp_path / "x.wav"), dur_ms=0)
    with pytest.raises(analysis.AnalysisError):
        analysis.extract_audio_wav(clip, r"\\server\share\x.wav", dur_ms=100)


def test_detect_silence(clip):
    spans = analysis.detect_silence(clip, noise_db=-35, min_sec=0.6)
    assert len(spans) == 1, spans
    s, e = spans[0]
    assert all(isinstance(v, int) for v in (s, e))
    assert abs(s - 1000) <= 150
    assert abs(e - 2000) <= 150


def test_detect_silence_none_when_long_min(clip):
    assert analysis.detect_silence(clip, min_sec=1.5) == []


def test_waveform(clip):
    pts = analysis.compute_waveform(clip, 100)
    assert len(pts) == 100
    assert max(pts) == pytest.approx(1.0)
    assert min(pts) >= 0.0
    loud = sum(pts[5:45]) / 40
    quiet = sum(pts[55:95]) / 40
    assert loud > 0.5 and quiet < 0.05, (loud, quiet)


def test_waveform_more_points_than_samples(clip):
    pts = analysis.compute_waveform(clip, 50_000, sample_rate=8000)  # 2초 × 8000 = 16000 샘플 < 50000
    assert len(pts) == 50_000


def test_waveform_cancel(clip):
    with pytest.raises(analysis.Cancelled):
        analysis.compute_waveform(clip, 10, cancel=lambda: True)


def test_decode_output():
    assert analysis.decode_output("가나".encode("utf-8")) == "가나"
    assert analysis.decode_output("가나".encode("cp949")) == "가나"
    assert analysis.decode_output(b"") == ""
    assert analysis.decode_output(b"\xff\xff\xff") != ""
