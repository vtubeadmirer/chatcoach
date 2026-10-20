"""합성 픽스처 생성기 (docs/design/02-architecture.md 11-2, 05-security.md 10-6 · S-21).

실제 치지직 응답·채팅·닉네임을 복사하지 않는다. 모든 값은 여기서 지어낸다.
  userIdHash = sha1('fixture-N')    닉네임 = '시청자N'    video_no = 0
  채널 = test-channel · 제목 = '테스트 방송' · 후원 금액 = 1000 고정 · 본문 = 직접 지은 문장

만드는 것
  labels    tests/fixtures/labels_*.jsonl (resolve 골든 입력 9개). 지난 묶음 파일과 바이트 단위로 같게 만든다.
  chzzk     chzzk_page_synth.json  — 치지직 VOD 채팅 API 응답 한 페이지 모양 (code·content.videoChats·nextPlayerMessageTime)
  messages  messages_synth.jsonl   — 위 페이지를 app.chzzk_chat.parse_message 로 바꾼 messages.jsonl (가명화된 것)

사용
  python tools/make_fixture.py --check                  labels 를 만들어 tests/fixtures 와 비교만 한다 (쓰지 않음). 다르면 exit 1
  python tools/make_fixture.py --out <폴더> [--what labels,chzzk,messages]
                                                        <폴더> 에 쓴다. --what 기본값은 셋 다

같은 입력이면 늘 같은 바이트가 나온다(시계·난수를 쓰지 않는다). 쓴 파일 이름과 크기만 출력한다(본문은 출력하지 않는다).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures")

# --------------------------------------------------------------------------- 가짜 신원 (S-21 허용 집합)
VIDEO_NO = "0"
CHANNEL_NAME = "test-channel"
TITLE = "테스트 방송"
DONATION_AMOUNT = 1000
SALT = bytes(range(32))                 # 합성용 고정 솔트 (실제 프로젝트 솔트 아님)
FETCH_ID = "F20261021-fixt"
MESSAGE_TIME_BASE = 1_700_000_000_000   # 가짜 서버 시각 기준 (ms)


def user_hash(n: int) -> str:
    """합성 시청자 n 의 userIdHash = sha1('fixture-n') 40자 16진수."""
    return hashlib.sha1(f"fixture-{n}".encode("ascii")).hexdigest()


def nickname(n: int) -> str:
    return f"시청자{n}"


def fixture_hashes(limit: int = 10000) -> frozenset[str]:
    """허용 목록: sha1('fixture-0') … sha1('fixture-{limit-1}'). test_fixture_clean 이 쓴다."""
    return frozenset(user_hash(n) for n in range(limit))


# --------------------------------------------------------------------------- labels_*.jsonl (resolve 골든)
# 지난 묶음(C1)이 scratchpad 스크립트로 만든 파일을 그대로 다시 만든다. 값·키 순서·구분자를 바꾸면 골든이 달라진다.
_S1 = "L01-P-20261021-aaaa"
_S2 = "L02-R2-20261021-bbbb"
_AT = "2026-10-21T09:00:00+09:00"


def _lid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _eid(n: int) -> str:
    return f"eeeeeeee-0000-4000-8000-{n:012d}"


def _open(sid: str, machine4: str = "m3fa", kind: str = "primary") -> dict:
    return {"v": 1, "id": _lid(9000 + sum(map(ord, sid + machine4))), "seq": 1, "kind": "session.open",
            "session_id": sid, "labeler_id": sid.split("-")[0], "created_at": _AT,
            "app_version": "0.1.0",
            "body": {"project_id": "11111111-2222-4333-8444-555555555555", "session_kind": kind,
                     "session_created_at": _AT,
                     "machine_id": "22222222-3333-4444-8555-666666666666", "machine4": machine4,
                     "guide_version": "0.1", "guide_doc_sha256": "a" * 64, "thresholds_sha256": "b" * 64,
                     "scope": {"type": "whole"},
                     "blind": {"to_ai": True, "to_other_sessions": True, "to_origin": True,
                               "nickname_masked_default": True},
                     "dataset_split": "dev"}}


def _label(n: int, seq: int, ent: int, sup: int | None = None, kind: str = "segment", sid: str = _S1) -> dict:
    if kind == "segment":
        body = {"start_ms": 1000 * n, "end_ms": 1000 * n + 60000, "category": "just_chatting",
                "confidence": "certain", "rationale": f"합성 {n}"}
    elif kind == "retract":
        body = {"reason": "undo"}
    else:
        body = {}
    return {"v": 1, "id": _lid(n), "seq": seq, "kind": kind, "session_id": sid, "labeler_id": sid.split("-")[0],
            "created_at": _AT, "app_version": "0.1.0",
            "entity_id": _eid(ent), "supersedes": _lid(sup) if sup else None,
            "guide_version": "0.1", "guide_sha12": "a" * 12, "thresholds_sha12": "b" * 12,
            "media_rev": 0, "calibration_rev": 0, "body": body}


def _line(rec: dict) -> bytes:
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _jsonl(recs: list[dict], *, bom: bool = False, cut_last: bytes | None = None) -> bytes:
    data = b"".join(_line(r) + b"\n" for r in recs)
    if bom:
        data = b"\xef\xbb\xbf" + data
    if cut_last is not None:
        data += cut_last          # 줄바꿈 없이 끝나는 잘린 줄
    return data


def label_fixtures() -> dict[str, bytes]:
    """파일 이름 → 내용. 경우 번호는 test_resolve.py 의 골든 경우와 같다."""
    L = _label
    out: dict[str, bytes] = {}
    # 1 단순 수정: 3판 연결 (머리 3) + 별개 대상 1판
    out["labels_simple_edit.jsonl"] = _jsonl([_open(_S1), L(1, 2, 1), L(2, 3, 1, sup=1), L(3, 4, 1, sup=2),
                                              L(4, 5, 2)])
    # 2·3 철회 / 철회 뒤 되살림
    out["labels_retract.jsonl"] = _jsonl([_open(_S1),
                                          L(10, 2, 10), L(11, 3, 10, sup=10, kind="retract"),   # 대상 10: 철회
                                          L(20, 4, 20), L(21, 5, 20, sup=20, kind="retract"),
                                          L(22, 6, 20, sup=21)])                                 # 대상 20: 되살림
    # 6·7 다른 세션 참조 / 다른 대상 참조 (+ 무효 줄을 가리키는 줄도 무효)
    out["labels_other_session.jsonl"] = _jsonl([
        _open(_S1), L(30, 2, 30),
        _open(_S2, "b7kq", "relabel_second"),
        L(31, 2, 30, sup=30, sid=_S2),          # S2 가 S1 의 판을 대체 → 무효
        L(32, 3, 30, sup=31, sid=_S2),          # 무효 줄을 가리킴 → 같이 무효
        L(33, 3, 31, sup=30),                   # 같은 세션, 다른 대상의 판 → 무효
    ])
    # 5 순환 (A→B→C→A) + 정상 대상 하나
    out["labels_cycle.jsonl"] = _jsonl([_open(_S1), L(40, 2, 40, sup=42), L(41, 3, 40, sup=40),
                                        L(42, 4, 40, sup=41), L(50, 5, 50)])
    # 4 포크: 한 판(60)을 두 줄이 대체
    out["labels_fork.jsonl"] = _jsonl([_open(_S1), L(60, 2, 60), L(61, 3, 60, sup=60), L(62, 4, 60, sup=60),
                                       L(70, 5, 70)])
    # 10 잘린 마지막 줄 (앞 57바이트만)
    tail = _line(L(81, 4, 80, sup=80))[:57]
    out["labels_truncated.jsonl"] = _jsonl([_open(_S1), L(80, 2, 80), L(90, 3, 90)], cut_last=tail)
    # 11 BOM
    out["labels_bom.jsonl"] = _jsonl([_open(_S1), L(100, 2, 100), L(101, 3, 100, sup=100)], bom=True)
    # 9 같은 세션, PC 두 대 (seq 가 겹침). 판 연결은 파일을 넘나든다
    out["labels_two_machines_a.jsonl"] = _jsonl([_open(_S1, "m3fa"), L(110, 2, 110), L(112, 3, 110, sup=111)])
    out["labels_two_machines_b.jsonl"] = _jsonl([_open(_S1, "b7kq"), L(111, 2, 110, sup=110), L(120, 3, 120)])
    return out


# --------------------------------------------------------------------------- 치지직 페이지 (videoChats 9필드)
# 직접 지은 문장. 실제 채팅을 옮기지 않는다.
_TEXTS = ("안녕하세요", "오늘도 왔어요", "ㅋㅋㅋㅋ", "방금 그거 뭐예요", "화이팅", "소리 잘 들려요",
          "다음 판 가요", "와 대박", "질문 있어요", "잘 자요")
_ROLES = ("common_user",) * 8 + ("streaming_chat_manager", "streamer")


def chzzk_chat_item(n: int, t_ms: int, *, text: str | None = None, code: int = 1,
                    role: str = "common_user", status: str = "NORMAL", extras: dict | None = None,
                    anonymous: bool = False) -> dict:
    """videoChats 항목 하나 (실측 '모양'만 따른 9필드). profile·extras 는 API 처럼 JSON 문자열로 넣는다."""
    if anonymous:
        uid, profile = "anonymous", None
    else:
        uid = user_hash(n)
        profile = json.dumps({"userIdHash": uid, "nickname": nickname(n), "userRoleCode": role},
                             ensure_ascii=False, separators=(",", ":"))
    return {"chatChannelId": "ch_fixture",
            "content": text if text is not None else _TEXTS[n % len(_TEXTS)],
            "extras": json.dumps(extras or {}, ensure_ascii=False, separators=(",", ":")),
            "messageStatusType": status,
            "messageTime": MESSAGE_TIME_BASE + t_ms,
            "messageTypeCode": code,
            "playerMessageTime": t_ms,
            "profile": profile,
            "userIdHash": uid}


def chzzk_items(count: int = 24, step_ms: int = 2500, viewers: int = 8) -> list[dict]:
    """일반 채팅 + 후원 1·구독 1·시스템 1·익명 후원 1·같은 시각 두 줄을 섞은 합성 항목들 (playerMessageTime 순)."""
    items: list[dict] = []
    for i in range(count):
        n = i % viewers + 1
        items.append(chzzk_chat_item(n, 1000 + i * step_ms, role=_ROLES[n % len(_ROLES)]))
    t_end = 1000 + count * step_ms
    items.append(chzzk_chat_item(1, t_end, text="응원합니다", code=10,
                                 extras={"payAmount": DONATION_AMOUNT, "payType": "CURRENCY", "isAnonymous": False}))
    items.append(chzzk_chat_item(0, t_end + 500, text="", code=10, anonymous=True,
                                 extras={"payAmount": DONATION_AMOUNT, "payType": "CURRENCY", "isAnonymous": True}))
    items.append(chzzk_chat_item(2, t_end + 1000, text="구독했어요", code=11, extras={"month": 1}))
    items.append(chzzk_chat_item(0, t_end + 1500, text="", code=30, anonymous=True))
    # 같은 시각·같은 사람·같은 본문 두 줄 (chat_id _2 꼴, 사유 E 판정 입력)
    items.append(chzzk_chat_item(3, t_end + 2000, text="ㅋㅋ"))
    items.append(chzzk_chat_item(3, t_end + 2000, text="ㅋㅋ"))
    return items


def chzzk_page(items: list[dict] | None = None, next_time="last") -> dict:
    """API 응답 본문 한 페이지. next_time='last' 면 마지막 항목 시각, None 이면 끝 페이지."""
    items = chzzk_items() if items is None else items
    nxt = (items[-1]["playerMessageTime"] if items else None) if next_time == "last" else next_time
    return {"code": 200, "message": None,
            "content": {"videoChats": items, "previousVideoChats": [], "nextPlayerMessageTime": nxt}}


def chzzk_page_bytes(page: dict | None = None) -> bytes:
    page = chzzk_page() if page is None else page
    return (json.dumps(page, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- messages.jsonl
def messages_records(items: list[dict] | None = None, page: int = 0) -> list[dict]:
    """합성 항목 → app.chzzk_chat.parse_message 결과 (chat_id 는 _2 꼴까지 붙인 최종값)."""
    from app import chzzk_chat as cc
    from app.core import ids

    items = chzzk_items() if items is None else items
    recs = [cc.parse_message(it, video_no=VIDEO_NO, salt=SALT, fetch_id=FETCH_ID, page=page, index=i).record
            for i, it in enumerate(items)]
    for rec, cid in zip(recs, ids.assign_chat_ids(r["chat_id"] for r in recs)):
        rec["chat_id"] = cid
    return recs


def messages_bytes(items: list[dict] | None = None) -> bytes:
    return _jsonl(messages_records(items))


# --------------------------------------------------------------------------- 쓰기 · 비교
WHAT = ("labels", "chzzk", "messages")


def build(what=WHAT) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if "labels" in what:
        out.update(label_fixtures())
    if "chzzk" in what:
        out["chzzk_page_synth.json"] = chzzk_page_bytes()
    if "messages" in what:
        out["messages_synth.jsonl"] = messages_bytes()
    return out


def write(out_dir: str, files: dict[str, bytes]) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for name, data in files.items():
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(data)
        print(f"{name} {len(data)}")


def compare(fixture_dir: str, files: dict[str, bytes]) -> list[str]:
    """다른 파일 이름 목록 (없거나 내용이 다름). 내용은 출력하지 않는다."""
    bad: list[str] = []
    for name, data in files.items():
        p = os.path.join(fixture_dir, name)
        try:
            with open(p, "rb") as f:
                same = f.read() == data
        except OSError:
            same = False
        if not same:
            bad.append(name)
    return bad


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="ChatCoach 합성 픽스처 생성 (실제 데이터 없음)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="labels 골든을 만들어 tests/fixtures 와 비교만 한다")
    g.add_argument("--out", help="쓸 폴더")
    ap.add_argument("--what", default=",".join(WHAT), help="labels,chzzk,messages 중 쉼표로")
    ap.add_argument("--fixture-dir", default=FIXTURE_DIR, help="--check 가 비교할 폴더 (기본 tests/fixtures)")
    a = ap.parse_args(argv)
    what = tuple(w.strip() for w in a.what.split(",") if w.strip())
    unknown = set(what) - set(WHAT)
    if unknown:
        ap.error(f"모르는 --what: {sorted(unknown)}")
    if a.check:
        files = label_fixtures()
        bad = compare(a.fixture_dir, files)
        for name in bad:
            print(f"다름: {name}")
        print(f"labels {len(files) - len(bad)}/{len(files)} 같음")
        return 1 if bad else 0
    write(a.out, build(what))
    return 0


if __name__ == "__main__":
    sys.exit(main())
