"""app/core/sampling.py 시험 — sha256-tau-v2(기본)·v1(재현) (06 2장, 03 6-13·15장, 02 3-3, 08 D-10 1·3).

합성 데이터만 쓴다(채팅 본문은 'x' 같은 자리표시). 실제 채팅·닉네임은 넣지 않는다.
"""
import copy
import hashlib
import json
import os

import pytest

from app.core import sampling as sp
from app.core.segments import SegmentTimeline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = "00112233445566778899aabbccddeeff"
SM = f"{SEED}|1"
SEG_EID = "7f3c2a1b-0000-4000-8000-000000000001"
SEG_VID = "7f3c2a1b-0000-4000-8000-000000000002"
PROJECT_ID = "11111111-2222-4333-8444-555555555555"
SHA_T = "a" * 64
SHA_M = "b" * 64


def cid(i: int) -> str:
    return "c%020x" % i


def msg(i: int, t, kind="text", status="NORMAL"):
    return {"chat_id": cid(i), "t_ms": t, "kind": kind, "status": status, "text": "x"}


def seg(start=0, end=1_000_000):
    return {"session_id": "L01-P-20261021-a1b2", "entity_id": SEG_EID, "version_id": SEG_VID,
            "start_ms": start, "end_ms": end}


def make(pop, **kw):
    args = dict(project_id=PROJECT_ID, source_segment=seg(pop.range_start_ms, pop.range_end_ms),
                guide_version="0.1", thresholds_sha256=SHA_T, sample_seed=SEED, draw_no=1, population=pop,
                messages_sha256=SHA_M, n=100, census_max=100, drawn_at="2026-10-21T14:03:11+09:00",
                drawn_by="L01", app_version="0.1.0")
    args.update(kw)
    return sp.build_manifest(**args)


def manifest_schema():
    from app.store import LabelSchema
    with open(os.path.join(ROOT, "schemas", "sample-manifest.schema.json"), encoding="utf-8") as f:
        return LabelSchema(json.load(f))


# --------------------------------------------------------------------------- 순서 키 (06 2-1)
def test_sort_key_golden_and_independent():
    # 고정값(골든): 알고리즘을 바꾸면 여기가 깨진다 → 이름을 올려야 한다(sha256-tau-v2).
    assert sp.sort_key(SM, cid(0)) == "1aef486198b0fe3e6c754e64b4a4037f219071d084e87216647b400e77c5a57a"
    assert sp.sort_key(SM, cid(1)) == "18479e845f54a37964a5ba7e135d05ee540f2d9dcb83aef492af54ff12c89d5e"
    assert sp.sort_key(SM, "c" + "a" * 20 + "_2") == "84a0bcf1c56f546d9ddd7280ac5ee558019af93fa05a115359f74b23cb874c57"
    # 같은 식을 손으로
    assert sp.sort_key(SM, cid(7)) == hashlib.sha256(f"{SEED}|1|{cid(7)}".encode("utf-8")).hexdigest()


def test_seed_material_rules():
    assert sp.make_seed_material(SEED, 1) == SM
    assert sp.parse_seed_material(f"{SEED}|12") == (SEED, 12)
    for bad in [("ABC", 1), (SEED.upper(), 1), (SEED, 0), (SEED, True), (SEED, "1")]:
        with pytest.raises(sp.SamplingError):
            sp.make_seed_material(*bad)
    # draw_no 가 바뀌면 순서 전체가 바뀐다
    assert sp.sort_key(f"{SEED}|2", cid(0)) != sp.sort_key(SM, cid(0))


def test_ids_sha256_order_free():
    a = sp.ids_sha256([cid(2), cid(1)])
    assert a == sp.ids_sha256([cid(1), cid(2)])
    assert a == hashlib.sha256(f"{cid(1)}\n{cid(2)}".encode()).hexdigest()
    assert sp.ids_sha256([]) == hashlib.sha256(b"").hexdigest()


def test_set_id():
    assert sp.sample_set_id(SEG_EID, 1, 3) == "S-7f3c2a1b-d1-r3"
    assert sp.parse_sample_set_id("S-7f3c2a1b-d2-r10") == ("7f3c2a1b", 2, 10)
    assert sp.series_of("S-7f3c2a1b-d2-r10") == ("7f3c2a1b", 2)
    with pytest.raises(sp.SamplingError):
        sp.parse_sample_set_id("S-7f3c2a1b-d2")


# --------------------------------------------------------------------------- 06 2-5 예시 (골든)
EX_KEYS = {"c01": "9f21", "c02": "0a13", "c03": "1b77", "c04": "3c05", "c05": "e410",
           "c06": "2d98", "c07": "7a3e", "c08": "5512", "c09": "1a00"}


def ex_key(_sm, c):
    return EX_KEYS[c].ljust(64, "0")


def K(c):
    return EX_KEYS[c].ljust(64, "0")


def test_doc_example_step1_initial():
    eligible = ["c01", "c04", "c05", "c06", "c07", "c08"]     # c02 후원, c03 완충은 적격 밖
    d = sp.draw(eligible, SM, 3, 0, key_fn=ex_key)
    assert d.tau_key == K("c08")
    assert d.items == ("c06", "c04", "c08")
    assert not d.census and not d.tau_raised_by_prev
    assert [d.ranks[c] for c in d.items] == [1, 2, 3]


