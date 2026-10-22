"""AI 결과 가져오기 검사(06 6-1·6-2)와 엔진 link 출력 변환(tools/engine_to_ai.py, 08 D-15 7).

합성 데이터만 쓴다 — chat_id·run_id·엔진 이름은 지어낸 표식이다. 실제 프로젝트·채팅·전사문은 쓰지 않는다.
"""
import copy
import json
import os

import pytest

from app import ai_results as air
from tests.test_engine_link import project  # noqa: F401  (합성 엔진 프로젝트 fixture)
from tools import engine_to_ai as e2a

VIDEO = "777"
FP = "ab" * 32
MSHA = "cd" * 32


def cid(i):
    return "c" + f"{i:020x}"


def project_data(**kw):
    d = {"broadcast": {"video_no": VIDEO}, "chat": {"messages_sha256": MSHA},
         "media": {"current": {"partial_fp": FP}},
         "calibration": {"active_rev": 1, "revs": [
             {"rev": 0, "offset_ms": 0, "status": "unverified", "method": "single_offset", "anchors": []},
             {"rev": 1, "offset_ms": 2000, "status": "verified", "method": "single_offset", "anchors": []}]}}
    d.update(kw)
    return d


def doc(**kw):
    d = {"schema": "chatcoach/ai-result", "schema_version": 1, "run_id": "run-synth-01",
         "created_at": "2026-10-22T10:00:00+09:00",
         "engine": {"name": "synth-engine", "version": "0.0.1", "preregistered": True,
                    "components": {"vad": None, "asr": "synth-asr", "ocr": None}},
         "target": {"platform": "chzzk", "video_no": VIDEO, "chat_id_algorithm": "sl-chat-id-v1",
                    "messages_sha256": MSHA},
         "time_axis": "vod_ms",
         "reactions": [
             {"chat_id": cid(1), "result": "graded", "grade": "direct", "reaction_time_ms": 5000, "score": 0.9},
             {"chat_id": cid(2), "result": "unconfirmed", "grade": None, "reaction_time_ms": None},
             {"chat_id": cid(3), "result": "graded", "grade": "related_only", "reaction_time_ms": 9000},
         ]}
    d.update(kw)
    return d


def b(o) -> bytes:
    return json.dumps(o, ensure_ascii=False).encode("utf-8")


# ============================================================================== 6-2 검사
def test_valid_doc_passes_without_warnings():
    r = air.check_bytes(b(doc()), project_data())
    assert r.ok and r.check == "valid" and r.rejects == [] and r.warnings == []
    assert r.unknown_chat_ids is None and r.duplicate_chat_ids == 0


def test_bom_is_accepted():
    assert air.check_bytes(b"\xef\xbb\xbf" + b(doc()), project_data()).ok


@pytest.mark.parametrize("data,code", [
    (b"{not json", air.R_NOT_JSON),
    (b"\xff\xfe{}", air.R_NOT_JSON),
    (b"[1, 2]", air.R_NOT_JSON),
    (b'{"schema": "chatcoach/ai-result", "schema": "x"}', air.R_NOT_JSON),
    (b'{"schema": "chatcoach/ai-result", "x": NaN}', air.R_NAN),
    (b'{"schema": "chatcoach/ai-result", "x": Infinity}', air.R_NAN),
])
def test_read_rejects(data, code):
    r = air.check_bytes(data, project_data())
    assert r.rejects == [code] and r.check == "rejected"


def test_too_large_rejected():
    with pytest.raises(air.AiResultError) as ei:
        air.parse_bytes(b(doc()), max_bytes=10)
    assert ei.value.code == air.R_TOO_LARGE


def test_schema_version_mismatch_rejected():
    assert air.check_bytes(b(doc(schema_version=2)), project_data()).rejects == [air.R_SCHEMA_ID]
    assert air.check_bytes(b(doc(schema="other")), project_data()).rejects == [air.R_SCHEMA_ID]
    assert air.check_bytes(b(doc(schema_version=True)), project_data()).rejects == [air.R_SCHEMA_ID]


def test_enum_outside_and_unknown_field_rejected():
    d = doc()
    d["reactions"][0]["grade"] = "high"                     # 4단계 밖
    r = air.check_doc(d, project_data())
    assert air.R_SCHEMA_INVALID in r.rejects and air.R_RESULT_GRADE in r.rejects
    d = doc()
    d["reactions"][0]["confidence"] = "certain"             # 1판에 없는 칸
    assert air.R_SCHEMA_INVALID in air.check_doc(d, project_data()).rejects
    # 문제 메시지에 값(문자열 본문)을 넣지 않는다
    d = doc(run_id="비밀표식 run")
    r = air.check_doc(d, project_data())
    assert air.R_SCHEMA_INVALID in r.rejects and not any("비밀표식" in p for p in r.problems)


