"""앱 고유 값의 단일 출처.

이름·저장소·exe·자산 이름·보존 폴더·업데이트 공개키처럼 '이 앱이 무엇인가' 를 정하는 값은 모두 여기 있다.
다른 모듈은 이 값을 조립해서 쓰고, 같은 값을 다시 적지 않는다 (docs/design/02-architecture.md 3-2, 04 7장 규칙 2).
build.ps1 · tools/*.py 도 `python -c "from app.appspec import SPEC; ..."` 로 읽는다.

PySide6 를 import 하지 않는다 (tools 와 테스트가 Qt 없이 읽는다).
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass

# ASCII 숫자만, 끝 줄바꿈 불허 ($ 는 끝 줄바꿈 앞에서도 맞고 \d 는 유니코드 숫자도 받는다)
VERSION_RE = re.compile(r"\A[0-9]+\.[0-9]+\.[0-9]+\Z")


@dataclass(frozen=True)
class PublicKey:
    """minisign 형식 Ed25519 공개키 하나.

    `line` 은 .pub 파일의 둘째 줄(base64: "Ed" 2바이트 + 키 ID 8바이트 + 공개키 32바이트).
    키 ID 표시는 minisign 과 같이 8바이트를 리틀엔디언 정수로 읽은 대문자 16진수 16자리다.
    """
    label: str   # "K1"(운용) · "K2"(예비)
    line: str

    @property
    def raw(self) -> bytes:
        return base64.b64decode(self.line, validate=True)

    @property
    def key_id(self) -> str:
        raw = self.raw
        if len(raw) != 42 or raw[:2] != b"Ed":
            raise ValueError(f"공개키 형식이 아닙니다: {self.label}")
        return f"{int.from_bytes(raw[2:10], 'little'):016X}"


# ---------------------------------------------------------------------------
# 업데이트 공개키. K1 = 운용, K2 = 예비 (04 9-2). 둘 다 첫 공개 빌드 **전에** 들어가야 한다.
# 비밀키는 저장소·OneDrive 밖(%LOCALAPPDATA%\ChatCoach-signing)에서 tools/sign_manifest.py keygen 으로 만든다.
# 비어 있으면: 업데이트 확인은 '공개키 없음' 으로 실패하고, release 채널 자체 점검(pubkeys)이 실패한다.
# 그래서 공개키 없는 빌드는 CI 를 통과하지 못한다 (fail-closed).
#
# 2026-10-20 생성 (tools/sign_manifest.py keygen). 키 ID: K1 F0DA83E17841C95C · K2 7297CBEEF96A118F.
# 비밀키·암호는 저장소 밖에만 있다. 키를 바꾸는 절차는 04 9-4 (분실·유출 때만, 정기 교체 없음).
UPDATE_PUBKEYS: tuple[PublicKey, ...] = (
    PublicKey("K1", "RWRcyUF44YPa8Bfo4jqfCXr/dSMQlH853FAHFR5U58miyChvUp/iC0gp"),
    PublicKey("K2", "RWSPEWr57suXcry8hkMgzUrqbh2tgeod3EiIJ/YGKa1NbwwrG0QA5B5u"),
)


@dataclass(frozen=True)
class AppSpec:
    name: str = "ChatCoach"
    repo: str = "vtubeadmirer/chatcoach"
    exe_name: str = "ChatCoach.exe"
    # 포터블 폴더 안에서 업데이트가 건드리지 않는 것. 교체 스크립트의 /XD 는 이 세 이름의 전체 경로로 생성된다 (04 6-8).
    keep_dirs: tuple[str, ...] = ("data", "_backup", "_update")
    # 경량 zip 에서 빠지는 것 (전체 zip 과의 차이). 경로는 <앱> 기준, 구분자는 '/'.
    heavy_dirs: tuple[str, ...] = ("_internal/ffmpeg",)
    heavy_files: tuple[str, ...] = ("_internal/libmpv-2.dll",)
    # 교체 스크립트가 새 폴더에서 확인하는 필수 파일 (04 6-8 4단계). 경량이면 heavy_files 는 보지 않는다.
    required_files: tuple[str, ...] = ("ChatCoach.exe", "_internal/python312.dll", "_internal/VERSION")
    # zip 레이아웃: 최상위 폴더 하나
    zip_top: str = "ChatCoach"
    manifest_version: int = 1
    # 이 앱이 읽고 쓰는 라벨 형식 버전 (03). 매니페스트의 label_schema_version 과 비교해 경고만 한다.
    label_schema_version: int = 1
    update_pubkeys: tuple[PublicKey, ...] = UPDATE_PUBKEYS
    # 매니페스트 검증 상한 (04 5-4 · 6-5)
    max_manifest_bytes: int = 64 * 1024
    max_sig_bytes: int = 2 * 1024
    max_notes_chars: int = 8 * 1024
    max_zip_bytes: int = 2 * 1024 ** 3 - 1
    max_zip_entries: int = 3000
    max_unpacked_bytes: int = 2 * 1024 ** 3
    # 업데이트 확인 주기 (04 6-2): 성공 뒤 24시간, 실패 뒤 1시간
    check_interval_ok_sec: int = 24 * 3600
    check_interval_fail_sec: int = 3600
    # 네트워크
    http_timeout_sec: float = 10.0
    download_stall_sec: float = 30.0
    max_redirects: int = 5
    # 교체 스크립트 시작 표식 대기 (04 6-8 4단계)
    replacer_start_wait_sec: float = 10.0
    selftest_timeout_sec: float = 90.0

    # ---- 파생 값 (여기서만 조립한다) ----
    @property
    def github_base(self) -> str:
        return "https://github.com/"

    @property
    def api_base(self) -> str:
        return f"https://api.github.com/repos/{self.repo}"

    def user_agent(self, version: str) -> str:
        return f"{self.name}-updater/{version}"

    def asset_name(self, version: str, kind: str) -> str:
        """kind: 'full' | 'lite'. 04 5-2 의 이름 규칙."""
        self._check_version(version)
        if kind == "full":
            return f"{self.name}-v{version}-win64.zip"
        if kind == "lite":
            return f"{self.name}-v{version}-win64-update.zip"
        raise ValueError(f"알 수 없는 자산 종류: {kind}")

    def asset_url(self, version: str, name: str) -> str:
        self._check_version(version)
        return f"{self.github_base}{self.repo}/releases/download/v{version}/{name}"

    def latest_download_url(self, name: str) -> str:
        """1차 확인 경로: GitHub 의 '최신 릴리스 자산 고정 링크' (04 6-2)."""
        return f"{self.github_base}{self.repo}/releases/latest/download/{name}"

    def release_page_url(self, version: str) -> str:
        self._check_version(version)
        return f"{self.github_base}{self.repo}/releases/tag/v{version}"

    def trusted_comment(self, version: str) -> str:
        self._check_version(version)
        return f"{self.name} latest.json v{version}"

    def tag(self, version: str) -> str:
        self._check_version(version)
        return f"v{version}"

    def _check_version(self, version: str) -> None:
        if not VERSION_RE.match(version or ""):
            raise ValueError(f"버전 형식이 아닙니다: {version!r}")


SPEC = AppSpec()
