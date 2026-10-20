"""app/chzzk_chat.py — 합성 페이지(가짜 http)로 커서·경계 중복·종료 조건·파싱·가명화·보호 구역 분리를 본다.

실제 채팅·닉네임·userIdHash 는 쓰지 않는다: 닉네임 '시청자N', userIdHash = sha1('fixture-N'), video_no 0.
"""
import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app import chzzk_chat as cc
from app import jsonl
from app.core import ids

SALT = bytes(range(32))
NOW = lambda: datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)  # noqa: E731
RAND = lambda: "t3st"  # noqa: E731
FID = "F20261020-t3st"


def uh(n: int) -> str:
    return hashlib.sha1(f"fixture-{n}".encode()).hexdigest()


def chat(n: int, t: int, text: str | None = None, code: int = 1, role: str = "common_user",
         nick: str | None = None, uid: str | None = "auto", profile=None, extras="{}", st=None) -> dict:
    u = uh(n) if uid == "auto" else uid
    if profile is None:
        profile = json.dumps({"userIdHash": u, "nickname": nick if nick is not None else f"시청자{n}",
                              "userRoleCode": role}, ensure_ascii=False)
    return {"chatChannelId": "chan0", "content": text if text is not None else f"메시지 {n}-{t}",
            "extras": extras, "messageStatusType": "NORMAL",
            "messageTime": st if st is not None else 1_700_000_000_000 + (t if isinstance(t, int) else 0),
            "messageTypeCode": code, "playerMessageTime": t, "profile": profile, "userIdHash": u}


class FakeApi:
    """실제 API 처럼: 커서보다 '큰' playerMessageTime 만, 앞에서부터 size 개. next = 마지막 항목 시각."""

    def __init__(self, chats, size=3, next_fn=None, fail_on_call=None, statuses=None):
        self.chats = sorted(chats, key=lambda c: c["playerMessageTime"])
        self.size = size
        self.next_fn = next_fn
        self.fail_on_call = fail_on_call
        self.statuses = list(statuses or [])
        self.calls: list[int] = []

    def __call__(self, url, timeout):
        if self.statuses:
            st = self.statuses.pop(0)
            if st != 200:
                return st, "oops"
        q = parse_qs(urlparse(url).query)
        cur = int(q["playerMessageTime"][0])
        assert q["previousVideoChatSize"] == ["50"]
        self.calls.append(cur)
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise OSError("연결 끊김")
        page = [c for c in self.chats if c["playerMessageTime"] > cur][: self.size]
        if self.next_fn:
            nxt = self.next_fn(cur, page)
        else:
            nxt = page[-1]["playerMessageTime"] if page else None
        body = {"code": 200, "message": None,
                "content": {"videoChats": page, "previousVideoChats": [], "nextPlayerMessageTime": nxt}}
        return 200, json.dumps(body, ensure_ascii=False)


