"""engine/transcript_words.py · app/core/textnorm.py · schemas/transcript-words.schema.json — 합성 데이터만 쓴다.

asr 문장·보정값·채팅은 모두 만든 것이다(이모티콘 코드 {:t_1:} 도 합성). VOD 시각은 손으로 계산한 값과 비교한다.
보정식(03 4-2): 영상 파일 시각 = VOD 시각 + offset_ms  →  VOD 시각 = 영상 파일 시각 − offset_ms.
"""
import hashlib
import json
import os
import re
import unicodedata

import pytest

from app.core import ids
from app.core import textnorm as TN
from app.core.timecode import Anchor, Calibration
from engine import retrieve as R
from engine import transcript_words as TW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALT = b"\x03" * 32
CREATED = "2026-10-22T00:00:00+09:00"


# ── 옛 retrieve 정규화(옮기기 전 코드를 그대로 둔다) ↔ textnorm ─────────────────────

_OLD_EMOTE = re.compile(r"\{:[^{}:]{1,40}:\}")
_OLD_JAMO = re.compile(r"[ㄱ-ㆎ]+")
_OLD_NON_WORD = re.compile(r"[^0-9a-z가-힣\s]+")
_OLD_PARTICLES = ("에서는", "으로는", "에서", "으로", "한테", "까지", "부터", "처럼", "보다", "이랑",
                  "은", "는", "이", "가", "을", "를", "에", "도", "로", "랑", "의", "만", "와", "과")


def _old_tokens(text):
    s = _OLD_EMOTE.sub(" ", unicodedata.normalize("NFKC", text or ""))
    s = _OLD_JAMO.sub(" ", s).lower()
    s = _OLD_NON_WORD.sub(" ", s)
    out = set()
    for w in s.split():
        cand = {w}
        for p in _OLD_PARTICLES:
            if len(w) > len(p) + 1 and w.endswith(p):
                cand.add(w[: -len(p)])
                break
        out.update(x for x in cand if len(x) >= 2)
    return frozenset(out)


def _old_squash(s):
    return re.sub(r"[^0-9a-z가-힣]+", "", unicodedata.normalize("NFKC", s or "").lower())


def _old_visible_len(t):
    return len(re.sub(r"\s+", "", _OLD_EMOTE.sub("", t or "")))


SAMPLES = ["보스 공략을 시작합니다", "떡볶이에서는 어묵이 최고 ㅋㅋㅋ", "{:t_1:} 제주로 가자!!", "ＡＢＣ 게임 Ｌｖ３",
           "ㅠㅠ 아이템이 안 나와요?", "", "   ", "a b 가", "한테 에서 으로", "호텔보다 숙소가 좋아요 {:t_2:}{:t_3:}",
           "１２３ 456 칠팔구", "과자와 과일과", "@시청자 링크 www.example.com"]


@pytest.mark.parametrize("text", SAMPLES)
def test_textnorm_same_as_old_retrieve(text):
    assert TN.tokens(text) == _old_tokens(text)
    assert TN.squash(text) == _old_squash(text)
    assert TN.visible_len(text) == _old_visible_len(text)


def test_retrieve_uses_textnorm():
    """retrieve 는 옮긴 모듈을 그대로 쓴다(같은 함수 객체) — 엔진과 앱이 같은 규칙."""
    assert R.tokens is TN.tokens and R._squash is TN.squash and R.visible_len is TN.visible_len
    assert R._EMOTE is TN.EMOTE_RE and R._PARTICLES is TN.PARTICLES


def test_textnorm_tokens_and_ngrams():
    assert TN.tokens("보스 공략을 {:t_1:} ㅋㅋ") == {"보스", "공략", "공략을"}
    assert TN.strip_particle("공략을") == "공략" and TN.strip_particle("을을") == "을을"
    assert TN.char_ngrams("보스 공략 시작", 5) == {"보스공략시", "스공략시작"}
    assert TN.char_ngrams("보스", 5) == {"보스"} and TN.char_ngrams("ㅋㅋ !!", 5) == frozenset()
    with pytest.raises(ValueError):
        TN.char_ngrams("보스", 0)
    assert TN.normalize("  ＡＢ   보스\t공략?  ") == "ab 보스 공략?" and TN.normalize(None) == ""


