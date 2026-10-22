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
- 맨 위(해제된 run 이 둘 이상일 때만, 17 6-1 4번): '엔진 판' 고르기와 '견줄 것'(사람 최종 세트 · 다른 판).
  판 A 기본은 **엔진 판이 가장 높은 run**이다(18 5-2 의 7번, 21 4-2 의 15번, 22 5-2 의 12번 — default_run_id). 판은
  고르기 목록 항목의 engine_version(seal.jsonl 의 ai.unsealed 기록에서 온 값)만 본다. 판을 아는 run 이 없으면 지금처럼
  가장 늦게 푼 run(목록 맨 앞)이고, 판을 모르는 run 은 고르기 글에 '판 모름' 을 붙인다(한 번 읽은 run 은 결과 파일의
  엔진 이름·판).
- 창 제목 (23 5-2 의 10): '<기본 제목> — run <판 A> · 판 <engine_version>'(해제 기록의 판, 없으면 '판 모름'), 판끼리
  견주면 '… ↔ run <판 B> · 판 …', 해제 전이면 '<기본 제목> — 봉인됨 n건'(D-18 3). window_title() 이 글을 만들고,
  이 화면을 담은 창이 대화상자(QDialog)이면 판이 바뀔 때마다 그 창 제목을 바꾼다(titleChanged 도 보낸다). 기본 제목은
  title_base 인자, 없으면 그 창의 지금 제목에서 ' — ' 앞부분(메인 창이 넣은 '엔진과 나란히 보기').
  다른 판을 고르면 판끼리 견준다 — 위 트랙 = 판 A, 아래 = 판 B, 필터는 '다름만'(같은 채팅에 다른 등급 · 한쪽만 반응)으로
  바뀌고, 결과 이름은 A만·B만, 고른 건 칸에는 사람 최종을 참고 열로 더한다. 점수 표는 두 판의 점수(각각 사람과 견준 것)를
  나란히 둔다. 다른 판은 받은 run_loader 로만 읽는다(이 파일은 디스크를 열지 않는다). 다른 곳 규칙과 대상 영상 검사는
  app.ai_compare.diff_results 와 같다(graded 인데 등급 없는 줄은 뺀다, 08 D-17 1).

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
from typing import Any, Callable, Iterable, Mapping, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import ai_compare
from .. import analysis_flow as af
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
    """한 채팅의 비교. 판끼리 견줄 때(17 6-1 4번)는 engine = 판 A(위), human = 판 B(아래), ref = 사람 최종(참고)."""
    chat_id: str
    t_ms: int | None
    status: str
    engine: Side
    human: Side
    ref: Side | None = None


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
            chat_times: Mapping[str, int] | None = None, *, against: Mapping | None = None) -> Comparison:
    """엔진 결과와 최종 세트를 chat_id 로 맞춘다. 목록은 시각 순(시각 없는 것은 뒤, chat_id 순).

    against: 다른 엔진 판(봉인 해제된 결과 dict). 주면 판끼리 견준다(17 6-1 4번) — 위(engine) = run, 아래(human) = against,
    ref = 사람 최종(참고). 이때 graded 인데 등급이 없는 줄은 양쪽 모두 뺀다(08 D-17 1, ai_compare.diff_results 와 같은 규칙).
    summary 의 키는 같고 뜻만 바뀐다(engine_rows = A, human_rows = B, engine_unevaluated = B 에만, human_missing = A 에만)."""
    eng, est = engine_sides(run)
    ref: dict[str, Side] | None = None
    if against is not None:
        hum, hst = engine_sides(against)
        eng = {k: v for k, v in eng.items() if v.value != GRADED_NO_GRADE}
        hum = {k: v for k, v in hum.items() if v.value != GRADED_NO_GRADE}
        hst = {"duplicates": hst["duplicates"], "skipped": hst["invalid"]}
        ref, _rst = human_sides(final_rows)
    else:
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
        elif ref is not None and cid in ref and ref[cid].t_ms is not None:
            t = ref[cid].t_ms
        items.append(CompareItem(cid, t, status, e, h, None if ref is None else ref.get(cid, absent)))
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
    if ref is not None:
        # 다른 곳 가운데 어느 판이 사람 최종과 같은가 (참고 — 점수는 ai_compare 가 판마다 낸다)
        rc = {"ref_a": 0, "ref_b": 0, "ref_neither": 0, "ref_none": 0}
        for it in items:
            if it.status == SAME:
                continue
            r = it.ref
            if r is None or not r.present or r.value == GRADED_NO_GRADE:
                rc["ref_none"] += 1
            elif r.value == it.engine.value:
                rc["ref_a"] += 1
            elif r.value == it.human.value:
                rc["ref_b"] += 1
            else:
                rc["ref_neither"] += 1
        summary.update(rc, mode="runs")
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


