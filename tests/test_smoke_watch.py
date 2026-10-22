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


# ---------------------------------------------------------------------------- 멈춤 요약 (--summarize, 23 5-2 의 23)
# 아래 스택·감시 기록은 모두 합성이다(경로·함수 이름은 지어낸 것, 채팅·닉네임 없음).
SP = "C:\\Fake\\venv\\Lib\\site-packages"
STD = "C:\\Fake\\Python312\\Lib"
SMOKE = os.path.join(ROOT, "tools", "gui_smoke.py")
APP = os.path.join(ROOT, "app", "fake_mod.py")


def _fr(path, line, func):
    return f'  File "{path}", line {line} in {func}'


def _stack_text():
    return "\n".join([
        "== 시작 2026-10-23T10:00:00 pid=7",
        "== 요청 덤프 1 2026-10-23T10:00:40 pid=7",
        "Thread 0x00000001 (most recent call first):",
        _fr(SP + "\\mpv.py", 689, "_event_generator"),
        _fr(STD + "\\threading.py", 1012, "run"),
        "",
        "Thread 0x00000002 (most recent call first):",
        _fr(STD + "\\threading.py", 359, "wait"),
        _fr(STD + "\\threading.py", 655, "wait"),
        _fr(SMOKE, 608, "run"),
        _fr(STD + "\\threading.py", 1012, "run"),
        "",
        "Current thread 0x00000003 (most recent call first):",
        _fr(SMOKE, 198, "loop"),
        _fr(STD + "\\threading.py", 1012, "run"),
        "",
        "Thread 0x00000004 (most recent call first):",
        _fr(APP, 41, "inner_wait"),
        _fr(APP, 12, "outer_call"),
        _fr(SMOKE, 646, "wait"),
        _fr(SMOKE, 1095, "step_labeling_flow"),
        _fr(SMOKE, 3014, "run"),
        _fr(SMOKE, 3092, "<module>"),
        "== 요청 덤프 1 끝 2026-10-23T10:00:40 pid=7",
        "Timeout (0:01:00)!",
        "Thread 0x00000004 (most recent call first):",
        _fr(SMOKE, 700, "step_window"),
        "  ...",
        _fr(SMOKE, 3092, "<module>"),
        "",
        "Windows fatal exception: access violation",
        "",
        "Current thread 0x00000004 (most recent call first):",
        _fr(APP, "???", "crashy"),
        _fr(SMOKE, 2990, "step_close"),
        _fr(SMOKE, 3092, "<module>"),
        "",
        "Extension modules: fake (total: 1)",
        "== 끝 2026-10-23T10:02:00 pid=7",
        ""])


def _log_line(t, tag, text):
    return f"[{t:8.2f}] {tag:<4} {text}"


def _watch_log_text(*, susp=0, thr=20, cpu_step=1.0, with_end=True):
    rows = [_log_line(0.0, "cmd", "C:\\Fake\\python.exe -u gui_smoke.py --project C:\\Users\\someone\\p"),
            _log_line(0.01, "pid", "7"),
            _log_line(0.2, "out", "== faulthandler: C:\\Users\\someone\\stack.txt (요청 파일 stack.txt.request)"),
            _log_line(5.0, "out", "PASS window                  12 ms  w=1"),
            _log_line(8.0, "out", "  경고: 합성비밀글"),
            _log_line(9.0, "out", "PASS flow_segments         900 ms  segs=2")]
    t, cpu = 10.0, 5.0
    while t <= 52.0:
        flag = " ALL-SUSPENDED" if thr and susp == thr else ""
        rows.append(_log_line(t, "smp", f"pids=2 thr={thr} susp={susp} cpu={cpu:.2f}s{flag}"))
        if abs(t - 40.0) < 1e-6:
            rows.append(_log_line(40.0, "evt", f"멈춤 출력이 31초 없음 · pids=2 thr={thr} susp={susp} cpu={cpu:.2f}s"))
            rows.append(_log_line(40.0, "req", "스택 요청 (멈춤 1 요청 1)"))
        t += 2.0
        cpu += cpu_step * 2.0
    rows += [_log_line(53.0, "out", "PASS flow_reaction        44000 ms  passes=2"),
             _log_line(53.0, "evt", "멈춤 끝: 44.0초"),
             _log_line(54.0, "out", "FAIL flow_sample_draw        10 ms  x=1")]
    if with_end:
        rows.append(_log_line(60.0, "end", "[watch] 끝: rc=1 · 60.0초 · 멈춤 1번(최장 44.0초, 그때 Suspended 0/20) · "
                              "덤프 요청 1·받음 1·자동 0·치명 0 · 표본 22 · 기록 C:\\Users\\someone\\watch\\x"))
    return "\n".join(rows) + "\n"


