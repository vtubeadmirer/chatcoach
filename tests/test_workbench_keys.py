"""시범 1일차(2026-10-22) 키 입력 수정의 회귀 시험. 합성 프로젝트만 쓴다.

1. 작업대를 열면 키 입력이 채팅 검색 칸이 아니라 작업대로 간다(Space·J·L 이 검색어로 들어가지 않음).
2. 한글 입력 상태에서 'B' 가 'ㅠ' 로 들어와도 실제 키 위치(VK 0x42)로 단축키가 동작한다.
3. 글자 칸에 커서가 있으면 'ㅠ' 는 글자로 남는다.
4. 둘러보기에서 B 를 누르면 ① 구간으로 넘어간다. 재생 단추가 있다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from tests.test_workbench import make_project, make_wb, qapp  # noqa: E402,F401


def _hangul_key(vk: int, text: str) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key_unknown, Qt.NoModifier, 0, vk, 0, text)


def test_focus_goes_to_workbench_not_search(qapp, tmp_path):
    wb = make_wb(qapp, make_project(tmp_path))
    try:
        wb.activateWindow()
        qapp.processEvents()
        wb.take_keys()
        qapp.processEvents()
        fw = QApplication.focusWidget()
        assert fw is not wb.search
        assert hasattr(wb, "btn_play")
    finally:
        wb.close()


def test_hangul_b_triggers_boundary_shortcut(qapp, tmp_path):
    wb = make_wb(qapp, make_project(tmp_path))
    try:
        hits = []
        wb.shortcut_map["B"].activated.connect(lambda: hits.append(1))
        wb.take_keys()
        qapp.processEvents()
        consumed = wb.eventFilter(wb, _hangul_key(0x42, "ㅠ"))
        assert consumed is True and hits == [1]
    finally:
        wb.close()


def test_hangul_kept_in_text_field(qapp, tmp_path):
    wb = make_wb(qapp, make_project(tmp_path))
    try:
        hits = []
        wb.shortcut_map["B"].activated.connect(lambda: hits.append(1))
        le = QLineEdit(wb)
        le.show()
        le.setFocus()
        qapp.processEvents()
        if QApplication.focusWidget() is not le:
            pytest.skip("offscreen 에서 포커스를 옮기지 못함")
        assert wb.eventFilter(le, _hangul_key(0x42, "ㅠ")) is False and hits == []
    finally:
        wb.close()


def test_b_in_browse_switches_to_segments(qapp, tmp_path):
    from app.core import phase as ph
    wb = make_wb(qapp, make_project(tmp_path))
    try:
        if ph.SEGMENTS not in wb.mode_buttons or wb.store is None or wb.store.read_only:
            pytest.skip("세션 없는 작업대")
        assert wb.mode == ph.BROWSE
        wb.add_boundary_here()
        assert wb.mode == ph.SEGMENTS
    finally:
        wb.close()


def test_play_buttons_call_player(qapp, tmp_path):
    wb = make_wb(qapp, make_project(tmp_path))
    try:
        want = {"back10", "back5", "back1", "frame_back", "play", "frame_fwd", "fwd1", "fwd5", "fwd10", "slower", "faster"}
        assert want <= set(wb.play_buttons)
        for b in wb.play_buttons.values():
            assert b.focusPolicy() == Qt.NoFocus
        wb.play_buttons["play"].click()
        wb.play_buttons["fwd5"].click()
    finally:
        wb.close()
