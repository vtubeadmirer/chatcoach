"""가상 채팅 시나리오 만들기 — 첫 판은 'retrieved' 만 (docs/design/20-plan-virtual-chat.md H-2, D-19 2).

사용:
  python tools/make_scenario.py --project <프로젝트 폴더> --from <시각> --to <시각>
         [--projects-root <폴더>] [--blocklist <txt>] [--topic-flow <topic-flow.json>] [--seed N] [--dry-run]
         [--threshold Q/T | test | default] [--quiet-max-real 2] [--target-per-min 6] [--max-virtual-ratio 0.5]

  --from/--to : 훈련 구간(VOD 시각). 1:02:03(.5) 형식 또는 정수 ms.
  --projects-root : 같은 채널(project.json broadcast.channel_id) 프로젝트를 찾을 곳. 기본은 프로젝트 폴더의 부모.
                    channel_id 가 없으면 같은 방송만 쓴다. pilot-* 폴더는 열지 않는다.
  --blocklist : 금칙어 목록(UTF-8, 한 줄에 하나, '#' 주석). 기본 <데이터 폴더>\\config\\blocklist-ko.txt (20 11-6, D-20 6).
                있으면 그 낱말이 든 줄을 빼고 시나리오 머리 filters.blocklist_sha256 에 파일 해시만 적는다(낱말은 어디에도
                적지 않는다). 없으면 금칙어 거르기 0낱말로 진행하고 경고만 낸다(출력 blocklist_found=false,
                warnings=["blocklist_missing"], 표준 오류에 한 줄). 있는데 읽지 못하면 만들지 않는다(bad_blocklist, rc 1).
                빈 값(--blocklist "")은 '목록 없이' — 시험용.
  --threshold : 채우기 문턱 한 번에. 'Q/T' = --quiet-max-real Q --target-per-min T (예: 45/60). 'test' = 45/60
                (시험 문턱, 21 보고 1-3 · gui_smoke), 'default' = 2/6(기본). --quiet-max-real·--target-per-min 과 같이 쓰면
                bad_argument.
  --seed : 0 이상 정수. 같은 입력·seed 면 같은 줄.
  --dry-run : 파일을 쓰지 않고 숫자만 낸다.

잇는 것: engine.retrieve(고르기, 표준 라이브러리만) → app.core.scenario.assemble(줄 목록·ID·끝 줄) →
  app.scenario_store.write_scenario(project_root, records)(검사 + scenarios\\<scenario_id>.jsonl 원자적 쓰기).
  같은 프로젝트·구간·lines_sha256 인 시나리오가 이미 있으면 write_scenario 가 새로 쓰지 않고 그 ID 를 준다.
  채울 줄이 0개면 쓰지 않는다(nothing_to_fill).
시범 프로젝트(pilot-*)는 읽기 전에 거부한다(pilot_locked). 이름이 달라도 시범 기록(freeze.json projects[]·setup.json
items[])에 적힌 프로젝트는 app.reference_set 으로 거른다 — 대상이면 거부, 같은 채널 후보면 열지 않는다(stats.pilot_skipped).
AI·외부 호출·모델 없음. messages.jsonl 은 읽기만.
출력: 표준 출력에 JSON 한 줄(숫자·ID·코드만. 본문·닉네임 없음).
종료 코드: 0 정상(이미 있음·채울 것 없음 포함) · 1 입력 오류(금칙어 파일 깨짐 포함) · 2 시범 거부 · 3 scenario_store 없음 ·
  4 만들 수 없음
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import json
import os
import re
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import retrieve as R  # noqa: E402  (표준 라이브러리만 쓰는 모듈)


def _store():
    """(assemble, write_scenario, 오류 클래스들) 또는 None. H-1 담당 모듈(app.core.scenario, app.scenario_store)."""
    try:
        from app.core import scenario as sc
        from app import scenario_store as st
    except ImportError:
        return None
    return sc.assemble, st.write_scenario, st


def _pilot_checks():
    """(대상 막힘 까닭 함수, 같은 채널 후보 제외 함수) — app.reference_set 의 시범 판단(폴더 이름 + 시범 기록
    freeze.json projects[]·setup.json items[]). 엔진은 이름(pilot-*)만 보므로 이름이 다른 시범 프로젝트는 여기서 막는다.
    대상은 시범 세션이 모두 잠겼으면 열어도 되고(pilot_block_reason, write_scenario 와 같은 판단), 같은 채널 후보는
    시범이면 잠김과 상관없이 열지 않는다(원 채팅·원 닉네임을 읽게 되므로). 모듈이 없으면 None."""
    try:
        from app import reference_set as rs
    except ImportError:
        return None
    return rs.pilot_block_reason, rs.is_pilot_project


_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?$")


def parse_ms(text: str) -> int:
    s = str(text).strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    m = _TIME.match(s)
    if not m:
        raise ValueError("시각 형식은 1:02:03(.5) 또는 정수 ms")
    h, mi, se, fr = m.group(1), m.group(2), m.group(3), m.group(4)
    ms = ((int(h or 0) * 60 + int(mi)) * 60 + int(se)) * 1000
    if fr:
        ms += int(fr.ljust(3, "0"))
    return ms


THRESHOLD_PRESETS = {"test": (45, 60), "default": (R.RetrieveConfig().quiet_max_real, R.RetrieveConfig().target_per_min)}


def parse_threshold(text: str) -> tuple[int, int]:
    """'Q/T' 또는 'test'·'default' → (quiet_max_real, target_per_min). 둘 다 0 이상 정수."""
    s = str(text).strip().lower()
    if s in THRESHOLD_PRESETS:
        return THRESHOLD_PRESETS[s]
    m = re.fullmatch(r"(\d{1,4})\s*/\s*(\d{1,4})", s)
    if not m:
        raise ValueError("문턱 형식은 Q/T (예: 45/60) 또는 test·default")
    return int(m.group(1)), int(m.group(2))


def default_blocklist(project_dir: str) -> str:
    """<데이터 폴더>\\config\\blocklist-ko.txt. 데이터 폴더 = projects 폴더의 부모(…\\chatcoach-data)."""
    projects = os.path.dirname(os.path.abspath(project_dir))
    return os.path.join(os.path.dirname(projects), "config", "blocklist-ko.txt")


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    d = R.RetrieveConfig()
    ap = argparse.ArgumentParser(description="가상 채팅 시나리오(retrieved) 만들기")
    ap.add_argument("--project", required=True)
    ap.add_argument("--from", dest="from_t", required=True)
    ap.add_argument("--to", dest="to_t", required=True)
    ap.add_argument("--projects-root")
    ap.add_argument("--blocklist")
    ap.add_argument("--topic-flow")
    ap.add_argument("--seed", default="0")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threshold", help="채우기 문턱 'Q/T'(예: 45/60) · test(45/60) · default(2/6)")
    ap.add_argument("--quiet-max-real", type=int, default=None,
                    help=f"이 수 이하인 1분 창을 채운다(기본 {d.quiet_max_real})")
    ap.add_argument("--target-per-min", type=int, default=None, help=f"채운 창의 실제+가상 목표(기본 {d.target_per_min})")
    ap.add_argument("--max-virtual-ratio", type=float, default=d.max_virtual_ratio)
    ns = ap.parse_args(argv)

    project = os.path.abspath(ns.project)
    if R.is_pilot_dir(project):                    # 읽기 전에 거부
        _emit({"error": "pilot_locked"})
        return 2
    try:
        a, b = parse_ms(ns.from_t), parse_ms(ns.to_t)
        seed = int(ns.seed)
        if seed < 0:
            raise ValueError
        qmr = d.quiet_max_real if ns.quiet_max_real is None else ns.quiet_max_real
        tpm = d.target_per_min if ns.target_per_min is None else ns.target_per_min
        if ns.threshold is not None:
            if ns.quiet_max_real is not None or ns.target_per_min is not None:
                raise ValueError("--threshold 와 --quiet-max-real·--target-per-min 은 같이 쓰지 않는다")
            qmr, tpm = parse_threshold(ns.threshold)
        cfg = R.RetrieveConfig(quiet_max_real=qmr, target_per_min=tpm, max_virtual_ratio=ns.max_virtual_ratio)
    except ValueError:
        _emit({"error": "bad_argument"})
        return 1
    if not os.path.isdir(project):
        _emit({"error": "no_project"})
        return 1
    store = _store()
    if store is None:
        _emit({"error": "scenario_store_missing"})
        return 3
    assemble, write_scenario, st = store
    checks = _pilot_checks()
    if checks is None:
        _emit({"error": "scenario_store_missing"})
        return 3
    block_reason, is_pilot = checks
    try:
        blocked = block_reason(project) is not None
    except Exception:  # noqa: BLE001 — 판단이 안 되면 막는다(블라인드 쪽으로 안전하게)
        blocked = True
    if blocked:                                     # 시범 기록에 적힌 프로젝트도 읽기 전에 거부
        _emit({"error": "pilot_locked"})
        return 2
    bl = ns.blocklist if ns.blocklist is not None else default_blocklist(project)
    try:
        header, chats, stats = R.make_retrieved_scenario(project, a, b, projects_root=ns.projects_root,
                                                         blocklist_path=bl, topic_flow_path=ns.topic_flow,
                                                         seed=seed, cfg=cfg, now=now, exclude=is_pilot)
    except R.RetrieveError as e:
        _emit({"error": e.code})
        return 2 if e.code == "pilot_locked" else (
            1 if e.code in ("bad_range", "no_messages", "bad_seed", "bad_blocklist") else 4)
    when = now or datetime.now().astimezone()
    records = assemble(header, chats, yyyymmdd=when.strftime("%Y%m%d"))
    head = records[0]
    found = "blocklist_sha256" in head.get("filters", {})
    out = {"scenario_id": head["scenario_id"], "counts": head["counts"], "stats": stats,
           "blocklist_found": found, "written": False,
           "fill": {"quiet_max_real": cfg.quiet_max_real, "target_per_min": cfg.target_per_min}}
    if not found and bl:
        # 지금처럼 경고만 하고 진행한다(D-20 6). 경로·낱말은 적지 않는다(코드만)
        out["warnings"] = ["blocklist_missing"]
        print("경고: 금칙어 목록 파일이 없어 금칙어 거르기 없이 만듭니다 (blocklist_missing)", file=sys.stderr)
    if ns.dry_run:
        problems = st.validate(records)
        out["valid"] = not problems
        out["problems"] = len(problems)
        _emit(out)
        return 0
    if not head["counts"].get("retrieved"):          # 채울 드문 창이 없거나 후보가 없다: 빈 시나리오는 쓰지 않는다
        out["reason"] = "nothing_to_fill"
        _emit(out)
        return 0
    try:
        res = write_scenario(project, records)
    except st.ScenarioBlockedError:
        _emit({"error": "pilot_locked"})
        return 2
    except st.ScenarioInvalidError as e:
        _emit({"error": "invalid", "problems": len(getattr(e, "problems", ()) or ())})
        return 4
    except (st.ScenarioStoreError, OSError):
        _emit({"error": "store_error"})
        return 4
    out.update({"scenario_id": res.scenario_id, "written": bool(res.created)})
    if not res.created:
        out["existing"] = res.scenario_id
    _emit(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
