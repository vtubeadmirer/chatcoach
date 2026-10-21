"""재라벨 계획 취소 기록과 색인 relabel/index.json (app/relabel.py, schemas/relabel-plan.schema.json $defs).

설계: 08 D-12 2(세션 화면은 색인만 읽고 다른 라벨러 계획 본문을 열지 않음)·7(검토자가 사유와 함께 취소, 지우지 않고
취소 기록), 06 7-1, 13 4-3 의 1·3·4. 합성 데이터만 쓴다(tmp_path 의 합성 프로젝트).
"""
import builtins
import hashlib
import json
import os

import pytest

from app import agreement_report as ar
from app import relabel, store
from tests.test_relabel_plan import NOW, SRC, _create, _project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sub_schema(name):
    with open(os.path.join(ROOT, "schemas", "relabel-plan.schema.json"), encoding="utf-8") as f:
        root = json.load(f)
    return store.LabelSchema({"$defs": root["$defs"], **root["$defs"][name]})


def _index_file(root):
    with open(relabel.index_path(root), encoding="utf-8") as f:
        return json.load(f)


class _OpenSpy:
    def __init__(self, monkeypatch):
        self.paths = []
        real = builtins.open

        def spy(file, *a, **k):
            self.paths.append(os.path.abspath(str(file)))
            return real(file, *a, **k)
        monkeypatch.setattr(builtins, "open", spy)


def _cancel(root, pid, reason="시험용 계획이라 쓰지 않음", kind="adjudication", by="L01"):
    return relabel.cancel_plan(root, pid, reason=reason, cancelled_by=by, cancelled_machine4="aaaa",
                               app_version="0.0.0", canceller_kind=kind, now=NOW)


# ============================================================================ 색인

def test_create_plan_writes_index_with_only_five_fields(tmp_path):
    root, data, *_ = _project(tmp_path)
    p1 = _create(root, data, "relabel_same", "L01", rand4="ab12")
    p2 = _create(root, data, "relabel_second", "L02", rand4="cd34")
    doc = _index_file(root)
    assert doc == {"schema": "chatcoach/relabel-index", "schema_version": 1, "plans": [
        {"plan_id": p1["plan_id"], "source_session_id": SRC, "labeler_id": "L01", "session_kind": "relabel_same",
         "status": "active"},
        {"plan_id": p2["plan_id"], "source_session_id": SRC, "labeler_id": "L02", "session_kind": "relabel_second",
         "status": "active"}]}
    assert relabel.check_index(doc) == [] and _sub_schema("planIndex").check(doc) == []
    assert relabel.read_index(root) == doc
    dumped = json.dumps(doc)
    for leak in ("ranges", "tau", "block", "600000", "seed"):
        assert leak not in dumped                       # 블록·시각·τ 는 색인에 없다
    # 임시 파일이 남지 않음
    assert sorted(os.listdir(os.path.join(root, "relabel"))) == [p1["plan_id"] + ".json", p2["plan_id"] + ".json",
                                                                  "index.json"]


def test_session_screen_opens_only_own_plan(tmp_path, monkeypatch):
    root, data, *_ = _project(tmp_path)
    p1 = _create(root, data, "relabel_same", "L01", rand4="ab12")
    p2 = _create(root, data, "relabel_second", "L02", rand4="cd34")
    spy = _OpenSpy(monkeypatch)
    got = relabel.plans_for_labeler(root, "L02")
    assert got.from_index and got.unindexed == 0 and got.others == 1 and got.opened == (p2["plan_id"],)
    assert [p.plan_id for p in got.plans] == [p2["plan_id"]] and got.plans[0].usable
    opened_plans = [x for x in spy.paths if os.path.basename(x).startswith("RP-")
                    and not x.endswith(".cancel.json")]
    assert not [x for x in spy.paths if os.path.basename(x).startswith(p1["plan_id"])]   # L01 쪽은 취소 기록도 안 엶
    assert opened_plans == [os.path.abspath(relabel.plan_path(root, p2["plan_id"]))]   # L01 계획 본문은 열지 않음
    assert all(os.sep + "samples" + os.sep not in x and SRC not in os.path.basename(x) for x in spy.paths)
    assert relabel.plans_for_labeler(root, "L03").plans == ()
    assert p1["plan_id"] not in relabel.plans_for_labeler(root, "L03").opened


