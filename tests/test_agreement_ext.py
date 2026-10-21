"""일치도 다섯째 묶음: 연결(±5초)·반응 시각·이어짐·놓친 후보·출처, 표본에서 빠진 판정, '조정자 = 라벨러'
(06 4-0·4-3·4-5·4-6·4-7·2-9, 08 D-11 8 '다음 묶음', D-12 2·3·5·6).

손계산 골든은 각 테스트의 주석에 식을 적었다. 합성 데이터만 쓴다 — 본문·닉네임·근거는 지어낸 표식 문자열이다.
"""
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
SHA_T = "ab" * 32
RATIONALE_SECRET = "근거표식문장확장"
NICK_SECRET = "합성닉네임표식확장"


def cid(i):
    return "c" + f"{i:020x}"


def uid(side, i):
    """발화·반응 entity_id (UUID v4 모양). side 로 세션마다 다른 ID 를 만든다."""
    return f"{side:08x}-0000-4000-8000-{i:012x}"


def U(eid, start_ms):
    return {"kind": "utterance", "entity_id": eid, "body": {"start_ms": start_ms, "end_ms": start_ms + 2000}}


def RR(c, grade, *, eid, t, utt=None, rt=None, entry="from_utterance", conf="certain"):
    b = {"chat_id": c, "entry": entry, "confidence": conf, "first_visible_ms": t, "rationale": RATIONALE_SECRET,
         "linked_utterance_id": utt, "reaction_time_ms": rt}
    if grade == "unconfirmed":
        b.update(result="unconfirmed", grade=None)
    else:
        b.update(result="graded", grade=grade)
    return {"kind": "reaction", "entity_id": eid, "body": b}


def C(reaction_eid, typ, *, span=None, related=0, truncated=False, conf="certain"):
    b = {"reaction_entity_id": reaction_eid, "type": typ, "truncated_at_segment_end": truncated,
         "related_chat_ids": [cid(900 + k) for k in range(related)], "confidence": conf, "rationale": RATIONALE_SECRET}
    if span is not None:
        b.update(span_start_ms=0, span_end_ms=span * 1000)
    return {"kind": "continuation", "body": b}


def M(c, is_cand, code=None, *, src="manual", scope="sample", t=0, c2=True, c3=True, c4=True):
    return {"kind": "missed_candidate", "body": {
        "chat_id": c, "scope": scope, "is_candidate": is_cand, "reason_code": code, "reason_code_source": src,
        "cond1": {"value": True, "in_segment": True, "in_buffer": False}, "cond2": {"value": c2},
        "cond3": c3, "cond4": c4, "first_visible_ms": t, "confidence": "certain", "rationale": RATIONALE_SECRET}}


def O(c, origin, method="manual"):
    return {"kind": "origin", "body": {"chat_id": c, "origin": origin, "method": method,
                                       "confidence": None if origin == "undeterminable" else "certain",
                                       "rationale": RATIONALE_SECRET}}


# ============================================================================== 연결 대상 · 반응 시각 (06 4-0 · 4-3)
def _link_sets():
    """A 연결 10개(c1~c10), B 12개(c1~c12). c1~c9 는 발화 시작 차이 (i−1)×0.5초(≤ 5초) → 짝 9.
    c10 은 6초 차이 → 같은 채팅인데 창 밖. c11·c12 는 B 만. A 의 c13 은 없는 발화를 가리킴(broken)."""
    rt_diff = [0, 500, 1000, 1500, 2000, 2500, 3000, 0, 1000]
    ra, rb, ua, ub = [], [], [], []
    for i in range(1, 13):
        base = i * 60_000
        if i <= 10:
            ua.append(U(uid(1, i), base))
            ra.append(RR(cid(i), "direct", eid=uid(11, i), t=base - 3000, utt=uid(1, i), rt=base))
        d = (i - 1) * 500 if i <= 9 else 6000
        ub.append(U(uid(2, i), base + d))
        rtb = base + (rt_diff[i - 1] if i <= 9 else 0)
        rb.append(RR(cid(i), "likely", eid=uid(22, i), t=base - 3000, utt=uid(2, i), rt=rtb))
    ra.append(RR(cid(13), "direct", eid=uid(11, 13), t=900_000, utt=uid(1, 999), rt=900_000))
    return ra, rb, ua, ub


