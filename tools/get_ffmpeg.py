# vendored from vtubeadmirer/clipslicer@07d13cc (tools/get_ffmpeg.py) — 변경: SHA256 고정값 비교(다르면 빌드 중단), User-Agent StreamLabeler-tools
"""ffmpeg 공유(shared) 빌드를 받아 bin/ffmpeg/ 에 넣는다.

정적 빌드(ffmpeg.exe 141MB + ffprobe.exe 141MB)와 달리 DLL 을 공유해서 전체 약 100MB.
BtbN/FFmpeg-Builds 의 특정 autobuild 태그에 고정 (재현 가능한 빌드).
받은 아카이브의 SHA-256 을 아래 SHA256 상수와 비교하고, 다르면 압축을 풀지 않고 멈춘다
(상류 계정이 털려 같은 이름의 파일이 바뀐 경우를 잡는다. docs/design/04-update-and-release.md 4-4, S-04).

사용: python tools/get_ffmpeg.py [--tag autobuild-YYYY-MM-DD-HH-MM]
      --tag 로 다른 버전을 받으면 해시를 검사하지 않고, 고정할 값(TAG·ASSET·SHA256)을 출력한다.
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
import sys
import urllib.request
import zipfile

# 고정 버전. 올릴 때는 세 값을 같이 바꾸고 integration 테스트를 돌린다.
TAG = "autobuild-2026-10-12-13-12"
ASSET = "ffmpeg-n8.1.3-18-g46d899d7a0-win64-gpl-shared-8.1.zip"
# 위 ASSET 의 SHA-256. 같은 릴리스의 상류 checksums.sha256 과 대조해 확인한 값이다 (2026-10-20).
SHA256 = "7bad9c116b45fb7441c636d2dc8c15d40cd2933772c6920d60569bb184c8c524"
USER_AGENT = "StreamLabeler-tools"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "bin", "ffmpeg")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as r:
        return r.read()


def main(argv):
    tag = TAG
    asset = ASSET
    pinned = True
    if "--tag" in argv:
        tag = argv[argv.index("--tag") + 1]
        api = f"https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/{tag}"
        rel = json.loads(_get(api))
        asset = next(a["name"] for a in rel["assets"]
                     if "win64-gpl-shared" in a["name"] and a["name"].endswith(".zip") and "-N-" not in a["name"])
        pinned = (tag, asset) == (TAG, ASSET)
    url = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{tag}/{asset}"
    print(f"다운로드: {asset}")
    data = _get(url)
    actual = hashlib.sha256(data).hexdigest()
    print(f"  {len(data) / 1e6:.1f} MB 받음, SHA-256 {actual}")
    if pinned:
        if actual != SHA256:
            # 예외로 끊는다 — build.ps1 · release.yml 이 여기서 멈춘다
            raise SystemExit(f"ffmpeg 아카이브 해시 불일치: 기대 {SHA256} 실제 {actual}")
        print("  해시 확인 OK (고정값과 같음)")
    else:
        print("  고정 버전이 아니므로 해시를 검사하지 않습니다.")
        print("  이 버전으로 고정하려면 tools/get_ffmpeg.py 의 TAG·ASSET·SHA256 세 상수에 다음 값을 적으세요")
        print("  (상류 릴리스의 checksums.sha256 과 대조한 뒤에):")
        print(f'    TAG = "{tag}"')
        print(f'    ASSET = "{asset}"')
        print(f'    SHA256 = "{actual}"')
    print("  압축 해제…")
    if os.path.isdir(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST, exist_ok=True)
    n = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            parts = name.split("/")
            # <root>/bin/ffmpeg.exe, <root>/bin/*.dll 만
            if len(parts) >= 3 and parts[1] == "bin" and parts[-1] and (
                    parts[-1].lower().endswith((".exe", ".dll"))):
                if parts[-1].lower() == "ffplay.exe":
                    continue
                with z.open(name) as src, open(os.path.join(DEST, parts[-1]), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                n += 1
    # 혹시 남아 있는 정적 빌드는 치운다 (공유 빌드가 우선되도록)
    for old in ("ffmpeg.exe", "ffprobe.exe"):
        p = os.path.join(ROOT, "bin", old)
        if os.path.isfile(p):
            os.remove(p)
            print(f"  정적 빌드 제거: bin\\{old}")
    with open(os.path.join(DEST, "VERSION.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{tag}\n{asset}\n")
    size = sum(os.path.getsize(os.path.join(DEST, f)) for f in os.listdir(DEST)) / 1e6
    print(f"완료: {DEST} ({n}개 파일, {size:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
