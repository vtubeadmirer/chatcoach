"""AI 결과 봉인·해제 명령줄 (06 7-1·7-2, 03 6-14, 08 D-15 7·8, D-22 2, D-23 3). 검토자(세션 밖) 도구다 — 라벨링 PC·세션에서
쓰지 않는다.

하위 명령
  seal    --project P --file F --by L01 [--source file|engine_link] [--remove-source]
          받은 파일을 봉인한다. 검사 결과(유효/거부 코드)·해시·크기만 찍는다. --remove-source 면 봉인 뒤 원본을 지운다
          (휴지통이 아니라 지운다 — 봉인 원본이 sealed\\ 에 같은 바이트로 남는다).
  status  --project P
          '봉인됨 n건' 과 봉인마다 해시 앞 12자·크기·시각·출처·검사·사전 봉인·해제 여부·짝(주제 흐름·전사 낱말·주제 벡터)
          유무·나중에 붙인 짝(pairs_attached)·해제 뒤 엔진 판. 봉인 파일은 열지 않는다.
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
  attach-pairs --project P --by L01 (--sha256 H | --all) <짝 입력>        (짝 보충, D-23 3 · 24 5-2 의 1)
          **이미 있는 판정 봉인**에 짝(주제 흐름·전사 낱말·주제 벡터)을 붙인다. 판정을 다시 봉인하지 않는다(사전 봉인 여부 그대로).
          해제 전에만 된다(풀린 판정에는 붙이지 않음). 이미 있는 짝은 건너뛴다('present') — 두 번 돌려도 같다.
          --sha256 은 64자 또는 status 가 찍는 앞 12자 이상(하나로 정해져야 함). --all = 아직 안 푼 '유효' 판정 봉인 모두.
          짝 입력 (둘 중 하나)
            --from-engine [--kinds topics,words,vectors] [--asr A] [--start-ms --end-ms --offset-ms --file-start-ms]
                          [--models-dir D] [--semantic-backend onnx|lmstudio] [--topics-semantic off|onnx|lmstudio]
                          [--no-identities]
                엔진(engine.topics · engine.transcript_words · engine.topic_vectors)을 이 자리에서 **메모리로** 돌려 만든다
                (파일을 쓰지 않음, tools/engine_to_ai 와 같은 함수 · 엔진 venv). 빠진 짝이 하나도 없으면 엔진을 띄우지 않는다.
                **판정을 만들 때와 같은 asr·범위·보정**을 준다(시범은 --asr <chatcoach-data\\sealed-work\\…>).
            --topics F · --words F · --vectors F [--remove-source]
                받은 파일. 시범 프로젝트(pilot-*)는 전사 낱말·벡터 파일이 <데이터>\\sealed-work\\ 아래에 있어야 한다(아니면 읽기
                전에 종료 2, pair_file_unprotected — 전사문과 같은 등급의 평문을 아무 데나 두지 않게, D-15 8). --remove-source 는
                모두 붙은 뒤에만 원본을 지운다(os.remove — 휴지통 아님, README-tools 의 sealed-work 규칙).
          먼저 모두 검사하고 하나라도 거부되면 그 판정에는 아무 짝도 붙이지 않는다(app.seal.attach_pairs).
공통: [--ai-root <폴더>] (기본: 프로젝트가 …\\projects\\<slug> 면 그 옆 …\\ai, 아니면 <데이터>\\ai)

출력은 JSON 한 줄. 해제 전에는 내용을 찍지 않는다. 해제 뒤에도 run_id·엔진 판·건수·코드·해시 앞 12자만 찍는다.
오류 줄은 {"error": 예외 이름, "code": 원인 코드(app.seal.SEAL_ERROR_CODES), "detail": 문장}.
종료 코드: 0 정상 · 1 거부·조건 안 됨·읽기 실패·엔진 실패 · 2 입력 오류
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
import re
from typing import Callable, Mapping, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import seal  # noqa: E402

SHA_PREFIX_RE = re.compile(r"\A[0-9a-f]{12,64}\Z")
SEALED_WORK = "sealed-work"            # 시범 엔진 작업 폴더 <데이터>\sealed-work (D-15 8)
PROTECTED_PAIR_FILES = ("words", "vectors")   # 전사문과 같은 등급인 짝(시범이면 sealed-work 안의 파일만)


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _err(e: BaseException) -> dict:
    return {"error": type(e).__name__, "code": seal.error_code(e), "detail": str(e)[:300]}


def _entry_row(e: seal.SealEntry) -> dict:
    return {"sha256_12": e.sha256[:12], "size": e.size, "sealed_at": e.sealed_at, "source": e.source,
            "check": e.check, "reject_reasons": e.reject_reasons, "presealed": e.presealed,
            "unsealed": e.unsealed_at is not None, "conditions_met": e.conditions_met,
            "pairs": {k: getattr(e, f"{k}_sha256") is not None for k in seal.PAIR_KINDS},
            "pairs_attached": list(e.pairs_attached), "engine_version": e.engine_version}


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


# ───────────────────────────────────────────────────────────── 짝 보충 (attach-pairs)

class AttachInputError(ValueError):
    """attach-pairs 입력 오류(종료 2). code 는 출력의 원인 코드."""

    def __init__(self, msg: str, code: str):
        super().__init__(msg)
        self.code = code


class EngineUnavailable(RuntimeError):
    """엔진으로 짝을 만들지 못했다(종료 1). code: engine_unavailable · words:<코드> · vectors:<코드> · topics_check."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _is_pilot(project: str) -> bool:
    return os.path.basename(os.path.abspath(project)).lower().startswith("pilot-")


