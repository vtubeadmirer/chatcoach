# vendored from vtubeadmirer/clipslicer@07d13cc (app/updater.py) — 변경: 확인·준비·적용 분리, 서명 필수 검증(fail-closed),
#   보존 목록을 appspec 한 곳에서 생성(data·_backup·_update), zip 레이아웃·크기 검사, 다운그레이드·발행 시각 하한 거부,
#   준비 폴더 <앱>\_update\<임의 이름>, 교체 전 --selftest 관문, 교체 스크립트 시작 표식 10초 대기, 백업 rc 검사,
#   보조 프로세스 강제 종료 삭제, 3상태 확인과 24h/1h 재시도, 1차 고정 링크 → 2차 REST API. Qt 를 import 하지 않는다.
#   M4: 되돌리기(-Rollback, 같은 관문), 실패 뒤 제안, min_supported·mandatory 정책, beta(prerelease) 채널.
r"""업데이트: 확인(check) → 준비(stage) → 적용(Applier).

흐름과 규칙은 docs/design/04-update-and-release.md 5·6장이 정한다. 여기서는 '어느 함수가 무엇을 지키는가' 만 적는다.

- check:  latest.json + .minisig 를 받아 크기 상한 → 서명(app/minisign.py, 내장 공개키) → 엄격 JSON → 필드 →
          버전·발행 시각 하한 → 자산 규칙 순으로 검사한다 (5-4). 하나라도 틀리면 '새 버전 아님' 이 아니라 '실패(사유)' 다.
          상태는 셋 중 하나: newer / latest / failed. 확인 시각은 결과가 나온 뒤에 쓴다 (S-41).
- stage:  <앱>\_update\<임의 이름>\ 에 받고(크기 정확 일치, sha256), validate_zip 의 7가지 검사를 거쳐 풀고,
          새 exe 를 --selftest --expect-version 으로 돌려 통과해야 StagedUpdate 를 돌려준다 (6-4~6-6).
- apply:  RobocopyApplier 가 updating.lock 을 먼저 쓰고 appspec 값으로 생성한 replace.ps1 을 띄운 뒤,
          시작 표식을 최대 10초 기다린다. 표식이 없으면 앱을 끄지 않는다 (6-8, S-17).
- rollback (M4, 6-10): rollback_status 가 '지금 되돌릴 수 있는가' 를 판정한다(_backup 이 있고 backup_ok.json 이
          그 사본의 버전을 가리키고 지금 버전과 다름). _backup 은 '첫 정상 종료 다음 시작' 에 지워지므로
          메뉴는 그동안만 켜진다. stage_rollback 이 _backup 의 exe 로 같은 자체 점검 관문을 돌리고,
          RobocopyApplier.rollback 이 같은 replace.ps1 을 -Rollback(= -New <앱>\\_backup -NoBackup)으로 띄운다.
          되돌린 버전은 skip_version 에 넣는다. rollback_suggestion 은 멈춘 업데이트 · 복구 실패 ·
          업데이트 뒤 연속 2회 비정상 종료 때 같은 함수를 권한다.
- 정책 (5-5): min_supported·mandatory 는 마지막으로 받은 서명 매니페스트 값을 state.json 에 남기고
          support_status 가 '새 라벨 저장 막음'·'필수 업데이트 대기' 를 판정한다. update_channel=beta 면
          REST API 목록에서 초안을 뺀 가장 높은 버전(prerelease 포함)을 본다 (질문 45 의 내부 확인 단계).

이 모듈은 PySide6 를 import 하지 않는다. 워커(QThread)는 app/workers.py 가 감싼다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Iterable, Protocol

from . import minisign
from .appspec import SPEC, AppSpec, VERSION_RE

LOG = logging.getLogger("chatcoach")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0
TEST_PUBKEY_ENV = "CHATCOACH_TEST_PUBKEY"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STAGE_PREFIX = "upd-"
ACTIVE_MARKER = "ACTIVE"      # 준비 폴더가 사용 중(pid). 그 pid 가 살아 있으면 정리하지 않는다
FAILED_MARKER = "FAILED"      # 자체 점검 실패로 원인 분석용으로 남긴 폴더
UPDATE_CHANNELS = ("stable", "beta")   # 숨은 설정 update_channel (04 6-2). beta = prerelease 포함 (개발 PC 내부 확인용)
ROLLBACK_FLAG = "--rolled-back"        # 되돌리기 뒤 재실행 인자 (main.STARTUP_FLAGS 에 더할 것 — 통합 담당)


class UpdateError(Exception):
    """업데이트 과정의 실패. kind 로 화면 문구를 고른다.

    kind: network(확인 실패) · signature(서명 검증 실패) · format(매니페스트 형식) · manual(수동 업데이트 필요)
          · pubkeys(공개키 없음) · download · verify(해시·zip) · selftest · blocked(교체 스크립트 시작 안 됨) · precheck
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


# --------------------------------------------------------------------------- 버전
def parse_version(text: str) -> tuple[int, int, int]:
    """'0.1.3' → (0, 1, 3). 형식이 아니면 ValueError. 'v' 접두어와 공백은 허용하지 않는다 (매니페스트는 엄격)."""
    if not isinstance(text, str) or not VERSION_RE.match(text):
        raise ValueError(f"버전 형식이 아닙니다: {text!r}")
    a, b, c = text.split(".")
    return int(a), int(b), int(c)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def parse_iso_utc(text: str) -> datetime:
    """ISO 8601 → tz-aware datetime. 시간대가 없으면 거부."""
    if not isinstance(text, str):
        raise ValueError("시각이 문자열이 아닙니다")
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"시각에 시간대가 없습니다: {text}")
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- 설치본
@dataclass(frozen=True)
class InstalledInfo:
    version: str
    channel: str                       # "release" | "test" | "source"
    built_at: datetime | None          # build_info.json 의 built_at. 소스 실행이면 None
    app_dir: str                       # <앱>
    code_dir: str                      # <앱>\_internal (소스면 저장소 루트)
    frozen: bool
    deps: dict[str, str] = field(default_factory=dict)   # _internal\deps.txt
    has_heavy: bool = False            # ffmpeg\ffmpeg.exe 와 libmpv-2.dll 이 있는가


