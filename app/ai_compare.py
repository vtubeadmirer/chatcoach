"""AI 결과 비교 보고서 — 봉인 해제된 엔진 판정(run)과 최종 세트를 견준다
(docs/design/06-evaluation-and-agreement.md 6-3 매칭 · 6-4 평가 집합 · 6-5 엄격·참고 점수 · 6-6 제외 계단표 · 6-7 보고서 ·
7장 블라인드, 16 8-1 6번, 08 D-15 7·8).

입출력 층이다. Qt 를 import 하지 않는다. 계산 함수(check_run · compare · reaction_metrics · segment_metrics)는 파일을 열지 않는다.

순서 (블라인드, 06 7-1·7-3)
1. 보고서를 여는 자리(reader_kind)는 세션 밖(None) 또는 조정 세션만. 아니면 AICompareBlindError — 아무것도 열지 않는다.
2. run 이 **봉인 해제된 것**인지 먼저 본다(check_run): `ai.sealed`(검사 valid) 와 같은 sha256 의 `ai.unsealed`(같은 run_id)
   기록이 있어야 한다. 아니면 RunNotUnsealedError — 라벨 파일을 열기 전에 거부한다.
   파일에서 읽을 때(load_unsealed_run)는 seal.jsonl 과 runs\\<run_id>.json 만 연다. sealed\\*.bin(봉인 원본)은 열지 않는다.
3. 최종 세트(export.build_final_set): 잠긴 세션만, 06 7-1 비교 조건을 라벨 파일보다 먼저 본다.
4. 계산 → <보고서 폴더>\\<project_id>\\<report_id>\\ 에 report.json · report.md · tables\\*.csv · manifest.json.
   보고서 폴더 규칙은 일치도 보고서와 같다(agreement_report.default_out_root · check_out_root: 프로젝트·저장소 안 거부).

평가 집합 (06 6-4)
- E1 표본: 최종 세트의 반응 가운데 표본 판정(sample_ref 가 있거나 entry = from_sample). 대표 점수.
- E2 2단계: entry = from_utterance 이고 최종 값이 '반응 있음'(직접·높음). AI 재현율만.
- E3 미검증: AI 는 반응 있음인데 E1·E2 어디에도 없는 채팅. 건수만(엄격·참고 어디에도 넣지 않음).

제외 계단표 (06 6-6) — 처음 걸린 사유 하나로만 센다. 행 사유는 최종 세트 행의 eval.excluded_reasons 를 그대로 쓰고,
보고서 사유 ai_unevaluated(6번)·adjudicated_ambiguous(조정 '모호함으로 남김')·adjudicated_disagreement(두 번 달라 조정 값으로
정함 — 엄격에서만 뺌)·agreement_below_gate(human_gate=False 일 때)를 더한다.

보고서에는 채팅 본문·닉네임·근거 한 줄·메모·전사문을 넣지 않는다. 남는 것은 숫자·분류·등급·chat_id·시각·ID 뿐이다.
"""
from __future__ import annotations

import builtins
import csv
import hashlib
import io
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from .core import agreement as ag
from .core import visibility as vis
from .core.ids import CHAT_ID_RE
from .core.segments import is_transition
from .core.validate import canonical_json

REPORT_TYPE = "ai_compare"
FORMAT_REV = 1
RUN_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
RUN_MAX_BYTES = 64 << 20
SEAL_MAX_BYTES = 16 << 20

# 06 6-6 순서 + 보고서 사유. 같은 채팅은 처음 걸린 사유 하나로만 센다.
# recheck_incomplete 는 참고 점수에 넣으므로(08 D-16 6b) LENIENT_MARK(human_disagreement) 다음에 센다 —
# 그래야 계단표의 '참고 점수 대상' 남은 건수가 참고 n 과 같다(app.export.EXCLUDE_ORDER 와 같은 자리).
ORDER = (
    "non_text_message", "in_boundary_buffer", "origin_virtual", "origin_undeterminable", "ai_unevaluated",
    "conflict_unresolved", "out_of_media", "calibration_unverified", "view_exposed", "audio_not_heard",
    "confidence_undeterminable", "grade_unclear", "adjudicated_exclude", "adjudicated_ambiguous",
    "human_disagreement", "recheck_incomplete", "adjudicated_disagreement", "confidence_probably",
    "agreement_below_gate",
)
REPORT_ONLY = frozenset({"ai_unevaluated", "adjudicated_ambiguous", "adjudicated_disagreement"})
LENIENT_OK = frozenset({"recheck_incomplete", "adjudicated_disagreement", "confidence_probably",
                        "agreement_below_gate"})
LENIENT_MARK = "human_disagreement"     # 이 단계 뒤에 남은 것 = 참고 점수 대상
GRADES_SHOWN = ("direct", "likely", "related_only", "unconfirmed")   # 표 순서 (06 4-2 예와 같음)
AI_COLS = GRADES_SHOWN + ("unclear",)
DEFAULT_RT_TOLERANCES_SEC = (2, 5)      # 06 6-3 반응 시각 ±2초·±5초

STATUS_OK = "ok"
STATUS_WITHHELD = "withheld"            # 사람 일치도 기준 미달 → 엄격 '보류' (06 6-5)
STATUS_GATE_UNKNOWN = "gate_unknown"    # 기준 판정을 받지 못함 → 내되 경고
STATUS_NOT_PROVIDED = "not_provided"    # 엔진이 그 종류를 내지 않음 (06 6-1)


class AICompareError(RuntimeError):
    """비교 보고서를 만들 수 없다. 메시지에 본문·닉네임·경로를 넣지 않는다."""


class RunNotUnsealedError(AICompareError):
    """봉인 해제 기록이 없는 run (06 7-1). 라벨 파일을 열기 전에 거부한다."""


class RunInvalidError(AICompareError):
    """run 이 ai-result 1판 규칙에 맞지 않는다(06 6-2 거부 사유)."""


class AICompareBlindError(AICompareError, PermissionError):
    """이 자리(세션 종류)는 AI 비교 보고서를 만들지 않는다(06 7-3)."""


@dataclass
class ReportResult:
    report: dict
    markdown: str
    tables: dict[str, bytes] = field(default_factory=dict)
    out_dir: str | None = None
    warnings: list[str] = field(default_factory=list)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _flat(rec) -> dict:
    """seal.jsonl 한 줄(평평하거나 body 안에 칸이 있는 모양 둘 다) → 평평한 dict."""
    if not isinstance(rec, Mapping):
        return {}
    out = {k: v for k, v in rec.items() if k != "body"}
    if isinstance(rec.get("body"), Mapping):
        for k, v in rec["body"].items():
            out.setdefault(k, v)
    return out


# ---------------------------------------------------------------------------------------------- run 검사
def check_run(run: Mapping) -> dict:
    """run = {result(ai-result 1판), sha256, sealed(ai.sealed 기록), unsealed(ai.unsealed 기록), raw?(바이트)}.

    봉인 해제 기록이 없거나 해시·run_id 가 맞지 않으면 RunNotUnsealedError. 내용이 6-2 규칙에 어긋나면 RunInvalidError.
    돌려주는 것: 봉인·해제 정보(숫자·시각·ID 만).
    """
    if not isinstance(run, Mapping):
        raise RunNotUnsealedError("run 이 봉인 해제 기록과 함께 오지 않았습니다")
    sha = run.get("sha256")
    sealed, unsealed = _flat(run.get("sealed")), _flat(run.get("unsealed"))
    result = run.get("result")
    if not isinstance(sha, str) or not SHA256_RE.match(sha):
        raise RunNotUnsealedError("run 에 봉인 해시(sha256)가 없습니다")
    if unsealed.get("kind") != "ai.unsealed":
        raise RunNotUnsealedError("봉인 해제(ai.unsealed) 기록이 없는 run 입니다 — 비교하지 않습니다 (06 7-1)")
    if unsealed.get("sha256") != sha:
        raise RunNotUnsealedError("봉인 해제 기록의 해시가 run 과 다릅니다")
    if sealed.get("kind") != "ai.sealed" or sealed.get("sha256") != sha:
        raise RunNotUnsealedError("같은 해시의 봉인(ai.sealed) 기록이 없습니다 — 봉인을 거치지 않은 run 입니다")
    if sealed.get("check") != "valid":
        raise RunNotUnsealedError("봉인 때 검사가 '유효' 가 아니었던 run 입니다")
    raw = run.get("raw")
    if raw is not None:
        want = unsealed.get("run_sha256")
        if not isinstance(raw, (bytes, bytearray)) or not isinstance(want, str) or hashlib.sha256(raw).hexdigest() != want:
            raise RunNotUnsealedError("해제한 결과 파일의 해시가 해제 기록(run_sha256)과 다릅니다")
    if not isinstance(result, Mapping):
        raise RunInvalidError("run 에 결과(result)가 없습니다")
    if unsealed.get("run_id") != result.get("run_id"):
        raise RunNotUnsealedError("봉인 해제 기록의 run_id 가 결과의 run_id 와 다릅니다")
    validate_result(result)
    return {
        "run_id": result.get("run_id"), "sha256": sha, "size": sealed.get("size"),
        "sealed_at": sealed.get("at"), "unsealed_at": unsealed.get("at"),
        "conditions_met": unsealed.get("conditions_met"), "unseal_reason_given": bool(unsealed.get("reason")),
        "presealed": unsealed.get("presealed") if isinstance(unsealed.get("presealed"), bool) else None,
        "unseal_locks": [{"session_id": x.get("session_id"), "sha256": x.get("sha256"), "locked_at": x.get("locked_at")}
                         for x in unsealed.get("locks") or () if isinstance(x, Mapping)],
    }


