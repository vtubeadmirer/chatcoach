"""시범 해제 당일 리허설 — 합성 시범 3편으로 '판정만 봉인 → 잠금 → 짝 보충 → 해제 → 비교 → 보정 → 검사' 를 한 번에
(08 D-22 2, D-23 3·4, 24 2장·5-3 의 4·7장 '나'). 지난 묶음 scratchpad 의 rehearse.py·rehearse_fix.py 를 저장소로 옮긴 것.

자리: %LOCALAPPDATA%\\ChatCoachSmoke\\rehearsal-<시각>-<pid>\\chatcoach-data\\{projects,ai,reports,pilot,sealed-work}.
  그 밖에는 쓰지 않는다(%LOCALAPPDATA% 도 그 안의 localappdata\\ 로 바꾸고, 앱 데이터 폴더도 그 안 appdata\\ 로 지정).
  자리가 ChatCoachSmoke 밖이거나 실제 데이터 폴더(chatcoach-data, 저장소 옆) 아래면 아무것도 만들기 전에 종료 2.
  끝나면 shutil.rmtree 로 지운다(--keep 이면 남김 — 합성 자료뿐이다).
실제 시범 프로젝트·봉인 폴더·실제 채팅·닉네임·전사문은 열지도 쓰지도 않는다. 자료는 모두 합성
(tests/test_pilot_unseal_day 의 합성 시범 도우미 — 합성 채팅 40줄, 엔진 2판 'link-v0.3'·'0.1.0', 합성 주제 흐름·전사 낱말·벡터).

차례 (기본 --seal-mode attach = 실제 시범과 같은 모양, D-23 3)
  1. 합성 시범 3편(pilot-15000001~3) + 시범 기록(pilot\\setup.json · freeze.json). 1편은 표본 매니페스트로 잠근다.
  2. 판정만 봉인(엔진 2판, 1차 잠금 전 = 사전 봉인). 짝은 없다.
  3. 1차 잠금 → 해제 당일 도구 dry-run 이 조건 미충족(종료 3)인지.
  4. 재라벨 계획·라벨·잠금.
  5. 짝 보충: 짝 파일을 <데이터>\\sealed-work\\ 에 쓰고 `ai_seal attach-pairs --all --topics --words --vectors --remove-source`
     (종료 0, 판정마다 짝 셋 attached, 사전 봉인 그대로, sealed-work 가 빔). 두 번째 attach-pairs 는 모두 'present'(종료 0).
     해제 전 네 읽기 함수가 모두 SealNotUnsealedError.
  6. 해제 당일 도구 dry-run(종료 0, 열기 감시 위반 0·쓰기 0, 아무것도 안 풂) → --go(종료 0, 편마다 해제 2·실패 0·짝 셋 각 2·
     짝 오류 0·대조 ok, 사전 봉인 2, 보고서 2, 판끼리 1, 보정 됨). 시범 폴더는 바뀌지 않는다(파일·크기·시각·해시).
  7. `ai_seal verify` 를 편마다(종료 0, run·topics·words·vectors 모두 ok).
  8. 출력(요약 폴더·도구 출력)에 합성 본문·닉네임·전사 낱말 표지가 없는지.
  --gui 를 주면 끝에 화면 확인을 더한다(offscreen 권장, 합성 영상은 로컬 ffmpeg 로 만든다 — 다운로드 없음): 잠기기 전 시청자 훈련
  메뉴 꺼짐 · 나란히 보기 기본 = v0.3 run(runs\\ 에서 읽고 sealed\\ 는 안 엶) · 방송 요약이 topics\\ 에서 읽음 · 시청자 훈련이
  run 의 전사 낱말을 읽고 엔진을 띄우지 않음.
  --seal-mode together 는 지난 묶음 방식(판정과 짝을 함께, 두 잠금 뒤 봉인 — 사전 봉인 아님)이다.

모든 도구는 이 프로세스 안에서 그 도구의 main(argv) 으로 부른다(돌려받는 값 = 종료 코드). 앱 데이터 폴더 지정이 자식
프로세스로 넘어가지 않아 저장소 data\\ 에 쓰는 일이 없게 하려는 것이다.

출력은 숫자·코드·ID 만(합성 자료라도 본문·낱말은 찍지 않는다). 사람 클릭 0(USER_CLICKS).
사용(저장소 루트, 앱 .venv): QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python tools\\pilot_rehearsal.py [--gui] [--keep]
                             [--seal-mode attach|together] [--json]
종료 코드: 0 검사 모두 통과 · 1 검사 실패 · 2 입력 오류(자리)
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import builtins
import contextlib
import hashlib
import io
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from typing import Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TOOL = "pilot_rehearsal"
TOOL_VERSION = "0.1"
USER_CLICKS = 0                            # 리허설·해제 당일 사람 클릭 (D-16)
SMOKE_DIR = "ChatCoachSmoke"
SEAL_MODES = ("attach", "together")
EXPECT = 3


class RehearsalError(RuntimeError):
    """입력 오류(종료 2)."""


def default_base(stamp: str | None = None) -> str:
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    lad = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(lad, SMOKE_DIR, f"rehearsal-{stamp}-{os.getpid()}")


def check_base(base: str) -> str:
    """자리 규칙: 경로에 ChatCoachSmoke 폴더가 있어야 하고, 실제 데이터 폴더 아래면 안 되며, 아직 없어야 한다."""
    from tools import pilot_unseal_day as day
    b = os.path.abspath(base)
    parts = [p.lower() for p in os.path.normpath(b).split(os.sep)]
    if SMOKE_DIR.lower() not in parts:
        raise RehearsalError("리허설 자리는 %LOCALAPPDATA%\\ChatCoachSmoke 아래여야 합니다")
    if day.touches_real_data([b]):
        raise RehearsalError("실제 데이터 폴더(chatcoach-data) 아래에는 리허설을 만들지 않습니다")
    if os.path.exists(b):
        raise RehearsalError("리허설 자리가 이미 있습니다(새 자리에서만)")
    return b


# ───────────────────────────────────────────────────────────── 기록

class Log:
    def __init__(self, verbose: bool = True):
        self.t0 = time.perf_counter()
        self.out: dict = {"tool": TOOL, "tool_version": TOOL_VERSION, "steps": {}, "checks": {}}
        self.verbose = verbose
        self.captured: list[str] = []          # 도구 출력(표지 검사용 — 화면에는 숫자만)

    def step(self, name: str, t: float) -> None:
        self.out["steps"][name] = round(time.perf_counter() - t, 2)

    def check(self, name: str, ok, detail=None) -> bool:
        ok = bool(ok)
        self.out["checks"][name] = ok if detail is None else {"ok": ok, "detail": detail}
        if self.verbose:
            print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if ok or detail is None else f"  {detail}"), flush=True)
        return ok

    @property
    def failed(self) -> list[str]:
        return [k for k, v in self.out["checks"].items() if not (v if isinstance(v, bool) else v["ok"])]


def run_main(log: Log, fn, argv: Sequence[str]) -> tuple[int, str]:
    """도구의 main(argv) 를 이 프로세스에서 부른다 → (종료 코드, 표준 출력). 출력은 표지 검사를 위해 모아 둔다."""
    so, se = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
        try:
            code = fn(list(argv))
        except SystemExit as e:                 # argparse 오류
            code = e.code if isinstance(e.code, int) else 2
    log.captured += [so.getvalue(), se.getvalue()]
    return int(code or 0), so.getvalue()


def _last_json(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def snapshot(roots: Sequence[str], base: str) -> dict:
    out = {}
    for r in roots:
        for dp, _dn, fn in os.walk(r):
            for n in fn:
                p = os.path.join(dp, n)
                st = os.stat(p)
                with builtins.open(p, "rb") as f:
                    out[os.path.relpath(p, base)] = (st.st_size, st.st_mtime_ns, hashlib.sha256(f.read()).hexdigest())
    return out


class Spy:
    """builtins.open·io.open·os.open 을 감싸 연 경로를 센다(--gui 확인용)."""

    def __init__(self):
        self.paths: list[str] = []

    def __enter__(self):
        self.saved = (builtins.open, io.open, os.open)
        rb, _ri, ro = self.saved
        me = self

        def note(p):
            try:
                me.paths.append(os.path.normcase(os.path.abspath(os.fspath(p))))
            except TypeError:
                pass

        def b(file, *a, **k):
            note(file)
            return rb(file, *a, **k)

        def o(path, *a, **k):
            note(path)
            return ro(path, *a, **k)
        builtins.open, io.open, os.open = b, b, o
        return self

    def __exit__(self, *e):
        builtins.open, io.open, os.open = self.saved

    def count(self, *parts) -> int:
        need = os.path.normcase(os.path.join(*parts))
        return sum(1 for p in self.paths if need in p)


# ───────────────────────────────────────────────────────────── 합성 자료

def _engine_grades(T, pre: str) -> dict:
    g = dict(T.GRADES)
    if pre == "run-v02":
        g[3], g[11] = "likely", "direct"           # v0.2 가 두 군데 다르게 본다(테스트 도우미와 같음)
    return g


def pair_bytes(T, pj: dict, tag: str) -> dict[str, bytes]:
    """합성 짝 셋(주제 흐름·전사 낱말·벡터) 바이트."""
    from tests.test_seal import vectors_doc
    vno = pj["video_no"]
    return {"topics": T._b(T.topic_doc(f"topics-{tag}", vno)),
            "words": T._b(T.words_doc(f"words-{tag}", pj["pid"], vno)),
            "vectors": T._b(vectors_doc(project_id=pj["pid"], video_no=str(vno), run_id=f"tvec-{tag}"))}


def seal_judgements(T, pj: dict, *, with_pairs: bool, now: datetime) -> list[tuple]:
    """엔진 2판(실제 판 이름, v0.3 먼저) 판정을 봉인. with_pairs 면 짝 셋도 함께(지난 묶음 방식)."""
    from app import seal
    rows = []
    for ver, pre in T.REAL_ENGINE_RUNS_V03_FIRST:
        run = f"{pre}-{pj['idx']}"
        e = seal.seal_bytes(pj["root"], T._b(T.ai_doc(run, ver, pj["video_no"], _engine_grades(T, pre))),
                            source="engine_link", by="L01", now=now)
        row = [e.check]
        if with_pairs:
            pb = pair_bytes(T, pj, run)
            row += [seal.seal_topics_bytes(pj["root"], pb["topics"], pair_sha256=e.sha256, by="L01",
                                           source="engine_topics", now=now).check,
                    seal.seal_words_bytes(pj["root"], pb["words"], pair_sha256=e.sha256, by="L01",
                                          source="engine_words", now=now).check,
                    seal.seal_vectors_bytes(pj["root"], pb["vectors"], pair_sha256=e.sha256, by="L01",
                                            source="engine_vectors", now=now).check]
        rows.append(tuple(row))
    return rows


def make_video(base: str, video_no: int) -> str:
    """합성 영상(테스트 패턴 + 사인파, 로컬 ffmpeg — 다운로드 없음)."""
    import subprocess
    from app import paths
    vids = os.path.join(base, "videos")
    os.makedirs(vids, exist_ok=True)
    vp = os.path.join(vids, f"synthetic-{video_no}.mp4")
    ffmpeg = paths.find_tool("ffmpeg")
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=64x48:rate=1:duration=620", "-f", "lavfi",
                    "-i", f"sine=frequency={300 + video_no % 7 * 50}:sample_rate=8000:duration=620",
                    "-c:v", "mpeg4", "-c:a", "aac", "-ac", "1", "-shortest", vp], check=True, capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return vp


# ───────────────────────────────────────────────────────────── 리허설

def rehearse(base: str, *, seal_mode: str = "attach", gui: bool = False, verbose: bool = True,
             date: str | None = None) -> dict:
    """리허설 한 번 → 기록 dict(steps·checks·failed_checks·숫자). base 아래에만 쓴다(부르는 쪽이 지운다)."""
    if seal_mode not in SEAL_MODES:
        raise RehearsalError(f"--seal-mode 는 {SEAL_MODES}")
    log = Log(verbose)
    O = log.out
    O["seal_mode"], O["gui"] = seal_mode, gui
    data = os.path.join(base, "chatcoach-data")
    projects = os.path.join(data, "projects")
    pilot_dir = os.path.join(data, "pilot")
    work = os.path.join(data, "sealed-work")
    os.makedirs(projects)
    from app import paths
    paths.set_data_root(os.path.join(base, "appdata"))
    from app import project, seal
    from app import reference_set as rs
    from tests import test_pilot_unseal_day as T
    from tools import ai_seal
    from tools import pilot_freeze as pf
    from tools import pilot_unseal_day as day
    T.FakeLock.held = set()
    date = date or datetime.now().strftime("%Y%m%d")
    attach = seal_mode == "attach"

    # ── 1. 합성 시범 3편 + 시범 기록
    t = time.perf_counter()
    pjs = []
    for i in range(1, EXPECT + 1):
        pj = T.build_pilot(projects, i, seal_results=False, sample=(i == 1))
        pj["idx"] = i
        pjs.append(pj)
    roots = [pj["root"] for pj in pjs]
    log.check("names", [os.path.basename(r) for r in roots] == [f"pilot-{pj['video_no']}" for pj in pjs])
    os.makedirs(pilot_dir)
    setup = {"schema": "chatcoach/pilot-setup", "version": 1,
             "items": [{"video_no": str(pj["video_no"]), "file_name": f"synthetic-{pj['video_no']}.mp4",
                        "start_ms": 1_200_000, "end_ms": 3_600_000, "project_root": pj["root"],
                        "project_id": pj["pid"], "created_at": "2026-10-18T08:00:00+09:00",
                        "steps": {"project": "done"}} for pj in pjs]}
    freeze = pf.build_doc(ROOT, now=datetime(2026, 10, 18, 8, 30, tzinfo=T.KST))
    freeze["projects"] = [{"project_id": pj["pid"], "slug": os.path.basename(pj["root"])} for pj in pjs]
    for name, doc in (("setup.json", setup), ("freeze.json", freeze)):
        with builtins.open(os.path.join(pilot_dir, name), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
    log.check("registered_as_pilot", all(rs.is_pilot_project(r, pilot_dir=pilot_dir) for r in roots))
    if gui:
        from tools import pilot_setup as ps
        linked = [ps.link_media(pj["root"], make_video(base, pj["video_no"]), "L01") for pj in pjs]
        log.check("gui_videos_linked", linked == ["linked"] * EXPECT, linked)
    log.step("1_create", t)

    # ── 2. 판정만 봉인(attach: 1차 잠금 전 = 사전 봉인)
    t = time.perf_counter()
    if attach:
        rows = [seal_judgements(T, pj, with_pairs=False, now=T.T_SEAL) for pj in pjs]
        log.check("judgement_only_sealed_valid", all(r == ("valid",) for rr in rows for r in rr), rows)
        log.check("no_pairs_before_attach", all(
            not any(getattr(e, f"{k}_sha256") for e in seal.seal_status(r).entries for k in seal.PAIR_KINDS)
            for r in roots))
    log.step("2_seal_judgement_only", t)

    # ── 3. 1차 잠금 → dry-run 은 조건 미충족(종료 3)
    t = time.perf_counter()
    for pj in pjs:
        T.lock_primary(pj["root"], pj["sample_id"])
    code, out = run_main(log, day.main, ["--projects-root", projects, "--json"])
    s = _last_json(out)
    log.check("dry_run_before_relabel_is_3", code == 3,
              {"code": code, "reasons": (s.get("conditions") or {}).get("reason_kinds")})
    log.step("3_primary_lock_dry_run_unmet", t)

    win = None
    if gui:
        win = _gui_window(base)
        t = time.perf_counter()
        _open_in(win, roots[0], None)
        win.update_viewer_action()
        why0, _ok0 = win.viewer_block_reason()
        log.check("gui_viewer_menu_off_before_lock", not win.a_viewer_training.isEnabled() and why0 is not None)
        win.show_home()
        log.step("3b_gui_viewer_menu_before_lock", t)

    # ── 4. 재라벨 계획·라벨·잠금
    t = time.perf_counter()
    for pj in pjs:
        T.make_plan_and_relabel(pj["root"])
    log.step("4_relabel_lock", t)

    # ── 5. 짝: attach-pairs (또는 together 면 지금 함께 봉인)
    t = time.perf_counter()
    if attach:
        att = []
        for pj in pjs:
            d = os.path.join(work, os.path.basename(pj["root"]))
            os.makedirs(d)
            files = {}
            for k, b in pair_bytes(T, pj, "attach").items():
                files[k] = os.path.join(d, f"{k}.json")
                with builtins.open(files[k], "wb") as f:
                    f.write(b)
            code, out = run_main(log, ai_seal.main, [
                "attach-pairs", "--project", pj["root"], "--all", "--by", "L01",
                "--topics", files["topics"], "--words", files["words"], "--vectors", files["vectors"],
                "--remove-source"])
            r = _last_json(out)
            att.append({"code": code, "targets": r.get("targets"), "ok": r.get("ok"),
                        "removed": r.get("source_removed"),
                        "rows": [[x.get("status") for x in res.get("rows") or ()] for res in r.get("results") or ()],
                        "presealed": [res.get("presealed") for res in r.get("results") or ()],
                        "after_lock": [res.get("after_primary_lock") for res in r.get("results") or ()]})
            shutil.rmtree(d)                             # 빈 폴더(원본은 --remove-source 가 지웠다)
        O["attach_pairs"] = att
        log.check("attach_pairs_exit_0", all(a["code"] == 0 and a["ok"] for a in att), att)
        log.check("attach_pairs_all_attached", all(
            a["targets"] == 2 and a["rows"] == [["attached"] * 3] * 2 for a in att), [a["rows"] for a in att])
        log.check("attach_keeps_presealed", all(a["presealed"] == [True, True] for a in att))
        log.check("attach_marked_after_lock", all(a["after_lock"] == [True, True] for a in att))
        log.check("attach_removed_sources", all(a["removed"] for a in att) and not os.listdir(work))
        again = [run_main(log, ai_seal.main, ["attach-pairs", "--project", pj["root"], "--all", "--by", "L01",
                                              "--from-engine"]) for pj in pjs]
        again_j = [_last_json(o) for _c, o in again]
        log.check("attach_again_present_no_engine", all(c == 0 for c, _o in again)
                  and all(j.get("missing") == [] and j.get("engine_run") is False for j in again_j),
                  [(c, j.get("missing")) for (c, _o), j in zip(again, again_j)])
        st_rows = []
        for pj in pjs:
            code, out = run_main(log, ai_seal.main, ["status", "--project", pj["root"]])
            st_rows.append(_last_json(out))
        log.check("status_pairs_all_true_attached", all(
            st.get("sealed_count") == 2 and all(e["pairs"] == {k: True for k in seal.PAIR_KINDS}
                                                 and e["pairs_attached"] == list(seal.PAIR_KINDS)
                                                 and e["presealed"] is True for e in st["entries"])
            for st in st_rows))
    else:
        rows = [seal_judgements(T, pj, with_pairs=True, now=datetime(2026, 10, 19, 12, 0, tzinfo=T.KST))
                for pj in pjs]
        log.check("sealed_with_pairs_valid", all(r == ("valid",) * 4 for rr in rows for r in rr), rows)
    blind = []
    for pj in pjs:
        for fn in (seal.read_run, seal.read_topics, seal.read_words, seal.read_vectors):
            try:
                fn(pj["root"], f"run-v03-{pj['idx']}")
                blind.append("opened")
            except seal.SealNotUnsealedError:
                blind.append("blind")
    log.check("readers_blind_before_unseal", set(blind) == {"blind"}, len(blind))
    log.step("5_pairs", t)

    # ── 6. 해제 당일: dry-run → --go
    t = time.perf_counter()
    reports = os.path.join(data, "reports")
    code, out = run_main(log, day.main, ["--projects-root", projects, "--json"])
    s = _last_json(out)
    cond = s.get("conditions") or {}
    log.check("dry_run_met_0", code == 0, {"code": code, "reasons": cond.get("reason_kinds")})
    log.check("dry_run_watch_clean", (s.get("watch") or {}).get("violations") == 0
              and (s.get("watch") or {}).get("writes") == 0, s.get("watch"))
    log.check("dry_run_no_reports_no_unseal", not os.path.exists(reports) and all(
        not any(e.unsealed_at for e in seal.seal_status(r).entries) for r in roots))
    log.step("6a_dry_run", t)
    t = time.perf_counter()
    before = snapshot(roots, base)
    code, out = run_main(log, day.main, ["--projects-root", projects, "--go", "--date", date, "--json"])
    after = snapshot(roots, base)
    s = _last_json(out)
    projs = s.get("projects") or []
    O["unseal_day"] = {"code": code, "elapsed_s": s.get("elapsed_s"), "user_clicks": s.get("user_clicks"),
                       "watch_run": s.get("watch_run"), "projects": [
                           {"name": p.get("name"), "errors": p.get("errors"),
                            "unsealed_now": (p.get("unseal") or {}).get("unsealed_now"),
                            "failed": (p.get("unseal") or {}).get("failed"),
                            "presealed": (p.get("unseal") or {}).get("presealed"),
                            "pairs": [(p.get("unseal") or {}).get(k) for k in seal.PAIR_KINDS],
                            "pair_errors": (p.get("unseal") or {}).get("pair_errors"),
                            "verify_ok": ((p.get("unseal") or {}).get("verify") or {}).get("ok"),
                            "verify_codes": ((p.get("unseal") or {}).get("verify") or {}).get("codes"),
                            "reports": len((p.get("compare") or {}).get("runs") or ()),
                            "engine_pairs": len((p.get("compare") or {}).get("pairs") or ()),
                            "calib": bool(p.get("calib"))} for p in projs]}
    U = O["unseal_day"]["projects"]
    log.check("unseal_day_exit_0", code == 0 and len(U) == EXPECT and all(not p["errors"] for p in U),
              {"code": code, "errors": [p["errors"] for p in U], "batch": s.get("errors")})
    log.check("unsealed_2_failed_0", all(p["unsealed_now"] == 2 and p["failed"] == [] for p in U))
    log.check("pairs_three_each_2_no_error", all(p["pairs"] == [2, 2, 2] and p["pair_errors"] == [] for p in U),
              [(p["pairs"], p["pair_errors"]) for p in U])
    log.check("verify_all_pairs_ok", all(p["verify_ok"] and p["verify_codes"] == {
        f"{k}:ok": 2 for k in ("run",) + tuple(seal.PAIR_KINDS)} for p in U), [p["verify_codes"] for p in U])
    log.check("presealed_judgements", all(p["presealed"] == (2 if attach else 0) for p in U),
              [p["presealed"] for p in U])
    log.check("reports_pairs_calib", all(p["reports"] == 2 and p["engine_pairs"] == 1 and p["calib"] for p in U))
    log.check("watch_run_clean", (s.get("watch_run") or {}).get("violations") == 0, s.get("watch_run"))
    log.check("pilot_folders_unchanged", before == after, len(set(before) ^ set(after)))
    log.step("6b_unseal_day_go", t)

    # ── 7. 편마다 ai_seal verify
    t = time.perf_counter()
    ver = []
    for r in roots:
        code, out = run_main(log, ai_seal.main, ["verify", "--project", r])
        j = _last_json(out)
        ver.append((code, j.get("ok"), sorted({(x.get("run"), x.get("topics"), x.get("words"), x.get("vectors"))
                                               for x in j.get("runs") or ()}), len(j.get("runs") or ())))
    O["verify"] = [{"code": c, "ok": ok, "runs": n, "values": v} for c, ok, v, n in ver]
    log.check("ai_seal_verify_ok", all(c == 0 and ok and n == 2 and v == [("ok", "ok", "ok", "ok")]
                                       for c, ok, v, n in ver), O["verify"])
    log.step("7_verify", t)

    # ── 8. 출력에 표지 없음
    out_dir = os.path.join(reports, f"{day.DAY_PREFIX}{date}")
    files = sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []
    log.check("summary_files", {"summary.md", "summary.json", "calib-pooled.json"} <= set(files), files)
    blob = "".join(log.captured).encode("utf-8")
    for dp, _dn, fn in os.walk(out_dir):
        for n in fn:
            with builtins.open(os.path.join(dp, n), "rb") as f:
                blob += f.read()
    leak = [x for x in (T.SECRET_TEXT, T.SECRET_NICK, T.SECRET_WORD) if x.encode("utf-8") in blob]
    log.check("no_text_in_outputs", not leak, leak)

    if gui:
        _gui_checks(log, win, pjs, data)

    O["user_clicks"] = USER_CLICKS
    O["total_s"] = round(time.perf_counter() - log.t0, 2)
    O["failed_checks"] = log.failed
    return O


# ───────────────────────────────────────────────────────────── --gui

def _gui_window(base: str):
    from PySide6.QtWidgets import QApplication
    from app import updater
    from app.appspec import SPEC
    from app.ui import mainwindow as mw
    from app.ui import workbench as wbm
    from tests.test_integration_c4 import PARTS
    QApplication.instance() or QApplication(sys.argv[:1])
    wbm._default_part = lambda kind: (PARTS[kind], None)
    inst = updater.InstalledInfo(version="0.0.0", channel="source", built_at=None, app_dir=base, code_dir=base,
                                 frozen=False)
    win = mw.MainWindow(SPEC, inst, os.path.join(base, "appdata"), os.path.join(base, "log.txt"), None, [], set())
    win.check_updates = lambda force=False, on_start=False: None
    win._shown = []
    win.inform = lambda title, text: win._shown.append("info")
    win.warn = lambda title, text: win._shown.append("warn")
    return win


def _open_in(win, root: str, session) -> None:
    win.session_chooser = lambda outcome: session
    o = win.project_view.open_path(root)
    if not (o.ok and win.workbench.wait_jobs(15_000)):
        raise RuntimeError("프로젝트를 열지 못했습니다")
    win.update_session_actions()
    win.update_analysis_actions()


def _gui_checks(log: Log, win, pjs, data: str) -> None:
    from app import project
    from app import viewer_flow as vf
    from tests.test_integration_c4 import ctx
    t = time.perf_counter()
    r0, pj0 = pjs[0]["root"], pjs[0]
    ai_dir = os.path.join(data, "ai", pj0["pid"])
    win.show_home()
    _open_in(win, r0, ctx("L01-AN-20261023-aa11", "analysis", "L01"))
    with Spy() as sp:
        dlg = win.side_by_side_view(show=False)
    if log.check("gui_side_by_side_opened", dlg is not None):
        log.out["gui_side_by_side"] = {"run": dlg.view.current_run_id(), "runs_reads": sp.count(ai_dir, "runs"),
                                       "sealed_reads": sp.count(ai_dir, "sealed")}
        log.check("gui_side_by_side_default_v03", dlg.view.current_run_id() == f"run-v03-{pj0['idx']}",
                  dlg.view.current_run_id())
        log.check("gui_side_by_side_reads_runs_not_sealed",
                  sp.count(ai_dir, "runs") > 0 and sp.count(ai_dir, "sealed") == 0)
    with Spy() as sp:
        sdlg = win.broadcast_summary_view(show=False)
    if log.check("gui_summary_opened", sdlg is not None):
        log.check("gui_summary_reads_topics_run", sp.count(ai_dir, "topics") > 0 and sp.count(ai_dir, "sealed") == 0
                  and sdlg.view.row_count() == 2, sdlg.view.row_count())
    win.show_home()
    _open_in(win, r0, None)
    why, ok = win.viewer_block_reason()
    win.update_viewer_action()
    if log.check("gui_viewer_menu_enabled", win.a_viewer_training.isEnabled() and ok, why):
        built = []
        win.words_runner = lambda c, cancelled: built.append(1) or {}
        with Spy() as sp:
            win.a_viewer_training.trigger()
        view = win.viewer_view
        if log.check("gui_viewer_opened", view is not None):
            c = view._context
            log.out["gui_viewer"] = {"transcript_windows": len(c.get("transcript") or ()),
                                     "topic_blocks": len(c.get("topic_blocks") or ()),
                                     "engine_built": len(built), "derived_asr_reads": sp.count(r0, "derived", "asr"),
                                     "words_reads": sp.count(ai_dir, "words")}
            log.check("gui_viewer_uses_run_words", bool(c.get("transcript")) and sp.count(ai_dir, "words") > 0
                      and sp.count(r0, "derived", "asr") == 0 and not built)
            win.close_viewer_training()
    win.show_home()
    win.close()
    _ctx, info = vf.build_context(r0, project.open_project(r0).data)
    log.check("gui_vectors_from_run", info.get("vector_windows", 0) > 0, info.get("vectors"))
    log.step("9_gui", t)


# ───────────────────────────────────────────────────────────── 명령줄

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="합성 시범 3편 해제 당일 리허설(사람 클릭 0). 자리는 %LOCALAPPDATA%\\ChatCoachSmoke 아래")
    ap.add_argument("--seal-mode", choices=SEAL_MODES, default="attach",
                    help="attach = 판정만 봉인 → 잠금 → 짝 보충(기본, D-23 3) · together = 판정과 짝을 함께(지난 묶음)")
    ap.add_argument("--gui", action="store_true", help="화면 확인도(offscreen 권장, 합성 영상은 로컬 ffmpeg)")
    ap.add_argument("--keep", action="store_true", help="끝나도 리허설 자리를 지우지 않음(합성 자료뿐)")
    ap.add_argument("--base", help="리허설 자리(기본 %%LOCALAPPDATA%%\\ChatCoachSmoke\\rehearsal-<시각>-<pid>)")
    ap.add_argument("--date", help="요약 폴더 날짜 YYYYMMDD(기본 오늘)")
    ap.add_argument("--json", action="store_true", help="기록 전체를 JSON 으로(기본은 PASS/FAIL 줄과 요약 한 줄)")
    a = ap.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        base = check_base(a.base or default_base())
    except RehearsalError as e:
        print(f"입력 오류: {e}", file=sys.stderr)
        return 2
    real_lad = os.environ.get("LOCALAPPDATA")
    os.makedirs(os.path.join(base, "localappdata"))
    os.environ["LOCALAPPDATA"] = os.path.join(base, "localappdata")   # 백업·폴백이 실제 %LOCALAPPDATA%\ChatCoach 로 가지 않게
    from app import paths
    rec: dict = {}
    try:
        rec = rehearse(base, seal_mode=a.seal_mode, gui=a.gui, verbose=not a.json, date=a.date)
    except Exception as e:  # noqa: BLE001 — 끝 정리는 늘 한다
        import traceback
        rec = {"error": type(e).__name__, "failed_checks": ["exception"]}
        # 틀(파일·줄)만 — 합성 자료라도 예외 메시지는 찍지 않는다
        frames = traceback.extract_tb(e.__traceback__)[-6:]
        print(f"리허설 오류: {type(e).__name__} · " + " ← ".join(
            f"{os.path.basename(f.filename)}:{f.lineno}" for f in reversed(frames)), file=sys.stderr)
    finally:
        paths.set_data_root(None)
        if real_lad is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = real_lad
        if not a.keep:
            shutil.rmtree(base, ignore_errors=False)
    rec["base"] = "…\\" + SMOKE_DIR + "\\…\\" + os.path.basename(base)
    rec["removed"] = not os.path.exists(base)
    if a.json:
        print(json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str))
    else:
        n = len(rec.get("checks") or {})
        failed = rec.get("failed_checks") or []
        print(f"리허설({rec.get('seal_mode')}{' +gui' if rec.get('gui') else ''}): 검사 {n}개 · 실패 {len(failed)}"
              f"{' (' + ', '.join(failed) + ')' if failed else ''} · {rec.get('total_s')}초 · 사람 클릭 {USER_CLICKS}"
              f" · 자리 지움 {rec['removed']}")
        print("단계(초): " + " · ".join(f"{k} {v}" for k, v in (rec.get("steps") or {}).items()))
    return 0 if not rec.get("failed_checks") else 1


__all__ = ["rehearse", "main", "check_base", "default_base", "RehearsalError", "USER_CLICKS", "SEAL_MODES"]

if __name__ == "__main__":
    sys.exit(main())
