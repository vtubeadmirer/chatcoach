"""시간 정렬(보정) — 영상이 VOD 의 어디인지 정하는 화면 없는 층 (08 D-32, 03 4-2, 02 7-5).

Qt 를 쓰지 않고 engine 패키지를 import 하지 않는다. 후보 찾기는 tools/find_anchors.py 를 따로 프로세스로 부른다
(앱 '파일 → 새 방송…' 의 앵커 단계와 같은 해석기 규칙 — app.engine_run.anchors_python).

위치 판단 media_position(project.json, 영상 경로) — 이 순서로 본다:
  confirmed  활성 판이 verified·skipped (사람이 정함)
  estimated  활성 판이 자동 위치 추정 판(note 머리 AUTO_NOTE) → 새 방송 끝에 확인 화면을 띄운다
  clip       ClipSlicer 이름에 구간이 있음(시작 0 포함 — 0 이 아니면 새 방송이 그 시작으로 판을 더했다)
  full       영상 길이와 VOD 길이 차가 FULL_TOL_MS 안 → 오프셋 0 이 맞다고 본다
  unknown    활성 판이 만든 그대로(판 0 · 오프셋 0 · 미확인) → 새 방송이 전사 뒤 자동 위치 추정을 한다
  manual     그 밖의 미확인 판(사람·도구가 더함) — 자동으로 덮지 않는다
보정 판 쓰기는 project.json 의 calibration 에 판을 더하기만 한다(timecode.append_rev). 저장 오프셋 축은 timecode 머리말 그대로:
  영상 파일 시각 = VOD 시각 + offset_ms (file_start 0 — 클립 시작도 offset_ms 에 들어 있다)
읽기 지연(D-32): 후보 짝의 차이(영상 시각 − 채팅 시각)에는 스트리머가 채팅을 보고 소리 내 읽기까지의 시간이 들어 있다.
지금까지의 보정(시범 3편 오프셋 0, 개발 방송 클립 시작)과 엔진 문턱은 이 지연을 빼고 맞춘 축이라, 짝 차이의 중앙값에서
READ_LAG_MS 를 뺀 값을 오프셋으로 둔다. 앵커에는 들은 시각을 그대로 남기고(file_ms), 판 note 에 뺀 값을 적는다.
로그·출력에는 숫자·코드만(채팅 본문·닉네임·전사문 없음).
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Sequence

from .core import timecode as tc

LOG = logging.getLogger("chatcoach")

FULL_TOL_MS = 2000                 # 02 7-2 '길이 차 2초 넘으면 경고' 와 같은 기준
# 읽기 지연 시작값: 참값(클립 시작)을 아는 실제 클립 2편에서 잰 '서로 맞는 후보' 차이 중앙값 5.3초 · 8.1초의 평균(D-32 측정).
# 두 편 모두 이 값을 빼면 참값과 ±1.4초. 편이 늘면 다시 잰다
READ_LAG_MS = 6700
AUTO_NOTE = "자동 위치 추정"        # 자동 추정 판의 note 머리(사람 확인 없음)
UI_NOTE = "보정 확정 화면"          # 이 화면이 더한 판의 note 머리
CANDIDATES_TOP = 20
CANDIDATES_TIMEOUT_S = 600.0
YES, NO, UNSURE = "yes", "no", "unsure"
VERDICTS = (YES, NO, UNSURE)

POSITIONS = ("confirmed", "estimated", "clip", "full", "unknown", "manual")


# ---------------------------------------------------------------------------- 보정 읽기
def _revs(cal: Mapping | None) -> list[Mapping]:
    return [x for x in (cal or {}).get("revs") or [] if isinstance(x, Mapping)] if isinstance(cal, Mapping) else []


def active_rev(cal: Mapping | None) -> Mapping | None:
    """calibration 의 활성 판(dict) 또는 None."""
    if not isinstance(cal, Mapping):
        return None
    return next((x for x in _revs(cal) if x.get("rev") == cal.get("active_rev")), None)


def is_default_calibration(cal: Mapping | None) -> bool:
    """활성 보정이 만든 그대로(판 하나 · 오프셋 0 · 앵커 없음 · 미확인)인가."""
    act = active_rev(cal)
    return act is None or (act.get("offset_ms") == 0 and not act.get("anchors") and act.get("status") == "unverified"
                           and len(_revs(cal)) == 1)


def is_auto_rev(rev: Mapping | None) -> bool:
    return isinstance(rev, Mapping) and str(rev.get("note") or "").startswith(AUTO_NOTE)


def active_calibration(data: Mapping | None) -> tc.Calibration:
    """project.json 활성 보정(읽지 못하면 판 0 · 오프셋 0) — 작업대 TimeMap 과 같은 규칙."""
    cal = (data or {}).get("calibration") if isinstance(data, Mapping) else None
    try:
        return tc.calibration_from_json(cal) if cal else tc.Calibration()
    except (KeyError, TypeError, ValueError):
        return tc.Calibration()


def approx_file_start_ms(data: Mapping | None) -> int:
    """영상 파일 0 초가 VOD 의 어디쯤인가(활성 보정으로, 0 이상) — find_anchors 의 창 방식 --file-start-ms."""
    return max(0, tc.video_to_chat_ms(0, active_calibration(data)))


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def media_duration_ms(data: Mapping | None) -> int | None:
    cur = ((data or {}).get("media") or {}).get("current") if isinstance(data, Mapping) else None
    probe = cur.get("probe") if isinstance(cur, Mapping) else None
    d = _int((probe or {}).get("duration_ms")) if isinstance(probe, Mapping) else None
    return d if d and d > 0 else None


def vod_duration_ms(data: Mapping | None) -> int | None:
    b = (data or {}).get("broadcast") if isinstance(data, Mapping) else None
    d = _int((b or {}).get("duration_ms")) if isinstance(b, Mapping) else None
    return d if d and d > 0 else None


def media_position(data: Mapping | None, video_path: str | None = None) -> str:
    """위치 판단(머리말 표). 활성 판의 status·note 로만 가른다(판이 몇 개인지는 보지 않는다 — 다시 돌려도 같은 답).
    video_path 가 없으면 project.json 의 media.current.path_abs 이름을 본다."""
    cal = (data or {}).get("calibration") if isinstance(data, Mapping) else None
    act = active_rev(cal)
    if act is not None and act.get("status") in ("verified", "skipped"):
        return "confirmed"
    if is_auto_rev(act):
        return "estimated"
    if video_path is None:
        cur = ((data or {}).get("media") or {}).get("current") if isinstance(data, Mapping) else None
        video_path = cur.get("path_abs") if isinstance(cur, Mapping) else None
    from .engine_run import parse_video_file_name
    vno, start = parse_video_file_name(video_path)
    want = str(((data or {}).get("broadcast") or {}).get("video_no") or "") if isinstance(data, Mapping) else ""
    if vno and start is not None and vno == want:
        return "clip"
    md, vd = media_duration_ms(data), vod_duration_ms(data)
    if md is not None and vd is not None and abs(md - vd) <= FULL_TOL_MS:
        return "full"
    return "unknown" if is_default_calibration(cal) else "manual"


# ---------------------------------------------------------------------------- 후보
@dataclass
class Candidate:
    """앵커 후보 한 줄(find_anchors --list 의 rows). 시각은 ms, 근거는 'text'·'name'·'name?'·'text+name' …"""
    chat_id: str
    chat_ms: int
    video_ms: int
    video_end_ms: int | None
    basis: str
    score: float
    verdict: str | None = None

    @property
    def offset_ms(self) -> int:
        """이 짝이 말하는 저장 오프셋: 영상 시각 − 채팅 시각."""
        return self.video_ms - self.chat_ms

    @property
    def kind(self) -> str:
        """project.schema anchor.kind."""
        return "nickname_call" if self.basis.startswith("name") else "text_read"


@dataclass
class Found:
    """후보 찾기 결과(숫자만). located 는 --locate 를 썼고 자리를 찾았을 때 그 정보."""
    candidates: list[Candidate] = field(default_factory=list)
    mode: str | None = None
    consistent: int | None = None
    median_offset_ms: int | None = None       # 서로 맞는 후보의 오프셋 중앙값(저장 오프셋 축)
    located: dict | None = None
    error: str | None = None
    elapsed_s: float | None = None


def parse_found(doc: Mapping | None) -> Found:
    """find_anchors --json --list 한 줄 → Found. 이상한 줄은 버린다."""
    if not isinstance(doc, Mapping):
        return Found(error="no_output")
    if doc.get("error"):
        return Found(error=str(doc.get("error"))[:40])
    out = Found(mode=doc.get("mode") if isinstance(doc.get("mode"), str) else None,
                consistent=_int(doc.get("consistent")), located=doc.get("located") if isinstance(doc.get("located"),
                                                                                              Mapping) else None,
                elapsed_s=doc.get("elapsed_s") if isinstance(doc.get("elapsed_s"), (int, float)) else None)
    fs = _int(doc.get("file_start_ms")) or 0
    med = _int(doc.get("median_diff_ms"))
    out.median_offset_ms = (med - fs) if med is not None else None
    for r in doc.get("rows") or []:
        if not isinstance(r, Mapping):
            continue
        cid, c, v = r.get("chat_id"), _int(r.get("chat_ms")), _int(r.get("video_ms"))
        if not isinstance(cid, str) or not cid or c is None or v is None:
            continue
        e = _int(r.get("video_end_ms"))
        sc = r.get("score")
        out.candidates.append(Candidate(chat_id=cid, chat_ms=c, video_ms=v, video_end_ms=e if e and e >= v else None,
                                        basis=str(r.get("basis") or "text")[:16],
                                        score=float(sc) if isinstance(sc, (int, float)) else 0.0))
    return out


def find_candidates(project_root: str, *, locate: bool, file_start_ms: int = 0, top: int = CANDIDATES_TOP,
                    engine_python: str | None = None, cancelled: Callable[[], bool] | None = None,
                    tick: Callable[[float], None] | None = None) -> Found:
    """tools/find_anchors.py --json --list 를 따로 프로세스로(파일을 쓰지 않음). asr 는 derived\\asr.json(새 방송이 만든 것).
    error 코드: tool_unavailable · asr_missing · cancelled · timeout · spawn_failed · <도구의 error> · exit_<n>."""
    from . import engine_run as er
    py = er.anchors_python(engine_python)
    tool = os.path.join(er.repo_root(), "tools", "find_anchors.py")
    asr = er.derived_path(project_root, er.ASR_REL)
    if py is None or not os.path.isfile(tool):
        return Found(error="tool_unavailable")
    if er.asr_summary(asr) is None:
        return Found(error="asr_missing")
    cmd = [py, "-I", tool, "--project", os.path.abspath(project_root), "--asr", asr, "--json", "--list",
           "--top", str(int(top))]
    cmd += ["--locate"] if locate else ["--file-start-ms", str(max(0, int(file_start_ms)))]
    p = er.RUN_PROCESS(cmd, cancelled=cancelled, tick=tick, timeout_s=CANDIDATES_TIMEOUT_S, env=er.tool_env())
    if p.error:
        return Found(error=p.error)
    if p.rc != 0:
        return Found(error=str(p.doc.get("error") or f"exit_{p.rc}")[:40])
    return parse_found(p.doc)


# ---------------------------------------------------------------------------- 맞춤
def fit_yes(cands: Sequence[Candidate]) -> tc.OffsetFit | None:
    """'맞음' 후보로 오프셋 중앙값(tools/calibrate.py 와 같은 timecode.fit_offset). 맞음이 없으면 None."""
    yes = [c for c in cands if c.verdict == YES]
    if not yes:
        return None
    return tc.fit_offset([(c.video_ms, c.chat_ms) for c in yes])


def ready(fit: tc.OffsetFit | None) -> bool:
    """확정할 수 있나: 앵커 3개 이상이고 경고(흩어짐·이상치) 없음 = timecode 의 verified 조건."""
    return fit is not None and fit.suggested_status == "verified"


def lag_corrected(median_diff_ms: int, lag_ms: int = READ_LAG_MS) -> int:
    """짝 차이(영상 − 채팅)의 중앙값 → 저장 오프셋(읽기 지연을 뺌, 머리말)."""
    return int(median_diff_ms) - int(lag_ms)


def counts(cands: Sequence[Candidate]) -> dict:
    out = {"candidates": len(cands), YES: 0, NO: 0, UNSURE: 0, "pending": 0}
    for c in cands:
        out[c.verdict if c.verdict in VERDICTS else "pending"] += 1
    return out


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def verified_rev_json(fit: tc.OffsetFit, cands: Sequence[Candidate], *, rev: int, media_rev: int, by: str,
                      now_iso: str, lag_ms: int = READ_LAG_MS) -> dict:
    """'맞음' 후보를 앵커로 한 판(tools/calibrate.build_rev 와 같은 모양, file_start 0 축). 오프셋 = 차이 중앙값 − 읽기 지연.
    앵커 file_ms 는 들은 시각 그대로, residual_max_ms 는 중앙값 기준(흩어짐)."""
    yes = [c for c in cands if c.verdict == YES]
    anchors = tuple(tc.Anchor(anchor_id=str(uuid.uuid4()), vod_ms=c.chat_ms, file_ms=c.video_ms, kind=c.kind,
                              chat_id=c.chat_id, note=("이상치" if out else None))
                    for c, out in zip(yes, fit.outliers))
    n = counts(cands)
    note = (f"{UI_NOTE}(D-32) · 맞음 {n[YES]} · 아님 {n[NO]} · 모름 {n[UNSURE]} · 후보 {n['candidates']} · "
            f"차이 중앙값 {fit.offset_ms}ms − 읽기 지연 {int(lag_ms)}ms · MAD={fit.mad_ms}ms 흩어짐={fit.spread_ms}ms")
    cal = tc.Calibration(offset_ms=lag_corrected(fit.offset_ms, lag_ms), anchors=anchors, rev=rev,
                         method="single_offset", media_rev=media_rev, status=fit.suggested_status,
                         residual_max_ms=fit.residual_max_ms, created_at=now_iso, created_by=by, note=note[:500])
    return tc.calibration_to_json(cal)


def skipped_rev_json(data: Mapping | None, *, rev: int, media_rev: int, by: str, now_iso: str, reason: str) -> dict:
    """건너뛰기(03 4-2): 오프셋은 지금 활성 판 그대로, status skipped + 사유. 활성 판이 자동 추정이면 note 에 그렇게 적는다
    (기계 추정을 사람이 '맞춤 없이 넘김' 으로 받아들였다는 기록)."""
    cur = active_calibration(data)
    auto = is_auto_rev(active_rev((data or {}).get("calibration") if isinstance(data, Mapping) else None))
    cal = tc.Calibration(offset_ms=cur.offset_ms, rev=rev, method="single_offset", media_rev=media_rev,
                         status="skipped", skip_reason=reason.strip()[:500], created_at=now_iso, created_by=by,
                         note=(f"{UI_NOTE}(D-32) · 건너뛰기 · 오프셋은 판 {cur.rev} 그대로"
                               + (" (자동 위치 추정 그대로, 사람 확인 없음)" if auto else "")))
    return tc.calibration_to_json(cal)


def auto_rev_json(median_diff_ms: int, *, rev: int, media_rev: int, by: str, now_iso: str, consistent: int,
                  peak: float | None = None, second: float | None = None, lag_ms: int = READ_LAG_MS) -> dict:
    """자동 위치 추정 판: 서로 맞는 후보의 차이 중앙값 − 읽기 지연, 앵커 없음 · unverified · note 에 숫자
    (사람 확인 없음 — 확정은 사람, 09 3-3)."""
    extra = ""
    if isinstance(peak, (int, float)) and isinstance(second, (int, float)):
        extra = f" · 창 합 {peak:.2f}/{second:.2f}"
    cal = tc.Calibration(offset_ms=lag_corrected(median_diff_ms, lag_ms), rev=rev, method="single_offset",
                         media_rev=media_rev, status="unverified", created_at=now_iso, created_by=by,
                         note=(f"{AUTO_NOTE}(D-32, 사람 확인 없음) · 서로 맞는 후보 {int(consistent)}{extra} · "
                               f"차이 중앙값 {int(median_diff_ms)}ms − 읽기 지연 {int(lag_ms)}ms"))
    return tc.calibration_to_json(cal)


def add_rev(write: Callable[[Callable[[dict], None]], object], build: Callable[[int, int, Mapping], dict]) -> dict:
    """calibration 에 판 하나를 더한다. write(mutate) 는 project.json 쓰기 통로(Project.update · 작업대 _write_project),
    build(rev, media_rev, 고치기 전 내용) → 판 dict. 판 번호·media_rev 는 쓰는 순간의 project.json 에서 정한다.
    더한 판(dict)을 돌려준다. ConflictError·ReadOnlyError·ValueError·OSError 는 그대로 올린다."""
    added: dict = {}

    def mutate(d: dict) -> None:
        cur = d.get("calibration")
        media_rev = int(((d.get("media") or {}).get("media_rev")) or 0)
        rj = build(tc.next_rev(cur), media_rev, d)
        d["calibration"] = tc.append_rev(cur, rj)
        added.clear()
        added.update(rj)
    write(mutate)
    LOG.info("보정 판 저장: 판 %s · 상태 %s · 앵커 %s", added.get("rev"), added.get("status"),
             len(added.get("anchors") or []))
    return dict(added)


__all__ = ["FULL_TOL_MS", "READ_LAG_MS", "AUTO_NOTE", "UI_NOTE", "YES", "NO", "UNSURE", "VERDICTS", "POSITIONS", "Candidate", "Found",
           "active_rev", "is_default_calibration", "is_auto_rev", "active_calibration", "approx_file_start_ms",
           "media_duration_ms", "vod_duration_ms", "media_position", "parse_found", "find_candidates", "fit_yes",
           "ready", "lag_corrected", "counts", "now_iso", "verified_rev_json", "skipped_rev_json", "auto_rev_json", "add_rev"]
