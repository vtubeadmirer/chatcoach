"""08 D-10 작업대 연결 시험 — 영상 밖(3), ③ 에서 ② 라벨 고치기(4), 저장 중 표시(2), 상태 줄 하나·분류만 고른 구간(11 4-2).

test_workbench_labeling 의 합성 프로젝트·가짜 부품을 그대로 쓴다(합성 채팅 100건: i번 채팅은 VOD i×1.5초, offscreen).
영상 파일은 없다. 영상이 덮는 범위는 project.json 의 영상 길이·보정 오프셋만으로 정해진다(timecode.media_coverage).
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QStatusBar  # noqa: E402

from app.core import phase as ph  # noqa: E402
from app.core import sampling as sp  # noqa: E402
from app.core import timecode as tc  # noqa: E402
from app.ui import workbench as wbm  # noqa: E402
from tests.test_workbench_labeling import (DURATION, NOW, STEP, cid, fill_direct, make_project,  # noqa: E402,F401
                                           make_two_segments, open_wb, qapp)


def _shift_media(proj, file0_vod_ms):
    """보정 새 판: 영상 파일 0초 = VOD file0_vod_ms (파일 = VOD + offset → offset = −file0_vod_ms)."""
    cal = tc.Calibration(offset_ms=-int(file0_vod_ms), rev=2, status="verified", created_at=NOW.isoformat(),
                         created_by="L01")
    proj.update(lambda d: d.__setitem__("calibration", tc.append_rev(d["calibration"], tc.calibration_to_json(cal))),
                now=NOW, by="L01")


def _project_30_120(tmp_path):
    """영상이 VOD [30초, 120초) 만 덮는 합성 프로젝트."""
    proj = make_project(tmp_path, media_ms=90_000)
    _shift_media(proj, 30_000)
    return proj


def _to_samples(wb, vod_ms=45_000):
    wb.mark_phase_done()                     # ① 완료
    assert wb.set_mode(ph.REACTIONS)
    wb.mark_phase_done()                     # ② 완료
    wb.seek_vod(vod_ms)
    assert wb.set_mode(ph.SAMPLES)


# --------------------------------------------------------------------------- D-10 3: 영상이 덮는 범위
def test_coverage_from_project_reaches_forms(qapp, tmp_path):
    wb = open_wb(qapp, _project_30_120(tmp_path))
    try:
        assert wb.media_coverage() == [(30_000, 120_000)]
        assert "0:00:30" in wb.btn_media.toolTip()
        make_two_segments(wb)                                            # 두 구간 다 영상에 걸쳐 저장된다
        assert wb.segment_form.media_coverage == [(30_000, 120_000)]
        wb.mark_phase_done()
        assert wb.set_mode(ph.REACTIONS)
        ed = wb._ensure_editor()
        wb._editor_context()
        assert ed.media_coverage() == [(30_000, 120_000)]
        # 확인 범위(채팅 −10초 ~ +30초)가 영상 밖인 채팅(3초)은 저장을 막고 이유를 보인다
        wb.chat.chatActivated.emit(cid(2), 2 * STEP)
        fill_direct(ed, 2)
        assert ed.save() is None
        assert any("영상 밖" in e for e in ed.validate().errors)
        ed.cancel()
        # 영상 안(45초)은 된다
        wb.chat.chatActivated.emit(cid(30), 30 * STEP)
        fill_direct(ed, 30)
        assert not [e for e in ed.validate().errors if "영상" in e]
        assert ed.save() is not None
    finally:
        wb.close_workbench()


def test_segment_form_blocks_segment_outside_media(qapp, tmp_path):
    wb = open_wb(qapp, _project_30_120(tmp_path))
    try:
        assert wb.set_mode(ph.SEGMENTS)
        f = wb.segment_form
        wb.seek_vod(20_000)
        assert wb.add_boundary_here()
        a, b = f.model.segments                                          # [0, 20초) 는 영상 밖, [20초, 끝) 은 걸침
        f.select_segment(b.id)
        assert f.set_category("game") and f.set_confidence("certain") and f.set_rationale("00:00:40 합성")
        assert not f.outside_media(b) and "일부만 영상 안" in f.lbl_head.text()
        f.select_segment(a.id)
        assert f.set_category("just_chatting") and f.set_confidence("certain") and f.set_rationale("00:00:05 합성")
        assert f.outside_media() and "저장 불가" in f.lbl_head.text()
        assert f.save() is None and "영상이 덮지 않는" in f.lbl_msg.text()
        assert f.plan().errors and wb.store.records()[-1]["kind"] == "session.open"     # 아무것도 쓰지 않았다
        assert f.undo() and f.undo() and f.undo()                         # 앞 구간 분류·확신도·근거를 되돌린다 → 저장됨
        f.select_segment(b.id)
        recs = f.save()
        assert recs is not None and len(recs) == 1, f.lbl_msg.text()
    finally:
        wb.close_workbench()


def test_no_media_blocks_labels_and_sampling(qapp, tmp_path):
    proj = make_project(tmp_path, media_ms=0)
    wb = open_wb(qapp, proj)
    try:
        assert wb.media_coverage() == []
        assert wb.set_mode(ph.SEGMENTS)
        f = wb.segment_form
        f.select_segment(f.model.segments[0].id)
        f.set_category("just_chatting"), f.set_confidence("certain"), f.set_rationale("00:00:00 합성")
        assert f.save() is None and "영상이 연결되지 않아" in f.lbl_msg.text()
        # 표본: 영상이 없으면 뽑지 않는다(빈 매니페스트로 계열을 막지 않게)
        with pytest.raises(ValueError, match="영상이 연결되지"):
            wbm.prepare_sample(proj.paths.messages_jsonl, {"entity_id": "x", "id": "y", "session_id": "s",
                                                          "body": {"start_ms": 0, "end_ms": 60_000}}, [],
                               duration_ms=DURATION, values={}, project_data=proj.data, meta={}, drawn_by="L01",
                               app_version="0.0.0", coverage=[])
        with pytest.raises(ValueError, match="영상 길이를 몰라"):
            wbm.prepare_sample(proj.paths.messages_jsonl, {"body": {"start_ms": 0, "end_ms": 60_000}}, [],
                               duration_ms=DURATION, values={}, project_data=proj.data, meta={}, drawn_by="L01",
                               app_version="0.0.0", coverage=None)
    finally:
        wb.close_workbench()


def test_sample_out_of_media_and_stale_buffers(qapp, tmp_path):
    proj = _project_30_120(tmp_path)
    wb = open_wb(qapp, proj)
    try:
        make_two_segments(wb)                                            # 저스트채팅 [0, 60초) · 게임 [60초, 끝)
        _to_samples(wb)
        wb.draw_sample(sync=True)
        q = wb.queue
        m = q.manifest
        assert m is not None and m["algorithm"] == sp.ALGORITHM_V2
        assert m["media"] == {"coverage": [[30_000, 120_000]], "pre_roll_ms": 10_000, "post_roll_ms": 30_000}
        # 구간 안 40건(0~58.5초) 중 확인 범위가 영상 안인 것은 40초 이상(13건), 그중 완충 [50, 60초) 6건을 빼면 7건
        assert m["excluded"]["out_of_media"] == 27 and m["excluded"]["buffer"] == 6
        assert m["eligible"]["size"] == 7 and m["boundary_buffers"] == [[50_000, 60_000]]
        assert all(40_000 <= it["t_ms"] < 50_000 for it in m["items"])
        assert "영상 밖 27" in q.meta.text() and "sha256-tau-v2" in q.meta.text()
        assert q.run_verify().ok
        assert wb.sample_stale(wb.sample_target(), m) is None and not wb.btn_draw.isEnabled()
        # 뒤 이웃을 저스트채팅으로 바꾸면 60초 경계는 전환이 아니다 → 완충이 사라져 표본 갱신(같은 원 구간 판)
        assert wb.set_mode(ph.SEGMENTS)
        f = wb.segment_form
        f.select_segment(wb.segment_heads()[1]["entity_id"])
        assert f.set_category("just_chatting") and f.save()
        wb.seek_vod(45_000)
        assert wb.set_mode(ph.SAMPLES)
        assert wb.sample_target()["entity_id"] == m["source_segment"]["entity_id"]
        assert wb.btn_draw.isEnabled() and "경계 완충" in wb.btn_draw.text()
        assert q.run_verify().ok                                         # 옛 판도 매니페스트 값으로 재현된다
        wb.draw_sample(sync=True)
        m2 = q.manifest
        assert m2["sample_rev"] == 2 and m2["reason"] == "buffers_changed" and m2["boundary_buffers"] == []
        assert m2["eligible"]["size"] == 13 and m2["excluded"]["buffer"] == 0
        assert q.run_verify().ok and not wb.btn_draw.isEnabled()
    finally:
        wb.close_workbench()


def test_stale_when_media_range_changes(qapp, tmp_path):
    proj = _project_30_120(tmp_path)
    wb = open_wb(qapp, proj)
    try:
        make_two_segments(wb)
        _to_samples(wb)
        wb.draw_sample(sync=True)
        m = wb.queue.manifest
        seg = wb.sample_target()
        assert wb.sample_stale(seg, m) is None
        wb._media_cov = [(20_000, 120_000)]                              # 보정이 바뀐 것처럼
        assert wb.sample_stale(seg, m) == "media_changed"
    finally:
        wb.close_workbench()


# --------------------------------------------------------------------------- D-10 4: 채팅 1건 = 현재 유효 라벨 1건
def test_stage2_reopens_existing_label_for_same_chat(qapp, tmp_path):
    wb = open_wb(qapp, make_project(tmp_path))
    try:
        make_two_segments(wb)
        wb.mark_phase_done()
        assert wb.set_mode(ph.REACTIONS)
        wb.chat.chatActivated.emit(cid(10), 10 * STEP)
        ed = wb.editor
        fill_direct(ed, 10)
        res = ed.save()
        assert res is not None
        eid = res["reaction"]["entity_id"]
        # 같은 채팅을 다시 열면 새 라벨이 아니라 그 라벨을 고치는 편집기
        wb.chat.chatActivated.emit(cid(10), 10 * STEP)
        assert wb.is_editing() and ed._existing["entity_id"] == eid and "고치기" in ed.title.text()
        ed.cancel()
        # 다른 채팅으로 열고 대상을 라벨이 있는 채팅으로 바꾸면 저장을 막는다
        wb.chat.chatActivated.emit(cid(12), 12 * STEP)
        assert ed._existing is None
        fill_direct(ed, 10)
        assert any("이미 반응 라벨" in e for e in ed.validate().errors) and ed.save() is None
        ed.cancel()
    finally:
        wb.close_workbench()


# --------------------------------------------------------------------------- D-10 2: 저장 중 표시
def test_busy_cursor_and_saving_text_while_writing(qapp, tmp_path):
    wb = open_wb(qapp, make_project(tmp_path))
    try:
        seen = []
        orig = wb.store.append_label

        def spy(draft):
            cur = QApplication.overrideCursor()
            seen.append((cur is not None and cur.shape() == Qt.WaitCursor, wb.last_message(), wb.is_busy()))
            return orig(draft)
        wb.store.append_label = spy
        states = []
        wb.busyChanged.connect(states.append)
        make_two_segments(wb)
        assert len(seen) == 2 and all(s[0] and s[2] for s in seen)
        assert all(s[1] == wbm.SAVING_TEXT for s in seen)
        assert states == [True]                                          # 한 번의 저장(2줄) = 바쁨 한 번
        qapp.processEvents()                                             # 이벤트 루프로 돌아오면 푼다
        assert QApplication.overrideCursor() is None and not wb.is_busy() and states == [True, False]
        assert wb.last_message() != wbm.SAVING_TEXT
        # 실패해도 커서가 남지 않는다
        wb.store.append_label = lambda draft: (_ for _ in ()).throw(OSError("합성 실패"))
        f = wb.segment_form
        f.select_segment(f.model.segments[0].id)
        assert f.set_rationale("00:00:01 합성 고침") and f.save() is None
        qapp.processEvents()
        # 폼이 보인 실패 이유가 남는다(_end_busy 가 일반 문구로 덮지 않음)
        assert QApplication.overrideCursor() is None and "합성 실패" in wb.last_message()
        # 부른 쪽이 아무것도 보이지 않은 실패면 일반 문구
        wb._begin_busy()
        wb._busy_failed = True
        qapp.processEvents()
        assert "저장하지 못했습니다" in wb.last_message()
    finally:
        wb.close_workbench()
    assert QApplication.overrideCursor() is None


def test_close_while_busy_restores_cursor(qapp, tmp_path):
    wb = open_wb(qapp, make_project(tmp_path))
    wb._begin_busy()
    assert QApplication.overrideCursor() is not None
    wb.close_workbench()
    assert QApplication.overrideCursor() is None
    qapp.processEvents()
    assert QApplication.overrideCursor() is None


# --------------------------------------------------------------------------- 11 4-2 의 5·9
def test_category_only_segment_counts_as_unsaved(qapp, tmp_path):
    wb = open_wb(qapp, make_project(tmp_path))
    try:
        assert wb.set_mode(ph.SEGMENTS)
        f = wb.segment_form
        f.select_segment(f.model.segments[0].id)
        assert f.set_category("just_chatting")                         # 확신도·근거는 비움
        assert f.plan().empty and f.partial_count() == 1
        assert f.has_unsaved and wb.has_unsaved()
        asked = []
        wb.ask_unsaved = lambda: asked.append(1) or "save"
        assert not wb.set_mode(ph.BROWSE) and asked == [1]              # 저장할 것이 없어도 남은 구간 때문에 머문다
        assert "확신도" in wb.last_message()                              # 폼이 빠진 것을 알린다
        wb.ask_unsaved = lambda: "discard"
        assert wb.set_mode(ph.BROWSE)
        assert wb.store.records()[-1]["kind"] == "session.open"
    finally:
        wb.close_workbench()


def test_status_chips_move_to_one_status_bar(qapp, tmp_path):
    wb = open_wb(qapp, make_project(tmp_path))
    bar = QStatusBar()
    try:
        msgs = []
        wb.messageShown.connect(msgs.append)
        before = wb.mode_reason.text()
        wb.attach_status_bar(bar)
        assert all(c.parent() is bar for c in wb.status_chips) and not wb.status_row.isVisible()
        wb._say("합성 문장")
        assert msgs[-1] == "합성 문장" and "합성 문장" not in wb.mode_reason.text()
        assert wb.mode_reason.text() in ("", before)
    finally:
        wb.close_workbench()
    assert all(c.parent() is wb.status_row for c in wb.status_chips)


# --------------------------------------------------------------------------- 검토 수정 (C단계 둘째 묶음 뒤)
def test_save_failure_keeps_callers_error_message(qapp, tmp_path):
    """저장 실패 뒤 _end_busy 가 부른 쪽이 보인 이유('되돌리지 못했습니다: …')를 일반 문구로 덮지 않는다."""
    wb = open_wb(qapp, make_project(tmp_path))
    try:
        make_two_segments(wb)
        wb.mark_phase_done()
        assert wb.set_mode(ph.REACTIONS)
        wb.chat.chatActivated.emit(cid(10), 10 * STEP)
        fill_direct(wb.editor, 10)
        assert wb.editor.save() is not None
        qapp.processEvents()
        wb.store.retract = lambda *a, **k: (_ for _ in ()).throw(OSError("합성 실패"))
        assert wb.undo_reaction() is None
        assert "되돌리지 못했습니다" in wb.last_message()
        qapp.processEvents()                                             # _end_busy
        assert QApplication.overrideCursor() is None and not wb.is_busy()
        assert "되돌리지 못했습니다" in wb.last_message() and "합성 실패" in wb.last_message()
    finally:
        wb.close_workbench()


def test_queue_counts_label_from_other_series(qapp, tmp_path):
    """06 2-6: 분할·병합으로 새 계열이 시작돼도, 원 구간 계열에서 판정한 채팅은 판정함이고 빠른 저장이 두 번째 라벨을
    만들지 않으며, M 은 그 라벨을 연다(D-10 4). 작업대 reaction_for_chat 과 큐가 같은 규칙을 쓴다."""
    proj = _project_30_120(tmp_path)
    wb = open_wb(qapp, proj)
    try:
        make_two_segments(wb)
        _to_samples(wb)
        wb.draw_sample(sync=True)
        q = wb.queue
        m = q.manifest
        it = q.current_item()
        q.set_confidence("certain")
        q.rationale.setText("00:00:45 합성 근거")
        rec = q.judge_unconfirmed()
        assert rec is not None
        qapp.processEvents()
        n_reactions = len(wb._label_heads["reaction"])
        # 같은 채팅을 담은 다른 계열(새 entity_id 의 구간)의 매니페스트를 싣는다
        m2 = dict(m, sample_set_id="S-bbbbbbbb-d1-r1")
        wb._load_queue(m2, wb._sample_msgs)
        assert q.state_of(it["chat_id"]) == sp.JUDGED and q.has_existing_label(it["chat_id"])
        assert q.progress().done == 1
        q.go(0)
        assert not q.unconf_btn.isEnabled() and not q.undet_btn.isEnabled()
        q.rationale.setText("x")
        assert q.judge_undeterminable() is None
        assert len(wb._label_heads["reaction"]) == n_reactions           # 두 번째 현재 유효 라벨을 만들지 않았다
        # M → 원 계열 라벨을 고치는 편집기
        q.request_reaction_editor()
        assert wb.is_editing() and wb.editor._existing["entity_id"] == rec["entity_id"]
        wb.editor.cancel()
        # 다시 읽어도 진행이 내려가지 않는다
        q.set_reactions(list(wb._label_heads["reaction"].values()))
        assert q.state_of(it["chat_id"]) == sp.JUDGED
    finally:
        wb.close_workbench()


def test_queue_gets_media_coverage(qapp, tmp_path):
    """③ 큐도 작업대의 영상 범위를 받아, 범위가 바뀌어 영상 밖이 된 표본은 빠른 저장을 막는다(D-10 3)."""
    proj = _project_30_120(tmp_path)
    wb = open_wb(qapp, proj)
    try:
        make_two_segments(wb)
        _to_samples(wb)
        wb.draw_sample(sync=True)
        q = wb.queue
        assert q.item_in_media() and q.unconf_btn.isEnabled()
        q.set_media_coverage([(100_000, 120_000)])                      # 영상 교체로 범위가 바뀐 것처럼
        assert not q.item_in_media() and not q.unconf_btn.isEnabled()
        q.rationale.setText("x")
        assert q.judge_undeterminable() is None and "영상" in q.msg.text()
        wb._refresh_coverage()                                           # project.json 범위로 다시
        assert q.item_in_media() and q.unconf_btn.isEnabled()
    finally:
        wb.close_workbench()
