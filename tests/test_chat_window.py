"""tools/chat_window.py 테스트 (합성 데이터만)."""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    import chat_window as cw
finally:
    sys.path.pop(0)

VA, VB, VC = "vw_aaaaaaaaaaaa", "vw_bbbbbbbbbbbb", "vw_cccccccccccc"


def _msg(cid, t, vid, text, kind="text", masked="가**"):
    return {"chat_id": cid, "t_ms": t, "server_time_ms": 1_000_000 + (t or 0), "type_code": 1, "kind": kind,
            "status": "NORMAL", "viewer_id": vid, "is_anonymous": vid == "anon", "nickname_masked": masked,
            "role": "viewer", "text": text, "raw_ref": {"fetch_id": "F1", "page": 0, "index": 0}}


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


@pytest.fixture
def proj(tmp_path):
    rows = [  # 일부러 시각 순서를 섞어 둔다 (수집 순서와 무관해야 함)
        _msg("c03", 70_000, VA, "세번째 글"),
        _msg("c01", 10_000, VB, "첫 글\n줄바꿈"),
        _msg("c02", 10_000, VA, "같은 시각 둘째"),
        _msg("c04", 75_000, "anon", "익명 글", masked="***"),
        _msg("c05", 80_000, VC, "후원 글", kind="donation"),
        _msg("c06", None, VC, "시각 없음"),
        _msg("c07", 200_000, VC, "멀리 있는 글"),
    ]
    _write_jsonl(str(tmp_path / "chat" / "messages.jsonl"), rows)
    _write_jsonl(str(tmp_path / "chat" / "protected" / "identities.jsonl"), [
        {"viewer_id": VA, "user_id_hash": "h" * 32,
         "nicknames": [{"nickname": "가짜닉A", "first_seen_ms": 0}, {"nickname": "바뀐닉A", "first_seen_ms": 60_000}]},
        {"viewer_id": VB, "user_id_hash": "g" * 32, "nicknames": [{"nickname": "가짜닉B", "first_seen_ms": 0}]},
    ])
    return tmp_path


def test_parse_at():
    assert cw.parse_at("1:02:03") == 3_723_000
    assert cw.parse_at("1:02:03.5") == 3_723_500
    assert cw.parse_at("3723000") == 3_723_000          # 정수는 ms
    assert cw.parse_at("3723000ms") == 3_723_000
    assert cw.parse_at("83.5s") == 83_500
    assert cw.parse_at("abc") is None
    assert cw.parse_at("-1:00") is None


def test_viewer_numbers_order_independent(proj):
    recs, warns = cw.load_messages(str(proj))
    assert warns == []
    vno = cw.viewer_numbers(recs)
    # (t_ms, chat_id) 순: c01(VB,10000) → c02(VA,10000) → … → VC 는 c05(80000)
    assert vno == {VB: 1, VA: 2, VC: 3}
    assert cw.viewer_numbers(list(reversed(recs))) == vno
    assert cw.fmt_viewer_no(VB, vno) == "시청자 0001"
    assert cw.fmt_viewer_no("anon", vno) == "익명"
    assert cw.fmt_viewer_no("vw_zzzzzzzzzzzz", vno) == "-"
    assert cw.fmt_viewer_no(VA, {VA: 12345}) == "시청자 12345"


def test_null_time_last_in_numbering():
    recs = [_msg("c9", None, VA, "x"), _msg("c1", 5, VB, "y")]
    assert cw.viewer_numbers(recs) == {VB: 1, VA: 2}


def test_select_window_filters(proj):
    recs = cw.sort_messages(cw.load_messages(str(proj))[0])
    rows = cw.select_window(recs, 60_000, 60_000, 20_000)
    assert [r["chat_id"] for r in rows] == ["c01", "c02", "c03", "c04"]      # 후원·시각 없음·먼 글 빠짐
    rows = cw.select_window(recs, 60_000, 60_000, 20_000, kinds=None)
    assert "c05" in [r["chat_id"] for r in rows]
    assert [r["chat_id"] for r in cw.select_window(recs, 60_000, 60_000, 20_000, viewer=VA)] == ["c02", "c03"]
    assert [r["chat_id"] for r in cw.select_window(recs, 60_000, 60_000, 20_000, grep=["둘째"])] == ["c02"]
    assert cw.select_window(recs, 60_000, 60_000, 20_000, grep=["둘째", "없는말"]) == []
    # 정렬 안 된 목록을 줘도 같다
    assert cw.select_window(list(reversed(recs)), 60_000, 60_000, 20_000) == \
        cw.select_window(recs, 60_000, 60_000, 20_000)


