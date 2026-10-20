"""현재 유효 라벨 계산 골든 테스트 (docs/design/03-data-model.md 11장 표, 05 S-10).

입력은 tests/fixtures/labels_*.jsonl (합성. 실제 채팅·닉네임 없음) 을 app.jsonl.read 로 읽은 결과 그대로다.
"""
import itertools
import os
import random

import pytest

from app import jsonl
from app.core import resolve as R

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
S1 = "L01-P-20261021-aaaa"
S2 = "L02-R2-20261021-bbbb"


def lid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def eid(n):
    return f"eeeeeeee-0000-4000-8000-{n:012d}"


def key(n, sid=S1):
    return f"{sid}/{eid(n)}"


def load(name):
    return jsonl.read(os.path.join(FIX, name))


def lab(n, seq, ent, sup=None, kind="segment", sid=S1):
    return {"v": 1, "id": lid(n), "seq": seq, "kind": kind, "session_id": sid, "labeler_id": sid.split("-")[0],
            "created_at": "2026-10-21T09:00:00+09:00", "app_version": "0.1.0", "entity_id": eid(ent),
            "supersedes": lid(sup) if sup else None, "body": {"n": n}}


def codes(view):
    return [(p.code, p.reason) for p in view.problems]


# --------------------------------------------------------------------------- 골든 (fixtures)
def test_golden_simple_edit_three_versions():
    v = R.resolve(load("labels_simple_edit.jsonl"))
    assert v.to_dict() == {
        "effective": {key(1): lid(3), key(2): lid(4)},
        "status": {key(1): R.ACTIVE, key(2): R.ACTIVE},
        "invalid": {},
        "problems": [],
    }
    # 어느 판 id 로 물어도 머리가 나온다
    for n in (1, 2, 3):
        assert v.effective(lid(n))["id"] == lid(3)
    assert [r["id"] for r in v.history(lid(1))] == [lid(1), lid(2), lid(3)]
    assert v.is_head(lid(3)) and not v.is_head(lid(2))
    assert [r["id"] for r in v.items()] == [lid(3), lid(4)]
    assert [r["kind"] for r in v.other_records] == ["session.open"]
    assert v.chain(eid(1)).kind == "segment"


def test_golden_retract_and_revive():
    v = R.resolve(load("labels_retract.jsonl"))
    assert v.to_dict()["status"] == {key(10): R.RETRACTED, key(20): R.ACTIVE}
    assert v.effective(lid(10)) is None and v.status(lid(11)) == R.RETRACTED
    assert v.effective(lid(21))["id"] == lid(22)      # 철회 뒤 되살린 판이 유효
    assert [r["kind"] for r in v.history(lid(20))] == ["segment", "retract", "segment"]
    assert v.is_head(lid(11))                          # 철회 줄도 머리다(되살릴 때 가리킨다)
    assert v.problems == []


def test_golden_other_session_and_other_entity_are_invalid():
    v = R.resolve(load("labels_other_session.jsonl"))
    d = v.to_dict()
    assert d["effective"] == {key(30): lid(30)}       # S1 의 원 판은 그대로 유효(사라지지 않음)
    assert d["invalid"] == {lid(31): R.P_INVALID_REF, lid(32): R.P_INVALID_REF, lid(33): R.P_INVALID_REF}
    reasons = {p.label_ids[0]: p.reason for p in v.problems if p.code == R.P_INVALID_REF}
    assert reasons == {lid(31): "other_session", lid(32): "target_invalid", lid(33): "other_entity"}
    assert v.status(lid(31)) == R.INVALID and v.effective(lid(31)) is None
    assert v.history(lid(32)) == []
    # 다른 대상의 판을 가리킨 줄의 대상(31)은 연결이 생기지 않는다
    assert v.chain(eid(31), S1) is None


def test_golden_cycle_stops_and_conflicts():
    v = R.resolve(load("labels_cycle.jsonl"))
    d = v.to_dict()
    assert d["status"] == {key(40): R.CONFLICT, key(50): R.ACTIVE}
    assert d["effective"] == {key(50): lid(50)}
    cyc = [p for p in v.problems if p.code == R.P_CYCLE]
    assert len(cyc) == 1 and cyc[0].entity_id == eid(40)
    assert set(cyc[0].label_ids) == {lid(40), lid(41), lid(42)}
    assert cyc[0].label_ids[0] == lid(40)              # seq 가 가장 앞인 줄부터
    assert v.effective(lid(41)) is None and v.status(lid(41)) == R.CONFLICT
    assert len(v.history(lid(40))) == 3                # 충돌이면 모두 보여 준다
    assert [c.entity_id for c in v.conflicts()] == [eid(40)]


