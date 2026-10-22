"""app/ai_compare.py · tools/ai_compare.py — 06 6-3 매칭, 6-4 E1·E2·E3, 6-5 엄격·참고, 6-6 계단표, 6-7 보고서, 7장 블라인드.

합성 데이터만 쓴다. 기대값은 손으로 계산했다(식은 주석에).
"""
import hashlib
import json
import os

import pytest

from app import agreement_report as ar
from app import ai_compare as ac
from app import seal
from tests.test_final_set import (AD, NOW, P, RS, _clear_locks, _write_adjudications, cid, proj)  # noqa: F401

SHA = "ab" * 32


def unsealed_run(result, **kw):
    return {"result": result, "sha256": SHA,
            "sealed": {"kind": "ai.sealed", "sha256": SHA, "at": "2026-10-23T09:00:00+09:00", "check": "valid",
                       "size": 1},
            "unsealed": {"kind": "ai.unsealed", "sha256": SHA, "run_id": result["run_id"],
                         "at": "2026-11-01T09:00:00+09:00", "conditions_met": True, "locks": [], **kw}}


def ai_doc(reactions=None, segments=None, video_no="777"):
    d = {"schema": "chatcoach/ai-result", "schema_version": 1, "run_id": "run-synth-a",
         "created_at": "2026-10-23T08:00:00+09:00",
         "engine": {"name": "synthetic", "version": "0.0.1", "preregistered": True},
         "target": {"platform": "chzzk", "video_no": video_no, "chat_id_algorithm": "sl-chat-id-v1",
                    "messages_sha256": None},
         "time_axis": "vod_ms"}
    if reactions is not None:
        d["reactions"] = reactions
    if segments is not None:
        d["segments"] = segments
    return d


def ai_rx(n, grade, t=None):
    if grade == "unconfirmed":
        return {"chat_id": cid(n), "result": "unconfirmed", "grade": None, "reaction_time_ms": None}
    return {"chat_id": cid(n), "result": "graded", "grade": grade, "reaction_time_ms": t}


def row(n, grade, *, conf="certain", reasons=(), entry="from_sample", t=None):
    unconf = grade == "unconfirmed"
    return {"label_kind": "reaction", "chat_id": cid(n), "chat_t_ms": n * 1000, "entry": entry,
            "sample_set_id": "S-0000abcd-d1-r1" if entry == "from_sample" else None,
            "result": "unconfirmed" if unconf else "graded", "grade": None if unconf else grade,
            "confidence": conf, "reaction_time_ms": t,
            "eval": {"strict": not reasons, "lenient": True, "excluded_reasons": list(reasons)}}


def item(r, st="final", report=()):
    return {"set": st, "row": r, "report_reasons": list(report)}


# 손계산 표 (사람, AI)
#  1 d/d  2 d/l  3 l/l  4 l/r  5 r/r  6 u/u  7 u/d  9 r/불명확   → 엄격 8건
#  8 d/u(아마도)  10 d/d(두 번 달라 조정 값)                   → 참고에만
#  11 AI 없음 · 12 후원 · 13 조정 제외 · 14 모호함 · 15 사람 불명확 · 16 사람 불일치 · 17 판 충돌
def golden_final():
    items = [
        item(row(1, "direct", t=100_000)), item(row(2, "direct", t=200_000)), item(row(3, "likely", t=300_000)),
        item(row(4, "likely")), item(row(5, "related_only")), item(row(6, "unconfirmed")),
        item(row(7, "unconfirmed")),
        item(row(8, "direct", conf="probably", reasons=["confidence_probably"])),
        item(row(9, "related_only")),
        item(row(10, "direct"), report=["adjudicated_disagreement"]),
        item(row(11, "likely")),
        item(row(12, "direct", reasons=["non_text_message"])),
        item(row(13, "direct", reasons=["adjudicated_exclude"]), st="excluded"),
        item(row(14, "direct"), st="ambiguous", report=["adjudicated_ambiguous"]),
        item(row(15, "unclear", reasons=["grade_unclear"])),
        item(row(16, "direct", reasons=["human_disagreement"])),
        # E2 (발화 출발)
        item(row(18, "direct", entry="from_utterance")), item(row(19, "likely", entry="from_utterance")),
        item(row(20, "direct", entry="from_utterance")), item(row(21, "related_only", entry="from_utterance")),
    ]
    conflicts = [{"chat_id": cid(17), "entry": "from_sample", "sample_set_id": "S-0000abcd-d1-r1"}]
    return {"items": items, "segment_items": [], "conflicts": conflicts}


