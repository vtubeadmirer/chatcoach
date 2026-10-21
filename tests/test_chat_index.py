"""채팅 색인 캐시 (app/chat_index.py) 와 재현 검증 워커 (app/workers.py JobWorker 계열). 합성 데이터만.

채팅 본문·닉네임은 지어낸 문장이다. 실제 프로젝트 폴더는 쓰지 않는다(tmp_path).
워커 테스트는 맡은 파일 범위 때문에 여기 같이 둔다(14 6-1 의 1번).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import chat_index as ci  # noqa: E402

FAKE_TEXTS = ["합성 문장 하나", "지어낸 인사", "가짜 질문입니다?", "ㅋㅋ 합성", "테스트 문장"]


def cid(i: int) -> str:
    return "c" + hashlib.sha256(f"synthetic-{i}".encode()).hexdigest()[:20]


def rec(i: int, t: int | None, kind: str = "chat", code: int = 1) -> dict:
    return {"chat_id": cid(i), "t_ms": t, "server_time_ms": None, "type_code": code, "kind": kind, "status": "",
            "viewer_id": f"vw_{'abcdefghijkl'}", "is_anonymous": False, "nickname_masked": "시**", "role": "common_user",
            "text": FAKE_TEXTS[i % len(FAKE_TEXTS)], "raw_ref": {"fetch_id": "F20261021-abcd", "page": 0, "index": i}}


def write_msgs(path, records, *, tail: bytes = b"") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n")
        f.write(tail)


@pytest.fixture()
def msgs(tmp_path):
    p = str(tmp_path / "proj" / "chat" / "messages.jsonl")
    records = [rec(0, 5_000), rec(1, 1_000, "donation", 10), rec(2, None), rec(3, 1_000, "chat"),
               rec(4, 9_000, "system", 30)]
    write_msgs(p, records)
    return p


# ---------------------------------------------------------------------------- 만들기·조회
def test_build_and_lookup(msgs):
    idx = ci.build_index(msgs)
    assert len(idx) == 5
    assert idx.get(cid(1)) == (1_000, "donation", 10)
    assert idx.get(cid(2)) == (None, "chat", 1)
    assert idx.get("c" + "0" * 20) is None and cid(9) not in idx
    assert idx.t_of(cid(4)) == 9_000 and idx.kind_of(cid(4)) == "system" and idx.t_of(cid(2)) is None
    assert idx.time_ordered_ids() == [cid(1), cid(3), cid(0), cid(4)]     # 같은 시각은 파일 순, 시각 없음 뺌
    assert idx.chat_times() == {cid(1): 1_000, cid(3): 1_000, cid(0): 5_000, cid(4): 9_000}
    assert idx.chat_times([cid(0), cid(2), "c-없음"]) == {cid(0): 5_000}
    assert idx.range_ids(1_000, 5_000) == [cid(1), cid(3)]                 # [시작, 끝)
    assert idx.range_ids(1_001, 9_001) == [cid(0), cid(4)]
    assert idx.count_in(0, 10_000) == 4 and idx.count_in(9_001, 20_000) == 0
    with open(msgs, "rb") as f:
        assert idx.messages_sha256 == hashlib.sha256(f.read()).hexdigest()


def test_corrupt_lines_duplicates_and_bom(tmp_path):
    p = str(tmp_path / "m.jsonl")
    body = (b"\xef\xbb\xbf" + json.dumps(rec(0, 10)).encode() + b"\n" + b"{broken\n" +
            json.dumps(rec(0, 20)).encode() + b"\n" +                       # 같은 chat_id → 첫 줄만
            json.dumps({"t_ms": 5}).encode() + b"\n" +                       # chat_id 없음
            b"[1,2]\n" + json.dumps(rec(1, 30, kind=None)).encode() + b"\n" +   # kind 없음 → other
            b'{"chat_id": "c1')                                              # 잘린 끝
    with open(p, "wb") as f:
        f.write(body)
    idx = ci.build_index(p)
    assert idx.chat_ids == [cid(0), cid(1)]
    assert idx.t_of(cid(0)) == 10 and idx.kind_of(cid(1)) == "other"
    assert (idx.corrupt_lines, idx.truncated_tail, idx.duplicates, idx.skipped) == (2, True, 1, 1)
    back = ci.from_bytes(ci.to_bytes(idx))
    assert (back.corrupt_lines, back.truncated_tail, back.duplicates, back.skipped) == (2, True, 1, 1)


def test_bulk_parse_cannot_merge_broken_lines():
    """빠른 길(한 번에 파싱)이 깨진 줄들을 이어 붙여 '맞는 것처럼' 읽지 않는다 — 줄마다 파싱과 같은 결과."""
    good = json.dumps(rec(0, 10)).encode()
    crafted = [b'{"chat_id":"' + cid(1).encode() + b'","t_ms":1,"x":"a}',     # 문자열이 안 닫힘
               b'{y","kind":"chat"}',                                       # 앞 줄의 문자열을 닫음
               b'{"chat_id":"' + cid(2).encode() + b'"}],[{"chat_id":"' + cid(3).encode() + b'"}',  # 한 줄에 둘
               good]
    recs, corrupt, truncated = ci._iter_lines(b"\n".join(crafted) + b"\n")
    assert [r["chat_id"] for r in recs] == [cid(0)] and corrupt == 3 and not truncated
    assert ci._bulk_parse([b'{"a":1},{"b":2}']) is None                     # 한 줄에 object 둘
    assert ci._bulk_parse([b'{"text":"x],[y"}']) is None                    # 의심 모양 → 줄마다 길로
    recs, corrupt, _ = ci._iter_lines(b'{"chat_id":"' + cid(5).encode() + b'","text":"x],[y"}\n')
    assert corrupt == 0 and recs[0]["text"] == "x],[y"                      # 줄마다 길에서는 정상으로 읽힘
    assert ci._bulk_parse([good, good]) == [json.loads(good)] * 2


def test_bool_and_out_of_range_values_are_none():
    idx = ci.build_from_records([{"chat_id": cid(0), "t_ms": True, "type_code": 1 << 40, "kind": "chat"},
                                 {"chat_id": cid(1), "t_ms": 3.5, "type_code": None, "kind": ""}])
    assert idx.get(cid(0)) == (None, "chat", None)
    assert idx.get(cid(1)) == (None, "other", None)
    assert idx.time_ordered_ids() == []


# ---------------------------------------------------------------------------- 파일 형식
def test_roundtrip_bytes(msgs):
    idx = ci.build_index(msgs)
    back = ci.from_bytes(ci.to_bytes(idx))
    assert back.chat_ids == idx.chat_ids and list(back.t_ms) == list(idx.t_ms)
    assert list(back.type_codes) == list(idx.type_codes) and back.kinds == idx.kinds
    assert list(back.by_time) == list(idx.by_time) and list(back.sorted_t) == list(idx.sorted_t)
    assert back.stamp == idx.stamp and back.messages_sha256 == idx.messages_sha256
    assert [back.get(c) for c in idx.chat_ids] == [idx.get(c) for c in idx.chat_ids]


def test_cache_has_no_text_nickname_viewer(msgs, tmp_path):
    """블라인드·개인정보: 캐시에는 chat_id·시각·종류·type_code 와 지문뿐 (본문·가림 닉네임·viewer_id·raw_ref 없음)."""
    data = ci.to_bytes(ci.build_index(msgs))
    for needle in [t.encode("utf-8") for t in FAKE_TEXTS] + ["시**".encode("utf-8"), b"vw_", b"common_user",
                                                             b"F20261021", b"raw_ref", b"nickname", b"text"]:
        assert needle not in data
    head = json.loads(data[12:12 + int.from_bytes(data[8:12], "little")])
    assert set(head) == {"schema", "version", "source", "messages_sha256", "count", "timed", "kinds", "id_bytes",
                         "corrupt_lines", "truncated_tail", "duplicates", "skipped", "body_sha256"}


@pytest.mark.parametrize("damage", ["magic", "body", "truncate", "version", "head"])
def test_damaged_cache_raises(msgs, damage):
    data = bytearray(ci.to_bytes(ci.build_index(msgs)))
    if damage == "magic":
        data[0:1] = b"X"
    elif damage == "body":
        data[-1] ^= 0xFF
    elif damage == "truncate":
        data = data[:-7]
    elif damage == "version":
        data = bytearray(bytes(data).replace(b'"version":1', b'"version":9'))
    else:
        data[12:13] = b"["
    with pytest.raises(ci.ChatIndexError):
        ci.from_bytes(bytes(data))


# ---------------------------------------------------------------------------- 무효화
def test_load_or_build_hit_and_invalidation(msgs, tmp_path):
    cache = str(tmp_path / "cache" / "pid" / ci.FILE_NAME)
    idx, st = ci.load_or_build(msgs, cache)
    assert st == ci.BUILT and os.path.isfile(cache)
    idx2, st = ci.load_or_build(msgs, cache)
    assert st == ci.HIT and idx2.chat_ids == idx.chat_ids
    # 크기가 바뀜(줄 추가)
    with open(msgs, "ab") as f:
        f.write(json.dumps(rec(7, 2_000)).encode() + b"\n")
    idx3, st = ci.load_or_build(msgs, cache)
    assert st == ci.REBUILT and idx3.t_of(cid(7)) == 2_000
    assert ci.load_or_build(msgs, cache)[1] == ci.HIT
    # mtime 만 바뀜
    s = os.stat(msgs)
    os.utime(msgs, ns=(s.st_atime_ns, s.st_mtime_ns + 5_000_000_000))
    assert ci.load_or_build(msgs, cache)[1] == ci.REBUILT


def test_same_size_same_mtime_head_change_is_detected(msgs, tmp_path):
    cache = str(tmp_path / "c.bin")
    ci.load_or_build(msgs, cache)
    s = os.stat(msgs)
    with open(msgs, "r+b") as f:
        data = f.read()
        f.seek(0)
        f.write(data.replace(b'"t_ms": 5000', b'"t_ms": 6000', 1))     # 같은 길이
    os.utime(msgs, ns=(s.st_atime_ns, s.st_mtime_ns))
    idx, st = ci.load_or_build(msgs, cache)
    assert st == ci.REBUILT and idx.t_of(cid(0)) == 6_000


def test_verify_full_catches_tail_change_beyond_head(tmp_path):
    """앞 64KiB 밖·같은 크기·같은 mtime 변화는 지문으로 못 잡는다 — verify_full 이 잡는다."""
    p = str(tmp_path / "m.jsonl")
    records = [rec(i, i * 10) for i in range(800)]        # 64KiB 넘게
    write_msgs(p, records)
    assert os.path.getsize(p) > ci.HEAD_BYTES * 2
    cache = str(tmp_path / "c.bin")
    ci.load_or_build(p, cache)
    s = os.stat(p)
    with open(p, "r+b") as f:
        data = f.read()
        f.seek(0)
        f.write(data.replace(b'"t_ms": 7990', b'"t_ms": 7999', 1))
    os.utime(p, ns=(s.st_atime_ns, s.st_mtime_ns))
    assert ci.load_or_build(p, cache)[1] == ci.HIT                     # 빠른 지문: 같다고 봄
    idx, st = ci.load_or_build(p, cache, verify_full=True)
    assert st == ci.REBUILT and idx.t_of(cid(799)) == 7_999


def test_corrupt_cache_file_is_rebuilt(msgs, tmp_path):
    cache = str(tmp_path / "c.bin")
    ci.load_or_build(msgs, cache)
    with open(cache, "r+b") as f:
        f.seek(-3, os.SEEK_END)
        f.write(b"\x00\x00\x00")
    idx, st = ci.load_or_build(msgs, cache)
    assert st == ci.REBUILT and len(idx) == 5
    assert ci.load_or_build(msgs, cache)[1] == ci.HIT


def test_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ci.load_or_build(str(tmp_path / "없음.jsonl"), str(tmp_path / "c.bin"))


def test_unwritable_cache_still_returns_index(msgs, tmp_path):
    blocker = tmp_path / "file_not_dir"
    blocker.write_bytes(b"x")
    idx, st = ci.load_or_build(msgs, str(blocker / "sub" / ci.FILE_NAME))
    assert st == ci.BUILT and len(idx) == 5


# ---------------------------------------------------------------------------- 위치 (03 2-2)
def test_cache_path_under_app_data_cache(tmp_path):
    from app import paths
    p = ci.cache_path_for("1b4e28ba-2fa1-11d2-883f-0016d3cca427")
    assert p == os.path.join(paths.data_root(), "cache", "1b4e28ba-2fa1-11d2-883f-0016d3cca427", "chat_index.bin")
    for bad in ("", "..", "a/b", "a\\b", "c:x", "x" * 81):
        with pytest.raises(ValueError):
            ci.cache_dir_for(bad)


def test_for_project_and_drop(msgs, tmp_path):
    class _P:
        def __init__(self, pid):
            self.project_id = pid
            self.paths = type("PP", (), {"messages_jsonl": msgs})()
    data_root = str(tmp_path / "data")
    idx, st = ci.for_project(_P("pid-1"), data_root=data_root)
    assert st == ci.BUILT and os.path.isfile(ci.cache_path_for("pid-1", data_root))
    assert not ci.cache_path_for("pid-1", data_root).startswith(os.path.dirname(os.path.dirname(msgs)))
    assert ci.for_project(_P("pid-1"), data_root=data_root)[1] == ci.HIT
    assert ci.drop_cache("pid-1", data_root) and not ci.drop_cache("pid-1", data_root)
    assert ci.for_project(_P(None), data_root=data_root)[1] == ci.BUILT


def test_200k_build_and_reload_under_target(tmp_path):
    """02 9장 '프로젝트 열기 3초 이하(색인 캐시가 있으면 1초)'. 넘으면 경고만(기계마다 다름)."""
    import warnings
    p = str(tmp_path / "m.jsonl")
    records = [rec(i, (i * 7_919) % (8 * 3_600_000), "chat" if i % 9 else "donation") for i in range(200_000)]
    write_msgs(p, records)
    del records
    cache = str(tmp_path / "c.bin")
    t0 = time.perf_counter()
    idx, st = ci.load_or_build(p, cache)
    build_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    idx2, st2 = ci.load_or_build(p, cache)
    n = len(idx2.chat_times([idx2.chat_ids[0]]))        # 첫 조회(위치 표 만들기)까지
    load_s = time.perf_counter() - t1
    assert (st, st2, len(idx2), n) == (ci.BUILT, ci.HIT, 200_000, 1)
    print(f"20만 건: 처음 만들기 {build_s:.2f}s, 다시 읽기 {load_s:.3f}s, 캐시 {os.path.getsize(cache) / 1e6:.1f}MB")
    if build_s > 3.0:
        warnings.warn(f"색인 처음 만들기 {build_s:.2f}s > 3s")
    if load_s > 1.0:
        warnings.warn(f"색인 다시 읽기 {load_s:.2f}s > 1s")


# ---------------------------------------------------------------------------- 워커 (14 6-1 의 1번)
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Rec:
    """시그널 기록."""

    def __init__(self, w):
        self.ev = []
        w.progress.connect(lambda a, b, k: self.ev.append(("progress", a, b, k)))
        w.item_done.connect(lambda k, r: self.ev.append(("item", k, r)))
        w.done.connect(lambda r: self.ev.append(("done", r)))
        w.failed.connect(lambda k, m: self.ev.append(("failed", k, m)))
        w.cancelled.connect(lambda r: self.ev.append(("cancelled", r)))

    def ends(self):
        return [e for e in self.ev if e[0] in ("done", "failed", "cancelled")]


def _run_thread(qapp, w, timeout_s=10.0):
    from PySide6.QtCore import QCoreApplication
    w.start()
    t0 = time.perf_counter()
    while not w.isFinished() or w.isRunning():
        QCoreApplication.processEvents()
        if time.perf_counter() - t0 > timeout_s:
            raise AssertionError("워커가 끝나지 않음")
        w.wait(10)
    QCoreApplication.processEvents()


def test_job_worker_runs_in_thread_and_signals(qapp):
    import threading
    from app.workers import JobWorker
    main = threading.get_ident()
    seen = []

    def job(v):
        def fn(ctx):
            seen.append(threading.get_ident())
            ctx.progress(0.5, "절반")
            return v * 2
        return fn
    w = JobWorker([("a", job(1)), ("b", job(2))])
    r = _Rec(w)
    sub = []
    w.subprogress.connect(lambda f, k, t: sub.append((round(f, 3), k)))
    _run_thread(qapp, w)
    assert all(t != main for t in seen)                   # UI 스레드 밖
    assert r.ends() == [("done", {"a": 2, "b": 4})] and w.outcome == "done"
    assert [e for e in r.ev if e[0] == "progress"] == [("progress", 0, 2, "a"), ("progress", 1, 2, "b"),
                                                       ("progress", 2, 2, "")]
    assert sub == [(0.25, "a"), (0.75, "b")]
    with pytest.raises(ValueError):
        JobWorker([("x", job(1)), ("x", job(2))])


def test_job_worker_failed_stops(qapp):
    from app.workers import JobWorker
    ran = []

    def bad(ctx):
        raise RuntimeError("합성 오류")
    w = JobWorker([("a", lambda c: ran.append("a") or 1), ("b", bad), ("c", lambda c: ran.append("c"))])
    r = _Rec(w)
    w.run()                                               # 부른 스레드에서 (도구·테스트 길)
    assert ran == ["a"] and r.ends() == [("failed", "b", "RuntimeError: 합성 오류")]
    assert w.outcome == "failed" and w.results == {"a": 1}


def test_job_worker_cancel_between_and_inside(qapp):
    import threading
    from app.workers import JobWorker
    gate = threading.Event()

    def slow(ctx):
        gate.wait(5)
        for _ in range(1000):
            ctx.check()                                   # 취소되면 JobCancelled
            time.sleep(0.001)
        return "끝"
    w = JobWorker([("a", lambda c: 1), ("b", slow), ("c", lambda c: 3)])
    r = _Rec(w)
    w.item_done.connect(lambda k, _r: (w.cancel(), gate.set()) if k == "a" else None,
                        type=__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.DirectConnection)
    _run_thread(qapp, w)
    assert r.ends() == [("cancelled", {"a": 1})] and w.outcome == "cancelled"
    w2 = JobWorker([("a", lambda c: 1)])
    r2 = _Rec(w2)
    w2.cancel()
    w2.run()
    assert r2.ends() == [("cancelled", {})]


def test_sample_verify_worker_rows(qapp):
    from app.core.sampling import Verification
    from app.workers import SampleVerifyWorker

    def verify(m):
        if m["sample_set_id"] == "S-b":
            raise ValueError("합성")
        return Verification(m["sample_set_id"] == "S-a", () if m["sample_set_id"] == "S-a" else ("x", "y"))
    w = SampleVerifyWorker([{"sample_set_id": "S-c"}, {"sample_set_id": "S-a"}, {"sample_set_id": "S-b"}], verify)
    got = []
    w.verified.connect(got.append)
    _run_thread(qapp, w)
    assert got == [[{"sample_set_id": "S-a", "ok": True, "problems": 0},
                    {"sample_set_id": "S-b", "ok": False, "problems": 1},
                    {"sample_set_id": "S-c", "ok": False, "problems": 2}]]


def test_plan_verify_worker(qapp, monkeypatch, tmp_path):
    from app import relabel
    from app.core.relabel_plan import Verification
    from app.workers import PlanVerifyWorker
    calls = []

    def fake(root, data, pid, *, creator_kind):
        calls.append((root, pid, creator_kind, data.get("sample_seed")))
        if pid == "RP-bad":
            raise OSError("합성")
        return Verification(pid == "RP-ok", () if pid == "RP-ok" else ("다름",))
    monkeypatch.setattr(relabel, "verify_plan_on_disk", fake)
    w = PlanVerifyWorker(str(tmp_path), {"sample_seed": "s"}, ["RP-ok", "RP-no", "RP-bad", "RP-ok"],
                         creator_kind="adjudication")
    got = []
    w.verified.connect(got.append)
    _run_thread(qapp, w)
    assert [c[1] for c in calls] == ["RP-ok", "RP-no", "RP-bad"]           # 겹친 ID 는 한 번
    assert all(c[2] == "adjudication" and c[3] == "s" for c in calls)
    assert got == [{"RP-ok": {"plan_id": "RP-ok", "ok": True, "problems": ()},
                    "RP-no": {"plan_id": "RP-no", "ok": False, "problems": ("다름",)},
                    "RP-bad": {"plan_id": "RP-bad", "ok": False, "problems": ("검증 중 오류: OSError",)}}]
