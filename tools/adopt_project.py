"""project.json 이 없는 기존 채팅 수집 폴더를 앱 프로젝트로 들인다 (09 5-2 질문 10, 03 2-1·6-1).

tools/fetch_chat.py 로 프로젝트 없이 받은 폴더(chat/messages.jsonl, chat/fetch-*.json, chat/protected/salt.json …)에
그 솔트를 **그대로** 쓰는 project.json 을 만든다. 솔트를 새로 만들면 viewer_id 가 모두 달라지기 때문이다.

사용:
  python tools/adopt_project.py --project <수집 폴더> --title "<방송 제목>" --video-no N
         [--labeler L01] [--purpose "..."] [--engine-author yes|no] [--machine4 abcd]
         [--fetch-id F20261020-ab12] [--dry-run]

하는 일
  1. project.json 이 이미 있으면 거부한다(덮어쓰지 않는다).
  2. chat/protected/salt.json 을 읽고, chat/fetch-*.json 중 messages.jsonl 바이트 해시가 같은 수집 기록 하나를 고른다.
     그 기록의 salt_id · video_no · 메시지 수 · kind 별 개수가 실제 파일과 다르면 거부한다.
  3. project.json 내용은 app.project.create 로 만든다(같은 검증·같은 기본값). 다만 폴더를 새로 만들 수 없으므로
     보호 구역 안 임시 폴더(chat/protected/.tmp-adopt-*)에 만들어 내용만 가져오고 임시 폴더는 지운다.
     (임시 폴더 이름이 .tmp- 로 시작해 강제 종료로 남아도 다음 채팅 수집이 지운다)
  4. chat 요약(chat_rev 1)을 수집 기록에서 채우고, broadcast.duration_ms 는 수집 기록의 duration_ms 로 둔다.
     media 는 비워 둔다(media_rev 0, current null). 보정은 rev 0(오프셋 0, unverified).
  5. 빠진 하위 폴더(guide·labels·samples·locks)를 만들고 guide/v0.1.md 를 복사한 뒤 project.json 을 맨 마지막에 쓴다.
     salt.json · messages.jsonl · fetch 기록은 건드리지 않는다.
  6. 다시 열어 읽기 전용이 아닌지, 솔트가 project.json 의 salt_id 와 맞는지 확인한다.

--dry-run: 1~4 까지만 하고 저장할 project.json 을 출력한다. 3 의 임시 폴더만 잠깐 만들었다 지우고
           project.json · guide · 하위 폴더는 만들지 않는다.

폴더 이름은 바꾸지 않는다. slug(<제목>-<번호>)와 폴더 이름이 다르면 열 때 '폴더 이름 ≠ slug' 경고가 뜬다(막지 않음).
출력에 채팅 본문·닉네임·솔트 값은 넣지 않는다.

종료 코드: 0 정상 · 1 입력 오류·거부 · 2 썼지만 다시 열었을 때 문제가 있음
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import argparse
import builtins
import copy
import glob
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import project  # noqa: E402
from app.core import ids  # noqa: E402
from app.core.validate import StrictJsonError, load_json_strict, validate_project  # noqa: E402

FETCH_MAX_BYTES = 1024 * 1024
CHAT_SOURCE = "chzzk_vod_api_v1"
CHAT_ID_ALGORITHM = "sl-chat-id-v1"
TMP_PREFIX = ".tmp-adopt-"


class AdoptError(RuntimeError):
    """들일 수 없다(이미 프로젝트임, 파일 없음, 해시·솔트·번호 불일치 등). 아무것도 쓰지 않았다."""


@dataclass
class MessagesStats:
    sha256: str
    count: int
    kind_counts: dict
    anonymous_count: int
    server_minus_player_ms: int | None


@dataclass
class AdoptResult:
    root: str
    data: dict                       # 저장한(또는 dry-run 이면 저장할) project.json 내용
    fetch_id: str
    written: bool
    warnings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # 쓴 뒤 다시 열었을 때의 읽기 전용 사유


# ---------------------------------------------------------------------------
# 읽기

def _read_json(path: str, max_bytes: int, what: str) -> dict:
    try:
        with builtins.open(path, "rb") as f:
            raw = f.read(max_bytes + 1)
    except FileNotFoundError:
        raise AdoptError(f"{what} 이 없습니다") from None
    except OSError as e:
        raise AdoptError(f"{what} 을 읽지 못했습니다 ({type(e).__name__})") from None
    try:
        return load_json_strict(raw, max_bytes)
    except StrictJsonError as e:
        raise AdoptError(f"{what} 이 엄격 JSON 이 아닙니다: {e}") from None


def read_salt(pp: project.ProjectPaths) -> bytes:
    s = _read_json(pp.salt_json, project.SALT_MAX_BYTES, "chat/protected/salt.json")
    hx = s.get("salt_hex")
    if not isinstance(hx, str) or len(hx) != 64 or any(c not in "0123456789abcdef" for c in hx):
        raise AdoptError("salt.json 의 salt_hex 형식이 아닙니다(소문자 16진수 64자)")
    return bytes.fromhex(hx)


def messages_stats(path: str) -> MessagesStats:
    """messages.jsonl 의 바이트 해시와 개수. 본문은 보지 않고 kind·is_anonymous·시각만 센다."""
    h = hashlib.sha256()
    kinds: Counter = Counter()
    n = anon = 0
    diffs: set[int] = set()
    try:
        f = builtins.open(path, "rb")
    except FileNotFoundError:
        raise AdoptError("chat/messages.jsonl 이 없습니다") from None
    with f:
        for lineno, line in enumerate(f, 1):
            h.update(line)
            if not line.strip():
                continue
            try:
                rec = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise AdoptError(f"messages.jsonl {lineno}행이 JSON 이 아닙니다") from None
            if not isinstance(rec, dict):
                raise AdoptError(f"messages.jsonl {lineno}행이 object 가 아닙니다")
            n += 1
            kinds[str(rec.get("kind"))] += 1
            if rec.get("is_anonymous") is True:
                anon += 1
            st, t = rec.get("server_time_ms"), rec.get("t_ms")
            if isinstance(st, int) and isinstance(t, int) and not isinstance(st, bool) and not isinstance(t, bool):
                diffs.add(st - t)
    smp = next(iter(diffs)) if len(diffs) == 1 else None
    return MessagesStats(h.hexdigest(), n, dict(kinds), anon, smp)


def find_fetch(pp: project.ProjectPaths, msha: str, fetch_id: str | None) -> dict:
    """messages.jsonl 해시와 맞는 fetch 기록 하나. 없거나 둘 이상이면 AdoptError."""
    found = []
    for p in sorted(glob.glob(os.path.join(glob.escape(pp.chat_dir), "fetch-*.json"))):
        fid = os.path.basename(p)[len("fetch-"):-len(".json")]
        if not ids.FETCH_ID_RE.match(fid):
            continue
        if fetch_id and fid != fetch_id:
            continue
        rec = _read_json(p, FETCH_MAX_BYTES, f"chat/fetch-{fid}.json")
        if rec.get("fetch_id") != fid:
            raise AdoptError(f"fetch-{fid}.json 안의 fetch_id 가 파일 이름과 다릅니다")
        found.append(rec)
    if not found:
        raise AdoptError("chat/fetch-*.json 수집 기록이 없습니다" + (f" (--fetch-id {fetch_id})" if fetch_id else ""))
    hits = [r for r in found if r.get("messages_sha256") == msha]
    if not hits:
        raise AdoptError("messages.jsonl 의 해시가 어느 수집 기록과도 맞지 않습니다(수집 뒤 파일이 바뀌었습니다)")
    if len(hits) > 1:
        raise AdoptError("messages.jsonl 과 맞는 수집 기록이 여럿입니다. --fetch-id 로 고르세요: "
                         + ", ".join(r["fetch_id"] for r in hits))
    return hits[0]


def chat_summary(fetch: dict, st: MessagesStats) -> dict:
    """project.json 의 chat 요약 (schemas/project.schema.json chatSummary)."""
    complete = fetch.get("complete") is True
    return {
        "chat_rev": 1,
        "fetch_id": fetch["fetch_id"],
        "fetched_at": fetch.get("finished_at") or fetch.get("started_at"),
        "source": CHAT_SOURCE,
        "chat_id_algorithm": CHAT_ID_ALGORITHM,
        "message_count": st.count,
        "messages_sha256": st.sha256,
        "complete": complete,
        "incomplete_reason": None if complete else (str(fetch.get("stop_reason") or "")[:300] or None),
        "server_minus_player_ms": st.server_minus_player_ms,
        "anonymous_count": st.anonymous_count,
        "kind_counts": dict(st.kind_counts),
    }


# ---------------------------------------------------------------------------
# 들이기

def _build_data(pp: project.ProjectPaths, title: str, vno: str, salt: bytes, fetch: dict, *, labeler: str,
                purpose: str, engine_author, machine4, now) -> tuple[dict, bytes, list[str]]:
    """app.project.create 를 보호 구역 안 임시 폴더에 돌려 project.json 내용과 기준서 바이트를 얻는다."""
    dur = fetch.get("duration_ms")
    dur = dur if isinstance(dur, int) and not isinstance(dur, bool) and dur >= 0 else 0
    tmp = tempfile.mkdtemp(prefix=TMP_PREFIX, dir=pp.protected_dir)
    try:
        p = project.create(tmp, title, vno, purpose, labeler, now=now, salt=salt,
                           engine_author=engine_author, machine4=machine4, duration_ms=dur)
        data = copy.deepcopy(p.data)
        doc_rel = data["guides"][0]["doc_path"]
        with builtins.open(os.path.join(p.root, doc_rel.replace("/", os.sep)), "rb") as f:
            doc = f.read()
        # 임시 폴더 경로에서 나온 경고(OneDrive 등)는 버리고 기준서 경고만 남긴다
        warns = [w for w in p.warnings if "시작값" in w]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return data, doc, warns


def adopt(project_dir: str, title: str, video_no, *, labeler: str = "L01",
          purpose: str = project.DEFAULT_PURPOSE, engine_author: bool | None = None,
          machine4: str | None = None, fetch_id: str | None = None, dry_run: bool = False,
          now: datetime | None = None) -> AdoptResult:
    root = os.path.abspath(project_dir)
    if not os.path.isdir(root):
        raise AdoptError("프로젝트 폴더가 없습니다")
    pp = project.ProjectPaths(root)
    if os.path.lexists(pp.project_json):
        raise AdoptError("이미 project.json 이 있습니다. 들이기는 project.json 이 없는 수집 폴더에만 씁니다")
    vno = str(video_no).strip()
    if not ids.VIDEO_NO_RE.match(vno):
        raise AdoptError(f"video_no 형식이 아닙니다: {video_no!r}")
    if fetch_id is not None and not ids.FETCH_ID_RE.match(fetch_id):
        raise AdoptError(f"fetch_id 형식이 아닙니다: {fetch_id!r}")
    if not isinstance(title, str) or not title.strip():
        raise AdoptError("제목이 비었습니다")

    salt = read_salt(pp)
    st = messages_stats(pp.messages_jsonl)
    fetch = find_fetch(pp, st.sha256, fetch_id)
    if fetch.get("salt_id") != ids.salt_id(salt):
        raise AdoptError("salt.json 이 수집 기록의 salt_id 와 다릅니다(다른 솔트로 받은 채팅입니다)")
    if str(fetch.get("video_no")) != vno:
        raise AdoptError("--video-no 가 수집 기록의 video_no 와 다릅니다")
    if fetch.get("messages") != st.count:
        raise AdoptError(f"메시지 수가 수집 기록과 다릅니다 (기록 {fetch.get('messages')}, 파일 {st.count})")
    if isinstance(fetch.get("kind_counts"), dict) and fetch["kind_counts"] != st.kind_counts:
        raise AdoptError("kind 별 개수가 수집 기록과 다릅니다")

    warnings: list[str] = []
    if fetch.get("complete") is not True:
        warnings.append(f"수집 기록이 complete=false 입니다 (사유 {fetch.get('stop_reason')!r})")
    if not os.path.exists(pp.identities_jsonl):
        warnings.append("chat/protected/identities.jsonl 이 없습니다")

    try:
        data, doc, gw = _build_data(pp, title, vno, salt, fetch, labeler=labeler, purpose=purpose,
                                    engine_author=engine_author, machine4=machine4, now=now)
    except (ValueError, OSError) as e:
        raise AdoptError(f"project.json 내용을 만들지 못했습니다: {e}") from None
    warnings += gw
    data["chat"] = chat_summary(fetch, st)
    x = dict(data.get("x") or {})
    x["adopted"] = {"tool": "tools/adopt_project.py", "at": data["created_at"], "fetch_id": fetch["fetch_id"],
                    "folder_name": os.path.basename(root)}
    data["x"] = x
    problems = validate_project(data)
    if problems:
        raise AdoptError("만든 project.json 이 검증을 통과하지 않습니다: " + "; ".join(problems[:5]))
    if data["pseudonymization"]["salt_id"] != fetch["salt_id"]:
        raise AdoptError("내부 오류: salt_id 가 수집 기록과 다릅니다")

    doc_path = os.path.join(root, data["guides"][0]["doc_path"].replace("/", os.sep))
    if os.path.exists(doc_path):
        with builtins.open(doc_path, "rb") as f:
            if f.read() != doc:
                raise AdoptError(f"{data['guides'][0]['doc_path']} 가 이미 있는데 기준서 원문과 다릅니다")
    if data["slug"] != os.path.basename(root):
        warnings.append(f"폴더 이름이 slug({data['slug']})와 다릅니다. 폴더는 옮기지 않았습니다(열 때 경고만 뜸)")

    res = AdoptResult(root=root, data=data, fetch_id=fetch["fetch_id"], written=False, warnings=warnings)
    if dry_run:
        return res

    for d in pp.created_dirs():
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(doc_path):
        project.atomic_write(doc_path, doc)
    if os.path.lexists(pp.project_json):          # 그 사이 누가 만들었으면 덮지 않는다
        raise AdoptError("그 사이 project.json 이 생겼습니다. 덮어쓰지 않았습니다")
    project.atomic_write(pp.project_json, project._dump_project(data))
    res.written = True
    p = project.open_project(root, machine4=machine4)
    res.problems = list(p.problems)
    if not p.read_only:
        try:
            p.salt()
        except (OSError, ValueError) as e:
            res.problems.append(f"솔트 확인 실패: {e}")
    return res


# ---------------------------------------------------------------------------
# 명령줄

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="project.json 이 없는 채팅 수집 폴더에 기존 솔트로 project.json 을 만든다")
    ap.add_argument("--project", required=True, help="기존 수집 폴더 (chat/ 와 chat/protected/salt.json 이 있는 곳)")
    ap.add_argument("--title", required=True, help="방송 제목 (slug 에 쓰인다)")
    ap.add_argument("--video-no", required=True, help="치지직 VOD 번호 (수집 기록과 같아야 한다)")
    ap.add_argument("--labeler", default="L01", help="만드는 라벨러 ID (기본 L01)")
    ap.add_argument("--purpose", default=project.DEFAULT_PURPOSE, help="수집·처리 목적 한 줄")
    ap.add_argument("--engine-author", choices=("yes", "no"), default=None,
                    help="만드는 사람이 엔진 규칙·코드 작성자인지. 주면 명부(labelers)에 primary 로 넣는다")
    ap.add_argument("--machine4", default=None, help="modified_by 의 PC 표시 4자 (기본: 이 PC 에서 계산)")
    ap.add_argument("--fetch-id", default=None, help="수집 기록이 여럿일 때 고른다 (예: F20261020-ab12)")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 만들 project.json 만 출력")
    a = ap.parse_args(argv)
    ea = None if a.engine_author is None else a.engine_author == "yes"
    try:
        r = adopt(a.project, a.title, a.video_no, labeler=a.labeler, purpose=a.purpose, engine_author=ea,
                  machine4=a.machine4, fetch_id=a.fetch_id, dry_run=a.dry_run)
    except AdoptError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    c = r.data["chat"]
    print(f"수집 기록 {r.fetch_id}: 메시지 {c['message_count']}건 · complete={c['complete']} · "
          f"익명 {c['anonymous_count']} · server−player {c['server_minus_player_ms']}ms")
    print(f"slug {r.data['slug']} · thresholds_sha256 {r.data['guides'][0]['thresholds_sha256'][:12]} · "
          f"duration_ms {r.data['broadcast']['duration_ms']}")
    for w in r.warnings:
        print(f"경고: {w}", file=sys.stderr)
    if a.dry_run:
        print("--dry-run: 쓰지 않음. 만들 project.json:")
        print(json.dumps(r.data, ensure_ascii=False, indent=2))
        return 0
    print(f"만듦: {os.path.join(r.root, project.PROJECT_FILE)}")
    if r.problems:
        for pr in r.problems:
            print(f"문제: {pr}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
