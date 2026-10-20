"""받은 업데이트 zip 의 검사 (validate_zip, docs/design/04 6-5 의 7가지) · 해제 · 릴리스 쪽 zip 만들기.

- 통과: package_release 가 만든 전체·경량 zip (매니페스트 asset 과 대조 포함).
- 거부: 항목 수·압축 해제 크기가 asset 과 다름, 보존 폴더(data/_backup/_update, 대소문자 섞어서도),
  zip-slip(..)·절대경로·':'·제어 문자, 최상위 폴더 다름, 필수 파일 없음, _internal/VERSION 내용 다름,
  casefold 중복, 경량에 ffmpeg·libmpv 있음, 전체에 없음, 최상위 exe 2개, 3000개·2GiB 상한(SPEC 을 줄여서), 깨진 파일.
- extract_zip 이 같은 내용을 풀고, package_release.make_zip 이 폴더 항목 없이 슬래시만 쓰며 보존 폴더를 넣지 않는다.
- link_heavy_files 가 ffmpeg\\ · libmpv-2.dll 을 붙인다 (하드링크 또는 복사).
"""
from __future__ import annotations

import os
import zipfile
from dataclasses import replace

import pytest

import release_kit as rk
import package_release

from app import updater
from app.appspec import SPEC
from app.updater import UpdateError

TOP = SPEC.zip_top + "/"


@pytest.fixture(scope="module")
def rel(tmp_path_factory):
    return rk.build_release(str(tmp_path_factory.mktemp("zrel")))


@pytest.fixture(scope="module")
def manifest(rel):
    return updater.parse_manifest(rel.data, SPEC)


def rebuild(src: str, dst: str, drop=(), add=None, replace_=None, rename=None) -> str:
    """src zip 을 복사하면서 항목을 빼고(drop) · 더하고(add) · 내용을 바꾸고(replace_) · 이름을 바꾼다(rename(fn))."""
    add = add or {}
    replace_ = replace_ or {}
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename in drop:
                continue
            data = replace_.get(info.filename, zin.read(info))
            name = rename(info.filename) if rename else info.filename
            zout.writestr(name, data)
        for name, data in add.items():
            zout.writestr(zipfile.ZipInfo(name), data)
    return dst


def _names(path: str) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def _rejects(path, version="0.0.2", lite=False, spec=SPEC, asset=None, match=None):
    with pytest.raises(UpdateError) as ei:
        updater.validate_zip(str(path), version, lite, spec, asset)
    assert ei.value.kind == "verify"
    if match:
        assert match in ei.value.message, ei.value.message
    return ei.value.message


# --------------------------------------------------------------------------- 통과
def test_full_and_lite_pass_with_asset(rel, manifest):
    full = updater.validate_zip(rel.full_zip, "0.0.2", False, SPEC, manifest.asset("full"))
    lite = updater.validate_zip(rel.lite_zip, "0.0.2", True, SPEC, manifest.asset("lite"))
    assert full.file_count == manifest.asset("full").file_count and full.unpacked_size == manifest.asset("full").unpacked_size
    assert lite.file_count == manifest.asset("lite").file_count
    assert TOP + "_internal/ffmpeg/ffmpeg.exe" in full.names and TOP + "_internal/libmpv-2.dll" in full.names
    assert not any("ffmpeg" in n or "libmpv" in n for n in lite.names)
    assert TOP + "ChatCoach.exe" in lite.names and TOP + "_internal/VERSION" in lite.names
    # asset 없이도 통과 (릴리스 쪽 tools/check_release_zip.py 의 쓰임)
    assert updater.validate_zip(rel.full_zip, "0.0.2", False).file_count == full.file_count


def test_asset_count_and_size_must_match(rel, manifest):
    a = manifest.asset("full")
    _rejects(rel.full_zip, asset=replace(a, file_count=a.file_count + 1), match="파일 수")
    _rejects(rel.full_zip, asset=replace(a, unpacked_size=a.unpacked_size - 1), match="압축 해제 크기")
    # 전체 asset 으로 경량 zip 을 검사하면 (바뀐 릴리스) 개수가 달라 거부
    _rejects(rel.lite_zip, lite=True, asset=a)


# --------------------------------------------------------------------------- 보존 폴더 · 경로
@pytest.mark.parametrize("name", [
    TOP + "data/settings.ini", TOP + "DATA/settings.ini", TOP + "Data/x/y.txt",
    TOP + "_backup/ChatCoach.exe", TOP + "_BackUp/x", TOP + "_update/upd-1/replace.ps1", TOP + "_Update/x",
    TOP + "data/",                     # 폴더 항목만 있어도
    TOP + "data",                      # 같은 이름의 파일
])
def test_keep_dirs_rejected(rel, tmp_path, name):
    p = rebuild(rel.full_zip, str(tmp_path / "k.zip"), add={name: b"x"})
    _rejects(p, match="보존 폴더")


