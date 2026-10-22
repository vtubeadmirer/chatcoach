"""계산된 AI 결과 노출(app.core.exposure)의 입력을 모으는 입출력 층 (16 8-1 7번, 06 6-6 10번·7-1·7-2, D-15 7).

core.exposure 는 파일을 열지 않는다. 여기서 프로젝트의 재라벨 계획 취소 기록(relabel/<plan_id>.cancel.json)과
봉인 기록(seal.jsonl 의 ai.unsealed 줄)을 읽어 넘긴다. 라벨 파일·봉인 파일(.bin)·runs 는 열지 않는다.
세션 쪽(session.open·잠금 시각)은 부르는 쪽(최종 세트·일치도 보고서)이 이미 읽은 것을 넘긴다.

보수적 규칙 (블라인드 쪽으로 안전하게)
- 취소 기록이 형식에 맞지 않으면 취소가 아니다(relabel.is_cancelled 와 같은 규칙) — 그 계획은 06 7-1 관문에서 계속 센다.
- seal.jsonl 에 깨진 줄이 있으면 그 줄이 해제였을 수 있어 '시각 모르는 해제' 하나를 더한다(모든 라벨링 세션이 노출).
- ai 폴더 자리가 규칙에 맞지 않으면(프로젝트·저장소 안 등) 봉인 자체가 될 수 없는 자리라 해제도 없다고 본다.

Qt 를 쓰지 않는다. 로그·경고에는 세션 ID·건수만 남긴다.
"""
from __future__ import annotations

import os
from typing import Iterable, Mapping

from .core import exposure as ex
from .core import relabel_plan as rp
from .core import visibility as vis

CANCEL_SUFFIX = ".cancel.json"


def project_cancels(project_root: str) -> list[dict]:
    """relabel/ 의 형식이 맞는 취소 기록 전부(plan_id 순). 계획 파일은 열지 않는다."""
    from . import relabel as relabel_io
    d = os.path.join(project_root, relabel_io.RELABEL_DIR)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        if not n.endswith(CANCEL_SUFFIX):
            continue
        pid = n[:-len(CANCEL_SUFFIX)]
        if not rp.PLAN_ID_RE.match(pid):
            continue
        try:
            doc = relabel_io.read_cancel(project_root, pid)
        except (OSError, ValueError):
            continue
        if doc is not None:
            out.append(doc)
    return out


def project_unseals(project_root: str, *, ai_root: str | None = None,
                    reader_kind: str | None = None) -> tuple[list[dict], list[str]]:
    """(ai.unsealed 기록들, 경고). seal.jsonl 만 읽는다."""
    from . import seal as sealmod
    try:
        events, probs = sealmod.unseal_events(project_root, ai_root=ai_root, reader_kind=reader_kind)
    except sealmod.SealBlindError:
        raise
    except (sealmod.SealError, OSError):
        return [], []
    warns: list[str] = []
    if probs:
        events = list(events) + [{"run_id": None, "at": None}]
        warns.append(f"봉인 기록(seal.jsonl)에 읽지 못한 줄이 {len(probs)}개 있어, 해제가 있었던 것으로 보고 "
                     "라벨링 세션을 노출로 셉니다 (06 7-2)")
    return events, warns


def session_info_of(session_id: str, other_records: Iterable[Mapping], locked_at: str | None) -> ex.SessionInfo:
    """그 세션의 session.open 줄(있으면 첫 줄) + 잠금 시각 → SessionInfo. session.open 이 없으면 만든 시각을 모름."""
    for r in other_records:
        if isinstance(r, Mapping) and r.get("session_id") == session_id and r.get("kind") == "session.open":
            info = ex.session_info(r, locked_at)
            if info is not None:
                return info
    return ex.SessionInfo(session_id=session_id, session_kind=vis.session_kind_of_id(session_id) or "",
                          created_at=None, locked_at=locked_at if isinstance(locked_at, str) else None)


def compute_for(project_root: str, sessions: Iterable[ex.SessionInfo], *, ai_root: str | None = None,
                reader_kind: str | None = None, include_unseals: bool = True) -> tuple[ex.ExposureView, list[str]]:
    """그 세션들의 계산된 노출 + 경고. 노출된 세션마다 사유를 한 줄로 경고에 남긴다(세션 ID·사유 코드만).
    include_unseals=False 면 ai 폴더(seal.jsonl)를 열지 않고 계획 취소(규칙 1)만 본다 — 사람끼리 일치도 보고서는
    봉인 폴더를 열지 않는다는 약속(tests/test_blind_ai)을 지킨다."""
    sessions = list(sessions)
    cancels = project_cancels(project_root)
    unseals, warns = (project_unseals(project_root, ai_root=ai_root, reader_kind=reader_kind)
                      if include_unseals else ([], []))
    view = ex.compute_exposure(sessions, cancels, unseals)
    for s in sorted(view.exposed()):
        warns.append(f"{s}: 계산된 AI 결과 노출({', '.join(view.reasons(s))}) — 대표값에서 뺍니다 (06 6-6 10번)")
    return view, warns


__all__ = ["project_cancels", "project_unseals", "session_info_of", "compute_for", "CANCEL_SUFFIX"]
