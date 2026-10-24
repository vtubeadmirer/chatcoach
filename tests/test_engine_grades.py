"""엔진 '직접' 만 기본 표시 (08 D-31 1) — app/core/engine_grades.py 규칙과 그것을 쓰는 화면들.

- 규칙 함수(counts_as_reaction 등)·설정 읽기/쓰기(settings.ini 키 하나)·열린 화면 알림.
- 나란히 보기(app/ui/analysis_view): 기본(꺼짐)·켬의 상태 집계, AI 확인 큐 불변(D-30 1), 판끼리 견주기는 등급 그대로.
- 방송 요약(app/ui/summary_view): 엔진 판정으로 센 줄의 'direct n · likely m(흐림)' 과 반응률.
- 작업대 엔진 띠(app/ui/workbench): likely = 흐린 속 빈 회색 점, 옵션을 켜면 채운 점.
- 설정 기억: 한 화면에서 바꾸면 settings.ini 에 남고, 새로 연 화면·열린 화면이 같은 값을 쓴다.

화면 없이(QT_QPA_PLATFORM=offscreen) **합성** 엔진 결과·합성 최종 세트만 쓴다. 실제 시범 폴더·채팅·닉네임·전사문은 쓰지 않는다.
"""
import gc
import json
import os
import sys
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QWidget  # noqa: E402

from app.core import engine_grades as eg  # noqa: E402
from app.ui import analysis_view as av  # noqa: E402
from app.ui import summary_view as sv  # noqa: E402
from tests.test_analysis_view import CHOICES, FakeLoader, cid, eng, hum, run_of, runs_fixture  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def _ini(tmp_path, name="settings.ini"):
    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


def d31_pair():
    """엔진 likely 다섯 경우 + direct 하나(합성): 1 likely·사람 직접 · 2 likely·사람 높음 · 3 likely·사람 미확인 ·
    4 likely·사람 없음 · 5 likely·사람 관련성만 · 6 direct·사람 직접."""
    e = [eng(1, "likely", 10_000), eng(2, "likely", 20_000), eng(3, "likely", 30_000), eng(4, "likely", 40_000),
         eng(5, "likely", 50_000), eng(6, "direct", 60_000)]
    h = [hum(1, "direct", 10_000), hum(2, "likely", 20_000), hum(3, None, 30_000), hum(5, "related_only", 50_000),
         hum(6, "direct", 60_000)]
    return run_of(e), h


OFF = {cid(1): av.HUMAN_ONLY, cid(2): av.HUMAN_ONLY, cid(3): av.SAME, cid(5): av.HUMAN_ONLY, cid(6): av.SAME}
ON = {cid(1): av.GRADE_DIFF, cid(2): av.SAME, cid(3): av.ENGINE_ONLY, cid(4): av.ENGINE_ONLY, cid(5): av.GRADE_DIFF,
      cid(6): av.SAME}


# ---------------------------------------------------------------------------- 규칙 함수
def test_rule_functions_and_texts():
    for inc in (False, True):
        assert eg.counts_as_reaction("direct", include_likely=inc) is True
        for g in ("related_only", "unclear", "unconfirmed", None, "graded", "x"):
            assert eg.counts_as_reaction(g, include_likely=inc) is False
            assert eg.is_dimmed(g, include_likely=inc) is False
        assert eg.is_dimmed("direct", include_likely=inc) is False
    assert eg.counts_as_reaction("likely", include_likely=False) is False
    assert eg.counts_as_reaction("likely", include_likely=True) is True
    assert eg.is_dimmed("likely", include_likely=False) is True and eg.is_dimmed("likely", include_likely=True) is False
    assert eg.count_grades(["direct", "likely", "likely", "unclear", None]) == {"direct": 1, "likely": 2}
    assert eg.count_text(3, 2, include_likely=False) == "direct 3 · likely 2(흐림)"
    assert eg.count_text(3, 2, include_likely=True) == "direct 3 · likely 2"
    assert eg.rule_text(include_likely=False, likely=5) == "direct 만 · likely 5건 흐림"
    assert eg.rule_text(include_likely=True, likely=5) == "direct·likely 모두 반응 · likely 5건 포함"
    assert eg.DEFAULT_INCLUDE_LIKELY is False and eg.SETTINGS_KEY == "analysis/likely_as_reaction"
    assert eg.OPTION_LABEL == "likely 도 반응으로 보기" and "0.88" in eg.OPTION_TIP and "0.23" in eg.OPTION_TIP
    # 점수·AI 확인 큐의 '반응 있음' 정의는 그대로(보여 주는 방식만 바뀐다)
    from app import ai_confirm
    from app.core import agreement
    assert eg.POSITIVE_GRADES == ("direct", "likely") == ai_confirm.POSITIVE
    assert agreement.POSITIVE == frozenset({"direct", "likely"})


