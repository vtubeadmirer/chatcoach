"""재라벨 계획 고르기 (app/core/relabel_plan.py), 계획 파일 (app/relabel.py), 재라벨 세션 종류 켜기 (app/session.py).

설계: 06 3장(10분 블록·비율·층화·3일 간격·재라벨 표본), 7장(블라인드), 03 6-14·12-1·12-2, 08 D-11 6·7.
합성 데이터만 쓴다(실제 채팅·닉네임·프로젝트 없음).
"""
import builtins
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import relabel, session, store
from app.core import relabel_plan as rp
from app.core import sampling

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = "00112233445566778899aabbccddeeff"
PID = "11111111-2222-4333-8444-555555555555"
SRC = "L01-P-20261018-ab12"
LOCKED_AT = "2026-10-18T21:00:00+09:00"
LOCK_SHA = "a" * 64
TH_SHA = "b" * 64
M4_A, M4_B = "aaaa", "bbbb"
MIN = 60_000
BLOCK = 600_000


def _settings(**kw):
    return rp.Settings(**kw)


def _seg(eid8_hex, a, z, sid=SRC):
    eid = f"{eid8_hex}-0000-4000-8000-000000000000"
    return {"session_id": sid, "entity_id": eid, "version_id": "99999999-0000-4000-8000-000000000000",
            "start_ms": a, "end_ms": z}


def _manifest(e8, draw_no, rev, a, z, tau, sid=SRC):
    return {"sample_set_id": f"S-{e8}-d{draw_no}-r{rev}", "source_segment": _seg(e8, a, z, sid),
            "draw_no": draw_no, "sample_rev": rev, "seed_material": f"{SEED}|{draw_no}", "tau_key": tau}


def _plan(kind="relabel_same", labeler="L01", done=((0, 60 * MIN),), manifests=(), hard=None, settings=None,
          m4s=(M4_A,), created_m4=M4_A, lock=None):
    return rp.build_plan(
        plan_id="RP-20261021-zz01", plan_no=1, project_id=PID, session_kind=kind, labeler_id=labeler,
        source_session_id=SRC, source_lock=lock or {"locked_at": LOCKED_AT, "sha256": LOCK_SHA},
        source_machine4s=m4s, created_machine4=created_m4, sample_seed=SEED, done_ranges=done,
        settings=settings or _settings(), manifests=manifests, hard_scores=hard, guide_version="0.1",
        thresholds_sha256=TH_SHA, created_at="2026-10-21T09:00:00+09:00", created_by="L01", app_version="0.0.0")


# ============================================================================ 블록 (06 3-1)

def test_blocks_are_ten_minute_cells_and_short_tail_needs_300s():
    # 0~42분: 블록 0~3 은 온전, 블록 4 는 2분(120초)이라 빠짐
    bs = rp.candidate_blocks([(0, 42 * MIN)])
    assert [b.index for b in bs] == [0, 1, 2, 3]
    # 0~45분: 블록 4 가 정확히 300초라 들어감. 범위는 블록 칸 전체(1차 범위 끝 45분이 드러나지 않게, 06 3-1)
    bs = rp.candidate_blocks([(0, 45 * MIN)])
    assert [b.index for b in bs] == [0, 1, 2, 3, 4]
    assert bs[-1].ranges == ((40 * MIN, 50 * MIN),) and bs[-1].covered_ms == 300_000
    # 방송·영상 끝(extent)으로만 자른다
    bs = rp.candidate_blocks([(0, 45 * MIN)], extent=[(0, 47 * MIN)])
    assert bs[-1].ranges == ((40 * MIN, 47 * MIN),)


def test_blocks_clip_by_extent_not_by_primary_segments():
    # 영상이 20:38 부터 덮는 것과 같은 모양(합성 값). 블록 2 는 [20:38, 30:00) = 562초라 들어감
    start = 20 * MIN + 38_000
    end = 39 * MIN + 51_000
    bs = rp.candidate_blocks([(start, end)], extent=[(start, end)])
    assert [b.index for b in bs] == [2, 3]
    assert bs[0].ranges == ((start, 30 * MIN),) and bs[1].ranges == ((30 * MIN, end),)
    # 한 블록 안의 두 조각은 합쳐 세지만, 범위에는 1차 조각의 가장자리(3·4·6·9분)가 들어가지 않는다
    bs = rp.candidate_blocks([(0, 3 * MIN), (6 * MIN, 9 * MIN), (3 * MIN, 4 * MIN)])
    assert len(bs) == 1 and bs[0].ranges == ((0, 10 * MIN),) and bs[0].covered_ms == 7 * MIN


