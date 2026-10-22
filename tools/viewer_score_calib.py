"""시청자 훈련(G) 채점 보정 자료 — 손라벨에서 '반응받은 채팅' 과 같은 구간의 다른 채팅의 세 축 분포 (22 7-1 의 1·2, D-21 1·4).

무엇을 재나
  손라벨(`labels\\manual-*.csv`)이 '스트리머가 직접 반응했다' 고 적은 채팅(반응받음)과, 같은 구간(반응받은 채팅의 첫 시각 ~
  끝 시각)의 다른 일반 채팅(반응 없음)을 채점기(app/core/viewer_score.py)로 매겨 축마다 good·ok·weak 비율을 낸다.
  '반응받은 쪽에서 good 비율이 더 높은가' 를 숫자(차이 %p, 피셔 정확 검정 양쪽 p)로 보고, good 의 **lift**(반응받음 good 률
  ÷ 반응 없음 good 률)와 **정밀도**(good 가운데 반응받은 쪽 비율)도 낸다. 문턱 후보(candidates)는 정밀도 우선(D-21 2)에
  맞춰 lift 가 base 보다 높은 판만 고른다(good 차이는 보조 — 문턱을 느슨하게 해 양쪽 good 이 함께 늘어도 커지므로).
  base 판에는 주제 흐름 누출 크기(flow_presend: flow_hit 가운데 겹친 낱말이 보낸 시각 앞 전사에도 있는 수)도 센다.
  같은 시각 근처(±near 초)의
  다른 채팅만 따로 묶은 '가까운 반응 없음' 과, 낱말(2자 이상)이 하나라도 있는 채팅만 묶은 '낱말 있는 반응 없음'
  ('ㅋㅋ'·이모티콘만인 채팅이 차이를 부풀리지 않는지 보려고)도 낸다.
  문턱 몇 개를 바꿔 본 판(변형)과, 22 5-2 의 3(flow_head2: 주제 흐름 2자 머리 허용)·4(habit_soft_ok_only: 끝 표지·흔한
  첫 낱말은 ok 까지만)를 켜고 끈 차이도 낸다. **기본값은 바꾸지 않는다** — 표본 30건이라 '후보' 로만 적는다.

채점 방법(실제 시청자 채팅을 '연습 채팅' 처럼 본다)
  - 보낸 시각 = 그 채팅의 VOD 시각(messages.jsonl 의 t_ms — 손라벨 chat_vod_ms 와 같은 축).
  - 맥락 = 주제 흐름 덩어리(--topics 파일 또는 해제된 run) + VOD 로 보정한 전사 낱말(derived\\asr-words.json, 상태 ok 일 때만)
    (--topics 는 <P>\\derived\\asr-topic-flow.json 처럼 보호 구역 안. 프로젝트 안의 보호 구역 밖 파일은 거부, 03 2-1)
    + 그 시각 앞 60초의 다른 시청자 채팅(같은 시청자의 글과 자기 자신은 뺀다 — 자기 글은 history 로 본다).
  - history = 같은 시청자가 앞 10분 안에 쓴 일반 채팅(오래된 것 → 최근).
  - 일반 채팅 = kind text · status NORMAL. 맥락 채팅에는 후원 글도 넣는다(화면에 보이는 글).

읽기만 한다: project.json, labels\\manual-*.csv(chat_id·chat_vod_ms·grade 칸만, note 칸은 읽지 않는다), chat\\messages.jsonl,
derived\\asr-words.json, 주제 흐름 파일. 아무것도 쓰지 않는다(--out 을 주면 그 파일 하나만).
출력은 **숫자·코드만**이다: 채팅 본문·닉네임·viewer_id·chat_id·낱말은 표준 출력·결과 파일·로그 어디에도 넣지 않는다.
시범 프로젝트(pilot-* 또는 시범 기록에 적힌 것)는 열지 않고 거부한다(D-15 8, D-17 4).

사용(저장소 루트, 앱 .venv)
  python tools/viewer_score_calib.py --project <P> [--topics <topic-flow.json>] [--labels <csv>]
         [--grades direct,likely] [--near-sec 30] [--out <결과.json>] [--quick]
종료 코드: 0 정상 · 1 거부(시범·손라벨 없음) · 2 입력 오류
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
import csv
import json
import math
import os
import time
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import viewer_score as vs  # noqa: E402

TOOL = "viewer_score_calib"
TOOL_VERSION = "0.1"
DEFAULT_GRADES = ("direct", "likely")
DEFAULT_NEAR_SEC = 30
HISTORY_SPAN_MS = 10 * 60_000          # history: 같은 시청자가 앞 10분 안에 쓴 글
HISTORY_MAX = 50
CTX_CHAT_SPAN_MS = 60_000              # 맥락 채팅: 앞 60초(copy_window 최대값)
MESSAGES_MAX_BYTES = 512 * 1024 * 1024
CSV_MAX_BYTES = 16 * 1024 * 1024
SCORE_KIND = "text"
VISIBLE_KINDS = ("text", "donation")
VISIBLE_STATUS = "NORMAL"

G_REACTED = "reacted"
G_OTHERS = "others"
G_NEAR = "others_near"
G_TEXT = "others_text"
G_DIRECT = "reacted_direct"
GROUPS = (G_REACTED, G_OTHERS, G_NEAR, G_TEXT)

#: 바꿔 볼 판: (이름, 보는 축, guide-config values.viewer_score 칸, 맥락). 맥락 full = 주제 흐름 + 전사, no_flow = 전사만.
#: 축이 None 이면 세 축 모두를 본다. 값은 settings_from_values 로 검사한다(범위 밖이면 기본값 + 메모).
VARIANTS: tuple[tuple[str, str | None, dict, str], ...] = (
    ("base", None, {}, "full"),
    ("no_flow", "topic", {}, "no_flow"),
    ("flow_head2", "topic", {"flow_head2": True}, "full"),
    ("habit_soft", "repeat", {"habit_soft_ok_only": True}, "full"),
    ("head2+soft", None, {"flow_head2": True, "habit_soft_ok_only": True}, "full"),
    ("topic_lookback_60", "topic", {"topic_lookback_sec": 60}, "full"),
    ("topic_lookback_180", "topic", {"topic_lookback_sec": 180}, "full"),
    ("transcript_lookback_60", "topic", {"transcript_lookback_sec": 60}, "full"),
    ("transcript_lookback_120", "topic", {"transcript_lookback_sec": 120}, "full"),
    ("good_talk_hits_1", "topic", {"topic_good_talk_hits": 1}, "full"),
    ("good_talk_hits_3", "topic", {"topic_good_talk_hits": 3}, "full"),
    ("good_flow_hits_2", "topic", {"topic_good_flow_hits": 2}, "full"),
    ("concrete_words_1", "expand", {"expand_concrete_words": 1}, "full"),
    ("concrete_words_3", "expand", {"expand_concrete_words": 3}, "full"),
    ("min_chars_2", "expand", {"expand_min_chars": 2}, "full"),
    ("min_chars_6", "expand", {"expand_min_chars": 6}, "full"),
    ("max_chars_40", "expand", {"expand_max_chars": 40}, "full"),
    ("copy_window_30", "repeat", {"copy_window_sec": 30}, "full"),
    ("copy_window_60", "repeat", {"copy_window_sec": 60}, "full"),
    ("copy_weak_70", "repeat", {"copy_weak_pct": 70}, "full"),
    ("copy_weak_90", "repeat", {"copy_weak_pct": 90}, "full"),
    ("self_weak_50", "repeat", {"self_weak_pct": 50}, "full"),
    ("self_weak_70", "repeat", {"self_weak_pct": 70}, "full"),
    ("habit_weak_4", "repeat", {"habit_weak_min": 4}, "full"),
)
QUICK = ("base", "no_flow", "flow_head2", "habit_soft", "head2+soft")
#: 이 두 판은 '켜고 끈 차이'(단계가 바뀐 채팅 수)를 따로 센다(22 5-2 의 3·4)
TOGGLES = ("flow_head2", "habit_soft", "head2+soft")


class CalibError(RuntimeError):
    """사용자에게 보일 거부·입력 오류(문구에 본문·닉네임 없음)."""

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


# ───────────────────────────────────────────────────────────── 입력

@dataclass(frozen=True)
class Chat:
    """메모리에만 둔다. repr 에 본문·시청자를 보이지 않는다."""
    chat_id: str
    t_ms: int
    viewer: str
    text: str
    scorable: bool      # kind text · status NORMAL (채점 대상)
    visible: bool       # 맥락 채팅으로 화면에 보이는 글(text·donation, NORMAL)

    def __repr__(self) -> str:
        return f"Chat(t_ms={self.t_ms}, scorable={self.scorable})"


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_messages(lines: Iterable[str | bytes]) -> list[Chat]:
    """messages.jsonl 줄 → Chat 목록(시각 순). 형식이 틀린 줄은 건너뛴다(개수만 센다)."""
    out: list[Chat] = []
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        cid, t, text = d.get("chat_id"), d.get("t_ms"), d.get("text")
        if not isinstance(cid, str) or not _is_int(t) or not isinstance(text, str):
            continue
        viewer = d.get("viewer_id")
        viewer = viewer if isinstance(viewer, str) and viewer else "?" + cid
        kind, status = d.get("kind"), d.get("status")
        out.append(Chat(cid, int(t), viewer, text, kind == SCORE_KIND and status == VISIBLE_STATUS,
                        kind in VISIBLE_KINDS and status == VISIBLE_STATUS))
    out.sort(key=lambda c: (c.t_ms, c.chat_id))
    return out


def read_messages(path: str) -> list[Chat]:
    if os.path.getsize(path) > MESSAGES_MAX_BYTES:
        raise CalibError("messages.jsonl 이 너무 큽니다")
    with open(path, "rb") as f:
        return parse_messages(f)


@dataclass(frozen=True)
class LabelRow:
    chat_id: str
    chat_vod_ms: int
    grade: str

    def __repr__(self) -> str:
        return f"LabelRow(grade={self.grade})"


def parse_manual_csv(text: str) -> list[LabelRow]:
    """손라벨 CSV → chat_id·chat_vod_ms·grade 만(다른 칸은 읽지 않는다). 머리줄에 세 칸이 없으면 CalibError."""
    rd = csv.DictReader(text.splitlines())
    need = ("chat_id", "chat_vod_ms", "grade")
    if not rd.fieldnames or any(k not in rd.fieldnames for k in need):
        raise CalibError("손라벨 CSV 머리줄에 chat_id·chat_vod_ms·grade 가 없습니다")
    rows: list[LabelRow] = []
    for r in rd:
        cid = (r.get("chat_id") or "").strip()
        grade = (r.get("grade") or "").strip()
        try:
            t = int((r.get("chat_vod_ms") or "").strip())
        except ValueError:
            continue
        if cid and grade:
            rows.append(LabelRow(cid, t, grade))
    return rows


def find_manual_csv(project: str) -> str | None:
    d = os.path.join(project, "labels")
    try:
        names = sorted(n for n in os.listdir(d) if n.lower().startswith("manual-") and n.lower().endswith(".csv"))
    except OSError:
        return None
    return os.path.join(d, names[0]) if names else None


def read_manual_csv(path: str) -> list[LabelRow]:
    with open(path, "rb") as f:
        raw = f.read(CSV_MAX_BYTES + 1)
    if len(raw) > CSV_MAX_BYTES:
        raise CalibError("손라벨 CSV 가 너무 큽니다")
    return parse_manual_csv(raw.decode("utf-8-sig"))


def read_topic_blocks(path: str) -> list[tuple[int, int, tuple[str, ...]]]:
    from app.core import topics as T
    with open(path, "rb") as f:
        doc = json.loads(f.read().decode("utf-8-sig"))
    try:
        blocks = T.blocks_from_doc(doc)
    except ValueError:
        raise CalibError("주제 흐름 파일 형식이 틀립니다") from None
    return [(int(b.start_ms), int(b.end_ms), tuple(b.keywords)) for b in blocks]


# ───────────────────────────────────────────────────────────── 묶음 나누기

def build_groups(chats: Sequence[Chat], labels: Sequence[LabelRow], *, grades: Sequence[str] = DEFAULT_GRADES,
                 near_ms: int = DEFAULT_NEAR_SEC * 1000) -> tuple[dict[str, list[int]], dict]:
    """(묶음 → chats 의 번호 목록, 숫자 정보).

    반응받음 = 손라벨의 grade 가 grades 안이고 messages 에서 찾은 채점 대상 채팅. 반응 없음 = 반응받음의 첫~끝 시각 안의
    다른 채점 대상 채팅(손라벨 어느 줄에도 없는 것). 가까운 반응 없음 = 그 가운데 반응받은 채팅과 ±near_ms 안인 것."""
    by_id = {c.chat_id: i for i, c in enumerate(chats)}
    labelled = {r.chat_id for r in labels}
    want = set(grades)
    reacted: list[int] = []
    direct: list[int] = []
    info = {"label_rows": len(labels), "label_grades": {}, "label_found": 0, "label_time_match": 0,
            "label_not_scorable": 0}
    for r in labels:
        info["label_grades"][r.grade] = info["label_grades"].get(r.grade, 0) + 1
        i = by_id.get(r.chat_id)
        if i is None:
            continue
        info["label_found"] += 1
        if chats[i].t_ms == r.chat_vod_ms:
            info["label_time_match"] += 1
        if not chats[i].scorable:
            info["label_not_scorable"] += 1
            continue
        if r.grade in want and i not in reacted:
            reacted.append(i)
            if r.grade == "direct":
                direct.append(i)
    reacted.sort()
    direct.sort()
    groups = {G_REACTED: reacted, G_DIRECT: direct, G_OTHERS: [], G_NEAR: [], G_TEXT: []}
    if not reacted:
        return groups, info
    lo, hi = chats[reacted[0]].t_ms, chats[reacted[-1]].t_ms
    rts = [chats[i].t_ms for i in reacted]
    for i, c in enumerate(chats):
        if not c.scorable or c.chat_id in labelled or not lo <= c.t_ms <= hi:
            continue
        groups[G_OTHERS].append(i)
        k = bisect.bisect_left(rts, c.t_ms)
        near = any(abs(rts[j] - c.t_ms) <= near_ms for j in (k - 1, k) if 0 <= j < len(rts))
        if near:
            groups[G_NEAR].append(i)
        if vs.word_set(c.text):
            groups[G_TEXT].append(i)
    info["span_ms"] = hi - lo
    return groups, info


# ───────────────────────────────────────────────────────────── 채점

class CalibScorer:
    """실제 채팅을 연습 채팅처럼 매기는 틀. 맥락 채팅은 채팅마다 앞 60초를 잘라 같은 시청자·자기 자신을 뺀다."""

    def __init__(self, chats: Sequence[Chat], base_ctx: vs.ScoreContext):
        self.chats = chats
        self.base_ctx = base_ctx
        self._vis = [(c.t_ms, c.viewer, c.chat_id, vs.canon(c.text)) for c in chats if c.visible]
        self._vis = [r for r in self._vis if r[3]]
        self._vis_ms = [r[0] for r in self._vis]
        self._grams: dict[tuple[str, int], frozenset[str]] = {}
        self._by_viewer: dict[str, list[int]] = {}
        for i, c in enumerate(chats):
            if c.scorable:
                self._by_viewer.setdefault(c.viewer, []).append(i)

    def _g(self, key: str, n: int) -> frozenset[str]:
        k = (key, n)
        g = self._grams.get(k)
        if g is None:
            g = self._grams[k] = vs.grams(key, n)
        return g

    def context_for(self, i: int, ctx: vs.ScoreContext) -> vs.ScoreContext:
        c = self.chats[i]
        a = bisect.bisect_left(self._vis_ms, c.t_ms - CTX_CHAT_SPAN_MS)
        b = bisect.bisect_right(self._vis_ms, c.t_ms)
        rows = [r for r in self._vis[a:b] if r[1] != c.viewer and r[2] != c.chat_id]
        return replace(ctx, chat_ms=tuple(r[0] for r in rows),
                       chat_keys=tuple((r[3], self._g(r[3], ctx.ngram)) for r in rows))

    def history_for(self, i: int) -> list[str]:
        c = self.chats[i]
        own = self._by_viewer.get(c.viewer, [])
        k = bisect.bisect_left(own, i)
        out = [self.chats[j].text for j in own[max(0, k - HISTORY_MAX):k]
               if c.t_ms - HISTORY_SPAN_MS <= self.chats[j].t_ms < c.t_ms
               or (self.chats[j].t_ms == c.t_ms and j < i)]
        return out

    def score(self, idxs: Iterable[int], settings: vs.ViewerScoreSettings,
              ctx: vs.ScoreContext | None = None) -> dict[int, vs.ViewerScore]:
        base = ctx or self.base_ctx
        out: dict[int, vs.ViewerScore] = {}
        for i in idxs:
            out[i] = vs.score_chat(self.chats[i].text, self.chats[i].t_ms, self.context_for(i, base),
                                   self.history_for(i), settings=settings)
        return out

    def flow_presend(self, idxs: Iterable[int], scores: Mapping[int, vs.ViewerScore], settings: vs.ViewerScoreSettings,
                     ctx: vs.ScoreContext | None = None) -> dict:
        """주제 흐름 누출 크기(숫자만). 주제 흐름 덩어리는 방송 전체의 전사·채팅으로 만든 것이라 보낸 시각 **뒤**의 말과
        채점 대상 채팅 자신의 낱말이 덩어리 낱말에 들어갈 수 있다(앱의 연습 채팅은 주제 흐름의 재료가 아니다).

        topic.flow_hit 인 채팅마다 겹친 주제 흐름 낱말 가운데 하나라도 '덩어리 시작 ~ 보낸 시각' 에 시작한 전사 창에
        있으면 presend, 아니면 postsend_only(보낸 시각 뒤의 말이나 채팅 낱말만이 받쳐 줌). block_past_send = 걸린 덩어리
        가운데 하나라도 보낸 시각 뒤까지 이어지는 채팅 수. 동의어·flow_head2 는 보지 않는다(base 판 전용)."""
        base = ctx or self.base_ctx
        out = {"flow_hit": 0, "presend": 0, "postsend_only": 0, "block_past_send": 0}
        for i in idxs:
            if scores[i].topic.code != vs.TOPIC_FLOW_HIT:
                continue
            c = self.chats[i]
            lo, hi = c.t_ms - settings.topic_lookback_ms, c.t_ms
            rows = [(a, b, ws) for a, b, ws in base.topic.items if b >= lo and a < hi]
            if not rows:
                continue
            pool = frozenset().union(*(ws for _a, _b, ws in rows))
            units = vs._content_units(c.text, base.talk_common)
            hit_words = sorted({w for u in units for w in u if any(vs._match(x, pool) for x in sorted(u))})
            talk_pre = base.talk.words_started(min(a for a, _b, _w in rows), hi)
            out["flow_hit"] += 1
            if any(vs._match(w, talk_pre) for w in hit_words):
                out["presend"] += 1
            else:
                out["postsend_only"] += 1
            if any(b > hi for _a, b, _w in rows):
                out["block_past_send"] += 1
        return out


# ───────────────────────────────────────────────────────────── 숫자

def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2×2 표 [[a, b], [c, d]] 의 피셔 정확 검정 양쪽 p (확률이 관측값 이하인 표들의 합)."""
    n1, n2, k = a + b, c + d, a + c
    n = n1 + n2
    if n == 0:
        return 1.0
    denom = math.comb(n, k)

    def p(x: int) -> float:
        return math.comb(n1, x) * math.comb(n2, k - x) / denom

    obs = p(a)
    lo, hi = max(0, k - n2), min(k, n1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= obs * (1 + 1e-9)))


