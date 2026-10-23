"""H-5 가려내기 묶음 검토 고침의 재현 시험 (2026-10-23).

1. (중간) 관문 통과·실패를 낼 길이 없었다 — 화면은 늘 broadcasts=1 이라 '표본 적음'. → app.discern_report ·
   tools/discern_report.py 가 프로젝트(= 방송)마다의 가려내기 기록을 다시 읽어 evaluate_sessions 로 낸다.
2. (중간) 조정 세션에서 '가상 채팅 만들기' 가 보이고 scenarios\\ 를 열어 생성까지 갔다(20 1장 4번 위반). →
   training_needs_reopen 이 조정도 라벨링으로 본다(가려내기 메뉴의 LABELING_KINDS 와 같은 판단).
3. (낮음) 모두 '모르겠음'·모두 '진짜' 처럼 판정이 한쪽에 몰린 판이 관문을 '통과' 했다. → 'unknown'(판단 불가).

**합성** 자료만 쓴다(합성 프로젝트 ID·chat_id·본문·가림 이름). 쓰기는 tmp_path(conftest 가 %LOCALAPPDATA%\\
ChatCoachSmoke\\pytest 아래로 옮긴다). 실제 프로젝트·시범 폴더·LM Studio 는 쓰지 않는다.
"""
from __future__ import annotations

import builtins
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import discern_report as dr
from app import discern_store as ds
from app import scenario_store as sst
from app.core import discern as dc
from app.core import scenario as sc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from tests.test_final_set import proj  # noqa: E402,F401
from tests.test_integration_c4 import ctx, qapp  # noqa: E402,F401
from tests.test_training_menu import _open, win  # noqa: E402,F401

KST = timezone(timedelta(hours=9))
PID1 = "11111111-2222-4333-8444-000000000001"
PID2 = "11111111-2222-4333-8444-000000000002"
RANGE = (1_200_000, 2_400_000)


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 23, 10, 0, 0, tzinfo=KST)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def _cid(k: int, i: int) -> str:
    return "c%020x" % (k * 1000 + i)


def _header(pid: str) -> dict:
    return {"project_id": pid, "range": {"vod_from_ms": RANGE[0], "vod_to_ms": RANGE[1]},
            "created_at": "2026-10-22T10:00:00.000+09:00", "created_by": "engine scenario 0.1.0",
            "inputs": {"messages_sha256": "a" * 64, "from_projects": []},
            "fill": {"quiet_window_ms": 60000, "quiet_max_real": 2, "target_per_min": 6, "max_virtual_ratio": 0.5},
            "retrieval": {"method": "topic_words", "min_score": 0.2, "max_per_viewer": 3, "seed": 7},
            "generator": {"backend": "none"}, "filters": {"versions": ["nickname-v1"], "dropped": {}},
            "external": {"enabled": False}}


def _generated(g: int, show: int) -> dict:
    return {"source": "generated", "chat_kind": "text", "show_vod_ms": show, "gen_id": f"g{g:04d}",
            "text": f"합성 생성 문장 {g}", "context": {"asr_from_ms": show - 120000, "asr_to_ms": show, "topic_id": "t03"},
            "display_mask": "두**", "emotes": [], "checks": {"nickname_hit": 0, "copy_max": 0.1, "blocked": 0, "llm_flag": 0}}


def _retrieved(pid: str, n: int, show: int) -> dict:
    return {"source": "retrieved", "chat_kind": "text", "show_vod_ms": show, "from_project_id": pid,
            "from_chat_id": "c%020x" % (900_000 + n), "from_t_ms": 3_901_000 + n, "from_scope": "same_broadcast",
            "retrieval_score": 0.41, "display_mask": "하**",
            "checks": {"nickname_hit": 0, "copy_max": 0.0, "blocked": 0, "llm_flag": 0}}


