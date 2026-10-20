"""사람 손 받아쓰기 txt → asr 비교용 json (09 3-1 의 5번 '손으로 감싸기' 를 대신한다).

입력  UTF-8 txt (BOM 있어도 됨, 줄바꿈 \\r\\n·\\n·\\r 아무거나). 들리는 대로 받아 쓴 글.
출력  {"format": "chatcoach.asr-manual", "source": "manual", "model": {"name": "manual"}, "segments": [{"text": …}], …}
      engine.compare 가 읽는 칸(segments[].text, model.name)만 맞춘다. 시각이 없어 engine.link 의 asr 로는 못 쓴다.

  python -m engine transcript <받아쓰기.txt> --out <asr.manual.json> [--single] [--skip-comments]

  --single         모든 줄을 한 segment 로 합친다(기본은 빈 줄이 아닌 줄마다 segment 하나).
                   compare 는 공백을 빼고 이어 붙여 비교하므로 어느 쪽이든 char_diff_ratio 는 같다.
  --skip-comments  '#' 으로 시작하는 줄을 버린다(메모용). 기본은 버리지 않는다 — 받아쓴 말이 '#' 으로 시작할 수 있다.

표준 출력에는 숫자만 낸다. 받아쓴 글은 --out 파일에만 쓴다(저장소 밖 chatcoach-data 아래에 둘 것).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

FORMAT = "chatcoach.asr-manual"
FORMAT_VERSION = 1


def decode_text(b: bytes) -> str:
    """UTF-8(BOM 허용)만 받는다. 깨진 바이트가 있으면 몇 번째 줄인지 알려 주고 실패한다(조용히 바꾸지 않는다)."""
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError as e:
        line = b[:e.start].count(b"\n") + 1
        raise ValueError(f"UTF-8 이 아닙니다 ({line}번째 줄, 바이트 {e.start}). 메모장에서 'UTF-8' 로 다시 저장하세요") from None


def split_lines(text: str, *, skip_comments: bool = False) -> list[str]:
    """줄바꿈을 하나로 맞추고, 줄 앞뒤 공백을 걷고, 빈 줄을 버린다. 줄 안의 연속 공백은 하나로."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n").replace(" ", "\n")
    out = []
    for raw in text.split("\n"):
        s = " ".join(raw.replace("﻿", "").split())
        if not s:
            continue
        if skip_comments and s.startswith("#"):
            continue
        out.append(s)
    return out


def build(text: str, *, single: bool = False, skip_comments: bool = False, source_file: str | None = None,
          source_sha256: str | None = None) -> dict:
    lines = split_lines(text, skip_comments=skip_comments)
    segs = [{"id": 0, "text": " ".join(lines)}] if (single and lines) else [{"id": i, "text": s} for i, s in enumerate(lines)]
    chars = sum(len("".join(s["text"].split())) for s in segs)
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "source": "manual",
        "model": {"name": "manual"},
        "source_file": source_file,
        "source_sha256": source_sha256,
        "stats": {"lines": len(lines), "segments": len(segs), "chars_no_space": chars},
        "segments": segs,
    }


def write_json(doc: dict, path: str) -> None:
    """UTF-8(BOM 없음 — engine.compare 가 utf-8 로 연다), 임시 파일 → 바꾸기."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.part-{os.getpid()}"
    data = json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def convert(txt_path: str, out_path: str, *, single: bool = False, skip_comments: bool = False) -> dict:
    with open(txt_path, "rb") as f:
        b = f.read()
    doc = build(decode_text(b), single=single, skip_comments=skip_comments,
                source_file=os.path.basename(txt_path), source_sha256=hashlib.sha256(b).hexdigest())
    if not doc["segments"]:
        raise ValueError("받아쓴 글이 비어 있습니다")
    write_json(doc, out_path)
    return doc


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m engine transcript", description="손 받아쓰기 txt → asr 비교용 json")
    ap.add_argument("txt")
    ap.add_argument("--out", required=True)
    ap.add_argument("--single", action="store_true", help="모든 줄을 segment 하나로")
    ap.add_argument("--skip-comments", action="store_true", help="'#' 으로 시작하는 줄을 버림")
    ns = ap.parse_args(argv)
    try:
        doc = convert(ns.txt, ns.out, single=ns.single, skip_comments=ns.skip_comments)
    except (OSError, ValueError) as e:
        print(json.dumps({"error": type(e).__name__, "detail": str(e)[:300]}, ensure_ascii=False))
        return 1
    print(json.dumps({"out": ns.out, "source": "manual", "source_sha256": doc["source_sha256"], **doc["stats"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
