"""H-2 retrieved 고르기 (engine/retrieve.py, tools/make_scenario.py) — 모두 합성 데이터.

예문·닉네임·이모티콘 코드는 합성이다({:zz_test_01:} 꼴). 실제 프로젝트 폴더는 열지 않는다(tmp_path 만).
"""
from __future__ import annotations

import ast
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app import scenario_store as STORE
from app.core import scenario as SC
from engine import retrieve as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_scenario as MS  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=KST)

_n = [0]


def uid(name: str) -> str:
    """합성 프로젝트 ID(UUID v4 모양, 이름에서 결정적으로)."""
    import hashlib
    h = hashlib.sha256(name.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}"


def make(project_dir, *a, **kw):
    """engine.retrieve → app.core.scenario.assemble → 스키마·규칙 검사(app.scenario_store.validate)까지.
    돌려주는 recs 는 [header, chat …] (끝 줄 뺌)."""
    header, lines, st = R.make_retrieved_scenario(project_dir, *a, **kw)
    full = SC.assemble(header, lines, yyyymmdd=NOW.strftime("%Y%m%d"))
    pid = R.read_project_meta(project_dir)[0]
    assert STORE.validate(full, project_id=pid) == []
    assert [c["line_id"] for c in full[1:-1]] == [c["line_id"] for c in lines]     # assemble 이 순서를 바꾸지 않음
    return full[:-1], st


def _cid():
    _n[0] += 1
    return "c" + format(_n[0], "020x")


def msg(t, text, viewer="vw_aaaaaaaaaaaa", kind="text", status="NORMAL", role="viewer", mask="합*"):
    return {"chat_id": _cid(), "t_ms": t, "server_time_ms": None, "type_code": 1, "kind": kind, "status": status,
            "viewer_id": viewer, "is_anonymous": viewer == "anon", "nickname_masked": mask, "role": role,
            "text": text, "raw_ref": {"fetch_id": "F20261023-abcd", "page": 0, "index": 0}}


