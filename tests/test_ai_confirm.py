"""AI 확인 큐 (08 D-29 3, 06 6-4 E3) — app/ai_confirm.py · 나란히 보기 필터 · AI 비교 보고서 줄 · 해제 당일 요약 칸 · main.py 인자.

합성 프로젝트(tests.test_final_set.proj: 채팅 6개, 1차·재라벨 잠김, 조정 3건)와 합성 엔진 결과만 쓴다. 실제 시범 폴더·
실제 채팅·닉네임·전사문은 쓰지 않는다. 기대값은 손으로 계산했다(식은 주석에).
"""
import builtins
import io
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app import ai_compare as ac  # noqa: E402
from app import ai_confirm as acf  # noqa: E402
from app import seal  # noqa: E402
from tests.test_ai_compare import ai_doc, ai_rx  # noqa: E402
from tests.test_final_set import _write_adjudications, cid, proj  # noqa: E402,F401

RUN = "run-synth-a"
SECRET_TEXT = "합성 본문"        # 합성 채팅 본문 — 판정 파일·보고서·화면에 나오면 안 된다
SECRET_NICK = "시**"
SECRET_REASON = "합성 근거"


def _engine_bytes(grades=("direct", "likely", "direct", "likely", "direct", "direct")):
    """채팅 1~6 의 엔진 판정(합성). 시각은 채팅 시각(100초 + i×10초) + 1.5초."""
    rx = [ai_rx(i, g, 101_500 + i * 10_000) if g != "unconfirmed" else ai_rx(i, g) for i, g in enumerate(grades, 1)]
    return json.dumps(ai_doc(reactions=rx), ensure_ascii=False).encode("utf-8")


def _seal_only(root, data=None):
    return seal.seal_bytes(root, data or _engine_bytes(), source="file", by="L01")


def _unseal(root, monkeypatch, e=None, data=None):
    monkeypatch.setattr(seal, "_now_iso", lambda now=None: "2026-10-30T09:00:00.000+09:00")
    e = e or _seal_only(root, data)
    return seal.unseal(root, e.sha256, by="L01", early_reason="합성 시험: AI 확인 큐")


def _add_messages(root, ns):
    """사람 라벨이 없는 합성 채팅을 더한다(시각 100초 + i×10초, proj 의 채팅과 같은 모양)."""
    from app import jsonl
    for i in ns:
        jsonl.append(os.path.join(root, "chat", "messages.jsonl"),
                     {"chat_id": cid(i), "t_ms": 100_000 + i * 10_000, "server_time_ms": i, "type_code": 1,
                      "kind": "text", "status": "normal", "viewer_id": "vw_aaaaaaaaaaaa", "is_anonymous": False,
                      "nickname_masked": SECRET_NICK, "role": "common_user", "text": SECRET_TEXT, "flags": {},
                      "raw_ref": f"f:1:{i}"})


# ----------------------------------------------------------------------------- 순수: 큐 규칙
def _row(i, grade, *, t=None, source="label", effective=True, conflict=False, lenient=True):
    unconf = grade is None
    return {"label_kind": "reaction", "chat_id": cid(i), "chat_t_ms": t, "source": source, "effective": effective,
            "conflict": conflict, "result": "unconfirmed" if unconf else "graded", "grade": None if unconf else grade,
            "text": SECRET_TEXT, "nickname_masked": SECRET_NICK, "rationale": SECRET_REASON,
            "eval": {"lenient": lenient, "strict": lenient, "excluded_reasons": []}}


def _run(rows):
    return ai_doc(reactions=rows)


def test_queue_rule_e3_plus_e1_false_positive_and_order():
    """큐 = 엔진 direct·likely 이고 사람 최종 세트에 없거나 반응 미확인. 사람이 등급을 매긴 것(관련성만 포함)은 빠진다."""
    run = _run([ai_rx(1, "direct", 1), ai_rx(2, "likely", 2), ai_rx(3, "direct", 3), ai_rx(4, "related_only", 4),
                ai_rx(5, "unclear", 5), ai_rx(6, "unconfirmed"), ai_rx(7, "direct", 7), ai_rx(8, "likely", 8),
                ai_rx(9, "direct", 9), ai_rx(10, "direct", 10), ai_rx(10, "unconfirmed"),        # 중복: 첫 줄
                ai_rx(11, "unconfirmed"), ai_rx(11, "direct", 11)])                              # 중복: 첫 줄(미확인)
    rows = [_row(1, "direct", t=10_000),                 # 사람 참 → 큐 밖
            _row(2, None, t=20_000),                     # E1 거짓 양성(사람 미확인) → 큐
            _row(3, "related_only", t=30_000),           # 사람이 다른 등급 → 큐 밖(분모에만)
            _row(7, "likely", t=70_000, effective=False),  # 무효 행은 없는 것 → 큐
            _row(8, None, t=80_000), _row(8, "direct", t=80_000, source="adjudication"),   # 조정 값이 앞선다 → 큐 밖
            _row(9, "unclear", t=90_000, lenient=False)]  # 평가에서 빠졌지만 사람 값 있음 → 큐 밖
    q = acf.queue_items(run, rows, None, chat_times={cid(10): 5_000, cid(7): 70_000})
    assert [it.chat_id for it in q] == [cid(10), cid(2), cid(7)]          # 시각 순: 10(5초) · 2(20초) · 7(70초)
    assert [it.human for it in q] == [acf.H_ABSENT, acf.H_UNCONFIRMED, acf.H_ABSENT]
    assert [it.grade for it in q] == ["direct", "likely", "direct"] and all(it.pending for it in q)
    # 판정 뒤 대기에서 빠진다(큐에는 남는다 — 되돌릴 수 있게)
    q2 = acf.queue_items(run, rows, {cid(2): "no", cid(1): "yes", cid(99): "yes", cid(7): "bogus"})
    assert [(it.chat_id, it.verdict) for it in q2] == [(cid(2), "no"), (cid(7), None), (cid(10), None)]
    c = acf.counts(q2)
    assert c == {"total": 3, "pending": 2, "yes": 0, "no": 1, "unsure": 0, "done": 1}
    assert acf.progress_text(q2) == "진행 1 / 3 · 맞음 0 · 아님 1 · 모름 0"
    # 시각을 모르는 건은 뒤로, export.build_final_set 의 item({set,row})은 set = final 만 사람 행
    items = [{"set": "excluded", "row": _row(1, "direct")}, {"set": "final", "row": _row(2, "direct")}]
    q3 = acf.queue_items(_run([ai_rx(1, "direct", 1), ai_rx(2, "direct", 2)]), items)
    assert [it.chat_id for it in q3] == [cid(1)] and q3[0].t_ms is None


