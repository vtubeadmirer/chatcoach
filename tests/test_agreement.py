"""app/core/agreement.py · app/agreement_report.py · tools/agreement_report.py
(06 4-0~4-3·4-8·5장·7-1, 08 D-11 6·8).

손계산 골든: 06 4-1 의 600초 예, 06 4-2 의 20건 등급표(이진으로 바꾼 4-3 예 포함), 작은 표 몇 개.
합성 데이터만 쓴다. 채팅 본문·닉네임·근거 문장은 지어낸 표식 문자열이다.
"""
import builtins
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import agreement_report as ar
from app import jsonl, project
from app.core import agreement as ag

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 9, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-555555555555"
SALT = bytes(range(32))
MID_1 = "aaaaaaaa-0000-4000-8000-000000000001"
MID_2 = "bbbbbbbb-0000-4000-8000-000000000002"
SID_A = "L01-P-20261021-ab12"
SID_B = "L02-R2-20261021-cd34"
SID_RS = "L01-RS-20261024-ef56"
RATIONALE_SECRET = "근거표식문장"          # 근거 한 줄 — 보고서에 나오면 안 된다
NICK_SECRET = "합성닉네임표식"
SHA_T = "ab" * 32


def S(s, e, c):
    return {"start_ms": s * 1000, "end_ms": e * 1000, "category": c}


def R(cid, grade, *, conf="certain", t=0, entry="from_sample"):
    """반응 body. grade: direct·likely·related_only·unclear·unconfirmed."""
    b = {"chat_id": cid, "entry": entry, "confidence": conf, "first_visible_ms": t, "rationale": RATIONALE_SECRET}
    if grade == "unconfirmed":
        b.update(result="unconfirmed", grade=None)
    else:
        b.update(result="graded", grade=grade)
    return b


def _read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def cid(i):
    return "c" + f"{i:020x}"


# ============================================================================== core: 06 4-1 예 (600초)
A_SEG = [S(0, 300, "just_chatting"), S(300, 600, "game")]
B_SEG = [S(0, 312, "just_chatting"), S(312, 580, "game"), S(580, 600, "other")]


def test_segment_golden_06_4_1():
    r = ag.compare_segments(A_SEG, B_SEG)
    assert r["cells_compared"] == 600
    assert r["time_agreement"] == pytest.approx(568 / 600)            # 94.7%
    assert r["p_e"] == pytest.approx(29 / 60)                         # 0.50×0.52 + 0.50×0.447 = 0.483
    assert r["kappa"] == pytest.approx(139 / 155)                     # 0.8968 → 06 의 0.90
    assert round(r["kappa"], 2) == 0.90
    assert r["jc_iou"] == pytest.approx(300 / 312)                    # 0.96
    assert r["core"]["excluded_cells"] == 52                          # 경계 ±10초 칸 52개
    assert r["core"]["cells"] == 548
    assert r["core"]["agreement"] == pytest.approx(538 / 548)         # 98.2%
    b = r["boundaries"]
    assert (b["a_count"], b["b_count"], b["matched"], b["a_only"], b["b_only"]) == (1, 2, 1, 0, 1)
    assert b["median_sec"] == 12.0 and b["p90_sec"] == 12.0
    assert b["within_buffer_ratio"] == 0.0                            # 12초는 완충 10초 밖
    conf = dict(zip(r["confusion"]["labels"], r["confusion"]["rows"]))
    assert conf["just_chatting"][0] == 300 and conf["game"][0] == 12
    assert conf["game"][1] == 268 and conf["game"][3] == 20
    assert r["undeterminable_ratio"] == {"a": 0.0, "b": 0.0}
    # 블록 1개 → 신뢰구간 없음 (06 4-8)
    assert r["ci"]["kappa"]["status"] == "too_few_blocks"


def test_segment_identical_is_kappa_one_and_accepts_records():
    recs = [{"kind": "segment", "body": s} for s in A_SEG]
    r = ag.compare_segments(recs, A_SEG)
    assert r["kappa"] == pytest.approx(1.0)
    assert r["boundaries"]["matched"] == 1 and r["boundaries"]["median_sec"] == 0.0


