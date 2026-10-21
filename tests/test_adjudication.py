"""조정 (app/core/adjudicate.py, app/ui/adjudication_view.py).

설계: 03 6-12(adjudication 본문)·12-1(조정 세션은 잠긴 세션을 모두 읽음), 06 5장(pattern_tag 3건 이상 → 개정 후보)·
7-1(계획 취소는 검토자 사유), 08 D-12 3(조정자 = 라벨러 표시)·4(두 근거 나란히)·7(계획 취소 기록).
합성 데이터만 쓴다(지어낸 근거 문장, 실제 채팅·닉네임·프로젝트 없음). 프로젝트는 tmp_path 에 새로 만든다.
"""
import copy
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app import project, relabel, session, store  # noqa: E402
from app.core import adjudicate as adj  # noqa: E402
from app.core import agreement as ag  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SA, SB, SC = "L01-P-20261021-ab12", "L02-R2-20261022-cd34", "L03-R2-20261023-ef56"
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 6, 0, 0, tzinfo=KST)
SEC = 1000


def _uuid(n):
    return f"{n:08x}-0000-4000-8000-{n:012x}"


_N = [0]


def _rec(sid, kind, body):
    _N[0] += 1
    return {"id": _uuid(10_000 + _N[0]), "entity_id": _uuid(20_000 + _N[0]), "kind": kind, "seq": _N[0],
            "session_id": sid, "labeler_id": sid.split("-")[0], "body": body}


def rx(sid, chat, grade=None, *, result="graded", conf="certain", t=None, why="합성 근거: 00:10 화면 반응"):
    b = {"chat_id": chat, "entry": "from_sample", "result": result, "grade": grade, "confidence": conf,
         "rationale": why, "first_visible_ms": t if t is not None else 1000}
    return _rec(sid, "reaction", b)


def seg(sid, a, z, cat, why="합성 구간 근거"):
    return _rec(sid, "segment", {"start_ms": a, "end_ms": z, "category": cat, "confidence": "certain",
                                 "rationale": why})


def side(sid, segments=(), reactions=(), scope=None):
    return {"session_id": sid, "labeler_id": sid.split("-")[0], "scope": scope, "segments": list(segments),
            "reactions": list(reactions)}


# ============================================================================ 표본 불일치

def _sample_sides():
    a = [rx(SA, "c1", "direct", t=5000), rx(SA, "c2", "likely", t=6000), rx(SA, "c3", "unclear", t=7000),
         rx(SA, "c4", "direct", t=8000, conf="undeterminable"), rx(SA, "c5", "direct", t=9000),
         rx(SA, "c6", None, result="unconfirmed", t=4000)]
    b = [rx(SB, "c1", "likely", t=5000, why="다른 합성 근거"), rx(SB, "c2", "likely", t=6000),
         rx(SB, "c3", "direct", t=7000), rx(SB, "c4", "direct", t=8000), rx(SB, "c6", "related_only", t=4000)]
    return side(SA, reactions=a), side(SB, reactions=b)


def test_sample_cases_follow_agreement_pairs_and_add_one_sided_undeterminable():
    sa, sb = _sample_sides()
    res = ag.compare_samples(sa["reactions"], sb["reactions"])
    cases = adj.sample_cases(res, sa, sb)
    # 시각 순: c6(4초) · c1(5초) · c3(7초) · c4(8초). c2 는 같음, c5 는 A 만 판정
    assert [c.chat_id for c in cases] == ["c6", "c1", "c3", "c4"]
    assert [c.kind for c in cases] == [adj.C_GRADE, adj.C_GRADE, adj.C_UNCLEAR, adj.C_UNDETERMINABLE]
    c1 = cases[1]
    assert c1.case_id == "reaction:c1" and c1.target_kind == "reaction"
    # 나란히: 두 판정·확신도·근거 한 줄 (D-12 4)
    assert [(s.session_id, s.value, s.confidence) for s in c1.sides] == [(SA, "direct", "certain"), (SB, "likely", "certain")]
    assert [s.rationale for s in c1.sides] == ["합성 근거: 00:10 화면 반응", "다른 합성 근거"]
    assert [t["session_id"] for t in c1.targets()] == [SA, SB]
    assert all(set(t) == {"session_id", "entity_id", "version_id"} for t in c1.targets())
    # 한쪽만 판단 불가를 빼면 agreement 목록과 같다
    no_und = adj.sample_cases(res, sa, sb, include_undeterminable=False)
    assert [c.chat_id for c in no_und] == sorted((d["chat_id"] for d in res["disagreements"]),
                                                 key=lambda x: {"c6": 0, "c1": 1, "c3": 2}[x])
    # 입력 레코드는 고치지 않는다
    before = copy.deepcopy(sa)
    adj.sample_cases(res, sa, sb)
    assert sa == before


