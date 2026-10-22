"""주제 흐름(topic-flow) 읽기·검사와 방송 요약 숫자 계산 (08 D-01 방송 분석, D-17 5·8).

주제 흐름은 엔진(engine/topics.py)이 내고 앱은 보이기만 한다(D-17 5). 이 모듈은 순수 계산만 한다
(파일·시계·네트워크 없음, tests/test_layers.py). 파일을 읽는 쪽이 dict 를 넘겨 준다.

방송 요약 표(D-01, 17 6-1 의 2번)는 주제 덩어리마다 한 줄이다.

| 열 | 뜻 |
|---|---|
| # | 덩어리 번호(0부터) |
| 시작 · 끝 | VOD 시각 |
| 주제 | 이름 키워드(2~6자) 1~3개를 ' · ' 로 이은 것 |
| 채팅 | 덩어리 안 일반 채팅 수(기본 kind == text) |
| 분당 채팅 | 채팅 ÷ 덩어리 길이(분) |
| 반응 | 반응 라벨이 붙은 채팅 수(입력으로 받는다 — 사람 판정이든 해제된 엔진 판정이든 부르는 쪽이 고른다) |
| 반응률 | 반응 ÷ 채팅 |
| 질문 | 질문형 채팅 수(입력 dict 의 열쇠) |
| 놓친 질문 | 질문형 채팅 중 반응이 없는 것(입력 dict 값이 False) |

저장·내보내기에는 숫자·주제 이름만 넣는다(D-17 8). 채팅 본문·전사문은 여기서 다루지 않는다.
"""
from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .timecode import fmt_display

SCHEMA = "chatcoach/topic-flow"
SCHEMA_VERSION = 1
TIME_AXIS = "vod_ms"

KEYWORD_MIN_LEN = 2
KEYWORD_MAX_LEN = 6
MAX_KEYWORDS = 8          # 덩어리마다 keywords 최대 개수
MAX_NAME_KEYWORDS = 3     # 주제 이름에 쓰는 키워드 최대 개수
# 키워드: 한글 음절·영문 소문자·숫자 2~6자 (기호·공백·낱자 없음 — 문장을 담을 수 없게)
KEYWORD_RE = re.compile(r"^[0-9a-z가-힣]{%d,%d}$" % (KEYWORD_MIN_LEN, KEYWORD_MAX_LEN))
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
NAME_SEP = " · "

_TOP_KEYS = ("schema", "schema_version", "run_id", "created_at", "engine", "target", "time_axis",
             "window_ms", "range", "stats", "blocks", "gaps")
_REQUIRED_TOP = ("schema", "schema_version", "run_id", "created_at", "engine", "target", "time_axis",
                 "window_ms", "range", "blocks")
_BLOCK_KEYS = ("index", "start_ms", "end_ms", "keywords", "name_keywords", "top_keyword", "change_ms",
               "boundary_score", "utterances", "chats")
_GAP_KEYS = ("at_ms", "similarity", "depth")


# ── 검사 ────────────────────────────────────────────────────────────────

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)) and math.isfinite(v)


_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha_or_none(v) -> bool:
    return v is None or (isinstance(v, str) and bool(_SHA_RE.match(v)))


def is_keyword(s) -> bool:
    return isinstance(s, str) and bool(KEYWORD_RE.match(s))


