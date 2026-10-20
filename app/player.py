# vendored from vtubeadmirer/clipslicer@07d13cc (app/player.py) — 변경: S-26 mpv 옵션(ytdl·sub_auto·audio_file_auto·load_scripts·config 끔, 내장 스크립트 끔), app.paths.import_mpv() 로 지연 import(PATH 고정), libmpv 없으면 errorOccurred, 공통 계약 API(ms 정수·A-B·프레임 n칸), 위치는 40ms QTimer 로 UI 스레드에서 보냄, 경로 문자열 검사
"""libmpv 를 Qt 위젯에 임베드한 플레이어 (docs/design/02-architecture.md 3장 player, 7-3, 8장, 10장).

공통 계약:
    MpvPlayer(QWidget)
      load(path) -> bool, play(), pause(), toggle(), seek(file_ms, exact=True), position_ms(), duration_ms(),
      frame_step(n), set_speed(x), set_volume(v), set_ab_loop(a_ms|None, b_ms|None), close_player()
      Signals: positionChanged(int ms), durationChanged(int ms), playingChanged(bool), errorOccurred(str)
    시각은 모두 '영상 파일 시각' 정수 ms 다. VOD(채팅) 시각으로 바꾸는 것은 부르는 쪽(timecode)이 한다.

스레드 (02 8장):
    mpv 의 속성 관찰·이벤트 콜백은 mpv 이벤트 스레드에서 불린다. 콜백은 값을 평범한 속성에 적고
    내부 Signal(_evt) 하나만 보낸다(QueuedConnection). 위젯 상태와 공개 Signal 은 모두 UI 스레드에서 바뀐다.
    positionChanged 는 UI 스레드의 QTimer(POLL_MS) 가 바뀐 때만 보낸다 — 프레임마다 보내지 않는다.

안전 (S-26, 10장):
    - mpv 생성 옵션은 이 파일의 _OPTIONS_REQUIRED·_OPTIONS_EXTRA 한 곳에서만 정한다.
    - load() 는 app.media.check_path_string 으로 URL·UNC·장치 경로를 파일 시스템에 닿기 전에 거부하고,
      재생 목록류 확장자(.m3u8 등 — 안에 적힌 http·다른 파일을 연다)도 거부한다.
    - libmpv-2.dll·python-mpv 가 없거나 mpv 생성이 실패하면 예외를 밖으로 던지지 않는다.
      available() 이 False 가 되고, errorOccurred 를 다음 이벤트 루프에서 보낸다(생성 직후 연결해도 받게).
      그 뒤의 모든 메서드는 아무 일도 하지 않는다.
    - 창이 숨겨지거나(hide)·아직 보이지 않았거나·close_player() 뒤에 불러도 예외가 나지 않는다.

화면이 없는 플랫폼(QT_QPA_PLATFORM=offscreen/minimal)에서는 진짜 창 핸들이 없으므로 wid 를 넘기지 않고
vo=null·ao=null 로 만든다(디코드·탐색·프레임 이동은 그대로 동작한다). 테스트가 이 경로를 쓴다.

ClipSlicer 와 다른 점: 시각 단위(초 float → ms int), pauseChanged → playingChanged, set_loop → set_ab_loop(ms),
frame_step(forward) → frame_step(n) (mpv 0.40+ 의 'frame-step <n>' 한 번, 안 되면 한 칸씩),
load 전에 부른 seek 는 기억했다가 파일이 열리면 적용, Qt 객체에 __del__ 없음.
"""
from __future__ import annotations

import locale
import logging
import os
import time

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

POLL_MS = 40  # 위치 Signal 간격 (30~60ms 범위, 02 9장)

