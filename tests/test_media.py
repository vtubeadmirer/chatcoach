"""app/media.py — 경로 문자열 검사(S-26), 부분 지문, mediaRef 모양, verify. 합성 데이터만 쓴다."""
import json
import os
import shutil
import subprocess

import pytest

from app import media, paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- 경로 검사
@pytest.mark.parametrize("p", [
    r"C:\Videos\a.mp4",
    "C:/Videos/a.mp4",
    r"d:\한글 폴더\[15]영상 00m00s.mp4",
    r"C:\a..b\c.mp4",          # '..' 가 이름 안에 있는 것은 괜찮다
    r"C:\x\.hidden.mp4",
])
def test_accepts_local_absolute(p):
    assert media.check_path_string(p) is None


@pytest.mark.parametrize("p,needle", [
    (r"\\server\share\a.mp4", "UNC"),
    ("//server/share/a.mp4", "UNC"),
    (r"\\?\C:\a.mp4", "장치"),
    (r"\\?\UNC\server\share\a.mp4", "장치"),
    (r"\\.\PhysicalDrive0", "장치"),
    ("http://example.com/a.mp4", "URL"),
    ("https://example.com/a.m3u8", "URL"),
    ("file:///C:/a.mp4", "URL"),
    ("file:C:/a.mp4", "URL"),
    ("rtmp://x/y", "URL"),
    ("concat:a.mp4|b.mp4", "URL"),
    (r"C:\a\..\b.mp4", ".."),
    (r"C:\a\..", ".."),
    ("C:/a/../b.mp4", ".."),
    ("a.mp4", "드라이브"),
    (r"videos\a.mp4", "드라이브"),
    (r"\videos\a.mp4", "드라이브"),
    ("C:a.mp4", "드라이브"),
    (r"C:\a.mp4:stream", ":"),
    ("", "비었"),
    ("   ", "비었"),
    (" C:\\a.mp4", "공백"),
    ("C:\\a\x00.mp4", "제어"),
    ("C:\\a\n.mp4", "제어"),
])
def test_rejects(p, needle):
    why = media.check_path_string(p)
    assert why and needle in why, (p, why)


def test_rejects_non_str():
    assert media.check_path_string(None)
    assert media.check_path_string(b"C:\\a.mp4")


# --------------------------------------------------------------------------- 부분 지문
def _write(path, size, seed=7):
    # 결정적 의사난수 바이트 (내용 자체는 의미 없음)
    import random
    rnd = random.Random(seed)
    with open(path, "wb") as f:
        f.write(rnd.randbytes(size))


@pytest.mark.parametrize("size", [0, 10, 70_000, 3 * 1024 * 1024 + 123])
def test_partial_fp_stable_and_copy_equal(tmp_path, size):
    a = tmp_path / "a.bin"
    _write(a, size)
    b = tmp_path / "b.bin"
    shutil.copyfile(a, b)
    fa = media.partial_fp(str(a))
    assert len(fa) == 64 and all(c in "0123456789abcdef" for c in fa)
    assert fa == media.partial_fp(str(a))
    assert fa == media.partial_fp(str(b))


@pytest.mark.parametrize("offset_frac", [0.0, 0.5, 0.999999])
def test_partial_fp_changes_on_one_byte(tmp_path, offset_frac):
    a = tmp_path / "a.bin"
    size = 3 * 1024 * 1024 + 123
    _write(a, size)
    before = media.partial_fp(str(a))
    # 앞(0)·가운데 표본(50% 위치)·끝 1MB 안의 1바이트를 바꾼다
    off = size * 5 // 10 + 10 if offset_frac == 0.5 else int(size * offset_frac)
    with open(a, "r+b") as f:
        f.seek(off)
        x = f.read(1)
        f.seek(off)
        f.write(bytes([x[0] ^ 0xFF]))
    assert media.partial_fp(str(a)) != before


def test_partial_fp_includes_size(tmp_path):
    a = tmp_path / "a.bin"
    _write(a, 1000)
    before = media.partial_fp(str(a))
    with open(a, "ab") as f:
        f.write(b"\0")
    assert media.partial_fp(str(a)) != before


def test_partial_fp_known_value(tmp_path):
    """알고리즘 고정(pfp-v1): 작은 파일은 앞 1MB + 64KB×8 + 끝 1MB 가 모두 전체 내용이다."""
    import hashlib
    a = tmp_path / "a.bin"
    data = b"chatcoach"
    a.write_bytes(data)
    want = hashlib.sha256(b"9\n" + data + b"".join(data[9 * k // 10:] for k in range(1, 9)) + data).hexdigest()
    assert media.partial_fp(str(a)) == want


def test_partial_fp_rejects_bad_path():
    with pytest.raises(ValueError):
        media.partial_fp(r"\\server\share\a.mp4")


# --------------------------------------------------------------------------- mediaRef · verify
def _make_clip(path):
    ffmpeg = paths.find_tool("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg 없음")
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
           "-f", "lavfi", "-i", "aevalsrc='if(lt(t,1),0.5*sin(2*PI*440*t),0)':s=48000:d=2",
           "-c:v", "mpeg4", "-c:a", "aac", "-ac", "2", "-shortest", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)


def _schema():
    with open(os.path.join(ROOT, "schemas", "project.schema.json"), encoding="utf-8") as f:
        return json.load(f)


def test_media_ref_shape(tmp_path):
    clip = tmp_path / "clip.mp4"
    _make_clip(clip)
    ref = media.media_ref(str(clip), project_dir=str(tmp_path))
    defs = _schema()["$defs"]
    allowed = set(defs["mediaRef"]["properties"])
    assert set(ref) <= allowed
    assert set(defs["mediaRef"]["required"]) <= set(ref)
    assert set(ref["probe"]) <= set(defs["probe"]["properties"])
    assert ref["path_rel"] == "clip.mp4"
    assert ref["size"] == os.path.getsize(clip)
    pr = ref["probe"]
    assert abs(pr["duration_ms"] - 2000) <= 100
    assert (pr["width"], pr["height"]) == (160, 120)
    assert pr["video_codec"] == "mpeg4" and pr["audio_codec"] == "aac"
    assert pr["r_frame_rate"] == "25/1"
    assert pr["vfr"] is False
    # jsonschema 가 있으면 정식으로도 검사
    try:
        import jsonschema
    except ImportError:
        return
    sch = {"$defs": defs, **defs["mediaRef"]}
    jsonschema.validate(ref, sch)


def test_verify(tmp_path):
    a = tmp_path / "a.bin"
    _write(a, 2 * 1024 * 1024)
    st = os.stat(a)
    ref = {"size": st.st_size, "partial_fp": media.partial_fp(str(a))}
    assert media.verify(ref, str(a)) == (True, None)
    moved = tmp_path / "sub" / "moved.bin"
    moved.parent.mkdir()
    shutil.copyfile(a, moved)
    assert media.verify(ref, str(moved)) == (True, None)
    ok, why = media.verify(ref, str(tmp_path / "none.bin"))
    assert not ok and "없" in why
    ok, why = media.verify(ref, r"\\server\share\a.bin")
    assert not ok and "거부" in why
    with open(a, "r+b") as f:
        f.write(b"\xff\xfe")
    ok, why = media.verify(ref, str(a))
    assert not ok and "지문" in why
    with open(a, "ab") as f:
        f.write(b"x")
    ok, why = media.verify(ref, str(a))
    assert not ok and "크기" in why
