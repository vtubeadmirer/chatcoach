# 출처: vtubeadmirer/clipslicer@07d13cc tools/gui_smoke.py 의 틀(실제 MainWindow 를 띄워 단계별 PASS/FAIL 출력, 종료 코드
#   0 = 통과, 임시 설정 폴더)을 가져왔다. 변경: 단계는 ChatCoach 작업대(프로젝트 열기 → 영상 → 탐색 → 채팅 → 타임라인)로
#   새로 썼고, QTimer 대신 이벤트를 돌리며 기다리는 순차 실행, 단계마다 걸린 시간, 명령줄 인자, 스크린샷 위치 제한을 더했다.
"""GUI 스모크 시험 (pytest 아님): 실제 앱 창으로 프로젝트를 열고 작업대가 도는지 순서대로 본다.

사용:
  .venv\\Scripts\\python tools\\gui_smoke.py --project <프로젝트 폴더> [--video <영상 파일>] [--offscreen]
        [--screenshot <png>] [--store-copy] [--timeout 초]

단계: 창 → 프로젝트 열기(파일 메뉴와 같은 길: ProjectView.open_path) → 채팅 싣기 → 타임라인 밀도 →
      영상 로드(project.json 에 저장하지 않음) → 정확 탐색 → 채팅 활성화(−3초 탐색 + 하이라이트) → 재생 1.5초 →
      타임라인 확대 → 채팅 필터 → (--store-copy: project.json·guide 만 담은 임시 사본에서 라벨 저장·되돌리기,
      보호 구역·채팅은 복사하지 않음) → 스크린샷 → 닫기(잠금 해제·
      project.json 그대로).

지키는 것
- 원본 프로젝트에는 쓰지 않는다: 영상 연결은 persist=False, 세션이 없어 라벨 기록도 열지 않는다. 열기 잠금
  (locks/open.lock)만 잡았다 푼다. 끝에 project.json 수정 시각과 크기가 그대로인지 확인한다.
- 설정·machine.json 은 임시 데이터 폴더(--data-dir, 기본 %TEMP%\\chatcoach_smoke)에 둔다.
- 출력은 숫자와 통과 여부만. 채팅 본문·닉네임·제목은 찍지 않는다(02 10장).
- 스크린샷은 저장소 밖 <저장소 옆>\\chatcoach-data\\screens\\ 안에만 저장한다. 다른 곳을 주면 거부한다.

offscreen 에서는 플레이어가 vo=null·ao=null 로 돌아(진짜 창 핸들이 없다) 스크린샷의 영상 칸은 비어 있고, 글꼴이 없어
글자가 네모로 보일 수 있다. 영상 그림까지 보려면 --offscreen 없이 실제 창으로 돌린다.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS_DIR = os.path.join(os.path.dirname(ROOT), "chatcoach-data", "screens")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="ChatCoach GUI 스모크 시험")
    ap.add_argument("--project", required=True, help="프로젝트 폴더 (project.json 이 있는 곳)")
    ap.add_argument("--video", help="영상 파일 (저장하지 않고 이번에만 연결)")
    ap.add_argument("--offscreen", action="store_true", help="화면 없이 (QT_QPA_PLATFORM=offscreen)")
    ap.add_argument("--screenshot", help=f"창 스크린샷 png (반드시 {SCREENS_DIR} 안)")
    ap.add_argument("--store-copy", action="store_true", help="프로젝트를 임시 폴더로 복사해 라벨 저장·되돌리기도 시험")
    ap.add_argument("--data-dir", help="임시 데이터 폴더 (기본 %%TEMP%%\\chatcoach_smoke)")
    ap.add_argument("--timeout", type=float, default=60.0, help="단계 하나의 대기 상한(초)")
    return ap.parse_args(argv)


# --store-copy 가 임시 폴더로 복사하는 것: 라벨 저장·되돌리기에 필요한 최소한만(03 2-3). 보호 구역(chat/protected·
# derived/asr*·derived/ocr*·origin/)은 물론 채팅 원문(messages.jsonl)·수집 기록·파생 자료·기존 라벨·영상도 옮기지 않는다
# — 실행을 중간에 끊거나 지우기가 실패해도 TEMP 에 개인정보가 남지 않게. 채팅이 없으니 작업대 띠에 '채팅 파일 없음' 이 뜬다.
STORE_COPY_FILES = ("project.json",)
STORE_COPY_DIRS = ("guide",)


def copy_for_store_test(src: str, dst: str) -> list[str]:
    """src 프로젝트에서 STORE_COPY_FILES·STORE_COPY_DIRS 만 dst 로 복사한다. 복사한 상대 경로 목록."""
    os.makedirs(dst, exist_ok=True)
    done = []
    for name in STORE_COPY_FILES:
        p = os.path.join(src, name)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dst, name))
            done.append(name)
    for name in STORE_COPY_DIRS:
        p = os.path.join(src, name)
        if os.path.isdir(p):
            shutil.copytree(p, os.path.join(dst, name))
            done.append(name + "/")
    return done


def remove_tree_checked(path: str) -> list[str]:
    """path 를 지우고, 지우지 못한 파일 목록을 돌려준다(빈 목록 = 다 지움). 실패를 조용히 넘기지 않는다."""
    failed: list[str] = []

    def onexc(_func, p, _exc):
        failed.append(p)
    if os.path.exists(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=onexc)
        else:
            shutil.rmtree(path, onerror=onexc)
    if os.path.exists(path) and not failed:
        failed.append(path)
    return failed


def _inside(path: str, folder: str) -> bool:
    p, f = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return os.path.commonpath([p, f]) == f


class Smoke:
    def __init__(self, args):
        self.args = args
        self.results: list[tuple[str, bool, float, str]] = []
        self.app = None
        self.win = None

    # ---------------------------------------------------------------- 도우미
    def check(self, name: str, ok: bool, t0: float, detail: str = "") -> bool:
        ms = (time.perf_counter() - t0) * 1000
        self.results.append((name, bool(ok), ms, detail))
        print(f"{'PASS' if ok else 'FAIL'} {name:<22} {ms:8.0f} ms  {detail}", flush=True)
        return bool(ok)

    def pump(self, ms: int = 0) -> None:
        end = time.perf_counter() + ms / 1000
        while True:
            self.app.processEvents()
            if time.perf_counter() >= end:
                return
            time.sleep(0.005)

    def wait(self, cond, timeout_s: float | None = None) -> bool:
        end = time.perf_counter() + (timeout_s or self.args.timeout)
        while time.perf_counter() < end:
            self.app.processEvents()
            try:
                if cond():
                    return True
            except Exception:  # noqa: BLE001 — 아직 준비 안 됨
                pass
            time.sleep(0.01)
        return False

    @property
    def wb(self):
        return self.win.workbench

    # ---------------------------------------------------------------- 준비
    def setup(self) -> None:
        a = self.args
        if a.offscreen:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        sys.path.insert(0, ROOT)
        from app import paths
        data = a.data_dir or os.path.join(tempfile.gettempdir(), "chatcoach_smoke")
        os.makedirs(data, exist_ok=True)
        paths.set_data_root(data)
        self.data_root = data

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.app.setApplicationName("ChatCoachSmoke")
        import main as main_mod
        main_mod.apply_dark_theme(self.app)

    # ---------------------------------------------------------------- 단계
    def step_window(self) -> bool:
        t0 = time.perf_counter()
        from app import updater
        from app.appspec import SPEC
        from app.ui.mainwindow import MainWindow
        installed = updater.load_installed(SPEC)
        self.win = MainWindow(SPEC, installed, self.data_root, "", None, [], set())
        self.win.check_updates = lambda force=False: None      # 스모크 중에는 네트워크 확인을 하지 않는다
        self.win.show()
        self.pump(50)
        return self.check("window", self.win.isVisible(), t0, f"{self.win.width()}x{self.win.height()}")

    def step_open(self) -> bool:
        t0 = time.perf_counter()
        proj_json = os.path.join(self.args.project, "project.json")
        st = os.stat(proj_json)
        self.pj_before = (st.st_mtime_ns, st.st_size)
        # 파일 메뉴 '프로젝트 열기' 와 같은 길: 메뉴 → ProjectView.choose_and_open → open_path. 대화상자만 바꿔 끼운다.
        pv = self.win.project_view
        pv.choose_folder = lambda: self.args.project
        acts = [x for m in self.win.menuBar().actions() if m.menu() is not None
                for x in m.menu().actions() if x.shortcut().toString() == "Ctrl+O"]
        if acts:
            acts[0].trigger()
        else:
            pv.choose_and_open()
        ok = self.wb is not None and self.win.stack.currentWidget() is self.wb
        ro = self.wb.project.read_only if ok else None
        return self.check("open_project", ok and ro is False, t0,
                          f"menu={'Ctrl+O' if acts else 'direct'} read_only={ro} warnings={len(self.wb.notes) if ok else '-'}")

    def step_chat(self) -> bool:
        t0 = time.perf_counter()
        ok = self.wait(lambda: "chat" not in self.wb._jobs and self.wb.chat_data.count)
        chat = self.wb.chat
        total = chat.total_count() if hasattr(chat, "total_count") else self.wb.chat_data.count
        kept = len(self.wb.chat_data.records)                   # 병렬 배열로 넘겼으면 0 이어야 한다(02 9장 메모리)
        real = hasattr(chat, "total_count")
        return self.check("chat_loaded", ok and total > 0 and (kept == 0 or not real), t0,
                          f"chats={total} viewers={len(self.wb.chat_data.viewer_no)} dict_rows_kept={kept} "
                          f"part={type(chat).__name__}")

    def step_timeline(self) -> bool:
        t0 = time.perf_counter()
        tl = self.wb.timeline
        dens = getattr(tl, "_density", None) or []
        dur = getattr(tl, "_duration", 0)
        self.pump(50)
        return self.check("timeline_density", len(dens) > 0 and sum(dens) == self.wb.chat_data.count
                          and dur > 0, t0, f"buckets={len(dens)} duration_ms={dur} part={type(tl).__name__}")

    def step_video(self) -> bool:
        t0 = time.perf_counter()
        v = self.args.video
        if not v:
            return self.check("video_load", True, t0, "skip (--video 없음)")
        if not os.path.isfile(v):
            return self.check("video_load", False, t0, "영상 파일 없음")
        self.wb.connect_media(v, persist=False)
        pl = self.wb.player
        ok = self.wait(lambda: pl.duration_ms() > 0 and self.wb._file_duration > 0)
        self.file_dur = pl.duration_ms()
        return self.check("video_load", ok, t0, f"duration_ms={self.file_dur} player={type(pl).__name__} "
                                               f"vod_of_file0={self.wb.file_to_vod(0)}")

    def step_waveform(self) -> bool:
        t0 = time.perf_counter()
        if not self.wb.media_path:
            return self.check("waveform", True, t0, "skip (영상 없음)")
        tl = self.wb.timeline
        ok = self.wait(lambda: bool(getattr(tl, "_wave", None)) and "waveform" not in self.wb._jobs)
        wave = getattr(tl, "_wave", []) or []
        return self.check("waveform", ok, t0, f"points={len(wave)} span_ms={getattr(tl, '_wave_span', 0)}")

    def _tl_pos(self):
        """타임라인이 그리는 재생 위치 (Timeline.position 은 속성, 메서드여도 받는다)."""
        p = getattr(self.wb.timeline, "position", None)
        return p() if callable(p) else p

    def _settled(self, file_target: int, tol: int = 100) -> bool:
        return abs(self.wb.player.position_ms() - file_target) <= tol

    def step_seek(self) -> bool:
        t0 = time.perf_counter()
        if not self.wb.media_path:
            target = 60_000
            self.wb.seek_vod(target)
            self.pump(30)
            return self.check("seek", self.wb.vod_position() == target, t0, "영상 없음: 가상 위치만")
        f_target = self.file_dur // 2
        vod_target = self.wb.file_to_vod(f_target)
        self.wb.seek_vod(vod_target)
        ok = self.wait(lambda: self._settled(f_target, 100), 10)
        self.pump(300)                                          # 정착 뒤 신호가 한 번 더 와도 그대로인지
        pos = self.wb.player.position_ms()
        tl_pos = self._tl_pos()
        ok = ok and abs(pos - f_target) <= 100 and tl_pos == self.wb.vod_position()
        return self.check("seek", ok, t0, f"file_err_ms={pos - f_target} timeline_pos_ok={tl_pos == self.wb.vod_position()}")

    def step_chat_activate(self) -> bool:
        t0 = time.perf_counter()
        chat = self.wb.chat
        if not hasattr(chat, "chat_data"):
            return self.check("chat_activate", False, t0, "ChatPanel 아님")
        data = chat.chat_data()
        lo = hi = None
        if self.wb.media_path:
            lo, hi = self.wb.file_to_vod(20_000), self.wb.file_to_vod(self.file_dur - 20_000)
        else:
            ts = [x for x in data.times if x >= 0]
            lo, hi = (ts[0], ts[-1]) if ts else (0, 0)
        idx = -1
        for i in range(data.n):
            t = data.times[i]
            if t < 0:
                continue
            if lo <= t <= hi:
                idx = i
                if t >= (lo + hi) // 2:
                    break
        if idx < 0:
            return self.check("chat_activate", False, t0, "영상 범위 안 채팅 없음")
        cid, t = data.chat_ids[idx], data.times[idx]
        chat.select_chat(cid)
        chat.chatActivated.emit(cid, int(t))                    # 더블클릭·Enter 와 같은 신호
        want_vod = t - 3_000
        if self.wb.media_path:
            want_file = self.wb.vod_to_file(want_vod)
            ok = self.wait(lambda: self._settled(want_file, 100), 10)
            self.pump(200)
        else:
            ok = self.wb.vod_position() == want_vod
        vod = self.wb.vod_position()
        hl = chat.highlighted_chat_id()
        hi_i = data.index_of(hl) if hl else -1
        # 하이라이트 = t_ms <= 지금 VOD 인 마지막 채팅
        hl_ok = hi_i >= 0 and data.times[hi_i] <= vod and (hi_i + 1 >= data.n or data.times[hi_i + 1] > vod
                                                            or data.times[hi_i + 1] < 0)
        tl_ok = self._tl_pos() == vod
        return self.check("chat_activate", ok and hl_ok and tl_ok, t0,
                          f"vod_err_ms={vod - want_vod} highlight_ok={hl_ok} timeline_ok={tl_ok}")

    def step_play(self) -> bool:
        t0 = time.perf_counter()
        if not self.wb.media_path:
            return self.check("play", True, t0, "skip (영상 없음)")
        before_vod = self.wb.vod_position()
        hl_before = self.wb.chat.highlighted_chat_id()
        self.wb.toggle_play()
        ok = self.wait(lambda: self.wb._playing, 5)
        self.pump(1500)
        self.wb.toggle_play()
        self.wait(lambda: not self.wb._playing, 5)
        self.pump(100)
        moved = self.wb.vod_position() - before_vod
        tl_ok = self._tl_pos() == self.wb.vod_position()
        hl_after = self.wb.chat.highlighted_chat_id()
        return self.check("play", ok and 800 <= moved <= 2500 and tl_ok, t0,
                          f"moved_ms={moved} timeline_ok={tl_ok} highlight_changed={hl_after != hl_before}")

    def step_zoom(self) -> bool:
        t0 = time.perf_counter()
        tl = self.wb.timeline
        if not hasattr(tl, "zoom_to_range"):
            return self.check("timeline_zoom", False, t0, "Timeline 아님")
        pos = self.wb.vod_position()
        seen = []
        tl.viewChanged.connect(lambda a, b: seen.append((a, b)))
        tl.zoom_to_range(pos - 30_000, pos + 30_000, 0.0)
        self.pump(50)
        a, b = tl.view_range()
        ok = a <= pos <= b and (b - a) <= 70_000 and bool(seen)
        tl.zoom_reset()
        self.pump(20)
        a2, b2 = tl.view_range()
        return self.check("timeline_zoom", ok and (b2 - a2) > (b - a), t0,
                          f"view_ms={b - a} reset_view_ms={b2 - a2} signals={len(seen)}")

    def step_filter(self) -> bool:
        t0 = time.perf_counter()
        chat = self.wb.chat
        if not hasattr(chat, "row_count"):
            return self.check("chat_filter", False, t0, "ChatPanel 아님")
        total = chat.total_count()
        chat.set_filter("#1")                                    # 시청자 번호 1 의 채팅만
        n1 = chat.row_count()
        chat.set_filter("")
        n2 = chat.row_count()
        return self.check("chat_filter", 0 < n1 < total and n2 == total, t0, f"viewer1_rows={n1} all_rows={n2}")

    def step_store_copy(self) -> bool:
        t0 = time.perf_counter()
        if not self.args.store_copy:
            return self.check("store_copy", True, t0, "skip (--store-copy 없음)")
        import uuid
        from app import session as sess
        from app.ui import project_view as pvm
        from app.ui.workbench import Workbench
        tmp = tempfile.mkdtemp(prefix="chatcoach_smoke_store_")
        dst = os.path.join(tmp, "p")
        leftover: list[str] = []
        try:
            copy_for_store_test(self.args.project, dst)
            mid = str(uuid.uuid4())
            s = sess.new_session("L99", "primary", machine_id_value=mid)
            out = pvm.open_checked(dst)
            wb = Workbench(session=s)
            try:
                wb.open_project(out)
                ok = wb.wait_jobs(30_000) and wb.store is not None and not wb.store.read_only
                n0 = len(wb.store.records()) if wb.store else 0
                seg = {"kind": "segment", "body": {"start_ms": 0, "end_ms": 60_000, "category": "just_chatting",
                                                   "confidence": "certain", "rationale": "smoke"}}
                t1 = time.perf_counter()
                wb.save_label(seg) if ok else None
                save_ms = (time.perf_counter() - t1) * 1000
                segs = len(getattr(wb.timeline, "_segments", []))
                wb.undo_last() if ok else None
                segs2 = len(getattr(wb.timeline, "_segments", []))
                ok = ok and segs == 1 and segs2 == 0
                detail = f"records_before={n0} save_ms={save_ms:.1f} segments={segs}->{segs2}"
            finally:
                wb.close_workbench()
                wb.deleteLater()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}"
        finally:
            leftover = remove_tree_checked(tmp)
        if leftover:
            ok = False
            detail += f" 임시 사본을 다 지우지 못함({len(leftover)}개, {tmp}) — 직접 지우세요"
        return self.check("store_copy", ok, t0, detail)

    def step_shot(self) -> bool:
        t0 = time.perf_counter()
        p = self.args.screenshot
        if not p:
            return self.check("screenshot", True, t0, "skip (--screenshot 없음)")
        if not _inside(p, SCREENS_DIR):
            return self.check("screenshot", False, t0, f"거부: {SCREENS_DIR} 밖")
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        offscreen = self.app.platformName() in ("offscreen", "minimal")
        if offscreen:
            self.pump(100)
            pix = self.win.grab()
            note = " (offscreen: 영상 칸은 vo=null 이라 비어 있음)"
        else:
            # mpv 는 자기 네이티브 창에 직접 그려 QWidget.grab() 에 영상이 안 담긴다 → 화면에서 창 영역을 찍는다
            self.win.raise_()
            self.win.activateWindow()
            self.pump(500)
            g = self.win.frameGeometry()
            pix = self.win.screen().grabWindow(0, g.x(), g.y(), g.width(), g.height())
            note = " (화면 캡처: 창 위에 다른 창이 있으면 같이 찍힘)"
        saved = pix.save(p, "PNG")
        size = os.path.getsize(p) if saved and os.path.isfile(p) else 0
        return self.check("screenshot", saved and size > 1000, t0, f"bytes={size}{note}")

    def step_close(self) -> bool:
        t0 = time.perf_counter()
        self.win.show_home()
        self.pump(100)
        from app.ui import project_view as pvm
        again = pvm.open_checked(self.args.project)
        relocked = again.lock is not None
        pvm.release_outcome(again)
        st = os.stat(os.path.join(self.args.project, "project.json"))
        same = (st.st_mtime_ns, st.st_size) == self.pj_before
        return self.check("close", self.win.workbench is None and relocked and same, t0,
                          f"lock_released={relocked} project_json_unchanged={same}")

    # ---------------------------------------------------------------- 실행
    def run(self) -> int:
        self.setup()
        steps = [self.step_window, self.step_open, self.step_chat, self.step_timeline, self.step_video, self.step_waveform,
                 self.step_seek, self.step_chat_activate, self.step_play, self.step_zoom, self.step_filter,
                 self.step_store_copy, self.step_shot, self.step_close]
        t_all = time.perf_counter()
        for st in steps:
            try:
                ok = st()
            except Exception as e:  # noqa: BLE001
                traceback.print_exc(limit=3)
                ok = self.check(st.__name__.removeprefix("step_"), False, time.perf_counter(), type(e).__name__)
            if not ok and st in (self.step_window, self.step_open):
                break                                            # 이 둘이 안 되면 뒤 단계는 의미가 없다
        try:
            if self.win is not None:
                self.win.close()
                self.pump(100)
        except Exception:  # noqa: BLE001
            traceback.print_exc(limit=3)
        failed = [r for r in self.results if not r[1]]
        print(f"== {len(self.results) - len(failed)} passed, {len(failed)} failed, "
              f"{(time.perf_counter() - t_all):.1f} s, platform={self.app.platformName() if self.app else '-'}")
        return 0 if not failed else 1


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    args = parse_args(argv)
    if not os.path.isfile(os.path.join(args.project, "project.json")):
        print("project.json 이 없습니다")
        return 2
    return Smoke(args).run()


if __name__ == "__main__":
    sys.exit(main())