def _run_dir(tmp_path, name="20261023-100000-7", **kw):
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "stack.txt").write_bytes(_stack_text().encode("utf-8"))
    (d / "watch.log").write_text(_watch_log_text(**kw), encoding="utf-8")
    return d


def test_display_path_hides_absolute_paths():
    assert sw.display_path(SP + "\\mpv.py") == "site-packages/mpv.py"
    assert sw.display_path(os.path.join(ROOT, ".venv", "Lib", "site-packages", "PySide6", "x.py")) == "site-packages/PySide6/x.py"
    assert sw.display_path(SMOKE) == "tools/gui_smoke.py" and sw.display_path(APP) == "app/fake_mod.py"
    assert sw.display_path(STD + "\\threading.py") == "threading.py"
    assert sw.display_path("<frozen importlib._bootstrap>") == "<frozen importlib._bootstrap>"


def test_parse_stack_request_timeout_fatal():
    starts, dumps = sw.parse_stack(_stack_text())
    assert starts == ["2026-10-23T10:00:00"] and [d.kind for d in dumps] == ["request", "timeout", "fatal"]
    req, to, fat = dumps
    assert req.label == "요청 덤프 1" and req.at == "2026-10-23T10:00:40" and req.complete and len(req.threads) == 4
    main = req.main_thread()
    assert main.ident == "0x00000004" and not main.current
    assert main.step() == sw.Frame("tools/gui_smoke.py", 1095, "step_labeling_flow")
    assert [f.text() for f in main.app_frames()] == ["app/fake_mod.py:41 inner_wait", "app/fake_mod.py:12 outer_call"]
    assert req.threads[1].own_frame() == sw.Frame("tools/gui_smoke.py", 608, "run")      # threading 기다림 틀은 건너뜀
    assert req.threads[2].current and req.threads[2].own_frame().func == "loop"
    assert to.label.startswith("Timeout (") and to.main_thread().truncated and to.at is None
    assert to.main_thread().step().func == "step_window"
    assert fat.label == "Windows fatal exception: access violation" and fat.threads[0].current
    assert fat.main_thread().frames[0] == sw.Frame("app/fake_mod.py", None, "crashy")
    # Stack 꼴(스레드 하나)과 Fatal Python error 이어 붙이기
    _s, d2 = sw.parse_stack("Fatal Python error: Segmentation fault\nWindows fatal exception: code 0xc0000005\n\n"
                            "Stack (most recent call first):\n" + _fr(SMOKE, 3092, "<module>") + "\n")
    assert len(d2) == 1 and "Segmentation fault / Windows fatal" in d2[0].label and d2[0].main_thread() is not None