def test_parse_read_write_settings(tmp_path):
    for v in (True, "true", "1", "on", " Yes ", 1):
        assert eg.parse_setting(v) is True, v
    for v in (False, "false", "0", None, "", "nope", 2, 0.5):
        assert eg.parse_setting(v) is False, v
    s = _ini(tmp_path)
    assert eg.read_include_likely(s) is False                       # 없으면 기본(꺼짐)
    assert eg.read_include_likely(None) is False
    assert eg.write_include_likely(s, True) is True
    assert eg.read_include_likely(_ini(tmp_path)) is True           # 새 객체로 읽어도(파일에 남음)
    text = (tmp_path / "settings.ini").read_text(encoding="utf-8")
    assert "[analysis]" in text and "likely_as_reaction=true" in text
    assert eg.write_include_likely(s, False) is True and eg.read_include_likely(_ini(tmp_path)) is False
    assert eg.write_include_likely(None, True) is False             # 설정이 없으면 남기지 않는다(알리기만)

    class Broken:
        def value(self, *a):
            raise OSError("x")

        def setValue(self, *a):  # noqa: N802
            raise OSError("x")
    assert eg.read_include_likely(Broken()) is False and eg.write_include_likely(Broken(), True) is False


def test_find_settings_walks_parents(qapp, tmp_path):
    class Node:
        def __init__(self, parent=None, settings=None):
            self._p = parent
            if settings is not None:
                self.settings = settings

        def parent(self):
            return self._p
    s = _ini(tmp_path)
    host = Node(settings=s)
    leaf = Node(Node(host))
    assert eg.find_settings(leaf) is s and eg.find_settings(host) is s
    assert eg.find_settings(Node(Node())) is None and eg.find_settings(None) is None

    class NotSettings:
        def value(self, *a):
            return None
    assert eg.find_settings(Node(settings=NotSettings())) is None   # setValue 가 없으면 설정이 아니다
    # 실제 위젯: 메인 창 자리(settings 속성)를 대화상자를 거쳐 찾는다
    w = QWidget()
    w.settings = s
    dlg = QDialog(w)
    child = QWidget(dlg)
    assert eg.find_settings(child) is s
    w.deleteLater()


def test_listeners_weak_source_skip_and_dead_widgets():
    class Obj:
        def __init__(self):
            self.got = []

        def on(self, v):
            self.got.append(v)
    a, b = Obj(), Obj()
    base = eg.listener_count()
    eg.subscribe(a.on)
    eg.subscribe(a.on)                                  # 두 번 넣어도 하나
    eg.subscribe(b.on)
    assert eg.listener_count() == base + 2
    assert eg.notify(True) >= 2 and a.got == [True] and b.got == [True]
    eg.notify(False, source=a)                          # 바꾼 화면은 건너뛴다
    assert a.got == [True] and b.got == [True, False]
    r = weakref.ref(a)
    del a
    gc.collect()
    assert r() is None and eg.listener_count() == base + 1      # 약한 참조 — 화면을 붙잡지 않는다
    eg.unsubscribe(b.on)
    assert eg.listener_count() == base

    class Dead:
        def on(self, v):
            raise RuntimeError("Internal C++ object already deleted")
    d = Dead()
    eg.subscribe(d.on)
    eg.notify(True)                                      # 지워진 Qt 화면은 목록에서 빠진다
    assert eg.listener_count() == base
    seen = []
    eg.subscribe(seen.append)                            # 묶인 메서드가 아니면 강하게 잡는다 → 다 쓰면 뺀다
    eg.notify(True)
    eg.unsubscribe(seen.append)
    eg.notify(False)
    assert seen == [True] and eg.listener_count() == base


