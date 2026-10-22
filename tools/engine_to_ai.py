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

엔진 판(--from-project): --engine v0.2(기본) · v0.3(로컬 문장 임베딩 의미 비교, D-17 6 — 모델이 없으면 v0.2 로 물러나고
결과의 engine.components.other 에 '물러남' 이 남는다). --topics 를 주면 같은 입력으로 주제 흐름(topic-flow, D-17 5)도
메모리로 만들어, --seal 이면 판정 봉인의 **짝**으로 봉인하고(seal.seal_topics_bytes — 판정을 풀 때 같이 풀린다),
--out a.json 이면 a.topics.json 에 쓴다('분석 → AI 결과 가져오기' 가 같은 이름을 찾아 같이 봉인한다).
--words 를 주면 같은 asr·보정으로 시각별 전사 낱말(transcript-words, D-20 3, engine.transcript_words)도 메모리로 만들어
--seal 이면 판정 봉인의 짝으로 봉인하고(app.seal.seal_words_bytes), --out a.json 이면 a.words.json 에 쓴다. 낱말 파일은
전사문과 같은 등급이라 a.words.json 이 그 프로젝트 보호 구역 안(예: --out <프로젝트>\\derived\\asr-link.json)일 때만 쓰고,
아니면 엔진을 돌리기 전에 종료 2(words_out_unprotected). --asr 가 시범 폴더 안이면 --out 은 거부(pilot_needs_seal).
--vectors 를 주면 같은 asr·보정으로 창별 주제 벡터(topic-vectors, G 둘째 22 7-1 의 3, engine.topic_vectors — 로컬 e5-small,
--models-dir·--semantic-backend 를 같이 쓴다)도 메모리로 만들어 --seal 이면 판정 봉인의 짝으로 봉인하고
(app.seal.seal_vectors_bytes, 23 5-2 의 2), --out a.json 이면 a.vectors.json 에 쓴다. 벡터도 전사에서 나온 값이라 낱말 파일과
같이 그 프로젝트 보호 구역 안일 때만 쓴다(아니면 종료 2, vectors_out_unprotected). 임베딩 모델이 없으면 아무것도 봉인·쓰지
않고 종료 1.
짝 봉인 함수(seal_words_bytes·seal_vectors_bytes)를 app.seal 에서 이름으로 찾는다. 찾지 못하는 앱(옛 판)이면 판정만 봉인되고 짝이
빠지지 않게 엔진을 돌리기 전에 아무것도 봉인하지 않고 종료 1(words_seal_unsupported · vectors_seal_unsupported).
짝은 판정을 풀 때 같은 해제에서 한 번에 풀린다(tools/ai_seal.py unseal --all · verify).
시범 해제 리허설(합성 시범 프로젝트, D-22 2): --from-project <pilot-…> --seal --by L01 --topics --words --vectors
→ ai_seal check(거부) → 세션 잠금 → ai_seal unseal --all → ai_seal verify. 사용자 클릭 0(Claude 가 명령으로).

사용
  python tools/engine_to_ai.py --in link.json --out ai.json
  python tools/engine_to_ai.py --from-project <폴더> --seal --by L01 [--start-ms ..] [--end-ms ..] [--ai-root <폴더>]
        [--engine v0.3] [--topics] [--words] [--vectors]
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


V03_VERSION = "link-v0.3"     # engine.link.ENGINE_VERSION_V03 (앱 venv 에서도 읽히게 글자로 둔다)


def engine_run_meta(doc: Mapping) -> dict:
    """봉인 출력에 찍어도 되는 메타데이터만: 엔진 판과 의미 비교 상태(active·status·fallback). 판정 내용은 넣지 않는다."""
    eng = doc.get("engine") if isinstance(doc.get("engine"), Mapping) else {}
    sem = doc.get("semantic") if isinstance(doc.get("semantic"), Mapping) else None
    return {"engine_version": eng.get("version"),
            "semantic": {k: sem.get(k) for k in ("active", "status", "fallback")} if sem else None}


def _is_pilot(project_dir: str | None) -> bool:
    return bool(project_dir) and os.path.basename(os.path.abspath(project_dir)).lower().startswith("pilot-")


