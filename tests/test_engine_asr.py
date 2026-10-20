"""engine.asr 형식 테스트 (합성 오디오만).

앱 .venv 에는 faster_whisper 가 없으므로 그때는 모델이 필요한 테스트를 건너뛴다.
모델이 캐시(<chatcoach-data>\\models 또는 CHATCOACH_MODELS)에 없으면 내려받지 않고 건너뛴다.
"""
from __future__ import annotations

import json
import math
import os
import struct
import wave

import pytest

from engine import ASR_FORMAT
from engine.asr import _stats, validate_asr, wav_info, write_asr_json
from engine.compare import compare, edit_distance


def _write_wav(path, seconds=3.0, freq=None, sr=16000):
    """무음(freq=None) 또는 사인파 16kHz 모노 16비트."""
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        if freq is None:
            w.writeframes(b"\x00\x00" * n)
        else:
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / sr)))
                                   for i in range(n)))


def _fake_result(segments):
    return {"format": ASR_FORMAT, "format_version": 1, "created_at": "2026-10-20T00:00:00+09:00",
            "engine": {}, "model": {"name": "x", "model_bin_sha256": None}, "settings": {}, "fallback": None, "warnings": [],
            "input": {}, "audio": {"duration_ms": 1000}, "language": {}, "timing": {},
            "stats": _stats(segments), "segments": segments}


# ------------------------------------------------ 모델 없이 도는 것

def test_wav_info(tmp_path):
    p = tmp_path / "s.wav"
    _write_wav(p, 2.0)
    assert wav_info(str(p)) == {"duration_ms": 2000, "sample_rate": 16000, "channels": 1}
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"not a wav")
    assert wav_info(str(bad))["duration_ms"] is None


def test_validate_and_write_roundtrip(tmp_path):
    seg = {"id": 1, "start_ms": 0, "end_ms": 500, "text": "가나", "avg_logprob": -0.2, "no_speech_prob": 0.1,
           "words": [{"start_ms": 0, "end_ms": 500, "word": "가나", "prob": 0.9}]}
    d = _fake_result([seg])
    assert validate_asr(d) == []
    assert d["stats"]["segment_count"] == 1 and d["stats"]["char_count"] == 2
    out = tmp_path / "derived" / "asr.json"
    write_asr_json(d, str(out))
    assert json.loads(out.read_text(encoding="utf-8")) == d
    assert not [f for f in os.listdir(out.parent) if ".tmp" in f]


def test_validate_catches_problems():
    d = _fake_result([{"start_ms": 500, "end_ms": 100, "text": "", "words": [{"word": "a"}]}])
    probs = validate_asr(d)
    assert any("avg_logprob" in p for p in probs)
    assert any("시각" in p for p in probs)
    assert any("words[0].prob" in p for p in probs)
    assert validate_asr([]) == ["최상위가 dict 가 아님"]


def test_write_rejects_nan(tmp_path):
    d = _fake_result([])
    d["timing"]["x"] = float("nan")
    with pytest.raises(ValueError):
        write_asr_json(d, str(tmp_path / "a.json"))


def test_compare_numbers_only():
    assert edit_distance("abc", "abd") == 1
    assert edit_distance("", "ab") == 2
    a = _fake_result([{"start_ms": 0, "end_ms": 1, "text": "가 나 다 라", "words": []}])
    b = _fake_result([{"start_ms": 0, "end_ms": 1, "text": "가나다마", "words": []}])
    r = compare(a, b)
    assert r["a_chars"] == 4 and r["edit_distance"] == 1 and r["char_diff_ratio"] == 0.25
    assert all(not isinstance(v, str) or v == "x" for v in r.values())  # 문장이 들어가지 않는다


def test_cli_help_without_faster_whisper():
    from engine.__main__ import main
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


# ------------------------------------------------ 모델이 필요한 것

def _cached_model_dir(name="large-v3-turbo"):
    pytest.importorskip("faster_whisper")
    from faster_whisper.utils import download_model
    from engine.asr import default_models_dir
    try:
        return download_model(name, local_files_only=True, cache_dir=default_models_dir())
    except Exception:
        pytest.skip(f"모델 {name} 이 캐시에 없음 (python -m engine model-hash 로 받는다)")


@pytest.mark.parametrize("freq", [None, 440.0])
def test_transcribe_synthetic_format(tmp_path, freq):
    md = _cached_model_dir()
    from engine.asr import transcribe
    p = tmp_path / "syn.wav"
    _write_wav(p, 3.0, freq)
    res = transcribe(str(p), model=md, device="cuda", compute_type="float16")
    assert validate_asr(res) == [], validate_asr(res)
    assert res["format"] == ASR_FORMAT
    assert res["model"]["model_bin_sha256"] and len(res["model"]["model_bin_sha256"]) == 64
    assert res["audio"]["duration_ms"] == 3000
    assert res["settings"]["device"] in ("cuda", "cpu")
    assert res["settings"]["initial_prompt_sha256"] is None
    assert isinstance(res["segments"], list)  # 무음·사인파는 빈 결과일 수 있다. 형식만 본다
    assert res["stats"]["segment_count"] == len(res["segments"])


def test_transcribe_prompt_not_stored(tmp_path):
    md = _cached_model_dir()
    from engine.asr import transcribe
    p = tmp_path / "syn.wav"
    _write_wav(p, 1.0)
    res = transcribe(str(p), model=md, initial_prompt="시청자1 시청자2", vad=False, word_timestamps=False)
    assert "시청자1" not in json.dumps(res["settings"], ensure_ascii=False)
    assert res["settings"]["initial_prompt_chars"] == len("시청자1 시청자2")
    assert validate_asr(res) == []


def test_transcribe_cpu_fallback(tmp_path):
    md = _cached_model_dir()
    from engine.asr import transcribe
    p = tmp_path / "syn.wav"
    _write_wav(p, 1.0)
    # 없는 장치 이름으로 GPU 실패를 흉내 낸다 → cpu int8 로 내려가고 기록
    res = transcribe(str(p), model=md, device="cuda", compute_type="no_such_type", vad=False)
    assert res["settings"]["device"] == "cpu" and res["settings"]["compute_type"] == "int8"
    assert res["fallback"]["from_device"] == "cuda" and res["fallback"]["error"]


def test_word_alignment_indexerror_retries_without_words(tmp_path, monkeypatch):
    """faster-whisper 의 find_alignment IndexError → 같은 장치에서 단어 시각 없이 다시, warnings 에 기록."""
    md = _cached_model_dir()
    from engine import asr
    calls = []
    real = asr._run

    def fake(model_dir, wav, device, ct, kw):
        calls.append((device, kw["word_timestamps"]))
        if kw["word_timestamps"]:
            raise IndexError("boolean index did not match")
        return real(model_dir, wav, device, ct, kw)

    monkeypatch.setattr(asr, "_run", fake)
    p = tmp_path / "syn.wav"
    _write_wav(p, 1.0)
    res = asr.transcribe(str(p), model=md, vad=False)
    assert calls == [("cuda", True), ("cuda", False)]
    assert res["fallback"] is None and res["settings"]["word_timestamps"] is False
    assert res["warnings"] and res["warnings"][0].startswith("word_timestamps_failed")
    assert validate_asr(res) == []
