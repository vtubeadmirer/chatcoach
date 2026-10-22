"""방송 요약 화면·주제 띠 시험 (app/ui/summary_view.py, app/ui/topic_track.py — 08 D-01·D-16·D-17 5·8, 17 6-1 1·2번).

화면 없이(QT_QPA_PLATFORM=offscreen) **합성** 주제 덩어리로만 시험한다. 주제 이름·키워드·본문은 모두 합성 자리표시다.
실제 채팅·닉네임·전사문은 쓰지 않는다.
"""
import logging
import os
import sys
from dataclasses import dataclass, field

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPlainTextEdit, QSpinBox, QTextEdit  # noqa: E402

from app.ui import summary_view as sv  # noqa: E402
from app.ui import topic_track as tt  # noqa: E402

SECRET = "SEALED-TOPIC-SECRET"     # 봉인 중에는 화면 어디에도 나오면 안 되는 값
BODY = "BODY-TEXT-MARKER"          # 본문 자리 — 화면에는 보이되 로그·내보내기에는 없어야 한다

MIN = 60_000


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def synth_topics():
    """합성 주제 덩어리 3개(일부러 시각 순서를 섞음) + 시각이 틀린 1개."""
    return [
        {"topic_id": "t2", "start_ms": 25 * MIN, "end_ms": 31 * MIN, "name": "주제B", "keywords": ["가나", "다라"],
         "chat_count": 40, "reply_rate": 0.5, "missed_questions": 2, "text": BODY},
        {"topic_id": "t1", "start_ms": 20 * MIN, "end_ms": 25 * MIN, "label": "주제A",
         "keywords": [("마바", 0.9), ("사아", 0.5)], "stats": {"chat_count": 10, "replied_count": 3,
                                                           "missed_questions": 0}},
        {"topic_id": "t3", "start_ms": 31 * MIN, "end_ms": 40 * MIN, "keywords": {"자차": 0.2, "카타": 0.8},
         "chat_count": 50, "reply_rate": 20, "missed_questions": 1},
        {"topic_id": "bad", "start_ms": 41 * MIN, "end_ms": 41 * MIN},
    ]


def all_text(w) -> str:
    """위젯 트리의 모든 보이는 글(라벨·표·툴팁)."""
    out = []
    from PySide6.QtWidgets import QLabel, QTableWidget, QWidget
    for c in [w] + w.findChildren(QWidget):
        out.append(c.toolTip() or "")
        if isinstance(c, QLabel):
            out.append(c.text())
        if isinstance(c, QTableWidget):
            for r in range(c.rowCount()):
                for k in range(c.columnCount()):
                    it = c.item(r, k)
                    if it:
                        out.append(it.text() + " " + (it.toolTip() or ""))
    return "\n".join(out)


# ---------------------------------------------------------------------------- 순수 계산
def test_normalize_sorts_and_reads_aliases():
    t = sv.normalize_topics(synth_topics())
    assert t.invalid == 1
    assert [r.topic_id for r in t.rows] == ["t1", "t2", "t3"]
    assert [r.index for r in t.rows] == [0, 1, 2]
    a, b, c = t.rows
    assert a.name == "주제A" and a.keywords == ("마바", "사아")
    assert a.chat_count == 10 and a.reply_rate == pytest.approx(0.3) and a.missed_questions == 0
    assert b.reply_rate == 0.5 and b.body == BODY
    assert c.name is None and c.keywords == ("카타", "자차")      # 점수 큰 순서
    assert c.reply_rate == pytest.approx(0.2)                     # 백분율 20 → 0.2
    assert "카타 · 자차" == c.display_name                         # 주제 이름(키워드) — 사람 닉네임 아님


def test_normalize_input_shapes():
    base = [{"start_ms": 0, "end_ms": 1000, "id": "x"}, {"start_ms": 1000, "end_ms": 3000, "id": "y"}]
    stats = {"x": {"chat_count": 5}, "y": {"chat_count": 7, "missed": 1}}
    for data in ({"topics": base, "stats": stats}, (base, stats), (base, [{"chat_count": 5}, {"chat_count": 7,
                                                                                          "missed": 1}])):
        rows = sv.normalize_topics(data).rows
        assert [r.chat_count for r in rows] == [5, 7]
        assert rows[1].missed_questions == 1

    @dataclass
    class Chunk:
        start_ms: int
        end_ms: int
        name: str
        keywords: list = field(default_factory=list)
        chat_count: int | None = None

    @dataclass
    class Result:
        topics: list

    rows = sv.normalize_topics(Result([Chunk(0, 500, "가"), Chunk(500, 900, "나", ["다"], 3)])).rows
    assert [r.name for r in rows] == ["가", "나"] and rows[1].chat_count == 3
    assert sv.normalize_topics(None).rows == ()
    assert sv.normalize_topics({"nothing": 1}).rows == ()
    assert sv.normalize_topics([None, {"start_ms": "x", "end_ms": 1}]).invalid == 2