def test_links_golden_f1_and_reaction_time():
    ra, rb, ua, ub = _link_sets()
    r = ag.compare_links(ra, rb, ua, ub)
    assert (r["a_links"], r["b_links"], r["matched"], r["a_only"], r["b_only"]) == (10, 12, 9, 1, 3)
    assert r["same_chat_far"] == 1
    assert r["f1"] == pytest.approx(18 / 22)                          # 2×9 ÷ (10+12) = 0.818 (06 4-3 예와 같은 수)
    assert r["utterance_start_median_sec"] == pytest.approx(2.0)      # 0, 0.5, …, 4.0 의 가운데
    rt = r["reaction_time"]
    assert rt["n"] == 9
    assert rt["median_sec"] == pytest.approx(1.0)                     # [0,0,0.5,1,1,1.5,2,2.5,3]
    assert rt["p90_sec"] == pytest.approx(2.6)                        # 위치 7.2 → 2.5 + 0.2×0.5
    assert rt["within_tolerance_ratio"] == pytest.approx(7 / 9)       # ≤ 2초: 7건
    assert r["problems"]["a"]["broken"] == 1 and r["low_n"] is True   # 22 < 30


def test_links_window_edge_and_scope_and_settings():
    ra = [RR(cid(1), "direct", eid=uid(11, 1), t=1000, utt=uid(1, 1), rt=2000)]
    rb = [RR(cid(1), "direct", eid=uid(22, 1), t=1000, utt=uid(2, 1), rt=2000)]
    ua, ub = [U(uid(1, 1), 10_000)], [U(uid(2, 1), 15_000)]
    assert ag.compare_links(ra, rb, ua, ub)["matched"] == 1                 # 정확히 5초는 짝
    tight = ag.compare_links(ra, rb, ua, ub, settings={"agreement": {"utterance_match_tolerance_sec": 4}})
    assert tight["matched"] == 0 and tight["same_chat_far"] == 1 and tight["f1"] == 0.0
    out = ag.compare_links(ra, rb, ua, ub, scope=[(600_000, 1_200_000)])
    assert out["a_links"] == 0 and out["problems"]["a"]["outside_scope"] == 1 and out["f1"] is None
    with pytest.raises(ag.AgreementError):
        ag.merge_settings({"agreement": {"reaction_time_tolerance_sec": -1}})


# ============================================================================== 이어짐 (06 4-5)
CONT_PAIRS = [  # (A 형, B 형, A 길이초, B 길이초)
    ("none", "none", None, None), ("none", "none", None, None), ("none", "none", None, None),
    ("short", "short", 30, 30), ("short", "short", 40, 35),
    ("extended", "short", 90, 45),
    ("new_topic", "new_topic", 20, 30),
    ("short", "extended", 50, 70),
]


def _cont_sets():
    ra, rb, ca, cb = [], [], [], []
    for i, (ta, tb, sa, sb) in enumerate(CONT_PAIRS, start=1):
        ea, eb = uid(11, i), uid(22, i)
        ra.append(RR(cid(i), "direct", eid=ea, t=i * 1000))
        rb.append(RR(cid(i), "likely", eid=eb, t=i * 1000))
        ca.append(C(ea, ta, span=sa, related=3 if i == 4 else 0, truncated=(i == 5)))
        cb.append(C(eb, tb, span=sb, related=1 if i == 4 else 0))
    # 9: B 가 이어짐을 비움 / 10: B 는 관련성만(비교 안 함) / 11: A 이어짐 확신도 판단 불가
    for i in (9, 10, 11):
        ra.append(RR(cid(i), "direct", eid=uid(11, i), t=i * 1000))
        rb.append(RR(cid(i), "related_only" if i == 10 else "direct", eid=uid(22, i), t=i * 1000))
        ca.append(C(uid(11, i), "short", span=10, conf="undeterminable" if i == 11 else "certain"))
    cb.append(C(uid(22, 11), "short", span=10))
    return ra, rb, ca, cb