def test_missing_or_broken_index_is_rebuilt_and_unindexed_plans_trigger_rebuild(tmp_path):
    root, data, *_ = _project(tmp_path)
    p1 = _create(root, data, "relabel_second", "L02", rand4="ab12")
    os.remove(relabel.index_path(root))
    got = relabel.plans_for_labeler(root, "L02")
    assert got.from_index and got.rebuilt == "색인이 없습니다" and got.problem is None
    assert [p.plan_id for p in got.plans] == [p1["plan_id"]] and os.path.isfile(relabel.index_path(root))
    with open(relabel.index_path(root), "wb") as f:
        f.write(b'{"schema": "chatcoach/relabel-index", "schema_version": 1, "plans": [{"plan_id": 1}]}')
    with pytest.raises(ValueError):
        relabel.read_index(root)
    got = relabel.plans_for_labeler(root, "L02")
    assert got.from_index and "형식" in got.rebuilt and relabel.read_index(root) is not None
    # 색인 밖에서 생긴 계획(색인 쓰기가 실패한 뒤 등) → 그 자리에서 다시 만든다
    p2 = _create(root, data, "relabel_second", "L02", rand4="cd34")
    with open(relabel.index_path(root), "w", encoding="utf-8") as f:     # p2 가 빠진 옛 색인을 흉내
        json.dump({"schema": "chatcoach/relabel-index", "schema_version": 1, "plans": [
            {"plan_id": p1["plan_id"], "source_session_id": SRC, "labeler_id": "L02", "session_kind": "relabel_second",
             "status": "active"}]}, f)
    got = relabel.plans_for_labeler(root, "L02")
    assert got.from_index and got.unindexed == 0 and got.rebuilt == "색인에 없는 계획 파일 1개"
    assert {p.plan_id for p in got.plans} == {p1["plan_id"], p2["plan_id"]}
    assert relabel.plans_for_labeler(root, "L02").rebuilt is None          # 다음부터는 다시 만들지 않는다


def _rp_opened(spy, pid):
    return [x for x in spy.paths if os.path.basename(x).startswith(pid)]


@pytest.mark.parametrize("state", ["no_index", "unindexed"])
def test_session_screen_never_reopens_other_labelers_plan_after_rebuild(tmp_path, monkeypatch, state):
    """재현(검토 지적): 색인이 없거나(넷째 묶음 프로젝트) L01 계획이 색인에 없으면, 예전에는 L02 를 고를 때마다 L01 계획
    본문과 취소 기록을 열었다. 이제는 한 번 색인을 다시 만들고, 그 뒤 고르기는 자기 계획만 연다."""
    root, data, *_ = _project(tmp_path)
    p1 = _create(root, data, "relabel_same", "L01", rand4="ab12")
    p2 = _create(root, data, "relabel_second", "L02", rand4="cd34")
    if state == "no_index":
        os.remove(relabel.index_path(root))
    else:
        doc = _index_file(root)
        doc["plans"] = [e for e in doc["plans"] if e["plan_id"] != p1["plan_id"]]
        with open(relabel.index_path(root), "w", encoding="utf-8") as f:
            json.dump(doc, f)
    first = relabel.plans_for_labeler(root, "L02")
    assert first.from_index and first.rebuilt and first.opened == (p2["plan_id"],)
    assert first.others == 1 and [p.plan_id for p in first.plans] == [p2["plan_id"]]
    spy = _OpenSpy(monkeypatch)
    again = relabel.plans_for_labeler(root, "L02")
    assert again.rebuilt is None and again.opened == (p2["plan_id"],)
    assert _rp_opened(spy, p1["plan_id"]) == []                      # L01 계획 본문·취소 기록 모두 열지 않음


def test_rebuild_failure_opens_no_plan_and_blocks_relabel(tmp_path, monkeypatch):
    root, data, *_ = _project(tmp_path)
    p1 = _create(root, data, "relabel_same", "L01", rand4="ab12")
    p2 = _create(root, data, "relabel_second", "L02", rand4="cd34")
    os.remove(relabel.index_path(root))

    def boom(*a, **k):
        raise PermissionError("잠김")                                 # OneDrive 가 잡은 것처럼
    monkeypatch.setattr(relabel, "rebuild_index", boom)
    spy = _OpenSpy(monkeypatch)
    got = relabel.plans_for_labeler(root, "L02")
    assert not got.from_index and got.plans == () and got.opened == () and "색인" in got.problem
    assert [p.plan_id for p in got.unreadable] == [p1["plan_id"], p2["plan_id"]]
    assert all(not p.usable and p.problem == got.problem for p in got.unreadable)
    assert [x for x in spy.paths if os.path.basename(x).startswith("RP-")] == []   # 어떤 계획 파일도 열지 않음


