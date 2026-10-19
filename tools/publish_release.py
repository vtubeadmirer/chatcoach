"""초안 릴리스 확인 → 서명 파일 업로드 → 게시 → latest 전환 (개발 PC, docs/design/04-update-and-release.md 4-2 12~16단계).

사용:
  python tools/publish_release.py check vX.Y.Z [--out <폴더>]   초안의 latest.json 과 zip 을 받아 버전·노트·크기를 보이고 해시를 다시 센다
  python tools/sign_manifest.py sign <폴더>\\latest.json           (별도 — 오프라인 키, 암호 입력)
  python tools/publish_release.py upload-sig vX.Y.Z <폴더>\\latest.json.minisig
  python tools/publish_release.py publish vX.Y.Z                 prerelease 로 게시 (초안 해제)
  python tools/publish_release.py make-latest vX.Y.Z             prerelease 를 풀고 latest 로. 공개 링크로 서명 검증

인증: gh CLI 가 있으면 gh 를 쓴다 (`gh auth login` 필요). 없으면 REST API 를 직접 부르고 토큰은 환경 변수 GH_TOKEN 에서만 읽는다.
앱에는 어떤 토큰도 넣지 않는다 (04 9-5).
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
import shutil
import subprocess
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from app.appspec import SPEC  # noqa: E402
from app.updater import parse_manifest  # noqa: E402
from app import minisign  # noqa: E402

API = "https://api.github.com"


# --------------------------------------------------------------------------- 백엔드
class Gh:
    """gh CLI 래퍼."""

    def __init__(self):
        self.exe = shutil.which("gh")

    def ok(self) -> bool:
        return bool(self.exe)

    def run(self, *args: str, capture: bool = True) -> str:
        r = subprocess.run([self.exe, *args, "-R", SPEC.repo], capture_output=capture, text=True, encoding="utf-8")
        if r.returncode != 0:
            raise SystemExit(f"gh 실패: {' '.join(args)}\n{r.stderr if capture else ''}")
        return r.stdout if capture else ""

    def view(self, tag: str) -> dict:
        return json.loads(self.run("release", "view", tag, "--json", "assets,isDraft,isPrerelease,name,body,tagName"))

    def download(self, tag: str, pattern: str, dest: str) -> None:
        self.run("release", "download", tag, "--pattern", pattern, "--dir", dest, "--clobber")

    def upload(self, tag: str, path: str) -> None:
        self.run("release", "upload", tag, path, "--clobber")

    def edit(self, tag: str, *flags: str) -> None:
        self.run("release", "edit", tag, *flags)


class Rest:
    """gh 가 없을 때. 토큰은 GH_TOKEN 환경 변수에서만."""

    def __init__(self):
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""

    def ok(self) -> bool:
        return bool(self.token)

    def _req(self, url: str, method: str = "GET", data: bytes | None = None, ctype: str = "application/json", accept: str | None = None):
        headers = {"Authorization": f"Bearer {self.token}", "User-Agent": SPEC.user_agent("tools"),
                   "Accept": accept or "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if data is not None:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    def _release(self, tag: str) -> dict:
        # 초안은 /releases/tags/<tag> 로 보이지 않으므로 목록에서 찾는다
        rels = json.loads(self._req(f"{API}/repos/{SPEC.repo}/releases?per_page=30"))
        for r in rels:
            if r.get("tag_name") == tag:
                return r
        raise SystemExit(f"릴리스를 찾지 못했습니다: {tag}")

    def view(self, tag: str) -> dict:
        r = self._release(tag)
        return {"assets": [{"name": a["name"], "url": a["url"], "size": a["size"]} for a in r.get("assets", [])],
                "isDraft": r["draft"], "isPrerelease": r["prerelease"], "name": r["name"], "body": r.get("body") or "",
                "tagName": r["tag_name"], "_id": r["id"], "_upload_url": r["upload_url"].split("{")[0]}

    def download(self, tag: str, pattern: str, dest: str) -> None:
        import fnmatch
        for a in self.view(tag)["assets"]:
            if fnmatch.fnmatch(a["name"], pattern):
                data = self._req(a["url"], accept="application/octet-stream")
                with open(os.path.join(dest, a["name"]), "wb") as f:
                    f.write(data)

    def upload(self, tag: str, path: str) -> None:
        v = self.view(tag)
        name = os.path.basename(path)
        for a in v["assets"]:
            if a["name"] == name:
                self._req(a["url"], method="DELETE")
        with open(path, "rb") as f:
            data = f.read()
        self._req(f"{v['_upload_url']}?name={name}", method="POST", data=data, ctype="application/octet-stream")

    def edit(self, tag: str, *flags: str) -> None:
        v = self.view(tag)
        body: dict = {}
        for fl in flags:
            if fl == "--draft=false":
                body["draft"] = False
            elif fl == "--prerelease":
                body["prerelease"] = True
            elif fl == "--prerelease=false":
                body["prerelease"] = False
            elif fl == "--latest":
                body["make_latest"] = "true"
        self._req(f"{API}/repos/{SPEC.repo}/releases/{v['_id']}", method="PATCH", data=json.dumps(body).encode("utf-8"))


def backend():
    gh = Gh()
    if gh.ok():
        return gh
    rest = Rest()
    if rest.ok():
        print("gh CLI 가 없어 REST API 를 씁니다 (GH_TOKEN)")
        return rest
    raise SystemExit("gh CLI 를 설치해 `gh auth login` 하거나, 환경 변수 GH_TOKEN 을 두세요 (04 4-2)")


# --------------------------------------------------------------------------- 명령
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_check(a) -> int:
    b = backend()
    tag = a.tag
    v = b.view(tag)
    print(f"릴리스 {v['tagName']} '{v['name']}'  draft={v['isDraft']} prerelease={v['isPrerelease']}")
    names = {x["name"] for x in v["assets"]}
    print("자산:", ", ".join(sorted(names)))
    out = a.out or os.path.join(ROOT, "out", tag)
    os.makedirs(out, exist_ok=True)
    b.download(tag, "latest.json", out)
    b.download(tag, "*.zip", out)
    with open(os.path.join(out, "latest.json"), "rb") as f:
        data = f.read()
    m = parse_manifest(data, SPEC)
    if f"v{m.version}" != tag:
        print(f"실패: latest.json 의 version({m.version})이 태그({tag})와 다릅니다")
        return 1
    print(f"latest.json: version={m.version} published_at={m.published_at.isoformat()} min_supported={m.min_supported} mandatory={m.mandatory}")
    print("--- notes_ko ---")
    print(m.notes_ko.strip())
    print("----------------")
    ok = True
    for asset in m.assets:
        p = os.path.join(out, asset.name)
        if not os.path.isfile(p):
            print(f"실패: {asset.name} 을 받지 못했습니다")
            ok = False
            continue
        size, digest = os.path.getsize(p), sha256_file(p)
        good = size == asset.size and digest == asset.sha256
        print(f"{asset.kind:4} {asset.name}  {size / 1e6:.1f} MB  sha256 {'일치' if good else '불일치!'}")
        ok = ok and good
    if "latest.json.minisig" in names:
        print("주의: 초안에 이미 .minisig 가 있습니다 (CI 는 서명하지 않아야 합니다)")
    print("확인 완료. 다음:" if ok else "실패 — 서명하지 마세요")
    print(f"  python tools\\sign_manifest.py sign {os.path.join(out, 'latest.json')}")
    print(f"  python tools\\publish_release.py upload-sig {tag} {os.path.join(out, 'latest.json.minisig')}")
    return 0 if ok else 1


def cmd_upload_sig(a) -> int:
    b = backend()
    with open(a.sig, "rb") as f:
        sig = f.read()
    minisign.parse_signature(sig)
    b.upload(a.tag, a.sig)
    print(f"업로드: {os.path.basename(a.sig)} → {a.tag}")
    return 0


def cmd_publish(a) -> int:
    b = backend()
    v = b.view(a.tag)
    names = {x["name"] for x in v["assets"]}
    for need in ("latest.json", "latest.json.minisig"):
        if need not in names:
            print(f"실패: 초안에 {need} 가 없습니다")
            return 1
    b.edit(a.tag, "--draft=false", "--prerelease")
    print(f"게시 (prerelease): https://github.com/{SPEC.repo}/releases/tag/{a.tag}")
    return 0


def cmd_make_latest(a) -> int:
    b = backend()
    b.edit(a.tag, "--prerelease=false", "--latest")
    print(f"latest 전환: {a.tag}")
    import sign_manifest
    url = SPEC.latest_download_url("latest.json")
    rc = sign_manifest.main(["verify", "--url", url])
    if rc:
        print("주의: 공개 링크의 서명 검증이 실패했습니다. CDN 캐시일 수 있으니 몇 분 뒤 다시 확인하세요")
    return rc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("tag")
    c.add_argument("--out")
    c.set_defaults(fn=cmd_check)
    u = sub.add_parser("upload-sig")
    u.add_argument("tag")
    u.add_argument("sig")
    u.set_defaults(fn=cmd_upload_sig)
    pb = sub.add_parser("publish")
    pb.add_argument("tag")
    pb.set_defaults(fn=cmd_publish)
    ml = sub.add_parser("make-latest")
    ml.add_argument("tag")
    ml.set_defaults(fn=cmd_make_latest)
    a = p.parse_args(argv)
    if not a.tag.startswith("v"):
        p.error("태그는 vX.Y.Z 형식")
    SPEC._check_version(a.tag[1:])
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
