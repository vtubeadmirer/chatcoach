"""app/core/final_set.py · app/export.py 최종 세트 모드 (16 8-1 4번, 06 6-5·6-6, 08 D-15 7).

합성 데이터만 쓴다. 채팅 ID·근거·닉네임은 지어낸 값이다. 기대값은 손으로 센 것이다.
"""
import hashlib
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from app import export, jsonl, project, session, store
from app import agreement_report as ar
from app.core import adjudicate as adj
from app.core import final_set as fs

P = "L01-P-20261024-ab12"
RS = "L01-RS-20261028-cd34"
AD = "L01-AD-20261029-ef56"


def cid(n):
    return "c" + f"{n:020x}"


def uid(n):
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def rx(sid, n, chat, grade, *, result="graded", conf="certain", entry="from_sample", seq=None):
    body = {"chat_id": cid(chat), "entry": entry, "result": result, "grade": None if result == "unconfirmed" else grade,
            "confidence": conf, "rationale": "합성 근거"}
    if entry == "from_sample":
        body["sample_ref"] = {"sample_set_id": "S-1", "order": chat}
    return {"id": uid(n), "entity_id": uid(n + 5000), "session_id": sid, "kind": "reaction", "seq": seq or n,
            "supersedes": None, "body": body}


def seg(sid, n, a, z, cat, conf="certain"):
    return {"id": uid(n), "entity_id": uid(n + 5000), "session_id": sid, "kind": "segment", "seq": n,
            "supersedes": None, "body": {"start_ms": a, "end_ms": z, "category": cat, "confidence": conf,
                                         "rationale": "합성 구간"}}


def target(rec, version=None):
    return {"session_id": rec["session_id"], "entity_id": rec["entity_id"], "version_id": version or rec["id"]}


def adj_rec(n, kind, recs, decision, final=None, seq=None, version=None):
    body = {"target_kind": kind, "targets": sorted([target(r) for r in recs], key=lambda t: t["session_id"]),
            "decision": decision, "cause": "simple_mistake", "pattern_tag": None}
    if version:
        body["targets"][0]["version_id"] = version
    if final is not None:
        body["final"] = final
    return {"id": uid(n), "entity_id": uid(n + 5000), "session_id": AD, "kind": "adjudication", "seq": seq or n,
            "supersedes": None, "body": body}


# ---------------------------------------------------------------------------------------------- 반응
def _reaction_fixture():
    p = {i: rx(P, i, i, g) for i, g in ((1, "direct"), (2, "likely"), (3, "likely"), (4, "related_only"),
                                         (5, "unclear"), (6, "direct"), (8, "direct"))}
    dup = [rx(P, 71, 7, "direct"), rx(P, 72, 7, "likely")]
    r = {i: rx(RS, 100 + i, i, g) for i, g in ((1, "direct"), (2, "related_only"), (3, "direct"), (5, "direct"),
                                                (8, "likely"))}
    r[4] = rx(RS, 104, 4, None, result="unconfirmed")
    final8 = dict(p[8]["body"], grade="related_only")          # 두 쪽 어느 것과도 같지 않은 새 값
    adjs = [adj_rec(201, "reaction", [p[3], r[3]], "final", final=r[3]["body"]),
            adj_rec(202, "reaction", [p[4], r[4]], "exclude_from_eval"),
            adj_rec(203, "reaction", [p[5], r[5]], "keep_as_ambiguous"),
            adj_rec(204, "reaction", [p[8], r[8]], "final", final=final8, version=uid(9999))]
    primary = {"session_id": P, "labeler_id": "L01", "reactions": list(p.values()) + dup}
    relabel = {"session_id": RS, "labeler_id": "L01", "reactions": list(r.values())}
    return primary, relabel, adjs, p, r


