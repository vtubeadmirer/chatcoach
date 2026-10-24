"""엔진 등급을 분석 화면에서 '반응 있음' 으로 볼지 — 화면 공통 규칙 한 곳 (08 D-31 1).

규칙 (보여 주는 방식만 바꾼다 — 문턱·엔진·저장 형식·점수는 그대로)
- 기본(옵션 꺼짐): 엔진 **direct 만** '반응 있음'. likely 는 흐리게(속 빈 회색 표시) 따로 센다.
- 보기 옵션 'likely 도 반응으로 보기' 를 켜면 전처럼 direct·likely 둘 다 '반응 있음'.
- related_only·unclear·반응 미확인은 어느 쪽이든 '반응 있음' 이 아니다(06 4-3 과 같다).
- 엔진 판정에만 쓴다. 사람 최종 세트(라벨)의 등급, ai_compare 점수(06 4-3 사전 정의 — direct·likely = 반응 있음),
  AI 확인 큐(08 D-30 1 — direct·likely 둘 다 판정 대상)에는 쓰지 않는다.
근거: docs/measurements/2026-10-24-pilot-first-score.md 2-1 — 시범 1편(v0.3)에서 direct 정밀도 0.88 · likely 0.23.

쓰는 곳: 나란히 보기(app/ui/analysis_view) · 방송 요약(app/ui/summary_view, 자료는 app/analysis_flow.broadcast_summary 가
엔진 판정으로 셀 때) · 작업대 엔진 띠(app/ui/workbench). 시청자 훈련·가상 채팅·스트리머 훈련은 엔진 반응 등급을 읽지 않는다
(시청자 훈련은 주제·전사 낱말·벡터, 스트리머 훈련은 사람 최종 세트·손라벨).

설정: settings.ini 의 키 하나 SETTINGS_KEY (기본 꺼짐). 화면마다 같은 키를 읽고, 어느 화면에서 바꾸든 write_include_likely 가
같은 프로세스에 열린 다른 화면에 알린다(subscribe — 화면이 지워지면 저절로 빠지는 약한 참조).
이 모듈은 Qt·파일을 쓰지 않는다(app/core 층 규칙, 02 3-1): 설정 객체(QSettings 처럼 .value·.setValue·.sync 가 있는 것)는
부르는 쪽이 넘기고, find_settings 도 받은 객체의 속성만 따라 올라간다.
"""
from __future__ import annotations

import weakref
from typing import Any, Callable, Iterable

DIRECT = "direct"
LIKELY = "likely"
POSITIVE_GRADES = (DIRECT, LIKELY)       # 점수·AI 확인 큐의 '반응 있음'(06 4-3). 이 모듈은 이 정의를 바꾸지 않는다

# settings.ini 키 (그룹/이름 — mainwindow 의 'startup/resume_last' · 'session/last_labeler' 와 같은 꼴). 값은 true·false
SETTINGS_KEY = "analysis/likely_as_reaction"
DEFAULT_INCLUDE_LIKELY = False           # 08 D-31 1: 기본은 direct 만

OPTION_LABEL = "likely 도 반응으로 보기"
OPTION_TIP = ("끄면(기본) 엔진 '직접(direct)' 만 반응으로 보고, '높음(likely)' 은 흐리게(속 빈 회색 점) 따로 셉니다. "
              "켜면 전처럼 둘 다 반응입니다. 시범 1편에서 direct 는 0.88, likely 는 0.23 만 맞았습니다 (08 D-31 1). "
              "문턱·엔진·저장·점수는 그대로이고 보여 주는 방식만 바뀝니다. "
              "나란히 보기·방송 요약·작업대 엔진 띠가 같은 설정을 씁니다.")
DIM_TEXT = "흐림"
_TRUE = ("true", "1", "yes", "on")


# ---------------------------------------------------------------------------- 규칙
def counts_as_reaction(grade, *, include_likely: bool) -> bool:
    """엔진 등급이 화면에서 '반응 있음' 인가. direct 는 늘, likely 는 include_likely 일 때만.
    그 밖(related_only·unclear·반응 미확인·None·모르는 값)은 아니다."""
    if grade == DIRECT:
        return True
    return bool(include_likely) and grade == LIKELY


def is_dimmed(grade, *, include_likely: bool) -> bool:
    """흐리게(속 빈 회색) 보일 엔진 등급인가 — likely 이고 옵션이 꺼졌을 때만."""
    return grade == LIKELY and not include_likely


def count_grades(grades: Iterable) -> dict[str, int]:
    """엔진 등급들 → {'direct': n, 'likely': m}. 그 밖의 값은 세지 않는다."""
    out = {DIRECT: 0, LIKELY: 0}
    for g in grades or ():
        if g in out:
            out[g] += 1
    return out


def count_text(direct: int, likely: int, *, include_likely: bool) -> str:
    """합계 숫자 글: 꺼짐 'direct n · likely m(흐림)', 켬 'direct n · likely m'. 둘 다 늘 보인다."""
    tail = "" if include_likely else f"({DIM_TEXT})"
    return f"direct {int(direct)} · likely {int(likely)}{tail}"


def rule_text(*, include_likely: bool, likely: int) -> str:
    """머리 줄 한 줄: 꺼짐 'direct 만 · likely n건 흐림', 켬 'direct·likely 모두 반응 · likely n건 포함'."""
    if include_likely:
        return f"direct·likely 모두 반응 · likely {int(likely)}건 포함"
    return f"direct 만 · likely {int(likely)}건 {DIM_TEXT}"


