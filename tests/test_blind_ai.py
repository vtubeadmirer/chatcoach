"""블라인드 시험: 라벨링·재라벨 세션은 data\\ai\\·data\\reports\\ 를 디스크에서 열지 않는다 (16 8-1 8번, 06 7-3, 05 S-11).

열기 감시: builtins.open·io.open·os.open 과 폴더 훑기(os.listdir·os.scandir)를 감싸 경로를 모은다. 라벨링 세션(1차)과
재라벨 세션(relabel_second)을 연 동안 — 세션 화면, 작업대 열기, 구간·반응·표본, 재라벨 계획 만들기, 잠그기, 닫기 —
`<data>\\ai\\` 와 `<data>\\reports\\` 아래 경로가 하나도 나오면 실패다.

AI 봉인 경로는 봉인 담당 모듈이 아직 없어 06 7-2 의 규칙을 **문자열로 가정**한다:
  <data>\\ai\\<project_id>\\sealed\\<sha256>.bin, <data>\\ai\\<project_id>\\seal.jsonl, <data>\\ai\\<project_id>\\runs\\<run_id>.json
보고서는 프로젝트가 '…\\projects\\<이름>' 에 있으면 그 옆 '…\\reports' (= <data>\\reports, agreement_report.default_out_dir).
봉인 모듈이 생기면 이 가정을 그 모듈의 경로 함수로 바꾼다.

합성 프로젝트(test_integration_c4 의 25분·합성 채팅)와 합성 봉인 파일만 쓴다. 실제 프로젝트·채팅을 열지 않는다(tmp_path 안).
"""
import builtins
import hashlib
import io
import json
import os
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from app import paths  # noqa: E402
from app import relabel  # noqa: E402
from app import session as sess  # noqa: E402
from app.core import phase as ph  # noqa: E402
from tests.test_integration_c4 import (MID, close_wb, ctx, judge, lock_via_dialog, make_long_project,  # noqa: E402
                                       open_wb, primary_session)
from tests.test_integration_c4 import qapp  # noqa: E402,F401
from tests.test_workbench_labeling import NOW  # noqa: E402

# 봉인 담당의 경로 규칙(가정, 06 7-2·03 2-2)
AI_DIR = "ai"
SEALED_DIR = "sealed"
SEAL_LOG = "seal.jsonl"
RUNS_DIR = "runs"
REPORTS_DIR = "reports"


