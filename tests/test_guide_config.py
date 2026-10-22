"""assets/guide-config.v0.1.json: 스키마 적합, 안내서 v0.1 시작값과 일치, 해시 상수, app.project.create 연결.

정규화 해시(03 8-4, app.core.validate.canonical_json)는 스키마가 최상위 추가 키를 막아 파일에 넣을 수 없으므로
여기 상수(THRESHOLDS_SHA256)로 둔다. 값 하나라도 바꾸면 이 상수가 깨진다 → guide_version 을 올리거나
change_log 를 적은 뒤 상수를 고친다(03 9-3).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone

import pytest

from app import project
from app.core import rules, segments
from app.core.validate import canonical_json, check, load_json_strict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "assets", "guide-config.v0.1.json")
SCHEMA = os.path.join(ROOT, "schemas", "guide-config.schema.json")
GUIDE_DOC = os.path.join(ROOT, "docs", "labeling-guide", "v0.1.md")

# values 정규화 JSON 의 SHA-256 (project.json guides[].thresholds_sha256 와 같은 값)
THRESHOLDS_SHA256 = "e8462bfc86dd1ba04619e0eefff7682e72c899fa0f8b6fdf70142a0b5b4947b2"
THRESHOLDS_SHA12 = THRESHOLDS_SHA256[:12]

# 안내서 v0.1 에 숫자가 없어 null 로 둔 값 (09 5-2 질문 5 등에서 정한다)
EXPECTED_NULLS = {
    "reaction.similar_chat_window_sec",
    "missed.max_chat_rate_per_10s",
    "missed.visible_lines",
    "relabel.block_minutes",
    "origin.match_time_tolerance_sec",
    "origin.text_similarity_min",
    "origin.ambiguous_margin",
}

ANNOTATION_KEYS = {"$schema", "$id", "title", "description", "format"}


def _load_config() -> dict:
    with open(CONFIG, "rb") as f:
        return load_json_strict(f.read(), 1024 * 1024)


def _load_schema() -> dict:
    with open(SCHEMA, encoding="utf-8") as f:
        return json.load(f)


def _strip(node):
    """app.core.validate.check 가 모르는 설명용 키(description·format 등)를 스키마 노드에서 뗀다."""
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k in ANNOTATION_KEYS:
            continue
        if k in ("properties", "$defs"):
            out[k] = {name: _strip(sub) for name, sub in v.items()}
        elif k in ("items", "additionalProperties"):
            out[k] = _strip(v)
        elif k == "oneOf":
            out[k] = [_strip(s) for s in v]
        else:
            out[k] = v
    return out


def _validate(obj) -> list[str]:
    spec = _strip(_load_schema())
    return check(obj, spec, spec)


def _leaves(d: dict, prefix=""):
    for k, v in d.items():
        p = f"{prefix}{k}"
        if isinstance(v, dict):
            yield from _leaves(v, p + ".")
        else:
            yield p, v


# ---------------------------------------------------------------------------

def test_matches_schema():
    assert _validate(_load_config()) == []


def test_validator_catches_errors():
    """검사기가 실제로 잡는지(가짜 통과 방지)."""
    c = _load_config()
    bad = copy.deepcopy(c)
    bad["extra"] = 1
    assert _validate(bad)
    bad = copy.deepcopy(c)
    bad["values"]["evidence_strength"]["same_topic"] = "very_weak"
    assert _validate(bad)
    bad = copy.deepcopy(c)
    bad["values"]["reaction"]["direct_min_strong"] = 1.0       # 정수 칸에 실수
    assert _validate(bad)
    bad = copy.deepcopy(c)
    del bad["values"]["segment"]["min_length_sec"]
    assert _validate(bad)
    bad = copy.deepcopy(c)
    bad["values"]["sample"]["max_extend_ratio"] = None         # null 이 허용되지 않는 칸
    assert _validate(bad)


def test_header_fields():
    c = _load_config()
    assert c["schema"] == "chatcoach/guide-config" and c["schema_version"] == 1
    assert c["guide_version"] == project.DEFAULT_GUIDE_VERSION == "0.1"
    assert c["guide_doc"] == "docs/labeling-guide/v0.1.md"
    assert c["effective_date"] == "2026-10-18"          # 안내서 머리의 작성 기준일
    assert c["change_log"] == []


def test_guide_doc_sha256_matches_file():
    with open(GUIDE_DOC, "rb") as f:
        b = f.read()
    assert b"\r\n" not in b                              # .gitattributes 가 LF 로 고정
    assert _load_config()["guide_doc_sha256"] == hashlib.sha256(b).hexdigest()


def test_thresholds_hash_constant():
    v = _load_config()["values"]
    assert hashlib.sha256(canonical_json(v)).hexdigest() == THRESHOLDS_SHA256


def test_nulls_are_exactly_the_undecided_values():
    nulls = {p for p, v in _leaves(_load_config()["values"]) if v is None}
    assert nulls == EXPECTED_NULLS


def test_values_match_schema_descriptions():
    """스키마 설명의 'v0.1 시작값 N' · 'v0.1: strong' · '시작값 N(안내서에 없는 새 값)' 과 파일 값이 같다."""
    schema = _load_schema()
    vals = _load_config()["values"]
    seen = 0
    for sec, sec_spec in schema["$defs"]["values"]["properties"].items():
        # 선택 섹션(예: viewer_score, D-21 4)은 v0.1 파일에 없고 칸 정의가 $ref 쪽에 있다 — 없는 섹션·칸은 건너뛴다
        for key, spec in sec_spec.get("properties", {}).items():
            desc = spec.get("description", "")
            if key not in vals.get(sec, {}) and key not in sec_spec.get("required", ()):
                continue          # v0.1 파일에 없는 선택 항목(예: recheck.heard_min_coverage, D-10 6 — 코드 기본값)
            got = vals[sec][key]
            m = (re.search(r"v0\.1 시작값 ([0-9]+(?:\.[0-9]+)?)", desc)
                 or re.search(r"시작값 ([0-9]+(?:\.[0-9]+)?)\(안내서에 없는 새 값\)", desc))
            if m:
                assert got == float(m.group(1)), f"{sec}.{key}"
                seen += 1
                continue
            m = re.search(r"v0\.1: (strong|medium|weak|gate)", desc)
            if m:
                assert got == m.group(1), f"{sec}.{key}"
                seen += 1
                continue
            m = re.search(r"시작값 (true|false)", desc)
            if m:
                assert got is (m.group(1) == "true"), f"{sec}.{key}"
                seen += 1
    assert seen >= 30


def test_values_spot_check_against_guide_text():
    """안내서 본문의 숫자 몇 개를 직접 대조(스키마 설명이 틀렸을 때도 잡게)."""
    with open(GUIDE_DOC, encoding="utf-8") as f:
        doc = f.read()
    v = _load_config()["values"]
    assert "시작값: 30초 미만" in doc and v["segment"]["silence_keep_max_sec"] == 30
    assert "곡 사이 1분 미만" in doc and v["segment"]["song_interlude_merge_max_sec"] == 60
    assert "시작값: 2분 이상" in doc and v["segment"]["long_silence_review_sec"] == 120
    assert "전환 앞뒤 10초" in doc and v["segment"]["boundary_buffer_sec"] == 10
    assert "시작값: 20초 이내" in doc and v["reaction"]["time_window_sec"] == 20
    assert "구간당 100개" in doc and v["sample"]["per_segment"] == 100
    assert "화면에 10초 이상" in doc and v["missed"]["min_visible_sec"] == 10
    assert "앞뒤 2분 안" in doc and v["missed"]["prior_handling_window_sec"] == 120
    assert "10초 전부터" in doc and v["recheck"]["pre_roll_sec"] == 10
    assert "30초 뒤까지" in doc and v["recheck"]["post_roll_sec"] == 30
    assert "20~30%" in doc and (v["relabel"]["fraction_min"], v["relabel"]["fraction_max"]) == (0.2, 0.3)
    assert "3일 이상" in doc and v["relabel"]["min_interval_days"] == 3
    assert "30건 안팎" in doc and v["pilot"]["target_direct_reaction_count"] == 30
    assert "다른 시청자 채팅 3개 이상" in doc and v["continuation"]["extended_min_other_chats"] == 3
    r = v["relabel"]
    assert r["fraction_min"] <= r["random_share"] + r["hard_share"] <= r["fraction_max"]
    assert v["sample"]["census_max"] >= v["sample"]["per_segment"]


def test_consumers_accept_values():
    v = _load_config()["values"]
    rc = rules.RuleConfig.from_values(v)
    assert rc.time_window_ms == 20_000 and rc.similar_chat_window_ms is None
    assert dict(rc.evidence_strength) == rules.V0_1_STRENGTH
    st = segments.thresholds_from_guide(v["segment"])
    assert (st.buffer_ms, st.min_length_ms) == (10_000, 30_000)


def test_project_create_fills_thresholds(tmp_path):
    now = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
    p = project.create(str(tmp_path), "합성 기준서 시험", 0, machine4="t3st", now=now)
    assert not p.read_only, p.problems
    g = p.data["guides"][0]
    assert g["thresholds"] == _load_config()["values"]
    assert g["thresholds_sha256"] == THRESHOLDS_SHA256
    assert "guide_thresholds_missing" not in (p.data.get("x") or {})
    assert not any("시작값" in w for w in p.warnings)
    gs, warns = project.default_guide()
    assert warns == [] and gs.thresholds == g["thresholds"]


@pytest.mark.parametrize("key", ["guide_version", "guide_doc_sha256", "values"])
def test_required_keys_present(key):
    assert key in _load_config()