def parse_deps(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def read_build_info(code_dir: str) -> dict:
    p = os.path.join(code_dir, "build_info.json")
    try:
        with open(p, "rb") as f:
            return load_json_strict(f.read(), 64 * 1024)
    except (OSError, ValueError):
        return {}


def load_installed(spec: AppSpec = SPEC, version: str | None = None) -> InstalledInfo:
    """지금 실행 중인 설치본 정보. frozen 이면 _internal 의 VERSION·build_info·deps 를 읽는다."""
    from . import paths
    frozen = bool(getattr(sys, "frozen", False))
    app_dir = paths.install_root()
    code_dir = paths.code_dir()
    if version is None:
        from . import __version__ as version
    info = read_build_info(code_dir) if frozen else {}
    channel = str(info.get("channel") or ("release" if frozen else "source"))
    built_at = None
    if isinstance(info.get("built_at"), str):
        try:
            built_at = parse_iso_utc(info["built_at"])
        except ValueError:
            built_at = None
    deps: dict[str, str] = {}
    try:
        with open(os.path.join(code_dir, "deps.txt"), encoding="utf-8", errors="replace") as f:
            deps = parse_deps(f.read())
    except OSError:
        pass
    has_heavy = all(os.path.isfile(os.path.join(code_dir, *p.split("/")[1:])) for p in spec.heavy_files) and \
        all(os.path.isfile(os.path.join(code_dir, *p.split("/")[1:], "ffmpeg.exe")) for p in spec.heavy_dirs)
    return InstalledInfo(str(version), channel, built_at, app_dir, code_dir, frozen, deps, has_heavy)


def effective_pubkeys(spec: AppSpec, installed: InstalledInfo, env: dict | None = None) -> list[tuple[str, str]]:
    """검증에 쓸 공개키. test·source 채널에서만 환경 변수 CHATCOACH_TEST_PUBKEY 가 내장 키를 대신한다 (04 12-2).
    release 채널은 환경 변수를 읽지 않는다. 자체 점검(pubkeys)이 이것을 확인한다."""
    env = os.environ if env is None else env
    if installed.channel in ("test", "source"):
        test_key = env.get(TEST_PUBKEY_ENV, "").strip()
        if test_key:
            return [("TEST", test_key)]
    return [(k.label, k.line) for k in spec.update_pubkeys]


def allowed_base_url(installed: InstalledInfo, base_url: str | None) -> str | None:
    """--update-base-url 은 test·source 채널에서만, http 는 127.0.0.1 일 때만 (04 12-2). release 는 무시."""
    if not base_url or installed.channel not in ("test", "source"):
        return None
    u = urllib.parse.urlsplit(base_url)
    if u.scheme == "https" or (u.scheme == "http" and u.hostname == "127.0.0.1"):
        return base_url if base_url.endswith("/") else base_url + "/"
    LOG.warning("허용되지 않는 update-base-url 무시: %s", base_url)
    return None


def rewrite_url(url: str, spec: AppSpec, base_url: str | None) -> str:
    """시험 채널: https://github.com/ 로 시작하는 주소를 가짜 서버 주소로 바꾼다. 매니페스트의 url 규칙은 그대로 둔다."""
    if base_url and url.startswith(spec.github_base):
        return base_url + url[len(spec.github_base):]
    return url


# --------------------------------------------------------------------------- 매니페스트
def _reject_constant(name: str):
    raise ValueError(f"JSON 에 허용하지 않는 상수 {name}")


def load_json_strict(data: bytes, max_bytes: int) -> dict:
    """엄격 JSON: 크기 상한, BOM 은 벗김, NaN·Infinity 거부, 최상위는 object (S-29)."""
    if len(data) > max_bytes:
        raise ValueError(f"JSON 이 너무 큽니다 ({len(data)} > {max_bytes} 바이트)")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        obj = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except UnicodeDecodeError as ex:
        raise ValueError("JSON 이 UTF-8 이 아닙니다") from ex
    except json.JSONDecodeError as ex:
        raise ValueError(f"JSON 문법 오류: {ex.msg} (줄 {ex.lineno})") from ex
    if not isinstance(obj, dict):
        raise ValueError("JSON 최상위가 object 가 아닙니다")
    return obj


@dataclass(frozen=True)
class Asset:
    kind: str            # "full" | "lite"
    name: str
    size: int
    sha256: str
    url: str
    unpacked_size: int
    file_count: int


@dataclass(frozen=True)
class Manifest:
    manifest_version: int
    app: str
    version: str
    published_at: datetime
    min_supported: str
    mandatory: bool
    label_schema_version: int
    guide_versions: tuple[str, ...]
    notes_ko: str
    deps: dict[str, dict[str, str]]
    assets: tuple[Asset, ...]

    def asset(self, kind: str) -> Asset:
        for a in self.assets:
            if a.kind == kind:
                return a
        raise KeyError(kind)


def _need(obj: dict, key: str, typ, what: str = "매니페스트"):
    if key not in obj:
        raise ValueError(f"{what}에 {key} 가 없습니다")
    v = obj[key]
    if typ is int and isinstance(v, bool):      # bool 은 int 의 하위 타입이라 따로 막는다
        raise ValueError(f"{what}의 {key} 가 정수가 아닙니다")
    if not isinstance(v, typ):
        raise ValueError(f"{what}의 {key} 타입이 틀렸습니다 ({type(v).__name__})")
    return v


def parse_manifest(data: bytes, spec: AppSpec = SPEC) -> Manifest:
    """latest.json 바이트 → Manifest. 형식·타입·이름 규칙만 검사한다 (설치본과의 비교는 evaluate 가 한다).
    manifest_version 이 앱보다 크면 UpdateError('manual'), 그 밖의 형식 오류는 ValueError."""
    obj = load_json_strict(data, spec.max_manifest_bytes)
    mv = _need(obj, "manifest_version", int)
    if mv > spec.manifest_version:
        raise UpdateError("manual", f"매니페스트 형식 {mv} 은 이 버전이 모릅니다. 수동 업데이트가 필요합니다")
    if mv < 1:
        raise ValueError("manifest_version 이 1 보다 작습니다")
    app = _need(obj, "app", str)
    if app != spec.name:
        raise ValueError(f"다른 앱의 매니페스트입니다 ({app!r})")
    version = _need(obj, "version", str)
    parse_version(version)
    published_at = parse_iso_utc(_need(obj, "published_at", str))
    min_supported = _need(obj, "min_supported", str)
    parse_version(min_supported)
    mandatory = _need(obj, "mandatory", bool)
    lsv = _need(obj, "label_schema_version", int)
    gv = _need(obj, "guide_versions", list)
    if not all(isinstance(g, str) and 0 < len(g) <= 32 for g in gv):
        raise ValueError("guide_versions 항목이 문자열이 아닙니다")
    notes = _need(obj, "notes_ko", str)[:spec.max_notes_chars]
    deps_raw = _need(obj, "deps", dict)
    deps: dict[str, dict[str, str]] = {}
    for k, v in deps_raw.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            raise ValueError("deps 형식이 틀렸습니다")
        tag, sha = v.get("tag"), v.get("sha256")
        if not isinstance(tag, str) or not isinstance(sha, str) or not SHA256_RE.match(sha):
            raise ValueError(f"deps.{k} 의 tag·sha256 이 틀렸습니다")
        deps[k] = {"tag": tag, "sha256": sha}
    assets_raw = _need(obj, "assets", list)
    assets: list[Asset] = []
    for i, a in enumerate(assets_raw):
        if not isinstance(a, dict):
            raise ValueError(f"assets[{i}] 가 object 가 아닙니다")
        what = f"assets[{i}]"
        kind = _need(a, "kind", str, what)
        if kind not in ("full", "lite"):
            raise ValueError(f"{what}.kind 가 full/lite 가 아닙니다 ({kind!r})")
        name = _need(a, "name", str, what)
        if name != spec.asset_name(version, kind):
            raise ValueError(f"{what}.name 이 규칙과 다릅니다 ({name!r})")
        size = _need(a, "size", int, what)
        if not 0 < size <= spec.max_zip_bytes:
            raise ValueError(f"{what}.size 범위 밖 ({size})")
        sha = _need(a, "sha256", str, what)
        if not SHA256_RE.match(sha):
            raise ValueError(f"{what}.sha256 이 소문자 16진수 64자가 아닙니다")
        url = _need(a, "url", str, what)
        if url != spec.asset_url(version, name):
            raise ValueError(f"{what}.url 이 규칙과 다릅니다 ({url!r})")
        unpacked = _need(a, "unpacked_size", int, what)
        if not 0 < unpacked <= spec.max_unpacked_bytes:
            raise ValueError(f"{what}.unpacked_size 범위 밖 ({unpacked})")
        count = _need(a, "file_count", int, what)
        if not 0 < count <= spec.max_zip_entries:
            raise ValueError(f"{what}.file_count 범위 밖 ({count})")
        assets.append(Asset(kind, name, size, sha, url, unpacked, count))
    kinds = [a.kind for a in assets]
    if kinds.count("full") != 1 or kinds.count("lite") != 1:
        raise ValueError("assets 에는 full 과 lite 가 정확히 하나씩 있어야 합니다")
    return Manifest(mv, app, version, published_at, min_supported, mandatory, lsv, tuple(gv), notes, deps,
                    tuple(assets))


# --------------------------------------------------------------------------- 확인 결과
@dataclass(frozen=True)
class Release:
    manifest: Manifest
    signed_by: str              # 내장 키 라벨 (K1/K2/TEST)
    key_id: str
    chosen: str                 # "full" | "lite" — 받을 자산
    warnings: tuple[str, ...] = ()

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def download_size(self) -> int:
        return self.manifest.asset(self.chosen).size


@dataclass(frozen=True)
class CheckResult:
    state: str                  # "newer" | "latest" | "failed"
    reason: str = ""            # failed 의 사유 (사람이 읽는 문장)
    kind: str = ""              # failed 의 종류 (UpdateError.kind)
    release: Release | None = None
    skipped: bool = False       # newer 이지만 사용자가 '이 버전 건너뛰기' 한 버전
    needs_update: bool = False  # 설치본 < min_supported (04 5-5 '업데이트 필요' 모드)

    @property
    def ok(self) -> bool:
        return self.state != "failed"


def choose_asset(manifest: Manifest, installed: InstalledInfo) -> str:
    """경량은 매니페스트 deps 가 로컬 deps.txt(태그·해시)와 모두 같고 로컬에 ffmpeg·libmpv 가 있을 때만 (04 6-4)."""
    if not installed.has_heavy or not installed.deps:
        return "full"
    for name, d in manifest.deps.items():
        if installed.deps.get(name) != d["tag"] or installed.deps.get(f"{name}_sha256") != d["sha256"]:
            return "full"
    if not manifest.deps:
        return "full"
    return "lite"


def evaluate(data: bytes, sig: bytes | None, installed: InstalledInfo, pubkeys: Iterable[tuple[str, str]],
             spec: AppSpec = SPEC, skip_version: str | None = None) -> CheckResult:
    """받은 latest.json·.minisig 를 설치본에 대해 평가한다 (04 5-4 순서). 네트워크·파일을 건드리지 않는다.

    서명 파일이 없으면(None) 실패다 — '서명이 있으면 검증' 분기는 두지 않는다 (ADR-13).
    """
    keys = list(pubkeys)
    if not keys:
        return CheckResult("failed", "이 빌드에는 업데이트 공개키가 없습니다 (appspec)", "pubkeys")
    if sig is None:
        return CheckResult("failed", "서명 파일(latest.json.minisig)이 없습니다", "signature")
    if len(data) > spec.max_manifest_bytes:
        return CheckResult("failed", f"latest.json 이 {spec.max_manifest_bytes // 1024}KB 를 넘습니다", "format")
    if len(sig) > spec.max_sig_bytes:
        return CheckResult("failed", "서명 파일이 너무 큽니다", "signature")
    try:
        vr = minisign.verify(data, sig, keys)
    except minisign.MinisignError as ex:
        return CheckResult("failed", f"서명 검증 실패 — 개발자에게 알려 주세요 ({ex})", "signature")
    try:
        m = parse_manifest(data, spec)
    except UpdateError as ex:
        return CheckResult("failed", ex.message, ex.kind)
    except ValueError as ex:
        return CheckResult("failed", f"매니페스트 형식 오류: {ex}", "format")
    expected_comment = spec.trusted_comment(m.version)
    if vr.trusted_comment != expected_comment:
        return CheckResult("failed", f"서명 검증 실패 — trusted comment 가 다릅니다 ({vr.trusted_comment!r})", "signature")
    # 설치본과 비교
    try:
        installed_v = parse_version(installed.version)
    except ValueError:
        return CheckResult("failed", f"설치본 버전을 읽을 수 없습니다 ({installed.version!r})", "format")
    if parse_version(m.version) <= installed_v:
        return CheckResult("latest", f"최신 (설치 {installed.version}, 매니페스트 {m.version})")
    if installed.frozen and installed.built_at is None:
        return CheckResult("failed", "설치본의 빌드 시각(build_info.json)을 읽을 수 없습니다", "format")
    if installed.built_at is not None and m.published_at < installed.built_at:
        return CheckResult("failed",
                           f"매니페스트 발행 시각({m.published_at.isoformat()})이 설치본 빌드 시각"
                           f"({installed.built_at.isoformat()})보다 이릅니다. 재생(replay)이거나 릴리스 실수입니다", "format")
    warnings: list[str] = []
    if m.label_schema_version != spec.label_schema_version:
        warnings.append(f"라벨 형식이 바뀝니다 ({spec.label_schema_version} → {m.label_schema_version})")
    needs = parse_version(m.min_supported) > installed_v
    rel = Release(m, vr.label, vr.key_id_hex, choose_asset(m, installed), tuple(warnings))
    skipped = bool(skip_version) and skip_version == m.version and not m.mandatory
    return CheckResult("newer", "", "", rel, skipped=skipped, needs_update=needs)


# --------------------------------------------------------------------------- 확인 상태 (data\update\state.json)
_STATE_KEYS = ("last_ok_at", "last_fail_at", "last_fail_reason", "last_seen_version", "skip_version",
               "last_min_supported", "mandatory_version")


@dataclass
class UpdateState:
    last_ok_at: str | None = None
    last_fail_at: str | None = None
    last_fail_reason: str | None = None
    last_seen_version: str | None = None
    skip_version: str | None = None
    # 마지막으로 '확인 성공' 한 서명 매니페스트의 정책 값 (04 5-5). 오프라인이 되어도 그대로 둔다 — support_status 참고
    last_min_supported: str | None = None
    mandatory_version: str | None = None     # 그 매니페스트가 mandatory 였으면 그 버전

    @classmethod
    def load(cls, path: str) -> "UpdateState":
        try:
            with open(path, "rb") as f:
                obj = load_json_strict(f.read(), 64 * 1024)
        except (OSError, ValueError):
            return cls()
        st = cls()
        for k in _STATE_KEYS:
            v = obj.get(k)
            if isinstance(v, str):
                setattr(st, k, v)
        return st

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.__dict__, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def record_ok(self, now: datetime, seen_version: str | None, min_supported: str | None = None,
                  mandatory_version: str | None = None) -> None:
        """확인 성공. 정책 값은 이번 매니페스트 값으로 바꾼다 ('최신' 이면 둘 다 None 으로 지운다)."""
        self.last_ok_at = now.astimezone(timezone.utc).isoformat()
        self.last_seen_version = seen_version
        self.last_min_supported = min_supported
        self.mandatory_version = mandatory_version

    def record_fail(self, now: datetime, reason: str) -> None:
        """확인 실패. 정책 값(last_min_supported·mandatory_version)은 건드리지 않는다."""
        self.last_fail_at = now.astimezone(timezone.utc).isoformat()
        self.last_fail_reason = reason[:500]


@dataclass(frozen=True)
class SupportStatus:
    """04 5-5 의 표. needs_update 면 '업데이트 필요' 모드(새 라벨 저장 막음, 열람·내보내기는 가능)."""
    installed_version: str
    min_supported: str | None = None
    mandatory_version: str | None = None    # 설치본보다 높은 필수 업데이트가 있으면 그 버전
    needs_update: bool = False

    @property
    def can_save_labels(self) -> bool:
        return not self.needs_update

    @property
    def required_pending(self) -> bool:
        """시작할 때마다 확인·알림할 것이 있는가 (필수 업데이트 또는 지원 종료)."""
        return self.needs_update or self.mandatory_version is not None

    def message(self) -> str:
        if self.needs_update:
            return (f"이 버전({self.installed_version})은 지원이 끝났습니다 (최소 {self.min_supported}). "
                    "업데이트해야 새 라벨을 저장할 수 있습니다. 열람·내보내기는 됩니다. "
                    "자동 업데이트가 안 되면 수동 업데이트 안내를 따라 주세요.")
        if self.mandatory_version:
            return f"필수 업데이트 {self.mandatory_version} 이 있습니다. 건너뛸 수 없습니다."
        return ""


def _ver_or_none(text) -> tuple[int, int, int] | None:
    try:
        return parse_version(text)
    except ValueError:
        return None


def support_status(state: UpdateState, installed_version: str) -> SupportStatus:
    """마지막으로 받은 서명 매니페스트의 min_supported·mandatory 로 지금 설치본의 처지를 판정한다 (질문 46 기본안).

    한 번도 확인에 성공하지 못했으면(값 없음) 아무것도 강제하지 않는다 — 오프라인이 라벨링 중단이 되지 않게.
    확인에 성공해 '지원 종료' 를 알게 된 뒤에는 오프라인으로 다시 켜도 막음을 유지한다 (다시 켜는 것으로 풀리면
    min_supported 가 뜻이 없다). 더 높은 버전으로 업데이트하거나 다음 확인이 다른 값을 주면 풀린다.
    """
    inst = _ver_or_none(installed_version)
    if inst is None:
        return SupportStatus(installed_version)
    ms = _ver_or_none(state.last_min_supported)
    mv = _ver_or_none(state.mandatory_version)
    needs = ms is not None and ms > inst
    mandatory = state.mandatory_version if mv is not None and mv > inst else None
    return SupportStatus(installed_version, state.last_min_supported if ms else None, mandatory, needs)


def load_support_status(state_path: str, installed_version: str) -> SupportStatus:
    return support_status(UpdateState.load(state_path), installed_version)


def label_save_block_reason(state_path: str, installed_version: str) -> str | None:
    """새 라벨 저장을 막아야 하면 사유, 아니면 None. 저장 경로(작업대·세션)가 이 함수 하나로 묻는다 (통합 담당)."""
    st = load_support_status(state_path, installed_version)
    return st.message() if st.needs_update else None


def is_due(state: UpdateState, now: datetime, spec: AppSpec = SPEC) -> bool:
    """성공 뒤 24시간, 실패 뒤 1시간 (04 6-2). 마지막 사건이 무엇이었는지로 판단한다."""
    def _dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return parse_iso_utc(s)
        except ValueError:
            return None
    ok, fail = _dt(state.last_ok_at), _dt(state.last_fail_at)
    now = now.astimezone(timezone.utc)
    if ok is None and fail is None:
        return True
    if fail is not None and (ok is None or fail > ok):
        return (now - fail).total_seconds() >= spec.check_interval_fail_sec
    return (now - ok).total_seconds() >= spec.check_interval_ok_sec


# --------------------------------------------------------------------------- 네트워크
class _RedirectPolicy(urllib.request.HTTPRedirectHandler):
    """리다이렉트는 https 로만 최대 N 번. 시험 채널의 127.0.0.1 http 는 허용."""

    def __init__(self, max_redirects: int, allow_http_loopback: bool):
        super().__init__()
        self.max_redirects = max_redirects
        self.allow_http_loopback = allow_http_loopback
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > self.max_redirects:
            raise urllib.error.URLError(f"리다이렉트가 {self.max_redirects}번을 넘었습니다")
        u = urllib.parse.urlsplit(newurl)
        if u.scheme != "https" and not (self.allow_http_loopback and u.scheme == "http" and u.hostname == "127.0.0.1"):
            raise urllib.error.URLError(f"https 가 아닌 곳으로의 리다이렉트를 막았습니다: {u.scheme}://{u.hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url: str, spec: AppSpec, installed: InstalledInfo, timeout: float | None = None, accept: str | None = None):
    """공통 요청: User-Agent, 제한 시간, 리다이렉트 정책. 돌려주는 응답은 with 로 닫는다."""
    u = urllib.parse.urlsplit(url)
    loopback = installed.channel in ("test", "source") and u.hostname == "127.0.0.1"
    if u.scheme != "https" and not (loopback and u.scheme == "http"):
        raise UpdateError("network", f"https 가 아닌 주소는 열지 않습니다: {url}")
    headers = {"User-Agent": spec.user_agent(installed.version)}
    if accept:
        headers["Accept"] = accept
    opener = urllib.request.build_opener(_RedirectPolicy(spec.max_redirects, loopback))
    return opener.open(urllib.request.Request(url, headers=headers), timeout=timeout or spec.http_timeout_sec)


def _get_small(url: str, limit: int, spec: AppSpec, installed: InstalledInfo, accept: str | None = None) -> bytes:
    with open_url(url, spec, installed, accept=accept) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("format", f"응답이 상한({limit} 바이트)을 넘습니다: {url}")
    return data


def normalize_channel(value) -> str:
    """설정값 → 'stable' | 'beta'. 모르는 값·빈 값은 stable (숨은 설정이라 오타가 beta 로 새지 않게)."""
    return "beta" if isinstance(value, str) and value.strip().lower() == "beta" else "stable"


def pick_beta_release(releases) -> dict[str, str] | None:
    """REST API /releases 목록에서 초안을 뺀 가장 높은 버전(prerelease 포함)의 {자산 이름: 주소}.
    tag 가 vX.Y.Z 가 아니거나 latest.json 자산이 없는 항목은 건너뛴다. 무엇을 믿을지는 여전히 서명이 정한다."""
    if not isinstance(releases, list):
        return None
    best: tuple[tuple[int, int, int], dict[str, str]] | None = None
    for r in releases:
        if not isinstance(r, dict) or r.get("draft") is not False:
            continue
        tag = r.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v") or _ver_or_none(tag[1:]) is None:
            continue
        assets = {a.get("name"): a.get("browser_download_url") for a in (r.get("assets") or []) if isinstance(a, dict)}
        assets = {k: v for k, v in assets.items() if isinstance(k, str) and isinstance(v, str)}
        if "latest.json" not in assets:
            continue
        v = parse_version(tag[1:])
        if best is None or v > best[0]:
            best = (v, assets)
    return best[1] if best else None


def _fetch_beta(spec: AppSpec, installed: InstalledInfo, base_url: str | None) -> tuple[bytes, bytes | None]:
    url = (base_url + "api/releases?per_page=10") if base_url else spec.api_base + "/releases?per_page=10"
    obj = json.loads(_get_small(url, 2 * 1024 * 1024, spec, installed, accept="application/vnd.github+json")
                     .decode("utf-8"), parse_constant=_reject_constant)
    assets = pick_beta_release(obj)
    if not assets:
        raise UpdateError("network", "릴리스 목록에 latest.json 이 있는 릴리스가 없습니다")
    data = _get_small(assets["latest.json"], spec.max_manifest_bytes, spec, installed)
    sig_url = assets.get("latest.json.minisig")
    return data, (_get_small(sig_url, spec.max_sig_bytes, spec, installed) if sig_url else None)


def fetch_manifest(spec: AppSpec, installed: InstalledInfo, base_url: str | None = None,
                   channel: str = "stable") -> tuple[bytes, bytes | None]:
    """(latest.json 바이트, .minisig 바이트 또는 None). 1차 고정 링크 → 2차 REST API (04 6-2).

    channel='beta' 면 먼저 REST API 의 릴리스 목록(prerelease 포함, 초안 제외)에서 가장 높은 버전을 본다.
    목록을 못 받으면 stable 경로로 내려간다 (내부 확인 PC 도 정식 릴리스는 받는다).
    서명 파일이 404 이면 None 을 돌려준다 (evaluate 가 '서명 없음' 으로 거부한다). 모두 실패하면 UpdateError('network').
    """
    name, sig_name = "latest.json", "latest.json.minisig"
    errors: list[str] = []
    if normalize_channel(channel) == "beta":
        try:
            return _fetch_beta(spec, installed, base_url)
        except (urllib.error.URLError, OSError, UpdateError, ValueError) as ex:
            errors.append(f"beta: {ex}")
    # 1차
    try:
        data = _get_small(rewrite_url(spec.latest_download_url(name), spec, base_url), spec.max_manifest_bytes, spec, installed)
        try:
            sig = _get_small(rewrite_url(spec.latest_download_url(sig_name), spec, base_url), spec.max_sig_bytes, spec, installed)
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                return data, None
            raise
        return data, sig
    except (urllib.error.URLError, OSError, UpdateError) as ex:
        errors.append(f"1차: {ex}")
    # 2차: REST API
    try:
        api = rewrite_url(spec.api_base + "/releases/latest", spec, base_url) if base_url else spec.api_base + "/releases/latest"
        if base_url:
            api = base_url + "api/releases/latest"
        obj = load_json_strict(_get_small(api, 1024 * 1024, spec, installed, accept="application/vnd.github+json"),
                               1024 * 1024)
        assets = {a.get("name"): a.get("browser_download_url") for a in obj.get("assets", []) if isinstance(a, dict)}
        if name not in assets or not isinstance(assets[name], str):
            raise UpdateError("network", "최신 릴리스에 latest.json 이 없습니다")
        data = _get_small(assets[name], spec.max_manifest_bytes, spec, installed)
        if sig_name not in assets or not isinstance(assets[sig_name], str):
            return data, None
        return data, _get_small(assets[sig_name], spec.max_sig_bytes, spec, installed)
    except (urllib.error.URLError, OSError, UpdateError, ValueError) as ex:
        errors.append(f"2차: {ex}")
    raise UpdateError("network", "업데이트 확인 실패 — " + " / ".join(errors))


def run_check(spec: AppSpec, installed: InstalledInfo, state_path: str, now: datetime | None = None,
              force: bool = False, base_url: str | None = None, pubkeys: Iterable[tuple[str, str]] | None = None,
              fetch: Callable[..., tuple[bytes, bytes | None]] = fetch_manifest, channel: str = "stable",
              on_start: bool = False) -> CheckResult | None:
    """주기 판단 → 받기 → 평가 → 상태 저장. 확인할 때가 아니면 None (force 면 항상 확인).
    on_start=True(앱 시작 때 한 번)이고 필수 업데이트·지원 종료가 걸려 있으면 주기와 상관없이 확인한다
    ('mandatory 면 시작할 때마다 알림', 04 5-5). 확인 시각은 결과가 나온 뒤에 쓴다.
    네트워크 실패는 failed(network) 로 돌려주고 1시간 뒤 다시 확인하게 기록한다."""
    now = now or datetime.now(timezone.utc)
    state = UpdateState.load(state_path)
    due = force or is_due(state, now, spec) or (on_start and support_status(state, installed.version).required_pending)
    if not due:
        return None
    base = allowed_base_url(installed, base_url)
    keys = list(pubkeys) if pubkeys is not None else effective_pubkeys(spec, installed)
    try:
        if normalize_channel(channel) == "beta":
            data, sig = fetch(spec, installed, base, channel="beta")
        else:
            data, sig = fetch(spec, installed, base)
    except UpdateError as ex:
        state.record_fail(now, ex.message)
        state.save(state_path)
        LOG.info("업데이트 확인 실패: %s", ex.message)
        return CheckResult("failed", ex.message, ex.kind)
    result = evaluate(data, sig, installed, keys, spec, skip_version=state.skip_version)
    if result.ok:
        rel = result.release
        if rel is not None:
            m = rel.manifest
            state.record_ok(now, rel.version, m.min_supported, rel.version if m.mandatory else None)
        else:
            state.record_ok(now, installed.version)
        LOG.info("업데이트 확인: %s %s", result.state, result.release.version if result.release else "")
    else:
        state.record_fail(now, result.reason)
        LOG.warning("업데이트 확인 실패 (%s): %s", result.kind, result.reason)
    state.save(state_path)
    return result


# --------------------------------------------------------------------------- 다운로드
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dst: str, size: int, spec: AppSpec, installed: InstalledInfo,
             progress: Callable[[float, str], None] | None = None,
             cancel: Callable[[], bool] | None = None) -> None:
    """정확히 size 바이트를 dst 에 받는다. Content-Length 불일치·초과·취소·30초 멈춤이면 UpdateError('download')."""
    if size <= 0 or size > spec.max_zip_bytes:
        raise UpdateError("download", f"자산 크기가 범위 밖입니다 ({size})")
    got = 0
    last = time.monotonic()
    try:
        with open_url(url, spec, installed, timeout=spec.download_stall_sec) as r, open(dst, "wb") as f:
            cl = r.headers.get("Content-Length")
            if cl is not None and cl.strip().isdigit() and int(cl) != size:
                raise UpdateError("download", f"Content-Length({cl})가 매니페스트의 크기({size})와 다릅니다")
            while True:
                if cancel and cancel():
                    raise UpdateError("download", "취소됨")
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                got += len(chunk)
                if got > size:
                    raise UpdateError("download", f"매니페스트의 크기({size})보다 많이 받았습니다. 중단")
                f.write(chunk)
                now = time.monotonic()
                if progress and (now - last >= 0.2 or got == size):
                    progress(got / size, f"받는 중 {got / 1e6:.0f} / {size / 1e6:.0f} MB")
                    last = now
    except urllib.error.HTTPError as ex:
        raise UpdateError("download", f"다운로드 실패 HTTP {ex.code}: {url}") from ex
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise UpdateError("download", f"다운로드 실패: {ex}") from ex
    if got != size:
        raise UpdateError("download", f"받은 크기({got})가 매니페스트의 크기({size})와 다릅니다")


# --------------------------------------------------------------------------- zip 검사 (04 6-5)
@dataclass(frozen=True)
class ZipSummary:
    file_count: int
    unpacked_size: int
    names: tuple[str, ...]


def _safe_zip_name(name: str, top: str) -> str | None:
    """경로가 규칙에 맞으면 None, 아니면 사유."""
    if not name or name.startswith("/") or name.startswith("\\"):
        return "절대경로"
    if "\\" in name:
        return "역슬래시"
    if ":" in name:
        return "드라이브 문자 또는 ':'"
    parts = name.split("/")
    if parts[0] != top:
        return f"최상위 폴더가 {top}/ 가 아님"
    if any(p in ("..", ".") for p in parts) or any(p == "" for p in parts[:-1]):
        return "'..' 또는 빈 경로 조각"
    if any(ch < " " for ch in name):
        return "제어 문자"
    return None


def validate_zip(zip_path: str, expected_version: str, lite: bool, spec: AppSpec = SPEC,
                 asset: Asset | None = None) -> ZipSummary:
    """받은 zip 의 7가지 검사. 릴리스 쪽 tools/check_release_zip.py 도 같은 함수를 쓴다. 실패하면 UpdateError('verify')."""
    top = spec.zip_top
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as ex:
        raise UpdateError("verify", f"zip 을 열 수 없습니다: {ex}") from ex
    with z:
        infos = z.infolist()
        files = [i for i in infos if not i.is_dir()]
        if len(files) > spec.max_zip_entries:
            raise UpdateError("verify", f"zip 항목이 {len(files)}개로 상한({spec.max_zip_entries})을 넘습니다")
        unpacked = sum(i.file_size for i in files)
        if unpacked > spec.max_unpacked_bytes:
            raise UpdateError("verify", f"압축 해제 크기 {unpacked} 가 상한을 넘습니다")
        if asset is not None:
            if len(files) != asset.file_count:
                raise UpdateError("verify", f"zip 파일 수({len(files)})가 매니페스트({asset.file_count})와 다릅니다")
            if unpacked != asset.unpacked_size:
                raise UpdateError("verify", f"압축 해제 크기({unpacked})가 매니페스트({asset.unpacked_size})와 다릅니다")
        seen: set[str] = set()
        names: list[str] = []
        for i in infos:
            why = _safe_zip_name(i.filename, top)
            if why:
                raise UpdateError("verify", f"zip 경로 거부 ({why}): {i.filename!r}")
            key = i.filename.casefold().rstrip("/")
            if key in seen:
                raise UpdateError("verify", f"대소문자만 다른 중복 이름: {i.filename!r}")
            seen.add(key)
            if not i.is_dir():
                names.append(i.filename)
        prefix = top + "/"
        cf_prefix = prefix.casefold()
        for keep in spec.keep_dirs:
            bad = [n for n in seen if n.startswith(cf_prefix + keep.casefold() + "/") or n == cf_prefix + keep.casefold()]
            if bad:
                raise UpdateError("verify", f"zip 에 보존 폴더가 들어 있습니다: {bad[0]!r}")
        for req in spec.required_files:
            if prefix + req not in names:
                raise UpdateError("verify", f"zip 에 필수 파일이 없습니다: {req}")
        try:
            ver = z.read(prefix + "_internal/VERSION").decode("utf-8", "replace").strip().lstrip("﻿")
        except KeyError:
            raise UpdateError("verify", "zip 에 _internal/VERSION 이 없습니다")
        if ver != expected_version:
            raise UpdateError("verify", f"zip 안의 버전({ver})이 매니페스트({expected_version})와 다릅니다")
        heavy_present = [n for n in names if any(n.startswith(prefix + d + "/") for d in spec.heavy_dirs)
                         or n in {prefix + f for f in spec.heavy_files}]
        if lite and heavy_present:
            raise UpdateError("verify", f"경량 zip 에 ffmpeg·libmpv 가 들어 있습니다: {heavy_present[0]!r}")
        if not lite:
            for f in spec.heavy_files:
                if prefix + f not in names:
                    raise UpdateError("verify", f"전체 zip 에 {f} 가 없습니다")
            if not any(n.startswith(prefix + spec.heavy_dirs[0] + "/") for n in names):
                raise UpdateError("verify", f"전체 zip 에 {spec.heavy_dirs[0]}/ 가 없습니다")
        exe_count = sum(1 for n in names if n.count("/") == 1 and n.lower().endswith(".exe"))
        if exe_count != 1:
            raise UpdateError("verify", f"최상위 exe 가 하나가 아닙니다 ({exe_count})")
    return ZipSummary(len(files), unpacked, tuple(names))


def extract_zip(zip_path: str, dest: str, spec: AppSpec = SPEC) -> str:
    """검사를 통과한 zip 을 dest 에 푼다. 돌려주는 값은 dest\\<zip_top>."""
    with zipfile.ZipFile(zip_path) as z:
        for i in z.infolist():
            if _safe_zip_name(i.filename, spec.zip_top):
                raise UpdateError("verify", f"zip 경로 거부: {i.filename!r}")
            target = os.path.join(dest, *i.filename.split("/"))
            if i.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(i) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return os.path.join(dest, spec.zip_top)


# --------------------------------------------------------------------------- 준비 (stage)
@dataclass(frozen=True)
class StagedUpdate:
    release: Release
    stage_dir: str          # <앱>\_update\<임의 이름>
    stage_id: str
    new_dir: str            # <stage_dir>\new\ChatCoach
    lite: bool
    selftest: dict          # 새 exe 의 자체 점검 결과 (건너뛰었으면 {})


def decode_output(data: bytes | None) -> str:
    """콘솔 출력 bytes → str. UTF-8 우선, 아니면 cp949, 그래도 안 되면 대체 문자 (0.9.8 사고)."""
    if not data:
        return ""
    for enc in ("utf-8", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def selftest_command(exe: str, out: str, version: str, channel: str, data_dir: str) -> list[str]:
    """자체 점검 관문의 명령. 시험은 이 함수만 바꿔 가짜 exe 를 쓴다 (관문의 판정 코드는 그대로 탄다)."""
    return [exe, "--selftest", out, "--expect-version", version, "--expect-channel", channel, "--data-dir", data_dir]


def run_selftest_gate(new_app_dir: str, version: str, channel: str, stage_dir: str, spec: AppSpec = SPEC) -> dict:
    """준비 폴더의 새 exe(되돌리기면 <앱>\\_backup 의 exe)로 --selftest. 통과하지 못하면 UpdateError('selftest').
    사용자의 data\\ 는 건드리지 않는다. 결과 JSON 의 version 이 기대 버전과 같아야 통과다."""
    exe = os.path.join(new_app_dir, spec.exe_name)
    out = os.path.join(stage_dir, "selftest.json")
    data_dir = os.path.join(stage_dir, "selftest-data")
    try:
        os.remove(out)          # 지난 결과 파일이 남아 있어도 통과로 읽지 않게
    except OSError:
        pass
    cmd = selftest_command(exe, out, version, channel, data_dir)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=spec.selftest_timeout_sec, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise UpdateError("selftest", f"새 버전의 자체 점검이 {spec.selftest_timeout_sec:.0f}초 안에 끝나지 않았습니다")
    except OSError as ex:
        raise UpdateError("selftest", f"새 버전을 실행할 수 없습니다: {ex}")
    report: dict = {}
    try:
        with open(out, "rb") as f:
            report = load_json_strict(f.read(), 1024 * 1024)
    except (OSError, ValueError):
        pass
    if r.returncode != 0 or not report.get("ok"):
        failed = {k: v.get("detail") for k, v in (report.get("checks") or {}).items() if isinstance(v, dict) and not v.get("ok")}
        tail = decode_output(r.stderr)[-500:]
        raise UpdateError("selftest", f"새 버전이 이 PC 에서 실행되지 않습니다 (exit {r.returncode}) 실패 항목: {failed or tail}")
    if report.get("version") != version:
        raise UpdateError("selftest", f"자체 점검이 보고한 버전({report.get('version')!r})이 기대({version})와 다릅니다")
    return report


def stage(release: Release, spec: AppSpec, installed: InstalledInfo, base_url: str | None = None,
          progress: Callable[[float, str], None] | None = None, cancel: Callable[[], bool] | None = None,
          selftest: bool = True) -> StagedUpdate:
    """다운로드 → 해시 → zip 검사 → 해제 → 자체 점검. 실패하면 준비 폴더를 지우고 UpdateError.
    자체 점검 실패 때만 원인 분석용으로 준비 폴더를 1개 남긴다 (더 오래된 것은 지운다)."""
    asset = release.manifest.asset(release.chosen)
    lite = release.chosen == "lite"
    base = allowed_base_url(installed, base_url)
    root = os.path.join(installed.app_dir, "_update")
    os.makedirs(root, exist_ok=True)
    stage_dir = tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=root)
    stage_id = os.path.basename(stage_dir)
    # 사용 중 표식: 정리(cleanup_stage_dirs)가 살아 있는 프로세스의 준비 폴더를 지우지 않게 한다.
    # (M0 시험에서 시작 직후의 정리가 추출 중인 폴더를 지운 경합이 있었다)
    with open(os.path.join(stage_dir, ACTIVE_MARKER), "w", encoding="ascii") as f:
        f.write(str(os.getpid()))
    part = os.path.join(stage_dir, "pkg.zip.part")
    pkg = os.path.join(stage_dir, "pkg.zip")

    def report(frac: float, text: str):
        if progress:
            progress(frac, text)

    try:
        report(0.0, "경량 업데이트 (ffmpeg·libmpv 는 그대로)" if lite else "전체 업데이트")
        url = rewrite_url(asset.url, spec, base)
        LOG.info("업데이트 다운로드 (%s): %s", release.chosen, url)
        download(url, part, asset.size, spec, installed,
                 progress=lambda f, t: report(f * 0.8, t), cancel=cancel)
        report(0.82, "체크섬 확인 중…")
        actual = sha256_file(part)
        if actual != asset.sha256:
            raise UpdateError("verify", "받은 파일이 올바르지 않습니다 (sha256 불일치)")
        os.replace(part, pkg)
        report(0.86, "패키지 검사 중…")
        validate_zip(pkg, release.version, lite, spec, asset)
        report(0.9, "압축 푸는 중…")
        new_dir = extract_zip(pkg, os.path.join(stage_dir, "new"), spec)
        os.remove(pkg)
        if lite:
            # 경량 zip 에는 ffmpeg·libmpv 가 없다. 자체 점검(ffmpeg·libmpv 항목)이 돌 수 있게 설치본의 것을
            # 같은 볼륨 하드링크로 붙인다 (실패하면 복사). 교체 스크립트는 어차피 이 두 경로를 /XD·/XF 로 뺀다.
            link_heavy_files(installed.code_dir, os.path.join(new_dir, "_internal"), spec)
        result: dict = {}
        if selftest:
            report(0.95, "새 버전 자체 점검 중…")
            try:
                result = run_selftest_gate(new_dir, release.version, installed.channel, stage_dir, spec)
            finally:
                # 새 exe 의 자체 점검이 자기 설치 폴더(new\ChatCoach) 아래에 빈 _update\ 를 남길 수 있다 (M0 판).
                # zip 에는 _update 가 들어올 수 없으니(validate_zip) 있으면 관문이 만든 것이다
                _remove_if_no_files(os.path.join(new_dir, spec.keep_dirs[2]))
        report(1.0, "준비 완료")
        return StagedUpdate(release, stage_dir, stage_id, new_dir, lite, result)
    except UpdateError as ex:
        if ex.kind == "selftest":
            keep_one_failed_stage(root, stage_dir)
        else:
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def link_heavy_files(src_internal: str, dst_internal: str, spec: AppSpec = SPEC) -> int:
    """설치본 _internal 의 ffmpeg\\ 와 libmpv-2.dll 을 준비 폴더에 하드링크(같은 볼륨)한다. 안 되면 복사. 붙인 파일 수."""
    n = 0

    def _link(src: str, dst: str) -> None:
        nonlocal n
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            return
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        n += 1

    for rel in spec.heavy_files:
        parts = rel.split("/")[1:]            # "_internal/libmpv-2.dll" → ["libmpv-2.dll"]
        src = os.path.join(src_internal, *parts)
        if os.path.isfile(src):
            _link(src, os.path.join(dst_internal, *parts))
    for rel in spec.heavy_dirs:
        parts = rel.split("/")[1:]
        src_dir = os.path.join(src_internal, *parts)
        if not os.path.isdir(src_dir):
            continue
        for dirpath, _dirs, files in os.walk(src_dir):
            for f in files:
                s = os.path.join(dirpath, f)
                d = os.path.join(dst_internal, *parts, os.path.relpath(s, src_dir))
                _link(s, d)
    return n


def stage_in_use(stage_dir: str) -> bool:
    """ACTIVE 표식의 pid 가 살아 있는가 (다른 창이나 이 창이 준비 중)."""
    try:
        with open(os.path.join(stage_dir, ACTIVE_MARKER), encoding="ascii") as f:
            pid = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return True
    from .crashguard import _pid_alive
    return _pid_alive(pid)


def keep_one_failed_stage(root: str, keep: str) -> None:
    """자체 점검에 실패한 준비 폴더를 1개만 남긴다 (04 6-6). 사용 중인 다른 폴더는 건드리지 않는다."""
    keep_key = os.path.normcase(os.path.abspath(keep))
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if os.path.normcase(os.path.abspath(p)) != keep_key and name.startswith(STAGE_PREFIX) and os.path.isdir(p) \
                and not stage_in_use(p):
            shutil.rmtree(p, ignore_errors=True)
    try:
        with open(os.path.join(keep, FAILED_MARKER), "w", encoding="utf-8") as f:
            f.write("selftest failed\n")
        os.remove(os.path.join(keep, ACTIVE_MARKER))
    except OSError:
        pass


def cleanup_stage_dirs(app_dir: str) -> int:
    """정상 시작 때 _update\\ 아래 남은 준비 폴더를 지운다. 이름 형식을 확인하고, FAILED 표식이 있는 1개와
    살아 있는 프로세스가 쓰는 중(ACTIVE)인 폴더는 남긴다."""
    root = os.path.join(app_dir, "_update")
    if not os.path.isdir(root):
        return 0
    n = 0
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if not name.startswith(STAGE_PREFIX) or not os.path.isdir(p):
            continue
        if os.path.isfile(os.path.join(p, FAILED_MARKER)) or stage_in_use(p):
            continue
        shutil.rmtree(p, ignore_errors=True)
        n += 1
    return n


# --------------------------------------------------------------------------- 적용 (Applier)
class Applier(Protocol):
    def preflight(self, staged: StagedUpdate) -> tuple[list[str], list[str]]: ...   # (막는 문제, 경고)
    def apply(self, staged: StagedUpdate) -> None: ...                              # 돌아오면 호출자가 앱을 끈다
    def rollback(self, staged: "StagedRollback | None" = None, state_path: str | None = None) -> None: ...


def _ps_array(items: Iterable[str]) -> str:
    return "@(" + ", ".join('"' + s.replace('"', '`"') + '"' for s in items) + ")"


def render_replacer(spec: AppSpec = SPEC) -> str:
    """appspec 값으로 교체 스크립트(PowerShell 5.1)를 만든다. 제외 목록은 여기 한 곳에서만 나온다 (04 6-8 · 7장 규칙 2).

    규칙: 내장 cmdlet 위주(Constrained Language Mode 호환), 프로세스를 죽이지 않는다, /XD 는 전체 경로,
    백업 rc ≥ 8 이면 아무것도 바꾸지 않는다, 결과를 data\\update\\last_result.json 과 data\\logs\\update.log 에 남긴다.

    -Rollback (04 6-10 되돌리기): -New 는 <앱>\\_backup 이고 -NoBackup·전체 교체로 동작한다. 복사가 rc ≥ 8 이면
    같은 사본으로 한 번 더 미러하고(복구 원본이 바로 그 사본이다), 그래도 실패하면 rollback_failed 다.
    성공하면 backup_ok.json 을 지우고 결과 rolled_back, 재실행 인자 --rolled-back.
    """
    keep = spec.keep_dirs
    required = [p.replace("/", "\\") for p in spec.required_files]
    heavy_files = [p.replace("/", "\\") for p in spec.heavy_files]
    heavy_dirs = [p.replace("/", "\\") for p in spec.heavy_dirs]
    heavy_names = [os.path.basename(p) for p in spec.heavy_files]
    return f"""# {spec.name} 업데이트 교체 스크립트 — app/updater.py 가 appspec 값으로 생성한다. 손으로 고치지 않는다.
param([int]$AppPid, [string]$App, [string]$New, [string]$Id, [string]$Exe, [string]$FromVer, [string]$ToVer,
      [switch]$Lite, [switch]$NoBackup, [switch]$Rollback, [int]$WaitSec = 120, [int]$TouchSec = 30,
      [string]$CopyExe = "")
$ErrorActionPreference = "Continue"
# 되돌리기: 원본은 _backup(이전 버전 전체 사본) 이라 백업을 다시 만들지 않고 경량 제외도 쓰지 않는다
if ($Rollback) {{ $NoBackup = [switch]$true; $Lite = [switch]$false }}
$App = $App.TrimEnd("\\")
$dataDir = Join-Path $App "{keep[0]}"
$updDir = Join-Path $dataDir "update"
$log = Join-Path $dataDir "logs\\update.log"
$lock = Join-Path $updDir "updating.lock"
$stage = Join-Path (Join-Path $App "{keep[2]}") $Id
$backup = Join-Path $App "{keep[1]}"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
New-Item -ItemType Directory -Force $updDir | Out-Null
function Log($m) {{ "[{{0}}] {{1}}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m | Out-File -FilePath $log -Append -Encoding utf8 }}
function Touch-Lock {{ Set-ItemProperty -LiteralPath $lock -Name LastWriteTime -Value (Get-Date) -ErrorAction SilentlyContinue }}
function Finish($result, $backupRc, $copyRc, $restoreRc, $flag) {{
  $at = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
  $json = '{{"result":"' + $result + '","from":"' + $FromVer + '","to":"' + $ToVer + '","backup_rc":' + $backupRc + ',"copy_rc":' + $copyRc + ',"restore_rc":' + $restoreRc + ',"at":"' + $at + '"}}'
  Set-Content -LiteralPath (Join-Path $updDir "last_result.json") -Value $json -Encoding Ascii
  Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
  Log "끝: $result -> $flag"
  Start-Process -FilePath (Join-Path $App $Exe) -ArgumentList $flag -WorkingDirectory $App
}}
# 1. 시작 표식 — 앱은 이것을 10초 기다린 뒤에만 종료한다
Set-Content -LiteralPath (Join-Path $stage "started") -Value "$PID" -Encoding Ascii
Log "교체 시작 pid=$AppPid app=$App new=$New id=$Id lite=$Lite nobackup=$NoBackup rollback=$Rollback $FromVer -> $ToVer"
# 앱이 시작 표식을 기다리다 포기했는가 (표식이 늦음 → 앱이 stage\\abandoned 를 남기고 잠금을 지운 뒤 '막혀 있습니다' 를 알림).
# 그렇다면 아무것도 바꾸지 않고, 결과도 쓰지 않고, 앱도 다시 띄우지 않고 끝낸다 (앱은 살아 있다)
function Test-Abandoned {{ (Test-Path -LiteralPath (Join-Path $stage "abandoned")) -or (-not (Test-Path -LiteralPath $lock)) }}
# 2. 앱 종료 대기
try {{ Wait-Process -Id $AppPid -Timeout 60 -ErrorAction Stop }} catch {{ }}
Touch-Lock
if (Test-Abandoned) {{ Log "중단: 앱이 이 교체를 포기함(시작 표식이 늦음). 아무 파일도 바꾸지 않음"; exit 3 }}
# 3. 같은 폴더에서 실행 중인 프로세스가 없어질 때까지 기다린다 (죽이지 않는다). 접두사는 끝 구분자를 포함한다
$prefix = $App + "\\"
$deadline = (Get-Date).AddSeconds($WaitSec)
$lastTouch = Get-Date
$warned = $false
while ($true) {{
  $procs = @(Get-Process | Where-Object {{ $_.Path -and $_.Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) }})
  if ($procs.Count -eq 0) {{ break }}
  if (-not $warned) {{ Log ("이 폴더의 프로세스가 닫힐 때까지 기다림: " + (($procs | ForEach-Object {{ "$($_.ProcessName)($($_.Id))" }}) -join ", ")); $warned = $true }}
  if ((Get-Date) -gt $deadline) {{
    Log "중단: 다른 창이 계속 열려 있어 아무 파일도 바꾸지 않음"
    Finish "blocked" -1 -1 -1 "--update-blocked"
    exit 2
  }}
  if (((Get-Date) - $lastTouch).TotalSeconds -ge $TouchSec) {{ Touch-Lock; $lastTouch = Get-Date }}
  Start-Sleep -Milliseconds 700
}}
Start-Sleep -Milliseconds 500
Touch-Lock
if (Test-Abandoned) {{ Log "중단: 앱이 이 교체를 포기함(시작 표식이 늦음). 아무 파일도 바꾸지 않음"; exit 3 }}
# 4. 새 폴더의 필수 파일 (백신이 그 사이 격리했을 수 있다)
$required = {_ps_array(required)}
if (-not $Lite) {{ $required += {_ps_array(heavy_files)} }}
foreach ($f in $required) {{
  if (-not (Test-Path -LiteralPath (Join-Path $New $f))) {{ Log "필수 파일 없음: $f"; Finish "missing_file" -1 -1 -1 "--update-failed"; exit 1 }}
}}
# 제외 목록: 전체 경로. 이름만 주면 _internal 안의 같은 이름 폴더까지 빠진다
$xd = @({", ".join('(Join-Path $App "' + k + '")' for k in keep)})
# 경량 교체에서만, 교체 단계에서만 뺀다. 백업·복구는 이전 버전 전체 사본이어야 한다 (04 6-8 5단계)
$liteXd = @({", ".join('(Join-Path $App "' + d + '")' for d in heavy_dirs)})
$liteXf = {_ps_array(heavy_names)}
if ($Lite) {{ Log "경량 교체: ffmpeg\\, libmpv-2.dll 유지" }}
# robocopy 는 따로 띄우고, 끝날 때까지 $TouchSec 마다 잠금을 갱신한다. 큰 미러(되돌리기는 전체 ≈400MB)가 2분을 넘겨도
# 앱이 '진행 중인 교체' 를 '멈춘 업데이트' 로 보지 않게 (update_in_progress · stale_update_lock 은 잠금 mtime 2분 기준).
# 전체 경로로 부른다(작업 폴더의 같은 이름 실행 파일을 집지 않게). -CopyExe 는 시험용 대체 실행 파일
$rcExe = Join-Path $env:SystemRoot "System32\\robocopy.exe"
if ($CopyExe) {{ $rcExe = $CopyExe }}
$rcOut = Join-Path $stage "robocopy.out"
function Mirror($src, $dst, $useLite) {{
  $rcArgs = @($src, $dst, "/MIR", "/XD") + $xd
  if ($useLite) {{ $rcArgs += $liteXd; $rcArgs += "/XF"; $rcArgs += $liteXf }}
  $rcArgs += @("/NFL", "/NDL", "/NJH", "/NJS", "/R:2", "/W:1")
  # Start-Process 는 인자를 공백으로만 잇는다: 공백 있는 경로는 따옴표로 싸고, 끝 \\ 는 겹쳐 따옴표를 먹지 않게 한다
  $quoted = @($rcArgs | ForEach-Object {{ $s = [string]$_; if ($s.Contains(" ")) {{ if ($s.EndsWith("\\")) {{ $s += "\\" }}; '"' + $s + '"' }} else {{ $s }} }})
  Touch-Lock
  try {{
    $p = Start-Process -FilePath $rcExe -ArgumentList $quoted -NoNewWindow -PassThru -RedirectStandardOutput $rcOut -ErrorAction Stop
  }} catch {{ Log "robocopy 를 띄우지 못함: $_"; return 16 }}
  $h = $p.Handle        # 핸들을 잡아 두어야 끝난 뒤 ExitCode 를 읽을 수 있다 (PowerShell 5.1)
  while (-not $p.HasExited) {{
    Wait-Process -Id $p.Id -Timeout $TouchSec -ErrorAction SilentlyContinue
    Touch-Lock
  }}
  $code = $p.ExitCode
  if ($null -eq $code) {{ Log "robocopy 종료 코드를 읽지 못함"; return 16 }}
  return [int]$code
}}
# 5. 백업 — rc >= 8 이면 아무것도 바꾸지 않는다
$backupRc = -1
if (-not $NoBackup) {{
  # 지난 업데이트의 완료 기록을 먼저 지운다. 이번 백업이 중간에 멈추면 반쯤 바뀐 _backup 을 옛 기록이 '완료' 로
  # 가리키게 되고, 되돌리기가 그 사본을 쓰게 된다 (6-10 '불완전한 _backup 은 backup_ok 가 없어 쓰지 않는다')
  Remove-Item -LiteralPath (Join-Path $updDir "backup_ok.json") -Force -ErrorAction SilentlyContinue
  Log "백업 -> $backup"
  $backupRc = Mirror $App $backup $false
  if ($backupRc -ge 8) {{ Log "백업 실패 rc=$backupRc"; Finish "backup_failed" $backupRc -1 -1 "--update-failed"; exit 1 }}
  $at = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
  Set-Content -LiteralPath (Join-Path $updDir "backup_ok.json") -Value ('{{"version":"' + $FromVer + '","at":"' + $at + '"}}') -Encoding Ascii
}}
# 7. 교체
Log "교체 $New -> $App"
$copyRc = Mirror $New $App $Lite
if ($Rollback) {{
  $retryRc = -1
  if ($copyRc -ge 8) {{
    Log "되돌리기 복사 실패 rc=$copyRc -> 같은 사본으로 한 번 더"
    Start-Sleep -Seconds 2
    $retryRc = Mirror $New $App $false
    if ($retryRc -ge 8) {{ Log "되돌리기 실패 rc=$retryRc"; Finish "rollback_failed" $backupRc $copyRc $retryRc "--update-failed"; exit 1 }}
  }}
  Remove-Item -LiteralPath (Join-Path $updDir "backup_ok.json") -Force -ErrorAction SilentlyContinue
  Log "되돌리기 성공 rc=$copyRc/$retryRc"
  Finish "rolled_back" $backupRc $copyRc $retryRc "{ROLLBACK_FLAG}"
  exit 0
}}
if ($copyRc -ge 8) {{
  Log "교체 실패 rc=$copyRc -> 백업에서 복구"
  $restoreRc = Mirror $backup $App $false
  if ($restoreRc -ge 8) {{ Log "복구도 실패 rc=$restoreRc"; Finish "restore_failed" $backupRc $copyRc $restoreRc "--update-failed"; exit 1 }}
  Finish "copy_failed" $backupRc $copyRc $restoreRc "--update-failed"
  exit 1
}}
Log "성공 rc=$copyRc"
Finish "ok" $backupRc $copyRc -1 "--updated"
exit 0
"""


def write_replacer(path: str, spec: AppSpec = SPEC) -> None:
    """BOM 을 바이트로 직접 붙인다. 'utf-8-sig' 코덱은 업데이트 도중 로드에 실패한 전례가 있다 (ClipSlicer 0.9.5)."""
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf" + render_replacer(spec).encode("utf-8"))


def replacer_command(script: str, app_dir: str, new_dir: str, stage_id: str, exe: str, pid: int,
                     from_ver: str, to_ver: str, lite: bool, no_backup: bool = False,
                     rollback: bool = False) -> list[str]:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script,
           "-AppPid", str(pid), "-App", app_dir, "-New", new_dir, "-Id", stage_id, "-Exe", exe,
           "-FromVer", from_ver, "-ToVer", to_ver]
    if rollback:                       # 되돌리기는 언제나 전체 · 백업 없음 (스크립트도 같은 값으로 고정한다)
        return cmd + ["-NoBackup", "-Rollback"]
    if lite:
        cmd.append("-Lite")
    if no_backup:
        cmd.append("-NoBackup")
    return cmd


