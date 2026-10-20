"""app/store.py — 세션 기록 열기·쓰기·다시 열기·잠금·잘린 줄·스키마·철회 (03 6-3·8-1·8-2·10·13). 합성 데이터만."""
import copy
import json
import os
import statistics
import warnings
from datetime import datetime, timedelta, timezone

import pytest

from app import jsonl, project, session, store

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 6, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-555555555555"
SALT = bytes(range(32))
MID_A = "aaaaaaaa-0000-4000-8000-000000000001"
MID_B = "bbbbbbbb-0000-4000-8000-000000000002"


# ---------------------------------------------------------------------------- 도우미

class FakeLock:
    """QLockFile 대역. held 집합에 있는 경로는 잡을 수 없다."""
    held: set = set()

    def __init__(self, path):
        self.path = path
        self.stale = None
        self.locked = False

    def setStaleLockTime(self, ms):
        self.stale = ms

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
        return (1234, "SYNTH-HOST", "chatcoach")


@pytest.fixture(autouse=True)
def _clear_locks():
    FakeLock.held = set()
    yield
    FakeLock.held = set()


class Clock:
    def __init__(self, start=NOW):
        self.t = start

    def __call__(self):
        self.t += timedelta(milliseconds=10)
        return self.t


def uuids():
    n = [0]

    def make():
        n[0] += 1
        return f"{n[0]:08x}-0000-4000-8000-{n[0]:012x}"
    return make


def _guide():
    return project.GuideSource("0.1", b"# guide\n", {"reaction": {"time_window_sec": 20}})


@pytest.fixture
def proj_dir(tmp_path):
    base = tmp_path / "한글 문서" / "프로젝트들"
    p = project.create(str(base), "합성 방송 제목", 123, "시험용 목적", "L01", "0.0.1", NOW, PID, SALT,
                       guide=_guide(), machine4="m3fa")
    assert not p.read_only
    return p.root


def ctx(mid=MID_A, sid="L01-P-20261021-ab12", kind="primary", labeler="L01", **kw):
    return session.SessionContext(labeler, kind, sid, session.machine4(mid), machine_id=mid,
                                  session_created_at="2026-10-21T06:00:00+09:00", **kw)


def open_store(root, c=None, lock=FakeLock, **kw):
    kw.setdefault("clock", Clock())
    kw.setdefault("app_version", "0.0.1")
    return store.ProjectStore.open(root, c or ctx(), lock, **kw)


def seg_body(start=0, end=60000, cat="just_chatting"):
    return {"start_ms": start, "end_ms": end, "category": cat, "confidence": "certain", "rationale": "00:00 합성 구간"}


def file_lines(st):
    with open(st.path, "rb") as f:
        return f.read().split(b"\n")


# ---------------------------------------------------------------------------- 열기·쓰기·다시 열기

def test_open_write_reopen_in_korean_folder(proj_dir):
    st = open_store(proj_dir, uuid_factory=uuids())
    assert not st.read_only, st.read_only_reasons
    m4 = session.machine4(MID_A)
    assert st.path == os.path.join(proj_dir, "labels", f"L01-P-20261021-ab12.{m4}.jsonl")
    recs = st.records()
    assert [r["kind"] for r in recs] == ["session.open"]
    so = recs[0]
    assert so["seq"] == 1 and so["v"] == 1
    assert so["body"]["project_id"] == PID and so["body"]["machine4"] == m4 and so["body"]["machine_id"] == MID_A
    assert so["body"]["scope"] == {"type": "whole"} and so["body"]["dataset_split"] == "dev"
    assert so["body"]["blind"]["to_other_sessions"] is True
    assert so["body"]["session_created_at"] == "2026-10-21T06:00:00+09:00"

    r = st.append_label({"kind": "segment", "body": seg_body()})
    assert r["seq"] == 2 and r["supersedes"] is None and r["entity_id"] != r["id"]
    assert r["session_id"] == "L01-P-20261021-ab12" and r["labeler_id"] == "L01"
    assert r["guide_version"] == "0.1" and len(r["guide_sha12"]) == 12 and len(r["thresholds_sha12"]) == 12
    assert r["guide_sha12"] == so["body"]["guide_doc_sha256"][:12]
    assert r["media_rev"] == 0 and r["calibration_rev"] == 0
    assert r["created_at"].endswith("+09:00") and "." in r["created_at"]
    # 돌려준 사본을 고쳐도 store 안은 그대로
    r["body"]["category"] = "game"
    assert st.records()[1]["body"]["category"] == "just_chatting"
    st.close()

    # 파일 형식: UTF-8, BOM 없음, LF, 한글 그대로
    with open(st.path, "rb") as f:
        raw = f.read()
    assert not raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"\n") and b"\r\n" not in raw
    assert "합성 구간".encode("utf-8") in raw

    st2 = open_store(proj_dir, uuid_factory=uuids_from(100))
    assert not st2.read_only, st2.read_only_reasons
    # session.open 을 다시 쓰지 않는다. 닫을 때 session.close 가 남는다(08 D-09 6)
    assert [x["kind"] for x in st2.records()] == ["session.open", "segment", "session.close"]
    r2 = st2.append_label({"kind": "segment", "body": seg_body(60000, 120000, "game")})
    assert r2["seq"] == 4
    st2.close()
    st3 = open_store(proj_dir)
    assert [x["kind"] for x in st3.records()] == ["session.open", "segment", "session.close", "segment",
                                                  "session.close"] and not st3.read_only
    st3.close()