def _inside_pilot(path: str | None) -> bool:
    """경로의 어느 폴더든 pilot- 로 시작하는가(--asr 가 시범 폴더 안을 가리키는지, engine.transcript_words.inside_pilot 과 같음)."""
    if not path:
        return False
    parts = os.path.normpath(os.path.abspath(path)).replace("\\", "/").split("/")
    return any(p.lower().startswith("pilot-") for p in parts)


def _run_engine(project_dir: str, ns, topics_out: dict | None = None) -> dict:
    """엔진 link 를 메모리로 돌린다(engine.link.run 은 결과 파일을 쓰므로 쓰지 않는다).

    ns.engine: 'v0.2'(기본, 글자·시간 규칙) 또는 'v0.3'(로컬 문장 임베딩 의미 비교, D-17 6 — 모델이 없으면 v0.2 로 물러나고
    결과의 engine.components.other 에 '물러남' 이 남는다). topics_out 에 dict 를 주면 같은 입력으로 주제 흐름(engine.topics,
    D-17 5)도 메모리로 만들어 topics_out['doc'] 에 넣는다 — 파일을 쓰지 않는다."""
    import time
    from datetime import datetime
    from engine import link as L
    engine = getattr(ns, "engine", None) or "v0.2"
    if engine == "v0.2" and topics_out is None:
        # 지난 판과 같은 길(바이트 단위로 같은 결과): link 를 바로 부른다
        inp = L.load_project(project_dir, use_identities=not ns.no_identities, offset_ms=ns.offset_ms,
                             file_start_ms=ns.file_start_ms, asr_path=getattr(ns, "asr", None))
        cfg = L.LinkConfig()
        t0 = time.perf_counter()
        reactions, stats = L.link(inp, cfg, start_ms=ns.start_ms, end_ms=ns.end_ms)
        now = datetime.now().astimezone()
        doc = L.build_result(inp, reactions, stats, cfg, start_ms=ns.start_ms, end_ms=ns.end_ms,
                             elapsed_s=time.perf_counter() - t0, created_at=now.isoformat(timespec="seconds"),
                             run_id=f"link-v0-{now.strftime('%Y%m%dT%H%M%S')}")
    else:
        if topics_out is not None:
            from engine import topics as TP
            utts, msgs, nicks, inp = TP.inputs_from_project(
                project_dir, asr_path=getattr(ns, "asr", None), offset_ms=ns.offset_ms,
                file_start_ms=ns.file_start_ms, use_identities=not ns.no_identities)
        else:
            inp = L.load_project(project_dir, use_identities=not ns.no_identities, offset_ms=ns.offset_ms,
                                 file_start_ms=ns.file_start_ms, asr_path=getattr(ns, "asr", None))
        doc = L.build_doc(inp, L.LinkConfig(), start_ms=ns.start_ms, end_ms=ns.end_ms, engine=engine,
                          semantic_backend=getattr(ns, "semantic_backend", None) or "onnx",
                          models_dir=getattr(ns, "models_dir", None))
        if topics_out is not None:
            topics_out["doc"] = _run_topics(utts, msgs, nicks, inp, ns)
    probs = L.validate_result(doc)
    if probs:
        raise ConvertError(f"엔진 결과 검사 실패 {len(probs)}건")
    return doc