def test_segment_single_category_both_kappa_undefined():
    r = ag.compare_segments([S(0, 100, "game")], [S(0, 100, "game")])
    assert r["time_agreement"] == 1.0 and r["kappa"] is None          # p_e = 1 → 정의 안 됨


def test_segment_undeterminable_is_a_category_and_ratio():
    a = [S(0, 50, "undeterminable"), S(50, 100, "game")]
    b = [S(0, 100, "game")]
    r = ag.compare_segments(a, b)
    assert r["undeterminable_ratio"]["a"] == pytest.approx(0.5)
    assert r["time_agreement"] == pytest.approx(0.5)
    # A: 판단 불가 0.5, 게임 0.5 / B: 게임 1.0 → p_e = 0.5, κ = 0
    assert r["kappa"] == pytest.approx(0.0)


def test_segment_scope_blocks_and_uncovered():
    # B(재라벨)는 [0, 600초) 블록만. 블록 가장자리 600초는 경계가 아니다(06 3-1)
    a = [S(0, 600, "just_chatting"), S(600, 1200, "game")]
    b = [S(0, 600, "just_chatting")]
    r = ag.compare_segments(a, b, scope_b=[(0, 600_000)])
    assert r["ranges"] == [[0, 600_000]]
    assert r["cells_compared"] == 600 and r["boundaries"]["a_count"] == 0
    # 범위를 주지 않으면 600~1200초는 한쪽만 구간이 있어 uncovered
    r2 = ag.compare_segments(a, b)
    assert r2["uncovered"]["a_only"] == 600 and r2["cells_compared"] == 600
    # 그때 600초는 A 쪽에서 전환(저스트→게임), B 쪽에서는 빈틈과 맞닿은 끝 → 둘 다 경계, Δ=0
    assert r2["boundaries"]["matched"] == 1


def test_segment_same_category_adjacent_is_not_boundary():
    a = [S(0, 300, "game"), S(300, 600, "game")]
    b = [S(0, 600, "game")]
    r = ag.compare_segments(a, b)
    assert r["boundaries"]["a_count"] == 0 and r["boundaries"]["b_count"] == 0
    assert r["core"]["excluded_cells"] == 0


def test_match_boundaries_greedy_smallest_first_and_window():
    pairs, oa, ob = ag.match_boundaries([100, 140], [110, 135], 30)
    assert pairs == [(100, 110), (140, 135)] and oa == [] and ob == []
    pairs, oa, ob = ag.match_boundaries([0, 100], [31, 95], 30)
    assert pairs == [(100, 95)] and oa == [0] and ob == [31]          # 31 > 30 창 밖
    # 한 경계가 두 짝 후보일 때 차이가 작은 쪽부터: B 112 는 A 110 과(2) — A 100 은 B 90 과(10)
    pairs, _, _ = ag.match_boundaries([100, 110], [90, 112], 30)
    assert pairs == [(100, 90), (110, 112)]


# ============================================================================== core: 06 4-2 예 (20건) · 4-3 이진
GRADE_TABLE = [  # 행 A, 열 B: 직접 · 높음 · 관련 · 미확인
    ("direct", [3, 1, 0, 0]),
    ("likely", [1, 2, 1, 0]),
    ("related_only", [0, 1, 2, 1]),
    ("unconfirmed", [0, 0, 1, 7]),
]
COLS = ["direct", "likely", "related_only", "unconfirmed"]


def _grade_pairs():
    ra, rb = [], []
    i = 0
    for a, counts in GRADE_TABLE:
        for b, n in zip(COLS, counts):
            for _ in range(n):
                i += 1
                ra.append(R(cid(i), a, t=i * 1000))
                rb.append(R(cid(i), b, t=i * 1000))
    # 불명확 2건(06 예의 '따로 표시') — 한쪽만 1, 둘 다 1
    for g_a, g_b in (("unclear", "unclear"), ("unclear", "direct")):
        i += 1
        ra.append(R(cid(i), g_a, t=i * 1000))
        rb.append(R(cid(i), g_b, t=i * 1000))
    return ra, rb


