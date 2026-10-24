"""시각 보정 앵커 '후보' 를 제안하는 로컬 도우미 (09 3-3).

후보일 뿐이다. 다시보기에서 그 장면을 직접 듣고 확인한 것만 사람이 골라 tools/calibrate.py 에 넣는다.

사용:
  python tools/find_anchors.py --project <프로젝트 폴더> [--asr <asr.json>] [--top 20]
         [--asr-start-ms N] [--file-start-ms N] [--lag-before 120] [--lag-after 30]
         [--from 시각] [--to 시각] [--no-identities] [--no-text]

(a) --asr 가 있으면: 발화마다 그 근처 채팅 가운데
      - 발화의 '…님' 앞 글자가 작성자 닉네임과 비슷한 것 (보호 구역 identities.jsonl 이 있을 때.
        닉네임은 대조에만 쓰고 화면에 찍지 않는다. 없으면 가림 닉네임 첫 글자만 보는 약한 단서)
      - 채팅 본문의 문자 bigram 이 발화에 많이 들어 있는 것 (채팅을 소리 내 읽은 장면)
    을 점수로 매겨 (발화 시각, 채팅 시각, 차이) 표를 찍는다. 한 채팅·한 발화는 한 번만 쓴다.
    asr.json 의 시각은 wav 0초 기준이다. wav 를 영상 파일 중간부터 뽑았으면 그 시작(영상 파일 시각, ms)을 더한다:
    asr.json 이 기억하면(audio.asr_start_ms — engine asr --asr-start-ms, 08 D-33) 그 값, 없으면 --asr-start-ms(기본 0).
    둘이 다르면 asr_start_conflict 로 멈춘다(engine.transcript_words.resolve_asr_start 와 같은 규칙).
    영상 파일 자체가 VOD 일부(클립)이면 --file-start-ms.
    차이 = 영상 시각 − (채팅 시각 − file_start) = calibrate 가 계산할 오프셋과 같은 축.
(b) --asr 가 없으면: '질문형이고 짧은' 채팅을 시간대별로 고르게 뽑는다. 다시보기에서 그 채팅 시각 근처를
    보며 스트리머가 읽거나 답하는지 사람이 확인할 목록이다. 영상 시각 칸은 사람이 채운다.

(c) --locate (--asr 필요): 영상이 VOD 의 어디인지 모를 때(13시간 VOD 의 19분 클립 등, 08 D-32). (1단계) 정보가 많은 발화
    최대 LOC_MAX_UTTS 개를 모든 text 채팅과 희귀 bigram 역색인으로 맞대어 짝마다 차이(영상 − 채팅)에 표를 던지고, LOC_WIN_MS
    폭 창에서 표 합이 가장 큰 자리를 찾는다(서로 다른 발화·채팅이 LOC_MIN_UTTS·LOC_MIN_CHATS 이상이어야 자리로 본다).
    그 차이 중앙값의 반대가 클립 시작 추정(읽기 지연이 든 값, 0 밑이면 0). (2단계) (a) 를 그 추정을 --file-start-ms 로 돌린다.

끝에 tools/calibrate.py --anchor <영상시각>=<채팅시각> 모양으로도 찍는다.
시각 형식: --from/--to 는 1:02:03(.5) 형식, 정수는 ms (tools/chat_window.py 와 같다).
출력은 화면에만 한다. 이 도구는 파일을 쓰지 않는다.
--json: 표·본문 없이 숫자만 JSON 한 줄(candidate_counts — 앱 '파일 → 새 방송…' 이 후보 수만 센다, 08 D-31 2).
--json --list: 같은 줄에 후보 rows(chat_id · 채팅 시각 · 영상 시각 · 발화 끝 · 근거 코드 · 점수 — 본문·닉네임·발화 글 없음)를
    더한다. 앱 '시간 정렬(보정)' 화면이 읽는다(08 D-32 — 본문은 앱이 messages.jsonl 에서 직접 읽는다).
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
import json
import os
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.core import timecode as tc  # noqa: E402
from engine import features as F  # noqa: E402
from engine.transcript_words import AsrStartConflict, recorded_asr_start, resolve_asr_start  # noqa: E402
import chat_window as cw  # noqa: E402

DISCLAIMER = ("※ 후보일 뿐이다. 확정은 사람이 한다: 다시보기에서 그 장면을 직접 듣고 "
              "'채팅을 읽었다/닉네임을 불렀다' 가 맞는 것만 골라 calibrate.py 에 넣는다 (09 3-3).")

# 엔진 시작값과 같은 결의 도구 시작값. 설계 숫자가 아니다.
MIN_CHAT_CHARS = 4          # (a) 문구 겹침을 볼 채팅의 최소 글자 수 (공백·기호 뺀 뒤)
MIN_SHARED_BIGRAMS = 3      # (a) 겹치는 bigram 이 이보다 적으면 문구 겹침 후보가 아니다
FULL_SHARED_BIGRAMS = 6     # (a) 이만큼 겹치면 길이 감점이 없다
MIN_SCORE = 0.3             # (a) 이보다 낮은 짝은 버린다
NAME_SCORE = 1.0            # (a) 닉네임 호명 (identities 대조)
WEAK_NAME_SCORE = 0.2       # (a) 가림 닉네임 첫 글자만 맞음
CONSISTENT_MS = 3000        # 차이 중앙값에서 이만큼 안이면 '서로 맞는 후보' (calibrate 흩어짐 경고 기준과 같음)
Q_MIN_CHARS, Q_MAX_CHARS = 4, 25   # (b) 짧은 질문의 글자 수 범위
DENSITY_WIN_MS = 15_000     # (b) 주변 채팅 밀도를 셀 창 (± 초)
# (c) 위치 찾기 — 도구 시작값(D-32 측정 문서에 실제 클립 2편 결과)
LOC_MAX_UTTS = 400          # 1단계에 쓸 발화 수(희귀 bigram 이 많은 순)
LOC_DF_MIN = 20             # 희귀 bigram: 그 bigram 이 든 채팅 수 ≤ max(LOC_DF_MIN, LOC_DF_FRAC × 채팅 수)
LOC_DF_FRAC = 0.002
LOC_MIN_SHARED = 3          # 짝이 되려면 공유 희귀 bigram 이 이만큼 (MIN_SHARED_BIGRAMS 와 같은 값)
LOC_TOPK = 3                # 발화 하나가 던지는 표 수(점수 높은 짝부터)
LOC_WIN_MS = 60_000         # 표를 모으는 창 폭 — 읽기 지연이 수 초~수십 초로 퍼져 있어 넓게
LOC_SECOND_GAP_MS = 120_000  # '둘째 자리' 는 첫 자리 가운데에서 이만큼 떨어진 창만(보고용)
LOC_MIN_UTTS = 3            # 자리로 보려면 창 안 표의 서로 다른 발화 수
LOC_MIN_CHATS = 3           # … 와 서로 다른 채팅 수

_NIM_NAME = re.compile(r"([0-9a-z가-힣]{1,12})님")


@dataclass
class Utt:
    seg_id: int
    start_ms: int        # wav 시각
    text: str
    name_ms: int | None  # '님' 이 든 단어의 시작 (wav 시각), 없으면 None
    end_ms: int | None = None  # 발화 끝 (wav 시각), 없으면 None


@dataclass
class Cand:
    score: float
    basis: str           # 'name' · 'name?' · 'text' · 'name+text' …
    video_ms: int        # 영상 파일 시각
    chat: dict
    utt: Utt | None = None
    shared: int = 0
    cont: float = 0.0

    def diff_ms(self, file_start_ms: int) -> int:
        return self.video_ms - (self.chat["t_ms"] - file_start_ms)


# ── 읽기 ───────────────────────────────────────────────────────────────────

def load_asr(path: str) -> list[Utt]:
    return load_asr_doc(path)[0]


def load_asr_doc(path: str) -> tuple[list[Utt], int | None]:
    """asr.json → (발화 목록(asr 시각 그대로), 기억한 asr_start_ms 또는 None — audio.asr_start_ms)."""
    with open(path, "r", encoding="utf-8-sig") as f:
        d = json.load(f)
    if not isinstance(d, dict) or d.get("format") != "chatcoach.asr":
        raise ValueError("chatcoach.asr 형식의 asr.json 이 아니다")
    rec = recorded_asr_start(d)
    out = []
    for i, s in enumerate(d.get("segments") or []):
        if not isinstance(s, dict) or not isinstance(s.get("start_ms"), int):
            continue
        name_ms = None
        for w in s.get("words") or []:
            if isinstance(w, dict) and "님" in str(w.get("word", "")) and isinstance(w.get("start_ms"), int):
                name_ms = w["start_ms"]
                break
        end = s.get("end_ms")
        end = end if isinstance(end, int) and not isinstance(end, bool) and end >= s["start_ms"] else None
        out.append(Utt(int(s.get("id", i)), int(s["start_ms"]), str(s.get("text") or ""), name_ms, end))
    out.sort(key=lambda u: (u.start_ms, u.seg_id))
    return out, rec


def text_chats(records: list[dict], t_from: int | None = None, t_to: int | None = None) -> list[dict]:
    """시각이 있는 text 채팅 (t_ms 순). 스트리머 본인 채팅은 앵커가 될 수 없어 뺀다."""
    out = []
    for r in cw.sort_messages(records):
        t = r.get("t_ms")
        if r.get("kind") != "text" or not isinstance(t, int):
            continue
        if r.get("role") == "streamer":
            continue
        if t_from is not None and t < t_from:
            continue
        if t_to is not None and t > t_to:
            continue
        out.append(r)
    return out


# ── (a) asr 가 있을 때 ────────────────────────────────────────────────────

def names_in_utterance(text: str) -> list[str]:
    """발화의 '…님' 앞 덩어리 (공백 지운 정규화 뒤). 표시·약한 대조용."""
    return [m.group(1) for m in _NIM_NAME.finditer(F.compact(text))]


def weak_name_match(masked: str | None, utt_names: list[str]) -> bool:
    """가림 닉네임('김**')의 첫 글자와 '…님' 앞 덩어리의 첫 글자가 같은가 (아주 약한 단서)."""
    if not masked or not utt_names:
        return False
    first = F.compact(masked[:1])
    if not first:
        return False
    return any(n[:1] == first for n in utt_names if len(n) >= 2)


def score_pair(chat: dict, utt: Utt, *, nicknames: list[str] | None, ngram_cache: dict,
               utt_ngrams: Counter | None = None) -> Cand | None:
    """한 채팅–발화 짝의 점수. 기준 미달이면 None. video_ms 는 wav 시각으로 두고 호출한 쪽이 옮긴다."""
    cid = chat.get("chat_id")
    if cid not in ngram_cache:
        body = str(chat.get("text") or "")
        ngram_cache[cid] = (F.char_ngrams(body), len(F.compact(body)))
    cg, clen = ngram_cache[cid]
    ug = utt_ngrams if utt_ngrams is not None else F.char_ngrams(utt.text)
    shared = sum((cg & ug).values()) if cg and ug else 0
    cont = F.containment(cg, ug) if shared else 0.0
    text_score = 0.0
    if clen >= MIN_CHAT_CHARS and shared >= MIN_SHARED_BIGRAMS:
        text_score = cont * min(1.0, shared / FULL_SHARED_BIGRAMS)

    name_score, name_basis = 0.0, ""
    if "님" in utt.text:
        if nicknames is not None:
            if F.nickname_called(nicknames, utt.text):
                name_score, name_basis = NAME_SCORE, "name"
        elif weak_name_match(chat.get("nickname_masked"), names_in_utterance(utt.text)):
            name_score, name_basis = WEAK_NAME_SCORE, "name?"

    if text_score >= name_score:
        score = text_score + (0.25 * name_score if name_score else 0.0)
        basis = "text" + ("+" + name_basis if name_basis else "")
    else:
        score = name_score + 0.25 * text_score
        basis = name_basis + ("+text" if text_score else "")
    if score < MIN_SCORE:
        return None
    t = utt.name_ms if (name_basis and name_score >= text_score and utt.name_ms is not None) else utt.start_ms
    return Cand(score=round(score, 3), basis=basis, video_ms=t, chat=chat, utt=utt, shared=shared,
                cont=round(cont, 3))


def asr_candidates(chats: list[dict], utts: list[Utt], *, identities: dict | None, asr_start_ms: int = 0,
                   file_start_ms: int = 0, lag_before_ms: int = 120_000, lag_after_ms: int = 30_000,
                   top: int = 20) -> list[Cand]:
    """(a) 발화별로 근처 채팅을 매겨 점수 높은 순으로, 한 채팅·한 발화는 한 번만."""
    ts = [c["t_ms"] for c in chats]
    cache: dict = {}
    pairs: list[Cand] = []
    for u in utts:
        video = asr_start_ms + u.start_ms
        approx_vod = video + file_start_ms           # 오프셋을 모르니 0 으로 둔 대략의 VOD 시각
        lo = bisect.bisect_left(ts, approx_vod - lag_before_ms)
        hi = bisect.bisect_right(ts, approx_vod + lag_after_ms)
        ug = F.char_ngrams(u.text)
        for c in chats[lo:hi]:
            nicks = None
            if identities is not None:
                nicks = cw.nickname_list(identities.get(c.get("viewer_id"))) or []
            cand = score_pair(c, u, nicknames=nicks, ngram_cache=cache, utt_ngrams=ug)
            if cand is None:
                continue
            cand.video_ms += asr_start_ms
            pairs.append(cand)
    pairs.sort(key=lambda p: (-p.score, abs(p.diff_ms(file_start_ms)), p.chat["chat_id"], p.utt.seg_id))
    used_c, used_u, out = set(), set(), []
    for p in pairs:
        if p.chat["chat_id"] in used_c or p.utt.seg_id in used_u:
            continue
        used_c.add(p.chat["chat_id"])
        used_u.add(p.utt.seg_id)
        out.append(p)
        if len(out) >= top:
            break
    return out


def consistent_subset(cands: list[Cand], file_start_ms: int) -> tuple[int | None, list[Cand]]:
    """차이 중앙값과, 그 중앙값에서 CONSISTENT_MS 안에 드는 후보들."""
    if not cands:
        return None, []
    med = int(statistics.median_low(c.diff_ms(file_start_ms) for c in cands))
    return med, [c for c in cands if abs(c.diff_ms(file_start_ms) - med) <= CONSISTENT_MS]


def spread_pick(cands: list, key, k: int = 3) -> list:
    """시간 순으로 앞·가운데·뒤에서 고르게 k 개."""
    s = sorted(cands, key=key)
    if len(s) <= k:
        return s
    idx = sorted({round(i * (len(s) - 1) / (k - 1)) for i in range(k)})
    return [s[i] for i in idx]


# ── (c) 위치 찾기 ────────────────────────────────────────────────────────

@dataclass
class Located:
    """1단계 결과(숫자만). file_start_ms = 영상 0초의 VOD 시각 추정(읽기 지연이 든 값, 0 밑이면 0 — clamped)."""
    file_start_ms: int
    diff_ms: int             # 자리 창 안 표의 차이(영상 − 채팅) 중앙값
    peak: float              # 자리 창의 표 합
    second: float            # 첫 자리에서 LOC_SECOND_GAP_MS 넘게 떨어진 창 가운데 가장 큰 표 합
    votes: int               # 던진 표 수
    peak_votes: int
    peak_utts: int
    peak_chats: int
    utts_used: int
    clamped: bool = False

    def doc(self) -> dict:
        return {"file_start_ms": self.file_start_ms, "diff_ms": self.diff_ms, "peak": round(self.peak, 3),
                "second": round(self.second, 3), "votes": self.votes, "peak_votes": self.peak_votes,
                "peak_utts": self.peak_utts, "peak_chats": self.peak_chats, "utts_used": self.utts_used,
                "clamped": self.clamped}


def locate_clip(chats: list[dict], utts: list[Utt], *, asr_start_ms: int = 0) -> Located | None:
    """(c) 1단계: 오프셋을 모를 때 영상이 VOD 의 어디인지. 자리를 못 찾으면 None.

    희귀 bigram(그 bigram 이 든 채팅이 max(LOC_DF_MIN, LOC_DF_FRAC × 채팅 수) 이하)만 역색인해, 발화마다 공유 희귀 bigram 이
    LOC_MIN_SHARED 이상인 채팅을 score_pair(닉네임 대조 없음)로 매긴다. 발화 하나는 점수 높은 LOC_TOPK 짝까지 표를 던지고, 표의
    무게는 점수 ÷ 같은 문구 채팅 수(인사·따라 치기처럼 흔한 문구가 자리를 만들지 않게). 차이(영상 − 채팅)로 늘어놓고 LOC_WIN_MS
    폭 창 가운데 무게 합이 가장 큰 창이 자리다."""
    cache: dict = {}
    post: dict[str, list[int]] = {}
    tcount: Counter = Counter()
    for i, c in enumerate(chats):
        body = str(c.get("text") or "")
        comp = F.compact(body)
        cg = F.char_ngrams(body)
        cache[c.get("chat_id")] = (cg, len(comp))
        tcount[comp] += 1
        if len(comp) < MIN_CHAT_CHARS:
            continue
        for g in cg:
            post.setdefault(g, []).append(i)
    df_max = max(LOC_DF_MIN, int(LOC_DF_FRAC * len(chats)))
    rare = {g: v for g, v in post.items() if len(v) <= df_max}
    sel = []
    for u in utts:
        ug = F.char_ngrams(u.text)
        r = [g for g in ug if g in rare]
        if len(r) >= LOC_MIN_SHARED:
            sel.append((len(r), u.start_ms, u, ug, r))
    sel.sort(key=lambda x: (-x[0], x[1], x[2].seg_id))
    sel = sel[:LOC_MAX_UTTS]
    votes: list[tuple[int, float, str, int]] = []           # (차이, 무게, chat_id, seg_id)
    for _n, _t, u, ug, r in sel:
        hits: Counter = Counter()
        for g in r:
            for i in rare[g]:
                hits[i] += 1
        mine = []
        for i, n in hits.items():
            if n < LOC_MIN_SHARED:
                continue
            c = chats[i]
            cand = score_pair(c, u, nicknames=[], ngram_cache=cache, utt_ngrams=ug)
            if cand is None:
                continue
            w = cand.score / max(1, tcount[F.compact(str(c.get("text") or ""))])
            mine.append((w, asr_start_ms + u.start_ms - c["t_ms"], c["chat_id"]))
        mine.sort(key=lambda x: (-x[0], x[1], x[2]))
        votes += [(d, w, cid, u.seg_id) for w, d, cid in mine[:LOC_TOPK]]
    if not votes:
        return None
    votes.sort(key=lambda v: (v[0], v[2], v[3]))
    wins = []                                               # (무게 합, 왼쪽, 오른쪽)
    j, s = 0, 0.0
    for i, v in enumerate(votes):
        s += v[1]
        while v[0] - votes[j][0] > LOC_WIN_MS:
            s -= votes[j][1]
            j += 1
        wins.append((s, j, i))
    best = max(wins, key=lambda w: (round(w[0], 9), -(votes[w[2]][0] - votes[w[1]][0]), -w[1]))
    s1, j1, i1 = best
    members = votes[j1:i1 + 1]
    n_utts, n_chats = len({m[3] for m in members}), len({m[2] for m in members})
    if n_utts < LOC_MIN_UTTS or n_chats < LOC_MIN_CHATS:
        return None
    center = (votes[j1][0] + votes[i1][0]) / 2
    second = max((w[0] for w in wins if abs((votes[w[1]][0] + votes[w[2]][0]) / 2 - center) > LOC_SECOND_GAP_MS),
                 default=0.0)
    diff = int(statistics.median_low(m[0] for m in members))
    return Located(file_start_ms=max(0, -diff), diff_ms=diff, peak=s1, second=second, votes=len(votes),
                   peak_votes=len(members), peak_utts=n_utts, peak_chats=n_chats, utts_used=len(sel), clamped=-diff < 0)


def candidate_rows(cands: list[Cand], file_start_ms: int, asr_start_ms: int = 0) -> list[dict]:
    """--list 의 rows: 숫자·chat_id·근거 코드만(본문·닉네임·발화 글 없음)."""
    out = []
    for c in cands:
        end = (asr_start_ms + c.utt.end_ms) if c.utt is not None and c.utt.end_ms is not None else None
        out.append({"chat_id": c.chat["chat_id"], "chat_ms": c.chat["t_ms"], "video_ms": c.video_ms,
                    "video_end_ms": end if end is not None and end >= c.video_ms else None, "basis": c.basis,
                    "score": c.score, "diff_ms": c.diff_ms(file_start_ms)})
    return out


# ── (b) asr 가 없을 때 ────────────────────────────────────────────────────

def question_candidates(chats: list[dict], *, top: int = 20) -> list[tuple[float, dict]]:
    """(b) 질문형이고 짧은 채팅을 시간대 top 개 칸으로 나눠 칸마다 하나씩."""
    if not chats or top <= 0:
        return []
    counts = Counter(F.compact(str(c.get("text") or "")) for c in chats)
    ts = [c["t_ms"] for c in chats]
    scored = []
    for c in chats:
        body = str(c.get("text") or "")
        comp = F.compact(body)
        if not (Q_MIN_CHARS <= len(comp) <= Q_MAX_CHARS):
            continue
        if not F.is_question(body) or F.repeat_ratio(body) >= 0.6:
            continue
        if c.get("role") not in (None, "viewer", "unknown"):
            continue
        s = 1.0
        if "?" in body or "？" in body:
            s += 0.5
        if 6 <= len(comp) <= 20:
            s += 0.3
        if counts[comp] == 1:
            s += 0.5                                    # 같은 문구가 없어야 다시보기에서 찾기 쉽다
        t = c["t_ms"]
        dens = bisect.bisect_right(ts, t + DENSITY_WIN_MS) - bisect.bisect_left(ts, t - DENSITY_WIN_MS) - 1
        s += 1.0 / (1.0 + dens / 10.0)                  # 주변 채팅이 적을수록 읽힐 가능성·찾기 쉬움
        scored.append((round(s, 3), c))
    if not scored:
        return []
    t0, t1 = ts[0], ts[-1]
    width = max(1, (t1 - t0 + 1) / top)
    best: dict[int, tuple[float, dict]] = {}
    for s, c in scored:
        b = min(top - 1, int((c["t_ms"] - t0) / width))
        cur = best.get(b)
        if cur is None or (s, _neg(c["chat_id"])) > (cur[0], _neg(cur[1]["chat_id"])):
            best[b] = (s, c)
    return [best[b] for b in sorted(best)]


def _neg(s: str) -> tuple:
    """동점이면 chat_id 가 작은 쪽을 고르려고 쓰는 비교 키."""
    return tuple(-ord(ch) for ch in s)


# ── 출력 ───────────────────────────────────────────────────────────────────

def anchor_arg(video_ms: int | None, chat_ms: int) -> str:
    v = tc.fmt_time(video_ms, always_hours=True) if video_ms is not None else "<영상시각>"
    return f"--anchor {v}={tc.fmt_time(chat_ms, always_hours=True)}"


def print_asr_table(cands: list[Cand], vno: dict, file_start_ms: int, *, show_text: bool) -> None:
    print(" #  점수   근거        발화(영상)      채팅(VOD)       차이      chat_id                시청자 번호"
          + ("  채팅 본문 | 발화" if show_text else ""))
    for i, c in enumerate(cands, 1):
        line = (f"{i:>2}  {c.score:<5.2f}  {c.basis:<10}  {tc.fmt_display(c.video_ms):<14}  "
                f"{tc.fmt_display(c.chat['t_ms']):<14}  {tc.fmt_offset(c.diff_ms(file_start_ms)):>8}  "
                f"{c.chat['chat_id']:<21}  {cw.fmt_viewer_no(c.chat.get('viewer_id'), vno)}")
        if show_text:
            line += f"  {cw.clean_text(c.chat.get('text'), 40)} | {cw.clean_text(c.utt.text, 60)}"
        print(line)


def print_question_table(rows: list[tuple[float, dict]], vno: dict, *, show_text: bool) -> None:
    print(" #  점수   채팅(VOD)       chat_id                시청자 번호  가림닉네임" + ("  본문" if show_text else ""))
    for i, (s, c) in enumerate(rows, 1):
        line = (f"{i:>2}  {s:<5.2f}  {tc.fmt_display(c['t_ms']):<14}  {c['chat_id']:<21}  "
                f"{cw.fmt_viewer_no(c.get('viewer_id'), vno):<11}  {cw.clean_text(c.get('nickname_masked'), 10)}")
        if show_text:
            line += f"  {cw.clean_text(c.get('text'), 60)}"
        print(line)


def candidate_counts(project: str, *, asr: str | None = None, top: int = 20, asr_start_ms: int | None = None,
                     file_start_ms: int = 0, lag_before_ms: int = 120_000, lag_after_ms: int = 30_000,
                     t_from: int | None = None, t_to: int | None = None, use_identities: bool = True,
                     locate: bool = False, rows: bool = False) -> dict:
    """(a)·(b)·(c) 후보를 main 과 같은 함수로 고르고 **숫자만** 돌려준다(본문·닉네임 없음).

    {"mode": "asr"|"question", "chats": text 채팅 수, "utterances": 발화 수(asr 일 때), "candidates": 후보 수,
     "consistent": 차이 중앙값 ±3초 안 후보 수(asr 일 때), "median_diff_ms": 그 중앙값(asr 일 때, file_start_ms 축),
     "file_start_ms": 그 축의 클립 시작, "asr_start_ms": 더한 asr 시작(asr 일 때), "warnings": 읽기 경고 수, "elapsed_s"}.
    asr_start_ms 가 None 이면 asr.json 이 기억하는 값(없으면 0), 주었는데 기억과 다르면 AsrStartConflict(ValueError).
    locate=True(asr 필요)면 "located": Located.doc() 또는 None(자리를 못 찾음 — 후보 0), 2단계는 그 file_start_ms 로.
    rows=True 면 "rows": candidate_rows(…)(chat_id · 시각 · 근거 코드 · 점수 — D-32 화면용).
    채팅 파일이 없으면 FileNotFoundError, asr 를 읽지 못하면 ValueError·OSError, locate 인데 asr 가 없으면 ValueError."""
    t0 = time.perf_counter()
    if locate and not asr:
        raise ValueError("locate_needs_asr")
    records, warns = cw.load_messages(project)
    records = cw.sort_messages(records)
    chats = text_chats(records, t_from, t_to)
    out: dict = {"mode": "asr" if asr else "question", "chats": len(chats), "warnings": len(warns)}
    if asr:
        utts, rec = load_asr_doc(asr)
        asr_start_ms = resolve_asr_start(asr_start_ms, rec)
        out["asr_start_ms"] = asr_start_ms
        fs = file_start_ms
        if locate:
            loc = locate_clip(chats, utts, asr_start_ms=asr_start_ms)
            out["located"] = loc.doc() if loc is not None else None
            if loc is None:
                out.update(utterances=len(utts), candidates=0, consistent=0, median_diff_ms=None, file_start_ms=None,
                           identities=None, elapsed_s=round(time.perf_counter() - t0, 2))
                if rows:
                    out["rows"] = []
                return out
            fs = loc.file_start_ms
        identities = None
        if use_identities:
            try:
                identities = cw.load_identities(project)
            except FileNotFoundError:
                identities = None
        cands = asr_candidates(chats, utts, identities=identities, asr_start_ms=asr_start_ms,
                               file_start_ms=fs, lag_before_ms=lag_before_ms, lag_after_ms=lag_after_ms,
                               top=top)
        med, cons = consistent_subset(cands, fs)
        out.update(utterances=len(utts), candidates=len(cands), consistent=len(cons), median_diff_ms=med,
                   file_start_ms=fs, identities=identities is not None)
        if rows:
            out["rows"] = candidate_rows(cands, fs, asr_start_ms)
    else:
        out.update(candidates=len(question_candidates(chats, top=top)))
    out["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="시각 보정 앵커 후보를 제안한다 (후보일 뿐, 확정은 사람)")
    ap.add_argument("--project", required=True, help="프로젝트 폴더")
    ap.add_argument("--asr", help="engine asr 결과 asr.json. 없으면 질문형 채팅 목록을 낸다")
    ap.add_argument("--top", type=int, default=20, help="후보 개수 (기본 20)")
    ap.add_argument("--asr-start-ms", type=int, default=None,
                    help="asr wav 0초의 영상 파일 시각(ms). wav 를 중간부터 뽑았을 때. 주지 않으면 asr.json 의 "
                         "audio.asr_start_ms(없으면 0), 주었는데 그 값과 다르면 멈춤(asr_start_conflict)")
    ap.add_argument("--file-start-ms", type=int, default=0, help="영상 파일 0초의 VOD 시각(ms). 클립 파일일 때")
    ap.add_argument("--lag-before", type=float, default=120.0, help="(a) 발화보다 몇 초 앞 채팅까지 볼지 (기본 120)")
    ap.add_argument("--lag-after", type=float, default=30.0, help="(a) 발화보다 몇 초 뒤 채팅까지 볼지 (기본 30)")
    ap.add_argument("--from", dest="t_from", help="이 채팅 시각부터 (1:02:03 형식, 정수는 ms)")
    ap.add_argument("--to", dest="t_to", help="이 채팅 시각까지")
    ap.add_argument("--no-identities", action="store_true", help="(a) 보호 구역 닉네임 목록을 쓰지 않는다")
    ap.add_argument("--no-text", action="store_true", help="본문·발화 글을 화면에도 찍지 않는다")
    ap.add_argument("--json", action="store_true",
                    help="표·본문 없이 후보 수 같은 숫자만 JSON 한 줄로 낸다(candidate_counts)")
    ap.add_argument("--locate", action="store_true",
                    help="(c) 영상이 VOD 의 어디인지 먼저 찾고(--asr 필요) 그 자리로 (a) 를 돈다. --file-start-ms 는 무시")
    ap.add_argument("--list", action="store_true",
                    help="--json 줄에 후보 rows(chat_id·시각·근거 코드·점수, 본문 없음)를 더한다 (앱 시간 정렬 화면용)")
    a = ap.parse_args(argv)

    def bad(msg: str, code: str) -> int:
        if a.json:
            print(json.dumps({"error": code}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 1

    if a.top <= 0:
        return bad("오류: --top 은 1 이상", "top")
    if (a.asr_start_ms or 0) < 0 or a.file_start_ms < 0 or a.lag_before < 0 or a.lag_after < 0:
        return bad("오류: 시작 시각·창은 0 이상", "negative")
    t_from = cw.parse_at(a.t_from) if a.t_from else None
    t_to = cw.parse_at(a.t_to) if a.t_to else None
    if (a.t_from and t_from is None) or (a.t_to and t_to is None):
        return bad("오류: --from / --to 시각을 읽지 못했다", "time")

    if a.locate and not a.asr:
        return bad("오류: --locate 는 --asr 가 필요하다", "locate_needs_asr")
    if a.json:
        if a.asr and not os.path.isfile(a.asr):
            return bad("", "asr_missing")
        try:
            doc = candidate_counts(a.project, asr=a.asr, top=a.top, asr_start_ms=a.asr_start_ms,
                                   file_start_ms=a.file_start_ms, lag_before_ms=int(a.lag_before * 1000),
                                   lag_after_ms=int(a.lag_after * 1000), t_from=t_from, t_to=t_to,
                                   use_identities=not a.no_identities, locate=a.locate, rows=a.list)
        except FileNotFoundError:
            return bad("", "messages_missing")
        except AsrStartConflict:
            return bad("", AsrStartConflict.code)
        except (OSError, ValueError):
            return bad("", "asr_unreadable")
        print(json.dumps(doc, ensure_ascii=False))
        return 0

    t_start = time.perf_counter()
    try:
        records, warns = cw.load_messages(a.project)
    except FileNotFoundError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    records = cw.sort_messages(records)
    vno = cw.viewer_numbers(records)
    chats = text_chats(records, t_from, t_to)
    show_text = not a.no_text

    print(DISCLAIMER)
    for w in warns:
        print("주의: " + w)

    if a.asr:
        try:
            utts, rec = load_asr_doc(a.asr)
            a.asr_start_ms = resolve_asr_start(a.asr_start_ms, rec)
        except AsrStartConflict:
            print("오류: --asr-start-ms 가 asr.json 이 기억하는 asr 시작(audio.asr_start_ms)과 다르다 — 빼고 다시 하라",
                  file=sys.stderr)
            return 1
        except (OSError, ValueError) as e:
            print(f"오류: asr.json 을 읽지 못했다: {e}", file=sys.stderr)
            return 1
        identities = None
        if not a.no_identities:
            try:
                identities = cw.load_identities(a.project)
            except FileNotFoundError:
                identities = None
        fs = a.file_start_ms
        if a.locate:
            loc = locate_clip(chats, utts, asr_start_ms=a.asr_start_ms)
            if loc is None:
                print(f"(c) 자리를 찾지 못했다 — 발화 {len(utts)}개 × text 채팅 {len(chats)}건에서 서로 맞는 짝이 "
                      f"{LOC_MIN_UTTS}개 넘게 모이지 않았다(채팅을 읽지 않은 구간일 수 있다). 클립 시작을 알면 --file-start-ms 로")
                return 0
            fs = loc.file_start_ms
            print(f"(c) 자리: 영상 0초 ≈ VOD {tc.fmt_display(fs)} (읽기 지연이 든 값) · 표 {loc.peak_votes}/{loc.votes} · "
                  f"발화 {loc.peak_utts} · 채팅 {loc.peak_chats} · 창 합 {loc.peak:.2f} (다른 자리 {loc.second:.2f})"
                  + (" · 0 밑이라 0 으로 자름" if loc.clamped else ""))
        print(f"(a) 발화 {len(utts)}개 × text 채팅 {len(chats)}건 · 창 발화 −{a.lag_before:g}초 ~ +{a.lag_after:g}초 · "
              f"asr 시작 {tc.fmt_display(a.asr_start_ms)} · 클립 시작 {tc.fmt_display(fs)}")
        print("    닉네임 대조: " + ("보호 구역 identities.jsonl 사용 (닉네임은 화면에 찍지 않음)" if identities is not None
                               else "없음 — 가림 닉네임 첫 글자만 보는 약한 단서(name?)"))
        cands = asr_candidates(chats, utts, identities=identities, asr_start_ms=a.asr_start_ms,
                               file_start_ms=fs, lag_before_ms=int(a.lag_before * 1000),
                               lag_after_ms=int(a.lag_after * 1000), top=a.top)
        elapsed = time.perf_counter() - t_start
        print(f"후보 {len(cands)}개 ({elapsed:.2f}초)")
        if not cands:
            print("후보가 없다. 말이 많은 구간의 asr 이나 --lag-before 를 넓혀 다시 해 보거나, --asr 없이 질문형 목록을 쓴다.")
            return 0
        print_asr_table(cands, vno, fs, show_text=show_text)
        med, cons = consistent_subset(cands, fs)
        print(f"차이 중앙값 {tc.fmt_offset(med)} ({med}ms) · 그 ±{CONSISTENT_MS // 1000}초 안 후보 {len(cons)}개 "
              "(서로 맞는 후보가 많을수록 그 오프셋일 가능성이 크다. 그래도 확인은 다시보기로)")
        print("calibrate 형식 (후보 전부, 확인한 것만 골라 쓴다):")
        for c in cands:
            print(f"  {anchor_arg(c.video_ms, c.chat['t_ms'])}")
        pick = spread_pick(cons, key=lambda c: c.chat["t_ms"])
        if pick:
            fsa = f" --file-start-ms {a.file_start_ms}" if a.file_start_ms and not a.locate else ""
            print("예시 명령 (서로 맞는 후보 중 앞·가운데·뒤 — 다시보기에서 확인한 뒤에만):")
            print(f"  .venv\\Scripts\\python tools\\calibrate.py --project {a.project} "
                  + " ".join(anchor_arg(c.video_ms, c.chat["t_ms"]) for c in pick) + f"{fsa} --dry-run")
        return 0

    rows = question_candidates(chats, top=a.top)
    elapsed = time.perf_counter() - t_start
    print(f"(b) asr 없음 — 질문형·짧은 채팅을 시간대 {a.top}칸에서 하나씩. text 채팅 {len(chats)}건 중 후보 {len(rows)}개 "
          f"({elapsed:.2f}초)")
    print("    다시보기에서 각 채팅 시각 근처를 보며 스트리머가 읽거나 답하는 순간의 영상 시각을 적는다.")
    print("    주변 채팅 보기: tools/chat_window.py --project <폴더> --at <채팅 시각>")
    if not rows:
        print("후보가 없다.")
        return 0
    print_question_table(rows, vno, show_text=show_text)
    print("calibrate 형식 (<영상시각> 은 사람이 다시보기에서 찾아 채운다):")
    for _, c in rows:
        print(f"  {anchor_arg(None, c['t_ms'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