def test_reactions_swap_adjudication_with_case_states_key():
    primary, relabel, adjs, p, r = _reaction_fixture()
    out = fs.build_reactions(primary, [relabel], adjs, origins={cid(6): "virtual", cid(1): "real"})
    by = {x.chat_id: x for x in out["reactions"]}
    assert sorted(by) == [cid(1), cid(2), cid(3), cid(6), cid(8)]
    # 1: 두 번 같음
    assert by[cid(1)].relabeled and by[cid(1)].agreed and by[cid(1)].reasons == () and by[cid(1)].source == "label"
    # 2: 다르고 조정 없음 → 사람 불일치
    assert by[cid(2)].agreed is False and by[cid(2)].reasons == ("human_disagreement",)
    # 3: 조정에서 재라벨 쪽을 고름 → 그 라벨 그대로, 엄격에서는 뺌(보고서 사유)
    x3 = by[cid(3)]
    assert x3.source == "adjudication" and x3.record["id"] == r[3]["id"] and x3.record["session_id"] == RS
    assert x3.decision == "final" and not x3.synthesized and x3.report_reasons == (fs.R_ADJ_DISAGREEMENT,)
    assert x3.base_record["id"] == p[3]["id"]
    # 6: 재라벨 없음, 출처 가상
    assert by[cid(6)].relabeled is False and by[cid(6)].reasons == ("origin_virtual",)
    # 8: 새 값 → 1차 레코드에 final 본문, 판이 달라 stale
    x8 = by[cid(8)]
    assert x8.synthesized and x8.stale and x8.record["id"] == p[8]["id"] and x8.record["body"]["grade"] == "related_only"
    # 4: 평가 제외, 5: 모호함으로 남김 — 세트 밖
    assert [x.chat_id for x in out["excluded"]] == [cid(4)] and out["excluded"][0].reasons == ("adjudicated_exclude",)
    assert [x.chat_id for x in out["ambiguous"]] == [cid(5)]
    assert out["ambiguous"][0].report_reasons == (fs.R_ADJ_AMBIGUOUS,)
    # 7: 1차 안에서 같은 채팅이 둘 → 충돌
    assert out["conflicts"] == [{"chat_id": cid(7), "entry": "from_sample", "sample_set_id": "S-1",
                                 "why": "duplicate_chat"}]
    assert out["summary"] == {"primary": 7, "relabeled": 6, "agreed": 1, "kept": 5, "excluded": 1, "ambiguous": 1,
                              "conflicts": 1, "adjudications": 4, "adjudications_unmatched": 0, "stale": 1,
                              "synthesized": 1, "human_disagreement": 1, "adjudicated_final": 2,
                              "adjudicated_disagreement": 2}
    # 입력을 고치지 않는다
    assert p[8]["body"]["grade"] == "direct"


def test_reaction_case_ids_match_adjudication_view_key():
    """조정 화면(case_states)이 쓰는 열쇠와 같은 열쇠로 이어지는지: 같은 기록이 같은 건에 붙는다."""
    primary, relabel, adjs, p, r = _reaction_fixture()
    side = lambda s: {"session_id": s["session_id"], "labeler_id": "L01", "reactions": s["reactions"]}  # noqa: E731
    case = adj.Case(f"reaction:{cid(3)}", adj.TARGET_REACTION, adj.C_GRADE, None,
                    (adj._reaction_side(P, "L01", p[3]), adj._reaction_side(RS, "L01", r[3])), chat_id=cid(3))
    st = adj.case_states([case], adjs)[case.case_id]
    assert st.record["id"] == uid(201)
    out = fs.build_reactions(side(primary), [side(relabel)], adjs)
    assert next(x for x in out["reactions"] if x.chat_id == cid(3)).adjudication_id == uid(201)


def test_later_adjudication_supersedes_by_seq_and_conflict_chain():
    primary, relabel, adjs, p, r = _reaction_fixture()
    later = adj_rec(301, "reaction", [p[2], r[2]], "final", final=p[2]["body"], seq=900)
    earlier = adj_rec(302, "reaction", [p[2], r[2]], "exclude_from_eval", seq=10)
    chain = [rx(P, 401, 9, "direct"), rx(P, 402, 9, "likely")]
    out = fs.build_reactions(primary, [relabel], adjs + [earlier, later], conflict_chains=[chain])
    x2 = next(x for x in out["reactions"] if x.chat_id == cid(2))
    assert x2.decision == "final" and x2.record["id"] == p[2]["id"] and not x2.synthesized
    assert [c["chat_id"] for c in out["conflicts"]] == [cid(7), cid(9)]
    assert out["summary"]["adjudications_unmatched"] == 1     # 같은 열쇠의 앞 기록은 쓰지 않음