def write_updating_lock(data_root: str, pid: int, from_ver: str, to_ver: str) -> str:
    p = os.path.join(data_root, "update", "updating.lock")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"pid": pid, "from": from_ver, "to": to_ver,
                   "started_at": datetime.now(timezone.utc).isoformat()}, f)
    return p


def update_in_progress(data_root: str, max_age_sec: float = 120.0) -> bool:
    """교체 스크립트가 지금 이 폴더를 바꾸는 중인가 (잠금이 있고 2분 안에 갱신됨). 스크립트가 30초마다 갱신한다."""
    p = os.path.join(data_root, "update", "updating.lock")
    try:
        return time.time() - os.path.getmtime(p) < max_age_sec
    except OSError:
        return False


def stale_update_lock(data_root: str, max_age_sec: float = 120.0) -> bool:
    """잠금이 있는데 2분 넘게 갱신되지 않았고 last_result 가 없다 → '지난 업데이트가 중간에 멈췄습니다' (04 6-9)."""
    p = os.path.join(data_root, "update", "updating.lock")
    if not os.path.isfile(p):
        return False
    try:
        old = time.time() - os.path.getmtime(p) >= max_age_sec
    except OSError:
        return False
    return old and read_last_result(data_root) is None


def read_last_result(data_root: str) -> dict | None:
    p = os.path.join(data_root, "update", "last_result.json")
    try:
        with open(p, "rb") as f:
            return load_json_strict(f.read(), 64 * 1024)
    except (OSError, ValueError):
        return None