def test_plan_created_after_rebuild_race_is_skipped_unopened(tmp_path, monkeypatch):
    root, data, *_ = _project(tmp_path)
    p1 = _create(root, data, "relabel_same", "L01", rand4="ab12")
    p2 = _create(root, data, "relabel_second", "L02", rand4="cd34")
    os.remove(relabel.index_path(root))
    stale = {"schema": "chatcoach/relabel-index", "schema_version": 1, "plans": [
        {"plan_id": p2["plan_id"], "source_session_id": SRC, "labeler_id": "L02", "session_kind": "relabel_second",
         "status": "active"}]}
    monkeypatch.setattr(relabel, "rebuild_index", lambda *a, **k: stale)   # 다시 만든 사이 p1 이 새로 생긴 것처럼
    spy = _OpenSpy(monkeypatch)
    got = relabel.plans_for_labeler(root, "L02")
    assert got.from_index and got.unindexed == 1 and got.opened == (p2["plan_id"],)
    assert _rp_opened(spy, p1["plan_id"]) == []


@pytest.mark.parametrize("kind", ["relabel_same", "relabel_second", "origin_review", "analysis"])
def test_rebuild_and_cancel_refused_before_any_open(tmp_path, monkeypatch, kind):
    root, data, *_ = _project(tmp_path)
    p = _create(root, data, "relabel_second", "L02")
    spy = _OpenSpy(monkeypatch)
    if kind in ("relabel_same", "relabel_second"):
        with pytest.raises(relabel.RelabelBlindError):
            relabel.rebuild_index(root, creator_kind=kind)
    with pytest.raises(relabel.RelabelBlindError):
        _cancel(root, p["plan_id"], kind=kind)
    assert spy.paths == []


def test_primary_cannot_cancel_but_may_rebuild(tmp_path):
    root, data, *_ = _project(tmp_path)
    p = _create(root, data, "relabel_second", "L02")
    with pytest.raises(relabel.RelabelBlindError):
        _cancel(root, p["plan_id"], kind="primary")     # 취소는 검토자(조정 세션) 자리
    assert relabel.rebuild_index(root, creator_kind="primary")["plans"][0]["status"] == "active"


# ============================================================================ 취소

def test_cancel_needs_reason_and_keeps_plan_file(tmp_path):
    root, data, *_ = _project(tmp_path)
    p = _create(root, data, "relabel_second", "L02", rand4="ab12")
    path = relabel.plan_path(root, p["plan_id"])
    with open(path, "rb") as f:
        before = f.read()
    for bad in ("", "   ", None, "가" * 1001):
        with pytest.raises(ValueError):
            _cancel(root, p["plan_id"], reason=bad)
    assert not os.path.exists(relabel.cancel_path(root, p["plan_id"]))
    with pytest.raises(FileNotFoundError):
        _cancel(root, "RP-20261021-zzzz")
    doc = _cancel(root, p["plan_id"], reason="  두 번째 사람이 없어 쓰지 않음  ")
    assert doc["reason"] == "두 번째 사람이 없어 쓰지 않음" and doc["plan_readable"] is True
    assert doc["plan_sha256"] == hashlib.sha256(before).hexdigest()
    assert doc["source_session_id"] == SRC and doc["labeler_id"] == "L02" and doc["session_kind"] == "relabel_second"
    assert doc["canceller_is_labeler"] is True                # L01 = 원 세션 라벨러 (1인 작업 표시)
    assert relabel.check_cancel(doc) == [] and _sub_schema("cancelRecord").check(doc) == []
    with open(path, "rb") as f:
        assert f.read() == before                             # 계획 파일은 고치지 않음
    assert relabel.read_cancel(root, p["plan_id"]) == doc and relabel.is_cancelled(root, p["plan_id"])
    with pytest.raises(FileExistsError):
        _cancel(root, p["plan_id"])
    # 다른 사람(외부 검토자)이 취소하면 표시가 꺼진다
    p2 = _create(root, data, "relabel_same", "L01", rand4="cd34")
    assert _cancel(root, p2["plan_id"], by="R01")["canceller_is_labeler"] is False


