"""H-3 생성 채팅 (engine/generate.py, python -m engine generate) — 모두 합성 데이터·합성 가짜 응답.

실제 LM Studio·외부 주소는 부르지 않는다: 함수 주입(FakeResponses·직접 만든 chat 함수), 또는 127.0.0.1 에만 묶는
engine/llm_fake.FakeLLMServer(있을 때만). 예문·닉네임·금칙어·이모티콘 코드는 모두 합성이다({:zz_test_01:} 꼴).
실제 프로젝트·시범 폴더는 열지 않는다(tmp_path 만 — conftest 가 %LOCALAPPDATA%\\ChatCoachSmoke\\pytest 아래로 둔다).
"""
from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import os
import socket
from datetime import datetime, timedelta, timezone

import pytest

from app import scenario_store as STORE
from app.core import scenario as SC
from engine import generate as G
from engine import retrieve as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=KST)

# 합성 닉네임·금칙어(실제 시청자·실제 금칙어 목록이 아니다)
NICK_A = "합성닉네임가"
NICK_B = "테스트시청자"
BLOCK_WORD = "합성금칙어"
REAL_COPY = "합성 실제 채팅 그대로 문장입니다"

_n = [0]


def uid(name: str) -> str:
    h = hashlib.sha256(name.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}"


def _cid():
    _n[0] += 1
    return "c" + format(_n[0], "020x")


def viewer(i):
    return "vw_" + format(i, "012d").translate(str.maketrans("0123456789", "abcdefghij"))


def msg(t, text, v=0, mask="합*"):
    return {"chat_id": _cid(), "t_ms": t, "server_time_ms": None, "type_code": 1, "kind": "text", "status": "NORMAL",
            "viewer_id": viewer(v), "is_anonymous": False, "nickname_masked": mask, "role": "viewer",
            "text": text, "raw_ref": {"fetch_id": "F20261023-abcd", "page": 0, "index": 0}}


def real_msgs():
    """창 0·3 은 붐빔(10개), 창 1·2·4 는 드묾(1개). 0~300초."""
    out = []
    for i in range(10):
        out.append(msg(1000 + i * 5000, f"합성 붐빔 채팅 {i} 번째 이야기", v=i, mask=f"하{'*' * (2 + i % 3)}"))
        out.append(msg(181000 + i * 5000, f"합성 두번째 붐빔 {i} 얘기 중", v=20 + i, mask="두**"))
    out.append(msg(30000, REAL_COPY, v=40))
    out.append(msg(70000, "합성 드문 창 하나", v=41))
    out.append(msg(130000, "합성 드문 창 둘", v=42))
    out.append(msg(250000, "합성 드문 창 셋", v=43))
    return sorted(out, key=lambda m: m["t_ms"])


def words_doc(windows):
    return {
        "schema": "chatcoach/transcript-words", "schema_version": 1, "run_id": "words-v0-20261023T120000",
        "created_at": "2026-10-23T12:00:00+09:00",
        "engine": {"name": "chatcoach-transcript-words", "version": "0.1.0", "config_sha256": None,
                   "textnorm": "textnorm-v1"},
        "target": {"project_id": None, "video_no": None, "asr_sha256": "a" * 64},
        "time_axis": "vod_ms",
        "calibration": {"source": "default", "rev": None, "method": "single_offset", "status": "skipped",
                        "offset_ms": 0, "file_start_in_vod_ms": 0},
        "window_ms": 10000, "range": {"start_ms": 0, "end_ms": 300000}, "stats": {"windows": len(windows)},
        "windows": [{"start_ms": s, "end_ms": s + 10000, "words": sorted(set(ws)), "segments": 1} for s, ws in windows],
    }


SYN_WORDS = ["떡볶이", "점심", "메뉴", "보스", "게임", "캐릭터"]


