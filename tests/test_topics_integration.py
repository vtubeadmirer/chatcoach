"""주제 흐름·방송 요약 연결 (08 D-01 · D-17 5·8, 17 6-1 1·2번) — app/seal.py · app/analysis_flow.py · 메인 창 · 작업대.

1. 봉인: 주제 흐름은 ai-result 봉인의 짝으로만 봉인된다(topics.sealed). '봉인됨 n건' 에 세지 않는다. 짝 판정을 풀 때
   같은 unseal 안에서 같이 풀린다(topics\\<run_id>.json, ai.unsealed 의 topics 칸). 해제 전에는 read_topics 가 열지 않는다.
2. analysis_flow.topic_flow · broadcast_summary: 분석 세션만, 덩어리별 채팅·반응·놓친 질문(숫자·주제 이름만).
3. 메인 창: '분석 → AI 결과 가져오기' 가 같은 폴더의 '<이름>.topics.json' 을 같이 봉인 → 해제 → 분석 세션 작업대 주제 트랙과
   '분석 → 방송 요약'. 라벨링 세션은 트랙이 숨고 data\\ai 를 열지 않는다.
4. tools/engine_to_ai: --topics 인자 규칙과 파일 이름.

합성 프로젝트(tests.test_final_set.proj)·합성 엔진 결과·합성 주제 흐름만 쓴다. 실제 채팅·닉네임·전사문은 쓰지 않는다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import analysis_flow as af
from app import seal
from app.core import topics as T
from tests.test_ai_compare import _engine_bytes
from tests.test_analysis_menu import AN, P2, _open, _Spy, win  # noqa: F401
from tests.test_final_set import _write_adjudications, proj  # noqa: F401
from tests.test_integration_c4 import PARTS, ctx, qapp  # noqa: F401

KST = timezone(timedelta(hours=9))
SEALED_AT = datetime(2026, 10, 23, 9, 0, tzinfo=KST)
UNSEAL_AT = "2026-10-30T09:00:00.000+09:00"          # 합성 잠금(10-24)보다 뒤


def topic_doc(run_id="topics-synth-1", video_no="777"):
    """합성 주제 흐름: 100~140초 '게임', 140~200초 '요리'. 채팅 1~3 은 앞 덩어리, 4~6 은 뒤 덩어리(tests.test_final_set.proj)."""
    return {
        "schema": T.SCHEMA, "schema_version": T.SCHEMA_VERSION, "run_id": run_id,
        "created_at": "2026-10-23T08:30:00+09:00",
        "engine": {"name": "synthetic-topics", "version": "0.0.1", "config_sha256": None, "embedding": None},
        "target": {"video_no": video_no, "messages_sha256": None, "asr_sha256": None},
        "time_axis": T.TIME_AXIS, "window_ms": 20_000, "range": {"start_ms": 100_000, "end_ms": 200_000},
        "stats": {"windows": 5, "blocks": 2},
        "blocks": [
            {"index": 0, "start_ms": 100_000, "end_ms": 140_000, "keywords": ["게임", "보스"],
             "name_keywords": ["게임"], "top_keyword": "게임", "change_ms": None, "boundary_score": None,
             "utterances": 4, "chats": 3},
            {"index": 1, "start_ms": 140_000, "end_ms": 200_000, "keywords": ["요리", "라면"],
             "name_keywords": ["요리", "라면"], "top_keyword": "요리", "change_ms": 140_000, "boundary_score": 0.4,
             "utterances": 5, "chats": 3},
        ],
        "gaps": [{"at_ms": 140_000, "similarity": 0.1, "depth": 0.4}],
    }


def tbytes(d=None):
    return json.dumps(d or topic_doc(), ensure_ascii=False).encode("utf-8")


def _seal_pair(root):
    e = seal.seal_bytes(root, _engine_bytes(), source="file", by="L01", now=SEALED_AT)
    te = seal.seal_topics_bytes(root, tbytes(), pair_sha256=e.sha256, by="L01", now=SEALED_AT)
    return e, te


def _unseal(root, sha, monkeypatch):
    monkeypatch.setattr(seal, "_now_iso", lambda now=None: UNSEAL_AT)
    return seal.unseal(root, sha, by="L01", early_reason="합성 시험")


# ---------------------------------------------------------------------------------------------- 1. 봉인·해제
def test_topics_seal_pairs_and_unseals_together(proj, monkeypatch):  # noqa: F811
    root = proj["root"]
    with pytest.raises(seal.SealError):                            # 짝 판정 봉인이 없으면 받지 않는다
        seal.seal_topics_bytes(root, tbytes(), pair_sha256="0" * 64, by="L01")
    e, te = _seal_pair(root)
    assert te.new and te.check == "valid" and te.reject_reasons == []
    assert seal.seal_topics_bytes(root, tbytes(), pair_sha256=e.sha256, by="L01").new is False   # 같은 바이트는 한 번
    st = seal.seal_status(root)
    assert st.sealed_count == 1 and len(st.entries) == 1           # 주제 흐름은 '봉인됨 n건' 에 세지 않는다
    assert st.entries[0].topics_sha256 == te.sha256
    # 해제 전: 어느 자리에서도 주제 흐름을 열지 않는다
    with pytest.raises(seal.SealError):
        seal.read_topics(root, "run-synth-a", reader_kind="analysis")
    with pytest.raises(seal.SealBlindError):
        seal.read_topics(root, "run-synth-a", reader_kind="primary")
    with pytest.raises(seal.SealBlindError):
        seal.seal_topics_bytes(root, tbytes(), pair_sha256=e.sha256, by="L01", reader_kind="relabel_second")
    res = _unseal(root, e.sha256, monkeypatch)
    assert res.record["topics"]["sha256"] == te.sha256 and res.record["topics_error"] is None
    doc = seal.read_topics(root, res.run_id, reader_kind="analysis")
    assert doc == topic_doc()
    # 풀린 판정에는 나중에 주제 흐름을 붙이지 않는다(봉인 없이 보이게 되므로)
    with pytest.raises(seal.SealError):
        seal.seal_topics_bytes(root, tbytes(topic_doc("topics-synth-2")), pair_sha256=e.sha256, by="L01")
    # 파일이 바뀌면 읽지 않는다
    d = seal.project_ai_dir(root, st.project_id)
    with open(os.path.join(d, seal.TOPICS_DIR, f"{res.run_id}.json"), "ab") as f:
        f.write(b" ")
    with pytest.raises(seal.SealError):
        seal.read_topics(root, res.run_id)


def test_topics_rejected_or_absent_does_not_block_unseal(proj, monkeypatch):  # noqa: F811
    root = proj["root"]
    e = seal.seal_bytes(root, _engine_bytes(), source="file", by="L01", now=SEALED_AT)
    bad = topic_doc()
    bad["blocks"][1]["start_ms"] = 150_000                         # 앞 덩어리와 이어지지 않음
    te = seal.seal_topics_bytes(root, tbytes(bad), pair_sha256=e.sha256, by="L01")
    assert te.check == "rejected" and te.reject_reasons == ["topics_format"]
    other = seal.seal_topics_bytes(root, tbytes(topic_doc(video_no="999")), pair_sha256=e.sha256, by="L01")
    assert other.check == "rejected" and "topics_video_mismatch" in other.reject_reasons
    assert seal.seal_status(root).entries[0].topics_sha256 is None
    res = _unseal(root, e.sha256, monkeypatch)
    assert "topics" not in res.record                              # 짝이 없으면 기록 칸도 없다(지난 판 기록과 같은 모양)
    assert seal.read_topics(root, res.run_id, reader_kind="analysis") is None


# ---------------------------------------------------------------------------------------------- 2. 분석 흐름
def test_topic_flow_and_broadcast_summary(proj, monkeypatch):  # noqa: F811
    _write_adjudications(proj)
    root = proj["root"]
    with pytest.raises(PermissionError):
        af.topic_flow(root, reader_kind="primary")
    e, _te = _seal_pair(root)
    tf = af.topic_flow(root, reader_kind="analysis")
    assert tf.sealed and tf.sealed_count == 1 and tf.doc is None and tf.blocks == []
    s0 = af.broadcast_summary(root, reader_kind="analysis")
    assert s0.sealed and s0.rows == [] and s0.reaction_source is None
    _unseal(root, e.sha256, monkeypatch)
    tf = af.topic_flow(root, reader_kind="analysis")
    assert not tf.sealed and tf.run_id == "run-synth-a" and [b.name for b in tf.blocks] == ["게임", "요리 · 라면"]
    s = af.broadcast_summary(root, reader_kind="analysis", flow=tf)
    assert s.reaction_source == "final_set" and not s.sealed and s.topic_run_id == "run-synth-a"
    assert [r["chats"] for r in s.rows] == [3, 3] and s.summary.total.chats == 6
    assert all(0 <= r["reacted"] <= r["chats"] for r in s.rows)
    assert [r["keywords"] for r in s.rows] == [["게임", "보스"], ["요리", "라면"]]
    blob = json.dumps(s.rows, ensure_ascii=False)
    assert "합성 본문" not in blob and "시**" not in blob           # 숫자·주제 이름만 (D-17 8)
    # 최종 세트를 만들 수 없으면 해제된 엔진 판정으로 센다(직접·높음 5건)
    from app import export

    def boom(*a, **k):
        raise export.ExportError("합성 실패")
    monkeypatch.setattr(export, "build_final_set", boom)
    s2 = af.broadcast_summary(root, reader_kind="analysis")
    assert s2.reaction_source == "engine" and s2.summary.total.reacted == 5 and s2.warnings


# ---------------------------------------------------------------------------------------------- 3. 메인 창·작업대
def test_menu_seals_sibling_topics_then_track_and_summary(win, proj, tmp_path, monkeypatch):  # noqa: F811
    from app.ui import mainwindow as mw
    from app.ui import workbench as wbm
    _write_adjudications(proj)
    root = proj["root"]
    eng = tmp_path / "engine-out.json"
    eng.write_bytes(_engine_bytes())
    (tmp_path / "engine-out.topics.json").write_bytes(tbytes())
    assert mw.topics_sibling(str(eng)) == str(tmp_path / "engine-out.topics.json")
    assert mw.topics_sibling(str(tmp_path / "engine-out.topics.json")) is None
    assert mw.topics_sibling(str(tmp_path / "없음.json")) is None

    # 1) 보기 전용: 가져오기 한 번으로 판정과 주제 흐름을 같이 봉인. 트랙은 숨어 있다
    wb = _open(win, root, None)
    assert wb.topic_track.isHidden()
    e = win.seal_import_dialog(path=str(eng), by="L01")
    assert e is not None and e.check == "valid"
    te = win.last_topics_seal
    assert te is not None and te.check == "valid" and te.sha256[:8] in win.last_seal_message
    assert seal.seal_status(root).entries[0].topics_sha256 == te.sha256
    assert "방송 요약(&B)" in [a.text() for a in win.m_analysis.actions()]

    # 2) 라벨링 세션: 트랙이 숨고 data\ai 를 열지 않는다
    spy = _Spy(monkeypatch, seal.default_ai_root(root))
    wb = _open(win, root, ctx(P2, "primary", "L01"))
    assert wb.topic_track.isHidden() and wb.topic_flow is None
    assert win.broadcast_summary_view(show=False) is None
    assert spy.hits == []
    win.show_home()
    monkeypatch.undo()
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))
    for n in os.listdir(os.path.join(root, "labels")):
        if n.startswith(P2):
            os.remove(os.path.join(root, "labels", n))

    # 3) 분석 세션, 해제 전: 트랙은 '봉인됨', 요약 메뉴는 꺼짐(봉인됨 1건), topics·sealed 파일은 열지 않는다
    bins = _Spy(monkeypatch, seal.default_ai_root(root))
    wb = _open(win, root, ctx(AN, "analysis", "L01"))
    assert not wb.topic_track.isHidden() and wb.topic_track.is_sealed() and wb.topic_track.bands() == []
    assert not win.a_summary.isEnabled() and "봉인됨 1건" in win.a_summary.toolTip()
    assert win.broadcast_summary_view(show=False) is None
    assert not [h for h in bins.hits if os.sep + "sealed" + os.sep in h or os.sep + "topics" + os.sep in h]
    win.show_home()
    monkeypatch.undo()
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))

    # 4) 보기 전용: 해제(사유) — 주제 흐름도 같이 풀린다
    monkeypatch.setattr(seal, "_now_iso", lambda now=None: UNSEAL_AT)
    _open(win, root, None)
    res = win.unseal_dialog(by="L01", early_reason=lambda reasons: "합성 시험: 계획 없이 연다")
    assert res is not None and res.record.get("topics", {}).get("sha256") == te.sha256

    # 5) 분석 세션, 해제 뒤: 작업대 주제 트랙(띠 2개, 누르면 그 주제 시작으로) · 방송 요약 창
    wb = _open(win, root, ctx(AN, "analysis", "L01"))
    tt = wb.topic_track
    assert not tt.isHidden() and not tt.is_sealed()
    assert [(b.start_ms, b.end_ms, b.name) for b in tt.bands()] == [(100_000, 140_000, "게임"),
                                                                    (140_000, 200_000, "요리 · 라면")]
    seen = []
    tt.seekRequested.connect(seen.append)
    tt.resize(400, 22)
    tt.set_view(0, 400_000)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(tt, Qt.LeftButton, Qt.NoModifier, QPoint(int(tt.t_to_x(150_000)), 10))
    assert seen == [140_000]
    assert win.a_summary.isEnabled()
    dlg = win.broadcast_summary_view(show=False)
    assert dlg is not None and dlg.view.row_count() == 2 and not dlg.view.is_sealed()
    assert dlg.data.reaction_source == "final_set" and "run-synth-a" in dlg.windowTitle()
    # 창 제목의 run 옆에 해제 기록의 엔진 판(23 5-2 의 10) — '판 X' 또는 '판 모름'
    assert f"run run-synth-a · {af.version_text(dlg.data.topic_engine_version)}" in dlg.windowTitle()
    assert [r["chat_count"] for r in dlg.view.export_rows()] == [3, 3]
    jumps = []
    dlg.view.seekRequested.connect(jumps.append)
    dlg.view._activate_row(1)                                      # 행 한 번 누름과 같은 길
    assert jumps == [140_000]
    win.show_home()


# ---------------------------------------------------------------------------------------------- 4. engine_to_ai
def test_engine_to_ai_topics_args_and_name(tmp_path, capsys):
    from tools import engine_to_ai as e2a
    assert e2a.topics_out_path(os.path.join("x", "ai.json")) == os.path.join("x", "ai.topics.json")
    assert e2a.topics_out_path("ai") == "ai.topics.json"
    src = tmp_path / "link.json"
    src.write_text("{}", encoding="utf-8")
    rc = e2a.main(["--in", str(src), "--out", str(tmp_path / "o.json"), "--topics"])
    assert rc == 2 and "--from-project" in capsys.readouterr().out