def test_result_grade_combination_rejected():
    d = doc()
    d["reactions"][1]["grade"] = "likely"                   # unconfirmed + grade
    assert air.check_doc(d, project_data()).rejects == [air.R_RESULT_GRADE]
    d = doc()
    d["reactions"][0]["grade"] = None                       # graded + null
    assert air.check_doc(d, project_data()).rejects == [air.R_RESULT_GRADE]
    d = doc()
    del d["reactions"][0]["grade"]                          # graded + 칸 없음
    assert air.check_doc(d, project_data()).rejects == [air.R_RESULT_GRADE]


def test_video_no_and_project_checks():
    d = doc()
    d["target"]["video_no"] = "778"
    assert air.check_doc(d, project_data()).rejects == [air.R_VIDEO_NO]
    assert air.R_PROJECT in air.check_doc(doc(), None).rejects
    assert air.R_PROJECT in air.check_doc(doc(), {"broadcast": {}}).rejects


def test_created_at_must_be_datetime():
    assert air.R_CREATED_AT in air.check_doc(doc(created_at="어제"), project_data()).rejects
    assert air.R_CREATED_AT in air.check_doc(doc(created_at="2026-10-22"), project_data()).rejects
    assert air.check_doc(doc(created_at="2026-10-22T01:00:00Z"), project_data()).ok


def test_media_ms_requires_matching_fp():
    no_media = doc(time_axis="media_ms")
    assert air.R_SCHEMA_INVALID in air.check_doc(no_media, project_data()).rejects      # 스키마 if/then
    bad = doc(time_axis="media_ms", media={"partial_fp": "ee" * 32})
    assert air.check_doc(bad, project_data()).rejects == [air.R_MEDIA_FP]
    ok = doc(time_axis="media_ms", media={"partial_fp": FP})
    assert air.check_doc(ok, project_data()).ok
    nomedia_project = project_data(media={"current": None})
    assert air.check_doc(ok, nomedia_project).rejects == [air.R_MEDIA_FP]


def test_warnings_messages_sha_and_preregistered():
    d = doc()
    d["target"]["messages_sha256"] = "ee" * 32
    d["engine"]["preregistered"] = False
    r = air.check_doc(d, project_data())
    assert r.ok and r.warnings == [air.W_MESSAGES_DIFF, air.W_NOT_PREREG]
    d["target"]["messages_sha256"] = None
    assert air.W_MESSAGES_NONE in air.check_doc(d, project_data()).warnings
    assert air.W_MESSAGES_DIFF in air.check_doc(doc(), project_data(chat=None)).warnings


def test_unknown_and_duplicate_chat_ids_are_counted_not_rejected():
    d = doc()
    d["reactions"].append(dict(d["reactions"][0]))          # 중복
    d["reactions"].append({"chat_id": cid(99), "result": "unconfirmed", "grade": None})
    r = air.check_doc(d, project_data(), chat_ids={cid(1), cid(2), cid(3)})
    assert r.ok and r.duplicate_chat_ids == 1 and r.unknown_chat_ids == 1


# ============================================================================== 정규화
def test_normalize_media_ms_uses_active_calibration_and_drops():
    d = doc(time_axis="media_ms", media={"partial_fp": FP},
            segments=[{"start_ms": 2000, "end_ms": 12000, "category": "just_chatting"}])
    d["reactions"][0]["utterance_start_ms"] = 6000
    d["reactions"].append(dict(d["reactions"][0]))
    d["reactions"].append({"chat_id": cid(99), "result": "unconfirmed", "grade": None})
    out, info = air.normalize(d, project_data(), chat_ids={cid(1), cid(2), cid(3)})
    assert out["time_axis"] == "vod_ms" and info.time_axis_in == "media_ms" and info.calibration_rev == 1
    assert out["reactions"][0]["reaction_time_ms"] == 3000 and out["reactions"][0]["utterance_start_ms"] == 4000
    assert out["reactions"][1]["reaction_time_ms"] is None
    assert out["segments"][0] == {"start_ms": 0, "end_ms": 10000, "category": "just_chatting"}
    assert [r["chat_id"] for r in out["reactions"]] == [cid(1), cid(2), cid(3)]
    assert info.dropped_duplicate == {"reactions": 1} and info.dropped_unknown == {"reactions": 1}
    assert d["time_axis"] == "media_ms"                       # 입력은 그대로
    assert air.check_doc(out, project_data()).ok              # 정규화한 사본도 1판 스키마에 맞는다


