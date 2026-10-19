"""업데이트 테스트 공용 도구 — 가짜 빌드 폴더 → package_release → make_manifest → 시험 키 서명.

test_update_manifest · test_update_zip · test_update_download 가 같은 흐름을 쓴다.
pytest 가 테스트로 모으지 않는 이름(test_ 로 시작하지 않음)이다. 네트워크를 쓰지 않는다.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
for _p in (ROOT, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import make_manifest  # noqa: E402
import package_release  # noqa: E402
import sign_manifest as sm  # noqa: E402

from app import updater  # noqa: E402
from app.appspec import SPEC  # noqa: E402

VERSION = "0.0.2"
# 매니페스트 deps 와 설치본 deps.txt 가 일치하는 값 (경량 선택 조건)
DEPS = {"ffmpeg": {"tag": "a", "sha256": "0" * 64}, "libmpv": {"tag": "b", "sha256": "1" * 64}}
LOCAL_DEPS = {"ffmpeg": "a", "libmpv": "b", "ffmpeg_sha256": "0" * 64, "libmpv_sha256": "1" * 64}
DEPS_TEXT = "".join(f"{k}={v}\n" for k, v in LOCAL_DEPS.items())


def fake_app(app_dir: str, version: str, heavy: bool = True, exe: bytes = b"EXE" * 1000) -> str:
    """빌드 폴더(<dist>\\StreamLabeler) 흉내. data\\ 는 zip 에 들어가면 안 되는 보존 폴더다."""
    internal = os.path.join(app_dir, "_internal")
    os.makedirs(internal, exist_ok=True)
    _w(os.path.join(app_dir, SPEC.exe_name), exe)
    _w(os.path.join(internal, "python312.dll"), b"DLL" * 100)
    _w(os.path.join(internal, "VERSION"), (version + "\n").encode("ascii"))
    _w(os.path.join(internal, "deps.txt"), DEPS_TEXT.encode("ascii"))
    _w(os.path.join(internal, "lib", "core.pyd"), b"PYD" * 50)
    if heavy:
        _w(os.path.join(internal, "libmpv-2.dll"), b"MPV" * 100)
        _w(os.path.join(internal, "ffmpeg", "ffmpeg.exe"), b"FF" * 100)
        _w(os.path.join(internal, "ffmpeg", "avcodec-61.dll"), b"AV" * 100)
    _w(os.path.join(app_dir, "data", "settings.ini"), b"[x]\nk=1\n")
    return app_dir


def _w(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def write_notes(path: str, min_supported: str = "0.0.1", mandatory: bool = False, body: str = "시험 노트.\n") -> str:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"min_supported: {min_supported}\nmandatory: {'true' if mandatory else 'false'}\n---\n{body}")
    return path


def dumps(obj) -> bytes:
    """make_manifest.write 와 같은 직렬화."""
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


@dataclass
class SignedRelease:
    work: str
    dist_app: str            # <dist>\StreamLabeler
    out: str                 # zip 두 개 · latest.json · latest.json.minisig
    notes: str
    version: str
    manifest: dict
    data: bytes
    sig: bytes
    kp: "sm.KeyPair"
    key_path: str            # 암호 없는 시험 비밀키 파일 (가짜 서버의 old_version·old_published 재서명용)

    @property
    def keys(self) -> list[tuple[str, str]]:
        return [("TEST", self.kp.public_line())]

    @property
    def full_zip(self) -> str:
        return os.path.join(self.out, SPEC.asset_name(self.version, "full"))

    @property
    def lite_zip(self) -> str:
        return os.path.join(self.out, SPEC.asset_name(self.version, "lite"))

    def sign(self, data: bytes, comment_version: str | None = None, kp: "sm.KeyPair | None" = None) -> bytes:
        return sm.sign_bytes(data, kp or self.kp, SPEC.trusted_comment(comment_version or self.version)).encode("utf-8")

    def variant(self, mutate, comment_version: str | None = None) -> tuple[bytes, bytes]:
        """매니페스트 사본을 mutate(obj) 로 제자리에서 고친 뒤 다시 직렬화·서명한다. (data, sig).
        mutate 의 반환값은 쓰지 않는다 (dict.pop 같은 반환값이 매니페스트를 대신하지 않게)."""
        obj = copy.deepcopy(self.manifest)
        mutate(obj)
        data = dumps(obj)
        if comment_version is None:
            v = obj.get("version") if isinstance(obj, dict) else None
            comment_version = v if isinstance(v, str) and updater.VERSION_RE.match(v) else self.version
        return data, self.sign(data, comment_version)


def build_release(work: str, version: str = VERSION, published_at: str | None = None) -> SignedRelease:
    """스모크 스크립트(smoke_updater.py)의 흐름: 가짜 빌드 → zip 두 개 → latest.json → 서명."""
    os.makedirs(work, exist_ok=True)
    dist = os.path.join(work, "dist")
    app = fake_app(os.path.join(dist, SPEC.zip_top), version)
    out = os.path.join(work, "out")
    notes = write_notes(os.path.join(work, f"v{version}.md"))
    args = ["--dist", dist, "--out", out, "--version", version, "--notes", notes]
    if published_at:
        args += ["--published-at", published_at]
    rc = package_release.main(args)
    assert rc == 0
    full = os.path.join(out, SPEC.asset_name(version, "full"))
    lite = os.path.join(out, SPEC.asset_name(version, "lite"))
    # package_release 는 deps 를 tools/get_*.py 상수로 채운다. 시험용 deps 로 다시 만든다
    m = make_manifest.build(version, full, lite, notes, published_at, deps=copy.deepcopy(DEPS))
    data = make_manifest.write(m, os.path.join(out, "latest.json"))
    kp = sm.generate_keypair()
    sig = sm.sign_bytes(data, kp, SPEC.trusted_comment(version)).encode("utf-8")
    with open(os.path.join(out, "latest.json.minisig"), "wb") as f:
        f.write(sig)
    keydir = os.path.join(work, "keys")
    os.makedirs(keydir, exist_ok=True)
    key_path = os.path.join(keydir, "test.key")
    with open(key_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(sm.secret_file_text(kp, None))
    return SignedRelease(work, app, out, notes, version, m, data, sig, kp, key_path)


def yesterday() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)


_UNSET = object()


def installed(app_dir: str, version: str = "0.0.1", channel: str = "test", built_at=_UNSET,
              frozen: bool = True, deps: dict | None = None, has_heavy: bool = True) -> "updater.InstalledInfo":
    """시험 설치본 정보. built_at 을 주지 않으면 어제(UTC), None 을 주면 '빌드 시각 없음'."""
    return updater.InstalledInfo(version, channel, yesterday() if built_at is _UNSET else built_at, app_dir,
                                 os.path.join(app_dir, "_internal"), frozen,
                                 dict(LOCAL_DEPS) if deps is None else deps, has_heavy)