def uuids_from(start):
    n = [start]

    def make():
        n[0] += 1
        return f"{n[0]:08x}-0000-4000-8000-{n[0]:012x}"
    return make


def test_run_info_and_ops_records(proj_dir):
    run = {"utc_offset_min": 540, "clock_source": "none", "data_dir_mode": "portable",
           "update_check_state": "never", "checked_at": None, "latest_seen_version": None}
    st = open_store(proj_dir, run_info=run)
    assert [r["kind"] for r in st.records()] == ["session.open", "run.start"]
    rec = st.append_ops("phase.mark", {"phase": "segments", "scope": "all", "done": True})
    assert rec["seq"] == 3 and "entity_id" not in rec
    with pytest.raises(store.LabelValidationError):
        st.append_ops("session.open", {})
    with pytest.raises(store.LabelValidationError):
        st.append_ops("warning", {"code": "nope", "action": "shown"})
    st.close()
    bad = dict(run, update_check_state="never", checked_at="2026-10-21T06:00:00+09:00")   # if/then 위반
    with pytest.raises(store.LabelValidationError):
        open_store(proj_dir, run_info=bad)


def test_injected_clock_and_uuid_are_used(proj_dir):
    st = open_store(proj_dir, clock=lambda: datetime(2026, 10, 21, 7, 8, 9, 123000, tzinfo=KST),
                    uuid_factory=uuids())
    r = st.append_label({"kind": "segment", "body": seg_body()})
    assert r["created_at"] == "2026-10-21T07:08:09.123+09:00"
    # 1번은 session.open, 새 대상 ID 를 먼저 정하고 그다음 판 ID
    assert r["entity_id"] == "00000002-0000-4000-8000-000000000002"
    assert r["id"] == "00000003-0000-4000-8000-000000000003"
    st.close()


def test_naive_clock_gets_os_offset(proj_dir):
    st = open_store(proj_dir, clock=lambda: datetime(2026, 10, 21, 7, 0, 0))
    r = st.append_label({"kind": "segment", "body": seg_body()})
    assert r["created_at"][-6] in "+-"
    st.close()


# ---------------------------------------------------------------------------- 잠금

def test_lock_conflict_opens_read_only(proj_dir):
    a = open_store(proj_dir)
    assert not a.read_only
    b = open_store(proj_dir, ctx(MID_B, "L02-P-20261021-cd34", labeler="L02"))
    assert b.read_only
    assert "다른 창이나 PC" in b.read_only_reason and "SYNTH-HOST" in b.read_only_reason
    assert not os.path.exists(b.path)        # 읽기 전용이면 session.open 도 쓰지 않는다
    with pytest.raises(store.ReadOnlyStoreError):
        b.append_label({"kind": "segment", "body": seg_body()})
    a.close()
    c = open_store(proj_dir, ctx(MID_B, "L02-P-20261021-cd34", labeler="L02"))
    assert not c.read_only                   # 앞 창이 닫히면 잡힌다
    c.close()


def test_real_qlockfile_conflict(proj_dir):
    qtcore = pytest.importorskip("PySide6.QtCore")
    lock = qtcore.QLockFile                    # 잠금은 호출하는 쪽이 주입한다(08 D-09 8)
    a = open_store(proj_dir, lock=lock)
    assert not a.read_only, a.read_only_reasons
    assert os.path.exists(os.path.join(proj_dir, "locks", "open.lock"))
    b = open_store(proj_dir, lock=lock)
    assert b.read_only and "다른 창이나 PC" in b.read_only_reason
    assert b.lock_busy and b.lock_help
    b.close()
    a.close()
    assert not os.path.exists(os.path.join(proj_dir, "locks", "open.lock"))
    c = open_store(proj_dir, lock=lock)
    assert not c.read_only
    c.close()


