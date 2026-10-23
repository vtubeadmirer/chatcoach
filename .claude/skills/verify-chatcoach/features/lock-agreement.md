# 세션 잠그기 · 일치도 · 조정

1차 라벨러(L01)가 ①②③ 을 마치고 세션을 잠그면, 재라벨 계획으로 두 번째 사람(L02)이 같은 블록을 1차 라벨을 보지 못한 채 다시 매기고, 둘 다 잠근 뒤 일치도 보고서를 만든다. 조정 세션에서는 두 판정이 다른 건을 한쪽으로 고르고, 출처 판정 대상·추가 계획을 만들고, 남은 재라벨 계획을 취소하고, 출처 판정 세션에서 대상 채팅의 출처(실제·가상)를 판정한다.

## Sub-features

- `agree_copy` · `adj_copy` 한글 경로 사본(`…\한글 사본\projects\프로젝트`), 시험 라벨러 L01·L02 명부.
- `*_open_a` L01 1차로 열기 + 영상 연결 + 채팅 색인.
- `*_a_label` 구간 2개(10분 블록 경계), 구간마다 표본 뽑기 + 앞 3건 판정, ③ 완료, 1차에는 ④ 단추 없음, 타임라인 부분 갱신.
- `*_a_lock_plan` 메뉴 길 잠그기(재현 검증 결과가 잠금에 남음) → 재라벨 계획(두 번째 사람 L02, 블록 표본 τ_block).
- `*_b_label` L02 relabel_second 세션: 계획 범위, 블록 안 구간, 블록 표본 3건 판정, 잠긴 1차 라벨이 화면 어디에도 없음.
- `adj_b_missed` ④ 놓친 후보 2건 + 채팅 표 오른쪽 메뉴 '표본 밖 후보' 1건(조정 흐름만).
- `*_b_lock_blind` L02 잠그기, 그동안 L01 기록·L01 잠금·`samples\` 를 한 번도 열지 않음(열기 감시).
- `agree_report` · `adj_report` 보기 전용에서 '보고 → 일치도 보고서' (프로젝트 밖 폴더, report.json·report.md).
- `agree_analysis` L01 분석 세션에서 잠긴 1차 라벨이 보라 트랙·구간·채팅 표에 보임(엔진 트랙은 빔).
- `adj_adjudicate` 조정 세션: 불일치 2건 한쪽 고르기 + pattern_tag, '조정자 = 라벨러' 표시, 오른쪽 칸이 넓어짐.
- `adj_tools` 출처 판정 대상 만들기(1차 표본 chat_id 전부, 같은 목록은 다시 안 만듦) + relabel_second 계획(L03).
- `adj_cancel_plan` 남은 계획 때문에 보고서 메뉴가 막힘 → '세션 → 재라벨 계획 취소…' 로 둘 취소 → 다시 켜짐(계획 파일은 남음).
- `adj_origin` 출처 판정 세션: 대상 전부 판정(자동 결과 1건 받기), 다른 세션 기록·`samples\`·`relabel\` 를 열지 않음.
- `agree_cleanup` · `adj_cleanup` 사본·보고서 지움.

## How to get to it (user POV)

- 프로젝트 열기 → 세션 고르기 창 → **새 세션 → 1차 라벨링** / **두 번째 사람 재라벨**(계획이 있을 때) / **분석 보기** / **조정** / **출처 판정** / **이어 하기**.
- **세션 → 이 세션 잠그기(&L)…** → 요약 → 다음 → 확인 칸 → 잠그기.
- **세션 → 재라벨 계획 만들기(&P)…** → 같은 사람 / 두 번째 사람.
- **세션 → 재라벨 계획 취소(&C)…** (조정 세션에서만).
- **보고 → 일치도 보고서(&A)…** (두 세션이 잠긴 뒤, 남은 계획이 없을 때).
- 재라벨 세션의 위쪽 **[④ 후보]** 모드, 채팅 표 오른쪽 메뉴 '표본 밖 후보로 적기'.
- 조정 화면 아래 칸: 출처 판정 대상 만들기, relabel_second 계획 만들기.

## Driving it with gui_smoke

Preconditions:

- `--doctor` 가 `fail 0`, `<P>` 에 `chat\messages.jsonl`, 영상 `<V1>` 있음.
- 흐름은 사본에서 L01·L02(조정은 L03 계획도)를 처음부터 만든다 — 기존 세션·잠금이 필요 없다.

- **일치도 흐름.** Run `.venv\Scripts\python tools\verify_chatcoach.py --feature agreement`. 단계 `agree_copy → agree_open_a → agree_a_label → agree_a_lock_plan → agree_b_label → agree_b_lock_blind → agree_report → agree_analysis → agree_cleanup` 모두 PASS.
- **1차 잠그기·계획.** `agree_a_lock_plan locked=True modes_off=True verify_ok=True tau_block_set=True index_entries=1`.
- **블라인드 재라벨.** `agree_b_label kind=relabel_second … judged=3 primary_hidden=True`, `agree_b_lock_blind locked=True forbidden_opens=0 current_sample=<n ≥ 3>`.
- **보고서.** `agree_report files=2 outside_project=True comparison=inter same_machine=True judged_both=<≥1> chat_times=lookup current_sample_b=given`.
- **분석 세션에서 1차 보기.** `agree_analysis sessions=1 segments=2 timeline_segments=2 primary_markers=<= 판정 수> engine_markers=0`. 스크린샷 `_agree_b` · `_agree_analysis`.
- **조정 흐름.** Run `.venv\Scripts\python tools\verify_chatcoach.py --feature adjudication`. 단계 `adj_copy → adj_open_a → adj_a_label → adj_a_lock_plan → adj_b_label → adj_b_missed → adj_b_lock_blind → adj_report → adj_adjudicate → adj_tools → adj_cancel_plan → adj_origin → adj_cleanup` 모두 PASS.
- **④ 놓친 후보.** `adj_b_missed saved=2 extra_saved=1 all_in_block=True`, 보고서 `adj_report missed_judged_b=2 extra_b=1 md_missed_line=True md_links_line=True`.
- **조정.** `adj_adjudicate cases=<≥2> saved=2 adjudicator_is_labeler=True split=<왼쪽>:<오른쪽, 더 큼>`.
- **계획 취소.** `adj_cancel_plan report_blocked_before=True cancel_menu=True cancelled=2 report_enabled_after=True plan_files_kept=True index_status=['cancelled', 'cancelled']`.
- **출처 판정.** `adj_origin target_files=2 multi_warning=True saved=<= want> auto=1 forbidden_opens=0`. 스크린샷 `_adj_b` · `_adj_missed` · `_adj` · `_adj_origin`.
- **Proof.** `== verify: features=agreement rc=0 … fail=0`(또는 adjudication), `== 부수 효과: <P> 파일 바뀜 0 · 남은 사본 0`.

## Gotchas

- `agree_report`(·`adj_report`) 가 가끔 `judged_both=0` 으로 실패한다(오락가락, 원인 미확인 — 잠글 때 표본 수 계산 쪽 의심). 한 번 다시 돌려 또 실패하면 그대로 보고한다.
- 실제 창에서 조정 흐름 사본(`adj_copy`) 직후 외부 정지(Acronis, Suspended 17/19, CPU 0)가 여러 번 났다 — 코드 문제가 아니다. `--cleanup` 뒤 다시.
- 같은 PC 에서 두 라벨러를 흉내 내므로 보고서는 늘 `same_machine=True` 다. 다른 PC 경우는 이 흐름으로 검증되지 않는다.
- 열기 감시는 `builtins.open`·`io.open`·`os.open` 만 본다. 다른 경로(mmap·C 확장)로 여는 일은 잡지 못한다.
- 잠그기·계획·보고서·취소 대화상자는 `run=` 콜백으로 몬다(단추 누르기 대신 대화상자 메서드). 대화상자 단추 배치는 스크린샷으로만 본다.