def test_primary_gap_edges_do_not_leak_into_plan():
    """검토 확인 문제: 1차 구간 사이 빈틈·블록 가운데서 시작한 첫 구간의 시각이 계획 범위·scope 에 드러났다."""
    done = [(1_000_000, 1_234_000), (1_300_000, 2_000_000)]
    bs = rp.candidate_blocks(done)
    b2 = next(b for b in bs if b.index == 2)
    assert b2.ranges == ((1_200_000, 1_800_000),) and b2.covered_ms == 534_000
    p = _plan(done=done)
    dumped = json.dumps(p)
    for leak in ("1234000", "1300000", "1000000", "2000000"):
        assert leak not in dumped
    assert "done_ranges" not in p["selection"]
    for s in p["selected"]:
        for a, z in s["ranges"]:
            assert a % 600_000 == 0 and z % 600_000 == 0
    v = rp.verify_plan(p, sample_seed=SEED, source_lock=p["source_lock"])
    assert v.ok, v.problems


def test_ranges_must_be_well_formed():
    for bad in ([(5, 5)], [(-1, 10)], [(1.5, 10)], [(1,)], [(True, 10)]):
        with pytest.raises(rp.RelabelPlanError):
            rp.normalize_ranges(bad)


# ============================================================================ 개수·층화 (06 3-2)

@pytest.mark.parametrize("n,expect", [(0, (0, 0)), (1, (1, 0)), (2, (1, 0)), (3, (1, 0)), (4, (1, 0)),
                                      (6, (2, 0)), (10, (3, 0)), (12, (3, 0)), (20, (5, 0))])
def test_counts_without_hard_are_all_random(n, expect):
    # 0.15 + 0.10 = 0.25. round_half_up: 4 → 1, 6 → 1.5 → 2, 10 → 2.5 → 3. 후보가 있으면 1개 이상
    assert rp.selection_counts(n, _settings(), with_hard=False) == expect


def test_counts_with_hard():
    s = _settings()
    assert rp.selection_counts(3, s, with_hard=True) == (1, 0)        # 4개 미만이면 무작위만
    assert rp.selection_counts(20, s, with_hard=True) == (3, 2)       # 5개 = 무작위 3 + 어려움 2
    assert rp.selection_counts(4, s, with_hard=True) == (1, 0)        # 1개뿐이면 무작위 1개는 남긴다


def test_selection_is_deterministic_and_seeded_by_project_and_session():
    cands = rp.candidate_blocks([(0, 200 * MIN)])
    sm = rp.make_seed_material(SEED, SRC)
    a = rp.select_blocks(cands, sm, _settings())
    b = rp.select_blocks(list(reversed(cands)), sm, _settings())
    assert [(s.block.index, s.order) for s in a] == [(s.block.index, s.order) for s in b]
    assert len(a) == 5 and all(s.stratum == rp.STRATUM_RANDOM for s in a)
    # 무작위 층 = block_key 작은 순 다섯
    want = sorted(range(20), key=lambda k: rp.block_key(sm, k))[:5]
    assert sorted(s.block.index for s in a) == sorted(want)
    # 제시 순서는 order_key 순이라 블록 번호 순이 아니다(층·시각을 순서로 흘리지 않음)
    assert [s.block.index for s in a] == sorted(want, key=lambda k: rp.order_key(sm, k))
    # 다른 잠긴 세션이면 다른 고르기(이 합성 값에서)
    other = rp.select_blocks(cands, rp.make_seed_material(SEED, "L01-P-20261018-cd34"), _settings())
    assert {s.block.index for s in other} != {s.block.index for s in a}


def test_selection_golden():
    """알고리즘 sha256-relabel-v1 의 고정값. 바뀌면 옛 계획을 재현할 수 없다 — 이름을 올리고 v1 을 남겨야 한다."""
    sm = rp.make_seed_material(SEED, SRC)
    assert sm == f"{SEED}|relabel|{SRC}"
    assert rp.block_key(sm, 0) == hashlib.sha256(f"{SEED}|relabel|{SRC}|0".encode()).hexdigest()
    assert rp.order_key(sm, 7) == hashlib.sha256(f"{SEED}|relabel|{SRC}|order|7".encode()).hexdigest()
    got = [s.block.index for s in rp.select_blocks(rp.candidate_blocks([(0, 200 * MIN)]), sm, _settings())]
    assert got == GOLDEN_ORDER


