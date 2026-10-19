"""latest.json 생성 (서명 없음) — docs/design/04-update-and-release.md 5장.

사용: python tools/make_manifest.py --version X.Y.Z --full <전체.zip> --lite <경량.zip> --notes docs/release-notes/vX.Y.Z.md --out latest.json
해시·크기·압축 해제 크기·파일 수는 zip 에서 직접 센다 (사람이 옮겨 적지 않는다). deps 는 tools/get_ffmpeg.py·get_libmpv.py 의 상수.
만든 뒤 app/updater.py 의 parse_manifest 로 되읽어 형식을 확인한다.
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
import zipfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from app.appspec import SPEC  # noqa: E402
from app.updater import parse_manifest  # noqa: E402


def zip_facts(path: str) -> dict:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    with zipfile.ZipFile(path) as z:
        files = [i for i in z.infolist() if not i.is_dir()]
        return {"size": os.path.getsize(path), "sha256": h.hexdigest(),
                "unpacked_size": sum(i.file_size for i in files), "file_count": len(files)}


def read_notes(path: str) -> tuple[str, bool, str]:
    """릴리스 노트 파일 → (min_supported, mandatory, 본문). 형식은 04 13장."""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    head, sep, body = text.partition("\n---\n")
    if not sep:
        raise SystemExit(f"릴리스 노트에 '---' 구분선이 없습니다: {path}")
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    if "min_supported" not in meta or meta.get("mandatory") not in ("true", "false"):
        raise SystemExit("릴리스 노트 머리에는 'min_supported: X.Y.Z' 와 'mandatory: true|false' 가 있어야 합니다")
    body = body.strip() + "\n"
    if len(body.encode("utf-8")) > SPEC.max_notes_chars:
        raise SystemExit(f"릴리스 노트 본문이 {SPEC.max_notes_chars} 바이트를 넘습니다")
    return meta["min_supported"], meta["mandatory"] == "true", body


def deps_from_tools() -> dict:
    import get_ffmpeg
    import get_libmpv
    return {"ffmpeg": {"tag": get_ffmpeg.TAG, "sha256": get_ffmpeg.SHA256},
            "libmpv": {"tag": get_libmpv.TAG, "sha256": get_libmpv.SHA256}}


def build(version: str, full_zip: str, lite_zip: str, notes_path: str, published_at: str | None = None,
          deps: dict | None = None, label_schema_version: int | None = None, guide_versions: list[str] | None = None) -> dict:
    min_supported, mandatory, body = read_notes(notes_path)
    assets = []
    for kind, path in (("full", full_zip), ("lite", lite_zip)):
        name = SPEC.asset_name(version, kind)
        if os.path.basename(path) != name:
            raise SystemExit(f"{kind} zip 이름이 규칙과 다릅니다: {os.path.basename(path)} (기대 {name})")
        facts = zip_facts(path)
        assets.append({"kind": kind, "name": name, "url": SPEC.asset_url(version, name), **facts})
    m = {
        "manifest_version": SPEC.manifest_version,
        "app": SPEC.name,
        "version": version,
        "published_at": published_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "min_supported": min_supported,
        "mandatory": mandatory,
        "label_schema_version": SPEC.label_schema_version if label_schema_version is None else label_schema_version,
        "guide_versions": guide_versions if guide_versions is not None else ["0.1"],
        "notes_ko": body,
        "deps": deps if deps is not None else deps_from_tools(),
        "assets": assets,
    }
    data = json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    parse_manifest(data, SPEC)   # 되읽기 검증
    return m


def write(m: dict, out: str) -> bytes:
    data = json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    with open(out, "wb") as f:
        f.write(data)
    return data


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--version", required=True)
    p.add_argument("--full", required=True)
    p.add_argument("--lite", required=True)
    p.add_argument("--notes", required=True)
    p.add_argument("--out", default="latest.json")
    p.add_argument("--published-at", help="ISO 8601 UTC. 기본은 지금")
    a = p.parse_args(argv)
    m = build(a.version, a.full, a.lite, a.notes, a.published_at)
    write(m, a.out)
    print(f"완료: {a.out}  version={m['version']} full={m['assets'][0]['size']} lite={m['assets'][1]['size']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