@pytest.mark.parametrize("bad", [None, "QLockFile", 0])
def test_lock_factory_is_required(proj_dir, bad):
    with pytest.raises(TypeError):
        store.ProjectStore.open(proj_dir, ctx(), bad, clock=Clock(), app_version="0.0.1")
    with pytest.raises(TypeError):
        store.ProjectStore.open(proj_dir, ctx())          # pylint: disable=no-value-for-parameter
    assert not os.path.exists(os.path.join(proj_dir, "labels", ctx().file_name))


def test_store_has_no_qt_import():
    src = open(store.__file__, encoding="utf-8").read()
    assert "importlib" not in src and "import PySide6" not in src and "from PySide6" not in src


def test_lock_failure_help_other_pc_and_same_pc(proj_dir):
    a = open_store(proj_dir)
    b = open_store(proj_dir, ctx(MID_B, "L02-P-20261021-cd34", labeler="L02"))
    assert b.lock_busy and b.lock_holder == {"pid": 1234, "host": "SYNTH-HOST", "app": "chatcoach"}
    other = store.lock_failure_help(b.lock_holder, host="ANOTHER-HOST")
    assert "SYNTH-HOST" in other and "locks\\open.lock" in other and "지우" in other
    same = store.lock_failure_help(b.lock_holder, host="synth-host")
    assert "이 PC" in same and "open.lock" not in same          # 같은 PC 면 지우라고 하지 않는다
    assert "open.lock" in store.lock_failure_help(None, host="X")
    assert not a.lock_busy and a.lock_help is None
    b.close()
    a.close()
    assert os.path.exists(os.path.join(proj_dir, "locks")) and not FakeLock.held


def test_stale_lock_time_is_set(proj_dir):
    made = []

    class L(FakeLock):
        def __init__(self, p):
            super().__init__(p)
            made.append(self)
    st = open_store(proj_dir, lock=L)
    assert made[0].stale == store.STALE_LOCK_MS
    st.close()
    assert not made[0].locked


def test_close_is_idempotent_and_blocks_writes(proj_dir):
    with open_store(proj_dir) as st:
        st.append_label({"kind": "segment", "body": seg_body()})
    st.close()
    with pytest.raises(store.ReadOnlyStoreError):
        st.append_label({"kind": "segment", "body": seg_body()})


# ---------------------------------------------------------------------------- 잘린 마지막 줄

def test_append_after_truncated_last_line(proj_dir):
    st = open_store(proj_dir)
    st.append_label({"kind": "segment", "body": seg_body()})
    st.close()
    with open(st.path, "ab") as f:
        f.write(b'{"v":1,"id":"trunc')         # 전원 차단으로 잘린 줄 흉내
    st2 = open_store(proj_dir)
    assert not st2.read_only, st2.read_only_reasons
    assert any("잘려" in w for w in st2.warnings)
    assert len(st2.partial_files) == 1
    part = os.path.join(proj_dir, "labels", st2.partial_files[0])
    with open(part, "rb") as f:
        assert f.read() == b'{"v":1,"id":"trunc'
    r = st2.append_label({"kind": "segment", "body": seg_body(60000, 90000)})
    assert r["seq"] == 4                       # 1 open · 2 segment · 3 session.close · 4
    st2.close()
    res = jsonl.read(st.path)
    assert res.ok and not res.truncated_tail and [x["seq"] for x in res.records] == [1, 2, 3, 4, 5]
    st3 = open_store(proj_dir)
    assert not st3.read_only and not st3.partial_files
    st3.close()


def test_truncated_tail_in_read_only_store_is_left_alone(proj_dir):
    st = open_store(proj_dir)
    st.close()
    with open(st.path, "ab") as f:
        f.write(b'{"v":1')
    FakeLock.held.add(os.path.join(proj_dir, "locks", "open.lock"))
    ro = open_store(proj_dir)
    assert ro.read_only and not ro.partial_files
    assert [r["kind"] for r in ro.records()] == ["session.open", "session.close"]
    with open(st.path, "rb") as f:
        assert f.read().endswith(b'{"v":1')       # 읽기 전용이면 파일을 건드리지 않는다


# ---------------------------------------------------------------------------- 스키마 위반

