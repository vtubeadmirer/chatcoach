"""가상 채팅 가려내기 대표값(H-5 관문) — 여러 방송의 가려내기 기록을 합쳐 통과·실패를 낸다 (app.discern_report,
20 7-3, 25 6-2).

사용:
  python tools/discern_report.py --projects-root <…\\projects> [--labeler L01] [--sessions] [--allow-locked-pilot]
  python tools/discern_report.py --project <P1> --project <P2> [--labeler L01]

- 가려내기 화면은 세션 하나라 늘 '방송 1편 = 표본 적음' 이다. 관문(생성 40·실제 40·방송 2편 이상에서 탐지율 ≤ 0.65 이고
  κ ≤ 0.4)은 이 도구가 프로젝트(= 방송)마다의 discern\\*.jsonl 을 다시 읽어 낸다. 다시 봄 세션은 뺀다.
- 정답(출처)은 기록에 없다 — 판정 줄의 키로 그 세션이 가리킨 시나리오 파일(바이트 해시가 같은 것)에서 찾는다.
- 시범 프로젝트(pilot-*)는 기본으로 폴더를 열지 않고 건너뛴다(pilot_skipped). --allow-locked-pilot 이면 시범 세션이
  모두 잠긴 것만 넣는다.
- 읽기만 한다. 표준 출력은 JSON 한 줄 — 숫자·ID·사유 코드만(본문·닉네임·경로 없음). 사람 클릭 0(Claude 가 돌린다).

종료 코드: 0 계산함(관문 값은 gate 칸 — pass · fail · low_n · unknown), 1 입력 오류.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import discern_report as dr  # noqa: E402
from app import discern_store as ds  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="ChatCoach 가상 채팅 가려내기 대표값 (H-5 관문)")
    ap.add_argument("--projects-root", default=None, help="프로젝트 폴더들이 있는 곳 (바로 아래 project.json 이 있는 폴더)")
    ap.add_argument("--project", action="append", default=[], help="프로젝트 폴더 (여러 번 줄 수 있음)")
    ap.add_argument("--labeler", default=None, help="이 라벨러의 세션만 (예 L01)")
    ap.add_argument("--sessions", action="store_true", help="세션마다 숫자·ID·사유 코드도 찍는다")
    ap.add_argument("--allow-locked-pilot", action="store_true",
                    help="시범 프로젝트도 넣는다 — 시범 세션이 모두 잠긴 것만")
    a = ap.parse_args(argv)
    if a.labeler is not None and not ds.LABELER_ID_RE.match(a.labeler):
        print(json.dumps({"error": "labeler_format"}), file=sys.stderr)
        return 1
    roots = [os.path.abspath(p) for p in a.project]
    if a.projects_root:
        if not os.path.isdir(a.projects_root):
            print(json.dumps({"error": "projects_root_missing"}), file=sys.stderr)
            return 1
        roots += dr.project_dirs(a.projects_root)
    if not roots:
        print(json.dumps({"error": "no_projects"}), file=sys.stderr)
        return 1
    for r in roots:
        if not os.path.isdir(r):
            print(json.dumps({"error": "project_missing"}), file=sys.stderr)
            return 1
    out = dr.report(roots, labeler_id=a.labeler, allow_locked_pilot=a.allow_locked_pilot, with_sessions=a.sessions)
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
