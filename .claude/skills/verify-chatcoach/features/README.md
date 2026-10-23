# ChatCoach 검증 지도

이 폴더는 ChatCoach 의 사용자 기능을 검증하는 유지 대상 원본이다. 앱을 몰기 전에 이 색인을 읽고, 맞는 기능 파일을 몰기 순서로 쓴다. 몰기는 모두 `tools/verify_chatcoach.py --feature <id>` (속은 `tools/gui_smoke.py`, 감시는 `tools/smoke_watch.py`).

## 기본 전제

- 저장소 루트 `C:\Users\dlem9\dev\chatcoach` 에서 앱 venv `.venv\Scripts\python` 으로 돌린다.
- 프로젝트 `<P>` = `C:\Users\dlem9\dev\chatcoach-data\projects\p15767532` (읽기만). 흐름마다 gui_smoke 가 한글 경로 사본 `%LOCALAPPDATA%\ChatCoachSmoke\chatcoach_smoke_<흐름>_…\한글 사본\…\프로젝트` 를 만들고 끝에 지운다.
- 영상 `<V1>` 은 `C:\Users\dlem9\dev\chatcoach-data\config\verify-video.txt` 첫 줄(이름은 저장소에 적지 않는다).
- 실행마다 빈 데이터 폴더(`--data-dir`, 껍데기가 만들고 지운다).
- `--doctor` 가 `fail 0` 이고, 다른 gui_smoke 가 돌고 있지 않다.
- 시범 자리(`projects\pilot-*` · `ai\pilot-*` · `chatcoach-data\pilot` · `sealed-work` · `Videos\ChatCoach-pilot`)는 어떤 흐름에도 주지 않는다.

## 몰기 약속

- 흐름은 서로 상태를 나누지 않는다: 흐름마다 새 사본에서 시작하고 끝에 `*_cleanup` 단계로 지운다.
- 고정 핸들은 gui_smoke 단계 이름(`flow_segments`, `tr_pick` …)과 앱 메뉴 이름('세션 → 이 세션 잠그기…')이다. 좌표·탭 순서에 기대지 않는다.
- gui_smoke 는 메뉴를 `QAction.trigger()` 로, 작업대·폼·편집기를 그 부품의 메서드로, 세션 선택은 실제 `SessionDialog` 로, 훈련·시청자·가려내기·주제 트랙·요약 행은 QTest 진짜 마우스/키로 몬다. 폴더 고르기·확인 상자 같은 모달은 바꿔 끼운다.
- 명령은 글자 그대로 쓴다.

## 증거와 건너뜀 보고

- 증거는 `C:\Users\dlem9\dev\chatcoach-data\screens\verify-<기능>-<시각>\` 의 `summary.json` · `run.log` · `watch\` · `verify-*.png`.
- 단계 줄에는 행동과 결과 상태가 숫자로 같이 있다. 부수 효과(파일 기록·내보내기·보호 구역·노출)는 gui_smoke 가 파일을 다시 읽어 확인한다.
- 훈련·시청자·가려내기 흐름은 사람 클릭 수(`clicks=`)·키 수(`keys=`)를 찍는다 — 사용자 길의 비용 증거다.
- `skip (…)` 줄은 검증이 아니다. 켜지 않은 흐름을 다른 흐름으로 검증했다고 적지 않는다. 입력이 없어 못 간 흐름은 그 단계 이름과 없는 입력을 보고한다.

## 기능 항목 약속

기능 파일은 H1 제목과 사용자에게 보이는 동작 한 단락으로 시작하고, 아래 네 H2 를 이 차례로 쓴다.

1. `Sub-features` — 짧은 ID(= gui_smoke 단계 이름)마다 한 줄.
2. `How to get to it (user POV)` — 사용자가 들어가는 모든 길(메뉴·단추·단축키).
3. `Driving it with gui_smoke` — `Preconditions:` 로 시작하고, 사용자 행동 · 정확한 명령/단계 · 보이는 결과(숫자 칸)를 짝지은 굵은 머리 글머리표.
4. `Gotchas` — 검증을 낭비하거나 무효로 만드는 함정.

구현 세부는 넣지 않는다. 사용자 길, 고정 핸들, 필요한 상태, 명령, 보이는 증거만 적는다. 실제 채팅 본문·닉네임은 적지 않는다.

## 기능

- [라벨링 작업대](./labeling-workbench.md) — 프로젝트 열기·영상·탐색·채팅·타임라인(기본 단계)과 ① 구간 · ② 반응 · ③ 표본 · 내보내기 · 다시 열기.
- [세션 잠그기 · 일치도 · 조정](./lock-agreement.md) — 1차 잠그기, 재라벨 계획, 두 번째 사람(블라인드), 일치도 보고서, 분석 세션, ④ 놓친 후보, 조정, 계획 취소, 출처 판정.
- [분석](./seal-side-by-side.md) — AI 결과 봉인 가져오기, 해제 거부·해제, 라벨링 세션 블라인드, 엔진과 나란히 보기, 주제 트랙, 방송 요약, AI 비교 보고서.
- [스트리머 훈련](./streamer-training.md) — 메뉴 한 번으로 소리 끈 다시보기, 고르기·취소, 견주기, 듣기, 기록·노출, 가상 채팅 만들기·섞기.
- [시청자 훈련 · 가상 채팅 가려내기](./viewer-discern.md) — 채팅 쓰기·채점·구간 끝 요약, 가짜 LM Studio 로 생성, 가려내기 판정·정답·숫자 대조.
