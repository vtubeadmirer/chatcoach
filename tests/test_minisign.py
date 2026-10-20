"""app/minisign.py (순수 Python Ed25519 검증기 + minisign 형식) 과 tools/sign_manifest.py (PyNaCl 서명) 검증.

- RFC 8032 7.1 시험 벡터 3개 통과, 1비트 훼손·메시지 변경은 실패.
- libsodium(PyNaCl)이 만든 서명 50건과 교차 검증, 가변 서명(S ≥ L) 거부, 길이가 틀린 입력은 False.
- minisign 형식: sign_bytes → parse_signature 74바이트 분해 → verify 통과,
  trusted comment 변조('전역 서명') · 다른 키('모르는 서명 키') · 내용 변조('서명이 맞지 않습니다') 를 구분해 거부,
  줄 수·접두어·base64·BOM·2KB 상한 위반은 MinisignError. 공개키는 파일 전체·줄 하나 모두 받고 42바이트만.
- 비밀키 파일: 암호 없는 키 왕복, 암호 있는 키(scrypt) 왕복과 틀린 암호 거부, 키 경로 규칙(OneDrive·저장소 안 거부).
"""
from __future__ import annotations

import base64
import hashlib
import os
import random

import pytest

from release_kit import TOOLS  # noqa: F401  (tools 를 sys.path 에 넣는다)
import sign_manifest as sm

from app import minisign
from app.minisign import MinisignError

import nacl.exceptions
import nacl.signing

L = 2 ** 252 + 27742317777372353535851937790883648493

# RFC 8032 7.1 TEST 1·2·3 (공개키, 메시지, 서명)
RFC8032 = [
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


def _flip(b: bytes, bit: int) -> bytes:
    """bit 번째 비트를 뒤집은 사본."""
    out = bytearray(b)
    out[bit // 8] ^= 1 << (bit % 8)
    return bytes(out)


# --------------------------------------------------------------------------- Ed25519 (RFC 8032)
@pytest.mark.parametrize("pk_hex,msg_hex,sig_hex", RFC8032, ids=["test1", "test2", "test3"])
def test_rfc8032_vectors_pass(pk_hex, msg_hex, sig_hex):
    pk, msg, sig = bytes.fromhex(pk_hex), bytes.fromhex(msg_hex), bytes.fromhex(sig_hex)
    assert minisign.ed25519_verify(pk, msg, sig)


@pytest.mark.parametrize("pk_hex,msg_hex,sig_hex", RFC8032, ids=["test1", "test2", "test3"])
def test_rfc8032_vectors_tampered_fail(pk_hex, msg_hex, sig_hex):
    pk, msg, sig = bytes.fromhex(pk_hex), bytes.fromhex(msg_hex), bytes.fromhex(sig_hex)
    # 서명의 R 쪽 · S 쪽 · 공개키 1비트 훼손
    for bit in (0, 100, 255, 256, 300, 500):
        assert not minisign.ed25519_verify(pk, msg, _flip(sig, bit)), bit
    assert not minisign.ed25519_verify(_flip(pk, 3), msg, sig)
    # 메시지 변경 (덧붙임 · 1비트)
    assert not minisign.ed25519_verify(pk, msg + b"\x00", sig)
    if msg:
        assert not minisign.ed25519_verify(pk, _flip(msg, 0), sig)


# --------------------------------------------------------------------------- libsodium 교차 검증
def test_cross_check_with_libsodium_50_signatures():
    rng = random.Random(20261020)
    for i in range(50):
        sk = nacl.signing.SigningKey(bytes(rng.getrandbits(8) for _ in range(32)))
        pk = bytes(sk.verify_key)
        msg = bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 300)))
        sig = sk.sign(msg).signature
        assert minisign.ed25519_verify(pk, msg, sig), i
        # 훼손: 서명 1비트, 메시지 1비트(또는 덧붙임) → 실패
        assert not minisign.ed25519_verify(pk, msg, _flip(sig, rng.randrange(512))), i
        bad_msg = _flip(msg, rng.randrange(len(msg) * 8)) if msg else b"x"
        assert not minisign.ed25519_verify(pk, bad_msg, sig), i


def test_malleable_signature_s_plus_l_rejected():
    """S 에 L 을 더한 서명은 수학적으로 같은 점을 주지만 libsodium 처럼 거부해야 한다 (S < L)."""
    sk = nacl.signing.SigningKey(b"\x07" * 32)
    pk = bytes(sk.verify_key)
    msg = b"chatcoach latest.json"
    sig = sk.sign(msg).signature
    s = int.from_bytes(sig[32:], "little")
    forged = sig[:32] + (s + L).to_bytes(32, "little")
    assert minisign.ed25519_verify(pk, msg, sig)
    assert not minisign.ed25519_verify(pk, msg, forged)
    # libsodium 도 거부한다 (두 검증기의 규칙이 같다)
    with pytest.raises(nacl.exceptions.BadSignatureError):
        sk.verify_key.verify(msg, forged)


