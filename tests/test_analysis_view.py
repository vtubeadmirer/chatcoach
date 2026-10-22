"""방송 분석 나란히 보기 시험 (app/ui/analysis_view.py, 08 D-01·D-15 7, 16 8-1 5번).

화면 없이(QT_QPA_PLATFORM=offscreen) **합성** 엔진 결과와 합성 최종 세트로만 시험한다(D-15 7: 시범 라벨이 잠기기 전).
실제 채팅·닉네임·전사문은 쓰지 않는다. 근거 칸의 글은 자리표시다.
"""
import builtins
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.rules import EVIDENCE_KEYS  # noqa: E402
from app.ui import analysis_view as av  # noqa: E402

SECRET = "SEALED-SECRET-MARKER"       # 봉인 중에는 화면 어디에도 나오면 안 되는 값
CHAT_TEXT = "CHAT-TEXT-MARKER"        # 채팅 본문·닉네임 자리 — 해제 뒤에도 보이면 안 된다


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def cid(i):
    return "c%020x" % i


def ev(present=(), at=None):
    return {k: {"present": k in present, "at_ms": at if k in present else None} for k in EVIDENCE_KEYS}


def eng(i, grade, t, *, present=("text_repeated", "within_time_window"), rt=None, conf="probably"):
    """engine/link.py v2 초안 모양의 반응 한 줄(합성)."""
    if grade is None:
        return {"chat_id": cid(i), "result": "unconfirmed", "grade": None, "reaction_time_ms": None,
                "utterance_start_ms": None, "utterance_end_ms": None, "score": None, "chat_t_ms": t,
                "confidence": None, "linked_candidate": None, "candidates": [], "rationale": "r-none"}
    rt = t + 1500 if rt is None else rt
    return {"chat_id": cid(i), "result": "graded", "grade": grade, "reaction_time_ms": rt,
            "utterance_start_ms": rt, "utterance_end_ms": rt + 800, "score": 3.3, "chat_t_ms": t,
            "confidence": conf, "linked_candidate": 0,
            "candidates": [{"asr_segment_id": 1, "utterance_start_ms": rt, "utterance_end_ms": rt + 800,
                            "tier": grade, "score": 3.3, "strong": 1, "medium": 1, "weak": 0, "evidence": ev(present, rt)}],
            "rationale": "r-engine"}


def run_of(reactions, version=2):
    return {"schema": "chatcoach/ai-result", "schema_version": version, "run_id": "run-synth-a",
            "created_at": "2026-10-22T10:00:00+09:00", "engine": {"name": "chatcoach-link", "version": "0.1.0"},
            "target": {"platform": "chzzk", "video_no": "1", "chat_id_algorithm": "sl-chat-id-v1"},
            "time_axis": "vod_ms", "reactions": reactions}


def hum(i, grade, t, *, source="label", strong=("text_repeated",), medium=("within_time_window",), rationale="r-human",
        effective=True, conflict=False, strict=True, lenient=True, reasons=()):
    """최종 세트(export-row) 반응 행(합성). text·nickname_masked 는 보이면 안 되는 자리표시."""
    return {"label_kind": "reaction", "label_id": "x", "entity_id": "y", "source": source, "effective": effective,
            "conflict": conflict, "chat_id": cid(i), "chat_t_ms": t, "text": CHAT_TEXT, "nickname_masked": CHAT_TEXT,
            "result": "unconfirmed" if grade is None else "graded", "grade": grade, "confidence": "certain",
            "reaction_time_ms": None if grade is None else t + 1600, "rationale": rationale,
            "evidence_strong": list(strong), "evidence_medium": list(medium), "evidence_weak": [],
            "eval": {"strict": strict, "lenient": lenient, "excluded_reasons": list(reasons)}}


def fixture_pair():
    """채팅 1~8: 같음(직접) · 등급 다름 · 엔진만(사람 미확인) · 엔진만(사람 없음) · 사람만(엔진 미확인) · 사람만(엔진 없음) ·
    짝 없음(엔진 미확인만) · 같음(둘 다 미확인)."""
    e = [eng(1, "direct", 10_000), eng(2, "likely", 20_000), eng(3, "related_only", 30_000), eng(4, "direct", 40_000),
         eng(5, None, 50_000), eng(7, None, 70_000), eng(8, None, 80_000)]
    h = [hum(1, "direct", 10_000), hum(2, "direct", 20_000), hum(3, None, 30_000), hum(5, "likely", 50_000),
         hum(6, "direct", 60_000), hum(8, None, 80_000)]
    return run_of(e), h