def test_without_origins_nothing_added_and_merge_origins():
    primary, relabel, adjs, *_ = _reaction_fixture()
    out = fs.build_final_set(primary, [relabel], adjs)
    assert out.origin_known is False
    assert all(not any(r.startswith("origin_") for r in x.reasons) for x in out.reactions)
    m = fs.merge_origins([{cid(1): "real", cid(2): "virtual"}, {cid(1): "real", cid(2): "real"}])
    assert m == {cid(1): "real", cid(2): "undeterminable"}


def test_same_session_as_primary_is_refused():
    primary, *_ = _reaction_fixture()
    with pytest.raises(fs.FinalSetError):
        fs.build_reactions(primary, [dict(primary)], [])


# ---------------------------------------------------------------------------------------------- 구간
def _segment_fixture():
    p1, p2 = seg(P, 1, 0, 600_000, "just_chatting"), seg(P, 2, 600_000, 1_200_000, "game")
    r1 = seg(RS, 11, 0, 200_000, "just_chatting")
    r2 = seg(RS, 12, 200_000, 260_000, "other")
    r3 = seg(RS, 13, 260_000, 595_000, "just_chatting")
    r4 = seg(RS, 14, 595_000, 600_000, "game")
    primary = {"session_id": P, "segments": [p1, p2], "scope": None}
    relabel = {"session_id": RS, "segments": [r1, r2, r3, r4], "scope": [(0, 600_000)]}
    return primary, relabel, (p1, p2, r1, r2, r3, r4)


def _short(pieces):
    return [(x["start_ms"], x["end_ms"], x["category"], x["status"], x["relabeled"]) for x in pieces]


def test_segment_timeline_overlay_and_buffer_shift():
    primary, relabel, (p1, p2, r1, r2, r3, r4) = _segment_fixture()
    a = adj_rec(501, "segment", [p1, r2], "final", final=r2["body"])
    pieces, overrides, summary = fs.segment_timeline(primary, [relabel], [a], buffer_ms=10_000)
    assert _short(pieces) == [
        (0, 200_000, "just_chatting", "label", True),
        (200_000, 260_000, "other", "adjudicated", True),          # 교집합 [200, 260)초에 조정 값
        (260_000, 595_000, "just_chatting", "label", True),
        (595_000, 600_000, "just_chatting", "label", True),         # 5초 차이(완충 이하) → 경계 차이
        (600_000, 1_200_000, "game", "label", False),               # 재라벨 범위 밖
    ]
    assert pieces[3].get("within_buffer_shift") is True
    assert pieces[1]["record"]["id"] == r2["id"] and pieces[1]["adjudication_id"] == uid(501)
    assert overrides[0]["start_ms"] == 200_000 and overrides[0]["end_ms"] == 260_000
    assert summary["ms_by_status"] == {"label": 1_140_000, "adjudicated": 60_000, "human_disagreement": 0,
                                       "excluded": 0, "ambiguous": 0}


def test_segment_timeline_without_adjudication_marks_disagreement_and_exclude():
    primary, relabel, (p1, p2, r1, r2, r3, r4) = _segment_fixture()
    pieces, _, s = fs.segment_timeline(primary, [relabel], [], buffer_ms=10_000)
    assert ("human_disagreement" in [x["status"] for x in pieces])
    assert s["ms_by_status"]["human_disagreement"] == 60_000
    ex = adj_rec(502, "segment", [p1, r2], "exclude_from_eval")
    pieces, _, s = fs.segment_timeline(primary, [relabel], [ex], buffer_ms=10_000)
    assert s["ms_by_status"]["excluded"] == 60_000 and s["ms_by_status"]["human_disagreement"] == 0
    amb = adj_rec(503, "segment", [p1, r2], "keep_as_ambiguous")
    _, _, s = fs.segment_timeline(primary, [relabel], [amb], buffer_ms=10_000)
    assert s["ms_by_status"]["ambiguous"] == 60_000
    gone = adj_rec(504, "segment", [p1, seg(RS, 99, 0, 10, "game")], "exclude_from_eval")
    _, ov, s = fs.segment_timeline(primary, [relabel], [gone], buffer_ms=10_000)
    assert ov == [] and s["unmatched"] == 1