def test_continuation_golden_kappa_and_raw():
    ra, rb, ca, cb = _cont_sets()
    r = ag.compare_continuations(ra, rb, ca, cb)
    assert r["pairs_positive_both"] == 10                             # 8 + 비움 1 + 판단 불가 1 (관련성만은 빠짐)
    assert r["n"] == 8
    assert r["agreement"] == pytest.approx(6 / 8)
    # A: none 3, short 3, extended 1, new 1 / B: 같은 분포 → p_e = (9+9+1+1)/64 = 20/64, κ = (0.75−0.3125)/0.6875 = 7/11
    assert r["p_e"] == pytest.approx(20 / 64)
    assert r["kappa"] == pytest.approx(7 / 11)
    assert r["missing"] == {"both": 0, "a_only": 0, "b_only": 1}
    assert r["undeterminable"] == {"both": 0, "a_only": 1, "b_only": 0}
    conf = dict(zip(r["confusion"]["labels"], r["confusion"]["rows"]))
    assert conf["none"] == [3, 0, 0, 0] and conf["extended"] == [0, 1, 0, 0] and conf["short"] == [0, 2, 1, 0]
    raw = r["raw"]
    assert raw["span_n"] == 5 and raw["span_diff_median_sec"] == pytest.approx(10.0)   # [0,5,45,10,20]
    assert raw["related_n"] == 8 and raw["related_count_diff_median"] == 0.0
    assert raw["truncated_n"] == 8 and raw["truncated_agreement"] == pytest.approx(7 / 8)
    assert len(r["disagreements"]) == 2 and RATIONALE_SECRET not in json.dumps(r, ensure_ascii=False)


# ============================================================================== 놓친 채팅 후보 (06 4-6)
def _missed_sets(base=0):
    """20 짝: 후보 (참,참) 2 · (참,거짓) 1 · (거짓,참) 1 · (거짓,거짓) 16.
    (거짓,거짓) 16 = 사람 코드 14 [(C,C)5 (E,E)3 (B,B)2 (C,E)2 (D,D)1 (G,C)1] + 자동 2 [(A,A) auto_suggested, (F,C) F auto].
    base: chat_id 번호를 밀어 다른 시험 반응(직접·높음)과 겹치지 않게 한다(06 2-9 대상 밖이면 일치도에서 빠진다)."""
    ma, mb = [], []
    i = 0

    def add(xa, ca, xb, cb, *, sa="manual", sb="manual", c3b=True, c4b=True):
        nonlocal i
        i += 1
        ma.append(M(cid(base + i), xa, ca, src=sa, t=i * 1000))
        mb.append(M(cid(base + i), xb, cb, src=sb, t=i * 1000, c3=c3b, c4=c4b))
    for _ in range(2):
        add(True, None, True, None)
    add(True, None, False, "C")
    add(False, "D", True, None)
    human = [("C", "C")] * 5 + [("E", "E")] * 3 + [("B", "B")] * 2 + [("C", "E")] * 2 + [("D", "D"), ("G", "C")]
    for k, (a, b) in enumerate(human):
        add(False, a, False, b, c3b=(k >= 2), c4b=(None if k < 5 else True))
    add(False, "A", False, "A", sa="auto_suggested", sb="auto_suggested")
    add(False, "F", False, "C", sa="auto")
    ma.append(M(cid(base + 50), True, None, scope="extra"))          # 표본 밖 — 비교 안 함
    ma.append(M(cid(base + 51), False, "C", t=51_000))                # A 만
    return ma, mb


