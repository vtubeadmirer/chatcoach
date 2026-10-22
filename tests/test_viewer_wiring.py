"""G 둘째 통합: 시청자 훈련 화면까지 이어지는 선택 입력 — app/viewer_flow.py → app/ui/viewer_train_view.py →
app/core/viewer_score.py (22 7-1 의 2·3·4, 22 5-2 의 15).

- 창별 주제 벡터(<P>\\derived\\asr-topic-vectors.json): 전사 낱말을 쓸 때만, asr 해시·보정이 같을 때만 넘긴다.
- 동의어(<데이터 폴더>\\config\\synonyms-ko.txt): 있으면 넘긴다.
- 미리 만든 주제 흐름(<P>\\derived\\asr-topic-flow.json): 해제된 run 이 없을 때만(시범 아님).
- 문턱(기준서 values.viewer_score)·전사 베끼기(repeat.talk_copy)가 화면 채점에서 실제로 쓰인다.

합성 데이터만 쓴다(합성 최종 세트 프로젝트, 합성 asr.json, 합성 벡터). 예문·낱말·벡터 모두 합성이다.
사람 클릭 수(D-16): 이 연결로 늘어난 클릭·입력 칸 0(파일이 있으면 저절로 읽는다).
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from app import viewer_flow as vf  # noqa: E402
from app.core import viewer_score as vsc  # noqa: E402
from tests.test_final_set import _clear_locks, cid, proj  # noqa: E402,F401
from tests.test_integration_c4 import PARTS, ctx, qapp  # noqa: E402,F401
from tests.test_training_menu import P2, _open, _slow_poll, win  # noqa: E402,F401
from tests.test_viewer_menu import _build_words_inprocess  # noqa: E402

N_WIN = 30                       # 10초 창 30개(0~300초) — vec_min_windows(12)·talk_common_min_windows(30) 이상
RARE_AT = 15                     # 합성 낱말 '용암맵' 은 창 15(150~160초)에만


def _write_asr(root):
    """합성 asr.json: 창마다 서로 다른 채움 낱말, 창 15 에만 '용암맵'. 합성 프로젝트의 보정은 오프셋 0(VOD 시각과 같다)."""
    segs = []
    for k in range(N_WIN):
        text = f"채움{k:02d}말" + (" 용암맵" if k == RARE_AT else "")
        segs.append({"start_ms": k * 10_000 + 1_000, "end_ms": k * 10_000 + 8_000, "text": text})
    os.makedirs(os.path.join(root, "derived"), exist_ok=True)
    doc = {"format": "chatcoach.asr", "format_version": 1, "model": {"name": "synthetic"}, "settings": {},
           "audio": {"duration_ms": N_WIN * 10_000}, "segments": segs}
    with open(vf.asr_path(root), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def _topic_vec(dim=8):
    v = [0.05] * dim
    v[0] = 1.0                   # 모든 창이 같은 합성 주제(A) — 같은 주제 줄기가 끊기지 않는다
    return v


def _write_vectors(root, words_doc, **over):
    """viewer_score.vectors_from_doc 이 읽는 꼴의 합성 벡터 문서(round4). target·calibration 은 전사 낱말 문서와 같게."""
    doc = {"schema": vsc.TOPIC_VECTORS_SCHEMA, "schema_version": vsc.TOPIC_VECTORS_SCHEMA_VERSION,
           "time_axis": "vod_ms", "encoding": "round4", "dim": 8,
           "target": dict(words_doc["target"]), "calibration": dict(words_doc["calibration"]),
           "windows": [{"start_ms": k * 10_000, "end_ms": (k + 1) * 10_000, "v": _topic_vec()} for k in range(N_WIN)]}
    doc.update(over)
    with open(vf.topic_vectors_path(root), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc


def _prepared(proj):  # noqa: F811
    from app import project as pm
    root = proj["root"]
    _write_asr(root)
    _build_words_inprocess(["--project", root], None)
    pdata = pm.open_project(root).data
    code, wdoc = vf.words_status(root, pdata)
    assert code == vf.W_OK
    return root, pdata, wdoc


def _score(ctx_, text, t_ms):
    sc = vsc.ScoreContext.build(ctx_["topic_blocks"], ctx_["transcript"], (), settings=ctx_["settings"],
                                topic_vectors=ctx_.get("topic_vectors"), synonyms=ctx_.get("synonyms"))
    return sc, vsc.score_chat(text, t_ms, sc, settings=ctx_["settings"])


# ----------------------------------------------------------------------------- 창별 벡터
def test_build_context_passes_matching_vectors_and_scores_change(proj):  # noqa: F811
    root, pdata, wdoc = _prepared(proj)
    ctx0, info0 = vf.build_context(root, pdata)
    assert info0["vectors"] == vf.F_MISSING and ctx0["topic_vectors"] is None
    _sc, r0 = _score(ctx0, "용암맵 어려워요", 260_000)       # 되돌아보기(170~260초)에는 '용암맵' 이 없다
    assert (r0.topic.level, r0.topic.code) == ("weak", vsc.TOPIC_NO_OVERLAP)

    _write_vectors(root, wdoc)
    ctx1, info1 = vf.build_context(root, pdata)
    assert info1["vectors"] == vf.F_OK and info1["vector_windows"] == N_WIN and len(ctx1["topic_vectors"]) == N_WIN
    sc, r1 = _score(ctx1, "용암맵 어려워요", 260_000)
    assert sc.vec is not None
    assert (r1.topic.level, r1.topic.code) == ("ok", vsc.TOPIC_RUN_HIT) and r1.topic.num("run") == 1
    # info·repr 에 낱말·벡터 값이 없다
    assert "용암맵" not in json.dumps(info1, ensure_ascii=False) and "용암맵" not in repr(sc)


def test_vectors_not_used_when_stale_bad_or_no_words(proj):  # noqa: F811
    root, pdata, wdoc = _prepared(proj)
    _write_vectors(root, wdoc, target={**wdoc["target"], "asr_sha256": "0" * 64})
    assert vf.build_context(root, pdata)[1]["vectors"] == vf.F_STALE
    _write_vectors(root, wdoc, calibration={**wdoc["calibration"], "offset_ms": 1234})
    assert vf.build_context(root, pdata)[1]["vectors"] == vf.F_STALE
    _write_vectors(root, wdoc, encoding="zz")
    assert vf.build_context(root, pdata)[1]["vectors"] == vf.F_BAD
    with open(vf.topic_vectors_path(root), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert vf.build_context(root, pdata)[1]["vectors"] == vf.F_BAD
    # 전사 낱말을 쓰지 않는 실행(보정 확인 전)에는 벡터 파일을 열지 않는다
    _write_vectors(root, wdoc)
    import copy
    unv = copy.deepcopy(pdata)
    for r in unv["calibration"]["revs"]:
        r["status"] = "unverified"
    ctx_, info = vf.build_context(root, unv)
    assert info["vectors"] == vf.F_NO_WORDS and ctx_["topic_vectors"] is None
    assert vf.read_topic_vectors(root, None) == (vf.F_NO_WORDS, None)


# ----------------------------------------------------------------------------- 동의어
def test_synonyms_file_is_read_from_data_folder(proj, tmp_path):  # noqa: F811
    root, pdata, _wdoc = _prepared(proj)
    assert vf.synonyms_path(root) == os.path.join(str(tmp_path), "config", "synonyms-ko.txt")
    ctx0, info0 = vf.build_context(root, pdata)
    assert info0["synonyms"] == vf.F_MISSING and ctx0["synonyms"] is None
    _sc, r0 = _score(ctx0, "불지옥맵 어려워요", 160_000)
    assert r0.topic.level == "weak"
    os.makedirs(os.path.dirname(vf.synonyms_path(root)), exist_ok=True)
    with open(vf.synonyms_path(root), "w", encoding="utf-8") as f:
        f.write("# 합성 동의어\n용암맵, 불지옥맵\n")
    ctx1, info1 = vf.build_context(root, pdata)
    assert info1["synonyms"] == vf.F_OK and isinstance(ctx1["synonyms"], str)
    sc, r1 = _score(ctx1, "불지옥맵 어려워요", 160_000)
    assert r1.topic.level in ("ok", "good") and r1.topic.num("syn") == 1
    assert "불지옥맵" not in repr(sc)
    with open(vf.synonyms_path(root), "wb") as f:
        f.write(b"\xff\xfe\x00bad")
    assert vf.build_context(root, pdata)[1]["synonyms"] == vf.F_BAD


# ----------------------------------------------------------------------------- 미리 만든 주제 흐름 (22 5-2 의 15)
def test_derived_topic_flow_used_only_without_unsealed_run(proj):  # noqa: F811
    from engine import topics as ET
    root, pdata, wdoc = _prepared(proj)
    ctx0, info0 = vf.build_context(root, pdata)
    assert info0["topic_source"] == "none" and info0["topic_derived"] == vf.F_MISSING and ctx0["topic_blocks"] == []
    assert ET.main(["--project", root, "--out", vf.derived_topics_path(root), "--window-sec", "30",
                    "--min-block-sec", "60"]) == 0
    ctx1, info1 = vf.build_context(root, pdata)
    assert info1["topic_source"] == "derived" and info1["topic_derived"] == vf.F_OK
    assert info1["topic_blocks"] == len(ctx1["topic_blocks"]) > 0
    assert all(isinstance(b[2], tuple) for b in ctx1["topic_blocks"])
    # 다른 asr 로 만든 것이면 쓰지 않는다
    p = vf.derived_topics_path(root)
    d = json.load(open(p, encoding="utf-8"))
    d["target"]["asr_sha256"] = "1" * 64
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    ctx2, info2 = vf.build_context(root, pdata)
    assert info2["topic_derived"] == vf.F_STALE and ctx2["topic_blocks"] == [] and info2["topic_source"] == "none"
    # 해제된 run 이 있으면 미리 만든 파일은 보지 않는다
    import app.viewer_flow as mod
    orig = mod.topic_blocks
    try:
        mod.topic_blocks = lambda r, **k: ([(0, 10_000, ("합성주제",))], "run-syn")
        ctx3, info3 = vf.build_context(root, pdata)
    finally:
        mod.topic_blocks = orig
    assert info3["topic_source"] == "run" and info3["topic_derived"] == vf.F_UNUSED and len(ctx3["topic_blocks"]) == 1


def test_pilot_reads_no_derived_inputs(tmp_path):
    pilot = str(tmp_path / "projects" / "pilot-합성")
    os.makedirs(os.path.join(pilot, "derived"))
    with open(os.path.join(pilot, "derived", "asr-topic-vectors.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(pilot, "derived", "asr-topic-flow.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    assert vf.read_topic_vectors(pilot, {"target": {"asr_sha256": "a" * 64}}) == (vf.F_PILOT, None)
    assert vf.derived_topic_blocks(pilot) == (vf.F_PILOT, [])
    ctx_, info = vf.build_context(pilot)
    # 시범 전사 낱말은 봉인 기록을 못 읽어 W_PILOT → 벡터는 전사 낱말이 없어 열어 보지도 않는다(F_NO_WORDS, 시범 길 23 5-2 의 3)
    assert info["words"] == vf.W_PILOT and info["vectors"] == vf.F_NO_WORDS
    assert info["topic_derived"] == vf.F_PILOT and ctx_["topic_vectors"] is None


# ----------------------------------------------------------------------------- 화면까지: 벡터·문턱·전사 베끼기
def test_viewer_view_scores_with_vectors_settings_and_talk_copy(win, proj):  # noqa: F811
    """메인 창 메뉴로 연 화면의 채점기가 벡터(run_hit)·기준서 문턱·전사 베끼기(talk_copy)를 실제로 쓴다."""
    from tests.test_viewer_menu import _with_viewer_score
    root, _pdata, wdoc = _prepared(proj)
    _write_vectors(root, wdoc)
    win.words_runner = _build_words_inprocess
    wb = _open(win, root, None)
    wb.project.data = _with_viewer_score(wb.project.data, {"copy_window_sec": 60})
    view = win.viewer_training()
    assert view is not None
    try:
        assert view._context["settings"].copy_window_ms == 60_000 and len(view._context["topic_vectors"]) == N_WIN
        res = view._score_builtin("용암맵 어려워요", {"vod_ms": 260_000, "my_previous": []})
        assert view._vsc_ctx is not None and view._vsc_ctx.vec is not None
        assert res is not None
        # 전사 베끼기: 창 15·16 의 낱말만 옮긴 서술(60초 안) → repeat.talk_copy
        res2 = view._score_builtin("채움15말 용암맵 채움16말", {"vod_ms": 175_000, "my_previous": []})
        raw = vsc.score_chat("채움15말 용암맵 채움16말", 175_000, view._vsc_ctx, settings=view._context["settings"])
        assert raw.repeat.code == vsc.REPEAT_TALK_COPY and res2 is not None
        top = vsc.score_chat("용암맵 어려워요", 260_000, view._vsc_ctx, settings=view._context["settings"])
        assert top.topic.code == vsc.TOPIC_RUN_HIT
    finally:
        win.close_viewer_training()
        win.show_home()