GOLDEN_ORDER = [12, 4, 18, 6, 5]   # 2026-10-21 에 계산해 고정 (블록 0~19, 시작값)


def test_hard_stratum_takes_highest_scores_outside_random():
    cands = rp.candidate_blocks([(0, 200 * MIN)])
    sm = rp.make_seed_material(SEED, SRC)
    rand_only = rp.select_blocks(cands, sm, _settings(hard_share=0.10, random_share=0.15))
    scores = {k: float(k) for k in range(20)}                   # 큰 번호가 어려움
    got = rp.select_blocks(cands, sm, _settings(), scores)
    rnd = [s.block.index for s in got if s.stratum == rp.STRATUM_RANDOM]
    hard = [s.block.index for s in got if s.stratum == rp.STRATUM_HARD]
    assert len(rnd) == 3 and len(hard) == 2 and not set(rnd) & set(hard)
    assert set(rnd) == set(sorted(range(20), key=lambda k: rp.block_key(sm, k))[:3])
    assert len(rand_only) == 5                                   # 점수가 없으면 다섯 모두 무작위
    assert set(hard) == set(sorted((k for k in range(20) if k not in rnd), reverse=True)[:2])
    assert sorted(s.order for s in got) == [1, 2, 3, 4, 5]


def test_hard_score_formula():
    assert rp.hard_score(1, 1, 1, 3) == pytest.approx(4 / 4)
    assert rp.hard_score(0, 0, 0, 0) == 0
    with pytest.raises(rp.RelabelPlanError):
        rp.hard_score(-1, 0, 0, 0)


# ============================================================================ τ_block (06 3-5)

def test_tau_block_uses_latest_rev_and_largest_draw():
    t = ["1" * 64, "2" * 64, "3" * 64, "4" * 64]
    ms = [
        _manifest("aaaaaaaa", 1, 1, 0, 15 * MIN, t[3]),
        _manifest("aaaaaaaa", 1, 2, 0, 15 * MIN, t[1]),          # 같은 계열의 큰 rev 가 유효 (τ 불감소는 표본 쪽 규칙)
        _manifest("bbbbbbbb", 1, 1, 15 * MIN, 40 * MIN, t[2]),
        _manifest("cccccccc", 1, 1, 50 * MIN, 60 * MIN, None),   # 적격 0
        _manifest("dddddddd", 1, 1, 0, 60 * MIN, t[3], sid="L02-P-20261018-zz99"),   # 다른 세션: 무시
    ]
    latest = rp.latest_source_manifests(ms, SRC)
    assert [m["sample_set_id"] for m in latest] == ["S-aaaaaaaa-d1-r2", "S-bbbbbbbb-d1-r1", "S-cccccccc-d1-r1"]
    assert rp.tau_for_block([(0, 10 * MIN)], latest) == (f"{SEED}|1", t[1])
    assert rp.tau_for_block([(10 * MIN, 20 * MIN)], latest) == (f"{SEED}|1", t[2])   # 두 표본의 큰 τ
    assert rp.tau_for_block([(50 * MIN, 60 * MIN)], latest) == (None, None)
    assert rp.tau_for_block([(40 * MIN, 50 * MIN)], latest) == (None, None)          # [a, z) 라 40분은 안 겹침
    # draw_no 가 큰 판이 있으면 그 판의 τ (D-11 2: 다시 뽑은 판이 현재 판)
    latest2 = rp.latest_source_manifests(ms + [_manifest("aaaaaaaa", 2, 1, 0, 15 * MIN, t[0])], SRC)
    assert rp.tau_for_block([(0, 10 * MIN)], latest2) == (f"{SEED}|2", t[0])


def test_block_sample_is_key_le_tau():
    sm = f"{SEED}|1"
    ids = [f"c{i:020x}" for i in range(50)]
    keys = sorted((sampling.sort_key(sm, c), c) for c in ids)
    tau = keys[9][0]
    got = rp.block_sample(ids + ids[:3], sm, tau)            # 겹친 ID 는 한 번
    assert got == [c for _k, c in keys[:10]]
    assert rp.block_sample(ids, None, None) == []
    with pytest.raises(rp.RelabelPlanError):
        rp.block_sample(["nope"], sm, tau)


