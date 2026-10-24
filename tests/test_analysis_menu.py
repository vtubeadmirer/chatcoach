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
    assert texts == ["AI 결과 가져오기(봉인)(&I)…", "봉인 해제(&U)…", "엔진과 나란히 보기(&S)", "방송 요약(&B)"]
    assert "AI 비교 보고서(&I)…" in [a.text() for a in win.m_report.actions()]
    win.update_analysis_actions()                       # 프로젝트 없음: 모두 꺼짐
    assert not win.m_analysis.isEnabled() and not win.a_ai_compare.isEnabled()


def test_analysis_menu_flow_seal_refuse_unseal_side_by_side_report(win, proj, tmp_path, monkeypatch):  # noqa: F811
    _write_adjudications(proj)
    root = proj["root"]
    eng = tmp_path / "engine-out.json"                 # 프로젝트 밖의 합성 엔진 결과
    eng.write_bytes(_engine_bytes())
    # 봉인 시각을 합성 첫 1차 잠금(2026-10-24 12:00) 앞으로 고정한다 — 실제 시계를 쓰면 그 시각이 지난 날부터
    # 5) 의 '사전 봉인' 이 거짓이 된다(3) 의 monkeypatch.undo 가 풀지만 봉인은 1) 에서 끝난다)
    monkeypatch.setattr(seal, "_now_iso", lambda now=None: "2026-10-24T10:00:00.000+09:00")

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


# ----------------------------------------------------------------------------- 23 5-2 의 12: 라벨링 세션 4종 열기 감시
SESSIONS_4 = {
    "primary": ("L01-P-20261031-bb22", "L01", None),
    "relabel_same": ("L01-RS-20261031-cc33", "L01", "rs01"),
    "relabel_second": ("L02-R2-20261031-dd44", "L02", "r201"),
    "origin_review": ("L01-OR-20261031-ee55", "L01", None),
}


class _OpenWatch:
    """builtins.open·io.open·os.open·os.listdir·os.scandir·os.stat·os.lstat 와 os.path.isfile·exists·lexists·isdir·
    getsize 가운데 roots 아래(또는 접두 prefixes)를 건드린 경로를 센다('있는지 보기' 도 센다).
    Windows 의 Python 3.12 에서 os.path.isfile·exists·isdir 는 nt._path_isfile 같은 C 함수라 os.stat 을 거치지 않는다 —
    그래서 os.path 의 것들을 따로 감싼다(pathlib 의 exists·is_file 은 os.stat 을 거친다)."""

    PATH_FNS = ("isfile", "exists", "lexists", "isdir", "getsize")

    def __init__(self, monkeypatch, roots, prefixes=()):
        self.hits = []
        self.roots = [os.path.normcase(os.path.abspath(r)) for r in roots]
        self.prefixes = [os.path.normcase(os.path.abspath(p)) for p in prefixes]
        real = {"open": builtins.open, "os_open": os.open, "listdir": os.listdir, "scandir": os.scandir,
                "stat": os.stat, "lstat": os.lstat}
        real.update({f"path.{n}": getattr(os.path, n) for n in self.PATH_FNS})

        def note(p, name):
            try:
                s = os.path.normcase(os.path.abspath(os.fspath(p)))
            except TypeError:
                return
            if any(s == r or s.startswith(r + os.sep) for r in self.roots) or \
                    any(s.startswith(p_) for p_ in self.prefixes):
                import traceback
                where = [f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in traceback.extract_stack()
                         if os.sep + "app" + os.sep in f.filename]
                self.hits.append((name, os.path.basename(s), where[-12:]))

        def wrap(name):
            fn = real[name]

            def inner(p=".", *a, **k):
                note(p, name)
                return fn(p, *a, **k)
            return inner
        monkeypatch.setattr(builtins, "open", wrap("open"))
        monkeypatch.setattr(io, "open", wrap("open"))
        monkeypatch.setattr(os, "open", wrap("os_open"))
        monkeypatch.setattr(os, "listdir", wrap("listdir"))
        monkeypatch.setattr(os, "scandir", wrap("scandir"))
        monkeypatch.setattr(os, "stat", wrap("stat"))
        monkeypatch.setattr(os, "lstat", wrap("lstat"))
        for n in self.PATH_FNS:
            monkeypatch.setattr(os.path, n, wrap(f"path.{n}"))