def test_segment_final_with_moved_boundary_only_inside_final_range():
    primary, relabel, (p1, p2, r1, r2, r3, r4) = _segment_fixture()
    final = dict(r2["body"], start_ms=230_000)                        # 새 값: 시작을 230초로
    a = adj_rec(505, "segment", [p1, r2], "final", final=final)
    pieces, _, _ = fs.segment_timeline(primary, [relabel], [a], buffer_ms=10_000)
    assert (200_000, 230_000, "just_chatting", "human_disagreement", True) in _short(pieces)
    assert (230_000, 260_000, "other", "adjudicated", True) in _short(pieces)
    adjp = next(x for x in pieces if x["status"] == "adjudicated")
    assert adjp["record"]["body"]["start_ms"] == 230_000        # 같은 본문의 라벨이 없어 사본


def test_core_final_set_has_no_io():
    src = open(fs.__file__, encoding="utf-8").read()
    for bad in ("import os", "open(", "datetime", "random", "PySide6", "time."):
        assert bad not in src, bad


# ---------------------------------------------------------------------------------------------- export 최종 세트 모드
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 30, 9, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-666666666666"
MID = "aaaaaaaa-0000-4000-8000-0000000000a1"
SALT = bytes(range(32))


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


def _uuids(start):
    n = [start]

    def make():
        n[0] += 1
        return f"{n[0]:08x}-0000-4000-8000-{n[0]:012x}"
    return make


def _ctx(sid, kind):
    return session.SessionContext("L01", kind, sid, session.machine4(MID), machine_id=MID,
                                  session_created_at="2026-10-24T09:00:00+09:00")


def _store(root, sid, kind, start):
    return store.ProjectStore.open(root, _ctx(sid, kind), FakeLock, clock=Clock(), uuid_factory=_uuids(start),
                                   app_version="0.0.1")


def _lock(root, sid, locked_at="2026-10-24T12:00:00+09:00"):
    labels = os.path.join(root, "labels")
    files = []
    for n in sorted(os.listdir(labels)):
        if n.startswith(sid + "."):
            data = open(os.path.join(labels, n), "rb").read()
            files.append({"name": n, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    os.makedirs(os.path.join(root, "locks"), exist_ok=True)
    with open(os.path.join(root, "locks", f"{sid}.json"), "w", encoding="utf-8") as f:
        json.dump({"session_id": sid, "files": files, "locked_at": locked_at}, f)


def _react(st, chat, grade, *, result="graded", conf="certain", t=None):
    graded = result == "graded"
    pos = graded and grade in ("direct", "likely")
    ev = {k: {"present": False} for k in ("nickname_called", "text_repeated", "direct_reference", "answer_form",
                                          "unique_content", "within_time_window", "same_topic")}
    body = {"chat_id": chat, "segment_id": st._seg_entity, "entry": "from_sample", "result": result,
            "grade": grade if graded else None, "eligible_grades": [],
            "grade_vs_conditions": "consistent" if graded else "not_applicable",
            "candidates": [{"utterance_id": st._utt, "evidence": ev}] if graded else [],
            "linked_utterance_id": st._utt if pos else None, "first_visible_ms": t,
            "first_visible_source": "chat_clock", "reaction_time_ms": (t + 1500) if pos else None,
            "confidence": conf, "rationale": "합성 근거",
            "sample_ref": {"sample_set_id": "S-0000abcd-d1-r1", "order": int(chat[-2:], 16)}}
    rec = st.append_label({"kind": "reaction", "body": body})
    if pos:
        for chk in ("first", "recheck"):
            st.append_label({"kind": "video_check", "body": {
                "target_kind": "reaction", "target_entity_id": rec["entity_id"], "check": chk,
                "window_start_ms": t - 5000, "window_end_ms": t + 20000, "watched_ranges": [[t - 5000, t + 20000]],
                "coverage_ratio": 1.0, "audio_heard": True}})
    return rec


def _base(st):
    s = st.append_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 600_000, "category": "just_chatting",
                                                      "confidence": "certain", "rationale": "합성 구간"}})
    st._seg_entity = s["entity_id"]
    u = st.append_label({"kind": "utterance", "body": {"segment_id": s["entity_id"], "start_ms": 100_000,
                                                        "end_ms": 102_000, "heard_text": "합성 받아쓰기",
                                                        "heard_text_source": "typed", "speaker": "streamer",
                                                        "modality": "verbal"}})
    st._utt = u["entity_id"]


