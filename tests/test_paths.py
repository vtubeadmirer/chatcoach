"""app/paths.py 경로 점검 · app/logging_setup.py 로그 정책 · app/crashguard.py crash.log·비정상 종료 (07 M4, S-09 · S-20 · S-31 · S-49).

로그 시험은 합성 닉네임·본문(아래 NICK·BODY)으로 수집 → 색인 → 백업 → 파기 흐름을 돌린 뒤
chatcoach.log · crash.log 에 그 글자가 없는지 본다. 실제 채팅·닉네임은 쓰지 않는다.
"""
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app import crashguard, logging_setup, paths
from app.logging_setup import redact

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))
NICK = "합성닉네임칠칠"
BODY = "합성본문문장가나다"


@pytest.fixture
def no_onedrive(monkeypatch):
    for v in paths.ONEDRIVE_VARS:
        monkeypatch.delenv(v, raising=False)


def codes(issues):
    return {(i.code, i.level) for i in issues}


# ---------------------------------------------------------------------------- 경로 점검
def test_path_issues_clean_folder(tmp_path, no_onedrive):
    d = tmp_path / "plain"
    d.mkdir()
    got = paths.path_issues(str(d), "project")
    assert {c for c, _ in codes(got)} <= {"non_ascii"}          # 임시 폴더 경로에 따라 한글이 있을 수 있다


def test_path_issues_onedrive_unc_long_missing(tmp_path, monkeypatch, no_onedrive):
    od = tmp_path / "od"
    (od / "proj").mkdir(parents=True)
    monkeypatch.setenv("OneDrive", str(od))
    assert ("onedrive", "confirm") in codes(paths.path_issues(str(od / "proj"), "project"))
    assert ("unc", "confirm") in codes(paths.path_issues(r"\\server\share\proj", "project", check_write=False))
    named = tmp_path / "OneDrive - 회사" / "p"
    named.mkdir(parents=True)
    monkeypatch.delenv("OneDrive")
    got = codes(paths.path_issues(str(named), "project"))
    assert ("onedrive_name", "warn") in got and ("non_ascii", "info") in got
    long = tmp_path / ("a" * 60) / ("b" * 60) / ("c" * 60)
    got = codes(paths.path_issues(str(long), "project"))
    assert ("long", "warn") in got and ("missing", "error") in got
    assert ("long", "warn") in codes(paths.path_issues(str(tmp_path / ("x" * 130)), "app", check_write=False))
    # 짧은 경로는 tmp_path 뿌리 길이에 기대지 않게 드라이브 바로 아래로 본다(tmp_path 뿌리가 %LOCALAPPDATA%\ChatCoachSmoke\pytest
    # 아래라 앱 문턱보다 길 수 있다, conftest). check_write=False 라 그 폴더를 만들지 않는다
    short = os.path.join(os.path.splitdrive(str(tmp_path))[0] + os.sep, "x" * 10)
    assert ("long", "warn") not in codes(paths.path_issues(short, "app", check_write=False))
    with pytest.raises(ValueError):
        paths.path_issues(str(tmp_path), "nope")


def test_path_issues_not_writable(tmp_path, monkeypatch, no_onedrive):
    d = tmp_path / "ro"
    d.mkdir()
    monkeypatch.setattr(paths, "_writable", lambda _d: False)
    assert ("not_writable", "error") in codes(paths.path_issues(str(d), "project"))
    assert ("not_writable", "warn") in codes(paths.path_issues(str(d), "app"))    # 앱 폴더는 폴백이 있다
    assert not paths.probe_writable(str(d / "none"))


def test_path_issue_messages_hold_no_path(tmp_path, monkeypatch, no_onedrive):
    secret = tmp_path / "숨길이름폴더" / ("q" * 200)
    monkeypatch.setenv("OneDrive", str(tmp_path))
    for it in paths.path_issues(str(secret), "project"):
        assert "숨길이름폴더" not in it.message and str(tmp_path) not in it.message
        assert it.role == "project" and it.level in ("confirm", "error", "warn", "info")


