# 출처: vtubeadmirer/clipslicer@07d13cc tools/gui_smoke.py 의 틀(실제 MainWindow 를 띄워 단계별 PASS/FAIL 출력, 종료 코드
#   0 = 통과, 임시 설정 폴더)을 가져왔다. 변경: 단계는 ChatCoach 작업대(프로젝트 열기 → 영상 → 탐색 → 채팅 → 타임라인)로
#   새로 썼고, QTimer 대신 이벤트를 돌리며 기다리는 순차 실행, 단계마다 걸린 시간, 명령줄 인자, 스크린샷 위치 제한을 더했다.
"""GUI 스모크 시험 (pytest 아님): 실제 앱 창으로 프로젝트를 열고 작업대가 도는지 순서대로 본다.

사용:
  .venv\\Scripts\\python tools\\gui_smoke.py --project <프로젝트 폴더> [--video <영상 파일>] [--offscreen]
        [--screenshot <png>] [--store-copy] [--labeling-flow] [--timeout 초]
  .venv\\Scripts\\python tools\\gui_smoke.py --screenshot-cleanup [c4,c5,c6] [--yes]
        스모크를 돌리지 않고 스크린샷 폴더의 묶음 스크린샷을 휴지통으로 보낸다(--yes 없으면 개수만, 15 5-2 의 11).

끝에는 스모크 데이터 폴더의 채팅 색인 캐시를 지운다(cache_drop, 15 5-2 의 10). 네 흐름을 다 켜면 단계 이름을
14(c5) 의 49 단계와 대조해(STEPS_C5 → expected_steps) 빠진 단계가 있으면 FAIL 로 끝난다(15 5-2 의 16).

단계: 창 → 프로젝트 열기(파일 메뉴와 같은 길: ProjectView.open_path, 세션 선택 화면은 '보기 전용') → 채팅 싣기 →
      타임라인 밀도 → 영상 로드(project.json 에 저장하지 않음) → 정확 탐색 → 채팅 활성화(−3초 탐색 + 하이라이트) →
      재생 1.5초 → 타임라인 확대 → 채팅 필터 → (--store-copy: project.json·guide 만 담은 임시 사본에서 라벨 저장·되돌리기,
      보호 구역·채팅은 복사하지 않음) → (--labeling-flow: 아래) → 스크린샷 → 닫기(잠금 해제·project.json 그대로).

--labeling-flow (08 D-09·D-10, 02 7-3·7-4): 한글 임시 폴더에 project.json · guide/ · chat/messages.jsonl 만 복사한 사본을
  메인 창으로 연다 → 세션 선택 화면(SessionDialog)에서 새 1차 세션 → store(QLockFile) → 상태 칩이 메인 창 상태 줄 하나로 →
  영상 연결(사본의 project.json 에만 저장: 영상이 덮는 VOD 범위는 project.json 으로 정해진다, D-10 3) →
  ① 구간: B 로 경계 하나(영상 끝 2분 전), 구간 2개(저스트채팅 · 게임) 저장(바쁨 표시 켜짐·꺼짐), 구간 완료 표시 →
  ② 반응: 영상 범위 안 채팅 1건에 M → 편집기: 강한 증거(닉네임 호명)·직접 반응, 7-2 범위를 2배속으로 두 번 들음(재확인) → 저장,
     반응 완료 표시 →
  ③ 표본: 저스트채팅 구간 표본 뽑기(sha256-tau-v2, 모든 표본의 확인 범위가 영상 안인지) · 재현 검증 → 1건 판정(반응 미확인) →
     대기 표본 하나에 ② 반응을 만들고 ③ 으로 돌아와: 판정함으로 세는지, 반응 미확인 단추가 막히는지, M 이 그 ② 라벨을
     고치는 편집기를 여는지, 고친 것이 같은 대상인지(D-10 4) →
  내보내기 zip(사본 밖 임시 폴더, 채팅마다 반응 라벨 1건) → 사본을 다시 열어(이어 하기 세션) 현재 유효 라벨이 같은지 → 지운다.
  영상(--video)이 꼭 있어야 한다: 영상이 없으면 영상 밖 규칙(D-10 3) 때문에 구간도 저장되지 않아 flow_media 에서 멈춘다.
  --window-size 1366x768 이면 흐름 동안 창 크기를 맞추고, 구간 폼·편집기·큐가 잘리는지 숫자(fit=…)로 찍는다.
  스크린샷(--screenshot 이름 뒤 _flow_segments · _flow_edit · _flow)을 남긴다.

--agreement-flow (06 3장·4장·5장·7장, 08 D-11 6·7·8, D-09 5): 같은 모양의 사본(한글 경로 …\\projects\\프로젝트)에서
  L01 1차: 영상 연결 → 구간 2개(둘 다 저스트채팅, 경계는 10분 블록 경계) → 구간마다 표본 뽑기·앞 3건 판정(직접·미확인 확실함·
  미확인 아마도) → 메뉴 길로 잠그기(재현 검증 결과가 잠금에 남음) → 재라벨 계획(두 번째 사람 L02) →
  L02 두 번째 사람 세션(세션 화면에서 relabel_second, 같은 PC 라 '같은 machine_id'): builtins.open·io.open·os.open 을 감시해
  L01 기록 파일·L01 잠금·samples/ 를 한 번도 열지 않는지 → 블록 안 구간 1개 → 계획의 블록 표본(1차 매니페스트의 순서 키·τ_block)
  3건 판정(직접·확실함·판단 불가), 잠긴 1차 라벨이 화면 어디에도 없는지 → 잠그기 → 감시 끝 →
  보기 전용으로 열어 '보고 → 일치도 보고서' (사본 옆 임시 폴더, 프로젝트 밖) → L01 분석 세션으로 열어 잠긴 1차 라벨이
  타임라인(보라 트랙·구간)과 채팅 표에 보이는지(엔진 트랙은 비어 있음). 스크린샷 _agree_b · _agree_analysis. 끝나면 다 지운다.
  (두 흐름의 앞부분 — 1차·잠그기·계획·두 번째 사람·잠그기·보고서 — 는 공통 도우미 _flow_primary · _flow_second ·
  _flow_report 로 돈다(14 4-2 의 15). 잠그기 전 재현 검증·보고서는 워커로 돌고(바쁨 표시), 보고서의 채팅 시각은 채팅 색인,
  재라벨 잠금에는 current_sample 이 들어간다 — 단계 이름 앞에 agree_ · adj_.)

--adjudication-flow (08 D-12 2~7, 06 2-9·4-6·4-7·5장·7-1, 03 6-10~6-12·12-1·12-3): 같은 모양의 사본에서
  L01 1차(구간 2·표본 판정·③ 완료, 1차에는 ④ 단추가 없음) → 잠그기 → 계획(L02, relabel/index.json 1줄) →
  L02 재라벨(열기 감시: L01 기록·잠금·samples/ 0건): 블록 안 구간·③ 3건 → ③ 완료 표시 → ④ 놓친 후보 2건(후보 1·사유 C 1,
  잠그기 전, 채팅 표 오른쪽 메뉴로 표본 밖 후보 1건 더) → 잠그기 → 보기 전용에서 일치도 보고서(놓친 후보·연결 줄) →
  조정 세션(오른쪽 칸이 넓어짐): 불일치 2건을 한쪽 고르기 + pattern_tag (조정자 = 라벨러 표시) → [출처 판정 대상 만들기]
  (1차 표본 chat_id 전부, 같은 목록은 다시 만들지 않음, D-13 5) → relabel_second 계획(L03) 만들기 → L01 1차를 다시 열어
  별도 계획(같은 사람 재라벨) → 보고서 메뉴가 막힘 → 조정 세션에서 '세션 → 재라벨 계획 취소…' 로 두 계획 취소 →
  보고서 메뉴가 다시 켜짐(계획 파일은 남고 취소 기록·색인 cancelled) → 합성 자동 결과 대상(3건·자동 1건)을 더해
  출처 판정 세션에서 전부 판정(다른 세션 기록·samples/·relabel/ 을 열지 않음). 스크린샷 _adj_b · _adj_missed · _adj ·
  _adj_origin. 끝나면 다 지운다.

--analysis-flow (E 첫 묶음, 08 D-01·D-15 7·8, 06 6장·7장): 같은 모양의 사본(…\\projects\\프로젝트, ai 루트는 그 옆 …\\ai)에서
  보기 전용으로 열어 '분석 → AI 결과 가져오기(봉인)' (합성 엔진 결과: 사본의 chat_id 만, 등급은 chat_id 해시로) →
  '봉인 해제' 가 조건 미충족으로 거부 → L01 1차·L02 재라벨을 기존 흐름(_flow_primary·_flow_second)으로 잠금(그동안 ai 폴더를
  한 번도 열지 않음, 잠긴 두 세션을 다시 열어도 분석 메뉴가 꺼짐) → L01 분석 세션: 나란히 보기가 '봉인됨 1건' 으로 막힘(봉인
  파일·runs 를 열지 않음) → 보기 전용에서 해제(조건 됨, 사전 봉인) → L01 분석 세션 '엔진과 나란히 보기'(목록·점수 표·
  다음 다른 곳, --window-size 면 그 크기로, 스크린샷 _an_side) → 보기 전용 '보고 → AI 비교 보고서'(사본 옆 임시 폴더).
  끝나면 다 지운다. 단계 이름 앞에 an_.

지키는 것
- 원본 프로젝트에는 쓰지 않는다: 영상 연결은 persist=False, 세션 없이(보기 전용) 열어 라벨 기록도 열지 않는다.
  열기 잠금(locks/open.lock)만 잡았다 푼다. 끝에 project.json 수정 시각과 크기가 그대로인지 확인한다.
- 사본(--store-copy·--labeling-flow)은 끝나면 지우고, 남으면 FAIL 과 경로를 찍는다. 보호 구역(chat/protected·
  derived·origin)·기존 라벨·영상은 복사하지 않는다.
- 설정·machine.json 은 임시 데이터 폴더(--data-dir, 기본 %TEMP%\\chatcoach_smoke)에 둔다.
- 출력은 숫자와 통과 여부만. 채팅 본문·닉네임·제목은 찍지 않는다(02 10장).
- 스크린샷은 저장소 밖 <저장소 옆>\\chatcoach-data\\screens\\ 안에만 저장한다. 다른 곳을 주면 거부한다.

offscreen 에서는 플레이어가 vo=null·ao=null 로 돌아(진짜 창 핸들이 없다) 스크린샷의 영상 칸은 비어 있고, 글꼴이 없어
글자가 네모로 보일 수 있다. 영상 그림까지 보려면 --offscreen 없이 실제 창으로 돌린다.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS_DIR = os.path.join(os.path.dirname(ROOT), "chatcoach-data", "screens")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="ChatCoach GUI 스모크 시험")
    ap.add_argument("--project", help="프로젝트 폴더 (project.json 이 있는 곳). --screenshot-cleanup 만 할 때는 필요 없음")
    ap.add_argument("--video", help="영상 파일 (저장하지 않고 이번에만 연결)")
    ap.add_argument("--offscreen", action="store_true", help="화면 없이 (QT_QPA_PLATFORM=offscreen)")
    ap.add_argument("--screenshot", help=f"창 스크린샷 png (반드시 {SCREENS_DIR} 안)")
    ap.add_argument("--store-copy", action="store_true", help="프로젝트를 임시 폴더로 복사해 라벨 저장·되돌리기도 시험")
    ap.add_argument("--labeling-flow", action="store_true",
                    help="사본(project.json·guide·messages.jsonl)에서 세션→구간→반응→표본→내보내기→다시 열기")
    ap.add_argument("--agreement-flow", action="store_true",
                    help="사본에서 L01 1차→잠그기→재라벨 계획→L02 두 번째 사람(열기 감시)→잠그기→일치도 보고서→분석 세션")
    ap.add_argument("--adjudication-flow", action="store_true",
                    help="사본에서 L01 1차·L02 재라벨(④ 놓친 후보 포함) 잠금→보고서→조정 2건→계획 취소→출처 판정 3건")
    ap.add_argument("--analysis-flow", action="store_true",
                    help="사본에서 합성 엔진 결과 봉인→해제 거부→L01·L02 잠금→해제→분석 세션 나란히 보기→AI 비교 보고서")
    ap.add_argument("--window-size", help="--labeling-flow 동안 메인 창 크기 (예: 1366x768, 테두리 포함)")
    ap.add_argument("--data-dir", help="임시 데이터 폴더 (기본 %%TEMP%%\\chatcoach_smoke)")
    ap.add_argument("--timeout", type=float, default=60.0, help="단계 하나의 대기 상한(초)")
    ap.add_argument("--screenshot-cleanup", nargs="?", const=",".join(CLEANUP_DEFAULT_PREFIXES), metavar="접두어",
                    help=f"스모크를 돌리지 않고 {SCREENS_DIR} 의 스크린샷(<접두어>_*.png, 기본 "
                         f"{','.join(CLEANUP_DEFAULT_PREFIXES)})을 휴지통으로 보낸다. --yes 가 없으면 개수만 보인다")
    ap.add_argument("--yes", action="store_true", help="--screenshot-cleanup 을 실제로 한다(휴지통)")
    return ap.parse_args(argv)


# ---------------------------------------------------------------------------- 스크린샷 정리 (15 5-2 의 11번)
# 사용자가 본 뒤 지우는 묶음 스크린샷 접두어. 실제 채팅이 찍혔을 수 있어 휴지통으로만 보낸다(바로 지우지 않음).
CLEANUP_DEFAULT_PREFIXES = ("c4", "c5", "c6")
_PREFIX_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def screenshot_cleanup_targets(folder: str, prefixes) -> list[str]:
    """folder 바로 아래(하위 폴더·링크 제외)의 '<접두어>_*.png' 파일 경로(이름순). 접두어는 소문자·숫자·'-' 만."""
    pre = [p.strip().lower() for p in prefixes if p and p.strip()]
    if not pre or any(not set(p) <= _PREFIX_OK for p in pre):
        raise ValueError("접두어는 영문 소문자·숫자·'-' 만 쓸 수 있습니다")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        low = name.lower()
        if not low.endswith(".png") or not any(low.startswith(p + "_") for p in pre):
            continue
        path = os.path.join(folder, name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        out.append(path)
    return out


def send_to_recycle_bin(paths: list[str]) -> bool:
    """Windows 휴지통으로 보낸다. 앱의 backup.send_to_trash 를 그대로 쓴다: 휴지통이 받는지(드라이브 종류 · subst ·
    휴지통 꺼짐 · 최대 크기) 먼저 보고, 못 받는 곳이면 지우지 않고 False. 영구 삭제로 넘어가지 않는다."""
    if not paths:
        return True
    if os.name != "nt":
        return False
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from app import backup
    if any(backup.can_trash(p) for p in paths):      # 하나라도 못 받으면 아무것도 보내지 않는다
        return False
    try:
        for p in paths:
            backup.send_to_trash(p)
    except OSError:                                  # TrashError 포함
        return False
    return True


def screenshot_cleanup(prefixes, *, yes: bool, folder: str | None = None, trash=None) -> int:
    """--screenshot-cleanup: SCREENS_DIR 안의 묶음 스크린샷을 휴지통으로. 출력은 개수·바이트만(파일 이름 안 찍음).
    yes 가 아니면 세기만 한다. 종료 코드 0 = 됨(또는 셈만), 1 = 휴지통으로 못 보냄(파일은 그대로)."""
    folder = folder or SCREENS_DIR
    targets = screenshot_cleanup_targets(folder, prefixes)
    size = sum(os.path.getsize(p) for p in targets)
    if not yes:
        print(f"스크린샷 정리(셈만): {len(targets)}개, {size} bytes — 휴지통으로 보내려면 --yes")
        return 0
    ok = (trash or send_to_recycle_bin)(targets)
    left = sum(1 for p in targets if os.path.exists(p))
    print(f"스크린샷 정리: {len(targets)}개 휴지통으로 {'보냄' if ok and not left else '못 보냄'} (남은 {left}개)")
    return 0 if ok and not left else 1


# ---------------------------------------------------------------------------- 단계 목록 (15 5-2 의 16번)
# 14(c5) 보고의 49 단계와 지금 단계의 대조. 네 흐름(--store-copy --labeling-flow --agreement-flow --adjudication-flow)을
# 다 켜고 돌렸을 때 이 이름들이 모두 찍혀야 한다(빠지면 FAIL 로 끝난다).
#   c5 → c6: agree_a_segments + agree_a_samples → agree_a_label (같은 확인 + ③ 완료·타임라인 부분 갱신),
#            agree_open_b + agree_b_segments + agree_b_samples → agree_b_label (같은 확인 + ④ 문이 ③ 완료 전 닫힘),
#            agree_copy 새로(전에는 agree_open_a 의 copied 숫자), adj_tools 새로(출처 대상·relabel_second 계획) → 49 − 4 + 2 + 1 = 48.
#   c6 정리: cache_drop (스모크 데이터 폴더의 채팅 색인 캐시를 끝에 지움, 15 5-2 의 10번) → 49.
STEPS_C5 = (
    "window", "open_project", "chat_loaded", "timeline_density", "video_load", "waveform", "seek", "chat_activate",
    "play", "timeline_zoom", "chat_filter", "store_copy", "screenshot",
    "flow_open", "flow_media", "flow_segments", "flow_reaction", "flow_sample_draw", "flow_sample_judge",
    "flow_sample_stage2", "flow_layout", "flow_export", "flow_reopen", "flow_close", "flow_cleanup",
    "agree_open_a", "agree_a_segments", "agree_a_samples", "agree_a_lock_plan", "agree_open_b", "agree_b_segments",
    "agree_b_samples", "agree_b_lock_blind", "agree_report", "agree_analysis", "agree_cleanup",
    "adj_copy", "adj_open_a", "adj_a_label", "adj_a_lock_plan", "adj_b_label", "adj_b_missed", "adj_b_lock_blind",
    "adj_report", "adj_adjudicate", "adj_cancel_plan", "adj_origin", "adj_cleanup", "close")
STEPS_RENAMED = {"agree_a_segments": "agree_a_label", "agree_a_samples": "agree_a_label",
                 "agree_open_b": "agree_b_label", "agree_b_segments": "agree_b_label",
                 "agree_b_samples": "agree_b_label"}
STEPS_ADDED = ("agree_copy", "adj_tools", "cache_drop")


# --analysis-flow (E 첫 묶음) 의 단계. 켜면 STEPS_C5 대조 목록 뒤에 붙고, 건너뛴 표시 'analysis_flow' 는 빠진다.
STEPS_ANALYSIS = ("an_copy", "an_seal", "an_open_a", "an_a_label", "an_a_lock_plan", "an_b_label", "an_b_lock_blind",
                  "an_labeling_blind", "an_view_sealed", "an_unseal", "an_side_by_side", "an_report", "an_cleanup")


def expected_steps(analysis: bool = False) -> list[str]:
    """지금 판에서 네 흐름을 다 켰을 때 찍혀야 하는 단계 이름(c5 목록을 STEPS_RENAMED 로 바꾸고 STEPS_ADDED 를 더함).
    analysis=True(--analysis-flow 도 켬)면 cache_drop 앞에 STEPS_ANALYSIS 를 넣는다."""
    out: list[str] = []
    for s in STEPS_C5:
        s = STEPS_RENAMED.get(s, s)
        if s not in out:
            out.append(s)
    out += [s for s in STEPS_ADDED if s not in out]
    if analysis:
        i = out.index("cache_drop") if "cache_drop" in out else len(out)
        out[i:i] = [s for s in STEPS_ANALYSIS if s not in out]
    return out


def compare_steps(names, analysis: bool = False) -> tuple[list[str], list[str]]:
    """찍힌 단계 이름 → (빠진 것, 목록에 없는 것). 'analysis_flow'(건너뜀 표시)는 대조하지 않는다."""
    seen = [s for s in dict.fromkeys(names) if s != "analysis_flow"]
    want = expected_steps(analysis)
    return [s for s in want if s not in seen], [s for s in seen if s not in want]


# --store-copy 가 임시 폴더로 복사하는 것: 라벨 저장·되돌리기에 필요한 최소한만(03 2-3). 보호 구역(chat/protected·
# derived/asr*·derived/ocr*·origin/)은 물론 채팅 원문(messages.jsonl)·수집 기록·파생 자료·기존 라벨·영상도 옮기지 않는다
# — 실행을 중간에 끊거나 지우기가 실패해도 TEMP 에 개인정보가 남지 않게. 채팅이 없으니 작업대 띠에 '채팅 파일 없음' 이 뜬다.
STORE_COPY_FILES = ("project.json",)
STORE_COPY_DIRS = ("guide",)


def copy_for_store_test(src: str, dst: str) -> list[str]:
    """src 프로젝트에서 STORE_COPY_FILES·STORE_COPY_DIRS 만 dst 로 복사한다. 복사한 상대 경로 목록."""
    os.makedirs(dst, exist_ok=True)
    done = []
    for name in STORE_COPY_FILES:
        p = os.path.join(src, name)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dst, name))
            done.append(name)
    for name in STORE_COPY_DIRS:
        p = os.path.join(src, name)
        if os.path.isdir(p):
            shutil.copytree(p, os.path.join(dst, name))
            done.append(name + "/")
    return done


# --labeling-flow 가 복사하는 것: 위 둘 + 채팅(chat/messages.jsonl). 수집 기록·원문 페이지·솔트·원 닉네임(chat/protected)·
# 파생 자료(derived: 전사문·엔진 결과)·기존 라벨·표본·영상은 옮기지 않는다.
FLOW_COPY_FILES = STORE_COPY_FILES + (os.path.join("chat", "messages.jsonl"),)


def copy_for_flow(src: str, dst: str) -> list[str]:
    """라벨링 흐름용 사본: project.json · guide/ · chat/messages.jsonl 만. 복사한 상대 경로 목록."""
    done = copy_for_store_test(src, dst)
    rel = os.path.join("chat", "messages.jsonl")
    p = os.path.join(src, rel)
    if os.path.isfile(p):
        os.makedirs(os.path.join(dst, "chat"), exist_ok=True)
        shutil.copy2(p, os.path.join(dst, rel))
        done.append(rel.replace(os.sep, "/"))
    return done


def remove_tree_checked(path: str) -> list[str]:
    """path 를 지우고, 지우지 못한 파일 목록을 돌려준다(빈 목록 = 다 지움). 실패를 조용히 넘기지 않는다."""
    failed: list[str] = []

    def onexc(_func, p, _exc):
        failed.append(p)
    if os.path.exists(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=onexc)
        else:
            shutil.rmtree(path, onerror=onexc)
    if os.path.exists(path) and not failed:
        failed.append(path)
    return failed


def synthetic_engine_result(project_root: str, run_id: str = "run-smoke-synth") -> bytes:
    """--analysis-flow 의 합성 엔진 결과(ai-result 1판). 사본의 chat_id 만 쓰고(본문은 읽지 않음) 등급은 chat_id 의
    sha256 으로 정한다(약 8%: 직접 3% · 높음 3% · 관련성만 2%, 나머지 반응 미확인). 엔진 판정이 아니라 화면·보고서 시험용."""
    import hashlib
    import json
    from app import ai_results as air
    from app.project import open_project
    data = open_project(project_root).data or {}
    rx = []
    for c in sorted(air.project_chat_ids(project_root)):
        v = int(hashlib.sha256(c.encode("ascii")).hexdigest()[:2], 16)
        grade = "direct" if v < 8 else "likely" if v < 16 else "related_only" if v < 21 else None
        rx.append({"chat_id": c, "result": "graded" if grade else "unconfirmed", "grade": grade})
    doc = {"schema": "chatcoach/ai-result", "schema_version": 1, "run_id": run_id,
           "created_at": "2026-10-22T09:00:00+09:00",
           "engine": {"name": "smoke-synthetic", "version": "0.0.0", "preregistered": True},
           "target": {"platform": "chzzk", "video_no": str((data.get("broadcast") or {}).get("video_no")),
                      "chat_id_algorithm": "sl-chat-id-v1",
                      "messages_sha256": (data.get("chat") or {}).get("messages_sha256")},
           "time_axis": "vod_ms", "reactions": rx}
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def _hms(ms: int) -> str:
    """근거 칸에 넣는 VOD 시각 'h:mm:ss' (안내서: 근거에 시각)."""
    from app.core.timecode import fmt_time
    return fmt_time(max(0, int(ms)), ms_digits=False, always_hours=True)


def _inside(path: str, folder: str) -> bool:
    p, f = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return os.path.commonpath([p, f]) == f


class Smoke:
    def __init__(self, args):
        self.args = args
        self.results: list[tuple[str, bool, float, str]] = []
        self.app = None
        self.win = None

    # ---------------------------------------------------------------- 도우미
    def check(self, name: str, ok: bool, t0: float, detail: str = "") -> bool:
        ms = (time.perf_counter() - t0) * 1000
        self.results.append((name, bool(ok), ms, detail))
        print(f"{'PASS' if ok else 'FAIL'} {name:<22} {ms:8.0f} ms  {detail}", flush=True)
        return bool(ok)

    def pump(self, ms: int = 0) -> None:
        end = time.perf_counter() + ms / 1000
        while True:
            self.app.processEvents()
            if time.perf_counter() >= end:
                return
            time.sleep(0.005)

    def wait(self, cond, timeout_s: float | None = None) -> bool:
        end = time.perf_counter() + (timeout_s or self.args.timeout)
        while time.perf_counter() < end:
            self.app.processEvents()
            try:
                if cond():
                    return True
            except Exception:  # noqa: BLE001 — 아직 준비 안 됨
                pass
            time.sleep(0.01)
        return False

    @property
    def wb(self):
        return self.win.workbench

    # ---------------------------------------------------------------- 준비
    def setup(self) -> None:
        a = self.args
        if a.offscreen:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        sys.path.insert(0, ROOT)
        from app import paths
        data = a.data_dir or os.path.join(tempfile.gettempdir(), "chatcoach_smoke")
        os.makedirs(data, exist_ok=True)
        paths.set_data_root(data)
        self.data_root = data

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.app.setApplicationName("ChatCoachSmoke")
        import main as main_mod
        main_mod.apply_dark_theme(self.app)

    # ---------------------------------------------------------------- 단계
    def step_window(self) -> bool:
        t0 = time.perf_counter()
        from app import updater
        from app.appspec import SPEC
        from app.ui.mainwindow import MainWindow
        installed = updater.load_installed(SPEC)
        self.win = MainWindow(SPEC, installed, self.data_root, "", None, [], set())
        self.win.check_updates = lambda *a, **k: None          # 스모크 중에는 네트워크 확인을 하지 않는다
        # 원본 프로젝트는 세션 선택 화면에서 '보기 전용' 을 고른 것과 같게 연다(라벨 기록을 열지 않는다)
        self.chosen: list = []
        self.win.session_chooser = lambda outcome: self.chosen.append(outcome.project.root) or None
        self.win.show()
        self.pump(50)
        return self.check("window", self.win.isVisible(), t0, f"{self.win.width()}x{self.win.height()}")

    def step_open(self) -> bool:
        t0 = time.perf_counter()
        proj_json = os.path.join(self.args.project, "project.json")
        st = os.stat(proj_json)
        self.pj_before = (st.st_mtime_ns, st.st_size)
        # 파일 메뉴 '프로젝트 열기' 와 같은 길: 메뉴 → ProjectView.choose_and_open → open_path. 대화상자만 바꿔 끼운다.
        pv = self.win.project_view
        pv.choose_folder = lambda: self.args.project
        acts = [x for m in self.win.menuBar().actions() if m.menu() is not None
                for x in m.menu().actions() if x.shortcut().toString() == "Ctrl+O"]
        if acts:
            acts[0].trigger()
        else:
            pv.choose_and_open()
        ok = self.wb is not None and self.win.stack.currentWidget() is self.wb
        ro = self.wb.project.read_only if ok else None
        asked = len(self.chosen) == 1
        view_only = ok and self.wb.session is None and self.wb.store is None
        return self.check("open_project", ok and ro is False and asked and view_only, t0,
                          f"menu={'Ctrl+O' if acts else 'direct'} read_only={ro} session_asked={asked} "
                          f"view_only={view_only} warnings={len(self.wb.notes) if ok else '-'}")

    def step_chat(self) -> bool:
        t0 = time.perf_counter()
        ok = self.wait(lambda: "chat" not in self.wb._jobs and self.wb.chat_data.count)
        chat = self.wb.chat
        total = chat.total_count() if hasattr(chat, "total_count") else self.wb.chat_data.count
        kept = len(self.wb.chat_data.records)                   # 병렬 배열로 넘겼으면 0 이어야 한다(02 9장 메모리)
        real = hasattr(chat, "total_count")
        return self.check("chat_loaded", ok and total > 0 and (kept == 0 or not real), t0,
                          f"chats={total} viewers={len(self.wb.chat_data.viewer_no)} dict_rows_kept={kept} "
                          f"part={type(chat).__name__}")

    def step_timeline(self) -> bool:
        t0 = time.perf_counter()
        tl = self.wb.timeline
        dens = getattr(tl, "_density", None) or []
        dur = getattr(tl, "_duration", 0)
        self.pump(50)
        return self.check("timeline_density", len(dens) > 0 and sum(dens) == self.wb.chat_data.count
                          and dur > 0, t0, f"buckets={len(dens)} duration_ms={dur} part={type(tl).__name__}")

    def step_video(self) -> bool:
        t0 = time.perf_counter()
        v = self.args.video
        if not v:
            return self.check("video_load", True, t0, "skip (--video 없음)")
        if not os.path.isfile(v):
            return self.check("video_load", False, t0, "영상 파일 없음")
        self.wb.connect_media(v, persist=False)
        pl = self.wb.player
        ok = self.wait(lambda: pl.duration_ms() > 0 and self.wb._file_duration > 0)
        self.file_dur = pl.duration_ms()
        return self.check("video_load", ok, t0, f"duration_ms={self.file_dur} player={type(pl).__name__} "
                                               f"vod_of_file0={self.wb.file_to_vod(0)}")

    def step_waveform(self) -> bool:
        t0 = time.perf_counter()
        if not self.wb.media_path:
            return self.check("waveform", True, t0, "skip (영상 없음)")
        tl = self.wb.timeline
        ok = self.wait(lambda: bool(getattr(tl, "_wave", None)) and "waveform" not in self.wb._jobs)
        wave = getattr(tl, "_wave", []) or []
        return self.check("waveform", ok, t0, f"points={len(wave)} span_ms={getattr(tl, '_wave_span', 0)}")

    def _tl_pos(self):
        """타임라인이 그리는 재생 위치 (Timeline.position 은 속성, 메서드여도 받는다)."""
        p = getattr(self.wb.timeline, "position", None)
        return p() if callable(p) else p

    def _settled(self, file_target: int, tol: int = 100) -> bool:
        return abs(self.wb.player.position_ms() - file_target) <= tol

    def step_seek(self) -> bool:
        t0 = time.perf_counter()
        if not self.wb.media_path:
            target = 60_000
            self.wb.seek_vod(target)
            self.pump(30)
            return self.check("seek", self.wb.vod_position() == target, t0, "영상 없음: 가상 위치만")
        f_target = self.file_dur // 2
        vod_target = self.wb.file_to_vod(f_target)
        self.wb.seek_vod(vod_target)
        ok = self.wait(lambda: self._settled(f_target, 100), 10)
        self.pump(300)                                          # 정착 뒤 신호가 한 번 더 와도 그대로인지
        pos = self.wb.player.position_ms()
        tl_pos = self._tl_pos()
        ok = ok and abs(pos - f_target) <= 100 and tl_pos == self.wb.vod_position()
        return self.check("seek", ok, t0, f"file_err_ms={pos - f_target} timeline_pos_ok={tl_pos == self.wb.vod_position()}")

    def step_chat_activate(self) -> bool:
        t0 = time.perf_counter()
        chat = self.wb.chat
        if not hasattr(chat, "chat_data"):
            return self.check("chat_activate", False, t0, "ChatPanel 아님")
        data = chat.chat_data()
        lo = hi = None
        if self.wb.media_path:
            lo, hi = self.wb.file_to_vod(20_000), self.wb.file_to_vod(self.file_dur - 20_000)
        else:
            ts = [x for x in data.times if x >= 0]
            lo, hi = (ts[0], ts[-1]) if ts else (0, 0)
        idx = -1
        for i in range(data.n):
            t = data.times[i]
            if t < 0:
                continue
            if lo <= t <= hi:
                idx = i
                if t >= (lo + hi) // 2:
                    break
        if idx < 0:
            return self.check("chat_activate", False, t0, "영상 범위 안 채팅 없음")
        cid, t = data.chat_ids[idx], data.times[idx]
        chat.select_chat(cid)
        chat.chatActivated.emit(cid, int(t))                    # 더블클릭·Enter 와 같은 신호
        want_vod = t - 3_000
        if self.wb.media_path:
            want_file = self.wb.vod_to_file(want_vod)
            ok = self.wait(lambda: self._settled(want_file, 100), 10)
            self.pump(200)
        else:
            ok = self.wb.vod_position() == want_vod
        vod = self.wb.vod_position()
        hl = chat.highlighted_chat_id()
        hi_i = data.index_of(hl) if hl else -1
        # 하이라이트 = t_ms <= 지금 VOD 인 마지막 채팅
        hl_ok = hi_i >= 0 and data.times[hi_i] <= vod and (hi_i + 1 >= data.n or data.times[hi_i + 1] > vod
                                                            or data.times[hi_i + 1] < 0)
        tl_ok = self._tl_pos() == vod
        return self.check("chat_activate", ok and hl_ok and tl_ok, t0,
                          f"vod_err_ms={vod - want_vod} highlight_ok={hl_ok} timeline_ok={tl_ok}")

    def step_play(self) -> bool:
        t0 = time.perf_counter()
        if not self.wb.media_path:
            return self.check("play", True, t0, "skip (영상 없음)")
        before_vod = self.wb.vod_position()
        hl_before = self.wb.chat.highlighted_chat_id()
        self.wb.toggle_play()
        ok = self.wait(lambda: self.wb._playing, 5)
        self.pump(1500)
        self.wb.toggle_play()
        self.wait(lambda: not self.wb._playing, 5)
        self.pump(100)
        moved = self.wb.vod_position() - before_vod
        tl_ok = self._tl_pos() == self.wb.vod_position()
        hl_after = self.wb.chat.highlighted_chat_id()
        return self.check("play", ok and 800 <= moved <= 2500 and tl_ok, t0,
                          f"moved_ms={moved} timeline_ok={tl_ok} highlight_changed={hl_after != hl_before}")

    def step_zoom(self) -> bool:
        t0 = time.perf_counter()
        tl = self.wb.timeline
        if not hasattr(tl, "zoom_to_range"):
            return self.check("timeline_zoom", False, t0, "Timeline 아님")
        pos = self.wb.vod_position()
        seen = []
        tl.viewChanged.connect(lambda a, b: seen.append((a, b)))
        tl.zoom_to_range(pos - 30_000, pos + 30_000, 0.0)
        self.pump(50)
        a, b = tl.view_range()
        ok = a <= pos <= b and (b - a) <= 70_000 and bool(seen)
        tl.zoom_reset()
        self.pump(20)
        a2, b2 = tl.view_range()
        return self.check("timeline_zoom", ok and (b2 - a2) > (b - a), t0,
                          f"view_ms={b - a} reset_view_ms={b2 - a2} signals={len(seen)}")

    def step_filter(self) -> bool:
        t0 = time.perf_counter()
        chat = self.wb.chat
        if not hasattr(chat, "row_count"):
            return self.check("chat_filter", False, t0, "ChatPanel 아님")
        total = chat.total_count()
        chat.set_filter("#1")                                    # 시청자 번호 1 의 채팅만
        n1 = chat.row_count()
        chat.set_filter("")
        n2 = chat.row_count()
        return self.check("chat_filter", 0 < n1 < total and n2 == total, t0, f"viewer1_rows={n1} all_rows={n2}")

    def step_store_copy(self) -> bool:
        t0 = time.perf_counter()
        if not self.args.store_copy:
            return self.check("store_copy", True, t0, "skip (--store-copy 없음)")
        import uuid
        from app import session as sess
        from app.ui import project_view as pvm
        from app.ui.workbench import Workbench
        tmp = tempfile.mkdtemp(prefix="chatcoach_smoke_store_")
        dst = os.path.join(tmp, "p")
        leftover: list[str] = []
        try:
            copy_for_store_test(self.args.project, dst)
            mid = str(uuid.uuid4())
            s = sess.new_session("L99", "primary", machine_id_value=mid)
            out = pvm.open_checked(dst)
            wb = Workbench(session=s)
            try:
                wb.open_project(out)
                ok = wb.wait_jobs(30_000) and wb.store is not None and not wb.store.read_only
                n0 = len(wb.store.records()) if wb.store else 0
                seg = {"kind": "segment", "body": {"start_ms": 0, "end_ms": 60_000, "category": "just_chatting",
                                                   "confidence": "certain", "rationale": "smoke"}}
                t1 = time.perf_counter()
                wb.save_label(seg) if ok else None
                save_ms = (time.perf_counter() - t1) * 1000
                segs = len(getattr(wb.timeline, "_segments", []))
                wb.undo_last() if ok else None
                segs2 = len(getattr(wb.timeline, "_segments", []))
                ok = ok and segs == 1 and segs2 == 0
                detail = f"records_before={n0} save_ms={save_ms:.1f} segments={segs}->{segs2}"
            finally:
                wb.close_workbench()
                wb.deleteLater()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}"
        finally:
            leftover = remove_tree_checked(tmp)
        if leftover:
            ok = False
            detail += f" 임시 사본을 다 지우지 못함({len(leftover)}개, {tmp}) — 직접 지우세요"
        return self.check("store_copy", ok, t0, detail)

    # ---------------------------------------------------------------- --labeling-flow
    def _flow_session(self, root: str, data: dict, key: str, labeler: str | None = None):
        """세션 선택 화면을 진짜로 만들어(모달 없이) key('new:primary' · 'resume:<sid>')를 고르고 SessionContext 를 받는다."""
        from app.ui.session_dialog import SessionDialog
        dlg = SessionDialog(self.win, data_root=self.data_root)
        try:
            dlg.load_context(root, data)
            if not dlg.select_labeler(labeler or self.FLOW_LABELER):
                raise RuntimeError("명부에 시험 라벨러가 없습니다")
            self.last_choices = list(dlg.choices)
            if not dlg.choose(key):
                raise RuntimeError(f"세션 화면에서 고를 수 없습니다: {key.split(':')[0]}")
            dlg._choose_open()
            ctx = dlg.context()
        finally:
            dlg.deleteLater()
        if ctx is None:
            raise RuntimeError("세션을 만들지 못했습니다")
        return ctx

    FLOW_LABELER = "L99"

    def _flow_open(self, dst: str, key: str, labeler: str | None = None, on_chosen=None):
        """메인 창의 열기 길(ProjectView.open_path → 세션 선택 → 작업대 → store)로 사본을 연다.
        on_chosen() 을 주면 세션 화면이 끝난 뒤(작업대를 만들기 전) 부른다 — 열기 감시를 세션 화면 뒤부터 걸 때."""
        holder = {}

        def chooser(outcome):
            holder["ctx"] = self._flow_session(outcome.project.root, outcome.project.data, key, labeler)
            if on_chosen is not None:
                on_chosen()
            return holder["ctx"]
        old = self.win.session_chooser
        self.win.session_chooser = chooser
        try:
            out = self.win.project_view.open_path(dst)
        finally:
            self.win.session_chooser = old
        ok = out is not None and out.ok and self.wb is not None and self.wait(
            lambda: self.wb.store is not None and not ({"chat", "store", "media_check"} & set(self.wb._jobs)))
        return ok, holder.get("ctx")

    def _signature(self, st) -> list:
        from app.ui.workbench import HEAD_KINDS
        eff = st.effective()
        return sorted((r["kind"], r["entity_id"], r["id"]) for k in HEAD_KINDS for r in eff.items(k))

    def _flow_chat(self, lo: int, hi: int):
        """[lo, hi] VOD 안의 일반 채팅(kind=text) 하나: 가운데에 가장 가까운 것. (chat_id, t_ms) 또는 None."""
        data = self.wb._editor_chats()
        from app.ui.chat_panel import KIND_CODES
        text_code = KIND_CODES.get("text")
        mid = (lo + hi) // 2
        best = None
        for i in range(data.n):
            t = data.times[i]
            if lo <= t <= hi and data.kinds[i] == text_code:
                if best is None or abs(t - mid) < abs(best[1] - mid):
                    best = (data.chat_ids[i], int(t))
        return best

    def step_labeling_flow(self) -> bool:
        t0 = time.perf_counter()
        if not self.args.labeling_flow:
            return self.check("labeling_flow", True, t0, "skip (--labeling-flow 없음)")
        from app import session as sess
        from app.core import phase as ph
        tmp = tempfile.mkdtemp(prefix="chatcoach_smoke_flow_")
        dst = os.path.join(tmp, "한글 사본", "프로젝트")
        out_zip = os.path.join(tmp, "내보내기", "labels.zip")
        ok_all = True
        try:
            done = copy_for_flow(self.args.project, dst)
            if not sess.load_labelers(self.data_root).get(self.FLOW_LABELER):
                sess.add_labeler(self.data_root, self.FLOW_LABELER)
            # ---- 1. 열기: 세션 화면 → store
            t1 = time.perf_counter()
            ok, ctx = self._flow_open(dst, "new:primary")
            wb = self.wb
            ok = ok and ctx is not None and wb.session is ctx and not wb.store.read_only
            ok_all &= self.check("flow_open", ok, t1,
                                 f"copied={len(done)} session_kind={ctx.session_kind if ctx else '-'} "
                                 f"store_ro={wb.store.read_only if wb and wb.store else '-'} chats={wb.chat_data.count}")
            if not ok:
                return False
            # 상태 줄 하나(11 4-2 의 9): 작업대 칩이 메인 창 상태 줄로 옮겨졌는가
            one_bar = (not wb.status_row.isVisible() and all(c.parent() is self.win.statusBar() for c in wb.status_chips))
            busy_log: list = []
            wb.busyChanged.connect(busy_log.append)
            # ---- 1-2. 영상 연결: 사본의 project.json 에 저장한다(영상이 덮는 범위 D-10 3 은 project.json 으로 정해진다).
            #      원본에는 쓰지 않는다(사본만). 영상이 없으면 영상 밖 규칙 때문에 구간도 저장되지 않는다 → 흐름을 멈춘다.
            t1 = time.perf_counter()
            cov_before = wb.media_coverage()
            if self.args.video and os.path.isfile(self.args.video):
                self._connect_copy_media(wb)
            fdur = wb._file_duration
            cov = wb.media_coverage() or []
            ok = bool(fdur) and len(cov) == 1 and one_bar
            ok_all &= self.check("flow_media", ok, t1,
                                 f"file_ms={fdur} coverage_before={cov_before} coverage_vod_ms={cov} "
                                 f"one_status_bar={one_bar}")
            if not ok:
                return False
            c_lo, c_hi = cov[0]
            if self.args.window_size:
                self._resize(self.args.window_size)
            # ---- 2. ① 구간 2개 (경계는 영상 끝 2분 전: 두 구간 다 영상에 걸쳐야 저장된다, D-10 3)
            t1 = time.perf_counter()
            gate_before = wb.gates()[ph.REACTIONS].allowed
            ok = wb.set_mode(ph.SEGMENTS) and wb.segment_form is not None
            f = wb.segment_form
            vdur = max(1, wb._vod_duration)
            cut = min(vdur - 60_000, c_hi - 120_000)
            wb.seek_vod(cut)
            ok = ok and wb.add_boundary_here() and len(f.model.segments) == 2
            a, b = f.model.segments
            f.select_segment(a.id)
            ok = ok and f.set_category("just_chatting") and f.set_confidence("certain") and \
                f.set_rationale(f"{_hms(c_lo + 5_000)} smoke 저스트채팅")
            f.select_segment(b.id)
            ok = ok and f.set_category("game") and f.set_confidence("probably") and \
                f.set_rationale(f"{_hms(cut)} smoke 게임")
            self._shot("_flow_segments")                     # 구간 폼 (저장 전)
            fit_seg = self._fit(f)
            ts = time.perf_counter()
            n_busy = len(busy_log)
            recs = f.save()
            seg_ms = (time.perf_counter() - ts) * 1000
            busy_seen = len(busy_log) > n_busy and busy_log[n_busy] is True
            self.pump(30)
            busy_off = not wb.is_busy() and busy_log[-1:] == [False]
            tl_segs = len(getattr(wb.timeline, "_segments", []))
            ok = ok and recs is not None and len(recs) == 2 and wb.mark_phase_done() is not None
            ok = ok and not gate_before and wb.gates()[ph.REACTIONS].allowed and tl_segs == 2 and busy_seen and busy_off
            ok_all &= self.check("flow_segments", ok, t1,
                                 f"saved={len(recs or [])} save_ms={seg_ms:.1f} timeline_segments={tl_segs} "
                                 f"reactions_locked_before={not gate_before} busy_shown={busy_seen} "
                                 f"busy_cleared={busy_off} fit={fit_seg}")
            if not ok:
                return False
            # ---- 3. ② 반응 1건 (강한 증거 · 7-2 재확인)
            t1 = time.perf_counter()
            ok = wb.set_mode(ph.REACTIONS)
            lo = wb.file_to_vod(60_000) if fdur else 60_000
            hi = min(cut - 60_000, wb.file_to_vod(fdur - 60_000) if fdur else cut - 60_000)
            pick = self._flow_chat(max(0, lo), hi)
            if not ok or pick is None:
                return self.check("flow_reaction", False, t1, "영상 범위 안 저스트채팅 채팅 없음")
            chat_id, t_chat = pick
            wb.chat.select_chat(chat_id)
            wb.seek_vod(t_chat + 1_000)
            if fdur:
                self.wait(lambda: abs(wb.vod_position() - (t_chat + 1_000)) <= 200, 10)
            wb.mark_reaction()
            ed = wb.editor
            ok = wb.is_editing() and ed.target_chat_id() == chat_id and ed.grade() is None
            self.pump(30)
            self.fit_edit = self._fit(ed)
            ed.utt_start.setText(f"{(t_chat + 1_000) / 1000:.3f}")
            ed.utt_end.setText(f"{(t_chat + 3_000) / 1000:.3f}")
            ed._on_utt_times()
            ed.heard.setText("smoke")
            ed._on_heard_edited("smoke")
            ed.evidence.boxes["nickname_called"].setChecked(True)
            ed.set_grade("direct")
            ed.set_confidence("certain")
            ed.rationale.setText(f"{_hms(t_chat + 1_000)} smoke 호명")
            passes, covs = 0, []
            if fdur:
                rv = wb.review
                rv.set_speed(2.0)
                for k in range(2):                       # 7-2: 처음 확인 + 다시 듣기
                    ed.listen_again()
                    if k == 0 and self.args.screenshot and _inside(self.args.screenshot, SCREENS_DIR):
                        self.pump(1500)                  # 범위 반복 재생 중인 편집 상태 화면
                        self._shot("_flow_edit")
                    got = self.wait(lambda: rv.current_coverage() >= 0.97, 90)
                    covs.append(rv.current_coverage())
                    passes += 1 if got else 0
                rv.stop_loop()
                wb.player.pause()
                rv.set_speed(1.0)
            ts = time.perf_counter()
            res = ed.save()
            r_ms = (time.perf_counter() - ts) * 1000
            marks = list(getattr(wb.timeline, "markers", lambda n: [])("labels"))
            complete = bool(res and res.get("complete"))
            want_kind = "reaction" if complete else "recheck"
            ok = ok and res is not None and not wb.is_editing() and marks and marks[0][1] == want_kind
            ok = ok and (complete if fdur else True)
            ok = ok and wb.mark_phase_done() is not None
            ok_all &= self.check("flow_reaction", ok, t1,
                                 f"lines={len((res or {}).get('utterances') or []) + 1 + len((res or {}).get('video_checks') or [])} "
                                 f"passes={passes} coverage={'/'.join(f'{c:.2f}' for c in covs) or '-'} "
                                 f"complete={complete} save_ms={r_ms:.1f} marker={marks[0][1] if marks else '-'} "
                                 f"fit={getattr(self, 'fit_edit', '-')}")
            if not ok:
                return False
            # ---- 4. ③ 표본 뽑기 · 재현 검증 · 1건 판정
            t1 = time.perf_counter()
            ok = wb.set_mode(ph.SAMPLES)
            q = wb.queue
            wb.draw_sample()
            ok = ok and self.wait(lambda: "sample_draw" not in wb._jobs and q.manifest is not None, 60)
            man = q.manifest or {}
            tv = time.perf_counter()
            ver = q.run_verify()
            v_ms = (time.perf_counter() - tv) * 1000
            from app.core import sampling as sp
            ex = man.get("excluded") or {}
            pre, post = (man.get("media") or {}).get("pre_roll_ms", 0), (man.get("media") or {}).get("post_roll_ms", 0)
            # 영상 안 범위에서만 뽑혔는가: 모든 표본의 7-2 확인 범위가 영상 범위 안 (D-10 3)
            inside = all(sp.window_in_coverage(it["t_ms"], sp.normalize_coverage(cov), pre, post)
                         for it in man.get("items") or [])
            had2 = sum(1 for it in man.get("items") or [] if q.has_stage2_label(it["chat_id"]))
            ok = ok and ver is not None and ver.ok and len(os.listdir(os.path.join(dst, "samples"))) == 1
            ok = ok and man.get("algorithm") == sp.ALGORITHM_V2 and inside and bool(man.get("items"))
            ok_all &= self.check("flow_sample_draw", ok, t1,
                                 f"alg={man.get('algorithm')} eligible={(man.get('eligible') or {}).get('size')} "
                                 f"items={len(man.get('items') or [])} census={man.get('census')} "
                                 f"out_of_media={ex.get('out_of_media')} buffer_excluded={ex.get('buffer')} "
                                 f"all_inside_media={inside} items_with_stage2={had2} "
                                 f"verify={'ok' if ver and ver.ok else 'fail'} verify_ms={v_ms:.0f}")
            if not ok:
                return False
            t1 = time.perf_counter()
            q.next_pending() if q.current_item() and q.has_stage2_label(q.current_item()["chat_id"]) else None
            q.set_confidence("certain")
            it = q.current_item()
            q.rationale.setText(f"{_hms(it['t_ms'] if it else 0)} smoke 답 없음")
            rec = q.judge_unconfirmed()
            pr = q.progress()
            done1 = pr.done if pr else -1
            ok = rec is not None and pr is not None and done1 == 1 + had2
            ok_all &= self.check("flow_sample_judge", ok, t1,
                                 f"progress={pr.done if pr else '-'}/{pr.denominator_text() if pr else '-'}")
            # ---- 4-2. ③ 에서 ② 라벨 다시 열기 (D-10 4): 대기 표본 하나에 ② 반응을 만든 뒤 ③ 으로 돌아와 고친다
            t1 = time.perf_counter()
            ok = q.next_pending()
            it2 = q.current_item()
            x_chat, x_t = (it2["chat_id"], int(it2["t_ms"])) if it2 else (None, 0)
            ok = ok and x_chat is not None and wb.set_mode(ph.REACTIONS)
            wb.chat.select_chat(x_chat)
            wb.seek_vod(x_t + 1_000)
            wb.mark_reaction()
            ed = wb.editor
            ok = ok and wb.is_editing() and ed.target_chat_id() == x_chat
            ed.utt_start.setText(f"{(x_t + 1_000) / 1000:.3f}")
            ed.utt_end.setText(f"{(x_t + 3_000) / 1000:.3f}")
            ed._on_utt_times()
            ed.heard.setText("smoke")
            ed._on_heard_edited("smoke")
            ed.evidence.boxes["nickname_called"].setChecked(True)
            ed.set_grade("direct")
            ed.set_confidence("certain")
            ed.rationale.setText(f"{_hms(x_t + 1_000)} smoke 호명 2")
            res2 = ed.save()
            x_eid = (res2 or {}).get("reaction", {}).get("entity_id") if res2 else None
            ok = ok and res2 is not None and wb.set_mode(ph.SAMPLES)
            q = wb.queue
            judged2 = q.state_of(x_chat) == sp.JUDGED and q.has_stage2_label(x_chat)
            pr2 = q.progress()
            q.go(next((i for i, x in enumerate(q._items) if x["chat_id"] == x_chat), 0))
            n_rec = len(wb.store.records())
            blocked = q.judge_undeterminable() is None and len(wb.store.records()) == n_rec
            wb.mark_reaction()                                # M → 그 ② 라벨을 고치는 편집기
            reopened = wb.is_editing() and (ed._existing or {}).get("entity_id") == x_eid and \
                ed._entry == "from_utterance"
            ed.set_confidence("probably")
            res3 = ed.save()
            same_entity = bool(res3) and res3["reaction"]["entity_id"] == x_eid and \
                res3["reaction"]["body"]["entry"] == "from_utterance"
            pr3 = q.progress()
            ok = ok and judged2 and blocked and reopened and same_entity and pr2 is not None and \
                pr2.done == done1 + 1 and pr3.done == pr2.done
            ok_all &= self.check("flow_sample_stage2", ok, t1,
                                 f"judged_by_stage2={judged2} quick_save_blocked={blocked} reopened_existing={reopened} "
                                 f"same_entity={same_entity} progress={pr2.done if pr2 else '-'}->{pr3.done if pr3 else '-'}")
            self.pump(50)
            self._shot("_flow")                                # 큐 화면
            fit_q = self._fit(wb.page_samples)
            self.check("flow_layout", True, t1, f"queue_fit={fit_q} window={self.win.width()}x{self.win.height()}")
            # ---- 5. 내보내기
            t1 = time.perf_counter()
            os.makedirs(os.path.dirname(out_zip), exist_ok=True)
            er = wb.export_labels(out_zip)
            import json as _json
            import zipfile
            with zipfile.ZipFile(out_zip) as z:
                names = set(z.namelist())
                rows = [_json.loads(x) for x in z.read("labels.jsonl").decode("utf-8").splitlines() if x.strip()]
            eff = wb.store.effective()
            want = sorted((r["kind"], r["entity_id"], r["id"]) for k in ("segment", "reaction") for r in eff.items(k))
            got = sorted((r["label_kind"], r["entity_id"], r["label_id"]) for r in rows
                         if r["label_kind"] in ("segment", "reaction"))
            bad_rows = [w for w in er.warnings if "export-row" in w]
            # 반응 3 = ② 1건 + ③ 판정 1건 + ③ 에서 고친 ② 1건 (같은 채팅에 라벨 둘이 생기지 않았다)
            ok = {"labels.jsonl", "segments.csv", "reactions.csv", "manifest.json"} <= names and got == want and \
                not bad_rows and er.by_kind.get("segment") == 2 and er.by_kind.get("reaction") == 3
            chats = [r.get("chat_id") for r in rows if r.get("label_kind") == "reaction"]
            ok = ok and len(chats) == len(set(chats))
            ok_all &= self.check("flow_export", ok, t1,
                                 f"rows={len(rows)} by_kind={dict((k, v) for k, v in er.by_kind.items() if v)} "
                                 f"zip_bytes={os.path.getsize(out_zip)} rows_match_effective={got == want}")
            # ---- 6. 다시 열기 (이어 하기 세션) → 같은 현재 유효 라벨
            t1 = time.perf_counter()
            before = self._signature(wb.store)
            sid = ctx.session_id
            ok2, ctx2 = self._flow_open(dst, "resume:" + sid)
            wb2 = self.wb
            after = self._signature(wb2.store) if ok2 else None
            ok = ok2 and ctx2 is not None and ctx2.session_id == sid and after == before and \
                wb2.gates()[ph.SAMPLES].allowed
            ok_all &= self.check("flow_reopen", ok, t1, f"labels={len(before)} same={after == before} "
                                                        f"samples_gate={wb2.gates()[ph.SAMPLES].allowed if ok2 else '-'}")
            # 사본 닫기: session.close 줄
            self.win.show_home()
            self.pump(50)
            import glob as _glob
            files = _glob.glob(os.path.join(dst, "labels", f"{sid}.*.jsonl"))
            last = ""
            if files:
                with open(files[0], encoding="utf-8") as fh:
                    last = _json.loads(fh.read().splitlines()[-1]).get("kind", "")
            ok_all &= self.check("flow_close", last == "session.close", time.perf_counter(), f"last_line={last}")
            return ok_all
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(limit=4)
            return self.check("labeling_flow", False, t0, type(e).__name__)
        finally:
            if self.wb is not None and getattr(self.wb, "project", None) is not None and \
                    _inside(self.wb.project.root, tmp):
                self.win.show_home()
                self.pump(50)
            leftover = remove_tree_checked(tmp)
            if leftover:
                self.check("flow_cleanup", False, t0, f"임시 사본을 다 지우지 못함({len(leftover)}개, {tmp}) — 직접 지우세요")
            else:
                self.check("flow_cleanup", True, time.perf_counter(),
                           f"사본 지움 (흐름 전체 {time.perf_counter() - t0:.1f}초)")

    # ---------------------------------------------------------------- --agreement-flow
    AGREE_A, AGREE_B = "L01", "L02"

    def _editor_direct(self, ed, t_chat: int, note: str) -> dict | None:
        """열린 반응 편집기에 강한 증거(닉네임 호명)·직접 반응을 채워 저장한다(재확인 듣기는 하지 않음 → 속이 빈 표시)."""
        ed.utt_start.setText(f"{(t_chat + 1_000) / 1000:.3f}")
        ed.utt_end.setText(f"{(t_chat + 3_000) / 1000:.3f}")
        ed._on_utt_times()
        ed.heard.setText("smoke")
        ed._on_heard_edited("smoke")
        ed.evidence.boxes["nickname_called"].setChecked(True)
        ed.set_grade("direct")
        ed.set_confidence("certain")
        ed.rationale.setText(f"{_hms(t_chat + 1_000)} smoke {note}")
        return ed.save()

    def _judge_three(self, wb, plan: list[str], tag: str) -> list[str]:
        """큐의 앞 대기 표본 3건을 plan 대로 판정: 'direct'(편집기 M) · 'certain'·'probably'·'undeterminable'(반응 미확인).
        판정한 chat_id 목록(순서대로)."""
        q = wb.queue
        done: list[str] = []
        for how in plan:
            it = q.current_item()
            if it is None:
                break
            cid = it["chat_id"]
            if how == "direct":
                wb.mark_reaction()
                if not wb.is_editing():
                    break
                res = self._editor_direct(wb.editor, int(it["t_ms"]), f"{tag} 호명")
                if not res:
                    break
            elif how == "undeterminable":
                q.rationale.setText(f"{_hms(it['t_ms'])} smoke {tag} 소리 불명")
                if q.judge_undeterminable() is None:
                    break
            else:
                q.set_confidence(how)
                q.rationale.setText(f"{_hms(it['t_ms'])} smoke {tag} 답 없음")
                if q.judge_unconfirmed() is None:
                    break
            done.append(cid)
            self.pump(20)
        return done

    def _lock_via_menu(self) -> dict | None:
        """메뉴 '세션 → 이 세션 잠그기…' 와 같은 길(MainWindow.lock_session_dialog): 요약 → 다음 → 확인 칸 → 잠그기."""
        def run(d):
            d.go_confirm()
            d.confirm_box.setChecked(True)
            d.do_lock()
        self.win.update_session_actions()
        if not self.win.a_lock.isEnabled():
            wb = self.win.workbench
            print(f"  잠그기 메뉴 꺼짐: {self.win.a_lock.toolTip()} "
                  f"(작업대 일 {wb.busy_job_keys() if wb is not None else []})", flush=True)
            return None
        return self.win.lock_session_dialog(run=run)

    # ---------------------------------------------------------------- 열기 감시 (두 흐름 공통)
    def _watch_opens(self, forbidden: tuple[str, ...]):
        """builtins.open·io.open·os.open 을 감시한다. (멈추는 함수, 상태 dict{seen, bad}) 를 돌려준다."""
        import builtins
        import io
        real_open, real_io_open, real_os_open = builtins.open, io.open, os.open
        state = {"seen": 0, "bad": []}

        def note(p):
            try:
                s = os.path.normcase(os.path.abspath(os.fspath(p)))
            except TypeError:
                return
            state["seen"] += 1
            if any(s.startswith(x) for x in forbidden):
                state["bad"].append(s)

        def w_open(file, *a, **k):
            note(file)
            return real_open(file, *a, **k)

        def w_os_open(path, *a, **k):
            note(path)
            return real_os_open(path, *a, **k)
        builtins.open, io.open, os.open = w_open, w_open, w_os_open

        def stop():
            builtins.open, io.open, os.open = real_open, real_io_open, real_os_open
        return stop, state

    # ---------------------------------------------------------------- 두 흐름의 공통 도우미 (14 4-2 의 15)
    # --agreement-flow 와 --adjudication-flow 는 같은 앞부분(L01 1차 → 잠그기 → 계획 → L02 두 번째 사람 → 잠그기 → 보고서)을
    # 이 도우미로 돈다. 단계 이름은 흐름 머리글자(p = agree · adj)를 앞에 붙인다.
    def _flow_copy(self, prefix: str) -> tuple[str, str, str, list[str]]:
        """한글 임시 사본(…\\projects\\프로젝트)을 만들고 시험 라벨러 둘을 명부에 넣는다. (tmp, dst, out_root, 복사한 파일)."""
        from app import session as sess
        tmp = tempfile.mkdtemp(prefix=f"chatcoach_smoke_{prefix}_")
        dst = os.path.join(tmp, "한글 사본", "projects", "프로젝트")
        out_root = os.path.join(tmp, "보고서")             # 사본 옆 임시 폴더 (프로젝트 밖)
        done = copy_for_flow(self.args.project, dst)
        for lid in (self.AGREE_A, self.AGREE_B):
            if not sess.load_labelers(self.data_root).get(lid):
                sess.add_labeler(self.data_root, lid)
        return tmp, dst, out_root, done

    def _connect_copy_media(self, wb) -> None:
        """사본에 영상을 연결한다(사본의 project.json 에만 저장). 사본의 영상 지문이 --video 와 다르면(합성 프로젝트 등)
        작업대가 교체 사유를 묻는 입력 상자를 띄워 화면 없이 멈추므로, 사유를 자리표시 문장으로 대신 넣는다."""
        wb.ask_replace_reason = lambda: "smoke: 시험 영상으로 교체"
        wb.connect_media(self.args.video, persist=True)
        self.wait(lambda: wb._file_duration > 0 and "media_ref" not in wb._jobs, 60)

    def _flow_cleanup(self, tmp: str, p: str, t0: float) -> None:
        if self.wb is not None and getattr(self.wb, "project", None) is not None and _inside(self.wb.project.root, tmp):
            self.win.show_home()
            self.pump(50)
        leftover = remove_tree_checked(tmp)
        if leftover:
            self.check(f"{p}_cleanup", False, t0, f"임시 사본을 다 지우지 못함({len(leftover)}개, {tmp}) — 직접 지우세요")
        else:
            self.check(f"{p}_cleanup", True, time.perf_counter(),
                       f"사본·보고서 지움 (흐름 전체 {time.perf_counter() - t0:.1f}초)")

    def _flow_primary(self, dst: str, p: str) -> dict | None:
        """L01 1차: 영상 연결 → 구간 2개(둘 다 저스트채팅, 경계는 10분 블록 경계 — 어느 블록이 뽑혀도 1차 표본이 겹친다) →
        구간마다 표본 뽑기·앞 3건 판정(직접·미확인 확실함·미확인 아마도) → ③ 완료(1차에는 ④ 가 없다, D-12 5) →
        메뉴 길로 잠그기(재현 검증 워커, 결과가 잠금에 남음) → 재라벨 계획(두 번째 사람 L02, relabel/index.json 1줄)."""
        from app import locking
        from app import relabel as relabel_io
        from app.core import phase as ph
        from app.core import sampling as sp
        t1 = time.perf_counter()
        ok, ctx_a = self._flow_open(dst, "new:primary", self.AGREE_A)
        wb = self.wb
        ok = ok and ctx_a is not None and not wb.store.read_only
        if ok and self.args.video and os.path.isfile(self.args.video):
            self._connect_copy_media(wb)
        cov = wb.media_coverage() or [] if ok else []
        ok = ok and len(cov) == 1
        self.wait(lambda: "chat_index" not in wb._jobs, 60)
        self.check(f"{p}_open_a", ok, t1, f"coverage_vod_ms={cov} chat_index={wb.chat_index_state} "
                                          f"indexed={len(wb.chat_index) if wb.chat_index is not None else '-'}")
        if not ok:
            return None
        c_lo, c_hi = cov[0]
        if self.args.window_size:
            self._resize(self.args.window_size)
        t1 = time.perf_counter()
        cut = (c_lo // 600_000 + 1) * 600_000
        if not (c_lo + 120_000 < cut < c_hi - 120_000):
            cut = (c_lo + c_hi) // 2
        ok = wb.set_mode(ph.SEGMENTS)
        f = wb.segment_form
        ok = ok and f.add_boundary(cut) and len(f.model.segments) == 2
        for i, s in enumerate(f.model.segments):
            f.select_segment(s.id)
            ok = ok and f.set_category("just_chatting") and f.set_confidence("certain") and \
                f.set_rationale(f"{_hms(max(c_lo, s.start_ms) + 5_000)} smoke 저스트채팅 {i + 1}")
        recs = f.save()
        ok = ok and recs is not None and len(recs) == 2 and wb.mark_phase_done() is not None
        ok = ok and wb.set_mode(ph.REACTIONS) and wb.mark_phase_done() is not None
        ok = ok and wb.set_mode(ph.SAMPLES)
        box = wb.sample_seg_box
        judged_a: dict[str, list[str]] = {}
        a_items: dict[str, list[str]] = {}
        patches0 = wb.timeline_patches
        for i in range(box.count() if ok else 0):
            box.setCurrentIndex(i)
            self.pump(10)
            wb._on_sample_target(sync=True)
            wb.draw_sample(sync=True)
            q = wb.queue
            good = self.wait(lambda: "sample_draw" not in wb._jobs and q.manifest is not None, 60)
            man = q.manifest or {}
            ok = ok and good and man.get("algorithm") == sp.ALGORITHM_V2
            eid = box.itemData(i)
            a_items[eid] = [it["chat_id"] for it in man.get("items") or []]
            judged_a[eid] = self._judge_three(wb, ["direct", "certain", "probably"], "A")
            ok = ok and len(judged_a[eid]) == 3
        n_samples = len(os.listdir(os.path.join(dst, "samples"))) if os.path.isdir(os.path.join(dst, "samples")) else 0
        ok = ok and n_samples == box.count() == 2
        ok = ok and wb.mark_phase_done() is not None                 # ③ 완료 표시 (1차에는 ④ 가 없다, D-12 5)
        no_missed = ph.MISSED not in wb.mode_buttons and not wb.gates()[ph.MISSED].allowed
        patched = wb.timeline_patches - patches0
        self.check(f"{p}_a_label", ok and no_missed and patched > 0, t1,
                   f"segments={len(recs or [])} manifests={n_samples} items={[len(v) for v in a_items.values()]} "
                   f"judged={[len(v) for v in judged_a.values()]} missed_mode_in_primary={not no_missed} "
                   f"timeline_partial_updates={patched}")
        if not (ok and no_missed):
            return None
        t1 = time.perf_counter()
        doc_a = self._lock_via_menu()
        sid_a = ctx_a.session_id
        locked_a = doc_a is not None and locking.is_locked(dst, sid_a) and wb.store.locked and wb.store.read_only
        gates_off = not wb.gates()[ph.SEGMENTS].allowed
        verify = (doc_a or {}).get("sample_verify") or []
        self.win.update_session_actions()
        plan = self.win.relabel_plan_dialog(run=lambda d: d.make("relabel_second", self.AGREE_B)) \
            if self.win.a_plan.isEnabled() else None
        sel = (plan or {}).get("selected") or []
        has_tau = bool(sel) and all(s.get("tau_block_key") for s in sel)
        try:
            idx = relabel_io.read_index(dst)
        except (OSError, ValueError):
            idx = None
        ok = locked_a and gates_off and plan is not None and has_tau and bool(verify) and \
            all(v["ok"] for v in verify) and idx is not None and len(idx["plans"]) == 1
        self.check(f"{p}_a_lock_plan", ok, t1,
                   f"locked={locked_a} modes_off={gates_off} sample_verify={len(verify)} "
                   f"verify_ok={all(v['ok'] for v in verify)} plan_blocks={len(sel)} tau_block_set={has_tau} "
                   f"index_entries={len(idx['plans']) if idx else '-'}")
        # 비교용 1차 표본 목록(a_items)은 감시 전에 기억해 둔다 — L02 세션은 samples/ 를 열지 않는다
        self.win.show_home()
        self.pump(50)
        if not ok:
            return None
        return {"sid_a": sid_a, "plan": plan, "a_items": a_items, "judged_a": judged_a, "doc_a": doc_a}

    def _flow_second(self, dst: str, plan: dict, sid_a: str, p: str, *, n_missed: int = 0,
                     a_items: dict | None = None) -> dict | None:
        """L02 두 번째 사람(세션 화면에서 relabel_second, 같은 PC 라 '같은 machine_id'): 블록 안 구간(끝 2분은 게임) →
        계획의 블록 표본(1차 매니페스트의 순서 키·τ_block) 3건 판정(직접·확실함·판단 불가), 잠긴 1차 라벨이 화면 어디에도
        없는지 → ③ 완료 → (n_missed > 0: ④ 놓친 후보 n 건 + 채팅 표 오른쪽 메뉴로 표본 밖 후보 1건) → 잠그기(current_sample).
        처음부터 끝까지 L01 기록·L01 잠금·samples/ 를 한 번도 열지 않는지 감시한다."""
        from app import locking
        from app.core import missed as mc
        from app.core import phase as ph
        forbidden = (os.path.normcase(os.path.join(dst, "labels", sid_a)),
                     os.path.normcase(os.path.join(dst, "locks", sid_a)),
                     os.path.normcase(os.path.join(dst, "samples")))
        stop, st = self._watch_opens(forbidden)
        locked_b, saved, n_extra, sid_b, doc_b = False, [], 0, None, None
        judged_b: list[str] = []
        b_items: list[str] = []
        t1 = time.perf_counter()
        try:
            ok, ctx_b = self._flow_open(dst, "new:relabel_second", self.AGREE_B)
            wb = self.wb
            choice = next((c for c in getattr(self, "last_choices", []) if c.kind == "relabel_second"), None)
            ok = ok and ctx_b is not None and ctx_b.session_kind == "relabel_second" and wb.relabel_plan is not None
            ok = ok and (ph.MISSED in wb.mode_buttons)
            scope = wb.relabel_scope() if ok else []
            ok = ok and scope == [tuple(r) for r in plan["scope"]["ranges"]]
            lo, hi = scope[0] if scope else (0, 0)
            ok = ok and wb.set_mode(ph.SEGMENTS)
            f = wb.segment_form
            if ok and lo > 0:
                ok = f.add_boundary(lo)
            if ok and hi < f.model.duration_ms:
                ok = f.add_boundary(hi)
            g_cut = hi - 120_000                       # 블록 끝 2분은 '게임' (1차와 다른 분류 → 구간 κ·경계 |Δt|)
            ok = ok and f.add_boundary(g_cut) and f.select_at((lo + g_cut) // 2)
            ok = ok and f.set_category("just_chatting") and f.set_confidence("certain") and \
                f.set_rationale(f"{_hms(lo + 5_000)} smoke B 저스트채팅")
            ok = ok and f.select_at((g_cut + hi) // 2) and f.set_category("game") and f.set_confidence("probably") \
                and f.set_rationale(f"{_hms(g_cut + 5_000)} smoke B 게임")
            recs = f.save() if ok else None
            ok = ok and recs is not None and len(recs) == 2 and wb.mark_phase_done() is not None
            ok = ok and wb.set_mode(ph.REACTIONS) and wb.mark_phase_done() is not None and wb.set_mode(ph.SAMPLES)
            q = wb.queue
            good = ok and self.wait(lambda: "sample_msgs" not in wb._jobs and q.manifest is not None, 60)
            man_b = q.manifest or {}
            b_items = [it["chat_id"] for it in man_b.get("items") or []]
            judged_b = self._judge_three(wb, ["direct", "certain", "undeterminable"], "B") if good else []
            # 채팅 표 표시: 자기 세션 라벨(초록, D-16)은 괜찮고 1차 라벨(보라)은 안 된다.
            # 초록이면 표시된 채팅이 모두 B 자신의 라벨이어야 한다(own_chat_marks 와 같음)
            b_marks = wb.chat.label_marks() if hasattr(wb.chat, "label_marks") else {}
            own_ok = (not b_marks) or (bool(getattr(wb.chat.model, "marks_own", False))
                                       and b_marks == wb.own_chat_marks())
            no_primary = wb.primary_labels()["chats"] == 0 and not wb.timeline.markers("primary") and own_ok
            gate_before = wb.gates()[ph.MISSED].allowed
            self._shot(f"_{p}_b")
            ok = good and bool(man_b.get("from_plan")) and bool(b_items) and len(judged_b) == 3 and no_primary \
                and not gate_before and wb.mark_phase_done() is not None
            # 14(c5) 의 agree_b_samples 가 찍던 숫자(같은 블록 1차 표본 수·겹친 수)를 되살린다(15 5-2 의 16번 대조)
            a_same = next((v for v in (a_items or {}).values() if set(v) & set(b_items)), [])
            overlap = len(set(a_same) & set(b_items))
            self.check(f"{p}_b_label", ok, t1,
                       f"kind={ctx_b.session_kind if ctx_b else '-'} scope_blocks={len(scope)} block_ms={lo}~{hi} "
                       f"items={len(b_items)} a_items_same_block={len(a_same)} overlap={overlap} "
                       f"judged={len(judged_b)} primary_hidden={no_primary} missed_gate_before_samples_mark={gate_before} "
                       f"same_machine_note={bool(choice and choice.same_machine)}")
            if not ok:
                return None
            if n_missed:
                # ---- ④ 놓친 후보 (잠그기 전, 블록 안만 — D-12 5). 대상은 채팅 색인으로 계산한다(14 6-1 의 2)
                t1 = time.perf_counter()
                ok = wb.gates()[ph.MISSED].allowed and wb.set_mode(ph.MISSED)
                ok = ok and self.wait(lambda: "missed" not in wb._jobs and wb.missed_selection() is not None, 60)
                mf = wb.missed_form
                sel = wb.missed_selection()
                targets = list(sel.targets) if sel is not None else []
                inside = all(lo <= t.t_ms < hi for t in targets)
                self.pump(30)
                self._shot(f"_{p}_missed")
                fit_m = self._fit(mf) if mf is not None else "-"
                answers = [mc.Answers(True, True, True, None, True, "certain", "{t} smoke 질문 후보"),
                           mc.Answers(True, False, True, "C", False, "probably", "{t} smoke 인사")]
                for t, a in zip(targets, answers[:n_missed]):
                    if not mf.select_chat(t.chat_id):
                        break
                    mf.set_answers(mc.Answers(a.cond2, a.cond3, a.cond4, a.reason_code, a.check_question, a.confidence,
                                              a.rationale.format(t=_hms(t.t_ms))))
                    rec = mf.save()
                    if rec is None:
                        break
                    saved.append(rec)
                # 채팅 표 오른쪽 메뉴 '표본 밖 후보로 적기' (scope = extra, 14 4-2 의 11): 블록 안, 대상이 아닌 채팅 하나
                tid = {t.chat_id for t in targets}
                idx = wb.chat_index
                extra_id = next((c for c in (idx.range_ids(lo + 60_000, hi - 60_000) if idx is not None else [])
                                 if c not in tid and wb.extra_block_reason(c) is None), None)
                acts = wb.chat_context_actions(extra_id) if extra_id else []
                if acts and acts[0][2]:
                    acts[0][1](extra_id)
                    t = mf.current_target()
                    if t is not None and t.chat_id == extra_id and t.sample_ref is None:
                        mf.set_answers(mc.Answers(True, False, True, "C", False, "probably",
                                                  f"{_hms(t.t_ms)} smoke 표본 밖 인사"))
                        rec = mf.save()
                        n_extra = int(rec is not None and rec["body"]["scope"] == "extra")
                ok = ok and inside and len(saved) == min(n_missed, len(targets)) and len(saved) >= 1 and n_extra == 1
                ok = ok and wb.mark_phase_done() is not None
                self.check(f"{p}_b_missed", ok, t1,
                           f"targets={len(targets)} skipped={dict((k, v) for k, v in (sel.counts if sel else {}).items() if v)} "
                           f"saved={len(saved)} candidates={sum(1 for r in saved if r['body']['is_candidate'])} "
                           f"extra_saved={n_extra} all_in_block={inside} fit={fit_m}")
                if not ok:
                    return None
            t1 = time.perf_counter()
            doc_b = self._lock_via_menu()
            sid_b = ctx_b.session_id
            locked_b = doc_b is not None and locking.is_locked(dst, sid_b)
            self.win.show_home()
            self.pump(50)
        finally:
            stop()
        cur = (doc_b or {}).get("current_sample") or {}
        ok = locked_b and not st["bad"] and st["seen"] > 0 and cur.get("count", 0) >= len(judged_b) > 0
        self.check(f"{p}_b_lock_blind", ok, t1, f"locked={locked_b} opens_watched={st['seen']} "
                                                f"forbidden_opens={len(st['bad'])} current_sample={cur.get('count')}")
        return {"sid_b": sid_b, "n_missed": len(saved), "n_extra": n_extra, "judged_b": judged_b,
                "b_items": b_items} if ok else None

    def _flow_report(self, dst: str, sid_a: str, sid_b: str, out_root: str):
        """보기 전용으로 연 뒤 메뉴 '보고 → 일치도 보고서…' 와 같은 길(대화상자가 워커로 만든다). (res, report, md).
        채팅 시각은 작업대의 채팅 색인으로 넘긴다(header.chat_times.source = lookup)."""
        old = self.win.session_chooser
        self.win.session_chooser = lambda outcome: None
        try:
            self.win.project_view.open_path(dst)
        finally:
            self.win.session_chooser = old
        self.wait(lambda: self.wb is not None and not ({"chat", "media_check", "chat_index"} & set(self.wb._jobs)))
        self.win.update_session_actions()
        res = self.win.agreement_dialog(run=lambda d: d.run(sid_a, sid_b, out_root)) \
            if self.win.a_agreement.isEnabled() else None
        rep = res.report if res else {}
        md = ""
        if res and res.out_dir:
            with open(os.path.join(res.out_dir, "report.md"), encoding="utf-8") as fh:
                md = fh.read()
        return res, rep, md

    # ---------------------------------------------------------------- --agreement-flow
    def step_agreement_flow(self) -> bool:
        t0 = time.perf_counter()
        if not self.args.agreement_flow:
            return self.check("agreement_flow", True, t0, "skip (--agreement-flow 없음)")
        p = "agree"
        tmp = None
        try:
            tmp, dst, out_root, done = self._flow_copy(p)
            self.check(f"{p}_copy", bool(done), time.perf_counter(), f"copied={len(done)}")
            a = self._flow_primary(dst, p)
            if a is None:
                return False
            b = self._flow_second(dst, a["plan"], a["sid_a"], p, a_items=a["a_items"])
            if b is None:
                return False
            sid_a, sid_b = a["sid_a"], b["sid_b"]
            # ---- C. 일치도 보고서
            t1 = time.perf_counter()
            res, rep, _md = self._flow_report(dst, sid_a, sid_b, out_root)
            files = sorted(os.listdir(res.out_dir)) if res and res.out_dir else []
            h = rep.get("header") or {}
            sm = (rep.get("samples") or {}).get("judged") or {}
            seg = rep.get("segments") or {}
            outside = res is not None and not _inside(res.out_dir, dst)
            both = sum(1 for v in a["judged_a"].values() for c in v if c in b["judged_b"])
            ok = res is not None and files == ["report.json", "report.md"] and outside and \
                h.get("comparison") == "inter" and h.get("same_machine") is True and sm.get("both", 0) >= 1 and \
                (h.get("chat_times") or {}).get("source") == "lookup" and \
                (h.get("current_sample_method") or {}).get("b") == "given"
            gates = ", ".join(f"{g.get('label_kind')}={g.get('verdict')}" for g in rep.get("gates") or [])
            self.check(f"{p}_report", ok, t1,
                       f"files={len(files)} outside_project={outside} comparison={h.get('comparison')} "
                       f"same_machine={h.get('same_machine')} judged_both={sm.get('both')} judged_by_both_flow={both} "
                       f"chat_times={(h.get('chat_times') or {}).get('source')} "
                       f"current_sample_b={(h.get('current_sample_method') or {}).get('b')} "
                       f"segment_kappa={seg.get('kappa') if seg.get('kappa') is None else round(seg['kappa'], 3)} "
                       f"gates=[{gates}]")
            self.win.show_home()
            self.pump(50)
            if not ok:
                return False
            # ---- D. 분석 세션: 잠긴 1차 라벨이 보이는가 (보라 트랙·구간·채팅 표)
            t1 = time.perf_counter()
            ok, _ctx_c = self._flow_open(dst, "new:analysis", self.AGREE_A)
            wb = self.wb
            pl = wb.primary_labels() if ok else {}
            marks = wb.timeline.markers("primary") if ok else []
            chat_marks = wb.chat.label_marks() if ok and hasattr(wb.chat, "label_marks") else {}
            tl_segs = len(getattr(wb.timeline, "_segments", [])) if ok else 0
            engine = wb.timeline.markers("engine") if ok else []
            n_judged_a = sum(len(v) for v in a["judged_a"].values())
            ok = ok and pl.get("sessions") == [sid_a] and pl.get("segments") == 2 and tl_segs == 2 and \
                len(marks) == n_judged_a and len(chat_marks) == n_judged_a and not engine and \
                sid_b not in pl.get("sessions", [])
            self._shot("_agree_analysis")
            self.check(f"{p}_analysis", ok, t1,
                       f"sessions={len(pl.get('sessions', []))} segments={pl.get('segments')} "
                       f"timeline_segments={tl_segs} primary_markers={len(marks)} chat_marks={len(chat_marks)} "
                       f"engine_markers={len(engine)}")
            self.win.show_home()
            self.pump(50)
            return ok
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(limit=6)
            return self.check("agreement_flow", False, t0, type(e).__name__)
        finally:
            if tmp is not None:
                self._flow_cleanup(tmp, p, t0)

    # ---------------------------------------------------------------- --adjudication-flow
    def step_adjudication_flow(self) -> bool:
        t0 = time.perf_counter()
        if not self.args.adjudication_flow:
            return self.check("adjudication_flow", True, t0, "skip (--adjudication-flow 없음)")
        from app import agreement_report as ar
        from app import relabel as relabel_io
        from app.core import origin as og
        from app.core import phase as ph
        p = "adj"
        tmp = None
        try:
            tmp, dst, out_root, done = self._flow_copy(p)
            self.check(f"{p}_copy", bool(done), time.perf_counter(), f"copied={len(done)}")
            a = self._flow_primary(dst, p)
            if a is None:
                return False
            b = self._flow_second(dst, a["plan"], a["sid_a"], p, n_missed=2, a_items=a["a_items"])
            if b is None:
                return False
            sid_a, sid_b = a["sid_a"], b["sid_b"]
            # ---- 보고서: 연결·놓친 후보 줄 (1차↔재라벨이라 놓친 후보는 '1차는 ④ 없음' 1줄, D-13 3)
            t1 = time.perf_counter()
            res, rep, md = self._flow_report(dst, sid_a, sid_b, out_root)
            ms = (rep.get("missed") or {}).get("judged") or {}
            lk = rep.get("links") or {}
            ok = res is not None and ms.get("b") == b["n_missed"] and ms.get("extra_b") == b["n_extra"] and \
                "links" in rep and "놓친" in md and "연결" in md
            self.check(f"{p}_report", ok, t1,
                       f"missed_judged_a={ms.get('a')} missed_judged_b={ms.get('b')} extra_b={ms.get('extra_b')} "
                       f"pair_applicable={(rep.get('missed') or {}).get('pair_applicable')} "
                       f"links_a={lk.get('a_links')} links_b={lk.get('b_links')} "
                       f"md_missed_line={'놓친' in md} md_links_line={'연결' in md} format_rev={rep.get('format_rev')}")
            self.win.show_home()
            self.pump(50)
            if not ok:
                return False
            # ---- 조정 세션: 불일치 2건 조정 + pattern_tag (근거 한 줄은 화면에만, D-12 4) · 조정 화면이 넓어짐 ·
            #      출처 판정 대상 만들기(D-13 5) · 두 번째 사람(L03) relabel_second 계획 만들기(14 4-2 의 9)
            t1 = time.perf_counter()
            ok, ctx_ad = self._flow_open(dst, "new:adjudication", self.AGREE_A)
            wb = self.wb
            av = wb.adjudication_view if ok else None
            ok = ok and wb.mode == ph.ADJUDICATE and av is not None
            self.pump(100)
            sizes = wb.hsplit.sizes() if ok else [0, 0]
            n_cases = av.load_cases() if ok else 0
            banner = "조정자 = 라벨러" in av.banner.text() if av else False
            saved = []
            for i, c in enumerate((av.cases if av else [])[:2]):
                av.select_case(i)
                side_ok = len(av.side_texts(c)) == 2
                if not (side_ok and av.choose_decision(f"pick:{c.sides[0].session_id}")):
                    break
                av.cause_box.setCurrentIndex(av.cause_box.findData("guide_unclear"))
                av.tag_edit.setText("smoke-경계흐림")
                rec = av.save_current()
                if rec is None:
                    break
                saved.append(rec)
            self.pump(30)
            self._shot("_adj")
            fit_a = self._fit(av) if av is not None else "-"
            kinds = sorted({c.target_kind for c in (av.cases if av else [])})
            ok = ok and n_cases >= 2 and banner and len(saved) == 2 and \
                all(r["body"].get("pattern_tag") == "smoke-경계흐림" for r in saved) and sizes[1] > sizes[0]
            self.check(f"{p}_adjudicate", ok, t1,
                       f"cases={n_cases} kinds={kinds} saved={len(saved)} adjudicator_is_labeler={banner} "
                       f"split={sizes[0]}:{sizes[1]} fit={fit_a}")
            if not ok:
                return False
            t1 = time.perf_counter()
            tobj = av.make_origin_targets()
            want = {c for v in a["a_items"].values() for c in v}
            got = {it["chat_id"] for it in (tobj or {}).get("items") or []}
            again = av.make_origin_targets()
            t_file = os.path.isfile(os.path.join(dst, "origin", (tobj or {}).get("target_id", "-") + ".json"))
            plan3 = av.make_second_plan(sid_a, "L03")
            ok = tobj is not None and tobj.get("basis") == og.SAMPLE_BASIS and got == want and again is None and \
                t_file and plan3 is not None and plan3.get("session_kind") == "relabel_second"
            self.check(f"{p}_tools", ok, t1,
                       f"origin_targets={len(got)} primary_sample_items={len(want)} same_list_refused={again is None} "
                       f"file={t_file} second_plan_blocks={len((plan3 or {}).get('selected') or [])}")
            sid_ad = ctx_ad.session_id if ctx_ad else None
            self.win.show_home()
            self.pump(50)
            if not ok:
                return False
            # ---- 계획 취소: L01 1차를 다시 열어 같은 사람 재라벨 계획도 만든다 → 보고서 메뉴가 막힘 → 조정 세션에서
            #      '세션 → 재라벨 계획 취소…' 로 두 계획을 취소 → 보고서 메뉴가 다시 켜짐
            t1 = time.perf_counter()
            ok, _c = self._flow_open(dst, "resume:" + sid_a, self.AGREE_A)
            self.win.update_session_actions()
            plan2 = self.win.relabel_plan_dialog(run=lambda d: d.make("relabel_same", self.AGREE_A)) \
                if ok and self.win.a_plan.isEnabled() else None
            self.win.show_home()
            self.pump(50)
            pend = sorted([plan2["plan_id"], plan3["plan_id"]]) if plan2 else []
            ok = plan2 is not None and sorted(ar.pending_relabel_plans(dst)) == pend
            ok2, _c2 = self._flow_open(dst, "resume:" + sid_ad, self.AGREE_A) if ok else (False, None)
            self.win.update_session_actions()
            blocked = not self.win.a_agreement.isEnabled()
            cancel_on = self.win.a_cancel_plan.isEnabled()
            docs = []
            for pid in pend if ok2 and cancel_on else []:
                def run_cancel(d, pid=pid):
                    d.plan_box.setCurrentIndex(d.plan_box.findData(pid))
                    d.confirm.setChecked(True)
                    d.cancel_plan(reason="smoke: 이 재라벨을 하지 않음")
                docs.append(self.win.plan_cancel_dialog(run=run_cancel))
            self.win.update_session_actions()
            unblocked = self.win.a_agreement.isEnabled()
            idx = relabel_io.read_index(dst)
            st = {e["plan_id"]: e["status"] for e in (idx or {}).get("plans", [])}
            kept = bool(pend) and all(os.path.isfile(relabel_io.plan_path(dst, x)) for x in pend)
            ok = ok and ok2 and blocked and cancel_on and len(docs) == 2 and all(d is not None for d in docs) and \
                unblocked and kept and all(relabel_io.is_cancelled(dst, x) and st.get(x) == "cancelled" for x in pend)
            self.check(f"{p}_cancel_plan", ok, t1,
                       f"report_blocked_before={blocked} cancel_menu={cancel_on} cancelled={sum(d is not None for d in docs)} "
                       f"report_enabled_after={unblocked} plan_files_kept={kept} "
                       f"index_status={[st.get(x) for x in pend]} "
                       f"canceller_is_labeler={[(d or {}).get('canceller_is_labeler') for d in docs]}")
            self.win.show_home()
            self.pump(50)
            if not ok:
                return False
            # ---- 출처 판정: 조정 세션이 만든 대상 + 합성 자동 결과 대상(앞 날짜라 먼저 합쳐짐) → 출처 판정 세션에서 전부
            t1 = time.perf_counter()
            from app.ui.origin_view import write_targets_file
            ids = [c for v in a["judged_a"].values() for c in v][:3]
            tgt = og.build_targets(chat_ids=ids, created_at="2026-10-01T10:00:00+09:00", created_by=self.AGREE_A,
                                   basis="smoke", note="합성 시험 대상",
                                   auto={ids[0]: {"origin": "virtual", "method": "generation_log_match",
                                                  "generation_ref": {"file": "gen-smoke.jsonl", "gen_id": "g1"},
                                                  "score": 0.9, "second_score": 0.2}})
            write_targets_file(os.path.join(dst, "origin"), tgt)
            total_want = len(want | set(ids))
            # 감시는 세션 화면 뒤부터(세션 화면은 고른 라벨러 자기 세션 첫 줄과 relabel/index.json 을 읽는다, D-12 2):
            # 출처 판정 세션은 다른 세션 기록·samples/·relabel/ 을 열지 않는다(03 12-1)
            forbidden = (os.path.normcase(os.path.join(dst, "labels", sid_a)),
                         os.path.normcase(os.path.join(dst, "labels", sid_b)),
                         os.path.normcase(os.path.join(dst, "samples")),
                         os.path.normcase(os.path.join(dst, "relabel")))
            watch: dict = {}

            def start_watch():
                watch["stop"], watch["st"] = self._watch_opens(forbidden)

            def stop():
                if "stop" in watch:
                    watch["stop"]()
            pr0 = pr = None
            refs: list = []
            multi_warn = False
            n = 0
            marked = False
            fit_o = "-"
            wst = {"seen": 0, "bad": ["감시 안 걸림"]}
            try:
                ok, _co = self._flow_open(dst, "new:origin_review", self.AGREE_A, on_chosen=start_watch)
                wst = watch.get("st") or {"seen": 0, "bad": ["감시 안 걸림"]}
                wb = self.wb
                ov = wb.origin_view if ok else None
                ok = ok and wb.mode == ph.ORIGIN and ov is not None and \
                    self.wait(lambda: "origin_msgs" not in wb._jobs and ov.progress() is not None, 30)
                pr0 = ov.progress() if ov else None
                if ok and ov.take_auto():
                    ov.set_confidence("probably")
                    n += ov.save_current() is not None
                for k in range((pr0.total if pr0 else 0) - n):
                    if not ok:
                        break
                    origin, conf = (("real", "certain"), ("undeterminable", None))[k % 2]
                    ov.set_origin(origin)
                    if conf:
                        ov.set_confidence(conf)
                    ov.rationale.setText(f"{_hms(1_000 + k)} smoke 출처 {origin}")
                    n += ov.save_current() is not None
                pr = ov.progress() if ov else None
                # 대상 파일 둘(조정 세션이 만든 것 + 합성) → 경고 1줄, 실은 파일 ID 를 작업대가 넘길 수 있음(15 5-2 의 6·7번)
                refs = wb.origin_target_refs() if ok else []
                multi_warn = bool(ov.multi_targets_warning()) and ov.multi_warn.isVisibleTo(ov) if ov else False
                marked = wb.mark_phase_done() is not None if ok else False
                self.pump(30)
                self._shot("_adj_origin")
                fit_o = self._fit(ov) if ov is not None else "-"
                self.win.show_home()
                self.pump(50)
            finally:
                stop()
            ok = ok and pr0 is not None and pr0.total == total_want and n == total_want and pr is not None and \
                pr.done == total_want and pr.auto == 1 and marked and not wst["bad"] and wst["seen"] > 0 and \
                len(refs) == 2 and tgt["target_id"] in [r["target_id"] for r in refs] and multi_warn
            self.check(f"{p}_origin", ok, t1,
                       f"target_files={len(refs)} multi_warning={multi_warn} "
                       f"targets={pr0.total if pr0 else '-'} want={total_want} saved={n} auto={pr.auto if pr else '-'} "
                       f"by_origin={dict(pr.by_origin) if pr else '-'} forbidden_opens={len(wst['bad'])} "
                       f"{sorted({os.path.basename(x) for x in wst['bad']})[:4] if wst['bad'] else ''}"
                       f"fit={fit_o}")
            return ok
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(limit=6)
            return self.check("adjudication_flow", False, t0, type(e).__name__)
        finally:
            if tmp is not None:
                self._flow_cleanup(tmp, p, t0)

    # ---------------------------------------------------------------- --analysis-flow (E 첫 묶음, 08 D-01·D-15 7·8)
    def _open_view_only(self, dst: str) -> bool:
        old = self.win.session_chooser
        self.win.session_chooser = lambda outcome: None
        try:
            out = self.win.project_view.open_path(dst)
        finally:
            self.win.session_chooser = old
        ok = out is not None and out.ok and self.wb is not None and self.wait(
            lambda: not ({"chat", "media_check", "chat_index", "store"} & set(self.wb._jobs)))
        self.win.update_session_actions()
        self.win.update_analysis_actions()
        return ok and self.wb.session is None

    def step_analysis_flow(self) -> bool:
        t0 = time.perf_counter()
        if not self.args.analysis_flow:
            return self.check("analysis_flow", True, t0, "skip (--analysis-flow 없음)")
        from app import seal
        p = "an"
        tmp = None
        # 경고·안내 상자는 모달이라 화면 없이 멈춘다 — 이 흐름 동안은 문장 앞부분만 찍는다(코드·건수·ID 만 담긴 문장)
        old_warn, old_inform = self.win.warn, self.win.inform
        self.win.warn = lambda title, text: print(f"  경고: {str(text)[:160]}", flush=True)
        self.win.inform = lambda title, text: print(f"  안내: {str(text)[:160]}", flush=True)
        try:
            tmp, dst, out_root, done = self._flow_copy(p)
            ai_dir = os.path.normcase(seal.default_ai_root(dst))
            outside = not _inside(ai_dir, dst)
            self.check(f"{p}_copy", bool(done) and outside, time.perf_counter(),
                       f"copied={len(done)} ai_root_outside_project={outside}")
            # ---- A. 보기 전용: 합성 엔진 결과 가져오기(봉인) → 해제 조건 미충족 거부
            t1 = time.perf_counter()
            eng = os.path.join(tmp, "engine-out.json")          # 사본 밖
            with open(eng, "wb") as fh:
                fh.write(synthetic_engine_result(dst))
            ok = self._open_view_only(dst)
            menu_on = self.win.m_analysis.isEnabled() and self.win.a_seal_import.isEnabled()
            e = self.win.seal_import_dialog(path=eng, by=self.AGREE_A) if ok and menu_on else None
            os.remove(eng)                                       # 봉인 사본만 남긴다
            self.win.update_analysis_actions()
            refused = self.win.unseal_dialog(by=self.AGREE_A, confirm=lambda _t: False) is None \
                if self.win.a_unseal.isEnabled() else False
            reasons = list(getattr(self.win, "last_unseal_reasons", []))
            st = seal.seal_status(dst)
            ok = ok and menu_on and e is not None and e.new and e.check == "valid" and refused and bool(reasons) \
                and st.sealed_count == 1 and st.unsealed_count == 0
            self.check(f"{p}_seal", ok, t1, f"sealed={st.sealed_count} sha={e.sha256[:8] if e else '-'} "
                                            f"size={e.size if e else 0} check={e.check if e else '-'} "
                                            f"unseal_refused={refused} unmet={len(reasons)}")
            self.win.show_home()
            self.pump(50)
            if not ok:
                return False
            # ---- B. L01 1차 → 잠그기 → 계획, L02 재라벨 → 잠그기 (기존 흐름). 그동안 data\ai 를 한 번도 열지 않는지
            stop, watch = self._watch_opens((ai_dir,))
            try:
                a = self._flow_primary(dst, p)
                b = self._flow_second(dst, a["plan"], a["sid_a"], p, a_items=a["a_items"]) if a else None
                # 잠긴 라벨링 세션을 다시 열어도 분석 메뉴는 꺼져 있다
                t1 = time.perf_counter()
                offs = []
                for sid, lab in ((a or {}).get("sid_a"), self.AGREE_A), ((b or {}).get("sid_b"), self.AGREE_B):
                    if not sid:
                        continue
                    okr, _c = self._flow_open(dst, "resume:" + sid, lab)
                    self.win.update_session_actions()
                    self.win.update_analysis_actions()
                    offs.append(okr and not self.win.m_analysis.isEnabled()
                                and self.win.seal_import_dialog(path=os.path.join(tmp, "없음.json"), by=lab) is None)
                    self.win.show_home()
                    self.pump(50)
            finally:
                stop()
            ok = a is not None and b is not None and len(offs) == 2 and all(offs) and watch["bad"] == []
            self.check(f"{p}_labeling_blind", ok, t1, f"menu_off={sum(offs)}/2 ai_opens={len(watch['bad'])} "
                                                      f"watched={watch['seen']}")
            if not ok:
                return False
            # ---- C. 분석 세션, 해제 전: 나란히 보기는 열리지 않고 '봉인됨 1건' 만
            t1 = time.perf_counter()
            stop, watch = self._watch_opens((os.path.join(ai_dir, ""),))
            try:
                okc, ctx_c = self._flow_open(dst, "new:analysis", self.AGREE_A)
                self.win.update_analysis_actions()
                tip = self.win.a_side_by_side.toolTip()
                closed = self.win.side_by_side_view(show=False) is None
            finally:
                stop()
            bins = [x for x in watch["bad"] if os.sep + "sealed" + os.sep in x or os.sep + "runs" + os.sep in x]
            ok = okc and not self.win.a_side_by_side.isEnabled() and "봉인됨 1건" in tip and closed and not bins \
                and not self.win.a_seal_import.isEnabled() and not self.win.a_unseal.isEnabled()
            self.check(f"{p}_view_sealed", ok, t1, f"side_by_side_off={not self.win.a_side_by_side.isEnabled()} "
                                                   f"sealed_count_shown={'봉인됨 1건' in tip} bin_or_run_opens={len(bins)}")
            self.win.show_home()
            self.pump(50)
            if not ok:
                return False
            # ---- D. 보기 전용: 조건이 되어 봉인 해제
            t1 = time.perf_counter()
            ok = self._open_view_only(dst)
            res = self.win.unseal_dialog(by=self.AGREE_A, confirm=lambda _t: True) if ok else None
            st = seal.seal_status(dst)
            ok = ok and res is not None and res.conditions_met and st.unsealed_count == 1 and st.sealed_count == 0
            self.check(f"{p}_unseal", ok, t1, f"conditions_met={res.conditions_met if res else '-'} "
                                              f"presealed={res.presealed if res else '-'} "
                                              f"locks={len(res.record.get('locks') or []) if res else 0}")
            self.win.show_home()
            self.pump(50)
            if not ok:
                return False
            # ---- E. 분석 세션: 엔진과 나란히 보기 (해제된 run · 최종 세트 · 점수)
            t1 = time.perf_counter()
            okc, _ctx = self._flow_open(dst, "new:analysis", self.AGREE_A)
            if self.args.window_size:
                self._resize(self.args.window_size)
            self.win.update_analysis_actions()
            dlg = self.win.side_by_side_view(show=True) if okc and self.win.a_side_by_side.isEnabled() else None
            v = dlg.view if dlg is not None else None
            s = v.summary() if v is not None else {}
            fit = ""
            if dlg is not None:
                if self.args.window_size:
                    w, h = (int(x) for x in self.args.window_size.lower().split("x", 1))
                    dlg.resize(min(dlg.width(), w - 40), min(dlg.height(), h - 60))
                self.pump(300)
                fit = self._fit(v)
                jumped = v.next_diff()
            else:
                jumped = False
            ok = v is not None and not v.is_sealed() and bool(v.items()) and bool(v.score_table_rows()) and jumped
            if dlg is not None and self.args.screenshot and _inside(self.args.screenshot, SCREENS_DIR):
                root_, ext = os.path.splitext(self.args.screenshot)
                pix = dlg.grab()
                pix.save(root_ + "_an_side" + (ext or ".png"), "PNG")
            self.check(f"{p}_side_by_side", ok, t1,
                       f"items={len(v.items()) if v else 0} engine_rows={s.get('engine_rows')} "
                       f"human_rows={s.get('human_rows')} same={s.get('same')} grade_diff={s.get('grade_diff')} "
                       f"engine_only={s.get('engine_only')} human_only={s.get('human_only')} "
                       f"score_rows={len(v.score_table_rows()) if v else 0} jump={jumped} fit={fit}")
            self.win.show_home()
            self.pump(50)
            if not ok:
                return False
            # ---- F. 보기 전용: AI 비교 보고서 (프로젝트 밖)
            t1 = time.perf_counter()
            ok = self._open_view_only(dst)
            rep = self.win.ai_compare_dialog(out_root=out_root) if ok and self.win.a_ai_compare.isEnabled() else None
            files = sorted(os.listdir(rep.out_dir)) if rep and rep.out_dir else []
            tables = sorted(os.listdir(os.path.join(rep.out_dir, "tables"))) if "tables" in files else []
            e1 = (rep.report.get("e1") or {}) if rep else {}
            hdr = (rep.report.get("header") or {}) if rep else {}
            ok = rep is not None and files == ["manifest.json", "report.json", "report.md", "tables"] and \
                not _inside(rep.out_dir, dst) and bool(tables) and hdr.get("conditions_met") is True and \
                hdr.get("pre_sealed") is True
            self.check(f"{p}_report", ok, t1,
                       f"files={len(files)} tables={len(tables)} e1_population={e1.get('population')} "
                       f"lenient_n={(e1.get('lenient') or {}).get('n')} pre_sealed={hdr.get('pre_sealed')} "
                       f"conditions_met={hdr.get('conditions_met')} warnings={len(rep.warnings) if rep else 0}")
            self.win.show_home()
            self.pump(50)
            return ok
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(limit=6)
            return self.check("analysis_flow", False, t0, type(e).__name__)
        finally:
            self.win.warn, self.win.inform = old_warn, old_inform
            if tmp is not None:
                self._flow_cleanup(tmp, p, t0)

    def _resize(self, spec: str) -> None:
        """--window-size '1366x768': 메인 창 바깥 크기(테두리 포함)를 그 크기로 맞춘다(노트북 화면 확인, 11 4-2 의 2)."""
        w, h = (int(x) for x in spec.lower().split("x", 1))
        self.win.showNormal()
        self.pump(50)
        fw = max(0, self.win.frameGeometry().width() - self.win.width())
        fh = max(0, self.win.frameGeometry().height() - self.win.height())
        self.win.resize(max(400, w - fw), max(300, h - fh))
        self.win.move(0, 0)
        self.pump(200)

    def _shot(self, suffix: str) -> None:
        """--screenshot 이름 뒤에 suffix 를 붙여 찍는다(SCREENS_DIR 안일 때만)."""
        p = self.args.screenshot
        if p and _inside(p, SCREENS_DIR):
            root_, ext = os.path.splitext(p)
            self._grab(root_ + suffix + (ext or ".png"))

    def _fit(self, w) -> str:
        """오른쪽 칸 부품이 잘리는지 숫자로: 크기, 세로 스크롤 필요 여부, 글자가 잘린 단추·라벨 수(폭이 sizeHint 보다 좁음),
        부품 오른쪽 밖으로 나간 자식 수. 글자는 찍지 않는다."""
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QLabel, QLineEdit,
                                       QScrollArea)
        self.pump(30)
        vs = hs = False
        for sa in ([w] if isinstance(w, QScrollArea) else []) + w.findChildren(QScrollArea):
            if sa.isVisibleTo(w) or sa is w:
                vs = vs or sa.verticalScrollBar().maximum() > 0
                hs = hs or sa.horizontalScrollBar().maximum() > 0     # 가로 스크롤 = 폭이 모자라 오른쪽이 가려짐
        clipped = outside = 0
        # 고르기 칸·숫자 칸도 오른쪽 밖을 센다(14 c6 ui 보고: 전에는 빠져 있었다). 글자 잘림은 단추·라벨만 본다.
        # 고르기·숫자 칸 안의 QLineEdit 은 그 칸으로 한 번만 센다.
        boxes = w.findChildren(QComboBox) + w.findChildren(QAbstractSpinBox)
        inner = {id(x) for b in boxes for x in b.findChildren(QLineEdit)}
        edits = [x for x in w.findChildren(QLineEdit) if id(x) not in inner]
        for c in w.findChildren(QAbstractButton) + w.findChildren(QLabel) + edits + boxes:
            if not c.isVisibleTo(w) or c.width() <= 0:
                continue
            if isinstance(c, QLabel) and (c.wordWrap() or not c.text()):
                pass
            elif isinstance(c, QAbstractButton) and c.text() and c.sizeHint().width() > c.width() + 1:
                clipped += 1
            elif isinstance(c, QLabel) and c.sizeHint().width() > c.width() + 1:
                clipped += 1
            # 스크롤 영역 안 자식은 그 보이는 칸(viewport) 기준
            sa = next((p for p in self._ancestors(c, w) if isinstance(p, QScrollArea)), None)
            box = sa.viewport() if sa is not None else w
            right = c.mapTo(box, QPoint(c.width(), 0)).x()
            if right > box.width() + 1:
                outside += 1
                if os.environ.get("CHATCOACH_FIT_DEBUG") == "1":          # 부품 종류·폭만(글자는 찍지 않는다)
                    par = c.parentWidget()
                    print(f"    outside: {type(c).__name__} right={right} box={box.width()} "
                          f"parent={type(par).__name__ if par else '-'} min={c.minimumSizeHint().width()}")
        if os.environ.get("CHATCOACH_FIT_DEBUG") == "1":
            from PySide6.QtWidgets import QWidget
            for c in w.findChildren(QWidget):
                m = c.minimumSizeHint().width()
                if c.isVisibleTo(w) and m > 300:
                    print(f"    wide: {type(c).__name__}#{c.objectName() or '-'} min={m} "
                          f"parent={type(c.parentWidget()).__name__ if c.parentWidget() else '-'}")
        return (f"{w.width()}x{w.height()}/vscroll={'y' if vs else 'n'}/hscroll={'y' if hs else 'n'}"
                f"/clipped={clipped}/outside={outside}")

    @staticmethod
    def _ancestors(c, top):
        p = c.parentWidget()
        while p is not None and p is not top:
            yield p
            p = p.parentWidget()

    def _grab(self, p: str) -> tuple[bool, int, str]:
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        offscreen = self.app.platformName() in ("offscreen", "minimal")
        if offscreen:
            self.pump(100)
            pix = self.win.grab()
            note = " (offscreen: 영상 칸은 vo=null 이라 비어 있음)"
        else:
            # mpv 는 자기 네이티브 창에 직접 그려 QWidget.grab() 에 영상이 안 담긴다 → 화면에서 창 영역을 찍는다
            self.win.raise_()
            self.win.activateWindow()
            self.pump(500)
            g = self.win.frameGeometry()
            pix = self.win.screen().grabWindow(0, g.x(), g.y(), g.width(), g.height())
            note = " (화면 캡처: 창 위에 다른 창이 있으면 같이 찍힘)"
        saved = pix.save(p, "PNG")
        size = os.path.getsize(p) if saved and os.path.isfile(p) else 0
        return saved, size, note

    def step_shot(self) -> bool:
        t0 = time.perf_counter()
        p = self.args.screenshot
        if not p:
            return self.check("screenshot", True, t0, "skip (--screenshot 없음)")
        if not _inside(p, SCREENS_DIR):
            return self.check("screenshot", False, t0, f"거부: {SCREENS_DIR} 밖")
        saved, size, note = self._grab(p)
        return self.check("screenshot", saved and size > 1000, t0, f"bytes={size}{note}")

    def step_close(self) -> bool:
        t0 = time.perf_counter()
        self.win.show_home()
        self.pump(100)
        from app.ui import project_view as pvm
        again = pvm.open_checked(self.args.project)
        relocked = again.lock is not None
        pvm.release_outcome(again)
        st = os.stat(os.path.join(self.args.project, "project.json"))
        same = (st.st_mtime_ns, st.st_size) == self.pj_before
        return self.check("close", self.win.workbench is None and relocked and same, t0,
                          f"lock_released={relocked} project_json_unchanged={same}")

    # ---------------------------------------------------------------- 실행
    def run(self) -> int:
        self.setup()
        steps = [self.step_window, self.step_open, self.step_chat, self.step_timeline, self.step_video, self.step_waveform,
                 self.step_seek, self.step_chat_activate, self.step_play, self.step_zoom, self.step_filter,
                 self.step_store_copy, self.step_shot, self.step_labeling_flow, self.step_agreement_flow,
                 self.step_adjudication_flow, self.step_analysis_flow, self.step_close]
        t_all = time.perf_counter()
        for st in steps:
            try:
                ok = st()
            except Exception as e:  # noqa: BLE001
                traceback.print_exc(limit=3)
                ok = self.check(st.__name__.removeprefix("step_"), False, time.perf_counter(), type(e).__name__)
            if not ok and st in (self.step_window, self.step_open):
                break                                            # 이 둘이 안 되면 뒤 단계는 의미가 없다
        try:
            if self.win is not None:
                self.win.close()
                self.pump(100)
        except Exception:  # noqa: BLE001
            traceback.print_exc(limit=3)
        self.step_cache_drop()
        failed = [r for r in self.results if not r[1]]
        print(f"== {len(self.results) - len(failed)} passed, {len(failed)} failed, "
              f"{(time.perf_counter() - t_all):.1f} s, platform={self.app.platformName() if self.app else '-'}")
        steps_ok = True
        a = self.args
        if a.store_copy and a.labeling_flow and a.agreement_flow and a.adjudication_flow and not failed:
            an = bool(a.analysis_flow)
            missing, extra = compare_steps((r[0] for r in self.results), analysis=an)
            steps_ok = not missing
            seen = [s for s in dict.fromkeys(r[0] for r in self.results) if s != "analysis_flow"]
            print(f"== 단계 목록(14 c5 의 49{' + E 분석 13' if an else ''} 와 대조, 15 5-2 의 16): {len(seen)}"
                  f"/{len(expected_steps(an))} missing={missing} extra={extra}")
        return 0 if not failed and steps_ok else 1

    def step_cache_drop(self) -> bool:
        """끝에: 스모크 데이터 폴더(--data-dir)에 남은 채팅 색인 캐시(chat_id·시각만)를 지운다(15 5-2 의 10번).
        사본들도 원본 project.json 의 project_id 를 쓰므로 그 하나만 지우면 된다. 앱의 진짜 data 폴더는 건드리지 않는다."""
        t0 = time.perf_counter()
        import json
        from app import chat_index as ci
        try:
            with open(os.path.join(self.args.project, "project.json"), encoding="utf-8") as f:
                pid = (json.load(f) or {}).get("project_id")
        except (OSError, ValueError):
            pid = None
        if not pid:
            return self.check("cache_drop", True, t0, "skip (project_id 없음 — 캐시를 만들지 않음)")
        try:
            dropped = ci.drop_cache(pid, self.data_root)
            left = os.path.exists(ci.cache_path_for(pid, self.data_root))
        except (OSError, ValueError) as e:
            return self.check("cache_drop", False, t0, type(e).__name__)
        return self.check("cache_drop", not left, t0, f"dropped={dropped} left={left}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    args = parse_args(argv)
    if args.screenshot_cleanup is not None:
        try:
            return screenshot_cleanup(args.screenshot_cleanup.split(","), yes=args.yes)
        except ValueError as e:
            print(str(e))
            return 2
    if not args.project or not os.path.isfile(os.path.join(args.project, "project.json")):
        print("project.json 이 없습니다")
        return 2
    return Smoke(args).run()


if __name__ == "__main__":
    sys.exit(main())