def make_project(parent, name: str, pid: str, *, n_gen: int = 20, n_ret: int = 2):
    """합성 프로젝트 + 시나리오 하나(생성 n_gen · 가져옴 n_ret). (폴더, 시나리오)."""
    root = parent / "projects" / name
    root.mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project_id": pid}), encoding="utf-8")
    chats = [_generated(i, RANGE[0] + 10_000 * i) for i in range(1, n_gen + 1)]
    chats += [_retrieved(pid, i, RANGE[0] + 10_000 * i + 5_000) for i in range(1, n_ret + 1)]
    recs = sc.assemble(_header(pid), chats, yyyymmdd="20261022")
    w = sst.write_scenario(str(root), recs)
    return str(root), sst.load_scenario(str(root), w.scenario_id)


def write_session(root: str, pid: str, scn, k: int, *, labeler="L01", rand="ab12", gen_fake=10, real_fake=5,
                  n_real=20, repeat=False, sha=None, unsure_all=False, ret_answer=None):
    """가려내기 세션 기록 하나(start · judge … · end). 생성 줄 중 gen_fake 를 가짜, 실제 n_real 줄 중 real_fake 를 가짜."""
    log = ds.DiscernLog.create(root, labeler, clock=Clock(), rand4=lambda: rand, app_version="0.9.0")
    gens = [c for c in scn.chats if c["source"] == "generated"]
    rets = [c for c in scn.chats if c["source"] == "retrieved"]
    log.start(project_id=pid, scenario_id=scn.scenario_id, scenario_sha256=sha or scn.sha256, range_ms=RANGE,
              shown={"replay": n_real, "retrieved": len(rets), "generated": len(gens)}, seen_before=False,
              repeat=repeat)
    t = RANGE[0]
    for j, c in enumerate(gens):
        a = dc.UNSURE if unsure_all else (dc.VIRTUAL if j < gen_fake else dc.REAL)
        log.judge(dc.target_key(scenario_id=scn.scenario_id, line_id=c["line_id"]), a, t + j)
    for i in range(n_real):
        a = dc.UNSURE if unsure_all else (dc.VIRTUAL if i < real_fake else dc.REAL)
        log.judge(_cid(k, i), a, t + 100 + i)
    if ret_answer is not None:
        for c in rets:
            log.judge(dc.target_key(scenario_id=scn.scenario_id, line_id=c["line_id"]), ret_answer, t + 500)
    log.end("finish", judged=0, unseen=0)
    return log


# ============================================================================ 1. 관문 대표값 (discern_report)
def _two_broadcasts(tmp_path):
    p1, s1 = make_project(tmp_path, "synthetic-p1", PID1, n_ret=0)
    p2, s2 = make_project(tmp_path, "synthetic-p2", PID2, n_ret=0)
    write_session(p1, PID1, s1, 1, rand="aa01")
    write_session(p2, PID2, s2, 2, rand="bb01")
    return p1, p2, s1, s2


def test_report_two_broadcasts_pass_matches_hand_calculation(tmp_path):
    """손 계산 2 와 같은 판(방송마다 생성 20 중 10 가짜 · 실제 20 중 5 가짜): κ 0.25, 탐지율 0.5 → 통과.
    세션 하나(화면)는 같은 판정이어도 방송 1편이라 늘 '표본 적음' 이다."""
    p1, p2, _s1, _s2 = _two_broadcasts(tmp_path)
    one = dr.report([p1])
    assert one["gate"] == dc.GATE_LOW_N and one["result"]["sample"]["broadcasts"] == 1
    out = dr.report([p1, p2])
    r = out["result"]
    assert r["confusion"] == [[30, 10, 0], [20, 20, 0], [0, 0, 0]]
    assert r["kappa"] == pytest.approx(0.25) and out["p_o"] == pytest.approx(0.625) and out["p_e"] == pytest.approx(0.5)
    assert r["detection"] == {"hit": 20, "n": 40, "rate": 0.5}
    assert r["false_alarm"] == {"hit": 10, "n": 40, "rate": 0.25}
    assert r["sample"] == {"generated": 40, "real": 40, "broadcasts": 2, "ok": True}
    assert out["gate"] == dc.GATE_PASS and out["pass"] is True
    assert out["sessions"]["found"] == 2 and out["sessions"]["used"] == 2 and out["unknown_keys"] == 0
    assert out["shown"] == 80 and out["notes"] == ["human_bucket_empty"]


