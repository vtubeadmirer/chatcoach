"""AI 결과 봉인·해제 (06 7-1·7-2·7-3, 03 6-14, 08 D-15 7·8) — app/seal.py, tools/ai_seal.py.

합성 프로젝트(tmp_path)와 합성 엔진 결과만 쓴다. 실제 프로젝트·채팅·닉네임은 쓰지 않는다.
"""
import builtins
import hashlib
import inspect
import json
import os
from datetime import timedelta

import pytest

from app import ai_results as air
from app import relabel, seal
from tests.test_agreement import MID_1, NOW, SID_A, SID_B, _lock, cid, proj  # noqa: F401
from tools import ai_seal

SECRET_RUN = "run-synthmarker-77"
SECRET_ENGINE = "synthengine-marker"


def result_doc(run_id=SECRET_RUN, **kw):
    d = {"schema": "chatcoach/ai-result", "schema_version": 1, "run_id": run_id,
         "created_at": "2026-10-22T10:00:00+09:00",
         "engine": {"name": SECRET_ENGINE, "version": "0.0.1", "preregistered": True},
         "target": {"platform": "chzzk", "video_no": "777", "chat_id_algorithm": "sl-chat-id-v1"},
         "time_axis": "vod_ms",
         "reactions": [{"chat_id": cid(1), "result": "graded", "grade": "likely", "reaction_time_ms": 3000},
                       {"chat_id": cid(1), "result": "unconfirmed", "grade": None},
                       {"chat_id": cid(50), "result": "unconfirmed", "grade": None}]}
    d.update(kw)
    return d


def data_of(d) -> bytes:
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def ai_dir(proj):
    from app.project import open_project
    pid = open_project(proj["root"]).project_id
    return os.path.join(seal.default_ai_root(proj["root"]), pid)


def _make_plan(root, kind, labeler, rand4):
    from app.project import open_project
    data = open_project(root).data
    return relabel.create_plan(root, data, session_kind=kind, labeler_id=labeler, source_session_id=SID_A,
                               done_ranges=[(0, 600_000)], created_by="L01", created_machine4="aaaa",
                               app_version="0.0.1", creator_kind=None, now=NOW + timedelta(hours=1), rand4=rand4)


def _set_lock(root, sid, **fields):
    p = os.path.join(root, "locks", f"{sid}.json")
    with open(p, encoding="utf-8") as f:
        doc = json.load(f)
    doc.update(fields)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def _ready(root):
    """1차 잠금 + 두 번째 사람 계획 + 그 재라벨 잠금 → 06 7-1 조건이 된다."""
    _lock(root, SID_A, MID_1)
    p2 = _make_plan(root, "relabel_second", "L02", "aa01")
    _lock(root, SID_B, MID_1)
    _set_lock(root, SID_B, relabel_plan_id=p2["plan_id"])
    return p2


def _spy_open(monkeypatch, needle):
    opened = []
    real = builtins.open

    def spy(path, *a, **kw):
        try:
            p = os.path.normcase(os.path.abspath(os.fspath(path)))
        except TypeError:
            p = ""
        if needle in p:
            opened.append(p)
        return real(path, *a, **kw)
    monkeypatch.setattr(builtins, "open", spy)
    return opened


# ============================================================================== 봉인
def test_seal_writes_bin_and_record_with_hash_size_time_source_only(proj):
    data = data_of(result_doc())
    e = seal.seal_bytes(proj["root"], data, source="engine_link", by="L01", now=NOW)
    d = ai_dir(proj)
    sha = hashlib.sha256(data).hexdigest()
    assert e.new and e.sha256 == sha and e.size == len(data) and e.check == "valid" and e.presealed is True
    assert os.path.normcase(d).startswith(os.path.normcase(str(proj["tmp"] / "chatcoach-data" / "ai")))
    with open(os.path.join(d, "sealed", f"{sha}.bin"), "rb") as f:
        assert f.read() == data
    raw = open(os.path.join(d, "seal.jsonl"), encoding="utf-8").read()
    rec = json.loads(raw)
    assert set(rec) == {"v", "seq", "kind", "at", "by", "app_version", "sha256", "size", "source", "check",
                        "reject_reasons"}
    assert rec["kind"] == "ai.sealed" and rec["source"] == "engine_link" and rec["reject_reasons"] == []
    assert SECRET_RUN not in raw and SECRET_ENGINE not in raw          # 해석 결과를 남기지 않는다 (06 7-2)
    # 같은 바이트는 다시 기록하지 않는다
    e2 = seal.seal_bytes(proj["root"], data, source="file", by="L01", now=NOW)
    assert not e2.new and e2.source == "engine_link"
    assert len(open(os.path.join(d, "seal.jsonl"), encoding="utf-8").read().splitlines()) == 1
    # 프로젝트 폴더에는 아무것도 생기지 않는다
    assert not os.path.exists(os.path.join(proj["root"], "ai"))


