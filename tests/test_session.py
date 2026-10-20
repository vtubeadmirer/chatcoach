"""app/session.py — 세션 ID·기계 ID·파일 이름 규칙 (03 13-1·13-2). 합성 값만 쓴다."""
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app import project, session

KST = timezone(timedelta(hours=9))
MID = "3f2a9c01-1111-4222-8333-444455556666"


def test_new_session_id_format():
    assert session.new_session_id("L01", "primary", date(2026, 10, 19), "k9q2") == "L01-P-20261019-k9q2"
    assert session.new_session_id("L01", "RS", "20261019", "ab12") == "L01-RS-20261019-ab12"
    assert session.new_session_id("REV2", "relabel_second", datetime(2026, 1, 2, tzinfo=KST), "zz00") \
        == "REV2-R2-20260102-zz00"
    for kind, code in session.KIND_CODES.items():
        assert session.new_session_id("L01", kind, "20261019", "aaaa").split("-")[1] == code


@pytest.mark.parametrize("args", [
    ("l01", "primary", "20261019", "k9q2"),       # 라벨러 소문자
    ("L01", "nope", "20261019", "k9q2"),          # 모르는 종류
    ("L01", "primary", "2026-10-19", "k9q2"),     # 날짜 형식
    ("L01", "primary", "20261019", "K9Q2"),       # 대문자 무작위
    ("L01", "primary", "20261019", "k9q"),        # 3자
    ("L01", "primary", "20261019", "..\\x"),      # 경로 조작
])
def test_new_session_id_rejects(args):
    with pytest.raises(ValueError):
        session.new_session_id(*args)


def test_random4_alphabet_and_injection():
    r = session.random4()
    assert re.fullmatch(r"[a-z0-9]{4}", r)
    assert session.random4(lambda n: 0) == "aaaa"
    assert session.random4(lambda n: n - 1) == "9999"


def test_machine_id_creates_and_reuses(tmp_path):
    d = tmp_path / "데이터 폴더"
    a = session.machine_id(str(d), uuid_factory=lambda: MID)
    assert a == MID
    doc = json.loads((d / "machine.json").read_text(encoding="utf-8"))
    assert doc["machine_id"] == MID
    # 다시 부르면 만들지 않고 읽는다
    assert session.machine_id(str(d), uuid_factory=lambda: "never-called") == MID
    assert session.machine4(MID) == project.machine4_of(MID)
    assert re.fullmatch(r"[a-z2-7]{4}", session.machine4(MID))


def test_machine_id_corrupt_file_is_preserved(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "machine.json").write_bytes(b"{not json")
    mid = session.machine_id(str(d), uuid_factory=lambda: MID, now=datetime(2026, 10, 21, 7, 0, tzinfo=KST))
    assert mid == MID
    kept = [n for n in os.listdir(d) if n.startswith("machine.json.corrupt-")]
    assert kept and (d / kept[0]).read_bytes() == b"{not json"


def test_machine_id_default_uuid(tmp_path):
    mid = session.machine_id(str(tmp_path))
    assert session.UUID4_RE.match(mid)


def test_session_file_name_rule():
    assert session.session_file_name("L01-P-20261019-k9q2", "m3fa") == "L01-P-20261019-k9q2.m3fa.jsonl"
    assert session.parse_session_file_name("L01-P-20261019-k9q2.m3fa.jsonl") == ("L01-P-20261019-k9q2", "m3fa")
    for bad in ("L01-P-20261019-k9q2.m3fa (1).jsonl", "L01-P-20261019-k9q2.m3fa-DESKTOP.jsonl",
                "L01-P-20261019-k9q2.M3FA.jsonl", "L01-P-20261019-k9q2.m3fa.jsonl.partial-1", "x.jsonl", ""):
        assert session.parse_session_file_name(bad) is None
    with pytest.raises(ValueError):
        session.session_file_name("L01-P-20261019-k9q2", "m1fa")   # base32 에 1 없음
    with pytest.raises(ValueError):
        session.session_file_name("..\\L01", "m3fa")


def test_session_context_validation():
    m4 = session.machine4(MID)
    ctx = session.SessionContext("L01", "primary", "L01-P-20261019-k9q2", m4, machine_id=MID)
    assert ctx.kind_code == "P" and ctx.file_name == f"L01-P-20261019-k9q2.{m4}.jsonl"
    with pytest.raises(ValueError):
        session.SessionContext("L02", "primary", "L01-P-20261019-k9q2", m4)     # 라벨러 불일치
    with pytest.raises(ValueError):
        session.SessionContext("L01", "relabel_same", "L01-P-20261019-k9q2", m4)  # 종류 코드 불일치
    with pytest.raises(ValueError):
        session.SessionContext("L01", "primary", "L01-P-20261019-k9q2", "abcd" if m4 != "abcd" else "abce",
                               machine_id=MID)                                   # machine4 불일치
    with pytest.raises(ValueError):
        session.SessionContext("L01", "primary", "L01-P-20261019-k9q2", m4, machine_id="not-a-uuid")


def test_new_session_uses_data_root_and_clock(tmp_path):
    now = datetime(2026, 10, 21, 6, 30, 5, tzinfo=KST)
    ctx = session.new_session("L01", "primary", data_root=str(tmp_path / "d"), now=now, rand4="ab12")
    assert ctx.session_id == "L01-P-20261021-ab12"
    assert ctx.session_created_at == "2026-10-21T06:30:05+09:00"
    assert ctx.machine_id == json.loads((tmp_path / "d" / "machine.json").read_text(encoding="utf-8"))["machine_id"]
    assert ctx.machine4 == session.machine4(ctx.machine_id)


def test_new_session_default_data_root():
    # conftest 가 data_root 를 임시 폴더로 지정해 둔다
    from app import paths
    ctx = session.new_session("L01", "relabel_same", rand4="zz99")
    assert ctx.session_id.startswith("L01-RS-") and ctx.session_id.endswith("-zz99")
    assert os.path.exists(os.path.join(paths.data_root(), "machine.json"))