def _pct(x: int, n: int) -> float | None:
    return round(100.0 * x / n, 1) if n else None


def distribution(scores: Mapping[int, vs.ViewerScore], idxs: Sequence[int]) -> dict:
    """축마다 {n, good, ok, weak, good_pct, ok_pct, weak_pct, codes{코드: 수}}. 숫자·코드만."""
    out: dict = {"n": len(idxs)}
    for axis in vs.AXES:
        cnt = {lv: 0 for lv in vs.LEVELS}
        codes: dict[str, int] = {}
        for i in idxs:
            r = getattr(scores[i], axis)
            cnt[r.level] += 1
            codes[r.code] = codes.get(r.code, 0) + 1
        n = len(idxs)
        out[axis] = {**cnt, **{f"{lv}_pct": _pct(cnt[lv], n) for lv in vs.LEVELS},
                     "codes": dict(sorted(codes.items()))}
    return out


def good_lift(a_good: int, na: int, b_good: int, nb: int) -> float | None:
    """lift = (a 의 good 률) ÷ (b 의 good 률). b 의 good 이 0 이거나 묶음이 비면 None(정의되지 않음)."""
    if not na or not nb or not b_good:
        return None
    return round((a_good / na) / (b_good / nb), 2)


def good_precision(a_good: int, b_good: int) -> float | None:
    """정밀도 = good 가운데 a(반응받음) 쪽 비율 = a_good / (a_good + b_good). good 이 하나도 없으면 None.
    두 묶음의 크기 비에 따라 달라지므로(29 : 713 등) 판끼리 견줄 때는 lift 를 주 지표로 쓴다."""
    tot = a_good + b_good
    return round(a_good / tot, 3) if tot else None