# ── 보정 → VOD 시각 (손 계산) ───────────────────────────────────────────────

def seg(start, end, text, words=None):
    return {"start_ms": start, "end_ms": end, "text": text, "avg_logprob": -0.1, "no_speech_prob": 0.01,
            "words": [{"start_ms": s, "end_ms": e, "word": w, "prob": 0.9} for s, e, w in (words or [])]}


def wmap(part):
    return {w["start_ms"]: w["words"] for w in part["windows"]}


def test_single_offset_word_times_hand_computed():
    """offset 2500: 파일 12000·12600·13900 → VOD 9500·10100·11400 → 창 0 · 1 · 1."""
    segs = [seg(12000, 14000, "보스 공략을 시작", [(12000, 12500, " 보스"), (12600, 13200, " 공략을"),
                                                   (13900, 14000, " 시작")])]
    part = TW.build_windows(segs, Calibration(offset_ms=2500))
    assert wmap(part) == {0: ["보스"], 10000: ["공략", "공략을", "시작"]}
    assert [(w["end_ms"], w["segments"]) for w in part["windows"]] == [(10000, 1), (20000, 1)]
    assert part["range"] == {"start_ms": 0, "end_ms": 20000}
    assert part["stats"]["word_time_segments"] == 1 and part["stats"]["spread_segments"] == 0


def test_segment_without_words_spreads_over_windows():
    """낱말 시각 없음: 파일 25000~37000, offset 2500 → VOD 22500~34500 → 창 20000·30000 두 곳에 같은 낱말."""
    part = TW.build_windows([seg(25000, 37000, "떡볶이 어묵 양념")], Calibration(offset_ms=2500))
    # '떡볶이' 는 끝의 '이' 를 조사로 보고 뗀 꼴 '떡볶' 도 들어간다(textnorm 규칙 — 앱 채점기도 같은 꼴을 만든다)
    assert wmap(part) == {20000: ["떡볶", "떡볶이", "양념", "어묵"], 30000: ["떡볶", "떡볶이", "양념", "어묵"]}
    # 끝이 창 경계에 딱 맞으면 다음 창에 넣지 않는다: VOD 22500~30000 → 창 20000 하나
    part = TW.build_windows([seg(25000, 32500, "떡볶이")], Calibration(offset_ms=2500))
    assert list(wmap(part)) == [20000]
    # 길이 0 구간은 시작 창 하나
    part = TW.build_windows([seg(32500, 32500, "떡볶이")], Calibration(offset_ms=2500))
    assert list(wmap(part)) == [30000]


def test_clip_file_negative_offset():
    """클립(파일 0초 = VOD 30초): project.json 꼴 offset −30000 → 파일 0·4500 → VOD 30000·34500."""
    pdata = {"calibration": {"active_rev": 1, "revs": [
        {"rev": 0, "offset_ms": 0, "status": "unverified"},
        {"rev": 1, "offset_ms": -30000, "method": "single_offset", "status": "verified"}]}}
    cal, fs, src = TW.calibration_for(pdata)
    assert (src, fs, cal.rev) == ("project.json", 0, 1)
    part = TW.build_windows([seg(0, 1000, "제주"), seg(4500, 5000, "비행기")], cal, fs)
    assert wmap(part) == {30000: ["비행기", "제주"]}
    info = TW.calibration_info(cal, fs, src)
    assert info == {"source": "project.json", "rev": 1, "method": "single_offset", "status": "verified",
                    "offset_ms": -30000, "file_start_in_vod_ms": 0}


def test_cli_file_start_restores_clip_start():
    """project.json offset_ms(= 어긋남 − 클립 시작) −29000 에 --file-start-ms 30000 을 주면 내부 어긋남 1000.
    파일 4000 → VOD 4000 + 30000 − 1000 = 33000. 기록 꼴 offset 은 그대로 −29000."""
    pdata = {"calibration": {"active_rev": 0, "revs": [{"rev": 0, "offset_ms": -29000, "status": "verified"}]}}
    cal, fs, src = TW.calibration_for(pdata, file_start_ms=30000)
    assert (src, fs, cal.offset_ms) == ("project.json+cli_file_start", 30000, 1000)
    assert wmap(TW.build_windows([seg(4000, 4100, "호텔")], cal, fs)) == {30000: ["호텔"]}
    assert TW.calibration_info(cal, fs, src)["offset_ms"] == -29000