def test_sample_cases_respect_chat_ids_and_scope_for_undeterminable():
    sa, sb = _sample_sides()
    res = ag.compare_samples(sa["reactions"], sb["reactions"], chat_ids=["c1"])
    cases = adj.sample_cases(res, sa, sb, chat_ids=["c1"])
    assert [c.chat_id for c in cases] == ["c1"]
    res = ag.compare_samples(sa["reactions"], sb["reactions"], scope=[(0, 7500)])
    cases = adj.sample_cases(res, sa, sb, scope=[(0, 7500)])
    assert "c4" not in [c.chat_id for c in cases]       # 8초 — 범위 밖


# ============================================================================ 구간 불일치

def test_segment_boundary_within_buffer_is_not_a_case():
    a = side(SA, [seg(SA, 0, 60 * SEC, "just_chatting"), seg(SA, 60 * SEC, 120 * SEC, "game")])
    b = side(SB, [seg(SB, 0, 65 * SEC, "just_chatting"), seg(SB, 65 * SEC, 120 * SEC, "game")])
    res = ag.compare_segments(a["segments"], b["segments"])
    assert adj.segment_cases(res, a, b, buffer_ms=10 * SEC) == []
    cs = adj.segment_cases(res, a, b, buffer_ms=10 * SEC, include_within_buffer=True)
    assert len(cs) == 1 and cs[0].kind == adj.C_BOUNDARY and cs[0].delta_ms == 5 * SEC


def test_segment_boundary_over_buffer_and_category_cases():
    a = side(SA, [seg(SA, 0, 60 * SEC, "just_chatting"), seg(SA, 60 * SEC, 120 * SEC, "game"),
                  seg(SA, 120 * SEC, 200 * SEC, "singing")])
    b = side(SB, [seg(SB, 0, 80 * SEC, "just_chatting"), seg(SB, 80 * SEC, 120 * SEC, "game"),
                  seg(SB, 120 * SEC, 200 * SEC, "other")])
    res = ag.compare_segments(a["segments"], b["segments"])
    cs = adj.segment_cases(res, a, b, buffer_ms=10 * SEC)
    assert [(c.kind, c.spans) for c in cs] == [(adj.C_BOUNDARY, ((60 * SEC, 80 * SEC),)),
                                               (adj.C_CATEGORY, ((120 * SEC, 200 * SEC),))]
    bd = cs[0]
    assert bd.delta_ms == 20 * SEC and bd.case_id == f"segment:{60 * SEC}-{80 * SEC}"
    # 경계 건의 양쪽: A 는 그 칸을 game(오른쪽 구간), B 는 just_chatting(왼쪽 구간)으로 봄
    assert [(s.value, s.start_ms, s.end_ms) for s in bd.sides] == [("game", 60 * SEC, 120 * SEC),
                                                                  ("just_chatting", 0, 80 * SEC)]
    assert [s.value for s in cs[1].sides] == ["singing", "other"]


def test_segment_cases_use_compare_ranges_and_group_same_pair():
    # 두 블록 범위로 끊긴 같은 두 구간 판의 분류 차이 → 한 건(spans 둘)
    a = side(SA, [seg(SA, 0, 1200 * SEC, "game")], scope=[(0, 600 * SEC)])
    b = side(SB, [seg(SB, 0, 1200 * SEC, "singing")])
    sc = [(0, 100 * SEC), (300 * SEC, 400 * SEC)]
    res = ag.compare_segments(a["segments"], b["segments"], scope_a=sc)
    cs = adj.segment_cases(res, a, b)
    assert len(cs) == 1 and cs[0].spans == ((0, 100 * SEC), (300 * SEC, 400 * SEC))
    # 한쪽만 구간이 있는 곳은 불일치가 아니다(덮임 차이)
    b2 = side(SB, [seg(SB, 0, 50 * SEC, "game")])
    res = ag.compare_segments(a["segments"], b2["segments"])
    assert adj.segment_cases(res, a, b2) == []