def compare(dist_a: Mapping, dist_b: Mapping) -> dict:
    """축마다 good 비율 차이(a − b, %p), 피셔 양쪽 p(good 대 good 아님), good 의 정밀도·lift.

    정밀도·lift 는 '정밀도 우선'(D-21 2 — 틀린 칭찬이 틀린 지적보다 나쁘다)으로 문턱 후보를 고를 때 쓴다. good 차이는
    문턱을 느슨하게 해 양쪽 good 이 함께 늘어도 커질 수 있어 보조 숫자로만 본다(검토 2026-10-23)."""
    out = {}
    for axis in vs.AXES:
        a, b = dist_a[axis], dist_b[axis]
        na, nb = dist_a["n"], dist_b["n"]
        if not na or not nb:
            out[axis] = {"good_diff_pp": None, "weak_diff_pp": None, "fisher_p_good": None,
                         "good_precision": None, "good_lift": None}
            continue
        out[axis] = {"good_diff_pp": round(a["good_pct"] - b["good_pct"], 1),
                     "weak_diff_pp": round(a["weak_pct"] - b["weak_pct"], 1),
                     "fisher_p_good": round(fisher_two_sided(a["good"], na - a["good"], b["good"], nb - b["good"]), 4),
                     "good_precision": good_precision(a["good"], b["good"]),
                     "good_lift": good_lift(a["good"], na, b["good"], nb)}
    return out