def test_weighted_kappa_table_golden_06_4_2():
    tbl = {(a, b): n for a, cs in GRADE_TABLE for b, n in zip(COLS, cs) if n}
    w = ag.weighted_kappa_from_table(tbl)
    assert w["n"] == 20
    assert w["p_o_w"] == pytest.approx(0.90)                         # (14 + 6×2/3) ÷ 20
    assert w["p_e_w"] == pytest.approx(43 / 75)                      # 0.573
    assert w["kappa_w"] == pytest.approx(49 / 64)                    # 0.7656 → 06 의 0.77
    assert round(w["kappa_w"], 2) == 0.77
    k = ag.kappa_from_table(tbl, ag.GRADE_SCALE)
    assert k["p_e"] == pytest.approx(0.28) and k["kappa"] == pytest.approx(7 / 12)   # 06 의 0.58


def test_linear_weights_06_table():
    assert [ag.linear_weight(0, j, 4) for j in range(4)] == pytest.approx([1, 2 / 3, 1 / 3, 0])


def test_compare_samples_golden_grade_and_binary():
    ra, rb = _grade_pairs()
    r = ag.compare_samples(ra, rb)
    g = r["grade"]
    assert g["n"] == 20
    assert g["exact"] == pytest.approx(0.70) and g["one_step"] == pytest.approx(0.30) and g["two_plus"] == 0
    assert g["kappa_w"] == pytest.approx(49 / 64)
    b = r["binary"]                                                   # 06 4-3 예
    assert (b["both_positive"], b["a_only"], b["b_only"], b["both_negative"]) == (7, 1, 1, 11)
    assert b["agreement"] == pytest.approx(0.90)
    assert b["kappa"] == pytest.approx(19 / 24)                       # 0.79
    assert b["ac1"] == pytest.approx(21 / 26)                         # 0.81
    assert b["prevalence"] == pytest.approx(0.4) and b["show_ac1"] is False
    u = r["unclear"]
    assert (u["both"], u["a_only"], u["b_only"]) == (1, 1, 0) and u["agreement"] == pytest.approx(0.5)
    assert r["judged"]["both"] == 22
    # 불일치 사례: 등급 6건 + 불명확 한쪽 1건, 본문·근거는 없다
    assert len(r["disagreements"]) == 7
    assert RATIONALE_SECRET not in json.dumps(r, ensure_ascii=False)


def test_small_hand_kappas():
    # 2×2: [[2,1],[0,1]] → p_o 3/4, p_e 3/8+1/8 = 1/2, κ = 0.5
    k = ag.kappa_from_table({(True, True): 2, (True, False): 1, (False, False): 1}, (True, False))
    assert k["kappa"] == pytest.approx(0.5)
    # 가중: (직접,직접)(직접,높음)(미확인,미확인) → p_o^w 8/9, p_e^w 14/27, κ_w 10/13
    w = ag.weighted_kappa_from_table({("direct", "direct"): 1, ("direct", "likely"): 1, ("unconfirmed", "unconfirmed"): 1})
    assert w["p_o_w"] == pytest.approx(8 / 9) and w["p_e_w"] == pytest.approx(14 / 27)
    assert w["kappa_w"] == pytest.approx(10 / 13)
    with pytest.raises(ag.AgreementError):
        ag.weighted_kappa_from_table({("unclear", "direct"): 1})
    assert ag.kappa_from_table({})["kappa"] is None