# ---------------------------------------------------------------------------- 나란히 보기: 순수 비교
def test_compare_default_dims_engine_likely():
    run, rows = d31_pair()
    off = av.compare(run, rows)                          # 기본 = 꺼짐(direct 만 반응)
    assert {it.chat_id: it.status for it in off.items} == OFF
    s = off.summary
    assert (s[av.SAME], s[av.GRADE_DIFF], s[av.ENGINE_ONLY], s[av.HUMAN_ONLY]) == (2, 0, 0, 3)
    assert s["unpaired_no_reaction"] == 1                # 4: 흐린 likely · 사람 없음 = 반응 미확인처럼 → 짝 없음
    assert (s["engine_direct"], s["engine_likely"], s["likely_dimmed"], s["include_likely"]) == (1, 5, 5, False)
    it1 = next(it for it in off.items if it.chat_id == cid(1))
    assert it1.engine.value == "likely" and it1.engine.dim and not it1.engine.graded   # 값은 그대로, 흐리게만
    assert it1.engine.cmp_value == av.UNCONFIRMED
    on = av.compare(run, rows, include_likely=True)      # 켬 = 전과 같다
    assert {it.chat_id: it.status for it in on.items} == ON
    s2 = on.summary
    assert (s2[av.SAME], s2[av.GRADE_DIFF], s2[av.ENGINE_ONLY], s2[av.HUMAN_ONLY]) == (2, 2, 2, 0)
    assert s2["unpaired_no_reaction"] == 0 and s2["likely_dimmed"] == 0 and s2["include_likely"] is True
    assert not any(it.engine.dim for it in on.items)
    # 혼동표는 옵션과 상관없이 원래 등급으로 센다(likely 줄 따로)
    assert off.confusion == on.confusion
    assert off.confusion["likely"] == {**{v: 0 for v in av.CONFUSION_VALUES}, "direct": 1, "likely": 1,
                                       "unconfirmed": 1, "absent": 1, "related_only": 1}
    # 판끼리 견주기는 옵션과 상관없이 등급 그대로 — ai_compare.diff_results 와 같은 다른 곳
    from app import ai_compare as ac
    new, old, hrows = runs_fixture()
    a = av.compare(new, hrows, against=old, include_likely=False)
    b = av.compare(new, hrows, against=old, include_likely=True)
    assert [(it.chat_id, it.status) for it in a.items] == [(it.chat_id, it.status) for it in b.items]
    d = ac.diff_results(new, old, human=hrows)
    kind = {ac.RD_GRADE_DIFF: av.GRADE_DIFF, ac.RD_A_ONLY: av.ENGINE_ONLY, ac.RD_B_ONLY: av.HUMAN_ONLY}
    assert {it.chat_id: it.status for it in a.items if it.status != av.SAME} == \
        {it["chat_id"]: kind[it["kind"]] for it in d["items"]}


