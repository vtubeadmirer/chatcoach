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
    asr.json 의 시각은 wav 0초 기준이다. wav 를 영상 파일 중간부터 뽑았으면 --asr-start-ms 로
    그 시작(영상 파일 시각, ms)을 준다. 영상 파일 자체가 VOD 일부(클립)이면 --file-start-ms.
    차이 = 영상 시각 − (채팅 시각 − file_start) = calibrate 가 계산할 오프셋과 같은 축.
(b) --asr 가 없으면: '질문형이고 짧은' 채팅을 시간대별로 고르게 뽑는다. 다시보기에서 그 채팅 시각 근처를
    보며 스트리머가 읽거나 답하는지 사람이 확인할 목록이다. 영상 시각 칸은 사람이 채운다.

끝에 tools/calibrate.py --anchor <영상시각>=<채팅시각> 모양으로도 찍는다.
시각 형식: --from/--to 는 1:02:03(.5) 형식, 정수는 ms (tools/chat_window.py 와 같다).
출력은 화면에만 한다. 이 도구는 파일을 쓰지 않는다.
--json: 표·본문 없이 숫자만 JSON 한 줄(candidate_counts — 앱 '파일 → 새 방송…' 이 후보 수만 센다, 08 D-31 2).
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

_NIM_NAME = re.compile(r"([0-9a-z가-힣]{1,12})님")


@dataclass
class Utt:
    seg_id: int
    start_ms: int        # wav 시각
    text: str
    name_ms: int | None  # '님' 이 든 단어의 시작 (wav 시각), 없으면 None


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
    with open(path, "r", encoding="utf-8-sig") as f:
        d = json.load(f)
    if not isinstance(d, dict) or d.get("format") != "chatcoach.asr":
        raise ValueError("chatcoach.asr 형식의 asr.json 이 아니다")
    out = []
    for i, s in enumerate(d.get("segments") or []):
        if not isinstance(s, dict) or not isinstance(s.get("start_ms"), int):
            continue
        name_ms = None
        for w in s.get("words") or []:
            if isinstance(w, dict) and "님" in str(w.get("word", "")) and isinstance(w.get("start_ms"), int):
                name_ms = w["start_ms"]
                break
        out.append(Utt(int(s.get("id", i)), int(s["start_ms"]), str(s.get("text") or ""), name_ms))
    out.sort(key=lambda u: (u.start_ms, u.seg_id))
    return out


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


