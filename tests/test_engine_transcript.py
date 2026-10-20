"""engine/transcript.py — 합성 문장만 쓴다(실제 전사문 없음)."""
import json
import os
import subprocess
import sys

import pytest

from engine import compare as C
from engine import transcript as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_split_lines_newlines_and_spaces():
    text = "첫 줄  입니다\r\n\r\n  둘째 줄\r셋째\n\n\t넷째\t줄  \n"
    assert T.split_lines(text) == ["첫 줄 입니다", "둘째 줄", "셋째", "넷째 줄"]


def test_skip_comments_only_when_asked():
    text = "# 메모\n말한 것\n#해시태그도 말일 수 있음\n"
    assert T.split_lines(text) == ["# 메모", "말한 것", "#해시태그도 말일 수 있음"]
    assert T.split_lines(text, skip_comments=True) == ["말한 것"]


def test_decode_bom_and_reject_non_utf8():
    assert T.decode_text("﻿안녕".encode("utf-8")) == "안녕"
    assert T.decode_text("안녕".encode("utf-8")) == "안녕"
    with pytest.raises(ValueError, match="2번째 줄"):
        T.decode_text("abc\n둘째".encode("cp949"))


def test_build_shape_per_line_and_single():
    d = T.build("가나 다\n라마\n")
    assert d["source"] == "manual" and d["model"]["name"] == "manual"
    assert d["segments"] == [{"id": 0, "text": "가나 다"}, {"id": 1, "text": "라마"}]
    assert d["stats"] == {"lines": 2, "segments": 2, "chars_no_space": 5}
    s = T.build("가나 다\n라마\n", single=True)
    assert s["segments"] == [{"id": 0, "text": "가나 다 라마"}]
    assert s["stats"]["chars_no_space"] == 5
    assert T.build("\n\n")["segments"] == []


def test_compare_ratio_same_for_single_and_lines():
    asr = {"model": {"name": "large-v3-turbo"}, "segments": [{"text": "가나 다라"}, {"text": "마바"}]}
    per_line = T.build("가나다\n라마사\n")
    single = T.build("가나다\n라마사\n", single=True)
    a = C.compare(per_line, asr)
    b = C.compare(single, asr)
    assert a["char_diff_ratio"] == b["char_diff_ratio"] == round(1 / 6, 4)
    assert a["a_model"] == "manual"


def test_convert_writes_utf8_without_bom_and_compare_files_reads_it(tmp_path):
    txt = tmp_path / "manual.txt"
    txt.write_bytes("﻿오늘 날씨 좋네\r\n두 번째 말\r\n".encode("utf-8"))
    out = tmp_path / "sub" / "asr.manual.json"
    doc = T.convert(str(txt), str(out))
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8")) == doc
    assert doc["source_file"] == "manual.txt" and len(doc["source_sha256"]) == 64
    other = tmp_path / "asr.json"
    other.write_text(json.dumps({"model": {"name": "x"}, "segments": [{"text": "오늘 날씨 좋네 두 번째 말"}]},
                                ensure_ascii=False), encoding="utf-8")
    r = C.compare_files(str(out), str(other))
    assert r["char_diff_ratio"] == 0.0 and r["a_segments"] == 2


def test_convert_empty_is_error(tmp_path):
    txt = tmp_path / "empty.txt"
    txt.write_bytes(b"\r\n  \r\n")
    with pytest.raises(ValueError, match="비어"):
        T.convert(str(txt), str(tmp_path / "o.json"))
    assert not (tmp_path / "o.json").exists()


def test_cli_prints_numbers_only(tmp_path):
    txt = tmp_path / "m.txt"
    txt.write_text("비밀스러운합성문장하나\n둘째합성문장\n", encoding="utf-8")
    out = tmp_path / "m.json"
    p = subprocess.run([sys.executable, "-m", "engine", "transcript", str(txt), "--out", str(out)],
                       cwd=ROOT, capture_output=True, timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    so = p.stdout.decode("utf-8")
    assert "비밀스러운" not in so and "둘째합성" not in so
    summary = json.loads(so)
    assert summary["lines"] == 2 and summary["source"] == "manual"
    assert json.loads(out.read_text(encoding="utf-8"))["segments"][0]["text"] == "비밀스러운합성문장하나"


def test_cli_bad_encoding_returns_1(tmp_path):
    txt = tmp_path / "bad.txt"
    txt.write_bytes("한글".encode("cp949"))
    p = subprocess.run([sys.executable, "-m", "engine", "transcript", str(txt), "--out", str(tmp_path / "o.json")],
                       cwd=ROOT, capture_output=True, timeout=60)
    assert p.returncode == 1
    assert json.loads(p.stdout.decode("utf-8"))["error"] == "ValueError"
