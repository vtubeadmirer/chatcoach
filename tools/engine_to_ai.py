"""엔진 link 출력 → ai-result 1판(schemas/ai-result.schema.json) (06 6-1, 08 D-15 7·8).

엔진 `python -m engine.link` 의 결과(schema_version 2 초안, 측정 문서 2장)에서 스키마 1판에 있는 칸만 뽑는다.
**4단계 등급만** 받는다(D-15 7): 등급은 direct · likely · related_only · unclear 가운데 하나이거나(result = graded),
없어야 한다(result = unconfirmed). `grade_scheme` 이 있고 4level 이 아니면 거부한다.

엔진이 graded 인데 등급을 고르지 못한 줄(후보는 있으나 어느 등급 조건도 못 채움)은 스키마 1판에 담을 수 없다
(06 6-1: graded + grade=null 로 대신하지 않는다). 기본은 그 줄을 빼서 'AI 미평가'(ai_unevaluated) 로 세게 하고,
`--graded-none unconfirmed` 를 주면 '반응 미확인' 으로 바꾼다. 어느 쪽인지·몇 건인지는 변환 정보에 남는다.

두 가지 입력
  --in <엔진 결과.json>                  엔진이 이미 쓴 파일
  --from-project <프로젝트 폴더>          엔진을 이 자리에서 메모리로 돌린다(결과 파일을 따로 쓰지 않음, D-15 8)
두 가지 출력
  --out <ai-result.json>                변환한 파일을 쓴다(시범 프로젝트에는 쓰지 않는다)
  --seal --project <프로젝트 폴더> --by L01   바로 봉인한다(<ai 루트>\\<project_id>\\sealed\\<sha256>.bin)

시범 프로젝트(폴더 이름 pilot-*)는 --seal 만 된다. 시범 라벨 세션이 잠기기 전에는 아무도 결과를 열지 않는다(D-15 8).
표준 출력에는 해시·크기·검사 결과(봉인), 또는 건수(파일 출력)만 찍는다. 채팅 본문·닉네임·전사문은 엔진 결과에도 없다.

사용
  python tools/engine_to_ai.py --in link.json --out ai.json
  python tools/engine_to_ai.py --from-project <폴더> --seal --by L01 [--start-ms ..] [--end-ms ..] [--ai-root <폴더>]
종료 코드: 0 정상 · 1 거부(4단계 아님·검사 거부·시범에 파일 출력) · 2 입력 오류
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
from typing import Mapping

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import ai_results as air  # noqa: E402

TOP_KEYS = ("schema", "run_id", "created_at", "engine", "target", "time_axis", "media")
ENGINE_KEYS = ("name", "version", "components", "config_sha256", "preregistered")
COMPONENT_KEYS = ("vad", "asr", "ocr", "other")
TARGET_KEYS = ("platform", "video_no", "chat_id_algorithm", "messages_sha256")
REACTION_KEYS = ("chat_id", "result", "grade", "reaction_time_ms", "utterance_start_ms", "utterance_end_ms", "score")
GRADED_NONE_POLICIES = ("drop", "unconfirmed")
ENGINE_SCHEMA_VERSIONS = (1, 2)


class ConvertError(ValueError):
    """변환 거부. 메시지에 값·본문을 넣지 않는다."""


def _pick(d: Mapping | None, keys) -> dict:
    return {k: d[k] for k in keys if isinstance(d, Mapping) and k in d}


def to_ai_result(doc: Mapping, *, graded_none: str = "drop") -> tuple[dict, dict]:
    """엔진 결과 dict → (ai-result 1판 dict, 변환 정보 {reactions_in, reactions_out, graded_none, graded_none_policy}).

    4단계 밖 등급·이진 형식은 ConvertError. 스키마 검사는 하지 않는다(app.ai_results.check_doc 이 한다)."""
    if graded_none not in GRADED_NONE_POLICIES:
        raise ConvertError(f"graded_none 은 {GRADED_NONE_POLICIES} 가운데 하나")
    if not isinstance(doc, Mapping) or doc.get("schema") != air.SCHEMA:
        raise ConvertError("chatcoach/ai-result 엔진 결과가 아닙니다")
    if doc.get("schema_version") not in ENGINE_SCHEMA_VERSIONS or isinstance(doc.get("schema_version"), bool):
        raise ConvertError("모르는 엔진 결과 판입니다")
    gs = doc.get("grade_scheme")
    if gs is not None and gs != "4level":
        raise ConvertError("4단계 등급만 받습니다(08 D-15 7) — grade_scheme 이 4level 이 아닙니다")
    reactions = doc.get("reactions")
    if not isinstance(reactions, list):
        raise ConvertError("reactions 가 없습니다")
    out = {k: doc[k] for k in TOP_KEYS if k in doc}
    out["schema_version"] = air.SCHEMA_VERSION
    if out.get("media") is None:
        out.pop("media", None)
    eng = _pick(doc.get("engine"), ENGINE_KEYS)
    if "components" in eng:
        eng["components"] = _pick(eng["components"], COMPONENT_KEYS)
    out["engine"] = eng
    out["target"] = _pick(doc.get("target"), TARGET_KEYS)
    rows, n_none = [], 0
    for r in reactions:
        if not isinstance(r, Mapping):
            raise ConvertError("reactions 줄이 object 가 아닙니다")
        g, res = r.get("grade"), r.get("result")
        if g is not None and g not in air.GRADES:
            raise ConvertError("4단계 밖 등급이 있습니다(08 D-15 7)")
        row = _pick(r, REACTION_KEYS)
        if res == "graded" and g is None:
            n_none += 1
            if graded_none == "drop":
                continue
            row = {"chat_id": row.get("chat_id"), "result": "unconfirmed", "grade": None,
                   "reaction_time_ms": None, "utterance_start_ms": None, "utterance_end_ms": None,
                   "score": row.get("score")}
        rows.append(row)
    out["reactions"] = rows
    for k in ("segments", "missed_candidates", "continuations", "origins"):
        if isinstance(doc.get(k), list) and doc.get("schema_version") == air.SCHEMA_VERSION:
            out[k] = doc[k]                      # 1판 입력은 다른 종류도 그대로 둔다(2판 초안에는 없음)
    info = {"reactions_in": len(reactions), "reactions_out": len(rows), "graded_none": n_none,
            "graded_none_policy": graded_none}
    return out, info


def dump(doc: Mapping) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")


def _is_pilot(project_dir: str | None) -> bool:
    return bool(project_dir) and os.path.basename(os.path.abspath(project_dir)).lower().startswith("pilot-")


def _run_engine(project_dir: str, ns) -> dict:
    """엔진 link 를 메모리로 돌린다(engine.link.run 은 결과 파일을 쓰므로 쓰지 않는다)."""
    import time
    from datetime import datetime
    from engine import link as L
    inp = L.load_project(project_dir, use_identities=not ns.no_identities, offset_ms=ns.offset_ms,
                         file_start_ms=ns.file_start_ms, asr_path=getattr(ns, "asr", None))
    cfg = L.LinkConfig()
    t0 = time.perf_counter()
    reactions, stats = L.link(inp, cfg, start_ms=ns.start_ms, end_ms=ns.end_ms)
    now = datetime.now().astimezone()
    doc = L.build_result(inp, reactions, stats, cfg, start_ms=ns.start_ms, end_ms=ns.end_ms,
                         elapsed_s=time.perf_counter() - t0, created_at=now.isoformat(timespec="seconds"),
                         run_id=f"link-v0-{now.strftime('%Y%m%dT%H%M%S')}")
    probs = L.validate_result(doc)
    if probs:
        raise ConvertError(f"엔진 결과 검사 실패 {len(probs)}건")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python tools/engine_to_ai.py", description="엔진 link 출력 → ai-result 1판")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--in", dest="inp", help="엔진 결과 파일")
    src.add_argument("--from-project", help="엔진을 메모리로 돌릴 프로젝트 폴더")
    dst = ap.add_mutually_exclusive_group(required=True)
    dst.add_argument("--out", help="ai-result 1판을 쓸 파일")
    dst.add_argument("--seal", action="store_true", help="바로 봉인")
    ap.add_argument("--project", help="봉인할 프로젝트 폴더(--from-project 면 생략 가능)")
    ap.add_argument("--by", help="봉인하는 검토자 라벨러 ID")
    ap.add_argument("--ai-root", help="AI 결과 루트(기본: 프로젝트 옆 ai 또는 <데이터>\\ai)")
    ap.add_argument("--graded-none", choices=GRADED_NONE_POLICIES, default="drop")
    ap.add_argument("--start-ms", type=int)
    ap.add_argument("--end-ms", type=int)
    ap.add_argument("--offset-ms", type=int)
    ap.add_argument("--file-start-ms", type=int)
    ap.add_argument("--no-identities", action="store_true")
    ap.add_argument("--asr", help="--from-project 의 asr.json 경로(기본 <project>\\derived\\asr.json). 시범은 프로젝트 밖 "
                                  "작업 폴더(chatcoach-data\\sealed-work)의 것을 준다(D-15 8)")
    ns = ap.parse_args(argv)
    project = ns.project or ns.from_project
    if ns.out and (_is_pilot(project) or _is_pilot(ns.from_project)):
        print(json.dumps({"error": "pilot_needs_seal", "detail": "시범 프로젝트 결과는 --seal 로만 받습니다(D-15 8)"},
                         ensure_ascii=False))
        return 1
    if ns.seal and (not project or not ns.by):
        print(json.dumps({"error": "args", "detail": "--seal 에는 --project(또는 --from-project)와 --by 가 필요합니다"},
                         ensure_ascii=False))
        return 2
    try:
        if ns.inp:
            with open(ns.inp, "rb") as f:
                doc = air.parse_bytes(f.read(air.MAX_BYTES + 1))
        else:
            doc = _run_engine(ns.from_project, ns)
        v1, info = to_ai_result(doc, graded_none=ns.graded_none)
    except (air.AiResultError, ConvertError) as e:
        print(json.dumps({"error": type(e).__name__, "detail": str(e)[:300]}, ensure_ascii=False))
        return 1
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({"error": type(e).__name__}, ensure_ascii=False))
        return 2
    data = dump(v1)
    if ns.seal:
        from app import seal
        try:
            e = seal.seal_bytes(project, data, source="engine_link", by=ns.by, ai_root=ns.ai_root)
        except seal.SealError as ex:
            print(json.dumps({"error": type(ex).__name__, "detail": str(ex)[:300]}, ensure_ascii=False))
            return 1
        # 봉인 기록에 있는 것만 찍는다(해시·크기·검사). 변환 건수는 내용을 해석한 값이라 찍지 않는다(06 7-2).
        print(json.dumps({"sealed": e.sha256, "size": e.size, "check": e.check, "reject_reasons": e.reject_reasons,
                          "new": e.new, "presealed": e.presealed}, ensure_ascii=False))
        return 0 if e.check == "valid" else 1
    os.makedirs(os.path.dirname(os.path.abspath(ns.out)), exist_ok=True)
    with open(ns.out, "wb") as f:
        f.write(data)
    print(json.dumps({"out": ns.out, **info}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
