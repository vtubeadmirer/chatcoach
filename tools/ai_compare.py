"""봉인 해제된 AI 결과(run)와 최종 세트를 견주는 AI 비교 보고서를 만든다 (app.ai_compare, 06 6장·7장, 16 8-1 6번).

사용:
  python tools/ai_compare.py --project <프로젝트 폴더> --run-id <run_id>
         [--ai-root <data\\ai 폴더>] [--out <출력 폴더>] [--eval-settings <분석 설정 JSON>]
         [--gate pass|fail|unknown] [--segment-gate pass|fail|unknown] [--by L01]
         [--primary <1차 세션 ID>] [--relabel <재라벨 세션 ID> ...] [--adjudication <조정 세션 ID> ...]
         [--final-set-zip <출력 zip>]

- run 은 <ai 루트>\\<project_id>\\seal.jsonl 의 ai.unsealed 기록과 runs\\<run_id>.json 으로만 읽는다(폴더 규칙은 app.seal:
  프로젝트가 '…\\projects\\<slug>' 면 그 옆 '…\\ai', 아니면 <data>\\ai). 해제 기록이 없으면
  라벨 파일을 열지 않고 종료 코드 3. 봉인 원본(sealed\\*.bin)은 열지 않는다.
- 최종 세트는 잠긴 세션만 쓴다(06 7-1 조건). 잠기지 않았으면 종료 코드 3.
- --gate: 사람 일치도 기준(06 5장) 판정. fail 이면 엄격 점수 '보류', unknown(기본)이면 내되 경고.
- --final-set-zip: 같은 최종 세트를 zip 으로도 내보낸다(export_type ground_truth_lenient).
- 출력: <출력 폴더>/<project_id>/<report_id>/report.json · report.md · tables/*.csv · manifest.json.
  기본 출력 폴더는 일치도 보고서와 같다. 프로젝트 폴더·저장소(git 작업 트리) 안이면 거부(종료 코드 2).
- 운영자(검토자) 도구다. 라벨링 중인 라벨러에게 보고서를 주지 않는다(06 7-3).

표준 출력에는 숫자와 판정만 찍는다(본문·닉네임·경로 없음).
종료 코드: 0 성공, 1 실패, 2 출력 위치 거부, 3 해제되지 않은 run·잠기지 않은 세션.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import agreement_report as ar  # noqa: E402
from app import ai_compare as ac  # noqa: E402
from app import export  # noqa: E402
from app.project import open_project  # noqa: E402

_GATE = {"pass": True, "fail": False, "unknown": None}


def _num(v, nd=3) -> str:
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="ChatCoach AI 비교 보고서 (봉인 해제된 run ↔ 최종 세트)")
    ap.add_argument("--project", required=True, help="프로젝트 폴더 (project.json 이 있는 곳)")
    ap.add_argument("--run-id", required=True, help="봉인 해제된 run_id")
    ap.add_argument("--ai-root", default=None, help="AI 결과 폴더 (기본 app.seal 규칙)")
    ap.add_argument("--out", default=None, help="출력 폴더 (프로젝트·저장소 밖)")
    ap.add_argument("--eval-settings", default=None, help="분석 설정 JSON. 없으면 기본값")
    ap.add_argument("--gate", choices=sorted(_GATE), default="unknown", help="반응 사람 일치도 기준 판정")
    ap.add_argument("--segment-gate", choices=sorted(_GATE), default=None, help="구간 사람 일치도 기준 판정(기본 --gate)")
    ap.add_argument("--by", default=None, help="보고서 created_by 라벨러 코드")
    ap.add_argument("--primary", default=None, help="1차 세션 ID (기본: 프로젝트의 유일한 1차 세션)")
    ap.add_argument("--relabel", action="append", default=None, help="재라벨 세션 ID (여러 번)")
    ap.add_argument("--adjudication", action="append", default=None, help="조정 세션 ID (여러 번)")
    ap.add_argument("--final-set-zip", default=None, help="최종 세트 zip 도 쓴다")
    a = ap.parse_args(argv)
    gate = {"reaction": _GATE[a.gate], "segment": _GATE[a.segment_gate or a.gate]}
    try:
        proj = open_project(a.project)
        if proj.data is None:
            print("실패: project.json 을 읽지 못했습니다", file=sys.stderr)
            return 1
        out_root = a.out or ar.default_out_root(proj.root)
        ar.check_out_root(out_root, proj.root)
        run = ac.load_unsealed_run(proj.root, a.run_id, ai_root=a.ai_root)   # 해제 확인이 먼저(라벨 파일 전)
        settings = ar.load_eval_settings(a.eval_settings) if a.eval_settings else None
        res = ac.make_report(proj.root, run, out_root=out_root, eval_settings=settings, human_gate=gate,
                             created_by=a.by, primary_session_id=a.primary, relabel_session_ids=a.relabel,
                             adjudication_session_ids=a.adjudication, ai_root=a.ai_root)
        if a.final_set_zip:
            export.export_final_set(proj.root, a.final_set_zip, primary_session_id=a.primary,
                                    relabel_session_ids=a.relabel, adjudication_session_ids=a.adjudication,
                                    created_by=a.by, ai_root=a.ai_root)
    except (ar.ReportLocationError, export.ExportLocationError) as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    except (ac.RunNotUnsealedError, ar.NotLockedError) as e:
        print(f"거부: {e}", file=sys.stderr)
        return 3
    except (ac.AICompareError, ar.ReportError, export.ExportError, OSError, ValueError) as e:
        print(f"실패: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    r = res.report
    print(f"보고서 {r['report_id']}  사전 봉인 {r['header']['pre_sealed']}  자료 {r['header']['dataset_split']}")
    e1 = r.get("e1") or {}
    if e1.get("status") != ac.STATUS_NOT_PROVIDED:
        for kind in ("lenient", "strict"):
            m = e1[kind]
            if m["status"] == ac.STATUS_WITHHELD:
                print(f"E1 {kind}: 보류")
                continue
            print(f"E1 {kind}: n {m['n']}  정확 {_num(m['grade']['exact'])}  κ_w {_num(m['grade']['kappa_w'])}  "
                  f"정밀도 {_num(m['binary']['precision'])}  재현율 {_num(m['binary']['recall'])}  F1 {_num(m['binary']['f1'])}  "
                  f"κ {_num(m['binary']['kappa'])}  ({m['status']})")
        print(f"E2 참고 n {r['e2']['lenient']['n']} 재현율 {_num(r['e2']['lenient']['recall'])}  E3 {r['e3']['count']}건")
    seg = r.get("segments") or {}
    if seg.get("status") != ac.STATUS_NOT_PROVIDED:
        m = seg["lenient"]
        print(f"구간 참고: 칸 {m['cells']}  일치율 {_num(m['time_agreement'])}  κ {_num(m['kappa'])}  "
              f"경계 짝 {seg['boundaries']['matched']}")
    for w in res.warnings:
        print(f"경고: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
