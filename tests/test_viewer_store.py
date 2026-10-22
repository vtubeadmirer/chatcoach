"""시청자 훈련 기록 시험 (app/viewer_store.py · schemas/viewer-event.schema.json, 08 D-20 4·5, 21 6-3).

합성 글만 쓴다(이모티콘 코드도 합성 '{:zz_test_01:}'). 프로젝트는 tmp_path 에 새로 만든다 — 실제 프로젝트·시범 폴더는
열지 않는다. 시범 판단은 tmp_path 안의 빈 pilot 폴더로 한다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import backup, jsonl
from app import project as project_mod
from app import viewer_store as vs

PID = "00000000-0000-4000-8000-000000000001"
TXT1 = "합성연습 주제 질문 있어요? {:zz_test_01:}"
TXT2 = "합성연습 둘째 줄"
KST = timezone(timedelta(hours=9))


def _clock():
    return datetime(2026, 10, 22, 12, 0, 0, tzinfo=KST)


def _uuids():
    n = [0]

    def f():
        n[0] += 1
        return f"00000000-0000-4000-8000-{n[0]:012d}"
    return f


def _sc(t="good", e="ok", r="weak", **extra):
    return {"topic": {"level": t, "code": "topic_overlap", "reason": "합성까닭 겹친 낱말", **extra},
            "expand": {"level": e, "code": "question_open"},
            "repeat": {"level": r, "code": "same_as_previous"}}


def _new(tmp_path, name="P-synth"):
    root = tmp_path / name
    root.mkdir()
    log = vs.ViewerLog.create(str(root), "L01", clock=_clock, rand4=lambda: "ab12", uuid_factory=_uuids(),
                              app_version="0.0.1", pilot_dir=str(tmp_path / "pilot"))
    return root, log


def test_roundtrip_schema_and_layout(tmp_path):
    root, log = _new(tmp_path)
    assert log.session_id == "L01-VW-20261022-ab12"
    assert os.path.dirname(log.path) == os.path.join(str(root), "viewer")
    log.start(project_id=PID, range_ms=(5_000, 25_000),
              scenario={"scenario_id": "SC-20261022-abcd", "scenario_sha256": "b" * 64, "virtual_lines": 2})
    log.write(TXT1, 7_000, _sc(), scorer="viewer_score/0.1")
    log.write(TXT2, 9_500, None, score_error="scorer_missing")
    log.end("segment_end", examples=2)
    recs, info = vs.read_valid(log.path)
    assert info == {"lines": 4, "invalid": 0, "corrupt": 0, "truncated_tail": False}
    assert [r["kind"] for r in recs] == ["viewer.start", "viewer.write", "viewer.write", "viewer.end"]
    assert [r["seq"] for r in recs] == [1, 2, 3, 4]
    assert recs[0]["body"]["audio"] == "on" and recs[0]["body"]["virtual_lines"] == 2
    w1, w2 = recs[1]["body"], recs[2]["body"]
    assert w1["text"] == TXT1 and w1["chars"] == len(TXT1) and w1["vod_ms"] == 7_000
    # 까닭 글(reason)은 버리고 코드만 — 글의 낱말이 기록에 새지 않게
    assert w1["scores"] == {"topic": {"level": "good", "code": "topic_overlap"},
                            "expand": {"level": "ok", "code": "question_open"},
                            "repeat": {"level": "weak", "code": "same_as_previous"}}
    assert w2["scores"] is None and w2["score_error"] == "scorer_missing"
    end = recs[3]["body"]
    assert end == {"reason": "segment_end", "writes": 2, "examples": 2,
                   "levels": {"topic": {"good": 1, "ok": 0, "weak": 0}, "expand": {"good": 0, "ok": 1, "weak": 0},
                              "repeat": {"good": 0, "ok": 0, "weak": 1}}}
    raw = open(log.path, "rb").read()
    assert "합성까닭".encode() not in raw
    assert raw.count(b"\n") == 4 and not raw.startswith(b"\xef\xbb\xbf")
    # 글은 write 줄에만
    for r in recs:
        if r["kind"] != "viewer.write":
            assert TXT1 not in json.dumps(r, ensure_ascii=False)
    assert vs.list_sessions(str(root)) == [log.session_id]
    assert vs.summarize(recs)["writes"] == 2 and vs.summarize(recs)["ended"] is True


def test_rejections_leave_file_unchanged(tmp_path):
    root, log = _new(tmp_path)
    log.start(project_id=PID, range_ms=(0, 10_000))
    before = open(log.path, "rb").read()
    for bad in (lambda: log.write("   ", 10, None),
                lambda: log.write("가" * (vs.MAX_TEXT_CHARS + 1), 10, None),
                lambda: log.write(TXT2, -1, None),
                lambda: log.write(TXT2, True, None),
                lambda: log.end("nope")):
        with pytest.raises(vs.ViewerStoreError) as ei:
            bad()
        assert TXT2 not in str(ei.value)
    with pytest.raises(vs.ViewerRecordError):          # 스키마: project_id 형식
        log._write("viewer.start", {"mode": "viewer", "project_id": "x", "source": "replay", "audio": "on",
                                    "range": {"start_ms": 0, "end_ms": 1}})
    assert open(log.path, "rb").read() == before
    # 줄바꿈·제어 문자는 공백으로
    rec = log.write("합성\n연습\t줄", 10, None)
    assert rec["body"]["text"] == "합성 연습 줄"
    log.end()
    with pytest.raises(vs.ViewerStoreError):
        log.write(TXT2, 20, None)


def test_schema_refuses_unknown_fields_and_bad_levels(tmp_path):
    _root, log = _new(tmp_path)
    rec = log.start(project_id=PID, range_ms=(0, 1_000))
    w = log.write(TXT2, 100, _sc())
    bad = json.loads(json.dumps(w))
    bad["body"]["nickname_masked"] = "시**"              # 다른 사람 칸은 없다(값은 합성 가림 꼴)
    assert vs.check_record(bad)
    bad = json.loads(json.dumps(w))
    bad["body"]["scores"]["topic"]["level"] = "great"
    assert vs.check_record(bad)
    bad = json.loads(json.dumps(w))
    bad["body"]["scores"]["topic"]["reason"] = "합성까닭"
    assert vs.check_record(bad)
    bad = json.loads(json.dumps(rec))
    bad["body"]["audio"] = "muted"
    assert vs.check_record(bad)
    # 문제 글에 본문이 나오지 않는다
    bad = json.loads(json.dumps(w))
    bad["body"]["text"] = TXT1 * 20
    probs = vs.check_record(bad)
    assert probs and all(TXT1 not in p for p in probs)
    # 모르는 단계·코드는 고르거나 버린다
    rec2 = log.write(TXT2, 200, {"topic": {"level": "good", "code": "Bad Code!"}, "expand": {"level": "ok"},
                                 "repeat": {"level": "weak", "code": "x" * 50}})
    assert rec2["body"]["scores"]["topic"]["code"] == "other" and rec2["body"]["scores"]["repeat"]["code"] == "other"
    rec3 = log.write(TXT2, 300, {"topic": {"level": "great"}})
    assert rec3["body"]["scores"] is None and rec3["body"]["score_error"] == "scorer_failed"


def test_reopen_continues_seq(tmp_path):
    root, log = _new(tmp_path)
    log.start(project_id=PID, range_ms=(0, 1_000))
    log.write(TXT2, 10, _sc())
    again = vs.ViewerLog(str(root), log.session_id, "L01", clock=_clock, uuid_factory=lambda: "00000000-0000-4000-8000-0000000000ff",
                         app_version="0.0.1")
    assert again.writes == 1 and again.levels()["topic"]["good"] == 1
    r = again.end("closed")
    assert r["seq"] == 3 and r["body"]["writes"] == 1
    with pytest.raises(vs.ViewerStoreError):
        vs.ViewerLog(str(root), log.session_id, "L02")
    with pytest.raises(vs.ViewerStoreError):
        vs.session_path(str(root), "L01-TR-20261022-ab12")


def test_create_retries_name_and_ids(tmp_path):
    root = tmp_path / "P-synth"
    root.mkdir()
    seq = iter(["ab12", "ab12", "cd34"])
    a = vs.ViewerLog.create(str(root), "L01", clock=_clock, rand4=lambda: "ab12", pilot_dir=str(tmp_path / "pilot"))
    b = vs.ViewerLog.create(str(root), "L01", clock=_clock, rand4=lambda: next(seq), pilot_dir=str(tmp_path / "pilot"))
    assert a.session_id.endswith("ab12") and b.session_id.endswith("cd34")
    with pytest.raises(vs.ViewerStoreError):
        vs.ViewerLog.create(str(root), "l01", pilot_dir=str(tmp_path / "pilot"))


def test_pilot_project_blocked_before_any_write(tmp_path):
    """시범 프로젝트(이름 pilot-*, 합성 빈 폴더)는 잠기기 전 새 세션을 막고 viewer\\ 폴더도 만들지 않는다(D-20 5)."""
    root = tmp_path / "pilot-synth"
    root.mkdir()
    with pytest.raises(vs.ViewerBlockedError):
        vs.ViewerLog.create(str(root), "L01", pilot_dir=str(tmp_path / "pilot"))
    assert not (root / "viewer").exists()


def test_viewer_dir_is_protected_and_not_backed_up(tmp_path):
    """viewer/ 는 보호 구역 기본값(D-20 4). 보호 구역 없는 백업 목록에 들어가지 않는다. 내보내기는 labels\\ 만 읽는다."""
    assert "viewer/" in project_mod.DEFAULT_PROTECTED_ZONE
    assert tuple(project_mod.DEFAULT_PROTECTED_ZONE) == backup.DEFAULT_PROTECTED_ZONE
    zone = backup.protected_patterns(None)
    assert backup.in_protected_zone("viewer/L01-VW-20261022-ab12.jsonl", zone)
    assert backup.in_protected_zone("VIEWER\\x.jsonl", zone)
    assert not backup.in_protected_zone("viewers/x.jsonl", zone)
    root, log = _new(tmp_path)
    log.start(project_id=PID, range_ms=(0, 1_000))
    log.write(TXT1, 10, _sc())
    (root / "labels").mkdir()
    (root / "labels" / "x.jsonl").write_bytes(b"")
    out = backup.select_files(str(root), include_chat=True, include_protected=False, dirs=(*backup.CORE_DIRS, "viewer"))
    assert all(not r.lower().startswith("viewer/") for r in out) and "labels/x.jsonl" in out
    inc = backup.select_files(str(root), include_chat=True, include_protected=True)
    assert any(r.startswith("viewer/") for r in inc)


def test_append_is_one_line_per_record_after_truncated_tail(tmp_path):
    """덧붙이기는 줄 하나 = os.write 한 번(app.jsonl.append). 앞 줄이 잘려 있으면 줄바꿈부터 넣어 새 줄은 온전하다."""
    root, log = _new(tmp_path)
    log.start(project_id=PID, range_ms=(0, 1_000))
    with open(log.path, "ab") as f:
        f.write(b'{"v": 1, "kind": "viewer.wr')            # 잘린 꼬리 (합성)
    jsonl_res = jsonl.read(log.path)
    assert jsonl_res.truncated_tail
    log2_rec = log.write(TXT2, 50, _sc())
    res = jsonl.read(log.path)
    assert res.records[-1]["id"] == log2_rec["id"] and len(res.corrupt_lines) == 1


def test_level_counts_and_empty():
    assert vs.empty_levels()["repeat"] == {"good": 0, "ok": 0, "weak": 0}
    c = vs.level_counts([{"topic": {"level": "good"}, "expand": {"level": "weak"}, "repeat": {"level": "x"}}, None, 3])
    assert c["topic"]["good"] == 1 and c["expand"]["weak"] == 1 and sum(c["repeat"].values()) == 0