def test_report_excludes_repeat_other_labeler_and_changed_scenario(tmp_path):
    p1, p2, s1, _s2 = _two_broadcasts(tmp_path)
    base = dr.report([p1, p2])
    write_session(p1, PID1, s1, 3, rand="aa02", gen_fake=20, repeat=True)        # 다시 봄 → 뺀다
    write_session(p1, PID1, s1, 4, labeler="L02", rand="aa03", gen_fake=20)       # 다른 라벨러
    write_session(p1, PID1, s1, 5, rand="aa04", gen_fake=20, sha="0" * 64)        # 시나리오 바이트가 다름
    out = dr.report([p1, p2], labeler_id="L01", with_sessions=True)
    assert out["result"] == base["result"] and out["gate"] == dc.GATE_PASS
    assert out["sessions"]["found"] == 5 and out["sessions"]["used"] == 2 and out["sessions"]["repeat_excluded"] == 1
    assert out["sessions"]["skipped"][dr.S_OTHER_LABELER] == 1
    assert out["sessions"]["skipped"][dr.S_SCENARIO_CHANGED] == 1
    assert {s["skip"] for s in out["by_session"]} == {None, dr.S_OTHER_LABELER, dr.S_SCENARIO_CHANGED}
    # 라벨러를 정하지 않으면 L02 세션(생성 20/20 가짜)도 들어가 탐지율이 오른다
    allr = dr.report([p1, p2])
    assert allr["result"]["detection"]["hit"] == 40 and allr["result"]["detection"]["n"] == 60


def test_report_retrieved_counts_as_real_from_scenario_file(tmp_path):
    p1, s1 = make_project(tmp_path, "synthetic-p1", PID1, n_ret=2)
    write_session(p1, PID1, s1, 1, rand="aa01", ret_answer=dc.VIRTUAL)
    out = dr.report([p1])
    assert out["result"]["retrieved"] == {"hit": 2, "n": 2, "rate": 1.0}
    assert out["result"]["sample"]["real"] == 22                      # replay 20 + retrieved 2


def test_report_skips_pilot_without_opening(tmp_path, monkeypatch):
    """projects 아래 pilot-* 는 project.json·discern\\·scenarios\\ 어느 것도 열지 않고 건너뛴다(D-23 2, D-18 7)."""
    p1, p2, _s1, _s2 = _two_broadcasts(tmp_path)
    pilot = tmp_path / "projects" / "pilot-합성"
    (pilot / "discern").mkdir(parents=True)
    (pilot / "project.json").write_text(json.dumps({"project_id": PID1}), encoding="utf-8")
    touched = []
    real_open, real_listdir, real_isfile = builtins.open, os.listdir, os.path.isfile

    def mark(p):
        if "pilot-" in str(p):
            touched.append(os.path.basename(str(p)))

    monkeypatch.setattr(builtins, "open", lambda p, *a, **k: mark(p) or real_open(p, *a, **k))
    monkeypatch.setattr(os, "listdir", lambda p=".": mark(p) or real_listdir(p))
    monkeypatch.setattr(os.path, "isfile", lambda p: mark(p) or real_isfile(p))
    roots = dr.project_dirs(str(tmp_path / "projects"))
    assert sorted(os.path.basename(r) for r in roots) == ["pilot-합성", "synthetic-p1", "synthetic-p2"]
    out = dr.report(roots)
    assert out["pilot_skipped"] == 1 and out["projects"] == 2 and out["gate"] == dc.GATE_PASS
    assert touched == []


