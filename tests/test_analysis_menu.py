"""메인 창 분석 메뉴 (E 통합, 08 D-01·D-15 7·8, 06 6장·7장) — app/ui/mainwindow.py · app/analysis_flow.py.

합성 프로젝트(tests.test_final_set.proj: 1차·같은 사람 재라벨 잠김, 조정 3건)와 합성 엔진 결과만 쓴다.
흐름: 보기 전용에서 가져오기(봉인) → 해제 조건 미충족 거부 → 라벨링 세션은 분석 메뉴가 꺼지고 data\\ai 를 열지 않음 →
분석 세션은 해제 전 '봉인됨 n건' 만 → 앞당겨 해제(사유) → 분석 세션 나란히 보기 → AI 비교 보고서(프로젝트 밖).
"""
import builtins
import io
import json
import os

import pytest

from app import analysis_flow as af
from app import seal
from tests.test_ai_compare import _engine_bytes
from tests.test_final_set import _write_adjudications, proj  # noqa: F401
from tests.test_integration_c4 import PARTS, ctx, qapp  # noqa: F401

AN = "L01-AN-20261031-aa11"
P2 = "L01-P-20261031-bb22"


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    from app.ui import workbench as wbm
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))
    data_root = str(tmp_path / "appdata")
    os.makedirs(data_root, exist_ok=True)
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    w = mw.MainWindow(SPEC, inst, data_root, str(tmp_path / "log.txt"), None, [], set())
    w.check_updates = lambda force=False, on_start=False: None
    w.shown = []
    w.inform = lambda title, text: w.shown.append(("info", text))
    w.warn = lambda title, text: w.shown.append(("warn", text))
    yield w
    w.close()


def _open(win, root, session):
    win.session_chooser = lambda outcome: session
    out = win.project_view.open_path(root)
    assert out.ok and win.workbench.wait_jobs(15_000)
    win.update_session_actions()
    win.update_analysis_actions()
    return win.workbench


class _Spy:
    """builtins.open·io.open·os.open 에서 needle 이 든 경로를 센다."""

    def __init__(self, monkeypatch, needle):
        self.hits = []
        real_b, real_os = builtins.open, os.open
        nd = os.path.normcase(needle)

        def note(p):
            try:
                s = os.path.normcase(os.path.abspath(os.fspath(p)))
            except TypeError:
                return
            if nd in s:
                self.hits.append(s)

        def b(file, *a, **k):
            note(file)
            return real_b(file, *a, **k)

        def o(path, *a, **k):
            note(path)
            return real_os(path, *a, **k)
        monkeypatch.setattr(builtins, "open", b)
        monkeypatch.setattr(io, "open", b)
        monkeypatch.setattr(os, "open", o)


def test_menu_texts_and_mnemonics(win):
    titles = [a.text() for a in win.menuBar().actions()]
    assert titles.index("분석(&I)") == titles.index("세션(&N)") + 1 and titles.index("보고(&B)") > titles.index("분석(&I)")
    texts = [a.text() for a in win.m_analysis.actions() if a.text()]
    assert texts == ["AI 결과 가져오기(봉인)(&I)…", "봉인 해제(&U)…", "엔진과 나란히 보기(&S)"]
    assert "AI 비교 보고서(&I)…" in [a.text() for a in win.m_report.actions()]
    win.update_analysis_actions()                       # 프로젝트 없음: 모두 꺼짐
    assert not win.m_analysis.isEnabled() and not win.a_ai_compare.isEnabled()


