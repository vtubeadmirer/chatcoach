"""가상 채팅 가려내기 대표값 — 여러 세션 기록을 합쳐 H-5 관문(통과·실패)을 낸다 (20 7-3, 25 6-2, 08 D-24 7 가).

가려내기 화면(app.ui.discern_view)은 세션 하나만 계산해 늘 '방송 1편 = 표본 적음' 이다. 관문의 '방송 2편 이상' 은
여기서 프로젝트(= 방송)마다의 `discern\\<session_id>.jsonl` 을 다시 읽어 `app.core.discern.evaluate_sessions` 로 낸다.
명령줄 껍데기는 tools/discern_report.py (Claude 가 돌린다, 사람 클릭 0).

세션 하나를 다시 만드는 법(기록에는 정답이 없다 — 03 6-19):
| 칸 | 어디서 |
|---|---|
| 판정 | judge 줄(seq 순 마지막, none 은 지움 — core.discern.answers_from_events) |
| 대상·정답 | 판정 줄의 키. chat_id → replay, '<scenario_id>:<line_id>' → start 줄이 가리킨 시나리오 파일의 그 줄 source |
| 방송 | start 줄의 project_id |
| 다시 봄·본 적 있음 | start 줄의 repeat·seen_before 그대로 (다시 봄은 evaluate_sessions 가 뺀다) |

- 판정하지 않은 대상('안 봄')은 혼동표에 넣지 않으므로(20 7-1) 판정 줄만으로 숫자가 화면과 같다. 대상 수는 start 줄의
  shown 합으로 따로 센다.
- 시나리오 파일이 없거나 깨졌거나 바이트 해시가 start 줄의 scenario_sha256 과 다르면 그 세션은 빼고 사유 코드만 센다.
- 시범 프로젝트(pilot-*)는 기본으로 **폴더를 열지 않고** 건너뛴다. allow_locked_pilot 이면 시범 세션이 모두 잠긴 것만
  (reference_set.pilot_block_reason) 넣는다. 폴더 이름이 pilot-* 가 아니어도 시범 기록에 적힌 프로젝트는 잠기기 전 뺀다.
- 결과는 숫자·ID·사유 코드만(본문·닉네임·키 없음, D-17 8). 쓰지 않는다(읽기만).
"""
from __future__ import annotations

import os
from typing import Iterable

from . import discern_store as ds
from .core import discern as dc

PROJECT_JSON = "project.json"

# 세션을 빼는 사유 코드
S_NO_START = "no_start"
S_OTHER_LABELER = "other_labeler"
S_SCENARIO_UNREADABLE = "scenario_unreadable"
S_SCENARIO_CHANGED = "scenario_changed"
S_UNREADABLE = "unreadable"
SKIP_CODES = (S_NO_START, S_OTHER_LABELER, S_SCENARIO_UNREADABLE, S_SCENARIO_CHANGED, S_UNREADABLE)

# 보고의 notes 코드(문장 대신)
N_HUMAN_EMPTY = "human_bucket_empty"
N_LOW_N = "low_n"
N_ONE_SIDED = "one_sided"
N_SEEN = "seen_before"


def _is_pilot_name(root: str) -> bool:
    from . import reference_set as rs
    return os.path.basename(os.path.normpath(str(root))).lower().startswith(rs.PILOT_PREFIX)


def project_dirs(projects_root: str) -> list[str]:
    """projects_root 바로 아래의 프로젝트 폴더(project.json 이 있는 곳, 이름 순). 시범 이름(pilot-*)도 목록에는 넣는다 —
    열지 않고 건너뛰는 것은 collect 가 한다(project.json 도 보지 않는다)."""
    try:
        names = sorted(os.listdir(projects_root))
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(projects_root, n)
        if not os.path.isdir(p):
            continue
        if _is_pilot_name(p) or os.path.isfile(os.path.join(p, PROJECT_JSON)):
            out.append(p)
    return out


def _source_map(project_root: str, scenario_id: str, sha256: str) -> tuple[dict | None, str | None]:
    """시나리오 줄 line_id → source. (지도, None) 또는 (None, 사유 코드)."""
    from . import scenario_store as sst
    try:
        scn = sst.load_scenario(project_root, scenario_id, check_pilot=False)   # 시범 판단은 collect 가 먼저
    except (sst.ScenarioStoreError, OSError, ValueError):
        return None, S_SCENARIO_UNREADABLE
    if scn.sha256 != sha256:
        return None, S_SCENARIO_CHANGED
    out = {}
    for r in scn.chats:
        if isinstance(r, dict) and r.get("kind") == "scenario.chat" and r.get("source") in dc.SOURCES:
            out[r.get("line_id")] = r["source"]
    return out, None


