"""여섯째 묶음 일치도: 놓친 후보는 재라벨 둘 사이에서(08 D-13 3), 반응이 없어진 놓친 후보 빼기(14 4-2 의 4번),
재라벨 잠금의 현재 표본(4-2 의 5번), 보고서 채팅 시각(4-2 의 6번), 출처 대상 파일 머리(D-13 5).

합성 데이터만 쓴다 — chat_id·본문·근거는 지어낸 표식 문자열이다. 실제 프로젝트 폴더에는 쓰지 않는다(tmp_path).
"""
import json
import os
import time

import pytest

from app import agreement_report as ar
from app import jsonl, locking
from app.core import agreement as ag
from app.core import origin as og
from app.core import sampling as sp
from tests.test_agreement_ext import (NOW, RATIONALE_SECRET, NICK_SECRET, RR, M, O, _lines, _missed_sets,  # noqa: F401
                                      _origin_sets, _seg, _write_and_lock, cid, proj, uid)

SA_RS = "L01-RS-20261021-ab12"
SB_R2 = "L02-R2-20261021-cd34"
SA_P = "L01-P-20261021-ab12"
BODY_SECRET = "합성본문표식여섯째"


def _rx_unconf(c, eid, t):
    return RR(c, "unconfirmed", eid=eid, t=t, entry="from_sample")


def _relabel_pair(root, *, base=100, extra_a=(), extra_b=()):
    """재라벨 둘(RS·R2). 놓친 후보 골든(_missed_sets)과 그 채팅의 ③ 반응(미확인 — 06 2-9 대상)을 둘 다 넣는다."""
    seg = [_seg(0, 1200, "just_chatting")]
    ma, mb = _missed_sets(base=base)
    ra = [_rx_unconf(m["body"]["chat_id"], uid(11, 500 + i), m["body"]["first_visible_ms"])
          for i, m in enumerate(ma) if m["body"]["scope"] == "sample"]
    rb = [_rx_unconf(m["body"]["chat_id"], uid(22, 500 + i), m["body"]["first_visible_ms"]) for i, m in enumerate(mb)]
    _write_and_lock(root, SA_RS, _lines(SA_RS, "relabel_same", seg + ra + list(extra_a) + ma, 0))
    _write_and_lock(root, SB_R2, _lines(SB_R2, "relabel_second", seg + rb + list(extra_b) + mb, 50_000))
    return ma, mb


def _add_current_sample(root, sid, chat_ids, *, tamper=False):
    p = os.path.join(root, "locks", f"{sid}.json")
    with open(p, encoding="utf-8") as f:
        doc = json.load(f)
    cs = locking.current_sample_doc(chat_ids)
    if tamper:
        cs["chat_ids_sha256"] = "0" * 64
    doc["current_sample"] = cs
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def _write_messages(root, rows):
    p = os.path.join(root, "chat", "messages.jsonl")
    if os.path.exists(p):
        os.remove(p)                                   # 시험 사본(tmp_path)의 합성 채팅을 바꿔 끼운다
    jsonl.write_all(p, rows)


# ============================================================================ 1. 놓친 후보 빼기 (14 4-2 의 4번)
def test_drop_missed_stale_counts_no_reaction_and_not_target_separately():
    X, Y, Z, W = cid(1), cid(2), cid(3), cid(4)
    missed = [M(X, False, "C"), M(Y, False, "C"), M(Z, False, "C"), M(W, False, "C"), M(cid(5), True, scope="extra")]
    reacts = [RR(Y, "direct", eid=uid(1, 1), t=0),                                         # 직접으로 고침 → not_target
              RR(Z, "unclear", eid=uid(1, 2), t=0),                                        # 대상 → 남김
              RR(W, "direct", eid=uid(1, 3), t=0), RR(W, "direct", eid=uid(1, 4), t=0)]   # 둘 이상 → 남김
    keep, cnt = ag.drop_missed_stale(missed, reacts)
    assert cnt == {"not_target": 1, "no_reaction": 1}                                      # X: 반응이 없어짐
    assert [m["body"]["chat_id"] for m in keep] == [Z, W, cid(5)]
    # 앞 판 함수는 그대로(반응 없음은 남김) — 하위 호환
    keep_old, n_old = ag.drop_missed_not_target(missed, reacts)
    assert n_old == 1 and len(keep_old) == 4


