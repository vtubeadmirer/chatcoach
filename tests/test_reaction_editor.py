"""app/ui/forms/reaction_editor.py · evidence_widget.py · continuation_editor.py — 반응 편집기 W2 (화면 없이, 합성 데이터만).

저장은 진짜 ProjectStore(임시 한글 폴더의 합성 프로젝트)로 보내 스키마·판 연결 검사를 함께 받는다.
들은 범위는 대역 플레이어 + 진짜 ReviewPlayer 로 흉내 낸다.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import project, session, store  # noqa: E402
from app.core import rules  # noqa: E402
from app.ui.chat_panel import ChatData  # noqa: E402
from app.ui.forms import continuation_editor as ce  # noqa: E402
from app.ui.forms.evidence_widget import EvidenceWidget, empty_evidence, normalize_evidence  # noqa: E402
from app.ui.forms.reaction_editor import ReactionEditor, find_segment  # noqa: E402
from app.ui.review_player import ReviewPlayer  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 6, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-555555555555"
MID = "aaaaaaaa-0000-4000-8000-000000000001"

with open(os.path.join(ROOT, "assets", "guide-config.v0.1.json"), encoding="utf-8") as _f:
    VALUES = json.load(_f)["values"]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


# ---------------------------------------------------------------------------- 대역

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
        self.ab = (None, None)
        self.osd = []

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
        self.ab = (a, b)

    def show_text(self, text, duration_ms=2000):
        self.osd.append(text)

    def run(self, start, end, step=200):
        self.play()
        self.seek(start)
        t = start
        while t < end:
            t = min(end, t + step)
            self.pos = t
            self.positionChanged.emit(t)
        self.pause()


def cid(n):
    return "c" + f"{n:020x}"


def make_chats():
    """합성 채팅: 100초부터 2초마다 일반 채팅 30건, 그 사이에 후원 1건. 본문·닉네임은 합성."""
    recs = []
    for n in range(30):
        recs.append({"chat_id": cid(n + 1), "t_ms": 100_000 + n * 2_000, "kind": "text",
                     "viewer_id": f"v{(n % 5) + 1:03d}", "nickname_masked": "시**",
                     "text": f"합성 채팅 {n + 1}", "role": "viewer"})
    recs.append({"chat_id": cid(999), "t_ms": 101_000, "kind": "donation", "viewer_id": "v009",
                 "nickname_masked": "시**", "text": "합성 후원", "role": "viewer"})
    recs.append({"chat_id": cid(500), "t_ms": 400_000, "kind": "text", "viewer_id": "v001",
                 "nickname_masked": "시**", "text": "게임 구간 합성 채팅", "role": "viewer"})
    return recs


@pytest.fixture
def env(tmp_path, qapp):
    """합성 프로젝트 + store + 구간 2개(저스트채팅 0~300초, 게임 300~600초) + 편집기."""
    FakeLock.held = set()
    base = tmp_path / "한글 문서" / "프로젝트"
    p = project.create(str(base), "합성 방송", 123, "시험용", "L01", "0.0.1", NOW, PID, bytes(range(32)),
                       guide=project.GuideSource("0.1", b"# guide\n", VALUES), machine4="m3fa")
    ctx = session.SessionContext("L01", "primary", "L01-P-20261021-ab12", session.machine4(MID), machine_id=MID,
                                 session_created_at="2026-10-21T06:00:00+09:00")
    st = store.ProjectStore.open(p.root, ctx, FakeLock, clock=Clock(), app_version="0.0.1")
    assert not st.read_only, st.read_only_reasons
    segs = [st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 300_000, "category": "just_chatting",
                                                           "confidence": "certain", "rationale": "00:00 합성"}}),
            st.append_label({"kind": "segment", "body": {"start_ms": 300_000, "end_ms": 600_000, "category": "game",
                                                           "confidence": "certain", "rationale": "05:00 합성",
                                                           "active_chat": False}})]
    saved, ops, exposed = [], [], []

    def save(draft):
        rec = st.append_label(draft)
        saved.append(rec)
        return rec

    def write_ops(kind, body):
        rec = st.append_ops(kind, body)
        ops.append(rec)
        return rec

    cancelled = []
    pos = {"v": 112_000}
    player = FakePlayer()
    review = ReviewPlayer(player)
    ed = ReactionEditor(save, lambda: cancelled.append(1), ops=write_ops,
                        exposed=lambda what, c, e: exposed.append((what, c, e)))
    ed.load_context(chats=ChatData(make_chats()), segments=segs, thresholds=VALUES, session_kind="primary",
                    phase="reactions", position_fn=lambda: pos["v"], review=review,
                    asr_lookup=lambda a, b: "합성 자동 인식 문장")
    ed.resize(900, 1200)
    e = type("Env", (), {})()
    e.store, e.ed, e.saved, e.ops, e.exposed, e.cancelled = st, ed, saved, ops, exposed, cancelled
    e.pos, e.player, e.review, e.segs, e.root = pos, player, review, segs, p.root
    yield e
    ed.close_editor()
    ed.deleteLater()
    st.close()


def fill_candidate(ed, start, end, text, **ev):
    ed.utt_start.setText(f"{start / 1000:.3f}")
    ed.utt_end.setText(f"{end / 1000:.3f}")
    ed._on_utt_times()
    ed.heard.setText(text)
    ed._on_heard_edited(text)
    for k, on in ev.items():
        ed.evidence.boxes[k].setChecked(on)


def listen(env, times=1):
    """확인 범위를 소리 켜고 1배속으로 times 번 끝까지 듣는다(회차마다 범위 반복을 새로 켬)."""
    for _ in range(times):
        env.ed.listen_again()
        a, b = env.review.window()
        env.player.run(a, b)
        env.review.stop_loop()


# ---------------------------------------------------------------------------- 증거 칸·이어짐 (부품)

def test_evidence_widget_value_and_time_window(qapp):
    pos = {"v": 5_000}
    w = EvidenceWidget(position_fn=lambda: pos["v"])
    v = w.value()
    assert set(v) == set(rules.EVIDENCE_KEYS) and not any(x["present"] for x in v.values())
    # 강·중·약 묶음은 시작값 강도에서
    assert set(w.group_labels) == {"strong", "medium", "weak"}
    w.boxes["nickname_called"].setChecked(True)
    w.notes["nickname_called"].setText("합성 메모")
    v = w.value()
    assert v["nickname_called"] == {"present": True, "at_ms": 5_000, "note": "합성 메모"}
    # 시간 창: 자동 체크, 손대면 고정
    assert w.set_time_window(100_000, 111_000) is True
    assert w.boxes["within_time_window"].isChecked()
    assert w.value()["within_time_window"]["at_ms"] == 111_000
    assert w.set_time_window(100_000, 125_000) is False
    assert not w.boxes["within_time_window"].isChecked()
    w.boxes["within_time_window"].setChecked(True)          # 라벨러가 바꿈
    assert w.time_manual()
    w.set_time_window(100_000, 130_000)
    assert w.boxes["within_time_window"].isChecked()        # 자동으로 덮어쓰지 않음
    assert "손으로" in w.time_info.text()
    assert w.counts() == (1, 1, 0)
    w.clear()
    assert w.value() == normalize_evidence(empty_evidence())
    w.deleteLater()


def test_truncate_span_and_hint():
    assert ce.truncate_span(100, 500, 300) == (100, 300, 500, True)
    assert ce.truncate_span(100, 200, 300) == (100, 200, None, False)
    assert ce.truncate_span(100, 500, None) == (100, 500, None, False)
    assert ce.continuation_hint(30_000, 0) == "short"
    assert ce.continuation_hint(60_000, 0) == "extended"
    assert ce.continuation_hint(10_000, 3) == "extended"
    assert ce.continuation_hint(None, 1) is None


def test_continuation_editor_standalone_save(qapp, env):
    rec = []
    c = ce.ContinuationEditor(save=lambda d: rec.append(d) or {"entity_id": "e", "id": "i", "body": d["body"]})
    c.load_context(chats=ChatData(make_chats()), segment_end_ms=300_000, thresholds=VALUES)
    c.open_for("12345678-0000-4000-8000-000000000001", 280_000, cid(1))
    assert c.save() is None and "종류" in c.msg.text()
    c.set_type("extended")
    c.start_edit.setText("280.0")
    c.end_edit.setText("340.0")
    c._on_span_edited()
    c.conf_box.setCurrentIndex(c.conf_box.findData("probably"))
    c.rationale.setText("04:40 합성 이어짐")
    out = c.save()
    assert out is not None
    b = rec[0]["body"]
    assert b["span_start_ms"] == 280_000 and b["span_end_ms"] == 300_000
    assert b["observed_end_ms"] == 340_000 and b["truncated_at_segment_end"] is True
    assert "잘림" in c.trunc_label.text()
    c.deleteLater()


# ---------------------------------------------------------------------------- 편집기: 블라인드·조건 충족

def test_open_does_not_preselect_grade_and_shows_conditions(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    assert ed.is_open() and ed.target_chat_id() == cid(1)
    assert ed.grade() is None and not any(b.isChecked() for b in ed.grade_buttons.values())
    assert not any(b.isChecked() for b in ed.conf_buttons.values())
    # 후보 채팅: −60초 ~ +5초, 후원은 목록에 없음
    ids = [ed.chat_list.item(i).data(Qt.UserRole) for i in range(ed.chat_list.count())]
    assert cid(999) not in ids and cid(1) in ids
    assert "후원" in ed.target_info.text()
    fill_candidate(ed, 110_000, 113_000, "합성 발화", nickname_called=True)
    gc = ed.conditions()
    assert "direct" in gc.eligible_grades
    assert "직접 반응 ✓" in ed.cond_label.text()
    assert ed.grade() is None      # 조건이 충족돼도 고르지 않는다
    # OSD 는 채팅 번호(시청자 번호·시각)만
    assert env.review.osd_text().startswith("판정 중 · 시청자")
    assert "합성 채팅" not in env.review.osd_text()


def test_editor_has_no_engine_or_other_session_inputs(env):
    """블라인드: 편집기 맥락에 엔진·AI·다른 세션 라벨을 넣는 길이 없다."""
    import inspect
    params = set(inspect.signature(ReactionEditor.load_context).parameters)
    assert not {p for p in params if any(w in p for w in ("engine", "ai", "other", "link", "suggest"))}
    with pytest.raises(TypeError):
        env.ed.load_context(engine_results=[])


def test_asr_reference_is_hidden_until_revealed_and_logged(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    assert "숨김" in ed.asr_label.text() and "합성 자동 인식" not in ed.asr_label.text()
    assert not ed.asr_show.isHidden()
    assert ed.paste_asr() is True                # 붙여 넣기는 보기를 거친다 → 열람 기록
    assert env.exposed == [("asr_text", cid(1), None)]
    assert ed.heard.text() == "합성 자동 인식 문장"
    assert ed._cands[0].heard_source == "asr_unedited"
    ed.heard.setText("합성 자동 인식 문장 고침")
    ed._on_heard_edited(ed.heard.text())
    assert ed._cands[0].heard_source == "asr_edited"
    # 세션 종류를 모르면 보이지 않는다
    ed.load_context(session_kind="adjudication")
    ed._session_kind = None
    assert ed.asr_policy() == "never" and ed.reveal_asr() is False


# ---------------------------------------------------------------------------- 저장 규칙

def _prepare_direct(ed):
    ed.open_for(cid(1), 112_000)
    fill_candidate(ed, 110_000, 113_000, "합성 발화 하나", nickname_called=True)
    ed.set_grade("direct")
    ed.set_confidence("certain")
    ed.rationale.setText("00:01:52 합성 호명")


def test_direct_without_strong_evidence_is_blocked(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    fill_candidate(ed, 110_000, 113_000, "합성 발화", answer_form=True, unique_content=True)
    ed.similar_box.setCurrentIndex(ed.similar_box.findData(True))
    ed.set_grade("direct")
    ed.set_confidence("certain")
    ed.rationale.setText("00:01:52 합성")
    ed.override.setText("사유를 적어도")
    v = ed.validate()
    assert any("강한 증거" in e for e in v.errors)
    assert ed.save() is None and env.saved == []
    assert not ed.save_btn.isEnabled()
    assert ed.is_open()


def test_direct_save_full_records_and_recheck_incomplete(env):
    ed = env.ed
    _prepare_direct(ed)
    listen(env, 1)
    ed.continuation.set_type("short")
    ed.continuation.start_edit.setText("112.0")
    ed.continuation.end_edit.setText("140.0")
    ed.continuation._on_span_edited()
    ed.continuation.conf_box.setCurrentIndex(ed.continuation.conf_box.findData("certain"))
    ed.continuation.rationale.setText("00:01:52 합성 이어짐")
    ed.continuation._on_change()
    res = ed.save()
    assert res is not None, ed.msg.text()
    kinds = [r["kind"] for r in env.saved]
    assert kinds == ["utterance", "reaction", "continuation", "video_check"]
    u, r, c, vc = env.saved
    assert u["body"]["heard_text_source"] == "typed" and u["body"]["segment_id"] == env.segs[0]["entity_id"]
    rb = r["body"]
    assert rb["result"] == "graded" and rb["grade"] == "direct"
    assert rb["grade_vs_conditions"] == "consistent" and "direct" in rb["eligible_grades"]
    assert rb["linked_utterance_id"] == u["entity_id"]
    assert rb["candidates"][0]["utterance_id"] == u["entity_id"]
    assert rb["candidates"][0]["evidence"]["nickname_called"]["present"] is True
    assert rb["first_visible_ms"] == 100_000 and rb["reaction_time_ms"] == 112_000
    assert rb["segment_id"] == env.segs[0]["entity_id"]
    assert rb["aids_used"] == ["grade_conditions"]
    assert c["body"]["reaction_entity_id"] == r["entity_id"] and c["body"]["type"] == "short"
    assert vc["body"]["check"] == "first" and vc["body"]["audio_heard"] is True
    assert vc["body"]["window_start_ms"] == 90_000 and vc["body"]["window_end_ms"] == 143_000
    assert vc["body"]["target_entity_id"] == r["entity_id"] and vc["body"]["target_version_id"] == r["id"]
    # 한 번만 들었으니 '완료' 아님 + 경고 기록
    assert res["complete"] is False and "recheck_missing" in res["warnings"]
    codes = [o["body"]["code"] for o in env.ops]
    assert "recheck_missing" in codes
    assert not ed.is_open()
    # 저장된 줄이 다시 읽어도 유효하다
    env.store.close()
    st2 = store.ProjectStore.open(env.root, env.store.session, FakeLock, clock=Clock(), app_version="0.0.1")
    eff = st2.effective()
    assert len(list(eff.items("reaction"))) == 1 and len(list(eff.items("video_check"))) == 1
    st2.close()
    env.store = st2


def test_two_listens_make_it_complete(env):
    ed = env.ed
    _prepare_direct(ed)
    listen(env, 2)
    res = ed.save()
    assert res is not None, ed.msg.text()
    assert res["complete"] is True and "recheck_missing" not in res["warnings"]
    checks = [r["body"]["check"] for r in env.saved if r["kind"] == "video_check"]
    assert checks == ["first", "recheck"]


def test_not_listening_warns_audio_not_heard(env):
    ed = env.ed
    _prepare_direct(ed)
    res = ed.save()
    assert res is not None
    assert "audio_not_heard" in res["warnings"] and res["video_checks"] == []
    assert res["complete"] is False


def test_deviation_requires_reason(env):
    ed = env.ed
    _prepare_direct(ed)
    ed.set_grade("related_only")          # 직접 조건이 충족됐는데 낮게 고름
    v = ed.validate()
    assert v.grade_vs == "deviates" and any("사유" in e for e in v.errors)
    assert ed.save() is None
    ed.override.setText("호명이 다른 채팅을 향함 (합성)")
    res = ed.save()
    assert res is not None, ed.msg.text()
    rb = res["reaction"]["body"]
    assert rb["grade_vs_conditions"] == "deviates" and rb["override_reason"].startswith("호명")
    assert rb["linked_utterance_id"] is None
    assert "grade_rule_override" in res["warnings"]


def test_likely_needs_no_similar_confirmation(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    fill_candidate(ed, 110_000, 113_000, "합성 답", answer_form=True, unique_content=True)
    assert "likely" not in ed.conditions().eligible_grades          # '비슷한 채팅 없음' 확인 전
    ed.similar_box.setCurrentIndex(ed.similar_box.findData(True))
    assert "likely" in ed.conditions().eligible_grades
    # 비슷한 채팅을 체크하면 '있음' 으로 바뀌고 높음이 막힌다
    for i in range(ed.chat_list.count()):
        it = ed.chat_list.item(i)
        if it.data(Qt.UserRole) == cid(2):
            it.setCheckState(Qt.Checked)
    assert ed.similar_box.currentData() is False
    gc = ed.conditions()
    assert "likely" not in gc.eligible_grades and "unclear" in gc.eligible_grades
    assert ed.similar_ids() == [cid(2)]


def test_time_window_alone_does_not_count(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    fill_candidate(ed, 110_000, 113_000, "합성")
    assert ed.evidence.boxes["within_time_window"].isChecked()     # 10초 뒤 → 자동 체크
    gc = ed.conditions()
    assert gc.eligible_grades == () and any(r.startswith("time_only") for r in gc.reasons)


def test_unconfirmed_saves_without_candidates(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    ed.set_grade("unconfirmed")
    ed.set_confidence("probably")
    ed.rationale.setText("00:01:52 후보 없음 (합성)")
    res = ed.save()
    assert res is not None, ed.msg.text()
    rb = res["reaction"]["body"]
    assert rb["result"] == "unconfirmed" and rb["grade"] is None and rb["candidates"] == []
    assert rb["grade_vs_conditions"] == "not_applicable" and rb["eligible_grades"] == []
    assert rb["reaction_time_ms"] is None and res["utterances"] == []


def test_unconfirmed_with_filled_candidate_is_blocked(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    fill_candidate(ed, 110_000, 113_000, "합성", same_topic=True)
    ed.set_grade("unconfirmed")
    ed.set_confidence("probably")
    ed.rationale.setText("00:01:52")
    assert any("반응 미확인" in e for e in ed.validate().errors)


def test_target_must_be_text_in_just_chatting(env):
    ed = env.ed
    ed.open_for(cid(500), 400_000)            # 게임 구간 채팅
    fill_candidate(ed, 400_000, 401_000, "합성", nickname_called=True)
    ed.set_grade("direct")
    ed.set_confidence("certain")
    ed.rationale.setText("06:40")
    assert any("저스트채팅" in e for e in ed.validate().errors)
    ed.open_for(cid(999), 101_500)            # 후원 채팅은 대상이 될 수 없다
    assert ed.target_chat_id() is None
    assert "일반 채팅이 아니" in ed.target_info.text()
    assert any("대상 채팅" in e for e in ed.validate().errors)


def test_unclear_needs_cause_and_multiple_candidates(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    ed.similar_box.setCurrentIndex(ed.similar_box.findData(True))
    fill_candidate(ed, 110_000, 113_000, "합성 하나", answer_form=True, unique_content=True)
    ed.add_candidate()
    fill_candidate(ed, 115_000, 118_000, "합성 둘", answer_form=True, unique_content=True)
    gc = ed.conditions()
    assert gc.tied and "unclear" in gc.eligible_grades
    ed.set_grade("unclear")
    ed.set_confidence("certain")
    ed.rationale.setText("00:01:50 합성 두 답")
    assert any("불명확 사유" in e for e in ed.validate().errors)
    ed.unclear_box.setCurrentIndex(ed.unclear_box.findData("multiple_utterances"))
    res = ed.save()
    assert res is not None, ed.msg.text()
    rb = res["reaction"]["body"]
    assert len(rb["candidates"]) == 2 and rb["unclear_cause"] == "multiple_utterances"
    assert len(res["utterances"]) == 2


def test_warnings_rationale_and_repeat_before_chat(env):
    ed = env.ed
    ed.open_for(cid(1), 112_000)
    fill_candidate(ed, 95_000, 99_000, "합성 앞선 말", text_repeated=True)
    ed.set_grade("direct")
    ed.set_confidence("probably")
    ed.rationale.setText("시각 없는 근거")
    codes = [c for c, _t in ed.validate().warnings]
    assert "rationale_without_time" in codes and "repeat_before_chat" in codes


def test_retry_after_failure_does_not_duplicate(env):
    ed = env.ed
    _prepare_direct(ed)
    orig = ed._save_cb
    calls = {"n": 0}

    def flaky(draft):
        calls["n"] += 1
        if draft["kind"] == "reaction" and calls["n"] < 3:
            raise store.WriteVerifyError("합성 실패")
        return orig(draft)
    ed._save_cb = flaky
    assert ed.save() is None and ed.is_open() and "저장 실패" in ed.msg.text()
    assert [r["kind"] for r in env.saved] == ["utterance"]
    res = ed.save()
    assert res is not None, ed.msg.text()
    assert [r["kind"] for r in env.saved] == ["utterance", "reaction"]   # 발화는 다시 쓰지 않음


def test_open_existing_recheck_keeps_first_judgment(env):
    ed = env.ed
    _prepare_direct(ed)
    listen(env, 1)
    res = ed.save()
    r1 = res["reaction"]
    utts = {u["entity_id"]: u for u in res["utterances"]}
    # 다시 열어 두 번째로 듣고 확신도를 바꾼다
    ed.open_existing(r1, utterances=utts, video_checks=res["video_checks"])
    assert ed.grade() == "direct" and ed._confidence == "certain"
    assert ed.heard.text() == "합성 발화 하나"
    listen(env, 1)
    ed.set_confidence("probably")
    res2 = ed.save()
    assert res2 is not None, ed.msg.text()
    r2 = res2["reaction"]
    assert r2["entity_id"] == r1["entity_id"] and r2["supersedes"] == r1["id"]
    fj = r2["body"]["first_judgment"]
    assert fj["version_id"] == r1["id"] and fj["confidence"] == "certain"
    assert r2["body"]["changed_on_recheck"] is True
    assert res2["utterances"] == [utts[r1["body"]["linked_utterance_id"]]]   # 같은 발화는 새 판을 만들지 않음
    assert [v["body"]["check"] for v in res2["video_checks"]] == ["recheck"]
    assert res2["complete"] is True


def test_cancel_and_shortcuts(env, qapp):
    ed = env.ed
    ed.show()
    qapp.processEvents()
    ed.open_for(cid(1), 112_000)
    ed.activateWindow()
    ed.chat_list.setFocus()
    qapp.processEvents()
    QTest.keyClick(ed.chat_list, Qt.Key_1, Qt.ControlModifier)
    QTest.keyClick(ed.chat_list, Qt.Key_2, Qt.AltModifier)
    if ed.grade() is None:
        pytest.skip("offscreen 에서 단축키가 창에 전달되지 않음")
    assert ed.grade() == "direct" and ed._confidence == "probably"
    QTest.keyClick(ed.chat_list, Qt.Key_5, Qt.ControlModifier)
    assert ed.grade() == "unconfirmed"
    # 수정자 없는 숫자는 텍스트 칸에서 글자다
    ed.rationale.setFocus()
    QTest.keyClick(ed.rationale, Qt.Key_1)
    assert ed.rationale.text().endswith("1") and ed.grade() == "unconfirmed"
    QTest.keyClick(ed.rationale, Qt.Key_T, Qt.ControlModifier)
    assert "00:01:52" in ed.rationale.text()
    QTest.keyClick(ed.chat_list, Qt.Key_Escape)
    assert env.cancelled == [1] and not ed.is_open()
    ed.hide()


def test_m_key_path_picks_target_from_list(env):
    """M 으로 열면(chat_id 없음) 대상이 비어 있고, 후보 채팅 목록에서 고른다. 시간 창도 그 채팅 기준으로 다시 잰다."""
    ed = env.ed
    ed.open_for(None, 112_000)
    assert ed.target_chat_id() is None
    assert any("대상 채팅" in e for e in ed.validate().errors)
    fill_candidate(ed, 113_000, 115_000, "합성")
    assert ed.set_target(cid(4)) is True            # 106초 채팅
    assert ed.target_chat_id() == cid(4)
    assert "7.0초" in ed.evidence.time_info.text()
    assert env.review.window() == (96_000, 145_000)


def test_find_segment():
    segs = [{"entity_id": "a", "body": {"start_ms": 0, "end_ms": 100}},
            {"entity_id": "b", "body": {"start_ms": 100, "end_ms": 200}}]
    assert find_segment(segs, 100)["entity_id"] == "b"
    assert find_segment(segs, 99)["entity_id"] == "a"
    assert find_segment(segs, 200) is None and find_segment(segs, None) is None