@pytest.fixture(autouse=True)
def _clear_locks():
    FakeLock.held = set()
    yield
    FakeLock.held = set()


@pytest.fixture
def proj(tmp_path):
    """합성 프로젝트: 1차(P)·같은 사람 재라벨(RS) 잠김, 조정(AD) 기록 3건. 채팅 6개(모두 text)."""
    p = project.create(str(tmp_path / "projects"), "합성 방송", 777, "시험", "L01", "0.0.1", NOW, PID, SALT,
                       guide=project.GuideSource("0.1", b"# guide\n", {"reaction": {"time_window_sec": 20}}),
                       machine4="m3fa")
    root = p.root

    def verify(d):
        d["calibration"]["revs"][0]["status"] = "verified"
    project.open_project(root).update(verify, by="L01")
    msgs = [{"chat_id": cid(i), "t_ms": 100_000 + i * 10_000, "server_time_ms": i, "type_code": 1, "kind": "text",
             "status": "normal", "viewer_id": "vw_aaaaaaaaaaaa", "is_anonymous": False, "nickname_masked": "시**",
             "role": "common_user", "text": "합성 본문", "flags": {}, "raw_ref": f"f:1:{i}"} for i in range(1, 7)]
    jsonl.write_all(os.path.join(root, "chat", "messages.jsonl"), msgs)
    a = _store(root, P, "primary", 0)
    _base(a)
    pr = {i: _react(a, cid(i), g, t=100_000 + i * 10_000)
          for i, g in ((1, "direct"), (2, "likely"), (3, "likely"), (4, "related_only"), (5, "related_only"))}
    pr[6] = _react(a, cid(6), None, result="unconfirmed", t=160_000)
    a.close()
    b = _store(root, RS, "relabel_same", 1000)
    _base(b)
    rr = {i: _react(b, cid(i), g, t=100_000 + i * 10_000)
          for i, g in ((1, "direct"), (2, "related_only"), (3, "direct"), (4, "likely"), (5, "direct"))}
    b.close()
    _lock(root, P)
    _lock(root, RS)
    return {"root": root, "p": pr, "r": rr}


def _write_adjudications(proj):
    p, r = proj["p"], proj["r"]
    c = _store(proj["root"], AD, "adjudication", 3000)

    def case(i):
        return adj.Case(f"reaction:{cid(i)}", adj.TARGET_REACTION, adj.C_GRADE, None,
                        (adj._reaction_side(P, "L01", p[i]), adj._reaction_side(RS, "L01", r[i])), chat_id=cid(i))
    c.append_label({"kind": "adjudication", "body": adj.make_body(case(3), decision="final", cause="simple_mistake",
                                                                  pick=RS)})
    c.append_label({"kind": "adjudication", "body": adj.make_body(case(4), decision="exclude_from_eval",
                                                                  cause="guide_unclear")})
    c.append_label({"kind": "adjudication", "body": adj.make_body(case(5), decision="keep_as_ambiguous",
                                                                  cause="truly_ambiguous")})
    c.close()


