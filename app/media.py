"""영상 참조 (docs/design/02-architecture.md 3-2 media 행, 03 의 $defs.mediaRef, 05 S-13·S-26).

영상은 프로젝트에 넣지 않고 참조만 한다. 같은 파일인지는 부분 지문 partial_fp 로 판정한다.

- check_path_string(path): 파일 시스템에 닿기 전에 문자열만 보고 거부 사유를 돌려준다 (UNC·URL·장치 경로·'..'·
  드라이브 없는 경로·대체 데이터 스트림).
- media_ref(path): schemas/project.schema.json 의 mediaRef 모양 dict (path_abs, path_rel, size, mtime_ns,
  partial_fp, full_sha256, ntfs_file_id, volume_serial, probe).
- verify(ref, path): 지금 path 의 파일이 ref 와 같은 파일인가.

partial_fp 알고리즘 (03 8-4, 스키마 설명과 같음. 이름: pfp-v1):
  sha256( ascii(size 십진수) + b"\\n" + 앞 1MB + [size×k/10 위치의 64KB, k=1..8] + 끝 1MB )
  파일이 작아 구간이 겹치거나 모자라면 있는 만큼만 읽는다(겹쳐도 같은 규칙이라 재현된다).
"""
from __future__ import annotations

import hashlib
import os
import re

from . import analysis

MB = 1024 * 1024
FP_HEAD = 1 * MB
FP_TAIL = 1 * MB
FP_MID = 64 * 1024
FP_MID_COUNT = 8

_DRIVE_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")  # 'C:' 같은 한 글자 드라이브는 제외(2글자 이상)


def check_path_string(path) -> str | None:
    """경로 문자열 검사. 받아들이면 None, 거부하면 사유(한국어 한 줄).

    거부: 빈 값·str 아님, NUL·제어 문자, URL(scheme: · ://), UNC(\\\\서버 · //서버), 장치 경로(\\\\?\\ · \\\\.\\),
    드라이브 문자로 시작하지 않는 경로(상대·루트 상대 '\\x'·'C:x'), '..' 구성요소, 드라이브 뒤의 ':'(대체 데이터 스트림).
    파일 시스템은 건드리지 않는다.
    """
    if not isinstance(path, str):
        return "경로가 문자열이 아닙니다"
    p = path.strip()
    if not p:
        return "경로가 비었습니다"
    if p != path:
        return "경로 앞뒤에 공백이 있습니다"
    if any(ord(c) < 32 for c in p):
        return "경로에 제어 문자가 있습니다"
    if "://" in p or _SCHEME_RE.match(p):
        return "URL 은 받지 않습니다 (로컬 파일만)"
    q = p.replace("/", "\\")
    if q.startswith("\\\\?\\") or q.startswith("\\\\.\\"):
        return "장치 경로(\\\\?\\, \\\\.\\)는 받지 않습니다"
    if q.startswith("\\\\"):
        return "네트워크 경로(UNC)는 받지 않습니다"
    if not _DRIVE_ABS_RE.match(p):
        return "드라이브 문자로 시작하는 절대 경로가 아닙니다"
    if ":" in p[2:]:
        return "경로에 ':' 가 더 있습니다 (대체 데이터 스트림)"
    parts = [s for s in q[3:].split("\\")]
    if any(s == ".." for s in parts):
        return "경로에 '..' 가 있습니다"
    return None


def _require_ok(path: str) -> str:
    why = check_path_string(path)
    if why:
        raise ValueError(why)
    return os.path.normpath(path)


def partial_fp(path: str, size: int | None = None) -> str:
    """부분 지문 pfp-v1 (모듈 설명). 64자 소문자 16진수."""
    p = _require_ok(path)
    if size is None:
        size = os.path.getsize(p)
    h = hashlib.sha256()
    h.update(str(int(size)).encode("ascii") + b"\n")
    with open(p, "rb") as f:
        def chunk(off: int, n: int):
            off = max(0, min(off, size))
            n = max(0, min(n, size - off))
            f.seek(off)
            data = f.read(n)
            if len(data) != n:
                raise OSError(f"읽기가 짧습니다 ({len(data)}/{n}바이트) — 파일이 바뀌는 중일 수 있습니다")
            h.update(data)
        chunk(0, FP_HEAD)
        for k in range(1, FP_MID_COUNT + 1):
            chunk(size * k // 10, FP_MID)
        chunk(size - FP_TAIL, FP_TAIL)
    return h.hexdigest()


def probe_summary(info: dict) -> dict:
    """analysis.probe 결과에서 스키마 $defs.probe 의 키만 고른다 (additionalProperties: false)."""
    return {k: info.get(k) for k in ("duration_ms", "video_codec", "audio_codec", "width", "height",
                                     "r_frame_rate", "avg_frame_rate", "vfr", "nb_frames")}


def media_ref(path: str, project_dir: str | None = None, probe_info: dict | None = None) -> dict:
    """mediaRef dict. 경로 검사 → stat → 부분 지문 → ffprobe.

    project_dir 를 주면 같은 드라이브일 때 path_rel 을 채운다. full_sha256 은 None (백그라운드에서 따로 계산).
    probe_info 로 analysis.probe 결과를 넘기면 다시 부르지 않는다. 스키마 밖의 probe 값(container·fps·
    sample_rate·channels)이 필요하면 analysis.probe 를 직접 쓴다.
    """
    p = _require_ok(path)
    st = os.stat(p)
    fp = partial_fp(p, st.st_size)
    st2 = os.stat(p)
    if (st2.st_size, st2.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
        raise OSError("지문을 계산하는 동안 파일이 바뀌었습니다")
    info = probe_info if probe_info is not None else analysis.probe(p)
    rel = None
    if project_dir:
        try:
            if os.path.splitdrive(os.path.abspath(project_dir))[0].lower() == os.path.splitdrive(p)[0].lower():
                rel = os.path.relpath(p, os.path.abspath(project_dir))
        except ValueError:
            rel = None
    return {
        "path_abs": p,
        "path_rel": rel,
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "partial_fp": fp,
        "full_sha256": None,
        "ntfs_file_id": str(st.st_ino) if st.st_ino else None,
        "volume_serial": str(st.st_dev) if st.st_dev else None,
        "probe": probe_summary(info),
    }


def verify(ref: dict, path: str) -> tuple[bool, str | None]:
    """path 의 파일이 ref(mediaRef) 와 같은 파일인가. (True, None) 또는 (False, 사유).

    판정 기준은 size 와 partial_fp 뿐이다. 경로·mtime·파일 ID 가 달라도 지문이 같으면 같은 파일(이동·복사).
    """
    why = check_path_string(path)
    if why:
        return False, f"경로 거부: {why}"
    p = os.path.normpath(path)
    try:
        size = os.path.getsize(p)
    except OSError:
        return False, "파일이 없거나 열 수 없습니다"
    if size != ref.get("size"):
        return False, f"크기가 다릅니다 ({size} ≠ {ref.get('size')})"
    try:
        fp = partial_fp(p, size)
    except OSError as e:
        return False, f"읽지 못했습니다: {e}"
    if fp != ref.get("partial_fp"):
        return False, "부분 지문이 다릅니다 (다른 파일이거나 내용이 바뀜)"
    return True, None