def validate(doc) -> list[str]:
    """topic-flow 문서 검사. 문제 목록(빈 목록이면 통과). schemas/topic-flow.schema.json 과 같은 규칙을 손으로 본다."""
    p: list[str] = []
    if not isinstance(doc, Mapping):
        return ["문서가 객체가 아님"]
    for k in _REQUIRED_TOP:
        if k not in doc:
            p.append(f"{k} 없음")
    extra = sorted(set(doc) - set(_TOP_KEYS))
    if extra:
        p.append(f"모르는 칸 {extra}")
    if p:
        return p
    if doc["schema"] != SCHEMA:
        p.append("schema 값")
    if doc["schema_version"] != SCHEMA_VERSION:
        p.append("schema_version 값")
    if not (isinstance(doc["run_id"], str) and RUN_ID_RE.match(doc["run_id"])):
        p.append("run_id 형식")
    if not isinstance(doc["created_at"], str) or not doc["created_at"]:
        p.append("created_at 형식")
    eng = doc["engine"]
    if not (isinstance(eng, Mapping) and isinstance(eng.get("name"), str) and eng.get("name")
            and isinstance(eng.get("version"), str) and eng.get("version")):
        p.append("engine.name·version")
    elif set(eng) - {"name", "version", "config_sha256", "embedding"}:
        p.append("engine 모르는 칸")
    elif not _sha_or_none(eng.get("config_sha256")) or not (eng.get("embedding") is None
                                                            or isinstance(eng.get("embedding"), str)):
        p.append("engine.config_sha256·embedding 형식")
    if not isinstance(doc["target"], Mapping):
        p.append("target 형식")
    else:
        tg = doc["target"]
        if set(tg) - {"video_no", "messages_sha256", "asr_sha256"}:
            p.append("target 모르는 칸")
        if not (_sha_or_none(tg.get("messages_sha256")) and _sha_or_none(tg.get("asr_sha256"))
                and (tg.get("video_no") is None or isinstance(tg.get("video_no"), str))):
            p.append("target 값 형식")
    if doc["time_axis"] != TIME_AXIS:
        p.append("time_axis 값")
    if not (_is_int(doc["window_ms"]) and doc["window_ms"] > 0):
        p.append("window_ms 양의 정수 아님")
    rng = doc["range"]
    if not (isinstance(rng, Mapping) and _is_int(rng.get("start_ms")) and _is_int(rng.get("end_ms"))
            and rng["start_ms"] <= rng["end_ms"] and set(rng) == {"start_ms", "end_ms"}):
        p.append("range 형식")
        rng = None
    st = doc.get("stats")
    if st is not None:
        if not isinstance(st, Mapping) or any(not _is_num(v) for v in st.values()):
            p.append("stats 는 숫자만")
    blocks = doc["blocks"]
    if not isinstance(blocks, list):
        p.append("blocks 배열 아님")
        blocks = []
    prev_end = None
    for i, b in enumerate(blocks):
        w = f"blocks[{i}]"
        if not isinstance(b, Mapping):
            p.append(f"{w} 객체 아님")
            continue
        miss = [k for k in _BLOCK_KEYS if k not in b]
        if miss:
            p.append(f"{w} {miss} 없음")
            continue
        if set(b) - set(_BLOCK_KEYS):
            p.append(f"{w} 모르는 칸")
        if b["index"] != i:
            p.append(f"{w}.index 순서")
        s, e = b["start_ms"], b["end_ms"]
        if not (_is_int(s) and _is_int(e) and s < e):
            p.append(f"{w} 시각")
            continue
        if prev_end is not None and s != prev_end:
            p.append(f"{w} 앞 덩어리와 이어지지 않음")
        if rng is not None and (s < rng["start_ms"] or e > rng["end_ms"]):
            p.append(f"{w} range 밖")
        prev_end = e
        kws = b["keywords"]
        if not (isinstance(kws, list) and len(kws) <= MAX_KEYWORDS and all(is_keyword(k) for k in kws)
                and len(set(kws)) == len(kws)):
            p.append(f"{w}.keywords 형식(2~6자 키워드, 최대 {MAX_KEYWORDS}개)")
            kws = []
        nk = b["name_keywords"]
        if not (isinstance(nk, list) and len(nk) <= MAX_NAME_KEYWORDS and all(k in kws for k in nk)
                and len(set(nk)) == len(nk)):
            p.append(f"{w}.name_keywords 형식(keywords 안에서 최대 {MAX_NAME_KEYWORDS}개)")
        tk = b["top_keyword"]
        if tk is None:
            if kws:
                p.append(f"{w}.top_keyword 없음")
        elif not (is_keyword(tk) and kws and tk == kws[0]):
            p.append(f"{w}.top_keyword 는 keywords[0]")
        if i == 0:
            if b["change_ms"] is not None or b["boundary_score"] is not None:
                p.append(f"{w} 첫 덩어리는 change_ms·boundary_score 가 null")
        else:
            if b["change_ms"] != s:
                p.append(f"{w}.change_ms 는 start_ms")
            if not _is_num(b["boundary_score"]):
                p.append(f"{w}.boundary_score 숫자 아님")
        for k in ("utterances", "chats"):
            if not (_is_int(b[k]) and b[k] >= 0):
                p.append(f"{w}.{k} 음이 아닌 정수 아님")
    if blocks and rng is not None and not p:
        if blocks[0]["start_ms"] != rng["start_ms"] or blocks[-1]["end_ms"] != rng["end_ms"]:
            p.append("blocks 가 range 를 다 덮지 않음")
    gaps = doc.get("gaps", [])
    if not isinstance(gaps, list):
        p.append("gaps 배열 아님")
    else:
        for i, g in enumerate(gaps):
            if not (isinstance(g, Mapping) and set(g) == set(_GAP_KEYS) and _is_int(g["at_ms"])
                    and (g["similarity"] is None or _is_num(g["similarity"]))
                    and (g["depth"] is None or _is_num(g["depth"]))):
                p.append(f"gaps[{i}] 형식")
    return p


