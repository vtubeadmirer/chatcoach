"""D-10 6: '들었다' 인정 비율(heard_min_coverage)을 기준서 시작값에서 읽고, 없으면 코드 기본값(0.9)을 쓴다.

- core.rules.heard_min_coverage: 값 읽기·범위 검사·기본값
- schemas/guide-config.schema.json: recheck.heard_min_coverage 는 선택 항목(0.5~1.0). v0.1 시작값 파일은 고치지 않음
- 내보내기: 라벨 기준서 판(project.json guides[].thresholds)의 값으로 recheck_incomplete·audio_not_heard 를 센다.
  ExportOptions.heard_min_coverage 를 주면 그 값이 이긴다.
편집기·review_player 쪽은 tests/test_reaction_editor.py · tests/test_review_player.py.
합성 데이터만 쓴다.
"""
import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import export, jsonl, project, session, store
from app.core import rules
from app.core.validate import canonical_json, check

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "assets", "guide-config.v0.1.json")
SCHEMA = os.path.join(ROOT, "schemas", "guide-config.schema.json")
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 6, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-555555555555"
MID = "aaaaaaaa-0000-4000-8000-000000000001"
SID = "L01-P-20261021-ab12"
C1 = "c" + "1" * 20
ANNOTATION_KEYS = {"$schema", "$id", "title", "description", "format"}
V0_1_THRESHOLDS_SHA256 = "e8462bfc86dd1ba04619e0eefff7682e72c899fa0f8b6fdf70142a0b5b4947b2"


def _config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def _strip(node):
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k in ANNOTATION_KEYS:
            continue
        if k in ("properties", "$defs"):
            out[k] = {n: _strip(s) for n, s in v.items()}
        elif k in ("items", "additionalProperties"):
            out[k] = _strip(v)
        elif k == "oneOf":
            out[k] = [_strip(s) for s in v]
        else:
            out[k] = v
    return out


def _validate(obj) -> list[str]:
    with open(SCHEMA, encoding="utf-8") as f:
        spec = _strip(json.load(f))
    return check(obj, spec, spec)


# ------------------------------------------------------------------------------------------ core.rules

def test_default_and_reading():
    assert rules.HEARD_MIN_COVERAGE == 0.9
    assert rules.heard_min_coverage(None) == 0.9
    assert rules.heard_min_coverage({}) == 0.9
    assert rules.heard_min_coverage(_config()["values"]) == 0.9          # v0.1 판에는 없다
    assert rules.heard_min_coverage({"recheck": {"heard_min_coverage": 0.95}}) == 0.95
    assert rules.heard_min_coverage({"recheck": {"heard_min_coverage": 1}}) == 1.0
    assert rules.heard_min_coverage({"recheck": {"heard_min_coverage": 0.5}}) == 0.5
    for bad in (0.49, 1.01, True, "0.95", None, float("nan")):
        assert rules.heard_min_coverage({"recheck": {"heard_min_coverage": bad}}) == 0.9, bad
    assert rules.heard_min_coverage({"recheck": "x"}) == 0.9


def test_same_value_drives_heard_count():
    checks = [{"audio_heard": True, "coverage_ratio": 0.92}, {"audio_heard": True, "coverage_ratio": 0.97}]
    v = {"recheck": {"heard_min_coverage": 0.95}}
    assert rules.heard_count(checks, rules.heard_min_coverage(v)) == 1
    assert rules.heard_count(checks, rules.heard_min_coverage({})) == 2
    assert not rules.recheck_complete("direct", checks, min_coverage=rules.heard_min_coverage(v))
    assert rules.recheck_complete("direct", checks, min_coverage=rules.heard_min_coverage({}))


# ------------------------------------------------------------------------------------------ 스키마

def test_v0_1_config_untouched_and_still_valid():
    c = _config()
    assert "heard_min_coverage" not in c["values"]["recheck"]
    assert hashlib.sha256(canonical_json(c["values"])).hexdigest() == V0_1_THRESHOLDS_SHA256
    assert _validate(c) == []


def test_schema_accepts_optional_value_in_range():
    c = _config()
    for ok in (0.5, 0.95, 1):
        d = copy.deepcopy(c)
        d["values"]["recheck"]["heard_min_coverage"] = ok
        assert _validate(d) == [], ok
    for bad in (0.4, 1.1, "0.9", None):
        d = copy.deepcopy(c)
        d["values"]["recheck"]["heard_min_coverage"] = bad
        assert _validate(d), bad


def test_schema_and_code_range_agree():
    with open(SCHEMA, encoding="utf-8") as f:
        s = json.load(f)
    rc = s["$defs"]["values"]["properties"]["recheck"]
    spec = rc["properties"]["heard_min_coverage"]
    assert "heard_min_coverage" not in rc["required"]
    assert (spec["minimum"], spec["maximum"]) == rules.HEARD_MIN_COVERAGE_RANGE


# ------------------------------------------------------------------------------------------ 내보내기

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


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        self.t += timedelta(milliseconds=10)
        return self.t


def _evidence():
    keys = ("nickname_called", "text_repeated", "direct_reference", "answer_form", "unique_content",
            "within_time_window", "same_topic")
    return {k: {"present": k == "nickname_called"} for k in keys}


