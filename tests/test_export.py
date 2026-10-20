"""app/export.py · tools/export_labels.py — 허용 목록·보호 구역 제외·CSV 셀 보호·식별자 0건·블라인드 (03 16장, 05 10-4·S-11·S-27).

합성 데이터만 쓴다. 닉네임·userIdHash·본문은 지어낸 값이다(05 10-6).
"""
import csv
import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from app import export, jsonl, project, session, store

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 6, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-555555555555"
SALT = bytes(range(32))
MID_A = "aaaaaaaa-0000-4000-8000-000000000001"
MID_B = "bbbbbbbb-0000-4000-8000-000000000002"
SID_A = "L01-P-20261021-ab12"
SID_B = "L02-P-20261021-cd34"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 지어낸 식별자·비밀 표식. zip 안 어디에도 나오면 안 된다(옵션에 따라 일부는 나와도 된다).
USER_ID_HASH = hashlib.sha1(b"fixture-1").hexdigest()
USER_ID_HASH2 = hashlib.sha1(b"fixture-2").hexdigest()
RAW_NICK = "합성원닉네임"
MASKED_NICK = "합**"
TEXT_SECRET = "본문표식문장"
HEARD_SECRET = "받아쓰기표식"
NOTE_SECRET = "증거메모표식"
X_SECRET = "실험필드표식"
ASR_SECRET = "자동인식표식"
ENGINE_SECRET = "엔진결과표식"
ORIGIN_SECRET = "출처기록표식"

C1 = "c" + "1" * 20          # 일반 채팅(수식 본문)
C2 = "c" + "2" * 20          # 후원 채팅
C3 = "c" + "3" * 20          # 놓친 후보 대상
VW1 = "vw_aaaaaaaaaaaa"
VW2 = "vw_bbbbbbbbbbbb"


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


def uuids(start):
    n = [start]

    def make():
        n[0] += 1
        return f"{n[0]:08x}-0000-4000-8000-{n[0]:012x}"
    return make


def ctx(mid, sid, labeler, kind="primary"):
    return session.SessionContext(labeler, kind, sid, session.machine4(mid), machine_id=mid,
                                  session_created_at="2026-10-21T06:00:00+09:00")


def open_store(root, c, start):
    return store.ProjectStore.open(root, c, FakeLock, clock=Clock(), uuid_factory=uuids(start), app_version="0.0.1")


def evidence(**present):
    return {k: ({"present": present.get(k, False), "at_ms": None, "note": NOTE_SECRET} if k in present
                else {"present": False}) for k in ("nickname_called", "text_repeated", "direct_reference",
                                                    "answer_form", "unique_content", "within_time_window",
                                                    "same_topic")}


