"""주제 흐름 topics-v0 (08 D-01 방송 분석, D-17 5): 전사문과 채팅을 몇 분 단위로 묶어 '주제 덩어리' 를 낸다.

방법(로컬·가볍게, 외부 호출 없음 — D-06, D-17 6)
  1. 범위를 window_ms(기본 60초) 창으로 자른다. 발화는 VOD 시작 시각, 채팅은 VOD 시각으로 창에 넣는다.
  2. 창마다 키워드 개수: 발화 낱말은 1, 채팅 낱말은 '그 낱말을 쓴 서로 다른 시청자 수 × chat_weight'
     (한 사람이 도배해도 1명). 낱말은 features.normalize → 공백 자르기 → 조사 떼기 → 불용어·2~6자 밖 제거.
  3. 창 사이 문서 빈도로 tf-idf. 너무 흔한 낱말(창의 max_df_ratio 넘게 나옴)은 버린다.
  4. 틈(창 g-1 | 창 g)마다 왼쪽 k 창 합과 오른쪽 k 창 합의 코사인 유사도. 임베딩 함수(embed)를 주면
     창 글의 임베딩 코사인을 embed_weight 만큼 섞는다(없어도 돈다).
  5. TextTiling 깊이 = (왼쪽 봉우리 − 유사도) + (오른쪽 봉우리 − 유사도). 깊이가 기준을 넘는 틈을 깊은 것부터
     고르되 덩어리가 min_block_ms 보다 짧아지지 않게 한다.
  6. 바뀐 시각은 창 경계에서 ±1 창 안의 '새 덩어리 이름 키워드를 처음 말한 발화' 시작으로 당긴다(없으면 창 경계).
  7. 덩어리마다 키워드: 덩어리 tf-idf 상위 8개. 채팅에서만 나온 낱말은 서로 다른 시청자 min_chat_viewers 명
     이상이 쓴 것만 후보(한 사람의 닉네임·사적인 말이 주제 이름이 되지 않게). 닉네임 목록이 있으면 그 낱말도 뺀다.

결과(schemas/topic-flow.schema.json, app/core/topics.validate)는 숫자·키워드만 담는다. 전사문·채팅 본문은 없다.
표준 출력에는 숫자만 낸다(키워드도 내지 않는다 — 시범 프로젝트 결과는 봉인 경로로만, D-15 8).

실행(저장소 루트. 무거운 의존성이 없어 앱 .venv 로도 돈다)
  python -m engine.topics --project <폴더> --out <topic-flow.json> [--asr F] [--start-ms N --end-ms N]
         [--offset-ms N] [--file-start-ms N] [--asr-start-ms N] [--window-sec 60] [--min-block-sec 180] [--no-identities]
         [--semantic off|onnx|lmstudio] [--models-dir D] [--embed engine.<모듈>:<함수>]
  --semantic 은 engine.semantic 의 로컬 임베딩(받지 않음, D-17 6)을 섞는다. 기본 off. 없으면 낱말만으로 돈다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping, Sequence

from app.core import topics as T
from app.core.timecode import video_to_chat_ms

from . import features as F

ENGINE_NAME = "chatcoach-topics"
ENGINE_VERSION = "0.1.0"

# 말할 때 흔한 군말·연결어(features.STOPWORDS 에 더한다). 주제를 가르지 못하는 낱말.
TOPIC_STOPWORDS = frozenset(F.STOPWORDS | {
    "그러니까", "그니까", "이렇게", "저렇게", "그렇게", "어떻게", "아니야", "아니요", "그래요", "그래", "맞아", "맞아요",
    "되게", "엄청", "그래도", "그러면", "그런데", "거야", "거예요", "건데", "때문", "때문에", "이건", "그건", "저건",
    "하나", "다시", "계속", "빨리", "잠깐", "잠시만", "일단", "이번", "저번", "여러분", "얘들아", "오케이", "대박",
    "생각", "느낌", "사람", "해야", "하자", "가자", "보자", "해봐", "봐봐", "했어", "하는데", "있는데", "없는데",
    "그쵸", "그죠", "뭐지", "뭐야", "어어", "아아", "오오", "와우", "네네네", "예예", "감사", "고마워", "고마워요",
    "이제는", "아까", "방금", "그다음", "다음", "이거야", "그거야", "어떡해", "왜냐면", "그러고", "하는거", "할게",
    "할게요", "해줘", "해주세요", "합니다", "입니다", "있습니다", "없습니다", "그렇죠", "아니고", "같은데", "같고",
})


@dataclass(frozen=True)
class TopicConfig:
    """엔진 시작값. 안내서 숫자가 아니라 엔진이 정한 값이다(측정으로 고친다)."""
    window_ms: int = 60000          # 창 길이
    side_windows: int = 2           # 틈 양쪽에서 합칠 창 수(k)
    min_block_ms: int = 180000      # 덩어리 최소 길이
    chat_weight: float = 0.5        # 채팅 시청자 1명 = 발화 낱말 0.5개
    max_df_ratio: float = 0.6       # 창의 이 비율 넘게 나오는 낱말은 버림(창 5개 이상일 때)
    min_depth: float = 0.15         # 경계 깊이 하한
    depth_sd_factor: float = 0.5    # 기준 = max(min_depth, 평균 − sd × 이 값) (TextTiling '느슨한' 기준)
    embed_weight: float = 0.5       # 임베딩을 줄 때 섞는 비율
    min_chat_viewers: int = 3       # 채팅에서만 나온 키워드의 최소 시청자 수
    max_keywords: int = T.MAX_KEYWORDS
    max_name_keywords: int = T.MAX_NAME_KEYWORDS

    def to_json(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        b = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(b.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Utt:
    t_ms: int          # VOD 시작
    end_ms: int        # VOD 끝
    text: str


@dataclass(frozen=True)
class Msg:
    t_ms: int          # VOD
    viewer_id: str
    text: str


# 글 목록 → 벡터 목록 함수, 또는 .encode(texts) 가 있는 객체(engine.semantic.Embedder)
Embedder = Callable[[Sequence[str]], Sequence[Sequence[float]]]


# ── 낱말 ─────────────────────────────────────────────────────────────────

def tokens(text: str) -> list[str]:
    """주제 낱말 목록(순서·중복 유지). 2~6자, 숫자만·같은 글자 되풀이(하하·오오) 제외."""
    out = []
    for w in F.normalize(text).split():
        if w in TOPIC_STOPWORDS:
            continue
        x = F._strip_particle(w)
        if len(x) > 2 and x.endswith("님"):        # 호칭 '님' 을 뗀다(닉네임 호명이 '감자님' 처럼 따로 세지지 않게)
            x = x[:-1]
        if x in TOPIC_STOPWORDS or not T.is_keyword(x):
            continue
        if x.isdigit() or len(set(x)) == 1:
            continue
        out.append(x)
    return out


def nickname_terms(nicknames: Iterable[str]) -> tuple[set[str], tuple[str, ...]]:
    """닉네임에서 뺄 낱말: (그 낱말 그대로 뺄 집합, 3자 이상 키워드가 들어 있으면 뺄 이어 붙인 형태들)."""
    exact, joined = set(), []
    for n in nicknames:
        c = F.compact(n)
        if len(c) >= 2:
            exact.add(c)
            joined.append(c)
        for w in F.normalize(n).split():
            if len(w) >= 2:
                exact.add(w)
                exact.add(F._strip_particle(w))
    return exact, tuple(joined)


def _excluded(kw: str, nick: tuple[set[str], tuple[str, ...]] | None) -> bool:
    if not nick:
        return False
    exact, joined = nick
    return kw in exact or (len(kw) >= 3 and any(kw in j for j in joined))


# ── 벡터 ─────────────────────────────────────────────────────────────────

def _cos(a: Mapping[str, float], b: Mapping[str, float]) -> float | None:
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return None
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items()) / (na * nb)


def _cos_dense(a: Sequence[float] | None, b: Sequence[float] | None) -> float | None:
    if a is None or b is None:
        return None
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return None
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _add(dst: dict, src: Mapping[str, float]) -> None:
    for k, v in src.items():
        dst[k] = dst.get(k, 0.0) + v


def _mean_dense(vecs: Sequence[Sequence[float] | None]) -> list[float] | None:
    vs = [v for v in vecs if v is not None]
    if not vs:
        return None
    n = len(vs[0])
    return [sum(v[i] for v in vs) / len(vs) for i in range(n)]


# ── 창 ───────────────────────────────────────────────────────────────────

@dataclass
class Window:
    start_ms: int
    end_ms: int
    speech: Counter
    chat_viewers: dict          # 낱말 → 시청자 집합
    utterances: int = 0
    chats: int = 0
    texts: list = None          # 임베딩용(메모리에만)

    def raw(self, chat_weight: float) -> dict[str, float]:
        d = {k: float(v) for k, v in self.speech.items()}
        for k, vs in self.chat_viewers.items():
            d[k] = d.get(k, 0.0) + chat_weight * len(vs)
        return d


def build_windows(utts: Sequence[Utt], msgs: Sequence[Msg], start_ms: int, end_ms: int,
                  window_ms: int, *, keep_text: bool = False) -> list[Window]:
    n = max(1, math.ceil((end_ms - start_ms) / window_ms))
    ws = [Window(start_ms + i * window_ms, min(end_ms, start_ms + (i + 1) * window_ms), Counter(), {},
                 texts=[] if keep_text else None) for i in range(n)]

    def idx(t):
        if t < start_ms or t >= end_ms:
            return None
        return min(n - 1, (t - start_ms) // window_ms)

    for u in utts:
        i = idx(u.t_ms)
        if i is None:
            continue
        w = ws[i]
        w.utterances += 1
        w.speech.update(tokens(u.text))
        if keep_text:
            w.texts.append(u.text)
    for m in msgs:
        i = idx(m.t_ms)
        if i is None:
            continue
        w = ws[i]
        w.chats += 1
        for tk in set(tokens(m.text)):
            w.chat_viewers.setdefault(tk, set()).add(m.viewer_id)
        if keep_text:
            w.texts.append(m.text)
    return ws


def idf_of(raws: Sequence[Mapping[str, float]], max_df_ratio: float) -> dict[str, float]:
    nonempty = [r for r in raws if r]
    N = len(nonempty)
    df = Counter()
    for r in nonempty:
        df.update(r.keys())
    out = {}
    for k, d in df.items():
        if N >= 5 and d / N > max_df_ratio:
            continue
        out[k] = 1.0 + math.log((1 + N) / (1 + d))
    return out


# ── 경계 ─────────────────────────────────────────────────────────────────

def gap_similarities(vecs: Sequence[Mapping[str, float]], k: int,
                     dense: Sequence[Sequence[float] | None] | None = None,
                     embed_weight: float = 0.5) -> list[float | None]:
    """틈 g(1..n-1) 의 유사도. 결과 목록의 [g-1] 이 틈 g. 한쪽이 비면 None."""
    n = len(vecs)
    out = []
    for g in range(1, n):
        L, R = {}, {}
        for v in vecs[max(0, g - k):g]:
            _add(L, v)
        for v in vecs[g:min(n, g + k)]:
            _add(R, v)
        s = _cos(L, R)
        if dense is not None:
            e = _cos_dense(_mean_dense(dense[max(0, g - k):g]), _mean_dense(dense[g:min(n, g + k)]))
            if e is not None:
                s = e if s is None else (1 - embed_weight) * s + embed_weight * e
        out.append(s)
    return out


def depth_scores(sims: Sequence[float | None]) -> list[float | None]:
    """TextTiling 깊이. None 인 틈은 None, 봉우리 찾기는 None 에서 멈춘다."""
    out = []
    n = len(sims)
    for i, s in enumerate(sims):
        if s is None:
            out.append(None)
            continue
        lp = s
        j = i - 1
        while j >= 0 and sims[j] is not None and sims[j] >= lp:
            lp = sims[j]
            j -= 1
        rp = s
        j = i + 1
        while j < n and sims[j] is not None and sims[j] >= rp:
            rp = sims[j]
            j += 1
        out.append((lp - s) + (rp - s))
    return out


def pick_boundaries(depths: Sequence[float | None], n_windows: int, min_block_windows: int,
                    min_depth: float, sd_factor: float) -> list[int]:
    """경계로 쓸 틈 번호 g(창 g 가 새 덩어리의 첫 창) 목록, 오름차순."""
    vals = [d for d in depths if d is not None]
    if not vals:
        return []
    mean = sum(vals) / len(vals)
    sd = math.sqrt(sum((d - mean) ** 2 for d in vals) / len(vals))
    cut = max(min_depth, mean - sd_factor * sd)
    cands = sorted(((d, g + 1) for g, d in enumerate(depths) if d is not None and d >= cut),
                   key=lambda x: (-x[0], x[1]))
    chosen: list[int] = []
    for _, g in cands:
        if g < min_block_windows or n_windows - g < min_block_windows:
            continue
        if any(abs(g - c) < min_block_windows for c in chosen):
            continue
        chosen.append(g)
    return sorted(chosen)


# ── 키워드 ───────────────────────────────────────────────────────────────

def block_keywords(ws: Sequence[Window], idf: Mapping[str, float], cfg: TopicConfig,
                   nick=None) -> list[str]:
    speech = Counter()
    viewers: dict[str, set] = {}
    for w in ws:
        speech.update(w.speech)
        for k, vs in w.chat_viewers.items():
            viewers.setdefault(k, set()).update(vs)
    score = {}
    for k in set(speech) | set(viewers):
        if k not in idf or _excluded(k, nick):
            continue
        nv = len(viewers.get(k, ()))
        if speech.get(k, 0) == 0 and nv < cfg.min_chat_viewers:
            continue
        score[k] = (speech.get(k, 0) + cfg.chat_weight * nv) * idf[k]
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return [k for k, _ in ranked[:cfg.max_keywords]]


def _refine_change(boundary_ms: int, lo: int, hi: int, utts: Sequence[Utt], new_kw: Sequence[str],
                   old_kw: Sequence[str]) -> int:
    """창 경계 ±1 창(lo..hi) 안에서 새 이름 키워드를 처음 말하고 옛 이름 키워드는 없는 발화 시작. 없으면 경계."""
    nk, ok = set(new_kw), set(old_kw)
    if not nk:
        return boundary_ms
    for u in utts:
        if u.t_ms < lo:
            continue
        if u.t_ms >= hi:
            break
        tk = set(tokens(u.text))
        if tk & nk and not tk & ok:
            return u.t_ms
    return boundary_ms


# ── 본체 ─────────────────────────────────────────────────────────────────

def segment(utts: Sequence[Utt], msgs: Sequence[Msg], *, start_ms: int, end_ms: int,
            cfg: TopicConfig = TopicConfig(), embed: Embedder | None = None,
            nicknames: Iterable[str] | None = None) -> dict:
    """주제 덩어리 계산. 돌려주는 dict 는 topic-flow 문서의 window_ms·range·stats·blocks·gaps 부분."""
    if end_ms <= start_ms:
        raise ValueError("end_ms 가 start_ms 보다 커야 합니다")
    utts = sorted(utts, key=lambda u: u.t_ms)
    msgs = sorted(msgs, key=lambda m: m.t_ms)
    W = cfg.window_ms
    ws = build_windows(utts, msgs, start_ms, end_ms, W, keep_text=embed is not None)
    n = len(ws)
    raws = [w.raw(cfg.chat_weight) for w in ws]
    idf = idf_of(raws, cfg.max_df_ratio)
    vecs = [{k: v * idf[k] for k, v in r.items() if k in idf} for r in raws]
    dense = None
    if embed is not None:
        fn = embed.encode if hasattr(embed, "encode") else embed   # engine.semantic 의 Embedder(.encode) 도 받는다
        idx = [i for i, w in enumerate(ws) if w.texts]
        got = list(fn([" ".join(ws[i].texts) for i in idx])) if idx else []
        if len(got) != len(idx):
            raise ValueError("임베딩 함수가 돌려준 개수가 다릅니다")
        dense = [None] * n
        for i, v in zip(idx, got):
            dense[i] = [float(x) for x in v]
        for w in ws:
            w.texts = None
    sims = gap_similarities(vecs, cfg.side_windows, dense, cfg.embed_weight)
    depths = depth_scores(sims)
    min_bw = max(1, math.ceil(cfg.min_block_ms / W))
    bounds = pick_boundaries(depths, n, min_bw, cfg.min_depth, cfg.depth_sd_factor)
    nick = nickname_terms(nicknames) if nicknames else None

    edges = [0] + bounds + [n]
    kws = [block_keywords(ws[a:b], idf, cfg, nick) for a, b in zip(edges, edges[1:])]
    starts = [start_ms]
    for j, g in enumerate(bounds):
        bms = ws[g].start_ms
        lo = max(ws[g - 1].start_ms, starts[-1] + 1)
        hi = ws[g].end_ms
        new_name, old_name = kws[j + 1][:cfg.max_name_keywords], kws[j][:cfg.max_name_keywords]
        starts.append(_refine_change(bms, lo, hi, utts, new_name, old_name))
    ends = starts[1:] + [end_ms]

    blocks = []
    for j, (s, e) in enumerate(zip(starts, ends)):
        k = kws[j]
        nu = sum(1 for u in utts if s <= u.t_ms < e or (j == len(starts) - 1 and u.t_ms == e))
        nc = sum(1 for m in msgs if s <= m.t_ms < e or (j == len(starts) - 1 and m.t_ms == e))
        g = bounds[j - 1] - 1 if j > 0 else None
        blocks.append({
            "index": j, "start_ms": s, "end_ms": e, "keywords": k,
            "name_keywords": k[:cfg.max_name_keywords], "top_keyword": k[0] if k else None,
            "change_ms": s if j > 0 else None,
            "boundary_score": round(depths[g], 4) if j > 0 else None,
            "utterances": nu, "chats": nc,
        })
    gaps = [{"at_ms": ws[g + 1].start_ms, "similarity": None if s is None else round(s, 4),
             "depth": None if d is None else round(d, 4)} for g, (s, d) in enumerate(zip(sims, depths))]
    in_rng = lambda t: start_ms <= t <= end_ms  # noqa: E731
    stats = {"windows": n, "blocks": len(blocks), "utterances": sum(1 for u in utts if in_rng(u.t_ms)),
             "chats": sum(1 for m in msgs if in_rng(m.t_ms)), "vocabulary": len(idf)}
    return {"window_ms": W, "range": {"start_ms": start_ms, "end_ms": end_ms}, "stats": stats,
            "blocks": blocks, "gaps": gaps}


def build_doc(part: Mapping, *, cfg: TopicConfig, run_id: str, created_at: str, video_no: str | None = None,
              messages_sha256: str | None = None, asr_sha256: str | None = None,
              embedding: str | None = None) -> dict:
    return {
        "schema": T.SCHEMA, "schema_version": T.SCHEMA_VERSION, "run_id": run_id, "created_at": created_at,
        "engine": {"name": ENGINE_NAME, "version": ENGINE_VERSION, "config_sha256": cfg.sha256(),
                   "embedding": embedding},
        "target": {"video_no": video_no, "messages_sha256": messages_sha256, "asr_sha256": asr_sha256},
        "time_axis": T.TIME_AXIS, **part,
    }


# ── 입력 · 출력 ──────────────────────────────────────────────────────────

def inputs_from_project(project_dir: str, *, asr_path: str | None = None, offset_ms: int | None = None,
                        file_start_ms: int | None = None, use_identities: bool = True, asr_start_ms: int = 0):
    """engine.link 의 읽기를 그대로 쓴다. 발화 시각은 VOD 로 바꾼다. (utts, msgs, nicknames, inp)

    asr_start_ms: asr 를 영상 파일의 어느 시각부터 잘라 냈나(L.load_project 가 보정 전에 더한다)."""
    from . import link as L
    inp = L.load_project(project_dir, use_identities=use_identities, offset_ms=offset_ms,
                         file_start_ms=file_start_ms, asr_path=asr_path, asr_start_ms=asr_start_ms)
    cal, fs = inp.calibration, inp.file_start_in_vod_ms
    utts = [Utt(video_to_chat_ms(u.start_ms, cal, fs), video_to_chat_ms(u.end_ms, cal, fs), u.text)
            for u in inp.utterances]
    msgs = [Msg(c.t_ms, c.viewer_id, c.text) for c in inp.chats]
    nicks = None
    if inp.nicknames:
        nicks = [n for names in inp.nicknames.values() for n in names]
    return utts, msgs, nicks, inp


def load_embedder(spec: str) -> Embedder:
    """'engine.<모듈>:<함수>' 를 불러온다. 엔진 패키지 안 모듈만 받는다(함수는 글 목록 → 벡터 목록)."""
    import importlib
    mod, _, fn = spec.partition(":")
    if not mod.startswith("engine.") or not fn:
        raise ValueError("--embed 는 'engine.<모듈>:<함수>' 모양이어야 합니다")
    f = getattr(importlib.import_module(mod), fn, None)
    if not callable(f):
        raise ValueError(f"{spec} 함수를 찾지 못했습니다")
    return f


def load_semantic(backend: str, models_dir: str | None = None):
    """engine.semantic.load_default 로 로컬 임베딩을 찾는다(받지 않음, 오프라인). (embedder 또는 None, 이름 또는 None).

    모듈·모델이 없으면 낱말만으로 돈다(None). 무엇을 썼는지는 결과의 engine.embedding 에만 남긴다."""
    if backend == "off":
        return None, None
    try:
        from . import semantic as S
    except ImportError:
        return None, None
    emb, status = S.load_default(models_dir, backend=backend)
    if emb is None:
        return None, None
    name = None
    try:
        info = emb.info() or {}
        name = info.get("name") or info.get("model")
    except Exception:
        pass
    return emb, f"engine.semantic:{backend}" + (f":{name}" if isinstance(name, str) and name else "")


def write_json(doc: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.part-{os.getpid()}"
    data = json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _is_pilot_project(project_dir: str | None) -> bool:
    """폴더 이름이 pilot- 로 시작하면 시범 프로젝트(tools/engine_to_ai._is_pilot 과 같은 규칙)."""
    return bool(project_dir) and os.path.basename(os.path.abspath(project_dir)).lower().startswith("pilot-")


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m engine.topics", description="주제 흐름(topic-flow) 만들기")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--asr", help="asr.json 경로(기본 <project>/derived/asr.json)")
    ap.add_argument("--start-ms", type=int)
    ap.add_argument("--end-ms", type=int)
    ap.add_argument("--offset-ms", type=int)
    ap.add_argument("--file-start-ms", type=int,
                    help="클립 파일이 VOD 몇 ms 에서 시작하는지(project.json 보정과 짝). asr 를 잘라 낸 시작이 아니다")
    ap.add_argument("--asr-start-ms", type=int, default=0,
                    help="asr 가 영상 파일의 몇 ms 부터 잘라 낸 음성인지. 영상 파일 시각 = asr 시각 + 이 값(보정 전). 기본 0")
    ap.add_argument("--window-sec", type=int, default=60)
    ap.add_argument("--min-block-sec", type=int, default=180)
    ap.add_argument("--no-identities", action="store_true", help="닉네임 목록으로 키워드를 거르지 않음")
    ap.add_argument("--embed", help="임베딩 함수 'engine.<모듈>:<함수>' (--semantic 보다 앞선다)")
    ap.add_argument("--semantic", choices=("off", "onnx", "lmstudio"), default="off",
                    help="engine.semantic 의 로컬 임베딩을 섞는다(기본 off = 낱말만). 없으면 낱말만으로 돈다")
    ap.add_argument("--models-dir", help="--semantic 모델 폴더(기본은 engine.semantic 이 정함)")
    ns = ap.parse_args(argv)
    if _is_pilot_project(ns.project):
        # 시범 주제 흐름은 봉인 경로로만(D-15 8, D-17 5) — 평문 --out 을 쓰기 전에, 입력도 읽기 전에 거부한다
        print(json.dumps({"error": "pilot_needs_seal",
                          "detail": "시범 프로젝트는 tools/engine_to_ai.py --from-project … --topics --seal 을 쓰세요"},
                         ensure_ascii=False))
        return 1
    if ns.window_sec <= 0 or ns.min_block_sec < 0:
        ap.error("--window-sec 는 양수, --min-block-sec 는 0 이상")
    if ns.asr_start_ms < 0:
        ap.error("--asr-start-ms 는 0 이상")

    t0 = time.perf_counter()
    utts, msgs, nicks, inp = inputs_from_project(ns.project, asr_path=ns.asr, offset_ms=ns.offset_ms,
                                                 file_start_ms=ns.file_start_ms,
                                                 use_identities=not ns.no_identities, asr_start_ms=ns.asr_start_ms)
    from . import link as L
    for w in inp.asr_meta.get("warnings") or []:
        print(L.format_asr_warning(w), file=sys.stderr)
    times = [u.t_ms for u in utts] + [u.end_ms for u in utts] + [m.t_ms for m in msgs]
    start = ns.start_ms if ns.start_ms is not None else (min(times) if times else 0)
    end = ns.end_ms if ns.end_ms is not None else (max(times) + 1 if times else start + 1)
    cfg = TopicConfig(window_ms=ns.window_sec * 1000, min_block_ms=ns.min_block_sec * 1000)
    if ns.embed:
        embed, embed_name = load_embedder(ns.embed), ns.embed
    else:
        embed, embed_name = load_semantic(ns.semantic, ns.models_dir)
    part = segment(utts, msgs, start_ms=start, end_ms=end, cfg=cfg, embed=embed, nicknames=nicks)
    now = datetime.now().astimezone()
    doc = build_doc(part, cfg=cfg, run_id=f"topics-v0-{now.strftime('%Y%m%dT%H%M%S')}",
                    created_at=now.isoformat(timespec="seconds"), video_no=inp.video_no,
                    messages_sha256=inp.messages_sha256, asr_sha256=inp.asr_meta.get("sha256"),
                    embedding=embed_name)
    doc["stats"]["elapsed_s"] = round(time.perf_counter() - t0, 3)
    probs = T.validate(doc)
    if probs:
        raise ValueError(f"결과 검사 실패 {len(probs)}건: {probs[:5]}")
    write_json(doc, ns.out)
    st = doc["stats"]
    print(f"topics: 창 {st['windows']} · 덩어리 {st['blocks']} · 발화 {st['utterances']} · 채팅 {st['chats']} · "
          f"{st['elapsed_s']}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