def test_build_cases_from_build_agreement():
    sa, sb = _sample_sides()
    # 채팅(4~9초)은 저스트채팅 구간 [0, 300초) 안. 300초 뒤 분류가 갈린다
    sa["segments"] = [seg(SA, 0, 300 * SEC, "just_chatting"), seg(SA, 300 * SEC, 600 * SEC, "game")]
    sb["segments"] = [seg(SB, 0, 300 * SEC, "just_chatting"), seg(SB, 300 * SEC, 600 * SEC, "singing")]
    res = ag.build_agreement(sa, sb, settings={"agreement": {"bootstrap_n": 0}}, buffer_ms=1 * SEC)
    cases = adj.build_cases(res, sa, sb)
    assert [c.target_kind for c in cases].count("segment") == 1
    assert [c.chat_id for c in cases if c.target_kind == "reaction"] == ["c6", "c1", "c3", "c4"]
    assert cases[-1].target_kind == "segment"           # 시각 300초가 마지막


def test_build_cases_skips_judgments_outside_current_sample():
    """D-12 2: 지금 구간표로 표본 자리가 아닌 판정은 일치도에서 빠진다 — 조정 목록에도 넣지 않는다(판단 불가 짝 포함)."""
    sa, sb = _sample_sides()
    sa["segments"] = [seg(SA, 0, 600 * SEC, "game")]
    sb["segments"] = [seg(SB, 0, 600 * SEC, "game")]
    res = ag.build_agreement(sa, sb, settings={"agreement": {"bootstrap_n": 0}})
    assert [c for c in adj.build_cases(res, sa, sb) if c.target_kind == "reaction"] == []


def test_merge_cases_for_three_sessions():
    a = side(SA, reactions=[rx(SA, "x1", "direct")])
    b = side(SB, reactions=[rx(SB, "x1", "likely")])
    c = side(SC, reactions=[rx(SC, "x1", "direct")])
    lists = []
    for p, q in ((a, b), (a, c), (b, c)):
        lists.append(adj.sample_cases(ag.compare_samples(p["reactions"], q["reactions"]), p, q))
    merged = adj.merge_cases(lists)
    assert len(merged) == 1 and [s.session_id for s in merged[0].sides] == [SA, SB, SC]
    assert len(merged[0].targets()) == 3


# ============================================================================ 본문 (adjudicationBody)

def _schema_check(body):
    with open(os.path.join(ROOT, "schemas", "label-event.schema.json"), encoding="utf-8") as f:
        root = json.load(f)
    sub = {"$defs": root["$defs"], **root["$defs"]["adjudicationBody"]}
    return store.LabelSchema(sub).check(body)


def _one_case():
    sa, sb = _sample_sides()
    return adj.sample_cases(ag.compare_samples(sa["reactions"], sb["reactions"]), sa, sb)[1]   # c1


def test_make_body_pick_exclude_ambiguous_match_schema():
    c = _one_case()
    b = adj.make_body(c, decision="final", cause="simple_mistake", pick=SB, pattern_tag="  호명  뒤   지연 ", note=" 메모 ")
    assert b["decision"] == "final" and b["final"] == c.side(SB).body and b["final"]["grade"] == "likely"
    assert b["pattern_tag"] == "호명 뒤 지연" and b["note"] == "메모" and b["target_kind"] == "reaction"
    assert _schema_check(b) == []
    for d in ("exclude_from_eval", "keep_as_ambiguous"):
        b = adj.make_body(c, decision=d, cause="truly_ambiguous")
        assert "final" not in b and b["pattern_tag"] is None and _schema_check(b) == []


