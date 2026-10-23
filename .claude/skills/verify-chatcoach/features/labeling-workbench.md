# 라벨링 작업대

사용자가 프로젝트를 열고 영상을 연결해 채팅·타임라인을 보며 ① 구간(활동 분류) · ② 반응(스트리머가 대답한 채팅) · ③ 표본(무작위 100개 판정)을 매기고, 라벨을 zip 으로 내보내고, 창을 닫았다 다시 열어 같은 라벨로 이어 하는 기능이다. 기본 단계(보기 전용으로 열기·영상·탐색·재생·확대·필터)도 이 파일이 맡는다.

## Sub-features

- `open_project` 파일 메뉴 길로 열기, 세션 선택 화면이 한 번 뜨고 보기 전용이면 라벨 기록을 열지 않음.
- `chat_loaded` · `timeline_density` 채팅 표와 타임라인 밀도가 같은 건수.
- `video_load` · `waveform` 영상 연결(저장 안 함)과 파형.
- `seek` · `chat_activate` · `play` 정확 탐색, 채팅 두 번 누름 = 3초 앞으로 가서 하이라이트, 재생 1.5초.
- `timeline_zoom` · `chat_filter` 타임라인 확대·전체, 검색칸 `#1` = 시청자 번호 1 만.
- `store_copy` 사본에 라벨 저장·되돌리기(Ctrl+Z).
- `flow_open` 새 1차 세션(세션 화면 → 잠금 → 기록 파일).
- `flow_media` 사본에 영상 연결, 영상이 덮는 VOD 범위 1개, 상태 칩이 메인 창 상태 줄 하나로.
- `flow_segments` ① 경계 하나(B) → 구간 2개(저스트채팅·게임) 저장 → 구간 완료 → ② 문이 열림.
- `flow_reaction` ② 채팅 하나에 M → 강한 증거(닉네임 호명)·직접 반응, 7-2 범위 2배속 두 번 듣기 → 저장 → 반응 완료.
- `flow_sample_draw` ③ 표본 뽑기(sha256-tau-v2, 모든 표본의 확인 범위가 영상 안) + 재현 검증.
- `flow_sample_judge` 표본 1건 '반응 미확인'.
- `flow_sample_stage2` ③ 표본에 ② 라벨이 있으면 판정함으로 세고, 빠른 저장 단추는 막히고, M 은 그 ② 라벨을 고친다(같은 대상).
- `flow_layout` 큐 화면 배치 숫자.
- `flow_export` 파일 → 라벨 내보내기 zip, 행 = 현재 유효 라벨.
- `flow_reopen` · `flow_close` 이어 하기 세션으로 다시 열어 같은 라벨, 닫을 때 `session.close` 줄.
- `close` · `cache_drop` 잠금 풀림·`<P>\project.json` 그대로, 색인 캐시 지움.

## How to get to it (user POV)

- 앱을 켜면 프로젝트 화면 → **최근 프로젝트** 두 번 누르기, 또는 **파일 → 프로젝트 열기(Ctrl+O)** → 폴더 선택.
- **세션 고르기** 창: **새 세션 → 1차 라벨링 → [열기]** · **이어 하기 → [열기]** · 보기 전용.
- 영상 아래 **[영상 파일 연결…]**.
- 위쪽 **[① 구간]** → 타임라인 누르기 → **[지금 위치에서 쪼개기 (B)]** → 분류 **[저스트채팅]/[게임]** → 확신도 → '근거 한 줄' → **[저장 Ctrl+Enter]** → **[구간 완료 표시]**.
- **[② 반응]** → 채팅 두 번 누르기 또는 **M** → 단추 6개 중 하나(Enter = '채팅 읽고 대답'), 또는 **[자세히 입력 ▸]** → **[7-2 범위 다시 듣기 (R)]** → **[저장 (Ctrl+Enter)]** → **[반응 완료 표시]**.
- **[③ 표본]** → **[표본 뽑기]** → **[재현 검증]** → **[반응 미확인 저장 Ctrl+5]** · **M** · **[판단 불가로 저장]** → **[다음 ▶ PgDown]**.
- **파일 → 라벨 내보내기…** → [저장]. 잘못 저장하면 **Ctrl+Z**.
- 재생 Space, 타임라인 확대 `Ctrl+=` / `Ctrl+-` / `Ctrl+0`, 채팅 검색칸.

## Driving it with gui_smoke

Preconditions:

