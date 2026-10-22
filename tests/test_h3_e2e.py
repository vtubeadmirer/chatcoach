"""H-3 통합: 가짜 LLM 생성 → G 채점기 거르기 → 시나리오 → 스트리머 훈련 섞기 (tools/gen_e2e.py, 08 D-23 4).

자료는 모두 합성(tests/test_generate 의 합성 프로젝트·합성 닉네임·합성 금칙어). 실제 LM Studio·외부 주소는 부르지 않는다
(가짜 서버는 127.0.0.1 에만, 그 밖 연결은 gen_e2e.loopback_only 가 막고 센다).
"""
from __future__ import annotations

import builtins
import json
import os
import sys

import pytest

from engine import generate as G
from engine import llm_fake as LF
from engine import llm_local as L

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import gen_e2e as E  # noqa: E402
import make_scenario as MS  # noqa: E402

from tests.test_generate import NICK_A, NICK_B, blocklist_file, make_project  # noqa: E402


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _src(tmp_path):
    data = tmp_path / "src" / "chatcoach-data"
    p = make_project(data / "projects")
    blocklist_file(data)
    return p


# ───────────────────────────────────────────────────────────── 끝에서 끝까지

def test_e2e_synthetic_project_all_checks(tmp_path, qapp):
    """합성 프로젝트: --fake CLI → 가짜 HTTP(127.0.0.1) → make_scenario --generated → 훈련 화면 섞기(가·생 표시) →
    바이트 검사 0건 → 자리 지움. 외부 연결 0."""
    src = _src(tmp_path)
    before = {os.path.relpath(os.path.join(d, f), src) for d, _s, fs in os.walk(src) for f in fs}
    base = tmp_path / "e2e"
    res = E.run_e2e(src, base=str(base), from_ms=0, to_ms=300000, threshold="test", verbose=False)
    bad = {k: v for k, v in res["checks"].items() if not (v if isinstance(v, bool) else v.get("ok"))}
    assert res["ok"], bad
    assert res["cleaned"] and not base.exists()
    n = res["numbers"]
    assert n["gen_b"]["http_posts"] == n["gen_b"]["requests"] > 0 and n["send_log"]["records"] == n["gen_b"]["requests"]
    assert n["training"]["generated_rows"] > 0 and n["training"]["marks_generated"] == n["training"]["generated_rows"]
    assert (n["gen_b"]["dropped"] or {}).get("mention", 0) >= 1                    # 요청마다 넣은 멘션 줄은 거르기 4
    assert res["user_clicks"] == 0
    # 원 프로젝트는 읽기만(파일 목록 그대로, scenarios\·training\ 없음)
    after = {os.path.relpath(os.path.join(d, f), src) for d, _s, fs in os.walk(src) for f in fs}
    assert after == before
    # 결과(숫자·코드)에 합성 닉네임·본문이 없다
    dump = json.dumps(res, ensure_ascii=False)
    for s in (NICK_A, NICK_B, "합성 붐빔 채팅", E.BAD_LINE):
        assert s not in dump


def test_e2e_refuses_pilot_source_before_reading(tmp_path, monkeypatch, capsys):
    data = tmp_path / "src" / "chatcoach-data"
    p = make_project(data / "projects", name="pilot-15000009")
    opened = []
    real_open = builtins.open

    def watch(f, *a, **k):
        if "pilot-15000009" in str(f):
            opened.append(1)
        return real_open(f, *a, **k)
    monkeypatch.setattr(builtins, "open", watch)
    with pytest.raises(E.E2EError):
        E.run_e2e(p, base=str(tmp_path / "e2e"), from_ms=0, to_ms=300000, verbose=False)
    assert E.main(["--project", p, "--base", str(tmp_path / "e2e2"), "--from", "0", "--to", "300000"]) == 2
    assert opened == [] and not (tmp_path / "e2e").exists() and not (tmp_path / "e2e2").exists()
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["error"] == "input"