def test_normalize_vod_ms_keeps_times():
    out, info = air.normalize(doc(), project_data())
    assert info.calibration_rev is None and out["reactions"][0]["reaction_time_ms"] == 5000


def test_project_chat_ids_reads_ids_only(tmp_path):
    root = tmp_path / "p"
    (root / "chat").mkdir(parents=True)
    (root / "chat" / "messages.jsonl").write_bytes(
        b"\xef\xbb\xbf" + b(({"chat_id": cid(1), "t_ms": 0, "text": "합성"})) + b"\n{broken\n"
        + b(({"chat_id": cid(2), "t_ms": 1})) + b"\n")
    assert air.project_chat_ids(str(root)) == {cid(1), cid(2)}
    assert air.project_chat_ids(str(tmp_path / "none")) == set()


def test_schema_file_still_parses_with_hand_checker():
    s = air.load_schema()
    assert s.check(doc()) == []


# ============================================================================== engine_to_ai (4단계만)
def engine_v2(**kw):
    """engine.link build_result 모양(필요한 칸만) — 합성."""
    rx = [
        {"chat_id": cid(1), "result": "graded", "grade": "direct", "reaction_time_ms": 5000,
         "utterance_start_ms": 5000, "utterance_end_ms": 6000, "score": 3.0, "chat_t_ms": 1000,
         "confidence": "certain", "candidates": [{}], "rationale": "합성근거", "eligible_grades": ["direct"]},
        {"chat_id": cid(2), "result": "unconfirmed", "grade": None, "reaction_time_ms": None,
         "utterance_start_ms": None, "utterance_end_ms": None, "score": None, "chat_t_ms": 2000, "candidates": []},
        {"chat_id": cid(3), "result": "graded", "grade": None, "reaction_time_ms": None,
         "utterance_start_ms": None, "utterance_end_ms": None, "score": 0.2, "chat_t_ms": 3000, "candidates": [{}]},
    ]
    d = {"schema": "chatcoach/ai-result", "schema_version": 2, "x_draft": "draft", "run_id": "link-v0-synth",
         "created_at": "2026-10-22T10:00:00+09:00",
         "engine": {"name": "chatcoach-link", "version": "0.1.0", "config_sha256": "12" * 32, "preregistered": False,
                    "components": {"vad": None, "asr": "synth", "ocr": None, "other": "features-v0"}},
         "target": {"platform": "chzzk", "video_no": VIDEO, "chat_id_algorithm": "sl-chat-id-v1",
                    "messages_sha256": MSHA},
         "time_axis": "vod_ms", "media": None, "calibration": {"offset_ms": 0}, "scope": {}, "inputs": {},
         "config": {}, "assumptions": {}, "stats": {}, "reactions": rx}
    d.update(kw)
    return d


def test_engine_to_ai_drops_graded_without_grade_by_default():
    out, info = e2a.to_ai_result(engine_v2())
    assert out["schema_version"] == 1 and "media" not in out and "stats" not in out
    assert [r["chat_id"] for r in out["reactions"]] == [cid(1), cid(2)]
    assert set(out["reactions"][0]) == set(e2a.REACTION_KEYS)
    assert info == {"reactions_in": 3, "reactions_out": 2, "graded_none": 1, "graded_none_policy": "drop"}
    r = air.check_doc(out, project_data())
    assert r.ok and r.warnings == [air.W_NOT_PREREG]
    assert "합성근거" not in e2a.dump(out).decode("utf-8")        # 근거 한 줄은 1판에 없다


def test_engine_to_ai_graded_none_as_unconfirmed():
    out, info = e2a.to_ai_result(engine_v2(), graded_none="unconfirmed")
    assert out["reactions"][2] == {"chat_id": cid(3), "result": "unconfirmed", "grade": None,
                                   "reaction_time_ms": None, "utterance_start_ms": None, "utterance_end_ms": None,
                                   "score": 0.2}
    assert air.check_doc(out, project_data()).ok and info["graded_none"] == 1


