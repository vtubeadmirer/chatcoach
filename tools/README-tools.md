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
| `find_anchors.py` | 시각 보정 앵커 '후보' 를 제안한다. 사람이 다시보기에서 확인한 것만 `calibrate.py` 에 넣는다. `--json` 은 표·본문 없이 후보 수 같은 숫자만 JSON 한 줄, `--list` 를 더하면 후보 rows(chat_id·시각·근거 코드·점수, 본문 없음). `--locate`(`--asr` 필요)는 영상이 VOD 의 어디인지 모를 때 먼저 자리를 찾는다(08 D-32 — 13시간·채팅 2만6천 건에서 약 1초). asr 시작은 asr.json 이 기억하는 값(아래 '시각 입력 두 가지', 08 D-33). |
| `calibrate.py` | 영상 시각 = 채팅 시각 앵커로 시각 보정을 기록한다(차이 중앙값 그대로 — 읽기 지연을 빼지 않는다). 앱의 '파일 → 시간 정렬(보정)…' 은 앵커를 들어 확정하고 읽기 지연 6.7초를 뺀다(08 D-32). |
| `extract_audio.py` | 영상에서 STT 용 16kHz 모노 WAV 를 뽑고 걸린 시간을 출력한다. |
| `export_labels.py` | 프로젝트의 현재 유효 라벨을 zip 하나로 내보낸다 (`app.export.export_labels`). |
| `make_scenario.py` | 가상 채팅 시나리오(retrieved, H-2)를 `scenarios\` 에 만든다. `--generated <생성 후보>`(H-3, `python -m engine generate --out` 이 만든 파일)를 주면 retrieved 와 합치고 G 채점기(`app.core.gen_filter`)로 한 번 더 거른다 — 기본 `--generated-mode replace` 는 생성 몫(`--max-generated-ratio`, 기본 0.25)을 먼저 잡고 그 창의 점수 낮은 retrieved 를 뺀다(창 목표·가상 비율 상한은 그대로). 이 도구는 LLM 을 부르지 않는다. 시범 프로젝트는 거부(`--generated`·`--topic-flow`·`--blocklist`·`--projects-root` 가 시범 폴더 안이어도 읽기 전에 종료 2). `--generated` 는 원 닉네임 목록(`chat\protected\identities.jsonl`)이 없으면 `nicknames_missing` 종료 1(`--allow-no-identities` 로만 진행, 경고). 금칙어는 저장소 밖 `config\blocklist-ko.txt`(해시만 기록). |
| `gen_e2e.py` | **H-3 생성 끝에서 끝까지 스모크(가짜 LLM, 사람 클릭 0)**: 원 프로젝트(기본 개발용 `p15767532`, 읽기만)의 필요한 파일만 `%LOCALAPPDATA%\ChatCoachSmoke\gen-e2e-*` 사본으로 → `engine generate --fake`(고정 합성 응답) · `engine generate --model … --url http://127.0.0.1:<가짜 서버>`(`engine.llm_local` → `engine.llm_fake.FakeLLMServer`, 받은 자료 낱말로 합성 줄) → `make_scenario --generated` → 스트리머 훈련 화면에 섞여 흐름(가·생 표시) → 시나리오·생성 후보·보낸 기록·보낸 요청·훈련 기록 바이트 검사(실제 닉네임·viewer_id·user_id_hash·가림 닉네임·본문 0건). 소켓 연결은 가짜 서버 포트만(그 밖은 막고 셈). 시범(pilot-*·시범 기록)은 아무것도 열기 전에 종료 2. 끝나면 `shutil.rmtree`. 출력은 숫자·코드만. 종료 0 = 검사 모두 통과. |
| `discern_report.py` | **H-5 가려내기 관문(대표값)**: `--projects-root <…\projects>`(또는 `--project` 여러 번) `[--labeler L01] [--sessions]` — 프로젝트(= 방송)마다의 `discern\*.jsonl` 을 다시 읽어(정답은 판정 키 → start 줄이 가리킨 시나리오 파일의 source, 바이트 해시가 다르면 그 세션을 뺌) `app.core.discern.evaluate_sessions` 로 혼동표·κ·탐지율·관문(pass·fail·low_n·unknown)을 낸다. 화면은 세션 하나라 늘 '표본 적음' 이라 통과·실패는 이 도구로만 나온다. 다시 봄 세션은 뺀다. 시범(`pilot-*`)은 기본으로 폴더를 열지 않고 건너뜀(`--allow-locked-pilot` 이면 모두 잠긴 것만). 읽기만, 출력은 JSON 한 줄(숫자·ID·코드만). 종료 0 계산함 · 1 입력 오류. |
| `viewer_score_calib.py` | 시청자 훈련 채점 보정 자료: 반응받은 채팅과 같은 구간 다른 채팅의 세 축 분포·lift·정밀도·문턱 후보(기본값은 바꾸지 않음). 손라벨 길과 시범 최종 세트 길(`--final-set`, D-22). 출력은 숫자·코드만. |
| `rehearse_new_broadcast.py` | **'파일 → 새 방송…' 한 바퀴 리허설**(08 D-31 4): 실제 MainWindow 를 임시 데이터 폴더(`%LOCALAPPDATA%\ChatCoachSmoke\rehearsal-<시각>`)로 띄워 메뉴 → 대화상자(번호 글자 입력·[고르기…]) → [시작] → 분석 화면까지 몰고 클릭 수·칸 수·단계별 초·막힌 곳을 JSON 한 줄로. 실제 채팅 받기·엔진 venv 를 쓴다. 끝에 데이터 폴더를 `shutil.rmtree`(휴지통 금지). `--limit-min N` = 처음 N분만 전사. `--range '시작-끝'` = 대화상자 '전사 범위' 칸에 글자로(08 D-33, 입력 칸 하나로 셈) — 출력 `asr_span` 에 asr 가 기억한 시작·음성 길이·도장의 시작·판정 발화의 영상 시각 범위·후보가 전사 구간 안인지. |

