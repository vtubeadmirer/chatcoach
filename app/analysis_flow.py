"""방송 분석 모드 메뉴의 자료 모으기 (08 D-01 '방송 분석', D-15 7, 16 8-1 1~6번) — Qt 를 쓰지 않는 입출력 층.

메인 창의 [분석] · [보고 → AI 비교 보고서…] 메뉴가 이 함수들을 부른다. 화면(app/ui/analysis_view.AnalysisView)은 파일을
열지 않으므로, 열 수 있는지와 무엇을 넘길지를 여기서 정한다.

자리(reader_kind, app.core.visibility 표)
- 라벨링·재라벨·출처 판정 세션: 아무것도 하지 않는다(ANALYSIS_MENU_KINDS 밖 — 메뉴 자체가 꺼진다). data\\ai 를 열지 않는다.
- 세션 밖(보기 전용, None)·조정 세션: 가져오기(봉인)·해제·AI 비교 보고서.
- 분석 세션: '봉인됨 n건' 과 해제된 결과의 나란히 보기만(visibility UNSEALED_ONLY). 해제 뒤(단계 COMPARISON)에만
  최종 세트(재라벨·조정 반영)를 만든다.

해제된 run 이 여럿이면(17 6-1 4번) 화면이 판을 고르고 두 판을 견줄 수 있게 고르기 목록(RunChoice, 최신이 맨 앞)과 읽기
함수(RunLoader — 해제 기록부터 보고, 최종 세트는 한 번만 만든다)를 같이 넘긴다. 사람 일치도 기준은 같은 1차·재라벨 쌍의
최신 일치도 보고서에서 읽는다(agreement_gate, 17 5-2 3번 — 못 찾으면 지금처럼 '기준 미확인').

기본 run (23 5-2 의 10·13): 나란히 보기·주제 흐름·방송 요약·시청자 훈련(app.viewer_flow)이 모두 **같은 규칙**(run_order ·
default_run_id)을 쓴다 — 해제 기록(ai.unsealed)의 engine_version 이 가장 높은 run, 같은 판이면 늦게 푼 것, 판을 아는 run 이
없으면 가장 늦게 푼 run. 판은 seal.jsonl 에서 온 값(SealEntry.engine_version)만 본다(run 파일은 열지 않는다). 옛 기록은
engine_version 이 없어 '판 모름'.

로그·화면 문장에는 건수·해시 앞 8자·ID 만 남긴다. 채팅 본문·닉네임·전사문은 다루지 않는다.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

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


ENGINE_VERSION_MAX = 40          # ai-result 스키마 engine.version 의 maxLength 와 같다
_VER_NUM = re.compile(r"\d+")
VERSION_UNKNOWN_KO = "판 모름"


def clean_version(v) -> str | None:
    """해제 기록의 engine_version 글 → 그대로 또는 None(없거나 이상하면 '판 모름'). 순수."""
    if isinstance(v, str) and 0 < len(v) <= ENGINE_VERSION_MAX and v.isprintable() and v.strip() == v:
        return v
    return None


def version_key(ver: str | None) -> tuple[int, ...] | None:
    """엔진 판 글 → 견줄 숫자 묶음(뒤의 0 은 뗀다). 'link-v0.3' → (0, 3), '0.3.0' → (0, 3),
    '0.1.0'(engine/link.py 의 v0.2 규칙 판 — 판 번호를 올리지 않았다) → (0, 1). 숫자가 없으면 None(판 모름과 같다). 순수."""
    if not isinstance(ver, str):
        return None
    nums = [int(n) for n in _VER_NUM.findall(ver)[:6]]
    if not nums:
        return None
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def version_text(ver: str | None) -> str:
    """'판 0.3.0' 또는 '판 모름' (창 제목·고르기 글, 23 5-2 의 10)."""
    v = clean_version(ver)
    return f"판 {v}" if v else VERSION_UNKNOWN_KO


def order_by_version(run_ids: Sequence[str], versions: Mapping[str, str | None]) -> list[str]:
    """run_ids(늦게 푼 것이 앞) → 기본 run 이 맨 앞인 순서. 판을 아는 run 을 판이 높은 순(같으면 목록 순)으로 앞에,
    판을 모르는 run 은 목록 순 그대로 뒤에. 판을 아는 run 이 없으면 run_ids 그대로. 순수."""
    ids = list(dict.fromkeys(r for r in run_ids if isinstance(r, str) and r))
    known = [(version_key(versions.get(r)), i, r) for i, r in enumerate(ids)]
    good = [x for x in known if x[0] is not None]
    good.sort(key=lambda x: x[0], reverse=True)          # 안정 정렬: 같은 판이면 목록 순(늦게 푼 것이 앞)
    rest = [r for k, _i, r in known if k is None]
    return [r for _k, _i, r in good] + rest


@dataclass(frozen=True)
class RunChoice:
    """나란히 보기의 '엔진 판' 고르기 한 줄 (17 6-1 4번). seal.jsonl 에서 온 값만 — run 파일은 열지 않았다.
    engine_version: 해제 기록(ai.unsealed)의 엔진 판(SealEntry.engine_version). 없으면 None = '판 모름'."""
    run_id: str
    unsealed_at: str | None = None
    presealed: bool | None = None
    latest: bool = False
    engine_version: str | None = None

    @property
    def version_text(self) -> str:
        return version_text(self.engine_version)

    @property
    def label(self) -> str:
        parts = [self.run_id]
        at = (self.unsealed_at or "")[:16].replace("T", " ")
        if at:
            parts.append(f"해제 {at}")
        if self.presealed is False:
            parts.append("사전 봉인 아님")
        if self.latest:
            parts.append("최신")
        return " · ".join(parts)


@dataclass
class SideBySide:
    """나란히 보기에 넘길 것. run 이 None 이면 봉인 상태 — sealed_count 만 쓴다.

    choices·load_run: 해제된 run 이 여럿일 때 화면이 다른 판을 고르거나 두 판을 견줄 때 쓴다(17 6-1 4번).
    화면(AnalysisView)에는 runs=choices, run_loader=load_run 으로 넘긴다. load_run(run_id) 은 RunLoader 의 결과 dict."""
    sealed_count: int
    unsealed_runs: list[str]
    run_id: str | None = None
    run: Mapping | None = None
    final_rows: list[dict] = field(default_factory=list)       # 점수와 같은 모집단: set=final 이고 eval.lenient 인 행
    excluded_rows: list[dict] = field(default_factory=list)    # set=final 이지만 평가에서 빠진 행(사유는 row.eval)
    # AI 확인 큐(08 D-29 3)의 사람 쪽은 final_rows + excluded_rows(= set=final 전부, app.ai_confirm.queue_items)
    scores: dict | None = None
    warnings: list[str] = field(default_factory=list)
    choices: list[RunChoice] = field(default_factory=list)     # 최신이 맨 앞
    load_run: Callable[[str], dict] | None = None
    human_gate: dict | None = None                             # 최신 일치도 보고서에서 읽은 기준 (없으면 None)
    gate_report_id: str | None = None
    engine_version: str | None = None                          # run_id 의 해제 기록 엔진 판(없으면 None = 판 모름)

    @property
    def version_text(self) -> str:
        """창 제목에 붙일 '판 …' / '판 모름' (23 5-2 의 10)."""
        return version_text(self.engine_version)


def _unsealed_entries(status) -> list:
    rows = [e for e in status.entries if e.unsealed_at is not None and e.run_id]
    rows.sort(key=lambda e: e.unsealed_at or "")
    # 같은 run_id 를 두 번 풀었으면(다시 가져와 다시 봉인·해제) 늦은 것 하나만
    seen: dict[str, object] = {}
    for e in rows:
        seen.pop(e.run_id, None)
        seen[e.run_id] = e
    return list(seen.values())


def unsealed_run_ids(status) -> list[str]:
    """seal_status 결과 → 해제된 run_id (해제 시각 순, 같은 run_id 는 늦은 해제 하나)."""
    return [e.run_id for e in _unsealed_entries(status)]


def _entry_version(e) -> str | None:
    """SealEntry.engine_version(해제 기록의 엔진 판). 칸이 없는 옛 SealEntry·옛 기록은 None(판 모름)."""
    return clean_version(getattr(e, "engine_version", None))


def run_versions(status) -> dict[str, str | None]:
    """seal_status 결과 → {해제된 run_id: 엔진 판 또는 None}. seal.jsonl 에서 온 값만."""
    return {e.run_id: _entry_version(e) for e in _unsealed_entries(status)}


def run_choices(status) -> list[RunChoice]:
    """seal_status 결과 → 고르기 목록. 최신(가장 늦게 푼 것)이 맨 앞이다. 기본으로 고를 run 은 default_run_id
    (판이 가장 높은 run — 판을 모르면 맨 앞). 항목마다 해제 기록의 engine_version 을 싣는다."""
    rows = list(reversed(_unsealed_entries(status)))
    return [RunChoice(e.run_id, e.unsealed_at, e.presealed, i == 0, _entry_version(e)) for i, e in enumerate(rows)]


def run_order(status) -> list[str]:
    """seal_status 결과 → 해제된 run_id 를 '기본 run 먼저' 순서로(order_by_version: 판 높은 것 → 같으면 늦게 푼 것 →
    판 모름은 늦게 푼 순). 주제 흐름·전사 낱말·벡터처럼 짝이 있는 run 을 찾을 때 이 순서로 본다."""
    latest_first = list(reversed(unsealed_run_ids(status)))
    return order_by_version(latest_first, run_versions(status))


def default_run_id(status) -> str | None:
    """판 A 기본 (app.ui.analysis_view.default_run_id 와 같은 규칙). 해제된 run 이 없으면 None."""
    order = run_order(status)
    return order[0] if order else None


# ---------------------------------------------------------------------------- 사람 일치도 기준 (17 5-2 3번)
_REACTION_GATES = ("binary_kappa", "grade_kappa_w")
_SEGMENT_GATES = ("segment_kappa", "boundary_median_sec")
_GATE_PASS = ("pass", "pass_point_only")
_REPORT_MAX_BYTES = 16 << 20


def _gate_value(rows: list[Mapping], keys: tuple[str, ...]) -> bool | None:
    vs = [r.get("verdict") for r in rows if r.get("key") in keys]
    if not vs:
        return None
    if "fail" in vs:
        return False
    return True if all(v in _GATE_PASS for v in vs) else None


def agreement_gate(project_root: str, info: Mapping, *, reader_kind: str | None,
                   reports_root: str | None = None) -> tuple[dict | None, str | None]:
    """최종 세트의 1차·재라벨 쌍과 같은 두 세션을 견준 **최신** 일치도 보고서의 기준 판정 → ai_compare 의 human_gate.

    info: export.build_final_set(...).info (primary_session_id · relabel_session_ids · project).
    돌려주는 것: ({'reaction': True·False·None, 'segment': …}, report_id) 또는 (None, None)(못 찾음 — 지금처럼 '기준 미확인').
    판정: 그 종류의 기준 줄 가운데 하나라도 미달(fail)이면 False, 모두 통과면 True, 그 밖(불확실·건수 부족)은 None.
    보고서 폴더(<보고서>\\<project_id>\\*\\report.json)만 읽는다. 보고서를 읽을 수 없는 자리(라벨링·재라벨·출처)는 열지 않는다."""
    if reader_kind is not None and vis.files_to_open(reader_kind).access("reports") not in (vis.READ, vis.RW):
        return None, None
    primary = info.get("primary_session_id")
    relabels = [s for s in info.get("relabel_session_ids") or () if isinstance(s, str)]
    proj = info.get("project") if isinstance(info.get("project"), Mapping) else {}
    pid = proj.get("project_id")
    if not isinstance(primary, str) or not relabels or not isinstance(pid, str) or not pid or os.sep in pid or "/" in pid:
        return None, None
    from . import agreement_report as ar
    from .core.validate import load_json_strict
    root = reports_root or ar.default_out_root(project_root)
    d = os.path.join(root, pid)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return None, None
    pairs = [{primary, r} for r in relabels]
    best: tuple[str, str, dict] | None = None
    for name in names:
        p = os.path.join(d, name, "report.json")
        try:
            with open(p, "rb") as f:
                raw = f.read(_REPORT_MAX_BYTES + 1)
            doc = load_json_strict(raw, _REPORT_MAX_BYTES)
        except (OSError, ValueError):
            continue
        if doc.get("report_type") != ar.REPORT_TYPE or not isinstance(doc.get("gates"), list):
            continue
        sess = (doc.get("header") or {}).get("sessions") if isinstance(doc.get("header"), Mapping) else None
        sids = {s.get("session_id") for s in sess or () if isinstance(s, Mapping)}
        if sids not in pairs:
            continue
        at = doc.get("created_at") if isinstance(doc.get("created_at"), str) else ""
        if best is None or at > best[0]:
            rid = doc.get("report_id") if isinstance(doc.get("report_id"), str) else name
            best = (at, rid, doc)
    if best is None:
        return None, None
    rows = [g for g in best[2]["gates"] if isinstance(g, Mapping)]
    return {"reaction": _gate_value(rows, _REACTION_GATES), "segment": _gate_value(rows, _SEGMENT_GATES)}, best[1]


# ---------------------------------------------------------------------------- run 읽기 (화면이 판을 바꿀 때)
class RunLoader:
    """나란히 보기에서 다른 판을 고르거나 두 판을 견줄 때 부르는 읽기 함수 (17 6-1 4번).

    최종 세트(fx)는 한 번만 만들어 둔다. 부를 때마다 run 의 해제 기록부터 본다(ai_compare.load_unsealed_run,
    for_view). 처음 열 때 고를 수 있던 run_id 밖이면 열지 않는다. 결과: {run_id, run(ai-result), scores, warnings}.
    한 번 읽은 판은 기억한다."""

    def __init__(self, project_root: str, *, reader_kind: str | None, run_ids: Sequence[str], fx,
                 ai_root: str | None = None, human_gate: Mapping | None = None, gate_report_id: str | None = None):
        self.project_root = project_root
        self.reader_kind = reader_kind
        self.run_ids = list(run_ids)
        self.fx = fx
        self.ai_root = ai_root
        self.human_gate = dict(human_gate) if human_gate is not None else None
        self.gate_report_id = gate_report_id
        self._cache: dict[str, dict] = {}
        self._primed: dict[str, dict] = {}

    def prime(self, run_id: str, run: Mapping) -> None:
        """이미 load_unsealed_run 으로 읽은 run 을 넘겨 두 번 읽지 않게 한다(처음 열 때)."""
        if run_id in self.run_ids and isinstance(run, Mapping):
            self._primed[run_id] = dict(run)

    def __call__(self, run_id: str) -> dict:
        why = view_block_reason(self.reader_kind)
        if why:
            raise PermissionError(why)
        if run_id not in self.run_ids:
            raise ValueError("해제된 run 목록에 없는 run 입니다")
        if run_id in self._cache:
            return self._cache[run_id]
        from . import ai_compare
        run = self._primed.pop(run_id, None) or ai_compare.load_unsealed_run(
            self.project_root, run_id, ai_root=self.ai_root, reader_kind=self.reader_kind, for_view=True)
        body = ai_compare.compare(run, self.fx, human_gate=self.human_gate)
        scores = scores_for_view(body)
        if scores is not None and self.gate_report_id is not None:
            scores.setdefault("warnings", []).append(
                f"사람 일치도 기준은 일치도 보고서 {self.gate_report_id} 에서 읽었습니다")
        out = {"run_id": run_id, "run": run["result"], "scores": scores,
               "warnings": list(body.get("warnings") or []), "unsealed": run}
        self._cache[run_id] = out
        LOG.info("나란히 보기: run %s 읽음 (해시 %s)", run_id, short(run.get("sha256")))
        return out

    def compare_runs(self, run_a: str, run_b: str, *, chat_times: Mapping[str, int] | None = None) -> dict:
        """두 판의 다른 곳(ai_compare.compare_runs) — 사람 판정은 이 최종 세트. 숫자·ID 만."""
        from . import ai_compare
        a, b = self(run_a), self(run_b)
        return ai_compare.compare_runs(a["unsealed"], b["unsealed"], final=self.fx, chat_times=chat_times)


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
    """나란히 보기 자료. 해제된 run 이 없으면 봉인 건수만(다른 파일은 열지 않는다). run_id 가 없으면 default_run_id
    (해제 기록의 엔진 판이 가장 높은 run, 판을 모르면 가장 늦게 푼 것 — 화면의 판 A 기본과 같다, 23 5-2 의 10).

    해제된 run 이 있으면: 그 run(runs\\<run_id>.json, 해시 대조) → 최종 세트(export.build_final_set, 잠긴 세션만 ·
    06 7-1 조건을 라벨 파일보다 먼저) → 최신 일치도 보고서의 기준(agreement_gate) → ai_compare.compare(파일을 열지 않음)
    점수. 다른 판은 out.load_run(run_id) 으로 읽는다(고르기 목록은 out.choices, 최신이 맨 앞)."""
    why = view_block_reason(reader_kind)
    if why:
        raise PermissionError(why)
    from . import ai_compare, export, seal
    st = seal.seal_status(project_root, ai_root=ai_root, reader_kind=reader_kind)
    sealed, runs = st.sealed_count, unsealed_run_ids(st)
    out = SideBySide(sealed_count=sealed, unsealed_runs=runs, choices=run_choices(st))
    if not runs:
        return out
    rid = run_id if run_id in runs else (default_run_id(st) or runs[-1])
    out.engine_version = run_versions(st).get(rid)
    # 해제 기록을 라벨 파일보다 먼저 본다(06 7-1): 고른 run 이 해제되지 않았으면 최종 세트를 만들기 전에 멈춘다
    run = ai_compare.load_unsealed_run(project_root, rid, ai_root=ai_root, reader_kind=reader_kind, for_view=True)
    fx = export.build_final_set(project_root, reader_kind=reader_kind, ai_root=ai_root)
    gate, gate_rid = agreement_gate(project_root, fx.info, reader_kind=reader_kind)
    loader = RunLoader(project_root, reader_kind=reader_kind, run_ids=runs, fx=fx, ai_root=ai_root, human_gate=gate,
                       gate_report_id=gate_rid)
    loader.prime(rid, run)
    first = loader(rid)
    body_warnings = first["warnings"]
    out.run_id, out.run = rid, first["run"]
    out.load_run, out.human_gate, out.gate_report_id = loader, gate, gate_rid
    # 같음/다름·혼동표가 점수(ai_compare 관대·엄격)와 같은 모집단이 되게, 평가에서 빠진 행(사람끼리 불일치·판단 불가 등)은 따로 둔다
    for it in fx.items:
        if it.get("set") != export.SET_FINAL:
            continue
        (out.final_rows if (it["row"].get("eval") or {}).get("lenient") else out.excluded_rows).append(it["row"])
    out.scores = first["scores"]
    out.warnings = list(fx.info.get("warnings") or []) + list(body_warnings)
    if out.excluded_rows:
        by: dict[str, int] = {}
        for r in out.excluded_rows:
            rsn = (r.get("eval") or {}).get("excluded_reasons") or ["unknown"]
            by[rsn[0]] = by.get(rsn[0], 0) + 1
        out.warnings.append(f"평가에서 빠진 사람 최종 행 {len(out.excluded_rows)}건은 나란히 보기·점수에 넣지 않았습니다 ("
                            + ", ".join(f"{k} {v}" for k, v in sorted(by.items())) + ")")
    LOG.info("나란히 보기 자료: 해제 run %d개 · 봉인 %d건 · 최종 세트 행 %d · %s", len(runs), sealed,
             len(out.final_rows), "판 앎" if out.engine_version else "판 모름")
    return out


def short(sha: str | None) -> str:
    return (sha or "")[:8]


# ---------------------------------------------------------------------------- 주제 흐름·방송 요약 (D-01, D-17 5·8)
@dataclass
class TopicFlowView:
    """작업대 주제 트랙·방송 요약에 넘길 주제 흐름. doc 이 None 이면 해제된 run 이 없거나(sealed_count 만 뜻이 있음)
    해제된 run 에 짝 주제 흐름이 없다. 해제 전에는 topics\\ 를 열지 않는다(seal.read_topics 가 해제 기록부터 본다)."""
    sealed_count: int
    unsealed_runs: list[str] = field(default_factory=list)
    run_id: str | None = None
    doc: dict | None = None
    blocks: list = field(default_factory=list)                 # app.core.topics.TopicBlock
    warnings: list[str] = field(default_factory=list)
    engine_version: str | None = None                          # run_id 의 해제 기록 엔진 판(없으면 None = 판 모름)

    @property
    def sealed(self) -> bool:
        """해제된 run 이 하나도 없다 — 화면은 '봉인됨 n건' 만 보인다(D-18 3)."""
        return not self.unsealed_runs

    @property
    def sealed_text(self) -> str:
        """'봉인됨 n건' (D-18 3 — 어디서나 같은 말)."""
        return sealed_text(self.sealed_count)


def sealed_text(n) -> str:
    """'봉인됨 n건' (08 D-18 3). n 이 정수가 아니면 0건."""
    k = n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0
    return f"봉인됨 {k}건"


def topic_flow(project_root: str, *, reader_kind: str | None, run_id: str | None = None,
               ai_root: str | None = None) -> TopicFlowView:
    """해제된 run 의 짝 주제 흐름. run_id 를 주면 그 run, 없으면 run_order(판이 가장 높은 run 먼저 → 같으면 늦게 푼 것,
    23 5-2 의 13) 가운데 짝 주제 흐름이 있는 첫 run. 분석 세션만(view_block_reason). seal.jsonl → topics\\<run_id>.json 만
    연다(봉인 파일·라벨 파일은 열지 않는다). 해제 전이면 seal.jsonl 밖에는 아무것도 열지 않는다."""
    why = view_block_reason(reader_kind)
    if why:
        raise PermissionError(why)
    from . import seal
    from .core import topics as T
    st = seal.seal_status(project_root, ai_root=ai_root, reader_kind=reader_kind)
    runs = unsealed_run_ids(st)
    out = TopicFlowView(sealed_count=st.sealed_count, unsealed_runs=runs)
    order = [run_id] if run_id in runs else run_order(st)
    versions = run_versions(st)
    for rid in order:
        try:
            doc = seal.read_topics(project_root, rid, ai_root=ai_root, reader_kind=reader_kind)
        except (seal.SealError, OSError, ValueError) as e:
            out.warnings.append(f"run {rid} 의 주제 흐름을 읽지 못했습니다: {type(e).__name__}")
            continue
        if doc is None:
            continue
        try:
            out.blocks = T.blocks_from_doc(doc)
        except ValueError:
            out.warnings.append(f"run {rid} 의 주제 흐름 형식이 맞지 않습니다")
            continue
        out.run_id, out.doc, out.engine_version = rid, doc, versions.get(rid)
        break
    LOG.info("주제 흐름: 해제 run %d개 · 봉인 %d건 · 덩어리 %d개", len(runs), out.sealed_count, len(out.blocks))
    return out


@dataclass
class BroadcastSummaryData:
    """방송 요약 화면(app/ui/summary_view.SummaryView)에 넘길 것. rows 는 덩어리마다 숫자·주제 이름·키워드만(D-17 8).

    reaction_source = 'engine' 이면(08 D-31 1) summary·rows 의 반응은 엔진 **direct 만**이고, 엔진 likely 는 따로 싣는다:
    rows 마다 likely_reacted(그 덩어리의 likely 채팅 수)·reaction_rate_with_likely(likely 도 넣은 반응률), summary_with_likely
    (likely 도 반응으로 센 요약), engine_counts({'direct', 'likely'} — 덩어리 안 채팅). 화면은 보기 옵션
    'likely 도 반응으로 보기'(app.core.engine_grades)에 따라 고른다. 'final_set'(사람 등급)이면 이 칸들은 없다(None)."""
    sealed_count: int
    sealed: bool
    topic_run_id: str | None = None
    rows: list[dict] = field(default_factory=list)
    summary: object | None = None                              # app.core.topics.BroadcastSummary
    reaction_source: str | None = None                         # 'final_set'(사람 최종 세트) · 'engine'(해제된 엔진 판정)
    question_source: str | None = None                         # 'missed_check'(놓친 후보 판정의 질문 표시) · None
    warnings: list[str] = field(default_factory=list)
    topic_engine_version: str | None = None                    # topic_run_id 의 해제 기록 엔진 판(없으면 판 모름)
    summary_with_likely: object | None = None                  # 'engine' 일 때만: likely 도 반응으로 센 요약(D-31 1)
    engine_counts: dict | None = None                          # 'engine' 일 때만: {'direct': n, 'likely': m}

    @property
    def sealed_text(self) -> str:
        """'봉인됨 n건' (D-18 3)."""
        return sealed_text(self.sealed_count)


def _chat_times_kinds(project_root: str) -> list[dict]:
    """chat/messages.jsonl → [{chat_id, t_ms, kind}] (본문·닉네임은 버린다)."""
    from . import jsonl
    p = os.path.join(project_root, "chat", "messages.jsonl")
    if not os.path.isfile(p):
        return []
    res = jsonl.read(p)
    out = []
    for r in res.records:
        cid, t = r.get("chat_id"), r.get("t_ms")
        if isinstance(cid, str) and isinstance(t, int) and not isinstance(t, bool):
            out.append({"chat_id": cid, "t_ms": t, "kind": r.get("kind") if isinstance(r.get("kind"), str) else None})
    return out


def chat_times(project_root: str | None) -> dict[str, int]:
    """chat/messages.jsonl → {chat_id: VOD 시각 ms} (본문·닉네임은 버린다). 나란히 보기가 채팅 색인이 아직 없을 때 쓴다."""
    if not project_root:
        return {}
    return {r["chat_id"]: r["t_ms"] for r in _chat_times_kinds(project_root)}


def broadcast_summary(project_root: str, *, reader_kind: str | None, run_id: str | None = None,
                      ai_root: str | None = None, flow: TopicFlowView | None = None) -> BroadcastSummaryData:
    """방송 요약 숫자 (17 6-1 2번): 주제 덩어리별 채팅 수·분당 채팅·반응 수·반응률·질문·놓친 질문.

    - 반응: 사람 최종 세트(set=final, 직접·높음 — 사람 등급은 그대로)의 채팅. 최종 세트를 만들 수 없으면 해제된 엔진
      판정으로 대신하고 reaction_source='engine' 과 경고를 남긴다. 이때 반응은 엔진 **direct 만**(08 D-31 1, 같은 chat_id 는
      첫 줄 — 06 6-2)이고 likely 는 따로 센다(BroadcastSummaryData 설명 — rows 의 likely_reacted 등).
    - 질문: 최종 세트의 놓친 후보 판정 가운데 '질문' 표시(check_question)가 있는 채팅 → 반응이 없으면 놓친 질문.
      앱은 채팅 본문으로 질문을 고르지 않는다(엔진 몫).
    해제 전이면 아무 파일도 더 열지 않고 sealed=True 만."""
    tf = flow if flow is not None else topic_flow(project_root, reader_kind=reader_kind, run_id=run_id,
                                                  ai_root=ai_root)
    out = BroadcastSummaryData(sealed_count=tf.sealed_count, sealed=tf.sealed, topic_run_id=tf.run_id,
                               warnings=list(tf.warnings), topic_engine_version=tf.engine_version)
    if tf.doc is None:
        return out
    from . import export
    from .core import agreement as ag
    from .core import engine_grades as eg
    from .core import topics as T
    reacted: set[str] = set()
    likely_ids: set[str] = set()       # 엔진 판정으로 셀 때만: likely 채팅(흐리게 따로, 08 D-31 1)
    questions: dict[str, bool | None] = {}
    try:
        fx = export.build_final_set(project_root, reader_kind=reader_kind, ai_root=ai_root)
        for it in fx.items:
            r = it.get("row") or {}
            if it.get("set") == export.SET_FINAL and r.get("result") == "graded" and r.get("grade") in ag.POSITIVE \
                    and isinstance(r.get("chat_id"), str):
                reacted.add(r["chat_id"])
        for r in fx.other_rows:
            if r.get("label_kind") == "missed_candidate" and r.get("check_question") is True \
                    and isinstance(r.get("chat_id"), str):
                questions[r["chat_id"]] = None
        out.reaction_source = "final_set"
        out.question_source = "missed_check" if questions else None
    except Exception as e:  # noqa: BLE001 — 최종 세트가 안 되면 해제된 엔진 판정으로 대신한다
        from . import seal
        out.warnings.append(f"사람 최종 세트를 만들지 못해 반응은 엔진 판정으로 셉니다: {type(e).__name__}")
        try:
            run = seal.read_run(project_root, tf.run_id, ai_root=ai_root, reader_kind=reader_kind)
        except (seal.SealError, OSError, ValueError):
            run = {}
        seen: set[str] = set()
        for x in run.get("reactions") or []:
            cid = x.get("chat_id") if isinstance(x, Mapping) else None
            if not isinstance(cid, str) or cid in seen:
                continue
            seen.add(cid)                                # 같은 chat_id 는 첫 줄만(06 6-2)
            if x.get("result") != "graded":
                continue
            if eg.counts_as_reaction(x.get("grade"), include_likely=False):
                reacted.add(cid)                         # 기본은 direct 만 반응(08 D-31 1)
            elif x.get("grade") == eg.LIKELY:
                likely_ids.add(cid)                      # likely 는 흐리게 따로
        out.reaction_source = "engine"
    for cid in questions:
        questions[cid] = cid in reacted
    msgs = _chat_times_kinds(project_root)
    summary = T.summarize(tf.blocks, msgs, reacted_chat_ids=reacted, questions=questions)
    out.summary = summary
    out.rows = [{**r.as_dict(), "keywords": list(b.keywords)} for r, b in zip(summary.rows, tf.blocks)]
    if out.reaction_source == "engine":
        only_likely = T.summarize(tf.blocks, msgs, reacted_chat_ids=likely_ids)
        with_likely = T.summarize(tf.blocks, msgs, reacted_chat_ids=reacted | likely_ids, questions=questions)
        for row, rl, rw in zip(out.rows, only_likely.rows, with_likely.rows):
            row["likely_reacted"] = rl.reacted
            row["reaction_rate_with_likely"] = rw.reaction_rate
        out.summary_with_likely = with_likely
        out.engine_counts = {eg.DIRECT: summary.total.reacted, eg.LIKELY: only_likely.total.reacted}
    LOG.info("방송 요약: 덩어리 %d개 · 채팅 %d · 반응 %d (%s) · likely 따로 %s · 질문 %d", len(out.rows),
             summary.total.chats, summary.total.reacted, out.reaction_source,
             out.engine_counts[eg.LIKELY] if out.engine_counts else "-", summary.total.questions)
    return out


__all__ = ["ANALYSIS_MENU_KINDS", "SEAL_KINDS", "VIEW_KINDS", "menu_block_reason", "seal_block_reason",
           "view_block_reason", "SideBySide", "unsealed_run_ids", "seal_counts", "scores_for_view", "side_by_side",
           "short", "RunChoice", "run_choices", "RunLoader", "agreement_gate", "TopicFlowView", "topic_flow",
           "BroadcastSummaryData", "broadcast_summary", "run_versions", "run_order", "default_run_id",
           "order_by_version", "version_key", "version_text", "clean_version", "sealed_text", "VERSION_UNKNOWN_KO",
           "ENGINE_VERSION_MAX", "chat_times"]