def test_undeterminable_counted_separately_and_one_sided():
    ra = [R(cid(1), "direct", conf="undeterminable"), R(cid(2), "likely", conf="undeterminable"),
          R(cid(3), "direct", conf="probably"), R(cid(4), "direct"), R(cid(6), "likely")]
    rb = [R(cid(1), "direct", conf="undeterminable"), R(cid(2), "likely"),
          R(cid(3), "direct", conf="undeterminable"), R(cid(4), "direct"), R(cid(5), "direct")]
    r = ag.compare_samples(ra, rb)
    assert r["undeterminable"] == {"both": 1, "a_only": 1, "b_only": 1}
    assert r["grade"]["n"] == 1 and r["binary"]["n"] == 1
    assert r["judged"]["a_only"] == 1 and r["judged"]["b_only"] == 1  # cid 6 · cid 5
    # chat_ids 로 좁히기
    r2 = ag.compare_samples(ra, rb, chat_ids=[cid(4)])
    assert r2["judged"]["both"] == 1 and r2["restricted_to_chat_ids"] is True


def test_duplicate_chat_label_excluded():
    ra = [R(cid(1), "direct"), R(cid(1), "likely"), R(cid(2), "direct")]
    rb = [R(cid(1), "direct"), R(cid(2), "direct")]
    r = ag.compare_samples(ra, rb)
    assert r["judged"]["duplicate_chat_a"] == 1 and r["judged"]["both"] == 1


# ============================================================================== core: 분위수 · 의사난수 · 부트스트랩
def test_percentile_linear():
    assert ag.percentile([1, 2, 3, 4], 0.9) == pytest.approx(3.7)
    assert ag.percentile([4, 1, 3, 2], 0.5) == pytest.approx(2.5)
    assert ag.percentile([7], 0.9) == 7 and ag.percentile([], 0.5) is None


def test_splitmix64_reference_value_and_determinism():
    assert ag.SplitMix64(0).next_u64() == 0xE220A8397B1DCDAF        # splitmix64 의 알려진 첫 값(시드 0)
    a, b = ag.SplitMix64(ag.seed_from("x")), ag.SplitMix64(ag.seed_from("x"))
    assert [a.below(7) for _ in range(50)] == [b.below(7) for _ in range(50)]
    assert all(0 <= ag.SplitMix64(3).below(5) < 5 for _ in range(5))


def test_bootstrap_deterministic_and_block_rule():
    blocks = list(range(12))
    stat = lambda bs: sum(bs) / len(bs)                               # noqa: E731
    r1 = ag.bootstrap_ci(blocks, stat, n_boot=200, seed_material="m")
    r2 = ag.bootstrap_ci(blocks, stat, n_boot=200, seed_material="m")
    r3 = ag.bootstrap_ci(blocks, stat, n_boot=200, seed_material="other")
    assert r1 == r2 and r1["status"] == "ok" and r1["lo"] < 5.5 < r1["hi"]
    assert r3["lo"] != r1["lo"] or r3["hi"] != r1["hi"]
    assert ag.bootstrap_ci(blocks[:9], stat, n_boot=200, seed_material="m")["status"] == "too_few_blocks"
    assert ag.bootstrap_ci(blocks, lambda bs: None, n_boot=5, seed_material="m")["status"] == "no_valid_resample"