def _ev_name(k: str) -> str:
    return f"{EVIDENCE_KO[k]} ({STRENGTH_KO.get(V0_1_STRENGTH[k], '')})"


def _engine_ev_text(side: Side, k: str) -> str:
    """엔진 쪽 증거 한 칸: 연결 후보의 present(at_ms)."""
    if not side.present:
        return "—"
    if side.evidence is None:
        return tr("증거 없음")
    e = side.evidence.get(k) or {}
    p = e.get("present")
    if p is True:
        return tr("있음") + (f" {_t(e.get('at_ms'))}" if _is_int(e.get("at_ms")) else "")
    return tr("없음") if p is False else tr("판단 불가")


def _human_ev_text(side: Side | None, k: str) -> str:
    """사람 쪽 증거 한 칸: 연결 발화의 강·중·약."""
    if side is None or not side.present:
        return "—"
    if side.evidence is None:
        return tr("증거 없음")
    s = side.evidence.get(k)
    return f"{tr('있음')}({STRENGTH_KO.get(s, s)})" if s else tr("없음")


def evidence_rows(item: CompareItem) -> list[tuple[str, str, str]]:
    """증거 7종: (표시명, 엔진, 사람). 엔진 = 연결 후보의 present(at_ms), 사람 = 연결 발화의 강·중·약."""
    return [(_ev_name(k), _engine_ev_text(item.engine, k), _human_ev_text(item.human, k)) for k in EVIDENCE_KEYS]


def evidence_rows_runs(item: CompareItem) -> list[tuple[str, str, str, str]]:
    """판끼리 견줄 때 증거 7종: (표시명, 판 A, 판 B, 사람 최종)."""
    return [(_ev_name(k), _engine_ev_text(item.engine, k), _engine_ev_text(item.human, k), _human_ev_text(item.ref, k))
            for k in EVIDENCE_KEYS]


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


def score_rows_pair(scores_a: Mapping | None, scores_b: Mapping | None) -> list[tuple[str, str, str, str, str]]:
    """판끼리 견줄 때 점수 표: (지표, A 엄격, A 참고, B 엄격, B 참고). 지표 순서는 A 먼저, B 에만 있는 것은 뒤."""
    ra = {r[0]: r for r in score_rows(scores_a)}
    rb = {r[0]: r for r in score_rows(scores_b)}
    keys = list(ra) + [k for k in rb if k not in ra]
    out = []
    for k in keys:
        a, b = ra.get(k, (k, "—", "—")), rb.get(k, (k, "—", "—"))
        out.append((k, a[1], a[2], b[1], b[2]))
    return out


def _run_entries(runs) -> list[tuple[str, str]]:
    """고르기 목록 → [(run_id, 표시 글)]. str · {run_id, label} · .run_id/.label 객체를 받는다. 순서는 받은 대로(최신 먼저)."""
    out: list[tuple[str, str]] = []
    for r in runs or ():
        if isinstance(r, str):
            rid, label = r, r
        elif isinstance(r, Mapping):
            rid, label = r.get("run_id"), r.get("label") or r.get("run_id")
        else:
            rid, label = getattr(r, "run_id", None), getattr(r, "label", None) or getattr(r, "run_id", None)
        if isinstance(rid, str) and rid and rid not in (x[0] for x in out):
            out.append((rid, label if isinstance(label, str) else rid))
    return out


ENGINE_VERSION_MAX = af.ENGINE_VERSION_MAX     # ai-result 스키마 engine.version 의 maxLength 와 같다


def _choice_version(r) -> str | None:
    """고르기 항목의 engine_version(dict 키 또는 RunChoice 류의 속성). 없거나 이상하면 None(= 판 모름)."""
    v = r.get("engine_version") if isinstance(r, Mapping) else getattr(r, "engine_version", None)
    return af.clean_version(v)


def run_versions(runs) -> dict[str, str | None]:
    """고르기 목록 → {run_id: 엔진 판 또는 None}. 같은 run_id 는 첫 항목(_run_entries 와 같은 규칙)."""
    out: dict[str, str | None] = {}
    for r in runs or ():
        rid = r if isinstance(r, str) else r.get("run_id") if isinstance(r, Mapping) else getattr(r, "run_id", None)
        if isinstance(rid, str) and rid and rid not in out:
            out[rid] = None if isinstance(r, str) else _choice_version(r)
    return out


def version_key(ver: str | None) -> tuple[int, ...] | None:
    """엔진 판 글 → 견줄 숫자 묶음(app.analysis_flow.version_key 와 같다 — 나란히 보기 자료·주제 흐름·시청자 훈련이
    같은 규칙으로 기본 run 을 고른다). 'link-v0.3' → (0, 3), '0.3.0' → (0, 3). 숫자가 없으면 None(판 모름과 같다)."""
    return af.version_key(ver)


