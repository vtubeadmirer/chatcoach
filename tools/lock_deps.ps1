# requirements.in(최상위 의존성) → requirements.lock(전이 의존성까지 버전·해시 고정). docs/design/04-update-and-release.md 4-4.
#   .\tools\lock_deps.ps1
# 설치는 pip install --require-hashes -r requirements.lock (CI · 로컬 모두)
# 저장소의 .venv 파이썬으로 실행한다. requirements.in 을 고친 뒤 한 번 돌리고 두 파일을 같이 커밋한다.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$root = (Get-Location).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw ".venv 가 없습니다: py -3.12 -m venv .venv" }

& $py -m pip install -q pip-tools==7.6.2
if ($LASTEXITCODE -ne 0) { throw "pip-tools 설치 실패" }

# 잠금 파일 머리의 '이 명령으로 만들었다' 줄에 이 스크립트 이름이 찍히게 한다
$env:CUSTOM_COMPILE_COMMAND = ".\tools\lock_deps.ps1"
$pipCompile = Join-Path $root ".venv\Scripts\pip-compile.exe"
& $pipCompile --generate-hashes --allow-unsafe --strip-extras -o requirements.lock requirements.in
if ($LASTEXITCODE -ne 0) { throw "pip-compile 실패" }
Write-Host "완료: requirements.lock (설치: pip install --require-hashes -r requirements.lock)"