def test_cancelled_plan_leaves_lists_pending_and_session_screen(tmp_path):
    root, data, *_ = _project(tmp_path)
    ps = _create(root, data, "relabel_same", "L01", rand4="ab12")
    p2 = _create(root, data, "relabel_second", "L02", rand4="cd34")
    assert sorted(ar.pending_relabel_plans(root)) == sorted([ps["plan_id"], p2["plan_id"]])
    _cancel(root, ps["plan_id"])
    assert ar.pending_relabel_plans(root) == [p2["plan_id"]]     # 취소한 계획은 보고서를 막지 않는다
    assert [p.plan_id for p in relabel.list_plans(root)] == [p2["plan_id"]]
    allp = relabel.list_plans(root, include_cancelled=True)
    assert [(p.plan_id, p.status, p.usable) for p in allp] == [(ps["plan_id"], "cancelled", False),
                                                                (p2["plan_id"], "active", True)]
    assert [e["status"] for e in _index_file(root)["plans"]] == ["cancelled", "active"]
    got = relabel.plans_for_labeler(root, "L01")
    assert got.plans == () and got.cancelled == 1
    with pytest.raises(ValueError, match="취소"):
        relabel.read_plan_for_session(root, "relabel_same", "L01", ps["plan_id"])
    assert relabel.next_plan_no(root) == 3                    # 취소한 계획 번호도 쓰인 번호
    names = sorted(os.listdir(os.path.join(root, "relabel")))
    assert names == sorted([ps["plan_id"] + ".json", ps["plan_id"] + ".cancel.json", p2["plan_id"] + ".json",
                            "index.json"])


def test_stale_index_still_honours_cancel_file(tmp_path):
    root, data, *_ = _project(tmp_path)
    ps = _create(root, data, "relabel_same", "L01", rand4="ab12")
    with open(relabel.index_path(root), "rb") as f:
        old = f.read()
    _cancel(root, ps["plan_id"])
    with open(relabel.index_path(root), "wb") as f:           # 색인 쓰기가 실패했던 것처럼 옛 색인으로 되돌림
        f.write(old)
    assert relabel.plans_for_labeler(root, "L01").plans == ()


def test_unreadable_plan_can_be_cancelled_and_stops_blocking(tmp_path):
    root, data, *_ = _project(tmp_path)
    bad = "RP-20261021-zz99"
    with open(relabel.plan_path(root, bad), "wb") as f:
        f.write(b"{not json")
    assert ar.pending_relabel_plans(root) == [bad]             # 읽지 못한 계획은 보고서를 막는다
    relabel.rebuild_index(root, creator_kind=None)
    assert _index_file(root)["plans"] == [{"plan_id": bad, "source_session_id": None, "labeler_id": None,
                                           "session_kind": None, "status": "unreadable"}]
    doc = _cancel(root, bad)
    assert doc["plan_readable"] is False and doc["source_session_id"] is None and doc["canceller_is_labeler"] is False
    assert _sub_schema("cancelRecord").check(doc) == []
    assert ar.pending_relabel_plans(root) == []
    assert _index_file(root)["plans"][0]["status"] == "cancelled"


def test_broken_cancel_record_is_not_a_cancel(tmp_path):
    root, data, *_ = _project(tmp_path)
    p = _create(root, data, "relabel_second", "L02")
    with open(relabel.cancel_path(root, p["plan_id"]), "w", encoding="utf-8") as f:
        json.dump({"schema": "chatcoach/relabel-cancel", "schema_version": 1, "plan_id": p["plan_id"]}, f)
    assert not relabel.is_cancelled(root, p["plan_id"])
    with pytest.raises(ValueError):
        relabel.read_cancel(root, p["plan_id"])
    assert ar.pending_relabel_plans(root) == [p["plan_id"]]   # 깨진 취소 → 계속 막힘(블라인드 쪽으로 안전)
    assert [x.plan_id for x in relabel.list_plans(root)] == [p["plan_id"]]


def test_cancel_file_name_does_not_look_like_a_plan(tmp_path):
    root, data, *_ = _project(tmp_path)
    p = _create(root, data, "relabel_second", "L02")
    _cancel(root, p["plan_id"])
    from app import session
    assert session.RELABEL_PLAN_RE.match(os.path.basename(relabel.cancel_path(root, p["plan_id"]))) is None
    assert session.RELABEL_PLAN_RE.match("index.json") is None