def make_project(root, name="gen-synth", *, words=True, topic=True, identities=True, msgs=None):
    d = os.path.join(str(root), name)
    os.makedirs(os.path.join(d, "chat", "protected"), exist_ok=True)
    with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": uid(name), "broadcast": {"channel_id": "ch-synth-gen"}}, f)
    with open(os.path.join(d, "chat", "messages.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for m in (msgs if msgs is not None else real_msgs()):
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    if identities:
        with open(os.path.join(d, "chat", "protected", "identities.jsonl"), "w", encoding="utf-8") as f:
            for vid, nm in ((viewer(40), NICK_A), (viewer(41), NICK_B)):
                f.write(json.dumps({"viewer_id": vid, "nicknames": [{"nickname": nm}]}, ensure_ascii=False) + "\n")
    os.makedirs(os.path.join(d, "derived"), exist_ok=True)
    if words:
        # 전사 낱말: 합성 낱말 + 스트리머가 부른 닉네임(보내면 안 됨) + 식별자 꼴 낱말(보내면 안 됨)
        wins = [(s, SYN_WORDS + ([NICK_A.lower()] if s == 50000 else []) + (["abcdef123456"] if s == 60000 else []))
                for s in range(0, 300000, 10000)]
        with open(os.path.join(d, "derived", "asr-words.json"), "w", encoding="utf-8") as f:
            json.dump(words_doc(wins), f, ensure_ascii=False)
    if topic:
        tf = {"schema": "chatcoach/topic-flow", "time_axis": "vod_ms",
              "blocks": [{"index": 0, "start_ms": 0, "end_ms": 150000, "keywords": ["점심", "메뉴"]},
                         {"index": 1, "start_ms": 150000, "end_ms": 300000, "keywords": ["보스", "게임"]}]}
        with open(os.path.join(d, "derived", "asr-topic-flow.json"), "w", encoding="utf-8") as f:
            json.dump(tf, f, ensure_ascii=False)
    return d


def blocklist_file(root):
    p = os.path.join(str(root), "config", "blocklist-ko.txt")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("# 합성 금칙어(시험용)\n" + BLOCK_WORD + "\n")
    return p


# 거르기 1~5 가 각각 걸리도록 만든 합성 응답(창 1 · 2 · 4 차례)
RESP_1 = "\n".join([
    "1. 오 점심 메뉴 뭐 드셨어요",            # 통과
    f"2. {BLOCK_WORD} 들어간 나쁜 말",          # 1 금칙어
    "3. ㅋ",                                    # 2 너무 짧음
    "4. " + "아주 길게 늘어지는 줄 " * 8,       # 2 너무 긺
    f"5. {REAL_COPY}",                          # 3 실제 채팅 베끼기
    "6. 떡볶이 점심 메뉴",                       # 3 전사 베끼기(내용 낱말 3개가 모두 전사 낱말)
    "7. @누군가 안녕",                           # 4 멘션
    "8. www.example.com 가보세요",              # 4 링크
    "9. 010-1234-5678 연락 주세요",             # 4 전화번호
    f"10. {NICK_A} 님 안녕하세요",               # 4 닉네임
    "11. 오 점심 메뉴 뭐 드셨어요",              # 5 중복
    "12. 보스전 언제 하세요?",                   # 통과
])
RESP_2 = "\n".join([
    "다음은 시청자 채팅입니다:",                 # 설명 줄(meta)
    "- 다음 판은 어떤 캐릭터 하실 거예요?",
    '- "그 노래 제목이 뭐였죠 {:zz_test_01:}"',
    "- 시청자3: {:zz_other_02:} 대박 웃겨요",   # 이름 머리 떼고, 허용 밖 이모티콘 지움
])
RESP_3 = "\n".join(["오늘 날씨 좋네요", "저도 해 보고 싶어요", "후원 감사합니다 1000원"])   # 마지막은 4 후원 꼴


def fake(responses=(RESP_1, RESP_2, RESP_3)):
    return G.FakeResponses(list(responses), "synthetic-fake")


def run(project, *, chat=None, **kw):
    chat = chat or fake()
    kw.setdefault("blocklist_path", blocklist_file(os.path.dirname(os.path.dirname(project))))
    header, lines, st = G.make_generated_scenario(project, 0, 300000, chat_fn=chat, now=NOW,
                                                  emote_allow=("{:zz_test_01:}",), **kw)
    return header, lines, st, chat


# ───────────────────────────────────────────────────────────── 거르기 1~5

def test_filters_1_to_5_each_hit(tmp_path):
    p = make_project(tmp_path / "data" / "projects")
    header, lines, st, chat = run(p)
    d = st["dropped"]
    for code in ("blocked", "too_short", "too_long", "copy_chat", "copy_talk", "mention", "link", "phone",
                 "nickname", "donation", "duplicate"):
        assert d.get(code, 0) >= 1, code
    assert all(st["filter_groups"][str(g)] >= 1 for g in range(1, 6))
    assert st["split"]["meta"] == 1 and st["split"]["emote_removed"] == 1
    texts = [ln["text"] for ln in lines]
    assert "오 점심 메뉴 뭐 드셨어요" in texts and "보스전 언제 하세요?" in texts
    assert "대박 웃겨요" in texts                                   # 이름 머리·허용 밖 이모티콘이 빠짐
    assert "그 노래 제목이 뭐였죠 {:zz_test_01:}" in texts           # 따옴표 떼고, 허용 코드는 남김
    joined = "\n".join(texts)
    for bad in (BLOCK_WORD, REAL_COPY, "@", "example.com", "010-", NICK_A, "후원", "시청자3", "zz_other"):
        assert bad not in joined, bad
    assert len(texts) == len(set(texts))
    assert header["filters"]["dropped"] == d
    assert header["filters"]["blocklist_sha256"] and BLOCK_WORD not in json.dumps(header, ensure_ascii=False)
    assert st["generated"] == len(lines) > 0 and st["requests"] == 3 == chat.calls


def test_filter_line_unit_order():
    idx = G.CopyIndex([REAL_COPY, "합성 다른 실제 채팅 하나"])
    cfg = G.GenerateConfig()
    kw = dict(blocklist=(R._squash(BLOCK_WORD),), nicknames=(R._squash(NICK_A),), copy_index=idx,
              talk_words=frozenset(SYN_WORDS), accepted=(), cfg=cfg)
    assert G.filter_line(f"{BLOCK_WORD} ㅋ", **kw)[0] == "blocked"         # 1 이 2 보다 먼저
    assert G.filter_line("ㅋ", **kw)[0] == "too_short"
    assert G.filter_line(REAL_COPY + "!", **kw) == ("copy_chat", 1.0)
    assert G.filter_line("합성 실제 채팅 그대로 문장입니다요", **kw)[0] == "copy_chat"   # 5-gram 포함률 0.8 이상
    assert G.filter_line("떡볶이를 점심 메뉴로", **kw)[0] == "copy_talk"      # 조사 뗀 꼴도 전사 낱말
    assert G.filter_line("떡볶이 점심 좋아요 정말", **kw)[0] is None          # 내 말이 섞이면 베끼기 아님
    assert G.filter_line("전화 02 123 4567 주세요", **kw)[0] == "phone"
    assert G.filter_line("공지 [공지] 아님", **kw)[0] is None
    assert G.filter_line("[공지] 합성 안내 말", **kw)[0] == "system_text"
    acc = [(R._squash("오늘 방송 재밌어요"), G.TN.char_ngrams("오늘 방송 재밌어요", 5))]
    assert G.filter_line("오늘 방송 재밌어요!!", **{**kw, "accepted": acc})[0] == "duplicate"
    assert G.filter_line("완전히 다른 새 얘기", **{**kw, "accepted": acc})[0] is None


def test_split_candidates_cleans_markers():
    c, st = G.split_candidates('1) "첫 줄"\n\n• 둘째 줄\n채팅2: 셋째 줄\n[4] 넷째 {:zz_x:}\n설명:\n')
    assert c == ["첫 줄", "둘째 줄", "셋째 줄", "넷째"]
    assert st == {"raw_lines": 6, "empty": 1, "meta": 1, "emote_removed": 1, "over_limit": 0}
    c2, st2 = G.split_candidates("가나\n다라\n마바", limit=2)
    assert c2 == ["가나", "다라"] and st2["over_limit"] == 1


# ───────────────────────────────────────────────────────────── 시나리오 형식

def test_output_passes_scenario_schema_and_marks(tmp_path):
    p = make_project(tmp_path / "data" / "projects")
    header, lines, st, _ = run(p)
    recs = SC.assemble(header, lines, yyyymmdd=NOW.strftime("%Y%m%d"))
    assert STORE.validate(recs, project_id=uid("gen-synth")) == []
    h = recs[0]
    assert h["counts"] == {"replay": 0, "retrieved": 0, "generated": len(lines)}
    g = h["generator"]
    assert g["backend"] == "lmstudio" and g["model_id"] == "fake/synthetic-fake"
    assert g["prompt_template_id"] == G.PROMPT_TEMPLATE_ID == "gen-chat-v1"
    assert g["prompt_sha256"] == G.prompt_sha256() == st["prompt_sha256"]
    assert g["context_ms"] == 60000 and h["external"] == {"enabled": False}
    assert h["inputs"]["asr_sha256"] == "a" * 64 and "topic_flow_sha256" in h["inputs"]
    assert st["response_sha256"] and len(st["response_sha256"]) == 64
    masks = [ln["display_mask"] for ln in recs[1:-1]]
    assert len(set(masks)) == len(masks)                                   # 줄마다 다른 가림 이름
    real_masks = {m["nickname_masked"] for m in real_msgs()}
    assert not set(masks) & real_masks
    for ln in recs[1:-1]:
        assert ln["source"] == "generated" and ln["chat_kind"] == "text"   # 가상 표시
        assert ln["checks"]["nickname_hit"] == ln["checks"]["blocked"] == ln["checks"]["llm_flag"] == 0
        cx = ln["context"]
        assert 0 < cx["asr_to_ms"] - cx["asr_from_ms"] <= 120000             # 앞 1~2분만
        assert cx["asr_from_ms"] <= ln["show_vod_ms"] < cx["asr_to_ms"]
        assert cx["topic_id"] in ("t00", "t01")
    # 드문 창(1·2·4)에만 들어간다
    for ln in recs[1:-1]:
        assert ln["show_vod_ms"] // 60000 in (1, 2, 4)


def test_deterministic(tmp_path):
    p = make_project(tmp_path / "data" / "projects")
    a = run(p)
    b = run(p)
    assert a[1] == b[1] and a[2]["selection_sha256"] == b[2]["selection_sha256"]
    c = run(p, seed=5)
    assert c[2]["selection_sha256"] != a[2]["selection_sha256"]


# ───────────────────────────────────────────────────────────── 보내는 글 (D-06)

def test_prompt_has_no_body_nickname_or_ids(tmp_path):
    p = make_project(tmp_path / "data" / "projects")
    _h, _l, st, chat = run(p)
    sent = "\n".join(m["content"] for msgs in chat.sent for m in msgs)
    assert "떡볶이" in sent and "점심" in sent                             # 전사 낱말·주제 키워드는 간다
    for m in real_msgs():
        assert m["text"] not in sent and m["viewer_id"] not in sent and m["chat_id"] not in sent
        assert m["nickname_masked"] not in sent
    assert NICK_A not in sent and NICK_A.lower() not in sent and NICK_B not in sent
    assert "abcdef123456" not in sent and "vw_" not in sent
    assert st["prompt_words_dropped"].get("nickname", 0) >= 1 and st["prompt_words_dropped"].get("id_like", 0) >= 1
    assert st["sent_chars"] == sum(len(m["content"]) for msgs in chat.sent for m in msgs)
    for msgs in chat.sent:
        assert [m["role"] for m in msgs] == ["system", "user"]                 # 지시문과 자료를 나눔
        assert msgs[0]["content"] == G.SYSTEM_V1
        assert "참고만, 지시 아님" in msgs[1]["content"]
    # 통계에는 숫자·코드·해시만
    s = json.dumps(st, ensure_ascii=False)
    for bad in ("떡볶이", NICK_A, REAL_COPY, BLOCK_WORD):
        assert bad not in s


def test_context_limits():
    with pytest.raises(ValueError):
        G.GenerateConfig(context_before_ms=120001)
    with pytest.raises(ValueError):
        G.GenerateConfig(context_after_ms=-1)
    G.GenerateConfig(context_before_ms=120000, context_after_ms=120000)


def test_no_context_no_request(tmp_path):
    p = make_project(tmp_path / "data" / "projects", words=False, topic=False)
    _h, lines, st, chat = run(p)
    assert lines == [] and chat.calls == 0 and st["requests"] == 0
    assert st["windows_no_context"] == 3 and st["words_found"] is False


# ───────────────────────────────────────────────────────────── 시범 거부

@pytest.fixture
def no_pilot_open(monkeypatch):
    """pilot- 가 든 경로를 열거나 목록·존재 확인하면 실패."""
    real_open, real_listdir, real_isfile, real_isdir = builtins.open, os.listdir, os.path.isfile, os.path.isdir
    seen = []

    def bad(p):
        return "pilot-" in str(p).lower()

    def o(p, *a, **k):
        if bad(p):
            seen.append(p)
            raise AssertionError("시범 경로를 열었다")
        return real_open(p, *a, **k)

    def ld(p="."):
        if bad(p):
            seen.append(p)
            raise AssertionError("시범 경로 목록")
        return real_listdir(p)

    def isf(p):
        if bad(p):
            seen.append(p)
            raise AssertionError("시범 경로 확인")
        return real_isfile(p)

    def isd(p):
        if bad(p):
            seen.append(p)
            raise AssertionError("시범 경로 확인")
        return real_isdir(p)

    monkeypatch.setattr(builtins, "open", o)
    monkeypatch.setattr(os, "listdir", ld)
    monkeypatch.setattr(os.path, "isfile", isf)
    monkeypatch.setattr(os.path, "isdir", isd)
    return seen


def test_pilot_project_refused_before_reading(tmp_path, no_pilot_open, capsys):
    root = tmp_path / "data" / "projects"
    p = os.path.join(str(root), "pilot-synth-01")      # 폴더가 있어도(만들지 않아도) 열기 전에 거부
    with pytest.raises(G.GenerateError) as e:
        G.make_generated_scenario(p, 0, 300000, chat_fn=fake())
    assert e.value.code == "pilot_locked"
    rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", "x.json"], now=NOW)
    assert rc == 2 and json.loads(capsys.readouterr().out)["error"] == "pilot_locked"
    assert no_pilot_open == []


def test_pilot_paths_refused(tmp_path, no_pilot_open, capsys):
    p = make_project(tmp_path / "data" / "projects")
    seal = os.path.join(str(tmp_path), "data", "ai", "pilot-synth-01", "words", "r.json")   # 시범 봉인 폴더 모양
    for kw in ({"words_path": seal}, {"topic_flow_path": seal}, {"blocklist_path": seal}):
        with pytest.raises(G.GenerateError) as e:
            G.make_generated_scenario(p, 0, 300000, chat_fn=fake(), **kw)
        assert e.value.code == "pilot_locked"
    for flag in ("--words", "--topic-flow", "--fake", "--out", "--send-log", "--blocklist"):
        rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", "f.json", flag, seal], now=NOW)
        assert rc == 2, flag
        assert json.loads(capsys.readouterr().out)["error"] == "pilot_locked"
    assert no_pilot_open == []


# ───────────────────────────────────────────────────────────── 주소 · 서버 꺼짐

@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("네트워크를 쓰면 안 된다")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


@pytest.mark.parametrize("url", ["http://192.168.0.5:1234", "http://10.0.0.1:1234", "http://example.com:1234",
                                 "http://127.0.0.1.example.com:1234", "http://user@127.0.0.1:1234",
                                 "ftp://127.0.0.1:1234", "http://127.0.0.1:1234/?x=1", "http://[::2]:1234"])
def test_non_local_url_refused(url, no_network):
    assert not G.is_local_url(url)
    with pytest.raises(G.GenerateError) as e:
        G.make_local_chat(url, "m")
    assert e.value.code == "not_local"


def test_local_urls_ok():
    for u in ("http://127.0.0.1:1234", "http://localhost:1234", "http://[::1]:1234", "https://127.0.0.1/v1"):
        assert G.is_local_url(u), u


def test_main_non_local_refused_no_network(tmp_path, no_network, capsys):
    p = make_project(tmp_path / "data" / "projects")
    rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--model", "m", "--url", "http://192.168.0.5:1234"],
                now=NOW)
    assert rc == 1 and json.loads(capsys.readouterr().out)["error"] == "not_local"
    assert not os.path.exists(os.path.join(p, "scenarios"))


def test_main_requires_model(tmp_path, no_network, capsys):
    p = make_project(tmp_path / "data" / "projects")
    rc = G.main(["--project", p, "--from", "0", "--to", "300000"], now=NOW)
    assert rc == 3 and json.loads(capsys.readouterr().out)["error"] == "model_missing"


def test_server_off_mid_run_makes_nothing(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects")
    calls = []

    def chat(messages, **kw):
        calls.append(1)
        if len(calls) >= 2:
            raise ConnectionRefusedError("합성: 서버 꺼짐")
        return RESP_1

    with pytest.raises(G.GenerateError) as e:
        run(p, chat=chat)
    assert e.value.code == "lmstudio_off" and len(calls) == 2
    bl = blocklist_file(tmp_path / "data")
    rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--blocklist", bl], now=NOW,
                chat_fn=lambda m, **k: (_ for _ in ()).throw(ConnectionRefusedError()))
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and out == {"error": "lmstudio_off", "generated": 0}
    assert not os.path.exists(os.path.join(p, "scenarios"))


def test_server_off_real_socket_closed_port(tmp_path, capsys):
    """127.0.0.1 의 닫힌 포트(가짜 서버를 띄우지 않음) — 생성 0건, lmstudio_off, 외부로 넘어가지 않음."""
    pytest.importorskip("engine.llm_local")
    fk = pytest.importorskip("engine.llm_fake")
    p = make_project(tmp_path / "data" / "projects")
    url = f"http://127.0.0.1:{fk.closed_port()}"
    rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--model", "m", "--url", url,
                 "--blocklist", ""], now=NOW)
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and out["error"] == "lmstudio_off" and out["generated"] == 0
    assert not any(n.startswith("SC-") for n in (os.listdir(os.path.join(p, "scenarios"))
                                                if os.path.isdir(os.path.join(p, "scenarios")) else ()))


