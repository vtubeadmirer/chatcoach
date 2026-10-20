"""활동 구간 폼 (app/ui/forms/segment_form.py) — 화면 없이 (QT_QPA_PLATFORM=offscreen). 합성 데이터만.

폼은 store 를 모른다: save·retract·cancel 콜백만 부른다. 앞부분은 콜백을 기록하는 가짜(Sink)로,
뒷부분은 진짜 ProjectStore(임시 한글 폴더의 합성 프로젝트)에 이어 저장 → 다시 열기 → 같은 상태를 확인한다.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.segments import SegmentThresholds  # noqa: E402
from app.ui.forms import SegmentForm  # noqa: E402
from app.ui.forms import segment_form as sf  # noqa: E402

DUR = 600_000          # 10분 합성 영상
SID = "L01-P-20261021-ab12"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def idgen(prefix: int = 0xF):
    """폼이 만드는 구간 ID (store 가 만드는 ID 와 겹치지 않게 앞자리를 다르게)."""
    n = [0]

    def make():
        n[0] += 1
        return f"{prefix:x}{n[0]:07x}-0000-4000-8000-{n[0]:012x}"
    return make


class Sink:
    """save·retract·cancel 콜백 대역. 받은 것을 기록하고 store 처럼 id 가 붙은 레코드를 돌려준다."""

    def __init__(self, fail_at: int | None = None):
        self.drafts: list[dict] = []
        self.retracts: list[tuple] = []
        self.cancels = 0
        self.n = 0
        self.fail_at = fail_at

    def _rid(self):
        self.n += 1
        return f"e{self.n:07x}-1111-4111-8111-{self.n:012x}"

    def save(self, draft):
        if self.fail_at is not None and len(self.drafts) + len(self.retracts) == self.fail_at:
            raise RuntimeError("합성 실패")
        self.drafts.append(draft)
        return {"id": self._rid(), "kind": draft["kind"], "entity_id": draft["entity_id"],
                "supersedes": draft["supersedes"], "body": draft["body"]}

    def retract(self, eid, reason, merged_into):
        self.retracts.append((eid, reason, merged_into))
        return {"id": self._rid(), "kind": "retract", "entity_id": eid}

    def cancel(self):
        self.cancels += 1


def make(qapp, sink=None, *, records=(), fps=30.0, use_retract=True, **kw):
    sink = sink or Sink()
    form = SegmentForm(sink.save, sink.cancel, retract=sink.retract if use_retract else None)
    form.load_context(duration_ms=DUR, records=records, fps=fps, new_id=idgen(), **kw)
    return form, sink


def label(form, cat="just_chatting", conf="certain", why="00:00 합성 근거"):
    assert form.set_category(cat)
    assert form.set_confidence(conf)
    assert form.set_rationale(why)


def rec(eid, rid, s, e, cat="just_chatting", sid=SID, **body):
    b = {"start_ms": s, "end_ms": e, "category": cat, "confidence": "certain", "rationale": "합성",
         "active_chat": None, "start_signal": None, "long_silence_reviewed": None,
         "split_from": None, "merged_from": [], "out_of_media": False}
    b.update(body)
    return {"kind": "segment", "id": rid, "entity_id": eid, "session_id": sid, "supersedes": None, "body": b}


A = "a0000001-0000-4000-8000-000000000001"
B = "a0000002-0000-4000-8000-000000000002"
C = "a0000003-0000-4000-8000-000000000003"
RA, RB, RC = ("b0000001-0000-4000-8000-000000000001", "b0000002-0000-4000-8000-000000000002",
              "b0000003-0000-4000-8000-000000000003")


# ---------------------------------------------------------------------------- 기본
def test_empty_project_one_unlabeled_segment(qapp):
    form, sink = make(qapp)
    segs = form.segments_for_timeline()
    assert len(segs) == 1 and segs[0]["category"] is None
    assert (segs[0]["start_ms"], segs[0]["end_ms"]) == (0, DUR)
    assert segs[0]["entity_id"] == form.selected_id
    assert form.save() == []                     # 안 매긴 시간은 기록하지 않는다
    assert sink.drafts == [] and not form.has_unsaved


def test_label_and_save_draft_shape(qapp):
    form, sink = make(qapp)
    changed = []
    form.segmentsChanged.connect(lambda: changed.append(1))
    label(form)
    assert changed                               # 작업대가 타임라인을 다시 그릴 신호
    assert form.has_unsaved
    out = form.save()
    assert len(out) == 1 and len(sink.drafts) == 1
    d = sink.drafts[0]
    assert d["kind"] == "segment" and d["supersedes"] is None and d["entity_id"] == form.selected_id
    assert set(d) == {"kind", "entity_id", "supersedes", "body"}
    body = d["body"]
    assert body["category"] == "just_chatting" and body["confidence"] == "certain"
    assert body["rationale"] == "00:00 합성 근거" and body["active_chat"] is None
    assert (body["start_ms"], body["end_ms"]) == (0, DUR)
    assert not form.has_unsaved
    # 바뀐 것이 없으면 다시 쓰지 않는다
    assert form.save() == [] and len(sink.drafts) == 1
    # 고치면 같은 entity_id 새 판, supersedes = 직전 판 id
    form.set_confidence("probably")
    form.save()
    assert sink.drafts[1]["entity_id"] == d["entity_id"]
    assert sink.drafts[1]["supersedes"] == out[0]["id"]


def test_rationale_is_required(qapp):
    form, sink = make(qapp)
    form.set_category("game")
    form.set_confidence("certain")
    assert form.save() is None                   # 고른 구간이 '매기는 중'
    assert "근거" in form.lbl_msg.text()
    assert not form.set_rationale("   ")         # 빈 근거는 받지 않는다
    assert sink.drafts == []
    form.ed_rationale.setText("01:00 게임 시작")   # 칸에만 쓰고 저장 → 저장이 먼저 넣는다
    assert form.has_unsaved
    assert form.save() and sink.drafts[0]["body"]["rationale"] == "01:00 게임 시작"


def test_no_preselected_values(qapp):
    """등급처럼 미리 고르지 않는다: 새 구간은 분류·확신도 버튼이 하나도 눌려 있지 않다."""
    form, _ = make(qapp)
    assert not any(b.isChecked() for b in form.cat_buttons.values())
    assert not any(b.isChecked() for b in form.conf_buttons.values())
    form.add_boundary(100_000)
    assert form.selected.category is None


def test_active_chat_only_for_game(qapp):
    form, sink = make(qapp)
    form.set_category("just_chatting")
    assert not form.chk_active.isEnabled()
    assert not form.set_active_chat(True)
    form.set_category("game")
    assert form.chk_active.isEnabled()
    assert form.set_active_chat(True)
    assert form.selected.active_chat is True and form.chk_active.isChecked()
    form.set_category("singing")                 # 게임이 아니면 비운다 (스키마: null)
    assert form.selected.active_chat is None and not form.chk_active.isEnabled()
    form.set_category("game")
    form.chk_active.click()                      # 화면의 체크 상자로도
    assert form.selected.active_chat is True
    form.set_confidence("certain")
    form.set_rationale("02:00 게임 중 채팅 대화")
    form.save()
    assert sink.drafts[-1]["body"]["active_chat"] is True


def test_start_signal_optional(qapp):
    form, sink = make(qapp)
    label(form)
    assert form.set_start_signal("screen")
    form.save()
    assert sink.drafts[-1]["body"]["start_signal"] == "screen"


# ---------------------------------------------------------------------------- 경계
def test_add_boundary_split_records(qapp):
    form, sink = make(qapp)
    label(form)
    out = form.save()
    a_id, a_rec = form.selected_id, out[0]["id"]
    assert form.add_boundary(200_000)
    c = form.selected                            # 뒤쪽 새 구간을 고른다
    assert c.start_ms == 200_000 and c.split_from == a_id and c.category is None
    # 저장: A 새 판(끝 줄임)만. C 는 안 매긴 시간이라 아직 안 씀
    form.save()
    assert len(sink.drafts) == 2
    d = sink.drafts[1]
    assert d["entity_id"] == a_id and d["supersedes"] == a_rec and d["body"]["end_ms"] == 200_000
    label(form, "game", "probably", "03:20 게임 화면")
    form.save()
    d = sink.drafts[2]
    assert d["entity_id"] == c.id and d["supersedes"] is None
    assert d["body"]["split_from"] == a_id and d["body"]["start_ms"] == 200_000
    segs = form.segments_for_timeline()
    assert [(s["start_ms"], s["end_ms"], s["category"]) for s in segs] == [
        (0, 200_000, "just_chatting"), (200_000, DUR, "game")]


def test_add_boundary_inherit_option(qapp):
    sink = Sink()
    form = SegmentForm(sink.save, sink.cancel, retract=sink.retract, inherit_on_split=True)
    form.load_context(duration_ms=DUR, new_id=idgen())
    label(form)
    form.add_boundary(300_000)
    assert form.selected.category == "just_chatting"


def test_add_boundary_rejected_outside_or_too_close(qapp):
    form, _ = make(qapp)
    assert not form.add_boundary(0)
    assert not form.add_boundary(DUR)
    assert form.add_boundary(100_000)
    assert not form.add_boundary(100_050)        # min_gap 100ms
    assert len(form.model) == 2


def test_nudge_frames_and_seconds(qapp):
    form, _ = make(qapp, fps=30.0)
    form.add_boundary(60_000)                    # 고른 구간 = 뒤 구간, 시작 경계가 60초
    assert form.edge_ms() == 60_000
    assert form.nudge_frames(1) and form.edge_ms() == 60_033   # 30fps 격자 (1800프레임 → 1801)
    assert form.nudge_frames(-1) and form.edge_ms() == 60_000
    assert form.nudge_seconds(1) and form.edge_ms() == 61_000
    assert form.nudge_seconds(-2) and form.edge_ms() == 59_000
    assert form.model.boundaries == [59_000]
    # 앞 구간의 끝 경계도 같은 경계
    form.select_relative(-1)
    form.choose_edge("end")
    assert form.edge_ms() == 59_000
    form.choose_edge("start")                    # 영상 처음 → 움직일 경계 없음
    assert form.edge_ms() is None and not form.nudge_seconds(1)


def test_nudge_frame_grid_origin_and_vfr(qapp):
    form, _ = make(qapp, fps=25.0, frame_origin_ms=10)
    assert form.frame_step_ms(1_010, 1) == 1_050
    assert form.frame_step_ms(1_010, -1) == 970
    form2, _ = make(qapp, fps=None)              # 가변 프레임률: 0.1초
    form2.add_boundary(60_000)
    assert form2.nudge_frames(1) and form2.edge_ms() == 60_100
    assert "0.1초" in form2.btn_p1f.text()


def test_nudge_blocked_by_neighbor(qapp):
    form, _ = make(qapp)
    form.add_boundary(60_000)
    form.add_boundary(60_500)                    # 고른 구간 = [60.5s, 끝)
    form.select_relative(-1)                     # [60s, 60.5s)
    form.choose_edge("end")
    before = list(form.model.boundaries)
    assert not form.nudge_seconds(-1)            # 이웃 경계를 넘음 → 거절, 모델 그대로
    assert form.model.boundaries == before


def test_move_edge_to_position(qapp):
    form, _ = make(qapp)
    form.add_boundary(100_000)
    form.set_position(100_000)
    form._pos = 95_000                           # 재생 위치만 바꿈(선택은 그대로)
    assert form.move_edge_to(95_000)
    assert form.model.boundaries == [95_000]


# ---------------------------------------------------------------------------- 합치기 제안
def test_short_segment_suggestion_not_auto(qapp):
    form, sink = make(qapp)
    label(form)
    form.add_boundary(100_000)
    label(form, "game", "certain", "01:40 게임")
    form.add_boundary(110_000)                   # [100s, 110s) 10초짜리 게임 구간
    label(form, "just_chatting", "certain", "01:50 잡담")
    form.select_at(105_000)
    sug = form.merge_suggestions()
    assert [m.segment_id for m in sug] == [form.selected_id]
    assert len(form.model) == 3                  # 자동으로 합치지 않았다
    assert "제안" in form.lbl_suggest.text() and "★" in form.btn_merge_prev.text()
    form.save()
    n_before = len(sink.drafts)
    short_id = form.selected_id
    prev_id = form.model.segments[0].id
    assert form.accept_suggestion()              # 사람이 눌러야 합친다
    assert len(form.model) == 2 and form.selected_id == prev_id
    form.save()
    w = sink.drafts[n_before:]
    assert len(w) == 1 and w[0]["entity_id"] == prev_id
    assert w[0]["body"]["merged_from"] == [short_id] and w[0]["body"]["end_ms"] == 110_000
    assert sink.retracts == [(short_id, "merged", prev_id)]


def test_select_next_short(qapp):
    form, _ = make(qapp)
    for t in (100_000, 110_000, 300_000, 320_000):
        form.add_boundary(t)
    form.select_at(0)
    assert form.select_next_short() and form.selected.start_ms == 100_000
    assert form.select_next_short() and form.selected.start_ms == 300_000
    assert form.select_next_short() and form.selected.start_ms == 100_000   # 처음으로 돈다


def test_merge_into_unlabeled_survivor_blocks_save(qapp):
    form, sink = make(qapp)
    label(form)
    form.add_boundary(100_000)                   # 뒤 구간은 안 매김
    form.save()
    form.select_at(0)
    n = len(sink.drafts)
    assert form.merge_selected("next")           # 안 매긴 뒤 구간이 남음
    assert form.save() is None
    assert "채운 뒤" in form.lbl_msg.text()
    assert len(sink.drafts) == n and sink.retracts == []
    label(form, "just_chatting", "certain", "00:00 합침")
    out = form.save()
    assert out and sink.retracts[0][1] == "merged"


# ---------------------------------------------------------------------------- 실행 취소 두 층
def test_undo_is_model_only_then_save_records_it(qapp):
    form, sink = make(qapp)
    label(form)
    first = form.save()
    eid = form.selected_id
    form.set_category("game")
    form.save()
    assert len(sink.drafts) == 2
    assert form.undo()                           # 모델만: 디스크에 쓰지 않는다
    assert len(sink.drafts) == 2 and form.selected.category == "just_chatting"
    assert form.has_unsaved
    form.save()                                  # 되돌림을 새 판으로
    d = sink.drafts[2]
    assert d["entity_id"] == eid and d["body"]["category"] == "just_chatting"
    assert d["supersedes"] != first[0]["id"]     # 머리(두 번째 판)를 가리킨다
    assert form.redo() and form.selected.category == "game"


def test_undo_split_after_save_retracts_created(qapp):
    form, sink = make(qapp)
    label(form)
    form.add_boundary(200_000)
    label(form, "game", "certain", "03:20 게임")
    form.save()
    c_id = form.selected_id
    for _ in range(4):                           # 근거·확신도·분류·경계 추가를 되돌림
        form.undo()
    assert len(form.model) == 1
    form.save()
    assert (c_id, "undo", None) in sink.retracts
    assert sink.drafts[-1]["body"]["end_ms"] == DUR   # A 는 다시 끝까지


def test_undo_merge_revives_retracted(qapp):
    form, sink = make(qapp)
    label(form)
    form.add_boundary(100_000)
    label(form, "game", "certain", "01:40 게임")
    form.save()
    g_id = form.selected_id
    form.merge_selected("prev")
    out = form.save()
    retract_rec = [r for r in out if r.get("kind") == "retract"][0]
    assert form.undo()                           # 합치기 되돌림 → 철회된 대상이 모델에 다시
    form.save()
    revived = [d for d in sink.drafts if d["entity_id"] == g_id][-1]
    assert revived["supersedes"] == retract_rec["id"]


def test_undo_label_back_to_unlabeled_retracts(qapp):
    form, sink = make(qapp)
    label(form)
    form.save()
    eid = form.selected_id
    for _ in range(3):
        form.undo()
    assert form.selected.category is None
    form.save()
    assert sink.retracts == [(eid, "undo", None)]


def test_undo_discards_field_text_first(qapp):
    form, _ = make(qapp)
    label(form)
    form.ed_rationale.setText("고치던 글자")
    assert form.undo()
    assert form.ed_rationale.text() == "00:00 합성 근거"
    assert form.selected.rationale == "00:00 합성 근거"


def test_retract_fallback_via_save(qapp):
    """retract 콜백이 없으면 철회도 save 로 kind=retract draft 를 보낸다(작업대가 store.retract 로 가른다)."""
    form, sink = make(qapp, use_retract=False)
    label(form)
    form.add_boundary(100_000)
    label(form, "game", "certain", "01:40 게임")
    out = form.save()
    g_id, g_rec = form.selected_id, out[-1]["id"]
    form.merge_selected("prev")
    n0 = len(sink.drafts)
    form.save()
    r = sink.drafts[n0]                                  # 철회가 먼저(겹침 방지), 그다음 남은 구간의 새 판
    assert [d["kind"] for d in sink.drafts[n0:]] == ["retract", "segment"]
    assert r["kind"] == "retract" and r["entity_id"] == g_id and r["supersedes"] == g_rec
    assert r["body"] == {"reason": "merged", "merged_into": form.selected_id}


def test_save_failure_midway_keeps_written(qapp):
    sink = Sink(fail_at=1)
    form, _ = make(qapp, sink)
    label(form)
    form.add_boundary(200_000)
    label(form, "game", "certain", "03:20 게임")
    assert form.save() is None                   # 두 번째 줄에서 실패
    assert len(sink.drafts) == 1 and "실패" in form.lbl_msg.text()
    assert len(form.plan().writes) == 1          # 남은 것만 다시
    sink.fail_at = None
    assert len(form.save()) == 1


# ---------------------------------------------------------------------------- 불러오기 · 블라인드
def test_load_existing_records_not_dirty(qapp):
    recs = [rec(A, RA, 0, 100_000), rec(B, RB, 100_000, 400_000, "game", active_chat=True)]
    form, sink = make(qapp, records=recs, position_ms=150_000)
    assert form.selected_id == B and form.selected.active_chat is True
    segs = form.segments_for_timeline()
    assert [s["category"] for s in segs] == ["just_chatting", "game", None]   # 끝 빈틈 = 안 매긴 시간
    assert not form.has_unsaved
    form.set_category("singing")
    form.save()
    assert sink.drafts[0]["entity_id"] == B and sink.drafts[0]["supersedes"] == RB


def test_other_session_records_are_dropped(qapp):
    recs = [rec(A, RA, 0, 100_000), rec(B, RB, 100_000, 200_000, sid="L02-P-20261021-zz99")]
    form, _ = make(qapp, records=recs, session_id=SID)
    cats = [s["category"] for s in form.segments_for_timeline()]
    assert cats == ["just_chatting", None]       # 다른 세션 구간은 그리지 않는다
    assert any("다른 세션" in p for p in form.problems)
    assert "1건" in form.problems[0]


def test_overlap_records_read_only(qapp):
    recs = [rec(A, RA, 0, 100_000), rec(B, RB, 50_000, 200_000)]
    form, sink = make(qapp, records=recs)
    assert form.read_only and form.model is None
    assert form.problems and form.segments_for_timeline() == []
    assert not form.add_boundary(10_000) and form.save() is None
    assert not form.btn_save.isEnabled()


def test_read_only_context(qapp):
    form, sink = make(qapp, read_only=True)
    assert not form.set_category("game") and not form.add_boundary(10_000)
    assert form.save() is None and sink.drafts == []
    assert not any(b.isEnabled() for b in form.cat_buttons.values())


def test_thresholds_from_guide_section(qapp):
    form, _ = make(qapp, thresholds={"boundary_buffer_sec": 5, "min_length_sec": 60})
    assert form.buffer_ms == 5_000 and form.min_length_ms == 60_000
    form.add_boundary(40_000)
    assert len(form.merge_suggestions()) == 1    # 40초 < 60초
    form2, _ = make(qapp, thresholds=SegmentThresholds(buffer_ms=0, min_length_ms=10_000))
    assert form2.buffer_ms == 0


def test_form_module_reads_no_engine_or_other_sessions():
    """블라인드: 폼은 엔진·AI·store·visibility 를 import 하지 않는다(받은 레코드만 그림)."""
    import ast
    src = open(sf.__file__, encoding="utf-8").read()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            mods.add((node.module or ""))
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    joined = " ".join(mods)
    for bad in ("engine", "store", "visibility", "analysis", "workbench", "resolve"):
        assert bad not in joined, bad


# ---------------------------------------------------------------------------- 선택 · 취소 · 단축키
def test_set_position_follows_unless_editing(qapp):
    form, _ = make(qapp)
    form.add_boundary(100_000)
    seen = []
    form.selectionChanged.connect(lambda sid, ms: seen.append(ms))
    form.set_position(50_000)
    assert form.selected.start_ms == 0 and seen == [0]
    form.ed_rationale.setText("쓰는 중")          # 근거를 고치는 중이면 따라가지 않는다
    form.set_position(150_000)
    assert form.selected.start_ms == 0
    form.follow_position = True
    form.ed_rationale.setText("")
    form._refresh()
    form.set_position(150_000)
    assert form.selected.start_ms == 100_000


def test_cancel_reverts_field_and_calls_back(qapp):
    form, sink = make(qapp)
    label(form)
    form.ed_rationale.setText("버릴 글자")
    form.shortcuts["Escape"].activated.emit()
    assert sink.cancels == 1 and form.ed_rationale.text() == "00:00 합성 근거"


def test_insert_time(qapp):
    form, _ = make(qapp)
    form._pos = 3_723_400
    form.ed_rationale.setText("")
    form.insert_time()
    assert form.ed_rationale.text().startswith("01:02:03.4")


def test_shortcut_table(qapp):
    form, sink = make(qapp)
    keys = set(form.shortcuts)
    for k in list(sf.CATEGORY_KEYS.values()) + list(sf.CONFIDENCE_KEYS.values()):
        assert k in keys
    assert {"Alt+,", "Alt+.", "Alt+Left", "Alt+Right", "Ctrl+Return", "Ctrl+Z", "Ctrl+Y", "Ctrl+T"} <= keys
    # 02 7-6 원칙: 수정자 없는 글자·숫자 키를 쓰지 않는다 ('B' 는 작업대가 잡는다)
    for k in keys:
        assert k == "Escape" or k.startswith(("Alt+", "Ctrl+")), k
    assert "B" not in keys
    # 단축키로 매기기
    form.shortcuts["Alt+G"].activated.emit()
    form.shortcuts["Alt+2"].activated.emit()
    assert form.selected.category == "game" and form.selected.confidence == "probably"
    form.set_rationale("01:00 게임")
    form.shortcuts["Ctrl+Return"].activated.emit()
    assert len(sink.drafts) == 1
    form.add_boundary(60_000)
    form.shortcuts["Alt+."].activated.emit()
    assert form.edge_ms() == 60_033
    form.shortcuts["Ctrl+Z"].activated.emit()
    assert form.edge_ms() == 60_000


def test_real_key_event_alt_j(qapp):
    """실제 키 이벤트로 Alt+J 가 분류를 바꾸는지 (창이 활성화되지 않는 환경이면 건너뜀)."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    form, _ = make(qapp)
    form.resize(420, 700)
    form.show()
    form.activateWindow()
    if not QTest.qWaitForWindowActive(form, 1000):
        form.close()
        pytest.skip("창 활성화 안 됨 (offscreen)")
    QTest.keyClick(form, Qt.Key_J, Qt.AltModifier)
    QTest.keyClick(form, Qt.Key_1, Qt.AltModifier)
    assert form.selected.category == "just_chatting" and form.selected.confidence == "certain"
    form.close()