# S-26 ② + 02 10장. 생성할 때 꼭 넣는다 (config·load_scripts 는 초기화 전에만 의미가 있다).
_OPTIONS_REQUIRED = {
    "ytdl": "no",
    "sub_auto": "no",
    "audio_file_auto": "no",
    "load_scripts": "no",
    "config": "no",
    "osc": "no",
    "input_default_bindings": "no",
    "input_vo_keyboard": "no",
    "input_cursor": "no",
    "keep_open": "yes",
    "hr_seek": "yes",
    "hr_seek_framedrop": "no",
    "pause": "yes",
    "idle": "yes",
    "volume": "100",
    "audio_pitch_correction": "yes",
}
# 내장 스크립트·참조 따라가기 끔. libmpv 판에 따라 없는 이름일 수 있어, 실패하면 이것만 빼고 다시 만든다.
_OPTIONS_EXTRA = {
    "input_builtin_bindings": "no",
    "load_stats_overlay": "no",
    "load_osd_console": "no",
    "load_auto_profiles": "no",
    "load_select": "no",
    "load_commands": "no",
    "load_positioning": "no",
    "access_references": "no",
}
# 안에 적힌 다른 주소·파일을 여는 형식 (S-26: 로컬 경로 검사를 우회한다)
SEEK_SETTLE_MS = 250     # 정확 탐색이 끝났다고 볼 거리 (목표와 실제 위치 차)
SEEK_GIVEUP_S = 3.0      # 이 시간이 지나도 끝나지 않으면 목표값을 버리고 실제 위치를 보인다
_PLAYLIST_EXTS = {".m3u", ".m3u8", ".pls", ".cue", ".edl", ".asx", ".xspf", ".strm", ".txt", ".url", ".lnk"}

# libmpv 이벤트 번호 (client.h 의 mpv_event_id·mpv_end_file_reason, python-mpv MpvEventID 와 같다)
_EV_SHUTDOWN, _EV_END_FILE, _EV_FILE_LOADED, _EV_SEEK, _EV_PLAYBACK_RESTART = 1, 7, 8, 20, 21
_END_REASON_ERROR = 4

_HEADLESS_PLATFORMS = {"offscreen", "minimal", "vnc"}

_mpv_module = None
_mpv_import_error: str | None = None


def _load_mpv_module():
    """python-mpv 모듈 (app.paths.import_mpv — import 동안 PATH 를 libmpv 폴더 하나로). 실패하면 None 과 사유."""
    global _mpv_module, _mpv_import_error
    if _mpv_module is not None:
        return _mpv_module, None
    try:
        from .paths import import_mpv
        _mpv_module = import_mpv()
        _mpv_import_error = None
    except Exception as e:  # FileNotFoundError(DLL 없음)·ImportError(python-mpv 없음)·OSError(DLL 로드 실패)
        _mpv_import_error = f"영상 재생 모듈(libmpv)을 불러오지 못했습니다: {type(e).__name__}: {e}"
        return None, _mpv_import_error
    return _mpv_module, None


def check_media_path(path) -> str | None:
    """load() 가 받아들이는 경로인가. 받아들이면 None, 아니면 사유. 파일 시스템은 존재 확인만 한다(문자열 검사 뒤)."""
    from .media import check_path_string
    why = check_path_string(path)
    if why:
        return why
    ext = os.path.splitext(path)[1].lower()
    if ext in _PLAYLIST_EXTS:
        return f"재생 목록·바로 가기 형식({ext})은 열지 않습니다"
    if not os.path.isfile(path):
        return "영상 파일이 없습니다"
    return None