def test_missed_golden_candidate_kappa_ac1_and_reason_codes():
    ma, mb = _missed_sets()
    r = ag.compare_missed(ma, mb)
    j = r["judged"]
    assert (j["a"], j["b"], j["both"], j["a_only"], j["extra_a"]) == (21, 20, 20, 1, 1)
    c = r["candidate"]
    assert (c["both_candidate"], c["a_only"], c["b_only"], c["both_not"]) == (2, 1, 1, 16)
    assert c["agreement"] == pytest.approx(0.9)
    # p_a = p_b = 3/20 → p_e = 0.15² + 0.85² = 0.745, κ = (0.9 − 0.745)/0.255 = 31/51
    assert c["kappa"] == pytest.approx(31 / 51)
    # π = 0.15, p_e = 2·0.15·0.85 = 0.255, AC1 = (0.9 − 0.255)/0.745 = 129/149
    assert c["ac1"] == pytest.approx(129 / 149) and c["prevalence"] == pytest.approx(0.15)
    rc = r["reason_code"]
    assert rc["n"] == 14 and rc["auto_excluded"] == 2
    assert rc["agreement"] == pytest.approx(11 / 14)
    # A: C7 E3 B2 D1 G1 / B: C6 E5 B2 D1 G0 → p_e = 62/196, κ = (154−62)/(196−62) = 92/134 = 46/67
    assert rc["p_e"] == pytest.approx(62 / 196) and rc["kappa"] == pytest.approx(46 / 67)
    conf = dict(zip(rc["confusion"]["labels"], rc["confusion"]["rows"]))
    assert conf["C"] == [0, 5, 0, 2, 0] and conf["G"] == [0, 1, 0, 0, 0] and rc["confusion"]["labels"] == ["B", "C", "D", "E", "G"]
    cd = r["conditions"]
    assert cd["cond2"] == {"n": 20, "agreement": 1.0}
    assert cd["cond3"]["n"] == 20 and cd["cond3"]["agreement"] == pytest.approx(18 / 20)
    assert cd["cond4"]["n"] == 15 and cd["cond4"]["agreement"] == 1.0


def test_missed_scope_restricts_to_relabel_block():
    ma, mb = _missed_sets()
    r = ag.compare_missed(ma, mb, scope=[(0, 10_500)])               # 1~10초 채팅만 (D-12 5: 블록 안)
    assert r["judged"]["both"] == 10 and r["judged"]["outside_scope_a"] == 11 and r["judged"]["outside_scope_b"] == 10


# ============================================================================== 출처 (06 4-7, D-12 6)
def _origin_sets():
    """12 짝: (실제,실제) 6 [그중 2 는 A 가 생성 기록 대조 = 자동] · (가상,가상) 3 · (실제,판별 불가) 1 · (가상,실제) 1 ·
    (판별 불가,판별 불가) 1."""
    pairs = ([("real", "real")] * 6 + [("virtual", "virtual")] * 3
             + [("real", "undeterminable"), ("virtual", "real"), ("undeterminable", "undeterminable")])
    oa, ob = [], []
    for i, (a, b) in enumerate(pairs, start=1):
        oa.append(O(cid(i), a, "generation_log_match" if i <= 2 else "manual"))
        ob.append(O(cid(i), b))
    ob.append(O(cid(99), "real"))
    return oa, ob


def test_origin_golden_confusion_and_kappa_with_auto_split():
    oa, ob = _origin_sets()
    r = ag.compare_origins(oa, ob)
    assert r["compared"] and r["n"] == 12 and r["judged"]["b_only"] == 1
    assert r["agreement"] == pytest.approx(10 / 12)
    # A: 실제7 가상4 판별불가1 / B: 실제7 가상3 판별불가2 → p_e = (49+12+2)/144 = 63/144, κ = 57/81 = 19/27
    assert r["p_e"] == pytest.approx(63 / 144) and r["kappa"] == pytest.approx(19 / 27)
    assert r["confusion"]["labels"] == ["real", "virtual", "undeterminable"]
    assert r["confusion"]["rows"] == [[6, 0, 1], [1, 3, 0], [0, 0, 1]]
    assert r["auto_pairs"] == 2
    h = r["human"]                                                    # 자동 2 를 뺀 10 짝
    # A: 실제5 가상4 판별불가1 / B: 실제5 가상3 판별불가2 → p_e = 0.39, κ = (0.8 − 0.39)/0.61 = 41/61
    assert h["n"] == 10 and h["agreement"] == pytest.approx(0.8) and h["kappa"] == pytest.approx(41 / 61)
    assert r["low_n"] is True
    assert ag.compare_origins([], ob)["compared"] is False


def test_origin_invalid_value_counted():
    r = ag.compare_origins([O(cid(1), "real"), O(cid(2), "bot")], [O(cid(1), "real"), O(cid(2), "real")])
    assert r["n"] == 1 and r["judged"]["invalid_pairs"] == 1


