# vendored from vtubeadmirer/clipslicer@07d13cc (app/updater.py) — 변경: 확인·준비·적용 분리, 서명 필수 검증(fail-closed),
#   보존 목록을 appspec 한 곳에서 생성(data·_backup·_update), zip 레이아웃·크기 검사, 다운그레이드·발행 시각 하한 거부,
#   준비 폴더 <앱>\_update\<임의 이름>, 교체 전 --selftest 관문, 교체 스크립트 시작 표식 10초 대기, 백업 rc 검사,
#   보조 프로세스 강제 종료 삭제, 3상태 확인과 24h/1h 재시도, 1차 고정 링크 → 2차 REST API. Qt 를 import 하지 않는다.
r"""업데이트: 확인(check) → 준비(stage) → 적용(Applier).

흐름과 규칙은 docs/design/04-update-and-release.md 5·6장이 정한다. 여기서는 '어느 함수가 무엇을 지키는가' 만 적는다.

- check:  latest.json + .minisig 를 받아 크기 상한 → 서명(app/minisign.py, 내장 공개키) → 엄격 JSON → 필드 →
          버전·발행 시각 하한 → 자산 규칙 순으로 검사한다 (5-4). 하나라도 틀리면 '새 버전 아님' 이 아니라 '실패(사유)' 다.
          상태는 셋 중 하나: newer / latest / failed. 확인 시각은 결과가 나온 뒤에 쓴다 (S-41).
- stage:  <앱>\_update\<임의 이름>\ 에 받고(크기 정확 일치, sha256), validate_zip 의 7가지 검사를 거쳐 풀고,
          새 exe 를 --selftest --expect-version 으로 돌려 통과해야 StagedUpdate 를 돌려준다 (6-4~6-6).
- apply:  RobocopyApplier 가 updating.lock 을 먼저 쓰고 appspec 값으로 생성한 replace.ps1 을 띄운 뒤,
          시작 표식을 최대 10초 기다린다. 표식이 없으면 앱을 끄지 않는다 (6-8, S-17).

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

LOG = logging.getLogger("streamlabeler")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0
TEST_PUBKEY_ENV = "STREAMLABELER_TEST_PUBKEY"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STAGE_PREFIX = "upd-"
ACTIVE_MARKER = "ACTIVE"      # 준비 폴더가 사용 중(pid). 그 pid 가 살아 있으면 정리하지 않는다
FAILED_MARKER = "FAILED"      # 자체 점검 실패로 원인 분석용으로 남긴 폴더


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
    """검증에 쓸 공개키. test·source 채널에서만 환경 변수 STREAMLABELER_TEST_PUBKEY 가 내장 키를 대신한다 (04 12-2).
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
@dataclass
class UpdateState:
    last_ok_at: str | None = None
    last_fail_at: str | None = None
    last_fail_reason: str | None = None
    last_seen_version: str | None = None
    skip_version: str | None = None

    @classmethod
    def load(cls, path: str) -> "UpdateState":
        try:
            with open(path, "rb") as f:
                obj = load_json_strict(f.read(), 64 * 1024)
        except (OSError, ValueError):
            return cls()
        st = cls()
        for k in ("last_ok_at", "last_fail_at", "last_fail_reason", "last_seen_version", "skip_version"):
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

    def record_ok(self, now: datetime, seen_version: str | None) -> None:
        self.last_ok_at = now.astimezone(timezone.utc).isoformat()
        self.last_seen_version = seen_version

    def record_fail(self, now: datetime, reason: str) -> None:
        self.last_fail_at = now.astimezone(timezone.utc).isoformat()
        self.last_fail_reason = reason[:500]


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


