"""잠긴 두 세션의 일치도 보고서를 만든다 (app.agreement_report, 06 4장·5장·7장, 08 D-11 8).

사용:
  python tools/agreement_report.py --project <프로젝트 폴더> --a <세션 ID> --b <세션 ID>
         [--out <출력 폴더>] [--eval-settings <분석 설정 JSON>] [--samples-only] [--by L01]

- 두 세션이 모두 잠겼을 때(locks/<session_id>.json)만 만든다. 하나라도 안 잠겼으면 라벨 파일을 열지 않고 종료 코드 3.
- 출력: <출력 폴더>/<project_id>/<report_id>/report.json · report.md. 기본 출력 폴더는 프로젝트가 '…\\projects\\<slug>'
  에 있으면 그 옆 '…\\reports', 아니면 <data>\\reports. 프로젝트 폴더·저장소(git 작업 트리) 안이면 거부(종료 코드 2).
- --samples-only: 표본 판정을 samples/ 매니페스트(계열마다 가장 큰 sample_rev)의 채팅으로만 계산한다.
  없으면 두 세션의 현재 유효 반응 전부를 같은 chat_id 로 짝짓는다.
- 운영자(검토자) 도구다. 두 세션의 라벨을 읽으므로 라벨링 중인 라벨러에게 보고서를 주지 않는다(06 7-3).

표준 출력에는 숫자와 판정만 찍는다(본문·닉네임·경로 없음).
종료 코드: 0 성공, 1 실패, 2 출력 위치 거부, 3 잠기지 않은 세션.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import agreement_report as ar  # noqa: E402


def _num(v, nd=3) -> str:
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="ChatCoach 일치도 보고서 (잠긴 두 세션)")
    ap.add_argument("--project", required=True, help="프로젝트 폴더 (project.json 이 있는 곳)")
    ap.add_argument("--a", required=True, help="세션 A (보통 1차)")
    ap.add_argument("--b", required=True, help="세션 B (재라벨·두 번째 사람)")
    ap.add_argument("--out", default=None, help="출력 폴더 (프로젝트·저장소 밖)")
    ap.add_argument("--eval-settings", default=None, help="분석 설정 JSON (06 10장 eval_settings). 없으면 기본값")
    ap.add_argument("--samples-only", action="store_true", help="표본 매니페스트의 채팅만 비교")
    ap.add_argument("--by", default=None, help="보고서 created_by 라벨러 코드")
    a = ap.parse_args(argv)
    try:
        settings = ar.load_eval_settings(a.eval_settings) if a.eval_settings else None
        res = ar.make_report(a.project, a.a, a.b, out_root=a.out, eval_settings=settings,
                             samples_only=a.samples_only, created_by=a.by)
    except ar.ReportLocationError as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    except ar.NotLockedError as e:
        print(f"거부: {e}", file=sys.stderr)
        return 3
    except (ar.ReportError, OSError, ValueError) as e:
        print(f"실패: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    r = res.report
    seg, sm = r["segments"], r["samples"]
    print(f"보고서 {r['report_id']}  비교 {r['header']['comparison']}  같은 machine_id {r['header']['same_machine']}")
    print(f"구간 칸 {seg['cells_compared']}  κ {_num(seg['kappa'])}  핵심 일치율 {_num(seg['core']['agreement'])}  "
          f"경계 짝 {seg['boundaries']['matched']}  |Δt| 중앙값 {_num(seg['boundaries']['median_sec'], 1)}초  "
          f"90분위 {_num(seg['boundaries']['p90_sec'], 1)}초")
    print(f"표본 둘 다 {sm['judged']['both']}  이진 n {sm['binary']['n']} κ {_num(sm['binary']['kappa'])}  "
          f"등급 n {sm['grade']['n']} κ_w {_num(sm['grade']['kappa_w'])}  판단 불가 {sum(sm['undeterminable'].values())}  "
          f"불명확 {sum(v for k, v in sm['unclear'].items() if k != 'agreement')}")
    for g in r["gates"]:
        print(f"기준 {g['key']} {_num(g['value'])} {g['op']} {g['gate']}  n {g['n']}  {g['verdict']}")
    for w in res.warnings:
        print(f"경고: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
