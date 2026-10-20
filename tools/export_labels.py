"""프로젝트의 현재 유효 라벨을 zip 하나로 내보낸다 (app.export.export_labels, 03 16장).

사용:
  python tools/export_labels.py --project <프로젝트 폴더> --out <저장할 .zip> [--include-text]
         [--include-nickname-masked] [--include-heard-text] [--session <session_id> ...] [--by L01] [--overwrite]

- zip 안: labels.jsonl · segments.csv · reactions.csv · missed.csv · manifest.json. 프로젝트 폴더에는 쓰지 않는다.
- 기본값(05 10-4): 채팅 본문·가림 닉네임·발화 받아쓰기는 빠진다. 원 닉네임·userIdHash 는 어떤 옵션으로도 나가지 않는다.
- 출력 위치가 저장소(git 작업 트리)나 프로젝트 폴더 안이면 거부(종료 코드 2). OneDrive·UNC 아래면 경고하고 쓴다.
- 운영자 도구다. 모든 세션의 라벨을 읽으므로 라벨링 중인 라벨러에게 결과 zip 을 주지 않는다(블라인드, 03 12장).

표준 출력에는 숫자만 찍는다(행 수·세션 수·파일 크기). 본문·닉네임·경로는 찍지 않는다.
종료 코드: 0 성공, 1 실패, 2 출력 위치 거부.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import export  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="ChatCoach 라벨 내보내기 (JSONL · CSV · manifest zip)")
    ap.add_argument("--project", required=True, help="프로젝트 폴더 (project.json 이 있는 곳)")
    ap.add_argument("--out", required=True, help="저장할 zip 경로 (저장소·프로젝트 폴더 밖)")
    ap.add_argument("--include-text", action="store_true", help="채팅 본문과 증거 메모를 넣는다 (기본: 빼기)")
    ap.add_argument("--include-nickname-masked", action="store_true", help="가림 닉네임(예: 김**)을 넣는다")
    ap.add_argument("--include-heard-text", action="store_true", help="발화 받아쓰기(heard_text)를 넣는다")
    ap.add_argument("--session", action="append", default=None, help="이 세션만 (여러 번 줄 수 있음)")
    ap.add_argument("--by", default=None, help="매니페스트 created_by 라벨러 코드 (기본: project.json 의 마지막 저장자)")
    ap.add_argument("--overwrite", action="store_true", help="같은 이름의 zip 이 있으면 바꾼다")
    a = ap.parse_args(argv)
    opts = export.ExportOptions(
        include_text=a.include_text, include_nickname_masked=a.include_nickname_masked,
        include_heard_text=a.include_heard_text,
        session_ids=tuple(a.session) if a.session else None, overwrite=a.overwrite)
    try:
        res = export.export_labels(a.project, a.out, opts, created_by=a.by)
    except export.ExportLocationError as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    except FileExistsError as e:
        print(f"실패: {e}", file=sys.stderr)
        return 1
    except (export.ExportError, OSError, ValueError) as e:
        print(f"실패: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    m = res.manifest
    print(f"행 {res.rows}  " + "  ".join(f"{k} {v}" for k, v in res.by_kind.items()))
    print(f"세션 {len(res.session_ids)}  기준서 {len(m['guides'])}  무효 기록 {m['counts'].get('invalid_records', 0)}")
    if m["counts"]["excluded"]:
        print("제외(첫 사유) " + "  ".join(f"{k} {v}" for k, v in sorted(m["counts"]["excluded"].items())))
    for f in m["files"]:
        print(f"파일 {f['name']} {f['size']}바이트 {f['rows']}행")
    for w in res.warnings:
        print(f"경고: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