def test_parse_watch_log_stall_steps_cpu():
    log = sw.parse_watch_log(_watch_log_text())
    assert log.fh_s == 0.2 and log.steps == 4 and log.fails == ["flow_sample_draw"] and log.earlier_runs == 0
    assert log.end.startswith("rc=1 · 60.0초") and "기록" not in log.end and "someone" not in log.end
    (st,) = log.stalls
    assert st.start_s == 9.0 and st.found_s == 40.0 and st.end_s == 53.0 and st.requests == 1
    assert st.before_step == "flow_segments" and st.after_step == ("PASS", "flow_reaction", 44000)
    assert abs(st.cpu_rate() - 1.0) < 1e-6 and "CPU 를 쓰며 돎" in st.verdict()
    assert "CPU 거의 0" in sw.parse_watch_log(_watch_log_text(cpu_step=0.0)).stalls[0].verdict()
    assert "모두 Suspended" in sw.parse_watch_log(_watch_log_text(susp=20, cpu_step=0.0)).stalls[0].verdict()
    mostly = sw.parse_watch_log(_watch_log_text(susp=17, thr=19, cpu_step=0.0))
    assert "대부분이 Suspended" in mostly.stalls[0].verdict() and mostly.any_all_suspended
    # 같은 watch.log 에 실행이 둘이면 마지막 실행만
    two = sw.parse_watch_log(_watch_log_text() + _log_line(0.0, "cmd", "x") + "\n" + _log_line(1.0, "out", "PASS a 1 ms") + "\n")
    assert two.earlier_runs == 1 and two.steps == 1 and two.stalls == []


def test_probe_mostly_suspended():
    assert sw.Probe(2, 19, 17, 1.0).mostly_suspended and not sw.Probe(2, 19, 17, 1.0).all_suspended
    assert not sw.Probe(2, 19, 3, 1.0).mostly_suspended and not sw.Probe(0, 0, 0, 0.0).mostly_suspended


def test_summarize_run_groups_dumps_under_stall(tmp_path):
    d = _run_dir(tmp_path)
    lines = sw.summarize_run(str(d))
    text = "\n".join(lines)
    assert lines[0].startswith("[watch 요약] 20261023-100000-7 · rc=1 · 60.0초")
    assert "멈춤 1: 9.0~53.0초(44.0초) · 앞 단계 flow_segments · 뒤 처음 끝난 단계 flow_reaction(44000 ms, PASS)" in text
    assert "스택 요청 1·받음 1 → CPU 를 쓰며 돎" in text
    i = next(k for k, s in enumerate(lines) if s.startswith("  멈춤 1:"))
    assert lines[i + 1].startswith("    요청 덤프 1 10:00:40 · 멈춤 1 안 · 스레드 4")
    assert ("주 스레드: 단계 step_labeling_flow (tools/gui_smoke.py:1095) · 가장 안쪽 app/fake_mod.py:41 inner_wait"
            " · 앱 코드 app/fake_mod.py:41 inner_wait ← app/fake_mod.py:12 outer_call") in text
    assert "tools/gui_smoke.py:198 loop(덤프 쓴 스레드)" in text and "site-packages/mpv.py:689 _event_generator" in text
    assert "  Timeout (0:01:00)! · 시각 없음 · 스레드 1" in text and "단계 step_window" in text and "…잘림" in text
    assert "  Windows fatal exception: access violation · 시각 없음" in text and "단계 step_close" in text
    assert "app/fake_mod.py:? crashy" in text and "오류 난 스레드" in text
    assert "FAIL 단계: flow_sample_draw" in text
    # 개인정보·경로: 자식 출력의 글, 명령줄, 절대 경로를 옮기지 않는다
    for bad in ("합성비밀글", "someone", "Fake", ROOT, str(tmp_path), ":\\"):
        assert bad not in text, bad
    # stack.txt 나 watch.log 를 직접 줘도 같은 폴더를 본다
    assert sw.summarize_run(str(d / "stack.txt")) == lines == sw.summarize_run(str(d / "watch.log"))


def test_summarize_suspended_stall_without_dump(tmp_path):
    d = tmp_path / "20261023-110000-8"
    d.mkdir()
    (d / "stack.txt").write_text("== 시작 2026-10-23T11:00:00 pid=8\n", encoding="utf-8")
    (d / "watch.log").write_text(_watch_log_text(susp=17, thr=19, cpu_step=0.0, with_end=False), encoding="utf-8")
    text = "\n".join(sw.summarize_run(str(d)))
    assert "끝 줄 없음" in text and "스택 요청 1·받음 0 → 밖에서 멈춤으로 보임(① 스레드 대부분이 Suspended" in text
    assert "요청한 스택이 오지 않았다" in text and "Timeout" not in text