def test_doc_example_step2_origin_extend():
    eligible = ["c01", "c04", "c05", "c06", "c07", "c08"]
    d = sp.draw(eligible, SM, 3, 0, key_fn=ex_key)
    real = [c for c in eligible if c != "c04"]                  # c04 가 가상
    tau_real, extra, capped = sp.origin_extend(d, real, 0.5)
    assert tau_real == K("c07") and extra == ("c07",) and not capped
    assert d.items == ("c06", "c04", "c08")                    # 전체 통계 표본은 그대로


def test_doc_example_step3_boundary_moves_c08_into_buffer():
    eligible = ["c01", "c04", "c05", "c06", "c07"]
    d = sp.draw(eligible, SM, 3, 0, prev_tau_key=K("c08"), key_fn=ex_key)
    assert d.tau_key == K("c07")
    assert set(d.items) == {"c06", "c04", "c07"}


def test_doc_example_step4_new_chat_tau_does_not_drop():
    eligible = ["c01", "c04", "c05", "c06", "c07", "c08", "c09"]
    d = sp.draw(eligible, SM, 3, 0, prev_tau_key=K("c08"), key_fn=ex_key)
    assert d.tau_key == K("c08") and d.tau_raised_by_prev
    assert d.items == ("c09", "c06", "c04", "c08")             # 4개, 기존 3개 유지


def test_origin_extend_cap():
    ids = [cid(i) for i in range(50)]
    d = sp.draw(ids, SM, 10, 0)
    real = [c for c in ids if c not in d.items]                 # 표본 10개가 전부 가상이라 치면
    tau_real, extra, capped = sp.origin_extend(d, real, 0.5)
    assert capped and len(extra) == 5
    assert tau_real == d.keys[extra[-1]]


# --------------------------------------------------------------------------- 크기·전수·불감소 (06 2-3, 2-6)
def test_exactly_n_on_first_draw():
    ids = [cid(i) for i in range(1000)]
    d = sp.draw(ids, SM, 100, 100)
    assert len(d.items) == 100 and not d.census
    ks = sorted(d.keys.values())
    assert d.tau_key == ks[99]
    assert all(d.keys[c] <= d.tau_key for c in d.items)
    # 입력 순서와 무관
    assert sp.draw(list(reversed(ids)), SM, 100, 100).items == d.items


def test_census_switch():
    ids = [cid(i) for i in range(100)]
    d = sp.draw(ids, SM, 100, 100)
    assert d.census and len(d.items) == 100 and d.tau_key == max(d.keys.values())
    d2 = sp.draw(ids[:5], SM, 100, 100)
    assert d2.census and len(d2.items) == 5
    d3 = sp.draw([cid(i) for i in range(101)], SM, 100, 100)
    assert not d3.census and len(d3.items) == 100
    # census_max 를 120 으로 올리면(v0.2 후보) 110개는 전수
    d4 = sp.draw([cid(i) for i in range(110)], SM, 100, 120)
    assert d4.census and len(d4.items) == 110


def test_census_max_below_n_takes_all():
    d = sp.draw([cid(i) for i in range(50)], SM, 100, 10)
    assert not d.census and len(d.items) == 50


def test_empty_eligible():
    d = sp.draw([], SM, 100, 100)
    assert d.tau_key is None and d.items == () and d.eligible_size == 0


def test_tau_monotone_random_boundary_changes():
    """경계를 여러 번 바꿔도 τ 는 내려가지 않고, 남아 있는 표본 채팅은 빠지지 않는다."""
    universe = [cid(i) for i in range(600)]
    elig = universe[:400]
    d = sp.draw(elig, SM, 50, 50)
    tau, kept = d.tau_key, set(d.items)
    for lo, hi in [(20, 380), (0, 450), (100, 600), (150, 300)]:
        elig = universe[lo:hi]
        d = sp.draw(elig, SM, 50, 50, prev_tau_key=tau)
        assert d.tau_key >= tau
        assert (kept & set(elig)) <= set(d.items)
        assert len(d.items) >= min(50, len(elig))
        tau, kept = d.tau_key, set(d.items)


def test_duplicate_ids_rejected():
    with pytest.raises(sp.SamplingError):
        sp.draw([cid(1), cid(1)], SM, 1, 0)
    with pytest.raises(sp.SamplingError):
        sp.draw([cid(1)], SM, 0, 0)
    with pytest.raises(sp.SamplingError):
        sp.draw([cid(1)], SM, 1, 0, prev_tau_key="xyz")


# --------------------------------------------------------------------------- 모집단·적격 (06 2-2)
def test_population_exclusion_order():
    msgs = [msg(1, 5_000), msg(2, 6_000, "donation"), msg(3, None), msg(4, 99_000),  # 4: 완충 안
            msg(5, 150_000, "system"), msg(6, 120_000), msg(7, 300_000),           # 7: 범위 밖
            msg(8, 101_000.5), msg(9, 109_999), msg(10, 110_000)]
    pop = sp.build_population(msgs, 0, 200_000, [(90_000, 110_000)])
    assert pop.excluded_non_text == 2
    assert pop.excluded_invalid_time == 2          # None, 실수
    assert pop.excluded_buffer == 2                # 99_000, 109_999 (끝은 반열림)
    assert pop.population_ids == [cid(1), cid(4), cid(9), cid(10), cid(6)]
    assert pop.eligible_ids == [cid(1), cid(10), cid(6)]


