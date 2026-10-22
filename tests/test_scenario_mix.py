"""app/scenario_mix.py — 가상 채팅을 훈련에 섞을 때 앱 쪽 일 (20 5장 H-4·11-9, 08 D-05·D-19 2).

합성 데이터만 쓴다(tmp_path). 실제 채팅·닉네임·시범 프로젝트 폴더는 쓰지 않는다. 엔진 도구 실행은 가짜 Popen 으로 본다.
"""
import ast
import json
import os
import subprocess
import sys

import pytest

from app import jsonl, scenario_mix as sm
from app import scenario_store as sst
from app.core import scenario as sc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID = "11111111-2222-4333-8444-555555555555"
OTHER = "11111111-2222-4333-8444-666666666666"


def cid(n):
    return "c" + f"{n:020x}"


def msg(n, t, text="합성 본문"):
    return {"chat_id": cid(n), "t_ms": t, "kind": "text", "viewer_id": "vw_aaaaaaaaaaaa", "nickname_masked": "시**",
            "role": "common_user", "text": text}


def mkproj(parent, name, pid, msgs):
    d = os.path.join(str(parent), name)
    os.makedirs(os.path.join(d, "chat"))
    with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": pid}, f)
    jsonl.write_all(os.path.join(d, "chat", "messages.jsonl"), msgs)
    return d


# ---------------------------------------------------------------- resolve
def test_resolver_same_project_and_other_project_once(tmp_path, monkeypatch):
    own = [msg(1, 1000), msg(2, 2000)]
    root = mkproj(tmp_path, "proj-a", PID, own)
    mkproj(tmp_path, "proj-b", OTHER, [msg(9, 9000, "다른 방송 합성")])
    reads = []
    real = sm.load_messages
    monkeypatch.setattr(sm, "load_messages", lambda d: reads.append(os.path.basename(d)) or real(d))
    r = sm.make_resolver(root, PID, own)
    assert r(PID, cid(2)) is own[1]                                  # 같은 프로젝트: 같은 dict (복사 없음)
    assert r(PID, cid(7)) is None
    assert r(OTHER, cid(9))["text"] == "다른 방송 합성"
    assert r(OTHER, cid(8)) is None
    assert reads == ["proj-b"]                                       # 다른 프로젝트는 한 번만 읽는다
    assert r("99999999-2222-4333-8444-555555555555", cid(9)) is None  # 모르는 프로젝트
    assert r(PID, None) is None


