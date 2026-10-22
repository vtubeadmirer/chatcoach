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

멈춤 요약 (23 5-2 의 23번, 08 D-22 2 의 '멈춤 스택 보기')

  .venv\\Scripts\\python tools\\smoke_watch.py --summarize [기록 폴더 | stack.txt | watch.log …] [--last N]

- 이미 끝난 감시 기록을 읽기만 한다(자식을 띄우지 않는다). 경로를 안 주면 기본 기록 뿌리에서 가장 새 N 개(기본 1).
- 멈춤마다: 언제부터 얼마나 · 멈춤 앞에 마지막으로 끝난 단계 · 멈춤 뒤 처음 끝난 단계(그 단계의 ms) · 멈춘 동안 CPU 초/초 ·
  Suspended 최대 → 판정 한 마디(밖에서 멈춤 ① / CPU 를 쓰며 돎 = 긴 단계 / CPU 거의 0 = 기다림 ②).
- 덤프마다(요청 · Timeout · 치명): 어느 멈춤 안에서 받았는지, 주 스레드(가장 바깥 틀이 <module>)가 있던 **gui_smoke 단계
  함수(step_*)**와 줄, 가장 안쪽 틀, 앱 코드(app/·engine/) 틀 3개, 다른 스레드는 가장 안쪽 '자기' 틀 하나씩.
- 내는 것은 파일(저장소 안은 상대 경로, 밖은 site-packages/… 또는 파일 이름)·함수 이름·줄 번호·숫자·단계 이름뿐이다.
  watch.log 의 자식 출력은 PASS/FAIL 줄의 단계 이름과 ms 만 읽는다(나머지 칸·경고 글·명령줄·기록 경로는 옮기지 않는다).
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as _dt
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
MOSTLY_SUSPENDED = 0.8               # 스레드의 이 비율 이상이 Suspended 면 '밖에서 멈춤으로 보임'
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

    @property
    def mostly_suspended(self) -> bool:
        """스레드의 MOSTLY_SUSPENDED 이상이 Suspended. 23 3-4 의 실제 외부 정지는 17/18 → 17/19 였다(런처 스레드와
        멈춘 동안 새로 생긴 스레드는 멈추지 않음). 코드 교착은 스레드를 Suspended 로 만들지 않는다."""
        return self.threads > 0 and self.suspended >= MOSTLY_SUSPENDED * self.threads

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
            w("smp", p.text() + (" ALL-SUSPENDED" if p.all_suspended
                                 else " MOSTLY-SUSPENDED" if p.mostly_suspended else ""))
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
                           + (" · 스레드가 모두 Suspended(밖에서 멈춤으로 보임)" if p and p.all_suspended
                              else " · 스레드 대부분이 Suspended(밖에서 멈춤으로 보임)" if p and p.mostly_suspended
                              else ""))
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


# ---------------------------------------------------------------------------- 멈춤 요약 (--summarize, 23 5-2 의 23)
BUSY_CPU_RATE = 0.2                  # 멈춘 동안 CPU 초/초가 이 이상이면 '돎'(긴 단계·바쁜 반복)
IDLE_CPU_RATE = 0.02                 # 이 미만이면 '기다림'
MATCH_SLACK_S = 2.0                  # 덤프 시각(초 단위로 찍힘)을 감시 시각에 맞출 때 여유
APP_PREFIXES = ("app/", "engine/")
SUMMARY_MAX_BYTES = 32 << 20
_WAIT_FILES = frozenset({"threading.py", "queue.py", "selectors.py", "socket.py", "subprocess.py"})

_NOTE_RE = re.compile(r"\A== (.+?) (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}) pid=(\d+)\s*\Z")   # arm_faulthandler.note
_THREAD_RE = re.compile(r"\A(Current thread|Thread) (0x[0-9a-fA-F]+) \(most recent call first\):\s*\Z")
_STACK_RE = re.compile(r"\AStack \(most recent call first\):\s*\Z")
_FRAME_RE = re.compile(r'\A\s+File "(.*)", line (\d+|\?\?\?) in (.+?)\s*\Z')
_LOG_RE = re.compile(r"\A\[([ \d.\-]{8,})\] (.{4}) (.*)\Z")                                # watch() 의 w()
_STEP_OUT_RE = re.compile(r"\A(PASS|FAIL) (\S+)\s+(\d+) ms")                               # gui_smoke.check
_SMP_RE = re.compile(r"\Apids=(\d+) thr=(\d+) susp=(\d+) cpu=([\d.]+)s")
_STALL_EVT_RE = re.compile(r"\A멈춤 출력이 (\d+)초 없음")
_STALL_END_RE = re.compile(r"\A멈춤 끝: ([\d.]+)초")