def test_export_final_set_rows_and_zip(proj, tmp_path):
    _write_adjudications(proj)
    fx = export.build_final_set(proj["root"], relabel_session_ids=[RS])
    by = {it["row"]["chat_id"]: it for it in fx.items}
    assert by[cid(1)]["set"] == "final" and by[cid(1)]["row"]["source"] == "label"
    assert "human_disagreement" in by[cid(2)]["row"]["eval"]["excluded_reasons"]
    assert by[cid(2)]["row"]["eval"]["lenient"] is False
    r3 = by[cid(3)]["row"]
    assert r3["source"] == "adjudication" and r3["grade"] == "direct" and r3["session_id"] == RS
    assert r3["eval"]["strict"] is False                      # 두 번 달라 조정 값 → 엄격에서 뺌
    assert by[cid(3)]["report_reasons"] == [fs.R_ADJ_DISAGREEMENT]
    assert by[cid(4)]["set"] == "excluded" and "adjudicated_exclude" in by[cid(4)]["row"]["eval"]["excluded_reasons"]
    assert by[cid(5)]["set"] == "ambiguous" and by[cid(5)]["row"]["eval"]["lenient"] is False
    assert by[cid(6)]["row"]["result"] == "unconfirmed"

    out = str(tmp_path / "내보내기" / "final.zip")
    res = export.export_final_set(proj["root"], out, relabel_session_ids=[RS], clock=lambda: NOW,
                                  uuid_factory=lambda: "99999999-0000-4000-8000-000000000999", app_version="0.0.1")
    with zipfile.ZipFile(out) as z:
        names = sorted(z.namelist())
        rows = [json.loads(x) for x in z.read(export.JSONL_NAME).decode().splitlines() if x]
        amb = [json.loads(x) for x in z.read(export.AMBIGUOUS_NAME).decode().splitlines() if x]
        man = json.loads(z.read(export.MANIFEST_NAME))
        blob = b"".join(z.read(n) for n in z.namelist())
    assert names == sorted(export.FINAL_ZIP_NAMES)
    assert man["export_type"] == "ground_truth_lenient"
    chats = sorted(r["chat_id"] for r in rows if r["label_kind"] == "reaction")
    assert chats == [cid(1), cid(3), cid(6)]                  # 2 사람 불일치·4 제외·5 모호 → 빠짐
    assert [r["chat_id"] for r in amb] == [cid(5)]
    # 계단(처음 걸린 사유 하나): 2 사람 불일치 · 4 조정 제외 · 5 모호함으로 남김
    assert man["counts"]["excluded"] == {"human_disagreement": 1, "adjudicated_exclude": 1, "adjudicated_ambiguous": 1}
    assert man["counts"]["by_kind"] == {"segment": 1, "reaction": 3, "continuation": 0, "missed_candidate": 0}
    r3z = next(r for r in rows if r.get("chat_id") == cid(3))
    assert r3z["eval"] == {"in_boundary_buffer": False, "strict": False, "lenient": True, "excluded_reasons": []}
    r1z = next(r for r in rows if r.get("chat_id") == cid(1))
    assert r1z["eval"]["strict"] is True
    row_schema, man_schema = export._load_export_schema()
    assert man_schema.check(man) == []
    assert all(row_schema.check(r) == [] for r in rows + amb)
    assert "합성 본문".encode() not in blob and "시**".encode() not in blob
    assert res.rows == len(rows)


def test_final_set_refuses_unlocked_before_reading_labels(proj, monkeypatch):
    os.remove(os.path.join(proj["root"], "locks", f"{RS}.json"))
    called = []
    monkeypatch.setattr(ar, "load_locked_session", lambda *a, **k: called.append(a) or (_ for _ in ()).throw(AssertionError))
    with pytest.raises(ar.NotLockedError):
        export.build_final_set(proj["root"], relabel_session_ids=[RS])
    assert called == []


def test_final_set_refuses_labeling_reader(proj):
    for kind in ("primary", "relabel_same", "relabel_second", "origin_review", "analysis"):
        with pytest.raises(export.FinalSetBlindError):
            export.build_final_set(proj["root"], reader_kind=kind)
    export.build_final_set(proj["root"], relabel_session_ids=[RS], reader_kind="adjudication")


def test_full_labels_export_unchanged_names():
    assert export.ZIP_NAMES == ("labels.jsonl", "segments.csv", "reactions.csv", "missed.csv", "manifest.json")
