"""JSON Lines 안전 읽기·쓰기 (docs/design/03-data-model.md 8-1·8-2, S-30).

쓰기: 파일 끝이 줄바꿈이 아니면 먼저 줄바꿈을 더하고(잘린 줄과 이어 붙지 않게), 한 줄을 os.write 한 번으로 쓰고 fsync.
읽기: 앞 BOM 은 벗긴다. 마지막 줄만 깨졌으면 '잘린 마지막 줄' 로 격리하고 나머지는 읽는다.
      중간 줄이 깨졌으면 그 줄을 건너뛰지 않고 문제로 알린다(호출하는 쪽이 읽기 전용으로 연다).
본문·닉네임을 로그에 남기지 않는다. 문제 보고에는 줄 번호와 사유만 담는다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Iterable


def _reject_constant(name: str):
    raise ValueError(f"허용하지 않는 상수 {name}")


def dumps_line(obj: dict) -> bytes:
    """한 줄 직렬화: UTF-8, 한글 그대로, 공백 없는 구분자, NaN 금지, 끝에 LF."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def append(path: str, obj: dict) -> None:
    """한 줄 추가. 앞서 잘린 줄이 있으면 줄바꿈부터 넣는다."""
    data = dumps_line(obj)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o644)
    try:
        size = os.fstat(fd).st_size
        if size > 0:
            with open(path, "rb") as r:
                r.seek(size - 1)
                if r.read(1) != b"\n":
                    data = b"\n" + data
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def write_all(path: str, objs: Iterable[dict]) -> int:
    """새 파일에 전부 쓴다 (임시 파일 → fsync → os.replace). 줄 수를 돌려준다. 이미 있으면 덮어쓰지 않는다."""
    if os.path.exists(path):
        raise FileExistsError(path)
    tmp = path + ".tmp"
    n = 0
    with open(tmp, "wb") as f:
        for o in objs:
            f.write(dumps_line(o))
            n += 1
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return n


@dataclass
class ReadResult:
    records: list[dict] = field(default_factory=list)
    truncated_tail: bool = False          # 마지막 줄이 깨짐 (격리하고 나머지는 씀)
    corrupt_lines: list[int] = field(default_factory=list)   # 중간에 깨진 줄 번호 (1부터)
    had_bom: bool = False

    @property
    def ok(self) -> bool:
        return not self.corrupt_lines


def read(path: str, max_line_bytes: int = 1 << 20) -> ReadResult:
    """전체를 읽는다. 각 줄은 최상위 object 여야 한다."""
    res = ReadResult()
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
        res.had_bom = True
    lines = data.split(b"\n")
    ends_with_newline = data.endswith(b"\n")
    if ends_with_newline:
        lines = lines[:-1]
    last = len(lines)
    for i, raw in enumerate(lines, start=1):
        raw = raw.rstrip(b"\r")
        if not raw.strip():
            continue
        try:
            if len(raw) > max_line_bytes:
                raise ValueError("줄이 너무 깁니다")
            obj = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
            if not isinstance(obj, dict):
                raise ValueError("object 가 아닙니다")
            res.records.append(obj)
        except (ValueError, UnicodeDecodeError):
            if i == last and not ends_with_newline:
                res.truncated_tail = True
            else:
                res.corrupt_lines.append(i)
    return res