def test_cli_offset_wins_over_project():
    pdata = {"calibration": {"active_rev": 0, "revs": [{"rev": 0, "offset_ms": 2500, "status": "verified"}]}}
    cal, fs, src = TW.calibration_for(pdata, offset_ms=-7000)
    assert (src, fs, cal.offset_ms, cal.status) == ("cli", 0, -7000, "unverified")
    # 파일 1000 → VOD 8000
    assert wmap(TW.build_windows([seg(1000, 1200, "숙소")], cal, fs)) == {0: ["숙소"]}
    assert wmap(TW.build_windows([seg(5000, 5200, "숙소")], cal, fs)) == {10000: ["숙소"]}


def test_default_calibration_when_none():
    cal, fs, src = TW.calibration_for({})
    assert (src, fs, cal.offset_ms, cal.status) == ("default", 0, 0, "unverified")


def test_piecewise_linear_inverse():
    """앵커 (VOD 0, 파일 1000) · (VOD 100000, 파일 103000): 파일 52000 → VOD 50000 (선형 보간 역)."""
    cal = Calibration(method="piecewise_linear", anchors=(Anchor("a1", 0, 1000), Anchor("a2", 100000, 103000)))
    part = TW.build_windows([seg(52000, 52100, "공항", [(52000, 52100, "공항")])], cal)
    assert wmap(part) == {50000: ["공항"]}


def test_before_vod0_dropped_and_range_filter():
    """offset 5000: 파일 2000 → VOD −3000 (버림). 범위 [10000, 20000) 밖 낱말은 넣지 않는다."""
    segs = [seg(2000, 2200, "보스", [(2000, 2200, "보스")]),
            seg(16000, 16500, "아이템", [(16000, 16500, "아이템")]),          # VOD 11000
            seg(26000, 26500, "던전", [(26000, 26500, "던전")])]               # VOD 21000
    part = TW.build_windows(segs, Calibration(offset_ms=5000), start_ms=10000, end_ms=20000)
    assert wmap(part) == {10000: ["아이템"]}
    st = part["stats"]
    assert st["dropped_before_vod0"] == 1 and st["dropped_out_of_range"] == 1 and st["segments_used"] == 1
    assert part["range"] == {"start_ms": 10000, "end_ms": 20000}
    with pytest.raises(TW.WordsError) as e:
        TW.build_windows(segs, Calibration(), start_ms=5, end_ms=1)
    assert e.value.code == "bad_range"


def test_no_sentence_body_only_sorted_sets():
    """창마다 겹치지 않는 낱말을 글자 순으로만 둔다 — 말한 순서·되풀이가 남지 않는다. 긴 덩어리·기호는 버린다."""
    text = "제주 여행 제주 여행 비행기 예약 {:t_9:} ㅋㅋ 가나다라마바사아자차카타파하"
    part = TW.build_windows([seg(0, 3000, text)], Calibration())
    words = part["windows"][0]["words"]
    assert words == sorted(set(words)) == ["비행기", "여행", "예약", "제주"]
    assert part["stats"]["dropped_long_words"] == 1
    blob = json.dumps(part, ensure_ascii=False)
    assert "제주 여행" not in blob and "{:" not in blob and "ㅋ" not in blob


def test_cap_keeps_longer_words():
    cfg = TW.WordsConfig(max_words_per_window=2)
    part = TW.build_windows([seg(0, 100, "가나 다라마 바사아자")], Calibration(), cfg=cfg)
    assert part["windows"][0]["words"] == ["다라마", "바사아자"] and part["stats"]["capped_words"] == 1