def test_startup_and_project_issues(tmp_path, monkeypatch, no_onedrive):
    got = paths.startup_path_issues()
    assert all(i.role in ("app", "data", "backup") for i in got)
    monkeypatch.setenv("OneDrive", os.path.dirname(paths.data_root()))
    paths.reset_for_tests()
    paths.set_data_root(str(tmp_path / "data"))
    got = paths.startup_path_issues()
    ods = [i for i in got if i.code == "onedrive"]
    assert len(ods) == 1 and ods[0].role == "data"                  # 같은 종류는 한 번만
    pr = tmp_path / "data" / "projects" / "p-0"
    pr.mkdir(parents=True)
    assert any(i.code == "onedrive" for i in paths.project_path_issues(str(pr)))
    assert paths.projects_root() == os.path.join(paths.data_root(), "projects")


def test_looks_like_onedrive_and_non_ascii():
    assert paths.looks_like_onedrive(r"C:\Users\u\OneDrive\문서\x")
    assert paths.looks_like_onedrive(r"D:\OneDrive - Contoso\x")
    assert not paths.looks_like_onedrive(r"C:\Users\u\dev\chatcoach")
    assert paths.has_non_ascii("C:\\문서") and not paths.has_non_ascii("C:\\docs")


# ---------------------------------------------------------------------------- 가림 · 로그 정책
def test_redact_chat_key_values():
    s = redact('{"nickname": "시청자1", "content": "합성 \\"인용\\" 문장", "profile": "{\\"a\\":1}", '
               "'heard_text': '말한 내용', memo=메모값, display_name=표시명}")
    for leak in ("시청자1", "합성", "인용", "말한 내용", "메모값", "표시명", '\\"a'):
        assert leak not in s, (leak, s)
    assert s.count("***") == 6
    # 키가 아닌 비슷한 낱말은 그대로
    assert redact("content-length: 120") == "content-length: 120"
    assert redact("nickname_mask=first_char") == "nickname_mask=first_char"
    # 앞선 규칙과의 순서: userIdHash 값은 여전히 <hex>
    assert redact("userIdHash=" + "a" * 40) == "userIdHash=<hex>"


def test_redact_app_chat_record_shape():
    """앱 자체 채팅 기록(messages.jsonl) 모양의 dict repr·JSON 이 실수로 찍혀도 본문·가린 닉네임이 나가지 않는다 (합성 값)."""
    s = redact("{'text': 'SYNTH_BODY', 'nickname_masked': '시**', 'viewer_id': 'v1'} {\"text\": \"SYNTH2\"}")
    for leak in ("SYNTH_BODY", "시**", "SYNTH2"):
        assert leak not in s, (leak, s)
    assert "'viewer_id': 'v1'" in s
    assert redact("context: 3") == "context: 3" and redact("text-align: left") == "text-align: left"


def test_log_rotation_limits(tmp_path):
    path = logging_setup.setup("0.0.0-test")
    try:
        fh = [h for h in logging_setup.LOG.handlers if getattr(h, "_sl_installed", False)
              and isinstance(h, logging.handlers.RotatingFileHandler)][0]
        assert fh.maxBytes == logging_setup.LOG_MAX_BYTES == 1_000_000
        assert fh.backupCount == logging_setup.LOG_BACKUP_COUNT == 3
        assert fh.level == logging.INFO                               # 파일 로그 기본 INFO (S-20)
        logging_setup.LOG.debug("디버그 줄은 파일에 안 남는다")
        fh.maxBytes = 2000                                            # 회전이 실제로 도는지 작게 해서 본다
        for i in range(200):
            logging_setup.LOG.info("회전 시험 줄 %d %s", i, "x" * 40)
        fh.flush()
        names = sorted(os.path.basename(p) for p in logging_setup.log_files())
        assert "chatcoach.log" in names and "chatcoach.log.1" in names
        assert len([n for n in names if n.startswith("chatcoach.log")]) <= 1 + logging_setup.LOG_BACKUP_COUNT
        assert "디버그 줄" not in open(path, encoding="utf-8").read()
    finally:
        _remove_handlers()


