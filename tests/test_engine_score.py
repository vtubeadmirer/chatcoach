"""engine/score.py — 합성 데이터만 쓴다(chat_id = 'c' + sha1('fixture-chat-N')[:20], 본문 없음, video_no 0)."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from engine import score as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTE_SECRET = "합성메모문장절대출력금지"


def cid(n: int) -> str:
    return "c" + hashlib.sha1(f"fixture-chat-{n}".encode()).hexdigest()[:20]


def rx(n, grade, rt=None):
    return {"chat_id": cid(n), "result": "unconfirmed" if grade is None else "graded", "grade": grade,
            "reaction_time_ms": rt, "utterance_start_ms": rt, "utterance_end_ms": None if rt is None else rt + 800,
            "score": None, "chat_t_ms": n * 10000}


def link_doc(**kw):
    d = {
        "schema": "chatcoach/ai-result", "schema_version": 2, "run_id": "link-v0-test", "time_axis": "vod_ms",
        "engine": {"name": "chatcoach-link", "version": "0.1.0", "config_sha256": "0" * 64},
        "target": {"video_no": "0", "messages_sha256": None},
        "calibration": {"offset_ms": 0, "file_start_in_vod_ms": 0, "status": "verified", "needs_anchor_calibration": False},
        "scope": {"start_ms": 0, "end_ms": 200000},
        "reactions": [
            rx(1, "direct", 11500), rx(2, "likely", 21000), rx(3, "direct", 33000), rx(4, "related_only"),
            rx(5, None), rx(6, "direct", 61000), rx(7, "unclear"), rx(8, "likely", 82000), rx(9, "direct", 91000),
            rx(10, None),
        ],
    }
    d.update(kw)
    return d


LABEL_ROWS = [
    (1, 10000, 11000, "direct"),
    (2, 20000, 23000, "likely"),
    (3, 30000, 30000, "높음"),
    (4, 40000, 41000, "direct"),
    (5, 50000, 52000, "likely"),
    (8, 80000, "", "related_only"),
    (9, 90000, 91000, "불명확"),
    (7, 70000, "", "미확인"),
    (12, 120000, 121000, "direct"),
]


def write_labels(path, rows=LABEL_ROWS, *, bom=True, header="chat_id, chat_vod_ms, utterance_vod_ms, grade, note"):
    lines = [header] + [f"{cid(n)}, {c}, {u}, {g}, {NOTE_SECRET}" for n, c, u, g in rows]
    data = ("﻿" if bom else "") + "\r\n".join(lines) + "\r\n"
    path.write_bytes(data.encode("utf-8"))
    return str(path)


def write_messages(path, ns=range(1, 12), *, overrides=None):
    overrides = overrides or {}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for n in ns:
            r = {"chat_id": cid(n), "t_ms": n * 10000, "kind": "donation" if n == 11 else "text", "text": "합성"}
            r.update(overrides.get(n, {}))
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(path)


def labels(tmp_path, **kw):
    lb, w, _ = S.load_labels(write_labels(tmp_path / "l.csv"), **kw)
    return lb, w


# ── 단위 ─────────────────────────────────────────────────────────────────

def test_parse_ms_cell():
    assert S.parse_ms_cell("") is None
    assert S.parse_ms_cell(" 412300 ") == 412300
    assert S.parse_ms_cell("1:02:03.5") == 3723500
    assert S.parse_ms_cell("6:52.3") == 412300
    with pytest.raises(ValueError):
        S.parse_ms_cell("412.3")           # 초인지 ms 인지 모름
    with pytest.raises(ValueError):
        S.parse_ms_cell("abc")


@pytest.mark.parametrize("s,g", [("direct", "direct"), ("Direct", "direct"), ("직접", "direct"), ("높음", "likely"),
                                 ("관련성만", "related_only"), ("관련성만 있음", "related_only"), ("불명확", "unclear"),
                                 ("미확인", "unconfirmed"), ("반응 미확인", "unconfirmed"), ("none", "unconfirmed")])
def test_normalize_grade(s, g):
    assert S.normalize_grade(s) == g


def test_normalize_grade_unknown():
    with pytest.raises(ValueError):
        S.normalize_grade("아마")


def test_percentile_linear():
    assert S.percentile([], 50) is None
    assert S.percentile([7], 90) == 7.0
    assert S.percentile([-2000, 500, 3000], 50) == 500
    assert S.percentile([-2000, 500, 3000], 90) == pytest.approx(2500)
    assert S.percentile([1, 2, 3, 4], 50) == 2.5


def test_load_labels_bom_spaces_dups_and_bad_rows(tmp_path):
    rows = LABEL_ROWS[:2] + [(1, 10000, 11000, "likely"), (99, "x", 1, "direct")]
    p = write_labels(tmp_path / "l.csv", rows)
    with open(p, "ab") as f:
        f.write(b"\r\n,,,,\r\n# memo line\r\nnot-a-chat-id, 1, 2, direct, x\r\n")
    lb, w, sha = S.load_labels(p)
    assert [x["chat_id"] for x in lb] == [cid(1), cid(2)]
    assert lb[0]["grade"] == "direct"          # 중복은 첫 행
    assert len(sha) == 64
    assert any("같은 chat_id" in x for x in w)
    assert any("뺀 행 2개" in x for x in w)
    assert all("note" not in x for x in lb)


def test_load_labels_missing_column(tmp_path):
    p = write_labels(tmp_path / "l.csv", header="chat_id, chat_vod_ms, grade")
    with pytest.raises(ValueError, match="utterance_vod_ms"):
        S.load_labels(p)


def test_load_labels_cp949_fallback(tmp_path):
    p = tmp_path / "l.csv"
    p.write_bytes(f"chat_id,chat_vod_ms,utterance_vod_ms,grade,note\n{cid(1)},10000,11000,직접,메모\n".encode("cp949"))
    lb, w, _ = S.load_labels(str(p))
    assert lb[0]["grade"] == "direct" and any("cp949" in x for x in w)


# ── 채점 ─────────────────────────────────────────────────────────────────

def test_score_main_scenario(tmp_path):
    lb, _ = labels(tmp_path)
    msgs = S.load_message_index(write_messages(tmp_path / "m.jsonl"))
    r = S.score(lb, link_doc(), messages=msgs)
    b = r["binary"]
    assert (b["tp"], b["fn"], b["fp_labeled"], b["fp_unlabeled"], b["tn_labeled"]) == (3, 2, 1, 1, 1)
    assert b["precision"] == 0.6 and b["precision_labeled_only"] == 0.75 and b["recall"] == 0.6 and b["f1"] == 0.6
    c = r["counts"]
    assert c["labels_in_scope"] == 9 and c["excluded"] == {"ai_unevaluated": 1, "grade_unclear": 1}
    assert c["engine_positive_in_scope"] == 6 and c["engine_positive_on_human_unclear"] == 1
    t = r["reaction_time_error"]
    assert t["n"] == 3 and t["missing_time"] == 0
    assert t["median_ms"] == 500 and t["p90_ms"] == pytest.approx(2500)
    assert t["abs_median_ms"] == 2000 and t["abs_p90_ms"] == pytest.approx(2800)
    assert t["within_2s"] == 0.6667 and t["within_5s"] == 1.0
    tab = r["grade_confusion"]["table"]
    assert tab["direct"] == {"direct": 1, "likely": 0, "related_only": 1, "unclear": 0, "unconfirmed": 0, "not_evaluated": 1}
    assert tab["likely"]["likely"] == 1 and tab["likely"]["direct"] == 1 and tab["likely"]["unconfirmed"] == 1
    assert tab["related_only"]["likely"] == 1 and tab["unclear"]["direct"] == 1 and tab["unconfirmed"]["unclear"] == 1
    assert r["grade_exact_agreement"] == {"n": 6, "exact": 2, "rate": 0.3333,
                                          "note": r["grade_exact_agreement"]["note"]}
    ls = r["lists"]
    assert ls["human_only"] == [cid(4), cid(5)]
    assert ls["engine_only"] == [cid(6), cid(8)]
    assert ls["engine_only_labeled_negative"] == [cid(8)]
    assert ls["ai_unevaluated"] == [cid(12)] and ls["human_unclear"] == [cid(9)]
    # chat 12 는 messages 에 없다 → 경고
    assert r["messages_check"]["labels_not_in_messages"] == [cid(12)]
    assert any("messages.jsonl 에 없는 chat_id 1개" in w for w in r["warnings"])


def test_score_cli_scope(tmp_path):
    lb, _ = labels(tmp_path)
    r = S.score(lb, link_doc(), start_ms=0, end_ms=25000)
    assert r["scope"] == {"start_ms": 0, "end_ms": 25000, "source": "cli"}
    b = r["binary"]
    assert (b["tp"], b["fn"], b["fp_labeled"], b["fp_unlabeled"]) == (2, 0, 0, 0)
    assert b["precision"] == 1.0 and b["recall"] == 1.0 and b["f1"] == 1.0
    assert r["counts"]["labels_out_of_scope"] == 7
    assert any("messages.jsonl 을 주지 않아" in w for w in r["warnings"])


def test_score_uses_link_scope_by_default(tmp_path):
    lb, _ = labels(tmp_path)
    r = S.score(lb, link_doc(scope={"start_ms": 25000, "end_ms": 60000}))
    assert r["scope"]["source"] == "link"
    b = r["binary"]
    # 범위 안: 사람 3(likely·TP)·4(FN)·5(FN). 엔진 반응 있음: 3 뿐(6 은 60000 으로 끝 경계 밖)
    assert (b["tp"], b["fn"], b["fp_unlabeled"]) == (1, 2, 0)


def test_score_messages_time_mismatch_and_non_text(tmp_path):
    rows = [(1, 10001, 11000, "direct"), (11, 110000, "", "direct")]
    lb, _, _ = S.load_labels(write_labels(tmp_path / "l.csv", rows))
    msgs = S.load_message_index(write_messages(tmp_path / "m.jsonl"))
    r = S.score(lb, link_doc(), messages=msgs)
    assert r["messages_check"]["chat_vod_ms_mismatch"] == [cid(1)]
    assert r["messages_check"]["non_text_message"] == [cid(11)]
    assert r["binary"]["tp"] == 1    # 범위·시각은 messages t_ms 를 쓴다


def test_score_no_positive_gives_none_not_crash(tmp_path):
    lb, _, _ = S.load_labels(write_labels(tmp_path / "l.csv", [(4, 40000, "", "related_only")]))
    r = S.score(lb, link_doc(reactions=[rx(4, "related_only")]))
    b = r["binary"]
    assert b["precision"] is None and b["recall"] is None and b["f1"] is None
    assert r["reaction_time_error"]["median_ms"] is None


def test_score_zero_recall_f1_zero(tmp_path):
    lb, _, _ = S.load_labels(write_labels(tmp_path / "l.csv", [(4, 40000, "", "direct")]))
    r = S.score(lb, link_doc(reactions=[rx(4, "related_only"), rx(6, "direct", 61000)]))
    assert r["binary"]["recall"] == 0.0 and r["binary"]["precision"] == 0.0 and r["binary"]["f1"] == 0.0


def test_v1_link_without_chat_t_ms(tmp_path):
    lb, _ = labels(tmp_path)
    doc = link_doc()
    doc["schema_version"] = 1
    for x in doc["reactions"]:
        del x["chat_t_ms"]
    del doc["scope"]
    msgs = S.load_message_index(write_messages(tmp_path / "m.jsonl"))
    r = S.score(lb, doc, messages=msgs, start_ms=0, end_ms=65000)
    # 범위 안 엔진 반응 있음: 1,2,3,6 (시각은 messages 에서)
    assert r["counts"]["engine_positive_in_scope"] == 4
    assert r["binary"]["fp_unlabeled"] == 1


# ── run / 파일 / CLI ─────────────────────────────────────────────────────

def _write_link(tmp_path, doc=None):
    p = tmp_path / "link.json"
    p.write_text(json.dumps(doc or link_doc(), ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_load_link_rejects_wrong_schema(tmp_path):
    with pytest.raises(ValueError, match="ai-result"):
        S.load_link(_write_link(tmp_path, {"schema": "x", "reactions": []}))
    with pytest.raises(ValueError, match="schema_version"):
        S.load_link(_write_link(tmp_path, link_doc(schema_version=9)))
    p = tmp_path / "nan.json"
    p.write_text('{"schema": "chatcoach/ai-result", "schema_version": 2, "reactions": [], "x": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="NaN"):
        S.load_link(str(p))


def test_unverified_calibration_warns(tmp_path):
    doc = link_doc(calibration={"offset_ms": 0, "status": "unverified", "needs_anchor_calibration": True})
    rep = S.run(write_labels(tmp_path / "l.csv"), _write_link(tmp_path, doc))
    assert any("unverified" in w for w in rep["warnings"])


def test_player_time_conversion_uses_link_calibration(tmp_path):
    rows = [(1, 10000, 12500, "direct")]
    doc = link_doc(calibration={"offset_ms": 1000, "file_start_in_vod_ms": 0, "status": "verified"})
    rep = S.run(write_labels(tmp_path / "l.csv", rows), _write_link(tmp_path, doc), label_time="player")
    # VOD = 플레이어 − 오프셋 = 11500 → 엔진 11500 과 Δt 0
    assert rep["reaction_time_error"]["median_ms"] == 0
    assert rep["inputs"]["label_time_conversion"] == {"offset_ms": 1000, "file_start_in_vod_ms": 0}
    rep2 = S.run(write_labels(tmp_path / "l.csv", rows), _write_link(tmp_path, doc), label_time="player",
                 offset_ms=0, file_start_ms=500)
    assert rep2["reaction_time_error"]["median_ms"] == 11500 - 13000


def test_messages_sha_mismatch_warns(tmp_path):
    doc = link_doc(target={"video_no": "0", "messages_sha256": "f" * 64})
    rep = S.run(write_labels(tmp_path / "l.csv"), _write_link(tmp_path, doc),
                messages_path=write_messages(tmp_path / "m.jsonl"))
    assert any("messages_sha256" in w for w in rep["warnings"])


def test_run_writes_report_without_note(tmp_path):
    out = tmp_path / "rep" / "score.json"
    rep = S.run(write_labels(tmp_path / "l.csv"), _write_link(tmp_path), out_path=str(out))
    raw = out.read_text(encoding="utf-8")
    assert NOTE_SECRET not in raw
    assert json.loads(raw)["binary"] == rep["binary"]
    assert rep["format"] == "chatcoach.score"
    txt = S.format_report(rep)
    assert "재현율" in txt and "혼동표" in txt and NOTE_SECRET not in txt


def _run_cli(*args):
    return subprocess.run([sys.executable, "-m", "engine", *args], cwd=ROOT, capture_output=True, timeout=60)


def test_cli_score_with_project(tmp_path):
    proj = tmp_path / "p0"
    (proj / "chat").mkdir(parents=True)
    write_messages(proj / "chat" / "messages.jsonl")
    out = tmp_path / "score.json"
    p = _run_cli("score", "--labels", write_labels(tmp_path / "l.csv"), "--link", _write_link(tmp_path),
                 "--project", str(proj), "--out", str(out))
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    so = p.stdout.decode("utf-8")
    assert NOTE_SECRET not in so and "합성" not in so
    assert "TP 3" in so and cid(4) in so and "messages.jsonl 에 없는 chat_id 1개" in so
    assert json.loads(out.read_text(encoding="utf-8"))["binary"]["recall"] == 0.6


def test_cli_score_error_returns_1(tmp_path):
    p = _run_cli("score", "--labels", str(tmp_path / "none.csv"), "--link", _write_link(tmp_path))
    assert p.returncode == 1
    assert json.loads(p.stdout.decode("utf-8"))["error"] in ("FileNotFoundError", "OSError")


def test_cli_link_passthrough_and_help_lists_new_commands():
    p = _run_cli("link", "-h")
    assert p.returncode == 0 and b"--project" in p.stdout
    p = _run_cli("-h")
    so = p.stdout.decode("utf-8")
    assert p.returncode == 0 and "transcript" in so and "score" in so and "link" in so


# ── 검토 수정 재현 ────────────────────────────────────────────────────────

def _v1(reactions):
    doc = link_doc(reactions=reactions)
    doc["schema_version"] = 1
    for x in doc["reactions"]:
        x.pop("chat_t_ms", None)
    for k in ("scope", "calibration"):
        doc.pop(k, None)
    return doc


def test_v1_link_without_messages_does_not_count_unknown_time_in_scope(tmp_path):
    """v1 link(chat_t_ms 없음) + messages 없음 + 범위 → 시각 모르는 엔진 반응은 범위 안으로 치지 않는다."""
    lb, _, _ = S.load_labels(write_labels(tmp_path / "l.csv", [(1, 1000, 1500, "direct")]))
    doc = _v1([rx(1, "direct", 1500), rx(92, "direct", 921000), rx(93, "likely", 931000)])
    r = S.score(lb, doc, start_ms=0, end_ms=60000)
    c, b = r["counts"], r["binary"]
    assert c["engine_positive_in_scope"] == 1 and b["fp_unlabeled"] == 0 and b["precision"] == 1.0
    assert b["tp"] == 1 and c["link_reactions_no_time"] == 2 and c["engine_positive_no_time"] == 2
    assert c["link_reactions_in_scope"] == 1
    assert any("범위 판정을 못 해 뺀 엔진 반응 2개" in w for w in r["warnings"])


def test_v1_link_without_scope_keeps_all(tmp_path):
    lb, _, _ = S.load_labels(write_labels(tmp_path / "l.csv", [(1, 1000, 1500, "direct")]))
    r = S.score(lb, _v1([rx(1, "direct", 1500), rx(92, "direct", 921000)]))
    assert r["counts"]["engine_positive_in_scope"] == 2 and r["counts"]["link_reactions_no_time"] == 0
    assert not any("범위 판정을 못 해" in w for w in r["warnings"])


def test_v1_link_unknown_time_label_without_time_still_tp(tmp_path):
    """라벨도 시각이 없어 범위 판정 없이 넣었으면 엔진 쪽도 그 라벨을 따라 범위 안으로 본다."""
    lb, _, _ = S.load_labels(write_labels(tmp_path / "l.csv", [(1, "", 1500, "direct")]))
    r = S.score(lb, _v1([rx(1, "direct", 1500)]), start_ms=0, end_ms=60000)
    assert r["binary"]["tp"] == 1 and r["counts"]["link_reactions_no_time"] == 0


SHIFT_NOTE_A = "합성닉네임님 호명"
SHIFT_NOTE_B = "합성닉네임B 13:42"


def _shifted_csv(tmp_path):
    p = tmp_path / "shift.csv"
    p.write_text("chat_id,chat_vod_ms,utterance_vod_ms,grade,note\n"
                 f"{cid(1)},1000,직접,{SHIFT_NOTE_A}\n"          # utterance_vod_ms 칸을 빼먹음
                 f"{cid(2)},20000,21000,direct,정상\n"
                 f"{cid(3)},1000,{SHIFT_NOTE_B},direct\n", encoding="utf-8")   # note 가 시각 칸으로 밀림
    return str(p)


def test_bad_row_warnings_do_not_contain_cell_values(tmp_path):
    lb, w, _ = S.load_labels(_shifted_csv(tmp_path))
    assert [x["chat_id"] for x in lb] == [cid(2)]
    joined = "\n".join(w)
    assert "합성닉네임" not in joined and "13:42" not in joined and "직접" not in joined
    assert "2행: grade 칸" in joined and "4행: utterance_vod_ms 칸" in joined


def test_bad_row_values_not_in_cli_stdout_or_report(tmp_path):
    out = tmp_path / "rep.json"
    p = _run_cli("score", "--labels", _shifted_csv(tmp_path), "--link", _write_link(tmp_path), "--out", str(out))
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    so = p.stdout.decode("utf-8")
    raw = out.read_text(encoding="utf-8")
    for s in (so, raw):
        assert "합성닉네임" not in s and "13:42" not in s


def test_unknown_values_not_in_messages():
    with pytest.raises(ValueError) as e:
        S.normalize_grade("비밀닉")
    assert "비밀닉" not in str(e.value)
    with pytest.raises(ValueError) as e:
        S.parse_ms_cell("비밀닉 1:2")
    assert "비밀닉" not in str(e.value)


def test_comment_row_before_header_is_skipped(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text(f"# 손라벨 메모\nchat_id,chat_vod_ms,utterance_vod_ms,grade,note\n{cid(1)},10000,11000,direct,\n",
                 encoding="utf-8-sig")
    lb, _, _ = S.load_labels(str(p))
    assert [x["chat_id"] for x in lb] == [cid(1)]


def test_player_time_v1_link_without_offset_refused(tmp_path):
    rows = [(1, 10000, 12500, "direct")]
    with pytest.raises(ValueError, match="offset-ms"):
        S.run(write_labels(tmp_path / "l.csv", rows), _write_link(tmp_path, _v1([rx(1, "direct", 11500)])),
              label_time="player")
    rep = S.run(write_labels(tmp_path / "l.csv", rows), _write_link(tmp_path, _v1([rx(1, "direct", 11500)])),
                label_time="player", offset_ms=1000)
    assert rep["reaction_time_error"]["median_ms"] == 0


def test_player_time_piecewise_calibration_refused(tmp_path):
    doc = link_doc(calibration={"offset_ms": 1000, "method": "piecewise_linear", "file_start_in_vod_ms": 0,
                                "status": "verified"})
    with pytest.raises(ValueError, match="piecewise_linear"):
        S.run(write_labels(tmp_path / "l.csv"), _write_link(tmp_path, doc), label_time="player")
    # vod 축 라벨이면 보정을 쓰지 않으므로 그대로 된다
    assert S.run(write_labels(tmp_path / "l.csv"), _write_link(tmp_path, doc))["binary"]["tp"] == 3


def test_cli_project_reports_nickname_exposure(tmp_path):
    proj = tmp_path / "p0"
    (proj / "chat").mkdir(parents=True)
    write_messages(proj / "chat" / "messages.jsonl")
    (proj / "labels").mkdir()
    with open(proj / "labels" / "nickname-exposure.jsonl", "w", encoding="utf-8") as f:
        for a, b in ((0, 90000), (500000, 600000)):
            f.write(json.dumps({"event": "view.exposed", "what": "nickname_raw", "tool": "chat_window",
                                "window_start_ms": a, "window_end_ms": b}) + "\n")
    out = tmp_path / "score.json"
    p = _run_cli("score", "--labels", write_labels(tmp_path / "l.csv"), "--link", _write_link(tmp_path),
                 "--project", str(proj), "--out", str(out))
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    ex = json.loads(out.read_text(encoding="utf-8"))["nickname_exposure"]
    assert ex["sessions_total"] == 2 and ex["sessions_overlapping_scope"] == 1
    assert "원 닉네임 노출 기록 2건" in p.stdout.decode("utf-8")


def test_no_exposure_log_is_zero_and_messages_only_is_none(tmp_path):
    rep = S.run(write_labels(tmp_path / "l.csv"), _write_link(tmp_path),
                exposure_log_path=str(tmp_path / "none.jsonl"))
    assert rep["nickname_exposure"]["sessions_total"] == 0 and rep["nickname_exposure"]["log_present"] is False
    assert S.run(write_labels(tmp_path / "l.csv"), _write_link(tmp_path))["nickname_exposure"] is None