def test_wrong_lengths_are_false():
    pk, msg, sig = (bytes.fromhex(x) for x in RFC8032[1])
    assert not minisign.ed25519_verify(pk[:31], msg, sig)
    assert not minisign.ed25519_verify(pk + b"\x00", msg, sig)
    assert not minisign.ed25519_verify(pk, msg, sig[:63])
    assert not minisign.ed25519_verify(pk, msg, sig + b"\x00")
    assert not minisign.ed25519_verify(b"", msg, b"")


def test_non_canonical_and_small_order_points_are_false():
    pk, msg, sig = (bytes.fromhex(x) for x in RFC8032[0])
    # 항등원(작은 위수) 공개키, y >= p 인 비정규 인코딩
    identity = (1).to_bytes(32, "little")
    assert not minisign.ed25519_verify(identity, msg, sig)
    non_canonical = (2 ** 255 - 19 + 1).to_bytes(32, "little")
    assert not minisign.ed25519_verify(non_canonical, msg, sig)
    assert not minisign.ed25519_verify(pk, msg, identity + sig[32:])


# --------------------------------------------------------------------------- minisign 형식
@pytest.fixture(scope="module")
def kp():
    return sm.generate_keypair()


def _sig_lines(text: str) -> list[str]:
    return text.split("\n")


def test_sign_bytes_parse_signature_74_bytes(kp):
    data = b'{"version": "0.0.2"}\n'
    text = sm.sign_bytes(data, kp, "ChatCoach latest.json v0.0.2")
    lines = _sig_lines(text)
    assert lines[0].startswith("untrusted comment: ") and lines[2].startswith("trusted comment: ")
    raw = base64.b64decode(lines[1])
    assert len(raw) == 74 and raw[:2] == b"ED" and raw[2:10] == kp.key_id
    sig = minisign.parse_signature(text)
    assert sig.algorithm == minisign.SIG_ALG_PREHASH == b"ED"
    assert sig.key_id == kp.key_id and sig.signature == raw[10:74] and len(sig.signature) == 64
    assert len(sig.global_signature) == 64
    assert sig.trusted_comment == "ChatCoach latest.json v0.0.2"
    assert sig.untrusted_comment == "signature from minisign secret key"
    assert sig.key_id_hex == kp.key_id_hex == minisign.key_id_hex(kp.key_id)
    # 'ED' = BLAKE2b-512(파일) 에 서명
    digest = hashlib.blake2b(data, digest_size=64).digest()
    assert minisign.ed25519_verify(kp.public, digest, sig.signature)
    # bytes 로 줘도 같다
    assert minisign.parse_signature(text.encode("utf-8")) == sig


def test_verify_passes_and_reports_label(kp):
    data = b"payload \xea\xb0\x80"
    text = sm.sign_bytes(data, kp, "tc")
    res = minisign.verify(data, text, [("K2", "untrusted comment: x\n" + sm.generate_keypair().public_line()),
                                       ("K1", kp.public_line())])
    assert res.label == "K1" and res.key_id_hex == kp.key_id_hex
    assert res.trusted_comment == "tc" and res.algorithm == b"ED"
    # 공개키 파일 전체(주석 줄 포함)도 받는다
    assert minisign.verify(data, text, [("F", kp.public_file_text())]).label == "F"


def test_verify_legacy_ed_algorithm(kp):
    """'Ed'(prehash 없음) 서명도 검증한다 — 옛 minisign 형식."""
    data = b"legacy payload"
    sk = nacl.signing.SigningKey(kp.seed)
    sig = sk.sign(data).signature
    gsig = sk.sign(sig + b"legacy").signature
    text = (f"untrusted comment: legacy\n{base64.b64encode(b'Ed' + kp.key_id + sig).decode()}\n"
            f"trusted comment: legacy\n{base64.b64encode(gsig).decode()}\n")
    res = minisign.verify(data, text, [("K1", kp.public_line())])
    assert res.algorithm == b"Ed"


def test_trusted_comment_tamper_is_global_signature_error(kp):
    data = b"manifest"
    lines = _sig_lines(sm.sign_bytes(data, kp, "ChatCoach latest.json v0.0.2"))
    lines[2] = "trusted comment: ChatCoach latest.json v9.9.9"
    with pytest.raises(MinisignError, match="전역 서명"):
        minisign.verify(data, "\n".join(lines), [("K1", kp.public_line())])