@pytest.mark.parametrize("kw,msg", [
    ({"decision": "final", "cause": "other"}, "하나로"),
    ({"decision": "final", "cause": "other", "pick": SA, "final": {"chat_id": "c1"}}, "하나로"),
    ({"decision": "exclude_from_eval", "cause": "other", "pick": SA}, "주지 않습니다"),
    ({"decision": "final", "cause": None, "pick": SA}, "원인"),
    ({"decision": "nope", "cause": "other"}, "decision"),
    ({"decision": "final", "cause": "other", "final": {"chat_id": "zz"}}, "chat_id"),
    ({"decision": "final", "cause": "other", "pick": SC}, "판정이 없습니다"),
    ({"decision": "keep_as_ambiguous", "cause": "other", "pattern_tag": "가" * 61}, "60자"),
])
def test_make_body_refuses(kw, msg):
    with pytest.raises(adj.AdjudicationError, match=msg):
        adj.make_body(_one_case(), **kw)


def test_segment_new_value_and_reaction_has_none():
    a = side(SA, [seg(SA, 0, 60 * SEC, "game")])
    b = side(SB, [seg(SB, 0, 60 * SEC, "singing")])
    c = adj.segment_cases(ag.compare_segments(a["segments"], b["segments"]), a, b)[0]
    fin = adj.segment_final(c, SA, category="other", end_ms=55 * SEC)
    assert fin["category"] == "other" and fin["end_ms"] == 55 * SEC and fin["rationale"] == "합성 구간 근거"
    body = adj.make_body(c, decision="final", cause="guide_unclear", final=fin)
    assert _schema_check(body) == [] and body["target_kind"] == "segment"
    with pytest.raises(adj.AdjudicationError, match="끝 > 시작"):
        adj.segment_final(c, SA, start_ms=70 * SEC)
    with pytest.raises(adj.AdjudicationError, match="구간 건에서만"):
        adj.segment_final(_one_case(), SA, category="game")
    # active_chat 은 game 일 때만
    c.side(SA).body["active_chat"] = True
    assert "active_chat" not in adj.segment_final(c, SA, category="singing")


def test_make_draft_new_and_revision():
    c = _one_case()
    body = adj.make_body(c, decision="keep_as_ambiguous", cause="truly_ambiguous")
    assert adj.make_draft(c, body) == {"kind": "adjudication", "body": body}
    prev = {"id": _uuid(1), "entity_id": _uuid(2)}
    d = adj.make_draft(c, body, prev)
    assert d["entity_id"] == _uuid(2) and d["supersedes"] == _uuid(1)


# ============================================================================ 상태·집계·개정 후보·조정자 표시

def _adj_rec(case, body, seq, sid="L01-AD-20261024-gh78"):
    return {"id": _uuid(900 + seq), "entity_id": _uuid(800 + seq), "kind": "adjudication", "seq": seq,
            "session_id": sid, "body": body}


def test_case_states_summary_and_stale():
    sa, sb = _sample_sides()
    cases = adj.sample_cases(ag.compare_samples(sa["reactions"], sb["reactions"]), sa, sb)
    b0 = adj.make_body(cases[0], decision="exclude_from_eval", cause="other")
    b1 = adj.make_body(cases[1], decision="final", cause="simple_mistake", pick=SA)
    stale = copy.deepcopy(b1)
    stale["targets"][0]["version_id"] = _uuid(5)
    st = adj.case_states(cases, [_adj_rec(cases[0], b0, 1), _adj_rec(cases[1], stale, 2), _adj_rec(cases[1], b1, 3)])
    assert st[cases[0].case_id].decided and not st[cases[0].case_id].stale
    assert st[cases[1].case_id].record["seq"] == 3 and not st[cases[1].case_id].stale   # 나중 기록
    assert not st[cases[2].case_id].decided
    s = adj.summary(cases, st)
    assert s == {"total": 4, "decided": 2, "pending": 2, "stale": 0,
                 "by_decision": {"exclude_from_eval": 1, "final": 1}, "by_cause": {"other": 1, "simple_mistake": 1},
                 "by_kind": {"grade": 2, "unclear": 1, "undeterminable": 1}}
    st = adj.case_states(cases, [_adj_rec(cases[1], stale, 9)])
    assert st[cases[1].case_id].stale and adj.summary(cases, st)["stale"] == 1