@pytest.mark.parametrize("draft", [
    {"kind": "segment", "body": {"start_ms": 0, "end_ms": 1000, "category": "just_chatting", "confidence": "certain"}},
    {"kind": "segment", "body": dict(seg_body(), category="cooking")},
    {"kind": "segment", "body": dict(seg_body(), start_ms=1.5)},
    {"kind": "segment", "body": dict(seg_body(), start_ms=True)},
    {"kind": "segment", "body": dict(seg_body(), rationale="")},
    {"kind": "segment", "body": dict(seg_body(), surprise=1)},
    {"kind": "segment", "body": dict(seg_body(), active_chat=True)},          # if/then: 게임 구간만
    {"kind": "segment", "body": dict(seg_body(), start_ms=10 ** 12)},
    {"kind": "segment", "body": "문자열"},
    {"kind": "nope", "body": {}},
    {"kind": "segment", "body": seg_body(), "seq": 99},                         # store 가 채우는 필드
    {"kind": "segment", "body": seg_body(), "labeler_id": "L09"},
    {"kind": "segment", "body": seg_body(), "guide_version": "9.9"},
    {"kind": "segment", "body": seg_body(), "extra": 1},
])
def test_schema_violations_are_rejected(proj_dir, draft):
    st = open_store(proj_dir)
    before = open(st.path, "rb").read()
    with pytest.raises(store.LabelValidationError) as ei:
        st.append_label(draft)
    assert ei.value.problems
    assert open(st.path, "rb").read() == before            # 한 바이트도 쓰지 않는다
    st.close()


def test_reaction_conditional_rules():
    sch = store.load_label_schema()
    base = {
        "v": 1, "id": "00000001-0000-4000-8000-000000000001", "seq": 2, "kind": "reaction",
        "session_id": "L01-P-20261021-ab12", "labeler_id": "L01", "created_at": "2026-10-21T06:00:00.000+09:00",
        "app_version": "0.0.1", "entity_id": "00000002-0000-4000-8000-000000000002", "supersedes": None,
        "guide_version": "0.1", "guide_sha12": "0123456789ab", "thresholds_sha12": "0123456789ab",
        "media_rev": 0, "calibration_rev": 0,
        "body": {
            "chat_id": "c" + "0" * 20, "segment_id": "00000003-0000-4000-8000-000000000003",
            "entry": "from_utterance", "result": "unconfirmed", "grade": None, "eligible_grades": [],
            "grade_vs_conditions": "not_applicable", "candidates": [], "linked_utterance_id": None,
            "first_visible_ms": 1000, "first_visible_source": "chat_clock", "reaction_time_ms": None,
            "confidence": "probably", "rationale": "00:01 합성",
        },
    }
    assert sch.check(base) == []
    bad = copy.deepcopy(base)
    bad["body"]["grade"] = "direct"                 # unconfirmed 인데 등급
    assert sch.check(bad)
    bad = copy.deepcopy(base)
    bad["body"]["eligible_grades"] = ["direct", "direct"]   # uniqueItems + maxItems
    assert sch.check(bad)
    bad = copy.deepcopy(base)
    bad["body"]["entry"] = "from_sample"             # sample_ref 필수
    assert sch.check(bad)
    bad = copy.deepcopy(base)
    bad["x"] = {"ok": 1}
    assert sch.check(bad) == []
    bad["y"] = 1                                     # unevaluatedProperties
    assert any("모르는 필드 'y'" in p for p in sch.check(bad))
    bad = copy.deepcopy(base)
    del bad["entity_id"]                              # 라벨 메타 필수
    assert sch.check(bad)
    bad = copy.deepcopy(base)
    bad["v"] = 1.0
    assert sch.check(bad)


def test_schema_retract_requires_supersedes_uuid():
    sch = store.load_label_schema()
    rec = {"v": 1, "id": "00000001-0000-4000-8000-000000000001", "seq": 2, "kind": "retract",
           "session_id": "L01-P-20261021-ab12", "labeler_id": "L01", "created_at": "2026-10-21T06:00:00+09:00",
           "app_version": "0.0.1", "entity_id": "00000002-0000-4000-8000-000000000002",
           "supersedes": "00000003-0000-4000-8000-000000000003",
           "guide_version": "0.1", "guide_sha12": "0123456789ab", "thresholds_sha12": "0123456789ab",
           "media_rev": 0, "calibration_rev": 0, "body": {"reason": "undo"}}
    assert sch.check(rec) == []
    rec["supersedes"] = None
    assert sch.check(rec)


def test_unknown_schema_key_raises():
    s = store.LabelSchema({"$defs": {}, "type": "object", "patternProperties": {}})
    with pytest.raises(ValueError):
        s.check({})


def test_origin_label_needs_origin_review_session(proj_dir):
    st = open_store(proj_dir)
    with pytest.raises(store.LabelValidationError):
        st.append_label({"kind": "origin", "body": {"chat_id": "c" + "0" * 20, "origin": "real", "method": "manual",
                                                    "confidence": "certain", "rationale": "00:01 합성"}})
    st.close()


# ---------------------------------------------------------------------------- 고치기·철회