def test_config_checks():
    for bad in ({"window_ms": 0}, {"window_ms": True}, {"max_word_len": 13}, {"max_words_per_window": 0}):
        with pytest.raises(ValueError):
            TW.WordsConfig(**bad)
    assert TW.WordsConfig().sha256() == TW.WordsConfig().sha256()
    assert TW.WordsConfig(window_ms=5000).sha256() != TW.WordsConfig().sha256()


# ── 문서 · 검사 · 스키마 ────────────────────────────────────────────────────

def _doc(**kw):
    segs = [seg(12000, 14000, "보스 공략을 시작", [(12000, 12500, "보스"), (12600, 13200, "공략을"), (13900, 14000, "시작")]),
            seg(25000, 37000, "떡볶이 어묵")]
    cal = Calibration(offset_ms=2500, rev=1, status="verified")
    part = TW.build_windows(segs, cal)
    return TW.build_doc(part, cfg=TW.WordsConfig(), run_id="words-test", created_at=CREATED,
                        calibration=TW.calibration_info(cal, 0, "project.json"), project_id="p1", video_no="0",
                        asr_sha256="a" * 64, **kw)


def test_doc_validates_and_is_deterministic():
    a, b = _doc(), _doc()
    assert TW.validate(a) == [] and TW.dump(a) == TW.dump(b)
    assert a["engine"]["textnorm"] == TN.VERSION and a["time_axis"] == "vod_ms"


@pytest.mark.parametrize("breaker,code", [
    (lambda d: d.pop("calibration"), "calibration 없음"),
    (lambda d: d.update(extra=1), "모르는 칸"),
    (lambda d: d["windows"][0]["words"].append("두 낱말"), "words 형식"),
    (lambda d: d["windows"][0]["words"].insert(0, "힣힣"), "words 정렬·겹침"),
    (lambda d: d["windows"][0].update(start_ms=5), "창 경계"),
    (lambda d: d["windows"].reverse(), "순서·겹침"),
    (lambda d: d["calibration"].update(source="guess"), "calibration 형식"),
    (lambda d: d["stats"].update(note="x"), "stats 는 숫자만"),
    (lambda d: d.update(time_axis="video_ms"), "time_axis 값"),
])
def test_validate_catches(breaker, code):
    d = _doc()
    breaker(d)
    probs = TW.validate(d)
    assert any(code in p for p in probs), probs


def _schema():
    with open(os.path.join(ROOT, "schemas", "transcript-words.schema.json"), encoding="utf-8") as f:
        return json.load(f)


def test_schema_matches_module():
    s = _schema()
    assert tuple(s["required"]) == TW._TOP_REQUIRED and set(s["properties"]) == set(TW._TOP_KEYS)
    assert s["properties"]["schema"]["const"] == TW.SCHEMA and s["properties"]["schema_version"]["const"] == 1
    assert s["$defs"]["word"]["pattern"] == TW.WORD_RE.pattern
    assert s["properties"]["windows"]["items"]["properties"]["words"]["maxItems"] == TW.MAX_WORDS_PER_WINDOW
    assert s["properties"]["calibration"]["properties"]["source"]["enum"] == list(TW.CAL_SOURCES)


def test_schema_with_jsonschema_if_available():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(_doc(), _schema())


# ── 프로젝트 · 명령줄 · 시범 거부 ────────────────────────────────────────────

def vid(n):
    return ids.viewer_id(SALT, hashlib.sha1(f"fixture-{n}".encode()).hexdigest())


def _msg_rec(n, t_ms, text):
    uid = hashlib.sha1(f"fixture-{n}".encode()).hexdigest()
    return {"chat_id": ids.chat_id_base("0", 1_700_000_000_000 + t_ms, uid, 1, text), "t_ms": t_ms,
            "server_time_ms": 1_700_000_000_000 + t_ms, "type_code": 1, "kind": "text", "status": "NORMAL",
            "viewer_id": vid(n), "is_anonymous": False, "nickname_masked": "시*", "role": "viewer", "text": text,
            "raw_ref": {"fetch_id": "F20261022-test", "page": 0, "index": 0}}


