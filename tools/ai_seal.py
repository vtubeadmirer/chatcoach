"""AI 결과 봉인·해제 명령줄 (06 7-1·7-2, 03 6-14, 08 D-15 7·8, D-22 2). 검토자(세션 밖) 도구다 — 라벨링 PC·세션에서
쓰지 않는다.

하위 명령
  seal    --project P --file F --by L01 [--source file|engine_link] [--remove-source]
          받은 파일을 봉인한다. 검사 결과(유효/거부 코드)·해시·크기만 찍는다. --remove-source 면 봉인 뒤 원본을 지운다
          (휴지통이 아니라 지운다 — 봉인 원본이 sealed\\ 에 같은 바이트로 남는다).
  status  --project P
          '봉인됨 n건' 과 봉인마다 해시 앞 12자·크기·시각·출처·검사·사전 봉인·해제 여부·짝(주제 흐름·전사 낱말·주제 벡터)
          유무·해제 뒤 엔진 판. 봉인 파일은 열지 않는다.
  check   --project P
          해제 조건(06 7-1)만 본다. 라벨 파일·봉인 파일은 열지 않는다.
  unseal  --project P (--sha256 H | --all) --by L01 [--early-reason "사유"]
          조건이 되면 runs\\<run_id>.json 으로 푼다(짝도 같은 해제에서 한 번에). 조건 전에는 --early-reason 이 있어야 하고
          기록에 남는다. --all 은 아직 안 푼 '유효' 봉인을 모두 푼다(사용자 0클릭, D-16). 시범 해제 당일의 입구는
          tools/pilot_unseal_day.py 하나다(3편 조건을 한꺼번에 보고, 푼 뒤 verify_project 로 대조) — 이것은 한 편을 손으로 풀 때.
          조건이 안 되면(사유도 없으면) 하나도 풀지 않는다.
  verify  --project P
          해제된 run 마다 결과·짝 파일을 해제 기록의 해시와 대조해 읽어 본다(read_run·read_topics·read_words·
          read_vectors). 내용은 찍지 않고 ok · none(짝 없음) · 코드만.
공통: [--ai-root <폴더>] (기본: 프로젝트가 …\\projects\\<slug> 면 그 옆 …\\ai, 아니면 <데이터>\\ai)

출력은 JSON 한 줄. 해제 전에는 내용을 찍지 않는다. 해제 뒤에도 run_id·엔진 판·건수·코드·해시 앞 12자만 찍는다.
종료 코드: 0 정상 · 1 거부·조건 안 됨·읽기 실패 · 2 입력 오류
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
            "unsealed": e.unsealed_at is not None, "conditions_met": e.conditions_met,
            "pairs": {k: getattr(e, f"{k}_sha256") is not None for k in seal.PAIR_KINDS},
            "engine_version": e.engine_version}


def _unseal_row(r: seal.UnsealResult) -> dict:
    rec = r.record
    return {"run_id": r.run_id, "sha256_12": r.sha256[:12], "conditions_met": r.conditions_met,
            "presealed": r.presealed, "engine_version": rec.get("engine_version"),
            "locks": len(rec["locks"]), "warnings": rec["warnings"], "calibration_rev": rec["calibration_rev"],
            "dropped_unknown": rec["dropped_unknown"], "dropped_duplicate": rec["dropped_duplicate"],
            "pairs": [{"kind": p["kind"], "sha256_12": p["sha256"][:12], "error": p["error"]}
                      for p in rec.get("pairs") or []]}


_READERS = (("topics", seal.read_topics), ("words", seal.read_words), ("vectors", seal.read_vectors))


def verify_project(project: str, *, ai_root: str | None = None) -> tuple[list[dict], bool]:
    """해제된 run 마다 결과·짝을 읽어 본다 → ([{run_id, engine_version, run, topics, words, vectors}], 모두 됐나).
    값은 'ok' · 'none'(짝 없음) · 오류 종류 이름. 기록에 짝이 있는데(pairs 에 오류 없이) 읽지 못하면 실패로 센다."""
    st = seal.seal_status(project, ai_root=ai_root)
    rows, ok = [], True
    for e in st.entries:
        if e.unsealed_at is None or not e.run_id:
            continue
        row = {"run_id": e.run_id, "engine_version": e.engine_version}
        try:
            seal.read_run(project, e.run_id, ai_root=ai_root)
            row["run"] = "ok"
        except (seal.SealError, OSError, ValueError) as ex:
            row["run"], ok = type(ex).__name__, False
        for name, fn in _READERS:
            try:
                row[name] = "none" if fn(project, e.run_id, ai_root=ai_root) is None else "ok"
            except (seal.SealError, OSError, ValueError) as ex:
                row[name], ok = type(ex).__name__, False
            if row[name] == "none" and getattr(e, f"{name}_sha256") is not None:
                row[name] = "not_unsealed"          # 짝 봉인은 있는데 해제 기록에 풀린 파일이 없다(짝 오류 코드)
                ok = False
        rows.append(row)
    return rows, ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python tools/ai_seal.py", description="AI 결과 봉인·해제(검토자 도구)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seal", "status", "check", "unseal", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.add_argument("--ai-root")
        if name == "seal":
            p.add_argument("--file", required=True)
            p.add_argument("--by", required=True)
            p.add_argument("--source", choices=seal.SOURCE_KINDS, default="file")
            p.add_argument("--remove-source", action="store_true")
        if name == "unseal":
            which = p.add_mutually_exclusive_group(required=True)
            which.add_argument("--sha256")
            which.add_argument("--all", action="store_true")
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
        if ns.cmd == "verify":
            rows, ok = verify_project(ns.project, ai_root=ns.ai_root)
            _print({"runs": rows, "ok": ok})
            return 0 if ok else 1
        if not ns.all:
            r = seal.unseal(ns.project, ns.sha256, by=ns.by, ai_root=ns.ai_root, early_reason=ns.early_reason)
            _print(_unseal_row(r))
            return 0
        # --all: 조건을 먼저 본다 — 안 되면(사유도 없으면) 하나도 풀지 않는다
        c = seal.unseal_conditions(ns.project)
        if not c.met and ns.early_reason is None:
            raise seal.SealBlockedError(c.reasons)
        st = seal.seal_status(ns.project, ai_root=ns.ai_root)
        todo = [e for e in st.entries if e.unsealed_at is None and e.check == "valid"]
        done, errors = [], []
        for e in todo:
            try:
                done.append(_unseal_row(seal.unseal(ns.project, e.sha256, by=ns.by, ai_root=ns.ai_root,
                                                    early_reason=ns.early_reason)))
            except seal.SealError as ex:
                errors.append({"sha256_12": e.sha256[:12], "error": type(ex).__name__, "detail": str(ex)[:300]})
        _print({"unsealed": done, "errors": errors,
                "skipped_rejected": sum(1 for e in st.entries if e.unsealed_at is None and e.check != "valid")})
        return 0 if not errors else 1
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