def test_revision_candidates_need_three_same_tags_case_insensitive():
    c = _one_case()
    recs = []
    for i, tag in enumerate(["Delay after call", "delay  after call", "DELAY AFTER CALL", "other tag", "other tag", None]):
        recs.append(_adj_rec(c, adj.make_body(c, decision="keep_as_ambiguous", cause="guide_unclear", pattern_tag=tag), i))
    got = adj.revision_candidates(recs)
    assert got == [{"pattern_tag": "Delay after call", "count": 3, "by_target_kind": {"reaction": 3},
                    "by_cause": {"guide_unclear": 3}}]
    assert adj.revision_candidates(recs, min_count=2)[1]["pattern_tag"] == "other tag"
    assert adj.known_tags(recs) == ["Delay after call", "other tag"]


def test_header_marks_adjudicator_equals_labeler():
    h = adj.header("L01", ["L01", "L02"])
    assert h["adjudicator_is_labeler"] and "조정자 = 라벨러" in h["banner"]
    h = adj.header("R01", ["L01", "L02"])
    assert not h["adjudicator_is_labeler"] and h["banner"] is None
    assert adj.adjudicator_is_labeler("L01", [_one_case().sides[0].labeler_id])


def test_core_has_no_io_or_qt():
    with open(os.path.join(ROOT, "app", "core", "adjudicate.py"), encoding="utf-8") as f:
        src = f.read()
    for bad in ("import os", "open(", "PySide6", "datetime", "random", "time."):
        assert bad not in src, bad


# ============================================================================ 화면 + 진짜 store (조정 세션)

pytest.importorskip("PySide6")


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
        return (1, "SYNTH-HOST", "chatcoach")


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        self.t += timedelta(milliseconds=10)
        return self.t


MID = "aaaaaaaa-0000-4000-8000-000000000001"
MID2 = "bbbbbbbb-0000-4000-8000-000000000002"
P1, P2, AD = "L01-P-20261021-ab12", "L02-P-20261021-cd34", "L01-AD-20261024-ef56"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


def _ctx(sid, kind, labeler, mid=MID):
    return session.SessionContext(labeler, kind, sid, session.machine4(mid), machine_id=mid,
                                  session_created_at="2026-10-21T06:00:00+09:00")


def _open(root, c):
    return store.ProjectStore.open(root, c, FakeLock, clock=Clock(), app_version="0.0.1")


def _sb(a, z, cat):
    return {"start_ms": a, "end_ms": z, "category": cat, "confidence": "certain", "rationale": "합성 근거 한 줄"}


@pytest.fixture
def proj(tmp_path):
    FakeLock.held = set()
    p = project.create(str(tmp_path / "프로젝트"), "합성 방송", 123, "시험", "L01", "0.0.1", NOW,
                       "11111111-2222-4333-8444-555555555555", bytes(range(32)),
                       guide=project.GuideSource("0.1", b"# guide\n", {"segment": {"boundary_buffer_sec": 10}}),
                       machine4="m3fa")
    root = p.root
    for sid, lab, mid, segs in ((P1, "L01", MID, [(0, 60 * SEC, "just_chatting"), (60 * SEC, 120 * SEC, "game")]),
                                (P2, "L02", MID2, [(0, 90 * SEC, "just_chatting"), (90 * SEC, 120 * SEC, "game")])):
        st = _open(root, _ctx(sid, "primary", lab, mid))
        for a, z, cat in segs:
            st.append_label({"kind": "segment", "body": _sb(a, z, cat)})
        st.lock_session()
        st.close()
    return root


