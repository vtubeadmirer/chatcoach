"""작업대·프로젝트 화면 시험 (app/ui/workbench.py, app/ui/project_view.py, app/ui/mainwindow.py 의 화면 전환).

화면 없이(QT_QPA_PLATFORM=offscreen) 합성 프로젝트(합성 채팅 100건, 영상 없음)를 열어 본다. 실제 채팅·닉네임은 쓰지 않는다.
부품(플레이어·채팅 패널·타임라인)은 공통 계약 모양의 가짜를 넣어 호출을 기록한다. 진짜 부품 시험은 import 가 되면 돈다.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from app import project as project_mod  # noqa: E402
from app.core import timecode as tc  # noqa: E402
from app.ui import project_view as pv  # noqa: E402
from app.ui import workbench as wbm  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 9, 0, 0, tzinfo=KST)
N_CHATS = 100
CHAT_STEP_MS = 1_500
OFFSET_MS = 2_500          # 영상 파일 시각 = VOD 시각 + 2.5초


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


# --------------------------------------------------------------------------- 가짜 부품 (02 3-3 계약 모양)
class FakePlayer(QLabel):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playingChanged = Signal(bool)
    errorOccurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__("player", parent)
        self.calls = []

    def load(self, path): self.calls.append(("load", path)); return True
    def play(self): self.calls.append(("play",))
    def pause(self): self.calls.append(("pause",))
    def toggle(self): self.calls.append(("toggle",))
    def seek(self, file_ms, exact=True): self.calls.append(("seek", file_ms, exact))
    def position_ms(self): return 0
    def duration_ms(self): return 0
    def frame_step(self, n): self.calls.append(("frame_step", n))
    def set_speed(self, x): pass
    def set_volume(self, v): pass
    def set_ab_loop(self, a_ms, b_ms): pass
    def close_player(self): self.calls.append(("close",))


class FakeChat(QLabel):
    chatActivated = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__("chat", parent)
        self.records, self.viewer_no, self.times, self.filters = None, None, [], []

    def set_messages(self, records, viewer_no=None): self.records, self.viewer_no = records, viewer_no
    def set_time_ms(self, vod_ms, follow=True): self.times.append((vod_ms, follow))
    def set_filter(self, text): self.filters.append(text)
    def selected_chat_id(self): return None
    def select_chat(self, chat_id): pass


class FakeTimeline(QLabel):
    seekRequested = Signal(int)
    viewChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__("timeline", parent)
        self.duration, self.positions, self.density, self.wave, self.markers = 0, [], None, None, {}
        self.view = None

    def set_duration(self, ms): self.duration = ms
    def set_position(self, ms): self.positions.append(ms)
    def set_view(self, start_ms, end_ms): self.view = (start_ms, end_ms)
    def zoom(self, factor, anchor_ms): pass
    def set_segments(self, items): self.segments = list(items)
    def set_density(self, counts, bucket_ms): self.density = (list(counts), bucket_ms)
    def set_waveform(self, values, span_ms): self.wave = (list(values), span_ms)
    def set_markers(self, name, items): self.markers[name] = list(items)
    def clear_markers(self, name): self.markers.pop(name, None)


FAKE_PARTS = {"player": FakePlayer, "chat": FakeChat, "timeline": FakeTimeline}


# --------------------------------------------------------------------------- 합성 프로젝트
def _synthetic_messages(n=N_CHATS):
    out = []
    for i in range(n):
        anon = i % 10 == 9
        out.append({
            "chat_id": f"c{i:04d}", "t_ms": i * CHAT_STEP_MS, "server_time_ms": 1_760_000_000_000 + i * CHAT_STEP_MS,
            "type_code": 1, "kind": "text", "status": "normal",
            "viewer_id": "anon" if anon else f"vw_test{i % 7:08d}", "is_anonymous": anon,
            "nickname_masked": None if anon else "가**", "role": "viewer",
            "text": f"합성 채팅 {i}", "raw_ref": {"fetch_id": "F20261021-test", "page": 0, "index": i},
        })
    return out


def make_project(tmp_path, *, verified=True, offset_ms=OFFSET_MS, n_chats=N_CHATS, shuffle=False):
    proj = project_mod.create(str(tmp_path / "projects"), "합성 방송", "1234", labeler_id="L01",
                              created_app_version="0.0.0", now=NOW, salt=bytes(range(32)),
                              sample_seed="0" * 32, engine_author=False, machine4="test",
                              duration_ms=n_chats * CHAT_STEP_MS + 5_000)
    msgs = _synthetic_messages(n_chats)
    if shuffle:
        msgs = msgs[1::2] + msgs[0::2]
    with open(proj.paths.messages_jsonl, "wb") as f:
        for m in msgs:
            f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
    cal = tc.Calibration(offset_ms=offset_ms, rev=1, status="verified" if verified else "unverified",
                         created_at=NOW.isoformat(), created_by="L01")

    def mutate(d):
        d["calibration"] = tc.append_rev(d["calibration"], tc.calibration_to_json(cal))
    proj.update(mutate, now=NOW, by="L01")
    return proj


def make_wb(qapp, proj, **kw):
    wb = wbm.Workbench(parts=FAKE_PARTS, **kw)
    wb.resize(1366, 768)
    wb.show()
    wb.open_project(proj, sync=True)
    qapp.processEvents()
    return wb


# --------------------------------------------------------------------------- 순수 도우미
def test_chat_density_and_viewer_numbers():
    msgs = _synthetic_messages()
    d = wbm.chat_density(msgs, 10_000, 0)
    assert sum(d) == N_CHATS
    assert len(d) == -(-((N_CHATS - 1) * CHAT_STEP_MS + 1) // 10_000)
    assert d[0] == 7                      # 0, 1.5, … 9.0 초
    d2 = wbm.chat_density(msgs, 10_000, 1_000_000)
    assert len(d2) == 100 and sum(d2) == N_CHATS
    vno = wbm.viewer_numbers(msgs)
    assert len(vno) == 7 and "anon" not in vno
    assert vno["vw_test00000000"] == 1 and vno["vw_test00000001"] == 2


def test_align_waveform_shifts_to_vod_axis():
    vals = [0.1, 0.2, 0.3, 0.4]           # 파일 0~4초, 점 하나 1초
    out, span = wbm.align_waveform(vals, 4_000, 2_000)     # 파일 0초 = VOD 2초
    assert out == [0.0, 0.0, 0.1, 0.2, 0.3, 0.4] and span == 6_000
    out, span = wbm.align_waveform(vals, 4_000, -1_000)    # 파일 1초 = VOD 0초
    assert out == [0.2, 0.3, 0.4] and span == 3_000
    assert wbm.align_waveform([], 0, 0) == ([], 0)


def test_engine_markers_and_link_file(tmp_path):
    obj = {"time_axis": "vod_ms", "reactions": [
        {"result": "graded", "grade": "direct", "reaction_time_ms": 5_000},
        {"result": "unconfirmed", "grade": None, "reaction_time_ms": None},
        {"result": "graded", "grade": "likely", "reaction_time_ms": 1_000},
    ]}
    assert wbm.engine_markers(obj) == [(1_000, "likely"), (5_000, "direct")]
    with pytest.raises(ValueError):
        wbm.engine_markers({"time_axis": "file_ms", "reactions": []})
    assert wbm.find_link_file(str(tmp_path)) is None
    (tmp_path / "link-v0.json").write_text("{}", encoding="utf-8")
    (tmp_path / "link-v0-review.csv").write_text("", encoding="utf-8")
    later = tmp_path / "link-v02.json"
    later.write_text("{}", encoding="utf-8")
    os.utime(later, (2_000_000_000, 2_000_000_000))
    assert os.path.basename(wbm.find_link_file(str(tmp_path))) == "link-v02.json"


def test_visibility_gates():
    pytest.importorskip("app.core.visibility")
    assert wbm.analysis_view_allowed("analysis", None) is True
    for sk in ("primary", "relabel_same", "relabel_second", None, "nonsense"):
        assert wbm.analysis_view_allowed(sk, None) is False
    assert wbm.may_write_media(None, None) is True
    assert wbm.may_write_media("primary", "segments") is True
    assert wbm.may_write_media("relabel_same", None) is False


# --------------------------------------------------------------------------- 작업대 (가짜 부품)
def test_layout_and_initial_state(qapp, tmp_path):
    proj = make_project(tmp_path)
    wb = make_wb(qapp, proj)
    try:
        # 왼쪽 위 플레이어, 아래 타임라인, 오른쪽 채팅
        assert wb.hsplit.orientation() == Qt.Horizontal and wb.vsplit.orientation() == Qt.Vertical
        assert wb.hsplit.widget(0) is wb.vsplit
        assert wb.vsplit.widget(0).isAncestorOf(wb.player)
        assert wb.vsplit.widget(1) is wb.timeline
        assert wb.hsplit.widget(1).isAncestorOf(wb.chat)
        pg = wb.player.mapTo(wb, wb.player.rect().topLeft())
        tg = wb.timeline.mapTo(wb, wb.timeline.rect().topLeft())
        cg = wb.chat.mapTo(wb, wb.chat.rect().topLeft())
        assert tg.y() > pg.y() and cg.x() > pg.x() and cg.x() > tg.x()
        # 채팅 100건, 시각 순, 시청자 번호
        assert len(wb.chat.records) == N_CHATS and len(wb.chat.viewer_no) == 7
        assert wb.timeline.density[1] == wbm.DENSITY_BUCKET_MS and sum(wb.timeline.density[0]) == N_CHATS
        assert wb.timeline.duration == N_CHATS * CHAT_STEP_MS + 5_000
        # 상태 줄
        st = wb.status_text()
        assert "VOD 00:00:00.0" in st and "채팅 100건" in st and "보정 rev 1" in st and "경고" not in st
        assert "영상 없음" in st
        assert wb.media_path is None and "연결되지 않았습니다" in wb.media_info.text()
        assert not wb.part_problems
    finally:
        wb.close_workbench()


def test_messages_are_sorted_even_if_file_is_not(qapp, tmp_path):
    proj = make_project(tmp_path, shuffle=True)
    wb = make_wb(qapp, proj)
    try:
        ts = [r["t_ms"] for r in wb.chat.records]
        assert ts == sorted(ts)
    finally:
        wb.close_workbench()


def test_time_conversion_uses_active_calibration(qapp, tmp_path):
    proj = make_project(tmp_path)
    wb = make_wb(qapp, proj)
    try:
        assert wb.vod_to_file(10_000) == 10_000 + OFFSET_MS
        assert wb.file_to_vod(10_000) == 10_000 - OFFSET_MS
        assert wb.file_to_vod(wb.vod_to_file(123_456)) == 123_456
    finally:
        wb.close_workbench()


def test_unverified_calibration_warns_in_status(qapp, tmp_path):
    proj = make_project(tmp_path, verified=False)
    wb = make_wb(qapp, proj)
    try:
        assert "경고: 보정 확인 안 됨" in wb.lbl_cal.text()
    finally:
        wb.close_workbench()


def test_chat_activation_seeks_three_seconds_before_without_media(qapp, tmp_path):
    proj = make_project(tmp_path)
    wb = make_wb(qapp, proj)
    got = []
    wb.seekIssued.connect(lambda f, v: got.append((f, v)))
    try:
        wb.chat.chatActivated.emit("c0040", 60_000)
        assert got == [(-1, 57_000)]
        assert wb.vod_position() == 57_000
        assert wb.chat.times[-1] == (57_000, True) and wb.timeline.positions[-1] == 57_000
        assert "VOD 00:00:57.0" in wb.status_text()
        wb.chat.chatActivated.emit("c0000", 1_000)          # 0 밑으로 내려가지 않는다
        assert got[-1] == (-1, 0)
        wb.timeline.seekRequested.emit(30_000)
        assert got[-1] == (-1, 30_000) and wb.vod_position() == 30_000
    finally:
        wb.close_workbench()


def test_with_media_player_gets_file_time(qapp, tmp_path, monkeypatch):
    proj = make_project(tmp_path)
    monkeypatch.setattr(wbm.Workbench, "_start_waveform", lambda self, path: None)
    wb = make_wb(qapp, proj)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\0" * 16)
    got = []
    wb.seekIssued.connect(lambda f, v: got.append((f, v)))
    try:
        wb.connect_media(str(video), persist=False)
        assert ("load", str(video)) in wb.player.calls
        # 파일 0초 = VOD −2.5초 → 화면은 그 VOD 시각
        assert wb.vod_position() == -OFFSET_MS
        wb.player.durationChanged.emit(300_000)
        assert wb.timeline.duration == 300_000 - OFFSET_MS          # 영상 끝(VOD 297.5초)이 채팅 끝보다 길어 늘어난다
        # 채팅 더블클릭 → VOD 60초 − 3초 → 파일 59.5초
        wb.chat.chatActivated.emit("c0040", 60_000)
        assert ("seek", 57_000 + OFFSET_MS, True) in wb.player.calls
        assert got[-1] == (57_000 + OFFSET_MS, 57_000)
        # 플레이어 위치 → VOD 로 바꿔 채팅·타임라인에
        wb.player.positionChanged.emit(20_000)
        assert wb.chat.times[-1] == (20_000 - OFFSET_MS, True)
        assert wb.timeline.positions[-1] == 20_000 - OFFSET_MS
        assert "파일 00:00:20.0" in wb.status_text()
        # 단축키 동작(메서드)
        wb.step(wbm.STEP_MS)
        assert wb.player.calls[-1] == ("seek", 20_000 + wbm.STEP_MS, True)
        wb.frame_step(1)
        wb.frame_step(-1)
        assert wb.player.calls[-2:] == [("frame_step", 1), ("frame_step", -1)]
        wb.toggle_play()
        assert wb.player.calls[-1] == ("toggle",)
        # 파일 끝을 넘지 않는다
        wb.seek_vod(10_000_000)
        assert wb.player.calls[-1] == ("seek", 300_000, True)
        # 파형은 VOD 축으로 옮겨 넘긴다
        wb._on_waveform(([0.5] * 300, 300_000), "")
        vals, span = wb.timeline.wave
        assert len(vals) == 300 - 2 and span == 298_000        # 앞 2.5초 ≈ 점 2~3개 잘림 (반올림 2)
    finally:
        wb.close_workbench()
    assert ("close",) in wb.player.calls


def test_shortcuts_are_registered(qapp, tmp_path):
    proj = make_project(tmp_path)
    wb = make_wb(qapp, proj)
    try:
        keys = {s.key().toString() for s in wb.shortcuts}
        for k in ("Space", "Left", "Right", ",", ".", "Ctrl+F", "J", "L", "K"):
            assert k in keys
        wb.step(wbm.STEP_MS)
        assert wb.vod_position() == wbm.STEP_MS
        wb.step(-10 * wbm.STEP_MS)
        assert wb.vod_position() == 0
        wb.focus_search()                    # 예외 없이
        wb.search.setText("합성")
        wb._apply_filter()
        assert wb.chat.filters[-1] == "합성"
    finally:
        wb.close_workbench()


def _write_link(proj):
    obj = {"schema": "chatcoach/ai-result", "time_axis": "vod_ms", "reactions": [
        {"chat_id": "c0010", "result": "graded", "grade": "direct", "reaction_time_ms": 16_000},
        {"chat_id": "c0011", "result": "unconfirmed", "grade": None, "reaction_time_ms": None},
        {"chat_id": "c0020", "result": "graded", "grade": "unclear", "reaction_time_ms": 31_000},
    ]}
    with open(os.path.join(proj.paths.derived_dir, "link-test.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f)


def test_analysis_view_only_in_allowed_session(qapp, tmp_path):
    pytest.importorskip("app.core.visibility")
    proj = make_project(tmp_path)
    _write_link(proj)
    wb = make_wb(qapp, proj, session_kind="primary")
    try:
        assert not wb.analysis_action.isVisible()
        wb.set_analysis_view(True)                      # 숨긴 메뉴를 억지로 켜도 그리지 않는다
        assert "engine" not in wb.timeline.markers
    finally:
        wb.close_workbench()
    wb = make_wb(qapp, proj, session_kind="analysis")
    try:
        assert wb.analysis_action.isVisible() and wb.analysis_action.isEnabled()
        wb.analysis_action.setChecked(True)
        assert wb.wait_jobs(10_000)
        assert wb.timeline.markers["engine"] == [(16_000, "engine"), (31_000, "engine_weak")]   # 등급 → 표시 종류
        wb.analysis_action.setChecked(False)
        assert "engine" not in wb.timeline.markers
    finally:
        wb.close_workbench()


def test_analysis_action_disabled_without_link(qapp, tmp_path):
    pytest.importorskip("app.core.visibility")
    proj = make_project(tmp_path)
    wb = make_wb(qapp, proj, session_kind="analysis")
    try:
        assert wb.analysis_action.isVisible() and not wb.analysis_action.isEnabled()
    finally:
        wb.close_workbench()


def _fake_ref(path, fp_char="a", size=16):
    return {"path_abs": os.path.normpath(path), "path_rel": None, "size": size, "mtime_ns": 1,
            "partial_fp": fp_char * 64, "full_sha256": None, "ntfs_file_id": None, "volume_serial": None,
            "probe": {"duration_ms": 300_000, "video_codec": "h264", "audio_codec": "aac", "width": 1920,
                      "height": 1080, "r_frame_rate": "60/1", "avg_frame_rate": "60/1", "vfr": False,
                      "nb_frames": None}}


def test_connect_media_persists_and_replacement_needs_reason(qapp, tmp_path, monkeypatch):
    proj = make_project(tmp_path)
    monkeypatch.setattr(wbm.Workbench, "_start_waveform", lambda self, path: None)
    v1, v2 = tmp_path / "a.mp4", tmp_path / "b.mp4"
    v1.write_bytes(b"\0" * 16)
    v2.write_bytes(b"\1" * 16)
    refs = {os.path.normpath(str(v1)): _fake_ref(v1, "a"), os.path.normpath(str(v2)): _fake_ref(v2, "b")}
    monkeypatch.setattr(wbm.media, "media_ref", lambda p, root=None: refs[os.path.normpath(p)])
    wb = make_wb(qapp, proj)
    try:
        wb.connect_media(str(v1), sync=True)
        on_disk = project_mod.open(proj.root).data["media"]
        assert on_disk["media_rev"] == 1 and on_disk["current"]["partial_fp"] == "a" * 64 and not on_disk["history"]
        assert wb.media_path == os.path.normpath(str(v1))
        # 지문이 다른 파일: 사유를 묻고, 취소하면 바꾸지 않는다
        monkeypatch.setattr(wbm.Workbench, "ask_replace_reason", lambda self: None)
        wb.connect_media(str(v2), sync=True)
        assert project_mod.open(proj.root).data["media"]["media_rev"] == 1
        wb.connect_media(str(v2), sync=True, reason="시험: 다른 화질 파일")
        m = project_mod.open(proj.root).data["media"]
        assert m["media_rev"] == 2 and m["current"]["partial_fp"] == "b" * 64
        assert m["history"][0]["media_rev"] == 1 and m["history"][0]["reason"] == "시험: 다른 화질 파일"
        assert any("시간 정렬" in n for n in wb.notes)
    finally:
        wb.close_workbench()
    # 다시 열면 연결된 영상을 찾아(verify) 연다
    monkeypatch.setattr(wbm.media, "verify", lambda ref, p: (True, None))
    wb = make_wb(qapp, project_mod.open(proj.root))
    try:
        assert wb.media_path == os.path.normpath(str(v2))
    finally:
        wb.close_workbench()


def test_missing_media_file_shows_connect_button(qapp, tmp_path):
    proj = make_project(tmp_path)
    gone = tmp_path / "gone.mp4"

    def mutate(d):
        d["media"] = {"media_rev": 1, "current": _fake_ref(gone), "history": []}
    proj.update(mutate, now=NOW, by="L01")
    wb = make_wb(qapp, proj)
    try:
        assert wb.media_path is None
        assert "찾지 못했습니다" in wb.media_info.text() and wb.btn_media.isVisible()
    finally:
        wb.close_workbench()


def test_missing_parts_fall_back_without_crash(qapp, tmp_path):
    class Broken(QWidget):
        def __init__(self, parent=None):
            raise OSError("no dll")
    proj = make_project(tmp_path)
    wb = wbm.Workbench(parts={"player": Broken, "chat": FakeChat, "timeline": FakeTimeline})
    try:
        wb.open_project(proj, sync=True)
        assert isinstance(wb.player, wbm._MissingPlayer)
        assert wb.part_problems and wb.banner.text()
        wb.chat.chatActivated.emit("c0001", 10_000)
        assert wb.vod_position() == 7_000
    finally:
        wb.close_workbench()


def test_background_load_path(qapp, tmp_path):
    """sync=False(실제 앱의 길): 워커가 끝나면 채팅이 실린다."""
    proj = make_project(tmp_path)
    wb = wbm.Workbench(parts=FAKE_PARTS)
    try:
        wb.open_project(proj)
        assert wb.wait_jobs(10_000)
        assert len(wb.chat.records) == N_CHATS
    finally:
        wb.close_workbench()


# --------------------------------------------------------------------------- 프로젝트 화면
def test_recent_list_roundtrip(qapp, tmp_path):
    s = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    assert pv.load_recent(s) == []
    for i in range(pv.MAX_RECENT + 3):
        pv.push_recent(s, str(tmp_path / f"p{i}"))
    items = pv.load_recent(s)
    assert len(items) == pv.MAX_RECENT and items[0].endswith(f"p{pv.MAX_RECENT + 2}")
    pv.push_recent(s, str(tmp_path / "p5"))
    assert pv.load_recent(s)[0].endswith("p5") and len(pv.load_recent(s)) == pv.MAX_RECENT
    pv.remove_recent(s, str(tmp_path / "p5"))
    assert not any(p.endswith("p5") for p in pv.load_recent(s))
    s2 = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    assert pv.load_recent(s2) == pv.load_recent(s)


def test_open_checked_lock_and_warnings(qapp, tmp_path):
    proj = make_project(tmp_path)
    a = pv.open_checked(proj.root)
    try:
        assert a.ok and not a.read_only and a.lock is not None
        b = pv.open_checked(proj.root)              # 같은 프로젝트를 또 열면 잠금을 못 잡아 읽기 전용
        assert b.ok and b.read_only and b.lock is None
        assert any("열고 있습니다" in m for m in b.messages())
    finally:
        pv.release_outcome(a)
    c = pv.open_checked(proj.root)
    assert not c.read_only
    pv.release_outcome(c)
    bad = pv.open_checked(str(tmp_path / "nothing"))
    assert not bad.ok


def test_open_warns_onedrive(qapp, tmp_path, monkeypatch):
    proj = make_project(tmp_path)
    monkeypatch.setenv("OneDrive", str(tmp_path))
    out = pv.open_checked(proj.root, lock_factory=None)
    assert out.ok and any("OneDrive" in w for w in out.warnings)


def test_project_view_open_emits_and_records_recent(qapp, tmp_path):
    proj = make_project(tmp_path)
    s = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    view = pv.ProjectView(s, lock_factory=None)
    got = []
    view.projectOpened.connect(got.append)
    view.choose_folder = lambda: proj.root
    out = view.choose_and_open()
    assert got and got[0] is out and out.ok
    assert pv.load_recent(s) == [proj.root] and view.list.count() == 1
    view.open_path(str(tmp_path / "nothing"))
    assert len(got) == 1 and "열지 못했습니다" in view.msg.text()


def test_mainwindow_switches_screens(qapp, tmp_path, monkeypatch):
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (FAKE_PARTS[kind], None))
    proj = make_project(tmp_path)
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    win = mw.MainWindow(SPEC, inst, str(tmp_path / "data"), str(tmp_path / "log.txt"), None, [], set())
    try:
        assert win.stack.currentWidget() is win.home_page
        out = win.project_view.open_path(proj.root)
        assert out.ok and win.workbench is not None and win.stack.currentWidget() is win.workbench
        assert win.workbench.wait_jobs(10_000)
        assert len(win.workbench.chat.records) == N_CHATS
        assert not win.m_view.menuAction().isVisible()       # 세션 없음: 분석 보기 숨김
        assert win.a_close_project.isEnabled()
        win._fill_recent_menu()
        assert win.m_recent.actions()[0].toolTip() == proj.root
        win.show_home()
        assert win.workbench is None and win.stack.currentWidget() is win.home_page
        # 잠금이 풀렸다: 다시 열면 읽기 전용이 아니다
        again = pv.open_checked(proj.root)
        assert not again.read_only
        pv.release_outcome(again)
    finally:
        win.close()


# --------------------------------------------------------------------------- 진짜 부품 (있으면)
def test_with_real_parts_if_available(qapp, tmp_path):
    for mod in ("app.player", "app.ui.chat_panel", "app.ui.timeline"):
        pytest.importorskip(mod)
    proj = make_project(tmp_path)
    wb = wbm.Workbench()
    wb.resize(1366, 768)
    wb.show()
    try:
        wb.open_project(proj, sync=True)
        qapp.processEvents()
        assert type(wb.chat).__name__ == "ChatPanel" and type(wb.timeline).__name__ == "Timeline"
        assert not any("채팅 패널" in p or "타임라인" in p for p in wb.part_problems)
        if hasattr(wb.chat, "total_count"):
            assert wb.chat.total_count() == N_CHATS
        assert wb.search is getattr(wb.chat, "search", wb.search)
        got = []
        wb.seekIssued.connect(lambda f, v: got.append((f, v)))
        wb.chat.chatActivated.emit("c0040", 60_000)
        assert got[-1] == (-1, 57_000)
        wb.timeline.seekRequested.emit(12_345)
        assert wb.vod_position() == 12_345
        pos = getattr(wb.timeline, "position", None)
        if pos is not None:
            assert (pos() if callable(pos) else pos) == 12_345
    finally:
        wb.close_workbench()


# --------------------------------------------------------------------------- 라벨 기록(store) 연동
MID_TEST = "0f3a9c2e-5b7d-4e1f-8a6b-1c2d3e4f5a6b"


def _session():
    from app import session as sess
    return sess.SessionContext("L01", "primary", "L01-P-20261021-ab12", sess.machine4(MID_TEST), machine_id=MID_TEST,
                               session_created_at="2026-10-21T09:00:00+09:00")


def test_store_opens_with_session_and_takes_over_lock(qapp, tmp_path):
    pytest.importorskip("app.store")
    proj = make_project(tmp_path)
    out = pv.open_checked(proj.root)
    assert out.lock is not None and not out.read_only
    wb = wbm.Workbench(parts=FAKE_PARTS, session=_session(), store_options={"app_version": "0.0.0"})
    try:
        assert wb.session_kind == "primary" and wb.labeler_id == "L01"
        wb.open_project(out, sync=True)
        qapp.processEvents()
        assert wb.store is not None, wb.notes
        assert not wb.store.read_only, wb.store.read_only_reasons    # 넘겨받은 잠금으로 열었다(두 번 잡지 않음)
        assert out.lock is None                                         # 잠금은 store 가 가진다
        assert pv.open_checked(proj.root).read_only                     # 다른 창은 여전히 못 잡는다
        assert "L01-P-20261021-ab12" in wb.status_text()
        rec = wb.save_label({"kind": "segment", "body": {"start_ms": 0, "end_ms": 60_000, "category": "just_chatting",
                                                         "confidence": "certain", "rationale": "합성 구간"}})
        assert rec["kind"] == "segment"
        assert [(r["body"]["start_ms"], r["body"]["end_ms"]) for r in wb.timeline.segments] == [(0, 60_000)]
        wb.undo_last()
        assert wb.timeline.segments == []
    finally:
        wb.close_workbench()
    again = pv.open_checked(proj.root)                                  # 닫으면 잠금이 풀린다
    assert not again.read_only
    pv.release_outcome(again)


def test_store_background_open_and_no_session_means_no_store(qapp, tmp_path):
    pytest.importorskip("app.store")
    proj = make_project(tmp_path)
    wb = make_wb(qapp, proj)
    assert wb.store is None and wb.lbl_session.text() == ""
    wb.close_workbench()
    out = pv.open_checked(proj.root)
    wb2 = wbm.Workbench(parts=FAKE_PARTS, session=_session(), store_options={"app_version": "0.0.0"})
    try:
        wb2.open_project(out)                                           # 워커로 연다
        assert wb2.wait_jobs(15_000)
        assert wb2.store is not None and not wb2.store.read_only
    finally:
        wb2.close_workbench()
    assert wb2.store is None
    again = pv.open_checked(proj.root)
    assert not again.read_only
    pv.release_outcome(again)


# --------------------------------------------------------------------------- 검토 수정 재현 시험
def _seg(start, end):
    return {"kind": "segment", "body": {"start_ms": start, "end_ms": end, "category": "just_chatting",
                                        "confidence": "certain", "rationale": "합성 구간"}}


def test_media_link_goes_through_store_and_labels_get_new_media_rev(qapp, tmp_path, monkeypatch):
    """작업대가 project.json 을 따로 쓰면 store 가 옛 media_rev 를 라벨에 붙이고 update_project 가 Conflict 였다."""
    pytest.importorskip("app.store")
    proj = make_project(tmp_path)
    monkeypatch.setattr(wbm.Workbench, "_start_waveform", lambda self, path: None)
    v1 = tmp_path / "a.mp4"
    v1.write_bytes(b"\0" * 16)
    monkeypatch.setattr(wbm.media, "media_ref", lambda p, root=None: _fake_ref(v1, "a"))
    out = pv.open_checked(proj.root)
    wb = wbm.Workbench(parts=FAKE_PARTS, session=_session(), store_options={"app_version": "0.0.0"})
    try:
        wb.open_project(out, sync=True)
        assert wb.store is not None and not wb.store.read_only
        wb.connect_media(str(v1), sync=True)
        disk = project_mod.open(proj.root).data
        assert disk["media"]["media_rev"] == 1
        assert wb.project.data["rev"] == disk["rev"] == wb.store.project.data["rev"]
        rec = wb.save_label(_seg(0, 60_000))
        assert rec["media_rev"] == 1 and rec["calibration_rev"] == disk["calibration"]["active_rev"]
        wb.store.update_project(lambda d: d.__setitem__("notes", "합성 메모"))     # ConflictError 가 나지 않는다
    finally:
        wb.close_workbench()


def test_reopening_same_project_from_main_window_is_writable(qapp, tmp_path, monkeypatch):
    """같은 프로젝트를 다시 열면(Ctrl+O, 최근 목록) 옛 작업대의 잠금 때문에 늘 읽기 전용이던 문제."""
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    monkeypatch.setattr(wbm, "_default_part", lambda kind: (FAKE_PARTS[kind], None))
    proj = make_project(tmp_path)
    other = make_project(tmp_path / "other")
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=str(tmp_path),
                                 code_dir=str(tmp_path), frozen=False)
    win = mw.MainWindow(SPEC, inst, str(tmp_path / "data"), str(tmp_path / "log.txt"), None, [], set())
    try:
        first = win.project_view.open_path(proj.root)
        assert not first.read_only
        win.session = _session()                                       # 세션이 있으면 store 도 같은 잠금 상태여야 한다
        second = win.project_view.open_path(proj.root)
        assert not second.read_only, second.project.problems
        assert not win.workbench.project.read_only
        assert win.workbench.wait_jobs(15_000)
        assert win.workbench.store is not None and not win.workbench.store.read_only
        # 다른 프로젝트를 열 때는 앞 작업대를 미리 닫지 않는다(열기가 실패해도 작업대를 잃지 않게)
        win.session = None
        bad = win.project_view.open_path(str(tmp_path / "없는 폴더"))
        assert not bad.ok and win.workbench is not None
        third = win.project_view.open_path(other.root)
        assert third.ok and not third.read_only
    finally:
        win.close()
    again = pv.open_checked(proj.root)
    assert not again.read_only
    pv.release_outcome(again)


def test_load_chat_drops_dict_rows_when_panel_data_is_built(tmp_path):
    proj = make_project(tmp_path)
    built = []
    res = wbm.load_chat(proj.paths.messages_jsonl, 0, lambda recs, vno: built.append(len(recs)) or "panel")
    assert res.panel_data == "panel" and built == [N_CHATS]
    assert res.records == [] and res.count == N_CHATS and sum(res.density) == N_CHATS
    plain = wbm.load_chat(proj.paths.messages_jsonl, 0, None)            # 가짜 부품 길: 목록을 그대로 넘긴다
    assert len(plain.records) == N_CHATS and plain.count == N_CHATS


def test_workbench_keeps_no_chat_dicts_with_real_panel(qapp, tmp_path):
    pytest.importorskip("app.ui.chat_panel")
    proj = make_project(tmp_path)
    wb = wbm.Workbench(parts={"player": FakePlayer, "timeline": FakeTimeline})
    try:
        wb.open_project(proj, sync=True)
        assert type(wb.chat).__name__ == "ChatPanel"
        assert wb.chat_data.records == [] and wb.chat_data.count == N_CHATS
        assert f"{N_CHATS:,}" in wb.lbl_chats.text()
    finally:
        wb.close_workbench()


def test_session_kind_must_match_session(qapp):
    with pytest.raises(ValueError):
        wbm.Workbench(parts=FAKE_PARTS, session=_session(), session_kind="analysis")
    with pytest.raises(ValueError):
        wbm.Workbench(parts=FAKE_PARTS, session=_session(), labeler_id="L02")
    wb = wbm.Workbench(parts=FAKE_PARTS, session=_session(), session_kind="primary")
    assert wb.session_kind == "primary" and not wb.analysis_action.isVisible()
    wb.close_workbench()


def test_align_waveform_fractional_shift_keeps_axis():
    vals = [1.0, 1.0, 1.0, 1.0]                      # 파일 0~4초, 점 하나 1초
    out, span = wbm.align_waveform(vals, 4_000, 500)            # 파일 0초 = VOD 0.5초
    assert out == [0.5, 1.0, 1.0, 1.0, 0.5] and span == 5_000
    out, span = wbm.align_waveform(vals, 4_000, -500)           # 파일 0.5초 = VOD 0초
    assert out == [1.0, 1.0, 1.0, 0.5] and span == 4_000
    # 실제 프로젝트와 같은 크기(숫자만): 점 폭 약 500.18ms, 이동 2475.4점. 무게 중심이 vod0 + 파일 위치에 온다
    n, dur, vod0 = 2305, 1_152_917, 1_238_150
    vals = [0.0] * n
    vals[1000] = 1.0                                             # 파일 [1000*bw, 1001*bw) 의 소리
    out, span = wbm.align_waveform(vals, dur, vod0)
    bw = dur / n
    center = sum((k + 0.5) * bw * v for k, v in enumerate(out)) / sum(out)
    assert abs(center - (vod0 + 1000.5 * bw)) < 1.0
    assert abs(span / len(out) - bw) < 1.0


def _bulk_labels(wb, n, monkeypatch):
    """fsync 를 끄고 segment n 개를 store 에 쓴다(시험 시간 줄이기). 그다음 전체 계산 한 번."""
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    for i in range(n):
        wb.store.append_label(_seg(i * 1_000, i * 1_000 + 900))
    wb.refresh_labels()


def test_save_label_updates_only_changed_entity_and_matches_full_resolve(qapp, tmp_path, monkeypatch):
    pytest.importorskip("app.store")
    proj = make_project(tmp_path)
    out = pv.open_checked(proj.root)
    wb = wbm.Workbench(parts=FAKE_PARTS, session=_session(), store_options={"app_version": "0.0.0"})
    try:
        wb.open_project(out, sync=True)
        _bulk_labels(wb, 50, monkeypatch)
        calls = []
        orig = wb.store.effective
        monkeypatch.setattr(wb.store, "effective", lambda **kw: calls.append(kw) or orig(**kw))
        a = wb.save_label(_seg(100_000, 110_000))
        b = wb.save_label({**_seg(100_000, 105_000), "entity_id": a["entity_id"], "supersedes": a["id"]})
        wb.undo_last()                                           # b 를 되돌림 → a 의 body 로 새 판
        wb.undo_last()                                           # 되돌리기를 되돌림
        wb.undo_last()
        assert calls == []                                       # 저장마다 전체 resolve 를 하지 않았다
        got = sorted((r["entity_id"], r["id"]) for r in wb.timeline.segments)
        full = sorted((r["entity_id"], r["id"]) for r in orig(copy_records=True).items("segment"))
        assert got == full and len(got) == 51
        assert b["id"] not in {i for _, i in got}
        # 철회도 반영된다
        wb.save_label(_seg(200_000, 210_000))
        wb.undo_last()                                           # 새로 만든 대상 → retract
        assert sorted((r["entity_id"], r["id"]) for r in wb.timeline.segments) == \
            sorted((r["entity_id"], r["id"]) for r in orig(copy_records=True).items("segment"))
        assert calls == []
    finally:
        wb.close_workbench()


def test_save_label_ui_cost_does_not_grow_with_label_count(qapp, tmp_path, monkeypatch):
    """02 8장: 저장 뒤 화면 갱신(UI 스레드)이 라벨 수에 비례해 늘지 않는다. 디스크 쓰기는 빼고 잰다."""
    import statistics
    import time
    pytest.importorskip("app.store")
    proj = make_project(tmp_path)
    out = pv.open_checked(proj.root)
    wb = wbm.Workbench(parts=FAKE_PARTS, session=_session(), store_options={"app_version": "0.0.0"})
    try:
        wb.open_project(out, sync=True)
        _bulk_labels(wb, 2_000, monkeypatch)
        ui = []
        for i in range(20):
            t0 = time.perf_counter()
            wb.save_label(_seg(5_000_000 + i * 1_000, 5_000_000 + i * 1_000 + 500))
            ui.append((time.perf_counter() - t0) * 1000 - (wb.store.last_append_ms or 0))
        med = statistics.median(ui)
        print(f"\n라벨 2,000건에서 저장 뒤 화면 갱신 중앙값 {med:.2f}ms")
        assert med < 16.0
        assert len(wb.timeline.segments) == 2_020
    finally:
        wb.close_workbench()


def test_position_ticks_do_not_restyle_or_resend_same_status(qapp, tmp_path, monkeypatch):
    proj = make_project(tmp_path, verified=False)
    wb = make_wb(qapp, proj)
    try:
        sent, styled = [], []
        wb.statusChanged.connect(sent.append)
        monkeypatch.setattr(wb.lbl_cal, "setStyleSheet", lambda s: styled.append(s))
        for _ in range(5):
            wb._set_vod(12_000)
        assert len(sent) == 1 and styled == []                   # 같은 문장은 한 번만, 보정 칩 스타일은 그대로
        wb._set_vod(13_000)
        assert len(sent) == 2 and "경고" in wb.lbl_cal.text()
    finally:
        wb.close_workbench()


def test_gui_smoke_store_copy_leaves_out_protected_zone(tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import gui_smoke
    proj = make_project(tmp_path)
    os.makedirs(os.path.join(proj.root, "derived"), exist_ok=True)
    with open(os.path.join(proj.root, "derived", "asr.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    dst = tmp_path / "copy" / "p"
    done = gui_smoke.copy_for_store_test(proj.root, str(dst))
    assert "project.json" in done
    copied = {os.path.relpath(os.path.join(r, n), dst).replace("\\", "/") for r, _, ns in os.walk(dst) for n in ns}
    assert not any(p.startswith(("chat/", "derived/", "origin/", "labels/")) for p in copied), copied
    assert gui_smoke.remove_tree_checked(str(tmp_path / "copy")) == []
    assert not (tmp_path / "copy").exists()
