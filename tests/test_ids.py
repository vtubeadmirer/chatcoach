"""app/core/ids.py — 설계 문서 검산값 (03 13-1·14-2) 과 ID 정규식."""
import pytest

from app.core import ids


def test_chat_id_check_value():
    cid = ids.chat_id_base(15632692, 1791689602000, "abc123", 1, "여행 가면 꼭 하는 거 있어요?")
    assert cid == "c126056b5d9f74056f3c0"
    # 문자열로 줘도 같다 (API 원값을 str 로 이어 붙인다)
    assert ids.chat_id_base("15632692", "1791689602000", "abc123", "1", "여행 가면 꼭 하는 거 있어요?") == cid


def test_viewer_id_check_value():
    assert ids.viewer_id(bytes(range(32)), "abc123") == "vw_zhm34oc3r6u4"


def test_viewer_id_anonymous_and_short_salt():
    for u in (None, "", " ", "anonymous"):
        assert ids.viewer_id(bytes(range(32)), u) == "anon"
    with pytest.raises(ValueError):
        ids.viewer_id(b"short", "abc123")


def test_assign_chat_ids_suffix():
    assert ids.assign_chat_ids(["ca", "cb", "ca", "ca"]) == ["ca", "cb", "ca_2", "ca_3"]


@pytest.mark.parametrize("v,ok", [("c126056b5d9f74056f3c0", True), ("c126056b5d9f74056f3c0_2", True),
                                  ("c126056b5d9f74056f3c", False), ("C126056b5d9f74056f3c0", False),
                                  ("c126056b5d9f74056f3c0_", False), ("c126056b5d9f74056f3c0\n", False)])
def test_chat_id_re(v, ok):
    assert bool(ids.CHAT_ID_RE.match(v)) is ok


@pytest.mark.parametrize("v,ok", [("vw_zhm34oc3r6u4", True), ("anon", True), ("vw_zhm34oc3r6u", False),
                                  ("vw_ZHM34OC3R6U4", False), ("vw_zhm34oc3r6u1", False), ("anon\n", False)])
def test_viewer_id_re(v, ok):
    assert bool(ids.VIEWER_ID_RE.match(v)) is ok


@pytest.mark.parametrize("v,ok", [("F20261019-a1b2", True), ("F2026101-a1b2", False), ("F20261019-A1B2", False),
                                  ("F20261019-a1b2\n", False), ("..\\x", False)])
def test_fetch_id_re(v, ok):
    assert bool(ids.FETCH_ID_RE.match(v)) is ok


def test_fetch_id_and_path_check():
    assert ids.fetch_id("20261020", "ab12") == "F20261020-ab12"
    with pytest.raises(ValueError):
        ids.fetch_id("2026-10-20", "ab12")
    with pytest.raises(ValueError):
        ids.check_path_id("..\\CON", ids.FETCH_ID_RE, "fetch_id")