def test_fake_http_server_end_to_end(tmp_path, capsys):
    """engine/llm_fake.FakeLLMServer(127.0.0.1) + engine/llm_local — 실제 HTTP 길로 3번 부르고 시나리오를 쓴다."""
    pytest.importorskip("engine.llm_local")
    fk = pytest.importorskip("engine.llm_fake")
    p = make_project(tmp_path / "data" / "projects")
    bl = blocklist_file(tmp_path / "data")
    steps = [{"content": RESP_1}, {"content": RESP_2}, {"content": RESP_3}]
    with fk.FakeLLMServer(steps, models=("synthetic-model",)) as srv:
        rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--model", "synthetic-model",
                     "--url", srv.base_url, "--blocklist", bl, "--emotes", "{:zz_test_01:}"], now=NOW)
        reqs = list(srv.requests)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["written"] is True and out["backend"] == "lmstudio"
    posts = [r for r in reqs if r.get("method") == "POST"]
    assert len(posts) == 3
    body = json.dumps([r.get("json") for r in posts], ensure_ascii=False)
    assert NICK_A not in body and REAL_COPY not in body and "vw_" not in body
    sc = STORE.load_scenario(p, out["scenario_id"], check_pilot=False)
    assert sc.header["generator"]["model_id"] == "synthetic-model"
    assert sc.header["counts"]["generated"] == out["counts"]["generated"] > 0
    log = os.path.join(p, "scenarios", "llm-send.jsonl")       # D-06 5: 보낸 기록(본문 없음)
    assert os.path.isfile(log)
    with open(log, encoding="utf-8") as f:
        text = f.read()
    assert "떡볶이" not in text and NICK_A not in text and '"sent_chars"' in text


