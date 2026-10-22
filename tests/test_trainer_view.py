"""스트리머 훈련 화면 시험 (app/ui/trainer_view.py · app/ui/train_compare_view.py, 08 D-18 6·7·8, 18 6-2).

화면 없이(QT_QPA_PLATFORM=offscreen) **합성** 채팅·합성 기준만 쓴다. 실제 채팅·닉네임·전사문은 쓰지 않는다.
플레이어는 가짜(호출 기록), 채팅 표·타임라인은 작업대와 같은 진짜 부품이다. 시범 프로젝트 폴더는 열지 않는다.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from app.ui import train_compare_view as tcv  # noqa: E402
from app.ui import trainer_view as tv  # noqa: E402

TEXT_MARK = "합성본문"          # 기록·신호에 나오면 안 되는 본문 표시
NICK_MARK = "합성닉***"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture(autouse=True)
def _close_views():
    yield
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


# ---------------------------------------------------------------------------- 가짜 부품·합성 자료
class FakePlayer(QWidget):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playingChanged = Signal(bool)
    errorOccurred = Signal(str)
    fileLoaded = Signal()
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.calls = []
        self.muted = None
        self.playing = False
        self.pos = 0
        self.closed = False

    def load(self, path):
        self.calls.append(("load", os.path.basename(path)))
        return True

    def play(self):
        self.calls.append(("play",))
        self.playing = True
        self.playingChanged.emit(True)

    def pause(self):
        self.calls.append(("pause",))
        if self.playing:
            self.playing = False
            self.playingChanged.emit(False)

    def toggle(self):
        self.calls.append(("toggle",))
        self.pause() if self.playing else self.play()

    def seek(self, file_ms, exact=True):
        self.calls.append(("seek", int(file_ms)))
        self.pos = int(file_ms)

    def position_ms(self):
        return self.pos

    def duration_ms(self):
        return 0

    def frame_step(self, n): pass
    def set_speed(self, x): pass
    def set_volume(self, v): self.calls.append(("volume", v))
    def set_ab_loop(self, a, b): pass

    def set_mute(self, m):
        self.calls.append(("mute", bool(m)))
        self.muted = bool(m)

    def close_player(self):
        self.closed = True

    # 시험 도우미: 영상 파일 시각으로 위치 신호
    def at(self, file_ms):
        self.pos = int(file_ms)
        self.positionChanged.emit(int(file_ms))


class Shift:
    """파일 시각 = VOD 시각 − 2000 (합성 보정)."""
    def to_file(self, vod):
        return vod - 2000

    def to_vod(self, f):
        return f + 2000


def cid(i):
    return "c%020x" % i


def messages(n=30, step=1000, start=0):
    out = []
    for i in range(n):
        out.append({"chat_id": cid(i), "t_ms": start + i * step, "viewer_id": "v%03d" % (i % 5),
                    "nickname_masked": NICK_MARK, "kind": "text", "role": "viewer",
                    "text": f"{TEXT_MARK} {i} {{:x_9{i % 3}:}}"})
    return out


SEG = (5_000, 25_000)
REF = [{"chat_id": cid(6), "reaction_vod_ms": 7_500},     # 원래 반응 (구간 안)
       {"chat_id": cid(10), "reaction_vod_ms": 12_000},
       {"chat_id": cid(15), "reaction_vod_ms": 16_000},
       {"chat_id": cid(2), "reaction_vod_ms": 3_000}]     # 구간 밖 — 견주기에서 빠진다


CALLS = []


def fake_compare(picks, refs, *, range_ms=None, chat_times=None, until_ms=None):
    """app.core.training.compare 와 같은 인자로 부르는 합성 계산 (dict 를 돌려준다 — 화면이 두 모양을 다 받는지)."""
    CALLS.append({"picks": dict(picks), "refs": [dict(r) for r in refs], "range_ms": range_ms, "until_ms": until_ms})
    a, b = range_ms
    ref = {r["chat_id"]: r["reaction_vod_ms"] for r in refs if r["chat_vod_ms"] is not None and a <= r["chat_vod_ms"] < b
           and (until_ms is None or r["chat_vod_ms"] <= until_ms)}
    same = [{"chat_id": c, "picked_vod_ms": picks[c], "reaction_vod_ms": ref[c], "delta_ms": picks[c] - ref[c]}
            for c in picks if c in ref]
    return {"same": same,
            "mine_only": [{"chat_id": c, "picked_vod_ms": t} for c, t in picks.items() if c not in ref],
            "orig_only": [{"chat_id": c, "reaction_vod_ms": t} for c, t in ref.items() if c not in picks]}


def make(qapp, *, reference=REF, compare=fake_compare, media="synthetic.mp4", **kw):
    v = tv.TrainerView(parts={"player": FakePlayer}, compare_fn=compare, clock=lambda: "2026-10-22T00:00:00.000Z")
    kw.setdefault("reference_hash", "h" * 64)
    ok = v.start(chat=messages(), segment=SEG, reference=reference, media_path=media, timemap=Shift(),
                 duration_ms=30_000, **kw)
    return v, ok


def no_private(events):
    s = repr(events)
    return TEXT_MARK not in s and NICK_MARK not in s and "x_9" not in s


# ---------------------------------------------------------------------------- 메뉴 켜기 (D-18 7)
def test_menu_state_pilot_and_reference():
    assert tv.training_menu_state("pilot-001", has_reference=True) == (False, tv.REASON_PILOT)
    assert tv.training_menu_state(r"C:\x\projects\pilot-abc", has_reference=True)[0] is False
    assert tv.training_menu_state("pilot-001", has_reference=True, pilot_locked=True) == (True, "")
    assert tv.training_menu_state("P-synth", has_reference=False) == (False, tv.REASON_NO_REFERENCE)
    assert tv.training_menu_state("P-synth", has_reference=True) == (True, "")
    assert tv.is_pilot_project("PILOT-2") and not tv.is_pilot_project("my-pilot") and not tv.is_pilot_project(None)


def test_first_segment():
    assert tv.first_segment([(9, 20), (3, 5), (1, 1)]) == (3, 5)
    assert tv.first_segment([]) is None and tv.first_segment([(4, 2)]) is None


def test_start_refuses_pilot_and_missing(qapp):
    called = []
    v, ok = make(qapp, project_name="pilot-xyz", reference=lambda: called.append(1) or REF)
    assert ok is False and v.stack.currentWidget() is v.blocked_label
    assert v.blocked_label.text() == tv.REASON_PILOT and not called
    assert not v.btn_compare.isEnabled() and v.events == []
    v2, ok2 = make(qapp, reference=None)
    assert ok2 is False and v2.blocked_label.text() == tv.REASON_NO_REFERENCE
    v3 = tv.TrainerView(parts={"player": FakePlayer})
    assert v3.start(chat=messages(), segments=[], reference=REF) is False


# ---------------------------------------------------------------------------- 열면 바로
def test_open_muted_autoplay_and_hidden(qapp):
    called = []
    v, ok = make(qapp, reference=lambda: (called.append(1), REF)[1])
    p = v.player
    assert ok and v.state() == "training"
    assert p.muted is True and v.is_muted()
    assert ("load", "synthetic.mp4") in p.calls and ("seek", SEG[0] - 2000) in p.calls   # 보정 적용
    assert ("play",) not in p.calls
    p.fileLoaded.emit()                      # 열리면 바로 재생
    assert p.calls[-1] == ("play",) and v.btn_play.text().startswith("❚❚")
    assert v.count_label.text() == "고른 수 0"
    # 입력 칸 없음, 정답·라벨·파형 없음
    assert not v.chat.search.isVisible() and v.chat.search.isHidden()
    assert v.chat.label_marks() == {}
    names = [n for n, _r in v.timeline.track_rects()]
    assert "waveform" not in names and "segments" not in names and "marks:train_orig" not in names
    assert called == []                      # 기준은 견주기 전에는 펼치지 않는다
    assert v.right.currentWidget() is v.chat
    ev = v.events[0]
    assert ev["kind"] == "training.start" and ev["range"] == {"start_ms": SEG[0], "end_ms": SEG[1]}
    assert ev["source"] == "replay" and ev["audio"] == "muted"


def test_buttons_have_shortcut_tooltips(qapp):
    v, _ = make(qapp)
    for b in v.findChildren(QPushButton):
        tip = b.toolTip()
        assert tip and "(" in tip and ")" in tip, b.text()


# ---------------------------------------------------------------------------- 고르기
def test_pick_unpick_and_events(qapp):
    v, _ = make(qapp)
    p = v.player
    p.at(10_500 - 2000)                       # VOD 10.5초
    assert v.position_vod() == 10_500 and v.chat.highlighted_chat_id() == cid(10)
    assert v.toggle_pick(cid(8)) == "pick"
    assert v.toggle_pick(cid(10)) == "pick"
    assert v.pick_count() == 2 and v.count_label.text() == "고른 수 2"
    assert v.chat.label_marks() == {cid(8): tv.PICK_MARK, cid(10): tv.PICK_MARK}
    assert [t for t, _k in v.timeline.markers("train_pick")] == [10_500, 10_500]
    assert v.toggle_pick(cid(8)) == "unpick"
    assert v.picks() == [{"chat_id": cid(10), "picked_vod_ms": 10_500}]
    kinds = [e["kind"] for e in v.events]
    assert kinds == ["training.start", "training.pick", "training.pick", "training.unpick"]
    pk = v.events[1]
    assert pk == {"kind": "training.pick", "chat_id": cid(8), "picked_vod_ms": 10_500,
                  "wall_at": "2026-10-22T00:00:00.000Z", "source": "replay"}
    assert v.events[3]["vod_ms"] == 10_500
    assert no_private(v.events)


def test_pick_rejects_future_and_outside(qapp):
    v, _ = make(qapp)
    v.player.at(8_000 - 2000)
    assert v.toggle_pick(cid(20)) is None and "아직" in v.msg.text()     # 20초 채팅은 아직
    assert v.toggle_pick(cid(9)) == "pick"                               # 9초: 허용 여유 1초 안
    assert v.toggle_pick(cid(2)) is None and "구간 밖" in v.msg.text()
    assert v.toggle_pick("") is None and v.toggle_pick("c-none") is None
    assert v.pick_count() == 1


def test_real_click_picks_once(qapp):
    v, _ = make(qapp)
    v.resize(1200, 700)
    v.show()
    QApplication.processEvents()
    v.player.at(12_000 - 2000)
    QApplication.processEvents()
    view = v.chat.view
    row = v.chat.model.view_row_of(v.chat.chat_data().index_of(cid(11)))
    view.scrollTo(v.chat.model.index(row, 0))
    QApplication.processEvents()
    rect = view.visualRect(v.chat.model.index(row, 3))
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert v.is_picked(cid(11))
    # 더블클릭 간격 안에 같은 채팅을 또 누르면 한 번으로 본다(더블클릭 = 고름 한 번)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert v.is_picked(cid(11))
    v._last_click = None                     # 간격이 지난 것으로 보고 다시 누름 = 취소
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert not v.is_picked(cid(11))


def test_space_toggles(qapp):
    v, _ = make(qapp)
    v.show()
    v.activateWindow()
    QApplication.processEvents()
    v.chat.view.setFocus()
    QApplication.processEvents()
    n = len(v.player.calls)
    QTest.keyClick(v.chat.view, Qt.Key_Space)
    QApplication.processEvents()
    if len(v.player.calls) == n:
        pytest.skip("offscreen 에서 QShortcut 이 키를 받지 못함")
    assert ("toggle",) in v.player.calls[n:]
    v.toggle_play()
    assert v.player.calls[-2:] in ([("toggle",), ("pause",)], [("toggle",), ("play",)])


# ---------------------------------------------------------------------------- 견주기
def test_segment_end_opens_compare(qapp):
    called = []

    def ref():
        called.append(1)
        return REF
    v, _ = make(qapp, reference=ref)
    v.player.at(7_000 - 2000)
    v.toggle_pick(cid(6))
    v.player.at(9_000 - 2000)
    v.toggle_pick(cid(9))
    v.player.at(SEG[1] - 2000)               # 구간 끝
    assert v.state() == "compare" and called == [1]
    assert ("pause",) in v.player.calls
    assert v.right.currentWidget() is v.compare_view and not v.btn_compare.isEnabled()
    nums = v.compare_view.summary()
    assert nums == {"same": 1, "mine_only": 1, "orig_only": 2, "reference": 3, "hit_rate": pytest.approx(1 / 3),
                    "median_delta_ms": -500}
    cats = {r.chat_id: r.category for r in v.compare_view.rows()}
    assert cats == {cid(6): "same", cid(9): "mine_only", cid(10): "orig_only", cid(15): "orig_only"}
    assert cid(2) not in cats                # 구간 밖 기준은 뺐다
    txt = v.compare_view.numbers_text()
    assert "같음 1" in txt and "33%" in txt and "-0.5초" in txt and "원래만 2" in txt
    # 본문은 화면 표에만, 이모티콘 코드는 짧게
    shown = [v.compare_view.table.item(i, tcv.COL_TEXT).text() for i in range(v.compare_view.table.rowCount())]
    assert all(TEXT_MARK in s and "[이모티콘]" in s for s in shown)
    cmp_ev = v.events[-1]
    assert cmp_ev["kind"] == "training.compare" and cmp_ev["same"] == 1 and cmp_ev["orig_only"] == 2
    assert cmp_ev["reference_sha256"] == "h" * 64 and cmp_ev["reference_count"] == 3
    assert cmp_ev["range"] == {"start_ms": SEG[0], "end_ms": SEG[1]}
    # compare 에 넘긴 것: 고른 dict · 채팅 시각을 채운 기준 · 구간
    last = CALLS[-1]
    assert last["picks"] == {cid(6): 7_000, cid(9): 9_000} and last["range_ms"] == SEG
    assert {r["chat_id"]: r["chat_vod_ms"] for r in last["refs"]}[cid(10)] == 10_000
    assert set(last["refs"][0]) == {"chat_id", "chat_vod_ms", "reaction_vod_ms", "grade"}
    assert no_private(v.events) and no_private(CALLS)
    # 이제 원래 반응 자리가 타임라인에 보인다
    assert sorted(t for t, _k in v.timeline.markers("train_orig")) == [7_500, 12_000, 16_000]
    # 견주기 뒤에는 더 고르지 않는다
    assert v.toggle_pick(cid(12)) is None
    v.close_view()
    assert v.events[-1] == {"kind": "training.end", "reason": "segment_end", "picks": 2}


def test_compare_button_and_listen(qapp):
    v, _ = make(qapp)
    v.player.at(11_000 - 2000)
    v.toggle_pick(cid(10))
    v.player.at(16_000 - 2000)               # c15 채팅이 나온 뒤 견주기 (안 나온 채팅은 견주기에 없다)
    assert v.show_compare() is True
    assert CALLS[-1]["until_ms"] == 16_000
    cv = v.compare_view
    rows = cv.rows()
    i = next(k for k, r in enumerate(rows) if r.chat_id == cid(15))
    p = v.player
    n = len(p.calls)
    cv.table.cellClicked.emit(i, 2)          # 행 한 번 누름
    a, b = tcv.listen_window(15_000, 16_000)
    assert p.calls[n:] == [("mute", False), ("seek", a - 2000), ("play",)]
    assert not v.is_muted() and v.listen_end() == b
    p.at(b - 2000)                            # 듣기 범위 끝에서 멈춘다
    assert p.calls[-1] == ("pause",) and v.listen_end() is None


def test_early_compare_counts_only_seen_chats(qapp):
    """구간 끝 전에 견주기: 아직 화면에 안 나온 채팅의 원래 반응은 '원래만'·분모·타임라인·N 에 넣지 않는다.
    기본 계산(app.core.training.compare)으로 — 재생 8초, c6 을 고르고 견주기 → 같음 1 · 원래만 0 · 맞힌 비율 100%."""
    pytest.importorskip("app.core.training")
    v = tv.TrainerView(parts={"player": FakePlayer})
    assert v.start(chat=messages(), segment=SEG, reference=REF, media_path="synthetic.mp4", timemap=Shift())
    v.player.at(8_000 - 2000)
    assert v.toggle_pick(cid(6)) == "pick"
    assert v.show_compare() is True
    nums = v.compare_view.summary()
    assert nums["same"] == 1 and nums["orig_only"] == 0 and nums["reference"] == 1
    assert nums["hit_rate"] == pytest.approx(1.0) and nums["median_delta_ms"] == 500
    assert v.compare_result().until_ms == 8_000
    assert {r.chat_id for r in v.compare_view.rows()} == {cid(6)}
    assert sorted(t for t, _k in v.timeline.markers("train_orig")) == [7_500]   # c10·c15 자리는 안 보인다
    assert v.compare_view.next_orig_only() is False
    ev = [e for e in v.events if e["kind"] == "training.compare"][-1]
    assert ev["until_ms"] == 8_000 and ev["orig_only"] == 0


def test_early_compare_after_seek_back_keeps_seen(qapp):
    """뒤로 옮긴 뒤 견주기: 이미 화면에 나왔던 채팅까지 본 것으로 친다(until_ms = 가장 늦게 본 시각)."""
    v, _ = make(qapp)
    v.player.at(16_000 - 2000)
    v.seek_vod(9_000)
    v.toggle_pick(cid(6))
    assert v.show_compare() is True
    assert CALLS[-1]["until_ms"] == 16_000
    assert {r.chat_id for r in v.compare_view.rows() if r.category == "orig_only"} == {cid(10), cid(15)}


def test_segment_end_compare_has_no_until(qapp):
    v, _ = make(qapp)
    v.player.at(SEG[1] - 2000)
    assert v.state() == "compare" and CALLS[-1]["until_ms"] is None
    assert [e for e in v.events if e["kind"] == "training.compare"][-1]["until_ms"] is None


def test_n_and_shift_n(qapp):
    v, _ = make(qapp)
    v.player.at(20_000 - 2000)
    v.toggle_pick(cid(10))
    v.show_compare()
    cv = v.compare_view
    order = [r.chat_id for r in cv.rows() if r.category == "orig_only"]
    assert order == [cid(6), cid(15)]
    got = []
    cv.listenRequested.connect(lambda c, a, b: got.append(c))
    assert cv.next_orig_only() and cv.next_orig_only()
    assert cv.next_orig_only() is False and "마지막" in cv.nav_msg.text()   # 끝에서 돌지 않는다(core 와 같음)
    assert got == [cid(6), cid(15)]
    assert cv.prev_orig_only() and got[-1] == cid(6)
    assert cv.prev_orig_only() is False and "첫" in cv.nav_msg.text()
    assert "Shift+N" in cv.prev_btn.toolTip() and "(N)" in cv.next_btn.toolTip()
    assert set(cv.shortcuts) == {"N", "Shift+N"}


def test_compare_module_missing_stays_training(qapp):
    def broken(_p, _r, **_k):
        raise ImportError("no module")
    v, _ = make(qapp, compare=broken)
    assert v.show_compare() is False and v.state() == "training"
    assert "app.core.training" in v.msg.text()


def test_real_core_compare_and_training_log(qapp, tmp_path):
    """기본 계산(app.core.training.compare)과 기록(app.training_store.TrainingLog)을 합성 자료·임시 폴더로 끝까지."""
    trn = pytest.importorskip("app.core.training")
    ts = pytest.importorskip("app.training_store")
    rs = pytest.importorskip("app.reference_set")
    refs = [trn.RefReaction(r["chat_id"], None, r["reaction_vod_ms"], "direct") for r in REF]
    sha = trn.reference_sha256(trn.REF_FINAL_SET, [trn.RefReaction(r["chat_id"], int(r["chat_id"][1:], 16) * 1000,
                                                                  r["reaction_vod_ms"], "direct") for r in REF])
    refset = rs.ReferenceSet(source=trn.REF_FINAL_SET, reactions=refs, ranges=[SEG, (40_000, 50_000)], sha256=sha)
    root = tmp_path / "P-synth"
    root.mkdir()
    log = ts.TrainingLog.create(str(root), "TST", check_pilot=False)
    v = tv.TrainerView(parts={"player": FakePlayer})        # compare_fn 기본 = core.training.compare
    assert v.start(chat=messages(), reference=refset, media_path=None, log=log,
                   project_id="00000000-0000-4000-8000-000000000001")
    assert v._segment == SEG                                # ReferenceSet.first_range
    v.seek_vod(7_200)
    assert v.toggle_pick(cid(6)) == "pick"
    v.seek_vod(9_100)
    assert v.toggle_pick(cid(9)) == "pick"
    assert v.toggle_pick(cid(9)) == "unpick"
    assert v.toggle_pick(cid(9)) == "pick"
    assert v.show_compare() is True
    res = v.compare_result()
    # 구간 끝 전(9.1초) 견주기: 아직 안 나온 c10·c15 의 원래 반응은 '원래만'·분모에 넣지 않는다 (until_ms)
    assert isinstance(res, trn.Comparison) and res.same == 1 and res.orig_only == 0 and res.mine_only == 1
    assert res.until_ms == 9_100
    nums = v.compare_view.summary()
    assert nums["reference"] == 1 and nums["median_delta_ms"] == -300 and nums["hit_rate"] == pytest.approx(1.0)
    assert {r.chat_id for r in v.compare_view.rows()} == {cid(6), cid(9)}
    v.close_view()
    recs, _info = ts.read_valid(log.path)
    kinds = [r["kind"] for r in recs]
    assert kinds == ["training.start", "training.pick", "training.pick", "training.unpick", "training.pick",
                     "training.compare", "training.end"]
    assert recs[-2]["body"]["reference_sha256"] == sha and recs[-1]["body"]["reason"] == "compare"
    assert recs[-2]["body"]["until_ms"] == 9_100 and recs[-2]["body"]["orig_only"] == 0
    assert trn.picks_from_events(recs) == {cid(6): 7_200, cid(9): 9_100}
    raw = open(log.path, encoding="utf-8").read()
    assert TEXT_MARK not in raw and NICK_MARK not in raw


def test_log_failure_blocks_pick_and_compare(qapp):
    class BadLog:
        def __init__(self):
            self.fail = False

        def start(self, **_k):
            return {}

        def toggle(self, *_a):
            if self.fail:
                raise OSError("disk")
            return {}

        def compare(self, *_a):
            raise OSError("disk")

        def end(self, *_a):
            raise OSError("disk")
    log = BadLog()
    v, ok = make(qapp, log=log, compare=lambda p, r, **k: type("C", (), {"summary": lambda s: {}, "rows": []})())
    assert ok
    v.seek_vod(9_000)
    log.fail = True
    assert v.toggle_pick(cid(8)) is None and v.pick_count() == 0 and "기록" in v.msg.text()
    log.fail = False
    assert v.toggle_pick(cid(8)) == "pick"
    assert v.show_compare() is False and v.state() == "training"     # 기록 못 하면 정답을 보이지 않는다
    v.close_view()                                                   # 끝 줄 실패는 닫기를 막지 않는다
    assert v.state() == "closed"


def test_start_log_failure_blocks(qapp):
    class NoStart:
        def start(self, **_k):
            raise PermissionError("blocked")
    v, ok = make(qapp, log=NoStart())
    assert ok is False and "훈련 기록" in v.blocked_label.text() and v.events == []


def test_no_media_chat_only(qapp):
    v = tv.TrainerView(parts={"player": FakePlayer}, compare_fn=fake_compare)
    assert v.start(chat=messages(), segment=SEG, reference=REF, media_path=None)
    assert "영상" in v.msg.text() and ("play",) not in v.player.calls
    v.toggle_play()
    assert ("toggle",) not in v.player.calls
    v.seek_vod(9_000)                         # 타임라인으로 옮겨 고를 수 있다
    assert v.toggle_pick(cid(9)) == "pick"


def test_close_emits_end(qapp):
    v, _ = make(qapp)
    v.toggle_pick(cid(5))
    v.close_view()
    v.close_view()
    assert v.player.closed and v.state() == "closed"
    ends = [e for e in v.events if e["kind"] == "training.end"]
    assert ends == [{"kind": "training.end", "reason": "closed", "picks": 1}]


# ---------------------------------------------------------------------------- 견주기 순수 도우미
def test_rows_and_summary_pure():
    res = {"same": [{"chat_id": "a", "picked_vod_ms": 1_000, "reaction_vod_ms": 1_400}],
           "mine_only": ["b"],
           "orig_only": [{"chat_id": "c", "reaction_vod_ms": 5_000}, {"chat_id": "a"}],   # 겹침은 앞 칸만
           "hit_rate": 0.25}
    times = {"a": 900, "b": 3_000, "c": 4_000}
    rows = tcv.rows_from_result(res, times.get)
    assert [(r.chat_id, r.category) for r in rows] == [("a", "same"), ("b", "mine_only"), ("c", "orig_only")]
    assert rows[0].delta_ms == -400 and rows[1].reaction_ms is None and rows[2].picked_ms is None
    nums = tcv.summary_numbers(res, rows)
    assert nums["hit_rate"] == 0.25 and nums["reference"] == 2 and nums["median_delta_ms"] == -400
    assert tcv.summary_numbers(None, [])["hit_rate"] is None
    assert tcv.listen_window(None, None) is None
    assert tcv.listen_window(1_000, None) == (0, 9_000)
    assert tcv.listen_window(10_000, 12_000) == (5_000, 20_000)
    assert "—" in tcv.fmt_summary(tcv.summary_numbers(None, []))


def test_compare_view_empty(qapp):
    cv = tcv.TrainCompareView()
    assert cv.table.rowCount() == 0 and not cv.next_btn.isEnabled()
    assert cv.next_orig_only() is False and cv.activate_row(0) is False
    assert cv.virtual_summary() is None and cv.virtual_label.isHidden()


# ---------------------------------------------------------------------------- 가상 채팅 섞기 (H-4, 20 5장)
V_TEXT = "합성가상본문"
# 합성 시나리오 줄 (mix 가 돌려줄 가상 줄 모양). 이름은 시나리오의 가림 이름 또는 없음(→ 가명)
V_LINES = [
    {"source": "retrieved", "line_id": "s0001", "show_vod_ms": 6_500, "text": V_TEXT + " 가", "display_mask": "하**",
     "nickname_masked": NICK_MARK, "viewer_id": "v001"},          # 원 채팅 이름·viewer_id 는 버려야 한다
    {"source": "generated", "line_id": "s0002", "show_vod_ms": 9_500, "text": V_TEXT + " {:zz_test_01:}",
     "display_mask": "두*"},
    {"source": "generated", "line_id": "s0003", "show_vod_ms": 20_500, "text": V_TEXT + " 다"},
    {"source": "retrieved", "line_id": "s0004", "show_vod_ms": 27_000, "text": V_TEXT + " 라", "display_mask": "세**"},
]
MIX_CALLS = []


def fake_mix(real, scenario):
    """core.scenario 의 섞기와 같은 인자(real, scenario) — 실제 줄 + 가상 줄을 시각 순으로."""
    MIX_CALLS.append((len(real), scenario))
    return sorted([*real, *[dict(x) for x in V_LINES]], key=lambda r: r.get("t_ms", r.get("show_vod_ms")))


SCEN = {"scenario_id": "SC-20261022-abcd", "scenario_sha256": "b" * 64}


def make_v(qapp, *, mix=fake_mix, scenario=SCEN, compare=fake_compare, **kw):
    v = tv.TrainerView(parts={"player": FakePlayer}, compare_fn=compare, mix_fn=mix,
                       clock=lambda: "2026-10-22T00:00:00.000Z")
    kw.setdefault("reference_hash", "h" * 64)
    ok = v.start(chat=messages(), segment=SEG, reference=REF, media_path="synthetic.mp4", timemap=Shift(),
                 duration_ms=30_000, scenario=scenario, **kw)
    return v, ok


def _nick_of(v, chat_id):
    d = v.chat.chat_data()
    return d.nicks[d.index_of(chat_id)]


def test_virtual_mixed_into_chat_with_marks(qapp):
    v, ok = make_v(qapp)
    # 가상 채팅을 섞으면 실제 채팅은 훈련 구간 [5초, 25초) 안 20줄만 싣는다(구간 밖 원 채팅과 본문이 겹치지 않게)
    assert ok and v.virtual_line_count() == 4 and v.chat.total_count() == 24
    assert _nick_of(v, "v:s0001") == "하** · 가져옴" and _nick_of(v, "v:s0002") == "두* · 생성"
    assert _nick_of(v, "v:s0003") == "시청자3 · 생성"             # 가림 이름이 없으면 가명 (가상 이름 차례 3)
    d = v.chat.chat_data()
    assert all(d.vnos[d.index_of(f"v:s000{i}")] > 0 for i in range(1, 5))   # 가명 번호 (실제 viewer_id 아님)
    assert NICK_MARK not in [d.nicks[d.index_of(f"v:s000{i}")] for i in range(1, 5)]
    ev = v.events[0]
    assert ev["scenario_id"] == SCEN["scenario_id"] and ev["scenario_sha256"] == SCEN["scenario_sha256"]
    assert ev["virtual_lines"] == 4 and ev["source"] == "replay"
    # 흐르는 채팅: 아직 안 온 가상 채팅도 보이지 않는다
    v.player.at(7_000 - 2000)
    vis = {v.chat.model.data(v.chat.model.index(r, 0), Qt.UserRole) for r in range(v.chat.row_count())}
    assert "v:s0001" in vis and "v:s0002" not in vis


def test_virtual_marks_hidden_option(qapp):
    v, ok = make_v(qapp, show_virtual_marks=False)
    assert ok and _nick_of(v, "v:s0001") == "하**" and _nick_of(v, "v:s0002") == "두*"


def test_virtual_pick_counts_separately(qapp):
    # 같은 실제 고르기를 시나리오 없이 / 있이 — 견주기 점수는 같아야 한다 (20 10장 H-4)
    base, _ = make(qapp)
    base.player.at(7_000 - 2000)
    base.toggle_pick(cid(6))
    base.player.at(10_000 - 2000)
    base.toggle_pick(cid(9))
    base.player.at(SEG[1] - 2000)
    base_nums = base.compare_view.summary()
    base_last = dict(CALLS[-1])

    v, _ = make_v(qapp)
    v.player.at(7_000 - 2000)
    assert v.toggle_pick(cid(6)) == "pick"
    assert v.toggle_pick("v:s0001") == "pick"                         # 가상 채팅 한 번 누름 = 고름
    assert v.toggle_pick("v:s0002") is None and "아직" in v.msg.text()
    v.player.at(10_000 - 2000)
    assert v.toggle_pick(cid(9)) == "pick"
    assert v.toggle_pick("v:s0002") == "pick"
    assert v.toggle_pick("v:s0002") == "unpick"                       # 다시 누름 = 취소
    assert v.toggle_pick("v:s0002") == "pick"
    assert v.toggle_pick("v:s0004") is None and "구간 밖" in v.msg.text()
    assert v.toggle_pick("v:nope") is None
    assert v.pick_count() == 4 and v.count_label.text() == "고른 수 4"
    assert v.chat.label_marks() == {cid(6): tv.PICK_MARK, cid(9): tv.PICK_MARK, "v:s0001": tv.PICK_MARK,
                                    "v:s0002": tv.PICK_MARK}
    assert v.picks() == [{"chat_id": cid(6), "picked_vod_ms": 7_000}, {"chat_id": cid(9), "picked_vod_ms": 10_000}]
    assert v.virtual_picks() == [{"line_id": "s0001", "source": "retrieved", "picked_vod_ms": 7_000},
                                 {"line_id": "s0002", "source": "generated", "picked_vod_ms": 10_000}]
    vp = [e for e in v.events if e["kind"] == "training.pick" and "line_id" in e]
    assert vp[0] == {"kind": "training.pick", "line_id": "s0001", "picked_vod_ms": 7_000,
                     "wall_at": "2026-10-22T00:00:00.000Z", "source": "retrieved"}
    assert all("chat_id" not in e for e in vp)
    un = [e for e in v.events if e["kind"] == "training.unpick"]
    assert un == [{"kind": "training.unpick", "line_id": "s0002", "source": "generated", "vod_ms": 10_000,
                   "wall_at": "2026-10-22T00:00:00.000Z"}]
    v.player.at(SEG[1] - 2000)                                           # 구간 끝 → 견주기
    assert v.state() == "compare"
    # 점수 계산에는 실제 고르기만 넘어간다 → 시나리오 없을 때와 같은 숫자
    assert CALLS[-1]["picks"] == base_last["picks"] == {cid(6): 7_000, cid(9): 10_000}
    assert v.compare_view.summary() == base_nums
    vs = v.compare_view.virtual_summary()
    assert vs["shown"] == 3 and vs["picked"] == 2                       # s0004 는 구간 밖
    assert vs["by_source"]["generated"] == {"shown": 2, "picked": 1}
    assert v.compare_view.virtual_text().startswith("가상 채팅 3개 가운데 2개 고름 (연습용, 점수 없음)")
    assert not v.compare_view.virtual_label.isHidden()
    rows = v.compare_view.rows()
    vr = [r for r in rows if r.category == tcv.CAT_VIRTUAL]
    assert [(r.chat_id, r.source, r.reaction_ms) for r in vr] == [("v:s0001", "retrieved", None),
                                                                  ("v:s0002", "generated", None)]
    assert rows[-2:] == vr                                               # 점수 행 아래에 따로
    # 견주기에서는 가상 표시를 드러낸다
    cats = [v.compare_view.table.item(i, tcv.COL_CAT).text() for i in range(len(rows))]
    assert cats[-2:] == ["가상 고름 · 가져옴", "가상 고름 · 생성"]
    cmp_ev = v.events[-1]
    assert cmp_ev["kind"] == "training.compare" and cmp_ev["virtual_shown"] == 3 and cmp_ev["virtual_picked"] == 2
    assert cmp_ev["same"] == 1
    # 원래 반응 자리 표시·N 은 가상 행과 상관없다
    assert sorted(t for t, _k in v.timeline.markers("train_orig")) == [7_500, 12_000, 16_000]
    i = len(rows) - 1
    p = v.player
    n = len(p.calls)
    v.compare_view.table.cellClicked.emit(i, 0)                         # 가상 행 누름 = 그 시각 앞뒤 듣기
    a, _b = tcv.listen_window(9_500, None)
    assert p.calls[n:] == [("mute", False), ("seek", a - 2000), ("play",)]
    v.close_view()
    assert v.events[-1] == {"kind": "training.end", "reason": "segment_end", "picks": 2, "virtual_picks": 2}
    assert no_private(v.events) and V_TEXT not in repr(v.events) and "zz_test" not in repr(v.events)
    assert "하**" not in repr(v.events) and "두*" not in repr(v.events)


def test_virtual_real_click_picks_once(qapp):
    """가상 채팅도 표에서 한 번 누름 = 고름 (D-16 클릭 1)."""
    v, _ = make_v(qapp)
    v.resize(1200, 700)
    v.show()
    QApplication.processEvents()
    v.player.at(10_000 - 2000)
    QApplication.processEvents()
    view = v.chat.view
    row = v.chat.model.view_row_of(v.chat.chat_data().index_of("v:s0002"))
    view.scrollTo(v.chat.model.index(row, 0))
    QApplication.processEvents()
    rect = view.visualRect(v.chat.model.index(row, 3))
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert v.is_picked("v:s0002") and v.pick_count() == 1


def test_mix_failure_or_empty_blocks_before_log(qapp):
    class Log:
        def __init__(self):
            self.calls = []

        def start(self, **k):
            self.calls.append(k)

    def broken(_real, _scn):
        raise ValueError("깨진 시나리오")
    log = Log()
    v, ok = make_v(qapp, mix=broken, log=log)
    assert ok is False and "가상 채팅을 섞지 못했습니다" in v.blocked_label.text()
    assert log.calls == [] and v.events == []                          # 노출로 세지 않는다
    v2, ok2 = make_v(qapp, mix=lambda real, _s: list(real), log=log)  # 가상 줄 0개
    assert ok2 is False and v2.blocked_label.text() == tv.REASON_NO_SCENARIO and log.calls == []
    # 시범 프로젝트면 섞기 전에 막는다 (시나리오를 읽지도 않음)
    n = len(MIX_CALLS)
    v3, ok3 = make_v(qapp, project_name="pilot-xyz")
    assert ok3 is False and v3.blocked_label.text() == tv.REASON_PILOT and len(MIX_CALLS) == n


def test_no_scenario_is_unchanged(qapp):
    v, ok = make(qapp)
    assert ok and v.virtual_line_count() == 0
    assert "scenario_id" not in v.events[0] and "virtual_lines" not in v.events[0]
    v.player.at(SEG[1] - 2000)
    assert v.compare_view.virtual_summary() is None and v.compare_view.virtual_label.isHidden()
    assert "virtual_shown" not in v.events[-1]
    assert all(r.category != tcv.CAT_VIRTUAL for r in v.compare_view.rows())


def test_virtual_with_real_core_and_log(qapp, tmp_path):
    """기본 섞기(core.scenario) + 기본 계산 + 실제 TrainingLog(임시 폴더): 기록 파일이 스키마를 통과하고
    가상 고르기는 line_id·source 로 남고(20 5-3 선택 칸), 가상 채팅 본문·이름·화면 id('v:')는 파일에 남지 않는다."""
    scn = pytest.importorskip("app.core.scenario")
    if not hasattr(scn, "merge_for_display") and not hasattr(scn, "mix"):
        pytest.skip("core.scenario 섞기 함수 없음")
    trn = pytest.importorskip("app.core.training")
    ts = pytest.importorskip("app.training_store")
    src = {"chat_id": cid(99), "t_ms": 99_000, "kind": "text", "nickname_masked": NICK_MARK, "text": V_TEXT + " 원"}
    recs = [{"kind": "scenario.header", "scenario_id": "SC-20261022-abcd", "range": {"vod_from_ms": SEG[0],
                                                                                     "vod_to_ms": SEG[1]}},
            {"kind": "scenario.chat", "line_id": "s0001", "source": "retrieved", "show_vod_ms": 6_500,
             "from_project_id": "p0", "from_chat_id": cid(99), "from_t_ms": 99_000, "display_mask": "하**"},
            {"kind": "scenario.chat", "line_id": "s0002", "source": "generated", "show_vod_ms": 8_500,
             "gen_id": "g0001", "text": V_TEXT + " {:zz_test_01:}", "display_mask": "두*"},
            {"kind": "scenario.end", "lines": 2, "lines_sha256": "0" * 64}]
    root = tmp_path / "P-synth"
    root.mkdir()
    log = ts.TrainingLog.create(str(root), "TST", check_pilot=False)
    rs = pytest.importorskip("app.reference_set")
    v = tv.TrainerView(parts={"player": FakePlayer})
    refs = [trn.RefReaction(r["chat_id"], None, r["reaction_vod_ms"], "direct") for r in REF]
    refset = rs.ReferenceSet(source=trn.REF_FINAL_SET, reactions=refs, ranges=[SEG], sha256="a" * 64)
    ok = v.start(chat=messages(), segment=SEG, reference=refset, media_path=None, log=log,
                 project_id="00000000-0000-4000-8000-000000000001",
                 scenario={"records": recs, "resolve": lambda p, c: src if c == cid(99) else None})
    assert ok and v.virtual_line_count() == 2
    assert v.events[0]["scenario_id"] == "SC-20261022-abcd"
    v.seek_vod(7_200)
    assert v.toggle_pick(cid(6)) == "pick"
    assert v.toggle_pick("v:s0001") == "pick"
    v.seek_vod(9_100)
    assert v.toggle_pick("v:s0002") == "pick"
    assert v.show_compare() is True
    res = v.compare_result()
    assert res.same == 1 and res.mine_only == 0                      # 가상 고르기는 '나만' 에 들어가지 않는다
    assert v.virtual_result().numbers() == {"virtual_shown": 2, "virtual_picked": 2}
    v.close_view()
    recs_out, info = ts.read_valid(log.path)
    assert info.get("corrupt", 0) == 0
    assert [r["kind"] for r in recs_out] == ["training.start", "training.pick", "training.pick", "training.pick",
                                             "training.compare", "training.end"]
    assert all(ts.check_record(r) == [] for r in recs_out)
    # 20 5-3 선택 칸: start 의 시나리오, 가상 pick 은 line_id·source, compare·end 는 건수만
    assert recs_out[0]["body"]["scenario_id"] == "SC-20261022-abcd" and recs_out[0]["body"]["virtual_lines"] == 2
    assert [r["body"].get("line_id") for r in recs_out[1:4]] == [None, "s0001", "s0002"]
    assert [r["body"]["source"] for r in recs_out[1:4]] == ["replay", "retrieved", "generated"]
    assert recs_out[4]["body"]["virtual_shown"] == 2 and recs_out[4]["body"]["virtual_picked"] == 2
    assert recs_out[4]["body"]["same"] == 1 and recs_out[4]["body"]["mine_only"] == 0
    assert recs_out[5]["body"]["picks"] == 1 and recs_out[5]["body"]["virtual_picks"] == 2
    raw = open(log.path, encoding="utf-8").read()
    for s in (V_TEXT, NICK_MARK, TEXT_MARK, "하**", "두*", "v:"):
        assert s not in raw
    assert not (root / "messages.jsonl").exists()                     # 실제 채팅 파일에 쓰지 않는다


def test_virtual_compare_fits_1366(qapp):
    v, _ = make_v(qapp)
    v.resize(1366, 768)
    v.show()
    QApplication.processEvents()
    v.player.at(10_000 - 2000)
    v.toggle_pick("v:s0002")
    v.player.at(SEG[1] - 2000)
    QApplication.processEvents()
    cv = v.compare_view
    assert v.width() <= 1366 and v.height() <= 768
    g = cv.virtual_label.geometry()
    assert g.right() <= cv.width() and g.bottom() <= cv.height() and cv.virtual_label.isVisible()


def test_virtual_pure_helpers():
    vs = {"shown": 3, "picked": 1, "by_source": {"retrieved": {"shown": 1, "picked": 0},
                                                 "generated": {"shown": 2, "picked": 1}},
          "picks": [{"vid": "v:s0002", "source": "generated", "show_vod_ms": 9_500, "picked_vod_ms": 10_000},
                    {"vid": "v:s0002"}]}
    rows = tcv.virtual_rows(vs)
    assert rows == [tcv.CompareRow(tcv.CAT_VIRTUAL, "v:s0002", 9_500, 10_000, None, None, "generated")]
    vn = tcv.virtual_numbers(vs)
    assert vn["shown"] == 3 and vn["picked"] == 1
    assert tcv.fmt_virtual(vn) == "가상 채팅 3개 가운데 1개 고름 (연습용, 점수 없음) · 가져옴 0/1 · 생성 1/2"
    assert tcv.virtual_numbers(None) is None and tcv.fmt_virtual(None) == ""
    # 점수 숫자에는 가상 행이 들어가지 않는다
    assert tcv.summary_numbers(None, rows) == tcv.summary_numbers(None, [])


# ---------------------------------------------------------------------------- 고친 것 재현 (검토 의견)
def test_early_compare_virtual_shown_not_below_picked(qapp):
    """재생 위치 VOD 6000 에서 1초 여유 안의 가상 줄(6500)을 고르고 바로 견주기: 가상 채팅 shown ≥ picked
    (전에는 shown 0 · picked 1). 사람 클릭 2(가상 채팅 1 + 견주기 1)."""
    v, ok = make_v(qapp)
    assert ok
    v.player.at(4_000)                                                 # 보정 Shift → VOD 6000
    assert v.position_vod() == 6_000
    assert v.toggle_pick("v:s0001") == "pick"
    assert v.show_compare() is True
    vs = v.virtual_result()
    assert vs.picked == 1 and vs.shown == 1 and vs.by_source["retrieved"] == {"shown": 1, "picked": 1}
    assert v.compare_view.virtual_text().startswith("가상 채팅 1개 가운데 1개 고름")
    cmp_ev = v.events[-1]
    assert cmp_ev["kind"] == "training.compare" and cmp_ev["virtual_shown"] >= cmp_ev["virtual_picked"] == 1
    assert cmp_ev["until_ms"] == 6_500                                 # 고른 줄은 본 것으로 친다


def test_early_compare_real_pick_within_tolerance_is_same(qapp):
    """실제 채팅도 같다: VOD 5500 에서 6000 의 채팅(원래 반응 있음)을 고르고 바로 견주기 → '같음'
    (전에는 until 5500 이 그 원래 반응을 빼 '나만' 이 됐다)."""
    v, ok = make(qapp)
    assert ok
    v.player.at(5_500 - 2000)
    assert v.toggle_pick(cid(6)) == "pick"
    assert v.show_compare() is True
    assert CALLS[-1]["until_ms"] == 6_000
    nums = v.compare_view.summary()
    assert nums["same"] == 1 and nums["mine_only"] == 0


def test_virtual_mix_clips_real_chat_to_segment(qapp):
    """retrieved 줄의 원 채팅(같은 방송, 구간 밖)이 같은 표에 원 가림 닉네임으로 보이지 않는다 — 기본 섞기
    (core.scenario, clip_to_range) + 화면 자르기. 견주기 뒤(다 보이기)에도 같은 본문의 실제 줄은 0개."""
    pytest.importorskip("app.core.scenario")
    real = messages()
    src = real[2]                                                      # t 2000: 구간(5000~25000) 앞
    recs = [{"kind": "scenario.header", "scenario_id": "SC-20261022-abcd", "range": {"vod_from_ms": SEG[0],
                                                                                     "vod_to_ms": SEG[1]}},
            {"kind": "scenario.chat", "line_id": "s0001", "source": "retrieved", "show_vod_ms": 6_500,
             "from_project_id": "p0", "from_chat_id": src["chat_id"], "from_t_ms": src["t_ms"], "display_mask": "하**"},
            {"kind": "scenario.end", "lines": 1, "lines_sha256": "0" * 64}]
    v = tv.TrainerView(parts={"player": FakePlayer}, compare_fn=fake_compare)
    ok = v.start(chat=real, segment=SEG, reference=REF, media_path=None,
                 scenario={"records": recs, "resolve": lambda p, c: src if c == src["chat_id"] else None})
    assert ok and v.virtual_line_count() == 1

    def rows(d):
        return [(d.chat_ids[i], int(d.times[i]), d.texts[i]) for i in range(d.n)]
    d = v.chat.chat_data()
    real_rows = [r for r in rows(d) if not r[0].startswith("v:")]
    virt_rows = [r for r in rows(d) if r[0].startswith("v:")]
    assert len(real_rows) == 20 and all(SEG[0] <= t < SEG[1] for _c, t, _x in real_rows)
    assert src["chat_id"] not in d.chat_ids
    assert {x for _c, _t, x in virt_rows} & {x for _c, _t, x in real_rows} == set()
    v.seek_vod(SEG[0])
    assert v.show_compare() is True
    shown = {v.chat.model.data(v.chat.model.index(r, 0), Qt.UserRole) for r in range(v.chat.row_count())}
    assert src["chat_id"] not in shown and "v:s0001" in shown


def test_virtual_mix_drops_line_with_same_text_as_real(qapp):
    """한 번 더 막기: 남은 실제 줄과 본문이 같은 가상 줄은 뺀다. 가상 줄만 돌려주는 섞기(실제 줄을 split_mixed 가
    앞에 더함)도 구간 밖 실제 줄은 싣지 않는다."""
    real = messages()
    same_text = real[10]["text"]                                       # 구간 안 실제 줄과 같은 본문
    lines = [dict(V_LINES[0]), {"source": "retrieved", "line_id": "s0009", "show_vod_ms": 8_000,
                                "text": "  " + same_text.upper() + " ", "display_mask": "세**"}]
    v = tv.TrainerView(parts={"player": FakePlayer}, compare_fn=fake_compare,
                       mix_fn=lambda _real, _s: [dict(x) for x in lines])
    ok = v.start(chat=real, segment=SEG, reference=REF, media_path=None, scenario=SCEN)
    assert ok and v.virtual_line_count() == 1 and "v:s0009" not in v._vlines
    d = v.chat.chat_data()
    assert d.n == 21 and all(SEG[0] <= int(d.times[i]) < SEG[1] for i in range(d.n))
    assert v.events[0]["virtual_lines"] == 1
