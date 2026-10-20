# tools/ 도구 목록

저장소 루트에서 `python tools/<이름>.py --help` (PowerShell 스크립트는 `.\tools\<이름>.ps1`) 로 돌린다. 앱 venv 는 `.venv`, 엔진 도구는 `.venv-engine` 을 쓴다.
새 도구를 더하면 이 표에도 한 줄을 더한다.

## 프로젝트 · 채팅 · 라벨

| 도구 | 한 줄 설명 |
|---|---|
| `new_project.py` | 새 프로젝트 폴더를 만든다 (`app.project.create`). |
| `adopt_project.py` | project.json 이 없는 기존 채팅 수집 폴더를, 그 솔트를 그대로 쓰는 앱 프로젝트로 들인다. |
| `fetch_chat.py` | 치지직 VOD 채팅을 프로젝트 폴더에 받는다 (`app.chzzk_chat.collect` 의 명령줄 껍데기). |
| `chat_window.py` | 어느 시각 주변의 채팅을 화면에 보여 주는 로컬 도우미 (손라벨·앵커 찾기용). |
| `find_anchors.py` | 시각 보정 앵커 '후보' 를 제안한다. 사람이 다시보기에서 확인한 것만 `calibrate.py` 에 넣는다. |
| `calibrate.py` | 영상 시각 = 채팅 시각 앵커로 시각 보정을 기록한다. |
| `extract_audio.py` | 영상에서 STT 용 16kHz 모노 WAV 를 뽑고 걸린 시간을 출력한다. |
| `export_labels.py` | 프로젝트의 현재 유효 라벨을 zip 하나로 내보낸다 (`app.export.export_labels`). |

## 테스트 · 점검

| 도구 | 한 줄 설명 |
|---|---|
| `make_fixture.py` | 합성 픽스처 생성기 (sha1('fixture-N')·'시청자N'·video_no 0). `--check` 로 `tests/fixtures/labels_*.jsonl` 골든이 그대로 다시 만들어지는지 본다. |
| `gui_smoke.py` | 실제 창을 띄워 작업대 단계(프로젝트 열기 → 영상 → 탐색 → 채팅 → 타임라인)를 PASS/FAIL 로 점검한다 (릴리스 전 수동). `--labeling-flow` 는 사본(project.json·guide·messages.jsonl)에서 세션 → 구간 → 반응(재확인) → 표본 → 내보내기 → 다시 열기까지 돌리고 사본을 지운다. |
| `ci_selftest.py` | 빌드된 exe 를 한글 폴더 사본에서 `--selftest` 로 점검한다 (release.yml). |
| `update_roundtrip.py` | 앱 안 업데이트 왕복 시험(04 12-5)을 자동으로 돌린다. |
| `fake_release_server.py` | 127.0.0.1 전용 가짜 릴리스 서버와 시험 릴리스 준비. |

## 빌드 · 의존성

| 도구 | 한 줄 설명 |
|---|---|
| `get_ffmpeg.py` | ffmpeg 공유 빌드를 받아 `bin/ffmpeg/` 에 넣는다 (SHA256 고정값 비교). |
| `get_libmpv.py` | libmpv-2.dll 을 고정 릴리스에서 받아 `bin/` 에 넣는다 (SHA256 고정값 비교). |
| `make_icon.py` | `assets/icon.png`·`icon.ico` 를 그린다 (Pillow). |
| `write_build_info.py` | 빌드 폴더에 VERSION·build_info.json·deps.txt 를 쓴다 (build.ps1 이 부른다). |
| `lock_deps.ps1` | `requirements.in` → `requirements.lock` (전이 의존성까지 버전·해시 고정). |
| `setup_engine.ps1` | 엔진 도구 전용 venv(`.venv-engine`)를 잠금 파일로 준비한다. 앱 venv 와 섞지 않는다. |

## 릴리스

| 도구 | 한 줄 설명 |
|---|---|
| `sync_version.py` | `VERSION` → `app/__init__.py`·`assets/ChatCoach.manifest` 를 생성한다. |
| `release.ps1` | VERSION 갱신 → 전체 테스트 → 명시 경로 커밋(추적 안 된 파일이 있으면 중단, S-21) → push → 성공했을 때만 태그. |
| `release_oneclick.py` | release.cmd 로 릴리스 전 과정(버전 → 테스트 → 태그 → 빌드 대기 → 서명 → 게시)을 한 번에 돌린다. |
| `check_tag.py` | 릴리스 태그 형식·VERSION 일치·main 포함을 검사한다 (release.yml). |
| `package_release.py` | 빌드 폴더 → 전체/경량 zip·SHA256SUMS·latest.json(서명 없음)·notes.md. |
| `make_manifest.py` | zip 에서 직접 센 해시·크기로 latest.json 을 만든다 (서명 없음). |
| `check_release_zip.py` | 릴리스 zip 을 앱과 같은 `validate_zip` 으로 검사한다. |
| `sign_manifest.py` | latest.json minisign 키 생성·서명·검증 (개발 PC 전용). |
| `publish_release.py` | 초안 릴리스 확인(해시 재계산) → 서명 업로드 → 게시 → latest 전환 (개발 PC). |
