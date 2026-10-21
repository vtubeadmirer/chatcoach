"""C단계 여섯째 묶음 성능 벤치 (14 6-1 의 1·2·3번). 합성 데이터만 — 실제 프로젝트·채팅을 열지 않는다.

잰다:
  1. 채팅 색인 캐시 (app/chat_index.py): 20만 건 messages.jsonl 에서 처음 만들기 · 다시 읽기(캐시 맞음) · 첫 조회.
     비교값으로 지금 길(jsonl.read 로 전부 파싱)도 잰다. 목표: 02 9장 '프로젝트 열기 3초 이하(색인 캐시가 있으면 1초)'.
  2. 재현 검증 워커 (app/workers.py SampleVerifyWorker): 20만 건 표본 재현 검증을 UI 스레드에서 바로 돌릴 때(그동안 화면 멈춤)와
     워커로 돌릴 때의 걸린 시간, 그리고 워커가 도는 동안 UI 스레드 타이머(5ms)의 가장 긴 틈.
  3. 타임라인 부분 갱신 (app/ui/timeline.py): 20만 마커에서 add_marker · remove_marker · update_marker 한 번 평균과
     set_markers(전체 정렬) + 정적 트랙 다시 그리기 한 번.

쓰는 법 (앱 .venv):
    set QT_QPA_PLATFORM=offscreen
    .venv\\Scripts\\python.exe tools\\bench_c6.py [--n 200000] [--json out.json]

출력은 숫자뿐이다. 합성 채팅 본문은 '합성' 같은 자리표시다. 임시 폴더는 끝나면 지운다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

DUR_MS = 8 * 3_600_000          # VOD 8시간 (02 9장 기준 규모)
TEXTS = ["합성", "자리표시 문장", "ㅋㅋ", "합성 질문?", "가짜 인사입니다"]
KINDS = ["text"] * 18 + ["donation", "system"]


def synth_records(n: int):
    """chat-message 모양 합성 줄. viewer_id·가림 닉네임도 지어낸 값(색인에 들어가지 않는지 보려고 넣는다)."""
    import hashlib
    for i in range(n):
        t = (i * 7_919) % DUR_MS if i % 997 else None
        yield {"chat_id": "c" + hashlib.sha256(b"bench-%d" % i).hexdigest()[:20], "t_ms": t,
               "server_time_ms": None, "type_code": 1 if i % 20 else 10, "kind": KINDS[i % len(KINDS)],
               "status": "NORMAL", "viewer_id": "vw_aaaaaaaaaaaa", "is_anonymous": False, "nickname_masked": "가*",
               "role": "common_user", "text": TEXTS[i % len(TEXTS)],
               "raw_ref": {"fetch_id": "F20261021-abcd", "page": i // 200, "index": i % 200}}


def write_messages(path: str, n: int) -> None:
    from app import jsonl
    with open(path, "wb") as f:
        for r in synth_records(n):
            f.write(jsonl.dumps_line(r))


# ---------------------------------------------------------------------------- 1. 채팅 색인
def bench_chat_index(tmp: str, n: int) -> dict:
    from app import chat_index as ci
    from app import jsonl
    msgs = os.path.join(tmp, "messages.jsonl")
    write_messages(msgs, n)
    cache = os.path.join(tmp, "cache", "bench", ci.FILE_NAME)
    t0 = time.perf_counter()
    res = jsonl.read(msgs, max_line_bytes=ci.MESSAGES_MAX_LINE)
    full_parse_s = time.perf_counter() - t0
    del res
    t0 = time.perf_counter()
    idx, st1 = ci.load_or_build(msgs, cache)
    build_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    idx2, st2 = ci.load_or_build(msgs, cache)
    reload_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    idx2.chat_times(idx2.chat_ids[:1000])
    first_lookup_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    ci.load_or_build(msgs, cache, verify_full=True)
    reload_full_s = time.perf_counter() - t0
    with open(cache, "rb") as f:
        blob = f.read()
    leaked = any(s.encode("utf-8") in blob for s in TEXTS + ["가*", "vw_", "common_user"])
    return {"n": n, "messages_mb": round(os.path.getsize(msgs) / 1e6, 1),
            "cache_mb": round(os.path.getsize(cache) / 1e6, 1),
            "status": [st1, st2], "timed": len(idx.by_time),
            "jsonl_read_full_s": round(full_parse_s, 3), "build_s": round(build_s, 3),
            "reload_s": round(reload_s, 3), "first_lookup_1000_s": round(first_lookup_s, 4),
            "reload_verify_full_s": round(reload_full_s, 3), "cache_has_text_or_ids": leaked,
            "target_build_s": 3.0, "target_reload_s": 1.0,
            "_messages_path": msgs}


# ---------------------------------------------------------------------------- 2. 재현 검증 워커
def bench_verify_worker(msgs: str) -> dict:
    import hashlib
    from PySide6.QtCore import QCoreApplication, QElapsedTimer, QTimer
    from app import jsonl
    from app.core import sampling as sp
    from app.workers import SampleVerifyWorker

    with open(msgs, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    records = jsonl.read(msgs, max_line_bytes=1 << 20).records
    pop = sp.build_population(records, 0, DUR_MS, ())
    man = sp.build_manifest(
        project_id="11111111-2222-4333-8444-555555555555",
        source_segment={"session_id": "L01-P-20261021-a1b2", "entity_id": "7f3c2a1b-0000-4000-8000-000000000001",
                        "version_id": "7f3c2a1b-0000-4000-8000-000000000002", "start_ms": 0, "end_ms": DUR_MS},
        guide_version="0.1", thresholds_sha256="a" * 64, sample_seed="00112233445566778899aabbccddeeff", draw_no=1,
        population=pop, messages_sha256=sha, n=100, census_max=100, drawn_at="2026-10-21T14:03:11+09:00",
        drawn_by="L01", app_version="0.0.0")
    del records, pop

    def verify_one(m):
        # 작업대 _verify_sample 과 같은 일: 채팅 파일을 읽고(파일 I/O) 같은 입력으로 다시 뽑아 견준다
        with open(msgs, "rb") as f:
            s = hashlib.sha256(f.read()).hexdigest()
        recs = jsonl.read(msgs, max_line_bytes=1 << 20).records
        return sp.verify_manifest(m, recs, messages_sha256=s)

    t0 = time.perf_counter()
    v = verify_one(man)
    sync_s = time.perf_counter() - t0          # UI 스레드에서 바로 돌리면 이만큼 화면이 멈춘다

    w = SampleVerifyWorker([man], verify_one)
    rows: list = []
    w.verified.connect(rows.extend)
    gaps: list[float] = []
    clock = QElapsedTimer()
    clock.start()
    last = [clock.nsecsElapsed()]

    def tick():
        now = clock.nsecsElapsed()
        gaps.append((now - last[0]) / 1e6)
        last[0] = now
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(tick)
    timer.start()
    t0 = time.perf_counter()
    w.start()
    while w.isRunning() or not w.isFinished():
        QCoreApplication.processEvents()
        w.wait(1)
    QCoreApplication.processEvents()
    worker_s = time.perf_counter() - t0
    timer.stop()
    gaps.sort()
    return {"manifest_items": len(man.get("items") or []), "verify_ok": bool(v.ok), "worker_rows": rows,
            "sync_ui_blocked_s": round(sync_s, 3), "worker_total_s": round(worker_s, 3),
            "ui_timer_ticks": len(gaps), "ui_max_gap_ms": round(gaps[-1], 1) if gaps else None,
            "ui_p95_gap_ms": round(gaps[int(len(gaps) * 0.95) - 1], 1) if len(gaps) >= 20 else None,
            "note": "파이썬 GIL 때문에 워커가 계산하는 동안에도 UI 틈이 생긴다. 멈춤(sync)과 견줄 것"}


# ---------------------------------------------------------------------------- 3. 타임라인 부분 갱신
def bench_timeline(n: int) -> dict:
    from app.ui.timeline import Timeline
    w = Timeline()
    w.resize(1600, w.height())
    w.set_duration(DUR_MS)
    w.set_fps(30.0)
    step = DUR_MS // n
    w.set_markers("labels", [(i * step, "reaction" if i % 5 else "recheck") for i in range(n)])
    out = {"markers": n}
    for view_name, view in (("full", (0, DUR_MS)), ("10min", (3_600_000, 3_600_000 + 600_000))):
        w.set_view(*view)
        w.grab()
        builds = w.cache_builds
        k = 50
        t0 = time.perf_counter()
        for i in range(k):
            w.add_marker("labels", view[0] + 1_003 + i * ((view[1] - view[0]) // (k + 1)), "reaction")
        add_ms = (time.perf_counter() - t0) / k * 1000
        t0 = time.perf_counter()
        for i in range(k):
            w.update_marker("labels", view[0] + 1_003 + i * ((view[1] - view[0]) // (k + 1)), "reaction",
                            view[0] + 2_003 + i * ((view[1] - view[0]) // (k + 1)), "recheck")
        upd_ms = (time.perf_counter() - t0) / k * 1000
        t0 = time.perf_counter()
        for i in range(k):
            w.remove_marker("labels", view[0] + 2_003 + i * ((view[1] - view[0]) // (k + 1)), "recheck")
        rem_ms = (time.perf_counter() - t0) / k * 1000
        rebuilt = w.cache_builds - builds
        w.grab()
        rebuilt_after_paint = w.cache_builds - builds
        items = w.markers("labels") + [(view[0] + 5_000, "reaction")]
        t0 = time.perf_counter()
        w.set_markers("labels", items)
        w._ensure_cache()
        full_ms = (time.perf_counter() - t0) * 1000
        out[view_name] = {"add_ms": round(add_ms, 3), "update_ms": round(upd_ms, 3), "remove_ms": round(rem_ms, 3),
                          "dense": w._is_dense("labels"), "cache_rebuilds_during_ops": rebuilt,
                          "cache_rebuilds_after_paint": rebuilt_after_paint, "patches": w.cache_patches,
                          "set_markers_plus_redraw_ms": round(full_ms, 1)}
    out["target_ms"] = 8.0
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C6 성능 벤치 (합성 데이터)")
    ap.add_argument("--n", type=int, default=200_000, help="합성 채팅 수·마커 수 (기본 20만)")
    ap.add_argument("--json", help="결과를 JSON 파일로도 쓴다")
    args = ap.parse_args(argv)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    tmp = tempfile.mkdtemp(prefix="cc_bench_c6_")
    try:
        r1 = bench_chat_index(tmp, args.n)
        msgs = r1.pop("_messages_path")
        r2 = bench_verify_worker(msgs)
        r3 = bench_timeline(args.n)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    result = {"chat_index": r1, "verify_worker": r2, "timeline": r3}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
