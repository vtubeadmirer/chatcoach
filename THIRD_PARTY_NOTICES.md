# 제3자 구성 요소 고지 (THIRD_PARTY_NOTICES)

> **이 문서는 법률 자문이 아니다.** Claude 가 M0 에 쓴 초안이며, 첫 공개 릴리스 전에 배포자가 확인한다
> ([07-roadmap.md](docs/design/07-roadmap.md) 질문 31). '확인 필요' 로 표시한 곳은 아직 확인하지 않았다.

StreamLabeler 자체의 코드는 MIT 라이선스다 ([LICENSE](LICENSE)). 배포 zip(`StreamLabeler-vX.Y.Z-win64.zip`)에는
아래 구성 요소가 함께 들어가며, 각 구성 요소는 **자기 라이선스**를 따른다. 경량 업데이트 zip
(`…-win64-update.zip`)에는 FFmpeg(`_internal\ffmpeg\`)와 libmpv(`_internal\libmpv-2.dll`)가 빠진다.

## 한눈에 보기

| 구성 요소 | 버전 | 라이선스 | 어떻게 쓰나 |
|---|---|---|---|
| Qt 6 / PySide6 · Shiboken6 | 6.12.0 | LGPL-3.0-only (GPL-2.0/3.0·상용과 선택) | 번들, 동적 링크 (`_internal\PySide6\*.dll`) |
| FFmpeg (BtbN `win64-gpl-shared`) | n8.1.3-18-g46d899d7a0 | GPL-3.0-or-later (아래 설명) | 번들, 별도 실행 파일 (`_internal\ffmpeg\ffmpeg.exe`·`ffprobe.exe` + DLL) |
| libmpv (shinchiro/mpv-winbuild-cmake) | mpv v0.41.0-1151-g9d144e57b | GPL-2.0-or-later 로 보임 — 확인 필요 | 번들, 실행 중 동적 로드 (`_internal\libmpv-2.dll`) |
| python-mpv | 1.0.8 | GPL-2.0-or-later 또는 LGPL-2.1-or-later (이중) | 번들 (Python 모듈) |
| Python | 3.12 | PSF-2.0 | 번들 (`_internal\python312.dll`·표준 라이브러리) |
| PyInstaller 부트로더 | 6.20.0 | GPL-2.0-or-later WITH Bootloader-exception | `StreamLabeler.exe` 에 들어간 실행기 |

---

## Qt 6 / PySide6 6.12.0

- 라이선스: LGPL-3.0-only (Qt for Python 은 LGPL-3.0 / GPL-2.0 / GPL-3.0 / 상용 중 선택. 설치된 패키지 메타데이터
  `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` 로 확인). StreamLabeler 는 LGPL-3.0 조건으로 쓴다. Shiboken6 도 같다.
- 출처: https://www.qt.io/ · 소스 https://code.qt.io (qtbase · qtsvg · pyside/pyside-setup)
- 쓰는 방식: **동적 링크**. Qt 라이브러리는 실행 파일에 합쳐 넣지 않고 `_internal\PySide6\` 아래 별도 DLL
  (`Qt6Core.dll`, `Qt6Gui.dll`, `Qt6Widgets.dll`, `Qt6Svg.dll`)과 `.pyd` 로 둔다. Qt 소스를 고치지 않았다.
- **라이브러리 교체**: 사용자는 같은 버전대(ABI 호환)의 Qt / PySide6 DLL 로 `_internal\PySide6\` 의 파일을 바꿔 넣어
  실행할 수 있다 (LGPL-3.0 4항). 앱은 이를 막는 서명 검사·암호화를 하지 않는다.
- 포함된 Qt 플러그인: `platforms` · `styles` · `imageformats` · `iconengines`.
- Qt 안에 들어 있는 제3자 코드(FreeType, HarfBuzz, libpng, libjpeg, zlib 등)는 Qt 문서의 'Licenses Used in Qt' 를
  따른다 — 실제 번들된 DLL 기준 목록은 확인 필요.

## FFmpeg (BtbN FFmpeg-Builds `win64-gpl-shared`)

- 빌드: BtbN/FFmpeg-Builds 릴리스 `autobuild-2026-10-12-13-12`,
  자산 `ffmpeg-n8.1.3-18-g46d899d7a0-win64-gpl-shared-8.1.zip`
  (SHA-256 `7bad9c116b45fb7441c636d2dc8c15d40cd2933772c6920d60569bb184c8c524`, `tools/get_ffmpeg.py` 에 고정).
- 라이선스: **GPL-3.0-or-later**. 이 빌드는 `--enable-gpl --enable-version3` 으로 구성되어 있고 `ffmpeg -L` 이
  'GNU General Public License … version 3 of the License, or (at your option) any later version' 을 출력한다
  (2026-10-20 확인). 설계 문서의 'GPL-2.0-or-later' 표기보다 한 단계 강하다 — 배포자 확인 필요.
- 출처: https://ffmpeg.org/ · 빌드 https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-10-12-13-12
- 쓰는 방식: **별도 프로그램으로 실행**(subprocess). `_internal\ffmpeg\` 의 `ffmpeg.exe`·`ffprobe.exe` 와
  그 DLL(`avcodec`·`avformat`·`avfilter`·`avdevice`·`avutil`·`swresample`·`swscale`)을 그대로 번들한다. 고치지 않았다.
- 소스: 아래 '소스 제공 방법' (확인 필요).

## libmpv (shinchiro/mpv-winbuild-cmake)

- 빌드: shinchiro/mpv-winbuild-cmake 릴리스 `20261013`, 자산 `mpv-dev-x86_64-20261013-git-9d144e57b6.7z`
  (SHA-256 `10860837bc04491862c1e66a95dc1af1d7886d69757ce447a09e1e49ae993b52`, `tools/get_libmpv.py` 에 고정.
  상류에 체크섬 파일이 없어 공개 값과 대조하지 못했다). 이 DLL 이 보고하는 버전은 `mpv v0.41.0-1151-g9d144e57b`,
  안에 정적으로 들어간 FFmpeg 는 `N-127491-g0eeb79fcc`.
- 라이선스: mpv 는 기본이 GPL-2.0-or-later 이고, `-Dgpl=false` 로 빌드하면 LGPL-2.1-or-later 가 된다. 이 DLL 의
  빌드 설정(`mpv-configuration`)에 `-Dgpl=false` 가 없으므로 **GPL 빌드로 보인다**. 정적으로 들어간 FFmpeg·rubberband·
  libbluray 등 구성 요소의 라이선스 때문에 결합물 전체가 GPL-3.0 조건이 될 수도 있다 — **이 빌드의 정확한 라이선스는 확인 필요**.
- 출처: https://mpv.io/ · 소스 https://github.com/mpv-player/mpv · 빌드 스크립트
  https://github.com/shinchiro/mpv-winbuild-cmake/releases/tag/20261013
- 쓰는 방식: **동적 로드**. python-mpv 가 실행 중 ctypes 로 `_internal\libmpv-2.dll` 을 불러 재생 창을 띄운다. 고치지 않았다.
- 앱 코드와의 관계: GPL 라이브러리를 불러 쓰는 앱 코드를 MIT 로 공개해도 되는지(MIT 는 GPL 과 호환되므로 배포물 전체를
  GPL 조건으로 내면 된다는 해석)는 **확인 필요** (07 질문 31).

## python-mpv 1.0.8

- 라이선스: GPL-2.0-or-later 또는 LGPL-2.1-or-later 이중 라이선스 (패키지 메타데이터 `GPLv2+ or LGPLv2.1+`,
  `LICENSE.GPL`·`LICENSE.LGPL` 동봉으로 확인). 어느 쪽을 고르는지는 함께 쓰는 libmpv 빌드에 따른다 — 확인 필요.
- 출처: https://github.com/jaseg/python-mpv · https://pypi.org/project/mpv/1.0.8/
- 쓰는 방식: 번들 (PyInstaller 가 Python 모듈로 묶는다). 고치지 않았다.

## Python 3.12

- 라이선스: PSF-2.0 (Python Software Foundation License Version 2)
- 출처: https://www.python.org/ · 소스 https://github.com/python/cpython
- 쓰는 방식: 번들. `_internal\python312.dll` 과 표준 라이브러리 일부, 확장 모듈(`*.pyd`).
- Python 배포에 딸린 확장 DLL(예: OpenSSL 3 = Apache-2.0, libffi = MIT, bzip2 · xz · zlib · expat 등)도 함께 들어갈 수 있다 —
  실제 목록은 빌드 후 `_internal\` 을 보고 확정 (확인 필요).

## PyInstaller 6.20.0 부트로더

- 라이선스: GPL-2.0-or-later WITH Bootloader-exception. 부트로더 예외에 따라 PyInstaller 로 만든 실행 파일은
  자유롭게 배포할 수 있다 (패키지 메타데이터 · `COPYING.txt` 로 확인).
- 출처: https://pyinstaller.org/ · 소스 https://github.com/pyinstaller/pyinstaller
- 쓰는 방식: 빌드 도구. `StreamLabeler.exe` 안에 부트로더가 들어간다. PyInstaller 자체는 배포물에 들어가지 않는다.

---

## 소스 제공 방법 (확인 필요)

GPL 로 배포되는 FFmpeg·libmpv(와 LGPL 인 Qt)는 바이너리와 함께 대응 소스를 제공하거나 그 방법을 알려야 한다.
지금 계획은 다음과 같다. **충분한지는 배포자가 확인한다.**

1. 각 구성 요소의 정확한 빌드 태그와 해시를 이 문서와 `_internal\deps.txt` 에 적는다.
2. 상류 소스 위치를 안내한다.
   - FFmpeg: https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-10-12-13-12 (빌드 스크립트) ·
     https://git.ffmpeg.org/ffmpeg.git (커밋 `46d899d7a0`)
   - libmpv: https://github.com/shinchiro/mpv-winbuild-cmake (빌드 스크립트) · https://github.com/mpv-player/mpv (커밋 `9d144e57b`)
   - Qt / PySide6: https://code.qt.io (6.12.0 태그)
3. **요청 시 제공**: 배포 후 3년 동안, 요청하면 위 바이너리에 대응하는 전체 소스를 실비 이하로 제공한다.
   요청 창구는 저장소 이슈(https://github.com/vtubeadmirer/streamlabeler/issues)로 한다.
   - 상류 링크만으로는 GPL 의 소스 제공 의무를 다하지 못할 수 있다(상류가 파일을 지우면 끊긴다). 릴리스마다 대응 소스
     아카이브를 직접 보관·게시할지 — 확인 필요.
4. 배포 zip 에 라이선스 전문(GPL-3.0 · GPL-2.0 · LGPL-3.0 · LGPL-2.1 · PSF-2.0)을 넣을지와 넣을 위치 — 확인 필요.