def golden_ai(**kw):
    rx = [ai_rx(1, "direct", 101_000), ai_rx(2, "likely", 203_000), ai_rx(3, "likely", 306_000),
          ai_rx(4, "related_only"), ai_rx(5, "related_only"), ai_rx(6, "unconfirmed"), ai_rx(7, "direct", 7000),
          ai_rx(8, "unconfirmed"), ai_rx(9, "unclear"), ai_rx(10, "direct", 10_000), ai_rx(12, "direct", 1),
          ai_rx(13, "direct", 1), ai_rx(14, "direct", 1), ai_rx(15, "likely", 1), ai_rx(16, "direct", 1),
          ai_rx(17, "direct", 1), ai_rx(18, "likely", 1), ai_rx(19, "unconfirmed"), ai_rx(21, "direct", 1),
          ai_rx(30, "direct", 1), ai_rx(1, "unconfirmed")]          # 마지막 줄은 중복(첫 줄만)
    return ai_doc(reactions=rx, **kw)


def steps(c):
    return {r["step"]: (r["removed"], r["remaining"]) for r in c}


def test_e1_cascade_strict_and_lenient_golden():
    out = ac.compare(unsealed_run(golden_ai()), golden_final(), human_gate=True)
    e1 = out["e1"]
    assert e1["population"] == 17
    s = steps(e1["cascade"])
    assert s["start"] == (0, 17)
    assert s["non_text_message"] == (1, 16) and s["ai_unevaluated"] == (1, 15) and s["conflict_unresolved"] == (1, 14)
    assert s["grade_unclear"] == (1, 13) and s["adjudicated_exclude"] == (1, 12)
    assert s["adjudicated_ambiguous"] == (1, 11) and s["human_disagreement"] == (1, 10)
    assert s["adjudicated_disagreement"] == (1, 9) and s["confidence_probably"] == (1, 8)
    assert s["agreement_below_gate"] == (0, 8)
    marks = {r["step"]: r.get("mark") for r in e1["cascade"]}
    assert marks["human_disagreement"] == "lenient_target" and marks["agreement_below_gate"] == "strict_target"

    st = e1["strict"]
    assert st["status"] == ac.STATUS_OK and st["n"] == 8
    g = st["grade"]
    assert g["n"] == 7 and g["ai_unclear"] == 1
    assert g["exact"] == pytest.approx(4 / 7) and g["one_step"] == pytest.approx(2 / 7)
    assert g["two_plus"] == pytest.approx(1 / 7)
    # p_o^w = 16/21, p_e^w = 86/147 → κ_w = 26/61
    assert g["p_o_w"] == pytest.approx(16 / 21) and g["p_e_w"] == pytest.approx(86 / 147)
    assert g["kappa_w"] == pytest.approx(26 / 61)
    assert g["confusion"]["rows"][0] == [1, 1, 0, 0, 0]          # 사람 직접: AI 직접 1, 높음 1
    assert g["confusion"]["rows"][2] == [0, 0, 1, 0, 1]          # 사람 관련: AI 관련 1, 불명확 1
    b = st["binary"]
    assert (b["tp"], b["fp"], b["fn"], b["tn"]) == (3, 1, 1, 3)
    assert b["precision"] == pytest.approx(0.75) and b["recall"] == pytest.approx(0.75) and b["f1"] == pytest.approx(0.75)
    assert b["kappa"] == pytest.approx(0.5) and b["ac1"] == pytest.approx(0.5) and b["human_prevalence"] == 0.5
    t = st["reaction_time"]
    assert t["n"] == 3 and t["median_sec"] == pytest.approx(3.0)
    assert t["within"] == {"2s": pytest.approx(1 / 3), "5s": pytest.approx(2 / 3)}

    le = e1["lenient"]
    assert le["n"] == 10 and le["grade"]["n"] == 9
    assert le["grade"]["exact"] == pytest.approx(5 / 9) and le["grade"]["two_plus"] == pytest.approx(2 / 9)
    lb = le["binary"]
    assert (lb["tp"], lb["fp"], lb["fn"], lb["tn"]) == (4, 1, 2, 3)
    assert lb["precision"] == pytest.approx(0.8) and lb["recall"] == pytest.approx(4 / 6)
    assert lb["f1"] == pytest.approx(8 / 11) and lb["kappa"] == pytest.approx(0.4)

    assert e1["reference"]["human_unclear"]["likely"] == 1
    assert e1["reference"]["adjudicated_ambiguous"]["direct"] == 1
    dis = {d["chat_id"]: d for d in e1["disagreements"]}
    assert set(dis) == {cid(2), cid(4), cid(7), cid(8), cid(9)}
    assert dis[cid(8)]["strict"] is False and dis[cid(7)]["strict"] is True