def test_e2e_base_rules(tmp_path):
    with pytest.raises(E.E2EError):
        E.check_base(os.path.join(os.path.dirname(E.ROOT), "chatcoach-data", "ChatCoachSmoke", "x"))
    with pytest.raises(E.E2EError):
        E.check_base(r"C:\somewhere\else")
    (tmp_path / "exists").mkdir()
    with pytest.raises(E.E2EError):
        E.check_base(str(tmp_path / "exists"))
    assert E.check_base(str(tmp_path / "new")) == str(tmp_path / "new")


def test_loopback_only_blocks_outside(tmp_path):
    import socket
    with E.loopback_only([1]) as seen:
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection(("192.0.2.1", 80), timeout=0.1)
        s = socket.socket()
        try:
            with pytest.raises(ConnectionRefusedError):
                s.connect(("127.0.0.1", 1234))                 # 실제 LM Studio 포트도 막는다
        finally:
            s.close()
    assert seen == {"allowed": 0, "blocked": 2}
    orig = socket.socket.connect
    with E.loopback_only(()):
        assert socket.socket.connect is not orig
    assert socket.socket.connect is orig                                           # 블록 밖에서는 되돌린다


# ───────────────────────────────────────────────────────────── 이음(이름 맞춤)

def test_make_scenario_public_bridge_names():
    """engine.generate 가 파일 경로로 빌려 쓰는 공개 이름(store_api·pilot_api)이 있고, generate 가 그것으로 이음을 연다."""
    assert callable(MS.store_api) and callable(MS.pilot_api)
    st = G._tool_store()
    assert st is not None and len(st) == 4 and callable(st[0]) and callable(st[1]) and callable(st[3])


@pytest.mark.parametrize("url", ["http://127.0.0.1:1234", "http://localhost:1234", "http://[::1]:1234",
                                 "http://192.168.0.5:1234", "http://2130706433:1234", "http://[::ffff:127.0.0.1]:1234",
                                 "http://localhost.:1234", "http://127.0.0.1:1234/?q=1", "http://u@127.0.0.1:1234"])
def test_generate_uses_llm_local_address_rule(url):
    """주소 규칙은 한 벌: generate.is_local_url == llm_local.is_local_url."""
    assert G.is_local_url(url) == L.is_local_url(url)


def test_make_local_chat_uses_client_and_maps_codes(monkeypatch):
    with LF.FakeLLMServer(default={"respond": lambda msgs: "합성 응답 한 줄"}) as srv:
        fn = G.make_local_chat(srv.base_url, LF.FAKE_MODEL)
        assert fn.model_id == LF.FAKE_MODEL and isinstance(fn.client, L.LocalLLMClient)
        res = fn([{"role": "user", "content": "합성 요청"}], temperature=0.5, max_tokens=8, seed=1,
                 prompt_template_id=G.PROMPT_TEMPLATE_ID)
        assert res.text == "합성 응답 한 줄" and fn.client.records[-1]["ok"] is True
        with pytest.raises(G.GenerateError) as e:
            G.make_local_chat(srv.base_url, "없는-모델")
        assert e.value.code == "model_missing"
    with pytest.raises(G.GenerateError) as e:
        G.make_local_chat(f"http://127.0.0.1:{LF.closed_port()}", "m")
    assert e.value.code == "lmstudio_off"


def test_send_log_folder_is_created(tmp_path):
    log = tmp_path / "새 폴더" / "llm-send.jsonl"
    c = L.LocalLLMClient(transport=LF.FakeTransport(), log_path=str(log), sleep=lambda s: None)
    c.chat([{"role": "user", "content": "합성 요청"}], model=LF.FAKE_MODEL)
    assert log.is_file() and len(log.read_text(encoding="utf-8").splitlines()) == 1


# ───────────────────────────────────────────────────────────── 가짜 LLM 'respond' 단계