# ---------------------------------------------------------------------------- 순수 계산
def test_compare_statuses():
    run, rows = fixture_pair()
    c = av.compare(run, rows)
    st = {it.chat_id: it.status for it in c.items}
    assert st == {cid(1): av.SAME, cid(2): av.GRADE_DIFF, cid(3): av.ENGINE_ONLY, cid(4): av.ENGINE_ONLY,
                  cid(5): av.HUMAN_ONLY, cid(6): av.HUMAN_ONLY, cid(8): av.SAME}
    s = c.summary
    assert (s[av.SAME], s[av.GRADE_DIFF], s[av.ENGINE_ONLY], s[av.HUMAN_ONLY]) == (2, 1, 2, 2)
    assert s["engine_rows"] == 7 and s["human_rows"] == 6 and s["both"] == 5
    assert s["engine_unevaluated"] == 1 and s["human_missing"] == 2 and s["unpaired_no_reaction"] == 1
    assert [it.t_ms for it in c.items] == sorted(it.t_ms for it in c.items)
    # 혼동표: 엔진 줄 × 사람 칸
    assert c.confusion["likely"]["direct"] == 1
    assert c.confusion["direct"]["absent"] == 1
    assert c.confusion["absent"]["direct"] == 1
    assert c.confusion["unconfirmed"]["absent"] == 1
    assert sum(sum(r.values()) for r in c.confusion.values()) == 8


def test_duplicates_and_skips():
    e = [eng(1, "direct", 1000), eng(1, "likely", 1000)]            # 엔진 중복: 첫 줄
    h = [hum(1, "likely", 1000), hum(1, "direct", 1000, source="adjudication"),   # 조정 값이 앞선다
         hum(1, "related_only", 1000),                                # 조정 뒤 원 라벨은 덮지 못한다
         hum(2, "direct", 2000, effective=False), hum(3, "direct", 3000, conflict=True),
         {"label_kind": "segment", "chat_id": None}]
    c = av.compare(run_of(e), h)
    assert len(c.items) == 1 and c.items[0].status == av.SAME
    assert c.items[0].engine.value == "direct" and c.items[0].human.extra["source"] == "adjudication"
    assert c.summary["engine_duplicates"] == 1 and c.summary["human_duplicates"] == 2
    assert c.summary["human_skipped"] == 2


def test_label_event_record_and_final_reaction_like():
    """label-event 반응 레코드와 FinalReaction 모양(.record·.source·.reasons) 도 최종 세트로 받는다."""
    from types import SimpleNamespace
    u1, u2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    evd = {k: {"present": k in ("nickname_called", "same_topic"), "at_ms": None, "note": CHAT_TEXT} for k in EVIDENCE_KEYS}
    other = {k: {"present": k == "answer_form"} for k in EVIDENCE_KEYS}
    rec = {"kind": "reaction", "body": {"chat_id": cid(1), "result": "graded", "grade": "direct", "confidence": "certain",
                                        "rationale": "r-rec", "reaction_time_ms": 1500, "linked_utterance_id": u1,
                                        "candidates": [{"utterance_id": u1, "evidence": evd},
                                                       {"utterance_id": u2, "evidence": other}]}}
    fr = SimpleNamespace(chat_id=cid(2), record={"kind": "reaction", "body": {**rec["body"], "chat_id": cid(2),
                                                                               "grade": "likely"}},
                         source="adjudication", reasons=("origin_virtual",), report_reasons=())
    c = av.compare(run_of([eng(1, "direct", 1000), eng(2, "direct", 2000)]), [rec, fr],
                   chat_times={cid(1): 1000, cid(2): 2000})
    st = {it.chat_id: it for it in c.items}
    assert st[cid(1)].status == av.SAME and st[cid(2)].status == av.GRADE_DIFF
    h = st[cid(1)].human
    assert h.evidence["nickname_called"] == "strong" and h.evidence["same_topic"] == "weak"
    assert h.evidence["answer_form"] is None          # 연결 안 된 후보의 증거는 넣지 않는다
    assert st[cid(2)].human.extra["source"] == "adjudication"
    assert st[cid(2)].human.extra["excluded_reasons"] == ["origin_virtual"]
    assert not any(CHAT_TEXT in x for row in av.evidence_rows(st[cid(1)]) for x in row)


def test_chat_times_priority_and_missing_time():
    e = [eng(1, "direct", 5000)]
    e[0].pop("chat_t_ms")
    c = av.compare(run_of(e), [], chat_times={cid(1): 7777})
    assert c.items[0].t_ms == 7777
    c2 = av.compare(run_of(e), [])
    assert c2.items[0].t_ms is None and c2.summary["no_time"] == 1


def test_filters_and_next_diff_index():
    run, rows = fixture_pair()
    items = av.compare(run, rows).items
    assert len(av.filter_items(items, av.FILTER_ALL)) == 7
    assert all(it.status != av.SAME for it in av.filter_items(items, av.FILTER_DIFF))
    assert len(av.filter_items(items, av.FILTER_DIFF)) == 5
    assert {it.chat_id for it in av.filter_items(items, av.FILTER_MISSED)} == {cid(5), cid(6)}
    assert av.next_diff_index(items, None) == 1        # 0 은 같음
    assert av.next_diff_index(items, 5) is None        # 6 은 같음 → 끝, 돌아가지 않는다
    assert av.next_diff_index(items, None, -1) == 5
    assert av.next_diff_index([], None) is None


