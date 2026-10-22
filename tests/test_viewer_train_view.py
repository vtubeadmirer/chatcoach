"""시청자 훈련 화면 시험 (app/ui/viewer_train_view.py, 08 D-19 5·D-20 2·4·5, 21 6-1~6-3).

화면 없이(QT_QPA_PLATFORM=offscreen) **합성** 채팅·합성 연습 글(이모티콘 코드도 합성)만 쓴다. 채점기는 합성 함수로 바꿔
넣는다(app.core.viewer_score 담당과 따로 — 그 모듈이 없어도 이 시험은 돈다). 플레이어는 가짜, 채팅 표·타임라인은
작업대와 같은 진짜 부품. 기록 시험은 tmp_path 의 새 합성 프로젝트에만 쓴다. 시범 폴더는 열지 않는다.
"""
import json
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit, QWidget  # noqa: E402

from app.ui import viewer_train_view as vtv  # noqa: E402

TEXT_MARK = "합성본문"           # 실제 채팅 자리의 합성 본문
NICK_MARK = "합성닉***"
MINE = "합성연습글"              # 시청자가 쓴 연습 글 표시 — 기록 파일 말고는 어디에도 나오면 안 된다
WHY_MARK = "합성까닭"            # 채점기의 까닭 글 — 화면에만
PID = "00000000-0000-4000-8000-000000000001"
SEG = (5_000, 25_000)


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

    def set_volume(self, v):
        self.calls.append(("volume", v))

    def set_mute(self, m):
        self.calls.append(("mute", bool(m)))
        self.muted = bool(m)

    def close_player(self):
        self.closed = True

    def at(self, file_ms):
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
    return [{"chat_id": cid(i), "t_ms": start + i * step, "viewer_id": "v%03d" % (i % 5), "nickname_masked": NICK_MARK,
             "kind": "text", "role": "viewer", "text": f"{TEXT_MARK} {i} {{:zz_test_0{i % 3}:}}"} for i in range(n)]