def display_path(path: str) -> str:
    """스택 틀의 파일 경로 → 보일 이름. site-packages 아래는 'site-packages/…', 저장소 안은 상대 경로, 그 밖(표준 라이브러리
    등)은 파일 이름만. 사용자 폴더 이름이 든 절대 경로를 요약에 옮기지 않는다."""
    p = path.replace("\\", "/")
    i = p.lower().rfind("/site-packages/")
    if i >= 0:
        return "site-packages/" + p[i + len("/site-packages/"):]
    root = ROOT.replace("\\", "/").rstrip("/") + "/"
    if p.lower().startswith(root.lower()):
        return p[len(root):]
    return p.rsplit("/", 1)[-1] or p


@dataclass(frozen=True)
class Frame:
    file: str                            # display_path 를 거친 이름
    line: int | None
    func: str

    def text(self) -> str:
        return f"{self.file}:{self.line if self.line is not None else '?'} {self.func}"


@dataclass
class ThreadStack:
    ident: str                           # 0x… ('' = 'Stack (…)' 꼴, 스레드 하나)
    current: bool                        # faulthandler 가 덤프를 쓴 스레드(요청 덤프면 덤프 스레드, 치명이면 오류 난 스레드)
    frames: list[Frame] = field(default_factory=list)    # 가장 안쪽 먼저(faulthandler 순서)
    truncated: bool = False

    @property
    def is_main(self) -> bool:
        """주 스레드로 본다: 가장 바깥 틀이 <module>(스크립트 맨 위)."""
        return bool(self.frames) and self.frames[-1].func == "<module>"

    def own_frame(self) -> Frame | None:
        """가장 안쪽의 '자기' 틀 — threading·queue 같은 표준 기다림 틀은 건너뛴다(다 그렇다면 가장 안쪽)."""
        for f in self.frames:
            if f.file not in _WAIT_FILES:
                return f
        return self.frames[0] if self.frames else None

    def step(self) -> Frame | None:
        """가장 안쪽 gui_smoke 단계 함수(step_*) 틀. 없으면 None(단계 밖 — 시작·정리 등)."""
        for f in self.frames:
            if f.func.startswith("step_") and f.file.endswith("gui_smoke.py"):
                return f
        return None

    def app_frames(self, n: int = 3) -> list[Frame]:
        """앱·엔진 코드 틀(가장 안쪽 먼저) n 개."""
        return [f for f in self.frames if f.file.startswith(APP_PREFIXES)][:n]


@dataclass
class Dump:
    kind: str                            # request · timeout · fatal
    label: str                           # '요청 덤프 n' · 'Timeout (0:01:00)!' · 'Windows fatal exception: …'
    at: str | None = None                # 요청 덤프 시각(ISO, 초). Timeout·치명은 시각이 찍히지 않는다
    start_at: str | None = None          # 그 자식의 '== 시작' 시각(감시 시각으로 바꾸는 기준)
    pid: str | None = None
    threads: list[ThreadStack] = field(default_factory=list)
    complete: bool = False               # 요청 덤프의 '… 끝' 줄을 봤는가

    def main_thread(self) -> ThreadStack | None:
        for t in self.threads:
            if t.is_main:
                return t
        return None