def _num_ok(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))


def validate_result(result: Mapping) -> None:
    """06 6-2 거부 사유 가운데 비교에 걸리는 것(스키마 판·조합·형식). 엄격 스키마 검사는 가져오기(봉인) 때 했다."""
    if result.get("schema") != "chatcoach/ai-result" or result.get("schema_version") != 1:
        raise RunInvalidError("ai-result 1판이 아닙니다 (schema·schema_version)")
    if not isinstance(result.get("run_id"), str) or not RUN_ID_RE.match(result["run_id"]):
        raise RunInvalidError("run_id 형식이 아닙니다")
    tgt = result.get("target")
    if not isinstance(tgt, Mapping) or not isinstance(tgt.get("video_no"), str):
        raise RunInvalidError("target.video_no 가 없습니다")
    if result.get("time_axis") != "vod_ms":
        raise RunInvalidError("time_axis 가 vod_ms 가 아닙니다 — media_ms 는 가져오기에서 vod_ms 로 바꿔 둔다 (06 6-1)")
    for key in ("reactions", "segments"):
        v = result.get(key)
        if v is not None and not isinstance(v, list):
            raise RunInvalidError(f"{key} 가 배열이 아닙니다")
    for r in result.get("reactions") or ():
        if not isinstance(r, Mapping) or not isinstance(r.get("chat_id"), str) or not CHAT_ID_RE.match(r["chat_id"]):
            raise RunInvalidError("reactions 의 chat_id 형식이 아닙니다")
        res, g = r.get("result"), r.get("grade")
        if res == "unconfirmed":
            if g is not None:
                raise RunInvalidError("unconfirmed 인데 grade 가 있습니다 (06 6-2)")
        elif res == "graded":
            if g not in ("direct", "likely", "related_only", "unclear"):
                raise RunInvalidError("graded 인데 grade 가 없거나 값이 아닙니다 (06 6-2)")
        else:
            raise RunInvalidError("reactions 의 result 값이 아닙니다")
        for k in ("reaction_time_ms", "utterance_start_ms", "utterance_end_ms"):
            if r.get(k) is not None and not _is_int(r.get(k)):
                raise RunInvalidError(f"reactions 의 {k} 가 정수가 아닙니다")
        if not _num_ok(r.get("score")):
            raise RunInvalidError("score 가 수가 아닙니다(NaN·Infinity 거부)")
    for s in result.get("segments") or ():
        if (not isinstance(s, Mapping) or not _is_int(s.get("start_ms")) or not _is_int(s.get("end_ms"))
                or s["end_ms"] <= s["start_ms"] or s.get("category") not in ag.CATEGORIES):
            raise RunInvalidError("segments 의 시작·끝·분류가 올바르지 않습니다")
        if not _num_ok(s.get("score")):
            raise RunInvalidError("score 가 수가 아닙니다(NaN·Infinity 거부)")


def _ai_reactions(result: Mapping) -> tuple[dict[str, Mapping], int]:
    """chat_id → AI 판정(첫 줄만, 06 6-2 '중복은 첫 줄만'). 중복 수."""
    out: dict[str, Mapping] = {}
    dup = 0
    for r in result.get("reactions") or ():
        if r["chat_id"] in out:
            dup += 1
            continue
        out[r["chat_id"]] = r
    return out, dup


# ---------------------------------------------------------------------------------------------- 지표
def _ratio(a, b):
    return (a / b) if b else None


def reaction_metrics(pairs: Sequence[Mapping], *, tolerances_ms: Sequence[int] = (2000, 5000),
                     weights: Mapping[str, float] | None = None) -> dict:
    """사람 ↔ AI 반응 판정 짝 → 지표 (06 6-5 E1 대표 점수, 4-2·4-3 의 식).

    pair = {chat_id, h(사람 값: GRADE_SCALE), a(AI 값: GRADE_SCALE 또는 unclear), h_t, a_t(반응 시각 ms 또는 None)}.
    - 등급표·κ_w 는 AI 값이 척도 안인 짝만(AI '불명확' 은 ai_unclear 로 따로 센다).
    - 이진(반응 있음 = 직접·높음)은 모든 짝. AI '불명확' 은 '반응 있음 아님' 이다.
    - 반응 시각 오차는 둘 다 반응 있음이고 둘 다 시각이 있는 짝.
    - weights(chat_id → E_s/n_s)를 주면 방송 전체로 가중한 정밀도·재현율을 더한다(06 6-4 식).
    """
    table: Counter = Counter()
    ai_unclear: Counter = Counter()
    binary: Counter = Counter()
    diffs: list[int] = []
    wsum = {"tp": 0.0, "fp": 0.0, "fn": 0.0}
    for p in pairs:
        h, a = p["h"], p["a"]
        hp, apos = h in ag.POSITIVE, a in ag.POSITIVE
        binary[(hp, apos)] += 1
        if weights is not None:
            w = float(weights.get(p["chat_id"], 0.0))
            if hp and apos:
                wsum["tp"] += w
            elif apos:
                wsum["fp"] += w
            elif hp:
                wsum["fn"] += w
        if a in ag.GRADE_RANK:
            table[(h, a)] += 1
        else:
            ai_unclear[h] += 1
        if hp and apos and _is_int(p.get("h_t")) and _is_int(p.get("a_t")):
            diffs.append(abs(p["a_t"] - p["h_t"]))
    n_grade = sum(table.values())
    exact = sum(c for (x, y), c in table.items() if x == y)
    one = sum(c for (x, y), c in table.items() if abs(ag.GRADE_RANK[x] - ag.GRADE_RANK[y]) == 1)
    kw = ag.weighted_kappa_from_table(table, ag.GRADE_SCALE)
    tp, fp = binary[(True, True)], binary[(False, True)]
    fn, tn = binary[(True, False)], binary[(False, False)]
    prec, rec = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else None
    kb = ag.kappa_from_table(binary, (True, False))
    ac = ag.ac1_binary(binary)
    n = len(pairs)
    out = {
        "n": n,
        "grade": {
            "n": n_grade,
            "confusion": {"rows_human": list(GRADES_SHOWN), "cols_ai": list(AI_COLS),
                          "rows": [[int(table.get((h, a), 0)) for a in GRADES_SHOWN] + [int(ai_unclear.get(h, 0))]
                                   for h in GRADES_SHOWN]},
            "exact": _ratio(exact, n_grade), "one_step": _ratio(one, n_grade),
            "two_plus": _ratio(n_grade - exact - one, n_grade),
            "kappa_w": kw["kappa_w"], "p_o_w": kw["p_o_w"], "p_e_w": kw["p_e_w"],
            "ai_unclear": sum(ai_unclear.values()),
        },
        "binary": {
            "n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec, "f1": f1,
            "kappa": kb["kappa"], "ac1": ac["ac1"], "human_prevalence": _ratio(tp + fn, n),
            "ai_positive_rate": _ratio(tp + fp, n),
            "show_ac1": (_ratio(tp + fn, n) is not None
                         and not ag.AC1_LOW <= _ratio(tp + fn, n) <= ag.AC1_HIGH),
        },
        "reaction_time": {
            "n": len(diffs),
            "median_sec": None if not diffs else ag.percentile(diffs, 0.5) / 1000,
            "within": {f"{t / 1000:g}s": _ratio(sum(1 for d in diffs if d <= t), len(diffs)) for t in tolerances_ms},
        },
    }
    if weights is not None:
        out["binary"]["weighted"] = {
            "precision": _ratio(wsum["tp"], wsum["tp"] + wsum["fp"]),
            "recall": _ratio(wsum["tp"], wsum["tp"] + wsum["fn"]),
        }
    return out


