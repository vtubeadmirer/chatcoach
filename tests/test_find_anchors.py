"""tools/find_anchors.py 테스트 (합성 데이터만)."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    import find_anchors as fa
finally:
    sys.path.pop(0)

from app.core import timecode as tc  # noqa: E402

VA, VB, VC, VD = ("vw_aaaaaaaaaaaa", "vw_bbbbbbbbbbbb", "vw_cccccccccccc", "vw_dddddddddddd")
OFFSET = 4_000   # 합성 정답: 영상 시각 = 채팅 시각 + 4초


def _msg(cid, t, vid, text, masked="가**", role="viewer", kind="text"):
    return {"chat_id": cid, "t_ms": t, "server_time_ms": 1_000_000 + t, "type_code": 1, "kind": kind,
            "status": "NORMAL", "viewer_id": vid, "is_anonymous": False, "nickname_masked": masked,
            "role": role, "text": text, "raw_ref": {"fetch_id": "F1", "page": 0, "index": 0}}


def _seg(i, start, text, words=None):
    return {"id": i, "start_ms": start, "end_ms": start + 2000, "text": text, "avg_logprob": -0.2,
            "no_speech_prob": 0.01, "compression_ratio": 1.2, "temperature": 0.0, "words": words or []}


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


@pytest.fixture
def proj(tmp_path):
    msgs = [
        _msg("c01", 10_000, VA, "오늘 저녁 메뉴 뭐 드셨어요?", masked="가**"),
        _msg("c02", 12_000, VB, "ㅋㅋㅋㅋ", masked="나**"),
        _msg("c03", 50_000, VB, "배고프다", masked="나**"),
        _msg("c04", 100_000, VC, "인사 드립니다", masked="다**"),
        _msg("c05", 150_000, VD, "다음 게임 언제 하나요?", masked="라**"),
        _msg("c06", 151_000, VA, "와 대박", masked="가**"),
        _msg("c07", 160_000, VB, "스트리머 글", role="streamer"),
    ]
    _write_jsonl(str(tmp_path / "chat" / "messages.jsonl"), msgs)
    _write_jsonl(str(tmp_path / "chat" / "protected" / "identities.jsonl"), [
        {"viewer_id": VA, "user_id_hash": "a" * 32, "nicknames": [{"nickname": "합성닉", "first_seen_ms": 0}]},
        {"viewer_id": VB, "user_id_hash": "b" * 32, "nicknames": [{"nickname": "나비", "first_seen_ms": 0}]},
        {"viewer_id": VC, "user_id_hash": "c" * 32, "nicknames": [{"nickname": "다람쥐왕", "first_seen_ms": 0}]},
        {"viewer_id": VD, "user_id_hash": "d" * 32, "nicknames": [{"nickname": "라면", "first_seen_ms": 0}]},
    ])
    asr = {"format": "chatcoach.asr", "format_version": 1, "audio": {"duration_ms": 200_000}, "segments": [
        # 채팅 c01 을 읽음 (10초 뒤에 읽었고, 영상은 +4초 어긋남)
        _seg(0, 20_000 + OFFSET, "오늘 저녁 메뉴 뭐 드셨냐고요 저는 라면"),
        _seg(1, 60_000, "음 그렇구나"),
        # c04 작성자 닉네임 호명. '님' 단어 시작이 segment 시작보다 0.5초 뒤
        _seg(2, 105_000 + OFFSET, "다람쥐왕님 어서 오세요",
             words=[{"start_ms": 105_500 + OFFSET, "end_ms": 106_000 + OFFSET, "word": " 다람쥐왕님", "prob": 0.9},
                    {"start_ms": 106_000 + OFFSET, "end_ms": 107_000 + OFFSET, "word": " 어서", "prob": 0.9}]),
        # c05 를 읽음
        _seg(3, 158_000 + OFFSET, "다음 게임 언제 하냐면요 내일이요"),
    ]}
    os.makedirs(tmp_path / "derived", exist_ok=True)
    with open(tmp_path / "derived" / "asr.json", "w", encoding="utf-8") as f:
        json.dump(asr, f, ensure_ascii=False)
    return tmp_path


def _load(proj):
    import chat_window as cw  # find_anchors 가 tools 를 sys.path 에 넣었다
    recs = cw.sort_messages(cw.load_messages(str(proj))[0])
    return cw, recs, fa.text_chats(recs)


def test_text_chats_excludes_streamer(proj):
    _, _, chats = _load(proj)
    assert "c07" not in [c["chat_id"] for c in chats]


def test_asr_candidates_with_identities(proj):
    cw, _, chats = _load(proj)
    utts = fa.load_asr(str(proj / "derived" / "asr.json"))
    cands = fa.asr_candidates(chats, utts, identities=cw.load_identities(str(proj)), top=10)
    by = {c.chat["chat_id"]: c for c in cands}
    assert set(by) == {"c01", "c04", "c05"}
    assert by["c04"].basis.startswith("name")
    assert by["c04"].video_ms == 105_500 + OFFSET            # '님' 단어 시각을 쓴다
    assert by["c01"].basis.startswith("text") and by["c05"].basis.startswith("text")
    # 차이 = 영상 − 채팅 = 읽기 지연 + 오프셋
    assert by["c01"].diff_ms(0) == 10_000 + OFFSET
    assert by["c05"].diff_ms(0) == 8_000 + OFFSET
    assert len({c.chat["chat_id"] for c in cands}) == len(cands)
    assert len({c.utt.seg_id for c in cands}) == len(cands)
    assert cands == sorted(cands, key=lambda c: -c.score)


def test_asr_candidates_without_identities_is_weak(proj):
    _, _, chats = _load(proj)
    utts = fa.load_asr(str(proj / "derived" / "asr.json"))
    cands = fa.asr_candidates(chats, utts, identities=None, top=10)
    ids = {c.chat["chat_id"]: c for c in cands}
    assert "c04" not in ids                       # 첫 글자만으로는 MIN_SCORE 를 못 넘는다
    assert {"c01", "c05"} <= set(ids)
    assert fa.weak_name_match("다**", ["다람쥐왕"]) is True
    assert fa.weak_name_match("***", ["다람쥐왕"]) is False


def test_asr_start_and_file_start_shift(proj):
    """영상 파일이 VOD 5초부터인 클립이고 wav 를 그 파일 1초부터 뽑았으면 축이 그만큼 옮겨진다."""
    cw, recs, _ = _load(proj)
    utts = fa.load_asr(str(proj / "derived" / "asr.json"))
    sh = 5_000 + 1_000                            # wav 시각 = 원래 영상 시각 − 클립 시작 − wav 시작
    shifted = [fa.Utt(u.seg_id, u.start_ms - sh, u.text, None if u.name_ms is None else u.name_ms - sh)
               for u in utts]
    chats = fa.text_chats(recs)
    a = fa.asr_candidates(chats, utts, identities=None, top=10)
    b = fa.asr_candidates(chats, shifted, identities=None, asr_start_ms=1_000, file_start_ms=5_000, top=10)
    assert [c.chat["chat_id"] for c in a] == [c.chat["chat_id"] for c in b]
    for x, y in zip(a, b):
        assert y.video_ms == x.video_ms - 5_000          # 영상 파일 시각은 클립 시작만큼 앞당겨진다
        assert y.diff_ms(5_000) == x.diff_ms(0)          # 오프셋 축의 차이는 같다


def test_lag_window_limits(proj):
    _, _, chats = _load(proj)
    utts = fa.load_asr(str(proj / "derived" / "asr.json"))
    cands = fa.asr_candidates(chats, utts, identities=None, lag_before_ms=5_000, lag_after_ms=0, top=10)
    assert cands == []                            # 모든 정답 짝은 9초 넘게 떨어져 있다


def test_consistent_and_spread_pick():
    class C:
        def __init__(self, d, t):
            self.d, self.chat = d, {"t_ms": t}

        def diff_ms(self, fs):
            return self.d

    cs = [C(4000, 10), C(4500, 20), C(3500, 30), C(60_000, 40), C(4200, 50)]
    med, cons = fa.consistent_subset(cs, 0)
    assert med == 4200 and len(cons) == 4
    pick = fa.spread_pick(cons, key=lambda c: c.chat["t_ms"])
    assert [c.chat["t_ms"] for c in pick] == [10, 30, 50]
    assert fa.consistent_subset([], 0) == (None, [])


def test_question_candidates_spread(tmp_path):
    msgs = []
    for k in range(10):                            # 10분짜리, 1분마다 질문 하나와 잡담 여럿
        base = k * 60_000
        msgs.append(_msg(f"q{k:02d}", base + 1000, VA, f"이번 판 몇 등 할까요? {k}번"))
        for j in range(5):
            msgs.append(_msg(f"z{k:02d}{j}", base + 2000 + j * 100, VB, "ㅋㅋㅋㅋㅋ"))
    msgs.append(_msg("long", 30_000, VC, "이건 아주 아주 아주 길어서 짧은 질문이 전혀 아닌데 정말 궁금해요?"))
    chats = fa.text_chats(msgs)
    rows = fa.question_candidates(chats, top=5)
    assert len(rows) == 5
    ids = [c["chat_id"] for _, c in rows]
    assert all(i.startswith("q") for i in ids)
    ts = [c["t_ms"] for _, c in rows]
    assert ts == sorted(ts) and ts[-1] - ts[0] >= 400_000      # 앞에서 뒤까지 고르게
    assert fa.question_candidates([], top=5) == []


def test_main_asr_output(proj, capsys):
    assert fa.main(["--project", str(proj), "--asr", str(proj / "derived" / "asr.json")]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == fa.DISCLAIMER
    assert "후보 3개" in out
    assert "identities.jsonl 사용" in out
    assert "다람쥐왕" not in out.split("|")[0]      # 닉네임 칸은 없다 (본문|발화 칸에만 발화 글이 나올 수 있음)
    assert f"--anchor {tc.fmt_time(20_000 + OFFSET, always_hours=True)}={tc.fmt_time(10_000, always_hours=True)}" in out
    assert "tools\\calibrate.py" in out and "--dry-run" in out
    # 출력된 앵커 인자를 calibrate 가 그대로 읽는지
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import calibrate
    finally:
        sys.path.pop(0)
    args = [ln.strip().split(" ", 1)[1] for ln in out.splitlines() if ln.strip().startswith("--anchor ")]
    assert len(args) == 3
    for x in args:
        v, c = calibrate.parse_anchor(x)
        assert v - c > 0


def test_main_no_text_hides_bodies(proj, capsys):
    assert fa.main(["--project", str(proj), "--asr", str(proj / "derived" / "asr.json"), "--no-text"]) == 0
    out = capsys.readouterr().out
    assert "저녁 메뉴" not in out and "다람쥐왕" not in out


def test_main_question_mode(proj, capsys):
    assert fa.main(["--project", str(proj), "--top", "5"]) == 0
    out = capsys.readouterr().out
    assert fa.DISCLAIMER in out and "(b)" in out
    assert "c01" in out and "c05" in out
    assert "--anchor <영상시각>=00:00:10.000" in out


def test_main_bad_input(proj, tmp_path):
    assert fa.main(["--project", str(proj), "--top", "0"]) == 1
    assert fa.main(["--project", str(proj), "--from", "xx"]) == 1
    assert fa.main(["--project", str(tmp_path / "none")]) == 1
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "x"}', encoding="utf-8")
    assert fa.main(["--project", str(proj), "--asr", str(bad)]) == 1


def test_cli_subprocess_utf8(proj):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "find_anchors.py"), "--project", str(proj)],
                       capture_output=True, timeout=60)
    assert r.returncode == 0
    assert "후보" in r.stdout.decode("utf-8")