def parse_stack(text: str) -> tuple[list[str], list[Dump]]:
    """스택 파일(gui_smoke --faulthandler-file) → (자식 '== 시작' 시각 목록, 덤프 목록). 모르는 줄은 건너뛴다."""
    starts: list[str] = []
    dumps: list[Dump] = []
    cur: Dump | None = None
    th: ThreadStack | None = None
    start_at: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        m = _NOTE_RE.match(line)
        if m:
            what, at, pid = m.groups()
            th = None
            if what == "시작":
                starts.append(at)
                start_at = at
                cur = None
            elif what.startswith("요청 덤프 ") and what.endswith(" 끝"):
                if cur is not None and cur.kind == "request":
                    cur.complete = True
                cur = None
            elif what.startswith("요청 덤프 "):
                cur = Dump("request", what, at=at, start_at=start_at, pid=pid)
                dumps.append(cur)
            else:
                cur = None
            continue
        if line.startswith("Timeout ("):
            cur, th = Dump("timeout", line.strip()[:80], start_at=start_at), None
            dumps.append(cur)
            continue
        if line.startswith(("Windows fatal exception", "Fatal Python error")):
            if cur is not None and cur.kind == "fatal" and not cur.threads:
                cur.label = (cur.label + " / " + line.strip())[:160]
            else:
                cur = Dump("fatal", line.strip()[:80], start_at=start_at)
                dumps.append(cur)
            th = None
            continue
        if cur is None:
            continue
        m = _THREAD_RE.match(line)
        if m:
            th = ThreadStack(m.group(2), m.group(1) == "Current thread")
            cur.threads.append(th)
            continue
        if _STACK_RE.match(line):
            th = ThreadStack("", True)
            cur.threads.append(th)
            continue
        if th is None:
            continue
        m = _FRAME_RE.match(line)
        if m:
            ln = m.group(2)
            th.frames.append(Frame(display_path(m.group(1)), int(ln) if ln.isdigit() else None, m.group(3)))
        elif line.strip() == "...":
            th.truncated = True
        elif not line.strip():
            th = None
    return starts, dumps


@dataclass
class StallInfo:
    """watch.log 에서 읽은 멈춤 하나."""
    no: int
    start_s: float                       # 마지막 출력 시각
    found_s: float                       # 멈춤을 알아챈 시각
    end_s: float | None = None           # 출력이 다시 나온 시각(None = 끝날 때까지)
    samples: list[tuple[float, int, int, float]] = field(default_factory=list)   # (시각, 스레드, Suspended, CPU 초)
    before_step: str | None = None       # 멈춤 앞에 마지막으로 끝난 단계
    after_step: tuple[str, str, int] | None = None      # 멈춤 뒤 처음 끝난 단계 (PASS/FAIL, 이름, ms)
    requests: int = 0                    # 이 멈춤 동안 감시가 보낸 스택 요청 수

    def length(self, until: float) -> float:
        return (self.end_s if self.end_s is not None else until) - self.start_s

    def cpu_rate(self) -> float | None:
        if len(self.samples) < 2 or self.samples[-1][0] <= self.samples[0][0]:
            return None
        return max(0.0, (self.samples[-1][3] - self.samples[0][3]) / (self.samples[-1][0] - self.samples[0][0]))

    def max_suspended(self) -> tuple[int, int] | None:
        if not self.samples:
            return None
        s = max(self.samples, key=lambda x: (x[2], x[1]))
        return s[2], s[1]

    def all_suspended(self) -> bool:
        return any(thr > 0 and sus == thr for _t, thr, sus, _c in self.samples)

    def mostly_suspended(self) -> bool:
        return any(thr > 0 and sus >= MOSTLY_SUSPENDED * thr for _t, thr, sus, _c in self.samples)

    def verdict(self) -> str:
        if self.all_suspended():
            return "밖에서 멈춤으로 보임(① 스레드가 모두 Suspended — 코드 문제 아님)"
        if self.mostly_suspended():
            return "밖에서 멈춤으로 보임(① 스레드 대부분이 Suspended — 코드 문제 아님)"
        r = self.cpu_rate()
        if r is None:
            return "CPU 를 모름(표본 2개 미만)"
        if r >= BUSY_CPU_RATE:
            return "CPU 를 쓰며 돎 — 출력 없이 긴 단계이거나 바쁜 반복(교착 아님)"
        if r < IDLE_CPU_RATE:
            return "CPU 거의 0 — 무언가를 기다림(② 주 스레드 스택을 본다)"
        return "CPU 조금 — 기다림과 일이 섞임(주 스레드 스택을 본다)"


