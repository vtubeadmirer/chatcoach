"""가상 채팅 시나리오 — 검사·조립·실제 채팅과 시간축 섞기 (docs/design/20-plan-virtual-chat.md 3장 H-1·5장 H-4, 08 D-05·D-19 2).

하는 일
- 형식: 시나리오 파일은 scenario.header 1줄 → scenario.chat 줄(보일 시각 순) → scenario.end 1줄이다
  (schemas/scenario.schema.json, chatcoach-scenario-v1). 줄 하나의 칸은 스키마가 보고, 줄 사이의 규칙은 여기서 본다.
- check_records: 줄 순서·건수(counts·end.lines)·해시(end.lines_sha256)·ID(scenario_id 4자 = 해시 앞 4자)·
  보일 시각(훈련 구간 [from, to) 안, 시각 순)·line_id 차례·retrieved 의 출처(같은 방송이면 구간 밖,
  다른 방송이면 header.inputs.from_projects 에 있음)·거르기 결과(걸린 줄이 들어 있지 않음)·이모티콘 허용 목록.
- assemble: 엔진(H-2)이 만든 header·chat 줄로 파일에 쓸 줄 목록을 만든다(시각 순 정렬, line_id 다시 매김,
  counts·end·scenario_id 채움). 같은 입력이면 같은 바이트.
- merge_for_display: 실제 채팅 목록 + 시나리오 → 화면용 새 목록(시간축 하나). 실제 채팅 dict 는 고치지 않고
  복사하지도 않는다(원 dict 를 row.message 로 가리키기만). 가상 줄마다 출처·가상 표시('가져옴'·'생성')를 단다.

지키는 것 (20 1장·8장)
- 가상 채팅의 표시 이름은 가명만(display_mask). retrieved 는 원 채팅의 가림 닉네임을 쓰지 않는다. 표시 이름이 화면의
  실제 채팅 가림 이름과 겹치면 '시청자N' 으로 바꿔 보인다(N = 그 줄의 차례).
- 가상 줄은 늘 virtual=True·source 를 단다. 표시 숨김(가려내기 H-5)은 badge 만 비우고 source·virtual 은 남긴다.
- 문제 보고에는 줄 번호·line_id·사유만. 본문·이름은 넣지 않는다(D-17 8).

core 층: 파일·시계·난수·Qt·네트워크를 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

SCHEMA_ID = "chatcoach-scenario-v1"

K_HEADER = "scenario.header"
K_CHAT = "scenario.chat"
K_END = "scenario.end"
LINE_KINDS = (K_HEADER, K_CHAT, K_END)

REPLAY = "replay"
RETRIEVED = "retrieved"
GENERATED = "generated"
SOURCES = (REPLAY, RETRIEVED, GENERATED)
VIRTUAL_SOURCES = (RETRIEVED, GENERATED)

SAME_BROADCAST = "same_broadcast"
SAME_CHANNEL = "same_channel"

# 화면 가상 표시 (20 5-2). 실제 채팅(replay)은 표시 없음.
BADGES = {REPLAY: "", RETRIEVED: "가져옴", GENERATED: "생성"}
PSEUDONYM_PREFIX = "시청자"

SCENARIO_ID_RE = re.compile(r"\ASC-([0-9]{8})-([0-9a-f]{4})\Z")
LINE_ID_RE = re.compile(r"\As([0-9]{4,6})\Z")
DISPLAY_MASK_RE = re.compile(r"\A(시청자[1-9][0-9]{0,3}|[^*\s{}:@]\*{2,7})\Z")
EMOTE_TOKEN_RE = re.compile(r"\{:[^{}:\s]{1,40}:\}")

MAX_CHAT_LINES = 20000


class ScenarioError(ValueError):
    """조립할 수 없는 입력. 메시지에는 본문·이름을 넣지 않는다."""


# ---------------------------------------------------------------- 직렬화·해시

def canonical_line(obj: Mapping) -> bytes:
    """한 줄 정규 직렬화. app.jsonl.dumps_line 과 같은 바이트(UTF-8, 한글 그대로, 공백 없는 구분자, NaN 금지, 끝 LF)."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def lines_sha256(chats: Iterable[Mapping]) -> str:
    """scenario.chat 줄들의 정규 바이트를 이어 붙인 SHA-256 (end.lines_sha256)."""
    h = hashlib.sha256()
    for c in chats:
        h.update(canonical_line(c))
    return h.hexdigest()