# ============================================================================ 3일 간격 (06 3-3)

def test_earliest_start_only_for_same_person():
    assert rp.earliest_start_at("relabel_same", LOCKED_AT, 3) == "2026-10-21T21:00:00+09:00"
    assert rp.earliest_start_at("relabel_second", LOCKED_AT, 3) is None
    with pytest.raises(rp.RelabelPlanError):
        rp.earliest_start_at("relabel_same", "2026-10-18T21:00:00", 3)     # 오프셋 없음


def test_start_gate():
    p = _plan()
    assert p["earliest_start_at"] == "2026-10-21T21:00:00+09:00" and p["enforce_interval"] is False
    g = rp.start_gate(p, "2026-10-21T09:00:00+09:00")
    assert g.status == rp.GATE_EARLY and g.remaining_ms == 12 * 3_600_000
    assert rp.start_gate(p, "2026-10-21T12:00:00+00:00").status == rp.GATE_OK       # 다른 오프셋도 같은 순간으로
    strict = _plan(settings=_settings(enforce_interval=True))
    assert rp.start_gate(strict, "2026-10-21T09:00:00+09:00").status == rp.GATE_BLOCKED
    second = _plan("relabel_second", "L02", settings=_settings(enforce_interval=True))
    assert second["earliest_start_at"] is None and second["enforce_interval"] is False
    assert rp.start_gate(second, "2026-10-18T21:00:01+09:00").status == rp.GATE_OK


# ============================================================================ 계획 만들기·검사

def test_plan_shape_and_schema():
    ms = [(_manifest("aaaaaaaa", 1, 1, 0, 60 * MIN, "5" * 64), "c" * 64)]
    p = _plan(manifests=ms)
    assert rp.check_plan(p) == []
    with open(os.path.join(ROOT, "schemas", "relabel-plan.schema.json"), encoding="utf-8") as f:
        schema = store.LabelSchema(json.load(f))
    assert schema.check(json.loads(json.dumps(p))) == []
    assert p["selection"]["candidate_blocks"] == [0, 1, 2, 3, 4, 5] and len(p["selected"]) == 2   # 6 × 0.25 = 1.5 → 2
    assert p["scope"]["type"] == "blocks" and p["scope"] == rp.scope_of(p)
    assert all(s["tau_block_key"] == "5" * 64 and s["seed_material"] == f"{SEED}|1" for s in p["selected"])
    assert p["source_manifests"] == [{"sample_set_id": "S-aaaaaaaa-d1-r1", "sha256": "c" * 64, "draw_no": 1,
                                      "tau_key": "5" * 64}]
    # 1차 표본 목록·원 구간 경계는 계획에 없다 (06 3-5)
    text = json.dumps(p)
    assert "items" not in p and "source_segment" not in text and "entity_id" not in text
    assert any("어려움 층" in n for n in p["notes"])
    bad = dict(p, plan_id="RP-1")
    assert schema.check(bad) and rp.check_plan(bad)


def test_build_plan_refuses():
    with pytest.raises(rp.RelabelPlanError, match="같은 라벨러"):
        _plan("relabel_same", "L02")
    with pytest.raises(rp.RelabelPlanError, match="다른 라벨러"):
        _plan("relabel_second", "L01")
    with pytest.raises(rp.RelabelPlanError, match="잠기지"):
        rp.build_plan(**{**_kw(), "source_lock": None})
    with pytest.raises(rp.RelabelPlanError, match="1차"):
        rp.build_plan(**{**_kw(), "source_session_id": "L01-RS-20261018-ab12"})
    with pytest.raises(rp.RelabelPlanError, match="비었"):
        rp.build_plan(**{**_kw(), "done_ranges": []})
    with pytest.raises(rp.RelabelPlanError, match="후보 블록"):
        rp.build_plan(**{**_kw(), "done_ranges": [(0, 4 * MIN)]})


def _kw():
    return dict(plan_id="RP-20261021-zz01", plan_no=1, project_id=PID, session_kind="relabel_same", labeler_id="L01",
                source_session_id=SRC, source_lock={"locked_at": LOCKED_AT, "sha256": LOCK_SHA},
                source_machine4s=[M4_A], created_machine4=M4_A, sample_seed=SEED, done_ranges=[(0, 60 * MIN)],
                settings=_settings(), guide_version="0.1", thresholds_sha256=TH_SHA,
                created_at="2026-10-21T09:00:00+09:00", created_by="L01", app_version="0.0.0")


