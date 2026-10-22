"""GUI 스모크 바깥 감시 시험 (tools/smoke_watch.py · tools/gui_smoke.py --faulthandler-file, 22 5-2 의 1번).

진짜 gui_smoke·프로젝트 대신 **가짜 자식**(이 시험이 tmp_path 에 쓰는 작은 파이썬 스크립트)을 띄운다. 채팅·닉네임 같은
실제 자료는 쓰지 않는다. 스레드 상태(Suspended) 시험은 이 시험이 띄운 자식만 멈췄다 푼다(Windows).
"""
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import gui_smoke  # noqa: E402
import smoke_watch as sw  # noqa: E402

WIN = os.name == "nt"


def child(tmp_path, body: str, name="fake_child.py") -> str:
    """가짜 자식 스크립트. 감시가 붙이는 --faulthandler-file 경로를 FH 로 받는다. gui_smoke 의 arm_faulthandler 를 쓴다."""
    head = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {TOOLS!r})
        args = sys.argv[1:]
        FH = args[args.index("--faulthandler-file") + 1] if "--faulthandler-file" in args else None
    """)
    p = tmp_path / name
    p.write_text(head + textwrap.dedent(body), encoding="utf-8")
    return str(p)


def run_watch(tmp_path, script, *, extra=(), **kw):
    out = str(tmp_path / "watch")
    cmd, stack = sw.build_cmd(list(extra), os.path.join(out, "stack.txt"), script=script)
    lines = []
    kw.setdefault("stall_s", 1.0)
    kw.setdefault("sample_s", 0.2)
    kw.setdefault("max_s", 60.0)
    res = sw.watch(cmd, out_dir=out, stack_path=stack, echo=lines.append, **kw)
    return res, lines


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------- 명령 · 요약 · 기록 정리
def test_build_cmd_adds_faulthandler_file_once(tmp_path):
    stack = str(tmp_path / "s.txt")
    cmd, got = sw.build_cmd(["--project", "P", "--offscreen"], stack, python="py.exe")
    assert cmd[:3] == ["py.exe", "-u", sw.GUI_SMOKE] and cmd[-2:] == ["--faulthandler-file", stack] and got == stack
    mine = str(tmp_path / "mine.txt")
    cmd, got = sw.build_cmd(["--faulthandler-file", mine, "--offscreen"], stack)
    assert cmd.count("--faulthandler-file") == 1 and got == os.path.abspath(mine)
    cmd, got = sw.build_cmd([f"--faulthandler-file={mine}"], stack)
    assert "--faulthandler-file" not in cmd and got == os.path.abspath(mine)


def test_names_match_gui_smoke():
    assert sw.REQUEST_SUFFIX == gui_smoke.FH_REQUEST_SUFFIX
    ns = gui_smoke.parse_args(["--project", "x", "--faulthandler-file", "y.txt"])
    assert ns.faulthandler_file == "y.txt"
    assert gui_smoke.parse_args(["--project", "x"]).faulthandler_file is None


def test_parse_args_splits_child_args():
    ns = sw.parse_args(["--stall", "5", "--", "--project", "P", "--analysis-flow"])
    assert ns.stall == 5.0 and ns.child == ["--project", "P", "--analysis-flow"] and ns.sample == sw.DEFAULT_SAMPLE_S


def test_count_dumps(tmp_path):
    p = tmp_path / "stack.txt"
    p.write_bytes("\n".join([
        "== 시작 2026-10-23T10:00:00 pid=1",
        "== 요청 덤프 1 2026-10-23T10:00:05 pid=1", "Thread 0x1 (most recent call first):", "== 요청 덤프 1 끝 x pid=1",
        "== 요청 덤프 2 2026-10-23T10:00:06 pid=1", "== 요청 덤프 2 끝 x pid=1",
        "Timeout (0:01:00)!", "Thread 0x2 (most recent call first):",
        "Windows fatal exception: access violation", ""]).encode("utf-8"))
    assert sw.count_dumps(str(p)) == {"written": 2, "auto": 1, "fatal": 1}
    assert sw.count_dumps(str(tmp_path / "없음.txt")) == {"written": 0, "auto": 0, "fatal": 0}


def test_summary_line():
    r = sw.WatchResult(rc=0, elapsed_s=12.34, out_dir="D", stack_path="S", samples=6, max_threads=15, max_suspended=15,
                       probe_ok=True, dumps={"requested": 1, "written": 0, "auto": 0, "fatal": 0})
    r.stalls.append(sw.Stall(start_s=2.0, end_s=9.5, probe=sw.Probe(2, 15, 15, 1.0)))
    s = r.summary()
    assert s.startswith("[watch] 끝: rc=0 · 12.3초") and "멈춤 1번(최장 7.5초, 그때 Suspended 15/15)" in s
    assert "Suspended 최대 15/15" in s and "덤프 요청 1·받음 0·자동 0·치명 0" in s and "\n" not in s
    r2 = sw.WatchResult(rc=124, elapsed_s=3.0, out_dir="D", stack_path="S", timed_out=True)
    assert "rc=124 (시간 초과로 끝냄)" in r2.summary() and "멈춤 0" in r2.summary() and "스레드 상태 못 봄" in r2.summary()


def test_prune_runs_keeps_newest_and_ignores_others(tmp_path):
    root = tmp_path / "watch"
    names = [f"202610{d:02d}-120000-{d}" for d in range(1, 8)]
    for n in names:
        (root / n).mkdir(parents=True)
        (root / n / "watch.log").write_text("x", encoding="utf-8")
    (root / "남의폴더").mkdir()
    (root / "20261001-120000-1.txt").write_text("x", encoding="utf-8")
    assert sw.prune_runs(str(root), keep=3) == 4
    assert sorted(os.listdir(root)) == sorted(names[-3:] + ["남의폴더", "20261001-120000-1.txt"])


# ---------------------------------------------------------------------------- 가짜 자식으로 감시
def test_quick_child_rc_and_log(tmp_path):
    script = child(tmp_path, """
        print("PASS step_a 1 ms", flush=True)
        print("PASS step_b 2 ms", flush=True)
        sys.exit(3)
    """)
    res, lines = run_watch(tmp_path, script)
    assert res.rc == 3 and not res.timed_out and res.stalls == []
    assert lines == ["PASS step_a 1 ms", "PASS step_b 2 ms"]
    log = read(os.path.join(res.out_dir, "watch.log"))
    assert " out  PASS step_a" in log and "--faulthandler-file" in log and " end  [watch] 끝: rc=3" in log


def test_stall_requests_stack_dump(tmp_path):
    """출력이 멈추면 요청 파일을 만들고, 자식(gui_smoke.arm_faulthandler)은 모든 스레드 스택을 스택 파일에 쓴다."""
    script = child(tmp_path, """
        import gui_smoke
        disarm = gui_smoke.arm_faulthandler(FH, poll_s=0.1, hang_s=0)
        print("step-a", flush=True)
        def fake_slow_step_marker():
            t = time.monotonic()
            while time.monotonic() - t < 3.5:
                time.sleep(0.05)
        fake_slow_step_marker()
        print("step-b", flush=True)
        disarm()
    """)
    res, lines = run_watch(tmp_path, script)
    assert res.rc == 0 and len(res.stalls) == 1
    st = res.stalls[0]
    assert st.end_s is not None and 1.0 <= st.length(res.elapsed_s) < 6.0 and st.requests >= 1
    assert res.dumps["requested"] == st.requests and res.dumps["written"] >= 1 and res.dumps["fatal"] == 0
    text = read(res.stack_path)
    assert "fake_slow_step_marker" in text and "== 요청 덤프 1 " in text
    assert any(x.startswith("[watch] 멈춤: 출력이") for x in lines) and lines[-1] == "step-b"
    assert not os.path.exists(res.stack_path + sw.REQUEST_SUFFIX)
    if WIN:
        assert res.probe_ok and res.samples >= 3 and st.probe is not None and st.probe.threads >= 1
        assert st.probe.suspended == 0


def test_requests_capped_per_stall(tmp_path):
    script = child(tmp_path, """
        import gui_smoke
        disarm = gui_smoke.arm_faulthandler(FH, poll_s=0.05, hang_s=0)
        print("a", flush=True)
        time.sleep(4.0)
        disarm()
    """)
    res, _ = run_watch(tmp_path, script, stall_s=0.5)
    assert len(res.stalls) == 1 and res.stalls[0].requests == sw.MAX_REQUESTS_PER_STALL
    assert res.dumps["requested"] == sw.MAX_REQUESTS_PER_STALL


def test_gil_hang_auto_dump(tmp_path):
    """폴링 스레드가 GIL 을 못 얻으면(주 스레드가 쥐고 놓지 않음) faulthandler 의 C 감시가 'Timeout' 덤프를 쓴다."""
    script = child(tmp_path, """
        import gui_smoke
        disarm = gui_smoke.arm_faulthandler(FH, poll_s=0.05, hang_s=1.0, rearm_s=0.1)
        print("a", flush=True)
        time.sleep(0.5)
        sys.setswitchinterval(30.0)          # 다른 파이썬 스레드에 GIL 을 넘기지 않는다
        def fake_gil_hog_marker():
            t = time.perf_counter()
            while time.perf_counter() - t < 2.5:
                pass
        fake_gil_hog_marker()
        sys.setswitchinterval(0.005)
        print("b", flush=True)
        disarm()
    """)
    res, _ = run_watch(tmp_path, script, stall_s=30.0)
    assert res.rc == 0 and res.dumps["auto"] >= 1
    assert "fake_gil_hog_marker" in read(res.stack_path)


def test_max_time_kills_child_tree(tmp_path):
    script = child(tmp_path, """
        print("a", flush=True)
        time.sleep(60)
    """)
    t0 = time.monotonic()
    res, lines = run_watch(tmp_path, script, stall_s=30.0, max_s=1.0)
    assert res.timed_out and res.rc == sw.TIMEOUT_RC and time.monotonic() - t0 < 20
    assert any("시간 초과" in x for x in lines) and "rc=124 (시간 초과로 끝냄)" in res.summary()


def test_fake_probe_all_suspended_is_reported(tmp_path):
    script = child(tmp_path, """
        print("a", flush=True)
        time.sleep(1.8)
        print("b", flush=True)
    """)
    calls = []

    def probe(pid):
        calls.append(pid)
        return sw.Probe(pids=2, threads=15, suspended=15, cpu_s=1.0)

    res, lines = run_watch(tmp_path, script, probe=probe)
    assert res.rc == 0 and calls and len(set(calls)) == 1
    assert res.stalls and res.stalls[0].probe.all_suspended and res.max_suspended == 15
    assert any("모두 Suspended" in x for x in lines)
    assert "그때 Suspended 15/15" in res.summary()
    assert "ALL-SUSPENDED" in read(os.path.join(res.out_dir, "watch.log"))


def test_launch_failure_rc2(tmp_path):
    res = sw.watch([str(tmp_path / "없는파이썬.exe"), "x"], out_dir=str(tmp_path / "w"),
                   stack_path=str(tmp_path / "w" / "stack.txt"), echo=lambda s: None)
    assert res.rc == 2


def test_main_cli_with_fake_script(tmp_path, capsys):
    script = child(tmp_path, """
        print("PASS only 1 ms", flush=True)
        sys.exit(0 if FH else 9)
    """)
    out = tmp_path / "cli"
    rc = sw.main(["--stall", "5", "--sample", "0.5", "--out-dir", str(out), "--script", script, "--", "--offscreen"])
    text = capsys.readouterr().out
    assert rc == 0 and "PASS only 1 ms" in text and text.strip().splitlines()[-1].startswith("[watch] 끝: rc=0")
    assert (out / "watch.log").is_file()
    assert sw.main(["--stall", "0"]) == 2


# ---------------------------------------------------------------------------- 진짜 스레드 상태 (Windows)
@pytest.mark.skipif(not WIN, reason="Windows 스레드 상태")
def test_struct_sizes_match_x64():
    import ctypes
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(sw._ProcInfo) == 256 and ctypes.sizeof(sw._ThreadInfo) == 80


@pytest.mark.skipif(not WIN, reason="Windows 스레드 상태")
def test_probe_tree_sees_suspend_and_resume():
    psutil = pytest.importorskip("psutil")
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        deadline = time.monotonic() + 10
        pr = None
        # venv 런처는 진짜 파이썬을 멈춘 채 만들고 곧 푼다 — 자리 잡을 때까지 기다린다
        while time.monotonic() < deadline:
            time.sleep(0.2)
            procs = [psutil.Process(p.pid)] + psutil.Process(p.pid).children(recursive=True)
            pr = sw.probe_tree(p.pid)
            if pr is not None and pr.suspended == 0 and pr.pids == len(procs):
                break
        assert pr is not None and pr.threads >= 1 and pr.suspended == 0 and not pr.all_suspended
        assert pr.pids == len(procs)                                     # venv 런처 아래 진짜 파이썬까지
        assert pr.threads == sum(x.num_threads() for x in procs)
        for x in procs:
            x.suspend()
        time.sleep(0.2)
        s = sw.probe_tree(p.pid)
        assert s.all_suspended and s.suspended == s.threads
        for x in procs:
            x.resume()
        time.sleep(0.2)
        assert sw.probe_tree(p.pid).suspended == 0
    finally:
        sw.kill_tree(p)
        p.wait(timeout=10)
    assert p.returncode is not None


@pytest.mark.skipif(not WIN, reason="Windows 스레드 상태")
def test_watch_records_externally_suspended_child(tmp_path):
    """밖에서 통째로 멈춘 자식: 멈춤 기록의 스레드가 모두 Suspended 이고 요청한 덤프는 오지 않는다(19 3-2 의 모습)."""
    psutil = pytest.importorskip("psutil")
    script = child(tmp_path, """
        import gui_smoke
        disarm = gui_smoke.arm_faulthandler(FH, poll_s=0.05, hang_s=0)
        print("a", flush=True)
        time.sleep(4.0)
        print("b", flush=True)
        disarm()
    """)
    seen = threading.Event()
    state = {}

    def echo(line):
        if line == "a":
            seen.set()

    def probe(pid):
        state["pid"] = pid
        return sw.probe_tree(pid)

    def suspender():
        if not seen.wait(20) or "pid" not in state:
            return
        root = psutil.Process(state["pid"])
        procs = [root] + root.children(recursive=True)
        for x in procs:
            x.suspend()
        time.sleep(2.5)
        for x in procs:
            x.resume()

    th = threading.Thread(target=suspender, daemon=True)
    th.start()
    out = str(tmp_path / "watch")
    cmd, stack = sw.build_cmd([], os.path.join(out, "stack.txt"), script=script)
    res = sw.watch(cmd, out_dir=out, stack_path=stack, stall_s=1.0, sample_s=0.2, max_s=60.0, probe=probe, echo=echo)
    th.join(10)
    assert res.rc == 0 and res.stalls
    first = res.stalls[0]
    assert first.probe is not None and first.probe.all_suspended
    assert res.max_suspended == res.max_threads >= 1
    assert "ALL-SUSPENDED" in read(os.path.join(out, "watch.log"))