def test_seal_rejected_file_is_sealed_with_codes_and_cannot_unseal(proj):
    d = result_doc()
    d["target"]["video_no"] = "778"
    e = seal.seal_bytes(proj["root"], data_of(d), source="file", by="L01", now=NOW)
    assert e.check == "rejected" and e.reject_reasons == [air.R_VIDEO_NO]
    _ready(proj["root"])
    with pytest.raises(seal.SealError, match="거부"):
        seal.unseal(proj["root"], e.sha256, by="L01", now=NOW)
    bad = seal.seal_bytes(proj["root"], b'{"x": NaN}', source="file", by="L01", now=NOW)
    assert bad.reject_reasons == [air.R_NAN]


def test_seal_refuses_bad_args_and_oversize(proj, monkeypatch):
    with pytest.raises(seal.SealError):
        seal.seal_bytes(proj["root"], b"{}", source="web", by="L01")
    with pytest.raises(seal.SealError):
        seal.seal_bytes(proj["root"], b"{}", source="file", by="nobody")
    monkeypatch.setattr(air, "MAX_BYTES", 10)
    with pytest.raises(seal.SealError, match="상한"):
        seal.seal_bytes(proj["root"], b"x" * 11, source="file", by="L01")
    assert not os.path.exists(ai_dir(proj))


def test_ai_root_inside_project_refused(proj):
    with pytest.raises(seal.SealError, match="프로젝트"):
        seal.seal_bytes(proj["root"], b"{}", source="file", by="L01", ai_root=os.path.join(proj["root"], "x"))


@pytest.mark.parametrize("kind", ["primary", "relabel_same", "relabel_second", "origin_review"])
def test_labeling_sessions_cannot_touch_ai_folder(proj, kind, monkeypatch):
    data = data_of(result_doc())
    seal.seal_bytes(proj["root"], data, source="file", by="L01", now=NOW)
    opened = _spy_open(monkeypatch, os.path.normcase(os.sep + "ai" + os.sep))
    for call in (lambda: seal.seal_bytes(proj["root"], data, source="file", by="L01", reader_kind=kind),
                 lambda: seal.seal_status(proj["root"], reader_kind=kind),
                 lambda: seal.unseal_conditions(proj["root"], reader_kind=kind),
                 lambda: seal.unseal(proj["root"], "0" * 64, by="L01", reader_kind=kind),
                 lambda: seal.read_run(proj["root"], SECRET_RUN, reader_kind=kind)):
        with pytest.raises(seal.SealBlindError):
            call()
    assert opened == []


def test_analysis_session_sees_count_and_unsealed_runs_only(proj, monkeypatch):
    """분석 세션(visibility UNSEALED_ONLY, E 통합): 건수 보기·해제된 결과 읽기만. 봉인·조건·해제는 막고 .bin 은 열지 않는다."""
    root = proj["root"]
    data = data_of(result_doc())
    e = seal.seal_bytes(root, data, source="file", by="L01", now=NOW)
    bins = _spy_open(monkeypatch, os.path.normcase(os.sep + "sealed" + os.sep))
    for call in (lambda: seal.seal_bytes(root, data, source="file", by="L01", reader_kind="analysis"),
                 lambda: seal.unseal_conditions(root, reader_kind="analysis"),
                 lambda: seal.unseal(root, e.sha256, by="L01", reader_kind="analysis")):
        with pytest.raises(seal.SealBlindError):
            call()
    st = seal.seal_status(root, reader_kind="analysis")
    assert st.sealed_count == 1 and st.unsealed_count == 0
    with pytest.raises(seal.SealError, match="해제 기록"):          # 해제 전: runs 도 열지 않는다
        seal.read_run(root, SECRET_RUN, reader_kind="analysis")
    assert bins == []
    _ready(root)
    seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4))
    run = seal.read_run(root, SECRET_RUN, reader_kind="analysis")
    assert run["run_id"] == SECRET_RUN
    assert seal.seal_status(root, reader_kind="analysis").unsealed_count == 1


