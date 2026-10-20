"""작업대 통합: 세션 → store → 모드(① 구간 · ② 반응 · ③ 표본) → 내보내기 → 다시 열기 (02 7-3·7-4·7-6, 08 D-09).

화면 없이(QT_QPA_PLATFORM=offscreen) 합성 프로젝트(합성 채팅 100건, 영상 없음)로 돈다. 부품(플레이어·채팅·타임라인)은
공통 계약 모양의 가짜, 폼(구간 폼·반응 편집기·큐)과 store 는 진짜다. 실제 채팅·닉네임은 쓰지 않는다.
"""
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget  # noqa: E402

from app import project as project_mod  # noqa: E402
from app import session as sess  # noqa: E402
from app import store as store_mod  # noqa: E402
from app.core import phase as ph  # noqa: E402
from app.core import timecode as tc  # noqa: E402
from app.ui import project_view as pv  # noqa: E402
from app.ui import workbench as wbm  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 9, 0, 0, tzinfo=KST)
N_CHATS = 100
STEP = 1_500
DURATION = N_CHATS * STEP + 5_000
MID = "0f3a9c2e-5b7d-4e1f-8a6b-1c2d3e4f5a6b"
SID = "L01-P-20261021-ab12"
MARKER = "블라인드표식"            # 다른 세션·엔진 결과 파일에만 넣는 글자. 화면 어디에도 나오면 안 된다


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


# --------------------------------------------------------------------------- 가짜 부품 (02 3-3 계약 모양)
class FakePlayer(QLabel):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playingChanged = Signal(bool)
    errorOccurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__("player", parent)
        self.calls = []

    def load(self, path): self.calls.append(("load", path)); return True
    def play(self): self.calls.append(("play",))
    def pause(self): self.calls.append(("pause",))
    def toggle(self): self.calls.append(("toggle",))
    def seek(self, file_ms, exact=True): self.calls.append(("seek", file_ms, exact))
    def position_ms(self): return 0
    def duration_ms(self): return 0
    def frame_step(self, n): self.calls.append(("frame_step", n))
    def set_speed(self, x): self.calls.append(("speed", x))
    def speed(self): return 1.0
    def set_volume(self, v): pass
    def set_ab_loop(self, a_ms, b_ms): self.calls.append(("ab", a_ms, b_ms))
    def close_player(self): self.calls.append(("close",))


class FakeChat(QLabel):
    chatActivated = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__("chat", parent)
        self.records, self.viewer_no, self.selected = None, None, None

    def set_messages(self, records, viewer_no=None): self.records, self.viewer_no = records, viewer_no
    def set_time_ms(self, vod_ms, follow=True): pass
    def set_filter(self, text): pass
    def selected_chat_id(self): return self.selected
    def select_chat(self, chat_id): self.selected = chat_id


class FakeTimeline(QLabel):
    seekRequested = Signal(int)
    viewChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__("timeline", parent)
        self.segments, self.markers, self.buffer_ms, self.loop = [], {}, None, None

    def set_duration(self, ms): self.duration = ms
    def set_position(self, ms): pass
    def set_view(self, start_ms, end_ms): pass
    def zoom(self, factor, anchor_ms): pass
    def set_segments(self, items, buffer_ms=None): self.segments, self.buffer_ms = list(items), buffer_ms
    def set_density(self, counts, bucket_ms): pass
    def set_waveform(self, values, span_ms): pass
    def set_markers(self, name, items): self.markers[name] = list(items)
    def clear_markers(self, name): self.markers.pop(name, None)
    def set_loop_range(self, a, b): self.loop = (a, b)


PARTS = {"player": FakePlayer, "chat": FakeChat, "timeline": FakeTimeline}


def cid(i):
    return f"c{i:020x}"


def make_project(tmp_path):
    proj = project_mod.create(str(tmp_path / "한글 프로젝트"), "합성 방송", "0", labeler_id="L01",
                              created_app_version="0.0.0", now=NOW, salt=bytes(range(32)),
                              sample_seed="0" * 32, engine_author=False, machine4="test", duration_ms=DURATION)
    with open(proj.paths.messages_jsonl, "wb") as f:
        for i in range(N_CHATS):
            m = {"chat_id": cid(i), "t_ms": i * STEP, "server_time_ms": 1_760_000_000_000 + i * STEP,
                 "type_code": 1, "kind": "text", "status": "normal", "viewer_id": "vw_synthaaaaaa" + "abcdefg"[i % 7],
                 "is_anonymous": False, "nickname_masked": "시**", "role": "viewer", "text": f"합성 채팅 {i}",
                 "raw_ref": {"fetch_id": "F20261021-test", "page": 0, "index": i}}
            f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
    cal = tc.Calibration(offset_ms=0, rev=1, status="verified", created_at=NOW.isoformat(), created_by="L01")
    proj.update(lambda d: d.__setitem__("calibration", tc.append_rev(d["calibration"], tc.calibration_to_json(cal))),
                now=NOW, by="L01")
    return proj


