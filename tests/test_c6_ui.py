"""C단계 여섯째 묶음 — 14 4-2 의 1·3·7·9 (08 D-13 2·5).

- (1) 조정 화면·반응 편집기가 1366x768 의 오른쪽 칸 폭(471px·731px)에서 가로로 잘리지 않음(오른쪽 밖 0, 가로 스크롤 없음,
  글자 잘린 단추·라벨 0). 계획 취소 칸은 조정 화면에 없음(메뉴만).
- (3) 이 라벨러가 볼 계획 가운데 읽지 못한 것이 있으면 잠긴 1차 다시 열기를 막음(app.session.reopen_refusal).
- (7) 조정 세션의 '출처 판정 대상 만들기': 1차 매니페스트 표본 chat_id → origin/OT-*.json(basis = sample_items).
  순수 함수 app.core.origin.sample_target_chat_ids·build_sample_targets, 파일 쓰기는 콜백.
- (9) 조정 세션에서 잠긴 1차를 골라 relabel_second 계획 만들기(app.relabel.create_second_plan + 조정 화면 칸).
합성 데이터만 쓴다(지어낸 chat_id·근거 문장, 실제 채팅·닉네임·프로젝트 없음). 프로젝트는 tmp_path 에 새로 만든다.
"""
import hashlib
import json
import os
import sys
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app import project, relabel, session, store  # noqa: E402
from app.core import origin as og  # noqa: E402
from tests.test_adjudication import NOW, Clock, FakeLock  # noqa: E402

SEC = 1000
MIN = 60 * SEC
MID = "aaaaaaaa-0000-4000-8000-000000000001"
MID2 = "bbbbbbbb-0000-4000-8000-000000000002"
P1, P2, AD = "L01-P-20261021-ab12", "L02-P-20261021-cd34", "L01-AD-20261024-ef56"
RS = "L02-R2-20261023-aa11"


def cid(n: int) -> str:
    return f"c{n:020x}"


def manifest(sid, e8, draw_no, rev, items):
    """모양만 갖춘 합성 매니페스트(대상 만들기에 필요한 칸만)."""
    return {"sample_set_id": f"S-{e8}-d{draw_no}-r{rev}", "draw_no": draw_no, "sample_rev": rev,
            "source_segment": {"session_id": sid, "entity_id": f"{e8}-0000-4000-8000-000000000000"},
            "items": [{"order": i, "chat_id": c, "t_ms": t, "role": "base"} for i, (c, t) in enumerate(items, 1)]}


# ============================================================================ (7) core: 1차 표본 → 대상
def test_sample_targets_use_latest_primary_manifests_in_time_order():
    ms = [
        manifest(P1, "aaaaaaaa", 1, 1, [(cid(1), 5000), (cid(2), 1000)]),         # 같은 계열의 옛 판 → 안 씀
        manifest(P1, "aaaaaaaa", 1, 2, [(cid(2), 1000), (cid(3), 3000)]),
        manifest(P2, "bbbbbbbb", 1, 1, [(cid(3), 3000), (cid(4), None)]),         # cid(3) 겹침, cid(4) 시각 없음 → 맨 뒤
        manifest(P2, "cccccccc", 1, 3, [(cid(9), 100)]),
        manifest(P2, "cccccccc", 2, 1, [(cid(5), 200)]),                          # draw_no 가 큰 판이 이김
        manifest(RS, "dddddddd", 1, 1, [(cid(6), 10)]),                           # 재라벨 세션 표본 → 뺌
        {"sample_set_id": "bad", "source_segment": {"session_id": P1}, "items": []},
        manifest(P1, "eeeeeeee", 1, 1, [("not-a-chat", 1), (cid(7), 50)]),
    ]
    pick = og.sample_target_chat_ids(ms)
    assert pick.chat_ids == (cid(7), cid(5), cid(2), cid(3), cid(4))
    assert pick.sessions == (P1, P2) and pick.skipped == 2 and pick.older == 2
    assert pick.bad_items == 1 and pick.duplicates == 1
    assert set(pick.manifests) == {"S-aaaaaaaa-d1-r2", "S-bbbbbbbb-d1-r1", "S-cccccccc-d2-r1", "S-eeeeeeee-d1-r1"}
    only = og.sample_target_chat_ids(ms, session_ids=[P1])
    assert only.chat_ids == (cid(7), cid(2), cid(3)) and only.sessions == (P1,)


