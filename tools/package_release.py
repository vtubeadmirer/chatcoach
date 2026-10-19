"""빌드 폴더 → 전체 zip · 경량 zip · SHA256SUMS · latest.json(서명 없음) · notes.md (04 4-2 7~8단계).

사용: python tools/package_release.py --dist dist --out out --version X.Y.Z [--ref vX.Y.Z --sha <커밋>]
- zip 은 Python zipfile 로 만든다 (Compress-Archive 의 역슬래시 항목 문제를 피한다). 최상위 폴더 StreamLabeler/ 하나.
- 보존 폴더(data·_backup·_update)가 빌드 폴더에 있으면 넣지 않는다.
- --ref 가 vX.Y.Z 태그가 아니면(workflow_dispatch) 이름을 dev-<커밋 7자리> 로 하고 latest.json 은 만들지 않는다.
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
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from app.appspec import SPEC  # noqa: E402
from app.updater import UpdateError, validate_zip  # noqa: E402
import make_manifest  # noqa: E402


def _is_heavy(rel: str) -> bool:
    return any(rel == f or rel.startswith(f + "/") for f in SPEC.heavy_files) or \
        any(rel.startswith(d + "/") for d in SPEC.heavy_dirs)


def make_zip(app_dir: str, out_path: str, lite: bool) -> int:
    """app_dir 의 내용을 <zip_top>/… 으로 묶는다. 파일 항목만(폴더 항목 없음), 슬래시 구분, 결정적 순서."""
    top = SPEC.zip_top
    n = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirs, files in os.walk(app_dir):
            rel_dir = os.path.relpath(dirpath, app_dir).replace("\\", "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            if rel_dir == "":
                dirs[:] = sorted(d for d in dirs if d not in SPEC.keep_dirs)
            else:
                dirs.sort()
            for name in sorted(files):
                rel = f"{rel_dir}/{name}" if rel_dir else name
                if lite and _is_heavy(rel):
                    continue
                z.write(os.path.join(dirpath, name), f"{top}/{rel}")
                n += 1
    return n


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dist", default="dist")
    p.add_argument("--out", default="out")
    p.add_argument("--version", required=True)
    p.add_argument("--ref", default="")
    p.add_argument("--sha", default="")
    p.add_argument("--notes", help="기본 docs/release-notes/v<version>.md")
    p.add_argument("--published-at")
    a = p.parse_args(argv)
    SPEC._check_version(a.version)
    app_dir = os.path.join(a.dist, SPEC.zip_top)
    if not os.path.isfile(os.path.join(app_dir, SPEC.exe_name)):
        raise SystemExit(f"빌드 폴더가 아닙니다: {app_dir}")
    with open(os.path.join(app_dir, "_internal", "VERSION"), encoding="utf-8-sig") as f:
        built = f.read().strip()
    if built != a.version:
        raise SystemExit(f"빌드된 VERSION({built})이 --version({a.version})과 다릅니다")
    os.makedirs(a.out, exist_ok=True)
    is_tag = bool(re.match(r"\Av[0-9]+\.[0-9]+\.[0-9]+\Z", a.ref or ""))
    if a.ref and not is_tag:
        label = f"dev-{(a.sha or 'unknown')[:7]}"
        names = {"full": f"{SPEC.name}-{label}-win64.zip", "lite": f"{SPEC.name}-{label}-win64-update.zip"}
    else:
        names = {k: SPEC.asset_name(a.version, k) for k in ("full", "lite")}
    paths = {}
    for kind, name in names.items():
        out = os.path.join(a.out, name)
        n = make_zip(app_dir, out, lite=(kind == "lite"))
        try:
            s = validate_zip(out, a.version, kind == "lite", SPEC)
        except UpdateError as ex:
            raise SystemExit(f"{name} 검사 실패: {ex.message}")
        print(f"{name}: {n}개 파일, {os.path.getsize(out) / 1e6:.1f} MB (해제 {s.unpacked_size / 1e6:.0f} MB)")
        paths[kind] = out
    with open(os.path.join(a.out, "SHA256SUMS"), "w", encoding="ascii", newline="\n") as f:
        for kind in ("full", "lite"):
            f.write(f"{sha256_file(paths[kind])} *{names[kind]}\n")
    notes = a.notes or os.path.join(ROOT, "docs", "release-notes", f"v{a.version}.md")
    if not os.path.isfile(notes):
        raise SystemExit(f"릴리스 노트가 없습니다: {notes}")
    _, _, body = make_manifest.read_notes(notes)
    with open(os.path.join(a.out, "notes.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    if is_tag or not a.ref:
        m = make_manifest.build(a.version, paths["full"], paths["lite"], notes, a.published_at)
        make_manifest.write(m, os.path.join(a.out, "latest.json"))
        print(f"latest.json: version={m['version']} published_at={m['published_at']}")
    else:
        print("dev 빌드: latest.json 은 만들지 않습니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