def test_supersede_and_retract_chain(proj_dir):
    st = open_store(proj_dir)
    a = st.append_label({"kind": "segment", "body": seg_body()})
    eid = a["entity_id"]
    # 이미 있는 대상을 supersedes 없이 → 거부
    with pytest.raises(store.ChainError):
        st.append_label({"kind": "segment", "body": seg_body(), "entity_id": eid})
    b = st.append_label({"kind": "segment", "body": seg_body(0, 50000), "entity_id": eid, "supersedes": a["id"]})
    assert b["entity_id"] == eid and b["supersedes"] == a["id"]
    # 머리가 아닌 판을 supersedes → 거부(포크 방지)
    with pytest.raises(store.ChainError):
        st.append_label({"kind": "segment", "body": seg_body(), "entity_id": eid, "supersedes": a["id"]})
    # 다른 종류로 고치기 → 거부
    with pytest.raises(store.ChainError):
        st.append_label({"kind": "utterance", "entity_id": eid, "supersedes": b["id"],
                         "body": {"start_ms": 0, "end_ms": 1000, "heard_text": "합성", "heard_text_source": "typed"}})
    # 머리가 아닌 판을 철회 → 거부
    with pytest.raises(store.ChainError):
        st.retract(a["id"], "undo")
    with pytest.raises(store.LabelValidationError):
        st.retract(eid, "because")
    with pytest.raises(store.LabelValidationError):
        st.retract(eid, "merged")                 # merged_into 필요
    r = st.retract(eid, "wrong_target", note="합성 메모")
    assert r["kind"] == "retract" and r["entity_id"] == eid and r["supersedes"] == b["id"]
    assert r["body"] == {"reason": "wrong_target", "note": "합성 메모"}
    assert st.head(eid)["kind"] == "retract"
    with pytest.raises(store.ChainError):
        st.retract(eid, "undo")                   # 이미 철회됨
    with pytest.raises(store.ChainError):
        st.retract("99999999-0000-4000-8000-000000000009", "undo")
    # 되살리기: retract 줄을 supersedes
    c = st.append_label({"kind": "segment", "body": seg_body(0, 55000), "entity_id": eid, "supersedes": r["id"]})
    assert st.head(eid)["id"] == c["id"]
    st.close()
    # 다시 열어도 같은 연결
    st2 = open_store(proj_dir)
    assert st2.head(eid)["id"] == c["id"]
    with pytest.raises(store.ChainError):
        st2.append_label({"kind": "segment", "body": seg_body(), "entity_id": eid, "supersedes": b["id"]})
    r2 = st2.retract(c["id"], "undo")              # 머리 판 id 로도 철회할 수 있다
    assert r2["supersedes"] == c["id"]
    st2.close()


def test_retract_last_undo(proj_dir):
    st = open_store(proj_dir)
    with pytest.raises(store.StoreError):
        st.retract_last()
    a = st.append_label({"kind": "segment", "body": seg_body()})
    u = st.retract_last()                          # 방금 만든 것 → retract(undo)
    assert u["kind"] == "retract" and u["body"]["reason"] == "undo"
    v = st.retract_last()                          # 되돌리기를 되돌림 → 되살림
    assert v["kind"] == "segment" and v["supersedes"] == u["id"] and v["body"] == a["body"]
    e = st.append_label({"kind": "segment", "body": seg_body(0, 30000), "entity_id": a["entity_id"],
                         "supersedes": v["id"]})
    w = st.retract_last()                          # 방금 고친 것 → 그 전 판 내용으로 새 판
    assert w["supersedes"] == e["id"] and w["body"] == v["body"]
    st.close()


def test_explicit_new_entity_id(proj_dir):
    st = open_store(proj_dir)
    eid = "eeeeeeee-0000-4000-8000-00000000000e"
    r = st.append_label({"kind": "segment", "body": seg_body(), "entity_id": eid})
    assert r["entity_id"] == eid and r["supersedes"] is None
    with pytest.raises(store.ChainError):
        st.append_label({"kind": "segment", "body": seg_body(), "entity_id": "NOT-A-UUID"})
    with pytest.raises(store.ChainError):
        st.append_label({"kind": "segment", "body": seg_body(), "supersedes": r["id"]})
    st.close()


# ---------------------------------------------------------------------------- 파일 이름·세션 격리

def test_file_name_rule_and_isolation(proj_dir):
    labels = os.path.join(proj_dir, "labels")
    os.makedirs(labels, exist_ok=True)
    # 다른 세션 파일(깨져 있어도 열지 않는다) · 이 세션의 충돌 사본 · 무관한 파일
    with open(os.path.join(labels, "L09-P-20261001-zz99.abcd.jsonl"), "wb") as f:
        f.write(b"{broken\n{also broken\n")
    m4 = session.machine4(MID_A)
    with open(os.path.join(labels, f"L01-P-20261021-ab12.{m4} (1).jsonl"), "wb") as f:
        f.write(b"{broken\n")
    st = open_store(proj_dir)
    assert not st.read_only, st.read_only_reasons
    assert st.conflict_files == [f"L01-P-20261021-ab12.{m4} (1).jsonl"]
    assert any("충돌" in w for w in st.warnings)
    assert os.path.basename(st.path) == f"L01-P-20261021-ab12.{m4}.jsonl"
    assert all(r["session_id"] == "L01-P-20261021-ab12" for r in st.records())
    st.close()