def make_project(root, name, msgs, *, channel="ch-synth-1", pid=None, identities=None):
    d = os.path.join(str(root), name)
    os.makedirs(os.path.join(d, "chat", "protected"), exist_ok=True)
    with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": pid or uid(name), "broadcast": {"channel_id": channel}}, f)
    with open(os.path.join(d, "chat", "messages.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    if identities is not None:
        with open(os.path.join(d, "chat", "protected", "identities.jsonl"), "w", encoding="utf-8") as f:
            for vid, names in identities.items():
                f.write(json.dumps({"viewer_id": vid, "nicknames": [{"nickname": n} for n in names]},
                                   ensure_ascii=False) + "\n")
    return d


def viewer(i):
    return "vw_" + format(i, "012d").translate(str.maketrans("0123456789", "abcdefghij"))


def pool_msgs(t0, n, prefix="합성", step=1000, start_viewer=0):
    """후보용 깨끗한 합성 채팅 n개(시청자 n명)."""
    return [msg(t0 + i * step, f"{prefix} 문장 {i} 점심 메뉴 {{:zz_test_01:}}", viewer=viewer(start_viewer + i))
            for i in range(n)]


def busy(t0, n):
    """한 창을 바쁘게: 실제 채팅 n개."""
    return [msg(t0 + i * 500, f"바쁜 창 채팅 {i}", viewer=viewer(900 + i)) for i in range(n)]


# 구간 [600000, 840000) = 4분 창 4개. 창0·창2 바쁨(5개), 창1 1개, 창3 0개.
FROM, TO = 600000, 840000


def basic_target(tmp_path, extra=()):
    ms = busy(600000, 5) + [msg(670000, "드문 창 실제 하나", viewer=viewer(950))] + busy(720000, 5)
    ms += pool_msgs(0, 40)                      # 구간 밖(앞)
    ms += list(extra)
    return make_project(tmp_path / "projects", "p-target", ms, identities={viewer(0): ["합성닉"]})


# ───────────────────────────────────────────────────────────── 창·나누기

def test_windows_and_quiet():
    cfg = R.RetrieveConfig()
    chats = [R.Chat("c1", 600100, "text", "NORMAL", "v", "viewer", None, "a"),
             R.Chat("c2", 600200, "donation", "NORMAL", "v", "viewer", None, "a")]
    ws = R.windows(chats, FROM, TO, cfg)
    assert [w.real for w in ws] == [1, 0, 0, 0] and all(w.quiet for w in ws) and ws[0].target == 6
    ws2 = R.windows(chats, 600000, 630000, cfg)          # 짧은 창은 목표를 비율로
    assert ws2[0].target == 3
    with pytest.raises(R.RetrieveError) as e:
        R.windows(chats, 5, 5, cfg)
    assert e.value.code == "bad_range"


def test_allocate_ratio_cap():
    cfg = R.RetrieveConfig()
    ws = [R.Window(0, 60000, 0, 6, True), R.Window(60000, 120000, 1, 6, True), R.Window(120000, 180000, 9, 6, False)]
    assert R.allocate(ws, 100, cfg) == [6, 5, 0]
    assert R.allocate(ws, 3, cfg) == [2, 1, 0]          # 가상 ≤ 실제(비율 0.5), 앞 창부터 고르게
    assert sum(R.allocate(ws, 3, R.RetrieveConfig(max_virtual_ratio=0.25))) == 1


# ───────────────────────────────────────────────────────────── 거르기

@pytest.mark.parametrize("chat,why", [
    (dict(text="그 얘기 합성닉 도 했잖아"), "nickname"),
    (dict(text="@누군가 안녕하세요 반가워요"), "mention"),
    (dict(text="여기 보세요 https://example.invalid/x"), "link"),
    (dict(text="주소 synth-site.com 들어가 봐"), "link"),
    (dict(text="연락 010-1234-5678 주세요"), "phone"),
    (dict(text="1000원 후원 감사합니다"), "donation"),
    (dict(text="치즈 보냈어요 확인해 주세요"), "donation"),
    (dict(text="정상 문장이지만 종류가 후원", kind="donation"), "not_text"),
    (dict(text="정상 문장이지만 블라인드", status="CBOTBLIND"), "status"),
    (dict(text="[공지] 오늘은 합성 공지입니다"), "system_text"),
    (dict(text="ㅋ"), "too_short"),
    (dict(text="{:zz_test_01:}{:zz_test_02:}"), "too_short"),
    (dict(text="가" * 61), "too_long"),
    (dict(text="합성금칙 단어가 들어간 문장"), "blocked"),
    (dict(text="운영자가 쓴 합성 문장", role="manager"), "role"),
    (dict(text="스트리머가 쓴 합성 문장", role="streamer"), "role"),
    (dict(text="익명 후원 합성 문장", viewer="anon"), "anon"),
    (dict(text="평범한 합성 채팅 문장 {:zz_test_01:}"), None),
])
def test_reject_reason(chat, why):
    c = R.Chat("c0", 1000, chat.get("kind", "text"), chat.get("status", "NORMAL"), chat.get("viewer", "vw_x"),
               chat.get("role", "viewer"), "합*", chat["text"])
    assert R.reject_reason(c, nicknames={"합성닉"}, blocklist=("합성금칙",), cfg=R.RetrieveConfig()) == why


def test_blocklist_file(tmp_path):
    p = tmp_path / "bl.txt"
    p.write_text("# 합성 금칙어 목록\n합성 금칙\n\n합성금칙\n", encoding="utf-8")
    words, sha = R.load_blocklist(str(p))
    assert words == ("합성금칙",) and len(sha) == 64
    assert R.load_blocklist(str(tmp_path / "없음.txt")) == ((), None)


# ───────────────────────────────────────────────────────────── 고르기

def test_fills_only_quiet_windows_and_skips_range(tmp_path):
    tgt = basic_target(tmp_path)
    recs, st = make(tgt, FROM, TO, seed=7, now=NOW)
    head, lines = recs[0], recs[1:]
    assert head["kind"] == "scenario.header" and head["schema"] == R.SCENARIO_SCHEMA
    assert head["scenario_id"].startswith("SC-20261023-") and len(head["scenario_id"]) == 16
    assert head["counts"] == {"replay": 0, "retrieved": len(lines), "generated": 0}
    assert head["external"] == {"enabled": False} and head["generator"] == {"backend": "none"}
    assert head["retrieval"]["seed"] == 7 and "from_projects" not in head["inputs"]
    # 창1(실제 1)·창3(0)만 채운다: 6-1 + 6-0 = 11, 실제 11개라 비율 상한(11)과 같다
    assert st["quiet_windows"] == 2 and st["real_in_range"] == 11 and len(lines) == 11
    for ln in lines:
        assert ln["source"] == "retrieved" and "text" not in ln and "nickname_masked" not in ln
        assert 660000 <= ln["show_vod_ms"] < 720000 or 780000 <= ln["show_vod_ms"] < 840000
        assert not (FROM <= ln["from_t_ms"] < TO)       # 구간 안 채팅은 0건
        assert ln["from_scope"] == "same_broadcast"
    assert [ln["line_id"] for ln in lines] == [f"s{i:04d}" for i in range(1, 12)]
    assert [ln["show_vod_ms"] for ln in lines] == sorted(ln["show_vod_ms"] for ln in lines)
    assert st["removed"].get("in_range") == 11


def test_filtered_chats_never_picked(tmp_path):
    bad = [msg(100000 + i, t, viewer=viewer(500 + i), **kw) for i, (t, kw) in enumerate([
        ("합성닉 오늘 왔어요 반가워요", {}), ("@합성 이거 봐요", {}), ("www.synth.invalid 가 봐", {}),
        ("010 1234 5678", {}), ("5,000원 보냅니다", {}), ("구독 축하합니다", {"kind": "subscription"}),
        ("합성금칙 섞인 문장입니다", {}), ("운영 합성 공지 같은 말", {"role": "manager"}),
    ])]
    tgt = basic_target(tmp_path, extra=bad)
    bl = tmp_path / "bl.txt"
    bl.write_text("합성금칙\n", encoding="utf-8")
    recs, st = make(tgt, FROM, TO, seed=1, blocklist_path=str(bl), now=NOW)
    bad_ids = {m["chat_id"] for m in bad}
    assert not bad_ids & {ln["from_chat_id"] for ln in recs[1:]}
    rm = st["removed"]
    for k in ("nickname", "mention", "link", "phone", "donation", "not_text", "blocked", "role"):
        assert rm.get(k, 0) >= 1, k
    assert len(recs[0]["filters"]["blocklist_sha256"]) == 64 and st["blocklist_words"] == 1
    assert recs[0]["filters"]["dropped"] == rm


def test_deterministic_seed(tmp_path):
    tgt = basic_target(tmp_path)
    a, _ = make(tgt, FROM, TO, seed=3, now=NOW)
    b, _ = make(tgt, FROM, TO, seed=3, now=NOW)
    c, _ = make(tgt, FROM, TO, seed=4, now=NOW)
    assert a == b
    assert a[1:] != c[1:]
    assert a[0]["scenario_id"] == b[0]["scenario_id"]


def test_max_per_viewer(tmp_path):
    same_v = [msg(i * 1000, f"한 시청자 합성 문장 {i}", viewer=viewer(7)) for i in range(30)]
    ms = busy(600000, 5) + busy(720000, 5) + [msg(670000, "드문 창 실제", viewer=viewer(950))] + same_v
    tgt = make_project(tmp_path / "projects", "p-one", ms)
    recs, st = make(tgt, FROM, TO, now=NOW)
    assert len(recs) - 1 == 3 and st["viewer_capped"] >= 1


def test_masks_new_and_not_real(tmp_path):
    real_masks = {m: None for m in ["가*", "나**", "다***"]}
    ms = busy(600000, 5) + busy(720000, 5) + [msg(670000, "드문 창 실제", viewer=viewer(950))]
    ms += [msg(i * 1000, f"마스크 합성 {i}", viewer=viewer(i), mask=list(real_masks)[i % 3]) for i in range(40)]
    tgt = make_project(tmp_path / "projects", "p-mask", ms)
    recs, _ = make(tgt, FROM, TO, now=NOW)
    masks = [ln["display_mask"] for ln in recs[1:]]
    assert masks and not set(masks) & set(real_masks)
    assert all(m[0] in R._MASK_SYLLABLES and set(m[1:]) == {"*"} for m in masks)
    # 같은 원 시청자는 같은 가림 이름, 다른 시청자는 다른 이름
    by_v = {}
    for ln in recs[1:]:
        by_v.setdefault(ln["display_mask"], set()).add(ln["from_chat_id"])
    assert len(by_v) == len(masks)        # 시청자마다 한 줄씩 골랐으니 이름도 줄 수만큼


def test_no_nickname_or_text_in_output(tmp_path):
    tgt = basic_target(tmp_path)
    recs, st = make(tgt, FROM, TO, now=NOW)
    blob = json.dumps(recs, ensure_ascii=False) + json.dumps(st, ensure_ascii=False)
    assert "합성닉" not in blob and "점심 메뉴" not in blob and "합*" not in blob


def test_no_real_chat_refused(tmp_path):
    tgt = make_project(tmp_path / "projects", "p-empty", pool_msgs(0, 10))
    with pytest.raises(R.RetrieveError) as e:
        make(tgt, FROM, TO, now=NOW)
    assert e.value.code == "no_real_chat"


def test_topic_words_rank_first(tmp_path):
    ms = busy(600000, 5) + busy(720000, 5) + busy(780000, 5) + [msg(670000, "드문 창 실제", viewer=viewer(950))]
    ms += [msg(i * 1000, f"관계없는 합성 문장 {i}", viewer=viewer(i)) for i in range(20)]
    hit = msg(30500, "고양이 사료 합성 이야기", viewer=viewer(300))
    tf = tmp_path / "tf.json"
    tf.write_text(json.dumps({"schema": "chatcoach/topic-flow", "time_axis": "vod_ms",
                              "blocks": [{"start_ms": 660000, "end_ms": 720000, "keywords": ["고양이", "사료"]}]}),
                  encoding="utf-8")
    tgt = make_project(tmp_path / "projects", "p-topic", ms + [hit])
    recs, st = make(tgt, FROM, TO, topic_flow_path=str(tf), now=NOW)
    best = max(recs[1:], key=lambda ln: ln["retrieval_score"])
    assert best["from_chat_id"] == hit["chat_id"] and best["retrieval_score"] > 0
    assert recs[0]["retrieval"]["method"] == "topic_words" and recs[0]["inputs"]["topic_flow_sha256"]


# ───────────────────────────────────────────────────────────── 같은 채널·시범

def test_same_channel_and_pilot_excluded(tmp_path):
    root = tmp_path / "projects"
    tgt = make_project(root, "p-a", busy(600000, 5) + busy(720000, 5) + [msg(670000, "드문", viewer=viewer(950))])
    other = make_project(root, "p-b", pool_msgs(0, 30, prefix="다른방송", start_viewer=100))
    diff = make_project(root, "p-c", pool_msgs(0, 30, prefix="딴채널", start_viewer=200), channel="ch-other")
    pilot = make_project(root, "pilot-x", pool_msgs(0, 30, prefix="시범", start_viewer=300))
    recs, st = make(tgt, FROM, TO, now=NOW)
    src = {ln["from_project_id"] for ln in recs[1:]}
    assert src == {uid("p-b")} and st["same_channel_projects"] == 1 and st["pilot_skipped"] == 1
    assert all(ln["from_scope"] == "same_channel" for ln in recs[1:])
    assert [o["project_id"] for o in recs[0]["inputs"]["from_projects"]] == [uid("p-b")]
    assert os.path.isdir(diff) and os.path.isdir(pilot)


def test_pilot_sibling_not_opened(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    tgt = make_project(root, "p-a", busy(600000, 5) + [msg(670000, "드문", viewer=viewer(950))] + pool_msgs(0, 20))
    make_project(root, "pilot-y", pool_msgs(0, 5))
    opened = []
    real_open = open

    def spy(path, *a, **kw):
        opened.append(os.path.basename(os.path.dirname(os.path.dirname(str(path)))) + "|" + str(path))
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", spy)
    make(tgt, FROM, TO, now=NOW)
    assert not any("pilot-y" in p for p in opened)


def test_no_channel_only_same_broadcast(tmp_path):
    root = tmp_path / "projects"
    tgt = make_project(root, "p-a", busy(600000, 5) + [msg(670000, "드문", viewer=viewer(950))] + pool_msgs(0, 20),
                       channel=None)
    make_project(root, "p-b", pool_msgs(0, 30, prefix="다른", start_viewer=100), channel=None)
    recs, st = make(tgt, FROM, TO, now=NOW)
    assert {ln["from_project_id"] for ln in recs[1:]} == {uid("p-a")} and not st["channel_known"]


def test_pilot_target_refused_before_read(tmp_path):
    missing = tmp_path / "projects" / "pilot-z"          # 파일이 없어도 pilot_locked 가 먼저
    with pytest.raises(R.RetrieveError) as e:
        make(str(missing), FROM, TO)
    assert e.value.code == "pilot_locked"
    with pytest.raises(R.RetrieveError) as e:
        R.load_project(str(missing))
    assert e.value.code == "pilot_locked"


# ───────────────────────────────────────────────────────────── 도구

def test_tool_writes_and_reads_back(tmp_path, capsys):
    """도구 → 진짜 app.scenario_store.write_scenario 로 쓰고 read_scenario 로 다시 읽는다. 두 번째는 새로 쓰지 않는다."""
    tgt = basic_target(tmp_path)
    with open(os.path.join(tgt, "chat", "messages.jsonl"), "rb") as f:
        before = f.read()
    args = ["--project", tgt, "--from", "10:00", "--to", "14:00", "--seed", "5", "--blocklist", ""]
    assert MS.main(args, now=NOW) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["written"] is True and out["counts"]["retrieved"] == 11
    sid = out["scenario_id"]
    assert os.listdir(os.path.join(tgt, "scenarios")) == [sid + ".jsonl"]
    s = STORE.read_scenario(STORE.scenario_path(tgt, sid), project_id=uid("p-target"))
    assert len(s.chats) == 11 and all(c["source"] == "retrieved" for c in s.chats)
    assert "합성" not in json.dumps(out, ensure_ascii=False)        # 출력은 숫자·ID 만
    assert MS.main(args, now=NOW) == 0                                # 같은 시나리오 → 새로 만들지 않음
    out2 = json.loads(capsys.readouterr().out.strip())
    assert out2["written"] is False and out2["existing"] == sid
    assert len(os.listdir(os.path.join(tgt, "scenarios"))) == 1
    with open(os.path.join(tgt, "chat", "messages.jsonl"), "rb") as f:
        assert f.read() == before                                     # 실제 채팅 파일은 그대로(섞지 않는다)
    with open(STORE.scenario_path(tgt, sid), encoding="utf-8") as f:
        body = f.read()
    assert "합성닉" not in body and "점심 메뉴" not in body            # 본문·닉네임 없음


def test_tool_refusals(tmp_path, capsys):
    pilot = tmp_path / "projects" / "pilot-q"
    assert MS.main(["--project", str(pilot), "--from", "0", "--to", "1000"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "pilot_locked"
    tgt = basic_target(tmp_path)
    assert MS.main(["--project", tgt, "--from", "x", "--to", "1000"]) == 1
    assert MS.main(["--project", tgt, "--from", "0", "--to", "1000", "--seed", "-1"]) == 1
    capsys.readouterr()
    assert MS.main(["--project", tgt, "--from", "900000", "--to", "960000", "--dry-run"]) == 4  # 구간에 실제 채팅 없음
    assert json.loads(capsys.readouterr().out)["error"] == "no_real_chat"
    # 드문 창이 없으면(채울 것 0) 파일을 쓰지 않는다
    assert MS.main(["--project", tgt, "--from", "600000", "--to", "660000"], now=NOW) == 0
    o = json.loads(capsys.readouterr().out)
    assert o["written"] is False and o["reason"] == "nothing_to_fill"
    assert MS.main(["--project", tgt, "--from", "600000", "--to", "840000", "--dry-run"], now=NOW) == 0
    o = json.loads(capsys.readouterr().out)
    assert o["written"] is False and o["valid"] is True and o["problems"] == 0
    assert not os.path.exists(os.path.join(tgt, "scenarios"))


def test_tool_store_missing(tmp_path, capsys, monkeypatch):
    tgt = basic_target(tmp_path)
    monkeypatch.setattr(MS, "_store", lambda: None)
    assert MS.main(["--project", tgt, "--from", "600000", "--to", "840000"]) == 3
    assert json.loads(capsys.readouterr().out)["error"] == "scenario_store_missing"


def test_parse_ms():
    assert MS.parse_ms("1:02:03.5") == 3723500 and MS.parse_ms("10:00") == 600000 and MS.parse_ms("42") == 42


# ───────────────────────────────────────────────────────────── 표준 라이브러리만

def test_retrieve_stdlib_only():
    path = os.path.join(ROOT, "engine", "retrieve.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "상대 import 없음"
            mods.add((node.module or "").split(".")[0])
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}, mods - set(sys.stdlib_module_names)
