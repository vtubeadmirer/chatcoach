"""tests/ 아래에 실제 식별자가 새어 들어오지 않았는지 검사한다 (S-21, 02 11-2, 05 10-6).

tests/ 의 모든 파일(__pycache__ 제외, .gz 는 풀어서)을 훑어 다음을 찾으면 그 파일의 테스트가 실패한다.
  1. 32자 이상 이어진 16진수. 허용: sha1('fixture-N')(앞 32자 이상 잘라 쓴 것 포함) · 한 글자 반복('a'*64 꼴)
     · 아래 ALLOWED_HEX(공개 시험 벡터·저장소 상수, 사유 필수)
  2. 0 이 아닌 video_no. video_no·videoNo·VNO 키/변수, chzzk 주소의 /video(s)/N, parse_video_no("N"),
     프로젝트 폴더 이름 pNNNNNN. 허용: 0 과 ALLOWED_VIDEO_NOS
  3. '시청자N' 형식 밖 닉네임. *nickname·nick·display_name 키/변수의 문자열 값. 가린 닉네임(nickname_masked)은 '시' + '*' 또는 '*' 만
     허용: '시청자N'(f-string·%d 자리표시 포함) · 빈 문자열 · ALLOWED_NICKNAMES

위반을 보고할 때 값 자체는 앞 8자만 보인다(실제 값이면 로그에 다시 남기지 않게).
이 파일 안의 위반 예시는 실행 중에 조립한다(이 파일도 검사 대상이다).
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
FIXTURES = os.path.join(TESTS, "fixtures")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"_tool_{name}", os.path.join(ROOT, "tools", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mf = _load_tool("make_fixture")

# --------------------------------------------------------------------------- 허용 목록 (값 → 사유)
# 새 값을 넣을 때는 실제 사람·방송과 무관하다는 사유를 적는다. 쓰이지 않게 된 값은 test_allowlists_not_stale 이 잡는다.
ALLOWED_HEX: dict[str, str] = {
    # tests/test_minisign.py — RFC 8032 7.1 TEST 1·2·3 (공개 문서의 Ed25519 시험 벡터)
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a": "RFC 8032 TEST 1 공개키",
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b":
        "RFC 8032 TEST 1 서명",
    "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c": "RFC 8032 TEST 2 공개키",
    "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00":
        "RFC 8032 TEST 2 서명",
    "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025": "RFC 8032 TEST 3 공개키",
    "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a":
        "RFC 8032 TEST 3 서명",
    "27742317777372353535851937790883648493": "Ed25519 군 위수 L = 2^252 + 이 10진 상수 (16진수 아님)",
    # tests/test_guide_config.py — assets/guide-config.v0.1.json values 의 SHA-256 (저장소 파일에서 계산되는 값)
    "e8462bfc86dd1ba04619e0eefff7682e72c899fa0f8b6fdf70142a0b5b4947b2": "guide-config v0.1 thresholds_sha256",
    # tests/test_report_selftest.py — 로그 가림 시험용 0~f 차례 문자열
    "0123456789abcdef0123456789ABCDEF01234567": "가림 시험용 차례 문자열",
    # tests/test_sampling.py · test_queue_view.py — 합성 표본 시드(바이트 00~ff 차례)와, 그 시드·합성 chat_id 로
    # 계산한 sha256-tau-v1 순서 키 골든값(06 2-1 식을 손으로 계산한 값과 같다). 실제 방송·사람과 무관하다.
    "00112233445566778899aabbccddeeff": "합성 표본 시드 (바이트 차례)",
    "1aef486198b0fe3e6c754e64b4a4037f219071d084e87216647b400e77c5a57a": "합성 시드·cid(0) 의 순서 키 골든값",
    "18479e845f54a37964a5ba7e135d05ee540f2d9dcb83aef492af54ff12c89d5e": "합성 시드·cid(1) 의 순서 키 골든값",
    "84a0bcf1c56f546d9ddd7280ac5ee558019af93fa05a115359f74b23cb874c57": "합성 시드·'c'+'a'*20+'_2' 의 순서 키 골든값",
    # tests/test_sampling.py — v1 재현 골든값(08 D-10 1): 합성 채팅·합성 시드로 고친 코드 전의 v1 코드가 낸 값
    "22d86ad32463ed9479faf3853d3b62c2768fa995ac7fce8732e22fb4b0181c87": "합성 v1 매니페스트 전체 sha256 골든값",
    "1f21a51b767f10b9cd53ce9fe9fd8e80d5fbd8206e80cd66f128ca21a8779746": "합성 v1 τ 골든값",
    "4a6e1c248a1e77916b4d74adb304b1d4aa151c720070a8191c5b0e0a5825167e": "합성 v1 표본 chat_id 해시 골든값",
    "0efb01788b76723602c59b7985ad023ee50805ced8af469fffc4a7279d80d7f3": "합성 v1 적격 chat_id 해시 골든값",
}
ALLOWED_VIDEO_NOS: dict[str, str] = {
    # tests/test_chzzk_parse.py test_parse_video_no — 주소 파싱 시험의 1~3자리 값 (실제 VOD 번호는 7~8자리)
    "1": "주소 파싱 시험값", "9": "주소 파싱 시험값", "77": "주소 파싱 시험값", "123": "주소 파싱 시험값",
}
ALLOWED_NICKNAMES: dict[str, str] = {
    "익명의 후원자": "치지직이 익명 후원에 붙이는 고정 표시 (사람 닉네임 아님)",
    # tests/test_paths.py 로그 흐름 시험 — 로그에 새는지 찾으려고 '시청자N' 과 다른 눈에 띄는 합성 값(NICK 상수 + 번호)을 쓴다
    "{NICK}{i}": "합성 닉네임 (NICK = '합성닉네임칠칠' + 번호, 로그 누출 검사용)",
    # tests/test_find_anchors.py — 호명·첫 글자 약한 일치 시험은 닉네임의 첫 글자가 서로 달라야 해서 '시청자N' 을 쓸 수 없다.
    # 흔한 낱말로 지은 합성 닉네임이다(실제 채팅에서 가져오지 않음).
    "합성닉": "합성 닉네임 (낱말 '합성')", "나비": "합성 닉네임 (흔한 낱말)", "다람쥐왕": "합성 닉네임 (흔한 낱말)",
    "라면": "합성 닉네임 (흔한 낱말, 발화 글과 겹치는 경우 시험)",
    # tests/test_chat_window.py — 닉네임이 바뀐 사람 시험. '가짜'·'바뀐' 이 붙은 합성 값
    "가짜닉A": "합성 닉네임 (가짜)", "가짜닉B": "합성 닉네임 (가짜)", "바뀐닉A": "합성 닉네임 (바뀐 뒤)",
    # tests/test_chzzk_kinds.py — 구독권 선물 받는 사람 칸의 자리값
    "받는이": "합성 자리값 (낱말 '받는 이')",
}

FIXTURE_HASHES = mf.fixture_hashes()
_FIXTURE_PREFIX32 = {h[:32]: h for h in FIXTURE_HASHES}

# --------------------------------------------------------------------------- 찾기 규칙
_Q = r"""\\?["']?"""          # 따옴표 없음 · " · ' · JSON 문자열 안의 \"
HEX_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])")
VNO_KEY_RE = re.compile(r"(?i)(?<![a-z0-9_])" + _Q + r"(video[_-]?no|vno)" + _Q + r"\]?\s*(?:==|=|:|,)\s*"
                        + _Q + r"(-?\d+)")