# ============================================================================== 표본에서 빠진 판정 (D-12 2)
def S(s, e, c):
    return {"start_ms": s * 1000, "end_ms": e * 1000, "category": c}


def test_split_current_sample_by_segments_whole_and_block_scope():
    reacts = [RR(cid(1), "direct", eid=uid(22, 1), t=50_000, entry="from_sample"),
              RR(cid(2), "direct", eid=uid(22, 2), t=95_000, entry="from_sample"),      # 경계 100초 ±10초 완충
              RR(cid(3), "direct", eid=uid(22, 3), t=150_000, entry="from_sample"),     # 게임 구간
              RR(cid(4), "direct", eid=uid(22, 4), t=150_000, entry="from_utterance")]  # ② 라벨은 남는다
    missed = [M(cid(3), False, "C", t=150_000), M(cid(5), False, "C", scope="extra", t=150_000)]
    segs = [S(0, 100, "just_chatting"), S(100, 600, "game")]
    r = ag.split_current_sample(reacts, missed, segs)
    assert r["method"] == "segments" and r["dropped_reactions"] == 2 and r["dropped_missed"] == 1
    assert [x["body"]["chat_id"] for x in r["reactions"]] == [cid(1), cid(4)]
    # 블록 범위: 블록 가장자리(600초)는 경계가 아니다 → 605초는 남는다. 범위 없이 보면 600초가 빈틈과 맞닿은 경계라 빠진다
    blk = [RR(cid(7), "likely", eid=uid(22, 7), t=605_000, entry="from_sample")]
    segs2 = [S(600, 1200, "just_chatting")]
    assert ag.split_current_sample(blk, [], segs2, scope=[(600_000, 1_200_000)])["dropped_reactions"] == 0
    assert ag.split_current_sample(blk, [], segs2)["dropped_reactions"] == 1
    # current_sample 을 주면 그것으로 본다 · 구간표가 없으면 가르지 않는다
    g = ag.split_current_sample(reacts, missed, segs, current_sample=[cid(3)])
    assert g["method"] == "given" and g["dropped_reactions"] == 2      # cid1·cid2 빠짐, cid4 는 ② 라벨
    assert ag.split_current_sample(reacts, missed, [])["method"] == "none"


def test_build_agreement_drops_out_of_sample_and_reports_count():
    seg_a = [S(0, 600, "just_chatting")]
    seg_b = [S(0, 100, "just_chatting"), S(100, 600, "game")]        # B 가 구간을 고침
    ra = [RR(cid(i), "direct", eid=uid(11, i), t=i * 30_000, entry="from_sample") for i in range(1, 6)]
    rb = [RR(cid(i), "direct", eid=uid(22, i), t=i * 30_000, entry="from_sample") for i in range(1, 6)]
    side = {"session_kind": "primary", "machine_id": MID_1, "scope": None, "view_exposed": [],
            "analysis_exposed": False, "engine_author": False}
    res = ag.build_agreement({**side, "session_id": "L01-P-20261021-ab12", "labeler_id": "L01", "segments": seg_a,
                              "reactions": ra},
                             {**side, "session_id": "L02-R2-20261021-cd34", "labeler_id": "L02", "segments": seg_b,
                              "reactions": rb}, adjudicator_is_labeler=True)
    # B: 30·60초는 저스트채팅 안(완충 90~110초 밖), 90초는 완충, 120·150초는 게임 → 3건 빠짐
    assert res["samples"]["dropped_from_sample"]["b"] == 3 and res["samples"]["dropped_from_sample"]["a"] == 0
    assert res["samples"]["judged"]["both"] == 2 and res["samples"]["judged"]["a_only"] == 3
    text = "\n".join(res["interpretation"])
    assert "08 D-12 2" in text and "조정자 = 라벨러" in text
    assert res["header"]["adjudicator_is_labeler"] is True
    assert {"links", "continuation", "missed", "origin"} <= set(res)


# ============================================================================== app: 파일로 만든 보고서
class _Ids:
    def __init__(self, start):
        self.n = start

    def __call__(self):
        self.n += 1
        return f"{self.n:08x}-0000-4000-8000-{self.n:012x}"


