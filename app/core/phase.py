"""단계 순서 강제 (안내서 원칙 5, docs/design/02-architecture.md 6-2, 03 6-4·12-3).

작업대의 모드(둘러보기 · ① 구간 · ② 반응 · ③ 표본)를 열어도 되는지와, 열 수 없을 때 화면에 보일 까닭을 계산한다.
프로젝트 단계는 저장하지 않는다. 자기 세션의 `phase.mark` 줄과 현재 유효 구간 라벨에서 계산한다(03 '단계는 저장하지 않는다').

규칙 (02 6-2 관문 표, 03 12-3)
- ① 구간: 라벨을 쓸 수 있는 세션이면 늘 연다.
- ② 반응: 구간 라벨이 있고, 저스트채팅 구간이 하나 이상이고, 그 구간이 `phase.mark(segments)` 로 완료된 것.
  (반응·발화 라벨은 '대상 채팅이 속한 구간의 분류가 just_chatting 이고 그 구간이 완료됨' — 03 12-3)
- ③ 표본: ② 조건 + 그 구간이 `phase.mark(reactions)` 로 완료된 것(02 6-2 '② → ③ 구간 단위로 2단계 완료 표시').
- 뒤 단계 진행 중에 앞 단계(구간 경계)를 고치는 것은 막지 않는다(03 12-3 끝 문단). 완료 표시는 그대로 남는다.

phase.mark body: {phase, scope: 'all' | [구간 entity_id…], done}. 같은 단계의 줄은 쓴 순서(seq)대로 덮어쓴다 —
'all' 은 모든 구간의 상태를 정하고, 목록은 그 구간들만 정한다. done=false 는 다시 열기다.

core 층: Qt·파일·시계·난수를 쓰지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

BROWSE = "browse"
SEGMENTS = "segments"
REACTIONS = "reactions"
SAMPLES = "samples"
MODES = (BROWSE, SEGMENTS, REACTIONS, SAMPLES)
MODE_LABELS = {BROWSE: "둘러보기", SEGMENTS: "① 구간", REACTIONS: "② 반응", SAMPLES: "③ 표본"}
MARK_PHASES = ("segments", "reactions", "samples", "missed", "origin")
JUST_CHATTING = "just_chatting"


@dataclass(frozen=True)
class Gate:
    """모드 하나를 열 수 있는가. 못 열면 reason 에 사람이 읽을 까닭(무엇을 먼저 하면 되는지)."""
    allowed: bool
    reason: str | None = None


def _seq(r: Mapping) -> tuple:
    s = r.get("seq")
    return (s if isinstance(s, int) and not isinstance(s, bool) else 0, str(r.get("id") or ""))


class PhaseMarks:
    """phase.mark 줄들의 현재 상태. done(phase, entity_id) 로 묻는다."""

    def __init__(self, records: Iterable[Mapping] = ()):
        self._marks: list[Mapping] = []
        for r in records or ():
            self.add(r)

    def add(self, rec: Mapping) -> None:
        """phase.mark 줄 하나를 더한다. 다른 kind 나 모양이 틀린 줄은 무시한다."""
        if not isinstance(rec, Mapping) or rec.get("kind") != "phase.mark":
            return
        b = rec.get("body")
        if not isinstance(b, Mapping) or b.get("phase") not in MARK_PHASES or not isinstance(b.get("done"), bool):
            return
        sc = b.get("scope")
        if sc != "all" and not (isinstance(sc, (list, tuple)) and sc and all(isinstance(x, str) for x in sc)):
            return
        self._marks.append(rec)
        self._marks.sort(key=_seq)

    def __len__(self) -> int:
        return len(self._marks)

    def done(self, phase: str, entity_id: str | None = None) -> bool:
        """그 단계가 (그 구간에서) 완료 표시돼 있는가. entity_id 가 None 이면 'all' 로 표시된 상태만 본다."""
        state = False
        for r in self._marks:
            b = r["body"]
            if b["phase"] != phase:
                continue
            sc = b["scope"]
            if sc == "all":
                state = b["done"]
            elif entity_id is not None and entity_id in sc:
                state = b["done"]
        return bool(state)

    def done_ids(self, phase: str, entity_ids: Iterable[str]) -> list[str]:
        return [e for e in entity_ids if self.done(phase, e)]


def _body(r: Mapping) -> Mapping:
    b = r.get("body") if isinstance(r, Mapping) else None
    return b if isinstance(b, Mapping) else {}


def just_chatting_segments(segments: Sequence[Mapping]) -> list[Mapping]:
    """저스트채팅 분류의 현재 유효 구간 레코드(시작 시각 순)."""
    out = [s for s in segments or () if _body(s).get("category") == JUST_CHATTING
           and isinstance(s.get("entity_id"), str)]
    out.sort(key=lambda s: (_body(s).get("start_ms") or 0, s.get("entity_id")))
    return out


def gates(segments: Sequence[Mapping], marks: PhaseMarks, *, can_write_labels: bool = True,
          why_no_write: str | None = None) -> dict[str, Gate]:
    """모드 → Gate. segments = 자기 세션의 현재 유효 segment 레코드(entity_id + body)."""
    out = {BROWSE: Gate(True)}
    if not can_write_labels:
        why = why_no_write or "이 세션은 라벨을 만들 수 없습니다"
        for m in (SEGMENTS, REACTIONS, SAMPLES):
            out[m] = Gate(False, why)
        return out
    out[SEGMENTS] = Gate(True)
    jc = just_chatting_segments(segments)
    if not segments:
        r = "구간 라벨이 아직 없습니다. ① 구간에서 구간표를 먼저 만드세요 (안내서 원칙 5: 순서를 지킨다)"
        out[REACTIONS] = out[SAMPLES] = Gate(False, r)
        return out
    if not jc:
        r = "저스트채팅 구간이 없습니다. 반응·표본은 저스트채팅 구간 안에서만 만듭니다"
        out[REACTIONS] = out[SAMPLES] = Gate(False, r)
        return out
    seg_done = [s for s in jc if marks.done("segments", s["entity_id"])]
    if not seg_done:
        r = "구간표 완료 표시가 없습니다. ① 구간에서 [구간 완료 표시] 를 누르세요"
        out[REACTIONS] = out[SAMPLES] = Gate(False, r)
        return out
    out[REACTIONS] = Gate(True)
    react_done = [s for s in seg_done if marks.done("reactions", s["entity_id"])]
    if not react_done:
        out[SAMPLES] = Gate(False, "② 반응 단계 완료 표시가 없습니다. ② 반응에서 [반응 완료 표시] 를 누르세요")
    else:
        out[SAMPLES] = Gate(True)
    return out


def sample_targets(segments: Sequence[Mapping], marks: PhaseMarks) -> list[Mapping]:
    """표본을 뽑을 수 있는 구간: 저스트채팅 · 구간 완료 · 반응 완료 (시작 시각 순)."""
    return [s for s in just_chatting_segments(segments)
            if marks.done("segments", s["entity_id"]) and marks.done("reactions", s["entity_id"])]


def mark_body(phase: str, done: bool = True, scope: str | Sequence[str] = "all", note: str | None = None) -> dict:
    """phase.mark 줄의 body (스키마 phaseMarkBody)."""
    if phase not in MARK_PHASES:
        raise ValueError(f"phase 는 {MARK_PHASES} 중 하나")
    if scope != "all":
        scope = list(scope)
        if not scope:
            raise ValueError("scope 목록이 비었습니다")
    b: dict = {"phase": phase, "scope": scope, "done": bool(done)}
    if note:
        b["note"] = str(note)[:300]
    return b


__all__ = ["BROWSE", "SEGMENTS", "REACTIONS", "SAMPLES", "MODES", "MODE_LABELS", "Gate", "PhaseMarks",
           "gates", "just_chatting_segments", "sample_targets", "mark_body"]
