"""시청자 훈련 — 채팅 써 보기 화면 (08 D-01 '시청자 훈련', D-05, D-16 클릭 한 번, D-19 5, D-20 2·4·5, 21 6-1~6-3).

열면 바로: **소리 켠** 다시보기(시청자 자리) · 흐르는 채팅(가상 채팅이 있으면 '가져옴'·'생성' 표시와 함께 섞음) ·
타임라인 · 아래 **쓰기 칸 하나**. 고르는 창·점수 고르기·태그 칸은 없다(D-19 5 — 입력 칸은 쓰기 칸 하나뿐).

| 동작 | 결과 | 클릭 |
|---|---|---|
| 쓰기 칸에 채팅을 쓰고 Enter | 그 순간의 VOD 시각으로 보냄. 채팅 패널에 '연습' 표시로 흐름. 재생은 멈추지 않음 | 0 |
| 보낸 직후 | 쓰기 칸 바로 아래에 세 축(주제 적합성·대화 확장성·반복 여부) 칸마다 3단계(좋음·보통·아쉬움)와 까닭 한 줄. 합계 점수 없음 | 0 |
| 구간 끝 (또는 [요약]·Ctrl+Enter) | 쓴 수, 축마다 단계 분포, '다른 시청자라면' 예시 몇 줄(retrieved, 가림 이름, ' · 가져옴' 표시) | 0 (단추면 1) |
| [닫기] | closeRequested — 메인 창이 close_view() 뒤 작업대로 | 1 |
| [▶ 재생] | 멈춤·재생 (쓰기 칸에 초점이 있어 Space 는 글자로 들어간다) | 1 |

채점(D-20 3): 기본은 순수 모듈 app.core.viewer_score —
  score_chat(text, sent_ms, ScoreContext, history) -> ViewerScore(축마다 level·code·nums). ScoreContext 는 구간마다 한 번
  ScoreContext.build(topic_blocks, transcript, chats) 로 만든다: topic_blocks·transcript(·settings·topic_vectors·synonyms)는
  start(context=…) 의 같은 이름 칸(엔진이 미리 만든 주제 흐름·VOD 로 보정한 시각별 전사 낱말·창별 벡터를 app.viewer_flow
  .build_context 가 읽어 넘긴다), chats 는 이 화면에
  흐르는 실제·가상 줄(연습 줄은 빼고 — 내 글은 history 로). 까닭 한 줄은 viewer_score.reason_line(코드 + 숫자).
  score_fn(text, context) -> dict 로 바꿔 넣을 수도 있다(시험·다른 채점기). 그때 context = build_context(vod_ms):
  {"vod_ms", "range_ms", "window_ms", "recent_chat": [{"t_ms", "text", "virtual"}](앞 window_ms 안 화면에 나온 실제·가상,
  연습 줄 뺌), "my_previous": [앞서 쓴 내 글]} + start(context=…) + context_fn(vod_ms). 돌려받는 dict 는 normalize_score 가
  세 축 {level, code, reason} 으로 고른다. 채점기가 없거나 실패하면 단계 없이 기록하고 까닭 한 줄을 보인다.
기록(D-20 4): log(app.viewer_store.ViewerLog)를 주면 start·write·end 를 부른다(쓰기 실패면 보내지 않고 쓰기 칸 글을 그대로
  둔다). 쓴 글은 **그 기록 파일(viewer\\, 보호 구역)에만** 간다. 화면 신호(viewerEvent·writeScored·summaryShown)·events·
  로그·상태 줄에는 본문이 없다 — 단계·까닭 코드·VOD 시각·숫자만. 시청자 글은 시나리오·LLM 입력에 넣지 않는다(20 6장).
메뉴 켜기(D-20 5): 시범 프로젝트(pilot-*)는 시범 세션이 모두 잠기기 전에 끈다. viewer_menu_state() 는 폴더 이름만 보는
  이중 잠금이고 start() 가 한 번 더 부른다. 첫 판은 원래 반응·사람 라벨을 보이지 않으므로 노출로 세지 않는다(21 6-3 —
  기록이 training\\ 이 아니라 viewer\\ 에 있어 app.core.exposure 가 보지 않는다).
가상 채팅(20 6장): start(scenario=…) 를 주면 스트리머 훈련(app.ui.trainer_view)과 같은 섞기(core.scenario.merge_for_display →
  core.training.split_mixed → 구간 자르기·같은 본문 빼기)를 쓴다. 시나리오 찾기·원 채팅 찾기는 app.scenario_mix
  (load_viewer_scenario 가 묶어 준다 — 시범 폴더는 이름만 보고 열지 않는다). 섞든 안 섞든 실제 채팅은 훈련 구간
  [시작, 끝) 안만 싣는다(연습 줄을 넣을 때마다 표를 다시 만들어도 가볍게, retrieved 원 채팅이 같은 표에 보이지 않게).

부품: 작업대와 같은 MpvPlayer·ChatPanel·Timeline 을 import 해 쓴다(02 3-3). 시험은 parts= 로 가짜 부품을 넣는다.
  채팅 표의 검색 칸(QLineEdit)은 이 화면에서 떼어 낸다(부모 없음, 보이지 않음) — 이 화면 안의 입력 칸은 쓰기 칸 하나다.
  플레이어는 처음부터 이 화면 안의 분할기를 부모로 만든다(10 5-3 의 8 — trainer_view 와 같은 까닭).
시각: 플레이어는 영상 파일 시각, 채팅·타임라인·기록은 VOD 시각. 변환은 timemap(to_file·to_vod).
"""
from __future__ import annotations

import logging
import re
from array import array
from bisect import bisect_left
from typing import Callable, Iterable, Mapping, Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSplitter,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..i18n import tr
from . import trainer_view as _tv

LOG = logging.getLogger(__name__)

PILOT_PREFIX = "pilot-"
AXES = ("topic", "expand", "repeat")                     # app.viewer_store.AXES 와 같다
LEVELS = ("good", "ok", "weak")
AXIS_NAMES = {"topic": "주제 적합성", "expand": "대화 확장성", "repeat": "반복 여부"}
LEVEL_NAMES = {"good": "좋음", "ok": "보통", "weak": "아쉬움"}
LEVEL_COLORS = {"good": "#81c784", "ok": "#c8c8d0", "weak": "#e0b252"}
NO_LEVEL_COLOR = "#7d848c"

PRACTICE_ID_PREFIX = "p:"                                # 연습 줄의 화면 id (실제 chat_id·가상 'v:' 와 섞이지 않는다)
PRACTICE_VIEWER_ID = "practice-self"                     # 시청자 번호표에 넣지 않는다 → '시청자' 칸은 '-'
PRACTICE_NICK = "나"
PRACTICE_MARK = "연습"
PRACTICE_TIP = "내가 쓴 연습 채팅 — 앱 안에서만 보이고 어디에도 보내지 않습니다"
VIRTUAL_TAG = _tv.VIRTUAL_TAG                            # {"retrieved": "가져옴", "generated": "생성"}
VIRTUAL_MARK = _tv.VIRTUAL_MARK                          # {"retrieved": "가", "generated": "생"} — 닉네임 칸 앞 한 글자

