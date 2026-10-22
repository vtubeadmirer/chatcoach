"""app/core/training.py · app/training_store.py · schemas/training-event.schema.json (18 6-2·6-3, 08 D-18 6~8).

합성 데이터만 쓴다. chat_id 는 지어낸 값이다. 기대값은 손으로 센 것이다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import training_store as ts
from app.core import training as tr

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 22, 21, 0, 0, tzinfo=KST)
PID = "12345678-0000-4000-8000-000000000001"
SHA = "a" * 64


def cid(n):
    return "c" + f"{n:020x}"


def ref(n, chat_ms, react_ms, grade="direct"):
    return tr.RefReaction(cid(n), chat_ms, react_ms, grade)


# ----------------------------------------------------------------------------- 고르기
def test_toggle_pick_and_replay():
    p0 = {}
    p1, k1 = tr.toggle_pick(p0, cid(1), 1000)
    assert k1 == tr.K_PICK and p1 == {cid(1): 1000} and p0 == {}
    p2, k2 = tr.toggle_pick(p1, cid(1), 1500)          # 같은 채팅 다시 누름 = 취소
    assert k2 == tr.K_UNPICK and p2 == {}
    p3, _ = tr.toggle_pick(p2, cid(1), 2000)
    assert p3 == {cid(1): 2000}                         # 다시 고르면 새 시각
    with pytest.raises(tr.TrainingError):
        tr.toggle_pick({}, "합성", 0)
    with pytest.raises(tr.TrainingError):
        tr.toggle_pick({}, cid(1), -1)
    with pytest.raises(tr.TrainingError):
        tr.toggle_pick({}, cid(1), True)


def test_picks_from_events_follows_seq():
    evs = [
        {"seq": 3, "kind": tr.K_UNPICK, "body": {"chat_id": cid(1), "vod_ms": 30}},
        {"seq": 1, "kind": tr.K_PICK, "body": {"chat_id": cid(1), "picked_vod_ms": 10}},
        {"seq": 2, "kind": tr.K_PICK, "body": {"chat_id": cid(2), "picked_vod_ms": 20}},
        {"seq": 4, "kind": tr.K_PICK, "body": {"chat_id": "합성", "picked_vod_ms": 40}},   # 모양이 틀림 → 건너뜀
        {"seq": 5, "kind": tr.K_COMPARE, "body": {}},
    ]
    assert tr.picks_from_events(evs) == {cid(2): 20}


def test_same_chat_is_chat_id_equality():
    assert tr.same_chat(cid(1), cid(1))
    assert not tr.same_chat(cid(1), cid(2))
    assert not tr.same_chat(cid(1), cid(1) + "_2")      # 같은 base 의 두 번째 메시지는 다른 채팅
    assert not tr.same_chat(None, None)


# ----------------------------------------------------------------------------- 견주기
def test_compare_counts_rate_median():
    refs = [ref(1, 10_000, 12_000), ref(2, 20_000, 23_000), ref(3, 30_000, 31_000), ref(4, 40_000, None)]
    picks = {cid(1): 11_000, cid(2): 25_000, cid(4): 41_000, cid(9): 50_000}
    c = tr.compare(picks, refs, chat_times={cid(9): 49_000})
    assert (c.same, c.mine_only, c.orig_only, c.reference_count) == (3, 1, 1, 4)
    assert c.hit_rate == 0.75
    # 차: 1 → 11000−12000 = −1000, 2 → 25000−23000 = 2000, 4 → 반응 시각 없음(뺌). 중앙값 (−1000+2000)//2 = 500
    assert c.diff_count == 2 and c.median_diff_ms == 500
    assert [r.status for r in c.rows] == [tr.SAME, tr.SAME, tr.ORIG_ONLY, tr.SAME, tr.MINE_ONLY]
    mine = c.by_status(tr.MINE_ONLY)[0]
    assert mine.chat_id == cid(9) and mine.chat_vod_ms == 49_000 and mine.reaction_vod_ms is None
    assert c.summary() == {"same": 3, "mine_only": 1, "orig_only": 1, "reference_count": 4, "hit_rate": 0.75,
                           "median_diff_ms": 500, "diff_count": 2}


def test_compare_empty_and_odd_median():
    c = tr.compare({}, [])
    assert c.hit_rate is None and c.median_diff_ms is None and c.rows == []
    c = tr.compare({cid(1): 5}, [])
    assert c.mine_only == 1 and c.hit_rate is None
    refs = [ref(1, 0, 100), ref(2, 0, 100), ref(3, 0, 100)]
    c = tr.compare({cid(1): 50, cid(2): 400, cid(3): 130}, refs)
    assert c.median_diff_ms == 30 and c.hit_rate == 1.0


def test_compare_range_and_until():
    refs = [ref(1, 5_000, 6_000), ref(2, 15_000, 16_000), ref(3, 25_000, 26_000), ref(4, None, 1)]
    c = tr.compare({}, refs, range_ms=(10_000, 30_000))
    assert c.reference_count == 2 and c.orig_only == 2 and c.excluded_refs == 2
    c = tr.compare({cid(2): 16_500}, refs, range_ms=(10_000, 30_000), until_ms=20_000)
    assert c.reference_count == 1 and c.same == 1 and c.orig_only == 0 and c.until_ms == 20_000
    with pytest.raises(tr.TrainingError):
        tr.compare({}, refs, range_ms=(5, 5))


def test_duplicate_reference_keeps_earliest_reaction():
    refs = [ref(1, 1000, 3000), ref(1, 1000, 2000), ref(2, 500, None)]
    norm, dup = tr.normalize_reference(refs)
    assert dup == 1 and [r.chat_id for r in norm] == [cid(2), cid(1)] and norm[1].reaction_vod_ms == 2000
    c = tr.compare({cid(1): 2500}, refs)
    assert c.duplicate_refs == 1 and c.reference_count == 2 and c.median_diff_ms == 500


def test_compare_accepts_dict_shapes():
    picks = [{"chat_id": cid(1), "picked_vod_ms": 900}, {"chat_id": cid(1), "picked_vod_ms": 1100}]
    refs = [{"chat_id": cid(1), "reaction_vod_ms": 1000}, {"chat_id": cid(2), "chat_vod_ms": 5, "reaction_vod_ms": None}]
    c = tr.compare(picks, refs)
    assert (c.same, c.orig_only, c.median_diff_ms) == (1, 1, 100)
    with pytest.raises(tr.TrainingError):
        tr.compare([("합성", 1)], [])
    with pytest.raises(tr.TrainingError):
        tr.compare({}, [{"chat_id": "합성"}])


def test_reference_sha256_order_independent():
    a = [ref(1, 10, 20), ref(2, 30, 40)]
    b = list(reversed(a))
    assert tr.reference_sha256(tr.REF_FINAL_SET, a) == tr.reference_sha256(tr.REF_FINAL_SET, b)
    assert tr.reference_sha256(tr.REF_FINAL_SET, a) != tr.reference_sha256(tr.REF_MANUAL_CSV, a)
    assert tr.reference_sha256(tr.REF_FINAL_SET, a) != tr.reference_sha256(tr.REF_FINAL_SET, [ref(1, 10, 21), a[1]])
    with pytest.raises(tr.TrainingError):
        tr.reference_sha256("engine", a)


def test_next_orig_only_and_listen_start():
    refs = [ref(1, 10_000, 12_000), ref(2, 20_000, 21_000), ref(3, 30_000, 33_000)]
    c = tr.compare({cid(2): 20_500}, refs)
    st = [r.status for r in c.rows]
    assert st == [tr.ORIG_ONLY, tr.SAME, tr.ORIG_ONLY]
    assert tr.next_orig_only(c.rows, None) == 0
    assert tr.next_orig_only(c.rows, 0) == 2
    assert tr.next_orig_only(c.rows, 2) is None
    assert tr.next_orig_only(c.rows, 2, -1) == 0
    assert tr.next_orig_only(c.rows, None, -1) == 2
    assert tr.listen_start_ms(c.rows[0]) == 5_000
    assert tr.listen_start_ms(c.rows[1], lead_ms=1_000) == 19_000
    assert tr.listen_start_ms(tr.CompareRow(tr.MINE_ONLY, cid(5), None, 2_000, None, None)) == 0
    assert tr.listen_start_ms(tr.CompareRow(tr.MINE_ONLY, cid(5), None, None, None, None)) == 0


def test_core_is_pure():
    src = open(tr.__file__, encoding="utf-8").read()
    for bad in ("import os", "open(", "datetime", "random", "PySide", "time."):
        assert bad not in src


# ----------------------------------------------------------------------------- 기록 파일
class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        self.t += timedelta(milliseconds=250)
        return self.t


def _uuids():
    n = [0]

    def make():
        n[0] += 1
        return f"{n[0]:08x}-0000-4000-8000-{n[0]:012x}"
    return make


def _project(tmp_path, name="p777"):
    root = tmp_path / "projects" / name
    (root / "labels").mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project_id": PID}), encoding="utf-8")
    return str(root)


def _log(root, **kw):
    r = iter(["ab12", "ab12", "cd34", "ef56"])
    return ts.TrainingLog.create(root, "L01", clock=Clock(), rand4=lambda: next(r), uuid_factory=_uuids(),
                                 app_version="0.0.1", **kw)


def test_training_log_roundtrip(tmp_path):
    root = _project(tmp_path)
    log = _log(root)
    assert log.session_id == "L01-TR-20261022-ab12"
    assert os.path.dirname(log.path) == os.path.join(root, "training")
    log.start(project_id=PID, range_ms=(0, 60_000),
              reference={"source": "final_set", "sha256": SHA, "count": 3, "file": None})
    log.toggle(cid(1), 10_000)
    log.toggle(cid(2), 20_000)
    log.toggle(cid(1), 21_000)                           # 취소
    assert log.picks == {cid(2): 20_000}
    c = tr.compare(log.picks, [ref(2, 19_000, 19_500), ref(3, 30_000, 31_000)], range_ms=(0, 60_000))
    log.compare(c, SHA, (0, 60_000))
    log.end("compare")
    recs, info = ts.read_valid(log.path)
    assert info == {"lines": 6, "invalid": 0, "corrupt": 0, "truncated_tail": False}
    assert [r["kind"] for r in recs] == ["training.start", "training.pick", "training.pick", "training.unpick",
                                         "training.compare", "training.end"]
    assert [r["seq"] for r in recs] == [1, 2, 3, 4, 5, 6]
    cmp_ = recs[4]["body"]
    assert (cmp_["same"], cmp_["orig_only"], cmp_["hit_rate"], cmp_["median_diff_ms"]) == (1, 1, 0.5, 500)
    assert recs[5]["body"] == {"reason": "compare", "picks": 1}
    assert recs[1]["body"]["source"] == "replay" and recs[1]["body"]["wall_at"] == recs[1]["created_at"]
    # 다시 열면 seq·고른 상태를 이어 받는다
    again = ts.TrainingLog(root, log.session_id, "L01", clock=Clock(), uuid_factory=lambda: "ffffffff-0000-4000-8000-000000000fff",
                           app_version="0.0.1")
    assert again.picks == {cid(2): 20_000}
    rec = again.toggle(cid(3), 31_000)
    assert rec["seq"] == 7
    assert ts.list_sessions(root) == [log.session_id]
    assert [r["seq"] for r in ts.load_records(root)] == list(range(1, 8))


def test_new_session_name_retries_on_collision(tmp_path):
    root = _project(tmp_path)
    a = _log(root)
    r = iter(["ab12", "cd34"])
    b = ts.TrainingLog.create(root, "L01", clock=Clock(), rand4=lambda: next(r), uuid_factory=_uuids(),
                              app_version="0.0.1")
    assert a.session_id != b.session_id and b.session_id.endswith("-cd34")


def test_schema_rejects_text_and_nickname(tmp_path):
    root = _project(tmp_path)
    log = _log(root)
    with pytest.raises(ts.TrainingRecordError) as ei:
        log._write(tr.K_PICK, {"chat_id": cid(1), "picked_vod_ms": 1, "wall_at": "2026-10-22T21:00:00+09:00",
                               "source": "replay", "text": "합성 본문"})
    assert "합성 본문" not in str(ei.value)
    with pytest.raises(ts.TrainingRecordError):
        log._write(tr.K_PICK, {"chat_id": cid(1), "picked_vod_ms": 1, "wall_at": "2026-10-22T21:00:00+09:00",
                               "source": "generated"})                     # F 첫 판은 replay 만
    with pytest.raises(ts.TrainingRecordError):
        log._write(tr.K_END, {"reason": "closed", "picks": 0, "nickname": "시청자1"})   # 합성 자리표시
    with pytest.raises(ts.TrainingRecordError):
        log.start(project_id=PID, range_ms=(0, 1), reference={"source": "engine", "sha256": SHA, "count": 0})
    assert not os.path.getsize(log.path)                                    # 거부된 줄은 쓰지 않았다
    assert log.picks == {}


def test_schema_file_valid_and_kinds_match():
    with open(ts.schema_path(), encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["properties"]["kind"]["enum"] == list(tr.KINDS)
    assert doc["$id"].endswith("/schemas/training-event.schema.json")
    assert set(doc["$defs"]["compareBody"]["required"]) >= set(tr.Comparison([], 0, 0, 0, 0, None, None, 0).summary())
    good = {"v": 1, "id": "00000001-0000-4000-8000-000000000001", "seq": 1, "kind": "training.end",
            "session_id": "L01-TR-20261022-ab12", "labeler_id": "L01", "created_at": "2026-10-22T21:00:00.000+09:00",
            "app_version": "0.0.1", "body": {"reason": "closed", "picks": 0}}
    assert ts.check_record(good) == []
    assert ts.check_record({**good, "session_id": "L01-P-20261022-ab12"})        # 라벨 세션 ID 는 거부
    assert ts.check_record({**good, "kind": "reaction"})
    assert ts.check_record({**good, "extra": 1})


def test_training_dir_separate_from_labels(tmp_path):
    root = _project(tmp_path)
    _log(root)
    assert os.listdir(os.path.join(root, "labels")) == []
    with pytest.raises(ts.TrainingStoreError):
        ts.session_path(root, "../labels/L01-P-20261022-ab12")


def test_truncated_tail_then_append(tmp_path):
    root = _project(tmp_path)
    log = _log(root)
    log.toggle(cid(1), 5)
    with open(log.path, "ab") as f:
        f.write(b'{"v":1,"id":"trunc')                                     # 잘린 꼬리
    again = ts.TrainingLog(root, log.session_id, "L01", clock=Clock(),
                           uuid_factory=lambda: "eeeeeeee-0000-4000-8000-000000000eee", app_version="0.0.1")
    again.toggle(cid(2), 6)
    recs, info = ts.read_valid(log.path)
    assert info["corrupt"] == 1 and len(recs) == 2                          # 잘린 줄은 격리, 새 줄은 따로 읽힘


def test_pilot_project_refuses_new_session(tmp_path):
    root = _project(tmp_path, "pilot-777")
    with pytest.raises(ts.TrainingBlockedError):
        _log(root)
    assert not os.path.exists(os.path.join(root, "training"))


# ----------------------------------------------------------------------------- 가상 채팅 (H-4, 20 5장)
V_TEXT = "합성가상본문"          # 합성 가상 채팅 본문
REAL_NICK = "합성실제닉***"     # 실제 채팅의 가림 닉네임(합성) — 가상 줄 이름에 나오면 안 된다


def _real(n, t):
    return {"chat_id": cid(n), "t_ms": t, "viewer_id": "vw_aaaaaaaaaaaa", "nickname_masked": REAL_NICK,
            "kind": "text", "role": "viewer", "text": f"합성실제 {n}"}


def test_split_mixed_dicts_pseudonym_and_ids():
    real = [_real(1, 1000), _real(2, 3000)]
    mixed = [real[0],
             {"source": "retrieved", "line_id": "s0001", "show_vod_ms": 2000, "text": V_TEXT,
              "display_mask": "시**", "nickname_masked": REAL_NICK, "viewer_id": "vw_bbbbbbbbbbbb"},
             real[1],
             {"source": "generated", "line_id": "s0002", "show_vod_ms": 4000, "text": V_TEXT + " {:zz_test_01:}"},
             {"source": "generated", "line_id": "s0002", "show_vod_ms": 4100, "text": "겹침"},     # 같은 line_id
             {"source": "generated", "line_id": "s0003", "text": "시각 없음"}]                    # 시각 없음
    rows, virt = tr.split_mixed(mixed, real=real)
    assert rows[0] is real[0] and rows[2] is real[1]                  # 실제 줄은 같은 dict (고치지 않음)
    assert set(virt) == {"v:s0001", "v:s0002"} and len(rows) == 4
    r1, r3 = rows[1], rows[3]
    assert r1["chat_id"] == "v:s0001" and r1["nickname_masked"] == "시**" and r1["t_ms"] == 2000
    assert r3["nickname_masked"] == tr.VIRTUAL_PSEUDONYM.format(2)    # 가림 이름이 없으면 가명 '시청자2'
    assert REAL_NICK not in repr([r1, r3]) and "vw_" not in repr([r1, r3])   # 원 닉네임·viewer_id 를 버린다
    assert virt["v:s0001"] == tr.VirtualLine("v:s0001", "s0001", "retrieved", 2000)
    assert tr.is_virtual_id("v:s0001") and not tr.is_virtual_id(cid(1))
    assert not tr.CHAT_ID_RE.match("v:s0001")                          # 실제 chat_id 와 섞이지 않는다
    # 가상 줄만 돌려주는 mix: 실제 줄을 앞에 붙인다
    rows2, virt2 = tr.split_mixed([mixed[1]], real=real)
    assert rows2[:2] == real and len(virt2) == 1
    with pytest.raises(tr.TrainingError):
        tr.virtual_id("")


def test_split_mixed_from_scenario_merge():
    """core.scenario.merge_for_display 결과(MergeResult·DisplayRow)도 받는다 — 합성 시나리오."""
    scn = pytest.importorskip("app.core.scenario")
    if not hasattr(scn, "merge_for_display"):
        pytest.skip("core.scenario.merge_for_display 없음")
    real = [_real(1, 1000), _real(2, 3000)]
    src = {"chat_id": cid(9), "t_ms": 90_000, "kind": "text", "nickname_masked": REAL_NICK, "text": V_TEXT}
    recs = [{"kind": "scenario.header", "scenario_id": "SC-20261022-abcd", "range": {"vod_from_ms": 0, "vod_to_ms": 10_000}},
            {"kind": "scenario.chat", "line_id": "s0001", "source": "retrieved", "show_vod_ms": 2000,
             "from_project_id": "p0", "from_chat_id": cid(9), "from_t_ms": 90_000, "display_mask": "하**"},
            {"kind": "scenario.chat", "line_id": "s0002", "source": "generated", "show_vod_ms": 2500,
             "gen_id": "g0001", "text": V_TEXT, "display_mask": "두*"},
            {"kind": "scenario.end", "lines": 2, "lines_sha256": "0" * 64}]
    res = scn.merge_for_display(real, recs, resolve=lambda p, c: src if c == cid(9) else None, clip_to_range=False)
    rows, virt = tr.split_mixed(res, real=real)
    assert [r["chat_id"] for r in rows] == [cid(1), "v:s0001", "v:s0002", cid(2)]
    assert rows[0] is real[0]
    assert {v.source for v in virt.values()} == {"retrieved", "generated"}
    assert REAL_NICK not in repr(rows[1:3])


def test_compare_virtual_separate_from_score():
    lines = {"v:s0001": tr.VirtualLine("v:s0001", "s0001", "retrieved", 2000),
             "v:s0002": tr.VirtualLine("v:s0002", "s0002", "generated", 6000),
             "v:s0003": tr.VirtualLine("v:s0003", "s0003", "generated", 12_000)}   # 구간 밖
    vs = tr.compare_virtual({"v:s0002": 6500, "v:zzz": 1}, lines, range_ms=(0, 10_000))
    assert vs.shown == 2 and vs.picked == 1
    assert vs.by_source == {"retrieved": {"shown": 1, "picked": 0}, "generated": {"shown": 1, "picked": 1}}
    assert vs.picks == [{"vid": "v:s0002", "line_id": "s0002", "source": "generated", "show_vod_ms": 6000,
                         "picked_vod_ms": 6500}]
    assert vs.numbers() == {"virtual_shown": 2, "virtual_picked": 1}
    assert tr.compare_virtual({}, lines, range_ms=(0, 10_000), until_ms=3000).shown == 1    # 이른 견주기
    # 점수(compare)는 실제 고르기만 — 가상과 상관없이 같은 숫자, summary 칸도 그대로(스키마 additionalProperties)
    c = tr.compare({cid(1): 1500}, [ref(1, 1000, 1400)], range_ms=(0, 10_000))
    assert set(c.summary()) == {"same", "mine_only", "orig_only", "reference_count", "hit_rate", "median_diff_ms",
                                "diff_count"}
    with pytest.raises(tr.TrainingError):
        tr.compare_virtual({}, [object()])