def test_play_window_and_next_pending():
    it = acf.QueueItem(cid(1), 100_000, "direct", reaction_time_ms=104_000, utterance_end_ms=106_000)
    assert it.play_window() == (95_000, 109_000)                 # 채팅 − 5초 ~ 발화 끝 + 3초
    assert acf.QueueItem(cid(1), 3_000, "likely").play_window() == (0, 6_000)   # 0 아래로 내리지 않음, 끝 = 채팅 + 3초
    assert acf.QueueItem(cid(1), None, "likely", reaction_time_ms=50_000).play_window() == (45_000, 53_000)
    assert acf.QueueItem(cid(1), None, "likely").play_window() is None
    q = [acf.QueueItem(cid(i), i, "direct", verdict=v) for i, v in enumerate(("yes", None, "no", None), 1)]
    assert acf.next_pending_index(q, None) == 1 and acf.next_pending_index(q, 1) == 3
    assert acf.next_pending_index(q, 3) == 1                     # 뒤에 없으면 앞에서
    assert acf.next_pending_index([acf.QueueItem(cid(1), 1, "direct", verdict="yes")], 0) is None
    assert acf.next_pending_index([], None) is None


def test_summarize_precision_formula_hand_calc():
    """엔진 양성 8: 사람 참 3(1·2·3), 사람 다른 등급 1(4), 큐 4(5 미확인 · 6·7·8 라벨 없음).
    판정: 5 맞음 · 6 아님 · 7 모름 · 8 대기 → (3 + 1) / (8 − 1 − 1) = 4/6."""
    run = _run([ai_rx(i, "direct" if i % 2 else "likely", i) for i in range(1, 9)] + [ai_rx(9, "unconfirmed")])
    rows = [_row(1, "direct"), _row(2, "likely"), _row(3, "direct"), _row(4, "related_only"), _row(5, None),
            _row(9, "direct")]
    s = acf.summarize(run, rows, {cid(5): "yes", cid(6): "no", cid(7): "unsure", cid(1): "no"})
    assert (s["engine_positive"], s["human_true"], s["human_other"], s["queue"]) == (8, 3, 1, 4)
    assert (s["pending"], s["yes"], s["no"], s["unsure"], s["outside_queue"]) == (1, 1, 1, 1, 1)
    assert (s["queue_human_unconfirmed"], s["queue_no_human_label"]) == (1, 3)
    assert s["precision_lower"] == pytest.approx(3 / 8) and s["precision_confirmed"] == pytest.approx(4 / 6)
    assert acf.summary_line(s) == ("AI 확인 큐 판정: 대기 1 · 맞음 1 · 아님 1 · 모름 1 · 정밀도(확인 뒤) 0.667 = "
                                   "(사람 확인된 참 3 + 맞음 1) / (엔진 양성 8 − 모름 1 − 대기 1)")
    assert acf.summary_cells(s) == ("1·1·1·1", "0.667")
    # 판정 파일 없음 → '판정 없음', 정밀도(확인 뒤) 없음
    s0 = acf.summarize(run, rows, None)
    assert s0["verdict_file"] is False and s0["precision_confirmed"] is None and s0["pending"] == 4
    assert acf.summary_line(s0) == "AI 확인 큐 판정: 판정 없음 (큐 4건)" and acf.summary_cells(s0) == ("판정 없음", "-")
    # 분모 0(모두 모름) → None
    s1 = acf.summarize(_run([ai_rx(1, "direct", 1)]), [], {cid(1): "unsure"})
    assert s1["precision_confirmed"] is None and s1["verdict_file"] is True
    assert acf.summary_line({"status": "not_provided"}).endswith("내지 않음")


