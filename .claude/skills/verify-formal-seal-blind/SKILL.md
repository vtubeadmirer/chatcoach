---
name: verify-formal-seal-blind
description: "AI 결과 봉인·해제(블라인드 보장, 06 7장)의 형식 검증. app/seal.py(unseal_conditions·unseal·attach_pairs·_guard·read_*·conditions_met_unseal_exists), app/agreement_report.py 의 comparison_gate·pending_relabel_plans, app/relabel.py 의 계획·취소, app/locking.py 의 잠금 쓰기, app/core/visibility.py 의 ai_sealed·reports 칸을 바꿨거나, 해제 조건·짝 봉인·자리 규칙을 손볼 때, 그리고 시범 해제 당일(tools/pilot_unseal_day.py --go) 전에 쓴다."
---

# 봉인·해제 블라인드 보장 형식 검증

## 무엇을 지키나

- **성질 (한 문장):** AI 결과는 계획된 라벨링 세션(1차 + 계획된 재라벨)이 모두 잠기고 표본·계획 재현 검증이 통과한 뒤에만 사유 없이 풀리고(명시 플래그 `allow_no_relabel`(08 D-26)은 계획 파일이 하나도 없을 때의 `no_relabel_plan` 하나만 넘을 수 있다), 한 번 풀린 것은 되돌려지거나 다시 풀리거나 다시 봉인되지 않으며, 라벨링·재라벨·출처 판정 세션은 그 전에도 뒤에도 봉인 폴더를 열지 못한다.
- **깨지면 (한 문장):** 사람이 재라벨하기 전에 AI 판정을 보게 되어 재라벨이 AI 쪽으로 쏠리고, 해제는 한 번뿐이라 시범 평가(pilot-* 3편)를 되돌릴 수 없게 망친다.

명세의 출처는 `docs/design/06-evaluation-and-agreement.md` 7-1(순서)·7-2(봉인)·7-3(열지 않음)과 `app/seal.py` 머리말이다.

## 도구와 까닭

`check_seal_blind.py` — 표준 라이브러리만 쓰는 **명시적 상태 모델 검사기**가 **실제 코드**를 디스크 위에서 돌린다.

