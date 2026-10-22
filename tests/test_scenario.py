"""가상 채팅 시나리오 H-1 — 형식·검사·섞기·원자적 쓰기 (20 3장·5장, 08 D-05·D-19 2).

모든 자료는 합성이다: 프로젝트 ID·chat_id·본문·이모티콘 코드({:zz_test_01:})·가림 이름 모두 만든 값.
실제 프로젝트 폴더(chatcoach-data\\projects\\pilot-* 등)는 열지 않는다. 쓰기 시험은 pytest 임시 폴더에서만.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil

import pytest

from app import jsonl
from app import scenario_store as ss
from app.core import scenario as sc

PID = "11111111-2222-4333-8444-555555555555"      # 합성 프로젝트
OTHER = "66666666-7777-4888-9999-aaaaaaaaaaaa"     # 같은 채널의 다른 합성 프로젝트
SHA_M = "a" * 64
SHA_O = "b" * 64
RANGE = (1_200_000, 2_400_000)


def cid(n: int) -> str:
    return "c" + hashlib.sha256(f"synthetic-{n}".encode()).hexdigest()[:20]


def real_msg(n: int, t_ms, *, kind="text", name=None, text=None) -> dict:
    return {"chat_id": cid(n), "t_ms": t_ms, "server_time_ms": None, "type_code": 1, "kind": kind,
            "status": "NORMAL", "viewer_id": "vw_aaaaaaaaaaaa", "is_anonymous": False,
            "nickname_masked": name or "가**", "text": text or f"합성 채팅 {n}", "raw_ref": {"fetch_id": "F20261022-abcd", "page": 0, "index": n}}


def header(**over) -> dict:
    h = {"project_id": PID, "range": {"vod_from_ms": RANGE[0], "vod_to_ms": RANGE[1]},
         "created_at": "2026-10-22T10:00:00.000+09:00", "created_by": "engine scenario 0.1.0",
         "inputs": {"messages_sha256": SHA_M, "from_projects": [{"project_id": OTHER, "messages_sha256": SHA_O}]},
         "fill": {"quiet_window_ms": 60000, "quiet_max_real": 2, "target_per_min": 6, "max_virtual_ratio": 0.5},
         "retrieval": {"method": "topic_words", "min_score": 0.2, "max_per_viewer": 3, "seed": 7},
         "generator": {"backend": "none"}, "filters": {"versions": ["nickname-v1", "block-v1"], "dropped": {"nickname": 1}},
         "external": {"enabled": False}}
    h.update(over)
    return h


def retrieved(n: int, show: int, *, from_pid=PID, from_t=3_901_000, scope="same_broadcast", mask="하**", rel=False):
    d = {"source": "retrieved", "chat_kind": "text", "from_project_id": from_pid, "from_chat_id": cid(n),
         "from_t_ms": from_t, "from_scope": scope, "retrieval_score": 0.41, "display_mask": mask,
         "checks": {"nickname_hit": 0, "copy_max": 0.0, "blocked": 0, "llm_flag": 0}}
    d["show_rel_ms" if rel else "show_vod_ms"] = show - RANGE[0] if rel else show
    return d


def generated(g: int, show: int, *, text="오늘 점심은 뭐 드셨어요 {:zz_test_01:}", emotes=("{:zz_test_01:}",), mask="두**"):
    return {"source": "generated", "chat_kind": "text", "show_vod_ms": show, "gen_id": f"g{g:04d}", "text": text,
            "context": {"asr_from_ms": show - 120000, "asr_to_ms": show, "topic_id": "t03"}, "display_mask": mask,
            "emotes": list(emotes), "checks": {"nickname_hit": 0, "copy_max": 0.12, "blocked": 0, "llm_flag": 0}}


def sample_records() -> list[dict]:
    chats = [retrieved(900, 1_263_000),
             generated(1, 1_271_500),
             retrieved(901, 1_250_000, from_pid=OTHER, from_t=100_000, scope="same_channel", mask="시**", rel=True)]
    return sc.assemble(header(), chats, yyyymmdd="20261022")


def make_project(tmp_path, name="synthetic-proj", pid=PID) -> str:
    root = tmp_path / "projects" / name
    root.mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project_id": pid}), encoding="utf-8")
    return str(root)


@pytest.fixture
def pilot_dir(tmp_path):
    d = tmp_path / "pilot"
    d.mkdir()
    return str(d)


# ---------------------------------------------------------------- 형식

def test_assemble_valid_and_deterministic():
    recs = sample_records()
    assert ss.validate(recs, project_id=PID) == []
    assert recs[0]["kind"] == sc.K_HEADER and recs[-1]["kind"] == sc.K_END
    assert [c["line_id"] for c in recs[1:-1]] == ["s0001", "s0002", "s0003"]
    # 시각 순 정렬: rel 1_250_000 → 1_263_000 → 1_271_500
    assert [c["source"] for c in recs[1:-1]] == ["retrieved", "retrieved", "generated"]
    assert recs[0]["counts"] == {"replay": 0, "retrieved": 2, "generated": 1}
    assert recs[0]["scenario_id"] == f"SC-20261022-{recs[-1]['lines_sha256'][:4]}"
    again = sample_records()
    assert b"".join(map(sc.canonical_line, recs)) == b"".join(map(sc.canonical_line, again))


def test_canonical_line_matches_jsonl():
    for r in sample_records():
        assert sc.canonical_line(r) == jsonl.dumps_line(r)


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r[1].update(nickname="시청자9"), "모르는 필드"),                     # 닉네임 칸 없음
    (lambda r: r[1].update(nickname_masked="시**"), "모르는 필드"),                # 원 가림 닉네임 칸 없음
    (lambda r: r[1].update(viewer_id="vw_aaaaaaaaaaaa"), "모르는 필드"),
    (lambda r: r[1].update(text="본문"), "모르는 필드"),                           # retrieved 는 본문을 적지 않는다
    (lambda r: r[1].update(chat_kind="donation"), "값은"),                          # 후원 등 다른 kind 금지
    (lambda r: r[1].update(badge="manager"), "모르는 필드"),
    (lambda r: r[1].update(display_mask="진짜닉네임"), "형식"),                     # 가명 모양만
    (lambda r: r[1].update(source="ai"), "허용 값"),
    (lambda r: r.__setitem__(1, {**r[1], "kind": "scenario.other"}), "허용 값"),
    (lambda r: r[0].update(external={"enabled": True}), "값은"),
    (lambda r: r[0]["generator"].update(backend="external"), "허용 값"),
    (lambda r: r[3].update(text="줄\n바꿈"), "형식"),
    (lambda r: r[3].update(text="가" * 101), "이하"),
])
def test_schema_rejects(mutate, needle):
    recs = sample_records()
    mutate(recs)
    probs = ss.validate(recs)
    assert probs and any(needle in p for p in probs), probs


def test_show_time_exactly_one():
    recs = sample_records()
    assert "show_rel_ms" in recs[1] and "show_vod_ms" in recs[2]
    recs[2]["show_rel_ms"] = 5
    assert any("동시에" in p for p in ss.validate(recs))
    recs = sample_records()
    del recs[2]["show_vod_ms"]
    assert ss.validate(recs)


def _resign(recs):
    """채팅 줄을 고친 뒤 해시·ID·건수를 다시 맞춘다(줄 사이 규칙만 시험하려고)."""
    chats = recs[1:-1]
    sha = sc.lines_sha256(chats)
    recs[-1].update(lines=len(chats), lines_sha256=sha)
    recs[0]["scenario_id"] = f"SC-20261022-{sha[:4]}"
    recs[0]["counts"] = {s: sum(1 for c in chats if c["source"] == s) for s in sc.SOURCES}
    return recs


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r.pop(), "scenario.end"),                                                     # 끝 줄 없음
    (lambda r: r.pop(0), "scenario.header"),
    (lambda r: r[-1].update(lines=9), "end.lines"),
    (lambda r: r[-1].update(lines_sha256="0" * 64), "lines_sha256"),
    (lambda r: r[0].update(counts={"replay": 0, "retrieved": 3, "generated": 0}), "counts"),
    (lambda r: r[0].update(project_id=OTHER, inputs={"messages_sha256": SHA_M}), "project_id"),
])
def test_core_rejects_structure(mutate, needle):
    recs = sample_records()
    mutate(recs)
    probs = ss.validate(recs, project_id=PID)
    assert probs and any(needle in p for p in probs), probs


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r[1].update(show_rel_ms=RANGE[1] - RANGE[0] + 10), "구간 밖"),
    (lambda r: r[2].update(from_t_ms=RANGE[0] + 5), "구간 안"),                            # 같은 방송 구간 안
    (lambda r: r[2].update(from_scope="same_channel"), "same_broadcast"),
    (lambda r: r[1].update(from_project_id="99999999-7777-4888-9999-aaaaaaaaaaaa"), "from_projects"),
    (lambda r: r[3].update(text="안녕 {:zz_test_99:}"), "emotes"),
    (lambda r: r[3]["checks"].update(nickname_hit=1), "nickname_hit"),
    (lambda r: r[2].update(line_id="s0009"), "차례"),
    (lambda r: r[3].update(show_vod_ms=RANGE[0]), "시각 순"),
])
def test_core_rejects_rules(mutate, needle):
    recs = sample_records()
    mutate(recs)
    _resign(recs)
    probs = ss.validate(recs, project_id=PID)
    assert probs and any(needle in p for p in probs), probs


def test_problem_messages_have_no_body():
    recs = sample_records()
    recs[3]["text"] = "비밀본문XYZ {:zz_test_99:}"
    _resign(recs)
    probs = ss.validate(recs)
    assert probs and not any("비밀본문XYZ" in p for p in probs)


# ---------------------------------------------------------------- 섞기

def test_merge_keeps_real_untouched_and_marks_virtual():
    recs = sample_records()
    reals = [real_msg(1, 1_240_000, name="가**"), real_msg(2, 1_263_000, name="하**"),
             real_msg(3, 1_100_000), real_msg(4, None), real_msg(5, 2_500_000)]
    before = copy.deepcopy(reals)
    recs_before = copy.deepcopy(recs)
    origin = {(PID, cid(900)): real_msg(900, 3_901_000, name="원**", text="옮긴 합성 본문"),
              (OTHER, cid(901)): real_msg(901, 100_000, name="원**", text="다른 방송 합성 본문")}
    res = sc.merge_for_display(reals, recs, resolve=lambda p, c: origin.get((p, c)))
    assert reals == before and recs == recs_before
    keys = [(r.vod_ms, r.source) for r in res.rows]
    assert keys == [(1_240_000, "replay"), (1_250_000, "retrieved"), (1_263_000, "replay"),
                    (1_263_000, "retrieved"), (1_271_500, "generated")]
    assert res.counts["real"] == 2 and res.counts["real_no_time"] == 1 and res.counts["real_out_of_range"] == 2
    real_rows = [r for r in res.rows if not r.virtual]
    assert all(r.badge == "" and r.message is m for r, m in zip(real_rows, [reals[0], reals[1]]))
    v = [r for r in res.rows if r.virtual]
    assert {r.badge for r in v if r.source == "retrieved"} == {"가져옴"}
    assert [r.badge for r in v if r.source == "generated"] == ["생성"]
    # retrieved 의 '하**' 는 화면의 실제 채팅 이름과 겹쳐 가명 '시청자N' 으로 바뀐다. 원 가림 이름은 안 쓴다.
    by_line = {r.line_id: r for r in v}
    assert by_line["s0002"].display_name.startswith("시청자")
    assert by_line["s0001"].display_name == next(c["display_mask"] for c in recs if c.get("line_id") == "s0001")
    assert all(r.display_name != "원**" for r in v)
    assert by_line["s0001"].text == "다른 방송 합성 본문"
    assert all(r.key == f"{recs[0]['scenario_id']}:{r.line_id}" for r in v)
    assert res.counts["renamed"] == 1


def test_merge_skips_missing_or_changed_origin():
    recs = sample_records()
    origin = {(PID, cid(900)): real_msg(900, 3_901_001),                 # 시각이 달라짐 → 건너뜀
              (OTHER, cid(901)): real_msg(901, 100_000, kind="donation")}  # 일반 채팅 아님 → 건너뜀
    res = sc.merge_for_display([], recs, resolve=lambda p, c: origin.get((p, c)))
    assert [r.source for r in res.rows] == ["generated"]
    assert res.counts["skipped_mismatch"] == 1 and res.counts["skipped_kind"] == 1
    res2 = sc.merge_for_display([], recs)       # resolve 없음 → retrieved 는 모두 '없음'
    assert res2.counts["skipped_missing"] == 2


def test_merge_hide_badges_keeps_source():
    res = sc.merge_for_display([], sample_records(), hide_badges=True)
    assert res.rows and all(r.badge == "" and r.virtual and r.source == "generated" for r in res.rows)


def test_merge_without_scenario_is_real_only():
    reals = [real_msg(1, 5), real_msg(2, 3)]
    res = sc.merge_for_display(reals, None)
    assert [r.chat_id for r in res.rows] == [cid(2), cid(1)] and not any(r.virtual for r in res.rows)


def test_merge_replay_lines_limit_real():
    chats = [{"source": "replay", "chat_kind": "text", "show_vod_ms": 1_300_000, "chat_id": cid(2)}]
    recs = sc.assemble(header(), chats, yyyymmdd="20261022")
    assert ss.validate(recs, project_id=PID) == []
    res = sc.merge_for_display([real_msg(1, 1_250_000), real_msg(2, 1_300_000)], recs)
    assert [r.chat_id for r in res.rows] == [cid(2)]


# ---------------------------------------------------------------- 저장소

def test_write_read_roundtrip_bytes(tmp_path, pilot_dir):
    root = make_project(tmp_path)
    try:
        recs = sample_records()
        w = ss.write_scenario(root, recs, pilot_dir=pilot_dir)
        assert w.created and os.path.basename(w.path) == f"{w.scenario_id}.jsonl"
        assert os.path.dirname(w.path) == os.path.join(root, "scenarios")
        raw = open(w.path, "rb").read()
        assert raw == b"".join(map(sc.canonical_line, recs))
        assert hashlib.sha256(raw).hexdigest() == w.sha256
        s = ss.load_scenario(root, w.scenario_id, pilot_dir=pilot_dir)
        assert s.records == recs and s.canonical and s.sha256 == w.sha256
        assert b"".join(map(sc.canonical_line, s.records)) == raw
        assert not [n for n in os.listdir(os.path.dirname(w.path)) if n.endswith(".tmp")]
        # 같은 내용이면 새로 쓰지 않는다
        w2 = ss.write_scenario(root, sample_records(), pilot_dir=pilot_dir)
        assert not w2.created and w2.scenario_id == w.scenario_id
        assert ss.list_scenarios(root, pilot_dir=pilot_dir) == [w.scenario_id]
        # scenarios\ 밖에는 아무것도 생기지 않았다 (messages.jsonl 등과 섞지 않음)
        assert sorted(os.listdir(root)) == ["project.json", "scenarios"]
    finally:
        shutil.rmtree(root)


def test_write_rejects_invalid_and_other_project(tmp_path, pilot_dir):
    root = make_project(tmp_path, pid=OTHER)
    try:
        with pytest.raises(ss.ScenarioInvalidError):
            ss.write_scenario(root, sample_records(), pilot_dir=pilot_dir)   # header.project_id 가 다름
        assert not os.path.exists(os.path.join(root, "scenarios")) or not os.listdir(os.path.join(root, "scenarios"))
    finally:
        shutil.rmtree(root)


def test_read_rejects_broken_files(tmp_path, pilot_dir):
    root = make_project(tmp_path)
    try:
        w = ss.write_scenario(root, sample_records(), pilot_dir=pilot_dir)
        good = open(w.path, "rb").read()
        lines = good.split(b"\n")[:-1]
        cases = {
            ss.R_TRUNCATED: b"\n".join(lines[:-1]) + b"\n",                  # 끝 줄 없음
            ss.R_BOM: b"\xef\xbb\xbf" + good,
            ss.R_CORRUPT: lines[0] + b"\n{broken\n" + b"\n".join(lines[1:]) + b"\n",
        }
        for code, data in cases.items():
            p = os.path.join(root, "scenarios", f"{w.scenario_id}.jsonl")
            with open(p, "wb") as f:
                f.write(data)
            with pytest.raises(ss.ScenarioInvalidError) as e:
                ss.read_scenario(p)
            assert e.value.code == code, (code, e.value.code)
        # 잘린 마지막 줄
        with open(w.path, "wb") as f:
            f.write(good[:-10])
        with pytest.raises(ss.ScenarioInvalidError) as e:
            ss.read_scenario(w.path)
        assert e.value.code == ss.R_TRUNCATED
        # 파일 이름과 ID 가 다름
        other = os.path.join(root, "scenarios", "SC-20261022-0000.jsonl")
        with open(other, "wb") as f:
            f.write(good)
        with pytest.raises(ss.ScenarioInvalidError) as e:
            ss.read_scenario(other)
        assert e.value.code == ss.R_NAME
        best, counts = ss.latest_scenario(root, pilot_dir=pilot_dir)
        assert best is None and counts["broken"] == 2
    finally:
        shutil.rmtree(root)


def test_latest_scenario_picks_newest_same_range(tmp_path, pilot_dir):
    root = make_project(tmp_path)
    try:
        a = ss.write_scenario(root, sample_records(), pilot_dir=pilot_dir)
        chats = [generated(1, 1_300_000, text="합성 둘째", emotes=())]
        recs_b = sc.assemble(header(created_at="2026-10-22T11:00:00.000+09:00"), chats, yyyymmdd="20261022")
        b = ss.write_scenario(root, recs_b, pilot_dir=pilot_dir)
        recs_c = sc.assemble(header(created_at="2026-10-22T12:00:00.000+09:00",
                                    range={"vod_from_ms": 0, "vod_to_ms": 600_000}),
                             [generated(1, 200_000, text="합성 셋째", emotes=())], yyyymmdd="20261022")
        ss.write_scenario(root, recs_c, pilot_dir=pilot_dir)
        best, counts = ss.latest_scenario(root, RANGE, pilot_dir=pilot_dir)
        assert best.scenario_id == b.scenario_id != a.scenario_id
        assert counts == {"files": 3, "ok": 2, "broken": 0, "other_range": 1, "blocked": 0}
    finally:
        shutil.rmtree(root)


def test_pilot_project_blocked_before_reading(tmp_path, pilot_dir, monkeypatch):
    root = make_project(tmp_path, name="pilot-synthetic")
    try:
        opened = []
        real_listdir = os.listdir

        def spy(p="."):
            if str(p).endswith("scenarios"):
                opened.append(p)
            return real_listdir(p)
        monkeypatch.setattr(ss.os, "listdir", spy)
        with pytest.raises(ss.ScenarioBlockedError):
            ss.write_scenario(root, sample_records(), pilot_dir=pilot_dir)
        with pytest.raises(ss.ScenarioBlockedError):
            ss.load_scenario(root, "SC-20261022-abcd", pilot_dir=pilot_dir)
        assert ss.list_scenarios(root, pilot_dir=pilot_dir) == []
        best, counts = ss.latest_scenario(root, pilot_dir=pilot_dir)
        assert best is None and counts["blocked"] == 1
        assert opened == [] and not os.path.exists(os.path.join(root, "scenarios"))
    finally:
        shutil.rmtree(root)


def test_no_overwrite_of_different_content_same_name(tmp_path, pilot_dir):
    root = make_project(tmp_path)
    try:
        recs = sample_records()
        d = os.path.join(root, "scenarios")
        os.makedirs(d)
        p = os.path.join(d, f"{recs[0]['scenario_id']}.jsonl")
        with open(p, "wb") as f:
            f.write(b"placeholder\n")
        with pytest.raises(ss.ScenarioStoreError):
            ss.write_scenario(root, recs, pilot_dir=pilot_dir)
        assert open(p, "rb").read() == b"placeholder\n"
    finally:
        shutil.rmtree(root)


def test_module_has_no_network_or_export():
    import inspect
    for mod in (sc, ss):
        src = inspect.getsource(mod)
        for bad in ("socket", "urllib", "requests", "http.client", "QClipboard", "clipboard"):
            assert bad not in src, (mod.__name__, bad)