def default_run_id(runs, versions: Mapping[str, str | None] | None = None) -> str | None:
    """판 고르기 A 기본 (18 5-2 의 7번, 21 4-2 의 15번): 판을 아는 run 가운데 **가장 높은 판**, 같은 판이면 목록에서 앞
    (늦게 푼) 것. 판을 아는 run 이 없으면 목록 맨 앞(가장 늦게 푼 것, 앞 판과 같은 동작). 목록이 비면 None.

    runs: _run_entries 의 [(run_id, 글)] 또는 고르기 목록 그대로. versions: {run_id: 판}(없으면 runs 에서 읽음)."""
    runs = list(runs or ())
    if runs and all(isinstance(r, tuple) for r in runs):
        ids = [r[0] for r in runs]
    else:
        ids = list(run_versions(runs))
    if not ids:
        return None
    vers = versions if versions is not None else run_versions(runs)
    best, best_key = None, None
    for rid in ids:
        k = version_key(vers.get(rid))
        if k is not None and (best_key is None or k > best_key):
            best, best_key = rid, k
    return best if best is not None else ids[0]


# ---------------------------------------------------------------------------- 화면
class AnalysisView(QWidget):
    """나란히 보기. 해제 전(run=None)이면 '봉인됨 n건' 만 보인다.

    해제된 run 이 여럿이면(runs 2개 이상 + run_loader) 맨 위에 '엔진 판' 과 '견줄 것' 고르기가 보인다(17 6-1 4번).
    열면 판이 가장 높은 run(판을 모르면 최신) ↔ 사람 최종이 바로 보이고, '견줄 것' 에서 다른 판을 고르면 두 판의 다른 곳만(같은 채팅에 다른 등급 ·
    한쪽만 반응) 보인다. run_loader(run_id) 는 부르는 쪽(app.analysis_flow.RunLoader)이 준다 — 이 화면은 파일을 열지 않는다."""
    chatSelected = Signal(str)       # 고른 chat_id
    seekRequested = Signal(int)      # 고른 채팅의 VOD 시각 (플레이어를 옮길 때)
    runChanged = Signal(str, str)    # (판 A run_id, 견줄 것: run_id 또는 '' = 사람 최종)
    titleChanged = Signal(str)       # 창 제목 글이 바뀜 (window_title())

    def __init__(self, parent=None, *, run: Mapping | None = None, final_rows: Iterable[Mapping] | None = None,
                 sealed_count: int = 0, scores: Mapping | None = None, chat_times: Mapping[str, int] | None = None,
                 duration_ms: int | None = None, runs: Sequence | None = None,
                 run_loader: Callable[[str], Mapping] | None = None, run_id: str | None = None,
                 pick_default: bool = True, title_base: str | None = None):
        super().__init__(parent)
        self._title_base: str | None = title_base if isinstance(title_base, str) and title_base else None
        self._title = ""
        self._cmp = Comparison([], {}, {})
        self._visible: list[CompareItem] = []
        self._filter = FILTER_ALL
        self._current: CompareItem | None = None
        self._sealed = True
        self._sealed_count = 0
        self._runs: list[tuple[str, str]] = []
        self._versions: dict[str, str | None] = {}  # run_id → 엔진 판(고르기 목록에서, 없으면 None = 판 모름)
        self._loader: Callable[[str], Mapping] | None = None
        self._loaded: dict[str, dict] = {}          # run_id → {run, scores}
        self._run_id: str | None = None             # 판 A (위)
        self._against: str | None = None            # 판 B (아래). None = 사람 최종
        self._final_rows: list = []
        self._chat_times: Mapping[str, int] | None = None
        self._duration: int | None = None
        self._build()
        self._install_shortcuts()
        self.set_data(run=run, final_rows=final_rows, sealed_count=sealed_count, scores=scores,
                      chat_times=chat_times, duration_ms=duration_ms, runs=runs, run_loader=run_loader, run_id=run_id,
                      pick_default=pick_default)

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
        # 판 고르기 (17 6-1 4번) — 해제된 run 이 둘 이상일 때만 보인다
        self.run_bar = QWidget()
        rb = QHBoxLayout(self.run_bar)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.addWidget(QLabel(tr("엔진 판")))
        self.run_box = QComboBox()
        self.run_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.run_box.setMinimumContentsLength(16)
        self.run_box.currentIndexChanged.connect(self._on_run_box)
        rb.addWidget(self.run_box, 1)
        rb.addWidget(QLabel(tr("견줄 것")))
        self.against_box = QComboBox()
        self.against_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.against_box.setMinimumContentsLength(16)
        self.against_box.currentIndexChanged.connect(self._on_against_box)
        rb.addWidget(self.against_box, 1)
        cl.addWidget(self.run_bar)
        self.run_msg = QLabel()
        self.run_msg.setWordWrap(True)
        self.run_msg.setVisible(False)
        cl.addWidget(self.run_msg)
        self.run_bar.setVisible(False)
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
        self.legend = QLabel()
        self.legend.setTextFormat(Qt.RichText)
        self._fill_legend()
        cl.addWidget(self.legend)

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
        # 연결은 묶인 메서드로만 한다. self 를 잡은 람다는 Qt 연결이 강하게 들고 있어 이 화면이 지워지지 않고
        # 프로세스 끝(QApplication 뒤)까지 남아 종료 때 접근 위반을 낸다(18 4-2, D-18 10)
        self.filter_box.currentIndexChanged.connect(self._on_filter_box)
        fl.addWidget(self.filter_box, 1)
        ll.addLayout(fl)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        ll.addWidget(self.list, 1)
        nl = QHBoxLayout()
        self.prev_btn = QPushButton(tr("이전 다른 곳 (Shift+N)"))
        self.prev_btn.clicked.connect(self._on_prev_clicked)
        self.next_btn = QPushButton(tr("다음 다른 곳 (N)"))
        self.next_btn.clicked.connect(self._on_next_clicked)
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
        self.confusion_title = QLabel(tr("엔진(줄) × 사람 최종(칸) 등급"))
        rl.addWidget(self.confusion_title)
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

    def _on_filter_box(self, _i: int) -> None:
        self.set_filter(self.filter_box.currentData())

    def _on_prev_clicked(self, _checked: bool = False) -> None:
        self.prev_diff()

    def _on_next_clicked(self, _checked: bool = False) -> None:
        self.next_diff()

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
                 duration_ms: int | None = None, runs: Sequence | None = None,
                 run_loader: Callable[[str], Mapping] | None = None, run_id: str | None = None,
                 pick_default: bool = True) -> None:
        """자료를 바꾼다. run 이 None 이면 봉인 상태 — 최종 세트도 계산하지 않고 '봉인됨 n건' 만 보인다.

        runs: 해제된 run 고르기 목록(최신 먼저. str · {run_id, label, engine_version} · RunChoice — engine_version
        속성이 있으면 읽는다). run_loader(run_id) → {run, scores} — 다른 판을 고를 때만 부른다. run_id: 넘긴 run 의
        ID(없으면 run['run_id']). pick_default: 고를 수 있으면(run 2개 이상 + run_loader) 판 A 를 default_run_id(판이
        가장 높은 run)로 연다 — 넘긴 run 과 다르면 run_loader 로 한 번 읽고, 못 읽으면 넘긴 run 으로 두고 까닭을 보인다.
        False 면 넘긴 run 이 판 A 다."""
        self._current = None
        self._sealed_count = max(0, int(sealed_count)) if _is_int(sealed_count) else 0
        self._loaded = {}
        self._against = None
        if run is None:
            self._sealed = True
            self._cmp = Comparison([], {}, {})
            self._visible = []
            self._runs, self._loader, self._run_id = [], None, None
            self._versions = {}
            self._final_rows, self._chat_times, self._duration = [], None, None
            self._clear_content()
            self._fill_run_bar()
            self.sealed_label.setText(tr("봉인됨 {n}건").format(n=self._sealed_count))
            self.stack.setCurrentWidget(self.sealed_page)
            self._sync_title()
            LOG.info("분석 나란히 보기: 봉인 %d건 (열지 않음)", self._sealed_count)
            return
        self._sealed = False
        self._final_rows = list(final_rows or [])
        self._chat_times = chat_times
        self._duration = duration_ms if _is_int(duration_ms) and duration_ms > 0 else None
        rid = run_id if isinstance(run_id, str) and run_id else run.get("run_id") if isinstance(run.get("run_id"), str) else "?"
        self._run_id = rid
        self._loaded[rid] = {"run": run, "scores": scores}
        self._runs = _run_entries(runs)
        self._versions = run_versions(runs)
        if self._runs and rid not in (r for r, _ in self._runs):
            self._runs.insert(0, (rid, rid))
        self._loader = run_loader
        if self._filter != FILTER_ALL and self._filter_auto:
            self._filter = FILTER_ALL
        self._filter_auto = False
        msg = ""
        if pick_default and self._can_pick():
            best = default_run_id(self._runs, self._versions)
            if best is not None and best != rid:
                if self._get(best) is not None:
                    self._run_id = best
                    LOG.info("나란히 보기: 판 A 기본 = 판이 가장 높은 run (넘긴 run 과 다름)")
                else:
                    msg = self.run_msg.text()           # _fill_run_bar 가 지우므로 다시 보인다
        self._fill_run_bar()
        if msg:
            self._show_run_msg(msg)
        self._render()
        self._sync_title()

    # ------------------------------------------------------------------ 판 고르기 (17 6-1 4번)
    _filter_auto = False      # 판끼리 견주기로 들어가며 '다름만' 으로 바꾼 것인가

    def is_runs_mode(self) -> bool:
        """판끼리 견주는 중인가 (아래 = 다른 엔진 판)."""
        return not self._sealed and self._against is not None

    def run_ids(self) -> list[str]:
        return [r for r, _ in self._runs]

    def current_run_id(self) -> str | None:
        return None if self._sealed else self._run_id

    def against_run_id(self) -> str | None:
        return self._against

    def _can_pick(self) -> bool:
        return not self._sealed and self._loader is not None and len(self._runs) >= 2

    def _fill_run_bar(self) -> None:
        for box in (self.run_box, self.against_box):
            box.blockSignals(True)
            box.clear()
        if self._can_pick():
            for rid, label in self._runs:
                self.run_box.addItem(self._run_label(rid, label), rid)
            i = self.run_box.findData(self._run_id)
            self.run_box.setCurrentIndex(max(0, i))
            self.against_box.addItem(tr("사람 최종 세트"), "")
            for rid, label in self._runs:
                if rid != self._run_id:
                    self.against_box.addItem(self._run_label(rid, label), rid)
            j = self.against_box.findData(self._against or "")
            self.against_box.setCurrentIndex(max(0, j))
        for box in (self.run_box, self.against_box):
            box.blockSignals(False)
        self.run_bar.setVisible(self._can_pick())
        self.run_msg.clear()
        self.run_msg.setVisible(False)

    def _run_label(self, rid: str, label: str) -> str:
        """고르기 글 (23 5-2 의 10). 해제 기록(ai.unsealed)의 engine_version 을 '판 …' 으로, 없으면 '판 모름' 을 붙인다
        — 판 A 기본(default_run_id)이 보는 값과 같다. 해제 기록에 판이 없고 한 번 읽은 판이면 결과 파일의 엔진 이름·판을
        참고로 뒤에 더 붙인다(기본 고르기에는 쓰지 않는 값)."""
        ver = self._versions.get(rid)
        if ver:
            return f"{label} · " + tr("판 {0}").format(ver)
        text = f"{label} · " + tr("판 모름")
        got = self._loaded.get(rid)
        run = got.get("run") if got else None
        eng = run.get("engine") if isinstance(run, Mapping) and isinstance(run.get("engine"), Mapping) else None
        if eng:
            text += f" · {eng.get('name', '?')} {eng.get('version', '?')}"
        return text

    def run_version(self, run_id: str) -> str | None:
        """고르기 목록이 알려 준 엔진 판(없으면 None = 판 모름)."""
        return self._versions.get(run_id)

    def _get(self, rid: str) -> dict | None:
        """판 하나를 읽는다(기억해 둔 것 먼저). 못 읽으면 None 과 함께 까닭을 보인다."""
        if rid in self._loaded:
            return self._loaded[rid]
        if self._loader is None or rid not in self.run_ids():
            self._show_run_msg(tr("이 판은 고를 수 없습니다"))
            return None
        try:
            got = self._loader(rid)
        except Exception as ex:  # noqa: BLE001 — 해제 기록·형식 문제는 까닭만 보이고 지금 화면을 둔다
            msg = str(ex) if not isinstance(ex, (OSError, KeyError, TypeError, AttributeError)) else type(ex).__name__
            self._show_run_msg(tr("판을 열지 못했습니다: {0}").format(msg))
            LOG.info("나란히 보기: run %s 읽기 실패 (%s)", rid, type(ex).__name__)
            return None
        run = got.get("run") if isinstance(got, Mapping) else None
        if not isinstance(run, Mapping):
            self._show_run_msg(tr("판을 열지 못했습니다: 결과가 없습니다"))
            return None
        self._loaded[rid] = {"run": run, "scores": got.get("scores")}
        return self._loaded[rid]

    def _show_run_msg(self, text: str) -> None:
        self.run_msg.setText(text)
        self.run_msg.setVisible(True)

    def select_run(self, run_id: str) -> bool:
        """판 A(위)를 바꾼다. 견주던 판과 같아지면 '사람 최종' 으로 돌아간다."""
        if self._sealed or run_id == self._run_id:
            return not self._sealed
        if self._get(run_id) is None:
            self._fill_run_bar_keep_msg()
            return False
        self._run_id = run_id
        if self._against == run_id:
            self._against = None
            if self._filter_auto:
                self._filter, self._filter_auto = FILTER_ALL, False
        self._fill_run_bar()
        self._render()
        self._sync_title()
        self.runChanged.emit(self._run_id, self._against or "")
        return True

    def compare_with(self, run_id: str | None) -> bool:
        """판 B(아래)를 고른다. None·'' = 사람 최종 세트. 다른 판이면 다른 곳만(필터 '다름만')으로 연다."""
        rid = run_id or None
        if self._sealed:
            return False
        if rid == self._against:
            return True
        if rid is not None:
            if rid == self._run_id or self._get(rid) is None:
                if rid == self._run_id:
                    self._show_run_msg(tr("같은 판끼리는 견주지 않습니다"))
                self._fill_run_bar_keep_msg()
                return False
        prev = (self._against, self._filter, self._filter_auto)
        self._against = rid
        if rid is not None:
            if prev[0] is None:
                self._filter, self._filter_auto = FILTER_DIFF, True
        elif self._filter_auto:
            self._filter, self._filter_auto = FILTER_ALL, False
        self._fill_run_bar()
        if not self._render():
            self._against, self._filter, self._filter_auto = prev
            self._fill_run_bar_keep_msg()
            self._render()
            return False
        self._sync_title()
        self.runChanged.emit(self._run_id or "", self._against or "")
        return True

    # ------------------------------------------------------------------ 창 제목 (23 5-2 의 10)
    def _title_version(self, rid: str | None) -> str:
        """'판 …'(해제 기록의 engine_version) 또는 '판 모름'."""
        ver = self._versions.get(rid) if rid else None
        return tr("판 {0}").format(ver) if ver else tr("판 모름")

    def window_title(self, base: str | None = None) -> str:
        """창 제목 글. base(없으면 title_base·담은 창의 지금 제목 앞부분, 그것도 없으면 '엔진과 나란히 보기') 뒤에
        해제 전이면 '봉인됨 n건', 아니면 'run <판 A> · 판 …'(판끼리 견주면 ' ↔ run <판 B> · 판 …')."""
        b = base or self._title_base or tr("엔진과 나란히 보기")
        if self._sealed:
            return f"{b} — " + tr("봉인됨 {n}건").format(n=self._sealed_count)
        head = f"{b} — run {self._run_id} · {self._title_version(self._run_id)}"
        if self._against:
            head += f" ↔ run {self._against} · {self._title_version(self._against)}"
        return head

    def _sync_title(self) -> None:
        """담은 창이 대화상자이면 그 제목을 window_title() 로 바꾼다(메인 창 같은 다른 창은 건드리지 않는다)."""
        win = self.window()
        host = win if win is not self and isinstance(win, QDialog) else None
        if self._title_base is None and host is not None:
            cur = host.windowTitle() or ""
            self._title_base = cur.split(" — ", 1)[0].strip() or None
        text = self.window_title()
        if host is not None and host.windowTitle() != text:
            host.setWindowTitle(text)
        if text != self._title:
            self._title = text
            self.titleChanged.emit(text)

    def _fill_run_bar_keep_msg(self) -> None:
        msg = self.run_msg.text()          # _fill_run_bar 가 지우므로 글이 있으면 보이던 것
        self._fill_run_bar()
        if msg:
            self._show_run_msg(msg)

    def _on_run_box(self, _i: int) -> None:
        rid = self.run_box.currentData()
        if isinstance(rid, str) and rid:
            self.select_run(rid)

    def _on_against_box(self, _i: int) -> None:
        rid = self.against_box.currentData()
        self.compare_with(rid if isinstance(rid, str) and rid else None)

    # ------------------------------------------------------------------ 그리기
    def _render(self) -> bool:
        """지금 고른 판(들)로 다시 그린다. 판끼리 견주기에 실패하면(대상 영상이 다름 등) False."""
        cur = self._loaded.get(self._run_id or "")
        if cur is None:
            return False
        run = cur["run"]
        self._current = None
        b = self._loaded.get(self._against) if self._against else None
        if self._against and b is None:
            return False
        if b is not None:
            try:
                # 대상 영상이 같은지·다른 곳 규칙을 한 곳(ai_compare.diff_results)에서 본다
                diff = ai_compare.diff_results(run, b["run"], human=self._final_rows, chat_times=self._chat_times)
            except ai_compare.AICompareError as ex:
                self._show_run_msg(tr("두 판을 견주지 못했습니다: {0}").format(str(ex)))
                return False
            self._cmp = compare(run, self._final_rows, self._chat_times, against=b["run"])
            self._cmp.summary.update(graded_no_grade_a=diff["a"]["graded_no_grade"],
                                     graded_no_grade_b=diff["b"]["graded_no_grade"])
            self.header.setText(tr("판 A = {a} ↔ 판 B = {b} · 사람 = 최종 세트(참고)").format(
                a=self._run_title(run), b=self._run_title(b["run"])))
        else:
            self._cmp = compare(run, self._final_rows, self._chat_times)
            eng = run.get("engine") if isinstance(run.get("engine"), Mapping) else {}
            rid = run.get("run_id") if isinstance(run.get("run_id"), str) else "?"
            self.header.setText(tr("엔진 실행 {rid} · {name} {ver} · 사람 = 최종 세트").format(
                rid=rid, name=eng.get("name", "?"), ver=eng.get("version", "?")))
        times = [it.t_ms for it in self._cmp.items if it.t_ms is not None]
        self.timeline.set_duration(self._duration or (max(times, default=0) + 10_000))
        self._fill_legend()
        self._fill_filter_labels()
        self._fill_summary()
        if b is not None:
            self._fill_scores_pair(cur.get("scores"), b.get("scores"))
        else:
            self._fill_scores(cur.get("scores"))
        self._apply_filter()
        self.stack.setCurrentWidget(self.content)
        s = self._cmp.summary
        LOG.info("분석 나란히 보기%s: 위 %d · 아래 %d · 같음 %d · 등급 다름 %d · 위만 %d · 아래만 %d",
                 " (판끼리)" if b is not None else "", s["engine_rows"], s["human_rows"], s[SAME], s[GRADE_DIFF],
                 s[ENGINE_ONLY], s[HUMAN_ONLY])
        return True

    @staticmethod
    def _run_title(run: Mapping) -> str:
        eng = run.get("engine") if isinstance(run.get("engine"), Mapping) else {}
        rid = run.get("run_id") if isinstance(run.get("run_id"), str) else "?"
        return f"{rid} ({eng.get('name', '?')} {eng.get('version', '?')})"

    def status_ko(self, status: str) -> str:
        """비교 결과 이름. 판끼리 견줄 때는 엔진만·사람만 대신 A만·B만."""
        if self.is_runs_mode():
            return tr({ENGINE_ONLY: "A만", HUMAN_ONLY: "B만"}.get(status, STATUS_KO.get(status, status)))
        return tr(STATUS_KO.get(status, status))

    def _side_names(self) -> tuple[str, str]:
        return (tr("판 A"), tr("판 B")) if self.is_runs_mode() else (tr("엔진"), tr("사람 최종"))

    def _fill_legend(self) -> None:
        self.legend.setText("  ".join(f"<span style='color:{STATUS_COLORS[s].name()}'>●</span> {self.status_ko(s)}"
                                      for s in STATUSES) + "  " + tr("○ = 그쪽 판정이 반응 미확인"))

    def _fill_filter_labels(self) -> None:
        names = dict(FILTER_KO)
        if self.is_runs_mode():
            names[FILTER_MISSED] = "B만"
        self.filter_box.blockSignals(True)
        for i in range(self.filter_box.count()):
            self.filter_box.setItemText(i, tr(names[self.filter_box.itemData(i)]))
        j = self.filter_box.findData(self._filter)
        if j >= 0:
            self.filter_box.setCurrentIndex(j)
        self.filter_box.blockSignals(False)
        a, b = self._side_names()
        self.confusion_title.setText(tr("{a}(줄) × {b}(칸) 등급").format(a=a, b=b))

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
        self._set_score_columns(False)
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
        self._filter_auto = False
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
        runs = self.is_runs_mode()
        a, b = (tr("A"), tr("B")) if runs else (tr("엔진"), tr("사람"))
        for it in self._visible:
            text = f"{_t(it.t_ms)}  {a} {tr(VAL_KO.get(it.engine.value, it.engine.value))} · " \
                   f"{b} {tr(VAL_KO.get(it.human.value, it.human.value))}  [{self.status_ko(it.status)}]"
            if runs and it.ref is not None and it.ref.present:
                text += f"  {tr('사람')} {tr(VAL_KO.get(it.ref.value, it.ref.value))}"
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
        a, b = self._side_names()
        self.timeline.set_markers(ENGINE_TRACK, e_marks, a)
        self.timeline.set_markers(HUMAN_TRACK, h_marks, b)

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

    def detail_rows(self) -> list[tuple[str, ...]]:
        """고른 건의 표 (항목, 엔진, 사람). 판끼리 견줄 때는 (항목, 판 A, 판 B, 사람 최종). 고르지 않았으면 빈 목록."""
        it = self._current
        if it is None:
            return []
        if self.is_runs_mode():
            return self._detail_rows_runs(it)
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

    @staticmethod
    def _detail_rows_runs(it: CompareItem) -> list[tuple[str, str, str, str]]:
        a, b, h = it.engine, it.human, it.ref if it.ref is not None else Side()

        def val(s: Side, missing: str) -> str:
            return tr(VAL_KO.get(s.value, s.value)) if s.present else tr(missing)
        rows = [
            (tr("판정"), val(a, "평가 안 함"), val(b, "평가 안 함"), val(h, "최종 세트에 없음")),
            (tr("확신도"), tr(CONF_KO.get(a.confidence, "—")), tr(CONF_KO.get(b.confidence, "—")),
             tr(CONF_KO.get(h.confidence, "—"))),
            (tr("반응 시각"), _t(a.reaction_time_ms), _t(b.reaction_time_ms), _t(h.reaction_time_ms)),
        ]
        rows += evidence_rows_runs(it)
        rows.append((tr("근거"), a.rationale or "—", b.rationale or "—", h.rationale or "—"))
        sa, sb = (s.extra.get("score") if s.present else None for s in (a, b))
        same = None
        if h.present and h.value != GRADED_NO_GRADE:
            same = tr("A 와 같음") if h.value == a.value else tr("B 와 같음") if h.value == b.value else tr("둘 다 다름")
        rows.append((tr("점수 · 사람과"), _fmt_score(sa) if sa is not None else "—",
                     _fmt_score(sb) if sb is not None else "—", same or "—"))
        return rows

    def _fill_detail(self, it: CompareItem) -> None:
        self._take_detail()
        self.detail_title.setText(f"{_t(it.t_ms)} · {it.chat_id} · {self.status_ko(it.status)}")
        heads = ("",) + self._side_names() + ((tr("사람 최종"),) if self.is_runs_mode() else ())
        for c, text in enumerate(heads):
            lab = QLabel(f"<b>{text}</b>")
            self.detail.addWidget(lab, 0, c)
        for r, row in enumerate(self.detail_rows(), 1):
            for c, text in enumerate(row):
                lab = QLabel(text)
                lab.setTextFormat(Qt.PlainText)
                lab.setWordWrap(True)
                lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
                if r == 1 and c in (1, 2):    # 판정 줄은 비교 결과 색
                    lab.setStyleSheet(f"color: {STATUS_COLORS[it.status].name()};")
                self.detail.addWidget(lab, r, c)

    # ------------------------------------------------------------------ 합계·점수
    def summary_rows(self) -> list[tuple[str, int]]:
        s = self._cmp.summary
        if not s:
            return []
        if s.get("mode") == "runs":
            return [
                (tr("판 A 채팅"), s["engine_rows"]), (tr("판 B 채팅"), s["human_rows"]), (tr("두 판 모두"), s["both"]),
                (tr("같음"), s[SAME]), (tr("등급 다름"), s[GRADE_DIFF]), (tr("A만"), s[ENGINE_ONLY]), (tr("B만"), s[HUMAN_ONLY]),
                (tr("다른 곳 중 사람과 A 가 같음"), s["ref_a"]), (tr("다른 곳 중 사람과 B 가 같음"), s["ref_b"]),
                (tr("다른 곳 중 둘 다 사람과 다름"), s["ref_neither"]), (tr("다른 곳 중 사람 판정 없음"), s["ref_none"]),
                (tr("A 에만 있는 채팅"), s["human_missing"]), (tr("B 에만 있는 채팅"), s["engine_unevaluated"]),
                (tr("짝 없음 (반응 없음)"), s["unpaired_no_reaction"]), (tr("시각 없음"), s["no_time"]),
                (tr("등급 없는 graded 뺌 (A)"), s.get("graded_no_grade_a", 0)),
                (tr("등급 없는 graded 뺌 (B)"), s.get("graded_no_grade_b", 0)),
                (tr("A 중복 chat_id"), s["engine_duplicates"]), (tr("B 중복 chat_id"), s["human_duplicates"]),
            ]
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

    def _set_score_columns(self, pair: bool) -> None:
        heads = [tr("지표"), tr("A 엄격"), tr("A 참고"), tr("B 엄격"), tr("B 참고")] if pair \
            else [tr("지표"), tr("엄격"), tr("참고")]
        self.score_table.setColumnCount(len(heads))
        self.score_table.setHorizontalHeaderLabels(heads)

    def _fill_scores_pair(self, sa: Mapping | None, sb: Mapping | None) -> None:
        """판끼리 견줄 때: 두 판의 점수(각각 사람 최종과 견준 것)를 나란히."""
        self._set_score_columns(True)
        rows = score_rows_pair(sa, sb)
        self.score_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.score_table.setItem(r, c, QTableWidgetItem(text))
        notes = [tr("점수는 판마다 사람 최종 세트와 견준 것입니다")]
        for tag, s in (("A", sa), ("B", sb)):
            if not isinstance(s, Mapping):
                notes.append(tr("{0}: 점수 없음").format(tag))
            elif s.get("dev_only") is True or s.get("preregistered") is False:
                notes.append(tr("{0}: 사전 봉인 아님 — 개발용 점수").format(tag))
        self.score_note.setText(" · ".join(notes))

    def _fill_scores(self, scores: Mapping | None) -> None:
        self._set_score_columns(False)
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

    def score_table_rows(self) -> list[tuple[str, ...]]:
        out = []
        n = self.score_table.columnCount()
        for r in range(self.score_table.rowCount()):
            out.append(tuple(self.score_table.item(r, c).text() for c in range(n)))
        return out

    def all_texts(self) -> list[str]:
        """화면의 모든 글자(시험용: 봉인 중 새는 것이 없는지 본다)."""
        out = []
        for w in self.findChildren(QLabel):
            out.append(w.text())
        for i in range(self.list.count()):
            out.append(self.list.item(i).text())
        for box in (self.run_box, self.against_box):
            out += [box.itemText(i) for i in range(box.count())]
        for t in (self.summary_table, self.confusion_table, self.score_table):
            for r in range(t.rowCount()):
                for c in range(t.columnCount()):
                    it = t.item(r, c)
                    if it is not None:
                        out.append(it.text())
        return out
