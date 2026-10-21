"""시범 시작 때 라벨 형식·기준서를 고정한 기록을 남긴다 (08 D-14 7, 07 3-5 '시범용 기준서 동결').

기록하는 것 (<데이터>\\pilot\\freeze.json, 한 번만 만든다 — 이미 있으면 거부)
  - schemas/label-event.schema.json 의 sha256 · $id · 봉투 v(app.store.RECORD_V)
  - docs/labeling-guide/v0.1.md 의 sha256 (= project.json guides[].doc_sha256 과 같은 값)
  - assets/guide-config.v0.1.json 파일 sha256 과, 그 values 의 정규 JSON sha256 (= project.json guides[].thresholds_sha256)
  - 형식 고정 골든 tests/fixtures/frozen_labels_v0_1.jsonl 의 sha256
  - 앱 버전(VERSION) · git HEAD · 위 파일들이 커밋되지 않은 변경을 가졌는지
시범 중에는 --check 로 지금 파일이 고정 기록과 같은지 본다. 다르면 무엇이 바뀌었는지 이름만 찍고 종료 코드 1.

사용
  python tools/pilot_freeze.py [--out <freeze.json>] [--dry-run] [--allow-dirty]
  python tools/pilot_freeze.py --check [--out <freeze.json>]

고정 대상 파일에 커밋되지 않은 변경이 있으면 기본으로 거부한다(--allow-dirty 로 기록은 하되 dirty 를 남김).
이 도구는 저장소 파일을 고치지 않는다. 서명 키·암호 파일은 읽지 않는다.
종료 코드: 0 정상 · 1 거부(이미 있음·다름·dirty) · 2 입력 오류(파일 없음·형식 오류)
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core.validate import StrictJsonError, canonical_json, load_json_strict  # noqa: E402

FREEZE_SCHEMA = "chatcoach/pilot-freeze"
FREEZE_VERSION = 1
GUIDE_VERSION = "0.1"
DEFAULT_OUT = os.path.join(os.path.dirname(ROOT), "chatcoach-data", "pilot", "freeze.json")

# 이름 → 저장소 안 상대 경로 (/ 구분)
TARGETS = {
    "label_event_schema": "schemas/label-event.schema.json",
    "guide_doc": f"docs/labeling-guide/v{GUIDE_VERSION}.md",
    "guide_config": f"assets/guide-config.v{GUIDE_VERSION}.json",
    "label_golden": "tests/fixtures/frozen_labels_v0_1.jsonl",
}
MAX_JSON_BYTES = 4 * 1024 * 1024


class FreezeError(RuntimeError):
    pass


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(repo: str, rel: str) -> bytes:
    p = os.path.join(repo, *rel.split("/"))
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError as e:
        raise FreezeError(f"파일을 읽지 못했습니다: {rel} ({type(e).__name__})") from None


def _git(repo: str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, encoding="utf-8", errors="replace",
                           timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def git_state(repo: str) -> tuple[str | None, list[str] | None]:
    """(HEAD 커밋, 고정 대상 중 커밋되지 않은 변경이 있는 이름). git 이 없거나 저장소가 아니면 (None, None)."""
    head = _git(repo, "rev-parse", "HEAD")
    if head is None:
        return None, None
    out = _git(repo, "status", "--porcelain", "--", *TARGETS.values())
    if out is None:
        return head.strip(), None
    dirty_paths = {ln[3:].strip().strip('"') for ln in out.splitlines() if len(ln) > 3}
    return head.strip(), sorted(k for k, rel in TARGETS.items() if rel in dirty_paths)


def collect(repo: str) -> dict:
    """고정할 값을 모은다. 시각·git 은 넣지 않는다(비교용 본체)."""
    files = {}
    for name, rel in TARGETS.items():
        data = _read(repo, rel)
        files[name] = {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    try:
        schema = load_json_strict(_read(repo, TARGETS["label_event_schema"]), MAX_JSON_BYTES)
        cfg = load_json_strict(_read(repo, TARGETS["guide_config"]), MAX_JSON_BYTES)
    except StrictJsonError as e:
        raise FreezeError(f"JSON 형식 오류: {e}") from None
    values = cfg.get("values")
    if not isinstance(values, dict):
        raise FreezeError("guide-config 에 values 가 없습니다")
    want_doc = cfg.get("guide_doc_sha256")
    if want_doc is not None and want_doc != files["guide_doc"]["sha256"]:
        raise FreezeError("guide-config 의 guide_doc_sha256 이 기준서 파일과 다릅니다 — 기준서와 시작값이 짝이 아닙니다")
    kinds = (((schema.get("properties") or {}).get("kind") or {}).get("enum")) or []
    version = _read(repo, "VERSION").decode("utf-8").strip()
    if not version:
        raise FreezeError("VERSION 이 비어 있습니다")
    try:
        from app.store import RECORD_V
    except Exception:  # noqa: BLE001 — 다른 저장소 사본을 볼 때도 값만 비워 두고 계속
        RECORD_V = None
    return {
        "app_version": version,
        "guide_version": GUIDE_VERSION,
        "label_event": {"schema_id": schema.get("$id"), "record_v": RECORD_V, "kinds": sorted(kinds)},
        "guide": {"doc_sha256": files["guide_doc"]["sha256"],
                  "thresholds_sha256": hashlib.sha256(canonical_json(values)).hexdigest(),
                  "config_version": cfg.get("guide_version") or cfg.get("version")},
        "files": files,
    }


def build_doc(repo: str, now: datetime | None = None) -> dict:
    body = collect(repo)
    head, dirty = git_state(repo)
    now = now or datetime.now().astimezone()
    return {"schema": FREEZE_SCHEMA, "version": FREEZE_VERSION,
            "frozen_at": now.isoformat(timespec="seconds"), "git_head": head, "dirty": dirty, **body}


def compare(frozen: dict, current: dict) -> list[str]:
    """고정 기록과 지금 값의 다른 곳 (이름만)."""
    diffs = []
    if frozen.get("app_version") != current["app_version"]:
        diffs.append(f"app_version {frozen.get('app_version')} → {current['app_version']} (앱 판만 바뀐 것은 허용)")
    for k in ("label_event", "guide"):
        if frozen.get(k) != current[k]:
            diffs.append(k)
    for name, f in current["files"].items():
        old = (frozen.get("files") or {}).get(name) or {}
        if old.get("sha256") != f["sha256"]:
            diffs.append(f"files.{name} ({f['path']})")
    return diffs


def write_exclusive(path: str, doc: dict) -> None:
    """있으면 FileExistsError. 'x' 로 열어 덮어쓰지 않는다."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _inside(path: str, root: str) -> bool:
    p, r = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="시범용 라벨 형식·기준서 v0.1 고정 기록 (D-14 7)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="freeze.json 위치 (기본 <저장소 옆>\\chatcoach-data\\pilot\\freeze.json)")
    ap.add_argument("--repo", default=ROOT, help=argparse.SUPPRESS)       # 시험용: 다른 저장소 사본
    ap.add_argument("--check", action="store_true", help="지금 파일이 고정 기록과 같은지 본다")
    ap.add_argument("--dry-run", action="store_true", help="기록할 내용을 찍기만 한다")
    ap.add_argument("--allow-dirty", action="store_true", help="고정 대상에 커밋 안 된 변경이 있어도 기록한다")
    a = ap.parse_args(argv)
    repo = os.path.abspath(a.repo)

    if a.check:
        try:
            with open(a.out, "rb") as f:
                frozen = load_json_strict(f.read(), MAX_JSON_BYTES)
            current = collect(repo)
        except FileNotFoundError:
            print(f"오류: 고정 기록이 없습니다: {a.out}", file=sys.stderr)
            return 2
        except (OSError, StrictJsonError, FreezeError) as e:
            print(f"오류: {e}", file=sys.stderr)
            return 2
        if frozen.get("schema") != FREEZE_SCHEMA:
            print("오류: freeze.json 형식이 아닙니다", file=sys.stderr)
            return 2
        diffs = compare(frozen, current)
        hard = [d for d in diffs if not d.startswith("app_version")]
        for d in diffs:
            print(("다름: " if d in hard else "참고: ") + d)
        print(f"고정 시각 {frozen.get('frozen_at')} · 앱 {frozen.get('app_version')} · 다른 곳 {len(hard)}개")
        return 1 if hard else 0

    if _inside(a.out, repo):
        print("오류: freeze.json 은 저장소 밖(라벨 데이터 폴더)에 둔다", file=sys.stderr)
        return 2
    if os.path.exists(a.out):
        print(f"거부: 이미 고정 기록이 있습니다: {a.out} (한 번만 만든다. 확인은 --check)", file=sys.stderr)
        return 1
    try:
        doc = build_doc(repo)
    except FreezeError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2
    if doc["dirty"]:
        print("고정 대상에 커밋되지 않은 변경: " + ", ".join(doc["dirty"]), file=sys.stderr)
        if not a.allow_dirty:
            print("거부: 먼저 커밋하거나 --allow-dirty 를 준다", file=sys.stderr)
            return 1
    if doc["git_head"] is None:
        print("경고: git HEAD 를 읽지 못했습니다(git_head null)", file=sys.stderr)
    print(f"앱 {doc['app_version']} · 기준서 v{doc['guide_version']} · 기록 v{doc['label_event']['record_v']} · "
          f"종류 {len(doc['label_event']['kinds'])}개 · 파일 {len(doc['files'])}개")
    for name, f in doc["files"].items():
        print(f"  {name:<20} {f['sha256'][:12]}  {f['path']}")
    if a.dry_run:
        print("--dry-run: 쓰지 않았습니다")
        return 0
    try:
        write_exclusive(a.out, doc)
    except FileExistsError:
        print(f"거부: 이미 고정 기록이 있습니다: {a.out}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"오류: 쓰지 못했습니다 ({type(e).__name__})", file=sys.stderr)
        return 2
    print(f"기록함: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