def make_project(root, name="합성 프로젝트-낱말", offset_ms=2500):
    p = os.path.join(str(root), name)
    os.makedirs(os.path.join(p, "chat"))
    os.makedirs(os.path.join(p, "derived"))
    with open(os.path.join(p, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": "p-synth", "broadcast": {"video_no": "0"},
                   "calibration": {"active_rev": 1, "revs": [
                       {"rev": 0, "offset_ms": 0, "status": "unverified"},
                       {"rev": 1, "offset_ms": offset_ms, "method": "single_offset", "status": "verified"}]}}, f)
    with open(os.path.join(p, "chat", "messages.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for i, (t, text) in enumerate([(9000, "보스 언제 잡아요?"), (21000, "떡볶이 맛있겠다 {:t_1:}")]):
            f.write(json.dumps(_msg_rec(1 + i, t, text), ensure_ascii=False) + "\n")
    with open(os.path.join(p, "chat", "fetch-F20261022-test.json"), "w", encoding="utf-8") as f:
        json.dump({"fetch_id": "F20261022-test", "video_no": "0"}, f)
    asr = {"format": "chatcoach.asr", "format_version": 1, "model": {"name": "synthetic"}, "settings": {"vad": True},
           "audio": {"duration_ms": 60000},
           "segments": [seg(12000, 14000, "보스 공략을 시작", [(12000, 12500, " 보스"), (12600, 13200, " 공략을"),
                                                             (13900, 14000, " 시작")]),
                        seg(25000, 37000, "떡볶이 어묵 양념")]}
    with open(os.path.join(p, "derived", "asr.json"), "w", encoding="utf-8") as f:
        json.dump(asr, f, ensure_ascii=False)
    return p


def test_from_project_hand_values(tmp_path):
    p = make_project(tmp_path)
    doc = TW.from_project(p, now=None)
    assert TW.validate(doc) == []
    assert {w["start_ms"]: w["words"] for w in doc["windows"]} == {
        0: ["보스"], 10000: ["공략", "공략을", "시작"], 20000: ["떡볶", "떡볶이", "양념", "어묵"],
        30000: ["떡볶", "떡볶이", "양념", "어묵"]}
    assert doc["target"]["project_id"] == "p-synth" and doc["target"]["video_no"] == "0"
    with open(os.path.join(p, "derived", "asr.json"), "rb") as f:
        assert doc["target"]["asr_sha256"] == hashlib.sha256(f.read()).hexdigest()
    assert doc["calibration"]["rev"] == 1 and doc["calibration"]["offset_ms"] == 2500


def test_cli_default_out_numbers_only(tmp_path, capsys):
    p = make_project(tmp_path)
    assert TW.main(["--project", p]) == 0
    out = os.path.join(p, "derived", "asr-words.json")
    printed = capsys.readouterr().out
    with open(out, encoding="utf-8") as f:
        doc = json.load(f)
    assert TW.validate(doc) == [] and doc["stats"]["windows"] == 4
    for w in doc["windows"]:
        for x in w["words"]:
            assert x not in printed                    # 표준 출력에 낱말을 내지 않는다
    assert not [n for n in os.listdir(os.path.dirname(out)) if ".part-" in n]


def test_engine_main_passthrough(tmp_path, capsys):
    from engine.__main__ import main as engine_main
    p = make_project(tmp_path)
    out = os.path.join(p, "derived", "asr-words-20.json")          # 보호 구역 안(밖은 거부 — 아래 시험)
    assert engine_main(["transcript-words", "--project", p, "--out", out, "--window-sec", "20"]) == 0
    with open(out, encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["window_ms"] == 20000 and [w["start_ms"] for w in doc["windows"]] == [0, 20000]


def test_cli_refuses_out_outside_protected_zone(tmp_path, capsys, monkeypatch):
    """--out 은 그 프로젝트 보호 구역 'derived/asr*' 안만(검토 2026-10-23). 밖이면 asr 을 읽기 전에 종료 2, 아무것도 쓰지 않는다."""
    p = make_project(tmp_path)
    bad = [os.path.join(p, "derived", "words.json"),                 # 프로젝트 안, 보호 구역 밖 이름
           os.path.join(p, "asr-words.json"),                         # derived 밖
           os.path.join(str(tmp_path), "elsewhere", "asr-words.json"),  # 프로젝트 밖(공용 TEMP 같은 곳)
           p]                                                         # 프로젝트 폴더 자체
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    for out in bad:
        assert TW.main(["--project", p, "--out", out]) == 2
    monkeypatch.undo()
    assert not [f for f in opened if f.endswith("asr.json")]           # 입력을 읽기 전에 거부
    for out in bad[:3]:
        assert not os.path.exists(out)
    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    assert [x["error"] for x in lines] == ["out_unprotected"] * len(bad)
    assert TW.out_protected(p, os.path.join(p, "DERIVED", "ASR-x.json"))
    assert TW.out_protected(p, os.path.join(p, "derived", "asr_dir", "a.json"))
    assert not TW.out_protected(p, os.path.join(p, "derived", "x-asr.json"))


def test_refuses_pilot_before_reading(tmp_path, capsys, monkeypatch):
    """시범(pilot-*)은 아무것도 읽기 전에 거부한다. --out 이 시범 폴더 안이어도 거부."""
    pilot = os.path.join(str(tmp_path), "pilot-00000001")
    os.makedirs(pilot)
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    with pytest.raises(TW.WordsError) as e:
        TW.from_project(pilot)
    assert e.value.code == "pilot_locked"
    assert TW.main(["--project", pilot]) == 1
    assert not [f for f in opened if "pilot-" in f]
    monkeypatch.undo()
    assert "pilot_locked" in capsys.readouterr().out
    assert not os.path.exists(os.path.join(pilot, "derived"))
    p = make_project(tmp_path)
    bad_out = os.path.join(str(tmp_path), "pilot-00000002", "w.json")
    assert TW.main(["--project", p, "--out", bad_out]) == 1 and not os.path.exists(bad_out)
    assert TW.inside_pilot(bad_out) and not TW.inside_pilot(p)


def test_refuses_pilot_asr_path(tmp_path, capsys, monkeypatch):
    """--asr 가 시범 폴더 안이면 시범 아닌 프로젝트 이름으로도 읽지 않는다(검토 2026-10-23). 합성 폴더만 쓴다."""
    p = make_project(tmp_path)
    pilot_asr = os.path.join(str(tmp_path), "pilot-00000003", "derived", "asr.json")
    os.makedirs(os.path.dirname(pilot_asr))
    with open(os.path.join(p, "derived", "asr.json"), "rb") as f:
        raw = f.read()
    with open(pilot_asr, "wb") as f:
        f.write(raw)
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    with pytest.raises(TW.WordsError) as e:
        TW.from_project(p, asr_path=pilot_asr)
    assert e.value.code == "pilot_locked"
    out = os.path.join(p, "derived", "asr-words.json")
    if os.path.exists(out):
        os.remove(out)
    assert TW.main(["--project", p, "--asr", pilot_asr]) == 1
    assert not [f for f in opened if "pilot-" in f]
    monkeypatch.undo()
    assert "pilot_locked" in capsys.readouterr().out and not os.path.exists(out)
    # 봉인 경로(allow_pilot)는 그대로 읽는다
    assert TW.from_project(p, asr_path=pilot_asr, allow_pilot=True)["windows"]
    # engine_to_ai --out 도 --asr 가 시범 안이면 거부
    from tools import engine_to_ai as e2a
    res = os.path.join(str(tmp_path), "res", "ai.json")
    assert e2a.main(["--from-project", p, "--asr", pilot_asr, "--out", res]) == 1 and not os.path.exists(res)
    assert "pilot_needs_seal" in capsys.readouterr().out


def test_bad_asr_codes(tmp_path, capsys):
    p = make_project(tmp_path)
    with pytest.raises(TW.WordsError) as e:
        TW.from_project(p, asr_path=os.path.join(p, "derived", "없음.json"))
    assert e.value.code == "no_asr"
    bad = os.path.join(p, "derived", "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"format": "other", "segments": []}, f)
    with pytest.raises(TW.WordsError) as e:
        TW.from_project(p, asr_path=bad)
    assert e.value.code == "bad_asr"
    assert TW.main(["--project", p, "--asr", bad]) == 2


# ── tools/engine_to_ai --words ───────────────────────────────────────────────

def test_engine_to_ai_words_out(tmp_path, capsys):
    from tools import engine_to_ai as e2a
    p = make_project(tmp_path)
    # 낱말 파일은 보호 구역 밖에 평문으로 쓰지 않는다(검토 2026-10-23) — 엔진을 돌리기 전에 종료 2
    outside = os.path.join(str(tmp_path), "res", "ai.json")
    assert e2a.main(["--from-project", p, "--out", outside, "--words"]) == 2
    assert "words_out_unprotected" in capsys.readouterr().out
    assert not os.path.exists(outside) and not os.path.exists(e2a.words_out_path(outside))
    assert not e2a.words_out_protected(p, os.path.join(p, "derived", "link.json"))     # derived 안이어도 asr* 아님
    out = os.path.join(p, "derived", "asr-link.json")
    assert e2a.main(["--from-project", p, "--out", out, "--words"]) == 0
    info = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert info["words_out"] == e2a.words_out_path(out) == os.path.join(p, "derived", "asr-link.words.json")
    with open(info["words_out"], encoding="utf-8") as f:
        doc = json.load(f)
    assert TW.validate(doc) == [] and info["word_windows"] == len(doc["windows"]) == 4
    assert e2a.main(["--in", out, "--out", out + "2", "--words"]) == 2       # --from-project 와 함께만


def test_engine_to_ai_words_seal_needs_sealer(tmp_path, capsys, monkeypatch):
    """app.seal 에 짝 봉인 함수가 없으면 엔진을 돌리기 전에 멈추고 아무것도 봉인하지 않는다."""
    from app import seal
    from tools import engine_to_ai as e2a
    monkeypatch.delattr(seal, e2a.WORDS_SEAL_FN, raising=False)
    calls = []
    monkeypatch.setattr(seal, "seal_bytes", lambda *a, **k: calls.append("seal"))
    monkeypatch.setattr(e2a, "_run_engine", lambda *a, **k: calls.append("engine"))
    p = make_project(tmp_path)
    ai_root = os.path.join(str(tmp_path), "ai")
    rc = e2a.main(["--from-project", p, "--seal", "--by", "L01", "--words", "--ai-root", ai_root])
    assert rc == 1 and "words_seal_unsupported" in capsys.readouterr().out
    assert calls == [] and not os.path.exists(ai_root)


def test_engine_to_ai_words_seal_pairs(tmp_path, capsys, monkeypatch):
    """짝 봉인 함수가 생기면 판정 봉인의 sha256 을 짝으로 넘긴다(봉인 자체는 가짜로 바꿔 디스크에 쓰지 않는다)."""
    from types import SimpleNamespace
    from app import seal
    from tools import engine_to_ai as e2a
    got = {}

    def fake_seal(project, data, **k):
        got["ai"] = data
        return SimpleNamespace(sha256="b" * 64, size=len(data), check="valid", reject_reasons=[], new=True,
                               presealed=False)

    def fake_words(project, data, *, pair_sha256, by, source, ai_root):
        got.update(words=data, pair=pair_sha256, by=by, source=source)
        return SimpleNamespace(sha256=hashlib.sha256(data).hexdigest(), size=len(data), check="valid",
                               reject_reasons=[])

    monkeypatch.setattr(seal, "seal_bytes", fake_seal)
    monkeypatch.setattr(seal, e2a.WORDS_SEAL_FN, fake_words, raising=False)
    p = make_project(tmp_path)
    assert e2a.main(["--from-project", p, "--seal", "--by", "L01", "--words"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert got["pair"] == "b" * 64 and got["source"] == "engine_words" and got["by"] == "L01"
    assert TW.validate(json.loads(got["words"].decode("utf-8"))) == []
    assert out["words_sealed"] == hashlib.sha256(got["words"]).hexdigest() and out["words_check"] == "valid"
    for w in json.loads(got["words"].decode("utf-8"))["windows"]:
        for x in w["words"]:
            assert x not in json.dumps(out, ensure_ascii=False)    # 봉인 출력에도 낱말이 없다