def test_sample_ci_with_many_blocks():
    ra, rb = [], []
    for i in range(60):                                               # 12 블록(10분) × 5건
        t = (i // 5) * 600_000 + 1000
        g = "direct" if i % 3 == 0 else "unconfirmed"
        ra.append(R(cid(i), g, t=t))
        rb.append(R(cid(i), g if i % 7 else "likely", t=t))
    r = ag.compare_samples(ra, rb, seed_material="seed")
    assert r["n_blocks"] == 12
    ci = r["ci"]["binary_kappa"]
    assert ci["status"] == "ok" and ci["lo"] <= r["binary"]["kappa"] <= ci["hi"]
    assert ag.compare_samples(ra, rb, seed_material="seed")["ci"] == r["ci"]     # 같은 시드 → 같은 구간


# ============================================================================== core: 판정 · 해석
def test_gate_verdicts():
    ok = {"status": "ok", "lo": 0.85, "hi": 0.95}
    assert ag.gate_verdict(0.9, ok, 0.8, n=100, min_n=30) == ag.V_PASS
    assert ag.gate_verdict(0.9, {"status": "ok", "lo": 0.7, "hi": 0.95}, 0.8, n=100, min_n=30) == ag.V_UNCERTAIN
    assert ag.gate_verdict(0.9, {"status": "too_few_blocks"}, 0.8, n=100, min_n=30) == ag.V_PASS_POINT
    assert ag.gate_verdict(0.7, ok, 0.8, n=100, min_n=30) == ag.V_FAIL
    assert ag.gate_verdict(0.9, ok, 0.8, n=29, min_n=30) == ag.V_LOW_N
    assert ag.gate_verdict(None, ok, 0.8, n=100, min_n=30) == ag.V_NA
    # 경계 중앙값은 낮을수록 좋다
    assert ag.gate_verdict(4.0, {"status": "ok", "lo": 3, "hi": 6}, 5, n=40, min_n=30, higher_is_better=False) == ag.V_UNCERTAIN
    assert ag.gate_verdict(6.0, None, 5, n=40, min_n=30, higher_is_better=False) == ag.V_FAIL


def _side(sid, lab, mid, segs, reacts, **kw):
    return {"session_id": sid, "labeler_id": lab, "session_kind": "primary", "machine_id": mid, "scope": None,
            "segments": segs, "reactions": reacts, "view_exposed": [], "analysis_exposed": False,
            "engine_author": False, **kw}


def test_build_agreement_intra_same_machine_text():
    ra, rb = _grade_pairs()
    res = ag.build_agreement(_side(SID_A, "L01", MID_1, A_SEG, ra), _side(SID_RS, "L01", MID_1, B_SEG, rb),
                             seed_material="s")
    h = res["header"]
    assert h["comparison"] == "intra" and h["intra_only"] and h["same_machine"] and h["representative"]
    text = "\n".join(res["interpretation"])
    assert "같은 machine_id" in text and "intra-rater" in text and "상한" in text
    keys = {g["key"]: g for g in res["gates"]}
    assert keys["grade_kappa_w"]["verdict"] == ag.V_LOW_N             # 20 < 30
    assert keys["segment_kappa"]["verdict"] == ag.V_PASS_POINT        # 0.897 ≥ 0.80, 블록 1개
    assert keys["boundary_median_sec"]["verdict"] == ag.V_LOW_N       # 짝 1개
    assert "점추정만" in text and "결론을 미룬다" in text
    assert RATIONALE_SECRET not in json.dumps(res, ensure_ascii=False)


def test_build_agreement_inter_fail_and_exposed():
    a = [S(0, 600, "game")] + [S(600 + i * 10, 610 + i * 10, "just_chatting" if i % 2 else "other") for i in range(10)]
    b = [S(0, 600, "just_chatting")] + [S(600 + i * 10, 610 + i * 10, "other") for i in range(10)]
    res = ag.build_agreement(_side(SID_A, "L01", MID_1, a, []),
                             _side(SID_B, "L02", MID_2, b, [], view_exposed=["asr_text"], engine_author=True))
    h = res["header"]
    assert h["comparison"] == "inter" and not h["same_machine"]
    assert h["representative"] is False and h["view_exposed_sessions"] == [SID_B]
    text = "\n".join(res["interpretation"])
    assert "inter-rater" in text and "기준 흐림" in text and "엔진 작성자" in text and "대표값" in text
    assert {g["key"]: g["verdict"] for g in res["gates"]}["segment_kappa"] == ag.V_FAIL


def test_settings_override_and_validation():
    st = ag.merge_settings({"gate": {"segment_kappa": 0.5}, "agreement": {"bootstrap_n": 0}})
    assert st["gate"]["segment_kappa"] == 0.5 and st["gate"]["binary_kappa"] == 0.70
    assert ag.DEFAULT_SETTINGS["gate"]["segment_kappa"] == 0.80       # 기본값은 그대로
    with pytest.raises(ag.AgreementError):
        ag.merge_settings({"agreement": {"grid_ms": 0}})
    with pytest.raises(ag.AgreementError):
        ag.merge_settings({"agreement": {"ci_level": 1.5}})


def test_core_is_pure():
    src = _read(ag.__file__).decode("utf-8")
    for bad in ("import random", "import os", "import time", "datetime", "PySide6", "open(", "secrets"):
        assert bad not in src, bad


# ============================================================================== app: 잠긴 두 세션 → 보고서
class _Ids:
    def __init__(self, start):
        self.n = start

    def __call__(self):
        self.n += 1
        return f"{self.n:08x}-0000-4000-8000-{self.n:012x}"


def _open_line(sid, mid, *, kind, scope=None, thresholds=SHA_T):
    return {"v": 1, "id": "00000000-0000-4000-8000-00000000ffff", "seq": 1, "kind": "session.open",
            "session_id": sid, "labeler_id": sid.split("-")[0], "created_at": "2026-10-21T09:00:00+09:00",
            "app_version": "0.0.1",
            "body": {"project_id": PID, "session_kind": kind, "session_created_at": "2026-10-21T09:00:00+09:00",
                     "machine_id": mid, "machine4": "aaaa", "guide_version": "0.1", "guide_doc_sha256": SHA_T,
                     "thresholds_sha256": thresholds, "scope": scope or {"type": "whole"},
                     "blind": {"to_ai": True, "to_other_sessions": True, "to_origin": True,
                               "nickname_masked_default": True}, "dataset_split": "dev"}}


def _label_lines(sid, mid, segs, reacts, *, kind, start, scope=None, thresholds=SHA_T, extra=()):
    ids = _Ids(start)
    lines = [_open_line(sid, mid, kind=kind, scope=scope, thresholds=thresholds)]
    lines[0]["id"] = ids()
    seq = 1
    seg_ids = []
    for s in segs:
        seq += 1
        i = ids()
        seg_ids.append(i)
        lines.append({"v": 1, "id": i, "seq": seq, "kind": "segment", "session_id": sid, "entity_id": i,
                      "supersedes": None, "body": {**s, "confidence": "certain", "rationale": RATIONALE_SECRET}})
    for r in reacts:
        seq += 1
        i = ids()
        lines.append({"v": 1, "id": i, "seq": seq, "kind": "reaction", "session_id": sid, "entity_id": i,
                      "supersedes": None, "body": {**r, "segment_id": seg_ids[0] if seg_ids else i,
                                                   "nickname_masked": NICK_SECRET}})
    for e in extra:
        seq += 1
        lines.append({**e, "seq": seq, "session_id": sid, "id": ids()})
    return lines


def _m4(mid):
    return project.machine4_of(mid)


def _write_session(root, sid, mid, lines):
    name = f"{sid}.{_m4(mid)}.jsonl"
    p = os.path.join(root, "labels", name)
    if os.path.exists(p):
        os.remove(p)
    jsonl.write_all(p, lines)
    return p


def _lock(root, sid, mid):
    name = f"{sid}.{_m4(mid)}.jsonl"
    with open(os.path.join(root, "labels", name), "rb") as f:
        data = f.read()
    seqs = [json.loads(x)["seq"] for x in data.decode("utf-8").splitlines() if x]
    lk = {"session_id": sid, "project_id": PID, "locked_at": "2026-10-21T10:00:00+09:00", "locked_by": sid.split("-")[0],
          "app_version": "0.0.1",
          "files": [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "last_seq": max(seqs)}],
          "label_counts": {}, "sample_verify": []}
    os.makedirs(os.path.join(root, "locks"), exist_ok=True)
    with open(os.path.join(root, "locks", f"{sid}.json"), "w", encoding="utf-8") as f:
        json.dump(lk, f, ensure_ascii=False)


