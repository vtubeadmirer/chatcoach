"""릴리스 zip 을 앱과 같은 validate_zip 으로 검사한다 (04 4-2 7단계, S-43).

사용: python tools/check_release_zip.py <zip> --version X.Y.Z [--lite]
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.appspec import SPEC  # noqa: E402
from app.updater import UpdateError, validate_zip  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("zip")
    p.add_argument("--version", required=True)
    p.add_argument("--lite", action="store_true")
    a = p.parse_args(argv)
    try:
        s = validate_zip(a.zip, a.version, a.lite, SPEC)
    except UpdateError as ex:
        print(f"실패: {ex.message}")
        return 1
    print(f"통과: {os.path.basename(a.zip)}  파일 {s.file_count}개, 압축 해제 {s.unpacked_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