def test_golden_fork_is_not_auto_chosen():
    v = R.resolve(load("labels_fork.jsonl"))
    d = v.to_dict()
    assert d["status"] == {key(60): R.CONFLICT, key(70): R.ACTIVE}
    fk = [p for p in v.problems if p.code == R.P_FORK]
    assert len(fk) == 1
    assert fk[0].label_ids == (lid(61), lid(62)) and fk[0].reason == "same_parent"
    assert fk[0].detail == {"supersedes": lid(60)}
    assert v.effective(lid(60)) is None and v.effective(lid(62)) is None
    assert [r["id"] for r in v.history(lid(60))] == [lid(60), lid(61), lid(62)]


def test_golden_truncated_last_line_isolated():
    rr = load("labels_truncated.jsonl")
    assert rr.truncated_tail and rr.ok
    v = R.resolve(rr)                                   # jsonl.read 결과를 그대로 넣는다
    assert v.to_dict() == {
        "effective": {key(80): lid(80), key(90): lid(90)},
        "status": {key(80): R.ACTIVE, key(90): R.ACTIVE},
        "invalid": {},
        "problems": [{"code": R.P_TRUNCATED_TAIL}],
    }


def test_golden_bom_file():
    rr = load("labels_bom.jsonl")
    assert rr.had_bom
    v = R.resolve(rr)
    assert v.effective(lid(100))["id"] == lid(101) and v.problems == []


def test_golden_two_machines_same_session_merge():
    a, b = load("labels_two_machines_a.jsonl"), load("labels_two_machines_b.jsonl")
    for inp in ([a, b], [b, a], a.records + b.records):
        v = R.resolve(inp)
        assert v.to_dict() == {
            "effective": {key(110): lid(112), key(120): lid(120)},
            "status": {key(110): R.ACTIVE, key(120): R.ACTIVE},
            "invalid": {},
            "problems": [],
        }
        assert [r["id"] for r in v.history(lid(110))] == [lid(110), lid(111), lid(112)]