def fetch_manifest(spec: AppSpec, installed: InstalledInfo, base_url: str | None = None) -> tuple[bytes, bytes | None]:
    """(latest.json 바이트, .minisig 바이트 또는 None). 1차 고정 링크 → 2차 REST API (04 6-2).

    서명 파일이 404 이면 None 을 돌려준다 (evaluate 가 '서명 없음' 으로 거부한다). 둘 다 실패하면 UpdateError('network').
    """
    name, sig_name = "latest.json", "latest.json.minisig"
    errors: list[str] = []
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
              fetch: Callable[..., tuple[bytes, bytes | None]] = fetch_manifest) -> CheckResult | None:
    """주기 판단 → 받기 → 평가 → 상태 저장. 확인할 때가 아니면 None (force 면 항상 확인).
    확인 시각은 결과가 나온 뒤에 쓴다. 네트워크 실패는 failed(network) 로 돌려주고 1시간 뒤 다시 확인하게 기록한다."""
    now = now or datetime.now(timezone.utc)
    state = UpdateState.load(state_path)
    if not force and not is_due(state, now, spec):
        return None
    base = allowed_base_url(installed, base_url)
    keys = list(pubkeys) if pubkeys is not None else effective_pubkeys(spec, installed)
    try:
        data, sig = fetch(spec, installed, base)
    except UpdateError as ex:
        state.record_fail(now, ex.message)
        state.save(state_path)
        LOG.info("업데이트 확인 실패: %s", ex.message)
        return CheckResult("failed", ex.message, ex.kind)
    result = evaluate(data, sig, installed, keys, spec, skip_version=state.skip_version)
    if result.ok:
        state.record_ok(now, result.release.version if result.release else installed.version)
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
    new_dir: str            # <stage_dir>\new\StreamLabeler
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


def run_selftest_gate(new_app_dir: str, version: str, channel: str, stage_dir: str, spec: AppSpec = SPEC) -> dict:
    """준비 폴더의 새 exe 로 --selftest. 통과하지 못하면 UpdateError('selftest'). 사용자의 data\\ 는 건드리지 않는다."""
    exe = os.path.join(new_app_dir, spec.exe_name)
    out = os.path.join(stage_dir, "selftest.json")
    data_dir = os.path.join(stage_dir, "selftest-data")
    cmd = [exe, "--selftest", out, "--expect-version", version, "--expect-channel", channel, "--data-dir", data_dir]
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
            result = run_selftest_gate(new_dir, release.version, installed.channel, stage_dir, spec)
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
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if p != keep and name.startswith(STAGE_PREFIX) and os.path.isdir(p) and not stage_in_use(p):
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
    def rollback(self) -> None: ...


def _ps_array(items: Iterable[str]) -> str:
    return "@(" + ", ".join('"' + s.replace('"', '`"') + '"' for s in items) + ")"