VNO_URL_RE = re.compile(r"(?i)/videos?/(\d+)")
VNO_PARSE_RE = re.compile(r"""parse_video_no\(\s*\\?["']\s*(\d+)""")
VNO_SLUG_RE = re.compile(r"(?<![A-Za-z0-9_])p(\d{6,12})(?![0-9])")
_VAL = r"""\s*(?:==|=|:)\s*[fFrRuU]?\\?(["'])(.*?)\\?\2"""
NICK_RE = re.compile(r"(?<![A-Za-z0-9_])" + _Q + r"([A-Za-z]*[Nn]ickname|nick|display_?[Nn]ame|displayName)"
                     + _Q + r"\]?" + _VAL)
MASKED_RE = re.compile(r"(?<![A-Za-z0-9_])" + _Q + r"(nickname_masked)" + _Q + r"\]?" + _VAL)
NICK_OK_RE = re.compile(r"\A시청자(\d+|\{[^{}]*\}|%d|%s)\Z")
MASKED_OK_RE = re.compile(r"\A(시\*{1,7}|\*+)\Z")    # first_char 가림 · hidden 가림


def _short(v: str) -> str:
    return v[:8] + ("…" if len(v) > 8 else "") + f"({len(v)}자)"


def hex_ok(run: str) -> bool:
    low = run.lower()
    if len(set(low)) == 1:
        return True
    if low in FIXTURE_HASHES or (len(low) <= 40 and _FIXTURE_PREFIX32.get(low[:32], "").startswith(low)):
        return True
    return run in ALLOWED_HEX or low in ALLOWED_HEX