def make_scenario_id(yyyymmdd: str, sha: str) -> str:
    sid = f"SC-{yyyymmdd}-{sha[:4]}"
    if not SCENARIO_ID_RE.match(sid):
        raise ScenarioError("시나리오 ID 를 만들지 못했습니다(날짜·해시 형식)")
    return sid


def line_id(n: int) -> str:
    return f"s{n:04d}"


# ---------------------------------------------------------------- 시각

def show_vod_ms(chat: Mapping, header: Mapping) -> int | None:
    """보일 VOD 시각. show_vod_ms 가 있으면 그것, show_rel_ms 면 구간 시작 + 상대 시각. 둘 다 아니면 None."""
    v = chat.get("show_vod_ms")
    if _is_int(v):
        return v
    r = chat.get("show_rel_ms")
    rng = header.get("range") if isinstance(header, Mapping) else None
    a = rng.get("vod_from_ms") if isinstance(rng, Mapping) else None
    if _is_int(r) and _is_int(a):
        return a + r
    return None


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _range(header: Mapping) -> tuple[int, int] | None:
    rng = header.get("range") if isinstance(header, Mapping) else None
    if not isinstance(rng, Mapping):
        return None
    a, b = rng.get("vod_from_ms"), rng.get("vod_to_ms")
    if _is_int(a) and _is_int(b):
        return a, b
    return None


# ---------------------------------------------------------------- 검사

def split_records(records: Sequence[Mapping]) -> tuple[Mapping | None, list[Mapping], Mapping | None]:
    """(header, chats, end). 순서가 틀려도 나눠만 준다 — 순서 검사는 check_records."""
    header = records[0] if records and records[0].get("kind") == K_HEADER else None
    end = records[-1] if len(records) >= 2 and records[-1].get("kind") == K_END else None
    lo = 1 if header is not None else 0
    hi = len(records) - 1 if end is not None else len(records)
    return header, [r for r in records[lo:hi]], end