def render_replacer(spec: AppSpec = SPEC) -> str:
    """appspec 값으로 교체 스크립트(PowerShell 5.1)를 만든다. 제외 목록은 여기 한 곳에서만 나온다 (04 6-8 · 7장 규칙 2).

    규칙: 내장 cmdlet 위주(Constrained Language Mode 호환), 프로세스를 죽이지 않는다, /XD 는 전체 경로,
    백업 rc ≥ 8 이면 아무것도 바꾸지 않는다, 결과를 data\\update\\last_result.json 과 data\\logs\\update.log 에 남긴다.
    """
    keep = spec.keep_dirs
    required = [p.replace("/", "\\") for p in spec.required_files]
    heavy_files = [p.replace("/", "\\") for p in spec.heavy_files]
    heavy_dirs = [p.replace("/", "\\") for p in spec.heavy_dirs]
    heavy_names = [os.path.basename(p) for p in spec.heavy_files]
    return f"""# {spec.name} 업데이트 교체 스크립트 — app/updater.py 가 appspec 값으로 생성한다. 손으로 고치지 않는다.
param([int]$AppPid, [string]$App, [string]$New, [string]$Id, [string]$Exe, [string]$FromVer, [string]$ToVer,
      [switch]$Lite, [switch]$NoBackup, [int]$WaitSec = 120)
$ErrorActionPreference = "Continue"
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
Log "교체 시작 pid=$AppPid app=$App new=$New id=$Id lite=$Lite nobackup=$NoBackup $FromVer -> $ToVer"
# 2. 앱 종료 대기
try {{ Wait-Process -Id $AppPid -Timeout 60 -ErrorAction Stop }} catch {{ }}
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
  if (((Get-Date) - $lastTouch).TotalSeconds -ge 30) {{ Touch-Lock; $lastTouch = Get-Date }}
  Start-Sleep -Milliseconds 700
}}
Start-Sleep -Milliseconds 500
# 4. 새 폴더의 필수 파일 (백신이 그 사이 격리했을 수 있다)
$required = {_ps_array(required)}
if (-not $Lite) {{ $required += {_ps_array(heavy_files)} }}
foreach ($f in $required) {{
  if (-not (Test-Path -LiteralPath (Join-Path $New $f))) {{ Log "필수 파일 없음: $f"; Finish "missing_file" -1 -1 -1 "--update-failed"; exit 1 }}
}}
# 제외 목록: 전체 경로. 이름만 주면 _internal 안의 같은 이름 폴더까지 빠진다
$xd = @({", ".join('(Join-Path $App "' + k + '")' for k in keep)})
$xf = @()
if ($Lite) {{ $xd += @({", ".join('(Join-Path $App "' + d + '")' for d in heavy_dirs)}); $xf += {_ps_array(heavy_names)}; Log "경량 교체: ffmpeg\\, libmpv-2.dll 유지" }}
function Mirror($src, $dst) {{
  $rcArgs = @($src, $dst, "/MIR", "/XD") + $xd
  if ($xf.Count -gt 0) {{ $rcArgs += "/XF"; $rcArgs += $xf }}
  $rcArgs += @("/NFL", "/NDL", "/NJH", "/NJS", "/R:2", "/W:1")
  & robocopy @rcArgs | Out-Null
  return $LASTEXITCODE
}}
# 5. 백업 — rc >= 8 이면 아무것도 바꾸지 않는다
$backupRc = -1
if (-not $NoBackup) {{
  Log "백업 -> $backup"
  $backupRc = Mirror $App $backup
  if ($backupRc -ge 8) {{ Log "백업 실패 rc=$backupRc"; Finish "backup_failed" $backupRc -1 -1 "--update-failed"; exit 1 }}
  $at = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
  Set-Content -LiteralPath (Join-Path $updDir "backup_ok.json") -Value ('{{"version":"' + $FromVer + '","at":"' + $at + '"}}') -Encoding Ascii
}}
# 7. 교체
Log "교체 $New -> $App"
$copyRc = Mirror $New $App
if ($copyRc -ge 8) {{
  Log "교체 실패 rc=$copyRc -> 백업에서 복구"
  $restoreRc = Mirror $backup $App
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
                     from_ver: str, to_ver: str, lite: bool, no_backup: bool = False) -> list[str]:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script,
           "-AppPid", str(pid), "-App", app_dir, "-New", new_dir, "-Id", stage_id, "-Exe", exe,
           "-FromVer", from_ver, "-ToVer", to_ver]
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


def execution_policy_report() -> str:
    """시작 표식이 없을 때 로그에 남길 실행 정책 (S-17)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy -List | Out-String"],
                           capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        return decode_output(r.stdout).strip()[:1000]
    except (OSError, subprocess.TimeoutExpired) as ex:
        return f"조회 실패: {ex}"