def cascade(item_reasons: Sequence[Sequence[str]]) -> list[dict]:
    """첫 사유 하나로만 세는 계단표 (06 6-6). 결과: [{step, removed, remaining, mark?}] — 첫 줄은 start."""
    first = Counter(rs[0] for rs in item_reasons if rs)
    remain = len(item_reasons)
    rows = [{"step": "start", "removed": 0, "remaining": remain}]
    for r in ORDER:
        k = first.get(r, 0)
        remain -= k
        row = {"step": r, "removed": k, "remaining": remain}
        if r == LENIENT_MARK:
            row["mark"] = "lenient_target"
        if r == ORDER[-1]:
            row["mark"] = "strict_target"
        rows.append(row)
    return rows


def _gate_for(human_gate, kind: str):
    if isinstance(human_gate, Mapping):
        return human_gate.get(kind)
    return human_gate


def _items_of(final) -> tuple[list[dict], list[dict], list[dict]]:
    if isinstance(final, Mapping):
        return list(final.get("items") or ()), list(final.get("segment_items") or ()), list(final.get("conflicts") or ())
    return list(getattr(final, "items", ())), list(getattr(final, "segment_items", ())), list(getattr(final, "conflicts", ()))


def _row_reasons(item: Mapping, ai_has: bool | None, gate) -> list[str]:
    row = item.get("row") or {}
    rs = set((row.get("eval") or {}).get("excluded_reasons") or ()) | set(item.get("report_reasons") or ())
    if item.get("set") == "excluded":
        rs.add("adjudicated_exclude")
    if item.get("set") == "ambiguous":
        rs.add("adjudicated_ambiguous")
    if ai_has is False:
        rs.add("ai_unevaluated")
    if gate is False:
        rs.add("agreement_below_gate")
    return [r for r in ORDER if r in rs]


def _is_e1(row: Mapping) -> bool:
    return row.get("sample_set_id") is not None or row.get("entry") == "from_sample"


def _human_positive(row: Mapping) -> bool:
    return row.get("result") == "graded" and row.get("grade") in ag.POSITIVE


def compare(run: Mapping, final, *, settings: Mapping | None = None, human_gate=None,
            strata: Sequence[Mapping] | None = None) -> dict:
    """봉인 해제된 run + 최종 세트 → 지표 dict (파일을 열지 않는다).

    final: export.build_final_set 결과(FinalSetExport) 또는 같은 모양의 dict {items, segment_items, conflicts}.
    human_gate: 사람 일치도 기준(06 5장) 통과 여부. True·False·None(모름) 또는 {"reaction": …, "segment": …}.
      False 면 그 종류의 엄격 점수는 '보류', None 이면 내되 경고.
    strata: 방송 전체 가중(06 6-4) [{chat_ids: [...], eligible: E_s}]. 주면 n_s = chat_ids 수, 가중치 E_s/n_s.
    """
    info = check_run(run)
    result = run["result"]
    st = ag.merge_settings({k: v for k, v in (settings or {}).items() if k in ("agreement", "gate")})
    extra = (settings or {}).get("ai_compare") or {}
    tols = extra.get("reaction_time_tolerances_sec", DEFAULT_RT_TOLERANCES_SEC)
    if not isinstance(tols, (list, tuple)) or not tols or not all(isinstance(t, (int, float)) and t >= 0 for t in tols):
        raise AICompareError("ai_compare.reaction_time_tolerances_sec 는 0 이상의 수 목록이어야 합니다")
    tol_ms = [int(round(float(t) * 1000)) for t in tols]
    items, seg_items, conflicts = _items_of(final)
    has_rx = "reactions" in result
    ai, dup = _ai_reactions(result)
    g_rx = _gate_for(human_gate, "reaction")

    weights = None
    if strata:
        weights = {}
        for s in strata:
            ids = [c for c in s.get("chat_ids") or () if isinstance(c, str)]
            e = s.get("eligible")
            if ids and isinstance(e, (int, float)) and e > 0:
                for c in ids:
                    weights[c] = float(e) / len(ids)

    rx_items = [it for it in items if (it.get("row") or {}).get("label_kind") == "reaction"]
    e1 = [it for it in rx_items if _is_e1(it["row"])]
    e2 = [it for it in rx_items if it["row"].get("entry") == "from_utterance" and _human_positive(it["row"])]
    e1_conf = [c for c in conflicts if c.get("sample_set_id") is not None or c.get("entry") == "from_sample"]

    def evaluate(group: Sequence[Mapping], conf: Sequence[Mapping] = ()) -> dict:
        reasons, lenient, strict = [], [], []
        for it in group:
            cid = it["row"].get("chat_id")
            rs = _row_reasons(it, (cid in ai) if has_rx else None, g_rx)
            reasons.append(rs)
            if all(r in LENIENT_OK for r in rs):
                lenient.append(it)
                if not rs:
                    strict.append(it)
        for c in conf:
            rs = [r for r in ORDER if r in ({"conflict_unresolved"} | ({"ai_unevaluated"} if has_rx and c["chat_id"] not in ai else set()))]
            reasons.append(rs)
        return {"reasons": reasons, "lenient": lenient, "strict": strict}

    def pairs_of(group: Sequence[Mapping]) -> list[dict]:
        out = []
        for it in group:
            row = it["row"]
            a = ai[row["chat_id"]]
            out.append({"chat_id": row["chat_id"], "h": ag.reaction_value(row), "a": ag.reaction_value(a),
                        "h_t": row.get("reaction_time_ms"), "a_t": a.get("reaction_time_ms"),
                        "t": row.get("chat_t_ms")})
        return out

    warnings: list[str] = []
    if dup:
        warnings.append(f"AI 결과에 같은 chat_id 가 둘 이상인 줄 {dup}개 — 첫 줄만 썼습니다 (06 6-2)")
    if g_rx is None:
        warnings.append("사람 일치도 기준(06 5장) 판정을 받지 않아 반응 엄격 점수를 '보류' 하지 않고 냈습니다 — 일치도 보고서로 확인하세요")

    out: dict[str, Any] = {"run": info, "warnings": warnings}
    if not has_rx:
        out["e1"] = out["e2"] = {"status": STATUS_NOT_PROVIDED}
        out["e3"] = {"status": STATUS_NOT_PROVIDED}
    else:
        ev1 = evaluate(e1, e1_conf)
        p_len, p_str = pairs_of(ev1["lenient"]), pairs_of(ev1["strict"])
        strict_status = STATUS_WITHHELD if g_rx is False else STATUS_GATE_UNKNOWN if g_rx is None else STATUS_OK
        out["e1"] = {
            "population": len(e1) + len(e1_conf),
            "cascade": cascade(ev1["reasons"]),
            "lenient": {"status": STATUS_OK, **reaction_metrics(p_len, tolerances_ms=tol_ms, weights=weights)},
            "strict": ({"status": strict_status, **reaction_metrics(p_str, tolerances_ms=tol_ms, weights=weights)}
                       if strict_status != STATUS_WITHHELD else {"status": STATUS_WITHHELD, "n": 0}),
            "disagreements": sorted(
                [{"chat_id": p["chat_id"], "chat_t_ms": p["t"], "human": p["h"], "ai": p["a"],
                  "strict": any(q["chat_id"] == p["chat_id"] for q in p_str)}
                 for p in p_len if p["h"] != p["a"]], key=lambda d: (d["chat_t_ms"] if _is_int(d["chat_t_ms"]) else -1,
                                                                    d["chat_id"])),
        }
        # 불명확·모호함 참고 표 (06 6-5: 점수에 넣지 않고 AI 가 무엇을 냈는지만)
        ref_unclear: Counter = Counter()
        ref_amb: Counter = Counter()
        for it, rs in zip(e1, ev1["reasons"]):
            cid = it["row"].get("chat_id")
            if cid not in ai:
                continue
            av = ag.reaction_value(ai[cid])
            if it["row"].get("grade") == "unclear" and it.get("set") == "final":
                ref_unclear[av] += 1
            if it.get("set") == "ambiguous":
                ref_amb[av] += 1
        out["e1"]["reference"] = {"human_unclear": {k: ref_unclear.get(k, 0) for k in AI_COLS},
                                  "adjudicated_ambiguous": {k: ref_amb.get(k, 0) for k in AI_COLS}}
        ev2 = evaluate(e2)

        def recall(group):
            n = len(group)
            hit = sum(1 for it in group if ag.reaction_value(ai[it["row"]["chat_id"]]) in ag.POSITIVE)
            return {"n": n, "ai_positive": hit, "recall": _ratio(hit, n)}
        out["e2"] = {"population": len(e2), "cascade": cascade(ev2["reasons"]),
                     "lenient": {"status": STATUS_OK, **recall(ev2["lenient"])},
                     "strict": ({"status": strict_status, **recall(ev2["strict"])}
                                if strict_status != STATUS_WITHHELD else {"status": STATUS_WITHHELD, "n": 0})}
        known = {it["row"].get("chat_id") for it in e1 + e2} | {c["chat_id"] for c in e1_conf}
        labeled = {it["row"].get("chat_id") for it in rx_items} | {c["chat_id"] for c in conflicts}
        ai_pos = [c for c, r in ai.items() if ag.reaction_value(r) in ag.POSITIVE]
        e3 = [c for c in ai_pos if c not in known]
        out["e3"] = {"count": len(e3), "with_other_human_label": sum(1 for c in e3 if c in labeled),
                     "no_human_label": sum(1 for c in e3 if c not in labeled), "ai_positive_total": len(ai_pos)}
        out["ai_reactions"] = {"total": len(ai), "duplicates": dup,
                               "not_in_final_set": sum(1 for c in ai if c not in labeled)}

    if "segments" in result:
        a = st["agreement"]
        out["segments"] = segment_metrics(seg_items, result.get("segments") or (), grid_ms=int(a["grid_ms"]),
                                          tolerance_ms=int(round(float(a["boundary_tolerance_sec"]) * 1000)),
                                          buffer_ms=int(extra.get("boundary_buffer_ms", ag.DEFAULT_BUFFER_MS)),
                                          gate=_gate_for(human_gate, "segment"))
        if _gate_for(human_gate, "segment") is None:
            warnings.append("사람 일치도 기준 판정을 받지 않아 구간 엄격 점수를 '보류' 하지 않고 냈습니다")
    else:
        out["segments"] = {"status": STATUS_NOT_PROVIDED}
    out["settings"] = {"agreement": st["agreement"], "ai_compare": {"reaction_time_tolerances_sec": list(tols)}}
    return out


