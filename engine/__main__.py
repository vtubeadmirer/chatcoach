"""python -m engine <명령>

  asr <wav> --out <asr.json> [--model large-v3-turbo] [--prompt-file F] [--device cuda] [--compute-type float16]
      [--language ko] [--no-vad] [--no-words] [--beam-size 5] [--models-dir D] [--measure-vram] [--no-fallback]
      [--asr-start-ms N]                   wav 가 영상 파일의 N ms 부터 뽑은 음성이면 asr.json audio.asr_start_ms 에 적는다
                                           (08 D-34 — 아래 읽는 명령들이 그 값을 쓴다)
  compare <a.asr.json> <b.asr.json>        두 결과의 문자 차이 비율(숫자만)
  model-hash [--model large-v3-turbo]      모델을 받아(없으면) model.bin sha256 을 출력
  transcript <txt> --out <json> [--single] [--skip-comments]
                                           손 받아쓰기 txt → compare 용 json (engine.transcript)
  score --labels <csv> --link <json> [--start-ms N --end-ms N] [--messages F | --project P] [--out report.json]
                                           손라벨 ↔ link 결과 채점 (engine.score)
  link --project <P> --out <json> …        engine.link 의 main 을 그대로 부른다(인자도 그대로)
                                           기본 --engine v0.3(의미 비교), 모델이 없으면 v0.2 로 물러남
  topics --project <P> --out <json> …      engine.topics 의 main 을 그대로 부른다(주제 흐름, 숫자만 출력)
  transcript-words --project <P> [--out <json>] …
                                           engine.transcript_words 의 main(asr.json 을 VOD 로 보정한 시각별 전사 낱말,
                                           기본 <P>/derived/asr-words.json, 시범은 거부, 숫자만 출력)
  topic-vectors --project <P> [--out <json>] …
                                           engine.topic_vectors 의 main(VOD 10초 창마다 전사 글의 e5-small 벡터.
                                           창 글은 저장하지 않음, 기본 <P>/derived/asr-topic-vectors.json, 시범은 거부,
                                           모델이 없으면 종료 3. 엔진 venv 에서)
  link · topics · transcript-words · topic-vectors 는 모두 --asr-start-ms N 을 받는다: asr.json 이 영상 파일의 N ms 부터
                                           잘라 낸 음성이면(extract_audio --start-ms N) 영상 파일 시각 = asr 시각 + N 으로
                                           보정 전에 옮긴다. 주지 않으면 asr.json 의 audio.asr_start_ms(없으면 0), 주었는데
                                           그 값과 다르면 asr_start_conflict 로 거부(종료 2). --file-start-ms(클립 파일이
                                           VOD 의 어디서 시작하나, project.json 보정과 짝)와 다르다 — project.json 보정이
                                           있으면 그 값은 상쇄되어 asr 시각을 옮기지 않는다
  scenario --project <P> --from T --to T … tools/make_scenario.py 의 main 을 그대로 부른다(가상 채팅 시나리오)
  generate --project <P> --from T --to T (--fake <응답.json> | --model <id> [--url http://127.0.0.1:1234]) …
                                           engine.generate 의 main(H-3 생성 채팅, 지시문 판 gen-chat-v1). 앞 1~2분 전사
                                           낱말·주제 키워드·채팅 수만 보내고 본문·닉네임은 보내지 않음, 거르기 1~5,
                                           LM Studio 는 localhost 만. --fake 는 합성 가짜 응답 파일(서버를 부르지 않음)
  semantic-info [--models-dir D] [--lmstudio]
                                           의미 비교 실행기 상태(모델 이름·해시·실행기). 받지 않는다.
                                           --lmstudio 면 이 PC 의 LM Studio 서버(localhost)도 본다

표준 출력에는 요약 숫자만 낸다. 전사 문장은 --out 파일에만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys


def _utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_PASSTHROUGH = {
    # 하위 명령 → (모듈, 설명). 인자를 그 모듈의 main 에 그대로 넘긴다(도움말도 그쪽 것).
    "transcript": ("transcript", "손 받아쓰기 txt → compare 용 json"),
    "score": ("score", "손라벨 csv ↔ link 결과 채점"),
    "link": ("link", "채팅 ↔ 발화 연결 시험판(engine.link)"),
    "topics": ("topics", "주제 흐름(topic-flow) 만들기(engine.topics)"),
    "transcript-words": ("transcript_words", "VOD 로 보정한 시각별 전사 낱말 만들기(engine.transcript_words)"),
    "topic-vectors": ("topic_vectors", "VOD 창별 주제 벡터 만들기(engine.topic_vectors, 로컬 e5-small, 엔진 venv)"),
    "scenario": ("tools/make_scenario.py", "가상 채팅 시나리오(retrieved) 만들기(tools/make_scenario.py)"),
    "generate": ("generate", "가상 채팅 생성(generated, 로컬 LLM·--fake 가짜 응답, engine.generate)"),
}


def _passthrough(cmd: str, rest: list[str]) -> int:
    import importlib
    target = _PASSTHROUGH[cmd][0]
    if target.endswith(".py"):
        # 저장소 tools/ 의 도구(패키지가 아니다): 파일 경로로 불러 main 을 부른다
        import importlib.util
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *target.split("/"))
        spec = importlib.util.spec_from_file_location("_engine_tool_" + cmd.replace("-", "_"), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.main(rest)
    mod = importlib.import_module(f"{__package__ or 'engine'}.{target}")
    return mod.main(rest)


def main(argv: list[str] | None = None) -> int:
    _utf8()
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in _PASSTHROUGH:
        return _passthrough(args[0], args[1:])
    ap = argparse.ArgumentParser(prog="python -m engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("asr", help="wav 전사 → asr.json")
    a.add_argument("wav")
    a.add_argument("--out", required=True)
    a.add_argument("--model", default="large-v3-turbo")
    a.add_argument("--prompt-file", help="initial_prompt 로 넣을 UTF-8 텍스트 파일 (닉네임 힌트 등)")
    a.add_argument("--device", default="cuda")
    a.add_argument("--compute-type", default="float16")
    a.add_argument("--language", default="ko", help="'auto' 면 자동 감지")
    a.add_argument("--no-vad", action="store_true")
    a.add_argument("--no-words", action="store_true")
    a.add_argument("--beam-size", type=int, default=5)
    a.add_argument("--models-dir")
    a.add_argument("--measure-vram", action="store_true")
    a.add_argument("--no-fallback", action="store_true", help="GPU 실패 시 cpu 로 내려가지 않고 실패")
    a.add_argument("--asr-start-ms", type=int, default=None,
                   help="wav 가 영상 파일의 몇 ms 부터 뽑은 음성인지. asr.json audio.asr_start_ms 에 적는다(08 D-34)")

    c = sub.add_parser("compare", help="두 asr.json 의 문자 차이 비율")
    c.add_argument("a")
    c.add_argument("b")

    m = sub.add_parser("model-hash", help="모델 받기 + model.bin sha256")
    m.add_argument("--model", default="large-v3-turbo")
    m.add_argument("--models-dir")

    si = sub.add_parser("semantic-info", help="의미 비교(엔진 v0.3) 실행기·모델 상태")
    si.add_argument("--models-dir")
    si.add_argument("--lmstudio", action="store_true", help="LM Studio 로컬 서버(localhost:1234)도 본다")

    for name, (_, desc) in _PASSTHROUGH.items():   # 도움말 목록에만 보인다. 실제 처리는 위의 _passthrough
        sub.add_parser(name, help=desc + " (python -m engine " + name + " -h)", add_help=False)

    ns = ap.parse_args(args)

    if ns.cmd == "asr":
        from .asr import mark_asr_start, transcribe, validate_asr, write_asr_json
        if ns.asr_start_ms is not None and ns.asr_start_ms < 0:
            ap.error("--asr-start-ms 는 0 이상")
        prompt = None
        if ns.prompt_file:
            with open(ns.prompt_file, encoding="utf-8-sig") as f:
                prompt = f.read().strip() or None
        res = transcribe(ns.wav, model=ns.model, device=ns.device, compute_type=ns.compute_type,
                         language=None if ns.language == "auto" else ns.language, initial_prompt=prompt,
                         vad=not ns.no_vad, word_timestamps=not ns.no_words, models_dir=ns.models_dir,
                         beam_size=ns.beam_size, measure_vram=ns.measure_vram,
                         allow_cpu_fallback=not ns.no_fallback)
        if ns.asr_start_ms is not None:
            mark_asr_start(res, ns.asr_start_ms)
        probs = validate_asr(res)
        write_asr_json(res, ns.out)
        summary = {"out": ns.out, "model": res["model"]["name"], "model_bin_sha256": res["model"]["model_bin_sha256"],
                   "device": res["settings"]["device"], "compute_type": res["settings"]["compute_type"],
                   "fallback": res["fallback"], "warnings": res["warnings"], "audio": res["audio"], "timing": res["timing"], "vram": res["vram"],
                   "stats": res["stats"], "format_problems": probs}
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 1 if probs else 0

    if ns.cmd == "compare":
        from .compare import compare_files
        print(json.dumps(compare_files(ns.a, ns.b), ensure_ascii=False, indent=1))
        return 0

    if ns.cmd == "model-hash":
        from .asr import add_cuda_dll_dirs, model_bin_sha256, resolve_model_dir
        add_cuda_dll_dirs()
        d, repo = resolve_model_dir(ns.model, ns.models_dir)
        print(json.dumps({"model": ns.model, "repo": repo, "dir": d, "model_bin_sha256": model_bin_sha256(d)},
                         ensure_ascii=False, indent=1))
        return 0

    if ns.cmd == "semantic-info":
        from . import semantic as S
        emb, st = S.load_default(ns.models_dir)
        out = {"status": st["status"], "active": st["active"], "detail": st["detail"], "model": st["model"],
               "models_dir": ns.models_dir or S.default_models_dir(),
               "expected": {k: S.E5_SMALL[k] for k in ("name", "repo", "revision", "license", "model_sha256")},
               "lmstudio": S.probe_lmstudio() if ns.lmstudio else None}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