class RobocopyApplier:
    """MVP 의 in-place 교체. v1 에 PointerApplier 를 같은 자리에 꽂을 수 있게 Applier 모양을 지킨다."""

    def __init__(self, spec: AppSpec, installed: InstalledInfo, data_root: str):
        self.spec = spec
        self.installed = installed
        self.data_root = data_root

    def preflight(self, staged: StagedUpdate) -> tuple[list[str], list[str]]:
        """앱을 끄기 전에 확인한다 (04 6-6). (막는 문제, 경고) 를 돌려준다."""
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
            need = staged.release.manifest.asset(staged.release.chosen).unpacked_size
            installed_size = _dir_size(app, exclude=tuple(os.path.join(app, k) for k in self.spec.keep_dirs))
            free = shutil.disk_usage(app).free
            if free < (need + installed_size) * 1.2:
                problems.append(f"디스크 여유가 부족합니다 (필요 {((need + installed_size) * 1.2) / 1e6:.0f} MB, 여유 {free / 1e6:.0f} MB)")
        except OSError:
            pass
        if paths.is_under_onedrive(app):
            warnings.append("OneDrive 아래에서 실행 중입니다. 동기화를 잠시 멈추거나 %LOCALAPPDATA%\\Programs\\StreamLabeler 로 옮기세요")
        return problems, warnings

    def apply(self, staged: StagedUpdate) -> None:
        """잠금 선기록 → replace.ps1 생성 → 실행 → 시작 표식 10초 대기. 돌아오면 호출자가 즉시 앱을 끈다.
        표식이 없으면 잠금을 지우고 UpdateError('blocked') — 앱은 살아 있다."""
        problems, _ = self.preflight(staged)
        if problems:
            raise UpdateError("precheck", "; ".join(problems))
        app = self.installed.app_dir
        lock = write_updating_lock(self.data_root, os.getpid(), self.installed.version, staged.release.version)
        script = os.path.join(staged.stage_dir, "replace.ps1")
        write_replacer(script, self.spec)
        cmd = replacer_command(script, app, staged.new_dir, staged.stage_id, self.spec.exe_name, os.getpid(),
                               self.installed.version, staged.release.version, staged.lite)
        LOG.info("교체 스크립트 실행: %s", " ".join(cmd))
        out_path = os.path.join(staged.stage_dir, "replacer.out")
        with open(out_path, "wb") as out:
            # 숨긴 콘솔 + 새 프로세스 그룹: 앱이 종료돼도 살아남고 창은 안 뜬다 (DETACHED_PROCESS 는 powershell 이 못 뜸)
            proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, close_fds=False,
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, cwd=app)
        marker = os.path.join(staged.stage_dir, "started")
        deadline = time.monotonic() + self.spec.replacer_start_wait_sec
        while time.monotonic() < deadline:
            if os.path.isfile(marker):
                LOG.info("교체 스크립트 시작 확인 (%s). 앱을 종료합니다", staged.stage_id)
                return
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        try:
            os.remove(lock)
        except OSError:
            pass
        try:
            with open(out_path, "rb") as f:
                tail = decode_output(f.read())[-800:]
        except OSError:
            tail = ""
        policy = execution_policy_report()
        LOG.error("교체 스크립트가 시작하지 않음 (exit=%s). 출력: %s | 실행 정책: %s", proc.poll(), tail, policy)
        raise UpdateError("blocked", "이 PC 에서는 자동 교체가 막혀 있습니다 (PowerShell 스크립트가 시작되지 않음). "
                                     "수동 업데이트 안내를 따라 주세요. 자세한 내용은 data\\logs\\streamlabeler.log")

    def rollback(self) -> None:
        raise UpdateError("precheck", "되돌리기는 M4 에서 넣는다 (04 15장)")


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
    --update-failed 로 켜진 경우는 호출하지 않는다 (사본 보존). 지운 바이트 수를 돌려준다."""
    last = read_last_result(data_root)
    backup = os.path.join(app_dir, "_backup")
    if not last or last.get("result") != "ok" or last.get("to") != current_version or not os.path.isdir(backup):
        return 0
    size = _dir_size(backup)
    shutil.rmtree(backup, ignore_errors=True)
    for name in ("backup_ok.json",):
        try:
            os.remove(os.path.join(data_root, "update", name))
        except OSError:
            pass
    if os.path.isdir(backup):
        LOG.warning("이전 버전 백업을 다 지우지 못함: %s", backup)
    else:
        LOG.info("이전 버전 백업 삭제: %.0f MB", size / 1e6)
    return size