MAX_TEXT_CHARS = 200                                     # app.viewer_store.MAX_TEXT_CHARS 와 같다
CONTEXT_WINDOW_MS = 120_000                              # 채점 맥락: 보낸 시각 앞 120초 (21 6-2)
EXAMPLES_MAX = 3                                         # '다른 시청자라면' 예시 줄 수
RANK_AXES = ("topic", "expand")                          # 예시 고르는 순서에 쓰는 축
END_TOL_MS = 200                                         # 구간 끝 이만큼 앞이면 끝난 것으로 본다
SCORER_NAME = "viewer_score"
_CODE_RE = re.compile(r"\A[a-z][a-z0-9_.]{0,39}\Z")     # viewer_score 의 'topic.flow_hit' 꼴 (viewer_store 와 같다)
_VER_RE = re.compile(r"\A[0-9A-Za-z.+-]{1,20}\Z")

REASON_PILOT = "시범 세션이 모두 잠기기 전에는 시범 프로젝트로 시청자 훈련을 하지 않습니다"
REASON_NO_MEDIA = "영상이 연결되지 않은 프로젝트에서는 시청자 훈련을 할 수 없습니다"
REASON_NO_RANGE = "훈련할 구간이 없습니다"
REASON_NO_SCENARIO = "가상 채팅이 아직 없습니다"
WRITE_PLACEHOLDER = "채팅을 쓰고 Enter — 재생은 멈추지 않습니다 (앱 안에서만 보이고 어디에도 보내지 않습니다)"

# 채점기가 까닭 글 없이 코드만 줄 때의 한 줄 (모르는 코드는 단계 이름만 보인다)
REASON_TEXT = {
    "scorer_missing": "채점기(app.core.viewer_score)를 쓸 수 없습니다",
    "scorer_failed": "채점하지 못했습니다",
}

# 채점기 결과의 다른 이름들 (첫 판 채점기와 이름이 조금 달라도 받는다)
_AXIS_ALIASES = {
    "topic": ("topic", "topic_fit", "topicality", "relevance", "fit"),
    "expand": ("expand", "expansion", "extend", "extensibility", "conversation", "engage", "engagement"),
    "repeat": ("repeat", "repetition", "repetitive", "novelty", "dup", "duplicate"),
}
_LEVEL_ALIASES = {
    "good": "good", "좋음": "good", "high": "good", "great": "good",
    "ok": "ok", "보통": "ok", "mid": "ok", "medium": "ok", "fair": "ok", "normal": "ok",
    "weak": "weak", "아쉬움": "weak", "low": "weak", "bad": "weak", "poor": "weak",
}
_LEVEL_INTS = {2: "good", 1: "ok", 0: "weak"}


# ---------------------------------------------------------------------------- 메뉴 켜기 (순수)
def is_pilot_project(project_name: str | None) -> bool:
    """시범 프로젝트 폴더 이름(pilot-*)인가. 경로를 주면 마지막 이름으로 본다. 폴더는 열지 않는다."""
    return _tv.is_pilot_project(project_name)


def viewer_menu_state(project_name: str | None, *, pilot_locked: bool = False,
                      has_media: bool = True) -> tuple[bool, str]:
    """'훈련 → 시청자 훈련' 메뉴를 켤까. (켜짐, 꺼졌으면 까닭 한 줄).

    - 시범 프로젝트(pilot-*)이고 시범 세션이 모두 잠기지 않았으면 끈다(D-20 5). 이 검사가 먼저다.
    - 영상이 연결되지 않았으면 끈다(소리 켠 다시보기가 화면의 바탕이라).
    정답(원래 반응·사람 라벨)은 쓰지 않으므로 기준이 있는지는 보지 않는다(21 6-3)."""
    if is_pilot_project(project_name) and not pilot_locked:
        return False, tr(REASON_PILOT)
    if not has_media:
        return False, tr(REASON_NO_MEDIA)
    return True, ""


# ---------------------------------------------------------------------------- 채점 결과 고르기 (순수)
def _level_of(v) -> str | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return _LEVEL_INTS.get(v)
    if isinstance(v, str):
        return _LEVEL_ALIASES.get(v.strip().casefold())
    return None


def _code_of(ax: Mapping) -> str:
    for k in ("code", "reason_code"):
        c = ax.get(k)
        if isinstance(c, str) and _CODE_RE.match(c):
            return c
    cs = ax.get("codes")
    if isinstance(cs, (list, tuple)):
        for c in cs:
            if isinstance(c, str) and _CODE_RE.match(c):
                return c
    return "other"


def _reason_of(ax: Mapping) -> str:
    for k in ("reason", "why", "message", "text", "note"):
        r = ax.get(k)
        if isinstance(r, str) and r.strip():
            return " ".join(r.split())[:120]
    return ""


def normalize_score(raw) -> dict | None:
    """채점기 결과 → {축: {"level", "code", "reason"}} (세 축 다 있을 때만). 아니면 None.

    받는 모양: {"topic": {...}, "expand": {...}, "repeat": {...}} 또는 {"axes": {...}}. 축 이름은 _AXIS_ALIASES,
    단계는 good·ok·weak(좋음·보통·아쉬움·high·low 등, 정수 2·1·0)도 받는다. 축 값이 단계 글자 하나여도 된다.
    reason(까닭 한 줄)은 화면에만 쓴다 — 기록에는 code 만 간다(app.viewer_store)."""
    if not isinstance(raw, Mapping):
        return None
    src = raw.get("axes") if isinstance(raw.get("axes"), Mapping) else raw
    out = {}
    for axis in AXES:
        val = None
        for k in _AXIS_ALIASES[axis]:
            if k in src:
                val = src[k]
                break
        if isinstance(val, Mapping):
            lv = None
            for k in ("level", "grade", "stage", "band", "value"):
                if k in val:
                    lv = _level_of(val[k])
                    if lv is not None:
                        break
            if lv is None:
                return None
            out[axis] = {"level": lv, "code": _code_of(val), "reason": _reason_of(val)}
        else:
            lv = _level_of(val)
            if lv is None:
                return None
            out[axis] = {"level": lv, "code": "other", "reason": ""}
    return out


