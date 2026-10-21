"""반응 편집기 빠른 입력 (08 D-16) — 화면 없이, 합성 데이터만.

단추마다 저장된 레코드가 label-event 스키마(그대로, D-14 7)를 통과하고 기대한 등급·증거를 갖는지,
단추를 누르기 전에는 등급이 골라져 있지 않은지(D-16 6), 7-2 두 번 듣기가 저장을 막지 않는지(D-16 4)를 본다.
저장은 진짜 ProjectStore(임시 한글 폴더의 합성 프로젝트)로 보낸다. 실제 시범 데이터는 건드리지 않는다.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import project, session, store  # noqa: E402
from app.core import rules  # noqa: E402
from app.core.timecode import parse_timestamps  # noqa: E402
from app.ui.chat_panel import ChatData  # noqa: E402
from app.ui.forms import reaction_editor as rx  # noqa: E402
from app.ui.forms.reaction_editor import QUICK_END_MS, QUICK_KEYS, QUICK_PRESETS, ReactionEditor  # noqa: E402
from app.ui.review_player import ReviewPlayer  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 22, 6, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-666666666666"
MID = "aaaaaaaa-0000-4000-8000-000000000002"

with open(os.path.join(ROOT, "assets", "guide-config.v0.1.json"), encoding="utf-8") as _f:
    VALUES = json.load(_f)["values"]
with open(os.path.join(ROOT, "schemas", "label-event.schema.json"), encoding="utf-8") as _f:
    SCHEMA_ROOT = json.load(_f)
SCHEMA = store.LabelSchema(SCHEMA_ROOT)     # 라벨 기록 검사기(app/core/validate 는 project.json 용이라 store 의 것을 쓴다)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


class FakeLock:
    held: set = set()

    def __init__(self, path):
        self.path = path
        self.locked = False

    def setStaleLockTime(self, ms):
        pass

    def tryLock(self, timeout=0):
        if self.path in FakeLock.held:
            return False
        FakeLock.held.add(self.path)
        self.locked = True
        return True

    def unlock(self):
        if self.locked:
            FakeLock.held.discard(self.path)
            self.locked = False

    def getLockInfo(self):
        return (1, "SYNTH", "chatcoach")


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        self.t += timedelta(milliseconds=10)
        return self.t


class FakePlayer(QObject):
    positionChanged = Signal(int)
    playingChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.pos = 0
        self.playing = False

    def position_ms(self):
        return self.pos

    def is_playing(self):
        return self.playing

    def speed(self):
        return 1.0

    def volume(self):
        return 100

    def play(self):
        self.playing = True
        self.playingChanged.emit(True)

    def pause(self):
        self.playing = False
        self.playingChanged.emit(False)

    def seek(self, ms, exact=True):
        self.pos = int(ms)
        self.positionChanged.emit(self.pos)

    def set_speed(self, x):
        pass

    def set_ab_loop(self, a, b):
        pass

    def show_text(self, text, duration_ms=2000):
        pass


def cid(n):
    return "c" + f"{n:020x}"


def make_chats():
    """합성 채팅: 100초부터 2초마다 일반 채팅 30건. 본문·닉네임은 합성."""
    return [{"chat_id": cid(n + 1), "t_ms": 100_000 + n * 2_000, "kind": "text", "viewer_id": f"v{(n % 5) + 1:03d}",
             "nickname_masked": "시**", "text": f"합성 채팅 {n + 1}", "role": "viewer"} for n in range(30)]


@pytest.fixture
def env(tmp_path, qapp):
    FakeLock.held = set()
    base = tmp_path / "한글 문서" / "빠른 입력"
    p = project.create(str(base), "합성 방송", 123, "시험용", "L01", "0.0.1", NOW, PID, bytes(range(32)),
                       guide=project.GuideSource("0.1", b"# guide\n", VALUES), machine4="m3fa")
    ctx = session.SessionContext("L01", "primary", "L01-P-20261022-ab12", session.machine4(MID), machine_id=MID,
                                 session_created_at="2026-10-22T06:00:00+09:00")
    st = store.ProjectStore.open(p.root, ctx, FakeLock, clock=Clock(), app_version="0.0.1")
    assert not st.read_only, st.read_only_reasons
    segs = [st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 300_000, "category": "just_chatting",
                                                           "confidence": "certain", "rationale": "00:00 합성"}})]
    saved, ops = [], []

    def save(draft):
        rec = st.append_label(draft)
        saved.append(rec)
        return rec

    def write_ops(kind, body):
        rec = st.append_ops(kind, body)
        ops.append(rec)
        return rec

    pos = {"v": 115_000}
    review = ReviewPlayer(FakePlayer())
    ed = ReactionEditor(save, lambda: None, ops=write_ops)
    ed.load_context(chats=ChatData(make_chats()), segments=segs, thresholds=VALUES, session_kind="primary",
                    phase="reactions", position_fn=lambda: pos["v"], review=review)
    ed.resize(900, 1200)
    e = type("Env", (), {})()
    e.store, e.ed, e.saved, e.ops, e.pos, e.segs = st, ed, saved, ops, pos, segs
    yield e
    ed.close_editor()
    ed.deleteLater()
    st.close()


def _valid(rec):
    """스키마(그대로) 검사. store 도 쓰기 전에 같은 검사를 한다."""
    assert SCHEMA.check(rec) == [], SCHEMA.check(rec)


def _press(env, key, chat=cid(5), reaction_ms=114_000, pos=115_000, *, no_similar=None):
    env.pos["v"] = pos
    env.ed.open_for(chat, reaction_ms)
    if no_similar is not None:
        env.ed.similar_box.setCurrentIndex(rx._find_data(env.ed.similar_box, no_similar))
    env.pos["v"] = pos + 7_000          # 연 뒤에 재생 위치가 움직여도 발화 시작은 '연 순간' 위치
    return env.ed.quick_input(key)


def _label(key):
    return next(p.label for p in QUICK_PRESETS if p.key == key)


# ---------------------------------------------------------------------------- 단추마다

@pytest.mark.parametrize("key,ev_key", [("nickname", "nickname_called"), ("repeat", "text_repeated"),
                                        ("reference", "direct_reference")])
def test_strong_buttons_save_direct(env, key, ev_key):
    res = _press(env, key)
    assert res is not None, env.ed.msg.text()
    r = res["reaction"]
    b = r["body"]
    assert b["result"] == "graded" and b["grade"] == "direct"
    assert b["grade_vs_conditions"] == "consistent" and "direct" in b["eligible_grades"]
    assert b["confidence"] == "certain"
    assert b["rationale"] == f"{_label(key)} 00:01:55"           # 멈춘 자리(=반응 지점)
    assert parse_timestamps(b["rationale"]) == [115_000]
    assert b["reaction_time_ms"] == 115_000
    assert len(b["candidates"]) == 1
    ev = b["candidates"][0]["evidence"]
    assert ev[ev_key]["present"] is True and ev[ev_key]["at_ms"] == 115_000
    assert ev["within_time_window"]["present"] is True       # 채팅 108초 → 발화 115초: 시간 창 안(앱 계산)
    (u,) = res["utterances"]
    assert b["linked_utterance_id"] == u["entity_id"] == b["candidates"][0]["utterance_id"]
    ub = u["body"]
    assert (ub["start_ms"], ub["end_ms"]) == (115_000, 115_000 + QUICK_END_MS)
    assert ub["heard_text"] == _label(key) and ub["heard_text_source"] == "typed"
    for rec in res["written"]:
        _valid(rec)
    # 7-2 두 번 듣기는 권장: 저장은 됐고 미완료 표시만
    assert res["complete"] is False and "recheck_missing" in res["warnings"]
    assert env.ed.last_quick == key


def test_answer_defaults_to_likely(env):
    """'질문에 대답' 은 라벨러가 이 채팅을 대답 대상으로 고른 것: 비슷한 채팅을 체크하지 않았으면 '없음' 으로 보고 높음."""
    res = _press(env, "answer")
    b = res["reaction"]["body"]
    assert b["grade"] == "likely" and b["grade_vs_conditions"] == "consistent"
    assert b["no_similar_chat_nearby"] is True
    ev = b["candidates"][0]["evidence"]
    assert ev["answer_form"]["present"] and ev["within_time_window"]["present"]
    assert b["linked_utterance_id"] == res["utterances"][0]["entity_id"]
    for rec in res["written"]:
        _valid(rec)


def test_answer_with_no_similar_is_likely(env):
    res = _press(env, "answer", no_similar=True)
    b = res["reaction"]["body"]
    assert b["grade"] == "likely" and b["grade_vs_conditions"] == "consistent"
    assert b["no_similar_chat_nearby"] is True
    assert b["linked_utterance_id"] == res["utterances"][0]["entity_id"]
    for rec in res["written"]:
        _valid(rec)


def test_answer_outside_time_window_falls_back(env):
    """발화가 시간 창(20초) 밖이면 중간 증거가 1개라 높음이 안 된다 → 관련성만 있음."""
    res = _press(env, "answer", chat=cid(1), reaction_ms=140_000, pos=140_000, no_similar=True)
    b = res["reaction"]["body"]
    assert b["candidates"][0]["evidence"]["within_time_window"]["present"] is False
    assert b["grade"] == "related_only"


def test_topic_button(env):
    res = _press(env, "topic")
    b = res["reaction"]["body"]
    assert b["grade"] == "related_only" and b["grade_vs_conditions"] == "consistent"
    assert b["candidates"][0]["evidence"]["same_topic"]["present"] is True
    for rec in res["written"]:
        _valid(rec)


def test_unknown_button(env):
    res = _press(env, "unknown")
    b = res["reaction"]["body"]
    assert b["grade"] == "unclear" and b["unclear_cause"] == "multiple_similar_chats"
    assert b["grade_vs_conditions"] == "consistent" and b["confidence"] == "certain"
    ev = b["candidates"][0]["evidence"]
    assert not any(v["present"] for k, v in ev.items() if k != rules.TIME_KEY)
    assert res["utterances"][0]["body"]["heard_text"] == _label("unknown")
    for rec in res["written"]:
        _valid(rec)


# ---------------------------------------------------------------------------- 등급을 미리 고르지 않음 · 막힘

def test_no_grade_preselected_before_click(env):
    env.ed.open_for(cid(5), 114_000)
    assert env.ed.grade() is None
    assert not any(b.isChecked() for b in env.ed.grade_buttons.values())
    assert env.ed._confidence is None and env.ed.rationale.text() == ""
    assert not any(v["present"] for k, v in env.ed.evidence.value().items() if k != rules.TIME_KEY)
    assert env.saved == []
    # 고치기로 연 미확인 반응도 같다: 빠른 입력 단추가 등급을 골라 두지 않는다
    assert env.ed.last_quick is None
    for p in QUICK_PRESETS:
        assert not env.ed.quick_buttons[p.key].isCheckable()


def test_no_target_does_not_save(env):
    env.ed.open_for(None, 114_000)
    assert env.ed.quick_input("nickname") is None
    assert env.saved == [] and env.ed.grade() is None
    assert "대상 채팅" in env.ed.msg.text()


def test_user_set_times_are_kept(env):
    env.pos["v"] = 115_000
    env.ed.open_for(cid(5), 114_000)
    env.ed.utt_start.setText("0:01:56.000")
    env.ed.utt_end.setText("0:02:00.500")
    env.ed._on_utt_times()
    env.ed.heard.setText("합성으로 들은 말")
    env.ed._on_heard_edited("합성으로 들은 말")
    res = env.ed.quick_input("nickname")
    ub = res["utterances"][0]["body"]
    assert (ub["start_ms"], ub["end_ms"]) == (116_000, 120_500)
    assert ub["heard_text"] == "합성으로 들은 말"


def test_existing_reaction_reopened_and_edited(env):
    """빠른 입력으로 저장한 반응을 다시 열면 자세히 입력으로 고칠 수 있다(D-16 5)."""
    res = _press(env, "topic")
    r = res["reaction"]
    utt = {u["entity_id"]: u for u in res["utterances"]}
    env.ed.open_existing(r, utterances=utt)
    assert env.ed.grade() == "related_only"
    res2 = env.ed.quick_input("nickname")
    b2 = res2["reaction"]["body"]
    assert b2["grade"] == "direct" and res2["reaction"]["entity_id"] == r["entity_id"]
    assert res2["reaction"]["supersedes"] == r["id"]
    assert b2["rationale"].startswith(_label("nickname"))   # 앞선 빠른 입력 근거는 바꿔 쓴다
    assert res2["utterances"][0]["body"]["heard_text"] == _label("nickname")
    for rec in res2["written"]:
        _valid(rec)


def test_recheck_does_not_block_save(env):
    """D-16 4: 직접 반응을 한 번도 안 들어도 저장된다(미완료 표시·경고만)."""
    res = _press(env, "reference")
    assert res is not None and res["complete"] is False
    codes = [(o.get("body") or {}).get("code") for o in env.ops]
    assert "recheck_missing" in codes
    assert not env.ed.is_open()


def test_open_pos_overrides_current_position(env):
    """작업대가 채팅 더블클릭으로 영상을 옮긴 뒤 열어도, 넘긴 '멈춘 자리' 가 발화 시작이 된다(D-16 2)."""
    env.pos["v"] = 98_000                 # 옮긴 뒤 위치(채팅 − 10초)
    env.ed.open_for(cid(5), 108_000, open_pos_ms=113_500)
    assert env.ed.open_position() == 113_500
    res = env.ed.quick_input("repeat")
    ub = res["utterances"][0]["body"]
    assert (ub["start_ms"], ub["end_ms"]) == (113_500, 116_500)
    assert res["reaction"]["body"]["grade"] == "direct"
    assert "repeat_before_chat" not in res["warnings"]


# ---------------------------------------------------------------------------- '딸깍' 화면 (접힌 자세히 입력 · Enter)

def test_click_only_layout_by_default(env):
    env.ed.show()
    env.ed.open_for(cid(5), 114_000)
    assert env.ed.detail.isHidden() and env.ed.detail_buttons.isHidden()       # 자세히 입력은 접혀 있다
    assert not env.ed.detail_expanded()
    for b in env.ed.quick_buttons.values():
        assert not b.isHidden()
    assert not env.ed.quick_cancel.isHidden()
    assert "합성 채팅 5" in env.ed.quick_target.text()
    assert env.ed.quick_buttons[rx.QUICK_DEFAULT].isDefault()                  # 기본 단추가 눈에 띈다
    assert rx.QUICK_DEFAULT == "repeat"


def test_enter_saves_default_and_status(env):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    got = []
    env.ed.finished.connect(got.append)
    env.ed.show()
    env.pos["v"] = 115_000
    env.ed.open_for(cid(5), 114_000)
    env.ed.setFocus()
    QTest.keyClick(env.ed, Qt.Key_Return)
    assert got, env.ed.quick_error.text()
    res = got[0]
    assert res["reaction"]["body"]["grade"] == "direct"
    assert res["reaction"]["body"]["candidates"][0]["evidence"]["text_repeated"]["present"] is True
    assert res["status_text"] == "저장: 직접 반응 (채팅 읽고 대답)"
    assert not env.ed.is_open()                                                # 저장하고 닫힌다


def test_enter_in_detail_text_field_does_not_save(env):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    env.ed.show()
    env.ed.open_for(cid(5), 114_000)
    env.ed.set_detail_expanded(True)
    env.ed.heard.setFocus()
    QTest.keyClick(env.ed.heard, Qt.Key_Return)
    assert env.saved == [] and env.ed.is_open()


def test_esc_closes_without_label(env):
    env.ed.open_for(cid(5), 114_000)
    env.ed.quick_cancel.click()
    assert not env.ed.is_open() and env.saved == []


def test_failed_quick_save_expands_with_plain_sentence(env):
    env.ed.show()
    env.ed.open_for(cid(5), 114_000)
    env.ed.utt_end.setText("0:01:00.000")             # 끝이 시작보다 앞섬(라벨러가 잘못 고침) → 저장 막힘
    env.ed._on_utt_times()
    assert env.ed.quick_input("nickname") is None and env.saved == []
    assert env.ed.detail_expanded() and not env.ed.detail.isHidden()
    t = env.ed.quick_error.text()
    assert not env.ed.quick_error.isHidden() and t.startswith("저장하지 못했습니다.") and "✗" not in t
    assert "\n" not in t
    # 자동 펼침은 기억하지 않는다: 다시 열면 접혀 있다
    env.ed.cancel()
    env.ed.open_for(cid(6), 114_000)
    assert not env.ed.detail_expanded() and env.ed.quick_error.isHidden()


@pytest.mark.parametrize("expanded", [False, True])
def test_fits_right_pane_471(qapp, expanded):
    """1366×768 편집기 칸(471px)에서 접힌·펼친 화면 모두 잘리거나 밖으로 나가지 않는다(test_c6_ui 와 같은 셈)."""
    from tests.test_c6_ui import _fit
    e = ReactionEditor(save=lambda d: d)
    e.set_detail_expanded(expanded)
    e.resize(471, 700)
    e.show()
    assert _fit(e, qapp) == {"hscroll": False, "clipped": 0, "outside": 0}
    e.close()
    e.deleteLater()


def test_detail_pref_remembered(env):
    env.ed.open_for(cid(5), 114_000)
    env.ed.detail_toggle.click()                       # 라벨러가 직접 펼침 → 기억
    assert env.ed.detail_expanded()
    env.ed.cancel()
    env.ed.open_for(cid(6), 114_000)
    assert env.ed.detail_expanded()
    env.ed.detail_toggle.click()                       # 다시 접어 두기(다른 시험에 남기지 않게)
    env.ed.cancel()
    env.ed.open_for(cid(6), 114_000)
    assert not env.ed.detail_expanded()


# ---------------------------------------------------------------------------- 작업대 (② 더블클릭 · ③ 표본 M)

def test_workbench_double_click_and_sample_flow(qapp, tmp_path):
    from app.core import phase as ph
    from tests.test_workbench_labeling import STEP, make_project, make_two_segments, open_wb
    from tests.test_workbench_labeling import cid as wcid
    proj = make_project(tmp_path)
    wb = open_wb(qapp, proj)
    try:
        make_two_segments(wb)
        wb.mark_phase_done()
        assert wb.set_mode(ph.REACTIONS)
        paused = 12 * STEP + 4_000
        wb.seek_vod(paused)                                   # 대답을 듣고 멈춘 자리
        wb.chat.chatActivated.emit(wcid(12), 12 * STEP)       # 채팅 더블클릭(영상은 채팅 앞으로 옮겨진다)
        ed = wb.editor
        assert wb.is_editing() and ed.grade() is None and ed.open_position() == paused
        assert set(ed.quick_buttons) == {p.key for p in QUICK_PRESETS}
        res = ed.quick_input("nickname")
        assert res is not None, ed.msg.text()
        assert res["utterances"][0]["body"]["start_ms"] == paused
        assert res["reaction"]["body"]["grade"] == "direct" and not wb.is_editing()
        for rec in res["written"]:
            _valid(rec)
        assert wb.mark_phase_done() is not None
        # ③ 표본: '반응 있음 → 편집기 M' 도 같은 편집기라 빠른 입력 단추가 있다
        assert wb.set_mode(ph.SAMPLES)
        wb.draw_sample(sync=True)
        q = wb.queue
        while q.current_item() is not None and q.state_of(q.current_item()["chat_id"]) != "pending":
            q.next()
        it = q.current_item()
        assert it is not None
        wb.seek_vod(it["t_ms"] + 2_000)
        wb.mark_reaction()
        assert wb.is_editing() and wb.editor is ed and ed.grade() is None
        res = ed.quick_input("topic")
        assert res is not None, ed.msg.text()
        b = res["reaction"]["body"]
        assert b["entry"] == "from_sample" and b["grade"] == "related_only" and b["sample_ref"]
        assert q.state_of(it["chat_id"]) == "judged"
        for rec in res["written"]:
            _valid(rec)
    finally:
        wb.close_workbench()


# ---------------------------------------------------------------------------- 이미 매긴 채팅 = 초록 (자기 세션만)

def test_chat_panel_own_marks_green(qapp):
    from PySide6.QtCore import Qt
    from app.ui import chat_panel as cp
    p = cp.ChatPanel()
    p.set_messages(make_chats())
    assert p.set_label_marks({cid(3): "✓ 직접"}, own=True) == 1
    m = p.model
    row = m.view_row_of(2)                              # cid(3) = 원본 인덱스 2
    idx = m.index(row, cp.COL_KIND)
    assert m.data(idx, Qt.ItemDataRole.DisplayRole) == "✓ 직접"
    assert m.data(idx, Qt.ItemDataRole.BackgroundRole).color() == cp.COLOR_OWN_BG != cp.COLOR_LABEL_BG
    p.set_label_marks({cid(3): "1차 직접"})            # 분석 세션 표시는 보라 그대로
    assert m.data(idx, Qt.ItemDataRole.BackgroundRole).color() == cp.COLOR_LABEL_BG
    p.deleteLater()


def test_workbench_marks_own_labels_live_and_on_resume(qapp, tmp_path):
    from app.core import phase as ph
    from app.ui import workbench as wbm
    from tests import test_workbench_labeling as wl

    class MarkChat(wl.FakeChat):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.marks, self.own = {}, None

        def set_label_marks(self, marks, *, own=False):
            self.marks, self.own = dict(marks or {}), own
            return len(self.marks)

    parts = dict(wl.PARTS, chat=MarkChat)

    def open_marked():
        out = wl.pv.open_checked(proj.root)
        wb = wbm.Workbench(parts=parts, session=wl.session(), store_options={"app_version": "0.0.0"})
        wb.resize(1366, 768)
        wb.show()
        wb.open_project(out, sync=True)
        qapp.processEvents()
        return wb

    proj = wl.make_project(tmp_path)
    wb = open_marked()
    try:
        wl.make_two_segments(wb)
        wb.mark_phase_done()
        assert wb.set_mode(ph.REACTIONS)
        assert wb.chat.marks == {}
        wb.seek_vod(12 * wl.STEP + 4_000)
        wb.chat.chatActivated.emit(wl.cid(12), 12 * wl.STEP)
        assert wb.editor.quick_input("repeat") is not None
        assert wb.chat.marks == {wl.cid(12): "✓ 직접"} and wb.chat.own is True        # 저장하자마자 초록
        wb.seek_vod(14 * wl.STEP + 2_000)
        wb.chat.chatActivated.emit(wl.cid(14), 14 * wl.STEP)
        assert wb.editor.quick_input("topic") is not None
        assert wb.chat.marks == {wl.cid(12): "✓ 직접", wl.cid(14): "✓ 관련"}
        wb.undo_shortcut()                                                         # Ctrl+Z: 직전 반응 철회
        assert wb.chat.marks == {wl.cid(12): "✓ 직접"}
        # 이미 매긴 채팅을 두 번 누르면 새 라벨이 아니라 그 라벨을 고친다(D-10 4)
        wb.chat.chatActivated.emit(wl.cid(12), 12 * wl.STEP)
        assert wb.is_editing() and wb.editor._existing is not None
        wb.editor.cancel()
    finally:
        wb.close_workbench()
    wb = open_marked()                                                             # 다시 열기(이어 하기)
    try:
        assert wb.chat.marks == {wl.cid(12): "✓ 직접"} and wb.chat.own is True
    finally:
        wb.close_workbench()


# ---------------------------------------------------------------------------- 단축키

def test_quick_shortcuts_unique_and_free(env):
    keys = [QKeySequence(k).toString() for k in QUICK_KEYS]
    assert len(set(keys)) == len(QUICK_PRESETS) == 6
    others = [s.key().toString() for s in env.ed._shortcuts if s.key().toString() not in keys]
    assert not set(keys) & set(others)
    # 작업대 shortcut_map 의 키(02 7-6)와 겹치지 않는다
    wb_keys = {"Space", "K", "Left", "Right", ",", ".", "Shift+Left", "Shift+Right", "Ctrl+Left", "Ctrl+Right", "J",
               "L", "[", "]", "R", "M", "B", "Ctrl+Z", "Ctrl+=", "Ctrl++", "Ctrl+-", "Ctrl+0", "Ctrl+F"}
    assert not {QKeySequence(k).toString() for k in wb_keys} & set(keys)


def test_shortcut_activation_saves(env):
    env.pos["v"] = 115_000
    env.ed.open_for(cid(5), 114_000)
    sc = next(s for s in env.ed._shortcuts if s.key().toString() == QKeySequence(QUICK_KEYS[0]).toString())
    sc.activated.emit()
    assert env.ed.last_quick == "nickname"
    assert any(r.get("kind") == "reaction" and r["body"]["grade"] == "direct" for r in env.saved)