@dataclass
class WatchLog:
    stalls: list[StallInfo] = field(default_factory=list)
    end: str | None = None               # 끝 요약 줄('[watch] 끝: ' 과 기록 경로를 뺀)
    fh_s: float | None = None            # '== faulthandler:' 출력 시각(자식 '== 시작' 직후)
    last_s: float = 0.0                  # 마지막 줄 시각
    steps: int = 0                       # PASS/FAIL 줄 수
    fails: list[str] = field(default_factory=list)
    any_all_suspended: bool = False      # 모두(또는 대부분) Suspended 인 표본이 있었다 = 외부 정지 흔적
    earlier_runs: int = 0               # 같은 watch.log 의 앞선 실행 수(마지막 실행만 본다)


def parse_watch_log(text: str) -> WatchLog:
    """watch.log → 멈춤·단계·끝 요약. 자식 출력은 PASS/FAIL 줄의 단계 이름과 ms, faulthandler 줄의 시각만 읽는다."""
    rows = []
    for raw in text.splitlines():
        m = _LOG_RE.match(raw.rstrip("\r"))
        if not m:
            continue
        try:
            t = float(m.group(1))
        except ValueError:
            continue
        rows.append((t, m.group(2).strip(), m.group(3)))
    cmd_idx = [i for i, r in enumerate(rows) if r[1] == "cmd"]
    out = WatchLog(earlier_runs=max(0, len(cmd_idx) - 1))
    if cmd_idx:
        rows = rows[cmd_idx[-1]:]
    steps: list[tuple[float, str, str, int]] = []
    samples: list[tuple[float, int, int, float]] = []
    reqs: list[float] = []
    last_out = 0.0
    cur: StallInfo | None = None
    for t, tag, body in rows:
        out.last_s = max(out.last_s, t)
        if tag == "out":
            if body.startswith("== faulthandler:") and out.fh_s is None:
                out.fh_s = t
            m = _STEP_OUT_RE.match(body)
            if m:
                steps.append((t, m.group(1), m.group(2), int(m.group(3))))
                if m.group(1) == "FAIL":
                    out.fails.append(m.group(2))
            last_out = t
        elif tag == "smp":
            m = _SMP_RE.match(body)
            if m:
                samples.append((t, int(m.group(2)), int(m.group(3)), float(m.group(4))))
                if "ALL-SUSPENDED" in body or "MOSTLY-SUSPENDED" in body:
                    out.any_all_suspended = True
        elif tag == "req":
            reqs.append(t)
        elif tag == "evt":
            if _STALL_EVT_RE.match(body):
                cur = StallInfo(no=len(out.stalls) + 1, start_s=last_out, found_s=t)
                out.stalls.append(cur)
            elif _STALL_END_RE.match(body) and cur is not None:
                cur.end_s = t
                cur = None
        elif tag == "end":
            s = body.split(" · 기록 ", 1)[0]
            out.end = s[len("[watch] 끝: "):] if s.startswith("[watch] 끝: ") else s
    out.steps = len(steps)
    for st in out.stalls:
        hi = st.end_s if st.end_s is not None else float("inf")
        st.samples = [x for x in samples if st.start_s <= x[0] <= hi]
        st.requests = sum(1 for t in reqs if st.found_s <= t <= hi)
        if st.mostly_suspended():
            out.any_all_suspended = True
        before = [s for s in steps if s[0] <= st.start_s]
        st.before_step = before[-1][2] if before else None
        after = [s for s in steps if s[0] > st.start_s]
        st.after_step = (after[0][1], after[0][2], after[0][3]) if after else None
    return out


def _iso(s: str | None) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def dump_watch_time(d: Dump, log: WatchLog) -> float | None:
    """요청 덤프 시각 → 감시 시각(초). 자식 '== 시작' 이 faulthandler 출력 줄(fh_s)과 같은 때라고 본다."""
    a, s = _iso(d.at), _iso(d.start_at)
    if a is None or s is None:
        return None
    return (a - s).total_seconds() + (log.fh_s or 0.0)


def stall_of(d: Dump, log: WatchLog) -> StallInfo | None:
    t = dump_watch_time(d, log)
    if t is None:
        return None
    for st in log.stalls:
        hi = st.end_s if st.end_s is not None else log.last_s
        if st.start_s - MATCH_SLACK_S <= t <= hi + MATCH_SLACK_S:
            return st
    return None