# ---------------------------------------------------------------------------- 나란히 보기: 화면
def test_view_default_off_and_toggle(qapp):
    run, rows = d31_pair()
    v = av.AnalysisView(run=run, final_rows=rows, scores={"strict": {"n": 3}, "lenient": {"n": 4}})
    # 기본(설정 없음) = 꺼짐: 체크 상자는 필터 줄 옆, 머리 줄 아래 한 줄
    assert not v.include_likely() and not v.likely_box.isChecked() and v.likely_box.isEnabled()
    assert v.likely_box.text() == "likely 도 반응으로 보기"
    assert v.likely_box.parentWidget() is v.filter_box.parentWidget()
    assert v.rule_text() == "direct 만 · likely 5건 흐림"
    assert {it.chat_id: it.status for it in v.items()} == OFF and v.list.count() == 5
    sr = dict(v.summary_rows())
    assert sr["같음"] == 2 and sr["사람만"] == 3 and sr["엔진만"] == 0 and sr["등급 다름"] == 0
    assert sr["엔진 direct (반응 있음)"] == 1 and sr["엔진 likely (흐림 — 반응으로 안 셈)"] == 5
    assert sr["짝 없음 (반응 없음)"] == 1
    em, hm = v.timeline.markers(av.ENGINE_TRACK), v.timeline.markers(av.HUMAN_TRACK)
    assert [k for _t, k in em] == [av.DIM_MARK] * 4 + ["an_same"]       # 흐린 likely = 속 빈 회색 점
    assert (10_000, "an_human_only") in hm and (30_000, "an_same_n") in hm
    assert "엔진 likely 흐림" in v.legend.text()
    assert any("높음(흐림)" in v.list.item(i).text() for i in range(v.list.count()))
    li = av.CONFUSION_VALUES.index("likely")
    assert v.confusion_table.verticalHeaderItem(li).text() == "높음(흐림)"
    assert v.confusion_table.item(li, av.CONFUSION_VALUES.index("direct")).text() == "1"
    assert "사전 정의" in v.score_note.text()             # 점수는 옵션과 무관하다는 한 줄
    # 채팅 1 을 고른 채 켠다 → 고른 건은 그대로, 재생 위치·반복 재생 시그널은 다시 나가지 않는다
    seeks, loops = [], []
    v.seekRequested.connect(seeks.append)
    v.loopRequested.connect(lambda a, b: loops.append((a, b)))
    assert v.select_chat(cid(1)) and seeks == [10_000]
    v.likely_box.setChecked(True)                        # 클릭 한 번
    assert v.include_likely() and v.current_item().chat_id == cid(1) and v.current_item().status == av.GRADE_DIFF
    assert seeks == [10_000] and loops == []
    assert {it.chat_id: it.status for it in v.items()} == ON and v.list.count() == 6
    assert v.rule_text() == "direct·likely 모두 반응 · likely 5건 포함"
    assert av.DIM_MARK not in [k for _t, k in v.timeline.markers(av.ENGINE_TRACK)]
    assert "흐림" not in v.legend.text() and "사전 정의" not in v.score_note.text()
    sr = dict(v.summary_rows())
    assert sr["엔진만"] == 2 and sr["등급 다름"] == 2 and sr["엔진 likely (반응으로 셈)"] == 5
    assert v.confusion_table.verticalHeaderItem(li).text() == "높음"
    assert {r[0]: r[1:] for r in v.detail_rows()}["판정"] == ("높음", "직접")
    # 켠 상태에서만 보이는 채팅(4)을 고르고 끄면 목록에서 빠져 고른 건이 풀린다(시그널 없음)
    assert v.select_chat(cid(4)) and seeks[-1] == 40_000
    n = len(seeks)
    assert v.set_include_likely(False) is True
    assert v.current_item() is None and len(seeks) == n and v.list.count() == 5
    assert v.set_include_likely(False) is False          # 같은 값이면 아무것도 하지 않는다


def test_confirm_queue_unchanged_by_option(qapp):
    """AI 확인 큐(D-30 1)는 옵션과 무관: 큐·목록 줄·고른 건·타임라인 점이 꺼짐·켬에서 같고, 큐에는 likely 도 있다."""
    from tests.test_ai_confirm import RUN, FakeConfirm, _view_fixture
    seen = {}
    for inc in (False, True):
        run, rows = _view_fixture()
        fc = FakeConfirm()
        v = av.AnalysisView(run=run, final_rows=rows, confirm_loader=fc.load, confirm_writer=fc.save,
                            include_likely=inc)
        v.set_filter(av.FILTER_CONFIRM)
        seen[inc] = ([q.chat_id for q in v.confirm_queue()], [v.list.item(i).text() for i in range(v.list.count())],
                     v.visible_items(), v.timeline.markers(av.ENGINE_TRACK), v.detail_rows(), v.confirm_progress_text())
        assert not v.likely_box.isEnabled() and "AI 확인 큐" in v.rule_text()
        if not inc:
            # 꺼짐이면 '전체' 에는 없는 likely 채팅(2·6, 사람 없음)도 큐에는 그대로 있다
            assert cid(2) not in [it.chat_id for it in v.items()]
            loops, seeks = [], []
            v.loopRequested.connect(lambda a, b: loops.append((a, b)))
            v.seekRequested.connect(seeks.append)
            cur = v.current_item().chat_id
            v.set_include_likely(True)                   # 큐에서 옵션이 바뀌어도(다른 화면에서) 영상이 튀지 않는다
            assert v.current_item().chat_id == cur and loops == [] and seeks == []
            assert [q.chat_id for q in v.confirm_queue()] == seen[False][0]
            v.set_include_likely(False)
            assert v.judge("yes") and fc.saved == [(RUN, cid(2), "yes")]
    assert seen[False] == seen[True]
    assert seen[False][0] == [cid(2), cid(4), cid(6)]