class FakeScore:
    """합성 채점기 — score(text, context) -> dict (viewer_score 담당의 가정 모양)."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, ctx):
        self.calls.append((text, ctx))
        topic = "good" if "주제" in text else "weak"
        expand = "good" if "?" in text else ("ok" if len(text) > 8 else "weak")
        repeat = "weak" if text in ctx.get("my_previous", []) else "good"
        return {"version": "0.1",
                "topic": {"level": topic, "code": "topic_overlap", "reason": f"{WHY_MARK} 주제 낱말"},
                "expand": {"level": expand, "code": "question_open", "reason": f"{WHY_MARK} 질문 꼴"},
                "repeat": {"level": repeat, "code": "same_as_previous", "reason": f"{WHY_MARK} 반복"}}


def make(qapp, *, score=None, **kw):
    v = vtv.ViewerTrainView(parts={"player": FakePlayer}, score_fn=score or FakeScore())
    args = dict(chat=messages(), segment=SEG, media_path="synthetic.mp4", project_name="P-synth")
    args.update(kw)
    ok = v.start(**args)
    return v, ok


def _send(v, text):
    """쓰기 칸에 글을 넣고 Enter. (QTest.keyClicks 는 한글 글자에서 PySide6 가 프로세스를 끝내므로 insert 로 넣는다 —
    Enter 는 진짜 키 누름.)"""
    v.input.setFocus()
    v.input.insert(text)
    QTest.keyClick(v.input, Qt.Key_Return)


# ---------------------------------------------------------------------------- 메뉴 켜기
def test_menu_state_pilot_and_media():
    assert vtv.viewer_menu_state("P-synth") == (True, "")
    ok, why = vtv.viewer_menu_state("pilot-synth")
    assert not ok and "시범" in why
    assert vtv.viewer_menu_state(os.path.join("x", "Pilot-A"))[0] is False
    assert vtv.viewer_menu_state("pilot-synth", pilot_locked=True)[0] is True
    assert vtv.viewer_menu_state("P-synth", has_media=False)[0] is False


def test_pilot_project_blocked_in_view(qapp):
    """메뉴를 거치지 않고 불러도 시범 프로젝트는 열리지 않는다(이중 잠금, D-20 5). 채점기·기록도 부르지 않는다."""
    sc = FakeScore()
    v, ok = make(qapp, score=sc, project_name="pilot-synth")
    assert not ok and v.state() == "idle" and "시범" in v.blocked_label.text()
    assert not v.input.isEnabled() and v.events == [] and sc.calls == []
    assert v.write(MINE) is None
    assert vtv.load_viewer_scenario(os.path.join("nowhere", "pilot-synth"), PID, SEG) == (None, None)


# ---------------------------------------------------------------------------- 입력 칸 하나
def test_exactly_one_input_field(qapp):
    """입력 칸(QLineEdit·QTextEdit·…)은 쓰기 칸 하나뿐(D-19 5). 채팅 표의 검색 칸은 떼어 냈다."""
    v, ok = make(qapp)
    assert ok
    assert vtv.input_fields(v) == [v.input]
    assert len(v.findChildren(QLineEdit)) == 1
    assert v.findChildren(QTextEdit) == [] and v.findChildren(QPlainTextEdit) == []
    assert not v.chat.search.isVisible() and v.chat.search.parent() is None


def test_opens_with_sound_and_flows_segment_only(qapp):
    v, ok = make(qapp, timemap=Shift())
    assert ok and v.state() == "training"
    assert v.player.muted is False and not v.is_muted()           # 소리 켬(21 6-1)
    assert ("seek", SEG[0] - 2000) in v.player.calls
    v.player.fileLoaded.emit()
    assert ("play",) in v.player.calls                            # 열면 바로 재생
    data = v.chat.chat_data()
    assert data.n == 20 and min(data.times) >= SEG[0] and max(data.times) < SEG[1]   # 구간 안만 싣는다
    v.player.at(9_000 - 2000)                                       # VOD 9000
    assert v.position_vod() == 9_000 and v.chat.row_count() == 5   # 흐르는 채팅: 5000~9000
    assert v.events[0]["kind"] == "viewer.start" and v.events[0]["audio"] == "on"


def test_enter_sends_practice_line_without_pausing(qapp):
    sc = FakeScore()
    v, ok = make(qapp, score=sc, timemap=Shift())
    assert ok
    v.player.fileLoaded.emit()
    v.player.at(10_000 - 2000)
    got = []
    v.writeScored.connect(got.append)
    _send(v, f"{MINE} 주제 질문?")
    assert v.write_count() == 1 and v.input.text() == ""
    assert ("pause",) not in v.player.calls and v.player.playing   # 재생은 멈추지 않는다
    # 그 순간의 VOD 시각으로 채팅 표에 '연습' 줄
    data = v.chat.chat_data()
    pid = v.practice_ids()[0]
    i = data.index_of(pid)
    assert i is not None and data.times[i] == 10_000 and data.nicks[i] == "나"
    assert v.chat.label_marks() == {pid: "연습"}
    assert v.chat.row_count() == 7                                 # 실제 6줄(5000~10000) + 연습 1줄
    # 세 축 칸: 단계와 까닭 한 줄
    t = v.axis_labels["topic"].text()
    assert "주제 적합성" in t and "좋음" in t and WHY_MARK in t
    assert "좋음" in v.axis_labels["expand"].text() and "좋음" in v.axis_labels["repeat"].text()
    assert got == [{"n": 1, "vod_ms": 10_000, "chars": len(f"{MINE} 주제 질문?"),
                    "levels": {"topic": "good", "expand": "good", "repeat": "good"},
                    "codes": {"topic": "topic_overlap", "expand": "question_open", "repeat": "same_as_previous"}}]
    # 같은 글을 또 쓰면 반복 축이 아쉬움(맥락 my_previous)
    _send(v, f"{MINE} 주제 질문?")
    assert v.last_scores()["repeat"]["level"] == "weak" and "아쉬움" in v.axis_labels["repeat"].text()
    assert v.count_label.text() == "쓴 수 2"
    # 빈 칸 Enter 는 아무것도 안 함
    QTest.keyClick(v.input, Qt.Key_Return)
    assert v.write_count() == 2


def test_context_has_recent_chat_and_my_previous(qapp):
    sc = FakeScore()
    v, ok = make(qapp, score=sc, context={"topic_words": ["합성주제"]}, context_fn=lambda ms: {"asr_words": [ms]})
    assert ok
    v.seek_vod(12_000)
    v.write(f"{MINE} 하나")
    v.seek_vod(14_500)
    v.write(f"{MINE} 둘")
    _text, ctx = sc.calls[-1]
    assert ctx["vod_ms"] == 14_500 and ctx["range_ms"] == SEG and ctx["window_ms"] == vtv.CONTEXT_WINDOW_MS
    ts = [r["t_ms"] for r in ctx["recent_chat"]]
    assert ts == list(range(5_000, 14_001, 1000))                  # 앞 120초 안, 화면에 나온 것만(미래·연습 줄 없음)
    assert all(r["virtual"] is False for r in ctx["recent_chat"])
    assert ctx["my_previous"] == [f"{MINE} 하나"]
    assert ctx["topic_words"] == ["합성주제"] and ctx["asr_words"] == [14_500]


def test_no_text_in_signals_events_logs_status(qapp, caplog):
    caplog.set_level(logging.DEBUG)
    msgs = []
    v = vtv.ViewerTrainView(parts={"player": FakePlayer}, score_fn=FakeScore())
    v.messageShown.connect(msgs.append)
    evs = []
    v.viewerEvent.connect(evs.append)
    assert v.start(chat=messages(), segment=SEG, media_path="synthetic.mp4", project_name="P-synth")
    v.seek_vod(8_000)
    _send(v, f"{MINE} 주제?")
    v.show_summary()
    v.close_view()
    blob = json.dumps([v.events, evs, msgs, v.summary()], ensure_ascii=False)
    assert MINE not in blob and WHY_MARK not in blob and TEXT_MARK not in blob and NICK_MARK not in blob
    assert MINE not in v.msg.text()
    for rec in caplog.records:
        s = rec.getMessage()
        assert MINE not in s and WHY_MARK not in s and TEXT_MARK not in s


def test_write_log_failure_keeps_text(qapp):
    class BadLog:
        def start(self, **kw):
            pass

        def write(self, *a, **kw):
            raise OSError("디스크")

        def end(self, *a, **kw):
            pass
    v, ok = make(qapp, log=BadLog(), project_id=PID)
    assert ok
    _send(v, f"{MINE} 주제")
    assert v.write_count() == 0 and v.input.text() == f"{MINE} 주제" and "보내지 않았습니다" in v.msg.text()
    assert all(not r["chat_id"].startswith("p:") for r in v._rows)


def test_scorer_missing_still_records_without_levels(qapp):
    def missing(text, ctx):
        raise ImportError("viewer_score")
    v, ok = make(qapp, score=missing)
    assert ok
    res = v.write(f"{MINE} 하나")
    assert res["levels"] == {"topic": None, "expand": None, "repeat": None}
    assert res["codes"]["topic"] == "scorer_missing"
    assert "쓸 수 없습니다" in v.axis_labels["topic"].text() and v.write_count() == 1


def test_normalize_score_shapes():
    full = {"topic": {"level": "good", "code": "a_b", "reason": "x"}, "expand": {"level": "ok"},
            "repeat": {"level": "weak", "codes": ["Bad", "dup_copy"]}}
    n = vtv.normalize_score(full)
    assert n["topic"] == {"level": "good", "code": "a_b", "reason": "x"}
    assert n["expand"]["code"] == "other" and n["repeat"]["code"] == "dup_copy"
    alt = {"axes": {"topic_fit": "좋음", "expansion": {"grade": 1, "why": "y"}, "repetition": {"level": "low"}}}
    n2 = vtv.normalize_score(alt)
    assert [n2[a]["level"] for a in vtv.AXES] == ["good", "ok", "weak"] and n2["expand"]["reason"] == "y"
    assert vtv.normalize_score({"topic": {"level": "good"}}) is None
    assert vtv.normalize_score(None) is None and vtv.normalize_score({"topic": True, "expand": 1, "repeat": 0}) is None
    assert vtv.reason_line(None) == "—" and vtv.reason_line({"level": "ok", "reason": ""}) == "보통"


def test_builtin_viewer_score_and_log(qapp, tmp_path):
    """기본 채점기(app.core.viewer_score: score_chat + ScoreContext)로: 세 축 단계·점 코드('topic.…')가 기록 스키마를
    통과하고, 같은 글을 또 쓰면 반복 여부가 나빠진다(history). 까닭 한 줄은 화면에만 — 기록에는 코드만."""
    vsc = pytest.importorskip("app.core.viewer_score")
    if not hasattr(vsc, "score_chat"):
        pytest.skip("viewer_score.score_chat 없음")
    from app import viewer_store as vs
    root = tmp_path / "P-synth"
    root.mkdir()
    log = vs.ViewerLog.create(str(root), "L01", app_version="0.0.1", pilot_dir=str(tmp_path / "pilot"))
    v = vtv.ViewerTrainView(parts={"player": FakePlayer})
    ok = v.start(chat=messages(), segment=SEG, media_path="synthetic.mp4", project_name="P-synth", log=log,
                 project_id=PID, context={"topic_blocks": [(0, 30_000, ["합성주제"])],
                                          "transcript": [(8_000, 9_000, ["합성낱말"])]})
    assert ok
    v.seek_vod(10_000)
    a = v.write("합성주제 이거 어떻게 하는 거예요?")
    b = v.write("합성주제 이거 어떻게 하는 거예요?")
    assert a is not None and b is not None
    assert all(a["levels"][x] in vtv.LEVELS for x in vtv.AXES)
    assert all(a["codes"][x].startswith(x + ".") for x in vtv.AXES)
    order = {"good": 0, "ok": 1, "weak": 2}
    assert order[b["levels"]["repeat"]] > order[a["levels"]["repeat"]]
    assert v.axis_labels["topic"].text().count("\n") == 1
    v.close_view()
    recs, info = vs.read_valid(log.path)
    assert info["invalid"] == 0 and [r["kind"] for r in recs] == ["viewer.start", "viewer.write", "viewer.write",
                                                                   "viewer.end"]
    assert recs[1]["body"]["scores"]["topic"]["code"] == a["codes"]["topic"]


def test_incremental_insert_matches_full_rebuild(qapp):
    """연습 줄을 끼운 ChatData(_chatdata_insert)가 처음부터 다시 만든 것과 같다 — 같은 시각 줄 사이 순서까지."""
    from app.ui.chat_panel import ChatData
    v, ok = make(qapp)
    assert ok
    for t in (6_000, 6_000, 9_500, 5_000, 24_999):
        v.seek_vod(t)
        v.write(f"{MINE} {t}")
    cd = v.chat.chat_data()
    full = ChatData([*v._rows, *v._practice], dict(v._vno))
    for k in ("times", "chat_ids", "texts", "folded", "nicks", "vnos", "kinds", "roles", "dim"):
        assert list(getattr(cd, k)) == list(getattr(full, k)), k
    assert cd.n == full.n == 25 and cd.index_of("p:3") == full.index_of("p:3")
    assert set(v.chat.label_marks()) == {"p:1", "p:2", "p:3", "p:4", "p:5"}


def test_chatdata_input_is_clipped(qapp):
    from app.ui.chat_panel import ChatData
    cd = ChatData(messages())
    v, ok = make(qapp, chat=cd)
    assert ok and v.chat.chat_data().n == 20
    v.seek_vod(6_000)
    v.write(f"{MINE} 하나")
    assert v.chat.chat_data().n == 21


def test_segment_end_summary_and_close(qapp):
    v, ok = make(qapp, timemap=Shift())
    assert ok
    v.player.fileLoaded.emit()
    v.player.at(7_000 - 2000)
    v.write(f"{MINE} 주제 질문?")
    v.player.at(9_000 - 2000)
    v.write(f"{MINE} 짧")
    shown = []
    v.summaryShown.connect(shown.append)
    v.player.at(SEG[1] - 2000)                                      # 구간 끝
    assert v.state() == "summary" and ("pause",) in v.player.calls
    assert not v.input.isEnabled()
    assert shown == [{"writes": 2, "levels": {"topic": {"good": 1, "ok": 0, "weak": 1},
                                               "expand": {"good": 1, "ok": 0, "weak": 1},
                                               "repeat": {"good": 2, "ok": 0, "weak": 0}}, "examples": 0}]
    assert v.right.currentWidget() is v.summary_page
    assert "쓴 채팅 2개" in v.sum_count.text() and "좋음 1" in v.sum_axes["topic"].text()
    assert "가상 채팅이 없습니다" in v.sum_examples[0].text()
    assert v.events[-1]["kind"] == "viewer.end" and v.events[-1]["reason"] == "segment_end"
    assert v.write(f"{MINE} 늦음") is None
    # 닫기 1클릭
    closed = []
    v.closeRequested.connect(lambda: closed.append(1))
    QTest.mouseClick(v.btn_close, Qt.LeftButton)
    assert closed == [1]
    v.close_view()
    v.close_view()
    assert v.player.closed and [e["kind"] for e in v.events].count("viewer.end") == 1


def test_ctrl_enter_summary(qapp):
    v, ok = make(qapp)
    assert ok
    v.input.setFocus()
    QTest.keyClick(v.input, Qt.Key_Return, Qt.ControlModifier)
    assert v.state() == "summary" and v.events[-1]["reason"] == "summary"


def test_chat_click_does_nothing(qapp):
    v, ok = make(qapp)
    assert ok
    v.seek_vod(9_000)
    v.chat.chatClicked.emit(cid(6), 6_000)
    assert v.write_count() == 0 and v.chat.label_marks() == {}


# ---------------------------------------------------------------------------- 가상 채팅 섞기 + 기록
def _scenario(src):
    return {"records": [
        {"kind": "scenario.header", "scenario_id": "SC-20261022-abcd", "range": {"vod_from_ms": SEG[0], "vod_to_ms": SEG[1]}},
        {"kind": "scenario.chat", "line_id": "s0001", "source": "retrieved", "show_vod_ms": 6_500,
         "from_project_id": "p0", "from_chat_id": src["chat_id"], "from_t_ms": src["t_ms"], "display_mask": "하**"},
        {"kind": "scenario.chat", "line_id": "s0002", "source": "generated", "show_vod_ms": 8_500,
         "gen_id": "g0001", "text": "합성생성 주제 {:zz_test_01:}", "display_mask": "두*"},
        {"kind": "scenario.end", "lines": 2, "lines_sha256": "0" * 64}],
        "scenario_id": "SC-20261022-abcd", "scenario_sha256": "b" * 64,
        "resolve": lambda p, c: src if c == src["chat_id"] else None}


def test_virtual_mix_examples_and_real_log(qapp, tmp_path):
    """기본 섞기(core.scenario) + 실제 ViewerLog(합성 프로젝트): 가상 줄은 '가져옴'·'생성' 표시와 함께 흐르고, 구간 끝
    '다른 시청자라면' 은 retrieved 만(가림 이름 · 가져옴). 기록은 스키마를 통과하고 글은 write 줄에만 있다."""
    pytest.importorskip("app.core.scenario")
    from app import viewer_store as vs
    src = {"chat_id": cid(99), "t_ms": 99_000, "kind": "text", "nickname_masked": NICK_MARK,
           "viewer_id": "v099", "text": "합성가져옴 주제 질문?"}
    root = tmp_path / "P-synth"
    root.mkdir()
    log = vs.ViewerLog.create(str(root), "L01", app_version="0.0.1", pilot_dir=str(tmp_path / "pilot"))
    v = vtv.ViewerTrainView(parts={"player": FakePlayer}, score_fn=FakeScore())
    ok = v.start(chat=messages(), media_path="synthetic.mp4", project_name="P-synth", log=log, project_id=PID,
                 scenario=_scenario(src))
    assert ok and v.virtual_line_count() == 2
    assert v.events[0]["scenario_id"] == "SC-20261022-abcd" and v.events[0]["virtual_lines"] == 2
    data = v.chat.chat_data()
    nicks = {data.chat_ids[i]: data.nicks[i] for i in range(data.n)}
    assert nicks["v:s0001"] == "하** · 가져옴" and nicks["v:s0002"].endswith(" · 생성")   # 가림 이름 모양은 섞기가 정한다
    assert min(t for t in data.times) >= SEG[0]                    # 구간(시나리오 머리줄) 안만
    v.seek_vod(9_000)
    _send(v, f"{MINE} 주제 질문?")
    ctx = v._score_fn.calls[-1][1]
    assert sum(1 for r in ctx["recent_chat"] if r["virtual"]) == 2
    v.show_summary()
    ex = v.examples()
    assert len(ex) == 1 and ex[0]["line_id"] == "s0001" and ex[0]["name"] == "하**"
    assert "하** · 가져옴 — 합성가져옴" in v.sum_examples[0].text()
    v.close_view()
    recs, info = vs.read_valid(log.path)
    assert info["invalid"] == 0 and [r["kind"] for r in recs] == ["viewer.start", "viewer.write", "viewer.end"]
    assert recs[0]["body"]["scenario_id"] == "SC-20261022-abcd" and recs[0]["body"]["virtual_lines"] == 2
    assert recs[1]["body"]["text"] == f"{MINE} 주제 질문?" and recs[1]["body"]["scorer"] == "viewer_score/0.1"
    assert recs[2]["body"]["reason"] == "summary" and recs[2]["body"]["examples"] == 1
    raw = open(log.path, encoding="utf-8").read()
    assert raw.count(MINE) == 1 and WHY_MARK not in raw and "합성가져옴" not in raw and NICK_MARK not in raw
    assert "하**" not in raw


def test_broken_scenario_blocks_before_log(qapp, tmp_path):
    class RecLog:
        def __init__(self):
            self.calls = []

        def start(self, **kw):
            self.calls.append("start")
    lg = RecLog()

    def boom(real, scenario):
        raise ValueError("깨짐")
    v = vtv.ViewerTrainView(parts={"player": FakePlayer}, score_fn=FakeScore(), mix_fn=boom)
    ok = v.start(chat=messages(), segment=SEG, project_name="P-synth", log=lg, scenario={"records": [{}]})
    assert not ok and lg.calls == [] and "섞지 못했습니다" in v.blocked_label.text()


# ---------------------------------------------------------------------------- 1366×768
def _fit(w, app):
    """test_c6_ui._fit 과 같은 셈(오른쪽 밖·가로 스크롤·글자 잘림)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QLabel, QScrollArea
    for _ in range(4):
        app.processEvents()
    hs = any(sa.horizontalScrollBar().maximum() > 0 for sa in w.findChildren(QScrollArea) if sa.isVisibleTo(w))
    clipped = outside = 0
    kinds = (QAbstractButton, QLabel, QLineEdit, QComboBox, QAbstractSpinBox)
    for c in [x for k in kinds for x in w.findChildren(k)]:
        if not c.isVisibleTo(w) or c.width() <= 0:
            continue
        if isinstance(c, QAbstractButton) and c.text() and c.sizeHint().width() > c.width() + 1:
            clipped += 1
        elif isinstance(c, QLabel) and not c.wordWrap() and c.text() and c.sizeHint().width() > c.width() + 1:
            clipped += 1
        p, sa = c.parentWidget(), None
        while p is not None and p is not w:
            if isinstance(p, QScrollArea):
                sa = p
                break
            p = p.parentWidget()
        box = sa.viewport() if sa is not None else w
        if c.mapTo(box, QPoint(c.width(), 0)).x() > box.width() + 1:
            outside += 1
    return {"hscroll": hs, "clipped": clipped, "outside": outside}