def session():
    return sess.SessionContext("L01", "primary", SID, sess.machine4(MID), machine_id=MID,
                               session_created_at="2026-10-21T09:00:00+09:00")


def open_wb(qapp, proj, s=None):
    out = pv.open_checked(proj.root)
    wb = wbm.Workbench(parts=PARTS, session=s or session(), store_options={"app_version": "0.0.0"})
    wb.resize(1366, 768)
    wb.show()
    wb.open_project(out, sync=True)
    qapp.processEvents()
    assert wb.store is not None and not wb.store.read_only, wb.notes
    return wb


def make_two_segments(wb, cut=60_000):
    """① 구간: B 로 경계 하나 → 저스트채팅 [0, cut) · 게임 [cut, 끝). 폼의 Ctrl+Enter 저장."""
    assert wb.set_mode(ph.SEGMENTS)
    f = wb.segment_form
    wb.seek_vod(cut)
    assert wb.add_boundary_here()
    a, b = f.model.segments
    f.select_segment(a.id)
    assert f.set_category("just_chatting") and f.set_confidence("certain") and f.set_rationale("00:00:00 합성 잡담")
    f.select_segment(b.id)
    assert f.set_category("game") and f.set_confidence("probably") and f.set_rationale("00:01:00 합성 게임")
    recs = f.save()
    assert recs is not None and len(recs) == 2, f.problems
    return recs


def fill_direct(ed, chat_i):
    t = chat_i * STEP
    assert ed.set_target(cid(chat_i))
    ed.utt_start.setText(f"{(t + 1_000) / 1000:.3f}")
    ed.utt_end.setText(f"{(t + 3_000) / 1000:.3f}")
    ed._on_utt_times()
    ed.heard.setText("합성 발화")
    ed._on_heard_edited("합성 발화")
    ed.evidence.boxes["nickname_called"].setChecked(True)       # 강한 증거
    ed.set_grade("direct")
    ed.set_confidence("certain")
    ed.rationale.setText(f"{tc.fmt_time(t + 1_000, ms_digits=False, always_hours=True)} 합성 호명")


def signature(st):
    eff = st.effective()
    return sorted((r["kind"], r["entity_id"], r["id"]) for k in wbm.HEAD_KINDS for r in eff.items(k))


def all_texts(w: QWidget) -> str:
    out = []
    for c in w.findChildren(QWidget):
        for attr in ("text", "toolTip", "placeholderText"):
            fn = getattr(c, attr, None)
            if callable(fn):
                try:
                    out.append(str(fn()))
                except TypeError:
                    pass
    return "\n".join(out)


# --------------------------------------------------------------------------- 순수 (core.phase, 마커)
def test_phase_gates_follow_principle_5():
    seg_a = {"entity_id": "a", "body": {"start_ms": 0, "end_ms": 10, "category": "just_chatting"}}
    seg_b = {"entity_id": "b", "body": {"start_ms": 10, "end_ms": 20, "category": "game"}}
    marks = ph.PhaseMarks()
    g = ph.gates([], marks)
    assert g[ph.SEGMENTS].allowed and not g[ph.REACTIONS].allowed and "구간 라벨" in g[ph.REACTIONS].reason
    assert not ph.gates([seg_b], marks)[ph.REACTIONS].allowed               # 저스트채팅 없음
    g = ph.gates([seg_a, seg_b], marks)
    assert not g[ph.REACTIONS].allowed and "완료 표시" in g[ph.REACTIONS].reason
    marks.add({"kind": "phase.mark", "seq": 3, "body": ph.mark_body("segments")})
    g = ph.gates([seg_a, seg_b], marks)
    assert g[ph.REACTIONS].allowed and not g[ph.SAMPLES].allowed and "반응" in g[ph.SAMPLES].reason
    marks.add({"kind": "phase.mark", "seq": 4, "body": ph.mark_body("reactions", scope=["a"])})
    assert ph.gates([seg_a, seg_b], marks)[ph.SAMPLES].allowed
    assert [s["entity_id"] for s in ph.sample_targets([seg_a, seg_b], marks)] == ["a"]
    marks.add({"kind": "phase.mark", "seq": 5, "body": ph.mark_body("segments", done=False)})   # 다시 열기
    assert not ph.gates([seg_a, seg_b], marks)[ph.REACTIONS].allowed
    g = ph.gates([seg_a], marks, can_write_labels=False, why_no_write="보기 전용")
    assert not g[ph.SEGMENTS].allowed and g[ph.SEGMENTS].reason == "보기 전용" and g[ph.BROWSE].allowed


