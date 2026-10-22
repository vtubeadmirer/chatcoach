"""GUI 스모크 바깥 감시 (22 5-2 의 1번 Claude 몫, 21 4-2 의 19번, 08 D-21 5).

gui_smoke 를 **자식 프로세스**로 띄우고 바깥에서 본다. 스모크가 가끔 멈추는 일(19 3-2: 스레드가 모두 Suspended — 보안
프로그램이 프로세스를 통째로 정지)은 안에서 재는 감시 스레드도 같이 멈춰 원인을 남기지 못했다. 이 스크립트는 다른
프로세스라 그때도 돈다.

  .venv\\Scripts\\python tools\\smoke_watch.py [--stall 30] [--sample 2] [--max-time 3600] [--out-dir 폴더] -- <gui_smoke 인자…>

하는 일
- 자식 출력(표준 출력·오류 합침)을 그대로 이 창에 찍고 감시 기록(watch.log)에도 시각과 같이 남긴다.
- --sample 초마다 자식 프로세스 **나무**(venv 의 python.exe 는 진짜 파이썬을 또 띄우는 런처라 그 아래까지)의 스레드 수,
  Suspended 스레드 수, CPU 초를 적는다(Windows: NtQuerySystemInformation — py-spy·PowerShell 없음).
- 출력이 --stall 초 넘게 없으면 '멈춤' 으로 보고 ① 그 순간의 스레드 상태를 적고 ② '<스택 파일>.request' 를 만들어
  gui_smoke 가 모든 스레드 스택을 스택 파일에 쓰게 한다(gui_smoke --faulthandler-file, 이 스크립트가 붙여 준다).
  멈춤이 이어지면 --stall 초마다 다시 요청한다(한 멈춤에 최대 3번). 출력이 다시 나오면 멈춤이 끝난다.
  스레드가 모두 Suspended 면 덤프는 오지 않는다 — 그것이 곧 '밖에서 멈췄다' 는 증거다.
- --max-time 을 넘기면 스택을 한 번 요청하고 3초 기다린 뒤 자식 나무를 끝내고 종료 코드 124.
- 끝나면 요약 한 줄: 종료 코드 · 걸린 시간 · 멈춤 횟수·최장 · 그때 Suspended · 덤프(요청·받음·자동·치명) · 기록 폴더.

기록 폴더(기본 %LOCALAPPDATA%\\ChatCoachSmoke\\watch\\<시각>-<pid>): watch.log(출력·표본·사건), stack.txt(스택 파일).
가장 새 20개만 남기고 그보다 오래된 이 스크립트의 기록 폴더는 shutil.rmtree 로 지운다(휴지통 아님, 공용 TEMP 아님).
자식 출력은 gui_smoke 규칙대로 숫자·통과 여부뿐이고, 스택 파일은 파일·함수 이름·줄 번호뿐이다(채팅 본문·닉네임 없음).

종료 코드: 자식의 종료 코드. 시간 초과로 끝냈으면 124, 자식을 띄우지 못했으면 2.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_SMOKE = os.path.join(ROOT, "tools", "gui_smoke.py")
REQUEST_SUFFIX = ".request"          # gui_smoke.FH_REQUEST_SUFFIX 와 같다(시험이 맞춰 본다)
FH_OPTION = "--faulthandler-file"
DEFAULT_STALL_S = 30.0
DEFAULT_SAMPLE_S = 2.0
DEFAULT_MAX_S = 3600.0
MAX_REQUESTS_PER_STALL = 3
KEEP_RUNS = 20
TIMEOUT_RC = 124
RUN_DIR_RE = re.compile(r"\A\d{8}-\d{6}-\d+\Z")
_REQ_DUMP_RE = re.compile(r"\A== 요청 덤프 \d+ (?!끝)")     # gui_smoke.arm_faulthandler 의 시작 줄('… 끝' 줄은 빼고)


# ---------------------------------------------------------------------------- 스레드 상태 (Windows)
@dataclass(frozen=True)
class Probe:
    """자식 프로세스 나무 한 번 본 값. suspended = 스레드 상태 Waiting 이고 기다림 까닭이 Suspended·WrSuspended 인 수."""
    pids: int
    threads: int
    suspended: int
    cpu_s: float

    @property
    def all_suspended(self) -> bool:
        return self.threads > 0 and self.suspended == self.threads

    def text(self) -> str:
        return f"pids={self.pids} thr={self.threads} susp={self.suspended} cpu={self.cpu_s:.2f}s"


_THREAD_WAITING = 5                  # KTHREAD_STATE Waiting
_SUSPEND_REASONS = (5, 12)           # KWAIT_REASON Suspended · WrSuspended
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_SYSTEM_PROCESS_INFORMATION = 5


if os.name == "nt":
    from ctypes import wintypes

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]

    class _ProcInfo(ctypes.Structure):
        """SYSTEM_PROCESS_INFORMATION (x64 에서 256 바이트, 시험이 크기를 본다). 바로 뒤에 스레드 정보 배열이 온다."""
        _fields_ = [("NextEntryOffset", ctypes.c_ulong), ("NumberOfThreads", ctypes.c_ulong),
                    ("WorkingSetPrivateSize", ctypes.c_longlong), ("HardFaultCount", ctypes.c_ulong),
                    ("NumberOfThreadsHighWatermark", ctypes.c_ulong), ("CycleTime", ctypes.c_ulonglong),
                    ("CreateTime", ctypes.c_longlong), ("UserTime", ctypes.c_longlong), ("KernelTime", ctypes.c_longlong),
                    ("ImageName", _UnicodeString), ("BasePriority", ctypes.c_long),
                    ("UniqueProcessId", ctypes.c_void_p), ("InheritedFromUniqueProcessId", ctypes.c_void_p),
                    ("HandleCount", ctypes.c_ulong), ("SessionId", ctypes.c_ulong), ("UniqueProcessKey", ctypes.c_size_t),
                    ("PeakVirtualSize", ctypes.c_size_t), ("VirtualSize", ctypes.c_size_t),
                    ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t), ("PrivatePageCount", ctypes.c_size_t),
                    ("ReadOperationCount", ctypes.c_longlong), ("WriteOperationCount", ctypes.c_longlong),
                    ("OtherOperationCount", ctypes.c_longlong), ("ReadTransferCount", ctypes.c_longlong),
                    ("WriteTransferCount", ctypes.c_longlong), ("OtherTransferCount", ctypes.c_longlong)]

    class _ThreadInfo(ctypes.Structure):
        """SYSTEM_THREAD_INFORMATION (x64 에서 80 바이트)."""
        _fields_ = [("KernelTime", ctypes.c_longlong), ("UserTime", ctypes.c_longlong), ("CreateTime", ctypes.c_longlong),
                    ("WaitTime", ctypes.c_ulong), ("StartAddress", ctypes.c_void_p),
                    ("UniqueProcess", ctypes.c_void_p), ("UniqueThread", ctypes.c_void_p),
                    ("Priority", ctypes.c_long), ("BasePriority", ctypes.c_long), ("ContextSwitches", ctypes.c_ulong),
                    ("ThreadState", ctypes.c_ulong), ("WaitReason", ctypes.c_ulong)]

    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtQuerySystemInformation.restype = ctypes.c_long
    _ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                                ctypes.POINTER(ctypes.c_ulong)]
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, ctypes.c_uint]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


@dataclass
class _Proc:
    pid: int
    ppid: int
    create: int
    cpu_100ns: int
    threads: list[tuple[int, int]]        # (ThreadState, WaitReason)


def _snapshot() -> dict[int, _Proc] | None:
    """모든 프로세스의 (부모·만든 시각·CPU·스레드 상태). Windows 가 아니거나 못 읽으면 None."""
    if os.name != "nt":
        return None
    size = 1 << 20
    for _ in range(8):
        buf = ctypes.create_string_buffer(size)
        need = ctypes.c_ulong(0)
        st = _ntdll.NtQuerySystemInformation(_SYSTEM_PROCESS_INFORMATION, buf, size, ctypes.byref(need)) & 0xFFFFFFFF
        if st == _STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, need.value + (64 << 10))
            continue
        if st != 0:
            return None
        break
    else:
        return None
    out: dict[int, _Proc] = {}
    pi_size, ti_size = ctypes.sizeof(_ProcInfo), ctypes.sizeof(_ThreadInfo)
    off = 0
    while off + pi_size <= size:
        pi = _ProcInfo.from_buffer_copy(buf, off)
        pid = int(pi.UniqueProcessId or 0)
        ths = []
        for i in range(int(pi.NumberOfThreads)):
            o = off + pi_size + i * ti_size
            if o + ti_size > size:
                break
            ti = _ThreadInfo.from_buffer_copy(buf, o)
            ths.append((int(ti.ThreadState), int(ti.WaitReason)))
        out[pid] = _Proc(pid, int(pi.InheritedFromUniqueProcessId or 0), int(pi.CreateTime),
                         int(pi.UserTime) + int(pi.KernelTime), ths)
        if not pi.NextEntryOffset:
            break
        off += int(pi.NextEntryOffset)
    return out


def tree_pids(snap: dict[int, _Proc], root: int) -> list[int]:
    """root 와 그 자손 pid(뿌리 먼저). 부모 pid 가 다시 쓰인 남의 프로세스는 만든 시각으로 거른다(부모보다 먼저 생긴 것 제외)."""
    if root not in snap:
        return []
    out, seen = [root], {root}
    i = 0
    while i < len(out):
        parent = snap[out[i]]
        for p in snap.values():
            if p.pid not in seen and p.pid and p.ppid == parent.pid and p.create >= parent.create:
                out.append(p.pid)
                seen.add(p.pid)
        i += 1
    return out


def probe_tree(root_pid: int) -> Probe | None:
    """자식 나무의 스레드 수·Suspended 수·CPU 초. Windows 가 아니거나 그 프로세스가 없으면 None."""
    snap = _snapshot()
    if not snap:
        return None
    pids = tree_pids(snap, root_pid)
    if not pids:
        return None
    thr = sus = cpu = 0
    for pid in pids:
        p = snap[pid]
        thr += len(p.threads)
        sus += sum(1 for s, w in p.threads if s == _THREAD_WAITING and w in _SUSPEND_REASONS)
        cpu += p.cpu_100ns
    return Probe(pids=len(pids), threads=thr, suspended=sus, cpu_s=cpu / 1e7)


def kill_tree(proc: subprocess.Popen) -> int:
    """자식 나무를 끝낸다(자손 먼저). 끝낸 수. Windows 가 아니면 proc.kill() 만."""
    n = 0
    if os.name == "nt":
        snap = _snapshot() or {}
        for pid in reversed(tree_pids(snap, proc.pid)):
            h = _kernel32.OpenProcess(0x0001, False, pid)            # PROCESS_TERMINATE
            if h:
                try:
                    if _kernel32.TerminateProcess(h, TIMEOUT_RC):
                        n += 1
                finally:
                    _kernel32.CloseHandle(h)
    try:
        proc.kill()
    except OSError:
        pass
    return n


# ---------------------------------------------------------------------------- 감시
@dataclass
class Stall:
    start_s: float                       # 마지막 출력 시각(감시 시작부터 초)
    end_s: float | None = None
    probe: Probe | None = None           # 멈춤을 알아챈 순간
    requests: int = 0
    last_request_s: float = 0.0

    def length(self, now_s: float) -> float:
        return (self.end_s if self.end_s is not None else now_s) - self.start_s


@dataclass
class WatchResult:
    rc: int
    elapsed_s: float
    out_dir: str
    stack_path: str
    stalls: list[Stall] = field(default_factory=list)
    samples: int = 0
    max_threads: int = 0
    max_suspended: int = 0
    probe_ok: bool = False
    timed_out: bool = False
    dumps: dict = field(default_factory=dict)        # requested · written · auto · fatal

    def summary(self) -> str:
        """끝에 찍는 요약 한 줄."""
        st = self.stalls
        if st:
            longest = max(st, key=lambda s: s.length(self.elapsed_s))
            p = longest.probe
            sus = f", 그때 Suspended {p.suspended}/{p.threads}" if p is not None else ", 그때 스레드 상태 모름"
            stall = f"멈춤 {len(st)}번(최장 {longest.length(self.elapsed_s):.1f}초{sus})"
        else:
            stall = "멈춤 0"
        d = self.dumps
        dump = (f"덤프 요청 {d.get('requested', 0)}·받음 {d.get('written', 0)}·자동 {d.get('auto', 0)}"
                f"·치명 {d.get('fatal', 0)}")
        susp = (f"Suspended 최대 {self.max_suspended}/{self.max_threads}" if self.probe_ok
                else "스레드 상태 못 봄")
        rc = f"rc={self.rc}" + (" (시간 초과로 끝냄)" if self.timed_out else "")
        return (f"[watch] 끝: {rc} · {self.elapsed_s:.1f}초 · {stall} · {susp} · {dump} · 표본 {self.samples}"
                f" · 기록 {self.out_dir}")


def count_dumps(stack_path: str) -> dict:
    """스택 파일에서 덤프 수를 센다: written = '== 요청 덤프 n' 시작 줄, auto = faulthandler 'Timeout (' 줄(GIL 묶임),
    fatal = 'Windows fatal exception' · 'Fatal Python error' 줄."""
    out = {"written": 0, "auto": 0, "fatal": 0}
    try:
        with open(stack_path, "rb") as f:
            data = f.read()
    except OSError:
        return out
    for line in data.decode("utf-8", "replace").splitlines():
        if _REQ_DUMP_RE.match(line):
            out["written"] += 1
        elif line.startswith("Timeout ("):
            out["auto"] += 1
        elif line.startswith("Windows fatal exception") or line.startswith("Fatal Python error"):
            out["fatal"] += 1
    return out


def _reader(stream, q: queue.Queue) -> None:
    for raw in iter(stream.readline, b""):
        q.put(raw)
    q.put(None)


def watch(cmd: Sequence[str], *, out_dir: str, stack_path: str, stall_s: float = DEFAULT_STALL_S,
          sample_s: float = DEFAULT_SAMPLE_S, max_s: float | None = DEFAULT_MAX_S,
          probe: Callable[[int], Probe | None] = probe_tree, echo: Callable[[str], None] | None = None,
          env: dict | None = None, clock: Callable[[], float] = time.monotonic) -> WatchResult:
    """cmd 를 띄워 끝날 때까지 본다. 기록은 out_dir\\watch.log. stack_path 는 자식에게 넘긴 스택 파일 경로
    (요청 파일 = stack_path + '.request')."""
    echo = echo or _print
    os.makedirs(out_dir, exist_ok=True)
    log = open(os.path.join(out_dir, "watch.log"), "a", encoding="utf-8", newline="\n")   # noqa: SIM115
    t0 = clock()

    def now_s() -> float:
        return clock() - t0

    def w(tag: str, text: str) -> None:
        log.write(f"[{now_s():8.2f}] {tag:<4} {text}\n")
        log.flush()

    req_path = stack_path + REQUEST_SUFFIX
    res = WatchResult(rc=2, elapsed_s=0.0, out_dir=out_dir, stack_path=stack_path)
    requested = 0
    w("cmd", " ".join(_quote(c) for c in cmd))
    cenv = dict(os.environ if env is None else env)
    cenv.setdefault("PYTHONIOENCODING", "utf-8")
    cenv.setdefault("PYTHONUNBUFFERED", "1")
    try:
        proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=cenv, cwd=ROOT)
    except OSError as e:
        w("err", f"띄우지 못함: {type(e).__name__}")
        echo(f"[watch] 자식을 띄우지 못했습니다: {type(e).__name__}")
        log.close()
        return res
    w("pid", str(proc.pid))
    q: queue.Queue = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout, q), name="watch-reader", daemon=True).start()
    last_out = 0.0
    next_sample = 0.0
    stall: Stall | None = None
    eof = False

    def request_dump(why: str) -> None:
        nonlocal requested
        try:
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(why + "\n")
            requested += 1
            w("req", f"스택 요청 ({why})")
        except OSError as e:
            w("err", f"요청 파일을 못 씀: {type(e).__name__}")

    def sample() -> Probe | None:
        p = probe(proc.pid)
        if p is not None:
            res.samples += 1
            res.probe_ok = True
            res.max_threads = max(res.max_threads, p.threads)
            res.max_suspended = max(res.max_suspended, p.suspended)
            w("smp", p.text() + (" ALL-SUSPENDED" if p.all_suspended else ""))
        return p

    try:
        while True:
            wait = max(0.0, min(next_sample - now_s(), 0.2))
            try:
                raw = q.get(timeout=wait) if not eof else None
            except queue.Empty:
                raw = False
            if raw is None and not eof:
                eof = True
            elif raw:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                echo(line)
                w("out", line)
                last_out = now_s()
                if stall is not None:
                    stall.end_s = last_out
                    w("evt", f"멈춤 끝: {stall.length(last_out):.1f}초")
                    stall = None
            t = now_s()                       # 출력이 쏟아질 때도 표본은 제때 적는다
            if t >= next_sample:
                next_sample = t + sample_s
                sample()
            if eof and proc.poll() is not None:
                break
            if eof:
                time.sleep(0.05)
            idle = t - last_out
            if idle >= stall_s and proc.poll() is None:
                if stall is None:
                    stall = Stall(start_s=last_out)
                    stall.probe = sample()
                    res.stalls.append(stall)
                    p = stall.probe
                    msg = (f"출력이 {idle:.0f}초 없음 · " + (p.text() if p else "스레드 상태 모름")
                           + (" · 스레드가 모두 Suspended(밖에서 멈춤으로 보임)" if p and p.all_suspended else ""))
                    w("evt", "멈춤 " + msg)
                    echo(f"[watch] 멈춤: {msg}")
                if stall.requests < MAX_REQUESTS_PER_STALL and (stall.requests == 0 or t - stall.last_request_s >= stall_s):
                    stall.requests += 1
                    stall.last_request_s = t
                    request_dump(f"멈춤 {len(res.stalls)} 요청 {stall.requests}")
            if max_s is not None and t >= max_s and proc.poll() is None:
                res.timed_out = True
                w("evt", f"시간 초과 {max_s:.0f}초")
                echo(f"[watch] 시간 초과({max_s:.0f}초): 스택을 요청하고 끝냅니다")
                sample()
                request_dump("시간 초과")
                time.sleep(3.0)
                n = kill_tree(proc)
                w("evt", f"끝냄 {n}개")
                break
    except KeyboardInterrupt:
        w("evt", "Ctrl+C — 자식을 끝냄")
        kill_tree(proc)
        res.timed_out = True
    try:
        rc = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        rc = proc.wait(timeout=10)
    res.rc = TIMEOUT_RC if res.timed_out else rc
    res.elapsed_s = now_s()
    if stall is not None and stall.end_s is None:
        stall.end_s = res.elapsed_s
    res.dumps = {"requested": requested, **count_dumps(stack_path)}
    try:
        os.remove(req_path)                     # 자식이 못 지운(멈췄거나 끝난) 요청
    except OSError:
        pass
    w("end", res.summary())
    log.close()
    return res


# ---------------------------------------------------------------------------- 명령줄
def _print(text: str) -> None:
    print(text, flush=True)


def _quote(s: str) -> str:
    return f'"{s}"' if (" " in s or not s) else s


def default_watch_root() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ChatCoachSmoke", "watch")


def prune_runs(root: str, keep: int = KEEP_RUNS) -> int:
    """root 아래 이 스크립트가 만든 기록 폴더(<yyyymmdd>-<hhmmss>-<pid>) 가운데 가장 새 keep 개만 남긴다. 지운 수."""
    try:
        names = sorted(n for n in os.listdir(root) if RUN_DIR_RE.match(n) and os.path.isdir(os.path.join(root, n)))
    except OSError:
        return 0
    n = 0
    for name in names[:-keep] if keep > 0 else names:
        try:
            shutil.rmtree(os.path.join(root, name))
            n += 1
        except OSError:
            pass
    return n


def build_cmd(child_args: Sequence[str], stack_path: str, *, python: str | None = None,
              script: str = GUI_SMOKE) -> tuple[list[str], str]:
    """자식 명령과 실제 스택 파일 경로. 인자에 --faulthandler-file 이 이미 있으면 그 경로를 쓴다."""
    args = list(child_args)
    for i, a in enumerate(args):
        if a == FH_OPTION and i + 1 < len(args):
            stack_path = args[i + 1]
            break
        if a.startswith(FH_OPTION + "="):
            stack_path = a.split("=", 1)[1]
            break
    else:
        args += [FH_OPTION, stack_path]
    return [python or sys.executable, "-u", script, *args], os.path.abspath(stack_path)


def parse_args(argv: Sequence[str] | None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        i = argv.index("--")
        own, child = argv[:i], argv[i + 1:]
    else:
        own, child = argv, []
    ap = argparse.ArgumentParser(description="gui_smoke 를 자식으로 띄워 바깥에서 감시한다 (멈춤 때 스레드 상태·스택)",
                                 epilog="gui_smoke 인자는 '--' 뒤에 적는다.")
    ap.add_argument("--stall", type=float, default=DEFAULT_STALL_S, help="출력이 이만큼(초) 없으면 멈춤으로 본다")
    ap.add_argument("--sample", type=float, default=DEFAULT_SAMPLE_S, help="스레드 상태를 적는 간격(초)")
    ap.add_argument("--max-time", type=float, default=DEFAULT_MAX_S, help="이보다 오래 걸리면 스택을 받고 끝낸다(초, 0 = 없음)")
    ap.add_argument("--out-dir", help="기록 폴더 (기본 %%LOCALAPPDATA%%\\ChatCoachSmoke\\watch\\<시각>-<pid>)")
    ap.add_argument("--python", help="자식 파이썬 (기본 이 파이썬)")
    ap.add_argument("--script", default=GUI_SMOKE, help=argparse.SUPPRESS)      # 시험용(가짜 자식)
    ns = ap.parse_args(own)
    ns.child = child
    return ns


def main(argv: Sequence[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ns = parse_args(argv)
    if ns.stall <= 0 or ns.sample <= 0:
        print("--stall · --sample 은 0 보다 커야 합니다")
        return 2
    if ns.out_dir:
        out_dir = os.path.abspath(ns.out_dir)
    else:
        root = default_watch_root()
        out_dir = os.path.join(root, f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}")
        os.makedirs(out_dir, exist_ok=True)
        prune_runs(root)
    cmd, stack = build_cmd(ns.child, os.path.join(out_dir, "stack.txt"), python=ns.python, script=ns.script)
    print(f"[watch] 기록 {out_dir} · 멈춤 {ns.stall:.0f}초 · 표본 {ns.sample:.1f}초", flush=True)
    res = watch(cmd, out_dir=out_dir, stack_path=stack, stall_s=ns.stall, sample_s=ns.sample,
                max_s=ns.max_time if ns.max_time and ns.max_time > 0 else None)
    print(res.summary(), flush=True)
    return res.rc


if __name__ == "__main__":
    sys.exit(main())