def test_fake_respond_step_server_and_transport():
    seen = []

    def respond(msgs):
        seen.append(len(msgs))
        return "\n".join(f"{m['role']}:{len(m['content'])}" for m in msgs)
    t = LF.FakeTransport(default={"respond": respond})
    c = L.LocalLLMClient(transport=t, sleep=lambda s: None)
    r = c.chat([{"role": "system", "content": "가"}, {"role": "user", "content": "나다"}], model=LF.FAKE_MODEL)
    assert r.text == "system:1\nuser:2" and seen == [2]
    with LF.FakeLLMServer(default={"respond": respond}) as srv:
        c2 = L.LocalLLMClient(srv.base_url, sleep=lambda s: None)
        assert c2.chat([{"role": "user", "content": "라"}], model=LF.FAKE_MODEL).text == "user:1"

    def boom(_msgs):
        raise RuntimeError("x")
    c3 = L.LocalLLMClient(transport=LF.FakeTransport(default={"respond": boom}), retries=0, sleep=lambda s: None)
    with pytest.raises(L.LLMError) as e:
        c3.chat([{"role": "user", "content": "마"}], model=LF.FAKE_MODEL)
    assert e.value.code == "server_error"


def test_adaptive_respond_uses_prompt_words_only():
    msgs = G.build_messages(["점심", "메뉴"], ["보스"], 3, 4)
    out = E.adaptive_respond(msgs)
    lines = out.splitlines()
    assert len(lines) == 5 and lines[-1].endswith(E.BAD_LINE)
    assert all(any(w in ln for w in ("점심", "메뉴", "보스")) for ln in lines[:-1])
    assert E.adaptive_respond(msgs) == out                                        # 결정적


# ───────────────────────────────────────────────────────────── 생성 몫 먼저(retrieved 바꾸기)

class _W:
    def __init__(self, a, b, real, target):
        self.start_ms, self.end_ms, self.real, self.target, self.quiet = a, b, real, target, True


def _ret(t, score):
    return {"kind": "scenario.chat", "source": "retrieved", "show_vod_ms": t, "retrieval_score": score,
            "from_project_id": "p", "from_chat_id": f"c{t}", "display_mask": "가*"}


def test_displace_retrieved_per_window_and_cap():
    ws = [_W(0, 60000, 2, 6), _W(60000, 120000, 2, 6)]
    chats = [_ret(1000, 0.9), _ret(2000, 0.1), _ret(3000, 0.5), _ret(4000, 0.1),   # 창 0: 실제 2 + 4 = 목표
             _ret(70000, 0.3)]                                                     # 창 1: 실제 2 + 1
    kept = [{"source": "generated", "gen_id": "g0001", "show_vod_ms": 5000, "text": "합성"},
            {"source": "generated", "gen_id": "g0002", "show_vod_ms": 6000, "text": "합성"},
            {"source": "generated", "gen_id": "g0003", "show_vod_ms": 80000, "text": "합성"}]
    out, n = MS.displace_retrieved(chats, kept, ws, cap_virtual=100, range_ms=(0, 120000))
    # 창 0 은 2줄 넘침 → 점수 0.1 두 줄(늦은 것 먼저 같은 점수면 늦게 보이는 것부터)만 빠진다. 창 1 은 자리가 있다
    assert n == 2 and [c["show_vod_ms"] for c in out] == [1000, 3000, 70000]
    out2, n2 = MS.displace_retrieved(chats, kept, ws, cap_virtual=5, range_ms=(0, 120000))
    # 구간 상한 5: 창 기준으로 2줄, 그 뒤 retrieved 3 + 생성 3 = 6 > 5 → 남은 것 가운데 가장 낮은 0.3 하나 더
    assert n2 == 3 and [c["show_vod_ms"] for c in out2] == [1000, 3000]
    assert chats[1]["show_vod_ms"] == 2000                                         # 입력은 고치지 않는다


def test_make_scenario_generated_mode_option(tmp_path, capsys):
    """--generated-mode 는 replace(기본)·room 만. room 은 지난 방식(retrieved 가 채운 뒤 남은 자리)."""
    p = _src(tmp_path)
    with pytest.raises(SystemExit):
        MS.main(["--project", p, "--from", "0", "--to", "300000", "--generated-mode", "nope", "--dry-run"])
    capsys.readouterr()
    with pytest.raises(MS.GenInputError) as e:
        MS.merge_generated(p, 0, 300000, {}, [], path=os.path.join(p, "none.jsonl"), blocklist_path=None,
                           topic_flow_path=None, seed=0, cfg=MS.R.RetrieveConfig(), max_generated_ratio=0.25,
                           mode="nope")
    assert e.value.code == "no_generated"                                          # 파일 검사가 먼저(읽기 전 거부)