def test_same_name_inside_internal_is_allowed(rel, tmp_path):
    """보존 폴더는 최상위만이다. _internal/data 같은 이름은 패키지 내용이다 (교체 스크립트의 전체 경로 /XD 와 같은 규칙)."""
    p = rebuild(rel.full_zip, str(tmp_path / "ok.zip"), add={TOP + "_internal/data/inner.txt": b"x",
                                                              TOP + "_internal/_update/x.txt": b"y"})
    assert updater.validate_zip(p, "0.0.2", False).file_count == len(_names(rel.full_zip)) + 2


@pytest.mark.parametrize("name,why", [
    (TOP + "../evil.txt", "'..'"),
    (TOP + "_internal/../../evil.txt", "'..'"),
    (TOP + "./x.txt", "'..'"),
    (TOP + "_internal//x.txt", "빈 경로"),
    ("/" + TOP + "x.txt", "절대경로"),
    ("C:/" + TOP + "x.txt", "':'"),
    (TOP + "C:x.txt", "':'"),
    (TOP + "x.txt:ads", "':'"),
    (TOP + "a\x07b.txt", "제어 문자"),
    (TOP + "a\nb.txt", "제어 문자"),
    ("Other/x.txt", "최상위 폴더"),
    ("x.txt", "최상위 폴더"),
    ("chatcoach/x.txt", "최상위 폴더"),      # 최상위 이름은 대소문자까지 같아야 한다
])
def test_unsafe_paths_rejected(rel, tmp_path, name, why):
    p = rebuild(rel.full_zip, str(tmp_path / "p.zip"), add={name: b"x"})
    _rejects(p, match=why)


def test_other_top_folder_rejected(rel, tmp_path):
    p = rebuild(rel.full_zip, str(tmp_path / "t.zip"), rename=lambda n: "ChatCoach2/" + n[len(TOP):])
    _rejects(p, match="최상위 폴더")


# --------------------------------------------------------------------------- 필수 파일 · 버전 · 중복
@pytest.mark.parametrize("missing", ["ChatCoach.exe", "_internal/python312.dll", "_internal/VERSION"])
def test_required_file_missing(rel, tmp_path, missing):
    p = rebuild(rel.full_zip, str(tmp_path / "m.zip"), drop={TOP + missing})
    msg = _rejects(p)
    assert "필수 파일" in msg or "VERSION" in msg


def test_version_inside_must_match(rel, tmp_path):
    p = rebuild(rel.full_zip, str(tmp_path / "v.zip"), replace_={TOP + "_internal/VERSION": b"0.0.3\n"})
    _rejects(p, match="버전")
    _rejects(rel.full_zip, version="0.0.9", match="버전")
    # BOM · CRLF 가 붙은 VERSION 은 같은 버전으로 본다
    p = rebuild(rel.full_zip, str(tmp_path / "v2.zip"), replace_={TOP + "_internal/VERSION": b"\xef\xbb\xbf0.0.2\r\n"})
    assert updater.validate_zip(p, "0.0.2", False).file_count > 0


@pytest.mark.parametrize("dup", ["_internal/version", "CHATCOACH.EXE", "_internal/Python312.dll"])
def test_casefold_duplicate_rejected(rel, tmp_path, dup):
    p = rebuild(rel.full_zip, str(tmp_path / "d.zip"), add={TOP + dup: b"x"})
    msg = _rejects(p)
    assert "중복" in msg or "exe" in msg


def test_casefold_duplicate_exact_message(rel, tmp_path):
    p = rebuild(rel.full_zip, str(tmp_path / "d.zip"), add={TOP + "_internal/version": b"x"})
    _rejects(p, match="대소문자만 다른 중복")


# --------------------------------------------------------------------------- 경량 / 전체
@pytest.mark.parametrize("extra", ["_internal/ffmpeg/ffmpeg.exe", "_internal/ffmpeg/sub/x.dll", "_internal/libmpv-2.dll"])
def test_lite_with_heavy_rejected(rel, tmp_path, extra):
    p = rebuild(rel.lite_zip, str(tmp_path / "l.zip"), add={TOP + extra: b"x"})
    _rejects(p, lite=True, match="경량 zip")


