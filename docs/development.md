# 개발자·운영자 안내

README 는 처음 보는 사람을 위한 소개다. 빌드·테스트·릴리스·서명·설계 문서는 여기에 모은다.

## 지금 상태

M0 '빈 껍데기와 배포 경로' 까지 됐다 (2026-10-20). 빈 창, 앱 안 자동 업데이트(서명 필수), 빌드·릴리스 도구가 있다. 라벨링 기능은 M1 부터 만든다 ([07 로드맵](design/07-roadmap.md)).

## 기술 스택

- Python 3.12 · PySide6 (Core·Gui·Widgets, QtWebEngine 없음) · libmpv(python-mpv) · ffmpeg · PyInstaller onedir.
- 업데이터·플레이어 등 인프라 코드는 [ClipSlicer](https://github.com/vtubeadmirer/clipslicer)에서 복사해 시작했다. 복사한 파일 머리에 출처를 적었다 ([02](design/02-architecture.md) 12장).
- 배포는 설치 없는 포터블 zip 을 GitHub Releases 로 낸다. 권장 설치 위치는 `%LOCALAPPDATA%\Programs\StreamLabeler` (OneDrive 밖). 이유는 [05](design/05-security.md).
- 코드 서명이 없어 첫 실행 때 Windows SmartScreen 경고가 뜬다.

## 개발 시작

의존성은 `requirements.in`(최상위만) → `requirements.lock`(전이 의존성까지 버전·해시 고정, `tools\lock_deps.ps1` 로 생성)이다. 설치는 항상 잠금 파일로 하고 pip 가 해시를 검사한다 ([04](design/04-update-and-release.md) 4-4).

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

- 테스트: `python -m pytest -m "not local_only"` (CI 와 같음). 실제 robocopy·PowerShell 로 교체를 시험하는 `local_only` 묶음은 릴리스 때 함께 돈다.
- 시험 빌드: `.\build.ps1 -DistPath dist_test -Channel test` (약 391MB). 배포 빌드는 CI 가 `-Channel release` 로 만든다.
- 업데이트 왕복 시험(가짜 릴리스 서버): `tools\update_roundtrip.py` 머리 주석을 본다.
- 주의: PySide6 메타 패키지와 PySide6-Essentials 를 같은 venv 에서 바꿔 끼우면 공용 파일이 지워진다. 그때는 `pip install --force-reinstall --no-deps --require-hashes -r requirements.lock`.

라벨·채팅·영상은 개인정보가 들어 있어 저장소에 올리지 않는다. `.gitignore` 가 `data/` · `projects/` · `*.jsonl` 등을 제외한다.

## 릴리스

**저장소 루트의 `release.cmd` 를 더블클릭하면 된다.** 새 버전 번호(Enter 면 패치 +1)를 정하고, 메모장에 릴리스 노트를 적고, 서명 암호를 한 번 넣으면 나머지는 알아서 한다. 중간에 멈추면 다시 더블클릭해서 이어 간다. 시험만 하려면 `release.cmd --dry-run`.

안에서 하는 순서: `VERSION` 쓰기와 생성 파일 동기화 → 전체 테스트 → 커밋·push·태그 → GitHub Actions(빌드·초안 릴리스) → `tools\publish_release.py check`(해시 재계산) → `tools\sign_manifest.py sign`(오프라인 키) → 서명 업로드 → prerelease 게시 → latest 전환 ([04](design/04-update-and-release.md) 4장·13장).

## 업데이트 서명

앱은 내장 공개키 K1·K2 로 서명이 맞는 `latest.json` 만 믿고, 서명이 없거나 틀리면 업데이트를 거부한다. 공개키는 `app\appspec.py` 에 있다. 손으로 확인할 때는 `minisign -Vm latest.json -P <공개키>` 또는 `python tools\sign_manifest.py verify latest.json` 을 쓴다 (04 8장).

| 키 | 키 ID | 공개키 (minisign) |
|---|---|---|
| K1 (운용) | `F0DA83E17841C95C` | `RWRcyUF44YPa8Bfo4jqfCXr/dSMQlH853FAHFR5U58miyChvUp/iC0gp` |
| K2 (예비) | `7297CBEEF96A118F` | `RWSPEWr57suXcry8hkMgzUrqbh2tgeod3EiIJ/YGKa1NbwwrG0QA5B5u` |

## 문서 지도

| 문서 | 내용 |
|---|---|
| [labeling-guide/v0.1.md](labeling-guide/v0.1.md) | 라벨링 안내서 v0.1 원문 (고치지 않고 보존) |
| [labeling-guide/v0.1-review.md](labeling-guide/v0.1-review.md) | v0.1 에 대한 외부 검토 의견 |
| [labeling-guide/v0.2-draft.md](labeling-guide/v0.2-draft.md) | 안내서 개정안 (시범 라벨링 전 초안) |
| [design/01-analysis-and-ideas.md](design/01-analysis-and-ideas.md) | 안내서 분석, 제품 아이디어, 우선순위 |
| [design/02-architecture.md](design/02-architecture.md) | 스택, 모듈, 폴더 구조, 데이터 흐름, 성능 측정값 |
| [design/03-data-model.md](design/03-data-model.md) | 엔터티, 저장 형식, 버전, 병합 |
| [../schemas/](../schemas/) | 프로젝트·라벨·내보내기·업데이트 매니페스트 JSON 스키마 |
| [design/04-update-and-release.md](design/04-update-and-release.md) | 릴리스 파이프라인, 업데이터, 서명 |
| [design/05-security.md](design/05-security.md) | 위협 모델, 확정 항목과 완화, 개인정보 |
| [design/06-evaluation-and-agreement.md](design/06-evaluation-and-agreement.md) | 일치도, AI 비교 지표, 표본 추출 |
| [design/07-roadmap.md](design/07-roadmap.md) | 단계별 계획, 열린 질문 |

## 라이선스

앱 코드는 [MIT](../LICENSE). 함께 묶는 Qt(LGPL)·ffmpeg(GPL)·libmpv(GPL) 의 고지는 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 에 있다. 첫 공개 릴리스 전에 배포자가 '확인 필요' 항목을 확인한다 (07 질문 31).