def test_e2_e3_golden():
    out = ac.compare(unsealed_run(golden_ai()), golden_final(), human_gate=True)
    e2 = out["e2"]
    assert e2["population"] == 3
    assert steps(e2["cascade"])["ai_unevaluated"] == (1, 2)
    assert e2["lenient"] == {"status": "ok", "n": 2, "ai_positive": 1, "recall": 0.5}
    assert e2["strict"]["recall"] == 0.5
    assert out["e3"] == {"count": 2, "with_other_human_label": 1, "no_human_label": 1, "ai_positive_total": 14}
    assert out["ai_reactions"]["duplicates"] == 1
    assert any("첫 줄만" in w for w in out["warnings"])


def test_gate_fail_withholds_strict_and_unknown_warns():
    out = ac.compare(unsealed_run(golden_ai()), golden_final(), human_gate=False)
    assert out["e1"]["strict"] == {"status": ac.STATUS_WITHHELD, "n": 0}
    assert steps(out["e1"]["cascade"])["agreement_below_gate"] == (8, 0)
    assert out["e1"]["lenient"]["n"] == 10                       # 참고 점수는 기준 미달이어도 낸다(경고)
    unk = ac.compare(unsealed_run(golden_ai()), golden_final(), human_gate=None)
    assert unk["e1"]["strict"]["status"] == ac.STATUS_GATE_UNKNOWN and unk["e1"]["strict"]["n"] == 8
    assert any("보류" in w for w in unk["warnings"])


def test_broadcast_weighting():
    # 표본 층 둘: {1..4} 적격 400, {5,6,7,9} 적격 100 → 가중치 100 · 25
    strata = [{"chat_ids": [cid(i) for i in (1, 2, 3, 4)], "eligible": 400},
              {"chat_ids": [cid(i) for i in (5, 6, 7, 9)], "eligible": 100}]
    out = ac.compare(unsealed_run(golden_ai()), golden_final(), human_gate=True, strata=strata)
    w = out["e1"]["strict"]["binary"]["weighted"]
    # tp = 1·2·3 (층1) → 300, fp = 7 (층2) → 25, fn = 4 (층1) → 100
    assert w["precision"] == pytest.approx(300 / 325) and w["recall"] == pytest.approx(300 / 400)


def seg_item(a, z, cat, conf="certain", reasons=(), st="final"):
    return {"set": st, "row": {"label_kind": "segment", "eval": {"excluded_reasons": list(reasons)}},
            "report_reasons": [], "piece": {"start_ms": a, "end_ms": z, "category": cat, "confidence": conf,
                                            "status": "label", "relabeled": False}}


