"""창 골격: 메뉴·상태바·화면 전환(프로젝트 화면 ↔ 작업대)·업데이트 연동.

화면 전환 (02 7-1): 시작은 프로젝트 화면(최근 목록·폴더에서 열기, app/ui/project_view.py). 프로젝트를 열면
세션 선택 화면(app/ui/session_dialog.py, 08 D-09 1)을 띄우고, 고른 SessionContext 로 작업대(app/ui/workbench.py)를
만든다. 작업대가 store.open(lock_factory=QLockFile, 열 때 잡은 잠금을 넘김)으로 라벨 기록을 연다. 고르지 않으면(보기 전용·
취소) 세션 없이 연다 — 라벨 저장·분석 보기가 꺼진다. [파일 → 프로젝트 닫기] 나 다른 프로젝트 열기는 작업대를 닫고
(플레이어·워커·session.close 줄·잠금 정리) 새로 만든다. session.close 의 reason: 프로젝트 닫기 'user',
다른 프로젝트 열기 'switch_project', 앱 종료 'app_exit'.

세션·보고 메뉴 (06 7-1·7-3, 08 D-11): [세션 → 이 세션 잠그기…] 는 app/ui/lock_dialog(2단계) 뒤 작업대를 읽기 전용으로,
[세션 → 재라벨 계획 만들기…] 는 잠긴 자기 1차 세션에서만(app/ui/relabel_dialog), [보고 → 일치도 보고서…] 는 잠긴 두 세션을
골라 프로젝트 밖에 쓴다(app/ui/agreement_dialog). 보고서 메뉴는 잠기지 않은 라벨링 세션 중에는 꺼진다.
[세션 → 재라벨 계획 취소…] 는 조정 세션(검토자)에서만 켜진다(app/ui/plan_cancel_dialog, 06 7-1·D-12 7). 조정·출처 판정·
④ 놓친 후보는 작업대의 모드다(세션 종류에 따라 모드 줄에 나온다, app.core.phase.modes_for).

업데이트 연동 흐름 (04 6장): 시작 30초 뒤 백그라운드 확인 → 상태바 3상태 → '새 버전' 버튼 → 대화상자 →
준비(진행률·취소) → 교체 전 조건 → 교체 직전 data 백업(backup.pre_update_backup, 6-7) → 적용(시작 표식 확인) → 앱 종료.
확인 시각은 updater 가 결과 뒤에 쓴다. 시작 확인은 on_start=True(필수 업데이트·지원 종료면 주기와 상관없이),
채널은 숨은 설정 settings.ini [update] channel (stable·beta, 04 6-2).

분석 메뉴 (E, 08 D-01·D-15 7·8, 06 6장·7장, app/analysis_flow.py):
- [분석 → AI 결과 가져오기(봉인)…] 세션 밖(보기 전용)·조정 세션: 파일을 바이트 그대로 봉인(app.seal). 화면에는 유효·거부 코드·
  해시 앞 8자·크기만. 같은 폴더에 '<이름>.topics.json'(주제 흐름)이 있으면 짝으로 같이 봉인한다(D-17 5).
- [분석 → 봉인 해제…] 같은 자리: 06 7-1 조건을 안내하고, 안 되면 거부(사유를 적으면 앞당겨 열기, conditions_met=false).
  짝 주제 흐름은 같은 해제에서 같이 풀린다.
- [분석 → 엔진과 나란히 보기] 분석 세션에서만, 해제된 run 이 있을 때만(D-15 7). app/ui/analysis_view 를 모달 아닌 창으로.
  해제된 run 이 둘 이상이면 판 고르기·판끼리 견주기(17 6-1 4번). 필터 'AI 확인 큐'(08 D-29 3)의 판정은
  app.ai_confirm.ConfirmStore 로 <ai 루트>\\<project_id>\\confirm\\<run_id>.jsonl 에 쓰고, 고른 건은 작업대의 확인 재생기로
  채팅 − 5초 ~ 발화 끝 + 3초를 반복한다(Workbench.loop_range, 창을 닫으면 끔).
- 한 번에 열기(D-16): main.py --project <폴더> --analysis [--confirm] → open_analysis: 시범 프로젝트는
  reference_set.pilot_sessions_locked 가 True 일 때만(아니면 상태 줄에 까닭 한 줄, 프로젝트를 열지 않음), 마지막 라벨러의
  이어 쓸 분석 세션(없으면 새로)으로 열고, 작업대 일이 끝나면 나란히 보기(--confirm 이면 'AI 확인 큐' 필터 · 기본 run ·
  첫 대기 건 재생). 마지막 라벨러를 모르면 세션 화면을 띄운다.
- [분석 → 방송 요약] 같은 자리·같은 조건: 주제 덩어리별 채팅·반응률·놓친 질문(app/ui/summary_view, D-01·D-17 8).
  분석 세션 작업대에는 타임라인 위 주제 트랙(app/ui/topic_track)이 같이 보인다(해제 전에는 '봉인됨').
- [보고 → AI 비교 보고서…] 세션 밖·조정 세션: 해제된 run 으로 app.ai_compare 보고서(프로젝트 밖).
- 라벨링·재라벨·출처 판정 세션에서는 분석 메뉴가 꺼지고 data\\ai 를 열지 않는다. 봉인 건수는 메뉴를 펼 때만 읽는다.

훈련 메뉴 (F, 08 D-01·D-16·D-18 6·7·8, 18 6-2):
- [훈련 → 스트리머 훈련] 한 번으로 app/ui/trainer_view 를 작업대 위에 쌓아 연다(고르는 창 없음). 기준은
  app.reference_set.training_availability(사람 최종 세트, 없으면 labels\\manual-*.csv). 기준이 없거나 시범 프로젝트
  (잠기기 전)·영상 없음이면 메뉴를 끄고 메뉴 안에 까닭 한 줄. 기록은 app.training_store(training\\),
  start 줄이 곧 노출 기록이다(app.core.exposure 규칙 5 — 그 뒤 이 프로젝트의 라벨링 세션은 대표값에서 빠진다).
  [닫기] 는 training.end 를 쓰고 작업대로 돌아간다. 작업대를 닫을 때도 먼저 닫는다.
- 라벨링(1차·재라벨)·출처 판정 세션에서는 같은 메뉴 한 번이 '보기 전용으로 다시 열고 훈련'(D-19 3): 저장 안 한 구간
  변경만 묻고, 세션을 닫아 잠금을 풀고, 세션 화면 없이 보기 전용으로 다시 연 뒤 채팅을 읽으면 훈련을 연다
  (reopen_view_only_and_train). 훈련 화면을 라벨링 세션 위에 쌓지는 않는다(06 7장 블라인드).
- 훈련 중에는 분석 메뉴·AI 비교 보고서·일치도 보고서를 끈다(D-18 6·8, D-19 4).
- 가상 채팅 섞기(H-4, 20 5장, D-19 2): 보기 전용에서 이 구간의 가장 새 시나리오(scenarios\\, app.scenario_mix)가 있으면
  [훈련 → 가상 채팅 섞기] 가 기본으로 켜져 '스트리머 훈련' 한 번에 섞인다(끄기 1클릭). 없으면 그 자리의
  [훈련 → 가상 채팅 만들기] 한 번이 엔진 도구(tools/make_scenario.py)를 따로 프로세스로 워커에서 돌린다. 숨은 설정
  settings.ini [lmstudio] model 이 있으면 같은 한 번 안에서 LM Studio 확인(engine generate --check-server) → 생성
  (engine generate --out … --llm-check) → 섞기(make_scenario --generated)까지 한다(D-24 3, app.scenario_mix.run_virtual_make).
  서버가 꺼져 있으면 retrieved 만 섞고 상태 줄에 'lmstudio_off' 한 줄. 주소는 [lmstudio] url(기본 http://127.0.0.1:1234,
  이 PC 만), [lmstudio] llm_check(기본 켬). 모델 설정이 없으면 LM Studio 에 접속하지 않고 retrieved 만(지금까지와 같음).
  앱은 LM Studio 에 직접 접속하지 않는다(엔진 도구만, D-06). 라벨링·출처 판정 세션에서는 scenarios\\ 를 열지 않고
  두 항목을 숨긴다(20 1장 4번).
- [훈련 → 시청자 훈련] 한 번(G, 08 D-20 2·5, 21 6-1)으로 app/ui/viewer_train_view 를 작업대 위에 쌓아 연다: 소리 켠
  다시보기, 쓰기 칸 하나, 세 축 3단계. 구간·맥락(주제 흐름·VOD 로 보정한 전사 낱말)은 app.viewer_flow 가 고르고 읽는다.
  전사 낱말 파일(derived\\asr-words.json)이 없거나 오래됐으면 열 때 엔진 도구를 워커로 돌려 만든다. 기록은
  app.viewer_store(viewer\\, 보호 구역). 정답을 보이지 않아 노출로 세지 않는다. 시범 프로젝트는 잠기기 전 끈다.
  라벨링·출처 판정 세션에서는 F 와 같이 '보기 전용으로 다시 열고' 연다.
- [훈련 → 가상 채팅 가려내기] 한 번(H-5, 20 7장, 25 6-2, D-24 7 가)으로 app/ui/discern_view 를 작업대 위에 쌓아 연다:
  가장 새 시나리오의 구간을 소리 켜고 틀고, 가상 표시를 숨기고, 맨 위에 '이 화면에는 생성 채팅이 섞여 있습니다' 한 줄,
  채팅마다 '진짜'·'가짜'·'모름' 단추(채팅마다 1클릭). 구간 끝에 정답·숫자 줄. 기록은 app.discern_store(discern\\, 보호
  구역, ID·판정·숫자만). 노출로 세지 않는다. 시범(잠기기 전)·라벨링 세션(1차·재라벨·조정·출처 판정)이면 메뉴를 끄고
  scenarios\\ 를 열지 않는다(discern_block_reason). 시나리오가 없으면 '가상 채팅 만들기' 를 먼저 하라고 메뉴 안에 한 줄.

운영 메뉴 (07 M4):
- [도움말 → 이전 버전으로 되돌리기…] (04 6-10): updater.rollback_status 로 켜고 끈다. 이번 실행이 _backup 을 지울 차례
  (main.py 9단계, delete_backup_pending)면 처음부터 끈다. 확인(RollbackDialog) → 워커에서 updater.stage_rollback(관문) →
  RobocopyApplier.rollback(data 백업 → replace.ps1 -Rollback) → 앱 종료. 업데이트 실패 뒤 제안(updater.rollback_suggestion)도
  같은 함수(rollback_dialog)를 탄다.
- [파일 → 프로젝트 백업…] · [파일 → 백업에서 되살리기…] · [파일 → 프로젝트 지우기(휴지통)…] (app/backup.py, app/ui/ops_dialogs.py).
  지우기는 휴지통으로만 보낸다. 지금 연 프로젝트면 작업대를 닫은 뒤 보낸다.
- 위쪽 띠: '업데이트 필요'·필수 업데이트(SupportBanner, 04 5-5)와 경로 경고(paths.startup_path_issues ·
  project_path_issues, S-09·S-49). '업데이트 필요' 면 작업대의 라벨 기록을 읽기 전용으로 연다(store save_block).
- 세션 메뉴는 작업(run_job_modal · 작업대 워커)이 도는 동안 끈다(15 5-2 의 15번).
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QApplication, QFileDialog, QLabel, QMainWindow, QMessageBox, QProgressDialog,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, updater
from ..appspec import AppSpec
from ..i18n import tr
from ..workers import UpdateCheckWorker, UpdateStageWorker
from .project_view import ProjectView, load_recent, remove_recent
from .update_dialog import RollbackDialog, SupportBanner, UpdateDialog, open_trusted_url, rollback_menu_text

LOG = logging.getLogger("chatcoach")
# 시험 채널 전용: 환경 변수가 1 이면 새 버전을 찾았을 때 대화상자 없이 바로 준비·적용한다 (04 12-5 손 시험·자동 왕복 시험용).
# release 채널은 읽지 않는다.
AUTO_UPDATE_ENV = "CHATCOACH_AUTO_UPDATE"
# 시험 채널 전용: 1 이면 --updated 로 켜진 실행에서 2.5초 뒤 [도움말 → 이전 버전으로 되돌리기] 를 확인 없이 부른다
# (tools/update_roundtrip.py --rollback, 04 12-5). release 채널은 읽지 않는다.
AUTO_ROLLBACK_ENV = "CHATCOACH_AUTO_ROLLBACK"
UPDATE_CHANNEL_KEY = "update/channel"        # 숨은 설정 (04 6-2): stable | beta
# [파일 → 시작할 때 마지막 작업 이어서 열기] (기본 켬, 08 D-16 메모 '한 번 누르기'): 인자 없이 켜면 마지막 프로젝트를 바로
# 열고, 같은 라벨러의 이어 쓸 라벨링 세션이 하나뿐이면 세션 화면 없이 그 세션으로 연다(session.auto_resume_candidate)
RESUME_KEY = "startup/resume_last"
LAST_LABELER_KEY = "session/last_labeler"
BACKGROUND_JOBS = frozenset({"waveform", "media_check", "media_ref", "engine", "chat_index",
                             "topics"})   # 세션 메뉴를 막지 않는 작업대 일
PATH_ACK_GROUP = "path_ack"                 # 앱·데이터·백업 폴더 경고(confirm)를 '그래도 계속' 한 기록
MANUAL_GUIDE =("수동 업데이트: 앱을 끄고 릴리스 페이지에서 전체 zip 을 받아 새 폴더에 푼 뒤, 옛 폴더의 data\\ 를 "
                "새 폴더의 ChatCoach\\ 안으로 복사합니다 (docs/install.md, 04 8장).")
TOPICS_SUFFIX = ".topics.json"
# '보기 전용으로 다시 열고 훈련'(D-19 3): 라벨링 세션에서 메뉴를 누르면 세션을 닫고 보기 전용으로 다시 연 뒤, 채팅·영상
# 확인이 끝나면 훈련을 연다. 기다리는 간격·상한(채팅 읽기는 보통 1초 안팎)
TRAINING_REOPEN_POLL_MS = 150
TRAINING_REOPEN_WAIT_S = 120
TRAINING_REOPEN_TIP = ("이 세션을 닫고(저장 안 한 변경은 묻습니다) 보기 전용으로 다시 연 뒤 훈련을 엽니다. "
                       "훈련을 열면 이 프로젝트의 잠기지 않은 라벨링 세션은 대표값에서 빠집니다")
VIEWER_TIP = "소리 켠 다시보기를 보며 채팅을 써 보고, 주제 적합성·대화 확장성·반복 여부를 축마다 봅니다"
# 가상 채팅 생성(D-24 3): 숨은 설정 settings.ini [lmstudio]. model 이 비면 생성하지 않는다(LM Studio 에 접속도 안 함)
LMSTUDIO_URL_KEY = "lmstudio/url"            # 기본 http://127.0.0.1:1234 — 이 PC(127.0.0.1·localhost·[::1])만
LMSTUDIO_MODEL_KEY = "lmstudio/model"        # LM Studio 에 올린 모델 id 하나(20 4-3 '설정 파일에 적은 model_id')
LMSTUDIO_CHECK_KEY = "lmstudio/llm_check"    # LLM 예/아니오 확인 llm-check-v1 (기본 켬)
MAKE_TIP_RETRIEVED = "같은 방송의 훈련 구간 밖 채팅을 골라 채팅이 드문 곳에 섞을 시나리오를 만듭니다 (AI 없음)"
MAKE_TIP_GENERATE = ("같은 방송의 훈련 구간 밖 채팅을 고르고, 이 PC 의 LM Studio({0})로 생성 채팅도 만들어 채팅이 드문 곳에 "
                     "섞습니다. 밖으로 나가는 것은 없습니다. 서버가 꺼져 있으면 가져온 채팅만 섞습니다")
VIEWER_REOPEN_TIP = ("이 세션을 닫고(저장 안 한 변경은 묻습니다) 보기 전용으로 다시 연 뒤 시청자 훈련을 엽니다. "
                     "시청자 훈련은 정답을 보이지 않아 라벨링 세션의 대표값에는 영향이 없습니다")
# 가상 채팅 가려내기(H-5, 20 7장, 25 6-2, D-24 7 가)
DISCERN_TIP = ("소리 켠 다시보기에서 채팅마다 '진짜' · '가짜' · '모름' 을 한 번씩 누릅니다. 가상 채팅 표시는 숨기고, 구간 끝에 "
               "정답과 숫자(가짜 탐지율·κ)를 보입니다")


def topics_sibling(path: str | None) -> str | None:
    """AI 결과 파일 'a.json' 옆의 주제 흐름 'a.topics.json'(tools/engine_to_ai --out a.json --topics 가 쓰는 이름).
    고른 파일이 이미 주제 흐름이면 None(판정 결과를 골라야 짝이 정해진다)."""
    if not path or path.lower().endswith(TOPICS_SUFFIX):
        return None
    stem, ext = os.path.splitext(path)
    cand = stem + ".topics" + (ext or ".json")
    return cand if os.path.isfile(cand) else None


class MainWindow(QMainWindow):
    def __init__(self, spec: AppSpec, installed: updater.InstalledInfo, data_root: str, log_file: str,
                 base_url: str | None, startup_notes: list[str], flags: set[str], *,
                 abnormal_streak: int = 0, delete_backup_pending: bool = False, path_issues=None):
        """abnormal_streak: crashguard.note_start 의 연속 비정상 종료 횟수 (되돌리기 제안, 04 6-9).
        delete_backup_pending: 이번 실행이 지난 교체의 _backup 을 지울 차례인가 (main.py 9단계) — 그러면 되돌리기 메뉴를 끈다.
        path_issues: paths.startup_path_issues() 결과 (위쪽 경로 띠 · confirm 은 대화상자)."""
        super().__init__()
        self.spec, self.installed, self.data_root, self.log_file = spec, installed, data_root, log_file
        self.base_url = base_url
        self.flags = set(flags)
        self.abnormal_streak = int(abnormal_streak or 0)
        self.delete_backup_pending = bool(delete_backup_pending)
        self.startup_path_issues = list(path_issues or [])
        self.project_path_issues: list = []
        self.retention_line: str | None = None
        self.session_backup = False   # 세션 끝 기록 복사(S-39 ③). main.py 가 켠다(시험·smoke 는 사용자 백업 폴더에 쓰지 않게)
        self.auto = installed.channel in ("test", "source") and os.environ.get(AUTO_UPDATE_ENV, "") == "1"
        self.auto_rollback = installed.channel in ("test", "source") and os.environ.get(AUTO_ROLLBACK_ENV, "") == "1"
        self.state_path = os.path.join(data_root, "update", "state.json")
        self._check_worker: UpdateCheckWorker | None = None
        self._stage_worker: UpdateStageWorker | None = None
        self._result: updater.CheckResult | None = None
        self._rolling_back = False
        self._manual_check = False       # 사용자가 [업데이트 → 지금 확인] 을 눌렀나 (그때만 결과 문장을 보인다)
        self.update_status = tr("업데이트: 아직 확인하지 않음")   # 문제 보고에 넣는 마지막 확인 상태(상태 줄에는 필요할 때만)
        # 시작할 때 마지막 작업 이어서 열기: main.py 가 인자 없이 켠 실행에서만 True 로 둔다(시험·업데이트 뒤 실행은 끔)
        self.resume_on_start = False
        self._auto_resume = False        # 이어서 열기 중(choose_session 이 세션 화면 없이 고를 수 있음)
        self._resume_note: str | None = None
        self.setWindowTitle(f"{spec.name} {__version__}")
        self.resize(760, 480)

        central = QWidget()
        lay = QVBoxLayout(central)
        title = QLabel(f"{spec.name} {__version__}")
        title.setObjectName("panelTitle")
        lay.addWidget(title)
        info = QLabel(tr("프로젝트를 열면 작업대(영상·타임라인·채팅)가 열립니다. 메뉴 [업데이트] 에서 새 버전을 확인할 수 있습니다.\n"
                         "채널 {0} · 데이터 폴더 {1}").format(installed.channel, data_root))
        info.setWordWrap(True)
        lay.addWidget(info)
        self.notes_label = QLabel("\n".join("• " + n for n in startup_notes))
        self.notes_label.setObjectName("hint")
        self.notes_label.setWordWrap(True)
        self.notes_label.setVisible(bool(startup_notes))
        lay.addWidget(self.notes_label)
        # ---- 프로젝트 화면 · 작업대 전환
        self.settings = QSettings(os.path.join(data_root, "settings.ini"), QSettings.Format.IniFormat)
        self.project_view = ProjectView(self.settings, self)
        self.project_view.projectOpened.connect(self.show_workbench)
        self.project_view.before_open = self._before_open
        self.project_view.data_root = data_root
        lay.addWidget(self.project_view, 1)
        self.stack = QStackedWidget()
        self.stack.addWidget(central)
        self.home_page = central
        self.workbench = None
        self.session = None      # app.session.SessionContext — 지금 작업대의 세션(보기 전용이면 None)
        # 세션 고르기: outcome → SessionContext | None. None 이면 세션 선택 화면(모달). 시험은 바꿔 끼운다.
        self.session_chooser = None
        # 위쪽 띠 두 줄(업데이트 필요 · 경로 경고)은 화면 전환과 상관없이 늘 위에 둔다
        root = QWidget()
        rlay = QVBoxLayout(root)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        self.support_banner = SupportBanner(spec, root)
        self.support_banner.update_requested.connect(self._banner_update)
        rlay.addWidget(self.support_banner)
        self.path_banner = QLabel("")
        self.path_banner.setObjectName("pathBanner")
        self.path_banner.setWordWrap(True)
        self.path_banner.setContentsMargins(8, 2, 8, 2)
        self.path_banner.setVisible(False)
        rlay.addWidget(self.path_banner)
        rlay.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.refresh_support_banner()
        self.refresh_path_banner()

        m_file = self.menuBar().addMenu(tr("파일(&F)"))
        self._build_project_menu(m_file)
        self._build_ops_menu(m_file)
        self.a_resume = QAction(tr("시작할 때 마지막 작업 이어서 열기"), self)
        self.a_resume.setCheckable(True)
        self.a_resume.setChecked(self.resume_enabled())
        self.a_resume.toggled.connect(self.set_resume_enabled)
        m_file.addAction(self.a_resume)
        m_file.addSeparator()
        a_quit = QAction(tr("종료(&X)"), self)
        a_quit.setShortcut("Ctrl+Q")
        a_quit.triggered.connect(self.close)
        m_file.addAction(a_quit)
        self.m_view = self.menuBar().addMenu(tr("보기(&V)"))
        self.m_view.menuAction().setVisible(False)
        self._build_session_menus()
        # 니모닉은 &D: Alt+U 는 구간 폼의 '판단 불가' 단축키라 메뉴와 겹치면 둘 다 동작하지 않는다(애매한 단축키)
        m_upd = self.menuBar().addMenu(tr("업데이트(&D)"))
        a_check = QAction(tr("지금 확인(&C)"), self)
        a_check.triggered.connect(lambda: self.manual_check_updates())
        m_upd.addAction(a_check)
        a_manual = QAction(tr("수동 업데이트 안내(&M)"), self)
        a_manual.triggered.connect(lambda: QMessageBox.information(self, tr("수동 업데이트"), tr(MANUAL_GUIDE)))
        m_upd.addAction(a_manual)
        m_help = self.menuBar().addMenu(tr("도움말(&H)"))
        a_report = QAction(tr("문제 보고 복사(&R)"), self)
        a_report.triggered.connect(self.copy_report)
        m_help.addAction(a_report)
        self.a_rollback = QAction(tr("이전 버전으로 되돌리기(&B)…"), self)
        self.a_rollback.triggered.connect(lambda: self.rollback_dialog())
        m_help.addAction(self.a_rollback)
        m_help.aboutToShow.connect(self.update_rollback_action)
        self.m_help = m_help
        self.update_rollback_action()
        a_about = QAction(tr("정보(&A)"), self)
        a_about.triggered.connect(self.about)
        m_help.addAction(a_about)

        # 업데이트 칩은 할 일이 있을 때만 보인다(새 버전·서명 실패·수동 업데이트 필요·지금 확인 중). 404·네트워크 실패나
        # '최신' 은 칩을 숨겨 작업대 상태 문장(예: '구간 경계 추가(B)는 …')이 가려지지 않게 한다
        self.status_chip = QLabel("")
        self.status_chip.setObjectName("statusChip")
        self.status_chip.setVisible(False)
        self.statusBar().addPermanentWidget(self.status_chip)
        self.update_btn = QPushButton("")
        self.update_btn.setObjectName("updateBtn")
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(self.offer_update)
        self.statusBar().addPermanentWidget(self.update_btn)
        self.statusBar().showMessage(tr("준비"))

        if self.auto:
            LOG.info("시험 자동 업데이트 모드 (%s=1): 2초 뒤 확인, 새 버전이면 바로 적용", AUTO_UPDATE_ENV)
            QTimer.singleShot(2_000, lambda: self.check_updates(force=True))
        else:
            QTimer.singleShot(30_000, self._startup_check)   # 04 6-2: 시작 30초 뒤
        if self.auto_rollback and "--updated" in self.flags:
            LOG.info("시험 자동 되돌리기 모드 (%s=1): 2.5초 뒤 메뉴 되돌리기", AUTO_ROLLBACK_ENV)
            QTimer.singleShot(2_500, lambda: self.rollback_dialog(confirm=False))
        if self.auto or self.auto_rollback:
            for flag in sorted(self.flags):
                LOG.info("시작 인자 %s (자동 모드라 대화상자 생략)", flag)
        else:
            QTimer.singleShot(400, self.startup_dialogs)

    def _startup_check(self) -> None:
        """시작 30초 뒤 확인 (on_start: 필수 업데이트·지원 종료면 주기와 상관없이). 시험이 check_updates 를 옛 서명으로
        바꿔 끼웠으면 그 서명으로 부른다."""
        try:
            self.check_updates(force=False, on_start=True)
        except TypeError:
            self.check_updates(force=False)

    def startup_dialogs(self) -> None:
        """시작 알림을 차례로(겹치지 않게): 시작 인자 알림 → 경로 확인(confirm) → 업데이트 실패 뒤 되돌리기 제안 →
        (인자 없이 켠 실행이면) 마지막 작업 이어서 열기."""
        for flag in sorted(self.flags):
            self.notify_startup_flag(flag)
        if not self.confirm_startup_paths():
            QTimer.singleShot(0, self.close)
            return
        if self.maybe_suggest_rollback():
            return                                   # 되돌리기 교체가 시작됨 — 앱이 곧 끝난다
        if self.start_request:
            # main.py --project … [--analysis] [--confirm]: 이어서 열기 대신 그 프로젝트를 연다(D-16 한 번에)
            req, self.start_request = self.start_request, None
            try:
                self.run_start_request(req)
            except Exception:  # noqa: BLE001 — 실패해도 프로젝트 화면은 그대로 쓴다
                LOG.exception("시작 인자로 프로젝트 열기 실패")
            return
        if self.resume_on_start and not self.flags:
            try:
                self.auto_resume()
            except Exception:  # noqa: BLE001 — 이어서 열기가 실패해도 프로젝트 화면은 그대로 쓴다
                LOG.exception("마지막 작업 이어서 열기 실패")

    # ---------------------------------------------------------------- 시작할 때 마지막 작업 이어서 열기
    def resume_enabled(self) -> bool:
        """설정 '시작할 때 마지막 작업 이어서 열기' (기본 켬). ini 에는 'true'·'false' 글자로 남는다."""
        v = self.settings.value(RESUME_KEY, True)
        return str(v).strip().lower() not in ("false", "0", "no", "off")

    def set_resume_enabled(self, on: bool) -> None:
        self.settings.setValue(RESUME_KEY, bool(on))
        self.settings.sync()
        if getattr(self, "a_resume", None) is not None and self.a_resume.isChecked() != bool(on):
            self.a_resume.setChecked(bool(on))

    def auto_resume(self) -> bool:
        """최근 목록 맨 앞(마지막으로 연) 프로젝트를 연다. 세션은 choose_session 이 정한다: 마지막 라벨러의 이어 쓸 라벨링
        세션이 하나뿐이고 프로젝트를 쓸 수 있으면 세션 화면 없이 그 세션, 아니면 지금처럼 세션 화면.
        설정이 꺼졌거나·이미 작업대가 있거나·최근 프로젝트가 없으면(폴더가 사라짐 포함) 아무것도 하지 않는다. 열었으면 True."""
        if not self.resume_enabled() or self.workbench is not None:
            return False
        recent = load_recent(self.settings)
        if not recent:
            return False
        path = recent[0]
        if not os.path.isfile(os.path.join(path, "project.json")):
            LOG.info("마지막 프로젝트를 찾을 수 없어 이어서 열지 않습니다")
            return False
        self._auto_resume, self._resume_note = True, None
        try:
            outcome = self.project_view.open_path(path)
        finally:
            self._auto_resume = False
        if outcome is None or not outcome.ok or self.workbench is None:
            return False
        note = self._resume_note or tr("이어서 엽니다: {0}").format(self._project_name(outcome))
        self.statusBar().showMessage(note, 10_000)
        return True

    @staticmethod
    def _project_name(outcome) -> str:
        proj = outcome.project
        data = proj.data or {}
        return (data.get("broadcast") or {}).get("title") or data.get("slug") \
            or os.path.basename(str(proj.root).rstrip("\\/"))

    def auto_resume_session(self, outcome):
        """이어서 열기에서 세션 화면 없이 고를 세션(SessionContext) 또는 None(→ 세션 화면).

        - 프로젝트가 읽기 전용(잠금 실패·검증 실패)이거나 '업데이트 필요' 로 라벨 저장이 막혔으면 None.
        - 마지막 라벨러(settings)가 이 PC 명부에 있고, 그 라벨러의 이어 쓸 세션이 라벨링 세션 하나뿐일 때만
          (app.session.auto_resume_candidate — 조정·출처 판정·분석 세션은 자동으로 열지 않는다)."""
        from .. import session as sess
        proj = getattr(outcome, "project", None)
        if proj is None or getattr(outcome, "read_only", True) or proj.read_only:
            LOG.info("이어서 열기: 읽기 전용 프로젝트라 세션 화면을 띄웁니다")
            return None
        if updater.label_save_block_reason(self.state_path, __version__):
            return None
        last = self.settings.value(LAST_LABELER_KEY, "")
        if not isinstance(last, str) or not last:
            return None
        try:
            sess.check_labeler_id(last)
            if sess.load_labelers(self.data_root).get(last) is None:
                return None
            mid = sess.machine_id(self.data_root)
            e = sess.auto_resume_candidate(proj.root, proj.data, last, sess.machine4(mid))
            if e is None:
                return None
            ctx = sess.resume_session(e.session_id, machine_id_value=mid)
        except (OSError, ValueError) as ex:
            LOG.info("이어서 열기: 세션을 고르지 못해 세션 화면을 띄웁니다 (%s)", type(ex).__name__)
            return None
        LOG.info("이어서 열기: 세션 %s (세션 화면 없이)", e.session_id)
        self._resume_note = tr("이어서 엽니다: {0} · {1}").format(self._project_name(outcome), e.session_id)
        return ctx

    # ---------------------------------------------------------------- 프로젝트 · 화면 전환
    def _build_project_menu(self, m_file) -> None:
        a_open = QAction(tr("프로젝트 열기(&O)…"), self)
        a_open.setShortcut("Ctrl+O")
        a_open.triggered.connect(self.project_view.choose_and_open)
        m_file.addAction(a_open)
        self.m_recent = m_file.addMenu(tr("최근 프로젝트(&R)"))
        self.m_recent.aboutToShow.connect(self._fill_recent_menu)
        self._fill_recent_menu()
        self.a_close_project = QAction(tr("프로젝트 닫기(&W)"), self)
        self.a_close_project.triggered.connect(self.show_home)
        self.a_close_project.setEnabled(False)
        m_file.addAction(self.a_close_project)
        self.a_export = QAction(tr("라벨 내보내기(&E)…"), self)
        self.a_export.triggered.connect(lambda: self.workbench.choose_and_export() if self.workbench else None)
        self.a_export.setEnabled(False)
        m_file.addAction(self.a_export)
        m_file.addSeparator()

    def _build_ops_menu(self, m_file) -> None:
        """파일 메뉴의 운영 항목 (07 M4): 백업 · 되살리기 · 지우기(휴지통)."""
        self.a_backup = QAction(tr("프로젝트 백업(&B)…"), self)
        self.a_backup.triggered.connect(lambda: self.backup_project_dialog())
        m_file.addAction(self.a_backup)
        self.a_restore = QAction(tr("백업에서 되살리기(&T)…"), self)
        self.a_restore.triggered.connect(lambda: self.restore_backup_dialog())
        m_file.addAction(self.a_restore)
        self.a_dispose = QAction(tr("프로젝트 지우기(휴지통)(&D)…"), self)
        self.a_dispose.triggered.connect(lambda: self.dispose_project_dialog())
        m_file.addAction(self.a_dispose)
        m_file.addSeparator()

    # ---------------------------------------------------------------- 운영: 백업 · 되살리기 · 지우기
    def inform(self, title: str, text: str) -> None:
        """알림 대화상자(시험은 바꿔 끼운다 — 화면 없이 모달이 멈추지 않게)."""
        QMessageBox.information(self, title, text)

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _current_project_root(self) -> str | None:
        proj = getattr(self.workbench, "project", None) if self.workbench is not None else None
        return proj.root if proj is not None else None

    def _choose_project_dir(self, title: str) -> str | None:
        """프로젝트 폴더 고르기(백업·지우기). 프로젝트 열기와 같은 시작 폴더(마지막으로 쓴 폴더 → 기본 위치)."""
        from .project_view import DIR_PROJECT, remember_dir
        d = QFileDialog.getExistingDirectory(self, title, self.project_view.start_folder())
        if not d:
            return None
        d = os.path.normpath(d)
        if not os.path.isfile(os.path.join(d, "project.json")):
            self.warn(title, tr("고른 폴더에 project.json 이 없습니다."))
            return None
        remember_dir(self.settings, DIR_PROJECT, os.path.dirname(d))
        return d

    def _project_title(self, root: str) -> str:
        from .. import backup
        try:
            data = backup.read_project_json(root)
        except Exception:  # noqa: BLE001 — 제목은 보여 주기용
            return os.path.basename(root.rstrip("\\/"))
        return (data.get("broadcast") or {}).get("title") or data.get("slug") or os.path.basename(root.rstrip("\\/"))

    def known_project_roots(self) -> list[str]:
        """업데이트·되돌리기 직전 백업에 넣을 프로젝트: 최근 목록 + <data>\\projects\\* (project.json 이 있는 것)."""
        out: list[str] = []
        seen: set[str] = set()
        cands = list(load_recent(self.settings))
        pdir = os.path.join(self.data_root, "projects")
        try:
            cands += [os.path.join(pdir, n) for n in sorted(os.listdir(pdir))]
        except OSError:
            pass
        for p in cands:
            k = os.path.normcase(os.path.abspath(p))
            if k in seen or not os.path.isfile(os.path.join(p, "project.json")):
                continue
            seen.add(k)
            out.append(p)
        return out

    def data_backup(self, from_ver: str, to_ver: str) -> str:
        """교체·되돌리기 직전 data 백업 (04 6-7). 실패하면 예외 — updater 가 교체를 멈춘다."""
        from .. import backup
        return backup.pre_update_backup(self.known_project_roots(), from_ver, to_ver, data_root=self.data_root)

    def backup_project_dialog(self, *, root: str | None = None, run=None):
        """'파일 → 프로젝트 백업…'. 지금 연 프로젝트(없으면 폴더 고르기). run(dlg) 을 주면 exec() 대신(시험).
        만든 backup.BackupResult 또는 None."""
        from .. import backup
        from ..workers import JobWorker
        from .ops_dialogs import BackupDialog
        root = root or self._current_project_root() or self._choose_project_dir(tr("백업할 프로젝트 폴더"))
        if not root:
            return None
        try:
            pid = backup.project_id_of(root)
            default_dir = backup.default_backup_dir(pid)
        except (backup.BackupError, OSError, ValueError) as e:
            self.warn(tr("프로젝트 백업"), tr("백업할 수 없습니다: {0}").format(e))
            return None
        from .project_view import DIR_BACKUP, last_dir, remember_dir
        dlg = BackupDialog(self, project_title=self._project_title(root), default_dir=default_dir,
                           start_dir=last_dir(self.settings, DIR_BACKUP, default_dir))
        try:
            rc = (run or (lambda d: d.exec()))(dlg)
            if rc != BackupDialog.Accepted:
                return None
            opts = {"include_chat": dlg.include_chat, "include_protected": dlg.include_protected, "out_dir": dlg.out_dir}
        finally:
            dlg.deleteLater()
        if opts["out_dir"]:
            remember_dir(self.settings, DIR_BACKUP, opts["out_dir"])   # [다른 폴더…] 로 고른 곳에서 다음에 연다
        box: dict = {}

        def job(_ctx):
            try:
                box["res"] = backup.backup_project(root, kind="manual", app_version=__version__, **opts)
            except (backup.BackupError, OSError, ValueError) as e:
                box["err"] = str(e) or type(e).__name__
        w = JobWorker.single("backup", job, name="프로젝트 백업", parent=self)
        self.run_job_modal(w, title=tr("프로젝트 백업"), label=tr("백업 zip 을 만드는 중…"))
        w.deleteLater()
        res = box.get("res")
        if res is None:
            self.warn(tr("프로젝트 백업"), tr("백업하지 못했습니다: {0}").format(box.get("err", tr("취소"))))
            return None
        msg = tr("백업을 만들었습니다 (파일 {0}개).\n{1}").format(len(res.manifest.get("files") or []), res.path)
        if res.warnings:
            msg += "\n\n" + "\n".join("⚠ " + tr(x) for x in res.warnings)
        self.inform(tr("프로젝트 백업"), msg)
        return res

    def restore_backup_dialog(self, *, zip_path: str | None = None, dest_parent: str | None = None):
        """'파일 → 백업에서 되살리기…'. 새 폴더로만 푼다(있는 폴더를 덮지 않는다). backup.RestoreResult 또는 None."""
        from .. import backup, paths
        from .project_view import DIR_BACKUP, last_dir, remember_dir
        if zip_path is None:
            start = last_dir(self.settings, DIR_BACKUP, paths.backup_root())
            zip_path, _f = QFileDialog.getOpenFileName(self, tr("되살릴 백업 zip"), start, tr("백업 (*.zip)"))
            if not zip_path:
                return None
            remember_dir(self.settings, DIR_BACKUP, os.path.dirname(os.path.abspath(zip_path)))
        problems = backup.verify_backup(zip_path)
        if problems:
            self.warn(tr("백업에서 되살리기"),
                      tr("이 백업은 검사를 통과하지 못했습니다:\n") + "\n".join("· " + p for p in problems[:8]))
            return None
        if dest_parent is None:
            dest_parent = QFileDialog.getExistingDirectory(self, tr("되살릴 위치(이 아래에 새 폴더를 만듭니다)"),
                                                           self.project_view.start_folder())
            if not dest_parent:
                return None
        try:
            res = backup.restore_backup(zip_path, os.path.normpath(dest_parent))
        except (backup.BackupError, OSError, ValueError) as e:
            self.warn(tr("백업에서 되살리기"), tr("되살리지 못했습니다: {0}").format(e))
            return None
        msg = tr("새 폴더에 되살렸습니다 (파일 {0}개).\n{1}").format(res.files, res.path)
        if res.warnings:
            msg += "\n\n" + "\n".join("⚠ " + tr(x) for x in res.warnings)
        self.inform(tr("백업에서 되살리기"), msg)
        return res

    def _open_lock_is_stale(self, root: str) -> bool:
        """locks/open.lock 이 남아 있지만 아무도 쥐고 있지 않은가(비정상 종료로 남은 파일). QLockFile 로 실제로 잡아 본다."""
        from PySide6.QtCore import QLockFile
        lk = QLockFile(os.path.join(root, "locks", "open.lock"))
        lk.setStaleLockTime(0)
        if lk.tryLock(0):
            lk.unlock()
            return True
        return False

    def _workbench_holds_lock(self) -> bool:
        """지금 작업대가 프로젝트 잠금(locks/open.lock)을 실제로 쥐고 있는가. 라벨 기록(store)이 넘겨받아 쥐었거나,
        세션 없이 보기만 하는 경우 project_view 가 잡은 잠금(outcome.lock)이 아직 작업대에 있을 때."""
        wb = self.workbench
        if wb is None:
            return False
        st = getattr(wb, "store", None)
        if st is not None and getattr(st, "_lock", None) is not None and not getattr(st, "lock_busy", False):
            return True
        lk = getattr(getattr(wb, "outcome", None), "lock", None)
        if lk is None:
            return False
        try:
            return bool(lk.isLocked()) if hasattr(lk, "isLocked") else True
        except Exception:  # noqa: BLE001 — 모르면 쥐지 않은 쪽(검사를 하는 쪽)으로
            return False

    def dispose_project_dialog(self, *, root: str | None = None, run=None, trash=None):
        """'파일 → 프로젝트 지우기(휴지통)…' (05 10-3). 대상 목록 확인 → (지금 연 프로젝트면 작업대를 닫고) 휴지통으로.
        run(dlg) 을 주면 exec() 대신(시험), trash 는 휴지통 함수 바꿔 끼우기(시험). backup.DisposalResult 또는 None."""
        from .. import backup
        from .ops_dialogs import DisposalDialog
        title = tr("프로젝트 지우기 (휴지통)")
        if getattr(self, "busy_jobs", 0) > 0:
            self.statusBar().showMessage(tr("작업이 도는 중입니다 — 끝난 뒤에 하세요"), 5000)
            return None
        cur = self._current_project_root()
        root = root or cur or self._choose_project_dir(tr("지울 프로젝트 폴더"))
        if not root:
            return None
        is_open = cur is not None and os.path.normcase(os.path.abspath(cur)) == os.path.normcase(os.path.abspath(root))
        busy_msg = tr("다른 창이나 PC 가 이 프로젝트를 열고 있습니다. 닫은 뒤 다시 하세요.")
        lock_path = os.path.join(root, "locks", "open.lock")
        # 이 창이 잠금을 실제로 쥔 경우만 잠금 검사를 건너뛴다. 다른 창·PC 가 쥐고 있어 읽기 전용으로 연 경우는
        # 열지 않은 것과 같이 본다 (그 사람이 지금 라벨링 중일 수 있다)
        ignore_lock = is_open and self._workbench_holds_lock()
        if not ignore_lock and os.path.exists(lock_path):
            if not self._open_lock_is_stale(root):
                self.warn(title, busy_msg)
                return None
            ignore_lock = True

        def planner(keep_labels: bool):
            return backup.plan_disposal(root, keep_labels=keep_labels, data_root=self.data_root,
                                        ignore_open_lock=ignore_lock)
        last = self.settings.value("session/last_labeler", "")
        dlg = DisposalDialog(self, planner=planner, project_title=self._project_title(root),
                             labeler_id=last if isinstance(last, str) else "")
        try:
            rc = (run or (lambda d: d.exec()))(dlg)
            if rc != DisposalDialog.Accepted or dlg.plan is None:
                return None
            plan, labeler = dlg.plan, dlg.labeler_id
        finally:
            dlg.deleteLater()
        if is_open:
            self.show_home()                    # 작업대·잠금(open.lock)을 먼저 정리한다
        # 확인하는 동안 다른 창·PC 가 열었을 수 있다: 보내기 직전에 한 번 더 본다
        if os.path.exists(lock_path) and not self._open_lock_is_stale(root):
            self.warn(title, busy_msg)
            return None
        res = backup.execute_disposal(plan, labeler_id=labeler, trash=trash, app_version=__version__)
        if not plan.keep_labels and not os.path.exists(root):
            remove_recent(self.settings, root)
            self._fill_recent_menu()
            self.project_view.refresh()
        msg = tr("휴지통으로 {0}건 보냈습니다.").format(len(res.trashed))
        if res.failed:
            msg += "\n" + tr("보내지 못한 대상 {0}건 (남겨 두었습니다 — 직접 지우세요):").format(len(res.failed))
            msg += "\n" + "\n".join(f"· {t.path} ({why})" for t, why in res.failed[:8])
        (self.inform if res.ok else self.warn)(title, msg)
        return res

    def _build_session_menus(self) -> None:
        """세션(&N): 이 세션 잠그기…·재라벨 계획 만들기… / 보고(&B): 일치도 보고서… (06 7-1, 08 D-11).
        니모닉은 N·B: Alt+S·O·G·J·U 는 폼 단축키라 겹치면 둘 다 동작하지 않는다."""
        self.m_session = self.menuBar().addMenu(tr("세션(&N)"))
        self.a_lock = QAction(tr("이 세션 잠그기(&L)…"), self)
        self.a_lock.triggered.connect(lambda: self.lock_session_dialog())
        self.m_session.addAction(self.a_lock)
        self.a_plan = QAction(tr("재라벨 계획 만들기(&P)…"), self)
        self.a_plan.triggered.connect(lambda: self.relabel_plan_dialog())
        self.m_session.addAction(self.a_plan)
        self.a_cancel_plan = QAction(tr("재라벨 계획 취소(&C)…"), self)
        self.a_cancel_plan.triggered.connect(lambda: self.plan_cancel_dialog())
        self.m_session.addAction(self.a_cancel_plan)
        self._build_analysis_menu()
        self._build_training_menu()
        self.m_report = self.menuBar().addMenu(tr("보고(&B)"))
        self.a_agreement = QAction(tr("일치도 보고서(&A)…"), self)
        self.a_agreement.triggered.connect(lambda: self.agreement_dialog())
        self.m_report.addAction(self.a_agreement)
        self.a_ai_compare = QAction(tr("AI 비교 보고서(&I)…"), self)
        self.a_ai_compare.triggered.connect(lambda: self.ai_compare_dialog())
        self.m_report.addAction(self.a_ai_compare)
        self.m_session.aboutToShow.connect(self.update_session_actions)
        self.m_report.aboutToShow.connect(self.update_session_actions)
        self.m_report.aboutToShow.connect(self.update_analysis_actions)
        self.update_session_actions()

    # ------------------------------------------------------------------ 분석 메뉴 (E, 08 D-01 · D-15 7·8, 06 6장·7장)
    def _build_analysis_menu(self) -> None:
        """분석(&I): AI 결과 가져오기(봉인)… · 봉인 해제… · 엔진과 나란히 보기. 라벨링·재라벨·출처 판정 세션에서는 메뉴가
        꺼지고 data\\ai 를 열지 않는다(06 7-3). 봉인 건수는 메뉴를 펼 때만 읽는다(update_analysis_actions)."""
        self.m_analysis = self.menuBar().addMenu(tr("분석(&I)"))
        self.a_seal_import = QAction(tr("AI 결과 가져오기(봉인)(&I)…"), self)
        self.a_seal_import.triggered.connect(lambda: self.seal_import_dialog())
        self.m_analysis.addAction(self.a_seal_import)
        self.a_unseal = QAction(tr("봉인 해제(&U)…"), self)
        self.a_unseal.triggered.connect(lambda: self.unseal_dialog())
        self.m_analysis.addAction(self.a_unseal)
        self.m_analysis.addSeparator()
        self.a_side_by_side = QAction(tr("엔진과 나란히 보기(&S)"), self)
        self.a_side_by_side.triggered.connect(lambda: self.side_by_side_view())
        self.m_analysis.addAction(self.a_side_by_side)
        self.a_summary = QAction(tr("방송 요약(&B)"), self)
        self.a_summary.triggered.connect(lambda: self.broadcast_summary_view())
        self.m_analysis.addAction(self.a_summary)
        self.m_analysis.aboutToShow.connect(self.update_analysis_actions)
        self.side_view_dialog = None
        self.summary_dialog = None
        # 한 번에 열기(main.py --project … --analysis [--confirm], D-16): 시작 알림 뒤 open_analysis 가 쓴다
        self.start_request: dict | None = None
        self._pending_session = None          # choose_session 이 세션 화면 없이 돌려줄 SessionContext(한 번)
        self._pending_side: dict | None = None  # 작업대 일이 끝나면 열 나란히 보기 {confirm, show}
        self.last_open_block: str | None = None  # open_analysis 가 막은 까닭(시험·상태 줄)

    # ------------------------------------------------------------------ 훈련 메뉴 (F, 08 D-01·D-16·D-18 6·7·8, 18 6-2)
    def _build_training_menu(self) -> None:
        """훈련(&T): 스트리머 훈련. 한 번 누르면 바로 열린다(고르는 창 없음, 18 6-2 '열기'). 꺼지면 메뉴 안에 까닭 한 줄.
        켜기 판단(기준 읽기·시범 잠금)은 메뉴를 펼 때만 하고 프로젝트마다 한 번 계산해 둔다(training_availability)."""
        self.m_training = self.menuBar().addMenu(tr("훈련(&T)"))
        self.m_training.setToolTipsVisible(True)
        self.a_streamer_training = QAction(tr("스트리머 훈련(&S)"), self)
        self.a_streamer_training.setToolTip(tr("소리 끈 다시보기에서 읽고 싶은 채팅을 한 번씩 누르고, 원래 반응과 견줍니다"))
        self.a_streamer_training.triggered.connect(self._on_streamer_training)
        self.m_training.addAction(self.a_streamer_training)
        # 가상 채팅 섞기(H-4, 20 5-2·11-9, D-16): 이 구간의 시나리오가 있으면 기본으로 켜져 '스트리머 훈련' 한 번에 섞인다
        # (끄기 1클릭, 프로젝트마다 기억 — 이 실행 동안만). 없으면 그 자리에 '가상 채팅 만들기' 한 번(엔진 도구를 워커로)
        self.a_mix_virtual = QAction(tr("가상 채팅 섞기(&V)"), self)
        self.a_mix_virtual.setCheckable(True)
        self.a_mix_virtual.setVisible(False)
        self.a_mix_virtual.toggled.connect(self._on_mix_virtual_toggled)
        self.m_training.addAction(self.a_mix_virtual)
        self.a_make_virtual = QAction(tr("가상 채팅 만들기(&M)"), self)
        self.a_make_virtual.setVisible(False)
        self.a_make_virtual.triggered.connect(self._on_make_virtual)
        self.m_training.addAction(self.a_make_virtual)
        self.a_training_reason = QAction("", self)      # 꺼졌을 때 까닭 한 줄(누를 수 없음)
        self.a_training_reason.setEnabled(False)
        self.a_training_reason.setVisible(False)
        self.m_training.addAction(self.a_training_reason)
        # 시청자 훈련(G, 08 D-20 2·5, 21 6-1): 한 번 누르면 바로 열린다. 정답을 보이지 않으므로 기준이 없어도 된다
        self.m_training.addSeparator()
        self.a_viewer_training = QAction(tr("시청자 훈련(&W)"), self)
        self.a_viewer_training.setToolTip(tr(VIEWER_TIP))
        self.a_viewer_training.triggered.connect(self._on_viewer_training)
        self.m_training.addAction(self.a_viewer_training)
        self.a_viewer_reason = QAction("", self)
        self.a_viewer_reason.setEnabled(False)
        self.a_viewer_reason.setVisible(False)
        self.m_training.addAction(self.a_viewer_reason)
        # 가상 채팅 가려내기(H-5, 20 7-1, 25 6-2): 한 번 누르면 바로 열린다(소리 켬 · 가상 표시 숨김 · 맨 위 한 줄).
        # 시범(잠기기 전)·라벨링 세션(1차·재라벨·조정·출처 판정)이면 끄고 scenarios\ 를 열지 않는다(20 1장 4번)
        self.m_training.addSeparator()
        self.a_discern = QAction(tr("가상 채팅 가려내기(&D)"), self)
        self.a_discern.setToolTip(tr(DISCERN_TIP))
        self.a_discern.triggered.connect(self._on_discern)
        self.m_training.addAction(self.a_discern)
        self.a_discern_reason = QAction("", self)
        self.a_discern_reason.setEnabled(False)
        self.a_discern_reason.setVisible(False)
        self.m_training.addAction(self.a_discern_reason)
        self.m_training.aboutToShow.connect(self.update_training_actions)
        self.training_view = None
        self.training_log = None
        self.viewer_view = None
        self.viewer_log = None
        self.discern_view = None
        self.discern_log = None
        self._discern_scn_cache = None    # (프로젝트 폴더, 가려낼 시나리오가 있나) — 보기 전용·분석 세션에서만 계산
        self._pending_kind = "streamer"   # 다시 연 뒤 열 훈련: 'streamer'(F) · 'viewer'(G)
        self.words_runner = None          # 시험: 전사 낱말 만들기 run_words 대신 부를 함수 (cmd, cancelled) -> dict
        self._training_cache = None       # (프로젝트 폴더, reference_set.Availability)
        self._reopen_view_only = False    # 다시 여는 중: choose_session 이 세션 화면 없이 보기 전용(None)을 돌려준다
        self._pending_training = None     # (프로젝트 폴더, show): 다시 연 작업대가 채팅을 읽으면 훈련을 연다
        self._pending_deadline = 0.0
        self._training_labeler_hint = None
        self._scenario_cache = None       # (프로젝트 폴더, 구간, scenario_store.Scenario 또는 None)
        self._mix_off_roots: set[str] = set()   # 사용자가 '가상 채팅 섞기' 를 끈 프로젝트(이 실행 동안)
        self._mix_syncing = False
        self.scenario_tool_args: tuple[str, ...] = ()   # '가상 채팅 만들기' 도구에 더 넘길 인자(스모크의 시험 문턱)
        self.scenario_runner = None       # 시험: run_make 대신 부를 함수 (cmd, cancelled) -> dict

    def _on_streamer_training(self, *_a) -> None:
        self.streamer_training()

    def _on_make_virtual(self, *_a) -> None:
        self.make_virtual_chat()

    def _on_mix_virtual_toggled(self, on: bool) -> None:
        if self._mix_syncing:
            return
        root = self._current_project_root()
        if not root:
            return
        if on:
            self._mix_off_roots.discard(root)
        else:
            self._mix_off_roots.add(root)

    # ---- 가상 채팅 (H-4, 20 5장)
    @staticmethod
    def _training_range(av) -> tuple[int, int] | None:
        """훈련 화면이 쓸 구간(trainer_view.start 와 같은 규칙: 기준의 첫 구간)."""
        ref = getattr(av, "reference", None) if av is not None else None
        rng = getattr(ref, "first_range", None) if ref is not None else None
        if not rng or len(rng) != 2 or int(rng[1]) <= int(rng[0]):
            return None
        return int(rng[0]), int(rng[1])

    def invalidate_scenario_cache(self) -> None:
        self._scenario_cache = None
        self._discern_scn_cache = None

    def training_scenario(self, root: str, av):
        """이 프로젝트·훈련 구간의 가장 새 시나리오(없으면 None). 보기 전용에서만 부른다 — 라벨링·출처 판정 세션은
        scenarios\\ 를 열지 않는다(20 1장 4번, 06 7장). 시범 판단은 scenario_store 가 한다. 프로젝트·구간마다 한 번 읽어 둔다."""
        if self.training_needs_reopen():
            return None
        rng = self._training_range(av)
        if rng is None:
            return None
        c = self._scenario_cache
        if c is not None and c[0] == root and c[1] == rng:
            return c[2]
        from .. import scenario_mix
        scn, counts = scenario_mix.find_scenario(root, rng)
        if scn is not None and scenario_mix.virtual_count(scn) <= 0:
            scn = None
        self._scenario_cache = (root, rng, scn)
        LOG.info("가상 채팅 시나리오: 파일 %s · 맞음 %s · 깨짐 %s · 다른 구간 %s", counts.get("files", 0),
                 counts.get("ok", 0), counts.get("broken", 0), counts.get("other_range", 0))
        return scn

    def _update_virtual_actions(self, why: str | None, av) -> None:
        """'가상 채팅 섞기'·'가상 채팅 만들기' 를 켜고 끈다. 훈련이 막혔거나 라벨링 세션이면 둘 다 숨긴다."""
        from .. import scenario_mix
        scn = None
        show = why is None and not self.training_needs_reopen() and av is not None
        if show:
            scn = self.training_scenario(self._current_project_root() or "", av)
        root = self._current_project_root() or ""
        self._mix_syncing = True
        try:
            self.a_mix_virtual.setVisible(bool(show and scn is not None))
            if scn is not None:
                n = scenario_mix.virtual_count(scn)
                self.a_mix_virtual.setText(tr("가상 채팅 섞기 ({0}줄)(&V)").format(n))
                self.a_mix_virtual.setToolTip(tr("가상 채팅을 '가 … · 가져옴'·'생 … · 생성' 표시와 함께 섞습니다 — "
                                                 "견주기 점수에는 넣지 않습니다"))
                self.a_mix_virtual.setEnabled(True)
                self.a_mix_virtual.setChecked(root not in self._mix_off_roots)
        finally:
            self._mix_syncing = False
        make = bool(show and scn is None)
        self.a_make_virtual.setVisible(make)
        if make:
            ok_tool = scenario_mix.tool_available() or self.scenario_runner is not None
            self.a_make_virtual.setEnabled(ok_tool)
            llm, _prob = self.lmstudio_settings()
            if not ok_tool:
                tip = tr(scenario_mix.REASON_NO_TOOL)
            elif llm.enabled:
                tip = tr(MAKE_TIP_GENERATE).format(llm.model)
            else:
                tip = tr(MAKE_TIP_RETRIEVED)
            self.a_make_virtual.setToolTip(tip)

    def mix_virtual_wanted(self, root: str) -> bool:
        return root not in self._mix_off_roots

    def lmstudio_settings(self):
        """(scenario_mix.LMSettings, 문제 코드 또는 None) — 숨은 설정 settings.ini [lmstudio] url·model·llm_check.
        모델이 비면 생성하지 않는다. 주소가 이 PC 밖이면 'not_local'(생성은 건너뛰고 retrieved 만). 접속하지 않는다."""
        from .. import scenario_mix
        s = getattr(self, "settings", None)

        def get(key, default):
            try:
                v = s.value(key, default) if s is not None else default
            except Exception:  # noqa: BLE001 — 설정을 못 읽으면 기본값(생성 꺼짐)
                v = default
            return v if isinstance(v, (str, bool)) else default
        return scenario_mix.lm_settings_from(get(LMSTUDIO_URL_KEY, ""), get(LMSTUDIO_MODEL_KEY, ""),
                                             get(LMSTUDIO_CHECK_KEY, "1"))

    def make_virtual_chat(self, *, extra_args=None):
        """'훈련 → 가상 채팅 만들기' 한 번(20 11-9, D-24 3): 엔진 도구를 따로 프로세스로, UI 스레드 밖(JobWorker)에서
        돌린다. [lmstudio] model 설정이 있으면 같은 한 번 안에서 LM Studio 확인 → 생성 → 섞기(app.scenario_mix.
        run_virtual_make), 없거나 서버가 꺼져 있으면 retrieved 만(꺼짐이면 상태 줄에 'lmstudio_off' 한 줄). 사람 클릭은
        메뉴 '훈련' + '가상 채팅 만들기' = 2 그대로(고르는 창·입력 칸 없음, D-16). 끝나면 상태 줄에 숫자 한 줄, 다음
        '스트리머 훈련' 에 섞인다(기본 켬). 훈련을 열지는 않는다(노출이 생기지 않게).
        결과 dict(도구 출력 + llm 칸, 숫자·코드만) 또는 None(막힘)."""
        from .. import scenario_mix
        from ..workers import JobWorker
        why, av = self.training_block_reason(compute=True)
        if why is None and self.training_needs_reopen():
            why = tr("보기 전용으로 연 프로젝트에서 만듭니다 — 먼저 '스트리머 훈련' 으로 다시 여세요")
        rng = self._training_range(av) if why is None else None
        if why is None and rng is None:
            why = tr("훈련 구간을 정하지 못했습니다")
        if why:
            self.statusBar().showMessage(why, 6000)
            return None
        root = self.workbench.project.root
        args = tuple(self.scenario_tool_args if extra_args is None else extra_args)
        runner = self.scenario_runner
        cmd = scenario_mix.make_command(root, rng, extra_args=args)
        if cmd is None and runner is None:
            self.statusBar().showMessage(tr(scenario_mix.REASON_NO_TOOL), 6000)
            return None
        llm, prob = self.lmstudio_settings()
        if prob:
            LOG.warning("가상 채팅 만들기: LM Studio 설정 문제 %s", prob)
        box: dict = {}

        def job(ctx):
            box["res"] = scenario_mix.run_virtual_make(root, rng, extra_args=args, llm=llm, runner=runner,
                                                       cancelled=ctx.cancelled)
        w = JobWorker.single("make_scenario", job, name="가상 채팅 만들기", parent=self)
        label = (tr("이 PC 의 LM Studio 를 확인하고 생성 채팅을 만들어 섞는 중… (밖으로 나가는 것 없음)") if llm.enabled
                 else tr("같은 방송의 다른 구간에서 채팅을 고르는 중…"))
        self.run_job_modal(w, title=tr("가상 채팅 만들기"), label=label)
        w.deleteLater()
        res = box.get("res") if w.outcome == "done" else {"error": w.outcome or "failed"}
        self.invalidate_scenario_cache()
        # 상태 줄의 문턱(1분에 실제 채팅 n개 이하)은 도구가 실제로 쓴 값(출력의 fill 칸)을 먼저 본다 —
        # --threshold test·default 프리셋이면 --quiet-max-real 인자가 없다(22 5-2 의 11). 없으면 인자, 그다음 기본 2.
        fill = (res or {}).get("fill") if isinstance((res or {}).get("fill"), dict) else {}
        qmr = fill.get("quiet_max_real")
        if not isinstance(qmr, int) or isinstance(qmr, bool) or qmr < 0:
            qmr = next((int(args[i + 1]) for i, a in enumerate(args[:-1]) if a == "--quiet-max-real"), 2)
        ok, msg = scenario_mix.describe(res or {}, quiet_max_real=qmr)
        if ok:
            self._mix_off_roots.discard(root)          # 만들었으면 섞기를 켠다(기본)
        li = (res or {}).get("llm") if isinstance((res or {}).get("llm"), dict) else {}
        LOG.info("가상 채팅 만들기: rc=%s 결과=%s 가져옴=%s 생성=%s 생성시도=%s 생성코드=%s %sms", (res or {}).get("rc"),
                 "ok" if ok else (res or {}).get("error") or (res or {}).get("reason") or "-",
                 ((res or {}).get("counts") or {}).get("retrieved", 0),
                 ((res or {}).get("counts") or {}).get("generated", 0), bool(li.get("tried")), li.get("code") or "-",
                 (res or {}).get("elapsed_ms", 0))
        self.statusBar().showMessage(tr(msg), 10000)
        return res

    def _training_labeler(self) -> str | None:
        """훈련 기록의 labeler_id: 세션의 라벨러 → 마지막에 고른 라벨러 → project.json 의 created_by. 묻지 않는다(D-16)."""
        from .. import training_store as ts
        wb = self.workbench
        cands = []
        if wb is not None and wb.session is not None:
            cands.append(getattr(wb, "labeler_id", None))
        cands.append(getattr(self, "_training_labeler_hint", None))   # 다시 열기 전 라벨링 세션의 라벨러(D-19 3)
        cands.append(self.settings.value(LAST_LABELER_KEY, ""))
        proj = getattr(wb, "project", None) if wb is not None else None
        cands.append(((proj.data or {}) if proj is not None else {}).get("created_by"))
        for c in cands:
            if isinstance(c, str) and ts.LABELER_ID_RE.match(c):
                return c
        return None

    def training_availability(self, root: str):
        """reference_set.training_availability 를 프로젝트마다 한 번만(무겁다 — 최종 세트를 만든다). 잠그기·계획 취소·
        훈련 열기 뒤에는 다시 계산한다(invalidate_training_cache)."""
        from .. import reference_set
        c = self._training_cache
        if c is not None and c[0] == root:
            return c[1]
        try:
            av = reference_set.training_availability(root)
        except Exception as e:  # noqa: BLE001 — 판단이 안 되면 끈다(블라인드 쪽으로 안전하게)
            LOG.warning("훈련 기준 판단 실패: %s", type(e).__name__)
            av = reference_set.Availability(False, tr("훈련 기준을 읽지 못했습니다 ({0})").format(type(e).__name__))
        self._training_cache = (root, av)
        return av

    def invalidate_training_cache(self) -> None:
        self._training_cache = None

    def training_block_reason(self, *, compute: bool = True) -> tuple[str | None, object]:
        """(막힌 까닭 한 줄 또는 None, Availability 또는 None). compute=False 면 기준을 새로 읽지 않는다(캐시만)."""
        wb = self.workbench
        proj = getattr(wb, "project", None) if wb is not None else None
        if proj is None or proj.data is None:
            return tr("프로젝트를 먼저 여세요"), None
        if self.training_view is not None or getattr(self, "viewer_view", None) is not None \
                or getattr(self, "discern_view", None) is not None:
            return tr("훈련이 이미 열려 있습니다"), None
        if self.jobs_busy():
            return tr("작업이 도는 중입니다 — 끝난 뒤에 쓸 수 있습니다"), None
        # 훈련은 견주기에서 사람 라벨(정답)을 보인다 — 라벨링·출처 판정 세션 위에는 열지 않는다(06 7장, D-18 7).
        # 대신 메뉴 한 번으로 '보기 전용으로 다시 열고 훈련' 한다(D-19 3, training_needs_reopen). 그 전에 막힐 까닭
        # (시범 잠금·기준 없음·영상 없음 등)은 여기서 먼저 걸러, 다시 열었는데 훈련이 안 열리는 일이 없게 한다
        root = proj.root
        av = None
        if self.training_needs_reopen():
            # 라벨링 세션은 다른 세션 라벨·손라벨을 디스크에서 열지 않는다(06 7-3) — 기준(최종 세트·손라벨)은 다시 연 뒤
            # 보기 전용에서 읽는다. 여기서는 폴더 이름만 보고(시범·잠금 파일·손라벨 파일 이름) 막힐 것을 먼저 거른다
            why = self._labeling_training_precheck(root)
            if why:
                return why, None
        else:
            if not compute and (self._training_cache is None or self._training_cache[0] != root):
                return None, None
            av = self.training_availability(root)
            if not av.enabled:
                return tr(av.reason or "라벨이 잠긴 구간이 없습니다"), av
        if not getattr(wb, "media_path", None):
            return tr("영상을 먼저 연결하세요 — 훈련은 소리 끈 다시보기로 합니다"), av
        if "chat" in (getattr(wb, "_jobs", None) or {}) or not getattr(wb.chat_data, "count", 0):
            return tr("채팅을 읽는 중입니다"), av
        if self._training_labeler() is None:
            return tr("라벨러를 한 번 고른 뒤에 열 수 있습니다 (세션 화면)"), av
        return None, av

    @staticmethod
    def _labeling_training_precheck(root: str) -> str | None:
        """라벨링 세션에서 '다시 열고 훈련' 전에 막힐 까닭(없으면 None). 라벨·손라벨 파일을 열지 않는다(06 7-3):
        - 시범 프로젝트(폴더 이름·시범 기록 freeze.json/setup.json)면 끈다 — 잠김 판정(계획·매니페스트 열기)도 하지 않는다.
          시범 세션이 모두 잠긴 뒤에는 보기 전용으로 열어 훈련한다(D-18 7, D-19 3).
        - 잠긴 세션(locks\\<id>.json)도 손라벨 파일(labels\\manual-*.csv) 이름도 없으면 기준이 없다."""
        from .. import reference_set as rs
        try:
            pilot = rs.is_pilot_project(root)
        except Exception:  # noqa: BLE001 — 판단이 안 되면 시범으로 본다(블라인드 쪽으로 안전하게)
            pilot = True
        if pilot:
            return tr("시범 프로젝트는 라벨링 세션에서 훈련을 열지 않습니다 — 시범 세션이 모두 잠긴 뒤 보기 전용으로 "
                      "여세요 (D-18 7)")
        try:
            locks = [n for n in os.listdir(os.path.join(root, "locks"))
                     if n.lower().endswith(".json")]
        except OSError:
            locks = []
        if not locks and not rs.manual_csv_files(root):
            return tr(rs.REASON_NO_REFERENCE)
        return None

    def training_needs_reopen(self) -> bool:
        """지금 작업대가 라벨링(1차·재라벨)·조정·출처 판정 세션인가 — 그러면 훈련은 '보기 전용으로 다시 열고 훈련'(D-19 3).
        조정도 넣는다: 20 1장 4번 '라벨링 세션(1차·재라벨·조정·출처 판정)은 scenarios\\ 를 열지 않는다' 와 가려내기
        메뉴(discern_view.LABELING_KINDS)가 같은 세션 판단을 쓰게(가상 채팅 섞기·만들기·시청자 훈련도 이 함수를 본다)."""
        from ..core import visibility as vis
        wb = self.workbench
        if wb is None or wb.session is None:
            return False
        return (wb.session_kind in vis.LABELING_SESSIONS
                or wb.session_kind in (vis.ADJUDICATION, vis.ORIGIN_REVIEW))

    def update_training_actions(self, *_a) -> None:
        """훈련 메뉴를 펼 때: 켜고 끄고, 꺼졌으면 메뉴 안에 까닭 한 줄. 라벨링 세션이면 풍선에 다시 열기를 알린다."""
        self.update_viewer_action()
        self.update_discern_action()
        why, _av = self.training_block_reason(compute=True)
        self.a_streamer_training.setEnabled(why is None)
        self._update_virtual_actions(why, _av)
        tip = tr("소리 끈 다시보기에서 읽고 싶은 채팅을 한 번씩 누르고, 원래 반응과 견줍니다")
        reopen = why is None and self.training_needs_reopen()
        if reopen:
            tip = tr(TRAINING_REOPEN_TIP)
        self.a_streamer_training.setText(tr("스트리머 훈련 — 보기 전용으로 다시 열기(&S)") if reopen
                                         else tr("스트리머 훈련(&S)"))
        self.a_streamer_training.setToolTip(why or tip)
        self.a_training_reason.setText(why or "")
        self.a_training_reason.setVisible(bool(why))

    # ---- 보기 전용으로 다시 열고 훈련 (D-19 3, 19 5-3 의 1번)
    def reopen_view_only_and_train(self, *, show: bool = True, kind: str = "streamer") -> bool:
        """라벨링·출처 판정 세션에서 '훈련 → 스트리머 훈련' 한 번: 저장 안 한 구간 변경이 있으면 묻고(저장·버리기·취소),
        세션을 닫아 잠금을 풀고, 같은 프로젝트를 세션 화면 없이 보기 전용으로 다시 연 뒤, 채팅·영상 확인이 끝나면 훈련을
        연다(_try_pending_training). 사람 클릭: 저장 안 한 변경이 없으면 0(메뉴 2 뿐), 있으면 묻는 창 1.
        다시 열기를 시작했으면 True(훈련은 조금 뒤 열린다), 취소·실패면 False. 블라인드 규칙은 training_block_reason 이
        먼저 본다(시범은 잠기기 전 금지). kind='viewer' 면 다시 연 뒤 시청자 훈련(G)을 연다(viewer_block_reason 이 먼저 봄)."""
        wb = self.workbench
        if wb is None or not self.training_needs_reopen():
            return False
        self._pending_kind = "viewer" if kind == "viewer" else "streamer"
        # ④ 놓친 후보 폼의 저장 전 입력은 resolve_unsaved(구간표·반응 편집기만 본다)가 묻지 않는다 — 세션을 닫으면
        # 사라지므로 set_mode·prepare_lock 처럼 묻는 창 없이 머문다(D-16)
        from ..core import phase as _ph
        mf = getattr(wb, "missed_form", None)
        if getattr(wb, "mode", None) == _ph.MISSED and mf is not None and mf.has_unsaved():
            self.statusBar().showMessage(tr("④ 저장하지 않은 입력이 있습니다 — 저장(Ctrl+Enter)하거나 취소(Esc)한 뒤에 "
                                            "훈련을 여세요"), 6000)
            return False
        if not wb.resolve_unsaved(allow_cancel=True):        # '취소' 나 반응 편집 중이면 그대로 머문다
            self.statusBar().showMessage(tr("저장하지 않은 변경이 있어 훈련을 열지 않았습니다"), 6000)
            return False
        root = wb.project.root
        hint = getattr(wb, "labeler_id", None)
        from .. import training_store as ts
        LOG.info("훈련: 라벨링 세션을 닫고 보기 전용으로 다시 엽니다")
        self._close_workbench("user")                        # session.close 줄·잠금 풀기(저장 안 한 변경은 위에서 처리)
        # 힌트는 닫은 뒤에 둔다(_close_workbench 가 비운다) — 다시 연 이 프로젝트의 작업대가 열려 있는 동안만 쓰인다
        self._training_labeler_hint = hint if isinstance(hint, str) and ts.LABELER_ID_RE.match(hint) else None
        self._reopen_view_only = True
        try:
            out = self.project_view.open_path(root)
        finally:
            self._reopen_view_only = False
        if out is None or not out.ok or self.workbench is None:
            self._training_labeler_hint = None
            self.show_home()
            self.statusBar().showMessage(tr("보기 전용으로 다시 열지 못했습니다"), 6000)
            return False
        self._pending_training = (root, show)
        self._pending_deadline = datetime.now().timestamp() + TRAINING_REOPEN_WAIT_S
        self.statusBar().showMessage(tr("보기 전용으로 다시 열었습니다 — 채팅을 읽으면 훈련이 열립니다"), 6000)
        QTimer.singleShot(TRAINING_REOPEN_POLL_MS, self._try_pending_training)
        return True

    def _try_pending_training(self):
        """다시 연 작업대가 채팅·영상 확인을 마치면 훈련을 연다. 아직이면 조금 뒤 다시 본다(최대 TRAINING_REOPEN_WAIT_S).
        다른 프로젝트로 바뀌었거나 닫혔으면 그만둔다. 연 TrainerView 또는 None."""
        pend = getattr(self, "_pending_training", None)
        if pend is None:
            return None
        root, show = pend
        wb = self.workbench
        proj = getattr(wb, "project", None) if wb is not None else None
        if proj is None or proj.root != root or wb.session is not None:
            self._pending_training = None
            return None
        waiting = {"chat", "media_check"} & set(wb.busy_job_keys() if hasattr(wb, "busy_job_keys") else ())
        if waiting:
            if datetime.now().timestamp() < self._pending_deadline:
                QTimer.singleShot(TRAINING_REOPEN_POLL_MS, self._try_pending_training)
            else:
                self._pending_training = None
                self.statusBar().showMessage(tr("채팅을 읽는 데 오래 걸려 훈련을 열지 않았습니다 — 다시 눌러 주세요"), 8000)
            return None
        self._pending_training = None
        if getattr(self, "_pending_kind", "streamer") == "viewer":
            self._pending_kind = "streamer"
            return self.viewer_training(show=show)
        return self.streamer_training(show=show)

    @staticmethod
    def _drop_empty_training_log(log) -> None:
        """훈련이 열리지 않았을 때(섞기 막힘 등) TrainingLog.create 가 먼저 만든 0바이트 training\\<id>.jsonl 을 지운다.
        한 줄이라도 쓴 파일은 그대로 둔다(노출 기록). 실패는 경고만."""
        p = getattr(log, "path", None)
        if not isinstance(p, str):
            return
        try:
            if os.path.isfile(p) and os.path.getsize(p) == 0:
                os.remove(p)
        except OSError as e:
            LOG.warning("빈 훈련 기록 지우기 실패: %s", type(e).__name__)

    @staticmethod
    def _fresh_chat_data(cd):
        """작업대의 채팅 배열을 그대로 나눠 쓰되(복사 없음) 흐림 표시(경계 완충 = 구간 라벨에서 옴)만 새로 — 훈련 화면에
        라벨에서 온 표시가 따라오지 않게."""
        from .chat_panel import ChatData
        if not isinstance(cd, ChatData):
            return cd
        new = object.__new__(ChatData)
        for k in ChatData.__slots__:
            setattr(new, k, getattr(cd, k))
        new.dim = bytearray(cd.n)
        return new

    def streamer_training(self, *, show: bool = True):
        """'훈련 → 스트리머 훈련' (08 D-01·D-18 6·7·8, 18 6-2). 한 번 누르면 바로: 기준이 있는 첫 구간 시작에서 소리 끈 영상 ·
        흐르는 채팅 · 타임라인 · '고른 수 n'. 고르는 창·입력 칸 없음. 훈련 기록은 training\\<id>.jsonl(app.training_store),
        start 줄을 쓴 순간이 노출이다(그 뒤 이 프로젝트의 라벨링 세션은 대표값에서 빠진다, app.core.exposure 규칙 5).
        작업대는 그대로 두고(영상은 멈춤) 이 화면을 위에 쌓는다. [닫기] 로 작업대로 돌아간다. TrainerView 또는 None."""
        title = tr("스트리머 훈련")
        why, av = self.training_block_reason(compute=True)
        if why:
            self.statusBar().showMessage(why, 6000)
            return None
        if self.training_needs_reopen():
            # 라벨링·출처 판정 세션 위에는 열지 않는다 — 보기 전용으로 다시 열고 훈련(D-19 3). 훈련은 조금 뒤 열린다
            self.reopen_view_only_and_train(show=show)
            return None
        from .. import training_store as ts
        from .trainer_view import TrainerView
        from .workbench import DENSITY_BUCKET_MS
        wb = self.workbench
        root = wb.project.root
        try:
            log = ts.TrainingLog.create(root, self._training_labeler(), app_version=__version__)
        except ts.TrainingBlockedError as e:
            self.invalidate_training_cache()
            self.statusBar().showMessage(str(e), 6000)
            return None
        except (ts.TrainingStoreError, OSError) as e:
            self.warn(title, tr("훈련 기록을 만들지 못했습니다: {0}").format(
                str(e) if isinstance(e, ts.TrainingStoreError) else type(e).__name__))
            return None
        try:
            wb.player.pause()                       # 작업대 영상은 멈춰 둔다(소리가 겹치지 않게)
        except Exception:  # noqa: BLE001
            pass
        # 떠 있는 '엔진과 나란히 보기'·'방송 요약' 창은 원래 반응·엔진 판정을 보인다 — 훈련 전에 닫는다(블라인드).
        # 닫기만 하고 묻지 않는다(사람 클릭 0). 훈련 중에는 분석 메뉴가 꺼진다(analysis_block_reasons)
        self._close_analysis_windows()
        fn = getattr(wb.chat, "chat_data", None)
        chat = self._fresh_chat_data(fn()) if callable(fn) else list(wb.chat_data.records)
        # 가상 채팅 섞기(H-4): 이 구간의 시나리오가 있고 끄지 않았으면 섞는다. 섞기에는 실제 채팅 줄(dict)이 필요해
        # messages.jsonl 을 한 번 더 읽는다(읽기만) — retrieved 본문(같은 방송의 구간 밖)도 여기서 찾는다
        scenario = None
        scn = self.training_scenario(root, av) if self.mix_virtual_wanted(root) else None
        if scn is not None:
            from .. import scenario_mix
            try:
                real = scenario_mix.load_messages(root)
            except OSError as e:
                LOG.warning("가상 채팅 섞기: 채팅 읽기 실패 %s", type(e).__name__)
                real = []
            if real:
                chat = real
                scenario = {"records": scn.records, "scenario_id": scn.scenario_id, "scenario_sha256": scn.sha256,
                            "resolve": scenario_mix.make_resolver(root, wb.project.project_id, real)}
        # 처음부터 이 창의 자식으로 만든다(작업대와 같은 길) — 최상위 창으로 만들었다가 옮기면 mpv 창 핸들(wid)이 따라오지
        # 않아 영상이 검게 남는다(10 5-3 의 8 '플레이어는 옮기지 않음')
        view = TrainerView(self)
        self.stack.addWidget(view)
        if show:
            self.stack.setCurrentWidget(view)
        ok = view.start(chat=chat, reference=av.reference, media_path=wb.media_path, timemap=wb.timemap,
                        viewer_no=dict(wb.chat_data.viewer_no or {}), duration_ms=int(getattr(wb, "_vod_duration", 0)),
                        density=list(wb.chat_data.density or []), density_bucket_ms=DENSITY_BUCKET_MS,
                        project_name=os.path.basename(root), pilot_locked=bool(av.pilot and av.enabled),
                        log=log, project_id=wb.project.project_id, scenario=scenario)
        self.invalidate_training_cache()            # start 줄이 생겼으면 노출이 바뀌었다(다음 판단은 새로)
        if not ok:
            reason = view.blocked_label.text()
            self._drop_empty_training_log(log)      # start 줄을 못 쓴 빈 기록 파일(노출 아님)은 남기지 않는다
            view.close_view()
            self.stack.removeWidget(view)
            view.deleteLater()
            if wb is not None:
                self.stack.setCurrentWidget(wb)
            self.statusBar().showMessage(reason, 6000)
            return None
        view.closeRequested.connect(self.close_training)
        view.messageShown.connect(self._on_training_message)
        self.training_view, self.training_log = view, log
        self._apply_analysis_reasons(self.analysis_block_reasons(read_seal=False))   # 분석·AI 보고서 끔
        self.update_session_actions()               # 일치도 보고서도 끔(D-19 4)
        if hasattr(wb, "detach_status_bar"):
            wb.detach_status_bar()          # 작업대의 VOD·파일 위치 칩이 훈련 화면 아래에 남아 헷갈리지 않게(닫을 때 다시 붙임)
        if show:
            view.setFocus()
        LOG.info("스트리머 훈련 열기: 기준 %s %d건 · 가상 채팅 %d줄", av.reference.source if av.reference else "-",
                 av.reference.count if av.reference else 0, view.virtual_line_count())
        return view

    def _on_training_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 6000)

    def close_training(self, *_a) -> None:
        """훈련 화면을 닫는다(training.end 를 쓰고 플레이어를 닫음). 작업대로 돌아간다. 여러 번 불러도 된다."""
        view = self.training_view
        if view is None:
            return
        self.training_view, self.training_log = None, None
        try:
            view.close_view()
        except Exception:  # noqa: BLE001 — 닫는 길은 막지 않는다
            LOG.exception("훈련 화면 닫기 실패")
        self.stack.removeWidget(view)
        view.deleteLater()
        wb = self.workbench
        if wb is not None:
            if hasattr(wb, "attach_status_bar"):
                wb.attach_status_bar(self.statusBar())
            self.stack.setCurrentWidget(wb)
            wb.setFocus()
        self.invalidate_training_cache()
        # 꺼진 메뉴 막대 항목은 펼쳐지지 않아 aboutToShow 로 다시 켜지지 않는다 — 여기서 다시 판단한다(일치도 보고서도)
        self._apply_analysis_reasons(self.analysis_block_reasons(read_seal=False))
        self.update_session_actions()

    # ---- 시청자 훈련 (G, 08 D-20 2·3·4·5, 21 6-1~6-3)
    def _on_viewer_training(self, *_a) -> None:
        self.viewer_training()

    def viewer_block_reason(self) -> tuple[str | None, bool]:
        """(막힌 까닭 한 줄 또는 None, 시범인데 다 잠겨 열 수 있음). 정답을 보이지 않으므로 기준(손라벨·최종 세트)은
        보지 않는다(21 6-3). 시범 프로젝트는 잠기기 전 끈다(D-20 5) — 라벨링 세션이면 잠김 판정도 하지 않고 끈다."""
        from .. import viewer_flow as vf
        from .viewer_train_view import viewer_menu_state
        wb = self.workbench
        proj = getattr(wb, "project", None) if wb is not None else None
        if proj is None or proj.data is None:
            return tr("프로젝트를 먼저 여세요"), False
        if self.training_view is not None or self.viewer_view is not None \
                or getattr(self, "discern_view", None) is not None:
            return tr("훈련이 이미 열려 있습니다"), False
        if self.jobs_busy():
            return tr("작업이 도는 중입니다 — 끝난 뒤에 쓸 수 있습니다"), False
        root = proj.root
        why, pilot_ok = vf.block_reason(root, labeling=self.training_needs_reopen())
        if why:
            return tr(why), False
        ok, why = viewer_menu_state(os.path.basename(root), pilot_locked=pilot_ok,
                                    has_media=bool(getattr(wb, "media_path", None)))
        if not ok:
            return why, False
        if "chat" in (getattr(wb, "_jobs", None) or {}) or not getattr(wb.chat_data, "count", 0):
            return tr("채팅을 읽는 중입니다"), False
        if self._training_labeler() is None:
            return tr("라벨러를 한 번 고른 뒤에 열 수 있습니다 (세션 화면)"), False
        return None, pilot_ok

    def update_viewer_action(self) -> None:
        """'시청자 훈련' 을 켜고 끈다. 꺼졌으면 메뉴 안에 까닭 한 줄. 라벨링 세션이면 '보기 전용으로 다시 열기'."""
        why, _ok = self.viewer_block_reason()
        self.a_viewer_training.setEnabled(why is None)
        reopen = why is None and self.training_needs_reopen()
        self.a_viewer_training.setText(tr("시청자 훈련 — 보기 전용으로 다시 열기(&W)") if reopen else tr("시청자 훈련(&W)"))
        self.a_viewer_training.setToolTip(why or tr(VIEWER_REOPEN_TIP if reopen else VIEWER_TIP))
        self.a_viewer_reason.setText(tr("시청자 훈련: {0}").format(why) if why else "")
        self.a_viewer_reason.setVisible(bool(why))

    def ensure_transcript_words(self, root: str, project_data) -> tuple[str, dict | None]:
        """전사 낱말 파일(derived\\asr-words.json)이 없거나 오래됐으면 엔진 도구를 따로 프로세스로, UI 스레드 밖(JobWorker)
        에서 만든다(진행 창·취소, 사람 클릭 0). 결과 (상태 코드, 쓸 수 있는 문서 또는 None). 못 만들어도 훈련은 연다
        (주제 적합성은 주제 흐름만으로, 없으면 '맥락 없음')."""
        from .. import viewer_flow as vf
        from ..workers import JobWorker
        code, doc = vf.words_status(root, project_data)
        if code not in vf.REBUILD:
            return code, doc
        runner = self.words_runner
        cmd = vf.words_command(root)
        if cmd is None and runner is None:
            LOG.info("시청자 훈련 전사 낱말: %s · 만들기 도구 없음", code)
            return code, None
        box: dict = {}

        def job(ctx):
            fn = runner or (lambda c, cancelled: vf.run_words(c, cancelled=cancelled))
            box["res"] = fn(cmd, ctx.cancelled)
        w = JobWorker.single("transcript_words", job, name="전사 낱말 만들기", parent=self)
        self.run_job_modal(w, title=tr("시청자 훈련"), label=tr("전사문 낱말을 채팅 시각(VOD)에 맞추는 중…"))
        w.deleteLater()
        res = box.get("res") if w.outcome == "done" else {"error": w.outcome or "failed"}
        res = res if isinstance(res, dict) else {"error": "no_output"}
        code2, doc2 = vf.words_status(root, project_data)
        LOG.info("시청자 훈련 전사 낱말 만들기: 전 %s → 뒤 %s · rc=%s 오류=%s 창 %s · 낱말 %s · %sms", code, code2,
                 res.get("rc"), res.get("error") or "-", res.get("windows", 0), res.get("words", 0),
                 res.get("elapsed_ms", 0))
        return code2, doc2

    def viewer_training(self, *, show: bool = True):
        """'훈련 → 시청자 훈련' (08 D-20 2, 21 6-1). 한 번 누르면 바로: 소리 켠 다시보기 · 흐르는 채팅(이 구간의 시나리오가
        있으면 가상 채팅도 표시와 함께) · 쓰기 칸 하나. 구간은 시나리오 구간 → 훈련 기준의 첫 구간 → 첫 채팅부터 10분.
        채점 맥락은 해제된 주제 흐름과 VOD 로 보정한 전사 낱말(없으면 열 때 워커로 만든다). 기록은 viewer\\<id>.jsonl
        (보호 구역, app.viewer_store). 정답을 보이지 않아 노출로 세지 않는다(training\\ 에 쓰지 않음, D-20 5).
        라벨링 세션이면 보기 전용으로 다시 연 뒤 연다(F 와 같은 길). ViewerTrainView 또는 None."""
        title = tr("시청자 훈련")
        why, pilot_ok = self.viewer_block_reason()
        if why:
            self.statusBar().showMessage(why, 6000)
            return None
        if self.training_needs_reopen():
            self.reopen_view_only_and_train(show=show, kind="viewer")
            return None
        from .. import scenario_mix
        from .. import viewer_flow as vf
        from .. import viewer_store as vs
        from .viewer_train_view import ViewerTrainView, load_viewer_scenario
        from .workbench import DENSITY_BUCKET_MS
        wb = self.workbench
        proj = wb.project
        root, pid = proj.root, proj.project_id
        # 전사 낱말(없거나 오래됐으면 지금 만든다)
        w_code, w_doc = self.ensure_transcript_words(root, proj.data)
        # 구간: 시나리오(가상 채팅이 있는 가장 새 것) → 훈련 기준의 첫 구간 → 첫 채팅부터 10분
        scn, _counts = scenario_mix.find_scenario(root, None)
        scn_rng = scn.range_ms if scn is not None and scenario_mix.virtual_count(scn) > 0 else None
        ref_rng = None
        if scn_rng is None:
            av = self.training_availability(root)
            ref_rng = self._training_range(av) if av.enabled else None
        fn = getattr(wb.chat, "chat_data", None)
        cd = self._fresh_chat_data(fn()) if callable(fn) else None
        if cd is not None and getattr(cd, "n", 0):
            first_chat = int(cd.times[0])
        else:
            first_chat = min((int(r["t_ms"]) for r in (wb.chat_data.records or ())
                              if isinstance(r, dict) and isinstance(r.get("t_ms"), int)), default=None)
        dur = int(getattr(wb, "_vod_duration", 0) or 0)
        seg, seg_src = vf.choose_segment(scenario_range=scn_rng, reference_range=ref_rng, first_chat_ms=first_chat,
                                         duration_ms=dur or None)
        if seg is None:
            self.statusBar().showMessage(tr("훈련할 구간이 없습니다"), 6000)
            return None
        chat = cd if cd is not None else list(wb.chat_data.records)
        scenario = None
        if seg_src == vf.SEG_SCENARIO:
            real, scenario = load_viewer_scenario(root, pid, seg)
            if scenario is not None:
                chat = real
        ctx, info = vf.build_context(root, proj.data, words_doc=w_doc)
        info["words"] = w_code
        try:
            log = vs.ViewerLog.create(root, self._training_labeler(), app_version=__version__)
        except vs.ViewerBlockedError as e:
            self.statusBar().showMessage(str(e), 6000)
            return None
        except (vs.ViewerStoreError, OSError) as e:
            self.warn(title, tr("시청자 훈련 기록을 만들지 못했습니다: {0}").format(
                str(e) if isinstance(e, vs.ViewerStoreError) else type(e).__name__))
            return None
        try:
            wb.player.pause()                       # 작업대 영상은 멈춰 둔다(소리가 겹치지 않게)
        except Exception:  # noqa: BLE001
            pass
        self._close_analysis_windows()
        view = ViewerTrainView(self)                # 처음부터 이 창의 자식으로(mpv 창 핸들, 10 5-3 의 8)
        self.stack.addWidget(view)
        if show:
            self.stack.setCurrentWidget(view)
        ok = view.start(chat=chat, segment=seg, media_path=wb.media_path, timemap=wb.timemap,
                        viewer_no=dict(wb.chat_data.viewer_no or {}), duration_ms=dur or None,
                        density=list(wb.chat_data.density or []), density_bucket_ms=DENSITY_BUCKET_MS,
                        project_name=os.path.basename(root), pilot_locked=pilot_ok, log=log, project_id=pid,
                        scenario=scenario, context=ctx)
        if not ok:
            reason = view.blocked_label.text()
            self._drop_empty_training_log(log)      # start 줄을 못 쓴 빈 viewer\<id>.jsonl 은 남기지 않는다
            view.close_view()
            self.stack.removeWidget(view)
            view.deleteLater()
            self.stack.setCurrentWidget(wb)
            self.statusBar().showMessage(reason, 6000)
            return None
        view.closeRequested.connect(self.close_viewer_training)
        view.messageShown.connect(self._on_training_message)
        self.viewer_view, self.viewer_log = view, log
        self._apply_analysis_reasons(self.analysis_block_reasons(read_seal=False))
        self.update_session_actions()
        if hasattr(wb, "detach_status_bar"):
            wb.detach_status_bar()
        if show:
            view.input.setFocus()
        note = vf.words_note(w_code, info.get("sealed_count"))
        if note:                                    # 보정 전·보정 없음·시범 봉인됨 n건: 전사 낱말을 쓰지 않았다는 것을 한 줄로(숫자·코드만)
            self.statusBar().showMessage(tr(note), 8000)
        LOG.info("시청자 훈련 열기: 구간 %s %d ms · 가상 채팅 %d줄 · 주제 덩어리 %d(%s) · 전사 창 %d(%s, 보정 %s) · "
                 "벡터 창 %d(%s) · 동의어 %s · 문턱 %s", seg_src,
                 seg[1] - seg[0], view.virtual_line_count(), info["topic_blocks"], info.get("topic_source", "-"),
                 info["transcript_windows"], info["words"], info["calibration_status"],
                 info.get("vector_windows", 0), info.get("vectors", "-"), info.get("synonyms", "-"),
                 info.get("settings", "-"))
        return view

    def close_viewer_training(self, *_a) -> None:
        """시청자 훈련 화면을 닫는다(요약 전이면 viewer.end closed). 작업대로 돌아간다. 여러 번 불러도 된다."""
        view = getattr(self, "viewer_view", None)
        if view is None:
            return
        self.viewer_view, self.viewer_log = None, None
        try:
            view.close_view()
        except Exception:  # noqa: BLE001 — 닫는 길은 막지 않는다
            LOG.exception("시청자 훈련 화면 닫기 실패")
        self.stack.removeWidget(view)
        view.deleteLater()
        wb = self.workbench
        if wb is not None:
            if hasattr(wb, "attach_status_bar"):
                wb.attach_status_bar(self.statusBar())
            self.stack.setCurrentWidget(wb)
            wb.setFocus()
        self._apply_analysis_reasons(self.analysis_block_reasons(read_seal=False))
        self.update_session_actions()

    # ---- 가상 채팅 가려내기 (H-5, 20 7장, 25 6-2, 08 D-24 7 가)
    def _on_discern(self, *_a) -> None:
        self.discern_training()

    def _discern_has_scenario(self, root: str) -> bool:
        """가려낼 시나리오(가상 줄 1개 이상)가 있나 — 프로젝트마다 한 번 읽어 둔다(만들기·닫기 뒤 다시). 시범·라벨링 판단을
        먼저 지난 뒤에만 부른다(scenarios\\ 를 여는 곳은 여기와 prepare_discern 뿐)."""
        c = self._discern_scn_cache
        if c is not None and c[0] == root:
            return c[1]
        from .. import scenario_mix
        try:
            scn, _counts = scenario_mix.find_scenario(root, None)
            ok = scn is not None and scenario_mix.virtual_count(scn) > 0
        except Exception as e:  # noqa: BLE001 — 못 읽으면 끈다(까닭은 로그에 이름만)
            LOG.warning("가려내기 시나리오 확인 실패: %s", type(e).__name__)
            ok = False
        self._discern_scn_cache = (root, ok)
        return ok

    def discern_block_reason(self) -> tuple[str | None, bool]:
        """(막힌 까닭 한 줄 또는 None, 시범인데 다 잠겨 열 수 있음). 순서는 discern_view.discern_menu_state 와 같다:
        시범(잠기기 전) → 라벨링 세션(1차·재라벨·조정·출처 판정) → 영상 → 시나리오. 앞 둘에 걸리면 scenarios\\ 를 열지
        않는다(20 1장 4번). 라벨링 세션이면 시범 잠김 판정(계획·매니페스트 열기)도 하지 않는다(06 7-3)."""
        from .. import viewer_flow as vf
        from . import discern_view as dv
        wb = self.workbench
        proj = getattr(wb, "project", None) if wb is not None else None
        if proj is None or proj.data is None:
            return tr("프로젝트를 먼저 여세요"), False
        if self.training_view is not None or self.viewer_view is not None or self.discern_view is not None:
            return tr("훈련이 이미 열려 있습니다"), False
        if self.jobs_busy():
            return tr("작업이 도는 중입니다 — 끝난 뒤에 쓸 수 있습니다"), False
        root = proj.root
        kind = wb.session_kind if wb.session is not None else None
        labeling = kind in dv.LABELING_KINDS
        why, pilot_ok = vf.block_reason(root, labeling=labeling)
        if why:
            # 시청자 훈련 문구 대신 가려내기 문구(시범 잠기기 전)
            return (tr(dv.REASON_PILOT) if labeling else tr(why)), False
        ok, why = dv.discern_menu_state(os.path.basename(root), session_kind=kind,
                                        has_media=bool(getattr(wb, "media_path", None)), has_scenario=True,
                                        pilot_locked=pilot_ok)
        if not ok:
            return why, False
        if not self._discern_has_scenario(root):
            return tr("가려낼 가상 채팅이 없습니다 — 먼저 '가상 채팅 만들기' 로 만드세요"), False
        if "chat" in (getattr(wb, "_jobs", None) or {}) or not getattr(wb.chat_data, "count", 0):
            return tr("채팅을 읽는 중입니다"), False
        if self._training_labeler() is None:
            return tr("라벨러를 한 번 고른 뒤에 열 수 있습니다 (세션 화면)"), False
        return None, pilot_ok

    def update_discern_action(self) -> None:
        """'가상 채팅 가려내기' 를 켜고 끈다. 꺼졌으면 메뉴 안에 까닭 한 줄."""
        why, _ok = self.discern_block_reason()
        self.a_discern.setEnabled(why is None)
        self.a_discern.setToolTip(why or tr(DISCERN_TIP))
        self.a_discern_reason.setText(tr("가려내기: {0}").format(why) if why else "")
        self.a_discern_reason.setVisible(bool(why))

    def discern_training(self, *, show: bool = True):
        """'훈련 → 가상 채팅 가려내기' (H-5, 20 7-1, 25 6-2). 한 번 누르면 바로: 가장 새 시나리오의 구간을 소리 켜고 튼다 ·
        가상 표시('가'·'생'·'가져옴·생성')를 숨긴다 · 맨 위에 '이 화면에는 생성 채팅이 섞여 있습니다' 한 줄 · 채팅마다
        '진짜'·'가짜'·'모름' 단추. 구간 끝에 정답과 숫자 줄(app.core.discern.evaluate → origin.compare_origins). 고르는 창·
        입력 칸 없음. 기록은 discern\\<id>.jsonl(app.discern_store, 보호 구역 — ID·판정·숫자만). 노출로 세지 않는다
        (training\\ 에 쓰지 않음, 원래 반응·사람 라벨·엔진 판정을 보이지 않음). DiscernView 또는 None."""
        title = tr("가상 채팅 가려내기")
        why, pilot_ok = self.discern_block_reason()
        if why:
            self.statusBar().showMessage(why, 6000)
            return None
        from .. import discern_store as ds
        from .discern_view import DiscernView, prepare_discern
        wb = self.workbench
        proj = wb.project
        root, pid = proj.root, proj.project_id
        kind = wb.session_kind if wb.session is not None else None
        labeler = self._training_labeler()
        prep = prepare_discern(root, pid, labeler_id=labeler, session_kind=kind,
                               has_media=bool(getattr(wb, "media_path", None)), pilot_locked=pilot_ok)
        if not prep.ok:
            self._discern_scn_cache = None
            self.statusBar().showMessage(prep.reason, 6000)
            return None
        try:
            log = ds.DiscernLog.create(root, labeler, app_version=__version__)
        except ds.DiscernBlockedError as e:
            self.statusBar().showMessage(str(e), 6000)
            return None
        except (ds.DiscernStoreError, OSError) as e:
            self.warn(title, tr("가려내기 기록을 만들지 못했습니다: {0}").format(
                str(e) if isinstance(e, ds.DiscernStoreError) else type(e).__name__))
            return None
        try:
            wb.player.pause()                       # 작업대 영상은 멈춰 둔다(소리가 겹치지 않게)
        except Exception:  # noqa: BLE001
            pass
        self._close_analysis_windows()
        view = DiscernView(self)                    # 처음부터 이 창의 자식으로(mpv 창 핸들, 10 5-3 의 8)
        self.stack.addWidget(view)
        if show:
            self.stack.setCurrentWidget(view)
        dur = int(getattr(wb, "_vod_duration", 0) or 0)
        ok = view.start(**prep.kwargs, media_path=wb.media_path, timemap=wb.timemap,
                        viewer_no=dict(wb.chat_data.viewer_no or {}), duration_ms=dur or None, log=log)
        if not ok:
            reason = view.blocked_label.text()
            self._drop_empty_training_log(log)      # start 줄을 못 쓴 빈 discern\<id>.jsonl 은 남기지 않는다
            view.close_view()
            self.stack.removeWidget(view)
            view.deleteLater()
            self.stack.setCurrentWidget(wb)
            self.statusBar().showMessage(reason, 6000)
            return None
        view.closeRequested.connect(self.close_discern)
        view.messageShown.connect(self._on_training_message)
        self.discern_view, self.discern_log = view, log
        self._apply_analysis_reasons(self.analysis_block_reasons(read_seal=False))
        self.update_session_actions()
        if hasattr(wb, "detach_status_bar"):
            wb.detach_status_bar()
        if show:
            view.setFocus()
        seg = prep.kwargs.get("segment") or (0, 0)
        tg = view.targets()
        LOG.info("가려내기 열기: 구간 %d ms · 대상 %d (실제 %d · 가져옴 %d · 생성 %d) · 본 적 있음 %s · 다시 봄 %s",
                 int(seg[1]) - int(seg[0]), len(tg), sum(1 for t in tg.values() if t.source == "replay"),
                 sum(1 for t in tg.values() if t.source == "retrieved"),
                 sum(1 for t in tg.values() if t.source == "generated"), prep.kwargs.get("seen_before"),
                 prep.kwargs.get("repeat"))
        return view

    def close_discern(self, *_a) -> None:
        """가려내기 화면을 닫는다(정답 전이면 discern.end closed). 작업대로 돌아간다. 여러 번 불러도 된다."""
        view = getattr(self, "discern_view", None)
        if view is None:
            return
        self.discern_view, self.discern_log = None, None
        try:
            view.close_view()
        except Exception:  # noqa: BLE001 — 닫는 길은 막지 않는다
            LOG.exception("가려내기 화면 닫기 실패")
        self.stack.removeWidget(view)
        view.deleteLater()
        wb = self.workbench
        if wb is not None:
            if hasattr(wb, "attach_status_bar"):
                wb.attach_status_bar(self.statusBar())
            self.stack.setCurrentWidget(wb)
            wb.setFocus()
        self._apply_analysis_reasons(self.analysis_block_reasons(read_seal=False))
        self.update_session_actions()

    def _close_analysis_windows(self) -> None:
        """'엔진과 나란히 보기'·'방송 요약' 창을 닫는다(훈련을 열 때). 여러 번 불러도 된다."""
        for attr in ("side_view_dialog", "summary_dialog"):
            dlg = getattr(self, attr, None)
            if dlg is None:
                continue
            setattr(self, attr, None)
            try:
                dlg.close()
                dlg.deleteLater()
            except RuntimeError:          # 이미 지워진 창
                pass

    def _reader_kind(self) -> str | None:
        wb = self.workbench
        return wb.session_kind if wb is not None and wb.session is not None else None

    def _ai_project_root(self) -> str | None:
        wb = self.workbench
        proj = getattr(wb, "project", None) if wb is not None else None
        return proj.root if proj is not None and proj.data is not None else None

    def analysis_block_reasons(self, *, read_seal: bool = True) -> dict[str, str | None]:
        """분석·AI 보고서 동작마다 막힌 까닭(None = 됨). read_seal=False 면 봉인 건수를 읽지 않는다(세션 메뉴 갱신 때).
        라벨링 세션은 read_seal 과 상관없이 data\\ai 를 열지 않는다."""
        from .. import ai_compare
        from .. import analysis_flow as af
        root, kind = self._ai_project_root(), self._reader_kind()
        out: dict[str, str | None] = {}
        keys = ("menu", "seal", "unseal", "view", "report", "summary")
        if root is None:
            why = tr("프로젝트를 먼저 여세요")
            return {k: why for k in keys}
        if self.jobs_busy():
            why = tr("작업이 도는 중입니다 — 끝난 뒤에 쓸 수 있습니다")
            return {k: why for k in keys}
        if getattr(self, "training_view", None) is not None or getattr(self, "viewer_view", None) is not None \
                or getattr(self, "discern_view", None) is not None:
            # 훈련 중에는 원래 반응·엔진 판정·사람 최종 세트를 보이지 않는다(훈련 점수 블라인드, D-18 6·8).
            # 시청자 훈련(G)도 같이 끈다 — 첫 판은 정답을 보이지 않아 노출로 세지 않는데(D-20 5), 그 전제를 지키려고
            # 훈련을 열 때 나란히 보기·방송 요약 창도 닫는다(_close_analysis_windows). 가려내기(H-5)도 같다(노출 아님)
            why = tr("훈련 중에는 쓰지 않습니다 — 훈련을 닫은 뒤에 쓸 수 있습니다")
            return {k: why for k in keys}
        menu = af.menu_block_reason(kind)
        out["menu"] = tr(menu) if menu else None
        seal_why = af.seal_block_reason(kind)
        out["seal"] = tr(seal_why) if seal_why else None
        out["unseal"] = out["seal"]
        rep = ai_compare.reader_block_reason(kind)
        out["report"] = tr(rep) if rep else None
        view = af.view_block_reason(kind)
        out["view"] = tr(view) if view else None
        if read_seal and menu is None:
            try:
                sealed, runs = af.seal_counts(root, reader_kind=kind)
            except Exception as e:  # noqa: BLE001 — 봉인 폴더를 못 읽어도 메뉴는 산다(까닭만)
                why = tr("AI 결과 봉인 기록을 읽지 못했습니다: {0}").format(type(e).__name__)
                sealed, runs = None, None
                for k in ("unseal", "view", "report"):
                    out[k] = out[k] or why
            if sealed is not None:
                if out["unseal"] is None and sealed == 0:
                    out["unseal"] = tr("봉인된 AI 결과가 없습니다 — 먼저 가져오세요")
                if out["view"] is None and not runs:
                    out["view"] = tr("봉인됨 {0}건 — 봉인을 푼 뒤에 열립니다 (D-15 7)").format(sealed)
                if out["report"] is None and not runs:
                    out["report"] = tr("봉인을 푼 AI 결과가 없습니다 (봉인됨 {0}건)").format(sealed)
        # 방송 요약(D-01, D-17 5): 나란히 보기와 같은 자리·같은 조건(분석 세션, 해제 뒤)
        out["summary"] = out["view"]
        return out

    def update_analysis_actions(self, *_a) -> None:
        """분석 메뉴를 펼 때: 봉인 건수를 읽어 동작을 켜고 끈다(seal.jsonl 만, 라벨링 세션이면 읽지 않음)."""
        r = self.analysis_block_reasons(read_seal=True)
        self._apply_analysis_reasons(r)

    def _apply_analysis_reasons(self, r: dict) -> None:
        self.m_analysis.setEnabled(r["menu"] is None)
        self.m_analysis.menuAction().setToolTip(r["menu"] or "")
        for a, k in ((self.a_seal_import, "seal"), (self.a_unseal, "unseal"), (self.a_side_by_side, "view"),
                     (self.a_ai_compare, "report"), (self.a_summary, "summary")):
            why = r.get(k, r.get("view"))
            a.setEnabled(why is None)
            a.setToolTip(why or "")

    def _actor_id(self, by: str | None, title: str) -> str | None:
        """봉인·해제 기록의 by(라벨러 ID). 세션이 있으면 그 라벨러, 없으면 마지막 라벨러를 기본값으로 묻는다."""
        from .. import seal
        if by:
            return by
        wb = self.workbench
        if wb is not None and wb.session is not None and wb.labeler_id:
            return wb.labeler_id
        last = self.settings.value("session/last_labeler", "")
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, title, tr("기록에 남길 라벨러 ID (예: L01)"),
                                        text=last if isinstance(last, str) else "")
        text = (text or "").strip()
        if not ok:
            return None
        if not seal.LABELER_RE.match(text):
            self.warn(title, tr("라벨러 ID 형식이 아닙니다 (예: L01)"))
            return None
        return text

    def seal_import_dialog(self, *, path: str | None = None, by: str | None = None):
        """'분석 → AI 결과 가져오기(봉인)…' (06 6-1·6-2·7-2, D-15 7·8). 고른 파일을 바이트 그대로 봉인한다.
        화면에는 봉인 결과(유효·거부 코드)·해시 앞 8자·크기만 보인다. 내용(run_id·판정 수)은 보이지 않는다.
        path·by 를 주면 묻지 않는다(시험). 봉인 기록(SealEntry) 또는 None."""
        title = tr("AI 결과 가져오기(봉인)")
        why = self.analysis_block_reasons(read_seal=False)["seal"]
        if why:
            self.statusBar().showMessage(why, 6000)
            return None
        if path is None:
            path, _f = QFileDialog.getOpenFileName(self, title, "", tr("AI 결과 (*.json);;모든 파일 (*)"))
            if not path:
                return None
        by = self._actor_id(by, title)
        if by is None:
            return None
        from .. import analysis_flow as af
        from .. import seal
        try:
            e = seal.seal_file(self._ai_project_root(), path, source="file", by=by, app_version=__version__,
                               reader_kind=self._reader_kind())
        except (seal.SealError, OSError) as ex:
            self.warn(title, tr("봉인하지 못했습니다: {0}").format(str(ex) if isinstance(ex, seal.SealError)
                                                                   else type(ex).__name__))
            return None
        LOG.info("AI 결과 봉인: %s %s (%d바이트, %s)", af.short(e.sha256), "새로" if e.new else "이미 있음", e.size, e.check)
        if e.check == "valid":
            msg = tr("봉인했습니다: {0}… ({1:,}바이트). 내용은 봉인을 풀 때까지 보이지 않습니다.").format(
                af.short(e.sha256), e.size)
        else:
            msg = tr("봉인은 했지만 가져오기 검사에서 거부되었습니다: {0} — 이 결과는 풀 수 없습니다.").format(
                ", ".join(e.reject_reasons))
        if not e.new:
            msg = tr("이미 봉인한 결과입니다: {0}…").format(af.short(e.sha256))
        # 같은 폴더의 '<이름>.topics.json'(엔진 주제 흐름, tools/engine_to_ai --topics)이 있으면 짝으로 같이 봉인한다
        # (D-17 5 — 판정을 풀 때 같이 풀린다). 고르는 단계를 더하지 않는다(D-16 클릭 한 번).
        self.last_topics_seal = None
        tpath = topics_sibling(path)
        if tpath is not None and e.check == "valid":
            try:
                te = seal.seal_topics_file(self._ai_project_root(), tpath, pair_sha256=e.sha256, by=by,
                                           app_version=__version__, reader_kind=self._reader_kind())
            except (seal.SealError, OSError) as ex:
                LOG.warning("주제 흐름 봉인 실패: %s", type(ex).__name__)
                msg += " " + tr("주제 흐름은 봉인하지 못했습니다: {0}").format(
                    str(ex) if isinstance(ex, seal.SealError) else type(ex).__name__)
            else:
                self.last_topics_seal = te
                LOG.info("주제 흐름 봉인: %s (%d바이트, %s, 짝 %s)", af.short(te.sha256), te.size, te.check,
                         af.short(e.sha256))
                msg += " " + (tr("주제 흐름도 함께 봉인했습니다: {0}…").format(af.short(te.sha256))
                              if te.check == "valid" else
                              tr("주제 흐름은 검사에서 거부되었습니다: {0}").format(", ".join(te.reject_reasons)))
        self.statusBar().showMessage(msg, 8000)
        self.last_seal_message = msg
        return e

    def broadcast_summary_view(self, *, show: bool = True):
        """'분석 → 방송 요약' (08 D-01 방송 분석, D-17 5·8, 17 6-1 2번). 분석 세션에서 해제 뒤에만. 해제된 run 의 짝
        주제 흐름 덩어리마다 채팅 수·반응률·놓친 질문을 표 하나로 보인다(열면 바로, 입력 칸 없음 — D-16). 행을 누르면 그
        덩어리 시작으로 간다. 숫자·주제 이름만 다루고 저장하지 않는다. SummaryView 를 담은 창(모달 아님) 또는 None."""
        title = tr("방송 요약")
        why = self.analysis_block_reasons(read_seal=True)["summary"]
        if why:
            self.statusBar().showMessage(why, 6000)
            return None
        from .. import analysis_flow as af
        wb = self.workbench
        try:
            data = af.broadcast_summary(self._ai_project_root(), reader_kind=self._reader_kind())
        except Exception as ex:  # noqa: BLE001 — 까닭만 보이고 창은 열지 않는다
            msg = str(ex) if not isinstance(ex, (OSError, KeyError, TypeError)) else type(ex).__name__
            self.warn(title, tr("방송 요약을 열지 못했습니다: {0}").format(msg))
            return None
        from PySide6.QtWidgets import QDialog
        from .summary_view import SummaryView
        old = self.summary_dialog
        if old is not None:
            old.close()
            old.deleteLater()
        dlg = QDialog(self)
        src = {"final_set": tr("반응: 사람 최종 세트"), "engine": tr("반응: 엔진 판정")}.get(data.reaction_source or "", "")
        # run 옆에 해제 기록의 엔진 판('판 X' · '판 모름', 23 5-2 의 10)
        run_txt = f"run {data.topic_run_id} · {af.version_text(data.topic_engine_version)}" if data.topic_run_id else ""
        dlg.setWindowTitle(" — ".join(x for x in (title, run_txt, src) if x))
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        # 봉인 표시는 '봉인됨 n건'(D-18 3) — 나란히 보기·상태 줄과 같은 말
        view = SummaryView(data.rows, sealed=data.sealed, sealed_count=data.sealed_count, parent=dlg)
        lay.addWidget(view)
        if wb is not None:
            view.seekRequested.connect(wb.seek_vod)
        avail = self.screen().availableGeometry() if self.screen() is not None else None
        w, h = 900, 560
        if avail is not None and avail.width() > 0:
            w, h = min(w, avail.width() - 40), min(h, avail.height() - 60)
        dlg.resize(w, h)
        dlg.view = view
        dlg.data = data
        self.summary_dialog = dlg
        if show:
            dlg.show()
        if data.warnings:
            self.statusBar().showMessage(tr("방송 요약: 경고 {0}개").format(len(data.warnings)), 6000)
        return dlg

    def unseal_dialog(self, *, sha256: str | None = None, by: str | None = None, confirm=None, early_reason=None):
        """'분석 → 봉인 해제…' (06 7-1·7-2). 06 7-1 조건(라벨링 세션 모두 잠김 · 1차마다 계획 · 계획된 재라벨 잠김 ·
        재현 검증 통과)을 먼저 안내한다. 조건이 안 되면 사유와 함께 거부하고, 사유를 적으면 앞당겨 열 수 있다(기록에
        conditions_met = false). confirm(text) → bool 을 주면 확인 상자 대신(시험), early_reason(reasons) → str|None 을
        주면 사유 입력 대신. UnsealResult 또는 None. self.last_unseal_reasons 에 안 된 조건이 남는다."""
        title = tr("봉인 해제")
        self.last_unseal_reasons = []
        why = self.analysis_block_reasons(read_seal=True)["unseal"]
        if why:
            self.statusBar().showMessage(why, 6000)
            return None
        from .. import analysis_flow as af
        from .. import seal
        root, kind = self._ai_project_root(), self._reader_kind()
        try:
            st = seal.seal_status(root, reader_kind=kind)
            cond = seal.unseal_conditions(root, reader_kind=kind)
        except (seal.SealError, OSError) as ex:
            self.warn(title, tr("봉인 기록을 읽지 못했습니다: {0}").format(type(ex).__name__))
            return None
        waiting = [e for e in st.entries if e.unsealed_at is None and e.check == "valid"]
        if sha256 is not None:
            waiting = [e for e in waiting if e.sha256 == sha256]
        if not waiting:
            self.statusBar().showMessage(tr("풀 수 있는 봉인이 없습니다 (봉인됨 {0}건 · 거부된 것은 풀지 않음)")
                                         .format(st.sealed_count), 6000)
            return None
        target = waiting[0]
        if len(waiting) > 1 and sha256 is None and confirm is None:
            from PySide6.QtWidgets import QInputDialog
            names = [f"{af.short(e.sha256)}… ({e.sealed_at[:19]}, {e.size:,}바이트)" for e in waiting]
            pick, ok = QInputDialog.getItem(self, title, tr("풀 봉인"), names, 0, False)
            if not ok:
                return None
            target = waiting[names.index(pick)]
        reason = None
        if not cond.met:
            self.last_unseal_reasons = list(cond.reasons)
            text = tr("06 7-1 해제 조건이 아직 되지 않았습니다:\n{0}\n\n조건 전에 열면 기록에 '조건 전 해제' 와 사유가 남고, "
                      "점수는 대표 점수로 쓰지 않습니다. 그래도 사유를 적고 앞당겨 열까요?").format(
                "\n".join("· " + r for r in cond.reasons))
            if early_reason is not None:
                reason = early_reason(cond.reasons)
            else:
                go = confirm(text) if confirm is not None else \
                    QMessageBox.question(self, title, text) == QMessageBox.Yes
                if go:
                    from PySide6.QtWidgets import QInputDialog
                    reason, ok = QInputDialog.getText(self, title, tr("앞당겨 여는 사유 (1~500자)"))
                    reason = reason if ok else None
            if not (reason or "").strip():
                LOG.info("봉인 해제 거부: 조건 %d개 안 됨", len(cond.reasons))
                self.statusBar().showMessage(tr("해제하지 않았습니다 — 조건 {0}개가 안 됩니다").format(
                    len(cond.reasons)), 8000)
                return None
        else:
            text = tr("06 7-1 해제 조건이 됩니다 (라벨링 세션 {0}개 모두 잠김). 봉인 {1}… 을 풀까요?\n"
                      "푼 뒤에는 되돌릴 수 없고, 그 시점의 잠금 해시 목록이 기록됩니다.").format(
                len(cond.labeling_sessions), af.short(target.sha256))
            ok = confirm(text) if confirm is not None else QMessageBox.question(self, title, text) == QMessageBox.Yes
            if not ok:
                return None
        by = self._actor_id(by, title)
        if by is None:
            return None
        try:
            res = seal.unseal(root, target.sha256, by=by, early_reason=reason, app_version=__version__,
                              reader_kind=kind)
        except seal.SealBlockedError as ex:
            self.last_unseal_reasons = list(ex.reasons)
            self.warn(title, tr("해제 조건이 되지 않았습니다:\n{0}").format("\n".join(ex.reasons)))
            return None
        except (seal.SealError, OSError, ValueError) as ex:
            self.warn(title, tr("해제하지 못했습니다: {0}").format(str(ex) if isinstance(ex, seal.SealError)
                                                                   else type(ex).__name__))
            return None
        LOG.info("봉인 해제: %s → run %s (조건 %s, 사전 봉인 %s)", af.short(res.sha256), res.run_id,
                 res.conditions_met, res.presealed)
        self.statusBar().showMessage(tr("봉인을 풀었습니다: run {0} ({1})").format(
            res.run_id, tr("사전 봉인") if res.presealed else tr("사전 봉인 아님")), 8000)
        return res

    def side_by_side_view(self, *, show: bool = True, confirm: bool = False):
        """'분석 → 엔진과 나란히 보기' (08 D-01 방송 분석 모드, 16 8-1 5번). 분석 세션에서 해제된 run 만 연다.
        해제 전이면 열지 않는다(D-15 7 — 상태 줄에 '봉인됨 n건'). AnalysisView 를 담은 창(모달 아님) 또는 None.
        confirm: 필터를 'AI 확인 큐' 로 두고 연다(기본 run · 첫 대기 건 반복 재생, 08 D-29 3)."""
        title = tr("엔진과 나란히 보기")
        why = self.analysis_block_reasons(read_seal=True)["view"]
        if why:
            self.statusBar().showMessage(why, 6000)
            return None
        from .. import analysis_flow as af
        wb = self.workbench
        try:
            data = af.side_by_side(self._ai_project_root(), reader_kind=self._reader_kind())
        except Exception as ex:  # noqa: BLE001 — 잠금·조건·형식 문제는 까닭만 보이고 창은 열지 않는다
            msg = str(ex) if not isinstance(ex, (OSError, KeyError, TypeError)) else type(ex).__name__
            self.warn(title, tr("나란히 보기를 열지 못했습니다: {0}").format(msg))
            return None
        if data.run is None:
            self.statusBar().showMessage(tr("봉인됨 {0}건 — 봉인을 푼 뒤에 열립니다 (D-15 7)").format(data.sealed_count), 6000)
            return None
        from PySide6.QtWidgets import QDialog
        from .analysis_view import AnalysisView
        chat_times = None
        idx = getattr(wb, "chat_index", None)
        if idx is not None:
            try:
                # 다른 판에만 있는 채팅도 시각이 있게 전체를 넘긴다(채팅 시각만, 본문 없음)
                chat_times = idx.chat_times(None if len(data.choices) > 1 else [
                    c for c in ({r.get("chat_id") for r in data.final_rows} | {
                        x.get("chat_id") for x in (data.run.get("reactions") or []) if isinstance(x, dict)})
                    if isinstance(c, str)])
            except Exception:  # noqa: BLE001 — 시각이 없으면 최종 세트 행의 시각을 쓴다
                chat_times = None
        if chat_times is None:
            # 채팅 색인이 아직 없으면(작업대가 막 열림) chat/messages.jsonl 의 시각만 — AI 확인 큐의 사람 라벨 없는 건도
            # 채팅 시각으로 재생하게(본문·닉네임은 버린다)
            try:
                chat_times = af.chat_times(self._ai_project_root()) or None
            except Exception:  # noqa: BLE001
                chat_times = None
        dur = None
        tl = getattr(wb, "timeline", None)
        if tl is not None and getattr(tl, "duration", 0):
            dur = int(tl.duration)
        old = self.side_view_dialog
        if old is not None:
            old.close()
            old.deleteLater()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("{0} — run {1}").format(title, data.run_id))
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        # AI 확인 큐 판정(08 D-29 3): 읽기·쓰기는 ai_confirm 이 한다(해제 기록을 먼저 본다) — 화면은 파일을 열지 않는다
        from .. import ai_confirm
        store = ai_confirm.ConfirmStore(self._ai_project_root(), reader_kind=self._reader_kind(),
                                        by=getattr(wb, "labeler_id", None), app_version=__version__)
        view = AnalysisView(dlg, run=data.run, final_rows=data.final_rows, sealed_count=data.sealed_count,
                            scores=data.scores, chat_times=chat_times, duration_ms=dur,
                            runs=data.choices, run_loader=data.load_run, run_id=data.run_id, title_base=title,
                            confirm_rows=list(data.final_rows) + list(data.excluded_rows),
                            confirm_loader=store.load, confirm_writer=store.save)
        lay.addWidget(view)
        if wb is not None:
            view.seekRequested.connect(wb.seek_vod)
        view.loopRequested.connect(self._on_confirm_loop)
        dlg.finished.connect(self._on_side_view_finished)
        avail = self.screen().availableGeometry() if self.screen() is not None else None
        w, h = 1200, 700
        if avail is not None and avail.width() > 0:
            w, h = min(w, avail.width() - 40), min(h, avail.height() - 60)
        dlg.resize(w, h)
        dlg.view = view
        self.side_view_dialog = dlg
        if confirm:
            from .analysis_view import FILTER_CONFIRM
            view.set_filter(FILTER_CONFIRM)          # 기본 run(판이 가장 높은 run) · 첫 대기 건 → 반복 재생
        if show:
            dlg.show()
            if confirm:
                view.list.setFocus()
        if data.warnings:
            self.statusBar().showMessage(tr("나란히 보기: 경고 {0}개 (보고서에서 확인)").format(len(data.warnings)), 6000)
        elif confirm:
            self.statusBar().showMessage(tr("AI 확인 큐: {0}").format(view.confirm_progress_text()), 8000)
        return dlg

    def _on_confirm_loop(self, start_ms: int, end_ms: int) -> None:
        """AI 확인 큐에서 고른 건: 작업대 확인 재생기로 그 범위를 반복한다(영상이 없으면 시작 위치로만, D-16 클릭 0)."""
        wb = self.workbench
        if wb is None:
            return
        try:
            wb.loop_range(int(start_ms), int(end_ms))
        except Exception:  # noqa: BLE001 — 재생 실패로 판정 화면을 막지 않는다
            LOG.exception("AI 확인 큐 범위 재생 실패")

    def _on_side_view_finished(self, _code: int = 0) -> None:
        """나란히 보기 창을 닫으면 AI 확인 큐가 건 반복을 끈다."""
        wb = self.workbench
        if wb is not None and hasattr(wb, "stop_range_loop"):
            try:
                wb.stop_range_loop()
            except Exception:  # noqa: BLE001
                LOG.exception("반복 끄기 실패")

    # ---------------------------------------------------------------- 한 번에 열기 (main.py --project … --analysis --confirm)
    def run_start_request(self, req: dict) -> str | None:
        """main.py 시작 인자: {project, analysis, confirm}. analysis(또는 confirm)이면 open_analysis, 아니면 보통 열기
        (세션 화면). 막혔으면 까닭 한 줄."""
        path = req.get("project")
        if not isinstance(path, str) or not path:
            return None
        if req.get("analysis") or req.get("confirm"):
            return self.open_analysis(path, confirm=bool(req.get("confirm")))
        self.project_view.open_path(os.path.abspath(path))
        return None

    def _block_open(self, why: str) -> str:
        self.last_open_block = why
        self.statusBar().showMessage(why, 15_000)
        LOG.info("한 번에 열기: 막음 (%s)", why[:60])
        return why

    def analysis_session_for(self, root: str, data: dict | None):
        """open_analysis 가 쓸 분석 세션: 마지막 라벨러(이 PC 명부에 있어야 함)의 이어 쓸(잠기지 않은) 분석 세션, 없으면 새
        분석 세션. (SessionContext 또는 None, 까닭). None·까닭 None 이면 라벨러를 몰라 세션 화면으로 간다."""
        from .. import session as sess
        last = self.settings.value(LAST_LABELER_KEY, "")
        if not isinstance(last, str) or not last:
            return None, None
        try:
            sess.check_labeler_id(last)
            if sess.load_labelers(self.data_root).get(last) is None:
                return None, None
            mid = sess.machine_id(self.data_root)
            sv = sess.survey_project(root, data)
            mine = sess.existing_sessions(root, last, sv, sess.machine4(mid))
            open_an = [e for e in mine if e.session_kind == "analysis" and e.resumable]
            if open_an:
                return sess.resume_session(open_an[-1].session_id, machine_id_value=mid), None
            ch = {c.kind: c for c in sess.kind_choices(sv, last, mine, this_machine4=sess.machine4(mid))}
            an = ch.get("analysis")
            if an is not None and not an.enabled:
                return None, tr("분석 세션을 열 수 없습니다: {0}").format(an.reason)
            return sess.new_session(last, "analysis", data_root=self.data_root, machine_id_value=mid), None
        except (OSError, ValueError) as ex:
            return None, tr("분석 세션을 고르지 못했습니다 ({0})").format(type(ex).__name__)

    def open_analysis(self, path: str, *, confirm: bool = False, show: bool = True) -> str | None:
        """프로젝트를 분석 세션으로 열고(세션 화면 없이), 작업대 일이 끝나면 나란히 보기를 연다 — confirm 이면 'AI 확인 큐'
        필터(08 D-29 3, D-16 클릭 한 번). 시범 프로젝트는 reference_set.pilot_sessions_locked 가 True(D-27 3: 조건 채운 해제
        기록이 있으면 True)일 때만 — 아니면 상태 줄에 까닭 한 줄을 보이고 프로젝트를 열지 않는다. 막은 까닭 또는 None."""
        from .. import reference_set as rs
        self.last_open_block = None
        root = os.path.abspath(path)
        if os.path.basename(root).lower() == "project.json":
            root = os.path.dirname(root)
        if not os.path.isfile(os.path.join(root, "project.json")):
            return self._block_open(tr("AI 확인 큐: 프로젝트 폴더가 아닙니다 (project.json 없음)"))
        if rs.is_pilot_project(root):
            ok, _why = rs.pilot_sessions_locked(root)
            if not ok:
                return self._block_open(tr("AI 확인 큐: 시범 세션이 모두 잠기고 조건을 채운 해제 기록이 있어야 엽니다 "
                                           "(08 D-27 3) — 프로젝트를 열지 않았습니다"))
        from ..project import open_project
        try:
            data = open_project(root).data
        except Exception:  # noqa: BLE001
            data = None
        ctx, why = self.analysis_session_for(root, data)
        if why:
            return self._block_open(why)
        self._pending_session = ctx                  # None 이면 세션 화면(라벨러를 모름)
        self._pending_side = {"confirm": bool(confirm), "show": bool(show)}
        try:
            outcome = self.project_view.open_path(root)
        finally:
            self._pending_session = None
        wb = self.workbench
        if outcome is None or not getattr(outcome, "ok", False) or wb is None:
            self._pending_side = None
            return self._block_open(tr("AI 확인 큐: 프로젝트를 열지 못했습니다"))
        if self._reader_kind() != "analysis":
            self._pending_side = None
            return self._block_open(tr("AI 확인 큐는 분석 세션에서 엽니다 — 세션 화면에서 '분석 보기' 를 고르세요"))
        if wb.jobs_busy():
            wb.jobsBusyChanged.connect(self._on_jobs_for_side)
        else:
            self._open_pending_side()
        return None

    def _on_jobs_for_side(self, busy: bool) -> None:
        if busy:
            return
        wb = self.workbench
        if wb is not None:
            try:
                wb.jobsBusyChanged.disconnect(self._on_jobs_for_side)
            except (RuntimeError, TypeError):
                pass
        self._open_pending_side()

    def _open_pending_side(self) -> None:
        req, self._pending_side = self._pending_side, None
        if req is None or self.workbench is None:
            return
        self.update_session_actions()
        self.update_analysis_actions()
        dlg = self.side_by_side_view(show=req["show"], confirm=req["confirm"])
        if dlg is None:
            self.last_open_block = self.statusBar().currentMessage() or tr("나란히 보기를 열지 못했습니다")

    def ai_compare_dialog(self, *, run_id: str | None = None, out_root: str | None = None, gate=None):
        """'보고 → AI 비교 보고서…' (06 6-3~6-7). 세션 밖·조정 세션에서 해제된 run 하나로 보고서를 프로젝트 밖에 쓴다
        (기본 <데이터>\\reports, 일치도 보고서와 같은 규칙). 사람 일치도 기준은 gate(True·False·None) — 없으면 '기준 미확인'.
        run_id·out_root 를 주면 묻지 않는다(시험). ReportResult 또는 None."""
        title = tr("AI 비교 보고서")
        why = self.analysis_block_reasons(read_seal=True)["report"]
        if why:
            self.statusBar().showMessage(why, 6000)
            return None
        from .. import ai_compare
        from .. import analysis_flow as af
        root, kind = self._ai_project_root(), self._reader_kind()
        _sealed, runs = af.seal_counts(root, reader_kind=kind)
        if run_id is None:
            if len(runs) == 1:
                run_id = runs[0]
            else:
                from PySide6.QtWidgets import QInputDialog
                pick, ok = QInputDialog.getItem(self, title, tr("보고서를 만들 run"), list(reversed(runs)), 0, False)
                if not ok:
                    return None
                run_id = pick
        if out_root is None:
            from .. import agreement_report as ar
            out_root = ar.default_out_root(root)
        created_by = self.workbench.labeler_id if self.workbench is not None and self.workbench.session else None
        box: dict = {}

        def job(_ctx):
            run = ai_compare.load_unsealed_run(root, run_id, reader_kind=kind)    # 해제 확인이 라벨 파일보다 먼저
            final, g = None, gate
            if g is None:
                # 사람 일치도 기준은 같은 1차·재라벨 쌍의 최신 일치도 보고서에서(17 5-2 3번) — 나란히 보기와 같은 기준
                from .. import export
                final = export.build_final_set(root, reader_kind=kind)
                g, _rid = af.agreement_gate(root, final.info, reader_kind=kind)
            return ai_compare.make_report(root, run, out_root=out_root, human_gate=g, reader_kind=kind,
                                          created_by=created_by, app_version=__version__, final=final)
        from ..workers import JobWorker
        w = JobWorker.single("ai_compare", job, name="AI 비교 보고서", parent=self)
        w.failed.connect(lambda _k, m: box.setdefault("err", m))
        outcome = self.run_job_modal(w, title=title, label=tr("AI 비교 보고서를 만드는 중… (run {0})").format(run_id))
        res = w.results.get("ai_compare")
        w.deleteLater()
        if outcome != "done" or res is None:
            if outcome == "failed":
                self.warn(title, tr("보고서를 만들지 못했습니다: {0}").format(box.get("err", "")))
            return None
        LOG.info("AI 비교 보고서: run %s · 경고 %d개", run_id, len(res.warnings or []))
        self.statusBar().showMessage(tr("AI 비교 보고서를 썼습니다: {0}").format(res.out_dir), 8000)
        return res

    def agreement_block_reason(self) -> str | None:
        """일치도 보고서를 지금 열 수 없는 까닭.

        - 자리: 보기 전용(세션 없음)과 조정 세션만(03 12-1: 보고서 쓰기는 조정 세션 RW). 라벨링 종류(1차·재라벨)는 잠긴
          세션을 다시 열었어도, 분석 세션(보고서 읽기만·다른 세션은 잠긴 1차만)도 끈다(06 7-3).
        - 06 7-1: relabel/ 계획 가운데 잠긴 재라벨 세션이 아직 없는 것이 있으면 끈다(어느 1차 세션의 것이든 — 메뉴는
          두 세션을 고르기 전이라 넓게 막는다. 고른 뒤에는 build_report 가 그 1차 세션의 계획만 다시 본다).
        """
        from .. import agreement_report as ar
        wb = self.workbench
        if wb is None or getattr(wb, "project", None) is None:
            return tr("프로젝트를 먼저 여세요")
        if getattr(self, "training_view", None) is not None or getattr(self, "viewer_view", None) is not None \
                or getattr(self, "discern_view", None) is not None:
            # 보고서는 채팅 단위로 사람 라벨(정답)을 보인다 — 훈련 중에는 끈다(D-19 4, 19 5-2 의 4번). 사람 클릭 0.
            # 시청자 훈련 중에도 끈다(정답을 보이지 않는다는 D-20 5 의 전제)
            return tr("훈련 중에는 쓰지 않습니다 — 훈련을 닫은 뒤에 쓸 수 있습니다")
        kind = wb.session_kind if wb.session is not None else None
        why = ar.reader_block_reason(kind)
        if why:
            return tr(why)
        try:
            pending = ar.pending_relabel_plans(wb.project.root)
        except OSError:
            pending = []
        if pending:
            return tr("계획된 재라벨 세션 {0}개가 아직 잠기지 않았습니다 — 모두 잠근 뒤에 보고서를 엽니다 (06 7-1)") \
                .format(len(pending))
        return None

    def jobs_busy(self) -> bool:
        """작업이 도는 중인가: 메인 창의 run_job_modal(busy_jobs) 또는 작업대 워커(jobs_busy) (15 5-2 의 15번)."""
        wb = self.workbench
        if getattr(self, "busy_jobs", 0) > 0:
            return True
        keys = getattr(wb, "busy_job_keys", None) if wb is not None else None
        if keys is None:
            return False
        # 파형·영상 확인·엔진 결과·색인 같은 뒤 작업은 라벨 기록을 쓰지 않는다 — 몇 분 걸려도 세션 메뉴를 막지 않는다
        return any(k not in BACKGROUND_JOBS for k in keys())

    def update_session_actions(self, *_a) -> None:
        wb = self.workbench
        # 분석 메뉴: 세션 종류만 본다(봉인 건수는 메뉴를 펼 때 읽는다 — 라벨링 세션은 data\ai 를 열지 않는다)
        self._apply_analysis_reasons(self.analysis_block_reasons(read_seal=False))
        if self.jobs_busy():
            why = tr("작업이 도는 중입니다 — 끝난 뒤에 쓸 수 있습니다")
            for a in (self.a_lock, self.a_plan, self.a_agreement, self.a_cancel_plan):
                a.setEnabled(False)
                a.setToolTip(why)
            return
        why_lock = wb.can_lock() if wb is not None else tr("프로젝트를 먼저 여세요")
        self.a_lock.setEnabled(why_lock is None)
        self.a_lock.setToolTip(why_lock or "")
        why_plan = wb.can_make_relabel_plan() if wb is not None else tr("프로젝트를 먼저 여세요")
        self.a_plan.setEnabled(why_plan is None)
        self.a_plan.setToolTip(why_plan or "")
        why_rep = self.agreement_block_reason()
        self.a_agreement.setEnabled(why_rep is None)
        self.a_agreement.setToolTip(why_rep or "")
        why_cancel = self.plan_cancel_block_reason()
        self.a_cancel_plan.setEnabled(why_cancel is None)
        self.a_cancel_plan.setToolTip(why_cancel or "")

    def plan_cancel_block_reason(self) -> str | None:
        """재라벨 계획 취소를 지금 열 수 없는 까닭(06 7-1 '검토자', D-12 7): 조정 세션이고 기록을 쓸 수 있어야 한다."""
        wb = self.workbench
        if wb is None or getattr(wb, "project", None) is None:
            return tr("프로젝트를 먼저 여세요")
        from .. import relabel as relabel_io
        if wb.session is None or wb.session_kind not in relabel_io.CANCEL_KINDS:
            return tr("재라벨 계획 취소는 조정 세션(검토자)에서 합니다 (06 7-1)")
        if wb.store is None:
            return tr("라벨 기록을 여는 중입니다")
        if wb.store.read_only:
            return tr("라벨 기록이 읽기 전용입니다: {0}").format(wb.store.read_only_reason or "")
        return None

    def plan_cancel_dialog(self, *, run=None):
        """'세션 → 재라벨 계획 취소…' (D-12 7). run(dlg) 을 주면 exec() 대신(시험). 쓴 취소 기록 dict 또는 None."""
        why = self.plan_cancel_block_reason()
        if why:
            self.statusBar().showMessage(why, 5000)
            return None
        from .plan_cancel_dialog import PlanCancelDialog
        wb = self.workbench
        dlg = PlanCancelDialog(self, store=wb.store, app_version=__version__)
        try:
            (run or (lambda d: d.exec()))(dlg)
            doc = dlg.doc
        finally:
            dlg.deleteLater()
        if doc is not None:
            if wb.adjudication_view is not None:
                wb.adjudication_view.refresh_plans()
            self.invalidate_training_cache()
            self.update_session_actions()
            self.statusBar().showMessage(tr("재라벨 계획 {0} 를 취소했습니다").format(doc.get("plan_id")), 5000)
        return doc

    def lock_session_dialog(self, *, run=None):
        """'세션 → 이 세션 잠그기…' — 2단계 잠그기 화면(app/ui/lock_dialog). run(dlg) 을 주면 exec() 대신 그것을 부른다
        (시험). 잠갔으면 잠금 내용 dict, 아니면 None. 잠근 뒤 작업대는 읽기 전용(after_session_locked)."""
        wb = self.workbench
        if wb is None:
            return None
        if getattr(self, "busy_jobs", 0) > 0:         # 잠그기 전 검증이 도는 중 — 다시 들어와 워커를 겹치지 않는다
            self.statusBar().showMessage(tr("잠그기 전 검증이 아직 도는 중입니다"), 5000)
            return None
        why = wb.can_lock()
        if why:
            self.statusBar().showMessage(why, 5000)
            return None
        if not wb.prepare_lock():
            return None
        prep = self.lock_prep(wb)
        if prep is None:
            return None
        # 검증이 도는 동안 작업대가 닫혔거나(창 닫기·프로젝트 바꾸기) 닫기를 기다리는 중이면 잠그기 화면을 만들지 않는다
        store = getattr(wb, "store", None)
        if self.workbench is not wb or store is None or getattr(store, "closed", False) \
                or getattr(self, "_close_pending", False):
            return None
        rows, current = prep
        from .lock_dialog import LockDialog
        recheck = getattr(wb, "current_sample_recheck", None) if current is not None else None
        extra = {"recheck": recheck} if recheck is not None else {}      # 잠그기 직전 구간표 지문 재검사 (15 5-2 의 12번)
        dlg = LockDialog(self, store=wb.store, sample_verify=rows, current_sample=current, **extra)
        try:
            (run or (lambda d: d.exec()))(dlg)
            doc = dlg.lock_doc
        finally:
            dlg.deleteLater()
        if doc is not None:
            wb.after_session_locked()
            self.invalidate_training_cache()         # 잠기면 훈련 기준(최종 세트)·시범 잠금이 바뀔 수 있다
            self.update_session_actions()
            self.statusBar().showMessage(tr("세션 {0} 를 잠갔습니다").format(doc.get("session_id")), 5000)
        return doc

    def run_job_modal(self, worker, *, title: str, label: str) -> str | None:
        """JobWorker 를 UI 스레드 밖에서 돌리는 동안 바쁨 표시(진행 대화상자·바쁨 커서)와 [취소] 를 보인다(14 6-1 의 1).

        끝날 때까지 이 자리에서 기다리되 이벤트 루프는 돈다(화면이 멈추지 않음). 끝난 결과 worker.outcome
        ('done'·'failed'·'cancelled')를 돌려준다. 취소는 일과 일 사이(또는 일이 ctx.check() 를 부를 때) 먹힌다."""
        from PySide6.QtCore import QEventLoop
        prog = QProgressDialog(label, tr("취소"), 0, max(1, len(worker.jobs)), self)
        prog.setWindowTitle(title)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)              # 처음부터 띄워 WindowModal 이 바로 걸리게(메인 창 입력을 막는다)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        prog.setValue(0)
        prog.show()
        loop = QEventLoop(self)
        ended = {"v": False}

        def end(*_a):
            ended["v"] = True
            loop.quit()
        worker.progress.connect(lambda n, _total, _key: prog.setValue(n))
        worker.done.connect(end)
        worker.failed.connect(end)
        worker.cancelled.connect(end)

        def on_cancel():
            worker.cancel()
            prog.setLabelText(tr("취소하는 중… (지금 하던 일이 끝나면 멈춥니다)"))
        prog.canceled.connect(on_cancel)
        self.busy_jobs = getattr(self, "busy_jobs", 0) + 1
        jobs = getattr(self, "_modal_workers", None)
        if jobs is None:
            jobs = self._modal_workers = []
        jobs.append(worker)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            worker.start()
            if not ended["v"]:
                loop.exec()
            worker.wait()
        finally:
            QApplication.restoreOverrideCursor()
            self.busy_jobs -= 1
            if worker in jobs:
                jobs.remove(worker)
            prog.close()
            prog.deleteLater()
            if self.busy_jobs == 0 and getattr(self, "_close_pending", False):
                QTimer.singleShot(0, self.close)        # 도는 동안 미뤄 둔 창 닫기(closeEvent)를 이제 한다
            try:
                self.update_session_actions()           # 작업이 끝났으니 세션 메뉴를 다시 켠다
            except Exception:  # noqa: BLE001 — 메뉴 갱신 실패가 작업 결과를 가리지 않게
                LOG.exception("세션 메뉴 갱신 실패")
        return worker.outcome

    def lock_prep(self, wb):
        """잠그기 직전 재현 검증(1차: 표본 sample_verify)·자기 표본 목록(재라벨: current_sample)을 워커로 낸다.
        (sample_verify 행 목록, current_sample 또는 None). 취소·실패면 None(잠그지 않는다, 상태 줄에 까닭)."""
        from ..workers import JobWorker, SampleVerifyWorker, sample_verify_rows
        job = wb.current_sample_job()
        if job is not None:
            w = JobWorker.single("current_sample", job, name="재라벨 표본 목록", parent=self)
            label = tr("잠그기 전에 이 세션의 블록 표본 목록을 계산하는 중…")
        else:
            mans, verify_one = wb.sample_verify_plan()
            if not mans:
                return [], None
            w = SampleVerifyWorker(mans, verify_one, parent=self)
            label = tr("잠그기 전에 표본 {0}개의 재현 검증을 하는 중…").format(len(mans))
        outcome = self.run_job_modal(w, title=tr("세션 잠그기"), label=label)
        res = dict(w.results)
        w.deleteLater()
        if outcome != "done":
            self.statusBar().showMessage(tr("잠그기 전 검증을 {0} — 잠그지 않았습니다").format(
                tr("취소했습니다") if outcome == "cancelled" else tr("하지 못했습니다")), 6000)
            return None
        if job is not None:
            return [], res.get("current_sample")
        return sample_verify_rows(res), None

    def relabel_plan_dialog(self, *, run=None):
        """'세션 → 재라벨 계획 만들기…'. run(dlg) 을 주면 exec() 대신(시험). 만든 계획 dict 또는 None."""
        wb = self.workbench
        if wb is None:
            return None
        why = wb.can_make_relabel_plan()
        if why:
            self.statusBar().showMessage(why, 5000)
            return None
        from .relabel_dialog import RelabelPlanDialog
        dlg = RelabelPlanDialog(self, workbench=wb, data_root=self.data_root)
        try:
            (run or (lambda d: d.exec()))(dlg)
            return dlg.plan
        finally:
            dlg.deleteLater()

    def agreement_dialog(self, *, run=None):
        """'보고 → 일치도 보고서…'. run(dlg) 을 주면 exec() 대신(시험). 만든 ReportResult 또는 None."""
        why = self.agreement_block_reason()
        if why:
            self.statusBar().showMessage(why, 5000)
            return None
        from .agreement_dialog import AgreementDialog
        wb = self.workbench
        dlg = AgreementDialog(self, project_root=wb.project.root, created_by=wb.labeler_id,
                              reader_kind=wb.session_kind if wb.session is not None else None,
                              chat_times_provider=wb.chat_times_provider())
        try:
            (run or (lambda d: d.exec()))(dlg)
            return dlg.result
        finally:
            dlg.deleteLater()

    def _fill_recent_menu(self) -> None:
        self.m_recent.clear()
        items = load_recent(self.settings)
        for p in items:
            a = self.m_recent.addAction(os.path.basename(p.rstrip("\\/")) or p)
            a.setToolTip(p)
            a.triggered.connect(lambda _c=False, p=p: self.project_view.open_path(p))
        if not items:
            a = self.m_recent.addAction(tr("(없음)"))
            a.setEnabled(False)

    def choose_session(self, outcome):
        """세션 선택 화면(08 D-09 1). 고른 SessionContext, 보기 전용·취소면 None. session_chooser 가 있으면 그것을 쓴다.
        시작할 때 이어서 열기(auto_resume) 중이면 먼저 auto_resume_session 으로 화면 없이 고를 수 있는지 본다.
        '보기 전용으로 다시 열고 훈련'(D-19 3) 중이면 묻지 않고 보기 전용(None)."""
        if getattr(self, "_reopen_view_only", False):
            return None
        pending, self._pending_session = getattr(self, "_pending_session", None), None
        if pending is not None:                      # open_analysis 가 고른 분석 세션(세션 화면 없이, D-16)
            self.settings.setValue(LAST_LABELER_KEY, pending.labeler_id)
            return pending
        if self._auto_resume:
            ctx = self.auto_resume_session(outcome)
            if ctx is not None:
                return ctx
        if self.session_chooser is not None:
            return self.session_chooser(outcome)
        from .session_dialog import SessionDialog
        data = outcome.project.data or {}
        title = (data.get("broadcast") or {}).get("title") or data.get("slug") or None
        last = self.settings.value("session/last_labeler", "")
        dlg = SessionDialog(self, data_root=self.data_root, default_labeler=last if isinstance(last, str) and last else None)
        dlg.load_context(outcome.project.root, data, project_title=title, read_only=outcome.read_only,
                         read_only_reasons=list(outcome.project.problems))
        dlg.exec()
        ctx = dlg.context()
        dlg.deleteLater()
        if ctx is not None:
            self.settings.setValue("session/last_labeler", ctx.labeler_id)
            self.settings.sync()
        return ctx

    def show_workbench(self, outcome) -> None:
        """연 프로젝트로 세션을 고르고 작업대를 새로 만들어 보인다. 이전 작업대는 닫는다(reason 'switch_project').
        잠금(outcome.lock)은 작업대가 넘겨받아 store 에 준다."""
        from .workbench import Workbench
        had = self.workbench is not None
        self._close_workbench("switch_project" if had else "user")   # 저장 안 한 구간 변경은 그 안에서 묻는다
        if not self.confirm_project_path(outcome):
            held = getattr(outcome, "lock", None)
            if held is not None:
                outcome.lock = None
                try:
                    held.unlock()
                except Exception:  # noqa: BLE001
                    LOG.exception("프로젝트 잠금 풀기 실패")
            self.show_home()
            return
        self.refresh_relabel_index(outcome)
        try:
            self.session = self.choose_session(outcome)
        except Exception:  # noqa: BLE001 — 세션 화면이 실패해도 보기 전용으로는 연다
            LOG.exception("세션 선택 화면 실패 — 보기 전용으로 엽니다")
            self.session = None
        # 04 5-5 '업데이트 필요' 모드: 새 라벨 저장을 막는다(라벨 기록을 읽기 전용으로 연다). 열람·내보내기는 된다
        block = updater.label_save_block_reason(self.state_path, __version__)
        opts = {"save_block": tr(block)} if block and self.session is not None else None
        wb = Workbench(self, session=self.session, store_options=opts)   # 세션이 있으면 작업대가 app.store 로 라벨 기록을 연다
        wb.data_root = self.data_root                # 채팅 색인 캐시 자리(<data>\cache\<project_id>, 프로젝트 밖)
        wb.settings = self.settings                  # 칸 나누기 크기 · 파일 대화상자 시작 폴더를 남길 자리
        self.workbench = wb
        wb.jobsBusyChanged.connect(self.update_session_actions)
        self.stack.addWidget(wb)
        self.stack.setCurrentWidget(wb)
        # 상태 줄 하나(11 4-2 의 9): 작업대의 상태 칩을 이 창 상태 줄로 옮기고, 잠깐 문장(저장 중… 등)만 왼쪽에 보인다
        wb.attach_status_bar(self.statusBar())
        wb.messageShown.connect(lambda t: self.statusBar().showMessage(t))
        self.m_view.clear()
        self.m_view.addAction(wb.analysis_action)
        self.m_view.menuAction().setVisible(wb.analysis_action.isVisible())
        self.a_close_project.setEnabled(True)
        self.a_export.setEnabled(self.session is not None)
        data = outcome.project.data or {}
        title = (data.get("broadcast") or {}).get("title") or data.get("slug") or ""
        base = f"{self.spec.name} {__version__}"
        self.setWindowTitle(f"{base} — {title}" if title else base)
        if self.width() < 1200:
            # 화면(작업 표시줄 뺀 영역)보다 크게 만들지 않는다 — 1366x768 화면에서 창 테두리만큼 잘리던 문제
            avail = self.screen().availableGeometry() if self.screen() is not None else None
            frame_w = max(0, self.frameGeometry().width() - self.width())
            frame_h = max(0, self.frameGeometry().height() - self.height())
            w, h = 1366, 768
            if avail is not None and avail.width() > 0:
                w, h = min(w, avail.width() - frame_w), min(h, avail.height() - frame_h)
            self.resize(w, h)
            if avail is not None and not avail.contains(self.frameGeometry()):
                self.move(avail.topLeft())
        wb.open_project(outcome)
        wb.restore_splits()                          # 지난번 칸 나누기 크기(비율). 조정 모드 넓히기와 함께 돈다
        wb.setFocus()
        self.update_session_actions()
        self._note_retention(outcome)
        self.refresh_path_banner()

    def refresh_relabel_index(self, outcome) -> str:
        """프로젝트를 열 때(세션을 고르기 전, 세션 밖) relabel/index.json 을 조용히 다시 만든다(14 4-2 의 2).

        세션 화면(라벨러 자리)이 색인을 다시 만들면 모든 계획·취소 기록을 여는데, 그 일을 세션 밖에서 미리 해 둔다.
        읽기 전용으로 열었거나 relabel/ 이 없으면 하지 않는다. 실패해도 열기는 계속한다(세션 화면이 그때 다시 본다).
        색인이 이미 계획 파일과 같으면 쓰지 않는다(열 때마다 프로젝트 폴더를 고치지 않게).
        로그에는 결과 종류·계획 수만. 돌려주는 값: 'rebuilt' · 'same' · 'skipped' · 'failed'(시험·점검용)."""
        proj = getattr(outcome, "project", None)
        if proj is None or getattr(outcome, "read_only", False) or getattr(proj, "read_only", False):
            return "skipped"
        from .. import relabel as relabel_io
        if not os.path.isdir(os.path.join(proj.root, relabel_io.RELABEL_DIR)):
            return "skipped"
        try:
            try:
                cur = relabel_io.read_index(proj.root)
            except ValueError:
                cur = None
            if cur is not None and cur == relabel_io.build_index(proj.root):
                return "same"
            doc = relabel_io.rebuild_index(proj.root, creator_kind=None)
        except (OSError, ValueError) as e:
            LOG.warning("재라벨 계획 색인을 다시 만들지 못했습니다: %s", type(e).__name__)
            return "failed"
        LOG.info("재라벨 계획 색인을 다시 만들었습니다: 계획 %d개", len(doc.get("plans") or []))
        return "rebuilt"

    def _before_open(self, path: str) -> None:
        """열려는 폴더가 지금 작업대의 프로젝트면 작업대를 먼저 닫아 잠금(locks/open.lock)을 푼다.

        닫지 않으면 open_checked 가 그 잠금 때문에 tryLock 에 실패해 다시 연 프로젝트가 늘 읽기 전용이 된다
        (그 뒤 세션이 있으면 store 만 잠금을 새로 잡아 쓰기 가능 — 둘이 어긋난다). 다른 프로젝트면 그대로 둔다
        (열기가 실패해도 지금 작업대를 잃지 않게). 비교는 project_view 의 경로 정규화와 같다.
        """
        from .project_view import _norm
        wb = self.workbench
        proj = getattr(wb, "project", None) if wb is not None else None
        if proj is None:
            return
        target = os.path.abspath(path)
        if os.path.basename(target).lower() == "project.json":
            target = os.path.dirname(target)
        if _norm(target) == _norm(proj.root):
            self._close_workbench("switch_project")
            self.show_home()

    def _close_workbench(self, reason: str = "user") -> None:
        """작업대를 닫는다. 저장하지 않은 구간 변경이 있으면 저장·버리기를 묻고(취소 없음), store 는 reason 으로 닫는다."""
        self.close_training()                 # 훈련 화면은 이 작업대(채팅·보정)에 묶여 있다 — 먼저 닫는다(training.end)
        self.close_viewer_training()          # 시청자 훈련도 같다(viewer.end closed)
        self.close_discern()                  # 가려내기도 같다(정답 전이면 discern.end closed)
        self.invalidate_training_cache()
        self._discern_scn_cache = None
        wb = self.workbench
        if wb is None:
            return
        # 다시 열기 전 세션의 라벨러는 그 다시 연 작업대에서만 쓴다(다른 프로젝트로 새지 않게). 작업대가 없을 때(새로 열기
        # 직전의 _close_workbench)는 비우지 않는다 — 다시 열기가 닫은 뒤 힌트를 두고 바로 연다
        self._training_labeler_hint = None
        try:
            wb.resolve_unsaved(allow_cancel=False)
        except Exception:  # noqa: BLE001 — 닫는 길은 막지 않는다
            LOG.exception("저장하지 않은 변경 처리 실패")
        st, proj = getattr(wb, "store", None), getattr(wb, "project", None)
        own_file = st.path if st is not None and not getattr(st, "read_only", True) else None
        side = getattr(self, "side_view_dialog", None)
        if side is not None:                 # 나란히 보기 창은 이 작업대(플레이어)에 묶여 있다
            self.side_view_dialog = None
            side.close()
            side.deleteLater()
        self.workbench = None
        sig = getattr(wb, "jobsBusyChanged", None)
        if sig is not None:
            try:
                sig.disconnect(self.update_session_actions)
            except (RuntimeError, TypeError):
                pass
        wb.close_workbench(reason)           # 상태 칩도 이 창 상태 줄에서 뗀다(detach_status_bar)
        self.stack.removeWidget(wb)
        wb.deleteLater()
        self.m_view.clear()
        self.m_view.menuAction().setVisible(False)
        self.a_export.setEnabled(False)
        self.project_path_issues, self.retention_line = [], None
        self.refresh_path_banner()
        self.update_session_actions()
        if self.session_backup and own_file and proj is not None and os.path.isfile(own_file):
            # 세션 끝: 자기 세션 기록 하나를 백업 폴더에 복사 (S-39 ③). 실패해도 닫기는 계속한다
            from .. import backup
            try:
                backup.copy_session_file(proj.root, own_file)
            except (backup.BackupError, OSError, ValueError) as e:
                LOG.warning("세션 기록 복사 실패 (%s)", type(e).__name__)

    def show_home(self) -> None:
        self._close_workbench("user")
        self.session = None
        self.stack.setCurrentWidget(self.home_page)
        self.a_close_project.setEnabled(False)
        self.setWindowTitle(f"{self.spec.name} {__version__}")
        self.project_view.refresh()
        self.statusBar().showMessage(tr("준비"))

    def closeEvent(self, ev):  # noqa: N802
        if getattr(self, "busy_jobs", 0) > 0:
            # run_job_modal 이 안쪽 이벤트 루프를 도는 중 — 지금 store 를 닫으면 그 뒤 잠그기 화면이 닫힌 store 로 뜬다.
            # 작업을 취소하고, 끝나면(run_job_modal 의 finally) 다시 닫는다.
            self._close_pending = True
            for w in list(getattr(self, "_modal_workers", None) or ()):
                try:
                    w.cancel()
                except Exception:  # noqa: BLE001 — 닫는 길은 막지 않는다
                    LOG.exception("작업 취소 실패")
            self.statusBar().showMessage(tr("하던 작업을 멈추는 중입니다 — 끝나면 창을 닫습니다"))
            ev.ignore()
            return
        self._close_pending = False
        self._close_workbench("app_exit")
        super().closeEvent(ev)

    # ---------------------------------------------------------------- 확인
    def update_channel(self) -> str:
        """숨은 설정 [update] channel (stable | beta, 04 6-2). 모르는 값은 stable."""
        return updater.normalize_channel(self.settings.value(UPDATE_CHANNEL_KEY, "stable"))

    def manual_check_updates(self) -> None:
        """[업데이트 → 지금 확인]: 결과를 쉬운 문장으로 상태 줄에 보인다(자동 확인은 할 일이 있을 때만 보인다)."""
        self._manual_check = True
        self.check_updates(force=True)

    def _set_chip(self, text: str, tip: str = "") -> None:
        """업데이트 칩: 글자가 있을 때만 보인다(빈 칩이 상태 줄 자리를 차지하지 않게). 문제 보고용 상태도 같이 남긴다."""
        self.status_chip.setText(text)
        self.status_chip.setToolTip(tip)
        self.status_chip.setVisible(bool(text))
        if text:
            self.update_status = text

    def check_updates(self, force: bool, on_start: bool = False) -> None:
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        if self._manual_check:
            self._set_chip(tr("업데이트 확인 중…"))
        self._check_worker = UpdateCheckWorker(self.spec, self.installed, self.state_path, force, self.base_url, self,
                                               channel=self.update_channel(), on_start=on_start)
        self._check_worker.result.connect(self._on_check)
        self._check_worker.start()

    @staticmethod
    def quiet_failure_text(res: updater.CheckResult) -> str:
        """조용히 넘기는 확인 실패(릴리스 없음 404·인터넷 끊김)의 쉬운 문장 — 사용자가 직접 확인했을 때만 보인다."""
        if "404" in (res.reason or ""):
            return tr("아직 받을 수 있는 업데이트가 없습니다")
        return tr("지금은 업데이트를 확인할 수 없습니다. 인터넷 연결을 확인한 뒤 다시 해 보세요")

    def _on_check(self, res: updater.CheckResult | None) -> None:
        self._result = res
        manual, self._manual_check = self._manual_check, False
        self.refresh_support_banner()           # 확인 결과로 min_supported·mandatory 가 바뀌었을 수 있다
        now = datetime.now().strftime("%H:%M")
        if res is None:
            self._set_chip("")
            self.update_status = tr("업데이트: 아직 확인할 때가 아님 ({0})").format(now)
            return
        if res.state == "failed":
            self.update_btn.setVisible(False)
            if res.kind == "signature":
                self._set_chip(tr("서명 검증 실패 — 개발자에게 알려 주세요"), res.reason)
            elif res.kind == "manual":
                self._set_chip(tr("수동 업데이트 필요"), res.reason + "\n" + MANUAL_GUIDE)
            elif res.kind == "network":
                # 릴리스가 아직 없음(404)·인터넷 끊김: 상태 줄에는 아무것도 띄우지 않는다(로그는 updater 가 INFO 로 남김).
                # 사용자가 직접 [지금 확인] 을 눌렀을 때만 쉬운 문장 한 줄
                self._set_chip("")
                self.update_status = tr("업데이트 확인 못 함 ({0}): {1}").format(now, res.reason[:120])
                LOG.info("업데이트 확인: 받을 수 없음 (네트워크·릴리스 없음) — 상태 줄에는 표시하지 않음")
                if manual:
                    self.statusBar().showMessage(self.quiet_failure_text(res), 8000)
            else:
                self._set_chip(tr("업데이트 확인 실패 — {0}").format(res.reason[:80]), res.reason)
            return
        if res.state == "latest":
            self._set_chip("")
            self.update_status = tr("최신 (마지막 확인 {0})").format(now)
            self.update_btn.setVisible(False)
            if manual:
                self.statusBar().showMessage(tr("지금 쓰는 버전이 최신입니다 (v{0})").format(__version__), 8000)
            return
        rel = res.release
        assert rel is not None
        self._set_chip(tr("새 버전 있음 ({0})").format(now))
        self.update_btn.setText(tr("새 버전 v{0}").format(rel.version))
        self.update_btn.setVisible(not res.skipped or rel.manifest.mandatory)
        if self.auto:
            if res.skipped and not rel.manifest.mandatory:
                LOG.info("자동 모드: 건너뛴 버전 v%s 은 적용하지 않음 (되돌린 뒤 등)", rel.version)
            else:
                QTimer.singleShot(0, lambda: self.start_stage(rel))
        elif rel.manifest.mandatory and not res.skipped:
            QTimer.singleShot(0, self.offer_update)

    # ---------------------------------------------------------------- 대화상자 · 준비 · 적용
    def offer_update(self) -> None:
        if not self._result or not self._result.release:
            return
        rel = self._result.release
        dlg = UpdateDialog(rel, self._result.needs_update, self.spec, self)
        rc = dlg.exec()
        if dlg.skip:
            st = updater.UpdateState.load(self.state_path)
            st.skip_version = rel.version
            st.save(self.state_path)
            self.update_btn.setVisible(False)
            return
        if rc == UpdateDialog.Accepted:
            self.start_stage(rel)

    def start_stage(self, rel: updater.Release) -> None:
        if self._stage_worker is not None and self._stage_worker.isRunning():
            return
        prog = QProgressDialog(tr("업데이트 준비 중…"), tr("취소"), 0, 1000, self)
        prog.setWindowTitle(tr("업데이트 v{0}").format(rel.version))
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        worker = UpdateStageWorker(rel, self.spec, self.installed, self.base_url, self)
        self._stage_worker = worker
        worker.progress.connect(lambda f, t: (prog.setValue(int(f * 1000)), prog.setLabelText(t)))
        prog.canceled.connect(worker.cancel)

        def done(staged, err: str):
            prog.close()
            if err or staged is None:
                if self.auto:
                    LOG.error("자동 모드: 준비 실패 — %s", err)
                elif not err.startswith("[download] 취소"):
                    QMessageBox.warning(self, tr("업데이트 준비 실패"), err or tr("알 수 없는 오류"))
                return
            self.apply_update(staged)

        worker.done.connect(done)
        worker.start()

    def apply_update(self, staged: updater.StagedUpdate) -> None:
        applier = updater.RobocopyApplier(self.spec, self.installed, self.data_root, data_backup=self.data_backup)
        problems, warnings = applier.preflight(staged)
        if problems:
            if self.auto:
                LOG.error("자동 모드: 교체 전 조건 미충족 — %s", problems)
            else:
                QMessageBox.warning(self, tr("자동 업데이트를 진행할 수 없습니다"), "\n".join("• " + p for p in problems) + "\n\n" + tr(MANUAL_GUIDE))
            return
        if warnings:
            if self.auto:
                LOG.info("자동 모드: 경고를 기록하고 계속 — %s", warnings)
            elif QMessageBox.question(self, tr("계속할까요?"), "\n".join("• " + w for w in warnings) + "\n\n" + tr("그래도 계속합니까?"),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                LOG.info("사용자가 경고 뒤 업데이트를 취소함: %s", warnings)
                return
            LOG.info("사용자가 경고를 보고 계속 진행: %s", warnings)
        try:
            applier.apply(staged)
        except updater.UpdateError as ex:
            if self.auto:
                LOG.error("자동 모드: 적용 실패 — %s", ex.message)
            else:
                QMessageBox.warning(self, tr("자동 업데이트를 진행할 수 없습니다"), ex.message + "\n\n" + tr(MANUAL_GUIDE))
            return
        LOG.info("교체를 위해 종료 (v%s → v%s)", self.installed.version, staged.release.version)
        self.statusBar().showMessage(tr("업데이트를 적용하려고 종료합니다…"))
        QTimer.singleShot(100, QApplication.instance().quit)

    # ---------------------------------------------------------------- 시작 알림
    def notify_startup_flag(self, flag: str) -> None:
        last = updater.read_last_result(self.data_root) or {}
        if flag == "--updated":
            QMessageBox.information(self, tr("업데이트 완료"),
                                    tr("v{0} 으로 업데이트했습니다. (이전 {1})\n이전 버전 사본(_backup)은 다음 정상 시작 뒤에 지워집니다.")
                                    .format(__version__, last.get("from", "?")))
        elif flag == updater.ROLLBACK_FLAG:
            QMessageBox.information(self, tr("되돌리기 완료"),
                                    tr("v{0} 으로 되돌렸습니다. (되돌리기 전 {1})\nv{1} 은 다시 권하지 않습니다. 더 높은 버전이 나오면 "
                                       "다시 알립니다.").format(__version__, last.get("from", "?")))
        elif flag == "--update-failed" and last.get("result") == "rollback_failed":
            log = os.path.join(self.data_root, "logs", "update.log")
            QMessageBox.warning(self, tr("되돌리기 실패"),
                                tr("이전 버전으로 되돌리지 못했습니다 (copy_rc={0}, retry_rc={1}). 프로그램 파일이 섞였을 수 "
                                   "있습니다.\n로그: {2}\n\n{3}").format(last.get("copy_rc"), last.get("restore_rc"), log,
                                                                       MANUAL_GUIDE))
        elif flag == "--update-failed":
            log = os.path.join(self.data_root, "logs", "update.log")
            QMessageBox.warning(self, tr("업데이트 실패"),
                                tr("교체가 실패해 이전 버전으로 돌아왔습니다.\n사유: {0} (backup_rc={1}, copy_rc={2}, restore_rc={3})\n로그: {4}\n\n{5}")
                                .format(last.get("result", "?"), last.get("backup_rc"), last.get("copy_rc"), last.get("restore_rc"), log, MANUAL_GUIDE))
        elif flag == "--update-blocked":
            QMessageBox.information(self, tr("업데이트 보류"),
                                    tr("다른 ChatCoach 창이 열려 있어 업데이트를 보류했습니다. 창을 모두 닫고 다시 시도하세요."))

    # ---------------------------------------------------------------- 되돌리기 (04 6-9 · 6-10)
    def rollback_state(self, repair: bool | None = None) -> updater.RollbackStatus:
        """메뉴용 되돌리기 상태. repair 를 주지 않으면 폴더가 섞였을 수 있는지(멈춘 업데이트 · restore_failed ·
        rollback_failed, updater.repair_needed)로 정한다 — 그때는 사본이 지금 버전과 같아도 '복구' 로 켠다."""
        if repair is None:
            repair = updater.repair_needed(self.data_root)
        return updater.rollback_status(self.installed.app_dir, self.data_root, __version__, self.installed.frozen,
                                       self.spec, repair=repair)

    def update_rollback_action(self) -> None:
        """도움말 메뉴를 열 때마다: 되돌릴 수 있을 때만 켠다. 이번 실행이 _backup 을 지울 차례면 끈다."""
        try:
            text, on, tip = rollback_menu_text(self.rollback_state())
        except Exception:  # noqa: BLE001 — 메뉴 문구 때문에 앱이 멈추지 않게
            LOG.exception("되돌리기 상태 확인 실패")
            text, on, tip = tr("이전 버전으로 되돌리기"), False, tr("상태를 확인하지 못했습니다")
        if on and self.delete_backup_pending:
            on, tip = False, tr("이번 실행에서 이전 버전 사본을 정리합니다 (업데이트 뒤 첫 정상 종료 다음 시작). 되돌리기는 "
                                "업데이트 직후 실행에서만 됩니다")
        if on and (self._rolling_back or (self._stage_worker is not None and self._stage_worker.isRunning())):
            on, tip = False, tr("업데이트·되돌리기를 준비하는 중입니다")
        self.a_rollback.setText(text + "…")
        self.a_rollback.setEnabled(on)
        self.a_rollback.setToolTip(tip)

    def _rollback_note(self, st: updater.RollbackStatus) -> str | None:
        """되돌릴 버전이 마지막 매니페스트의 min_supported 보다 낮으면 되돌린 뒤 바로 '업데이트 필요' 가 된다는 1줄."""
        if not st.target_version or st.target_version == st.current_version:
            return None
        sup = updater.load_support_status(self.state_path, st.target_version)
        if sup.needs_update:
            return tr("{0} 은 지원이 끝난 버전입니다 (최소 {1}). 되돌린 뒤에는 새 라벨을 저장할 수 없습니다(열람·내보내기만).") \
                .format(st.target_version, sup.min_supported)
        return None

    def rollback_dialog(self, *, status: updater.RollbackStatus | None = None, reason: str | None = None,
                        confirm: bool = True, run=None) -> bool:
        """'도움말 → 이전 버전으로 되돌리기…' 와 실패 뒤 제안의 [되돌리기] 가 부르는 한 함수.

        status 가 없으면 지금 상태(메뉴). confirm=False 는 시험 자동 모드. run(dlg) 을 주면 exec() 대신(시험).
        되돌리기 교체가 시작되면 True(앱은 곧 끝난다)."""
        st = status or self.rollback_state()
        if not st.available or (status is None and self.delete_backup_pending):
            why = st.reason if not st.available else tr("이번 실행에서는 되돌릴 수 없습니다 (이전 버전 사본 정리 차례)")
            self.statusBar().showMessage(why, 6000)
            LOG.info("되돌리기 불가: %s", st.reason if not st.available else "delete_backup_pending")
            return False
        if self._rolling_back:
            return False
        if confirm:
            dlg = RollbackDialog(st, reason, self, data_backup=True, note=self._rollback_note(st))
            try:
                rc = (run or (lambda d: d.exec()))(dlg)
            finally:
                dlg.deleteLater()
            if rc != RollbackDialog.Accepted:
                LOG.info("되돌리기를 하지 않음 (사용자)")
                return False
        return self.start_rollback(st)

    def start_rollback(self, st: updater.RollbackStatus) -> bool:
        """워커에서 준비(자체 점검 관문, 최대 90초) → 교체 전 조건 → data 백업 → replace.ps1 -Rollback 시작. 성공하면 앱을 끈다."""
        from ..workers import JobCancelled, JobWorker
        box: dict = {}
        spec, installed, droot, sp = self.spec, self.installed, self.data_root, self.state_path
        backup_fn = self.data_backup

        def job(ctx):
            try:
                staged = updater.stage_rollback(spec, installed, droot, repair=st.repair)
            except updater.UpdateError as ex:
                box["err"] = ex.message
                return None
            if ctx.cancelled():
                shutil.rmtree(staged.stage_dir, ignore_errors=True)
                raise JobCancelled()
            try:
                updater.RobocopyApplier(spec, installed, droot, data_backup=backup_fn).rollback(staged, sp)
            except updater.UpdateError as ex:
                box["err"] = ex.message
                return None
            return staged
        self._rolling_back = True
        try:
            w = JobWorker.single("rollback", job, name="되돌리기", parent=self)
            outcome = self.run_job_modal(w, title=tr("이전 버전으로 되돌리기"),
                                         label=tr("이전 버전 사본을 점검하고 되돌리기를 준비하는 중… (최대 90초)"))
            staged = w.results.get("rollback") if outcome == "done" else None
            w.deleteLater()
        finally:
            self._rolling_back = False
        if staged is None:
            err = box.get("err") or (tr("취소했습니다") if outcome == "cancelled" else tr("알 수 없는 오류"))
            LOG.warning("되돌리기 실패: %s", err)
            if self.auto_rollback:
                LOG.error("자동 되돌리기 실패 — %s", err)
            elif outcome != "cancelled":
                self.warn(tr("되돌리기를 진행할 수 없습니다"), err + "\n\n" + tr(MANUAL_GUIDE))
            return False
        LOG.info("되돌리기를 위해 종료 (v%s → v%s)", __version__, staged.target_version)
        self.statusBar().showMessage(tr("이전 버전으로 되돌리려고 종료합니다…"))
        QTimer.singleShot(100, QApplication.instance().quit)
        return True

    def maybe_suggest_rollback(self) -> bool:
        """업데이트 실패 뒤 되돌리기 제안 (updater.rollback_suggestion): 멈춘 업데이트 · 복구 실패 · 업데이트 뒤 연속 비정상 종료."""
        if not self.installed.frozen:
            return False
        try:
            sug = updater.rollback_suggestion(self.installed.app_dir, self.data_root, __version__, self.flags,
                                              self.abnormal_streak, frozen=True, spec=self.spec)
        except Exception:  # noqa: BLE001
            LOG.exception("되돌리기 제안 판정 실패")
            return False
        if sug is None:
            return False
        LOG.info("되돌리기 제안: %s", "repair" if sug.status.repair else "rollback")
        return self.rollback_dialog(status=sug.status, reason=tr(sug.reason))

    # ---------------------------------------------------------------- 위쪽 띠: 업데이트 필요 · 경로
    def refresh_support_banner(self) -> None:
        try:
            st = updater.load_support_status(self.state_path, __version__)
        except Exception:  # noqa: BLE001
            LOG.exception("지원 상태 확인 실패")
            st = None
        self.support_banner.set_status(st)

    def _banner_update(self) -> None:
        """띠의 [업데이트]: 확인한 결과가 있으면 대화상자, 없으면 지금 확인."""
        if self._result is not None and self._result.release is not None:
            self.offer_update()
        else:
            self.manual_check_updates()

    def refresh_path_banner(self) -> None:
        """경로 경고(confirm·error·warn)와 보존 기한 한 줄. info(한글 경로 등)는 로그에만."""
        lines = []
        for it in list(self.startup_path_issues) + list(self.project_path_issues):
            if getattr(it, "level", "info") in ("confirm", "error", "warn"):
                mark = "⛔" if it.level == "error" else "⚠"
                line = f"{mark} {tr(it.message)}"
                if line not in lines:
                    lines.append(line)
        if self.retention_line:
            lines.append(tr("보존 기한: ") + self.retention_line)
        self.path_banner.setText("\n".join(lines))
        self.path_banner.setVisible(bool(lines))

    def confirm_startup_paths(self, *, ask=None) -> bool:
        """앱·데이터·백업 폴더의 confirm 수준 경고(OneDrive·UNC, S-09 ①)를 묻는다. '그래도 계속' 은 settings 에 남겨
        다시 묻지 않는다(띠에는 계속 보인다). 종료를 고르면 False. ask(issue) → True(계속)/False(종료) 는 시험용."""
        for it in self.startup_path_issues:
            if getattr(it, "level", "") != "confirm":
                continue
            key = f"{PATH_ACK_GROUP}/{it.role}_{it.code}"
            if str(self.settings.value(key, "")) == "1":
                continue
            LOG.info("경로 확인 묻기: %s %s", it.role, it.code)
            ok = ask(it) if ask is not None else self._ask_path(it, quit_label=tr("종료"))
            if not ok:
                return False
            self.settings.setValue(key, "1")
            self.settings.sync()
        return True

    def _ask_path(self, it, *, quit_label: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("저장 위치 확인"))
        box.setText(tr(it.message))
        box.setInformativeText(tr("옮긴 뒤 다시 여는 것을 권장합니다. 그래도 이 위치에서 계속할까요?"))
        b_go = box.addButton(tr("그래도 계속"), QMessageBox.AcceptRole)
        box.addButton(quit_label, QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is b_go

    def confirm_project_path(self, outcome, *, ask=None) -> bool:
        """프로젝트를 열 때 경로 점검 (S-09 · S-49). confirm(OneDrive·UNC)이면 '이동 권장/그래도 계속' 을 묻고,
        계속하면 project.json 의 storage_warning_acknowledged 에 남긴다(그 뒤로는 띠에만). False 면 열지 않는다."""
        from .. import paths
        proj = getattr(outcome, "project", None)
        if proj is None:
            return True
        try:
            issues = paths.project_path_issues(proj.root) if not getattr(outcome, "read_only", False) \
                else paths.path_issues(proj.root, "project", check_write=False)
        except Exception:  # noqa: BLE001
            LOG.exception("프로젝트 경로 점검 실패")
            issues = []
        self.project_path_issues = issues
        conf = [i for i in issues if i.level == "confirm"]
        if not conf:
            return True
        data = proj.data or {}
        ack = data.get("storage_warning_acknowledged") if isinstance(data.get("storage_warning_acknowledged"), dict) else None
        kind = "unc" if any(i.code == "unc" for i in conf) else "onedrive"
        if ack and ack.get("path_kind") == kind:
            return True
        ok = ask(conf[0]) if ask is not None else self._ask_path(conf[0], quit_label=tr("열지 않기"))
        if not ok:
            self.project_path_issues = []
            return False
        last = self.settings.value("session/last_labeler", "")
        by = last if isinstance(last, str) and last else None
        if not getattr(proj, "read_only", True):
            stamp = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "by": by or "L01", "path_kind": kind}
            try:
                proj.update(lambda d: d.__setitem__("storage_warning_acknowledged", stamp), by=by)
            except Exception as e:  # noqa: BLE001 — 기록 실패해도 열기는 계속한다
                LOG.warning("storage_warning_acknowledged 기록 실패 (%s)", type(e).__name__)
        return True

    def _note_retention(self, outcome) -> None:
        """보존 기한 상태 (05 S-06): 30일 안이면·지났으면 띠에 1줄."""
        from datetime import date
        from .. import backup
        proj = getattr(outcome, "project", None)
        data = (proj.data if proj is not None else None) or {}
        try:
            st = backup.retention_state(data, date.today())
        except Exception:  # noqa: BLE001
            return
        self.retention_line = tr(backup.retention_message(st)) if st.status in ("soon", "expired") else None

    # ---------------------------------------------------------------- 시작 뒤 백그라운드 (main.py 가 부른다)
    def start_background_backups(self, delay_ms: int = 8000) -> None:
        """창을 보인 뒤: 일 1회 프로젝트 백업(backup.daily_backup_if_due, 02 5-3)을 백그라운드 스레드에서.
        data\\projects\\* + 최근 목록의 프로젝트. 실패해도 앱은 계속한다(로그에는 개수·예외 종류만)."""
        import threading
        from .. import backup
        extra = list(load_recent(self.settings))
        pdir = os.path.join(self.data_root, "projects")

        def work():
            try:
                rep = backup.daily_backup_if_due(pdir, extra_roots=extra, app_version=__version__)
                LOG.info("일일 백업: 만듦 %d · 건너뜀 %d · 실패 %d", len(rep.done), rep.skipped, len(rep.failed))
            except Exception:  # noqa: BLE001
                LOG.exception("일일 백업 실패")
        QTimer.singleShot(delay_ms, lambda: threading.Thread(target=work, name="daily-backup", daemon=True).start())

    # ---------------------------------------------------------------- 도움말
    def copy_report(self) -> None:
        from ..report import build_report
        text = build_report(self.log_file, {"업데이트": self.update_status})
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage(tr("문제 보고를 클립보드에 복사했습니다"), 4000)

    def about(self) -> None:
        keys = ", ".join(f"{k.label} {k.key_id}" for k in self.spec.update_pubkeys) or tr("(없음)")
        QMessageBox.about(self, tr("정보"),
                          tr("{0} {1}\n채널 {2} · 빌드 {3}\n업데이트 공개키: {4}\n저장소: https://github.com/{5}")
                          .format(self.spec.name, __version__, self.installed.channel,
                                  self.installed.built_at.isoformat() if self.installed.built_at else "-", keys, self.spec.repo))
