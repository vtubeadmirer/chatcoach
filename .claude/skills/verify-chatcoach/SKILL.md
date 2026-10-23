---
name: verify-chatcoach
description: "ChatCoach(PySide6 데스크톱 앱 — 치지직 VOD 채팅 라벨링 작업대·세션 잠그기·일치도·분석·스트리머/시청자 훈련) 를 실제 MainWindow 로 띄워 사용자 흐름을 몰고 PASS/FAIL 줄·스크린샷·summary.json 증거를 남긴다. 화면·메뉴·작업대·훈련 화면 코드를 고친 뒤 '앱에서 실제로 되는지', 릴리스 전 GUI 스모크, gui_smoke 가 멈추거나 실패했을 때 쓴다. Drives the real ChatCoach desktop app with tools/gui_smoke.py via tools/verify_chatcoach.py."
---

# verify-chatcoach

표면은 PySide6 데스크톱 앱 하나(`main.py` → `app.ui.mainwindow.MainWindow`: 프로젝트 화면 · 작업대 ①구간 ②반응 ③표본 ④후보 · 메뉴 파일/세션/보고/분석/훈련). 하네스는 `tools/gui_smoke.py` — 같은 프로세스에서 실제 MainWindow 를 만들고, 메뉴 `QAction.trigger()`·작업대/폼 메서드·세션 선택 화면·QTest 진짜 마우스/키로 몰아 단계마다 `PASS|FAIL <단계> <ms> ms <숫자>` 한 줄을 찍는다. 껍데기 `tools/verify_chatcoach.py` 가 감시(smoke_watch)·증거 폴더·부수 효과 검사를 더한다. 기능별 길은 [features/README.md](features/README.md).

## 먼저 지킬 것

- **시범 자리는 열지도 쓰지도 않는다**: `C:\Users\dlem9\dev\chatcoach-data\projects\pilot-*` · `chatcoach-data\ai\pilot-*` · `chatcoach-data\pilot` · `chatcoach-data\sealed-work` · `Videos\ChatCoach-pilot`. 사용자가 거기서 라벨링 중이다. 껍데기는 경로 글자만 보고 종료 2.
- `<P>` = `C:\Users\dlem9\dev\chatcoach-data\projects\p15767532` (개발용 실제 데이터)는 **읽기만**. 흐름은 gui_smoke 가 `%LOCALAPPDATA%\ChatCoachSmoke\chatcoach_smoke_*` 사본에서 돌고 지운다. 기본 단계는 원본 `<P>` 를 보기 전용으로 열어서, 잠깐 생겼다 지워지는 파일 둘만 남긴다: 쓰기 시험 `.write_test_<pid>`(경로 점검)와 열기 잠금 `locks\open.lock`. 그래서 `<P>` 와 `<P>\locks` 의 폴더 수정 시각은 바뀌지만 파일은 그대로다 — `close` 단계(project.json 그대로)와 껍데기의 파일 전후 대조(`<P> 파일 바뀜 0 · 더해짐 0 · 빠짐 0`)가 확인한다.
- 실제 채팅 본문·닉네임·전사문·방송 제목·영상 이름을 저장소·스킬·보고에 쓰지 않는다. 숫자·단계 이름·코드만. 영상은 `<V1>` 이라고 부른다.
- 스크린샷(실제 채팅이 찍힐 수 있다)은 `C:\Users\dlem9\dev\chatcoach-data\screens\` 아래에 `verify-*.png` 로만. gui_smoke 는 그 밖 경로를 거부한다.
- `python main.py` 를 맨 몸으로 띄우지 않는다: 저장소 `data\`(사용자의 실제 데이터 폴더 — 시범 라벨링도 이 폴더)를 쓰고, 마지막 프로젝트·세션(시범일 수 있다)을 이어서 열고, 단일 인스턴스 잠금이 사용자 앱과 겹친다.
- commit·push 하지 않는다(사용자가 시킬 때만).

## Launch

gui_smoke 가 곧 런처다(따로 띄워 둘 서버 없음). 실행마다 새 MainWindow 를 만들고(업데이트 확인 끔) 끝에 닫는다.

```powershell
Set-Location C:\Users\dlem9\dev\chatcoach
.venv\Scripts\python tools\verify_chatcoach.py --feature labeling                                  # offscreen (기본)
.venv\Scripts\python tools\verify_chatcoach.py --feature training --window --window-size 1366x768  # 실제 창
```

- 빈 데이터 폴더에서 뜬다: 실행마다 `%LOCALAPPDATA%\ChatCoachSmoke\chatcoach_smoke_vdata_<시각>` 을 `--data-dir` 로 주고 끝에 지운다. 라벨러 명부(L01·L02·L99)·machine.json·설정은 흐름이 만든다. 마이그레이션·시드 단계는 없다(`<P>` 가 시드다).
- 영상 `<V1>`: `--video` → 환경 변수 `CHATCOACH_VERIFY_VIDEO` → `C:\Users\dlem9\dev\chatcoach-data\config\verify-video.txt` 첫 줄(`#` 아님). `base` 말고는 모두 영상이 있어야 한다(없으면 종료 2).
- **준비됨** = `PASS window` 다음 `PASS open_project … read_only=False session_asked=True view_only=True`. 둘 중 하나가 FAIL 이면 gui_smoke 가 뒤 단계를 건너뛴다 — 그 실행은 증거가 아니다.
- Teardown 은 자동: gui_smoke 가 창을 닫고(`close`: 잠금 풀림·project.json 그대로) 사본을 지우고(`*_cleanup`) 색인 캐시를 지우고(`cache_drop`), 껍데기가 데이터 폴더를 지운다. `--max-time`(기본 1200초)을 넘기면 smoke_watch 가 자식 나무를 끝내고 124.
- gui_smoke 를 직접 돌려도 된다(감시·부수 효과 검사 없음, 공용 데이터 폴더 `%LOCALAPPDATA%\ChatCoachSmoke\data`): `.venv\Scripts\python tools\gui_smoke.py --project <P> --video <V1> --offscreen --store-copy --labeling-flow --screenshot C:\Users\dlem9\dev\chatcoach-data\screens\verify-x.png`

