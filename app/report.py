# vendored from vtubeadmirer/clipslicer@07d13cc (app/report.py) — 변경: redact·SECRET_PATTERNS 를 logging_setup 으로 옮김, 머리에 폴더 경로 대신 데이터 모드(포터블/폴백)와 빌드 채널
"""문제 보고용 문자열: 버전 · 윈도우 · 데이터 모드 · 빌드 채널 · 최근 로그.

폴더 경로는 넣지 않는다. 로그 본문은 logging_setup.redact 로 한 번 더 가린다
(토큰·쿠키·네이버 로그인 쿠키·userIdHash 류·사용자 홈 경로, S-20).
"""
from __future__ import annotations

import json
import os
import platform
import sys

from .logging_setup import DATA_MODE_KO, SECRET_PATTERNS, redact  # noqa: F401  (SECRET_PATTERNS 는 예전 import 자리 호환)


def tail_lines(path: str, n: int = 120) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


def _build_channel() -> str:
    """번들의 build_info.json 의 channel. 소스 실행이거나 읽지 못하면 'source'."""
    from . import paths
    try:
        with open(os.path.join(paths.code_dir(), "build_info.json"), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return "source"
    ch = info.get("channel") if isinstance(info, dict) else None
    return ch if isinstance(ch, str) and ch else "source"


def build_report(log_file: str, extra: dict | None = None, lines: int = 120) -> str:
    from . import __version__
    from . import paths
    from .appspec import SPEC
    try:
        mode = paths.data_mode()
    except OSError:
        mode = "?"
    head = [
        f"{SPEC.name} {__version__} ({'exe' if getattr(sys, 'frozen', False) else 'source'})",
        f"Windows: {platform.platform()}",
        f"Python: {platform.python_version()}",
        f"데이터: {DATA_MODE_KO.get(mode, mode)}",
        f"채널: {_build_channel()}",
    ]
    for k, v in (extra or {}).items():
        head.append(f"{k}: {v}")
    body = tail_lines(log_file, lines) if log_file else []
    name = os.path.basename(log_file) if log_file else "-"
    return redact("\n".join(head + ["", f"--- 최근 로그 {len(body)}줄 ({name}) ---"] + body)) + "\n"