# ----------------------------------------------------------------------------- 저장소 (합성 프로젝트)
def test_store_write_read_schema_last_wins(proj, monkeypatch):  # noqa: F811
    root = proj["root"]
    res = _unseal(root, monkeypatch)
    assert res.run_id == RUN
    r1 = acf.append_verdict(root, RUN, cid(4), "yes", by="L01", app_version="0.0.1", reader_kind="analysis")
    r2 = acf.append_verdict(root, RUN, cid(5), "unsure", by="L01", app_version="0.0.1", reader_kind="analysis")
    r3 = acf.append_verdict(root, RUN, cid(4), "no", by="L01", app_version="0.0.1")          # 되돌리기 = 새 줄
    assert (r1["seq"], r2["seq"], r3["seq"]) == (1, 2, 3)
    assert set(r1) == {"v", "chat_id", "run_id", "verdict", "by", "at", "app_version", "seq"} and r1["v"] == 1
    path = acf.confirm_path(root, RUN)
    from app.project import open_project
    pid = open_project(root).project_id
    assert path == os.path.join(seal.project_ai_dir(root, pid), "confirm", f"{RUN}.jsonl")
    assert not os.path.abspath(path).startswith(os.path.abspath(root) + os.sep)        # 프로젝트 폴더 밖
    lines = open(path, "rb").read().decode("utf-8").splitlines()
    assert len(lines) == 3 and all(acf.check_record(json.loads(x)) == [] for x in lines)
    raw = open(path, "rb").read()
    for secret in (SECRET_TEXT, SECRET_NICK, SECRET_REASON):
        assert secret.encode("utf-8") not in raw
    assert acf.load_verdicts(root, RUN) == {cid(4): "no", cid(5): "unsure"}                # 마지막 줄이 이긴다
    assert acf.load_verdicts(root, RUN, reader_kind="analysis") == acf.load_verdicts(root, RUN, reader_kind="adjudication")
    log = acf.read_log(root, RUN)
    assert log.exists and log.last_seq == 3 and log.problems == []
    # 스키마: 칸 밖·값 밖은 거부
    assert acf.check_record({**r1, "text": "x"}) and acf.check_record({**r1, "verdict": "maybe"})
    assert acf.check_record({**r1, "seq": 0}) and acf.check_record({k: v for k, v in r1.items() if k != "by"})
    # 값 검사·그 run 의 판정에 없는 chat_id
    for kw, code in ((dict(chat_id="nope"), "chat_id_format"), (dict(verdict="maybe"), "verdict_value"),
                     (dict(by="l01"), "bad_by"), (dict(chat_id=cid(77)), "chat_not_in_run")):
        args = {"chat_id": cid(4), "verdict": "yes", **kw}
        with pytest.raises(acf.ConfirmError) as ei:
            acf.append_verdict(root, RUN, args["chat_id"], args["verdict"], by=kw.get("by", "L01"), app_version="0.0.1")
        assert ei.value.code == code
    assert len(open(path, "rb").read().splitlines()) == 3
    # 깨진 줄이 있으면 읽기는 문제로 알리고, 쓰기는 멈춘다
    with open(path, "ab") as f:
        f.write(b'{"v":1,"chat_id":"oops"}\n')
    assert acf.read_log(root, RUN).problems and acf.load_verdicts(root, RUN) == {cid(4): "no", cid(5): "unsure"}
    with pytest.raises(acf.ConfirmError) as ei:
        acf.append_verdict(root, RUN, cid(6), "yes", by="L01", app_version="0.0.1")
    assert ei.value.code == "confirm_jsonl_corrupt"


class _Spy:
    """builtins.open·io.open·os.open 에서 needle 아래 경로를 센다."""

    def __init__(self, monkeypatch, needle):
        self.hits = []
        real_b, real_os = builtins.open, os.open
        nd = os.path.normcase(os.path.abspath(needle))

        def note(p):
            try:
                s = os.path.normcase(os.path.abspath(os.fspath(p)))
            except TypeError:
                return
            if s.startswith(nd):
                self.hits.append(s)

        def b(file, *a, **k):
            note(file)
            return real_b(file, *a, **k)

        def o(path, *a, **k):
            note(path)
            return real_os(path, *a, **k)
        monkeypatch.setattr(builtins, "open", b)
        monkeypatch.setattr(io, "open", b)
        monkeypatch.setattr(os, "open", o)


