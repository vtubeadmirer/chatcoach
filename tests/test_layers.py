"""층 규칙 검사 (docs/design/02-architecture.md 3-1 '거꾸로 가는 import 금지').

app/core/*.py 는 순수 계산만 한다: Qt·네트워크·프로세스·시계·난수를 import 하지 않고, 파일을 열지 않고,
core 밖의 app 모듈(입출력·기반 층)을 import 하지 않는다. ast 로 읽어 검사한다(실행하지 않는다).
os.path 의 문자열 조작(join·basename 등)은 허용한다. os 의 파일 입출력 함수는 금지한다.
"""
import ast
import glob
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "app", "core")

# 최상위 모듈 이름 기준으로 금지
BANNED_MODULES = {
    "PySide6", "PyQt5", "PyQt6", "shiboken6",
    "urllib", "http", "socket", "ssl", "requests", "ftplib", "smtplib",
    "subprocess", "multiprocessing", "threading", "asyncio",
    "time", "random", "secrets",
    "io", "shutil", "tempfile", "pathlib", "glob", "sqlite3", "zipfile", "gzip",
    "logging",
}
# os 에서 허용하는 것: os.path(문자열 조작)·os.sep 등 상수. 그 밖의 os.X 호출은 금지.
OS_ALLOWED_ATTRS = {"path", "sep", "altsep", "linesep", "curdir", "pardir", "extsep", "name", "fspath"}
OS_PATH_BANNED = {"exists", "isfile", "isdir", "getsize", "getmtime", "getctime", "getatime",
                  "lexists", "islink", "ismount", "samefile", "realpath", "abspath", "expanduser"}
BANNED_CALL_NAMES = {"open", "input", "exec", "eval", "__import__"}
# (객체 이름, 속성) 호출 금지: 시계·난수
BANNED_ATTR_CALLS = {
    ("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"), ("date", "today"),
    ("uuid", "uuid1"), ("uuid", "uuid4"), ("importlib", "import_module"),
}
BANNED_ATTR_ANY = {"urandom", "read_text", "read_bytes", "write_text", "write_bytes"}


def _core_files():
    files = sorted(glob.glob(os.path.join(CORE, "*.py")))
    assert files, "app/core 가 비어 있습니다"
    return files