# ───────────────────────────────────────────────────────────── 명령줄 --fake

def write_fake(root, responses=(RESP_1, RESP_2, RESP_3)):
    path = os.path.join(str(root), "fake-responses.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"model": "synthetic-fake", "responses": list(responses)}, f, ensure_ascii=False)
    return path


def test_main_fake_writes_scenario(tmp_path, no_network, capsys):
    p = make_project(tmp_path / "data" / "projects")
    fk = write_fake(tmp_path)
    blocklist_file(tmp_path / "data")                             # 기본 경로 <데이터>\config\blocklist-ko.txt
    args = ["--project", p, "--from", "0:00", "--to", "5:00", "--fake", fk, "--emotes", "{:zz_test_01:}"]
    rc = G.main(args + ["--dry-run"], now=NOW)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["valid"] is True and out["written"] is False and out["backend"] == "fake"
    assert not os.path.exists(os.path.join(p, "scenarios"))
    rc = G.main(args, now=NOW)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["written"] is True and "warnings" not in out
    sc, cnt = STORE.latest_scenario(p, (0, 300000), check_pilot=False)
    assert sc is not None and sc.scenario_id == out["scenario_id"] and cnt["broken"] == 0
    assert sc.header["filters"]["dropped"]["blocked"] >= 1
    rc = G.main(args, now=NOW)                                     # 같은 입력이면 새로 쓰지 않음
    out2 = json.loads(capsys.readouterr().out)
    assert rc == 0 and out2["written"] is False and out2["existing"] == out["scenario_id"]
    s = json.dumps(out, ensure_ascii=False)
    for bad in ("떡볶이", NICK_A, REAL_COPY, BLOCK_WORD, "점심 메뉴"):
        assert bad not in s


def test_main_out_generation_file_for_make_scenario(tmp_path, no_network, capsys):
    p = make_project(tmp_path / "data" / "projects")
    fk = write_fake(tmp_path)
    out_path = os.path.join(p, "scenarios", "gen-synth.jsonl")
    rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", fk, "--blocklist",
                 blocklist_file(tmp_path / "data"), "--out", out_path], now=NOW)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["out"] == "generation_file" and out["lines"] > 0
    with open(out_path, "rb") as f:
        raw = f.read()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    recs = [json.loads(x) for x in raw.decode("utf-8").splitlines()]
    assert recs[0]["kind"] == G.GEN_HEADER_KIND and recs[0]["project_id"] == uid("gen-synth")
    assert all(r["source"] == "generated" for r in recs[1:])
    assert STORE.list_scenarios(p, check_pilot=False) == []        # 시나리오 목록에 섞이지 않음
    spec = importlib.util.spec_from_file_location("_ms_for_gen_test", os.path.join(ROOT, "tools", "make_scenario.py"))
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)
    if not hasattr(ms, "read_generated"):
        pytest.skip("make_scenario --generated 가 아직 없다")
    head, lines, _sha = ms.read_generated(out_path)
    assert ms.clean_generator(head["generator"])["backend"] == "lmstudio"
    assert all(ms.sanitize_generated(r)[1] is None for r in lines)


