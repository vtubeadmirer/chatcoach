"""tools/adopt_project.py: project.json 이 없는 수집 폴더에 기존 솔트로 project.json 을 만든다.

수집 폴더는 실제와 같은 모양이 되도록 app.chzzk_chat.collect 에 합성 API 를 주입해 만든다
(tools/fetch_chat.py 를 프로젝트 없이 돌린 것과 같다: chat/, chat/protected/salt.json 만 있음).

합성 데이터만 쓴다: 닉네임 '시청자N', userIdHash = sha1('fixture-N'), video_no 100001.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app import chzzk_chat as cc
from app import project
from app.core import ids
from app.core.validate import canonical_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VNO = "100001"
FID = "F20261020-adpt"
SALT = bytes(range(32))
MACHINE4 = "t3st"
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
TITLE = "합성 시험 방송"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_tool_{name}", os.path.join(ROOT, "tools", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


adopt_mod = _load("adopt_project")
calibrate_mod = _load("calibrate")


def uh(n: int) -> str:
    return hashlib.sha1(f"fixture-{n}".encode()).hexdigest()


def chat(n: int, t: int, code: int = 1, anon: bool = False) -> dict:
    u = "anonymous" if anon else uh(n)
    profile = json.dumps({"userIdHash": u, "nickname": f"시청자{n}", "userRoleCode": "common_user"},
                         ensure_ascii=False)
    return {"chatChannelId": "chan0", "content": f"합성 메시지 {n}-{t}", "extras": "{}",
            "messageStatusType": "NORMAL", "messageTime": 1_700_000_000_000 + t,
            "messageTypeCode": code, "playerMessageTime": t, "profile": profile, "userIdHash": u}


CHATS = [chat(1, 1000), chat(2, 2000), chat(1, 3000), chat(3, 5000), chat(2, 9000),
         chat(4, 12000, code=10), chat(5, 15000), chat(6, 18000, code=10, anon=True), chat(3, 20000)]


class FakeApi:
    def __init__(self, chats, size=4):
        self.chats = sorted(chats, key=lambda c: c["playerMessageTime"])
        self.size = size

    def __call__(self, url, timeout):
        u = urlparse(url)
        assert u.netloc == "api.chzzk.naver.com"
        cur = int(parse_qs(u.query)["playerMessageTime"][0])
        page = [c for c in self.chats if c["playerMessageTime"] > cur][: self.size]
        nxt = page[-1]["playerMessageTime"] if page else None
        body = {"code": 200, "message": None,
                "content": {"videoChats": page, "previousVideoChats": [], "nextPlayerMessageTime": nxt}}
        return 200, json.dumps(body, ensure_ascii=False)


def make_collection(base, name="p" + VNO, *, salt=SALT, fid=FID) -> str:
    """project.json 없는 수집 폴더 (fetch_chat.py 가 만드는 모양)."""
    root = os.path.join(str(base), name)
    prot = os.path.join(root, "chat", "protected")
    os.makedirs(prot)
    with open(os.path.join(prot, "salt.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"salt_hex": salt.hex(), "created_at": "2026-10-20T12:00:00+09:00"}, f)
    cc.collect(VNO, root, salt, duration_ms=60_000, http=FakeApi(CHATS),
               now=lambda: datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc), rand4=lambda: fid[-4:],
               sleep=lambda s: None, request_gap=(0.0, 0.0))
    return root


def _files(root) -> dict[str, bytes]:
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


def _adopt(root, **kw):
    kw.setdefault("machine4", MACHINE4)
    kw.setdefault("now", NOW)
    return adopt_mod.adopt(root, TITLE, VNO, **kw)


# ---------------------------------------------------------------------------

def test_adopt_keeps_salt_and_fills_chat(tmp_path):
    root = make_collection(tmp_path)
    before = _files(root)
    r = _adopt(root, engine_author=True)
    assert r.written and not r.problems, r.problems

    p = project.open_project(root, machine4=MACHINE4)
    assert not p.read_only, p.problems
    assert p.salt() == SALT
    d = p.data
    assert d["pseudonymization"]["salt_id"] == ids.salt_id(SALT)
    assert d["broadcast"]["video_no"] == VNO and d["broadcast"]["title"] == TITLE
    assert d["broadcast"]["duration_ms"] == 60_000
    assert d["media"] == {"media_rev": 0, "current": None, "history": []}
    assert d["calibration"]["active_rev"] == 0
    assert d["labelers"][0]["engine_author"] is True

    with open(os.path.join(root, "chat", "messages.jsonl"), "rb") as f:
        msha = hashlib.sha256(f.read()).hexdigest()
    c = d["chat"]
    assert c["fetch_id"] == FID and c["chat_rev"] == 1
    assert c["messages_sha256"] == msha
    assert c["message_count"] == len(CHATS)
    assert c["complete"] is True and c["incomplete_reason"] is None
    assert c["kind_counts"] == {"text": 7, "donation": 2}
    assert c["anonymous_count"] == 1
    assert c["server_minus_player_ms"] == 1_700_000_000_000
    assert c["source"] == "chzzk_vod_api_v1" and c["chat_id_algorithm"] == "sl-chat-id-v1"
    assert d["x"]["adopted"]["fetch_id"] == FID

    # 기준서 시작값이 guide-config 에서 채워졌다
    with open(os.path.join(ROOT, "assets", "guide-config.v0.1.json"), "rb") as f:
        values = json.loads(f.read())["values"]
    g = d["guides"][0]
    assert g["thresholds"] == values
    assert g["thresholds_sha256"] == hashlib.sha256(canonical_json(values)).hexdigest()
    assert "guide_thresholds_missing" not in d["x"]

    # 기존 파일은 그대로, 새 파일은 project.json·guide 사본뿐, 임시 폴더는 남지 않는다
    after = _files(root)
    for k, v in before.items():
        assert after[k] == v, k
    new = set(after) - set(before)
    assert new == {"project.json", os.path.join("guide", "v0.1.md")}
    for sub in ("guide", "labels", "samples", "locks", "derived"):
        assert os.path.isdir(os.path.join(root, sub))
    assert not [n for n in os.listdir(os.path.join(root, "chat", "protected")) if n.startswith(".tmp")]
    # 폴더 이름은 바꾸지 않고 경고만
    assert os.path.basename(root) == "p" + VNO
    assert any("slug" in w for w in r.warnings)


def test_refuses_existing_project_json(tmp_path):
    root = make_collection(tmp_path)
    _adopt(root)
    with open(os.path.join(root, "project.json"), "rb") as f:
        first = f.read()
    with pytest.raises(adopt_mod.AdoptError, match="이미 project.json"):
        _adopt(root)
    with open(os.path.join(root, "project.json"), "rb") as f:
        assert f.read() == first


def test_refuses_app_created_project(tmp_path):
    p = project.create(str(tmp_path), "앱 프로젝트", VNO, machine4=MACHINE4, now=NOW)
    with pytest.raises(adopt_mod.AdoptError, match="이미 project.json"):
        adopt_mod.adopt(p.root, TITLE, VNO)


def test_dry_run_writes_nothing(tmp_path):
    root = make_collection(tmp_path)
    before = _files(root)
    dirs_before = sorted(d for d, _, _ in os.walk(root))
    r = _adopt(root, dry_run=True)
    assert not r.written
    assert r.data["chat"]["message_count"] == len(CHATS)
    assert project.validate_project(r.data) == []
    assert _files(root) == before
    assert sorted(d for d, _, _ in os.walk(root)) == dirs_before


def test_refuses_salt_mismatch(tmp_path):
    root = make_collection(tmp_path)
    with open(os.path.join(root, "chat", "protected", "salt.json"), "w", encoding="utf-8") as f:
        json.dump({"salt_hex": bytes(32).hex(), "created_at": "2026-10-20T12:00:00+09:00"}, f)
    with pytest.raises(adopt_mod.AdoptError, match="salt_id"):
        _adopt(root)
    assert not os.path.exists(os.path.join(root, "project.json"))


def test_refuses_missing_or_bad_salt(tmp_path):
    root = make_collection(tmp_path)
    sp = os.path.join(root, "chat", "protected", "salt.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump({"salt_hex": "XYZ"}, f)
    with pytest.raises(adopt_mod.AdoptError, match="salt_hex"):
        _adopt(root)
    os.remove(sp)
    with pytest.raises(adopt_mod.AdoptError, match="salt.json"):
        _adopt(root)


def test_refuses_changed_messages(tmp_path):
    root = make_collection(tmp_path)
    with open(os.path.join(root, "chat", "messages.jsonl"), "ab") as f:
        f.write(b"\n")
    with pytest.raises(adopt_mod.AdoptError, match="해시"):
        _adopt(root)


def test_refuses_video_no_mismatch(tmp_path):
    root = make_collection(tmp_path)
    with pytest.raises(adopt_mod.AdoptError, match="video_no"):
        adopt_mod.adopt(root, TITLE, "999", machine4=MACHINE4, now=NOW)
    with pytest.raises(adopt_mod.AdoptError, match="video_no"):
        adopt_mod.adopt(root, TITLE, "12a", machine4=MACHINE4, now=NOW)


def _edit_fetch(root, fid, **changes):
    p = os.path.join(root, "chat", f"fetch-{fid}.json")
    with open(p, encoding="utf-8") as f:
        rec = json.load(f)
    rec.update(changes)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    return rec


def test_refuses_count_and_kind_mismatch(tmp_path):
    root = make_collection(tmp_path)
    _edit_fetch(root, FID, messages=len(CHATS) + 1)
    with pytest.raises(adopt_mod.AdoptError, match="메시지 수"):
        _adopt(root)
    _edit_fetch(root, FID, messages=len(CHATS), kind_counts={"text": len(CHATS)})
    with pytest.raises(adopt_mod.AdoptError, match="kind"):
        _adopt(root)


def test_multiple_fetch_records(tmp_path):
    root = make_collection(tmp_path)
    with open(os.path.join(root, "chat", f"fetch-{FID}.json"), encoding="utf-8") as f:
        rec = json.load(f)
    other = "F20261020-zz99"
    rec["fetch_id"] = other
    with open(os.path.join(root, "chat", f"fetch-{other}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    with pytest.raises(adopt_mod.AdoptError, match="--fetch-id"):
        _adopt(root)
    r = _adopt(root, fetch_id=other, dry_run=True)
    assert r.data["chat"]["fetch_id"] == other
    with pytest.raises(adopt_mod.AdoptError, match="형식"):
        _adopt(root, fetch_id="bad")


def test_incomplete_fetch_warns(tmp_path):
    root = make_collection(tmp_path)
    _edit_fetch(root, FID, complete=False, stop_reason="page_limit")
    r = _adopt(root, dry_run=True)
    assert r.data["chat"]["complete"] is False
    assert r.data["chat"]["incomplete_reason"] == "page_limit"
    assert any("complete=false" in w for w in r.warnings)


def test_refuses_different_guide_copy(tmp_path):
    root = make_collection(tmp_path)
    os.makedirs(os.path.join(root, "guide"))
    with open(os.path.join(root, "guide", "v0.1.md"), "wb") as f:
        f.write(b"other")
    with pytest.raises(adopt_mod.AdoptError, match="기준서"):
        _adopt(root)
    assert not os.path.exists(os.path.join(root, "project.json"))


def test_cli_and_calibrate_can_save(tmp_path, capsys):
    root = make_collection(tmp_path)
    assert adopt_mod.main(["--project", root, "--title", TITLE, "--video-no", VNO, "--machine4", MACHINE4,
                           "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "합성 메시지" not in out and "시청자" not in out and SALT.hex() not in out
    assert not os.path.exists(os.path.join(root, "project.json"))

    assert adopt_mod.main(["--project", root, "--title", TITLE, "--video-no", VNO, "--machine4", MACHINE4]) == 0
    cap = capsys.readouterr()
    assert "합성 메시지" not in cap.out + cap.err and SALT.hex() not in cap.out + cap.err
    assert adopt_mod.main(["--project", root, "--title", TITLE, "--video-no", VNO]) == 1

    # 들인 뒤에는 calibrate 가 --dry-run 없이 저장할 수 있다
    assert calibrate_mod.main(["--project", root, "--anchor", "10=5", "--anchor", "20=15",
                               "--anchor", "30=25"]) == 0
    d = project.open_project(root).data
    assert d["calibration"]["active_rev"] == 1
    assert d["calibration"]["revs"][-1]["offset_ms"] == 5000
    assert d["rev"] == 2


def test_cli_missing_folder(tmp_path, capsys):
    assert adopt_mod.main(["--project", str(tmp_path / "없음"), "--title", TITLE, "--video-no", VNO]) == 1
    assert "폴더가 없습니다" in capsys.readouterr().err