def changed(before: Mapping[int, vs.ViewerScore], after: Mapping[int, vs.ViewerScore], idxs: Sequence[int]) -> dict:
    """축마다 단계가 바뀐 채팅 수와 방향(올라감·내려감)."""
    rank = {vs.WEAK: 0, vs.OK: 1, vs.GOOD: 2}
    out = {}
    for axis in vs.AXES:
        up = down = 0
        for i in idxs:
            x, y = rank[getattr(before[i], axis).level], rank[getattr(after[i], axis).level]
            up += y > x
            down += y < x
        out[axis] = {"up": up, "down": down}
    return out


def settings_for(overrides: Mapping) -> tuple[vs.ViewerScoreSettings, list[str]]:
    return vs.settings_from_values({"viewer_score": dict(overrides)})


def run_variants(scorer: CalibScorer, groups: Mapping[str, Sequence[int]], *, no_flow_ctx: vs.ScoreContext,
                 names: Sequence[str] | None = None) -> dict:
    """판마다 묶음별 분포·비교. base 와 견준 '단계가 바뀐 수' 는 TOGGLES 판에만."""
    idx_all = sorted({i for g in GROUPS + (G_DIRECT,) for i in groups.get(g, ())})
    results: dict = {}
    base_scores = None
    for name, axis, ov, ctx_kind in VARIANTS:
        if names is not None and name not in names:
            continue
        s, notes = settings_for(ov)
        ctx = no_flow_ctx if ctx_kind == "no_flow" else None
        t0 = time.perf_counter()
        sc = scorer.score(idx_all, s, ctx)
        el = time.perf_counter() - t0
        dists = {g: distribution(sc, groups.get(g, ())) for g in GROUPS + (G_DIRECT,)}
        row = {"axis": axis, "overrides": dict(ov), "context": ctx_kind, "notes": notes,
               "elapsed_s": round(el, 3), "groups": dists,
               "reacted_vs_others": compare(dists[G_REACTED], dists[G_OTHERS]),
               "reacted_vs_near": compare(dists[G_REACTED], dists[G_NEAR]),
               "reacted_vs_text": compare(dists[G_REACTED], dists[G_TEXT]),
               "direct_vs_others": compare(dists[G_DIRECT], dists[G_OTHERS])}
        if name == "base":
            base_scores = sc
            row["flow_presend"] = {g: scorer.flow_presend(groups.get(g, ()), sc, s) for g in GROUPS + (G_DIRECT,)}
        elif base_scores is not None and name in TOGGLES:
            row["changed_vs_base"] = {g: changed(base_scores, sc, groups.get(g, ())) for g in (G_REACTED, G_OTHERS)}
        results[name] = row
    return results