def test_same_session_two_machines_merge(proj_dir):
    a = open_store(proj_dir, ctx(MID_A))
    ra = a.append_label({"kind": "segment", "body": seg_body()})
    a.close()
    # 다른 PC: session_created_at 을 주지 않아도 첫 PC 파일의 메타를 그대로 쓴다
    b = open_store(proj_dir, session.SessionContext("L01", "primary", "L01-P-20261021-ab12",
                                                    session.machine4(MID_B), machine_id=MID_B))
    assert not b.read_only, b.read_only_reasons
    open_a = a.records()[0]["body"]
    open_b = [r for r in b.records() if r["kind"] == "session.open" and r["body"]["machine_id"] == MID_B][0]["body"]
    # 메타는 반복되고 machine 값만 다르다
    assert {k: v for k, v in open_b.items() if k not in ("machine_id", "machine4")} == \
           {k: v for k, v in open_a.items() if k not in ("machine_id", "machine4")}
    assert open_b["machine4"] == session.machine4(MID_B)
    # 다른 PC 에서 만든 대상을 이 PC 에서 고칠 수 있다(같은 세션)
    rb = b.append_label({"kind": "segment", "body": seg_body(0, 40000), "entity_id": ra["entity_id"],
                         "supersedes": ra["id"]})
    assert rb["seq"] == 2                         # seq 는 파일마다
    assert len(b.records()) == 5                  # A: open·segment·close, B: open·segment
    b.close()
    a2 = open_store(proj_dir, ctx(MID_A))
    assert a2.head(ra["entity_id"])["id"] == rb["id"]
    a2.close()


def test_session_kind_and_guide_cannot_change(proj_dir):
    st = open_store(proj_dir)
    st.close()
    with pytest.raises(ValueError):
        open_store(proj_dir, ctx(guide_version="0.2"))
    with pytest.raises(ValueError):
        open_store(proj_dir, ctx(guide_version="9.9", sid="L01-P-20261021-zz11"))   # 프로젝트에 없는 기준서
    assert not FakeLock.held                        # 실패한 열기는 잠금을 놓는다


def test_machine4_mismatch_with_data_root(proj_dir, tmp_path):
    d = tmp_path / "data2"
    session.machine_id(str(d), uuid_factory=lambda: MID_B)
    c = session.SessionContext("L01", "primary", "L01-P-20261021-ab12", session.machine4(MID_A))
    with pytest.raises(ValueError):
        open_store(proj_dir, c, data_root=str(d))
    c2 = session.SessionContext("L01", "primary", "L01-P-20261021-ab12", session.machine4(MID_B))
    st = open_store(proj_dir, c2, data_root=str(d))
    assert st.records()[0]["body"]["machine_id"] == MID_B
    st.close()


# ---------------------------------------------------------------------------- 읽기 전용에서 쓰기 거부

def _corrupt_middle(path):
    lines = open(path, "rb").read().split(b"\n")
    lines[1] = b"{broken"
    with open(path, "wb") as f:
        f.write(b"\n".join(lines))


def test_corrupt_middle_line_read_only_and_refuses_writes(proj_dir):
    st = open_store(proj_dir)
    a = st.append_label({"kind": "segment", "body": seg_body()})
    st.append_label({"kind": "segment", "body": seg_body(60000, 70000)})
    st.close()
    _corrupt_middle(st.path)
    before = open(st.path, "rb").read()
    ro = open_store(proj_dir)
    assert ro.read_only and "2 번째 줄" in ro.read_only_reason
    for call in (lambda: ro.append_label({"kind": "segment", "body": seg_body()}),
                 lambda: ro.retract(a["entity_id"], "undo"),
                 lambda: ro.retract_last(),
                 lambda: ro.append_ops("phase.mark", {"phase": "segments", "scope": "all", "done": True}),
                 lambda: ro.update_project(lambda d: None)):
        with pytest.raises(store.ReadOnlyStoreError):
            call()
    assert open(st.path, "rb").read() == before
    ro.close()
    assert not FakeLock.held