def test_reaction_marks_hollow_until_two_heard_checks():
    r = {"entity_id": "r1", "body": {"result": "graded", "grade": "direct", "reaction_time_ms": 5_000}}
    u = {"entity_id": "r2", "body": {"result": "unconfirmed", "grade": None, "reaction_time_ms": None,
                                     "first_visible_ms": 7_000}}
    rel = {"entity_id": "r3", "body": {"result": "graded", "grade": "related_only", "reaction_time_ms": 9_000}}
    chk = lambda cov, heard=True: {"body": {"target_kind": "reaction", "target_entity_id": "r1",  # noqa: E731
                                            "audio_heard": heard, "coverage_ratio": cov}}
    assert wbm.reaction_marks([r, u, rel], []) == [(5_000, "recheck"), (7_000, "unconfirmed"), (9_000, "reaction")]
    assert wbm.reaction_marks([r], [chk(0.95), chk(0.5)]) == [(5_000, "recheck")]       # 덜 들은 회차는 안 셈
    assert wbm.reaction_marks([r], [chk(0.95), chk(0.95, heard=False)]) == [(5_000, "recheck")]
    assert wbm.reaction_marks([r], [chk(0.95), chk(1.0)]) == [(5_000, "reaction")]


def test_fps_from_probe_and_guide_values():
    assert wbm.fps_from_probe({"r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001", "vfr": False})[0] \
        == pytest.approx(29.97, rel=1e-3)
    assert wbm.fps_from_probe({"fps": 25.0, "vfr": True}) == (None, True)
    assert wbm.fps_from_probe(None) == (None, False)
    data = {"active_guide_version": "0.1", "guides": [{"guide_version": "0.1", "thresholds": {"x": 1}}]}
    assert wbm.guide_values(data, None) == {"x": 1} and wbm.guide_values(data, "9.9") == {}
    assert wbm.segment_buffers([{"body": {"start_ms": 0, "end_ms": 60_000}},
                                {"body": {"start_ms": 60_000, "end_ms": 90_000}}], 90_000, 10_000) == [(50_000, 70_000)]


# --------------------------------------------------------------------------- 단계 순서·모드
def test_no_session_is_view_only_and_modes_off(qapp, tmp_path):
    proj = make_project(tmp_path)
    wb = wbm.Workbench(parts=PARTS)
    try:
        wb.open_project(proj, sync=True)
        assert wb.store is None
        for m in (ph.SEGMENTS, ph.REACTIONS, ph.SAMPLES):
            assert not wb.mode_buttons[m].isEnabled()
            assert not wb.set_mode(m)
        assert "보기 전용" in wb.mode_reason.text()
        assert wb.mode == ph.BROWSE
    finally:
        wb.close_workbench()


def test_reactions_and_samples_locked_until_segments_done(qapp, tmp_path):
    proj = make_project(tmp_path)
    wb = open_wb(qapp, proj)
    try:
        assert not wb.mode_buttons[ph.REACTIONS].isEnabled()
        assert not wb.set_mode(ph.REACTIONS) and "구간" in wb.mode_reason.text()
        make_two_segments(wb)
        # 폼의 모델이 곧바로 타임라인에 (entity_id 가 붙은 dict, 완충 시작값)
        assert [(s["start_ms"], s["end_ms"], s["category"]) for s in wb.timeline.segments] == \
            [(0, 60_000, "just_chatting"), (60_000, DURATION, "game")]
        assert wb.timeline.buffer_ms == 10_000
        assert not wb.gates()[ph.REACTIONS].allowed                       # 구간 완료 표시 전
        assert wb.mark_phase_done() is not None
        assert wb.gates()[ph.REACTIONS].allowed and not wb.gates()[ph.SAMPLES].allowed
        assert wb.btn_phase_done.text() == "구간 다시 열기"
        assert wb.set_mode(ph.REACTIONS)
        assert not wb.set_mode(ph.SAMPLES)
    finally:
        wb.close_workbench()