def test_cli_prints_numbers_only(tmp_path, capsys):
    import tools.discern_report as tool
    p1, p2, s1, _s2 = _two_broadcasts(tmp_path)
    assert tool.main(["--projects-root", str(tmp_path / "projects"), "--labeler", "L01", "--sessions"]) == 0
    raw = capsys.readouterr().out
    out = json.loads(raw)
    assert out["gate"] == "pass" and out["result"]["sample"]["broadcasts"] == 2
    # 키(chat_id·line_id)·본문·가림 이름·경로가 없다 (세션·프로젝트·시나리오 ID 와 숫자만)
    for word in (_cid(1, 0), "s0001", "합성 생성", "두**", str(tmp_path)):
        assert word not in raw
    assert tool.main(["--labeler", "bad"]) == 1
    assert tool.main([]) == 1
    assert tool.main(["--projects-root", str(tmp_path / "없음")]) == 1


def test_view_end_screen_says_gate_comes_from_combined_records():
    """화면은 세션 하나 — 끝 숫자 줄에 '통과·실패는 방송 2편 이상을 합쳐 따로' 한 줄."""
    from app.ui import discern_view as dv
    t = [(f"SC-20261022-aaaa:s{i:04d}", "generated", i) for i in range(1, 41)]
    t += [("c%020x" % i, "replay", 100 + i) for i in range(1, 41)]
    ans = {k: (dc.VIRTUAL if i % 2 else dc.REAL) for i, (k, _s, _t) in enumerate(t)}
    r = dc.evaluate(t, ans, broadcasts=1)
    assert r.gate["status"] == dc.GATE_LOW_N
    assert any("방송 2편 이상" in x for x in dv.result_lines(r))
    r2 = dc.evaluate(t, ans, broadcasts=2)
    assert not any("방송 2편 이상의 가려내기 기록" in x for x in dv.result_lines(r2))


# ============================================================================ 3. 한쪽에 몰린 판정 (낮음)
def _targets(n=40):
    t = [(f"SC-20261022-aaaa:s{i:04d}", "generated", i) for i in range(1, n + 1)]
    t += [("c%020x" % i, "replay", 1000 + i) for i in range(1, n + 1)]
    return t


@pytest.mark.parametrize("answer", [dc.UNSURE, dc.REAL, dc.VIRTUAL])
def test_one_sided_answers_never_pass(answer):
    """전부 '모르겠음'(탐지율 0 · κ 0) · 전부 '진짜'(탐지율 0 · κ 0) 가 '통과' 로 나오던 판 → 판단 불가."""
    t = _targets()
    r = dc.evaluate(t, {k: answer for k, _s, _t in t}, broadcasts=2)
    assert r.sample["ok"] is True
    assert r.gate["status"] == dc.GATE_UNKNOWN and r.gate["pass"] is None
    assert dc.NOTE_ONE_SIDED.format(dc.GATE_MAX_UNSURE) in r.notes
    assert r.numbers()["gate"] == "unknown"


def test_unsure_over_half_is_unknown_but_half_is_judged():
    t = _targets()
    keys = [k for k, _s, _t in t]
    # 41/80 모르겠음(> 0.5) — 나머지는 진짜·가짜 섞음
    ans = {k: dc.UNSURE for k in keys[:41]}
    ans.update({k: (dc.VIRTUAL if i % 2 else dc.REAL) for i, k in enumerate(keys[41:])})
    assert dc.evaluate(t, ans, broadcasts=2).gate["status"] == dc.GATE_UNKNOWN
    # 정확히 절반(40/80)은 몰림이 아니다 — 판단한다
    ans = {k: dc.UNSURE for k in keys[:20]}
    ans.update({k: dc.UNSURE for k in keys[40:60]})
    ans.update({k: (dc.VIRTUAL if i % 2 else dc.REAL) for i, k in enumerate(keys[20:40] + keys[60:])})
    r = dc.evaluate(t, ans, broadcasts=2)
    assert r.unsure["rate"] == pytest.approx(0.5) and r.gate["status"] in (dc.GATE_PASS, dc.GATE_FAIL)