def test_blind_before_unseal_and_labeling_seats(proj, monkeypatch):  # noqa: F811
    """해제 전에는 쓰지도 읽지도 않는다(판정 파일을 열기 전에). 라벨링·재라벨·출처 자리는 ai\\ 를 아예 열지 않는다."""
    root = proj["root"]
    e = _seal_only(root)
    for fn in (lambda: acf.append_verdict(root, RUN, cid(4), "yes", by="L01", app_version="0.0.1"),
               lambda: acf.load_verdicts(root, RUN), lambda: acf.read_verdicts(root, RUN, reader_kind="analysis")):
        with pytest.raises(acf.ConfirmBlindError) as ei:
            fn()
        assert ei.value.code == "not_unsealed"
    assert not os.path.exists(acf.confirm_path(root, RUN))
    ai_dir = seal.default_ai_root(root)
    spy = _Spy(monkeypatch, ai_dir)
    for kind in ("primary", "relabel_same", "relabel_second", "origin_review"):
        with pytest.raises(acf.ConfirmBlindError) as ei:
            acf.append_verdict(root, RUN, cid(4), "yes", by="L01", app_version="0.0.1", reader_kind=kind)
        assert ei.value.code == "reader_blind"
        with pytest.raises(acf.ConfirmBlindError):
            acf.load_verdicts(root, RUN, reader_kind=kind)
    with pytest.raises(acf.ConfirmBlindError):                       # 조정 세션은 읽기만
        acf.append_verdict(root, RUN, cid(4), "yes", by="L01", app_version="0.0.1", reader_kind="adjudication")
    assert spy.hits == []
    monkeypatch.undo()
    _unseal(root, monkeypatch, e)
    acf.append_verdict(root, RUN, cid(4), "yes", by="L01", app_version="0.0.1", reader_kind="analysis")
    assert acf.load_verdicts(root, RUN) == {cid(4): "yes"}
    with pytest.raises(acf.ConfirmBlindError):                       # 다른(해제 안 된) run
        acf.load_verdicts(root, "run-other")


# ----------------------------------------------------------------------------- 보고서 줄 · 해제 당일 요약 칸
def test_report_line_precision_and_no_verdicts(proj, tmp_path, monkeypatch):  # noqa: F811
    """합성: 엔진 양성 6(채팅 1~6). 사람 최종(1차 + 같은 사람 재라벨 + 조정) = 1 직접 · 2 사람 불일치(1차 높음) ·
    3 조정 직접 · 4 조정 '평가 제외' · 5 조정 '모호함' · 6 반응 미확인 → 사람 확인된 참 3(1·2·3), 큐 3(4·5 최종 세트 밖 ·
    6 미확인). 판정 4 맞음 · 5 아님 · 6 모름 → (3 + 1) / (6 − 1 − 0) = 0.8."""
    from tests.test_final_set import RS
    _write_adjudications(proj)
    root = proj["root"]
    _unseal(root, monkeypatch)
    run = ac.load_unsealed_run(root, RUN)
    out0 = str(tmp_path / "reports0")
    r0 = ac.make_report(root, run, out_root=out0, human_gate=True, created_by="L01", app_version="0.0.1",
                        relabel_session_ids=[RS])
    cf0 = r0.report["e3_confirm"]
    assert cf0["verdict_file"] is False and cf0["queue"] == 3 and cf0["precision_confirmed"] is None
    assert "AI 확인 큐 판정: 판정 없음 (큐 3건)" in r0.markdown
    for v, i in (("yes", 4), ("no", 5), ("unsure", 6)):
        acf.append_verdict(root, RUN, cid(i), v, by="L01", app_version="0.0.1", reader_kind="analysis")
    r1 = ac.make_report(root, run, out_root=str(tmp_path / "reports1"), human_gate=True, created_by="L01",
                        app_version="0.0.1", relabel_session_ids=[RS])
    cf = r1.report["e3_confirm"]
    assert (cf["engine_positive"], cf["human_true"], cf["queue"]) == (6, 3, 3)
    assert (cf["pending"], cf["yes"], cf["no"], cf["unsure"]) == (0, 1, 1, 1)
    assert cf["precision_confirmed"] == pytest.approx(0.8) and cf["precision_lower"] == pytest.approx(0.5)
    md = open(os.path.join(r1.out_dir, "report.md"), encoding="utf-8").read()
    assert "AI 확인 큐 판정: 대기 0 · 맞음 1 · 아님 1 · 모름 1 · 정밀도(확인 뒤) 0.800" in md
    doc = json.load(open(os.path.join(r1.out_dir, "report.json"), encoding="utf-8"))
    assert doc["e3_confirm"]["precision_confirmed"] == pytest.approx(0.8)
    csv = open(os.path.join(r1.out_dir, "tables", "e3_confirm.csv"), encoding="utf-8-sig").read().splitlines()
    assert csv[0].startswith("verdict_file,engine_positive,human_true,human_other,queue,pending,yes,no,unsure")
    assert csv[1].startswith("true,6,3,0,3,0,1,1,1")
    blob = b"".join(open(os.path.join(dp, n), "rb").read() for dp, _, ns in os.walk(r1.out_dir) for n in ns)
    for secret in (SECRET_TEXT, SECRET_NICK, SECRET_REASON):
        assert secret.encode("utf-8") not in blob
    # 해제 당일 요약 칸: report_numbers → 표 두 칸
    from tools import pilot_unseal_day as day
    n = day.report_numbers(r1.report)
    assert n["e3_confirm"]["precision_confirmed"] == pytest.approx(0.8)
    assert acf.summary_cells(n["e3_confirm"]) == ("0·1·1·1", "0.800")
    assert acf.summary_cells(day.report_numbers(r0.report)["e3_confirm"]) == ("판정 없음", "-")
    # 엔진이 반응을 내지 않으면 not_provided
    from tests.test_ai_compare import unsealed_run
    assert ac.compare(unsealed_run(ai_doc()), {"items": []})["e3_confirm"] == {"status": "not_provided"}
    assert ac.compare(run, {"items": []})["e3_confirm"]["verdict_file"] is False        # 판정을 주지 않으면 '판정 없음'


