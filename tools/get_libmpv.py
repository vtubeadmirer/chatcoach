# vendored from vtubeadmirer/clipslicer@07d13cc (tools/get_libmpv.py) — 변경: SHA256 고정값 비교(다르면 빌드 중단), User-Agent StreamLabeler-tools
"""libmpv-2.dll 을 shinchiro/mpv-winbuild-cmake 의 고정 릴리스에서 받아 bin/ 에 넣는다.

사용: python tools/get_libmpv.py [--latest]
압축 해제: 7-Zip / Bandizip / NanaZip 이 있으면 그걸 쓰고, 없으면 py7zr 로 시도
(mpv 빌드는 BCJ2 필터를 쓰기 때문에 py7zr 로는 실패할 수 있음 — 그 경우 7-Zip 이나 Bandizip 설치).
받은 아카이브의 SHA-256 을 아래 SHA256 상수와 비교하고, 다르면 풀지 않고 멈춘다
(docs/design/04-update-and-release.md 4-4, S-04). --latest 로 다른 버전을 받으면 해시를 검사하지 않고
고정할 값(TAG·ASSET·SHA256)을 출력한다.
"""
import sys

# CI 러너처럼 콘솔 인코딩이 UTF-8 이 아닌 곳에서도 한글 출력이 죽지 않게
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

# 고정 버전 (API 조회 없이 직접 받음 — GitHub Actions 러너는 비인증 API 호출이 자주 막힌다).
# 올릴 때는 세 값을 같이 바꾸고 실제 재생 테스트를 할 것. --latest 를 주면 API 로 최신을 찾는다.
TAG = "20261013"
ASSET = "mpv-dev-x86_64-20261013-git-9d144e57b6.7z"
# 위 ASSET 의 SHA-256. 상류 릴리스에는 체크섬 파일이 없어 공개 값과 대조하지 못했다 — 받은 파일에서 직접 계산한 값이다.
SHA256 = "10860837bc04491862c1e66a95dc1af1d7886d69757ce447a09e1e49ae993b52"
USER_AGENT = "StreamLabeler-tools"
API = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
DLL = "libmpv-2.dll"

EXTRACTORS = [
    # (실행 파일 후보, 인자 생성 함수)
    (["7z", r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files\NanaZip\7z.exe"],
     lambda exe, archive, out: [exe, "e", "-y", f"-o{out}", archive, DLL, "-r"]),
    (["bz", r"C:\Program Files\Bandizip\bz.exe"],
     lambda exe, archive, out: [exe, "x", "-y", f"-o:{out}", archive, DLL]),
]


def find_extractor():
    for cands, argf in EXTRACTORS:
        for c in cands:
            exe = shutil.which(c) if not os.path.isabs(c) else (c if os.path.isfile(c) else None)
            if exe:
                return exe, argf
    return None, None


def extract_with_tool(exe, argf, archive: str, out: str) -> str | None:
    subprocess.run(argf(exe, archive, out), check=False, capture_output=True)
    for dirpath, _, files in os.walk(out):
        if DLL in files:
            return os.path.join(dirpath, DLL)
    return None


def extract_with_py7zr(data: bytes, out: str) -> str | None:
    try:
        import py7zr
    except ImportError:
        print("py7zr 가 없습니다 (pip install py7zr) — 또는 7-Zip/Bandizip 을 설치하세요.")
        return None
    with py7zr.SevenZipFile(io.BytesIO(data), "r") as z:
        names = [n for n in z.getnames() if n.endswith(DLL)]
        if not names:
            return None
        z.extract(path=out, targets=names)
    return os.path.join(out, names[0])


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as r:
        return r.read()


def main():
    tag, name = TAG, ASSET
    if "--latest" in sys.argv:
        rel = json.loads(_get(API))
        asset = next((a for a in rel["assets"]
                      if a["name"].startswith("mpv-dev-x86_64-") and "-v3-" not in a["name"]
                      and a["name"].endswith(".7z")), None)
        if not asset:
            print("mpv-dev-x86_64 아카이브를 찾지 못했습니다.")
            return 1
        tag, name = rel["tag_name"], asset["name"]
    pinned = (tag, name) == (TAG, ASSET)
    url = f"https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/{tag}/{name}"
    print(f"다운로드: {name}")
    data = _get(url)
    actual = hashlib.sha256(data).hexdigest()
    print(f"  {len(data) / 1e6:.1f} MB 받음, SHA-256 {actual}")
    if pinned:
        if actual != SHA256:
            # 예외로 끊는다 — build.ps1 · release.yml 이 여기서 멈춘다
            raise SystemExit(f"libmpv 아카이브 해시 불일치: 기대 {SHA256} 실제 {actual}")
        print("  해시 확인 OK (고정값과 같음)")
    else:
        print("  고정 버전이 아니므로 해시를 검사하지 않습니다.")
        print("  이 버전으로 고정하려면 tools/get_libmpv.py 의 TAG·ASSET·SHA256 세 상수에 다음 값을 적으세요:")
        print(f'    TAG = "{tag}"')
        print(f'    ASSET = "{name}"')
        print(f'    SHA256 = "{actual}"')

    tmp = tempfile.mkdtemp(prefix="libmpv_")
    archive = os.path.join(tmp, name)
    with open(archive, "wb") as f:
        f.write(data)

    exe, argf = find_extractor()
    found = None
    if exe:
        print(f"압축 해제: {exe}")
        found = extract_with_tool(exe, argf, archive, tmp)
    if not found:
        print("py7zr 로 시도…")
        try:
            found = extract_with_py7zr(data, tmp)
        except Exception as ex:
            print(f"py7zr 실패: {ex}")
    if not found:
        print("libmpv-2.dll 을 꺼내지 못했습니다. 7-Zip 또는 Bandizip 을 설치한 뒤 다시 실행하세요.")
        return 1

    os.makedirs(BIN, exist_ok=True)
    dst = os.path.join(BIN, DLL)
    shutil.copy2(found, dst)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"완료: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