def test_one_sided_helper():
    assert dc.one_sided(0, 0, [0, 0, 0]) is False                  # 판정 없음은 몰림이 아니다(표본 적음이 먼저)
    assert dc.one_sided(10, 0, [10, 0, 0]) is True
    assert dc.one_sided(10, 0, [5, 5, 0]) is False
    assert dc.one_sided(10, 6, [2, 2, 6]) is True
    assert dc.one_sided(10, 5, [3, 2, 5]) is False


# ============================================================================ 2. 조정 세션은 scenarios\ 를 열지 않는다
AD = "L01-AD-20261031-bb22"


def test_adjudication_session_never_opens_scenarios(win, proj, monkeypatch):  # noqa: F811
    """조정 세션: '가상 채팅 섞기'·'만들기' 숨김, 만들기를 불러도 막힘, scenarios\\ 0번(20 1장 4번). 훈련 두 가지는
    '보기 전용으로 다시 열기' 로(가려내기 메뉴와 같은 세션 판단)."""
    from app import scenario_mix
    root = proj["root"]
    os.makedirs(os.path.join(root, "scenarios"), exist_ok=True)
    touched = []
    real_listdir = os.listdir
    monkeypatch.setattr(scenario_mix, "find_scenario", lambda *a, **k: touched.append("find") or (None, {}))
    monkeypatch.setattr(scenario_mix, "run_virtual_make", lambda *a, **k: touched.append("make") or {"rc": 1})
    monkeypatch.setattr(sst, "latest_scenario", lambda *a, **k: touched.append("latest") or (None, {}))
    monkeypatch.setattr(os, "listdir", lambda p=".": (touched.append("ls") if "scenarios" in str(p) else None)
                        or real_listdir(p))
    _open(win, root, ctx(AD, "adjudication", "L01"))
    assert win.workbench.session_kind == "adjudication"
    win.m_training.aboutToShow.emit()
    assert win.training_needs_reopen() is True
    assert not win.a_mix_virtual.isVisible() and not win.a_make_virtual.isVisible()
    assert not win.a_discern.isEnabled()
    assert win.make_virtual_chat() is None
    assert "보기 전용" in win.a_streamer_training.text()
    assert "보기 전용" in win.a_viewer_training.text()
    assert touched == []
    win.show_home()


def test_adjudication_session_reopens_view_only_for_training(win, proj, monkeypatch):  # noqa: F811
    """조정 세션에서 '스트리머 훈련' 한 번 = 세션을 닫고 보기 전용으로 다시 연 뒤 훈련(클릭 2, 묻는 창 0)."""
    from app import training_store as ts
    from app.ui import mainwindow as mw
    monkeypatch.setattr(mw, "TRAINING_REOPEN_POLL_MS", 600_000)
    root = proj["root"]
    wb = _open(win, root, ctx(AD, "adjudication", "L01"))
    assert win.a_streamer_training.isEnabled(), win.a_training_reason.text()
    monkeypatch.setattr(wb, "resolve_unsaved", lambda allow_cancel=True: True)
    win.a_streamer_training.trigger()
    assert win.training_view is None and ts.list_sessions(root) == []   # 조정 세션 위에서는 열지 않았다
    nwb = win.workbench
    assert nwb is not wb and nwb.session is None and win._pending_training is not None
    assert nwb.wait_jobs(15_000)
    nwb.media_path = os.path.join(root, "합성영상.mp4")
    view = win._try_pending_training()
    assert view is not None and win.training_view is view
    view.btn_close.click()
    win.show_home()

