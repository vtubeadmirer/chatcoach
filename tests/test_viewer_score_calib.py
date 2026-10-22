"""시청자 훈련 채점 문턱(THRESHOLDS · guide-config values.viewer_score)과 보정 도구(tools/viewer_score_calib.py) 시험.

22 7-1 의 2 '설정 바꾸면 결과가 바뀌는 시험. 범위 밖이면 기본값', 22 5-2 의 3(flow_head2)·4(habit_soft_ok_only) 켜고 끄기,
22 7-1 의 1 보정 자료 도구가 숫자만 내는지.
예문·채팅·닉네임은 모두 **합성**이다(실제 채팅·전사문이 아니다). 이모티콘 코드도 합성(`{:zz_test_NN:}`).
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os

import pytest

from app.core import validate as V
from app.core import viewer_score as vs
from tools import viewer_score_calib as cal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(ROOT, "schemas", "guide-config.schema.json")
CONFIG = os.path.join(ROOT, "assets", "guide-config.v0.1.json")
ANNOTATION_KEYS = {"$schema", "$id", "title", "description", "format"}

T = 600_000


def _schema() -> dict:
    with open(SCHEMA, encoding="utf-8") as f:
        return json.load(f)


def _strip(node):
    if isinstance(node, list):
        return [_strip(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k in ANNOTATION_KEYS:
            continue
        if k in ("properties", "$defs"):
            out[k] = {name: _strip(sub) for name, sub in v.items()}
        elif k in ("items", "additionalProperties", "oneOf"):
            out[k] = _strip(v)
        else:
            out[k] = v
    return out


def _check(obj) -> list[str]:
    spec = _strip(_schema())
    return V.check(obj, spec, spec)


def _config() -> dict:
    with open(CONFIG, "rb") as f:
        return V.load_json_strict(f.read(), 1024 * 1024)


def _guide_default(th: vs.Threshold):
    if th.is_bool or th.default is None:
        return th.default
    return th.default // th.scale if th.default % th.scale == 0 else th.default / th.scale


# ───────────────────────────────────────────────────────────── THRESHOLDS ↔ 설정 ↔ 스키마

def test_thresholds_cover_every_setting_field_with_same_default():
    fields = {f.name: f.default for f in dataclasses.fields(vs.ViewerScoreSettings)}
    attrs = {th.attr: th for th in vs.THRESHOLDS.values()}
    assert set(fields) == set(attrs), "ViewerScoreSettings 칸과 THRESHOLDS 가 한 짝이어야 한다"
    for name, default in fields.items():
        assert default == attrs[name].default and type(default) is type(attrs[name].default), name
    for key, th in vs.THRESHOLDS.items():
        if th.is_bool:
            assert (th.lo, th.hi, th.scale) == (False, True, 1), key
        else:
            assert th.default is not None, key                         # 숫자는 이 표 한 곳에(통합: 벡터 칸도)
            assert th.lo <= th.default / th.scale <= th.hi, key        # 기본값이 자기 범위 안
            assert key.endswith("_sec") == (th.scale == 1000), key


def test_old_constant_names_follow_thresholds():
    assert vs.DEFAULT_TRANSCRIPT_LOOKBACK_MS == vs.THRESHOLDS["transcript_lookback_sec"].default == 90_000
    assert vs.TRANSCRIPT_LOOKBACK_RANGE_MS == (60_000, 120_000)
    assert vs.COPY_WINDOW_RANGE_MS == (30_000, 60_000)
    assert vs.DEFAULT_COPY_WEAK_PCT == 80 and vs.DEFAULT_HABIT_WEAK_MIN == 3
    assert vs.DEFAULT_FLOW_HEAD2 is False and vs.DEFAULT_HABIT_SOFT_OK_ONLY is False
    # 전사 베끼기: 옛 상수 이름도 THRESHOLDS 에서 나온다(값이 두 곳에서 갈라지지 않게)
    for key, const in (("talk_copy_ok_words", "DEFAULT_TALK_COPY_OK_WORDS"),
                       ("talk_copy_weak_words", "DEFAULT_TALK_COPY_WEAK_WORDS")):
        assert vs.THRESHOLDS[key].default == getattr(vs, const), key
    assert (vs.DEFAULT_TALK_COPY_OK_WORDS, vs.DEFAULT_TALK_COPY_WEAK_WORDS) == (2, 3)


VEC_CONSTS = (("vec_run_max_sec", "DEFAULT_VEC_RUN_MAX_MS"), ("vec_boundary_pct", "DEFAULT_VEC_BOUNDARY_PCT"),
              ("vec_related_pct", "DEFAULT_VEC_RELATED_PCT"), ("vec_related_max", "DEFAULT_VEC_RELATED_MAX"),
              ("vec_min_windows", "DEFAULT_VEC_MIN_WINDOWS"), ("vec_max_gap_sec", "DEFAULT_VEC_MAX_GAP_MS"))


def test_vector_thresholds_come_from_thresholds_and_guide_config_overrides():
    """벡터 문턱도 THRESHOLDS 한 곳(통합에서 None→숫자). 옛 DEFAULT_VEC_* 이름은 그 표에서 나온다."""
    want = {"vec_run_max_sec": 300_000, "vec_boundary_pct": 20, "vec_related_pct": 50, "vec_related_max": 6,
            "vec_min_windows": 12, "vec_max_gap_sec": 20_000}
    for key, const in VEC_CONSTS:
        th = vs.THRESHOLDS[key]
        assert th.default == want[key] == getattr(vs, const) == getattr(vs.DEFAULT_SETTINGS, th.attr), key
    s, notes = vs.settings_from_values({"viewer_score": {"vec_related_pct": 70, "vec_run_max_sec": 120,
                                                         "vec_max_gap_sec": 15.5}})
    assert notes == [] and (s.vec_related_pct, s.vec_run_max_ms, s.vec_max_gap_ms) == (70, 120_000, 15_500)
    assert vs._vs(s, "vec_related_pct", 1) == 70
    assert vs._vs(object(), "vec_related_pct", 50) == 50          # 설정이 아닌 객체면 기본값


def test_schema_viewer_score_matches_thresholds():
    s = _schema()
    vsec = s["$defs"]["values"]["properties"]["viewer_score"]
    assert vsec["$ref"] == "#/$defs/viewerScore"
    assert "viewer_score" not in s["$defs"]["values"]["required"]          # 선택 항목(D-14 7, D-21 4)
    spec = s["$defs"]["viewerScore"]
    assert spec["additionalProperties"] is False and "required" not in spec
    props = spec["properties"]
    assert set(props) == set(vs.THRESHOLDS)
    for key, th in vs.THRESHOLDS.items():
        p = props[key]
        if th.is_bool:
            assert p["type"] == "boolean", key
            assert f"기본 {'true' if th.default else 'false'}" in p["description"], key
            continue
        assert p["type"] == ("number" if th.scale == 1000 else "integer"), key
        assert (p["minimum"], p["maximum"]) == (th.lo, th.hi), key
        assert f"기본 {_guide_default(th)}." in p["description"], key


def test_v0_1_file_unchanged_and_still_valid_and_full_example_valid():
    cfg = _config()
    assert "viewer_score" not in cfg["values"]                 # 시범 중 고정 파일은 그대로(D-14 7)
    assert _check(cfg) == []
    full = copy.deepcopy(cfg)
    full["values"]["viewer_score"] = {k: _guide_default(th) for k, th in vs.THRESHOLDS.items()
                                      if th.default is not None}
    assert _check(full) == []
    s, notes = vs.settings_from_values(full["values"])
    assert notes == [] and s == vs.DEFAULT_SETTINGS              # 기본값을 그대로 적으면 기본 설정
    bad = copy.deepcopy(full)
    bad["values"]["viewer_score"]["copy_window_sec"] = 10
    bad["values"]["viewer_score"]["flow_head2"] = 1
    bad["values"]["viewer_score"]["zz_unknown"] = 3
    probs = _check(bad)
    assert len(probs) == 3


# ───────────────────────────────────────────────────────────── settings_from_values

def test_settings_from_values_reads_nested_and_flat():
    s1, n1 = vs.settings_from_values({"viewer_score": {"topic_good_talk_hits": 1, "copy_window_sec": 60,
                                                       "flow_head2": True}})
    s2, n2 = vs.settings_from_values({"topic_good_talk_hits": 1, "copy_window_sec": 60, "flow_head2": True})
    assert n1 == n2 == [] and s1 == s2
    assert (s1.topic_good_talk_hits, s1.copy_window_ms, s1.flow_head2) == (1, 60_000, True)


@pytest.mark.parametrize("vals", [None, {}, {"viewer_score": {}}, {"segment": {"min_length_sec": 30}}, "x", 3])
def test_settings_missing_gives_defaults(vals):
    assert vs.settings_from_values(vals) == (vs.DEFAULT_SETTINGS, [])


@pytest.mark.parametrize("key,val", [("transcript_lookback_sec", 59), ("transcript_lookback_sec", 121),
                                     ("vec_run_max_sec", -1), ("talk_copy_weak_words", 11), ("ngram", 1),
                                     ("flow_head2", 1), ("habit_soft_ok_only", "true"), ("copy_ok_pct", None),
                                     ("self_lookback", float("nan")), ("expand_min_chars", True)])
def test_settings_out_of_range_or_wrong_type_falls_back(key, val):
    s, notes = vs.settings_from_values({"viewer_score": {key: val}})
    assert s == vs.DEFAULT_SETTINGS
    assert notes == [notes[0]] and notes[0].startswith(f"viewer_score.{key}:")
    assert repr(val) not in notes[0] or isinstance(val, bool)   # 메모에 값은 넣지 않는다(칸 이름만)


def test_settings_inverted_talk_copy_pair_reset():
    s, notes = vs.settings_from_values({"talk_copy_ok_words": 5, "talk_copy_weak_words": 3})
    assert (s.talk_copy_ok_words, s.talk_copy_weak_words) == (2, 3)
    assert len(notes) == 1 and "talk_copy_ok_words" in notes[0]


# ───────────────────────────────────────────────────────────── 설정을 바꾸면 결과가 바뀐다 (합성)

def _ctx(topic=(), talk=(), chats=()):
    return vs.ScoreContext.build(topic_blocks=topic, transcript=talk, chats=chats)


def test_changing_threshold_changes_result():
    c = _ctx(talk=[(T - 30_000, T - 25_000, ["오늘은 매운 치즈 라면을 끓여 볼게요"])])
    text = "라면 국물 진하네요"                                      # 전사와 1개 겹침
    base = vs.score_chat(text, T, c)
    assert (base.topic.level, base.topic.code) == (vs.OK, vs.TOPIC_TALK_ONE)
    s, _ = vs.settings_from_values({"viewer_score": {"topic_good_talk_hits": 1}})
    r = vs.score_chat(text, T, c, settings=s)
    assert (r.topic.level, r.topic.code) == (vs.GOOD, vs.TOPIC_TALK_HITS)
    s3, _ = vs.settings_from_values({"viewer_score": {"expand_concrete_words": 5}})
    plain = "국물 진하고 면발 좋네요"                                  # 내용 낱말 4개, 주제 겹침 없음
    assert (vs.score_chat(plain, T).expand.level, vs.score_chat(plain, T).expand.code) == (vs.OK, vs.EXPAND_CONCRETE)
    r3 = vs.score_chat(plain, T, settings=s3).expand
    assert (r3.level, r3.code) == (vs.WEAK, vs.EXPAND_THIN)
    s4, _ = vs.settings_from_values({"viewer_score": {"transcript_lookback_sec": 60}})
    c_far = _ctx(talk=[(T - 80_000, T - 75_000, ["매운 치즈 라면"])])
    assert vs.score_chat(text, T, c_far).topic.code == vs.TOPIC_TALK_ONE        # 기본 90초 안
    assert vs.score_chat(text, T, c_far, settings=s4).topic.code == vs.TOPIC_NO_CONTEXT   # 60초 밖 → 맥락 없음


def test_flow_head2_toggle_only_for_flow_words():
    flow = _ctx(topic=[(T - 200_000, T + 10_000, ("보스전", "공략"))])
    on, _ = vs.settings_from_values({"viewer_score": {"flow_head2": True}})
    off = vs.score_chat("보스 잡았어요?", T, flow)
    assert off.topic.code == vs.TOPIC_NO_OVERLAP
    r = vs.score_chat("보스 잡았어요?", T, flow, settings=on)
    assert (r.topic.level, r.topic.code) == (vs.GOOD, vs.TOPIC_FLOW_HIT)
    # 전사 풀에는 2자 머리 겹침을 쓰지 않는다
    talk = _ctx(talk=[(T - 20_000, T - 15_000, ["보스전 시작합니다 공략 보고 왔어요"])])
    assert vs.score_chat("보스 잡았어요?", T, talk, settings=on).topic.code == vs.TOPIC_NO_OVERLAP


@pytest.mark.parametrize("history,text", [
    (["떡볶이 맛있다 ㅋㅋ", "어묵 좋다 ㅋㅋ", "순대 최고 ㅋㅋ"], "튀김 바삭 ㅋㅋ"),                         # 끝 웃음 자모
    (["좋아요 {:zz_test_01:}", "멋져요 {:zz_test_01:}", "대단해요 {:zz_test_01:}"], "신기해요 {:zz_test_01:}"),  # 끝 이모티콘
    (["오늘 노래 좋다", "오늘 날씨 덥다", "오늘 반찬 맛있다"], "오늘 의상 예쁘다"),                         # 흔한 첫 낱말
])
def test_habit_soft_ok_only_toggle(history, text):
    off = vs.score_chat(text, T, None, history)
    assert (off.repeat.level, off.repeat.code) == (vs.WEAK, vs.REPEAT_HABIT)
    on, _ = vs.settings_from_values({"viewer_score": {"habit_soft_ok_only": True}})
    r = vs.score_chat(text, T, None, history, settings=on)
    assert (r.repeat.level, r.repeat.code) == (vs.OK, vs.REPEAT_HABIT)


def test_habit_soft_keeps_filler_word_weak_and_off_is_unchanged():
    history = ["진짜 노래 좋다", "진짜 춤 잘춘다", "진짜 목소리 좋다"]
    on, _ = vs.settings_from_values({"viewer_score": {"habit_soft_ok_only": True}})
    for s in (vs.DEFAULT_SETTINGS, on):          # 말버릇 후보 낱말('진짜')의 되풀이는 켜도 weak
        r = vs.score_chat("진짜 의상 예쁘다", T, None, history, settings=s)
        assert (r.repeat.level, r.repeat.code) == (vs.WEAK, vs.REPEAT_HABIT)
    # 기호 끝 '!' 는 켜든 끄든 ok 까지만(0.2 와 같음)
    h = ["노래 좋다!", "춤 멋지다!", "의상 예쁘다!"]
    for s in (vs.DEFAULT_SETTINGS, on):
        assert vs.score_chat("목소리 좋다!", T, None, h, settings=s).repeat.level == vs.OK


# ───────────────────────────────────────────────────────────── 보정 도구 (합성)

def _msg(cid, t, viewer, text, kind="text", status="NORMAL"):
    return json.dumps({"chat_id": cid, "t_ms": t, "server_time_ms": t, "type_code": 1, "kind": kind,
                       "status": status, "viewer_id": viewer, "is_anonymous": False,
                       "nickname_masked": "시**", "role": "common_user", "text": text, "raw_ref": None},
                      ensure_ascii=False)


SYN_TEXTS = {
    "c01": ("v1", 100_000, "떡볶이 치즈 추가하면 어떤 맛이에요?"),
    "c02": ("v2", 101_000, "ㅋㅋㅋㅋ"),
    "c03": ("v3", 102_000, "ㅋㅋㅋㅋ"),
    "c04": ("v4", 130_000, "어묵 국물 매운가요?"),
    "c05": ("v1", 131_000, "합성시청자 첫째 이야기 하나"),
    "c06": ("v5", 160_000, "{:zz_test_02:}"),
    "c07": ("v6", 170_000, "분식집 메뉴 추천해 주세요"),
    "c08": ("v7", 300_000, "구간 밖 채팅 하나 둘"),
    "c09": ("v8", 140_000, "후원 합성 문구 셋", "donation"),
    "c10": ("v9", 150_000, "가려진 합성 채팅 넷", "text", "CBOTBLIND"),
}


def _lines():
    out = []
    for cid, row in SYN_TEXTS.items():
        viewer, t, text, *rest = row
        out.append(_msg(cid, t, viewer, text, *rest))
    return out


LABEL_CSV = ("chat_id,chat_vod_ms,utterance_vod_ms,grade,note\n"
             "c01,100000,108000,direct,합성 메모 하나\n"
             "c04,130000,139000,likely,합성 메모 둘\n"
             "c07,170000,178000,direct,합성 메모 셋\n"
             "zz,1,2,direct,없는 채팅\n")


def test_parse_messages_and_csv_keep_only_needed_fields():
    chats = cal.parse_messages(_lines() + ["", "not json", json.dumps({"chat_id": 1})])
    assert len(chats) == len(SYN_TEXTS)
    assert [c.t_ms for c in chats] == sorted(c.t_ms for c in chats)
    by = {c.chat_id: c for c in chats}
    assert by["c09"].visible and not by["c09"].scorable
    assert not by["c10"].visible and not by["c10"].scorable
    assert "합성" not in repr(by["c05"]) and "v1" not in repr(by["c05"])
    rows = cal.parse_manual_csv(LABEL_CSV)
    assert [(r.chat_id, r.chat_vod_ms, r.grade) for r in rows][:3] == [("c01", 100_000, "direct"),
                                                                       ("c04", 130_000, "likely"),
                                                                       ("c07", 170_000, "direct")]
    assert not any(hasattr(r, "note") for r in rows) and "합성" not in repr(rows)
    with pytest.raises(cal.CalibError):
        cal.parse_manual_csv("a,b\n1,2\n")


def test_build_groups_span_near_and_text():
    chats = cal.parse_messages(_lines())
    labels = cal.parse_manual_csv(LABEL_CSV)
    groups, info = cal.build_groups(chats, labels, near_ms=10_000)
    ids = {g: sorted(chats[i].chat_id for i in idx) for g, idx in groups.items()}
    assert ids["reacted"] == ["c01", "c04", "c07"] and ids["reacted_direct"] == ["c01", "c07"]
    assert ids["others"] == ["c02", "c03", "c05", "c06"]                   # 구간(100~170초) 안, 일반 채팅만
    assert ids["others_near"] == ["c02", "c03", "c05", "c06"]
    assert ids["others_text"] == ["c05"]                                     # 'ㅋㅋ'·이모티콘만은 뺀다
    assert info["label_found"] == 3 and info["label_time_match"] == 3 and info["label_rows"] == 4
    only_direct, _ = cal.build_groups(chats, labels, grades=("direct",))
    assert sorted(chats[i].chat_id for i in only_direct["reacted"]) == ["c01", "c07"]


def test_scorer_context_excludes_self_and_same_viewer():
    chats = cal.parse_messages(_lines())
    sc = cal.CalibScorer(chats, vs.EMPTY_CONTEXT)
    by = {c.chat_id: i for i, c in enumerate(chats)}
    ctx = sc.context_for(by["c03"], vs.EMPTY_CONTEXT)
    assert ctx.chat_ms == (100_000, 101_000)                                 # 앞 60초의 다른 시청자 글만(자기 글 뺌)
    r = sc.score([by["c03"]], vs.DEFAULT_SETTINGS)[by["c03"]]
    assert r.repeat.code == vs.REPEAT_COPY_SAME                              # 다른 시청자(c02)의 같은 글
    # 같은 시청자의 앞 글은 맥락 채팅이 아니라 history 로 간다
    assert sc.history_for(by["c05"]) == [SYN_TEXTS["c01"][2]]
    assert all(t != 131_000 for t in sc.context_for(by["c05"], vs.EMPTY_CONTEXT).chat_ms)
    assert 100_000 not in sc.context_for(by["c05"], vs.EMPTY_CONTEXT).chat_ms


def test_fisher_and_compare_numbers():
    assert cal.fisher_two_sided(1, 9, 11, 3) == pytest.approx(0.002759, abs=1e-6)
    assert cal.fisher_two_sided(3, 1, 1, 3) == pytest.approx(0.485714, abs=1e-6)
    assert cal.fisher_two_sided(0, 0, 0, 0) == 1.0
    assert cal.fisher_two_sided(5, 5, 5, 5) == pytest.approx(1.0)


def test_run_variants_toggles_and_candidates():
    chats = cal.parse_messages(_lines())
    groups, _ = cal.build_groups(chats, cal.parse_manual_csv(LABEL_CSV))
    base_ctx = vs.ScoreContext.build(topic_blocks=[(0, 400_000, ("떡볶이", "분식집", "어묵"))])
    res = cal.run_variants(cal.CalibScorer(chats, base_ctx), groups, no_flow_ctx=vs.EMPTY_CONTEXT,
                           names=cal.QUICK + ("good_talk_hits_1",))
    assert set(res) == set(cal.QUICK) | {"good_talk_hits_1"}
    b = res["base"]["groups"]["reacted"]
    assert b["n"] == 3 and all(b[a]["good"] + b[a]["ok"] + b[a]["weak"] == 3 for a in vs.AXES)
    assert res["base"]["reacted_vs_others"]["topic"]["good_diff_pp"] is not None
    for t in cal.TOGGLES:
        assert set(res[t]["changed_vs_base"]) == {"reacted", "others"}
    assert all(not row["notes"] for row in res.values())                   # 판의 값이 모두 범위 안
    cand = cal.candidates(res)
    assert set(cand) == set(vs.AXES) and all(c["variant"] in res for c in cand.values())


def test_every_variant_override_is_valid_guide_config():
    for name, axis, ov, ctx_kind in cal.VARIANTS:
        s, notes = cal.settings_for(ov)
        assert notes == [], name
        assert ctx_kind in ("full", "no_flow") and axis in (None,) + vs.AXES
        if ov:
            assert s != vs.DEFAULT_SETTINGS, name


def _make_project(root, name="proj-syn-a"):
    p = os.path.join(root, name)
    os.makedirs(os.path.join(p, "chat"))
    os.makedirs(os.path.join(p, "labels"))
    with open(os.path.join(p, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": "00000000-0000-4000-8000-000000000001"}, f)
    with open(os.path.join(p, "chat", "messages.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(_lines()) + "\n")
    with open(os.path.join(p, "labels", "manual-syn.csv"), "w", encoding="utf-8") as f:
        f.write(LABEL_CSV)
    return p


def test_main_end_to_end_prints_numbers_only(tmp_path, capsys):
    p = _make_project(str(tmp_path))
    out = str(tmp_path / "result.json")
    assert cal.main(["--project", p, "--quick", "--out", out]) == 0
    printed = capsys.readouterr().out
    with open(out, encoding="utf-8") as f:
        saved = f.read()
    rep = json.loads(saved)
    assert rep["input"]["groups"]["reacted"] == 3 and rep["input"]["words"] in ("no_asr", "missing")
    assert set(rep["variants"]) == set(cal.QUICK)
    for blob in (printed, saved):           # 본문·시청자·chat_id·메모가 나오지 않는다
        for cid, (viewer, _t, text, *_r) in SYN_TEXTS.items():
            assert text not in blob and f'"{viewer}"' not in blob and f'"{cid}"' not in blob
        assert "합성 메모" not in blob and "합성" not in blob


def test_main_refuses_pilot_and_missing_labels(tmp_path, capsys):
    p = _make_project(str(tmp_path), "pilot-15000001")
    assert cal.main(["--project", p]) == 1
    q = _make_project(str(tmp_path), "proj-syn-b")
    os.remove(os.path.join(q, "labels", "manual-syn.csv"))
    assert cal.main(["--project", q]) == 1
    assert cal.main(["--project", str(tmp_path / "없음")]) == 2
    err = capsys.readouterr().err
    assert "합성" not in err


# ───────────────────────────────────────────────────────────── 정밀도 우선 후보 (검토 2026-10-23)

def _dist(rg, rn, og, on, axis="topic"):
    """축 하나만 good 수를 정한 가짜 분포 두 개(반응받음, 반응 없음). 나머지 축은 모두 weak."""
    def one(g, n, ax):
        out = {"n": n}
        for a in vs.AXES:
            gg = g if a == ax else 0
            out[a] = {"good": gg, "ok": 0, "weak": n - gg, "good_pct": round(100.0 * gg / n, 1), "ok_pct": 0.0,
                      "weak_pct": round(100.0 * (n - gg) / n, 1), "codes": {}}
        return out
    return one(rg, rn, axis), one(og, on, axis)


def _row(axis, rg, rn, og, on, on_axis=None):
    a, b = _dist(rg, rn, og, on, on_axis or axis or "topic")
    groups = {cal.G_REACTED: a, cal.G_OTHERS: b, cal.G_NEAR: b, cal.G_TEXT: b, cal.G_DIRECT: a}
    return {"axis": axis, "overrides": {}, "context": "full", "notes": [], "groups": groups,
            "reacted_vs_others": cal.compare(a, b), "reacted_vs_near": cal.compare(a, b),
            "reacted_vs_text": cal.compare(a, b), "direct_vs_others": cal.compare(a, b)}


def test_compare_gives_precision_and_lift():
    a, b = _dist(5, 29, 35, 713)
    c = cal.compare(a, b)["topic"]
    assert c["good_precision"] == pytest.approx(0.125) and c["good_lift"] == pytest.approx(3.51)
    assert c["good_diff_pp"] == pytest.approx(12.3)
    assert cal.good_lift(1, 10, 0, 10) is None and cal.good_lift(0, 10, 3, 10) == 0.0
    assert cal.good_precision(0, 0) is None
    e = cal.compare({**a, "n": 0}, b)["topic"]
    assert e["good_lift"] is None and e["good_precision"] is None


def test_candidates_prefer_lift_not_good_diff():
    """재현(숫자는 검토 의견의 건수, 본문 없음): good 차이가 가장 큰 판(talk_hits 1, min_chars 아님)은 lift 가 base 보다
    낮다. 후보는 lift 가 base 보다 높은 판(talk_hits 3)이어야 한다. 확장 축도 같다(concrete_words 1 이 아니라 min_chars 6)."""
    res = {"base": _row(None, 5, 29, 35, 713),
           "good_talk_hits_1": _row("topic", 9, 29, 71, 713),
           "good_talk_hits_3": _row("topic", 5, 29, 26, 713)}
    assert res["good_talk_hits_1"]["reacted_vs_others"]["topic"]["good_diff_pp"] > \
        res["good_talk_hits_3"]["reacted_vs_others"]["topic"]["good_diff_pp"]
    cand = cal.candidates(res)["topic"]
    assert cand["variant"] == "good_talk_hits_3" and cand["metric"] == "good_lift"
    assert cand["good_lift"] == pytest.approx(4.73) and cand["base_good_lift"] == pytest.approx(3.51)
    assert cand["good_precision"] == pytest.approx(0.161) and cand["base_good_precision"] == pytest.approx(0.125)
    assert (cand["reacted_good"], cand["others_good"], cand["base_others_good"]) == (5, 26, 35)

    ex = {"base": _row(None, 10, 29, 48, 713, on_axis="expand"),
          "concrete_words_1": _row("expand", 11, 29, 57, 713),
          "min_chars_6": _row("expand", 10, 29, 40, 713)}
    c2 = cal.candidates(ex)["expand"]
    assert c2["variant"] == "min_chars_6" and c2["good_lift"] == pytest.approx(6.15)
    assert ex["concrete_words_1"]["reacted_vs_others"]["expand"]["good_lift"] < c2["base_good_lift"]


def test_candidates_keep_base_when_nothing_beats_lift_or_recall_collapses():
    res = {"base": _row(None, 6, 30, 30, 600),
           "looser": _row("topic", 12, 30, 120, 600),        # good 차이는 크지만 lift 낮음
           "tiny": _row("topic", 1, 30, 0, 600),             # lift 정의 안 됨(반응 없음 good 0)
           "starved": _row("topic", 2, 30, 1, 600)}          # lift 는 높지만 잡는 수가 base 의 반 미만
    assert cal.candidates(res)["topic"]["variant"] == "base"


def test_flow_presend_counts_leak_numbers_only():
    """flow_hit 가운데 겹친 낱말이 보낸 시각 앞 전사에도 있는 것(presend)과 뒤에서만 받쳐 주는 것(postsend_only)."""
    chats = cal.parse_messages(_lines())
    by = {c.chat_id: i for i, c in enumerate(chats)}
    ctx = vs.ScoreContext.build(topic_blocks=[(90_000, 400_000, ("떡볶이", "분식집"))],
                                transcript=[(95_000, 105_000, ("떡볶이",)), (200_000, 210_000, ("분식집",))])
    sc = cal.CalibScorer(chats, ctx)
    idx = [by["c01"], by["c07"], by["c02"]]
    scores = sc.score(idx, vs.DEFAULT_SETTINGS)
    assert scores[by["c01"]].topic.code == vs.TOPIC_FLOW_HIT and scores[by["c07"]].topic.code == vs.TOPIC_FLOW_HIT
    fp = sc.flow_presend(idx, scores, vs.DEFAULT_SETTINGS)
    # c01(100초): '떡볶이' 가 95초에 시작한 전사 창에 있음 → presend. c07(170초): '분식집' 전사는 200초(보낸 뒤) → 뒤에만
    assert fp == {"flow_hit": 2, "presend": 1, "postsend_only": 1, "block_past_send": 2}
    assert all(isinstance(v, int) for v in fp.values())


def test_topics_zone_and_refuse_unprotected_name(tmp_path, capsys):
    """보호 구역 밖 이름(derived/viewer-calib-topics.json)의 주제 흐름 파일은 읽지 않고 거부한다(03 2-1, 검토 2026-10-23)."""
    p = _make_project(str(tmp_path))
    os.makedirs(os.path.join(p, "derived"))
    bad = os.path.join(p, "derived", "viewer-calib-topics.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{}")
    assert cal.topics_zone(p, bad) == "unprotected"
    assert cal.topics_zone(p, os.path.join(p, "derived", "asr-topic-flow.json")) == "protected"
    assert cal.topics_zone(p, str(tmp_path / "x.json")) == "outside_project"
    assert cal.topics_zone(p, os.path.join(p, "derived", "mine.json"), {"protected_zone": ["derived/mine*"]}) \
        == "protected"
    assert cal.main(["--project", p, "--topics", bad]) == 2
    err = capsys.readouterr().err
    assert "보호 구역" in err and "합성" not in err