# ---------------------------------------------------------------------------- 진짜 store 와
def test_with_real_store_roundtrip(qapp, tmp_path):
    from tests import test_store as ts
    ts.FakeLock.held = set()
    root = ts.project.create(str(tmp_path / "한글 문서"), "합성 방송", 123, "시험용 목적", "L01", "0.0.1",
                             ts.NOW, ts.PID, ts.SALT, guide=ts._guide(), machine4="m3fa").root
    st = ts.open_store(root)
    assert not st.read_only
    dur = DUR

    def retract(eid, reason, into):
        return st.retract(eid, reason, merged_into=into)

    form = SegmentForm(st.append_label, lambda: None, retract=retract)
    form.load_context(duration_ms=dur, records=[], session_id=st.session.session_id, new_id=idgen())
    label(form)
    form.add_boundary(100_000)
    label(form, "game", "certain", "01:40 게임")
    form.set_active_chat(True)
    form.add_boundary(110_000)
    label(form, "just_chatting", "certain", "01:50 잡담")
    assert len(form.save()) == 3
    form.select_at(105_000)
    assert form.accept_suggestion()              # 10초 게임 구간을 앞에 합침
    out = form.save()
    assert [r["kind"] for r in out] == ["retract", "segment"]     # 철회 먼저(중간에 멈춰도 겹침 없음)
    want = [(s["start_ms"], s["end_ms"], s["category"]) for s in form.segments_for_timeline()]
    st.close()

    # 다시 열기 → 현재 유효 구간 → 같은 상태
    st2 = ts.open_store(root)
    eff = st2.effective()
    heads = list(eff.items("segment"))
    assert len(heads) == 2
    form2 = SegmentForm(st2.append_label, lambda: None,
                        retract=lambda e, r, i: st2.retract(e, r, merged_into=i))
    form2.load_context(duration_ms=dur, records=heads, session_id=st2.session.session_id, new_id=idgen(0xD))
    assert [(s["start_ms"], s["end_ms"], s["category"]) for s in form2.segments_for_timeline()] == want
    assert not form2.has_unsaved
    form2.select_at(0)
    form2.set_confidence("probably")
    assert len(form2.save()) == 1                # supersedes 가 store 의 현재 판과 맞아 받아들여짐
    st2.close()