def test_full_without_heavy_rejected(rel, tmp_path):
    _rejects(rel.lite_zip, lite=False, match="전체 zip")
    no_mpv = rebuild(rel.full_zip, str(tmp_path / "a.zip"), drop={TOP + "_internal/libmpv-2.dll"})
    _rejects(no_mpv, match="libmpv-2.dll")
    with zipfile.ZipFile(rel.full_zip) as z:
        ff = {n for n in z.namelist() if n.startswith(TOP + "_internal/ffmpeg/")}
    no_ff = rebuild(rel.full_zip, str(tmp_path / "b.zip"), drop=ff)
    _rejects(no_ff, match="ffmpeg")


# --------------------------------------------------------------------------- exe · 상한 · 깨진 파일
def test_two_top_level_exe_rejected(rel, tmp_path):
    p = rebuild(rel.full_zip, str(tmp_path / "e.zip"), add={TOP + "Other.exe": b"MZ"})
    _rejects(p, match="최상위 exe")
    p = rebuild(rel.full_zip, str(tmp_path / "e2.zip"), add={TOP + "uninstall.EXE": b"MZ"})
    _rejects(p, match="최상위 exe")
    # 하위 폴더의 exe 는 세지 않는다 (ffmpeg.exe 가 이미 그렇다)
    p = rebuild(rel.full_zip, str(tmp_path / "e3.zip"), add={TOP + "_internal/helper.exe": b"MZ"})
    assert updater.validate_zip(p, "0.0.2", False).file_count > 0


def test_entry_limit(rel):
    n = len(_names(rel.full_zip))
    _rejects(rel.full_zip, spec=replace(SPEC, max_zip_entries=3), match="상한")
    assert updater.validate_zip(rel.full_zip, "0.0.2", False, replace(SPEC, max_zip_entries=n)).file_count == n


def test_unpacked_size_limit(rel, manifest):
    size = manifest.asset("full").unpacked_size
    _rejects(rel.full_zip, spec=replace(SPEC, max_unpacked_bytes=size - 1), match="상한")
    assert updater.validate_zip(rel.full_zip, "0.0.2", False, replace(SPEC, max_unpacked_bytes=size)).unpacked_size == size


def test_directory_entries_do_not_count_as_files(rel, tmp_path, manifest):
    p = rebuild(rel.full_zip, str(tmp_path / "dirs.zip"), add={TOP: b"", TOP + "_internal/": b""})
    s = updater.validate_zip(p, "0.0.2", False, SPEC, manifest.asset("full"))
    assert s.file_count == manifest.asset("full").file_count


def test_broken_file_rejected(rel, tmp_path):
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"PK\x03\x04 this is not a zip")
    _rejects(junk, match="열 수 없습니다")
    trunc = tmp_path / "trunc.zip"
    with open(rel.full_zip, "rb") as f:
        trunc.write_bytes(f.read()[:-30])
    _rejects(trunc, match="열 수 없습니다")
    _rejects(tmp_path / "none.zip", match="열 수 없습니다")


# --------------------------------------------------------------------------- 해제
def test_extract_zip_same_content(rel, tmp_path):
    dest = tmp_path / "new"
    out = updater.extract_zip(rel.full_zip, str(dest))
    assert out == os.path.join(str(dest), SPEC.zip_top) and os.path.isdir(out)
    with zipfile.ZipFile(rel.full_zip) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        for n in names:
            with open(os.path.join(str(dest), *n.split("/")), "rb") as f:
                assert f.read() == z.read(n), n
    extracted = sorted(os.path.relpath(os.path.join(d, f), str(dest)).replace("\\", "/")
                       for d, _, fs in os.walk(str(dest)) for f in fs)
    assert extracted == sorted(names)
    # 원본 빌드 폴더와도 같다 (data\ 는 빠짐)
    for rel_name in ("ChatCoach.exe", "_internal/python312.dll", "_internal/ffmpeg/ffmpeg.exe"):
        with open(os.path.join(rel.dist_app, *rel_name.split("/")), "rb") as a, \
                open(os.path.join(out, *rel_name.split("/")), "rb") as b:
            assert a.read() == b.read()
    assert not os.path.exists(os.path.join(out, "data"))


def test_extract_zip_refuses_unsafe_names(rel, tmp_path):
    p = rebuild(rel.full_zip, str(tmp_path / "s.zip"), add={TOP + "../evil.txt": b"x"})
    with pytest.raises(UpdateError):
        updater.extract_zip(p, str(tmp_path / "out"))
    assert not (tmp_path / "evil.txt").exists()