def test_adjudication_may_seal_and_see_status(proj):
    e = seal.seal_bytes(proj["root"], data_of(result_doc()), source="file", by="L01", reader_kind="adjudication")
    assert seal.seal_status(proj["root"], reader_kind="adjudication").sealed_count == 1 and e.new


# ============================================================================== 상태·사전 봉인
def test_status_counts_and_presealed_against_first_primary_lock(proj, monkeypatch):
    root = proj["root"]
    early = seal.seal_bytes(root, data_of(result_doc("run-a")), source="file", by="L01", now=NOW)   # 09:00
    _lock(root, SID_A, MID_1)                                                                       # 10:00
    late = seal.seal_bytes(root, data_of(result_doc("run-b")), source="file", by="L01",
                           now=NOW + timedelta(hours=2))
    assert late.presealed is False
    opened = _spy_open(monkeypatch, os.path.normcase(os.sep + "sealed" + os.sep))
    st = seal.seal_status(root)
    assert opened == []                                                 # 봉인 파일을 열지 않는다
    assert st.sealed_count == 2 and st.unsealed_count == 0 and st.first_primary_lock_at == "2026-10-21T10:00:00+09:00"
    by = {e.sha256: e for e in st.entries}
    assert by[early.sha256].presealed is True and by[late.sha256].presealed is False
    assert all(e.run_id is None for e in st.entries)                    # 해제 전에는 run_id 도 모른다


# ============================================================================== 해제 조건 (06 7-1)
def test_unseal_blocked_until_planned_relabels_locked_and_bin_not_opened(proj, monkeypatch):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    opened = _spy_open(monkeypatch, os.path.normcase(os.sep + "sealed" + os.sep))
    c = seal.unseal_conditions(root)
    assert not c.met and any("잠기지 않은" in r for r in c.reasons)
    with pytest.raises(seal.SealBlockedError):
        seal.unseal(root, e.sha256, by="L01", now=NOW)
    _lock(root, SID_A, MID_1)
    c = seal.unseal_conditions(root)
    assert any("재라벨 계획이 없는" in r for r in c.reasons) and any(SID_B in r for r in c.reasons)
    p2 = _make_plan(root, "relabel_second", "L02", "aa01")
    _lock(root, SID_B, MID_1)
    ps = _make_plan(root, "relabel_same", "L01", "aa02")                 # 계획된 같은 사람 재라벨이 남음
    c = seal.unseal_conditions(root)
    assert not c.met and any(ps["plan_id"] in r for r in c.reasons)
    with pytest.raises(seal.SealBlockedError):
        seal.unseal(root, e.sha256, by="L01", now=NOW)
    assert opened == []
    relabel.cancel_plan(root, ps["plan_id"], reason="합성 시험 취소", cancelled_by="L01", cancelled_machine4="aaaa",
                        app_version="0.0.1", canceller_kind=None, now=NOW + timedelta(hours=2))
    _set_lock(root, SID_B, relabel_plan_id=p2["plan_id"])
    assert seal.unseal_conditions(root).met


def test_unseal_blocked_when_sample_verify_failed(proj):
    root = proj["root"]
    _ready(root)
    _set_lock(root, SID_A, sample_verify=[{"sample_set_id": "S-0000aaaa-d1-r1", "ok": False, "problems": 1}])
    c = seal.unseal_conditions(root)
    assert not c.met and any("재현 검증" in r for r in c.reasons)