def test_corrupt_middle_line_reported(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_bytes(b'{"v":1}\nnot json\n')
    v = R.resolve(jsonl.read(str(p)))
    assert ("corrupt_line", None) in codes(v)
    assert [x.detail for x in v.problems if x.code == "corrupt_line"] == [{"line": 2}]


# --------------------------------------------------------------------------- 단위
def test_order_independent_and_input_not_mutated():
    recs = [lab(1, 1, 1), lab(2, 2, 1, 1), lab(3, 3, 1, 2), lab(4, 4, 1, 2),     # 포크
            lab(5, 5, 5), lab(6, 6, 5, 5, kind="retract"),
            lab(7, 7, 7, 99)]                                                   # 없는 판 가리킴
    snap = [dict(r) for r in recs]
    want = R.resolve(recs).to_dict()
    rnd = random.Random(7)
    for _ in range(20):
        sh = recs[:]
        rnd.shuffle(sh)
        assert R.resolve(sh).to_dict() == want
    assert recs == snap


def test_missing_target_and_self_reference():
    v = R.resolve([lab(1, 1, 1, sup=99), lab(2, 2, 2, sup=2)])
    assert {(p.label_ids[0], p.reason) for p in v.problems} == {(lid(1), "missing"), (lid(2), "self")}
    assert v.items() == []


def test_multiple_roots_is_fork():
    v = R.resolve([lab(1, 1, 1), lab(2, 2, 1)])
    assert codes(v) == [("fork", "multiple_roots")]
    assert v.chain(eid(1)).status == R.CONFLICT


def test_other_kind_rejected_and_revive_kind_checked():
    recs = [lab(1, 1, 1), lab(2, 2, 1, 1, kind="reaction"),                  # 종류가 다른 판으로 대체 → 무효
            lab(3, 3, 3), lab(4, 4, 3, 3, kind="retract"), lab(5, 5, 3, 4, kind="reaction"),  # 되살릴 때 종류 바뀜
            lab(6, 6, 6), lab(7, 7, 6, 6, kind="retract"), lab(8, 8, 6, 7, kind="retract")]   # 철회의 철회
    v = R.resolve(recs)
    reasons = {p.label_ids[0]: p.reason for p in v.problems}
    assert reasons == {lid(2): "other_kind", lid(5): "other_kind", lid(8): "retract_of_retract"}
    assert v.effective(lid(1))["id"] == lid(1)
    assert v.status(lid(4)) == R.RETRACTED and v.status(lid(7)) == R.RETRACTED


def test_retract_without_target_and_malformed():
    bad = lab(1, 1, 1, kind="retract")
    nokey = lab(2, 2, 2)
    del nokey["supersedes"]
    v = R.resolve([bad, nokey, {"kind": "segment"}, {"kind": "nope", "id": "x"}, 5])
    got = sorted((p.code, p.reason) for p in v.problems)
    assert got == sorted([("invalid_ref", "retract_without_target"), ("malformed", "missing_supersedes"),
                          ("malformed", "missing_id"), ("malformed", "unknown_kind"), ("malformed", "not_object")])
    assert v.status(lid(1)) == R.INVALID and v.status(lid(2)) == R.INVALID
    assert v.status(lid(999)) == R.UNKNOWN


def test_duplicate_id_same_content_merged_different_conflict():
    a = lab(1, 1, 1)
    v = R.resolve([a, dict(a), lab(2, 2, 1, 1)])
    assert v.problems == [] and v.effective(lid(1))["id"] == lid(2)
    b = dict(a, body={"n": "다름"})
    v = R.resolve([a, b, lab(2, 2, 1, 1)])
    assert ("duplicate_id", None) in codes(v)
    assert v.chain(eid(1), S1).status == R.CONFLICT
    assert v.status(lid(1)) == R.INVALID and v.effective(lid(2)) is None


def test_same_entity_in_two_sessions_kept_apart():
    v = R.resolve([lab(1, 1, 1), lab(2, 1, 1, sid=S2)])
    assert v.problems == []
    assert v.chain(eid(1)) is None                          # 세션을 안 주면 애매해서 None
    assert v.effective_entity(eid(1), S2)["id"] == lid(2)
    assert [r["id"] for r in v.items(session_id=S1)] == [lid(1)]


def test_hanging_off_cycle_and_long_chain():
    # 순환(1↔2)에 매달린 3 도 충돌 묶음에 들어간다
    v = R.resolve([lab(1, 1, 1, 2), lab(2, 2, 1, 1), lab(3, 3, 1, 2)])
    assert R.P_CYCLE in [p.code for p in v.problems]
    assert v.status(lid(3)) == R.CONFLICT
    # 긴 연결(2,000판)도 재귀 없이 끝난다
    n = 2000
    recs = [lab(1, 1, 1)] + [lab(i, i, 1, i - 1) for i in range(2, n + 1)]
    v = R.resolve(recs)
    assert v.effective(lid(1))["id"] == lid(n) and len(v.history(lid(5))) == n


def test_counts_and_problem_dict_has_no_body():
    recs = [lab(1, 1, 1), lab(2, 2, 2, sup=1), lab(3, 3, 3), lab(4, 4, 3, 3, kind="retract")]
    v = R.resolve(recs)
    assert v.count_by_status() == {R.ACTIVE: 1, R.RETRACTED: 1, R.CONFLICT: 0, R.INVALID: 1}
    for p in v.problems:
        assert "body" not in str(p.to_dict()) and "n" not in p.to_dict()


@pytest.mark.parametrize("perm", list(itertools.permutations(range(3))))
def test_fixture_files_any_order(perm):
    names = ["labels_simple_edit.jsonl", "labels_retract.jsonl", "labels_fork.jsonl"]
    rrs = [load(names[i]) for i in perm]
    d = R.resolve(rrs).to_dict()
    # 세 파일이 같은 세션 S1 이지만 entity 가 겹치지 않아 각자 결과가 그대로다
    assert d["effective"] == {key(1): lid(3), key(2): lid(4), key(20): lid(22), key(70): lid(70)}
    assert d["status"][key(60)] == R.CONFLICT and d["status"][key(10)] == R.RETRACTED
