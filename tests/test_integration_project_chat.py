"""통합: tools/new_project.py → app.project → app.chzzk_chat.collect / tools/fetch_chat.py → tools/calibrate.py.

- 새 프로젝트를 CLI 로 만들고, 그 솔트(Project.salt())로 합성 http 를 주입한 collect 를 돌려
  파일 배치가 03 문서 2-1 과 같은지 본다.
- tools/fetch_chat.py 가 프로젝트의 salt.json 을 다시 만들지 않고 그대로 쓰는지(salt_id 일치) 본다.
- tools/calibrate.py 가 실제 Project.update 로 저장하는지(rev+1, .bak, calibration 판 추가) 본다.

합성 데이터만 쓴다: 닉네임 '시청자N', userIdHash = sha1('fixture-N'), video_no 0.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app import chzzk_chat as cc
from app import jsonl
from app import project
from app.core import ids
from app.core import timecode as tc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = lambda: datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)  # noqa: E731
RAND = lambda: "intg"  # noqa: E731
FID = "F20261020-intg"
MACHINE4 = "t3st"


def uh(n: int) -> str:
    return hashlib.sha1(f"fixture-{n}".encode()).hexdigest()


def chat(n: int, t: int, code: int = 1) -> dict:
    u = uh(n)
    profile = json.dumps({"userIdHash": u, "nickname": f"시청자{n}", "userRoleCode": "common_user"},
                         ensure_ascii=False)
    return {"chatChannelId": "chan0", "content": f"메시지 {n}-{t}", "extras": "{}",
            "messageStatusType": "NORMAL", "messageTime": 1_700_000_000_000 + t,
            "messageTypeCode": code, "playerMessageTime": t, "profile": profile, "userIdHash": u}


CHATS = [chat(1, 1000), chat(2, 2000), chat(1, 2000), chat(3, 5000), chat(2, 9000),
         chat(4, 12000, code=10), chat(5, 15000), chat(3, 20000)]
NICKS = {f"시청자{n}" for n in range(1, 6)}
HASHES = {uh(n) for n in range(1, 6)}


class FakeApi:
    """실제 API 처럼: 커서보다 큰 playerMessageTime 만 size 개, next = 마지막 항목 시각(없으면 null)."""

    def __init__(self, chats, size=3):
        self.chats = sorted(chats, key=lambda c: c["playerMessageTime"])
        self.size = size
        self.calls: list[str] = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        u = urlparse(url)
        assert u.netloc == "api.chzzk.naver.com"
        assert u.path == "/service/v1/videos/0/chats"
        cur = int(parse_qs(u.query)["playerMessageTime"][0])
        page = [c for c in self.chats if c["playerMessageTime"] > cur][: self.size]
        nxt = page[-1]["playerMessageTime"] if page else None
        body = {"code": 200, "message": None,
                "content": {"videoChats": page, "previousVideoChats": [], "nextPlayerMessageTime": nxt}}
        return 200, json.dumps(body, ensure_ascii=False)


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _tool(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools", name), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=ROOT, env=_env(), timeout=120)


def _new_project(tmp_path) -> project.Project:
    r = _tool("new_project.py", "--root", str(tmp_path), "--title", "합성 시험 방송", "--video-no", "0",
              "--machine4", MACHINE4)
    assert r.returncode == 0, r.stdout + r.stderr
    root = r.stdout.splitlines()[0].strip()
    assert os.path.isdir(root)
    assert os.path.dirname(root) == str(tmp_path)
    p = project.open(root)
    assert not p.read_only, p.problems
    return p


def _tree(root: str) -> set[str]:
    out = set()
    for d, dirs, files in os.walk(root):
        rel = os.path.relpath(d, root)
        for x in dirs:
            out.add(os.path.normpath(os.path.join(rel, x)).replace("\\", "/") + "/")
        for x in files:
            out.add(os.path.normpath(os.path.join(rel, x)).replace("\\", "/"))
    return out


# 03 2-1 의 파일 배치 (MVP 단계에서 생기는 것). relabel/·origin/ 은 v1 이라 없다.
EXPECTED_AFTER_CREATE = {
    "project.json", "guide/", "guide/v0.1.md",
    "chat/", "chat/protected/", "chat/protected/salt.json",
    "labels/", "locks/", "samples/", "derived/",
}
EXPECTED_AFTER_CHAT = EXPECTED_AFTER_CREATE | {
    "chat/messages.jsonl", f"chat/fetch-{FID}.json",
    "chat/protected/identities.jsonl", f"chat/protected/pages-{FID}.jsonl.gz",
}


def _collect(p: project.Project, api) -> cc.FetchSummary:
    return cc.collect("0", p.root, p.salt(), http=api, now=NOW, rand4=RAND, sleep=lambda s: None,
                      duration_ms=60_000)


# --------------------------------------------------------------------------- 만들기 → 수집
def test_new_project_then_collect_layout(tmp_path):
    p = _new_project(tmp_path)
    assert _tree(p.root) == EXPECTED_AFTER_CREATE
    rev0 = p.rev

    api = FakeApi(CHATS)
    s = _collect(p, api)
    assert s.complete and s.messages == len(CHATS) and s.fetch_id == FID

    assert _tree(p.root) == EXPECTED_AFTER_CHAT          # .tmp-<fid>/ 도 남지 않는다
    # 두 모듈이 같은 경로를 가리킨다
    cp = cc.chat_paths(p.root, FID)
    assert os.path.samefile(cp["messages"], p.paths.messages_jsonl)
    assert os.path.samefile(cp["identities"], p.paths.identities_jsonl)
    assert os.path.samefile(cp["fetch"], p.paths.fetch_json(FID))
    assert os.path.samefile(cp["pages"], p.paths.pages_gz(FID))

    # 가명화는 프로젝트 솔트로: viewer_id 가 project.json 의 salt_id 솔트로 계산된 값과 같다
    salt = p.salt()
    assert ids.salt_id(salt) == p.data["pseudonymization"]["salt_id"]
    r = jsonl.read(p.paths.messages_jsonl)
    assert r.ok and len(r.records) == len(CHATS)
    want_viewers = {ids.viewer_id(salt, uh(n)) for n in range(1, 6)}
    assert {m["viewer_id"] for m in r.records} == want_viewers
    assert [m["t_ms"] for m in r.records] == sorted(m["t_ms"] for m in r.records)

    # 보호 구역 분리: 닉네임·userIdHash 는 protected/ 에만
    for path in (p.paths.messages_jsonl, p.paths.fetch_json(FID), p.paths.project_json):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert not any(h in text for h in HASHES), path
        assert not any(n in text for n in NICKS), path
    with open(p.paths.identities_jsonl, encoding="utf-8") as f:
        ident = f.read()
    assert all(h in ident for h in HASHES)
    with gzip.open(p.paths.pages_gz(FID), "rt", encoding="utf-8") as f:
        assert sum(1 for _ in f) == s.pages

    # 수집은 project.json 을 건드리지 않는다. 다시 열어도 정상
    q = project.open(p.root)
    assert not q.read_only, q.problems
    assert q.rev == rev0
    assert not any("salt" in w for w in q.warnings)

    # 두 번째 수집은 거부되고 파일은 그대로
    before = _tree(p.root)
    with pytest.raises(cc.ChatFetchError):
        _collect(q, FakeApi(CHATS))
    assert _tree(p.root) == before


def test_fetch_chat_cli_reuses_project_salt(tmp_path, monkeypatch, capsys):
    """tools/fetch_chat.main 은 프로젝트가 만든 salt.json 을 그대로 쓴다(새로 만들지 않는다)."""
    p = _new_project(tmp_path)
    with open(p.paths.salt_json, "rb") as f:
        salt_bytes_before = f.read()

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import fetch_chat
    finally:
        sys.path.pop(0)
    api = FakeApi(CHATS)
    # collect 는 http 를 안 주면 모듈 전역 urllib_http 를 부른다 → 합성 응답으로 바꾼다 (네트워크 없음)
    monkeypatch.setattr(cc, "urllib_http", api)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    rc = fetch_chat.main(["0", "--project", p.root, "--duration-sec", "60"])
    out = capsys.readouterr()
    assert rc == 0, out.out + out.err
    assert "기존 파일" in out.out
    assert p.data["pseudonymization"]["salt_id"] in out.out
    assert api.calls, "합성 http 가 불리지 않았다"

    with open(p.paths.salt_json, "rb") as f:
        assert f.read() == salt_bytes_before
    q = project.open(p.root)
    assert not q.read_only, q.problems
    r = jsonl.read(q.paths.messages_jsonl)
    assert r.ok and len(r.records) == len(CHATS)
    salt = q.salt()
    assert {m["viewer_id"] for m in r.records} == {ids.viewer_id(salt, uh(n)) for n in range(1, 6)}
    names = {x for x in _tree(q.root) if x.startswith("chat/fetch-")}
    assert len(names) == 1


# --------------------------------------------------------------------------- 보정 저장
def test_calibrate_saves_through_project_update(tmp_path):
    p = _new_project(tmp_path)
    _collect(p, FakeApi(CHATS))
    rev0 = p.rev
    media_rev = p.data["media"]["media_rev"]

    r = _tool("calibrate.py", "--project", p.root, "--by", "L01",
              "--anchor", "10.9=10", "--anchor", "21.2=20", "--anchor", "30.8=30")
    assert r.returncode == 0, r.stdout + r.stderr

    q = project.open(p.root)
    assert not q.read_only, q.problems
    assert q.rev == rev0 + 1
    assert q.data["modified_by"].startswith("L01@")
    assert os.path.exists(q.paths.project_json + ".bak")
    cal = q.data["calibration"]
    assert cal["active_rev"] == 1 and [x["rev"] for x in cal["revs"]] == [0, 1]
    r1 = cal["revs"][1]
    assert r1["offset_ms"] == 900 and r1["media_rev"] == media_rev and len(r1["anchors"]) == 3
    c = tc.calibration_from_json(cal)
    assert c.status == "verified" and c.offset_ms == 900
    assert tc.chat_to_video_ms(10_000, c) == 10_900

    # 두 번째 보정은 판을 덧붙인다 (덮어쓰지 않는다)
    r = _tool("calibrate.py", "--project", p.root, "--anchor", "11=10", "--anchor", "21=20")
    assert r.returncode == 0, r.stdout + r.stderr
    q2 = project.open(p.root)
    assert q2.rev == rev0 + 2
    cal2 = q2.data["calibration"]
    assert cal2["active_rev"] == 2 and [x["rev"] for x in cal2["revs"]] == [0, 1, 2]
    assert cal2["revs"][1] == r1
    assert tc.calibration_from_json(cal2).offset_ms == 1000

    # --dry-run 은 저장하지 않는다
    r = _tool("calibrate.py", "--project", p.root, "--dry-run", "--anchor", "12=10")
    assert r.returncode == 0, r.stdout + r.stderr
    assert project.open(p.root).rev == rev0 + 2


def test_calibrate_refuses_read_only_project(tmp_path):
    """project.json 이 검증에 실패해 읽기 전용으로 열리면 calibrate 는 저장하지 않고 exit 2."""
    p = _new_project(tmp_path)
    with open(p.paths.project_json, "rb") as f:
        raw = f.read()
    obj = json.loads(raw)
    obj["schema_version"] = 999
    with open(p.paths.project_json, "wb") as f:
        f.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    assert project.open(p.root).read_only
    r = _tool("calibrate.py", "--project", p.root, "--anchor", "11=10")
    assert r.returncode == 2, r.stdout + r.stderr
    with open(p.paths.project_json, "rb") as f:
        assert json.loads(f.read())["schema_version"] == 999
