"""C단계 둘째 묶음 검토 수정의 재현 시험 — 작업대(②·③ Ctrl+Z 장부, 재현 검증, 매니페스트 충돌, 열람 기록).

test_workbench_labeling 의 합성 프로젝트·가짜 부품을 그대로 쓴다(합성 채팅 100건, 영상 없음, offscreen).
"""
import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from app import export  # noqa: E402
from app.core import phase as ph  # noqa: E402
from app.core import sampling as sp  # noqa: E402
from app.ui import workbench as wbm  # noqa: E402
from tests.test_workbench_labeling import (STEP, cid, fill_direct, make_project, make_two_segments,  # noqa: E402,F401
                                           open_wb, qapp)


def _fill_continuation(ed, chat_i):
    t = chat_i * STEP
    c = ed.continuation
    c.set_type("short")
    c.start_edit.setText(f"{(t + 1_000) / 1000:.3f}")
    c.end_edit.setText(f"{(t + 20_000) / 1000:.3f}")
    c._on_span_edited()
    c.conf_box.setCurrentIndex(c.conf_box.findData("certain"))
    c.rationale.setText("00:00:16 합성 이어짐")
    c._on_change()


def _save_direct_with_continuation(wb, chat_i=10):
    wb.chat.select_chat(cid(chat_i))
    wb.seek_vod(chat_i * STEP + 1_000)
    wb.mark_reaction()
    ed = wb.editor
    fill_direct(ed, chat_i)
    _fill_continuation(ed, chat_i)
    res = ed.save()
    assert res is not None and res["continuation"] is not None, ed.msg.text()
    return res


def _live(wb, kind):
    return list(wb._label_heads[kind].values())


@pytest.fixture
def wb(qapp, tmp_path):
    proj = make_project(tmp_path)
    w = open_wb(qapp, proj)
    make_two_segments(w)
    w.mark_phase_done()
    assert w.set_mode(ph.REACTIONS)
    w._proj = proj
    yield w
    w.close_workbench()


def test_1_undo_new_reaction_also_retracts_its_continuation(wb):
    res = _save_direct_with_continuation(wb)
    assert len(_live(wb, "continuation")) == 1
    out = wb.undo_reaction()
    assert out is not None and out["kind"] == "retract" and out["entity_id"] == res["reaction"]["entity_id"]
    assert _live(wb, "reaction") == [] and _live(wb, "continuation") == []
    rows, _ = export.build_rows(wb._proj.root)
    assert not [r for r in rows if r["label_kind"] in ("reaction", "continuation")]


def test_3_undo_edit_of_earlier_label_restores_previous_version(wb):
    res = _save_direct_with_continuation(wb)
    eid = res["reaction"]["entity_id"]
    original = copy.deepcopy(res["reaction"]["body"])
    wb._reaction_undo.clear()                                     # 지난 실행에 만든 라벨로 친다
    assert wb.open_reaction_editor(None, None, existing=wb._label_heads["reaction"][eid])
    wb.editor.set_confidence("probably")
    res2 = wb.editor.save()
    assert res2 is not None and res2["reaction"]["supersedes"] == res["reaction"]["id"], wb.editor.msg.text()
    out = wb.undo_reaction()
    assert out is not None and out["kind"] == "reaction"          # 철회가 아니라 앞 판 내용의 새 판
    head = wb.store.head(eid)
    assert head["kind"] == "reaction" and head["body"] == original
    assert len(_live(wb, "continuation")) == 1                    # 이어짐도 그대로
    assert wb.undo_reaction() is None                             # 이번 실행의 다른 저장은 없다