def test_segments_mode_shortcuts_and_unsaved_prompt(qapp, tmp_path):
    proj = make_project(tmp_path)
    wb = open_wb(qapp, proj)
    try:
        assert wb.set_mode(ph.SEGMENTS)
        assert not wb.shortcut_map["Ctrl+Z"].isEnabled()                # ① 구간의 Ctrl+Z 는 폼(모델 층)
        assert "Ctrl+Z" in wb.segment_form.shortcuts
        wb.seek_vod(30_000)
        wb.add_boundary_here()
        assert len(wb.segment_form.model.segments) == 2
        assert len(wb.timeline.segments) == 2                            # 저장 전 모델이 보인다
        wb.undo_shortcut()                                               # 폼의 모델 층 undo
        assert len(wb.segment_form.model.segments) == 1
        wb.add_boundary_here()
        f = wb.segment_form
        f.select_segment(f.model.segments[0].id)
        f.set_category("other"), f.set_confidence("certain"), f.set_rationale("00:00:00 합성")
        wb.ask_unsaved = lambda: "cancel"
        assert wb.has_unsaved()
        assert not wb.set_mode(ph.BROWSE) and wb.mode == ph.SEGMENTS
        wb.ask_unsaved = lambda: "discard"
        assert wb.set_mode(ph.BROWSE)
        assert wb.store.records()[-1]["kind"] == "session.open"         # 아무 라벨도 쓰지 않았다
        assert wb.timeline.segments == []
    finally:
        wb.close_workbench()


def test_full_labeling_flow_export_and_reopen(qapp, tmp_path):
    proj = make_project(tmp_path)
    wb = open_wb(qapp, proj)
    try:
        make_two_segments(wb)
        wb.mark_phase_done()
        assert wb.set_mode(ph.REACTIONS) and wb.shortcut_map["Ctrl+Z"].isEnabled()
        # M → 편집 상태 (선택한 채팅이 대상)
        wb.chat.select_chat(cid(10))
        wb.seek_vod(10 * STEP + 1_000)
        wb.mark_reaction()
        ed = wb.editor
        assert wb.is_editing() and wb.right.currentWidget() is ed and ed.target_chat_id() == cid(10)
        assert ed.grade() is None                                         # 등급을 미리 고르지 않는다
        assert not wb.set_mode(ph.BROWSE)                                 # 편집 중에는 모드를 바꾸지 않는다
        fill_direct(ed, 10)
        res = ed.save()
        assert res is not None, ed.msg.text()
        assert not wb.is_editing() and wb.right.currentWidget() is wb.page_chat
        assert res["complete"] is False                                   # 영상 없이 7-2 재확인을 못 했다
        assert wb.timeline.markers["labels"] == [(10 * STEP + 1_000, "recheck")]    # 속이 빈 표시
        # Ctrl+Z (2단계) = 직전 반응 저장 되돌리기: 새로 만든 반응·발화를 철회(거꾸로, 반응 철회 포함)
        n = len(wb.store.records())
        rx_eid = res["reaction"]["entity_id"]
        wb.undo_shortcut()
        added = wb.store.records()[n:]
        assert added and all(r["kind"] == "retract" for r in added)
        assert rx_eid in {r["entity_id"] for r in added} and len(added) == len(res["written"])
        assert wb.timeline.markers["labels"] == []
        # 채팅 더블클릭으로 다시
        wb.chat.chatActivated.emit(cid(12), 12 * STEP)
        assert wb.is_editing()
        fill_direct(ed, 12)
        assert ed.save() is not None
        assert wb.mark_phase_done() is not None                           # 반응 완료 표시
        assert wb.set_mode(ph.SAMPLES)
        q = wb.queue
        assert wb.sample_seg_box.count() == 1 and wb.btn_draw.isEnabled()
        wb.draw_sample(sync=True)
        mans = os.listdir(proj.paths.samples_dir)
        assert len(mans) == 1 and mans[0].startswith("S-")
        man = q.manifest
        assert man["census"] is True and man["excluded"]["buffer"] > 0 and len(man["items"]) == man["eligible"]["size"]
        assert not wb.btn_draw.isEnabled()
        wb.draw_sample(sync=True)                                         # 다시 뽑지 않는다
        assert len(os.listdir(proj.paths.samples_dir)) == 1
        assert q.run_verify().ok
        # 1건 판정 (반응 미확인)
        first = q.current_item()["chat_id"]
        q.set_confidence("certain")
        q.rationale.setText("00:00:05 합성 답 없음")
        rec = q.judge_unconfirmed()
        assert rec is not None and rec["body"]["entry"] == "from_sample"
        assert q.state_of(first) == "judged"
        assert q.progress().done == 1
        # 블라인드: 큐·편집기 어디에도 등급 제안·엔진이 없다
        assert "engine" not in wb.timeline.markers
        # 내보내기 → zip
        out_zip = tmp_path / "내보내기" / "labels.zip"
        out_zip.parent.mkdir()
        er = wb.export_labels(str(out_zip))
        with zipfile.ZipFile(out_zip) as z:
            names = set(z.namelist())
            labels = [json.loads(x) for x in z.read("labels.jsonl").decode("utf-8").splitlines() if x.strip()]
        assert {"labels.jsonl", "segments.csv", "reactions.csv", "manifest.json"} <= names
        assert er.session_ids == [SID] or list(er.session_ids) == [SID]
        assert not [w for w in er.warnings if "export-row" in w]          # 모든 행이 내보내기 형식에 맞다
        before = signature(wb.store)
        n_reactions = len([r for r in labels if r.get("label_kind") == "reaction"])
        assert n_reactions == 2 and er.by_kind["segment"] == 2             # 2단계 1건 + 3단계 1건
    finally:
        wb.close_workbench()
    # session.close 줄 (reason user)
    path = os.path.join(proj.paths.labels_dir, session().file_name)
    with open(path, encoding="utf-8") as f:
        last = json.loads(f.read().splitlines()[-1])
    assert last["kind"] == "session.close" and last["body"]["reason"] == "user"
    # 다시 열면 같은 현재 유효 라벨
    wb2 = open_wb(qapp, proj)
    try:
        assert signature(wb2.store) == before
        assert len(wb2.segment_heads()) == 2 and wb2.gates()[ph.SAMPLES].allowed
        assert wb2.timeline.markers["labels"][0][1] in ("recheck", "unconfirmed")
        assert n_reactions >= 1
    finally:
        wb2.close_workbench("app_exit")