def test_pilot_day_summary_table_has_confirm_columns():
    """tools/pilot_unseal_day 요약 3장 표: '확인 큐 대기·맞음·아님·모름' · '정밀도(확인 뒤)' 칸(숫자만)."""
    from tools import pilot_unseal_day as day
    run_row = {"label": "v0.3", "run_id": RUN, "report_id": "R-20261030-abcd", "pre_sealed": False,
               "e1_lenient": {"n": 1, "exact": 1.0, "kappa_w": None, "precision": 1.0, "recall": 1.0, "f1": 1.0,
                              "kappa": None},
               "e1_strict": {"n": 0, "kappa_w": None, "f1": None}, "e2_lenient": {"n": 1, "recall": 0.5}, "e3": 3,
               "e3_confirm": {"verdict_file": True, "pending": 1, "yes": 2, "no": 0, "unsure": 0,
                              "precision_confirmed": 0.9},
               "segments": {"time_agreement": None, "kappa": None}}
    s = {"date": "20261030", "tool": day.TOOL, "tool_version": day.TOOL_VERSION, "user_clicks": 0, "elapsed_s": 1.0,
         "freeze": {"state": "ok", "diffs": 0},
         "conditions": {"met": True, "labeling_sessions": 1, "locked": 1, "reason_kinds": [], "projects": []},
         "projects": [{"name": "pilot-1", "unseal": {}, "compare": {"runs": [run_row], "pairs": []}}]}
    md = day.render_markdown(s)
    head = next(x for x in md.splitlines() if x.startswith("| 프로젝트 | 판 | run |"))
    assert "확인 큐 대기·맞음·아님·모름 | 정밀도(확인 뒤)" in head
    row = next(x for x in md.splitlines() if x.startswith("| pilot-1 | v0.3 |"))
    assert "| 1·2·0·0 | 0.900 |" in row
    sep = md.splitlines()[md.splitlines().index(head) + 1]
    assert sep.count("---") == head.count("|") - 1
    run_row["e3_confirm"] = {"verdict_file": False}
    assert "| 판정 없음 | - |" in day.render_markdown(s)


# ----------------------------------------------------------------------------- 화면 (나란히 보기 필터)
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def _view_fixture():
    """엔진 v2 모양 6건: 1 사람 같음(직접) · 2 큐(사람 없음, 20초) · 3 사람 관련성만 · 4 큐(사람 미확인, 40초) ·
    5 엔진 미확인 · 6 큐(사람 없음, 60초)."""
    from tests.test_analysis_view import eng, hum, run_of
    run = run_of([eng(1, "direct", 10_000), eng(2, "likely", 20_000), eng(3, "direct", 30_000),
                  eng(4, "direct", 40_000), eng(5, None, 50_000), eng(6, "likely", 60_000)])
    rows = [hum(1, "direct", 10_000), hum(3, "related_only", 30_000), hum(4, None, 40_000)]
    return run, rows


class FakeConfirm:
    """ConfirmStore 대신(파일 없음)."""

    def __init__(self, verdicts=None, fail_save=False):
        self.verdicts = dict(verdicts or {})
        self.saved, self.loads, self.fail_save = [], [], fail_save

    def load(self, rid):
        self.loads.append(rid)
        return dict(self.verdicts)

    def save(self, rid, chat_id, verdict):
        if self.fail_save:
            raise acf.ConfirmError("x", code="seal_busy")
        self.saved.append((rid, chat_id, verdict))
        return {}


def _av(qapp, fc=None, **kw):
    from app.ui import analysis_view as av
    run, rows = _view_fixture()
    fc = fc or FakeConfirm()
    v = av.AnalysisView(run=run, final_rows=rows, confirm_loader=fc.load, confirm_writer=fc.save, **kw)
    return av, v, fc