@pytest.fixture
def proj(tmp_path):
    base = tmp_path / "chatcoach-data" / "projects"
    p = project.create(str(base), "합성 방송", 777, "시험용", "L01", "0.0.1", NOW, PID, SALT,
                       guide=project.GuideSource("0.1", b"# guide\n", {"segment": {"boundary_buffer_sec": 10}}),
                       machine4="m3fa")
    root = p.root
    jsonl.write_all(os.path.join(root, "chat", "messages.jsonl"),
                    [{"chat_id": cid(1), "t_ms": 1000, "text": "합성본문표식", "nickname_masked": NICK_SECRET}])
    ra, rb = _grade_pairs()
    _write_session(root, SID_A, MID_1, _label_lines(SID_A, MID_1, A_SEG, ra, kind="primary", start=0))
    _write_session(root, SID_B, MID_1, _label_lines(SID_B, MID_1, B_SEG, rb, kind="relabel_second", start=5000))
    return {"root": root, "base": str(base), "tmp": tmp_path}


def test_report_refused_when_not_locked_and_label_files_not_opened(proj, monkeypatch):
    root = proj["root"]
    _lock(root, SID_A, MID_1)                                         # B 는 잠그지 않음
    opened = []
    real_open = builtins.open

    def spy(path, *a, **kw):
        opened.append(os.path.abspath(str(path)))
        return real_open(path, *a, **kw)
    monkeypatch.setattr(ar.builtins, "open", spy)
    out = str(proj["tmp"] / "reports")
    with pytest.raises(ar.NotLockedError) as ei:
        ar.make_report(root, SID_A, SID_B, out_root=out, now=NOW)
    assert ei.value.session_ids == [SID_B]
    labels = os.path.abspath(os.path.join(root, "labels"))
    assert not [p for p in opened if p.startswith(labels)]            # 라벨 파일을 하나도 열지 않음 (06 7-3)
    assert not os.path.exists(out)                                    # 보고서 없음