def sealed_work_dir(project: str) -> str:
    """<데이터>\\sealed-work — 프로젝트가 <데이터>\\projects\\<slug> 일 때 (D-15 8 의 시범 엔진 작업 폴더)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(project))), SEALED_WORK)


def _inside(path: str, root: str) -> bool:
    p = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    r = os.path.normcase(os.path.realpath(os.path.abspath(root))).rstrip("\\/")
    return p.startswith(r + os.sep)


def check_pair_files(project: str, files: Mapping[str, str]) -> None:
    """시범 프로젝트면 전사 낱말·벡터 파일이 sealed-work 아래인지(글자로만 — 열지 않는다). 아니면 AttachInputError."""
    if not _is_pilot(project):
        return
    sw = sealed_work_dir(project)
    bad = [k for k, p in files.items() if k in PROTECTED_PAIR_FILES and not _inside(p, sw)]
    if bad:
        raise AttachInputError("시범 프로젝트의 전사 낱말·벡터 파일은 <데이터>\\sealed-work 아래에 두어야 합니다: "
                               + ",".join(bad), "pair_file_unprotected")


def resolve_targets(st: seal.SealStatus, *, sha256: str | None, all_: bool) -> list[seal.SealEntry]:
    """짝을 붙일 판정 봉인. --all = 아직 안 푼 '유효' 봉인 모두. --sha256 = 64자 또는 앞 12자 이상(하나로 정해져야 함)."""
    if all_:
        return [e for e in st.entries if e.unsealed_at is None and e.check == "valid"]
    h = (sha256 or "").lower()
    if not SHA_PREFIX_RE.match(h):
        raise AttachInputError("--sha256 은 16진 12~64자", "sha_format")
    hit = [e for e in st.entries if e.sha256.startswith(h)]
    if len(hit) != 1:
        raise AttachInputError(f"--sha256 앞머리에 맞는 봉인이 {len(hit)}개입니다(하나여야 함)",
                               "no_seal_record" if not hit else "sha_ambiguous")
    return hit


def missing_kinds(targets: Sequence[seal.SealEntry], kinds: Sequence[str]) -> list[str]:
    """판정 봉인들 가운데 하나라도 빠진 짝 종류(kinds 순서대로)."""
    return [k for k in kinds if any(getattr(e, f"{k}_sha256") is None for e in targets)]


def engine_pairs(project: str, ns, kinds: Sequence[str]) -> dict[str, bytes]:
    """엔진을 메모리로 돌려 짝 바이트를 만든다(tools/engine_to_ai 와 같은 함수, 파일을 쓰지 않음). 엔진 venv 가 필요하다."""
    try:
        from tools import engine_to_ai as e2a
    except ImportError:
        raise EngineUnavailable("engine_unavailable") from None
    out: dict[str, bytes] = {}
    try:
        if "topics" in kinds:
            from engine import topics as TP
            utts, msgs, nicks, inp = TP.inputs_from_project(
                project, asr_path=ns.asr, offset_ms=ns.offset_ms, file_start_ms=ns.file_start_ms,
                use_identities=not ns.no_identities)
            try:
                out["topics"] = e2a.dump(e2a._run_topics(utts, msgs, nicks, inp, ns))
            except e2a.ConvertError:
                raise EngineUnavailable("topics_check") from None
        if "words" in kinds:
            from engine import transcript_words as TW
            try:
                out["words"] = TW.dump(e2a._run_words(project, ns))
            except TW.WordsError as we:
                raise EngineUnavailable(f"words:{we.code}") from None
        if "vectors" in kinds:
            from engine import topic_vectors as TV
            try:
                out["vectors"] = TV.dump(e2a._run_vectors(project, ns))
            except TV.VectorsError as ve:
                raise EngineUnavailable(f"vectors:{ve.code}") from None
    except ImportError:
        raise EngineUnavailable("engine_unavailable") from None
    return out


#: 엔진 짝 만들기(시험이 바꿔 끼운다 — 실제 엔진·모델 없이 attach-pairs 길을 본다)
ENGINE_PAIRS: Callable[[str, object, Sequence[str]], dict[str, bytes]] = engine_pairs


def _read_pair_files(files: Mapping[str, str]) -> dict[str, bytes]:
    out = {}
    for k, p in files.items():
        spec_max = {"topics": seal.TOPICS_MAX_BYTES, "words": seal.WORDS_MAX_BYTES, "vectors": seal.VECTORS_MAX_BYTES}[k]
        with open(p, "rb") as f:
            out[k] = f.read(spec_max + 1)
    return out


def attach_project(project: str, ns) -> tuple[int, dict]:
    """attach-pairs 한 번 → (종료 코드, 출력 dict). 숫자·코드·해시 앞 12자만."""
    files = {k: getattr(ns, k) for k in seal.PAIR_KINDS if getattr(ns, k, None)}
    if ns.from_engine and files:
        raise AttachInputError("--from-engine 과 짝 파일(--topics·--words·--vectors)은 같이 줄 수 없습니다", "args")
    if not ns.from_engine and not files:
        raise AttachInputError("--from-engine 또는 짝 파일(--topics·--words·--vectors) 하나 이상을 주세요", "args")
    if ns.remove_source and ns.from_engine:
        raise AttachInputError("--remove-source 는 짝 파일과 함께만 씁니다", "args")
    if ns.from_engine:
        kinds = [k.strip() for k in (ns.kinds or ",".join(seal.PAIR_KINDS)).split(",") if k.strip()]
        if not kinds or any(k not in seal.PAIR_KINDS for k in kinds):
            raise AttachInputError(f"--kinds 는 {','.join(seal.PAIR_KINDS)} 가운데", "pair_kind")
    else:
        kinds = [k for k in seal.PAIR_KINDS if k in files]
        check_pair_files(project, files)                       # 읽기 전에 (시범이면 sealed-work 안만)
    st = seal.seal_status(project, ai_root=ns.ai_root)
    if st.problems:
        raise seal.SealError("seal.jsonl 이 깨졌습니다", code="seal_jsonl_corrupt")
    targets = resolve_targets(st, sha256=ns.sha256, all_=ns.all)
    out: dict = {"targets": len(targets), "kinds": kinds, "engine_run": False, "source_removed": False, "results": []}
    if not targets:
        out["code"] = "no_target"
        return 1, out
    need = missing_kinds(targets, kinds)
    out["missing"] = need
    if not need:                                              # 할 일 없음 — 엔진을 띄우지 않는다
        out["results"] = [{"sha256_12": e.sha256[:12], "rows": [{"kind": k, "status": "present"} for k in kinds]}
                          for e in targets]
        out["ok"] = True
        return 0, out
    if ns.from_engine:
        ns.seal = True                                        # 엔진 함수의 allow_pilot(봉인 경로만)
        docs = ENGINE_PAIRS(project, ns, need)
        out["engine_run"] = True
    else:
        docs = _read_pair_files({k: files[k] for k in need})
    ok = True
    for e in targets:
        want = {k: docs[k] for k in need if getattr(e, f"{k}_sha256") is None and k in docs}
        if not want:
            out["results"].append({"sha256_12": e.sha256[:12],
                                   "rows": [{"kind": k, "status": "present"} for k in kinds]})
            continue
        r = seal.attach_pairs(project, e.sha256, want, by=ns.by, source="engine" if ns.from_engine else "file",
                              ai_root=ns.ai_root)
        ok &= r.ok
        rows = [{"kind": x["kind"], "status": x["status"], "sha256_12": (x["sha256"] or "")[:12], "check": x["check"],
                 "reject_reasons": x["reject_reasons"]} for x in r.rows]
        rows += [{"kind": k, "status": "present"} for k in kinds if k not in want]
        rows.sort(key=lambda x: seal.PAIR_KINDS.index(x["kind"]))
        out["results"].append({"sha256_12": e.sha256[:12], "presealed": r.presealed,
                               "after_primary_lock": r.after_primary_lock, "rows": rows})
    if ok and ns.remove_source:
        for p in files.values():
            os.remove(p)
        out["source_removed"] = True
    out["ok"] = ok
    return (0 if ok else 1), out


def _add_engine_args(p) -> None:
    p.add_argument("--from-engine", action="store_true", help="엔진을 메모리로 돌려 짝을 만든다(엔진 venv)")
    p.add_argument("--kinds", help="--from-engine 으로 만들 짝(기본 topics,words,vectors)")
    p.add_argument("--asr", help="판정을 만들 때와 같은 asr.json(시범은 sealed-work 의 것)")
    for a in ("--start-ms", "--end-ms", "--offset-ms", "--file-start-ms"):
        p.add_argument(a, type=int)
    p.add_argument("--no-identities", action="store_true")
    p.add_argument("--models-dir")
    p.add_argument("--semantic-backend", choices=("onnx", "lmstudio"), default="onnx")
    p.add_argument("--topics-semantic", choices=("off", "onnx", "lmstudio"), default="off")
    for k in seal.PAIR_KINDS:
        p.add_argument(f"--{k}", help=f"받은 {k} 파일")
    p.add_argument("--remove-source", action="store_true")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python tools/ai_seal.py", description="AI 결과 봉인·해제(검토자 도구)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seal", "status", "check", "unseal", "verify", "attach-pairs"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.add_argument("--ai-root")
        if name == "seal":
            p.add_argument("--file", required=True)
            p.add_argument("--by", required=True)
            p.add_argument("--source", choices=seal.SOURCE_KINDS, default="file")
            p.add_argument("--remove-source", action="store_true")
        if name in ("unseal", "attach-pairs"):
            which = p.add_mutually_exclusive_group(required=True)
            which.add_argument("--sha256")
            which.add_argument("--all", action="store_true")
            p.add_argument("--by", required=True)
        if name == "unseal":
            p.add_argument("--early-reason")
        if name == "attach-pairs":
            _add_engine_args(p)
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
        if ns.cmd == "attach-pairs":
            try:
                code, out = attach_project(ns.project, ns)
            except AttachInputError as e:
                _print({"error": "AttachInputError", "code": e.code, "detail": str(e)[:300]})
                return 2
            except EngineUnavailable as e:
                _print({"error": "EngineUnavailable", "code": e.code})
                return 1
            _print(out)
            return code
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
                errors.append({"sha256_12": e.sha256[:12], **_err(ex)})
        _print({"unsealed": done, "errors": errors,
                "skipped_rejected": sum(1 for e in st.entries if e.unsealed_at is None and e.check != "valid")})
        return 0 if not errors else 1
    except seal.SealBlockedError as e:
        _print({"error": "SealBlockedError", "code": e.code, "reasons": e.reasons})
        return 1
    except seal.SealError as e:
        _print(_err(e))
        return 1
    except (OSError, ValueError) as e:
        _print({"error": type(e).__name__})
        return 2


if __name__ == "__main__":
    sys.exit(main())
