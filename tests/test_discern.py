"""가상 채팅 가려내기 계산·기록 시험 (app/core/discern.py · app/discern_store.py · schemas/discern-event.schema.json,
H-5, 20 7장, 25 6-2, 03 6-19).

**합성** 대상·판정만 쓴다. 실제 채팅·닉네임·시범 폴더는 쓰지 않는다(쓰기는 tmp_path — conftest 가
%LOCALAPPDATA%\\ChatCoachSmoke\\pytest 아래로 옮긴다). 숫자는 아래 손 계산과 견준다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import discern_store as ds
from app.core import discern as dc
from app.core import origin as org

SID = "SC-20261022-abcd"
PID = "11111111-2222-4333-8444-555555555555"
KST = timezone(timedelta(hours=9))


def cid(i):
    return "c%020x" % i


def line(n):
    return f"{SID}:s{n:04d}"


# ---------------------------------------------------------------------------- 손 계산 1 (작은 표본)
# 대상 10: 생성 4(s0001~4) · 실제 replay 4(c1~c4) · retrieved 2(s0005~6)
# 판정: 생성 가짜·가짜·진짜·모르겠음 / replay 진짜·진짜·가짜·(안 봄) / retrieved 진짜·가짜
#   혼동표(행 = 정답 실제·가상·판별 불가, 열 = 사람 같은 차례)
#     실제  [3, 2, 0]   (replay 진짜 2 + retrieved 진짜 1 = 3, replay 가짜 1 + retrieved 가짜 1 = 2)
#     가상  [1, 2, 1]
#     불가  [0, 0, 0]
#   n = 9, p_o = (3 + 2)/9 = 5/9, 행 합 (5, 4, 0), 열 합 (4, 4, 1)
#   p_e = (5·4 + 4·4 + 0·1)/81 = 36/81 = 4/9, κ = (5/9 − 4/9)/(1 − 4/9) = (1/9)/(5/9) = 0.2
#   가짜 탐지율 2/4 = 0.5 · 진짜 오인율 2/5 = 0.4 · replay 1/3 · retrieved 1/2 · 모르겠음 1/9 · 안 봄 1
TARGETS1 = ([(line(i), "generated", 1000 * i) for i in range(1, 5)]
            + [(cid(i), "replay", 10_000 + 1000 * i) for i in range(1, 5)]
            + [(line(5), "retrieved", 20_000), (line(6), "retrieved", 21_000)])
ANSWERS1 = {line(1): "virtual", line(2): "virtual", line(3): "real", line(4): "unsure",
            cid(1): "real", cid(2): "real", cid(3): "virtual",
            line(5): "real", line(6): "virtual"}


def test_hand_calculation_small():
    r = dc.evaluate(TARGETS1, ANSWERS1)
    assert r.targets == 10 and r.judged == 9 and r.unseen == 1
    assert r.confusion["rows"] == [[3, 2, 0], [1, 2, 1], [0, 0, 0]]
    assert r.confusion["row_totals"] == [5, 4, 0] and r.confusion["col_totals"] == [4, 4, 1]
    assert r.p_o == pytest.approx(5 / 9) and r.p_e == pytest.approx(4 / 9)
    assert r.kappa == pytest.approx(0.2)
    assert r.detection == {"hit": 2, "n": 4, "rate": 0.5}
    assert r.false_alarm == {"hit": 2, "n": 5, "rate": 0.4}
    assert r.replay["rate"] == pytest.approx(1 / 3) and r.retrieved == {"hit": 1, "n": 2, "rate": 0.5}
    assert r.unsure["hit"] == 1 and r.unsure["n"] == 9 and r.unsure["rate"] == pytest.approx(1 / 9)
    assert r.by_source["generated"] == {"shown": 4, "judged": 4, "real": 1, "virtual": 2, "unsure": 1}
    assert r.by_source["replay"]["shown"] == 4 and r.by_source["replay"]["judged"] == 3
    # 표본 적음: 관문을 판단하지 않는다(숫자는 낸다). 통과 조건 자체는 맞는다(0.5 ≤ 0.65, 0.2 ≤ 0.4)
    assert r.sample == {"generated": 4, "real": 5, "broadcasts": 1, "ok": False}
    assert r.gate == {"status": "low_n", "pass": True, "detection_ok": True, "kappa_ok": True}
    assert dc.NOTE_HUMAN_EMPTY in r.notes and any("표본" in n for n in r.notes)
    # human 묶음은 빈다(정답 쪽이 모두 자동)
    assert r.compare["human"]["n"] == 0 and r.compare["all"]["n"] == 9
    # 줄마다 정답: 시각 순, 맞힘·틀림·모르겠음·안 봄
    by = {x["key"]: x for x in r.rows}
    assert by[line(1)]["correct"] is True and by[line(3)]["correct"] is False
    assert by[line(4)]["correct"] is None and by[cid(4)]["answer"] is None
    assert [x["key"] for x in r.rows][:2] == [line(1), line(2)]
    assert dc.answer_mark(by[line(1)]) == "✓ 생성 · 가짜"
    assert dc.answer_mark(by[cid(3)]) == "✗ 실제 · 가짜"
    assert dc.answer_mark(by[line(4)]) == "? 생성 · 모르겠음"
    assert dc.answer_mark(by[cid(4)]) == "· 실제 · 안 봄"
    assert dc.fmt_rate(r.detection) == "0.50 (2/4)" and dc.fmt_kappa(r.kappa) == "0.20"


def test_uses_compare_origins_not_strict(monkeypatch):
    """계산은 origin.compare_origins(strict=False) 하나 (20 7-2). 정답 쪽 method = generation_log_match, 사람 쪽 manual."""
    calls = []
    real = org.compare_origins

    def spy(a, b, **kw):
        a, b = list(a), list(b)
        calls.append((a, b, kw))
        return real(a, b, **kw)
    monkeypatch.setattr(org, "compare_origins", spy)
    dc.evaluate(TARGETS1, ANSWERS1)
    assert len(calls) == 1
    a, b, kw = calls[0]
    assert kw["strict"] is False and len(kw["targets"]) == 10
    assert {x["method"] for x in a} == {"generation_log_match"} and {x["method"] for x in b} == {"manual"}
    assert {x["chat_id"]: x["origin"] for x in a}[line(1)] == "virtual"
    assert {x["chat_id"]: x["origin"] for x in a}[line(5)] == "real"          # retrieved = 실제 채팅
    assert {x["chat_id"]: x["origin"] for x in b}[line(4)] == "undeterminable"


def test_answers_outside_targets_not_counted():
    r = dc.evaluate(TARGETS1, {**ANSWERS1, cid(99): "virtual", line(77): "real", cid(4): "bogus"})
    assert r.judged == 9 and r.kappa == pytest.approx(0.2) and r.compare["out_of_targets_b"] == 2


# ---------------------------------------------------------------------------- 손 계산 2 (관문)
def _session(prefix, broadcast, *, gen_fake=10, real_fake=5, n=20, repeat=False):
    """생성 n줄 중 gen_fake 를 가짜라고, 실제 n줄 중 real_fake 를 가짜라고(나머지 진짜)."""
    t = [(f"SC-20261022-{prefix}:s{i:04d}", "generated", i) for i in range(1, n + 1)]
    t += [("c%02s%018x" % (prefix[:2], i), "replay", 100 + i) for i in range(1, n + 1)]
    ans = {}
    for i, (k, src, _) in enumerate(t):
        j = i if src == "generated" else i - n
        ans[k] = ("virtual" if j < gen_fake else "real") if src == "generated" else \
            ("virtual" if j < real_fake else "real")
    return {"targets": t, "answers": ans, "broadcast": broadcast, "repeat": repeat}


def test_hand_calculation_gate_pass_two_broadcasts():
    """두 방송 × (생성 20 · 실제 20). 생성 10/20 가짜, 실제 5/20 가짜 (방송마다).
    혼동표 실제 [30, 10, 0] · 가상 [20, 20, 0], n = 80, p_o = 50/80 = 0.625,
    p_e = (40·50 + 40·30)/6400 = 0.5, κ = (0.625 − 0.5)/0.5 = 0.25. 탐지율 20/40 = 0.5 → 통과."""
    r = dc.evaluate_sessions([_session("aaaa", "P1"), _session("bbbb", "P2")])
    assert r.confusion["rows"] == [[30, 10, 0], [20, 20, 0], [0, 0, 0]]
    assert r.p_o == pytest.approx(0.625) and r.p_e == pytest.approx(0.5) and r.kappa == pytest.approx(0.25)
    assert r.detection == {"hit": 20, "n": 40, "rate": 0.5}
    assert r.false_alarm == {"hit": 10, "n": 40, "rate": 0.25}
    assert r.sample == {"generated": 40, "real": 40, "broadcasts": 2, "ok": True}
    assert r.gate["status"] == "pass" and r.gate["pass"] is True


def test_gate_fail_and_low_broadcasts_and_repeat_excluded():
    # 생성을 모두 가짜라고 맞힘: 탐지율 1.0 > 0.65 → 실패
    r = dc.evaluate_sessions([_session("aaaa", "P1", gen_fake=20), _session("bbbb", "P2", gen_fake=20)])
    assert r.detection["rate"] == 1.0 and r.gate["status"] == "fail" and r.gate["detection_ok"] is False
    # 같은 방송 두 세션 = 방송 1편 → 표본 적음
    r = dc.evaluate_sessions([_session("aaaa", "P1"), _session("bbbb", "P1")])
    assert r.sample["broadcasts"] == 1 and r.gate["status"] == "low_n"
    # 다시 봄 세션은 대표값에서 빠진다(숫자가 그 세션 없을 때와 같다)
    base = dc.evaluate_sessions([_session("aaaa", "P1"), _session("bbbb", "P2")])
    more = dc.evaluate_sessions([_session("aaaa", "P1"), _session("bbbb", "P2"),
                                 _session("cccc", "P3", gen_fake=20, repeat=True)])
    assert more.numbers() == base.numbers()


def test_single_repeat_session_flags():
    r = dc.evaluate(TARGETS1, ANSWERS1, repeat=True, seen_before=True)
    assert r.repeat and not r.representative and r.seen_before is True
    assert dc.NOTE_REPEAT in r.notes and dc.NOTE_SEEN in r.notes


def test_empty_answers():
    r = dc.evaluate(TARGETS1, {})
    assert r.judged == 0 and r.unseen == 10 and r.kappa is None and r.detection["rate"] is None
    assert r.gate["status"] == "low_n" and r.gate["pass"] is None
    assert dc.fmt_rate(r.detection) == "— (0/0)" and dc.fmt_kappa(None) == "—"


# ---------------------------------------------------------------------------- 판정 누름·기록 줄
def test_apply_answer_toggle():
    a, rec = dc.apply_answer({}, "k", "real")
    assert a == {"k": "real"} and rec == "real"
    a, rec = dc.apply_answer(a, "k", "virtual")             # 다른 단추 = 바꿈
    assert a == {"k": "virtual"} and rec == "virtual"
    a, rec = dc.apply_answer(a, "k", "virtual")             # 같은 단추 다시 = 지움('안 봄')
    assert a == {} and rec == "none"
    with pytest.raises(dc.DiscernError):
        dc.apply_answer({}, "k", "none")


def test_answers_from_events_last_wins_by_seq():
    recs = [
        {"kind": "discern.judge", "seq": 3, "body": {"chat_id": cid(1), "answer": "virtual"}},
        {"kind": "discern.judge", "seq": 2, "body": {"chat_id": cid(1), "answer": "real"}},
        {"kind": "discern.judge", "seq": 4, "body": {"scenario_id": SID, "line_id": "s0001", "answer": "unsure"}},
        {"kind": "discern.judge", "seq": 5, "body": {"scenario_id": SID, "line_id": "s0002", "answer": "real"}},
        {"kind": "discern.judge", "seq": 6, "body": {"scenario_id": SID, "line_id": "s0002", "answer": "none"}},
        {"kind": "discern.start", "seq": 1, "body": {"chat_id": cid(9), "answer": "real"}},     # 다른 kind
        {"kind": "discern.judge", "seq": 7, "body": {"chat_id": cid(8), "answer": "maybe"}},    # 모르는 값
    ]
    assert dc.answers_from_events(recs) == {cid(1): "virtual", line(1): "unsure"}


def test_keys():
    assert dc.target_key(chat_id=cid(1)) == cid(1)
    assert dc.target_key(scenario_id=SID, line_id="s0003") == line(3)
    assert dc.parse_key(line(3)) == {"scenario_id": SID, "line_id": "s0003"}
    assert dc.parse_key(cid(1)) == {"chat_id": cid(1)}
    for bad in ({"chat_id": "a:b"}, {"scenario_id": SID}, {"chat_id": cid(1), "line_id": "s0001"}):
        with pytest.raises(dc.DiscernError):
            dc.target_key(**bad)
    with pytest.raises(dc.DiscernError):
        dc.make_targets([("k", "other")])
    assert len(dc.make_targets([("k", "replay"), ("k", "generated")])) == 1     # 겹친 키는 앞 것만


# ---------------------------------------------------------------------------- 본 적 있음 · 다시 봄
def test_seen_before_and_repeat():
    w = [("training", (0, 5_000)), ("viewer", (100_000, 200_000)), ("discern", (9_000, 12_000)),
         ("labeling", None), ("other", (0, 99_999_999))]
    s = dc.seen_before((10_000, 20_000), w)
    assert s == {"seen": True, "by_kind": {"training": 0, "viewer": 0, "discern": 1, "labeling": 1}}
    assert dc.seen_before((10_000, 20_000), [("training", (20_000, 30_000))])["seen"] is False   # 끝이 닿기만
    assert dc.seen_before((10_000, 20_000), [])["seen"] is False
    assert dc.is_repeat(SID, [{"scenario_id": SID, "judged": 3}]) is True
    assert dc.is_repeat(SID, [{"scenario_id": SID, "judged": 0}]) is False      # 열기만 했으면 다시 봄 아님
    assert dc.is_repeat(SID, [{"scenario_id": "SC-20261022-ffff", "judged": 3}]) is False
    assert dc.is_repeat(None, [{"scenario_id": None, "judged": 3}]) is False


# ---------------------------------------------------------------------------- 기록 (discern_store)
class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 23, 10, 0, 0, tzinfo=KST)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def make_project(tmp_path, name="synthetic-proj"):
    root = tmp_path / "projects" / name
    root.mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project_id": PID}), encoding="utf-8")
    (tmp_path / "pilot").mkdir(exist_ok=True)
    return str(root)


def _log(root, labeler="L01", rand="ab12"):
    return ds.DiscernLog.create(root, labeler, clock=Clock(), rand4=lambda: rand, app_version="0.9.0")


def test_store_roundtrip_numbers_only(tmp_path):
    root = make_project(tmp_path)
    log = _log(root)
    assert log.session_id == "L01-DS-20261023-ab12" and log.path.endswith(os.path.join("discern",
                                                                                        "L01-DS-20261023-ab12.jsonl"))
    log.start(project_id=PID, scenario_id=SID, scenario_sha256="b" * 64, range_ms=(0, 30_000),
              shown={"replay": 4, "retrieved": 2, "generated": 4}, seen_before=None, repeat=False)
    log.judge(cid(1), "real", 11_000)
    log.judge(line(1), "virtual", 1_500)
    log.judge(line(1), "none", 1_600)
    log.judge(line(2), "unsure", 2_500)
    res = dc.evaluate(TARGETS1, ANSWERS1)
    log.end("segment_end", judged=res.judged, unseen=res.unseen, result=res.numbers())
    recs, info = ds.read_valid(log.path)
    assert info == {"lines": 6, "invalid": 0, "corrupt": 0, "truncated_tail": False}
    assert [r["seq"] for r in recs] == [1, 2, 3, 4, 5, 6]
    assert [r["kind"] for r in recs] == ["discern.start"] + ["discern.judge"] * 4 + ["discern.end"]
    j = recs[1]["body"]
    assert set(j) == {"chat_id", "answer", "judged_vod_ms", "wall_at"}            # 판정 줄: 키·판정·시각만
    assert set(recs[2]["body"]) == {"scenario_id", "line_id", "answer", "judged_vod_ms", "wall_at"}
    assert recs[-1]["body"]["result"]["kappa"] == pytest.approx(0.2)
    assert log.answers == {cid(1): "real", line(2): "unsure"}
    # 다시 열면 판정이 이어진다
    again = ds.DiscernLog(root, log.session_id, "L01")
    assert again.answers == log.answers and again.ended
    with pytest.raises(ds.DiscernStoreError):
        log.judge(cid(2), "real", 1)                                                 # 끝난 뒤 쓰기 거부
    # 파일에 본문·닉네임·출처 칸 이름이 없다
    raw = open(log.path, encoding="utf-8").read()
    for word in ("text", "nickname", "display_mask", "\"source\""):
        assert word not in raw


def test_store_rejects_extra_fields_and_bad_values(tmp_path):
    root = make_project(tmp_path)
    log = _log(root)
    with pytest.raises(ds.DiscernStoreError):
        log.judge(cid(1), "real", 1)                         # 시작 줄 전
    log.start(project_id=PID, scenario_id=SID, scenario_sha256="b" * 64, range_ms=(0, 30_000),
              shown={"replay": 1, "retrieved": 0, "generated": 1}, seen_before=True, repeat=True)
    for key, ans, ms in ((cid(1), "maybe", 1), (cid(1), "real", -1), ("x" * 5, "real", 1),
                         ("SC-bad:s0001", "real", 1), (f"{SID}:line1", "real", 1)):
        with pytest.raises(ds.DiscernStoreError):
            log.judge(key, ans, ms)
    base = {"v": 1, "id": "12345678-1234-4234-8234-123456789abc", "seq": 9, "kind": "discern.judge",
            "session_id": log.session_id, "labeler_id": "L01", "created_at": "2026-10-23T10:00:00.000+09:00",
            "app_version": "0.9.0"}
    ok = {"chat_id": cid(1), "answer": "real", "judged_vod_ms": 1, "wall_at": "2026-10-23T10:00:00.000+09:00"}
    assert ds.check_record({**base, "body": ok}) == []
    assert ds.check_record({**base, "body": {**ok, "text": "합성본문"}})                 # 본문 칸 거부
    assert ds.check_record({**base, "body": {**ok, "source": "generated"}})             # 정답 칸 거부
    assert ds.check_record({**base, "body": {**ok, "scenario_id": SID, "line_id": "s0001"}})   # 둘 다 거부
    assert ds.check_record({**base, "kind": "discern.end", "body": {"reason": "compare", "judged": 0, "unseen": 0}})
    with pytest.raises(ds.DiscernStoreError):
        log.end("compare", judged=0, unseen=0)


def test_store_pilot_blocked_before_any_write(tmp_path):
    root = make_project(tmp_path, name="pilot-합성")
    with pytest.raises(ds.DiscernBlockedError):
        _log(root)
    assert not os.path.exists(os.path.join(root, "discern"))
    assert ds.watched(root, "L01") == [] and ds.prior(root, "L01") == []


def test_watched_and_prior(tmp_path, monkeypatch):
    from app import training_store as ts
    root = make_project(tmp_path)
    # 스트리머 훈련 start 줄 (그 라벨러) + 다른 라벨러
    tl = ts.TrainingLog.create(root, "L01", clock=Clock(), rand4=lambda: "tr01", app_version="0.9.0")
    tl.start(project_id=PID, range_ms=(0, 60_000), reference={"source": "final_set", "sha256": "c" * 64, "count": 0})
    t2 = ts.TrainingLog.create(root, "L02", clock=Clock(), rand4=lambda: "tr02", app_version="0.9.0")
    t2.start(project_id=PID, range_ms=(0, 60_000), reference={"source": "final_set", "sha256": "c" * 64, "count": 0})
    # 라벨링 세션 파일(이름만 본다 — 내용은 비어 있다)
    os.makedirs(os.path.join(root, "labels"))
    open(os.path.join(root, "labels", "L01-P-20261020-abcd.aaaa.jsonl"), "w").close()
    open(os.path.join(root, "labels", "L02-P-20261020-abce.aaaa.jsonl"), "w").close()
    # 앞 가려내기 세션: 판정 1
    d1 = _log(root, rand="ds01")
    d1.start(project_id=PID, scenario_id=SID, scenario_sha256="b" * 64, range_ms=(100_000, 200_000),
             shown={"replay": 1, "retrieved": 0, "generated": 1}, seen_before=None, repeat=False)
    d1.judge(cid(1), "real", 100_500)
    d1.end("closed", judged=1, unseen=1)
    opened = []
    real_open = open

    def spy(path, *a, **k):
        opened.append(os.path.relpath(str(path), root))
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", spy)
    w = ds.watched(root, "L01")
    assert sorted(w, key=str) == sorted([("training", (0, 60_000)), ("discern", (100_000, 200_000)),
                                         ("labeling", None)], key=str)
    assert not any(p.startswith("labels") for p in opened)                       # labels\ 는 이름만
    assert not any(p.startswith("scenarios") for p in opened)
    assert ds.watched(root, "L01", exclude_session=d1.session_id).count(("discern", (100_000, 200_000))) == 0
    assert ds.prior(root, "L01") == [{"session_id": d1.session_id, "scenario_id": SID, "judged": 1}]
    assert ds.prior(root, "L02") == []
    assert dc.is_repeat(SID, ds.prior(root, "L01")) is True
    assert dc.seen_before((10_000, 20_000), w)["seen"] is True


def test_result_numbers_fit_schema():
    """DiscernResult.numbers() 가 discern.end 의 result 칸 스키마를 지난다(숫자만)."""
    rec = {"v": 1, "id": "12345678-1234-4234-8234-123456789abc", "seq": 3, "kind": "discern.end",
           "session_id": "L01-DS-20261023-ab12", "labeler_id": "L01", "created_at": "2026-10-23T10:00:00.000+09:00",
           "app_version": "0.9.0"}
    for r in (dc.evaluate(TARGETS1, ANSWERS1), dc.evaluate(TARGETS1, {}),
              dc.evaluate_sessions([_session("aaaa", "P1"), _session("bbbb", "P2")])):
        body = {"reason": "segment_end", "judged": r.judged, "unseen": r.unseen, "result": r.numbers()}
        assert ds.check_record({**rec, "body": body}) == []