def run(tmp_path, api, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("rand4", RAND)
    kw.setdefault("sleep", lambda s: None)
    return cc.collect("0", str(tmp_path), SALT, http=api, **kw)


def messages(tmp_path):
    r = jsonl.read(str(tmp_path / "chat" / "messages.jsonl"))
    assert r.ok and not r.truncated_tail
    return r.records


def fetch_rec(tmp_path):
    with open(tmp_path / "chat" / f"fetch-{FID}.json", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- 주소
def test_parse_video_no():
    assert cc.parse_video_no("123") == "123"
    assert cc.parse_video_no(" https://chzzk.naver.com/video/123?t=5 ") == "123"
    assert cc.parse_video_no("chzzk.naver.com/video/77") == "77"
    assert cc.parse_video_no("https://m.chzzk.naver.com/video/9/") == "9"
    assert cc.parse_video_no("https://chzzk.naver.com/live/abc") is None
    assert cc.parse_video_no("https://evil.example/chzzk.naver.com/video/1") is None
    assert cc.parse_video_no("") is None and cc.parse_video_no(None) is None


# --------------------------------------------------------------------------- 커서·경계
def test_cursor_progress_and_boundary_duplicates(tmp_path):
    # 같은 시각(200)에 세 채팅이 몰려 페이지 경계에 걸친다
    cs = [chat(1, 100), chat(2, 200), chat(3, 200), chat(4, 200), chat(5, 300), chat(6, 400)]
    api = FakeApi(cs, size=3)
    s = run(tmp_path, api)
    assert api.calls[0] == 0
    # 두 번째 요청은 next(=200) - 1
    assert api.calls[1] == 199
    msgs = messages(tmp_path)
    assert len(msgs) == 6 == s.messages
    assert sorted(m["t_ms"] for m in msgs) == [100, 200, 200, 200, 300, 400]
    assert s.duplicates_skipped > 0
    assert fetch_rec(tmp_path)["duplicates_skipped"] == s.duplicates_skipped
    assert s.complete and s.stop_reason == cc.STOP_NO_NEXT
    assert len({m["chat_id"] for m in msgs}) == 6


def test_same_page_identical_items_kept_with_suffix(tmp_path):
    a = chat(1, 100, text="ㅋㅋ")
    cs = [a, dict(a), chat(2, 150)]
    s = run(tmp_path, FakeApi(cs, size=10))
    msgs = messages(tmp_path)
    assert s.messages == 3 and s.duplicates_skipped == 1   # 같은 페이지 안 2건은 둘 다 남고, 경계에서 다시 온 150 만 걸러진다
    cids = [m["chat_id"] for m in msgs]
    assert cids[1] == cids[0] + "_2"
    assert all(ids.CHAT_ID_RE.match(c) for c in cids)


def test_stop_cursor_not_advancing(tmp_path):
    api = FakeApi([chat(1, 100), chat(2, 200)], size=1, next_fn=lambda cur, page: cur)
    s = run(tmp_path, api)
    assert s.complete and s.stop_reason == cc.STOP_NOT_ADVANCING and len(api.calls) == 1


def test_stop_next_not_integer(tmp_path):
    for bad in (None, "300", 1.5, True):
        d = tmp_path / repr(bad).replace("'", "")
        api = FakeApi([chat(1, 100), chat(2, 200)], size=1, next_fn=lambda cur, page, b=bad: b)
        s = run(d, api)
        assert s.stop_reason == cc.STOP_NO_NEXT and len(api.calls) == 1, bad


def test_stop_past_duration_limit(tmp_path):
    # 길이 1초 → 상한 1초 + 10분. next 가 그보다 크면 멈춘다
    cs = [chat(1, 100), chat(2, 200), chat(3, 700_000), chat(4, 800_000)]
    api = FakeApi(cs, size=2)
    s = run(tmp_path, api, duration_ms=1000)
    assert s.complete and s.stop_reason == cc.STOP_PAST_LIMIT
    assert len(api.calls) == 2
    # 상한을 넘는 시각의 채팅은 t_ms null + invalid_time
    bad = [m for m in messages(tmp_path) if m.get("flags", {}).get("invalid_time")]
    assert len(bad) == 1 and all(m["t_ms"] is None for m in bad)


def test_unknown_duration_limit_is_48h():
    assert cc.limit_ms_for(None) == 48 * 3600 * 1000
    assert cc.limit_ms_for(3_600_000) == 3_600_000 + 600_000
    assert cc.limit_ms_for(10 ** 12) == 48 * 3600 * 1000


def test_page_limit_incomplete(tmp_path):
    cs = [chat(i, 100 * i) for i in range(1, 11)]
    s = run(tmp_path, FakeApi(cs, size=2), max_pages=3)
    assert not s.complete and s.stop_reason == cc.STOP_PAGE_LIMIT and s.pages == 3
    rec = fetch_rec(tmp_path)
    assert rec["complete"] is False and rec["stop_reason"] == "page_limit"


def test_three_pages_without_new_items_stop(tmp_path):
    # 서버가 같은 항목만 계속 주고 next 는 앞으로 간다
    one = chat(1, 100)

    class Stuck(FakeApi):
        def __call__(self, url, timeout):
            cur = int(parse_qs(urlparse(url).query)["playerMessageTime"][0])
            self.calls.append(cur)
            return 200, json.dumps({"code": 200, "content": {"videoChats": [one], "nextPlayerMessageTime": cur + 10}})

    api = Stuck([])
    s = run(tmp_path, api)
    assert s.stop_reason == cc.STOP_NO_NEW and not s.complete
    assert len(api.calls) == 4 and s.messages == 1 and s.duplicates_skipped == 3


def test_same_ms_burst_skips_forward(tmp_path):
    # 한 ms 에 페이지 크기보다 많은 채팅 → next-1 이 커서와 같아지면 next 로 한 칸 건너뛴다
    cs = [chat(1, 100), chat(2, 100), chat(3, 100), chat(4, 300)]
    api = FakeApi(cs, size=2)
    s = run(tmp_path, api)
    assert api.calls[:3] == [0, 99, 100]
    assert s.messages == 3   # 그 ms 의 셋째 채팅은 받지 못한다 (알려진 한계, boundary_skips 로 남긴다)
    assert fetch_rec(tmp_path)["boundary_skips"] == 2   # 100 에서 한 번, 마지막 300 에서 한 번
    assert s.complete


def test_next_cursor_rules():
    assert cc.next_cursor(0, 200, 10_000) == (199, None, False)
    assert cc.next_cursor(199, 200, 10_000) == (200, None, True)
    assert cc.next_cursor(200, 200, 10_000) == (None, cc.STOP_NOT_ADVANCING, False)
    assert cc.next_cursor(0, 20_000, 10_000) == (None, cc.STOP_PAST_LIMIT, False)
    assert cc.next_cursor(0, None, 10_000)[1] == cc.STOP_NO_NEXT


# --------------------------------------------------------------------------- HTTP
def test_retry_then_success_and_waits(tmp_path):
    waits = []
    api = FakeApi([chat(1, 100)], statuses=[503, 429, 200])
    run(tmp_path, api, sleep=waits.append)
    assert round(sum(waits), 6) >= 3.0   # 1초 + 2초 (+요청 사이 쉼)


def test_code_not_200_is_error(tmp_path):
    def api(url, timeout):
        return 200, json.dumps({"code": 404, "message": "x", "content": None})
    with pytest.raises(cc.ChatFetchError):
        run(tmp_path, api)
    assert not os.path.exists(tmp_path / "chat" / "messages.jsonl")


def test_4xx_not_retried(tmp_path):
    n = []

    def api(url, timeout):
        n.append(1)
        return 404, "not found"
    with pytest.raises(cc.ChatFetchError):
        run(tmp_path, api)
    assert len(n) == 1


def test_gives_up_after_retries(tmp_path):
    n = []

    def api(url, timeout):
        n.append(1)
        raise OSError("down")
    with pytest.raises(cc.ChatFetchError):
        run(tmp_path, api)
    assert len(n) == 4


# --------------------------------------------------------------------------- 파싱
def parse(item, **kw):
    kw.setdefault("limit_ms", cc.UNKNOWN_LIMIT_MS)
    return cc.parse_message(item, video_no="0", salt=SALT, fetch_id=FID, page=2, index=5, **kw)


@pytest.mark.parametrize("code,kind", [(1, "text"), (2, "image"), (3, "sticker"), (4, "video"), (5, "rich"),
                                       (10, "donation"), (11, "subscription"), (30, "system"), (7, "other"),
                                       (None, "other")])
def test_kind_mapping(code, kind):
    r = parse(chat(1, 10, code=code)).record
    assert r["kind"] == kind
    assert r["type_code"] == (code if code is not None else -1)


@pytest.mark.parametrize("role,out", [("streamer", "streamer"), ("streaming_channel_manager", "manager"),
                                      ("streaming_chat_manager", "manager"), ("common_user", "viewer"),
                                      ("something_new", "unknown")])
def test_role_mapping(role, out):
    assert parse(chat(1, 10, role=role)).record["role"] == out


def test_nickname_masks():
    assert cc.mask_nickname("시청자1") == "시***"
    assert cc.mask_nickname("가") == "가"
    assert cc.mask_nickname("가나다라마바사아자차카") == "가" + "*" * 7
    assert cc.mask_nickname("시청자1", "hidden") == "***"
    assert cc.mask_nickname(None) is None and cc.mask_nickname("") is None
    with pytest.raises(ValueError):
        cc.mask_nickname("x", "plain")
    r = parse(chat(3, 10), nickname_mask="hidden").record
    assert r["nickname_masked"] == "***"


def test_record_shape_and_ids():
    item = chat(1, 1234)
    p = parse(item)
    r = p.record
    assert list(r) == ["chat_id", "t_ms", "server_time_ms", "type_code", "kind", "status", "viewer_id",
                       "is_anonymous", "nickname_masked", "role", "text", "raw_ref"]
    assert "flags" not in r   # 하나도 true 가 아니면 쓰지 않는다
    assert r["chat_id"] == ids.chat_id_base("0", item["messageTime"], uh(1), 1, item["content"])
    assert r["viewer_id"] == ids.viewer_id(SALT, uh(1)) and ids.VIEWER_ID_RE.match(r["viewer_id"])
    assert r["raw_ref"] == {"fetch_id": FID, "page": 2, "index": 5}
    assert r["t_ms"] == 1234 and r["server_time_ms"] == item["messageTime"]
    assert p.user_id_hash == uh(1) and p.nickname == "시청자1"


def test_anonymous():
    for uid in (None, "", "anonymous"):
        prof = json.dumps({"nickname": "익명의 후원자", "userRoleCode": "common_user"}, ensure_ascii=False)
        p = parse(chat(1, 10, code=10, uid=uid, profile=prof))
        assert p.record["viewer_id"] == "anon" and p.record["is_anonymous"] is True
        assert p.user_id_hash is None


def test_user_id_hash_from_profile_when_top_level_missing():
    item = chat(4, 10)
    item["userIdHash"] = None
    assert parse(item).record["viewer_id"] == ids.viewer_id(SALT, uh(4))


def test_text_truncated_4000():
    r = parse(chat(1, 10, text="가" * 4500)).record
    assert len(r["text"]) == 4000 and r["flags"] == {"text_truncated": True}
    assert "flags" not in parse(chat(1, 10, text="가" * 4000)).record


@pytest.mark.parametrize("t", [-1, "abc", 1.5, None, True, 48 * 3600 * 1000 + 1, [1]])
def test_invalid_time(t):
    item = chat(1, 0)
    item["playerMessageTime"] = t
    r = parse(item).record
    assert r["t_ms"] is None and r["flags"]["invalid_time"] is True


def test_valid_time_forms():
    item = chat(1, 0)
    for v, want in ((0, 0), ("1500", 1500), (2000.0, 2000)):
        item["playerMessageTime"] = v
        assert parse(item).record["t_ms"] == want


def test_profile_and_extras_parse_errors():
    item = chat(1, 10, profile="{not json", extras="[1,2")
    r = parse(item).record
    assert r["flags"] == {"profile_parse_error": True, "extras_parse_error": True}
    assert r["nickname_masked"] is None and r["role"] == "unknown"
    # 값이 없으면 오류가 아니다
    item = chat(1, 10, extras=None)
    item["profile"] = None
    r = parse(item).record
    assert "flags" not in r and r["nickname_masked"] is None


# --------------------------------------------------------------------------- 저장·보호 구역
def test_protected_zone_separation(tmp_path):
    cs = [chat(i, 100 * i) for i in range(1, 8)]
    cs.append(chat(1, 900, nick="시청자901"))   # 같은 사람, 바뀐 닉네임
    run(tmp_path, FakeApi(cs, size=3))
    raw_msgs = (tmp_path / "chat" / "messages.jsonl").read_text(encoding="utf-8")
    fetch_txt = (tmp_path / "chat" / f"fetch-{FID}.json").read_text(encoding="utf-8")
    ident_txt = (tmp_path / "chat" / "protected" / "identities.jsonl").read_text(encoding="utf-8")
    for i in range(1, 8):
        for secret in (uh(i), f"시청자{i}"):
            assert secret not in raw_msgs and secret not in fetch_txt
            assert secret in ident_txt
    idents = jsonl.read(str(tmp_path / "chat" / "protected" / "identities.jsonl")).records
    one = [x for x in idents if x["user_id_hash"] == uh(1)][0]
    assert [n["nickname"] for n in one["nicknames"]] == ["시청자1", "시청자901"]
    assert one["nicknames"][1]["first_seen_ms"] == 900
    assert one["viewer_id"] == ids.viewer_id(SALT, uh(1))
    # 원문 페이지
    with gzip.open(tmp_path / "chat" / "protected" / f"pages-{FID}.jsonl.gz", "rb") as f:
        pages = [json.loads(l) for l in f.read().decode("utf-8").splitlines()]
    assert [p["page"] for p in pages] == list(range(len(pages)))
    assert set(pages[0]) == {"page", "request_url", "requested_at", "status", "body"}
    assert pages[0]["status"] == 200 and "playerMessageTime=0" in pages[0]["request_url"]
    # raw_ref 로 원문을 다시 찾을 수 있다
    for m in messages(tmp_path):
        body = json.loads(pages[m["raw_ref"]["page"]]["body"])
        item = body["content"]["videoChats"][m["raw_ref"]["index"]]
        assert cc.parse_message(item, video_no="0", salt=SALT, fetch_id=FID, page=m["raw_ref"]["page"],
                                index=m["raw_ref"]["index"]).record["chat_id"] == m["chat_id"].split("_")[0]


def test_fetch_record_fields(tmp_path):
    run(tmp_path, FakeApi([chat(1, 100), chat(2, 200, code=10)], size=5), duration_ms=5000)
    rec = fetch_rec(tmp_path)
    for k in ("endpoint", "started_at", "finished_at", "pages", "messages", "complete", "stop_reason",
              "user_agent", "app_version", "messages_sha256", "duplicates_skipped"):
        assert k in rec
    assert rec["user_agent"] == "Mozilla/5.0" and rec["kind_counts"] == {"text": 1, "donation": 1}
    data = (tmp_path / "chat" / "messages.jsonl").read_bytes()
    assert rec["messages_sha256"] == hashlib.sha256(data).hexdigest()
    assert not data.startswith(b"\xef\xbb\xbf") and b"\r\n" not in data
    assert rec["fetch_id"] == FID and re.match(r"\AF[0-9]{8}-[a-z0-9]{4}\Z", rec["fetch_id"])


def test_refuses_existing_messages(tmp_path):
    run(tmp_path, FakeApi([chat(1, 100)]))
    with pytest.raises(cc.ChatFetchError):
        run(tmp_path, FakeApi([chat(1, 100)]), rand4=lambda: "zz99")
    assert not os.path.exists(tmp_path / "chat" / "fetch-F20261020-zz99.json")


def _left_files(tmp_path):
    out = []
    for root, _dirs, files in os.walk(tmp_path):
        out += [os.path.join(root, f) for f in files]
    return out


def test_mid_failure_leaves_no_files(tmp_path):
    cs = [chat(i, 100 * i) for i in range(1, 11)]
    api = FakeApi(cs, size=2, fail_on_call=3)
    api_always_fail_after = api

    def http(url, timeout):   # 3번째 페이지부터 계속 끊긴다
        if len(api.calls) >= 2:
            api.calls.append(-1)
            raise OSError("down")
        return api_always_fail_after(url, timeout)
    with pytest.raises(cc.ChatFetchError):
        run(tmp_path, http)
    assert _left_files(tmp_path) == []
    assert not [d for d in os.listdir(tmp_path / "chat") if d.startswith(".tmp")]


def test_cancel_leaves_no_files(tmp_path):
    cs = [chat(i, 100 * i) for i in range(1, 11)]
    api = FakeApi(cs, size=2)
    with pytest.raises(cc.ChatFetchCancelled):
        run(tmp_path, api, cancel=lambda: len(api.calls) >= 2)
    assert _left_files(tmp_path) == []


def test_progress_callback(tmp_path):
    seen = []
    run(tmp_path, FakeApi([chat(i, 100 * i) for i in range(1, 6)], size=2), duration_ms=1000,
        on_progress=lambda f, p, m, c: seen.append((f, p, m)))
    assert [p for _f, p, _m in seen] == list(range(1, len(seen) + 1))
    assert all(0.0 <= f <= 0.99 for f, _p, _m in seen) and seen[-1][2] == 5


def test_salt_file_tool(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("fetch_chat", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "fetch_chat.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = str(tmp_path / "chat" / "protected" / "salt.json")
    s1, created = mod.load_or_create_salt(p)
    assert created and len(s1) == 32
    s2, created2 = mod.load_or_create_salt(p)
    assert s2 == s1 and not created2
    obj = json.loads(open(p, encoding="utf-8").read())
    assert set(obj) == {"salt_hex", "created_at"} and len(obj["salt_hex"]) == 64


# --------------------------------------------------------------------------- 검토 수정 재현 (2026-10-20)
def test_incomplete_read_is_retried(tmp_path):
    """본문을 읽다 끊긴 http.client.IncompleteRead(OSError 아님)도 재시도한다."""
    import http.client as hc
    n = []

    def http(url, timeout):
        n.append(1)
        if len(n) == 1:
            raise hc.IncompleteRead(b"abc", 10)
        return 200, json.dumps({"code": 200, "content": {}})
    st, body, _ = cc.http_get("http://x/", http, sleep=lambda s: None)
    assert st == 200 and len(n) == 2


def test_incomplete_read_exhausted_is_chat_fetch_error():
    import http.client as hc

    def http(url, timeout):
        raise hc.IncompleteRead(b"abc", 10)
    with pytest.raises(cc.ChatFetchError):
        cc.http_get("http://x/", http, sleep=lambda s: None)


def test_temp_folder_inside_protected_zone(tmp_path):
    """수집 중 임시 폴더는 chat/protected/ 안에만 있다(강제 종료 때 원문이 보호 구역 밖에 남지 않게)."""
    seen = []
    api = FakeApi([chat(i, 100 * i) for i in range(1, 6)], size=2)

    def http(url, timeout):
        chat_dir = tmp_path / "chat"
        seen.append(sorted(d for d in os.listdir(chat_dir) if d.startswith(".tmp")))
        seen.append(sorted(d for d in os.listdir(chat_dir / "protected") if d.startswith(".tmp")))
        return api(url, timeout)
    run(tmp_path, http)
    assert seen[0] == [] and seen[1] == [f".tmp-{FID}"]
    assert not [d for d in os.listdir(tmp_path / "chat" / "protected") if d.startswith(".tmp")]


def test_stale_tmp_folders_removed(tmp_path):
    old1 = tmp_path / "chat" / ".tmp-F20261019-aaaa" / "protected"
    old2 = tmp_path / "chat" / "protected" / ".tmp-F20261019-bbbb"
    old1.mkdir(parents=True)
    old2.mkdir(parents=True)
    (old1 / "pages-F20261019-aaaa.jsonl.gz").write_bytes(b"x")
    run(tmp_path, FakeApi([chat(1, 100)]))
    assert not (tmp_path / "chat" / ".tmp-F20261019-aaaa").exists()
    assert not old2.exists()


def test_hard_kill_leaves_no_raw_outside_protected(tmp_path):
    """강제 종료(os._exit, except·finally 안 돎) 뒤에도 보호 구역 밖에 원문 페이지·신원 정보가 없다."""
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proj = tmp_path / "proj"
    proj.mkdir()
    script = tmp_path / "killcol.py"
    script.write_text(f'''
import hashlib, json, os, sys
sys.path.insert(0, {root!r})
from app import chzzk_chat as cc
calls = []
def http(url, timeout):
    calls.append(1)
    if len(calls) == 31:
        os._exit(9)
    t = len(calls) * 1000
    items = []
    for i in range(200):
        u = hashlib.sha1(("fixture-%d" % i).encode()).hexdigest()
        prof = json.dumps({{"userIdHash": u, "nickname": "시청자%d" % i, "userRoleCode": "common_user"}}, ensure_ascii=False)
        items.append({{"chatChannelId": "chan0", "content": "메시지 %d-%d" % (i, t), "extras": "{{}}",
                      "messageStatusType": "NORMAL", "messageTime": 1700000000000 + t + i, "messageTypeCode": 1,
                      "playerMessageTime": t + i, "profile": prof, "userIdHash": u}})
    body = {{"code": 200, "content": {{"videoChats": items, "previousVideoChats": [], "nextPlayerMessageTime": t + 199}}}}
    return 200, json.dumps(body, ensure_ascii=False)
cc.collect("0", {str(proj)!r}, b"s" * 32, http=http, sleep=lambda s: None, request_gap=(0, 0))
''', encoding="utf-8")
    r = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=120)
    assert r.returncode == 9, r.stderr.decode("utf-8", "replace")
    prot = os.path.join(str(proj), "chat", "protected")
    outside = []
    for dp, _dn, fns in os.walk(str(proj)):
        for fn in fns:
            full = os.path.join(dp, fn)
            if not os.path.abspath(full).startswith(os.path.abspath(prot) + os.sep):
                outside.append(full)
    assert outside == []
    inside = [os.path.join(dp, fn) for dp, _dn, fns in os.walk(prot) for fn in fns]
    assert any(fn.startswith(os.path.join(prot, ".tmp-")) for fn in inside)   # 남은 것은 보호 구역 안 임시 폴더뿐