def _dotted(node):
    """a.b.c 형태의 이름. 아니면 None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def violations(src: str, filename: str = "<src>") -> list[str]:
    tree = ast.parse(src, filename=filename)
    out = []
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in BANNED_MODULES:
                    out.append(f"{ln}: import {a.name}")
                if top == "app" and not a.name.startswith("app.core"):
                    out.append(f"{ln}: core 밖 app 모듈 import {a.name}")
                if a.name.startswith("os.") and a.name != "os.path":
                    out.append(f"{ln}: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0]
            if node.level == 0:
                if top in BANNED_MODULES:
                    out.append(f"{ln}: from {mod} import")
                if top == "app" and not mod.startswith("app.core"):
                    out.append(f"{ln}: core 밖 app 모듈 import {mod}")
                if top == "os":
                    for a in node.names:
                        if mod == "os" and a.name not in OS_ALLOWED_ATTRS:
                            out.append(f"{ln}: from os import {a.name}")
                        if mod == "os.path" and a.name in OS_PATH_BANNED:
                            out.append(f"{ln}: from os.path import {a.name}")
                if mod in ("datetime",):
                    pass  # 클래스 import 는 허용, .now() 호출은 아래에서 막는다
            elif node.level >= 2:
                out.append(f"{ln}: core 밖으로 상대 import (level {node.level}) {mod}")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in BANNED_CALL_NAMES:
                out.append(f"{ln}: {f.id}() 호출")
            if isinstance(f, ast.Attribute):
                name = _dotted(f) or ""
                owner = name.rsplit(".", 2)[-2] if name.count(".") >= 1 else ""
                if (owner, f.attr) in BANNED_ATTR_CALLS:
                    out.append(f"{ln}: {name}() 호출")
                if f.attr in BANNED_ATTR_ANY:
                    out.append(f"{ln}: {name or f.attr}() 호출")
                if name.startswith("os.") and not name.startswith("os.path.") and name != "os.fspath":
                    out.append(f"{ln}: {name}() 호출")
                if name.startswith("os.path.") and f.attr in OS_PATH_BANNED:
                    out.append(f"{ln}: {name}() 호출 (파일 시스템 조회)")
                if name in ("io.open", "builtins.open", "codecs.open"):
                    out.append(f"{ln}: {name}() 호출")
        elif isinstance(node, ast.Name) and node.id == "builtins":
            out.append(f"{ln}: builtins 사용")
    return out


@pytest.mark.parametrize("path", _core_files(), ids=os.path.basename)
def test_core_module_is_pure(path):
    with open(path, "rb") as f:
        src = f.read().decode("utf-8")
    v = violations(src, path)
    assert not v, f"{os.path.relpath(path, ROOT)} 층 규칙 위반:\n" + "\n".join(v)


def test_ids_is_checked():
    names = {os.path.basename(p) for p in _core_files()}
    assert "ids.py" in names


@pytest.mark.parametrize("src", [
    "import PySide6.QtCore",
    "from PySide6 import QtCore",
    "import urllib.request",
    "import socket",
    "import subprocess",
    "import time",
    "from random import random",
    "import app.paths",
    "from app import jsonl",
    "from .. import paths",
    "open('x')",
    "import os\nos.remove('x')",
    "import os\nos.urandom(4)",
    "import os\nos.path.exists('x')",
    "from os import listdir",
    "from datetime import datetime\ndatetime.now()",
    "import datetime\ndatetime.datetime.now()",
    "from datetime import date\ndate.today()",
    "import uuid\nuuid.uuid4()",
    "from pathlib import Path",
    "import builtins\nbuiltins.open('x')",
])
def test_checker_catches(src):
    assert violations(src), src


@pytest.mark.parametrize("src", [
    "import hashlib, hmac, base64, re, json, math",
    "from datetime import datetime\ndatetime.fromisoformat('2026-10-20T00:00:00+09:00')",
    "import os\nos.path.join('a', 'b')",
    "from . import ids",
    "from .ids import chat_id_base",
    "from app.core import ids",
    "import uuid\nuuid.UUID('12345678-1234-4abc-8def-1234567890ab')",
])
def test_checker_allows(src):
    assert not violations(src), violations(src)


# --------------------------------------------------------------------------- 입출력 층 · engine 분리
# 02 3-1: 입출력 층(store·jsonl·session·chzzk_chat·media·analysis·project 등)은 Qt 위젯·ui·workers 를 import 하지 않는다.
# engine/ 은 별도 venv(.venv-engine)에서 도는 패키지라 app 과 서로 import 하지 않는다 (asr.json 파일로만 주고받는다).
IO_MODULES = ["jsonl", "chzzk_chat", "media", "analysis", "project", "store", "session", "export", "backup"]
IO_BANNED = {"PySide6", "PyQt5", "PyQt6", "shiboken6"}
IO_BANNED_APP = {"ui", "workers", "player"}


def _imports(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = ("." * node.level) + (node.module or "")
            out.append(base)
            out += [f"{base}.{a.name}" if base and not base.endswith(".") else base + a.name for a in node.names]
    return out


def _io_files():
    return [p for p in (os.path.join(ROOT, "app", f"{m}.py") for m in IO_MODULES) if os.path.exists(p)]


@pytest.mark.parametrize("path", _io_files(), ids=os.path.basename)
def test_io_module_has_no_qt(path):
    bad = []
    for name in _imports(path):
        parts = name.lstrip(".").split(".")
        if parts[0] in IO_BANNED:
            bad.append(name)
        if parts[0] == "app" and len(parts) > 1 and parts[1] in IO_BANNED_APP:
            bad.append(name)
        if name.startswith(".") and not name.startswith("..") and parts[0] in IO_BANNED_APP:
            bad.append(name)
        if parts[0] == "engine":
            bad.append(name)
    assert not bad, f"{os.path.basename(path)}: {bad}"


# engine/ 이 쓸 수 있는 app 모듈: 순수 계산(app.core.*)과 JSONL 읽기·쓰기(app.jsonl)뿐. 결정 2026-10-20(수정 담당).
ENGINE_ALLOWED_APP = ("app.core", "app.jsonl")


def _engine_app_import_ok(name: str) -> bool:
    if name == "app":
        return False
    return any(name == a or name.startswith(a + ".") for a in ENGINE_ALLOWED_APP)


def test_engine_does_not_import_app():
    files = sorted(glob.glob(os.path.join(ROOT, "engine", "*.py")))
    assert files
    bad = [(os.path.basename(p), n) for p in files for n in _imports(p)
           if n.startswith("..") or n.split(".")[0] == "PySide6"
           or (n.split(".")[0] == "app" and not _engine_app_import_ok(n))]
    assert not bad, bad


def test_engine_app_allowlist_rules():
    """허용 목록이 app.core·app.jsonl 밖으로 새지 않는다(재현: link.py 의 app.core.rules 는 통과, app.paths 는 막힘)."""
    assert _engine_app_import_ok("app.core.rules") and _engine_app_import_ok("app.core.rules.RuleConfig")
    assert _engine_app_import_ok("app.jsonl") and _engine_app_import_ok("app.core")
    for n in ("app", "app.paths", "app.updater", "app.jsonlx", "app.corex", "app.project"):
        assert not _engine_app_import_ok(n), n
