"""채팅 표 벤치 (02 9장): 합성 20만·36만 행 로드·필터·필터 해제·set_time_ms.

목표를 넘으면 경고만 하고 실패시키지 않는다(기계마다 다르다). 결과는 -s 로 보면 표로 나온다.
실제 프로젝트 로드 시간은 CHATCOACH_BENCH_REAL=<messages.jsonl 경로> 를 주면 한 번 잰다(숫자만 출력).
"""
from __future__ import annotations

import os
import random
import sys
import time
import tracemalloc
import warnings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import chat_panel as cp  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - pytest 가 stdout 을 바꿔 둔 경우
    pass

TARGET_FILTER_MS = 100.0      # 02 9장 '채팅 검색 100ms 이하'
TARGET_CLEAR_MS = 100.0       # '필터 해제 100ms 이하'
TARGET_TIME_AVG_MS = 16.0     # '재생 위치 → 하이라이트 평균 16ms'
TARGET_TIME_WORST_MS = 100.0
TARGET_LOAD_MS = 3000.0       # '프로젝트 열기 3초 이하' (채팅 표 몫만 잰다)

_WORDS = ["ㅋㅋㅋ", "ㅎㅇ", "안녕하세요", "오늘", "게임", "맛집", "여행", "저도요", "그거", "어디예요", "진짜",
          "대박", "ㄷㄷ", "노래", "한판", "더", "가자", "ㅠㅠ", "Hello", "GG", "{:d_1:}", "점심", "뭐", "드셨어요"]


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


MASKS = tuple("시" + "*" * k for k in range(1, 8))      # 가린 닉네임 합성값 (S-21: '시' + 별 1~7개)