def _set_aside_last_result(data_root: str) -> str | None:
    """교체를 띄우기 직전: 지난 last_result.json 을 last_result.prev.json 으로 비킨다. 그래야 이번 교체가 중간에
    멈췄을 때 stale_update_lock('잠금 + 결과 없음')이 맞게 '멈춘 업데이트' 를 알아챈다. 비킨 경로(없으면 None)."""
    src = os.path.join(data_root, "update", "last_result.json")
    dst = os.path.join(data_root, "update", "last_result.prev.json")
    if not os.path.isfile(src):
        return None
    try:
        os.replace(src, dst)
        return dst
    except OSError:
        return None


def _restore_last_result(data_root: str, prev: str | None) -> None:
    """교체 스크립트가 시작하지 않았을 때: 비킨 결과를 되돌린다 (새 결과가 없을 때만)."""
    if not prev:
        return
    cur = os.path.join(data_root, "update", "last_result.json")
    if not os.path.exists(cur):
        try:
            os.replace(prev, cur)
        except OSError:
            pass


def read_backup_ok(data_root: str) -> dict | None:
    """data\\update\\backup_ok.json = {version, at}. 교체 스크립트가 백업 robocopy 가 rc < 8 로 끝난 뒤에만 쓴다."""
    p = os.path.join(data_root, "update", "backup_ok.json")
    try:
        with open(p, "rb") as f:
            obj = load_json_strict(f.read(), 64 * 1024)
    except (OSError, ValueError):
        return None
    return obj if _ver_or_none(obj.get("version")) is not None else None


