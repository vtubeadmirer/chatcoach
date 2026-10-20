"""python -m engine <명령>

  asr <wav> --out <asr.json> [--model large-v3-turbo] [--prompt-file F] [--device cuda] [--compute-type float16]
      [--language ko] [--no-vad] [--no-words] [--beam-size 5] [--models-dir D] [--measure-vram] [--no-fallback]
  compare <a.asr.json> <b.asr.json>        두 결과의 문자 차이 비율(숫자만)
  model-hash [--model large-v3-turbo]      모델을 받아(없으면) model.bin sha256 을 출력
  transcript <txt> --out <json> [--single] [--skip-comments]
                                           손 받아쓰기 txt → compare 용 json (engine.transcript)
  score --labels <csv> --link <json> [--start-ms N --end-ms N] [--messages F | --project P] [--out report.json]
                                           손라벨 ↔ link 결과 채점 (engine.score)
  link --project <P> --out <json> …        engine.link 의 main 을 그대로 부른다(인자도 그대로)

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
}


def _passthrough(cmd: str, rest: list[str]) -> int:
    import importlib
    mod = importlib.import_module(f"{__package__ or 'engine'}.{_PASSTHROUGH[cmd][0]}")
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

    c = sub.add_parser("compare", help="두 asr.json 의 문자 차이 비율")
    c.add_argument("a")
    c.add_argument("b")

    m = sub.add_parser("model-hash", help="모델 받기 + model.bin sha256")
    m.add_argument("--model", default="large-v3-turbo")
    m.add_argument("--models-dir")

    for name, (_, desc) in _PASSTHROUGH.items():   # 도움말 목록에만 보인다. 실제 처리는 위의 _passthrough
        sub.add_parser(name, help=desc + " (python -m engine " + name + " -h)", add_help=False)

    ns = ap.parse_args(args)

    if ns.cmd == "asr":
        from .asr import transcribe, validate_asr, write_asr_json
        prompt = None
        if ns.prompt_file:
            with open(ns.prompt_file, encoding="utf-8-sig") as f:
                prompt = f.read().strip() or None
        res = transcribe(ns.wav, model=ns.model, device=ns.device, compute_type=ns.compute_type,
                         language=None if ns.language == "auto" else ns.language, initial_prompt=prompt,
                         vad=not ns.no_vad, word_timestamps=not ns.no_words, models_dir=ns.models_dir,
                         beam_size=ns.beam_size, measure_vram=ns.measure_vram,
                         allow_cpu_fallback=not ns.no_fallback)
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
    return 2


if __name__ == "__main__":
    sys.exit(main())