def test_segments_golden():
    final = {"items": [], "conflicts": [], "segment_items": [
        seg_item(0, 60_000, "just_chatting"), seg_item(60_000, 90_000, "game"),
        seg_item(90_000, 120_000, "just_chatting", conf="probably", reasons=["confidence_probably"])]}
    ai = ai_doc(segments=[{"start_ms": 0, "end_ms": 55_000, "category": "just_chatting"},
                          {"start_ms": 55_000, "end_ms": 120_000, "category": "game"}])
    out = ac.compare(unsealed_run(ai), final, human_gate=True)
    assert out["e1"] == {"status": ac.STATUS_NOT_PROVIDED}          # 반응을 내지 않은 엔진
    seg = out["segments"]
    le, st = seg["lenient"], seg["strict"]
    # 참고: JC-JC 55, JC-game 35, game-game 30 → p_o 17/24, p_e 23/48, κ 11/25, IoU 55/90
    assert le["cells"] == 120 and le["time_agreement"] == pytest.approx(17 / 24)
    assert le["kappa"] == pytest.approx(11 / 25) and le["jc_iou"] == pytest.approx(55 / 90)
    # 엄격: 아마도 조각 빼고, 경계 60·90초 ±10초 칸 빼면 JC 50칸 + game 10칸, 모두 일치
    assert st["cells"] == 60 and st["time_agreement"] == 1.0 and st["kappa"] == pytest.approx(1.0)
    bd = seg["boundaries"]
    assert (bd["human"], bd["ai"], bd["matched"], bd["human_only"], bd["ai_only"]) == (2, 1, 1, 1, 0)
    assert bd["median_sec"] == 5.0 and bd["within_buffer_ratio"] == 1.0
    assert seg["ms_by_first_reason"] == {"confidence_probably": 30_000, "none": 90_000}


@pytest.mark.parametrize("bad", [
    lambda r: r.pop("unsealed"),
    lambda r: r["unsealed"].update(kind="ai.sealed"),
    lambda r: r["unsealed"].update(sha256="cd" * 32),
    lambda r: r["unsealed"].update(run_id="other"),
    lambda r: r.pop("sealed"),
    lambda r: r["sealed"].update(check="rejected"),
    lambda r: r.update(raw=b"x"),
])
def test_not_unsealed_run_is_refused(bad):
    r = unsealed_run(golden_ai())
    bad(r)
    with pytest.raises(ac.RunNotUnsealedError):
        ac.compare(r, golden_final())


@pytest.mark.parametrize("patch", [
    lambda d: d.update(schema_version=2),
    lambda d: d.update(time_axis="media_ms"),
    lambda d: d["reactions"].append({"chat_id": cid(40), "result": "unconfirmed", "grade": "direct"}),
    lambda d: d["reactions"].append({"chat_id": cid(41), "result": "graded", "grade": None}),
    lambda d: d["reactions"].append({"chat_id": cid(42), "result": "graded", "grade": "direct", "score": float("nan")}),
])
def test_invalid_result_is_refused(patch):
    d = golden_ai()
    patch(d)
    with pytest.raises(ac.RunInvalidError):
        ac.compare(unsealed_run(d), golden_final())


def test_core_metric_functions_do_not_open_files(monkeypatch):
    import builtins
    def boom(*a, **k):
        raise AssertionError("파일을 열었다")
    monkeypatch.setattr(builtins, "open", boom)
    ac.compare(unsealed_run(golden_ai()), golden_final(), human_gate=True)