def read_dir_version(app_like_dir: str) -> str | None:
    """<폴더>\\_internal\\VERSION 의 버전 (형식이 아니면 None)."""
    try:
        with open(os.path.join(app_like_dir, "_internal", "VERSION"), "rb") as f:
            text = f.read(64).decode("utf-8", "replace").strip().lstrip("﻿")
    except OSError:
        return None
    return text if _ver_or_none(text) is not None else None


@dataclass(frozen=True)
class RollbackStatus:
    """'이전 버전으로 되돌리기' 를 지금 할 수 있는가 (04 6-10). 메뉴는 available 일 때만 켠다."""
    available: bool
    reason: str                     # 안 되면 사유, 되면 'X → Y'
    current_version: str
    target_version: str | None = None
    backup_dir: str = ""
    repair: bool = False            # 같은 버전 사본으로 반쯤 바뀐 폴더를 고치는 경우 (멈춘 업데이트 · 복구 실패)


def rollback_status(app_dir: str, data_root: str, current_version: str, frozen: bool = True, spec: AppSpec = SPEC,
                    repair: bool = False) -> RollbackStatus:
    """조건: _backup 이 있고, backup_ok.json 이 그 사본의 버전을 가리키며, 그 버전이 지금 버전과 다르다
    (repair=True 면 같아도 된다), 사본에 필수 파일이 있고, 업데이트가 진행 중이 아니다.

    _backup 은 업데이트 뒤 '첫 정상 종료 다음 시작' 에 지워지므로(main.py 9단계, cleanup_after_update)
    되돌리기는 그 전까지만 된다. cleanup_after_update 는 backup_ok.json 을 먼저 지워 이 판정을 먼저 끈다."""
    backup = os.path.join(app_dir, spec.keep_dirs[1])

    def no(reason: str, target: str | None = None) -> RollbackStatus:
        return RollbackStatus(False, reason, current_version, target, backup, repair)
    if not frozen:
        return no("소스로 실행 중입니다 (되돌리기는 설치본에서만)")
    if not os.path.isdir(backup):
        return no("이전 버전 사본이 없습니다 (업데이트 뒤 첫 정상 종료 다음 시작에서 지워집니다)")
    ok = read_backup_ok(data_root)
    if ok is None:
        return no("이전 버전 사본의 백업 완료 기록이 없습니다 (불완전한 사본일 수 있어 쓰지 않습니다)")
    target = read_dir_version(backup)
    if target is None or target != ok["version"]:
        return no(f"사본의 버전({target})이 백업 완료 기록({ok['version']})과 다릅니다")
    if target == current_version and not repair:
        return no(f"사본이 지금 버전({current_version})과 같습니다", target)
    need = list(spec.required_files) + list(spec.heavy_files)
    missing = [p for p in need if not os.path.isfile(os.path.join(backup, *p.split("/")))]
    if missing:
        return no(f"사본에 필수 파일이 없습니다: {', '.join(missing)}", target)
    if update_in_progress(data_root):
        return no("업데이트가 진행 중입니다", target)
    verb = "복구" if target == current_version else "되돌리기"
    return RollbackStatus(True, f"{verb}: {current_version} → {target}", current_version, target, backup, repair)


