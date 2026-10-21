"""QThread 워커 (02 8장). 라벨 파일에는 쓰지 않는다.

- UpdateCheckWorker · UpdateStageWorker: 업데이트 확인·준비 (M0).
- JobWorker: 이름 붙은 일 여러 개를 차례로 백그라운드에서 돌리는 일반 워커 (14 6-1 의 1번). 취소·진행·오류 시그널.
  SampleVerifyWorker · PlanVerifyWorker 는 그 위에 얹은 재현 검증 워커다. 호출 쪽(작업대·보고서 대화상자) 연결은 통합 담당.

워커 안에서는 위젯을 건드리지 않는다. 일은 파일 읽기·계산만 하고 결과는 시그널로 UI 스레드에 넘긴다.
로그에는 예외 종류·건수만 남긴다(채팅 본문·닉네임이 섞일 수 있는 메시지는 남기지 않는다).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Iterable, Mapping, Sequence

from PySide6.QtCore import QThread, Signal

from . import updater
from .appspec import AppSpec

LOG = logging.getLogger("chatcoach")


class UpdateCheckWorker(QThread):
    """updater.run_check 를 백그라운드에서. result: CheckResult | None (None = 확인할 때가 아님)."""
    result = Signal(object)

    def __init__(self, spec: AppSpec, installed: updater.InstalledInfo, state_path: str, force: bool,
                 base_url: str | None = None, parent=None, *, channel: str = "stable", on_start: bool = False):
        super().__init__(parent)
        self.spec, self.installed, self.state_path, self.force, self.base_url = spec, installed, state_path, force, base_url
        self.channel, self.on_start = updater.normalize_channel(channel), bool(on_start)

    def run(self):
        try:
            res = updater.run_check(self.spec, self.installed, self.state_path, force=self.force, base_url=self.base_url,
                                    channel=self.channel, on_start=self.on_start)
        except Exception as ex:  # 예상 못 한 예외도 '확인 실패' 로 보인다. 조용히 삼키지 않는다
            LOG.exception("업데이트 확인 중 예외")
            res = updater.CheckResult("failed", f"확인 중 오류: {ex}", "network")
        self.result.emit(res)


class UpdateStageWorker(QThread):
    """updater.stage 를 백그라운드에서. progress(0~1, 문구), done(StagedUpdate | None, 오류 문구)."""
    progress = Signal(float, str)
    done = Signal(object, str)

    def __init__(self, release: updater.Release, spec: AppSpec, installed: updater.InstalledInfo,
                 base_url: str | None = None, parent=None):
        super().__init__(parent)
        self.release, self.spec, self.installed, self.base_url = release, spec, installed, base_url
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            staged = updater.stage(self.release, self.spec, self.installed, self.base_url,
                                   progress=self.progress.emit, cancel=lambda: self._cancel)
            self.done.emit(staged, "")
        except updater.UpdateError as ex:
            LOG.warning("업데이트 준비 실패 (%s): %s", ex.kind, ex.message)
            self.done.emit(None, f"[{ex.kind}] {ex.message}")
        except Exception as ex:
            LOG.exception("업데이트 준비 중 예외")
            self.done.emit(None, f"준비 중 오류: {ex}")


# ---------------------------------------------------------------------------- 일반 작업 워커 (14 6-1 의 1번)
class JobCancelled(Exception):
    """일 안에서 JobContext.check() 가 취소를 알아챘다. 워커는 cancelled 로 끝낸다."""


class JobContext:
    """일(job) 함수가 받는 것: 취소 확인과 일 안의 진행 알림. 위젯을 넘기지 않는다."""

    def __init__(self, worker: "JobWorker", key: str):
        self._worker, self.key = worker, key

    def cancelled(self) -> bool:
        return self._worker.is_cancel_requested()

    def check(self) -> None:
        """취소됐으면 JobCancelled. 긴 일은 고리마다 부른다."""
        if self.cancelled():
            raise JobCancelled()

    def progress(self, fraction: float, text: str = "") -> None:
        """이 일 안의 진행(0~1). 전체 진행으로 바꿔 subprogress 로 보낸다."""
        self._worker._sub(self.key, fraction, text)


Job = tuple[str, Callable[[JobContext], Any]]


class JobWorker(QThread):
    """이름 붙은 일들을 차례로 돌린다. 끝은 done · failed · cancelled 셋 가운데 **하나만** 한 번 나온다.

    시그널
    - progress(n_done, total, key): 일 하나를 시작할 때(key = 시작하는 일), 모두 끝나면 (total, total, "").
    - subprogress(fraction, key, text): 일 안의 진행(JobContext.progress). fraction 은 전체 기준 0~1.
    - item_done(key, result): 일 하나가 끝남.
    - done(results): 모두 끝남. results = {key: result} (넣은 순서).
    - failed(key, message): on_error 가 없을 때 일이 예외를 냄. 남은 일은 돌리지 않는다.
    - cancelled(partial): cancel() 뒤 멈춤. partial = 그때까지의 결과.

    on_error(key, exc) 를 주면 예외를 그 결과로 바꾸고 다음 일로 간다(재현 검증: '실패 1건' 으로 기록).
    취소는 일과 일 사이에서, 그리고 일이 JobContext.check() 를 부를 때 먹힌다 — 지금 도는 계산을 억지로 끊지 않는다.
    run() 을 직접 부르면 부른 스레드에서 돈다(도구·테스트용, 시그널은 바로 전달된다).
    """
    progress = Signal(int, int, str)
    subprogress = Signal(float, str, str)
    item_done = Signal(str, object)
    done = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal(object)

    def __init__(self, jobs: Iterable[Job], *, name: str = "작업",
                 on_error: Callable[[str, BaseException], Any] | None = None, parent=None):
        super().__init__(parent)
        self.jobs: list[Job] = [(str(k), fn) for k, fn in jobs]
        keys = [k for k, _ in self.jobs]
        if len(set(keys)) != len(keys):
            raise ValueError("일 이름이 겹칩니다")
        self.job_name = name
        self._on_error = on_error
        self._cancel = threading.Event()
        self._index = 0
        self.results: dict[str, Any] = {}
        self.outcome: str | None = None          # "done" | "failed" | "cancelled" (끝난 뒤)

    @classmethod
    def single(cls, key: str, fn: Callable[[JobContext], Any], **kw) -> "JobWorker":
        """일 하나짜리 (예: agreement_report.comparison_gate 를 UI 밖에서)."""
        return cls([(key, fn)], **kw)

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def _sub(self, key: str, fraction: float, text: str) -> None:
        total = max(1, len(self.jobs))
        f = min(1.0, max(0.0, float(fraction)))
        self.subprogress.emit((self._index + f) / total, key, text)

    def _finish(self, results: dict[str, Any]) -> None:
        """모두 끝났을 때(하위 클래스가 모양을 바꿔 더 내보낸다)."""
        self.done.emit(results)

    def run(self):
        results: dict[str, Any] = {}
        total = len(self.jobs)
        for n, (key, fn) in enumerate(self.jobs):
            self._index = n
            if self._cancel.is_set():
                self._end_cancelled(results)
                return
            self.progress.emit(n, total, key)
            try:
                res = fn(JobContext(self, key))
            except JobCancelled:
                self._end_cancelled(results)
                return
            except Exception as ex:  # noqa: BLE001 — 예외 종류만 로그에 남기고 UI 에 알린다
                LOG.warning("%s: 일 %d/%d 실패 (%s)", self.job_name, n + 1, total, type(ex).__name__)
                if self._on_error is None:
                    self.results, self.outcome = results, "failed"
                    self.failed.emit(key, f"{type(ex).__name__}: {ex}")
                    return
                try:
                    res = self._on_error(key, ex)
                except Exception as ex2:  # noqa: BLE001
                    self.results, self.outcome = results, "failed"
                    self.failed.emit(key, f"{type(ex2).__name__}: {ex2}")
                    return
            results[key] = res
            self.item_done.emit(key, res)
        self._index = total
        self.results, self.outcome = results, "done"
        self.progress.emit(total, total, "")
        self._finish(dict(results))

    def _end_cancelled(self, results: dict[str, Any]) -> None:
        LOG.info("%s: 취소 (%d/%d 끝남)", self.job_name, len(results), len(self.jobs))
        self.results, self.outcome = results, "cancelled"
        self.cancelled.emit(dict(results))


def _problem_count(v) -> int:
    probs = getattr(v, "problems", None)
    if probs is None and isinstance(v, Mapping):
        probs = v.get("problems")
    if isinstance(probs, int) and not isinstance(probs, bool):
        return probs
    return len(probs or ())


def _ok_of(v) -> bool:
    ok = getattr(v, "ok", None)
    if ok is None and isinstance(v, Mapping):
        ok = v.get("ok")
    return ok is True


class SampleVerifyWorker(JobWorker):
    """잠그기 직전 표본 재현 검증(03 15-5)을 UI 스레드 밖에서.

    manifests: 검증할 매니페스트(계열마다 가장 큰 rev — 작업대 sample_verify_results 와 같은 고르기는 부르는 쪽이 한다).
    verify_one(manifest) → sampling.Verification(ok·problems). **부르기 전에 UI 상태(guide 값·영상 길이·앞 판 매니페스트)를
    값으로 잡아 둔 함수**여야 한다 — 워커 스레드에서 위젯을 읽지 않게.
    verified(list): 잠금 파일 sample_verify 칸 모양 [{sample_set_id, ok, problems(건수)}], sample_set_id 순. 예외는 ok=False·1건.
    """
    verified = Signal(list)

    def __init__(self, manifests: Sequence[Mapping], verify_one: Callable[[Mapping], Any], parent=None):
        mans = sorted(manifests, key=lambda m: str(m["sample_set_id"]))

        def job(m):
            return lambda ctx: _sample_row(m["sample_set_id"], verify_one(m))
        super().__init__([(str(m["sample_set_id"]), job(m)) for m in mans], name="표본 재현 검증",
                         on_error=lambda key, _ex: {"sample_set_id": key, "ok": False, "problems": 1}, parent=parent)

    def _finish(self, results):
        super()._finish(results)
        self.verified.emit(sample_verify_rows(results))


def _sample_row(sample_set_id: str, v) -> dict:
    return {"sample_set_id": sample_set_id, "ok": _ok_of(v), "problems": _problem_count(v)}


def sample_verify_rows(results: Mapping[str, Any]) -> list[dict]:
    """SampleVerifyWorker 결과 dict → 잠금 파일 sample_verify 칸 목록 (sample_set_id 순)."""
    return [dict(results[k]) for k in sorted(results)]


class PlanVerifyWorker(JobWorker):
    """보고서의 계획 재현 검증(relabel.verify_plan_on_disk)을 UI 스레드 밖에서.

    잠금·relabel/·samples/ 만 연다(라벨 파일을 열지 않는다). creator_kind 는 verify_plan_on_disk 와 같다
    (재라벨 세션에서는 부르지 않는다 — samples/ 를 연다).
    verified(dict): {plan_id: {"plan_id", "ok", "problems": (문장, …)}}. 예외는 ok=False 와 예외 종류 1줄.
    """
    verified = Signal(dict)

    def __init__(self, project_root: str, project_data: Mapping, plan_ids: Iterable[str], *,
                 creator_kind: str | None = None, parent=None):
        data = dict(project_data)
        ids = list(dict.fromkeys(str(p) for p in plan_ids))

        def job(pid):
            def run(_ctx):
                from . import relabel as relabel_io
                v = relabel_io.verify_plan_on_disk(project_root, data, pid, creator_kind=creator_kind)
                return {"plan_id": pid, "ok": bool(v.ok), "problems": tuple(v.problems)}
            return run
        super().__init__([(pid, job(pid)) for pid in ids], name="계획 재현 검증",
                         on_error=lambda key, ex: {"plan_id": key, "ok": False,
                                                   "problems": (f"검증 중 오류: {type(ex).__name__}",)},
                         parent=parent)

    def _finish(self, results):
        super()._finish(results)
        self.verified.emit(dict(results))
