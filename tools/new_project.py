"""새 프로젝트 폴더를 만든다 (app.project.create).

사용:
  python tools/new_project.py --root <폴더> --title "<방송 제목>" --video-no N [--labeler L01]
         [--purpose "..."] [--engine-author yes|no] [--machine4 abcd]

성공하면 표준 출력 첫 줄에 프로젝트 폴더 경로를 찍고 종료 코드 0. 경고는 표준 오류로.
실패(폴더가 이미 있음·형식 오류)는 종료 코드 1.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import project  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="ChatCoach 새 프로젝트 만들기")
    ap.add_argument("--root", required=True, help="프로젝트들을 담는 폴더 (그 아래에 <slug> 폴더를 만든다)")
    ap.add_argument("--title", required=True, help="방송 제목")
    ap.add_argument("--video-no", required=True, help="치지직 VOD 번호")
    ap.add_argument("--labeler", default="L01", help="만드는 라벨러 ID (기본 L01)")
    ap.add_argument("--purpose", default=project.DEFAULT_PURPOSE, help="수집·처리 목적 한 줄")
    ap.add_argument("--engine-author", choices=("yes", "no"), default=None,
                    help="만드는 사람이 엔진 규칙·코드 작성자인지. 주면 명부(labelers)에 primary 로 넣는다")
    ap.add_argument("--machine4", default=None, help="modified_by 의 PC 표시 4자 (기본: 이 PC 에서 계산)")
    a = ap.parse_args(argv)
    ea = None if a.engine_author is None else a.engine_author == "yes"
    try:
        p = project.create(a.root, a.title, a.video_no, a.purpose, a.labeler,
                           engine_author=ea, machine4=a.machine4)
    except FileExistsError as e:
        print(f"오류: 이미 있는 폴더입니다: {e.filename or e}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    print(p.root)
    for w in p.warnings:
        print(f"경고: {w}", file=sys.stderr)
    if p.read_only:
        for pr in p.problems:
            print(f"문제: {pr}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
