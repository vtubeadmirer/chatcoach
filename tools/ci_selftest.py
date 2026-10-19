"""빌드된 exe 를 한글 폴더 사본에서 --selftest 로 점검한다 (04 4-2 6단계, release.yml).

사용: python tools/ci_selftest.py --dist dist --korean-dir "<임시>\\테스트 문서" --expect-version X.Y.Z [--expect-channel release]
dist 는 건드리지 않고 사본에서 실행한다. 결과 JSON 을 출력하고 exe 의 종료 코드로 끝낸다.
시작 시간(exe 실행 → 종료)도 함께 출력한다 (02 9장 첫 측정값용 참고치. 창을 띄우지 않으므로 하한이다).
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
import shutil
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.appspec import SPEC  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dist", default="dist")
    p.add_argument("--korean-dir", required=True)
    p.add_argument("--expect-version", required=True)
    p.add_argument("--expect-channel", default="release")
    p.add_argument("--keep", action="store_true", help="사본을 지우지 않는다")
    a = p.parse_args(argv)
    src = os.path.join(a.dist, SPEC.zip_top)
    dst = os.path.join(a.korean_dir, SPEC.zip_top)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(a.korean_dir, exist_ok=True)
    shutil.copytree(src, dst)
    exe = os.path.join(dst, SPEC.exe_name)
    out = os.path.join(a.korean_dir, "selftest.json")
    cmd = [exe, "--selftest", out, "--expect-version", a.expect_version, "--expect-channel", a.expect_channel]
    print("실행:", " ".join(cmd))
    t0 = time.perf_counter()
    r = subprocess.run(cmd, timeout=180)
    dt = time.perf_counter() - t0
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as f:
            rep = json.load(f)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("결과 파일 없음")
    print(f"exit {r.returncode}, {dt:.1f}초")
    if not a.keep:
        shutil.rmtree(dst, ignore_errors=True)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