def _synth(n: int, seed: int = 7) -> list[dict]:
    """8시간에 고르게 퍼진 n 건. 시청자 n/8 명, 후원·구독 약 1%. (t_ms, chat_id) 순."""
    rnd = random.Random(seed)
    span = 8 * 3600 * 1000
    viewers = [f"vw_{i:012d}" for i in range(max(1, n // 8))]
    ts = sorted(rnd.randrange(span) for _ in range(n))
    out = []
    for i, t in enumerate(ts):
        k = rnd.random()
        kind = "donation" if k < 0.008 else ("subscription" if k < 0.01 else "text")
        out.append({"chat_id": f"{i:08d}", "t_ms": t, "server_time_ms": 1_700_000_000_000 + t,
                    "type_code": 10 if kind == "donation" else (11 if kind == "subscription" else 1),
                    "kind": kind, "status": "NORMAL", "viewer_id": rnd.choice(viewers), "is_anonymous": False,
                    "nickname_masked": MASKS[rnd.randint(0, 6)], "role": "viewer",
                    "text": " ".join(rnd.choice(_WORDS) for _ in range(rnd.randint(1, 8))),
                    "raw_ref": {"fetch_id": "F1", "page": i // 200, "index": i % 200}})
    return out


def _ms(f) -> float:
    t0 = time.perf_counter()
    f()
    return (time.perf_counter() - t0) * 1000.0


def _warn_if(name: str, value: float, target: float) -> None:
    if value > target:
        warnings.warn(f"채팅 표 벤치 목표 넘음: {name} {value:.1f}ms > {target:.0f}ms", stacklevel=2)


@pytest.mark.parametrize("n", [200_000, 360_000])
def test_bench_chat_panel(qapp, n):
    rows = _synth(n)
    p = cp.ChatPanel()
    p.resize(520, 900)
    p.show()
    QApplication.processEvents()

    load_ms = _ms(lambda: (p.set_messages(rows), QApplication.processEvents()))
    shuffled = rows[:]
    random.Random(3).shuffle(shuffled)
    build_unsorted_ms = _ms(lambda: cp.ChatData(shuffled))
    assert p.row_count() == n

    p.select_chat(f"{n // 2:08d}")
    QApplication.processEvents()
    res, hits = {}, {}
    for q in ("맛집", "ㅋ", "드셨어요 점", "시청자 12", "없는말xyz"):
        res[q] = _ms(lambda: (p.set_filter(q), QApplication.processEvents()))
        hits[q] = p.row_count()
    clear_ms = _ms(lambda: (p.set_filter(""), QApplication.processEvents()))
    assert p.row_count() == n
    assert p.selected_chat_id() == f"{n // 2:08d}"

    # 재생 위치: 1초씩 앞으로(재생) 600번 + 무작위 탐색 200번
    span = 8 * 3600 * 1000
    rnd = random.Random(11)
    seq = [3_600_000 + i * 1000 for i in range(600)] + [rnd.randrange(span) for _ in range(200)]
    call, with_paint = [], []
    for i, t in enumerate(seq):
        call.append(_ms(lambda: p.set_time_ms(t)))
        if i % 4 == 0:
            with_paint.append(_ms(lambda: (p.set_time_ms(t + 500), QApplication.processEvents())))
    avg, worst = sum(call) / len(call), max(call)
    avg_p, worst_p = sum(with_paint) / len(with_paint), max(with_paint)
    p.set_filter("맛집")
    QApplication.processEvents()
    call_f = [_ms(lambda: p.set_time_ms(t)) for t in seq]
    avg_f = sum(call_f) / len(call_f)

    tracemalloc.start()
    keep = cp.ChatData(rows)
    mem_mb = tracemalloc.get_traced_memory()[0] / 1e6
    tracemalloc.stop()
    del keep
    print(f"\n[채팅 표 벤치 n={n:,}] 싣기(이미 정렬) {load_ms:.0f}ms · 정렬 안 된 입력 ChatData {build_unsorted_ms:.0f}ms"
          f" · ChatData 메모리 {mem_mb:.0f}MB (입력 dict 제외)")
    print("  필터 " + " · ".join(f"'{q}' {v:.1f}ms ({hits[q]:,}행)" for q, v in res.items()))
    print(f"  필터 해제(선택·스크롤 복원) {clear_ms:.1f}ms")
    print(f"  set_time_ms 평균 {avg:.3f}ms 최악 {worst:.2f}ms · 그리기 포함 평균 {avg_p:.2f}ms 최악 {worst_p:.1f}ms"
          f" · 필터 중 평균 {avg_f:.3f}ms")

    for q, v in res.items():
        _warn_if(f"n={n} 필터 '{q}'", v, TARGET_FILTER_MS)
    _warn_if(f"n={n} 필터 해제", clear_ms, TARGET_CLEAR_MS)
    _warn_if(f"n={n} set_time_ms 평균", avg, TARGET_TIME_AVG_MS)
    _warn_if(f"n={n} set_time_ms 최악", worst, TARGET_TIME_WORST_MS)
    _warn_if(f"n={n} 그리기 포함 평균", avg_p, TARGET_TIME_AVG_MS)
    _warn_if(f"n={n} 싣기", load_ms, TARGET_LOAD_MS)
    if n <= 200_000 and mem_mb > 100:
        warnings.warn(f"채팅 표 벤치 목표 넘음: n={n} 메모리 {mem_mb:.0f}MB > 100MB", stacklevel=2)
    p.close()
    p.deleteLater()
    QApplication.processEvents()


def test_bench_real_project_load(qapp):
    path = os.environ.get("CHATCOACH_BENCH_REAL")
    if not path or not os.path.isfile(path):
        pytest.skip("CHATCOACH_BENCH_REAL 이 없음 (실제 프로젝트 로드 시간은 손으로만 잰다)")
    from app import jsonl
    t0 = time.perf_counter()
    rr = jsonl.read(path)
    read_ms = (time.perf_counter() - t0) * 1000
    p = cp.ChatPanel()
    p.resize(520, 900)
    p.show()
    load_ms = _ms(lambda: (p.set_messages(rr.records), QApplication.processEvents()))
    q_ms = _ms(lambda: (p.set_filter("ㅋ"), QApplication.processEvents()))
    print(f"\n[실제 프로젝트] {len(rr.records):,}건 · jsonl 읽기 {read_ms:.0f}ms · 채팅 표 싣기 {load_ms:.0f}ms"
          f" · 필터 {q_ms:.1f}ms")      # 숫자만 (본문·닉네임은 찍지 않는다)
    p.close()