class _AITimeline:
    def __init__(self, segs: Iterable[Mapping]):
        self.segs = sorted(((s["start_ms"], s["end_ms"], s["category"]) for s in segs), key=lambda x: (x[0], x[1]))

    def at(self, t: int) -> str | None:
        for s, e, c in self.segs:
            if s <= t < e:
                return c
            if s > t:
                break
        return None

    def boundaries(self, lo: int, hi: int) -> list[int]:
        edges = sorted({s for s, _, _ in self.segs} | {e for _, e, _ in self.segs})
        return [t for t in edges if lo < t < hi and is_transition(self.at(t - 1), self.at(t))]


def segment_metrics(seg_items: Sequence[Mapping], ai_segments: Iterable[Mapping], *, grid_ms: int = 1000,
                    tolerance_ms: int = 30_000, buffer_ms: int = ag.DEFAULT_BUFFER_MS, gate=None) -> dict:
    """최종 구간 조각 ↔ AI 구간 (06 6-3 '1초 칸 비교, 경계는 4-1 짝짓기', 6-5 구간 엄격 규칙).

    참고: 세트에 남은 조각(final)이고 사유가 참고 허용(아마도·조정 값)뿐인 칸.
    엄격: + 사유 없음 · 분류 '판단 불가' 아님 · 사람 경계 ± 완충 밖 · 조정 값 아님(재라벨에서 두 번 같은 칸만).
    칸의 분류는 칸 가운데 시각의 값. AI 구간이 없는 칸은 ai_unevaluated 로 센다.
    """
    pieces = sorted((it for it in seg_items if it.get("piece")), key=lambda it: it["piece"]["start_ms"])
    A = _AITimeline(ai_segments)
    # 사람 경계: 맞닿은 두 조각의 분류가 '바뀜' 인 곳 (세트에서 빠진 조각도 사람 판정의 경계로 본다)
    hb: list[int] = []
    for x, y in zip(pieces, pieces[1:]):
        px, py = x["piece"], y["piece"]
        if px["end_ms"] == py["start_ms"] and is_transition(px["category"], py["category"]):
            hb.append(px["end_ms"])

    def near(t0: int, t1: int) -> bool:
        return any(t1 > b - buffer_ms and t0 < b + buffer_ms for b in hb)

    tab = {"lenient": Counter(), "strict": Counter()}
    unev = {"lenient": 0, "strict": 0}
    ms_first: Counter = Counter()
    total = 0
    lo_all = hi_all = None
    for it in pieces:
        pc = it["piece"]
        rs = _row_reasons(it, None, gate)
        if pc["category"] == ag.UNDETERMINABLE:
            rs_strict_extra = True
        else:
            rs_strict_extra = False
        ms = pc["end_ms"] - pc["start_ms"]
        total += ms
        ms_first[rs[0] if rs else "none"] += ms
        lenient = it.get("set") == "final" and all(r in LENIENT_OK for r in rs)
        if not lenient:
            continue
        strict_piece = not rs and not rs_strict_extra
        lo_all = pc["start_ms"] if lo_all is None else min(lo_all, pc["start_ms"])
        hi_all = pc["end_ms"] if hi_all is None else max(hi_all, pc["end_ms"])
        t = pc["start_ms"]
        while t + grid_ms <= pc["end_ms"]:
            m = t + grid_ms // 2
            ac = A.at(m)
            keys = ["lenient"] + (["strict"] if strict_piece and not near(t, t + grid_ms) else [])
            for k in keys:
                if ac is None:
                    unev[k] += 1
                else:
                    tab[k][(pc["category"], ac)] += 1
            t += grid_ms

    def stats(table: Counter, k: str) -> dict:
        kk = ag.kappa_from_table(table, ag.CATEGORIES)
        inter = sum(c for (x, y), c in table.items() if x == y == ag.JUST_CHATTING)
        union = sum(c for (x, y), c in table.items() if ag.JUST_CHATTING in (x, y))
        return {"cells": kk["n"], "ai_unevaluated_cells": unev[k], "time_agreement": kk["p_o"], "kappa": kk["kappa"],
                "jc_iou": _ratio(inter, union),
                "confusion": {"rows_human": list(ag.CATEGORIES), "cols_ai": list(ag.CATEGORIES),
                              "rows": [[int(table.get((x, y), 0)) for y in ag.CATEGORIES] for x in ag.CATEGORIES]}}

    out = {"grid_ms": grid_ms, "total_ms": total, "ms_by_first_reason": dict(sorted(ms_first.items())),
           "lenient": {"status": STATUS_OK, **stats(tab["lenient"], "lenient")}}
    out["strict"] = ({"status": STATUS_WITHHELD} if gate is False else
                     {"status": STATUS_GATE_UNKNOWN if gate is None else STATUS_OK, **stats(tab["strict"], "strict")})
    if lo_all is not None:
        ab = A.boundaries(lo_all, hi_all)
        inside = [b for b in hb if lo_all < b < hi_all]
        pairs, only_h, only_a = ag.match_boundaries(inside, ab, tolerance_ms)
        d = [abs(x - y) for x, y in pairs]
        out["boundaries"] = {"human": len(inside), "ai": len(ab), "matched": len(pairs), "human_only": len(only_h),
                             "ai_only": len(only_a), "median_sec": None if not d else ag.percentile(d, 0.5) / 1000,
                             "within_buffer_ratio": _ratio(sum(1 for x in d if x <= buffer_ms), len(d)),
                             "tolerance_sec": tolerance_ms / 1000, "buffer_sec": buffer_ms / 1000}
    else:
        out["boundaries"] = {"human": 0, "ai": 0, "matched": 0, "human_only": 0, "ai_only": 0, "median_sec": None,
                             "within_buffer_ratio": None, "tolerance_sec": tolerance_ms / 1000,
                             "buffer_sec": buffer_ms / 1000}
    return out