def test_resolver_never_opens_pilot_folders(tmp_path, monkeypatch):
    own = [msg(1, 1000)]
    root = mkproj(tmp_path, "proj-a", PID, own)
    pilot = mkproj(tmp_path, "pilot-합성", OTHER, [msg(9, 9000)])
    opened = []
    real_open = open

    def spy(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", spy)
    r = sm.make_resolver(root, PID, own)
    assert r(OTHER, cid(9)) is None
    assert not any(os.path.abspath(p).startswith(os.path.abspath(pilot)) for p in opened)


def test_resolver_skips_project_blocked_as_pilot(tmp_path, monkeypatch):
    from app import reference_set
    own = [msg(1, 1000)]
    root = mkproj(tmp_path, "proj-a", PID, own)
    mkproj(tmp_path, "proj-b", OTHER, [msg(9, 9000)])
    monkeypatch.setattr(reference_set, "pilot_block_reason", lambda d, **k: "합성 까닭")
    assert sm.make_resolver(root, PID, own)(OTHER, cid(9)) is None


# ---------------------------------------------------------------- 시나리오 찾기
def _write_scenario(root, rng=(0, 600_000)):
    hdr = {"kind": "scenario.header", "schema": sc.SCHEMA_ID, "project_id": PID,
           "range": {"vod_from_ms": rng[0], "vod_to_ms": rng[1]},
           "created_at": "2026-10-22T10:00:00+09:00", "created_by": "engine scenario 0.1.0",
           "inputs": {"messages_sha256": "0" * 64}, "generator": {"backend": "none"}, "external": {"enabled": False}}
    chats = [{"kind": "scenario.chat", "source": "retrieved", "show_vod_ms": 30_000, "from_project_id": PID,
              "from_chat_id": cid(5), "from_t_ms": 900_000, "from_scope": "same_broadcast", "retrieval_score": 0.2,
              "display_mask": "시청자1", "chat_kind": "text",
              "checks": {"nickname_hit": 0, "copy_max": 0.0, "blocked": 0, "llm_flag": 0}}]
    recs = sc.assemble(hdr, chats, yyyymmdd="20261022")
    problems = sst.validate(recs, project_id=PID)
    if problems:
        pytest.skip(f"합성 시나리오가 스키마와 맞지 않음({len(problems)}건): {problems[:2]}")
    return sst.write_scenario(root, recs, check_pilot=False)


def test_find_scenario_and_virtual_count(tmp_path):
    root = mkproj(tmp_path, "proj-a", PID, [msg(1, 10_000), msg(5, 900_000)])
    assert sm.find_scenario(root, (0, 600_000))[0] is None
    res = _write_scenario(root)
    scn, counts = sm.find_scenario(root, (0, 600_000))
    assert scn is not None and scn.scenario_id == res.scenario_id and sm.virtual_count(scn) == 1
    assert counts["ok"] == 1
    assert sm.find_scenario(root, (0, 500_000))[0] is None          # 다른 구간은 쓰지 않는다


def test_find_scenario_pilot_folder_not_listed(tmp_path, monkeypatch):
    root = mkproj(tmp_path, "pilot-합성", PID, [msg(1, 10_000)])
    seen = []
    real = os.listdir
    monkeypatch.setattr(os, "listdir", lambda p=".": seen.append(str(p)) or real(p))
    scn, counts = sm.find_scenario(root, (0, 600_000))
    assert scn is None and counts.get("blocked") == 1
    assert not any("scenarios" in p for p in seen)


# ---------------------------------------------------------------- 도구 부르기
def test_make_command_shape_and_frozen(monkeypatch, tmp_path):
    cmd = sm.make_command(str(tmp_path), (1000, 61_000), seed=3, extra_args=("--quiet-max-real", "45"))
    assert cmd[0] == sys.executable and cmd[1] == "-I" and cmd[2].endswith(os.path.join("tools", "make_scenario.py"))
    assert cmd[cmd.index("--from") + 1] == "1000" and cmd[cmd.index("--to") + 1] == "61000"
    assert cmd[cmd.index("--seed") + 1] == "3" and cmd[-2:] == ["--quiet-max-real", "45"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert sm.tool_path() is None and sm.make_command(str(tmp_path), (0, 1)) is None and not sm.tool_available()


def test_parse_output_last_json_line():
    assert sm.parse_output(b'noise\n{"a": 1}\n{"scenario_id": "SC-20261022-abcd", "written": true}\n') == \
        {"scenario_id": "SC-20261022-abcd", "written": True}
    assert sm.parse_output("") == {"error": "no_output"}
    assert sm.parse_output("not json\n[1]") == {"error": "no_output"}


class FakeProc:
    def __init__(self, out=b"", rc=0, hang=0):
        self.out, self.returncode, self.hang, self.killed = out, rc, hang, False

    def communicate(self, timeout=None):
        if self.hang > 0 and not self.killed:
            self.hang -= 1
            raise subprocess.TimeoutExpired("x", timeout)
        return self.out, b""

    def kill(self):
        self.killed = True


def test_run_make_ok_cancel_timeout():
    seen = {}

    def popen(cmd, **kw):
        seen.update(kw)
        seen["cmd"] = cmd
        return FakeProc('{"written": true, "counts": {"retrieved": 4}}\n'.encode(), rc=0)
    res = sm.run_make(["py", "tool"], popen=popen)
    assert res["written"] is True and res["rc"] == 0 and seen["stdin"] == subprocess.DEVNULL
    assert seen["cwd"] == sm.repo_root()
    p = FakeProc(hang=1000)
    res = sm.run_make(["py"], popen=lambda c, **k: p, cancelled=lambda: True)
    assert res["error"] == "cancelled" and p.killed
    p2 = FakeProc(hang=1000)
    res = sm.run_make(["py"], popen=lambda c, **k: p2, timeout_s=-1.0)
    assert res["error"] == "timeout" and p2.killed

    def boom(cmd, **kw):
        raise OSError("합성")
    assert sm.run_make(["py"], popen=boom)["error"] == "spawn_failed"


def test_describe_messages_have_numbers_only():
    assert sm.describe({"written": True, "counts": {"retrieved": 7}}) == (True, sm.MSG_WRITTEN.format(7))
    assert sm.describe({"written": False, "existing": "SC-20261022-abcd", "counts": {"retrieved": 7}})[0] is True
    ok, m = sm.describe({"reason": "nothing_to_fill", "counts": {"retrieved": 0}}, quiet_max_real=45)
    assert not ok and "45" in m
    assert sm.describe({"error": "pilot_locked"}) == (False, sm.MSG_FAILED.format("pilot_locked"))
    assert sm.describe(None)[0] is False


def test_module_boundaries():
    """app/ 은 engine/ 을 import 하지 않는다(엔진 도구는 따로 프로세스). Qt·네트워크·클립보드도 쓰지 않는다."""
    path = os.path.join(ROOT, "app", "scenario_mix.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            names.append(n.module or "")
    tops = {x.split(".")[0] for x in names if x}
    assert not tops & {"engine", "PySide6", "socket", "urllib", "http", "requests", "ssl"}
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "clipboard" not in src.lower()