def test_main_bad_inputs(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects")
    fk = write_fake(tmp_path)
    base = ["--project", p, "--from", "0", "--to", "300000", "--fake", fk]
    bad_bl = os.path.join(str(tmp_path), "bad.txt")
    with open(bad_bl, "wb") as f:
        f.write(b"\xff\xfe\x00bad")
    assert G.main(base + ["--blocklist", bad_bl], now=NOW) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "bad_blocklist"
    assert G.main(base + ["--emotes", "{:진짜코드:}"], now=NOW) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "bad_argument"
    assert G.main(base + ["--context-before-sec", "121"], now=NOW) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "bad_argument"
    bad_words = os.path.join(p, "derived", "asr-words-bad.json")
    with open(bad_words, "w", encoding="utf-8") as f:
        json.dump({"schema": "x"}, f)
    assert G.main(base + ["--words", bad_words], now=NOW) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "bad_words"
    assert not os.path.exists(os.path.join(p, "scenarios"))


def test_blocklist_missing_warns(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects")
    rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", write_fake(tmp_path), "--dry-run"], now=NOW)
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert rc == 0 and "blocklist_missing" in out["warnings"] and "blocklist_missing" in cap.err


def test_engine_main_passthrough(tmp_path, no_network, capsys):
    from engine import __main__ as EM
    p = make_project(tmp_path / "data" / "projects")
    rc = EM.main(["generate", "--project", p, "--from", "0", "--to", "300000", "--fake", write_fake(tmp_path),
                  "--blocklist", "", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["valid"] is True and out["counts"]["generated"] > 0


def test_fake_txt_format(tmp_path):
    path = os.path.join(str(tmp_path), "f.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("가나다 첫 응답\n---\n둘째 응답 줄\n")
    fr = G.FakeResponses.from_file(path)
    assert fr.responses == ["가나다 첫 응답", "둘째 응답 줄"] and fr.model_id == "fake/fake"
    assert fr([{"role": "user", "content": "x"}]) == "가나다 첫 응답" and fr([]) == "둘째 응답 줄" and fr([]) == "가나다 첫 응답"
