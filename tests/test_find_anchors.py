"""tools/find_anchors.py 테스트 (합성 데이터만)."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout

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


# ---------------------------------------------------------------------------- (c) 위치 찾기 (08 D-32)
SYL = "".join(chr(0xAC00 + (i * 21 + m) * 28) for i in range(19) for m in range(21))   # 받침 없는 음절 399개
CLIP_START = 7_380_000            # 합성 정답: 영상 0초 = VOD 2:03:00
CLIP_LEN = 1_200_000


def _word(rng):
    return "".join(rng.choice(SYL) for _ in range(rng.randint(2, 4)))


def _synth(n_chats=3000, span_ms=10_800_000, reads=30, noise_utts=60, spam=True, seed=7):
    """3시간 합성 채팅 + 20분 클립(영상 0초 = VOD CLIP_START)의 발화: 채팅을 읽은 발화(지연 3~9초) + 엉뚱한 발화 +
    흔한 인사(채팅·발화 모두 여기저기). 본문은 무작위 음절 낱말(실제 채팅 아님)."""
    import random
    rng = random.Random(seed)
    ts = sorted(rng.randrange(0, span_ms) for _ in range(n_chats))
    msgs = [_msg(f"s{i:06d}", t, f"vw_synth{i % 97:07d}", " ".join(_word(rng) for _ in range(rng.randint(2, 5))),
                 masked="시**") for i, t in enumerate(ts)]
    if spam:
        for k in range(60):
            msgs.append(_msg(f"g{k:04d}", rng.randrange(0, span_ms), VB, "안녕하세요 반가워요 오늘도", masked="시**"))
    msgs.sort(key=lambda m: (m["t_ms"], m["chat_id"]))
    inside = [m for m in msgs if CLIP_START + 30_000 <= m["t_ms"] < CLIP_START + CLIP_LEN - 30_000
              and m["chat_id"].startswith("s")]
    segs = []
    lags = []
    for m in rng.sample(inside, reads):
        lag = rng.randint(3000, 9000)
        lags.append(lag)
        st = m["t_ms"] - CLIP_START + lag
        segs.append({"start_ms": st, "end_ms": st + 2500, "text": m["text"] + " 라고 하셨네요"})
    for _ in range(noise_utts):
        st = rng.randrange(0, CLIP_LEN - 5000)
        segs.append({"start_ms": st, "end_ms": st + 2000, "text": " ".join(_word(rng) for _ in range(4))})
    if spam:
        for _ in range(8):
            st = rng.randrange(0, CLIP_LEN - 5000)
            segs.append({"start_ms": st, "end_ms": st + 2000, "text": "안녕하세요 반가워요 오늘도"})
    segs.sort(key=lambda s: s["start_ms"])
    asr = {"format": "chatcoach.asr", "format_version": 1, "audio": {"duration_ms": CLIP_LEN},
           "segments": [dict(id=i, words=[], **s) for i, s in enumerate(segs)]}
    return msgs, asr, sorted(lags)


def _write_synth(tmp_path, msgs, asr):
    _write_jsonl(str(tmp_path / "chat" / "messages.jsonl"), msgs)
    os.makedirs(tmp_path / "derived", exist_ok=True)
    with open(tmp_path / "derived" / "asr.json", "w", encoding="utf-8") as f:
        json.dump(asr, f, ensure_ascii=False)
    return str(tmp_path / "derived" / "asr.json")


def test_locate_finds_clip_inside_long_vod(tmp_path):
    msgs, asr, lags = _synth()
    path = _write_synth(tmp_path, msgs, asr)
    chats = fa.text_chats(msgs)
    utts = fa.load_asr(path)
    loc = fa.locate_clip(chats, utts)
    assert loc is not None and not loc.clamped
    med_lag = lags[len(lags) // 2]
    # 자리 = 클립 시작 − 읽기 지연(중앙값 근처): 지연을 빼면 참값과 몇 초 안
    assert abs((CLIP_START - loc.file_start_ms) - med_lag) <= 3000, (loc, med_lag)
    assert loc.peak_utts >= fa.LOC_MIN_UTTS and loc.peak_chats >= fa.LOC_MIN_CHATS
    assert loc.peak > 3 * max(loc.second, 0.1)                      # 인사 잡음은 자리를 만들지 않는다
    # 2단계: 그 자리로 (a) — 서로 맞는 후보가 읽은 채팅들
    cands = fa.asr_candidates(chats, utts, identities=None, file_start_ms=loc.file_start_ms, top=20)
    med, cons = fa.consistent_subset(cands, loc.file_start_ms)
    assert len(cons) >= 10 and all(c.chat["chat_id"].startswith("s") for c in cons)
    assert all(c.utt.end_ms == c.utt.start_ms + 2500 for c in cons)
    off = med - loc.file_start_ms                                     # 저장 오프셋 축(영상 − 채팅)
    assert abs(off - (-CLIP_START + med_lag)) <= 3000


def test_locate_no_signal_returns_none(tmp_path):
    msgs, asr, _ = _synth(reads=0, spam=True)
    path = _write_synth(tmp_path, msgs, asr)
    assert fa.locate_clip(fa.text_chats(msgs), fa.load_asr(path)) is None
    assert fa.locate_clip([], fa.load_asr(path)) is None


def test_locate_clamps_negative_start():
    """클립이 VOD 0초부터이고 지연만 있으면 차이가 양수 → 클립 시작 0 으로 자르고 표시."""
    chats = [_msg(f"c{i:02d}", 60_000 + i * 30_000, VA, f"합성 낱말 {_w} 이야기" , masked="시**")
             for i, _w in enumerate(["가나다", "라마바", "사아자", "차카타", "파하거", "너더러"])]
    utts = [fa.Utt(i, c["t_ms"] + 5000, c["text"] + " 네", None, c["t_ms"] + 7000) for i, c in enumerate(chats)]
    loc = fa.locate_clip(chats, utts)
    assert loc is not None and loc.clamped and loc.file_start_ms == 0 and loc.diff_ms == 5000


def test_locate_cost_large_vod(tmp_path):
    """3만 건 × 발화 600 이 넉넉히 1분 안(실제 13시간·2만6천 건은 약 1초 — 느린 PC 에서도 흔들리지 않게 크게 잡음)."""
    import time
    msgs, asr, _ = _synth(n_chats=30_000, span_ms=46_000_000, reads=40, noise_utts=560)
    path = _write_synth(tmp_path, msgs, asr)
    chats, utts = fa.text_chats(msgs), fa.load_asr(path)
    t0 = time.perf_counter()
    loc = fa.locate_clip(chats, utts)
    assert time.perf_counter() - t0 < 60
    assert loc is not None and abs(CLIP_START - loc.file_start_ms) <= 12_000


def test_cli_locate_list_numbers_only(tmp_path):
    msgs, asr, _ = _synth()
    path = _write_synth(tmp_path, msgs, asr)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fa.main(["--project", str(tmp_path), "--asr", path, "--json", "--list", "--locate", "--top", "10"])
    out = buf.getvalue().strip().splitlines()
    assert rc == 0 and len(out) == 1
    doc = json.loads(out[0])
    assert doc["located"]["file_start_ms"] == doc["file_start_ms"] and doc["candidates"] == len(doc["rows"]) == 10
    assert set(doc["rows"][0]) == {"chat_id", "chat_ms", "video_ms", "video_end_ms", "basis", "score", "diff_ms"}
    assert all(r["chat_id"].startswith("s") and r["basis"].startswith("text") for r in doc["rows"])
    for m in msgs[:200]:                                            # 본문·발화 글은 없다
        assert m["text"] not in out[0]
    assert "라고 하셨네요" not in out[0]
    # 자리를 못 찾으면 located null · 후보 0
    m2, a2, _ = _synth(reads=0)
    p2 = _write_synth(tmp_path / "b", m2, a2)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fa.main(["--project", str(tmp_path / "b"), "--asr", p2, "--json", "--list", "--locate"])
    d2 = json.loads(buf.getvalue())
    assert rc == 0 and d2["located"] is None and d2["candidates"] == 0 and d2["rows"] == []
    # --locate 는 --asr 가 있어야
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fa.main(["--project", str(tmp_path), "--json", "--locate"])
    assert rc == 1 and json.loads(buf.getvalue()) == {"error": "locate_needs_asr"}


def test_cli_locate_text_mode(tmp_path, capsys):
    msgs, asr, _ = _synth()
    path = _write_synth(tmp_path, msgs, asr)
    assert fa.main(["--project", str(tmp_path), "--asr", path, "--locate", "--no-text"]) == 0
    out = capsys.readouterr().out
    assert "(c) 자리: 영상 0초 ≈ VOD 02:02:" in out and "--file-start-ms" not in out