def test_population_with_segment_timeline_buffers():
    n = iter(["%08x-0000-4000-8000-%012x" % (i, i) for i in range(1, 50)])
    tl = SegmentTimeline(600_000, lambda: next(n))
    tl.add_boundary(200_000)
    tl.add_boundary(400_000)
    msgs = [msg(i + 100, 200_000 + i * 1_000) for i in range(-15, 15)]
    pop = sp.build_population(msgs, 200_000, 400_000, tl.buffer_ranges(10_000))
    # 구간 [200000,400000): 들어온 것은 i=0..14, 그중 완충 [190000,210000) 은 i=0..9
    assert len(pop.population) == 15 and pop.excluded_buffer == 10


def test_population_rejects_bad_ids():
    with pytest.raises(sp.SamplingError):
        sp.build_population([msg(1, 1), msg(1, 2)], 0, 10)
    with pytest.raises(sp.SamplingError):
        sp.build_population([{"chat_id": "bad", "t_ms": 1, "kind": "text"}], 0, 10)
    with pytest.raises(sp.SamplingError):
        sp.build_population([], 10, 10)


def test_statuses_filter():
    msgs = [msg(1, 1, status="NORMAL"), msg(2, 2, status="CBOTBLIND")]
    pop = sp.build_population(msgs, 0, 10, statuses=["NORMAL"])
    assert pop.eligible_ids == [cid(1)] and pop.excluded_non_text == 1


