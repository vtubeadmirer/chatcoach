"""latest.json 의 minisign 서명 — 키 생성 · 서명 · 검증 (개발 PC 전용, docs/design/04-update-and-release.md 9장).

사용:
  python tools/sign_manifest.py keygen [--dir %LOCALAPPDATA%\\ChatCoach-signing] [--label K1] [--password-file <파일>]
  python tools/sign_manifest.py sign latest.json [--key <비밀키 파일>] [--out latest.json.minisig]
  python tools/sign_manifest.py verify latest.json [--sig latest.json.minisig] [--pubkey <파일 또는 base64 줄>]
  python tools/sign_manifest.py verify --url https://github.com/<repo>/releases/latest/download/latest.json

형식은 공식 minisign 과 호환되게 둔다(비밀키는 scrypt 로 암호화, 서명은 BLAKE2b-512 prehash 'ED' 모드).
서명·키 생성은 PyNaCl(libsodium)로 하고, 검증은 앱과 같은 app/minisign.py(순수 Python)로 한다.
그래서 이 도구가 만든 서명이 앱에서 검증된다는 것이 서명 직후 바로 확인된다.

규칙 (04 9-2·9-3):
- 비밀키 경로에 'OneDrive' 가 들어 있거나 저장소 안이면 거부한다 (S-16).
- 같은 버전 번호로 내용이 다른 매니페스트에 다시 서명하지 않는다. <키 폴더>\\signed.log 에 (version, sha256) 을 남기고 겹치면 거부.
- 암호는 그때 입력하고 저장하지 않는다. 테스트·가짜 서버는 암호 없는 키(minisign -W 와 같은 형식)를 메모리에서 만든다.
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import base64
import getpass
import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import minisign  # noqa: E402
from app.appspec import SPEC, UPDATE_PUBKEYS  # noqa: E402

KEY_ALG = b"Ed"
KDF_SCRYPT = b"Sc"
KDF_NONE = b"\x00\x00"
CKSUM_BLAKE2 = b"B2"
# minisign 과 같은 값 (libsodium crypto_pwhash_scryptsalsa208sha256 의 SENSITIVE)
SCRYPT_OPSLIMIT = 33554432
SCRYPT_MEMLIMIT = 1073741824
DEFAULT_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ChatCoach-signing")


@dataclass(frozen=True)
class KeyPair:
    key_id: bytes      # 8
    seed: bytes        # 32 (Ed25519 비밀 시드)
    public: bytes      # 32

    @property
    def key_id_hex(self) -> str:
        return minisign.key_id_hex(self.key_id)

    def public_line(self) -> str:
        return base64.b64encode(KEY_ALG + self.key_id + self.public).decode("ascii")

    def public_file_text(self) -> str:
        return f"untrusted comment: minisign public key {self.key_id_hex}\n{self.public_line()}\n"


# --------------------------------------------------------------------------- 키
def _nacl():
    try:
        import nacl.signing  # noqa: F401
        import nacl.pwhash   # noqa: F401
        import nacl.utils    # noqa: F401
        import nacl
        return nacl
    except ImportError as ex:
        raise SystemExit("PyNaCl 이 필요합니다: pip install --require-hashes -r requirements.lock") from ex


def generate_keypair() -> KeyPair:
    nacl = _nacl()
    sk = nacl.signing.SigningKey.generate()
    return KeyPair(nacl.utils.random(8), bytes(sk), bytes(sk.verify_key))


def _checksum(key_id: bytes, secret64: bytes) -> bytes:
    return hashlib.blake2b(KEY_ALG + key_id + secret64, digest_size=32).digest()


def _scrypt_stream(password: bytes, salt: bytes, opslimit: int, memlimit: int) -> bytes:
    nacl = _nacl()
    return nacl.pwhash.scrypt.kdf(104, password, salt, opslimit=opslimit, memlimit=memlimit)


def secret_file_text(kp: KeyPair, password: str | None) -> str:
    """minisign 비밀키 파일 내용. password 가 None 이면 암호 없는 키(kdf 없음, minisign -W)."""
    secret64 = kp.seed + kp.public
    keynum_sk = kp.key_id + secret64 + _checksum(kp.key_id, secret64)
    if password is None:
        kdf_alg, salt, ops, mem = KDF_NONE, bytes(32), 0, 0
    else:
        nacl = _nacl()
        kdf_alg, salt, ops, mem = KDF_SCRYPT, nacl.utils.random(32), SCRYPT_OPSLIMIT, SCRYPT_MEMLIMIT
        stream = _scrypt_stream(password.encode("utf-8"), salt, ops, mem)
        keynum_sk = bytes(a ^ b for a, b in zip(keynum_sk, stream))
    raw = KEY_ALG + kdf_alg + CKSUM_BLAKE2 + salt + ops.to_bytes(8, "little") + mem.to_bytes(8, "little") + keynum_sk
    comment = "minisign encrypted secret key" if password is not None else "minisign unencrypted secret key"
    return f"untrusted comment: {comment}\n{base64.b64encode(raw).decode('ascii')}\n"


def load_secret_key(text: str, password: str | None) -> KeyPair:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) != 2 or not lines[0].startswith("untrusted comment:"):
        raise ValueError("비밀키 파일 형식이 아닙니다")
    raw = base64.b64decode(lines[1], validate=True)
    if len(raw) != 2 + 2 + 2 + 32 + 8 + 8 + 104 or raw[:2] != KEY_ALG or raw[4:6] != CKSUM_BLAKE2:
        raise ValueError("비밀키 파일 형식이 아닙니다 (길이·알고리즘)")
    kdf_alg = raw[2:4]
    salt = raw[6:38]
    ops = int.from_bytes(raw[38:46], "little")
    mem = int.from_bytes(raw[46:54], "little")
    keynum_sk = raw[54:158]
    if kdf_alg == KDF_SCRYPT:
        if password is None:
            raise ValueError("암호가 필요한 키입니다")
        stream = _scrypt_stream(password.encode("utf-8"), salt, ops, mem)
        keynum_sk = bytes(a ^ b for a, b in zip(keynum_sk, stream))
    elif kdf_alg != KDF_NONE:
        raise ValueError(f"알 수 없는 KDF {kdf_alg!r}")
    key_id, secret64, cksum = keynum_sk[:8], keynum_sk[8:72], keynum_sk[72:104]
    if _checksum(key_id, secret64) != cksum:
        raise ValueError("암호가 틀렸거나 비밀키 파일이 손상되었습니다")
    return KeyPair(key_id, secret64[:32], secret64[32:])


# --------------------------------------------------------------------------- 서명
def sign_bytes(data: bytes, kp: KeyPair, trusted_comment: str,
               untrusted_comment: str = "signature from minisign secret key") -> str:
    """분리 서명(.minisig) 텍스트. 'ED' prehash 모드. 전역 서명으로 trusted comment 를 보호한다."""
    nacl = _nacl()
    if "\n" in trusted_comment or "\r" in trusted_comment:
        raise ValueError("trusted comment 에 줄바꿈이 있습니다")
    sk = nacl.signing.SigningKey(kp.seed)
    digest = hashlib.blake2b(data, digest_size=64).digest()
    sig = sk.sign(digest).signature
    gsig = sk.sign(sig + trusted_comment.encode("utf-8")).signature
    body = base64.b64encode(minisign.SIG_ALG_PREHASH + kp.key_id + sig).decode("ascii")
    return (f"untrusted comment: {untrusted_comment}\n{body}\n"
            f"trusted comment: {trusted_comment}\n{base64.b64encode(gsig).decode('ascii')}\n")


def manifest_version(data: bytes) -> str:
    """서명 대상 latest.json 에서 version 만 읽는다 (서명 전에 trusted comment 를 만들기 위해)."""
    obj = json.loads(data.decode("utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("version"), str):
        raise ValueError("latest.json 에 version 문자열이 없습니다")
    SPEC._check_version(obj["version"])
    return obj["version"]


# --------------------------------------------------------------------------- 경로 규칙
def key_path_problem(path: str) -> str | None:
    """비밀키를 두면 안 되는 곳이면 사유를 돌려준다 (04 9-2, S-16)."""
    full = os.path.normcase(os.path.abspath(path))
    if "onedrive" in full:
        return "경로에 OneDrive 가 들어 있습니다. 클라우드에 올라가는 곳에는 비밀키를 두지 않습니다"
    root = os.path.normcase(os.path.abspath(ROOT)) + os.sep
    if full.startswith(root) or full == root.rstrip(os.sep):
        return "저장소 안입니다. .gitignore 로 빠져도 저장소 폴더가 OneDrive 아래라 클라우드에 올라갑니다"
    return None


def _read_pubkeys(arg: str | None) -> list[tuple[str, str]]:
    if arg:
        if os.path.isfile(arg):
            with open(arg, encoding="utf-8") as f:
                return [("지정", f.read())]
        return [("지정", arg)]
    if not UPDATE_PUBKEYS:
        raise SystemExit("app/appspec.py 에 공개키가 없습니다. --pubkey 로 지정하거나 keygen 뒤 appspec 에 넣으세요")
    return [(k.label, k.line) for k in UPDATE_PUBKEYS]


# --------------------------------------------------------------------------- 명령
def read_password_file(path: str) -> str:
    """암호 파일(첫 줄, UTF-8, BOM·끝 공백 제거). 저장소·OneDrive 안이면 거부. 내용은 절대 출력하지 않는다."""
    problem = key_path_problem(path)
    if problem:
        raise SystemExit(f"거부: 암호 파일 위치 — {problem}: {path}")
    with open(path, encoding="utf-8-sig") as f:
        pw = f.readline().rstrip("\r\n")
    if not pw:
        raise SystemExit(f"암호 파일이 비어 있습니다: {path}")
    return pw


def cmd_keygen(a) -> int:
    problem = key_path_problem(a.dir)
    if problem:
        print(f"거부: {problem}: {a.dir}")
        return 2
    os.makedirs(a.dir, exist_ok=True)
    sk_path = os.path.join(a.dir, f"chatcoach-{a.label}.key")
    pk_path = os.path.join(a.dir, f"chatcoach-{a.label}.pub")
    if os.path.exists(sk_path) or os.path.exists(pk_path):
        print(f"이미 있습니다: {sk_path} / {pk_path}. 덮어쓰지 않습니다")
        return 2
    if a.no_password:
        pw = None
    elif a.password_file:
        pw = read_password_file(a.password_file)
    else:
        pw = getpass.getpass(f"{a.label} 비밀키 암호: ")
        if not pw:
            print("빈 암호는 쓰지 않습니다")
            return 2
        if pw != getpass.getpass("다시 입력: "):
            print("두 입력이 다릅니다")
            return 2
    kp = generate_keypair()
    print("키 파일 암호화 중 (scrypt, 메모리 1GB, 몇 초 걸립니다)…" if pw else "암호 없는 키를 만듭니다 (시험용)")
    with open(sk_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(secret_file_text(kp, pw))
    with open(pk_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(kp.public_file_text())
    # 바로 되읽어 서명·검증 왕복을 확인한다
    with open(sk_path, encoding="utf-8") as f:
        back = load_secret_key(f.read(), pw)
    probe = b"chatcoach keygen probe"
    sig = sign_bytes(probe, back, "probe")
    minisign.verify(probe, sig, [(a.label, kp.public_line())])
    print(f"완료: {a.label}  키 ID {kp.key_id_hex}")
    print(f"  비밀키: {sk_path}  (백업 USB 두 곳에 복사. K2 는 이 PC 에서 지운다 — 04 9-2)")
    print(f"  공개키: {pk_path}")
    print("app/appspec.py UPDATE_PUBKEYS 에 넣을 줄:")
    print(f'    PublicKey("{a.label}", "{kp.public_line()}"),')
    return 0


def _signed_log_path(key_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(key_path)), "signed.log")


def _check_signed_log(log_path: str, version: str, digest: str) -> str | None:
    if not os.path.isfile(log_path):
        return None
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == version and parts[1] != digest:
                return f"버전 {version} 은 이미 다른 내용({parts[1][:12]}…)으로 서명했습니다. 다음 번호로 내세요 (04 9-3)"
    return None


def cmd_sign(a) -> int:
    key_path = a.key or os.path.join(DEFAULT_DIR, "chatcoach-K1.key")
    problem = key_path_problem(key_path)
    if problem:
        print(f"거부: {problem}: {key_path}")
        return 2
    with open(a.manifest, "rb") as f:
        data = f.read()
    version = manifest_version(data)
    digest = hashlib.sha256(data).hexdigest()
    log_path = _signed_log_path(key_path)
    dup = _check_signed_log(log_path, version, digest)
    if dup:
        print(f"거부: {dup}")
        return 2
    with open(key_path, encoding="utf-8") as f:
        text = f.read()
    if "unencrypted" in text.splitlines()[0]:
        pw = None
    elif a.password_file:
        pw = read_password_file(a.password_file)
    else:
        pw = getpass.getpass("비밀키 암호: ")
    kp = load_secret_key(text, pw)
    comment = SPEC.trusted_comment(version)
    sig = sign_bytes(data, kp, comment)
    out = a.out or (a.manifest + ".minisig")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(sig)
    # 내장 공개키(또는 --pubkey)로 즉시 검증 — 앱과 같은 코드
    keys = _read_pubkeys(a.pubkey) if (a.pubkey or UPDATE_PUBKEYS) else [("방금 만든 키", kp.public_line())]
    res = minisign.verify(data, sig, keys)
    with open(log_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{version} {digest} {res.key_id_hex}\n")
    print(f"서명 완료: {out}  (키 {res.label} {res.key_id_hex}, trusted comment '{comment}')")
    return 0


def cmd_verify(a) -> int:
    if a.url:
        req = urllib.request.Request(a.url, headers={"User-Agent": SPEC.user_agent("tools")})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(SPEC.max_manifest_bytes + 1)
        req = urllib.request.Request(a.url + ".minisig", headers={"User-Agent": SPEC.user_agent("tools")})
        with urllib.request.urlopen(req, timeout=20) as r:
            sig = r.read(SPEC.max_sig_bytes + 1)
    else:
        with open(a.manifest, "rb") as f:
            data = f.read()
        with open(a.sig or (a.manifest + ".minisig"), "rb") as f:
            sig = f.read()
    try:
        res = minisign.verify(data, sig, _read_pubkeys(a.pubkey))
    except minisign.MinisignError as ex:
        print(f"검증 실패: {ex}")
        return 1
    version = manifest_version(data)
    expected = SPEC.trusted_comment(version)
    if res.trusted_comment != expected:
        print(f"검증 실패: trusted comment '{res.trusted_comment}' 가 '{expected}' 와 다릅니다")
        return 1
    print(f"검증 통과: 키 {res.label} {res.key_id_hex}, 버전 {version}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="키쌍 생성")
    k.add_argument("--dir", default=DEFAULT_DIR)
    k.add_argument("--label", default="K1")
    k.add_argument("--no-password", action="store_true", help="시험용. 실제 키에는 쓰지 않는다")
    k.add_argument("--password-file", help="암호를 이 파일의 첫 줄에서 읽는다 (터미널 입력이 안 될 때. 저장소·OneDrive 밖이어야 함)")
    k.set_defaults(fn=cmd_keygen)
    s = sub.add_parser("sign", help="latest.json 서명")
    s.add_argument("manifest")
    s.add_argument("--key")
    s.add_argument("--out")
    s.add_argument("--pubkey", help="검증에 쓸 공개키 (기본: appspec 내장 키)")
    s.add_argument("--password-file", help="암호를 이 파일의 첫 줄에서 읽는다")
    s.set_defaults(fn=cmd_sign)
    v = sub.add_parser("verify", help="서명 검증")
    v.add_argument("manifest", nargs="?")
    v.add_argument("--sig")
    v.add_argument("--pubkey")
    v.add_argument("--url")
    v.set_defaults(fn=cmd_verify)
    a = p.parse_args(argv)
    if a.cmd == "verify" and not a.manifest and not a.url:
        p.error("manifest 또는 --url 이 필요합니다")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