def test_build_sample_targets_is_a_valid_immutable_target_file():
    ms = [manifest(P1, "aaaaaaaa", 1, 1, [(cid(2), 2000), (cid(1), 1000)])]
    obj, pick = og.build_sample_targets(ms, created_at="2026-10-24T10:00:00+09:00", created_by="L01")
    t = og.parse_targets(obj)
    assert obj["basis"] == og.SAMPLE_BASIS == "sample_items" and t.chat_ids == (cid(1), cid(2))
    assert obj["chat_ids_sha256"] == hashlib.sha256(f"{cid(1)}\n{cid(2)}".encode()).hexdigest()
    assert obj["target_id"] == f"OT-20261024-{obj['chat_ids_sha256'][:4]}"
    assert all(it["auto"] is None for it in obj["items"])
    assert "매니페스트 1개" in obj["note"]
    assert og.same_target_exists([t], obj) == t.target_id
    other, _ = og.build_sample_targets([manifest(P1, "aaaaaaaa", 1, 1, [(cid(3), 1)])],
                                       created_at="2026-10-25T10:00:00+09:00", created_by="L01")
    assert og.same_target_exists([t], other) is None
    with pytest.raises(og.OriginError):
        og.build_sample_targets([manifest(RS, "dddddddd", 1, 1, [(cid(1), 1)])],
                                created_at="2026-10-24T10:00:00+09:00", created_by="L01")


def test_origin_core_still_has_no_io_clock_or_qt():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app", "core", "origin.py"), encoding="utf-8") as f:
        src = f.read()
    for bad in ("import os", "open(", "PySide6", "import datetime", "from datetime", "random", "time."):
        assert bad not in src, bad


# ============================================================================ 화면 + 진짜 store
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
    """잠긴 1차 둘(L01·L02, 0~40분, 경계가 5분 어긋남). 계획을 만들 수 있게 길게."""
    FakeLock.held = set()
    p = project.create(str(tmp_path / "합성 프로젝트"), "합성 방송", 123, "시험", "L01", "0.0.1", NOW,
                       "11111111-2222-4333-8444-555555555555", bytes(range(32)),
                       guide=project.GuideSource("0.1", b"# guide\n", {"segment": {"boundary_buffer_sec": 10}}),
                       machine4="m3fa")
    for sid, lab, mid, cut in ((P1, "L01", MID, 20 * MIN), (P2, "L02", MID2, 25 * MIN)):
        st = _open(p.root, _ctx(sid, "primary", lab, mid))
        st.append_label({"kind": "segment", "body": _sb(0, cut, "just_chatting")})
        st.append_label({"kind": "segment", "body": _sb(cut, 40 * MIN, "game")})
        st.lock_session()
        st.close()
    return p.root