def _make(tmp_path, thresholds: dict) -> str:
    """합성 프로젝트: 직접 반응 1건 + 영상 확인 2회(덮은 비율 0.92, 0.97) + 이어짐."""
    FakeLock.held = set()
    base = tmp_path / "한글 문서" / "프로젝트"
    p = project.create(str(base), "합성 방송", 123, "시험용", "L01", "0.0.1", NOW, PID, bytes(range(32)),
                       guide=project.GuideSource("0.1", b"# guide\n", thresholds), machine4="m3fa")
    jsonl.write_all(os.path.join(p.root, "chat", "messages.jsonl"), [
        {"chat_id": C1, "t_ms": 60000, "server_time_ms": 1, "type_code": 1, "kind": "text", "status": "normal",
         "viewer_id": "vw_aaaaaaaaaaaa", "is_anonymous": False, "nickname_masked": "시**", "role": "common_user",
         "text": "합성 채팅", "flags": {}, "raw_ref": "f:1:0"}])

    def verify(d):
        d["calibration"]["revs"][0]["status"] = "verified"
    project.open_project(p.root).update(verify, by="L01")
    ctx = session.SessionContext("L01", "primary", SID, session.machine4(MID), machine_id=MID,
                                 session_created_at="2026-10-21T06:00:00+09:00")
    st = store.ProjectStore.open(p.root, ctx, FakeLock, clock=Clock(), app_version="0.0.1")
    seg = st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 600000, "category": "just_chatting",
                                                        "confidence": "certain", "rationale": "00:00 합성"}})
    utt = st.append_label({"kind": "utterance", "body": {"segment_id": seg["entity_id"], "start_ms": 61000,
                                                          "end_ms": 63000, "heard_text": "합성 발화",
                                                          "heard_text_source": "typed"}})
    rx = st.append_label({"kind": "reaction", "body": {
        "chat_id": C1, "segment_id": seg["entity_id"], "entry": "from_utterance", "result": "graded",
        "grade": "direct", "eligible_grades": ["direct"], "grade_vs_conditions": "consistent",
        "candidates": [{"utterance_id": utt["entity_id"], "evidence": _evidence()}],
        "linked_utterance_id": utt["entity_id"], "first_visible_ms": 60000, "first_visible_source": "chat_clock",
        "reaction_time_ms": 61500, "confidence": "certain", "rationale": "00:01:01 합성 호명"}})
    for chk, cov in (("first", 0.92), ("recheck", 0.97)):
        st.append_label({"kind": "video_check", "body": {
            "target_kind": "reaction", "target_entity_id": rx["entity_id"], "check": chk,
            "window_start_ms": 50000, "window_end_ms": 93000, "watched_ranges": [[50000, 93000]],
            "coverage_ratio": cov, "audio_heard": True}})
    st.append_label({"kind": "continuation", "body": {
        "reaction_entity_id": rx["entity_id"], "type": "none", "span_start_ms": 61500, "span_end_ms": None,
        "observed_end_ms": None, "truncated_at_segment_end": False, "related_chat_ids": [],
        "confidence": "certain", "rationale": "00:01:01 이어지지 않음"}})
    st.close()
    return p.root


def _reaction_reasons(root, tmp_path, name, **opt):
    rows, info = export.build_rows(root, export.ExportOptions(**opt))
    rx = next(r for r in rows if r["label_kind"] == "reaction")
    return rx["eval"]["excluded_reasons"], info


def test_export_uses_code_default_when_guide_has_no_value(tmp_path):
    root = _make(tmp_path, _config()["values"])
    reasons, info = _reaction_reasons(root, tmp_path, "a")
    assert reasons == []                                     # 0.92·0.97 둘 다 0.9 이상 → 2회
    assert info["continuation_missing"] == 0


def test_export_uses_guide_value(tmp_path):
    v = copy.deepcopy(_config()["values"])
    v["recheck"]["heard_min_coverage"] = 0.95
    root = _make(tmp_path, v)
    reasons, _ = _reaction_reasons(root, tmp_path, "b")
    assert reasons == ["recheck_incomplete"]                 # 0.95 기준이면 1회만 인정
    v["recheck"]["heard_min_coverage"] = 0.98                # (다른 폴더) 둘 다 미달 → 안 들음
    root2 = _make(tmp_path / "둘째", v)
    reasons2, _ = _reaction_reasons(root2, tmp_path, "c")
    assert reasons2 == ["audio_not_heard", "recheck_incomplete"]


def test_export_option_overrides_guide(tmp_path):
    v = copy.deepcopy(_config()["values"])
    v["recheck"]["heard_min_coverage"] = 0.95
    root = _make(tmp_path, v)
    reasons, _ = _reaction_reasons(root, tmp_path, "d", heard_min_coverage=0.9)
    assert reasons == []


def test_full_export_zip_with_guide_value(tmp_path):
    v = copy.deepcopy(_config()["values"])
    v["recheck"]["heard_min_coverage"] = 0.95
    root = _make(tmp_path, v)
    out = str(tmp_path / "내보내기" / "out.zip")
    res = export.export_labels(root, out, clock=lambda: NOW, uuid_factory=lambda: "99999999-0000-4000-8000-000000000999",
                               app_version="0.0.1")
    assert res.manifest["counts"]["excluded"] == {"recheck_incomplete": 1}
    assert not any("이어짐을 비운" in w for w in res.manifest["warnings"])


@pytest.fixture(autouse=True)
def _clear_locks():
    FakeLock.held = set()
    yield
    FakeLock.held = set()