def test_evidence_rows_v2_and_v1():
    run, rows = fixture_pair()
    it = next(x for x in av.compare(run, rows).items if x.chat_id == cid(1))
    er = av.evidence_rows(it)
    assert len(er) == 7
    d = {name.split(" (")[0]: (e, h) for name, e, h in er}
    assert d["문구 되풀이"][0].startswith("있음 ") and d["문구 되풀이"][1] == "있음(강)"
    assert d["시간 창"][1] == "있음(중)"
    assert d["닉네임 호명"] == ("없음", "없음")
    # v1(증거 없음) · 사람만
    v1 = run_of([{k: v for k, v in eng(1, "direct", 1000).items() if k in av_v1_keys()}], version=1)
    it1 = av.compare(v1, [hum(1, "direct", 1000)]).items[0]
    assert all(e == "증거 없음" for _n, e, _h in av.evidence_rows(it1))
    it6 = next(x for x in av.compare(run, rows).items if x.chat_id == cid(6))
    assert all(e == "—" for _n, e, _h in av.evidence_rows(it6))


def av_v1_keys():
    return ("chat_id", "result", "grade", "reaction_time_ms", "utterance_start_ms", "utterance_end_ms", "score")


def test_score_rows():
    assert av.score_rows(None) == []
    rows = av.score_rows({"strict": {"n": 62, "kappa_w": 0.71234, "f1": None, "confusion": {"a": 1}},
                          "lenient": {"n": 84, "kappa_w": "held", "recall": 0.5}})
    assert rows == [("건수", "62", "84"), ("κ_w", "0.712", "보류"), ("F1", "—", "—"), ("재현율", "—", "0.500")]


# ---------------------------------------------------------------------------- 화면
def test_sealed_shows_only_count(qapp):
    rows = [hum(1, "direct", 1000, rationale=SECRET)]
    v = av.AnalysisView(run=None, final_rows=rows, sealed_count=3)
    assert v.is_sealed() and v.sealed_count() == 3
    assert v.stack.currentWidget() is v.sealed_page
    texts = v.all_texts()
    assert any("봉인됨 3건" in t for t in texts)
    assert not any(SECRET in t or cid(1) in t for t in texts)
    assert v.items() == [] and v.summary() == {}
    assert v.timeline.markers(av.ENGINE_TRACK) == [] and v.timeline.markers(av.HUMAN_TRACK) == []
    assert v.next_diff() is False and v.select_chat(cid(1)) is False


def test_unsealed_then_resealed_clears(qapp):
    e = [eng(1, "direct", 1000)]
    e[0]["rationale"] = SECRET
    v = av.AnalysisView(run=run_of(e), final_rows=[hum(1, "likely", 1000, rationale=SECRET)])
    assert not v.is_sealed()
    assert v.select_chat(cid(1))
    assert any(SECRET in t for t in v.all_texts())
    v.set_data(run=None, final_rows=[hum(1, "likely", 1000, rationale=SECRET)], sealed_count=1)
    assert v.is_sealed() and v.current_item() is None
    assert not any(SECRET in t or cid(1) in t for t in v.all_texts())
    assert v.timeline.markers(av.ENGINE_TRACK) == []


def test_chat_text_never_shown(qapp):
    run, rows = fixture_pair()
    v = av.AnalysisView(run=run, final_rows=rows)
    for i in range(len(v.visible_items())):
        v.select_index(i)
        assert not any(CHAT_TEXT in t for t in v.all_texts())