#: 후보는 반응받은 쪽 good 수가 base 의 이 비율 이상일 때만(정밀도만 보면 good 을 거의 다 없애는 판이 이길 수 있어서)
CANDIDATE_MIN_RECALL = 0.5


def candidates(results: Mapping) -> dict:
    """축마다 **good lift(반응받음 good 률 ÷ 반응 없음 good 률)가 base 보다 높은** 판 가운데 lift 가 가장 큰 판.
    없으면 base. **후보일 뿐 기본값을 바꾸지 않는다.**

    정밀도 우선(D-21 2): good 차이(%p)가 가장 큰 판을 고르면 문턱을 느슨하게 해 반응 없는 쪽 good 도 함께 느는 판이
    이긴다(검토 2026-10-23). 그래서 lift 를 주 지표로, 정밀도·good 차이·p 는 보조 숫자로 함께 적는다.
    반응받은 쪽 good 수가 base 의 CANDIDATE_MIN_RECALL 미만이거나 0 이면 후보로 보지 않는다. 같은 lift 면 반응받은 쪽
    good 이 많은 판, 그다음 VARIANTS 순서."""
    out = {}
    base = results.get("base")
    if not base:
        return out
    for axis in vs.AXES:
        base_c = base["reacted_vs_others"][axis]
        base_rg = base["groups"][G_REACTED][axis]["good"]
        min_rg = max(1, math.ceil(base_rg * CANDIDATE_MIN_RECALL))
        best_name, best_lift, best_rg = "base", base_c["good_lift"], base_rg
        if best_lift is not None:
            for name, row in results.items():
                if row["axis"] not in (axis, None) or name == "base":
                    continue
                lift = row["reacted_vs_others"][axis]["good_lift"]
                rg = row["groups"][G_REACTED][axis]["good"]
                if lift is None or rg < min_rg:
                    continue
                if lift > best_lift or (lift == best_lift and best_name != "base" and rg > best_rg):
                    best_name, best_lift, best_rg = name, lift, rg
        row = results[best_name]
        c, t = row["reacted_vs_others"][axis], row["reacted_vs_text"][axis]
        out[axis] = {"variant": best_name, "metric": "good_lift",
                     "good_lift": c["good_lift"], "base_good_lift": base_c["good_lift"],
                     "good_precision": c["good_precision"], "base_good_precision": base_c["good_precision"],
                     "good_diff_pp": c["good_diff_pp"], "base_good_diff_pp": base_c["good_diff_pp"],
                     "fisher_p_good": c["fisher_p_good"],
                     "reacted_good": row["groups"][G_REACTED][axis]["good"], "base_reacted_good": base_rg,
                     "others_good": row["groups"][G_OTHERS][axis]["good"],
                     "base_others_good": base["groups"][G_OTHERS][axis]["good"],
                     "text_good_lift": t["good_lift"],
                     "base_text_good_lift": base["reacted_vs_text"][axis]["good_lift"],
                     "text_good_diff_pp": t["good_diff_pp"],
                     "base_text_good_diff_pp": base["reacted_vs_text"][axis]["good_diff_pp"]}
    return out


