"""어느 시각 주변의 채팅을 화면에 보여 주는 로컬 도우미 (09 3-2 손라벨 · 3-3 앵커 찾기용).

사용:
  python tools/chat_window.py --project <프로젝트 폴더> --at <시각> [--before 30] [--after 60]
                              [--viewer vw_...] [--grep 단어] [--all-kinds] [--show-nickname]

  --at       : 채팅 시각(VOD 축 = messages.t_ms). '1:02:03' · '1:02:03.5' · '62:03' · '83.5s' 는 시각,
               소수점 없는 정수('3723000')나 '3723000ms' 는 ms 로 읽는다.
               (tools/calibrate.py 는 '3723' 을 초로 읽는다. 여기서는 과제 지시대로 정수 = ms. 읽은 값을 첫 줄에 찍는다)
  --before / --after : 기준 시각 앞·뒤 몇 초까지 보일지 (기본 30 / 60)
  --viewer   : 이 viewer_id(vw_…) 의 채팅만. 'anon' 도 된다
  --grep     : 본문에 이 글자가 들어간 채팅만 (대소문자 무시, 여러 번 주면 모두 포함해야 함)
  --all-kinds: text 말고 후원·구독·기타도 보인다 (기본 text 만)
  --show-nickname : 보호 구역 chat/protected/identities.jsonl 의 원 닉네임을 함께 보인다. 경고를 찍는다.
                    켤 때마다 <P>/labels/nickname-exposure.jsonl 에 노출 기록 한 줄을 덧붙인다(03 14-4 view.exposed 의
                    시험판). 기록에는 시각·창 범위·chat_id 범위·건수만 적고 닉네임·본문·--grep 글자·--viewer 는 적지 않는다.
                    기록을 쓰지 못하면 닉네임을 보이지 않고 끝난다(종료 코드 1). engine score --project 가 이 파일을 읽어
                    보고서에 '노출 기록' 을 표시한다.

출력 한 줄: 'VOD시각  chat_id  시청자 번호  가림닉네임  본문'
  시청자 번호는 03 14-2 의 표시용 viewer_no (messages.jsonl 전체를 (t_ms, chat_id) 순으로 보고
  viewer_id 가 처음 나온 순서, t_ms 가 null 인 줄은 맨 뒤, 익명은 번호 없음).

출력은 화면에만 한다. --show-nickname 의 노출 기록 말고는 파일을 쓰지 않는다. 화면 내용을 저장소·보고에 옮기지 마라.
종료 코드: 0 정상 · 1 입력 오류
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import bisect
import os
import re
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import jsonl  # noqa: E402
from app.core import timecode as tc  # noqa: E402

NICKNAME_WARNING = ("경고: 원 닉네임(보호 구역 identities.jsonl)을 보이고 있다. "
                    "화면 밖(파일·저장소·채팅·보고)으로 옮기지 마라 (03 14-4)")

_INT_MS = re.compile(r"\A\s*\+?([0-9]+)\s*(ms)?\s*\Z", re.IGNORECASE)
_CTRL = re.compile(r"[\x00-\x1f\x7f  ]")


# ── 시각 ───────────────────────────────────────────────────────────────────

def parse_at(text: str) -> int | None:
    """--at 값 → ms. 소수점 없는 정수와 '…ms' 는 ms, 나머지는 timecode.parse_time(시각)."""
    if text is None:
        return None
    m = _INT_MS.match(text)
    if m:
        return int(m.group(1))
    v = tc.parse_time(text)
    if v is None or v < 0:
        return None
    return v


# ── 읽기 ───────────────────────────────────────────────────────────────────

def messages_path(project_dir: str) -> str:
    return os.path.join(project_dir, "chat", "messages.jsonl")


def identities_path(project_dir: str) -> str:
    return os.path.join(project_dir, "chat", "protected", "identities.jsonl")


EXPOSURE_LOG_REL = os.path.join("labels", "nickname-exposure.jsonl")   # engine/score.py 와 같은 이름


def exposure_log_path(project_dir: str) -> str:
    return os.path.join(project_dir, EXPOSURE_LOG_REL)


def exposure_record(at_ms: int, before_ms: int, after_ms: int, rows: list[dict], *, now: str) -> dict:
    """노출 기록 한 줄. 닉네임·본문·viewer_id·검색어는 넣지 않는다(03 14-4 의 what/chat_id 만)."""
    ids = [str(r.get("chat_id")) for r in rows if r.get("chat_id")]
    return {"event": "view.exposed", "what": "nickname_raw", "tool": "chat_window", "at": now,
            "at_ms": at_ms, "window_start_ms": max(0, at_ms - before_ms), "window_end_ms": at_ms + after_ms,
            "n_rows": len(rows), "first_chat_id": ids[0] if ids else None, "last_chat_id": ids[-1] if ids else None}


def append_exposure(project_dir: str, rec: dict) -> str:
    p = exposure_log_path(project_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    jsonl.append(p, rec)
    return p


def load_messages(project_dir: str) -> tuple[list[dict], list[str]]:
    """messages.jsonl 전체와 경고 목록. 깨진 줄은 빼고 경고에 줄 번호만 적는다."""
    p = messages_path(project_dir)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"messages.jsonl 이 없다: {p}")
    res = jsonl.read(p)
    warns = []
    if res.corrupt_lines:
        warns.append(f"깨진 줄 {len(res.corrupt_lines)}개를 뺐다 (처음: {res.corrupt_lines[:5]})")
    if res.truncated_tail:
        warns.append("마지막 줄이 잘려 뺐다")
    return res.records, warns


def load_identities(project_dir: str) -> dict[str, list[tuple[int, str]]]:
    """viewer_id → [(first_seen_ms, nickname)] (시각 순). 파일이 없으면 FileNotFoundError."""
    p = identities_path(project_dir)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"identities.jsonl 이 없다 (수집 PC 에만 있다): {p}")
    out: dict[str, list[tuple[int, str]]] = {}
    for r in jsonl.read(p).records:
        vid = r.get("viewer_id")
        if not vid:
            continue
        names = []
        for n in r.get("nicknames") or []:
            if isinstance(n, dict) and n.get("nickname"):
                fs = n.get("first_seen_ms")
                names.append((int(fs) if isinstance(fs, int) else 0, str(n["nickname"])))
        names.sort()
        out[vid] = names
    return out


def nickname_at(names: list[tuple[int, str]] | None, t_ms: int | None) -> str | None:
    """그 시각에 쓰던 닉네임: first_seen_ms ≤ t_ms 인 것 중 마지막. 없으면 처음 것."""
    if not names:
        return None
    if t_ms is None:
        return names[-1][1]
    cur = names[0][1]
    for fs, n in names:
        if fs <= t_ms:
            cur = n
    return cur


def nickname_list(names: list[tuple[int, str]] | None) -> list[str] | None:
    """engine.features.nickname_called 에 넘길 닉네임 문자열 목록."""
    if names is None:
        return None
    return [n for _, n in names]


# ── 시청자 번호 (03 14-2) ────────────────────────────────────────────────

def _order_key(r: dict):
    t = r.get("t_ms")
    return (1, 0, str(r.get("chat_id") or "")) if not isinstance(t, int) else (0, t, str(r.get("chat_id") or ""))


def sort_messages(records: list[dict]) -> list[dict]:
    """(t_ms, chat_id) 오름차순, t_ms 가 null 인 줄은 맨 뒤."""
    return sorted(records, key=_order_key)


def viewer_numbers(records: list[dict]) -> dict[str, int]:
    """viewer_id → 표시용 viewer_no (1부터). anon 은 넣지 않는다."""
    out: dict[str, int] = {}
    for r in sort_messages(records):
        vid = r.get("viewer_id")
        if not vid or vid == "anon" or r.get("is_anonymous"):
            continue
        if vid not in out:
            out[vid] = len(out) + 1
    return out


def fmt_viewer_no(vid: str | None, vno: dict[str, int]) -> str:
    if not vid or vid == "anon":
        return "익명"
    n = vno.get(vid)
    return "-" if n is None else f"시청자 {n:04d}"


# ── 창 고르기 ─────────────────────────────────────────────────────────────

def clean_text(s, limit: int | None = None) -> str:
    """화면 한 줄에 맞게: 줄바꿈·제어 문자를 공백으로, 길면 자른다."""
    s = _CTRL.sub(" ", str(s or ""))
    if limit and len(s) > limit:
        s = s[:limit - 1] + "…"
    return s


def select_window(records: list[dict], at_ms: int, before_ms: int, after_ms: int, *,
                  viewer: str | None = None, grep: list[str] | None = None,
                  kinds: tuple[str, ...] | None = ("text",)) -> list[dict]:
    """[at−before, at+after] 안의 채팅 (t_ms 순). 이미 sort_messages 로 정렬된 목록이면 bisect 로 빨리 찾는다."""
    rows = [r for r in records if isinstance(r.get("t_ms"), int)]
    if any(rows[i]["t_ms"] > rows[i + 1]["t_ms"] for i in range(len(rows) - 1)):
        rows = sort_messages(rows)
    ts = [r["t_ms"] for r in rows]
    lo = bisect.bisect_left(ts, at_ms - before_ms)
    hi = bisect.bisect_right(ts, at_ms + after_ms)
    words = [g.lower() for g in (grep or []) if g]
    out = []
    for r in rows[lo:hi]:
        if kinds is not None and r.get("kind") not in kinds:
            continue
        if viewer and r.get("viewer_id") != viewer:
            continue
        if words:
            body = str(r.get("text") or "").lower()
            if not all(w in body for w in words):
                continue
        out.append(r)
    return out


def format_row(r: dict, vno: dict[str, int], *, nickname: str | None = None, at_ms: int | None = None,
               text_limit: int | None = 200, show_kind: bool = False) -> str:
    t = r.get("t_ms")
    tstr = tc.fmt_display(t) if isinstance(t, int) else "  (시각 없음)"
    rel = ""
    if at_ms is not None and isinstance(t, int):
        rel = f" ({tc.fmt_offset(t - at_ms):>7})"
    masked = clean_text(r.get("nickname_masked") or "", 12)
    nick = f" [{clean_text(nickname, 30)}]" if nickname is not None else ""
    kind = f" <{r.get('kind')}>" if show_kind and r.get("kind") != "text" else ""
    return (f"{tstr}{rel}  {r.get('chat_id', '?'):<21}  {fmt_viewer_no(r.get('viewer_id'), vno):<11}  "
            f"{masked:<8}{nick}{kind}  {clean_text(r.get('text'), text_limit)}")


# ── 명령줄 ───────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="어느 VOD 시각 주변의 채팅을 화면에 보여 준다 (파일을 쓰지 않음)")
    ap.add_argument("--project", required=True, help="프로젝트 폴더 (chat/messages.jsonl 이 있는 곳)")
    ap.add_argument("--at", required=True, help="기준 채팅 시각. 1:02:03(.5) 형식, 정수는 ms")
    ap.add_argument("--before", type=float, default=30.0, help="앞으로 몇 초 (기본 30)")
    ap.add_argument("--after", type=float, default=60.0, help="뒤로 몇 초 (기본 60)")
    ap.add_argument("--viewer", help="이 viewer_id 의 채팅만 (vw_… 또는 anon)")
    ap.add_argument("--grep", action="append", default=[], help="본문에 들어 있어야 할 글자 (여러 번)")
    ap.add_argument("--all-kinds", action="store_true", help="text 말고 다른 종류도 보인다")
    ap.add_argument("--show-nickname", action="store_true", help="보호 구역의 원 닉네임을 함께 보인다")
    ap.add_argument("--max-text", type=int, default=200, help="본문을 몇 글자에서 자를지 (기본 200, 0 = 자르지 않음)")
    a = ap.parse_args(argv)

    at = parse_at(a.at)
    if at is None:
        print(f"오류: --at 시각을 읽지 못했다: {a.at!r}", file=sys.stderr)
        return 1
    if a.before < 0 or a.after < 0:
        print("오류: --before / --after 는 0 이상", file=sys.stderr)
        return 1
    if a.viewer and not re.fullmatch(r"vw_[a-z2-7]{12}|anon", a.viewer):
        print(f"오류: --viewer 는 vw_ + 12글자 또는 anon: {a.viewer!r}", file=sys.stderr)
        return 1
    try:
        records, warns = load_messages(a.project)
    except FileNotFoundError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    ident = None
    if a.show_nickname:
        try:
            ident = load_identities(a.project)
        except FileNotFoundError as e:
            print(f"오류: {e}", file=sys.stderr)
            return 1

    records = sort_messages(records)
    vno = viewer_numbers(records)
    before_ms, after_ms = int(round(a.before * 1000)), int(round(a.after * 1000))
    rows = select_window(records, at, before_ms, after_ms, viewer=a.viewer, grep=a.grep,
                         kinds=None if a.all_kinds else ("text",))

    if ident is not None:
        # 닉네임을 보이기 전에 노출 기록을 남긴다. 못 남기면 보이지 않는다.
        try:
            append_exposure(a.project, exposure_record(
                at, before_ms, after_ms, rows, now=datetime.now().astimezone().isoformat(timespec="seconds")))
        except (OSError, ValueError) as e:
            print(f"오류: 노출 기록을 쓰지 못해 닉네임을 보이지 않는다: {type(e).__name__}", file=sys.stderr)
            return 1
        print(NICKNAME_WARNING)
        print(f"노출 기록: {EXPOSURE_LOG_REL}")
    for w in warns:
        print("주의: " + w)
    print(f"기준 {tc.fmt_display(at)} ({at}ms) · 범위 {tc.fmt_display(max(0, at - before_ms))} ~ "
          f"{tc.fmt_display(at + after_ms)} · {len(rows)}건"
          + (f" · 시청자 {a.viewer}" if a.viewer else "") + (f" · 찾는 글자 {a.grep}" if a.grep else ""))
    print("VOD 시각     (기준과 차이)  chat_id                시청자 번호  가림닉네임  본문")
    marked = False
    for r in rows:
        if not marked and r["t_ms"] >= at:
            print(f"──────── 기준 {tc.fmt_display(at)} ────────")
            marked = True
        nick = nickname_at(ident.get(r.get("viewer_id")), r.get("t_ms")) if ident is not None else None
        if ident is not None and nick is None:
            nick = "?"
        print(format_row(r, vno, nickname=nick, at_ms=at, text_limit=a.max_text or None,
                         show_kind=a.all_kinds))
    if not marked:
        print(f"──────── 기준 {tc.fmt_display(at)} ────────")
    return 0


if __name__ == "__main__":
    sys.exit(main())