# ── 읽기 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TopicBlock:
    index: int
    start_ms: int
    end_ms: int
    keywords: tuple[str, ...]
    name_keywords: tuple[str, ...]
    top_keyword: str | None
    change_ms: int | None         # 앞 덩어리에서 이 덩어리로 바뀐 시각(첫 덩어리는 None)
    boundary_score: float | None  # 바뀐 정도(경계 깊이, 클수록 뚜렷)
    utterances: int
    chats: int

    @property
    def name(self) -> str:
        return NAME_SEP.join(self.name_keywords)

    @property
    def minutes(self) -> float:
        return (self.end_ms - self.start_ms) / 60000.0


def blocks_from_doc(doc: Mapping) -> list[TopicBlock]:
    """검사를 통과한 문서의 덩어리 목록. 문제가 있으면 ValueError(무엇이 틀렸는지 앞 5개)."""
    probs = validate(doc)
    if probs:
        raise ValueError(f"주제 흐름 형식 오류 {len(probs)}건: {probs[:5]}")
    return [TopicBlock(b["index"], b["start_ms"], b["end_ms"], tuple(b["keywords"]), tuple(b["name_keywords"]),
                       b["top_keyword"], b["change_ms"],
                       None if b["boundary_score"] is None else float(b["boundary_score"]),
                       b["utterances"], b["chats"]) for b in doc["blocks"]]


def block_index_at(blocks: Sequence[TopicBlock], t_ms: int) -> int | None:
    """t_ms(VOD)가 속한 덩어리 번호. 범위 밖이면 None. 덩어리는 [start, end) 이고 마지막 덩어리만 end 를 포함한다."""
    if not blocks or not _is_int(t_ms):
        return None
    starts = [b.start_ms for b in blocks]
    i = bisect.bisect_right(starts, t_ms) - 1
    if i < 0:
        return None
    b = blocks[i]
    if t_ms < b.end_ms or (i == len(blocks) - 1 and t_ms == b.end_ms):
        return i
    return None


def change_times(blocks: Sequence[TopicBlock]) -> list[int]:
    """주제가 바뀐 시각들(타임라인 '주제' 트랙의 경계 표시용)."""
    return [b.change_ms for b in blocks if b.change_ms is not None]


# ── 방송 요약 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SummaryRow:
    index: int | None             # None = 합계 줄
    start_ms: int
    end_ms: int
    name: str
    top_keyword: str | None
    chats: int
    chats_per_min: float | None   # 길이 0 이면 None
    reacted: int
    reaction_rate: float | None   # 채팅 0 이면 None
    questions: int
    missed_questions: int
    questions_unknown: int        # 질문 dict 값이 None(아직 모름)

    def as_dict(self) -> dict:
        return {"index": self.index, "start_ms": self.start_ms, "end_ms": self.end_ms, "name": self.name,
                "top_keyword": self.top_keyword, "chats": self.chats, "chats_per_min": self.chats_per_min,
                "reacted": self.reacted, "reaction_rate": self.reaction_rate, "questions": self.questions,
                "missed_questions": self.missed_questions, "questions_unknown": self.questions_unknown}


@dataclass(frozen=True)
class BroadcastSummary:
    rows: tuple[SummaryRow, ...]
    total: SummaryRow
    outside_chats: int            # 어느 덩어리에도 들지 않은 채팅(주제 흐름 범위 밖)
    unmatched_reactions: int      # 채팅 목록에 없는 반응 chat_id 수
    unmatched_questions: int      # 채팅 목록에 없는 질문 chat_id 수


def _rate(n: int, d: float) -> float | None:
    return round(n / d, 3) if d > 0 else None


