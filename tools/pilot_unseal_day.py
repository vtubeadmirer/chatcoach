"""시범 해제 당일을 한 번에 — 조건 확인 → 3편 해제 → AI 비교 보고서 → 엔진 판끼리 견주기 → 보정 재측정 → 요약
(08 D-22 2·6, D-21 1, D-17 4, D-15 8, D-16 클릭 한 번, 23 6장 '나', 06 7장).

하는 일 (차례대로, 사람 클릭 0 — Claude 가 이 도구를 돌린다)
  1. 시범 프로젝트 찾기: <projects 폴더>\\pilot-* (이름만 본다). --project 로 직접 줄 수도 있다. 기대 수(--expect, 기본 3)와 다르면
     조건 미충족(pilot_count).
  2. 조건 확인(06 7-1, D-17 4): 프로젝트마다 seal.unseal_conditions — 1차·재라벨 세션이 모두 잠김, 1차마다 재라벨 계획,
     계획마다 잠긴 재라벨 세션, 표본·계획 재현 검증. **잠금·계획·표본 매니페스트·project.json 만 연다**(라벨 파일·봉인 폴더는
     열지 않는다). 한 편이라도 안 되면 **아무것도 풀지 않고** 종료 3. 라벨 형식 고정(tools/pilot_freeze --check 와 같은 비교)은
     참고로만 적는다(막지 않음).
  3. 해제: 프로젝트마다 seal_status 의 봉인(검사 valid, 아직 안 푼 것)을 모두 seal.unseal 로 푼다(짝 주제 흐름·전사 낱말·
     벡터는 seal 이 같이 푼다). 해제는 되돌릴 수 없으므로 조건을 모두 본 뒤에만. 푼 뒤 tools/ai_seal.verify_project 로
     결과·짝 파일을 해제 기록 해시와 대조한다(어긋나면 오류 'verify:unsealed_files', 종료 1). 유효한 봉인이 풀리지 않으면
     (seal.unseal 의 SealError) 건수만큼 오류 'unseal:<원인 코드>'(seal.SEAL_ERROR_CODES — 예 'unseal:dup_run_id'·
     'unseal:seal_jsonl_corrupt'), seal.jsonl 에 문제(끝 줄 잘림 등)가 있으면 오류
     'seal_jsonl:problems' — 둘 다 종료 1(해제 실패가 '오류 0' 으로 가려지지 않게). 해제 당일 입구는 이 도구
     하나다 — ai_seal 'unseal --all'·'verify' 는 같은 seal.unseal·대조를 프로젝트 하나에 손으로 할 때 쓴다.
  4. AI 비교 보고서: 해제된 run 마다(엔진 v0.2·v0.3 모두) ai_compare.make_report. 최종 세트는 프로젝트마다 한 번 만든다
     (export.build_final_set). 사람 일치도 기준은 최신 일치도 보고서에서(analysis_flow.agreement_gate). 보고서가 없으면
     1차 ↔ 재라벨마다 이 도구가 만든다(요약 폴더 agreement\\, 사람 클릭 0). 그래도 못 만들면 '기준 미확인'(막지 않음).
  5. 엔진 판끼리 견주기: run 을 엔진 판 순서로 놓고 이웃한 두 판마다 ai_compare.compare_runs(사람 = 최종 세트). 건수만 남긴다.
  6. 보정 재측정: tools/viewer_score_calib.calibrate(final_set = 그 최종 세트, 직접 반응만 — D-22 1) 프로젝트마다 + 묶음.
  7. 요약: <보고서 폴더>\\pilot-unseal-<YYYYMMDD>\\ 에 summary.md · summary.json(**숫자·코드·ID 만**), calib-*.json,
     ai-compare\\<project_id>\\<report_id>\\(AI 비교 보고서 — 06 6-7 규칙).

기본은 dry-run 이다: --go 를 주지 않으면 1·2 만 하고 아무것도 풀지도 쓰지도 않는다(--dry-run 은 그 뜻을 밝혀 적는 것).
  되돌릴 수 없는 실제 해제는 --go 를 줄 때만 — 해제 당일 차례는 'dry-run → 사용자 세션의 시작 확인 → --go'(D-17 4).
실제 데이터 폴더(chatcoach-data\\…)를 가리키려면 --real 이 있어야 한다. 기본 폴더는 없다: --projects-root·--project
  가운데 하나를 주거나 --real(= chatcoach-data\\projects). 실제 폴더를 --real 없이 가리키면 **아무것도 열기 전에** 종료 2.
  사용자가 시범 라벨링 중인 동안 실제 시범 폴더로는 dry-run 도 돌리지 않는다(리허설은 합성 pilot-* 로만).
--dry-run: 1·2 만 하고 아무것도 풀지도 쓰지도 않는다. 결과는 표준 출력에만(잠긴 세션 수·조건 미충족 사유 코드).
  시범 폴더는 읽기만 한다. **열기 감시**(OpenWatch): 시범 프로젝트 안에서는 project.json·locks\\·relabel\\·samples\\ 와
  솔트 파일 chat\\protected\\salt.json(open_project 가 솔트 짝을 맞춰 봄 — 채팅·라벨 아님)만 읽기로 열 수 있고, 그 밖
  (labels\\·chat\\messages·derived\\ 등)을 열거나 어디든 쓰려 하면 열기 전에 막고(WatchViolation) 종료 1.
  AI 결과 폴더·보고서 폴더는 dry-run 에서 아예 열지 않는다.
실제 실행에서도 감시는 켜 둔다: 시범 프로젝트 폴더 안에는 **아무것도 쓰지 않는다**(쓰기는 AI 결과 폴더·요약 폴더만).

출력·로그·요약에는 채팅 본문·닉네임·전사문·낱말을 넣지 않는다. 숫자·코드·ID(run_id·report_id·세션 수)만.

사용(저장소 루트, 앱 .venv)
  python tools/pilot_unseal_day.py --projects-root <합성 data\\projects>              (리허설 dry-run)
  python tools/pilot_unseal_day.py --projects-root <합성 data\\projects> --go         (리허설 해제)
  python tools/pilot_unseal_day.py --real                  (해제 당일: 세션이 모두 잠긴 뒤 조건만 — dry-run)
  python tools/pilot_unseal_day.py --real --go             (해제 당일: 사용자 세션이 '시작' 을 확인한 뒤)
  그 밖: [--project <폴더> ...] [--expect 3] [--ai-root <폴더>] [--out-root <data\\reports>] [--by L01]
         [--date YYYYMMDD] [--quick] [--json] [--freeze <freeze.json>]
종료 코드: 0 정상(dry-run 이면 조건 충족) · 3 조건 미충족(아무것도 풀지 않음) · 1 실패(감시 위반·해제 실패·단계 오류 —
           요약은 남김) · 2 입력 오류(실제 폴더인데 --real 없음 · 폴더를 안 줌 · --go 와 --dry-run 을 같이 줌)
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import builtins
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TOOL = "pilot_unseal_day"
TOOL_VERSION = "0.1"
DATA_DIR = os.path.join(os.path.dirname(ROOT), "chatcoach-data")
DEFAULT_PROJECTS_ROOT = os.path.join(DATA_DIR, "projects")
DEFAULT_FREEZE = os.path.join(DATA_DIR, "pilot", "freeze.json")
DEFAULT_EXPECT = 3                       # 시범 영상 3편 (D-15 1)
PILOT_PREFIX = "pilot-"
DAY_PREFIX = "pilot-unseal-"
USER_CLICKS = 0                          # 해제 당일 사람 클릭 (D-16) — 이 도구는 Claude 가 돌린다
#: dry-run·조건 확인 때 시범 프로젝트 안에서 읽어도 되는 자리(06 7-1 조건에 필요한 것만, 라벨 파일 없음)
CONDITION_READS = ("project.json", "locks", "relabel", "samples")
#: 그 밖에 읽어도 되는 파일 하나: project.open_project 가 솔트 짝(salt_id)을 맞춰 보느라 여는 솔트 파일(채팅·라벨 아님)
CONDITION_FILES = (os.path.join("chat", "protected", "salt.json"),)
LABELER_RE = re.compile(r"\A[A-Z][A-Z0-9]{1,7}\Z")
DATE_RE = re.compile(r"\A\d{8}\Z")

# 조건 미충족 사유 코드 (seal.unseal_conditions 의 문장 → 코드, 내용 없음)
R_PILOT_COUNT = "pilot_count"
R_UNLOCKED = "unlocked_sessions"
R_NO_PRIMARY = "no_primary"
R_NO_PLAN = "no_relabel_plan"
R_RELABEL_PENDING = "relabel_not_locked"
R_SAMPLE_VERIFY = "sample_verify"
R_OTHER = "other"
R_WATCH = "open_watch"


class DayError(RuntimeError):
    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


# ───────────────────────────────────────────────────────────── 열기 감시

class WatchViolation(PermissionError):
    """감시 규칙에 맞지 않는 열기(열기 전에 막는다). 메시지는 코드뿐."""


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _under(p: str, root: str) -> bool:
    r = root.rstrip("\\/")
    return p == r or p.startswith(r + os.sep)


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


@dataclass
class OpenWatch:
    """builtins.open · io.open · os.open 을 감싸 규칙 밖의 열기를 **열기 전에** 막는다(dry-run 과 실제 실행 모두).

    guarded: 시범 프로젝트 폴더들. conditions_only=True 면 그 안에서 CONDITION_READS 만 읽기로 열 수 있다.
    deny: 아예 열지 않는 폴더(dry-run 의 AI 결과·보고서 폴더). write_ok: 쓰기를 허용하는 폴더(None = 어디도 쓰지 않음).
    기록(counts·violations)은 코드와 건수뿐이다."""
    guarded: Sequence[str]
    conditions_only: bool = True
    deny: Sequence[str] = ()
    write_ok: Sequence[str] | None = None
    counts: dict = field(default_factory=lambda: {"guarded_reads": 0, "other_opens": 0, "writes": 0})
    violations: list = field(default_factory=list)

    def __post_init__(self):
        self._g = [_norm(x) for x in self.guarded]
        self._d = [_norm(x) for x in self.deny]
        self._w = None if self.write_ok is None else [_norm(x) for x in self.write_ok]
        self._files = {os.path.normcase(f) for f in CONDITION_FILES}
        self._saved = None

    def check(self, path, write: bool) -> None:
        try:
            p = _norm(os.fspath(path))
        except TypeError:
            return                                    # 파일 기술자 등
        for d in self._d:
            if _under(p, d):
                self._deny("open_denied_root")
        for g in self._g:
            if _under(p, g):
                if write:
                    self._deny("write_in_pilot_project")
                if self.conditions_only:
                    rel = os.path.relpath(p, g)
                    head = rel.split(os.sep, 1)[0]
                    if head not in CONDITION_READS and rel not in self._files:
                        self._deny("read_outside_conditions:" + head.split(".", 1)[0][:16])
                self.counts["guarded_reads"] += 1
                return
        if write:
            if self._w is None or not any(_under(p, w) for w in self._w):
                self._deny("write_outside_allowed")
            self.counts["writes"] += 1
        else:
            self.counts["other_opens"] += 1

    def _deny(self, code: str):
        self.violations.append(code)
        raise WatchViolation(code)

    @staticmethod
    def _mode_writes(mode) -> bool:
        return isinstance(mode, str) and any(c in mode for c in "wax+")

    def __enter__(self) -> "OpenWatch":
        real_open, real_io_open, real_os_open = builtins.open, io.open, os.open
        self._saved = (real_open, real_io_open, real_os_open)
        watch = self

        def w_open(file, mode="r", *a, **kw):
            if not isinstance(file, int):
                watch.check(file, watch._mode_writes(kw.get("mode", mode)))
            return real_open(file, mode, *a, **kw)

        def w_io_open(file, mode="r", *a, **kw):
            if not isinstance(file, int):
                watch.check(file, watch._mode_writes(kw.get("mode", mode)))
            return real_io_open(file, mode, *a, **kw)

        def w_os_open(path, flags, *a, **kw):
            watch.check(path, bool(flags & _WRITE_FLAGS))
            return real_os_open(path, flags, *a, **kw)
        builtins.open, io.open, os.open = w_open, w_io_open, w_os_open
        return self

    def __exit__(self, *exc) -> None:
        builtins.open, io.open, os.open = self._saved
        self._saved = None

    def summary(self) -> dict:
        codes: dict[str, int] = {}
        for v in self.violations:
            codes[v] = codes.get(v, 0) + 1
        return {**self.counts, "violations": len(self.violations), "violation_codes": dict(sorted(codes.items()))}


# ───────────────────────────────────────────────────────────── 1·2. 찾기·조건

def find_pilots(projects_root: str) -> list[str]:
    """<projects>\\pilot-* 가운데 project.json 이 있는 폴더(이름·stat 만 본다)."""
    try:
        names = sorted(os.listdir(projects_root))
    except OSError:
        raise DayError("시범 프로젝트 폴더를 읽지 못했습니다(--projects-root)") from None
    out = []
    for n in names:
        p = os.path.join(projects_root, n)
        if n.lower().startswith(PILOT_PREFIX) and os.path.isdir(p) and os.path.isfile(os.path.join(p, "project.json")):
            out.append(os.path.abspath(p))
    return out


def reason_code(text: str) -> str:
    """seal.unseal_conditions 의 사유 문장 → 코드(세션·계획 ID 를 남기지 않는다)."""
    t = str(text)
    if t.startswith("잠기지 않은 라벨링 세션"):
        return R_UNLOCKED
    if t.startswith("1차 세션이 없습니다"):
        return R_NO_PRIMARY
    if t.startswith("재라벨 계획이 없는 1차 세션"):
        return R_NO_PLAN
    if t.startswith("잠긴 재라벨 세션이 없는 계획"):
        return R_RELABEL_PENDING
    if "재현 검증" in t:
        return R_SAMPLE_VERIFY
    return R_OTHER


def check_conditions(root: str) -> dict:
    """프로젝트 하나의 06 7-1 조건 → {name, labeling_sessions, locked, met, reasons[코드], gate_warnings}.
    잠금·계획·매니페스트·project.json 만 연다(seal.unseal_conditions). 라벨 파일·AI 결과 폴더는 열지 않는다."""
    from app import seal
    out = {"name": os.path.basename(root), "labeling_sessions": 0, "locked": 0, "met": False, "reasons": [],
           "gate_warnings": 0}
    try:
        cond = seal.unseal_conditions(root)
    except WatchViolation as e:
        out["reasons"] = [R_WATCH]
        out["watch"] = str(e)
        return out
    except Exception as e:  # noqa: BLE001 — 깨진 잠금 등은 '조건 안 됨'(블라인드 쪽으로 안전하게)
        out["reasons"] = [f"error:{type(e).__name__}"]
        return out
    sids = list(cond.labeling_sessions)
    out["labeling_sessions"] = len(sids)
    out["locked"] = sum(1 for s in sids if os.path.isfile(os.path.join(root, "locks", f"{s}.json")))
    out["met"] = bool(cond.met)
    out["reasons"] = sorted({reason_code(r) for r in cond.reasons})
    out["gate_warnings"] = len(cond.warnings)
    return out


def freeze_state(freeze_path: str | None) -> dict:
    """라벨 형식 고정 기록과 지금 저장소의 비교(tools/pilot_freeze --check 와 같은 비교). 막지 않는다 — 참고."""
    if not freeze_path or not os.path.isfile(freeze_path):
        return {"state": "missing", "diffs": 0}
    try:
        from app.core.validate import load_json_strict
        from tools import pilot_freeze as pf
        with open(freeze_path, "rb") as f:
            frozen = load_json_strict(f.read(), pf.MAX_JSON_BYTES)
        diffs = [d for d in pf.compare(frozen, pf.collect(ROOT)) if not d.startswith("app_version")]
    except Exception as e:  # noqa: BLE001
        return {"state": f"error:{type(e).__name__}", "diffs": 0}
    return {"state": "ok" if not diffs else "diff", "diffs": len(diffs)}


def conditions(roots: Sequence[str], *, expect: int | None) -> dict:
    per = [check_conditions(r) for r in roots]
    glob = []
    if expect is not None and len(roots) != expect:
        glob.append(R_PILOT_COUNT)
    if not roots:
        glob.append(R_PILOT_COUNT)
    met = bool(roots) and not glob and all(p["met"] for p in per)
    kinds = sorted(set(glob) | {c for p in per for c in p["reasons"]})
    return {"met": met, "projects": per, "global_reasons": sorted(set(glob)), "reason_kinds": kinds,
            "labeling_sessions": sum(p["labeling_sessions"] for p in per), "locked": sum(p["locked"] for p in per)}


# ───────────────────────────────────────────────────────────── 3. 해제

def unseal_project(root: str, *, by: str, ai_root: str | None = None) -> dict:
    """봉인(검사 valid, 아직 안 푼 것)을 모두 푼다. 숫자·코드만 돌려준다."""
    from app import seal
    st = seal.seal_status(root, ai_root=ai_root)
    out = {"sealed_valid": 0, "rejected": 0, "already": 0, "unsealed_now": 0, "failed": [], "presealed": 0,
           "topics": 0, "words": 0, "vectors": 0, "pair_errors": [], "seal_problems": len(st.problems)}
    for e in st.entries:
        if e.check != "valid":
            out["rejected"] += 1
            continue
        out["sealed_valid"] += 1
        if e.unsealed_at is not None:
            out["already"] += 1
            continue
        try:
            r = seal.unseal(root, e.sha256, by=by, ai_root=ai_root)
        except seal.SealError as x:
            out["failed"].append(seal.error_code(x))          # 원인 코드(seal.SEAL_ERROR_CODES, 예: dup_run_id)
            continue
        out["unsealed_now"] += 1
        out["presealed"] += int(bool(r.presealed))
        rec = r.record
        for k in seal.PAIR_KINDS:
            if isinstance(rec.get(k), Mapping):
                out[k] += 1
            err = rec.get(f"{k}_error")
            if isinstance(err, str):
                out["pair_errors"].append(err)
    out["verify"] = verify_unsealed(root, ai_root=ai_root)
    return out


def verify_unsealed(root: str, *, ai_root: str | None = None) -> dict:
    """해제된 run 마다 결과·짝 파일을 해제 기록의 해시와 대조해 읽어 본다(tools/ai_seal.verify_project — 같은 검사를 두
    도구가 따로 두지 않는다). → {runs, ok, codes{값: 건수}, engine_versions[]}. 내용은 남기지 않는다."""
    from tools import ai_seal
    rows, ok = ai_seal.verify_project(root, ai_root=ai_root)
    codes: dict[str, int] = {}
    for row in rows:
        for k in ("run",) + tuple(ai_seal.seal.PAIR_KINDS):
            v = f"{k}:{row.get(k)}"
            codes[v] = codes.get(v, 0) + 1
    vers = sorted({str(r.get("engine_version")) for r in rows if r.get("engine_version")})
    return {"runs": len(rows), "ok": bool(ok), "codes": dict(sorted(codes.items())), "engine_versions": vers}


# ───────────────────────────────────────────────────────────── 4·5. AI 비교 · 판끼리

def engine_version(result: Mapping | None) -> str | None:
    e = (result or {}).get("engine") if isinstance(result, Mapping) else None
    v = e.get("version") if isinstance(e, Mapping) else None
    return v if isinstance(v, str) else None


def version_key(v: str | None) -> tuple:
    if not v:
        return (1, ())
    nums = tuple(int(x) for x in re.findall(r"\d+", v)[:4])
    return (0, nums)


#: engine/link.py 의 ENGINE_VERSION — v0.2 규칙 결과가 싣는 값(판 번호를 올리지 않았다, link.py 48행). 'v0.1' 로 적으면
#: 요약을 읽는 쪽이 v0.2 결과가 없다고 오해하므로 이름을 따로 붙인다(tests 가 link.py 상수와 같은지 본다)
LINK_RULES_VERSION = "0.1.0"


def version_label(v: str | None) -> str:
    """'0.3.1' → 'v0.3', 'link-v0.3' → 'v0.3', '0.1.0'(engine/link.py 의 v0.2 규칙 판) → 'v0.2(0.1.0)', 모르면 'v?'."""
    if v == LINK_RULES_VERSION:
        return f"v0.2({LINK_RULES_VERSION})"
    n = re.findall(r"\d+", v or "")
    return f"v{n[0]}.{n[1]}" if len(n) >= 2 else "v?"


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def report_numbers(rep: Mapping) -> dict:
    """AI 비교 보고서 → 요약 숫자(06 6-7). 채팅 단위 값은 넣지 않는다."""
    out: dict = {"report_id": rep.get("report_id"),
                 "pre_sealed": (rep.get("header") or {}).get("pre_sealed"),
                 "conditions_met": (rep.get("header") or {}).get("conditions_met")}
    e1 = rep.get("e1") or {}
    for kind in ("lenient", "strict"):
        m = e1.get(kind) or {}
        g, b = m.get("grade") or {}, m.get("binary") or {}
        out[f"e1_{kind}"] = {"status": m.get("status"), "n": _num(m.get("n")), "exact": _num(g.get("exact")),
                             "kappa_w": _num(g.get("kappa_w")), "precision": _num(b.get("precision")),
                             "recall": _num(b.get("recall")), "f1": _num(b.get("f1")), "kappa": _num(b.get("kappa"))}
    e2 = (rep.get("e2") or {}).get("lenient") or {}
    out["e2_lenient"] = {"n": _num(e2.get("n")), "recall": _num(e2.get("recall"))}
    out["e3"] = _num((rep.get("e3") or {}).get("count"))
    seg = rep.get("segments") or {}
    sl = seg.get("lenient") or {}
    out["segments"] = {"status": seg.get("status"), "cells": _num(sl.get("cells")),
                       "time_agreement": _num(sl.get("time_agreement")), "kappa": _num(sl.get("kappa"))}
    return out


def agreement_reports(root: str, info: Mapping, *, out_dir: str, by: str) -> tuple[int, list[str]]:
    """최종 세트의 1차 ↔ 재라벨 쌍마다 일치도 보고서(app.agreement_report.make_report)를 out_dir 에 만든다 → (만든 수,
    오류 코드). comparison_gate 는 make_report 안에서 본다(조건은 이미 충족). 내용은 돌려주지 않는다."""
    from app import agreement_report as ar
    primary = info.get("primary_session_id")
    relabels = [s for s in info.get("relabel_session_ids") or () if isinstance(s, str)]
    if not isinstance(primary, str) or not relabels:
        return 0, []
    made, errs = 0, []
    for rs in relabels:
        try:
            ar.make_report(root, primary, rs, out_root=out_dir, created_by=by)
            made += 1
        except Exception as e:  # noqa: BLE001 — 기준이 없으면 '기준 미확인' 으로 남고 비교는 계속
            errs.append(f"agreement:{type(e).__name__}")
    return made, errs


def compare_project(root: str, *, out_dir: str, by: str, ai_root: str | None = None):
    """(최종 세트, 숫자 dict). 해제된 run 마다 보고서, 엔진 판 이웃끼리 견주기."""
    from app import ai_compare as ac
    from app import analysis_flow as af
    from app import export, seal
    fx = export.build_final_set(root, ai_root=ai_root)
    gate, gate_id = af.agreement_gate(root, fx.info, reader_kind=None)
    made, agree_err = 0, []
    if gate_id is None:
        # 사람 일치도 보고서가 없으면 여기서 만든다(사람 클릭 0) — 요약 폴더 agreement\\ 아래, 기준은 거기서 읽는다
        made, agree_err = agreement_reports(root, fx.info, out_dir=os.path.join(out_dir, "agreement"), by=by)
        if made:
            gate, gate_id = af.agreement_gate(root, fx.info, reader_kind=None,
                                              reports_root=os.path.join(out_dir, "agreement"))
    runs = af.unsealed_run_ids(seal.seal_status(root, ai_root=ai_root))
    out = {"runs": [], "pairs": [], "human_gate": gate, "gate_report_id": gate_id, "errors": [],
           "agreement_made": made, "agreement_errors": agree_err,       # 막지 않는다 — 기준 미확인으로 남음
           "final_set": {"items": len(fx.items), "segment_items": len(fx.segment_items),
                         "conflicts": len(fx.conflicts), "warnings": len(fx.info.get("warnings") or ())}}
    loaded = []
    for rid in runs:
        try:
            run = ac.load_unsealed_run(root, rid, ai_root=ai_root)
            res = ac.make_report(root, run, out_root=os.path.join(out_dir, "ai-compare"), final=fx,
                                 human_gate=gate, created_by=by, ai_root=ai_root)
        except Exception as e:  # noqa: BLE001 — 한 run 의 실패로 다른 run 을 멈추지 않는다
            out["errors"].append(f"report:{type(e).__name__}")
            continue
        v = engine_version(run["result"])
        loaded.append((version_key(v), rid, run))
        out["runs"].append({"run_id": rid, "engine_version": v, "label": version_label(v),
                            "warnings": len(res.warnings), **report_numbers(res.report)})
    loaded.sort(key=lambda x: (x[0], x[1]))
    for (ka, ra, a), (kb, rb, b) in zip(loaded, loaded[1:]):
        try:
            d = ac.compare_runs(a, b, final=fx)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"pair:{type(e).__name__}")
            continue
        out["pairs"].append({"a": {"run_id": ra, "label": version_label(engine_version(a["result"]))},
                             "b": {"run_id": rb, "label": version_label(engine_version(b["result"]))},
                             "counts": dict(d["counts"]), "human": d.get("human"),
                             "table": {x: dict(y) for x, y in d["table"].items()}})
    return fx, out


# ───────────────────────────────────────────────────────────── 6. 보정

def calib_project(root: str, fx, *, ai_root: str | None, quick: bool) -> dict:
    from tools import viewer_score_calib as cal
    return cal.calibrate(root, final_set=fx, ai_root=ai_root, quick=quick)


def calib_numbers(rep: Mapping) -> dict:
    """보정 결과 → 요약 숫자(base 판 세 축 + 후보)."""
    from app.core import viewer_score as vs
    from tools import viewer_score_calib as cal
    base = (rep.get("variants") or {}).get("base") or {}
    g = base.get("groups") or {}
    axes = {}
    for axis in vs.AXES:
        r, o, n = (g.get(cal.G_REACTED) or {}).get(axis) or {}, (g.get(cal.G_OTHERS) or {}).get(axis) or {}, \
            (g.get(cal.G_NONE) or {}).get(axis) or {}
        c = (base.get("reacted_vs_others") or {}).get(axis) or {}
        cn = (base.get("reacted_vs_none") or {}).get(axis) or {}
        axes[axis] = {"reacted_good_pct": r.get("good_pct"), "others_good_pct": o.get("good_pct"),
                      "none_good_pct": n.get("good_pct"), "good_diff_pp": c.get("good_diff_pp"),
                      "fisher_p_good": c.get("fisher_p_good"), "good_lift": c.get("good_lift"),
                      "good_precision": c.get("good_precision"), "none_good_lift": cn.get("good_lift"),
                      "none_fisher_p_good": cn.get("fisher_p_good")}
    cands = {a: {"variant": c.get("variant"), "good_lift": c.get("good_lift"), "base_good_lift": c.get("base_good_lift"),
                 "reacted_good": c.get("reacted_good"), "others_good": c.get("others_good")}
             for a, c in (rep.get("candidates") or {}).items()}
    inp = rep.get("input") or {}
    return {"groups": dict(inp.get("groups") or {}), "words": inp.get("words"), "topic_blocks": inp.get("topic_blocks"),
            "transcript_windows": inp.get("transcript_windows"), "context_run": inp.get("context_run"),
            "axes": axes, "candidates": cands}


# ───────────────────────────────────────────────────────────── 7. 요약

def day_dir(out_root: str, date: str) -> str:
    base = os.path.join(out_root, DAY_PREFIX + date)
    d, k = base, 2
    while os.path.exists(d):
        d = f"{base}-{k}"
        k += 1
    return d


def _f(v, nd=3) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def verify_text(v: Mapping | None) -> str:
    """해제 뒤 대조 → 'ok 2건' · '실패 1건' · '-'(대조할 해제 run 이 없음 — 'ok 0건' 으로 성공처럼 보이지 않게)."""
    v = v or {}
    if not v.get("runs"):
        return "-"
    return f"{'ok' if v.get('ok') else '실패'} {v.get('runs')}건"


def render_markdown(s: Mapping) -> str:
    """요약 → markdown(숫자·코드·ID 만)."""
    L = [f"# 시범 해제 당일 요약 — {s['date']}", "",
         f"도구 {s['tool']} {s['tool_version']} · 사람 클릭 **{s['user_clicks']}** · 걸린 시간 {_f(s.get('elapsed_s'), 1)}초"
         f" · 라벨 형식 고정 {s['freeze']['state']}(다른 곳 {s['freeze']['diffs']})", ""]
    c = s["conditions"]
    L += ["## 1. 조건 (06 7-1)", "", f"충족 **{c['met']}** · 라벨링 세션 {c['labeling_sessions']} · 잠김 {c['locked']}"
          f" · 사유 {', '.join(c['reason_kinds']) or '없음'}", "",
          "| 프로젝트 | 라벨링 세션 | 잠김 | 충족 | 사유 |", "|---|---|---|---|---|"]
    for p in c["projects"]:
        L.append(f"| {p['name']} | {p['labeling_sessions']} | {p['locked']} | {p['met']} | {', '.join(p['reasons']) or '-'} |")
    L.append("")
    if s.get("projects"):
        L += ["## 2. 해제", "", "| 프로젝트 | 봉인(유효) | 이번에 해제 | 이미 | 거부 | 실패 | 사전 봉인 | 주제 흐름 | 전사 낱말 | 벡터 |"
              " 대조(ai_seal verify) | 엔진 판 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for p in s["projects"]:
            u = p.get("unseal") or {}
            vf_ = u.get("verify") or {}
            L.append(f"| {p['name']} | {u.get('sealed_valid')} | {u.get('unsealed_now')} | {u.get('already')} | "
                     f"{u.get('rejected')} | {len(u.get('failed') or ())} | {u.get('presealed')} | {u.get('topics')} | "
                     f"{u.get('words')} | {u.get('vectors')} | {verify_text(vf_)} | "
                     f"{', '.join(vf_.get('engine_versions') or ()) or '-'} |")
        L += ["", "## 3. AI 비교 (E1 = 표본, 참고/엄격)", ""]
        for p in s["projects"]:
            cm = p.get("compare") or {}
            if cm:
                g = cm.get("human_gate")
                L.append(f"- {p['name']}: 사람 일치도 기준 {cm.get('gate_report_id') or '미확인'}"
                         f" (반응 {(g or {}).get('reaction', '-')} · 구간 {(g or {}).get('segment', '-')})"
                         f" · 이번에 만든 일치도 보고서 {cm.get('agreement_made', 0)}"
                         f"{' · 오류 ' + ', '.join(cm['agreement_errors']) if cm.get('agreement_errors') else ''}")
        L += ["",
              "| 프로젝트 | 판 | run | 보고서 | 사전 봉인 | E1 참고 n | 정확 | κ_w | 정밀도 | 재현율 | F1 | κ | E1 엄격 n | κ_w | F1 |"
              " E2 재현율 | E3 | 구간 일치 | 구간 κ |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for p in s["projects"]:
            for r in (p.get("compare") or {}).get("runs") or ():
                le, st = r["e1_lenient"], r["e1_strict"]
                L.append(f"| {p['name']} | {r['label']} | {r['run_id']} | {r['report_id']} | {r['pre_sealed']} | "
                         f"{_f(le['n'])} | {_f(le['exact'])} | {_f(le['kappa_w'])} | {_f(le['precision'])} | "
                         f"{_f(le['recall'])} | {_f(le['f1'])} | {_f(le['kappa'])} | {_f(st['n'])} | {_f(st['kappa_w'])} | "
                         f"{_f(st['f1'])} | {_f(r['e2_lenient']['recall'])} | {_f(r['e3'])} | "
                         f"{_f(r['segments']['time_agreement'])} | {_f(r['segments']['kappa'])} |")
        L += ["", "## 4. 엔진 판끼리 (다른 곳만, 사람 = 최종 세트)", "",
              "| 프로젝트 | A | B | 둘 다 | 같음 | 등급 다름 | A만 | B만 | 사람이 A | 사람이 B | 둘 다 아님 | 사람 없음 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for p in s["projects"]:
            for d in (p.get("compare") or {}).get("pairs") or ():
                k, h = d["counts"], d.get("human") or {}
                L.append(f"| {p['name']} | {d['a']['label']} | {d['b']['label']} | {k.get('both')} | {k.get('same')} | "
                         f"{k.get('grade_diff')} | {k.get('a_only')} | {k.get('b_only')} | {h.get('a_matches', '-')} | "
                         f"{h.get('b_matches', '-')} | {h.get('neither', '-')} | {h.get('no_human', '-')} |")
        L += ["", "## 5. 보정 재측정 (직접 반응만, base 판, good %)", ""]
        for p in s["projects"]:
            cb = p.get("calib")
            if cb:
                L.append(f"- {p['name']}: 맥락 run {cb.get('context_run') or '-'} · 전사 낱말 {cb.get('words') or '-'}"
                         f" · 주제 덩어리 {cb.get('topic_blocks')} · 전사 창 {cb.get('transcript_windows')}")
        L += ["",
              "| 묶음 | 반응받음 | 반응 없음 | 확인된 반응 없음 | 축 | 반응받음 | 반응 없음 | 확인된 없음 | 차이 %p | p | lift | 정밀도 | 후보 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        rows = [(p["name"], p.get("calib")) for p in s["projects"]] + [("묶음", s.get("calib_pooled"))]
        for name, cb in rows:
            if not cb:
                continue
            g = cb.get("groups") or {}
            for axis, a in (cb.get("axes") or {}).items():
                cand = (cb.get("candidates") or {}).get(axis) or {}
                L.append(f"| {name} | {g.get('reacted')} | {g.get('others')} | {g.get('labelled_none')} | {axis} | "
                         f"{_f(a['reacted_good_pct'], 1)} | {_f(a['others_good_pct'], 1)} | {_f(a['none_good_pct'], 1)} | "
                         f"{_f(a['good_diff_pp'], 1)} | {_f(a['fisher_p_good'], 4)} | {_f(a['good_lift'], 2)} | "
                         f"{_f(a['good_precision'], 3)} | {cand.get('variant', '-')} |")
        L.append("")
    errs = [e for p in s.get("projects") or () for e in p.get("errors") or ()]
    if errs or s.get("errors"):
        L += ["## 오류 (코드)", ""] + [f"- {e}" for e in list(s.get("errors") or ()) + errs] + [""]
    L += ["## 열기 감시", ""]
    for title, key in (("조건 확인(시범 폴더는 잠금·계획·매니페스트·project.json 만)", "watch"),
                       ("해제 뒤 실행(시범 폴더에 쓰지 않음)", "watch_run")):
        w = s.get(key)
        if w:
            L.append(f"- {title}: 시범 폴더 읽기 {w.get('guarded_reads')} · 다른 열기 {w.get('other_opens')} · "
                     f"쓰기 {w.get('writes')} · 위반 {w.get('violations')}")
    L.append("")
    L += ["채팅 본문·닉네임·전사문·낱말은 이 요약에 없다. AI 비교 보고서는 ai-compare\\ 아래(06 6-7).", ""]
    return "\n".join(L)


def _write(path: str, data: bytes) -> None:
    from app.project import atomic_write
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write(path, data)


def _jbytes(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


# ───────────────────────────────────────────────────────────── 한 번에

def touches_real_data(paths: Sequence[str | None]) -> bool:
    """경로 가운데 실제 데이터 폴더(DATA_DIR = 저장소 옆 chatcoach-data) 아래가 있나. 글자만 견준다(아무것도 열지 않음)."""
    real = _norm(DATA_DIR)
    return any(p and _under(_norm(p), real) for p in paths)


def unseal_errors(u: Mapping) -> list[str]:
    """unseal_project 결과 → 오류 코드(풀리지 않은 유효 봉인 건수만큼 'unseal:<원인 코드>' — seal.error_code, seal.jsonl 문제
    'seal_jsonl:problems').
    이 코드가 있으면 run_day 는 종료 1 — 해제가 실패했는데 '오류 0' 으로 보이지 않게(앱에는 '봉인됨' 으로 남는다)."""
    out = [f"unseal:{f}" for f in u.get("failed") or ()]
    if u.get("seal_problems"):
        out.append("seal_jsonl:problems")
    return out


def run_day(roots: Sequence[str], *, out_root: str, by: str = "L01", ai_root: str | None = None,
            expect: int | None = DEFAULT_EXPECT, dry_run: bool = False, date: str | None = None,
            freeze_path: str | None = None, quick: bool = False, allow_real: bool = False) -> tuple[int, dict]:
    """(종료 코드, 요약 dict). dry_run 이면 조건만 보고 아무것도 쓰지 않는다.
    allow_real=False 면 roots·ai_root·out_root·freeze_path 가운데 실제 데이터 폴더 아래가 있을 때 **아무것도 열기 전에**
    DayError(2) — 리허설·시험이 실제 시범 폴더를 건드리지 않게(사용자가 라벨링 중일 수 있다)."""
    from app import seal
    t0 = time.perf_counter()
    if not LABELER_RE.match(by or ""):
        raise DayError("--by 는 라벨러 ID(예: L01)")
    date = date or datetime.now().strftime("%Y%m%d")
    if not DATE_RE.match(date):
        raise DayError("--date 는 YYYYMMDD")
    if not allow_real and touches_real_data([*roots, ai_root, out_root, freeze_path]):
        raise DayError("실제 데이터 폴더(chatcoach-data)를 가리킵니다 — 해제 당일에만 --real 을 주세요(리허설은 합성 폴더로)")
    roots = [os.path.abspath(r) for r in roots]
    ai_roots = sorted({os.path.abspath(ai_root or seal.default_ai_root(r)) for r in roots})
    summary: dict = {"tool": TOOL, "tool_version": TOOL_VERSION, "date": date, "dry_run": dry_run,
                     "user_clicks": USER_CLICKS, "projects": [], "errors": []}
    summary["freeze"] = freeze_state(freeze_path)
    cond_watch = OpenWatch(roots, conditions_only=True, deny=ai_roots + [os.path.abspath(out_root)], write_ok=None)
    with cond_watch:
        summary["conditions"] = conditions(roots, expect=expect)
    summary["watch"] = cond_watch.summary()
    if cond_watch.violations:
        summary["elapsed_s"] = round(time.perf_counter() - t0, 2)
        return 1, summary
    if dry_run or not summary["conditions"]["met"]:
        summary["elapsed_s"] = round(time.perf_counter() - t0, 2)
        return (0 if summary["conditions"]["met"] else 3), summary
    # ---- 여기서부터 되돌릴 수 없다: 조건을 모두 본 뒤
    from app import agreement_report as ar
    from tools import viewer_score_calib as cal
    out_dir = day_dir(os.path.abspath(out_root), date)
    for r in roots:
        ar.check_out_root(out_dir, r)
    summary["out_dir_name"] = os.path.basename(out_dir)
    os.makedirs(out_dir, exist_ok=False)
    watch = OpenWatch(roots, conditions_only=False, write_ok=ai_roots + [out_dir])
    calibs = []
    code = 0
    with watch:
        for r in roots:
            name = os.path.basename(r)
            p: dict = {"name": name, "errors": []}
            summary["projects"].append(p)
            ts = time.perf_counter()
            try:
                p["unseal"] = unseal_project(r, by=by, ai_root=ai_root)
            except Exception as e:  # noqa: BLE001
                from app import seal as _seal
                p["errors"].append(f"unseal:{_seal.error_code(e)}")
                continue
            p["unseal_s"] = round(time.perf_counter() - ts, 2)
            p["errors"] += unseal_errors(p["unseal"])         # 풀리지 않은 유효 봉인 · 깨진 seal.jsonl → 종료 1
            if not (p["unseal"].get("verify") or {}).get("ok"):
                p["errors"].append("verify:unsealed_files")     # 해제한 파일이 기록 해시와 다르거나 짝이 풀리지 않음
            ts = time.perf_counter()
            try:
                fx, cmp_ = compare_project(r, out_dir=out_dir, by=by, ai_root=ai_root)
            except Exception as e:  # noqa: BLE001
                p["errors"].append(f"compare:{type(e).__name__}")
                continue
            p["errors"] += cmp_.pop("errors")
            p["compare"] = cmp_
            p["compare_s"] = round(time.perf_counter() - ts, 2)
            ts = time.perf_counter()
            try:
                rep = calib_project(r, fx, ai_root=ai_root, quick=quick)
            except Exception as e:  # noqa: BLE001
                p["errors"].append(f"calib:{type(e).__name__}")
                continue
            calibs.append(rep)
            _write(os.path.join(out_dir, f"calib-{name}.json"), _jbytes(rep))
            p["calib"] = calib_numbers(rep)
            p["calib_s"] = round(time.perf_counter() - ts, 2)
        if calibs:
            try:
                pooled = cal.pool_reports(calibs)
                _write(os.path.join(out_dir, "calib-pooled.json"), _jbytes(pooled))
                summary["calib_pooled"] = calib_numbers(pooled)
            except Exception as e:  # noqa: BLE001
                summary["errors"].append(f"pool:{type(e).__name__}")
        summary["watch_run"] = watch.summary()
        summary["elapsed_s"] = round(time.perf_counter() - t0, 2)
        if watch.violations or summary["errors"] or any(p["errors"] for p in summary["projects"]):
            code = 1
        _write(os.path.join(out_dir, "summary.json"), _jbytes(summary))
        _write(os.path.join(out_dir, "summary.md"), render_markdown(summary).encode("utf-8"))
    summary["watch_run"] = watch.summary()
    if watch.violations:
        code = 1
    return code, summary


def _print(summary: Mapping) -> None:
    c = summary["conditions"]
    print(f"시범 {len(c['projects'])}편 · 라벨링 세션 {c['labeling_sessions']} · 잠김 {c['locked']} · 조건 충족 {c['met']}"
          f" · 사유 {', '.join(c['reason_kinds']) or '없음'} · 사람 클릭 {summary['user_clicks']}")
    for p in c["projects"]:
        print(f"  {p['name']}: 라벨링 세션 {p['labeling_sessions']} · 잠김 {p['locked']} · 충족 {p['met']}"
              f" · 사유 {', '.join(p['reasons']) or '-'}")
    w = summary.get("watch") or {}
    print(f"열기 감시(조건): 시범 폴더 읽기 {w.get('guarded_reads')} · 쓰기 {w.get('writes')} · 위반 {w.get('violations')}"
          f" · 라벨 형식 고정 {summary['freeze']['state']}")
    if summary.get("dry_run"):
        print("dry-run: 아무것도 풀지도 쓰지도 않았습니다 · 실제 해제는 사용자 확인 뒤 --go")
        return
    if not summary.get("projects"):
        print("조건이 되지 않아 아무것도 풀지 않았습니다")
        return
    for p in summary["projects"]:
        u = p.get("unseal") or {}
        runs = (p.get("compare") or {}).get("runs") or ()
        ver = u.get("verify") or {}
        errs = list(p.get("errors") or ())
        print(f"  {p['name']}: 해제 {u.get('unsealed_now')}(이미 {u.get('already')}) · 실패 {len(u.get('failed') or ())}"
              f" · 대조 {verify_text(ver)} · 보고서 {len(runs)} · "
              f"판끼리 {len((p.get('compare') or {}).get('pairs') or ())} · 보정 {'됨' if p.get('calib') else '안 됨'}"
              f" · 오류 {len(errs)}{' (' + ', '.join(sorted(set(errs))) + ')' if errs else ''}")
    if summary.get("errors"):
        print(f"  묶음 오류: {', '.join(summary['errors'])}")
    wr = summary.get("watch_run") or {}
    print(f"열기 감시(실행): 쓰기 {wr.get('writes')} · 위반 {wr.get('violations')} · 요약 폴더 {summary.get('out_dir_name')}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시범 해제 당일: 조건 → 해제 → AI 비교 → 판끼리 → 보정 → 요약 (사람 클릭 0)."
                                             " 기본은 dry-run — 실제 해제는 --go")
    ap.add_argument("--projects-root", default=None,
                    help="pilot-* 를 찾을 projects 폴더(기본 없음 — --real 이면 chatcoach-data\\projects)")
    ap.add_argument("--real", action="store_true",
                    help="실제 데이터 폴더(chatcoach-data)를 가리킬 때 필수. 세션이 모두 잠긴 뒤에만(D-17 4)")
    ap.add_argument("--go", action="store_true",
                    help="되돌릴 수 없는 실제 해제를 한다(사용자 세션의 시작 확인 뒤). 없으면 dry-run")
    ap.add_argument("--project", action="append", default=None, help="시범 프로젝트 폴더(여러 번). 주면 찾지 않는다")
    ap.add_argument("--expect", type=int, default=DEFAULT_EXPECT, help="기대하는 시범 편 수(0 = 보지 않음)")
    ap.add_argument("--ai-root", default=None, help="AI 결과 폴더(기본 app.seal 규칙)")
    ap.add_argument("--out-root", default=None, help="요약 폴더를 만들 보고서 폴더(기본 projects 옆 reports)")
    ap.add_argument("--by", default="L01", help="해제 기록·보고서의 라벨러 ID")
    ap.add_argument("--date", default=None, help="요약 폴더 날짜 YYYYMMDD(기본 오늘)")
    ap.add_argument("--freeze", default=None,
                    help="라벨 형식 고정 기록(참고로만 비교, 기본 <projects 옆>\\pilot\\freeze.json)")
    ap.add_argument("--quick", action="store_true", help="보정은 기본·켜고 끄기 판만")
    ap.add_argument("--dry-run", action="store_true", help="조건만 보고 아무것도 풀지도 쓰지도 않는다(기본과 같음)")
    ap.add_argument("--json", action="store_true", help="요약 JSON 을 표준 출력으로")
    a = ap.parse_args(argv)
    try:
        if a.go and a.dry_run:
            raise DayError("--go 와 --dry-run 은 같이 줄 수 없습니다")
        projects_root = a.projects_root
        if projects_root is None and not a.project:
            if not a.real:
                raise DayError("--projects-root 또는 --project 를 주세요(실제 데이터 폴더는 --real)")
            projects_root = os.path.join(DATA_DIR, "projects")      # = DEFAULT_PROJECTS_ROOT (부를 때 계산)
        # 실제 폴더인지는 글자로만 먼저 본다 — --real 이 없으면 시범 폴더를 열기(listdir) 전에 멈춘다
        if not a.real and touches_real_data([projects_root, *(a.project or ()), a.ai_root, a.out_root, a.freeze]):
            raise DayError("실제 데이터 폴더(chatcoach-data)를 가리킵니다 — 해제 당일에만 --real 을 주세요"
                           "(리허설은 합성 pilot-* 폴더로)")
        roots = [os.path.abspath(p) for p in a.project] if a.project else find_pilots(projects_root)
        base = os.path.dirname(roots[0]) if a.project and roots else projects_root
        data = os.path.dirname(os.path.abspath(base))
        out_root = a.out_root or os.path.join(data, "reports")
        freeze = a.freeze or os.path.join(data, "pilot", "freeze.json")
        if a.go:
            print("되돌릴 수 없는 해제를 시작합니다(--go) · 조건이 모두 맞을 때만 풉니다",
                  file=sys.stderr if a.json else sys.stdout)
        code, summary = run_day(roots, out_root=out_root, by=a.by, ai_root=a.ai_root,
                                expect=a.expect or None, dry_run=not a.go, date=a.date, freeze_path=freeze,
                                quick=a.quick, allow_real=a.real)
    except DayError as e:
        print(f"입력 오류: {e}", file=sys.stderr)
        return e.code
    if a.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str))
    else:
        _print(summary)
    return code


__all__ = ["OpenWatch", "WatchViolation", "DayError", "find_pilots", "reason_code", "check_conditions", "conditions",
           "freeze_state", "unseal_project", "verify_unsealed", "compare_project", "calib_numbers", "report_numbers", "engine_version",
           "version_key", "version_label", "render_markdown", "run_day", "main", "USER_CLICKS", "CONDITION_READS",
           "touches_real_data", "unseal_errors", "verify_text", "LINK_RULES_VERSION"]

if __name__ == "__main__":
    raise SystemExit(main())