def test_runs_mode_keeps_grades_and_box_disabled(qapp):
    new, old, rows = runs_fixture()
    v = av.AnalysisView(run=new, final_rows=rows, runs=CHOICES, run_loader=FakeLoader({"run-old": old}))
    assert not v.include_likely()
    st = {it.chat_id: it.status for it in v.items()}
    assert st[cid(3)] == av.HUMAN_ONLY                  # 엔진 likely(흐림) · 사람 높음 → 사람만
    assert v.compare_with("run-old")
    assert not v.likely_box.isEnabled() and v.rule_text().startswith("판끼리")
    from app import ai_compare as ac
    d = ac.diff_results(new, old, human=rows)
    kind = {ac.RD_GRADE_DIFF: av.GRADE_DIFF, ac.RD_A_ONLY: av.ENGINE_ONLY, ac.RD_B_ONLY: av.HUMAN_ONLY}
    assert {it.chat_id: it.status for it in v.visible_items()} == {it["chat_id"]: kind[it["kind"]] for it in d["items"]}
    assert av.DIM_MARK not in [k for _t, k in v.timeline.markers(av.ENGINE_TRACK)]
    assert v.compare_with(None) and v.likely_box.isEnabled() and v.rule_text() == "direct 만 · likely 1건 흐림"


def test_sealed_shows_no_engine_counts(qapp):
    run, rows = d31_pair()
    v = av.AnalysisView(run=None, final_rows=rows, sealed_count=2)
    assert v.rule_text() == "" and not any("흐림" in t for t in v.all_texts())
    v.set_data(run=run, final_rows=rows)
    assert v.rule_text() == "direct 만 · likely 5건 흐림"
    v.set_data(run=None, final_rows=rows, sealed_count=1)
    assert v.rule_text() == "" and not any("흐림" in t or "likely" in t for t in v.all_texts())


def test_views_are_freed_with_listener(qapp):
    """알림 목록은 약한 참조라 화면을 붙잡지 않는다(08 D-18 10)."""
    run, rows = d31_pair()

    def make():
        v = av.AnalysisView(run=run, final_rows=rows)
        v.likely_box.setChecked(True)
        v.likely_box.setChecked(False)
        s = sv.SummaryView([], include_likely=False)
        return weakref.ref(v), eg.listener_count(), s
    r, n, _s = make()
    gc.collect()
    assert r() is None and eg.listener_count() <= n - 1
    eg.notify(False)                                     # 지워진 화면에 알려도 깨지지 않는다


# ---------------------------------------------------------------------------- 설정 기억 · 열린 화면 따라가기
class Host(QWidget):
    """메인 창 자리 — settings 속성(settings.ini)을 가진 창(app/ui/mainwindow 의 self.settings 와 같은 모양)."""

    def __init__(self, s):
        super().__init__()
        self.settings = s


def _engine_rows():
    """analysis_flow.broadcast_summary 가 엔진 판정으로 셀 때의 줄 모양(합성): 덩어리 2개, 채팅 10·10,
    direct 2·0, likely 3·0."""
    return [
        {"index": 0, "start_ms": 0, "end_ms": 60_000, "name": "합성A", "top_keyword": "가", "chats": 10,
         "chats_per_min": 10.0, "reacted": 2, "reaction_rate": 0.2, "questions": 0, "missed_questions": 0,
         "questions_unknown": 0, "keywords": ["가"], "likely_reacted": 3, "reaction_rate_with_likely": 0.5},
        {"index": 1, "start_ms": 60_000, "end_ms": 120_000, "name": "합성B", "top_keyword": "나", "chats": 10,
         "chats_per_min": 10.0, "reacted": 0, "reaction_rate": 0.0, "questions": 0, "missed_questions": 1,
         "questions_unknown": 0, "keywords": ["나"], "likely_reacted": 0, "reaction_rate_with_likely": 0.0},
    ]