def _write(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


@pytest.fixture(autouse=True)
def _clear_locks():
    FakeLock.held = set()
    yield
    FakeLock.held = set()


@pytest.fixture
def proj(tmp_path):
    """합성 프로젝트: 세션 둘(L01·L02), 보호 구역·파생·출처·표본 파일까지 있는 폴더."""
    base = tmp_path / "한글 문서" / "프로젝트들"
    p = project.create(str(base), "합성 방송 제목", 123, "시험용 목적", "L01", "0.0.1", NOW, PID, SALT,
                       guide=project.GuideSource("0.1", b"# guide\n", {"reaction": {"time_window_sec": 20}}),
                       machine4="m3fa")
    root = p.root
    msgs = [
        {"chat_id": C1, "t_ms": 60000, "server_time_ms": 1, "type_code": 1, "kind": "text", "status": "normal",
         "viewer_id": VW1, "is_anonymous": False, "nickname_masked": MASKED_NICK, "role": "common_user",
         "text": "=HYPERLINK(\"http://example.invalid\",\"x\") " + TEXT_SECRET, "flags": {}, "raw_ref": "f:1:0"},
        {"chat_id": C2, "t_ms": 70000, "server_time_ms": 2, "type_code": 10, "kind": "donation", "status": "normal",
         "viewer_id": VW2, "is_anonymous": False, "nickname_masked": MASKED_NICK, "role": "common_user",
         "text": "+후원 " + TEXT_SECRET, "flags": {}, "raw_ref": "f:1:1"},
        {"chat_id": C3, "t_ms": 80000, "server_time_ms": 3, "type_code": 1, "kind": "text", "status": "normal",
         "viewer_id": "anon", "is_anonymous": True, "nickname_masked": MASKED_NICK, "role": "common_user",
         "text": "@놓친 " + TEXT_SECRET, "flags": {}, "raw_ref": "f:1:2"},
    ]
    jsonl.write_all(os.path.join(root, "chat", "messages.jsonl"), msgs)
    ident = [{"viewer_id": VW1, "user_id_hash": USER_ID_HASH, "nickname": RAW_NICK},
             {"viewer_id": VW2, "user_id_hash": USER_ID_HASH2, "nickname": RAW_NICK + "2"}]
    jsonl.write_all(os.path.join(root, "chat", "protected", "identities.jsonl"), ident)
    _write(os.path.join(root, "chat", "protected", "pages-x.jsonl.gz"), (USER_ID_HASH + RAW_NICK).encode())
    _write(os.path.join(root, "derived", "asr.json"), json.dumps({"t": ASR_SECRET}, ensure_ascii=False).encode())
    _write(os.path.join(root, "derived", "link-20261021.json"),
           json.dumps({"engine": ENGINE_SECRET, "grade": "direct"}, ensure_ascii=False).encode())
    _write(os.path.join(root, "origin", "gen.jsonl"), ORIGIN_SECRET.encode())
    _write(os.path.join(root, "samples", "S-x.json"), b'{"sample": true}')

    # 세션 A (L01): 구간(고친 판·철회 포함) · 발화 · 반응 · 영상 확인 2회 · 이어짐 · 놓친 후보
    a = open_store(root, ctx(MID_A, SID_A, "L01"), 0)
    seg = a.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 600000, "category": "just_chatting",
                                                       "confidence": "certain", "rationale": "00:00 처음 판"}})
    seg2 = a.append_label({"kind": "segment", "entity_id": seg["entity_id"], "supersedes": seg["id"],
                           "body": {"start_ms": 0, "end_ms": 600000, "category": "just_chatting",
                                    "confidence": "certain", "rationale": "-2+3 고친 판"},
                           "x": {"memo": X_SECRET}})
    gone = a.append_label({"kind": "segment", "body": {"start_ms": 600000, "end_ms": 700000, "category": "game",
                                                        "confidence": "certain", "rationale": "지울 구간"}})
    a.retract(gone["entity_id"], "undo")
    utt = a.append_label({"kind": "utterance", "body": {"segment_id": seg["entity_id"], "start_ms": 61000,
                                                         "end_ms": 63000, "heard_text": HEARD_SECRET,
                                                         "heard_text_source": "typed", "speaker": "streamer",
                                                         "modality": "verbal"}})
    rx = a.append_label({"kind": "reaction", "body": {
        "chat_id": C1, "segment_id": seg["entity_id"], "entry": "from_utterance", "result": "graded",
        "grade": "direct", "eligible_grades": ["direct"], "grade_vs_conditions": "consistent",
        "candidates": [{"utterance_id": utt["entity_id"],
                        "evidence": evidence(nickname_called=True, within_time_window=True, same_topic=True)}],
        "linked_utterance_id": utt["entity_id"], "first_visible_ms": 60000, "first_visible_source": "chat_clock",
        "reaction_time_ms": 61500, "confidence": "certain", "rationale": "=1+1 01:01 호명"}})
    for i, chk in enumerate(("first", "recheck")):
        a.append_label({"kind": "video_check", "body": {
            "target_kind": "reaction", "target_entity_id": rx["entity_id"], "check": chk,
            "window_start_ms": 50000, "window_end_ms": 93000, "watched_ranges": [[50000, 93000]],
            "coverage_ratio": 1.0, "audio_heard": True}})
    a.append_label({"kind": "continuation", "body": {
        "reaction_entity_id": rx["entity_id"], "type": "short", "span_start_ms": 61500, "span_end_ms": 70000,
        "observed_end_ms": None, "truncated_at_segment_end": False, "related_chat_ids": [C3],
        "confidence": "probably", "rationale": "01:01 짧게 이어짐"}})
    a.append_label({"kind": "missed_candidate", "body": {
        "chat_id": C3, "segment_id": seg["entity_id"], "scope": "sample",
        "cond1": {"value": True, "in_segment": True, "in_buffer": False},
        "cond2": {"value": True, "exposure_est_ms": 8000, "chat_rate_10s": 3},
        "cond3": True, "cond4": False, "is_candidate": False, "reason_code": "D", "check_question": None,
        "confidence": "certain", "rationale": "@SUM(1) 이미 다룸"}})
    a.close()

    # 세션 B (L02): 구간 하나(아마도) · 후원 채팅 반응(재확인 없음)
    b = open_store(root, ctx(MID_B, SID_B, "L02"), 1000)
    segb = b.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 600000, "category": "game",
                                                        "confidence": "probably", "rationale": "다른 라벨러 구간"}})
    uttb = b.append_label({"kind": "utterance", "body": {"segment_id": segb["entity_id"], "start_ms": 71000,
                                                          "end_ms": 73000, "heard_text": HEARD_SECRET,
                                                          "heard_text_source": "typed", "speaker": "streamer",
                                                          "modality": "verbal"}})
    b.append_label({"kind": "reaction", "body": {
        "chat_id": C2, "segment_id": segb["entity_id"], "entry": "from_utterance", "result": "graded",
        "grade": "likely", "eligible_grades": ["likely"], "grade_vs_conditions": "consistent",
        "candidates": [{"utterance_id": uttb["entity_id"], "evidence": evidence(answer_form=True, unique_content=True)}],
        "linked_utterance_id": uttb["entity_id"], "first_visible_ms": 70000,
        "first_visible_source": "chat_clock", "reaction_time_ms": 72000, "confidence": "certain",
        "rationale": "01:12 감사 인사"}})
    b.close()
    return {"root": root, "seg2": seg2, "gone": gone, "rx": rx, "utt": utt}