- `--doctor` 가 `fail 0`, `<P>` 에 `project.json` · `chat\messages.jsonl`, 영상 `<V1>` 있음.
- 사본에는 `project.json` · `guide\` · `chat\messages.jsonl` 만 간다(보호 구역·기존 라벨·영상은 안 감).

- **기본 단계만.** 보기 전용으로 열고 둘러보기. Run `.venv\Scripts\python tools\verify_chatcoach.py --feature base`. `PASS open_project … menu=Ctrl+O read_only=False session_asked=True view_only=True`, `chat_loaded chats=<n> dict_rows_kept=0`, `timeline_density` 의 buckets 합 = chats, `seek file_err_ms` ±100 안, `chat_activate highlight_ok=True timeline_ok=True`, `play moved_ms` 800~2500, `chat_filter 0 < viewer1_rows < all_rows`.
- **전체 라벨링.** 1차 세션에서 ①②③·내보내기·다시 열기. Run `.venv\Scripts\python tools\verify_chatcoach.py --feature labeling`. 아래 단계가 차례로 PASS.
- **세션 열기.** 세션 화면에서 새 1차. `flow_open copied=3 session_kind=primary store_ro=False`.
- **영상 연결.** `flow_media coverage_vod_ms=[(시작, 끝)] one_status_bar=True` (범위 1개).
- **① 구간.** 경계 하나 → 2개 저장 → 완료. `flow_segments saved=2 timeline_segments=2 reactions_locked_before=True busy_shown=True busy_cleared=True`.
- **② 반응.** M → 편집기 → 두 번 듣기 → 저장. 진행 줄 `  .. flow_reaction 다시 듣기 1/2 …` · `2/2 … ok` 가 찍히고 `flow_reaction passes=2 coverage=0.97/0.97 complete=True marker=reaction`.
- **③ 표본.** 뽑기·재현 검증·1건 판정·② 라벨 다시 열기. `flow_sample_draw alg=sha256-tau-v2 items=100 all_inside_media=True verify=ok`, `flow_sample_judge progress=1/100`, `flow_sample_stage2 judged_by_stage2=True quick_save_blocked=True reopened_existing=True same_entity=True progress=2->2`.
- **내보내기.** `flow_export by_kind={'segment': 2, 'reaction': 3} rows_match_effective=True` (같은 채팅에 반응 라벨 둘이 생기지 않음).
- **다시 열기.** `flow_reopen same=True samples_gate=True`, `flow_close last_line=session.close`, `flow_cleanup 사본 지움`.
- **닫기.** `close lock_released=True project_json_unchanged=True`, `cache_drop left=False`.
- **노트북 화면.** Run `… --feature labeling --window --window-size 1366x768`. `flow_segments`·`flow_reaction`·`flow_layout` 의 `fit=…/hscroll=n/clipped=0/outside=0`.
- **Proof.** 껍데기 끝 `== verify: features=labeling rc=0 pass=27 fail=0 skip=6` (2026-10-23 offscreen 관찰값: 33줄, 약 53초), `== 부수 효과: <P> 파일 바뀜 0 · … 남은 사본 0`, 증거 폴더에 `verify-labeling.png` · `_flow_segments` · `_flow_edit` · `_flow` 4장.

## Gotchas

- 영상이 없으면 영상 밖 규칙 때문에 구간도 저장되지 않아 `flow_media` 에서 멈춘다 — `--video` 를 빼고 라벨링을 돌리지 않는다.
- `flow_reaction` 은 2배속 두 번 듣기로 약 42초. 진행 줄이 10초마다 나오면 멈춤이 아니다.
- offscreen 에서 구간 폼은 `hscroll=y outside=21` 이 나온다(글꼴 차이). 실제 창 값은 0 — 잘림 판단은 `--window` 로만.
- `open_project … warnings=1` 은 `<P>` 의 작업대 안내 띠 수다(실패 아님).
- 숫자(chats·items·eligible)는 `<P>` 의 값이다. `<P>` 가 바뀌면 같이 바뀐다 — 조건(`items=100`, `same=True`)을 본다.
- 기본 단계는 원본 `<P>` 를 보기 전용으로 연다 — 쓰기 시험 `.write_test_<pid>` 와 `locks\open.lock` 이 잠깐 생겼다 지워진다(폴더 수정 시각만 바뀜). `close` 가 FAIL 이거나 실행이 기본 단계 중에 죽으면 `<P>` 에 잠금이 남았을 수 있다(지우지 말고 보고).
