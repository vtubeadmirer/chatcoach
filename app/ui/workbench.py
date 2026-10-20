"""작업대: 플레이어 + 타임라인 + 채팅 패널 (docs/design/02-architecture.md 7-3·7-6·7-7·8장·9장).

배치: 왼쪽 위 플레이어(아래에 영상 연결 줄), 왼쪽 아래 타임라인, 오른쪽 채팅(검색 칸 + 채팅 패널), 맨 아래 상태 줄.

시각 축 (03 4장, app.core.timecode)
- 플레이어는 영상 파일 시각(file_ms), 채팅 패널·타임라인·엔진 결과는 VOD 시각(vod_ms = 채팅 시각).
- 변환은 project.json 의 활성 보정 판 하나로 한다: vod = video_to_chat_ms(file), file = chat_to_video_ms(vod).
  클립 파일의 시작 위치는 저장 offset_ms 에 이미 합쳐져 있으므로 file_start_in_vod_ms 는 0 으로 둔다.
- 영상이 없으면 '가상 재생 위치'(VOD)만 움직인다. 채팅을 보며 탐색할 수 있게.

데이터 흐름
- 채팅 읽기(messages.jsonl → 정렬 → 시청자 번호 → 10초 밀도)와 영상 확인(부분 지문)은 워커(QThread)에서 한다.
- 파형은 app.analysis.compute_waveform 을 워커로. 파일 시각 구간을 VOD 축으로 옮겨(앞을 0으로 채우거나 잘라) 넘긴다.
- '분석 보기'(엔진 반응 마커)는 app.core.visibility 가 허락할 때만 메뉴에 보인다. 모듈이 없거나 판단이 실패하면 숨김.
- project.json 에 쓰는 것은 '영상 파일 연결' 하나뿐이고 UI 스레드에서 Project.update 로 한다(rev 비교).

부품(MpvPlayer·ChatPanel·Timeline)은 02 3-3 공통 계약만 믿는다. import 나 생성이 실패하면 같은 메서드·시그널을
가진 빈 자리 위젯으로 바꾸고 띠에 알린다(앱이 죽지 않게). 시험은 parts= 로 가짜 부품을 넣을 수 있다.

로그에는 채팅 본문·닉네임을 남기지 않는다(10장). 건수·시간·예외 종류만.
"""
from __future__ import annotations

import copy
import glob
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from .. import jsonl, media
from .. import project as project_mod
from ..core import timecode as tc
from ..core.validate import load_json_strict
from ..i18n import tr

LOG = logging.getLogger("chatcoach")

# --------------------------------------------------------------------------- 시작값
DENSITY_BUCKET_MS = 10_000        # 채팅 밀도 버킷 (과제 명세: 10초)
SEEK_LEAD_MS = 3_000              # 채팅 더블클릭 → 그 채팅 VOD 시각 − 3초로 탐색
STEP_MS = 5_000                   # ←/→ · J/L
SMALL_STEP_MS = 1_000             # Shift+←/→
BIG_STEP_MS = 10_000              # Ctrl+←/→
FILTER_DEBOUNCE_MS = 250          # 02 9장: 입력 멈춘 뒤 250ms
WAVE_MS_PER_POINT = 500           # 파형 점 하나가 맡는 길이(대략). 10시간이면 72,000 → 상한에 걸린다
WAVE_MIN_POINTS = 1_000
WAVE_MAX_POINTS = 20_000
ENGINE_TRACK = "engine"
LABEL_TRACK = "labels"            # timeline.TRACK_ORDER 의 라벨 트랙
# 엔진 등급 → timeline.MARK_STYLES 의 종류 (강한 판정은 채운 표시, 약한 판정은 속이 빈 표시)
ENGINE_MARK_KIND = {"direct": "engine", "likely": "engine", "related_only": "engine_weak", "unclear": "engine_weak"}
LINK_MAX_BYTES = 64 * 1024 * 1024
ENGINE_ITEM = "engine_results"    # app.core.visibility.ENGINE_RESULTS (엔진 판정, 분석 모드). 봉인 AI 결과(ai_results)와 다르다

_ORPHANS: set = set()             # 화면이 닫힐 때 아직 도는 워커를 붙잡아 둔다(8장: setParent(None) + deleteLater)


# --------------------------------------------------------------------------- 순수 도우미 (Qt 없음)
def _order_key(r: dict):
    t = r.get("t_ms")
    if isinstance(t, bool) or not isinstance(t, int):
        return (1, 0, str(r.get("chat_id") or ""))
    return (0, t, str(r.get("chat_id") or ""))


def viewer_numbers(records: list[dict]) -> dict[str, int]:
    """viewer_id → 표시용 번호(1부터). (t_ms, chat_id) 순으로 처음 나온 순서, 익명은 넣지 않는다 (03 14-2,
    tools/chat_window.py 와 같은 규칙). records 는 이미 정렬돼 있다고 본다."""
    out: dict[str, int] = {}
    for r in records:
        vid = r.get("viewer_id")
        if not vid or vid == "anon" or r.get("is_anonymous"):
            continue
        if vid not in out:
            out[vid] = len(out) + 1
    return out