# --------------------------------------------------------------------------- 릴리스 쪽 zip
def test_make_zip_layout(tmp_path):
    app = rk.fake_app(str(tmp_path / "dist" / "ChatCoach"), "0.0.2")
    # 보존 폴더가 빌드 폴더에 있어도 넣지 않는다. _internal 안의 같은 이름은 넣는다
    rk._w(os.path.join(app, "_backup", "ChatCoach.exe"), b"old")
    rk._w(os.path.join(app, "_update", "upd-1", "replace.ps1"), b"x")
    rk._w(os.path.join(app, "data", "logs", "a.log"), b"log")
    rk._w(os.path.join(app, "_internal", "data", "inner.txt"), b"inner")
    os.makedirs(os.path.join(app, "_internal", "empty_dir"))
    full, lite = str(tmp_path / "full.zip"), str(tmp_path / "lite.zip")
    n_full = package_release.make_zip(app, full, lite=False)
    n_lite = package_release.make_zip(app, lite, lite=True)
    with zipfile.ZipFile(full) as z:
        names = z.namelist()
    with zipfile.ZipFile(lite) as z:
        lite_names = z.namelist()
    assert n_full == len(names) and n_lite == len(lite_names)
    for ns in (names, lite_names):
        assert all(n.startswith(TOP) for n in ns)
        assert not any(n.endswith("/") for n in ns)                        # 폴더 항목 없음
        assert not any("\\" in n for n in ns)                              # 슬래시만
        assert not any(n.split("/")[1] in SPEC.keep_dirs for n in ns)       # 보존 폴더 없음
        assert TOP + "_internal/data/inner.txt" in ns
    assert TOP + "_internal/libmpv-2.dll" in names and TOP + "_internal/ffmpeg/ffmpeg.exe" in names
    assert not any(n.startswith(TOP + "_internal/ffmpeg/") or n == TOP + "_internal/libmpv-2.dll" for n in lite_names)
    assert set(names) - set(lite_names) == {TOP + "_internal/libmpv-2.dll", TOP + "_internal/ffmpeg/ffmpeg.exe",
                                            TOP + "_internal/ffmpeg/avcodec-61.dll"}
    # 만든 zip 이 앱의 검사를 통과한다
    updater.validate_zip(full, "0.0.2", False)
    updater.validate_zip(lite, "0.0.2", True)
    # 같은 폴더로 두 번 만들면 항목 순서가 같다
    again = str(tmp_path / "again.zip")
    package_release.make_zip(app, again, lite=False)
    assert _names(again) == names


# --------------------------------------------------------------------------- 경량 준비: ffmpeg · libmpv 붙이기
def test_link_heavy_files(tmp_path):
    src_app = rk.fake_app(str(tmp_path / "installed"), "0.0.1")
    src = os.path.join(src_app, "_internal")
    dst = str(tmp_path / "new" / "_internal")
    os.makedirs(dst)
    n = updater.link_heavy_files(src, dst)
    assert n == 3   # libmpv-2.dll + ffmpeg\ffmpeg.exe + ffmpeg\avcodec-61.dll
    for rel_name in ("libmpv-2.dll", os.path.join("ffmpeg", "ffmpeg.exe"), os.path.join("ffmpeg", "avcodec-61.dll")):
        a, b = os.path.join(src, rel_name), os.path.join(dst, rel_name)
        assert os.path.isfile(b)
        with open(a, "rb") as fa, open(b, "rb") as fb:
            assert fa.read() == fb.read()
        st = os.stat(b)
        assert st.st_nlink >= 1            # 하드링크면 2, 복사면 1
    # 이미 있으면 다시 붙이지 않는다
    assert updater.link_heavy_files(src, dst) == 0
    # 설치본에 heavy 가 없으면 아무것도 하지 않는다
    bare = rk.fake_app(str(tmp_path / "bare"), "0.0.1", heavy=False)
    assert updater.link_heavy_files(os.path.join(bare, "_internal"), str(tmp_path / "new2")) == 0


def test_link_heavy_files_falls_back_to_copy(tmp_path, monkeypatch):
    src_app = rk.fake_app(str(tmp_path / "installed"), "0.0.1")
    dst = str(tmp_path / "new" / "_internal")

    def no_link(a, b):
        raise OSError("다른 볼륨")
    monkeypatch.setattr(updater.os, "link", no_link)
    assert updater.link_heavy_files(os.path.join(src_app, "_internal"), dst) == 3
    assert os.stat(os.path.join(dst, "libmpv-2.dll")).st_nlink == 1