def test_build_agreement_reports_dropped_no_reaction():
    ma, mb = _missed_sets()
    ra = [_rx_unconf(m["body"]["chat_id"], uid(11, i), 0) for i, m in enumerate(ma[:10])]   # 앞 10 채팅만 ③ 이 남음
    rb = [_rx_unconf(m["body"]["chat_id"], uid(22, i), 0) for i, m in enumerate(mb)]
    side = {"segments": [], "scope": None}
    res = ag.build_agreement({**side, "session_id": SA_RS, "session_kind": "relabel_same", "labeler_id": "L01",
                              "reactions": ra, "missed": ma},
                             {**side, "session_id": SB_R2, "session_kind": "relabel_second", "labeler_id": "L02",
                              "reactions": rb, "missed": mb})
    m = res["missed"]
    assert m["dropped_no_reaction"] == {"a": 11, "b": 0} and m["dropped_not_target"] == {"a": 0, "b": 0}
    assert m["judged"]["both"] == 10 and m["pair_applicable"] is True and m["pair_note"] is None
    assert any("반응을 물러" in s and "06 2-9" in s for s in res["interpretation"])


# ============================================================================ 2. 놓친 후보 짝 (08 D-13 3)
def test_missed_pair_applicable_only_between_relabels():
    assert ag.missed_pair_applicable("relabel_same", "relabel_second") is True
    assert ag.missed_pair_applicable("relabel_second", "relabel_second") is True
    assert ag.missed_pair_applicable("primary", "relabel_second") is False
    assert ag.missed_pair_applicable("relabel_same", "primary") is False
    assert ag.missed_pair_applicable(None, None) is True                     # 종류 모름(시험 입력) → 앞 판처럼


def test_report_relabel_pair_has_missed_kappa_golden(proj):
    root = proj["root"]
    _relabel_pair(root)
    res = ar.build_report(root, SA_RS, SB_R2, now=NOW, created_by="L01")
    ms = res.report["missed"]
    assert ms["pair_applicable"] is True
    # 골든(test_agreement_ext 와 같은 20 짝): κ = 31/51, AC1 = 129/149, 사유 코드 κ = 46/67
    assert ms["candidate"]["kappa"] == pytest.approx(31 / 51) and ms["candidate"]["ac1"] == pytest.approx(129 / 149)
    assert ms["reason_code"]["kappa"] == pytest.approx(46 / 67)
    assert ms["dropped_no_reaction"] == {"a": 0, "b": 0}
    md = res.markdown
    assert "후보 여부 n=20" in md and "1차는 ④ 없음" not in md
    # 앞 판 잠금(current_sample 없음) → 구간표 근사 + 경고 1줄씩
    assert sum("current_sample" in w and "08 D-12 2" in w for w in res.warnings) == 2
    assert res.report["header"]["current_sample_method"] == {"a": "segments", "b": "segments"}


def test_report_primary_pair_has_one_line_for_missed(proj):
    root = proj["root"]
    seg = [_seg(0, 600, "just_chatting")]
    _write_and_lock(root, SA_P, _lines(SA_P, "primary", seg, 0))
    _, mb = _missed_sets()
    _write_and_lock(root, SB_R2, _lines(SB_R2, "relabel_second", seg + mb, 50_000))
    res = ar.build_report(root, SA_P, SB_R2, now=NOW)
    assert res.report["missed"]["pair_applicable"] is False
    lines = [ln for ln in res.markdown.splitlines() if "④" in ln]
    assert len(lines) == 1 and "1차는 ④ 없음(D-12 5)" in lines[0] and "08 D-13 3" in lines[0]
    assert "후보 여부 n=" not in res.markdown


# ============================================================================ 3. 잠금의 현재 표본 (14 4-2 의 5번)
def _summary(sid, kind):
    return locking.LockSummary(sid, kind, 1, 1, 0, {k: 0 for k in locking.COUNT_KINDS},
                               {"active": 0, "retracted": 0, "conflict": 0, "invalid": 0})


def _doc(sid, kind, current_sample=None):
    return locking.build_lock_doc(
        session_id=sid, project_id="11111111-2222-4333-8444-555555555555", labeler_id=sid.split("-")[0],
        session_kind=kind, locked_at="2026-10-21T10:00:00.000+09:00", locked_on_machine4="aaaa", app_version="0.0.1",
        guide={"guide_version": "0.1", "guide_doc_sha256": "ab" * 32, "thresholds_sha256": "ab" * 32},
        relabel_plan_id="RP-20261021-ab12" if kind != "primary" else None,
        files=[{"name": f"{sid}.aaaa.jsonl", "machine4": "aaaa", "size": 1, "sha256": "cd" * 32, "lines": 1,
                "last_seq": 1}],
        summary=_summary(sid, kind), current_sample=current_sample)


