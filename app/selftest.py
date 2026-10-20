# vendored from vtubeadmirer/clipslicer@07d13cc (app/selftest.py) — 변경: 틀은 유지, 점검 항목 교체(02 11-3),
#   --expect-version · --expect-channel · --data-dir 추가, PATH 를 비운 상태의 번들 ffmpeg 검증, 공개키 점검.
"""ChatCoach.exe --selftest <결과.json> [--expect-version X.Y.Z] [--expect-channel release|test] [--data-dir D]

창 없이 핵심 기능을 점검하고 결과 JSON 과 종료 코드(0 = 모두 통과)로 끝낸다. 두 곳에서 쓴다.
① release.yml 이 빌드 직후 한글 폴더('테스트 문서') 사본에서 (tools/ci_selftest.py).
② 업데이터가 교체 직전, 준비 폴더의 새 exe 로 --expect-version 과 --data-dir _update\\<id>\\selftest-data 를 붙여서.
   이때 --expect-channel 은 지금 실행 중인 설치본의 채널이다. 그래서 test → test 시험 업데이트도 관문을 통과한다
   (02 11-3 의 열린 질문을 이렇게 닫는다. CI 는 --expect-channel release 를 준다).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _check(results: dict, name: str, fn):
    try:
        detail = fn()
        results[name] = {"ok": True, "detail": str(detail)[:300]}
    except Exception as ex:  # 점검은 하나가 실패해도 나머지를 계속한다
        results[name] = {"ok": False, "detail": f"{type(ex).__name__}: {ex}"[:500],
                         "trace": traceback.format_exc()[-1500:]}


def _version(expect: str | None) -> str:
    from . import __version__
    from .paths import code_dir
    frozen = bool(getattr(sys, "frozen", False))
    bundled = None
    if frozen:
        with open(os.path.join(code_dir(), "VERSION"), encoding="utf-8") as f:
            bundled = f.read().strip().lstrip("\ufeff")
        if bundled != __version__:
            raise RuntimeError(f"번들 VERSION({bundled}) 과 __version__({__version__}) 이 다릅니다")
    if expect and expect != __version__:
        raise RuntimeError(f"기대 버전({expect}) 과 __version__({__version__}) 이 다릅니다")
    return __version__


def _channel(expect: str | None) -> str:
    from .updater import load_installed
    inst = load_installed()
    want = expect or ("release" if inst.frozen else None)
    if want and inst.channel != want:
        raise RuntimeError(f"채널이 {inst.channel!r} (기대 {want!r})")
    if inst.frozen and inst.built_at is None:
        raise RuntimeError("build_info.json 의 built_at 를 읽을 수 없습니다")
    return f"{inst.channel} built_at={inst.built_at.isoformat() if inst.built_at else '-'}"


def _pubkeys() -> str:
    from .appspec import SPEC
    from .updater import TEST_PUBKEY_ENV, effective_pubkeys, load_installed
    from . import minisign
    inst = load_installed()
    ids = []
    for k in SPEC.update_pubkeys:
        kid, _ = minisign.parse_public_key(k.line)
        ids.append(f"{k.label}={minisign.key_id_hex(kid)}")
    if inst.channel == "release":
        if not SPEC.update_pubkeys:
            raise RuntimeError("내장 공개키가 없습니다 (appspec.UPDATE_PUBKEYS)")
        if len(SPEC.update_pubkeys) < 2:
            raise RuntimeError("공개키는 K1·K2 두 개여야 합니다 (04 9-2)")
        fake = {TEST_PUBKEY_ENV: "RWTAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="}
        if effective_pubkeys(SPEC, inst, fake) != [(k.label, k.line) for k in SPEC.update_pubkeys]:
            raise RuntimeError("release 채널인데 환경 변수의 시험 키를 읽습니다")
        return "release: " + ", ".join(ids)
    override = os.environ.get(TEST_PUBKEY_ENV, "")
    return f"{inst.channel}: 내장 {len(ids)}개, 환경 변수 시험 키 {'있음' if override else '없음'}"


def _tool_version(name: str) -> str:
    from .paths import find_tool
    exe = find_tool(name)
    if not exe:
        raise FileNotFoundError(name)
    env = dict(os.environ)
    env["PATH"] = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")   # 번들 DLL 만으로 뜨는지
    out = subprocess.run([exe, "-version"], capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"{name} exit {out.returncode}")
    return out.stdout.decode("utf-8", "replace").splitlines()[0]


def _mpv() -> str:
    from .paths import import_mpv, libmpv_dir, code_dir
    mod = import_mpv()
    backend = getattr(mod, "backend", None)
    loaded = ""
    if backend is not None and os.name == "nt":
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        ctypes.windll.kernel32.GetModuleFileNameW(ctypes.c_void_p(backend._handle), buf, 32768)
        loaded = buf.value
    expected = libmpv_dir()
    if getattr(sys, "frozen", False):
        if not loaded or os.path.normcase(os.path.dirname(loaded)) != os.path.normcase(code_dir()):
            raise RuntimeError(f"libmpv 가 번들 밖에서 로드됨: {loaded!r} (기대 {code_dir()})")
    return loaded or f"loaded from {expected}"


def _qt_plugins() -> str:
    from PySide6.QtCore import QLibraryInfo
    from .paths import code_dir
    p = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    if getattr(sys, "frozen", False) and not os.path.normcase(os.path.abspath(p)).startswith(os.path.normcase(code_dir())):
        raise RuntimeError(f"Qt 플러그인 경로가 번들 밖: {p}")
    return p


def _settings() -> str:
    from .paths import data_root, data_mode, make_settings, settings_path
    root = data_root()
    s = make_settings()
    s.setValue("selftest/ping", "pong")
    s.sync()
    if s.status() != s.Status.NoError:
        raise RuntimeError(f"QSettings 쓰기 실패: {s.status()}")
    s.remove("selftest/ping")
    s.sync()
    return f"{data_mode()} {settings_path()} ({root})"


def _korean_path() -> str:
    from .paths import data_root
    d = os.path.join(data_root(), "selftest", "테스트 문서")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "확인.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("한글 경로 쓰기\n")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    os.remove(p)
    os.removedirs(d)
    if text != "한글 경로 쓰기\n":
        raise RuntimeError("되읽은 내용이 다릅니다")
    return p


def _updater() -> str:
    import shutil
    import tempfile
    from . import updater
    from .paths import data_root, install_root
    root = data_root()
    lock = updater.write_updating_lock(root, os.getpid(), "0.0.0", "0.0.0")
    if not updater.update_in_progress(root):
        raise RuntimeError("잠금 파일 판정 실패")
    os.remove(lock)
    upd = os.path.join(install_root(), "_update")
    os.makedirs(upd, exist_ok=True)
    d = tempfile.mkdtemp(prefix=updater.STAGE_PREFIX, dir=upd)
    shutil.rmtree(d)
    if len(updater.render_replacer()) < 1000:
        raise RuntimeError("교체 스크립트 생성 실패")
    return f"lock ok, stage dir ok ({upd})"


def run(out_path: str, expect_version: str | None = None, expect_channel: str | None = None) -> int:
    from . import __version__
    from .paths import install_root, data_root
    results: dict = {}
    _check(results, "version", lambda: _version(expect_version))
    _check(results, "channel", lambda: _channel(expect_channel))
    _check(results, "pubkeys", _pubkeys)
    _check(results, "ffmpeg", lambda: _tool_version("ffmpeg"))
    _check(results, "ffprobe", lambda: _tool_version("ffprobe"))
    _check(results, "libmpv", _mpv)
    _check(results, "qt_plugins", _qt_plugins)
    _check(results, "settings", _settings)
    _check(results, "korean_path", _korean_path)
    _check(results, "updater", _updater)
    ok = all(r["ok"] for r in results.values())
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)), "dir": install_root(),
              "data": data_root(), "ok": ok, "checks": results}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return 0 if ok else 1