def summarize(blocks: Sequence[TopicBlock], messages: Iterable[Mapping], *,
              reacted_chat_ids: Iterable[str] = (), questions: Mapping[str, bool | None] | None = None,
              kinds: Iterable[str] | None = ("text",)) -> BroadcastSummary:
    """덩어리별 채팅 수·분당 채팅·반응 수·반응률·질문·놓친 질문.

    messages        채팅 레코드(chat_id·t_ms·kind). t_ms 가 정수가 아닌 것은 뺀다. kinds 가 None 이면 모든 종류.
    reacted_chat_ids 반응 라벨이 붙은 채팅 id(같은 채팅 여러 번이면 한 번만 센다).
    questions       {chat_id: 반응 있음?} — 질문형 채팅만 넣는다. False = 놓친 질문, None = 아직 모름.
                    질문인지 고르는 일과 반응 여부는 부르는 쪽이 정한다(이 모듈은 본문을 보지 않는다).
    반응·질문은 채팅 목록에 있고 kinds 를 통과한 채팅만 그 채팅 시각의 덩어리로 센다. kinds 로 걸러진 채팅
    (후원 등)에 붙은 반응·질문은 세지 않는다 — 분모(채팅 수)와 분자(반응 수)가 같은 채팅 묶음이어야 반응률이
    0~1 안에 든다. 걸러진 채팅은 '채팅 목록에 없음'(unmatched)으로도 세지 않는다.
    """
    kind_set = None if kinds is None else set(kinds)
    reacted = set(reacted_chat_ids or ())
    qs = dict(questions or {})
    n = len(blocks)
    chats = [0] * n
    rcount = [0] * n
    qcount = [0] * n
    missed = [0] * n
    unknown = [0] * n
    outside = 0
    t_of: dict[str, int] = {}
    excluded: set[str] = set()     # kinds 로 걸러진 채팅 id (반응·질문도 세지 않는다)
    for m in messages or ():
        if not isinstance(m, Mapping):
            continue
        cid, t = m.get("chat_id"), m.get("t_ms")
        if not isinstance(cid, str) or not _is_int(t):
            continue
        if kind_set is not None and m.get("kind") not in kind_set:
            if cid not in t_of:
                excluded.add(cid)
            continue
        excluded.discard(cid)
        t_of.setdefault(cid, t)
        i = block_index_at(blocks, t)
        if i is None:
            outside += 1
        else:
            chats[i] += 1
    unmatched_r = 0
    for cid in reacted:
        t = t_of.get(cid)
        if t is None:
            if cid not in excluded:
                unmatched_r += 1
            continue
        i = block_index_at(blocks, t)
        if i is not None:
            rcount[i] += 1
    unmatched_q = 0
    for cid, v in qs.items():
        t = t_of.get(cid)
        if t is None:
            if cid not in excluded:
                unmatched_q += 1
            continue
        i = block_index_at(blocks, t)
        if i is None:
            continue
        qcount[i] += 1
        if v is False:
            missed[i] += 1
        elif v is None:
            unknown[i] += 1
    rows = []
    for i, b in enumerate(blocks):
        rows.append(SummaryRow(i, b.start_ms, b.end_ms, b.name, b.top_keyword, chats[i], _rate(chats[i], b.minutes),
                               rcount[i], _rate(rcount[i], chats[i]), qcount[i], missed[i], unknown[i]))
    if blocks:
        s0, e0 = blocks[0].start_ms, blocks[-1].end_ms
    else:
        s0 = e0 = 0
    tc, tr = sum(chats), sum(rcount)
    total = SummaryRow(None, s0, e0, "합계", None, tc, _rate(tc, (e0 - s0) / 60000.0), tr, _rate(tr, tc),
                       sum(qcount), sum(missed), sum(unknown))
    return BroadcastSummary(tuple(rows), total, outside, unmatched_r, unmatched_q)


# 화면 표 모양: (칸 열쇠, 머리글)
SUMMARY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("index", "#"), ("start", "시작"), ("end", "끝"), ("name", "주제"), ("chats", "채팅"),
    ("chats_per_min", "분당 채팅"), ("reacted", "반응"), ("reaction_rate", "반응률"),
    ("questions", "질문"), ("missed_questions", "놓친 질문"),
)


def _fmt_num(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.0f}%" if pct else f"{v:.1f}"


def _row_cells(r: SummaryRow) -> list[str]:
    q = str(r.questions) + (f" (모름 {r.questions_unknown})" if r.questions_unknown else "")
    return ["" if r.index is None else str(r.index + 1), fmt_display(r.start_ms), fmt_display(r.end_ms),
            r.name or "-", str(r.chats), _fmt_num(r.chats_per_min), str(r.reacted),
            _fmt_num(r.reaction_rate, pct=True), q, str(r.missed_questions)]


def summary_table(summary: BroadcastSummary) -> list[list[str]]:
    """화면에 그대로 놓을 글자 표: 머리글 1줄 + 덩어리마다 1줄 + 합계 1줄. 번호는 1부터 보인다."""
    out = [[h for _, h in SUMMARY_COLUMNS]]
    out += [_row_cells(r) for r in summary.rows]
    out.append(_row_cells(summary.total))
    return out


def summary_export(summary: BroadcastSummary, *, topic_run_id: str | None = None) -> dict:
    """저장·내보내기용 dict. 숫자·주제 이름(키워드)만 담는다(D-17 8)."""
    return {"format": "chatcoach.broadcast-summary", "format_version": 1, "topic_run_id": topic_run_id,
            "rows": [r.as_dict() for r in summary.rows], "total": summary.total.as_dict(),
            "outside_chats": summary.outside_chats, "unmatched_reactions": summary.unmatched_reactions,
            "unmatched_questions": summary.unmatched_questions}
