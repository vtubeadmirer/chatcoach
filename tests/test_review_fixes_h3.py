"""H-3 생성 검토 고침의 재현 시험 (engine/generate.py · engine/llm_local.py · tools/make_scenario.py).

모두 합성 데이터(tests/test_generate 의 합성 프로젝트·합성 닉네임·합성 금칙어)와 합성 가짜 응답이다. 실제 LM Studio·외부
주소는 부르지 않는다(가짜 서버 engine/llm_fake.FakeLLMServer 는 127.0.0.1 에만). 실제 프로젝트·시범 폴더는 열지 않는다
(tmp_path 만 — conftest 가 %LOCALAPPDATA%\\ChatCoachSmoke\\pytest 아래로 둔다).

  1. 보낼 글 닉네임 검사의 거짓 양성: 지시문 틀 글자('방송'·'채팅'·'규칙')나 이웃 낱말을 이은 꼴('점심'+'메뉴')과 같은
     닉네임 하나 때문에 생성 전체가 privacy(종료 4)로 멈추던 것 → 틀 글자는 빼고, 낱말 조각마다 대조, 걸린 창만 건너뜀.
  2·3. 보호 구역 identities.jsonl 이 없으면 닉네임 대조가 조용히 꺼지던 것 → nicknames_missing 으로 닫음.
  4. --out·--send-log 가 chat\\messages.jsonl·labels\\ 를 덮거나 덧붙이던 것 → scenarios\\ 아래만.
  low. 전각 '＠' 멘션이 거르기 4 를 지나던 것 · make_scenario 가 시범 폴더 안의 --generated 를 열던 것.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import sys

import pytest

from engine import generate as G
from engine import llm_fake as LF
from engine import llm_local as L
from engine import retrieve as R

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import make_scenario as MS  # noqa: E402

from tests.test_generate import (NICK_A, NOW, RESP_2, blocklist_file, make_project, run, viewer,  # noqa: E402
                                 write_fake)


def _set_nickname(project, nick):
    """합성 프로젝트의 identities.jsonl 을 닉네임 하나로 바꾼다(합성 값)."""
    p = os.path.join(project, "chat", "protected", "identities.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"viewer_id": viewer(40), "nicknames": [{"nickname": nick}]}, ensure_ascii=False) + "\n")


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _real_args(p, url, bl, *extra):
    return ["--project", p, "--from", "0", "--to", "300000", "--model", LF.FAKE_MODEL, "--url", url,
            "--blocklist", bl, *extra]


# ───────────────────────────────────────────────────────────── 1. 닉네임 검사 거짓 양성

@pytest.mark.parametrize("nick", ["방송", "채팅", "규칙", "자료", "요청", "점심메뉴"])
def test_common_word_nickname_does_not_stop_generation_http(tmp_path, capsys, nick):
    """지시문 틀 조각·이웃 낱말을 이은 꼴과 같은 닉네임이 있어도 실제 HTTP 길(가짜 서버)로 생성이 된다."""
    p = make_project(tmp_path / "data" / "projects")
    _set_nickname(p, nick)
    bl = blocklist_file(tmp_path / "data")
    with LF.FakeLLMServer(default={"content": RESP_2}) as srv:
        rc = G.main(_real_args(p, srv.base_url, bl, "--dry-run"), now=NOW)
        posts = [r for r in srv.requests if r.get("method") == "POST"]
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and "error" not in out, out
    assert len(posts) == out["stats"]["requests"] == 3 and out["counts"]["generated"] > 0
    assert out["stats"]["windows_privacy_skipped"] == 0


def test_control_nickname_still_generates(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects")
    _set_nickname(p, NICK_A)
    bl = blocklist_file(tmp_path / "data")
    with LF.FakeLLMServer(default={"content": RESP_2}) as srv:
        rc = G.main(_real_args(p, srv.base_url, bl, "--dry-run"), now=NOW)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["counts"]["generated"] > 0


def test_privacy_problems_chunks_and_templates():
    # 낱말 목록을 이어 붙인 자리는 닉네임이 아니다 — 조각마다 대조
    assert L.privacy_problems("점심, 메뉴, 보스", ["점심메뉴"]) == {}
    assert L.privacy_problems("점심메뉴, 보스", ["점심메뉴"]) == {"nickname": 1}
    assert L.privacy_problems("합성 닉네임-가 왔다", [NICK_A]) == {"nickname": 1}     # 조각 안 띄어쓰기·기호는 무시
    # 고정 틀 글자는 닉네임 대조에서 빠지고, 채운 자리는 그대로 본다
    msgs = G.build_messages(["떡볶이", "게임"], ["보스"], 1, 3)
    for m in msgs:
        assert L.privacy_problems(m["content"], ["방송", "채팅", "규칙", "자료", "요청"],
                                  templates=(G.SYSTEM_V1, G.USER_V1)) == {}
        assert L.privacy_problems(m["content"], ["방송"]).get("nickname") == 1           # 틀 없이는 걸린다(옛 동작)
    bad = G.build_messages(["떡볶이", NICK_A.lower()], [], 1, 3)[1]["content"]
    assert L.privacy_problems(bad, [NICK_A], templates=(G.SYSTEM_V1, G.USER_V1)) == {"nickname": 1}
    # 틀과 맞지 않는 글은 전체를 본다(틀로 빠져나가지 못함)
    assert L.privacy_problems("방송 이야기 " + G.SYSTEM_V1, ["방송"], templates=(G.SYSTEM_V1,)) == {"nickname": 1}
    # 식별자 꼴은 틀과 상관없이 글 전체
    idm = G.build_messages(["vw_abcdefgh2345"], [], 1, 3)[1]["content"]
    assert "viewer_id" in L.privacy_problems(idm, (), templates=(G.USER_V1,))


def test_generate_privacy_kinds_matches_client():
    msgs = G.build_messages(["점심", "메뉴"], ["보스"], 2, 3)
    assert G.privacy_kinds(msgs, ["점심메뉴", "방송", "채팅"]) == {}
    assert G.privacy_kinds(G.build_messages([NICK_A.lower()], [], 2, 3), [NICK_A]) == {"nickname": 1}


def test_client_templates_checked_at_creation_and_used():
    with pytest.raises(ValueError):
        L.LocalLLMClient(transport=LF.FakeTransport(), templates=("고정 vw_abcdefgh2345 틀",))
    tr = LF.FakeTransport(default={"content": "합성 줄"})
    c = L.LocalLLMClient(transport=tr, nicknames=["방송"], templates=(G.SYSTEM_V1, G.USER_V1), sleep=lambda s: None)
    c.chat(G.build_messages(["떡볶이"], [], 1, 2), model=LF.FAKE_MODEL)
    assert len(tr.calls) == 1
    with pytest.raises(L.LLMPrivacyError):
        c.chat(G.build_messages(["방송"], [], 1, 2), model=LF.FAKE_MODEL)                  # 채운 자리의 닉네임은 막음
    assert len(tr.calls) == 1


def test_privacy_refusal_skips_one_window_not_all(tmp_path):
    """클라이언트가 한 창을 privacy 로 막아도 나머지 창은 만든다(전체 종료 4 없음). 보낸 글자 수는 실제로 보낸 것만."""
    p = make_project(tmp_path / "data" / "projects")
    calls = []

    def chat(messages, **kw):
        calls.append(sum(len(m["content"]) for m in messages))
        if len(calls) == 2:
            raise L.LLMPrivacyError({"nickname": 1})
        return RESP_2
    _h, lines, st, _c = run(p, chat=chat)
    assert len(calls) == 3 and st["windows_privacy_skipped"] == 1 and st["requests"] == 2
    assert st["sent_chars"] == calls[0] + calls[2] and len(lines) > 0


def test_precheck_skips_window_before_sending(tmp_path, monkeypatch):
    p = make_project(tmp_path / "data" / "projects")
    seen = {"n": 0}
    real = G.privacy_kinds

    def fake_kinds(msgs, nicks):
        seen["n"] += 1
        return {"hash": 1} if seen["n"] == 1 else real(msgs, nicks)
    monkeypatch.setattr(G, "privacy_kinds", fake_kinds)
    _h, _l, st, chat = run(p)
    assert st["windows_privacy_skipped"] == 1 and chat.calls == 2 == st["requests"]


# ───────────────────────────────────────────────────────────── 2·3. identities.jsonl 없음 → 닫음

def test_no_identities_refused_api_and_cli(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects", "gen-noid", identities=False)
    with pytest.raises(G.GenerateError) as e:
        run(p)
    assert e.value.code == "nicknames_missing"
    fk = write_fake(tmp_path)
    bl = blocklist_file(tmp_path / "data")
    rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", fk, "--blocklist", bl, "--dry-run"], now=NOW)
    assert rc == 1 and json.loads(capsys.readouterr().out) == {"error": "nicknames_missing", "generated": 0}
    assert not os.path.exists(os.path.join(p, "scenarios"))


def test_no_identities_real_path_refused_no_post(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects", "gen-noid", identities=False)
    bl = blocklist_file(tmp_path / "data")
    with LF.FakeLLMServer(default={"content": RESP_2}) as srv:
        for extra in ((), ("--allow-no-identities",)):            # 실제 서버 길은 허용 인자가 있어도 거부
            rc = G.main(_real_args(p, srv.base_url, bl, "--dry-run", *extra), now=NOW)
            assert rc == 1 and json.loads(capsys.readouterr().out)["error"] == "nicknames_missing"
        posts = [r for r in srv.requests if r.get("method") == "POST"]
    assert posts == []


def test_no_identities_fake_opt_in_warns(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects", "gen-noid", identities=False)
    _h, _l, st, chat = run(p, allow_no_identities=True)
    assert st["nicknames_known"] is False and chat.calls == 3
    rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", write_fake(tmp_path), "--blocklist",
                 blocklist_file(tmp_path / "data"), "--dry-run", "--allow-no-identities"], now=NOW)
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert rc == 0 and "nicknames_missing" in out["warnings"] and "nicknames_missing" in cap.err
    assert NICK_A not in cap.out + cap.err


def _gen_file(path, project_id=None):
    head = {"kind": "generation.header", "generator": {"backend": "lmstudio", "model_id": "fake/synthetic"}}
    if project_id:
        head["project_id"] = project_id
    line = {"kind": "scenario.chat", "source": "generated", "chat_kind": "text", "show_vod_ms": 70000,
            "gen_id": "g0001", "text": "합성 생성 줄 하나예요"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(head, ensure_ascii=False) + "\n" + json.dumps(line, ensure_ascii=False) + "\n")
    return path


def test_make_scenario_generated_needs_identities(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects", "gen-noid", identities=False)
    bl = blocklist_file(tmp_path / "data")
    gf = _gen_file(os.path.join(p, "scenarios", "gen-x.jsonl"))
    base = ["--project", p, "--from", "0", "--to", "300000", "--threshold", "test", "--blocklist", bl,
            "--generated", gf, "--dry-run"]
    assert MS.main(base) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "nicknames_missing"
    assert MS.main(base + ["--allow-no-identities"]) == 0
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert "nicknames_missing" in out["warnings"] and out["generated"]["nicknames_known"] is False
    assert "nicknames_missing" in cap.err


# ───────────────────────────────────────────────────────────── 4. --out · --send-log 는 scenarios\ 아래만

def _protected_targets(p):
    os.makedirs(os.path.join(p, "labels"), exist_ok=True)
    lab = os.path.join(p, "labels", "x.jsonl")
    with open(lab, "w", encoding="utf-8") as f:
        f.write('{"kind":"synthetic-label"}\n')
    return [os.path.join(p, "chat", "messages.jsonl"), lab]


def test_out_outside_scenarios_refused(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects")
    fk = write_fake(tmp_path)
    bl = blocklist_file(tmp_path / "data")
    scen = os.path.join(p, "scenarios")
    os.makedirs(scen, exist_ok=True)
    send_log = os.path.join(scen, "llm-send.jsonl")
    with open(send_log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"schema": G.SEND_LOG_SCHEMA, "sent_chars": 1}) + "\n")
    bad = _protected_targets(p) + [
        os.path.join(scen, "..", "chat", "messages.jsonl"),          # '..' 로 빠져나가기
        os.path.join(scen, "SC-20261023-abcdef.jsonl"),              # 시나리오 파일 자리
        os.path.join(scen, "gen.txt"),                                # .jsonl 아님
        send_log,                                                     # 보낸 기록 파일(첫 줄이 generation.header 아님)
        os.path.join(str(tmp_path), "elsewhere", "gen.jsonl"),       # 프로젝트 밖
        scen,                                                         # 폴더 자체
    ]
    hashes = {b: _sha(b) for b in bad if os.path.isfile(b)}
    for b in bad:
        rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", fk, "--blocklist", bl, "--out", b],
                    now=NOW)
        assert rc == 1, b
        assert json.loads(capsys.readouterr().out) == {"error": "out_path_unprotected"}, b
    for b, h in hashes.items():
        assert _sha(b) == h, b
    assert not os.path.exists(os.path.join(str(tmp_path), "elsewhere"))
    # 같은 자리에 다시 쓰기(앞서 쓴 생성 후보 파일)는 된다
    ok = os.path.join(scen, "gen-a.jsonl")
    for _ in range(2):
        rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", fk, "--blocklist", bl, "--out", ok],
                    now=NOW)
        assert rc == 0 and json.loads(capsys.readouterr().out)["written"] is True


def test_write_generation_file_refuses_other_files(tmp_path):
    p = make_project(tmp_path / "data" / "projects")
    msgs = os.path.join(p, "chat", "messages.jsonl")
    h = _sha(msgs)
    head = {"project_id": "x", "generator": {"backend": "lmstudio"}, "filters": {}}
    for pd in (p, None):                                             # 프로젝트를 몰라도 다른 종류 파일은 덮지 않는다
        with pytest.raises(G.GenerateError) as e:
            G.write_generation_file(msgs, head, [], project_dir=pd)
        assert e.value.code == "out_path_unprotected"
    assert _sha(msgs) == h


def test_send_log_outside_scenarios_refused_before_post(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects")
    bl = blocklist_file(tmp_path / "data")
    targets = _protected_targets(p) + [os.path.join(p, "scenarios", "SC-20261023-abcdef.jsonl")]
    hashes = {t: _sha(t) for t in targets if os.path.isfile(t)}
    with LF.FakeLLMServer(default={"content": RESP_2}) as srv:
        for t in targets:
            rc = G.main(_real_args(p, srv.base_url, bl, "--send-log", t, "--dry-run"), now=NOW)
            assert rc == 1, t
            assert json.loads(capsys.readouterr().out) == {"error": "send_log_unprotected"}, t
        assert srv.requests == [] or all(r.get("method") != "POST" for r in srv.requests)
        # --fake 길에서도 같은 규칙(쓰이지 않는 값이어도 거부)
        rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", write_fake(tmp_path), "--blocklist", bl,
                     "--send-log", targets[0], "--dry-run"], now=NOW)
        assert rc == 1 and json.loads(capsys.readouterr().out)["error"] == "send_log_unprotected"
        # 기본 자리(scenarios\llm-send.jsonl)는 된다 — dry-run 에서도 부른 기록은 남는다(D-06 5)
        rc = G.main(_real_args(p, srv.base_url, bl, "--dry-run"), now=NOW)
        assert rc == 0 and json.loads(capsys.readouterr().out)["counts"]["generated"] > 0
    for t, h in hashes.items():
        assert _sha(t) == h, t
    with open(os.path.join(p, "scenarios", "llm-send.jsonl"), encoding="utf-8") as f:
        assert all(json.loads(x)["schema"] == G.SEND_LOG_SCHEMA for x in f if x.strip())


def test_make_local_chat_refuses_foreign_log_file(tmp_path):
    p = make_project(tmp_path / "data" / "projects")
    with pytest.raises(G.GenerateError) as e:
        G.make_local_chat("http://127.0.0.1:1", LF.FAKE_MODEL, log_path=os.path.join(p, "chat", "messages.jsonl"),
                          check=False)
    assert e.value.code == "send_log_unprotected"


def test_out_and_send_log_same_file_refused(tmp_path, capsys):
    p = make_project(tmp_path / "data" / "projects")
    same = os.path.join(p, "scenarios", "both.jsonl")
    rc = G.main(["--project", p, "--from", "0", "--to", "300000", "--fake", write_fake(tmp_path), "--blocklist",
                 blocklist_file(tmp_path / "data"), "--out", same, "--send-log", same], now=NOW)
    assert rc == 1 and json.loads(capsys.readouterr().out)["error"] == "out_path_unprotected"
    assert not os.path.exists(same)


def test_send_log_schema_matches_client():
    assert G.SEND_LOG_SCHEMA == L.RECORD_SCHEMA


# ───────────────────────────────────────────────────────────── low

def test_filter_line_fullwidth_forms():
    kw = dict(blocklist=(), nicknames=(), copy_index=G.CopyIndex([]), talk_words=frozenset(), accepted=(),
              cfg=G.GenerateConfig())
    assert G.filter_line("＠합성닉 오늘도 재밌어요", **kw)[0] == "mention"
    assert G.filter_line("@합성닉 오늘도 재밌어요", **kw)[0] == "mention"
    assert G.filter_line("ｗｗｗ．ｅｘａｍｐｌｅ．ｃｏｍ 가보세요", **kw)[0] == "link"
    assert G.filter_line("［공지］ 합성 안내 말", **kw)[0] == "system_text"
    assert G.filter_line("오늘도 재밌어요 정말", **kw)[0] is None


def test_make_scenario_refuses_pilot_input_paths_before_open(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    p = make_project(data / "projects")
    seal = _gen_file(os.path.join(str(data), "ai", "pilot-99999999", "gen.jsonl"))
    opened = []
    real_open = builtins.open

    def watch(f, *a, **k):
        if "pilot-" in str(f).lower():
            opened.append(1)
        return real_open(f, *a, **k)
    monkeypatch.setattr(builtins, "open", watch)
    base = ["--project", p, "--from", "0", "--to", "300000", "--threshold", "test", "--dry-run"]
    for extra in (["--generated", seal], ["--topic-flow", seal], ["--blocklist", seal],
                  ["--projects-root", os.path.dirname(seal)]):
        assert MS.main(base + extra) == 2, extra
        assert json.loads(capsys.readouterr().out)["error"] == "pilot_locked"
    assert opened == []
    assert MS.inside_pilot(seal) and not MS.inside_pilot(p) and MS.inside_pilot(p) == G.inside_pilot(p)


def test_retrieve_unchanged_for_real_nickname_rule():
    """retrieve 의 닉네임 정규화 규칙(2자 이상)과 llm_local 이 같다(대조 기준 한 벌)."""
    assert L.NICKNAME_MIN_LEN == 2 and L.prepare_nicknames(["가", NICK_A]) == frozenset({R._squash(NICK_A)})