def _open_line(sid, kind, scope=None):
    return {"v": 1, "id": "00000000-0000-4000-8000-00000000ffff", "seq": 1, "kind": "session.open",
            "session_id": sid, "labeler_id": sid.split("-")[0], "created_at": "2026-10-21T09:00:00+09:00",
            "app_version": "0.0.1",
            "body": {"project_id": PID, "session_kind": kind, "session_created_at": "2026-10-21T09:00:00+09:00",
                     "machine_id": MID_1, "machine4": "aaaa", "guide_version": "0.1", "guide_doc_sha256": SHA_T,
                     "thresholds_sha256": SHA_T, "scope": scope or {"type": "whole"},
                     "blind": {"to_ai": True, "to_other_sessions": True, "to_origin": True,
                               "nickname_masked_default": True}, "dataset_split": "dev"}}


def _lines(sid, kind, items, start):
    """items: [{kind, body, entity_id?}] — entity_id 가 있으면 그대로 쓴다(반응·이어짐 연결용)."""
    ids = _Ids(0x100000 + start)                  # uid() 와 겹치지 않는 자리
    lines = [_open_line(sid, kind)]
    lines[0]["id"] = ids()
    for seq, it in enumerate(items, start=2):
        i = ids()
        eid = it.get("entity_id") or i
        body = dict(it["body"])
        lines.append({"v": 1, "id": i, "seq": seq, "kind": it["kind"], "session_id": sid, "entity_id": eid,
                      "supersedes": None, "body": body})
    return lines


def _write_and_lock(root, sid, lines):
    name = f"{sid}.{project.machine4_of(MID_1)}.jsonl"
    p = os.path.join(root, "labels", name)
    jsonl.write_all(p, lines)
    with open(p, "rb") as f:
        data = f.read()
    lk = {"session_id": sid, "project_id": PID, "locked_at": "2026-10-21T10:00:00+09:00", "locked_by": sid.split("-")[0],
          "app_version": "0.0.1",
          "files": [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "last_seq": len(lines)}],
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
    jsonl.write_all(os.path.join(p.root, "chat", "messages.jsonl"),
                    [{"chat_id": cid(1), "t_ms": 1000, "text": "합성본문표식확장", "nickname_masked": NICK_SECRET}])
    return {"root": p.root, "tmp": tmp_path}


def _seg(s, e, c):
    return {"kind": "segment", "body": {**S(s, e, c), "confidence": "certain", "rationale": RATIONALE_SECRET}}


def test_report_labeling_pair_has_new_sections_and_old_keys(proj):
    root = proj["root"]
    ra, rb, ua, ub = _link_sets()
    seg = [_seg(0, 1200, "just_chatting")]
    ma, mb = _missed_sets(base=100)          # 연결 시험 반응(직접·높음, c1~c13)과 겹치지 않는 채팅
    ca, cb = [C(uid(11, 1), "short", span=30)], [C(uid(22, 1), "short", span=30)]
    sa, sb = "L01-P-20261021-ab12", "L02-R2-20261021-cd34"
    _write_and_lock(root, sa, _lines(sa, "primary", seg + ua + ra + ca + ma, 0))
    _write_and_lock(root, sb, _lines(sb, "relabel_second", seg + ub + rb + cb + mb, 50_000))
    res = ar.build_report(root, sa, sb, now=NOW, created_by="L01")
    rep = res.report
    # 앞 판 칸은 그대로 (하위 호환)
    for k in ("schema", "schema_version", "report_id", "inputs", "header", "segments", "samples", "gates",
              "interpretation", "warnings", "not_computed"):
        assert k in rep
    assert rep["schema_version"] == 1 and rep["format_rev"] == ar.FORMAT_REV == 2
    assert rep["links"]["f1"] == pytest.approx(18 / 22)
    assert rep["continuation"]["n"] == 1 and rep["continuation"]["kappa"] is None   # 한 값뿐 → p_e = 1
    assert rep["missed"]["candidate"]["kappa"] == pytest.approx(31 / 51)
    assert rep["origin"]["compared"] is False
    h = rep["header"]
    assert h["adjudicator_is_labeler"] is True and h["adjudicator_source"] == "inferred_created_by"
    assert h["pair_family"] == "labeling"
    assert any("연결 발화를 찾지 못한" in w for w in res.warnings)
    md = res.markdown
    assert "조정자 = 라벨러" in md and "연결 F1" in md and "놓친 채팅 후보" in md and "08 D-12 2" in md
    blob = json.dumps(rep, ensure_ascii=False) + md
    for secret in (RATIONALE_SECRET, NICK_SECRET, "합성본문표식확장", MID_1):
        assert secret not in blob
    # 플래그가 추정을 이긴다 · 만든 사람이 없으면 표시하지 않는다
    assert ar.build_report(root, sa, sb, now=NOW, created_by="L01",
                           adjudicator_is_labeler=False).report["header"]["adjudicator_is_labeler"] is False
    none = ar.build_report(root, sa, sb, now=NOW).report["header"]
    assert none["adjudicator_is_labeler"] is None and none["adjudicator_source"] == "unknown"


