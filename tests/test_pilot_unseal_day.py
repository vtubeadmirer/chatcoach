"""시범 해제 당일 한 번에 (tools/pilot_unseal_day.py, 08 D-22 2·6, D-17 4, D-16, 23 6장 '나') — 합성 시범 프로젝트 리허설.

합성 시범 프로젝트(tmp 아래 'pilot-<번호>' 이름)·합성 채팅·합성 엔진 결과(v0.2·v0.3)·합성 주제 흐름·합성 전사 낱말만 쓴다.
실제 시범 프로젝트(chatcoach-data\\projects\\pilot-*)·실제 채팅·닉네임·전사문은 쓰지 않는다.

리허설 순서: 봉인(1차 잠금 전, 사전 봉인) → 1차 잠금 → 재라벨 계획 → 재라벨 잠금 → dry-run(아무것도 쓰지 않음) →
해제 당일 실행(해제 → AI 비교 2판 → 판끼리 → 보정 → 요약). 사람 클릭 0.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
from datetime import datetime, timedelta

import pytest

from app import jsonl, project, relabel, seal, session, store
from app.core import textnorm as TN
from app.core import topics as T
from tests.test_final_set import KST, MID, SALT, FakeLock, _base, _react, _uuids
from tools import pilot_unseal_day as day
from tools import viewer_score_calib as cal

SECRET_TEXT = "합성리허설본문"          # 채팅 본문 표지 — 요약·보고서·출력에 나오면 안 된다
SECRET_NICK = "합성리허설닉"
SECRET_WORD = "합성표지낱말"             # 전사 낱말 표지
P_ID = "L01-P-20261018-ab12"
RS_ID = "L01-RS-20261019-cd34"
# 합성 시각은 모두 오늘(실제 해제 시각)보다 앞이다 — 해제 때 이미 잠긴 세션이어야 노출(view_exposed)로 빠지지 않는다
T_SEAL = datetime(2026, 10, 18, 9, 0, tzinfo=KST)          # 엔진 결과 봉인(1차 잠금 전 → 사전 봉인)
T_P = datetime(2026, 10, 18, 10, 0, tzinfo=KST)            # 1차 기록
T_P_LOCK = datetime(2026, 10, 18, 12, 0, tzinfo=KST)       # 1차 잠금
T_PLAN = datetime(2026, 10, 18, 13, 0, tzinfo=KST)         # 재라벨 계획
T_RS = datetime(2026, 10, 19, 9, 0, tzinfo=KST)            # 재라벨 기록·잠금
TEXTS = ("떡볶이 치즈 추가하면 어떤 맛이에요?", "어묵 국물 매운가요?", "분식집 메뉴 추천해 주세요", "ㅋㅋㅋㅋ",
         "라면 끓이는 순서 알려 주세요", "오늘 방송 몇 시까지 해요?", "{:zz_test_03:}", "김밥 재료 뭐 넣어요?")


def cid(n: int) -> str:
    return "c" + f"{n:020x}"


@pytest.fixture(autouse=True)
def _clear_fake_locks():
    FakeLock.held = set()
    yield
    FakeLock.held = set()


def _ctx(sid, kind, created, plan_id=None):
    return session.SessionContext("L01", kind, sid, session.machine4(MID), machine_id=MID,
                                  session_created_at=created, relabel_plan_id=plan_id)


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        self.t += timedelta(milliseconds=10)
        return self.t


def _store(root, sid, kind, created, start, plan_id=None, *, at):
    return store.ProjectStore.open(root, _ctx(sid, kind, created.isoformat(timespec="seconds"), plan_id), FakeLock,
                                   clock=_Clock(at), uuid_factory=_uuids(start), app_version="0.0.1")


#: 1차 반응 (채팅 번호 → 등급). 재라벨은 같은 값(두 번 같음 — 최종 세트에 그대로 들어간다)
GRADES = {1: "direct", 3: "direct", 5: "direct", 7: "likely", 9: "direct", 11: "unconfirmed", 13: "unconfirmed",
          15: "related_only", 17: "unconfirmed", 19: "direct"}


def _messages(n=40):
    out = []
    for i in range(1, n + 1):
        out.append({"chat_id": cid(i), "t_ms": 100_000 + i * 5_000, "server_time_ms": i, "type_code": 1, "kind": "text",
                    "status": "NORMAL", "viewer_id": "vw_" + "abcdefg"[i % 7] * 12, "is_anonymous": False,
                    "nickname_masked": SECRET_NICK, "role": "common_user",
                    "text": f"{TEXTS[i % len(TEXTS)]} {SECRET_TEXT}" if i % 5 == 0 else TEXTS[i % len(TEXTS)],
                    "flags": {}, "raw_ref": f"f:1:{i}"})
    return out


def ai_doc(run_id, version, video_no, grades):
    rx = []
    for n, g in grades.items():
        if g == "unconfirmed":
            rx.append({"chat_id": cid(n), "result": "unconfirmed", "grade": None, "reaction_time_ms": None})
        else:
            rx.append({"chat_id": cid(n), "result": "graded", "grade": g, "reaction_time_ms": 100_000 + n * 5_000 + 2_000})
    return {"schema": "chatcoach/ai-result", "schema_version": 1, "run_id": run_id,
            "created_at": "2026-10-23T08:00:00+09:00",
            "engine": {"name": "synthetic-engine", "version": version, "preregistered": True},
            "target": {"platform": "chzzk", "video_no": str(video_no), "chat_id_algorithm": "sl-chat-id-v1",
                       "messages_sha256": None},
            "time_axis": "vod_ms", "reactions": rx,
            "segments": [{"start_ms": 0, "end_ms": 600_000, "category": "just_chatting"}]}


def topic_doc(run_id, video_no):
    return {"schema": T.SCHEMA, "schema_version": T.SCHEMA_VERSION, "run_id": run_id,
            "created_at": "2026-10-23T08:30:00+09:00",
            "engine": {"name": "synthetic-topics", "version": "0.0.1", "config_sha256": None, "embedding": None},
            "target": {"video_no": str(video_no), "messages_sha256": None, "asr_sha256": None},
            "time_axis": T.TIME_AXIS, "window_ms": 20_000, "range": {"start_ms": 100_000, "end_ms": 400_000},
            "stats": {"windows": 5, "blocks": 2},
            "blocks": [{"index": 0, "start_ms": 100_000, "end_ms": 200_000, "keywords": ["떡볶이", "분식집"],
                        "name_keywords": ["떡볶이"], "top_keyword": "떡볶이", "change_ms": None, "boundary_score": None,
                        "utterances": 4, "chats": 3},
                       {"index": 1, "start_ms": 200_000, "end_ms": 400_000, "keywords": ["라면", "김밥"],
                        "name_keywords": ["라면"], "top_keyword": "라면", "change_ms": 200_000, "boundary_score": 0.4,
                        "utterances": 5, "chats": 3}],
            "gaps": [{"at_ms": 200_000, "similarity": 0.1, "depth": 0.4}]}


def words_doc(run_id, pid, video_no):
    return {"schema": "chatcoach/transcript-words", "schema_version": 1, "run_id": run_id,
            "created_at": "2026-10-23T10:00:00+09:00",
            "engine": {"name": "transcript_words", "version": "0.1.0", "config_sha256": None, "textnorm": TN.VERSION},
            "target": {"project_id": pid, "video_no": str(video_no), "asr_sha256": "a" * 64},
            "time_axis": "vod_ms",
            "calibration": {"source": "project.json", "rev": 0, "method": "single_offset", "status": "verified",
                            "offset_ms": 0, "file_start_in_vod_ms": 0},
            "window_ms": 10_000, "range": {"start_ms": 0, "end_ms": 600_000},
            "windows": [{"start_ms": 90_000, "end_ms": 100_000, "words": sorted(["떡볶이", "치즈", SECRET_WORD]),
                         "segments": 1},
                        {"start_ms": 180_000, "end_ms": 190_000, "words": ["라면", "순서"], "segments": 1}]}


#: 합성 방송 번호의 밑(지어낸 둥근 수 — 실제 VOD 번호가 아니다. test_fixture_clean 의 'video_no = 숫자' 규칙은
#: 고정 자료에 실제 번호를 적는 일을 막는 것이라, 여기서는 이름 있는 합성 상수로 둔다)
SYNTH_VNO_BASE = 15 * 10 ** 6


def _b(d) -> bytes:
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


#: 봉인할 엔진 판 (engine.version, run_id 앞머리) — 봉인 차례대로. v0.2 판은 두 군데 다르게 본다
ENGINE_RUNS = (("0.2.0", "run-v02"), ("0.3.0", "run-v03"))
#: 실제 engine/link.py 가 싣는 값(v0.2 규칙 = '0.1.0', v0.3 = 'link-v0.3'), v0.3 를 먼저 봉인
REAL_ENGINE_RUNS_V03_FIRST = (("link-v0.3", "run-v03"), ("0.1.0", "run-v02"))


def _write_sample_manifest(root, seg_rec, n=5):
    """1차 세션 구간의 합성 표본 매니페스트(app.core.sampling.build_manifest — 앱이 쓰는 것과 같은 모양)를 samples\\ 에 쓴다."""
    from app import relabel as rl
    from app.core import sampling as sp
    data = project.open_project(root).data
    gv, th_sha, _vals = rl._active_guide(data)
    msgs = jsonl.read(os.path.join(root, "chat", "messages.jsonl")).records
    with builtins.open(os.path.join(root, "chat", "messages.jsonl"), "rb") as f:
        msha = hashlib.sha256(f.read()).hexdigest()
    pop = sp.build_population(msgs, 0, 600_000, [])
    m = sp.build_manifest(project_id=data["project_id"],
                          source_segment={"session_id": P_ID, "entity_id": seg_rec["entity_id"],
                                          "version_id": seg_rec["id"], "start_ms": 0, "end_ms": 600_000},
                          guide_version=gv, thresholds_sha256=th_sha, sample_seed=data["sample_seed"], draw_no=1,
                          population=pop, messages_sha256=msha, n=n, census_max=n,
                          drawn_at=T_P.isoformat(timespec="seconds"), drawn_by="L01", app_version="0.0.1")
    d = os.path.join(root, "samples")
    os.makedirs(d, exist_ok=True)
    with builtins.open(os.path.join(d, m["sample_set_id"] + ".json"), "wb") as f:
        f.write(json.dumps(m, ensure_ascii=False).encode("utf-8"))
    return m["sample_set_id"]


def build_pilot(projects_root, idx, *, seal_results=True, engine_runs=ENGINE_RUNS, sample=False):
    """합성 시범 프로젝트 하나(1차 세션 기록까지, 잠그지 않음). 엔진 2판을 사전 봉인(짝 주제 흐름·전사 낱말·벡터).
    sample=True 면 1차 구간의 표본 매니페스트를 samples\\ 에 하나 둔다(잠글 때 sample_manifests 에 실린다)."""
    video_no = SYNTH_VNO_BASE + idx
    pid = f"22222222-0000-4000-8000-{idx:012x}"
    p = project.create(str(projects_root), "pilot", video_no, "시험", "L01", "0.0.1", T_SEAL - timedelta(hours=1), pid, SALT,
                       guide=project.GuideSource("0.1", b"# guide\n", {"reaction": {"time_window_sec": 20}}),
                       machine4="m3fa")
    root = p.root
    assert os.path.basename(root) == f"pilot-{video_no}"

    def verify(d):
        d["calibration"]["revs"][0]["status"] = "verified"
    project.open_project(root).update(verify, by="L01")
    jsonl.write_all(os.path.join(root, "chat", "messages.jsonl"), _messages())
    a = _store(root, P_ID, "primary", T_P, 0, at=T_P)
    _base(a)
    for n, g in GRADES.items():
        t = 100_000 + n * 5_000
        if g == "unconfirmed":
            _react(a, cid(n), None, result="unconfirmed", t=t)
        else:
            _react(a, cid(n), g, t=t)
    seg_rec = next(r for r in a._own.records if r.get("kind") == "segment") if sample else None
    a.close()
    sample_id = _write_sample_manifest(root, seg_rec) if sample else None
    shas = []
    if seal_results:
        for ver, pre in engine_runs:
            run = f"{pre}-{idx}"
            g = dict(GRADES)
            if pre == "run-v02":
                g[3], g[11] = "likely", "direct"                   # v0.2 가 두 군데 다르게 본다
            e = seal.seal_bytes(root, _b(ai_doc(run, ver, video_no, g)), source="engine_link", by="L01", now=T_SEAL)
            assert e.check == "valid"
            seal.seal_topics_bytes(root, _b(topic_doc(f"topics-{run}", video_no)), pair_sha256=e.sha256, by="L01",
                                   source="engine_topics", now=T_SEAL)
            w = seal.seal_words_bytes(root, _b(words_doc(f"words-{run}", pid, video_no)), pair_sha256=e.sha256,
                                      by="L01", source="engine_words", now=T_SEAL)
            assert w.check == "valid"
            from tests.test_seal import vectors_doc       # 합성 창별 벡터(4차원 2창, 창 글 없음) — 짝 셋째
            v = seal.seal_vectors_bytes(root, _b(vectors_doc(project_id=pid, video_no=str(video_no), run_id=f"tvec-{run}")),
                                        pair_sha256=e.sha256, by="L01", source="engine_vectors", now=T_SEAL)
            assert v.check == "valid"
            shas.append(e.sha256)
    return {"root": root, "pid": pid, "video_no": video_no, "shas": shas, "sample_id": sample_id}


def lock_primary(root, sample_id=None):
    st = _store(root, P_ID, "primary", T_P, 900, at=T_P_LOCK)     # 앞 기록과 겹치지 않는 uuid
    ver = [] if sample_id is None else [{"sample_set_id": sample_id, "ok": True, "problems": 0}]
    st.lock_session(sample_verify=ver)
    st.close()


def make_plan_and_relabel(root, *, lock=True):
    data = project.open_project(root).data
    plan = relabel.create_plan(root, data, session_kind="relabel_same", labeler_id="L01", source_session_id=P_ID,
                               done_ranges=[(0, 600_000)], created_by="L01", created_machine4="aaaa",
                               app_version="0.0.1", creator_kind=None, now=T_PLAN, rand4="aa01")
    b = _store(root, RS_ID, "relabel_same", T_RS, 1000, plan_id=plan["plan_id"], at=T_RS)
    _base(b)
    for n, g in GRADES.items():
        t = 100_000 + n * 5_000
        if g == "unconfirmed":
            _react(b, cid(n), None, result="unconfirmed", t=t)
        else:
            _react(b, cid(n), g, t=t)
    if lock:
        b.lock_session(sample_verify=[])
    b.close()
    return plan


def ready_pilot(projects_root, idx, **kw):
    pj = build_pilot(projects_root, idx, **kw)
    lock_primary(pj["root"], pj["sample_id"])
    make_plan_and_relabel(pj["root"])
    return pj


def tree(root) -> dict:
    """폴더 아래 파일 → sha256 (바뀐 것이 없는지 보려고)."""
    out = {}
    for dp, _dn, fn in os.walk(root):
        for n in fn:
            p = os.path.join(dp, n)
            with builtins.open(p, "rb") as f:
                out[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def _data(tmp_path):
    base = tmp_path / "chatcoach-data"
    projects = base / "projects"
    projects.mkdir(parents=True)
    return base, projects


# ───────────────────────────────────────────────────────────── 열기 감시

def test_open_watch_blocks_label_and_any_write_in_conditions_mode(tmp_path):
    g = tmp_path / "pilot-1"
    (g / "labels").mkdir(parents=True)
    (g / "locks").mkdir()
    (g / "project.json").write_text("{}", encoding="utf-8")
    (g / "locks" / "x.json").write_text("{}", encoding="utf-8")
    (g / "labels" / "x.jsonl").write_text("{}", encoding="utf-8")
    deny = tmp_path / "ai"
    deny.mkdir()
    (deny / "s.jsonl").write_text("", encoding="utf-8")
    w = day.OpenWatch([str(g)], conditions_only=True, deny=[str(deny)], write_ok=None)
    with w:
        with open(g / "project.json", "rb") as f:
            f.read()
        with open(g / "locks" / "x.json", encoding="utf-8") as f:
            f.read()
        with pytest.raises(day.WatchViolation):
            open(g / "labels" / "x.jsonl", "rb")
        with pytest.raises(day.WatchViolation):
            open(g / "project.json", "ab")
        with pytest.raises(day.WatchViolation):
            os.open(str(g / "locks" / "new.json"), os.O_WRONLY | os.O_CREAT)
        with pytest.raises(day.WatchViolation):
            open(deny / "s.jsonl", "rb")
        with pytest.raises(day.WatchViolation):
            open(tmp_path / "anywhere.txt", "w")
    assert builtins.open is not None and open is builtins.open          # 되돌려 놓는다
    s = w.summary()
    assert s["guarded_reads"] == 2 and s["violations"] == 5
    assert s["violation_codes"] == {"open_denied_root": 1, "read_outside_conditions:labels": 1,
                                    "write_in_pilot_project": 2, "write_outside_allowed": 1}
    assert not (g / "locks" / "new.json").exists() and not (tmp_path / "anywhere.txt").exists()


def test_reason_codes_have_no_ids():
    assert day.reason_code("잠기지 않은 라벨링 세션 L01-P-20261024-ab12") == day.R_UNLOCKED
    assert day.reason_code("1차 세션이 없습니다") == day.R_NO_PRIMARY
    assert day.reason_code("재라벨 계획이 없는 1차 세션 L01-P-20261024-ab12") == day.R_NO_PLAN
    assert day.reason_code("잠긴 재라벨 세션이 없는 계획 RP-1") == day.R_RELABEL_PENDING
    assert day.reason_code("재현 검증이 통과하지 않아 보고서를 만들지 않습니다(06 7-1): x") == day.R_SAMPLE_VERIFY
    assert day.reason_code("읽지 못한 재라벨 계획 RP-1 (계획 파일이 깨졌거나 형식이 아닙니다)") == day.R_PLAN_UNREADABLE
    assert day.reason_code("L01-RS-20261019-cd34 의 재라벨 계획 RP-1 를 읽지 못했습니다") == day.R_PLAN_UNREADABLE
    assert day.reason_code("무엇") == day.R_OTHER
    # 도구의 사유 코드 = seal 의 사유 코드(+ 도구에만 있는 pilot_count·open_watch)
    assert {day.R_UNLOCKED, day.R_NO_PRIMARY, day.R_NO_PLAN, day.R_RELABEL_PENDING, day.R_PLAN_UNREADABLE,
            day.R_SAMPLE_VERIFY, day.R_OTHER} == set(seal.UNSEAL_REASON_CODES)
    assert day.version_label("0.3.1") == "v0.3" and day.version_label(None) == "v?"
    # 실제 engine/link.py 의 값: v0.2 규칙 결과는 '0.1.0'(판 번호를 올리지 않음) — 'v0.1' 로 적지 않는다
    from engine import link
    assert day.LINK_RULES_VERSION == link.ENGINE_VERSION
    assert day.version_label(link.ENGINE_VERSION) == "v0.2(0.1.0)"
    assert day.version_label(link.ENGINE_VERSION_V03) == "v0.3"
    assert day.version_key(link.ENGINE_VERSION) < day.version_key(link.ENGINE_VERSION_V03)
    assert day.version_key("0.10.0") > day.version_key("0.3.0") > day.version_key("0.2.9")


# ───────────────────────────────────────────────────────────── dry-run: 조건만, 아무것도 쓰지 않음

def test_dry_run_reports_unmet_reasons_and_writes_nothing(tmp_path, capsys):
    base, projects = _data(tmp_path)
    a = build_pilot(projects, 1)                       # 1차가 잠기지 않음
    b = build_pilot(projects, 2)
    lock_primary(b["root"])                            # 잠겼지만 재라벨 계획 없음
    c = build_pilot(projects, 3)
    lock_primary(c["root"])
    make_plan_and_relabel(c["root"], lock=False)       # 계획은 있으나 재라벨이 안 잠김
    before = tree(base)
    out_root = base / "reports"
    code = day.main(["--projects-root", str(projects), "--out-root", str(out_root), "--dry-run",
                     "--freeze", str(base / "pilot" / "freeze.json")])
    printed = capsys.readouterr().out
    assert code == 3
    assert tree(base) == before and not out_root.exists()          # 아무것도 쓰지 않음 (AI 결과 폴더도 그대로)
    code2, s = day.run_day(day.find_pilots(str(projects)), out_root=str(out_root), dry_run=True, freeze_path=None)
    assert code2 == 3 and tree(base) == before
    per = {p["name"]: p for p in s["conditions"]["projects"]}
    assert per[os.path.basename(a["root"])]["reasons"] == [day.R_NO_PLAN, day.R_UNLOCKED]
    assert per[os.path.basename(b["root"])]["reasons"] == [day.R_NO_PLAN]
    assert per[os.path.basename(c["root"])]["reasons"] == [day.R_RELABEL_PENDING, day.R_UNLOCKED]
    assert s["conditions"]["labeling_sessions"] == 4 and s["conditions"]["locked"] == 2
    assert s["watch"]["violations"] == 0 and s["watch"]["writes"] == 0 and s["watch"]["guarded_reads"] > 0
    assert s["user_clicks"] == 0 and s["projects"] == []
    for blob in (printed, json.dumps(s, ensure_ascii=False)):
        assert P_ID not in blob and RS_ID not in blob and SECRET_TEXT not in blob    # 세션 ID 도 남기지 않는다


def test_dry_run_corrupt_plan_file_is_plan_unreadable(tmp_path, capsys):
    """합성 시범 하나의 relabel\\ 에 깨진 계획 파일(찢어진 쓰기)이 있다 → 그 편만 사유 plan_unreadable 이고 계획 ID 가
    plans_unreadable·표준 출력에 나온다. 예전에는 relabel_not_locked 로 적혀 해제 당일에 잠기지 않은 재라벨을 찾게 됐다.
    막는 판정은 그대로(종료 3, 아무것도 풀지도 쓰지도 않음)."""
    base, projects = _data(tmp_path)
    pjs = [ready_pilot(projects, i) for i in (1, 2, 3)]
    bad = "RP-20261018-zz99"
    with builtins.open(relabel.plan_path(pjs[0]["root"], bad), "wb") as f:
        f.write(b'{"schema": "chatcoach/relabel-plan", "plan_id": "RP-2026')
    before = tree(base)
    out_root = base / "reports"
    code = day.main(["--projects-root", str(projects), "--out-root", str(out_root), "--dry-run",
                     "--freeze", str(base / "pilot" / "freeze.json")])
    printed = capsys.readouterr().out
    assert code == 3 and tree(base) == before and not out_root.exists()
    code2, s = day.run_day(day.find_pilots(str(projects)), out_root=str(out_root), dry_run=True, freeze_path=None)
    assert code2 == 3 and s["watch"]["violations"] == 0 and s["projects"] == []
    name = os.path.basename(pjs[0]["root"])
    per = {p["name"]: p for p in s["conditions"]["projects"]}
    assert per[name]["reasons"] == [day.R_PLAN_UNREADABLE] and per[name]["plans_unreadable"] == [bad]
    assert not per[name]["met"] and s["conditions"]["reason_kinds"] == [day.R_PLAN_UNREADABLE]
    assert all(p["met"] and p["reasons"] == [] and p["plans_unreadable"] == [] for n, p in per.items() if n != name)
    line = next(x for x in printed.splitlines() if x.strip().startswith(name + ":"))
    assert "사유 plan_unreadable" in line and f"relabel/{bad}.json" in line
    assert day.R_RELABEL_PENDING not in printed and P_ID not in printed and RS_ID not in printed
    assert all(seal.seal_status(p["root"]).unsealed_count == 0 for p in pjs)


def test_dry_run_never_opens_labels_or_ai_folder(tmp_path, monkeypatch):
    """열기 감시와 따로, 진짜 open 을 엿보아 시범 폴더의 labels\\·chat\\ 과 AI 결과 폴더가 한 번도 열리지 않음을 본다."""
    base, projects = _data(tmp_path)
    for i in (1, 2, 3):
        ready_pilot(projects, i)
    seen = []
    real = builtins.open

    def spy(file, *a, **kw):
        if not isinstance(file, int):
            seen.append(os.path.normcase(os.path.abspath(os.fspath(file))))
        return real(file, *a, **kw)
    monkeypatch.setattr(builtins, "open", spy)
    code, s = day.run_day(day.find_pilots(str(projects)), out_root=str(base / "reports"), dry_run=True,
                          freeze_path=None)
    monkeypatch.undo()
    assert code == 0 and s["conditions"]["met"] and s["conditions"]["locked"] == 6
    ai = os.path.normcase(str(base / "ai"))
    assert seen and not [p for p in seen if p.startswith(ai)]
    salt = os.path.join("chat", "protected", "salt.json")
    for p in seen:
        if os.path.normcase(str(projects)) in p:
            rel = p.split(os.sep)
            assert any(x in rel for x in ("project.json", "locks", "relabel", "samples")) or p.endswith(salt), p
            assert "labels" not in rel and "messages.jsonl" not in rel and "derived" not in rel
    assert not (base / "reports").exists()


def test_pilot_count_mismatch_blocks(tmp_path):
    base, projects = _data(tmp_path)
    ready_pilot(projects, 1)
    ready_pilot(projects, 2)
    code, s = day.run_day(day.find_pilots(str(projects)), out_root=str(base / "reports"), freeze_path=None)
    assert code == 3 and s["conditions"]["reason_kinds"] == [day.R_PILOT_COUNT] and s["projects"] == []
    st = seal.seal_status(day.find_pilots(str(projects))[0])
    assert st.unsealed_count == 0                                     # 실제 실행이어도 아무것도 풀지 않음


# ───────────────────────────────────────────────────────────── 리허설: 해제 당일 끝까지

def test_rehearsal_unseal_compare_calib_summary_zero_clicks(tmp_path, capsys):
    base, projects = _data(tmp_path)
    pjs = [ready_pilot(projects, i) for i in (1, 2, 3)]
    roots = [p["root"] for p in pjs]
    before = {r: tree(r) for r in roots}
    # 봉인 상태: 해제 전에는 시범 최종 세트 보정도 run 맥락 없이(해제 안 됨) — 조건은 됨
    assert all(seal.seal_status(r).sealed_count == 2 for r in roots)
    out_root = base / "reports"
    code = day.main(["--projects-root", str(projects), "--out-root", str(out_root), "--date", "20261101",
                     "--freeze", str(base / "pilot" / "freeze.json"), "--go"])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "되돌릴 수 없는 해제" in printed and "실패 0" in printed and "대조 ok 2건" in printed
    d = out_root / "pilot-unseal-20261101"
    names = sorted(os.listdir(d))
    assert names == sorted(["ai-compare", "agreement", "summary.json", "summary.md", "calib-pooled.json"]
                           + [f"calib-{os.path.basename(r)}.json" for r in roots])
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    md = (d / "summary.md").read_text(encoding="utf-8")
    assert s["user_clicks"] == 0 and s["conditions"]["met"] and s["errors"] == []
    assert s["watch_run"]["violations"] == 0 and s["watch"]["violations"] == 0
    for p in s["projects"]:
        assert p["errors"] == []
        u = p["unseal"]
        assert (u["sealed_valid"], u["unsealed_now"], u["already"], u["rejected"], u["presealed"]) == (2, 2, 0, 0, 2)
        assert (u["topics"], u["words"], u["vectors"]) == (2, 2, 2) and u["pair_errors"] == []
        # 푼 뒤 대조(tools/ai_seal.verify_project): 결과·짝 넷 모두 해시가 맞다
        assert u["verify"]["ok"] and u["verify"]["runs"] == 2 and u["verify"]["engine_versions"] == ["0.2.0", "0.3.0"]
        assert u["verify"]["codes"] == {"run:ok": 2, "topics:ok": 2, "vectors:ok": 2, "words:ok": 2}
        # 일치도 보고서가 없으면 이 도구가 만든다(요약 폴더 agreement\) — 사람 기준이 '미확인' 으로 남지 않는다
        assert p["compare"]["agreement_made"] == 1 and p["compare"]["agreement_errors"] == []
        assert p["compare"]["gate_report_id"] and p["compare"]["human_gate"] is not None
        runs = p["compare"]["runs"]
        assert sorted(r["label"] for r in runs) == ["v0.2", "v0.3"]
        v3 = next(r for r in runs if r["label"] == "v0.3")
        assert v3["pre_sealed"] is True and v3["e1_lenient"]["n"] >= 8
        assert v3["e1_lenient"]["exact"] == pytest.approx(1.0)          # v0.3 은 사람과 같은 값
        pair = p["compare"]["pairs"]
        assert len(pair) == 1 and (pair[0]["a"]["label"], pair[0]["b"]["label"]) == ("v0.2", "v0.3")
        assert pair[0]["counts"]["grade_diff"] == 1 and pair[0]["counts"]["a_only"] == 1
        assert pair[0]["human"]["b_matches"] == 2 and pair[0]["human"]["a_matches"] == 0
        cb = p["calib"]
        assert cb["groups"]["reacted"] == 5 and cb["groups"]["labelled_none"] == 3   # 직접 반응 5 · 반응 미확인 3
        assert cb["words"] == "ok" and cb["topic_blocks"] == 2 and cb["transcript_windows"] == 2
        assert cb["context_run"].startswith("run-v03-")             # 화면과 같은 기본 run(판이 가장 높은 것)
    pooled = s["calib_pooled"]
    assert pooled["groups"]["reacted"] == 15 and pooled["groups"]["labelled_none"] == 9
    # AI 비교 보고서: 프로젝트마다 run 2개
    for pj in pjs:
        reps = os.listdir(d / "ai-compare" / pj["pid"])
        assert len(reps) == 2
    # 시범 프로젝트 폴더는 한 글자도 바뀌지 않았다(쓰기는 AI 결과 폴더·요약 폴더만)
    assert {r: tree(r) for r in roots} == before
    # 본문·닉네임·전사 낱말·세션 ID 는 요약·보정 파일·출력에 없다
    blob = md + (d / "summary.json").read_text(encoding="utf-8") + printed + "".join(
        (d / n).read_text(encoding="utf-8") for n in names if n.startswith("calib-"))
    for secret in (SECRET_TEXT, SECRET_NICK, SECRET_WORD, *TEXTS, "떡볶이", P_ID, RS_ID):
        assert secret not in blob, secret
    rblob = b"".join(open(os.path.join(dp, n), "rb").read() for dp, _, ns in os.walk(d / "ai-compare") for n in ns)
    for secret in (SECRET_TEXT, SECRET_NICK, SECRET_WORD):
        assert secret.encode("utf-8") not in rblob
    assert "사람 클릭 **0**" in md and "| v0.2 |" in md and "| 묶음 |" in md
    # 다시 돌리면: 이미 풀린 것은 그대로, 새 폴더(-2)
    code2, s2 = day.run_day(roots, out_root=str(out_root), date="20261101", freeze_path=None)
    assert code2 == 0 and s2["out_dir_name"] == "pilot-unseal-20261101-2"
    assert all(p["unseal"]["unsealed_now"] == 0 and p["unseal"]["already"] == 2 for p in s2["projects"])
    # 두 번째는 첫 번째가 만든 일치도 보고서를 찾지 못하므로(요약 폴더 안) 새로 만든다 — 같은 기준
    assert all(p["compare"]["agreement_made"] == 1 for p in s2["projects"])


def test_calib_final_set_refuses_pilot_until_locked(tmp_path):
    base, projects = _data(tmp_path)
    pj = build_pilot(projects, 1)
    with pytest.raises(cal.CalibError) as ei:
        cal.calibrate(pj["root"], final_set="auto")
    assert ei.value.code == 1 and "잠기기 전" in str(ei.value)
    with pytest.raises(cal.CalibError) as ei:
        cal.calibrate(pj["root"])                        # 손라벨 길은 시범이면 언제나 거부
    assert ei.value.code == 1
    lock_primary(pj["root"])
    make_plan_and_relabel(pj["root"])
    rep = cal.calibrate(pj["root"], final_set="auto", quick=True)    # 잠긴 뒤: 해제 전이면 맥락 없음
    assert rep["input"]["pilot"] and rep["input"]["groups"]["reacted"] == 5
    assert rep["input"]["unsealed_runs"] == 0 and rep["input"]["context_run"] is None
    assert rep["input"]["words"] == "missing" and rep["input"]["topic_blocks"] == 0
    with pytest.raises(cal.CalibError):
        cal.calibrate(pj["root"], final_set="auto", topics_path=str(tmp_path / "x.json"))
    # 앱이 만든 최종 세트 zip(export_final_set)으로도 같은 숫자 — zip 에는 평가 제외 행이 없다는 표시만 다르다
    from app import export
    z = str(tmp_path / "final-set.zip")
    export.export_final_set(pj["root"], z, created_by="L01")
    rz = cal.calibrate(pj["root"], final_set=z, quick=True)
    assert rz["input"]["groups"] == rep["input"]["groups"]
    assert rz["input"]["final_set"]["source"] == "zip" and rz["input"]["final_set"]["messages_match"] is True
    assert rz["variants"]["base"]["groups"] == rep["variants"]["base"]["groups"]


# ───────────────────────────────────────────────────────────── 검토 고침: 해제 실패는 종료 1 (가려지지 않는다)

def _go(projects, out_root, capsys, *extra):
    code = day.main(["--projects-root", str(projects), "--out-root", str(out_root), "--date", "20261102",
                     "--freeze", str(out_root / "no-freeze.json"), "--go", *extra])
    return code, capsys.readouterr().out


def test_unsealable_valid_seal_is_error_exit_1(tmp_path, capsys):
    """run_id 가 같고 바이트만 다른 결과를 하나 더 봉인(둘 다 valid) → 둘째는 seal.unseal 이 SealError 로 거부.
    예전에는 종료 0 · '오류 0' 이었다(실패는 요약 md 의 '실패' 열에만). 이제 오류 'unseal:<원인 코드>'(여기서는 dup_run_id)와 종료 1."""
    base, projects = _data(tmp_path)
    pj1 = build_pilot(projects, 1)
    dup = ai_doc("run-v03-1", "0.3.0", pj1["video_no"], GRADES)
    dup["created_at"] = "2026-10-23T08:00:01+09:00"
    assert seal.seal_bytes(pj1["root"], _b(dup), source="engine_link", by="L01", now=T_SEAL).check == "valid"
    lock_primary(pj1["root"])
    make_plan_and_relabel(pj1["root"])
    for i in (2, 3):
        ready_pilot(projects, i)
    out_root = base / "reports"
    code, printed = _go(projects, out_root, capsys)
    assert code == 1, printed
    s = json.loads((out_root / "pilot-unseal-20261102" / "summary.json").read_text(encoding="utf-8"))
    p1 = next(p for p in s["projects"] if p["name"] == os.path.basename(pj1["root"]))
    # 원인 코드(seal.error_code): 예외 이름 'SealError' 가 아니라 '같은 run_id 이미 해제' 를 뜻하는 코드
    assert p1["unseal"]["failed"] == ["dup_run_id"] and p1["unseal"]["unsealed_now"] == 2
    assert "dup_run_id" in seal.SEAL_ERROR_CODES
    assert p1["errors"] == ["unseal:dup_run_id"]
    assert all(p["errors"] == [] for p in s["projects"] if p is not p1)
    assert seal.seal_status(pj1["root"]).sealed_count == 1             # 앱에는 '봉인됨 1건' 으로 남는다 — 그래서 오류
    line = next(x for x in printed.splitlines() if os.path.basename(pj1["root"]) + ": 해제" in x)
    assert "실패 1" in line and "오류 1 (unseal:dup_run_id)" in line
    md = (out_root / "pilot-unseal-20261102" / "summary.md").read_text(encoding="utf-8")
    assert "## 오류 (코드)" in md and "- unseal:dup_run_id" in md


def test_truncated_seal_jsonl_is_named_not_hidden_behind_calib(tmp_path, capsys):
    """seal.jsonl 끝 줄이 잘림(기록 중 전원 차단) → 두 봉인 모두 풀리지 않는다. 예전에는 원인이 'calib:CalibError' 로만
    적히고 대조는 'ok 0건' 이었다. 이제 'seal_jsonl:problems'·'unseal:seal_jsonl_corrupt' 가 적히고 대조는 '-'."""
    base, projects = _data(tmp_path)
    pjs = [ready_pilot(projects, i) for i in (1, 2, 3)]
    sj = os.path.join(seal.project_ai_dir(pjs[0]["root"], pjs[0]["pid"]), "seal.jsonl")
    with builtins.open(sj, "ab") as f:
        f.write(b'{"op": "seal", "sha2')
    assert seal.seal_status(pjs[0]["root"]).problems
    out_root = base / "reports"
    code, printed = _go(projects, out_root, capsys)
    assert code == 1, printed
    s = json.loads((out_root / "pilot-unseal-20261102" / "summary.json").read_text(encoding="utf-8"))
    p1 = s["projects"][0]
    assert p1["name"] == os.path.basename(pjs[0]["root"])
    assert p1["errors"].count("unseal:seal_jsonl_corrupt") == 2 and "seal_jsonl:problems" in p1["errors"]
    assert "seal_jsonl_corrupt" in seal.SEAL_ERROR_CODES
    line = next(x for x in printed.splitlines() if p1["name"] + ": 해제" in x)
    assert "대조 -" in line and "실패 2" in line and "seal_jsonl:problems" in line
    assert day.verify_text({"runs": 0, "ok": True}) == "-" and day.verify_text({"runs": 2, "ok": True}) == "ok 2건"


# ───────────────────────────────────────────────────────────── 검토 고침: 실제 폴더는 --real, 해제는 --go

def test_real_data_folder_needs_real_flag_and_is_not_opened(tmp_path, monkeypatch, capsys):
    """기본 폴더가 없다. 실제 데이터 폴더(여기서는 DATA_DIR 를 합성 폴더로 바꿔 흉내)를 --real 없이 가리키면 아무것도 열기 전에
    종료 2. --real 만 주면 dry-run(아무것도 풀지 않음)."""
    base, projects = _data(tmp_path)
    for i in (1, 2, 3):
        ready_pilot(projects, i)
    roots = day.find_pilots(str(projects))
    monkeypatch.setattr(day, "DATA_DIR", str(base))
    touched = []
    real_listdir, real_open = os.listdir, builtins.open

    def spy_listdir(p="."):
        touched.append(("listdir", os.fspath(p)))
        return real_listdir(p)

    def spy_open(file, *a, **kw):
        if not isinstance(file, int):
            touched.append(("open", os.fspath(file)))
        return real_open(file, *a, **kw)
    monkeypatch.setattr(os, "listdir", spy_listdir)
    monkeypatch.setattr(builtins, "open", spy_open)
    under = os.path.normcase(str(base))
    for argv in (["--projects-root", str(projects)], ["--project", roots[0]], ["--projects-root", str(projects), "--go"],
                 [], ["--go"], ["--projects-root", str(tmp_path / "x"), "--go", "--dry-run"]):
        touched.clear()
        assert day.main(argv) == 2, argv
        assert not [t for t in touched if os.path.normcase(os.path.abspath(t[1])).startswith(under)], (argv, touched)
    with pytest.raises(day.DayError) as ei:
        day.run_day(roots, out_root=str(tmp_path / "r"), freeze_path=None)
    assert ei.value.code == 2
    monkeypatch.setattr(os, "listdir", real_listdir)
    monkeypatch.setattr(builtins, "open", real_open)
    capsys.readouterr()
    assert day.main(["--real"]) == 0                                    # --real 만: 기본 projects 폴더, dry-run
    assert "dry-run" in capsys.readouterr().out
    assert all(seal.seal_status(r).unsealed_count == 0 for r in roots) and not (base / "reports").exists()


def test_default_without_go_is_dry_run(tmp_path, capsys):
    base, projects = _data(tmp_path)
    roots = [ready_pilot(projects, i)["root"] for i in (1, 2, 3)]
    before = tree(base)
    assert day.main(["--projects-root", str(projects)]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out and "--go" in out and "되돌릴 수 없는 해제" not in out
    assert tree(base) == before and not (base / "reports").exists()
    assert all(seal.seal_status(r).unsealed_count == 0 for r in roots)


# ───────────────────────────────────────────────────────────── 검토 고침: 실제 엔진 판 이름·기본 run·시범 asr

def test_real_engine_versions_v03_sealed_first_and_derived_asr_present(tmp_path, capsys):
    """실제 engine/link.py 의 판 값('0.1.0' = v0.2 규칙, 'link-v0.3')으로, v0.3 을 먼저 봉인. 시범 폴더에 derived\\asr.json
    (봉인된 전사 낱말의 asr 해시와 다른 내용)이 있어도 보정은 시범 규칙(asr 해시를 보지 않음)으로 전사 낱말을 쓰고, 맥락
    run 은 화면과 같은 기본 run(판이 가장 높은 v0.3)이다. 요약의 판 이름은 'v0.2(0.1.0)'·'v0.3'."""
    base, projects = _data(tmp_path)
    pjs = [ready_pilot(projects, i, engine_runs=REAL_ENGINE_RUNS_V03_FIRST) for i in (1, 2, 3)]
    for pj in pjs:
        os.makedirs(os.path.join(pj["root"], "derived"), exist_ok=True)
        with builtins.open(os.path.join(pj["root"], "derived", "asr.json"), "wb") as f:
            f.write(b'{"synthetic": true}\n')
    out_root = base / "reports"
    code, printed = _go(projects, out_root, capsys)
    assert code == 0, printed
    d = out_root / "pilot-unseal-20261102"
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    md = (d / "summary.md").read_text(encoding="utf-8")
    for p in s["projects"]:
        assert sorted(r["label"] for r in p["compare"]["runs"]) == ["v0.2(0.1.0)", "v0.3"]
        pair = p["compare"]["pairs"][0]
        assert (pair["a"]["label"], pair["b"]["label"]) == ("v0.2(0.1.0)", "v0.3")
        assert pair["human"]["b_matches"] == 2
        cb = p["calib"]
        assert cb["context_run"].startswith("run-v03-") and cb["words"] == "ok" and cb["transcript_windows"] == 2
    assert "| v0.2(0.1.0) | v0.3 |" in md and "맥락 run run-v03-" in md and "전사 낱말 ok" in md


def test_words_code_pilot_skips_asr_hash(tmp_path):
    base, projects = _data(tmp_path)
    pj = build_pilot(projects, 1, seal_results=False)
    os.makedirs(os.path.join(pj["root"], "derived"), exist_ok=True)
    with builtins.open(os.path.join(pj["root"], "derived", "asr.json"), "wb") as f:
        f.write(b"{}")
    pdata = project.open_project(pj["root"]).data
    doc = words_doc("w-1", pj["pid"], pj["video_no"])
    assert cal.words_code(pj["root"], doc, pdata) == "ok"                       # 시범: asr.json 을 열지 않는다
    assert cal.words_code(pj["root"], doc, pdata, pilot=False) == "stale_asr"   # 시범 아님: 해시를 본다


# ───────────────────────────────────────────────────────────── 검토 고침: 표본 매니페스트가 있는 잠금

def test_rehearsal_with_sample_manifest_lock(tmp_path, capsys):
    """1차 잠금에 표본 매니페스트(samples\\S-*.json)와 통과한 재현 검증이 실린 시범 하나 — comparison_gate 의 표본 가지와
    verify_plan_on_disk(매니페스트 있음)를 지난다. dry-run 조건 충족 → 해제 당일 끝까지 종료 0."""
    base, projects = _data(tmp_path)
    pjs = [ready_pilot(projects, 1, sample=True)] + [ready_pilot(projects, i) for i in (2, 3)]
    root = pjs[0]["root"]
    with builtins.open(os.path.join(root, "locks", f"{P_ID}.json"), "rb") as f:
        lock = json.loads(f.read())
    assert [m["sample_set_id"] for m in lock["sample_manifests"]] == [pjs[0]["sample_id"]]
    assert lock["sample_verify"] and lock["sample_verify"][0]["ok"] is True
    (plan,) = relabel.list_plans(root)
    assert relabel.verify_plan_on_disk(root, project.open_project(root).data, plan.plan_id, creator_kind=None).ok
    code, s = day.run_day(day.find_pilots(str(projects)), out_root=str(base / "reports"), dry_run=True)
    assert code == 0 and s["conditions"]["met"], s["conditions"]
    out_root = base / "reports"
    code, printed = _go(projects, out_root, capsys)
    assert code == 0, printed
    s = json.loads((out_root / "pilot-unseal-20261102" / "summary.json").read_text(encoding="utf-8"))
    assert all(p["errors"] == [] for p in s["projects"])
    p1 = s["projects"][0]
    assert p1["unseal"]["unsealed_now"] == 2 and p1["compare"]["agreement_made"] == 1


# ───────────────────────────────────────────────────────────── D-26: 1편 · 재라벨 없이 해제 (--allow-no-relabel · --only)

def _single_primary_pilot(projects, idx=1, **kw):
    """합성 시범 하나: 1차 세션만 잠김, 재라벨 계획 없음(relabel\\ 도 없음) — D-26 의 '1편 · 1인 · 재라벨 없음' 모양."""
    pj = build_pilot(projects, idx, **kw)
    lock_primary(pj["root"], pj["sample_id"])
    assert not os.path.exists(os.path.join(pj["root"], "relabel"))
    return pj


def _dry(projects, base, *extra):
    return day.main(["--projects-root", str(projects), "--out-root", str(base / "reports"), "--dry-run",
                     "--freeze", str(base / "pilot" / "freeze.json"), *extra])


def test_allow_no_relabel_dry_run_single_primary_and_only_skips(tmp_path, capsys, monkeypatch):
    """1차 하나만 잠긴 합성 시범: 플래그 없으면 전처럼 종료 3(no_relabel_plan), --allow-no-relabel --only 면 종료 0.
    고르지 않은 편은 'skipped (not selected)' 로 적고 열지 않는다. dry-run 이라 아무것도 쓰지 않는다."""
    base, projects = _data(tmp_path)
    a = _single_primary_pilot(projects, 1)
    b = build_pilot(projects, 2)                       # 1차가 잠기지 않음(고르지 않음)
    c = ready_pilot(projects, 3)                       # 다 됨(고르지 않음 — 풀리면 안 된다)
    na, nb, nc = (os.path.basename(p["root"]) for p in (a, b, c))
    before = tree(base)
    # 플래그 없음: 전과 같다 — 사유 no_relabel_plan, 종료 3
    assert _dry(projects, base, "--only", na) == 3
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if x.strip().startswith(na + ":"))
    assert "사유 no_relabel_plan" in line and day.NO_HUMAN_AGREEMENT not in out
    assert f"{nb}: 건너뜀" in out and day.SKIPPED_TEXT in out
    # 플래그 있음 + --only: 종료 0, 머리에 '사람 일치도 없음', 고르지 않은 두 편은 건너뜀
    seen = []
    real = builtins.open

    def spy(file, *aa, **kw):
        if not isinstance(file, int):
            seen.append(os.path.normcase(os.path.abspath(os.fspath(file))))
        return real(file, *aa, **kw)
    monkeypatch.setattr(builtins, "open", spy)
    code = _dry(projects, base, "--allow-no-relabel", "--only", na)
    monkeypatch.undo()
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.splitlines()[0].startswith(day.NO_HUMAN_AGREEMENT)
    line = next(x for x in out.splitlines() if x.strip().startswith(na + ":"))
    assert "충족 True" in line and "사유 -" in line and "플래그로 막지 않음 no_relabel_plan" in line
    for n in (nb, nc):
        assert f"  {n}: 건너뜀 — {day.SKIPPED_TEXT}" in out
    assert "시범 1편" in out and "dry-run" in out
    for skipped_root in (b["root"], c["root"]):                  # 고르지 않은 편은 한 번도 열지 않았다
        sk = os.path.normcase(skipped_root) + os.sep
        assert not [p for p in seen if p.startswith(sk)]
    assert tree(base) == before and not (base / "reports").exists()
    # run_day 로 보면: 요약에 고른 것·건너뛴 것·플래그가 숫자·이름만으로
    roots, skipped = day.select_pilots(day.find_pilots(str(projects)), [na.upper()])     # 이름은 대소문자 무시
    assert roots == [a["root"]] and skipped == [b["root"], c["root"]]
    code, s = day.run_day(roots, out_root=str(base / "reports"), expect=1, dry_run=True, freeze_path=None,
                          allow_no_relabel=True, skipped=skipped, only=True)
    assert code == 0 and s["conditions"]["met"] and s["allow_no_relabel"] is True and s["human_agreement"] == "none"
    assert s["selection"] == {"only": [na], "skipped": [nb, nc]}
    (p,) = s["conditions"]["projects"]
    assert p["name"] == na and p["reasons"] == [] and p["waived"] == [day.R_NO_PLAN] and p["locked"] == 1
    assert s["conditions"]["waived_kinds"] == [day.R_NO_PLAN] and s["watch"]["violations"] == 0
    assert s["watch"]["writes"] == 0 and s["projects"] == []
    # 플래그가 있어도 다른 사유는 그대로 막는다: --only 없이 3편이면 b 가 unlocked_sessions(no_relabel_plan 은 빠짐)
    code, s = day.run_day(day.find_pilots(str(projects)), out_root=str(base / "reports"), dry_run=True,
                          freeze_path=None, allow_no_relabel=True)
    per = {x["name"]: x for x in s["conditions"]["projects"]}
    assert code == 3 and per[na]["met"] and per[nc]["met"] and per[nc]["waived"] == []
    assert per[nb]["reasons"] == [day.R_UNLOCKED] and per[nb]["waived"] == [day.R_NO_PLAN] and not per[nb]["met"]
    # --only 이름이 없으면 아무것도 열기 전에 종료 2
    assert _dry(projects, base, "--allow-no-relabel", "--only", "pilot-99999999") == 2
    assert "--only" in capsys.readouterr().err
    assert tree(base) == before
    for blob in (out, json.dumps(s, ensure_ascii=False)):
        assert P_ID not in blob and RS_ID not in blob and SECRET_TEXT not in blob


def test_calib_single_primary_pilot_needs_allow_no_relabel(tmp_path):
    """보정의 '시범 세션이 모두 잠긴 뒤' 관문: 1차만 잠기고 계획이 없으면 플래그 없이는 전처럼 거부, 플래그(해제 당일 도구가
    --allow-no-relabel 일 때만 넘김)면 연다. 1차가 잠기지 않았으면 플래그가 있어도 거부."""
    base, projects = _data(tmp_path)
    pj = build_pilot(projects, 1, seal_results=False)
    for flag in (False, True):
        with pytest.raises(cal.CalibError) as ei:
            cal.calibrate(pj["root"], final_set="auto", allow_no_relabel=flag)
        assert ei.value.code == 1 and "잠기기 전" in str(ei.value)
    lock_primary(pj["root"])
    with pytest.raises(cal.CalibError) as ei:
        cal.calibrate(pj["root"], final_set="auto")
    assert ei.value.code == 1 and "잠기기 전" in str(ei.value)
    rep = cal.calibrate(pj["root"], final_set="auto", quick=True, allow_no_relabel=True)
    assert rep["input"]["pilot"] and rep["input"]["groups"]["reacted"] == 5
    assert rep["input"]["final_set"]["relabel_sessions"] == 0


def test_allow_no_relabel_go_single_pilot_compare_calib_summary(tmp_path, capsys):
    """--go --allow-no-relabel --only <1편>: 1차 하나의 최종 세트로 해제 → AI 비교 2판 → 판끼리 → 보정 → 요약. 일치도 보고서는
    건너뛴다(오류 아님). 고르지 않은 편(다 된 편 포함)은 풀지 않는다. 플래그 없이 --go 면 전처럼 종료 3 · 아무것도 풀지 않음."""
    base, projects = _data(tmp_path)
    a = _single_primary_pilot(projects, 1)
    c = ready_pilot(projects, 3)                       # 다 됐지만 고르지 않음 — 풀리면 안 된다
    na, nc = os.path.basename(a["root"]), os.path.basename(c["root"])
    out_root = base / "reports"
    # 플래그 없이 --go: 전과 같다(no_relabel_plan · 종료 3 · 요약 폴더도 없음)
    code, printed = _go(projects, out_root, capsys, "--only", na)
    assert code == 3, printed
    assert "사유 no_relabel_plan" in printed and "조건이 되지 않아 아무것도 풀지 않았습니다" in printed
    assert not out_root.exists() and seal.seal_status(a["root"]).unsealed_count == 0
    before_a, before_c = tree(a["root"]), tree(c["root"])
    c_ai = seal.project_ai_dir(c["root"], c["pid"])
    before_c_ai = tree(c_ai)
    code, printed = _go(projects, out_root, capsys, "--allow-no-relabel", "--only", na)
    assert code == 0, printed
    assert printed.splitlines()[1].startswith(day.NO_HUMAN_AGREEMENT)           # 0번 줄은 '되돌릴 수 없는 해제…'
    assert f"  {nc}: 건너뜀 — {day.SKIPPED_TEXT}" in printed
    line = next(x for x in printed.splitlines() if x.startswith(f"  {na}: 해제"))
    assert "해제 2(이미 0)" in line and "실패 0" in line and "대조 ok 2건" in line and "보고서 2" in line
    assert "판끼리 1" in line and "보정 됨" in line and f"일치도 보고서 건너뜀({day.AGREE_SKIPPED})" in line
    assert "오류 0" in line
    d = out_root / "pilot-unseal-20261102"
    names = sorted(os.listdir(d))
    assert names == sorted(["ai-compare", "summary.json", "summary.md", "calib-pooled.json", f"calib-{na}.json"])
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    md = (d / "summary.md").read_text(encoding="utf-8")
    assert s["allow_no_relabel"] is True and s["human_agreement"] == "none" and s["errors"] == []
    assert s["selection"] == {"only": [na], "skipped": [nc]}
    assert s["watch"]["violations"] == 0 and s["watch_run"]["violations"] == 0
    (p,) = s["projects"]
    assert p["name"] == na and p["errors"] == []
    u = p["unseal"]
    assert (u["sealed_valid"], u["unsealed_now"], u["failed"], u["presealed"], u["relabel_none"]) == (2, 2, [], 2, 2)
    assert (u["topics"], u["words"], u["vectors"]) == (2, 2, 2) and u["verify"]["ok"] and u["verify"]["runs"] == 2
    cm = p["compare"]
    assert cm["agreement"] == day.AGREE_SKIPPED and cm["relabel_sessions"] == 0
    assert cm["agreement_made"] == 0 and cm["agreement_errors"] == [] and cm["gate_report_id"] is None
    assert cm["human_gate"] is None
    assert sorted(r["label"] for r in cm["runs"]) == ["v0.2", "v0.3"]
    v3 = next(r for r in cm["runs"] if r["label"] == "v0.3")
    assert v3["pre_sealed"] is True and v3["e1_lenient"]["n"] >= 8
    assert v3["e1_lenient"]["exact"] == pytest.approx(1.0)                      # v0.3 은 1차와 같은 값
    (pair,) = cm["pairs"]
    assert (pair["a"]["label"], pair["b"]["label"]) == ("v0.2", "v0.3") and pair["human"]["b_matches"] == 2
    cb = p["calib"]
    assert cb["groups"]["reacted"] == 5 and cb["groups"]["labelled_none"] == 3 and cb["context_run"].startswith("run-v03-")
    assert len(os.listdir(d / "ai-compare" / a["pid"])) == 2                     # AI 비교 보고서 2판
    assert not (d / "agreement").exists()                                        # 일치도 보고서는 만들지 않음
    # 요약 머리·건너뜀·일치도 건너뜀
    assert md.splitlines()[2].startswith(f"**{day.NO_HUMAN_AGREEMENT}**")
    assert f"| {nc} | - | - | - | {day.SKIPPED_TEXT} | - |" in md and f"건너뜀 {day.SKIPPED_TEXT}: {nc}" in md
    assert f"| {na} | 1 | 1 | True | - | {day.R_NO_PLAN} |" in md
    assert f"재라벨 세션 0 이라 일치도 보고서를 건너뜀({day.AGREE_SKIPPED}, 오류 아님)" in md
    # 해제 기록: relabel = none · allow_no_relabel = true
    recs = jsonl.read(os.path.join(seal.project_ai_dir(a["root"], a["pid"]), "seal.jsonl")).records
    un = [r for r in recs if r.get("kind") == "ai.unsealed"]
    assert len(un) == 2 and all(r["relabel"] == "none" and r["allow_no_relabel"] is True and r["conditions_met"]
                                for r in un)
    # 고른 시범 폴더도, 고르지 않은 편(폴더·봉인 폴더)도 한 글자도 바뀌지 않았다
    assert tree(a["root"]) == before_a and tree(c["root"]) == before_c and tree(c_ai) == before_c_ai
    assert seal.seal_status(c["root"]).unsealed_count == 0 and seal.seal_status(c["root"]).sealed_count == 2
    blob = md + (d / "summary.json").read_text(encoding="utf-8") + printed
    for secret in (SECRET_TEXT, SECRET_NICK, SECRET_WORD, *TEXTS, "떡볶이", P_ID, RS_ID):
        assert secret not in blob, secret