def test_view_filter_buttons_save_advance_loop(qapp):
    av, v, fc = _av(qapp)
    assert av.FILTER_KO[av.FILTER_CONFIRM] == "AI 확인 큐" and v.filter_box.findData(av.FILTER_CONFIRM) >= 0
    assert v.confirm_box.isHidden() and not v.is_confirm_mode()
    loops, seeks = [], []
    v.loopRequested.connect(lambda a, b: loops.append((a, b)))
    v.seekRequested.connect(seeks.append)
    v.filter_box.setCurrentIndex(v.filter_box.findData(av.FILTER_CONFIRM))     # 필터 전환(클릭 한 번)
    assert v.is_confirm_mode() and not v.confirm_box.isHidden() and fc.loads == [RUN]
    assert [it.chat_id for it in v.visible_items()] == [cid(2), cid(4), cid(6)]
    # 줄: 시각 · 엔진 등급 · 판정만 (사람 값·비교 결과 없음)
    texts = [v.list.item(i).text() for i in range(v.list.count())]
    assert texts == ["00:00:20.0  엔진 높음  [대기]", "00:00:40.0  엔진 직접  [대기]", "00:01:00.0  엔진 높음  [대기]"]
    # 첫 대기 건을 바로 고르고 반복 재생 범위를 보낸다(채팅 − 5초 ~ 발화 끝 + 3초)
    assert v.current_item().chat_id == cid(2) and seeks[-1] == 20_000
    assert loops[-1] == (15_000, 20_000 + 1500 + 800 + 3000)
    assert v.confirm_progress_text() == "진행 0 / 3 · 맞음 0 · 아님 0 · 모름 0"
    assert all(b.isEnabled() for b in v.confirm_buttons.values())
    # [반응 맞음 1] 단추 → 저장 · 다음 대기 건 · 그 범위 반복
    v.confirm_buttons[acf.YES].click()
    assert fc.saved == [(RUN, cid(2), "yes")] and v.current_item().chat_id == cid(4)
    assert loops[-1] == (35_000, 40_000 + 1500 + 800 + 3000)
    assert v.list.item(0).text().endswith("[맞음]")
    # 숫자 키 2 (단축키) → 아님, 다음 6
    v.confirm_shortcuts["2"].activated.emit()
    assert fc.saved[-1] == (RUN, cid(4), "no") and v.current_item().chat_id == cid(6)
    # 한글 입력 상태처럼 단축키로 잡히지 않은 키 누름 — 키 위치(VK 0x33 = '3')로 되살린다
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    ev = QKeyEvent(QEvent.KeyPress, 0, Qt.NoModifier, 0x04, 0x33, 0, "")
    QApplication.sendEvent(v.list, ev)
    assert fc.saved[-1] == (RUN, cid(6), "unsure")
    assert v.confirm_progress_text() == "진행 3 / 3 · 맞음 1 · 아님 1 · 모름 1"
    assert "모두 판정" in v.confirm_msg.text() and v.current_item().chat_id == cid(6)
    # 되돌리기: 앞 건을 다시 고르고 다른 단추 → 새 줄(저장 한 번 더), 진행 줄 갱신
    assert v.select_chat(cid(2)) and loops[-1][0] == 15_000
    v.judge(acf.NO)
    assert fc.saved[-1] == (RUN, cid(2), "no") and len(fc.saved) == 4
    assert v.confirm_progress_text() == "진행 3 / 3 · 맞음 0 · 아님 2 · 모름 1"
    assert [q.verdict for q in v.confirm_queue()] == ["no", "no", "unsure"]
    # 다른 필터로 가면 판정 칸·숫자 키가 꺼진다
    v.set_filter(av.FILTER_ALL)
    assert v.confirm_box.isHidden() and not v.confirm_shortcuts["1"].isEnabled()
    assert v.judge(acf.YES) is False and len(fc.saved) == 4
    assert not any(SECRET_TEXT in t or SECRET_NICK in t for t in v.all_texts())


def test_view_reads_verdicts_and_save_failure_stays(qapp):
    av, v, fc = _av(qapp, FakeConfirm({cid(2): "yes", cid(99): "no"}, fail_save=True))
    v.set_filter(av.FILTER_CONFIRM)
    assert v.current_item().chat_id == cid(4)                  # 2 는 이미 맞음 → 첫 대기 건은 4
    assert v.confirm_progress_text() == "진행 1 / 3 · 맞음 1 · 아님 0 · 모름 0"
    assert v.judge(acf.YES) is False and "seal_busy" in v.confirm_msg.text()
    assert v.current_item().chat_id == cid(4) and v.confirm_counts()["pending"] == 2
    # 저장 함수가 없으면 단추가 꺼지고 까닭 한 줄
    run, rows = _view_fixture()
    w = av.AnalysisView(run=run, final_rows=rows)
    w.set_filter(av.FILTER_CONFIRM)
    assert not any(b.isEnabled() for b in w.confirm_buttons.values()) and "저장할 수 없습니다" in w.confirm_msg.text()
    assert w.judge(acf.YES) is False