def test_report_origin_pair_and_mixed_pair_refused(proj):
    root = proj["root"]
    oa, ob = _origin_sets()
    sa, sb = "L01-OR-20261021-aa11", "L02-OR-20261021-bb22"
    _write_and_lock(root, sa, _lines(sa, "origin_review", oa, 0))
    _write_and_lock(root, sb, _lines(sb, "origin_review", ob, 50_000))
    res = ar.build_report(root, sa, sb, now=NOW, adjudicator_is_labeler=True)
    og = res.report["origin"]
    assert res.report["header"]["pair_family"] == "origin"
    assert og["kappa"] == pytest.approx(19 / 27) and og["human"]["kappa"] == pytest.approx(41 / 61)
    assert og["auto_pairs"] == 2
    md = res.markdown
    assert "## 출처" in md and "자동(기록 대조) 짝 2건" in md and "조정자 = 라벨러" in md
    assert "## 활동 구간" not in md                                    # 출처 판정끼리는 출처만
    assert not any("재라벨 계획 없이" in w for w in res.warnings)
    with pytest.raises(ar.ReportError):
        ar.build_report(root, "L01-P-20261021-ab12", sa, now=NOW)
    assert ar.pair_family(sa, sb) == "origin" and ar.pair_family("L01-P-20261021-ab12", sa) is None


def test_old_report_without_new_keys_still_renders(proj):
    """앞 판(format_rev 없음) report.json 도 render_markdown 이 그린다."""
    root = proj["root"]
    seg = [_seg(0, 600, "just_chatting")]
    sa, sb = "L01-P-20261021-ab12", "L02-R2-20261021-cd34"
    _write_and_lock(root, sa, _lines(sa, "primary", seg, 0))
    _write_and_lock(root, sb, _lines(sb, "relabel_second", seg, 50_000))
    rep = ar.build_report(root, sa, sb, now=NOW).report
    old = {k: v for k, v in rep.items() if k not in ("links", "continuation", "missed", "origin", "format_rev")}
    old["header"] = {k: v for k, v in rep["header"].items()
                     if k not in ("adjudicator_is_labeler", "adjudicator_source", "pair_family")}
    old["samples"] = {k: v for k, v in rep["samples"].items() if k != "dropped_from_sample"}
    md = ar.render_markdown(old)
    assert "## 표본 판정" in md and "연결 F1" not in md


def test_tool_prints_new_lines_and_flag(proj, capsys):
    from tools import agreement_report as tool
    root = proj["root"]
    oa, ob = _origin_sets()
    sa, sb = "L01-OR-20261021-aa11", "L02-OR-20261021-bb22"
    _write_and_lock(root, sa, _lines(sa, "origin_review", oa, 0))
    _write_and_lock(root, sb, _lines(sb, "origin_review", ob, 50_000))
    out = str(proj["tmp"] / "out")
    assert tool.main(["--project", root, "--a", sa, "--b", sb, "--out", out, "--adjudicator-is-labeler"]) == 0
    printed = capsys.readouterr().out
    assert "출처 n 12" in printed and "조정자=라벨러 True" in printed and RATIONALE_SECRET not in printed