def _write_manifest(root, m):
    d = os.path.join(root, "samples")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, m["sample_set_id"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(m, f)


class _Writer:
    def __init__(self, root):
        self.dir = os.path.join(root, "origin")
        self.calls = 0

    def __call__(self, obj):
        from app.ui.origin_view import write_targets_file
        self.calls += 1
        return write_targets_file(self.dir, obj)


def _fit(w, app):
    """tools/gui_smoke._fit 와 같은 셈(오른쪽 밖·가로 스크롤·글자 잘림) + 고르기·숫자 칸도 오른쪽 밖을 센다."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QLabel, QLineEdit,
                                   QScrollArea)
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


# ---------------------------------------------------------------------------- (1) 1366x768 잘림
@pytest.mark.parametrize("width", [471, 731])
def test_adjudication_view_fits_right_pane_widths(qapp, proj, width):
    from app.ui import adjudication_view as av
    st = _open(proj, _ctx(AD, "adjudication", "L01"))
    try:
        v = av.AdjudicationView(store=st, app_version="0.0.1", write_origin_targets=_Writer(proj))
        assert not hasattr(v, "plan_box")                      # 계획 취소 칸은 메뉴 대화상자만(14 4-2 의 1)
        assert v.load_cases() >= 1
        assert v.choose_decision("new") and v.new_row.isVisibleTo(v)   # 가장 넓던 '새 값' 줄을 보이게
        v.tag_edit.setText("합성-유형-태그")
        v.resize(width, 640)
        v.show()
        got = _fit(v, qapp)
        assert got == {"hscroll": False, "clipped": 0, "outside": 0}, got
        from PySide6.QtCore import Qt
        assert width < av.NARROW_PX and v.split.orientation() == Qt.Vertical   # 좁으면 위아래
        v.resize(1100, 700)
        qapp.processEvents()
        assert v.split.orientation() == Qt.Horizontal          # 넓으면 나란히
        v.close()
    finally:
        st.close()


def test_adjudication_view_fits_471_with_three_sessions_side_by_side(qapp, proj):
    """표본 건은 세 세션 이상이면 한 건에 세 칸이 나란히 선다(merge_cases). 그 칸도 471px 에 들어가야 한다."""
    from app.core import adjudicate as adj
    from app.core import agreement as ag
    from app.ui import adjudication_view as av
    from tests.test_adjudication import SA, SB, SC, rx, side
    sides = [side(s, reactions=[rx(s, cid(1), g, why="합성 근거: 00:10 화면 반응")])
             for s, g in ((SA, "direct"), (SB, "likely"), (SC, "related_only"))]
    lists = [adj.sample_cases(ag.compare_samples(p["reactions"], q["reactions"]), p, q)
             for p, q in ((sides[0], sides[1]), (sides[0], sides[2]), (sides[1], sides[2]))]
    st = _open(proj, _ctx(AD, "adjudication", "L01"))
    try:
        v = av.AdjudicationView(store=st)
        v.cases = adj.merge_cases(lists)
        v._reload_states()
        v._fill_case_list()
        assert len(v.current.sides) == 3
        v.resize(471, 640)
        v.show()
        assert _fit(v, qapp) == {"hscroll": False, "clipped": 0, "outside": 0}
        assert [t["session_id"] for t in v.side_texts(v.current)] == [SA, SB, SC]   # 자료에는 폭 없는 빈칸 없음
        v.close()
    finally:
        st.close()


@pytest.mark.parametrize("width", [471, 731])
def test_reaction_editor_fits_right_pane_widths(qapp, width):
    from app.ui.forms.reaction_editor import ReactionEditor
    e = ReactionEditor(save=lambda d: d)
    e.resize(width, 700)
    e.show()
    got = _fit(e, qapp)
    assert got == {"hscroll": False, "clipped": 0, "outside": 0}, got
    assert e.grade_buttons["direct"].toolTip() == "Ctrl+1"     # 단축키는 풍선 도움말로
    e.close()


# ---------------------------------------------------------------------------- (7) 조정 화면: 출처 판정 대상 만들기
def test_adjudication_makes_origin_targets_from_primary_samples(qapp, proj):
    from app.ui import adjudication_view as av
    _write_manifest(proj, manifest(P1, "aaaaaaaa", 1, 1, [(cid(2), 2 * MIN), (cid(1), 1 * MIN)]))
    _write_manifest(proj, manifest(P1, "aaaaaaaa", 1, 2, [(cid(1), 1 * MIN), (cid(3), 3 * MIN)]))
    _write_manifest(proj, manifest(P2, "bbbbbbbb", 1, 1, [(cid(3), 3 * MIN), (cid(4), 4 * MIN)]))
    st = _open(proj, _ctx(AD, "adjudication", "L01"))
    w = _Writer(proj)
    try:
        v = av.AdjudicationView(store=st, write_origin_targets=w, now=lambda: NOW + timedelta(days=3))
        assert v.btn_origin_targets.isEnabled()
        obj = v.make_origin_targets()
        assert obj is not None, v.tool_msg.text()
        path = os.path.join(proj, "origin", obj["target_id"] + ".json")
        assert os.path.exists(path) and w.calls == 1
        with open(path, encoding="utf-8") as f:
            disk = json.load(f)
        t = og.parse_targets(disk)
        assert t.chat_ids == (cid(1), cid(3), cid(4)) and disk["basis"] == "sample_items"
        assert disk["created_by"] == "L01" and disk["created_at"] == "2026-10-24T06:00:00+09:00"
        assert set(disk) == {"schema", "target_id", "created_at", "created_by", "basis", "note", "chat_ids_sha256",
                             "items"}                          # 본문·닉네임 칸 없음
        assert obj["target_id"] in v.tool_msg.text() and "3건" in v.tool_msg.text()
        # 같은 목록이면(날이 달라도) 다시 만들지 않는다
        v._now = lambda: NOW + timedelta(days=4)
        assert v.make_origin_targets() is None and "이미" in v.tool_msg.text() and w.calls == 1
    finally:
        st.close()


def test_origin_targets_button_needs_writer_and_writable_session(qapp, proj):
    from app.ui import adjudication_view as av
    _write_manifest(proj, manifest(P1, "aaaaaaaa", 1, 1, [(cid(1), MIN)]))
    st = _open(proj, _ctx(AD, "adjudication", "L01"))
    try:
        v = av.AdjudicationView(store=st)                      # 콜백 없음
        assert not v.btn_origin_targets.isEnabled()
        assert v.make_origin_targets() is None and "연결" in v.tool_msg.text()
        assert not os.path.exists(os.path.join(proj, "origin")) or not os.listdir(os.path.join(proj, "origin"))
    finally:
        st.close()
    # 조정 세션 파일이 다른 곳에서 열려 있으면 읽기 전용 → 만들지 않음
    st1 = _open(proj, _ctx(AD, "adjudication", "L01"))
    st2 = _open(proj, _ctx(AD, "adjudication", "L01"))
    try:
        assert st2.read_only
        w = _Writer(proj)
        v2 = av.AdjudicationView(store=st2, write_origin_targets=w)
        assert not v2.btn_origin_targets.isEnabled() and not v2.btn_second_plan.isEnabled()
        assert v2.make_origin_targets() is None and w.calls == 0
        assert v2.make_second_plan(P1, "L03") is None and relabel.list_plans(proj) == []
    finally:
        st2.close()
        st1.close()


# ---------------------------------------------------------------------------- (9) relabel_second 계획
def test_adjudication_creates_second_person_plan_for_locked_primary(qapp, proj):
    from app.ui import adjudication_view as av
    st = _open(proj, _ctx(AD, "adjudication", "L01"))
    try:
        v = av.AdjudicationView(store=st, app_version="0.0.1", coverage=lambda: [(0, 40 * MIN)])
        assert [v.second_src.itemData(i) for i in range(v.second_src.count())] == [P1, P2]   # 잠긴 1차만
        assert v.make_second_plan(P1, "L01") is None and "다른 라벨러" in v.tool_msg.text()
        v.second_src.setCurrentIndex(0)
        v.second_labeler.setText("l03")                        # 소문자로 쳐도 대문자로
        plan = v.make_second_plan(now=NOW + timedelta(days=3), rand4="sc01")
        assert plan is not None, v.tool_msg.text()
        assert plan["session_kind"] == "relabel_second" and plan["labeler_id"] == "L03"
        assert plan["source_session_id"] == P1 and plan["created_by"] == "L01" and plan["selected"]
        assert relabel.read_plan(proj, plan["plan_id"]) == plan
        idx = relabel.read_index(proj)
        assert [e["plan_id"] for e in idx["plans"]] == [plan["plan_id"]]
        assert plan["plan_id"] in v.tool_msg.text() and v.second_labeler.text() == ""
        # 같은 원 세션·라벨러의 쓸 수 있는 계획이 있으면 또 만들지 않는다
        assert v.make_second_plan(P1, "L03", rand4="sc02") is None and "이미" in v.tool_msg.text()
        # 취소하면 다시 만들 수 있다
        relabel.cancel_plan(proj, plan["plan_id"], reason="시험", cancelled_by="L01", cancelled_machine4="aaaa",
                            app_version="0.0.1", canceller_kind="adjudication", now=NOW)
        assert v.make_second_plan(P1, "L03", now=NOW + timedelta(days=3), rand4="sc03") is not None
    finally:
        st.close()


def test_done_ranges_of_view_matches_store_and_coverage_cuts(proj):
    st_ad = _open(proj, _ctx(AD, "adjudication", "L01"))
    st_p = _open(proj, _ctx(P1, "primary", "L01"))              # 잠긴 1차를 읽기 전용으로
    try:
        view, _m = st_ad.locked_session_view(P1)
        assert relabel.done_ranges_of_view(view, P1) == st_p.done_ranges() == [[0, 40 * MIN]]
        assert relabel.done_ranges_of_view(view, P1, [(5 * MIN, 15 * MIN)]) == \
            st_p.done_ranges(coverage=[(5 * MIN, 15 * MIN)]) == [[5 * MIN, 15 * MIN]]
    finally:
        st_p.close()
        st_ad.close()


@pytest.mark.parametrize("kind", ["primary", "relabel_same", "relabel_second", "origin_review", "analysis"])
def test_second_plan_refused_outside_adjudication_before_any_open(proj, kind, monkeypatch):
    import builtins
    opened = []
    real = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: opened.append(str(f)) or real(f, *a, **k))
    with pytest.raises(relabel.RelabelBlindError):
        relabel.create_second_plan(proj, {}, source_view=None, source_session_id=P1, labeler_id="L03",
                                   created_by="L01", created_machine4="aaaa", app_version="0", creator_kind=kind)
    assert opened == []


def test_second_plan_refuses_non_primary_source_and_empty_ranges(proj):
    with pytest.raises(ValueError, match="1차"):
        relabel.create_second_plan(proj, {}, source_view=None, source_session_id=RS, labeler_id="L03",
                                   created_by="L01", created_machine4="aaaa", app_version="0",
                                   creator_kind="adjudication")

    class Empty:
        other_records = ()

        def items(self, kind=None, session_id=None):
            return []
    with pytest.raises(ValueError, match="라벨을 마친 범위"):
        relabel.create_second_plan(proj, {}, source_view=Empty(), source_session_id=P1, labeler_id="L03",
                                   created_by="L01", created_machine4="aaaa", app_version="0",
                                   creator_kind="adjudication")


# ---------------------------------------------------------------------------- (3) 읽지 못한 계획이 있으면 다시 열기 막음
SID_A = "L01-P-20261018-ab12"


def _survey(plans):
    return session.ProjectSurvey(session_ids=(SID_A,), locked_ids=frozenset([SID_A]), relabel_plans=len(plans),
                                 origin_files=0, engine_results=False, active_guide_version="0.1", guide_sha8=None,
                                 roster_kinds={}, plans=tuple(plans))


def test_reopen_refused_while_any_visible_plan_is_unreadable():
    primary = session.ExistingSession(SID_A, "primary", ("aaaa",), True, True, "0.1", None)
    bad = relabel.PlanSummary("RP-20261021-zz99", problem="읽지 못했습니다 — 계획 형식이 아닙니다",
                              status=relabel.STATUS_UNREADABLE)
    why = session.reopen_refusal(primary, "L01", _survey([bad]), [primary])
    assert why and "읽지 못한 재라벨 계획이 1개" in why and bad.plan_id in why
    # 취소된 읽지 못한 계획은 막지 않는다
    gone = relabel.PlanSummary(bad.plan_id, problem=bad.problem, status=relabel.STATUS_CANCELLED)
    assert session.reopen_refusal(primary, "L01", _survey([gone]), [primary]) is None
    # 잠기지 않은 세션·1차가 아닌 세션은 그대로 연다
    open_p = session.ExistingSession(SID_A, "primary", ("aaaa",), False, True, "0.1", None)
    assert session.reopen_refusal(open_p, "L01", _survey([bad]), [open_p]) is None


def test_reopen_refused_on_disk_when_plan_file_is_broken_or_index_cannot_be_rebuilt(tmp_path, monkeypatch):
    from tests.test_relabel_plan import SRC, _create, _project
    root, data, *_ = _project(tmp_path)
    primary = session.ExistingSession(SRC, "primary", ("aaaa",), True, True, "0.1", None)
    p2 = _create(root, data, "relabel_second", "L02", rand4="cd34")       # 다른 라벨러 계획만 → 연다
    assert session.reopen_refusal(primary, "L01", session.survey_project(root, data), [primary]) is None
    with open(relabel.plan_path(root, "RP-20261021-zz99"), "wb") as f:
        f.write(b"{not json")
    why = session.reopen_refusal(primary, "L01", session.survey_project(root, data), [primary])
    assert why and "RP-20261021-zz99" in why
    # 색인을 다시 만들지 못하면 모든 계획이 '읽지 못함' → 막는다(다른 라벨러 계획도 열지 않은 채)
    os.remove(relabel.index_path(root))

    def boom(*a, **k):
        raise PermissionError("잠김")
    monkeypatch.setattr(relabel, "rebuild_index", boom)
    why2 = session.reopen_refusal(primary, "L01", session.survey_project(root, data), [primary])
    assert why2 and "읽지 못한 재라벨 계획이 2개" in why2
