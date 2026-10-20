"""VERSION → app/__init__.py · assets/ChatCoach.manifest 생성 (docs/design/04-update-and-release.md 2-1).

사람이 고치는 버전은 저장소 루트의 VERSION 한 곳뿐이다. 두 생성 파일은 커밋하되 손으로 고치지 않는다.

사용:
  python tools/sync_version.py           두 파일을 쓴다
  python tools/sync_version.py --check   생성 결과가 디스크와 다르면 어느 파일인지 출력하고 exit 1 (같으면 0)

매니페스트 본문은 아래 고정 템플릿이고 버전만 바꿔 넣는다. 템플릿은
vtubeadmirer/clipslicer@07d13cc (assets/ClipSlicer.manifest) 를 본떴다.
"""
import sys

# CI 러너처럼 콘솔 인코딩이 UTF-8 이 아닌 곳에서도 한글 출력이 죽지 않게
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.appspec import SPEC  # noqa: E402  (앱 이름의 단일 출처)

VERSION_FILE = os.path.join(ROOT, "VERSION")
# 엄격: ASCII 숫자 세 마디만. 접미사(-rc1 등)는 받지 않는다 (04 2-2)
STRICT_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+", re.ASCII)

INIT_REL = "app/__init__.py"
MANIFEST_REL = f"assets/{SPEC.name}.manifest"

INIT_TEMPLATE = (
    "# 자동 생성: tools/sync_version.py 가 VERSION 에서 만든다. 손으로 고치지 않는다.\n"
    '__version__ = "@VERSION@"\n'
)

# XML 선언은 문서 맨 앞이어야 하므로 주석은 그 다음 줄에 둔다. 주석 안에 '--' 를 쓰지 않는다 (XML 규칙).
MANIFEST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!--
  자동 생성(버전만): tools/sync_version.py 가 VERSION 에서 version 값만 바꿔 쓴다. 손으로 고치지 않는다.
  나머지 내용은 그 스크립트 안의 고정 템플릿이다. 바꿀 때는 템플릿을 고치고 다시 생성한다.

  주의 activeCodePage=UTF-8: 이 프로세스의 ANSI 코드 페이지만 UTF-8 이 되고, 콘솔과 자식 프로세스
  (robocopy · PowerShell 5.1 · ffmpeg 오류 메시지 등)는 여전히 cp949 로 출력할 수 있다. 둘이 어긋나므로
  subprocess 출력은 text=True 로 받지 말고 bytes 로 받아 utf-8 → cp949 순으로 디코딩해야 한다.
-->
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity type="win32" name="@NAME@" version="@VERSION@.0" processorArchitecture="amd64"/>
  <description>@NAME@</description>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0"
                        processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/> <!-- Windows 10/11 -->
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/> <!-- Windows 8.1 -->
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2, PerMonitor</dpiAwareness>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <activeCodePage xmlns="http://schemas.microsoft.com/SMI/2019/WindowsSettings">UTF-8</activeCodePage>
    </windowsSettings>
  </application>
</assembly>
"""


def read_version(path: str = VERSION_FILE) -> str:
    """VERSION 을 읽어 'X.Y.Z' 를 돌려준다. BOM·앞뒤 공백은 지우고, 형식이 아니면 ValueError."""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig").strip()
    if not STRICT_VERSION.fullmatch(text):
        raise ValueError(f"VERSION 형식이 아닙니다 (X.Y.Z 숫자만): {text!r}")
    return text


def render(version: str) -> dict[str, bytes]:
    """생성할 파일 {저장소 기준 경로: 내용(bytes, BOM 없음, LF)}."""
    init = INIT_TEMPLATE.replace("@VERSION@", version)
    manifest = MANIFEST_TEMPLATE.replace("@VERSION@", version).replace("@NAME@", SPEC.name)
    return {INIT_REL: init.encode("utf-8"), MANIFEST_REL: manifest.encode("utf-8")}


def _read(rel: str) -> bytes | None:
    try:
        with open(os.path.join(ROOT, rel), "rb") as f:
            return f.read()
    except OSError:
        return None


def diff_files(version: str | None = None) -> list[str]:
    """디스크 내용이 생성 결과와 다른(또는 없는) 파일의 저장소 기준 경로 목록."""
    version = version or read_version()
    return [rel for rel, data in render(version).items() if _read(rel) != data]


def write_files(version: str | None = None) -> list[str]:
    """두 파일을 쓴다. 실제로 바뀐 파일 목록을 돌려준다."""
    version = version or read_version()
    changed = []
    for rel, data in render(version).items():
        if _read(rel) == data:
            continue
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        changed.append(rel)
    return changed


def main(argv: list[str]) -> int:
    try:
        version = read_version()
    except (OSError, ValueError) as ex:
        print(f"VERSION 을 읽지 못했습니다: {ex}", file=sys.stderr)
        return 1
    if "--check" in argv:
        bad = diff_files(version)
        if bad:
            print(f"VERSION({version}) 과 다른 생성 파일: {', '.join(bad)}", file=sys.stderr)
            print("  python tools/sync_version.py 로 다시 만드세요.", file=sys.stderr)
            return 1
        print(f"버전 파일 일치: {version}")
        return 0
    changed = write_files(version)
    print(f"버전 {version}: " + (", ".join(changed) + " 생성" if changed else "바뀐 파일 없음"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