def test_lock_doc_current_sample_schema_and_rules():
    ids = [cid(3), cid(1), cid(2), cid(1)]
    d = _doc(SB_R2, "relabel_second", ids)
    cs = d["current_sample"]
    assert cs["chat_ids"] == [cid(1), cid(2), cid(3)] and cs["count"] == 3
    assert cs["chat_ids_sha256"] == sp.ids_sha256([cid(1), cid(2), cid(3)])
    assert locking.check_lock_doc(d) == []
    assert locking.lock_current_sample(d) == [cid(1), cid(2), cid(3)]
    # 칸이 없으면(앞 판·1차) None, 스키마도 통과
    plain = _doc(SB_R2, "relabel_second")
    assert "current_sample" not in plain and locking.check_lock_doc(plain) == []
    assert locking.lock_current_sample(plain) is None
    # 1차에는 넣지 않는다
    with pytest.raises(ValueError):
        _doc(SA_P, "primary", [cid(1)])
    bad = dict(_doc(SA_P, "primary"), current_sample=cs)
    assert any("재라벨 세션 잠금에만" in p for p in locking.check_lock_doc(bad))
    # 해시·개수·정렬이 어긋나면 문제
    t = dict(d, current_sample=dict(cs, chat_ids_sha256="0" * 64))
    assert any("chat_ids_sha256" in p for p in locking.check_lock_doc(t))
    with pytest.raises(ValueError):
        locking.lock_current_sample(t)
    u = dict(d, current_sample=dict(cs, chat_ids=[cid(2), cid(1), cid(3)]))
    assert any("정렬" in p for p in locking.check_lock_doc(u))
    assert any("count" in p for p in locking.check_lock_doc(dict(d, current_sample=dict(cs, count=4))))
    assert locking.check_lock_doc(dict(d, current_sample=dict(cs, chat_ids=["본문"])))         # 형식 아님
    with pytest.raises(ValueError):
        locking.current_sample_doc(["not-a-chat-id"])


def test_report_uses_lock_current_sample(proj):
    root = proj["root"]
    extra = [RR(cid(1), "direct", eid=uid(11, 1), t=60_000, entry="from_sample")]
    extra_b = [RR(cid(1), "direct", eid=uid(22, 1), t=60_000, entry="from_sample")]
    ma, mb = _relabel_pair(root, extra_a=extra, extra_b=extra_b)
    keep_b = sorted({m["body"]["chat_id"] for m in mb})              # B 의 표본에는 cid(1) 이 없다 → B 쪽에서 빠짐
    _add_current_sample(root, SB_R2, keep_b)
    res = ar.build_report(root, SA_RS, SB_R2, now=NOW)
    rep = res.report
    assert rep["header"]["current_sample_method"] == {"a": "segments", "b": "given"}
    assert rep["samples"]["dropped_from_sample"]["b"] == 1 and rep["samples"]["dropped_from_sample"]["a"] == 0
    assert rep["inputs"]["sessions"][1]["current_sample_sha256"] == sp.ids_sha256(keep_b)
    assert rep["inputs"]["sessions"][0]["current_sample_sha256"] is None
    assert sum("current_sample" in w for w in res.warnings) == 1                # A 만 근사 경고
    # 해시가 목록과 다르면 거부(잠금 뒤 고쳐짐)
    _add_current_sample(root, SB_R2, keep_b, tamper=True)
    with pytest.raises(ar.LockMismatchError):
        ar.build_report(root, SA_RS, SB_R2, now=NOW)


# ============================================================================ 4. 채팅 시각 (14 4-2 의 6번)
def test_report_chat_times_lookup_is_used_and_gets_only_ids(proj):
    root = proj["root"]
    _relabel_pair(root)
    calls = []

    def lookup(ids):
        calls.append(ids)
        return {cid(103): 777_000, "c" + "f" * 20: 1}                 # 묻지 않은 채팅은 무시된다

    res = ar.build_report(root, SA_RS, SB_R2, now=NOW, chat_times=lookup)
    assert len(calls) == 1 and isinstance(calls[0], frozenset)
    assert all(isinstance(c, str) and c.startswith("c") for c in calls[0])  # 블라인드: chat_id 만 넘긴다
    assert cid(103) in calls[0] and len(calls[0]) == 22
    dis = {d["chat_id"]: d["t_ms"] for d in res.report["missed"]["disagreements"]}
    assert dis[cid(103)] == 777_000 and dis[cid(104)] == 4000            # 모르는 것은 라벨의 첫 표시 시각
    h = res.report["header"]["chat_times"]
    assert h == {"source": "lookup", "requested": 22, "missing": 21}
    assert any("시각을 찾지 못한 채팅 21건" in w for w in res.warnings)


