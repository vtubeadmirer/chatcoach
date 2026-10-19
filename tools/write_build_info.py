"""빌드 폴더에 VERSION · build_info.json · deps.txt 를 쓴다 (build.ps1 이 부른다. docs/design/04-update-and-release.md 2-1).

사용: python tools/write_build_info.py --app dist\\StreamLabeler --channel release|test

<app>\\_internal\\ 아래에 쓰는 것 (모두 UTF-8, BOM 없음, LF):
  VERSION          저장소 VERSION 의 값 한 줄
  build_info.json  {"version", "commit", "built_at"(UTC, 초 단위, Z), "channel"}
  deps.txt         번들 ffmpeg·libmpv 의 태그와 아카이브 SHA-256 (경량 업데이트 판정, 04 6-4)
"""
import sys

# CI 러너처럼 콘솔 인코딩이 UTF-8 이 아닌 곳에서도 한글 출력이 죽지 않게
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import argparse
import json
import os
import subprocess
from datetime import datetime, timezone

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import get_ffmpeg  # noqa: E402
import get_libmpv  # noqa: E402
import sync_version  # noqa: E402


def git_commit() -> str:
    """저장소 HEAD 의 전체 커밋 해시. git 이 없거나 실패하면 'unknown'."""
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    out = p.stdout.decode("ascii", errors="replace").strip()
    return out if p.returncode == 0 and out else "unknown"


def _write(path: str, text: str) -> None:
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="빌드 폴더에 VERSION · build_info.json · deps.txt 를 쓴다")
    ap.add_argument("--app", required=True, help=r"빌드된 앱 폴더 (예: dist\StreamLabeler)")
    ap.add_argument("--channel", required=True, choices=["release", "test"])
    args = ap.parse_args(argv)

    internal = os.path.join(os.path.abspath(args.app), "_internal")
    if not os.path.isdir(internal):
        print(f"_internal 폴더가 없습니다: {internal}", file=sys.stderr)
        return 1
    try:
        version = sync_version.read_version()
    except (OSError, ValueError) as ex:
        print(f"VERSION 을 읽지 못했습니다: {ex}", file=sys.stderr)
        return 1

    info = {
        "version": version,
        "commit": git_commit(),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channel": args.channel,
    }
    files = {
        "VERSION": f"{version}\n",
        "build_info.json": json.dumps(info, ensure_ascii=False, indent=2) + "\n",
        "deps.txt": (f"ffmpeg={get_ffmpeg.TAG}\n"
                     f"libmpv={get_libmpv.TAG}\n"
                     f"ffmpeg_sha256={get_ffmpeg.SHA256}\n"
                     f"libmpv_sha256={get_libmpv.SHA256}\n"),
    }
    for name, text in files.items():
        path = os.path.join(internal, name)
        _write(path, text)
        print(f"--- {path}")
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