def test_analysis_menu_flow_seal_refuse_unseal_side_by_side_report(win, proj, tmp_path, monkeypatch):  # noqa: F811
    _write_adjudications(proj)
    root = proj["root"]
    eng = tmp_path / "engine-out.json"                 # 프로젝트 밖의 합성 엔진 결과
    eng.write_bytes(_engine_bytes())

    # 1) 보기 전용: 가져오기(봉인)만 켜진다
    _open(win, root, None)
    assert win.a_seal_import.isEnabled()
    assert not win.a_unseal.isEnabled() and "없습니다" in win.a_unseal.toolTip()
    assert not win.a_side_by_side.isEnabled() and "분석 세션" in win.a_side_by_side.toolTip()
    e = win.seal_import_dialog(path=str(eng), by="L01")
    assert e is not None and e.new and e.check == "valid"
    assert e.sha256[:8] in win.last_seal_message and "run-synth" not in win.last_seal_message   # 내용(run_id)은 안 보임
    assert win.seal_import_dialog(path=str(eng), by="L01").new is False                       # 같은 바이트는 한 번만

    # 2) 해제 조건 미충족 → 거부(사유를 주지 않음)
    win.update_analysis_actions()
    assert win.a_unseal.isEnabled()
    assert win.unseal_dialog(by="L01", early_reason=lambda reasons: None) is None
    assert any("재라벨 계획이 없는 1차 세션" in r for r in win.last_unseal_reasons)
    assert seal.seal_status(root).sealed_count == 1

    # 3) 라벨링 세션(새 1차): 분석 메뉴가 꺼지고 data\ai 를 열지 않는다
    ai_dir = seal.default_ai_root(root)
    spy = _Spy(monkeypatch, ai_dir)
    _open(win, root, ctx(P2, "primary", "L01"))
    assert not win.m_analysis.isEnabled() and "라벨링" in win.m_analysis.menuAction().toolTip()
    assert win.seal_import_dialog(path=str(eng), by="L01") is None
    assert win.side_by_side_view(show=False) is None and win.unseal_dialog(by="L01") is None
    assert not win.a_ai_compare.isEnabled()
    assert spy.hits == []
    win.show_home()
    monkeypatch.undo()
    from app.ui import workbench as wbm
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))
    # 새 1차 세션 기록을 치운다(이 시험에서는 잠그지 않음 — 해제 조건에 들어가지 않게)
    for n in os.listdir(os.path.join(root, "labels")):
        if n.startswith(P2):
            os.remove(os.path.join(root, "labels", n))

    # 4) 분석 세션, 해제 전: 나란히 보기는 '봉인됨 1건' 만(열지 않음), 가져오기·해제는 꺼짐, 봉인 파일은 열지 않는다
    bins = _Spy(monkeypatch, ai_dir)
    _open(win, root, ctx(AN, "analysis", "L01"))
    assert win.m_analysis.isEnabled()
    assert not win.a_seal_import.isEnabled() and not win.a_unseal.isEnabled()
    assert not win.a_side_by_side.isEnabled() and "봉인됨 1건" in win.a_side_by_side.toolTip()
    assert win.side_by_side_view(show=False) is None
    assert not [h for h in bins.hits if os.sep + "sealed" + os.sep in h or os.sep + "runs" + os.sep in h]
    win.show_home()
    monkeypatch.undo()
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))

    # 5) 보기 전용: 사유를 적어 앞당겨 푼다(기록에 조건 전 해제)
    # 합성 잠금 시각(2026-10-24)이 실제 시계보다 뒤일 수 있다 — 해제 시각을 잠금 뒤로 고정해, 해제 때 이미 잠긴
    # 세션으로 본다(그러지 않으면 계산된 노출(app.core.exposure 규칙 2)로 사람 행이 모두 대표값에서 빠진다)
    monkeypatch.setattr(seal, "_now_iso", lambda now=None: "2026-10-30T09:00:00.000+09:00")
    _open(win, root, None)
    res = win.unseal_dialog(by="L01", early_reason=lambda reasons: "합성 시험: 계획 없이 연다")
    assert res is not None and res.conditions_met is False and res.presealed is True
    st = seal.seal_status(root)
    assert st.sealed_count == 0 and st.unsealed_count == 1

    # 6) 분석 세션, 해제 뒤: 나란히 보기(최종 세트 + 점수)
    _open(win, root, ctx(AN, "analysis", "L01"))
    assert win.a_side_by_side.isEnabled()
    dlg = win.side_by_side_view(show=False)
    assert dlg is not None
    v = dlg.view
    assert not v.is_sealed() and v.items() and v.score_table_rows()
    s = v.summary()
    assert s["engine_rows"] == 6 and s["human_rows"] >= 1
    win.show_home()
    assert win.side_view_dialog is None

    # 7) 보기 전용: AI 비교 보고서(프로젝트 밖)
    _open(win, root, None)
    assert win.a_ai_compare.isEnabled()
    out_root = str(tmp_path / "보고서")
    rep = win.ai_compare_dialog(out_root=out_root, gate=True)
    assert rep is not None and rep.out_dir.startswith(out_root)
    assert sorted(os.listdir(rep.out_dir)) == ["manifest.json", "report.json", "report.md", "tables"]
    doc = json.load(open(os.path.join(rep.out_dir, "report.json"), encoding="utf-8"))
    assert doc["header"]["conditions_met"] is False
    blob = json.dumps(doc, ensure_ascii=False)
    for secret in ("합성 본문", "시**"):
        assert secret not in blob


def test_analysis_flow_reasons_and_scores_adapter():
    assert af.menu_block_reason("primary") and af.menu_block_reason("relabel_second") and af.menu_block_reason("origin_review")
    assert af.menu_block_reason(None) is None and af.menu_block_reason("analysis") is None
    assert af.seal_block_reason("analysis") and af.seal_block_reason("adjudication") is None
    assert af.view_block_reason(None) and af.view_block_reason("analysis") is None
    with pytest.raises(PermissionError):
        af.side_by_side("C:\\없음", reader_kind="primary")
    body = {"e1": {"strict": {"status": "withheld", "n": 0},
                   "lenient": {"status": "ok", "n": 4, "grade": {"exact": 0.5, "one_step": 0.25, "kappa_w": 0.4},
                               "binary": {"precision": 1.0, "recall": 0.5, "f1": 2 / 3, "kappa": 0.2, "ac1": 0.3},
                               "reaction_time": {"within": {"2s": 0.5, "5s": 1.0}}}},
            "warnings": ["w"]}
    sc = af.scores_for_view(body)
    assert sc["strict"]["kappa_w"] == "held" and sc["lenient"]["within_one"] == 0.75
    assert sc["lenient"]["dt_within_5s"] == 1.0 and sc["warnings"] == ["w"]
    from app.ui import analysis_view as av
    rows = av.score_rows(sc)
    assert ("κ_w", "보류", "0.400") in rows


def test_timeline_fixed_tracks_can_be_hidden(qapp):  # noqa: F811
    """분석 화면이 쓰는 Timeline.set_fixed_tracks_visible: 기본은 모두 보이고(작업대 그대로), 숨기면 칸과 높이가 준다."""
    from app.ui.timeline import Timeline
    t = Timeline()
    t.set_markers("engine", [(1000, "x")])
    full = [n for n, _ in t.track_rects()]
    assert full[:4] == ["ruler", "segments", "density", "waveform"] and full[-1] == "marks:engine"
    h0 = t.height()
    t.set_fixed_tracks_visible(segments=False, density=False, waveform=False)
    assert [n for n, _ in t.track_rects()] == ["ruler", "marks:engine"] and t.height() < h0
    t.set_fixed_tracks_visible()
    assert [n for n, _ in t.track_rects()] == full and t.height() == h0
