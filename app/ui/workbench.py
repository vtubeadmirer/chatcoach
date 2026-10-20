"""작업대: 플레이어 + 타임라인 + 오른쪽 칸(채팅 표 · 구간 폼 · 반응 편집기 · 표본 큐)
(docs/design/02-architecture.md 7-3·7-4·7-6·7-7·8장·9장, 08 D-09).

배치: 맨 위 모드 줄(둘러보기 · ① 구간 · ② 반응 · ③ 표본 + 단계 완료 표시), 왼쪽 위 플레이어(아래에 영상 연결 줄),
왼쪽 아래 타임라인, 오른쪽 칸(모드마다 바뀜), 맨 아래 상태 줄.

모드 (02 7-3, 안내서 원칙 5 '순서를 지킨다', 03 12-3, app.core.phase)
- 둘러보기: 오른쪽은 채팅 표. 세션이 없으면(보기 전용) 이 모드뿐이다.
- ① 구간 (W1): 오른쪽은 구간 폼(app.ui.forms.SegmentForm). `B` = 지금 위치에 경계. 저장은 폼의 Ctrl+Enter.
  폼이 고친(저장 전) 구간표가 곧바로 타임라인에 그려진다(모델 층). Ctrl+Z 는 폼의 모델 층 실행 취소다(02 7-6).
- ② 반응 (W2): 오른쪽은 채팅 표. `M` 이나 채팅 더블클릭으로 반응 편집기(ReactionEditor)가 열린다. 저장·취소 전에는
  다른 라벨·모드로 넘어가지 않는다(편집 중 상태). Ctrl+Z 는 이번 실행에서 저장한 마지막 반응 라벨을 철회한다.
- ③ 표본 (W3): 오른쪽은 표본 큐(QueueView). 뽑기는 프로그램이 한다(app.core.sampling). '반응 있음'(M) 은 같은 편집기를 연다.
- 구간 라벨이 없거나, 저스트채팅 구간이 없거나, 단계 완료 표시(phase.mark)가 없으면 ②·③ 은 꺼지고 까닭이 보인다.

블라인드 (안내서 원칙 3, 03 12장)
- 라벨링 세션(primary·relabel_*)에서는 엔진 결과·다른 세션 라벨·AI 등급 제안을 어디에도 보이지 않는다.
  '분석 보기'(엔진 마커)는 visibility.can_show(engine_results) 가 허락하는 분석 세션에서만 메뉴에 나온다.
- 폼은 store 가 읽은 자기 세션 기록만 받는다(store 는 다른 세션 파일을 디스크에서 읽지 않는다).

시각 축 (03 4장, app.core.timecode)
- 플레이어는 영상 파일 시각(file_ms), 채팅 패널·타임라인·라벨·엔진 결과는 VOD 시각(vod_ms = 채팅 시각).
- 변환은 project.json 의 활성 보정 판 하나로 한다: vod = video_to_chat_ms(file), file = chat_to_video_ms(vod).
- 영상이 없으면 '가상 재생 위치'(VOD)만 움직인다. 채팅을 보며 탐색할 수 있게.

데이터 흐름
- 채팅 읽기·영상 확인·파형·엔진 결과·표본 뽑기는 워커(QThread). 라벨 저장은 UI 스레드의 store 하나(02 8장).
- project.json 에 쓰는 것은 '영상 파일 연결' 하나뿐이고, store 가 열려 있으면 store 를 통해서만 쓴다.
- samples/ 매니페스트 파일은 queue_view.write_manifest_file 로 UI 스레드에서 한 번만 쓴다(불변, 03 15-3).

부품(MpvPlayer·ChatPanel·Timeline)은 02 3-3 공통 계약만 믿는다. import 나 생성이 실패하면 같은 메서드·시그널을
가진 빈 자리 위젯으로 바꾸고 띠에 알린다(앱이 죽지 않게). 시험은 parts= 로 가짜 부품을 넣을 수 있다.

로그에는 채팅 본문·닉네임을 남기지 않는다(10장). 건수·시간·예외 종류만.
"""
from __future__ import annotations

import copy
import glob
import hashlib
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from .. import jsonl, media
from .. import project as project_mod
from ..core import phase as ph
from ..core import rules
from ..core import timecode as tc
from ..core.validate import load_json_strict
from ..i18n import tr

LOG = logging.getLogger("chatcoach")

# --------------------------------------------------------------------------- 시작값
DENSITY_BUCKET_MS = 10_000        # 채팅 밀도 버킷 (과제 명세: 10초)
SEEK_LEAD_MS = 3_000              # 채팅 더블클릭 → 그 채팅 VOD 시각 − 3초로 탐색
STEP_MS = 5_000                   # J/L (02 7-6)
SMALL_STEP_MS = 1_000             # Shift+←/→
BIG_STEP_MS = 10_000              # Ctrl+←/→
VFR_STEP_MS = 100                 # ←/→ 1프레임 대신 0.1초: 가변 프레임률이거나 영상이 없을 때 (02 7-6)
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
# 타임라인에 반영하는 라벨 종류(현재 유효 판). retract 는 대상을 지운다.
HEAD_KINDS = ("segment", "utterance", "reaction", "continuation", "video_check")
HEARD_MIN_COVERAGE = rules.HEARD_MIN_COVERAGE   # 들었다고 인정하는 덮은 비율(core.rules, review_player 와 같은 값)
DEFAULT_MIN_VIEWS = 2             # guide-config recheck.min_views_direct_likely
DEFAULT_BUFFER_MS = 10_000        # guide-config segment.boundary_buffer_sec
DEFAULT_SAMPLE_N = 100            # guide-config sample.per_segment
DEFAULT_CENSUS_MAX = 100          # guide-config sample.census_max

_ORPHANS: set = set()             # 화면이 닫힐 때 아직 도는 워커를 붙잡아 둔다(8장: setParent(None) + deleteLater)


# --------------------------------------------------------------------------- 순수 도우미 (Qt 없음)
def _order_key(r: dict):
    t = r.get("t_ms")
    if isinstance(t, bool) or not isinstance(t, int):
        return (1, 0, str(r.get("chat_id") or ""))
    return (0, t, str(r.get("chat_id") or ""))


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


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


def fps_from_probe(probe: Mapping | None) -> tuple[float | None, bool]:
    """mediaRef.probe 또는 analysis.probe 결과 → (고정 프레임률 fps, 가변 프레임률 여부).

    가변 프레임률(r_frame_rate ≠ avg_frame_rate, analysis.probe 의 vfr)이면 (None, True) — 프레임 단위 착시를 막는다(02 7-3).
    """
    if not isinstance(probe, Mapping):
        return None, False
    if probe.get("vfr"):
        return None, True
    f = probe.get("fps")
    if isinstance(f, (int, float)) and not isinstance(f, bool) and f > 0:
        return float(f), False
    for k in ("avg_frame_rate", "r_frame_rate"):
        v = probe.get(k)
        if isinstance(v, str) and "/" in v:
            try:
                n, d = v.split("/", 1)
                n, d = float(n), float(d)
                if n > 0 and d > 0:
                    return n / d, False
            except ValueError:
                continue
    return None, False


def guide_values(project_data: Mapping | None, guide_version: str | None) -> dict:
    """project.json guides[] 에서 그 기준서 판의 시작값(thresholds = guide-config values). 없으면 활성 판, 그것도 없으면 {}."""
    d = project_data or {}
    want = guide_version or d.get("active_guide_version")
    for g in d.get("guides") or []:
        if isinstance(g, Mapping) and g.get("guide_version") == want and isinstance(g.get("thresholds"), Mapping):
            return dict(g["thresholds"])
    return {}


def _sec_ms(values: Mapping, block: str, key: str, default_ms: int) -> int:
    v = (values.get(block) or {}).get(key) if isinstance(values.get(block), Mapping) else None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
        return int(round(v * 1000))
    return default_ms