def test_summarize_timeout_warns_after_external_stop(tmp_path):
    d = tmp_path / "20261023-120000-9"
    d.mkdir()
    (d / "stack.txt").write_text("== 시작 2026-10-23T12:00:00 pid=9\nTimeout (0:01:00)!\nThread 0x1 (most recent call first):\n"
                                 + _fr(SMOKE, 3092, "<module>") + "\n", encoding="utf-8")
    (d / "watch.log").write_text(_watch_log_text(susp=20, cpu_step=0.0), encoding="utf-8")
    text = "\n".join(sw.summarize_run(str(d)))
    assert "가짜 Timeout 일 수 있음" in text and "단계 밖(step_* 틀 없음)" in text


def test_summarize_only_last_child_in_shared_stack_file(tmp_path):
    d = tmp_path / "20261023-130000-1"
    d.mkdir()
    old = _stack_text().replace("10:00:", "09:00:")
    (d / "stack.txt").write_text(old + "== 시작 2026-10-23T13:00:00 pid=1\n== 끝 2026-10-23T13:00:05 pid=1\n", encoding="utf-8")
    lines = sw.summarize_run(str(d))
    assert "watch.log 없음" in lines[1] and lines[-1] == "  멈춤·덤프 없음"


def test_summarize_main_latest_and_missing(tmp_path, capsys):
    root = tmp_path / "watch"
    _run_dir(root, "20261023-090000-1")
    _run_dir(root, "20261023-100000-2")
    (root / "남의폴더").mkdir()
    assert [os.path.basename(p) for p in sw.latest_runs(str(root), 5)] == ["20261023-090000-1", "20261023-100000-2"]
    got = []
    assert sw.summarize_main([], 1, root=str(root), echo=got.append) == 0
    assert got[0].startswith("[watch 요약] 20261023-100000-2") and not any("090000" in x for x in got)
    got.clear()
    assert sw.summarize_main([], 1, root=str(tmp_path / "없음"), echo=got.append) == 2 and "기록이 없습니다" in got[0]
    got.clear()
    assert sw.summarize_main([str(tmp_path / "없는폴더")], echo=got.append) == 2 and "찾지 못함: 없는폴더" in got[0]
    # 명령줄: 감시하지 않고(자식을 띄우지 않고) 요약만
    rc = sw.main(["--summarize", str(root / "20261023-090000-1")])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("[watch 요약] 20261023-090000-1") and "[watch] 기록" not in out
    ns = sw.parse_args(["--summarize"])
    assert ns.summarize == [] and ns.last == 1 and sw.parse_args([]).summarize is None


def test_summarize_real_watch_of_fake_child(tmp_path):
    """가짜 자식을 진짜로 감시한 기록을 요약하면, 멈춘 동안의 주 스레드 단계 함수가 나온다."""
    script = child(tmp_path, """
        import gui_smoke
        disarm = gui_smoke.arm_faulthandler(FH, poll_s=0.1, hang_s=0)
        print("PASS fake_first               1 ms  n=1", flush=True)
        def step_fake_slow():
            t = time.monotonic()
            while time.monotonic() - t < 3.5:
                time.sleep(0.05)
        step_fake_slow()
        print("PASS fake_slow             3500 ms  n=1", flush=True)
        disarm()
    """, name="fake_gui_smoke.py")
    res, _ = run_watch(tmp_path, script)
    assert res.rc == 0 and len(res.stalls) == 1 and res.dumps["written"] >= 1
    text = "\n".join(sw.summarize_run(res.out_dir))
    assert "앞 단계 fake_first · 뒤 처음 끝난 단계 fake_slow(3500 ms, PASS)" in text
    assert "멈춤 1 안" in text and "주 스레드: 단계 step_fake_slow (fake_gui_smoke.py:" in text
    assert str(tmp_path) not in text and ROOT not in text
    if WIN:                                  # 잠자는 자식이라 CPU 가 거의 없다
        assert "→ CPU 거의 0" in text or "→ CPU 조금" in text


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