# ============================================================================== 해제
def test_unseal_writes_run_and_record_with_lock_hashes(proj):
    root = proj["root"]
    data = data_of(result_doc())
    e = seal.seal_bytes(root, data, source="engine_link", by="L01", now=NOW)        # 1차 잠금 전 → 사전 봉인
    with pytest.raises(seal.SealError, match="해제 기록"):
        seal.read_run(root, SECRET_RUN)
    _ready(root)
    r = seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4))
    assert r.conditions_met and r.presealed and r.run_id == SECRET_RUN
    assert os.path.basename(r.run_path) == f"{SECRET_RUN}.json"
    rec = r.record
    assert rec["kind"] == "ai.unsealed" and rec["reason"] is None and rec["unmet"] == []
    want = {}
    for sid in (SID_A, SID_B):
        with open(os.path.join(root, "locks", f"{sid}.json"), "rb") as f:
            want[sid] = hashlib.sha256(f.read()).hexdigest()
    assert {x["session_id"]: x["sha256"] for x in rec["locks"]} == want
    assert rec["dropped_duplicate"] == {"reactions": 1} and rec["dropped_unknown"] == {"reactions": 1}
    assert rec["warnings"] == [air.W_MESSAGES_NONE] and rec["calibration_rev"] is None
    run = seal.read_run(root, SECRET_RUN)
    assert [x["chat_id"] for x in run["reactions"]] == [cid(1)]
    assert air.check_doc(run, {"broadcast": {"video_no": "777"}}).ok
    st = seal.seal_status(root)
    assert st.sealed_count == 0 and st.unsealed_count == 1 and st.entries[0].run_id == SECRET_RUN
    with pytest.raises(seal.AlreadyUnsealedError):
        seal.unseal(root, e.sha256, by="L01")
    # 해제한 결과가 바뀌면 읽지 않는다
    with open(r.run_path, "ab") as f:
        f.write(b" ")
    with pytest.raises(seal.SealError, match="기록과 다릅니다"):
        seal.read_run(root, SECRET_RUN)


def test_unseal_early_needs_reason_and_records_conditions_not_met(proj):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    with pytest.raises(seal.SealError):
        seal.unseal(root, e.sha256, by="L01", early_reason="   ")
    r = seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험 사유")
    assert r.conditions_met is False and r.record["reason"] == "합성 시험 사유" and r.record["unmet"]


def test_unseal_detects_tampered_bin_and_run_id_clash(proj):
    root = proj["root"]
    _ready(root)
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    binp = os.path.join(ai_dir(proj), "sealed", f"{e.sha256}.bin")
    with open(binp, "ab") as f:
        f.write(b" ")
    with pytest.raises(seal.SealError, match="기록과 다릅니다"):
        seal.unseal(root, e.sha256, by="L01")
    os.remove(binp)
    seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01")   # 원본을 다시 받으면 되살아난다
    seal.unseal(root, e.sha256, by="L01")
    other = seal.seal_bytes(root, data_of(result_doc(created_at="2026-10-23T10:00:00+09:00")), source="file", by="L01")
    with pytest.raises(seal.SealError, match="run_id"):
        seal.unseal(root, other.sha256, by="L01")
    with pytest.raises(seal.SealError, match="봉인 기록"):
        seal.unseal(root, "f" * 64, by="L01")


def test_only_unseal_opens_sealed_bin():
    """봉인 파일(.bin)을 읽는 코드는 unseal 하나다. 다른 공개 함수는 sealed\\ 를 쓰기(봉인)만 한다."""
    src = inspect.getsource(seal)
    assert src.count("builtins.open(binp") == 1
    assert "builtins.open(binp" in inspect.getsource(seal.unseal)
    for name in seal.__all__:
        obj = getattr(seal, name)
        if callable(obj) and inspect.isfunction(obj) and name != "unseal":
            assert "builtins.open(binp" not in inspect.getsource(obj)
    assert "SEALED_DIR" not in inspect.getsource(air)