ABNORMAL_STREAK_FOR_ROLLBACK = 2   # 업데이트 직후 연속 비정상 종료가 이만큼이면 되돌리기를 권한다 (04 6-9, S-31)
# 연속 비정상 종료 횟수는 crashguard.note_start(data_root, version, had_abnormal_exit) 가 센다 (통합 담당이 넘긴다).


MIXED_RESULTS = ("restore_failed", "rollback_failed")   # 프로그램 파일이 섞였을 수 있는 교체 결과


def repair_needed(data_root: str) -> bool:
    """프로그램 폴더가 반쯤 바뀌었을 수 있는가: 멈춘 업데이트이거나 마지막 결과가 restore_failed · rollback_failed.
    그렇다면 메뉴의 되돌리기도 repair=True 로 판정한다(사본이 지금 버전과 같아도 '복구' 로 켠다).
    다음 교체·되돌리기가 결과를 덮어쓰면 풀린다."""
    if stale_update_lock(data_root):
        return True
    last = read_last_result(data_root)
    return bool(last) and last.get("result") in MIXED_RESULTS


@dataclass(frozen=True)
class RollbackSuggestion:
    reason: str                     # 화면 문구 (왜 권하는가)
    status: RollbackStatus          # 권하는 되돌리기 (available)


def rollback_suggestion(app_dir: str, data_root: str, current_version: str, flags: Iterable[str] = (),
                        abnormal_streak: int = 0, frozen: bool = True,
                        spec: AppSpec = SPEC) -> RollbackSuggestion | None:
    """업데이트 실패 뒤 되돌리기를 권할지 (04 6-9 · 6-10 표). 권하면 그 되돌리기의 상태까지 돌려준다.

    1. 멈춘 업데이트(잠금이 2분 넘게 갱신되지 않고 결과 없음) + backup_ok → 반쯤 바뀌었을 수 있어 같은 버전이어도 권한다.
    2. --update-failed 이고 결과가 restore_failed(교체·복구 실패) 또는 rollback_failed(되돌리기 미러가 두 번 실패)
       → 프로그램 파일이 섞였을 수 있다. 같은 버전이어도 권한다 (rollback_failed 뒤에도 스크립트는 backup_ok 를 남긴다).
    3. 지금 버전으로의 업데이트가 성공(ok)했는데 그 뒤 연속 2회 비정상 종료 (abnormal_streak = crashguard.note_start).
    backup_ok 가 없으면(백업 실패 · 불완전한 사본) 권하지 않는다. 그 밖의 실패(copy_failed 뒤 복구 성공 등)는
    이미 이전 버전이라 권할 것이 없다."""
    flags = set(flags)
    last = read_last_result(data_root)
    if stale_update_lock(data_root):
        st = rollback_status(app_dir, data_root, current_version, frozen, spec, repair=True)
        if st.available:
            return RollbackSuggestion("지난 업데이트가 중간에 멈췄습니다. 프로그램 파일이 섞였을 수 있습니다. "
                                      f"이전 버전 {st.target_version} 사본으로 되돌릴 수 있습니다.", st)
        return None
    if "--update-failed" in flags and last and last.get("result") in MIXED_RESULTS:
        st = rollback_status(app_dir, data_root, current_version, frozen, spec, repair=True)
        if st.available:
            what = "업데이트 교체와 복구가" if last.get("result") == "restore_failed" else "이전 버전으로 되돌리기가"
            return RollbackSuggestion(f"{what} 모두 실패해 프로그램 파일이 섞였을 수 있습니다. "
                                      f"이전 버전 {st.target_version} 사본으로 다시 되돌리기를 권합니다.", st)
        return None
    if abnormal_streak >= ABNORMAL_STREAK_FOR_ROLLBACK and last and last.get("result") == "ok" \
            and last.get("to") == current_version:
        st = rollback_status(app_dir, data_root, current_version, frozen, spec)
        if st.available:
            return RollbackSuggestion(f"{current_version} 으로 업데이트한 뒤 {abnormal_streak}번 연속 비정상 종료했습니다. "
                                      f"이전 버전 {st.target_version} 으로 되돌리기를 권합니다.", st)
    return None


