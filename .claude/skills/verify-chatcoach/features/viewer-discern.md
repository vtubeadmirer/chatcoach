# 시청자 훈련 · 가상 채팅 가려내기

시청자 훈련: 사용자가 메뉴 한 번으로 소리 켠 다시보기를 열고 쓰기 칸에 채팅을 써서 Enter 하면 세 축(주제 적합성 · 대화 확장성 · 반복 여부)으로 바로 채점되고, 구간 끝에 요약('다른 시청자라면' 예시 포함)이 저절로 뜬다. 가려내기: '가상 채팅 만들기' 가 LM Studio 로 생성한 줄을 섞은 다시보기에서 표시를 숨긴 채 줄마다 진짜·가짜·모르겠음을 누르고, 구간 끝에 정답 · 혼동표 · κ · 탐지율 · 오인율이 저절로 나온다. 두 화면 모두 기록에 본문·닉네임을 남기지 않고, 기록은 보호 구역이라 백업·내보내기에서 빠진다.

## Sub-features

- `vw_copy` 사본 + `scenarios\*.jsonl` + `derived\asr.json`(+ 있으면 주제 벡터·주제 흐름), 시범 아님.
- `vw_open` 보기 전용 + 영상 연결.
- `vw_menu` '훈련 → 시청자 훈련' 한 번: 전사 낱말을 워커로 만들고, 소리 켠 자동 재생, 입력 칸 1개, start 줄, 배치 `outside=0 hscroll=n`.
- `vw_write` 합성 글 3개를 진짜 Enter: 주제 맞는 질문 = good/good/good, 이모티콘만 = weak/weak, 앞 글 베끼기 = repeat weak(`repeat.self_same`), 쓰는 동안 재생 계속.
- `vw_summary` 구간 끝에 요약이 저절로(writes=3, 입력 꺼짐, 재생 멈춤).
- `vw_close` [닫기] → 작업대.
- `vw_log` 기록 줄 start · write×3 · end(segment_end), 채점기 판, 쓴 글은 viewer 기록에만(신호·앱 로그·다른 파일 0).
- `vw_privacy` 보호 구역, 백업·내보내기 제외, 훈련 기록 0, 노출 0.
- `vw_cleanup` 사본 지움.
- `ds_copy` 사본 + 원 닉네임 목록·전사 낱말·주제 흐름(보호 구역), 손라벨 기준, LM Studio 설정 없음.
- `ds_open` 보기 전용 + 영상 연결.
- `ds_make` '훈련 → 가상 채팅 만들기' 한 번을 가짜 LM Studio(127.0.0.1 빈 포트)로: 확인 → 생성 → 섞기, 새 파일은 시나리오·생성 후보·보낸 기록 셋뿐, 보낸 글에 원 닉네임 0, 시나리오 머리에 응답 해시·생성 파일 해시·gscore-v1·llm-check-v1.
- `ds_menu` '훈련 → 가상 채팅 가려내기' 한 번: 소리 켬, 가상 표시 숨김, '시청자 번호' 칸 숨김, 맨 위 한 줄, start 줄.
- `ds_judge` Space → 합성 판정 10건(생성 4 · 가져옴 2 · 실제 4)을 '종류' 칸 단추에 진짜 마우스로, 한 번은 바꾸기.
- `ds_result` 구간 끝에 정답: 혼동표·κ·p_o·p_e·탐지율·오인율·모르겠음이 손 계산과 같고, 숫자 줄 글자도 같고, 가상 줄 표시가 다시 붙고, 줄마다 ✓·✗·?·· 표시.
- `ds_log` 기록 줄 start · judge×11 · end, 끝 숫자 = 손 계산, judge 줄 키는 허용 목록만, 본문·가림 이름·viewer_id·출처 칸 0.
- `ds_privacy` 보호 구역·백업 제외·노출 0·보낸 기록에 생성 본문·닉네임 0·`messages.jsonl` 그대로·LM Studio 설정 지움.
- `ds_cleanup` 사본 지움.

## How to get to it (user POV)

- 프로젝트를 보기 전용으로 열고 영상 연결 → **훈련(&T) → 시청자 훈련(&W)** → 쓰기 칸에 채팅 → Enter → 구간 끝 요약 → **[닫기]**.
- LM Studio 서버를 켠 뒤(사용자, 개발자 탭 → '서버 시작') **훈련 → 가상 채팅 만들기(&M)** → **훈련 → 가상 채팅 가려내기(&D)** → Space 멈춤 → 줄마다 '종류' 칸의 진짜·가짜·모르겠음 → 구간 끝 정답 → **[닫기]**.
- 시범(잠기기 전)·영상 없음·가상 채팅 없음이면 메뉴 안에 까닭 한 줄.

