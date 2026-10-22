"""엔진 v0.3 의미 비교 — 발화와 채팅의 '뜻' 이 맞는지 본다 (08 D-17 6, 17 6-1 의 3).

로컬만 쓴다(D-06·D-17 6). 외부 AI·인터넷 API 를 부르지 않는다.
  - 기본: 작은 다국어 문장 임베딩 intfloat/multilingual-e5-small (MIT, ONNX fp32 약 470MB) 을
    <chatcoach-data>\\models\\multilingual-e5-small 에서 읽는다. 엔진 venv 에 이미 있는 onnxruntime(CPU)·tokenizers·numpy
    만 쓴다(새 의존성 없음). 받기는 사람이 한 번(허깅페이스, 고정 revision) 하고, 이 모듈은 받지 않는다.
  - 선택: LM Studio 의 로컬 서버(localhost 만)에 임베딩 모델이 올라가 있으면 /v1/embeddings 를 쓸 수 있다.
    서버가 꺼져 있거나 임베딩 모델이 없으면 쓰지 않는다. 결과가 켜짐 여부에 따라 달라지지 않도록 '명시할 때만' 쓴다.
  - 모델이 없거나(또는 해시가 다르거나 실행기를 못 읽으면) None 을 돌려주고, 엔진은 v0.2 로 물러나며 그렇게 기록한다.

증거 매핑(app/core/rules 의 증거 키 7종은 바꾸지 않는다). 엔진 내부 점수 → 기존 키:
  semantic_answer  (채팅이 질문, 발화가 질문이 아니고 뜻이 가장 가까움)      → answer_form   (중)
  semantic_content (채팅 뜻이 이 발화에서 처음 나옴: 앞 발화보다 뚜렷이 가까움) → unique_content(중)
  semantic_topic   (뜻이 주변 발화보다 가까움)                                → same_topic    (약)
의미만으로는 강한 증거(문구 되풀이·직접 지칭·호명)를 만들지 않는다. 그래서 의미만으로는 '직접' 이 되지 않고,
시간 창(중)과 합쳐 '높음' 까지 갈 수 있다(비슷한 채팅·이미 그 주제 규칙은 그대로).

점수는 모델마다 분포가 달라(e5 는 관계없는 문장도 0.7~0.8) 절대값과 '주변 발화 대비' 상대값을 같이 본다.
  sim   = cos(채팅, 후보 발화를 이어 붙인 글)
  base  = 채팅과 맥락 발화(채팅 앞 2분 ~ 시간 창 끝) 유사도의 중앙값
  rel   = sim − base
  prior = 채팅과 채팅 앞 발화 유사도의 최댓값 (semantic_content 의 '처음 나옴' 확인)
시작값(SemanticConfig)은 합성 문장으로만 맞춘 값이다. 실제 성적은 새 손라벨로 재야 한다(측정 문서 4장 과적합 주의).

결과·표준 출력에 채팅 본문·전사문을 넣지 않는다. 숫자(sim·rel)와 키 이름만 남긴다.
시험은 FakeEmbedder(모델 없이 도는 가짜 임베딩)로 한다.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Protocol, Sequence

from . import features as F

SEMANTIC_VERSION = "semantic-v0.3"

# 받은 모델의 고정값. 다른 파일이 놓이면 쓰지 않는다(해시가 다르면 v0.2 로 물러남).
E5_SMALL = {
    "name": "multilingual-e5-small",
    "repo": "intfloat/multilingual-e5-small",
    "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
    "license": "MIT",
    "dir": "multilingual-e5-small",
    "model_file": "onnx/model.onnx",
    "tokenizer_file": "onnx/tokenizer.json",
    "model_sha256": "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",
    "prefix": "query: ",      # e5 는 대칭 비교에 'query: ' 를 양쪽에 붙인다(모델 카드)
    "dim": 384,
    "max_tokens": 128,
}

HASH_CACHE_NAME = ".chatcoach-sha256.json"
LMSTUDIO_DEFAULT_URL = "http://127.0.0.1:1234"

Vector = tuple[float, ...]


# ── 설정 ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SemanticConfig:
    """의미 비교 시작값(엔진 값, 안내서 숫자 아님). e5-small 기준."""
    min_chat_chars: int = 4            # 이보다 짧은 채팅(공백·기호 뺀 글자)은 의미를 보지 않는다
    min_utt_chars: int = 4             # 이어 붙인 발화가 이보다 짧으면 보지 않는다
    min_context: int = 4               # 맥락 발화가 이보다 적으면 base 대신 fallback_base 를 쓴다
    fallback_base: float = 0.80        # e5-small 의 관계없는 한국어 문장 유사도 대략값
    # 2026-10-22 합성 문장 8쌍(뜻으로만 이어진 질문·대답, 관계없는 맥락 발화 10개)으로 본 e5-small 분포:
    # 맞는 대답 sim 0.81~0.87 · rel −0.02~0.07, 남의 대답 sim 0.80~0.84 · rel −0.04~0.04. 겹친다 —
    # 그래서 '창 안에서 가장 가까움(top)' 을 함께 요구하고, 의미만으로는 중간 증거까지만 준다.
    answer_min_sim: float = 0.84
    answer_min_rel: float = 0.03
    content_min_sim: float = 0.86
    content_min_rel: float = 0.05
    content_prior_margin: float = 0.02  # 앞 발화 최댓값보다 이만큼 더 가까워야 '처음 나옴'
    topic_min_sim: float = 0.85         # 약한 증거라도 후보를 만들므로(→ 관련성만) 대답보다 엄하게
    topic_min_rel: float = 0.05
    top_tolerance: float = 0.005       # 창 안 최고 유사도와 이만큼 안이면 '가장 가까움' 으로 본다
    tie_margin: float = 0.03           # '높음' 후보가 여럿일 때 1·2위 유사도 차가 이 이상이면 1위로 정한다

    def to_json(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        b = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(b.encode("utf-8")).hexdigest()


# ── 임베딩 실행기 ─────────────────────────────────────────────────────────

class Embedder(Protocol):
    def info(self) -> dict: ...
    def encode(self, texts: Sequence[str]) -> list[Vector]: ...


def _unit(v: Sequence[float]) -> Vector:
    n = math.sqrt(sum(x * x for x in v))
    if n == 0:
        return tuple(0.0 for _ in v)
    return tuple(x / n for x in v)


def cosine(a: Vector, b: Vector) -> float:
    """정규화된 두 벡터의 코사인(= 내적)."""
    return sum(x * y for x, y in zip(a, b))


class FakeEmbedder:
    """모델 없이 도는 가짜 임베딩(시험용). 결정적이다.

    글자 bigram 을 해시 칸에 넣고, concepts(낱말 → 개념 이름) 가 있으면 그 개념 칸을 크게 켠다. 그래서 글자는 다르지만
    같은 개념인 두 문장(예: '고양이' · '냥이')이 가까워진다. e5 처럼 관계없는 문장도 꽤 높게(약 base) 나오도록
    공통 칸 하나를 둔다 — 같은 SemanticConfig 로 시험하려는 것이다.
    """

    def __init__(self, concepts: Mapping[str, str] | None = None, *, dim: int = 256, base: float = 0.80,
                 concept_weight: float = 3.0):
        self.concepts = dict(concepts or {})
        self.dim = dim
        self.base = base
        self.concept_weight = concept_weight
        self._names = sorted(set(self.concepts.values()))
        self.calls = 0

    def info(self) -> dict:
        return {"backend": "fake", "name": "fake-embedder", "repo": None, "revision": None, "license": None,
                "model_sha256": None, "dim": self.dim + len(self._names) + 1}

    def _feat(self, text: str) -> list[float]:
        s = F.compact(text)
        v = [0.0] * (self.dim + len(self._names))
        for i in range(len(s) - 1):
            h = int(hashlib.md5(s[i:i + 2].encode("utf-8")).hexdigest()[:8], 16) % self.dim
            v[h] += 1.0
        for w, c in self.concepts.items():
            if F.compact(w) in s:
                v[self.dim + self._names.index(c)] += self.concept_weight
        return v

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        self.calls += 1
        out = []
        a = math.sqrt(self.base)
        b = math.sqrt(1.0 - self.base)
        for t in texts:
            f = _unit(self._feat(t))
            out.append(_unit([a] + [b * x for x in f]))
        return out


def _sha256_cached(path: str) -> str:
    """큰 모델 파일의 sha256. 크기·수정 시각이 같으면 옆 캐시 파일 값을 쓴다(engine.asr 와 같은 방식)."""
    st = os.stat(path)
    cache = os.path.join(os.path.dirname(path), HASH_CACHE_NAME)
    name = os.path.basename(path)
    try:
        with open(cache, encoding="utf-8") as f:
            c = json.load(f)
        e = c.get(name) if isinstance(c, dict) else None
        if e and e.get("size") == st.st_size and e.get("mtime_ns") == st.st_mtime_ns and e.get("sha256"):
            return e["sha256"]
    except (OSError, ValueError):
        c = {}
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(4 << 20), b""):
            h.update(blk)
    d = h.hexdigest()
    try:
        c = c if isinstance(c, dict) else {}
        c[name] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": d}
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(c, f)
    except OSError:
        pass
    return d


class OnnxE5Embedder:
    """multilingual-e5-small ONNX 를 onnxruntime(CPU)로 돌린다. 평균 풀링 + L2 정규화."""

    def __init__(self, model_dir: str, spec: Mapping = E5_SMALL, *, model_sha256: str | None = None,
                 batch_size: int = 64, threads: int | None = None):
        import numpy as np                      # 엔진 venv 에만 있다(앱 venv 에서는 ImportError → 물러남)
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self._np = np
        self.spec = dict(spec)
        self.model_dir = model_dir
        self.batch_size = batch_size
        mp = os.path.join(model_dir, *spec["model_file"].split("/"))
        tp = os.path.join(model_dir, *spec["tokenizer_file"].split("/"))
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        if threads:
            opts.intra_op_num_threads = threads
        self._sess = ort.InferenceSession(mp, sess_options=opts, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}
        self._tok = Tokenizer.from_file(tp)
        self._tok.enable_truncation(int(spec.get("max_tokens", 128)))
        self._tok.enable_padding()
        self._sha = model_sha256
        self._tok_sha = _sha256_cached(tp)
        self.provider = "CPUExecutionProvider"

    def info(self) -> dict:
        s = self.spec
        return {"backend": "onnxruntime", "provider": self.provider, "name": s["name"], "repo": s["repo"],
                "revision": s["revision"], "license": s["license"], "model_file": s["model_file"],
                "model_sha256": self._sha, "tokenizer_sha256": self._tok_sha, "dim": s["dim"],
                "prefix": s["prefix"], "max_tokens": s["max_tokens"], "pooling": "mean"}

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        np = self._np
        pre = self.spec.get("prefix", "")
        # 길이 순으로 묶어 덧채움(padding)을 줄이고, 끝에 원래 순서로 되돌린다
        order = sorted(range(len(texts)), key=lambda i: len(texts[i] or ""))
        res: list[Vector | None] = [None] * len(texts)
        for i in range(0, len(order), self.batch_size):
            idxs = order[i:i + self.batch_size]
            chunk = [pre + (texts[k] or "") for k in idxs]
            enc = self._tok.encode_batch(chunk)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            h = self._sess.run(None, feed)[0]
            m = mask[..., None].astype(h.dtype)
            v = (h * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            v = v / np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-12, None)
            for k, row in zip(idxs, v):
                res[k] = tuple(float(x) for x in row)
        return res   # type: ignore[return-value]


class LmStudioEmbedder:
    """LM Studio 로컬 서버의 /v1/embeddings (localhost 만). 서버가 꺼져 있으면 만들지 않는다(probe 가 None)."""

    def __init__(self, base_url: str, model: str, *, timeout: float = 30.0):
        _require_local(base_url)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def info(self) -> dict:
        return {"backend": "lmstudio", "name": self.model, "repo": None, "revision": None, "license": None,
                "model_sha256": None, "url": self.base_url}

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        import urllib.request
        out: list[Vector] = []
        for i in range(0, len(texts), 64):
            body = json.dumps({"model": self.model, "input": list(texts[i:i + 64])}).encode("utf-8")
            req = urllib.request.Request(self.base_url + "/v1/embeddings", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:   # localhost 만(_require_local)
                d = json.loads(r.read().decode("utf-8"))
            rows = sorted(d["data"], key=lambda x: x["index"])
            out.extend(_unit(x["embedding"]) for x in rows)
        return out


def _require_local(url: str) -> None:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("LM Studio 주소는 이 PC(localhost)만 받습니다(D-06·D-17 6)")


def probe_lmstudio(base_url: str = LMSTUDIO_DEFAULT_URL, *, timeout: float = 0.5) -> dict:
    """LM Studio 서버가 켜져 있는지, 임베딩 모델이 올라가 있는지. 네트워크는 localhost 만 본다.

    돌려주는 값: {'running': bool, 'embedding_models': [id...]}. 모델 id 는 이름에 'embed' 가 든 것만."""
    _require_local(base_url)
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/v1/models", timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return {"running": False, "embedding_models": []}
    ids = [m.get("id", "") for m in d.get("data", []) if isinstance(m, dict)]
    return {"running": True, "embedding_models": [i for i in ids if "embed" in i.lower()]}


# ── 모델 찾기 ────────────────────────────────────────────────────────────

def default_models_dir() -> str:
    from .asr import default_models_dir as d   # 같은 규칙(CHATCOACH_MODELS · CHATCOACH_DATA · 저장소 옆 chatcoach-data)
    return d()


def status_doc(status: str, *, requested: str = "link-v0.3", info: Mapping | None = None,
               cfg: SemanticConfig | None = None, detail: str | None = None) -> dict:
    """결과 문서의 semantic 칸. active 가 False 이면 엔진은 v0.2 로 돌았다."""
    active = status == "ok"
    return {"version": SEMANTIC_VERSION, "requested": requested, "active": active, "status": status,
            "fallback": None if active else "link-v0.2", "detail": detail,
            "model": dict(info) if info else None,
            "config": (cfg or SemanticConfig()).to_json() if active else None,
            "config_sha256": (cfg or SemanticConfig()).sha256() if active else None}


def load_default(models_dir: str | None = None, *, backend: str = "onnx", lmstudio_url: str = LMSTUDIO_DEFAULT_URL,
                 lmstudio_model: str | None = None, check_hash: bool = True) -> tuple[Embedder | None, dict]:
    """실행기를 찾는다. (embedder 또는 None, status_doc) — None 이면 v0.2 로 물러난다. 받지 않는다(오프라인).

    backend: 'onnx'(기본, e5-small) · 'lmstudio'(서버가 켜져 있고 임베딩 모델이 있을 때만) · 'off'."""
    if backend == "off":
        return None, status_doc("disabled")
    if backend == "lmstudio":
        try:
            p = probe_lmstudio(lmstudio_url)
        except ValueError as e:
            return None, status_doc("backend_unavailable", detail=str(e))
        if not p["running"]:
            return None, status_doc("backend_unavailable", detail="LM Studio 서버 꺼짐")
        model = lmstudio_model or (p["embedding_models"][0] if p["embedding_models"] else None)
        if not model:
            return None, status_doc("model_missing", detail="LM Studio 에 임베딩 모델 없음")
        emb = LmStudioEmbedder(lmstudio_url, model)
        return emb, status_doc("ok", info=emb.info())
    if backend != "onnx":
        raise ValueError(f"모르는 backend: {backend!r}")
    spec = E5_SMALL
    d = os.path.join(models_dir or default_models_dir(), spec["dir"])
    mp = os.path.join(d, *spec["model_file"].split("/"))
    tp = os.path.join(d, *spec["tokenizer_file"].split("/"))
    if not (os.path.isfile(mp) and os.path.isfile(tp)):
        return None, status_doc("model_missing", detail=f"{spec['name']} 파일 없음")
    sha = _sha256_cached(mp)                   # 처음 한 번만 잰다(옆 캐시 파일)
    if check_hash and sha != spec["model_sha256"]:
        return None, status_doc("hash_mismatch", detail=f"{spec['name']} 해시가 고정값과 다름",
                                info={"name": spec["name"], "model_sha256": sha})
    import importlib.util
    missing = [m for m in ("numpy", "onnxruntime", "tokenizers") if importlib.util.find_spec(m) is None]
    if missing:                                # 앱 .venv 등
        return None, status_doc("backend_unavailable", detail="실행기 없음(" + ",".join(missing) + ")",
                                info={"name": spec["name"], "model_sha256": sha})
    try:
        emb = OnnxE5Embedder(d, spec, model_sha256=sha)
    except ImportError as e:
        return None, status_doc("backend_unavailable", detail=f"실행기 없음({e.name})")
    except Exception as e:                    # onnxruntime 이 파일을 못 읽음 등
        return None, status_doc("backend_unavailable", detail=type(e).__name__)
    return emb, status_doc("ok", info=emb.info())


# ── 점수 ─────────────────────────────────────────────────────────────────

class SemanticIndex:
    """글 → 벡터 캐시. 같은 글은 한 번만 encode 한다."""

    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self._vec: dict[str, Vector] = {}
        try:                                   # 엔진 venv 에서는 numpy 로 빨리 곱한다(앱 venv·시험은 순수 파이썬)
            import numpy as np
            self._np = np
            self._arr: dict = {}
        except ImportError:
            self._np = None

    def prefetch(self, texts: Iterable[str]) -> None:
        todo = sorted({t for t in texts if t not in self._vec})
        if todo:
            for t, v in zip(todo, self.embedder.encode(todo)):
                self._vec[t] = v
                if self._np is not None:
                    self._arr[t] = self._np.asarray(v, dtype=self._np.float32)

    def vec(self, text: str) -> Vector:
        if text not in self._vec:
            self.prefetch([text])
        return self._vec[text]

    def sim(self, a: str, b: str) -> float:
        return cosine(self.vec(a), self.vec(b))

    def sims(self, a: str, others: Sequence[str]) -> list[float]:
        """a 와 others 각각의 코사인."""
        if not others:
            return []
        self.prefetch([a, *others])
        if self._np is None:
            va = self._vec[a]
            return [cosine(va, self._vec[t]) for t in others]
        m = self._np.stack([self._arr[t] for t in others])
        return [float(x) for x in m @ self._arr[a]]


@dataclass(frozen=True)
class CandSemantic:
    """후보 발화 하나의 의미 점수(엔진 내부). evidence 에는 매핑된 키로만 들어간다."""
    sim: float | None
    rel: float | None
    top: bool = False
    answer: bool = False      # semantic_answer  → answer_form
    content: bool = False     # semantic_content → unique_content
    topic: bool = False       # semantic_topic   → same_topic

    def mapped(self) -> dict[str, bool]:
        return {"answer_form": self.answer, "unique_content": self.content, "same_topic": self.topic}

    def to_json(self) -> dict:
        r = lambda x: None if x is None else round(x, 4)   # noqa: E731
        return {"sim": r(self.sim), "rel": r(self.rel), "top": self.top,
                "semantic_answer": self.answer, "semantic_content": self.content, "semantic_topic": self.topic}


SKIPPED = CandSemantic(None, None)


class SemanticScorer:
    """채팅 하나와 그 시간 창 발화들의 의미 점수를 낸다. link v0.3 이 부른다."""

    def __init__(self, embedder: Embedder, cfg: SemanticConfig = SemanticConfig(), status: Mapping | None = None):
        self.index = SemanticIndex(embedder)
        self.cfg = cfg
        self.status = dict(status) if status else status_doc("ok", info=embedder.info(), cfg=cfg)
        if self.status.get("active"):
            self.status["config"] = cfg.to_json()
            self.status["config_sha256"] = cfg.sha256()

    def usable_chat(self, text: str) -> bool:
        return not F.is_low_content(text) and len(F.compact(text)) >= self.cfg.min_chat_chars

    def score_chat(self, chat_text: str, cand_texts: Sequence[str], cand_heads: Sequence[str],
                   context_texts: Sequence[str], prior_texts: Sequence[str]) -> list[CandSemantic]:
        """cand_texts: 후보(시간 창 발화)마다 이어 붙인 글. cand_heads: 그 첫 조각(질문인지 볼 때).
        context_texts: base 를 잴 맥락 발화(채팅 앞 2분 ~ 창 끝, 조각 글). prior_texts: 채팅 앞 발화(조각 글)."""
        c = self.cfg
        if not cand_texts:
            return []
        if not self.usable_chat(chat_text):
            return [SKIPPED for _ in cand_texts]
        ok = [len(F.compact(t)) >= c.min_utt_chars for t in cand_texts]
        idx = self.index
        cs = idx.sims(chat_text, list(cand_texts))
        sims = [s if o else None for s, o in zip(cs, ok)]
        ctx = idx.sims(chat_text, [t for t in context_texts if len(F.compact(t)) >= c.min_utt_chars])
        base = statistics.median(ctx) if len(ctx) >= c.min_context else c.fallback_base
        pri = idx.sims(chat_text, [t for t in prior_texts if len(F.compact(t)) >= c.min_utt_chars])
        prior_max = max(pri) if pri else None
        top_sim = max((s for s in sims if s is not None), default=None)
        is_q = F.is_question(chat_text)
        out = []
        for s, head in zip(sims, cand_heads):
            if s is None:
                out.append(SKIPPED)
                continue
            rel = s - base
            top = top_sim is not None and s >= top_sim - c.top_tolerance
            answer = (is_q and top and not F.is_question(head) and s >= c.answer_min_sim and rel >= c.answer_min_rel)
            content = (top and s >= c.content_min_sim and rel >= c.content_min_rel
                       and (prior_max is None or s - prior_max >= c.content_prior_margin))
            topic = top and s >= c.topic_min_sim and rel >= c.topic_min_rel
            out.append(CandSemantic(sim=s, rel=rel, top=top, answer=answer, content=content, topic=topic))
        return out


def make_scorer(embedder: Embedder | None, status: Mapping, cfg: SemanticConfig = SemanticConfig()
                ) -> SemanticScorer | None:
    """load_default 의 결과로 scorer 를 만든다. 실행기가 없으면 None(v0.2)."""
    if embedder is None:
        return None
    return SemanticScorer(embedder, cfg, status)


def describe(status: Mapping | None) -> str:
    """components.other 에 넣을 짧은 설명(200자 이하). 본문 없음."""
    if not status:
        return "features-v0.2 rules-v0.1"
    if status.get("active"):
        m = status.get("model") or {}
        sha = (m.get("model_sha256") or "")[:12]
        rev = (m.get("revision") or "")[:7]
        s = f"features-v0.2 rules-v0.1 {SEMANTIC_VERSION} {m.get('backend')}:{m.get('name')}"
        if rev:
            s += f"@{rev}"
        if sha:
            s += f" sha256:{sha}"
        return s[:200]
    return f"features-v0.2 rules-v0.1 (v0.3 요청 → {status.get('status')}, v0.2 로 물러남)"[:200]


__all__ = ["SEMANTIC_VERSION", "E5_SMALL", "SemanticConfig", "Embedder", "FakeEmbedder", "OnnxE5Embedder",
           "LmStudioEmbedder", "probe_lmstudio", "load_default", "status_doc", "SemanticIndex", "CandSemantic",
           "SemanticScorer", "make_scorer", "describe", "cosine"]
