# 출처: vtubeadmirer/clipslicer@07d13cc tools/gui_smoke.py 의 틀(실제 MainWindow 를 띄워 단계별 PASS/FAIL 출력, 종료 코드
#   0 = 통과, 임시 설정 폴더)을 가져왔다. 변경: 단계는 ChatCoach 작업대(프로젝트 열기 → 영상 → 탐색 → 채팅 → 타임라인)로
#   새로 썼고, QTimer 대신 이벤트를 돌리며 기다리는 순차 실행, 단계마다 걸린 시간, 명령줄 인자, 스크린샷 위치 제한을 더했다.
"""GUI 스모크 시험 (pytest 아님): 실제 앱 창으로 프로젝트를 열고 작업대가 도는지 순서대로 본다.

사용:
  .venv\\Scripts\\python tools\\gui_smoke.py --project <프로젝트 폴더> [--video <영상 파일>] [--offscreen]
        [--screenshot <png>] [--store-copy] [--labeling-flow] [--timeout 초]

단계: 창 → 프로젝트 열기(파일 메뉴와 같은 길: ProjectView.open_path, 세션 선택 화면은 '보기 전용') → 채팅 싣기 →
      타임라인 밀도 → 영상 로드(project.json 에 저장하지 않음) → 정확 탐색 → 채팅 활성화(−3초 탐색 + 하이라이트) →
      재생 1.5초 → 타임라인 확대 → 채팅 필터 → (--store-copy: project.json·guide 만 담은 임시 사본에서 라벨 저장·되돌리기,
      보호 구역·채팅은 복사하지 않음) → (--labeling-flow: 아래) → 스크린샷 → 닫기(잠금 해제·project.json 그대로).

--labeling-flow (08 D-09·D-10, 02 7-3·7-4): 한글 임시 폴더에 project.json · guide/ · chat/messages.jsonl 만 복사한 사본을
  메인 창으로 연다 → 세션 선택 화면(SessionDialog)에서 새 1차 세션 → store(QLockFile) → 상태 칩이 메인 창 상태 줄 하나로 →
  영상 연결(사본의 project.json 에만 저장: 영상이 덮는 VOD 범위는 project.json 으로 정해진다, D-10 3) →
  ① 구간: B 로 경계 하나(영상 끝 2분 전), 구간 2개(저스트채팅 · 게임) 저장(바쁨 표시 켜짐·꺼짐), 구간 완료 표시 →
  ② 반응: 영상 범위 안 채팅 1건에 M → 편집기: 강한 증거(닉네임 호명)·직접 반응, 7-2 범위를 2배속으로 두 번 들음(재확인) → 저장,
     반응 완료 표시 →
  ③ 표본: 저스트채팅 구간 표본 뽑기(sha256-tau-v2, 모든 표본의 확인 범위가 영상 안인지) · 재현 검증 → 1건 판정(반응 미확인) →
     대기 표본 하나에 ② 반응을 만들고 ③ 으로 돌아와: 판정함으로 세는지, 반응 미확인 단추가 막히는지, M 이 그 ② 라벨을
     고치는 편집기를 여는지, 고친 것이 같은 대상인지(D-10 4) →
  내보내기 zip(사본 밖 임시 폴더, 채팅마다 반응 라벨 1건) → 사본을 다시 열어(이어 하기 세션) 현재 유효 라벨이 같은지 → 지운다.
  영상(--video)이 꼭 있어야 한다: 영상이 없으면 영상 밖 규칙(D-10 3) 때문에 구간도 저장되지 않아 flow_media 에서 멈춘다.
  --window-size 1366x768 이면 흐름 동안 창 크기를 맞추고, 구간 폼·편집기·큐가 잘리는지 숫자(fit=…)로 찍는다.
  스크린샷(--screenshot 이름 뒤 _flow_segments · _flow_edit · _flow)을 남긴다.

지키는 것
- 원본 프로젝트에는 쓰지 않는다: 영상 연결은 persist=False, 세션 없이(보기 전용) 열어 라벨 기록도 열지 않는다.
  열기 잠금(locks/open.lock)만 잡았다 푼다. 끝에 project.json 수정 시각과 크기가 그대로인지 확인한다.
- 사본(--store-copy·--labeling-flow)은 끝나면 지우고, 남으면 FAIL 과 경로를 찍는다. 보호 구역(chat/protected·
  derived·origin)·기존 라벨·영상은 복사하지 않는다.
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
    ap.add_argument("--labeling-flow", action="store_true",
                    help="사본(project.json·guide·messages.jsonl)에서 세션→구간→반응→표본→내보내기→다시 열기")
    ap.add_argument("--window-size", help="--labeling-flow 동안 메인 창 크기 (예: 1366x768, 테두리 포함)")
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


# --labeling-flow 가 복사하는 것: 위 둘 + 채팅(chat/messages.jsonl). 수집 기록·원문 페이지·솔트·원 닉네임(chat/protected)·
# 파생 자료(derived: 전사문·엔진 결과)·기존 라벨·표본·영상은 옮기지 않는다.
FLOW_COPY_FILES = STORE_COPY_FILES + (os.path.join("chat", "messages.jsonl"),)


def copy_for_flow(src: str, dst: str) -> list[str]:
    """라벨링 흐름용 사본: project.json · guide/ · chat/messages.jsonl 만. 복사한 상대 경로 목록."""
    done = copy_for_store_test(src, dst)
    rel = os.path.join("chat", "messages.jsonl")
    p = os.path.join(src, rel)
    if os.path.isfile(p):
        os.makedirs(os.path.join(dst, "chat"), exist_ok=True)
        shutil.copy2(p, os.path.join(dst, rel))
        done.append(rel.replace(os.sep, "/"))
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


def _hms(ms: int) -> str:
    """근거 칸에 넣는 VOD 시각 'h:mm:ss' (안내서: 근거에 시각)."""
    from app.core.timecode import fmt_time
    return fmt_time(max(0, int(ms)), ms_digits=False, always_hours=True)


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
        # 원본 프로젝트는 세션 선택 화면에서 '보기 전용' 을 고른 것과 같게 연다(라벨 기록을 열지 않는다)
        self.chosen: list = []
        self.win.session_chooser = lambda outcome: self.chosen.append(outcome.project.root) or None
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
        asked = len(self.chosen) == 1
        view_only = ok and self.wb.session is None and self.wb.store is None
        return self.check("open_project", ok and ro is False and asked and view_only, t0,
                          f"menu={'Ctrl+O' if acts else 'direct'} read_only={ro} session_asked={asked} "
                          f"view_only={view_only} warnings={len(self.wb.notes) if ok else '-'}")

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

    # ---------------------------------------------------------------- --labeling-flow
    def _flow_session(self, root: str, data: dict, key: str):
        """세션 선택 화면을 진짜로 만들어(모달 없이) key('new:primary' · 'resume:<sid>')를 고르고 SessionContext 를 받는다."""
        from app.ui.session_dialog import SessionDialog
        dlg = SessionDialog(self.win, data_root=self.data_root)
        try:
            dlg.load_context(root, data)
            if not dlg.select_labeler(self.FLOW_LABELER):
                raise RuntimeError("명부에 시험 라벨러가 없습니다")
            if not dlg.choose(key):
                raise RuntimeError(f"세션 화면에서 고를 수 없습니다: {key.split(':')[0]}")
            dlg._choose_open()
            ctx = dlg.context()
        finally:
            dlg.deleteLater()
        if ctx is None:
            raise RuntimeError("세션을 만들지 못했습니다")
        return ctx

    FLOW_LABELER = "L99"

    def _flow_open(self, dst: str, key: str):
        """메인 창의 열기 길(ProjectView.open_path → 세션 선택 → 작업대 → store)로 사본을 연다."""
        holder = {}

        def chooser(outcome):
            holder["ctx"] = self._flow_session(outcome.project.root, outcome.project.data, key)
            return holder["ctx"]
        old = self.win.session_chooser
        self.win.session_chooser = chooser
        try:
            out = self.win.project_view.open_path(dst)
        finally:
            self.win.session_chooser = old
        ok = out is not None and out.ok and self.wb is not None and self.wait(
            lambda: self.wb.store is not None and not ({"chat", "store", "media_check"} & set(self.wb._jobs)))
        return ok, holder.get("ctx")

    def _signature(self, st) -> list:
        from app.ui.workbench import HEAD_KINDS
        eff = st.effective()
        return sorted((r["kind"], r["entity_id"], r["id"]) for k in HEAD_KINDS for r in eff.items(k))

    def _flow_chat(self, lo: int, hi: int):
        """[lo, hi] VOD 안의 일반 채팅(kind=text) 하나: 가운데에 가장 가까운 것. (chat_id, t_ms) 또는 None."""
        data = self.wb._editor_chats()
        from app.ui.chat_panel import KIND_CODES
        text_code = KIND_CODES.get("text")
        mid = (lo + hi) // 2
        best = None
        for i in range(data.n):
            t = data.times[i]
            if lo <= t <= hi and data.kinds[i] == text_code:
                if best is None or abs(t - mid) < abs(best[1] - mid):
                    best = (data.chat_ids[i], int(t))
        return best

    def step_labeling_flow(self) -> bool:
        t0 = time.perf_counter()
        if not self.args.labeling_flow:
            return self.check("labeling_flow", True, t0, "skip (--labeling-flow 없음)")
        from app import session as sess
        from app.core import phase as ph
        tmp = tempfile.mkdtemp(prefix="chatcoach_smoke_flow_")
        dst = os.path.join(tmp, "한글 사본", "프로젝트")
        out_zip = os.path.join(tmp, "내보내기", "labels.zip")
        ok_all = True
        try:
            done = copy_for_flow(self.args.project, dst)
            if not sess.load_labelers(self.data_root).get(self.FLOW_LABELER):
                sess.add_labeler(self.data_root, self.FLOW_LABELER)
            # ---- 1. 열기: 세션 화면 → store
            t1 = time.perf_counter()
            ok, ctx = self._flow_open(dst, "new:primary")
            wb = self.wb
            ok = ok and ctx is not None and wb.session is ctx and not wb.store.read_only
            ok_all &= self.check("flow_open", ok, t1,
                                 f"copied={len(done)} session_kind={ctx.session_kind if ctx else '-'} "
                                 f"store_ro={wb.store.read_only if wb and wb.store else '-'} chats={wb.chat_data.count}")
            if not ok:
                return False
            # 상태 줄 하나(11 4-2 의 9): 작업대 칩이 메인 창 상태 줄로 옮겨졌는가
            one_bar = (not wb.status_row.isVisible() and all(c.parent() is self.win.statusBar() for c in wb.status_chips))
            busy_log: list = []
            wb.busyChanged.connect(busy_log.append)
            # ---- 1-2. 영상 연결: 사본의 project.json 에 저장한다(영상이 덮는 범위 D-10 3 은 project.json 으로 정해진다).
            #      원본에는 쓰지 않는다(사본만). 영상이 없으면 영상 밖 규칙 때문에 구간도 저장되지 않는다 → 흐름을 멈춘다.
            t1 = time.perf_counter()
            cov_before = wb.media_coverage()
            if self.args.video and os.path.isfile(self.args.video):
                wb.connect_media(self.args.video, persist=True)
                self.wait(lambda: wb._file_duration > 0 and "media_ref" not in wb._jobs, 60)
            fdur = wb._file_duration
            cov = wb.media_coverage() or []
            ok = bool(fdur) and len(cov) == 1 and one_bar
            ok_all &= self.check("flow_media", ok, t1,
                                 f"file_ms={fdur} coverage_before={cov_before} coverage_vod_ms={cov} "
                                 f"one_status_bar={one_bar}")
            if not ok:
                return False
            c_lo, c_hi = cov[0]
            if self.args.window_size:
                self._resize(self.args.window_size)
            # ---- 2. ① 구간 2개 (경계는 영상 끝 2분 전: 두 구간 다 영상에 걸쳐야 저장된다, D-10 3)
            t1 = time.perf_counter()
            gate_before = wb.gates()[ph.REACTIONS].allowed
            ok = wb.set_mode(ph.SEGMENTS) and wb.segment_form is not None
            f = wb.segment_form
            vdur = max(1, wb._vod_duration)
            cut = min(vdur - 60_000, c_hi - 120_000)
            wb.seek_vod(cut)
            ok = ok and wb.add_boundary_here() and len(f.model.segments) == 2
            a, b = f.model.segments
            f.select_segment(a.id)
            ok = ok and f.set_category("just_chatting") and f.set_confidence("certain") and \
                f.set_rationale(f"{_hms(c_lo + 5_000)} smoke 저스트채팅")
            f.select_segment(b.id)
            ok = ok and f.set_category("game") and f.set_confidence("probably") and \
                f.set_rationale(f"{_hms(cut)} smoke 게임")
            self._shot("_flow_segments")                     # 구간 폼 (저장 전)
            fit_seg = self._fit(f)
            ts = time.perf_counter()
            n_busy = len(busy_log)
            recs = f.save()
            seg_ms = (time.perf_counter() - ts) * 1000
            busy_seen = len(busy_log) > n_busy and busy_log[n_busy] is True
            self.pump(30)
            busy_off = not wb.is_busy() and busy_log[-1:] == [False]
            tl_segs = len(getattr(wb.timeline, "_segments", []))
            ok = ok and recs is not None and len(recs) == 2 and wb.mark_phase_done() is not None
            ok = ok and not gate_before and wb.gates()[ph.REACTIONS].allowed and tl_segs == 2 and busy_seen and busy_off
            ok_all &= self.check("flow_segments", ok, t1,
                                 f"saved={len(recs or [])} save_ms={seg_ms:.1f} timeline_segments={tl_segs} "
                                 f"reactions_locked_before={not gate_before} busy_shown={busy_seen} "
                                 f"busy_cleared={busy_off} fit={fit_seg}")
            if not ok:
                return False
            # ---- 3. ② 반응 1건 (강한 증거 · 7-2 재확인)
            t1 = time.perf_counter()
            ok = wb.set_mode(ph.REACTIONS)
            lo = wb.file_to_vod(60_000) if fdur else 60_000
            hi = min(cut - 60_000, wb.file_to_vod(fdur - 60_000) if fdur else cut - 60_000)
            pick = self._flow_chat(max(0, lo), hi)
            if not ok or pick is None:
                return self.check("flow_reaction", False, t1, "영상 범위 안 저스트채팅 채팅 없음")
            chat_id, t_chat = pick
            wb.chat.select_chat(chat_id)
            wb.seek_vod(t_chat + 1_000)
            if fdur:
                self.wait(lambda: abs(wb.vod_position() - (t_chat + 1_000)) <= 200, 10)
            wb.mark_reaction()
            ed = wb.editor
            ok = wb.is_editing() and ed.target_chat_id() == chat_id and ed.grade() is None
            self.pump(30)
            self.fit_edit = self._fit(ed)
            ed.utt_start.setText(f"{(t_chat + 1_000) / 1000:.3f}")
            ed.utt_end.setText(f"{(t_chat + 3_000) / 1000:.3f}")
            ed._on_utt_times()
            ed.heard.setText("smoke")
            ed._on_heard_edited("smoke")
            ed.evidence.boxes["nickname_called"].setChecked(True)
            ed.set_grade("direct")
            ed.set_confidence("certain")
            ed.rationale.setText(f"{_hms(t_chat + 1_000)} smoke 호명")
            passes, covs = 0, []
            if fdur:
                rv = wb.review
                rv.set_speed(2.0)
                for k in range(2):                       # 7-2: 처음 확인 + 다시 듣기
                    ed.listen_again()
                    if k == 0 and self.args.screenshot and _inside(self.args.screenshot, SCREENS_DIR):
                        self.pump(1500)                  # 범위 반복 재생 중인 편집 상태 화면
                        self._shot("_flow_edit")
                    got = self.wait(lambda: rv.current_coverage() >= 0.97, 90)
                    covs.append(rv.current_coverage())
                    passes += 1 if got else 0
                rv.stop_loop()
                wb.player.pause()
                rv.set_speed(1.0)
            ts = time.perf_counter()
            res = ed.save()
            r_ms = (time.perf_counter() - ts) * 1000
            marks = list(getattr(wb.timeline, "markers", lambda n: [])("labels"))
            complete = bool(res and res.get("complete"))
            want_kind = "reaction" if complete else "recheck"
            ok = ok and res is not None and not wb.is_editing() and marks and marks[0][1] == want_kind
            ok = ok and (complete if fdur else True)
            ok = ok and wb.mark_phase_done() is not None
            ok_all &= self.check("flow_reaction", ok, t1,
                                 f"lines={len((res or {}).get('utterances') or []) + 1 + len((res or {}).get('video_checks') or [])} "
                                 f"passes={passes} coverage={'/'.join(f'{c:.2f}' for c in covs) or '-'} "
                                 f"complete={complete} save_ms={r_ms:.1f} marker={marks[0][1] if marks else '-'} "
                                 f"fit={getattr(self, 'fit_edit', '-')}")
            if not ok:
                return False
            # ---- 4. ③ 표본 뽑기 · 재현 검증 · 1건 판정
            t1 = time.perf_counter()
            ok = wb.set_mode(ph.SAMPLES)
            q = wb.queue
            wb.draw_sample()
            ok = ok and self.wait(lambda: "sample_draw" not in wb._jobs and q.manifest is not None, 60)
            man = q.manifest or {}
            tv = time.perf_counter()
            ver = q.run_verify()
            v_ms = (time.perf_counter() - tv) * 1000
            from app.core import sampling as sp
            ex = man.get("excluded") or {}
            pre, post = (man.get("media") or {}).get("pre_roll_ms", 0), (man.get("media") or {}).get("post_roll_ms", 0)
            # 영상 안 범위에서만 뽑혔는가: 모든 표본의 7-2 확인 범위가 영상 범위 안 (D-10 3)
            inside = all(sp.window_in_coverage(it["t_ms"], sp.normalize_coverage(cov), pre, post)
                         for it in man.get("items") or [])
            had2 = sum(1 for it in man.get("items") or [] if q.has_stage2_label(it["chat_id"]))
            ok = ok and ver is not None and ver.ok and len(os.listdir(os.path.join(dst, "samples"))) == 1
            ok = ok and man.get("algorithm") == sp.ALGORITHM_V2 and inside and bool(man.get("items"))
            ok_all &= self.check("flow_sample_draw", ok, t1,
                                 f"alg={man.get('algorithm')} eligible={(man.get('eligible') or {}).get('size')} "
                                 f"items={len(man.get('items') or [])} census={man.get('census')} "
                                 f"out_of_media={ex.get('out_of_media')} buffer_excluded={ex.get('buffer')} "
                                 f"all_inside_media={inside} items_with_stage2={had2} "
                                 f"verify={'ok' if ver and ver.ok else 'fail'} verify_ms={v_ms:.0f}")
            if not ok:
                return False
            t1 = time.perf_counter()
            q.next_pending() if q.current_item() and q.has_stage2_label(q.current_item()["chat_id"]) else None
            q.set_confidence("certain")
            it = q.current_item()
            q.rationale.setText(f"{_hms(it['t_ms'] if it else 0)} smoke 답 없음")
            rec = q.judge_unconfirmed()
            pr = q.progress()
            done1 = pr.done if pr else -1
            ok = rec is not None and pr is not None and done1 == 1 + had2
            ok_all &= self.check("flow_sample_judge", ok, t1,
                                 f"progress={pr.done if pr else '-'}/{pr.denominator_text() if pr else '-'}")
            # ---- 4-2. ③ 에서 ② 라벨 다시 열기 (D-10 4): 대기 표본 하나에 ② 반응을 만든 뒤 ③ 으로 돌아와 고친다
            t1 = time.perf_counter()
            ok = q.next_pending()
            it2 = q.current_item()
            x_chat, x_t = (it2["chat_id"], int(it2["t_ms"])) if it2 else (None, 0)
            ok = ok and x_chat is not None and wb.set_mode(ph.REACTIONS)
            wb.chat.select_chat(x_chat)
            wb.seek_vod(x_t + 1_000)
            wb.mark_reaction()
            ed = wb.editor
            ok = ok and wb.is_editing() and ed.target_chat_id() == x_chat
            ed.utt_start.setText(f"{(x_t + 1_000) / 1000:.3f}")
            ed.utt_end.setText(f"{(x_t + 3_000) / 1000:.3f}")
            ed._on_utt_times()
            ed.heard.setText("smoke")
            ed._on_heard_edited("smoke")
            ed.evidence.boxes["nickname_called"].setChecked(True)
            ed.set_grade("direct")
            ed.set_confidence("certain")
            ed.rationale.setText(f"{_hms(x_t + 1_000)} smoke 호명 2")
            res2 = ed.save()
            x_eid = (res2 or {}).get("reaction", {}).get("entity_id") if res2 else None
            ok = ok and res2 is not None and wb.set_mode(ph.SAMPLES)
            q = wb.queue
            judged2 = q.state_of(x_chat) == sp.JUDGED and q.has_stage2_label(x_chat)
            pr2 = q.progress()
            q.go(next((i for i, x in enumerate(q._items) if x["chat_id"] == x_chat), 0))
            n_rec = len(wb.store.records())
            blocked = q.judge_undeterminable() is None and len(wb.store.records()) == n_rec
            wb.mark_reaction()                                # M → 그 ② 라벨을 고치는 편집기
            reopened = wb.is_editing() and (ed._existing or {}).get("entity_id") == x_eid and \
                ed._entry == "from_utterance"
            ed.set_confidence("probably")
            res3 = ed.save()
            same_entity = bool(res3) and res3["reaction"]["entity_id"] == x_eid and \
                res3["reaction"]["body"]["entry"] == "from_utterance"
            pr3 = q.progress()
            ok = ok and judged2 and blocked and reopened and same_entity and pr2 is not None and \
                pr2.done == done1 + 1 and pr3.done == pr2.done
            ok_all &= self.check("flow_sample_stage2", ok, t1,
                                 f"judged_by_stage2={judged2} quick_save_blocked={blocked} reopened_existing={reopened} "
                                 f"same_entity={same_entity} progress={pr2.done if pr2 else '-'}->{pr3.done if pr3 else '-'}")
            self.pump(50)
            self._shot("_flow")                                # 큐 화면
            fit_q = self._fit(wb.page_samples)
            self.check("flow_layout", True, t1, f"queue_fit={fit_q} window={self.win.width()}x{self.win.height()}")
            # ---- 5. 내보내기
            t1 = time.perf_counter()
            os.makedirs(os.path.dirname(out_zip), exist_ok=True)
            er = wb.export_labels(out_zip)
            import json as _json
            import zipfile
            with zipfile.ZipFile(out_zip) as z:
                names = set(z.namelist())
                rows = [_json.loads(x) for x in z.read("labels.jsonl").decode("utf-8").splitlines() if x.strip()]
            eff = wb.store.effective()
            want = sorted((r["kind"], r["entity_id"], r["id"]) for k in ("segment", "reaction") for r in eff.items(k))
            got = sorted((r["label_kind"], r["entity_id"], r["label_id"]) for r in rows
                         if r["label_kind"] in ("segment", "reaction"))
            bad_rows = [w for w in er.warnings if "export-row" in w]
            # 반응 3 = ② 1건 + ③ 판정 1건 + ③ 에서 고친 ② 1건 (같은 채팅에 라벨 둘이 생기지 않았다)
            ok = {"labels.jsonl", "segments.csv", "reactions.csv", "manifest.json"} <= names and got == want and \
                not bad_rows and er.by_kind.get("segment") == 2 and er.by_kind.get("reaction") == 3
            chats = [r.get("chat_id") for r in rows if r.get("label_kind") == "reaction"]
            ok = ok and len(chats) == len(set(chats))
            ok_all &= self.check("flow_export", ok, t1,
                                 f"rows={len(rows)} by_kind={dict((k, v) for k, v in er.by_kind.items() if v)} "
                                 f"zip_bytes={os.path.getsize(out_zip)} rows_match_effective={got == want}")
            # ---- 6. 다시 열기 (이어 하기 세션) → 같은 현재 유효 라벨
            t1 = time.perf_counter()
            before = self._signature(wb.store)
            sid = ctx.session_id
            ok2, ctx2 = self._flow_open(dst, "resume:" + sid)
            wb2 = self.wb
            after = self._signature(wb2.store) if ok2 else None
            ok = ok2 and ctx2 is not None and ctx2.session_id == sid and after == before and \
                wb2.gates()[ph.SAMPLES].allowed
            ok_all &= self.check("flow_reopen", ok, t1, f"labels={len(before)} same={after == before} "
                                                        f"samples_gate={wb2.gates()[ph.SAMPLES].allowed if ok2 else '-'}")
            # 사본 닫기: session.close 줄
            self.win.show_home()
            self.pump(50)
            import glob as _glob
            files = _glob.glob(os.path.join(dst, "labels", f"{sid}.*.jsonl"))
            last = ""
            if files:
                with open(files[0], encoding="utf-8") as fh:
                    last = _json.loads(fh.read().splitlines()[-1]).get("kind", "")
            ok_all &= self.check("flow_close", last == "session.close", time.perf_counter(), f"last_line={last}")
            return ok_all
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(limit=4)
            return self.check("labeling_flow", False, t0, type(e).__name__)
        finally:
            if self.wb is not None and getattr(self.wb, "project", None) is not None and \
                    _inside(self.wb.project.root, tmp):
                self.win.show_home()
                self.pump(50)
            leftover = remove_tree_checked(tmp)
            if leftover:
                self.check("flow_cleanup", False, t0, f"임시 사본을 다 지우지 못함({len(leftover)}개, {tmp}) — 직접 지우세요")
            else:
                self.check("flow_cleanup", True, time.perf_counter(),
                           f"사본 지움 (흐름 전체 {time.perf_counter() - t0:.1f}초)")

    def _resize(self, spec: str) -> None:
        """--window-size '1366x768': 메인 창 바깥 크기(테두리 포함)를 그 크기로 맞춘다(노트북 화면 확인, 11 4-2 의 2)."""
        w, h = (int(x) for x in spec.lower().split("x", 1))
        self.win.showNormal()
        self.pump(50)
        fw = max(0, self.win.frameGeometry().width() - self.win.width())
        fh = max(0, self.win.frameGeometry().height() - self.win.height())
        self.win.resize(max(400, w - fw), max(300, h - fh))
        self.win.move(0, 0)
        self.pump(200)

    def _shot(self, suffix: str) -> None:
        """--screenshot 이름 뒤에 suffix 를 붙여 찍는다(SCREENS_DIR 안일 때만)."""
        p = self.args.screenshot
        if p and _inside(p, SCREENS_DIR):
            root_, ext = os.path.splitext(p)
            self._grab(root_ + suffix + (ext or ".png"))

    def _fit(self, w) -> str:
        """오른쪽 칸 부품이 잘리는지 숫자로: 크기, 세로 스크롤 필요 여부, 글자가 잘린 단추·라벨 수(폭이 sizeHint 보다 좁음),
        부품 오른쪽 밖으로 나간 자식 수. 글자는 찍지 않는다."""
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit, QScrollArea
        self.pump(30)
        vs = hs = False
        for sa in ([w] if isinstance(w, QScrollArea) else []) + w.findChildren(QScrollArea):
            if sa.isVisibleTo(w) or sa is w:
                vs = vs or sa.verticalScrollBar().maximum() > 0
                hs = hs or sa.horizontalScrollBar().maximum() > 0     # 가로 스크롤 = 폭이 모자라 오른쪽이 가려짐
        clipped = outside = 0
        for c in w.findChildren(QAbstractButton) + w.findChildren(QLabel) + w.findChildren(QLineEdit):
            if not c.isVisibleTo(w) or c.width() <= 0:
                continue
            if isinstance(c, QLabel) and (c.wordWrap() or not c.text()):
                pass
            elif isinstance(c, QAbstractButton) and c.text() and c.sizeHint().width() > c.width() + 1:
                clipped += 1
            elif isinstance(c, QLabel) and c.sizeHint().width() > c.width() + 1:
                clipped += 1
            # 스크롤 영역 안 자식은 그 보이는 칸(viewport) 기준
            sa = next((p for p in self._ancestors(c, w) if isinstance(p, QScrollArea)), None)
            box = sa.viewport() if sa is not None else w
            right = c.mapTo(box, QPoint(c.width(), 0)).x()
            if right > box.width() + 1:
                outside += 1
        return (f"{w.width()}x{w.height()}/vscroll={'y' if vs else 'n'}/hscroll={'y' if hs else 'n'}"
                f"/clipped={clipped}/outside={outside}")

    @staticmethod
    def _ancestors(c, top):
        p = c.parentWidget()
        while p is not None and p is not top:
            yield p
            p = p.parentWidget()

    def _grab(self, p: str) -> tuple[bool, int, str]:
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
        return saved, size, note

    def step_shot(self) -> bool:
        t0 = time.perf_counter()
        p = self.args.screenshot
        if not p:
            return self.check("screenshot", True, t0, "skip (--screenshot 없음)")
        if not _inside(p, SCREENS_DIR):
            return self.check("screenshot", False, t0, f"거부: {SCREENS_DIR} 밖")
        saved, size, note = self._grab(p)
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
                 self.step_store_copy, self.step_shot, self.step_labeling_flow, self.step_close]
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
