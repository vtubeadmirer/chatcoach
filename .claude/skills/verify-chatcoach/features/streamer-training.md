# 스트리머 훈련 (가상 채팅 섞기 포함)

스트리머가 메뉴 한 번으로 소리 끈 다시보기 위에 채팅이 흐르는 훈련 화면을 열고, '읽겠다' 싶은 채팅을 눌러 고르고(다시 누르면 취소), [견주기] 로 원래 방송에서 실제로 대답한 채팅과 견주고(같음 · 나만 · 원래만), 행을 눌러 원래 대답을 소리 켜고 들은 뒤 [닫기] 로 작업대로 돌아간다. '가상 채팅 만들기' 로 다른 방송 채팅을 가져와 섞으면 가상 채팅은 '가져옴' 표시로 흐르고 견주기에서 따로 센다. 기록에는 본문·닉네임이 남지 않는다.

## Sub-features

- `tr_copy` 사본 + 손라벨 기준 `labels\manual-*.csv`(읽기만), 시범 프로젝트 아님.
- `tr_open` 보기 전용 + 사본에 영상 연결.
- `tr_menu` '훈련 → 스트리머 훈련' 한 번: 고르는 창 없음, 소리 끈 자동 재생, start 기록 줄 1개.
- `tr_space` Space = 멈춤(작업대 재생은 그대로).
- `tr_pick` 채팅 5개를 진짜 마우스로 고르기 + 1개 다시 눌러 취소, 아직 안 온 채팅은 표에 없음.
- `tr_compare` [견주기]: 같음 + 나만 = 4, 원래 대답 하나를 골라 같음 ≥ 1.
- `tr_listen` '원래만' 행 하나 누름 → 소리 켜고 재생 → 범위 끝에서 멈춤.
- `tr_close` [닫기] → 작업대.
- `tr_log` 기록 줄 start · pick×5 · unpick · compare · end, 본문·닉네임 0.
- `tr_exposure` 훈련 뒤 만든 라벨링 세션은 노출(대표값에서 빠짐)로 계산.
- `tr_v_make` '훈련 → 가상 채팅 만들기' 한 번(엔진 도구를 워커로, 사본 `scenarios\` 에만 씀, LM Studio 설정 없음).
- `tr_v_menu` 메뉴에 '가상 채팅 섞기' 가 기본으로 켜짐 → '스트리머 훈련' 한 번(start 줄에 scenario_id·sha256).
- `tr_v_pick` 가상 채팅 1개('가져옴' 표시) + 실제 채팅 1개 고르기.
- `tr_v_compare` 같음 + 나만 = 실제 1개만, 가상은 '가상 고름' 묶음에 1개.
- `tr_v_log` 기록 줄: 가상 pick 은 line_id·source 만, chat_id·본문·이름 없음.
- `tr_v_privacy` 시나리오 파일 바이트 검사: 가림 닉네임·viewer_id·원 닉네임·user_id_hash·원 본문·금지 키 0.
- `tr_cleanup` 사본 지움.

## How to get to it (user POV)

- 프로젝트를 보기 전용으로 열고 영상 연결 → **훈련(&T) → 스트리머 훈련(&S)** (라벨링 세션이면 같은 메뉴가 '보기 전용으로 다시 열고 훈련').
- 훈련 화면: Space 멈춤·재생, 채팅 행 한 번 누름 = 고르기/취소, **[견주기]**, 견주기 표 행 누름 = 듣기, **[닫기]**.
- **훈련 → 가상 채팅 만들기(&M)** → 다음부터 **훈련 → 가상 채팅 섞기(&V)** (기본 켬).
- 시범(잠기기 전)·기준 없음·영상 없음이면 메뉴 안에 까닭 한 줄이 보이고 꺼진다.

## Driving it with gui_smoke

Preconditions:

- `--doctor --feature training` 이 `fail 0` — `<P>` 에 `labels\manual-*.csv` 1개 이상, `chat\messages.jsonl`, 영상 `<V1>`.
- 실행 데이터 폴더에 `lmstudio/model` 이 없어야 한다(껍데기는 매번 빈 데이터 폴더라 늘 없음).

- **훈련 흐름.** Run `.venv\Scripts\python tools\verify_chatcoach.py --feature training`. 단계 `tr_copy → tr_open → tr_menu → tr_space → tr_pick → tr_compare → tr_listen → tr_close → tr_log → tr_exposure → tr_v_make → tr_v_menu → tr_v_pick → tr_v_compare → tr_v_log → tr_v_privacy → tr_cleanup` 모두 PASS.
- **메뉴 한 번.** `tr_menu enabled=True autoplay=True modal_dialog=False muted=True start_lines=1 clicks=2`.
- **멈춤.** `tr_space paused=True via=key workbench_player_untouched=True` (offscreen 은 `via=fallback` 일 수 있다).
- **고르기·취소.** `tr_pick picked=5 after_cancel=4 marks=4 future_hidden=True clicks=6`.
- **견주기·듣기.** `tr_compare same=<≥1> rows=<>0>`, `tr_listen unmuted=True played=True stopped_at_end=True`.
- **기록·노출.** `tr_log kinds_ok=True invalid=0 text_or_nick_in_file=0 end_reason=compare`, `tr_exposure later_session_exposed=True`.
- **가상 채팅.** `tr_v_make lmstudio_model_set=False scenarios=1 retrieved_lines=<>0> only_scenarios_dir=True`, `tr_v_menu mix_default_on=True make_hidden=True start_scenario_ok=True`, `tr_v_pick badge_shown=True virtual_picked=1 real_picked=1`, `tr_v_compare virtual_picked=1 virtual_rows=1`, `tr_v_log text_or_nick_in_file=0`, `tr_v_privacy … total=0`.
- **사람 클릭 수.** 끝에 `    사람 클릭 합계: F 흐름 <n> + 가상 채팅 흐름 <m> = <n+m> (키 1)`.
- **Proof.** `== verify: features=training rc=0 … fail=0`, 스크린샷 `_tr_train` · `_tr_compare` · `_tr_virtual`, `== 부수 효과: <P> 파일 바뀜 0 · 남은 사본 0`.

## Gotchas

- 영상이 없으면 `training_flow` 가 바로 FAIL(소리 끈 다시보기가 바탕).
- `tr_v_make` 는 `SMOKE_SCENARIO_ARGS`(채울 문턱을 낮춤)로 돈다 — 기본 문턱이면 채팅 많은 `<P>` 는 채울 곳이 0 이다. 기본 문턱 동작은 이 흐름으로 검증되지 않는다.
- 공용 스모크 데이터 폴더 `settings.ini` 에 `lmstudio/model` 이 남아 있으면 실제 127.0.0.1:1234 에 붙을 수 있다 — gui_smoke 를 직접 돌릴 때는 `--cleanup` 먼저. 껍데기는 실행마다 빈 데이터 폴더라 해당 없음.
- `tr_v_privacy` 는 원본 `<P>\chat\protected\identities.jsonl` 을 읽기만 한다(사본에 옮기지 않음, 값은 찍지 않음).
- 훈련 화면 스크린샷에는 실제 채팅이 흐른다 — 증거 폴더 밖으로 내지 않는다.