def scorer_tag(raw) -> str | None:
    """채점기 결과의 판 → 'viewer_score/<판>' (기록 scorer 칸). 모르면 None."""
    if not isinstance(raw, Mapping):
        return None
    for k in ("scorer_version", "version", "v"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if isinstance(v, str) and _VER_RE.match(v):
            return f"{SCORER_NAME}/{v}"
    return None


def _builtin_tag() -> str | None:
    """기본 채점기(app.core.viewer_score)의 판 → 'viewer_score/<판>'. 판 상수가 없으면 None."""
    try:
        from ..core import viewer_score as vsc
    except ImportError:
        return None
    v = getattr(vsc, "VERSION", None)
    return f"{SCORER_NAME}/{v}" if isinstance(v, str) and _VER_RE.match(v) else None


def reason_line(ax: Mapping | None) -> str:
    """축 하나의 화면 한 줄 '좋음 — 까닭'. 단계가 없으면 '—'."""
    if not isinstance(ax, Mapping) or ax.get("level") not in LEVELS:
        return "—"
    why = ax.get("reason") or tr(REASON_TEXT.get(ax.get("code") or "", ""))
    name = tr(LEVEL_NAMES[ax["level"]])
    return f"{name} — {why}" if why else name


def level_counts(scores: Iterable[Mapping | None]) -> dict:
    """{축: {good, ok, weak}} — 채점된 쓰기만 센다."""
    out = {a: {lv: 0 for lv in LEVELS} for a in AXES}
    for sc in scores:
        if not isinstance(sc, Mapping):
            continue
        for a in AXES:
            ax = sc.get(a)
            lv = ax.get("level") if isinstance(ax, Mapping) else None
            if lv in LEVELS:
                out[a][lv] += 1
    return out


def from_viewer_score(res, reason_fn: Callable | None = None) -> dict | None:
    """app.core.viewer_score.ViewerScore(축마다 AxisResult: level·code·nums) → {축: {"level", "code", "reason"}}.
    reason 은 reason_fn(AxisResult)(채점기의 reason_line — 코드 + 숫자로 만든 한 줄, 쓴 글은 안 들어간다)."""
    out = {}
    for axis in AXES:
        a = getattr(res, axis, None)
        lv = getattr(a, "level", None)
        if lv not in LEVELS:
            return None
        code = getattr(a, "code", None)
        why = ""
        if reason_fn is not None:
            try:
                why = str(reason_fn(a) or "")
            except Exception:  # noqa: BLE001 — 까닭 한 줄이 없어도 단계는 보인다
                why = ""
        if why == code:
            why = ""                       # 채점기가 문구를 모르는 코드는 코드를 그대로 돌려준다 — 화면엔 단계만
        out[axis] = {"level": lv, "code": code if isinstance(code, str) and _CODE_RE.match(code) else "other",
                     "reason": " ".join(why.split())[:120]}
    return out


def _clean_text(text) -> str:
    try:
        from ..viewer_store import clean_text
        return clean_text(text)
    except ImportError:
        return " ".join(str(text or "").split())


# ---------------------------------------------------------------------------- 시나리오 묶기 (app.scenario_mix)
def load_viewer_scenario(project_root: str, project_id: str | None, range_ms: tuple[int, int] | None):
    """이 구간의 가장 새 시나리오와 섞기에 필요한 것 → (실제 채팅 줄 목록, scenario dict) 또는 (None, None).

    F 와 같은 길(app.scenario_mix): find_scenario(시범이면 scenarios\\ 를 열지 않음) → 가상 줄이 있으면 load_messages ·
    make_resolver(다른 프로젝트의 pilot-* 폴더는 열지 않음). 이 함수도 시범 폴더는 이름만 보고 아무것도 열지 않는다.
    scenario dict = {"records", "resolve", "scenario_id", "scenario_sha256"} (start(scenario=…) 에 그대로)."""
    if is_pilot_project(project_root):
        return None, None
    from .. import scenario_mix
    scn, _counts = scenario_mix.find_scenario(project_root, range_ms)
    if scn is None or scenario_mix.virtual_count(scn) <= 0:
        return None, None
    try:
        real = scenario_mix.load_messages(project_root)
    except OSError as e:
        LOG.warning("시청자 훈련 가상 채팅: 채팅 읽기 실패 %s", type(e).__name__)
        return None, None
    if not real:
        return None, None
    scenario = {"records": scn.records, "scenario_id": getattr(scn, "scenario_id", None),
                "scenario_sha256": getattr(scn, "sha256", None),
                "resolve": scenario_mix.make_resolver(project_root, project_id, real)}
    return real, scenario


def scenario_range(scenario) -> tuple[int, int] | None:
    """시나리오 머리줄의 구간 (vod_from_ms, vod_to_ms). 모르면 None."""
    records, _ = _tv._scenario_parts(scenario)
    if not records:
        return None
    h = records[0] if isinstance(records[0], Mapping) else None
    rng = (h or {}).get("range") if (h or {}).get("kind") == "scenario.header" else None
    if not isinstance(rng, Mapping):
        return None
    a, b = rng.get("vod_from_ms"), rng.get("vod_to_ms")
    if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and not isinstance(b, bool) and b > a >= 0:
        return a, b
    return None


# ---------------------------------------------------------------------------- 채팅 줄
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _rows_from_chatdata(cd, seg: tuple[int, int]) -> tuple[list[dict], dict]:
    """작업대의 ChatData(병렬 배열) → 구간 [시작, 끝) 안의 줄 dict 와 시청자 번호표. viewer_id 는 번호에서 만든
    표시용 키('n<번호>')다 — 원 viewer_id 를 되살리지 않는다."""
    from .chat_panel import KIND_CODES, ROLE_CODES, VNO_ANON
    kinds = {v: k for k, v in KIND_CODES.items()}
    roles = {v: k for k, v in ROLE_CODES.items()}
    a = bisect_left(cd.times, int(seg[0]))
    b = bisect_left(cd.times, int(seg[1]))
    rows: list[dict] = []
    vno: dict[str, int] = {}
    for i in range(a, b):
        n = int(cd.vnos[i])
        if n == VNO_ANON:
            vid = "anon"
        else:
            vid = f"n{n}"
            if n > 0:
                vno[vid] = n
        rows.append({"chat_id": cd.chat_ids[i], "t_ms": int(cd.times[i]), "text": cd.texts[i],
                     "nickname_masked": cd.nicks[i], "viewer_id": vid, "kind": kinds.get(cd.kinds[i], "other"),
                     "role": roles.get(cd.roles[i])})
    return rows, vno


def _clip_real(rows: Sequence[Mapping], seg: tuple[int, int]) -> list:
    return [r for r in rows if isinstance(r, Mapping) and _is_int(r.get("t_ms")) and seg[0] <= r["t_ms"] < seg[1]]


def _chatdata_insert(cd, row: Mapping, vno: Mapping[str, int]):
    """ChatData 에 줄 하나를 (t_ms, chat_id) 자리에 끼운 **새** ChatData (병렬 배열 복사 — C 속도, 10만 줄도 수 ms).
    작업대가 쓰는 같은 배열 모양을 그대로 따른다(mainwindow._fresh_chat_data 와 같은 길). 모양이 다르면 None."""
    from .chat_panel import KIND_CODES, KIND_OTHER, ROLE_CODES, ROLE_UNKNOWN, VNO_UNKNOWN, ChatData, search_key
    try:
        t, cid = int(row["t_ms"]), str(row["chat_id"])
        times, ids = cd.times, cd.chat_ids
        i = bisect_left(times, t)
        while i < cd.n and times[i] == t and ids[i] < cid:
            i += 1
        new = object.__new__(ChatData)
        for k in ChatData.__slots__:
            v = getattr(cd, k)
            setattr(new, k, v[:] if isinstance(v, (list, bytearray, array)) else v)
        text = row.get("text") if isinstance(row.get("text"), str) else ""
        new.times.insert(i, t)
        new.chat_ids.insert(i, cid)
        new.texts.insert(i, text)
        new.folded.insert(i, search_key(text))
        new.nicks.insert(i, str(row.get("nickname_masked") or ""))
        new.vnos.insert(i, int(vno.get(row.get("viewer_id"), VNO_UNKNOWN)))
        new.kinds.insert(i, KIND_CODES.get(row.get("kind"), KIND_OTHER))
        new.roles.insert(i, ROLE_CODES.get(row.get("role"), ROLE_UNKNOWN))
        new.dim.insert(i, 0)
        new.n = cd.n + 1
        new._id_index = None
        return new
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _default_part(kind: str):
    return _tv._default_part(kind)


class _WriteBox(QLineEdit):
    """쓰기 칸. Enter = 보내기, Ctrl+Enter = 요약 (글자 넣기는 보통 QLineEdit 그대로)."""

    sendRequested = Signal()
    summaryRequested = Signal()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if e.modifiers() & Qt.ControlModifier:
                self.summaryRequested.emit()
            else:
                self.sendRequested.emit()
            e.accept()
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------------------- 화면
class ViewerTrainView(QWidget):
    """시청자 훈련: 소리 켠 다시보기 + 흐르는 채팅 + 쓰기 칸 하나. start() 로 시작한다."""

    viewerEvent = Signal(dict)          # 기록 한 줄의 화면 쪽 모양(본문 없음). log 를 주면 이미 쓴 뒤에 낸다
    writeScored = Signal(dict)          # {"n", "vod_ms", "chars", "levels": {축: 단계|None}, "codes": {축: 코드}}
    summaryShown = Signal(dict)         # {"writes", "levels", "examples"} (숫자만)
    messageShown = Signal(str)
    closeRequested = Signal()

    def __init__(self, parent=None, *, parts: dict | None = None,
                 score_fn: Callable[[str, Mapping], object] | None = None,
                 mix_fn: Callable[..., object] | None = None):
        super().__init__(parent)
        self._score_fn = score_fn                 # None = app.core.viewer_score (score_chat + ScoreContext, _score_builtin)
        self._vsc_ctx = None                      # 그 채점기의 ScoreContext (구간마다 한 번 만든다)
        self._mix_fn = mix_fn or _tv._default_mix
        self.events: list[dict] = []
        self._rows: list[dict] = []               # 화면 줄(실제 + 가상), 구간 안만
        self._practice: list[dict] = []           # 연습 줄(화면 표에만 — 본문은 기록 파일 말고는 메모리에만)
        self._scores: list[dict | None] = []      # 쓰기마다의 normalize_score 결과
        self._mine: list[str] = []                # 앞서 쓴 내 글 (채점 맥락 my_previous, 메모리만)
        self._vlines: dict = {}                   # 가상 화면 id → core.training.VirtualLine
        self._vnames: dict[str, str] = {}         # 가상 화면 id → 가림 이름(표시 붙이기 전)
        self._vno: dict[str, int] = {}
        self._cd = None                           # 지금 채팅 표에 실린 ChatData (연습 줄을 끼울 때 이어 쓴다)
        self._scenario: dict | None = None
        self._segment: tuple[int, int] | None = None
        self._context: dict = {}
        self._context_fn: Callable[[int], Mapping] | None = None
        self._to_file: Callable[[int], int] = _tv._ident
        self._to_vod: Callable[[int], int] = _tv._ident
        self._vod_pos = 0
        self._seen_until = 0
        self._has_media = False
        self._autoplay = False
        self._state = "idle"                      # idle · training · summary · closed
        self._started = False
        self._ended = False
        self._muted = False
        self._log = None
        self._last = None                         # 마지막 쓰기의 writeScored 값
        self._summary: dict | None = None
        self._examples: list[dict] = []
        self.setFocusPolicy(Qt.StrongFocus)

        parts = parts or {}
        self.chat = (parts.get("chat") or _default_part("chat"))()
        self.timeline = (parts.get("timeline") or _default_part("timeline"))()

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 2)
        top = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setObjectName("panelTitle")
        top.addWidget(self.count_label)
        self.msg = QLabel("")
        self.msg.setObjectName("hint")
        self.msg.setWordWrap(True)
        self.msg.setTextFormat(Qt.PlainText)
        top.addWidget(self.msg, 1)
        self.btn_play = QPushButton(tr("▶ 재생"))
        self.btn_play.setToolTip(tr("재생 / 일시정지"))
        self.btn_play.setFocusPolicy(Qt.NoFocus)
        self.btn_play.clicked.connect(self._on_play_btn)
        top.addWidget(self.btn_play)
        self.btn_summary = QPushButton(tr("요약"))
        self.btn_summary.setToolTip(tr("여기까지 쓴 연습을 요약합니다 (Ctrl+Enter) — 구간 끝에서도 저절로 넘어갑니다"))
        self.btn_summary.setFocusPolicy(Qt.NoFocus)
        self.btn_summary.clicked.connect(self._on_summary_btn)
        top.addWidget(self.btn_summary)
        self.btn_close = QPushButton(tr("닫기"))
        self.btn_close.setToolTip(tr("시청자 훈련을 끝내고 작업대로 돌아갑니다 (기록은 남습니다)"))
        self.btn_close.setFocusPolicy(Qt.NoFocus)
        self.btn_close.clicked.connect(self._on_close_btn)
        top.addWidget(self.btn_close)
        root.addLayout(top)

        self.stack = QStackedWidget(self)
        self.blocked_label = QLabel("")
        self.blocked_label.setAlignment(Qt.AlignCenter)
        self.blocked_label.setWordWrap(True)
        self.stack.addWidget(self.blocked_label)

        body = QWidget(self)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        self.hsplit = QSplitter(Qt.Horizontal, body)
        self.vsplit = QSplitter(Qt.Vertical, self.hsplit)
        self.player = (parts.get("player") or _default_part("player"))(self.vsplit)
        self.vsplit.addWidget(self.player)
        self.vsplit.addWidget(self.timeline)
        self.vsplit.setStretchFactor(0, 3)
        self.vsplit.setStretchFactor(1, 1)
        self.hsplit.addWidget(self.vsplit)
        self.right = QStackedWidget()
        self.right.addWidget(self.chat)
        self.summary_page = self._build_summary_page()
        self.right.addWidget(self.summary_page)
        self.hsplit.addWidget(self.right)
        self.hsplit.setStretchFactor(0, 3)
        self.hsplit.setStretchFactor(1, 2)
        bl.addWidget(self.hsplit, 1)

        # 쓰기 칸 하나 + 바로 아래 세 축 칸 (D-19 5 — 이 화면의 입력 칸은 이것뿐)
        self.input = _WriteBox(body)
        self.input.setMaxLength(MAX_TEXT_CHARS)
        self.input.setPlaceholderText(tr(WRITE_PLACEHOLDER))
        self.input.setToolTip(tr("Enter = 보내기 · Ctrl+Enter = 요약. 쓴 글은 이 PC 의 훈련 기록에만 남습니다"))
        self.input.setClearButtonEnabled(False)
        self.input.sendRequested.connect(self._on_send)
        self.input.summaryRequested.connect(self._on_summary_btn)
        bl.addWidget(self.input)
        axes_row = QHBoxLayout()
        axes_row.setContentsMargins(0, 2, 0, 2)
        self.axis_labels: dict[str, QLabel] = {}
        for axis in AXES:
            lab = QLabel("")
            lab.setTextFormat(Qt.PlainText)        # 까닭 글에 쓴 글의 낱말이 들어갈 수 있다 — 서식으로 해석하지 않는다
            lab.setWordWrap(True)
            lab.setFrameShape(QFrame.StyledPanel)
            lab.setMinimumWidth(0)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            axes_row.addWidget(lab, 1)
            self.axis_labels[axis] = lab
        bl.addLayout(axes_row)
        self.stack.addWidget(body)
        self.page_body = body
        root.addWidget(self.stack, 1)

        # 채팅 표의 검색 칸은 떼어 낸다(이 화면의 입력 칸은 쓰기 칸 하나, D-19 5). 부모 없이 숨긴 채 붙들어 둔다 —
        # ChatPanel.set_messages 가 그 칸을 비우므로 지우지는 않는다. 오른쪽 메뉴도 주지 않는다.
        self._detached_search = None
        search = getattr(self.chat, "search", None)
        if isinstance(search, QWidget):
            search.setVisible(False)
            search.setEnabled(False)
            search.setParent(None)
            self._detached_search = search
        if hasattr(self.timeline, "set_fixed_tracks_visible"):
            self.timeline.set_fixed_tracks_visible(segments=False, density=True, waveform=False)
        if hasattr(self.timeline, "set_marker_style"):
            self.timeline.set_marker_style("viewer_write", QColor(LEVEL_COLORS["good"]), False)
        self.timeline.seekRequested.connect(self._on_timeline_seek)

        self.player.positionChanged.connect(self._on_player_position)
        self.player.playingChanged.connect(self._on_playing)
        self.player.errorOccurred.connect(self._on_player_error)
        if hasattr(self.player, "fileLoaded"):
            self.player.fileLoaded.connect(self._on_file_loaded)
        if hasattr(self.player, "clicked"):
            self.player.clicked.connect(self._on_play_btn)

        self.shortcuts: dict[str, QShortcut] = {}
        for key in ("Ctrl+Return", "Ctrl+Enter"):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(self._on_summary_btn)
            self.shortcuts[key] = s

        self._show_axes(None)
        self._update_count()
        self.set_blocked(tr("시청자 훈련을 아직 열지 않았습니다"))

    def _build_summary_page(self) -> QWidget:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(8, 8, 8, 8)

        def _lab(obj: str | None = None) -> QLabel:
            lab = QLabel("")
            lab.setTextFormat(Qt.PlainText)
            lab.setWordWrap(True)
            if obj:
                lab.setObjectName(obj)
            lay.addWidget(lab)
            return lab

        self.sum_title = _lab("panelTitle")
        self.sum_count = _lab()
        self.sum_axes = {a: _lab() for a in AXES}
        self.sum_ex_title = _lab("panelTitle")
        self.sum_examples = [_lab() for _ in range(EXAMPLES_MAX)]
        self.sum_hint = _lab("hint")
        lay.addStretch(1)
        sa.setWidget(page)
        return sa

    # ------------------------------------------------------------------ 시작
    def set_blocked(self, reason: str) -> None:
        self.blocked_label.setText(reason)
        self.stack.setCurrentWidget(self.blocked_label)
        self.btn_play.setEnabled(False)
        self.btn_summary.setEnabled(False)
        self.input.setEnabled(False)

    def start(self, *, chat, segment: Sequence[int] | None = None, segments: Iterable[Sequence[int]] | None = None,
              media_path: str | None = None, timemap=None, viewer_no: Mapping[str, int] | None = None,
              duration_ms: int | None = None, density: Sequence[int] | None = None, density_bucket_ms: int = 10_000,
              project_name: str | None = None, pilot_locked: bool = False, log=None, project_id: str | None = None,
              scenario=None, show_virtual_marks: bool = True, context: Mapping | None = None,
              context_fn: Callable[[int], Mapping] | None = None) -> bool:
        """시청자 훈련을 연다. 막히면 까닭 한 줄을 보이고 False.

        chat: messages.jsonl 줄 목록 또는 chat_panel.ChatData(작업대 것). 화면에는 구간 [시작, 끝) 안만 싣는다.
        segment: 훈련할 VOD 범위. 없으면 시나리오 머리줄의 구간 → segments 의 첫 구간 → [0, duration_ms).
        scenario: load_viewer_scenario 의 dict(또는 F 와 같은 모양). 섞기가 깨지거나 가상 줄이 0개면 막는다(기록 시작 전).
        log: app.viewer_store.ViewerLog (ViewerLog.create 가 시범 잠금을 한 번 더 본다). project_id: start 줄 칸.
        context / context_fn(vod_ms): 채점 맥락에 더할 값(주제 흐름 낱말·시각별 전사 낱말 등, 엔진이 미리 만든 파일에서)."""
        ok, why = viewer_menu_state(project_name, pilot_locked=pilot_locked)
        if not ok:
            self.set_blocked(why)
            return False
        seg = None
        if segment is not None:
            seg = (int(segment[0]), int(segment[1]))
        if seg is None and scenario is not None:
            seg = scenario_range(scenario)
        if seg is None:
            seg = _tv.first_segment(segments)
        if seg is None and _is_int(duration_ms) and duration_ms > 0:
            seg = (0, int(duration_ms))
        if seg is None or seg[1] <= seg[0] or seg[0] < 0:
            self.set_blocked(tr(REASON_NO_RANGE))
            return False
        built = self._build_rows(chat, seg, viewer_no, scenario, show_virtual_marks)
        if built is None:
            return False
        rows, vno, vlines, vnames = built
        scen_info = _tv._scenario_info(scenario) if scenario is not None else None
        self._log = log
        if log is not None:
            extra = {"scenario": {**scen_info, "virtual_lines": len(vlines)}} if scen_info is not None else {}
            try:
                log.start(project_id=project_id or "", range_ms=seg, **extra)
            except Exception as e:  # noqa: BLE001 — 기록 없이 열지 않는다
                LOG.warning("시청자 훈련 기록 시작 실패: %s", type(e).__name__)
                self._log = None
                self.set_blocked(tr("시청자 훈련 기록을 시작하지 못했습니다 ({0})").format(type(e).__name__))
                return False
        self._segment = seg
        self._rows, self._vno, self._vlines, self._vnames = rows, vno, vlines, vnames
        self._scenario = scen_info
        self._practice, self._scores, self._mine = [], [], []
        self._context = dict(context or {})
        self._context_fn = context_fn
        self._vsc_ctx = None
        self._last, self._summary, self._examples = None, None, []
        self._ended = False
        if timemap is not None:
            self._to_file = lambda ms: int(timemap.to_file(int(ms)))
            self._to_vod = lambda ms: int(timemap.to_vod(int(ms)))
        else:
            self._to_file, self._to_vod = _tv._ident, _tv._ident
        dur = int(duration_ms) if _is_int(duration_ms) and duration_ms > 0 else seg[1]
        self.timeline.set_duration(max(dur, seg[1]))
        if density:
            self.timeline.set_density(list(density), int(density_bucket_ms))
        if hasattr(self.timeline, "clear_markers"):
            self.timeline.clear_markers("viewer_write")
        self.timeline.set_view(seg[0], seg[1])
        self.right.setCurrentWidget(self.chat)
        self.stack.setCurrentWidget(self.page_body)
        self.btn_play.setEnabled(True)
        self.btn_summary.setEnabled(True)
        self.input.setEnabled(True)
        self.input.clear()
        self._state = "training"
        self._vod_pos = seg[0]
        self._seen_until = seg[0]
        self._cd = None
        self._refresh_chat()
        self._apply_position(seg[0])
        self._set_muted(False)                    # 시청자 자리: 소리를 켜고 본다(21 6-1)
        self._has_media = False
        self._autoplay = True
        if media_path and self.player.load(media_path) is not False:
            self._has_media = True
        if self._has_media:
            self.player.seek(self._to_file(seg[0]))
            self._say(tr("소리를 켜고 재생합니다. 아래 칸에 채팅을 쓰고 Enter"))
        else:
            self._autoplay = False
            self._say(tr("영상이 연결되지 않아 채팅만 보입니다"))
        self._started = True
        self._emit("viewer.start", mode="viewer", source="replay", audio="on",
                   range={"start_ms": seg[0], "end_ms": seg[1]},
                   **({**scen_info, "virtual_lines": len(vlines)} if scen_info is not None else {}))
        self._show_axes(None)
        self._update_count()
        LOG.info("시청자 훈련 시작: 구간 %d~%d ms · 실제 채팅 %d줄 · 가상 채팅 %d줄", seg[0], seg[1],
                 len(rows) - len(vlines), len(vlines))
        QTimer.singleShot(0, self.input, self.input.setFocus)
        return True

    def _build_rows(self, chat, seg, viewer_no, scenario, show_marks):
        """→ (화면 줄, 시청자 번호표, 가상 줄 dict, 가상 가림 이름) 또는 None(막힘, 까닭은 화면에)."""
        from ..core import training as trn
        from .chat_panel import ChatData, compute_viewer_no
        if isinstance(chat, ChatData):
            if scenario is not None:
                self.set_blocked(tr("가상 채팅을 섞으려면 채팅 줄 목록이 필요합니다"))
                return None
            rows, vno = _rows_from_chatdata(chat, seg)
            return rows, vno, {}, {}
        if not isinstance(chat, (list, tuple)):
            self.set_blocked(tr("채팅을 읽지 못했습니다"))
            return None
        vlines: dict = {}
        vnames: dict[str, str] = {}
        if scenario is None:
            rows = _clip_real(chat, seg)
        else:
            try:
                mixed = self._mix_fn(list(chat), scenario)
                if isinstance(mixed, tuple) and len(mixed) == 2 and isinstance(mixed[0], (list, tuple)):
                    mixed = mixed[0]
                rows, vlines = trn.split_mixed(mixed, real=chat)
            except Exception as e:  # noqa: BLE001 — 깨진 시나리오·섞기 모듈 없음: 열지 않는다
                LOG.warning("시청자 훈련 가상 채팅 섞기 실패: %s", type(e).__name__)
                self.set_blocked(tr("가상 채팅을 섞지 못했습니다 ({0})").format(type(e).__name__))
                return None
            rows, vlines, n_out, n_dup = _tv._clip_mixed(rows, vlines, seg)
            if n_out or n_dup:
                LOG.info("시청자 훈련 섞기: 구간 밖 실제 채팅 %d줄 · 실제와 본문이 같은 가상 줄 %d줄 뺌", n_out, n_dup)
            if not vlines:
                self.set_blocked(tr(REASON_NO_SCENARIO))
                return None
            rows = [dict(r) if trn.is_virtual_id(r.get("chat_id")) else r for r in rows]
            for r in rows:
                vid = r.get("chat_id")
                if trn.is_virtual_id(vid):
                    vnames[vid] = str(r.get("nickname_masked") or "")
                    if show_marks and r.get("source") in VIRTUAL_TAG:   # F 와 같은 꼴: 앞 한 글자 + 뒤 표시
                        src = r["source"]
                        r["nickname_masked"] = (f"{tr(VIRTUAL_MARK[src])} {r['nickname_masked']} · "
                                                f"{tr(VIRTUAL_TAG[src])}")
        if viewer_no is not None:
            vno = {k: v for k, v in dict(viewer_no).items() if isinstance(v, int)}
        else:
            vno = compute_viewer_no([r for r in rows if not trn.is_virtual_id(r.get("chat_id"))])
        nxt = max(vno.values(), default=0)
        for r in rows:                         # 가상 가명 번호는 뒤에 붙인다('-' 로 드러나지 않게, F 와 같음)
            vid = r.get("viewer_id")
            if trn.is_virtual_id(r.get("chat_id")) and isinstance(vid, str) and vid not in vno:
                nxt += 1
                vno[vid] = nxt
        return list(rows), vno, vlines, vnames

    # ------------------------------------------------------------------ 쓰기
    def write_count(self) -> int:
        return len(self._practice)

    def virtual_line_count(self) -> int:
        return len(self._vlines)

    def practice_ids(self) -> list[str]:
        """화면 표의 연습 줄 id (쓴 순서)."""
        return [r["chat_id"] for r in self._practice]

    def last_result(self) -> dict | None:
        """마지막 쓰기의 writeScored 값(본문 없음)."""
        return self._last

    def last_scores(self) -> dict | None:
        """마지막 쓰기의 세 축 {level, code, reason} (화면에 보인 것)."""
        return self._scores[-1] if self._scores else None

    def _on_send(self) -> None:
        if self.write(self.input.text()) is not None:
            self.input.clear()
        self.input.setFocus()

    def build_context(self, vod_ms: int, *, exclude: str | None = None) -> dict:
        """채점 맥락 (모듈 머리말). recent_chat 은 vod_ms 앞 CONTEXT_WINDOW_MS 안에 화면에 나온 실제·가상 줄(연습 줄 빼고,
        exclude 화면 id 빼고). my_previous 는 vod_ms 까지 쓴 내 글."""
        from ..core import training as trn
        a = int(vod_ms) - CONTEXT_WINDOW_MS
        recent = []
        for r in self._rows:
            t = r.get("t_ms")
            cid = r.get("chat_id")
            if not _is_int(t) or t < a or t > int(vod_ms) or cid == exclude:
                continue
            if r.get("kind", "text") != "text":
                continue
            recent.append({"t_ms": t, "text": r.get("text") if isinstance(r.get("text"), str) else "",
                           "virtual": trn.is_virtual_id(cid)})
        recent.sort(key=lambda x: x["t_ms"])
        mine = [p["text"] for p in self._practice if p["t_ms"] <= int(vod_ms)]
        ctx = dict(self._context)
        if self._context_fn is not None:
            try:
                extra = self._context_fn(int(vod_ms))
                if isinstance(extra, Mapping):
                    ctx.update(extra)
            except Exception as e:  # noqa: BLE001 — 덧붙일 맥락이 없어도 채점은 한다
                LOG.warning("시청자 훈련 채점 맥락 읽기 실패: %s", type(e).__name__)
        ctx.update({"vod_ms": int(vod_ms), "range_ms": self._segment, "window_ms": CONTEXT_WINDOW_MS,
                    "recent_chat": recent, "my_previous": mine})
        return ctx

    def _score_builtin(self, text: str, ctx: Mapping) -> dict | None:
        """채점 담당(D-20 3)의 순수 모듈 app.core.viewer_score: score_chat(text, sent_ms, ScoreContext, history).
        ScoreContext 는 구간마다 한 번 만든다 — topic_blocks·transcript·settings 는 start(context=…) 에서(엔진이 미리 만든
        주제 흐름·VOD 로 보정한 시각별 전사 낱말을 통합 담당이 읽어 준다), chats 는 이 화면에 흐르는 실제·가상 줄
        (연습 줄은 넣지 않는다 — 내 글은 history=my_previous 로 따로 본다)."""
        from ..core import viewer_score as vsc
        settings = self._context.get("settings")
        if self._vsc_ctx is None:
            chats = [(r["t_ms"], r["text"]) for r in self._rows
                     if _is_int(r.get("t_ms")) and isinstance(r.get("text"), str) and r.get("kind", "text") == "text"]
            # 창별 주제 벡터·동의어(선택, G 둘째): app.viewer_flow.build_context 가 맞는 파일만 넣는다. 없으면 None(지금처럼)
            self._vsc_ctx = vsc.ScoreContext.build(self._context.get("topic_blocks") or (),
                                                   self._context.get("transcript") or (), chats, settings=settings,
                                                   topic_vectors=self._context.get("topic_vectors"),
                                                   synonyms=self._context.get("synonyms"))
        res = vsc.score_chat(text, int(ctx["vod_ms"]), self._vsc_ctx, list(ctx.get("my_previous") or ()),
                             settings=settings)
        return from_viewer_score(res, getattr(vsc, "reason_line", None))

    def _score(self, text: str, ctx: Mapping) -> tuple[dict | None, str | None, str | None]:
        """→ (세 축, 실패 코드, scorer 판). 본문은 로그에 남기지 않는다(예외 종류만)."""
        try:
            raw = self._score_fn(text, ctx) if self._score_fn is not None else self._score_builtin(text, ctx)
        except ImportError:
            return None, "scorer_missing", None
        except Exception as e:  # noqa: BLE001
            LOG.warning("시청자 훈련 채점 실패: %s", type(e).__name__)
            return None, "scorer_failed", None
        tag = scorer_tag(raw) if self._score_fn is not None else _builtin_tag()
        sc = normalize_score(raw)
        if sc is None:
            return None, "scorer_failed", tag
        return sc, None, tag

    def write(self, text: str) -> dict | None:
        """Enter 의 뜻: 지금 재생 위치(VOD)로 연습 채팅 한 줄을 보낸다. 재생은 멈추지 않는다.
        채점 → 기록(log 가 있으면, 실패면 보내지 않음) → 채팅 표에 '연습' 줄 → 세 축 칸. writeScored 값 또는 None."""
        if self._state != "training":
            return None
        t = _clean_text(text)
        if not t:
            return None
        if len(t) > MAX_TEXT_CHARS:
            self._say(tr("채팅이 너무 깁니다 (최대 {0}자)").format(MAX_TEXT_CHARS))
            return None
        vod = max(0, int(self._vod_pos))
        sc, err, tag = self._score(t, self.build_context(vod))
        if self._log is not None:
            try:
                self._log.write(t, vod, sc, score_error=err, scorer=tag)
            except Exception as e:  # noqa: BLE001 — 기록과 화면이 어긋나지 않게 보내지 않는다
                LOG.warning("시청자 훈련 기록 쓰기 실패: %s", type(e).__name__)
                self._say(tr("기록을 쓰지 못해 보내지 않았습니다 ({0})").format(type(e).__name__))
                return None
        n = len(self._practice) + 1
        self._practice.append({"chat_id": f"{PRACTICE_ID_PREFIX}{n}", "t_ms": vod, "text": t,
                               "nickname_masked": tr(PRACTICE_NICK), "viewer_id": PRACTICE_VIEWER_ID,
                               "kind": "text", "role": "viewer", "source": "practice"})
        self._scores.append(sc)
        self._mine.append(t)
        self._seen_until = max(int(self._seen_until), vod)
        self._refresh_chat(added=self._practice[-1])
        self._show_axes(sc, err)
        if hasattr(self.timeline, "set_markers"):
            self.timeline.set_markers("viewer_write", [(p["t_ms"], "viewer_write") for p in self._practice],
                                      label=tr("연습 채팅"))
        levels = {a: (sc[a]["level"] if sc else None) for a in AXES}
        codes = {a: (sc[a]["code"] if sc else err) for a in AXES}
        res = {"n": n, "vod_ms": vod, "chars": len(t), "levels": levels, "codes": codes}
        self._last = res
        self._emit("viewer.write", **res)
        self.writeScored.emit(dict(res))
        self._update_count()
        if sc is None:
            self._say(tr("보냈습니다 ({0}번째) — 채점하지 못해 단계 없이 남겼습니다").format(n))
        else:
            self._say(tr("보냈습니다 ({0}번째)").format(n))
        LOG.info("시청자 훈련 쓰기: %d번째 · 주제 %s · 확장 %s · 반복 %s", n, levels["topic"], levels["expand"],
                 levels["repeat"])
        return res

    def _refresh_chat(self, added: Mapping | None = None) -> None:
        """채팅 표를 다시 싣는다. added(연습 줄 하나)가 있으면 지난 ChatData 에 그 줄만 끼운다(_chatdata_insert —
        줄 dict 를 다시 훑지 않는다). 흐르는 채팅·'연습' 표시를 다시 건다."""
        from .chat_panel import ChatData
        cd = _chatdata_insert(self._cd, added, self._vno) if added is not None and self._cd is not None else None
        if cd is None:
            cd = ChatData([*self._rows, *self._practice], dict(self._vno))
        self._cd = cd
        self.chat.set_messages(cd)
        if hasattr(self.chat, "set_label_marks"):
            marks = {p["chat_id"]: tr(PRACTICE_MARK) for p in self._practice}
            try:
                self.chat.set_label_marks(marks, own=True, tooltip=tr(PRACTICE_TIP))
            except TypeError:
                self.chat.set_label_marks(marks, own=True)
        self._apply_position(self._vod_pos)

    def _show_axes(self, sc: Mapping | None, err: str | None = None) -> None:
        for axis in AXES:
            lab = self.axis_labels[axis]
            name = tr(AXIS_NAMES[axis])
            if sc is None:
                tail = tr(REASON_TEXT.get(err, "")) if err else tr("Enter 로 보내면 단계가 나옵니다")
                lab.setText(f"{name}\n{tail}" if tail else name)
                lab.setStyleSheet(f"color: {NO_LEVEL_COLOR};")
                lab.setProperty("level", "")
                continue
            ax = sc[axis]
            lab.setText(f"{name} · {tr(LEVEL_NAMES[ax['level']])}\n{reason_line(ax)}")
            lab.setStyleSheet(f"color: {LEVEL_COLORS[ax['level']]};")
            lab.setProperty("level", ax["level"])

    def _update_count(self) -> None:
        self.count_label.setText(tr("쓴 수 {0}").format(len(self._practice)))

    # ------------------------------------------------------------------ 재생
    def _set_muted(self, mute: bool) -> None:
        if hasattr(self.player, "set_mute"):
            self.player.set_mute(bool(mute))
        else:
            self.player.set_volume(0 if mute else 100)
        self._muted = bool(mute)

    def is_muted(self) -> bool:
        return self._muted

    def toggle_play(self) -> None:
        if self._state != "training" or not self._has_media:
            return
        self.player.toggle()

    def seek_vod(self, vod_ms: int) -> None:
        vod_ms = int(vod_ms)
        self._vod_pos = vod_ms
        if self._has_media:
            self.player.seek(self._to_file(vod_ms))
        self._apply_position(vod_ms)

    def position_vod(self) -> int:
        return int(self._vod_pos)

    def state(self) -> str:
        return self._state

    def _on_play_btn(self, *_a) -> None:
        self.toggle_play()
        self.input.setFocus()

    def _on_summary_btn(self, *_a) -> None:
        self.show_summary("summary")

    def _on_close_btn(self, *_a) -> None:
        self.closeRequested.emit()

    def _on_timeline_seek(self, vod_ms: int) -> None:
        if self._state == "training":
            self.seek_vod(vod_ms)
            self.input.setFocus()

    def _on_file_loaded(self) -> None:
        if self._autoplay and self._state == "training":
            self._autoplay = False
            self.player.play()

    def _on_playing(self, playing: bool) -> None:
        self.btn_play.setText(tr("❚❚ 일시정지") if playing else tr("▶ 재생"))

    def _on_player_error(self, msg: str) -> None:
        self._say(str(msg))

    def _on_player_position(self, file_ms: int) -> None:
        vod = self._to_vod(int(file_ms))
        self._vod_pos = vod
        self._apply_position(vod)
        if self._state == "training" and self._segment is not None and vod >= self._segment[1] - END_TOL_MS:
            self.show_summary("segment_end")

    def _apply_position(self, vod: int) -> None:
        if self._state == "training":
            self._seen_until = max(int(self._seen_until), int(vod))
        if hasattr(self.chat, "set_reveal_until"):
            self.chat.set_reveal_until(int(vod) if self._state == "training" else None)
        self.chat.set_time_ms(int(vod))
        self.timeline.set_position(int(vod))

    # ------------------------------------------------------------------ 요약
    def _row_text(self, vid: str) -> str:
        for r in self._rows:
            if r.get("chat_id") == vid:
                return r.get("text") if isinstance(r.get("text"), str) else ""
        return ""

    def _pick_examples(self) -> list[dict]:
        """'다른 시청자라면' 예시: 구간 안 retrieved 가상 줄 가운데(이미 화면에 나온 것 먼저) 같은 채점기로 단계가 좋은
        순서(좋음 수 → 보통 수 → 시각)로 EXAMPLES_MAX 줄. 화면에만 보인다(본문은 기록·로그에 넣지 않는다)."""
        seg = self._segment
        cands = [vl for vl in self._vlines.values()
                 if vl.source == "retrieved" and vl.show_vod_ms is not None
                 and (seg is None or seg[0] <= vl.show_vod_ms < seg[1])]
        seen = [vl for vl in cands if vl.show_vod_ms <= self._seen_until]
        pool = seen or cands
        scored = []
        for vl in pool:
            text = self._row_text(vl.vid)
            if not text:
                continue
            ctx = self.build_context(vl.show_vod_ms, exclude=vl.vid)
            ctx["my_previous"] = []
            sc, _err, _tag = self._score(text, ctx)
            # 순서는 주제 적합성·대화 확장성만 본다 — 반복 여부는 그 줄이 맥락 채팅에 이미 있어 '베낌' 으로 나올 수 있다
            good = sum(1 for a in RANK_AXES if sc and sc[a]["level"] == "good")
            ok = sum(1 for a in RANK_AXES if sc and sc[a]["level"] == "ok")
            scored.append((-good, -ok, vl.show_vod_ms, vl.line_id, vl, text, sc))
        scored.sort(key=lambda x: x[:4])
        out = []
        for *_k, vl, text, sc in scored[:EXAMPLES_MAX]:
            out.append({"vid": vl.vid, "line_id": vl.line_id, "source": vl.source, "show_vod_ms": vl.show_vod_ms,
                        "name": self._vnames.get(vl.vid) or "", "text": text,
                        "levels": {a: (sc[a]["level"] if sc else None) for a in AXES}})
        return out

    def summary(self) -> dict | None:
        """마지막 요약 숫자 {"writes", "levels", "examples"} (요약 전이면 None)."""
        return self._summary

    def examples(self) -> list[dict]:
        """마지막 요약의 예시 줄(화면용 — 가림 이름·본문 포함). 기록·로그로 넘기지 않는다."""
        return list(self._examples)

    def show_summary(self, reason: str = "summary") -> bool:
        """요약 화면으로(구간 끝 'segment_end' 또는 단추·Ctrl+Enter 'summary'). 재생을 멈추고 쓰기 칸을 닫는다."""
        if self._state != "training":
            return False
        reason = reason if reason in ("segment_end", "summary") else "summary"
        if self._has_media:
            self.player.pause()
        levels = level_counts(self._scores)
        examples = self._pick_examples()
        self._end_log(reason, len(examples))
        self._state = "summary"
        self._examples = examples
        self._summary = {"writes": len(self._practice), "levels": levels, "examples": len(examples)}
        if hasattr(self.chat, "set_reveal_until"):
            self.chat.set_reveal_until(None)
        self._fill_summary(levels, examples)
        self.right.setCurrentWidget(self.summary_page)
        self.input.setEnabled(False)
        self.btn_summary.setEnabled(False)
        self.btn_play.setEnabled(False)
        self._say(tr("구간 끝 — [닫기] 로 작업대로 돌아갑니다"))
        self.summaryShown.emit({"writes": len(self._practice), "levels": levels, "examples": len(examples)})
        LOG.info("시청자 훈련 요약: 쓴 수 %d · 주제 %s · 확장 %s · 반복 %s · 예시 %d", len(self._practice),
                 _fmt_counts(levels["topic"]), _fmt_counts(levels["expand"]), _fmt_counts(levels["repeat"]),
                 len(examples))
        return True

    def _fill_summary(self, levels: Mapping, examples: Sequence[Mapping]) -> None:
        from .chat_panel import display_text
        self.sum_title.setText(tr("연습 요약"))
        self.sum_count.setText(tr("쓴 채팅 {0}개").format(len(self._practice)))
        for a in AXES:
            c = levels[a]
            self.sum_axes[a].setText(tr("{0}: 좋음 {1} · 보통 {2} · 아쉬움 {3}").format(
                tr(AXIS_NAMES[a]), c["good"], c["ok"], c["weak"]))
        self.sum_ex_title.setText(tr("다른 시청자라면 (가져온 채팅 · 연습용)"))
        for i, lab in enumerate(self.sum_examples):
            if i < len(examples):
                ex = examples[i]
                tag = tr(VIRTUAL_TAG.get(ex["source"], ""))
                name = ex["name"] or tr("시청자")
                lab.setText(f"{name} · {tag} — {display_text(ex['text'])}")
                lab.setVisible(True)
            elif i == 0:
                lab.setText(tr("보여 줄 가상 채팅이 없습니다 (가상 채팅을 만들면 여기에 몇 줄 나옵니다)"))
                lab.setVisible(True)
            else:
                lab.setText("")
                lab.setVisible(False)
        self.sum_hint.setText(tr("세 축은 따로 봅니다 — 합계 점수는 내지 않습니다. 쓴 글은 이 PC 의 기록에만 남습니다"))

    # ------------------------------------------------------------------ 끝
    def _end_log(self, reason: str, examples: int | None = None) -> None:
        if self._ended or not self._started:
            return
        self._ended = True
        if self._log is not None:
            try:
                self._log.end(reason, examples=examples)
            except Exception as e:  # noqa: BLE001 — 끝 줄이 없어도 화면은 닫는다
                LOG.warning("시청자 훈련 기록 끝 줄 실패: %s", type(e).__name__)
        levels = level_counts(self._scores)
        self._emit("viewer.end", reason=reason, writes=len(self._practice), levels=levels,
                   **({"examples": examples} if examples is not None else {}))

    def close_view(self) -> None:
        """시청자 훈련을 끝낸다(여러 번 불러도 된다). 요약 전이면 end(closed) 를 쓰고 플레이어를 닫는다."""
        if self._state == "closed":
            return
        self._end_log("closed")
        self._state = "closed"
        try:
            self.player.close_player()
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event):
        self.close_view()
        super().closeEvent(event)

    # ------------------------------------------------------------------ 도우미
    def _emit(self, kind: str, **fields) -> None:
        ev = {"kind": kind, **fields}
        self.events.append(ev)
        self.viewerEvent.emit(ev)

    def _say(self, text: str) -> None:
        self.msg.setText(text)
        if text:
            self.messageShown.emit(text)