def test_nickname_at(proj):
    ident = cw.load_identities(str(proj))
    assert cw.nickname_at(ident[VA], 10_000) == "가짜닉A"
    assert cw.nickname_at(ident[VA], 70_000) == "바뀐닉A"
    assert cw.nickname_at(None, 1) is None
    assert cw.nickname_list(ident[VA]) == ["가짜닉A", "바뀐닉A"]


def test_main_output_hides_nickname_by_default(proj, capsys):
    assert cw.main(["--project", str(proj), "--at", "1:00", "--before", "60", "--after", "20"]) == 0
    out = capsys.readouterr().out
    assert "가짜닉" not in out and "바뀐닉" not in out and "경고" not in out
    assert "시청자 0001" in out and "익명" in out
    assert "c01" in out and "첫 글 줄바꿈" in out         # 줄바꿈은 공백으로
    assert "── 기준 00:01:00.0" in out
    lines = out.splitlines()
    assert lines.index(next(l for l in lines if "기준 00:01:00.0 ─" in l)) < lines.index(
        next(l for l in lines if "c03" in l))
    assert "4건" in out


def test_main_show_nickname_warns(proj, capsys):
    assert cw.main(["--project", str(proj), "--at", "70000", "--before", "1", "--after", "1",
                    "--show-nickname"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == cw.NICKNAME_WARNING
    assert "[바뀐닉A]" in out


def test_main_show_nickname_without_identities(tmp_path, capsys):
    _write_jsonl(str(tmp_path / "chat" / "messages.jsonl"), [_msg("c1", 1000, VA, "a")])
    assert cw.main(["--project", str(tmp_path), "--at", "1000", "--show-nickname"]) == 1


def test_main_bad_input(proj, tmp_path):
    assert cw.main(["--project", str(proj), "--at", "xx"]) == 1
    assert cw.main(["--project", str(proj), "--at", "1:00", "--viewer", "bad"]) == 1
    assert cw.main(["--project", str(tmp_path / "none"), "--at", "1:00"]) == 1


def test_corrupt_line_reported(tmp_path, capsys):
    p = tmp_path / "chat" / "messages.jsonl"
    _write_jsonl(str(p), [_msg("c1", 1000, VA, "a")])
    with open(p, "a", encoding="utf-8") as f:
        f.write("{깨짐\n")
        f.write(json.dumps(_msg("c2", 2000, VB, "b"), ensure_ascii=False) + "\n")
    assert cw.main(["--project", str(tmp_path), "--at", "1500", "--before", "5", "--after", "5"]) == 0
    out = capsys.readouterr().out
    assert "깨진 줄 1개" in out and "c2" in out


def _files(proj):
    return sorted(os.path.relpath(os.path.join(d, f), proj) for d, _, fs in os.walk(proj) for f in fs)


def test_writes_no_files_without_show_nickname(proj, capsys):
    before = _files(proj)
    cw.main(["--project", str(proj), "--at", "1:00", "--grep", "글"])
    assert _files(proj) == before


def test_show_nickname_appends_exposure_log_without_nickname(proj, capsys):
    """03 14-4: 켤 때마다 노출 기록. 닉네임·본문·검색어·viewer_id 는 기록에 없다."""
    before = _files(proj)
    assert cw.main(["--project", str(proj), "--at", "1:00", "--before", "60", "--after", "20",
                    "--grep", "가짜닉A", "--show-nickname"]) == 0
    assert cw.main(["--project", str(proj), "--at", "70000", "--viewer", VA, "--show-nickname"]) == 0
    added = sorted(set(_files(proj)) - set(before))
    assert added == [cw.EXPOSURE_LOG_REL]
    raw = open(cw.exposure_log_path(str(proj)), encoding="utf-8").read()
    for secret in ("가짜닉", "바뀐닉", "글", VA, "h" * 32):
        assert secret not in raw
    recs = [json.loads(x) for x in raw.splitlines()]
    assert len(recs) == 2
    assert recs[0]["what"] == "nickname_raw" and recs[0]["event"] == "view.exposed"
    assert recs[0]["window_start_ms"] == 0 and recs[0]["window_end_ms"] == 80_000
    assert recs[1]["first_chat_id"] == "c03" and recs[1]["n_rows"] == 1


def test_show_nickname_refuses_when_log_cannot_be_written(proj, capsys, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(cw.jsonl, "append", boom)
    assert cw.main(["--project", str(proj), "--at", "70000", "--show-nickname"]) == 1
    cap = capsys.readouterr()
    assert "가짜닉" not in cap.out + cap.err and "바뀐닉" not in cap.out + cap.err