def chat_density(records: list[dict], bucket_ms: int = DENSITY_BUCKET_MS, duration_ms: int = 0) -> list[int]:
    """t_ms 를 bucket_ms 버킷으로 센 목록. 길이는 max(duration, 마지막 t_ms+1) 을 덮는다. 음수 t_ms 는 버린다."""
    ts = [r["t_ms"] for r in records if isinstance(r.get("t_ms"), int) and not isinstance(r.get("t_ms"), bool)
          and r["t_ms"] >= 0]
    end = max([duration_ms] + [t + 1 for t in ts]) if (ts or duration_ms) else 0
    n = -(-end // bucket_ms) if end > 0 else 0
    counts = [0] * n
    for t in ts:
        counts[t // bucket_ms] += 1
    return counts


def align_waveform(values: list[float], file_duration_ms: int, vod_of_file0_ms: int) -> tuple[list[float], int]:
    """파일 시각 0~file_duration 의 파형을 VOD 0 부터 시작하는 목록으로 옮긴다.

    파일 0초가 VOD 의 vod_of_file0_ms 다. 양수면 앞을 0.0 으로 채우고, 음수면 앞을 잘라 낸다.
    돌려주는 (values, span_ms): span_ms 는 VOD 0 부터 values 끝까지의 길이. 점 하나의 폭(bw)은 원래와 같다.

    이동량이 점 폭의 정수배가 아니면(보통 그렇다) 반올림하지 않고, 새 격자(VOD 0 부터 bw 간격)의 점마다 겹치는
    원래 두 점을 겹친 길이 비율로 섞는다. 반올림하면 파형이 최대 bw/2(≈250ms) 어긋나 프레임 단위로 확대했을 때
    채팅·라벨·재생선과 맞지 않는다. 정수배면 결과는 단순 이동과 같다.
    """
    if not values or file_duration_ms <= 0:
        return [], 0
    n = len(values)
    bw = file_duration_ms / n
    frac = vod_of_file0_ms / bw                 # 원래 점 j 는 새 격자의 [j+frac, j+1+frac) 를 덮는다
    if abs(frac - round(frac)) < 1e-9:          # 정수배: 그대로 옮긴다
        shift = int(round(frac))
        out = [0.0] * shift + list(values) if shift >= 0 else list(values[-shift:])
        return out, int(round(len(out) * bw))
    k0 = math.floor(frac)
    f = frac - k0                               # 0 < f < 1
    # 새 점 k (k0 ≤ k ≤ k0+n) 는 원래 점 k-k0-1 의 뒤 f 만큼과 원래 점 k-k0 의 앞 (1-f) 만큼을 덮는다
    out = [0.0] * k0 if k0 > 0 else []
    for k in range(max(0, k0), k0 + n + 1):
        j = k - k0
        a = values[j - 1] if 0 <= j - 1 < n else 0.0
        b = values[j] if 0 <= j < n else 0.0
        out.append(a * f + b * (1.0 - f))
    return out, int(round(len(out) * bw))


def waveform_points(file_duration_ms: int) -> int:
    return max(WAVE_MIN_POINTS, min(WAVE_MAX_POINTS, int(file_duration_ms // WAVE_MS_PER_POINT) or 1))


def find_link_file(derived_dir: str) -> str | None:
    """derived\\link-*.json 중 가장 최근(수정 시각) 파일. 없으면 None. (-review.csv 등은 확장자로 빠진다)"""
    files = [p for p in glob.glob(os.path.join(derived_dir, "link-*.json")) if os.path.isfile(p)]
    if not files:
        return None
    return max(files, key=lambda p: (os.path.getmtime(p), p))


def engine_markers(obj: dict) -> list[tuple[int, str]]:
    """ai-result(link) 의 reactions 중 등급이 붙은 것 → [(reaction_time_ms, grade)] (VOD 시각, 시각 순).

    time_axis 가 vod_ms 가 아니면 ValueError(축이 다른 결과를 VOD 타임라인에 그리지 않는다).
    """
    if obj.get("time_axis") != "vod_ms":
        raise ValueError(f"엔진 결과의 시각 축이 vod_ms 가 아닙니다: {obj.get('time_axis')!r}")
    out = []
    for r in obj.get("reactions") or []:
        if not isinstance(r, dict) or r.get("result") != "graded":
            continue
        t = r.get("reaction_time_ms")
        if isinstance(t, bool) or not isinstance(t, int):
            continue
        out.append((t, str(r.get("grade") or "graded")))
    out.sort()
    return out


def analysis_view_allowed(session_kind, phase) -> bool:
    """'분석 보기'(엔진 결과 표시)를 이 세션에서 보여도 되는가 — app.core.visibility.can_show(engine_results, …).

    visibility 모듈이 없거나 판단이 실패하면 False(기본 숨김). 세션 없음(None)도 visibility 가 막는다(모르는 종류).
    라벨링 세션(primary·relabel_*)은 visibility 표에서 엔진 결과가 '열지 않음' 이라 False 다.
    """
    try:
        from ..core import visibility as vis
    except Exception:  # noqa: BLE001 — 아직 없는 모듈: 기본값 숨김
        return False
    try:
        return bool(vis.can_show(ENGINE_ITEM, phase, session_kind))
    except Exception:  # noqa: BLE001
        LOG.info("visibility 판단 실패 — 분석 보기를 숨김")
        return False


def may_write_media(session_kind, phase) -> bool:
    """영상 연결을 project.json 에 저장해도 되는가.

    세션 없음(None) = 프로젝트 준비 상태(새 프로젝트 마법사의 '영상 파일 선택' 과 같은 자리)라 허용한다.
    세션이 있으면 visibility.files_to_open(…).can_write('project_json') 을 따른다(primary 는 '보정·영상만 쓰기').
    visibility 가 없으면 세션이 있을 때 막는다.
    """
    if session_kind is None:
        return True
    try:
        from ..core import visibility as vis
        return bool(vis.files_to_open(session_kind, phase).can_write("project_json"))
    except Exception:  # noqa: BLE001
        return False


@dataclass
class ChatLoad:
    """워커가 읽은 채팅 묶음. panel_data 는 chat_panel.ChatData(병렬 배열)를 워커에서 미리 만든 것(없으면 None).

    panel_data 를 만들었으면 records 는 비운다(02 9장 '채팅 메모리 20만 건 100MB 이하': 원본 dict 20만 개만으로
    350MB 쯤 된다). 건수는 count 로 본다.
    """
    records: list[dict] = field(default_factory=list)
    count: int = 0
    viewer_no: dict[str, int] = field(default_factory=dict)
    density: list[int] = field(default_factory=list)
    last_t_ms: int = 0
    problems: list[str] = field(default_factory=list)
    panel_data: object = None


def load_chat(messages_path: str, duration_ms: int = 0, prebuild: Callable | None = None) -> ChatLoad:
    """messages.jsonl → 정렬된 레코드·시청자 번호·10초 밀도. 파일이 없으면 빈 결과 + 문제 한 줄.

    prebuild(records, viewer_no) 를 주면 그 결과를 panel_data 에 담는다(채팅 표의 병렬 배열을 UI 스레드 밖에서 만든다,
    02 9장).
    """
    out = ChatLoad()
    if not os.path.isfile(messages_path):
        out.problems.append(tr("채팅 파일(chat/messages.jsonl)이 없습니다"))
        return out
    res = jsonl.read(messages_path)
    if res.corrupt_lines:
        out.problems.append(tr("채팅 파일의 깨진 줄 {0}개를 뺐습니다").format(len(res.corrupt_lines)))
    if res.truncated_tail:
        out.problems.append(tr("채팅 파일의 마지막 줄이 잘려 뺐습니다"))
    recs = sorted(res.records, key=_order_key)
    out.records = recs
    out.count = len(recs)
    out.viewer_no = viewer_numbers(recs)
    ts = [r["t_ms"] for r in recs if _order_key(r)[0] == 0]
    out.last_t_ms = ts[-1] if ts else 0
    out.density = chat_density(recs, DENSITY_BUCKET_MS, duration_ms)
    if prebuild is not None:
        out.panel_data = prebuild(recs, out.viewer_no)
        out.records = []                         # 병렬 배열로 옮겼다: dict 목록은 놓는다
    return out


def resolve_media_path(ref: dict | None, project_root: str) -> tuple[str | None, str | None]:
    """mediaRef → (지금 쓸 경로, 문제). path_abs → path_rel 순으로 찾고 media.verify(크기·부분 지문)로 확인한다."""
    if not ref:
        return None, tr("영상 파일이 아직 연결되지 않았습니다")
    tried = []
    cands = [ref.get("path_abs")]
    if ref.get("path_rel"):
        cands.append(os.path.normpath(os.path.join(project_root, ref["path_rel"])))
    for p in cands:
        if not p or not os.path.isfile(p):
            continue
        ok, why = media.verify(ref, p)
        if ok:
            return p, None
        tried.append(why)
    if tried:
        return None, tr("연결된 영상과 다른 파일입니다: {0}").format(tried[0])
    return None, tr("연결된 영상 파일을 찾지 못했습니다 (옮겼거나 지웠을 수 있습니다)")


class TimeMap:
    """활성 보정 판 하나로 파일 시각 ↔ VOD 시각."""

    def __init__(self, calibration_obj: dict | None):
        self.problem: str | None = None
        try:
            self.cal = tc.calibration_from_json(calibration_obj) if calibration_obj else tc.Calibration()
        except (KeyError, TypeError, ValueError) as e:
            self.cal = tc.Calibration()
            self.problem = tr("보정 기록을 읽지 못해 오프셋 0 으로 봅니다: {0}").format(type(e).__name__)

    def to_vod(self, file_ms: int) -> int:
        return tc.video_to_chat_ms(int(file_ms), self.cal)

    def to_file(self, vod_ms: int) -> int:
        return tc.chat_to_video_ms(int(vod_ms), self.cal)

    @property
    def status(self) -> str:
        return self.cal.status

    def describe(self) -> str:
        c = self.cal
        n = len(c.anchors)
        if c.status == "verified":
            return tr("보정 rev {0} · 오프셋 {1} (앵커 {2})").format(c.rev, tc.fmt_offset(c.offset_ms), n)
        if c.status == "skipped":
            return tr("경고: 정렬 건너뜀 (rev {0}) — {1}").format(c.rev, c.skip_reason or tr("사유 없음"))
        return tr("경고: 보정 확인 안 됨 (rev {0} · 오프셋 {1})").format(c.rev, tc.fmt_offset(c.offset_ms))


class _HeldLock:
    """이미 잡은 잠금(QLockFile)을 ProjectStore 에 넘기는 얇은 껍데기. tryLock 은 바로 성공하고 unlock 은 원래 잠금을 푼다."""

    def __init__(self, lk):
        self._lk = lk

    def tryLock(self, timeout: int = 0) -> bool:  # noqa: N802 — QLockFile 모양
        return self._lk is not None

    def unlock(self) -> None:
        lk, self._lk = self._lk, None
        if lk is not None:
            lk.unlock()

    def getLockInfo(self):  # noqa: N802
        return self._lk.getLockInfo() if self._lk is not None and hasattr(self._lk, "getLockInfo") else None


def _set_text(w: QLabel, text: str) -> None:
    if w.text() != text:
        w.setText(text)


# --------------------------------------------------------------------------- 워커
class FuncWorker(QThread):
    """fn(cancelled: () -> bool) 을 백그라운드에서 돌려 done(결과, 오류 문자열) 로 넘긴다."""

    done = Signal(object, str)

    def __init__(self, fn: Callable[[Callable[[], bool]], object], parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:
        try:
            res = self._fn(self.cancelled)
        except Exception as e:  # noqa: BLE001 — 결과로 넘긴다
            LOG.info("작업대 작업 실패: %s", type(e).__name__)
            self.done.emit(None, f"{type(e).__name__}: {e}")
            return
        self.done.emit(res, "")


# --------------------------------------------------------------------------- 빈 자리 부품
class _MissingPlayer(QLabel):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playingChanged = Signal(bool)
    errorOccurred = Signal(str)

    def __init__(self, why: str = "", parent=None):
        super().__init__(tr("플레이어를 쓸 수 없습니다 {0}").format(why), parent)
        self.setAlignment(Qt.AlignCenter)

    def load(self, path): self.errorOccurred.emit(tr("플레이어가 없어 영상을 열 수 없습니다"))
    def play(self): pass
    def pause(self): pass
    def toggle(self): pass
    def seek(self, file_ms, exact=True): pass
    def position_ms(self): return 0
    def duration_ms(self): return 0
    def frame_step(self, n): pass
    def set_speed(self, x): pass
    def set_volume(self, v): pass
    def set_ab_loop(self, a_ms, b_ms): pass
    def close_player(self): pass


class _MissingChat(QLabel):
    chatActivated = Signal(str, int)

    def __init__(self, why: str = "", parent=None):
        super().__init__(tr("채팅 패널을 쓸 수 없습니다 {0}").format(why), parent)
        self.setAlignment(Qt.AlignCenter)

    def set_messages(self, records, viewer_no=None): pass
    def set_time_ms(self, vod_ms, follow=True): pass
    def set_filter(self, text): pass
    def selected_chat_id(self): return None
    def select_chat(self, chat_id): pass


class _MissingTimeline(QLabel):
    seekRequested = Signal(int)
    viewChanged = Signal(int, int)

    def __init__(self, why: str = "", parent=None):
        super().__init__(tr("타임라인을 쓸 수 없습니다 {0}").format(why), parent)
        self.setAlignment(Qt.AlignCenter)

    def set_duration(self, ms): pass
    def set_position(self, ms): pass
    def set_view(self, start_ms, end_ms): pass
    def zoom(self, factor, anchor_ms): pass
    def set_segments(self, items): pass
    def set_density(self, counts, bucket_ms): pass
    def set_waveform(self, values, span_ms): pass
    def set_markers(self, name, items): pass
    def clear_markers(self, name): pass


def _default_part(kind: str):
    """기본 부품 클래스. import 실패면 (빈 자리 클래스, 사유)."""
    try:
        if kind == "player":
            from ..player import MpvPlayer
            return MpvPlayer, None
        if kind == "chat":
            from .chat_panel import ChatPanel
            return ChatPanel, None
        from .timeline import Timeline
        return Timeline, None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}"


_MISSING = {"player": _MissingPlayer, "chat": _MissingChat, "timeline": _MissingTimeline}
_PART_NAMES = {"player": "플레이어", "chat": "채팅 패널", "timeline": "타임라인"}


# --------------------------------------------------------------------------- 작업대
class Workbench(QWidget):
    """02 7-3 의 둘러보기 상태 작업대."""

    statusChanged = Signal(str)
    seekIssued = Signal(int, int)        # (file_ms, vod_ms) — 영상이 없으면 file_ms = -1

    def __init__(self, parent=None, *, parts: dict | None = None, session_kind=None, phase=None,
                 labeler_id: str | None = None, session=None, store_options: dict | None = None):
        """session(app.session.SessionContext)을 주면 open_project 때 app.store.ProjectStore 로 라벨 기록을 연다.
        그때 session_kind·labeler_id 를 따로 주지 않으면 세션 값을 쓴다. store_options 는 ProjectStore.open 의
        키워드 인자(시험의 clock·uuid_factory·data_root 등)."""
        super().__init__(parent)
        if session is not None:
            # 세션이 있으면 그 종류·라벨러가 기준이다. 다른 값을 같이 주면 거부한다 — '분석 보기' 허용(session_kind)과
            # 실제로 쓰는 기록 파일(session)이 어긋나 라벨링 세션에 엔진 결과가 보이는 길을 막는다.
            if session_kind is not None and session_kind != session.session_kind:
                raise ValueError("session_kind 가 session.session_kind 와 다릅니다")
            if labeler_id is not None and labeler_id != session.labeler_id:
                raise ValueError("labeler_id 가 session.labeler_id 와 다릅니다")
            session_kind, labeler_id = session.session_kind, session.labeler_id
        self.session_kind, self.phase, self.labeler_id = session_kind, phase, labeler_id
        self.session = session
        self.store = None                    # app.store.ProjectStore (세션이 있을 때만)
        self.store_problem: str | None = None
        self._store_options = dict(store_options or {})
        self._store_guard = threading.Lock()  # 워커가 연 store 와 close_workbench 가 엇갈리지 않게
        self.outcome = None
        self.project: project_mod.Project | None = None
        self.timemap = TimeMap(None)
        self.chat_data = ChatLoad()
        self.media_path: str | None = None
        self.notes: list[str] = []           # 띠에 보이는 줄
        self.part_problems: list[str] = []
        self._vod_pos = 0
        self._file_pos: int | None = None
        self._file_duration = 0
        self._vod_duration = 0
        self._playing = False
        self._jobs: dict[str, FuncWorker] = {}
        self._closed = False
        self._link_path: str | None = None
        self._status_last = ""
        self._cal_style: str | None = None
        # 타임라인에 그린 유효 라벨 (종류 → entity_id → 기록). 저장·되돌리기 때는 바뀐 대상만 고친다(_apply_saved)
        self._label_heads: dict[str, dict[str, dict]] = {"segment": {}, "utterance": {}}
        self._labels_clean = False

        self.player =self._make_part("player", parts)
        self.chat = self._make_part("chat", parts)
        self.timeline = self._make_part("timeline", parts)

        # ---- 배치
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 2)
        self.banner = QLabel("")
        self.banner.setObjectName("hint")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        root.addWidget(self.banner)

        self.hsplit = QSplitter(Qt.Horizontal)
        self.vsplit = QSplitter(Qt.Vertical)
        pbox = QWidget()
        pl = QVBoxLayout(pbox)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.player, 1)
        mrow = QHBoxLayout()
        self.media_info = QLabel(tr("영상 없음"))
        self.media_info.setObjectName("hint")
        self.btn_media = QPushButton(tr("영상 파일 연결…"))
        self.btn_media.clicked.connect(self.choose_and_connect_media)
        mrow.addWidget(self.media_info, 1)
        mrow.addWidget(self.btn_media)
        pl.addLayout(mrow)
        self.vsplit.addWidget(pbox)
        self.vsplit.addWidget(self.timeline)
        self.vsplit.setStretchFactor(0, 3)
        self.vsplit.setStretchFactor(1, 1)
        self.hsplit.addWidget(self.vsplit)

        cbox = QWidget()
        cl = QVBoxLayout(cbox)
        cl.setContentsMargins(0, 0, 0, 0)
        # 채팅 패널이 자기 검색 칸(search: QLineEdit, 디바운스 포함)을 가지면 그것을 쓰고, 없으면 여기서 만든다
        own = getattr(self.chat, "search", None)
        self._own_search = not isinstance(own, QLineEdit)
        if self._own_search:
            srow = QHBoxLayout()
            lab = QLabel(tr("채팅"))
            lab.setObjectName("panelTitle")
            self.search = QLineEdit()
            self.search.setPlaceholderText(tr("검색 (Ctrl+F)"))
            self.search.setClearButtonEnabled(True)
            srow.addWidget(lab)
            srow.addWidget(self.search, 1)
            cl.addLayout(srow)
        else:
            self.search = own
        cl.addWidget(self.chat, 1)
        self.hsplit.addWidget(cbox)
        self.hsplit.setStretchFactor(0, 3)
        self.hsplit.setStretchFactor(1, 2)
        root.addWidget(self.hsplit, 1)

        srow2 = QHBoxLayout()
        self.lbl_vod = QLabel("")
        self.lbl_file = QLabel("")
        self.lbl_cal = QLabel("")
        self.lbl_chats = QLabel("")
        self.lbl_session = QLabel("")        # 02 7-7: 라벨러 · 세션 ID · 기준서 버전 (세션이 있을 때만)
        for w in (self.lbl_vod, self.lbl_file, self.lbl_cal, self.lbl_chats, self.lbl_session):
            w.setObjectName("statusChip")
            srow2.addWidget(w)
        srow2.addStretch(1)
        root.addLayout(srow2)

        # ---- 검색 디바운스 (QTimer.start 에 시그널을 바로 잇지 않는다 — 8장)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(FILTER_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self._apply_filter)
        if self._own_search:
            self.search.textChanged.connect(lambda _t: self._filter_timer.start())

        # ---- 부품 연결
        self.player.positionChanged.connect(self._on_player_position)
        self.player.durationChanged.connect(self._on_player_duration)
        self.player.playingChanged.connect(self._on_playing)
        self.player.errorOccurred.connect(self._on_player_error)
        self.chat.chatActivated.connect(self._on_chat_activated)
        self.timeline.seekRequested.connect(self.seek_vod)

        # ---- 분석 보기
        self.analysis_action = QAction(tr("분석 보기 (엔진 반응)"), self)
        self.analysis_action.setCheckable(True)
        self.analysis_action.toggled.connect(self.set_analysis_view)
        self.analysis_action.setVisible(analysis_view_allowed(self.session_kind, self.phase))

        self._install_shortcuts()
        self._refresh_notes()
        self._update_status(full=True)

    # ------------------------------------------------------------ 부품
    def _make_part(self, kind: str, parts: dict | None):
        cls, why = (parts[kind], None) if parts and kind in parts else _default_part(kind)
        if cls is not None:
            try:
                return cls()
            except Exception as e:  # noqa: BLE001 — libmpv 없음 등
                why = type(e).__name__
                LOG.warning("작업대 부품 %s 생성 실패: %s", kind, why)
        self.part_problems.append(tr("{0} 부품을 쓸 수 없습니다 ({1})").format(_PART_NAMES[kind], why))
        return _MISSING[kind](f"({why})")

    def _chat_prebuild(self):
        """채팅 부품이 진짜 ChatPanel 이면 그 병렬 배열(ChatData)을 워커에서 만들 함수. 아니면 None."""
        try:
            from .chat_panel import ChatData as PanelData, ChatPanel
        except Exception:  # noqa: BLE001
            return None
        if isinstance(self.chat, ChatPanel):
            return lambda recs, vno: PanelData(recs, vno)
        return None

    # ------------------------------------------------------------ 단축키 (02 7-6 중 재생·이동·검색)
    def _install_shortcuts(self) -> None:
        def sc(key: str, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WindowShortcut)      # 보이는 동안만 동작한다. 글자 칸은 ShortcutOverride 로 글자를 먼저 가져간다
            s.activated.connect(fn)
            return s
        self.shortcuts = [
            sc("Space", self.toggle_play), sc("K", self.toggle_play),
            sc("Left", lambda: self.step(-STEP_MS)), sc("Right", lambda: self.step(STEP_MS)),
            sc("J", lambda: self.step(-STEP_MS)), sc("L", lambda: self.step(STEP_MS)),
            sc("Shift+Left", lambda: self.step(-SMALL_STEP_MS)), sc("Shift+Right", lambda: self.step(SMALL_STEP_MS)),
            sc("Ctrl+Left", lambda: self.step(-BIG_STEP_MS)), sc("Ctrl+Right", lambda: self.step(BIG_STEP_MS)),
            sc(",", lambda: self.frame_step(-1)), sc(".", lambda: self.frame_step(1)),
            sc("Ctrl+F", self.focus_search),
        ]

    # ------------------------------------------------------------ 작업 실행
    def _run_job(self, name: str, fn, on_done, sync: bool) -> None:
        old = self._jobs.pop(name, None)
        if old is not None:
            self._orphan(old)
        if sync:
            try:
                res, err = fn(lambda: False), ""
            except Exception as e:  # noqa: BLE001
                res, err = None, f"{type(e).__name__}: {e}"
            on_done(res, err)
            return
        w = FuncWorker(fn, self)
        self._jobs[name] = w

        def finished(res, err, w=w, name=name):
            if self._jobs.get(name) is w:
                del self._jobs[name]
            if not self._closed:
                on_done(res, err)
        w.done.connect(finished)
        w.finished.connect(w.deleteLater)
        w.start()

    @staticmethod
    def _orphan(w: FuncWorker) -> None:
        w.cancel()
        try:
            w.done.disconnect()
        except (RuntimeError, TypeError):
            pass
        if w.isRunning():
            w.setParent(None)
            _ORPHANS.add(w)
            w.finished.connect(lambda w=w: (_ORPHANS.discard(w), w.deleteLater()))

    def wait_jobs(self, timeout_ms: int = 30_000) -> bool:
        """시험·종료용: 지금 도는 작업이 끝날 때까지 기다리고 결과 시그널을 처리한다."""
        from PySide6.QtCore import QCoreApplication, QElapsedTimer
        t = QElapsedTimer()
        t.start()
        while self._jobs and t.elapsed() < timeout_ms:
            for w in list(self._jobs.values()):
                w.wait(20)
            QCoreApplication.processEvents()
        QCoreApplication.processEvents()
        return not self._jobs

    # ------------------------------------------------------------ 프로젝트
    def open_project(self, outcome, *, sync: bool = False) -> None:
        """OpenOutcome(project_view) 또는 Project 를 받아 연다. 잠금은 이 작업대가 넘겨받아 close_workbench 에서 푼다."""
        proj = getattr(outcome, "project", outcome)
        self.outcome = outcome if hasattr(outcome, "project") else None
        self.project = proj
        data = proj.data or {}
        self.notes = []
        if proj.read_only:
            self.notes += [tr("읽기 전용: {0}").format(p) for p in proj.problems[:5]]
        self.notes += list(getattr(outcome, "warnings", proj.warnings))
        self.timemap = TimeMap(data.get("calibration"))
        if self.timemap.problem:
            self.notes.append(self.timemap.problem)
        cur = (data.get("media") or {}).get("current")
        mrev = (data.get("media") or {}).get("media_rev", 0)
        if self.timemap.cal.media_rev and self.timemap.cal.media_rev != mrev:
            self.notes.append(tr("활성 보정은 영상 판 {0} 기준인데 지금 영상 판은 {1} 입니다. 보정을 다시 재야 합니다.")
                              .format(self.timemap.cal.media_rev, mrev))
        self._vod_duration = int((data.get("broadcast") or {}).get("duration_ms") or 0)
        self._vod_pos = 0
        self._refresh_notes()
        self._update_status(full=True)
        self._link_path = find_link_file(proj.paths.derived_dir)
        self.analysis_action.setEnabled(self._link_path is not None)
        self.analysis_action.setToolTip("" if self._link_path else tr("derived\\link-*.json 이 없습니다"))

        if self.session is not None:
            self._open_store(outcome, proj, sync)

        mpath = proj.paths.messages_jsonl
        dur = self._vod_duration
        pre = self._chat_prebuild()
        self._run_job("chat", lambda cancelled: load_chat(mpath, dur, pre), self._on_chat_loaded, sync)
        if cur:
            root = proj.root
            self.media_info.setText(tr("영상 확인 중…"))
            self._run_job("media_check", lambda cancelled: resolve_media_path(cur, root), self._on_media_checked, sync)
        else:
            self.media_info.setText(tr("영상 파일이 아직 연결되지 않았습니다"))

    # ------------------------------------------------------------ 라벨 기록 (app.store)
    def _open_store(self, outcome, proj, sync: bool) -> None:
        """세션으로 ProjectStore 를 연다(워커). project_view 가 잡은 locks/open.lock 은 store 에 넘긴다 —
        같은 프로세스가 같은 잠금을 두 번 잡으면 실패하기 때문이다. 넘긴 뒤 잠금은 store.close() 가 푼다."""
        from .. import store as store_mod
        self._close_store()
        held = getattr(outcome, "lock", None)
        if held is not None:
            outcome.lock = None
            lock_factory = lambda _path, held=held: _HeldLock(held)  # noqa: E731
        else:
            # 잠금은 ui 층이 주입한다(입출력 층은 QtCore 를 직접 쓰지 않는다). 따로 주지 않았으면 project_view 의 QLockFile
            lock_factory = self._store_options.get("lock_factory")
            if lock_factory is None:
                from .project_view import default_lock_factory
                lock_factory = default_lock_factory
        opts = {k: v for k, v in self._store_options.items() if k != "lock_factory"}
        root, session = proj.root, self.session
        self.store_problem = None

        def job(cancelled):
            try:
                st = store_mod.ProjectStore.open(root, session, lock_factory, **opts)
            except BaseException:
                if held is not None and getattr(held, "isLocked", lambda: True)():
                    held.unlock()            # 넘기기 전에 실패: 잡아 둔 잠금을 놓는다
                raise
            with self._store_guard:
                if self._closed:
                    st.close()
                    return None
                self.store = st
            return st
        self._run_job("store", job, self._on_store_opened, sync)

    def _on_store_opened(self, st, err: str) -> None:
        if st is None:
            self.store_problem = tr("라벨 기록을 열지 못했습니다: {0}").format(err[:200])
            self._note(self.store_problem)
            return
        if st.read_only:
            self._note(tr("라벨 기록 읽기 전용: {0}").format(st.read_only_reason or ""))
        for w in st.warnings[:5]:
            self._note(str(w))
        self.refresh_labels()
        self._update_status(full=True)

    def refresh_labels(self) -> None:
        """store 의 현재 유효 라벨을 모두 다시 계산해 타임라인에 그린다: segment → 활동 구간, utterance → 'labels' 마커.

        사본 없이(effective(copy_records=False)) 읽는다. 받은 dict 는 store 내부 객체라 고치지 않는다.
        """
        if self.store is None:
            return
        try:
            eff = self.store.effective(copy_records=False)
        except Exception as e:  # noqa: BLE001
            self._note(tr("라벨 계산 실패: {0}").format(type(e).__name__))
            return
        heads: dict[str, dict[str, dict]] = {k: {} for k in self._label_heads}
        for kind, d in heads.items():
            for r in eff.items(kind):
                d[r["entity_id"]] = r
        self._label_heads = heads
        # 충돌·무효 줄이 있으면 다음 저장도 전체 계산으로 한다(증분 갱신은 깨끗한 기록에서만 resolve 결과와 같다)
        self._labels_clean = not eff.conflicts() and not eff.invalid_ids
        self._push_labels(tuple(heads))
        bad = [p for p in eff.problems if p.code in ("cycle", "fork", "duplicate_id", "invalid_ref")]
        if bad:
            self._note(tr("라벨 기록에 확인할 문제가 {0}건 있습니다 (충돌·순환 등)").format(len(bad)))

    def _push_labels(self, kinds) -> None:
        if "segment" in kinds:
            self.timeline.set_segments(list(self._label_heads["segment"].values()))
        if "utterance" in kinds:
            marks = []
            for r in self._label_heads["utterance"].values():
                t = (r.get("body") or {}).get("start_ms")
                if isinstance(t, int) and not isinstance(t, bool):
                    marks.append((t, "reaction"))
            self.timeline.set_markers(LABEL_TRACK, marks)

    def _apply_saved(self, rec) -> None:
        """방금 저장한 줄 하나만 반영한다(02 8장: UI 스레드 한 번 16ms). 기록 전체를 다시 resolve 하지 않는다.

        store 는 supersedes 가 그 대상의 유일한 머리일 때만 쓰므로, 지난 전체 계산이 깨끗했다면(충돌·무효 없음)
        새 줄이 곧 그 대상의 현재 판이다(retract 면 철회). 깨끗하지 않았거나 모르는 모양이면 전체 계산으로 돌아간다.
        """
        if not self._labels_clean or not isinstance(rec, dict) or not isinstance(rec.get("entity_id"), str):
            self.refresh_labels()
            return
        eid, kind = rec["entity_id"], rec.get("kind")
        changed = set()
        if kind == "retract":
            for k, d in self._label_heads.items():
                if d.pop(eid, None) is not None:
                    changed.add(k)
        elif kind in self._label_heads:
            self._label_heads[kind][eid] = rec
            changed.add(kind)
        if changed:
            self._push_labels(changed)

    def save_label(self, draft: dict) -> dict:
        """라벨 한 건 저장(store.append_label) 뒤 타임라인에서 그 대상만 고친다. 세션·store 가 없으면 RuntimeError."""
        if self.store is None:
            raise RuntimeError("라벨 기록이 열려 있지 않습니다 (세션 없음)")
        rec = self.store.append_label(draft)
        self._apply_saved(rec)
        return rec

    def undo_last(self) -> dict | None:
        """마지막 라벨 되돌리기(store.retract_last, 02 3-3 의 Ctrl+Z)."""
        if self.store is None:
            return None
        rec = self.store.retract_last()
        self._apply_saved(rec)
        return rec

    def _close_store(self) -> None:
        with self._store_guard:
            st, self.store = self.store, None
        if st is not None:
            try:
                st.close()
            except Exception:  # noqa: BLE001
                LOG.exception("라벨 기록 닫기 실패")

    def _on_chat_loaded(self, data: ChatLoad | None, err: str) -> None:
        if data is None:
            self._note(tr("채팅을 읽지 못했습니다: {0}").format(err))
            return
        self.chat_data = data
        for p in data.problems:
            self._note(p)
        LOG.info("작업대: 채팅 %d건, 시청자 %d명", data.count, len(data.viewer_no))
        if data.panel_data is not None:
            self.chat.set_messages(data.panel_data)
            data.records = []
        else:
            self.chat.set_messages(data.records, data.viewer_no)
        self._grow_duration(data.last_t_ms + 1000 if data.count else 0)
        self.timeline.set_density(data.density, DENSITY_BUCKET_MS)
        self.chat.set_time_ms(self._vod_pos, True)
        self._update_status(full=True)

    def _on_media_checked(self, res, err: str) -> None:
        if res is None:
            self.media_info.setText(tr("영상 확인 실패: {0}").format(err))
            return
        path, problem = res
        if path is None:
            self.media_info.setText(problem or "")
            self._note(problem or "")
            return
        self._load_media(path)

    def _grow_duration(self, vod_end: int) -> None:
        if vod_end > self._vod_duration:
            self._vod_duration = int(vod_end)
        self.timeline.set_duration(self._vod_duration)
        self.timeline.set_view(0, max(1, self._vod_duration))

    # ------------------------------------------------------------ 영상
    def _load_media(self, path: str) -> None:
        self.media_path = path
        self.media_info.setText(os.path.basename(path))
        self.media_info.setToolTip(path)
        self.btn_media.setText(tr("영상 바꾸기…"))
        self.player.load(path)
        # 파일 0초 위치로 맞춘다(채팅·타임라인이 그 VOD 시각을 보이게)
        self._file_pos = 0
        self._set_vod(self.timemap.to_vod(0), from_player=True)
        self._start_waveform(path)

    def choose_media_file(self) -> str | None:
        """영상 고르기 대화상자. 시험에서는 바꿔 끼운다."""
        start = os.path.dirname(self.media_path) if self.media_path else os.path.expanduser("~")
        p, _ = QFileDialog.getOpenFileName(self, tr("영상 파일 고르기"), start,
                                           tr("영상 (*.mp4 *.mkv *.ts *.flv *.mov *.webm);;모든 파일 (*)"))
        return os.path.normpath(p) if p else None

    def ask_replace_reason(self) -> str | None:
        txt, ok = QInputDialog.getText(self, tr("미디어 교체"),
                                       tr("연결된 영상과 지문이 다른 파일입니다. 바꾸는 사유를 적어 주세요.\n"
                                          "(바꾸면 영상 판이 올라가고 시간 정렬을 다시 해야 합니다)"))
        return txt.strip() if ok and txt.strip() else None

    def choose_and_connect_media(self) -> None:
        p = self.choose_media_file()
        if p:
            self.connect_media(p)

    def connect_media(self, path: str, *, persist: bool = True, reason: str | None = None,
                      sync: bool = False) -> None:
        """영상 파일을 연결한다. persist=True 면 app.media.media_ref 로 project.json 의 media 를 고친다.

        - 연결된 영상이 없으면 current 지정, media_rev 0 → 1.
        - 지문이 같으면(옮긴 파일) current 의 경로만 고친다.
        - 지문이 다르면 '미디어 교체': 사유 필수, history 에 옛 판을 남기고 media_rev+1. 보정은 다시 재야 한다.
        persist=False 면 저장하지 않고 재생만 한다(동작 확인용).
        """
        why = media.check_path_string(path)
        if why:
            self._note(tr("영상 경로를 받을 수 없습니다: {0}").format(why))
            return
        if not persist or self.project is None:
            self._load_media(os.path.normpath(path))
            return
        root = self.project.root
        self.media_info.setText(tr("영상 확인 중 (지문·ffprobe)…"))
        self._run_job("media_ref", lambda cancelled: media.media_ref(path, root),
                      lambda ref, err: self._on_media_ref(path, ref, err, reason), sync)

    def _on_media_ref(self, path: str, ref: dict | None, err: str, reason: str | None) -> None:
        if ref is None:
            self.media_info.setText(tr("영상을 확인하지 못했습니다"))
            self._note(tr("영상을 확인하지 못했습니다: {0}").format(err))
            return
        proj = self.project
        cur = ((proj.data or {}).get("media") or {}).get("current")
        replacing = bool(cur) and (cur.get("partial_fp") != ref["partial_fp"] or cur.get("size") != ref["size"])
        if replacing and not reason:
            reason = self.ask_replace_reason()
            if not reason:
                self.media_info.setText(tr("영상 교체를 취소했습니다"))
                return
        if proj.read_only:
            self._note(tr("읽기 전용이라 영상 연결을 project.json 에 저장하지 않았습니다"))
        elif not may_write_media(self.session_kind, self.phase):
            self._note(tr("이 세션은 project.json 을 쓸 수 없어 영상 연결을 저장하지 않았습니다 (이번만 엽니다)"))
        else:
            by = self.labeler_id or (proj.data or {}).get("created_by") or "L01"

            def mutate(d: dict) -> None:
                m = d.setdefault("media", {"media_rev": 0, "current": None, "history": []})
                old = m.get("current")
                if old is None:
                    m["media_rev"] = int(m.get("media_rev", 0)) + 1
                elif replacing:
                    m.setdefault("history", []).append({
                        "media_rev": max(1, int(m.get("media_rev", 1))),
                        "partial_fp": old["partial_fp"], "size": old["size"],
                        "duration_ms": (old.get("probe") or {}).get("duration_ms"),
                        "replaced_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "replaced_by": by, "reason": reason[:500],
                    })
                    m["media_rev"] = int(m.get("media_rev", 0)) + 1
                m["current"] = ref
            try:
                self._write_project(mutate, by)
                LOG.info("영상 연결 저장: media_rev %s, 교체=%s", proj.data["media"]["media_rev"], replacing)
            except Exception as e:  # noqa: BLE001 — Conflict·ReadOnly(project·store)·ValueError·OSError: 띠에 알린다
                self._note(tr("영상 연결을 저장하지 못했습니다: {0}").format(e))
            if replacing:
                self._note(tr("영상을 바꿨습니다. 시간 정렬(보정)을 다시 해야 합니다."))
        self._load_media(os.path.normpath(path))

    def _write_project(self, mutate, by: str) -> None:
        """project.json 쓰기. 라벨 기록(store)이 열려 있으면 store 를 통해서만 쓴다(02 3-2 '유일한 쓰기 통로').

        작업대와 store 가 project.json 을 따로 읽어 들고 있어, 한쪽만 쓰면 다른 쪽의 rev·media_rev 가 옛 값으로 남는다
        (라벨에 옛 media_rev 가 붙고 store.update_project 가 늘 ConflictError). 쓴 뒤 작업대 쪽 사본도 맞춘다.
        """
        st = self.store
        if st is not None:
            new = st.update_project(mutate)
            self.project.data = copy.deepcopy(new)
            return
        self.project.update(mutate, by=by)

    def _start_waveform(self, path: str) -> None:
        from .. import analysis

        def job(cancelled):
            info = analysis.probe(path, cancel=cancelled)
            if not info.get("has_audio"):
                return None
            dur = int(info.get("duration_ms") or 0)
            vals = analysis.compute_waveform(path, waveform_points(dur), cancel=cancelled)
            return vals, dur
        self._run_job("waveform", job, self._on_waveform, False)

    def _on_waveform(self, res, err: str) -> None:
        if res is None:
            if err:
                LOG.info("파형 실패")
                self._note(tr("파형을 만들지 못했습니다: {0}").format(err[:200]))
            return
        vals, dur = res
        aligned, span = align_waveform(vals, dur, self.timemap.to_vod(0))
        self.timeline.set_waveform(aligned, span)

    # ------------------------------------------------------------ 시각
    def file_to_vod(self, file_ms: int) -> int:
        return self.timemap.to_vod(file_ms)

    def vod_to_file(self, vod_ms: int) -> int:
        return self.timemap.to_file(vod_ms)

    def vod_position(self) -> int:
        return self._vod_pos

    def _set_vod(self, vod_ms: int, *, from_player: bool = False) -> None:
        self._vod_pos = int(vod_ms)
        self.chat.set_time_ms(self._vod_pos, True)
        self.timeline.set_position(self._vod_pos)
        self._update_status()

    def _on_player_position(self, file_ms: int) -> None:
        self._file_pos = int(file_ms)
        self._set_vod(self.timemap.to_vod(int(file_ms)), from_player=True)

    def _on_player_duration(self, file_ms: int) -> None:
        self._file_duration = int(file_ms)
        if file_ms > 0:
            self._grow_duration(self.timemap.to_vod(int(file_ms)))

    def _on_playing(self, on: bool) -> None:
        self._playing = bool(on)
        self._update_status()

    def _on_player_error(self, msg: str) -> None:
        self._note(tr("플레이어: {0}").format(msg))

    def seek_vod(self, vod_ms: int) -> None:
        """VOD 시각으로 탐색. 영상이 있으면 파일 시각으로 바꿔 플레이어에, 없으면 가상 위치만."""
        vod_ms = max(0, int(vod_ms))
        if self.media_path:
            f = max(0, self.timemap.to_file(vod_ms))
            if self._file_duration:
                f = min(f, self._file_duration)
            self.player.seek(f, True)
            self._file_pos = f
            self.seekIssued.emit(f, vod_ms)
            self._set_vod(self.timemap.to_vod(f))
        else:
            self.seekIssued.emit(-1, vod_ms)
            self._set_vod(vod_ms)

    def _on_chat_activated(self, chat_id: str, vod_ms: int) -> None:
        self.seek_vod(int(vod_ms) - SEEK_LEAD_MS)

    def step(self, delta_ms: int) -> None:
        self.seek_vod(self._vod_pos + int(delta_ms))

    def toggle_play(self) -> None:
        if self.media_path:
            self.player.toggle()

    def frame_step(self, n: int) -> None:
        if self.media_path:
            self.player.frame_step(int(n))

    def focus_search(self) -> None:
        self.search.setFocus(Qt.ShortcutFocusReason)
        self.search.selectAll()

    def _apply_filter(self) -> None:
        self.chat.set_filter(self.search.text())

    # ------------------------------------------------------------ 분석 보기
    def set_analysis_view(self, on: bool) -> None:
        if not on:
            self.timeline.clear_markers(ENGINE_TRACK)
            return
        if not analysis_view_allowed(self.session_kind, self.phase):
            self.analysis_action.setChecked(False)
            return
        if not self._link_path:
            self._note(tr("엔진 결과(derived\\link-*.json)가 없습니다"))
            return
        p = self._link_path

        def job(cancelled):
            with open(p, "rb") as f:
                return engine_markers(load_json_strict(f.read(LINK_MAX_BYTES + 1), LINK_MAX_BYTES))
        self._run_job("engine", job, self._on_engine, False)

    def _on_engine(self, items, err: str) -> None:
        if items is None:
            self._note(tr("엔진 결과를 읽지 못했습니다: {0}").format(err[:200]))
            return
        if self.analysis_action.isChecked():
            self.timeline.set_markers(ENGINE_TRACK, [(t, ENGINE_MARK_KIND.get(g, "engine_weak")) for t, g in items])
            LOG.info("분석 보기: 엔진 반응 %d개", len(items))

    # ------------------------------------------------------------ 상태 · 띠
    def _note(self, line: str) -> None:
        if line and line not in self.notes:
            self.notes.append(line)
        self._refresh_notes()

    def _refresh_notes(self) -> None:
        lines = self.part_problems + self.notes
        self.banner.setText("\n".join("• " + n for n in lines))
        self.banner.setVisible(bool(lines))

    def status_text(self) -> str:
        return " · ".join(w.text() for w in (self.lbl_vod, self.lbl_file, self.lbl_cal, self.lbl_chats, self.lbl_session)
                          if w.text())

    def _update_status(self, full: bool = False) -> None:
        """상태 칩 갱신. 재생 위치가 바뀔 때(25Hz)는 위치 칩만 고치고, 보정·채팅 수·세션 칩은 full=True 일 때만
        (프로젝트·채팅·store 가 바뀔 때) 고친다. 문장이 그대로면 statusChanged 를 다시 보내지 않는다."""
        _set_text(self.lbl_vod, tr("VOD {0}").format(tc.fmt_display(self._vod_pos)))
        if self.media_path and self._file_pos is not None:
            state = tr("재생") if self._playing else tr("정지")
            _set_text(self.lbl_file, tr("파일 {0} ({1})").format(tc.fmt_display(max(0, self._file_pos)), state))
        else:
            _set_text(self.lbl_file, tr("파일 -- (영상 없음)"))
        if full:
            _set_text(self.lbl_cal, self.timemap.describe())
            style = "" if self.timemap.status == "verified" else "color: #ffb74d;"
            if style != self._cal_style:          # setStyleSheet 는 같은 문자열이어도 스타일을 다시 입힌다
                self._cal_style = style
                self.lbl_cal.setStyleSheet(style)
            _set_text(self.lbl_chats, tr("채팅 {0:,}건").format(self.chat_data.count))
            if self.session is not None:
                st = self.store
                gv = (st.session_meta or {}).get("guide_version") if st is not None else None
                ro = tr(" (읽기 전용)") if st is not None and st.read_only else ""
                _set_text(self.lbl_session, tr("{0} · {1} · 기준서 {2}{3}").format(
                    self.session.labeler_id, self.session.session_id, gv or "-", ro))
        text = self.status_text()
        if text != self._status_last:
            self._status_last = text
            self.statusChanged.emit(text)

    # ------------------------------------------------------------ 닫기
    def close_workbench(self) -> None:
        """워커 정리, 플레이어 닫기, 프로젝트 잠금 해제. 여러 번 불러도 된다."""
        with self._store_guard:
            if self._closed:
                return
            self._closed = True
        self._filter_timer.stop()
        for w in list(self._jobs.values()):
            self._orphan(w)
        self._jobs.clear()
        try:
            self.player.close_player()
        except Exception:  # noqa: BLE001
            LOG.exception("플레이어 닫기 실패")
        self._close_store()                  # 잠금을 넘겨받았으면 여기서 풀린다
        from .project_view import release_outcome
        release_outcome(self.outcome)

    def closeEvent(self, ev):  # noqa: N802
        self.close_workbench()
        super().closeEvent(ev)

