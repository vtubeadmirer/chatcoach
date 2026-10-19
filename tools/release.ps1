# vendored from vtubeadmirer/clipslicer@07d13cc (tools/release.ps1) — 변경: VERSION 쓰기 → sync_version.py, git add -A 대신 add -u + 명시 경로, 추적 안 된 파일 검사(S-21), main 브랜치 확인, 역이식 점검 출력
# 릴리스 한 번에: VERSION 갱신 → 생성 파일 → 전체 테스트 → 커밋 → push → (커밋·push 가 성공했을 때만) 태그 → 태그 push.
#   .\tools\release.ps1 -Version 0.0.2 -MessageFile msg.txt
# 태그가 push 되면 GitHub Actions(release.yml)가 빌드해 초안(draft) Release 를 만든다. 서명·게시는 개발 PC 에서 한다
# (docs/design/04-update-and-release.md 4장·13장). release.yml 도 태그·VERSION·생성 파일이 다르면 멈춘다(tools/check_tag.py).
param(
  [Parameter(Mandatory = $true)][string]$Version,
  [Parameter(Mandatory = $true)][string]$MessageFile,
  [switch]$SkipTests
)
$ErrorActionPreference = "Stop"

# 커밋 메시지 파일은 호출한 위치 기준으로 먼저 찾는다 (저장소 루트로 옮기기 전에)
if (-not (Test-Path -LiteralPath $MessageFile)) { throw "커밋 메시지 파일이 없습니다: $MessageFile" }
$msgPath = (Resolve-Path -LiteralPath $MessageFile).Path
Set-Location (Split-Path $PSScriptRoot -Parent)
$root = (Get-Location).Path

# ASCII 숫자만 (.NET 의 \d 는 다른 문자권 숫자도 받는다)
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw "버전은 0.0.2 같은 형식이어야 합니다: $Version" }
$tag = "v$Version"

$appName = (& python -c "from app.appspec import SPEC; print(SPEC.name)")
if ($LASTEXITCODE -ne 0 -or -not $appName) { throw "app\appspec.py 를 읽지 못했습니다 (python·venv 확인)" }
$appName = "$appName".Trim()

$branch = (git rev-parse --abbrev-ref HEAD)
if ($branch -ne "main") { throw "main 브랜치에서만 릴리스합니다 (지금: $branch)" }
if (git tag --list $tag) { throw "로컬에 이미 $tag 태그가 있습니다" }
$remoteTag = (git ls-remote origin "refs/tags/$tag")
if ($LASTEXITCODE -ne 0) { throw "GitHub 태그 확인 실패 (네트워크·인증 확인)" }
if ($remoteTag) { throw "GitHub 에 이미 $tag 태그가 있습니다" }

# 직전 태그 (역이식 점검용). 새 태그를 만들기 전에 읽어 둔다.
# PowerShell 5.1 은 네이티브 명령의 stderr 를 리디렉션하면 Stop 에서 멈추므로 이 줄만 Continue 로 돌린다.
$ErrorActionPreference = "Continue"
$prevTag = (git describe --tags --abbrev=0 2>$null)
if ($LASTEXITCODE -ne 0) { $prevTag = $null }
$ErrorActionPreference = "Stop"

# VERSION 한 곳만 고치고 (BOM 없음, LF) 생성 파일 두 개를 만든다
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $root "VERSION"), "$Version`n", $utf8)
python tools\sync_version.py
if ($LASTEXITCODE -ne 0) { throw "sync_version.py 실패 — 릴리스 중단" }
Write-Host "버전 → $Version"

if (-not $SkipTests) {
  # 로컬 전용(local_only: 실제 robocopy·PowerShell) 테스트까지 전부
  python -m pytest -q
  if ($LASTEXITCODE -ne 0) { throw "테스트 실패 — 릴리스 중단 (버전 변경은 작업 트리에 남아 있음)" }
}

# 추적 안 된 파일이 있으면 멈춘다 (S-21: 실제 채팅·라벨 파일이 딸려 올라가지 않게). 아래에서 명시해 넣는 세 파일은 뺀다.
$explicit = @("VERSION", "app/__init__.py", "assets/$appName.manifest")
$untracked = @(git status --porcelain | Where-Object { $_.StartsWith("?? ") } |
  Where-Object { $explicit -notcontains $_.Substring(3).Trim('"') })
if ($untracked.Count -gt 0) {
  Write-Host "추적 안 된 파일:"
  $untracked | ForEach-Object { Write-Host "  $_" }
  throw "추적 안 된 파일이 있습니다 — 커밋할 것은 git add 로 넣고, 아닌 것은 지우거나 .gitignore 에 넣은 뒤 다시 실행하세요"
}

git add -u
if ($LASTEXITCODE -ne 0) { throw "git add -u 실패" }
git add -- VERSION app\__init__.py "assets\$appName.manifest"
if ($LASTEXITCODE -ne 0) { throw "git add (버전 파일) 실패" }
git commit -q -F $msgPath
if ($LASTEXITCODE -ne 0) { throw "커밋 실패 — 태그를 만들지 않고 중단" }
$sha = (git rev-parse --short HEAD)
git push -q origin main
if ($LASTEXITCODE -ne 0) { throw "push 실패 — 태그를 만들지 않고 중단" }
git tag $tag -m "$appName $Version"
if ($LASTEXITCODE -ne 0) { throw "태그 생성 실패" }
git push -q origin $tag
if ($LASTEXITCODE -ne 0) { throw "태그 push 실패" }
Write-Host "완료: $sha → $tag push. GitHub Actions 가 빌드해 초안 Release 를 만듭니다."

# 역이식 점검 (02 12-4): ClipSlicer 에서 가져와 고친 파일 중 업데이트 경로에 걸린 것
$watch = @("app/updater.py", "app/paths.py", "app/minisign.py")
if ($prevTag) {
  $changed = @(git diff --name-only $prevTag HEAD -- $watch)
  $since = "$prevTag 이후"
} else {
  $changed = @($watch | Where-Object { Test-Path -LiteralPath (Join-Path $root $_) })
  $since = "첫 릴리스 — 전부"
}
if ($changed.Count -gt 0) {
  Write-Host ""
  Write-Host ("역이식 검토: ClipSlicer 에도 같은 수정이 필요한지 보세요 — " + ($changed -join ", ") + " ($since)")
}
