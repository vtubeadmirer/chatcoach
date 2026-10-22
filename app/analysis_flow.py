"""방송 분석 모드 메뉴의 자료 모으기 (08 D-01 '방송 분석', D-15 7, 16 8-1 1~6번) — Qt 를 쓰지 않는 입출력 층.

메인 창의 [분석] · [보고 → AI 비교 보고서…] 메뉴가 이 함수들을 부른다. 화면(app/ui/analysis_view.AnalysisView)은 파일을
열지 않으므로, 열 수 있는지와 무엇을 넘길지를 여기서 정한다.

자리(reader_kind, app.core.visibility 표)
- 라벨링·재라벨·출처 판정 세션: 아무것도 하지 않는다(ANALYSIS_MENU_KINDS 밖 — 메뉴 자체가 꺼진다). data\\ai 를 열지 않는다.
- 세션 밖(보기 전용, None)·조정 세션: 가져오기(봉인)·해제·AI 비교 보고서.
- 분석 세션: '봉인됨 n건' 과 해제된 결과의 나란히 보기만(visibility UNSEALED_ONLY). 해제 뒤(단계 COMPARISON)에만
  최종 세트(재라벨·조정 반영)를 만든다.

로그·화면 문장에는 건수·해시 앞 8자·ID 만 남긴다. 채팅 본문·닉네임·전사문은 다루지 않는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from .core import visibility as vis

LOG = logging.getLogger("chatcoach")

# 분석 메뉴가 켜지는 자리. 라벨링(1차·재라벨)·출처 판정 세션은 메뉴 자체가 꺼진다(06 7-3).
ANALYSIS_MENU_KINDS = (None, vis.ADJUDICATION, vis.ANALYSIS)
SEAL_KINDS = (None, vis.ADJUDICATION)          # 가져오기(봉인)·해제·보고서
VIEW_KINDS = (vis.ANALYSIS,)                   # 나란히 보기 (D-01 방송 분석 모드)


def menu_block_reason(reader_kind: str | None) -> str | None:
    if reader_kind in ANALYSIS_MENU_KINDS:
        return None
    return "라벨링·재라벨·출처 판정 세션에서는 분석 메뉴를 쓰지 않습니다 (06 7-3 블라인드)"


def seal_block_reason(reader_kind: str | None) -> str | None:
    if reader_kind in SEAL_KINDS:
        return None
    if reader_kind == vis.ANALYSIS:
        return "AI 결과 가져오기·봉인 해제는 세션 없이(보기 전용) 또는 조정 세션에서 합니다 (06 7-1)"
    return menu_block_reason(reader_kind)


def view_block_reason(reader_kind: str | None) -> str | None:
    if reader_kind in VIEW_KINDS:
        return None
    return "엔진과 나란히 보기는 분석 세션에서 엽니다 (08 D-01 방송 분석 모드)"


@dataclass
class SideBySide:
    """나란히 보기에 넘길 것. run 이 None 이면 봉인 상태 — sealed_count 만 쓴다."""
    sealed_count: int
    unsealed_runs: list[str]
    run_id: str | None = None
    run: Mapping | None = None
    final_rows: list[dict] = field(default_factory=list)       # 점수와 같은 모집단: set=final 이고 eval.lenient 인 행
    excluded_rows: list[dict] = field(default_factory=list)    # set=final 이지만 평가에서 빠진 행(사유는 row.eval)
    scores: dict | None = None
    warnings: list[str] = field(default_factory=list)


def unsealed_run_ids(status) -> list[str]:
    """seal_status 결과 → 해제된 run_id (해제 시각 순)."""
    rows = [e for e in status.entries if e.unsealed_at is not None and e.run_id]
    rows.sort(key=lambda e: e.unsealed_at or "")
    return [e.run_id for e in rows]


def seal_counts(project_root: str, *, reader_kind: str | None, ai_root: str | None = None) -> tuple[int, list[str]]:
    """(봉인됨 = 아직 해제하지 않은 건수, 해제된 run_id 목록). seal.jsonl 과 잠금 파일만 연다."""
    from . import seal
    st = seal.seal_status(project_root, ai_root=ai_root, reader_kind=reader_kind)
    return st.sealed_count, unsealed_run_ids(st)


def scores_for_view(body: Mapping | None) -> dict | None:
    """ai_compare.compare 결과 → 나란히 보기 점수 표 모양 {strict:{지표:값}, lenient:{…}, warnings, gate}.
    엄격 점수가 보류(withheld)면 값은 'held'."""
    if not isinstance(body, Mapping):
        return None
    e1 = body.get("e1")
    if not isinstance(e1, Mapping) or "strict" not in e1:
        return {"strict": {}, "lenient": {}, "warnings": list(body.get("warnings") or [])}

    def flat(m: Mapping | None) -> dict:
        if not isinstance(m, Mapping):
            return {}
        if m.get("status") == "withheld":
            return {k: "held" for k in ("n", "exact", "within_one", "kappa_w", "precision", "recall", "f1",
                                       "kappa", "ac1", "dt_within_2s", "dt_within_5s")}
        g = m.get("grade") or {}
        b = m.get("binary") or {}
        rt = (m.get("reaction_time") or {}).get("within") or {}
        ex, one = g.get("exact"), g.get("one_step")
        return {
            "n": m.get("n"), "exact": ex,
            "within_one": (ex + one) if isinstance(ex, (int, float)) and isinstance(one, (int, float)) else None,
            "kappa_w": g.get("kappa_w"), "precision": b.get("precision"), "recall": b.get("recall"), "f1": b.get("f1"),
            "kappa": b.get("kappa"), "ac1": b.get("ac1"), "dt_within_2s": rt.get("2s"), "dt_within_5s": rt.get("5s"),
        }
    return {"strict": flat(e1.get("strict")), "lenient": flat(e1.get("lenient")),
            "gate": (e1.get("strict") or {}).get("status"), "warnings": list(body.get("warnings") or [])}


def side_by_side(project_root: str, *, reader_kind: str | None, run_id: str | None = None,
                 ai_root: str | None = None) -> SideBySide:
    """나란히 보기 자료. 해제된 run 이 없으면 봉인 건수만(다른 파일은 열지 않는다). run_id 가 없으면 가장 늦게 푼 것.

    해제된 run 이 있으면: 그 run(runs\\<run_id>.json, 해시 대조) → 최종 세트(export.build_final_set, 잠긴 세션만 ·
    06 7-1 조건을 라벨 파일보다 먼저) → ai_compare.compare(파일을 열지 않음) 점수."""
    why = view_block_reason(reader_kind)
    if why:
        raise PermissionError(why)
    from . import ai_compare, export
    sealed, runs = seal_counts(project_root, reader_kind=reader_kind, ai_root=ai_root)
    out = SideBySide(sealed_count=sealed, unsealed_runs=runs)
    if not runs:
        return out
    rid = run_id if run_id in runs else runs[-1]
    run = ai_compare.load_unsealed_run(project_root, rid, ai_root=ai_root, reader_kind=reader_kind, for_view=True)
    fx = export.build_final_set(project_root, reader_kind=reader_kind, ai_root=ai_root)
    body = ai_compare.compare(run, fx)
    out.run_id, out.run = rid, run["result"]
    # 같음/다름·혼동표가 점수(ai_compare 관대·엄격)와 같은 모집단이 되게, 평가에서 빠진 행(사람끼리 불일치·판단 불가 등)은 따로 둔다
    for it in fx.items:
        if it.get("set") != export.SET_FINAL:
            continue
        (out.final_rows if (it["row"].get("eval") or {}).get("lenient") else out.excluded_rows).append(it["row"])
    out.scores = scores_for_view(body)
    out.warnings = list(fx.info.get("warnings") or []) + list(body.get("warnings") or [])
    if out.excluded_rows:
        by: dict[str, int] = {}
        for r in out.excluded_rows:
            rsn = (r.get("eval") or {}).get("excluded_reasons") or ["unknown"]
            by[rsn[0]] = by.get(rsn[0], 0) + 1
        out.warnings.append(f"평가에서 빠진 사람 최종 행 {len(out.excluded_rows)}건은 나란히 보기·점수에 넣지 않았습니다 ("
                            + ", ".join(f"{k} {v}" for k, v in sorted(by.items())) + ")")
    LOG.info("나란히 보기 자료: 해제 run %d개 · 봉인 %d건 · 최종 세트 행 %d", len(runs), sealed, len(out.final_rows))
    return out


def short(sha: str | None) -> str:
    return (sha or "")[:8]


__all__ = ["ANALYSIS_MENU_KINDS", "SEAL_KINDS", "VIEW_KINDS", "menu_block_reason", "seal_block_reason",
           "view_block_reason", "SideBySide", "unsealed_run_ids", "seal_counts", "scores_for_view", "side_by_side",
           "short"]