def _run_topics(utts, msgs, nicks, inp, ns) -> dict:
    """주제 흐름(engine.topics.segment → build_doc)을 메모리로. 범위는 --start-ms/--end-ms, 없으면 입력 전체."""
    import time
    from datetime import datetime
    from app.core import topics as T
    from engine import topics as TP
    t0 = time.perf_counter()
    times = [u.t_ms for u in utts] + [u.end_ms for u in utts] + [m.t_ms for m in msgs]
    start = ns.start_ms if ns.start_ms is not None else (min(times) if times else 0)
    end = ns.end_ms if ns.end_ms is not None else (max(times) + 1 if times else start + 1)
    cfg = TP.TopicConfig()
    embed, embed_name = TP.load_semantic(getattr(ns, "topics_semantic", None) or "off", getattr(ns, "models_dir", None))
    part = TP.segment(utts, msgs, start_ms=start, end_ms=end, cfg=cfg, embed=embed, nicknames=nicks)
    now = datetime.now().astimezone()
    tdoc = TP.build_doc(part, cfg=cfg, run_id=f"topics-v0-{now.strftime('%Y%m%dT%H%M%S')}",
                        created_at=now.isoformat(timespec="seconds"), video_no=inp.video_no,
                        messages_sha256=inp.messages_sha256, asr_sha256=inp.asr_meta.get("sha256"),
                        embedding=embed_name)
    tdoc["stats"]["elapsed_s"] = round(time.perf_counter() - t0, 3)
    if T.validate(tdoc):
        raise ConvertError("주제 흐름 검사 실패")
    return tdoc


def topics_out_path(out: str) -> str:
    """--out a.json 일 때 주제 흐름 파일 이름: a.topics.json (분석 → AI 결과 가져오기 가 같은 이름을 찾아 같이 봉인한다)."""
    stem, ext = os.path.splitext(out)
    return stem + ".topics" + (ext or ".json")


def words_out_path(out: str) -> str:
    """--out a.json 일 때 시각별 전사 낱말 파일 이름: a.words.json."""
    stem, ext = os.path.splitext(out)
    return stem + ".words" + (ext or ".json")


def words_out_protected(project_dir: str, out: str) -> bool:
    """--words --out 의 낱말 파일(<out>.words.json)이 그 프로젝트 보호 구역(derived/asr* 등) 안인가.

    시각별 전사 낱말은 전사문과 같은 등급의 자료라(10초마다 한 말이 낱말로 거의 그대로 남는다) 평문으로는 보호 구역 밖에
    두지 않는다(검토 2026-10-23). 예: --out <프로젝트>\\derived\\asr-link.json → derived\\asr-link.words.json."""
    from app import backup
    wp = os.path.abspath(words_out_path(out))
    root = os.path.abspath(project_dir)
    try:
        rel = os.path.relpath(wp, root)
    except ValueError:          # 다른 드라이브
        return False
    if rel == os.curdir or rel.startswith(os.pardir) or os.path.isabs(rel):
        return False
    return backup.in_protected_zone(rel, backup.DEFAULT_PROTECTED_ZONE)


WORDS_SEAL_FN = "seal_words_bytes"   # app.seal 의 전사 낱말 짝 봉인 함수(없는 옛 앱이면 --words --seal 은 종료 1)


def _words_sealer():
    """app.seal 의 전사 낱말 짝 봉인 함수(seal_topics_bytes 와 같은 인자) 또는 None."""
    from app import seal
    fn = getattr(seal, WORDS_SEAL_FN, None)
    return fn if callable(fn) else None


def _run_words(project_dir: str, ns) -> dict:
    """시각별 전사 낱말(engine.transcript_words)을 메모리로. 범위·보정·asr 는 판정과 같은 인자.

    시범 프로젝트는 봉인할 때만 만든다(평문 --out 은 main 이 먼저 거부한다)."""
    from engine import transcript_words as TW
    return TW.from_project(project_dir, asr_path=getattr(ns, "asr", None), offset_ms=ns.offset_ms,
                           file_start_ms=ns.file_start_ms, start_ms=ns.start_ms, end_ms=ns.end_ms,
                           allow_pilot=bool(ns.seal))


VECTORS_SEAL_FN = "seal_vectors_bytes"   # app.seal 의 주제 벡터 짝 봉인 함수(없는 옛 앱이면 --vectors --seal 은 종료 1)


def vectors_out_path(out: str) -> str:
    """--out a.json 일 때 창별 주제 벡터 파일 이름: a.vectors.json."""
    stem, ext = os.path.splitext(out)
    return stem + ".vectors" + (ext or ".json")


