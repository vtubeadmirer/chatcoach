"""생성 채팅 거르기 (app/core/gen_filter.py) 와 tools/make_scenario.py --generated — 모두 합성 데이터.

LLM 은 부르지 않는다: 생성 후보는 생성 담당(H-3)이 낼 파일 모양을 흉내 낸 합성 줄이다. 예문·이모티콘 코드·닉네임 모두 합성.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app import scenario_store as STORE
from app.core import gen_filter as GF
from app.core import scenario as SC
from app.core import viewer_score as VS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_scenario as MS  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=KST)

TOPIC = [(0, 2_000_000, ("보스", "공략"))]          # 합성 주제 흐름 낱말


def cand(gid, t, text, **kw):
    return {"source": "generated", "chat_kind": "text", "gen_id": gid, "show_vod_ms": t, "text": text, **kw}


def ctx(chats=()):
    return VS.ScoreContext.build(TOPIC, (), chats)


# ───────────────────────────────────────────────────────────── 순수: 거르기
def test_topic_weak_dropped_expand_recorded_only():
    cs = [cand("g0001", 100_000, "보스 공략 어떻게 해요?"),      # 주제 good · 열린 질문
          cand("g0002", 101_000, "오늘 날씨 맑네요"),             # 주제 겹침 없음 → 뺌
          cand("g0003", 102_000, "ㅋㅋㅋ {:zz_test_01:}"),        # 낱말 없음(주제 weak) → 뺌
          cand("g0004", 103_000, "보스")]                         # 주제 good · 확장성 weak(너무 짧음) → 남김(기록만)
    r = GF.filter_generated(cs, ctx())
    assert [c["gen_id"] for c in r.kept] == ["g0001", "g0004"]
    assert r.stats["dropped"] == {"topic.no_overlap": 1, "topic.no_words": 1}
    assert r.stats["expand"]["weak"] == 1 and r.stats["expand_codes"].get("expand.too_short") == 1
    assert r.stats["axis_weak"]["topic"] == 2 and r.stats["kept"] == 2 and r.stats["scored"] == 4
    v = {x.index: x for x in r.verdicts}
    assert v[3].keep and v[3].score.expand.level == VS.WEAK            # 확장성 weak 이어도 남는다
    assert v[1].reason == "topic.no_overlap" and not v[1].keep


def test_no_context_kept():
    """맥락(주제 흐름·전사)이 없으면 주제는 판단하지 않음(ok) — 남긴다. 반복은 history 로 본다."""
    cs = [cand("g0001", 100_000, "오늘 날씨 맑네요"), cand("g0002", 120_000, "오늘 날씨 맑네요")]
    r = GF.filter_generated(cs, None)
    assert [c["gen_id"] for c in r.kept] == ["g0001"]
    assert r.stats["dropped"] == {"repeat.self_same": 1} and r.stats["topic_no_context"] == 2


def test_repeat_weak_dropped_self_and_copy():
    """앞서 남긴 생성 줄과 같음(self_same), 앞 45초 화면 채팅(실제·가져온 채팅)과 같음(copy_same)은 뺀다."""
    real = [(99_000, "보스 패턴 외웠어요")]
    cs = [cand("g0001", 100_000, "보스 공략 어떻게 해요?"),
          cand("g0002", 110_000, "보스 공략 어떻게 해요?"),
          cand("g0003", 105_000, "보스 패턴 외웠어요")]
    r = GF.filter_generated(cs, ctx(real))
    assert [c["gen_id"] for c in r.kept] == ["g0001"]
    assert r.stats["dropped"] == {"repeat.copy_same": 1, "repeat.self_same": 1}
    assert r.stats["axis_weak"]["repeat"] == 2


def test_history_only_kept_lines_and_order_independent():
    """자리가 없어 뺀 줄은 history 에 넣지 않는다(뒤의 같은 글이 self_same 이 되지 않음). 입력 순서와 상관없이 같은 결과."""
    q = GF.plan_quota([(60_000, 120_000, 2, 6, True), (120_000, 180_000, 2, 6, True)],
                      max_virtual_ratio=0.5, max_generated_ratio=None)
    q.left[:] = [0, 1]                                   # 앞 창은 자리 없음
    cs = [cand("g0001", 70_000, "보스 공략 어떻게 해요?"), cand("g0002", 130_000, "보스 공략 어떻게 해요?")]
    r = GF.filter_generated(cs, ctx(), quota=q)
    assert [c["gen_id"] for c in r.kept] == ["g0002"] and r.stats["dropped"] == {"no_room": 1}
    q2 = GF.plan_quota([(60_000, 120_000, 2, 6, True), (120_000, 180_000, 2, 6, True)],
                       max_virtual_ratio=0.5, max_generated_ratio=None)
    q2.left[:] = [0, 1]
    r2 = GF.filter_generated(list(reversed(cs)), ctx(), quota=q2)
    assert [c["gen_id"] for c in r2.kept] == ["g0002"] and r2.stats == r.stats


def test_bad_inputs_and_range():
    cs = [cand("g0001", 50_000, "보스 공략"), {"source": "retrieved", "text": "보스"}, cand("g0003", 70_000, "  "),
          cand("g0004", None, "보스 공략"), cand("g0005", 0, "보스 공략", show_vod_ms=None, show_rel_ms=5_000)]
    r = GF.filter_generated(cs, ctx(), range_ms=(60_000, 120_000))
    assert r.stats["dropped"] == {"bad_text": 1, "bad_time": 2, "not_generated": 1}
    assert [c["gen_id"] for c in r.kept] == ["g0005"]                    # show_rel_ms = 구간 시작 + 5초


def test_stats_numbers_and_codes_only():
    secret = "합성비밀문장"
    r = GF.filter_generated([cand("g0001", 100_000, f"보스 {secret}"), cand("g0002", 101_000, secret)], ctx([(99_000, secret)]))
    blob = json.dumps([r.stats, [v.as_dict() for v in r.verdicts]], ensure_ascii=False)
    assert secret not in blob and "보스" not in blob
    assert GF.dropped_by_axis(r.stats)["gscore_repeat"] + GF.dropped_by_axis(r.stats)["gscore_topic"] == \
        sum(r.stats["dropped"].values())


# ───────────────────────────────────────────────────────────── 순수: 자리
def test_plan_quota_caps_and_quiet_only():
    # 창 4개: 바쁨(5) · 드묾(1) · 드묾(0) · 바쁨(5). 실제 11, 이미 넣은 가상 3(창1 에 2, 창2 에 1)
    ws = [(0, 60_000, 5, 6, False), (60_000, 120_000, 1, 6, True), (120_000, 180_000, 0, 6, True),
          (180_000, 240_000, 5, 6, False)]
    q = GF.plan_quota(ws, [61_000, 62_000, 121_000], max_virtual_ratio=0.5, max_generated_ratio=0.25)
    # 가상 상한 floor(0.5*11/0.5)=11 → 남은 8, 생성 상한 floor(0.25*14/0.75)=4. 필요 3+5=8 → 4 를 돌려 나눔
    assert q.info == {"real": 11, "virtual_before": 3, "need": 8, "cap_virtual": 11, "cap_generated": 4, "room": 4,
                      "quiet_windows": 2}
    assert q.left == [0, 2, 2, 0]
    assert not q.room(30_000) and q.room(60_000) and not q.room(240_000)
    q.take(60_000)
    q.take(61_000)
    assert not q.room(65_000) and q.total == 2
    with pytest.raises(ValueError):
        q.take(65_000)
    # 생성 상한 없음 → 가상 상한까지
    q2 = GF.plan_quota(ws, [61_000, 62_000, 121_000], max_virtual_ratio=0.5)
    assert q2.left == [0, 3, 5, 0] and q2.info["cap_generated"] == -1
    # 실제 채팅이 없으면 자리 0 (20 5-1)
    assert GF.plan_quota([(0, 60_000, 0, 6, True)], max_virtual_ratio=0.5).total == 0
    with pytest.raises(ValueError):
        GF.plan_quota(ws, max_virtual_ratio=1.0)
    with pytest.raises(ValueError):
        GF.plan_quota(ws, max_virtual_ratio=0.5, max_generated_ratio=-0.1)


# ───────────────────────────────────────────────────────────── 도구: make_scenario --generated
def uid(name: str) -> str:
    import hashlib
    h = hashlib.sha256(name.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}"


_n = [0]


def _cid():
    _n[0] += 1
    return "c" + format(0x900000 + _n[0], "020x")


def viewer(i):
    return "vw_" + format(i, "012d").translate(str.maketrans("0123456789", "abcdefghij"))


def msg(t, text, v, mask="합*"):
    return {"chat_id": _cid(), "t_ms": t, "server_time_ms": None, "type_code": 1, "kind": "text", "status": "NORMAL",
            "viewer_id": v, "is_anonymous": False, "nickname_masked": mask, "role": "viewer", "text": text,
            "raw_ref": {"fetch_id": "F20261023-abcd", "page": 0, "index": 0}}


FROM, TO = 600_000, 840_000      # 1분 창 4개: 창0 바쁨(5) · 창1 드묾(1) · 창2 바쁨(5) · 창3 드묾(0). 생성 후보는 모두 창1


def make_layout(tmp_path):
    """<데이터>\\projects\\p-gen (+ identities 합성닉) · <데이터>\\config\\blocklist-ko.txt(합성금칙) · 주제 흐름 파일."""
    data = tmp_path / "data"
    d = data / "projects" / "p-gen"
    os.makedirs(d / "chat" / "protected")
    ms = [msg(600_000 + i * 500, f"바쁜 창 채팅 {i}", viewer(900 + i)) for i in range(5)]
    ms += [msg(670_000, "드문 창 실제 하나", viewer(950))]
    ms += [msg(720_000 + i * 500, f"바쁜 창 둘째 {i}", viewer(960 + i)) for i in range(5)]
    ms += [msg(10_000 + i * 1000, f"합성 문장 {i} 보스 공략", viewer(i)) for i in range(3)]   # retrieved 후보(구간 밖)
    (d / "project.json").write_text(json.dumps({"project_id": uid("p-gen"), "broadcast": {"channel_id": "ch-g"}}),
                                    encoding="utf-8")
    with open(d / "chat" / "messages.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for m in ms:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    with open(d / "chat" / "protected" / "identities.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"viewer_id": viewer(0), "nicknames": [{"nickname": "합성닉"}]}, ensure_ascii=False) + "\n")
    os.makedirs(data / "config")
    (data / "config" / "blocklist-ko.txt").write_text("# 합성\n합성금칙\n", encoding="utf-8")
    tf = tmp_path / "topic-flow.json"
    tf.write_text(json.dumps({"schema": "chatcoach/topic-flow", "time_axis": "vod_ms",
                              "blocks": [{"start_ms": 0, "end_ms": 900_000, "keywords": ["보스", "공략"]}]},
                             ensure_ascii=False), encoding="utf-8")
    return str(d), str(tf)


GEN_HEAD = {"kind": "generation.header", "project_id": uid("p-gen"),
            "generator": {"backend": "lmstudio", "model_id": "synthetic-model-7b", "prompt_template_id": "gen-v1",
                          "temperature": 0.8, "seed": 7, "context_ms": 120_000},
            "filters": {"versions": ["llm-check-v1"], "dropped": {"llm_flag": 2}}}


def gen_lines():
    L = lambda gid, t, text, **kw: {"kind": "scenario.chat", "source": "generated", "chat_kind": "text",  # noqa: E731
                                    "show_vod_ms": t, "gen_id": gid, "text": text, **kw}
    return [
        L("g0001", 670_500, "보스 공략 어떻게 해요?", display_mask="하**"),
        L("g0002", 671_000, "보스 공략 어떻게 해요?"),                        # 반복(self_same)
        L("g0003", 672_000, "오늘 날씨 맑네요"),                               # 주제 겹침 없음
        L("g0004", 673_000, "합성금칙 보스 진짜"),                             # 금칙어(두 번째 막기)
        L("g0005", 674_000, "합성닉 보스 잡아요"),                             # 원 닉네임
        L("g0006", 700_000, "보스 패턴이 어렵네", display_mask="하**"),        # 이름 겹침 → 새 이름
        L("g0007", 701_000, "공략 영상 보셨어요? {:zz_test_01:}", emotes=["{:zz_test_01:}"]),
        L("g0008", 702_000, "보스 체력 얼마 남았어요?"),
        L("g0009", 703_000, "보스 클리어 가능할까요?"),
        L("g0010", 900_000, "보스 구간 밖"),                                   # 구간 밖
        {"source": "generated", "gen_id": "x1", "text": "보스"},                # 모양 틀림
        L("g0012", 704_000, "보스 {:zz_test_02:}"),                            # 허용 목록 밖 이모티콘
        L("g0013", 705_000, "보스 공략 최고", checks={"blocked": 1}),          # 생성 담당 거르기에 걸린 줄
        L("g0014", 706_000, "바쁜 창 채팅 3"),                                 # 실제 채팅 그대로
    ]


def write_gen(path, lines, head=GEN_HEAD):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if head is not None:
            f.write(json.dumps(head, ensure_ascii=False) + "\n")
        for x in lines:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")


def run(capsys, args):
    rc = MS.main(args, now=NOW)
    return rc, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_tool_merges_generated_and_filters(tmp_path, capsys):
    proj, tf = make_layout(tmp_path)
    gp = tmp_path / "gen.jsonl"
    write_gen(gp, gen_lines())
    with open(os.path.join(proj, "chat", "messages.jsonl"), "rb") as f:
        before = f.read()
    args = ["--project", proj, "--from", str(FROM), "--to", str(TO), "--seed", "3", "--topic-flow", tf,
            "--generated", str(gp)]
    rc, out = run(capsys, args)
    assert rc == 0 and out["written"] is True
    g = out["generated"]
    assert g["guard"] == {"blocked": 1, "nickname": 1, "copy": 1}
    assert g["invalid"] == {"gen_emote": 1, "gen_flagged": 1, "gen_invalid": 1}
    f = g["filter"]
    assert f["dropped"].get("repeat.self_same") == 1 and f["dropped"].get("topic.no_overlap") == 1
    assert f["dropped"].get("bad_time") == 1
    n_ret, n_gen = out["counts"]["retrieved"], out["counts"]["generated"]
    assert n_gen == g["kept"] == f["kept"] >= 2
    real = 11
    assert (n_ret + n_gen) / (real + n_ret + n_gen) <= 0.5                      # 구간 가상 비율 상한
    assert n_gen / (real + n_ret + n_gen) <= 0.25                               # 생성 비율 상한(기본)
    s = STORE.read_scenario(STORE.scenario_path(proj, out["scenario_id"]), project_id=uid("p-gen"))
    gens = [c for c in s.chats if c["source"] == "generated"]
    assert len(gens) == n_gen and s.header["counts"]["generated"] == n_gen
    assert s.header["generator"]["backend"] == "lmstudio" and s.header["generator"]["model_id"] == "synthetic-model-7b"
    dr = s.header["filters"]["dropped"]
    assert dr["gen_gscore_topic"] == 1 and dr["gen_gscore_repeat"] == 1 and dr["gen_blocked"] == 1
    assert dr["gen_nickname"] == 1 and dr["gen_copy"] == 1 and dr["gen_llm_flag"] == 2 and dr["gen_bad_time"] == 1
    assert "llm-check-v1" in s.header["filters"]["versions"]
    masks = [c["display_mask"] for c in s.chats]
    assert len(set(masks)) == len(masks)                                        # 줄마다 다른 가림 이름(D-20 7)
    assert all(SC.DISPLAY_MASK_RE.match(m) for m in masks) and "합*" not in masks
    with open(STORE.scenario_path(proj, out["scenario_id"]), encoding="utf-8") as fh:
        body = fh.read()
    assert "합성금칙" not in body and "합성닉" not in body and "바쁜 창" not in body and "날씨" not in body
    blob = json.dumps(out, ensure_ascii=False)
    assert "보스" not in blob and "합성금칙" not in blob and "합성닉" not in blob   # 출력은 숫자·코드·ID 만
    with open(os.path.join(proj, "chat", "messages.jsonl"), "rb") as fh:
        assert fh.read() == before
    # 같은 입력 → 같은 시나리오(새로 쓰지 않음)
    rc, out2 = run(capsys, args)
    assert rc == 0 and out2["written"] is False and out2["existing"] == out["scenario_id"]


def test_tool_generated_ratio_option_and_dry_run(tmp_path, capsys):
    proj, tf = make_layout(tmp_path)
    gp = tmp_path / "gen.jsonl"
    write_gen(gp, gen_lines())
    base = ["--project", proj, "--from", str(FROM), "--to", str(TO), "--seed", "3", "--topic-flow", tf,
            "--generated", str(gp), "--dry-run"]
    rc, out0 = run(capsys, base + ["--max-generated-ratio", "0"])
    assert rc == 0 and out0["counts"]["generated"] == 0 and out0["valid"] is True
    assert out0["generated"]["filter"]["dropped"].get("no_room", 0) >= 1
    rc, out1 = run(capsys, base + ["--max-generated-ratio", "0.6"])
    assert rc == 0 and out1["valid"] is True and out1["counts"]["generated"] >= out0["counts"]["generated"]
    assert not os.path.isdir(os.path.join(proj, "scenarios"))                  # dry-run 은 쓰지 않는다
    rc, bad = run(capsys, base + ["--max-generated-ratio", "1"])
    assert rc == 1 and bad["error"] == "bad_argument"


@pytest.mark.parametrize("head,body,code", [
    ({**GEN_HEAD, "generator": {"backend": "external"}}, None, "generated_backend"),
    ({**GEN_HEAD, "project_id": uid("someone-else")}, None, "generated_project_mismatch"),
    (GEN_HEAD, "{깨진 줄\n", "bad_generated"),
])
def test_tool_generated_refusals(tmp_path, capsys, head, body, code):
    proj, tf = make_layout(tmp_path)
    gp = tmp_path / "gen.jsonl"
    write_gen(gp, gen_lines(), head=head)
    if body is not None:
        with open(gp, "a", encoding="utf-8") as f:
            f.write(body)
    rc, out = run(capsys, ["--project", proj, "--from", str(FROM), "--to", str(TO), "--generated", str(gp)])
    assert rc == 1 and out == {"error": code}
    assert not os.path.isdir(os.path.join(proj, "scenarios"))


def test_tool_generated_missing_file_and_pilot(tmp_path, capsys):
    proj, _tf = make_layout(tmp_path)
    rc, out = run(capsys, ["--project", proj, "--from", str(FROM), "--to", str(TO),
                           "--generated", str(tmp_path / "없음.jsonl")])
    assert rc == 1 and out == {"error": "no_generated"}
    pilot = tmp_path / "data" / "projects" / "pilot-x"
    rc, out = run(capsys, ["--project", str(pilot), "--from", "0", "--to", "1000", "--generated", "x"])
    assert rc == 2 and out == {"error": "pilot_locked"}                     # 생성 파일도 읽기 전에 거부


def test_sanitize_drops_unknown_fields():
    c, why = MS.sanitize_generated({"kind": "scenario.chat", "source": "generated", "show_vod_ms": 5, "gen_id": "g0001",
                                    "text": "보스", "nickname_masked": "시***", "viewer_id": "v1", "badge": "x",
                                    "context": {"asr_from_ms": 1, "asr_to_ms": 2, "topic_id": "t03", "raw": "본문"}})
    assert why is None and set(c) == {"source", "chat_kind", "show_vod_ms", "gen_id", "text", "context"}
    assert c["context"] == {"asr_from_ms": 1, "asr_to_ms": 2, "topic_id": "t03"}
    assert MS.sanitize_generated({**c, "chat_kind": "donation"})[1] == "gen_kind"     # 사칭 막기: 일반 채팅만
    assert MS.clean_generator(None) == {"backend": "lmstudio"}
    with pytest.raises(MS.GenInputError):
        MS.clean_generator({"backend": "none"})
