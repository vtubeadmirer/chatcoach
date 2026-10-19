# vendored from vtubeadmirer/clipslicer@07d13cc (build.ps1) — 변경: StreamLabeler.spec 로 빌드, WebEngine·yt-dlp·확장 단계 삭제, Qt 화이트리스트 Core/Gui/Widgets/Svg, -Channel, build_info·deps 는 tools\write_build_info.py
# PyInstaller 로 <DistPath>\StreamLabeler\ 폴더 빌드.
#   .\build.ps1                                     → dist\StreamLabeler (release 채널)
#   .\build.ps1 -DistPath dist_test -Channel test   → 시험 빌드 (실제 설치본 dist\ 를 건드리지 않는다)
# 실행 전: pip install --require-hashes -r requirements.lock (bin\ 의 ffmpeg·libmpv 가 없으면 여기서 받는다)
param(
  [string]$DistPath = "dist",
  [ValidateSet("release", "test")][string]$Channel = "release"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 앱 이름은 app\appspec.py 가 단일 출처 (02 3-2)
$name = (& python -c "from app.appspec import SPEC; print(SPEC.name)")
if ($LASTEXITCODE -ne 0 -or -not $name) { throw "app\appspec.py 를 읽지 못했습니다 (python·venv 확인)" }
$name = "$name".Trim()
$app = Join-Path $DistPath $name

# 번들 입력 준비 (받은 파일은 각 스크립트가 SHA-256 고정값과 비교한다)
if (-not (Test-Path "bin\libmpv-2.dll")) {
  Write-Host "bin\libmpv-2.dll 이 없어 받습니다 (python tools\get_libmpv.py)"
  python tools\get_libmpv.py
  if ($LASTEXITCODE -ne 0) { throw "libmpv 다운로드 실패" }
}
if (-not (Test-Path "bin\ffmpeg\ffmpeg.exe")) {
  Write-Host "bin\ffmpeg\ 가 없어 공유 빌드를 받습니다 (python tools\get_ffmpeg.py)"
  python tools\get_ffmpeg.py
  if ($LASTEXITCODE -ne 0) { throw "ffmpeg 다운로드 실패" }
}
if (-not (Test-Path "assets\icon.ico")) { throw "assets\icon.ico 가 없습니다: python tools\make_icon.py" }

# pyinstaller 는 진행 로그를 stderr 로 찍으므로 PowerShell 이 오류로 오해하지 않게 한다
# (upx 는 StreamLabeler.spec 의 upx=False 로 끈다. --noupx 는 spec 빌드에 줄 수 없는 옵션이다)
$ErrorActionPreference = "Continue"
& pyinstaller --noconfirm --clean --distpath $DistPath StreamLabeler.spec 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { throw "pyinstaller 실패 (exit $LASTEXITCODE)" }
$ErrorActionPreference = "Stop"

# 쓰지 않는 구성 요소 정리 (용량 줄이기)
$internal = Join-Path (Resolve-Path $app) "_internal"
$qt = Join-Path $internal "PySide6"
# Qt 모듈은 화이트리스트만 남긴다 (PyInstaller 가 QtQuick 계열을 통째로 끌고 온다). 위젯 앱이라 네 개면 된다.
$keepDll = @("Qt6Core", "Qt6Gui", "Qt6Widgets", "Qt6Svg")
Get-ChildItem $qt -Filter "Qt6*.dll" | Where-Object { $keepDll -notcontains $_.BaseName } | Remove-Item -Force
$keepPyd = @("QtCore", "QtGui", "QtWidgets", "QtSvg")
Get-ChildItem $qt -Filter "Qt*.pyd" | Where-Object { $keepPyd -notcontains $_.BaseName } | Remove-Item -Force
# 플러그인은 창·스타일·이미지·아이콘 네 종류만
$keepPlugins = @("platforms", "styles", "imageformats", "iconengines")
Get-ChildItem (Join-Path $qt "plugins") -Directory -ErrorAction SilentlyContinue |
  Where-Object { $keepPlugins -notcontains $_.Name } | Remove-Item -Recurse -Force
$dirs = @("PySide6/qml", "PySide6/translations", "numpy", "numpy.libs", "PIL", "yaml", "psutil", "nacl")
foreach ($d in $dirs) {
  $full = Join-Path $internal $d
  if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Recurse -Force }
}
# PyInstaller 가 ffmpeg 의 DLL 을 의존성으로 보고 _internal 루트에 한 번 더 복사한다 → 중복 제거 (ffmpeg\ 안의 것만 쓴다)
foreach ($pat in @("avcodec-*.dll", "avformat-*.dll", "avfilter-*.dll", "avdevice-*.dll", "avutil-*.dll",
                   "swscale-*.dll", "swresample-*.dll", "postproc-*.dll")) {
  Get-ChildItem $internal -Filter $pat -ErrorAction SilentlyContinue | Remove-Item -Force
}
# WebEngine 이 다시 딸려 오면(약 200MB) 빌드를 멈춘다 — spec 의 excludes 를 확인할 것
if (Test-Path (Join-Path $qt "QtWebEngineProcess.exe")) { throw "QtWebEngine 이 번들됐습니다 (StreamLabeler.spec excludes 확인)" }

# _internal\VERSION · build_info.json · deps.txt (PATH 를 비우기 전에 실행)
python tools\write_build_info.py --app $app --channel $Channel
if ($LASTEXITCODE -ne 0) { throw "build_info 생성 실패" }

# 필수 파일 확인
foreach ($f in @("$name.exe", "_internal\libmpv-2.dll", "_internal\VERSION", "_internal\build_info.json",
                 "_internal\deps.txt", "_internal\docs\labeling-guide\v0.1.md")) {
  if (-not (Test-Path (Join-Path $app $f))) { throw "빌드 결과에 $f 가 없습니다" }
}

# 검증: PATH 를 비운 상태에서 번들된 ffmpeg/ffprobe 가 자기 DLL 만으로 뜨는지
$env:PATH = "$env:SystemRoot\System32"
foreach ($exe in @("ffmpeg.exe", "ffprobe.exe")) {
  $p = Join-Path $internal "ffmpeg\$exe"
  $out = & $p -version 2>&1
  $code = $LASTEXITCODE
  $first = ($out | Select-Object -First 1)
  if ($code -ne 0) { throw "번들 $exe 실행 실패 (DLL 누락?): $first" }
  Write-Host "검증 OK: $first"
}
$files = @(Get-ChildItem $app -Recurse -File)
$size = ($files | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("완료: $app\$name.exe  ({0:N0} MB, 파일 {1}개, 채널 $Channel)" -f $size, $files.Count)