def vectors_out_protected(project_dir: str, out: str) -> bool:
    """--vectors --out 의 벡터 파일(<out>.vectors.json)이 그 프로젝트 보호 구역 안인가(낱말 파일과 같은 규칙).

    임베딩은 어느 정도 거꾸로 풀 수 있어 전사문과 같은 등급으로 둔다. 예: --out <프로젝트>\\derived\\asr-link.json."""
    from app import backup
    vp = os.path.abspath(vectors_out_path(out))
    root = os.path.abspath(project_dir)
    try:
        rel = os.path.relpath(vp, root)
    except ValueError:          # 다른 드라이브
        return False
    if rel == os.curdir or rel.startswith(os.pardir) or os.path.isabs(rel):
        return False
    return backup.in_protected_zone(rel, backup.DEFAULT_PROTECTED_ZONE)


def _vectors_sealer():
    """app.seal 의 주제 벡터 짝 봉인 함수(seal_words_bytes 와 같은 인자) 또는 None."""
    from app import seal
    fn = getattr(seal, VECTORS_SEAL_FN, None)
    return fn if callable(fn) else None


def _run_vectors(project_dir: str, ns) -> dict:
    """창별 주제 벡터(engine.topic_vectors)를 메모리로. 범위·보정·asr 는 판정과 같은 인자, 모델은 --models-dir.

    시범 프로젝트는 봉인할 때만 만든다(평문 --out 은 main 이 먼저 거부한다). 모델이 없으면 VectorsError."""
    from engine import topic_vectors as TV
    backend = "lmstudio" if getattr(ns, "semantic_backend", None) == "lmstudio" else "onnx"
    return TV.from_project(project_dir, models_dir=getattr(ns, "models_dir", None), backend=backend,
                           asr_path=getattr(ns, "asr", None), offset_ms=ns.offset_ms, file_start_ms=ns.file_start_ms,
                           start_ms=ns.start_ms, end_ms=ns.end_ms, allow_pilot=bool(ns.seal))


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
    ap.add_argument("--engine", choices=("v0.2", "v0.3"), default="v0.2",
                    help="--from-project 의 엔진 판(v0.3 = 로컬 임베딩 의미 비교, 모델이 없으면 v0.2 로 물러남)")
    ap.add_argument("--semantic-backend", choices=("onnx", "lmstudio", "off"), default="onnx")
    ap.add_argument("--models-dir", help="의미 비교 모델 폴더(기본은 engine.semantic 이 정함)")
    ap.add_argument("--topics", action="store_true",
                    help="--from-project 에서 주제 흐름(topic-flow)도 만든다. --seal 이면 판정 봉인의 짝으로 봉인, "
                         "--out 이면 <out>.topics.json")
    ap.add_argument("--topics-semantic", choices=("off", "onnx", "lmstudio"), default="off",
                    help="주제 흐름에 로컬 임베딩을 섞을지(기본 off = 낱말만)")
    ap.add_argument("--words", action="store_true",
                    help="--from-project 에서 시각별 전사 낱말(transcript-words)도 만든다. --seal 이면 판정 봉인의 짝으로 "
                         "봉인(app.seal.seal_words_bytes), --out 이면 <out>.words.json")
    ap.add_argument("--vectors", action="store_true",
                    help="--from-project 에서 창별 주제 벡터(topic-vectors, 로컬 e5-small)도 만든다. --seal 이면 판정 봉인의 "
                         "짝으로 봉인(app.seal.seal_vectors_bytes), --out 이면 <out>.vectors.json")
    ap.add_argument("--allow-fallback", action="store_true",
                    help="--engine v0.3 --seal 에서 의미 비교가 돌지 못해 v0.2 로 물러나도 봉인한다(기본은 봉인하지 않고 종료 1)")
    ns = ap.parse_args(argv)
    if ns.topics and not ns.from_project:
        print(json.dumps({"error": "args", "detail": "--topics 는 --from-project 와 함께 씁니다"}, ensure_ascii=False))
        return 2
    if ns.words and not ns.from_project:
        print(json.dumps({"error": "args", "detail": "--words 는 --from-project 와 함께 씁니다"}, ensure_ascii=False))
        return 2
    if ns.vectors and not ns.from_project:
        print(json.dumps({"error": "args", "detail": "--vectors 는 --from-project 와 함께 씁니다"}, ensure_ascii=False))
        return 2
    if ns.vectors and ns.semantic_backend == "off":
        print(json.dumps({"error": "args", "detail": "--vectors 는 임베딩이 필요합니다(--semantic-backend off 와 함께 못 씀)"},
                         ensure_ascii=False))
        return 2
    project = ns.project or ns.from_project
    if ns.out and (_is_pilot(project) or _is_pilot(ns.from_project) or _inside_pilot(ns.asr)):
        print(json.dumps({"error": "pilot_needs_seal", "detail": "시범 프로젝트 결과는 --seal 로만 받습니다(D-15 8)"},
                         ensure_ascii=False))
        return 1
    if ns.words and ns.out and not words_out_protected(ns.from_project, ns.out):
        # 엔진을 돌리기 전에 멈춘다(평문 낱말 파일이 보호 구역 밖에 생기지 않게)
        print(json.dumps({"error": "words_out_unprotected",
                          "detail": "--words --out 의 낱말 파일은 프로젝트 보호 구역 안이어야 합니다 "
                                    "(예: --out <프로젝트>\\derived\\asr-link.json)"}, ensure_ascii=False))
        return 2
    if ns.vectors and ns.out and not vectors_out_protected(ns.from_project, ns.out):
        # 엔진을 돌리기 전에 멈춘다(평문 벡터 파일이 보호 구역 밖에 생기지 않게)
        print(json.dumps({"error": "vectors_out_unprotected",
                          "detail": "--vectors --out 의 벡터 파일은 프로젝트 보호 구역 안이어야 합니다 "
                                    "(예: --out <프로젝트>\\derived\\asr-link.json)"}, ensure_ascii=False))
        return 2
    if ns.seal and (not project or not ns.by):
        print(json.dumps({"error": "args", "detail": "--seal 에는 --project(또는 --from-project)와 --by 가 필요합니다"},
                         ensure_ascii=False))
        return 2
    if ns.words and ns.seal and _words_sealer() is None:
        # 짝 봉인 함수가 없으면 판정만 봉인되고 낱말이 빠진다 — 엔진을 돌리기 전에, 아무것도 봉인하지 않고 멈춘다
        print(json.dumps({"error": "words_seal_unsupported",
                          "detail": f"app.seal.{WORDS_SEAL_FN} 가 없어 전사 낱말을 짝으로 봉인할 수 없습니다"},
                         ensure_ascii=False))
        return 1
    if ns.vectors and ns.seal and _vectors_sealer() is None:
        # 짝 봉인 함수가 없으면 판정만 봉인되고 벡터가 빠진다 — 엔진을 돌리기 전에, 아무것도 봉인하지 않고 멈춘다
        print(json.dumps({"error": "vectors_seal_unsupported",
                          "detail": f"app.seal.{VECTORS_SEAL_FN} 가 없어 주제 벡터를 짝으로 봉인할 수 없습니다"},
                         ensure_ascii=False))
        return 1
    tbox: dict | None = {} if ns.topics else None
    wdoc = None
    vdoc = None
    try:
        if ns.inp:
            with open(ns.inp, "rb") as f:
                doc = air.parse_bytes(f.read(air.MAX_BYTES + 1))
        else:
            doc = _run_engine(ns.from_project, ns, tbox)
            if ns.words:
                from engine import transcript_words as TW
                try:
                    wdoc = _run_words(ns.from_project, ns)
                except TW.WordsError as we:
                    print(json.dumps({"error": "words", "code": we.code}, ensure_ascii=False))
                    return 1
            if ns.vectors:
                from engine import topic_vectors as TV
                try:
                    vdoc = _run_vectors(ns.from_project, ns)
                except TV.VectorsError as ve:   # 모델 없음 포함 — 아무것도 봉인·쓰지 않았다
                    print(json.dumps({"error": "vectors", "code": ve.code}, ensure_ascii=False))
                    return 1
        v1, info = to_ai_result(doc, graded_none=ns.graded_none)
    except (air.AiResultError, ConvertError) as e:
        print(json.dumps({"error": type(e).__name__, "detail": str(e)[:300]}, ensure_ascii=False))
        return 1
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({"error": type(e).__name__}, ensure_ascii=False))
        return 2
    run_meta = engine_run_meta(doc)
    fell_back = bool(ns.from_project) and ns.engine == "v0.3" and run_meta["engine_version"] != V03_VERSION
    if fell_back and ns.seal and not ns.allow_fallback:
        # 봉인 뒤에는 내용을 열어야만 판을 알 수 있다 — 물러났으면 봉인하지 않는다(시범 봉인 'link-v0.3 로 봉인' 확인)
        print(json.dumps({"error": "engine_fallback", **run_meta,
                          "detail": "v0.3 의미 비교가 돌지 않아 v0.2 로 물러났습니다. 엔진 venv·모델을 확인하거나 "
                                    "--allow-fallback 을 주세요"}, ensure_ascii=False))
        return 1
    data = dump(v1)
    tdata = dump(tbox["doc"]) if tbox else None
    wdata = None
    if wdoc is not None:
        from engine import transcript_words as TW
        wdata = TW.dump(wdoc)
    vdata = None
    if vdoc is not None:
        from engine import topic_vectors as TV
        vdata = TV.dump(vdoc)
    if ns.seal:
        from app import seal
        try:
            e = seal.seal_bytes(project, data, source="engine_link", by=ns.by, ai_root=ns.ai_root)
            te = seal.seal_topics_bytes(project, tdata, pair_sha256=e.sha256, by=ns.by, source="engine_topics",
                                        ai_root=ns.ai_root) if tdata is not None else None
            we = _words_sealer()(project, wdata, pair_sha256=e.sha256, by=ns.by, source="engine_words",
                                 ai_root=ns.ai_root) if wdata is not None else None
            ve = _vectors_sealer()(project, vdata, pair_sha256=e.sha256, by=ns.by, source="engine_vectors",
                                   ai_root=ns.ai_root) if vdata is not None else None
        except seal.SealError as ex:
            print(json.dumps({"error": type(ex).__name__, "detail": str(ex)[:300]}, ensure_ascii=False))
            return 1
        # 봉인 기록에 있는 것(해시·크기·검사)과 엔진 판·의미 비교 상태(메타데이터)만 찍는다.
        # 변환 건수는 내용을 해석한 값이라 찍지 않는다(06 7-2).
        out = {"sealed": e.sha256, "size": e.size, "check": e.check, "reject_reasons": e.reject_reasons,
               "new": e.new, "presealed": e.presealed, **run_meta, "engine_fallback": fell_back}
        if te is not None:
            out.update(topics_sealed=te.sha256, topics_size=te.size, topics_check=te.check,
                       topics_reject_reasons=te.reject_reasons)
        if we is not None:
            out.update(words_sealed=we.sha256, words_size=we.size, words_check=we.check,
                       words_reject_reasons=we.reject_reasons)
        if ve is not None:
            out.update(vectors_sealed=ve.sha256, vectors_size=ve.size, vectors_check=ve.check,
                       vectors_reject_reasons=ve.reject_reasons)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if e.check == "valid" and (te is None or te.check == "valid") \
            and (we is None or we.check == "valid") and (ve is None or ve.check == "valid") else 1
    os.makedirs(os.path.dirname(os.path.abspath(ns.out)), exist_ok=True)
    with open(ns.out, "wb") as f:
        f.write(data)
    if tdata is not None:
        with open(topics_out_path(ns.out), "wb") as f:
            f.write(tdata)
        info = {**info, "topics_out": topics_out_path(ns.out), "topic_blocks": len(tbox["doc"]["blocks"])}
    if wdata is not None:
        with open(words_out_path(ns.out), "wb") as f:
            f.write(wdata)
        info = {**info, "words_out": words_out_path(ns.out), "word_windows": len(wdoc["windows"])}
    if vdata is not None:
        with open(vectors_out_path(ns.out), "wb") as f:
            f.write(vdata)
        info = {**info, "vectors_out": vectors_out_path(ns.out), "vector_windows": len(vdoc["windows"])}
    print(json.dumps({"out": ns.out, **info, **run_meta, "engine_fallback": fell_back}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