# --------------------------------------------------------------------------- 매니페스트·스키마·재현 검증
def synth(n_text=300, start=0, end=600_000):
    msgs = []
    for i in range(n_text):
        msgs.append(msg(i, start + i * (end - start) // n_text))
    msgs.append(msg(10_000, start + 1_000, "donation"))
    msgs.append(msg(10_001, None))
    return msgs


def test_manifest_shape_and_schema():
    msgs = synth()
    bufs = [(590_000, 600_000)]
    pop = sp.build_population(msgs, 0, 600_000, bufs)
    m = make(pop)
    assert m["sample_set_id"] == "S-7f3c2a1b-d1-r1" and m["sample_rev"] == 1
    assert m["previous_sample_rev"] is None and m["changes"] is None and m["reason"] == "initial"
    assert m["algorithm"] == "sha256-tau-v2"
    assert m["excluded"] == {"non_text": 1, "invalid_time": 1, "buffer": pop.excluded_buffer, "out_of_media": 0}
    # coverage 를 넘기지 않았으면 'coverage 없음' 을 명시한다
    assert m["media"] == {"coverage": None, "pre_roll_ms": 10_000, "post_roll_ms": 30_000}
    assert m["eligible"]["size"] == m["population"]["size"] - m["excluded"]["buffer"]
    assert len(m["items"]) == 100 and all(it["role"] == "base" for it in m["items"])
    ts = [it["t_ms"] for it in m["items"]]
    assert ts == sorted(ts) and [it["order"] for it in m["items"]] == list(range(1, 101))
    assert manifest_schema().check(m) == []
    # 순수: 같은 입력이면 같은 dict
    assert make(pop) == m


def test_manifest_key_order_presentation():
    pop = sp.build_population(synth(), 0, 600_000)
    m = make(pop, presentation_order="key")
    keys = [it["sort_key"] for it in m["items"]]
    assert keys == sorted(keys) and [it["rank"] for it in m["items"]] == list(range(1, 101))


def test_verify_roundtrip_ok_and_tamper():
    msgs = synth()
    bufs = [(0, 5_000)]
    pop = sp.build_population(msgs, 0, 600_000, bufs)
    m = make(pop)
    v = sp.verify_manifest(m, msgs, bufs, messages_sha256=SHA_M, census_max=100)
    assert v.ok, v.problems
    assert v.summary() == "재현 검증 통과"
    # census_max 없이도(매니페스트 census 를 따름)
    assert sp.verify_manifest(m, msgs, bufs).ok

    bad = copy.deepcopy(m)
    bad["items"][0]["chat_id"] = cid(299)
    assert not sp.verify_manifest(bad, msgs, bufs).ok

    bad = copy.deepcopy(m)
    bad["tau_key"] = "f" * 64
    assert any("τ" in x for x in sp.verify_manifest(bad, msgs, bufs).problems)

    # 채팅 파일이 바뀜
    assert not sp.verify_manifest(m, msgs, bufs, messages_sha256="c" * 64).ok
    # 구간표(완충)가 다르면 실패
    assert not sp.verify_manifest(m, msgs, [(0, 50_000)]).ok
    # 채팅 하나가 더 들어오면 실패
    assert not sp.verify_manifest(m, msgs + [msg(20_000, 10_001)], bufs).ok
    # 순서를 섞으면 실패
    bad = copy.deepcopy(m)
    bad["items"][0]["order"], bad["items"][1]["order"] = 2, 1
    bad["items"][0], bad["items"][1] = bad["items"][1], bad["items"][0]
    assert not sp.verify_manifest(bad, msgs, bufs).ok
    # 다른 알고리즘
    bad = copy.deepcopy(m)
    bad["algorithm"] = "sha256-sort-v1"
    assert not sp.verify_manifest(bad, msgs, bufs).ok


def test_boundary_change_new_rev():
    msgs = synth(400)
    pop1 = sp.build_population(msgs, 0, 600_000, [])
    m1 = make(pop1)
    # 경계를 옮겨 앞 1/4 이 빠지고 뒤로 늘어남 → 새 구간 범위의 새 판
    msgs2 = msgs + [msg(5_000 + i, 600_000 + i * 1000) for i in range(100)]
    pop2 = sp.build_population(msgs2, 150_000, 700_000, [])
    s2 = seg(150_000, 700_000)
    m2 = make(pop2, source_segment=s2, previous=m1)
    assert m2["sample_set_id"] == "S-7f3c2a1b-d1-r2" and m2["previous_sample_rev"] == 1
    assert m2["reason"] == "boundary_changed"
    assert m2["tau_key"] >= m1["tau_key"]
    kept = {it["chat_id"] for it in m1["items"]} & set(pop2.eligible_ids)
    assert kept <= {it["chat_id"] for it in m2["items"]}
    removed = {r["chat_id"] for r in m2["changes"]["removed"]}
    assert removed == {it["chat_id"] for it in m1["items"]} - set(pop2.eligible_ids)
    assert manifest_schema().check(m2) == []
    assert sp.verify_manifest(m2, msgs2, [], previous=m1).ok
    # 앞 판 정보 없이는 불감소를 확인할 수 없다
    assert not sp.verify_manifest(m2, msgs2, []).ok
    # 다른 계열을 previous 로 쓰면 거부
    other = dict(m1, sample_set_id="S-00000000-d1-r1")
    with pytest.raises(sp.SamplingError):
        make(pop2, source_segment=s2, previous=other)
    with pytest.raises(sp.SamplingError):
        make(pop2, source_segment=s2, previous=m1, guide_version="0.2")


def test_inherited_tau_new_series():
    pop = sp.build_population(synth(400), 0, 600_000)
    m = make(pop, inherited_tau_keys=["f" * 63 + "0"], reason="split from S-7f3c2a1b-d1-r1")
    assert m["tau_key"] == "f" * 63 + "0" and len(m["items"]) > 100
    assert sp.verify_manifest(m, synth(400), [], prev_tau_key="f" * 63 + "0").ok


def test_manifest_census_and_empty():
    pop = sp.build_population(synth(30), 0, 600_000)
    m = make(pop)
    assert m["census"] and len(m["items"]) == 30
    assert manifest_schema().check(m) == []
    pop0 = sp.build_population([msg(1, 1, "donation")], 0, 600_000)
    m0 = make(pop0)
    assert m0["tau_key"] is None and m0["items"] == [] and m0["eligible"]["size"] == 0
    assert manifest_schema().check(m0) == []
    assert sp.verify_manifest(m0, [msg(1, 1, "donation")], []).ok


def test_manifest_origin_extend_role():
    msgs = synth(400)
    pop = sp.build_population(msgs, 0, 600_000)
    base = make(pop)
    virtual = {it["chat_id"] for it in base["items"][:10]}
    real = [c for c in pop.eligible_ids if c not in virtual]
    m = make(pop, real_chat_ids=real, max_extend_ratio=0.5)
    roles = [it["role"] for it in m["items"]]
    assert roles.count("base") == 100 and roles.count("origin_extend") == 10
    assert m["tau_real_key"] > m["tau_key"]
    assert manifest_schema().check(m) == []
    assert sp.verify_manifest(m, msgs, [], real_chat_ids=real, max_extend_ratio=0.5).ok
    v = sp.verify_manifest(m, msgs, [])
    assert not v.ok and any("origin_extend" in x for x in v.problems)


def test_build_manifest_rejects():
    pop = sp.build_population(synth(10), 0, 600_000)
    with pytest.raises(sp.SamplingError):
        make(pop, source_segment=seg(0, 500_000))          # 범위 불일치
    with pytest.raises(sp.SamplingError):
        make(pop, thresholds_sha256="nothex")
    with pytest.raises(sp.SamplingError):
        make(pop, presentation_order="random")


# --------------------------------------------------------------------------- 판정 상태·진행
def test_item_states_and_progress():
    pop = sp.build_population(synth(300), 0, 600_000)
    m = make(pop)
    a, b = m["items"][0]["chat_id"], m["items"][1]["chat_id"]
    reactions = [
        {"kind": "reaction", "body": {"chat_id": a, "entry": "from_sample",
                                      "sample_ref": {"sample_set_id": "S-7f3c2a1b-d1-r1", "order": 1}}},
        # 같은 계열의 다른 판을 가리켜도 같은 계열이면 센다
        {"chat_id": b, "entry": "from_sample", "sample_ref": {"sample_set_id": "S-7f3c2a1b-d1-r2", "order": 2}},
        # ② 반응이 있는 표본 채팅은 판정함으로 센다 (08 D-10 4: ③ 은 그 라벨을 고친다)
        {"chat_id": m["items"][2]["chat_id"], "entry": "from_utterance", "sample_ref": None},
        # 표본 밖 채팅의 ② 반응은 out_of_sample 이 아니다 (② 라벨일 뿐)
        {"chat_id": cid(66_666), "entry": "from_utterance", "sample_ref": None},
        # 다른 계열(draw 2·분할 전 원 구간)의 판정도 그 채팅의 현재 유효 라벨이라 판정함 (D-10 4, 06 2-6)
        {"chat_id": m["items"][3]["chat_id"], "entry": "from_sample",
         "sample_ref": {"sample_set_id": "S-7f3c2a1b-d2-r1", "order": 4}},
        # 다른 계열 판정이 이 표본에 없어도 out_of_sample 이 아니다 (그 계열의 판정일 뿐)
        {"chat_id": cid(55_555), "entry": "from_sample", "sample_ref": {"sample_set_id": "S-aaaaaaaa-d1-r1", "order": 3}},
        # 표본에서 빠진 채팅의 판정 → out_of_sample
        {"chat_id": cid(77_777), "entry": "from_sample", "sample_ref": {"sample_set_id": "S-7f3c2a1b-d1-r1", "order": 9}},
    ]
    states, out = sp.item_states(m, reactions)
    assert states[a] == sp.JUDGED and states[b] == sp.JUDGED
    assert states[m["items"][2]["chat_id"]] == sp.JUDGED
    assert states[m["items"][3]["chat_id"]] == sp.JUDGED
    assert states[m["items"][4]["chat_id"]] == sp.PENDING
    assert sum(v == sp.JUDGED for v in states.values()) == 4
    assert out == (cid(77_777),)
    pr = sp.progress(m, states)
    assert (pr.done, pr.total, pr.base, pr.extra) == (4, 100, 100, 0)
    assert pr.denominator_text() == "100"
    m3 = dict(m, items=m["items"] + [dict(m["items"][0], chat_id=cid(88_000 + i), order=101 + i) for i in range(3)])
    assert sp.progress(m3, {}).denominator_text() == "100 + 3"


def test_latest_of():
    a = {"sample_set_id": "S-7f3c2a1b-d1-r1", "sample_rev": 1}
    b = {"sample_set_id": "S-7f3c2a1b-d1-r2", "sample_rev": 2}
    c = {"sample_set_id": "S-7f3c2a1b-d2-r1", "sample_rev": 1}
    got = sp.latest_of([b, a, c])
    assert got[("7f3c2a1b", 1)] is b and got[("7f3c2a1b", 2)] is c


# --------------------------------------------------------------------------- v1 골든 · v2 완충 규칙 (08 D-10 1)
# 같은 분류(저스트채팅)끼리 맞닿은 경계 200초 + 분류가 바뀌는 경계 400초(→ 게임). 표본 구간은 [200초, 400초).
GOLD_RECS = [{"start_ms": 0, "end_ms": 200_000, "category": "just_chatting"},
             {"start_ms": 200_000, "end_ms": 400_000, "category": "just_chatting"},
             {"start_ms": 400_000, "end_ms": 600_000, "category": "game"}]
# 이 값은 sha256-tau-v2 를 넣기 전 코드(v1 만 있던 판)로 같은 입력을 계산해 고정했다. 바뀌면 v1 재현이 깨진 것이다.
GOLD_V1_MANIFEST_SHA = "22d86ad32463ed9479faf3853d3b62c2768fa995ac7fce8732e22fb4b0181c87"
GOLD_V1_TAU = "1f21a51b767f10b9cd53ce9fe9fd8e80d5fbd8206e80cd66f128ca21a8779746"
GOLD_V1_ITEMS_SHA = "4a6e1c248a1e77916b4d74adb304b1d4aa151c720070a8191c5b0e0a5825167e"
GOLD_V1_ELIG_SHA = "0efb01788b76723602c59b7985ad023ee50805ced8af469fffc4a7279d80d7f3"


def gold_msgs():
    return [msg(i, i * 1000 + 500) for i in range(600)]


def gold_make(pop, **kw):
    return make(pop, source_segment=seg(200_000, 400_000), n=20, census_max=10, **kw)


def gold_bufs(rule):
    from app.core.segments import record_buffers
    return record_buffers(GOLD_RECS, 600_000, 10_000, rule=rule)


def _dict_sha(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def test_v1_golden_manifest_reproduces():
    from app.core.segments import BUFFER_RULE_ALL
    assert sp.buffer_rule("sha256-tau-v1") == BUFFER_RULE_ALL
    bufs = gold_bufs(BUFFER_RULE_ALL)
    assert bufs == [(190_000, 210_000), (390_000, 410_000)]
    pop = sp.build_population(gold_msgs(), 200_000, 400_000, bufs)
    m = gold_make(pop, algorithm="sha256-tau-v1")
    # v1 모양은 예전과 바이트까지 같다 (media·out_of_media 칸 없음)
    assert "media" not in m and "out_of_media" not in m["excluded"]
    assert m["tau_key"] == GOLD_V1_TAU
    assert sp.ids_sha256([it["chat_id"] for it in m["items"]]) == GOLD_V1_ITEMS_SHA
    assert m["eligible"] == {"size": 180, "chat_ids_sha256": GOLD_V1_ELIG_SHA}
    assert m["excluded"] == {"non_text": 0, "invalid_time": 0, "buffer": 20}
    assert _dict_sha(m) == GOLD_V1_MANIFEST_SHA
    assert manifest_schema().check(m) == []
    # 재현 검증: 완충을 목록으로 주든, 규칙을 받는 함수로 주든 v1 규칙(모든 경계)으로 통과
    assert sp.verify_manifest(m, gold_msgs(), bufs).ok
    v = sp.verify_manifest(m, gold_msgs(), lambda rule: gold_bufs(rule))
    assert v.ok, v.problems
    # v2 규칙 완충을 v1 매니페스트에 쓰면 실패한다(규칙이 실제로 다르다)
    assert not sp.verify_manifest(m, gold_msgs(), gold_bufs("category_change")).ok
    # v1 에 v2 칸을 붙이면 실패
    bad = dict(m, media={"coverage": None, "pre_roll_ms": 10_000, "post_roll_ms": 30_000})
    assert not sp.verify_manifest(bad, gold_msgs(), bufs).ok
    assert manifest_schema().check(bad) != []


def test_v2_only_category_change_boundary_buffered():
    bufs = gold_bufs("category_change")
    assert bufs == [(390_000, 410_000)]                     # 같은 분류 경계 200초에는 완충 없음
    pop = sp.build_population(gold_msgs(), 200_000, 400_000, bufs)
    m = gold_make(pop)
    assert m["algorithm"] == "sha256-tau-v2"
    assert m["excluded"] == {"non_text": 0, "invalid_time": 0, "buffer": 10, "out_of_media": 0}
    assert m["eligible"]["size"] == 190 and len(m["items"]) == 20
    assert manifest_schema().check(m) == []
    assert sp.verify_manifest(m, gold_msgs(), lambda rule: gold_bufs(rule)).ok
    # v1 규칙 완충으로 검증하면 실패
    assert not sp.verify_manifest(m, gold_msgs(), gold_bufs("all_boundaries")).ok


def test_series_keeps_algorithm():
    msgs = synth(400)
    pop = sp.build_population(msgs, 0, 600_000)
    m1 = make(pop, algorithm="sha256-tau-v1")
    pop2 = sp.build_population(msgs, 100_000, 600_000)
    m2 = make(pop2, source_segment=seg(100_000, 600_000), previous=m1)     # 알고리즘을 안 주면 앞 판을 따른다
    assert m2["algorithm"] == "sha256-tau-v1" and "media" not in m2
    assert sp.verify_manifest(m2, msgs, [], previous=m1).ok
    with pytest.raises(sp.SamplingError):
        make(pop2, source_segment=seg(100_000, 600_000), previous=m1, algorithm="sha256-tau-v2")
    with pytest.raises(sp.SamplingError):
        make(pop, algorithm="sha256-tau-v9")
    # v1 은 영상 범위를 쓰지 않는다
    popc = sp.build_population(msgs, 0, 600_000, coverage=[(0, 600_000)])
    with pytest.raises(sp.SamplingError):
        make(popc, algorithm="sha256-tau-v1")


# --------------------------------------------------------------------------- 영상 밖 (08 D-10 3)
def test_normalize_coverage_and_window():
    assert sp.normalize_coverage(None) is None
    assert sp.normalize_coverage([]) == ()
    assert sp.normalize_coverage([[50, 60], (0, 10), (10, 20), (15, 30)]) == ((0, 30), (50, 60))
    for bad in ([(10, 10)], [(5, 1)], [(0.5, 3)], [(1, 2, 3)], [5]):
        with pytest.raises(sp.SamplingError):
            sp.normalize_coverage(bad)
    cov = sp.normalize_coverage([(100_000, 200_000), (200_000, 300_000), (400_000, 500_000)])
    assert sp.review_window(5_000) == (0, 35_000)
    assert sp.window_in_coverage(110_000, cov)                 # [100000, 140000]
    assert not sp.window_in_coverage(109_999, cov)             # 시작이 영상 앞
    assert sp.window_in_coverage(270_000, cov)                 # 맞닿은 범위는 합쳐서 본다
    assert not sp.window_in_coverage(270_001, cov)             # 끝이 영상 뒤
    assert not sp.window_in_coverage(320_000, cov)             # 빈틈
    assert not sp.window_in_coverage(395_000, cov)             # 두 범위에 걸침(빈틈 포함)
    assert sp.window_in_coverage(5_000, ((0, 35_000),))        # 0 아래는 0 으로 자른다
    assert sp.window_in_coverage(1, None)
    assert not sp.window_in_coverage(150_000, ())


def test_population_out_of_media_counts_first():
    # 영상 [100초, 300초). 확인 범위 −10초 ~ +30초 가 다 들어오려면 110초 ≤ t ≤ 270초.
    msgs = [msg(1, 50_000), msg(2, 105_000), msg(3, 110_000), msg(4, 200_000), msg(5, 205_000),
            msg(6, 265_000), msg(7, 280_000), msg(8, 150_000, "donation"), msg(9, None)]
    pop = sp.build_population(msgs, 0, 400_000, [(195_000, 215_000), (260_000, 290_000)], coverage=[(100_000, 300_000)])
    assert pop.excluded_non_text == 1 and pop.excluded_invalid_time == 1
    assert pop.excluded_out_of_media == 3                      # 50초, 105초, 280초(완충에도 걸리지만 영상 밖으로 센다)
    assert pop.excluded_buffer == 3                            # 200초, 205초, 265초
    assert pop.eligible_ids == [cid(3)]
    assert len(pop.population) == 7                            # 모집단은 그대로(영상 밖도 모집단)
    # 확인 범위 값을 바꿔 넘길 수 있다(기준서 recheck 값)
    pop2 = sp.build_population(msgs, 0, 400_000, coverage=[(100_000, 300_000)], pre_roll_ms=0, post_roll_ms=0)
    assert pop2.excluded_out_of_media == 1
    with pytest.raises(sp.SamplingError):
        sp.build_population(msgs, 0, 400_000, pre_roll_ms=-1)


def test_manifest_records_coverage_and_verifies():
    msgs = synth(600)                                         # 0~600초에 1초 간격
    cov = [(300_000, 450_000), (100_000, 200_000)]
    pop = sp.build_population(msgs, 0, 600_000, [(440_000, 460_000)], coverage=cov)
    m = make(pop)
    assert m["media"] == {"coverage": [[100_000, 200_000], [300_000, 450_000]], "pre_roll_ms": 10_000, "post_roll_ms": 30_000}
    oom = m["excluded"]["out_of_media"]
    assert oom == pop.excluded_out_of_media and oom > 0
    assert m["eligible"]["size"] == m["population"]["size"] - oom - m["excluded"]["buffer"]
    assert manifest_schema().check(m) == []
    t_ok = [it["t_ms"] for it in m["items"]]
    assert all(sp.window_in_coverage(t, sp.normalize_coverage(cov)) for t in t_ok)
    bufs = [(440_000, 460_000)]
    assert sp.verify_manifest(m, msgs, bufs).ok
    assert sp.verify_manifest(m, msgs, bufs, coverage=list(reversed(cov))).ok       # 같은 범위(순서만 다름)
    v = sp.verify_manifest(m, msgs, bufs, coverage=[(100_000, 450_000)])
    assert not v.ok and any("coverage" in x for x in v.problems)
    assert not sp.verify_manifest(m, msgs, bufs, coverage=None).ok                   # 뽑을 때는 있었는데 지금은 없음
    # 매니페스트의 coverage 를 바꾸면 재계산이 달라져 실패
    bad = copy.deepcopy(m)
    bad["media"]["coverage"] = [[0, 600_000]]
    assert not sp.verify_manifest(bad, msgs, bufs).ok
    bad = copy.deepcopy(m)
    bad["excluded"]["out_of_media"] += 1
    assert not sp.verify_manifest(bad, msgs, bufs).ok
    bad = copy.deepcopy(m)
    del bad["media"]
    assert not sp.verify_manifest(bad, msgs, bufs).ok
    assert manifest_schema().check(bad) != []
    bad = copy.deepcopy(m)
    del bad["excluded"]["out_of_media"]
    assert manifest_schema().check(bad) != []


def test_manifest_empty_coverage_all_out_of_media():
    msgs = synth(50)
    pop = sp.build_population(msgs, 0, 600_000, coverage=[])
    m = make(pop)
    assert m["media"]["coverage"] == [] and m["excluded"]["out_of_media"] == 50
    assert m["tau_key"] is None and m["items"] == []
    assert manifest_schema().check(m) == []
    assert sp.verify_manifest(m, msgs, []).ok


def test_v2_records_boundary_buffers_and_reproduces_from_manifest():
    """v2 완충은 이웃 구간 분류에 달려 있어 매니페스트에 boundary_buffers 로 남는다(구간 범위로 자름). 재현 검증은
    그 값으로 다시 계산한다 — 그 뒤 이웃 구간 분류가 바뀌어도 매니페스트 하나로 재현된다(06 2-1)."""
    from app.core.segments import record_buffers
    msgs = synth(600)
    # 표본 구간 [100초, 400초): 앞 이웃은 같은 분류(완충 없음), 뒤 이웃은 다른 분류(완충 390~410초)
    recs = [{"start_ms": 0, "end_ms": 100_000, "category": "just_chatting"},
            {"start_ms": 100_000, "end_ms": 400_000, "category": "just_chatting"},
            {"start_ms": 400_000, "end_ms": 600_000, "category": "game"}]
    bufs = record_buffers(recs, 600_000, 10_000)
    pop = sp.build_population(msgs, 100_000, 400_000, bufs, coverage=[(0, 600_000)])
    assert pop.buffers == ((390_000, 400_000),)              # 구간 범위로 잘랐다(앞 경계 100초에는 없음)
    m = make(pop)
    assert m["boundary_buffers"] == [[390_000, 400_000]]
    assert manifest_schema().check(m) == []
    assert sp.verify_manifest(m, msgs).ok                     # 완충을 넘기지 않아도 매니페스트로 재현
    assert sp.verify_manifest(m, msgs, bufs).ok               # 같은 완충을 넘기면 같다
    # 그 뒤 앞 이웃이 게임으로 바뀜 → 지금 구간표 완충은 다르다: 넘기면 '다르다' 로 알리지만, 안 넘기면 재현은 된다
    recs2 = [dict(recs[0], category="game")] + recs[1:]
    v = sp.verify_manifest(m, msgs, record_buffers(recs2, 600_000, 10_000))
    assert not v.ok and any("경계 완충" in p for p in v.problems)
    assert sp.verify_manifest(m, msgs, lambda rule: record_buffers(recs, 600_000, 10_000, rule=rule)).ok
    # 매니페스트의 완충을 고치면 실패
    bad = copy.deepcopy(m)
    bad["boundary_buffers"] = []
    assert not sp.verify_manifest(bad, msgs).ok
    bad = copy.deepcopy(m)
    del bad["boundary_buffers"]
    assert not sp.verify_manifest(bad, msgs).ok and manifest_schema().check(bad) != []
    bad = copy.deepcopy(m)
    bad["boundary_buffers"] = [[390_000, 410_000]]            # 범위로 자르지 않은 모양
    assert not sp.verify_manifest(bad, msgs).ok
    # v1 매니페스트에는 그 칸이 없어야 한다
    m1 = make(sp.build_population(msgs, 100_000, 400_000, bufs), algorithm=sp.ALGORITHM_V1)
    assert "boundary_buffers" not in m1
    assert manifest_schema().check(dict(m1, boundary_buffers=[])) != []


def test_clip_buffers():
    assert sp.clip_buffers([(0, 50), (40, 70), (90, 200), (300, 400)], 30, 150) == ((30, 70), (90, 150))
    assert sp.clip_buffers([(0, 30), (150, 160)], 30, 150) == ()
    assert sp.clip_buffers([(10, 20), (20, 30)], 0, 100) == ((10, 30),)     # 맞닿은 것은 합친다


def test_item_heads_prefers_same_series_sample_label():
    pop = sp.build_population(synth(300), 0, 600_000)
    m = make(pop)
    a, b = m["items"][0]["chat_id"], m["items"][1]["chat_id"]
    rs = [{"id": "r1", "entity_id": "e1", "body": {"chat_id": a, "entry": "from_utterance"}},
          {"id": "r2", "entity_id": "e2", "body": {"chat_id": a, "entry": "from_sample",
                                                    "sample_ref": {"sample_set_id": m["sample_set_id"], "order": 1}}},
          {"id": "r3", "entity_id": "e3", "body": {"chat_id": b, "entry": "from_utterance"}},
          {"id": "r4", "entity_id": "e4", "body": {"chat_id": cid(77_000), "entry": "from_utterance"}},
          {"body": {"chat_id": b, "entry": "from_sample"}}]                    # id 없음 → 넣지 않음
    heads = sp.item_heads(m, rs)
    assert heads[a]["id"] == "r2" and heads[b]["id"] == "r3" and cid(77_000) not in heads
    assert sp.head_is_this_series(m, heads[a]) and not sp.head_is_this_series(m, heads[b])


def test_other_series_sample_label_counts_as_judged():
    """분할·병합 뒤 새 계열(06 2-6): 원 구간 계열에서 판정한 채팅이 새 표본에 남으면 그 판정이 그 채팅의 라벨이다
    (작업대 reaction_for_chat 과 같은 규칙, D-10 4). 우선순위: 같은 계열 > 다른 계열 > ② 반응."""
    pop = sp.build_population(synth(300), 0, 600_000)
    m = make(pop)
    a, b, c = (m["items"][i]["chat_id"] for i in range(3))
    old = {"sample_set_id": "S-aaaaaaaa-d1-r1", "order": 1}
    rs = [{"id": "r1", "entity_id": "e1", "body": {"chat_id": a, "entry": "from_sample", "sample_ref": old}},
          {"id": "r2", "entity_id": "e2", "body": {"chat_id": b, "entry": "from_utterance"}},
          {"id": "r3", "entity_id": "e3", "body": {"chat_id": b, "entry": "from_sample", "sample_ref": old}},
          {"id": "r4", "entity_id": "e4", "body": {"chat_id": c, "entry": "from_sample", "sample_ref": old}},
          {"id": "r5", "entity_id": "e5", "body": {"chat_id": c, "entry": "from_sample",
                                                    "sample_ref": {"sample_set_id": m["sample_set_id"], "order": 3}}}]
    states, out = sp.item_states(m, rs[:1])
    assert states[a] == sp.JUDGED and out == ()
    heads = sp.item_heads(m, rs)
    assert heads[a]["id"] == "r1" and not sp.head_is_this_series(m, heads[a])
    assert heads[b]["id"] == "r3"                       # 다른 계열 ③ 판정 > ② 반응
    assert heads[c]["id"] == "r5" and sp.head_is_this_series(m, heads[c])
    # 순서를 바꿔도 같다
    heads2 = sp.item_heads(m, list(reversed(rs)))
    assert (heads2[b]["id"], heads2[c]["id"]) == ("r3", "r5")
    assert not sp.head_is_this_series(m, None)


def test_layer_rules_clean():
    from tests.test_layers import violations
    with open(os.path.join(ROOT, "app", "core", "sampling.py"), encoding="utf-8") as f:
        assert violations(f.read(), "sampling.py") == []


def test_bench_200k_draw():
    """20만 건 적격에서 뽑기 — 시간은 기록만(합성)."""
    import time
    ids = [cid(i) for i in range(200_000)]
    t0 = time.perf_counter()
    d = sp.draw(ids, SM, 100, 100)
    dt = time.perf_counter() - t0
    assert len(d.items) == 100
    print(f"\n[bench] draw 200k: {dt * 1000:.0f}ms")
