"""시범 라벨 형식 고정 (08 D-14 7).

tests/fixtures/frozen_labels_v0_1.jsonl 은 시범을 시작할 때의 라벨 형식(label-event 스키마, 봉투 v=1, 기준서 v0.1)으로
쓴 **합성** 기록이다. 세션 넷(1차 P · 같은 사람 재라벨 RS · 조정 AD · 출처 판정 OR)의 줄을 이어 붙였고, 스키마의
기록 종류 15가지를 모두 한 번 이상 담는다. 시범 중에 앱을 고쳐도 이 파일을 store·resolve·validate·export 가 그대로
읽어야 한다. 이 시험이 깨지면 '옛 라벨을 읽는 길' 을 고친다 — 골든을 다시 만들지 않는다.

- 골든은 지난 store(0.0.1)가 시계·uuid 를 고정해 쓴 바이트다. 기준서 해시는 합성 값('a'*64·'b'*64)으로 바꿔 두었고,
  시험 프로젝트의 project.json 도 같은 값으로 맞춘다(도구 없이 사람 손으로 고친 값이 아니라 store 가 project.json 에서 읽어 쓴 값).
- 본문·닉네임은 지어낸 것이다(05 10-6). 실제 방송·사람과 무관하다.
"""
from __future__ import annotations

import json
import os
import zlib
from datetime import datetime, timedelta, timezone

import pytest

from app import export, jsonl, project, session, store
from app.core import resolve as rs
from app.core import visibility as vis
from app.core.validate import load_json_strict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 이름이 labels_ 로 시작하면 make_fixture 가 다시 만드는 resolve 골든 묶음(test_fixture_clean)에 섞여서 앞에 frozen_ 을 둔다.
GOLDEN = os.path.join(ROOT, "tests", "fixtures", "frozen_labels_v0_1.jsonl")

# 골든 바이트 지문. 바뀌었으면 누가 골든을 다시 만든 것이다 — 되돌리고 읽는 코드를 고친다.
GOLDEN_LINES = 29
GOLDEN_BYTES = 16019
GOLDEN_CRC32 = "2047f117"

# 시범 시작(v0.1) 때의 기록 종류 전부. 스키마가 종류를 더해도 이 목록은 줄지 않는다.
FROZEN_KINDS = frozenset({
    "session.open", "run.start", "phase.mark", "view.exposed", "warning", "session.close",
    "segment", "utterance", "reaction", "continuation", "video_check", "missed_candidate",
    "origin", "adjudication", "retract",
})

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 9, 0, 0, tzinfo=KST)
PID = "f0f0f0f0-1111-4222-8333-444444444444"
SALT = bytes(range(32))
GUIDE = project.GuideSource("0.1", "# 합성 기준서 v0.1 (골든용)\n".encode("utf-8"),
                            {"reaction": {"time_window_sec": 20}})
DOC_SHA = "a" * 64
THR_SHA = "b" * 64

SID_P = "L01-P-20261021-aaaa"
SID_RS = "L01-RS-20261028-bbbb"
SID_AD = "L02-AD-20261029-cccc"
SID_OR = "L02-OR-20261029-dddd"
SEG_EID = "a0000000-0000-4000-8000-000000000003"
SEG_HEAD = "a0000000-0000-4000-8000-000000000005"
GONE_EID = "a0000000-0000-4000-8000-000000000006"
C1 = "c" + "1" * 20
C2 = "c" + "2" * 20
C3 = "c" + "3" * 20


class FakeLock:
    def __init__(self, path):
        self.path = path

    def setStaleLockTime(self, ms):
        pass

    def tryLock(self, timeout=0):
        return True

    def unlock(self):
        pass


def _golden_bytes() -> bytes:
    with open(GOLDEN, "rb") as f:
        return f.read()


def _golden_records() -> list[dict]:
    return [load_json_strict(ln, store.MAX_LINE_BYTES) for ln in _golden_bytes().splitlines() if ln.strip()]