def test_report_written_without_text_and_golden_numbers(proj):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    res = ar.make_report(root, SID_A, SID_B, now=NOW, created_by="L01")
    # 기본 출력: …\projects\<slug> 옆 …\reports\<project_id>\<report_id>
    assert res.out_dir.startswith(os.path.join(os.path.dirname(proj["base"]), "reports", PID))
    rep = json.loads(_read(os.path.join(res.out_dir, "report.json")).decode("utf-8"))
    md = _read(os.path.join(res.out_dir, "report.md")).decode("utf-8")
    assert rep["report_id"].startswith("R-20261021-")
    assert rep["segments"]["kappa"] == pytest.approx(139 / 155)
    assert rep["samples"]["grade"]["kappa_w"] == pytest.approx(49 / 64)
    assert rep["samples"]["binary"]["kappa"] == pytest.approx(19 / 24)
    assert rep["header"]["same_machine"] is True and rep["header"]["comparison"] == "inter"
    assert "같은 machine_id" in md and "같은 machine_id" in "\n".join(rep["interpretation"])
    blob = json.dumps(rep, ensure_ascii=False) + md
    for secret in (RATIONALE_SECRET, NICK_SECRET, "합성본문표식", MID_1):
        assert secret not in blob
    s = rep["inputs"]["sessions"]
    assert [x["session_id"] for x in s] == [SID_A, SID_B] and all(len(x["lock_sha256"]) == 64 for x in s)
    assert rep["inputs"]["eval_settings"]["values"]["gate"]["segment_kappa"] == 0.80
    # 같은 입력 → 같은 계산(만든 시각만 다름)
    res2 = ar.build_report(root, SID_A, SID_B, now=NOW + timedelta(hours=1))
    assert res2.report["inputs_sha256"] == rep["inputs_sha256"]
    assert res2.report["samples"] == rep["samples"] and res2.report["report_id"] == rep["report_id"]


def test_lines_after_lock_are_excluded(proj):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    p = os.path.join(root, "labels", f"{SID_B}.{_m4(MID_1)}.jsonl")
    jsonl.append(p, {"v": 1, "id": "ffffffff-0000-4000-8000-000000000001", "seq": 999, "kind": "segment",
                     "session_id": SID_B, "entity_id": "ffffffff-0000-4000-8000-000000000001", "supersedes": None,
                     "body": {"start_ms": 0, "end_ms": 600000, "category": "singing", "confidence": "certain",
                              "rationale": RATIONALE_SECRET}})
    res = ar.build_report(root, SID_A, SID_B, now=NOW)
    assert res.report["segments"]["kappa"] == pytest.approx(139 / 155)   # 잠금 뒤 줄은 안 셈
    assert any("잠금 뒤" in w for w in res.warnings)


