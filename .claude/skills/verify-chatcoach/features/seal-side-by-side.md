# 분석 (봉인 · 나란히 보기 · 방송 요약)

엔진(AI) 판정 결과는 사람 라벨이 다 잠기기 전에는 봉인된 채로만 들어온다. 사용자는 보기 전용에서 결과를 봉인해 가져오고, 조건(두 세션 잠김)이 차면 봉인을 해제하고, 분석 세션에서 엔진 판정과 사람 최종 세트를 나란히 보고, 작업대 위 주제 트랙과 방송 요약으로 주제 덩어리별 채팅·반응을 보고, AI 비교 보고서를 만든다. 라벨링 세션에서는 이 모든 것이 꺼져 있어야 한다(블라인드).

## Sub-features

- `an_copy` 사본 + ai 폴더가 프로젝트 밖(사본 옆 `…\ai`).
- `an_seal` 보기 전용 '분석 → AI 결과 가져오기(봉인)' (합성 엔진 결과 + 짝 주제 흐름이 같이 봉인), '봉인 해제' 는 조건 미충족으로 거부.
- `an_open_a` · `an_a_label` · `an_a_lock_plan` · `an_b_label` · `an_b_lock_blind` L01 1차 · L02 재라벨을 잠금(일치도 흐름과 같은 도우미).
- `an_labeling_blind` 그동안 ai 폴더를 한 번도 열지 않음, 잠긴 두 라벨링 세션을 다시 열어도 분석 메뉴·주제 트랙·요약·가져오기가 꺼짐.
- `an_view_sealed` 해제 전 분석 세션: '엔진과 나란히 보기' 가 '봉인됨 1건' 으로 막힘, 주제 트랙은 '봉인됨', 요약 꺼짐, 봉인 파일·runs·topics 를 열지 않음.
- `an_unseal` 보기 전용에서 해제(조건 됨, 사전 봉인), 주제 흐름 짝도 같이 풀림.
- `an_side_by_side` 분석 세션 '엔진과 나란히 보기': 목록 · 점수 표 · '다음 다른 곳'.
- `an_topic_track` 같은 분석 세션 작업대의 주제 트랙 띠 3개, 띠를 누르면 그 주제 시작으로.
- `an_summary` '분석 → 방송 요약': 덩어리 3줄, 행 한 번 누름 = 그 주제 시작, 입력 칸 없음.
- `an_report` 보기 전용 '보고 → AI 비교 보고서' (프로젝트 밖 폴더: manifest.json · report.json · report.md · tables\).
- `an_cleanup` 사본·보고서 지움.

## How to get to it (user POV)

- 프로젝트 열기 → 세션 고르기 창에서 보기 전용 → **분석 → AI 결과 가져오기(봉인)(&I)…** → 결과 파일 고르기.
- **분석 → 봉인 해제(&U)…** (보기 전용, 모든 사람 세션이 잠긴 뒤) → 확인.
- 세션 고르기 창 **분석 보기** → **분석 → 엔진과 나란히 보기(&S)** · **분석 → 방송 요약(&B)** · 타임라인 위 주제 트랙 띠 누르기.
- **보고 → AI 비교 보고서(&I)…** (세션 밖·조정 세션, 해제된 run 만).

## Driving it with gui_smoke

Preconditions:

- `--doctor` 가 `fail 0`, `<P>` 에 `chat\messages.jsonl`, 영상 `<V1>` 있음.
- 엔진 결과·주제 흐름은 gui_smoke 가 사본의 chat_id·시각만으로 합성한다(본문을 읽지 않음, 등급은 chat_id 해시). 실제 엔진은 돌지 않는다.

- **분석 흐름.** Run `.venv\Scripts\python tools\verify_chatcoach.py --feature analysis`. 단계 `an_copy → an_seal → an_open_a → an_a_label → an_a_lock_plan → an_b_label → an_b_lock_blind → an_labeling_blind → an_view_sealed → an_unseal → an_side_by_side → an_topic_track → an_summary → an_report → an_cleanup` 모두 PASS.
- **봉인 가져오기·해제 거부.** `an_seal sealed=1 check=valid topics_check=valid paired=True unseal_refused=True unmet=<≥1>`. 거부 사유는 `  경고: …`·`  안내: …` 줄(앞 160자, 코드·건수만)로 찍힌다.
- **라벨링 블라인드.** `an_labeling_blind menu_off=2/2 ai_opens=0`.
- **해제 전 분석 세션.** `an_view_sealed side_by_side_off=True sealed_count_shown=True topic_track_sealed=True summary_off=True bin_run_topics_opens=0`.
- **해제.** `an_unseal conditions_met=True presealed=True topics_unsealed=True`.
- **나란히 보기.** `an_side_by_side items=<>0> score_rows=<>0> jump=True`. 스크린샷 `_an_side`.
- **주제 트랙.** `an_topic_track bands=3 sealed=False jump_to_band_start=True` (QTest 로 가운데 띠를 누름).
- **방송 요약.** `an_summary rows=3 chats=<>0> reaction_source=final_set|engine row_click_seek=True inputs=0`. 스크린샷 `_an_summary`.
- **AI 비교 보고서.** `an_report files=4 tables=<>0> pre_sealed=True conditions_met=True`.
- **Proof.** `== verify: features=analysis rc=0 … fail=0`, `== 부수 효과: <P> 파일 바뀜 0 · 남은 사본 0`. 실제 `chatcoach-data\ai\` 에는 아무것도 생기지 않는다(ai 루트는 사본 옆 임시 폴더).

## Gotchas

- 봉인·해제는 되돌릴 수 없는 동작이다 — 사본에서만 한다. 실제 시범 해제는 `tools/pilot_unseal_day.py`(사용자 확인 뒤)이고 이 스킬의 일이 아니다.
- 경고·안내 상자는 모달이라 화면 없이 멈추므로 흐름 동안 줄 출력으로 바꿔 끼운다. 상자 모양은 검증되지 않는다.
- 숫자(items·score_rows·chats)는 합성 엔진 결과의 값이라 엔진 품질과 상관없다.
- 앞 1차·재라벨 단계는 일치도 흐름과 같은 도우미라 `agree_report` 와 같은 오락가락 실패가 `an_b_lock_blind` 쪽에 보일 수 있다 — 한 번 다시 돌리고 그래도면 보고.
- `--window-size` 를 주면 나란히 보기 대화상자를 그 크기에 맞춰 줄이고 `fit=` 을 찍는다.