def _by_session(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in records:
        out.setdefault(r["session_id"], []).append(r)
    return out


def _lines_by_session() -> dict[str, bytes]:
    """골든 줄을 세션별로 원래 바이트 그대로 나눈다(다시 직렬화하지 않는다)."""
    out: dict[str, bytes] = {}
    for ln in _golden_bytes().splitlines(keepends=True):
        if not ln.strip():
            continue
        sid = load_json_strict(ln, store.MAX_LINE_BYTES)["session_id"]
        out[sid] = out.get(sid, b"") + ln
    return out


@pytest.fixture
def frozen_project(tmp_path):
    """골든을 labels/<session_id>.<machine4>.jsonl 로 풀어 놓은 합성 프로젝트."""
    p = project.create(str(tmp_path / "한글 프로젝트"), "합성 시범 방송", 0, "형식 고정 시험", "L01", "0.0.1", NOW,
                       PID, SALT, guide=GUIDE, machine4="gold")

    def fake_hashes(d):
        d["guides"][0]["doc_sha256"] = DOC_SHA
        d["guides"][0]["thresholds_sha256"] = THR_SHA
    p.update(fake_hashes, by="L01", now=NOW)
    msgs = [
        {"chat_id": C1, "t_ms": 60000, "server_time_ms": 1, "type_code": 1, "kind": "text", "status": "normal",
         "viewer_id": "vw_aaaaaaaaaaaa", "is_anonymous": False, "nickname_masked": "시**", "role": "common_user",
         "text": "합성 채팅 하나", "flags": {}, "raw_ref": "f:1:0"},
        {"chat_id": C2, "t_ms": 70000, "server_time_ms": 2, "type_code": 1, "kind": "text", "status": "normal",
         "viewer_id": "vw_bbbbbbbbbbbb", "is_anonymous": False, "nickname_masked": "시**", "role": "common_user",
         "text": "합성 채팅 둘", "flags": {}, "raw_ref": "f:1:1"},
        {"chat_id": C3, "t_ms": 80000, "server_time_ms": 3, "type_code": 1, "kind": "text", "status": "normal",
         "viewer_id": "vw_aaaaaaaaaaaa", "is_anonymous": False, "nickname_masked": "시**", "role": "common_user",
         "text": "합성 채팅 셋", "flags": {}, "raw_ref": "f:1:2"},
    ]
    jsonl.write_all(os.path.join(p.root, "chat", "messages.jsonl"), msgs)
    opens = {r["session_id"]: r["body"] for r in _golden_records() if r["kind"] == "session.open"}
    for sid, data in _lines_by_session().items():
        name = session.session_file_name(sid, opens[sid]["machine4"])
        with open(os.path.join(p.root, "labels", name), "wb") as f:
            f.write(data)
    return p.root, opens


def _ctx(sid: str, body: dict) -> session.SessionContext:
    return session.SessionContext(sid.split("-", 1)[0], body["session_kind"], sid, body["machine4"],
                                  machine_id=body["machine_id"], session_created_at=body["session_created_at"])


# ------------------------------------------------------------------------------------------------ 골든 자체

def test_golden_bytes_unchanged():
    b = _golden_bytes()
    assert len(b) == GOLDEN_BYTES
    assert b.count(b"\n") == GOLDEN_LINES and b.endswith(b"\n") and b"\r" not in b
    assert format(zlib.crc32(b), "08x") == GOLDEN_CRC32


def test_golden_covers_every_frozen_kind_and_schema_still_knows_them():
    recs = _golden_records()
    kinds = {r["kind"] for r in recs}
    assert kinds == FROZEN_KINDS
    schema = store.load_label_schema()
    assert FROZEN_KINDS <= set(schema.root["properties"]["kind"]["enum"])   # 종류를 지우거나 이름을 바꾸지 않는다
    assert {vis.session_kind_of_id(s) for s in _by_session(recs)} == {
        vis.PRIMARY, vis.RELABEL_SAME, vis.ADJUDICATION, vis.ORIGIN_REVIEW}


# ------------------------------------------------------------------------------------------------ validate

def test_every_line_is_strict_json_and_passes_current_schema():
    schema = store.load_label_schema()
    recs = _golden_records()
    assert len(recs) == GOLDEN_LINES
    for r in recs:
        assert r["v"] == 1 and store.RECORD_V >= 1       # 봉투 v=1 은 앞으로도 읽는다
        assert schema.check(r) == [], (r["session_id"], r["seq"], r["kind"])
    for sid, rs_ in _by_session(recs).items():
        assert [r["seq"] for r in rs_] == list(range(1, len(rs_) + 1)), sid
        assert rs_[0]["kind"] == "session.open" and rs_[-1]["kind"] == "session.close"


# ------------------------------------------------------------------------------------------------ resolve

def test_resolve_reads_golden_without_problems():
    view = rs.resolve(_golden_records())
    assert view.invalid_ids == {}
    assert view.conflicts() == []
    counts: dict[str, int] = {}
    for rec in view.items():
        counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1
    assert counts == {"segment": 2, "utterance": 1, "reaction": 2, "continuation": 1, "video_check": 2,
                      "missed_candidate": 1, "adjudication": 1, "origin": 1}
    seg = view.effective_entity(SEG_EID, SID_P)
    assert seg["id"] == SEG_HEAD and seg["body"]["rationale"] == "00:00 합성 고친 판"
    assert seg["x"] == {"memo": "합성 실험 필드"}
    assert view.effective_entity(GONE_EID, SID_P) is None
    assert view.chain(GONE_EID, SID_P).status == rs.RETRACTED


# ------------------------------------------------------------------------------------------------ store

@pytest.mark.parametrize("sid", [SID_P, SID_RS, SID_AD, SID_OR])
def test_store_reopens_each_golden_session(frozen_project, sid):
    root, opens = frozen_project
    path = os.path.join(root, "labels", session.session_file_name(sid, opens[sid]["machine4"]))
    with open(path, "rb") as f:
        before = f.read()
    st = store.ProjectStore.open(root, _ctx(sid, opens[sid]), FakeLock, app_version="9.9.9")
    assert not st.read_only, st.read_only_reasons
    assert not any("해시" in w for w in st.warnings), st.warnings
    assert st.records() == _by_session(_golden_records())[sid]
    assert st.session_meta["session_kind"] == opens[sid]["session_kind"]
    with open(path, "rb") as f:
        assert f.read() == before                        # 여는 것만으로는 옛 기록을 고치지 않는다


def test_store_appends_after_golden_lines(frozen_project):
    """새 앱이 옛 세션 파일 뒤에 이어 쓸 수 있다: 옛 판을 고치는 새 판·철회."""
    root, opens = frozen_project
    st = store.ProjectStore.open(root, _ctx(SID_P, opens[SID_P]), FakeLock, app_version="9.9.9")
    assert not st.read_only, st.read_only_reasons
    head = st.head(SEG_EID)
    assert head["id"] == SEG_HEAD
    new = st.append_label({"kind": "segment", "entity_id": SEG_EID, "supersedes": SEG_HEAD,
                           "body": dict(head["body"], rationale="00:00 합성 새 앱 판")})
    assert new["seq"] == 19 and new["guide_sha12"] == "a" * 12 and new["thresholds_sha12"] == "b" * 12
    st.close()
    st2 = store.ProjectStore.open(root, _ctx(SID_P, opens[SID_P]), FakeLock, app_version="9.9.9")
    assert not st2.read_only, st2.read_only_reasons
    assert st2.effective().effective_entity(SEG_EID, SID_P)["id"] == new["id"]


# ------------------------------------------------------------------------------------------------ export

def test_export_reads_golden_project(frozen_project, tmp_path):
    root, _ = frozen_project
    rows, info = export.build_rows(root)
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["label_kind"]] = by_kind.get(r["label_kind"], 0) + 1
    assert by_kind == {"segment": 2, "reaction": 2, "continuation": 1, "missed_candidate": 1}
    assert info["invalid_records"] == 0 and info["conflicts"] == 0
    assert sorted(info["session_ids"]) == sorted([SID_P, SID_RS, SID_AD, SID_OR])
    row_schema, man_schema = export._load_export_schema()
    for r in rows:
        assert row_schema.check(r) == []

    out = str(tmp_path / "내보내기" / "frozen.zip")
    res = export.export_labels(root, out, clock=lambda: NOW,
                               uuid_factory=lambda: "99999999-0000-4000-8000-000000000999", app_version="9.9.9")
    assert res.by_kind == by_kind and res.rows == 6
    assert man_schema.check(res.manifest) == []
    rx = next(r for r in rows if r["label_kind"] == "reaction" and r["chat_id"] == C1)
    assert rx["delay_ms"] == 1500 and rx["evidence_strong"] == ["nickname_called"]
    assert [c["check"] for c in rx["video_checks"]] == ["first", "recheck"]
    mc = next(r for r in rows if r["label_kind"] == "missed_candidate")
    assert mc["chat_id"] == C3 and mc["session_id"] == SID_RS
    assert json.dumps(rows, ensure_ascii=False).count("합성 채팅") == 0   # 본문은 기본으로 나가지 않는다