def session_input(project_root: str, session_id: str, *, labeler_id: str | None = None) -> tuple[dict | None, dict]:
    """세션 하나 → (evaluate_sessions 의 한 칸 또는 None, 숫자·코드 요약).

    요약: {session_id, project_id, scenario_id, labeler_id, judged, shown, unknown_keys, repeat, seen_before, ended,
    skip(사유 코드 또는 None)}."""
    info = {"session_id": session_id, "project_id": None, "scenario_id": None, "labeler_id": None, "judged": 0,
            "shown": 0, "unknown_keys": 0, "repeat": False, "seen_before": None, "ended": False, "skip": None}
    try:
        recs, _sum = ds.read_valid(ds.session_path(project_root, session_id))
    except (ds.DiscernStoreError, OSError, ValueError):
        info["skip"] = S_UNREADABLE
        return None, info
    recs = sorted(recs, key=lambda r: r.get("seq", 0))
    starts = [r for r in recs if r.get("kind") == ds.K_START]
    if not starts:
        info["skip"] = S_NO_START
        return None, info
    st = starts[0]
    body = st.get("body") or {}
    info.update(project_id=body.get("project_id"), scenario_id=body.get("scenario_id"),
                labeler_id=st.get("labeler_id"), repeat=bool(body.get("repeat")),
                seen_before=body.get("seen_before"),
                shown=sum(int(v) for v in (body.get("shown") or {}).values() if isinstance(v, int)),
                ended=any(r.get("kind") == ds.K_END for r in recs))
    if labeler_id is not None and st.get("labeler_id") != labeler_id:
        info["skip"] = S_OTHER_LABELER
        return None, info
    smap, why = _source_map(project_root, body.get("scenario_id"), body.get("scenario_sha256"))
    if smap is None:
        info["skip"] = why
        return None, info
    answers = dc.answers_from_events(r for r in recs if r.get("kind") == ds.K_JUDGE
                                     and r.get("session_id") == session_id)
    targets, ans = [], {}
    for key, a in answers.items():
        try:
            k = dc.parse_key(key)
        except dc.DiscernError:
            info["unknown_keys"] += 1
            continue
        if "chat_id" in k:
            src = dc.SRC_REPLAY
        elif k["scenario_id"] == body.get("scenario_id") and k["line_id"] in smap:
            src = smap[k["line_id"]]
        else:
            info["unknown_keys"] += 1
            continue
        targets.append(dc.Target(key, src))
        ans[key] = a
    info["judged"] = len(ans)
    return ({"targets": targets, "answers": ans, "broadcast": body.get("project_id"), "repeat": info["repeat"],
             "seen_before": info["seen_before"]}, info)


def collect(project_roots: Iterable[str], *, labeler_id: str | None = None,
            allow_locked_pilot: bool = False) -> dict:
    """여러 프로젝트의 가려내기 세션 → {"inputs": [...], "sessions": [요약...], "projects": n, "pilot_skipped": n}."""
    from . import reference_set as rs
    inputs, sessions = [], []
    n_proj = n_pilot = 0
    for root in project_roots:
        root = os.path.abspath(str(root))
        if _is_pilot_name(root) and not allow_locked_pilot:
            n_pilot += 1                       # 열지 않는다(project.json·discern\\ 도)
            continue
        try:
            blocked = rs.pilot_block_reason(root)
        except Exception:  # noqa: BLE001 — 판단 못 하면 막는 쪽
            blocked = "unknown"
        if blocked:
            n_pilot += 1
            continue
        n_proj += 1
        for sid in ds.list_sessions(root):
            inp, info = session_input(root, sid, labeler_id=labeler_id)
            sessions.append(info)
            if inp is not None:
                inputs.append(inp)
    return {"inputs": inputs, "sessions": sessions, "projects": n_proj, "pilot_skipped": n_pilot}


def _note_codes(res: dc.DiscernResult) -> list[str]:
    out = []
    if dc.NOTE_HUMAN_EMPTY in res.notes:
        out.append(N_HUMAN_EMPTY)
    if not res.sample.get("ok"):
        out.append(N_LOW_N)
    if dc.NOTE_ONE_SIDED.format(dc.GATE_MAX_UNSURE) in res.notes:
        out.append(N_ONE_SIDED)
    if res.seen_before:
        out.append(N_SEEN)
    return out


def report(project_roots: Iterable[str], *, labeler_id: str | None = None, allow_locked_pilot: bool = False,
           with_sessions: bool = False) -> dict:
    """대표값 보고(숫자·ID·코드만). gate = pass · fail · low_n · unknown."""
    got = collect(project_roots, labeler_id=labeler_id, allow_locked_pilot=allow_locked_pilot)
    res = dc.evaluate_sessions(got["inputs"])
    ss = got["sessions"]
    skipped = {c: sum(1 for s in ss if s["skip"] == c) for c in SKIP_CODES}
    used = [s for s in ss if s["skip"] is None]
    out = {
        "projects": got["projects"], "pilot_skipped": got["pilot_skipped"], "labeler_id": labeler_id,
        "sessions": {"found": len(ss), "used": sum(1 for s in used if not s["repeat"]),
                     "repeat_excluded": sum(1 for s in used if s["repeat"]),
                     "unended": sum(1 for s in used if not s["ended"]), "skipped": skipped},
        "shown": sum(s["shown"] for s in used if not s["repeat"]),
        "unknown_keys": sum(s["unknown_keys"] for s in used),
        "seen_before": res.seen_before,
        "result": res.numbers(), "gate": res.gate["status"], "pass": res.gate["pass"],
        "p_o": res.p_o, "p_e": res.p_e, "notes": _note_codes(res),
        "thresholds": {"min_generated": dc.GATE_MIN_GENERATED, "min_real": dc.GATE_MIN_REAL,
                       "min_broadcasts": dc.GATE_MIN_BROADCASTS, "max_detection": dc.GATE_MAX_DETECTION,
                       "max_kappa": dc.GATE_MAX_KAPPA, "max_unsure": dc.GATE_MAX_UNSURE},
    }
    if with_sessions:
        out["by_session"] = [{k: s[k] for k in ("session_id", "project_id", "scenario_id", "labeler_id", "judged",
                                                "repeat", "ended", "skip")} for s in ss]
    return out


__all__ = ["project_dirs", "session_input", "collect", "report", "SKIP_CODES", "S_NO_START", "S_OTHER_LABELER",
           "S_SCENARIO_UNREADABLE", "S_SCENARIO_CHANGED", "S_UNREADABLE"]