def test_same_and_second_pick_same_blocks_and_mark_machine():
    same = _plan()
    second = _plan("relabel_second", "L02", m4s=(M4_A, M4_B), created_m4=M4_B)
    assert [s["block_index"] for s in same["selected"]] == [s["block_index"] for s in second["selected"]]
    assert second["same_machine_as_source"] is True and second["source_machine4s"] == [M4_A, M4_B]
    assert _plan("relabel_second", "L02", created_m4="cccc")["same_machine_as_source"] is False
    assert rp.same_machine(second, M4_A) and not rp.same_machine(second, "zzzz") and not rp.same_machine(second, None)
    assert rp.plan_refusal(second, "relabel_second", "L02") is None
    assert rp.plan_refusal(second, "relabel_second", "L03")                 # 맡긴 사람이 아님
    assert rp.plan_refusal(second, "relabel_same", "L02")                   # 다른 종류
    assert rp.plan_refusal(same, "relabel_same", "L01") is None


def test_verify_plan_reproduces_and_catches_changes():
    m = _manifest("aaaaaaaa", 1, 1, 0, 60 * MIN, "5" * 64)
    ms = [(m, "c" * 64)]
    lock = {"locked_at": LOCKED_AT, "sha256": LOCK_SHA}
    p = json.loads(json.dumps(_plan(manifests=ms, hard={k: float(k % 3) for k in range(6)})))
    v = rp.verify_plan(p, sample_seed=SEED, source_lock=lock, manifests=ms)
    assert v.ok, v.problems
    # 계획을 손으로 바꿈
    t = json.loads(json.dumps(p))
    t["selected"][0]["block_index"] += 1
    assert not rp.verify_plan(t, sample_seed=SEED, source_lock=lock, manifests=ms).ok
    # 잠금 파일이 바뀜
    assert not rp.verify_plan(p, sample_seed=SEED, source_lock={**lock, "sha256": "d" * 64}, manifests=ms).ok
    # 매니페스트가 바뀜(τ)
    m2 = dict(m, tau_key="6" * 64)
    assert not rp.verify_plan(p, sample_seed=SEED, source_lock=lock, manifests=[(m2, "c" * 64)]).ok
    # 다른 시드
    assert not rp.verify_plan(p, sample_seed="f" * 32, source_lock=lock, manifests=ms).ok


def test_settings_from_guide_v01_asset():
    with open(os.path.join(ROOT, "assets", "guide-config.v0.1.json"), encoding="utf-8") as f:
        values = json.load(f)["values"]
    s, notes = rp.settings_from_values(values)
    assert s.block_ms == BLOCK and s.min_interval_days == 3 and s.enforce_interval is False
    assert (s.random_share, s.hard_share) == (0.15, 0.1)
    assert values["relabel"]["unit"] == "segment" and any("segment" in n for n in notes)
    s2, n2 = rp.settings_from_values({"relabel": {**values["relabel"], "unit": "block", "block_minutes": 5}})
    assert s2.block_ms == 300_000 and not n2
    s3, n3 = rp.settings_from_values({})
    assert s3 == rp.Settings() and n3


# ============================================================================ 계획 파일 (app/relabel.py)

def _project(tmp_path):
    root = tmp_path / "proj"
    for d in ("labels", "locks", "samples", "relabel"):
        (root / d).mkdir(parents=True)
    with open(os.path.join(ROOT, "assets", "guide-config.v0.1.json"), encoding="utf-8") as f:
        values = json.load(f)["values"]
    data = {"project_id": PID, "sample_seed": SEED, "active_guide_version": "0.1",
            "guides": [{"guide_version": "0.1", "thresholds": values, "thresholds_sha256": TH_SHA}]}
    (root / "labels" / f"{SRC}.{M4_A}.jsonl").write_bytes(b'{"secret": "SRC-LABEL-BODY"}\n')
    lock = json.dumps({"session_id": SRC, "locked_at": LOCKED_AT}).encode()
    (root / "locks" / f"{SRC}.json").write_bytes(lock)
    m = _manifest("aaaaaaaa", 1, 1, 0, 30 * MIN, "5" * 64)
    mb = json.dumps(m).encode()
    (root / "samples" / "S-aaaaaaaa-d1-r1.json").write_bytes(mb)
    return str(root), data, hashlib.sha256(lock).hexdigest(), hashlib.sha256(mb).hexdigest()


