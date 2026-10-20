# vendored from vtubeadmirer/clipslicer@07d13cc (app/i18n.py) — 변경: 영어 사전(i18n_en) import 분기·시스템 언어 판정 삭제, 한국어 전용 tr() 껍데기, 로거 이름 chatcoach
"""UI 문자열 번역 껍데기. 코드에는 한국어를 그대로 두고 tr("...") 로 감싼다.

ChatCoach 는 한국어 전용이다 (docs/design/02-architecture.md 12-5). 사전이 없으므로 tr() 은 받은 문자열을
그대로 돌려준다. ClipSlicer 와 같은 이름·자리를 유지해 역이식 diff 를 작게 하고, 나중에 사전을 붙일 자리를 남긴다.
"""
from __future__ import annotations

import logging

LOG = logging.getLogger("chatcoach")
LANGUAGES = [("ko", "한국어")]

_lang = "ko"


def set_language(code: str):
    """언어 설정. 어떤 값을 받아도 한국어로 둔다 (사전 없음)."""
    global _lang
    if (code or "ko").lower() != "ko":
        LOG.info("UI 언어 %r 은 지원하지 않아 한국어로 둡니다", code)
    _lang = "ko"
    LOG.info("UI 언어: %s", _lang)


def language() -> str:
    """현재 언어 코드. 항상 'ko'."""
    return _lang


def tr(text: str) -> str:
    """번역 자리. 사전이 없으므로 그대로 돌려준다."""
    return text


def missing() -> set[str]:
    """번역 사전에 없던 문자열 (사전이 없으므로 늘 비어 있다)."""
    return set()