def find_hex(text: str) -> list[str]:
    return [m.group(0) for m in HEX_RE.finditer(text) if not hex_ok(m.group(0))]


def find_video_nos(text: str) -> list[str]:
    vals = [m.group(2) for m in VNO_KEY_RE.finditer(text)]
    for rx in (VNO_URL_RE, VNO_PARSE_RE, VNO_SLUG_RE):
        vals += [m.group(1) for m in rx.finditer(text)]
    return [v for v in vals if int(v) != 0 and v not in ALLOWED_VIDEO_NOS]


def find_nicknames(text: str) -> list[str]:
    bad = [m.group(3) for m in NICK_RE.finditer(text)
           if m.group(3) != "" and not NICK_OK_RE.match(m.group(3)) and m.group(3) not in ALLOWED_NICKNAMES]
    bad += [m.group(3) for m in MASKED_RE.finditer(text) if m.group(3) != "" and not MASKED_OK_RE.match(m.group(3))]
    return bad


# --------------------------------------------------------------------------- 대상 파일
def _scan_files() -> list[str]:
    out = []
    for d, dirs, files in os.walk(TESTS):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        out += [os.path.relpath(os.path.join(d, f), TESTS).replace(os.sep, "/") for f in sorted(files)]
    return out


def _read_text(rel: str) -> str:
    with open(os.path.join(TESTS, rel), "rb") as f:
        data = f.read()
    if rel.endswith(".gz"):
        data = gzip.decompress(data)
    return data.decode("utf-8-sig", errors="replace")


FILES = _scan_files()


def _report(kind: str, rel: str, vals: list[str]) -> str:
    shown = ", ".join(sorted({_short(v) for v in vals})[:10])
    return f"{rel}: {kind} {len(vals)}건 — {shown}. 합성값(tools/make_fixture.py)으로 바꾸거나 사유와 함께 허용 목록에"


@pytest.mark.parametrize("rel", FILES)
def test_no_unknown_long_hex(rel):
    bad = find_hex(_read_text(rel))
    assert not bad, _report("32자 이상 16진수", rel, bad)


@pytest.mark.parametrize("rel", FILES)
def test_video_no_is_zero(rel):
    bad = find_video_nos(_read_text(rel))
    assert not bad, _report("0 아닌 video_no", rel, bad)


@pytest.mark.parametrize("rel", FILES)
def test_nicknames_are_synthetic(rel):
    bad = find_nicknames(_read_text(rel))
    assert not bad, _report("'시청자N' 밖 닉네임", rel, bad)


def test_scan_covers_fixtures():
    assert "test_fixture_clean.py" in FILES
    assert any(f.startswith("fixtures/") for f in FILES)
    assert not any("__pycache__" in f for f in FILES)


def test_allowlists_not_stale():
    """허용 목록 값이 다른 테스트 파일 어딘가에 아직 쓰이는지. 안 쓰이면 지운다 (목록이 쌓이지 않게)."""
    blob = "\n".join(_read_text(f) for f in FILES if f != "test_fixture_clean.py")
    stale = [v for v in ALLOWED_HEX if v not in blob]
    stale += [v for v in ALLOWED_NICKNAMES if v not in blob]
    stale += [v for v in ALLOWED_VIDEO_NOS if not re.search(rf"/videos?/{v}(?!\d)|[\"']{v}[\"']", blob)]
    assert not stale, f"쓰이지 않는 허용 값 {len(stale)}개: {[_short(v) for v in stale]}"


# --------------------------------------------------------------------------- 찾기 규칙 자체 (위반 예시는 조립)
NK = "nick" + "name"
VK = "video" + "_no"