def test_setting_remembered_and_followed(qapp, tmp_path):
    run, rows = d31_pair()
    s = _ini(tmp_path)
    host = Host(s)
    dlg = QDialog(host)
    v1 = av.AnalysisView(dlg, run=run, final_rows=rows)       # 메인 창이 만드는 것처럼(settings 를 넘기지 않음)
    other = av.AnalysisView(run=run, final_rows=rows)         # 설정 없이 열린 다른 나란히 보기
    summ = sv.SummaryView(_engine_rows())                     # 열린 방송 요약
    assert not v1.include_likely() and not other.include_likely() and not summ.include_likely()
    v1.likely_box.setChecked(True)                            # 한 화면에서 켠다
    assert eg.read_include_likely(_ini(tmp_path)) is True     # settings.ini 에 남는다(같은 키 하나)
    assert other.include_likely() and other.likely_box.isChecked()          # 열린 화면이 따라간다
    assert other.rule_text().startswith("direct·likely 모두 반응")
    assert summ.include_likely() and summ.table.item(0, 3).text() == "50%"
    # 새로 연 화면은 남은 값으로 연다
    host2 = Host(_ini(tmp_path))
    dlg2 = QDialog(host2)
    v2 = av.AnalysisView(dlg2, run=run, final_rows=rows)
    s2 = sv.SummaryView(_engine_rows(), parent=QDialog(host2))
    assert v2.include_likely() and v2.likely_box.isChecked() and s2.include_likely()
    # 거기서 끄면 처음 화면도 꺼지고 파일도 꺼짐
    v2.set_include_likely(False)
    assert not v1.include_likely() and not v1.likely_box.isChecked() and not summ.include_likely()
    assert eg.read_include_likely(_ini(tmp_path)) is False
    assert summ.table.item(0, 3).text() == "20%"
    for w in (host, host2):
        w.deleteLater()


# ---------------------------------------------------------------------------- 방송 요약
def test_summary_engine_rows_show_direct_and_likely(qapp):
    rows = sv.normalize_topics(_engine_rows()).rows
    assert [(r.reacted, r.likely, r.rate_with_likely) for r in rows] == [(2, 3, 0.5), (0, 0, 0.0)]
    assert sv.has_engine_likely(rows)
    assert sv.row_rate(rows[0], include_likely=False) == 0.2 and sv.row_rate(rows[0], include_likely=True) == 0.5
    off, on = sv.summary_totals(rows), sv.summary_totals(rows, include_likely=True)
    assert off["reply_rate"] == pytest.approx(0.1) and on["reply_rate"] == pytest.approx(0.25)
    assert (off["reacted"], off["likely"]) == (2, 3)
    # likely 도 넣은 반응률이 없으면 줄의 숫자로 계산한다
    r = sv.normalize_topics([{"start_ms": 0, "end_ms": 10, "chats": 10, "reacted": 1, "likely_reacted": 2}]).rows[0]
    assert r.rate_with_likely == pytest.approx(0.3) and r.reply_rate == pytest.approx(0.1)
    v = sv.SummaryView(_engine_rows(), include_likely=False)
    assert not v.likely_box.isHidden() and not v.likely_box.isChecked()
    assert v.table.item(0, 3).text() == "20%" and "likely 3(흐림)" in v.table.item(0, 3).toolTip()
    assert "엔진 반응 direct 2 · likely 3(흐림)" in v.totals_text() and "반응률 10%" in v.totals_text()
    v.set_include_likely(True, save=False)
    assert v.table.item(0, 3).text() == "50%" and "엔진 반응 direct 2 · likely 3 " in v.totals_text() + " "
    assert "반응률 25%" in v.totals_text() and "(흐림)" not in v.totals_text()
    # 저장·내보내기 줄은 바꾸지 않는다
    assert set(v.export_rows()[0]) == {"index", "topic_id", "start_ms", "end_ms", "name", "keywords", "chat_count",
                                       "reply_rate", "missed_questions"}
    assert v.export_rows()[0]["reply_rate"] == 0.2
    # 사람 최종 세트로 센 요약(likely 칸 없음)은 그대로 — 옵션 상자도 없다
    human = [{k: x for k, x in row.items() if k not in ("likely_reacted", "reaction_rate_with_likely")}
             for row in _engine_rows()]
    h = sv.SummaryView(human, include_likely=True)
    assert h.likely_box.isHidden() and h.table.item(0, 3).text() == "20%" and "엔진 반응" not in h.totals_text()


