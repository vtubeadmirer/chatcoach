"""app/jsonl.py — BOM · 잘린 마지막 줄 · 중간 깨짐 · append 의 줄바꿈 보정 · NaN 거부."""
import pytest

from app import jsonl


def w(p, data: bytes):
    p.write_bytes(data)
    return str(p)


def test_bom_stripped(tmp_path):
    r = jsonl.read(w(tmp_path / "a.jsonl", b'\xef\xbb\xbf{"a":1}\n{"a":2}\n'))
    assert r.had_bom and r.ok and [x["a"] for x in r.records] == [1, 2]


def test_truncated_last_line_isolated(tmp_path):
    r = jsonl.read(w(tmp_path / "a.jsonl", b'{"a":1}\n{"a":2}\n{"a":'))
    assert r.truncated_tail and r.ok and len(r.records) == 2


def test_broken_last_line_with_newline_is_corrupt(tmp_path):
    r = jsonl.read(w(tmp_path / "a.jsonl", b'{"a":1}\n{"a":\n'))
    assert not r.truncated_tail and r.corrupt_lines == [2] and not r.ok


def test_middle_corruption_reported(tmp_path):
    r = jsonl.read(w(tmp_path / "a.jsonl", b'{"a":1}\nnot json\n[1]\n{"a":4}\n'))
    assert r.corrupt_lines == [2, 3] and not r.ok and len(r.records) == 2


def test_crlf_and_blank_lines(tmp_path):
    r = jsonl.read(w(tmp_path / "a.jsonl", b'{"a":1}\r\n\n{"a":2}\r\n'))
    assert r.ok and len(r.records) == 2


def test_append_after_truncated_line_adds_newline(tmp_path):
    p = w(tmp_path / "a.jsonl", b'{"a":1}\n{"a":')
    jsonl.append(p, {"b": 2})
    data = (tmp_path / "a.jsonl").read_bytes()
    assert data == b'{"a":1}\n{"a":\n{"b":2}\n'
    r = jsonl.read(p)
    assert r.corrupt_lines == [2] and r.records[-1] == {"b": 2}


def test_append_new_and_normal(tmp_path):
    p = str(tmp_path / "n.jsonl")
    jsonl.append(p, {"k": "한글"})
    jsonl.append(p, {"k": 2})
    assert (tmp_path / "n.jsonl").read_bytes() == '{"k":"한글"}\n{"k":2}\n'.encode("utf-8")


def test_nan_rejected_on_write_and_read(tmp_path):
    with pytest.raises(ValueError):
        jsonl.dumps_line({"x": float("nan")})
    with pytest.raises(ValueError):
        jsonl.append(str(tmp_path / "x.jsonl"), {"x": float("inf")})
    r = jsonl.read(w(tmp_path / "b.jsonl", b'{"x":NaN}\n{"x":Infinity}\n{"x":1}\n'))
    assert r.corrupt_lines == [1, 2] and r.records == [{"x": 1}]


def test_write_all_refuses_existing(tmp_path):
    p = str(tmp_path / "w.jsonl")
    assert jsonl.write_all(p, [{"a": 1}, {"a": 2}]) == 2
    with pytest.raises(FileExistsError):
        jsonl.write_all(p, [])
    assert not (tmp_path / "w.jsonl.tmp").exists()
