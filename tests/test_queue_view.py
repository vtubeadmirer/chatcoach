"""큐 모드 시험 (app/ui/queue_view.py, 02 7-4).

화면 없이(QT_QPA_PLATFORM=offscreen) 합성 채팅(본문 'q…' 자리표시)으로 표본을 뽑아 판정해 본다. 실제 채팅·닉네임은 쓰지 않는다.
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.core import sampling as sp  # noqa: E402
from app.ui import queue_view as qv  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = "00112233445566778899aabbccddeeff"
SEG_EID = "7f3c2a1b-0000-4000-8000-000000000001"
SEG_VID = "7f3c2a1b-0000-4000-8000-000000000002"
OFFSET = 2_500                         # 영상 파일 시각 = VOD + 2.5초
SID = "L01-P-20261021-a1b2"
SECRET = "ENGINE-DIRECT-MARKER"        # 블라인드 시험: 화면 어디에도 나오면 안 되는 값


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def cid(i):
    return "c%020x" % i


def synth_messages(n=300, span=600_000):
    out = []
    for i in range(n):
        out.append({"chat_id": cid(i), "t_ms": 60_000 + i * span // n, "kind": "text", "status": "NORMAL",
                    "viewer_id": "vw_" + "abcdefghijkl"[: 11] + "abcdefghijklmnopqrstuvwxyz234567"[i % 32],
                    "is_anonymous": False, "text": f"q{i}",
                    # 블라인드 시험용: 화면이 쓰면 안 되는 키
                    "engine_grade": SECRET, "origin": SECRET, "flags": [SECRET]})
    return out


def build(msgs, n=20):
    pop = sp.build_population(msgs, 60_000, 660_000, [(55_000, 65_000)])
    return sp.build_manifest(
        project_id="11111111-2222-4333-8444-555555555555",
        source_segment={"session_id": SID, "entity_id": SEG_EID, "version_id": SEG_VID,
                        "start_ms": 60_000, "end_ms": 660_000},
        guide_version="0.1", thresholds_sha256="a" * 64, sample_seed=SEED, draw_no=1, population=pop,
        messages_sha256="b" * 64, n=n, census_max=n, drawn_at="2026-10-21T14:03:11+09:00", drawn_by="L01",
        app_version="0.1.0")


class FakePlayer:
    def __init__(self):
        self.calls = []
        self.pos = 0

    def set_ab_loop(self, a, b): self.calls.append(("ab", a, b))
    def seek(self, ms, exact=True): self.calls.append(("seek", ms))
    def play(self): self.calls.append(("play",))
    def show_text(self, text, duration_ms=2000): self.calls.append(("osd", text))
    def position_ms(self): return self.pos


def reaction_checker():
    from app.store import LabelSchema
    with open(os.path.join(ROOT, "schemas", "label-event.schema.json"), encoding="utf-8") as f:
        root = json.load(f)
    s = LabelSchema(root)
    return lambda body: s._check(body, root["$defs"]["reactionBody"], "$.body")[0]


class Saver:
    def __init__(self, fail=False):
        self.drafts, self.fail, self.n = [], fail, 0

    def __call__(self, draft):
        if self.fail:
            raise ValueError("읽기 전용")
        self.drafts.append(draft)
        self.n += 1
        return {"id": "00000000-0000-4000-8000-%012d" % self.n,
                "entity_id": draft.get("entity_id") or "00000000-0000-4000-9000-%012d" % self.n,
                "session_id": SID, "kind": "reaction", "body": draft["body"]}


def make_view(qapp, session_kind="primary", n=20, reactions=(), saver=None, verify=None, player=None, **kw):
    msgs = synth_messages()
    m = build(msgs, n)
    saver = saver or Saver()
    cancels = []
    v = qv.QueueView(saver, lambda: cancels.append(1), verify=verify)
    v.cancels = cancels
    v.load_context(manifest=m, messages=msgs, session_kind=session_kind, session_id=SID, reactions=reactions,
                   viewer_no={}, player=player, to_file=lambda ms: ms + OFFSET, to_vod=lambda ms: ms - OFFSET, **kw)
    return v, m, msgs, saver


def all_text(w):
    parts = [lab.text() for lab in w.findChildren(QLabel)]
    return "\n".join(parts)


# --------------------------------------------------------------------------- 들어가기·범위 반복
def test_enter_sets_loop_and_autoplay(qapp):
    p = FakePlayer()
    ranges = []
    msgs = synth_messages()
    m = build(msgs)
    v = qv.QueueView(Saver(), lambda: None)
    v.rangeRequested.connect(lambda a, b: ranges.append((a, b)))
    v.load_context(manifest=m, messages={x["chat_id"]: x for x in msgs}, session_kind="primary", session_id=SID,
                   player=p, to_file=lambda ms: ms + OFFSET)
    it = v.current_item()
    assert it["order"] == 1
    a, b = max(0, it["t_ms"] - 10_000), it["t_ms"] + 30_000
    assert ranges[-1] == (a, b)
    assert ("ab", a + OFFSET, b + OFFSET) in p.calls and ("seek", a + OFFSET) in p.calls and ("play",) in p.calls
    osd = [c for c in p.calls if c[0] == "osd"]
    assert osd and f"q{int(it['chat_id'][1:], 16)}" in osd[-1][1]
    assert "진행 0 / 20" in v.head.text() and "적격" in v.head.text()
    assert "draw 1" in v.meta.text() and "rev 1" in v.meta.text()
    v.toggle_loop()
    assert p.calls[-1] == ("ab", None, None)


# --------------------------------------------------------------------------- 블라인드
def test_blind_nothing_from_engine_or_other_sessions(qapp):
    msgs = synth_messages()
    m = build(msgs)
    first = m["items"][0]["chat_id"]
    second = m["items"][1]["chat_id"]
    reactions = [
        # 다른 세션의 표본 판정 → 무시
        {"id": "x", "entity_id": "y", "session_id": "L02-P-20261021-zzzz",
         "body": {"chat_id": first, "entry": "from_sample", "sample_ref": {"sample_set_id": m["sample_set_id"], "order": 1}}},
        # 자기 세션 2단계 반응 → 2단계 연결 표시는 숨기므로 판정함으로 세지 않는다
        {"id": "x2", "entity_id": "y2", "session_id": SID,
         "body": {"chat_id": second, "entry": "from_utterance", "grade": "direct", "rationale": SECRET}},
    ]
    v = qv.QueueView(Saver(), lambda: None)
    v.load_context(manifest=m, messages=msgs, session_kind="primary", session_id=SID, reactions=reactions)
    assert v.state_of(first) == sp.PENDING and v.state_of(second) == sp.PENDING
    assert v.current_index() == 0
    for i in range(len(m["items"])):
        v.go(i)
        assert SECRET not in all_text(v)
    # 채팅 dict 의 다른 키는 들고 있지 않는다
    assert all(set(x) <= {"chat_id", "t_ms", "text", "viewer_id", "is_anonymous", "kind"} for x in v._msgs.values())
    # 등급 고르기 단추가 없다 (등급을 미리 고르지 않음)
    names = " ".join(b.text() for b in v.findChildren(type(v.react_btn)))
    assert "직접" not in names and "높음" not in names


def test_relabel_session_cannot_see_samples(qapp):
    v, *_ = make_view(qapp, session_kind="relabel_same")
    assert v.manifest is None and v.current_item() is None
    assert "볼 수 없습니다" in v.head.text()
    assert not v.react_btn.isEnabled()


def test_adjudication_reads_but_cannot_judge(qapp):
    v, m, _, saver = make_view(qapp, session_kind="adjudication")
    assert v.manifest is not None and v.current_item() is not None
    assert not v.unconf_btn.isEnabled()
    v.set_confidence("certain")
    v.rationale.setText("x")
    assert v.judge_unconfirmed() is None and saver.drafts == []


def test_unknown_session_blocked(qapp):
    v, *_ = make_view(qapp, session_kind=None)
    assert v.manifest is None


# --------------------------------------------------------------------------- 판정
def test_unconfirmed_requires_confidence_and_rationale(qapp):
    v, m, _, saver = make_view(qapp)
    assert v.judge_unconfirmed() is None and "확신도" in v.msg.text()
    v.set_confidence("certain")
    assert v.judge_unconfirmed() is None and "근거" in v.msg.text()
    assert saver.drafts == []


def test_unconfirmed_save_valid_and_advances(qapp):
    check = reaction_checker()
    v, m, _, saver = make_view(qapp)
    v.set_confidence("probably")
    v.rationale.setText("01:02:03 답 없음")
    rec = v.judge_unconfirmed()
    assert rec is not None
    d = saver.drafts[0]
    assert d["kind"] == "reaction" and "entity_id" not in d
    b = d["body"]
    assert check(b) == []
    assert b["entry"] == "from_sample" and b["result"] == "unconfirmed" and b["confidence"] == "probably"
    assert b["sample_ref"] == {"sample_set_id": m["sample_set_id"], "order": 1}
    assert b["segment_id"] == SEG_EID and b["first_visible_ms"] == m["items"][0]["t_ms"]
    assert v.current_index() == 1
    assert v.state_of(m["items"][0]["chat_id"]) == sp.JUDGED
    assert "진행 1 / 20" in v.head.text()
    # 다음 표본에서는 확신도·근거가 비어 있다
    assert v.confidence() is None and v.rationale.text() == ""


def test_undeterminable(qapp):
    check = reaction_checker()
    v, m, _, saver = make_view(qapp)
    v.rationale.setText("소리 깨짐")
    assert v.judge_undeterminable() is not None
    b = saver.drafts[0]["body"]
    assert b["confidence"] == "undeterminable" and b["result"] == "unconfirmed" and check(b) == []


def test_rejudge_own_sample_label_supersedes(qapp):
    msgs = synth_messages()
    m = build(msgs)
    first = m["items"][0]["chat_id"]
    eid, lid = "00000000-0000-4000-8000-0000000000aa", "00000000-0000-4000-8000-0000000000bb"
    reactions = [{"id": lid, "entity_id": eid, "session_id": SID,
                  "body": {"chat_id": first, "entry": "from_sample",
                           "sample_ref": {"sample_set_id": m["sample_set_id"], "order": 1}}}]
    saver = Saver()
    v = qv.QueueView(saver, lambda: None)
    v.load_context(manifest=m, messages=msgs, session_kind="primary", session_id=SID, reactions=reactions,
                   start_at=first)
    assert v.state_of(first) == sp.JUDGED and v.current_item()["chat_id"] == first
    assert "판정함" in v.item_line.text()
    v.set_confidence("certain")
    v.rationale.setText("다시 봄")
    v.judge_unconfirmed()
    assert saver.drafts[0]["entity_id"] == eid and saver.drafts[0]["supersedes"] == lid
    # 처음 열면 첫 '대기' 표본에서 시작
    v2 = qv.QueueView(Saver(), lambda: None)
    v2.load_context(manifest=m, messages=msgs, session_kind="primary", session_id=SID, reactions=reactions)
    assert v2.current_index() == 1


def test_save_failure_keeps_item(qapp):
    v, m, _, saver = make_view(qapp, saver=Saver(fail=True))
    v.set_confidence("certain")
    v.rationale.setText("x")
    assert v.judge_unconfirmed() is None
    assert v.current_index() == 0 and "저장하지 못했습니다" in v.msg.text()


def test_reaction_editor_request_and_mark_saved(qapp):
    v, m, _, _ = make_view(qapp)
    got = []
    v.reactionEditorRequested.connect(got.append)
    ctx = v.request_reaction_editor()
    assert got == [ctx]
    assert ctx["entry"] == "from_sample" and ctx["sample_ref"]["order"] == 1 and ctx["segment_id"] == SEG_EID
    assert "grade" not in ctx and "eligible_grades" not in ctx
    v.mark_saved({"id": "i", "entity_id": "e", "body": {"chat_id": ctx["chat_id"], "entry": "from_sample"}})
    assert v.current_index() == 1 and v.state_of(ctx["chat_id"]) == sp.JUDGED


def test_all_judged_signal_and_navigation(qapp):
    v, m, _, saver = make_view(qapp, n=3)
    done = []
    v.allJudged.connect(lambda: done.append(1))
    assert not v.prev_btn.isEnabled()
    assert v.next() and v.prev() and not v.prev()
    for _ in range(3):
        v.set_confidence("certain")
        v.rationale.setText("x")
        v.judge_unconfirmed()
    assert done == [1] and len(saver.drafts) == 3
    assert not v.pending_btn.isEnabled()
    assert "진행 3 / 3" in v.head.text()


def test_next_pending_wraps(qapp):
    v, m, _, _ = make_view(qapp, n=3)
    v.go(2)
    v.mark_saved({"body": {"chat_id": m["items"][2]["chat_id"]}})
    # 끝에서 저장하면 앞쪽 대기로 돈다
    assert v.current_index() == 0


def test_insert_time_uses_player_vod(qapp):
    p = FakePlayer()
    v, m, _, _ = make_view(qapp, player=p)
    p.pos = 3_723_000 + OFFSET            # 파일 시각 → VOD 1:02:03
    v.insert_time()
    assert v.rationale.text().startswith("01:02:03")


def test_verify_button(qapp):
    msgs = synth_messages()
    bufs = [(55_000, 65_000)]
    v, m, _, _ = make_view(qapp, verify=lambda man: sp.verify_manifest(man, msgs, bufs))
    assert v.verify_btn.isEnabled()
    r = v.run_verify()
    assert r.ok and "통과" in v.verify_msg.text()
    v2, *_ = make_view(qapp, verify=lambda man: sp.verify_manifest(man, msgs[:-1], bufs))
    r2 = v2.run_verify()
    assert not r2.ok and "실패" in v2.verify_msg.text()
    v3, *_ = make_view(qapp)
    assert not v3.verify_btn.isEnabled()


def test_shortcuts(qapp):
    v, m, _, saver = make_view(qapp)
    v.show()
    v.activateWindow()
    QApplication.processEvents()
    QTest.keyClick(v, Qt.Key_1, Qt.AltModifier)
    QApplication.processEvents()
    if v.confidence() is None:
        pytest.skip("offscreen 에서 QShortcut 이 키를 받지 못함")
    assert v.confidence() == "certain"
    v.rationale.setText("x")
    QTest.keyClick(v, Qt.Key_5, Qt.ControlModifier)
    QApplication.processEvents()
    assert len(saver.drafts) == 1 and v.current_index() == 1
    QTest.keyClick(v, Qt.Key_PageUp)
    QApplication.processEvents()
    assert v.current_index() == 0
    QTest.keyClick(v, Qt.Key_Escape)
    QApplication.processEvents()
    assert v.cancels == [1]
    v.close()


def test_no_manifest(qapp):
    v = qv.QueueView(Saver(), lambda: None)
    v.load_context(manifest=None, session_kind="primary")
    assert "표본 없음" in v.head.text() and v.request_reaction_editor() is None


# --------------------------------------------------------------------------- 매니페스트 파일
def test_manifest_file_roundtrip(tmp_path):
    msgs = synth_messages()
    m = build(msgs)
    d = str(tmp_path / "프로젝트" / "samples")
    p = qv.write_manifest_file(d, m)
    assert os.path.basename(p) == "S-7f3c2a1b-d1-r1.json"
    assert qv.read_manifest_file(p) == m
    with pytest.raises(FileExistsError):
        qv.write_manifest_file(d, m)
    with open(os.path.join(d, "S-00000000-d1-r1.json"), "w", encoding="utf-8") as f:
        f.write("{broken")
    got = qv.list_manifests(d)
    assert [x["sample_set_id"] for x in got] == ["S-7f3c2a1b-d1-r1"]
    with pytest.raises(sp.SamplingError):
        qv.manifest_path(d, "../evil")
    assert not [n for n in os.listdir(d) if n.endswith(".tmp")]
