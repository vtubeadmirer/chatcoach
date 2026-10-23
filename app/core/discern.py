"""가상 채팅 가려내기 계산 — 판정 모음 → 혼동표·κ·가짜 탐지율·관문·'본 적 있음' (H-5, 20 7장, 25 6-2, 03 6-19,
08 D-24 7 가).

사람이 구간에 보인 채팅마다 '진짜 · 가짜 · 모르겠음' 을 한 번씩 눌러 가려낸다. 이 모듈은 그 판정을 정답(시나리오의
출처)과 견준다. 계산은 06 4-7 의 출처 일치도를 그대로 쓴다 — `app.core.origin.compare_origins(a, b, strict=False)` 하나만
부르고 그 함수는 고치지 않는다(20 7-2).

| 항목 | 값 |
|---|---|
| A 쪽(정답) | 시나리오에서: replay·retrieved → real, generated → virtual. method = generation_log_match |
| B 쪽(사람) | 판정: 진짜 → real, 가짜 → virtual, 모르겠음 → undeterminable. method = manual |
| 짝 키 | 실제 채팅 = chat_id, 시나리오 줄 = '<scenario_id>:<line_id>' (core.scenario.DisplayRow.key 와 같다) |
| 혼동표·κ | compare_origins 결과의 `all` 묶음. A 쪽이 모두 '자동' 이라 `human` 묶음은 비고 `all` 만 뜻이 있다(NOTE_HUMAN_EMPTY) |
| 가짜 탐지율 | 생성 줄을 '가짜' 라고 한 수 / 생성 줄 판정 수('모르겠음' 포함) |
| 진짜 오인율 | 실제 줄(replay + retrieved)을 '가짜' 라고 한 수 / 실제 줄 판정 수. replay·retrieved 따로도 한 줄씩 |
| '모르겠음' 비율 | 모르겠음 / 판정 수 |
| 안 봄 | 판정하지 않은 대상. 혼동표에 넣지 않는다(20 7-1) |
| 관문 (20 7-3 시작값) | 표본: 생성 판정 40 이상 + 실제 판정 40 이상 + 방송 2편 이상. 통과: 탐지율 ≤ 0.65 이고 κ ≤ 0.4. 판정이 한쪽에 몰렸으면(모르겠음 비율 > 0.5 또는 한 값만 누름, one_sided) '판단 불가' |
| 방송 2편 | 화면(DiscernView)은 세션 하나라 늘 방송 1편 = '표본 적음'. 합친 관문은 evaluate_sessions — tools/discern_report.py 가 기록에서 다시 만든다 |
| 다시 봄 | 같은 사람이 같은 시나리오를 두 번째로 가려내면 숫자는 내되 대표값(evaluate_sessions)에서 뺀다 |
| 본 적 있음 | 이 구간을 연 다른 세션 기록(훈련·시청자 훈련·가려내기·라벨링)이 있으면 보고 머리에 적는다(seen_before) |

판정 값은 파일(discern-event)과 같은 이름이다: real · virtual · unsure. 같은 단추를 다시 누르면 판정을 지운다(none →
'안 봄'), 다른 단추를 누르면 바꾼다(apply_answer).

core 층: 파일·시계·난수·Qt 를 쓰지 않는다. 본문·닉네임을 받지도 돌려주지도 않는다(키·출처·판정·숫자만, D-17 8).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from . import origin as org

# ----------------------------------------------------------------------------- 값
REAL = "real"                 # '진짜'
VIRTUAL = "virtual"           # '가짜'
UNSURE = "unsure"             # '모르겠음'
NONE = "none"                 # 판정 지움(같은 단추를 다시 누름) → '안 봄'
ANSWERS = (REAL, VIRTUAL, UNSURE)
ANSWER_KO = {REAL: "진짜", VIRTUAL: "가짜", UNSURE: "모르겠음"}
UNSEEN_KO = "안 봄"

SRC_REPLAY = "replay"
SRC_RETRIEVED = "retrieved"
SRC_GENERATED = "generated"
SOURCES = (SRC_REPLAY, SRC_RETRIEVED, SRC_GENERATED)
SOURCE_KO = {SRC_REPLAY: "실제", SRC_RETRIEVED: "가져옴", SRC_GENERATED: "생성"}

TRUTH_OF_SOURCE = {SRC_REPLAY: org.REAL, SRC_RETRIEVED: org.REAL, SRC_GENERATED: org.VIRTUAL}
ORIGIN_OF_ANSWER = {REAL: org.REAL, VIRTUAL: org.VIRTUAL, UNSURE: org.UNDETERMINABLE}

# 관문 시작값 (20 7-3, 시범 뒤 고친다)
GATE_MIN_GENERATED = 40
GATE_MIN_REAL = 40
GATE_MIN_BROADCASTS = 2
GATE_MAX_DETECTION = 0.65
GATE_MAX_KAPPA = 0.4
# 판정이 한쪽에 몰린 판(시작값, 20 7-3): '모르겠음' 비율이 이 값을 넘거나 사람이 한 값만 눌렀으면 관문을 판단하지 않는다
# (탐지율 0·κ 0 이라 '통과' 로 보이지만 가려내려 하지 않은 판이다)
GATE_MAX_UNSURE = 0.5

GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_LOW_N = "low_n"          # 표본 적음 — 숫자는 내지만 관문을 판단하지 않는다
GATE_UNKNOWN = "unknown"      # 표본은 되지만 κ·탐지율을 계산할 수 없음(예: 한 값만 누름)
GATE_KO = {GATE_PASS: "통과", GATE_FAIL: "실패", GATE_LOW_N: "표본 적음", GATE_UNKNOWN: "판단 불가"}

# 본 적 있음 — 기록 종류
WATCH_TRAINING = "training"
WATCH_VIEWER = "viewer"
WATCH_DISCERN = "discern"
WATCH_LABELING = "labeling"
WATCH_KINDS = (WATCH_TRAINING, WATCH_VIEWER, WATCH_DISCERN, WATCH_LABELING)

NOTE_HUMAN_EMPTY = ("정답 쪽이 모두 '자동'(생성 기록 대조)이라 출처 일치도의 human 묶음은 비고 all 묶음만 뜻이 있습니다 "
                    "(20 7-2).")
NOTE_SEEN = "이 구간을 본 적이 있어 실제 채팅을 기억해 가짜 탐지율이 높게 나올 수 있습니다 (20 7-3)."
NOTE_REPEAT = "같은 시나리오를 다시 가려냈습니다 — 숫자는 내지만 대표값에서 뺍니다 (20 7-1)."
NOTE_LOW_N = ("표본이 관문 시작값(생성 {0}줄 · 실제 {1}줄 · 방송 {2}편 이상)에 못 미쳐 관문을 판단하지 않습니다 (20 7-3).")
NOTE_ONE_SIDED = ("판정이 한쪽에 몰려(모르겠음 비율 {0} 초과 또는 한 값만 누름) 관문을 판단하지 않습니다 — 탐지율·κ 가 "
                  "낮아도 가려내려 하지 않은 판입니다 (20 7-3).")

KEY_SEP = ":"


class DiscernError(ValueError):
    """대상·판정 모양이 맞지 않음. 메시지에는 키·사유만(본문 없음)."""


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ----------------------------------------------------------------------------- 대상·키
@dataclass(frozen=True)
class Target:
    """가려낼 채팅 하나. key = 짝 키, source = replay·retrieved·generated, vod_ms = 보인 시각(모르면 None)."""
    key: str
    source: str
    vod_ms: int | None = None

    @property
    def truth(self) -> str:
        return TRUTH_OF_SOURCE[self.source]


def target_key(*, chat_id: str | None = None, scenario_id: str | None = None, line_id: str | None = None) -> str:
    """짝 키(20 7-2): 실제 채팅이면 chat_id, 시나리오 줄이면 '<scenario_id>:<line_id>'."""
    if chat_id is not None:
        if not isinstance(chat_id, str) or not chat_id or KEY_SEP in chat_id:
            raise DiscernError("chat_id 형식이 아닙니다")
        if scenario_id is not None or line_id is not None:
            raise DiscernError("chat_id 와 line_id 를 함께 줄 수 없습니다")
        return chat_id
    if not isinstance(scenario_id, str) or not scenario_id or KEY_SEP in scenario_id:
        raise DiscernError("scenario_id 형식이 아닙니다")
    if not isinstance(line_id, str) or not line_id or KEY_SEP in line_id:
        raise DiscernError("line_id 형식이 아닙니다")
    return f"{scenario_id}{KEY_SEP}{line_id}"


def parse_key(key: str) -> dict:
    """짝 키 → {"chat_id"} 또는 {"scenario_id", "line_id"} (기록 줄 칸)."""
    if not isinstance(key, str) or not key:
        raise DiscernError("키 형식이 아닙니다")
    if KEY_SEP in key:
        sid, _, lid = key.partition(KEY_SEP)
        if not sid or not lid or KEY_SEP in lid:
            raise DiscernError("키 형식이 아닙니다")
        return {"scenario_id": sid, "line_id": lid}
    return {"chat_id": key}


def make_targets(items: Iterable) -> list[Target]:
    """(key, source[, vod_ms]) · Target · {"key", "source", "vod_ms"?} 모음 → Target 목록(겹친 키는 앞 것만)."""
    out: list[Target] = []
    seen: set[str] = set()
    for it in items or ():
        if isinstance(it, Target):
            t = it
        elif isinstance(it, Mapping):
            t = Target(it.get("key"), it.get("source"), it.get("vod_ms"))
        else:
            seq = tuple(it)
            t = Target(seq[0], seq[1], seq[2] if len(seq) > 2 else None)
        if not isinstance(t.key, str) or not t.key:
            raise DiscernError("대상 키 형식이 아닙니다")
        if t.source not in SOURCES:
            raise DiscernError(f"대상 출처가 아닙니다: {t.key}")
        if t.vod_ms is not None and not _is_int(t.vod_ms):
            raise DiscernError(f"대상 시각이 정수가 아닙니다: {t.key}")
        if t.key in seen:
            continue
        seen.add(t.key)
        out.append(t)
    return out


# ----------------------------------------------------------------------------- 판정
def apply_answer(answers: Mapping[str, str], key: str, answer: str) -> tuple[dict, str]:
    """한 번 누름 → (새 판정 dict, 기록할 값). 안 누른 채팅에 누르면 그 판정, 다른 판정이 있으면 바꿈,
    같은 판정을 다시 누르면 지움('none' → 안 봄)."""
    if answer not in ANSWERS:
        raise DiscernError("판정 값이 아닙니다")
    if not isinstance(key, str) or not key:
        raise DiscernError("키 형식이 아닙니다")
    nxt = dict(answers)
    if nxt.get(key) == answer:
        del nxt[key]
        return nxt, NONE
    nxt[key] = answer
    return nxt, answer


def answers_from_events(records: Iterable[Mapping]) -> dict[str, str]:
    """기록 줄(discern.judge) → 유효 판정 {키: real·virtual·unsure}. seq 순으로 마지막 줄이 유효, 'none' 이면 지움.

    줄은 봉투(kind·seq·body) 또는 body dict(chat_id | scenario_id·line_id, answer) 를 받는다. 모양이 틀린 줄은 버린다."""
    rows = []
    for i, r in enumerate(records or ()):
        if not isinstance(r, Mapping):
            continue
        if "body" in r:
            if r.get("kind") not in (None, "discern.judge"):
                continue
            b = r.get("body")
            seq = r.get("seq")
        else:
            b, seq = r, None
        if not isinstance(b, Mapping):
            continue
        rows.append((seq if _is_int(seq) else i, i, b))
    rows.sort(key=lambda x: (x[0], x[1]))
    out: dict[str, str] = {}
    for _, _, b in rows:
        a = b.get("answer")
        if a not in ANSWERS and a != NONE:
            continue
        try:
            if isinstance(b.get("chat_id"), str):
                k = target_key(chat_id=b["chat_id"])
            else:
                k = target_key(scenario_id=b.get("scenario_id"), line_id=b.get("line_id"))
        except DiscernError:
            continue
        if a == NONE:
            out.pop(k, None)
        else:
            out[k] = a
    return out


def truth_labels(targets: Iterable[Target]) -> list[dict]:
    """정답 쪽(A) 출처 라벨 body — compare_origins 입력."""
    return [{"chat_id": t.key, "origin": t.truth, "method": org.M_GENERATION} for t in targets]


def human_labels(answers: Mapping[str, str]) -> list[dict]:
    """사람 쪽(B) 출처 라벨 body — compare_origins 입력. 판정 값 밖은 버린다."""
    return [{"chat_id": k, "origin": ORIGIN_OF_ANSWER[a], "method": org.M_MANUAL}
            for k, a in answers.items() if a in ANSWERS]


# ----------------------------------------------------------------------------- 결과
def _rate(hit: int, n: int) -> dict:
    return {"hit": int(hit), "n": int(n), "rate": (hit / n) if n else None}


@dataclass
class DiscernResult:
    """가려내기 숫자. 본문·이름 없음(키·출처·판정·숫자만)."""
    targets: int                         # 대상 수(구간에 보인 실제 + 시나리오 줄)
    judged: int                          # 판정한 수
    unseen: int                          # 안 봄
    by_source: dict                      # {src: {shown, judged, real, virtual, unsure}}
    confusion: dict                      # 3×3 (행 = 정답, 열 = 사람) — origin.confusion_from_pairs 모양
    kappa: float | None
    p_o: float | None
    p_e: float | None
    detection: dict                      # 가짜 탐지율 {hit, n, rate}
    false_alarm: dict                    # 진짜 오인율(replay + retrieved)
    replay: dict                         # replay 만의 오인율
    retrieved: dict                      # retrieved 만의 오인율 (20 7-2 '따로 한 줄')
    unsure: dict                         # 모르겠음 비율 {hit = 모르겠음 수, n = 판정 수, rate}
    sample: dict                         # {generated, real, broadcasts, ok}
    gate: dict                           # {status, pass, detection_ok, kappa_ok}
    seen_before: bool | None = None
    repeat: bool = False
    rows: list = field(default_factory=list)       # [{key, source, truth, answer, correct}] 시각 순
    compare: dict = field(default_factory=dict)    # compare_origins 결과 그대로
    notes: list = field(default_factory=list)

    @property
    def representative(self) -> bool:
        """대표값에 넣나(다시 봄이 아님)."""
        return not self.repeat

    def numbers(self) -> dict:
        """기록(discern.end 의 result)·신호용 숫자. 키·본문 없음."""
        return {
            "targets": self.targets, "judged": self.judged, "unseen": self.unseen,
            "confusion": [list(r) for r in self.confusion["rows"]],
            "kappa": self.kappa,
            "detection": dict(self.detection), "false_alarm": dict(self.false_alarm),
            "retrieved": dict(self.retrieved), "unsure": dict(self.unsure),
            "sample": dict(self.sample), "gate": self.gate["status"],
            "low_n": not self.sample["ok"],
        }


def one_sided(judged: int, unsure_n: int, col_totals: Sequence[int]) -> bool:
    """판정이 한쪽에 몰렸나(20 7-3 시작값): 판정이 있고, '모르겠음' 비율이 GATE_MAX_UNSURE 를 넘거나 사람 쪽 혼동표 열
    (진짜·가짜·모르겠음) 가운데 0 이 아닌 것이 하나뿐."""
    if judged <= 0:
        return False
    if unsure_n / judged > GATE_MAX_UNSURE:
        return True
    return sum(1 for c in (col_totals or ()) if c) <= 1


def _gate(sample_ok: bool, detection_rate, kappa, *, lopsided: bool = False) -> dict:
    d_ok = None if detection_rate is None else bool(detection_rate <= GATE_MAX_DETECTION)
    k_ok = None if kappa is None else bool(kappa <= GATE_MAX_KAPPA)
    if d_ok is None or k_ok is None or lopsided:
        passed = None
    else:
        passed = d_ok and k_ok
    if not sample_ok:
        status = GATE_LOW_N
    elif passed is None:
        status = GATE_UNKNOWN
    else:
        status = GATE_PASS if passed else GATE_FAIL
    return {"status": status, "pass": passed, "detection_ok": d_ok, "kappa_ok": k_ok}


def evaluate(targets: Iterable, answers: Mapping[str, str], *, broadcasts: int = 1,
             seen_before: bool | None = None, repeat: bool = False) -> DiscernResult:
    """한 세션(또는 이미 합친 대상)의 판정 → DiscernResult.

    targets: Target 모음(make_targets 가 받는 모양도 됨). answers: {키: real·virtual·unsure} — 대상 밖 키는 compare_origins 가
    `out_of_targets_b` 로 세고 숫자에는 넣지 않는다. broadcasts: 대상이 나온 방송 수(관문 표본)."""
    ts = make_targets(targets)
    keys = [t.key for t in ts]
    ans = {k: a for k, a in (answers or {}).items() if a in ANSWERS}
    cmp = org.compare_origins(truth_labels(ts), human_labels(ans), targets=keys, strict=False)
    allb = cmp["all"]
    by = {s: {"shown": 0, "judged": 0, REAL: 0, VIRTUAL: 0, UNSURE: 0} for s in SOURCES}
    rows = []
    for t in ts:
        a = ans.get(t.key)
        b = by[t.source]
        b["shown"] += 1
        if a is not None:
            b["judged"] += 1
            b[a] += 1
        correct = None if a in (None, UNSURE) else (ORIGIN_OF_ANSWER[a] == t.truth)
        rows.append({"key": t.key, "source": t.source, "truth": t.truth, "answer": a, "correct": correct,
                     "vod_ms": t.vod_ms})
    rows.sort(key=lambda r: (r["vod_ms"] is None, r["vod_ms"] or 0, r["key"]))
    g, rp, rt = by[SRC_GENERATED], by[SRC_REPLAY], by[SRC_RETRIEVED]
    judged = sum(b["judged"] for b in by.values())
    detection = _rate(g[VIRTUAL], g["judged"])
    false_alarm = _rate(rp[VIRTUAL] + rt[VIRTUAL], rp["judged"] + rt["judged"])
    n_real = rp["judged"] + rt["judged"]
    bc = int(broadcasts) if _is_int(broadcasts) and broadcasts > 0 else 0
    sample_ok = g["judged"] >= GATE_MIN_GENERATED and n_real >= GATE_MIN_REAL and bc >= GATE_MIN_BROADCASTS
    n_unsure = sum(b[UNSURE] for b in by.values())
    lopsided = one_sided(judged, n_unsure, allb["confusion"].get("col_totals") or ())
    gate = _gate(sample_ok, detection["rate"], allb["kappa"], lopsided=lopsided)
    notes = [NOTE_HUMAN_EMPTY]
    if not sample_ok:
        notes.append(NOTE_LOW_N.format(GATE_MIN_GENERATED, GATE_MIN_REAL, GATE_MIN_BROADCASTS))
    if lopsided:
        notes.append(NOTE_ONE_SIDED.format(GATE_MAX_UNSURE))
    if seen_before:
        notes.append(NOTE_SEEN)
    if repeat:
        notes.append(NOTE_REPEAT)
    return DiscernResult(
        targets=len(ts), judged=judged, unseen=len(ts) - judged, by_source=by,
        confusion=allb["confusion"], kappa=allb["kappa"], p_o=allb["p_o"], p_e=allb["p_e"],
        detection=detection, false_alarm=false_alarm,
        replay=_rate(rp[VIRTUAL], rp["judged"]), retrieved=_rate(rt[VIRTUAL], rt["judged"]),
        unsure=_rate(n_unsure, judged),
        sample={"generated": g["judged"], "real": n_real, "broadcasts": bc, "ok": sample_ok},
        gate=gate, seen_before=seen_before, repeat=bool(repeat), rows=rows, compare=cmp, notes=notes)


def evaluate_sessions(sessions: Iterable[Mapping]) -> DiscernResult:
    """여러 가려내기 세션을 합친 대표값(관문 판단용, 20 7-3 '방송 2편 이상').

    sessions: [{"targets", "answers", "broadcast"(방송 id — project_id 등), "repeat"?, "seen_before"?}]
    - repeat = True 인 세션은 뺀다(다시 봄, 20 7-1). 키는 세션 차례를 붙여 겹치지 않게 한다.
    - 방송 수 = 넣은 세션의 서로 다른 broadcast 수. seen_before 는 하나라도 True 면 True(모두 모르면 None)."""
    ts: list[Target] = []
    ans: dict[str, str] = {}
    casts: set = set()
    seen_any = None
    for i, s in enumerate(sessions or ()):
        if not isinstance(s, Mapping) or s.get("repeat"):
            continue
        pre = f"{i}|"
        for t in make_targets(s.get("targets") or ()):
            ts.append(Target(pre + t.key, t.source, t.vod_ms))
        for k, a in (s.get("answers") or {}).items():
            if isinstance(k, str) and a in ANSWERS:
                ans[pre + k] = a
        if s.get("broadcast") is not None:
            casts.add(s.get("broadcast"))
        sb = s.get("seen_before")
        if sb is True:
            seen_any = True
        elif sb is False and seen_any is None:
            seen_any = False
    return evaluate(ts, ans, broadcasts=len(casts), seen_before=seen_any, repeat=False)


# ----------------------------------------------------------------------------- 본 적 있음 · 다시 봄
def _overlaps(a: Sequence[int], b) -> bool:
    if b is None:
        return True               # 범위 없음 = 방송 전체(라벨링 세션)
    try:
        x0, x1 = int(a[0]), int(a[1])
        y0, y1 = int(b[0]), int(b[1])
    except (TypeError, ValueError, IndexError):
        return False
    return x0 < y1 and y0 < x1


def seen_before(range_ms: Sequence[int], watched: Iterable) -> dict:
    """이 구간을 본 적이 있나 (20 7-3). watched: (종류, 범위 또는 None) 모음 — 종류는 WATCH_KINDS, 범위 None 은 방송 전체.
    결과 {"seen": bool, "by_kind": {종류: 겹친 기록 수}} (숫자만)."""
    by = {k: 0 for k in WATCH_KINDS}
    for w in watched or ():
        try:
            kind, rng = w[0], w[1]
        except (TypeError, IndexError, KeyError):
            continue
        if kind not in by:
            continue
        if _overlaps(range_ms, rng):
            by[kind] += 1
    return {"seen": any(by.values()), "by_kind": by}


def is_repeat(scenario_id: str | None, prior: Iterable[Mapping]) -> bool:
    """같은 사람이 같은 시나리오를 이미 가려냈나(판정이 1개 이상 있는 앞 세션). prior: [{"scenario_id", "judged"}]."""
    if not scenario_id:
        return False
    for p in prior or ():
        if isinstance(p, Mapping) and p.get("scenario_id") == scenario_id and _is_int(p.get("judged")) \
                and p["judged"] > 0:
            return True
    return False


# ----------------------------------------------------------------------------- 보이기 (글자만)
def fmt_rate(d: Mapping | None) -> str:
    """{hit, n, rate} → '0.50 (2/4)'. 분모 0 이면 '— (0/0)'."""
    if not d:
        return "—"
    r = d.get("rate")
    return f"{'—' if r is None else f'{r:.2f}'} ({d.get('hit', 0)}/{d.get('n', 0)})"


def fmt_kappa(k) -> str:
    return "—" if k is None else f"{k:.2f}"


def answer_mark(row: Mapping) -> str:
    """끝 화면의 줄 표시: '✓ 생성 · 가짜' (맞힘) · '✗ 실제 · 가짜' (틀림) · '? 생성 · 모르겠음' · '· 가져옴 · 안 봄'."""
    a = row.get("answer")
    src = SOURCE_KO.get(row.get("source"), "")
    if a is None:
        return f"· {src} · {UNSEEN_KO}"
    head = "?" if row.get("correct") is None else ("✓" if row.get("correct") else "✗")
    return f"{head} {src} · {ANSWER_KO[a]}"


__all__ = [
    "REAL", "VIRTUAL", "UNSURE", "NONE", "ANSWERS", "ANSWER_KO", "UNSEEN_KO",
    "SRC_REPLAY", "SRC_RETRIEVED", "SRC_GENERATED", "SOURCES", "SOURCE_KO", "TRUTH_OF_SOURCE", "ORIGIN_OF_ANSWER",
    "GATE_MIN_GENERATED", "GATE_MIN_REAL", "GATE_MIN_BROADCASTS", "GATE_MAX_DETECTION", "GATE_MAX_KAPPA",
    "GATE_MAX_UNSURE", "NOTE_ONE_SIDED", "one_sided", "GATE_PASS", "GATE_FAIL", "GATE_LOW_N", "GATE_UNKNOWN", "GATE_KO",
    "WATCH_TRAINING", "WATCH_VIEWER", "WATCH_DISCERN", "WATCH_LABELING", "WATCH_KINDS",
    "NOTE_HUMAN_EMPTY", "NOTE_SEEN", "NOTE_REPEAT", "NOTE_LOW_N",
    "DiscernError", "Target", "target_key", "parse_key", "make_targets", "apply_answer", "answers_from_events",
    "truth_labels", "human_labels", "DiscernResult", "evaluate", "evaluate_sessions", "seen_before", "is_repeat",
    "fmt_rate", "fmt_kappa", "answer_mark",
]