NOW = datetime(2026, 10, 21, 9, 0, tzinfo=KST)


def _create(root, data, kind="relabel_same", labeler="L01", rand4="ab12", creator="primary", m4=M4_A):
    return relabel.create_plan(root, data, session_kind=kind, labeler_id=labeler, source_session_id=SRC,
                               done_ranges=[(0, 60 * MIN)], created_by="L01", created_machine4=m4,
                               app_version="0.0.0", creator_kind=creator, now=NOW, rand4=rand4)


def test_create_read_and_never_overwrite(tmp_path):
    root, data, lock_sha, man_sha = _project(tmp_path)
    p = _create(root, data)
    assert p["plan_id"] == "RP-20261021-ab12" and p["plan_no"] == 1
    assert p["source_lock"] == {"locked_at": LOCKED_AT, "sha256": lock_sha}
    assert p["source_manifests"][0]["sha256"] == man_sha and p["source_machine4s"] == [M4_A]
    assert any("segment" in n for n in p["notes"])              # v0.1 기준서는 unit = segment
    assert relabel.read_plan(root, p["plan_id"]) == p
    with pytest.raises(FileExistsError):
        relabel.write_plan(root, p)
    p2 = _create(root, data, "relabel_second", "L02", rand4="cd34")
    assert p2["plan_no"] == 2 and [s["block_index"] for s in p2["selected"]] == [s["block_index"] for s in p["selected"]]
    v = relabel.verify_plan_on_disk(root, data, p["plan_id"], creator_kind="adjudication")
    assert v.ok, v.problems
    assert relabel.session_args(p) == {"scope": p["scope"], "relabel_plan_id": p["plan_id"]}
    names = sorted(os.listdir(os.path.join(root, "relabel")))
    # 임시 파일이 남지 않음. index.json 은 계획을 만들 때 같이 쓰는 색인(D-12 2, 다섯째 묶음)
    assert names == ["RP-20261021-ab12.json", "RP-20261021-cd34.json", "index.json"]


def test_create_requires_lock(tmp_path):
    root, data, *_ = _project(tmp_path)
    os.remove(os.path.join(root, "locks", f"{SRC}.json"))
    with pytest.raises(ValueError, match="잠기지"):
        _create(root, data)
    assert os.listdir(os.path.join(root, "relabel")) == []


class _OpenSpy:
    def __init__(self, monkeypatch):
        self.paths = []
        real = builtins.open

        def spy(file, *a, **k):
            self.paths.append(os.path.abspath(str(file)))
            return real(file, *a, **k)
        monkeypatch.setattr(builtins, "open", spy)


@pytest.mark.parametrize("kind", ["relabel_same", "relabel_second"])
def test_relabel_session_cannot_create_and_opens_nothing(tmp_path, monkeypatch, kind):
    root, data, *_ = _project(tmp_path)
    spy = _OpenSpy(monkeypatch)
    for call in (lambda: _create(root, data, creator=kind),
                 lambda: relabel.load_source_manifests(root, SRC, creator_kind=kind),
                 lambda: relabel.source_lock_info(root, SRC, creator_kind=kind),
                 lambda: relabel.verify_plan_on_disk(root, data, "RP-20261021-ab12", creator_kind=kind)):
        with pytest.raises(relabel.RelabelBlindError):
            call()
    assert spy.paths == []


def test_second_person_opens_only_own_plan(tmp_path, monkeypatch):
    root, data, *_ = _project(tmp_path)
    p = _create(root, data, "relabel_second", "L02")
    spy = _OpenSpy(monkeypatch)
    got = relabel.read_plan_for_session(root, "relabel_second", "L02", p["plan_id"])
    assert got["plan_id"] == p["plan_id"]
    assert spy.paths == [os.path.abspath(relabel.plan_path(root, p["plan_id"]))]   # 원 세션 라벨·samples 는 열지 않음
    assert "SRC-LABEL-BODY" not in json.dumps(got)
    with pytest.raises(ValueError, match="다른 라벨러"):
        relabel.read_plan_for_session(root, "relabel_second", "L03", p["plan_id"])
    with pytest.raises(ValueError):
        relabel.read_plan_for_session(root, "relabel_same", "L01", p["plan_id"])
    with pytest.raises(relabel.RelabelBlindError):
        relabel.read_plan_for_session(root, "primary", "L01", p["plan_id"])
    with pytest.raises(ValueError):
        relabel.plan_path(root, "..\\RP-20261021-ab12")


