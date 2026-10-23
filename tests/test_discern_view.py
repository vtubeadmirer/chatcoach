"""가상 채팅 가려내기 화면 시험 (app/ui/discern_view.py, H-5, 20 7장, 25 6-2, 03 6-19, D-16).

화면 없이(QT_QPA_PLATFORM=offscreen) **합성** 채팅·합성 시나리오만 쓴다. 실제 채팅·닉네임·시범 폴더는 쓰지 않는다.
플레이어는 가짜(호출 기록), 채팅 표·타임라인은 작업대와 같은 진짜 부품이다. 쓰기는 tmp_path 에만.
사람 클릭 수: 열기 1 + 채팅마다 1 (+ 선택 '끝내고 정답 보기' 1). 구간 끝 정답은 0.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from app import discern_store as ds  # noqa: E402
from app.core import discern as dc  # noqa: E402
from app.ui import discern_view as dv  # noqa: E402

TEXT_MARK = "합성본문"
NICK_MARK = "합*"
V_TEXT = "합성가상본문"
SID = "SC-20261022-abcd"
SHA = "b" * 64
PID = "11111111-2222-4333-8444-555555555555"
SEG = (5_000, 25_000)
KST = timezone(timedelta(hours=9))


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


def messages(n=30, step=1000):
    """합성 실제 채팅: VOD 0~29초, 1초마다. c7 은 후원(대상 아님)."""
    out = []
    for i in range(n):
        out.append({"chat_id": cid(i), "t_ms": i * step, "viewer_id": "v%03d" % (i % 5), "nickname_masked": NICK_MARK,
                    "kind": "donation" if i == 7 else "text", "role": "viewer", "text": f"{TEXT_MARK} {i}"})
    return out


V_LINES = [
    {"source": "generated", "line_id": "s0001", "show_vod_ms": 6_500, "text": V_TEXT + " 가", "display_mask": "하**"},
    {"source": "generated", "line_id": "s0002", "show_vod_ms": 9_500, "text": V_TEXT + " 나", "display_mask": "두*"},
    {"source": "generated", "line_id": "s0003", "show_vod_ms": 12_500, "text": V_TEXT + " 다"},
    {"source": "generated", "line_id": "s0004", "show_vod_ms": 14_500, "text": V_TEXT + " 라", "display_mask": "세**"},
    {"source": "retrieved", "line_id": "s0005", "show_vod_ms": 16_500, "text": V_TEXT + " 마", "display_mask": "네**"},
    {"source": "retrieved", "line_id": "s0006", "show_vod_ms": 18_500, "text": V_TEXT + " 바", "display_mask": "다**"},
]
MIX_CALLS = []


def fake_mix(real, scenario):
    MIX_CALLS.append(len(real))
    return sorted([*real, *[dict(x) for x in V_LINES]], key=lambda r: r.get("t_ms", r.get("show_vod_ms")))


def scenario_obj(rng=SEG):
    hdr = {"kind": "scenario.header", "scenario_id": SID, "range": {"vod_from_ms": rng[0], "vod_to_ms": rng[1]}}
    return {"records": [hdr, {"kind": "scenario.end"}], "scenario_id": SID, "scenario_sha256": SHA}


def make_v(qapp, *, mix=fake_mix, scenario="default", **kw):
    v = dv.DiscernView(parts={"player": FakePlayer}, mix_fn=mix, clock=lambda: "2026-10-23T00:00:00.000Z")
    kw.setdefault("media_path", "synthetic.mp4")
    kw.setdefault("timemap", Shift())
    ok = v.start(chat=messages(), scenario=scenario_obj() if scenario == "default" else scenario,
                 duration_ms=30_000, project_id=PID, **kw)
    return v, ok


def vid(n):
    return f"v:s{n:04d}"


def key(n):
    return f"{SID}:s{n:04d}"


def _nick(v, chat_id):
    d = v.chat.chat_data()
    return d.nicks[d.index_of(chat_id)]


def _ev_text(events):
    return json.dumps(events, ensure_ascii=False)


# ---------------------------------------------------------------------------- 열기·숨김
def test_open_hides_marks_and_shows_banner(qapp):
    MIX_CALLS.clear()
    v, ok = make_v(qapp)
    assert ok and v.state() == "judging" and MIX_CALLS == [30]
    # 대상: 구간 [5초, 25초) 안 실제 일반 채팅 19 (후원 c7 뺌) + 시나리오 줄 6
    t = v.targets()
    assert len(t) == 25 and cid(7) not in t and cid(4) not in t and cid(25) not in t
    assert t[vid(1)].key == key(1) and t[vid(1)].source == "generated" and t[vid(5)].source == "retrieved"
    assert t[cid(5)].key == cid(5) and t[cid(5)].source == "replay"
    # 판정 중에는 '가'·'생'·'· 생성' 표시가 없다
    assert _nick(v, vid(1)) == "하**" and _nick(v, vid(3)) == "시청자3" and _nick(v, vid(5)) == "네**"
    assert not v.banner.isHidden() and v.banner.text() == dv.BANNER_GENERATED
    assert v.chat.view.isColumnHidden(dv.COL_VIEWER)                     # 시청자 번호 칸 숨김
    assert v.chat.search.isHidden()
    # 소리 켜고, 보정한 파일 시각으로 시작
    assert v.player.muted is False and ("seek", SEG[0] - 2000) in v.player.calls
    v.player.fileLoaded.emit()
    assert ("play",) in v.player.calls
    st = v.events[0]
    assert st["kind"] == "discern.start" and st["shown"] == {"replay": 19, "retrieved": 2, "generated": 4}
    assert st["marks_hidden"] is True and st["audio"] == "on"
    assert v.count_label.text() == "판정 0 / 25"


def test_retrieved_only_banner(qapp):
    def mix_r(real, _s):
        return sorted([*real, *[dict(x) for x in V_LINES if x["source"] == "retrieved"]],
                      key=lambda r: r.get("t_ms", r.get("show_vod_ms")))
    v, ok = make_v(qapp, mix=mix_r)
    assert ok and v.banner.text() == dv.BANNER_RETRIEVED


def test_flowing_chat_and_future_lines_not_judgeable(qapp):
    v, _ = make_v(qapp)
    v.player.at(6_000 - 2000)                       # VOD 6초
    assert v.chat.row_count() == 2                  # c5, c6 (6.5초 가상 줄은 아직)
    assert v.judge(vid(2), "virtual") is None and "아직" in v.msg.text()
    assert v.judge(vid(1), "virtual") == "virtual"  # 1초 여유 안
    assert v.judge(cid(7), "real") is None          # 후원은 대상 아님
    assert v.judged_count() == 1


# ---------------------------------------------------------------------------- 판정
def test_judge_change_and_clear(qapp):
    v, _ = make_v(qapp)
    v.player.at(10_000 - 2000)
    assert v.judge(cid(5), "real") == "real"
    assert v.judge(cid(5), "virtual") == "virtual"            # 다른 단추 = 바꿈
    assert v.answer_of(cid(5)) == "virtual"
    assert v.judge(cid(5), "virtual") == "none"               # 같은 단추 다시 = 지움(안 봄)
    assert v.answer_of(cid(5)) is None and v.judged_count() == 0
    assert v.judge(vid(1), "unsure") == "unsure"
    assert v.chat.label_marks() == {vid(1): "모르겠음"}
    judges = [e for e in v.events if e["kind"] == "discern.judge"]
    assert [e["answer"] for e in judges] == ["real", "virtual", "none", "unsure"]
    assert judges[0] == {"kind": "discern.judge", "chat_id": cid(5), "answer": "real", "judged_vod_ms": 10_000,
                         "wall_at": "2026-10-23T00:00:00.000Z"}
    assert judges[-1]["scenario_id"] == SID and judges[-1]["line_id"] == "s0001"
    assert "source" not in judges[-1]                         # 정답(출처)은 신호·기록에 없다
    txt = _ev_text(v.events)
    assert TEXT_MARK not in txt and V_TEXT not in txt and NICK_MARK not in txt and "하**" not in txt


def test_click_on_kind_cell_judges_one_click(qapp):
    """채팅 옆 단추 한 번 = 판정 한 번 (D-16). '종류' 칸의 왼쪽·가운데·오른쪽 1/3 = 진짜·가짜·모름."""
    v, _ = make_v(qapp)
    v.resize(1366, 768)
    v.show()
    QApplication.processEvents()
    v.player.at(10_000 - 2000)
    QApplication.processEvents()
    view = v.chat.view
    m = v.chat.model
    row = m.view_row_of(v.chat.chat_data().index_of(vid(1)))
    assert row >= 0
    rect = view.visualRect(m.index(row, dv.COL_KIND))
    assert rect.width() >= dv.JUDGE_COL_W - 2
    for frac, want in ((1 / 6, "real"), (3 / 6, "virtual"), (5 / 6, "unsure")):
        x = rect.left() + int(rect.width() * frac)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(x, rect.center().y()))
        QApplication.processEvents()
        assert v.answer_of(vid(1)) == want
    assert dv.JudgeDelegate.part_at(rect, rect.left() - 5) is None


# ---------------------------------------------------------------------------- 끝: 손 계산과 같은지
def _judge_like_hand_calc(v):
    """test_discern 의 손 계산 1 과 같은 판정(생성 4 · replay 4 · retrieved 2, 그 밖의 replay 는 안 봄)."""
    v.player.at(24_000 - 2000)
    plan = {vid(1): "virtual", vid(2): "virtual", vid(3): "real", vid(4): "unsure",
            cid(10): "real", cid(11): "real", cid(12): "virtual",
            vid(5): "real", vid(6): "virtual"}
    for k, a in plan.items():
        assert v.judge(k, a) == a


def test_segment_end_shows_answers_and_numbers_equal_hand_calc(qapp):
    v, _ = make_v(qapp)
    shown = []
    v.resultShown.connect(shown.append)
    _judge_like_hand_calc(v)
    v.player.at(SEG[1] - 2000)                     # 구간 끝 → 저절로 정답 (클릭 0)
    assert v.state() == "result"
    r = v.result()
    # 대상 25(생성 4 · retrieved 2 · replay 19), 판정 9, 안 봄 16. 혼동표·κ 는 손 계산 1 과 같다(안 봄은 표에 없음)
    assert r.targets == 25 and r.judged == 9 and r.unseen == 16
    assert r.confusion["rows"] == [[3, 2, 0], [1, 2, 1], [0, 0, 0]]
    assert r.kappa == pytest.approx(0.2)
    assert r.detection["rate"] == 0.5 and r.false_alarm["rate"] == 0.4 and r.retrieved["rate"] == 0.5
    assert r.gate["status"] == "low_n"
    assert shown and shown[0]["kappa"] == pytest.approx(0.2) and shown[0]["gate"] == "low_n"
    # 끝나면 표시가 다시 보이고, 줄마다 정답
    assert _nick(v, vid(1)) == "생 하** · 생성" and _nick(v, vid(5)) == "가 네** · 가져옴"
    marks = v.answer_marks()
    assert marks[vid(1)] == "✓ 생성 · 가짜" and marks[vid(3)] == "✗ 생성 · 진짜"
    assert marks[cid(12)] == "✗ 실제 · 가짜" and marks[cid(13)] == "· 실제 · 안 봄"
    assert marks[vid(5)] == "✓ 가져옴 · 진짜" and marks[vid(6)] == "✗ 가져옴 · 가짜"   # retrieved 정답 = 실제
    assert v.chat.label_marks() == marks
    assert v.chat.row_count() == v.chat.total_count()               # 다 보인다
    assert not v.result_label.isHidden() and "0.50 (2/4)" in v.result_label.text()
    assert "κ 0.20" in v.result_label.text() and "표본 적음" in v.result_label.text()
    assert v.banner.text() == dv.BANNER_RESULT
    end = v.events[-1]
    assert end["kind"] == "discern.end" and end["reason"] == "segment_end" and end["judged"] == 9
    assert TEXT_MARK not in _ev_text(v.events) and V_TEXT not in _ev_text(v.events)
    # 정답 뒤에는 판정이 안 된다
    assert v.judge(cid(14), "real") is None


def test_finish_button_early(qapp):
    v, _ = make_v(qapp)
    v.player.at(10_000 - 2000)
    v.judge(vid(1), "virtual")
    v.btn_finish.click()                            # 선택 1클릭
    assert v.state() == "result" and v.result().judged == 1 and v.events[-1]["reason"] == "finish"
    assert not v.btn_finish.isEnabled()


def test_seen_and_repeat_in_result(qapp):
    v, _ = make_v(qapp, seen_before=True, repeat=True)
    v.finish()
    t = v.result_label.text()
    assert "본 적 있음" in t and "다시 봄" in t
    assert v.events[0]["seen_before"] is True and v.events[0]["repeat"] is True


def test_close_before_result_writes_closed(qapp):
    v, _ = make_v(qapp)
    v.player.at(10_000 - 2000)
    v.judge(cid(5), "real")
    v.close_view()
    v.close_view()
    ends = [e for e in v.events if e["kind"] == "discern.end"]
    assert ends == [{"kind": "discern.end", "reason": "closed", "judged": 1, "unseen": 24}]
    assert v.player.closed and v.state() == "closed"


# ---------------------------------------------------------------------------- 기록 파일
class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 23, 10, 0, 0, tzinfo=KST)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def _proj(tmp_path, name="synthetic-proj"):
    root = tmp_path / "projects" / name
    root.mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project_id": PID}), encoding="utf-8")
    (tmp_path / "pilot").mkdir(exist_ok=True)
    return str(root)


def test_log_file_lines_and_numbers(qapp, tmp_path):
    root = _proj(tmp_path)
    log = ds.DiscernLog.create(root, "L01", clock=Clock(), rand4=lambda: "ab12", app_version="0.9.0")
    v, ok = make_v(qapp, log=log)
    assert ok
    _judge_like_hand_calc(v)
    v.judge(cid(10), "real")                       # 같은 단추 다시 = 지움 → none 줄
    v.judge(cid(10), "real")                       # 다시 진짜
    v.player.at(SEG[1] - 2000)
    recs, info = ds.read_valid(log.path)
    assert info["invalid"] == 0 and info["corrupt"] == 0
    kinds = [r["kind"] for r in recs]
    assert kinds[0] == "discern.start" and kinds[-1] == "discern.end" and kinds.count("discern.judge") == 11
    assert dc.answers_from_events(recs) == v.answers()
    end = recs[-1]["body"]
    assert end["reason"] == "segment_end" and end["result"]["kappa"] == pytest.approx(0.2)
    assert end["result"]["confusion"] == [[3, 2, 0], [1, 2, 1], [0, 0, 0]]
    raw = open(log.path, encoding="utf-8").read()
    for word in (TEXT_MARK, V_TEXT, NICK_MARK, "하**", "nickname", "display_mask", '"source"'):
        assert word not in raw
    v.close_view()                                  # 이미 끝 줄을 썼으면 더 쓰지 않는다
    assert len(ds.read_valid(log.path)[0]) == len(recs)


class FailingLog:
    def __init__(self, fail_on):
        self.fail_on = fail_on
        self.calls = []

    def start(self, **kw):
        self.calls.append("start")
        if self.fail_on == "start":
            raise OSError("합성 실패")

    def judge(self, k, a, ms):
        self.calls.append("judge")
        if self.fail_on == "judge":
            raise OSError("합성 실패")

    def end(self, reason, **kw):
        self.calls.append("end")
        if self.fail_on == "end":
            raise OSError("합성 실패")


def test_log_failures_block(qapp):
    v, ok = make_v(qapp, log=FailingLog("start"))
    assert not ok and "시작하지 못했습니다" in v.blocked_label.text()
    v, ok = make_v(qapp, log=FailingLog("judge"))
    v.player.at(10_000 - 2000)
    assert v.judge(cid(5), "real") is None and v.judged_count() == 0 and "반영하지 않았습니다" in v.msg.text()
    v, ok = make_v(qapp, log=FailingLog("end"))
    v.player.at(10_000 - 2000)
    assert v.finish() is False and v.state() == "judging"             # 끝 줄을 못 쓰면 정답을 보이지 않는다
    assert v.result_label.isHidden() and _nick(v, vid(1)) == "하**"


# ---------------------------------------------------------------------------- 메뉴 막기 (시범·라벨링)
def test_menu_state_order():
    assert dv.discern_menu_state("pilot-합성", session_kind="primary")[1] == dv.REASON_PILOT
    assert dv.discern_menu_state("pilot-합성", pilot_locked=True) == (True, "")
    for k in ("primary", "relabel_same", "relabel_second", "adjudication", "origin_review"):
        assert dv.discern_menu_state("syn", session_kind=k) == (False, dv.REASON_LABELING)
    assert dv.discern_menu_state("syn", session_kind="analysis") == (True, "")
    assert dv.discern_menu_state("syn", has_media=False)[1] == dv.REASON_NO_MEDIA
    assert dv.discern_menu_state("syn", has_scenario=False)[1] == dv.REASON_NO_SCENARIO


def test_labeling_session_never_opens_scenarios(qapp, tmp_path, monkeypatch):
    """라벨링 세션에서는 메뉴가 꺼지고 scenarios\\ 를 0번 연다(20 1장 4번)."""
    from app import scenario_mix
    from app import scenario_store as sst
    root = _proj(tmp_path)
    os.makedirs(os.path.join(root, "scenarios"))
    touched = []
    real_listdir, real_open = os.listdir, open
    monkeypatch.setattr(scenario_mix, "find_scenario", lambda *a, **k: touched.append("find") or (None, {}))
    monkeypatch.setattr(sst, "latest_scenario", lambda *a, **k: touched.append("latest") or (None, {}))
    monkeypatch.setattr(os, "listdir", lambda p=".": (touched.append("ls") if "scenarios" in str(p) else None)
                        or real_listdir(p))
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: (touched.append("open") if "scenarios" in str(p)
                                                             else None) or real_open(p, *a, **k))
    for kind in sorted(dv.LABELING_KINDS):
        prep = dv.prepare_discern(root, PID, labeler_id="L01", session_kind=kind)
        assert not prep.ok and prep.reason == dv.REASON_LABELING
        MIX_CALLS.clear()
        v, ok = make_v(qapp, session_kind=kind)
        assert not ok and MIX_CALLS == [] and v.blocked_label.text() == dv.REASON_LABELING
    assert touched == []


def test_pilot_blocked_before_mix(qapp, tmp_path, monkeypatch):
    from app import scenario_mix
    MIX_CALLS.clear()
    v, ok = make_v(qapp, project_name="pilot-합성")
    assert not ok and MIX_CALLS == [] and v.blocked_label.text() == dv.REASON_PILOT
    root = _proj(tmp_path, name="pilot-합성")
    monkeypatch.setattr(scenario_mix, "find_scenario", lambda *a, **k: pytest.fail("시범 폴더 시나리오를 열었음"))
    assert dv.prepare_discern(root, PID).reason == dv.REASON_PILOT


def test_blocked_without_media_or_virtual_lines(qapp):
    v, ok = make_v(qapp, media_path=None)
    assert not ok and v.blocked_label.text() == dv.REASON_NO_MEDIA
    v, ok = make_v(qapp, mix=lambda real, s: list(real))
    assert not ok and v.blocked_label.text() == dv.REASON_NO_SCENARIO
    v, ok = make_v(qapp, scenario={"records": [], "scenario_sha256": SHA})
    assert not ok


# ---------------------------------------------------------------------------- 진짜 섞기 + prepare_discern (합성 프로젝트)
def _write_synthetic_project(tmp_path):
    from app import jsonl
    from app import scenario_store as sst
    from app.core import scenario as sc
    root = _proj(tmp_path)
    os.makedirs(os.path.join(root, "chat"))
    msgs = [{"chat_id": cid(i), "t_ms": i * 1000, "kind": "text", "viewer_id": "vw_%012d" % (i % 3),
             "nickname_masked": NICK_MARK, "role": "viewer" if i != 12 else "streamer",
             "text": f"{TEXT_MARK} {i}"} for i in range(60)]
    jsonl.write_all(os.path.join(root, "chat", "messages.jsonl"), msgs)
    rng = (10_000, 40_000)
    hdr = {"kind": "scenario.header", "schema": sc.SCHEMA_ID, "project_id": PID,
           "range": {"vod_from_ms": rng[0], "vod_to_ms": rng[1]},
           "created_at": "2026-10-22T10:00:00+09:00", "created_by": "engine scenario 0.1.0",
           "inputs": {"messages_sha256": "0" * 64}, "generator": {"backend": "none"}, "external": {"enabled": False}}
    chats = [{"kind": "scenario.chat", "source": "retrieved", "show_vod_ms": 15_500, "from_project_id": PID,
              "from_chat_id": cid(50), "from_t_ms": 50_000, "from_scope": "same_broadcast", "retrieval_score": 0.2,
              "display_mask": "하**", "chat_kind": "text",
              "checks": {"nickname_hit": 0, "copy_max": 0.0, "blocked": 0, "llm_flag": 0}},
             {"kind": "scenario.chat", "source": "generated", "chat_kind": "text", "show_vod_ms": 20_500,
              "gen_id": "g0001", "text": V_TEXT, "context": {"asr_from_ms": 0, "asr_to_ms": 20_500, "topic_id": "t01"},
              "display_mask": "두**", "emotes": [],
              "checks": {"nickname_hit": 0, "copy_max": 0.1, "blocked": 0, "llm_flag": 0}}]
    recs = sc.assemble(hdr, chats, yyyymmdd="20261022")
    problems = sst.validate(recs, project_id=PID)
    if problems:
        pytest.skip(f"합성 시나리오가 스키마와 맞지 않음({len(problems)}건)")
    sst.write_scenario(root, recs, check_pilot=False)
    return root, rng


def test_prepare_and_real_merge_end_to_end(qapp, tmp_path):
    """메뉴 한 번의 길: prepare_discern → DiscernLog.create → start (기본 섞기 = core.scenario.merge_for_display)."""
    root, rng = _write_synthetic_project(tmp_path)
    prep = dv.prepare_discern(root, PID, labeler_id="L01")
    assert prep.ok, prep.reason
    assert prep.kwargs["segment"] == rng and prep.kwargs["repeat"] is False and prep.kwargs["seen_before"] is False
    log = ds.DiscernLog.create(root, "L01", clock=Clock(), rand4=lambda: "e2e1", app_version="0.9.0")
    v = dv.DiscernView(parts={"player": FakePlayer})
    assert v.start(**prep.kwargs, media_path="synthetic.mp4", log=log, duration_ms=60_000)
    t = v.targets()
    assert sorted(x.source for x in t.values()).count("replay") == 29           # 구간 10~40초 실제 30줄 − 스트리머 1
    assert cid(12) not in t
    assert {x.source for x in t.values()} == {"replay", "retrieved", "generated"}
    assert _nick(v, "v:s0002") == "두**" and _nick(v, "v:s0001") == "하**"      # 표시 없음
    v.player.at(21_000)
    assert v.judge("v:s0002", "virtual") == "virtual"
    assert v.judge(cid(15), "real") == "real"
    v.player.at(40_000)
    r = v.result()
    assert r.detection == {"hit": 1, "n": 1, "rate": 1.0} and r.judged == 2
    assert _nick(v, "v:s0002") == "생 두** · 생성"
    # 두 번째로 같은 시나리오를 열면 '다시 봄'·'본 적 있음'
    prep2 = dv.prepare_discern(root, PID, labeler_id="L01")
    assert prep2.kwargs["repeat"] is True and prep2.kwargs["seen_before"] is True
    assert ds.list_sessions(root) == [log.session_id]
    # 쓴 곳은 discern\ 하나 (messages.jsonl·scenarios\ 는 그대로)
    assert sorted(os.listdir(root)) == ["chat", "discern", "project.json", "scenarios"]


# ---------------------------------------------------------------------------- 1366×768
def _inside(w, top):
    p = w.mapTo(top, QPoint(0, 0))
    return p.x() >= 0 and p.y() >= 0 and p.x() + w.width() <= top.width() and p.y() + w.height() <= top.height()


def test_fits_1366x768_judging_and_result(qapp):
    v, _ = make_v(qapp)
    v.resize(1366, 768)
    v.show()
    QApplication.processEvents()
    v.player.at(24_000 - 2000)
    QApplication.processEvents()
    out = [b.text() for b in v.findChildren(QPushButton) if b.isVisible() and not _inside(b, v)]
    assert out == [] and _inside(v.banner, v) and _inside(v.chat, v)
    _judge_like_hand_calc(v)
    v.finish()
    QApplication.processEvents()
    assert v.width() <= 1366 and v.height() <= 768
    out = [b.text() for b in v.findChildren(QPushButton) if b.isVisible() and not _inside(b, v)]
    assert out == [] and _inside(v.result_label, v) and v.result_label.isVisible()
    assert v.chat.view.height() >= 150                       # 정답 줄을 볼 자리가 남는다