def candidate_counts(project: str, *, asr: str | None = None, top: int = 20, asr_start_ms: int = 0,
                     file_start_ms: int = 0, lag_before_ms: int = 120_000, lag_after_ms: int = 30_000,
                     t_from: int | None = None, t_to: int | None = None, use_identities: bool = True) -> dict:
    """(a)·(b) 후보를 main 과 같은 함수로 고르고 **숫자만** 돌려준다(본문·닉네임·시각 목록 없음).

    {"mode": "asr"|"question", "chats": text 채팅 수, "utterances": 발화 수(asr 일 때), "candidates": 후보 수,
     "consistent": 차이 중앙값 ±3초 안 후보 수(asr 일 때), "median_diff_ms": 그 중앙값(asr 일 때), "warnings": 읽기 경고 수,
     "elapsed_s"}. 채팅 파일이 없으면 FileNotFoundError, asr 를 읽지 못하면 ValueError·OSError."""
    t0 = time.perf_counter()
    records, warns = cw.load_messages(project)
    records = cw.sort_messages(records)
    chats = text_chats(records, t_from, t_to)
    out: dict = {"mode": "asr" if asr else "question", "chats": len(chats), "warnings": len(warns)}
    if asr:
        utts = load_asr(asr)
        identities = None
        if use_identities:
            try:
                identities = cw.load_identities(project)
            except FileNotFoundError:
                identities = None
        cands = asr_candidates(chats, utts, identities=identities, asr_start_ms=asr_start_ms,
                               file_start_ms=file_start_ms, lag_before_ms=lag_before_ms, lag_after_ms=lag_after_ms,
                               top=top)
        med, cons = consistent_subset(cands, file_start_ms)
        out.update(utterances=len(utts), candidates=len(cands), consistent=len(cons), median_diff_ms=med,
                   identities=identities is not None)
    else:
        out.update(candidates=len(question_candidates(chats, top=top)))
    out["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="시각 보정 앵커 후보를 제안한다 (후보일 뿐, 확정은 사람)")
    ap.add_argument("--project", required=True, help="프로젝트 폴더")
    ap.add_argument("--asr", help="engine asr 결과 asr.json. 없으면 질문형 채팅 목록을 낸다")
    ap.add_argument("--top", type=int, default=20, help="후보 개수 (기본 20)")
    ap.add_argument("--asr-start-ms", type=int, default=0, help="asr wav 0초의 영상 파일 시각(ms). wav 를 중간부터 뽑았을 때")
    ap.add_argument("--file-start-ms", type=int, default=0, help="영상 파일 0초의 VOD 시각(ms). 클립 파일일 때")
    ap.add_argument("--lag-before", type=float, default=120.0, help="(a) 발화보다 몇 초 앞 채팅까지 볼지 (기본 120)")
    ap.add_argument("--lag-after", type=float, default=30.0, help="(a) 발화보다 몇 초 뒤 채팅까지 볼지 (기본 30)")
    ap.add_argument("--from", dest="t_from", help="이 채팅 시각부터 (1:02:03 형식, 정수는 ms)")
    ap.add_argument("--to", dest="t_to", help="이 채팅 시각까지")
    ap.add_argument("--no-identities", action="store_true", help="(a) 보호 구역 닉네임 목록을 쓰지 않는다")
    ap.add_argument("--no-text", action="store_true", help="본문·발화 글을 화면에도 찍지 않는다")
    ap.add_argument("--json", action="store_true",
                    help="표·본문 없이 후보 수 같은 숫자만 JSON 한 줄로 낸다(candidate_counts)")
    a = ap.parse_args(argv)

    def bad(msg: str, code: str) -> int:
        if a.json:
            print(json.dumps({"error": code}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 1

    if a.top <= 0:
        return bad("오류: --top 은 1 이상", "top")
    if a.asr_start_ms < 0 or a.file_start_ms < 0 or a.lag_before < 0 or a.lag_after < 0:
        return bad("오류: 시작 시각·창은 0 이상", "negative")
    t_from = cw.parse_at(a.t_from) if a.t_from else None
    t_to = cw.parse_at(a.t_to) if a.t_to else None
    if (a.t_from and t_from is None) or (a.t_to and t_to is None):
        return bad("오류: --from / --to 시각을 읽지 못했다", "time")

    if a.json:
        if a.asr and not os.path.isfile(a.asr):
            return bad("", "asr_missing")
        try:
            doc = candidate_counts(a.project, asr=a.asr, top=a.top, asr_start_ms=a.asr_start_ms,
                                   file_start_ms=a.file_start_ms, lag_before_ms=int(a.lag_before * 1000),
                                   lag_after_ms=int(a.lag_after * 1000), t_from=t_from, t_to=t_to,
                                   use_identities=not a.no_identities)
        except FileNotFoundError:
            return bad("", "messages_missing")
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
            utts = load_asr(a.asr)
        except (OSError, ValueError) as e:
            print(f"오류: asr.json 을 읽지 못했다: {e}", file=sys.stderr)
            return 1
        identities = None
        if not a.no_identities:
            try:
                identities = cw.load_identities(a.project)
            except FileNotFoundError:
                identities = None
        print(f"(a) 발화 {len(utts)}개 × text 채팅 {len(chats)}건 · 창 발화 −{a.lag_before:g}초 ~ +{a.lag_after:g}초 · "
              f"asr 시작 {tc.fmt_display(a.asr_start_ms)} · 클립 시작 {tc.fmt_display(a.file_start_ms)}")
        print("    닉네임 대조: " + ("보호 구역 identities.jsonl 사용 (닉네임은 화면에 찍지 않음)" if identities is not None
                               else "없음 — 가림 닉네임 첫 글자만 보는 약한 단서(name?)"))
        cands = asr_candidates(chats, utts, identities=identities, asr_start_ms=a.asr_start_ms,
                               file_start_ms=a.file_start_ms, lag_before_ms=int(a.lag_before * 1000),
                               lag_after_ms=int(a.lag_after * 1000), top=a.top)
        elapsed = time.perf_counter() - t_start
        print(f"후보 {len(cands)}개 ({elapsed:.2f}초)")
        if not cands:
            print("후보가 없다. 말이 많은 구간의 asr 이나 --lag-before 를 넓혀 다시 해 보거나, --asr 없이 질문형 목록을 쓴다.")
            return 0
        print_asr_table(cands, vno, a.file_start_ms, show_text=show_text)
        med, cons = consistent_subset(cands, a.file_start_ms)
        print(f"차이 중앙값 {tc.fmt_offset(med)} ({med}ms) · 그 ±{CONSISTENT_MS // 1000}초 안 후보 {len(cons)}개 "
              "(서로 맞는 후보가 많을수록 그 오프셋일 가능성이 크다. 그래도 확인은 다시보기로)")
        print("calibrate 형식 (후보 전부, 확인한 것만 골라 쓴다):")
        for c in cands:
            print(f"  {anchor_arg(c.video_ms, c.chat['t_ms'])}")
        pick = spread_pick(cons, key=lambda c: c.chat["t_ms"])
        if pick:
            fs = f" --file-start-ms {a.file_start_ms}" if a.file_start_ms else ""
            print("예시 명령 (서로 맞는 후보 중 앞·가운데·뒤 — 다시보기에서 확인한 뒤에만):")
            print(f"  .venv\\Scripts\\python tools\\calibrate.py --project {a.project} "
                  + " ".join(anchor_arg(c.video_ms, c.chat["t_ms"]) for c in pick) + f"{fs} --dry-run")
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