def _thread_line(t: ThreadStack, kind: str) -> str:
    f = t.own_frame()
    s = f.text() if f else "틀 없음"
    if t.current and kind == "request":
        s += "(덤프 쓴 스레드)"
    elif t.current and kind == "fatal":
        s += "(오류 난 스레드)"
    return s + (" …잘림" if t.truncated else "")


def describe_dump(d: Dump, log: WatchLog | None = None) -> list[str]:
    """덤프 하나의 요약 줄들(들여쓰기 포함)."""
    head = f"  {d.label}"
    if d.at:
        head += f" {d.at[11:19]}"
    if log is not None:
        st = stall_of(d, log) if d.kind == "request" else None
        if st is not None:
            head += f" · 멈춤 {st.no} 안"
        elif d.kind == "request":
            head += " · 멈춤 밖(시간 초과 요청이거나 시각을 맞추지 못함)"
        else:
            head += " · 시각 없음"
    head += f" · 스레드 {len(d.threads)}" + ("" if d.complete or d.kind != "request" else " · 끝 줄 없음(쓰다 멈춤)")
    lines = [head]
    if d.kind == "timeout" and log is not None and log.any_all_suspended:
        lines.append("    주의: 이 실행에 '모두 Suspended' 표본이 있다 — 외부 정지가 풀릴 때 생긴 가짜 Timeout 일 수 있음(23 5-2 의 24)")
    main = d.main_thread()
    if main is None:
        lines.append("    주 스레드: 못 찾음(가장 바깥 틀이 <module> 인 스레드 없음)")
    else:
        step = main.step()
        inner = main.frames[0].text() if main.frames else "틀 없음"
        app = " ← ".join(f.text() for f in main.app_frames()) or "없음"
        where = f"단계 {step.func} ({step.file}:{step.line})" if step else "단계 밖(step_* 틀 없음)"
        lines.append(f"    주 스레드: {where} · 가장 안쪽 {inner} · 앱 코드 {app}"
                     + (" · 오류 난 스레드" if main.current and d.kind == "fatal" else "")
                     + (" · …잘림" if main.truncated else ""))
    others = [t for t in d.threads if t is not main]
    if others:
        counts: dict[str, int] = {}
        for t in others:
            k = _thread_line(t, d.kind)
            counts[k] = counts.get(k, 0) + 1
        parts = [k + (f" ×{n}" if n > 1 else "") for k, n in counts.items()]
        lines.append(f"    다른 스레드 {len(others)}: " + " · ".join(parts))
    return lines


def resolve_run(path: str) -> tuple[str, str | None, str | None]:
    """기록 폴더·stack.txt·watch.log → (폴더, 스택 파일 또는 None, 감시 기록 또는 None). 없으면 FileNotFoundError."""
    p = os.path.abspath(path)
    if os.path.isdir(p):
        d = p
        stack, log = os.path.join(d, "stack.txt"), os.path.join(d, "watch.log")
    elif os.path.isfile(p):
        d = os.path.dirname(p)
        if os.path.basename(p).lower() == "watch.log":
            stack, log = os.path.join(d, "stack.txt"), p
        else:
            stack, log = p, os.path.join(d, "watch.log")
    else:
        raise FileNotFoundError(path)
    stack = stack if os.path.isfile(stack) else None
    log = log if os.path.isfile(log) else None
    if stack is None and log is None:
        raise FileNotFoundError(path)
    return d, stack, log


def _read_text(path: str) -> str:
    with open(path, "rb") as f:
        return f.read(SUMMARY_MAX_BYTES).decode("utf-8", "replace")