def check_records(records: Sequence[Mapping], *, schema_check: Callable[[Mapping], list[str]] | None = None,
                  project_id: str | None = None) -> list[str]:
    """시나리오 줄 목록 전체 검사. 문제 목록(빈 목록이면 통과).

    schema_check(rec) → 문제 목록 을 주면 줄마다 스키마 검사도 한다(app.scenario_store 가 넘긴다).
    project_id 를 주면 header.project_id 가 같은지도 본다.
    """
    out: list[str] = []
    if not records:
        return ["빈 시나리오입니다"]
    for i, r in enumerate(records, start=1):
        if not isinstance(r, Mapping):
            out.append(f"{i}줄: object 가 아닙니다")
            continue
        if schema_check is not None:
            out += [f"{i}줄 {p}" for p in schema_check(r)]
    if out:
        return out

    if records[0].get("kind") != K_HEADER:
        return ["첫 줄이 scenario.header 가 아닙니다"]
    if len(records) < 2 or records[-1].get("kind") != K_END:
        return ["마지막 줄이 scenario.end 가 아닙니다(쓰다 끊긴 파일)"]
    header, chats, end = split_records(records)
    for i, r in enumerate(chats, start=2):
        if r.get("kind") != K_CHAT:
            out.append(f"{i}줄: 가운데 줄은 scenario.chat 이어야 합니다")
    if out:
        return out
    if len(chats) > MAX_CHAT_LINES:
        return [f"채팅 줄이 너무 많습니다({len(chats)}줄)"]

    if header.get("schema") != SCHEMA_ID:
        out.append("header.schema 가 chatcoach-scenario-v1 이 아닙니다")
    if project_id is not None and header.get("project_id") != project_id:
        out.append("header.project_id 가 이 프로젝트와 다릅니다")
    rng = _range(header)
    if rng is None or rng[0] >= rng[1]:
        out.append("header.range 가 비었거나 거꾸로입니다")
    ext = header.get("external")
    if not isinstance(ext, Mapping) or ext.get("enabled") is not False:
        out.append("외부 AI 는 꺼져 있어야 합니다(external.enabled=false)")

    # 건수·해시·ID
    if end.get("lines") != len(chats):
        out.append(f"end.lines({end.get('lines')}) 와 채팅 줄 수({len(chats)})가 다릅니다")
    sha = lines_sha256(chats)
    if end.get("lines_sha256") != sha:
        out.append("end.lines_sha256 이 채팅 줄과 맞지 않습니다")
    m = SCENARIO_ID_RE.match(str(header.get("scenario_id", "")))
    if not m or m.group(2) != sha[:4]:
        out.append("scenario_id 끝 4자가 lines_sha256 앞 4자와 다릅니다")
    counts = header.get("counts") if isinstance(header.get("counts"), Mapping) else {}
    for src in SOURCES:
        n = sum(1 for c in chats if c.get("source") == src)
        if counts.get(src) != n:
            out.append(f"header.counts.{src}({counts.get(src)}) 와 실제 줄 수({n})가 다릅니다")

    # 출처 허용
    own = header.get("project_id")
    inputs = header.get("inputs") if isinstance(header.get("inputs"), Mapping) else {}
    allowed_from = {p.get("project_id") for p in (inputs.get("from_projects") or ()) if isinstance(p, Mapping)}
    if own in allowed_from:
        out.append("inputs.from_projects 에 이 프로젝트가 들어 있습니다(같은 방송은 적지 않습니다)")

    prev_t = None
    seen_lines: set[str] = set()
    seen_gen: set[str] = set()
    seen_from: set[tuple] = set()
    seen_replay: set[str] = set()
    for n, c in enumerate(chats, start=1):
        lid = c.get("line_id")
        where = f"{lid}"
        if lid != line_id(n):
            out.append(f"{where}: line_id 는 차례대로 {line_id(n)} 이어야 합니다")
        if lid in seen_lines:
            out.append(f"{where}: line_id 가 겹칩니다")
        seen_lines.add(lid)
        t = show_vod_ms(c, header)
        if t is None:
            out.append(f"{where}: 보일 시각이 없습니다")
            continue
        if rng is not None and not (rng[0] <= t < rng[1]):
            out.append(f"{where}: 보일 시각이 훈련 구간 밖입니다")
        if prev_t is not None and t < prev_t:
            out.append(f"{where}: 보일 시각 순이 아닙니다")
        prev_t = t
        src = c.get("source")
        if src == REPLAY:
            cid = c.get("chat_id")
            if cid in seen_replay:
                out.append(f"{where}: 같은 replay chat_id 가 두 번 나옵니다")
            seen_replay.add(cid)
        elif src == RETRIEVED:
            key = (c.get("from_project_id"), c.get("from_chat_id"))
            if key in seen_from:
                out.append(f"{where}: 같은 원 채팅을 두 번 가져왔습니다")
            seen_from.add(key)
            fp, scope, ft = c.get("from_project_id"), c.get("from_scope"), c.get("from_t_ms")
            if fp == own:
                if scope != SAME_BROADCAST:
                    out.append(f"{where}: 같은 방송에서 가져온 줄은 from_scope=same_broadcast 이어야 합니다")
                if rng is not None and _is_int(ft) and rng[0] <= ft < rng[1]:
                    out.append(f"{where}: 같은 방송의 훈련 구간 안 채팅은 가져오지 않습니다")
            else:
                if scope != SAME_CHANNEL:
                    out.append(f"{where}: 다른 방송에서 가져온 줄은 from_scope=same_channel 이어야 합니다")
                if fp not in allowed_from:
                    out.append(f"{where}: 가져온 프로젝트가 inputs.from_projects 에 없습니다")
        elif src == GENERATED:
            gid = c.get("gen_id")
            if gid in seen_gen:
                out.append(f"{where}: gen_id 가 겹칩니다")
            seen_gen.add(gid)
            emotes = set(c.get("emotes") or ())
            for tok in EMOTE_TOKEN_RE.findall(str(c.get("text", ""))):
                if tok not in emotes:
                    out.append(f"{where}: 본문의 이모티콘 코드가 emotes 허용 목록에 없습니다")
                    break
        if src in VIRTUAL_SOURCES:
            if not DISPLAY_MASK_RE.match(str(c.get("display_mask", ""))):
                out.append(f"{where}: 표시 이름이 가명 모양이 아닙니다")
            ch = c.get("checks")
            if isinstance(ch, Mapping):
                for k in ("nickname_hit", "blocked", "llm_flag"):
                    if ch.get(k, 0) != 0:
                        out.append(f"{where}: 거르기에 걸린 줄({k})이 들어 있습니다")
    return out


# ---------------------------------------------------------------- 조립 (엔진 H-2 가 쓴다)