def test_newer_record_version_is_read_only(proj_dir):
    st = open_store(proj_dir)
    st.close()
    rec = json.loads(open(st.path, "rb").read().split(b"\n")[0])
    rec.update(v=2, seq=2, id="22222222-0000-4000-8000-000000000002", kind="phase.mark",
               body={"phase": "segments", "scope": "all", "done": True})
    jsonl.append(st.path, rec)
    ro = open_store(proj_dir)
    assert ro.read_only and "새 기록 형식" in ro.read_only_reason
    ro.close()


def test_schema_invalid_line_on_disk_is_read_only(proj_dir):
    st = open_store(proj_dir)
    st.close()
    rec = json.loads(open(st.path, "rb").read().split(b"\n")[0])
    rec.update(seq=3, id="22222222-0000-4000-8000-000000000002", kind="session.close", body={})  # reason 없음
    jsonl.append(st.path, rec)
    ro = open_store(proj_dir)
    assert ro.read_only and "3번째 기록" in ro.read_only_reason
    ro.close()


def test_seq_gap_is_read_only(proj_dir):
    st = open_store(proj_dir)
    st.close()
    rec = json.loads(open(st.path, "rb").read().split(b"\n")[0])
    rec.update(seq=4, id="22222222-0000-4000-8000-000000000002", kind="phase.mark",
               body={"phase": "segments", "scope": "all", "done": True})       # 1 open · 2 close · (3 없음) · 4
    jsonl.append(st.path, rec)
    ro = open_store(proj_dir)
    assert ro.read_only and "seq" in ro.read_only_reason
    ro.close()


def test_bom_file_reads(proj_dir):
    st = open_store(proj_dir)
    st.append_label({"kind": "segment", "body": seg_body()})
    st.close()
    raw = open(st.path, "rb").read()
    with open(st.path, "wb") as f:
        f.write(b"\xef\xbb\xbf" + raw)
    st2 = open_store(proj_dir)
    assert not st2.read_only and len(st2.records()) == 3
    st2.close()


def test_read_only_project_json(proj_dir):
    pj = os.path.join(proj_dir, "project.json")
    with open(pj, "ab") as f:
        f.write(b"garbage")
    ro = open_store(proj_dir)
    assert ro.read_only and "project.json" in ro.read_only_reason
    assert not FakeLock.held                         # 읽기 전용이면 잠금을 잡지 않는다
    with pytest.raises(store.ReadOnlyStoreError):
        ro.append_label({"kind": "segment", "body": seg_body()})
    ro.close()


def test_write_verify_failure_turns_read_only(proj_dir, monkeypatch):
    st = open_store(proj_dir)
    real = jsonl.append

    def bad_append(path, obj):
        o = copy.deepcopy(obj)
        o["seq"] = 999                                # 디스크에 다른 줄이 쓰인 상황
        real(path, o)
    monkeypatch.setattr(store.jsonl, "append", bad_append)
    with pytest.raises(store.WriteVerifyError):
        st.append_label({"kind": "segment", "body": seg_body()})
    assert st.read_only
    st.close()


# ---------------------------------------------------------------------------- 프로젝트 저장·뷰

def test_update_project_through_store(proj_dir):
    st = open_store(proj_dir)
    new = st.update_project(lambda d: d.__setitem__("notes", "합성 메모"))
    assert new["rev"] == 2 and new["modified_by"].startswith("L01@")
    st.close()


def test_effective_uses_core_resolve(proj_dir):
    pytest.importorskip("app.core.resolve")
    st = open_store(proj_dir)
    a = st.append_label({"kind": "segment", "body": seg_body()})
    b = st.append_label({"kind": "segment", "body": seg_body(0, 1000), "entity_id": a["entity_id"],
                         "supersedes": a["id"]})
    view = st.effective()
    assert view.effective(a["id"])["id"] == b["id"]
    assert len(view.history(a["id"])) == 2
    st.retract(a["entity_id"], "undo")
    assert st.effective().effective(a["id"]) is None      # 철회됨
    st.close()


# ---------------------------------------------------------------------------- 성능 (경고만)

def test_append_latency_target(proj_dir):
    st = open_store(proj_dir)
    times = []
    for i in range(30):
        st.append_label({"kind": "segment", "body": seg_body(i * 1000, i * 1000 + 1000)})
        times.append(st.last_append_ms)
    st.close()
    med, worst = statistics.median(times), max(times)
    print(f"\n라벨 저장(되읽기 포함) 중앙값 {med:.2f}ms, 최악 {worst:.2f}ms, 30건")
    if med > store.SAVE_TARGET_MS:
        warnings.warn(f"라벨 저장 중앙값 {med:.1f}ms > 목표 {store.SAVE_TARGET_MS}ms")
    assert all(t is not None and t >= 0 for t in times)


# ---------------------------------------------------------------------------- 검토 수정: visibility 표 · scope · 최신 project.json