class MpvPlayer(QWidget):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playingChanged = Signal(bool)
    errorOccurred = Signal(str)
    fileLoaded = Signal()       # 계약 밖 보조: 파일이 열려 탐색할 수 있게 됨
    clicked = Signal()          # 계약 밖 보조: 영상 위 왼쪽 클릭

    _evt = Signal(str, object)  # mpv 이벤트 스레드 → UI 스레드

    def __init__(self, parent=None, compat: bool = False, headless: bool | None = None):
        """compat=True 면 GPU 가속 없이 그리는 호환 모드(원격 데스크톱·가상머신·구형 GPU).

        headless=None 이면 Qt 플랫폼이 offscreen/minimal 일 때 자동으로 켠다(wid 없이 vo=null·ao=null).
        """
        super().__init__(parent)
        self.compat = compat
        if headless is None:
            headless = QGuiApplication.platformName() in _HEADLESS_PLATFORMS
        self.headless = headless
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0))
        self.setPalette(pal)
        self.setMinimumSize(320, 180)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # 상태 (UI 스레드에서만 바꾼다. _raw_* 는 이벤트 스레드가 적는 값)
        self._mpv = None
        self._error: str | None = None
        self._closed = False
        self._loaded = False
        self._path: str | None = None
        self._duration_ms = 0
        self._playing = False
        self._raw_pos: float | None = None
        self._seek_target_ms: int | None = None   # 탐색 중이면 목표값 (position_ms 가 이 값을 돌려준다)
        self._pending_seek: tuple[int, bool] | None = None  # 파일이 열리기 전에 부른 seek
        self._seek_exact = True
        self._seek_seen = False      # 마지막 seek() 뒤 mpv SEEK 이벤트를 받았나
        self._seek_started = 0.0
        self._last_emitted_pos = -1
        self._ab: tuple[int | None, int | None] = (None, None)
        self._speed = 1.0
        self._volume = 100

        self._evt.connect(self._on_evt, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(lambda: self._poll())   # 람다로 감싼다 (02 8장)

        self._create_mpv()

    # ---------------------------------------------------------------- 생성
    def _create_mpv(self) -> None:
        mod, err = _load_mpv_module()
        if mod is None:
            self._fail(err)
            return
        try:
            locale.setlocale(locale.LC_NUMERIC, "C")  # libmpv 요구 사항
        except locale.Error:
            pass
        opts = dict(_OPTIONS_REQUIRED)
        if self.headless:
            opts.update(vo="null", ao="null")
        else:
            opts.update(
                wid=str(int(self.winId())),
                vo="direct3d" if self.compat else "gpu,direct3d",
                hwdec="no" if self.compat else "auto-safe",
            )
        player = None
        last = None
        for extra in (_OPTIONS_EXTRA, {}):
            try:
                player = mod.MPV(**opts, **extra)
                break
            except Exception as e:
                last = e
                log.warning("mpv 생성 실패 (추가 옵션 %d개): %s", len(extra), e)
        if player is None:
            self._fail(f"영상 재생기(mpv)를 만들지 못했습니다: {type(last).__name__}: {last}")
            return
        try:
            player["drag-and-drop"] = "no"
        except Exception:
            pass
        self._mpv = player
        try:
            player.observe_property("time-pos", self._mpv_time_pos)
            player.observe_property("duration", self._mpv_duration)
            player.observe_property("pause", self._mpv_pause)
            player.register_event_callback(self._mpv_event)
        except Exception as e:
            self._fail(f"영상 재생기(mpv) 연결 실패: {e}")
            self._terminate()

    def _fail(self, msg: str) -> None:
        self._error = msg
        log.error("%s", msg)
        QTimer.singleShot(0, self, lambda: self._emit_error(msg))

    def _emit_error(self, msg: str) -> None:
        self.errorOccurred.emit(msg)

    # ---------------------------------------------------------------- mpv 이벤트 스레드 (Signal 만)
    def _mpv_time_pos(self, _name, value):
        self._raw_pos = None if value is None else float(value)

    def _mpv_duration(self, _name, value):
        self._evt.emit("duration", value)

    def _mpv_pause(self, _name, value):
        self._evt.emit("pause", value)

    def _mpv_event(self, event):
        try:
            name = event.event_id.value
        except Exception:
            return
        try:
            if name == _EV_FILE_LOADED:
                self._evt.emit("file-loaded", None)
            elif name == _EV_SEEK:
                self._evt.emit("seek", None)
            elif name == _EV_PLAYBACK_RESTART:
                self._evt.emit("restart", None)
            elif name == _EV_END_FILE:
                d = event.data
                self._evt.emit("end-file", (int(getattr(d, "reason", -1)), int(getattr(d, "error", 0))))
            elif name == _EV_SHUTDOWN:
                self._evt.emit("shutdown", None)
        except Exception:
            pass

    # ---------------------------------------------------------------- UI 스레드
    @Slot(str, object)
    def _on_evt(self, kind: str, value) -> None:
        if self._closed:
            return
        if kind == "duration":
            ms = 0 if value is None else max(0, int(round(float(value) * 1000)))
            if ms != self._duration_ms:
                self._duration_ms = ms
                self.durationChanged.emit(ms)
        elif kind == "pause":
            playing = value is not None and not bool(value) and self._loaded
            self._set_playing(playing)
        elif kind == "file-loaded":
            self._loaded = True
            self._timer.start()
            self.fileLoaded.emit()
            self._apply_ab()
            if self._pending_seek is not None:
                ms, exact = self._pending_seek
                self._pending_seek = None
                self.seek(ms, exact)
            self._set_playing(self._mpv is not None and not self._get("pause", True))
        elif kind == "seek":
            self._seek_seen = True
        elif kind == "restart":
            # 파일 열기의 첫 restart 나 이전 탐색의 restart 가 늦게 와도 목표값을 지우지 않게:
            # 정확 탐색은 실제 위치가 목표 근처일 때, 키프레임 탐색은 seek() 뒤 SEEK 이벤트를 본 뒤에만 끝난 것으로 본다.
            t = self._seek_target_ms
            if t is not None:
                raw = self._raw_pos
                near = raw is not None and abs(raw * 1000 - t) <= SEEK_SETTLE_MS
                if (self._seek_exact and near) or (not self._seek_exact and self._seek_seen):
                    self._seek_target_ms = None
            self._poll()
        elif kind == "end-file":
            reason, err = value
            if reason == _END_REASON_ERROR:
                self._loaded = False
                self._timer.stop()
                self._set_playing(False)
                self.errorOccurred.emit(f"영상을 열지 못했습니다 (mpv 오류 {err}): {os.path.basename(self._path or '')}")
        elif kind == "shutdown":
            self._mpv = None
            self._loaded = False
            self._timer.stop()
            self._set_playing(False)

    def _set_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            self.playingChanged.emit(playing)

    def _poll(self) -> None:
        if self._seek_target_ms is not None and time.monotonic() - self._seek_started > SEEK_GIVEUP_S:
            self._seek_target_ms = None
        ms = self.position_ms()
        if ms != self._last_emitted_pos:
            self._last_emitted_pos = ms
            self.positionChanged.emit(ms)

    def _get(self, name: str, default=None):
        if self._mpv is None:
            return default
        try:
            return self._mpv[name]
        except Exception:
            return default

    def _set(self, name: str, value) -> bool:
        if self._mpv is None:
            return False
        try:
            self._mpv[name] = value
            return True
        except Exception as e:
            log.warning("mpv %s=%r 실패: %s", name, value, e)
            return False

    def _cmd(self, *args) -> bool:
        if self._mpv is None:
            return False
        try:
            self._mpv.command(*args)
            return True
        except Exception as e:
            log.warning("mpv 명령 %r 실패: %s", args, e)
            return False

    # ---------------------------------------------------------------- 상태
    def available(self) -> bool:
        """mpv 를 쓸 수 있는가 (libmpv 로드·생성 성공, 아직 닫지 않음)."""
        return self._mpv is not None and not self._closed

    def error(self) -> str | None:
        """생성 실패 사유 (없으면 None)."""
        return self._error

    def is_loaded(self) -> bool:
        return self._loaded

    def is_playing(self) -> bool:
        return self._playing

    def path(self) -> str | None:
        return self._path

    def position_ms(self) -> int:
        """현재 영상 파일 시각(ms). 탐색 중이면 목표 시각, 파일이 없으면 0 (load 전 seek 를 불렀으면 그 값)."""
        if self._seek_target_ms is not None:
            return self._seek_target_ms
        if not self._loaded:
            return self._pending_seek[0] if self._pending_seek else 0
        if self._raw_pos is None:
            return 0
        ms = int(round(self._raw_pos * 1000))
        if self._duration_ms:
            ms = min(ms, self._duration_ms)
        return max(0, ms)

    def duration_ms(self) -> int:
        return self._duration_ms

    def ab_loop(self) -> tuple[int | None, int | None]:
        return self._ab

    def speed(self) -> float:
        return self._speed

    def volume(self) -> int:
        return self._volume

    # ---------------------------------------------------------------- 제어
    def load(self, path: str) -> bool:
        """로컬 영상 파일을 정지 상태로 연다. 거부하거나 mpv 가 없으면 errorOccurred 를 보내고 False.

        열기는 비동기다: 실제로 열리면 fileLoaded·durationChanged 가 온다. 열기 전에 부른 seek 는 열린 뒤 적용된다.
        A-B 반복·속도·음량 설정은 파일이 바뀌어도 유지된다(반복은 새 파일에 다시 적용된다).
        """
        if self._closed:
            return False
        if self._mpv is None:
            self.errorOccurred.emit(self._error or "영상 재생기를 쓸 수 없습니다")
            return False
        why = check_media_path(path)
        if why:
            self.errorOccurred.emit(why)
            return False
        self._loaded = False
        self._timer.stop()
        self._path = path
        self._raw_pos = None
        self._seek_target_ms = None
        self._pending_seek = None
        self._last_emitted_pos = -1
        if self._duration_ms:
            self._duration_ms = 0
            self.durationChanged.emit(0)
        self._set_playing(False)
        self._set("pause", True)
        if not self._cmd("loadfile", path, "replace"):
            self.errorOccurred.emit(f"영상을 열지 못했습니다: {os.path.basename(path)}")
            return False
        return True

    def unload(self) -> None:
        """열린 파일을 닫는다 (재생기는 그대로)."""
        self._cmd("stop")
        self._loaded = False
        self._timer.stop()
        self._path = None
        self._raw_pos = None
        self._seek_target_ms = None
        self._pending_seek = None
        self._set_playing(False)

    def _at_end(self) -> bool:
        return bool(self._duration_ms) and self.position_ms() >= self._duration_ms - 50

    def play(self) -> None:
        if not self._loaded:
            return
        if self._at_end():  # 끝에서 재생하면 처음부터 (A-B 반복 중이면 A 부터)
            self.seek(self._ab[0] if self._ab[0] is not None else 0)
        self._set("pause", False)

    def pause(self) -> None:
        self._set("pause", True)

    def toggle(self) -> None:
        if not self._loaded:
            return
        if self._get("pause", True):
            self.play()
        else:
            self.pause()

    def seek(self, file_ms: int, exact: bool = True) -> None:
        """영상 파일 시각 file_ms 로 간다. exact=True 면 정확 탐색(hr-seek), False 면 키프레임."""
        if self._mpv is None:
            return
        ms = max(0, int(file_ms))
        if not self._loaded:
            self._pending_seek = (ms, exact)
            return
        if self._duration_ms:
            ms = min(ms, self._duration_ms)
        self._seek_seen = False
        if self._cmd("seek", f"{ms / 1000:.3f}", "absolute", "exact" if exact else "keyframes"):
            self._seek_target_ms = ms
            self._seek_exact = bool(exact)
            self._seek_started = time.monotonic()
            self._poll()

    def seek_relative(self, delta_ms: int, exact: bool = True) -> None:
        self.seek(self.position_ms() + int(delta_ms), exact)

    def frame_step(self, n: int) -> None:
        """n 프레임 이동 (음수면 뒤로). 이동하면 정지 상태가 된다. 가변 프레임률 판단은 부르는 쪽(7-6)."""
        n = int(n)
        if not self._loaded or n == 0:
            return
        self._seek_target_ms = None
        if self._cmd("frame-step", str(n)):
            return
        # 'frame-step <n>' 이 없는 libmpv: 한 칸씩
        name = "frame-step" if n > 0 else "frame-back-step"
        for _ in range(abs(n)):
            if not self._cmd(name):
                break

    def set_speed(self, x: float) -> None:
        x = float(x)
        if not (0.01 <= x <= 100.0):
            return
        if self._set("speed", x):
            self._speed = x

    def set_volume(self, v: int) -> None:
        v = max(0, min(100, int(v)))
        if self._set("volume", v):
            self._volume = v

    def set_mute(self, mute: bool) -> None:
        self._set("mute", bool(mute))

    def set_ab_loop(self, a_ms: int | None, b_ms: int | None) -> None:
        """A-B 반복 (02 7-3 '7-2 범위 반복'). 둘 다 None 이면 끈다. a > b 면 바꿔 넣는다.

        위치를 옮기지 않는다 — 범위 시작으로 가려면 부르는 쪽이 seek(a_ms) 한다.
        """
        a = None if a_ms is None else max(0, int(a_ms))
        b = None if b_ms is None else max(0, int(b_ms))
        if a is not None and b is not None and a > b:
            a, b = b, a
        self._ab = (a, b)
        self._apply_ab()

    def _apply_ab(self) -> None:
        a, b = self._ab
        self._set("ab-loop-a", "no" if a is None else f"{a / 1000:.3f}")
        self._set("ab-loop-b", "no" if b is None else f"{b / 1000:.3f}")

    def show_text(self, text: str, duration_ms: int = 2000) -> None:
        """영상 위 문구(OSD, 02 7-3). Qt 위젯을 겹치지 않는다."""
        self._cmd("show-text", str(text), str(int(duration_ms)))

    def set_audio_normalize(self, on: bool) -> bool:
        """음량 고르게(dynaudnorm) 켜기·끄기 (02 7-3). 성공하면 True."""
        return self._set("af", "lavfi=[dynaudnorm]" if on else "")

    def close_player(self) -> None:
        """mpv 를 끝낸다. 여러 번 불러도 된다. 그 뒤 모든 메서드는 아무 일도 하지 않는다."""
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._loaded = False
        self._terminate()

    def _terminate(self) -> None:
        player, self._mpv = self._mpv, None
        if player is None:
            return
        try:
            player.terminate()
        except Exception as e:
            log.warning("mpv 종료 실패: %s", e)

    # ---------------------------------------------------------------- Qt 이벤트
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def closeEvent(self, event):
        self.close_player()
        super().closeEvent(event)
