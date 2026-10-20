# vendored from vtubeadmirer/clipslicer@07d13cc (app/logging_setup.py) — 변경: 로거 이름 chatcoach, 로그 폴더 data_root()\logs(후보 목록 제거), 파일 로그 INFO 기본, 가림 패턴·redact 를 report.py 에서 옮겨 와 모든 레코드에 적용, 시작 줄에 경로 대신 데이터 모드
"""회전 로그 + 처리되지 않은 예외 대화상자 + 개인정보 가림 필터.

로그 위치: paths.data_root()\\logs\\chatcoach.log (1MB × 3개 회전). 폴백은 paths 가 이미 처리한다.
가림(redact): 토큰·쿠키·인증 값, 네이버 로그인 쿠키(NID_AUT·NID_SES), 32자 이상 16진수(userIdHash 류),
사용자 홈 경로(C:\\Users\\<이름>\\ → %USERPROFILE%\\). 파일·콘솔로 나가는 모든 레코드에 적용된다 (S-20).
문제 보고(report.py)도 같은 redact 를 쓴다.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler

from . import paths
from .i18n import tr

LOG = logging.getLogger("chatcoach")
LOG_FILE_NAME = "chatcoach.log"
DATA_MODE_KO = {"portable": "포터블", "fallback": "폴백", "override": "지정(--data-dir)"}
_SEEN: set[str] = set()

# 사용자 이름 한 마디: 경로 구분자·드라이브 콜론·파일명 금지 문자·줄바꿈이 아닌 것
_NAME = r"[^\\/:*?\"<>|\r\n]"

SECRET_PATTERNS = [
    # --- ClipSlicer report.py 와 같은 4개 ---
    (re.compile(r"(?i)\b(authorization|cookie|set-cookie|x-[\w-]*token)\s*[:=]\s*[^\r\n,]+"), r"\1: ***"),
    (re.compile(r"(?i)([?&](?:token|sig|signature|key|auth|session|policy|expires|hdnts|_lsu_sa_)=)[^&\s\"']+"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)[\w\-.~+/]+=*"), r"\1***"),
    (re.compile(r"(?i)(\"?(?:access_token|refresh_token|client_secret)\"?\s*[:=]\s*\"?)[^\"\s,}]+"), r"\1***"),
    # --- ChatCoach 추가 ---
    # 네이버 로그인 쿠키 값. 'cookie:' 머리 없이 따로 찍힌 경우 (NID_AUT=…, "NID_SES": "…")
    (re.compile(r"(?i)(\bNID_(?:AUT|SES)\b[\"']?\s*[=:]\s*[\"']?)[^;\s\"',&}]+"), r"\1***"),
    # 32자 이상 16진수 (userIdHash 32자 · sha1 40자 · sha256 64자 식별자)
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<hex>"),
    # 사용자 홈 경로: C:\Users\<이름>\ → %USERPROFILE%\ (구분자가 / 이거나 \\ 로 겹친 repr 표기도).
    # 이름 뒤에 구분자가 오면 공백 포함 이름 전체를, 아니면 공백 앞까지만 가린다.
    (re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?:" + _NAME + r"+(?=[\\/])|[^\\/:*?\"<>|\r\n\s'\",;)\]]+)"),
     "%USERPROFILE%"),
]


def redact(text: str) -> str:
    """비밀 값·식별자·사용자 홈 경로를 가린다."""
    for pat, rep in SECRET_PATTERNS:
        text = pat.sub(rep, text)
    return text


class RedactFilter(logging.Filter):
    """레코드의 메시지·예외·스택 문자열에 redact() 를 적용한다. 핸들러마다 붙인다
    (로거에 붙이면 자식 로거에서 올라온 레코드는 거치지 않는다)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # 형식 문자열이 틀린 레코드는 그대로 둔다 (logging 이 오류를 따로 알린다)
            return True
        record.msg = redact(msg)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def log_dir() -> str:
    """로그 폴더 data_root()\\logs (없으면 만든다)."""
    d = os.path.join(paths.data_root(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def log_file() -> str:
    """현재 로그 파일 경로."""
    return os.path.join(log_dir(), LOG_FILE_NAME)


def setup(version: str, file_level: int = logging.INFO) -> str:
    """파일 로그 핸들러(기본 INFO)와 예외 훅 설치. 로그 파일 경로를 돌려준다. 두 번 불러도 핸들러가 겹치지 않는다."""
    for h in [h for h in LOG.handlers if getattr(h, "_sl_installed", False)]:
        LOG.removeHandler(h)
        h.close()
    LOG.setLevel(min(file_level, logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    flt = RedactFilter()
    path = ""
    try:
        path = log_file()
        fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(file_level)
        fh.addFilter(flt)
        fh._sl_installed = True
        LOG.addHandler(fh)
    except OSError:
        pass
    if sys.stderr is not None:  # 콘솔이 있을 때만 (windowed exe 는 None)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        sh.addFilter(flt)
        sh._sl_installed = True
        LOG.addHandler(sh)
    try:
        mode = DATA_MODE_KO.get(paths.data_mode(), paths.data_mode())
    except OSError:
        mode = "알 수 없음"
    LOG.info("=== ChatCoach %s 시작 | Python %s | %s %s | 데이터 폴더 %s",
             version, platform.python_version(), platform.system(), platform.release(), mode)

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        LOG.error("처리되지 않은 예외:\n%s", text)
        _report(redact(text))

    def _thread_hook(args):
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
    return path


# ---- 오류 대화상자 (메인 스레드에서만, 같은 오류는 한 번만) ----
_reporter = None


def _report(text: str):
    key = text.strip().splitlines()[-1] if text.strip() else text
    if key in _SEEN:
        return
    _SEEN.add(key)
    if _reporter is not None:
        _reporter.report.emit(text)


def install_error_dialog(parent):
    """QApplication 이 뜬 뒤에 호출. 백그라운드 스레드의 예외도 메인 스레드에서 대화상자로 보여 준다."""
    global _reporter
    from PySide6.QtCore import QObject, Signal, Qt
    from PySide6.QtWidgets import QMessageBox, QPushButton

    class Reporter(QObject):
        report = Signal(str)

    _reporter = Reporter()

    def show(text: str):
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(tr("문제가 생겼습니다"))
        last = text.strip().splitlines()[-1] if text.strip() else tr("알 수 없는 오류")
        box.setText(tr("{0}\n\n프로그램은 계속 쓸 수 있지만, 방금 동작은 완료되지 않았을 수 있습니다.").format(last))
        box.setDetailedText(text)
        b_log = QPushButton(tr("로그 폴더 열기"))
        box.addButton(b_log, QMessageBox.ActionRole)
        box.addButton(tr("닫기"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_log:
            try:
                os.startfile(log_dir())
            except OSError:
                pass

    _reporter.report.connect(show, Qt.QueuedConnection)