def _adj_body(target_sid="L02-P-20261021-cd34"):
    return {"target_kind": "segment", "decision": "exclude_from_eval", "cause": "other",
            "targets": [{"session_id": target_sid, "entity_id": "00000000-0000-4000-8000-0000000000aa",
                         "version_id": "00000000-0000-4000-8000-0000000000bb"}]}


def test_primary_session_cannot_write_adjudication(proj_dir):
    """visibility: primary 의 writable_label_kinds 에 adjudication 이 없다 → store 가 거부한다(03 12-1)."""
    st = open_store(proj_dir)
    with pytest.raises(store.LabelValidationError):
        st.append_label({"kind": "adjudication", "body": _adj_body()})
    assert not any(r["kind"] == "adjudication" for r in st.records())
    st.close()


def test_adjudication_session_only_writes_adjudication(proj_dir):
    st = open_store(proj_dir, ctx(sid="L01-AD-20261021-ab12", kind="adjudication"))
    with pytest.raises(store.LabelValidationError):
        st.append_label({"kind": "segment", "body": seg_body()})
    rec = st.append_label({"kind": "adjudication", "body": _adj_body()})
    assert rec["kind"] == "adjudication"
    st.retract(rec["entity_id"], "undo")                 # retract 는 표에 있다
    st.close()


def test_store_follows_visibility_table_for_every_kind(proj_dir):
    """store 가 허락하는 라벨 종류 = visibility.files_to_open(kind).writable_label_kinds (거부만 확인)."""
    from app.core import visibility as vis
    for kind, sid in (("primary", "L01-P-20261021-ab12"), ("relabel_same", "L01-RS-20261021-ab13"),
                      ("adjudication", "L01-AD-20261021-ab14"), ("origin_review", "L01-OR-20261021-ab15")):
        st = open_store(proj_dir, ctx(sid=sid, kind=kind))
        allowed = vis.files_to_open(kind).writable_label_kinds
        for lk in store.LABEL_KINDS:
            if lk in allowed:
                continue
            with pytest.raises(store.LabelValidationError) as ei:
                st.append_label({"kind": lk, "body": {}})
            assert "03 12-" in str(ei.value)                  # 스키마가 아니라 세션 종류 규칙으로 거부
        st.close()


def test_relabel_blocks_scope_rejects_outside(proj_dir):
    scope = {"type": "blocks", "ranges": [[0, 100_000], [300_000, 400_000]]}
    st = open_store(proj_dir, ctx(sid="L01-RS-20261021-ab12", kind="relabel_same", scope=scope))
    assert st.session_meta["scope"] == scope
    st.append_label({"kind": "segment", "body": seg_body(0, 60_000)})
    st.append_label({"kind": "segment", "body": seg_body(300_000, 400_000)})     # 끝 = 범위 끝: 안
    for a, b in ((500_000, 600_000), (90_000, 110_000), (100_000, 200_000)):     # 밖 · 걸침 · 범위 끝에서 시작
        with pytest.raises(store.LabelValidationError):
            st.append_label({"kind": "segment", "body": seg_body(a, b)})
    assert sum(1 for r in st.records() if r["kind"] == "segment") == 2
    st.close()


def test_label_meta_follows_project_json_written_elsewhere(proj_dir):
    """다른 Project 객체(작업대 등)가 project.json 을 고쳐도 다음 라벨은 새 media_rev 를 갖는다(D2)."""
    st = open_store(proj_dir)
    a = st.append_label({"kind": "segment", "body": seg_body()})
    other = project.open(proj_dir)

    def mutate(d):
        d["media"]["media_rev"] = int(d["media"].get("media_rev", 0)) + 1
    other.update(mutate, by="L01")
    b = st.append_label({"kind": "segment", "body": seg_body(60_000, 120_000)})
    assert b["media_rev"] == a["media_rev"] + 1
    st.update_project(lambda d: d.__setitem__("notes", "합성"))                    # rev 도 따라와 Conflict 아님
    st.close()


def test_effective_without_copy_is_cached_and_not_mutated(proj_dir):
    st = open_store(proj_dir)
    a = st.append_label({"kind": "segment", "body": seg_body()})
    v1 = st.effective(copy_records=False)
    assert st.effective(copy_records=False) is v1                                  # 기록이 그대로면 다시 계산하지 않음
    snap = copy.deepcopy(st.records())
    st.append_label({"kind": "segment", "body": seg_body(0, 1000), "entity_id": a["entity_id"], "supersedes": a["id"]})
    v2 = st.effective(copy_records=False)
    assert v2 is not v1 and v2.effective(a["id"])["body"]["end_ms"] == 1000
    assert st.records()[:len(snap)] == snap                                        # resolve 가 내부 기록을 고치지 않음
    st.close()