def assemble(header: Mapping, chats: Iterable[Mapping], *, yyyymmdd: str) -> list[dict]:
    """파일에 쓸 줄 목록 [header, chat…, end] 을 만든다. 입력 dict 는 고치지 않는다.

    - chat 줄은 보일 VOD 시각 순(같으면 넣은 순)으로 세우고 line_id 를 s0001 부터 다시 매긴다.
    - header 의 kind·schema·counts·scenario_id 를 채운다(scenario_id = SC-<yyyymmdd>-<lines_sha256 앞 4자>).
    - external 이 없으면 {'enabled': false}.
    검사는 하지 않는다 — 쓰기 전에 check_records 로.
    """
    h = dict(header)
    h["kind"] = K_HEADER
    h["schema"] = SCHEMA_ID
    h.setdefault("external", {"enabled": False})
    items = []
    for i, c in enumerate(chats):
        if not isinstance(c, Mapping):
            raise ScenarioError("채팅 줄이 object 가 아닙니다")
        t = show_vod_ms(c, h)
        if t is None:
            raise ScenarioError(f"{i + 1}번째 채팅 줄에 보일 시각이 없습니다")
        items.append((t, i, c))
    items.sort(key=lambda x: (x[0], x[1]))
    out_chats = []
    for n, (_, _, c) in enumerate(items, start=1):
        d = {"kind": K_CHAT, "line_id": line_id(n)}
        for k, v in c.items():
            if k not in ("kind", "line_id"):
                d[k] = v
        out_chats.append(d)
    h["counts"] = {s: sum(1 for c in out_chats if c.get("source") == s) for s in SOURCES}
    sha = lines_sha256(out_chats)
    h["scenario_id"] = make_scenario_id(yyyymmdd, sha)
    end = {"kind": K_END, "lines": len(out_chats), "lines_sha256": sha}
    return [h, *out_chats, end]


# ---------------------------------------------------------------- 화면용 섞기 (H-4)

@dataclass(frozen=True)
class DisplayRow:
    """채팅 패널 한 행. 실제 채팅이면 message 는 원 dict(고치지 않는다), 가상이면 None."""
    key: str                    # 실제 = chat_id, 가상 = '<scenario_id>:<line_id>' (20 7-2 짝 키)
    vod_ms: int
    source: str                 # replay · retrieved · generated
    virtual: bool
    badge: str                  # '' · '가져옴' · '생성' (hide_badges 면 '')
    display_name: str | None    # 실제 = 원 가림 닉네임, 가상 = 가명
    text: str
    chat_id: str | None = None          # 실제 채팅만
    scenario_id: str | None = None      # 가상만
    line_id: str | None = None          # 가상만
    message: Mapping | None = field(default=None, compare=False, repr=False)


@dataclass
class MergeResult:
    rows: list[DisplayRow]
    counts: dict  # 숫자만: real · retrieved · generated · skipped_missing · skipped_mismatch · skipped_kind · renamed · real_no_time · real_out_of_range


def _real_time(m: Mapping) -> int | None:
    t = m.get("t_ms")
    return t if _is_int(t) else None