# ───────────────────────────────────────────────────────────── 실행

def _is_pilot(project: str) -> bool:
    from app import reference_set as rs
    from app import viewer_flow as vf
    try:
        return vf.is_pilot_name(project) or rs.is_pilot_project(project)
    except Exception:  # noqa: BLE001 — 판단 못 하면 시범으로 본다(블라인드 쪽으로 안전하게)
        return True


def topics_zone(project: str, path: str, pdata: Mapping | None = None) -> str:
    """--topics 파일이 어디 있나: 'protected'(프로젝트 보호 구역 안), 'unprotected'(프로젝트 안, 보호 구역 밖),
    'outside_project'(프로젝트 밖)."""
    from app import backup
    root, p = os.path.abspath(project), os.path.abspath(path)
    try:
        rel = os.path.relpath(p, root)
    except ValueError:          # 다른 드라이브
        return "outside_project"
    if rel == os.curdir or rel.startswith(os.pardir) or os.path.isabs(rel):
        return "outside_project"
    pats = backup.protected_patterns(dict(pdata) if isinstance(pdata, Mapping) else None)
    return "protected" if backup.in_protected_zone(rel, pats) else "unprotected"


def load_context(project: str, *, topics_path: str | None) -> tuple[list, list, dict]:
    """(주제 흐름 덩어리, 전사 창, 숫자 정보). 전사 낱말은 viewer_flow.words_status 가 ok 일 때만."""
    from app import viewer_flow as vf
    from app.core import words_doc as WD
    info: dict = {}
    try:
        with open(os.path.join(project, "project.json"), "rb") as f:
            pdata = json.loads(f.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        raise CalibError("project.json 을 읽지 못했습니다") from None
    if topics_path:
        # 주제 흐름 낱말은 전사·채팅에서 나온 값이라 보호 구역(derived/asr*)에 둔다(03 2-1). 프로젝트 안의 보호 구역 밖
        # 파일은 읽지 않는다(그런 이름으로 다시 만드는 일이 없게, 검토 2026-10-23). 프로젝트 밖(합성 시험 등)은 표시만.
        zone = topics_zone(project, topics_path, pdata)
        if zone == "unprotected":
            raise CalibError("주제 흐름 파일이 프로젝트 보호 구역 밖입니다 — <P>\\derived\\asr-topic-flow.json 처럼 "
                             "derived\\asr* 이름으로 두십시오")
        info["topic_zone"] = zone
    code, wdoc = vf.words_status(project, pdata)
    info["words"] = code
    windows = WD.score_windows(wdoc) if wdoc is not None else []
    info["transcript_windows"] = len(windows)
    if topics_path:
        blocks = read_topic_blocks(topics_path)
        info["topic_source"] = "file"
    else:
        blocks, rid = vf.topic_blocks(project)
        info["topic_source"] = "unsealed_run" if rid else "none"
    info["topic_blocks"] = len(blocks)
    return blocks, windows, info


def calibrate(project: str, *, topics_path: str | None = None, labels_path: str | None = None,
              grades: Sequence[str] = DEFAULT_GRADES, near_sec: int = DEFAULT_NEAR_SEC, quick: bool = False) -> dict:
    project = os.path.abspath(project)
    if not os.path.isfile(os.path.join(project, "project.json")):
        raise CalibError("프로젝트 폴더가 아닙니다(project.json 없음)")
    if _is_pilot(project):
        raise CalibError("시범 프로젝트는 열지 않습니다(시범 세션이 모두 잠기고 해제된 뒤, D-17 4)", 1)
    lp = labels_path or find_manual_csv(project)
    if not lp or not os.path.isfile(lp):
        raise CalibError("손라벨 labels\\manual-*.csv 가 없습니다", 1)
    t0 = time.perf_counter()
    labels = read_manual_csv(lp)
    chats = read_messages(os.path.join(project, "chat", "messages.jsonl"))
    blocks, windows, cinfo = load_context(project, topics_path=topics_path)
    groups, ginfo = build_groups(chats, labels, grades=grades, near_ms=int(near_sec) * 1000)
    if not groups[G_REACTED]:
        raise CalibError("손라벨에서 반응받은 채팅을 찾지 못했습니다", 1)
    base_ctx = vs.ScoreContext.build(topic_blocks=blocks, transcript=windows)
    no_flow_ctx = vs.ScoreContext.build(topic_blocks=(), transcript=windows)
    scorer = CalibScorer(chats, base_ctx)
    load_s = time.perf_counter() - t0
    results = run_variants(scorer, groups, no_flow_ctx=no_flow_ctx, names=QUICK if quick else None)
    return {
        "tool": TOOL, "tool_version": TOOL_VERSION, "scorer": f"viewer_score/{vs.VERSION}",
        "normalizer": vs.NORMALIZER,
        "input": {"messages": len(chats), "scorable": sum(1 for c in chats if c.scorable),
                  "visible": sum(1 for c in chats if c.visible), **ginfo, **cinfo,
                  "grades": list(grades), "near_sec": int(near_sec),
                  "groups": {g: len(groups.get(g, ())) for g in GROUPS + (G_DIRECT,)}},
        "load_s": round(load_s, 3),
        "variants": results,
        "candidates": candidates(results),
    }


def _table(rep: Mapping) -> list[str]:
    """사람이 읽는 숫자 표(본문 없음)."""
    lines = []
    inp = rep["input"]
    lines.append(f"{rep['scorer']} · 채팅 {inp['messages']} · 반응받음 {inp['groups'][G_REACTED]}"
                 f"(direct {inp['groups'][G_DIRECT]}) · 반응 없음 {inp['groups'][G_OTHERS]}"
                 f" · 가까운 반응 없음 {inp['groups'][G_NEAR]} · 낱말 있는 반응 없음 {inp['groups'][G_TEXT]}"
                 f" · 주제 흐름 {inp['topic_blocks']} · 전사 창 {inp['transcript_windows']}({inp['words']})")
    lines.append("판 | 축 | 반응받음 good/ok/weak % | 반응 없음 good/ok/weak % | good 건수(반응받음/반응 없음) | lift | 정밀도"
                 " | good 차이 %p | p | 낱말 있는 쪽과 lift | 차이 %p | p")
    for name, row in rep["variants"].items():
        for axis in vs.AXES:
            if row["axis"] not in (None, axis):
                continue
            a, b = row["groups"][G_REACTED][axis], row["groups"][G_OTHERS][axis]
            c, t = row["reacted_vs_others"][axis], row["reacted_vs_text"][axis]
            lines.append(f"{name} | {axis} | {a['good_pct']}/{a['ok_pct']}/{a['weak_pct']} | "
                         f"{b['good_pct']}/{b['ok_pct']}/{b['weak_pct']} | {a['good']}/{b['good']} | {c['good_lift']}"
                         f" | {c['good_precision']} | {c['good_diff_pp']} | {c['fisher_p_good']}"
                         f" | {t['good_lift']} | {t['good_diff_pp']} | {t['fisher_p_good']}")
    fp = (rep["variants"].get("base") or {}).get("flow_presend")
    if fp:
        lines.append("주제 흐름 누출(base): " + " · ".join(
            f"{g} flow_hit {fp[g]['flow_hit']} = 보낸 시각 앞 전사에도 {fp[g]['presend']} + 뒤에만 "
            f"{fp[g]['postsend_only']} (덩어리가 보낸 뒤까지 {fp[g]['block_past_send']})" for g in (G_REACTED, G_OTHERS)))
    lines.append("후보(lift 가 base 보다 높은 판, 기본값 아님): " + " · ".join(
        f"{axis} {c['variant']}(lift {c['base_good_lift']} → {c['good_lift']})"
        for axis, c in rep.get("candidates", {}).items()))
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시청자 훈련 채점 보정 자료(숫자만)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--topics", help="주제 흐름 파일(engine.topics 결과, <P>\\derived\\asr-topic-flow.json 처럼 보호 구역 "
                                     "derived\\asr* 안). 프로젝트 안의 보호 구역 밖 파일은 거부. 없으면 해제된 run 의 주제 흐름")
    ap.add_argument("--labels", help="손라벨 CSV(기본 labels\\manual-*.csv 이름 순 첫 파일)")
    ap.add_argument("--grades", default=",".join(DEFAULT_GRADES), help="반응받음으로 볼 grade(쉼표)")
    ap.add_argument("--near-sec", type=int, default=DEFAULT_NEAR_SEC)
    ap.add_argument("--out", help="결과 JSON 파일(숫자만)")
    ap.add_argument("--quick", action="store_true", help="기본 + 켜고 끄기 판만")
    ap.add_argument("--json", action="store_true", help="표 대신 JSON 을 표준 출력으로")
    a = ap.parse_args(argv)
    if not 0 < a.near_sec <= 600:
        print("--near-sec 는 1~600", file=sys.stderr)
        return 2
    grades = tuple(g.strip() for g in a.grades.split(",") if g.strip())
    try:
        rep = calibrate(a.project, topics_path=a.topics, labels_path=a.labels, grades=grades,
                        near_sec=a.near_sec, quick=a.quick)
    except CalibError as e:
        print(f"거부: {e}", file=sys.stderr)
        return e.code
    except (OSError, ValueError) as e:
        print(f"입력 오류: {type(e).__name__}", file=sys.stderr)
        return 2
    if rep["input"].get("topic_zone") == "outside_project":
        print("주의: 주제 흐름 파일이 프로젝트 밖입니다(실제 방송이면 <P>\\derived\\asr-topic-flow.json 에 두십시오)",
              file=sys.stderr)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, sort_keys=True))
    else:
        print("\n".join(_table(rep)))
    return 0


__all__ = ["Chat", "LabelRow", "CalibError", "CalibScorer", "VARIANTS", "QUICK", "TOGGLES", "CANDIDATE_MIN_RECALL",
           "parse_messages", "parse_manual_csv", "build_groups", "distribution", "compare", "good_lift",
           "good_precision", "changed", "fisher_two_sided", "settings_for", "run_variants", "candidates",
           "topics_zone", "calibrate", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