def test_view_confirm_rows_runs_mode_and_sealed(qapp):
    from tests.test_analysis_view import CHOICES, FakeLoader, eng, hum, run_named
    from app.ui import analysis_view as av
    run, rows = _view_fixture()
    # confirm_rows(평가에서 빠진 사람 행 포함)가 있으면 그것으로: 6 에 사람 값이 있으면 큐 밖
    fc = FakeConfirm()
    v = av.AnalysisView(run=run, final_rows=rows, confirm_rows=rows + [hum(6, "direct", 60_000, lenient=False)],
                        confirm_loader=fc.load, confirm_writer=fc.save)
    v.set_filter(av.FILTER_CONFIRM)
    assert [it.chat_id for it in v.visible_items()] == [cid(2), cid(4)]
    # 판끼리 견주는 중에 큐를 고르면 사람 최종과 견주기로 돌아간다
    new = run_named(RUN, "0.3.0", [eng(2, "likely", 20_000), eng(4, "direct", 40_000)])
    old = run_named("run-old", "0.2.0", [eng(2, None, 20_000)])
    choices = [{"run_id": RUN, "label": RUN}, CHOICES[1]]
    v2 = av.AnalysisView(run=new, final_rows=rows, runs=choices, run_loader=FakeLoader({"run-old": old}),
                         confirm_loader=fc.load, confirm_writer=fc.save)
    assert v2.compare_with("run-old") and v2.is_runs_mode() and v2.confirm_queue() == []
    v2.set_filter(av.FILTER_CONFIRM)
    assert not v2.is_runs_mode() and v2.is_confirm_mode() and [q.chat_id for q in v2.confirm_queue()] == [cid(2), cid(4)]
    # 판 A 를 바꾸면 그 판의 판정을 읽고 큐를 다시 만든다
    assert v2.select_run("run-old") and fc.loads[-1] == "run-old" and v2.confirm_queue() == []
    assert "확인할 건이 없습니다" in v2.confirm_msg.text()
    # 봉인으로 바꾸면 큐·판정 칸이 사라진다
    v2.set_data(run=None, sealed_count=1)
    assert v2.confirm_box.isHidden() and v2.confirm_queue() == [] and not any(RUN in t for t in v2.all_texts())


def test_view_confirm_opens_no_files(qapp, monkeypatch):
    opened = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: opened.append(str(f)) or real_open(f, *a, **k))
    av, v, fc = _av(qapp)
    v.set_filter(av.FILTER_CONFIRM)
    v.judge(acf.YES)
    v.judge(acf.NO)
    assert opened == [] and len(fc.saved) == 2


def test_confirm_view_is_freed_when_dropped(qapp):
    """판정 단추·숫자 키·eventFilter 를 건 화면도 이름이 사라지면 지워진다(D-18 10 — 자기를 잡는 연결 없음)."""
    import gc
    import weakref

    def make():
        av, v, _fc = _av(qapp)
        v.set_filter(av.FILTER_CONFIRM)
        v.confirm_buttons[acf.YES].click()
        return weakref.ref(v)
    r = make()
    gc.collect()
    assert r() is None


# ----------------------------------------------------------------------------- 메인 창: 한 번에 열기 · main.py 인자
def test_main_parse_args_project_analysis_confirm():
    import main
    a = main.parse_args(["--project", "C:\\x\\pilot-1", "--confirm"])
    assert a["project"] == "C:\\x\\pilot-1" and a["confirm"] and a["analysis"] and a["flags"] == set()
    assert main.start_request(a) == {"project": os.path.abspath("C:\\x\\pilot-1"), "analysis": True, "confirm": True}
    b = main.parse_args(["--project", "C:\\y", "--analysis", "--updated"])
    assert b["analysis"] and not b["confirm"] and b["flags"] == {"--updated"}
    assert main.start_request(main.parse_args([])) is None
    c = main.parse_args(["--data-dir", "D:\\d"])
    assert c["project"] is None and c["data_dir"] == "D:\\d" and not c["analysis"]


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    from app.ui import workbench as wbm
    from tests.test_integration_c4 import PARTS
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (PARTS[kind], None))
    data_root = str(tmp_path / "appdata")
    os.makedirs(data_root, exist_ok=True)
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    w = mw.MainWindow(SPEC, inst, data_root, str(tmp_path / "log.txt"), None, [], set())
    w.check_updates = lambda force=False, on_start=False: None
    w.shown = []
    w.inform = lambda title, text: w.shown.append(("info", text))
    w.warn = lambda title, text: w.shown.append(("warn", text))
    yield w
    w.close()


def _labeler(win, lid="L01"):
    from app import session as sess
    sess.add_labeler(win.data_root, lid)
    win.settings.setValue("session/last_labeler", lid)