def test_report_chat_times_falls_back_to_messages_scan(proj):
    root = proj["root"]
    _relabel_pair(root)
    _write_messages(root, [{"chat_id": cid(103), "t_ms": 888_000, "text": BODY_SECRET, "nickname_masked": NICK_SECRET},
                           {"chat_id": cid(104), "t_ms": 999_000, "text": BODY_SECRET},
                           {"chat_id": cid(9999), "t_ms": 5, "text": BODY_SECRET}])
    res = ar.build_report(root, SA_RS, SB_R2, now=NOW)
    dis = {d["chat_id"]: d["t_ms"] for d in res.report["missed"]["disagreements"]}
    assert dis[cid(103)] == 888_000 and dis[cid(104)] == 999_000
    assert res.report["header"]["chat_times"] == {"source": "messages_scan", "requested": 22, "missing": 20}
    blob = json.dumps(res.report, ensure_ascii=False) + res.markdown
    for s in (BODY_SECRET, NICK_SECRET, RATIONALE_SECRET):
        assert s not in blob


def test_scan_chat_times_skips_bad_lines_and_stops(tmp_path):
    p = tmp_path / "messages.jsonl"
    rows = [b'{"chat_id": "%s", "t_ms": 10}' % cid(1).encode(), b"{broken", b'{"chat_id": "%s", "t_ms": -1}' % cid(2).encode(),
            b'{"chat_id": "%s", "t_ms": 1.5}' % cid(3).encode(), b'{"chat_id": "%s", "t_ms": 30}' % cid(4).encode()]
    p.write_bytes(b"\xef\xbb\xbf" + b"\n".join(rows) + b"\n")
    assert ar.scan_chat_times(str(p), {cid(1), cid(2), cid(3), cid(4)}) == {cid(1): 10, cid(4): 30}
    assert ar.scan_chat_times(str(p), set()) == {}
    assert ar.scan_chat_times(str(tmp_path / "없음.jsonl"), {cid(1)}) == {}


def test_scan_chat_times_200k_lines_is_fast(tmp_path):
    """02 성능 목표(채팅 20만 건): 대체 길 한 번이 몇 초 안. 숫자는 보고에만 쓴다."""
    p = tmp_path / "messages.jsonl"
    with open(p, "wb") as f:
        for i in range(200_000):
            f.write(('{"chat_id": "%s", "t_ms": %d, "kind": "text", "text": "합성 %d"}\n' % (cid(i), i * 50, i)).encode())
    want = {cid(i) for i in range(0, 200_000, 200)}                       # 1000건, 끝까지 훑어야 한다
    t0 = time.perf_counter()
    got = ar.scan_chat_times(str(p), want)
    dt = time.perf_counter() - t0
    assert len(got) == 1000 and got[cid(199_800)] == 199_800 * 50
    assert dt < 15.0


# ============================================================================ 5. 출처 대상 파일 머리 (08 D-13 5)
def test_origin_report_header_has_target_ids_and_sha(proj):
    root = proj["root"]
    oa, ob = _origin_sets()
    sa, sb = "L01-OR-20261021-aa11", "L02-OR-20261021-bb22"
    _write_and_lock(root, sa, _lines(sa, "origin_review", oa, 0))
    _write_and_lock(root, sb, _lines(sb, "origin_review", ob, 50_000))
    # 대상 파일이 없으면 경고
    res0 = ar.build_report(root, sa, sb, now=NOW)
    assert res0.report["header"]["origin_targets"] == [] and any("08 D-13 5" in w for w in res0.warnings)
    t = og.build_targets(chat_ids=[cid(i) for i in range(1, 13)], created_at="2026-10-21T15:00:00+09:00",
                         created_by="L01", basis="sample_items")
    d = os.path.join(root, "origin")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{t['target_id']}.json"), "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False)
    with open(os.path.join(d, "OT-20261021-zzzz.json"), "w", encoding="utf-8") as f:
        f.write("{깨진")
    res = ar.build_report(root, sa, sb, now=NOW)
    h = res.report["header"]
    assert h["origin_targets"] == [{"target_id": t["target_id"], "chat_ids_sha256": t["chat_ids_sha256"],
                                    "count": 12, "basis": "sample_items"}]
    assert res.report["inputs"]["data"]["origin_targets"] == h["origin_targets"]
    assert any("읽지 못한 출처 판정 대상 파일 1개" in w for w in res.warnings)
    assert t["target_id"] in res.markdown and t["chat_ids_sha256"] in res.markdown and "08 D-13 5" in res.markdown
    assert res.report["origin"]["kappa"] == pytest.approx(19 / 27)          # 계산은 그대로
    # 라벨링 쌍 보고서 머리에는 출처 대상 칸이 없다
    seg = [_seg(0, 600, "just_chatting")]
    _write_and_lock(root, SA_P, _lines(SA_P, "primary", seg, 0))
    _write_and_lock(root, SB_R2, _lines(SB_R2, "relabel_second", seg, 60_000))
    lab = ar.build_report(root, SA_P, SB_R2, now=NOW).report
    assert "origin_targets" not in lab["header"] and "origin_targets" not in lab["inputs"]["data"]
