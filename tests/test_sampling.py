"""app/core/sampling.py 시험 — sha256-tau-v1 (06 2장, 03 6-13·15장, 02 3-3).

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
    assert m["excluded"] == {"non_text": 1, "invalid_time": 1, "buffer": pop.excluded_buffer}
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
        # 2단계 반응은 세지 않는다
        {"chat_id": m["items"][2]["chat_id"], "entry": "from_utterance", "sample_ref": None},
        # 다른 계열(draw 2)은 세지 않는다
        {"chat_id": m["items"][3]["chat_id"], "entry": "from_sample",
         "sample_ref": {"sample_set_id": "S-7f3c2a1b-d2-r1", "order": 4}},
        # 표본에서 빠진 채팅의 판정 → out_of_sample
        {"chat_id": cid(77_777), "entry": "from_sample", "sample_ref": {"sample_set_id": "S-7f3c2a1b-d1-r1", "order": 9}},
    ]
    states, out = sp.item_states(m, reactions)
    assert states[a] == sp.JUDGED and states[b] == sp.JUDGED
    assert sum(v == sp.JUDGED for v in states.values()) == 2
    assert out == (cid(77_777),)
    pr = sp.progress(m, states)
    assert (pr.done, pr.total, pr.base, pr.extra) == (2, 100, 100, 0)
    assert pr.denominator_text() == "100"
    m3 = dict(m, items=m["items"] + [dict(m["items"][0], chat_id=cid(88_000 + i), order=101 + i) for i in range(3)])
    assert sp.progress(m3, {}).denominator_text() == "100 + 3"


def test_latest_of():
    a = {"sample_set_id": "S-7f3c2a1b-d1-r1", "sample_rev": 1}
    b = {"sample_set_id": "S-7f3c2a1b-d1-r2", "sample_rev": 2}
    c = {"sample_set_id": "S-7f3c2a1b-d2-r1", "sample_rev": 1}
    got = sp.latest_of([b, a, c])
    assert got[("7f3c2a1b", 1)] is b and got[("7f3c2a1b", 2)] is c


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
