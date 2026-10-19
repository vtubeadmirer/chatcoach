@echo off
rem 릴리스 한 번에: 더블클릭하면 tools\release_oneclick.py 가 버전, 테스트, 커밋, 태그, 빌드 대기, 서명, 게시까지 한다.
chcp 65001 >nul
cd /d "%~dp0"
set RELEASE_PAUSE=1
set PYTHONUTF8=1
".venv\Scripts\python.exe" tools\release_oneclick.py %*