def _remove_handlers():
    for h in [h for h in logging_setup.LOG.handlers if getattr(h, "_sl_installed", False)]:
        logging_setup.LOG.removeHandler(h)
        h.close()
    sys.excepthook = sys.__excepthook__


# ---------------------------------------------------------------------------- 흐름 로그에 본문·닉네임 없음
class _FakeChzzk:
    """합성 채팅 API. 첫 요청은 503(재시도 경고 로그가 남게)."""

    def __init__(self, n=6):
        import hashlib
        self.chats = []
        for i in range(n):
            uid = hashlib.sha1(f"fixture-{i}".encode()).hexdigest()
            prof = json.dumps({"userIdHash": uid, "nickname": f"{NICK}{i}", "userRoleCode": "common_user"},
                              ensure_ascii=False)
            self.chats.append({"chatChannelId": "chan0", "content": f"{BODY} {i}", "extras": "{}",
                               "messageStatusType": "NORMAL", "messageTime": 1_700_000_000_000 + i * 1000,
                               "messageTypeCode": 1, "playerMessageTime": (i + 1) * 1000, "profile": prof,
                               "userIdHash": uid})
        self.calls = 0

    def __call__(self, url, timeout):
        self.calls += 1
        if self.calls == 1:
            return 503, "busy"
        cur = int(parse_qs(urlparse(url).query)["playerMessageTime"][0])
        page = [c for c in self.chats if c["playerMessageTime"] > cur][:3]
        nxt = page[-1]["playerMessageTime"] if page else None
        return 200, json.dumps({"code": 200, "message": None, "content": {
            "videoChats": page, "previousVideoChats": [], "nextPlayerMessageTime": nxt}}, ensure_ascii=False)


def test_flow_logs_hold_no_chat_text_or_nickname(tmp_path):
    from app import backup, chat_index, chzzk_chat
    from app import project as project_mod
    log_path = logging_setup.setup("0.0.0-test")
    crashguard.enable(paths.data_root(), version="0.0.0-test")
    try:
        now = datetime(2026, 10, 21, 10, 0, tzinfo=KST)
        proj = project_mod.create(str(tmp_path / "projects"), "테스트 방송", 0, "시험용 목적", "L01", "0.0.1", now,
                                  "11111111-2222-4333-8444-555555555555", bytes(range(32)), machine4="ab23")
        chzzk_chat.collect("0", proj.root, proj.salt(), http=_FakeChzzk(), sleep=lambda s: None,
                           now=lambda: now, rand4=lambda: "t3st")
        assert BODY in open(os.path.join(proj.root, "chat", "messages.jsonl"), encoding="utf-8").read()
        proj = project_mod.open_project(proj.root)
        chat_index.for_project(proj)
        backup.backup_project(proj.root, include_protected=True, now=now)
        backup.daily_backup_if_due(str(tmp_path / "projects"), now=now + timedelta(days=1))
        trash_dir = tmp_path / "trash"
        trash_dir.mkdir()

        def fake_trash(p):
            import shutil
            shutil.move(p, str(trash_dir / f"{len(os.listdir(trash_dir))}-{os.path.basename(p)}"))
        plan = backup.plan_disposal(proj.root)
        backup.execute_disposal(plan, labeler_id="L01", trash=fake_trash, now=now)
        # 예외 경로: 처리되지 않은 예외 훅으로 들어온 문자열 속 경로도 가린다
        try:
            raise OSError(f"열기 실패 {os.path.join(os.path.expanduser('~'), 'x.txt')}")
        except OSError:
            logging_setup.LOG.exception("시험용 예외")
        for h in logging_setup.LOG.handlers:
            h.flush()
        text = open(log_path, encoding="utf-8").read()
        assert "채팅 수집 끝" in text and "백업:" in text and "프로젝트 파기" in text and "채팅 요청 실패" in text
        user = os.path.basename(os.path.expanduser("~"))
        for leak in (NICK, BODY, "fixture-", "\\Users\\" + user + "\\"):
            assert leak not in text, leak
        crash = open(crashguard.crash_log_path(paths.data_root()), encoding="utf-8").read()
        assert NICK not in crash and BODY not in crash
        disp = open(os.path.join(paths.data_root(), "disposal_log.jsonl"), encoding="utf-8").read()
        assert NICK not in disp and BODY not in disp
    finally:
        _remove_handlers()
        crashguard.mark_clean_exit()
        import faulthandler
        faulthandler.disable()