def test_list_plans_reports_broken_files(tmp_path):
    root, data, *_ = _project(tmp_path)
    _create(root, data)
    with open(os.path.join(root, "relabel", "RP-20261021-zz99.json"), "wb") as f:
        f.write(b"{not json")
    with open(os.path.join(root, "relabel", "notes.txt"), "wb") as f:
        f.write(b"x")
    s = relabel.list_plans(root)
    assert [x.plan_id for x in s] == ["RP-20261021-ab12", "RP-20261021-zz99"]
    assert s[0].usable and s[0].session_kind == "relabel_same" and s[0].earliest_start_at
    assert not s[1].usable and "v1" in s[1].problem
    assert relabel.next_plan_no(root) == 2


# ============================================================================ 세션 종류 켜기 (app/session.py, D-11 6)

def _choices(root, labeler, now, m4=M4_A):
    sv = session.survey_project(root, {"labelers": []})
    mine = session.existing_sessions(root, labeler, sv, m4)
    return {c.kind: c for c in session.kind_choices(sv, labeler, mine, this_machine4=m4, now=now)}


def test_relabel_same_gate_in_kind_choices(tmp_path):
    root, data, *_ = _project(tmp_path)
    p = _create(root, data)
    ch = _choices(root, "L01", NOW)                         # 잠금 뒤 2.5일 → 앞당긴 시작
    c = ch["relabel_same"]
    assert c.enabled and c.early and c.plan_id == p["plan_id"] and c.remaining_ms == 12 * 3_600_000
    assert "사유" in c.reason
    assert not ch["relabel_second"].enabled                  # L01 은 원 세션 라벨러라 두 번째 사람이 될 수 없다
    later = _choices(root, "L01", NOW + timedelta(days=1))["relabel_same"]
    assert later.enabled and not later.early and later.reason == ""
    assert not _choices(root, "L03", NOW)["relabel_same"].enabled


def test_relabel_same_enforced_interval_blocks(tmp_path):
    root, data, *_ = _project(tmp_path)
    data["guides"][0]["thresholds"]["relabel"]["enforce_interval"] = True
    _create(root, data)
    c = _choices(root, "L01", NOW)["relabel_same"]
    assert not c.enabled and "강제" in c.reason


def test_relabel_second_same_machine_and_used_plan(tmp_path):
    root, data, *_ = _project(tmp_path)
    p = _create(root, data, "relabel_second", "L02")
    c = _choices(root, "L02", NOW - timedelta(days=2))["relabel_second"]     # 간격 없음
    assert c.enabled and c.same_machine and "같은 machine_id" in c.reason and c.plan_id == p["plan_id"]
    c2 = _choices(root, "L02", NOW, m4="cccc")["relabel_second"]
    assert c2.enabled and not c2.same_machine
    # 이 계획으로 연 자기 세션이 있으면 새로 열지 않는다(이어 하기)
    first = {"kind": "session.open", "body": {"guide_version": "0.1", "relabel_plan_id": p["plan_id"]}}
    with open(os.path.join(root, "labels", f"L02-R2-20261021-aa11.{M4_A}.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(first) + "\n")
    c3 = _choices(root, "L02", NOW)["relabel_second"]
    assert not c3.enabled and "이미" in c3.reason


def test_relabel_needs_locked_source(tmp_path):
    root, data, *_ = _project(tmp_path)
    _create(root, data, "relabel_second", "L02")
    os.remove(os.path.join(root, "locks", f"{SRC}.json"))
    c = _choices(root, "L02", NOW)["relabel_second"]
    assert not c.enabled and "잠기지" in c.reason


def test_survey_does_not_open_source_labels(tmp_path, monkeypatch):
    root, data, *_ = _project(tmp_path)
    _create(root, data, "relabel_second", "L02")
    spy = _OpenSpy(monkeypatch)
    _choices(root, "L02", NOW)
    src_files = [x for x in spy.paths if os.path.basename(x).startswith(SRC) or os.sep + "samples" + os.sep in x]
    assert src_files == []