def test_tracks_list_filter_and_navigation(qapp):
    run, rows = fixture_pair()
    v = av.AnalysisView(run=run, final_rows=rows)
    assert v.list.count() == 7
    em, hm = v.timeline.markers(av.ENGINE_TRACK), v.timeline.markers(av.HUMAN_TRACK)
    assert len(em) == 6 and len(hm) == 6          # 엔진 없음 1건(6), 사람 없음 1건(4)
    assert (50_000, "an_human_only_n") in em      # 엔진 미확인 = 속이 빈 표시
    assert (50_000, "an_human_only") in hm
    assert (40_000, "an_engine_only") in em and all(t != 40_000 for t, _ in hm)
    names = [n for n, _ in v.timeline.track_rects()]
    assert names.index("marks:" + av.ENGINE_TRACK) < names.index("marks:" + av.HUMAN_TRACK)   # 엔진 위, 사람 아래

    sel, seek = [], []
    v.chatSelected.connect(sel.append)
    v.seekRequested.connect(seek.append)
    assert v.next_diff() and v.current_item().chat_id == cid(2)
    assert sel[-1] == cid(2) and seek[-1] == 20_000 and v.timeline.position == 20_000
    v.shortcuts["N"].activated.emit()
    assert v.current_item().chat_id == cid(3)
    v.shortcuts["Shift+N"].activated.emit()
    assert v.current_item().chat_id == cid(2)
    assert set(v.shortcuts) == {"N", "Shift+N"}

    v.set_filter(av.FILTER_MISSED)
    assert v.list.count() == 2 and v.filter_box.currentData() == av.FILTER_MISSED
    assert v.current_item() is None              # 고른 건(2)이 필터에 걸려 풀림
    assert len(v.timeline.markers(av.ENGINE_TRACK)) == 1 and len(v.timeline.markers(av.HUMAN_TRACK)) == 2
    assert v.next_diff() and v.current_item().chat_id == cid(5)
    assert v.next_diff() and v.current_item().chat_id == cid(6)
    assert v.next_diff() is False and v.current_item().chat_id == cid(6)

    v.filter_box.setCurrentIndex(v.filter_box.findData(av.FILTER_DIFF))
    assert v.filter_mode() == av.FILTER_DIFF and v.list.count() == 5
    assert v.current_item().chat_id == cid(6)    # 다름만에도 보이므로 그대로
    with pytest.raises(ValueError):
        v.set_filter("nope")


def test_detail_rows_and_list_click(qapp):
    run, rows = fixture_pair()
    v = av.AnalysisView(run=run, final_rows=rows)
    assert v.detail_rows() == []
    v.list.setCurrentRow(1)                       # 채팅 2: 엔진 높음 / 사람 직접
    assert v.current_item().chat_id == cid(2)
    d = {r[0]: r[1:] for r in v.detail_rows()}
    assert d["판정"] == ("높음", "직접")
    assert d["확신도"] == ("아마도", "확실함")
    assert d["근거"] == ("r-engine", "r-human")
    assert "엄격 포함" in d["평가"][1] and "참고 포함" in d["평가"][1]
    assert sum(1 for k in d if "(" in k) == 7    # 증거 7종 줄
    v.select_chat(cid(6))
    d = {r[0]: r[1:] for r in v.detail_rows()}
    assert d["판정"] == ("평가 안 함", "직접")
    v.select_chat(cid(4))
    assert {r[0]: r[1:] for r in v.detail_rows()}["판정"] == ("직접", "최종 세트에 없음")


def test_timeline_click_selects_nearest(qapp):
    run, rows = fixture_pair()
    v = av.AnalysisView(run=run, final_rows=rows, duration_ms=100_000)
    v.resize(1000, 600)
    v.timeline.resize(1000, v.timeline.height())
    v.timeline.seekRequested.emit(30_300)         # 1000px 에 100초 → 8px ≈ 800ms
    assert v.current_item().chat_id == cid(3)
    v.timeline.seekRequested.emit(35_000)         # 가까운 채팅 없음 → 그대로
    assert v.current_item().chat_id == cid(3)


def test_summary_and_scores_tables(qapp):
    run, rows = fixture_pair()
    v = av.AnalysisView(run=run, final_rows=rows)
    sr = dict(v.summary_rows())
    assert sr["같음"] == 2 and sr["사람만"] == 2 and sr["엔진 미평가 (사람에게만)"] == 1
    assert v.summary_table.rowCount() == len(v.summary_rows())
    assert "아직" in v.score_note.text() and v.score_table.rowCount() == 0
    v.set_data(run=run, final_rows=rows, scores={"strict": {"n": 5, "precision": 0.8}, "lenient": {"n": 6},
                                                 "preregistered": False})
    assert v.score_table_rows() == [("건수", "5", "6"), ("정밀도", "0.800", "—")]
    assert "개발용" in v.score_note.text()
    ci = av.CONFUSION_VALUES.index
    assert v.confusion_table.item(ci("likely"), ci("direct")).text() == "1"
    assert v.confusion_table.item(ci("absent"), ci("absent")).text() == ""


def test_view_opens_no_files(qapp, monkeypatch):
    """06 7-3: 이 화면은 파일을 직접 열지 않는다(받은 dict 만 쓴다)."""
    opened = []
    real_open = builtins.open
    real_os_open = os.open

    def spy_open(f, *a, **k):
        opened.append(str(f))
        return real_open(f, *a, **k)

    def spy_os_open(f, *a, **k):
        opened.append(str(f))
        return real_os_open(f, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    run, rows = fixture_pair()
    v = av.AnalysisView(run=None, final_rows=rows, sealed_count=2)
    v.set_data(run=run, final_rows=rows)
    v.next_diff()
    v.set_filter(av.FILTER_DIFF)
    assert opened == []