def test_other_key_is_unknown_key(kp):
    data = b"manifest"
    text = sm.sign_bytes(data, kp, "tc")
    other = sm.generate_keypair()
    with pytest.raises(MinisignError, match="모르는 서명 키"):
        minisign.verify(data, text, [("K1", other.public_line())])


def test_content_tamper_is_signature_mismatch(kp):
    data = b"manifest"
    text = sm.sign_bytes(data, kp, "tc")
    with pytest.raises(MinisignError, match="서명이 맞지 않습니다"):
        minisign.verify(data + b" ", text, [("K1", kp.public_line())])
    # 키 ID 는 같지만 공개키가 다른 경우도 '서명 불일치' 다 (모르는 키가 아니다)
    impostor = base64.b64encode(b"Ed" + kp.key_id + sm.generate_keypair().public).decode()
    with pytest.raises(MinisignError, match="서명이 맞지 않습니다"):
        minisign.verify(data, text, [("K1", impostor)])


def test_signature_bytes_tamper_is_rejected(kp):
    data = b"manifest"
    lines = _sig_lines(sm.sign_bytes(data, kp, "tc"))
    raw = bytearray(base64.b64decode(lines[1]))
    raw[20] ^= 0x01                      # 서명 본문(10..74) 1비트
    lines[1] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(MinisignError, match="서명이 맞지 않습니다"):
        minisign.verify(data, "\n".join(lines), [("K1", kp.public_line())])


def _good_sig_text(kp) -> str:
    return sm.sign_bytes(b"x", kp, "tc")


def test_parse_signature_rejects_malformed(kp):
    good = _good_sig_text(kp)
    lines = _sig_lines(good)[:4]
    bad = {
        "3줄": "\n".join(lines[:3]),
        "5줄": "\n".join(lines + ["extra"]),
        "1줄 접두어": "\n".join(["comment: x"] + lines[1:]),
        "3줄 접두어": "\n".join(lines[:2] + ["trusted: x"] + lines[3:]),
        "서명 base64": "\n".join([lines[0], lines[1][:-4] + "!!!!", lines[2], lines[3]]),
        "전역 서명 base64": "\n".join(lines[:3] + ["%%%%"]),
        "서명 길이": "\n".join([lines[0], base64.b64encode(b"ED" + b"\x00" * 71).decode(), lines[2], lines[3]]),
        "알고리즘": "\n".join([lines[0], base64.b64encode(b"Xx" + base64.b64decode(lines[1])[2:]).decode(),
                           lines[2], lines[3]]),
        "전역 서명 길이": "\n".join(lines[:3] + [base64.b64encode(b"\x00" * 63).decode()]),
        "빈 내용": "",
    }
    for why, text in bad.items():
        with pytest.raises(MinisignError):
            minisign.parse_signature(text)
        with pytest.raises(MinisignError):
            minisign.parse_signature(text.encode("utf-8"))
    # BOM (bytes) 과 UTF-8 이 아닌 바이트
    with pytest.raises(MinisignError, match="BOM"):
        minisign.parse_signature(b"\xef\xbb\xbf" + good.encode("utf-8"))
    with pytest.raises(MinisignError):
        minisign.parse_signature(b"\xff\xfe" + good.encode("utf-8"))
    # 2KB 초과 (str · bytes)
    big = good.replace("untrusted comment: ", "untrusted comment: " + "x" * 2100, 1)
    with pytest.raises(MinisignError, match="너무 큽니다"):
        minisign.parse_signature(big)
    with pytest.raises(MinisignError, match="너무 큽니다"):
        minisign.parse_signature(big.encode("utf-8"))
    # CRLF 와 끝의 빈 줄은 허용
    assert minisign.parse_signature(good.replace("\n", "\r\n") + "\r\n").key_id == kp.key_id


def test_parse_public_key_file_and_line(kp):
    assert minisign.parse_public_key(kp.public_file_text()) == (kp.key_id, kp.public)
    assert minisign.parse_public_key(kp.public_line()) == (kp.key_id, kp.public)
    assert minisign.parse_public_key(kp.public_line().encode("ascii")) == (kp.key_id, kp.public)
    assert minisign.parse_public_key("\n" + kp.public_line() + "\r\n\n") == (kp.key_id, kp.public)


def test_parse_public_key_rejects_wrong_size_and_format(kp):
    raw = b"Ed" + kp.key_id + kp.public
    for bad in (raw[:41], raw + b"\x00", b"XX" + raw[2:]):
        with pytest.raises(MinisignError, match="공개키 형식"):
            minisign.parse_public_key(base64.b64encode(bad).decode())
    with pytest.raises(MinisignError):
        minisign.parse_public_key("")
    with pytest.raises(MinisignError):
        minisign.parse_public_key("not base64 !!!")
    with pytest.raises(MinisignError):    # 주석 없이 두 줄
        minisign.parse_public_key(kp.public_line() + "\n" + kp.public_line())
    with pytest.raises(MinisignError):    # 세 줄
        minisign.parse_public_key(kp.public_file_text() + kp.public_line())