def _prepare_sealed_and_reports(root, tmp_path, monkeypatch):
    """ai\\(봉인 + 해제된 run)·reports\\(보고서 하나)·derived\\asr*(합성 파일 4개)를 만들어 둔다. 모두 합성."""
    from app import agreement_report as ar
    from app.project import open_project
    monkeypatch.setattr(seal, "_now_iso", lambda now=None: "2026-10-30T09:00:00.000+09:00")
    e = seal.seal_bytes(root, _engine_bytes(), source="file", by="L01")
    seal.unseal(root, e.sha256, by="L01", early_reason="합성 감시 시험")
    pid = open_project(root).project_id
    rep = os.path.join(ar.default_out_root(root), pid, "R-synth-0001")
    os.makedirs(rep, exist_ok=True)
    with open(os.path.join(rep, "report.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    der = os.path.join(root, "derived")
    os.makedirs(der, exist_ok=True)
    for n in ("asr.json", "asr-words.json", "asr-topic-flow.json", "asr-topic-vectors.json"):
        with open(os.path.join(der, n), "w", encoding="utf-8") as f:
            f.write("{}")
    eng = tmp_path / "engine-out.json"
    eng.write_bytes(_engine_bytes())
    return str(eng), seal.default_ai_root(root), ar.default_out_root(root), os.path.join(der, "asr")


@pytest.mark.parametrize("kind", list(SESSIONS_4))
def test_labeling_session_opens_no_ai_reports_asr(win, proj, tmp_path, monkeypatch, kind):  # noqa: F811
    """라벨링 세션 4종(1차·같은 사람 재라벨·다른 사람 재라벨·출처 판정)은 세션을 열 때부터 분석·보고·훈련 메뉴를 모두 눌러
    봐도 ai\\·reports\\·derived\\asr* 를 열지도, 목록을 읽지도, 있는지 보지도 않는다(06 7-3, 23 5-2 의 12).
    합성 프로젝트에 봉인·해제된 run·보고서·전사 파일을 미리 두고 본다."""
    from app import relabel
    from app.project import open_project
    root = proj["root"]
    eng, ai_root, rep_root, asr_prefix = _prepare_sealed_and_reports(root, tmp_path, monkeypatch)
    sid, labeler, rand4 = SESSIONS_4[kind]
    extra = {}
    if rand4 is not None:
        plan = relabel.create_plan(root, open_project(root).data, session_kind=kind, labeler_id=labeler,
                                   source_session_id="L01-P-20261024-ab12", done_ranges=[(0, 600_000)],
                                   created_by="L01", created_machine4="m3fa", app_version="0.0.1", creator_kind=None,
                                   rand4=rand4)
        extra = relabel.session_args(plan)
    watch = _OpenWatch(monkeypatch, [ai_root, rep_root], prefixes=[asr_prefix])
    wb = _open(win, root, ctx(sid, kind, labeler, **extra))
    assert wb.session is not None and wb.session_kind == kind, wb.notes if hasattr(wb, "notes") else None
    assert not win.m_analysis.isEnabled()
    assert win.seal_import_dialog(path=eng, by=labeler) is None
    assert win.unseal_dialog(by=labeler) is None
    assert win.side_by_side_view(show=False) is None
    assert win.broadcast_summary_view(show=False) is None
    assert win.ai_compare_dialog(out_root=str(tmp_path / "보고서-감시")) is None
    assert win.agreement_block_reason()
    assert win.agreement_dialog(run=lambda dlg: None) is None
    win.update_training_actions()                       # 메뉴를 펼 때의 길(훈련 기준은 이름만 본다, D-19 3)
    win.training_block_reason(compute=True)
    win.viewer_block_reason()
    win.update_session_actions()
    win.update_analysis_actions()
    assert watch.hits == [], watch.hits[:5]
    win.show_home()
    monkeypatch.undo()


def test_open_watch_sees_view_only_reads(win, proj, tmp_path, monkeypatch):  # noqa: F811
    """감시가 제대로 보는지(대조): 보기 전용에서 분석 메뉴를 펴면 seal.jsonl 을 읽는다 — 감시에 걸려야 한다."""
    root = proj["root"]
    _eng, ai_root, rep_root, asr_prefix = _prepare_sealed_and_reports(root, tmp_path, monkeypatch)
    watch = _OpenWatch(monkeypatch, [ai_root, rep_root], prefixes=[asr_prefix])
    _open(win, root, None)
    win.update_analysis_actions()
    assert any(h[1] == "seal.jsonl" for h in watch.hits)
    # '있는지 보기' 도 감시에 걸린다(os.path.* 는 이 Python 에서 os.stat 을 거치지 않으므로 따로 감쌌다)
    from app.project import open_project
    seal_file = os.path.join(seal.project_ai_dir(root, open_project(root).project_id), "seal.jsonl")
    for fn in (os.path.exists, os.path.isfile, os.path.isdir, os.path.lexists, os.path.getsize, os.lstat):
        n0 = len(watch.hits)
        fn(seal_file)
        assert len(watch.hits) >= n0 + 1, fn             # getsize 는 os.stat 도 거쳐 2번 잡힌다
    n0 = len(watch.hits)
    os.path.exists(asr_prefix + ".json")
    assert len(watch.hits) == n0 + 1                     # derived\asr* 접두도
    assert any(h[0] == "path.exists" and h[1] == "asr.json" for h in watch.hits)
    win.show_home()
    monkeypatch.undo()


# ----------------------------------------------------------------------------- 23 5-2 의 10·13: 판 기록과 기본 run
def _st(*rows, sealed=0):
    from types import SimpleNamespace
    return SimpleNamespace(entries=[SimpleNamespace(run_id=r, unsealed_at=at, presealed=True, engine_version=v)
                                    for r, at, v in rows], sealed_count=sealed)


def test_run_order_and_choices_carry_engine_version():
    st = _st(("run-a", "2026-10-30T09:00", "link-v0.3"), ("run-b", "2026-10-30T10:00", "0.1.0"),
             ("run-c", "2026-10-30T11:00", None))
    assert af.unsealed_run_ids(st) == ["run-a", "run-b", "run-c"]
    assert af.run_order(st) == ["run-a", "run-b", "run-c"] and af.default_run_id(st) == "run-a"
    ch = af.run_choices(st)
    assert [c.run_id for c in ch] == ["run-c", "run-b", "run-a"] and ch[0].latest
    assert [c.engine_version for c in ch] == [None, "0.1.0", "link-v0.3"]
    assert [c.version_text for c in ch] == ["판 모름", "판 0.1.0", "판 link-v0.3"]
    # 판을 모르면 늦게 푼 것, 같은 판이면 늦게 푼 것, 옛 SealEntry(칸 없음)는 판 모름
    assert af.run_order(_st(("a", "1", None), ("b", "2", None))) == ["b", "a"]
    assert af.run_order(_st(("a", "1", "0.2"), ("b", "2", "0.2.0"))) == ["b", "a"]
    from types import SimpleNamespace
    old = SimpleNamespace(entries=[SimpleNamespace(run_id="x", unsealed_at="1", presealed=None)], sealed_count=0)
    assert af.run_choices(old)[0].engine_version is None and af.default_run_id(old) == "x"
    assert af.default_run_id(_st()) is None
    # 이상한 판 글은 판 모름, 화면과 같은 규칙
    assert af.clean_version(" 0.3") is None and af.clean_version("x" * 41) is None and af.clean_version(3) is None
    from app.ui import analysis_view as av
    for v in ("link-v0.3", "0.3.0", "0.1.0", "v1.10", None, "판모름"):
        assert av.version_key(v) == af.version_key(v)
    assert af.sealed_text(2) == "봉인됨 2건" and af.sealed_text(None) == "봉인됨 0건"


def test_side_by_side_defaults_to_highest_engine_version(proj, monkeypatch):  # noqa: F811
    """나란히 보기 자료의 기본 run = 해제 기록의 엔진 판이 가장 높은 run(화면의 판 A 기본과 같아 창 제목이 맞는다).
    판 칸이 없는 옛 기록은 '판 모름'. 시범 해제처럼 run 둘을 같이 푼다(합성)."""
    _write_adjudications(proj)
    root = proj["root"]
    monkeypatch.setattr(seal, "_now_iso", lambda now=None: "2026-10-30T09:00:00.000+09:00")
    e = seal.seal_bytes(root, _engine_bytes(), source="file", by="L01")
    res = seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험")
    data = af.side_by_side(root, reader_kind="analysis")
    st = seal.seal_status(root, reader_kind="analysis")
    want = getattr(st.entries[0], "engine_version", None)
    assert data.run_id == res.run_id and data.engine_version == want
    assert data.version_text == (f"판 {want}" if want else "판 모름")
    assert data.choices[0].engine_version == want
    tf = af.topic_flow(root, reader_kind="analysis")
    assert tf.engine_version is None and tf.run_id is None and tf.sealed_text == "봉인됨 0건"   # 짝 주제 흐름 없음


def test_side_by_side_window_title_shows_engine_version(win, proj, monkeypatch):  # noqa: F811
    """메인 창 '엔진과 나란히 보기' 창 제목 = '… — run <판 A> · 판 …'(해제 기록의 판, 없으면 '판 모름')."""
    _write_adjudications(proj)
    root = proj["root"]
    monkeypatch.setattr(seal, "_now_iso", lambda now=None: "2026-10-30T09:00:00.000+09:00")
    e = seal.seal_bytes(root, _engine_bytes(), source="file", by="L01")
    res = seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험")
    _open(win, root, ctx(AN, "analysis", "L01"))
    dlg = win.side_by_side_view(show=False)
    assert dlg is not None
    st = seal.seal_status(root, reader_kind="analysis")
    ver = getattr(st.entries[0], "engine_version", None)
    want = f"엔진과 나란히 보기 — run {res.run_id} · " + (f"판 {ver}" if ver else "판 모름")
    assert dlg.windowTitle() == want and dlg.view.window_title() == want
    assert dlg.view.current_run_id() == res.run_id
    win.show_home()


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
