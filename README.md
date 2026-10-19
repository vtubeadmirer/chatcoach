# StreamLabeler (가칭)

상태: M0 '빈 껍데기와 배포 경로' 구현 중 (2026-10-20) · 라벨링 기능 없음 · 공개 릴리스 없음

사람이 치지직 VOD 영상과 채팅 로그를 보며 정답 라벨을 매기는 Windows 포터블 도구다. 이 정답으로 방송 분석 엔진(AI)이 얼마나 정확한지 잰다.

지금 저장소에는 라벨링 안내서와 설계 문서만 있고 코드는 없다. 검토 의견에 따라 시범 라벨링으로 안내서를 먼저 검증하고, 도구는 그 시범에 필요한 범위(MVP)부터 만든다 ([07](docs/design/07-roadmap.md)).

## 이 도구가 하는 일

1. 치지직 VOD 채팅을 받아 원문 그대로 보관하고, 로컬 영상 파일과 시각을 맞춘다. (MVP)
2. 방송 전체를 활동 구간(저스트채팅·게임·노래·기타·판단 불가)으로 나눈다. (MVP)
3. 저스트채팅 구간 안에서 채팅과 스트리머 발화를 연결하고 증거·등급·확신도·이어짐을 적는다. (MVP)
4. 구간마다 프로그램이 뽑은 무작위 표본을 판정한다(MVP). 놓친 채팅 후보는 사유 코드와 함께 기록한다(v1).
5. 라벨을 JSONL·CSV 로 내보낸다(MVP). 재라벨 일치도와 AI 결과 비교는 v1 에서 한다.

판단 기준은 [라벨링 안내서](docs/labeling-guide/v0.1.md)가 정한다. 도구는 그 기준을 지키게 돕는다. 기준을 새로 만들지는 않는다.

## 하지 않는 일

| 하지 않는 일 | 이유 |
|---|---|
| AI 판정을 대신하거나 사람 라벨을 자동으로 채우기 | 정답은 사람이 소리를 듣고 정한다(안내서 1장 3·4항). 자동 인식 문장은 참고로만 보이고, 등급은 미리 고르지 않고 조건 충족 여부만 보인다([01](docs/design/01-analysis-and-ideas.md) 3-1) |
| 라벨링 중 AI 결과 보여 주기 | 1차 라벨을 잠근 뒤에만 AI 결과를 가져올 수 있다 |
| 가상 채팅 만들기·방송에 넣기 | 별도 프로그램이 할 일이다. 이 도구는 채팅 출처(실제·가상·판별 불가)를 판정만 한다(v1, 검토 의견 1) |
| 가상 채팅이 방송에 도움이 되는지 평가하기 | 안내서 0장의 별도 '시험 평가표'에서 다룬다 |
| 영상 다운로드·편집 | 영상은 경로와 지문으로만 참조하고, 쓰기 코드를 두지 않는다 |
| 라벨·채팅을 외부 서버로 보내기 | 시청자 개인정보가 들어 있다. 앱이 접속하는 곳은 GitHub(업데이트)와 치지직 API(채팅 수집)뿐이다(예정) |

## 문서 지도

| 문서 | 내용 |
|---|---|
| [labeling-guide/v0.1.md](docs/labeling-guide/v0.1.md) | 라벨링 안내서 v0.1 원문 (고치지 않고 보존) |
| [labeling-guide/v0.1-review.md](docs/labeling-guide/v0.1-review.md) | v0.1 에 대한 외부 검토 의견 |
| [labeling-guide/v0.2-draft.md](docs/labeling-guide/v0.2-draft.md) | 안내서 개정안 (시범 라벨링 전 초안) |
| [design/01-analysis-and-ideas.md](docs/design/01-analysis-and-ideas.md) | 안내서 분석, 제품 아이디어, 우선순위 |
| [design/02-architecture.md](docs/design/02-architecture.md) | 스택, 모듈, 폴더 구조, 데이터 흐름 |
| [design/03-data-model.md](docs/design/03-data-model.md) | 엔터티, 저장 형식, 버전, 병합 |
| [schemas/](schemas/) | 프로젝트·라벨·내보내기 JSON 스키마. 저장소 루트에 있다. 위치는 [02](docs/design/02-architecture.md) 4장, 파일 목록은 [03](docs/design/03-data-model.md) 19장이 기준이다 |
| [design/04-update-and-release.md](docs/design/04-update-and-release.md) | 릴리스 파이프라인, 업데이터, 서명 |
| [design/05-security.md](docs/design/05-security.md) | 위협 모델, 확정 항목과 완화, 개인정보 |
| [design/06-evaluation-and-agreement.md](docs/design/06-evaluation-and-agreement.md) | 일치도, AI 비교 지표, 표본 추출 |
| [design/07-roadmap.md](docs/design/07-roadmap.md) | 단계별 계획, 열린 질문 |