# ---------------------------------------------------------------------------------------------- 파일·보고서 (합성 프로젝트)
def _engine_bytes():
    rx = [ai_rx(1, "direct", 111_500), ai_rx(2, "likely", 121_500), ai_rx(3, "direct", 131_500),
          ai_rx(4, "likely", 141_500), ai_rx(5, "direct", 151_500), ai_rx(6, "unconfirmed")]
    d = ai_doc(reactions=rx, segments=[{"start_ms": 0, "end_ms": 600_000, "category": "just_chatting"}])
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def test_report_end_to_end_with_seal(proj, tmp_path, monkeypatch):
    _write_adjudications(proj)
    root = proj["root"]
    e = seal.seal_bytes(root, _engine_bytes(), source="file", by="L01", now=NOW)
    assert e.check == "valid"
    # 해제 전: run 을 만들 수 없고, 라벨 파일은 열리지 않는다
    called = []
    monkeypatch.setattr(ar, "load_locked_session", lambda *a, **k: called.append(1))
    with pytest.raises(ac.RunNotUnsealedError):
        ac.load_unsealed_run(root, "run-synth-a")
    fake = unsealed_run(ai_doc(reactions=[]))
    fake.pop("unsealed")
    with pytest.raises(ac.RunNotUnsealedError):
        ac.build_report(root, fake)
    assert called == []
    monkeypatch.undo()

    seal.unseal(root, e.sha256, by="L01", now=NOW, early_reason="합성 시험: 계획 없이 연다")
    run = ac.load_unsealed_run(root, "run-synth-a")
    out_root = str(tmp_path / "reports")
    res = ac.make_report(root, run, out_root=out_root, relabel_session_ids=[RS], human_gate=True, now=NOW,
                         created_by="L01", app_version="0.0.1")
    d = res.out_dir
    assert os.path.basename(d) == res.report["report_id"] and ar.REPORT_ID_RE.match(res.report["report_id"])
    names = sorted(os.listdir(d))
    assert names == ["manifest.json", "report.json", "report.md", "tables"]
    man = json.load(open(os.path.join(d, "manifest.json"), encoding="utf-8"))
    for f in man["files"]:
        data = open(os.path.join(d, *f["name"].split("/")), "rb").read()
        assert hashlib.sha256(data).hexdigest() == f["sha256"] and len(data) == f["size"]
    assert any(f["name"] == "tables/e1_cascade.csv" for f in man["files"])
    csv_b = open(os.path.join(d, "tables", "e1_cascade.csv"), "rb").read()
    assert csv_b.startswith(b"\xef\xbb\xbf")
    rep = res.report
    # 최종 세트: 1(두 번 같음) · 3(조정 값) · 6 이 참고, 2 사람 불일치 · 4 제외 · 5 모호
    e1 = rep["e1"]
    assert e1["population"] == 6
    s = steps(e1["cascade"])
    assert s["human_disagreement"] == (1, 3) and s["adjudicated_exclude"] == (1, 5) and s["adjudicated_ambiguous"] == (1, 4)
    assert e1["lenient"]["n"] == 3 and e1["strict"]["n"] == 2          # 3 은 두 번 달라 엄격에서 뺌
    assert e1["lenient"]["binary"]["tp"] == 2 and e1["lenient"]["binary"]["tn"] == 1
    assert rep["header"]["conditions_met"] is False and rep["header"]["dataset_split"] == "dev"
    assert rep["header"]["adjudicator_is_labeler"] is True
    assert rep["segments"]["lenient"]["time_agreement"] == 1.0
    blob = b"".join(open(os.path.join(dp, n), "rb").read() for dp, _, ns in os.walk(d) for n in ns)
    for secret in ("합성 본문", "시**", "합성 근거", "합성 받아쓰기"):
        assert secret.encode("utf-8") not in blob


def test_report_location_and_reader_refused(proj, tmp_path):
    run = unsealed_run(ai_doc(reactions=[]))
    with pytest.raises(ar.ReportLocationError):
        ac.make_report(proj["root"], run, out_root=os.path.join(proj["root"], "reports"))
    for kind in ("primary", "relabel_same", "relabel_second", "origin_review", "analysis"):
        with pytest.raises(ac.AICompareBlindError):
            ac.build_report(proj["root"], run, reader_kind=kind)
        with pytest.raises(ac.AICompareBlindError):
            ac.load_unsealed_run(proj["root"], "run-synth-a", reader_kind=kind)


def test_video_no_mismatch_refused(proj):
    run = unsealed_run(ai_doc(reactions=[], video_no="999"))
    with pytest.raises(ac.RunInvalidError):
        ac.build_report(proj["root"], run, relabel_session_ids=[RS])


def test_cli_refuses_sealed_only_run(proj, tmp_path, capsys):
    from tools import ai_compare as tool
    seal.seal_bytes(proj["root"], _engine_bytes(), source="file", by="L01", now=NOW)
    code = tool.main(["--project", proj["root"], "--run-id", "run-synth-a", "--out", str(tmp_path / "rep")])
    assert code == 3
    assert "해제" in capsys.readouterr().err
