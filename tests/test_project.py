"""app.project · app.core.validate 테스트. 합성 데이터만 쓴다 (실제 방송·채팅 없음, video_no 0)."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app import project
from app.core import ids
from app.core.validate import (PROJECT_SPEC, StrictJsonError, canonical_json, check, load_json_strict,
                               validate_project)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 20, 21, 0, 0, tzinfo=KST)
SALT = bytes(range(32))
PID = "12345678-1234-4abc-8def-1234567890ab"


def _guide():
    return project.GuideSource("0.1", b"# guide\n", {"reaction": {"time_window_sec": 20}})


def _make(tmp_path, title="합성 방송 제목", **kw):
    kw.setdefault("guide", _guide())
    kw.setdefault("machine4", "m3fa")
    return project.create(str(tmp_path / "projects"), title, 0, "시험용 목적", "L01", "0.0.1", NOW, PID, SALT, **kw)


@pytest.fixture(autouse=True)
def _no_onedrive(monkeypatch):
    for v in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        monkeypatch.delenv(v, raising=False)


# --- 엄격 JSON -------------------------------------------------------------

def test_strict_json_rules():
    assert load_json_strict(b'\xef\xbb\xbf{"a": 1}', 100) == {"a": 1}
    for bad in (b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}', b'[1]', b'"x"',
                b'{"a": 1, "a": 2}', b'{"a": ', b'\xff\xfe{}'):
        with pytest.raises(StrictJsonError):
            load_json_strict(bad, 100)
    with pytest.raises(StrictJsonError):
        load_json_strict(b'{"a": 1}', 3)


def test_canonical_json():
    assert canonical_json({"b": 3.0, "a": [1.5, "한"]}) == '{"a":[1.5,"한"],"b":3}'.encode("utf-8")
    with pytest.raises(ValueError):
        canonical_json({"a": float("nan")})


def test_spec_matches_schema_file():
    """PROJECT_SPEC 이 schemas/project.schema.json 과 같은지 (설명 키만 뺀 것)."""
    drop = {"description", "title", "$schema", "$id", "format", "default"}

    def strip(o):
        if isinstance(o, dict):   # properties·$defs 의 키는 필드 이름이라 빼지 않는다 (broadcast.title 등)
            return {k: ({n: strip(s) for n, s in v.items()} if k in ("properties", "$defs") else strip(v))
                    for k, v in o.items() if k not in drop}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o
    with open(os.path.join(ROOT, "schemas", "project.schema.json"), "rb") as f:
        schema = load_json_strict(f.read(), 1 << 20)
    assert strip(schema) == PROJECT_SPEC


def test_check_type_strictness():
    spec = {"type": "integer", "minimum": 1}
    assert check(1, spec, {}) == []
    assert check(True, spec, {}) and check(1.0, spec, {}) and check(0, spec, {})
    # '$' 는 끝 줄바꿈을 받지 않는다
    assert check("ab\n", {"type": "string", "pattern": "^[a-z]+$"}, {})


# --- slug ------------------------------------------------------------------

@pytest.mark.parametrize("title,expect", [
    ('a\\b/c:d*e?f"g<h>i|j', "a_b_c_d_e_f_g_h_i_j-0"),
    ("  공백   여러개 ", "_공백_여러개_-0"),
    ("", "방송-0"),
    ("...", "방송-0"),
    ("끝점.", "끝점-0"),
    ("탭\t줄\n바꿈\x00널", "탭_줄_바꿈_널-0"),
])
def test_slugify_forbidden(title, expect):
    assert project.slugify(title, 0) == expect


def test_slugify_length_and_pattern():
    s = project.slugify("가" * 100, "123456789012")
    assert s == "가" * 30 + "-123456789012"
    assert check(s, PROJECT_SPEC["properties"]["slug"], PROJECT_SPEC) == []
    with pytest.raises(ValueError):
        project.slugify("x", "12a")
    with pytest.raises(ValueError):
        project.slugify("x", "1234567890123")


# --- 만들기·열기 -------------------------------------------------------------

def test_create_and_open(tmp_path):
    p = _make(tmp_path)
    assert not p.read_only, p.problems
    assert os.path.basename(p.root) == "합성_방송_제목-0"
    for d in ("chat/protected", "labels", "samples", "locks", "derived", "guide"):
        assert os.path.isdir(os.path.join(p.root, d))
    d = p.data
    assert d["rev"] == 1 and d["project_id"] == PID and d["slug"] == "합성_방송_제목-0"
    assert d["created_at"] == "2026-10-20T21:00:00+09:00"
    assert d["modified_by"] == "L01@m3fa"
    assert d["retention_until"] == "2027-10-20"
    assert d["broadcast"]["video_no"] == "0" and d["chat"] is None
    assert d["media"] == {"media_rev": 0, "current": None, "history": []}
    assert d["calibration"]["active_rev"] == 0 and d["calibration"]["revs"][0]["status"] == "unverified"
    assert d["pseudonymization"]["salt_id"] == ids.salt_id(SALT)
    assert d["labelers"] == []
    g = d["guides"][0]
    assert g["doc_sha256"] == hashlib.sha256(b"# guide\n").hexdigest()
    assert g["thresholds_sha256"] == hashlib.sha256(canonical_json(g["thresholds"])).hexdigest()
    with open(os.path.join(p.root, "guide", "v0.1.md"), "rb") as f:
        assert f.read() == b"# guide\n"
    assert p.salt() == SALT
    assert validate_project(d) == []
    # 다시 열어도 같다 (폴더나 project.json 경로 둘 다)
    for q in (project.open(p.root), project.open(os.path.join(p.root, "project.json"))):
        assert not q.read_only and q.data == d
    # 채팅 수집기가 이어 쓰는 값
    assert ids.viewer_id(p.salt(), hashlib.sha1(b"fixture-1").hexdigest()).startswith("vw_")


def test_create_engine_author_and_existing(tmp_path):
    p = _make(tmp_path, engine_author=True)
    assert p.data["labelers"] == [{"labeler_id": "L01", "role": "primary",
                                   "assigned_session_kinds": ["primary"], "engine_author": True}]
    with pytest.raises(FileExistsError):
        _make(tmp_path)


def test_create_rejects_bad_args(tmp_path):
    with pytest.raises(ValueError):
        project.create(str(tmp_path), "t", 0, "목적", "l01", "0.0.1", NOW, PID, SALT, guide=_guide(), machine4="m3fa")
    with pytest.raises(ValueError):
        project.create(str(tmp_path), "t", 0, "", "L01", "0.0.1", NOW, PID, SALT, guide=_guide(), machine4="m3fa")
    with pytest.raises(ValueError):
        project.create(str(tmp_path), "t", 0, "목적", "L01", "0.0.1", NOW, PID, b"short", guide=_guide(), machine4="m3fa")


def test_default_guide_from_repo(tmp_path):
    p = project.create(str(tmp_path), "기본 기준서", 0, machine4="m3fa", now=NOW)
    assert not p.read_only, p.problems
    with open(os.path.join(ROOT, "docs", "labeling-guide", "v0.1.md"), "rb") as f:
        assert p.data["guides"][0]["doc_sha256"] == hashlib.sha256(f.read()).hexdigest()
    if not os.path.exists(os.path.join(ROOT, "assets", "guide-config.v0.1.json")):
        assert p.data["x"] == {"guide_thresholds_missing": True}
        assert any("시작값" in w for w in p.warnings)


def test_korean_path(tmp_path):
    base = tmp_path / "한글 폴더" / "프로젝트들"
    p = project.create(str(base), "한글 제목 테스트", 0, "목적", "L01", "0.0.1", NOW, PID, SALT,
                       guide=_guide(), machine4="m3fa")
    assert not p.read_only
    assert project.open(p.root).salt() == SALT
    p.update(lambda d: d.__setitem__("notes", "한글 메모"), now=NOW)
    assert project.open(p.root).data["notes"] == "한글 메모"


# --- 저장·rev ---------------------------------------------------------------

def test_update_increments_rev(tmp_path):
    p = _make(tmp_path)
    later = NOW + timedelta(minutes=5)
    new = p.update(lambda d: d.__setitem__("dataset_split", "test"), now=later, by="L02")
    assert new["rev"] == 2 and p.rev == 2
    assert new["modified_by"] == "L02@m3fa" and new["modified_at"] == "2026-10-20T21:05:00+09:00"
    q = project.open(p.root)
    assert q.data["dataset_split"] == "test" and q.rev == 2
    with open(os.path.join(p.root, "project.json.bak"), "rb") as f:
        assert load_json_strict(f.read(), 1 << 20)["rev"] == 1
    assert not [n for n in os.listdir(p.root) if n.endswith(".tmp")]


def test_update_conflict(tmp_path):
    a = _make(tmp_path)
    b = project.open(a.root, machine4="zzzz")
    a.update(lambda d: d.__setitem__("notes", "A"))
    with pytest.raises(project.ConflictError) as ei:
        b.update(lambda d: d.__setitem__("notes", "B"))
    assert ei.value.read_rev == 1 and ei.value.disk_rev == 2
    assert project.open(a.root).data["notes"] == "A"     # 뒤에 저장하려던 쪽이 덮지 못했다
    b.reload()
    b.update(lambda d: d.__setitem__("notes", "B"))
    assert project.open(a.root).data["notes"] == "B" and b.rev == 3


def test_update_rejects_invalid_and_frozen_fields(tmp_path):
    p = _make(tmp_path)
    with pytest.raises(ValueError):
        p.update(lambda d: d.__setitem__("dataset_split", "prod"))
    with pytest.raises(ValueError):
        p.update(lambda d: d.__setitem__("project_id", "12345678-1234-4abc-8def-000000000000"))
    assert project.open(p.root).rev == 1


def test_update_media_by_other_module(tmp_path):
    p = _make(tmp_path)

    def set_media(d):
        d["media"]["media_rev"] = 1
        d["media"]["current"] = {"path_abs": "D:\\vod\\x.mp4", "path_rel": None, "size": 10, "mtime_ns": 1,
                                 "partial_fp": "0" * 64, "probe": {"duration_ms": 1000, "vfr": False}}
    p.update(set_media)
    assert project.open(p.root).data["media"]["media_rev"] == 1


# --- 검증 실패 → 읽기 전용 -----------------------------------------------------

def _rewrite(p, mutate=None, raw=None):
    pj = os.path.join(p.root, "project.json")
    if raw is None:
        with open(pj, "rb") as f:
            d = json.loads(f.read())
        mutate(d)
        raw = json.dumps(d, ensure_ascii=False).encode("utf-8")
    with open(pj, "wb") as f:
        f.write(raw)


@pytest.mark.parametrize("mutate", [
    lambda d: d.__setitem__("rev", "2"),
    lambda d: d.__setitem__("rev", True),
    lambda d: d.pop("purpose"),
    lambda d: d.__setitem__("unknown_field", 1),
    lambda d: d["broadcast"].__setitem__("video_no", 0),
    lambda d: d.__setitem__("active_guide_version", "9.9"),
    lambda d: d["calibration"].__setitem__("active_rev", 5),
    lambda d: d.__setitem__("modified_by", "L01"),
    lambda d: d.__setitem__("schema_version", 2),
])
def test_invalid_opens_read_only(tmp_path, mutate):
    p = _make(tmp_path)
    _rewrite(p, mutate)
    q = project.open(p.root)
    assert q.read_only and q.problems
    with pytest.raises(project.ReadOnlyError):
        q.update(lambda d: None)


@pytest.mark.parametrize("raw", [b'{"rev": NaN}', b"not json", b"[]", b"\xef\xbb\xbf"])
def test_corrupt_json_read_only(tmp_path, raw):
    p = _make(tmp_path)
    _rewrite(p, raw=raw)
    q = project.open(p.root)
    assert q.read_only and q.problems and (q.data is None)


def test_bom_project_json_ok(tmp_path):
    p = _make(tmp_path)
    pj = os.path.join(p.root, "project.json")
    with open(pj, "rb") as f:
        raw = f.read()
    with open(pj, "wb") as f:
        f.write(b"\xef\xbb\xbf" + raw)
    assert not project.open(p.root).read_only


def test_salt_mismatch_and_missing(tmp_path):
    p = _make(tmp_path)
    sj = os.path.join(p.root, "chat", "protected", "salt.json")
    os.remove(sj)
    q = project.open(p.root)
    assert not q.read_only and any("salt.json" in w for w in q.warnings)
    with pytest.raises(FileNotFoundError):
        q.salt()
    with open(sj, "w", encoding="utf-8") as f:
        json.dump({"salt_hex": "ab" * 32, "created_at": "x"}, f)
    q = project.open(p.root)
    assert q.read_only and any("솔트" in x for x in q.problems)


# --- 경고 --------------------------------------------------------------------

def test_onedrive_warning(tmp_path, monkeypatch):
    od = tmp_path / "OneDrive"
    od.mkdir()
    monkeypatch.setenv("OneDrive", str(od))
    p = project.create(str(od / "projects"), "원드라이브", 0, "목적", "L01", "0.0.1", NOW, PID, SALT,
                       guide=_guide(), machine4="m3fa")
    assert not p.read_only
    assert any("OneDrive" in w for w in p.warnings)
    assert p.storage_kind == "onedrive"
    assert not any("OneDrive" in w for w in _make(tmp_path).warnings)


def test_unc_and_long_path_warnings():
    assert project.storage_kind(r"\\server\share\p") == "unc"
    assert any("UNC" in w for w in project.path_warnings(r"\\server\share\p"))
    long = "C:\\" + "a" * 200
    assert any("180" in w for w in project.path_warnings(long))
    assert not any("180" in w for w in project.path_warnings("C:\\short"))


def test_atomic_write_retries(tmp_path, monkeypatch):
    target = str(tmp_path / "f.json")
    calls = {"n": 0}
    real = os.replace

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("busy")
        real(a, b)
    monkeypatch.setattr(project.os, "replace", flaky)
    slept = []
    project.atomic_write(target, b"x", sleep=slept.append)
    assert open(target, "rb").read() == b"x" and slept == [0.1, 0.3]
    calls["n"] = -100
    with pytest.raises(PermissionError):
        project.atomic_write(target, b"y", sleep=lambda s: None)
    assert open(target, "rb").read() == b"x"
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


# --- CLI ---------------------------------------------------------------------

def test_cli(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for v in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        env.pop(v, None)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "new_project.py"), "--root", str(tmp_path),
                        "--title", "CLI 합성 방송", "--video-no", "0", "--labeler", "L02", "--machine4", "m3fa"],
                       capture_output=True, env=env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = r.stdout.decode("utf-8").strip().splitlines()[0]
    assert os.path.basename(out) == "CLI_합성_방송-0"
    p = project.open(out)
    assert not p.read_only and p.data["created_by"] == "L02"
    r2 = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "new_project.py"), "--root", str(tmp_path),
                         "--title", "CLI 합성 방송", "--video-no", "0"], capture_output=True, env=env)
    assert r2.returncode == 1


# ── 검토 수정 재현 (2026-10-20) ──────────────────────────────────────────
@pytest.mark.parametrize("title", ["aux.txt", "com1.a", "nul.x", "CON.b", "lpt9.z", "Prn.q"])
def test_slugify_avoids_windows_reserved_names(tmp_path, title):
    slug = project.slugify(title, "0")
    assert slug.startswith("_")
    assert slug.split(".", 1)[0].rstrip(" ").upper() not in project.WINDOWS_RESERVED
    p = project.create(str(tmp_path), title, "0")      # 예전에는 WinError 267 / FileNotFoundError
    assert os.path.isdir(p.root)


def test_slugify_reserved_without_dot_unchanged():
    assert project.slugify("aux", "0") == "aux-0"
    assert project.slugify("console.log", "0") == "console.log-0"
