"""엔진 판끼리 견주기와 해제된 run 고르기 (17 6-1 4번, 17 5-2 3번) — app/ai_compare.py · app/analysis_flow.py.

1. ai_compare.diff_results · compare_runs: 두 판의 다른 곳(같은 채팅에 다른 등급 · 한쪽만 반응)만, 파일을 열지 않는다.
   compare_runs 는 두 run 모두 봉인 해제 기록을 먼저 본다.
2. analysis_flow.side_by_side: 해제된 run 이 여럿이면 고르기 목록(최신이 맨 앞·기본)과 읽기 함수(RunLoader)를 같이 준다.
3. analysis_flow.agreement_gate: 같은 1차·재라벨 쌍의 최신 일치도 보고서에서 사람 일치도 기준을 읽는다.

합성 프로젝트·합성 엔진 결과만 쓴다. 실제 채팅·닉네임·전사문은 쓰지 않는다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import ai_compare as ac
from app import analysis_flow as af
from app import export, seal
from tests.test_ai_compare import ai_doc, ai_rx, unsealed_run
from tests.test_final_set import P, RS, cid, proj  # noqa: F401

KST = timezone(timedelta(hours=9))
LATER = datetime(2026, 10, 30, 9, 0, 0, tzinfo=KST)        # 합성 잠금(10-24)보다 뒤


def doc(run_id, version, rx, video_no="777"):
    d = ai_doc(reactions=rx, video_no=video_no)
    d["run_id"] = run_id
    d["engine"] = {"name": "synthetic", "version": version, "preregistered": True}
    return d


def with_t(r, t):
    return {**r, "chat_t_ms": t}


def human_row(n, grade, *, source="label"):
    unc = grade == "unconfirmed"
    return {"label_kind": "reaction", "source": source, "effective": True, "conflict": False, "chat_id": cid(n),
            "result": "unconfirmed" if unc else "graded", "grade": None if unc else grade,
            "eval": {"strict": True, "lenient": True, "excluded_reasons": []}}


def pair_docs():
    """판 A(새 판)·B(옛 판). 채팅 1 같음 · 2 등급 다름 · 3 A만(B 미확인) · 4 A만(B 없음) · 5 B만(A 미확인) ·
    6 B만(A 없음) · 7 짝 없음(A 미확인, B 없음) · 8 같음(둘 다 미확인) · 9 A 의 graded+등급 없음(뺌)."""
    a = [ai_rx(1, "direct", 1), ai_rx(2, "likely", 2), ai_rx(3, "related_only", 3), ai_rx(4, "direct", 4),
         ai_rx(5, "unconfirmed"), ai_rx(7, "unconfirmed"), ai_rx(8, "unconfirmed"),
         {"chat_id": cid(9), "result": "graded", "grade": None}]
    b = [ai_rx(1, "direct", 1), ai_rx(2, "direct", 2), ai_rx(3, "unconfirmed"), ai_rx(5, "likely", 5),
         ai_rx(6, "unclear", 6), ai_rx(8, "unconfirmed"), ai_rx(9, "direct", 9)]
    a = [with_t(r, 10_000 * int(r["chat_id"][1:], 16)) for r in a]
    b = [with_t(r, 10_000 * int(r["chat_id"][1:], 16)) for r in b]
    return doc("run-new", "0.3.0", a), doc("run-old", "0.2.0", b)


# ---------------------------------------------------------------------------------------------- 1. 순수 계산
def test_diff_results_kinds_counts_and_table():
    a, b = pair_docs()
    d = ac.diff_results(a, b)
    kinds = {it["chat_id"]: it["kind"] for it in d["items"]}
    assert kinds == {cid(2): ac.RD_GRADE_DIFF, cid(3): ac.RD_A_ONLY, cid(4): ac.RD_A_ONLY,
                     cid(5): ac.RD_B_ONLY, cid(6): ac.RD_B_ONLY, cid(9): ac.RD_B_ONLY}
    c = d["counts"]
    assert (c["same"], c["grade_diff"], c["a_only"], c["b_only"], c["unpaired_no_reaction"]) == (2, 1, 2, 3, 1)
    assert d["a"]["graded_no_grade"] == 1 and d["b"]["graded_no_grade"] == 0     # 08 D-17 1: 뺀다
    assert d["a"]["rows"] == 7 and d["b"]["rows"] == 7
    assert d["a"]["run_id"] == "run-new" and d["b"]["engine"] == {"name": "synthetic", "version": "0.2.0"}
    assert d["table"]["likely"]["direct"] == 1 and d["table"]["absent"]["unclear"] == 1
    assert d["table"]["absent"]["direct"] == 1                                     # 9: A 쪽은 뺐으므로 없음
    assert sum(sum(r.values()) for r in d["table"].values()) == 9
    assert [it["t_ms"] for it in d["items"]] == sorted(it["t_ms"] for it in d["items"])
    assert d["human"] is None and all(it["closer"] is None for it in d["items"])
    # 같은 것을 뒤집으면 A만·B만이 바뀐다
    r = ac.diff_results(b, a)
    assert (r["counts"]["a_only"], r["counts"]["b_only"]) == (3, 2)


def test_diff_results_human_closer_and_adjudication_first():
    a, b = pair_docs()
    rows = [human_row(2, "likely"), human_row(2, "direct", source="adjudication"),   # 조정 값(직접)이 앞선다 → B
            human_row(3, "related_only"), human_row(5, "related_only"),             # 3 → A, 5 → 둘 다 다름
            {**human_row(6, "unclear"), "effective": False}]                        # 6 은 무효 → 사람 없음
    d = ac.diff_results(a, b, human=rows)
    closer = {it["chat_id"]: it["closer"] for it in d["items"]}
    assert closer[cid(2)] == "b" and closer[cid(3)] == "a" and closer[cid(5)] == "neither"
    assert closer[cid(4)] is None and closer[cid(6)] is None
    assert d["human"] == {"a_matches": 1, "b_matches": 1, "neither": 1, "no_human": 3}
    # dict 로 줘도 된다
    d2 = ac.diff_results(a, b, human={cid(4): "direct"})
    assert d2["human"]["a_matches"] == 1


def test_diff_results_chat_times_and_other_video_refused():
    a, b = pair_docs()
    d = ac.diff_results(a, b, chat_times={cid(6): 1})
    assert d["items"][0]["chat_id"] == cid(6) and d["items"][0]["t_ms"] == 1
    with pytest.raises(ac.AICompareError):
        ac.diff_results(a, doc("run-x", "0.1.0", [], video_no="999"))
    blob = json.dumps(d, ensure_ascii=False)
    assert "rationale" not in blob


def test_compare_runs_requires_both_unsealed():
    a, b = pair_docs()
    a["reactions"] = [r for r in a["reactions"] if r["chat_id"] != cid(9)]   # 1판 검사는 등급 없는 graded 를 거부한다
    ra, rb = unsealed_run(a), unsealed_run(b)
    rb["sha256"] = rb["sealed"]["sha256"] = rb["unsealed"]["sha256"] = "cd" * 32
    out = ac.compare_runs(ra, rb, final={"items": [{"set": "final", "row": human_row(3, "related_only")},
                                                   {"set": "excluded", "row": human_row(5, "related_only")},
                                                   {"set": "final", "row": {**human_row(2, "direct"),
                                                                            "eval": {"lenient": False}}}]})
    assert out["a"]["sha8"] == "ab" * 4 and out["b"]["sha8"] == "cd" * 4
    assert out["human"]["a_matches"] == 1 and out["human"]["no_human"] == 5      # 빠진 행·평가 밖 행은 사람 판정 아님
    sealed_only = unsealed_run(b)
    sealed_only.pop("unsealed")
    with pytest.raises(ac.RunNotUnsealedError):
        ac.compare_runs(ra, sealed_only)
    with pytest.raises(ac.AICompareError):
        ac.compare_runs(ra, unsealed_run(a))                                        # 같은 run 끼리


# ---------------------------------------------------------------------------------------------- 2. 고르기·읽기
def _seal_two(root):
    """옛 판을 먼저, 새 판을 나중에 푼다."""
    old = [ai_rx(1, "direct", 111_500), ai_rx(2, "likely", 121_500), ai_rx(3, "unconfirmed")]
    new = [ai_rx(1, "direct", 111_500), ai_rx(2, "direct", 121_500), ai_rx(3, "likely", 131_500)]
    ids = []
    for i, (rid, ver, rx) in enumerate((("run-old", "0.2.0", old), ("run-new", "0.3.0", new))):
        data = json.dumps(doc(rid, ver, rx), ensure_ascii=False).encode("utf-8")
        e = seal.seal_bytes(root, data, source="file", by="L01", now=datetime(2026, 10, 23, 9, i, tzinfo=KST))
        seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험", now=LATER + timedelta(hours=i))
        ids.append(rid)
    return ids


def test_side_by_side_choices_latest_first_and_loader(proj, monkeypatch):  # noqa: F811
    root = proj["root"]
    _seal_two(root)
    calls = []
    real = export.build_final_set
    monkeypatch.setattr(export, "build_final_set", lambda *a, **k: calls.append(1) or real(*a, **k))
    out = af.side_by_side(root, reader_kind="analysis")
    assert out.unsealed_runs == ["run-old", "run-new"] and out.run_id == "run-new"
    assert [c.run_id for c in out.choices] == ["run-new", "run-old"]
    assert out.choices[0].latest and not out.choices[1].latest and "최신" in out.choices[0].label
    assert out.run["engine"]["version"] == "0.3.0"
    other = out.load_run("run-old")
    assert other["run"]["engine"]["version"] == "0.2.0" and other["scores"] is not None
    assert out.load_run("run-old") is other                       # 한 번 읽은 판은 기억한다
    assert calls == [1]                                            # 최종 세트는 한 번만 만든다
    with pytest.raises(ValueError):
        out.load_run("run-none")
    d = out.load_run.compare_runs("run-new", "run-old")
    assert {it["chat_id"]: it["kind"] for it in d["items"]} == {cid(2): ac.RD_GRADE_DIFF, cid(3): ac.RD_A_ONLY}
    # 옛 판을 골라 열 수도 있다
    out2 = af.side_by_side(root, reader_kind="analysis", run_id="run-old")
    assert out2.run_id == "run-old" and out2.choices[0].run_id == "run-new"


def test_loader_refuses_blind_seats_and_not_unsealed(proj):  # noqa: F811
    root = proj["root"]
    _seal_two(root)
    out = af.side_by_side(root, reader_kind="analysis")
    loader = af.RunLoader(root, reader_kind="primary", run_ids=["run-old"], fx=None)
    with pytest.raises(PermissionError):
        loader("run-old")
    ghost = af.RunLoader(root, reader_kind="analysis", run_ids=["run-ghost"], fx=out.load_run.fx)
    with pytest.raises(ac.RunNotUnsealedError):                   # 목록에 있어도 해제 기록이 없으면 열지 않는다
        ghost("run-ghost")


def test_run_choices_dedup_same_run_unsealed_twice():
    from types import SimpleNamespace as NS
    st = NS(entries=[NS(unsealed_at="2026-10-30T09:00:00+09:00", run_id="r1", presealed=True),
                     NS(unsealed_at="2026-10-30T10:00:00+09:00", run_id="r2", presealed=False),
                     NS(unsealed_at="2026-10-30T11:00:00+09:00", run_id="r1", presealed=True),
                     NS(unsealed_at=None, run_id=None, presealed=None)])
    assert af.unsealed_run_ids(st) == ["r2", "r1"]
    ch = af.run_choices(st)
    assert [c.run_id for c in ch] == ["r1", "r2"] and ch[0].latest
    assert "사전 봉인 아님" in ch[1].label and "2026-10-30 10:00" in ch[1].label


# ---------------------------------------------------------------------------------------------- 3. 사람 일치도 기준
def _write_agreement(reports, pid, report_id, created_at, sessions, verdicts):
    d = os.path.join(reports, pid, report_id)
    os.makedirs(d)
    gates = [{"key": k, "verdict": v} for k, v in verdicts.items()]
    body = {"report_type": "agreement", "report_id": report_id, "created_at": created_at,
            "header": {"sessions": [{"session_id": s} for s in sessions]}, "gates": gates}
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as f:
        json.dump(body, f)


def test_agreement_gate_latest_matching_pair(tmp_path):
    reports, pid = str(tmp_path / "reports"), "11111111-2222-4333-8444-666666666666"
    info = {"primary_session_id": P, "relabel_session_ids": [RS], "project": {"project_id": pid}}
    assert af.agreement_gate(str(tmp_path), info, reader_kind="analysis", reports_root=reports) == (None, None)
    passing = {"binary_kappa": "pass", "grade_kappa_w": "pass_point_only", "segment_kappa": "uncertain",
               "boundary_median_sec": "pass"}
    _write_agreement(reports, pid, "R-old", "2026-10-30T09:00:00+09:00", [P, RS], passing)
    _write_agreement(reports, pid, "R-new", "2026-10-31T09:00:00+09:00", [P, RS],
                     {**passing, "grade_kappa_w": "fail"})
    _write_agreement(reports, pid, "R-other", "2026-11-01T09:00:00+09:00", [P, "L02-RS-20261028-ffff"], passing)
    os.makedirs(os.path.join(reports, pid, "broken"))
    with open(os.path.join(reports, pid, "broken", "report.json"), "wb") as f:
        f.write(b"{not json")
    gate, rid = af.agreement_gate(str(tmp_path), info, reader_kind="analysis", reports_root=reports)
    assert rid == "R-new" and gate == {"reaction": False, "segment": None}
    os.remove(os.path.join(reports, pid, "R-new", "report.json"))
    gate, rid = af.agreement_gate(str(tmp_path), info, reader_kind=None, reports_root=reports)
    assert rid == "R-old" and gate == {"reaction": True, "segment": None}
    # 보고서를 읽지 않는 자리는 폴더를 열지 않는다
    assert af.agreement_gate(str(tmp_path), info, reader_kind="primary", reports_root=reports) == (None, None)
    assert af.agreement_gate(str(tmp_path), {**info, "relabel_session_ids": []}, reader_kind=None,
                             reports_root=reports) == (None, None)


def test_side_by_side_reads_gate_from_report(proj, monkeypatch):  # noqa: F811
    root = proj["root"]
    _seal_two(root)
    real = export.build_final_set
    monkeypatch.setattr(export, "build_final_set", lambda *a, **k: real(*a, relabel_session_ids=[RS], **k))
    from app.project import open_project
    pid = open_project(root).project_id
    reports = os.path.join(os.path.dirname(os.path.dirname(root)), "reports")    # projects 옆 (03 2-2)
    _write_agreement(reports, pid, "R-gate", "2026-10-30T09:00:00+09:00", [P, RS],
                     {"binary_kappa": "fail", "grade_kappa_w": "pass"})
    seen = []
    real_cmp = ac.compare
    monkeypatch.setattr(ac, "compare", lambda run, fx, **k: seen.append(k.get("human_gate")) or real_cmp(run, fx, **k))
    out = af.side_by_side(root, reader_kind="analysis")
    assert out.gate_report_id == "R-gate" and out.human_gate == {"reaction": False, "segment": None}
    assert seen == [{"reaction": False, "segment": None}]
    assert out.scores["strict"].get("kappa_w") == "held"            # 기준 미달 → 엄격 보류 (06 6-5)
    assert any("R-gate" in w for w in out.scores["warnings"])
    out.load_run("run-old")
    assert seen[-1] == {"reaction": False, "segment": None}         # 다른 판도 같은 기준