## Driving it with gui_smoke

Preconditions:

- `--doctor --feature viewer` / `--feature discern` 이 `fail 0` — 시청자: `<P>\derived\asr.json`; 가려내기: `<P>\chat\protected\identities.jsonl` 과 `labels\manual-*.csv`; 둘 다 `chat\messages.jsonl` 과 영상 `<V1>`.
- 실제 LM Studio 는 부르지 않는다: gui_smoke 가 `engine.llm_fake.FakeLLMServer` 를 127.0.0.1 빈 포트에 띄우고 설정 `lmstudio/*` 를 그 흐름 동안만 그 주소로 둔다.

- **시청자 흐름.** Run `.venv\Scripts\python tools\verify_chatcoach.py --feature viewer`. 단계 `vw_copy → vw_open → vw_menu → vw_write → vw_summary → vw_close → vw_log → vw_privacy → vw_cleanup` 모두 PASS.
- **메뉴 한 번.** `vw_menu enabled=True autoplay=True muted=False modal_dialog=False input_fields=1 words=<ok 코드> windows=<>0> start_lines=1 fit=…/hscroll=n/…/outside=0 clicks=2`.
- **쓰기·채점.** `vw_write levels=good/good/good;weak/weak/…;…/…/weak codes=…;…;…/…/repeat.self_same kept_playing=3/3 practice_rows=3 marks=3 keys=3(Enter)`.
- **요약·기록.** `vw_summary writes=3 … clicks=0`, `vw_log kinds_ok=True levels_match=True end_reason=segment_end text_in_signals=0 text_in_app_log=0 text_in_other_files=0 text_in_viewer_log=2/2`, `vw_privacy protected_zone=True backup_or_export_files=0 training_logs=0 later_session_exposed=0`.
- **가려내기 흐름.** Run `.venv\Scripts\python tools\verify_chatcoach.py --feature discern`. 단계 `ds_copy → ds_open → ds_make → ds_menu → ds_judge → ds_result → ds_log → ds_privacy → ds_cleanup` 모두 PASS.
- **만들기(가짜 LLM).** `ds_make … generated=<>0> new_files=3(scenario·gen·llm-send=True) nickname_in_sent=0/<n> provenance=True status_line=True`.
- **가려내기 열기·판정.** `ds_menu banner_generated=True marks_shown=0 viewer_col_hidden=True start_line=True`, `ds_judge judged=10 answers_match=True changed_once=True marks=10 clicks=11`.
- **정답 = 손 계산.** `ds_result numbers_match=True line_match=True gate=low_n virtual_rows_marked=<n>/<n> unseen_marks_ok=True`.
- **기록·개인정보.** `ds_log kinds_ok=True end_numbers_match=True judge_keys_only=True text_mask_id_in_file=0 banned_keys=0 in_signals=0 in_app_log=0`, `ds_privacy … messages_unchanged=True send_log_text_or_nick=0 lmstudio_model_after=False menu_enabled_again=True`.
- **사람 클릭 수.** `    사람 클릭 합계: 보기 전용 1 + 메뉴 2 + …` (시청자), `… 판정 10 + 바꾸기 1 + 정답 0 + 닫기 1 = <n> (키 1)` (가려내기).
- **Proof.** `== verify: features=viewer rc=0 …`(또는 discern), `== 부수 효과: <P> 파일 바뀜 0 · 남은 사본 0`. 이 두 흐름은 스크린샷을 남기지 않는다(요약·정답 화면에 실제 본문이 보인다) — 증거는 단계 줄과 summary.json.

## Gotchas

- 가려내기의 숫자는 화면 세션 하나라 관문이 늘 `low_n`(표본 적음)이다. 통과·실패 관문은 `tools/discern_report.py` 로만 나온다.
- `vw_write` 의 한글은 `insert` 로 넣고 Enter 만 진짜 키다(PySide6 의 한글 keyClicks 가 죽는다). 한국어 입력기 조합 중 동작은 검증되지 않는다.
- 구간 끝 요약·정답은 재생이 구간 끝에 닿아야 뜬다. 재생이 안 되는 환경이면 `via=fallback` 으로 화면 함수를 직접 부른다 — `via=segment_end` 여야 사용자 길이다.
- 가려내기 흐름은 보호 구역 파일(원 닉네임 목록·전사 낱말)을 사본에 복사한다. 사본은 흐름 끝에 `shutil.rmtree` — 남으면 `ds_cleanup` FAIL 과 경로가 찍히고, `--cleanup` 으로 지운다(휴지통 금지).
- 실제 LM Studio 생성 품질·서버 꺼짐(`lmstudio_off`) 길은 이 흐름으로 검증되지 않는다. Claude 는 `lms server start` 를 실행하지 않는다.