def test_lock_hash_mismatch_refused(proj):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    p = os.path.join(root, "labels", f"{SID_A}.{_m4(MID_1)}.jsonl")
    data = _read(p)
    with open(p, "wb") as f:
        f.write(data.replace(b'"game"', b'"other"', 1))
    with pytest.raises(ar.LockMismatchError):
        ar.build_report(root, SID_A, SID_B, now=NOW)


def test_guide_mismatch_refused(proj):
    root = proj["root"]
    _write_session(root, SID_B, MID_1, _label_lines(SID_B, MID_1, B_SEG, [], kind="relabel_second", start=5000,
                                                    thresholds="cd" * 32))
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    with pytest.raises(ar.GuideMismatchError):
        ar.build_report(root, SID_A, SID_B, now=NOW)


def test_out_root_inside_project_or_repo_refused(proj):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    with pytest.raises(ar.ReportLocationError):
        ar.make_report(root, SID_A, SID_B, out_root=os.path.join(root, "reports"), now=NOW)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with pytest.raises(ar.ReportLocationError):
        ar.check_out_root(os.path.join(repo, "out"), root)


def test_view_exposed_and_relabel_scope(proj):
    root = proj["root"]
    exposed = {"v": 1, "kind": "view.exposed", "body": {"what": "asr_text", "chat_id": None}}
    lines = _label_lines(SID_B, MID_2, [S(0, 312, "just_chatting"), S(312, 580, "game")], [],
                         kind="relabel_second", start=5000,
                         scope={"type": "blocks", "ranges": [{"start_ms": 0, "end_ms": 600_000}]}, extra=[exposed])
    _write_session(root, SID_B, MID_2, lines)
    os.remove(os.path.join(root, "labels", f"{SID_B}.{_m4(MID_1)}.jsonl"))
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_2)
    rep = ar.build_report(root, SID_A, SID_B, now=NOW).report
    assert rep["header"]["same_machine"] is False
    assert rep["header"]["representative"] is False and rep["header"]["view_exposed_sessions"] == [SID_B]
    assert rep["inputs"]["sessions"][1]["scope"] == [[0, 600_000]]
    assert rep["segments"]["uncovered"]["a_only"] == 20               # 580~600초는 B 쪽 구간 없음


def test_unlisted_file_not_opened(proj):
    root = proj["root"]
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    other = _label_lines(SID_B, MID_2, [S(0, 600, "singing")], [], kind="relabel_second", start=9000)
    _write_session(root, SID_B, MID_2, other)                          # 잠금 뒤 다른 PC 에서 생긴 파일
    res = ar.build_report(root, SID_A, SID_B, now=NOW)
    assert res.report["segments"]["kappa"] == pytest.approx(139 / 155)
    assert any("잠금에 없는 기록 파일 1개" in w for w in res.warnings)


def test_tool_exit_codes(proj, capsys):
    from tools import agreement_report as tool
    root = proj["root"]
    out = str(proj["tmp"] / "out")
    assert tool.main(["--project", root, "--a", SID_A, "--b", SID_B, "--out", out]) == 3
    _lock(root, SID_A, MID_1)
    _lock(root, SID_B, MID_1)
    assert tool.main(["--project", root, "--a", SID_A, "--b", SID_B, "--out", os.path.join(root, "x")]) == 2
    assert tool.main(["--project", root, "--a", SID_A, "--b", SID_B, "--out", out]) == 0
    printed = capsys.readouterr().out
    assert "segment_kappa" in printed and RATIONALE_SECRET not in printed and NICK_SECRET not in printed
    assert os.path.isdir(os.path.join(out, PID))
