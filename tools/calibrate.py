"""시각 보정 명령줄 도구 (docs/design/03-data-model.md 4-2, 02-architecture.md 7-5).

사용:
  python tools/calibrate.py --project <프로젝트 폴더> --anchor <영상시각>=<채팅시각> [--anchor ...]
                            [--file-start-ms N] [--by L01] [--kind nickname_call] [--dry-run]

  영상시각 = 로컬 영상 파일에서 들린 시각, 채팅시각 = 그 채팅의 VOD 시각(messages.t_ms 축).
  시각 형식: 1:02:03.5 · 62:03 · 3723.5 · 01:02:03,500 · 1h2m3s
  --file-start-ms : 영상 파일이 VOD 의 일부(ClipSlicer 클립)일 때 파일 0초의 VOD 시각(ms).
                    스키마에 칸이 없어 offset_ms 에 합쳐 저장하고 판의 note 에 값을 적는다.

하는 일: 앵커 차이의 중앙값으로 오프셋을 정하고, 흩어짐(>3초)·이상치·앵커 부족을 경고한 뒤
app.project 의 Project.update 로 calibration 에 새 판을 붙인다(active_rev 로).
app.project 를 불러오지 못하면 --dry-run 으로만 동작한다(계산 결과와 저장될 JSON 만 출력).

종료 코드: 0 정상 · 1 입력 오류 · 2 저장 불가(app.project 없음 등)
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import datetime as _dt
import json
import os
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import timecode as tc  # noqa: E402


def parse_anchor(text: str) -> tuple[int, int]:
    """'<영상시각>=<채팅시각>' → (video_ms, chat_ms)."""
    if text.count("=") != 1:
        raise ValueError(f"앵커는 '<영상시각>=<채팅시각>' 모양이어야 한다: {text!r}")
    v_s, c_s = text.split("=")
    v, c = tc.parse_time(v_s), tc.parse_time(c_s)
    if v is None or c is None:
        raise ValueError(f"시각을 읽지 못했다: {text!r}")
    if v < 0 or c < 0:
        raise ValueError(f"앵커 시각은 음수일 수 없다: {text!r}")
    return v, c


def build_rev(fit: tc.OffsetFit, pairs, *, rev: int, media_rev: int, by: str, kind: str,
              file_start_ms: int, now_iso: str, status: str | None = None) -> dict:
    anchors = tuple(
        tc.Anchor(anchor_id=str(uuid.uuid4()), vod_ms=c, file_ms=v, kind=kind,
                  note=("이상치" if out else None))
        for (v, c), out in zip(pairs, fit.outliers)
    )
    note_parts = []
    if file_start_ms:
        note_parts.append(f"file_start_in_vod_ms={file_start_ms} (offset_ms 에 합쳐 저장)")
    if fit.warnings:
        note_parts.append("경고: " + ",".join(fit.warnings))
    note_parts.append(f"MAD={fit.mad_ms}ms 흩어짐={fit.spread_ms}ms")
    cal = tc.Calibration(
        offset_ms=fit.offset_ms, anchors=anchors, rev=rev, method="single_offset",
        media_rev=media_rev, status=status or fit.suggested_status,
        residual_max_ms=fit.residual_max_ms, created_at=now_iso, created_by=by,
        note="; ".join(note_parts)[:500],
    )
    return tc.calibration_to_json(cal, file_start_in_vod_ms=file_start_ms)


def print_fit(fit: tc.OffsetFit, pairs, file_start_ms: int) -> None:
    print(f"앵커 {fit.n}개 (클립 시작 {tc.fmt_time(file_start_ms)})")
    print("  #  채팅 시각       영상 시각       차이")
    for i, ((v, c), d, out) in enumerate(zip(pairs, fit.diffs_ms, fit.outliers), 1):
        mark = "  <- 이상치" if out else ""
        print(f"  {i}  {tc.fmt_display(c):<14} {tc.fmt_display(v):<14} {tc.fmt_offset(d)}{mark}")
    print(f"중앙값 {tc.fmt_offset(fit.offset_ms)} ({fit.offset_ms}ms) · 흩어짐 {fit.spread_ms}ms · "
          f"MAD {fit.mad_ms}ms · 최대 잔차 {fit.residual_max_ms}ms")
    msgs = {
        "too_few_anchors": f"앵커가 {tc.MIN_ANCHORS_VERIFIED}개보다 적다 — verified 로 확정할 수 없다",
        "spread_large": f"앵커 차이의 흩어짐이 {tc.SPREAD_WARN_MS}ms 를 넘는다 — 앞부분이 잘린 파일이거나 중간에 끊긴 VOD 일 수 있다",
        "outliers": "이상치 앵커가 있다 — 그 앵커를 다시 확인하라",
    }
    for w in fit.warnings:
        print("경고: " + msgs.get(w, w))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="앵커로 영상-채팅 시각 오프셋을 계산해 project.json 에 저장")
    ap.add_argument("--project", required=True, help="프로젝트 폴더")
    ap.add_argument("--anchor", action="append", default=[], help="<영상시각>=<채팅시각> (여러 번)")
    ap.add_argument("--file-start-ms", type=int, default=0, help="영상 파일 0초의 VOD 시각(ms), 클립일 때")
    ap.add_argument("--by", default="L01", help="보정한 라벨러 ID (기본 L01)")
    ap.add_argument("--kind", default="nickname_call", choices=tc.ANCHOR_KINDS)
    ap.add_argument("--status", choices=("verified", "unverified"), default=None,
                    help="상태를 직접 정함(기본: 앵커 3개 이상·경고 없음이면 verified)")
    ap.add_argument("--dry-run", action="store_true", help="저장하지 않고 결과만 출력")
    a = ap.parse_args(argv)

    if not a.anchor:
        print("오류: --anchor 가 하나 이상 필요하다", file=sys.stderr)
        return 1
    if a.file_start_ms < 0:
        print("오류: --file-start-ms 는 0 이상", file=sys.stderr)
        return 1
    try:
        pairs = [parse_anchor(x) for x in a.anchor]
        fit = tc.fit_offset(pairs, file_start_in_vod_ms=a.file_start_ms)
    except ValueError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    print_fit(fit, pairs, a.file_start_ms)
    if a.status == "verified" and fit.n < tc.MIN_ANCHORS_VERIFIED:
        print(f"오류: 앵커 {tc.MIN_ANCHORS_VERIFIED}개 미만은 verified 로 둘 수 없다", file=sys.stderr)
        return 1

    now_iso = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    kw = dict(by=a.by, kind=a.kind, file_start_ms=a.file_start_ms, now_iso=now_iso, status=a.status)

    try:
        from app.project import open as open_project  # 다른 에이전트가 만드는 모듈
    except Exception as e:  # noqa: BLE001 — 없거나 깨졌으면 미리보기만
        open_project = None
        import_err = e

    if a.dry_run or open_project is None:
        if open_project is None and not a.dry_run:
            print(f"오류: app.project 를 불러오지 못해 저장할 수 없다 ({import_err!r}). --dry-run 으로만 쓸 수 있다",
                  file=sys.stderr)
            return 2
        rev_json = build_rev(fit, pairs, rev=1, media_rev=0, **kw)
        print("--dry-run: 저장하지 않음. 새 판(rev·media_rev 는 실제 저장 때 project.json 에서 정함):")
        print(json.dumps(rev_json, ensure_ascii=False, indent=2))
        return 0

    saved = {}

    def mutate(d: dict) -> None:
        cur = d.get("calibration")
        media_rev = int(((d.get("media") or {}).get("media_rev")) or 0)
        rev_json = build_rev(fit, pairs, rev=tc.next_rev(cur), media_rev=media_rev, **kw)
        d.__setitem__("calibration", tc.append_rev(cur, rev_json))
        saved["rev"] = rev_json["rev"]
        saved["offset_ms"] = rev_json["offset_ms"]
        saved["status"] = rev_json["status"]

    p = open_project(a.project)
    if getattr(p, "read_only", False):
        print("오류: 읽기 전용으로 열렸다 — " + "; ".join(getattr(p, "problems", [])[:3]), file=sys.stderr)
        return 2
    try:
        try:
            p.update(mutate, by=a.by)
        except TypeError:  # by 를 받지 않는 update (계약 최소형)
            p.update(mutate)
    except Exception as e:  # noqa: BLE001 — ConflictError·검증 실패 등은 저장하지 않고 알린다
        print(f"오류: 저장하지 못했다 ({type(e).__name__}: {e})", file=sys.stderr)
        return 2
    print(f"저장함: 보정 판 {saved['rev']} (active) · offset_ms={saved['offset_ms']} · status={saved['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