def seed_sealed(data_root: str, project_id: str) -> dict:
    """합성 봉인 파일·봉인 기록·해제 결과·지난 보고서를 만든다. 내용은 지어낸 값."""
    body = json.dumps({"schema": "synthetic", "items": [{"chat_id": "c" + "0" * 20, "grade": "direct"}]}).encode()
    sha = hashlib.sha256(body).hexdigest()
    ai = os.path.join(data_root, AI_DIR, project_id)
    os.makedirs(os.path.join(ai, SEALED_DIR), exist_ok=True)
    os.makedirs(os.path.join(ai, RUNS_DIR), exist_ok=True)
    with open(os.path.join(ai, SEALED_DIR, sha + ".bin"), "wb") as f:
        f.write(body)
    with open(os.path.join(ai, SEAL_LOG), "w", encoding="utf-8") as f:
        f.write(json.dumps({"sha256": sha, "size": len(body), "sealed_at": NOW.isoformat(), "sealed_by": "L09",
                            "check": "valid"}) + "\n")
    with open(os.path.join(ai, RUNS_DIR, "run-synth.json"), "wb") as f:
        f.write(body)
    rep = os.path.join(data_root, REPORTS_DIR, "합성-지난보고서")
    os.makedirs(rep, exist_ok=True)
    with open(os.path.join(rep, "report.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    return {"ai": ai, "reports": os.path.join(data_root, REPORTS_DIR), "sealed_sha": sha}


class OpenWatch:
    """연 경로·훑은 폴더를 모은다. on 동안만 센다(감시를 켠 채 준비·확인 코드를 돌리지 않게)."""

    def __init__(self, monkeypatch, forbidden):
        self.forbidden = tuple(os.path.normcase(os.path.abspath(p)) for p in forbidden)
        self.on = False
        self.seen = 0
        self.bad: list[tuple[str, str]] = []
        r_open, r_os_open, r_listdir, r_scandir = builtins.open, os.open, os.listdir, os.scandir

        def note(how, p):
            if not self.on:
                return
            try:
                s = os.path.normcase(os.path.abspath(os.fspath(p)))
            except TypeError:            # 파일 기술자(int) 등
                return
            self.seen += 1
            if any(s == x or s.startswith(x + os.sep) for x in self.forbidden):
                self.bad.append((how, s))

        def w_open(file, *a, **k):
            note("open", file)
            return r_open(file, *a, **k)

        def w_os_open(path, *a, **k):
            note("os.open", path)
            return r_os_open(path, *a, **k)

        def w_listdir(path="."):
            note("listdir", path)
            return r_listdir(path)

        def w_scandir(path="."):
            note("scandir", path)
            return r_scandir(path)
        monkeypatch.setattr(builtins, "open", w_open)
        monkeypatch.setattr(io, "open", w_open)
        monkeypatch.setattr(os, "open", w_os_open)
        monkeypatch.setattr(os, "listdir", w_listdir)
        monkeypatch.setattr(os, "scandir", w_scandir)


def _project_under_data(tmp_path):
    """프로젝트를 <data>\\projects\\ 아래에 둔다 — 보고서 기본 위치가 <data>\\reports 가 되게(실제 배치와 같음)."""
    base = tmp_path / "data" / "projects"
    base.mkdir(parents=True, exist_ok=True)
    return make_long_project(base)


def test_labeling_and_relabel_sessions_never_open_ai_or_reports(qapp, tmp_path, monkeypatch):
    data_root = paths.data_root()
    assert os.path.normcase(data_root) == os.path.normcase(str(tmp_path / "data"))       # conftest 가 임시로 지정
    proj = _project_under_data(tmp_path)
    for lid in ("L01", "L02"):
        sess.add_labeler(data_root, lid)
    seeded = seed_sealed(data_root, proj.data["project_id"])
    watch = OpenWatch(monkeypatch, [seeded["ai"], os.path.join(data_root, AI_DIR), seeded["reports"]])

    # ---- 감시가 실제로 잡는지 먼저 본다(감시 자체의 시험)
    watch.on = True
    with open(os.path.join(seeded["ai"], SEALED_DIR, seeded["sealed_sha"] + ".bin"), "rb"):
        pass
    os.listdir(seeded["reports"])
    watch.on = False
    assert [h for h, _ in watch.bad] == ["open", "listdir"]
    watch.bad.clear()

    # ---- 1차 라벨링 세션: 열기 → 구간 → 반응 → 표본 판정 → 잠그기 → 재라벨 계획 → 닫기
    watch.on = True
    wb, doc, _items, _judged = primary_session(qapp, proj, n_judge=1)
    plan = wb.create_relabel_plan("relabel_second", "L02", now=NOW + timedelta(hours=1), rand4="bl01")
    close_wb(wb)
    watch.on = False
    assert doc["sample_verify"] and all(v["ok"] for v in doc["sample_verify"])
    assert watch.seen > 0 and watch.bad == [], watch.bad
    primary_seen = watch.seen

    # ---- 세션 화면(라벨러 고르기·재라벨 선택지) → 재라벨 세션: 열기 → 구간 → 반응 → 표본 → 잠그기 → 닫기
    from app.ui.session_dialog import SessionDialog
    watch.on = True
    dlg = SessionDialog(data_root=data_root, machine_id_value=MID, now=lambda: NOW + timedelta(hours=2),
                        rand4=lambda: "bl02")
    dlg.load_context(proj.root, proj.data)
    assert dlg.select_labeler("L02")
    assert dlg.choose("new:relabel_second")
    dlg._choose_open()
    ctx_b = dlg.context()
    dlg.deleteLater()
    assert ctx_b is not None and ctx_b.relabel_plan_id == plan["plan_id"]
    wb = open_wb(qapp, proj, ctx_b)
    lo, hi = wb.relabel_scope()[0]
    assert wb.set_mode(ph.SEGMENTS)
    f = wb.segment_form
    for b in (lo, hi):
        if 0 < b < f.model.duration_ms:
            assert f.add_boundary(b)
    assert f.select_at((lo + hi) // 2)
    assert f.set_category("just_chatting") and f.set_confidence("certain") and f.set_rationale("00:10:05 합성 잡담")
    assert f.save() is not None
    assert wb.mark_phase_done() is not None and wb.set_mode(ph.REACTIONS) and wb.mark_phase_done() is not None
    assert wb.set_mode(ph.SAMPLES) and wb.queue.manifest is not None
    judge(wb.queue, "certain", "합성 답 없음")
    judge(wb.queue, "undeterminable", "합성 소리 불명")
    doc_b = lock_via_dialog(wb)
    close_wb(wb)
    watch.on = False
    assert doc_b["relabel_plan_id"] == plan["plan_id"]
    assert watch.seen > primary_seen and watch.bad == [], watch.bad

    # ---- 대조: 검토자 쪽 일치도 보고서는 <data>\\reports 에 쓴다 → 감시가 그 경로를 잡는다(감시가 앱 경로도 본다)
    from app.ui.agreement_dialog import AgreementDialog
    watch.on = True
    ad = AgreementDialog(project_root=proj.root, created_by="L01")
    res = ad.run(doc["session_id"], ctx_b.session_id, os.path.join(seeded["reports"], "합성-이번"))
    ad.deleteLater()
    watch.on = False
    assert res is not None, ad.msg.text()
    assert any(p.startswith(os.path.normcase(seeded["reports"])) for _h, p in watch.bad)
    # 보고서 만들기도 봉인 폴더(data\\ai)는 열지 않는다 — 봉인 해제 전이므로
    assert not [p for _h, p in watch.bad if p.startswith(os.path.normcase(os.path.join(data_root, AI_DIR)))]
    # 봉인 파일은 그대로 (지우거나 고치지 않음)
    with open(os.path.join(seeded["ai"], SEALED_DIR, seeded["sealed_sha"] + ".bin"), "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == seeded["sealed_sha"]


def test_analysis_session_does_not_open_sealed_ai(qapp, tmp_path, monkeypatch):
    """분석 세션은 엔진 결과는 읽어도 봉인된 평가용 AI 결과(data\\ai)는 열지 않는다(visibility 머리말, 06 7-1)."""
    data_root = paths.data_root()
    proj = _project_under_data(tmp_path)
    seeded = seed_sealed(data_root, proj.data["project_id"])
    wb, _doc, _items, _judged = primary_session(qapp, proj, n_judge=1)
    close_wb(wb)
    watch = OpenWatch(monkeypatch, [os.path.join(data_root, AI_DIR), seeded["reports"]])
    watch.on = True
    wb = open_wb(qapp, proj, ctx("L01-AN-20261023-bl03", "analysis", "L01"))
    assert wb.primary_labels()["sessions"]
    # 주제 트랙(D-17 5)은 '봉인됨' — 해제 기록이 없어 topics\ 를 열지 않는다
    assert not wb.topic_track.isHidden() and wb.topic_track.is_sealed()
    close_wb(wb)
    watch.on = False
    # 분석 세션은 seal.jsonl 의 건수만 읽을 수 있다(visibility UNSEALED_ONLY — 주제 트랙의 '봉인됨'). 봉인 파일·runs·
    # topics·보고서는 열지 않는다
    bad = [(h, p) for h, p in watch.bad if not p.endswith(os.sep + SEAL_LOG)]
    assert watch.seen > 0 and bad == [], bad


def test_assumed_paths_match_seal_module_when_present(tmp_path):
    """가정한 경로 문자열이 봉인 모듈(app/seal.py, 동시 작업)의 규칙과 같은지. 모듈이 없거나 이름이 다르면 건너뛴다."""
    seal = pytest.importorskip("app.seal")
    if not hasattr(seal, "default_ai_root"):
        pytest.skip("app.seal.default_ai_root 없음")
    root = str(tmp_path / "data" / "projects" / "합성")
    assert os.path.normcase(seal.default_ai_root(root)) == os.path.normcase(str(tmp_path / "data" / AI_DIR))
    for name, mine in (("SEALED_DIR", SEALED_DIR), ("SEAL_FILE", SEAL_LOG), ("RUNS_DIR", RUNS_DIR)):
        if hasattr(seal, name):
            assert getattr(seal, name) == mine


def test_relabel_plan_files_do_not_live_under_ai_or_reports(tmp_path):
    """재라벨 계획·취소 기록(세션이 여는 파일)은 프로젝트 relabel\\ 아래다 — 봉인·보고서 폴더와 겹치지 않는다."""
    root = str(tmp_path / "p")
    for p in (relabel.cancel_path(root, "RP-20261022-ab12"), relabel.index_path(root)):
        parts = os.path.normcase(p).split(os.sep)
        assert AI_DIR not in parts and REPORTS_DIR not in parts
