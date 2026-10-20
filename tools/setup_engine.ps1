# 엔진 도구(engine/) 전용 venv 준비. 앱 .venv 와 섞지 않는다 (D-04, ADR-18).
#   .\tools\setup_engine.ps1              .venv-engine 만들기 + 잠금 파일로 해시 검증 설치
#   .\tools\setup_engine.ps1 -Lock        requirements-engine.in -> requirements-engine.lock 다시 만들기 (pip-compile --generate-hashes)
#   .\tools\setup_engine.ps1 -Model       설치 뒤 기본 모델(large-v3-turbo)을 받고 model.bin sha256 출력
# 모델 캐시: CHATCOACH_MODELS 가 있으면 그곳, 없으면 저장소 옆 chatcoach-data\models
param(
    [switch]$Lock,
    [switch]$Model
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$root = (Get-Location).Path
$venv = Join-Path $root ".venv-engine"
$py = Join-Path $venv "Scripts\python.exe"
# requirements-engine.in 의 한글 주석을 pip 가 cp949 로 읽다 깨지지 않게
$env:PYTHONUTF8 = "1"

if (-not (Test-Path $py)) {
    Write-Host ".venv-engine 만드는 중 (py -3.12)"
    & py -3.12 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "venv 생성 실패 (Python 3.12 필요)" }
}

if ($Lock) {
    & $py -m pip install -q pip-tools==7.6.2
    if ($LASTEXITCODE -ne 0) { throw "pip-tools 설치 실패" }
    $env:CUSTOM_COMPILE_COMMAND = ".\tools\setup_engine.ps1 -Lock"
    & (Join-Path $venv "Scripts\pip-compile.exe") --generate-hashes --allow-unsafe --strip-extras -o requirements-engine.lock requirements-engine.in
    if ($LASTEXITCODE -ne 0) { throw "pip-compile 실패" }
    Write-Host "완료: requirements-engine.lock"
}

& $py -m pip install -q --require-hashes -r requirements-engine.lock
if ($LASTEXITCODE -ne 0) { throw "설치 실패 (--require-hashes)" }
Write-Host "설치 완료: $venv"

if ($Model) {
    & $py -m engine model-hash --model large-v3-turbo
    if ($LASTEXITCODE -ne 0) { throw "모델 받기 실패" }
}
