"""AI 결과 봉인·해제 명령줄 (06 7-1·7-2, 03 6-14, 08 D-15 7·8). 검토자(세션 밖) 도구다 — 라벨링 PC·세션에서 쓰지 않는다.

하위 명령
  seal    --project P --file F --by L01 [--source file|engine_link] [--remove-source]
          받은 파일을 봉인한다. 검사 결과(유효/거부 코드)·해시·크기만 찍는다. --remove-source 면 봉인 뒤 원본을 지운다
          (휴지통이 아니라 지운다 — 봉인 원본이 sealed\\ 에 같은 바이트로 남는다).
  status  --project P
          '봉인됨 n건' 과 봉인마다 해시 앞 12자·크기·시각·출처·검사·사전 봉인·해제 여부. 봉인 파일은 열지 않는다.
  check   --project P
          해제 조건(06 7-1)만 본다. 라벨 파일·봉인 파일은 열지 않는다.
  unseal  --project P --sha256 H --by L01 [--early-reason "사유"]
          조건이 되면 runs\\<run_id>.json 으로 푼다. 조건 전에는 --early-reason 이 있어야 하고 기록에 남는다.
공통: [--ai-root <폴더>] (기본: 프로젝트가 …\\projects\\<slug> 면 그 옆 …\\ai, 아니면 <데이터>\\ai)

출력은 JSON 한 줄. 해제 전에는 내용을 찍지 않는다. 해제 뒤에도 run_id·건수·코드만 찍는다.
종료 코드: 0 정상 · 1 거부·조건 안 됨 · 2 입력 오류
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import seal  # noqa: E402


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _entry_row(e: seal.SealEntry) -> dict:
    return {"sha256_12": e.sha256[:12], "size": e.size, "sealed_at": e.sealed_at, "source": e.source,
            "check": e.check, "reject_reasons": e.reject_reasons, "presealed": e.presealed,
            "unsealed": e.unsealed_at is not None, "conditions_met": e.conditions_met}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python tools/ai_seal.py", description="AI 결과 봉인·해제(검토자 도구)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seal", "status", "check", "unseal"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.add_argument("--ai-root")
        if name == "seal":
            p.add_argument("--file", required=True)
            p.add_argument("--by", required=True)
            p.add_argument("--source", choices=seal.SOURCE_KINDS, default="file")
            p.add_argument("--remove-source", action="store_true")
        if name == "unseal":
            p.add_argument("--sha256", required=True)
            p.add_argument("--by", required=True)
            p.add_argument("--early-reason")
    ns = ap.parse_args(argv)
    try:
        if ns.cmd == "seal":
            e = seal.seal_file(ns.project, ns.file, source=ns.source, by=ns.by, ai_root=ns.ai_root)
            removed = False
            if ns.remove_source:
                os.remove(ns.file)
                removed = True
            _print({"sealed": e.sha256, "size": e.size, "check": e.check, "reject_reasons": e.reject_reasons,
                    "new": e.new, "presealed": e.presealed, "source_removed": removed})
            return 0 if e.check == "valid" else 1
        if ns.cmd == "status":
            st = seal.seal_status(ns.project, ai_root=ns.ai_root)
            _print({"project_id": st.project_id, "sealed_count": st.sealed_count,
                    "unsealed_count": st.unsealed_count, "first_primary_lock_at": st.first_primary_lock_at,
                    "entries": [_entry_row(e) for e in st.entries], "problems": st.problems})
            return 0
        if ns.cmd == "check":
            c = seal.unseal_conditions(ns.project)
            _print({"conditions_met": c.met, "reasons": c.reasons, "warnings": c.warnings,
                    "labeling_sessions": len(c.labeling_sessions)})
            return 0 if c.met else 1
        r = seal.unseal(ns.project, ns.sha256, by=ns.by, ai_root=ns.ai_root, early_reason=ns.early_reason)
        rec = r.record
        _print({"run_id": r.run_id, "conditions_met": r.conditions_met, "presealed": r.presealed,
                "locks": len(rec["locks"]), "warnings": rec["warnings"], "calibration_rev": rec["calibration_rev"],
                "dropped_unknown": rec["dropped_unknown"], "dropped_duplicate": rec["dropped_duplicate"]})
        return 0
    except seal.SealBlockedError as e:
        _print({"error": "SealBlockedError", "reasons": e.reasons})
        return 1
    except seal.SealError as e:
        _print({"error": type(e).__name__, "detail": str(e)[:300]})
        return 1
    except (OSError, ValueError) as e:
        _print({"error": type(e).__name__})
        return 2


if __name__ == "__main__":
    sys.exit(main())