- **앱에서는 `파일 → 새 방송…` 으로 같은 일을 한다**(08 D-31 2·3, `app/engine_run.py`): 위 `new_project`·`fetch_chat`·영상 연결·`find_anchors`(후보 수만) 다음에 엔진(엔진 venv 따로 프로세스: 음성 뽑기(대화상자 '전사 범위' 칸 — 영상 파일 시각 `시작-끝`, 비우면 처음부터, 08 D-33) → `engine asr --asr-start-ms <시작>`(asr.json 이 시작을 기억) → (영상이 VOD 의 어디인지 모르면 `find_anchors.py --locate` 로 자동 위치 추정, 08 D-32) → `engine_to_ai.py --engine v0.3 --topics --words --vectors`)까지. 판정 도장 `derived\engine-stamp.json` 이 지금 보정과 다르면 판정만 다시 한다. 시범이 아니면 봉인 없이 그 프로젝트 `derived\`(asr.json · link-v0.3.json · asr-topic-flow.json · asr-words.json · asr-topic-vectors.json), 시범이면 `pilot_engine_run.py` 봉인 길. 명령줄은 개발·시험용이다.

### 가상 채팅 생성 (H-3) — `python -m engine generate` 와 LM Studio

`tools/` 밖의 엔진 명령이지만 `make_scenario.py --generated` 와 짝이라 여기 적는다. 자세한 규칙은 `engine/generate.py` 머리말과 [20](../docs/design/20-plan-virtual-chat.md) 4-3.

- **실제 길**: `.venv-engine\Scripts\python -m engine generate --project <P> --from 20:00 --to 40:00 --model <모델 id> --out <P>\scenarios\<이름>.jsonl` → `.venv\Scripts\python tools\make_scenario.py --project <P> … --generated <그 파일>`. `engine generate` 의 기본(시나리오를 바로 씀)은 시험·개발용이다. 앱 메뉴 '훈련 → 가상 채팅 만들기' 가 이 둘을 한 번에 부르게 한다(08 D-24 3).
- **LM Studio 켜는 법(사용자, 클릭 2)**: LM Studio 를 열고 개발자 탭 → '서버 시작'. 쓸 모델이 올라가 있어야 한다(`/v1/models` 에 안 보이면 `model_missing`). 주소는 `http://127.0.0.1:1234` 만 쓴다 — `localhost`·`127.0.0.1`·`::1` 밖 주소는 `not_local` 로 거부하고, 프록시 환경 변수를 무시하고, 넘겨주기(3xx)를 따르지 않는다. Claude 는 `lms server start` 를 **실행하지 않는다**(시범 뒤 사용자 확인을 받고 시험, D-24 6).
- **서버가 꺼져 있으면** 생성 0건으로 멈추고 `lmstudio_off`(종료 3), 아무것도 쓰지 않는다. 밖으로 넘어가지 않는다.
- **보낸 기록**: 실제 서버를 부를 때마다(거부·실패 포함) `<P>\scenarios\llm-send.jsonl` 에 한 줄 — 언제·몇 글자·모델·지시문 판·요청/응답 해시·코드만, 본문·닉네임 없음(D-06 5, D-24 2). `--send-log`·`--out` 은 `<P>\scenarios\` 아래 `.jsonl` 만 받는다.
- **`--dry-run` 은 '파일(시나리오·생성 후보)을 쓰지 않음'** 이다. 실제 서버 길이면 dry-run 이어도 LLM 을 부르고 보낸 기록을 남긴다.
- **원 닉네임 목록이 없으면 닫는다**: `chat\protected\identities.jsonl` 이 없으면 `nicknames_missing`(종료 1). 가짜 응답(`--fake`) 길만 `--allow-no-identities` 로 연다(경고). 그래서 실제 생성은 채팅을 수집한 PC 에서만 된다.
- **시험은 실제 LM Studio 를 부르지 않는다**: `--fake <합성 응답.json>` 또는 `engine.llm_fake.FakeLLMServer`(127.0.0.1 전용 가짜 서버). 끝에서 끝까지는 `gen_e2e.py`.
- 시범 프로젝트(`pilot-*`)와 시범 폴더 안 경로는 읽기 전에 거부(`pilot_locked`, 종료 2).
- 종료 코드: 0 정상 · 1 입력 오류(`not_local`·`nicknames_missing`·`out_path_unprotected`·`send_log_unprotected` 등) · 2 시범 거부 · 3 LLM 없음(`lmstudio_off`·`model_missing`) · 4 만들 수 없음.

## AI 결과 · 봉인

| 도구 | 한 줄 설명 |
|---|---|
| `engine_to_ai.py` | 엔진 link 출력 → ai-result 1판. 시범 프로젝트는 `--seal` 로만 받는다(D-15 8). `--engine v0.3`(로컬 임베딩 의미 비교, 엔진 venv) · 짝 셋(판정 봉인의 짝으로 봉인, `--out a.json` 이면 `a.<짝>.json`): `--topics`(주제 흐름, D-17 5) · `--words`(시각별 전사 낱말, D-20 3) · `--vectors`(창별 주제 벡터, D-22). 낱말·벡터 평문 파일은 그 프로젝트 보호 구역 안일 때만 쓴다. 리허설·시범: `--from-project <P> --seal --by L01 --topics --words --vectors`. asr 를 영상 중간부터 잘라 전사했으면 `--asr-start-ms <잘라 낸 시작>`(아래 '시각 입력 두 가지'). |
| `pilot_engine_run.py` | **시범 엔진 실행 한 번에**(08 D-28 5, **엔진 venv**): 편마다(`--project` 여러 번 또는 `--all` = `projects\pilot-*`) project.json `media.current.path_abs` 영상(앱에서 아직 열지 않아 비어 있으면 `--videos`, 기본 `~\Videos\ChatCoach-pilot` 에서 VOD 번호로 `pilot_setup.py` 이름 규칙 — 이름이 VOD 0 부터인 파일만, project.json 에는 쓰지 않음)에서 VOD `--start-ms`~`--end-ms`(기본 1200000~3600000 = 20:00~60:00) 음성을 `chatcoach-data\sealed-work` 에 뽑고(`extract_audio.py` 와 같은 함수) → `python -m engine asr`(기본 large-v3-turbo cuda float16, cpu 로 물러나면 출력 `asr.fallback` 1) → `engine_to_ai.main` 을 같은 프로세스에서 v0.2·v0.3 차례로 `--seal --by L01 --topics --words --vectors --asr-start-ms <start>` 로 → 성공·실패 모두 finally 에서 sealed-work 의 wav·asr·log 를 `os.remove`. 시각 입력은 asr_start_ms 하나(`--file-start-ms` 는 받지 않음). 시범 폴더에는 쓰지 않는다(열기 감시: 그 안 쓰기 금지, 읽기는 project.json·chat·locks 만). labels 에 파일이 있는 편(라벨링 시작)은 `--allow-labeled` 없이 거부. `--dry-run` = 영상·project.json·엔진 venv·모델·ai_seal status 만 보고 아무것도 쓰지 않음. 출력은 편마다 JSON 한 줄(해시 앞 12자·크기·check·presealed·걸린 시간). 종료 0 모두 됨 · 1 실패·거부(dry-run 은 준비 안 됨) · 2 입력 오류·환경 없음(아무것도 안 함). 사용: `.venv-engine\Scripts\python tools\pilot_engine_run.py --project <…\pilot-…> --dry-run` → 같은 줄에서 `--dry-run` 빼고. |
| `ai_seal.py` | AI 결과 봉인 받기·상태·해제 (`app.seal`). `seal`·`status`('봉인됨 n건', 짝 유무, 나중에 붙인 짝 `pairs_attached`, 해제 뒤 엔진 판)·`check`(해제 조건만)·`unseal --all`(프로젝트 하나를 손으로 풀 때 — 해제 당일 입구는 `pilot_unseal_day.py` 하나)·`verify`(해제된 결과·짝 파일을 해제 기록 해시와 대조)·**`attach-pairs`**(짝 보충, D-23 3: 이미 있는 판정 봉인에 주제 흐름·전사 낱말·벡터 짝을 붙인다. 판정은 다시 봉인하지 않아 사전 봉인 여부 그대로. `--all` 또는 `--sha256 <앞 12자 이상>`, 입력은 `--from-engine`(엔진을 메모리로, 엔진 venv, 판정과 같은 `--asr`·`--asr-start-ms`·범위) 또는 `--topics/--words/--vectors <파일> [--remove-source]` — 시범은 낱말·벡터 파일이 `chatcoach-data\sealed-work` 아래일 때만. 이미 있는 짝은 'present' 로 건너뛰고, 하나라도 거부되면 아무것도 붙이지 않는다). 오류 줄에 원인 코드 `code`(`app.seal.SEAL_ERROR_CODES` — `dup_run_id`·`sealed_mismatch`·`messages_missing` 등). 출력은 JSON 한 줄(해시 앞 12자·코드·건수). |
| `ai_compare.py` | 해제한 run 과 최종 세트로 AI 비교 보고서를 만든다 (`app.ai_compare`). |
| `pilot_unseal_day.py` | **시범 해제 당일 한 번에**(D-22 2): 조건 확인 → 3편 해제(짝 포함) → 해제 파일 대조(`ai_seal.verify_project`) → 일치도 보고서가 없으면 만듦(요약 폴더 `agreement\`) → AI 비교 보고서 → 엔진 판끼리 견주기 → 보정 재측정 → 요약(숫자·코드·ID 만). 한 편이라도 조건이 안 되면 아무것도 풀지 않고 종료 3. **기본은 dry-run**(조건만 보고 아무것도 쓰지 않음) — 되돌릴 수 없는 해제는 `--go` 를 줄 때만. 기본 폴더는 없다: `--projects-root`·`--project` 를 주거나, 실제 데이터 폴더(`chatcoach-data`)면 `--real` 이 있어야 한다(없으면 아무것도 열기 전에 종료 2). 해제 당일 차례: 세션이 모두 잠긴 뒤 `--real`(dry-run) → 사용자 세션의 '시작' 확인 → `--real --go`. 유효한 봉인이 풀리지 않거나 seal.jsonl 이 깨졌으면 오류 코드(`unseal:<원인 코드>` — `app.seal.SEAL_ERROR_CODES`, 예 `unseal:dup_run_id`·`unseal:seal_jsonl_corrupt` — 와 `seal_jsonl:problems`)와 종료 1. 시범 폴더 안에는 쓰지 않고(열기 감시), 사람 클릭 0 — Claude 가 돌린다. 리허설은 합성 시범 프로젝트로만(사용자가 라벨링 중인 실제 시범 폴더로는 dry-run 도 돌리지 않는다). **`--allow-no-relabel`**(08 D-26, dry-run·`--go` 모두): 재라벨 계획 파일이 하나도 없는 편은 사유 `no_relabel_plan` 하나만 막지 않는다(1차 없음·잠기지 않음·깨진 계획·남은 재라벨·표본 검증 실패는 그대로 막음, 플래그 없으면 전처럼 종료 3). 해제 기록에 `relabel: none`·`allow_no_relabel: true`, 요약 머리·표준 출력 첫 줄에 '사람 일치도 없음(1인·재라벨 없음)'. 재라벨 세션이 없는 편은 일치도 보고서를 건너뛰고(`skipped_no_relabel`, 오류 아님) AI 비교·판끼리·보정은 1차 세션 하나의 최종 세트로 한다. **`--only <폴더 이름>`**(여러 번): 그 시범만 본다 — 고르지 않은 편은 조건도 보지 않고 풀지도 않으며(열기 감시가 그 폴더를 막음) 표준 출력·summary.md 에 `skipped (not selected)`. 없는 이름이면 종료 2. `--only` 를 주고 `--expect` 를 안 주면 기대 수 = 고른 수. D-26 해제 당일: `--real --allow-no-relabel --only pilot-15663452-15663452`(dry-run) → '시작' 확인 → 같은 줄 + `--go`. |
| `agreement_report.py` | 잠긴 두 세션의 일치도 보고서 (`app.agreement_report`). 하나라도 안 잠겼으면 라벨 파일을 열지 않고 종료 3. |

- **시범 엔진 작업 폴더(`chatcoach-data\sealed-work`)의 산출물(wav·asr.json 등)은 휴지통으로 보내지 않는다.** 봉인(`sealed\<sha>.bin`)이
  결과를 보존하므로, 봉인이 끝나면 **영구 삭제**(PowerShell `Remove-Item`, 탐색기 Shift+Delete)한다. 휴지통에 두면 시범 세션이 잠기기
  전에도 '복원' 한 번으로 라벨 구간 전사문을 열 수 있고 엔진으로 봉인된 판정을 다시 계산할 수 있다(D-15 8, 개인정보).
- `sealed-work` 를 지우는 모든 길(스크립트·도구·손)은 `Remove-Item`(PowerShell) 또는 `os.remove`·`shutil.rmtree`(Python)만 쓴다.
  `send2trash`·`send_to_recycle_bin`(screenshot_cleanup, 스크린샷 전용)·탐색기 Delete 는 쓰지 않는다.
- 시범 봉인 절차의 마지막 단계: ① `sealed-work` 가 비었는지 ② **휴지통에 원래 위치가 `sealed-work` 인 항목이 0개인지** 본다.
  휴지통 확인은 이름·원래 위치·지운 시각만 읽는다(내용은 열지 않는다). PowerShell 예:
  `$ns=(New-Object -ComObject Shell.Application).Namespace(10); ($ns.Items() | ? { $ns.GetDetailsOf($_,1) -like '*sealed-work*' }).Count`
  (`GetDetailsOf(항목, 1)` = 원래 위치)
  하나라도 있으면 사람이 휴지통에서 그 항목을 직접 영구 삭제한다(영구 삭제는 도구·에이전트가 하지 않는다).
- **시각 입력 두 가지**(엔진 `link`·`topics`·`transcript-words`·`topic-vectors`, `engine_to_ai.py`, `ai_seal.py attach-pairs --from-engine`
  모두 같다. 뜻이 달라 서로 대신 쓸 수 없다 — 2026-10-24 시범 봉인에서 `--file-start-ms` 로 대신 써 발화가 20분 밀렸다):
  - `--file-start-ms N` = **클립 파일이 VOD 의 어디서 시작하나**(project.json 보정과 짝). project.json 보정이 있으면 상쇄되어 asr 시각을 옮기지 않는다.
  - `--asr-start-ms N` = **asr 가 영상 파일의 어디서 잘라 낸 음성인가**(`extract_audio.py --start-ms N`). 영상 파일 시각 = asr 시각 + N, 보정 전에 더한다.
  - **asr.json 이 그 값을 기억한다**(08 D-33 '시작 옮기기'): `python -m engine asr … --asr-start-ms N` 이 asr.json `audio.asr_start_ms = N`
    을 적고, 위 읽는 명령들과 `find_anchors.py`(후보·`--locate`)는 `--asr-start-ms` 를 주지 않으면 그 값(칸이 없는 옛 파일은 0)을 쓴다.
    주었는데 기억과 다르면 `asr_start_conflict` 로 거부한다(엔진 명령·`engine_to_ai.py` 종료 2, `find_anchors.py` 종료 1 — 두 번 더하지
    않는다). 앱 '파일 → 새 방송…' 의 '전사 범위' 칸이 이 길을 쓴다 — 그래서 메인 창이 보정이 바뀌어 전사 낱말을 다시 만들 때
    (`engine transcript-words --project`, asr 시작을 명령줄로 주지 않음)도 시각이 맞다. 기억이 없는 asr(시범 `sealed-work` 처럼 따로
    뽑아 전사한 것)는 전처럼 `--asr-start-ms` 로 준다.
  - 시범처럼 VOD 전체 파일에서 20:00 부터 40분 wav 를 뽑아 전사했으면:
    `tools\engine_to_ai.py --from-project <P> --asr <sealed-work\…asr.json> --asr-start-ms 1200000 --start-ms 1200000 --end-ms 3600000 …`
  - 시범 봉인은 손으로 치지 않고 `pilot_engine_run.py` 한 줄로 한다(뽑기 시작 = `--asr-start-ms` = `--start-ms` 를 도구가 맞춘다, D-28 5).
  - `--file-start-ms` 를 주고 `--asr-start-ms` 가 0 인데 asr 음성이 영상 길이(project.json `media.current.probe.duration_ms`)의 60% 보다
    짧으면 표준 오류에 `asr_shorter_than_media` 경고 한 줄(초)을 찍는다. 멈추지는 않는다.
- `engine_to_ai.py --seal` 의 출력에는 해시·크기·검사와 함께 `engine_version`·`semantic`(active·status·fallback)이 찍힌다(내용 아님).
  `--engine v0.3` 인데 v0.2 로 물러나면 봉인하지 않고 종료 1(`engine_fallback`) — 일부러 v0.2 로 봉인하려면 `--allow-fallback`.
- `python -m engine link`·`python -m engine topics` 는 시범 프로젝트(`pilot-*`)를 거부한다(`pilot_needs_seal`, 평문 파일을 쓰지 않음).
- **실제 채팅 사본도 휴지통으로 보내지 않는다.** gui_smoke 등이 개발용 실제 프로젝트를 복사한 임시 폴더(`chatcoach_smoke_*`,
  `chat\messages.jsonl` 포함)는 `shutil.rmtree`(remove_tree_checked)로만 지운다. 휴지통에 그런 폴더가 보이면 사람이 영구 삭제한다.

## 테스트 · 점검

| 도구 | 한 줄 설명 |
|---|---|
| `make_fixture.py` | 합성 픽스처 생성기 (sha1('fixture-N')·'시청자N'·video_no 0). `--check` 로 `tests/fixtures/labels_*.jsonl` 골든이 그대로 다시 만들어지는지 본다. |
| `gui_smoke.py` | 실제 창을 띄워 작업대 단계(프로젝트 열기 → 영상 → 탐색 → 채팅 → 타임라인)를 PASS/FAIL 로 점검한다 (릴리스 전 수동). `--labeling-flow` 는 사본(project.json·guide·messages.jsonl)에서 세션 → 구간 → 반응(재확인) → 표본 → 내보내기 → 다시 열기까지 돌리고 사본을 지운다. `--discern-flow`(H-5)는 사본(+ 원 닉네임 목록·전사 낱말·주제 흐름, 끝에 rmtree)에서 '가상 채팅 만들기' 를 가짜 LM Studio(127.0.0.1 빈 포트)로 생성까지 돌리고, '가상 채팅 가려내기' 를 메뉴로 열어 합성 판정 10건 → 구간 끝 숫자 줄을 손 계산과 대조 → 기록 바이트 검사까지 본다(단계 `ds_*`). 실제 LM Studio 설정은 이 흐름 동안만 가짜 주소로 두고 끝에 지운다. |
| `smoke_watch.py` | gui_smoke 를 자식으로 띄워 **바깥에서** 감시한다(22 5-2 의 1번). 2초마다 자식 나무의 스레드 수·Suspended 수·CPU 를 적고, 출력이 `--stall` 초(기본 30) 멈추면 그 순간의 스레드 상태를 남기고 gui_smoke `--faulthandler-file` 로 모든 스레드 스택을 받는다. 끝에 요약 한 줄(종료 코드·멈춤·Suspended·덤프 수). 사용: `python tools/smoke_watch.py -- --project <P> --offscreen --analysis-flow`. 기록은 `%LOCALAPPDATA%\ChatCoachSmoke\watch\<시각>-<pid>\`(watch.log·stack.txt, 새 20개만 남김). **`--summarize [기록 폴더·stack.txt·watch.log …] [--last N]`** 은 감시하지 않고 끝난 기록을 읽어 멈춤마다 앞·뒤 단계·CPU 초/초·Suspended·판정 한 마디와, 덤프마다 주 스레드의 gui_smoke 단계 함수(`step_*`)·줄·앱 코드 틀을 요약한다(23 5-2 의 23. 경로가 없으면 가장 새 기록). |
| `verify_chatcoach.py` | **verify-chatcoach 스킬(`.claude/skills/verify-chatcoach/`)의 껍데기**: `--doctor`(읽기만: venv·bin·`<P>` 입력·영상·남은 사본·돌고 있는 gui_smoke 와 Suspended 수) · `--feature <base·labeling·agreement·adjudication·analysis·training·viewer·discern·all>`(gui_smoke 를 smoke_watch 로, 실행마다 빈 `--data-dir`, 증거는 `chatcoach-data\screens\verify-<기능>-<시각>\` 에 스크린샷·run.log·watch\·summary.json, `<P>` 파일 전후 대조·남은 사본 수) · `--cleanup`(우리 실행이 남긴 자식·사본만, 증거는 남김). 시범 경로는 글자만 보고 종료 2. 영상은 `--video` → `CHATCOACH_VERIFY_VIDEO` → `chatcoach-data\config\verify-video.txt`. 종료 0 통과 · 1 FAIL/부수 효과 · 2 거부 · 124 시간 초과 · 125 끝 요약 줄 없음. |
| `ci_selftest.py` | 빌드된 exe 를 한글 폴더 사본에서 `--selftest` 로 점검한다 (release.yml). |
| `bench_c6.py` | 합성 20만 건으로 채팅 색인·재현 검증 워커·타임라인 갱신·확대 성능을 잰다 (14 6-1). 숫자만. |
| `update_roundtrip.py` | 앱 안 업데이트 왕복 시험(04 12-5)을 자동으로 돌린다. `--rollback` 이면 업데이트 직후 실행에서 [도움말 → 이전 버전으로 되돌리기] 메뉴 함수로 되돌리고 확인한다. |
| `pilot_setup.py` | 시범 영상 폴더 → 프로젝트·채팅·앵커 후보 (보정은 제안만). `--dry-run` 은 아무것도 쓰지 않는다. |
| `pilot_freeze.py` | 시범 시작 때 라벨 형식·기준서 v0.1 고정 기록(freeze.json)을 한 번 만들고, 시범 중에는 `--check` 로 확인. |
| `pilot_rehearsal.py` | **해제 당일 리허설**(합성 시범 3편, 사람 클릭 0): 판정만 사전 봉인 → 1차 잠금 → 해제 당일 dry-run 이 종료 3 → 재라벨 잠금 → `ai_seal attach-pairs`(sealed-work 의 합성 짝 파일, `--remove-source`) → dry-run 종료 0 → `pilot_unseal_day --go` 종료 0(편마다 해제 2·짝 셋 각 2·대조 ok·사전 봉인 2) → 편마다 `ai_seal verify` → 출력에 합성 표지 없음. 자리는 `%LOCALAPPDATA%\ChatCoachSmoke\rehearsal-*` 만(밖이거나 실제 데이터 폴더면 종료 2), 끝나면 `shutil.rmtree`. 도구는 모두 같은 프로세스에서 `main(argv)` 로 부른다(앱 데이터 폴더 지정이 저장소 `data\` 로 새지 않게). `--gui`(화면 확인, 합성 영상은 로컬 ffmpeg) · `--seal-mode together`(지난 묶음 방식) · `--keep` · `--json`. 합성 자료는 `tests/test_pilot_unseal_day` 의 도우미를 쓴다. 종료 0 = 검사 모두 통과. |
| `fake_release_server.py` | 127.0.0.1 전용 가짜 릴리스 서버와 시험 릴리스 준비. |

- **스모크가 멈췄을 때 읽는 법** (`smoke_watch.py` 요약 줄·watch.log — 먼저 `python tools/smoke_watch.py --summarize` 한 번):
  ① 멈춤 기록에 `ALL-SUSPENDED`(스레드가 모두 Suspended) 또는 `MOSTLY-SUSPENDED`(80% 이상 — 실제 외부 정지는 런처 스레드와 멈춘 동안 새로 생긴
  스레드가 멈추지 않아 17/19 였다, 23 3-4)이고 요청한 덤프를 '받음 0' → 프로세스가 **밖에서 통째로 정지**됐다
  (보안 프로그램 등, 19 3-2). 코드 문제가 아니다 — Acronis 신뢰 목록에 python.exe 를 넣는다(사용자 몫).
  ② Suspended 가 0 인데 출력이 멈추고 덤프를 받았다 → 요약의 '주 스레드: 단계 step_… (줄)' 과 '가장 안쪽' 틀을 본다.
  요약 판정이 'CPU 를 쓰며 돎' 이면 출력 없이 긴 단계(예: 23 5-2 의 23 — `flow_reaction` 의 두 번 듣기 2배속 재생 42초, `step_labeling_flow`
  가 재생 범위 도달을 기다림)이고 교착이 아니다. 'CPU 거의 0' 이면 무언가를 기다리는 것이라 그 틀이 원인 후보다.
  두 번 듣기는 이제 진행 줄(`  .. flow_reaction 다시 듣기 n/2 시작 · coverage=… 10초마다 · 끝`, 24 5-2 의 13)을 찍으므로 전체 흐름도
  기본 `--stall 30` 으로 가짜 멈춤이 잡히지 않는다. 진행 줄은 PASS/FAIL 줄과 모양이 달라 요약의 단계 이름에는 들어가지 않는다.
  ③ `Timeout (` 덤프(자동) → 어떤 스레드가 GIL 을 60초 넘게 쥐었다(같은 실행에 Suspended 표본이 있으면 외부 정지가 풀릴 때 생긴 가짜일 수 있음,
  23 5-2 의 24). ④ `Windows fatal exception` → 치명 오류(접근 위반 등) 순간의 스택('오류 난 스레드').
  스택 파일에는 파일·함수 이름과 줄 번호만 있다(채팅 본문·닉네임 없음). 요약은 경로를 저장소 상대·`site-packages/…`·파일 이름으로 줄이고,
  자식 출력에서는 PASS/FAIL 단계 이름과 ms 만 옮긴다. gui_smoke 를 혼자 돌릴 때도 `--faulthandler-file <경로>` 를 쓸 수 있다.

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