def execution_policy_report() -> str:
    """시작 표식이 없을 때 로그에 남길 실행 정책 (S-17)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy -List | Out-String"],
                           capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        return decode_output(r.stdout).strip()[:1000]
    except (OSError, subprocess.TimeoutExpired) as ex:
        return f"조회 실패: {ex}"


@dataclass(frozen=True)
class StagedRollback:
    """되돌리기 준비 결과: 자체 점검 관문을 통과한 _backup 과 replace.ps1·시작 표식을 둘 준비 폴더."""
    status: RollbackStatus
    stage_dir: str          # <앱>\_update\<임의 이름>
    stage_id: str
    source_dir: str         # <앱>\_backup
    selftest: dict

    @property
    def target_version(self) -> str:
        return self.status.target_version or ""


def _remove_if_no_files(path: str) -> None:
    """파일이 하나도 없는 폴더 트리만 지운다 (자체 점검이 _backup 안에 만든 빈 _update\\ 정리용)."""
    if not os.path.isdir(path):
        return
    for _dirpath, _dirs, files in os.walk(path):
        if files:
            return
    shutil.rmtree(path, ignore_errors=True)


def stage_rollback(spec: AppSpec, installed: InstalledInfo, data_root: str, selftest: bool = True,
                   repair: bool = False) -> StagedRollback:
    """되돌리기 준비: 상태 확인 → 준비 폴더 → _backup 의 exe 로 --selftest --expect-version <사본 버전> 관문 → 재확인.

    관문은 업데이트와 같은 run_selftest_gate 다(--data-dir 은 준비 폴더 아래라 사용자 data\\ 를 건드리지 않는다).
    실패하면 업데이트와 같이 준비 폴더 1개를 FAILED 로 남기고 UpdateError('selftest'). 사본은 그대로 둔다."""
    st = rollback_status(installed.app_dir, data_root, installed.version, installed.frozen, spec, repair)
    if not st.available:
        raise UpdateError("precheck", st.reason)
    root = os.path.join(installed.app_dir, spec.keep_dirs[2])
    os.makedirs(root, exist_ok=True)
    stage_dir = tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=root)
    stage_id = os.path.basename(stage_dir)
    with open(os.path.join(stage_dir, ACTIVE_MARKER), "w", encoding="ascii") as f:
        f.write(str(os.getpid()))
    try:
        report: dict = {}
        if selftest:
            # 사본의 exe 가 자기 설치 폴더(_backup) 아래에 _update\ 를 만들 수 있다. 원래 없던 빈 폴더면 치운다
            # (그대로 두면 되돌리기 미러가 그것을 <앱>\_update 로 옮기려 한다)
            inner = os.path.join(st.backup_dir, spec.keep_dirs[2])
            existed = os.path.exists(inner)
            try:
                report = run_selftest_gate(st.backup_dir, st.target_version or "", installed.channel, stage_dir, spec)
            finally:
                if not existed:
                    _remove_if_no_files(inner)
        # 관문이 도는 동안(최대 90초) 시작 정리 스레드가 사본을 지우기 시작했을 수 있다
        again = rollback_status(installed.app_dir, data_root, installed.version, installed.frozen, spec, repair)
        if not again.available or again.target_version != st.target_version:
            raise UpdateError("precheck", f"되돌리기를 준비하는 동안 사본이 바뀌었습니다: {again.reason}")
        return StagedRollback(st, stage_dir, stage_id, st.backup_dir, report)
    except UpdateError as ex:
        if ex.kind == "selftest":
            keep_one_failed_stage(root, stage_dir)
        else:
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


class RobocopyApplier:
    """MVP 의 in-place 교체. v1 에 PointerApplier 를 같은 자리에 꽂을 수 있게 Applier 모양을 지킨다.

    pid: 교체 스크립트가 종료를 기다릴 앱 프로세스 (기본 = 이 프로세스).
    data_backup: 교체·되돌리기 직전 data 백업 (04 6-7). (from_ver, to_ver) 를 받아 실패하면 예외를 던지는 함수.
                 업데이터는 data\\ 를 고치지 않으므로 백업 내용은 백업 모듈이 정한다 (통합 담당이 연결).
    """

    def __init__(self, spec: AppSpec, installed: InstalledInfo, data_root: str, pid: int | None = None,
                 data_backup: Callable[[str, str], object] | None = None):
        self.spec = spec
        self.installed = installed
        self.data_root = data_root
        self.pid = os.getpid() if pid is None else pid
        self.data_backup = data_backup

    def _preflight(self, need_bytes: int) -> tuple[list[str], list[str]]:
        from . import paths
        problems: list[str] = []
        warnings: list[str] = []
        app = self.installed.app_dir
        if not self.installed.frozen:
            problems.append("소스로 실행 중입니다 (git pull 로 갱신)")
        if paths.is_network_path(app):
            problems.append("네트워크 드라이브에서는 자동 업데이트를 지원하지 않습니다")
        try:
            probe = os.path.join(app, f".update_write_test_{os.getpid()}")
            with open(probe, "w"):
                pass
            os.remove(probe)
        except OSError:
            problems.append("프로그램 폴더에 쓸 수 없습니다 (읽기 전용 위치). 수동 업데이트가 필요합니다")
        try:
            free = shutil.disk_usage(app).free
            if free < need_bytes * 1.2:
                problems.append(f"디스크 여유가 부족합니다 (필요 {need_bytes * 1.2 / 1e6:.0f} MB, 여유 {free / 1e6:.0f} MB)")
        except OSError:
            pass
        if paths.is_under_onedrive(app):
            warnings.append("OneDrive 아래에서 실행 중입니다. 동기화를 잠시 멈추거나 %LOCALAPPDATA%\\Programs\\ChatCoach 로 옮기세요")
        return problems, warnings

    def preflight(self, staged: StagedUpdate) -> tuple[list[str], list[str]]:
        """앱을 끄기 전에 확인한다 (04 6-6). (막는 문제, 경고) 를 돌려준다.
        디스크: 압축 해제 크기 + 현재 설치 크기(_backup 몫) × 1.2."""
        app = self.installed.app_dir
        try:
            need = staged.release.manifest.asset(staged.release.chosen).unpacked_size
            need += _dir_size(app, exclude=tuple(os.path.join(app, k) for k in self.spec.keep_dirs))
        except OSError:
            need = 0
        return self._preflight(need)

    def preflight_rollback(self, staged: StagedRollback) -> tuple[list[str], list[str]]:
        """되돌리기도 같은 조건 (04 6-10 '교체 전 조건과 시작 표식 대기도 같다'). 디스크는 사본 크기 × 1.2."""
        try:
            need = _dir_size(staged.source_dir)
        except OSError:
            need = 0
        return self._preflight(need)

    def _run_data_backup(self, from_ver: str, to_ver: str) -> None:
        if self.data_backup is None:
            return
        try:
            self.data_backup(from_ver, to_ver)
        except Exception as ex:   # 백업 모듈의 어떤 실패든 교체를 멈춘다 (6-7 '실패하면 업데이트를 멈춘다')
            raise UpdateError("precheck", f"교체 직전 data 백업 실패: {ex}") from ex

    def apply(self, staged: StagedUpdate) -> None:
        """잠금 선기록 → replace.ps1 생성 → 실행 → 시작 표식 10초 대기. 돌아오면 호출자가 즉시 앱을 끈다.
        표식이 없으면 잠금을 지우고 UpdateError('blocked') — 앱은 살아 있다."""
        problems, _ = self.preflight(staged)
        if problems:
            raise UpdateError("precheck", "; ".join(problems))
        self._run_data_backup(self.installed.version, staged.release.version)
        self._launch(staged.stage_dir, staged.stage_id, staged.new_dir, staged.release.version, staged.lite, False)

    def rollback(self, staged: StagedRollback | None = None, state_path: str | None = None) -> None:
        """'이전 버전으로 되돌리기' (04 6-10). 메뉴와 실패 뒤 제안이 이 함수를 쓴다.

        staged 가 없으면 stage_rollback 부터 한다(자체 점검 관문 포함, 최대 90초 — UI 는 워커에서 부른다).
        교체 전 조건 → data 백업 → skip_version 에 지금 버전 기록 → replace.ps1 -Rollback → 시작 표식 대기.
        돌아오면 호출자가 즉시 앱을 끈다. 시작 표식이 없으면 skip_version 을 되돌리고 UpdateError('blocked')."""
        if staged is None:
            staged = stage_rollback(self.spec, self.installed, self.data_root)
        target = staged.target_version
        problems, _ = self.preflight_rollback(staged)
        try:
            if problems:
                raise UpdateError("precheck", "; ".join(problems))
            self._run_data_backup(self.installed.version, target)
        except UpdateError:
            shutil.rmtree(staged.stage_dir, ignore_errors=True)   # 아무것도 시작하지 않았다: 준비 폴더를 남기지 않는다
            raise
        sp = state_path or os.path.join(self.data_root, "update", "state.json")
        state = UpdateState.load(sp)
        prev_skip = state.skip_version
        mark_skip = target != self.installed.version          # 같은 버전 복구(repair)는 건너뛰기에 넣지 않는다
        if mark_skip:
            state.skip_version = self.installed.version       # 되돌린 버전은 다시 권하지 않는다 (더 높은 버전은 알린다)
            state.save(sp)
        try:
            self._launch(staged.stage_dir, staged.stage_id, staged.source_dir, target, False, True)
        except UpdateError:
            if mark_skip:
                state = UpdateState.load(sp)
                state.skip_version = prev_skip
                state.save(sp)
            raise

    def _launch(self, stage_dir: str, stage_id: str, new_dir: str, to_ver: str, lite: bool, rollback: bool) -> None:
        app = self.installed.app_dir
        lock = write_updating_lock(self.data_root, self.pid, self.installed.version, to_ver)
        prev_result = _set_aside_last_result(self.data_root)
        script = os.path.join(stage_dir, "replace.ps1")
        out_path = os.path.join(stage_dir, "replacer.out")
        try:
            write_replacer(script, self.spec)
            cmd = replacer_command(script, app, new_dir, stage_id, self.spec.exe_name, self.pid,
                                   self.installed.version, to_ver, lite, rollback=rollback)
            LOG.info("교체 스크립트 실행: %s", " ".join(cmd))
            with open(out_path, "wb") as out:
                # 숨긴 콘솔 + 새 프로세스 그룹: 앱이 종료돼도 살아남고 창은 안 뜬다 (DETACHED_PROCESS 는 powershell 이 못 뜸)
                proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, close_fds=False,
                                        stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, cwd=app)
        except OSError as ex:
            # powershell 이 없거나 스크립트를 쓸 수 없음: 잠금을 남기면 2분 동안 앱이 '적용 중' 으로 닫힌다
            try:
                os.remove(lock)
            except OSError:
                pass
            _restore_last_result(self.data_root, prev_result)
            LOG.error("교체 스크립트를 띄우지 못함: %s", ex)
            raise UpdateError("blocked", f"이 PC 에서는 자동 교체가 막혀 있습니다 (PowerShell 을 실행할 수 없음: {ex}). "
                                         "수동 업데이트 안내를 따라 주세요.") from ex
        marker = os.path.join(stage_dir, "started")
        deadline = time.monotonic() + self.spec.replacer_start_wait_sec
        while time.monotonic() < deadline:
            if os.path.isfile(marker):
                LOG.info("교체 스크립트 시작 확인 (%s%s). 앱을 종료합니다", stage_id, ", 되돌리기" if rollback else "")
                return
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        if os.path.isfile(marker):            # 마지막 확인 (시간이 다 된 바로 그때 표식이 생김)
            LOG.info("교체 스크립트 시작 확인 (%s, 마감 직후). 앱을 종료합니다", stage_id)
            return
        # 포기한다. 늦게 뜬 스크립트가 앱 종료를 기다렸다가 교체·되돌리기를 이어 가지 않게 표식을 먼저 남기고
        # (스크립트가 종료 대기 뒤 이것과 잠금을 본다) 아직 살아 있으면 끝낸다. 사용자는 '막혀 있습니다' 를 듣는다
        try:
            with open(os.path.join(stage_dir, "abandoned"), "w", encoding="ascii") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as ex:
                LOG.warning("늦은 교체 스크립트를 끝내지 못함 (%s). 스크립트가 포기 표식을 보고 스스로 멈춥니다",
                            type(ex).__name__)
        try:
            os.remove(lock)
        except OSError:
            pass
        _restore_last_result(self.data_root, prev_result)
        try:
            with open(out_path, "rb") as f:
                tail = decode_output(f.read())[-800:]
        except OSError:
            tail = ""
        policy = execution_policy_report()
        LOG.error("교체 스크립트가 시작하지 않음 (exit=%s). 출력: %s | 실행 정책: %s", proc.poll(), tail, policy)
        raise UpdateError("blocked", "이 PC 에서는 자동 교체가 막혀 있습니다 (PowerShell 스크립트가 시작되지 않음). "
                                     "수동 업데이트 안내를 따라 주세요. 자세한 내용은 data\\logs\\chatcoach.log")


def rollback_now(spec: AppSpec, installed: InstalledInfo, data_root: str, state_path: str | None = None,
                 pid: int | None = None, data_backup: Callable[[str, str], object] | None = None,
                 repair: bool = False) -> StagedRollback:
    """메뉴 '이전 버전으로 되돌리기' 와 실패 뒤 제안의 [되돌리기] 가 부르는 한 함수 (04 6-10).
    준비(관문) → 교체 전 조건 → 스크립트 시작까지. 돌아오면 호출자가 즉시 앱을 끈다. 실패는 UpdateError."""
    staged = stage_rollback(spec, installed, data_root, repair=repair)
    RobocopyApplier(spec, installed, data_root, pid=pid, data_backup=data_backup).rollback(staged, state_path)
    return staged


def _dir_size(root: str, exclude: tuple[str, ...] = ()) -> int:
    total = 0
    ex = tuple(os.path.normcase(e) for e in exclude)
    for dirpath, dirs, files in os.walk(root):
        if os.path.normcase(dirpath) in ex:
            dirs[:] = []
            continue
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


# --------------------------------------------------------------------------- 시작 뒤 정리
def cleanup_after_update(app_dir: str, data_root: str, current_version: str) -> int:
    """정상 시작에서 호출. 지난 교체가 성공했고 그 대상이 지금 버전이면 _backup 과 backup_ok 를 지운다 (04 6-9).
    되돌리기가 성공(rolled_back)해 사본이 지금 버전과 같아진 경우도 지운다 (되돌릴 곳이 없는 650MB 사본).
    --update-failed 로 켜진 경우는 호출하지 않는다 (사본 보존). 지운 바이트 수를 돌려준다.

    backup_ok.json 을 **먼저** 지운다: rollback_status 가 곧바로 '불가' 가 되어, 지우는 도중의 사본으로
    되돌리기를 시작하지 않는다."""
    last = read_last_result(data_root)
    backup = os.path.join(app_dir, "_backup")
    if not last or last.get("to") != current_version or not os.path.isdir(backup):
        return 0
    result = last.get("result")
    if result == "rolled_back":
        if read_dir_version(backup) != current_version:
            return 0
    elif result != "ok":
        return 0
    for name in ("backup_ok.json",):
        try:
            os.remove(os.path.join(data_root, "update", name))
        except OSError:
            pass
    size = _dir_size(backup)
    shutil.rmtree(backup, ignore_errors=True)
    if os.path.isdir(backup):
        LOG.warning("이전 버전 백업을 다 지우지 못함: %s", backup)
    else:
        LOG.info("이전 버전 백업 삭제: %.0f MB", size / 1e6)
    return size
