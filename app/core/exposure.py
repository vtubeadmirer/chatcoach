"""계획 취소·봉인 해제 뒤 라벨링 세션의 AI 결과 노출 표시 (16 8-1 7번, 06 7-1·7-2, 08 D-13 2·D-15 7).

라벨 형식(label-event 스키마)은 시범 동안 고정이다(D-14 7, D-15 7). 그래서 '취소 뒤 만든 세션은 AI 결과에 노출됐을 수
있다' 를 라벨 파일에 `view.exposed` 줄로 **써 넣지 않고**, 보고서·내보내기가 그때그때 **계산**한다.
계산 결과는 `view.exposed(what = ai_results)` 줄이 있는 것과 같은 자리(06 6-6 의 view_exposed)로 쓰여, 그 세션을
일치도·AI 비교의 대표값에서 뺀다(숫자는 낸다).

규칙 (보수적으로 — 블라인드 쪽으로 안전하게)
1. **계획 취소**: 재라벨 계획 하나가 취소되면 06 7-1 관문이 그 계획을 '잠긴 재라벨 세션이 있어야 하는 계획' 으로 세지 않아
   봉인 해제가 앞당겨질 수 있다. 그래서 그 프로젝트에서 `cancelled_at` **이후(같은 초 포함)** 에 만든 재라벨 세션
   (relabel_same·relabel_second)은 모두 `ai_results` 노출로 본다. 어느 계획이 취소됐는지는 가리지 않는다(프로젝트 단위).
2. **봉인 해제**(`ai.unsealed`, 봉인 담당이 쓰는 기록): 해제 시각에 아직 잠기지 않았던 라벨링 세션(primary·relabel_*)과
   해제 뒤에 만든 라벨링 세션은 노출로 본다. 잠금 시각을 모르면 잠기지 않은 것으로 본다. 조건을 채운 해제
   (`conditions_met = true`)도 같다 — 해제 뒤의 라벨링은 결과를 볼 수 있다.
3. 시각을 읽지 못하면(빈 값·형식 틀림·시간대 없음) 그 세션은 노출로 본다(사유 `time_unknown`). 취소·해제 시각을 읽지
   못하면 그 기록은 '맨 처음' 으로 본다(모든 대상 세션이 그 뒤).
4. 분석·조정·출처 판정 세션은 라벨링 세션이 아니라 표시하지 않는다(그 세션들의 노출은 visibility·agreement 가 따로 본다).
5. **훈련 열기**(D-18 7, 18 6-5 3): 스트리머 훈련은 견주기에서 사람 최종 세트(또는 손라벨)를 보인다. 그래서 그 프로젝트에서
   훈련 세션을 연 시각(training\\<id>.jsonl 의 training.start 줄 created_at)에 아직 잠기지 않았던 라벨링 세션과 그 뒤에
   만든 라벨링 세션은 노출로 본다(what = other_session_labels, 사유 training_opened). 누가 훈련했는지는 가리지 않는다
   (D-17 3 노출 범위는 프로젝트 단위). 열기 시각을 읽지 못하면 '맨 처음' 으로 본다(규칙 3 과 같음).
   ai_results 가 아니라 other_session_labels 로 친다 — 보고서는 what 과 상관없이 view_exposed 가 있으면 대표값에서 뺀다.

시각 비교는 벽시계 값(ISO 8601, 시간대 오프셋 포함)끼리 한다. 순서 판단에 벽시계를 쓰지 않는 원칙(03 13-1)의 예외인데,
취소·해제 기록과 세션 기록은 서로 다른 파일이라 (파일, seq) 순서가 없다. PC 시계 조작은 06 7-4 대로 보장 밖이다.

core 층: 파일·시계·난수·Qt 를 쓰지 않는다. 호출하는 쪽이 session.open·잠금·취소 기록·해제 기록을 읽어 dict 로 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from . import visibility as vis

AI_RESULTS = vis.AI_RESULTS                       # view.exposed 의 what 값 (03 6-4)
RELABEL_KINDS = frozenset({vis.RELABEL_SAME, vis.RELABEL_SECOND})

REASON_PLAN_CANCELLED = "plan_cancelled"          # 규칙 1
REASON_AI_UNSEALED = "ai_unsealed"                # 규칙 2
REASON_TIME_UNKNOWN = "time_unknown"              # 규칙 3 (세션 시각을 읽지 못함)
REASON_TRAINING_OPENED = "training_opened"        # 규칙 5 (스트리머 훈련을 엶, D-18 7)
REASONS = (REASON_PLAN_CANCELLED, REASON_AI_UNSEALED, REASON_TIME_UNKNOWN, REASON_TRAINING_OPENED)
TRAINING_WHAT = vis.OTHER_SESSION_LABELS          # 훈련이 보이는 것 = 사람 라벨(최종 세트·손라벨)


# ----------------------------------------------------------------------------- 입력
@dataclass(frozen=True)
class SessionInfo:
    """노출 계산에 쓰는 세션 하나. created_at = session.open.body.session_created_at,
    locked_at = 잠금 파일의 locked_at(없으면 None = 아직 안 잠김)."""
    session_id: str
    session_kind: str
    created_at: str | None = None
    locked_at: str | None = None
    relabel_plan_id: str | None = None


def session_info(open_record: Mapping, locked_at: str | None = None) -> SessionInfo | None:
    """session.open 줄(dict) → SessionInfo. session.open 이 아니거나 모양이 틀리면 None."""
    if not isinstance(open_record, Mapping) or open_record.get("kind") != "session.open":
        return None
    body = open_record.get("body")
    sid = open_record.get("session_id")
    if not isinstance(body, Mapping) or not isinstance(sid, str):
        return None
    kind = body.get("session_kind")
    if not isinstance(kind, str):
        return None
    ca = body.get("session_created_at")
    pid = body.get("relabel_plan_id")
    return SessionInfo(session_id=sid, session_kind=kind, created_at=ca if isinstance(ca, str) else None,
                       locked_at=locked_at if isinstance(locked_at, str) else None,
                       relabel_plan_id=pid if isinstance(pid, str) else None)


def parse_time(s) -> datetime | None:
    """ISO 8601 시각(시간대 오프셋 필수) → aware datetime. 아니면 None. 'Z' 도 받는다."""
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.strip()
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo is not None and d.utcoffset() is not None else None


def _as_session(s) -> SessionInfo | None:
    if isinstance(s, SessionInfo):
        return s
    if isinstance(s, Mapping):
        sid, kind = s.get("session_id"), s.get("session_kind")
        if not isinstance(sid, str) or not isinstance(kind, str):
            return None
        ca, la, pid = s.get("created_at", s.get("session_created_at")), s.get("locked_at"), s.get("relabel_plan_id")
        return SessionInfo(sid, kind, ca if isinstance(ca, str) else None, la if isinstance(la, str) else None,
                           pid if isinstance(pid, str) else None)
    return None


# ----------------------------------------------------------------------------- 출력
@dataclass(frozen=True)
class ExposureMark:
    """세션 하나가 노출로 표시된 까닭 하나. basis = 취소된 plan_id 또는 해제 run_id, basis_at = 그 기록의 시각 원문."""
    session_id: str
    what: str
    reason: str
    basis: str | None
    basis_at: str | None


@dataclass(frozen=True)
class ExposureView:
    """계산된 노출 표시 묶음. 보고서는 view_exposed(sid, 기록된 what 들) 로 기록과 계산을 합쳐 쓴다."""
    marks: tuple = ()

    def exposed(self, what: str = AI_RESULTS) -> frozenset:
        """그 what 에 노출로 표시된 세션 ID 들 (view.exposed(ai_results) 를 계산한 것)."""
        return frozenset(m.session_id for m in self.marks if m.what == what)

    def exposed_any(self) -> frozenset:
        """what 과 상관없이 노출로 표시된 세션 ID 들 (AI 결과 노출 + 훈련 노출)."""
        return frozenset(m.session_id for m in self.marks)

    def whats(self, session_id: str) -> tuple:
        """그 세션의 계산된 노출 what 들(정렬)."""
        return tuple(sorted({m.what for m in self.marks if m.session_id == session_id}))

    def is_exposed(self, session_id: str, what: str | None = None) -> bool:
        return any(m.session_id == session_id and (what is None or m.what == what) for m in self.marks)

    def marks_of(self, session_id: str) -> tuple:
        return tuple(m for m in self.marks if m.session_id == session_id)

    def reasons(self, session_id: str) -> tuple:
        return tuple(sorted({m.reason for m in self.marks if m.session_id == session_id}))

    def view_exposed(self, session_id: str, recorded: Iterable[str] = ()) -> list:
        """라벨 파일에 기록된 view.exposed 의 what 들 + 계산된 what 들 (정렬, 중복 없음).
        agreement_report._side 의 'view_exposed' 칸에 그대로 넣으면 대표값에서 빠진다."""
        out = {w for w in recorded if isinstance(w, str)}
        out.update(self.whats(session_id))
        return sorted(out)


# ----------------------------------------------------------------------------- 계산
def _event_time(doc: Mapping, keys: tuple) -> tuple[datetime | None, str | None]:
    for k in keys:
        v = doc.get(k)
        if isinstance(v, str):
            return parse_time(v), v
    return None, None


def compute_exposure(sessions: Iterable, cancels: Iterable[Mapping] = (),
                     unseals: Iterable[Mapping] = (), trainings: Iterable[Mapping] = ()) -> ExposureView:
    """노출 표시를 계산한다.

    - sessions: SessionInfo 또는 {session_id, session_kind, created_at|session_created_at, locked_at?, relabel_plan_id?}.
      모양이 틀린 것은 건너뛴다.
    - cancels: 재라벨 계획 취소 기록(relabel/<plan_id>.cancel.json, app.relabel.read_cancel 이 준 dict).
      `plan_id`·`cancelled_at` 만 본다.
    - unseals: 봉인 해제 기록(seal.jsonl 의 ai.unsealed 줄, app/seal.py). `run_id` 와 시각만 본다 — 시각 칸은
      `unsealed_at` → `at`(app/seal.py 가 쓰는 이름) → `created_at` 순으로 찾는다.
    - trainings: 훈련 세션 열기(규칙 5). {session_id, started_at|created_at} — app.exposure_io.project_trainings 가
      training\\<id>.jsonl 의 training.start 줄에서 만든다. 시각을 읽지 못하면 맨 처음으로 본다.
    같은 세션·같은 사유·같은 근거는 한 번만 남는다. 순서: 세션 ID, 사유, 근거.
    """
    infos = [x for x in (_as_session(s) for s in sessions) if x is not None]
    cancel_ev = []
    for c in cancels:
        if isinstance(c, Mapping):
            t, raw = _event_time(c, ("cancelled_at",))
            pid = c.get("plan_id")
            cancel_ev.append((t, raw, pid if isinstance(pid, str) else None))
    unseal_ev = []
    for u in unseals:
        if isinstance(u, Mapping):
            t, raw = _event_time(u, ("unsealed_at", "at", "created_at"))
            rid = u.get("run_id")
            unseal_ev.append((t, raw, rid if isinstance(rid, str) else None))
    train_ev = []
    for g in trainings:
        if isinstance(g, Mapping):
            t, raw = _event_time(g, ("started_at", "created_at"))
            tid = g.get("session_id")
            train_ev.append((t, raw, tid if isinstance(tid, str) else None))
    marks: set[ExposureMark] = set()
    for s in infos:
        if s.session_kind not in vis.LABELING_SESSIONS:
            continue
        created = parse_time(s.created_at)
        locked = parse_time(s.locked_at)
        if s.session_kind in RELABEL_KINDS:
            for t, raw, pid in cancel_ev:
                if created is None:
                    marks.add(ExposureMark(s.session_id, AI_RESULTS, REASON_TIME_UNKNOWN, pid, raw))
                elif t is None or created >= t:
                    marks.add(ExposureMark(s.session_id, AI_RESULTS, REASON_PLAN_CANCELLED, pid, raw))
        for t, raw, rid in unseal_ev:
            if created is None:
                marks.add(ExposureMark(s.session_id, AI_RESULTS, REASON_TIME_UNKNOWN, rid, raw))
                continue
            locked_before = locked is not None and t is not None and locked < t
            if not locked_before:
                marks.add(ExposureMark(s.session_id, AI_RESULTS, REASON_AI_UNSEALED, rid, raw))
        for t, raw, tid in train_ev:
            if created is None:
                marks.add(ExposureMark(s.session_id, TRAINING_WHAT, REASON_TIME_UNKNOWN, tid, raw))
                continue
            locked_before = locked is not None and t is not None and locked < t
            if not locked_before:
                marks.add(ExposureMark(s.session_id, TRAINING_WHAT, REASON_TRAINING_OPENED, tid, raw))
    order = sorted(marks, key=lambda m: (m.session_id, m.reason, m.basis or "", m.basis_at or ""))
    return ExposureView(marks=tuple(order))


__all__ = [
    "AI_RESULTS", "RELABEL_KINDS", "REASON_PLAN_CANCELLED", "REASON_AI_UNSEALED", "REASON_TIME_UNKNOWN", "REASONS",
    "REASON_TRAINING_OPENED", "TRAINING_WHAT",
    "SessionInfo", "session_info", "parse_time", "ExposureMark", "ExposureView", "compute_exposure",
]
