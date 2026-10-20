"""app/chzzk_chat.py kind_of — 실측에서 새로 나온 type_code 12·15, status BLIND 처리를 본다.

구조는 docs/measurements/2026-10-21-chzzk-types.md 에 적은 실측 '모양'만 따른다.
실제 채팅·닉네임·userIdHash·금액은 쓰지 않는다(전부 가짜 값).
"""
import hashlib
import json

import pytest

from app import chzzk_chat as cc

SALT = bytes(range(32))
KIND_ENUM = {"text", "image", "sticker", "video", "rich", "donation", "subscription", "system", "other"}


def _item(code: int, *, content: str = "", uid: str = "anonymous", profile=None, extras=None,
          status: str = "NORMAL", t: int = 1000) -> dict:
    return {
        "chatChannelId": "ch_fake",
        "content": content,
        "extras": json.dumps(extras or {}),
        "messageStatusType": status,
        "messageTime": 1_700_000_000_000 + t,
        "messageTypeCode": code,
        "playerMessageTime": t,
        "profile": None if profile is None else json.dumps(profile),
        "userIdHash": uid,
    }


def _parse(item: dict) -> dict:
    return cc.parse_message(item, video_no="0", salt=SALT, fetch_id="F20261020-t3st", page=0, index=0).record


@pytest.mark.parametrize("code,kind", [(12, "subscription"), (15, "other"), (11, "subscription"),
                                       (10, "donation"), (1, "text")])
def test_kind_of_new_codes(code, kind):
    assert cc.kind_of(code) == kind


def test_kind_table_stays_inside_schema_enum():
    assert set(cc.KIND_BY_CODE.values()) <= KIND_ENUM


def test_subscription_gift_item_shape():
    """12: content 빈 문자열, profile null, userIdHash 'anonymous', extras 에 gift* · receiver* 키."""
    extras = {"giftId": "g_fake", "giftTierName": "tier", "giftTierNo": 1,
              "giftType": "SUBSCRIPTION_GIFT_RECEIVER", "receiverNickname": "받는이",
              "receiverUserIdHash": hashlib.sha1(b"fixture-r").hexdigest(),
              "receiverVerifiedMark": False, "selectionType": "MANUAL"}
    rec = _parse(_item(12, extras=extras))
    assert rec["kind"] == "subscription"
    assert rec["type_code"] == 12
    assert rec["viewer_id"] == "anon" and rec["is_anonymous"] is True
    assert rec["text"] == ""
    assert rec["role"] == "unknown"
    # 받는 사람 신원(extras)은 레코드에 들어가지 않는다
    dumped = json.dumps(rec, ensure_ascii=False)
    assert "받는이" not in dumped and extras["receiverUserIdHash"] not in dumped


def test_code15_stays_other_and_keeps_raw_code():
    profile = {"nickname": "시청자1", "userIdHash": hashlib.sha1(b"fixture-1").hexdigest(),
               "userRoleCode": "common_user"}
    extras = {"minorPurchasable": True, "nickname": "시청자1", "orderAmount": 1,
              "productName": "가짜상품", "quantity": 1, "thumbnailImageUrl": "https://example.invalid/x.png"}
    rec = _parse(_item(15, uid=profile["userIdHash"], profile=profile, extras=extras))
    assert rec["kind"] == "other"
    assert rec["type_code"] == 15  # 원값은 남겨 나중에 다시 분류할 수 있다
    assert rec["text"] == ""
    assert "가짜상품" not in json.dumps(rec, ensure_ascii=False)


def test_blind_status_kept_as_is_with_empty_text():
    profile = {"nickname": "시청자2", "userIdHash": hashlib.sha1(b"fixture-2").hexdigest(),
               "userRoleCode": "common_user"}
    extras = {"chatType": "STREAMING", "emojis": {}, "extraToken": "x", "osType": "AOS",
              "streamingChannelId": "ch_fake"}
    rec = _parse(_item(1, uid=profile["userIdHash"], profile=profile, extras=extras, status="BLIND"))
    assert rec["status"] == "BLIND"
    assert rec["kind"] == "text"  # kind 는 type_code 로만 정한다
    assert rec["text"] == ""
