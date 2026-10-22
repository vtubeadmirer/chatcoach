"""app/backup.py — 프로젝트 백업 zip · 순환 · 업데이트 직전 백업 · 되살리기 · 보존 기한 · 프로젝트 파기(휴지통).

모두 임시 폴더의 합성 프로젝트로 본다. 실제 채팅·닉네임은 쓰지 않는다(닉네임 '시청자N', video_no 0).
휴지통은 가짜 함수(임시 폴더로 옮김)로 바꿔 끼운다. 진짜 Windows 휴지통 시험은 CHATCOACH_TEST_REAL_TRASH=1 일 때만.
"""
import hashlib
import json
import os
import shutil
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from app import backup, paths
from app import project as project_mod

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 10, 0, 0, tzinfo=KST)
PID = "11111111-2222-4333-8444-555555555555"
PID2 = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SALT = bytes(range(32))
FP = "ab" * 32


@pytest.fixture(autouse=True)
def _own_backup_root(tmp_path, monkeypatch):
    """conftest 의 %LOCALAPPDATA% 는 세션 하나에 하나라 백업이 시험 사이에 쌓인다 → 시험마다 따로."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    paths.reset_for_tests()
    paths.set_data_root(str(tmp_path / "data"))
    yield


class FakeTrash:
    """휴지통 대신: 임시 '휴지통' 폴더로 옮기고 경로를 적는다."""

    def __init__(self, where, fail_on=()):
        self.where = str(where)
        os.makedirs(self.where, exist_ok=True)
        self.sent: list[str] = []
        self.fail_on = tuple(fail_on)

    def __call__(self, p):
        if any(s in p for s in self.fail_on):
            raise backup.TrashError("shell_0x78")
        dest = os.path.join(self.where, f"{len(self.sent):03d}-{os.path.basename(p)}")
        shutil.move(p, dest)
        self.sent.append(p)


def _write(p, data: bytes | str = b"x"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data.encode("utf-8") if isinstance(data, str) else data)


def make_project(root, pid=PID, title="테스트 방송", open_lock=False, media_fp=None):
    proj = project_mod.create(str(root), title, 0, "시험용 목적", "L01", "0.0.1", NOW, pid, SALT, machine4="ab23")
    r = proj.root if hasattr(proj, "root") else proj.paths.project_json[:-len("project.json") - 1]
    _write(os.path.join(r, "labels", "S20261021-0001.ab23.jsonl"), '{"kind":"segment","note":"합성"}\n')
    _write(os.path.join(r, "samples", "SS-1.json"), "{}")
    _write(os.path.join(r, "locks", "S20261021-0001.json"), "{}")
    if open_lock:
        _write(os.path.join(r, "locks", "open.lock"), "1")
    _write(os.path.join(r, "relabel", "RP-1.json"), "{}")
    _write(os.path.join(r, "chat", "messages.jsonl"), '{"chat_id":"c1","text":"합성 본문"}\n')
    _write(os.path.join(r, "chat", "fetch-F20261021-t3st.json"), "{}")
    _write(os.path.join(r, "chat", "protected", "identities.jsonl"), '{"viewer_id":"vw_1","nickname":"시청자1"}\n')
    _write(os.path.join(r, "derived", "asr.json"), "{}")
    _write(os.path.join(r, "derived", "vad.json"), "{}")
    _write(os.path.join(r, "origin", "OT-20261021-abcd.json"), "{}")
    _write(os.path.join(r, "labels", ".x.jsonl.123.abcd1234.tmp"), "tmp")
    if media_fp:
        pj = os.path.join(r, "project.json")
        obj = json.loads(open(pj, encoding="utf-8").read())
        obj["media"]["current"] = {"path_abs": "C:\\v.mp4", "path_rel": None, "size": 1, "mtime_ns": 1,
                                   "partial_fp": media_fp, "full_sha256": None, "ntfs_file_id": None,
                                   "volume_serial": None, "probe": {}}
        _write(pj, json.dumps(obj, ensure_ascii=False))
    return r


def names_in(zpath):
    with zipfile.ZipFile(zpath) as z:
        return sorted(z.namelist())


def rels_in(zpath, folder):
    return sorted(n[len(folder) + 1:] for n in names_in(zpath) if n != "manifest.json")


# ---------------------------------------------------------------------------- 보호 구역
def test_in_protected_zone_rules():
    z = list(backup.DEFAULT_PROTECTED_ZONE)
    assert backup.in_protected_zone("chat/protected/identities.jsonl", z)
    assert backup.in_protected_zone("chat\\protected\\salt.json", z)
    assert backup.in_protected_zone("CHAT/Protected/x", z)                  # 대소문자 무시
    assert backup.in_protected_zone("derived/asr.json", z)
    assert backup.in_protected_zone("derived/asr_v2/part.json", z)         # 폴더가 맞으면 그 아래 전부
    assert backup.in_protected_zone("derived/ocr.jsonl", z)
    assert backup.in_protected_zone("origin/OT-1.json", z)
    assert backup.in_protected_zone("origin/", z)
    assert backup.in_protected_zone("scenarios/SC-20261022-abcd.jsonl", z)   # 가상 채팅 시나리오(H-1, D-05)
    assert not backup.in_protected_zone("scenario.json", z)
    assert not backup.in_protected_zone("derived/vad.json", z)
    assert not backup.in_protected_zone("chat/messages.jsonl", z)
    assert not backup.in_protected_zone("labels/a.jsonl", z)
    assert not backup.in_protected_zone("chat/protectedness.txt", z)


def test_protected_patterns_union_with_defaults():
    assert set(backup.DEFAULT_PROTECTED_ZONE) <= set(backup.protected_patterns({"protected_zone": []}))
    assert set(backup.DEFAULT_PROTECTED_ZONE) <= set(backup.protected_patterns(None))
    got = backup.protected_patterns({"protected_zone": ["labels/secret*", 3, ""]})
    assert "labels/secret*" in got and 3 not in got and "" not in got
    assert tuple(project_mod.DEFAULT_PROTECTED_ZONE) == backup.DEFAULT_PROTECTED_ZONE


# ---------------------------------------------------------------------------- 백업 내용
def test_daily_backup_contents_and_location(tmp_path):
    r = make_project(tmp_path / "projects", open_lock=True)
    folder = os.path.basename(r)
    res = backup.backup_project(r, kind="daily", now=NOW)
    assert os.path.dirname(res.path) == os.path.join(paths.backup_root(), PID)
    assert paths.backup_root().startswith(os.environ["LOCALAPPDATA"])     # %LOCALAPPDATA%\ChatCoach\backups (S-39)
    assert os.path.basename(res.path) == "daily-20261021-100000.zip"
    rels = rels_in(res.path, folder)
    assert "project.json" in rels and "labels/S20261021-0001.ab23.jsonl" in rels
    assert "samples/SS-1.json" in rels and "locks/S20261021-0001.json" in rels and "relabel/RP-1.json" in rels
    assert any(x.startswith("guide/") for x in rels)
    for bad in ("locks/open.lock", "chat/messages.jsonl", "chat/protected/identities.jsonl", "chat/protected/salt.json",
                "derived/asr.json", "derived/vad.json", "origin/OT-20261021-abcd.json"):
        assert bad not in rels, bad
    assert not any(x.endswith(".tmp") for x in rels)
    man = backup.read_manifest(res.path)
    assert man["project_id"] == PID and man["kind"] == "daily" and not man["include_chat"]
    assert {f["path"] for f in man["files"]} == {f"{folder}/{x}" for x in rels}
    assert backup.verify_backup(res.path) == []


def test_manual_backup_includes_chat_not_protected(tmp_path):
    r = make_project(tmp_path / "projects")
    folder = os.path.basename(r)
    res = backup.backup_project(r, now=NOW)
    rels = rels_in(res.path, folder)
    assert "chat/messages.jsonl" in rels and "chat/fetch-F20261021-t3st.json" in rels
    assert not any(x.startswith(("chat/protected/", "derived/", "origin/")) for x in rels)
    assert os.path.basename(res.path) == "manual-20261021-100000-chat.zip"
    # 같은 초에 한 번 더 → 이름 뒤에 -2
    res2 = backup.backup_project(r, now=NOW)
    assert os.path.basename(res2.path) == "manual-20261021-100000-chat-2.zip"


def test_protected_backup_only_on_request(tmp_path):
    r = make_project(tmp_path / "projects")
    folder = os.path.basename(r)
    res = backup.backup_project(r, include_protected=True, now=NOW)
    rels = rels_in(res.path, folder)
    assert "chat/protected/identities.jsonl" in rels and "derived/asr.json" in rels
    assert "origin/OT-20261021-abcd.json" in rels
    assert "derived/vad.json" not in rels                     # 보호 밖 파생은 백업하지 않는다(03 16-2)
    assert res.path.endswith("-chat-protected.zip") and res.manifest["include_protected"]
    assert any("보호 구역" in w for w in res.warnings)


def test_empty_protected_zone_in_project_json_still_protects_defaults(tmp_path):
    r = make_project(tmp_path / "projects")
    pj = os.path.join(r, "project.json")
    obj = json.loads(open(pj, encoding="utf-8").read())
    obj["protected_zone"] = []
    _write(pj, json.dumps(obj, ensure_ascii=False))
    res = backup.backup_project(r, now=NOW)
    assert not any("protected" in n or "/origin/" in n or "/derived/" in n for n in names_in(res.path))


def test_links_are_not_followed(tmp_path):
    r = make_project(tmp_path / "projects")
    outside = tmp_path / "outside"
    _write(str(outside / "secret.txt"), "밖의 파일")
    link = os.path.join(r, "labels", "jx")
    rc = os.system(f'mklink /J "{link}" "{outside}" >NUL 2>&1') if os.name == "nt" else 1
    if rc != 0 or not os.path.exists(link):
        pytest.skip("정션을 만들 수 없음")
    res = backup.backup_project(r, now=NOW)
    assert not any("secret.txt" in n for n in names_in(res.path))


# ---------------------------------------------------------------------------- 위치 검사
def test_check_backup_dest(tmp_path, monkeypatch):
    r = make_project(tmp_path / "projects")
    with pytest.raises(backup.BackupLocationError, match="프로젝트 폴더"):
        backup.check_backup_dest(os.path.join(r, "bk"), r)
    with pytest.raises(backup.BackupLocationError, match="데이터 폴더"):
        backup.check_backup_dest(os.path.join(paths.data_root(), "mybk"), r)
    with pytest.raises(backup.BackupLocationError, match="프로그램 폴더|저장소"):
        backup.check_backup_dest(os.path.join(paths.install_root(), "bk"), r)
    gitdir = tmp_path / "repo"
    (gitdir / ".git").mkdir(parents=True)
    with pytest.raises(backup.BackupLocationError, match="저장소"):
        backup.check_backup_dest(str(gitdir / "sub"), r)
    assert backup.check_backup_dest(os.path.join(paths.backup_root(), PID), r) == []
    assert backup.check_backup_dest(str(tmp_path / "usb-copy"), r) == []
    od = tmp_path / "od"
    od.mkdir()
    monkeypatch.setenv("OneDrive", str(od))
    assert backup.check_backup_dest(str(od / "bk"), r)                     # 경고
    with pytest.raises(backup.BackupLocationError, match="OneDrive"):
        backup.check_backup_dest(str(od / "bk"), r, include_protected=True)
    with pytest.raises(backup.BackupLocationError):
        backup.check_backup_dest(r"\\server\share\bk", r, include_protected=True)
    with pytest.raises(backup.BackupLocationError):
        backup.backup_project(r, out_dir=os.path.join(r, "inside"), now=NOW)


def test_backup_to_chosen_folder(tmp_path):
    r = make_project(tmp_path / "projects")
    res = backup.backup_project(r, out_dir=str(tmp_path / "mine"), now=NOW)
    assert os.path.dirname(res.path) == str(tmp_path / "mine")


# ---------------------------------------------------------------------------- 순환 · 일 1회
def test_daily_rotation_keeps_14(tmp_path):
    r = make_project(tmp_path / "projects")
    trash = FakeTrash(tmp_path / "trash")
    for i in range(16):
        backup.backup_project(r, kind="daily", now=NOW + timedelta(days=i), trash=trash)
    left = backup.list_backups(PID)
    assert len([b for b in left if b.kind == "daily"]) == backup.KEEP_DAILY
    assert len(trash.sent) == 2
    assert [os.path.basename(p) for p in trash.sent] == ["daily-20261021-100000.zip", "daily-20261022-100000.zip"]
    # 수동 백업은 순환에 들지 않는다
    backup.backup_project(r, kind="manual", now=NOW)
    backup.backup_project(r, kind="daily", now=NOW + timedelta(days=30), trash=trash)
    assert any(b.kind == "manual" for b in backup.list_backups(PID))


def test_rotation_trash_failure_is_warning_not_delete(tmp_path):
    r = make_project(tmp_path / "projects")
    ok = FakeTrash(tmp_path / "trash")
    for i in range(14):
        backup.backup_project(r, kind="daily", now=NOW + timedelta(days=i), trash=ok)
    bad = FakeTrash(tmp_path / "trash2", fail_on=("daily-",))
    res = backup.backup_project(r, kind="daily", now=NOW + timedelta(days=20), trash=bad)
    assert any("휴지통" in w for w in res.warnings)
    assert len(backup.list_backups(PID)) == 15                              # 지우지 않고 남김


def test_daily_backup_if_due_all_projects_and_weekly_chat(tmp_path):
    pr = tmp_path / "projects"
    make_project(pr, PID, "방송 하나")
    make_project(pr, PID2, "방송 둘")
    trash = FakeTrash(tmp_path / "trash")
    rep = backup.daily_backup_if_due(str(pr), now=NOW, trash=trash)
    assert len(rep.done) == 2 and rep.skipped == 0 and not rep.failed
    assert all(b.manifest["include_chat"] for b in rep.done)                # 처음엔 채팅 포함
    rep = backup.daily_backup_if_due(str(pr), now=NOW + timedelta(hours=3), trash=trash)
    assert not rep.done and rep.skipped == 2                                # 같은 날은 건너뜀
    rep = backup.daily_backup_if_due(str(pr), now=NOW + timedelta(days=1), trash=trash)
    assert len(rep.done) == 2 and not any(b.manifest["include_chat"] for b in rep.done)
    rep = backup.daily_backup_if_due(str(pr), now=NOW + timedelta(days=7), trash=trash)
    assert all(b.manifest["include_chat"] for b in rep.done)                # 주 1회 채팅


def test_daily_backup_default_projects_root_and_broken_project(tmp_path):
    pr = paths.projects_root()
    make_project(pr, PID)
    os.makedirs(os.path.join(pr, "broken"))
    _write(os.path.join(pr, "broken", "project.json"), "{깨짐")
    rep = backup.daily_backup_if_due(now=NOW)
    assert len(rep.done) == 1 and rep.failed == [("?", "BackupError")]


def test_copy_session_file(tmp_path):
    r = make_project(tmp_path / "projects")
    src = os.path.join(r, "labels", "S20261021-0001.ab23.jsonl")
    dest = backup.copy_session_file(r, src)
    assert dest == os.path.join(paths.backup_root(), PID, "sessions", "S20261021-0001.ab23.jsonl")
    assert open(dest, "rb").read() == open(src, "rb").read()
    with pytest.raises(backup.BackupError):
        backup.copy_session_file(r, os.path.join(r, "chat", "messages.jsonl"))


# ---------------------------------------------------------------------------- 업데이트 직전
def test_pre_update_backup(tmp_path):
    r1 = make_project(tmp_path / "projects", PID)
    r2 = make_project(tmp_path / "elsewhere", PID2)
    _write(os.path.join(paths.data_root(), "settings.ini"), "[a]\nb=1\n")
    _write(os.path.join(paths.data_root(), "labelers.json"), "{}")
    trash = FakeTrash(tmp_path / "trash")
    z = backup.pre_update_backup([r1, r2, r1], "0.1.0", "0.1.1", now=NOW, trash=trash)
    assert os.path.dirname(z) == os.path.join(paths.backup_root(), "pre-update")
    assert os.path.basename(z) == "pre-update-0.1.0-to-0.1.1-20261021100000.zip"
    n = names_in(z)
    assert "data/settings.ini" in n and "data/labelers.json" in n
    assert f"projects/{PID}/labels/S20261021-0001.ab23.jsonl" in n and f"projects/{PID2}/project.json" in n
    assert f"projects/{PID}/relabel/RP-1.json" in n
    assert not any("/chat/" in x or "/derived/" in x or "/origin/" in x or "/guide/" in x or "open.lock" in x for x in n)
    assert backup.read_manifest(z)["projects"] == [PID, PID2]
    assert backup.verify_backup(z) == []
    for i in range(1, 7):
        backup.pre_update_backup([r1], "0.1.0", f"0.1.{i + 1}", now=NOW + timedelta(minutes=i), trash=trash)
    left = [x for x in os.listdir(os.path.dirname(z)) if x.endswith(".zip")]
    assert len(left) == backup.KEEP_PRE_UPDATE and len(trash.sent) == 2


# ---------------------------------------------------------------------------- 검사 · 되살리기
def test_restore_roundtrip_and_no_overwrite(tmp_path):
    r = make_project(tmp_path / "projects")
    folder = os.path.basename(r)
    res = backup.backup_project(r, now=NOW)
    out = tmp_path / "restore"
    rr = backup.restore_backup(res.path, str(out), now=NOW)
    assert rr.path == str(out / folder)
    for rel in ("project.json", "labels/S20261021-0001.ab23.jsonl", "chat/messages.jsonl"):
        a = os.path.join(r, *rel.split("/"))
        b = os.path.join(rr.path, *rel.split("/"))
        assert open(a, "rb").read() == open(b, "rb").read()
    assert os.path.isdir(os.path.join(rr.path, "chat", "protected"))
    assert not os.path.exists(os.path.join(rr.path, "chat", "protected", "identities.jsonl"))
    # 두 번째는 새 이름 + 경고, 원래 폴더는 그대로
    rr2 = backup.restore_backup(res.path, str(out), now=NOW)
    assert rr2.path.endswith("-restored-20261021-100000") and rr2.warnings
    assert not any(n.startswith(".restore-") for n in os.listdir(out))


def test_verify_detects_tamper_and_unsafe_names(tmp_path):
    r = make_project(tmp_path / "projects")
    folder = os.path.basename(r)
    res = backup.backup_project(r, kind="daily", now=NOW)
    # 내용 바꿔치기
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(res.path) as zi, zipfile.ZipFile(bad, "w") as zo:
        for info in zi.infolist():
            data = zi.read(info)
            if info.filename.endswith("SS-1.json"):
                data = b'{"changed":1}'
            zo.writestr(info, data)
    assert any("해시" in p for p in backup.verify_backup(str(bad)))
    with pytest.raises(backup.BackupError):
        backup.restore_backup(str(bad), str(tmp_path / "out"))
    # 경로 탈출 이름
    evil = tmp_path / "evil.zip"
    man = {"schema": backup.BACKUP_SCHEMA, "kind": "manual", "folder": folder,
           "files": [{"path": f"{folder}/../../x.txt", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}]}
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr(f"{folder}/../../x.txt", b"x")
        z.writestr("manifest.json", json.dumps(man))
    assert any("이름" in p for p in backup.verify_backup(str(evil)))
    with pytest.raises(backup.BackupError):
        backup.restore_backup(str(evil), str(tmp_path / "out2"))
    assert not (tmp_path / "x.txt").exists()
    # manifest 없음
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("a.txt", "a")
    assert backup.verify_backup(str(plain))


# ---------------------------------------------------------------------------- 보존 기한
def test_retention_state():
    p = {"retention_until": "2027-10-21"}
    assert backup.retention_state(p, date(2026, 10, 21)).status == "ok"
    s = backup.retention_state(p, date(2027, 10, 1))
    assert s.status == "soon" and s.days_left == 20 and "20일" in backup.retention_message(s)
    s = backup.retention_state(p, date(2027, 10, 22))
    assert s.status == "expired" and s.days_left == -1 and "파기" in backup.retention_message(s)
    assert backup.retention_state({}, date(2026, 1, 1)).status == "unknown"
    assert backup.retention_state({"retention_until": "내년"}, date(2026, 1, 1)).status == "unknown"
    assert backup.retention_message(backup.retention_state(p, date(2026, 10, 21))) is None


def test_new_project_retention_is_12_months(tmp_path):
    r = make_project(tmp_path / "projects")
    st = backup.retention_state(backup.read_project_json(r), NOW.date())
    assert st.until == date(2027, 10, 21) and st.status == "ok"


# ---------------------------------------------------------------------------- 파기
def _aux_files(pid=PID, fp=FP):
    from app import chat_index
    d = paths.data_root()
    cache = chat_index.cache_path_for(pid, d)
    _write(cache, b"IDX")
    _write(os.path.join(d, "cache", pid, "index.sqlite"), b"S")
    _write(os.path.join(d, "cache", fp[:16], "waveform.json"), b"{}")
    _write(os.path.join(d, "ai", pid, "seal.jsonl"), b"{}\n")
    _write(os.path.join(d, "reports", pid, "R-20261021-abcd", "report.json"), b"{}")
    _write(os.path.join(d, "reports", PID2, "R-1", "report.json"), b"{}")      # 다른 프로젝트 것은 남아야
    _write(os.path.join(d, "cache", PID2, "chat_index.bin"), b"OTHER")
    return cache


def test_plan_disposal_targets_and_guards(tmp_path):
    r = make_project(tmp_path / "projects", media_fp=FP)
    _aux_files()
    backup.backup_project(r, now=NOW)
    plan = backup.plan_disposal(r)
    kinds = {t.kind for t in plan.targets}
    assert kinds == {"project", "backups", "cache", "media_cache", "ai", "reports"}
    assert plan.project_id == PID and not plan.keep_labels
    assert all(PID2 not in t.path for t in plan.targets)
    assert any("휴지통" in w for w in plan.warnings) and any("다른 PC" in w for w in plan.warnings)
    assert os.path.exists(r)                                               # 계획은 아무것도 지우지 않는다
    # 열려 있는 프로젝트
    _write(os.path.join(r, "locks", "open.lock"), "1")
    with pytest.raises(backup.ProjectBusyError):
        backup.plan_disposal(r)
    assert backup.plan_disposal(r, ignore_open_lock=True).targets
    # 프로젝트가 아닌 폴더 · 데이터 폴더를 품은 폴더
    with pytest.raises(backup.BackupError):
        backup.plan_disposal(str(tmp_path))
    _write(os.path.join(paths.data_root(), "..", "project.json"),
           json.dumps({"project_id": PID2}))
    with pytest.raises(backup.DisposalError, match="프로젝트 폴더가 아닙니다"):
        backup.plan_disposal(os.path.dirname(paths.data_root()))


def test_execute_disposal_all(tmp_path):
    r = make_project(tmp_path / "projects", media_fp=FP)
    cache = _aux_files()
    backup.backup_project(r, now=NOW)
    backup.copy_session_file(r, os.path.join(r, "labels", "S20261021-0001.ab23.jsonl"))
    trash = FakeTrash(tmp_path / "trash")
    plan = backup.plan_disposal(r)
    res = backup.execute_disposal(plan, labeler_id="L01", trash=trash, now=NOW)
    assert res.ok and res.cache_index_dropped
    assert not os.path.exists(cache)                                        # chat_index.drop_cache
    assert not os.path.exists(r)
    assert not os.path.exists(os.path.join(paths.backup_root(), PID))
    d = paths.data_root()
    for gone in (os.path.join(d, "cache", PID), os.path.join(d, "cache", FP[:16]), os.path.join(d, "ai", PID),
                 os.path.join(d, "reports", PID)):
        assert not os.path.exists(gone), gone
    assert os.path.exists(os.path.join(d, "reports", PID2)) and os.path.exists(os.path.join(d, "cache", PID2))
    assert trash.sent[-1] == os.path.abspath(r)                             # 프로젝트는 마지막
    # 휴지통 폴더에 실제로 옮겨졌다 (영구 삭제가 아님)
    assert len(os.listdir(trash.where)) == len(trash.sent)
    log = backup.disposal_log()
    assert len(log) == 1
    rec = log[0]
    assert rec["project_id"] == PID and rec["labeler_id"] == "L01" and rec["mode"] == "all"
    assert rec["method"] == "trash" and rec["at"] == "2026-10-21T10:00:00+09:00"
    assert {t["kind"] for t in rec["trashed"]} >= {"project", "backups", "ai"}
    raw = open(res.log_path, encoding="utf-8").read()
    user = os.path.basename(os.path.expanduser("~"))
    assert f"\\Users\\{user}\\" not in raw and "%USERPROFILE%" in raw    # 사용자 이름은 가린다
    assert "시청자" not in raw and "합성 본문" not in raw


def test_execute_disposal_keep_labels(tmp_path):
    r = make_project(tmp_path / "projects")
    _aux_files()
    trash = FakeTrash(tmp_path / "trash")
    plan = backup.plan_disposal(r, keep_labels=True)
    kinds = {t.kind for t in plan.targets}
    assert {"project_chat", "project_derived", "project_protected", "backups", "cache", "ai", "reports"} >= kinds
    assert "project" not in kinds
    res = backup.execute_disposal(plan, labeler_id="L01", trash=trash, now=NOW)
    assert res.ok
    for kept in ("project.json", "labels", "samples", "locks", "relabel", "guide"):
        assert os.path.exists(os.path.join(r, kept)), kept
    for gone in ("chat", "derived", "origin"):
        assert not os.path.exists(os.path.join(r, gone)), gone
    assert backup.disposal_log()[0]["mode"] == "keep_labels"


def test_keep_labels_sends_nested_custom_protected_zone(tmp_path):
    """'라벨만 남기기' 에서도 남기는 폴더 안을 가리키는 사용자 보호 구역(samples/identities/ · labels/raw*)은 보낸다."""
    r = make_project(tmp_path / "projects")
    pj = os.path.join(r, "project.json")
    with open(pj, encoding="utf-8") as f:
        obj = json.load(f)
    obj["protected_zone"] = list(obj.get("protected_zone") or []) + ["samples/identities/", "labels/raw*"]
    with open(pj, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False)
    _write(os.path.join(r, "samples", "identities", "a", "synthetic.json"), "{}")
    _write(os.path.join(r, "samples", "identities", "b.json"), "{}")
    _write(os.path.join(r, "samples", "keep.json"), "{}")
    _write(os.path.join(r, "labels", "raw_synth.jsonl"), "{}\n")
    plan = backup.plan_disposal(r, keep_labels=True)
    prot = sorted(os.path.relpath(t.path, r) for t in plan.targets if t.kind == "project_protected")
    assert os.path.join("samples", "identities") in prot and os.path.join("labels", "raw_synth.jsonl") in prot
    assert not any(p.startswith(os.path.join("samples", "identities") + os.sep) for p in prot)   # 위 폴더 하나만
    res = backup.execute_disposal(plan, labeler_id="L01", trash=FakeTrash(tmp_path / "trash"), now=NOW)
    assert res.ok
    assert not os.path.exists(os.path.join(r, "samples", "identities"))
    assert not os.path.exists(os.path.join(r, "labels", "raw_synth.jsonl"))
    assert os.path.exists(os.path.join(r, "samples", "keep.json"))
    assert os.path.exists(os.path.join(r, "labels", "S20261021-0001.ab23.jsonl"))


def test_execute_disposal_partial_failure_continues(tmp_path):
    r = make_project(tmp_path / "projects")
    _aux_files()
    trash = FakeTrash(tmp_path / "trash", fail_on=(os.sep + "ai" + os.sep,))
    plan = backup.plan_disposal(r)
    res = backup.execute_disposal(plan, labeler_id="L01", trash=trash, now=NOW)
    assert not res.ok and [t.kind for t, _ in res.failed] == ["ai"] and res.failed[0][1] == "shell_0x78"
    assert not os.path.exists(r)
    assert os.path.exists(os.path.join(paths.data_root(), "ai", PID))       # 실패한 것은 남는다(영구 삭제 안 함)
    assert backup.disposal_log()[0]["failed"][0]["reason"] == "shell_0x78"
    with pytest.raises(ValueError):
        backup.execute_disposal(plan, labeler_id="홍길동")


def test_target_without_trash_is_left_alone(tmp_path, monkeypatch):
    r = make_project(tmp_path / "projects")
    _aux_files()
    plan = backup.plan_disposal(r)
    for t in plan.targets:
        if t.kind == "ai":
            t.trash_problem = "network"
    trash = FakeTrash(tmp_path / "trash")
    res = backup.execute_disposal(plan, labeler_id="L01", trash=trash, now=NOW)
    assert [(t.kind, why) for t, why in res.failed] == [("ai", "network")]
    assert os.path.exists(os.path.join(paths.data_root(), "ai", PID))


@pytest.mark.skipif(os.name != "nt", reason="Windows 휴지통")
def test_can_trash_reasons(tmp_path):
    assert backup.can_trash(r"\\server\share\x") == "network"
    assert backup.can_trash(str(tmp_path)) is None
    assert backup.can_trash(str(tmp_path / ("a" * 250))) == "long_path"
    with pytest.raises(backup.TrashError) as ei:
        backup.send_to_trash(r"\\server\share\x")
    assert ei.value.reason == "network"


@pytest.mark.skipif(os.name != "nt", reason="Windows 휴지통")
def test_can_trash_refuses_where_shell_would_delete_for_good(tmp_path, monkeypatch):
    """휴지통이 못 받는 곳에서 셸은 묻지 않고 영구 삭제한다. 그런 곳은 미리 막고, 대상 파일은 그대로 둔다.
    subst·가상 드라이브(볼륨 GUID 없음) · 정책·볼륨 설정으로 휴지통 꺼짐 · 대상이 휴지통 최대 크기보다 큼."""
    d = tmp_path / "합성 대상"
    _write(str(d / "synthetic.bin"), "x" * 3000)
    real = backup._reg_dword
    assert backup._volume_guid(str(d))                                  # 보통 고정 드라이브는 볼륨 GUID 가 있다
    monkeypatch.setattr(backup, "_volume_guid", lambda a: None)
    assert backup.can_trash(str(d)) == "no_volume"
    with pytest.raises(backup.TrashError) as ei:
        backup.send_to_trash(str(d))
    assert ei.value.reason == "no_volume" and (d / "synthetic.bin").exists()
    monkeypatch.setattr(backup, "_volume_guid", lambda a: "{00000000-0000-0000-0000-000000000000}")

    def reg(values):
        return lambda hive, key, name: values.get(name)
    monkeypatch.setattr(backup, "_reg_dword", reg({"NoRecycleFiles": 1}))
    assert backup.can_trash(str(d)) == "bin_disabled"
    monkeypatch.setattr(backup, "_reg_dword", reg({"NukeOnDelete": 1, "MaxCapacity": 1000}))
    assert backup.can_trash(str(d)) == "bin_disabled"
    monkeypatch.setattr(backup, "_reg_dword", reg({"NukeOnDelete": 0, "MaxCapacity": 0}))
    assert backup.can_trash(str(d)) == "bin_too_small"                  # 3000 B > 0 MB
    monkeypatch.setattr(backup, "_reg_dword", reg({"NukeOnDelete": 0, "MaxCapacity": 1}))
    assert backup.can_trash(str(d)) is None                             # 3000 B ≤ 1 MB
    monkeypatch.setattr(backup, "_reg_dword", reg({}))
    assert backup.can_trash(str(d)) is None                             # 볼륨 설정이 없으면 크기는 보지 않는다
    monkeypatch.setattr(backup, "_reg_dword", real)
    # 파기 계획은 이런 대상에 trash_problem 을 달고, 실행은 그 대상을 건드리지 않는다(test_execute_skips_untrashable 류)
    monkeypatch.setattr(backup, "_recycle_bin_problem", lambda a: "no_volume")
    assert backup.can_trash(str(d)) == "no_volume"
    assert (d / "synthetic.bin").exists()


def test_send_to_trash_asks_before_permanent_delete():
    """휴지통이 못 받으면 조용히 지우지 않고 묻도록 FOF_WANTNUKEWARNING(0x4000)을 준다 (코드 확인)."""
    import inspect
    src = inspect.getsource(backup.send_to_trash)
    assert "FOF_WANTNUKEWARNING = 0x4000" in src and "| FOF_WANTNUKEWARNING" in src


def test_gui_smoke_recycle_uses_app_checks(tmp_path, monkeypatch):
    """스크린샷 정리도 앱과 같은 검사: 하나라도 휴지통이 못 받으면 아무것도 보내지 않고 False."""
    import sys
    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import gui_smoke as gs
    a, b = tmp_path / "c6_a.png", tmp_path / "c6_b.png"
    a.write_bytes(b"\x89PNG synthetic")
    b.write_bytes(b"\x89PNG synthetic")
    sent = []
    monkeypatch.setattr(backup, "can_trash", lambda p: "no_volume" if p == str(b) else None)
    monkeypatch.setattr(backup, "send_to_trash", lambda p: sent.append(p))
    if os.name == "nt":
        assert gs.send_to_recycle_bin([str(a), str(b)]) is False and sent == []
        assert a.exists() and b.exists()
        monkeypatch.setattr(backup, "can_trash", lambda p: None)
        assert gs.send_to_recycle_bin([str(a), str(b)]) is True and sent == [str(a), str(b)]
    else:
        assert gs.send_to_recycle_bin([str(a)]) is False


@pytest.mark.skipif(os.name != "nt" or os.environ.get("CHATCOACH_TEST_REAL_TRASH") != "1",
                    reason="진짜 휴지통 시험은 CHATCOACH_TEST_REAL_TRASH=1 일 때만 (합성 파일 하나가 휴지통에 남는다)")
def test_send_to_trash_real(tmp_path):
    d = tmp_path / "chatcoach-trash-test"
    _write(str(d / "synthetic.txt"), "합성 시험 파일")
    backup.send_to_trash(str(d))
    assert not d.exists()