def test_engine_to_ai_four_levels_only():
    d = engine_v2()
    d["reactions"][0]["grade"] = "high"
    with pytest.raises(e2a.ConvertError):
        e2a.to_ai_result(d)
    with pytest.raises(e2a.ConvertError):
        e2a.to_ai_result(engine_v2(grade_scheme="binary"))
    assert e2a.to_ai_result(engine_v2(grade_scheme="4level"))[1]["reactions_out"] == 2
    with pytest.raises(e2a.ConvertError):
        e2a.to_ai_result(engine_v2(schema_version=3))
    with pytest.raises(e2a.ConvertError):
        e2a.to_ai_result({"schema": "other"})


def test_engine_to_ai_v1_input_passes_other_kinds():
    d = doc(segments=[{"start_ms": 0, "end_ms": 1000, "category": "game"}])
    out, _ = e2a.to_ai_result(d)
    assert out["segments"] == d["segments"] and air.check_doc(out, project_data()).ok


def test_engine_to_ai_cli_file_and_pilot_refusal(tmp_path, capsys):
    src = tmp_path / "link.json"
    src.write_bytes(b(engine_v2()))
    dst = tmp_path / "out" / "ai.json"
    assert e2a.main(["--in", str(src), "--out", str(dst)]) == 0
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["reactions_out"] == 2
    assert air.check_bytes(dst.read_bytes(), project_data()).ok
    # 시범 프로젝트는 파일 출력이 안 된다(엔진을 돌리기 전에 거부)
    pilot = tmp_path / "pilot-15600000"
    pilot.mkdir()
    assert e2a.main(["--from-project", str(pilot), "--out", str(tmp_path / "x.json")]) == 1
    assert "pilot_needs_seal" in capsys.readouterr().out and not (tmp_path / "x.json").exists()
    assert e2a.main(["--in", str(src), "--seal", "--project", str(tmp_path)]) == 2   # --by 없음
    bad = tmp_path / "bad.json"
    bad.write_bytes(b'{"schema": "chatcoach/ai-result", "x": NaN}')
    assert e2a.main(["--in", str(bad), "--out", str(dst)]) == 1


def test_engine_to_ai_v03_fallback_refuses_seal_and_reports_meta(project, tmp_path, capsys, monkeypatch):
    """--engine v0.3 가 v0.2 로 물러나면 봉인하지 않는다(종료 1). 출력에는 엔진 판·의미 비교 상태만 찍는다."""
    from types import SimpleNamespace
    from app import seal
    calls = []

    def fake_seal(*a, **k):
        calls.append(1)
        return SimpleNamespace(sha256="a" * 64, size=1, check="valid", reject_reasons=[], new=True, presealed=False)
    monkeypatch.setattr(seal, "seal_bytes", fake_seal)
    base = ["--from-project", project, "--engine", "v0.3", "--semantic-backend", "off"]
    assert e2a.main(base + ["--seal", "--by", "L01"]) == 1
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["error"] == "engine_fallback" and line["engine_version"] == "0.1.0"
    assert line["semantic"]["active"] is False and line["semantic"]["status"] == "disabled" and not calls
    assert e2a.main(base + ["--seal", "--by", "L01", "--allow-fallback"]) == 0
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert calls and line["engine_fallback"] is True and line["engine_version"] == "0.1.0"
    assert set(line) >= {"sealed", "size", "check", "engine_version", "semantic"} and "reactions_out" not in line
    # v0.2 를 고르면 물러남이 아니다
    assert e2a.main(["--from-project", project, "--engine", "v0.2", "--out", str(tmp_path / "o.json")]) == 0
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["engine_fallback"] is False and line["engine_version"] == "0.1.0"


def test_engine_to_ai_real_engine_output_shape(project):
    """engine.link 가 합성 프로젝트(tests/test_engine_link 의 장면)에서 메모리로 낸 결과를 변환하면 1판 검사를 통과한다."""
    import argparse
    ns = argparse.Namespace(no_identities=False, offset_ms=None, file_start_ms=None, start_ms=None, end_ms=None)
    eng = e2a._run_engine(project, ns)
    out, info = e2a.to_ai_result(eng)
    assert info["reactions_in"] == len(eng["reactions"])
    pd = {"broadcast": {"video_no": eng["target"]["video_no"]}, "chat": {"messages_sha256": MSHA}}
    r = air.check_doc(out, pd, chat_ids=air.project_chat_ids(project))
    assert r.ok, r.problems
    assert r.unknown_chat_ids == 0 and air.W_MESSAGES_DIFF in r.warnings