def read_zip(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def rows_of(files):
    return [json.loads(ln) for ln in files[export.JSONL_NAME].decode("utf-8").splitlines() if ln]


def csv_rows(files, name):
    raw = files[name]
    assert raw.startswith(b"\xef\xbb\xbf")             # UTF-8 BOM (16-4)
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


def do_export(proj, tmp_path, name="out.zip", **kw):
    opts = kw.pop("options", export.ExportOptions())
    out = str(tmp_path / "내보내기" / name)
    res = export.export_labels(proj["root"], out, opts, clock=lambda: NOW,
                               uuid_factory=lambda: "99999999-0000-4000-8000-000000000999", app_version="0.0.1", **kw)
    return res, read_zip(out)


# ------------------------------------------------------------------------------------------------ safe_cell

@pytest.mark.parametrize("v,want", [
    ("=1+1", "'=1+1"), ("+1", "'+1"), ("-2+3", "'-2+3"), ("@SUM(A1)", "'@SUM(A1)"),
    ("  =1", "'  =1"), ("\t=1", "'\t=1"), ("\r=1", "'=1"), ("a\x00b\x07c", "abc"), ("줄\n바꿈", "줄\n바꿈"),
    ("보통 문장", "보통 문장"), ("", ""),
])
def test_safe_cell(v, want):
    assert export.safe_cell(v) == want


def test_safe_cell_keeps_numbers():
    assert export.safe_cell(-5) == -5 and export.safe_cell(1.5) == 1.5 and export.safe_cell(None) is None


# ------------------------------------------------------------------------------------------------ 기본 내보내기

def test_zip_contains_only_allowed_files_and_current_labels(proj, tmp_path):
    res, files = do_export(proj, tmp_path)
    assert sorted(files) == sorted(export.ZIP_NAMES)
    rows = rows_of(files)
    ids = {r["label_id"] for r in rows}
    assert proj["seg2"]["id"] in ids                    # 고친 판(현재 유효)
    assert proj["seg2"]["supersedes"] not in ids          # 대체된 판은 없음
    assert all(r["entity_id"] != proj["gone"]["entity_id"] for r in rows)   # 철회된 대상 없음
    assert res.by_kind == {"segment": 2, "reaction": 2, "continuation": 1, "missed_candidate": 1}
    assert {r["label_kind"] for r in rows} <= set(export.ROW_KINDS)       # 발화·영상 확인은 행이 아님
    assert all(r["effective"] is True and r["source"] == "label" for r in rows)
    assert res.session_ids == [SID_A, SID_B]


def test_rows_and_manifest_match_schema(proj, tmp_path):
    res, files = do_export(proj, tmp_path)
    row_schema, man_schema = export._load_export_schema()
    for r in rows_of(files):
        assert row_schema.check(r) == []
    m = json.loads(files[export.MANIFEST_NAME])
    assert man_schema.check(m) == []
    assert m["export_type"] == "full_labels" and m["app_version"] == "0.0.1"
    assert m["filters"]["session_ids"] == [SID_A, SID_B]
    assert m["filters"]["include_text"] is False and m["filters"]["include_protected"] is False
    pj = project.open_project(proj["root"]).data
    g = pj["guides"][0]
    assert m["guides"] == [{"guide_version": "0.1", "doc_sha256": g["doc_sha256"],
                            "thresholds_sha256": g["thresholds_sha256"]}]
    assert m["calibration"] == {"active_rev": 0, "offset_ms": 0, "status": "unverified"}
    with open(os.path.join(proj["root"], "chat", "messages.jsonl"), "rb") as f:
        assert m["messages_sha256"] == hashlib.sha256(f.read()).hexdigest()
    for f in m["files"]:
        assert hashlib.sha256(files[f["name"]]).hexdigest() == f["sha256"]
        assert len(files[f["name"]]) == f["size"]
    assert {f["name"] for f in m["files"]} == set(export.ZIP_NAMES) - {export.MANIFEST_NAME}
    assert m["counts"]["by_kind"]["reaction"] == 2
    assert "CSV 는 참고용" in m["notes"]


def test_reaction_row_is_flattened(proj, tmp_path):
    _, files = do_export(proj, tmp_path)
    rx = next(r for r in rows_of(files) if r["label_kind"] == "reaction" and r["session_id"] == SID_A)
    assert rx["chat_id"] == C1 and rx["chat_kind"] == "text" and rx["chat_t_ms"] == 60000 and rx["viewer_id"] == VW1
    assert rx["delay_ms"] == 1500
    assert rx["evidence_strong"] == ["nickname_called"]
    assert rx["evidence_medium"] == ["within_time_window"] and rx["evidence_weak"] == ["same_topic"]
    assert [u["utterance_id"] for u in rx["utterances"]] == [proj["utt"]["entity_id"]]
    assert rx["utterances"][0]["heard_text"] is None
    assert [c["check"] for c in rx["video_checks"]] == ["first", "recheck"]
    assert rx["text"] is None and rx["nickname_masked"] is None
    assert all("note" not in it for c in rx["evidence_detail"] for it in c["evidence"].values())
    # 보정 미확인 프로젝트라 모든 행이 calibration_unverified (06 6-6)
    assert rx["eval"]["excluded_reasons"] == ["calibration_unverified"]


def test_eval_reasons(proj, tmp_path):
    def verify(d):
        d["calibration"]["revs"][0]["status"] = "verified"
    project.open_project(proj["root"]).update(verify, by="L01")
    res, files = do_export(proj, tmp_path)
    rows = rows_of(files)
    rx_a = next(r for r in rows if r["label_kind"] == "reaction" and r["session_id"] == SID_A)
    assert rx_a["eval"] == {"in_boundary_buffer": False, "strict": True, "lenient": True, "excluded_reasons": []}
    rx_b = next(r for r in rows if r["label_kind"] == "reaction" and r["session_id"] == SID_B)
    # 후원 채팅 + 재확인 없는 '높음'
    assert rx_b["eval"]["excluded_reasons"] == ["non_text_message", "recheck_incomplete"]
    assert rx_b["eval"]["strict"] is False and rx_b["eval"]["lenient"] is False
    seg_b = next(r for r in rows if r["label_kind"] == "segment" and r["session_id"] == SID_B)
    assert seg_b["eval"] == {"strict": False, "lenient": True, "excluded_reasons": ["confidence_probably"]}
    m = res.manifest
    assert m["calibration"]["status"] == "verified"
    assert m["counts"]["excluded"] == {"non_text_message": 1, "confidence_probably": 2}


# ------------------------------------------------------------------------------------------------ 허용 목록

def test_fields_outside_allowlist_do_not_leave(proj, tmp_path):
    _, files = do_export(proj, tmp_path)
    schema = json.load(open(os.path.join(ROOT, "schemas", "export-row.schema.json"), encoding="utf-8"))
    allowed = set(schema["properties"])
    for r in rows_of(files):
        assert set(r) <= allowed
        assert set(r) <= set(export._ROW_KEYS)
        assert "body" not in r and "x" not in r and "seq" not in r and "machine4" not in r
    blob = b"".join(files.values()).decode("utf-8", errors="replace")
    assert X_SECRET not in blob                     # 라벨의 x(실험 필드)는 나가지 않는다
    assert NOTE_SECRET not in blob                  # 증거 메모는 본문 옵션 없이는 나가지 않는다
    # CSV 열도 정한 것만
    for name in (export.CSV_SEGMENTS, export.CSV_REACTIONS, export.CSV_MISSED):
        header = csv_rows(files, name)[0]
        assert header == [c for c in export.CSV_COLUMNS[name] if c not in ("text", "heard_text")]


# ------------------------------------------------------------------------------------------------ 보호 구역 · 식별자

def _hex_runs(s):
    return set(re.findall(r"[0-9a-f]{32,}", s))


def test_protected_and_identifiers_absent_by_default(proj, tmp_path):
    res, files = do_export(proj, tmp_path)
    assert not any(n.startswith(("chat/", "derived/", "origin/", "samples/", "locks/", "guide/", "labels/"))
                   for n in files)
    blob = b"".join(files.values()).decode("utf-8", errors="replace")
    forbidden = [USER_ID_HASH, USER_ID_HASH2, RAW_NICK, MASKED_NICK, TEXT_SECRET, HEARD_SECRET, NOTE_SECRET,
                 X_SECRET, ASR_SECRET, ENGINE_SECRET, ORIGIN_SECRET, SALT.hex(), MID_A, MID_B,
                 session.machine4(MID_A), session.machine4(MID_B), "HYPERLINK", "user_id_hash", "nickname\"",
                 proj["root"], os.path.basename(str(tmp_path)), "한글 문서", "machine_id"]
    for s in forbidden:
        assert s not in blob, s
    # 32자 이상 hex 는 매니페스트에 적은 해시(파일·기준서·채팅 파일)뿐이다
    m = res.manifest
    known = {f["sha256"] for f in m["files"]} | {m["messages_sha256"]}
    known |= {g["doc_sha256"] for g in m["guides"]} | {g["thresholds_sha256"] for g in m["guides"]}
    for name, data in files.items():
        if name == export.MANIFEST_NAME:
            assert _hex_runs(data.decode("utf-8")) <= known
        else:
            assert _hex_runs(data.decode("utf-8-sig")) == set()


def test_include_text_still_hides_raw_identity(proj, tmp_path):
    opts = export.ExportOptions(include_text=True, include_nickname_masked=True, include_heard_text=True)
    res, files = do_export(proj, tmp_path, options=opts)
    blob = b"".join(files.values()).decode("utf-8", errors="replace")
    assert TEXT_SECRET in blob and MASKED_NICK in blob and HEARD_SECRET in blob and NOTE_SECRET in blob
    for s in (USER_ID_HASH, USER_ID_HASH2, RAW_NICK, SALT.hex(), MID_A, ASR_SECRET, ENGINE_SECRET, ORIGIN_SECRET,
              X_SECRET):
        assert s not in blob, s
    m = res.manifest
    assert m["filters"]["include_text"] and m["filters"]["include_nickname_masked"] and m["filters"]["include_heard_text"]


# ------------------------------------------------------------------------------------------------ CSV

def test_csv_cells_are_protected(proj, tmp_path):
    _, files = do_export(proj, tmp_path, options=export.ExportOptions(include_text=True))
    react = csv_rows(files, export.CSV_REACTIONS)
    head = react[0]
    assert head[0] == "session_id"                   # 주석 줄 없이 열 이름이 첫 줄
    body = {r[head.index("session_id")]: dict(zip(head, r)) for r in react[1:]}
    a = body[SID_A]
    assert a["text"].startswith("'=HYPERLINK(")
    assert a["rationale"] == "'=1+1 01:01 호명"
    assert a["viewer_no"] == "1" and a["chat_t_hms"] == "00:01:00.0" and a["delay_ms"] == "1500"
    assert a["evidence_strong"] == "nickname_called" and a["recheck_count"] == "2"
    assert body[SID_B]["text"].startswith("'+")
    seg = csv_rows(files, export.CSV_SEGMENTS)
    sh = seg[0]
    rats = {r[sh.index("rationale")] for r in seg[1:]}
    assert "'-2+3 고친 판" in rats
    missed = csv_rows(files, export.CSV_MISSED)
    mh = missed[0]
    m1 = dict(zip(mh, missed[1]))
    assert m1["text"].startswith("'@") and m1["rationale"] == "'@SUM(1) 이미 다룸"
    assert m1["viewer_id"] == "anon" and m1["viewer_no"] == ""
    # 모든 칸: 수식 시작 글자로 시작하는 칸이 없다(숫자 칸의 음수는 없음)
    for name in (export.CSV_SEGMENTS, export.CSV_REACTIONS, export.CSV_MISSED):
        for row in csv_rows(files, name)[1:]:
            for cell in row:
                assert cell.lstrip(" ")[:1] not in ("=", "+", "-", "@", "\t"), (name, cell[:10])


# ------------------------------------------------------------------------------------------------ 블라인드

def test_labeling_viewer_reads_only_own_session(proj, tmp_path, monkeypatch):
    opened = []
    real = jsonl.read

    def spy(path, *a, **kw):
        opened.append(os.path.basename(path))
        return real(path, *a, **kw)
    monkeypatch.setattr(jsonl, "read", spy)
    viewer = ctx(MID_A, SID_A, "L01")
    res, files = do_export(proj, tmp_path, viewer=viewer)
    assert res.session_ids == [SID_A]
    assert {r["session_id"] for r in rows_of(files)} == {SID_A}
    assert not any(n.startswith(SID_B) for n in opened)      # 다른 세션 파일은 열지도 않는다
    assert res.manifest["created_by"] == "L01"


def test_adjudication_viewer_reads_only_locked_sessions(proj, tmp_path):
    _write(os.path.join(proj["root"], "locks", f"{SID_B}.json"), b"{}")
    viewer = ctx(MID_A, "L03-AD-20261021-ee11", "L03", kind="adjudication")
    res, _ = do_export(proj, tmp_path, viewer=viewer)
    assert res.session_ids == [SID_B]


def test_unknown_viewer_kind_reads_nothing(proj, tmp_path):
    class V:
        session_kind = "weird"
        session_id = SID_A
        labeler_id = "L01"
    res, files = do_export(proj, tmp_path, viewer=V())
    assert res.session_ids == [] and rows_of(files) == []


def test_session_filter(proj, tmp_path):
    res, files = do_export(proj, tmp_path, options=export.ExportOptions(session_ids=(SID_B,)))
    assert res.session_ids == [SID_B] and res.by_kind["reaction"] == 1


# ------------------------------------------------------------------------------------------------ 출력 위치

def test_refuses_inside_repo_and_project(proj, tmp_path):
    inside_repo = os.path.join(ROOT, "build", "export-test-should-not-exist.zip")
    with pytest.raises(export.ExportLocationError):
        export.export_labels(proj["root"], inside_repo)
    assert not os.path.exists(inside_repo)
    gitdir = tmp_path / "other-repo"
    (gitdir / ".git").mkdir(parents=True)
    with pytest.raises(export.ExportLocationError):
        export.export_labels(proj["root"], str(gitdir / "sub" / "x.zip"))
    with pytest.raises(export.ExportLocationError):
        export.export_labels(proj["root"], os.path.join(proj["root"], "export.zip"))
    with pytest.raises(export.ExportLocationError):
        export.export_labels(proj["root"], str(tmp_path / "x.txt"))


def test_onedrive_warns_but_writes(proj, tmp_path, monkeypatch):
    od = tmp_path / "OneDriveSim"
    od.mkdir()
    monkeypatch.setenv("OneDrive", str(od))
    out = od / "문서" / "labels.zip"
    res = export.export_labels(proj["root"], str(out))
    assert out.exists()
    assert any("OneDrive" in w for w in res.warnings)
    assert any("OneDrive" in w for w in res.manifest["warnings"])


def test_no_overwrite_and_project_untouched(proj, tmp_path):
    before = {}
    for d, _, fs in os.walk(proj["root"]):
        for f in fs:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                before[p] = hashlib.sha256(fh.read()).hexdigest()
    do_export(proj, tmp_path)
    with pytest.raises(FileExistsError):
        do_export(proj, tmp_path)
    do_export(proj, tmp_path, options=export.ExportOptions(overwrite=True))
    after = {}
    for d, _, fs in os.walk(proj["root"]):
        for f in fs:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                after[p] = hashlib.sha256(fh.read()).hexdigest()
    assert after == before
    assert not any(n.endswith(".tmp") for n in os.listdir(tmp_path / "내보내기"))


# ------------------------------------------------------------------------------------------------ 손상·충돌

def test_corrupt_and_conflict_copies_are_skipped(proj, tmp_path):
    labels = os.path.join(proj["root"], "labels")
    name_b = next(n for n in os.listdir(labels) if n.startswith(SID_B))
    with open(os.path.join(labels, name_b), "rb") as f:
        data = f.read()
    lines = data.split(b"\n")
    lines.insert(1, b"{broken")
    with open(os.path.join(labels, name_b), "wb") as f:
        f.write(b"\n".join(lines))
    name_a = next(n for n in os.listdir(labels) if n.startswith(SID_A))
    with open(os.path.join(labels, name_a), "rb") as f:
        a_data = f.read()
    _write(os.path.join(labels, name_a.replace(".jsonl", "-PC (1).jsonl")), a_data)   # OneDrive 충돌 사본 모양
    res, files = do_export(proj, tmp_path)
    assert res.session_ids == [SID_A]
    assert len({r["label_id"] for r in rows_of(files)}) == len(rows_of(files))       # 충돌 사본을 두 번 세지 않음
    assert any("깨진 줄" in w for w in res.warnings)


# ------------------------------------------------------------------------------------------------ 명령줄 도구

def test_cli(proj, tmp_path, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("export_labels_cli", os.path.join(ROOT, "tools", "export_labels.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "cli" / "x.zip"
    assert mod.main(["--project", proj["root"], "--out", str(out), "--by", "L09"]) == 0
    printed = capsys.readouterr()
    assert "행 6" in printed.out
    assert TEXT_SECRET not in printed.out + printed.err and proj["root"] not in printed.out
    m = json.loads(read_zip(str(out))[export.MANIFEST_NAME])
    assert m["created_by"] == "L09" and m["filters"]["include_text"] is False
    assert mod.main(["--project", proj["root"], "--out", str(out)]) == 1                 # 덮어쓰지 않음
    assert mod.main(["--project", proj["root"], "--out", str(tmp_path / "t.zip"), "--include-text"]) == 0
    assert TEXT_SECRET in read_zip(str(tmp_path / "t.zip"))[export.JSONL_NAME].decode("utf-8")
    assert mod.main(["--project", proj["root"], "--out", os.path.join(ROOT, "x.zip")]) == 2
    assert not os.path.exists(os.path.join(ROOT, "x.zip"))


# ------------------------------------------------------------------------------------------------ D-10 6·7

def test_manifest_counts_linked_reactions_without_continuation(proj, tmp_path):
    """직접·높음 반응에 이어짐이 없으면 막지 않고 매니페스트 warnings 에 건수를 남긴다(D-10 7)."""
    res, files = do_export(proj, tmp_path)
    w = [x for x in res.manifest["warnings"] if "이어짐을 비운 직접·높음 반응" in x]
    assert w == ["이어짐을 비운 직접·높음 반응 1건(이어짐은 선택 항목, D-10 7)."]   # 세션 B 의 '높음' 1건
    assert sum(r["label_kind"] == "reaction" for r in rows_of(files)) == 2      # 행은 그대로 나감
    res2, _ = do_export(proj, tmp_path, name="a.zip", options=export.ExportOptions(session_ids=(SID_A,)))
    assert not any("이어짐을 비운" in x for x in res2.manifest["warnings"])     # A 는 이어짐이 있다

