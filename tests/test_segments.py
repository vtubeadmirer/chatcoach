"""app/core/segments.py 테스트 — 합성 데이터만 (실제 채팅·닉네임 없음)."""
import ast
import json
import os

import pytest

from app.core import segments as sg
from app.core.segments import (
    BoundaryTooCloseError, IncompleteSegmentError, OutOfMediaError, SegmentError,
    SegmentOverlapError, SegmentTimeline, diff_segments, thresholds_from_guide,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN = 60_000


class Ids:
    """결정적 UUID v4 모양 생성기 (core 는 난수를 쓰지 않으므로 테스트가 주입)."""

    def __init__(self, start=1):
        self.n = start - 1

    def __call__(self):
        self.n += 1
        return f"{self.n:08x}-0000-4000-8000-{self.n:012x}"


def make(duration=10 * MIN, **kw):
    return SegmentTimeline(duration, Ids(), **kw)


def spans(tl):
    return [(s.start_ms, s.end_ms) for s in tl.segments]


def label(tl, i, cat="just_chatting", conf="certain", why="00:10 잡담 시작"):
    return tl.set_props(tl.segments[i].id, category=cat, confidence=conf, rationale=why)


def rec(sid, s, e, cat="just_chatting", **extra):
    body = {"start_ms": s, "end_ms": e, "category": cat, "confidence": "certain", "rationale": "근거"}
    body.update(extra)
    return {"kind": "segment", "entity_id": sid, "body": body}


def uid(n):
    return f"{n:08x}-0000-4000-8000-{n:012x}"


# ---------------------------------------------------------------- 층 규칙
def test_core_imports_no_forbidden_modules():
    path = os.path.join(ROOT, "app", "core", "segments.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    forbidden = {"PySide6", "PyQt5", "PyQt6", "os", "io", "pathlib", "socket", "urllib", "http",
                 "requests", "time", "datetime", "random", "uuid", "secrets", "shutil", "subprocess"}
    assert not (names & forbidden), names & forbidden


# ---------------------------------------------------------------- 생성
def test_initial_single_unlabeled_segment():
    tl = make()
    assert spans(tl) == [(0, 10 * MIN)]
    assert tl.boundaries == []
    assert tl.segments[0].category is None
    assert tl.unlabeled() == list(tl.segments)
    assert tl.to_records() == []
    assert not tl.can_undo


@pytest.mark.parametrize("bad", [0, -1])
def test_bad_duration(bad):
    with pytest.raises(SegmentError):
        make(bad)


@pytest.mark.parametrize("bad", [1.5, "100", True, None])
def test_duration_must_be_int(bad):
    with pytest.raises(TypeError):
        make(bad)


def test_new_id_must_be_uuid4():
    with pytest.raises(SegmentError):
        SegmentTimeline(1000, lambda: "not-a-uuid")
    with pytest.raises(SegmentError):
        SegmentTimeline(1000, lambda: "00000001-0000-1000-8000-000000000001")  # v1


def test_new_id_duplicate_rejected():
    tl = SegmentTimeline(10 * MIN, lambda: uid(1))
    with pytest.raises(SegmentError):
        tl.add_boundary(MIN)
    assert spans(tl) == [(0, 10 * MIN)]


# ---------------------------------------------------------------- 경계 추가
def test_add_boundary_splits_with_split_from():
    tl = make()
    a = tl.segments[0].id
    e = tl.add_boundary(3 * MIN)
    assert spans(tl) == [(0, 3 * MIN), (3 * MIN, 10 * MIN)]
    c = tl.segments[1]
    assert c.split_from == a
    assert tl.segments[0].id == a
    assert e.updated == (a,) and e.created == (c.id,) and e.retracted == ()
    tl.check()


def test_add_boundary_out_of_order_keeps_sorted():
    tl = make()
    tl.add_boundary(7 * MIN)
    tl.add_boundary(2 * MIN)
    tl.add_boundary(5 * MIN)
    assert tl.boundaries == [2 * MIN, 5 * MIN, 7 * MIN]
    tl.check()


def test_split_inherits_labels_by_default():
    tl = make()
    a = tl.segments[0].id
    tl.set_props(a, category="game", confidence="probably", rationale="게임 화면", active_chat=True,
                 long_silence_reviewed=True)
    tl.add_boundary(MIN, start_signal="screen")
    c = tl.segments[1]
    assert (c.category, c.confidence, c.rationale, c.active_chat) == ("game", "probably", "게임 화면", True)
    assert c.start_signal == "screen"
    assert c.long_silence_reviewed is None


def test_split_without_inherit():
    tl = make()
    label(tl, 0)
    tl.add_boundary(MIN, inherit=False)
    c = tl.segments[1]
    assert c.category is None and c.rationale == "" and c.confidence is None


@pytest.mark.parametrize("t", [0, 10 * MIN, -5, 10 * MIN + 1, 99 * MIN])
def test_add_boundary_out_of_media_raises(t):
    tl = make()
    with pytest.raises(OutOfMediaError):
        tl.add_boundary(t)
    assert spans(tl) == [(0, 10 * MIN)]
    assert not tl.can_undo


def test_add_boundary_too_close_raises():
    tl = make(min_gap_ms=100)
    tl.add_boundary(MIN)
    with pytest.raises(BoundaryTooCloseError):
        tl.add_boundary(MIN)
    with pytest.raises(BoundaryTooCloseError):
        tl.add_boundary(MIN + 99)
    with pytest.raises(BoundaryTooCloseError):
        tl.add_boundary(50)
    tl.add_boundary(MIN + 100)
    assert tl.boundaries == [MIN, MIN + 100]


def test_add_boundary_float_rejected():
    tl = make()
    with pytest.raises(TypeError):
        tl.add_boundary(1000.5)


def test_add_boundaries_is_one_undo_step_and_atomic():
    tl = make()
    tl.add_boundaries([3 * MIN, MIN, 5 * MIN])
    assert tl.boundaries == [MIN, 3 * MIN, 5 * MIN]
    tl.undo()
    assert tl.boundaries == []
    assert not tl.can_undo
    tl.redo()
    assert tl.boundaries == [MIN, 3 * MIN, 5 * MIN]
    with pytest.raises(OutOfMediaError):
        tl.add_boundaries([2 * MIN, 11 * MIN])
    assert tl.boundaries == [MIN, 3 * MIN, 5 * MIN]
    tl.undo()
    assert tl.boundaries == []


# ---------------------------------------------------------------- 경계 이동
def test_move_boundary_updates_both_neighbors():
    tl = make()
    tl.add_boundaries([2 * MIN, 5 * MIN])
    a, b, c = (s.id for s in tl.segments)
    e = tl.move_boundary(0, 3 * MIN)
    assert spans(tl) == [(0, 3 * MIN), (3 * MIN, 5 * MIN), (5 * MIN, 10 * MIN)]
    assert e.updated == (a, b) and not e.created and not e.retracted
    assert [s.id for s in tl.segments] == [a, b, c]  # ID 가 경계를 따라간다 (S-13)


def test_move_boundary_keeps_attributes_on_id():
    tl = make()
    tl.add_boundary(2 * MIN, inherit=False)
    tl.set_props(tl.segments[1].id, category="singing", confidence="certain", rationale="노래")
    tl.move_boundary(0, MIN)
    assert tl.segments[1].category == "singing" and tl.segments[1].start_ms == MIN
    assert tl.segments[0].category is None


def test_move_boundary_cannot_cross_neighbor():
    tl = make(min_gap_ms=100)
    tl.add_boundaries([2 * MIN, 5 * MIN])
    with pytest.raises(BoundaryTooCloseError):
        tl.move_boundary(0, 6 * MIN)
    with pytest.raises(BoundaryTooCloseError):
        tl.move_boundary(0, 5 * MIN - 50)
    assert tl.boundaries == [2 * MIN, 5 * MIN]
    tl.move_boundary(0, 6 * MIN, clamp=True)
    assert tl.boundaries == [5 * MIN - 100, 5 * MIN]
    tl.check()


def test_move_boundary_out_of_media_raises_even_with_clamp():
    tl = make()
    tl.add_boundary(MIN)
    for t in (0, -1, 10 * MIN, 20 * MIN):
        with pytest.raises(OutOfMediaError):
            tl.move_boundary(0, t, clamp=True)
    assert tl.boundaries == [MIN]


def test_move_boundary_bad_index_and_noop():
    tl = make()
    tl.add_boundary(MIN)
    with pytest.raises(SegmentError):
        tl.move_boundary(1, 2 * MIN)
    n_undo = len(tl._undo)
    assert tl.move_boundary(0, MIN).empty
    assert len(tl._undo) == n_undo


# ---------------------------------------------------------------- 경계 삭제(합치기)
def test_remove_boundary_merges_left_keeps_left():
    tl = make()
    tl.add_boundaries([2 * MIN, 5 * MIN])
    a, b, c = (s.id for s in tl.segments)
    e = tl.remove_boundary(0)
    assert spans(tl) == [(0, 5 * MIN), (5 * MIN, 10 * MIN)]
    assert tl.segments[0].id == a and tl.segments[0].merged_from == (b,)
    assert e.updated == (a,) and e.retracted == ((b, a),) and not e.created


def test_remove_boundary_keep_right():
    tl = make()
    tl.add_boundary(2 * MIN, inherit=False)
    a, b = (s.id for s in tl.segments)
    tl.set_props(b, category="game", confidence="certain", rationale="게임")
    e = tl.remove_boundary(0, keep="right")
    assert spans(tl) == [(0, 10 * MIN)]
    s = tl.segments[0]
    assert s.id == b and s.category == "game" and s.merged_from == (a,)
    assert e.retracted == ((a, b),)


def test_remove_boundary_merged_from_accumulates():
    tl = make()
    tl.add_boundaries([MIN, 2 * MIN, 3 * MIN])
    a, b, c, d = (s.id for s in tl.segments)
    tl.remove_boundary(1)        # b ← c
    tl.remove_boundary(0)        # a ← b(+c)
    assert tl.segments[0].merged_from == (b, c)
    tl.remove_boundary(0)        # a ← d
    assert tl.segments[0].merged_from == (b, c, d)
    assert spans(tl) == [(0, 10 * MIN)]


def test_remove_boundary_errors():
    tl = make()
    with pytest.raises(SegmentError):
        tl.remove_boundary(0)
    tl.add_boundary(MIN)
    with pytest.raises(SegmentError):
        tl.remove_boundary(0, keep="middle")


def test_merge_into_prev_next():
    tl = make()
    tl.add_boundaries([MIN, MIN + 10_000])
    a, short, c = (s.id for s in tl.segments)
    tl.merge_into(short, "next")
    assert [s.id for s in tl.segments] == [a, c]
    assert tl.segments[1].start_ms == MIN and tl.segments[1].merged_from == (short,)
    tl.undo()
    tl.merge_into(short, "prev")
    assert [s.id for s in tl.segments] == [a, c]
    assert tl.segments[0].end_ms == MIN + 10_000
    with pytest.raises(SegmentError):
        tl.merge_into(a, "prev")
    with pytest.raises(SegmentError):
        tl.merge_into(c, "next")
    with pytest.raises(SegmentError):
        tl.merge_into(a, "sideways")


# ---------------------------------------------------------------- 속성
def test_set_props_validation():
    tl = make()
    sid = tl.segments[0].id
    with pytest.raises(SegmentError):
        tl.set_props(sid, category="talk")
    with pytest.raises(SegmentError):
        tl.set_props(sid, confidence="sure")
    with pytest.raises(SegmentError):
        tl.set_props(sid, rationale="가" * 301)
    with pytest.raises(SegmentError):
        tl.set_props(sid, category="just_chatting", active_chat=True)
    with pytest.raises(SegmentError):
        tl.set_props(sid, start_signal="smell")
    with pytest.raises(SegmentError):
        tl.set_props(sid, category="game", active_chat="yes")
    with pytest.raises(KeyError):
        tl.set_props(uid(999), category="game")
    assert not tl.can_undo


def test_all_enums_accepted():
    tl = make()
    sid = tl.segments[0].id
    for cat in sg.CATEGORIES:
        for conf in sg.CONFIDENCES:
            tl.set_props(sid, category=cat, confidence=conf, rationale="r")
    for sig in sg.START_SIGNALS:
        tl.set_props(sid, start_signal=sig)


def test_active_chat_cleared_when_leaving_game():
    tl = make()
    sid = tl.segments[0].id
    tl.set_props(sid, category="game", active_chat=True)
    assert tl.segments[0].active_chat is True
    tl.set_props(sid, category="singing")
    assert tl.segments[0].active_chat is None


def test_set_props_strips_rationale_and_noop():
    tl = make()
    sid = tl.segments[0].id
    e = tl.set_props(sid, category="other", confidence="probably", rationale="  자리비움 화면  ")
    assert tl.segments[0].rationale == "자리비움 화면"
    assert e.updated == (sid,)
    n = len(tl._undo)
    assert tl.set_props(sid, category="other").empty
    assert len(tl._undo) == n


# ---------------------------------------------------------------- 찾기
def test_segment_at_bisect():
    tl = make()
    tl.add_boundaries([MIN, 4 * MIN])
    ids = [s.id for s in tl.segments]
    assert tl.segment_at(0).id == ids[0]
    assert tl.segment_at(MIN - 1).id == ids[0]
    assert tl.segment_at(MIN).id == ids[1]
    assert tl.segment_at(4 * MIN).id == ids[2]
    assert tl.segment_at(10 * MIN - 1).id == ids[2]
    assert tl.segment_at(10 * MIN) is None
    assert tl.segment_at(-1) is None
    assert tl.index_at(2 * MIN) == 1


def test_segment_at_many_boundaries():
    tl = make(duration=1000 * 1000, min_gap_ms=1)
    tl.add_boundaries(list(range(1000, 1000 * 1000, 1000)))
    assert len(tl) == 1000
    for t in (0, 999, 1000, 123_456, 999_999):
        s = tl.segment_at(t)
        assert s.start_ms <= t < s.end_ms


def test_nearest_boundary():
    tl = make()
    tl.add_boundaries([MIN, 2 * MIN])
    assert tl.nearest_boundary(MIN + 300, 500) == 0
    assert tl.nearest_boundary(2 * MIN - 10, 500) == 1
    assert tl.nearest_boundary(MIN + 30_000, 500) is None
    assert make().nearest_boundary(5, 10) is None


def test_get_and_index_of():
    tl = make()
    tl.add_boundary(MIN)
    sid = tl.segments[1].id
    assert tl.index_of(sid) == 1 and tl.get(sid).start_ms == MIN
    with pytest.raises(KeyError):
        tl.index_of(uid(999))


# ---------------------------------------------------------------- 병합 제안
def test_short_segments_suggestion_not_auto_merge():
    tl = make()
    tl.add_boundaries([MIN, MIN + 20_000, 5 * MIN])
    before = spans(tl)
    sug = tl.short_segments()
    assert spans(tl) == before        # 자동 병합 아님
    assert len(sug) == 1
    s = sug[0]
    assert s.segment_id == tl.segments[1].id and s.length_ms == 20_000
    assert s.prev_id == tl.segments[0].id and s.next_id == tl.segments[2].id
    assert s.suggested == "prev"


def test_short_segments_prefers_same_category_neighbor():
    tl = make()
    tl.add_boundaries([MIN, MIN + 20_000], inherit=False)
    label(tl, 0, cat="just_chatting")
    label(tl, 1, cat="game")
    label(tl, 2, cat="game")
    assert tl.short_segments()[0].suggested == "next"


def test_short_segments_edges_and_threshold():
    tl = make()
    tl.add_boundaries([10_000, 10 * MIN - 5_000])
    sug = tl.short_segments(30_000)
    assert [x.suggested for x in sug] == ["next", "prev"]
    assert sug[0].prev_id is None and sug[1].next_id is None
    # 정확히 30초는 짧지 않다
    tl2 = make()
    tl2.add_boundary(30_000)
    assert tl2.short_segments(30_000) == []
    # 구간이 하나뿐이면 제안 없음
    assert make(duration=10_000).short_segments() == []


# ---------------------------------------------------------------- 경계 완충
def test_buffer_ranges_basic_and_clipped():
    tl = make()
    tl.add_boundaries([5_000, 2 * MIN, 10 * MIN - 3_000])
    br = tl.buffer_ranges(10_000)
    assert [(b.start_ms, b.end_ms, b.boundaries) for b in br] == [
        (0, 15_000, (5_000,)),
        (2 * MIN - 10_000, 2 * MIN + 10_000, (2 * MIN,)),
        (10 * MIN - 13_000, 10 * MIN, (10 * MIN - 3_000,)),
    ]


def test_buffer_ranges_merge_close_boundaries():
    tl = make()
    tl.add_boundaries([MIN, MIN + 15_000, MIN + 20_000, 3 * MIN])
    br = tl.buffer_ranges(10_000)
    assert (br[0].start_ms, br[0].end_ms) == (MIN - 10_000, MIN + 30_000)
    assert br[0].boundaries == (MIN, MIN + 15_000, MIN + 20_000)
    assert len(br) == 2
    # 정확히 맞닿는 완충도 합친다
    tl2 = make()
    tl2.add_boundaries([MIN, MIN + 20_000])
    assert len(tl2.buffer_ranges(10_000)) == 1


def test_buffer_ranges_none_and_zero():
    assert make().buffer_ranges() == []
    tl = make()
    tl.add_boundary(MIN)
    assert tl.buffer_ranges(0) == []
    with pytest.raises(SegmentError):
        tl.buffer_ranges(-1)


def test_in_buffer_matches_ranges():
    tl = make()
    tl.add_boundaries([MIN, MIN + 15_000, 4 * MIN])
    br = tl.buffer_ranges(10_000)
    for t in range(0, 10 * MIN, 997):
        assert tl.in_buffer(t, 10_000) == any(b.contains(t) for b in br), t
    assert tl.in_buffer(MIN - 10_000)
    assert not tl.in_buffer(MIN - 10_001)
    assert not tl.in_buffer(MIN + 15_000 + 10_000)
    assert not make().in_buffer(5)


# ---------------------------------------------------------------- undo / redo
def test_undo_redo_roundtrip():
    tl = make()
    s0 = spans(tl)
    tl.add_boundary(MIN)
    s1 = spans(tl)
    label(tl, 1)
    tl.move_boundary(0, 2 * MIN)
    s3 = (spans(tl), tl.segments)
    tl.undo()
    tl.undo()
    assert spans(tl) == s1 and tl.segments[1].category is None
    tl.undo()
    assert spans(tl) == s0
    assert tl.undo() is None
    tl.redo()
    tl.redo()
    tl.redo()
    assert (spans(tl), tl.segments) == s3
    assert tl.redo() is None


def test_new_edit_clears_redo():
    tl = make()
    tl.add_boundary(MIN)
    tl.undo()
    assert tl.can_redo
    tl.add_boundary(2 * MIN)
    assert not tl.can_redo


def test_undo_limit_200():
    tl = make(duration=1000 * MIN)
    for k in range(1, 251):
        tl.add_boundary(k * 1000)
    n = 0
    while tl.undo() is not None:
        n += 1
    assert n == sg.UNDO_LIMIT == 200
    assert len(tl.boundaries) == 50


def test_undo_edit_reports_reverse_diff():
    tl = make()
    tl.add_boundary(MIN)
    a, b = (s.id for s in tl.segments)
    e = tl.remove_boundary(0)
    assert e.retracted == ((b, a),)
    u = tl.undo()
    assert u.created == (b,) and u.updated == (a,)
    u2 = tl.undo()
    assert u2.retracted == ((b, None),) and u2.updated == (a,)
    r = tl.redo()
    assert r.created == (b,)


def test_failed_op_does_not_touch_undo():
    tl = make()
    tl.add_boundary(MIN)
    tl.undo()
    with pytest.raises(OutOfMediaError):
        tl.add_boundary(0)
    assert tl.can_redo and not tl.can_undo


# ---------------------------------------------------------------- 불변식 (속성 기반, 결정적 의사난수)
def test_invariant_random_ops():
    import random  # 테스트에서만. core 는 난수를 쓰지 않는다
    rng = random.Random(1234)
    tl = make(duration=30 * MIN, min_gap_ms=100)
    for _ in range(2000):
        op = rng.random()
        try:
            if op < 0.35:
                tl.add_boundary(rng.randrange(-1000, 30 * MIN + 1000))
            elif op < 0.55 and tl.boundaries:
                tl.move_boundary(rng.randrange(len(tl.boundaries)), rng.randrange(1, 30 * MIN), clamp=rng.random() < .5)
            elif op < 0.7 and tl.boundaries:
                tl.remove_boundary(rng.randrange(len(tl.boundaries)), keep=rng.choice(["left", "right"]))
            elif op < 0.8:
                s = rng.choice(tl.segments)
                cat = rng.choice(sg.CATEGORIES)
                tl.set_props(s.id, category=cat, confidence=rng.choice(sg.CONFIDENCES), rationale="r",
                             active_chat=(rng.random() < .5) if cat == "game" else None)
            elif op < 0.9:
                tl.undo()
            else:
                tl.redo()
        except SegmentError:
            pass
        tl.check()
        assert tl.segments[0].start_ms == 0 and tl.segments[-1].end_ms == 30 * MIN
        assert all(s.length_ms >= 1 for s in tl.segments)


# ---------------------------------------------------------------- 기록 레코드
def _schema_segment_body():
    with open(os.path.join(ROOT, "schemas", "label-event.schema.json"), encoding="utf-8") as f:
        return json.load(f)["$defs"]


def test_record_shape_matches_schema():
    defs = _schema_segment_body()
    body_def = defs["segmentBody"]
    tl = make()
    tl.add_boundary(MIN, start_signal="audio")
    label(tl, 0, cat="game")
    tl.set_props(tl.segments[0].id, active_chat=True)
    label(tl, 1, cat="singing")
    recs = tl.to_records(strict=True)
    assert len(recs) == 2
    for r in recs:
        assert set(r) == {"kind", "entity_id", "body"} and r["kind"] == "segment"
        body = r["body"]
        assert set(body) <= set(body_def["properties"])
        assert set(body_def["required"]) <= set(body)
        assert isinstance(body["start_ms"], int) and isinstance(body["merged_from"], list)
        if body["active_chat"] is True:
            assert body["category"] == "game"
    assert recs[1]["body"]["start_signal"] == "audio"
    assert recs[1]["body"]["split_from"] == recs[0]["entity_id"]
    assert recs[0]["body"]["out_of_media"] is False
    json.dumps(recs)  # 직렬화 가능


def test_enums_match_schema():
    defs = _schema_segment_body()
    assert list(sg.CATEGORIES) == defs["category"]["enum"]
    assert list(sg.CONFIDENCES) == defs["confidence"]["enum"]
    assert list(sg.START_SIGNALS) == defs["segmentBody"]["properties"]["start_signal"]["enum"]
    assert sg.RATIONALE_MAX == defs["rationale"]["maxLength"]


def test_to_records_skips_incomplete_strict_raises():
    tl = make()
    tl.add_boundary(MIN, inherit=False)
    label(tl, 0)
    tl.set_props(tl.segments[1].id, category="game")  # 확신도·근거 없음
    assert [r["entity_id"] for r in tl.to_records()] == [tl.segments[0].id]
    assert tl.incomplete() == [tl.segments[1]]
    with pytest.raises(IncompleteSegmentError):
        tl.to_records(strict=True)
    with pytest.raises(IncompleteSegmentError):
        tl.record(tl.segments[1].id)
    assert tl.record(tl.segments[0].id)["body"]["category"] == "just_chatting"


def test_records_roundtrip():
    tl = make()
    tl.add_boundaries([MIN, 3 * MIN])
    for i in range(3):
        label(tl, i, cat=["just_chatting", "game", "other"][i])
    tl.remove_boundary(1)
    recs = tl.to_records(strict=True)
    tl2 = SegmentTimeline.from_records(10 * MIN, recs, Ids(100))
    assert tl2.segments == tl.segments
    assert tl2.to_records() == recs
    assert not tl2.can_undo


def test_from_records_fills_gaps_with_unlabeled():
    ids = Ids(100)
    recs = [rec(uid(1), MIN, 2 * MIN), rec(uid(2), 5 * MIN, 6 * MIN, cat="game")]
    tl = SegmentTimeline.from_records(10 * MIN, list(reversed(recs)), ids)
    assert spans(tl) == [(0, MIN), (MIN, 2 * MIN), (2 * MIN, 5 * MIN), (5 * MIN, 6 * MIN), (6 * MIN, 10 * MIN)]
    assert [s.is_labeled for s in tl.segments] == [False, True, False, True, False]
    assert [r["entity_id"] for r in tl.to_records()] == [uid(1), uid(2)]
    tl.check()


def test_from_records_overlap_raises_and_check_records():
    recs = [rec(uid(1), 0, 2 * MIN), rec(uid(2), MIN, 3 * MIN)]
    with pytest.raises(SegmentOverlapError) as ei:
        SegmentTimeline.from_records(10 * MIN, recs, Ids(100))
    assert ei.value.overlaps == [(uid(1), uid(2))]
    issues = SegmentTimeline.check_records(10 * MIN, recs)
    assert [(i.kind, i.start_ms, i.end_ms) for i in issues] == [("overlap", MIN, 2 * MIN), ("gap", 3 * MIN, 10 * MIN)]
    assert issues[0].ids == (uid(1), uid(2))


def test_from_records_out_of_media_preserved_not_dropped():
    recs = [
        rec(uid(1), 0, 4 * MIN),
        rec(uid(2), 4 * MIN, 12 * MIN, cat="game"),     # 영상 끝(10분)에 걸침
        rec(uid(3), 12 * MIN, 15 * MIN, cat="singing"),  # 시작부터 밖
    ]
    tl = SegmentTimeline.from_records(10 * MIN, recs, Ids(100))
    assert spans(tl) == [(0, 4 * MIN), (4 * MIN, 10 * MIN)]
    s2 = tl.segments[1]
    assert s2.out_of_media and s2.orig_end_ms == 12 * MIN
    assert [s.id for s in tl.outside] == [uid(3)] and tl.outside[0].out_of_media
    out = {r["entity_id"]: r["body"] for r in tl.to_records()}
    assert out[uid(1)]["out_of_media"] is False
    assert (out[uid(2)]["end_ms"], out[uid(2)]["out_of_media"]) == (12 * MIN, True)
    assert (out[uid(3)]["start_ms"], out[uid(3)]["end_ms"], out[uid(3)]["out_of_media"]) == (12 * MIN, 15 * MIN, True)
    kinds = [(i.kind, i.ids) for i in SegmentTimeline.check_records(10 * MIN, recs)]
    assert kinds == [("out_of_media", (uid(2),)), ("out_of_media", (uid(3),))]


def test_out_of_media_tail_follows_split_and_merge():
    recs = [rec(uid(1), 0, 12 * MIN)]
    tl = SegmentTimeline.from_records(10 * MIN, recs, Ids(100))
    tl.add_boundary(5 * MIN)
    left, right = tl.segments
    assert not left.out_of_media and right.orig_end_ms == 12 * MIN
    assert tl.record(right.id)["body"]["end_ms"] == 12 * MIN
    tl.move_boundary(0, 6 * MIN)
    assert tl.segments[1].orig_end_ms == 12 * MIN
    tl.remove_boundary(0)
    s = tl.segments[0]
    assert s.id == uid(1) and s.orig_end_ms == 12 * MIN
    assert tl.record(uid(1))["body"]["end_ms"] == 12 * MIN


def test_out_of_media_negative_start():
    recs = [rec(uid(1), -5_000, MIN), rec(uid(2), MIN, 10 * MIN)]
    tl = SegmentTimeline.from_records(10 * MIN, recs, Ids(100))
    assert spans(tl) == [(0, MIN), (MIN, 10 * MIN)]
    assert tl.record(uid(1))["body"]["start_ms"] == -5_000
    assert tl.record(uid(1))["body"]["out_of_media"] is True


def test_discard_outside_undoable():
    recs = [rec(uid(1), 0, 10 * MIN), rec(uid(2), 11 * MIN, 12 * MIN)]
    tl = SegmentTimeline.from_records(10 * MIN, recs, Ids(100))
    e = tl.discard_outside(uid(2))
    assert e.retracted == ((uid(2), None),) and tl.outside == ()
    tl.undo()
    assert [s.id for s in tl.outside] == [uid(2)]
    with pytest.raises(KeyError):
        tl.discard_outside(uid(1))


@pytest.mark.parametrize("bad", [
    {"kind": "reaction", "entity_id": uid(1), "body": {}},
    {"kind": "segment", "entity_id": "x", "body": {}},
    {"kind": "segment", "entity_id": uid(1)},
    rec(uid(1), MIN, MIN),
    rec(uid(1), 2 * MIN, MIN),
    rec(uid(1), 0, 1.5),
    rec(uid(1), 0, MIN, cat="talk"),
    rec(uid(1), 0, MIN, cat="singing", active_chat=True),
    rec(uid(1), 0, MIN, merged_from=["nope"]),
    {"kind": "segment", "entity_id": uid(1), "body": {"start_ms": 0, "end_ms": MIN, "category": "game",
                                                      "confidence": "certain", "rationale": ""}},
])
def test_from_records_rejects_bad_records(bad):
    with pytest.raises(SegmentError):
        SegmentTimeline.from_records(10 * MIN, [bad], Ids(100))


def test_from_records_duplicate_entity_rejected():
    with pytest.raises(SegmentError):
        SegmentTimeline.from_records(10 * MIN, [rec(uid(1), 0, MIN), rec(uid(1), MIN, 2 * MIN)], Ids(100))


def test_from_records_gap_id_collision_rejected():
    with pytest.raises(SegmentError):
        SegmentTimeline.from_records(10 * MIN, [rec(uid(1), MIN, 2 * MIN)], lambda: uid(1))


def test_from_records_empty_gives_one_unlabeled():
    tl = SegmentTimeline.from_records(10 * MIN, [], Ids())
    assert spans(tl) == [(0, 10 * MIN)] and tl.to_records() == []


# ---------------------------------------------------------------- 도우미
def test_thresholds_from_guide():
    cfg = {"silence_keep_max_sec": 30, "song_interlude_merge_max_sec": 60, "long_silence_review_sec": 120,
           "boundary_buffer_sec": 10, "min_length_sec": 30}
    th = thresholds_from_guide(cfg)
    assert (th.buffer_ms, th.min_length_ms) == (10_000, 30_000)
    assert thresholds_from_guide({**cfg, "boundary_buffer_sec": 2.5}).buffer_ms == 2_500
    with pytest.raises(TypeError):
        thresholds_from_guide({**cfg, "min_length_sec": "30"})


def test_diff_segments_direct():
    a = sg.Segment(uid(1), 0, 10)
    b = sg.Segment(uid(2), 10, 20)
    merged = sg.Segment(uid(1), 0, 20, merged_from=(uid(2),))
    e = diff_segments([a, b], [merged])
    assert e.updated == (uid(1),) and e.retracted == ((uid(2), uid(1)),)
    assert diff_segments([a], [a]).empty