def test_rate_bounds_and_counts():
    rows = sv.normalize_topics([
        {"start_ms": 0, "end_ms": 10, "reply_rate": 150},           # 범위 밖 → 없음
        {"start_ms": 10, "end_ms": 20, "reply_rate": True},         # bool 은 숫자가 아님
        {"start_ms": 20, "end_ms": 30, "chat_count": 2, "replied_count": 5},   # 채팅 수보다 많음 → 없음
        {"start_ms": 30, "end_ms": 40, "missed_questions": ["q1", "q2"]},      # 목록이면 개수
    ]).rows
    assert [r.reply_rate for r in rows] == [None, None, None, None]
    assert rows[3].missed_questions == 2


def test_totals_weighted_and_export_has_no_body():
    rows = sv.normalize_topics(synth_topics()).rows
    tot = sv.summary_totals(rows)
    assert tot["topics"] == 3 and tot["chat_count"] == 100 and tot["missed_questions"] == 3
    assert tot["reply_rate"] == pytest.approx((10 * 0.3 + 40 * 0.5 + 50 * 0.2) / 100)
    assert tot["duration_ms"] == 20 * MIN
    exp = sv.export_rows(rows)
    assert len(exp) == 3 and "body" not in exp[0]
    assert BODY not in repr(exp)
    assert set(exp[0]) == {"index", "topic_id", "start_ms", "end_ms", "name", "keywords", "chat_count",
                           "reply_rate", "missed_questions"}
    assert sv.summary_totals([])["reply_rate"] is None


def test_row_at_and_formatting():
    rows = sv.normalize_topics(synth_topics()).rows
    assert sv.row_at(rows, 20 * MIN) == 0
    assert sv.row_at(rows, 25 * MIN) == 1          # 경계는 뒤 덩어리
    assert sv.row_at(rows, 40 * MIN) is None
    assert sv.row_at(rows, 0) is None
    assert sv.fmt_rate(0.425) == "42%" and sv.fmt_rate(None) == "—"
    assert sv.fmt_span(90_000) == "1분 30초" and sv.fmt_span(3_900_000) == "1시간 5분" and sv.fmt_span(5000) == "5초"


# ---------------------------------------------------------------------------- 화면
def test_view_shows_table_immediately_without_inputs(qapp):
    v = sv.SummaryView(synth_topics())
    assert not v.is_sealed()
    assert v.stack.currentWidget() is v.table_page
    assert v.table.rowCount() == 3 and v.row_count() == 3 and v.invalid_count() == 1
    assert [v.table.horizontalHeaderItem(i).text() for i in range(5)] == ["주제", "시간", "채팅 수", "반응률",
                                                                            "놓친 질문"]
    assert v.table.item(0, 0).text().startswith("주제A")
    assert "마바" in v.table.item(0, 0).text()
    assert v.table.item(0, 2).text() == "10" and v.table.item(0, 3).text() == "30%"
    assert v.table.item(0, 4).text() == "0"
    assert "주제 3개" in v.totals_label.text() and "놓친 질문 3" in v.totals_label.text()
    # D-16: 입력 칸이 없다
    for kind in (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox):
        assert not v.findChildren(kind)