def test_finders_catch_planted_values():
    real_like = "9f" * 16 + "0c"                                  # 34자, 한 글자 반복 아님, fixture 해시 아님
    assert find_hex(f'"userIdHash": "{real_like}"') == [real_like]
    assert find_hex("x" + "ab" * 15 + "y") == []                    # 30자는 대상 아님
    assert find_hex("a" * 64 + " " + mf.user_hash(7) + " " + mf.user_hash(7)[:32]) == []
    assert find_video_nos(f'"{VK}": "4" + "2"') == ["4"]
    assert find_video_nos(f'{{"{VK}":0}} {VK}="0" /videos/0/chats') == []
    assert find_video_nos('V' + 'NO = "' + "31" + '"') == ["31"]
    assert find_video_nos("chzzk.naver.com/vi" + "deo/" + "5550001") == ["5550001"]
    assert find_video_nos("parse_vi" + 'deo_no("' + "8080" + '")') == ["8080"]
    assert find_video_nos("projects/p" + "1234567") == ["1234567"]
    bad = "철" + "수"
    assert find_nicknames(f'{{"{NK}": "{bad}"}}') == [bad]
    assert find_nicknames(f'{{\\"{NK}\\": \\"{bad}\\"}}') == [bad]         # JSON 문자열 안의 JSON
    assert find_nicknames(NK[:4] + f'="{bad}"') == [bad]
    assert find_nicknames(f'receiver{NK.capitalize()}": "{bad}"') == [bad]
    assert find_nicknames(f'"{NK}": "시청자12", {NK}=f"시청자{{n}}", "{NK}": "시청자%d" % i, "{NK}": ""') == []
    assert find_nicknames(f'"{NK}_masked": "시**", "{NK}_masked": null') == []
    assert find_nicknames(f'"{NK}_masked": "철*"') == ["철*"]
    assert find_nicknames(f'{NK}_mask="hidden", "{NK}_mask": "first_char"') == []


# --------------------------------------------------------------------------- 생성기
def test_make_fixture_reproduces_golden_labels():
    """지난 묶음의 골든 9개가 이 도구로 바이트 단위까지 같게 다시 만들어진다 (픽스처 파일은 고치지 않는다)."""
    files = mf.label_fixtures()
    golden = sorted(f for f in os.listdir(FIXTURES) if f.startswith("labels_") and f.endswith(".jsonl"))
    assert sorted(files) == golden
    assert mf.compare(FIXTURES, files) == []


def test_make_fixture_check_cli(capsys):
    assert mf.main(["--check"]) == 0
    assert "9/9" in capsys.readouterr().out


def test_generated_files_are_clean_and_deterministic(tmp_path):
    a = mf.build()
    assert a == mf.build()
    mf.main(["--out", str(tmp_path)])
    for name, data in a.items():
        assert (tmp_path / name).read_bytes() == data
        text = data.decode("utf-8-sig")
        assert find_hex(text) == [] and find_video_nos(text) == [] and find_nicknames(text) == [], name


def test_generated_chzzk_page_parses_like_api():
    from app import chzzk_chat as cc

    body = mf.chzzk_page_bytes().decode("utf-8")
    chats, nxt = cc.parse_page_body(body)
    assert len(chats) == len(mf.chzzk_items()) and nxt == chats[-1]["playerMessageTime"]
    assert all(set(c) == {"chatChannelId", "content", "extras", "messageStatusType", "messageTime",
                          "messageTypeCode", "playerMessageTime", "profile", "userIdHash"} for c in chats)
    uids = {c["userIdHash"] for c in chats} - {"anonymous"}
    assert uids and uids <= FIXTURE_HASHES
    for c in chats:
        if c["profile"]:
            assert NICK_OK_RE.match(json.loads(c["profile"])["nickname"])
        ex = json.loads(c["extras"])
        if "payAmount" in ex:
            assert ex["payAmount"] == mf.DONATION_AMOUNT


def _strip_schema(node):
    if isinstance(node, dict):
        return {k: _strip_schema(v) for k, v in node.items()
                if k not in ("$schema", "$id", "title", "description", "examples", "default", "$comment", "format")}
    if isinstance(node, list):
        return [_strip_schema(v) for v in node]
    return node


def test_generated_messages_match_schema():
    from app.core.validate import check

    with open(os.path.join(ROOT, "schemas", "chat-message.schema.json"), encoding="utf-8") as f:
        spec = _strip_schema(json.load(f))
    recs = mf.messages_records()
    assert len(recs) == len(mf.chzzk_items())
    for r in recs:
        assert check(r, spec, spec) == [], r["chat_id"]
    kinds = {r["kind"] for r in recs}
    assert {"text", "donation", "subscription", "system"} <= kinds
    assert any(r["chat_id"].endswith("_2") for r in recs)          # 같은 원문 두 줄
    assert any(r["is_anonymous"] for r in recs)
    dumped = json.dumps(recs, ensure_ascii=False)
    assert "userIdHash" not in dumped and mf.user_hash(1) not in dumped and "시청자" not in dumped