def merge_for_display(real_messages: Sequence[Mapping], records: Sequence[Mapping] | None, *,
                      resolve: Callable[[str, str], Mapping | None] | None = None,
                      clip_to_range: bool = True, hide_badges: bool = False) -> MergeResult:
    """실제 채팅 + 시나리오 → 화면용 새 목록(시각 순). 둘 다 고치지 않는다.

    real_messages: 이 프로젝트 messages.jsonl 의 줄(dict). 시각(t_ms)이 없는 줄은 놓을 수 없어 뺀다(수만 셈).
    records: check_records 를 통과한 시나리오 줄 목록. None 이면 실제 채팅만(F 와 같음).
    resolve(from_project_id, from_chat_id) → 원 채팅 dict 또는 None: retrieved 본문을 읽는 곳(앱이 원
      messages.jsonl 에서). 없거나 None 이면 그 줄은 건너뛴다. 원 채팅의 t_ms 가 from_t_ms 와 다르거나
      일반 채팅(kind=text)이 아니면 건너뛴다. 원 채팅의 닉네임·가림 닉네임은 쓰지 않는다.
    clip_to_range: 시나리오가 있으면 실제 채팅을 header.range [from, to) 안으로 줄인다.
    scenario 에 replay 줄이 있으면 실제 채팅은 그 chat_id 만 보인다(없으면 구간의 실제 채팅 전부, 첫 판).
    줄 순서: 시각 → 실제 먼저 → 넣은 순.
    """
    counts = {"real": 0, "retrieved": 0, "generated": 0, "skipped_missing": 0, "skipped_mismatch": 0,
              "skipped_kind": 0, "renamed": 0, "real_no_time": 0, "real_out_of_range": 0}
    header, chats, _ = split_records(list(records)) if records else (None, [], None)
    rng = _range(header) if header is not None else None
    sid = header.get("scenario_id") if header is not None else None
    replay_only = {c.get("chat_id") for c in chats if c.get("source") == REPLAY}

    items: list[tuple[int, int, int, DisplayRow]] = []
    real_names: set[str] = set()
    for i, m in enumerate(real_messages):
        t = _real_time(m)
        if t is None:
            counts["real_no_time"] += 1
            continue
        if clip_to_range and rng is not None and not (rng[0] <= t < rng[1]):
            counts["real_out_of_range"] += 1
            continue
        cid = m.get("chat_id")
        if replay_only and cid not in replay_only:
            continue
        name = m.get("nickname_masked")
        if isinstance(name, str):
            real_names.add(name)
        row = DisplayRow(key=str(cid), vod_ms=t, source=REPLAY, virtual=False, badge="",
                         display_name=name if isinstance(name, str) else None,
                         text=str(m.get("text", "")), chat_id=cid, message=m)
        items.append((t, 0, i, row))
        counts["real"] += 1

    virtual: list[tuple[int, int, Mapping, str]] = []   # (시각, 차례, 줄, 본문)
    for n, c in enumerate(chats, start=1):
        src = c.get("source")
        if src not in VIRTUAL_SOURCES:
            continue
        t = show_vod_ms(c, header)
        if t is None:
            counts["skipped_mismatch"] += 1
            continue
        if src == RETRIEVED:
            orig = resolve(c.get("from_project_id"), c.get("from_chat_id")) if resolve else None
            if not isinstance(orig, Mapping):
                counts["skipped_missing"] += 1
                continue
            if orig.get("kind") != "text":
                counts["skipped_kind"] += 1
                continue
            if _real_time(orig) != c.get("from_t_ms") or orig.get("chat_id") not in (None, c.get("from_chat_id")):
                counts["skipped_mismatch"] += 1
                continue
            on = orig.get("nickname_masked")
            if isinstance(on, str):
                real_names.add(on)   # 원 가림 이름과 같은 표시는 피한다(20 8장 3번)
            text = str(orig.get("text", ""))
        else:
            text = str(c.get("text", ""))
        virtual.append((t, n, c, text))

    taken = set(real_names) | {c.get("display_mask") for _, _, c, _ in virtual}
    for t, n, c, text in virtual:
        src = c.get("source")
        mask = c.get("display_mask")
        if not isinstance(mask, str) or not DISPLAY_MASK_RE.match(mask) or mask in real_names:
            k = n
            while f"{PSEUDONYM_PREFIX}{k}" in taken:
                k += 1
            mask = f"{PSEUDONYM_PREFIX}{k}"
            taken.add(mask)
            counts["renamed"] += 1
        lid = c.get("line_id")
        row = DisplayRow(key=f"{sid}:{lid}", vod_ms=t, source=src, virtual=True,
                         badge="" if hide_badges else BADGES[src], display_name=mask, text=text,
                         scenario_id=sid, line_id=lid)
        items.append((t, 1, n, row))
        counts[src] += 1

    items.sort(key=lambda x: (x[0], x[1], x[2]))
    return MergeResult(rows=[x[3] for x in items], counts=counts)


__all__ = [
    "SCHEMA_ID", "K_HEADER", "K_CHAT", "K_END", "LINE_KINDS", "REPLAY", "RETRIEVED", "GENERATED", "SOURCES",
    "VIRTUAL_SOURCES", "SAME_BROADCAST", "SAME_CHANNEL", "BADGES", "SCENARIO_ID_RE", "LINE_ID_RE",
    "DISPLAY_MASK_RE", "ScenarioError", "DisplayRow", "MergeResult", "canonical_line", "lines_sha256",
    "make_scenario_id", "line_id", "show_vod_ms", "split_records", "check_records", "assemble",
    "merge_for_display",
]