def test_click_row_emits_seek_once(qapp):
    v = sv.SummaryView(synth_topics())
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    got, ids = [], []
    v.seekRequested.connect(got.append)
    v.topicActivated.connect(ids.append)
    rect = v.table.visualItemRect(v.table.item(1, 2))
    QTest.mouseClick(v.table.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert got == [25 * MIN] and ids == [1]
    # Enter 로도
    v.table.setCurrentCell(2, 0)
    QTest.keyClick(v.table, Qt.Key_Return)
    assert got[-1] == 31 * MIN and ids[-1] == 2
    v.close()


def test_set_position_selects_without_signal(qapp):
    v = sv.SummaryView(synth_topics())
    got = []
    v.seekRequested.connect(got.append)
    v.set_position(32 * MIN)
    assert v.table.currentRow() == 2
    assert BODY not in v.detail_label.text()        # 본문 없는 덩어리
    v.set_position(26 * MIN)
    assert v.table.currentRow() == 1
    assert BODY in v.detail_label.text()            # 본문은 화면에만 보인다
    v.set_position(0)                               # 덩어리 밖 — 그대로
    assert v.table.currentRow() == 1
    assert got == []


def test_sealed_shows_only_sealed(qapp):
    secret = [{"start_ms": 0, "end_ms": 1000, "name": SECRET, "keywords": [SECRET], "text": SECRET,
               "chat_count": 1}]
    v = sv.SummaryView(secret, sealed=True)
    assert v.is_sealed() and v.stack.currentWidget() is v.sealed_page
    assert v.sealed_label.text() == "봉인됨"
    assert v.row_count() == 0 and v.table.rowCount() == 0
    assert SECRET not in all_text(v)
    assert v.export_rows() == []
    got = []
    v.seekRequested.connect(got.append)
    v._activate_row(0)
    v.set_position(500)
    assert got == []
    # 해제 뒤 같은 위젯에 자료를 넣으면 보인다
    v.set_data(synth_topics())
    assert not v.is_sealed() and v.table.rowCount() == 3
    # 다시 봉인하면 표를 비운다
    v.set_data(synth_topics(), sealed=True)
    assert v.table.rowCount() == 0 and "주제A" not in all_text(v)


def test_sealed_does_not_read_topics(qapp):
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("봉인 중에 주제 흐름을 읽었다")

        def __iter__(self):
            raise AssertionError("봉인 중에 주제 흐름을 읽었다")

    v = sv.SummaryView(Boom(), sealed=True)
    assert v.is_sealed()


def test_empty_state(qapp):
    v = sv.SummaryView([])
    assert v.stack.currentWidget() is v.empty_page
    assert "아직 없습니다" in v.empty_label.text()


def test_log_has_counts_only(qapp, caplog):
    caplog.set_level(logging.DEBUG, logger="chatcoach")
    v = sv.SummaryView(synth_topics())
    v.set_position(26 * MIN)
    v._activate_row(1)
    text = caplog.text
    assert "주제 3개" in text
    for word in ("주제A", "주제B", "가나", "마바", BODY):
        assert word not in text


# ---------------------------------------------------------------------------- 주제 띠
def test_topic_bands_and_markers():
    bands = tt.topic_bands(synth_topics())
    assert [(b.start_ms, b.end_ms, b.name) for b in bands] == [
        (20 * MIN, 25 * MIN, "주제A"), (25 * MIN, 31 * MIN, "주제B"), (31 * MIN, 40 * MIN, "카타 · 자차")]
    assert bands[0].color.rgb() != bands[1].color.rgb()
    assert tt.topic_markers(synth_topics()) == [(20 * MIN, "topic"), (25 * MIN, "topic"), (31 * MIN, "topic")]
    assert tt.band_at(bands, 30 * MIN).index == 1
    assert tt.band_at(bands, 45 * MIN) is None
    assert tt.topic_bands(None) == []


def test_topic_track_click_and_view(qapp):
    w = tt.TopicTrack()
    w.resize(600, tt.TRACK_H)
    w.set_duration(60 * MIN)
    w.set_view(20 * MIN, 40 * MIN)
    w.set_topics(synth_topics())
    w.show()
    qapp.processEvents()
    got, ids = [], []
    w.seekRequested.connect(got.append)
    w.topicClicked.connect(ids.append)
    x = int(w.t_to_x(28 * MIN))                     # 주제B 안
    QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(x, 10))
    assert got == [25 * MIN] and ids == [1]
    # 보이는 범위가 바뀌면 좌표가 따라간다 (Timeline.viewChanged 에 잇는 자리)
    w.set_view(30 * MIN, 40 * MIN)
    assert w.view_range() == (30 * MIN, 40 * MIN)
    assert w.band_at_x(0).index == 1 and w.band_at_x(599).index == 2
    w.set_position(35 * MIN)
    assert w.current_band().index == 2
    # 그리기가 깨지지 않는다
    pm = QPixmap(w.size())
    w.render(pm)
    # 봉인
    w.set_sealed(True)
    assert w.is_sealed() and w.bands() == []
    QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(x, 10))
    assert len(got) == 1
    w.render(pm)
    w.close()


def test_topic_track_click_outside_band_seeks_to_x(qapp):
    w = tt.TopicTrack()
    w.resize(600, tt.TRACK_H)
    w.set_duration(60 * MIN)
    w.set_view(0, 60 * MIN)
    w.set_topics(synth_topics())
    w.show()
    qapp.processEvents()
    got, ids = [], []
    w.seekRequested.connect(got.append)
    w.topicClicked.connect(ids.append)
    QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(50, 10))   # 5분 자리, 띠 밖
    assert ids == [] and got == [w.x_to_t(50)]
    w.close()