def _fmt_counts(c: Mapping) -> str:
    return f"{c.get('good', 0)}/{c.get('ok', 0)}/{c.get('weak', 0)}"


def input_fields(widget: QWidget) -> list[QWidget]:
    """widget 안의 글자 입력 칸(QLineEdit·QTextEdit·QPlainTextEdit·숫자 칸·고쳐 쓰는 고르기 칸) — 위젯 수 시험용."""
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QPlainTextEdit, QTextEdit
    out: list[QWidget] = []
    for w in widget.findChildren(QWidget):
        if isinstance(w, QLineEdit):
            p = w.parentWidget()
            if isinstance(p, (QAbstractSpinBox, QComboBox)):
                continue                    # 숫자 칸·고르기 칸 속의 줄은 그 칸 하나로 센다
            out.append(w)
        elif isinstance(w, (QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            out.append(w)
        elif isinstance(w, QComboBox) and w.isEditable():
            out.append(w)
    return out


__all__ = ["ViewerTrainView", "viewer_menu_state", "is_pilot_project", "normalize_score", "from_viewer_score",
           "reason_line", "level_counts",
           "load_viewer_scenario", "scenario_range", "input_fields", "AXES", "LEVELS", "AXIS_NAMES", "LEVEL_NAMES",
           "PRACTICE_MARK", "CONTEXT_WINDOW_MS", "EXAMPLES_MAX", "REASON_PILOT"]