def test_key_id_hex_little_endian_uppercase():
    assert minisign.key_id_hex(bytes([1, 0, 0, 0, 0, 0, 0, 0xAB])) == "AB00000000000001"
    with pytest.raises(MinisignError):
        minisign.key_id_hex(b"\x00" * 7)


# --------------------------------------------------------------------------- 비밀키 파일 (tools/sign_manifest.py)
def test_unencrypted_secret_key_roundtrip(kp):
    text = sm.secret_file_text(kp, None)
    assert text.startswith("untrusted comment: minisign unencrypted secret key\n")
    back = sm.load_secret_key(text, None)
    assert back == kp
    # 되읽은 키로 서명해도 원래 공개키로 검증된다
    minisign.verify(b"probe", sm.sign_bytes(b"probe", back, "probe"), [("K1", kp.public_line())])


def test_encrypted_secret_key_roundtrip_and_wrong_password(kp):
    """scrypt(메모리 1GB, 실제 minisign 값) — 한 번에 몇 초 걸리므로 이 테스트 하나에서만 돈다."""
    text = sm.secret_file_text(kp, "맞는 암호")
    assert text.startswith("untrusted comment: minisign encrypted secret key\n")
    assert sm.load_secret_key(text, "맞는 암호") == kp
    with pytest.raises(ValueError, match="암호가 틀렸거나"):
        sm.load_secret_key(text, "틀린 암호")
    with pytest.raises(ValueError, match="암호가 필요"):
        sm.load_secret_key(text, None)


def test_load_secret_key_rejects_malformed(kp):
    text = sm.secret_file_text(kp, None)
    lines = text.splitlines()
    with pytest.raises(ValueError):
        sm.load_secret_key(lines[1], None)                           # 주석 줄 없음
    raw = bytearray(base64.b64decode(lines[1]))
    raw[100] ^= 1                                                     # 키 본문 훼손 → 체크섬 불일치
    with pytest.raises(ValueError, match="손상"):
        sm.load_secret_key(lines[0] + "\n" + base64.b64encode(bytes(raw)).decode() + "\n", None)
    with pytest.raises(ValueError, match="길이"):
        sm.load_secret_key(lines[0] + "\n" + base64.b64encode(bytes(raw[:-1])).decode() + "\n", None)


def test_sign_bytes_rejects_newline_in_trusted_comment(kp):
    with pytest.raises(ValueError):
        sm.sign_bytes(b"x", kp, "a\nb")


def test_manifest_version_reads_version_only():
    assert sm.manifest_version(b'{"version": "1.2.3", "x": 1}') == "1.2.3"
    for bad in (b'{"version": "v1.2.3"}', b'{"x": 1}', b'[1]', b'{"version": 3}'):
        with pytest.raises(ValueError):
            sm.manifest_version(bad)


@pytest.mark.skipif(os.name != "nt", reason="Windows 경로 규칙 (normcase 가 대소문자를 접는다)")
def test_key_path_problem(tmp_path, monkeypatch):
    # OneDrive 가 경로에 있으면 거부 (대소문자 무시)
    assert "OneDrive" in sm.key_path_problem(r"C:\Users\x\OneDrive\keys\k1.key")
    assert sm.key_path_problem(r"D:\onedrive - 회사\k1.key") is not None
    # 저장소 안이면 거부. 이 저장소는 OneDrive 아래라 어느 사유든 거부여야 한다
    assert sm.key_path_problem(os.path.join(sm.ROOT, "keys", "k1.key")) is not None
    # OneDrive 밖 저장소를 흉내 내 '저장소 안' 사유만 따로 확인
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    monkeypatch.setattr(sm, "ROOT", str(fake_root))
    assert "저장소 안" in sm.key_path_problem(str(fake_root / "secrets" / "k1.key"))
    assert "저장소 안" in sm.key_path_problem(str(fake_root))
    assert sm.key_path_problem(str(tmp_path / "repo-other" / "k1.key")) is None   # 접두사만 같은 형제 폴더
    # %LOCALAPPDATA%\ChatCoach-signing 은 허용 (기본 위치 · conftest 가 바꾼 임시 LOCALAPPDATA 둘 다)
    assert sm.key_path_problem(os.path.join(sm.DEFAULT_DIR, "chatcoach-K1.key")) is None
    assert sm.key_path_problem(os.path.join(os.environ["LOCALAPPDATA"], "ChatCoach-signing",
                                            "chatcoach-K1.key")) is None