- 합성 프로젝트 하나를 `%LOCALAPPDATA%\ChatCoachSmoke\pytest\formal-seal-*` 에 만들고, `seal.seal_bytes`·`seal.unseal`·`relabel.create_plan`·`relabel.cancel_plan`·`locking.write_lock_exclusive` 같은 실제 함수 호출 하나를 '동작' 하나로 삼아, 동작 묶음으로 갈 수 있는 **모든 상태를 끝까지**(깊이 제한 없음) 너비 우선으로 훑는다. 같은 상태인지는 디스크에서 읽은 추상 상태(세션 열림·잠김, 계획 있음·취소·읽지 못함, `seal.jsonl` 기록 요약, `runs\`·`topics\` 파일)로 가른다.
- 06 7-1 해제 조건은 이 파일의 `oracle_met`(설계 문장에서 따로 쓴 판정, 코드를 부르지 않음)과 실제 `seal.unseal_conditions` 를 **모든 상태에서** 견준다. 재라벨 없이 해제(08 D-26)도 모든 상태에서 `seal.unseal_conditions(allow_no_relabel=True)` 를 플래그 명세 `oracle_met(allow_no_relabel=True)`(계획 파일이 하나도 없을 때 'no_plan' 규칙 하나만 뺌)와 견주고, 플래그가 빼는 사유 코드가 `FLAG_WAIVABLE`(= `no_relabel_plan`) 밖으로 나가지 않는지 본다.
- 왜 이것인가: 이 PC 에는 Java(TLA+)도 hypothesis 도 없다. 손으로 만든 모델 대신 실제 코드를 돌리므로 모델과 코드가 어긋날 걱정이 없고, 무작위 표본이 아니라 정해진 범위 안의 **모든 순서**를 본다. 설치할 것이 없어 다음 사람이 그대로 돌린다.

## 실행 (한 줄, 저장소 루트, 약 80초 — PowerShell 이든 Git Bash 든 같다)

```
.venv\Scripts\python .claude\skills\verify-formal-seal-blind\check_seal_blind.py
```

- `--mutants` : 변이 자가 시험(약 15초). 일부러 깨뜨린 코드 11개와 틀린 모델 1개를 모두 '잡음' 해야 한다. 검증을 고친 뒤에는 꼭 돌린다.
- `--model gate_plans|gate_faults|seal|rerun|flag` : 모델 하나만. `--keep` : 임시 폴더를 남긴다(디스크를 들여다볼 때). `--no-watchdog` : 감시 부모 없이(디버깅).
- 시간: D-26 플래그 검사를 더하기 전 약 53초, 더한 뒤 약 80초, D-27 3 검사(상태마다 seal.jsonl 한 번 더 읽기)를 더한 뒤 약 90초(2026-10-24 이 PC). 늘어난 것은 상태마다 플래그 관문을 한 번 더 부르는 몫(선형)이고 상태 수는 그대로다 — 곱이 커지지 않게 플래그 해제를 이어 가는 동작은 `flag` 모델로 따로 뒀다.
- 종료 코드 0 통과 · 1 반례(또는 못 잡은 변이) · 2 준비 실패 또는 **멈춤**. 실제 자료(`chatcoach-data\projects\pilot-*`, `…\ai\pilot-*`)는 열지 않는다.
- 감시 부모: 검사는 자식 프로세스에서 돌고, 60초 동안 맥박이 없으면 부모가 자식을 끝내고 임시 폴더를 지운 뒤 `멈춤 …` 한 줄과 종료 2 를 낸다. 이것은 검증 결과가 아니다. 이 PC 에서는 파일을 몰아서 만들고 지우는 프로세스를 보안 프로그램(Defender·AhnLab 행위 감시로 보임)이 통째로 멈춰 세운 일이 있다(스레드 모두 `Suspended`, 2026-10-23 — 기본 네 모델에서는 여러 번 돌려 한 번도 없었다). 다시 돌리고, 되풀이되면 사용자에게 알린다. 보안 설정은 바꾸지 않는다.

## 결과 읽기

통과는 이렇게 보인다(상태 수가 바뀌면 동작 묶음이나 코드의 상태 공간이 바뀐 것이다 — 까닭을 찾는다):

```
통과  visibility 표: 라벨링·재라벨 세션 × 모든 단계에서 ai_sealed·reports·engine_results = none, 전사문은 토글뿐
통과  모델 gate_plans: 상태 72개 · 동작 696번 끝까지 훑음
통과  모델 gate_faults: 상태 135개 · 동작 1521번 끝까지 훑음
통과  모델 seal: 상태 129개 · 동작 997번 끝까지 훑음
통과  모델 rerun: 상태 8개 · 동작 48번 끝까지 훑음
통과  모델 flag: 상태 51개 · 동작 344번 끝까지 훑음
```

(gate_plans·gate_faults 의 동작 수가 D-26 전 624·1386 에서 는 것은 상태마다 `seal.unseal(R1, allow_no_relabel=True)` 시도가 하나 더해져서다. 상태 수는 같다.)

반례는 처음 상태에서 깨진 곳까지 **가장 짧은** 동작 순서로 나온다(너비 우선이라). 예 — `unseal_conditions` 에서 '잠기지 않은 라벨링 세션' 검사를 빼면:

```
반례  모델 gate_plans: 성질 '재라벨 없이 해제가 푸는 것은 no_relabel_plan 하나(D-26)' 이 깨짐 (0.6초, 상태 10개 훑던 중)
  0. 합성 프로젝트, seal.seal_bytes 로 R1(유효)·Rbad(받을 때 거부)을 먼저 봉인해 둠
  1. 1차 세션 A(L01-P-…ab12)를 연다 — labels/<A>.<m4>.jsonl 이 생긴다 → 성공 (열림)
  2. A 를 잠근다(locking.write_lock_exclusive) — 표본 재현 검증 통과 → 성공 (잠김)
  3. 재라벨 세션 B(L02-R2-…cd34)를 연다 — labels/<B>.<m4>.jsonl → 성공 (열림)
  위반: seal.unseal_conditions(allow_no_relabel=True).met=True (사유: 없음) 인데 플래그 명세 판정은 False (잠기지 않은 라벨링 세션 B)
  그때 디스크:
  labels/: A, B
  locks/: A(계획 None, 검증 ok)
  relabel/: 없음
  seal.jsonl: sealed R1 valid; sealed Rbad rejected
  runs/: 없음 · topics/: 없음
```

읽는 법: 1차 A 를 잠그고 계획 없이 B 를 열어 두면, 플래그 없는 관문은 `no_relabel_plan` 으로 우연히 막지만(그래서 이 버그가 가려진다), 플래그(`--allow-no-relabel`)는 그 사유를 빼므로 열린 B 를 못 본 코드가 '풀어도 된다' 고 답한다 — 해제 당일 도구가 이 상태에서 `seal.unseal(R1, allow_no_relabel=True)` 를 사유 없이 성공시킨다. 플래그 성질이 없을 때 같은 버그는 5단계(… pR2 를 만들고 B 를 열어 둔 채 pR2 를 취소)에서 성질 '해제 관문(06 7-1)' 으로 드러났다.

`unseal` 안의 `if not cond.met and early_reason is None: raise SealBlockedError` 를 빼면 더 짧다: `seal.seal_bytes(R1)` → `seal.unseal(R1)` 사유 없이 성공(`conditions_met=False`, 1차 세션 없음) — 성질 '해제 관문(06 7-1)'.

**진짜 버그인가, 모델 오류인가** — 위반 이름으로 가른다.

| 위반 | 뜻 | 먼저 볼 것 |
|---|---|---|
| 해제 관문(06 7-1) | 실제 `unseal_conditions.met` ≠ `oracle_met` | 두 사유 목록을 06 7-1 과 견준다. 설계가 oracle 편이면 코드 버그. 설계를 일부러 바꿨으면 `oracle_met` 을 고친다(아래 '바꿀 때') |
| 해제 관문 / 조건이 되면 풀린다(완전성) | 사유 없는 `unseal` 이 조건 안 될 때 풀림 / 될 때 막힘 | 앞쪽은 가장 위험한 버그(블라인드 깨짐). 뒤쪽은 해제 당일을 막는 버그 |
| 사유 코드가 명세와 맞음 | `unseal_conditions.codes` 가 사유 문장과 개수가 다르거나, 계획 코드(`plan_unreadable`·`relabel_not_locked`)가 그 상태에서 걸린 명세 규칙(`CODE_OF_RULE`)과 다름 | 코드 버그(해제 당일 요약이 엉뚱한 원인을 적음). 막는 판정은 맞을 수 있다 |
| 재라벨 없이 해제가 푸는 것은 no_relabel_plan 하나(D-26) | `unseal_conditions(allow_no_relabel=True)` 가 `no_relabel_plan` 밖의 사유를 빼거나(waived·codes), 계획 파일이 있는데 빼거나, met 이 플래그 명세와 다름 / 플래그 해제가 그 명세로 조건이 안 되는데 풀림 | 플래그 길의 버그면 가장 위험하다(해제 당일 `--allow-no-relabel --go` 가 블라인드를 깸). 다만 플래그는 `no_relabel_plan` 을 빼므로 **플래그와 무관한 관문 버그**(잠기지 않은 세션·표본 검증을 못 봄)도 여기서 먼저 보인다 — 플래그 없는 사유 목록과 견줘 가른다 |
| 재라벨 없이 해제는 기록에 남김 · 플래그 없는 해제 기록은 그대로 | 플래그 해제 기록에 `allow_no_relabel = true`·`relabel`(계획 파일 0개면 `none`, 있으면 `planned`)이 없음 / 플래그 없는 해제 기록에 그 칸이 생김 | 코드 버그(03 6-14 해제 기록 칸) |
| 잠금은 남지 않음 | 동작 뒤 `<ai>\<project_id>\.unseal.lock` 이 남음 | 코드 버그. 남으면 다음 쓰기가 모두 30초 기다리다 `seal_busy` — 반례 없이 '멈춤' 으로만 보이므로 이 성질로 먼저 잡는다 |
| 해제는 한 번(멱등) · 되돌릴 수 없음 · 같은 run_id 는 한 번만 풀림 · 추가 전용 | `ai.unsealed` 가 둘, 해제 상태가 사라짐, `runs\`·`sealed\`·`seal.jsonl` 앞부분이 바뀜 | 코드 버그 |
| 짝 보충은 다시 봉인하지 않음 · 해제 뒤 짝 없음 · 짝 보충은 사전 봉인 여부를 바꾸지 않음 | `attach_pairs`·`seal_topics_bytes` 가 판정 `ai.sealed` 를 늘림, 풀린 뒤 짝을 받음, presealed 가 바뀜 | 코드 버그 |
| 자리 검사(06 7-3) · 해제 전에는 읽지 않음 · 봉인 원본은 unseal 만 연다 | 라벨링 자리가 seal 함수를 부름, 해제 전 `read_*` 가 `runs\` 를 엶, `conditions_met_unseal_exists` 가 ai 폴더에서 `seal.jsonl` 밖을 엶, `unseal` 밖에서 `sealed\*.bin` 을 읽음 | 코드 버그(블라인드 깨짐) |
| 조건을 채운 해제 기록만 '잠김'(D-27 3) | `conditions_met_unseal_exists` 가 조건을 채운 `ai.unsealed`(conditions_met = true)가 없는데 True(앞당겨 연 기록을 셈) / 있는데 False | 앞쪽은 블라인드 깨짐(앞당긴 해제 하나로 화면 `pilot_sessions_locked` 가 열려 재라벨 전에 훈련·가상 채팅이 열림). 뒤쪽은 해제 뒤 화면이 1편 시범을 계속 막음 |
| 거절은 쓰지 않음 · 쓰는 곳 · 읽기는 쓰지 않음 | 거절된 호출이 봉인 폴더를 바꿈, 봉인 함수가 `data\ai` 밖(프로젝트 폴더)에 씀, 상태 보기 함수가 씀 | 새 쓰기 자리가 의도된 것이면 `World.allowed_write` 를 고친다 |
| 예상하지 못한 예외 | 동작이 뜻밖의 예외로 무너짐 | 세션 동작이면 대개 하네스 문제(함수 인자가 바뀜) — `_create_plan`·`_lock_session` 을 고친다. 봉인 동작이면 코드 버그일 수 있다 |

## 무엇을 덮나 (적용 기록)

**동기 커밋: `f012609`** (2026-10-24 확인, D-26 플래그 해제·D-27 3 해제 기록 판단 반영 뒤 check_seal_blind·--mutants 12/12 통과). 아래 파일·함수가 이 커밋 뒤로 바뀌었으면 검증을 다시 본다. `git diff f012609 -- app/seal.py app/agreement_report.py app/relabel.py app/locking.py app/core/visibility.py` 로 확인한다. 동기 커밋은 **검증이 새 커밋의 코드를 여전히 그대로 그린다고 확인한 뒤에만** 앞으로 옮긴다(이 파일의 이 줄을 고친다).

| 검사 | 덮는 코드 |
|---|---|
| visibility 표 | `app/core/visibility.py` `files_to_open`·`show_policy`·`can_show`·`can_reveal` (라벨링 3종 × 모든 단계) |
| gate_plans · gate_faults (해제 관문, 상태마다 사유 없는 해제 시도 — 플래그 없음·`allow_no_relabel=True` 둘 다) | `app/seal.py` `unseal_conditions`(met 과 사유 코드 `codes`·`unreadable_plans`, `allow_no_relabel` 의 `waived`·`plan_files`)·`_labeling_session_ids`·`_lock_docs`·`unseal`(관문·거절 길, `allow_no_relabel` 전달) · `app/agreement_report.py` `comparison_gate`·`_comparison_gate`(`PlanUnreadableError`)·`pending_relabel_plans`·`_locked_relabel_plan_ids`·`_read_lock_doc` · `app/relabel.py` `create_plan`·`list_plans`·`read_plan`·`is_cancelled`·`read_cancel`·`cancel_plan`·`verify_plan_on_disk`·`source_lock_info` · `app/locking.py` `write_lock_exclusive`·`lock_path`·`dump_lock` |
| seal (봉인·짝·보충·해제 × 관문 사슬, 상태마다 자리 검사) | `app/seal.py` `seal_bytes`·`seal_topics_bytes`·`_seal_pair_bytes`·`attach_pairs`·`_attach_pairs_locked`·`unseal`·`_unseal_pair`·`_append`·`_seal_lock`·`_lock_acquire`·`_lock_release`(동작마다 잠금이 남지 않는지만 — 기다림·`seal_busy`·낡은 잠금 지우기는 차례로 도는 모델에 없다)·`_sealed_record`·`_unsealed_record`·`_pair_record`·`_entry`·`_first_primary_lock_at`·`_is_presealed`·`seal_status`·`unseal_events`·`read_run`·`read_topics`·`_read_pair`·`_unsealed_or_blind`·`_guard`(라벨링·재라벨·출처 판정·분석 자리 × 17 함수 — `unseal_conditions`·`unseal` 은 `allow_no_relabel` 있음·없음 둘 다, `seal_file`·`read_words`·`read_vectors`·`seal_words_bytes`·`seal_vectors_bytes` 는 자리 검사만) |
| 화면의 시범 잠김 판단(08 D-27 3, 모든 모델·모든 상태) | `app/seal.py` `conditions_met_unseal_exists` — ai 폴더에서 `seal.jsonl` 밖(sealed·runs·topics)을 열지 않음(성질 '해제 전에는 읽지 않음'), 조건을 채운 `ai.unsealed`(conditions_met = true)가 있을 때만 True(성질 '조건을 채운 해제 기록만 '잠김'(D-27 3)'), 읽기는 쓰지 않음, 자리 검사(위 17 함수에 듦 — 분석 자리는 됨). 이것을 부르는 `app/reference_set.py` `pilot_sessions_locked` 는 덮지 않는다(`tests/test_reference_set.py` 몫) |
| rerun (같은 run_id 의 다른 결과) | `app/seal.py` `unseal` 의 `dup_run_id`·`already_unsealed` 길, `runs\<run_id>.json` 불변 |
| flag (재라벨 없이 해제, 08 D-26 — 관문 사슬 × R1 × 해제 셋, 플래그로 푼 뒤도 이어 감) | `app/seal.py` `unseal(allow_no_relabel=True)` 의 성공 길과 해제 기록 칸 `allow_no_relabel`·`relabel`, 플래그로 푼 뒤 계획·재라벨을 만든 상태의 관문 · 플래그 없는 해제 기록에 그 칸이 없음 |
| 지나가며 돌지만 성질을 따로 보지 않음 | `app/ai_results.py` `check_bytes`·`check_doc`·`normalize`·`project_chat_ids`, `app/project.py` `open_project`·`atomic_write`, `app/jsonl.py` |

동작 하나하나는 실제 함수 호출 하나다. 동작 이름과 문장은 `check_seal_blind.py` 의 `SESSION_ACTIONS`·`SEAL_ACTIONS`·`PROBES` 표에 있다.

**동기 커밋 뒤 바뀐 것(2026-10-23, 아직 commit 전 — 동기 커밋은 commit 뒤에 옮긴다):** 아래 '범위와 가정' 의 알려진 것 둘을 고쳤다. `app/seal.py` 에 seal.jsonl 잠금(`_seal_lock`·`_lock_acquire`·`_lock_release`, `seal_bytes`·`_seal_pair_bytes`·`attach_pairs`→`_attach_pairs_locked`·`unseal`·`_append` 가 씀)과 사유 코드(`UnsealConditions.codes`·`unreadable_plans`, `UNSEAL_REASON_CODES`, `SealError` 코드 `seal_busy`), `app/agreement_report.py` `_comparison_gate` 가 잠긴 재라벨 세션의 계획을 못 읽으면 `PlanUnreadableError`(`SampleVerifyError` 하위). 기본 네 모델의 상태·동작 수는 그대로(잠금 파일은 동작 안에서 생겼다 없어진다)이고, 성질 '사유 코드가 명세와 맞음'·'잠금은 남지 않음' 과 변이 `unreadable_as_pending`·`lock_leak` 을 더했다.

**D-26 반영(2026-10-24, 아직 commit 전 — 동기 커밋은 commit 뒤에 옮긴다):** `app/seal.py` `unseal_conditions(allow_no_relabel=False)`(계획 파일이 하나도 없을 때 `no_relabel_plan` 만 `waived` 로, `UnsealConditions.waived`·`plan_files`)·`unseal(allow_no_relabel=False)`(줄 때만 기록에 `allow_no_relabel`·`relabel`). 검증에 더한 것: 플래그 명세(`oracle_why(allow_no_relabel=True)`)·`FLAG_WAIVABLE`·성질 '재라벨 없이 해제가 푸는 것은 no_relabel_plan 하나(D-26)'(G1f, 모든 모델·모든 상태)·'재라벨 없이 해제는 기록에 남김'·'플래그 없는 해제 기록은 그대로', 시도 `probe_unseal_R1_flag`(gate 두 모델), 동작 `unseal_R1_flag` 와 모델 `flag`, 자리 검사의 `unseal_conditions_flag`·`unseal_flag`, 변이 `flag_bypasses_unlocked`(플래그가 잠기지 않은 세션도 넘음 — 1단계 반례로 잡힘). 기존 네 모델의 상태 수는 그대로다.

**D-27 3 반영(2026-10-24, 아직 commit 전 — 동기 커밋은 commit 뒤에 옮긴다):** `app/seal.py` `conditions_met_unseal_exists`(seal.jsonl 만 읽어 조건을 채운 해제 기록이 있는지 — `reference_set.pilot_sessions_locked` 가 True 면 '잠김'). 검증에 더한 것: 상태마다 G5b(seal.jsonl 밖을 열지 않음·기록과 맞음, 성질 '조건을 채운 해제 기록만 '잠김'(D-27 3)'), 자리 검사의 `conditions_met_unseal_exists`(분석 자리는 됨), 변이 `unseal_exists_counts_early`(앞당겨 연 기록도 셈 — seal 모델 2단계 반례로 잡힘). 다섯 모델의 상태·동작 수는 그대로다(동작이 아니라 상태마다 읽는 검사).

## 범위와 가정 (통과가 덮지 않는 것)

- **범위:** 합성 프로젝트 하나, 1차 세션 A 하나, 재라벨 세션 둘(B = 두 번째 사람 R2, RS = 같은 사람), 계획 둘(pR2·pRS) + 읽지 못하는 계획 파일 pX, 판정 R1(유효)·R1b(같은 run_id 의 다른 결과)·Rbad(받을 때 거부), 주제 흐름 짝 T1·T2. 이것을 다섯 모델로 나눠 각각 끝까지(깊이 제한 없이) 훑는다: gate_plans = A·B·RS·pR2·pRS 의 열기·잠그기·계획·취소(10 동작 + 해제 시도 3), gate_faults = A·B·pR2·pX 와 검증 실패·검증 없는 매니페스트·계획 ID 없는 잠금(12 동작 + 3), seal = A→pR2→B 사슬 × 봉인·짝 봉인·짝 보충·해제(10 동작), rerun = 사슬이 다 된 상태에서 R1·R1b 봉인·해제(6 동작), flag = A→pR2→B 사슬 × R1 봉인 × 해제 셋(사유 없음·앞당김·`allow_no_relabel`, 9 동작).
- **모델 사이의 곱은 보지 않는다:** 예를 들어 '읽지 못하는 계획 pX + 재라벨 둘(B·RS)' 이나 'RS 잠금의 검증 실패' 처럼 두 모델에 갈린 것이 한 상태에 같이 있는 경우. 세션 쪽 18 동작을 한 모델에 다 넣은 판(상태 약 970개)은 이 PC 에서 보안 프로그램이 2~3초 만에 프로세스를 멈춰 세워 끝까지 돌리지 못해 뺐다.
- **추상화:** 상태는 추상 열쇠(`Facts.key`)로 합친다. 시각·엔진 칸 같은 세부가 다른 상태는 한 대표에서만 본다. 시계는 동작마다 1분씩 앞으로만 간다(PC 시계 조작은 06 7-4 대로 범위 밖).
- **잠금 파일:** `ProjectStore.lock_session` 을 돌리지 않는다. 관문이 읽는 칸(session_id·locked_at·relabel_plan_id·sample_manifests·sample_verify)만 가진 잠금을 실제 `locking.write_lock_exclusive` 로 쓴다. 표본 재현 검증 **계산**(store 의 표본 검증, `samples\` 매니페스트, 계획 재현 τ)은 돌리지 않고 결과(ok/false/빔)만 넣는다 — `samples\` 는 비어 있어 계획 재현 검증은 늘 통과한다.
- **차례로만:** 동작은 하나씩 끝까지 돈다. 두 프로세스가 동시에 `unseal` 하는 경쟁, 쓰다가 꺼짐(runs 는 썼는데 기록 전), `seal.jsonl` 손상은 보지 않는다.
  - 고침(2026-10-23, 처음 찾을 때는 손으로 흉내 냄): 한 프로세스가 '아직 안 풂' 을 본 뒤 기록하기 전에 다른 프로세스가 같은 봉인을 끝까지 풀면 `ai.unsealed` 가 두 줄 생기던 일. 이제 seal.jsonl 에 쓰는 곳은 모두 `<ai>\<project_id>\.unseal.lock`(O_EXCL 잠금 파일)을 잡고, `unseal` 은 잠금을 얻은 뒤 기록을 다시 읽어 늦은 쪽이 `AlreadyUnsealedError` 를 낸다. **경쟁 자체는 이 검증이 아니라** `tests/test_seal.py` 의 두 스레드·두 프로세스 시험이 본다(잠금을 끄면 두 줄이 생기는 것을 확인함). 이 검증은 동작마다 잠금이 남지 않는지만 본다.
  - 고침: 읽지 못하는 계획 파일이 '잠긴 재라벨 세션이 없는 계획 RP-…'(→ `relabel_not_locked`)로 적히던 일. 이제 사유 코드 `plan_unreadable`(계획 ID 는 `unreadable_plans`)이고, 성질 '사유 코드가 명세와 맞음' 이 모든 상태에서 본다.
- **덮지 않는 코드:** `tools/pilot_unseal_day.py`(`--allow-no-relabel`·`--only` 포함 — 그것은 `tests/test_pilot_unseal_day.py` 몫)·`tools/ai_seal.py`(이 둘이 부르는 `seal.unseal_conditions`·`seal.unseal` 은 덮는다)·`tools/viewer_score_calib.py` `_pilot_locked(allow_no_relabel)`, 1차 세션 둘 이상에서 한쪽만 계획이 있는 경우(플래그가 풀지 않아야 함 — 코드는 계획 파일이 하나라도 있으면 풀지 않는다), UI(`ui/analysis_view.py`)·store 의 열기 감시(이것은 `tests/test_blind_ai.py` 몫), 짝 words·vectors 의 봉인·해제 본체(같은 `_PairSpec` 길이지만 자리 검사만), 1차 세션 둘 이상, 조정·출처 판정 세션 잠금, 재라벨 3일 대기(`start_gate`).
- **전사문 `derived\asr*`:** 라벨링 세션은 '절대 안 읽음' 이 아니라 '기본은 안 읽음, 켜면 `view.exposed` 를 남기고 연다'(visibility `TOGGLE`, 03 12-1)이다. 표 검사는 기본으로 열지 않는다는 것만 본다.

## 바꿀 때 (변경이 의도된 것일 때)

1. 바뀐 덮는 함수를 읽고, 바뀐 것이 06 7-1~7-3 의 규칙인지(명세가 바뀜), 새 자료·자리·동작인지(모델이 넓어져야 함), 리팩터링인지 가른다.
2. 규칙이 바뀌었으면 설계 문서를 먼저 고치고 `oracle_met` 과 `ORACLE_RULES` 를 그 문장대로 고친다. 코드를 보고 oracle 을 맞추지 않는다 — oracle 은 설계에서 쓴다.
3. 새 짝 종류·새 봉인 함수·새 자리 종류면 `SEAL_ACTIONS`·`blind_calls`·`BLIND_KINDS` 에 더하고, 새 세션·계획 모양이면 `SESSION_ACTIONS` 와 모델의 동작 묶음에 더한다. 함수 인자만 바뀌었으면 해당 `_create_plan`·`_lock_session`·`_seal*` 만 고친다.
4. 기본 실행과 `--mutants` 를 돌린다. 모든 모델 통과 + 변이 12개 모두 '잡음' 이어야 한다. 새 규칙에는 그것을 빼는 변이를 `MUTANTS` 에 하나 더한다(못 잡는 검사는 증명이 아니다).
5. 실행 시간이 1분을 넘으면 동작을 새 모델로 나눈다(곱이 커지지 않게). 기본 실행을 느리게 두지 않는다.
6. 위 '적용 기록' 의 동기 커밋과 표를 고친다.

반례가 나왔는데 성질을 약하게 고쳐 통과시키지 않는다. 반례는 위 단계 순서 그대로 보고하고, 제품 코드는 사용자가 요청할 때만 고친다.