def summarize_run(path: str) -> list[str]:
    """감시 기록 하나의 요약 줄들. 파일·함수·줄 번호·숫자·단계 이름만 낸다."""
    d, stack, logp = resolve_run(path)
    log = parse_watch_log(_read_text(logp)) if logp else None
    starts, dumps = parse_stack(_read_text(stack)) if stack else ([], [])
    if len(starts) > 1:                 # 같은 스택 파일을 여러 실행이 썼다 — 마지막 자식만
        last = starts[-1]
        dumps = [x for x in dumps if x.start_at == last]
    name = os.path.basename(d.rstrip("\\/")) or d
    end = (log.end if log and log.end else "끝 줄 없음(감시가 도중에 끝났거나 watch.log 없음)")
    lines = [f"[watch 요약] {name} · {end}"]
    if log is None:
        lines.append("  watch.log 없음 — 멈춤 시각·CPU 없이 스택만 본다")
    elif log.earlier_runs:
        lines.append(f"  watch.log 에 앞선 실행 {log.earlier_runs}번이 더 있다 — 마지막 실행만 본다")
    if stack is None:
        lines.append("  stack.txt 없음")
    shown: set[int] = set()
    if log is not None:
        for st in log.stalls:
            ln = st.length(log.last_s)
            rng = f"{st.start_s:.1f}~" + (f"{st.end_s:.1f}초" if st.end_s is not None else "끝까지")
            before = st.before_step or "없음(첫 단계 전)"
            after = (f"{st.after_step[1]}({st.after_step[2]} ms, {st.after_step[0]})" if st.after_step
                     else "없음(그 뒤 끝난 단계 없음)")
            r = st.cpu_rate()
            cpu = f"CPU {r:.2f}초/초(표본 {len(st.samples)})" if r is not None else f"CPU 모름(표본 {len(st.samples)})"
            ms = st.max_suspended()
            sus = f"Suspended 최대 {ms[0]}/{ms[1]}" if ms else "스레드 상태 없음"
            mine = [i for i, x in enumerate(dumps) if x.kind == "request" and stall_of(x, log) is st]
            got = f"스택 요청 {st.requests}·받음 {len(mine)}"
            lines.append(f"  멈춤 {st.no}: {rng}({ln:.1f}초) · 앞 단계 {before} · 뒤 처음 끝난 단계 {after} · {cpu} · {sus}"
                         f" · {got} → {st.verdict()}")
            if st.requests and not mine:
                lines.append("    요청한 스택이 오지 않았다 — 자식의 파이썬 스레드가 돌지 못했다(① 외부 정지이거나 GIL 묶임)")
            for i in mine:
                lines.extend("  " + s for s in describe_dump(dumps[i], log))
                shown.add(i)
        if log.fails:
            lines.append("  FAIL 단계: " + ", ".join(log.fails))
    for i, x in enumerate(dumps):
        if i not in shown:
            lines.extend(describe_dump(x, log))
    if (log is None or not log.stalls) and not dumps:
        lines.append("  멈춤·덤프 없음")
    return lines


def latest_runs(root: str, n: int = 1) -> list[str]:
    """root 아래 이 스크립트의 기록 폴더 가운데 가장 새 n 개(오래된 것 먼저)."""
    try:
        names = sorted(x for x in os.listdir(root) if RUN_DIR_RE.match(x) and os.path.isdir(os.path.join(root, x)))
    except OSError:
        return []
    return [os.path.join(root, x) for x in names[-n:]] if n > 0 else []


def summarize_main(paths_: Sequence[str], last: int = 1, *, root: str | None = None,
                   echo: Callable[[str], None] | None = None) -> int:
    """--summarize: 주어진 기록(없으면 기본 뿌리의 가장 새 last 개)을 요약해 찍는다. 하나라도 못 찾으면 2."""
    echo = echo or _print
    targets = list(paths_) or latest_runs(root or default_watch_root(), max(1, last))
    if not targets:
        echo("[watch 요약] 감시 기록이 없습니다")
        return 2
    rc = 0
    for p in targets:
        try:
            for line in summarize_run(p):
                echo(line)
        except FileNotFoundError:
            echo(f"[watch 요약] 기록을 찾지 못함: {os.path.basename(os.path.normpath(p)) or p}")
            rc = 2
        except OSError as e:
            echo(f"[watch 요약] 읽지 못함: {os.path.basename(os.path.normpath(p)) or p} ({type(e).__name__})")
            rc = 2
    return rc


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
    ap.add_argument("--summarize", nargs="*", metavar="경로",
                    help="감시하지 않고, 끝난 기록(폴더·stack.txt·watch.log)의 멈춤·스택을 요약한다. 경로가 없으면 가장 새 기록")
    ap.add_argument("--last", type=int, default=1, help="--summarize 에 경로가 없을 때 가장 새 기록 몇 개를 볼지")
    ns = ap.parse_args(own)
    ns.child = child
    return ns


def main(argv: Sequence[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ns = parse_args(argv)
    if ns.summarize is not None:
        return summarize_main(ns.summarize, ns.last)
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