# ---------------------------------------------------------------------------- 설정 (객체는 부르는 쪽이 준다)
def parse_setting(value) -> bool:
    """settings.ini 값 → 켬/끔. True·'true'·'1'·'yes'·'on' 만 켬. 없거나 그 밖이면 기본(꺼짐)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return DEFAULT_INCLUDE_LIKELY


def read_include_likely(settings) -> bool:
    """settings(.value)의 SETTINGS_KEY. settings 가 None 이거나 읽다 실패하면 기본(꺼짐)."""
    if settings is None:
        return DEFAULT_INCLUDE_LIKELY
    try:
        v = settings.value(SETTINGS_KEY, DEFAULT_INCLUDE_LIKELY)
    except Exception:  # noqa: BLE001 — 설정 파일 문제로 화면을 막지 않는다(기본값)
        return DEFAULT_INCLUDE_LIKELY
    return parse_setting(v)


def write_include_likely(settings, on: bool, *, source: object | None = None) -> bool:
    """settings 에 남기고(.setValue·.sync) 같은 프로세스에 열린 다른 화면에 알린다(source 의 알림 함수는 건너뜀).
    settings 가 None 이면 남기지 않고 알리기만 한다. 남겼으면 True."""
    on = bool(on)
    saved = False
    if settings is not None:
        try:
            settings.setValue(SETTINGS_KEY, on)
            sync = getattr(settings, "sync", None)
            if callable(sync):
                sync()
            saved = True
        except Exception:  # noqa: BLE001 — 못 남겨도 지금 열린 화면에는 알린다
            saved = False
    notify(on, source=source)
    return saved


def find_settings(obj, *, max_depth: int = 32):
    """obj 와 그 부모(.parent())를 따라 올라가며 처음 만나는 settings 속성(.value·.setValue 가 있는 것) — 메인 창의
    settings.ini(메인 창·작업대의 settings). 없으면 None(그 화면은 남기지 않고 기본값으로 시작한다)."""
    w = obj
    for _ in range(max(1, int(max_depth))):
        if w is None:
            return None
        s = getattr(w, "settings", None)
        if s is not None and callable(getattr(s, "value", None)) and callable(getattr(s, "setValue", None)):
            return s
        parent = getattr(w, "parent", None)
        try:
            w = parent() if callable(parent) else None
        except Exception:  # noqa: BLE001 — 지워진 Qt 객체 등: 더 올라가지 않는다
            return None
    return None


# ---------------------------------------------------------------------------- 열린 화면에 알리기 (같은 프로세스)
_listeners: list = []          # weakref.WeakMethod(화면의 묶인 메서드) 또는 _Strong — 화면을 붙잡지 않는다(08 D-18 10)


class _Strong:
    """묶인 메서드가 아닌 알림 함수(함수·람다·내장 메서드)를 그대로 잡아 둔다 — weakref 처럼 부르면 함수를 돌려준다."""
    __slots__ = ("cb",)

    def __init__(self, cb):
        self.cb = cb

    def __call__(self):
        return self.cb


def _ref(callback: Callable[[bool], Any]):
    if getattr(callback, "__self__", None) is not None and getattr(callback, "__func__", None) is not None:
        return weakref.WeakMethod(callback)
    return _Strong(callback)


def subscribe(callback: Callable[[bool], Any]) -> None:
    """옵션이 바뀌면 callback(켬/끔) 을 부른다. 화면의 묶인 메서드는 약하게 잡는다 — 화면이 지워지면 저절로 빠진다.
    그 밖의 함수(람다 등)는 강하게 잡으므로 다 쓰면 unsubscribe 로 뺀다(화면을 잡은 람다는 넘기지 않는다, D-18 10)."""
    _prune()
    for r in _listeners:
        if r() == callback:
            return
    _listeners.append(_ref(callback))


def unsubscribe(callback: Callable[[bool], Any]) -> None:
    for r in list(_listeners):
        cb = r()
        if cb is None or cb == callback:
            _listeners.remove(r)


def _prune() -> None:
    _listeners[:] = [r for r in _listeners if r() is not None]


def listener_count() -> int:
    """살아 있는 알림 함수 수(시험·점검용)."""
    _prune()
    return len(_listeners)


def notify(on: bool, *, source: object | None = None) -> int:
    """열린 화면들에 알린다. source(바꾼 화면)의 묶인 메서드는 건너뛴다. 부른 수."""
    n = 0
    for r in list(_listeners):
        cb = r()
        if cb is None:
            _listeners.remove(r)
            continue
        if source is not None and getattr(cb, "__self__", None) is source:
            continue
        try:
            cb(bool(on))
            n += 1
        except RuntimeError:          # 지워진 Qt 화면(C++ 쪽) — 목록에서 뺀다
            if r in _listeners:
                _listeners.remove(r)
        except Exception:  # noqa: BLE001 — 한 화면 문제로 다른 화면 알림을 막지 않는다
            continue
    return n


__all__ = ["DIRECT", "LIKELY", "POSITIVE_GRADES", "SETTINGS_KEY", "DEFAULT_INCLUDE_LIKELY", "OPTION_LABEL",
           "OPTION_TIP", "DIM_TEXT", "counts_as_reaction", "is_dimmed", "count_grades", "count_text", "rule_text",
           "parse_setting", "read_include_likely", "write_include_likely", "find_settings", "subscribe",
           "unsubscribe", "notify", "listener_count"]