def test_view_lists_case_side_by_side_and_saves_adjudication(qapp, proj):
    from app.ui import adjudication_view as av
    st = _open(proj, _ctx(AD, "adjudication", "L01"))
    try:
        assert not st.read_only, st.read_only_reasons
        v = av.AdjudicationView(store=st, app_version="0.0.1")
        assert v.checked_sessions() == [P1, P2]
        assert v.load_cases() == 1
        assert "조정자 = 라벨러" in v.banner.text()          # D-12 3
        c = v.current
        assert c.kind == adj.C_BOUNDARY and c.delta_ms == 30 * SEC
        texts = v.side_texts(c)
        assert [t["session_id"] for t in texts] == [P1, P2] and all(t["rationale"] == "합성 근거 한 줄" for t in texts)
        # 원인을 안 고르면 막힘
        assert v.save_current() is None and "원인" in v.last_error
        v.cause_box.setCurrentIndex(v.cause_box.findData("simple_mistake"))
        v.tag_edit.setText("경계 늦게 찍음")
        rec = v.save_current()
        assert rec is not None and rec["kind"] == "adjudication" and rec["supersedes"] is None
        b = rec["body"]
        assert b["decision"] == "final" and b["final"]["category"] == "game" and b["target_kind"] == "segment"
        assert {t["session_id"] for t in b["targets"]} == {P1, P2}
        assert "✓" in v.case_list.item(0).text()
        # 다시 저장하면 같은 조정의 새 판(supersedes)
        assert v.decision_box.currentData() == f"pick:{P1}"      # 다시 고르면 저장한 결정이 다시 보인다
        assert v.cause_box.currentData() == "simple_mistake" and v.tag_edit.text() == "경계 늦게 찍음"
        assert v.choose_decision("keep_as_ambiguous")
        rec2 = v.save_current()
        assert rec2 is not None, v.last_error
        assert rec2["entity_id"] == rec["entity_id"] and rec2["supersedes"] == rec["id"]
        assert "final" not in rec2["body"]
        # 새 값(구간만): 분류·범위
        assert v.choose_decision("new") and v.new_row.isVisibleTo(v)
        v.new_cat.setCurrentIndex(v.new_cat.findData("other"))
        v.new_end.setValue(70)
        rec3 = v.save_current()
        assert rec3["body"]["final"]["category"] == "other" and rec3["body"]["final"]["end_ms"] == 70 * SEC
        own = st.effective().items(kind="adjudication", session_id=AD)
        assert len(own) == 1 and own[0]["id"] == rec3["id"]
    finally:
        st.close()


def test_view_refuses_in_read_only_and_labeling_session_cannot_write_adjudication(qapp, proj):
    from app.ui import adjudication_view as av
    st = _open(proj, _ctx("L01-P-20261025-zz11", "primary", "L01"))
    try:
        assert st.readable_locked_sessions() == []            # 라벨링 세션은 아무것도 안 보임
        with pytest.raises(store.LabelValidationError):
            st.append_label({"kind": "adjudication", "body": {}})
        v = av.AdjudicationView(store=st)
        assert v.session_list.count() == 0 and not v.btn_load.isEnabled()
    finally:
        st.close()


def test_view_cancels_plan_with_reason(qapp, proj):
    from app.ui import adjudication_view as av
    st = _open(proj, _ctx(AD, "adjudication", "L01"))
    try:
        data = st.project.data
        plan = relabel.create_plan(proj, data, session_kind="relabel_second", labeler_id="L02", source_session_id=P1,
                                   done_ranges=[(0, 600 * SEC)], created_by="L01", created_machine4="aaaa",
                                   app_version="0.0.1", creator_kind="adjudication", now=NOW, rand4="ab12")
        v = av.AdjudicationView(store=st, app_version="0.0.1")
        assert v.plan_box.count() == 1 and v.plan_box.currentData() == plan["plan_id"]
        assert v.cancel_selected_plan() is None and "되돌릴 수 없음" in v.plan_msg.text()
        v.plan_confirm.setChecked(True)
        assert v.cancel_selected_plan() is None and "사유" in v.plan_msg.text()     # 사유 필수
        v.plan_reason.setText("시험용 계획이라 쓰지 않음")
        doc = v.cancel_selected_plan()
        assert doc["plan_id"] == plan["plan_id"] and doc["canceller_is_labeler"] is True
        assert relabel.is_cancelled(proj, plan["plan_id"]) and v.plan_box.count() == 0
        assert os.path.exists(relabel.plan_path(proj, plan["plan_id"]))           # 계획 파일은 그대로
    finally:
        st.close()
