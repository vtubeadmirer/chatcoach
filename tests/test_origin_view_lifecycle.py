"""출처 판정 화면의 수명·연결 시험 (app/ui/origin_view.py, 08 D-18 10).

- 부모 없이 만든 화면이 이름이 사라지면 바로 지워지는가(자기를 잡은 람다 연결이 없나). 남으면 프로세스 끝에
  QApplication 보다 늦게 지워지며 접근 위반(0xC0000005·0xC0000409)이 난다.
- 람다를 걷어 낸 뒤에도 버튼·단축키가 같은 일을 하는가.
- 재생기를 받은 채 여러 건을 판정할 때 재생기를 다시 열지 않고(load 없음), 반복은 대상마다 한 번만 건다.

합성 채팅(본문 'o…' 자리표시)과 합성 ID 만 쓴다. 화면 시험은 QT_QPA_PLATFORM=offscreen.
"""
import gc
import os
import sys
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import origin as og  # noqa: E402
from app.ui import origin_view as ov  # noqa: E402

SID = "L01-OR-20261022-e5f6"
CREATED = "2026-10-22T15:00:00+09:00"


def cid(i):
    return "c%020x" % (0x5000 + i)


def targets_obj(n=3):
    return og.build_targets(chat_ids=[cid(i) for i in range(n)], created_at=CREATED, created_by="L01", basis="manual")


def synth_messages(n=4):
    return [{"chat_id": cid(i), "t_ms": 60_000 + i * 5_000, "kind": "text", "viewer_id": "vw_synthsynth" + "z",
             "is_anonymous": False, "text": f"o{i}"} for i in range(n)]


class Save:
    def __init__(self):
        self.drafts = []

    def __call__(self, d):
        self.drafts.append(d)
        return {"kind": "origin", "session_id": SID, "entity_id": d.get("entity_id") or f"e{len(self.drafts)}",
                "id": f"r{len(self.drafts)}", "supersedes": d.get("supersedes"), "body": d["body"]}


class Player:
    """MpvPlayer 계약 모양의 가짜 재생기. 부른 것을 적는다."""

    def __init__(self):
        self.calls = []

    def load(self, path):
        self.calls.append(("load", path))
        return True

    def set_ab_loop(self, a, b):
        self.calls.append(("loop", a, b))

    def seek(self, ms, exact=True):
        self.calls.append(("seek", ms))

    def play(self):
        self.calls.append(("play",))

    def position_ms(self):
        return 61_000


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def _noop():
    return None


def make(player=None, n=3):
    save = Save()
    v = ov.OriginView(save, _noop)
    v.load_context(session_kind="origin_review", session_id=SID, targets=[targets_obj(n)],
                   messages=synth_messages(), player=player)
    return v, save


def test_view_is_freed_when_dropped(qapp):
    def build():
        v, _save = make(Player())
        v.origin_btns["real"].click()
        v.loop_btn.click()
        return weakref.ref(v)

    r = build()
    gc.collect()
    assert r() is None
    assert not any(isinstance(w, ov.OriginView) for w in QApplication.topLevelWidgets())


def test_buttons_and_shortcuts_still_work(qapp):
    v, save = make()
    v.origin_btns["virtual"].click()
    assert v.origin() == "virtual"
    v.shortcuts["Ctrl+1"].activated.emit()                     # 실제
    assert v.origin() == og.ORIGINS[0]
    v.shortcuts["Alt+2"].activated.emit()
    assert v.confidence() == og.CONFIDENCES[1]
    v.time_btn.click()
    assert v.rationale.text()
    first = v.current_item().chat_id
    v.save_btn.click()
    assert len(save.drafts) == 1 and v.current_item().chat_id != first
    v.prev_btn.click()
    assert v.current_item().chat_id == first
    v.next_btn.click()
    assert v.current_item().chat_id != first
    v.pending_btn.click()
    assert v.current_item() is not None


def test_player_not_reopened_and_one_loop_per_item(qapp):
    p = Player()
    v, save = make(p, n=3)
    for _ in range(3):
        v.set_origin("real")
        v.set_confidence("certain")
        v.rationale.setText("00:01:00 합성 근거")
        assert v.save_current() is not None
    assert not [c for c in p.calls if c[0] == "load"]          # 재생기를 다시 열지 않는다
    loops = [c for c in p.calls if c[0] == "loop"]
    assert 1 <= len(loops) <= 3                                 # 들어간 대상마다 한 번