## 예정

- 스택: Python 3.12 · PySide6 (Widgets, QtWebEngine 제외) · libmpv(python-mpv) · ffmpeg · PyInstaller onedir. 업데이터·플레이어 등 인프라 코드는 [ClipSlicer](https://github.com/vtubeadmirer/clipslicer)에서 복사해 시작한다 ([02](docs/design/02-architecture.md)).
- 배포: 설치 없는 포터블 zip(폴더째 복사)을 GitHub Releases 로 낸다. 새 버전은 프로그램 안에서 알리고 버튼 한 번으로 바꾼다 ([04](docs/design/04-update-and-release.md)).
- 설치 위치: OneDrive 밖 사용자 폴더(예: `%LOCALAPPDATA%\Programs\StreamLabeler`)를 권장한다. 이유는 [05](docs/design/05-security.md)에 있다.
- 코드 서명이 없어 첫 실행 때 Windows SmartScreen 경고가 뜬다.

## 개발 시작

의존성은 `requirements.in`(최상위만) → `requirements.lock`(전이 의존성까지 버전·해시 고정, `tools\lock_deps.ps1` 로 생성) 이다. 설치는 항상 잠금 파일로 하고 pip 가 해시를 검사한다 ([04](docs/design/04-update-and-release.md) 4-4).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install pip==25.2
pip install --require-hashes -r requirements.lock
python tools\get_ffmpeg.py      # bin\ffmpeg\  (SHA-256 고정값과 비교)
python tools\get_libmpv.py      # bin\libmpv-2.dll (7-Zip·Bandizip 또는 py7zr 로 해제)
python tools\make_icon.py       # assets\icon.ico
python main.py
```

- 테스트: `python -m pytest -q -m "not local_only"` (CI 와 같음). 실제 robocopy·PowerShell 로 교체를 시험하는 `local_only` 묶음은 `tools\release.ps1` 이 릴리스 전에 돌린다.
- 시험 빌드: `.\build.ps1 -DistPath dist_test -Channel test` (PyInstaller onedir, 약 365MB). 실제 배포 빌드는 CI 가 `-Channel release` 로 만든다.
- 업데이트 왕복 시험(가짜 릴리스 서버): `tools\fake_release_server.py` 와 `tools\update_roundtrip.py` 의 머리 주석을 본다.
- 주의: PySide6 메타 패키지와 PySide6-Essentials 를 같은 venv 에서 바꿔 끼우면 공용 파일(`PySide6\__init__.py`)이 지워진다. 그때는 `pip install --force-reinstall --no-deps --require-hashes -r requirements.lock`.

라벨·채팅·영상은 개인정보가 들어 있어 저장소에 올리지 않는다. `.gitignore` 가 `data/` · `projects/` · `*.jsonl` 등을 이미 제외한다.

## 릴리스와 서명

버전은 저장소 루트 `VERSION` 한 파일이 정한다(`tools\sync_version.py` 가 `app\__init__.py` 와 매니페스트를 생성). 릴리스는 `tools\release.ps1` → GitHub Actions(빌드·초안 릴리스) → 개발 PC 에서 `tools\publish_release.py check` → `tools\sign_manifest.py sign`(오프라인 minisign 키, 암호 입력) → `upload-sig` → `publish` → `make-latest` 순서다 ([04](docs/design/04-update-and-release.md) 4장·13장). 앱은 내장 공개키 K1·K2 로 서명이 맞는 `latest.json` 만 믿고, 서명이 없거나 틀리면 거부한다. 공개키는 `app\appspec.py` 에 있다(첫 공개 릴리스 전에 키 ID 를 여기와 `docs/install.md` 에도 적는다).

## 이름과 라이선스

- 이름은 StreamLabeler, 저장소는 `vtubeadmirer/streamlabeler`(공개)로 정했다 (2026-10-20, [07](docs/design/07-roadmap.md) 질문 3·4). 로컬 저장소에 원격은 아직 연결되지 않았다.
- 라이선스: 앱 코드는 [MIT](LICENSE). 함께 묶는 Qt(LGPL)·ffmpeg(GPL)·libmpv(GPL) 의 고지는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 에 있다. 첫 공개 릴리스 전에 배포자가 '확인 필요' 항목을 확인한다 (07 질문 31).