def test_real_key_events_reach_one_handler(qapp, tmp_path):
    """같은 키를 두 곳이 잡으면 Qt 가 '애매한 단축키' 로 버린다. 진짜 키 이벤트로 B · Ctrl+Z · M · R 이 한 곳에만 가는지."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    proj = make_project(tmp_path)
    wb = open_wb(qapp, proj)
    try:
        wb.activateWindow()
        qapp.processEvents()
        assert wb.set_mode(ph.SEGMENTS)
        wb.seek_vod(60_000)
        wb.setFocus()
        QTest.keyClick(wb, Qt.Key_B)                                      # 작업대 → 폼.add_boundary
        assert len(wb.segment_form.model.segments) == 2
        QTest.keyClick(wb, Qt.Key_Z, Qt.ControlModifier)                 # 폼의 모델 층 undo (작업대 Ctrl+Z 는 꺼짐)
        assert len(wb.segment_form.model.segments) == 1
        QTest.keyClick(wb, Qt.Key_B)
        f = wb.segment_form
        a, b = f.model.segments
        f.select_segment(a.id)
        f.set_category("just_chatting"), f.set_confidence("certain"), f.set_rationale("00:00:00 합성")
        f.select_segment(b.id)
        f.set_category("game"), f.set_confidence("certain"), f.set_rationale("00:01:00 합성")
        QTest.keyClick(wb, Qt.Key_Return, Qt.ControlModifier)             # 폼의 Ctrl+Enter 저장
        assert len(wb.segment_heads()) == 2, f.problems
        wb.mark_phase_done()
        assert wb.set_mode(ph.REACTIONS)
        wb.chat.select_chat(cid(10))
        wb.setFocus()
        QTest.keyClick(wb, Qt.Key_M)                                      # 작업대 M → 편집기
        assert wb.is_editing()
        wb.editor.setFocus()
        QTest.keyClick(wb.editor, Qt.Key_Escape)                         # 편집기 Esc
        assert not wb.is_editing()
        wb.mark_phase_done()
        assert wb.set_mode(ph.SAMPLES)
        wb.draw_sample(sync=True)
        wb.queue.setFocus()
        loop0 = wb.queue._loop_on
        QTest.keyClick(wb.queue, Qt.Key_R)                                # 큐 화면의 R 은 꺼져 있고 작업대가 넘긴다
        assert wb.queue._loop_on is (not loop0)
        QTest.keyClick(wb.queue, Qt.Key_M)                                # 큐 화면에서 M → 같은 편집기
        assert wb.is_editing() and wb.editor._entry == "from_sample"
        wb.editor.cancel()
        assert wb.right.currentWidget() is wb.page_samples
    finally:
        wb.close_workbench()


def test_labeling_session_never_shows_engine_or_other_sessions(qapp, tmp_path):
    """안내서 원칙 3: 다른 세션 기록·엔진 결과 파일에 넣은 표식 글자가 작업대 어디에도 나오지 않는다."""
    proj = make_project(tmp_path)
    os.makedirs(proj.paths.derived_dir, exist_ok=True)
    with open(os.path.join(proj.paths.derived_dir, "link-test.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "chatcoach/ai-result", "time_axis": "vod_ms", "note": MARKER,
                   "reactions": [{"chat_id": cid(10), "result": "graded", "grade": "direct",
                                  "reaction_time_ms": 16_000, "rationale": MARKER}]}, f, ensure_ascii=False)
    other = sess.SessionContext("L02", "primary", "L02-P-20261021-zz99", sess.machine4(MID), machine_id=MID,
                                session_created_at="2026-10-21T09:00:00+09:00")
    st = store_mod.ProjectStore.open(proj.root, other, lambda p: pv.default_lock_factory(p), app_version="0.0.0")
    st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 60_000, "category": "just_chatting",
                                                 "confidence": "certain", "rationale": MARKER}})
    st.close()
    wb = open_wb(qapp, proj)
    try:
        assert not wb.analysis_action.isVisible()
        wb.set_analysis_view(True)
        wb.wait_jobs(5_000)
        assert "engine" not in wb.timeline.markers
        assert wb.segment_heads() == []                                   # 다른 라벨러 구간은 읽지 않았다
        make_two_segments(wb)
        wb.mark_phase_done()
        wb.set_mode(ph.REACTIONS)
        wb.chat.select_chat(cid(10))
        wb.mark_reaction()
        assert MARKER not in all_texts(wb)
        assert "L02" not in all_texts(wb)
        wb.editor.cancel()
        assert not wb.is_editing()
    finally:
        wb.close_workbench()


def test_mainwindow_session_dialog_opens_store_and_closes_with_reason(qapp, tmp_path, monkeypatch):
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    from app.ui.session_dialog import SessionDialog
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))
    proj = make_project(tmp_path)
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    sess.add_labeler(data_root, "L01")
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    win = mw.MainWindow(SPEC, inst, data_root, str(tmp_path / "log.txt"), None, [], set())
    made = []

    def chooser(outcome):
        dlg = SessionDialog(win, data_root=data_root, machine_id_value=MID, now=lambda: NOW, rand4=lambda: "ab12")
        dlg.load_context(outcome.project.root, outcome.project.data)
        assert dlg.select_labeler("L01") and dlg.choose("new:primary")
        dlg._choose_open()
        made.append(dlg.context())
        return dlg.context()
    win.session_chooser = chooser
    try:
        out = win.project_view.open_path(proj.root)
        assert out.ok and made and made[0].session_id == SID
        wb = win.workbench
        assert wb.wait_jobs(15_000) and wb.store is not None and not wb.store.read_only
        assert win.a_export.isEnabled()
        assert SID in wb.status_text()
        win.show_home()
        assert win.session is None
    finally:
        win.close()
    with open(os.path.join(proj.paths.labels_dir, made[0].file_name), encoding="utf-8") as f:
        recs = [json.loads(x) for x in f.read().splitlines()]
    assert recs[0]["kind"] == "session.open" and recs[-1]["kind"] == "session.close"
    assert recs[-1]["body"]["reason"] == "user"


def test_mainwindow_update_menu_mnemonic_does_not_clash_with_alt_u(qapp, tmp_path):
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    win = mw.MainWindow(SPEC, inst, str(tmp_path / "data"), str(tmp_path / "log.txt"), None, [], set())
    try:
        titles = [a.text() for a in win.menuBar().actions()]
        assert not any("&U" in t for t in titles), titles                  # Alt+U = 구간 폼 '판단 불가'
    finally:
        win.close()


def test_open_checked_lock_message_uses_store_help(qapp, tmp_path):
    proj = make_project(tmp_path)
    a = pv.open_checked(proj.root)
    try:
        b = pv.open_checked(proj.root)
        assert b.read_only
        assert any("창에서 프로젝트를 닫은 뒤" in p or "열고 있습니다" in p for p in b.project.problems)
    finally:
        pv.release_outcome(a)
    QSettings  # noqa: B018 — import 확인용
