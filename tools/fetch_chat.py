"""치지직 VOD 채팅을 프로젝트 폴더에 받는다 (app/chzzk_chat.collect 의 명령줄 껍데기).

    python tools/fetch_chat.py <주소|번호> --project <폴더> [--duration-sec N] [--salt-file <chat/protected/salt.json>]
                               [--max-pages N] [--nickname-mask first_char|hidden] [--no-duration-lookup]

- 솔트 파일이 없으면 새로 만든다: {"salt_hex": "<64자>", "created_at": "..."} (기본 <project>/chat/protected/salt.json).
- --duration-sec 이 없으면 영상 정보 API 에서 길이를 한 번 읽는다(--no-duration-lookup 이면 안 읽고 48시간 상한).
- 프로젝트 폴더가 OneDrive 아래면 경고한다 (보호 구역이 클라우드로 동기화된다).
- 출력에는 개수·시각만 찍는다. 본문·닉네임은 찍지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import chzzk_chat, paths  # noqa: E402

_SALT_HEX_RE = re.compile(r"\A[0-9a-f]{64}\Z")


def load_or_create_salt(path: str) -> tuple[bytes, bool]:
    """(솔트 32바이트, 새로 만들었는지). 있는 파일이 형식에 맞지 않으면 ValueError (덮어쓰지 않는다)."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            obj = json.loads(f.read().decode("utf-8-sig"))
        hx = obj.get("salt_hex") if isinstance(obj, dict) else None
        if not isinstance(hx, str) or not _SALT_HEX_RE.match(hx):
            raise ValueError(f"솔트 파일 형식이 아닙니다: {path}")
        return bytes.fromhex(hx), False
    salt = os.urandom(32)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(json.dumps({"salt_hex": salt.hex(),
                            "created_at": datetime.now().astimezone().isoformat(timespec="seconds")},
                           ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return salt, True


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="치지직 VOD 채팅 수집")
    ap.add_argument("video", help="치지직 영상 주소 또는 번호")
    ap.add_argument("--project", required=True, help="프로젝트 폴더")
    ap.add_argument("--duration-sec", type=float, default=None, help="VOD 길이(초)")
    ap.add_argument("--salt-file", default=None, help="솔트 파일 (기본 <project>/chat/protected/salt.json)")
    ap.add_argument("--max-pages", type=int, default=chzzk_chat.DEFAULT_MAX_PAGES)
    ap.add_argument("--nickname-mask", choices=chzzk_chat.NICKNAME_MASKS, default="first_char")
    ap.add_argument("--no-duration-lookup", action="store_true", help="영상 정보 API 로 길이를 읽지 않는다")
    a = ap.parse_args(argv)

    no = chzzk_chat.parse_video_no(a.video)
    if not no:
        print(f"치지직 영상 번호를 찾을 수 없습니다: {a.video}", file=sys.stderr)
        return 2
    project = os.path.abspath(a.project)
    if paths.is_under_onedrive(project):
        print("경고: 프로젝트 폴더가 OneDrive 아래에 있습니다. 보호 구역(닉네임·userIdHash)이 클라우드로 동기화됩니다.",
              file=sys.stderr)
    salt_file = a.salt_file or os.path.join(project, "chat", "protected", "salt.json")
    try:
        salt, created = load_or_create_salt(salt_file)
    except (OSError, ValueError) as ex:
        print(f"솔트를 읽을 수 없습니다: {ex}", file=sys.stderr)
        return 2
    print(f"솔트: {'새로 만듦' if created else '기존 파일'} (salt_id {chzzk_chat.ids.salt_id(salt)})")

    duration_ms = int(a.duration_sec * 1000) if a.duration_sec and a.duration_sec > 0 else None
    if duration_ms is None and not a.no_duration_lookup:
        duration_ms = chzzk_chat.fetch_video_duration_ms(no)
        print(f"VOD 길이: {duration_ms // 1000 if duration_ms else '모름'}초")

    last = [0]

    def progress(frac: float, pages: int, msgs: int, cursor_ms: int) -> None:
        if pages - last[0] >= 50:
            last[0] = pages
            print(f"  {pages}페이지 {msgs}건 커서 {cursor_ms // 1000}초 ({frac * 100:.0f}%)", flush=True)

    try:
        s = chzzk_chat.collect(no, project, salt, nickname_mask=a.nickname_mask, duration_ms=duration_ms,
                               on_progress=progress, max_pages=a.max_pages)
    except chzzk_chat.ChatFetchError as ex:
        print(f"수집 실패: {ex}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("중단됨 (임시 파일 지움)", file=sys.stderr)
        return 130
    print(json.dumps({"fetch_id": s.fetch_id, "pages": s.pages, "messages": s.messages, "viewers": s.viewers,
                      "complete": s.complete, "stop_reason": s.stop_reason,
                      "duplicates_skipped": s.duplicates_skipped, "elapsed_sec": round(s.elapsed_sec, 1),
                      "kind_counts": s.kind_counts}, ensure_ascii=False, indent=2))
    return 0 if s.complete else 3


if __name__ == "__main__":
    sys.exit(main())
