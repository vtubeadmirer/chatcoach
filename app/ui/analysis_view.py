"""방송 분석 모드 — 엔진 판정과 사람 최종 판정 나란히 보기 (08 D-01 '방송 분석', D-14 8, D-15 7, 16 8-1 5번).

받는 것(생성자·set_data 인자. 이 파일은 디스크를 열지 않는다 — 06 7-3, 05 S-11):
- run: **봉인 해제된** 엔진 결과 dict(ai-result v1, 또는 engine/link.py 의 v2 초안). 해제 전이면 None 을 넘긴다.
- final_rows: 최종 세트 행 목록(export-row 모양 dict. 16 8-1 4번 — 조정 값으로 바뀌고 exclude_from_eval 이 빠진 것).
  반응 행(label_kind = reaction)만 쓴다. effective = false · conflict = true 인 행은 뺀다. label-event 반응 레코드나
  app.core.final_set.FinalReaction(.record·.source·.reasons) 을 그대로 넘겨도 된다(as_final_row 가 바꾼다. 이때 채팅
  시각은 chat_times 로 받는다).
- sealed_count: 해제 전일 때 보일 봉인 건수. run 이 None 이면 이 숫자만 보이고 다른 것은 아무것도 그리지 않는다
  (D-15 7 '해제 전에는 열지 않는다'). final_rows 를 같이 넘겨도 해제 전에는 계산도 하지 않는다.
- scores: ai_compare 결과(엄격·참고 점수). 이 화면은 계산하지 않고 받은 값을 표로 보이기만 한다.
- chat_times: chat_id → 채팅 VOD 시각(ms). 없으면 최종 세트 행의 chat_t_ms, 그다음 엔진 v2 의 chat_t_ms 를 쓴다.

화면
- 위: 타임라인(app.ui.timeline.Timeline 의 마커 트랙 두 개 — 위 '엔진', 아래 '사람 최종'). 같은 채팅은 같은 시각(채팅 시각)에
  위아래로 찍는다. 색 = 비교 결과(같음·등급 다름·엔진만·사람만), 속이 빈 표시 = 그쪽 판정이 '반응 미확인'.
- 왼쪽: 목록(시각 · 엔진 · 사람 · 결과). 필터: 전체 · 다름만(같음 아닌 것) · 놓침만(사람만 = 사람은 반응을 찾았는데 엔진은
  못 찾거나 평가하지 않음). [이전 다른 곳] [다음 다른 곳] — 단축키 N / Shift+N.
- 오른쪽 위: 고른 채팅의 두 판정(등급·확신도·반응 시각·근거)과 증거 7종(엔진: 연결 후보의 present·at_ms, 사람: 연결 발화의
  강·중·약 목록).
- 오른쪽 아래: 합계 표(비교 결과 건수, 엔진 × 사람 등급 혼동표)와 ai_compare 점수 표.

비교 규칙(이 화면의 표시용. 점수는 ai_compare 가 낸다)
- 같음: 두 쪽 모두 판정이 있고 값(등급, 또는 반응 미확인)이 같다.
- 등급 다름: 두 쪽 모두 등급이 있는데(반응 후보 있음) 다르다.
- 엔진만: 엔진은 등급이 있고 사람은 반응 미확인이거나 최종 세트에 없다.
- 사람만: 사람은 등급이 있고 엔진은 반응 미확인이거나 평가하지 않았다(ai_unevaluated).
- 한쪽에만 있고 그쪽도 반응 미확인이면 목록에 넣지 않고 합계 표의 '짝 없음(반응 없음)' 으로만 센다.
- 엔진 결과의 같은 chat_id 는 첫 줄만 쓴다(06 6-2). 최종 세트에 같은 chat_id 가 둘 이상이면 조정 값(source = adjudication)을,
  없으면 첫 줄을 쓴다.

채팅 본문·닉네임은 받지도 보이지도 않는다(최종 세트 행의 text·nickname_masked 칸은 읽지 않는다). 사람 근거 한 줄은
조정 화면(D-12 4)처럼 이 화면에만 보이고 로그에 쓰지 않는다. 로그에는 건수만 남긴다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..core.rules import EVIDENCE_KEYS, V0_1_STRENGTH
from ..core.timecode import fmt_display
from ..i18n import tr
from .timeline import Timeline

LOG = logging.getLogger("chatcoach")

# ---------------------------------------------------------------------------- 비교 결과
SAME = "same"
GRADE_DIFF = "grade_diff"
ENGINE_ONLY = "engine_only"
HUMAN_ONLY = "human_only"
STATUSES = (SAME, GRADE_DIFF, ENGINE_ONLY, HUMAN_ONLY)
STATUS_KO = {SAME: "같음", GRADE_DIFF: "등급 다름", ENGINE_ONLY: "엔진만", HUMAN_ONLY: "사람만"}

FILTER_ALL = "all"
FILTER_DIFF = "diff"         # 같음이 아닌 것
FILTER_MISSED = "missed"     # 사람만 (엔진이 놓친 것)
FILTERS = (FILTER_ALL, FILTER_DIFF, FILTER_MISSED)
FILTER_KO = {FILTER_ALL: "전체", FILTER_DIFF: "다름만", FILTER_MISSED: "놓침만"}

# 판정 값: 등급 4개 · 'unconfirmed'(반응 미확인) · 'graded'(등급 없이 graded — 가져오기 검사가 막지만 표시는 한다) · 'absent'(없음)
UNCONFIRMED = "unconfirmed"
GRADED_NO_GRADE = "graded"
ABSENT = "absent"
GRADE_VALUES = ("direct", "likely", "related_only", "unclear")
CONFUSION_VALUES = GRADE_VALUES + (UNCONFIRMED, ABSENT)
VAL_KO = {"direct": "직접", "likely": "높음", "related_only": "관련성만", "unclear": "불명확", UNCONFIRMED: "반응 미확인",
          GRADED_NO_GRADE: "등급 없음", ABSENT: "없음"}
CONF_KO = {"certain": "확실함", "probably": "아마도", "undeterminable": "판단 불가"}
EVIDENCE_KO = {
    "nickname_called": "닉네임 호명", "text_repeated": "문구 되풀이", "direct_reference": "직접 지칭",
    "answer_form": "답의 형태", "unique_content": "고유 내용", "within_time_window": "시간 창", "same_topic": "같은 주제",
}
STRENGTH_KO = {"strong": "강", "medium": "중", "weak": "약", "gate": "관문"}

# 마커 색 (timeline.MARK_STYLES 와 겹치지 않는 종류 이름 'an_*')
STATUS_COLORS = {
    SAME: QColor(120, 144, 156),
    GRADE_DIFF: QColor(255, 167, 38),
    ENGINE_ONLY: QColor(38, 198, 218),
    HUMAN_ONLY: QColor(236, 64, 122),
}
ENGINE_TRACK = "engine"
HUMAN_TRACK = "final"


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _t(ms) -> str:
    return fmt_display(ms) if _is_int(ms) else "—"


@dataclass(frozen=True)
class Side:
    """한쪽(엔진 또는 사람)의 판정. present = 그쪽 자료에 이 채팅이 있음."""
    present: bool = False
    value: str = ABSENT                       # CONFUSION_VALUES 또는 GRADED_NO_GRADE
    reaction_time_ms: int | None = None
    confidence: str | None = None
    evidence: Mapping[str, Any] | None = None  # 키 → 엔진: {'present': bool|None, 'at_ms': int|None} / 사람: 강도 문자열|None
    rationale: str | None = None
    t_ms: int | None = None                   # 이쪽 자료에 적힌 채팅 시각
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def graded(self) -> bool:
        return self.present and self.value not in (UNCONFIRMED, ABSENT)


@dataclass(frozen=True)
class CompareItem:
    chat_id: str
    t_ms: int | None
    status: str
    engine: Side
    human: Side


# ---------------------------------------------------------------------------- 순수 계산 (Qt 없음)
def _value(result, grade) -> str:
    if result == UNCONFIRMED:
        return UNCONFIRMED
    if grade in GRADE_VALUES:
        return grade
    return GRADED_NO_GRADE if result == "graded" else UNCONFIRMED


def engine_sides(run: Mapping | None) -> tuple[dict[str, Side], dict]:
    """엔진 결과 reactions → chat_id 별 Side. (sides, 집계). 같은 chat_id 는 첫 줄만(06 6-2)."""
    out: dict[str, Side] = {}
    st = {"rows": 0, "duplicates": 0, "invalid": 0}
    rows = run.get("reactions") if isinstance(run, Mapping) else None
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, Mapping) or not isinstance(r.get("chat_id"), str) or not r["chat_id"]:
            st["invalid"] += 1
            continue
        st["rows"] += 1
        cid = r["chat_id"]
        if cid in out:
            st["duplicates"] += 1
            continue
        ev = None
        cands = r.get("candidates")
        lc = r.get("linked_candidate")
        if isinstance(cands, list) and _is_int(lc) and 0 <= lc < len(cands) and isinstance(cands[lc], Mapping):
            raw = cands[lc].get("evidence")
            if isinstance(raw, Mapping):
                ev = {}
                for k in EVIDENCE_KEYS:
                    e = raw.get(k)
                    if isinstance(e, Mapping):
                        p = e.get("present")
                        ev[k] = {"present": p if p in (True, False) else None,
                                 "at_ms": e.get("at_ms") if _is_int(e.get("at_ms")) else None}
                    else:
                        ev[k] = {"present": None, "at_ms": None}
        elif isinstance(cands, list) and r.get("result") == UNCONFIRMED:
            # v2 의 반응 미확인: 시간 창 말고 증거가 있는 후보가 없었다 → 7종 모두 없음
            ev = {k: {"present": False, "at_ms": None} for k in EVIDENCE_KEYS}
        rt = r.get("reaction_time_ms")
        out[cid] = Side(
            present=True, value=_value(r.get("result"), r.get("grade")),
            reaction_time_ms=rt if _is_int(rt) else None,
            confidence=r.get("confidence") if r.get("confidence") in CONF_KO else None,
            evidence=ev,
            rationale=r.get("rationale") if isinstance(r.get("rationale"), str) else None,
            t_ms=r.get("chat_t_ms") if _is_int(r.get("chat_t_ms")) else None,
            extra={"score": r.get("score") if isinstance(r.get("score"), (int, float)) and not isinstance(r.get("score"), bool) else None},
        )
    return out, st


def _human_evidence(row: Mapping) -> dict[str, str | None] | None:
    lists = {s: row.get(f"evidence_{s}") for s in ("strong", "medium", "weak")}
    if all(v is None for v in lists.values()):
        return None
    ev: dict[str, str | None] = {k: None for k in EVIDENCE_KEYS}
    for s, v in lists.items():
        for k in v if isinstance(v, list) else []:
            if k in ev and ev[k] is None:
                ev[k] = s
    return ev


def _record_to_row(rec: Mapping, *, source: str | None = None, reasons: Sequence[str] = ()) -> dict | None:
    """label-event 반응 레코드({'kind': 'reaction', 'body': …}) → export-row 모양의 필요한 칸만.
    증거는 연결 발화(linked_utterance_id) 후보의 present 를 기준서 v0.1 강도로 나눈다(연결 발화가 없으면 후보 합집합,
    export 03 16-3 과 같은 규칙). note 칸은 읽지 않는다."""
    body = rec.get("body") if isinstance(rec.get("body"), Mapping) else None
    if rec.get("kind") != "reaction" or body is None:
        return None
    cands = [c for c in body.get("candidates") or [] if isinstance(c, Mapping)]
    linked = body.get("linked_utterance_id")
    use = [c for c in cands if linked is not None and c.get("utterance_id") == linked] or cands
    present: set[str] = set()
    for c in use:
        e = c.get("evidence") if isinstance(c.get("evidence"), Mapping) else {}
        present.update(k for k in EVIDENCE_KEYS if isinstance(e.get(k), Mapping) and e[k].get("present") is True)
    lists = {s: [k for k in EVIDENCE_KEYS if k in present and V0_1_STRENGTH[k] == s] for s in ("strong", "medium", "weak")}
    return {"label_kind": "reaction", "source": source or "label", "effective": True, "conflict": False,
            "chat_id": body.get("chat_id"), "chat_t_ms": None, "result": body.get("result"), "grade": body.get("grade"),
            "confidence": body.get("confidence"), "rationale": body.get("rationale"),
            "reaction_time_ms": body.get("reaction_time_ms"),
            "evidence_strong": lists["strong"], "evidence_medium": lists["medium"], "evidence_weak": lists["weak"],
            "eval": {"excluded_reasons": [r for r in reasons if isinstance(r, str)]}}


def as_final_row(obj) -> Mapping | None:
    """최종 세트 한 건을 export-row 모양으로. 받는 것: export-row dict · label-event 반응 레코드 ·
    app.core.final_set.FinalReaction 처럼 .record·.source·.reasons 를 가진 객체."""
    if isinstance(obj, Mapping):
        if obj.get("label_kind") is not None:
            return obj
        return _record_to_row(obj)
    rec = getattr(obj, "record", None)
    if isinstance(rec, Mapping):
        src = getattr(obj, "source", None)
        reasons = tuple(getattr(obj, "reasons", ()) or ()) + tuple(getattr(obj, "report_reasons", ()) or ())
        return _record_to_row(rec, source=src if isinstance(src, str) else None, reasons=reasons)
    return None


def human_sides(rows: Iterable | None) -> tuple[dict[str, Side], dict]:
    """최종 세트 → chat_id 별 Side. 반응만, effective=false·conflict=true 는 뺀다. 조정 값이 원 라벨보다 앞선다.
    행 모양은 as_final_row 가 받는 셋 중 하나."""
    out: dict[str, Side] = {}
    src: dict[str, str] = {}
    st = {"rows": 0, "skipped": 0, "duplicates": 0}
    for raw in rows or []:
        row = as_final_row(raw)
        if not isinstance(row, Mapping) or row.get("label_kind") != "reaction":
            continue
        if row.get("effective") is False or row.get("conflict") is True:
            st["skipped"] += 1
            continue
        cid = row.get("chat_id")
        if not isinstance(cid, str) or not cid:
            st["skipped"] += 1
            continue
        st["rows"] += 1
        source = row.get("source")
        if cid in out:
            st["duplicates"] += 1
            if not (source == "adjudication" and src.get(cid) != "adjudication"):
                continue
        ev_ = row.get("eval") if isinstance(row.get("eval"), Mapping) else {}
        rt = row.get("reaction_time_ms")
        out[cid] = Side(
            present=True, value=_value(row.get("result"), row.get("grade")),
            reaction_time_ms=rt if _is_int(rt) else None,
            confidence=row.get("confidence") if row.get("confidence") in CONF_KO else None,
            evidence=_human_evidence(row),
            rationale=row.get("rationale") if isinstance(row.get("rationale"), str) else None,
            t_ms=row.get("chat_t_ms") if _is_int(row.get("chat_t_ms")) else None,
            extra={"source": source if isinstance(source, str) else None,
                   "strict": ev_.get("strict") if isinstance(ev_.get("strict"), bool) else None,
                   "lenient": ev_.get("lenient") if isinstance(ev_.get("lenient"), bool) else None,
                   "excluded_reasons": [x for x in ev_.get("excluded_reasons") or [] if isinstance(x, str)]},
        )
        src[cid] = source if isinstance(source, str) else ""
    return out, st


def classify(engine: Side, human: Side) -> str | None:
    """두 판정의 비교 결과. 한쪽에만 있고 그쪽도 반응 미확인이면 None(목록에 넣지 않음)."""
    if engine.present and human.present:
        if engine.value == human.value:
            return SAME
        if engine.graded and human.graded:
            return GRADE_DIFF
        return ENGINE_ONLY if engine.graded else HUMAN_ONLY
    if engine.graded:
        return ENGINE_ONLY
    if human.graded:
        return HUMAN_ONLY
    return None


@dataclass
class Comparison:
    items: list[CompareItem]
    summary: dict
    confusion: dict[str, dict[str, int]]   # 엔진 값 → 사람 값 → 건수 (CONFUSION_VALUES, absent 끼리는 없음)


def compare(run: Mapping | None, final_rows: Iterable[Mapping] | None,
            chat_times: Mapping[str, int] | None = None) -> Comparison:
    """엔진 결과와 최종 세트를 chat_id 로 맞춘다. 목록은 시각 순(시각 없는 것은 뒤, chat_id 순)."""
    eng, est = engine_sides(run)
    hum, hst = human_sides(final_rows)
    absent = Side()
    items: list[CompareItem] = []
    conf = {a: {b: 0 for b in CONFUSION_VALUES} for a in CONFUSION_VALUES}
    counts = {s: 0 for s in STATUSES}
    unpaired = 0
    no_grade = 0
    for cid in set(eng) | set(hum):
        e, h = eng.get(cid, absent), hum.get(cid, absent)
        if GRADED_NO_GRADE in (e.value, h.value):
            no_grade += 1                  # 혼동표에 넣지 않고 따로 센다(가져오기 검사가 막아야 하는 조합)
        else:
            conf[e.value][h.value] += 1
        status = classify(e, h)
        if status is None:
            unpaired += 1
            continue
        counts[status] += 1
        t = None
        if chat_times is not None and _is_int(chat_times.get(cid)):
            t = chat_times[cid]
        elif h.t_ms is not None:
            t = h.t_ms
        elif e.t_ms is not None:
            t = e.t_ms
        items.append(CompareItem(cid, t, status, e, h))
    items.sort(key=lambda it: (it.t_ms is None, it.t_ms if it.t_ms is not None else 0, it.chat_id))
    both = sum(1 for c in eng if c in hum)
    summary = {
        "engine_rows": len(eng), "human_rows": len(hum), "both": both,
        "engine_unevaluated": sum(1 for c in hum if c not in eng),   # 사람에게만 있음 (AI 미평가)
        "human_missing": sum(1 for c in eng if c not in hum),        # 엔진에게만 있음 (최종 세트 밖)
        "unpaired_no_reaction": unpaired, "graded_no_grade": no_grade,
        "engine_duplicates": est["duplicates"], "engine_invalid": est["invalid"],
        "human_duplicates": hst["duplicates"], "human_skipped": hst["skipped"],
        "no_time": sum(1 for it in items if it.t_ms is None),
        **counts,
    }
    return Comparison(items, summary, conf)


def filter_items(items: Sequence[CompareItem], mode: str) -> list[CompareItem]:
    if mode == FILTER_DIFF:
        return [it for it in items if it.status != SAME]
    if mode == FILTER_MISSED:
        return [it for it in items if it.status == HUMAN_ONLY]
    return list(items)


def next_diff_index(items: Sequence[CompareItem], current: int | None, step: int = 1) -> int | None:
    """current 다음(step=-1 이면 앞)의 '같음 아닌' 항목 자리. 없으면 None(돌아가지 않는다)."""
    n = len(items)
    if n == 0:
        return None
    i = (-1 if step > 0 else n) if current is None else current
    i += 1 if step > 0 else -1
    while 0 <= i < n:
        if items[i].status != SAME:
            return i
        i += 1 if step > 0 else -1
    return None


def evidence_rows(item: CompareItem) -> list[tuple[str, str, str]]:
    """증거 7종: (표시명, 엔진, 사람). 엔진 = 연결 후보의 present(at_ms), 사람 = 연결 발화의 강·중·약."""
    out = []
    for k in EVIDENCE_KEYS:
        name = f"{EVIDENCE_KO[k]} ({STRENGTH_KO.get(V0_1_STRENGTH[k], '')})"
        ee = item.engine.evidence
        if not item.engine.present:
            et = "—"
        elif ee is None:
            et = tr("증거 없음")
        else:
            e = ee.get(k) or {}
            p = e.get("present")
            et = (tr("있음") + (f" {_t(e.get('at_ms'))}" if _is_int(e.get("at_ms")) else "")) if p is True \
                else tr("없음") if p is False else tr("판단 불가")
        he = item.human.evidence
        if not item.human.present:
            ht = "—"
        elif he is None:
            ht = tr("증거 없음")
        else:
            s = he.get(k)
            ht = f"{tr('있음')}({STRENGTH_KO.get(s, s)})" if s else tr("없음")
        out.append((name, et, ht))
    return out


def _fmt_score(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return tr("예") if v else tr("아니오")
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return "—" if v != v else f"{v:.3f}"
    if v == "held":
        return tr("보류")
    return str(v)


SCORE_KO = {
    "n": "건수", "exact": "정확 일치", "exact_agreement": "정확 일치", "within_one": "한 단계 어긋남까지",
    "kappa_w": "κ_w", "kappa": "κ", "ac1": "AC1", "precision": "정밀도", "recall": "재현율", "f1": "F1",
    "dt_within_2s": "반응 시각 ±2초", "dt_within_5s": "반응 시각 ±5초",
}


def score_rows(scores: Mapping | None) -> list[tuple[str, str, str]]:
    """ai_compare 결과 → (지표, 엄격, 참고). strict·lenient 가 dict 이면 그 키의 합집합을, 아니면 빈 목록."""
    if not isinstance(scores, Mapping):
        return []
    s, l_ = scores.get("strict"), scores.get("lenient")
    s = s if isinstance(s, Mapping) else {}
    l_ = l_ if isinstance(l_, Mapping) else {}
    keys = list(s) + [k for k in l_ if k not in s]
    rows = []
    for k in keys:
        if isinstance(s.get(k), Mapping) or isinstance(l_.get(k), Mapping):
            continue                       # 표 안의 표(혼동표 등)는 보고서에서 본다
        rows.append((SCORE_KO.get(k, str(k)), _fmt_score(s.get(k)), _fmt_score(l_.get(k))))
    return rows


# ---------------------------------------------------------------------------- 화면
class AnalysisView(QWidget):
    """나란히 보기. 해제 전(run=None)이면 '봉인됨 n건' 만 보인다."""
    chatSelected = Signal(str)       # 고른 chat_id
    seekRequested = Signal(int)      # 고른 채팅의 VOD 시각 (플레이어를 옮길 때)

    def __init__(self, parent=None, *, run: Mapping | None = None, final_rows: Iterable[Mapping] | None = None,
                 sealed_count: int = 0, scores: Mapping | None = None, chat_times: Mapping[str, int] | None = None,
                 duration_ms: int | None = None):
        super().__init__(parent)
        self._cmp = Comparison([], {}, {})
        self._visible: list[CompareItem] = []
        self._filter = FILTER_ALL
        self._current: CompareItem | None = None
        self._sealed = True
        self._sealed_count = 0
        self._build()
        self._install_shortcuts()
        self.set_data(run=run, final_rows=final_rows, sealed_count=sealed_count, scores=scores,
                      chat_times=chat_times, duration_ms=duration_ms)

    # ------------------------------------------------------------------ 만들기
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.stack = QStackedWidget(self)
        lay.addWidget(self.stack)

        # 빈 상태 (봉인됨)
        self.sealed_page = QWidget()
        sl = QVBoxLayout(self.sealed_page)
        sl.addStretch(1)
        self.sealed_label = QLabel()
        self.sealed_label.setAlignment(Qt.AlignCenter)
        self.sealed_label.setWordWrap(True)
        sl.addWidget(self.sealed_label)
        self.sealed_hint = QLabel(tr("계획된 재라벨까지 모두 잠기고 재현 검증이 통과하면 봉인을 풀 수 있습니다. "
                                     "풀기 전에는 엔진 판정을 보이지 않습니다."))
        self.sealed_hint.setAlignment(Qt.AlignCenter)
        self.sealed_hint.setWordWrap(True)
        sl.addWidget(self.sealed_hint)
        sl.addStretch(2)
        self.stack.addWidget(self.sealed_page)

        # 내용
        self.content = QWidget()
        cl = QVBoxLayout(self.content)
        cl.setContentsMargins(0, 0, 0, 0)
        self.header = QLabel()
        self.header.setWordWrap(True)
        cl.addWidget(self.header)
        self.timeline = Timeline(self.content)
        for s, c in STATUS_COLORS.items():
            self.timeline.set_marker_style(f"an_{s}", c, False)
            self.timeline.set_marker_style(f"an_{s}_n", c, True)
        self.timeline.set_fixed_tracks_visible(segments=False, density=False, waveform=False)   # 마커 두 줄만 쓴다
        self.timeline.seekRequested.connect(self._on_timeline_seek)
        cl.addWidget(self.timeline)
        legend = QLabel("  ".join(f"<span style='color:{STATUS_COLORS[s].name()}'>●</span> {tr(STATUS_KO[s])}"
                                  for s in STATUSES) + "  " + tr("○ = 그쪽 판정이 반응 미확인"))
        legend.setTextFormat(Qt.RichText)
        cl.addWidget(legend)

        split = QSplitter(Qt.Horizontal, self.content)
        cl.addWidget(split, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        fl = QHBoxLayout()
        fl.addWidget(QLabel(tr("필터")))
        self.filter_box = QComboBox()
        for f in FILTERS:
            self.filter_box.addItem(tr(FILTER_KO[f]), f)
        self.filter_box.currentIndexChanged.connect(lambda _i: self.set_filter(self.filter_box.currentData()))
        fl.addWidget(self.filter_box, 1)
        ll.addLayout(fl)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        ll.addWidget(self.list, 1)
        nl = QHBoxLayout()
        self.prev_btn = QPushButton(tr("이전 다른 곳 (Shift+N)"))
        self.prev_btn.clicked.connect(lambda _=False: self.prev_diff())
        self.next_btn = QPushButton(tr("다음 다른 곳 (N)"))
        self.next_btn.clicked.connect(lambda _=False: self.next_diff())
        nl.addWidget(self.prev_btn)
        nl.addWidget(self.next_btn)
        ll.addLayout(nl)
        self.nav_msg = QLabel()
        ll.addWidget(self.nav_msg)
        split.addWidget(left)

        right_inner = QWidget()
        rl = QVBoxLayout(right_inner)
        rl.setContentsMargins(4, 0, 4, 0)
        self.detail_title = QLabel()
        self.detail_title.setWordWrap(True)
        rl.addWidget(self.detail_title)
        self.detail = QGridLayout()
        self.detail.setHorizontalSpacing(12)
        rl.addLayout(self.detail)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        rl.addWidget(line)
        rl.addWidget(QLabel(tr("합계")))
        self.summary_table = QTableWidget(0, 2)
        self.summary_table.setHorizontalHeaderLabels([tr("항목"), tr("건수")])
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setEditTriggers(QTableWidget.NoEditTriggers)
        rl.addWidget(self.summary_table)
        rl.addWidget(QLabel(tr("엔진(줄) × 사람 최종(칸) 등급")))
        self.confusion_table = QTableWidget(len(CONFUSION_VALUES), len(CONFUSION_VALUES))
        names = [tr(VAL_KO[v]) for v in CONFUSION_VALUES]
        self.confusion_table.setHorizontalHeaderLabels(names)
        self.confusion_table.setVerticalHeaderLabels(names)
        self.confusion_table.setEditTriggers(QTableWidget.NoEditTriggers)
        rl.addWidget(self.confusion_table)
        rl.addWidget(QLabel(tr("점수 (ai_compare 결과)")))
        self.score_note = QLabel()
        self.score_note.setWordWrap(True)
        rl.addWidget(self.score_note)
        self.score_table = QTableWidget(0, 3)
        self.score_table.setHorizontalHeaderLabels([tr("지표"), tr("엄격"), tr("참고")])
        self.score_table.verticalHeader().setVisible(False)
        self.score_table.setEditTriggers(QTableWidget.NoEditTriggers)
        rl.addWidget(self.score_table)
        rl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(right_inner)
        split.addWidget(scroll)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        self.stack.addWidget(self.content)

    def _install_shortcuts(self) -> None:
        """N / Shift+N. 작업대·큐·출처 화면이 쓰지 않는 키다. 이 화면 안에 초점이 있을 때만 동작한다."""
        self.shortcuts: dict[str, QShortcut] = {}
        for key, fn in (("N", self.next_diff), ("Shift+N", self.prev_diff)):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
            self.shortcuts[key] = s

    # ------------------------------------------------------------------ 자료
    def set_data(self, *, run: Mapping | None = None, final_rows: Iterable[Mapping] | None = None,
                 sealed_count: int = 0, scores: Mapping | None = None, chat_times: Mapping[str, int] | None = None,
                 duration_ms: int | None = None) -> None:
        """자료를 바꾼다. run 이 None 이면 봉인 상태 — 최종 세트도 계산하지 않고 '봉인됨 n건' 만 보인다."""
        self._current = None
        self._sealed_count = max(0, int(sealed_count)) if _is_int(sealed_count) else 0
        if run is None:
            self._sealed = True
            self._cmp = Comparison([], {}, {})
            self._visible = []
            self._clear_content()
            self.sealed_label.setText(tr("봉인됨 {n}건").format(n=self._sealed_count))
            self.stack.setCurrentWidget(self.sealed_page)
            LOG.info("분석 나란히 보기: 봉인 %d건 (열지 않음)", self._sealed_count)
            return
        self._sealed = False
        self._cmp = compare(run, final_rows, chat_times)
        eng = run.get("engine") if isinstance(run.get("engine"), Mapping) else {}
        rid = run.get("run_id") if isinstance(run.get("run_id"), str) else "?"
        self.header.setText(tr("엔진 실행 {rid} · {name} {ver} · 사람 = 최종 세트").format(
            rid=rid, name=eng.get("name", "?"), ver=eng.get("version", "?")))
        times = [it.t_ms for it in self._cmp.items if it.t_ms is not None]
        dur = duration_ms if _is_int(duration_ms) and duration_ms > 0 else (max(times, default=0) + 10_000)
        self.timeline.set_duration(dur)
        self._fill_summary()
        self._fill_scores(scores)
        self._apply_filter()
        self.stack.setCurrentWidget(self.content)
        s = self._cmp.summary
        LOG.info("분석 나란히 보기: 엔진 %d · 사람 %d · 같음 %d · 등급 다름 %d · 엔진만 %d · 사람만 %d",
                 s["engine_rows"], s["human_rows"], s[SAME], s[GRADE_DIFF], s[ENGINE_ONLY], s[HUMAN_ONLY])

    def _clear_content(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.list.blockSignals(False)
        self.header.clear()
        self.timeline.clear_markers(ENGINE_TRACK)
        self.timeline.clear_markers(HUMAN_TRACK)
        self.summary_table.setRowCount(0)
        for r in range(self.confusion_table.rowCount()):
            for c in range(self.confusion_table.columnCount()):
                self.confusion_table.setItem(r, c, QTableWidgetItem(""))
        self.score_table.setRowCount(0)
        self.score_note.clear()
        self._clear_detail()
        self.nav_msg.clear()

    # ------------------------------------------------------------------ 상태 (시험·통합용)
    def is_sealed(self) -> bool:
        return self._sealed

    def sealed_count(self) -> int:
        return self._sealed_count

    def items(self) -> list[CompareItem]:
        return list(self._cmp.items)

    def visible_items(self) -> list[CompareItem]:
        return list(self._visible)

    def summary(self) -> dict:
        return dict(self._cmp.summary)

    def confusion(self) -> dict[str, dict[str, int]]:
        return {a: dict(b) for a, b in self._cmp.confusion.items()}

    def current_item(self) -> CompareItem | None:
        return self._current

    def filter_mode(self) -> str:
        return self._filter

    # ------------------------------------------------------------------ 필터·목록
    def set_filter(self, mode: str) -> None:
        if mode not in FILTERS:
            raise ValueError(f"모르는 필터 {mode!r}")
        self._filter = mode
        i = self.filter_box.findData(mode)
        if i >= 0 and self.filter_box.currentIndex() != i:
            self.filter_box.blockSignals(True)
            self.filter_box.setCurrentIndex(i)
            self.filter_box.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self) -> None:
        if self._sealed:
            return
        keep = self._current
        self._visible = filter_items(self._cmp.items, self._filter)
        self.list.blockSignals(True)
        self.list.clear()
        for it in self._visible:
            text = f"{_t(it.t_ms)}  {tr('엔진')} {tr(VAL_KO.get(it.engine.value, it.engine.value))} · " \
                   f"{tr('사람')} {tr(VAL_KO.get(it.human.value, it.human.value))}  [{tr(STATUS_KO[it.status])}]"
            li = QListWidgetItem(text)
            li.setData(Qt.UserRole, it.chat_id)
            li.setForeground(STATUS_COLORS[it.status])
            self.list.addItem(li)
        self.list.blockSignals(False)
        self._fill_timeline()
        self._current = None
        if keep is not None and keep in self._visible:
            self.select_index(self._visible.index(keep))
        else:
            self._clear_detail()
        self.nav_msg.setText(tr("{n}건 보임").format(n=len(self._visible)))

    def _fill_timeline(self) -> None:
        e_marks, h_marks = [], []
        for it in self._visible:
            if it.t_ms is None:
                continue
            if it.engine.present:
                e_marks.append((it.t_ms, f"an_{it.status}" + ("" if it.engine.graded else "_n")))
            if it.human.present:
                h_marks.append((it.t_ms, f"an_{it.status}" + ("" if it.human.graded else "_n")))
        self.timeline.set_markers(ENGINE_TRACK, e_marks, tr("엔진"))
        self.timeline.set_markers(HUMAN_TRACK, h_marks, tr("사람 최종"))

    # ------------------------------------------------------------------ 고르기·건너뛰기
    def _on_row(self, row: int) -> None:
        if 0 <= row < len(self._visible):
            self._select(self._visible[row], from_list=True)

    def select_index(self, i: int) -> bool:
        if not 0 <= i < len(self._visible):
            return False
        self._select(self._visible[i])
        return True

    def select_chat(self, chat_id: str) -> bool:
        """보이는 목록에서 chat_id 를 고른다. 필터에 걸려 안 보이면 False."""
        for i, it in enumerate(self._visible):
            if it.chat_id == chat_id:
                return self.select_index(i)
        return False

    def _select(self, it: CompareItem, from_list: bool = False) -> None:
        self._current = it
        if not from_list:
            i = self._visible.index(it)
            self.list.blockSignals(True)
            self.list.setCurrentRow(i)
            self.list.blockSignals(False)
        self._fill_detail(it)
        if it.t_ms is not None:
            self.timeline.set_position(it.t_ms)
        self.chatSelected.emit(it.chat_id)
        if it.t_ms is not None:
            self.seekRequested.emit(it.t_ms)

    def _cur_index(self) -> int | None:
        if self._current is None or self._current not in self._visible:
            return None
        return self._visible.index(self._current)

    def next_diff(self) -> bool:
        return self._jump(1)

    def prev_diff(self) -> bool:
        return self._jump(-1)

    def _jump(self, step: int) -> bool:
        if self._sealed:
            return False
        j = next_diff_index(self._visible, self._cur_index(), step)
        if j is None:
            self.nav_msg.setText(tr("더 다른 곳이 없습니다") if step > 0 else tr("앞에 다른 곳이 없습니다"))
            return False
        self.select_index(j)
        self.nav_msg.setText(tr("{i} / {n}").format(i=j + 1, n=len(self._visible)))
        return True

    def _on_timeline_seek(self, t_ms: int) -> None:
        """타임라인을 누르면 가장 가까운 채팅(8px 안)을 고른다."""
        tol = self.timeline.px_tol_ms(8)
        best, bd = None, None
        for i, it in enumerate(self._visible):
            if it.t_ms is None:
                continue
            d = abs(it.t_ms - t_ms)
            if d <= tol and (bd is None or d < bd):
                best, bd = i, d
        if best is not None:
            self.select_index(best)

    # ------------------------------------------------------------------ 고른 건
    def _take_detail(self) -> None:
        """고른 건 칸의 글자를 바로 떼어 낸다(deleteLater 를 기다리면 봉인으로 바꾼 뒤에도 잠깐 자식으로 남는다)."""
        while self.detail.count():
            w = self.detail.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _clear_detail(self) -> None:
        self._take_detail()
        self.detail_title.setText(tr("목록이나 타임라인에서 채팅을 고르세요"))

    def detail_rows(self) -> list[tuple[str, str, str]]:
        """고른 건의 표 (항목, 엔진, 사람). 아무것도 고르지 않았으면 빈 목록."""
        it = self._current
        if it is None:
            return []
        e, h = it.engine, it.human
        rows = [
            (tr("판정"), tr(VAL_KO.get(e.value, e.value)) if e.present else tr("평가 안 함"),
             tr(VAL_KO.get(h.value, h.value)) if h.present else tr("최종 세트에 없음")),
            (tr("확신도"), tr(CONF_KO.get(e.confidence, "—")), tr(CONF_KO.get(h.confidence, "—"))),
            (tr("반응 시각"), _t(e.reaction_time_ms), _t(h.reaction_time_ms)),
        ]
        rows += evidence_rows(it)
        rows.append((tr("근거"), e.rationale or "—", h.rationale or "—"))
        if h.present:
            ex = h.extra
            ev = []
            if ex.get("source") == "adjudication":
                ev.append(tr("조정 값"))
            if ex.get("strict") is True:
                ev.append(tr("엄격 포함"))
            if ex.get("lenient") is True:
                ev.append(tr("참고 포함"))
            if ex.get("excluded_reasons"):
                ev.append(tr("제외: ") + ",".join(ex["excluded_reasons"]))
            sc = e.extra.get("score") if e.present else None
            rows.append((tr("평가"), _fmt_score(sc) if sc is not None else "—", " · ".join(ev) or "—"))
        return rows

    def _fill_detail(self, it: CompareItem) -> None:
        self._take_detail()
        self.detail_title.setText(f"{_t(it.t_ms)} · {it.chat_id} · {tr(STATUS_KO[it.status])}")
        for c, text in enumerate(("", tr("엔진"), tr("사람 최종"))):
            lab = QLabel(f"<b>{text}</b>")
            self.detail.addWidget(lab, 0, c)
        for r, (name, et, ht) in enumerate(self.detail_rows(), 1):
            for c, text in enumerate((name, et, ht)):
                lab = QLabel(text)
                lab.setTextFormat(Qt.PlainText)
                lab.setWordWrap(True)
                lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
                if r == 1 and c > 0:          # 판정 줄은 비교 결과 색
                    lab.setStyleSheet(f"color: {STATUS_COLORS[it.status].name()};")
                self.detail.addWidget(lab, r, c)

    # ------------------------------------------------------------------ 합계·점수
    def summary_rows(self) -> list[tuple[str, int]]:
        s = self._cmp.summary
        if not s:
            return []
        return [
            (tr("엔진 판정 채팅"), s["engine_rows"]), (tr("사람 최종 채팅"), s["human_rows"]), (tr("두 쪽 모두"), s["both"]),
            (tr(STATUS_KO[SAME]), s[SAME]), (tr(STATUS_KO[GRADE_DIFF]), s[GRADE_DIFF]),
            (tr(STATUS_KO[ENGINE_ONLY]), s[ENGINE_ONLY]), (tr(STATUS_KO[HUMAN_ONLY]), s[HUMAN_ONLY]),
            (tr("엔진 미평가 (사람에게만)"), s["engine_unevaluated"]), (tr("최종 세트 밖 (엔진에게만)"), s["human_missing"]),
            (tr("짝 없음 (반응 없음)"), s["unpaired_no_reaction"]), (tr("시각 없음"), s["no_time"]),
            (tr("등급 없는 graded (혼동표 밖)"), s["graded_no_grade"]),
            (tr("엔진 중복 chat_id"), s["engine_duplicates"]), (tr("사람 중복 chat_id"), s["human_duplicates"]),
        ]

    def _fill_summary(self) -> None:
        rows = self.summary_rows()
        self.summary_table.setRowCount(len(rows))
        for r, (name, n) in enumerate(rows):
            self.summary_table.setItem(r, 0, QTableWidgetItem(name))
            it = QTableWidgetItem(str(n))
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.summary_table.setItem(r, 1, it)
        conf = self._cmp.confusion
        for r, a in enumerate(CONFUSION_VALUES):
            for c, b in enumerate(CONFUSION_VALUES):
                n = conf.get(a, {}).get(b, 0) if not (a == ABSENT and b == ABSENT) else None
                it = QTableWidgetItem("" if n is None else str(n))
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.confusion_table.setItem(r, c, it)

    def _fill_scores(self, scores: Mapping | None) -> None:
        rows = score_rows(scores)
        self.score_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.score_table.setItem(r, c, QTableWidgetItem(text))
        if not isinstance(scores, Mapping):
            self.score_note.setText(tr("AI 비교 보고서를 아직 만들지 않았습니다. 만들면 엄격·참고 점수가 여기에 보입니다."))
        else:
            notes = []
            if scores.get("dev_only") is True or scores.get("preregistered") is False:
                notes.append(tr("사전 봉인 아님 — 개발용 점수"))
            for w in scores.get("warnings") or []:
                if isinstance(w, str):
                    notes.append(w)
            self.score_note.setText(" · ".join(notes))

    def score_table_rows(self) -> list[tuple[str, str, str]]:
        out = []
        for r in range(self.score_table.rowCount()):
            out.append(tuple(self.score_table.item(r, c).text() for c in range(3)))
        return out

    def all_texts(self) -> list[str]:
        """화면의 모든 글자(시험용: 봉인 중 새는 것이 없는지 본다)."""
        out = []
        for w in self.findChildren(QLabel):
            out.append(w.text())
        for i in range(self.list.count()):
            out.append(self.list.item(i).text())
        for t in (self.summary_table, self.confusion_table, self.score_table):
            for r in range(t.rowCount()):
                for c in range(t.columnCount()):
                    it = t.item(r, c)
                    if it is not None:
                        out.append(it.text())
        return out