def test_open_analysis_confirm_one_click_flow(win, proj, tmp_path, monkeypatch):  # noqa: F811
    """--project … --confirm 과 같은 길: 분석 세션(새로) → 작업대 일이 끝나면 나란히 보기 'AI 확인 큐' → 첫 대기 건 반복 →
    1·2·3 저장(confirm\\<run_id>.jsonl) → 보고서 줄.
    합성: 채팅 7·8 은 사람 라벨 없음. 엔진 양성 8(1~8), 사람 최종(1차만) = 1 직접 · 2·3 높음 · 4·5 관련성만 · 6 미확인 →
    사람 확인된 참 3, 큐 3(6 미확인 · 7·8 없음). 판정 6 맞음 · 7 아님 · 8 모름 → (3 + 1) / (8 − 1 − 0) = 4/7."""
    from app.ui import analysis_view as av
    from app.ui import workbench as wbm
    root = proj["root"]
    _add_messages(root, (7, 8))
    _unseal(root, monkeypatch, data=_engine_bytes(("direct", "likely", "direct", "likely", "direct", "direct",
                                                   "likely", "direct")))
    _labeler(win)
    loops = []
    monkeypatch.setattr(wbm.Workbench, "loop_range", lambda self, a, b: loops.append((a, b)) or True)
    stops = []
    monkeypatch.setattr(wbm.Workbench, "stop_range_loop", lambda self: stops.append(1))
    assert win.open_analysis(root, confirm=True, show=False) is None
    wb = win.workbench
    assert wb is not None and wb.session_kind == "analysis" and wb.session.labeler_id == "L01"
    assert wb.wait_jobs(15_000)
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.processEvents()
    dlg = win.side_view_dialog
    assert dlg is not None, win.last_open_block
    v = dlg.view
    assert v.filter_mode() == av.FILTER_CONFIRM and v.current_run_id() == RUN
    assert [q.chat_id for q in v.confirm_queue()] == [cid(6), cid(7), cid(8)]
    assert [q.t_ms for q in v.confirm_queue()] == [160_000, 170_000, 180_000]    # 사람 라벨 없는 건도 채팅 시각
    assert v.current_item().chat_id == cid(6)
    assert loops[-1] == (155_000, 161_500 + 3000)                # 채팅 160초 − 5초 ~ 반응 161.5초 + 3초
    v.confirm_shortcuts["1"].activated.emit()
    assert loops[-1] == (165_000, 171_500 + 3000)                # 자동으로 다음 대기 건(7)
    v.confirm_buttons[acf.NO].click()
    v.judge(acf.UNSURE)
    assert loops[-1] == (175_000, 181_500 + 3000) and v.confirm_counts()["pending"] == 0
    assert acf.load_verdicts(root, RUN) == {cid(6): "yes", cid(7): "no", cid(8): "unsure"}
    lines = open(acf.confirm_path(root, RUN), encoding="utf-8").read().splitlines()
    assert [json.loads(x)["by"] for x in lines] == ["L01"] * 3
    dlg.show()
    dlg.close()
    assert stops                                                  # 창을 닫으면 반복을 끈다
    win.show_home()
    # 보기 전용에서 AI 비교 보고서 → 'AI 확인 큐 판정' 줄
    from tests.test_analysis_menu import _open
    _open(win, root, None)
    rep = win.ai_compare_dialog(out_root=str(tmp_path / "보고서"), gate=True)
    cf = rep.report["e3_confirm"] if rep is not None else {}
    assert (cf.get("engine_positive"), cf.get("human_true"), cf.get("queue")) == (8, 3, 3)
    assert cf["precision_confirmed"] == pytest.approx(4 / 7)
    assert "정밀도(확인 뒤) 0.571 = (사람 확인된 참 3 + 맞음 1) / (엔진 양성 8 − 모름 1 − 대기 0)" in rep.markdown
    win.show_home()


def test_open_analysis_resumes_existing_and_loop_range(win, proj, monkeypatch):  # noqa: F811
    """이어 쓸 분석 세션이 있으면 그것으로. Workbench.loop_range 는 확인 재생기로 A-B 반복(영상이 없으면 위치만)."""
    root = proj["root"]
    _unseal(root, monkeypatch)
    _labeler(win)
    assert win.open_analysis(root, show=False) is None
    sid = win.workbench.session.session_id
    assert win.workbench.wait_jobs(15_000)
    wb = win.workbench
    assert wb.loop_range(10_000, 20_000) is False and wb.review is None         # 영상 없음: 위치만
    wb.media_path = "합성.mp4"
    assert wb.loop_range(10_000, 20_000) is True
    assert wb.review.window() == (10_000, 20_000) and wb.review.loop_on()
    assert ("play",) in wb.player.calls
    wb.stop_range_loop()
    assert not wb.review.loop_on()
    wb.media_path = None
    win.show_home()
    assert win.open_analysis(root, show=False) is None
    assert win.workbench.session.session_id == sid                            # 같은 분석 세션을 이어 쓴다
    assert win.workbench.wait_jobs(15_000)
    win.show_home()


def test_open_analysis_pilot_blocked_until_locked(win, tmp_path, monkeypatch):
    """시범 프로젝트(pilot-*)는 조건 채운 해제 기록이 없으면 까닭 한 줄만 보이고 프로젝트를 열지 않는다(D-27 3)."""
    from app import project
    from tests.test_final_set import NOW, SALT
    p = project.create(str(tmp_path / "projects"), "pilot", 4242, "시험", "L01", "0.0.1", NOW,
                       "11111111-2222-4333-8444-777777777777", SALT)
    assert os.path.basename(p.root) == "pilot-4242"
    _labeler(win)
    opened = []
    monkeypatch.setattr(win.project_view, "open_path", lambda path: opened.append(path))
    why = win.open_analysis(p.root, confirm=True, show=False)
    assert why and "시범 세션" in why and win.statusBar().currentMessage() == why
    assert opened == [] and win.workbench is None and win.side_view_dialog is None
    assert win.open_analysis(str(tmp_path / "없는 폴더"), confirm=True) and opened == []
    # 시작 요청도 같은 길
    win.start_request = {"project": p.root, "analysis": True, "confirm": True}
    win.startup_dialogs()
    assert win.start_request is None and opened == [] and "시범 세션" in (win.last_open_block or "")


def test_spec_bundles_confirm_schema():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = open(os.path.join(root, "ChatCoach.spec"), encoding="utf-8").read()
    assert os.path.isfile(os.path.join(root, *acf.SCHEMA_REL))
    assert "('schemas\\\\ai-confirm.schema.json', 'schemas')" in spec
