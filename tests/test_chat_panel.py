"""app/ui/chat_panel.py 테스트 (합성 데이터만, 화면 없이 offscreen)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QModelIndex, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import chat_panel as cp  # noqa: E402

VA, VB, VC = "vw_aaaaaaaaaaaa", "vw_bbbbbbbbbbbb", "vw_cccccccccccc"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _msg(cid, t, vid, text, kind="text", masked="가**", role="viewer"):
    return {"chat_id": cid, "t_ms": t, "server_time_ms": 1_000_000 + (t or 0), "type_code": 1, "kind": kind,
            "status": "NORMAL", "viewer_id": vid, "is_anonymous": vid == "anon", "nickname_masked": masked,
            "role": role, "text": text, "raw_ref": {"fetch_id": "F1", "page": 0, "index": 0}}


def _rows():
    # 일부러 시각 순서를 섞어 둔다 (입력 순서와 무관해야 함)
    return [
        _msg("c03", 70_000, VA, "세번째 글 Hello"),
        _msg("c01", 10_000, VB, "첫 글\n줄바꿈"),
        _msg("c02", 10_000, VA, "같은 시각 둘째 hello"),
        _msg("c04", 75_000, "anon", "익명 글", masked="***"),
        _msg("c05", 80_000, VC, "후원 글", kind="donation"),
        _msg("c06", None, VC, "시각 없음"),
        _msg("c07", 90_000, VC, "스트리머 글", role="streamer"),
    ]


@pytest.fixture
def panel(qapp):
    p = cp.ChatPanel()
    p.resize(500, 300)
    p.show()
    QApplication.processEvents()
    p.set_messages(_rows())
    yield p
    p.close()
    p.deleteLater()
    QApplication.processEvents()


def _ids(p):
    m = p.model
    return [m.data(m.index(r, 0), Qt.UserRole) for r in range(m.rowCount())]


def _cell(p, row, col, role=Qt.DisplayRole):
    return p.model.data(p.model.index(row, col), role)


# ── 순수 도우미 ───────────────────────────────────────────────────────────
def test_compute_viewer_no_order_independent():
    rows = _rows()
    a = cp.compute_viewer_no(rows)
    b = cp.compute_viewer_no(list(reversed(rows)))
    assert a == b == {VB: 1, VA: 2, VC: 3}   # (t_ms, chat_id) 순: c01(VB) → c02(VA) → … ; 익명 없음


def test_fmt_viewer_no():
    assert cp.fmt_viewer_no(12) == "시청자 0012"
    assert cp.fmt_viewer_no(12345) == "시청자 12345"
    assert cp.fmt_viewer_no(cp.VNO_ANON) == "익명"
    assert cp.fmt_viewer_no(cp.VNO_UNKNOWN) == "-"


def test_chat_data_sorted_and_codes():
    d = cp.ChatData(_rows())
    assert d.chat_ids == ["c01", "c02", "c03", "c04", "c05", "c07", "c06"]   # null 시각은 맨 뒤
    assert list(d.times[:6]) == [10_000, 10_000, 70_000, 75_000, 80_000, 90_000]
    assert d.times[6] == cp.NULL_T
    assert d.kinds[4] == cp.KIND_CODES["donation"]
    assert d.roles[5] == cp.ROLE_CODES["streamer"]
    assert d.folded[2] == "세번째 글 hello"
    assert list(d.vnos) == [1, 2, 2, cp.VNO_ANON, 3, 3, 3]


def test_chat_data_already_sorted_fast_path_same_result():
    rows = sorted(_rows(), key=cp._order_key)
    assert cp._already_sorted(rows)
    assert not cp._already_sorted(_rows())
    assert cp.ChatData(rows).chat_ids == cp.ChatData(_rows()).chat_ids


def test_given_viewer_no_is_used():
    d = cp.ChatData(_rows(), viewer_no={VA: 44})
    assert d.vnos[0] == cp.VNO_UNKNOWN   # VB 는 넘겨받은 표에 없음
    assert d.vnos[1] == 44


# ── 모델 ─────────────────────────────────────────────────────────────────
def test_row_count_and_columns(panel):
    m = panel.model
    assert m.rowCount() == 7 and m.columnCount() == 5
    assert m.rowCount(m.index(0, 0)) == 0      # 표 모델: 자식 없음
    assert [m.headerData(c, Qt.Horizontal) for c in range(5)] == list(cp.HEADERS)
    assert _cell(panel, 0, cp.COL_TIME) == "00:00:10"
    assert _cell(panel, 0, cp.COL_VIEWER) == "시청자 0001"
    assert _cell(panel, 3, cp.COL_VIEWER) == "익명"
    assert _cell(panel, 0, cp.COL_TEXT) == "첫 글 줄바꿈"            # 한 줄로
    assert _cell(panel, 0, cp.COL_TEXT, Qt.ToolTipRole) == "첫 글\n줄바꿈"
    assert _cell(panel, 6, cp.COL_TIME) == "—"
    assert _cell(panel, 4, cp.COL_KIND) == "후원"
    assert _cell(panel, 5, cp.COL_KIND) == "스트리머"
    assert _cell(panel, 0, cp.COL_KIND) == ""
    assert _cell(panel, 0, cp.COL_TEXT, Qt.ForegroundRole) is None
    assert _cell(panel, 4, cp.COL_TEXT, Qt.ForegroundRole) is not None   # 후원은 흐리게
    assert panel.model.data(QModelIndex()) is None


def test_fixed_row_height_no_wrap(panel):
    v = panel.view
    assert not v.wordWrap()
    assert v.verticalHeader().sectionResizeMode(0) == cp.QHeaderView.Fixed
    assert v.verticalHeader().defaultSectionSize() == cp.ROW_HEIGHT
    assert v.model() is panel.model


def test_filter_text_and_viewer(panel):
    panel.set_filter("HELLO")                      # casefold
    assert _ids(panel) == ["c02", "c03"]
    assert panel.row_count() == 2 and panel.total_count() == 7
    assert panel.count_label.text() == "2 / 7"
    panel.set_filter("시청자 3")
    assert _ids(panel) == ["c05", "c07", "c06"]
    panel.set_filter("#0002")
    assert _ids(panel) == ["c02", "c03"]
    panel.set_filter("없는말")
    assert _ids(panel) == []
    panel.set_filter("")
    assert panel.row_count() == 7
    assert panel.search.text() == ""


def test_kind_filter(panel):
    panel.set_kind_filter(["text"])
    assert "c05" not in _ids(panel) and len(_ids(panel)) == 6
    panel.set_filter("글")
    assert _ids(panel) == ["c01", "c03", "c04", "c07"]
    panel.set_kind_filter(None)
    assert "c05" in _ids(panel)


def test_selection_restored_after_filter(panel):
    assert panel.selected_chat_id() is None
    assert panel.select_chat("c03")
    assert panel.selected_chat_id() == "c03"
    panel.set_filter("hello")
    assert panel.selected_chat_id() == "c03"
    panel.set_filter("")
    assert panel.selected_chat_id() == "c03"
    panel.set_filter("첫")                           # 선택이 걸러져 사라지면 선택 없음
    assert panel.selected_chat_id() is None
    assert not panel.select_chat("c03")              # 걸러져 있으면 False
    assert not panel.select_chat("없음")
    panel.set_filter("")                             # 해제하면 직전 선택이 돌아온다
    assert panel.selected_chat_id() == "c03"
    panel.view.clearSelection()
    panel.view.selectionModel().clearCurrentIndex()  # 사용자가 선택을 지우면 기억도 지운다
    panel.set_filter("hello")
    panel.set_filter("")
    assert panel.selected_chat_id() is None


def test_set_time_ms_highlight(panel):
    panel.set_time_ms(5_000)
    assert panel.highlighted_chat_id() is None
    panel.set_time_ms(10_000)
    assert panel.highlighted_chat_id() == "c02"      # 같은 시각이면 마지막 행
    panel.set_time_ms(74_999)
    assert panel.highlighted_chat_id() == "c03"
    assert _cell(panel, 2, 0, Qt.BackgroundRole) is not None
    assert _cell(panel, 1, 0, Qt.BackgroundRole) is None
    panel.set_time_ms(10 ** 9)
    assert panel.highlighted_chat_id() == "c07"      # 시각 없는 줄(c06)은 하이라이트되지 않는다
    panel.set_filter("hello")
    panel.set_time_ms(80_000)
    assert panel.highlighted_chat_id() == "c03"      # 걸러진 행 중에서
    panel.set_time_ms(10_000)
    assert panel.highlighted_chat_id() == "c02"


def test_highlight_follows_scroll(qapp):
    p = cp.ChatPanel()
    p.resize(400, 200)
    p.show()
    QApplication.processEvents()
    p.set_messages([_msg(f"m{i:05d}", i * 1000, VA, f"글 {i}") for i in range(2000)])
    QApplication.processEvents()
    sb = p.view.verticalScrollBar()
    assert sb.value() == 0
    p.set_time_ms(1_500_000)
    row = 1500
    vh = p.view.viewport().height()
    assert sb.value() <= row * cp.ROW_HEIGHT <= sb.value() + vh - cp.ROW_HEIGHT
    before = sb.value()
    p.set_time_ms(100_000, follow=False)
    assert sb.value() == before
    assert p.highlighted_chat_id() == "m00100"
    # 사용자가 스크롤하면 잠시 따라가지 않는다
    p._pause_follow()
    p.set_time_ms(1_900_000)
    assert sb.value() == before
    p.close()


def test_scroll_restored_on_filter_clear(qapp):
    p = cp.ChatPanel()
    p.resize(400, 200)
    p.show()
    QApplication.processEvents()
    p.set_messages([_msg(f"m{i:05d}", i * 1000, VA, "짝" if i % 2 == 0 else "홀") for i in range(1000)])
    QApplication.processEvents()
    assert p.select_chat("m00500")
    sb = p.view.verticalScrollBar()
    y_before = 500 * cp.ROW_HEIGHT - sb.value()
    p.set_filter("짝")
    assert p.selected_chat_id() == "m00500"
    assert 250 * cp.ROW_HEIGHT - sb.value() == y_before      # 같은 화면 높이에
    p.set_filter("")
    assert p.selected_chat_id() == "m00500"
    assert 500 * cp.ROW_HEIGHT - sb.value() == y_before
    p.close()


def test_dim_ranges(panel):
    panel.set_dim_ranges([(70_000, 80_000)])        # [70s, 80s): c03·c04
    d = panel.chat_data()
    assert list(d.dim) == [0, 0, 1, 1, 0, 0, 0]
    assert _cell(panel, 2, cp.COL_TEXT, Qt.ForegroundRole) is not None
    assert _cell(panel, 1, cp.COL_TEXT, Qt.ForegroundRole) is None
    panel.set_dim_ranges([])
    assert not any(panel.chat_data().dim)


def test_chat_activated_double_click_and_enter(panel):
    got = []
    panel.chatActivated.connect(lambda cid, t: got.append((cid, t)))
    v = panel.view
    v.doubleClicked.emit(panel.model.index(2, 3))
    assert got == [("c03", 70_000)]
    panel.select_chat("c05")
    v.setFocus()
    QTest.keyClick(v, Qt.Key_Return)
    assert got[-1] == ("c05", 80_000)
    QTest.keyClick(v, Qt.Key_Enter)
    assert got[-1] == ("c05", 80_000) and len(got) == 3
    panel.select_chat("c06")                          # 시각 없는 채팅은 알리지 않는다
    QTest.keyClick(v, Qt.Key_Return)
    assert len(got) == 3


def test_real_double_click_emits_once(panel):
    got = []
    panel.chatActivated.connect(lambda cid, t: got.append(cid))
    v = panel.view
    rect = v.visualRect(panel.model.index(1, cp.COL_TEXT))
    QTest.mousePress(v.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())   # 실제 순서: 누름 → 뗌 → 두 번째 누름(더블클릭)
    QTest.mouseRelease(v.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTest.mouseDClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert got == ["c02"]


def test_chat_selected_signal(panel):
    got = []
    panel.chatSelected.connect(got.append)
    panel.select_chat("c04")
    assert got[-1] == "c04"


def test_search_debounce(panel):
    panel.search.setText("hello")                    # 사용자가 친 것처럼 (textChanged)
    assert panel.row_count() == 7                    # 바로 거르지 않는다
    QTest.qWait(100)
    panel.search.setText("hell")                     # 다시 치면 타이머가 처음부터
    QTest.qWait(180)
    assert panel.row_count() == 7
    QTest.qWait(250)
    assert _ids(panel) == ["c02", "c03"]


def test_set_messages_resets_state(panel):
    panel.set_filter("hello")
    panel.set_time_ms(70_000)
    panel.set_messages([_msg("z1", 1, VA, "a")])
    assert panel.row_count() == 1 and panel.search.text() == ""
    assert panel.highlighted_chat_id() is None
    panel.set_messages([])
    assert panel.row_count() == 0
    panel.set_time_ms(100)
    assert panel.highlighted_chat_id() is None
    assert panel.selected_chat_id() is None


def test_no_proxy_or_list_widget_in_module():
    src = open(cp.__file__, encoding="utf-8").read()
    code = src.split('"""', 2)[2]                    # 머리 설명문 제외
    for banned in ("QSortFilterProxyModel(", "QListWidget(", "QTableWidget("):
        assert banned not in code