# ============================================================================== 명령줄
def test_cli_seal_status_check_unseal(proj, tmp_path, capsys):
    root = proj["root"]
    f = tmp_path / "incoming.json"
    f.write_bytes(data_of(result_doc()))
    assert ai_seal.main(["seal", "--project", root, "--file", str(f), "--by", "L01", "--remove-source"]) == 0
    out = capsys.readouterr().out
    line = json.loads(out)
    assert line["check"] == "valid" and line["source_removed"] and not f.exists()
    assert SECRET_RUN not in out and SECRET_ENGINE not in out
    assert ai_seal.main(["status", "--project", root]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["sealed_count"] == 1 and st["entries"][0]["sha256_12"] == line["sealed"][:12]
    assert ai_seal.main(["check", "--project", root]) == 1
    assert json.loads(capsys.readouterr().out)["conditions_met"] is False
    assert ai_seal.main(["unseal", "--project", root, "--sha256", line["sealed"], "--by", "L01"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "SealBlockedError"
    _ready(root)
    assert ai_seal.main(["unseal", "--project", root, "--sha256", line["sealed"], "--by", "L01"]) == 0
    u = json.loads(capsys.readouterr().out)
    assert u["run_id"] == SECRET_RUN and u["conditions_met"] is True and u["locks"] == 2


# ============================================================================== 시각별 전사 낱말 짝 봉인 (22 5-2 의 7)
SECRET_WORD = "합성표지낱말"          # 합성 낱말 — 기록·출력·오류에 나오면 안 된다


def words_doc(project_id=None, video_no="777", words=("보스", SECRET_WORD), **kw):
    d = {"schema": "chatcoach/transcript-words", "schema_version": 1, "run_id": "words-synth-1",
         "created_at": "2026-10-23T10:00:00+09:00",
         "engine": {"name": "transcript_words", "version": "0.1.0", "config_sha256": None, "textnorm": "textnorm-v1"},
         "target": {"project_id": project_id, "video_no": video_no, "asr_sha256": "a" * 64},
         "time_axis": "vod_ms",
         "calibration": {"source": "project.json", "rev": 0, "method": "single_offset", "status": "verified",
                         "offset_ms": 0, "file_start_in_vod_ms": 0},
         "window_ms": 10_000, "range": {"start_ms": 0, "end_ms": 600_000},
         "windows": [{"start_ms": 100_000, "end_ms": 110_000, "words": sorted(words), "segments": 1}]}
    d.update(kw)
    return d


def _pid(root):
    from app.project import open_project
    return open_project(root).project_id


def test_seal_words_pairs_and_unseals_with_ai_result(proj, monkeypatch):
    root = proj["root"]
    wdata = data_of(words_doc(project_id=_pid(root)))
    with pytest.raises(seal.SealError, match="짝"):                 # 짝 판정 봉인이 없으면 받지 않는다
        seal.seal_words_bytes(root, wdata, pair_sha256="0" * 64, by="L01")
    e = seal.seal_bytes(root, data_of(result_doc()), source="engine_link", by="L01", now=NOW)
    we = seal.seal_words_bytes(root, wdata, pair_sha256=e.sha256, by="L01", source="engine_words", now=NOW)
    assert we.new and we.check == "valid" and we.reject_reasons == [] and we.sha256 == hashlib.sha256(wdata).hexdigest()
    assert we.run_id is None and we.unsealed_at is None
    assert seal.seal_words_bytes(root, wdata, pair_sha256=e.sha256, by="L01").new is False   # 같은 바이트는 한 번
    st = seal.seal_status(root)
    assert st.sealed_count == 1 and st.entries[0].words_sha256 == we.sha256 and st.entries[0].topics_sha256 is None
    d = ai_dir(proj)
    raw = open(os.path.join(d, "seal.jsonl"), encoding="utf-8").read()
    recs = [json.loads(x) for x in raw.splitlines()]
    assert [r["kind"] for r in recs] == ["ai.sealed", "words.sealed"]
    assert set(recs[1]) == {"v", "seq", "kind", "at", "by", "app_version", "sha256", "size", "source", "pair",
                            "check", "reject_reasons"}
    assert recs[1]["pair"] == e.sha256 and recs[1]["source"] == "engine_words"
    assert SECRET_WORD not in raw and "보스" not in raw                     # 낱말을 기록에 남기지 않는다
    # 해제 전에는 읽지 않는다, 라벨링 세션은 받지도 읽지도 못한다
    with pytest.raises(seal.SealError, match="해제 기록"):
        seal.read_words(root, SECRET_RUN, reader_kind="analysis")
    for kind in ("primary", "relabel_second", "origin_review"):
        with pytest.raises(seal.SealBlindError):
            seal.seal_words_bytes(root, wdata, pair_sha256=e.sha256, by="L01", reader_kind=kind)
        with pytest.raises(seal.SealBlindError):
            seal.read_words(root, SECRET_RUN, reader_kind=kind)
    # 판정을 풀면 같이 풀린다
    _ready(root)
    res = seal.unseal(root, e.sha256, by="L01", now=NOW + timedelta(days=4))
    assert res.record["words"]["sha256"] == we.sha256 and res.record["words_error"] is None
    assert "topics" not in res.record                                      # 주제 흐름 짝이 없으면 그 칸은 없다
    doc = seal.read_words(root, res.run_id, reader_kind="analysis")
    assert doc == json.loads(wdata)
    assert os.path.isfile(os.path.join(d, seal.WORDS_DIR, f"{res.run_id}.json"))
    # 풀린 결과에는 나중에 붙이지 않는다
    with pytest.raises(seal.SealError, match="이미 해제"):
        seal.seal_words_bytes(root, data_of(words_doc(project_id=_pid(root), words=("공략",))),
                              pair_sha256=e.sha256, by="L01")
    # 해제한 파일이 바뀌면 읽지 않는다
    with open(os.path.join(d, seal.WORDS_DIR, f"{res.run_id}.json"), "ab") as f:
        f.write(b" ")
    with pytest.raises(seal.SealError, match="기록과 다릅니다"):
        seal.read_words(root, res.run_id)
    assert not os.path.exists(os.path.join(root, "ai"))                    # 프로젝트 폴더에는 아무것도 없다


def test_seal_words_rejects_with_codes_only(proj):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    bad_fmt = seal.seal_words_bytes(root, data_of(words_doc(window_ms=0)), pair_sha256=e.sha256, by="L01")
    assert bad_fmt.check == "rejected" and bad_fmt.reject_reasons == ["words_format"]
    bad_json = seal.seal_words_bytes(root, b'{"x": NaN}', pair_sha256=e.sha256, by="L01")
    assert bad_json.reject_reasons == ["words_json"]
    other_video = seal.seal_words_bytes(root, data_of(words_doc(video_no="999")), pair_sha256=e.sha256, by="L01")
    assert other_video.reject_reasons == ["words_video_mismatch"]
    other_proj = seal.seal_words_bytes(root, data_of(words_doc(project_id="0" * 8 + "-0000-0000-0000-" + "0" * 12)),
                                       pair_sha256=e.sha256, by="L01")
    assert other_proj.reject_reasons == ["words_project_mismatch"]
    for x in (bad_fmt, other_video, other_proj):
        assert SECRET_WORD not in json.dumps(x.reject_reasons, ensure_ascii=False)
    assert seal.seal_status(root).entries[0].words_sha256 is None           # 거부된 것은 짝으로 치지 않는다
    with pytest.raises(seal.SealError, match="출처"):
        seal.seal_words_bytes(root, b"{}", pair_sha256=e.sha256, by="L01", source="engine_topics")
    with pytest.raises(seal.SealError, match="sha256"):
        seal.seal_words_bytes(root, b"{}", pair_sha256="xyz", by="L01")
    _ready(root)
    res = seal.unseal(root, e.sha256, by="L01")
    assert "words" not in res.record and seal.read_words(root, res.run_id) is None


def test_seal_words_file_and_oversize(proj, tmp_path, monkeypatch):
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    f = tmp_path / "w.json"
    f.write_bytes(data_of(words_doc()))
    we = seal.seal_words_file(root, str(f), pair_sha256=e.sha256, by="L01")
    assert we.source == "file" and we.check == "valid" and f.exists()    # 원본을 지우는 것은 부르는 쪽 몫
    monkeypatch.setattr(seal, "WORDS_MAX_BYTES", 10)
    with pytest.raises(seal.SealError, match="상한"):
        seal.seal_words_bytes(root, b"x" * 11, pair_sha256=e.sha256, by="L01")


def test_seal_words_unseal_problem_does_not_block_ai_result(proj):
    """짝 전사 낱말 .bin 이 깨졌어도 판정 해제는 된다 — 코드만 남기고 낱말은 풀지 않는다."""
    root = proj["root"]
    e = seal.seal_bytes(root, data_of(result_doc()), source="file", by="L01", now=NOW)
    we = seal.seal_words_bytes(root, data_of(words_doc()), pair_sha256=e.sha256, by="L01")
    with open(os.path.join(ai_dir(proj), "sealed", f"{we.sha256}.bin"), "ab") as f:
        f.write(b" ")
    _ready(root)
    res = seal.unseal(root, e.sha256, by="L01")
    assert res.record["words"] is None and res.record["words_error"] == "words_bin_mismatch"
    assert seal.read_words(root, res.run_id) is None
    assert not os.path.exists(os.path.join(ai_dir(proj), seal.WORDS_DIR, f"{res.run_id}.json"))


def test_engine_to_ai_finds_words_sealer():
    """tools/engine_to_ai --words --seal 이 찾는 이름(WORDS_SEAL_FN)이 이 함수다 — 엔진 쪽은 고칠 것이 없다."""
    from tools import engine_to_ai as e2a
    assert e2a._words_sealer() is seal.seal_words_bytes
    sig = inspect.signature(seal.seal_words_bytes)
    assert list(sig.parameters)[:2] == ["project_root", "data"]
    for k in ("pair_sha256", "by", "source", "ai_root"):
        assert sig.parameters[k].kind is inspect.Parameter.KEYWORD_ONLY