# ---------------------------------------------------------------------------- 작업대 엔진 띠
def test_engine_mark_kind_and_timeline_style():
    from app.ui import workbench as wbm
    from app.ui.timeline import MARK_STYLES
    assert wbm.engine_mark_kind("direct", include_likely=False) == "engine"
    assert wbm.engine_mark_kind("likely", include_likely=False) == wbm.ENGINE_DIM_KIND == "engine_dim"
    assert wbm.engine_mark_kind("likely", include_likely=True) == "engine"
    for g in ("related_only", "unclear", "graded", None):
        assert wbm.engine_mark_kind(g, include_likely=False) == "engine_weak"
    col, hollow = MARK_STYLES["engine_dim"]
    assert hollow is True and col.red() == col.green() == col.blue()              # 속 빈 회색


def test_timeline_track_label(qapp):
    from app.ui.timeline import Timeline
    t = Timeline()
    t.set_markers("engine", [(1_000, "engine_dim")])
    t.set_track_label("engine", "엔진 · direct 0 · likely 1(흐림)")
    assert t._track_labels["engine"] == "엔진 · direct 0 · likely 1(흐림)"
    assert t.markers("engine") == [(1_000, "engine_dim")]
    t.set_track_label("engine", None)
    assert "engine" not in t._track_labels


def test_workbench_engine_band_follows_option(qapp, tmp_path):
    pytest.importorskip("app.core.visibility")
    from tests.test_workbench import make_project, make_wb
    proj = make_project(tmp_path)
    obj = {"schema": "chatcoach/ai-result", "time_axis": "vod_ms", "reactions": [
        {"chat_id": "c0010", "result": "graded", "grade": "direct", "reaction_time_ms": 16_000},
        {"chat_id": "c0012", "result": "graded", "grade": "likely", "reaction_time_ms": 20_000},
        {"chat_id": "c0020", "result": "graded", "grade": "unclear", "reaction_time_ms": 31_000},
    ]}
    with open(os.path.join(proj.paths.derived_dir, "link-test.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f)
    wb = make_wb(qapp, proj, session_kind="analysis")
    try:
        s = _ini(tmp_path)
        wb.settings = s                                       # 메인 창이 넣는 자리
        assert wb.likely_action.isVisible() and not wb.likely_action.isChecked()
        wb.analysis_action.setChecked(True)
        assert wb.wait_jobs(10_000)
        assert wb.timeline.markers["engine"] == [(16_000, "engine"), (20_000, "engine_dim"), (31_000, "engine_weak")]
        assert wb.engine_counts_text == "엔진 · direct 1 · likely 1(흐림)"
        assert "direct 1 · likely 1(흐림)" in wb.last_message()
        wb.likely_action.setChecked(True)                     # 켜면 채운 점, 설정에 남는다
        assert wb.timeline.markers["engine"] == [(16_000, "engine"), (20_000, "engine"), (31_000, "engine_weak")]
        assert wb.engine_counts_text == "엔진 · direct 1 · likely 1" and eg.read_include_likely(_ini(tmp_path))
        eg.write_include_likely(None, False)                  # 다른 화면에서 끄면 띠가 따라간다
        assert wb.timeline.markers["engine"][1] == (20_000, "engine_dim") and not wb.likely_action.isChecked()
        wb.analysis_action.setChecked(False)
        assert "engine" not in wb.timeline.markers and wb.engine_counts_text == ""
    finally:
        wb.close_workbench()
    eg.notify(True)                                           # 닫힌 작업대에 알려도 아무것도 하지 않는다
    eg.notify(False)