def reaction_marks(reactions, video_checks, *, min_views: int = DEFAULT_MIN_VIEWS,
                   min_coverage: float = HEARD_MIN_COVERAGE) -> list[tuple[int, str]]:
    """반응 라벨 → 타임라인 'labels' 트랙 마커 [(VOD ms, 종류)] (02 7-3 '재확인 필요는 속이 빈 표시').

    - 반응 미확인 → 'unconfirmed' (표본 채팅 시각, first_visible_ms).
    - 직접·높음 중 7-2 재확인('들었다' 고 인정된 회차 min_views 이상)이 모자라면 → 'recheck' (속이 빈 표시).
    - 그 밖의 등급 → 'reaction'. 시각은 reaction_time_ms, 없으면 first_visible_ms.
    '들었다' = video_check 의 audio_heard 이고 coverage_ratio ≥ min_coverage (review_player 와 같은 규칙).
    """
    heard: dict[str, int] = {}
    for v in video_checks or ():
        b = v.get("body") if isinstance(v, Mapping) else None
        if not isinstance(b, Mapping) or b.get("target_kind", "reaction") != "reaction":
            continue
        if rules.check_heard(b, min_coverage):
            t = b.get("target_entity_id")
            if isinstance(t, str):
                heard[t] = heard.get(t, 0) + 1
    out: list[tuple[int, str]] = []
    for r in reactions or ():
        b = r.get("body") if isinstance(r, Mapping) else None
        if not isinstance(b, Mapping):
            continue
        t = b.get("reaction_time_ms") if _is_int(b.get("reaction_time_ms")) else b.get("first_visible_ms")
        if not _is_int(t):
            continue
        if b.get("result") != "graded":
            out.append((t, "unconfirmed"))
        elif b.get("grade") in ("direct", "likely") and heard.get(r.get("entity_id"), 0) < min_views:
            out.append((t, "recheck"))
        else:
            out.append((t, "reaction"))
    out.sort()
    return out


