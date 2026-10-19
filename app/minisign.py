"""minisign 서명 검증 — 표준 라이브러리만 쓰는 Ed25519 (RFC 8032) 검증기.

업데이트 매니페스트(latest.json)의 신뢰 근거다 (docs/design/04-update-and-release.md 5장·9장).
외부 패키지를 쓰지 않는 이유: 검증기는 신뢰의 뿌리라 번들에 들어가는 코드가 작고 읽을 수 있어야 하고,
py-minisign 은 C 확장(cryptography)에 의존해 설계 문서의 '순수 Python' 전제가 맞지 않았다 (2026-10-20 확인).
서명·키 생성은 하지 않는다. 그것은 tools/sign_manifest.py 가 PyNaCl(libsodium)로 한다.
tests/test_update_manifest.py 가 RFC 8032 벡터와 libsodium 이 만든 서명으로 이 검증기를 대조한다.

minisign 형식 (https://jedisct1.github.io/minisign/):
  공개키 파일  줄1 'untrusted comment: …'  줄2 base64( 'Ed' | key_id(8) | pk(32) )
  서명 파일    줄1 'untrusted comment: …'  줄2 base64( alg(2) | key_id(8) | sig(64) )
               줄3 'trusted comment: …'   줄4 base64( global_sig(64) )
  alg 'Ed' = 파일 바이트에 직접 서명, 'ED' = BLAKE2b-512(파일) 에 서명 (minisign 0.8+ 기본).
  global_sig = Ed25519( sig(64) | trusted_comment 바이트 ).  trusted comment 는 이 서명으로 보호된다.
  key_id 표시는 8바이트를 리틀엔디언 정수로 읽은 대문자 16진수 16자리.

검증 규칙은 libsodium 의 crypto_sign_verify_detached 와 같게 둔다: S < L(정규 스칼라), 공개키·R 의 정규 인코딩,
작은 위수(small order) 점 거부, 여인자(cofactor) 없이 [S]B == R + [h]A 비교.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Iterable

__all__ = ["MinisignError", "Signature", "VerifyResult", "parse_public_key", "parse_signature",
           "verify", "ed25519_verify", "key_id_hex", "SIG_ALG_LEGACY", "SIG_ALG_PREHASH"]

SIG_ALG_LEGACY = b"Ed"
SIG_ALG_PREHASH = b"ED"
_KEY_ALG = b"Ed"
MAX_SIGNATURE_TEXT = 2 * 1024      # .minisig 파일 상한 (04 5-4)
MAX_TRUSTED_COMMENT = 1024


class MinisignError(ValueError):
    """형식이 틀렸거나 서명이 맞지 않는다. 메시지는 사람이 읽는 사유(한국어)."""


# --------------------------------------------------------------------------- Ed25519 (RFC 8032 5.1)
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)          # sqrt(-1)
_BY = (4 * pow(5, _P - 2, _P)) % _P
_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_B = (_BX, _BY, 1, (_BX * _BY) % _P)   # 확장 좌표 (X, Y, Z, T)
_IDENTITY = (0, 1, 1, 0)


def _add(p: tuple, q: tuple) -> tuple:
    """확장 좌표 덧셈 (RFC 8032 5.1.4, a = -1 완전 공식)."""
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _double(p: tuple) -> tuple:
    x1, y1, z1, _ = p
    a = x1 * x1 % _P
    b = y1 * y1 % _P
    c = 2 * z1 * z1 % _P
    h = a + b
    e = h - (x1 + y1) * (x1 + y1)
    g = a - b
    f = c + g
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(k: int, p: tuple) -> tuple:
    """[k]P — 왼쪽부터 더블-앤-애드. 공개 값만 다루므로 상수 시간일 필요가 없다."""
    r = _IDENTITY
    for bit in bin(k)[2:]:
        r = _double(r)
        if bit == "1":
            r = _add(r, p)
    return r


def _equal(p: tuple, q: tuple) -> bool:
    x1, y1, z1, _ = p
    x2, y2, z2, _ = q
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _is_identity(p: tuple) -> bool:
    return _equal(p, _IDENTITY)


def _decode_point(b: bytes) -> tuple:
    """32바이트 → 점. 정규 인코딩이 아니거나 곡선 위에 없으면 MinisignError (RFC 8032 5.1.3)."""
    if len(b) != 32:
        raise MinisignError("점 인코딩 길이가 32바이트가 아닙니다")
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        raise MinisignError("점 인코딩이 정규형이 아닙니다 (y >= p)")
    y2 = y * y % _P
    u = (y2 - 1) % _P
    v = (_D * y2 + 1) % _P
    x = u * pow(v, 3, _P) % _P * pow(u * pow(v, 7, _P) % _P, (_P - 5) // 8, _P) % _P
    vx2 = v * x * x % _P
    if vx2 == u:
        pass
    elif vx2 == (-u) % _P:
        x = x * _I % _P
    else:
        raise MinisignError("곡선 위의 점이 아닙니다")
    if x == 0 and sign == 1:
        raise MinisignError("점 인코딩이 정규형이 아닙니다 (x = 0, 부호 1)")
    if (x & 1) != sign:
        x = _P - x
    return x, y, 1, x * y % _P


def _has_small_order(p: tuple) -> bool:
    return _is_identity(_mul(8, p))


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Ed25519 분리 서명 검증. libsodium crypto_sign_verify_detached 와 같은 규칙. 형식 오류도 False."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:                       # 가변 서명(malleability) 거부
        return False
    try:
        a = _decode_point(public_key)
        r = _decode_point(signature[:32])
    except MinisignError:
        return False
    if _has_small_order(a) or _has_small_order(r):
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
    left = _mul(s, _B)
    right = _add(r, _mul(h, a))
    return _equal(left, right)


# --------------------------------------------------------------------------- minisign 형식
def key_id_hex(key_id: bytes) -> str:
    if len(key_id) != 8:
        raise MinisignError("키 ID 길이가 8바이트가 아닙니다")
    return f"{int.from_bytes(key_id, 'little'):016X}"


def _b64(line: str, what: str) -> bytes:
    try:
        return base64.b64decode(line.strip(), validate=True)
    except (ValueError, TypeError) as ex:
        raise MinisignError(f"{what} 의 base64 가 깨졌습니다") from ex


def _lines(text: str | bytes, what: str, max_bytes: int) -> list[str]:
    if isinstance(text, bytes):
        if len(text) > max_bytes:
            raise MinisignError(f"{what} 가 너무 큽니다 ({len(text)} > {max_bytes} 바이트)")
        if text.startswith(b"\xef\xbb\xbf"):
            raise MinisignError(f"{what} 에 BOM 이 있습니다")
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as ex:
            raise MinisignError(f"{what} 가 UTF-8 이 아닙니다") from ex
    elif len(text.encode("utf-8")) > max_bytes:
        raise MinisignError(f"{what} 가 너무 큽니다")
    return [ln.rstrip("\r") for ln in text.split("\n")]


def parse_public_key(text: str | bytes) -> tuple[bytes, bytes]:
    """공개키 파일 전체 또는 base64 줄 하나 → (key_id 8바이트, 공개키 32바이트)."""
    lines = [ln for ln in _lines(text, "공개키", 1024) if ln.strip()]
    if not lines:
        raise MinisignError("공개키가 비어 있습니다")
    line = lines[-1] if lines[0].startswith("untrusted comment:") else lines[0]
    if len(lines) > 2 or (len(lines) == 2 and not lines[0].startswith("untrusted comment:")):
        raise MinisignError("공개키 형식이 아닙니다")
    raw = _b64(line, "공개키")
    if len(raw) != 42 or raw[:2] != _KEY_ALG:
        raise MinisignError("공개키 형식이 아닙니다 (Ed + 키 ID 8 + 키 32 바이트여야 함)")
    return raw[2:10], raw[10:42]


@dataclass(frozen=True)
class Signature:
    algorithm: bytes          # b"Ed" | b"ED"
    key_id: bytes             # 8
    signature: bytes          # 64
    trusted_comment: str
    global_signature: bytes   # 64
    untrusted_comment: str

    @property
    def key_id_hex(self) -> str:
        return key_id_hex(self.key_id)


def parse_signature(text: str | bytes) -> Signature:
    """.minisig 파일 내용 → Signature. 줄 수·접두어·길이를 모두 검사한다."""
    lines = _lines(text, "서명 파일", MAX_SIGNATURE_TEXT)
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) != 4:
        raise MinisignError(f"서명 파일은 4줄이어야 합니다 (지금 {len(lines)}줄)")
    if not lines[0].startswith("untrusted comment: "):
        raise MinisignError("서명 파일 1줄이 'untrusted comment: ' 로 시작하지 않습니다")
    if not lines[2].startswith("trusted comment: "):
        raise MinisignError("서명 파일 3줄이 'trusted comment: ' 로 시작하지 않습니다")
    raw = _b64(lines[1], "서명")
    if len(raw) != 74:
        raise MinisignError(f"서명 길이가 74바이트가 아닙니다 ({len(raw)})")
    alg = raw[:2]
    if alg not in (SIG_ALG_LEGACY, SIG_ALG_PREHASH):
        raise MinisignError(f"알 수 없는 서명 알고리즘 {alg!r}")
    gsig = _b64(lines[3], "전역 서명")
    if len(gsig) != 64:
        raise MinisignError(f"전역 서명 길이가 64바이트가 아닙니다 ({len(gsig)})")
    trusted = lines[2][len("trusted comment: "):]
    if len(trusted.encode("utf-8")) > MAX_TRUSTED_COMMENT:
        raise MinisignError("trusted comment 가 너무 깁니다")
    return Signature(alg, raw[2:10], raw[10:74], trusted, gsig, lines[0][len("untrusted comment: "):])


@dataclass(frozen=True)
class VerifyResult:
    label: str            # 어느 내장 키로 맞았는가 ("K1" 등)
    key_id_hex: str
    trusted_comment: str
    algorithm: bytes


def verify(data: bytes, signature_text: str | bytes, public_keys: Iterable[tuple[str, str | bytes]]) -> VerifyResult:
    """data 에 대한 분리 서명을 검증한다. 맞으면 VerifyResult, 아니면 MinisignError.

    public_keys: (라벨, 공개키 줄 또는 파일 내용) 반복자. 서명의 key_id 와 같은 키만 시도한다.
    키 ID 가 어느 키와도 다르면 '모르는 키', 키 ID 는 맞는데 서명이 틀리면 '서명 불일치' 로 구분해 알린다.
    검증 순서: ① 형식 ② key_id 매칭 ③ 파일 서명 ④ 전역 서명(trusted comment 보호). 셋 중 하나라도 틀리면 거부.
    """
    sig = parse_signature(signature_text)
    matched: list[tuple[str, bytes]] = []
    for label, key_text in public_keys:
        kid, pk = parse_public_key(key_text)
        if kid == sig.key_id:
            matched.append((label, pk))
    if not matched:
        raise MinisignError(f"모르는 서명 키입니다 (키 ID {sig.key_id_hex})")
    message = hashlib.blake2b(data, digest_size=64).digest() if sig.algorithm == SIG_ALG_PREHASH else data
    for label, pk in matched:
        if not ed25519_verify(pk, message, sig.signature):
            continue
        if not ed25519_verify(pk, sig.signature + sig.trusted_comment.encode("utf-8"), sig.global_signature):
            raise MinisignError("trusted comment 의 전역 서명이 맞지 않습니다")
        return VerifyResult(label, sig.key_id_hex, sig.trusted_comment, sig.algorithm)
    raise MinisignError(f"서명이 맞지 않습니다 (키 ID {sig.key_id_hex})")