@pytest.mark.parametrize("size", [(1366, 700), (1024, 640)])
def test_fits_small_screen(qapp, size):
    """1366×768(메뉴·상태 줄을 뺀 700) — 훈련 중·쓴 뒤·요약에서 나감 0, 잘림 0, 가로 스크롤 없음."""
    class LongScore(FakeScore):
        def __call__(self, text, ctx):
            r = super().__call__(text, ctx)
            for a in vtv.AXES:
                r[a]["reason"] = WHY_MARK + " 아주 긴 까닭 한 줄이 칸보다 길어도 줄을 바꿔 보입니다" * 2
            return r
    v, ok = make(qapp, score=LongScore())
    assert ok
    v.resize(*size)
    v.show()
    assert v.minimumSizeHint().width() <= size[0]
    assert _fit(v, qapp) == {"hscroll": False, "clipped": 0, "outside": 0}
    v.seek_vod(9_000)
    v.write(f"{MINE} 주제 질문?")
    assert _fit(v, qapp) == {"hscroll": False, "clipped": 0, "outside": 0}
    v.show_summary()
    assert _fit(v, qapp) == {"hscroll": False, "clipped": 0, "outside": 0}


def test_human_clicks(qapp):
    """사람 손: 열기는 메뉴 2클릭(통합 담당), 쓰기는 Enter(클릭 0), 닫기 1. 이 화면 안에서 센 클릭은 닫기 1."""
    v, ok = make(qapp)
    assert ok
    clicks = 0
    v.seek_vod(8_000)
    _send(v, f"{MINE} 하나")                 # 클릭 0
    v.seek_vod(12_000)
    _send(v, f"{MINE} 둘?")                  # 클릭 0
    v.player.at(SEG[1])                      # 구간 끝 요약: 클릭 0
    assert v.state() == "summary"
    got = []
    v.closeRequested.connect(lambda: got.append(1))
    QTest.mouseClick(v.btn_close, Qt.LeftButton)
    clicks += 1
    assert got == [1] and clicks == 1 and v.write_count() == 2
