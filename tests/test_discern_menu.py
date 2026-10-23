"""메인 창 '훈련 → 가상 채팅 가려내기' 통합 (H-5, 20 7장, 25 6-2, 08 D-24 7 가) — app/ui/mainwindow.py ·
app/ui/discern_view.py · app/discern_store.py.

합성 데이터만 쓴다: 합성 최종 세트 프로젝트(tests.test_final_set.proj)에 합성 후보 채팅·합성 원 닉네임·합성 전사 낱말을
더하고, '가상 채팅 만들기' 를 가짜 LM Studio(engine.llm_fake.FakeLLMServer, 127.0.0.1 빈 포트)로 돌려 생성 줄이 든
시나리오를 만든다. 실제 LM Studio·외부 호출·시범 폴더는 쓰지 않는다.
사람 클릭(D-16): 가려내기 열기 = 메뉴 '훈련' + '가상 채팅 가려내기' = 2, 판정 = 채팅마다 1, 정답 = 0(구간 끝), 닫기 1.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from app import discern_store as ds  # noqa: E402
from app import reference_set as rsm  # noqa: E402
from app import training_store as ts  # noqa: E402
from app.core import discern as dc  # noqa: E402
from app.ui import discern_view as dv  # noqa: E402
from tests.test_final_set import _clear_locks, cid, proj  # noqa: E402,F401
from tests.test_integration_c4 import ctx, qapp  # noqa: E402,F401
from tests.test_training_menu import (_add_outside_chats, _gen_setup, _inproc_gen_runner, _Loopback, _open,  # noqa: E402
                                      _respond, _set_llm, win)  # noqa: F401

P2 = "L01-P-20261031-bb22"


def _make_generated(win, root, monkeypatch):
    """'가상 채팅 만들기' 한 번을 가짜 서버로 — 생성 줄이 든 시나리오. (결과, 서버 요청 목록)."""
    from engine import llm_fake as LF
    rng = win._training_range(rsm.training_availability(root))
    _add_outside_chats(root, rng)
    _gen_setup(root, rng)
    calls = []
    win.scenario_runner = _inproc_gen_runner(calls)
    with LF.FakeLLMServer(default={"respond": _respond}, models=("synthetic-model",)) as srv:
        guard = _Loopback(monkeypatch, allow={srv.port})
        _set_llm(win, srv.base_url)
        _open(win, root, None)
        res = win.make_virtual_chat()
        reqs = list(srv.requests)
    assert calls == ["check", "generate", "make"] and guard.blocked == []
    assert res["written"] is True and res["counts"]["generated"] > 0
    # 가려내기·훈련은 LM Studio 에 다시 붙지 않는다 — 설정을 비워 둔다
    _set_llm(win, "", model="")
    return res, reqs


def test_discern_menu_end_to_end(win, proj, monkeypatch):  # noqa: F811
    root = proj["root"]
    # 시나리오가 없으면 꺼지고 메뉴 안에 '만들기 먼저' 한 줄
    _open(win, root, None)
    assert not win.a_discern.isEnabled() and win.a_discern_reason.isVisible()
    assert "가상 채팅 만들기" in win.a_discern_reason.text()
    win.show_home()
    res, _reqs = _make_generated(win, root, monkeypatch)
    win.update_training_actions()
    assert win.a_discern.isEnabled() and not win.a_discern_reason.isVisible()
    titles = [a.text() for a in win.m_training.actions() if a.isVisible() and not a.isSeparator()]
    assert titles[-1] == "가상 채팅 가려내기(&D)"
    before = {os.path.relpath(os.path.join(d, f), root) for d, _x, fs in os.walk(root) for f in fs}
    win.a_discern.trigger()                                       # 메뉴 한 번 = 열림(고르는 창 없음)
    view = win.discern_view
    assert isinstance(view, dv.DiscernView) and win.stack.currentWidget() is view and view.state() == "judging"
    assert not view.is_muted()                                    # 소리 켜고
    assert view.banner.isVisibleTo(view) and view.banner.text() == dv.BANNER_GENERATED
    tg = view.targets()
    srcs = {t.source for t in tg.values()}
    assert dc.SRC_GENERATED in srcs and dc.SRC_REPLAY in srcs
    # 판정 중에는 가상 표시가 없다(닉네임 칸 끝의 '· 가져옴'·'· 생성', 앞의 '가 '·'생 ')
    nick = {r["chat_id"]: r.get("nickname_masked") or "" for r in view._rows}
    assert not any(n.endswith(("가져옴", "생성")) or n.startswith(("가 ", "생 ")) for n in nick.values())
    # 다른 훈련·분석·일치도 보고서는 꺼진다
    win.update_training_actions()
    assert not win.a_streamer_training.isEnabled() and not win.a_viewer_training.isEnabled()
    assert not win.a_discern.isEnabled()
    assert win.agreement_block_reason() is not None
    # 판정: 대상마다 한 번(시각 순). 생성 → 가짜, 가져옴 → 진짜, 실제 → 진짜, 첫 실제 하나는 모름
    seg = view._segment
    view.seek_vod(seg[1] - 1)
    want = {}
    first_real = True
    for disp, t in sorted(tg.items(), key=lambda kv: kv[1].vod_ms):
        a = dc.VIRTUAL if t.source == dc.SRC_GENERATED else dc.REAL
        if t.source == dc.SRC_REPLAY and first_real:
            a, first_real = dc.UNSURE, False
        assert view.judge(disp, a) == a
        want[t.key] = a
    assert view.answers() == want
    # 구간 끝 = 정답(사람 클릭 0)
    view.player.positionChanged.emit(view._to_file(seg[1]))
    assert view.state() == "result"
    r = view.result()
    n_gen = sum(1 for t in tg.values() if t.source == dc.SRC_GENERATED)
    assert r.detection == {"hit": n_gen, "n": n_gen, "rate": 1.0}
    assert r.judged == len(tg) and r.unseen == 0
    data = view.chat.chat_data()
    assert any(n.endswith("생성") and n.startswith("생 ") for n in data.nicks)   # 끝나면 표시가 다시 붙는다
    marks = view.answer_marks()
    assert len(marks) == len(tg) and sum(m.startswith("✓") for m in marks.values()) == len(tg) - 1
    assert sum(m.startswith("?") for m in marks.values()) == 1
    assert view.banner.text() == dv.BANNER_RESULT
    # 닫기 1번 → 작업대
    view.btn_close.click()
    assert win.discern_view is None and win.stack.currentWidget() is win.workbench
    # 기록: discern\ 하나만 새로. start · judge × n · end(segment_end). 본문·닉네임·출처 칸 없음. 노출 0
    after = {os.path.relpath(os.path.join(d, f), root) for d, _x, fs in os.walk(root) for f in fs}
    new = after - before
    assert len(new) == 1 and next(iter(new)).startswith("discern" + os.sep)
    sids = ds.list_sessions(root)
    recs, summ = ds.read_valid(ds.session_path(root, sids[0]))
    kinds = [x["kind"] for x in recs]
    assert kinds == [ds.K_START] + [ds.K_JUDGE] * len(tg) + [ds.K_END] and summ["invalid"] == 0
    assert recs[-1]["body"]["reason"] == "segment_end" and recs[-1]["body"]["result"]["judged"] == len(tg)
    blob = open(ds.session_path(root, sids[0]), "rb").read()
    assert "합성 본문".encode() not in blob and "시**".encode() not in blob and b'"source"' not in blob
    assert b'"text"' not in blob and b'"nickname' not in blob
    from app import scenario_store as sst
    scn = sst.load_scenario(root, sst.list_scenarios(root)[0])
    assert not any(c["text"].encode() in blob for c in scn.records if c.get("source") == "generated")
    assert ts.list_sessions(root) == []                           # 노출 아님(training\ 에 쓰지 않음)
    from app import backup
    assert backup.in_protected_zone(f"discern/{sids[0]}.jsonl", backup.protected_patterns(win.workbench.project.data))
    # 두 번째로 열면 '다시 봄'·'본 적 있음'
    win.update_training_actions()
    view2 = win.discern_training()
    assert view2 is not None
    st = ds.read_valid(ds.session_path(root, [s for s in ds.list_sessions(root) if s != sids[0]][0]))[0][0]
    assert st["body"]["repeat"] is True and st["body"]["seen_before"] is True
    win.close_discern()                                            # 정답 전 닫기 = end closed
    last = ds.read_valid(ds.session_path(root, [s for s in ds.list_sessions(root) if s != sids[0]][0]))[0][-1]
    assert last["kind"] == ds.K_END and last["body"]["reason"] == "closed"
    win.show_home()


def test_discern_menu_off_in_labeling_session_never_opens_scenarios(win, proj, monkeypatch):  # noqa: F811
    """라벨링 세션(1차)에서는 메뉴가 꺼지고 scenarios\\ 를 0번 연다(20 1장 4번). 다시 열기도 하지 않는다."""
    from app import scenario_mix
    from app import scenario_store as sst
    root = proj["root"]
    os.makedirs(os.path.join(root, "scenarios"), exist_ok=True)
    touched = []
    monkeypatch.setattr(scenario_mix, "find_scenario", lambda *a, **k: touched.append("find") or (None, {}))
    monkeypatch.setattr(sst, "latest_scenario", lambda *a, **k: touched.append("latest") or (None, {}))
    real_listdir = os.listdir
    monkeypatch.setattr(os, "listdir", lambda p=".": (touched.append("ls") if "scenarios" in str(p) else None)
                        or real_listdir(p))
    wb = _open(win, root, ctx(P2, "primary", "L01"))
    assert not win.a_discern.isEnabled() and win.a_discern_reason.text().endswith(dv.REASON_LABELING)
    assert win.discern_training() is None and win.discern_view is None and wb is win.workbench
    assert wb.session is not None                                 # 세션을 닫지 않았다
    assert touched == [] and ds.list_sessions(root) == []
    win.show_home()


def test_discern_menu_off_for_pilot_and_without_media(win, proj, tmp_path, monkeypatch):  # noqa: F811
    """시범 이름 프로젝트(잠기기 전)·영상 없음이면 꺼짐. 시범이면 scenarios\\ 를 열지 않는다."""
    import shutil
    from app import scenario_mix
    root = proj["root"]
    _open(win, root, None, media=False)
    assert not win.a_discern.isEnabled() and dv.REASON_NO_MEDIA in win.a_discern_reason.text()
    win.show_home()
    pilot = os.path.join(os.path.dirname(root), "pilot-합성")
    shutil.copytree(root, pilot)
    try:
        monkeypatch.setattr(scenario_mix, "find_scenario", lambda *a, **k: pytest.fail("시범 폴더 시나리오를 열었음"))
        _open(win, pilot, None)
        assert not win.a_discern.isEnabled()
        assert win.discern_training() is None and not os.path.isdir(os.path.join(pilot, "discern"))
        win.show_home()
    finally:
        shutil.rmtree(pilot)


def test_blocked_discern_start_leaves_no_empty_file(win, proj, monkeypatch):  # noqa: F811
    """화면이 start 에서 막히면 먼저 만든 0바이트 discern\\<id>.jsonl 을 지운다."""
    root = proj["root"]
    _make_generated(win, root, monkeypatch)
    win.show_home()
    _open(win, root, None)

    def blocked(self, **_k):
        self.set_blocked("합성 막힘")
        return False
    monkeypatch.setattr(dv.DiscernView, "start", blocked)
    for _ in range(2):
        assert win.discern_training() is None
        assert win.statusBar().currentMessage() == "합성 막힘"
    assert ds.list_sessions(root) == []
    win.show_home()


def test_gui_smoke_hand_calc_matches_evaluate():
    """gui_smoke --discern-flow 의 손 계산(discern_hand_calc, core 를 쓰지 않음)과 core.discern.evaluate 가 같은 판에서
    같은 숫자를 낸다 — 생성 4 · 가져옴 2 · 실제 4 의 합성 판정 10건 + 안 봄 3."""
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("_gui_smoke_ds", os.path.join(here, "tools", "gui_smoke.py"))
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)
    hc = gs.discern_hand_calc()
    assert hc["confusion"] == [[3, 2, 1], [1, 2, 1], [0, 0, 0]]
    assert abs(hc["kappa"] - 1 / 6) < 1e-12 and abs(hc["p_o"] - 0.5) < 1e-12 and abs(hc["p_e"] - 0.4) < 1e-12
    targets, answers = [], {}
    n = 0
    for src, ans in gs.DISCERN_PLAN:
        for a in ans:
            n += 1
            k = cid(n) if src == dc.SRC_REPLAY else dc.target_key(scenario_id="SC-20261023-abcd", line_id=f"s{n:04d}")
            targets.append(dc.Target(k, src, n * 1000))
            answers[k] = a
    for i in range(3):                                             # 안 봄
        targets.append(dc.Target(cid(90 + i), dc.SRC_REPLAY, 50_000 + i))
    r = dc.evaluate(targets, answers)
    assert [list(x) for x in r.confusion["rows"]] == hc["confusion"]
    assert abs(r.kappa - hc["kappa"]) < 1e-9 and abs(r.p_o - hc["p_o"]) < 1e-9 and abs(r.p_e - hc["p_e"]) < 1e-9
    for k in ("detection", "false_alarm", "retrieved", "unsure"):
        got = getattr(r, k)
        assert (got["hit"], got["n"]) == (hc[k]["hit"], hc[k]["n"]) and abs(got["rate"] - hc[k]["rate"]) < 1e-9, k
    assert r.judged == 10 and r.unseen == 3 and r.gate["status"] == dc.GATE_LOW_N
    marks = [dc.answer_mark(row) for row in r.rows if row.get("answer") is not None]
    assert {h: sum(m.startswith(h) for m in marks) for h in ("✓", "?", "✗")} == hc["marks"]
    line = (f"가짜 탐지율 {gs._fmt_hand(hc['detection'])} · κ {hc['kappa']:.2f} · 진짜 오인율 "
            f"{gs._fmt_hand(hc['false_alarm'])} · 가져옴 오인율 {gs._fmt_hand(hc['retrieved'])} · 모르겠음 "
            f"{gs._fmt_hand(hc['unsure'])}")
    assert line in dv.result_lines(r)


def test_make_scenario_carries_provenance_fields(win, proj, monkeypatch):  # noqa: F811
    """D-24 1: 메뉴 길 시나리오에도 응답 해시 · 생성 후보 파일 해시 · 새 거르기 판 이름이 들어간다(make_scenario)."""
    import hashlib
    from app import scenario_mix
    from app import scenario_store as sst
    root = proj["root"]
    _make_generated(win, root, monkeypatch)
    scn = sst.load_scenario(root, sst.list_scenarios(root)[0])
    h = scn.header
    rng = (h["range"]["vod_from_ms"], h["range"]["vod_to_ms"])
    gen = scenario_mix.generation_path(root, rng)
    with open(gen, "rb") as f:
        assert h["inputs"]["generated_sha256"] == hashlib.sha256(f.read()).hexdigest()
    assert len(h["generator"].get("response_sha256", "")) == 64
    vers = set(h["filters"]["versions"])
    assert {"gscore-v1", "llm-check-v1", "length-v1", "idform-v1", "dup-v1"} <= vers
    assert json.dumps(h, ensure_ascii=False).count("합성닉네임가") == 0
    win.show_home()