## Doctor

```powershell
.venv\Scripts\python tools\verify_chatcoach.py --doctor [--feature <기능>]
```

읽기만 한다. 줄마다 `OK|WARN|FAIL <이름> <숫자>`, 끝 `== doctor: ok n · warn n · fail n`, FAIL 이 있으면 종료 1. 보는 것: `.venv` 파이썬 · PySide6 · python-mpv · `bin\` (ffmpeg·ffprobe·libmpv-2.dll) · 앱 버전·git HEAD·바뀐 파일 수 · `<P>` 와 기능별 입력 파일(이름만) · 영상(어디서·크기) · 증거 자리 · 남은 스모크 사본·예전 TEMP 사본 · 공용 스모크 설정의 `lmstudio/model` · **지금 도는 gui_smoke 와 그 Suspended 스레드 수** · 사용자 앱(main.py) 실행 여부(건드리지 않음).

- `smoke_running FAIL` → 두 번 몰지 않는다(사본 뿌리·공용 데이터 폴더를 나눈다). 그 줄에 `밖에서 멈춤으로 보임` 이 붙으면 외부 정지(아래 주의).
- 무엇이든 이상해 보이면 doctor 부터 다시.

## Drive

| `--feature` | gui_smoke 인자 | 지도 |
|---|---|---|
| `base` | (기본 단계만) | [labeling-workbench](features/labeling-workbench.md) |
| `labeling` | `--store-copy --labeling-flow` | [labeling-workbench](features/labeling-workbench.md) |
| `agreement` · `adjudication` | `--agreement-flow` · `--adjudication-flow` | [lock-agreement](features/lock-agreement.md) |
| `analysis` | `--analysis-flow` | [seal-side-by-side](features/seal-side-by-side.md) |
| `training` | `--training-flow` | [streamer-training](features/streamer-training.md) |
| `viewer` · `discern` | `--viewer-flow` · `--discern-flow` | [viewer-discern](features/viewer-discern.md) |
| `all` | 위 전부 — 끝에 `== 단계 목록 … missing=[]` 대조 | 전부 |

- `--feature` 는 여러 번 줄 수 있다(`--feature analysis --feature training`). `all` 은 offscreen 약 150~200초.
- 기본 단계는 늘 먼저 돈다: `window → open_project → chat_loaded → timeline_density → video_load → waveform → seek → chat_activate → play → timeline_zoom → chat_filter → store_copy → screenshot → (흐름들) → close → cache_drop`. 켜지 않은 흐름은 `PASS <흐름>_flow 0 ms skip (…)` 한 줄 — 껍데기는 `skip` 으로 따로 센다. skip 은 검증이 아니다.
- 줄 모양: 단계 `PASS|FAIL <단계> <ms> ms  <숫자 칸>` · 진행 `  .. <단계> …`(PASS/FAIL 아님) · gui_smoke 끝 `== n passed, m failed, s, platform=…` · 껍데기 끝 `== verify: … rc=… pass= fail= skip=` · `== 증거: …` · `== 부수 효과: …`.
- 실제 창(`--window`)이 필요할 때: 영상 그림(mpv 는 자기 네이티브 창에 그려 offscreen 은 vo=null·검은 칸), 실제 글꼴 배치(offscreen 은 한글이 네모, 구간 폼 `hscroll=y outside=21` 은 offscreen 만의 값), 키 초점(offscreen 은 Space 가 `via=fallback` 일 수 있음), 1366×768 잘림(`--window-size 1366x768` 의 `fit=…/clipped=0/outside=0`).
- 새 기능을 몰 때는 gui_smoke 에 단계를 더한다: 메뉴 QAction 또는 화면 부품 메서드로 열고, 결과를 파일로 다시 읽어 숫자만 찍는다(기존 `step_*` 를 본보기로). 이 지도에도 줄을 더한다.

종료 코드(껍데기): `0` 모두 통과 · `1` FAIL 또는 사본 남음·`<P>` 바뀜 · `2` 거부(시범 자리·입력 없음·다른 스모크 돎) · `124` 시간 초과 · `125` 끝 요약 줄 없음(끝까지 못 감).

## Evidence

실행마다 새 폴더 `C:\Users\dlem9\dev\chatcoach-data\screens\verify-<기능>-<YYYYMMDD-HHMMSS>\`:

- `verify-<기능>.png`(기본 화면) + 흐름 스크린샷 `…_flow_segments` · `_flow_edit` · `_flow` · `_agree_b` · `_agree_analysis` · `_adj_b` · `_adj_missed` · `_adj` · `_adj_origin` · `_an_side` · `_an_summary` · `_tr_train` · `_tr_compare` · `_tr_virtual`. 시청자 요약·가려내기 정답 화면은 일부러 찍지 않는다(본문이 보인다). 실제 창 캡처에는 실제 채팅이 찍힌다 — 이 폴더 밖으로 옮기지 말고 저장소·보고에 붙이지 않는다.
- `run.log` 자식 출력 전부 · `watch\watch.log` 2초마다 스레드·Suspended·CPU(첫 `cmd` 줄에 영상 경로가 있다) · `watch\stack.txt` 멈춤 때 스택(파일·함수·줄 번호만) · `summary.json` rc·pass/fail/skip·단계별 ok/ms·external_suspend·멈춤·스크린샷 수·부수 효과(숫자·이름만).
- 증명 기준: 단계 줄은 사용자 행동과 그 결과 상태를 같이 담는다(예 `flow_export rows=5 by_kind={'segment': 2, 'reaction': 3} rows_match_effective=True`). 부수 효과는 파일로 확인한다 — gui_smoke 가 내보내기 zip·다시 연 세션·기록 줄·보호 구역·노출을 다시 읽고, 껍데기가 `<P> 파일 바뀜 0 · 남은 사본 0` 을 본다. 화면만 보고 통과라 하지 않는다.
- 가짜인 것(이 스킬로 증명되지 않음): 분석 흐름의 엔진 결과·주제 흐름(합성), 가려내기의 LM Studio(127.0.0.1 가짜 서버 `engine.llm_fake`), 판정·글 입력값(합성), 폴더 고르기·확인 상자(바꿔 끼움). 실제 LM Studio·실제 엔진 품질은 따로다.
- 보고에는 `pass/fail/skip` 숫자, 실패 단계 이름, 증거 폴더 경로만.

## Cleanup

```powershell
.venv\Scripts\python tools\verify_chatcoach.py --cleanup
```

- 끝내는 것: 증거 폴더 `watch.log` 에 pid 는 있고 `end` 줄이 없는(껍데기가 중간에 죽은) 우리 실행의 gui_smoke 나무만, 그 pid 의 명령줄에 지금도 `gui_smoke.py` 가 있을 때. 이름으로 프로세스를 죽이지 않는다. 남이 띄운 gui_smoke 는 건드리지 않고, 그때는 1시간 넘은 사본만 지운다.
- 지우는 것: `%LOCALAPPDATA%\ChatCoachSmoke\chatcoach_smoke_*`(사본·실행 데이터 폴더 — 실제 채팅 사본이라 `shutil.rmtree`, 휴지통 금지) · 공용 스모크 데이터의 `<P>` 채팅 색인 캐시 · 공용 스모크 `settings.ini` 의 `lmstudio/*`(실제 127.0.0.1:1234 에 붙지 않게).
- 지우지 않는 것: 증거 `screens\verify-*` · `<P>`(남은 `locks\open.lock` 은 보고만) · 예전 TEMP 사본(세기만, 사용자 확인 뒤) · 저장소 `data\`.
- 실패한 반복 뒤에도 매번 돌린다. 끝 줄 `증거는 그대로: … n개 (가장 새 것 …)` 로 증거가 남았는지 확인한다. `· 끝나지 않은 실행 k개(summary.json 없음 — 증거 아님)` 가 붙은 폴더는 중간에 끊긴 실행의 기록일 뿐 통과 증거가 아니다.
- 껍데기가 중간에 죽으면(창을 닫음·Ctrl+C·프로세스 끝냄) gui_smoke 자식과 사본이 남는다 — doctor 가 `smoke_running FAIL` · `smoke_copies WARN` 을 보이고, `--cleanup` 이 그 나무만 끝내고 사본을 지운다(2026-10-23 에 일부러 끊어 확인: 나무 2개 끝냄, 사본 2개 지움, `<P>` 에 잠금 남지 않음).
- 증거 폴더 버리기는 사용자가 본 뒤, 사용자가 한다(실제 채팅 캡처일 수 있다).

## Helpers

- `tools/verify_chatcoach.py` — `--doctor` · (기본) 몰기 · `--cleanup`. 옵션 `--feature <id>`(여러 번) `--project` `--video` `--window` `--window-size WxH` `--timeout 초`(단계 하나, 기본 60) `--stall 초`(기본 30) `--max-time 초`(기본 1200). `--help`.
- `tools/gui_smoke.py` — 하네스 본체, `--help`. 머리말에 흐름마다 하는 일. 혼자 돌릴 때 `--faulthandler-file <경로>` 로 스택.
- `.venv\Scripts\python tools\smoke_watch.py --summarize <증거 폴더>\watch` — 멈춘 실행 읽기: 멈춤마다 앞뒤 단계·CPU 초/초·Suspended·판정 한 마디, 덤프마다 gui_smoke `step_*` 와 줄.
- `.venv\Scripts\python -m pytest tests\test_smoke_watch.py -q` — 감시 도구 자체 시험.

## 주의 (Gotchas)

- **외부 정지 — Acronis Active Protection**: 보안 프로그램이 python.exe 를 밖에서 통째로 일시 정지한다(스레드 Suspended, CPU 0). 코드 교착이 아니다. 징후: `watch.log` 의 `smp … MOSTLY-SUSPENDED|ALL-SUSPENDED`, 덤프 요청은 있는데 받음 0, 단계 줄 끝 `external_suspend=…s`, gui_smoke 끝 `== external_suspend: …`, doctor `smoke_running … 밖에서 멈춤으로 보임`. 실제 창에서 조정 흐름 사본(`adj_copy`) 직후에 여러 번 났다(Suspended 17/19). 할 일: 기다리거나 `--max-time` 에 맡기고 `--cleanup` → 다시 한 번. 근본 해결은 Acronis 신뢰 목록에 `.venv`·`.venv-engine` 의 python.exe 를 넣는 것(사용자 몫). 60초 넘게 멈췄다 풀리면 가짜 `Timeout (` 덤프가 생길 수 있다.
- smoke_watch 혼자는 밖에서 멈춰 끝까지 못 간 실행을 rc 0 으로 보고할 수 있다 — 껍데기는 끝 요약 줄이 없으면 125.
- `flow_reaction` 은 2배속 두 번 듣기로 약 42초 걸린다(진행 줄 `  .. flow_reaction 다시 듣기 n/2`). 멈춤이 아니다.
- `agree_report` 가 가끔 `judged_both=0` 으로 실패한다(오락가락, 원인 미확인). 한 번 다시 돌려 또 실패하면 그대로 보고한다. 다시 돌려 통과한 것을 '고침' 으로 적지 않는다.
- offscreen 스크린샷은 영상 칸이 검고 글자가 네모다 — 배치 증거일 뿐 글자·영상 증거가 아니다. 실제 창 캡처는 화면 영역을 찍어 위에 다른 창이 있으면 같이 찍히고, 실제 창은 사용자 화면에 떠서 초점을 가져간다.
- `<P>` 에 `locks\open.lock` 이 남아 있으면 앱이 열고 있거나 지난 실행이 죽은 것 — 지우지 말고 사용자 앱 실행 여부부터 본다.
- 훈련·가려내기는 `<P>\labels\manual-*.csv`, 시청자 훈련은 `<P>\derived\asr.json`, 가려내기는 `<P>\chat\protected\identities.jsonl` 이 있어야 한다. 없으면 `tr_copy`·`vw_copy`·`ds_copy` 가 FAIL — 흐름을 약하게 고치지 말고 그 이유를 보고한다.
- 단계 목록 대조(`== 단계 목록 … missing=[]`)는 `--feature all`(store-copy·labeling·agreement·adjudication 을 다 켰을 때)에만 나온다.
- 기능 지도는 앱이 바뀌면 같이 고친다(`/pstack:maintain-verification-skill`).