def test_3b_queue_rejudge_downgrade_retracts_continuation_and_undo_restores(wb):
    res = _save_direct_with_continuation(wb)
    rx = res["reaction"]
    wb._reaction_undo.clear()
    body = {"chat_id": rx["body"]["chat_id"], "segment_id": rx["body"]["segment_id"], "entry": "from_utterance",
            "result": "unconfirmed", "grade": None, "eligible_grades": [], "grade_vs_conditions": "not_applicable",
            "candidates": [], "linked_utterance_id": None, "first_visible_ms": rx["body"]["first_visible_ms"],
            "first_visible_source": "chat_clock", "reaction_time_ms": None, "confidence": "certain",
            "rationale": "00:00:15 합성 답 없음"}
    rec = wb._queue_save({"kind": "reaction", "entity_id": rx["entity_id"], "supersedes": rx["id"], "body": body})
    assert rec["body"]["result"] == "unconfirmed"
    assert _live(wb, "continuation") == []                        # 낮춘 판정에 남은 이어짐 철회(03 12-3)
    out = wb.undo_reaction()
    assert out["kind"] == "reaction" and wb.store.head(rx["entity_id"])["body"] == rx["body"]
    conts = _live(wb, "continuation")
    assert len(conts) == 1 and conts[0]["body"] == res["continuation"]["body"]   # 되살림


def _to_samples(wb):
    wb.mark_phase_done()
    assert wb.set_mode(ph.SAMPLES)
    wb.draw_sample(sync=True)
    man = wb.queue.manifest
    assert man is not None and man["excluded"]["buffer"] > 0
    return man


def test_4_verify_stale_manifest_uses_draw_time_buffers(wb):
    man = _to_samples(wb)
    assert wb._verify_sample(man).ok
    a = wb.segment_heads()[0]
    body = dict(a["body"], end_ms=50_000)                         # 표본을 뽑은 뒤 구간 끝을 옮김 → stale 판
    wb.save_label({"kind": "segment", "entity_id": a["entity_id"], "supersedes": a["id"], "body": body})
    # 지금 구간표로 완충을 만들면 실패한다(고치기 전 동작) — 시험이 뜻있는지 확인
    msgs, sha = wbm.read_messages(wb.project.paths.messages_jsonl)
    now_buf = wbm.segment_buffers(wb.segment_heads(), wb._vod_duration, 10_000)
    assert not sp.verify_manifest(man, msgs, now_buf, messages_sha256=sha).ok
    v = wb._verify_sample(man)
    assert v.ok, v.problems


def test_low_manifest_exists_loads_disk_copy(wb):
    man = _to_samples(wb)
    other = copy.deepcopy(man)
    other["drawn_at"] = "2026-10-21T10:00:00+09:00"               # 같은 id, 다른 내용(다른 PC 가 먼저 씀)
    other["items"] = list(reversed(other["items"]))
    wb._on_sample_drawn((other, {}), "")
    assert wb.queue.manifest == man                               # 계산한 것이 아니라 디스크의 판
    assert len(os.listdir(wb._proj.paths.samples_dir)) == 1


def test_low_exposed_failure_reaches_editor(wb, monkeypatch):
    def boom(kind, body):
        raise OSError("합성 실패")
    monkeypatch.setattr(wb, "write_ops", boom)
    with pytest.raises(OSError):
        wb._exposed("asr_text", cid(1), None)
    wb.chat.select_chat(cid(10))
    wb.mark_reaction()
    ed = wb.editor
    ed.load_context(asr_lookup=lambda a, b: "합성 자동 인식")
    assert ed.reveal_asr() is False and not ed.asr_visible()
    assert "열람 기록" in ed.msg.text()
    ed.cancel()


def test_low_partial_save_cancel_feeds_undo(wb):
    """반응 줄을 쓴 뒤 이어짐에서 실패 → Esc: 이미 쓴 반응이 Ctrl+Z 장부에 들어가 되돌릴 수 있다(큐는 mark_saved)."""
    wb.chat.select_chat(cid(10))
    wb.mark_reaction()
    ed = wb.editor
    fill_direct(ed, 10)
    _fill_continuation(ed, 10)
    orig = ed._save_cb

    def flaky(draft):
        if draft["kind"] == "continuation":
            raise OSError("합성 실패")
        return orig(draft)
    ed._save_cb = flaky
    assert ed.save() is None and ed.is_open()
    rx = [r for r in _live(wb, "reaction")]
    assert len(rx) == 1                                           # 반응 줄은 이미 써졌다
    ed.cancel()
    ed._save_cb = orig
    assert not wb.is_editing()
    assert wb._reaction_undo and wb._reaction_undo[-1]["eid"] == rx[0]["entity_id"]
    out = wb.undo_reaction()
    assert out["kind"] == "retract" and _live(wb, "reaction") == []