# ---------------------------------------------------------------------------- crash.log · 비정상 종료
def test_crash_log_rotates_and_marker_has_no_paths(tmp_path, monkeypatch):
    d = str(tmp_path / "data")
    os.makedirs(os.path.join(d, "logs"))
    big = crashguard.crash_log_path(d)
    with open(big, "w", encoding="utf-8") as f:
        f.write("old\n" * 100_000)                                       # 약 400KB
    monkeypatch.setattr(sys, "argv", ["main.py", "--updated", r"C:\Users\someone\secret\proj", "--data-dir=X"])
    try:
        marker = crashguard.enable(d, version="9.9.9")
        assert os.path.getsize(big + ".1") > crashguard.CRASH_LOG_MAX_BYTES
        assert os.path.getsize(big) < 1000 and "pid=" in open(big, encoding="utf-8").read()
        m = json.loads(open(marker, encoding="utf-8").read())
        assert m["flags"] == ["--updated"] and m["version"] == "9.9.9"
        assert "someone" not in open(marker, encoding="utf-8").read()
        # 작으면 돌리지 않는다
        os.remove(big + ".1")
        crashguard.enable(d)
        assert not os.path.exists(big + ".1")
    finally:
        crashguard.mark_clean_exit()
        import faulthandler
        faulthandler.disable()


def test_native_crash_is_recorded_and_detected(tmp_path):
    d = str(tmp_path / "data")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from app import crashguard; "
            "crashguard.enable(sys.argv[2], version='0.0.0'); import faulthandler; faulthandler._sigsegv()")
    r = subprocess.run([sys.executable, "-c", code, ROOT, d], capture_output=True, timeout=60,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    assert r.returncode != 0
    text = open(crashguard.crash_log_path(d), encoding="utf-8", errors="replace").read()
    assert "Fatal Python error" in text and "=== 시작" in text
    pids = crashguard.stale_markers(d)
    assert len(pids) == 1
    assert "Fatal Python error" in crashguard.crash_tail(d)
    assert crashguard.clear_stale_markers(d) == 1 and crashguard.stale_markers(d) == []


def test_crash_tail_redacts(tmp_path):
    d = str(tmp_path / "data")
    os.makedirs(os.path.join(d, "logs"))
    with open(crashguard.crash_log_path(d), "w", encoding="utf-8") as f:
        f.write("앞줄\n" * 5000 + r'  File "C:\Users\someone\app\x.py", line 3' + "\n")
    t = crashguard.crash_tail(d, max_bytes=200)
    assert "someone" not in t and "%USERPROFILE%" in t and len(t.encode("utf-8")) <= 200
    assert crashguard.crash_tail(str(tmp_path / "none")) == ""


def test_abnormal_exit_streak(tmp_path):
    d = str(tmp_path / "data")
    assert crashguard.note_start(d, "0.1.1", False) == 0
    assert crashguard.note_start(d, "0.1.1", True) == 1
    assert crashguard.note_start(d, "0.1.1", True) == 2
    assert crashguard.should_suggest_rollback(2, just_updated=True)
    assert not crashguard.should_suggest_rollback(2, just_updated=False)
    assert not crashguard.should_suggest_rollback(1, just_updated=True)
    assert crashguard.note_start(d, "0.1.2", True) == 1                  # 버전이 바뀌면 다시 센다
    assert crashguard.note_start(d, "0.1.2", False) == 0
    with open(os.path.join(d, "run", crashguard.STREAK_FILE), "w") as f:
        f.write("{깨짐")
    assert crashguard.note_start(d, "0.1.2", True) == 1