def segment_buffers(segments, duration_ms: int, buffer_ms: int) -> list[tuple[int, int]]:
    """현재 유효 구간 레코드의 안쪽 경계마다 [b − 완충, b + 완충) (core.segments.record_buffers — 내보내기와 같은 규칙)."""
    from ..core.segments import record_buffers
    return record_buffers(segments, duration_ms, buffer_ms)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_messages(messages_path: str) -> tuple[list[dict], str]:
    """messages.jsonl → (줄 dict 목록, 파일 sha256). 표본 뽑기·재현 검증용(워커에서 부른다)."""
    with open(messages_path, "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()
    res = jsonl.read(messages_path)
    return list(res.records), sha


def prepare_sample(messages_path: str, seg: Mapping, segments, *, duration_ms: int, values: Mapping,
                   project_data: Mapping, meta: Mapping, drawn_by: str, app_version: str,
                   previous: Mapping | None = None, drawn_at: str | None = None) -> tuple[dict, dict[str, dict]]:
    """표본 매니페스트를 계산한다(파일에 쓰지 않음). (manifest, 표본 채팅 chat_id → 줄 dict).

    seg = 표본을 뽑을 구간의 현재 유효 레코드(entity_id·id·body). segments = 지금 구간표(경계 완충 계산용).
    시작값: sample.per_segment(n)·sample.census_max·segment.boundary_buffer_sec. 시드는 project.json 의 sample_seed.
    """
    from ..core import sampling as sp
    msgs, sha = read_messages(messages_path)
    b = seg["body"]
    buf = _sec_ms(values, "segment", "boundary_buffer_sec", DEFAULT_BUFFER_MS)
    buffers = segment_buffers(segments, duration_ms, buf)
    pop = sp.build_population(msgs, int(b["start_ms"]), int(b["end_ms"]), buffers)
    smp = values.get("sample") if isinstance(values.get("sample"), Mapping) else {}
    n = smp.get("per_segment") if _is_int(smp.get("per_segment")) else DEFAULT_SAMPLE_N
    cmax = smp.get("census_max") if _is_int(smp.get("census_max")) else DEFAULT_CENSUS_MAX
    man = sp.build_manifest(
        project_id=project_data["project_id"],
        source_segment={"session_id": seg["session_id"], "entity_id": seg["entity_id"], "version_id": seg["id"],
                        "start_ms": int(b["start_ms"]), "end_ms": int(b["end_ms"])},
        guide_version=meta["guide_version"], thresholds_sha256=meta["thresholds_sha256"],
        sample_seed=project_data["sample_seed"], draw_no=1, population=pop, messages_sha256=sha,
        n=int(n), census_max=int(cmax), drawn_at=drawn_at or _now_iso(), drawn_by=drawn_by,
        app_version=app_version, previous=previous)
    wanted = {it["chat_id"] for it in man["items"]}
    return man, {m["chat_id"]: m for m in msgs if m.get("chat_id") in wanted}


def sample_messages(messages_path: str, chat_ids) -> dict[str, dict]:
    """표본 채팅만 골라 읽는다(큐 화면이 시각·본문·시청자만 쓴다)."""
    wanted = set(chat_ids)
    msgs, _ = read_messages(messages_path)
    return {m["chat_id"]: m for m in msgs if m.get("chat_id") in wanted}


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
    def set_segments(self, items, buffer_ms=None): pass
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
    """02 7-3 의 작업대. 모드는 app.core.phase 의 BROWSE·SEGMENTS·REACTIONS·SAMPLES."""

    statusChanged = Signal(str)
    seekIssued = Signal(int, int)        # (file_ms, vod_ms) — 영상이 없으면 file_ms = -1
    modeChanged = Signal(str)
    labelsChanged = Signal()             # 저장·철회·단계 표시로 라벨 기록이 바뀜

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
        self._fps: float | None = None       # 고정 프레임률 (영상 probe)
        self._vfr = False                    # 가변 프레임률이면 ←/→ 가 0.1초
        self._jobs: dict[str, FuncWorker] = {}
        self._closed = False
        self._link_path: str | None = None
        self._status_last = ""
        self._cal_style: str | None = None
        # 타임라인에 그린 유효 라벨 (종류 → entity_id → 기록). 저장·되돌리기 때는 바뀐 대상만 고친다(_apply_saved)
        self._label_heads: dict[str, dict[str, dict]] = {k: {} for k in HEAD_KINDS}
        self._labels_clean = False
        self._marks = ph.PhaseMarks()
        # 모드와 편집 부품 (처음 쓸 때 만든다)
        self.mode = ph.BROWSE
        self.segment_form = None
        self.editor = None
        self.queue = None
        self.review = None
        self._editor_return = None           # 편집기를 닫으면 돌아갈 오른쪽 칸
        # 2·3단계 Ctrl+Z 장부: 이번 실행의 반응 저장 한 번 = 한 항목 {'eid': 반응 entity_id, 'items': [(entity_id, 저장 전 머리
        # 사본 또는 None(새 대상), 저장 뒤 머리 id)]}. 되돌리면 새 대상은 철회, 고친 대상은 앞 판 body 로 새 판(03 10-1 표).
        self._reaction_undo: list[dict] = []
        self._edit_snapshot: dict[str, dict] = {}
        self._sample_manifest: dict | None = None
        self._sample_msgs: dict[str, dict] = {}

        # 바탕을 늘 칠한다: 작업대를 바꿀 때(다른 프로젝트·사본 열기) 닫힌 플레이어의 네이티브 창 자리에 옛 영상 그림이
        # 남아 보이던 것(실제 창 스모크 화면)을 막는다
        self.setAutoFillBackground(True)
        self.player = self._make_part("player", parts)
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

        # 모드 줄 (02 7-3 그림 맨 위의 [①구간][②반응][③표본])
        mrow_top = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for m in ph.MODES:
            b = QPushButton(tr(ph.MODE_LABELS[m]))
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _c=False, m=m: self.set_mode(m))
            self.mode_group.addButton(b)
            self.mode_buttons[m] = b
            mrow_top.addWidget(b)
        self.btn_phase_done = QPushButton("")
        self.btn_phase_done.setFocusPolicy(Qt.NoFocus)
        self.btn_phase_done.clicked.connect(lambda _c=False: self.mark_phase_done())
        self.btn_phase_done.setVisible(False)
        mrow_top.addWidget(self.btn_phase_done)
        self.mode_reason = QLabel("")
        self.mode_reason.setObjectName("hint")
        self.mode_reason.setWordWrap(True)
        mrow_top.addWidget(self.mode_reason, 1)
        root.addLayout(mrow_top)

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
        self.page_chat = cbox
        self.right = QStackedWidget()
        self.right.addWidget(cbox)
        self.hsplit.addWidget(self.right)
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
        self._update_modes()
        self._update_status(full=True)

    # ------------------------------------------------------------ 부품
    def _make_part(self, kind: str, parts: dict | None):
        cls, why = (parts[kind], None) if parts and kind in parts else _default_part(kind)
        if cls is not None:
            try:
                # 플레이어는 처음부터 작업대를 부모로 만든다. 부모 없이 만들면 MpvPlayer 가 winId() 를 부를 때 최상위 창
                # 핸들이 생기고, 그 뒤 배치에 넣으며 부모가 바뀐다 — 두 번째 작업대(다른 프로젝트 열기)에서 영상이 검게
                # 나오고 옛 자리에 그림이 남던 실제 창 문제(10 5-3 의 8 '플레이어를 다른 부모로 옮기지 말 것')
                return cls(self) if kind == "player" else cls()
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

    # ------------------------------------------------------------ 단축키 (02 7-6 표 전부)
    def _install_shortcuts(self) -> None:
        """02 7-6 의 작업대 몫. 등급(Ctrl+1~5)·확신도(Alt+1~3)·Ctrl+T·Ctrl+Enter·Esc 는 오른쪽 칸의 폼이 잡는다
        (폼이 보일 때만 동작). 같은 키를 두 곳이 잡으면 Qt 가 애매한 단축키로 보고 둘 다 버리므로, Ctrl+Z 는
        ② 반응·③ 표본에서만 켜고(① 구간에서는 폼의 Ctrl+Z), 큐 화면의 M·R 은 끄고 여기서 넘겨준다."""
        self.shortcut_map: dict[str, QShortcut] = {}

        def sc(key: str, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WindowShortcut)      # 보이는 동안만 동작한다. 글자 칸은 ShortcutOverride 로 글자를 먼저 가져간다
            s.activated.connect(fn)
            self.shortcut_map[key] = s
            return s
        sc("Space", self.toggle_play)
        sc("K", self.toggle_play)
        sc("Left", lambda: self.frame_key(-1))
        sc("Right", lambda: self.frame_key(1))
        sc(",", lambda: self.frame_key(-1))           # 지난 묶음의 프레임 키도 남긴다(7-6 표 밖, 같은 동작)
        sc(".", lambda: self.frame_key(1))
        sc("Shift+Left", lambda: self.step(-SMALL_STEP_MS))
        sc("Shift+Right", lambda: self.step(SMALL_STEP_MS))
        sc("Ctrl+Left", lambda: self.step(-BIG_STEP_MS))
        sc("Ctrl+Right", lambda: self.step(BIG_STEP_MS))
        sc("J", lambda: self.step(-STEP_MS))
        sc("L", lambda: self.step(STEP_MS))
        sc("[", self.speed_down)
        sc("]", self.speed_up)
        sc("R", self.toggle_review_loop)
        sc("M", self.mark_reaction)
        sc("B", self.add_boundary_here)
        sc("Ctrl+Z", self.undo_shortcut)
        sc("Ctrl+=", self.zoom_in)
        sc("Ctrl++", self.zoom_in)
        sc("Ctrl+-", self.zoom_out)
        sc("Ctrl+0", self.zoom_reset)
        sc("Ctrl+F", self.focus_search)
        self.shortcuts = list(self.shortcut_map.values())
        self._apply_mode_shortcuts()

    def _apply_mode_shortcuts(self) -> None:
        self.shortcut_map["Ctrl+Z"].setEnabled(self.mode in (ph.REACTIONS, ph.SAMPLES))

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
        if cur:
            self._fps, self._vfr = fps_from_probe(cur.get("probe"))
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
        self._update_modes()

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
            # 잠금은 ui 층이 주입한다(입출력 층은 QtCore 를 직접 쓰지 않는다, 08 D-09 8). 따로 주지 않았으면 QLockFile
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
                    st.close("user")
                    return None
                self.store = st
            return st
        self._run_job("store", job, self._on_store_opened, sync)

    def _on_store_opened(self, st, err: str) -> None:
        if st is None:
            self.store_problem = tr("라벨 기록을 열지 못했습니다: {0}").format(err[:200])
            self._note(self.store_problem)
            self._update_modes()
            return
        if st.read_only:
            self._note(tr("라벨 기록 읽기 전용: {0}").format(st.read_only_reason or ""))
            if getattr(st, "lock_help", None):
                self._note(st.lock_help)
        for w in st.warnings[:5]:
            self._note(str(w))
        self.refresh_labels()
        self._update_status(full=True)

    def refresh_labels(self) -> None:
        """store 의 현재 유효 라벨을 모두 다시 계산해 타임라인에 그린다: segment → 활동 구간, reaction → 'labels' 마커.

        사본 없이(effective(copy_records=False)) 읽는다. 받은 dict 는 store 내부 객체라 고치지 않는다.
        단계 완료 표시(phase.mark)도 여기서 다시 읽는다.
        """
        if self.store is None:
            return
        try:
            eff = self.store.effective(copy_records=False)
        except Exception as e:  # noqa: BLE001
            self._note(tr("라벨 계산 실패: {0}").format(type(e).__name__))
            return
        heads: dict[str, dict[str, dict]] = {k: {} for k in HEAD_KINDS}
        for kind, d in heads.items():
            for r in eff.items(kind):
                d[r["entity_id"]] = r
        self._label_heads = heads
        self._marks = ph.PhaseMarks(r for r in eff.other_records if r.get("kind") == "phase.mark")
        # 충돌·무효 줄이 있으면 다음 저장도 전체 계산으로 한다(증분 갱신은 깨끗한 기록에서만 resolve 결과와 같다)
        self._labels_clean = not eff.conflicts() and not eff.invalid_ids
        self._push_labels(tuple(heads))
        bad = [p for p in eff.problems if p.code in ("cycle", "fork", "duplicate_id", "invalid_ref")]
        if bad:
            self._note(tr("라벨 기록에 확인할 문제가 {0}건 있습니다 (충돌·순환 등)").format(len(bad)))
        self._update_modes()

    def segment_heads(self) -> list[dict]:
        """자기 세션의 현재 유효 segment 레코드(시작 시각 순). 받은 dict 는 store 내부 객체라 고치지 않는다."""
        return sorted(self._label_heads["segment"].values(),
                      key=lambda r: ((r.get("body") or {}).get("start_ms") or 0, r.get("entity_id")))

    def _push_labels(self, kinds) -> None:
        if "segment" in kinds:
            self._push_segments_track()
        if "reaction" in kinds or "video_check" in kinds:
            self.timeline.set_markers(LABEL_TRACK, reaction_marks(
                self._label_heads["reaction"].values(), self._label_heads["video_check"].values(),
                min_views=self._min_views()))

    def _push_segments_track(self) -> None:
        """활동 구간 트랙: ① 구간 모드면 폼의 모델(저장 전 상태 포함), 아니면 store 의 현재 유효 판."""
        if self.mode == ph.SEGMENTS and self.segment_form is not None and self.segment_form.model is not None:
            items, buf = self.segment_form.segments_for_timeline(), self.segment_form.buffer_ms
        else:
            items = list(self._label_heads["segment"].values())
            buf = _sec_ms(self.guide_values(), "segment", "boundary_buffer_sec", DEFAULT_BUFFER_MS)
        try:
            self.timeline.set_segments(items, buffer_ms=buf)
        except TypeError:                     # buffer_ms 를 받지 않는 부품(계약 최소 모양)
            self.timeline.set_segments(items)

    def _apply_saved(self, rec) -> None:
        """방금 저장한 줄 하나만 반영한다(02 8장: UI 스레드 한 번 16ms). 기록 전체를 다시 resolve 하지 않는다.

        store 는 supersedes 가 그 대상의 유일한 머리일 때만 쓰므로, 지난 전체 계산이 깨끗했다면(충돌·무효 없음)
        새 줄이 곧 그 대상의 현재 판이다(retract 면 철회). 깨끗하지 않았거나 모르는 모양이면 전체 계산으로 돌아간다.
        """
        if not self._labels_clean or not isinstance(rec, dict) or not isinstance(rec.get("entity_id"), str):
            self.refresh_labels()
            self.labelsChanged.emit()
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
            if "segment" in changed:
                self._update_modes()
        self.labelsChanged.emit()

    def save_label(self, draft: dict) -> dict:
        """라벨 한 건 저장(store.append_label) 뒤 타임라인에서 그 대상만 고친다. 세션·store 가 없으면 RuntimeError.

        폼들의 save 콜백이다. 철회 초안(kind='retract', SegmentForm 의 retract 콜백이 없을 때 모양)은 store.retract 로 보낸다.
        """
        if self.store is None:
            raise RuntimeError("라벨 기록이 열려 있지 않습니다 (세션 없음)")
        if isinstance(draft, dict) and draft.get("kind") == "retract":
            b = draft.get("body") or {}
            return self.retract_label(draft.get("entity_id"), b.get("reason", "undo"), b.get("merged_into"))
        rec = self.store.append_label(draft)
        self._apply_saved(rec)
        return rec

    def retract_label(self, entity_id: str, reason: str, merged_into: str | None = None) -> dict:
        """대상 철회(store.retract). SegmentForm 의 retract 콜백."""
        if self.store is None:
            raise RuntimeError("라벨 기록이 열려 있지 않습니다 (세션 없음)")
        rec = self.store.retract(entity_id, reason, merged_into=merged_into)
        self._apply_saved(rec)
        return rec

    def undo_last(self) -> dict | None:
        """마지막 라벨 줄 되돌리기(store.retract_last, 02 3-3 의 기록 층 Ctrl+Z)."""
        if self.store is None:
            return None
        rec = self.store.retract_last()
        self._apply_saved(rec)
        return rec

    def write_ops(self, kind: str, body: dict) -> dict:
        """운영 기록(warning·view.exposed·phase.mark) 한 줄. 반응 편집기의 ops 콜백."""
        if self.store is None:
            raise RuntimeError("라벨 기록이 열려 있지 않습니다 (세션 없음)")
        rec = self.store.append_ops(kind, body)
        if kind == "phase.mark":
            self._marks.add(rec)
            self._update_modes()
            self.labelsChanged.emit()
        return rec

    def _exposed(self, what: str, chat_id: str | None, entity_id: str | None) -> None:
        """숨겨 둔 자료를 열었다(view.exposed, 03 6-4). 편집기의 자동 인식 문장 보기."""
        body = {"what": what, "chat_id": chat_id, "entity_id": entity_id}
        try:
            self.write_ops("view.exposed", body)
        except Exception as e:  # noqa: BLE001 — 기록 실패는 띠에 알리고 다시 던진다(편집기가 보여 주지 않게, 03 6-4)
            self._note(tr("열람 기록을 남기지 못했습니다: {0}").format(type(e).__name__))
            raise

    def _close_store(self, reason: str = "user") -> None:
        with self._store_guard:
            st, self.store = self.store, None
        if st is not None:
            try:
                st.close(reason)
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
        if self.editor is not None:
            self.editor.load_context(chats=self._editor_chats())
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
        self._apply_frame_info()
        self._start_waveform(path)

    def _apply_frame_info(self) -> None:
        if hasattr(self.timeline, "set_fps"):
            self.timeline.set_fps(None if self._vfr else self._fps, self.timemap.to_vod(0))
        if self.review is not None:
            self.review.set_frame_info(self._fps, self._vfr)

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
        self._fps, self._vfr = fps_from_probe(ref.get("probe"))
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
            out = {"fps": info.get("fps"), "vfr": bool(info.get("vfr")), "vals": None, "dur": 0}
            if not info.get("has_audio"):
                return out
            dur = int(info.get("duration_ms") or 0)
            out["vals"] = analysis.compute_waveform(path, waveform_points(dur), cancel=cancelled)
            out["dur"] = dur
            return out
        self._run_job("waveform", job, self._on_waveform, False)

    def _on_waveform(self, res, err: str) -> None:
        if res is None:
            if err:
                LOG.info("파형 실패")
                self._note(tr("파형을 만들지 못했습니다: {0}").format(err[:200]))
            return
        if isinstance(res, tuple):           # (vals, dur) — 예전 모양
            res = {"vals": res[0], "dur": res[1], "fps": None, "vfr": self._vfr}
        if self._fps is None and not self._vfr:
            fps, vfr = fps_from_probe({"fps": res.get("fps"), "vfr": res.get("vfr")})
            if fps or vfr:
                self._fps, self._vfr = fps, vfr
                self._apply_frame_info()
        if res.get("vals"):
            aligned, span = align_waveform(res["vals"], res["dur"], self.timemap.to_vod(0))
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
        if self.mode == ph.SEGMENTS and self.segment_form is not None:
            self.segment_form.set_position(self._vod_pos)
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
        if self.mode == ph.REACTIONS and not self.is_editing():
            self.open_reaction_editor(chat_id, int(vod_ms))      # 02 7-3: 채팅 더블클릭 → 편집 상태

    def step(self, delta_ms: int) -> None:
        self.seek_vod(self._vod_pos + int(delta_ms))

    def frame_key(self, n: int) -> None:
        """←/→ (02 7-6): 1프레임. 가변 프레임률이거나 영상이 없으면 0.1초."""
        if self.media_path and not self._vfr:
            self.frame_step(n)
        else:
            self.step(int(n) * VFR_STEP_MS)

    def toggle_play(self) -> None:
        if self.media_path:
            self.player.toggle()

    def frame_step(self, n: int) -> None:
        if self.media_path:
            self.player.frame_step(int(n))

    def focus_search(self) -> None:
        if self.right.currentWidget() is not self.page_chat and not self.is_editing():
            self.right.setCurrentWidget(self.page_chat)
        self.search.setFocus(Qt.ShortcutFocusReason)
        self.search.selectAll()

    def _apply_filter(self) -> None:
        self.chat.set_filter(self.search.text())

    def zoom_in(self) -> None:
        getattr(self.timeline, "zoom_in", lambda: None)()

    def zoom_out(self) -> None:
        getattr(self.timeline, "zoom_out", lambda: None)()

    def zoom_reset(self) -> None:
        getattr(self.timeline, "zoom_reset", lambda: None)()

    # ------------------------------------------------------------ 확인 재생기 (7-2 범위·속도)
    def _ensure_review(self):
        if self.review is None:
            from .review_player import ReviewPlayer
            self.review = ReviewPlayer(self.player, self, to_file=self.vod_to_file, to_vod=self.file_to_vod)
            self.review.set_thresholds(self.guide_values())
            self.review.set_frame_info(self._fps, self._vfr)
            self.review.loopChanged.connect(self._on_review_loop)
        return self.review

    def _on_review_loop(self, on: bool) -> None:
        w = self.review.window() if (on and self.review is not None) else None
        if hasattr(self.timeline, "set_loop_range"):
            self.timeline.set_loop_range(*(w if w else (None, None)))

    def speed_up(self) -> None:
        self._ensure_review().speed_up()
        self._say(tr("속도 x{0:g}").format(self.review.speed()))

    def speed_down(self) -> None:
        self._ensure_review().speed_down()
        self._say(tr("속도 x{0:g}").format(self.review.speed()))

    def toggle_review_loop(self) -> None:
        """R (02 7-6): 7-2 범위 반복. 편집기가 열려 있으면 그 범위, ③ 표본이면 표본 범위."""
        if self.is_editing():
            self.editor.listen_again() if not self.review.loop_on() else self.review.stop_loop()
        elif self.mode == ph.SAMPLES and self.queue is not None:
            self.queue.toggle_loop()
        else:
            self._say(tr("7-2 범위 반복(R)은 반응 편집기나 ③ 표본에서 씁니다"))

    # ------------------------------------------------------------ 모드 (02 7-3, 안내서 원칙 5)
    def guide_values(self) -> dict:
        """이 세션 기준서 판의 시작값(guide-config values). 세션이 없으면 활성 판."""
        meta = self.store.session_meta if self.store is not None else None
        gv = (meta or {}).get("guide_version")
        return guide_values(self.project.data if self.project else None, gv)

    def _min_views(self) -> int:
        rc = self.guide_values().get("recheck")
        v = rc.get("min_views_direct_likely") if isinstance(rc, Mapping) else None
        return v if _is_int(v) and v > 0 else DEFAULT_MIN_VIEWS

    def _write_block_reason(self) -> str | None:
        """라벨을 만들 수 없는 까닭. None 이면 만들 수 있다."""
        if self.session is None:
            return tr("세션 없이 연 보기 전용입니다 (라벨 저장 꺼짐). 프로젝트를 다시 열어 세션을 고르세요")
        try:
            from ..core import visibility as vis
            if not vis.files_to_open(self.session_kind).can_write_label("segment"):
                return tr("이 세션 종류({0})는 구간·반응 라벨을 만들지 않습니다").format(self.session_kind)
        except Exception:  # noqa: BLE001
            return tr("세션 정책을 확인하지 못했습니다")
        if self.store is None:
            return self.store_problem or tr("라벨 기록을 여는 중입니다")
        if self.store.read_only:
            return tr("라벨 기록이 읽기 전용입니다: {0}").format(self.store.read_only_reason or "")
        return None

    def gates(self) -> dict[str, ph.Gate]:
        why = self._write_block_reason()
        return ph.gates(self.segment_heads(), self._marks, can_write_labels=why is None, why_no_write=why)

    def _update_modes(self) -> None:
        g = self.gates()
        reasons = []
        for m, b in self.mode_buttons.items():
            gate = g.get(m, ph.Gate(False))
            b.setEnabled(gate.allowed or m == self.mode)
            b.setToolTip(gate.reason or "")
            b.setChecked(m == self.mode)
            if not gate.allowed and m in (ph.REACTIONS, ph.SAMPLES, ph.SEGMENTS) and gate.reason:
                reasons.append((m, gate.reason))
        # 같은 까닭은 한 번만 (세션 없음 등)
        seen, lines = set(), []
        for m, r in reasons:
            if r in seen:
                continue
            seen.add(r)
            lines.append(f"{tr(ph.MODE_LABELS[m])}: {r}")
        _set_text(self.mode_reason, " · ".join(lines[:2]))
        self._update_phase_button()

    def _update_phase_button(self) -> None:
        b = self.btn_phase_done
        if self.mode == ph.SEGMENTS:
            done = self._marks.done("segments")
            b.setText(tr("구간 다시 열기") if done else tr("구간 완료 표시"))
            b.setVisible(True)
        elif self.mode == ph.REACTIONS:
            done = self._marks.done("reactions")
            b.setText(tr("반응 단계 다시 열기") if done else tr("반응 완료 표시"))
            b.setVisible(True)
        else:
            b.setVisible(False)

    def is_editing(self) -> bool:
        """반응 편집 중(저장·취소 전)인가."""
        return self.editor is not None and self.editor.is_open()

    def has_unsaved(self) -> bool:
        """저장하지 않은 것이 있는가(구간 폼의 모델 변경 또는 열린 반응 편집기)."""
        f = self.segment_form
        return bool((f is not None and f.model is not None and f.has_unsaved) or self.is_editing())

    def ask_unsaved(self) -> str:
        """저장하지 않은 구간 변경이 있을 때 묻는다: 'save' · 'discard' · 'cancel'. 시험에서는 바꿔 끼운다."""
        box = QMessageBox(self)
        box.setWindowTitle(tr("저장하지 않은 구간"))
        box.setText(tr("구간표에 저장하지 않은 변경이 있습니다. 저장할까요?"))
        bs = box.addButton(tr("저장"), QMessageBox.AcceptRole)
        bd = box.addButton(tr("버리기"), QMessageBox.DestructiveRole)
        box.addButton(tr("취소"), QMessageBox.RejectRole)
        box.exec()
        c = box.clickedButton()
        return "save" if c is bs else "discard" if c is bd else "cancel"

    def resolve_unsaved(self, *, allow_cancel: bool = True) -> bool:
        """저장하지 않은 구간 변경을 처리한다. 계속해도 되면 True. 반응 편집 중이면 False(먼저 저장·취소)."""
        if self.is_editing():
            if not allow_cancel:
                self.editor.cancel()
                return True
            self._say(tr("반응 편집 중입니다 — 저장(Ctrl+Enter)하거나 취소(Esc)한 뒤에 바꿀 수 있습니다"))
            return False
        f = self.segment_form
        if f is None or f.model is None or not f.has_unsaved:
            return True
        ans = self.ask_unsaved()
        if ans == "cancel" and not allow_cancel:
            ans = "save"
        if ans == "save":
            if f.save() is None:
                if not allow_cancel:
                    LOG.warning("닫을 때 구간 저장이 막혀 저장하지 않은 변경을 버립니다")
                    return True
                return False
            return True
        if ans == "discard":
            f.discard_changes()
            return True
        return False

    def set_mode(self, mode: str) -> bool:
        """모드를 바꾼다. 막히면(단계 순서·편집 중·저장 안 한 변경 취소) False 이고 까닭을 띠·모드 줄에 보인다."""
        if mode not in ph.MODES:
            raise ValueError(f"모드가 아닙니다: {mode!r}")
        if mode == self.mode:
            self._sync_mode_buttons()
            return True
        if self.is_editing():
            self._say(tr("반응 편집 중입니다 — 저장(Ctrl+Enter)하거나 취소(Esc)한 뒤에 바꿀 수 있습니다"))
            self._sync_mode_buttons()
            return False
        gate = self.gates().get(mode, ph.Gate(False))
        if not gate.allowed:
            self._say(gate.reason or tr("이 모드는 지금 열 수 없습니다"))
            self._sync_mode_buttons()
            return False
        if self.mode == ph.SEGMENTS and not self.resolve_unsaved():
            self._sync_mode_buttons()
            return False
        self.mode = mode
        if mode == ph.SEGMENTS:
            self._enter_segments()
        elif mode == ph.REACTIONS:
            self._ensure_review()
            self._ensure_editor()
            self.right.setCurrentWidget(self.page_chat)
        elif mode == ph.SAMPLES:
            self._ensure_review()
            self._ensure_editor()
            self._enter_samples()
        else:
            self.right.setCurrentWidget(self.page_chat)
        self._push_segments_track()
        self._apply_mode_shortcuts()
        self._update_modes()
        LOG.info("작업대 모드: %s", mode)
        self.modeChanged.emit(mode)
        return True

    def _sync_mode_buttons(self) -> None:
        for m, b in self.mode_buttons.items():
            b.setChecked(m == self.mode)

    # ---- ① 구간
    def _ensure_segment_form(self):
        if self.segment_form is None:
            from .forms import SegmentForm
            f = SegmentForm(self.save_label, self._form_cancel, retract=self.retract_label)
            f.segmentsChanged.connect(self._on_form_segments)
            f.recordsSaved.connect(lambda _recs: self._on_form_segments())
            f.message.connect(self._say)
            self.segment_form = f
            self.right.addWidget(f)
        return self.segment_form

    def _enter_segments(self) -> None:
        f = self._ensure_segment_form()
        seg_cfg = self.guide_values().get("segment")
        f.load_context(duration_ms=max(1, self._vod_duration), records=self.segment_heads(),
                       session_id=self.session.session_id if self.session else None,
                       fps=None if self._vfr else self._fps, frame_origin_ms=self.timemap.to_vod(0),
                       thresholds=seg_cfg if isinstance(seg_cfg, Mapping) else None,
                       position_ms=self._vod_pos, read_only=bool(self.store is None or self.store.read_only))
        self.right.setCurrentWidget(f)

    def _on_form_segments(self) -> None:
        if self.mode == ph.SEGMENTS:
            self._push_segments_track()

    def _form_cancel(self) -> None:
        self.setFocus(Qt.OtherFocusReason)

    def add_boundary_here(self) -> bool:
        """B (02 7-6): ① 구간에서 지금 재생 위치에 경계를 더한다(폼의 모델 층, 실행 취소 200단계)."""
        if self.mode != ph.SEGMENTS or self.segment_form is None:
            self._say(tr("구간 경계 추가(B)는 ① 구간에서 씁니다"))
            return False
        return self.segment_form.add_boundary(self._vod_pos)

    # ---- 단계 완료 표시 (phase.mark)
    def mark_phase_done(self, done: bool | None = None) -> dict | None:
        """① 구간 · ② 반응 모드에서 그 단계의 완료 표시(phase.mark scope=all)를 쓴다. done 을 주지 않으면 지금 상태를 뒤집는다."""
        if self.mode == ph.SEGMENTS:
            p = "segments"
            if self.segment_form is not None and self.segment_form.has_unsaved:
                self._say(tr("저장하지 않은 구간 변경이 있습니다. 먼저 저장하세요 (Ctrl+Enter)"))
                return None
            if not self.segment_heads():
                self._say(tr("저장된 구간이 없습니다"))
                return None
        elif self.mode == ph.REACTIONS:
            p = "reactions"
            if self.is_editing():
                self._say(tr("반응 편집 중입니다 — 저장하거나 취소하세요"))
                return None
        else:
            return None
        if done is None:
            done = not self._marks.done(p)
        try:
            rec = self.write_ops("phase.mark", ph.mark_body(p, done))
        except Exception as e:  # noqa: BLE001
            self._say(tr("단계 표시를 저장하지 못했습니다: {0}").format(e))
            return None
        self._say(tr("{0} 단계를 {1}로 표시했습니다").format(
            tr("① 구간") if p == "segments" else tr("② 반응"), tr("완료") if done else tr("다시 열기")))
        return rec

    # ---- ② 반응 (반응 편집기)
    def _editor_chats(self):
        from .chat_panel import ChatData
        cd = getattr(self.chat, "chat_data", None)
        if callable(cd):
            try:
                d = cd()
                if isinstance(d, ChatData):
                    return d
            except Exception:  # noqa: BLE001
                pass
        if isinstance(self.chat_data.panel_data, ChatData):
            return self.chat_data.panel_data
        return ChatData(self.chat_data.records, self.chat_data.viewer_no)

    def _ensure_editor(self):
        if self.editor is None:
            from .forms.reaction_editor import ReactionEditor
            ed = ReactionEditor(self.save_label, self._editor_cancelled, ops=self.write_ops, exposed=self._exposed,
                                retract=lambda eid, reason: self.retract_label(eid, reason))
            ed.setFocusPolicy(Qt.StrongFocus)
            ed.finished.connect(self._on_editor_finished)
            ed.partialSaved.connect(self._on_editor_partial)
            self.editor = ed
            self.right.addWidget(ed)
        return self.editor

    def _editor_context(self) -> None:
        ed = self._ensure_editor()
        ed.load_context(chats=self._editor_chats(), segments=self.segment_heads(), thresholds=self.guide_values(),
                        session_kind=self.session_kind, phase=self.mode if self.mode != ph.BROWSE else ph.REACTIONS,
                        position_fn=self.vod_position, review=self._ensure_review())

    def mark_reaction(self) -> None:
        """M (02 7-6): 반응 지점 찍기 — 편집기를 열기만 한다(저장하지 않음). ③ 표본에서는 '반응 있음'."""
        if self.mode == ph.SAMPLES and self.queue is not None:
            if not self.is_editing():
                self.queue.request_reaction_editor()
            return
        if self.mode != ph.REACTIONS:
            self._say(tr("반응 지점 찍기(M)는 ② 반응에서 씁니다"))
            return
        if self.is_editing():
            return
        sel = None
        try:
            sel = self.chat.selected_chat_id()
        except Exception:  # noqa: BLE001
            sel = None
        self.open_reaction_editor(sel, self._vod_pos)

    def open_reaction_editor(self, chat_id: str | None, reaction_vod_ms: int | None, *,
                             sample_ref: dict | None = None, existing: dict | None = None) -> bool:
        """반응 편집 상태로 들어간다(오른쪽 칸 = 편집기). 단계 순서가 막혀 있으면 False."""
        if self.mode not in (ph.REACTIONS, ph.SAMPLES):
            return False
        if self.is_editing():
            return False
        self._editor_context()
        ed = self.editor
        self._editor_return = self.right.currentWidget()
        self._edit_snapshot = self._undo_snapshot(existing.get("entity_id") if existing is not None else None)
        if existing is not None:
            utt = {}
            for c in (existing.get("body") or {}).get("candidates") or []:
                uid = c.get("utterance_id")
                if uid and uid in self._label_heads["utterance"]:
                    utt[uid] = self._label_heads["utterance"][uid]
            eid = existing.get("entity_id")
            cont = next((r for r in self._label_heads["continuation"].values()
                         if (r.get("body") or {}).get("reaction_entity_id") == eid), None)
            checks = [r for r in self._label_heads["video_check"].values()
                      if (r.get("body") or {}).get("target_entity_id") == eid]
            ed.open_existing(copy.deepcopy(existing), utterances=copy.deepcopy(utt),
                             continuation=copy.deepcopy(cont), video_checks=copy.deepcopy(checks))
        else:
            ed.open_for(chat_id, reaction_vod_ms, sample_ref=sample_ref)
        self.right.setCurrentWidget(ed)
        ed.setFocus(Qt.OtherFocusReason)
        return True

    def _on_editor_finished(self, result) -> None:
        rec = (result or {}).get("reaction") if isinstance(result, dict) else None
        if isinstance(result, dict):
            self._push_undo(rec, result.get("written") or [], self._edit_snapshot)
        self._edit_snapshot = {}
        self._leave_editor()
        if self.mode == ph.SAMPLES and self.queue is not None and isinstance(rec, dict):
            self.queue.mark_saved(rec)
        if isinstance(result, dict) and not result.get("complete", True):
            self._say(tr("저장했습니다 — 7-2 재확인이 남아 타임라인에 속이 빈 표시로 남습니다"))
        else:
            self._say(tr("반응 라벨을 저장했습니다"))

    def _on_editor_partial(self, info) -> None:
        """저장이 중간에 실패한 뒤 Esc: 이미 쓴 줄을 Ctrl+Z 장부에 넣고, 반응이 써졌으면 큐도 '판정함' 으로 맞춘다.
        (그러지 않으면 큐가 그 표본을 '대기' 로 두어 다시 판정할 때 같은 채팅에 반응 라벨이 둘 생긴다.)"""
        if not isinstance(info, dict):
            return
        rec = info.get("reaction")
        self._push_undo(rec, info.get("written") or [], self._edit_snapshot)
        self._edit_snapshot = {}
        if self.mode == ph.SAMPLES and self.queue is not None and isinstance(rec, dict):
            self.queue.mark_saved(rec)
        self._say(tr("저장이 중간에 실패했습니다 — 이미 쓴 줄 {0}개는 남아 있습니다 (Ctrl+Z 로 되돌릴 수 있음)")
                  .format(len(info.get("written") or [])))

    def _editor_cancelled(self) -> None:
        self._edit_snapshot = {}
        self._leave_editor()

    # ---- 2·3단계 Ctrl+Z 장부
    def _undo_snapshot(self, reaction_eid: str | None) -> dict[str, dict]:
        """반응 저장 전 머리 사본: 그 반응 · 후보 발화 · 그 반응을 가리키는 이어짐. 새 반응이면 빈 dict."""
        snap: dict[str, dict] = {}
        if not isinstance(reaction_eid, str):
            return snap
        r = self._label_heads["reaction"].get(reaction_eid)
        if r is None:
            return snap
        snap[reaction_eid] = copy.deepcopy(r)
        for c in (r.get("body") or {}).get("candidates") or []:
            uid = c.get("utterance_id") if isinstance(c, Mapping) else None
            if uid in self._label_heads["utterance"]:
                snap[uid] = copy.deepcopy(self._label_heads["utterance"][uid])
        for ceid, c in self._label_heads["continuation"].items():
            if (c.get("body") or {}).get("reaction_entity_id") == reaction_eid:
                snap[ceid] = copy.deepcopy(c)
        return snap

    def _push_undo(self, reaction_rec, written, snapshot: Mapping[str, dict]) -> None:
        """반응 저장 한 번에 쓴 줄 → Ctrl+Z 장부 한 항목. 반응 대상이 바뀌지 않았으면(같은 내용) 넣지 않는다."""
        eid = reaction_rec.get("entity_id") if isinstance(reaction_rec, Mapping) else None
        last: dict[str, str] = {}
        order: list[str] = []
        for r in written or ():
            if not isinstance(r, Mapping) or not isinstance(r.get("entity_id"), str) or not isinstance(r.get("id"), str):
                continue
            if r["entity_id"] not in last:
                order.append(r["entity_id"])
            last[r["entity_id"]] = r["id"]
            if eid is None and r.get("kind") == "reaction":
                eid = r["entity_id"]
        items = [(e, snapshot.get(e), last[e]) for e in order
                 if not (snapshot.get(e) is not None and snapshot[e].get("id") == last[e])]
        if eid is None or not items or all(e != eid for e, _b, _a in items):
            return
        self._reaction_undo.append({"eid": eid, "items": items})

    def _orphan_continuations(self, rec) -> list[dict]:
        """반응 rec 이 직접·높음이 아니면 그 반응을 가리키는 현재 유효 이어짐을 철회한다(안내서 4-3, 03 12-3)."""
        if not isinstance(rec, Mapping) or rec.get("kind") != "reaction":
            return []
        b = rec.get("body") or {}
        if b.get("result") == "graded" and b.get("grade") in ("direct", "likely"):
            return []
        out = []
        for ceid, c in list(self._label_heads["continuation"].items()):
            if (c.get("body") or {}).get("reaction_entity_id") == rec.get("entity_id"):
                out.append(self.retract_label(ceid, "other"))
        return out

    def _leave_editor(self) -> None:
        back = self._editor_return
        self._editor_return = None
        if self.mode == ph.SAMPLES and self.queue is not None:
            back = self.page_samples
        if back is None or back is self.editor:
            back = self.page_chat
        self.right.setCurrentWidget(back)
        if hasattr(self.timeline, "set_loop_range"):
            self.timeline.set_loop_range(None, None)
        (self.queue if back is getattr(self, "page_samples", None) and self.queue is not None else self).setFocus(
            Qt.OtherFocusReason)

    def undo_shortcut(self) -> None:
        """Ctrl+Z (02 7-6). ① 구간: 폼의 모델 층 실행 취소. ②·③: 이 세션의 직전 반응 라벨 철회(철회 줄 1건)."""
        if self.mode == ph.SEGMENTS and self.segment_form is not None:
            self.segment_form.undo()
            return
        if self.mode in (ph.REACTIONS, ph.SAMPLES):
            self.undo_reaction()

    def undo_reaction(self) -> dict | None:
        """이번 실행의 마지막 반응 저장 한 번을 되돌린다(03 10-1 표: 새로 만든 것은 retract(undo), 고친 것은 앞 판 body 로 새 판).

        그 저장에서 쓴 줄(발화·반응·이어짐·이어짐 철회·영상 확인)을 거꾸로 되돌리므로, 철회된 반응을 가리키는 이어짐이 남지
        않고 지난 실행에 만든 라벨이 통째로 지워지지도 않는다. 그 뒤 다른 곳에서 고친 대상은 건드리지 않는다.
        돌려주는 값은 반응 대상에 쓴 줄(철회 또는 되돌린 판).
        """
        if self.store is None:
            return None
        if self.is_editing():
            self._say(tr("편집 중에는 되돌리기 대신 취소(Esc)를 쓰세요"))
            return None
        while self._reaction_undo:
            entry = self._reaction_undo.pop()
            eid = entry["eid"]
            after = next((a for e, _b, a in entry["items"] if e == eid), None)
            head = self.store.head(eid)
            if head is None or head.get("id") != after:
                continue                                  # 그 뒤 다른 곳에서 고쳤거나 이미 되돌림
            out = None
            skipped = 0
            try:
                for e, before, a in reversed(entry["items"]):
                    cur = self.store.head(e)
                    if cur is None or cur.get("id") != a:
                        skipped += 1
                        continue
                    if before is None:
                        rec = self.retract_label(e, "undo") if cur.get("kind") != "retract" else None
                    else:
                        draft = {"kind": before["kind"], "entity_id": e, "supersedes": cur["id"],
                                 "body": copy.deepcopy(before.get("body"))}
                        if "x" in before:
                            draft["x"] = copy.deepcopy(before["x"])
                        rec = self.save_label(draft)
                    if e == eid:
                        out = rec
            except Exception as ex:  # noqa: BLE001
                self._say(tr("되돌리지 못했습니다: {0}").format(ex))
                return None
            msg = tr("직전 반응 저장을 되돌렸습니다 ({0}줄)").format(len(entry["items"]) - skipped)
            if skipped:
                msg += " · " + tr("그 뒤 고친 {0}건은 그대로 둠").format(skipped)
            self._say(msg)
            if self.queue is not None:
                self.queue.set_reactions(list(self._label_heads["reaction"].values()))
            return out
        self._say(tr("이번 실행에서 저장한 반응 라벨이 없습니다"))
        return None

    # ---- ③ 표본 (큐 모드)
    def _ensure_queue(self):
        if self.queue is None:
            from .queue_view import QueueView
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            row = QHBoxLayout()
            row.addWidget(QLabel(tr("표본 구간")))
            self.sample_seg_box = QComboBox()
            self.sample_seg_box.currentIndexChanged.connect(lambda _i: self._on_sample_target())
            row.addWidget(self.sample_seg_box, 1)
            self.btn_draw = QPushButton(tr("표본 뽑기"))
            self.btn_draw.setToolTip(tr("프로그램이 sha256-tau-v1 로 뽑습니다. 다시 뽑기는 없습니다 (안내서 2장)"))
            self.btn_draw.clicked.connect(lambda _c=False: self.draw_sample())
            row.addWidget(self.btn_draw)
            pl.addLayout(row)
            q = QueueView(self._queue_save, self._queue_cancel, verify=self._verify_sample)
            q.set_play_keys(False)          # M·R 은 작업대가 잡아 넘긴다(애매한 단축키 방지)
            q.setFocusPolicy(Qt.StrongFocus)
            q.reactionEditorRequested.connect(self._on_queue_editor)
            q.statusChanged.connect(self._say)
            q.rangeRequested.connect(lambda a, b: hasattr(self.timeline, "set_loop_range")
                                     and self.timeline.set_loop_range(a, b))
            pl.addWidget(q, 1)
            self.queue = q
            self.page_samples = page
            self.right.addWidget(page)
        return self.queue

    def _enter_samples(self) -> None:
        self._ensure_queue()
        box = self.sample_seg_box
        box.blockSignals(True)
        box.clear()
        for s in ph.sample_targets(self.segment_heads(), self._marks):
            b = s["body"]
            box.addItem(f"{tc.fmt_display(b['start_ms'])} ~ {tc.fmt_display(b['end_ms'])}", s["entity_id"])
        # 지금 위치가 든 구간을 고른다
        idx = 0
        for i in range(box.count()):
            s = self._label_heads["segment"].get(box.itemData(i))
            if s and s["body"]["start_ms"] <= self._vod_pos < s["body"]["end_ms"]:
                idx = i
                break
        box.setCurrentIndex(idx if box.count() else -1)
        box.blockSignals(False)
        self.right.setCurrentWidget(self.page_samples)
        self._on_sample_target(sync=True)
        self.queue.setFocus(Qt.OtherFocusReason)

    def sample_target(self) -> dict | None:
        """지금 고른 표본 구간의 현재 유효 레코드."""
        if self.queue is None or self.sample_seg_box.currentIndex() < 0:
            return None
        return self._label_heads["segment"].get(self.sample_seg_box.currentData())

    def _manifests_for(self, entity_id: str) -> list[dict]:
        from .queue_view import list_manifests
        if self.project is None:
            return []
        out = [m for m in list_manifests(self.project.paths.samples_dir)
               if (m.get("source_segment") or {}).get("entity_id") == entity_id and m.get("draw_no") == 1]
        out.sort(key=lambda m: m.get("sample_rev", 0))
        return out

    def _on_sample_target(self, sync: bool = False) -> None:
        seg = self.sample_target()
        if seg is None:
            self.btn_draw.setEnabled(False)
            self._load_queue(None, {})
            return
        mans = self._manifests_for(seg["entity_id"])
        latest = mans[-1] if mans else None
        if latest is None:
            self.btn_draw.setText(tr("표본 뽑기"))
            self.btn_draw.setEnabled(True)
            self._load_queue(None, {})
            return
        stale = (latest.get("source_segment") or {}).get("version_id") != seg.get("id")
        self.btn_draw.setText(tr("표본 갱신 (구간이 바뀜)") if stale else tr("표본 있음"))
        self.btn_draw.setEnabled(stale)
        mpath = self.project.paths.messages_jsonl
        ids = [it["chat_id"] for it in latest.get("items", [])]
        self._run_job("sample_msgs", lambda cancelled: sample_messages(mpath, ids),
                      lambda msgs, err, m=latest: self._load_queue(m, msgs or {}, err), sync)

    def draw_sample(self, *, sync: bool = False) -> None:
        """고른 구간의 표본을 뽑는다(워커) → samples/<id>.json 을 한 번 쓴다 → 큐에 싣는다.

        구간이 바뀌어 지난 매니페스트의 판과 다르면 그 다음 판(sample_rev + 1, τ 는 내려가지 않음)을 만든다(06 2-6).
        """
        seg = self.sample_target()
        if seg is None or self.store is None or self.project is None:
            self._say(tr("표본을 뽑을 구간이 없습니다"))
            return
        try:
            from ..core import visibility as vis
            if not vis.files_to_open(self.session_kind, ph.SAMPLES).can_write("samples"):
                self._say(tr("이 세션은 표본을 뽑을 수 없습니다"))
                return
        except Exception:  # noqa: BLE001
            return
        if self.store.read_only:
            self._say(tr("읽기 전용이라 표본을 뽑을 수 없습니다"))
            return
        meta = self.store.session_meta or {}
        mans = self._manifests_for(seg["entity_id"])
        prev = mans[-1] if mans else None
        if prev is not None and (prev.get("source_segment") or {}).get("version_id") == seg.get("id"):
            self._say(tr("이 구간의 표본이 이미 있습니다 (다시 뽑지 않습니다)"))
            return
        if prev is not None and prev.get("guide_version") != meta.get("guide_version"):
            prev = None                         # 기준서 판이 바뀌면 새 계열 (06 2-6) — 이번 판은 draw 1 그대로 거부될 수 있다
        from .. import __version__
        args = dict(duration_ms=self._vod_duration, values=self.guide_values(), project_data=dict(self.project.data),
                    meta=meta, drawn_by=self.labeler_id or "L01", app_version=__version__, previous=prev)
        mpath = self.project.paths.messages_jsonl
        segs = [dict(s) for s in self.segment_heads()]
        seg_c = copy.deepcopy(seg)
        self.btn_draw.setEnabled(False)
        self._say(tr("표본을 뽑는 중…"))
        self._run_job("sample_draw", lambda cancelled: prepare_sample(mpath, seg_c, segs, **args),
                      self._on_sample_drawn, sync)

    def _on_sample_drawn(self, res, err: str) -> None:
        if res is None:
            self._say(tr("표본을 뽑지 못했습니다: {0}").format(err[:200]))
            self._on_sample_target(sync=True)
            return
        man, msgs = res
        from .queue_view import write_manifest_file
        try:
            write_manifest_file(self.project.paths.samples_dir, man)
        except FileExistsError:
            # 다른 PC·동기화로 같은 id 의 매니페스트가 이미 있다: 계산한 것을 싣지 않고 디스크의 판을 싣는다
            # (판정 라벨의 sample_ref 가 디스크의 다른 내용을 가리키지 않게).
            self._say(tr("같은 표본 매니페스트가 이미 있어 그 파일을 싣습니다"))
            self._on_sample_target(sync=True)
            return
        except OSError as e:
            self._say(tr("표본 매니페스트를 저장하지 못했습니다: {0}").format(type(e).__name__))
            return
        LOG.info("표본 뽑기: %s 적격 %d 표본 %d", man["sample_set_id"], man["eligible"]["size"], len(man["items"]))
        self.btn_draw.setText(tr("표본 있음"))
        self.btn_draw.setEnabled(False)
        self._load_queue(man, msgs)

    def _load_queue(self, manifest, msgs, err: str = "") -> None:
        if err:
            self._say(tr("표본 채팅을 읽지 못했습니다: {0}").format(err[:200]))
        self._sample_manifest = manifest
        self._sample_msgs = msgs or {}
        pre = _sec_ms(self.guide_values(), "recheck", "pre_roll_sec", 10_000)
        post = _sec_ms(self.guide_values(), "recheck", "post_roll_sec", 30_000)
        self.queue.load_context(manifest=manifest, messages=self._sample_msgs, session_kind=self.session_kind,
                                phase=ph.SAMPLES, session_id=self.session.session_id if self.session else None,
                                reactions=list(self._label_heads["reaction"].values()),
                                viewer_no=self.chat_data.viewer_no or None,
                                player=self.player if self.media_path else None,
                                to_file=self.vod_to_file, to_vod=self.file_to_vod, pre_roll_ms=pre, post_roll_ms=post,
                                autoplay=bool(self.media_path))

    def _queue_save(self, draft: dict) -> dict:
        snap = self._undo_snapshot(draft.get("entity_id") if isinstance(draft, Mapping) else None)
        rec = self.save_label(draft)
        written = [rec]
        try:
            written += self._orphan_continuations(rec)     # 판정을 낮춰 고치면 남은 이어짐 철회
        except Exception as e:  # noqa: BLE001
            self._say(tr("이어짐을 철회하지 못했습니다: {0}").format(e))
        self._push_undo(rec, written, snap)
        return rec

    def _queue_cancel(self) -> None:
        self.setFocus(Qt.OtherFocusReason)

    def _on_queue_editor(self, ctx: dict) -> None:
        existing = None
        if ctx.get("entity_id"):
            existing = self._label_heads["reaction"].get(ctx["entity_id"])
        self.open_reaction_editor(ctx.get("chat_id"), ctx.get("t_ms"), sample_ref=ctx.get("sample_ref"),
                                  existing=existing)

    def _verify_sample(self, manifest: dict):
        """재현 검증(03 15-5): 같은 입력(채팅 파일·뽑을 때의 구간표 완충)으로 다시 계산해 견준다. UI 스레드에서 돈다.

        뽑을 때의 완충은 매니페스트의 source_segment 경계(start_ms·end_ms)만으로 다시 만든다. 모집단은 그 구간 안 채팅이고,
        구간끼리는 겹치지 않으므로 다른 구간의 경계가 만든 완충이 이 범위에 닿는 부분은 늘 이 구간 양 끝 경계의 완충 안에
        든다(segment_buffers 는 모든 시작·끝을 경계로 본다). 그래서 지금 구간표가 바뀌었어도(stale 판) 결과가 같다.
        """
        from ..core import sampling as sp
        msgs, sha = read_messages(self.project.paths.messages_jsonl)
        buf = _sec_ms(self.guide_values(), "segment", "boundary_buffer_sec", DEFAULT_BUFFER_MS)
        src = manifest.get("source_segment") or {}
        buffers = segment_buffers([{"start_ms": src.get("start_ms"), "end_ms": src.get("end_ms")}],
                                  self._vod_duration, buf)
        prev = None
        rev = manifest.get("sample_rev", 1)
        if rev and rev > 1:
            prev = next((m for m in self._manifests_for(manifest["source_segment"]["entity_id"])
                         if m.get("sample_rev") == rev - 1), None)
        return sp.verify_manifest(manifest, msgs, buffers, messages_sha256=sha, previous=prev)

    # ------------------------------------------------------------ 내보내기
    def export_labels(self, out_path: str, *, overwrite: bool = False):
        """이 세션 라벨을 zip 하나로 내보낸다(app.export, 블라인드: viewer = 이 세션). ExportResult."""
        from .. import __version__
        from .. import export as export_mod
        if self.project is None or self.session is None:
            raise RuntimeError("세션이 없어 내보낼 라벨이 없습니다")
        if self.has_unsaved() and not self.is_editing():
            self._say(tr("저장하지 않은 구간 변경은 내보내지 않습니다"))
        return export_mod.export_labels(self.project.root, out_path,
                                        export_mod.ExportOptions(overwrite=overwrite),
                                        viewer=self.session, created_by=self.labeler_id, app_version=__version__)

    def choose_and_export(self) -> None:
        if self.project is None or self.session is None:
            return
        start = os.path.join(os.path.expanduser("~"), f"labels-{self.session.session_id}.zip")
        p, _ = QFileDialog.getSaveFileName(self, tr("라벨 내보내기"), start, tr("zip (*.zip)"))
        if not p:
            return
        try:
            res = self.export_labels(os.path.normpath(p), overwrite=os.path.exists(p))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("내보내기 실패"), str(e))
            return
        self._say(tr("내보냈습니다: 행 {0}개").format(len(res.rows)))

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
        if self.analysis_action.isChecked() and analysis_view_allowed(self.session_kind, self.phase):
            self.timeline.set_markers(ENGINE_TRACK, [(t, ENGINE_MARK_KIND.get(g, "engine_weak")) for t, g in items])
            LOG.info("분석 보기: 엔진 반응 %d개", len(items))

    # ------------------------------------------------------------ 상태 · 띠
    def _note(self, line: str) -> None:
        if line and line not in self.notes:
            self.notes.append(line)
        self._refresh_notes()

    def _say(self, text: str) -> None:
        """잠깐 보이는 상태 문장(모드 줄 오른쪽). 띠(notes)에는 쌓지 않는다."""
        if text:
            _set_text(self.mode_reason, text)
            self.statusChanged.emit(text)

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
    def close_workbench(self, reason: str = "user") -> None:
        """워커 정리, 플레이어 닫기, 라벨 기록 닫기(session.close 줄의 reason), 프로젝트 잠금 해제. 여러 번 불러도 된다.

        reason: 'user'(프로젝트 닫기) · 'app_exit'(앱 종료) · 'switch_project'(다른 프로젝트 열기) · 'error'.
        저장하지 않은 구간 변경은 여기서 묻지 않는다(창이 닫기 전에 resolve_unsaved 로 묻는다).
        """
        with self._store_guard:
            if self._closed:
                return
            self._closed = True
        self._filter_timer.stop()
        for w in list(self._jobs.values()):
            self._orphan(w)
        self._jobs.clear()
        if self.review is not None:
            try:
                self.review.detach()
            except Exception:  # noqa: BLE001
                LOG.exception("확인 재생기 떼기 실패")
        try:
            self.player.close_player()
        except Exception:  # noqa: BLE001
            LOG.exception("플레이어 닫기 실패")
        self._close_store(reason)            # 잠금을 넘겨받았으면 여기서 풀린다
        from .project_view import release_outcome
        release_outcome(self.outcome)

    def closeEvent(self, ev):  # noqa: N802
        self.close_workbench()
        super().closeEvent(ev)