# ---------------------------------------------------------------------------------------------- 파일: run 읽기
def _read_seal_records(d: str) -> list[dict]:
    """seal.jsonl 의 줄(평평하게). 깨진 줄은 없는 것으로 본다(해제 기록이 깨졌으면 해제되지 않은 것)."""
    p = os.path.join(d, "seal.jsonl")
    try:
        with builtins.open(p, "rb") as f:
            raw = f.read(SEAL_MAX_BYTES + 1)
    except FileNotFoundError:
        return []
    if len(raw) > SEAL_MAX_BYTES:
        raise AICompareError("봉인 기록이 너무 큽니다")
    out = []
    for line in raw.decode("utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line, parse_constant=_reject_constant)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(_flat(obj))
    return out


def _reject_constant(name: str):
    raise ValueError(f"JSON 이 아닌 값: {name}")


def load_unsealed_run(project_root: str, run_id: str, *, ai_root: str | None = None,
                      reader_kind: str | None = None, for_view: bool = False) -> dict:
    """봉인 해제된 run 을 읽는다: {result, sha256, sealed, unsealed, raw}.

    seal.jsonl 에서 run_id 의 ai.unsealed 기록을 먼저 찾고, 없으면 결과 파일을 열기 전에 RunNotUnsealedError.
    결과 파일은 app.seal.read_run 으로 연다(해제 기록의 run_sha256 대조). 봉인 원본(sealed\\*.bin)은 열지 않는다.
    폴더 규칙은 app.seal(project_ai_dir) 을 그대로 쓴다.
    for_view: 나란히 보기(분석 세션, D-15 7)용 — 분석 세션(visibility UNSEALED_ONLY)도 읽는다. 보고서는 쓰지 않는다.
    """
    from . import seal
    why = None if for_view and reader_kind == vis.ANALYSIS else reader_block_reason(reader_kind)
    if why:
        raise AICompareBlindError(why)
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise AICompareError("run_id 형식이 아닙니다")
    from .project import open_project
    proj = open_project(project_root)
    if proj.data is None:
        raise AICompareError("project.json 을 읽지 못했습니다")
    try:
        d = seal.project_ai_dir(project_root, proj.project_id, ai_root)
    except seal.SealError as e:
        raise AICompareError(str(e)) from None
    recs = _read_seal_records(d)
    uns = [r for r in recs if r.get("kind") == seal.K_UNSEALED and r.get("run_id") == run_id]
    if not uns:
        raise RunNotUnsealedError(f"run {run_id} 의 봉인 해제(ai.unsealed) 기록이 없습니다 (06 7-1)")
    u = uns[-1]
    sealed = [r for r in recs if r.get("kind") == seal.K_SEALED and r.get("sha256") == u.get("sha256")]
    if not sealed:
        raise RunNotUnsealedError("해제 기록과 같은 해시의 봉인 기록이 없습니다")
    try:
        result = seal.read_run(project_root, run_id, ai_root=ai_root, reader_kind=reader_kind)
    except seal.SealError as e:
        raise RunNotUnsealedError(str(e)) from None
    except (OSError, ValueError) as e:
        raise RunInvalidError(f"해제한 결과 파일을 읽지 못했습니다: {type(e).__name__}") from None
    return {"result": result, "sha256": u.get("sha256"), "sealed": sealed[0], "unsealed": u}


# ---------------------------------------------------------------------------------------------- 보고서
def reader_block_reason(reader_kind: str | None) -> str | None:
    """세션 밖(None)·조정 세션만 된다. 라벨링·재라벨·출처·분석 세션은 data\\ai·data\\reports 를 열지 않는다(06 7-3)."""
    if reader_kind is None or reader_kind == vis.ADJUDICATION:
        return None
    return "이 세션 종류에서는 AI 비교 보고서를 만들지 않습니다 — 세션 밖이나 조정 세션에서 여세요 (06 7-3)"


def _parse_iso(v) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else None


def build_report(project_root: str, run: Mapping, *, final=None, eval_settings: Mapping | None = None,
                 human_gate=None, strata: Sequence[Mapping] | None = None, now: datetime | None = None,
                 created_by: str | None = None, reader_kind: str | None = None, app_version: str | None = None,
                 primary_session_id: str | None = None, relabel_session_ids: Iterable[str] | None = None,
                 adjudication_session_ids: Iterable[str] | None = None,
                 origin_session_ids: Iterable[str] | None = None, ai_root: str | None = None) -> ReportResult:
    """봉인 해제된 run + 프로젝트 → 보고서(아직 쓰지 않음). 해제되지 않았으면 라벨 파일을 열기 전에 RunNotUnsealedError."""
    from . import export
    from . import agreement_report as ar
    from .project import open_project
    why = reader_block_reason(reader_kind)
    if why:
        raise AICompareBlindError(why)
    info = check_run(run)                              # 1) 해제 확인 — 라벨 파일을 열기 전에
    root = os.path.abspath(project_root)
    proj = open_project(root)
    if proj.data is None:
        raise AICompareError("project.json 을 읽지 못했습니다")
    data = proj.data
    result = run["result"]
    if str((data.get("broadcast") or {}).get("video_no")) != result["target"]["video_no"]:
        raise RunInvalidError("run 의 target.video_no 가 프로젝트 방송과 다릅니다 (06 6-2)")
    if final is None:
        final = export.build_final_set(root, primary_session_id=primary_session_id,
                                       relabel_session_ids=relabel_session_ids,
                                       adjudication_session_ids=adjudication_session_ids,
                                       origin_session_ids=origin_session_ids,
                                       reader_kind=reader_kind, ai_root=ai_root)
    fi = final.info if hasattr(final, "info") else dict(final.get("info") or {})
    body = compare(run, final, settings=eval_settings, human_gate=human_gate, strata=strata)
    warnings = list(fi.get("warnings") or []) + list(body.pop("warnings"))

    tgt = result.get("target") or {}
    msha = fi.get("messages_sha256")
    if tgt.get("messages_sha256") is None or tgt.get("messages_sha256") != msha:
        warnings.append("엔진이 쓴 채팅 파일 해시가 프로젝트와 다르거나 없습니다 — 'AI 미평가' 건수로 확인하세요 (06 6-2)")
    eng = result.get("engine") or {}
    if eng.get("preregistered") is not True:
        warnings.append("엔진 설정이 사전 등록되지 않았습니다 (06 7-4)")
    locks = fi.get("locks") or []
    primary = fi.get("primary_session_id")
    p_lock = next((l for l in locks if l["session_id"] == primary), None)
    sealed_at, p_locked = _parse_iso(info["sealed_at"]), _parse_iso((p_lock or {}).get("locked_at"))
    pre_sealed = info["presealed"]                    # 해제 기록에 있으면 그 값(app.seal 계산), 없으면 시각으로
    if pre_sealed is None:
        pre_sealed = None if sealed_at is None or p_locked is None else sealed_at < p_locked
    if pre_sealed is not True:
        warnings.append("사전 봉인 아님(또는 시각을 모름) — 개발용 점수로만 씁니다 (06 7-2)")
    if info["conditions_met"] is not True:
        warnings.append("06 7-1 조건 전에 봉인을 연 run 입니다 — 대표 점수로 쓰지 않습니다")
    unseal_locks = {x["session_id"]: x["sha256"] for x in info["unseal_locks"]}
    for l in locks:
        if l["session_id"] in unseal_locks and unseal_locks[l["session_id"]] != l["lock_sha256"]:
            warnings.append(f"{l['session_id']}: 해제 때 잠금 해시와 지금 잠금 해시가 다릅니다")
        elif l["session_id"] not in unseal_locks:
            warnings.append(f"{l['session_id']}: 봉인 해제 기록에 없는 잠금입니다(해제 뒤 잠근 세션)")
    split = data.get("dataset_split")
    if split == "dev":
        warnings.append("시범(dev) 자료입니다 — 대표 점수가 아니라 dev 보고입니다 (06 6-5)")
    sessions = fi.get("sessions") or {}
    exposed = sorted(s for s, v in sessions.items() if getattr(v, "exposed", False))
    labelers = sorted({s.split("-", 1)[0] for s in [primary] + list(fi.get("relabel_session_ids") or []) if s})
    adjudicators = sorted({s.split("-", 1)[0] for s in fi.get("adjudication_session_ids") or []})
    analysis = vis.analysis_exposure(ar._session_ids_from_names(root)) if os.path.isdir(os.path.join(root, "labels")) else None
    analysis_exposed = sorted(s for s in [primary] + list(fi.get("relabel_session_ids") or [])
                              if analysis is not None and analysis.is_exposed(s))
    if analysis_exposed:
        warnings.append(f"분석 세션(엔진 결과를 보는 화면)을 연 라벨러의 세션 {len(analysis_exposed)}개가 들어 있습니다 (D-14 3)")

    settings_used = body.pop("settings")
    inputs = {
        "project_id": proj.project_id, "video_no": result["target"]["video_no"],
        "run": {"run_id": info["run_id"], "sha256": info["sha256"]},
        "locks": [{"session_id": l["session_id"], "lock_sha256": l["lock_sha256"]} for l in locks],
        "adjudication_session_ids": list(fi.get("adjudication_session_ids") or []),
        "guide": {"guide_version": fi.get("guide_version"), "thresholds_sha256": fi.get("thresholds_sha256")},
        "messages_sha256": msha, "eval_settings": settings_used,
        "eval_settings_sha256": hashlib.sha256(canonical_json(settings_used)).hexdigest(),
        "human_gate": human_gate if not isinstance(human_gate, Mapping) else dict(human_gate),
        "strata": [{"n": len(s.get("chat_ids") or ()), "eligible": s.get("eligible")} for s in strata or ()],
    }
    inputs_sha = hashlib.sha256(canonical_json(inputs)).hexdigest()
    when = now or datetime.now().astimezone()
    from . import __version__
    report = {
        "schema": "chatcoach.report.ai_compare", "schema_version": 1, "format_rev": FORMAT_REV,
        "report_id": f"R-{when.strftime('%Y%m%d')}-{inputs_sha[:4]}", "report_type": REPORT_TYPE,
        "created_at": when.isoformat(timespec="seconds"), "created_by": created_by,
        "app_version": app_version or __version__, "inputs": inputs, "inputs_sha256": inputs_sha,
        "header": {
            "guide_version": fi.get("guide_version"), "thresholds_sha256": fi.get("thresholds_sha256"),
            "origin_judged": bool(fi.get("origin_session_ids")), "dataset_split": split,
            "pre_sealed": pre_sealed, "sealed_at": info["sealed_at"], "unsealed_at": info["unsealed_at"],
            "primary_locked_at": (p_lock or {}).get("locked_at"), "conditions_met": info["conditions_met"],
            "engine": {"name": eng.get("name"), "version": eng.get("version"),
                       "components": eng.get("components"), "preregistered": eng.get("preregistered")},
            "engine_author_included": any(ar._engine_author(data, x) for x in labelers),
            "view_exposed_sessions": exposed, "analysis_exposed_sessions": analysis_exposed,
            "adjudicator_is_labeler": bool(set(adjudicators) & set(labelers)),
            "reinterpretation": False, "human_gate": inputs["human_gate"],
            "sessions": {"primary": primary, "relabel": list(fi.get("relabel_session_ids") or []),
                         "adjudication": list(fi.get("adjudication_session_ids") or []),
                         "origin": list(fi.get("origin_session_ids") or [])},
            "final_set": _final_summary(final),
        },
        **body,
        "warnings": warnings,
        "not_computed": ["신뢰구간", "이어짐·놓친 후보·출처의 AI 점수(11장 1번)", "공유 전처리 대조(6-8)",
                         "자막·자석 사용 여부로 나눈 점수(6-8)", "재해석 보기(9장)"],
    }
    tables = render_tables(report)
    return ReportResult(report, render_markdown(report), tables, None, warnings)


def _final_summary(final) -> dict:
    fs = getattr(final, "final_set", None)
    if fs is not None:
        return {"reactions": fs.summary["reactions"], "segments": fs.summary["segments"],
                "origin_known": fs.summary["origin_known"]}
    s = (final.get("info") or {}).get("summary") if isinstance(final, Mapping) else None
    return dict(s or {})


# ---------------------------------------------------------------------------------------------- 표·쓰기
def _csv(rows: Sequence[Sequence]) -> bytes:
    from .export import _cell
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return buf.getvalue().encode("utf-8-sig")


def render_tables(rep: Mapping) -> dict[str, bytes]:
    """tables/*.csv (UTF-8 BOM, 수식 주입 방지 — export.safe_cell). 채팅 본문은 없다."""
    t: dict[str, bytes] = {}
    e1 = rep.get("e1") or {}
    for kind in ("lenient", "strict"):
        m = e1.get(kind) or {}
        g = (m.get("grade") or {}).get("confusion")
        if g:
            t[f"e1_grade_confusion_{kind}.csv"] = _csv([["human\\ai"] + g["cols_ai"]]
                                                      + [[h] + r for h, r in zip(g["rows_human"], g["rows"])])
        b = m.get("binary")
        if b:
            t[f"e1_binary_{kind}.csv"] = _csv([["tp", "fp", "fn", "tn", "precision", "recall", "f1", "kappa", "ac1"],
                                               [b["tp"], b["fp"], b["fn"], b["tn"], b["precision"], b["recall"],
                                                b["f1"], b["kappa"], b["ac1"]]])
    for key in ("e1", "e2"):
        c = (rep.get(key) or {}).get("cascade")
        if c:
            t[f"{key}_cascade.csv"] = _csv([["step", "removed", "remaining", "mark"]]
                                           + [[r["step"], r["removed"], r["remaining"], r.get("mark")] for r in c])
    if e1.get("disagreements") is not None:
        t["e1_disagreements.csv"] = _csv([["chat_id", "chat_t_ms", "human", "ai", "strict"]]
                                         + [[d["chat_id"], d["chat_t_ms"], d["human"], d["ai"], d["strict"]]
                                            for d in e1["disagreements"]])
    seg = rep.get("segments") or {}
    for kind in ("lenient", "strict"):
        cf = (seg.get(kind) or {}).get("confusion")
        if cf:
            t[f"segment_confusion_{kind}.csv"] = _csv([["human\\ai"] + cf["cols_ai"]]
                                                      + [[h] + r for h, r in zip(cf["rows_human"], cf["rows"])])
    return t


def write_report(res: ReportResult, out_root: str, project_root: str) -> str:
    """<out_root>/<project_id>/<report_id>/ 에 tables/*.csv · report.json · report.md · manifest.json(마지막)."""
    from . import agreement_report as ar
    from .project import atomic_write
    ar.check_out_root(out_root, project_root)
    rep = res.report
    pid = rep["inputs"]["project_id"]
    if not isinstance(pid, str) or not re.match(r"\A[0-9a-f-]{36}\Z", pid):
        raise AICompareError("project_id 형식이 아닙니다")
    if not ar.REPORT_ID_RE.match(rep["report_id"]):
        raise AICompareError("report_id 형식이 아닙니다")
    d = os.path.join(out_root, pid, rep["report_id"])
    os.makedirs(os.path.join(d, "tables"), exist_ok=True)
    files: list[tuple[str, bytes]] = [(f"tables/{n}", b) for n, b in sorted(res.tables.items())]
    files.append(("report.json", (json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")))
    files.append(("report.md", res.markdown.encode("utf-8")))
    for name, b in files:
        atomic_write(os.path.join(d, *name.split("/")), b)
    manifest = {"schema": "chatcoach.report.manifest", "schema_version": 1, "report_id": rep["report_id"],
                "files": [{"name": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in files]}
    atomic_write(os.path.join(d, "manifest.json"),
                 (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    res.out_dir = d
    return d


def make_report(project_root: str, run: Mapping, *, out_root: str | None = None, **kw) -> ReportResult:
    """build_report + write_report. 출력 위치는 무엇을 읽기 전에 먼저 검사한다."""
    from . import agreement_report as ar
    root = os.path.abspath(project_root)
    out = out_root or ar.default_out_root(root)
    ar.check_out_root(out, root)
    res = build_report(root, run, **kw)
    write_report(res, out, root)
    return res


# ---------------------------------------------------------------------------------------------- Markdown
def _f(v, nd: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


_STATUS_KO = {STATUS_OK: "", STATUS_WITHHELD: " — 보류(사람 일치도 기준 미달)",
              STATUS_GATE_UNKNOWN: " — 사람 일치도 기준 미확인", STATUS_NOT_PROVIDED: " — 엔진이 내지 않음"}
_REASON_KO = {
    "start": "시작", "non_text_message": "후원·구독·시스템", "in_boundary_buffer": "경계 완충", "origin_virtual": "출처 가상",
    "origin_undeterminable": "출처 판별 불가", "ai_unevaluated": "AI 미평가", "conflict_unresolved": "판 충돌",
    "out_of_media": "영상 길이 밖", "calibration_unverified": "보정 미확인", "view_exposed": "블라인드 노출",
    "audio_not_heard": "들은 범위 부족", "recheck_incomplete": "재확인 부족", "confidence_undeterminable": "확신도 판단 불가",
    "grade_unclear": "등급 불명확", "adjudicated_exclude": "조정: 평가 제외", "adjudicated_ambiguous": "조정: 모호함으로 남김",
    "human_disagreement": "사람 불일치", "adjudicated_disagreement": "두 번 달라 조정 값(엄격만 제외)",
    "confidence_probably": "확신도 아마도", "agreement_below_gate": "사람 일치도 기준 미달",
}


def render_markdown(rep: Mapping) -> str:
    h = rep["header"]
    L = [f"# AI 비교 보고서 {rep['report_id']}", "",
         f"- run: {rep['inputs']['run']['run_id']} (sha256 앞 12자 {rep['inputs']['run']['sha256'][:12]})",
         f"- 기준서 {h['guide_version']} · 시작값 해시 앞 12자 {str(h['thresholds_sha256'])[:12]}",
         f"- 사전 봉인: {_f(h['pre_sealed'])} · 7-1 조건 충족 뒤 해제: {_f(h['conditions_met'])}",
         f"- 자료 구분: {h['dataset_split']} · 출처 판정: {_f(h['origin_judged'])} · 엔진 작성자 포함: "
         f"{_f(h['engine_author_included'])} · 조정자 = 라벨러: {_f(h['adjudicator_is_labeler'])}",
         f"- 노출 세션: {len(h['view_exposed_sessions'])}개 · 분석 세션 노출: {len(h['analysis_exposed_sessions'])}개", ""]
    e1 = rep.get("e1") or {}
    if e1.get("status") == STATUS_NOT_PROVIDED:
        L += ["## E1 표본", "", "엔진이 반응 판정을 내지 않았다.", ""]
    else:
        L += ["## E1 표본 (대표 점수)", "", f"모집단 {e1['population']}건", "", "| 단계 | 빠짐 | 남음 |", "|---|---|---|"]
        for r in e1["cascade"]:
            mark = " → 참고 점수 대상" if r.get("mark") == "lenient_target" else (
                " → 엄격 점수 대상" if r.get("mark") == "strict_target" else "")
            L.append(f"| {_REASON_KO.get(r['step'], r['step'])} | {r['removed']} | {r['remaining']}{mark} |")
        L.append("")
        for kind, ko in (("lenient", "참고 점수"), ("strict", "엄격 점수")):
            m = e1[kind]
            L.append(f"### {ko}{_STATUS_KO.get(m['status'], '')}")
            L.append("")
            if m["status"] == STATUS_WITHHELD:
                L += ["보류.", ""]
                continue
            g, b, t = m["grade"], m["binary"], m["reaction_time"]
            L += [f"- n {m['n']} · 등급 n {g['n']} · 정확 일치 {_f(g['exact'])} · 한 단계 {_f(g['one_step'])} · "
                  f"두 단계 이상 {_f(g['two_plus'])} · κ_w {_f(g['kappa_w'])} · AI 불명확 {g['ai_unclear']}",
                  f"- 이진: TP {b['tp']} FP {b['fp']} FN {b['fn']} TN {b['tn']} · 정밀도 {_f(b['precision'])} · "
                  f"재현율 {_f(b['recall'])} · F1 {_f(b['f1'])} · κ {_f(b['kappa'])}"
                  + (f" · AC1 {_f(b['ac1'])}" if b.get("show_ac1") else ""),
                  f"- 반응 시각: n {t['n']} · |Δt| 중앙값 {_f(t['median_sec'], 2)}초 · "
                  + " · ".join(f"±{k} 안 {_f(v)}" for k, v in t["within"].items()), ""]
        ref = e1.get("reference") or {}
        L += ["### 참고 표 (점수에 넣지 않음)", "",
              "- 사람 '불명확' 에 AI 가 낸 값: " + ", ".join(f"{k} {v}" for k, v in (ref.get("human_unclear") or {}).items()),
              "- 조정 '모호함으로 남김' 에 AI 가 낸 값: "
              + ", ".join(f"{k} {v}" for k, v in (ref.get("adjudicated_ambiguous") or {}).items()), ""]
        e2, e3 = rep["e2"], rep["e3"]
        L += ["## E2 2단계 (AI 재현율, 따로 보고)", "",
              f"- 참고: n {e2['lenient']['n']} · AI 반응 있음 {e2['lenient']['ai_positive']} · 재현율 {_f(e2['lenient']['recall'])}",
              f"- 엄격{_STATUS_KO.get(e2['strict']['status'], '')}: n {e2['strict'].get('n', 0)}"
              + (f" · 재현율 {_f(e2['strict'].get('recall'))}" if e2['strict']['status'] != STATUS_WITHHELD else ""), "",
              "## E3 미검증 (건수만, 'AI 확인 큐')", "",
              f"- AI 반응 있음 가운데 E1·E2 에 없는 채팅 {e3['count']}건 (다른 사람 라벨 있음 {e3['with_other_human_label']}, "
              f"없음 {e3['no_human_label']})", ""]
    seg = rep.get("segments") or {}
    if seg.get("status") == STATUS_NOT_PROVIDED:
        L += ["## 활동 구간", "", "엔진이 구간을 내지 않았다.", ""]
    else:
        L += ["## 활동 구간 (1초 칸)", ""]
        for kind, ko in (("lenient", "참고"), ("strict", "엄격")):
            m = seg[kind]
            if m["status"] == STATUS_WITHHELD:
                L.append(f"- {ko}: 보류")
                continue
            L.append(f"- {ko}{_STATUS_KO.get(m['status'], '')}: 칸 {m['cells']} · 시간 일치율 {_f(m['time_agreement'])} · "
                     f"κ {_f(m['kappa'])} · 저스트채팅 IoU {_f(m['jc_iou'])} · AI 미평가 칸 {m['ai_unevaluated_cells']}")
        bd = seg["boundaries"]
        L += [f"- 경계: 사람 {bd['human']} · AI {bd['ai']} · 짝 {bd['matched']} · |Δt| 중앙값 {_f(bd['median_sec'], 1)}초 · "
              f"완충 안 비율 {_f(bd['within_buffer_ratio'])}", ""]
    if rep.get("warnings"):
        L += ["## 경고", ""] + [f"- {w}" for w in rep["warnings"]] + [""]
    L += ["## 계산하지 않은 것", ""] + [f"- {x}" for x in rep.get("not_computed") or []] + [""]
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------- 엔진 판끼리 견주기
# 17 6-1 4번: 두 엔진 판(예: link v0.2 ↔ v0.3)의 다른 곳만 본다 — 같은 채팅에 다른 등급, 또는 한쪽만 반응 있음.
# 파일을 열지 않는다. 숫자·분류·등급·chat_id·시각·run_id 만 돌려준다(근거·본문 없음).
RUN_DIFF_VALUES = ("direct", "likely", "related_only", "unclear", "unconfirmed", "absent")
RD_SAME = "same"
RD_GRADE_DIFF = "grade_diff"      # 두 판 모두 등급이 있는데 다르다
RD_A_ONLY = "a_only"              # A 는 등급(반응 있음 쪽), B 는 반응 미확인이거나 그 채팅을 내지 않음
RD_B_ONLY = "b_only"
RD_KINDS = (RD_GRADE_DIFF, RD_A_ONLY, RD_B_ONLY)
_RD_GRADES = ("direct", "likely", "related_only", "unclear")


def _run_values(result: Mapping) -> tuple[dict[str, tuple[str, int | None]], dict]:
    """ai-result(1판 또는 engine/link 2판 초안) → chat_id → (값, 채팅 시각). 같은 chat_id 는 첫 줄만(06 6-2).
    graded 인데 등급이 없는 줄은 비교에서 뺀다(08 D-17 1). 값은 RUN_DIFF_VALUES 의 'absent' 밖 다섯 개."""
    out: dict[str, tuple[str, int | None]] = {}
    st = {"rows": 0, "duplicates": 0, "invalid": 0, "graded_no_grade": 0}
    rows = result.get("reactions") if isinstance(result, Mapping) else None
    for r in rows if isinstance(rows, list) else ():
        if not isinstance(r, Mapping) or not isinstance(r.get("chat_id"), str) or not r["chat_id"]:
            st["invalid"] += 1
            continue
        cid = r["chat_id"]
        if cid in out:
            st["duplicates"] += 1
            continue
        res, g = r.get("result"), r.get("grade")
        if res == "unconfirmed":
            v = "unconfirmed"
        elif res == "graded" and g in _RD_GRADES:
            v = g
        elif res == "graded":
            st["graded_no_grade"] += 1
            out[cid] = ("", None)              # 첫 줄 자리는 차지한다(뒤 중복이 대신 들어오지 않게)
            continue
        else:
            st["invalid"] += 1
            continue
        t = r.get("chat_t_ms")
        out[cid] = (v, t if _is_int(t) else None)
        st["rows"] += 1
    return {k: v for k, v in out.items() if v[0]}, st


def _human_values(rows: Iterable | None) -> dict[str, str]:
    """사람 최종 세트 행(export-row 모양) → chat_id → 값. 반응 행만, effective=false·conflict=true 는 뺀다.
    같은 chat_id 가 둘 이상이면 조정 값(source=adjudication)이 앞선다. 등급 없는 graded 는 뺀다."""
    out: dict[str, str] = {}
    adj: set[str] = set()
    for row in rows or ():
        if not isinstance(row, Mapping) or row.get("label_kind") != "reaction":
            continue
        if row.get("effective") is False or row.get("conflict") is True:
            continue
        cid = row.get("chat_id")
        if not isinstance(cid, str) or not cid:
            continue
        res, g = row.get("result"), row.get("grade")
        v = "unconfirmed" if res == "unconfirmed" else g if res == "graded" and g in _RD_GRADES else None
        if v is None:
            continue
        is_adj = row.get("source") == "adjudication"
        if cid in out and not (is_adj and cid not in adj):
            continue
        out[cid] = v
        if is_adj:
            adj.add(cid)
    return out


def _rd_kind(a: str, b: str) -> str | None:
    """두 판의 값 → 다른 곳 종류. 같으면 RD_SAME, 둘 다 반응 없음(미확인·없음)이면 None(다른 곳이 아님)."""
    if a == b:
        return RD_SAME
    ga, gb = a in _RD_GRADES, b in _RD_GRADES
    if ga and gb:
        return RD_GRADE_DIFF
    if ga:
        return RD_A_ONLY
    if gb:
        return RD_B_ONLY
    return None


def _engine_of(result: Mapping) -> dict:
    e = result.get("engine") if isinstance(result.get("engine"), Mapping) else {}
    return {"name": e.get("name") if isinstance(e.get("name"), str) else None,
            "version": e.get("version") if isinstance(e.get("version"), str) else None}


def diff_results(result_a: Mapping, result_b: Mapping, *, human: Iterable | Mapping | None = None,
                 chat_times: Mapping[str, int] | None = None) -> dict:
    """두 엔진 판의 판정을 chat_id 로 맞춰 다른 곳만 뽑는다 (17 6-1 4번). 파일을 열지 않는다.

    result_a·result_b: ai-result dict(봉인 해제된 것 — 해제 확인은 부르는 쪽 몫, compare_runs 는 직접 확인한다).
    human: 사람 최종 세트 행 목록(export-row 모양) 또는 chat_id → 값 dict. 주면 다른 곳마다 어느 판이 사람과 같은지 센다.
    chat_times: chat_id → 채팅 VOD 시각(ms). 없으면 각 판의 chat_t_ms.

    돌려주는 것:
      a·b: {run_id, engine{name,version}, rows, duplicates, invalid, graded_no_grade}
      counts: both · same · grade_diff · a_only · b_only · unpaired_no_reaction(한쪽 미확인·다른 쪽 없음 등) ·
              only_in_a · only_in_b(그 판에만 있는 채팅 수)
      table: A 값 → B 값 → 건수 (RUN_DIFF_VALUES, absent 끼리는 0)
      human: None 또는 {a_matches, b_matches, neither, no_human} — 다른 곳 가운데서만 센다
      items: 다른 곳 목록 [{chat_id, t_ms, kind, a, b, human, closer}] (시각 순, 시각 없는 것은 뒤).
             closer = 'a' · 'b' · 'neither'(둘 다 사람과 다름) · None(사람 판정 없음)
    두 판의 대상 영상(target.video_no)이 다르면 AICompareError.
    """
    if not isinstance(result_a, Mapping) or not isinstance(result_b, Mapping):
        raise AICompareError("견줄 두 판이 dict 가 아닙니다")
    va = (result_a.get("target") or {}).get("video_no") if isinstance(result_a.get("target"), Mapping) else None
    vb = (result_b.get("target") or {}).get("video_no") if isinstance(result_b.get("target"), Mapping) else None
    if va is not None and vb is not None and va != vb:
        raise AICompareError("두 판의 대상 영상(target.video_no)이 다릅니다")
    a, sa = _run_values(result_a)
    b, sb = _run_values(result_b)
    hv: dict[str, str] | None
    if human is None:
        hv = None
    elif isinstance(human, Mapping):
        hv = {k: v for k, v in human.items() if isinstance(k, str) and v in RUN_DIFF_VALUES and v != "absent"}
    else:
        hv = _human_values(human)
    table = {x: {y: 0 for y in RUN_DIFF_VALUES} for x in RUN_DIFF_VALUES}
    counts = {"both": 0, RD_SAME: 0, RD_GRADE_DIFF: 0, RD_A_ONLY: 0, RD_B_ONLY: 0, "unpaired_no_reaction": 0,
              "only_in_a": 0, "only_in_b": 0}
    hc = {"a_matches": 0, "b_matches": 0, "neither": 0, "no_human": 0} if hv is not None else None
    items: list[dict] = []
    for cid in set(a) | set(b):
        av_, at = a.get(cid, ("absent", None))
        bv_, bt = b.get(cid, ("absent", None))
        if cid in a and cid in b:
            counts["both"] += 1
        elif cid in a:
            counts["only_in_a"] += 1
        else:
            counts["only_in_b"] += 1
        table[av_][bv_] += 1
        kind = _rd_kind(av_, bv_)
        if kind is None:
            counts["unpaired_no_reaction"] += 1
            continue
        counts[kind] += 1
        if kind == RD_SAME:
            continue
        t = chat_times.get(cid) if chat_times is not None and _is_int(chat_times.get(cid)) else (at if at is not None else bt)
        h = hv.get(cid) if hv is not None else None
        closer = None
        if hc is not None:
            if h is None:
                hc["no_human"] += 1
            elif h == av_:
                closer = "a"
                hc["a_matches"] += 1
            elif h == bv_:
                closer = "b"
                hc["b_matches"] += 1
            else:
                closer = "neither"
                hc["neither"] += 1
        items.append({"chat_id": cid, "t_ms": t, "kind": kind, "a": av_, "b": bv_, "human": h, "closer": closer})
    items.sort(key=lambda it: (it["t_ms"] is None, it["t_ms"] if it["t_ms"] is not None else 0, it["chat_id"]))
    side = lambda res, st: {"run_id": res.get("run_id") if isinstance(res.get("run_id"), str) else None,  # noqa: E731
                            "engine": _engine_of(res), **st}
    return {"a": side(result_a, sa), "b": side(result_b, sb), "counts": counts, "table": table, "human": hc,
            "items": items}


def compare_runs(run_a: Mapping, run_b: Mapping, *, final=None, chat_times: Mapping[str, int] | None = None) -> dict:
    """봉인 해제된 두 run({result, sha256, sealed, unsealed} — load_unsealed_run 결과)의 다른 곳 (17 6-1 4번).

    두 run 모두 check_run(해제 기록·해시·06 6-2 형식)을 통과해야 한다. 아니면 RunNotUnsealedError·RunInvalidError —
    다른 쪽 내용을 보기 전에 멈춘다. 같은 run 둘이면 AICompareError.
    final: export.build_final_set 결과(또는 {items} dict). 주면 set=final 이고 eval.lenient 인 행을 사람 판정으로 쓴다
    (나란히 보기·참고 점수와 같은 모집단). 돌려주는 것: diff_results 결과 + a·b 에 sha256 앞 8자·해제 시각."""
    ia, ib = check_run(run_a), check_run(run_b)
    if ia["sha256"] == ib["sha256"] or ia["run_id"] == ib["run_id"]:
        raise AICompareError("같은 run 끼리는 견주지 않습니다")
    human = None
    if final is not None:
        items, _seg, _conf = _items_of(final)
        human = [it["row"] for it in items if it.get("set") == "final" and isinstance(it.get("row"), Mapping)
                 and ((it["row"].get("eval") or {}).get("lenient") is True)]
    out = diff_results(run_a["result"], run_b["result"], human=human, chat_times=chat_times)
    for key, info in (("a", ia), ("b", ib)):
        out[key].update({"sha8": info["sha256"][:8], "unsealed_at": info["unsealed_at"],
                         "presealed": info["presealed"]})
    return out


__all__ = [
    "RUN_DIFF_VALUES", "RD_SAME", "RD_GRADE_DIFF", "RD_A_ONLY", "RD_B_ONLY", "RD_KINDS", "diff_results", "compare_runs",
    "AICompareError", "RunNotUnsealedError", "RunInvalidError", "AICompareBlindError", "ReportResult",
    "check_run", "validate_result", "compare", "reaction_metrics", "segment_metrics", "cascade",
    "load_unsealed_run", "reader_block_reason", "build_report", "write_report", "make_report",
    "render_markdown", "render_tables", "ORDER", "LENIENT_OK", "REPORT_TYPE",
    "STATUS_OK", "STATUS_WITHHELD", "STATUS_GATE_UNKNOWN", "STATUS_NOT_PROVIDED",
]
